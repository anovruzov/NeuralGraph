//! Cross-chat memory retrieval.
//!
//! Three channels, fused by Reciprocal Rank Fusion (scores from different channels are not comparable, ranks are):
//!
//! * **vector** — cosine between the query embedding and memory embeddings (float32 matrix cached per store
//!   revision; filters are boolean masks over parallel arrays, so 50k memories stay in the low milliseconds).
//! * **keyword** — FTS5 bm25 over memory text (or in-process BM25 when FTS5 is missing): exact names, numbers, rare
//!   words.
//! * **graph** — entities named in the query (matched against the entity registry and its aliases, longest match
//!   first) pull in the memories that mention them, plus one typed hop over relations.
//!
//! After fusion, priors reorder the top of the list: a query that *names* a subject boosts that subject's own
//! memories (identity comes from metadata, never from text similarity — the single biggest win in the LoCoMo
//! campaign), importance and mild recency are multiplied in, and superseded memories are excluded unless asked for.
//! Every result carries its provenance (source messages) and a per-channel breakdown.

use crate::error::Result;
use crate::llm::LlmClient;
use crate::models::{parse_iso, total_seconds, utcnow, ChatMessage, Memory, RetrievedMemory};
use crate::pycompat::{fnum, obj, py_fixed, py_len, py_prefix, py_round, py_strip};
use crate::store::{query_bounds, ChannelFilter, ChatMemoryStore, IndexRow, MemoryQuery};
use crate::textutil::{norm_entity, normalize_ws};
use chrono::{DateTime, Utc};
use indexmap::{IndexMap, IndexSet};
use regex::Regex;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, LazyLock, Mutex};

/// Retrieval settings (defaults are the measured ones).
#[derive(Clone, Debug)]
pub struct RetrievalConfig {
    /// candidates per channel before fusion
    pub depth: usize,
    pub rrf_k: i64,
    pub weight_vector: f64,
    pub weight_keyword: f64,
    pub weight_graph: f64,
    /// default channels: V=vector K=keyword G=entity graph. The graph channel is off by default: on LoCoMo it never
    /// helped and cost up to 9 pts of evidence recall (it pulls related non-answers into the context). Pass
    /// channels="VKG" per call, or set this, to turn it on.
    pub channels: String,
    /// query names the memory's subject
    pub subject_boost: f64,
    /// query names an entity the memory mentions
    pub entity_boost: f64,
    /// score *= (1 - w) + w * importance
    pub importance_weight: f64,
    /// score *= (1 - w) + w * recency
    pub recency_weight: f64,
    pub recency_half_life_days: f64,
    pub min_channel_hits: usize,
    pub context_max_chars: usize,
    /// 15 beat 10 by 3-4 pts and 5 by ~10 on LoCoMo evidence recall
    pub context_k: usize,
    /// drop context lines scoring < this fraction of the top hit
    pub context_min_relative_score: f64,
    /// cosine floor for the vector channel (drops ~orthogonal noise)
    pub vector_min_sim: f64,
    /// a context line needs a keyword/graph hit or at least this cosine
    pub context_min_vector_sim: f64,
}

impl Default for RetrievalConfig {
    fn default() -> Self {
        RetrievalConfig {
            depth: 100,
            rrf_k: 60,
            weight_vector: 1.0,
            weight_keyword: 0.8,
            weight_graph: 0.7,
            channels: "VK".into(),
            subject_boost: 1.6,
            entity_boost: 1.25,
            importance_weight: 0.35,
            recency_weight: 0.2,
            recency_half_life_days: 365.0,
            min_channel_hits: 1,
            context_max_chars: 3600,
            context_k: 15,
            context_min_relative_score: 0.25,
            vector_min_sim: 0.05,
            context_min_vector_sim: 0.3,
        }
    }
}

/// Reciprocal Rank Fusion: `score(d) = sum_i w_i / (k + rank_i(d) + 1)`, in first-seen order.
pub fn rrf(rankings: &[Vec<String>], weights: &[f64], k: i64) -> IndexMap<String, f64> {
    let mut score: IndexMap<String, f64> = IndexMap::new();
    for (i, ranking) in rankings.iter().enumerate() {
        let w = weights.get(i).copied().unwrap_or(1.0);
        for (r, doc_id) in ranking.iter().enumerate() {
            *score.entry(doc_id.clone()).or_insert(0.0) += w / ((k + r as i64 + 1) as f64);
        }
    }
    score
}

fn recency(observed_at: &str, half_life_days: f64, now: DateTime<Utc>) -> f64 {
    let Some(dt) = parse_iso(Some(observed_at)) else { return 0.5 };
    let days = (total_seconds(now.signed_duration_since(dt)) / 86400.0).max(0.0);
    (-(2f64.ln()) * days / half_life_days.max(1.0)).exp()
}

/// [start, end] ISO bounds of a memory's time given its precision ("2026-10" -> whole month).
pub fn time_bounds(t: Option<&str>, precision: Option<&str>) -> (String, String) {
    let Some(t) = t.filter(|s| !s.is_empty()) else { return (String::new(), String::new()) };
    let t = py_strip(t);
    let n = py_len(t);
    if precision == Some("year") || n == 4 {
        let y = py_prefix(t, 4);
        return (format!("{y}-01-01T00:00:00"), format!("{y}-12-31T23:59:59"));
    }
    if precision == Some("month") || n == 7 {
        let ym = py_prefix(t, 7);
        return (format!("{ym}-01T00:00:00"), format!("{ym}-31T23:59:59"));
    }
    if n == 10 {
        return (format!("{t}T00:00:00"), format!("{t}T23:59:59"));
    }
    let base = py_prefix(t, 19).to_string();
    (base.clone(), base)
}

/// Normalised float32 rows of one embedding dimension.
struct DimMatrix {
    /// row positions (into the index arrays) of each matrix row
    idxs: Vec<usize>,
    dim: usize,
    data: Vec<f32>,
}

/// Immutable snapshot of the index arrays (rebuilt after any change, shared by in-flight searches).
pub struct IndexData {
    pub ids: Vec<String>,
    pos: HashMap<String, usize>,
    subject: Vec<String>,
    speaker: Vec<String>,
    chat: Vec<String>,
    kind: Vec<String>,
    t_lo: Vec<String>,
    t_hi: Vec<String>,
    importance: Vec<f32>,
    active: Vec<bool>,
    observed: Vec<String>,
    mats: IndexMap<usize, DimMatrix>,
}

fn dot_f32(a: &[f32], b: &[f32]) -> f32 {
    // eight independent accumulators let the compiler vectorise the loop
    let mut acc = [0f32; 8];
    let chunks = a.len() / 8;
    for c in 0..chunks {
        for l in 0..8 {
            acc[l] += a[c * 8 + l] * b[c * 8 + l];
        }
    }
    let mut s = ((acc[0] + acc[1]) + (acc[2] + acc[3])) + ((acc[4] + acc[5]) + (acc[6] + acc[7]));
    for i in chunks * 8..a.len() {
        s += a[i] * b[i];
    }
    s
}

fn normalise_f32(v: &[f64]) -> Vec<f32> {
    let row: Vec<f32> = v.iter().map(|x| *x as f32).collect();
    let norm = dot_f32(&row, &row).sqrt() + 1e-9f32;
    row.iter().map(|x| x / norm).collect()
}

impl IndexData {
    fn build(rows: &IndexMap<String, IndexRow>) -> Self {
        let n = rows.len();
        let mut d = IndexData {
            ids: Vec::with_capacity(n),
            pos: HashMap::with_capacity(n),
            subject: Vec::with_capacity(n),
            speaker: Vec::with_capacity(n),
            chat: Vec::with_capacity(n),
            kind: Vec::with_capacity(n),
            t_lo: Vec::with_capacity(n),
            t_hi: Vec::with_capacity(n),
            importance: Vec::with_capacity(n),
            active: Vec::with_capacity(n),
            observed: Vec::with_capacity(n),
            mats: IndexMap::new(),
        };
        let mut by_dim: IndexMap<usize, Vec<usize>> = IndexMap::new();
        for (i, r) in rows.values().enumerate() {
            d.ids.push(r.memory_id.clone());
            d.pos.insert(r.memory_id.clone(), i);
            d.subject.push(r.subject.clone());
            d.speaker.push(r.speaker.clone());
            d.chat.push(r.chat_id.clone());
            d.kind.push(r.kind.clone());
            let has_event = r.event_time.as_deref().is_some_and(|e| !e.is_empty());
            let (lo, hi) = if has_event {
                time_bounds(r.event_time.as_deref(), r.event_time_precision.as_deref())
            } else {
                time_bounds(Some(&r.observed_at), None)
            };
            d.t_lo.push(lo);
            d.t_hi.push(hi);
            d.importance.push(r.importance as f32);
            d.active.push(r.status == "active");
            d.observed.push(r.observed_at.clone());
            if let Some(e) = r.embedding.as_ref().filter(|e| !e.is_empty()) {
                by_dim.entry(e.len()).or_default().push(i);
            }
        }
        let row_list: Vec<&IndexRow> = rows.values().collect();
        for (dim, idxs) in by_dim {
            let mut data = Vec::with_capacity(idxs.len() * dim);
            for &i in &idxs {
                data.extend(normalise_f32(row_list[i].embedding.as_ref().expect("has embedding")));
            }
            d.mats.insert(dim, DimMatrix { idxs, dim, data });
        }
        d
    }

    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }

    /// Embedding dimensions present in the index.
    pub fn dims(&self) -> Vec<usize> {
        self.mats.keys().copied().collect()
    }

    pub fn position(&self, memory_id: &str) -> Option<usize> {
        self.pos.get(memory_id).copied()
    }

    #[allow(clippy::too_many_arguments)]
    fn mask(
        &self,
        subject: Option<&str>,
        speaker: Option<&str>,
        chat_id: Option<&str>,
        kinds: Option<&[String]>,
        since: Option<&str>,
        until: Option<&str>,
        include_superseded: bool,
    ) -> Vec<bool> {
        let n = self.ids.len();
        let mut m = vec![true; n];
        if n == 0 {
            return m;
        }
        // superseded memories are hidden unless asked for, or unless the query is about a time window (a fact that
        // was later replaced was still the truth back then)
        let windowed = since.is_some_and(|s| !s.is_empty()) || until.is_some_and(|s| !s.is_empty());
        let (lo, hi) = query_bounds(since, until);
        for (i, keep) in m.iter_mut().enumerate() {
            if !include_superseded && !windowed && !self.active[i] {
                *keep = false;
                continue;
            }
            if subject.is_some_and(|s| self.subject[i] != s)
                || speaker.is_some_and(|s| self.speaker[i] != s)
                || chat_id.is_some_and(|s| self.chat[i] != s)
                || kinds.is_some_and(|k| !k.contains(&self.kind[i]))
                // memory interval ends after the window starts, and starts before the window ends
                || (!lo.is_empty() && self.t_hi[i] < lo)
                || (!hi.is_empty() && self.t_lo[i] > hi)
            {
                *keep = false;
            }
        }
        m
    }

    fn vector_rank(&self, qvec: &[f64], mask: &[bool], k: usize, min_sim: f64) -> Vec<(String, f64)> {
        let Some(mat) = self.mats.get(&qvec.len()) else { return Vec::new() };
        if qvec.is_empty() {
            return Vec::new();
        }
        let q = normalise_f32(qvec);
        let floor = min_sim as f32;
        let scan = |rows: std::ops::Range<usize>| -> Vec<(usize, f32)> {
            let mut out = Vec::new();
            for row in rows {
                let i = mat.idxs[row];
                if !mask[i] {
                    continue;
                }
                let sim = dot_f32(&mat.data[row * mat.dim..(row + 1) * mat.dim], &q);
                if sim >= floor && sim.is_finite() {
                    out.push((i, sim));
                }
            }
            out
        };
        // Large indexes are scanned on every core; chunks are concatenated in row order, so the result is the
        // same as a sequential scan.
        let rows = mat.idxs.len();
        let threads = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1).min(16);
        let mut hits: Vec<(usize, f32)> = if threads > 1 && rows * mat.dim >= PARALLEL_SCAN_FLOPS {
            let chunk = rows.div_ceil(threads);
            std::thread::scope(|sc| {
                let handles: Vec<_> =
                    (0..threads).map(|t| sc.spawn(move || scan(t * chunk..((t + 1) * chunk).min(rows)))).collect();
                handles.into_iter().flat_map(|h| h.join().expect("vector scan thread")).collect()
            })
        } else {
            scan(0..rows)
        };
        // highest similarity first; ties keep index order (numpy's order for ties is unspecified)
        hits.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        hits.truncate(k);
        hits.into_iter().map(|(i, s)| (self.ids[i].clone(), s as f64)).collect()
    }
}

/// Below this many multiply-adds a vector scan is faster on one thread than spread over the cores.
const PARALLEL_SCAN_FLOPS: usize = 2_000_000;

/// Mutable side of the index: the rows, refreshed incrementally from the store.
#[derive(Default)]
struct IndexState {
    rows: IndexMap<String, IndexRow>,
    last_updated_at: String,
    built: Option<Arc<IndexData>>,
    loaded: bool,
    rev: Option<(u64, i64)>,
}

impl IndexState {
    fn upsert(&mut self, rows: Vec<IndexRow>) {
        for r in rows {
            if !r.updated_at.is_empty() && r.updated_at > self.last_updated_at {
                self.last_updated_at = r.updated_at.clone();
            }
            if r.status == "active" || r.status == "superseded" {
                self.rows.insert(r.memory_id.clone(), r);
            } else {
                self.rows.shift_remove(&r.memory_id);
            }
        }
        self.built = None;
    }
}

/// Per-call search options (the keyword arguments of the Python `search`).
#[derive(Clone, Debug)]
pub struct SearchOptions {
    pub k: usize,
    pub subject: Option<String>,
    pub speaker: Option<String>,
    pub chat_id: Option<String>,
    pub kinds: Option<Vec<String>>,
    pub since: Option<String>,
    pub until: Option<String>,
    pub include_superseded: bool,
    pub with_sources: bool,
    pub query_embedding: Option<Vec<f64>>,
    pub channels: Option<String>,
    pub touch: bool,
}

impl Default for SearchOptions {
    fn default() -> Self {
        SearchOptions {
            k: 10,
            subject: None,
            speaker: None,
            chat_id: None,
            kinds: None,
            since: None,
            until: None,
            include_superseded: false,
            with_sources: true,
            query_embedding: None,
            channels: None,
            touch: true,
        }
    }
}

fn some_str(s: &Option<String>) -> Option<&str> {
    s.as_deref().filter(|v| !v.is_empty())
}

/// alias/key -> entity id, and the store cache key it was read at
type EntityKeyCache = (IndexMap<String, String>, Option<(u64, i64)>);

pub struct MemoryRetriever {
    pub store: Arc<ChatMemoryStore>,
    pub llm: Arc<dyn LlmClient>,
    pub config: RetrievalConfig,
    index: Mutex<IndexState>,
    entity_keys: Mutex<EntityKeyCache>,
    full_loads: AtomicU64,
}

static POSSESSIVE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"'s\b|'\b").unwrap());

impl MemoryRetriever {
    pub fn new(store: Arc<ChatMemoryStore>, llm: Arc<dyn LlmClient>, config: RetrievalConfig) -> Self {
        MemoryRetriever {
            store,
            llm,
            config,
            index: Mutex::new(IndexState::default()),
            entity_keys: Mutex::new((IndexMap::new(), None)),
            full_loads: AtomicU64::new(0),
        }
    }

    // ------------------------------------------------------------------ caches
    /// The current index snapshot: loaded once, then refreshed incrementally with rows written since the last
    /// refresh (inclusive; upsert is idempotent) whenever the store's cache key moves.
    pub fn get_index(&self) -> Result<Arc<IndexData>> {
        let key = self.store.cache_key();
        let mut st = self.index.lock().unwrap_or_else(|e| e.into_inner());
        if !st.loaded {
            let rows = self.store.index_rows(None, None)?;
            st.upsert(rows);
            st.loaded = true;
            st.rev = Some(key);
            self.full_loads.fetch_add(1, Ordering::SeqCst);
        } else if st.rev != Some(key) {
            let after = if st.last_updated_at.is_empty() { None } else { Some(st.last_updated_at.clone()) };
            let changed = self.store.index_rows(None, after.as_deref())?;
            st.upsert(changed);
            st.rev = Some(key);
        }
        if st.built.is_none() {
            st.built = Some(Arc::new(IndexData::build(&st.rows)));
        }
        Ok(st.built.clone().expect("built above"))
    }

    /// How many times the index was loaded from scratch (it should be once; refreshes are incremental).
    pub fn full_loads(&self) -> u64 {
        self.full_loads.load(Ordering::SeqCst)
    }

    /// Latest `updated_at` the index has seen.
    pub fn index_last_updated_at(&self) -> String {
        self.index.lock().unwrap_or_else(|e| e.into_inner()).last_updated_at.clone()
    }

    fn get_entity_keys(&self) -> Result<IndexMap<String, String>> {
        let key = self.store.cache_key();
        let mut st = self.entity_keys.lock().unwrap_or_else(|e| e.into_inner());
        if st.1 != Some(key) {
            st.0 = self.store.all_entity_keys()?;
            st.1 = Some(key);
        }
        Ok(st.0.clone())
    }

    pub fn invalidate(&self) {
        self.index.lock().unwrap_or_else(|e| e.into_inner()).rev = None;
        self.entity_keys.lock().unwrap_or_else(|e| e.into_inner()).1 = None;
    }

    // ------------------------------------------------------------------ query analysis
    /// Entity ids named in the query (longest alias match first, no overlaps).
    pub fn query_entities(&self, query: &str) -> Result<Vec<String>> {
        let keys = self.get_entity_keys()?;
        if keys.is_empty() {
            return Ok(Vec::new());
        }
        let q = format!(" {} ", norm_entity(&POSSESSIVE_RE.replace_all(query, "")));
        let mut found: Vec<(usize, &String)> = Vec::new();
        for (alias, eid) in &keys {
            let n = py_len(alias);
            if n < 3 {
                continue;
            }
            if q.contains(&format!(" {alias} ")) {
                found.push((n, eid));
            }
        }
        found.sort_by_key(|(n, _)| std::cmp::Reverse(*n));
        let mut out: Vec<String> = Vec::new();
        for (_, eid) in found {
            if !out.contains(eid) {
                out.push(eid.clone());
            }
        }
        Ok(out)
    }

    // ------------------------------------------------------------------ search
    pub async fn search(&self, query: &str, opts: &SearchOptions) -> Result<Vec<RetrievedMemory>> {
        let cfg = &self.config;
        let channels = opts.channels.clone().filter(|c| !c.is_empty()).unwrap_or_else(|| cfg.channels.clone());
        let query = normalize_ws(query);
        if query.is_empty() {
            return Ok(Vec::new());
        }
        let index = self.get_index()?;
        let kinds: Option<Vec<String>> = opts.kinds.as_ref().filter(|k| !k.is_empty()).map(|k| {
            let set: IndexSet<String> = k.iter().cloned().collect();
            set.into_iter().collect()
        });
        let subject_id = match some_str(&opts.subject) {
            Some(s) => Some(self.store.resolve_alias(s)?.unwrap_or_else(|| norm_entity(s))),
            None => None,
        };
        let since = some_str(&opts.since);
        let until = some_str(&opts.until);
        let mask = index.mask(
            subject_id.as_deref().filter(|s| !s.is_empty()),
            some_str(&opts.speaker),
            some_str(&opts.chat_id),
            kinds.as_deref(),
            since,
            until,
            opts.include_superseded,
        );
        if !mask.iter().any(|m| *m) {
            return Ok(Vec::new());
        }
        let allowed = |mid: &str| index.position(mid).is_some_and(|i| mask[i]);

        let mut rankings: Vec<Vec<String>> = Vec::new();
        let mut weights: Vec<f64> = Vec::new();
        let mut per_channel: IndexMap<&str, IndexMap<String, f64>> =
            IndexMap::from([("vector", IndexMap::new()), ("keyword", IndexMap::new()), ("graph", IndexMap::new())]);

        let qvec = if channels.contains('V') {
            match &opts.query_embedding {
                Some(v) => Some(v.clone()),
                None => self.llm.embed(&query).await.ok(),
            }
            .filter(|v| !v.is_empty())
        } else {
            None
        };
        let widen = opts.include_superseded || since.is_some() || until.is_some();
        let keyword = || -> Result<Vec<(String, f64)>> {
            let filter = ChannelFilter {
                subject: subject_id.clone(),
                chat_id: some_str(&opts.chat_id).map(str::to_string),
                speaker: some_str(&opts.speaker).map(str::to_string),
                kinds: kinds.clone(),
                since: since.map(str::to_string),
                until: until.map(str::to_string),
            };
            let depth2 = (cfg.depth * 2) as i64;
            let mut kw_rows = self.store.keyword_candidates(&query, depth2, "active", &filter)?;
            if widen {
                kw_rows.extend(self.store.keyword_candidates(&query, depth2, "superseded", &filter)?);
            }
            Ok(kw_rows)
        };
        // The two channels are independent reads: the FTS5 query runs on its own thread while this one scans the
        // vectors. Rankings are assembled in the fixed order vector, keyword, so fusion is unaffected.
        let (vr, kw_rows) = std::thread::scope(|sc| {
            let kw = channels.contains('K').then(|| sc.spawn(keyword));
            let vr = qvec.as_ref().map(|qv| index.vector_rank(qv, &mask, cfg.depth, cfg.vector_min_sim));
            (vr, kw.map(|h| h.join().expect("keyword channel thread")))
        });
        if let Some(vr) = vr {
            per_channel["vector"] = vr.iter().cloned().collect();
            rankings.push(vr.into_iter().map(|(m, _)| m).collect());
            weights.push(cfg.weight_vector);
        }
        if let Some(kw_rows) = kw_rows {
            let kr: Vec<(String, f64)> = kw_rows?.into_iter().filter(|(m, _)| allowed(m)).take(cfg.depth).collect();
            let mut kmap = IndexMap::new();
            for (m, s) in &kr {
                kmap.insert(m.clone(), *s);
            }
            per_channel["keyword"] = kmap;
            rankings.push(kr.into_iter().map(|(m, _)| m).collect());
            weights.push(cfg.weight_keyword);
        }

        // entities named in the query drive the subject/entity priors regardless of channel selection; only the
        // graph *channel* (mention + 1-hop ranking) is gated on "G"
        let q_entities = self.query_entities(&query)?;
        if channels.contains('G') && !q_entities.is_empty() {
            let filter = ChannelFilter {
                subject: subject_id.clone(),
                chat_id: some_str(&opts.chat_id).map(str::to_string),
                speaker: None,
                kinds: kinds.clone(),
                since: since.map(str::to_string),
                until: until.map(str::to_string),
            };
            let gr = self.graph_rank(&q_entities, &allowed, cfg.depth, widen, &filter)?;
            per_channel["graph"] = gr.iter().cloned().collect();
            rankings.push(gr.into_iter().map(|(m, _)| m).collect());
            weights.push(cfg.weight_graph);
        }

        let fused = rrf(&rankings, &weights, cfg.rrf_k);
        if fused.is_empty() {
            return Ok(Vec::new());
        }

        // priors
        let now = utcnow();
        let fused_ids: Vec<String> = fused.keys().cloned().collect();
        let ent_map = self.store.entity_ids_for_memories(&fused_ids)?;
        let q_ent_set: IndexSet<&String> = q_entities.iter().collect();
        let mut scored: Vec<(String, f64, IndexMap<String, f64>)> = Vec::new();
        for (mid, base) in &fused {
            let Some(i) = index.position(mid) else { continue };
            let imp = index.importance[i] as f64;
            let rec = recency(&index.observed[i], cfg.recency_half_life_days, now);
            let mut s = base * ((1.0 - cfg.importance_weight) + cfg.importance_weight * imp);
            s *= (1.0 - cfg.recency_weight) + cfg.recency_weight * rec;
            let mut boosts: Vec<(&str, f64)> = Vec::new();
            if !q_ent_set.is_empty() && q_ent_set.contains(&index.subject[i]) {
                s *= cfg.subject_boost;
                boosts.push(("subject", cfg.subject_boost));
            } else if !q_ent_set.is_empty()
                && ent_map.get(mid).is_some_and(|ents| ents.iter().any(|e| q_ent_set.contains(e)))
            {
                s *= cfg.entity_boost;
                boosts.push(("entity", cfg.entity_boost));
            }
            let hits = per_channel.values().filter(|ch| ch.contains_key(mid)).count();
            if hits < cfg.min_channel_hits {
                continue;
            }
            let mut ch: IndexMap<String, f64> = IndexMap::new();
            for (name, vals) in &per_channel {
                if let Some(v) = vals.get(mid) {
                    ch.insert((*name).to_string(), py_round(*v, 4));
                }
            }
            ch.insert("rrf".into(), py_round(*base, 5));
            ch.insert("importance".into(), py_round(imp, 2));
            ch.insert("recency".into(), py_round(rec, 3));
            for (k, v) in boosts {
                ch.insert(k.into(), v);
            }
            scored.push((mid.clone(), s, ch));
        }
        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        scored.truncate(opts.k);
        let top_ids: Vec<String> = scored.iter().map(|(m, _, _)| m.clone()).collect();
        let mems = self.store.get_memories_by_ids(&top_ids, opts.with_sources)?;
        let by_id: IndexMap<String, Memory> = mems.iter().map(|m| (m.memory_id.clone(), m.clone())).collect();
        let mut src_cache: HashMap<String, ChatMessage> = HashMap::new();
        if opts.with_sources {
            let all_src: Vec<String> = mems.iter().flat_map(|m| m.source_message_ids.iter().cloned()).collect();
            for msg in self.store.get_messages_by_ids(&all_src)? {
                src_cache.insert(msg.message_id.clone(), msg);
            }
        }
        let mut out: Vec<RetrievedMemory> = Vec::new();
        for (mid, s, ch) in scored {
            let Some(m) = by_id.get(&mid) else { continue };
            let explanation = explain(&ch);
            out.push(RetrievedMemory {
                memory: m.clone(),
                score: py_round(s, 6),
                sources: m.source_message_ids.iter().filter_map(|x| src_cache.get(x).cloned()).collect(),
                entities: ent_map.get(&mid).cloned().unwrap_or_default(),
                channels: ch,
                explanation,
            });
        }
        if opts.include_superseded {
            let mut present: IndexSet<String> = out.iter().map(|r| r.memory.memory_id.clone()).collect();
            let mut hist: Vec<RetrievedMemory> = Vec::new();
            for rm in &out {
                for old in self.store.history(&rm.memory.memory_id, 20)? {
                    if !present.contains(&old.memory_id) {
                        present.insert(old.memory_id.clone());
                        hist.push(RetrievedMemory {
                            memory: old,
                            score: rm.score * 0.5,
                            channels: IndexMap::from([("history_of".to_string(), rm.score)]),
                            sources: Vec::new(),
                            entities: Vec::new(),
                            explanation: format!("superseded by {}", rm.memory.memory_id),
                        });
                    }
                }
            }
            out.extend(hist);
        }
        for r in out.iter_mut() {
            if r.memory.status == "superseded" && !r.explanation.contains("superseded") {
                let note = match r.memory.superseded_by.as_deref().filter(|s| !s.is_empty()) {
                    Some(by) => format!("superseded by {by}"),
                    None => "superseded".to_string(),
                };
                let prefix = if r.explanation.is_empty() { String::new() } else { format!("{}, ", r.explanation) };
                r.explanation = format!("{prefix}{note} (was current at that time)");
            }
        }
        if opts.touch && !out.is_empty() {
            let ids: Vec<String> = out.iter().map(|r| r.memory.memory_id.clone()).collect();
            self.store.touch_access(&ids)?;
        }
        Ok(out)
    }

    fn graph_rank(
        &self,
        entity_ids: &[String],
        allowed: &dyn Fn(&str) -> bool,
        depth: usize,
        with_superseded: bool,
        filter: &ChannelFilter,
    ) -> Result<Vec<(String, f64)>> {
        let counts = self.store.entity_memory_counts(entity_ids)?;
        let mut scores: IndexMap<String, f64> = IndexMap::new();
        // direct mentions, weighted by inverse entity frequency (hub entities carry less signal)
        let mut rows = self.store.memories_for_entities(entity_ids, (depth * 4) as i64, "active", filter)?;
        if with_superseded {
            rows.extend(self.store.memories_for_entities(entity_ids, (depth * 4) as i64, "superseded", filter)?);
        }
        for (mid, eid) in rows {
            if allowed(&mid) {
                let c = counts.get(&eid).copied().unwrap_or(1) as f64;
                *scores.entry(mid).or_insert(0.0) += 1.0 / (2.0 + c).ln();
            }
        }
        // one typed hop over relations
        let nbrs = self.store.neighbors(entity_ids, 100)?;
        let hop: Vec<String> = nbrs
            .values()
            .flat_map(|lst| lst.iter().map(|(n, _, _)| n.clone()))
            .filter(|n| !entity_ids.contains(n))
            .collect();
        if !hop.is_empty() {
            let hop_counts = self.store.entity_memory_counts(&hop)?;
            let first40: Vec<String> = hop.iter().take(40).cloned().collect();
            for (mid, eid) in self.store.memories_for_entities(&first40, (depth * 2) as i64, "active", filter)? {
                if allowed(&mid) {
                    let c = hop_counts.get(&eid).copied().unwrap_or(1) as f64;
                    *scores.entry(mid).or_insert(0.0) += 0.35 / (2.0 + c).ln();
                }
            }
        }
        let mut ranked: Vec<(String, f64)> = scores.into_iter().collect();
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        ranked.truncate(depth);
        Ok(ranked)
    }

    // ------------------------------------------------------------------ higher-level helpers
    /// A compact block to prepend to a new chat's prompt. Empty string when nothing is relevant.
    pub async fn context_for(
        &self,
        query: &str,
        k: Option<usize>,
        max_chars: Option<usize>,
        filters: &SearchOptions,
    ) -> Result<String> {
        Ok(self.context_with_results(query, k, max_chars, DEFAULT_HEADER, filters).await?.0)
    }

    /// Like [`Self::context_for`] but also returns the memories that made it into the block.
    pub async fn context_with_results(
        &self,
        query: &str,
        k: Option<usize>,
        max_chars: Option<usize>,
        header: &str,
        filters: &SearchOptions,
    ) -> Result<(String, Vec<RetrievedMemory>)> {
        let cfg = &self.config;
        let mut opts = filters.clone();
        opts.k = k.filter(|k| *k > 0).unwrap_or(cfg.context_k);
        opts.with_sources = false;
        let results = self.search(query, &opts).await?;
        if results.is_empty() {
            return Ok((String::new(), Vec::new()));
        }
        // evidence floor: a line needs a keyword or graph hit, or a reasonably similar embedding
        let results: Vec<RetrievedMemory> = results
            .into_iter()
            .filter(|r| {
                r.channels.contains_key("keyword")
                    || r.channels.contains_key("graph")
                    || r.channels.get("vector").copied().unwrap_or(0.0) >= cfg.context_min_vector_sim
            })
            .collect();
        if results.is_empty() {
            return Ok((String::new(), Vec::new()));
        }
        let top_score = if results[0].score != 0.0 { results[0].score } else { 1.0 };
        let results: Vec<RetrievedMemory> =
            results.into_iter().filter(|r| r.score >= cfg.context_min_relative_score * top_score).collect();
        let mut lines = vec![header.to_string()];
        let mut used = py_len(header);
        let limit = max_chars.filter(|m| *m > 0).unwrap_or(cfg.context_max_chars);
        let mut included = Vec::new();
        for r in results {
            let m = &r.memory;
            let when = match m.event_time.as_deref().filter(|s| !s.is_empty()) {
                Some(t) => format!("{t}, "),
                None => String::new(),
            };
            let line =
                format!("- [{when}{}] {} (from chat {}, {})", m.kind, m.text, m.chat_id, py_prefix(&m.observed_at, 10));
            let n = py_len(&line);
            if used + n + 1 > limit {
                continue; // too long for the remaining budget; a shorter, later line may still fit
            }
            lines.push(line);
            used += n + 1;
            included.push(r);
        }
        if lines.len() == 1 {
            return Ok((String::new(), Vec::new()));
        }
        Ok((lines.join("\n"), included))
    }

    pub fn profile(&self, subject: &str, limit: i64) -> Result<Value> {
        let sid = self.store.resolve_alias(subject)?.unwrap_or_else(|| norm_entity(subject));
        let q = MemoryQuery {
            subject: Some(sid.clone()),
            limit: Some(limit),
            order: "importance DESC".into(),
            with_sources: true,
            ..Default::default()
        };
        let mems = self.store.list_memories(&q)?;
        let mut by_kind: IndexMap<String, Vec<Value>> = IndexMap::new();
        for m in &mems {
            by_kind.entry(m.kind.clone()).or_default().push(m.to_value(false));
        }
        let entity = self.store.get_entity(&sid)?;
        let rels = self.store.relations_for(&sid, Some("active"), 100)?;
        Ok(obj([
            ("subject", Value::String(sid)),
            ("name", Value::String(entity.as_ref().map(|e| e.name.clone()).unwrap_or_else(|| subject.to_string()))),
            ("entity", entity.as_ref().map(|e| e.to_value()).unwrap_or(Value::Null)),
            ("memory_count", Value::from(mems.len())),
            ("memories_by_kind", obj(by_kind.into_iter().map(|(k, v)| (k, Value::Array(v))))),
            ("relations", Value::Array(rels.iter().map(|r| r.to_value()).collect())),
        ]))
    }

    pub fn related(&self, memory_id: &str) -> Result<Vec<Value>> {
        let mut out = Vec::new();
        for link in self.store.links_for(memory_id)? {
            let other = if link.source_id == memory_id { &link.target_id } else { &link.source_id };
            if let Some(m) = self.store.get_memory(other, false)? {
                if m.status != "retracted" {
                    let direction = if link.source_id == memory_id { "out" } else { "in" };
                    out.push(obj([
                        ("memory", m.to_value(false)),
                        ("link_type", Value::String(link.link_type.clone())),
                        ("weight", fnum(link.weight)),
                        ("direction", Value::from(direction)),
                    ]));
                }
            }
        }
        Ok(out)
    }

    pub fn timeline(
        &self,
        subject: Option<&str>,
        since: Option<&str>,
        until: Option<&str>,
        limit: i64,
    ) -> Result<Vec<Memory>> {
        let q = MemoryQuery {
            subject: subject.map(str::to_string),
            since: since.map(str::to_string),
            until: until.map(str::to_string),
            limit: Some(limit),
            order: "observed_at ASC".into(),
            ..Default::default()
        };
        let mut mems = self.store.list_memories(&q)?;
        mems.sort_by(|a, b| {
            let ka = a.event_time.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| a.observed_at.clone());
            let kb = b.event_time.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| b.observed_at.clone());
            ka.cmp(&kb)
        });
        Ok(mems)
    }
}

pub const DEFAULT_HEADER: &str = "Relevant long-term memories (most relevant first):";

/// Human-readable channel summary ("semantic 0.83, keyword match, names the subject").
pub fn explain(ch: &IndexMap<String, f64>) -> String {
    let mut parts = Vec::new();
    if let Some(v) = ch.get("vector") {
        parts.push(format!("semantic {}", py_fixed(*v, 2)));
    }
    if ch.contains_key("keyword") {
        parts.push("keyword match".to_string());
    }
    if ch.contains_key("graph") {
        parts.push("entity graph".to_string());
    }
    if ch.contains_key("subject") {
        parts.push("names the subject".to_string());
    } else if ch.contains_key("entity") {
        parts.push("names a mentioned entity".to_string());
    }
    parts.join(", ")
}

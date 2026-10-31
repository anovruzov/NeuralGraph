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
use crate::temporal::{extract_query_time_cues, parse_datetime_flexible, OrderCue};
use chrono::{DateTime, NaiveDate, Utc};
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
    /// context block budget in characters (default 6000, was 3600). With `context_raw_messages = 3` this is what
    /// the overnight round-3 winner measured: the block grows from ~770 to ~1100 tokens. LongMemEval-50, two
    /// independent ingests, same reader protocol vs the previous defaults: strict_v2 56 -> 76 and 52 -> 66,
    /// official judge 58 -> 70 and 48 -> 68 (11:1 both), lenient-non-IDK 72 -> 80 and 64 -> 80; LoCoMo-66 strict
    /// 50 -> 56. Judge audit: research/overnight/judgments/round3_winner.md
    pub context_max_chars: usize,
    /// 15 beat 10 by 3-4 pts and 5 by ~10 on LoCoMo evidence recall
    pub context_k: usize,
    /// drop context lines scoring < this fraction of the top hit
    pub context_min_relative_score: f64,
    /// cosine floor for the vector channel (drops ~orthogonal noise)
    pub vector_min_sim: f64,
    /// a context line needs a keyword/graph hit or at least this cosine
    pub context_min_vector_sim: f64,
    /// source quotes rendered under each memory line of the context block (0 = off). Rendering up to 3 of the
    /// original messages a memory was extracted from lifted LoCoMo conv0 answer accuracy (strict 50 -> 54-56,
    /// lenient 77 -> 83-86). Quotes only use the budget left after the memory lines, so they never displace one.
    pub context_quotes: usize,
    /// quotes are cut to this many characters (then "…"); 300 was the measured setting
    pub context_quote_chars: usize,
    /// only quote messages whose speaker is not the assistant. The overnight LongMemEval-50 benchmark's winning
    /// rendering ("quotes_user") was measured this way: assistant turns echo the question and distract the reader.
    /// `false` appends assistant turns after the human ones as a fallback (the older, unmeasured behaviour).
    pub context_quotes_human_only: bool,
    /// episodic recall (default 3; 0 = off): up to this many raw chat messages matching the question (BM25 over the
    /// message store) are appended after the memory lines under `RAW_HEADER`, skipping messages already quoted in
    /// the block. They spend the budget left after memories and quotes. Default flipped 0 -> 3 together with
    /// `context_max_chars` 3600 -> 6000 on LongMemEval-50 evidence (two ingests: strict_v2 56 -> 76 / 52 -> 66,
    /// official judge 58 -> 70 / 48 -> 68, lenient-non-IDK 72 -> 80 / 64 -> 80; LoCoMo-66 strict 50 -> 56; see
    /// research/overnight/judgments/round3_winner.md). Human turns only unless `context_raw_include_assistant`.
    pub context_raw_messages: usize,
    /// raw messages are cut to this many characters (sentence-boundary rule of `cut_quote`)
    pub context_raw_message_chars: usize,
    /// also allow assistant-role messages as raw messages (default false)
    pub context_raw_include_assistant: bool,
    /// opt-in rule-based multi-query retrieval for `context_with_results` (default false): up to 3 extra sub-queries
    /// are derived from the question by rules (see [`multi_query_rules`]; no LLM), each searched with
    /// `max(context_k / 2, 5)` hits, and merged with the main search by memory id keeping the highest score.
    pub context_multi_query: bool,
    /// opt-in rule-based temporal prior (default 0.0 = off; env token `temporal_prior=<f64>`). When > 0, `search`
    /// reads time cues off the query by rules (see [`crate::temporal::extract_query_time_cues`]; no LLM): a date
    /// window ("in March", "March 2023", "2023-04-10", "last week", "3 months ago") multiplies the score of
    /// memories whose time (event time, else observation time) lies inside it by `1 + w`, and of memories with a
    /// known event time outside it by `1 / (1 + w/2)`; memories with no event time are never demoted. "most
    /// recent / latest" wording tilts toward the newest candidates by `1 + w * f` (`f` = position of the memory's
    /// time among the candidates, 0 oldest .. 1 newest), "first / earliest" toward the oldest. The factor shows as
    /// the `temporal` channel.
    pub temporal_prior: f64,
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
            context_max_chars: 6000,
            context_k: 15,
            context_min_relative_score: 0.25,
            vector_min_sim: 0.05,
            context_min_vector_sim: 0.3,
            context_quotes: 1,
            context_quote_chars: 300,
            context_quotes_human_only: true,
            context_raw_messages: 3,
            context_raw_message_chars: 400,
            context_raw_include_assistant: false,
            context_multi_query: false,
            temporal_prior: 0.0,
        }
    }
}

// ---------------------------------------------------------------------------------------- rule-based sub-queries
const MQ_MAX: usize = 3;
/// words that do not count as content in a sub-query (on top of `textutil::is_stop`)
const MQ_FILLER: &[&str] = &[
    "i", "am", "is", "a", "an", "of", "in", "on", "at", "to", "my", "me", "or", "as", "by", "if", "so", "do", "it", "be",
    "we", "up", "us", "am",
];
/// leading words stripped from a "how many/much/long, total" noun phrase
const MQ_AUX: &[&str] = &[
    "did", "i", "have", "do", "does", "has", "had", "was", "were", "is", "are", "am", "will", "would", "can", "could",
    "the", "a", "an", "my", "total", "number", "amount", "of", "been",
];
/// words that end a noun phrase ("hours of yoga | did I do")
const MQ_STOP_NP: &[&str] = &[
    "i", "did", "have", "do", "does", "has", "had", "was", "were", "is", "are", "am", "will", "would", "can", "could",
    "we", "you", "when", "while", "that", "which", "who", "where", "what", "if", "now",
];

static MQ_SPLIT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i),\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+").unwrap());
static MQ_BETWEEN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bbetween\s+(.+?)\s+and\s+(.+)$").unwrap());
static MQ_FROM_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bfrom\s+(.+?)\s+to\s+(.+)$").unwrap());
static MQ_BEFORE_AFTER_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\b(?:before|after)\s+(.+)$").unwrap());
static MQ_QUANT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(?:how\s+many|how\s+much|how\s+long|total)\b\s*(.*)$").unwrap());

fn mq_words(text: &str) -> Vec<&str> {
    text.split(|c: char| !(c.is_alphanumeric() || c == '\'' || c == '-')).filter(|w| !w.is_empty()).collect()
}

fn mq_content_words(text: &str) -> usize {
    mq_words(text)
        .into_iter()
        .filter(|w| {
            let l = w.to_lowercase();
            !crate::textutil::is_stop(&l) && !MQ_FILLER.contains(&l.as_str())
        })
        .count()
}

fn mq_clean(text: &str) -> String {
    normalize_ws(text).trim_matches(|c: char| c.is_whitespace() || matches!(c, '?' | '.' | '!' | ',' | ';' | ':')).to_string()
}

/// Up to three extra sub-queries for `question`, derived by rules only (no LLM):
/// (a) the question split on `, ` / " and " / " or " when every piece keeps at least two content words (a piece that
///     is too short is glued back to its neighbour);
/// (b) one sub-query per anchor of "between X and Y", "from X to Y", "before X", "after X";
/// (c) for "how many / how much / how long / total ..." the noun phrase alone (interrogative prefix and auxiliaries
///     stripped: how many, did I, have I, do I, was, were, the, a, ...). A noun phrase with fewer than two content
///     words falls back to the remainder with every auxiliary removed ("projects have I led" -> "projects led").
/// Sub-queries with fewer than two content words, or equal to the question (case-insensitive), are dropped; the rest
/// are deduplicated and capped at three, in the order (b), (c), (a): the anchors and noun phrases are more specific
/// than the clauses, whose first half mostly repeats the question. (c) is skipped when (b) matched.
pub fn multi_query_rules(question: &str) -> Vec<String> {
    let q = mq_clean(question);
    let mut cands: Vec<String> = Vec::new(); // (b) and (c) first: they are the more specific
    let mut clauses: Vec<String> = Vec::new();

    // (a) clause split
    let mut pieces: Vec<String> = Vec::new();
    let mut seps: Vec<String> = Vec::new();
    let mut last = 0;
    for m in MQ_SPLIT_RE.find_iter(&q) {
        pieces.push(q[last..m.start()].to_string());
        seps.push(m.as_str().to_string());
        last = m.end();
    }
    pieces.push(q[last..].to_string());
    while pieces.len() > 1 {
        let Some(i) = pieces.iter().position(|p| mq_content_words(p) < 2) else { break };
        // glue the short piece to its right neighbour (or its left one when it is the last)
        let (a, b) = if i + 1 < pieces.len() { (i, i + 1) } else { (i - 1, i) };
        let glued = format!("{}{}{}", pieces[a], seps[a], pieces[b]);
        pieces[a] = glued;
        pieces.remove(b);
        seps.remove(a);
    }
    if pieces.len() > 1 {
        clauses = pieces;
    }

    // (b) anchors
    for re in [&*MQ_BETWEEN_RE, &*MQ_FROM_RE] {
        if let Some(c) = re.captures(&q) {
            cands.push(c[1].to_string());
            cands.push(c[2].to_string());
        }
    }
    if let Some(c) = MQ_BEFORE_AFTER_RE.captures(&q) {
        cands.push(c[1].to_string());
    }

    // (c) quantity questions (skipped when (b) found anchors: the noun phrase would be a fragment of them)
    let quant = if cands.is_empty() { MQ_QUANT_RE.captures(&q) } else { None };
    if let Some(c) = quant {
        let words = mq_words(&c[1]);
        let lead = words.iter().take_while(|w| MQ_AUX.contains(&w.to_lowercase().as_str())).count();
        let rest = &words[lead..];
        let np: Vec<&str> =
            rest.iter().take_while(|w| !MQ_STOP_NP.contains(&w.to_lowercase().as_str())).copied().collect();
        let np = np.join(" ");
        if mq_content_words(&np) >= 2 {
            cands.push(np);
        } else {
            let all: Vec<&str> =
                rest.iter().filter(|w| !MQ_AUX.contains(&w.to_lowercase().as_str())).copied().collect();
            cands.push(all.join(" "));
        }
    }

    cands.extend(clauses);
    let mut out: Vec<String> = Vec::new();
    for c in cands {
        let c = mq_clean(&c);
        if mq_content_words(&c) < 2 || c.eq_ignore_ascii_case(&q) || out.iter().any(|o| o.eq_ignore_ascii_case(&c)) {
            continue;
        }
        out.push(c);
        if out.len() == MQ_MAX {
            break;
        }
    }
    out
}

/// An over-long quote cut after the last ". " / "? " / "! " within `limit` characters when that boundary lies past
/// half the limit, else hard-cut at `limit`; "…" marks the cut. Same rule as the benchmark harness (`cut_quote`).
fn cut_quote(text: &str, limit: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= limit {
        return text.to_string();
    }
    let head = &chars[..limit + 1];
    let end = head.windows(2).rposition(|w| matches!(w, ['.' | '?' | '!', ' ']));
    match end {
        Some(i) if i as f64 > limit as f64 / 2.0 => format!("{}…", head[..=i].iter().collect::<String>()),
        _ => format!("{}…", chars[..limit].iter().collect::<String>().trim_end()),
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
    /// the memory carries its own event time (`t_lo`/`t_hi` otherwise come from `observed_at`)
    has_event: Vec<bool>,
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
            has_event: Vec::with_capacity(n),
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
            d.has_event.push(has_event);
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
    /// ISO date used as "today" for the relative time cues of `RetrievalConfig::temporal_prior` ("last week", "3
    /// months ago"); the clock when absent. Ignored while the prior is off.
    pub now: Option<String>,
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
            now: None,
        }
    }
}

/// Query time cues resolved for one search (see `RetrievalConfig::temporal_prior`).
struct TemporalPrior {
    /// inclusive window as ISO bounds comparable with `IndexData::t_lo` / `t_hi`
    window: Option<(String, String)>,
    order: Option<OrderCue>,
    /// (oldest day, newest day) over the fused candidates, when the ordering cue can be applied
    span: Option<(NaiveDate, NaiveDate)>,
}

fn index_day(t: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(py_prefix(t, 10), "%Y-%m-%d").ok()
}

impl TemporalPrior {
    /// `None` when the query carries no time cue.
    fn new(query: &str, now: Option<&str>, index: &IndexData, fused_ids: &[String]) -> Option<Self> {
        let today = now
            .and_then(|n| parse_datetime_flexible(Some(n)).ok().flatten())
            .map(|f| f.naive.date())
            .unwrap_or_else(|| utcnow().date_naive());
        let cues = extract_query_time_cues(query, today);
        let window = cues.window.map(|(a, b)| (format!("{a}T00:00:00"), format!("{b}T23:59:59")));
        let mut span = None;
        if cues.order.is_some() {
            let days: Vec<NaiveDate> =
                fused_ids.iter().filter_map(|m| index.position(m)).filter_map(|i| index_day(&index.t_lo[i])).collect();
            if let (Some(lo), Some(hi)) = (days.iter().min(), days.iter().max()) {
                if hi > lo {
                    span = Some((*lo, *hi));
                }
            }
        }
        if window.is_none() && span.is_none() {
            return None;
        }
        Some(TemporalPrior { window, order: cues.order, span })
    }

    /// Score multiplier of the memory at index position `i` (1.0 = untouched).
    fn factor(&self, index: &IndexData, i: usize, w: f64) -> f64 {
        let mut f = 1.0;
        if let Some((lo, hi)) = &self.window {
            let inside = index.t_hi[i].as_str() >= lo.as_str() && index.t_lo[i].as_str() <= hi.as_str();
            if inside {
                f *= 1.0 + w;
            } else if index.has_event[i] {
                f /= 1.0 + w / 2.0;
            }
        }
        if let (Some(order), Some((lo, hi))) = (self.order, self.span) {
            if let Some(d) = index_day(&index.t_lo[i]) {
                let newest = (d - lo).num_days() as f64 / (hi - lo).num_days() as f64;
                f *= 1.0 + w * if order == OrderCue::Latest { newest } else { 1.0 - newest };
            }
        }
        f
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
        let tprior = (cfg.temporal_prior > 0.0).then(|| TemporalPrior::new(&query, opts.now.as_deref(), &index, &fused_ids)).flatten();
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
            if let Some(tp) = &tprior {
                let f = tp.factor(&index, i, cfg.temporal_prior);
                if f != 1.0 {
                    s *= f;
                    boosts.push(("temporal", py_round(f, 4)));
                }
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
        let results = if cfg.context_multi_query {
            let mut merged = self.search(query, &opts).await?;
            let subs = multi_query_rules(query);
            if !subs.is_empty() {
                let mut sub_opts = opts.clone();
                sub_opts.k = (opts.k / 2).max(5);
                sub_opts.query_embedding = None; // the caller's embedding belongs to the main query only
                let mut at: HashMap<String, usize> =
                    merged.iter().enumerate().map(|(i, r)| (r.memory.memory_id.clone(), i)).collect();
                for sub in &subs {
                    for r in self.search(sub, &sub_opts).await? {
                        match at.get(&r.memory.memory_id) {
                            Some(&i) => {
                                if r.score > merged[i].score {
                                    merged[i] = r;
                                }
                            }
                            None => {
                                at.insert(r.memory.memory_id.clone(), merged.len());
                                merged.push(r);
                            }
                        }
                    }
                }
                // stable: on equal scores the main query's results (pushed first) stay ahead
                merged.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
                merged.truncate(opts.k);
            }
            merged
        } else {
            self.search(query, &opts).await?
        };
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
        let mut line_of = Vec::new();
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
            line_of.push(lines.len() - 1);
            used += n + 1;
            included.push(r);
        }
        if lines.len() == 1 {
            return Ok((String::new(), Vec::new()));
        }
        let mut used = used;
        let mut seen = IndexSet::new();
        let mut quotes: Vec<Vec<String>> = vec![Vec::new(); lines.len()];
        if cfg.context_quotes > 0 {
            self.attach_quotes(&included, &line_of, limit, &mut used, &mut seen, &mut quotes)?;
        }
        let mut out = Vec::with_capacity(lines.len() + quotes.iter().map(Vec::len).sum::<usize>());
        for (line, q) in lines.into_iter().zip(quotes) {
            out.push(line);
            out.extend(q);
        }
        if cfg.context_raw_messages > 0 {
            self.append_raw_messages(query, filters, limit, used, &seen, &mut out)?;
        }
        Ok((out.join("\n"), included))
    }

    /// Source quotes under each included memory line. Spends only what the memory lines left over.
    fn attach_quotes(
        &self,
        included: &[RetrievedMemory],
        line_of: &[usize],
        limit: usize,
        used: &mut usize,
        seen: &mut IndexSet<String>,
        quotes: &mut [Vec<String>],
    ) -> Result<()> {
        let cfg = &self.config;
        // Quotes only spend what the memory lines left over, so they can never push a memory out of the block.
        // The search ran without sources (`with_sources = false`); load them only for the lines that made it in.
        let included_ids: Vec<String> = included.iter().map(|r| r.memory.memory_id.clone()).collect();
        let sourced: HashMap<String, Vec<String>> = self
            .store
            .get_memories_by_ids(&included_ids, true)?
            .into_iter()
            .map(|m| (m.memory_id, m.source_message_ids))
            .collect();
        let all_ids: Vec<String> = sourced.values().flatten().cloned().collect();
        let by_id: HashMap<String, ChatMessage> =
            self.store.get_messages_by_ids(&all_ids)?.into_iter().map(|m| (m.message_id.clone(), m)).collect();
        // a quote already shown under an earlier memory (whitespace-collapsed, lowercased) is not repeated
        for (r, &at) in included.iter().zip(line_of) {
            let ids = sourced.get(&r.memory.memory_id).map(Vec::as_slice).unwrap_or_default();
            let srcs: Vec<&ChatMessage> = ids.iter().filter_map(|id| by_id.get(id)).collect();
            let is_bot = |m: &ChatMessage| m.role == "assistant" || m.speaker.trim().eq_ignore_ascii_case("assistant");
            let (bot, human): (Vec<_>, Vec<_>) = srcs.into_iter().partition(|m| is_bot(m));
            let bot = if cfg.context_quotes_human_only { Vec::new() } else { bot };
            for m in human.into_iter().chain(bot) {
                if quotes[at].len() >= cfg.context_quotes {
                    break;
                }
                let raw = m.text.replace('\n', " ");
                let norm = normalize_ws(&raw).to_lowercase();
                if norm.is_empty() || !seen.insert(norm) {
                    continue;
                }
                let text = cut_quote(&raw, cfg.context_quote_chars);
                let when =
                    py_prefix(m.sent_at.as_deref().filter(|s| !s.is_empty()).unwrap_or(&r.memory.observed_at), 10);
                let line = format!("    > {} ({when}): \"{text}\"", m.speaker);
                let n = py_len(&line) + 1;
                if *used + n > limit {
                    break; // out of budget: this memory (and, in effect, later ones) keep their line without quotes
                }
                *used += n;
                quotes[at].push(line);
            }
        }
        Ok(())
    }

    /// Episodic recall: raw messages matching the query, appended after the memory/quote lines.
    fn append_raw_messages(
        &self,
        query: &str,
        filters: &SearchOptions,
        limit: usize,
        mut used: usize,
        seen: &IndexSet<String>,
        out: &mut Vec<String>,
    ) -> Result<()> {
        let cfg = &self.config;
        let filter =
            ChannelFilter { chat_id: filters.chat_id.clone(), speaker: filters.speaker.clone(), ..Default::default() };
        // over-fetch: quoted and assistant messages are skipped after ranking
        let depth = (cfg.context_raw_messages * 20).max(100) as i64;
        let hits = self.store.message_keyword_candidates(&normalize_ws(query), depth, &filter)?;
        if hits.is_empty() {
            return Ok(());
        }
        let ids: Vec<String> = hits.iter().map(|h| h.0.clone()).collect();
        let mut by_id: HashMap<String, ChatMessage> =
            self.store.get_messages_by_ids(&ids)?.into_iter().map(|m| (m.message_id.clone(), m)).collect();
        let mut seen_raw = IndexSet::new();
        let mut lines = Vec::new();
        let mut header_cost = py_len(RAW_HEADER) + 1;
        for id in &ids {
            if lines.len() >= cfg.context_raw_messages {
                break;
            }
            let Some(m) = by_id.remove(id) else { continue };
            let is_bot = m.role == "assistant" || m.speaker.trim().eq_ignore_ascii_case("assistant");
            if is_bot && !cfg.context_raw_include_assistant {
                continue;
            }
            let norm = normalize_ws(&m.text.replace('\n', " ")).to_lowercase();
            if norm.is_empty() || seen.contains(&norm) || !seen_raw.insert(norm) {
                continue;
            }
            let text = cut_quote(&normalize_ws(&m.text.replace('\n', " ")), cfg.context_raw_message_chars);
            let when = py_prefix(m.sent_at.as_deref().filter(|s| !s.is_empty()).unwrap_or(&m.ingested_at), 10);
            let line = format!("- ({}, {when}) \"{text}\"", m.speaker);
            let n = py_len(&line) + 1;
            if used + header_cost + n > limit {
                continue; // a shorter, lower-ranked message may still fit
            }
            used += header_cost + n;
            header_cost = 0;
            lines.push(line);
        }
        if !lines.is_empty() {
            out.push(RAW_HEADER.to_string());
            out.extend(lines);
        }
        Ok(())
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

/// Section header of the episodic-recall (raw messages) block.
pub const RAW_HEADER: &str = "Raw messages that match the question:";

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
    if let Some(v) = ch.get("temporal") {
        parts.push(format!("temporal x{}", py_fixed(*v, 2)));
    }
    parts.join(", ")
}

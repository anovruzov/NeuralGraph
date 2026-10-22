//! Mega search: vector + BM25 keyword + entity-graph hop, fused by Reciprocal Rank Fusion (port of
//! `retrieval/mega_search.py`).
//!
//! - Vector channel: cosine over message nodes and question+reply pair nodes (pair hits map back to their
//!   member messages), in float32 exactly as numpy computed it ([`crate::numpy`]).
//! - Keyword channel: BM25 over message text (`rank_bm25.BM25Okapi`, ported by the product crate).
//! - Graph channel: HippoRAG-style personalised PageRank over a message <-> entity bipartite graph plus the
//!   message <-> next-message chain, seeded by messages sharing entities with the query and by the top hits of
//!   the other channels. With an LLM-built entity graph ([`crate::retrieval::kg_extractor`]) its entities replace
//!   the regex ones and typed relations add entity -> entity hops.
//! - Fusion: RRF (Cormack et al.).
//!
//! All per conversation; speaker routing is a pool filter applied inside every channel. No LLM calls.
//!
//! Set order: the Python kept entity sets as `set`s, and the graph channel's PageRank mass is summed in their
//! iteration order, so its scores (last bits) and its ranking among equal scores varied with the process's hash
//! seed; the fused ranking inherited that. Every set here is insertion-ordered ([`IndexSet`], intersections keep
//! the left operand's order), and the golden fixture pins the result against the reference module run with
//! insertion-ordered sets (`tests/golden/_ordered_sets.py`), whose score maps agree with the original module's.

use crate::numpy::{normalized_f32_eps, normalized_rows_f32, sgemv};
use crate::pyre::findall_whole_words;
use crate::retrieval::data_types::NodeRef;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::bm25::Bm25Okapi;
use neuralgraph_memory::pycompat::py_round_int;
use regex::Regex;
use serde_json::Value;
use std::sync::LazyLock;

/// `_STOP`
pub const STOP: [&str; 67] = [
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our", "with", "that",
    "this", "have", "from", "they", "what", "when", "where", "which", "who", "how", "did", "does", "has", "his", "she",
    "him", "them", "been", "were", "will", "would", "could", "should", "about", "into", "your", "just", "like", "some",
    "than", "then", "there", "their", "also", "very", "really", "much", "many", "more", "yeah", "yes", "hey", "thanks",
    "thank", "sounds", "sure", "good", "great", "awesome", "love", "know",
];

static TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-z0-9]+").unwrap());

fn is_stop(w: &str) -> bool {
    STOP.contains(&w)
}

/// `tokenize`: lowercase alphanumeric runs of three or more characters, minus stopwords.
pub fn tokenize(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    TOKEN_RE.find_iter(&lower).map(|m| m.as_str()).filter(|w| w.len() >= 3 && !is_stop(w)).map(str::to_string).collect()
}

/// `entities_of`: capitalised words (lowercased) plus content words of five or more letters.
pub fn entities_of(text: &str) -> IndexSet<String> {
    let mut caps: IndexSet<String> =
        findall_whole_words(text, |c| c.is_ascii_uppercase(), |c| c.is_ascii_alphabetic(), 1)
            .into_iter()
            .map(str::to_lowercase)
            .collect();
    caps.shift_remove("i");
    for w in tokenize(text) {
        if w.chars().count() >= 5 {
            caps.insert(w);
        }
    }
    caps
}

/// Reciprocal Rank Fusion: `sum(w / (k + rank))` per document, best first (ties keep first-seen order).
/// `weights` defaults to all ones (also when empty, as Python's `weights or ...` did).
pub fn rrf(rankings: &[Vec<String>], weights: Option<&[f64]>, k: usize) -> Vec<(String, f64)> {
    let ones = vec![1.0; rankings.len()];
    let weights = weights.filter(|w| !w.is_empty()).unwrap_or(&ones);
    let mut score: IndexMap<String, f64> = IndexMap::new();
    for (ranking, w) in rankings.iter().zip(weights) {
        for (r, doc_id) in ranking.iter().enumerate() {
            *score.entry(doc_id.clone()).or_insert(0.0) += w / (k + r + 1) as f64;
        }
    }
    sorted_desc(score)
}

/// `sorted(d.items(), key=lambda x: -x[1])`: stable, so equal scores keep insertion order.
fn sorted_desc(d: IndexMap<String, f64>) -> Vec<(String, f64)> {
    let mut items: Vec<(String, f64)> = d.into_iter().collect();
    items.sort_by(|a, b| (-a.1).partial_cmp(&-b.1).unwrap_or(std::cmp::Ordering::Equal));
    items
}

fn top_ids(d: IndexMap<String, f64>, k: usize) -> Vec<String> {
    sorted_desc(d).into_iter().take(k).map(|(m, _)| m).collect()
}

/// The fused index of one conversation.
pub struct MegaIndex {
    pub nodes: Vec<NodeRef>,
    pub pairs: Vec<NodeRef>,
    by_id: IndexMap<String, NodeRef>,
    /// node id -> position (`order`; kept for parity, unused by the channels)
    pub order: IndexMap<String, usize>,
    bm25: Bm25Okapi,
    /// row-normalised float32 message embeddings and their width
    m: Vec<f32>,
    cols: usize,
    /// row-normalised float32 pair embeddings
    p: Option<Vec<f32>>,
    /// whether a (non-empty) KG was given: Python tested `if self.kg:`
    kg: bool,
    pub kg_relation_weight: f64,
    graph: Graph,
}

/// The message <-> entity graph with every id replaced by its position, so PageRank runs on integers. Message
/// positions follow `msg_ents`' key order (node ids, first occurrence), entity positions `ent_msgs`' key order;
/// every list keeps the order the Python iterated the corresponding set in.
struct Graph {
    /// message ids (`msg_ents` keys)
    mids: IndexSet<String>,
    /// entity names (`ent_msgs` keys, after dropping hubs)
    ents: IndexSet<String>,
    /// per message: its entities (`msg_ents[mid]`)
    msg_ents: Vec<Vec<usize>>,
    /// per entity: its messages (`ent_msgs[e]`)
    ent_msgs: Vec<Vec<usize>>,
    /// per entity: the message lists of its related entities that survived hub filtering (`ent_ent[e]`, KG only)
    ent_related_msgs: Vec<Vec<usize>>,
    /// whether `ent_ent` had any entry (Python tested `if self.ent_ent:`)
    has_relations: bool,
    next_id: Vec<Option<usize>>,
    prev_id: Vec<Option<usize>>,
}

/// A `defaultdict(float)` over message positions that remembers insertion order.
struct OrderedAcc {
    val: Vec<f64>,
    present: Vec<bool>,
    order: Vec<usize>,
}

impl OrderedAcc {
    fn new(n: usize) -> Self {
        OrderedAcc { val: vec![0.0; n], present: vec![false; n], order: Vec::new() }
    }
    fn add(&mut self, i: usize, x: f64) {
        if !self.present[i] {
            self.present[i] = true;
            self.order.push(i);
            self.val[i] = 0.0;
        }
        self.val[i] += x;
    }
    fn items(&self) -> impl Iterator<Item = (usize, f64)> + '_ {
        self.order.iter().map(|&i| (i, self.val[i]))
    }
}

impl Graph {
    fn build(
        nodes: &[NodeRef],
        msg_ents: &IndexMap<String, IndexSet<String>>,
        ent_msgs: &IndexMap<String, IndexSet<String>>,
        ent_ent: &IndexMap<String, IndexSet<String>>,
    ) -> Graph {
        let mids: IndexSet<String> = msg_ents.keys().cloned().collect();
        let ents: IndexSet<String> = ent_msgs.keys().cloned().collect();
        let mid = |id: &str| mids.get_index_of(id).expect("message of the index");
        let msg_ents_idx = msg_ents
            .values()
            .map(|es| es.iter().map(|e| ents.get_index_of(e).expect("filtered entity")).collect())
            .collect();
        let ent_msgs_idx: Vec<Vec<usize>> = ent_msgs.values().map(|ms| ms.iter().map(|m| mid(m)).collect()).collect();
        let ent_related_msgs = ents
            .iter()
            .map(|e| {
                ent_ent
                    .get(e)
                    .into_iter()
                    .flatten()
                    .filter_map(|e2| ents.get_index_of(e2))
                    .filter(|&j| !ent_msgs_idx[j].is_empty())
                    .collect()
            })
            .collect();
        // next_id = {nodes[i]: nodes[i + 1]}; prev_id = {v: k for k, v in next_id.items()}
        let mut next_map: IndexMap<usize, usize> = IndexMap::new();
        for w in nodes.windows(2) {
            next_map.insert(mid(&w[0].borrow().node_id), mid(&w[1].borrow().node_id));
        }
        let mut next_id = vec![None; mids.len()];
        let mut prev_id = vec![None; mids.len()];
        for (&k, &v) in &next_map {
            next_id[k] = Some(v);
            prev_id[v] = Some(k);
        }
        Graph {
            msg_ents: msg_ents_idx,
            ent_msgs: ent_msgs_idx,
            ent_related_msgs,
            has_relations: !ent_ent.is_empty(),
            next_id,
            prev_id,
            mids,
            ents,
        }
    }
}

fn embedding_rows(nodes: &[NodeRef], what: &str) -> Result<(Vec<f32>, usize), String> {
    let rows: Vec<Vec<f64>> = nodes
        .iter()
        .map(|n| n.borrow().embedding.clone().ok_or_else(|| format!("{what} node without embedding")))
        .collect::<Result<_, _>>()?;
    let width = rows.first().map_or(0, Vec::len);
    if rows.iter().any(|r| r.len() != width) {
        return Err(format!("ValueError: inhomogeneous {what} embeddings"));
    }
    let refs: Vec<&[f64]> = rows.iter().map(Vec::as_slice).collect();
    Ok(normalized_rows_f32(&refs, 1e-9))
}

fn kg_str<'a>(v: &'a Value, what: &str) -> Result<&'a str, String> {
    v.as_str().ok_or_else(|| format!("non-string KG {what}: {v}"))
}

impl MegaIndex {
    /// Build the index. `kg` is `extract_conversation` output aligned by message index with `nodes` (optionally
    /// with `"_union_regex": true` to add the regex entities to the LLM ones). `Err` where Python raised: an
    /// empty corpus (`rank_bm25` divides by zero), missing or ragged embeddings, a malformed KG.
    pub fn new(
        nodes: &[NodeRef],
        pair_nodes: &[NodeRef],
        kg: Option<&Value>,
        kg_relation_weight: f64,
    ) -> Result<Self, String> {
        let nodes = nodes.to_vec();
        let pairs = pair_nodes.to_vec();
        let mut by_id = IndexMap::new();
        let mut order = IndexMap::new();
        for (i, n) in nodes.iter().enumerate() {
            let id = n.borrow().node_id.clone();
            by_id.insert(id.clone(), n.clone());
            order.insert(id, i);
        }
        let tokens: Vec<Vec<String>> = nodes.iter().map(|n| tokenize(&n.borrow().content)).collect();
        let bm25 = Bm25Okapi::new(&tokens).ok_or("ZeroDivisionError: division by zero")?;
        let (m, cols) = embedding_rows(&nodes, "message")?;
        let p = if pairs.is_empty() {
            None
        } else {
            let (p, pcols) = embedding_rows(&pairs, "pair")?;
            if pcols != cols {
                return Err("ValueError: pair and message embeddings differ in width".into());
            }
            Some(p)
        };

        let kg_obj = kg.filter(|v| neuralgraph_memory::pycompat::py_truthy(v));
        let mut ent_ent: IndexMap<String, IndexSet<String>> = IndexMap::new();
        let mut msg_ents: IndexMap<String, IndexSet<String>> = IndexMap::new();
        if let Some(kg) = kg_obj {
            let messages = kg.get("messages").and_then(Value::as_array).ok_or("KeyError: 'messages'")?;
            let mut per_msg: IndexMap<i64, &Value> = IndexMap::new();
            for m in messages {
                per_msg.insert(m.get("idx").and_then(Value::as_i64).ok_or("KeyError: 'idx'")?, m);
            }
            for (i, n) in nodes.iter().enumerate() {
                let mut ents = IndexSet::new();
                if let Some(list) = per_msg.get(&(i as i64)).and_then(|m| m.get("entities")) {
                    for e in list.as_array().ok_or("KG entities is not a list")? {
                        ents.insert(kg_str(e, "entity")?.to_string());
                    }
                }
                msg_ents.insert(n.borrow().node_id.clone(), ents);
            }
            if kg.get("_union_regex").is_some_and(neuralgraph_memory::pycompat::py_truthy) {
                for n in &nodes {
                    let n = n.borrow();
                    let extra = entities_of(&n.content);
                    msg_ents.entry(n.node_id.clone()).or_default().extend(extra);
                }
            }
            for m in messages {
                let Some(triples) = m.get("triples") else { continue };
                for t in triples.as_array().ok_or("KG triples is not a list")? {
                    let t = t
                        .as_array()
                        .filter(|t| t.len() == 3)
                        .ok_or_else(|| format!("ValueError: not enough values to unpack (expected 3) in triple {t}"))?;
                    let (s, o) = (kg_str(&t[0], "triple subject")?, kg_str(&t[2], "triple object")?);
                    ent_ent.entry(s.to_string()).or_default().insert(o.to_string());
                    ent_ent.entry(o.to_string()).or_default().insert(s.to_string());
                }
            }
        } else {
            for n in &nodes {
                let n = n.borrow();
                msg_ents.insert(n.node_id.clone(), entities_of(&n.content));
            }
        }

        let mut ent_msgs: IndexMap<String, IndexSet<String>> = IndexMap::new();
        for (mid, ents) in &msg_ents {
            for e in ents {
                ent_msgs.entry(e.clone()).or_default().insert(mid.clone());
            }
        }
        // drop hub entities that touch too many messages (they carry no signal)
        let n_msgs = nodes.len().max(1) as f64;
        let hub_cap = f64::max(3.0, if kg_obj.is_some() { 0.10 } else { 0.05 } * n_msgs);
        ent_msgs.retain(|_, ms| ms.len() as f64 <= hub_cap);
        for ents in msg_ents.values_mut() {
            ents.retain(|e| ent_msgs.contains_key(e));
        }

        let graph = Graph::build(&nodes, &msg_ents, &ent_msgs, &ent_ent);
        Ok(MegaIndex { nodes, pairs, by_id, order, bm25, m, cols, p, kg: kg_obj.is_some(), kg_relation_weight, graph })
    }

    // ---- channels -------------------------------------------------------------------------------------------

    /// Cosine ranking of the pool's messages; a pair node's score counts for each member message.
    pub fn vector_rank(&self, qvec: &[f64], pool: &IndexSet<String>, k: usize) -> Vec<String> {
        let q = normalized_f32_eps(&crate::numpy::to_f32(qvec), 1e-9);
        let mut scored: IndexMap<String, f64> = IndexMap::new();
        let sims = sgemv(&self.m, self.nodes.len(), &q);
        for (i, n) in self.nodes.iter().enumerate() {
            let id = &n.borrow().node_id;
            if pool.contains(id) {
                scored.insert(id.clone(), sims[i] as f64);
            }
        }
        if let Some(p) = &self.p {
            let psims = sgemv(p, self.pairs.len(), &q);
            for (j, pair) in self.pairs.iter().enumerate() {
                let pair = pair.borrow();
                let members = pair.metadata.get("pair_members").and_then(Value::as_array);
                for mid in members.into_iter().flatten().filter_map(Value::as_str) {
                    if pool.contains(mid) {
                        let cur = scored.get(mid).copied().unwrap_or(-1.0);
                        let s = psims[j] as f64;
                        // max(cur, s) returns the first argument unless the second is larger
                        scored.insert(mid.to_string(), if s > cur { s } else { cur });
                    }
                }
            }
        }
        top_ids(scored, k)
    }

    /// BM25 ranking of the pool's messages with a positive score.
    pub fn keyword_rank(&self, query: &str, pool: &IndexSet<String>, k: usize) -> Vec<String> {
        let toks = tokenize(query);
        if toks.is_empty() {
            return Vec::new();
        }
        let scores = self.bm25.get_scores(&toks);
        let mut ranked: Vec<(String, f64)> = Vec::new();
        for (i, s) in scores.iter().enumerate() {
            let id = &self.nodes[i].borrow().node_id;
            if pool.contains(id) && *s > 0.0 {
                ranked.push((id.clone(), *s));
            }
        }
        ranked.sort_by(|a, b| (-a.1).partial_cmp(&-b.1).unwrap_or(std::cmp::Ordering::Equal));
        ranked.into_iter().take(k).map(|(m, _)| m).collect()
    }

    /// Personalised PageRank over the message-entity graph (+ temporal chain), restarting at the seeds.
    #[allow(clippy::too_many_arguments)] // the Python keyword arguments, one to one
    pub fn graph_rank(
        &self,
        query: &str,
        seeds: &[String],
        pool: &IndexSet<String>,
        k: usize,
        alpha: f64,
        iters: usize,
        hop_temporal: bool,
    ) -> Vec<String> {
        top_ids(self.graph_scores(query, seeds, pool, alpha, iters, hop_temporal), k)
    }

    /// The PageRank mass of every reached message (`p` at the end of `graph_rank`), in insertion order.
    pub fn graph_scores(
        &self,
        query: &str,
        seeds: &[String],
        pool: &IndexSet<String>,
        alpha: f64,
        iters: usize,
        hop_temporal: bool,
    ) -> IndexMap<String, f64> {
        let g = &self.graph;
        let mut q_ents: IndexSet<usize> = entities_of(query).iter().filter_map(|e| g.ents.get_index_of(e)).collect();
        if self.kg {
            // also match multi-word KG entity names appearing in the query
            let ql = query.to_lowercase();
            for (j, e) in g.ents.iter().enumerate() {
                if e.chars().count() >= 4 && ql.contains(e.as_str()) {
                    q_ents.insert(j);
                }
            }
        }
        // positions beyond the index's messages: seeds that are not messages (they never get neighbours)
        let mut ids: Vec<&str> = g.mids.iter().map(String::as_str).collect();
        let mut extra: IndexMap<&str, usize> = IndexMap::new();
        let seed_pos: Vec<usize> = seeds
            .iter()
            .take(10)
            .map(|s| {
                g.mids.get_index_of(s).unwrap_or_else(|| {
                    *extra.entry(s.as_str()).or_insert_with(|| {
                        ids.push(s.as_str());
                        ids.len() - 1
                    })
                })
            })
            .collect();
        let in_pool: Vec<bool> = ids.iter().map(|id| pool.contains(*id)).collect();
        let n = ids.len();

        let mut seed_w = OrderedAcc::new(n);
        for &e in &q_ents {
            let ms = &g.ent_msgs[e];
            for &m in ms {
                if in_pool[m] {
                    seed_w.add(m, 1.0 / ms.len() as f64);
                }
            }
        }
        for (r, &m) in seed_pos.iter().enumerate() {
            if in_pool[m] {
                seed_w.add(m, 1.0 / (r + 1) as f64);
            }
        }
        if seed_w.order.is_empty() {
            return IndexMap::new();
        }
        let total = seed_w.items().fold(0.0, |s, (_, w)| s + w);
        let mut p0 = OrderedAcc::new(n);
        for (m, w) in seed_w.items() {
            p0.add(m, w / total);
        }
        let repeat = py_round_int(self.kg_relation_weight * 2.0).max(1) as usize;
        let mut p: Vec<(usize, f64)> = p0.items().collect();
        let mut nbrs: Vec<usize> = Vec::new();
        for _ in 0..iters {
            let mut nxt = OrderedAcc::new(n);
            for &(mid, mass) in &p {
                nbrs.clear();
                if mid < g.mids.len() {
                    for &e in &g.msg_ents[mid] {
                        nbrs.extend_from_slice(&g.ent_msgs[e]);
                        if g.has_relations {
                            // 2-hop via typed relations: msg -> entity -> related entity -> its msgs
                            for &e2 in &g.ent_related_msgs[e] {
                                for _ in 0..repeat {
                                    nbrs.extend_from_slice(&g.ent_msgs[e2]);
                                }
                            }
                        }
                    }
                    if hop_temporal {
                        // `if t:` also skipped an empty id
                        nbrs.extend(g.next_id[mid].filter(|&t| !ids[t].is_empty()));
                        nbrs.extend(g.prev_id[mid].filter(|&t| !ids[t].is_empty()));
                    }
                }
                nbrs.retain(|&x| in_pool[x] && x != mid);
                if nbrs.is_empty() {
                    nxt.add(mid, mass * (1.0 - alpha));
                    continue;
                }
                let share = mass * (1.0 - alpha) / nbrs.len() as f64;
                for &x in &nbrs {
                    nxt.add(x, share);
                }
            }
            for (m, w) in p0.items() {
                nxt.add(m, alpha * w);
            }
            p = nxt.items().collect();
        }
        p.into_iter().map(|(m, w)| (ids[m].to_string(), w)).collect()
    }

    // ---- fused retrieval ------------------------------------------------------------------------------------

    /// Fuse the requested channels (`"V"`, `"K"`, `"G"` in `channels`) and any extra rankings (weight `"X"`) by
    /// RRF; the top `k` `(node, score)`. `pool_ids` restricts every channel (default: all messages). `Err` is
    /// Python's `KeyError` for a fused id that is not a message of this index (from an extra ranking or a pair
    /// member).
    #[allow(clippy::too_many_arguments)] // the Python keyword arguments, one to one
    pub fn retrieve(
        &self,
        query: &str,
        qvec: &[f64],
        pool_ids: Option<&IndexSet<String>>,
        k: usize,
        channels: &str,
        weights: Option<&IndexMap<String, f64>>,
        extra_rankings: &[Vec<String>],
        depth: usize,
    ) -> Result<Vec<(NodeRef, f64)>, String> {
        let all: IndexSet<String>;
        let pool = match pool_ids {
            Some(p) => p,
            None => {
                all = self.by_id.keys().cloned().collect();
                &all
            }
        };
        let weight = |c: &str| weights.and_then(|w| w.get(c)).copied().unwrap_or(1.0);
        let mut rankings: Vec<Vec<String>> = Vec::new();
        let mut ws: Vec<f64> = Vec::new();
        let v = if channels.contains('V') { self.vector_rank(qvec, pool, depth) } else { Vec::new() };
        let kw = if channels.contains('K') { self.keyword_rank(query, pool, depth) } else { Vec::new() };
        if channels.contains('V') {
            rankings.push(v.clone());
            ws.push(weight("V"));
        }
        if channels.contains('K') {
            rankings.push(kw.clone());
            ws.push(weight("K"));
        }
        if channels.contains('G') {
            let seeds: Vec<String> = if !v.is_empty() || !kw.is_empty() {
                rrf(&[v, kw], None, 60).into_iter().take(10).map(|(m, _)| m).collect()
            } else {
                Vec::new()
            };
            rankings.push(self.graph_rank(query, &seeds, pool, depth, 0.5, 12, true));
            ws.push(weight("G"));
        }
        for extra in extra_rankings {
            rankings.push(extra.clone());
            ws.push(weight("X"));
        }
        let fused = rrf(&rankings, Some(&ws), 60);
        fused
            .into_iter()
            .take(k)
            .map(|(m, s)| self.by_id.get(&m).cloned().map(|n| (n, s)).ok_or_else(|| format!("KeyError: '{m}'")))
            .collect()
    }

    /// Width of the embeddings the index was built with.
    pub fn dim(&self) -> usize {
        self.cols
    }
}

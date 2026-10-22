//! Storage interface and the in-memory implementation (port of `retrieval/storage.py`).
//!
//! The Python storage was `async` but never awaited anything real for the in-memory store; here the trait is
//! synchronous and uses interior mutability, so callers keep `&self` handles the way they kept object references.
//! Do not hold a `borrow()` of a node while saving an edge from it: `save_edge` updates the source node's
//! fairness counters in place, exactly as the Python did.
//!
//! Ordering: the Python indexes were `set`s of ids, whose iteration order depends on per-process string hashing
//! and was therefore not reproducible even in Python. Every index here is insertion-ordered (`IndexSet`), the
//! deterministic analogue; results sorted by score are unaffected except among exactly equal scores.

use crate::retrieval::data_types::{cosine_similarity, ConsolidationState, EdgeRef, EdgeType, NodeLayer, NodeRef};
use crate::retrieval::lsh::{LshConfig, RandomProjectionLsh};
use indexmap::{IndexMap, IndexSet};
use serde_json::{json, Map, Value};
use std::cell::RefCell;

/// `edge_types` arguments: `None` or an empty list means "all types" (Python tested `if edge_types:`).
fn type_allowed(filter: Option<&[EdgeType]>, t: EdgeType) -> bool {
    match filter {
        Some(f) if !f.is_empty() => f.contains(&t),
        _ => true,
    }
}

/// Neighbour direction for [`NeuralGraphStorage::get_neighbors`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Direction {
    Outgoing,
    Incoming,
    Both,
}

impl Direction {
    /// `"outgoing" | "incoming" | "both"`; anything else matches neither branch, as in Python.
    pub fn parse(s: &str) -> Option<Direction> {
        match s {
            "outgoing" => Some(Direction::Outgoing),
            "incoming" => Some(Direction::Incoming),
            "both" => Some(Direction::Both),
            _ => None,
        }
    }
}

/// Storage protocol shared by the in-memory and SQLite stores.
pub trait NeuralGraphStorage {
    fn save_node(&self, node: &NodeRef);
    fn get_node(&self, node_id: &str) -> Option<NodeRef>;
    fn update_node(&self, node: &NodeRef) {
        self.save_node(node)
    }
    fn delete_node(&self, node_id: &str) -> bool;
    fn get_nodes_by_session(&self, session_key: &str, layer: Option<NodeLayer>) -> Vec<NodeRef>;
    fn get_nodes_by_layer(&self, session_key: &str, layer: NodeLayer) -> Vec<NodeRef>;
    fn get_all_nodes(&self, session_key: &str) -> Vec<NodeRef> {
        self.get_nodes_by_session(session_key, None)
    }

    fn save_edge(&self, edge: &EdgeRef);
    fn get_edge(&self, edge_id: &str) -> Option<EdgeRef>;
    fn delete_edge(&self, edge_id: &str) -> bool;
    fn get_edges_from(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> Vec<EdgeRef>;
    fn get_edges_to(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> Vec<EdgeRef>;
    fn get_edge_between(&self, source_id: &str, target_id: &str, edge_type: Option<EdgeType>) -> Option<EdgeRef>;
    fn get_all_edges(&self, session_key: &str) -> Vec<EdgeRef>;

    fn vector_search(
        &self,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
        layer_filter: Option<&[NodeLayer]>,
    ) -> Vec<(NodeRef, f64)>;

    fn save_nodes_batch(&self, nodes: &[NodeRef]) -> usize {
        for n in nodes {
            self.save_node(n);
        }
        nodes.len()
    }
    fn save_edges_batch(&self, edges: &[EdgeRef]) -> usize {
        for e in edges {
            self.save_edge(e);
        }
        edges.len()
    }
    fn clear_session(&self, session_key: &str) -> usize;
    fn get_session_statistics(&self, session_key: &str) -> Value;

    // ------------------------------------------------------------------------------------------------
    // Query helpers. In Python only the in-memory store defined these (callers duck-typed them); the
    // defaults below are the in-memory algorithms expressed over the core methods.
    // ------------------------------------------------------------------------------------------------

    /// `(neighbour, connecting edge)` pairs; outgoing first, then incoming.
    fn get_neighbors(
        &self,
        node_id: &str,
        edge_types: Option<&[EdgeType]>,
        direction: Direction,
    ) -> Vec<(NodeRef, EdgeRef)> {
        let mut out = Vec::new();
        if matches!(direction, Direction::Outgoing | Direction::Both) {
            for e in self.get_edges_from(node_id, edge_types) {
                let target = e.borrow().target_id.clone();
                if let Some(n) = self.get_node(&target) {
                    out.push((n, e));
                }
            }
        }
        if matches!(direction, Direction::Incoming | Direction::Both) {
            for e in self.get_edges_to(node_id, edge_types) {
                let source = e.borrow().source_id.clone();
                if let Some(n) = self.get_node(&source) {
                    out.push((n, e));
                }
            }
        }
        out
    }

    fn get_children(&self, node_id: &str) -> Vec<NodeRef> {
        let Some(node) = self.get_node(node_id) else { return Vec::new() };
        let ids = node.borrow().child_ids.clone();
        ids.iter().filter_map(|c| self.get_node(c)).collect()
    }

    fn get_parent(&self, node_id: &str) -> Option<NodeRef> {
        let node = self.get_node(node_id)?;
        let parent = node.borrow().parent_id.clone()?;
        self.get_node(&parent)
    }

    fn get_edges_batch(&self, pairs: &[(String, String)], edge_type: Option<EdgeType>) -> Vec<Option<EdgeRef>> {
        pairs.iter().map(|(s, t)| self.get_edge_between(s, t, edge_type)).collect()
    }

    fn find_nodes_by_entities(&self, entity_ids: &[String], session_key: &str) -> Vec<NodeRef> {
        self.get_nodes_by_session(session_key, None)
            .into_iter()
            .filter(|n| n.borrow().entity_ids.iter().any(|e| entity_ids.contains(e)))
            .collect()
    }

    /// Nodes with `heat_score >= threshold`, hottest first.
    fn get_hot_nodes(&self, session_key: &str, threshold: f64, limit: usize) -> Vec<NodeRef> {
        let mut hot: Vec<NodeRef> = self
            .get_nodes_by_session(session_key, None)
            .into_iter()
            .filter(|n| n.borrow().heat_score >= threshold)
            .collect();
        hot.sort_by(|a, b| {
            b.borrow().heat_score.partial_cmp(&a.borrow().heat_score).unwrap_or(std::cmp::Ordering::Equal)
        });
        hot.truncate(limit);
        hot
    }

    /// Nodes with `heat_score <= threshold` and at least `min_age_days` old, coldest first.
    fn get_cold_nodes(&self, session_key: &str, threshold: f64, min_age_days: f64, limit: usize) -> Vec<NodeRef> {
        let mut cold: Vec<NodeRef> = self
            .get_nodes_by_session(session_key, None)
            .into_iter()
            .filter(|n| {
                let n = n.borrow();
                n.heat_score <= threshold && n.days_since_creation() >= min_age_days
            })
            .collect();
        cold.sort_by(|a, b| {
            a.borrow().heat_score.partial_cmp(&b.borrow().heat_score).unwrap_or(std::cmp::Ordering::Equal)
        });
        cold.truncate(limit);
        cold
    }

    /// `save_node_with_dedup`: `(saved, duplicate_id)`. Stores without deduplication just save.
    fn save_node_with_dedup(&self, node: &NodeRef, _force: bool) -> Result<(bool, Option<String>), String> {
        self.save_node(node);
        Ok((true, None))
    }
}

#[derive(Default)]
struct Inner {
    nodes: IndexMap<String, NodeRef>,
    edges: IndexMap<String, EdgeRef>,
    nodes_by_session: IndexMap<String, IndexSet<String>>,
    edges_from: IndexMap<String, IndexSet<String>>,
    edges_to: IndexMap<String, IndexSet<String>>,
    edges_by_session: IndexMap<String, IndexSet<String>>,
    nodes_by_entity: IndexMap<String, IndexSet<String>>,
    nodes_by_layer: IndexMap<String, IndexMap<NodeLayer, IndexSet<String>>>,
    lsh_indices: IndexMap<String, RandomProjectionLsh>,
    lsh_queries: usize,
    lsh_fallbacks: usize,
    dedup_count: usize,
}

/// In-memory store with per-session LSH indexes and ingest-time deduplication.
pub struct InMemoryNeuralGraphStorage {
    inner: RefCell<Inner>,
    lsh_config: LshConfig,
    enable_dedup: bool,
}

impl Default for InMemoryNeuralGraphStorage {
    fn default() -> Self {
        Self::new(None, true)
    }
}

impl InMemoryNeuralGraphStorage {
    /// Sessions below this size are searched exhaustively.
    pub const LSH_MIN_NODES: usize = 100;
    /// LSH returns `limit * this` candidates before exact refinement.
    pub const LSH_CANDIDATE_MULTIPLIER: usize = 3;
    pub const DEDUP_ENABLED: bool = true;
    pub const DEDUP_SIMILARITY_THRESHOLD: f64 = 0.92;
    pub const DEDUP_MAX_CANDIDATES: usize = 10;

    pub fn new(lsh_config: Option<LshConfig>, enable_dedup: bool) -> Self {
        InMemoryNeuralGraphStorage {
            inner: RefCell::new(Inner::default()),
            lsh_config: lsh_config.unwrap_or(LshConfig { num_tables: 8, num_hashes: 12, ..LshConfig::default() }),
            enable_dedup: enable_dedup && Self::DEDUP_ENABLED,
        }
    }

    fn save_node_internal(&self, inner: &mut Inner, node: &NodeRef) {
        let (node_id, session_key, layer, entity_ids, embedding) = {
            let n = node.borrow();
            (n.node_id.clone(), n.session_key.clone(), n.layer, n.entity_ids.clone(), n.embedding.clone())
        };
        if let Some(old) = inner.nodes.get(&node_id).cloned() {
            let (old_entities, old_session, old_layer, old_has_embedding) = {
                let o = old.borrow();
                (
                    o.entity_ids.clone(),
                    o.session_key.clone(),
                    o.layer,
                    o.embedding.as_ref().is_some_and(|e| !e.is_empty()),
                )
            };
            for e in &old_entities {
                inner.nodes_by_entity.entry(e.clone()).or_default().shift_remove(&node_id);
            }
            inner.nodes_by_layer.entry(old_session).or_default().entry(old_layer).or_default().shift_remove(&node_id);
            if old_has_embedding {
                if let Some(lsh) = inner.lsh_indices.get_mut(&session_key) {
                    lsh.remove(&node_id);
                }
            }
        }
        inner.nodes.insert(node_id.clone(), node.clone());
        inner.nodes_by_session.entry(session_key.clone()).or_default().insert(node_id.clone());
        for e in &entity_ids {
            inner.nodes_by_entity.entry(e.clone()).or_default().insert(node_id.clone());
        }
        inner.nodes_by_layer.entry(session_key.clone()).or_default().entry(layer).or_default().insert(node_id.clone());
        if let Some(emb) = embedding.filter(|e| !e.is_empty()) {
            let config = self.lsh_config.clone();
            inner
                .lsh_indices
                .entry(session_key)
                .or_insert_with(|| RandomProjectionLsh::new(config))
                .index(&node_id, &emb);
        }
    }

    /// The Python called `lsh.query(embedding, limit=...)`, a keyword `query()` does not accept, so every
    /// deduplication against a session that already had an LSH index raised `TypeError`. Nothing called it; the
    /// error is kept rather than inventing the behaviour the author probably meant.
    fn find_duplicate_node(&self, inner: &mut Inner, node: &NodeRef) -> Result<Option<String>, String> {
        let (node_id, session_key, layer, embedding) = {
            let n = node.borrow();
            (n.node_id.clone(), n.session_key.clone(), n.layer, n.embedding.clone())
        };
        let Some(embedding) = embedding.filter(|e| !e.is_empty()) else { return Ok(None) };
        let candidates: Vec<String> = match inner.lsh_indices.get(&session_key) {
            Some(_) => return Err("RandomProjectionLSH.query() got an unexpected keyword argument 'limit'".into()),
            None => inner
                .nodes_by_session
                .get(&session_key)
                .map(|s| s.iter().take(Self::DEDUP_MAX_CANDIDATES).cloned().collect())
                .unwrap_or_default(),
        };
        for cid in candidates {
            if cid == node_id {
                continue;
            }
            let Some(candidate) = inner.nodes.get(&cid) else { continue };
            let c = candidate.borrow();
            let Some(cemb) = c.embedding.as_ref().filter(|e| !e.is_empty()) else { continue };
            if cosine_similarity(&embedding, cemb) >= Self::DEDUP_SIMILARITY_THRESHOLD && c.layer == layer {
                return Ok(Some(cid));
            }
        }
        Ok(None)
    }

    pub fn get_dedup_stats(&self) -> Value {
        json!({
            "dedup_enabled": self.enable_dedup,
            "duplicates_caught": self.inner.borrow().dedup_count,
            "threshold": Self::DEDUP_SIMILARITY_THRESHOLD,
        })
    }

    /// Number of LSH queries and brute-force fallbacks so far.
    pub fn lsh_counters(&self) -> (usize, usize) {
        let i = self.inner.borrow();
        (i.lsh_queries, i.lsh_fallbacks)
    }

    fn remove_edge_from_indexes(inner: &mut Inner, edge_id: &str, source_id: &str, target_id: &str) {
        if let Some(s) = inner.edges_from.get_mut(source_id) {
            s.shift_remove(edge_id);
        }
        if let Some(s) = inner.edges_to.get_mut(target_id) {
            s.shift_remove(edge_id);
        }
    }

    fn save_edge_internal(&self, inner: &mut Inner, edge: &EdgeRef) {
        let (edge_id, source_id, target_id, edge_type) = {
            let e = edge.borrow();
            (e.edge_id.clone(), e.source_id.clone(), e.target_id.clone(), e.edge_type)
        };
        let is_new = !inner.edges.contains_key(&edge_id);
        if let Some(old) = inner.edges.get(&edge_id).cloned() {
            let (os, ot) = {
                let o = old.borrow();
                (o.source_id.clone(), o.target_id.clone())
            };
            Self::remove_edge_from_indexes(inner, &edge_id, &os, &ot);
            if let Some(src) = inner.nodes.get(&os).cloned() {
                let sk = src.borrow().session_key.clone();
                inner.edges_by_session.entry(sk).or_default().shift_remove(&edge_id);
            }
        }
        inner.edges.insert(edge_id.clone(), edge.clone());
        inner.edges_from.entry(source_id.clone()).or_default().insert(edge_id.clone());
        inner.edges_to.entry(target_id).or_default().insert(edge_id.clone());
        let source_node = inner.nodes.get(&source_id).cloned();
        if let Some(src) = &source_node {
            let sk = src.borrow().session_key.clone();
            inner.edges_by_session.entry(sk).or_default().insert(edge_id);
        }
        if is_new {
            if let Some(src) = source_node {
                src.borrow_mut().update_edge_counts(edge_type.value());
            }
        }
    }

    fn brute_force(
        &self,
        query: &[f64],
        session_key: &str,
        limit: usize,
        layer_filter: Option<&[NodeLayer]>,
    ) -> Vec<(NodeRef, f64)> {
        let mut nodes = self.get_nodes_by_session(session_key, None);
        if let Some(f) = layer_filter.filter(|f| !f.is_empty()) {
            nodes.retain(|n| f.contains(&n.borrow().layer));
        }
        let mut scored: Vec<(NodeRef, f64)> = Vec::new();
        for n in nodes {
            let sim = match n.borrow().embedding.as_ref().filter(|e| !e.is_empty()) {
                Some(e) => cosine_similarity(query, e),
                None => continue,
            };
            if sim > 0.0 {
                scored.push((n, sim));
            }
        }
        // heapq.nlargest == sorted(..., reverse=True)[:limit], stable for ties
        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        scored.truncate(limit);
        scored
    }
}

impl NeuralGraphStorage for InMemoryNeuralGraphStorage {
    fn save_node(&self, node: &NodeRef) {
        let mut inner = self.inner.borrow_mut();
        self.save_node_internal(&mut inner, node);
    }

    fn save_node_with_dedup(&self, node: &NodeRef, force: bool) -> Result<(bool, Option<String>), String> {
        let has_embedding = node.borrow().embedding.as_ref().is_some_and(|e| !e.is_empty());
        if !self.enable_dedup || force || !has_embedding {
            self.save_node(node);
            return Ok((true, None));
        }
        let mut inner = self.inner.borrow_mut();
        if let Some(dup) = self.find_duplicate_node(&mut inner, node)? {
            inner.dedup_count += 1;
            return Ok((false, Some(dup)));
        }
        self.save_node_internal(&mut inner, node);
        Ok((true, None))
    }

    fn get_node(&self, node_id: &str) -> Option<NodeRef> {
        self.inner.borrow().nodes.get(node_id).cloned()
    }

    fn delete_node(&self, node_id: &str) -> bool {
        let mut inner = self.inner.borrow_mut();
        let Some(node) = inner.nodes.get(node_id).cloned() else { return false };
        let (session_key, layer, entity_ids) = {
            let n = node.borrow();
            (n.session_key.clone(), n.layer, n.entity_ids.clone())
        };
        let mut to_delete: Vec<String> =
            inner.edges_from.get(node_id).map(|s| s.iter().cloned().collect()).unwrap_or_default();
        to_delete
            .extend(inner.edges_to.get(node_id).map(|s| s.iter().cloned().collect::<Vec<_>>()).unwrap_or_default());
        for eid in to_delete {
            if let Some(edge) = inner.edges.shift_remove(&eid) {
                let (s, t) = {
                    let e = edge.borrow();
                    (e.source_id.clone(), e.target_id.clone())
                };
                Self::remove_edge_from_indexes(&mut inner, &eid, &s, &t);
                inner.edges_by_session.entry(session_key.clone()).or_default().shift_remove(&eid);
            }
        }
        for e in &entity_ids {
            inner.nodes_by_entity.entry(e.clone()).or_default().shift_remove(node_id);
        }
        inner.nodes_by_layer.entry(session_key.clone()).or_default().entry(layer).or_default().shift_remove(node_id);
        if let Some(lsh) = inner.lsh_indices.get_mut(&session_key) {
            lsh.remove(node_id);
        }
        inner.nodes.shift_remove(node_id);
        inner.nodes_by_session.entry(session_key).or_default().shift_remove(node_id);
        true
    }

    fn get_nodes_by_session(&self, session_key: &str, layer: Option<NodeLayer>) -> Vec<NodeRef> {
        let inner = self.inner.borrow();
        let Some(ids) = inner.nodes_by_session.get(session_key) else { return Vec::new() };
        let nodes = ids.iter().filter_map(|id| inner.nodes.get(id).cloned());
        match layer {
            Some(l) => nodes.filter(|n| n.borrow().layer == l).collect(),
            None => nodes.collect(),
        }
    }

    fn get_nodes_by_layer(&self, session_key: &str, layer: NodeLayer) -> Vec<NodeRef> {
        let inner = self.inner.borrow();
        let Some(ids) = inner.nodes_by_layer.get(session_key).and_then(|m| m.get(&layer)) else { return Vec::new() };
        ids.iter().filter_map(|id| inner.nodes.get(id).cloned()).collect()
    }

    fn save_edge(&self, edge: &EdgeRef) {
        let mut inner = self.inner.borrow_mut();
        self.save_edge_internal(&mut inner, edge);
    }

    fn get_edge(&self, edge_id: &str) -> Option<EdgeRef> {
        self.inner.borrow().edges.get(edge_id).cloned()
    }

    fn delete_edge(&self, edge_id: &str) -> bool {
        let mut inner = self.inner.borrow_mut();
        let Some(edge) = inner.edges.get(edge_id).cloned() else { return false };
        let (s, t) = {
            let e = edge.borrow();
            (e.source_id.clone(), e.target_id.clone())
        };
        if let Some(src) = inner.nodes.get(&s).cloned() {
            let sk = src.borrow().session_key.clone();
            inner.edges_by_session.entry(sk).or_default().shift_remove(edge_id);
        }
        inner.edges.shift_remove(edge_id);
        Self::remove_edge_from_indexes(&mut inner, edge_id, &s, &t);
        true
    }

    fn get_edges_from(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> Vec<EdgeRef> {
        let inner = self.inner.borrow();
        let Some(ids) = inner.edges_from.get(node_id) else { return Vec::new() };
        ids.iter()
            .filter_map(|id| inner.edges.get(id).cloned())
            .filter(|e| type_allowed(edge_types, e.borrow().edge_type))
            .collect()
    }

    fn get_edges_to(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> Vec<EdgeRef> {
        let inner = self.inner.borrow();
        let Some(ids) = inner.edges_to.get(node_id) else { return Vec::new() };
        ids.iter()
            .filter_map(|id| inner.edges.get(id).cloned())
            .filter(|e| type_allowed(edge_types, e.borrow().edge_type))
            .collect()
    }

    fn get_edge_between(&self, source_id: &str, target_id: &str, edge_type: Option<EdgeType>) -> Option<EdgeRef> {
        let inner = self.inner.borrow();
        let ids = inner.edges_from.get(source_id)?;
        for id in ids {
            if let Some(e) = inner.edges.get(id) {
                let b = e.borrow();
                if b.target_id == target_id && edge_type.is_none_or(|t| b.edge_type == t) {
                    return Some(e.clone());
                }
            }
        }
        None
    }

    fn get_all_edges(&self, session_key: &str) -> Vec<EdgeRef> {
        let inner = self.inner.borrow();
        let Some(ids) = inner.edges_by_session.get(session_key) else { return Vec::new() };
        ids.iter().filter_map(|id| inner.edges.get(id).cloned()).collect()
    }

    fn vector_search(
        &self,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
        layer_filter: Option<&[NodeLayer]>,
    ) -> Vec<(NodeRef, f64)> {
        let use_lsh = {
            let inner = self.inner.borrow();
            let count = inner.nodes_by_session.get(session_key).map(IndexSet::len).unwrap_or(0);
            count >= Self::LSH_MIN_NODES
                && inner.lsh_indices.contains_key(session_key)
                && layer_filter.is_none_or(|f| f.is_empty())
        };
        if !use_lsh {
            return self.brute_force(query_embedding, session_key, limit, layer_filter);
        }
        let candidates = {
            let mut inner = self.inner.borrow_mut();
            inner.lsh_queries += 1;
            let lsh = inner.lsh_indices.get_mut(session_key).expect("checked above");
            lsh.query_with_probing(query_embedding, limit * Self::LSH_CANDIDATE_MULTIPLIER, 0.0, 3)
        };
        if candidates.len() < limit {
            self.inner.borrow_mut().lsh_fallbacks += 1;
            return self.brute_force(query_embedding, session_key, limit, layer_filter);
        }
        let inner = self.inner.borrow();
        let mut results = Vec::new();
        for (id, sim) in candidates {
            if let Some(n) = inner.nodes.get(&id) {
                if let Some(f) = layer_filter.filter(|f| !f.is_empty()) {
                    if !f.contains(&n.borrow().layer) {
                        continue;
                    }
                }
                results.push((n.clone(), sim));
            }
        }
        results.truncate(limit);
        results
    }

    fn save_nodes_batch(&self, nodes: &[NodeRef]) -> usize {
        let mut inner = self.inner.borrow_mut();
        for n in nodes {
            self.save_node_internal(&mut inner, n);
        }
        nodes.len()
    }

    fn save_edges_batch(&self, edges: &[EdgeRef]) -> usize {
        let mut inner = self.inner.borrow_mut();
        for e in edges {
            self.save_edge_internal(&mut inner, e);
        }
        edges.len()
    }

    fn clear_session(&self, session_key: &str) -> usize {
        let mut inner = self.inner.borrow_mut();
        let node_ids: Vec<String> =
            inner.nodes_by_session.get(session_key).map(|s| s.iter().cloned().collect()).unwrap_or_default();
        let mut count = 0;
        for node_id in &node_ids {
            let node = inner.nodes.get(node_id).cloned();
            let mut to_delete: Vec<String> =
                inner.edges_from.get(node_id).map(|s| s.iter().cloned().collect()).unwrap_or_default();
            to_delete
                .extend(inner.edges_to.get(node_id).map(|s| s.iter().cloned().collect::<Vec<_>>()).unwrap_or_default());
            for eid in to_delete {
                if let Some(edge) = inner.edges.shift_remove(&eid) {
                    let (s, t) = {
                        let e = edge.borrow();
                        (e.source_id.clone(), e.target_id.clone())
                    };
                    Self::remove_edge_from_indexes(&mut inner, &eid, &s, &t);
                    count += 1;
                }
            }
            if let Some(n) = node {
                let entity_ids = n.borrow().entity_ids.clone();
                for e in entity_ids {
                    inner.nodes_by_entity.entry(e).or_default().shift_remove(node_id);
                }
            }
            if inner.nodes.shift_remove(node_id).is_some() {
                count += 1;
            }
        }
        inner.nodes_by_session.entry(session_key.to_string()).or_default().clear();
        inner.edges_by_session.entry(session_key.to_string()).or_default().clear();
        inner.nodes_by_layer.entry(session_key.to_string()).or_default().clear();
        if let Some(mut lsh) = inner.lsh_indices.shift_remove(session_key) {
            lsh.clear();
        }
        count
    }

    fn get_session_statistics(&self, session_key: &str) -> Value {
        let nodes = self.get_nodes_by_session(session_key, None);
        let edges = self.get_all_edges(session_key);
        let mut layer_counts: Map<String, Value> = Map::new();
        let mut state_counts: Map<String, Value> = Map::new();
        let (mut heat, mut importance) = (0.0, 0.0);
        for n in &nodes {
            let n = n.borrow();
            bump(&mut layer_counts, n.layer.name());
            heat += n.heat_score;
            importance += n.importance_score;
        }
        for n in &nodes {
            let state: ConsolidationState = n.borrow().consolidation_state;
            bump(&mut state_counts, state.value());
        }
        let mut edge_counts: Map<String, Value> = Map::new();
        for e in &edges {
            bump(&mut edge_counts, e.borrow().edge_type.value());
        }
        let avg = |total: f64| if nodes.is_empty() { json!(0) } else { json!(total / nodes.len() as f64) };
        let inner = self.inner.borrow();
        let lsh_stats = inner.lsh_indices.get(session_key).map(RandomProjectionLsh::get_stats).unwrap_or(json!({}));
        json!({
            "total_nodes": nodes.len(),
            "total_edges": edges.len(),
            "nodes_by_layer": layer_counts,
            "edges_by_type": edge_counts,
            "average_heat_score": avg(heat),
            "average_importance_score": avg(importance),
            "consolidation_states": state_counts,
            "lsh_stats": lsh_stats,
            "lsh_queries_total": inner.lsh_queries,
            "lsh_fallbacks_total": inner.lsh_fallbacks,
        })
    }

    fn get_edges_batch(&self, pairs: &[(String, String)], edge_type: Option<EdgeType>) -> Vec<Option<EdgeRef>> {
        pairs.iter().map(|(s, t)| self.get_edge_between(s, t, edge_type)).collect()
    }

    fn find_nodes_by_entities(&self, entity_ids: &[String], session_key: &str) -> Vec<NodeRef> {
        let inner = self.inner.borrow();
        let empty = IndexSet::new();
        let session_ids = inner.nodes_by_session.get(session_key).unwrap_or(&empty);
        let mut matching: IndexSet<String> = IndexSet::new();
        for e in entity_ids {
            if let Some(ids) = inner.nodes_by_entity.get(e) {
                for id in ids {
                    if session_ids.contains(id) {
                        matching.insert(id.clone());
                    }
                }
            }
        }
        matching.iter().filter_map(|id| inner.nodes.get(id).cloned()).collect()
    }
}

fn bump(m: &mut Map<String, Value>, key: &str) {
    let n = m.get(key).and_then(Value::as_i64).unwrap_or(0);
    m.insert(key.to_string(), json!(n + 1));
}

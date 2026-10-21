//! Memory consolidation: pruning, merging, summary hubs and the heat lifecycle (port of
//! `retrieval/consolidation.py`).
//!
//! A cycle recovers nodes stuck in CONSOLIDATING, prunes weak and old edges, merges near-duplicate messages,
//! creates topic-layer summary hubs over entity clusters (linking them into the temporal chain when a temporal
//! manager is attached), then decays heat and marks hot nodes for promotion or deletes cold ones, protecting
//! factual nodes. "Now" (`datetime.now(timezone.utc)`) drives every age; the minimum interval between cycles
//! uses the wall clock (`time.time()`) and `total_time_ms` `perf_counter`, neither part of any fixture. The
//! Python methods were `async` only because storage was; these are synchronous.
//!
//! Python behaviour kept:
//! - `_merge_nodes` re-points the removed node's edges at the kept node by mutating the edge and saving it, but
//!   the store looks up the edge's *old* endpoints on the same (already mutated) object, so the edge id stays
//!   indexed under the removed node and `delete_node(remove)` then deletes it: merged nodes lose their edges.
//! - `estimate_memory_savings` on an empty session raised `KeyError: 'edges'` (the statistics of an empty
//!   session have no `edges` entry): [`EngineError::KeyError`].
//! - Entity and wave-dimension unions went through `set`s (hash-seed order); here they keep first-seen order.

use crate::pytime::PyDateTime;
use crate::retrieval::data_types::{
    cosine_similarity, edge_ref, generate_edge_id, generate_node_id, node_ref, ConsolidationResult, ConsolidationState,
    EdgeType, NeuralEdge, NeuralNode, NodeLayer, NodeRef,
};
use crate::retrieval::hierarchy::{compare_error, py_max_iter, EngineError, HierarchyManager};
use crate::retrieval::storage::NeuralGraphStorage;
use crate::retrieval::temporal::TemporalChainManager;
use chrono::TimeDelta;
use indexmap::{IndexMap, IndexSet};
use serde_json::{json, Map, Value};
use std::cell::Cell;
use std::rc::Rc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

/// Configuration for memory consolidation.
#[derive(Clone, Debug, PartialEq)]
pub struct ConsolidationConfig {
    pub prune_weight_threshold: f64,
    pub prune_age_threshold_days: f64,
    pub prune_batch_size: usize,
    pub merge_similarity_threshold: f64,
    pub merge_max_per_cycle: usize,
    pub stuck_state_timeout_minutes: f64,
    pub hub_cluster_size_threshold: usize,
    pub hub_density_threshold: f64,
    pub hub_max_per_cycle: usize,
    pub promotion_heat_threshold: f64,
    pub eviction_heat_threshold: f64,
    pub eviction_age_threshold_days: f64,
    pub eviction_recency_threshold_days: f64,
    pub heat_decay_rate: f64,
    pub inactive_decay_multiplier: f64,
    pub inactive_threshold_days: f64,
    pub message_decay_rate: f64,
    pub episode_decay_rate: f64,
    pub topic_decay_rate: f64,
    pub persona_decay_rate: f64,
    pub heat_boost_base: f64,
    pub heat_boost_recency_bonus: f64,
    pub max_heat_score: f64,
    pub message_to_episode_threshold: f64,
    pub episode_to_topic_threshold: f64,
    pub topic_to_persona_threshold: f64,
    pub min_activations_for_promotion: i64,
    pub min_cycle_interval_seconds: f64,
    pub recency_aware_retention: bool,
    pub factual_node_protection: bool,
    pub min_corroborating_edges: usize,
    pub factual_heat_boost: f64,
    pub recency_window_days: f64,
    pub entity_node_protection: bool,
    pub speaker_fact_retention: bool,
}

impl Default for ConsolidationConfig {
    fn default() -> Self {
        ConsolidationConfig {
            prune_weight_threshold: 0.1,
            prune_age_threshold_days: 14.0,
            prune_batch_size: 100,
            merge_similarity_threshold: 0.72,
            merge_max_per_cycle: 20,
            stuck_state_timeout_minutes: 5.0,
            hub_cluster_size_threshold: 5,
            hub_density_threshold: 0.6,
            hub_max_per_cycle: 5,
            promotion_heat_threshold: 2.5,
            eviction_heat_threshold: 0.25,
            eviction_age_threshold_days: 7.0,
            eviction_recency_threshold_days: 7.0,
            heat_decay_rate: 0.015,
            inactive_decay_multiplier: 2.0,
            inactive_threshold_days: 3.0,
            message_decay_rate: 0.02,
            episode_decay_rate: 0.015,
            topic_decay_rate: 0.01,
            persona_decay_rate: 0.005,
            heat_boost_base: 0.2,
            heat_boost_recency_bonus: 0.05,
            max_heat_score: 10.0,
            message_to_episode_threshold: 2.5,
            episode_to_topic_threshold: 3.5,
            topic_to_persona_threshold: 4.5,
            min_activations_for_promotion: 3,
            min_cycle_interval_seconds: 60.0,
            recency_aware_retention: true,
            factual_node_protection: true,
            min_corroborating_edges: 1,
            factual_heat_boost: 0.15,
            recency_window_days: 14.0,
            entity_node_protection: true,
            speaker_fact_retention: true,
        }
    }
}

/// Phrases marking a factual statement (`factual_patterns`).
const FACTUAL_PATTERNS: [&str; 15] = [
    " is ",
    " are ",
    " was ",
    " were ",
    " has ",
    " have ",
    " had ",
    " works ",
    " works as ",
    " work ",
    " lives ",
    " lived ",
    " born ",
    " moved ",
    " started ",
];

/// `timedelta(minutes=m)` rounded to microseconds.
fn timedelta_minutes(minutes: f64) -> TimeDelta {
    TimeDelta::microseconds((minutes * 60_000_000.0).round_ties_even() as i64)
}

/// Sequential `sum(xs) / len(xs)`.
fn mean(xs: &[f64]) -> f64 {
    let mut s = 0.0;
    for x in xs {
        s += x;
    }
    s / xs.len() as f64
}

/// The memory consolidation system.
pub struct ConsolidationManager {
    storage: Rc<dyn NeuralGraphStorage>,
    hierarchy: Option<Rc<HierarchyManager>>,
    temporal: Option<Rc<TemporalChainManager>>,
    config: ConsolidationConfig,
    /// `time.time()` of the last cycle that ran (0.0 before the first).
    last_cycle_time: Cell<f64>,
}

impl ConsolidationManager {
    pub fn new(
        storage: Rc<dyn NeuralGraphStorage>,
        hierarchy_manager: Option<Rc<HierarchyManager>>,
        temporal_manager: Option<Rc<TemporalChainManager>>,
        config: Option<ConsolidationConfig>,
    ) -> Self {
        ConsolidationManager {
            storage,
            hierarchy: hierarchy_manager,
            temporal: temporal_manager,
            config: config.unwrap_or_default(),
            last_cycle_time: Cell::new(0.0),
        }
    }

    pub fn config(&self) -> &ConsolidationConfig {
        &self.config
    }

    /// The hierarchy manager given at construction (kept for promotions; the Python never used it).
    pub fn hierarchy(&self) -> Option<&Rc<HierarchyManager>> {
        self.hierarchy.as_ref()
    }

    pub fn temporal(&self) -> Option<&Rc<TemporalChainManager>> {
        self.temporal.as_ref()
    }

    /// Run all phases; within `min_cycle_interval_seconds` of the previous cycle nothing runs and every count
    /// (and `total_time_ms`) is zero.
    pub fn run_consolidation_cycle(&self, session_key: &str) -> Result<ConsolidationResult, EngineError> {
        let start = Instant::now();
        let current_time = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
        if current_time - self.last_cycle_time.get() < self.config.min_cycle_interval_seconds {
            return Ok(ConsolidationResult { session_key: session_key.to_string(), ..Default::default() });
        }
        self.last_cycle_time.set(current_time);

        let recovered = self.recover_orphaned_nodes(session_key)?;
        if recovered > 0 {
            tracing::info!("Recovered {recovered} orphaned nodes from intermediate states");
        }
        let edges_pruned = self.prune_weak_edges(session_key);
        let nodes_merged = self.merge_similar_nodes(session_key);
        let hubs_created = self.create_summary_hubs(session_key)?;
        let (promoted, evicted) = self.process_heat_lifecycle(session_key);
        let result = ConsolidationResult {
            session_key: session_key.to_string(),
            edges_pruned: edges_pruned as i64,
            nodes_merged: nodes_merged as i64,
            hubs_created: hubs_created as i64,
            nodes_promoted: promoted as i64,
            nodes_evicted: evicted as i64,
            total_time_ms: start.elapsed().as_secs_f64() * 1000.0,
        };
        tracing::info!(
            "Consolidation cycle for {session_key}: pruned={edges_pruned}, merged={nodes_merged}, hubs={hubs_created}, promoted={promoted}, evicted={evicted}"
        );
        Ok(result)
    }

    /// Phase 0: CONSOLIDATING nodes not updated for `stuck_state_timeout_minutes` go back to ACTIVE, or to
    /// EVICTED below the eviction heat. A naive `updated_at` is Python's `TypeError`.
    pub fn recover_orphaned_nodes(&self, session_key: &str) -> Result<usize, EngineError> {
        let timeout = timedelta_minutes(self.config.stuck_state_timeout_minutes);
        let mut recovered = 0;
        for node in self.storage.get_all_nodes(session_key) {
            if node.borrow().consolidation_state != ConsolidationState::Consolidating {
                continue;
            }
            let age = PyDateTime::now_utc().sub(&node.borrow().updated_at)?;
            if age > timeout {
                {
                    let mut n = node.borrow_mut();
                    n.consolidation_state = if n.heat_score < self.config.eviction_heat_threshold {
                        ConsolidationState::Evicted
                    } else {
                        ConsolidationState::Active
                    };
                }
                self.storage.save_node(&node);
                recovered += 1;
            }
        }
        Ok(recovered)
    }

    /// Phase 1: delete edges that are both weak (`|effective_weight|` below the threshold) and stale (not
    /// activated for `prune_age_threshold_days`), at most `prune_batch_size` per cycle.
    pub fn prune_weak_edges(&self, session_key: &str) -> usize {
        let mut pruned = 0;
        for edge in self.storage.get_all_edges(session_key) {
            if pruned >= self.config.prune_batch_size {
                break;
            }
            let (age_days, effective_weight, edge_id) = {
                let mut e = edge.borrow_mut();
                (e.days_since_activation(), e.effective_weight().abs(), e.edge_id.clone())
            };
            if effective_weight < self.config.prune_weight_threshold && age_days > self.config.prune_age_threshold_days
            {
                self.storage.delete_edge(&edge_id);
                pruned += 1;
            }
        }
        pruned
    }

    /// Delete every edge of one type below `weight_threshold or prune_weight_threshold`.
    pub fn prune_edges_by_type(&self, session_key: &str, edge_type: EdgeType, weight_threshold: Option<f64>) -> usize {
        let threshold = match weight_threshold {
            Some(t) if t != 0.0 => t,
            _ => self.config.prune_weight_threshold,
        };
        let mut pruned = 0;
        for edge in self.storage.get_all_edges(session_key) {
            if edge.borrow().edge_type != edge_type {
                continue;
            }
            let (w, id) = {
                let mut e = edge.borrow_mut();
                (e.effective_weight().abs(), e.edge_id.clone())
            };
            if w < threshold {
                self.storage.delete_edge(&id);
                pruned += 1;
            }
        }
        pruned
    }

    /// Phase 2: merge ACTIVE message nodes (with an embedding) whose cosine similarity reaches
    /// `merge_similarity_threshold` into the earlier one, at most `merge_max_per_cycle` keep-nodes per cycle.
    pub fn merge_similar_nodes(&self, session_key: &str) -> usize {
        let mergeable: Vec<NodeRef> = self
            .storage
            .get_nodes_by_layer(session_key, NodeLayer::Message)
            .into_iter()
            .filter(|n| {
                let n = n.borrow();
                n.embedding.is_some() && n.consolidation_state == ConsolidationState::Active
            })
            .collect();
        let mut merged = 0;
        let mut merged_ids: IndexSet<String> = IndexSet::new();
        for (i, node1) in mergeable.iter().enumerate() {
            if merged >= self.config.merge_max_per_cycle {
                break;
            }
            if merged_ids.contains(&node1.borrow().node_id) {
                continue;
            }
            for node2 in &mergeable[i + 1..] {
                let id2 = node2.borrow().node_id.clone();
                if merged_ids.contains(&id2) {
                    continue;
                }
                let sim = {
                    let (a, b) = (node1.borrow(), node2.borrow());
                    cosine_similarity(a.embedding.as_deref().unwrap_or(&[]), b.embedding.as_deref().unwrap_or(&[]))
                };
                if sim >= self.config.merge_similarity_threshold {
                    self.merge_nodes(node1, node2);
                    merged_ids.insert(id2);
                    merged += 1;
                }
            }
        }
        merged
    }

    /// Merge `remove` into `keep`: edges re-pointed (see the module docs for what the store makes of that),
    /// max heat and importance, entity and source-id unions, the longer content; then `remove` is deleted.
    pub fn merge_nodes(&self, keep: &NodeRef, remove: &NodeRef) {
        let keep_id = keep.borrow().node_id.clone();
        let remove_id = remove.borrow().node_id.clone();
        let edges_from = self.storage.get_edges_from(&remove_id, None);
        let edges_to = self.storage.get_edges_to(&remove_id, None);
        for edge in edges_from {
            if edge.borrow().target_id != keep_id {
                edge.borrow_mut().source_id = keep_id.clone();
                self.storage.save_edge(&edge);
            }
        }
        for edge in edges_to {
            if edge.borrow().source_id != keep_id {
                edge.borrow_mut().target_id = keep_id.clone();
                self.storage.save_edge(&edge);
            }
        }
        {
            let r = remove.borrow();
            let mut k = keep.borrow_mut();
            k.heat_score = if r.heat_score > k.heat_score { r.heat_score } else { k.heat_score };
            k.importance_score =
                if r.importance_score > k.importance_score { r.importance_score } else { k.importance_score };
            let entities: IndexSet<String> = k.entity_ids.iter().chain(r.entity_ids.iter()).cloned().collect();
            k.entity_ids = entities.into_iter().collect();
            let sources: IndexSet<String> =
                k.source_memory_ids.iter().chain(r.source_memory_ids.iter()).cloned().collect();
            k.source_memory_ids = sources.into_iter().collect();
            if r.content.chars().count() > k.content.chars().count() {
                k.content = r.content.clone();
            }
            k.updated_at = PyDateTime::now_utc();
        }
        self.storage.save_node(keep);
        self.storage.delete_node(&remove_id);
    }

    /// Phase 3: a summary hub per dense cluster of at least `hub_cluster_size_threshold` nodes, at most
    /// `hub_max_per_cycle`.
    pub fn create_summary_hubs(&self, session_key: &str) -> Result<usize, EngineError> {
        let mut hubs_created = 0;
        for cluster in self.find_dense_clusters(session_key) {
            if hubs_created >= self.config.hub_max_per_cycle {
                break;
            }
            if cluster.len() < self.config.hub_cluster_size_threshold {
                continue;
            }
            if self.create_hub_for_cluster(&cluster, session_key)?.is_some() {
                hubs_created += 1;
            }
        }
        Ok(hubs_created)
    }

    /// Message nodes grouped by shared entity (first-seen entity order); a group becomes a cluster when at
    /// least `hub_cluster_size_threshold` of its nodes are not yet in an earlier cluster.
    pub fn find_dense_clusters(&self, session_key: &str) -> Vec<Vec<NodeRef>> {
        let nodes_with_entities: Vec<NodeRef> = self
            .storage
            .get_nodes_by_layer(session_key, NodeLayer::Message)
            .into_iter()
            .filter(|n| !n.borrow().entity_ids.is_empty())
            .collect();
        if nodes_with_entities.len() < self.config.hub_cluster_size_threshold {
            return Vec::new();
        }
        let mut entity_to_nodes: IndexMap<String, Vec<NodeRef>> = IndexMap::new();
        for node in &nodes_with_entities {
            let entity_ids = node.borrow().entity_ids.clone();
            for entity_id in entity_ids {
                entity_to_nodes.entry(entity_id).or_default().push(node.clone());
            }
        }
        let mut clusters = Vec::new();
        let mut used_nodes: IndexSet<String> = IndexSet::new();
        for sharing_nodes in entity_to_nodes.values() {
            if sharing_nodes.len() < self.config.hub_cluster_size_threshold {
                continue;
            }
            let unused: Vec<NodeRef> =
                sharing_nodes.iter().filter(|n| !used_nodes.contains(&n.borrow().node_id)).cloned().collect();
            if unused.len() >= self.config.hub_cluster_size_threshold {
                for n in &unused {
                    used_nodes.insert(n.borrow().node_id.clone());
                }
                clusters.push(unused);
            }
        }
        clusters
    }

    /// A TOPIC-layer hub over the cluster (centroid embedding, mean waves, entity union, max importance, mean
    /// heat, median creation time) with HIERARCHY edges to its members, which get it as parent. With a
    /// temporal manager the session's TOPIC nodes are then auto-linked (errors only logged, as in Python).
    pub fn create_hub_for_cluster(
        &self,
        cluster: &[NodeRef],
        session_key: &str,
    ) -> Result<Option<NodeRef>, EngineError> {
        if cluster.is_empty() {
            return Ok(None);
        }
        let embeddings: Vec<Vec<f64>> =
            cluster.iter().filter_map(|n| n.borrow().embedding.clone().filter(|e| !e.is_empty())).collect();
        let centroid = if embeddings.is_empty() {
            None
        } else {
            let dim = embeddings[0].len();
            Some(
                (0..dim)
                    .map(|i| {
                        let mut s = 0.0;
                        for e in &embeddings {
                            s += e[i];
                        }
                        s / embeddings.len() as f64
                    })
                    .collect::<Vec<f64>>(),
            )
        };
        let all_amps: Vec<IndexMap<String, f64>> =
            cluster.iter().map(|n| n.borrow().wave_amplitudes.clone()).filter(|a| !a.is_empty()).collect();
        let mut all_keys: IndexSet<String> = IndexSet::new();
        for a in &all_amps {
            all_keys.extend(a.keys().cloned());
        }
        let mut agg_waves = IndexMap::new();
        for key in all_keys {
            let vals: Vec<f64> = all_amps.iter().map(|a| a.get(&key).copied().unwrap_or(0.0)).collect();
            agg_waves.insert(key, mean(&vals));
        }
        let mut all_entities: IndexSet<String> = IndexSet::new();
        for n in cluster {
            all_entities.extend(n.borrow().entity_ids.iter().cloned());
        }
        let entity_list: Vec<&str> = all_entities.iter().take(5).map(String::as_str).collect();
        let summary = format!("Summary hub: {} nodes about {}", cluster.len(), entity_list.join(", "));
        let importance = py_max_iter(cluster.iter().map(|n| n.borrow().importance_score));
        let heats: Vec<f64> = cluster.iter().map(|n| n.borrow().heat_score).collect();

        let mut hub = NeuralNode::new(generate_node_id(), NodeLayer::Topic, summary);
        hub.embedding = centroid;
        hub.wave_amplitudes = agg_waves;
        hub.entity_ids = all_entities.iter().cloned().collect();
        hub.session_key = session_key.to_string();
        hub.importance_score = importance.expect("non-empty cluster");
        hub.heat_score = mean(&heats);
        hub.child_ids = cluster.iter().map(|n| n.borrow().node_id.clone()).collect();
        let mut metadata = Map::new();
        metadata.insert("hub_type".into(), json!("summary"));
        metadata.insert("cluster_size".into(), json!(cluster.len()));
        hub.metadata = metadata;

        // sorted(n.created_at for n in cluster): mixing naive and aware raised TypeError
        let mut cluster_times: Vec<PyDateTime> = cluster.iter().map(|n| n.borrow().created_at).collect();
        if cluster_times.len() > 1 && cluster_times.iter().any(|t| t.is_aware() != cluster_times[0].is_aware()) {
            return Err(compare_error());
        }
        cluster_times.sort_by(|a, b| a.py_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        hub.created_at = cluster_times[cluster_times.len() / 2];
        let hub_id = hub.node_id.clone();
        let hub = node_ref(hub);
        self.storage.save_node(&hub);

        for node in cluster {
            let node_id = node.borrow().node_id.clone();
            let edge = NeuralEdge::new(generate_edge_id(), hub_id.clone(), node_id, EdgeType::Hierarchy);
            self.storage.save_edge(&edge_ref(edge));
            node.borrow_mut().parent_id = Some(hub_id.clone());
            self.storage.save_node(node);
        }
        if let Some(temporal) = &self.temporal {
            if let Err(e) = temporal.auto_link_temporal(session_key, NodeLayer::Topic) {
                tracing::warn!("Failed to link hub {} to temporal chain: {e}", &hub_id[..8.min(hub_id.len())]);
            }
        }
        Ok(Some(hub))
    }

    /// Phase 4: layer-specific heat decay since last activation (doubled when inactive), then promotion
    /// (marked CONSOLIDATING) or eviction (deleted with its edges, unless protected as factual). Returns
    /// `(promoted, evicted)`.
    pub fn process_heat_lifecycle(&self, session_key: &str) -> (usize, usize) {
        let cfg = &self.config;
        let mut promoted = 0;
        let mut evicted = 0;
        for node in self.storage.get_all_nodes(session_key) {
            let (layer, days_since_activation) = {
                let n = node.borrow();
                (n.layer, n.days_since_activation())
            };
            let base_decay_rate = match layer {
                NodeLayer::Message => cfg.message_decay_rate,
                NodeLayer::Episode => cfg.episode_decay_rate,
                NodeLayer::Topic => cfg.topic_decay_rate,
                _ => cfg.persona_decay_rate,
            };
            let decay_rate = if days_since_activation > cfg.inactive_threshold_days {
                base_decay_rate * cfg.inactive_decay_multiplier
            } else {
                base_decay_rate
            };
            let promotion_threshold = match layer {
                NodeLayer::Message => cfg.message_to_episode_threshold,
                NodeLayer::Episode => cfg.episode_to_topic_threshold,
                NodeLayer::Topic => cfg.topic_to_persona_threshold,
                _ => f64::INFINITY,
            };
            let (promote, evict) = {
                let mut n = node.borrow_mut();
                let decay_factor = (-decay_rate * n.days_since_activation()).exp();
                n.heat_score *= decay_factor;
                let active = n.consolidation_state == ConsolidationState::Active;
                let promote = n.heat_score >= promotion_threshold
                    && n.activation_count >= cfg.min_activations_for_promotion
                    && active;
                let evict = !promote
                    && n.heat_score <= cfg.eviction_heat_threshold
                    && n.days_since_creation() >= cfg.eviction_age_threshold_days
                    && n.days_since_activation() >= cfg.eviction_recency_threshold_days
                    && active;
                if promote {
                    n.consolidation_state = ConsolidationState::Consolidating;
                }
                (promote, evict)
            };
            if promote {
                promoted += 1;
            } else if evict {
                if cfg.recency_aware_retention && self.should_protect_factual_node(&node) {
                    node.borrow_mut().heat_score += cfg.factual_heat_boost;
                    self.storage.save_node(&node);
                    continue;
                }
                let (node_id, parent_id) = {
                    let n = node.borrow();
                    (n.node_id.clone(), n.parent_id.clone())
                };
                if let Some(parent_id) = parent_id.filter(|p| !p.is_empty()) {
                    if let Some(parent) = self.storage.get_node(&parent_id) {
                        let pos = parent.borrow().child_ids.iter().position(|c| *c == node_id);
                        if let Some(pos) = pos {
                            parent.borrow_mut().child_ids.remove(pos);
                            self.storage.save_node(&parent);
                        }
                    }
                }
                for edge in self.storage.get_edges_from(&node_id, None) {
                    let id = edge.borrow().edge_id.clone();
                    self.storage.delete_edge(&id);
                }
                for edge in self.storage.get_edges_to(&node_id, None) {
                    let id = edge.borrow().edge_id.clone();
                    self.storage.delete_edge(&id);
                }
                self.storage.delete_node(&node_id);
                evicted += 1;
                continue;
            }
            self.storage.save_node(&node);
        }
        (promoted, evicted)
    }

    /// Add heat (capped at 10.0); 0.0 for an unknown node (Python default amount 0.5).
    pub fn boost_heat(&self, node_id: &str, amount: f64) -> f64 {
        let Some(node) = self.storage.get_node(node_id) else { return 0.0 };
        let heat = {
            let mut n = node.borrow_mut();
            n.heat_score = f64::min(10.0, n.heat_score + amount);
            n.heat_score
        };
        self.storage.save_node(&node);
        heat
    }

    /// Decay every node with positive heat by `exp(-rate * days_since_activation)` (layer rate unless
    /// overridden, doubled when inactive). Returns the count decayed.
    pub fn decay_all_heat(&self, session_key: &str, decay_rate: Option<f64>) -> usize {
        let cfg = &self.config;
        let mut decayed = 0;
        for node in self.storage.get_all_nodes(session_key) {
            {
                let mut n = node.borrow_mut();
                // `if node.heat_score > 0` (NaN heat is skipped too)
                if n.heat_score.partial_cmp(&0.0) != Some(std::cmp::Ordering::Greater) {
                    continue;
                }
                let mut rate = match (decay_rate, n.layer) {
                    (Some(r), _) => r,
                    (None, NodeLayer::Message) => cfg.message_decay_rate,
                    (None, NodeLayer::Episode) => cfg.episode_decay_rate,
                    (None, NodeLayer::Topic) => cfg.topic_decay_rate,
                    (None, _) => cfg.persona_decay_rate,
                };
                if n.days_since_activation() > cfg.inactive_threshold_days {
                    rate *= cfg.inactive_decay_multiplier;
                }
                let decay_factor = (-rate * n.days_since_activation()).exp();
                n.heat_score *= decay_factor;
            }
            self.storage.save_node(&node);
            decayed += 1;
        }
        decayed
    }

    /// Heat, state, edge-weight and age statistics (`{"node_count": 0, "edge_count": 0}` for an empty session).
    pub fn get_consolidation_statistics(&self, session_key: &str) -> Value {
        let cfg = &self.config;
        let nodes = self.storage.get_all_nodes(session_key);
        let edges = self.storage.get_all_edges(session_key);
        if nodes.is_empty() {
            return json!({"node_count": 0, "edge_count": 0});
        }
        let heat_scores: Vec<f64> = nodes.iter().map(|n| n.borrow().heat_score).collect();
        let avg_heat = mean(&heat_scores);
        let min_heat = heat_scores[1..].iter().fold(heat_scores[0], |m, h| if *h < m { *h } else { m });
        let max_heat = heat_scores[1..].iter().fold(heat_scores[0], |m, h| if *h > m { *h } else { m });
        let mut state_counts = Map::new();
        for state in ConsolidationState::ALL {
            let c = nodes.iter().filter(|n| n.borrow().consolidation_state == state).count();
            state_counts.insert(state.value().to_string(), json!(c));
        }
        let weights: Vec<f64> = edges.iter().map(|e| e.borrow_mut().effective_weight().abs()).collect();
        let avg_weight = if weights.is_empty() { json!(0) } else { json!(mean(&weights)) };
        let weak_edges = weights.iter().filter(|w| **w < cfg.prune_weight_threshold).count();
        let ages: Vec<f64> = nodes.iter().map(|n| n.borrow().days_since_creation()).collect();
        let avg_age = mean(&ages);
        let old_nodes =
            nodes.iter().filter(|n| n.borrow().days_since_creation() > cfg.eviction_age_threshold_days).count();
        let ready_for_promotion = heat_scores.iter().filter(|h| **h >= cfg.promotion_heat_threshold).count();
        let ready_for_eviction = nodes
            .iter()
            .filter(|n| {
                let n = n.borrow();
                n.heat_score <= cfg.eviction_heat_threshold
                    && n.days_since_creation() >= cfg.eviction_age_threshold_days
            })
            .count();
        json!({
            "node_count": nodes.len(),
            "edge_count": edges.len(),
            "heat": {
                "average": avg_heat,
                "min": min_heat,
                "max": max_heat,
                "ready_for_promotion": ready_for_promotion,
                "ready_for_eviction": ready_for_eviction,
            },
            "consolidation_states": state_counts,
            "edges": {
                "average_weight": avg_weight,
                "weak_edges": weak_edges,
                "weak_edge_ratio": if edges.is_empty() { json!(0) } else { json!(weak_edges as f64 / edges.len() as f64) },
            },
            "age": {
                "average_days": avg_age,
                "old_nodes": old_nodes,
            },
        })
    }

    /// Rough savings from pruning the weak edges and evicting the cold nodes (200 B per edge, 1000 B per node).
    pub fn estimate_memory_savings(&self, session_key: &str) -> Result<Value, EngineError> {
        let stats = self.get_consolidation_statistics(session_key);
        let edges = stats.get("edges").ok_or_else(|| EngineError::KeyError("'edges'".into()))?;
        let potential_edge_pruning = edges["weak_edges"].as_i64().unwrap_or(0);
        let potential_evictions = stats["heat"]["ready_for_eviction"].as_i64().unwrap_or(0);
        let edge_count = stats["edge_count"].as_i64().unwrap_or(0);
        let node_count = stats["node_count"].as_i64().unwrap_or(0);
        Ok(json!({
            "edges_to_prune": potential_edge_pruning,
            "nodes_to_evict": potential_evictions,
            "estimated_savings_bytes": potential_edge_pruning * 200 + potential_evictions * 1000,
            "edge_reduction_ratio": if edge_count > 0 { json!(potential_edge_pruning as f64 / edge_count as f64) } else { json!(0) },
            "node_reduction_ratio": if node_count > 0 { json!(potential_evictions as f64 / node_count as f64) } else { json!(0) },
        }))
    }

    /// Whether a node is protected from eviction: recent, mentions entities, speaker-attributed, phrased as
    /// a fact, or without the minimum number of corroborating edges.
    pub fn should_protect_factual_node(&self, node: &NodeRef) -> bool {
        let cfg = &self.config;
        let node_id = {
            let n = node.borrow();
            if n.days_since_creation() < cfg.recency_window_days {
                return true;
            }
            if cfg.entity_node_protection && !n.entity_ids.is_empty() {
                return true;
            }
            if cfg.speaker_fact_retention {
                let speaker = n.speaker_id();
                if !speaker.is_empty() && speaker.to_lowercase() != "unknown" {
                    return true;
                }
            }
            if cfg.factual_node_protection {
                let content = n.content.to_lowercase();
                if FACTUAL_PATTERNS.iter().any(|p| content.contains(p)) {
                    return true;
                }
            }
            n.node_id.clone()
        };
        let total_edges =
            self.storage.get_edges_from(&node_id, None).len() + self.storage.get_edges_to(&node_id, None).len();
        total_edges < cfg.min_corroborating_edges
    }

    /// Boost the heat of protected nodes that are close to the eviction threshold; returns how many.
    pub fn protect_session_facts(&self, session_key: &str) -> usize {
        let mut protected = 0;
        for node in self.storage.get_all_nodes(session_key) {
            if self.should_protect_factual_node(&node) {
                let low = node.borrow().heat_score < self.config.eviction_heat_threshold + 0.2;
                if low {
                    node.borrow_mut().heat_score += self.config.factual_heat_boost;
                    self.storage.save_node(&node);
                    protected += 1;
                }
            }
        }
        tracing::info!("Protected {protected} factual nodes in session {session_key}");
        protected
    }
}

//! Systolic wavefront propagation through the memory graph (port of `retrieval/wavefront.py`).
//!
//! The query wave enters at the seed nodes and sweeps diagonally through TIME x HIERARCHY: temporal, semantic
//! and entity edges move it along time, hierarchy edges up and down the layers. Every reached node is scored by
//! wave interference with the query ([`InterferenceScorer`]); constructive matches are amplified, destructive
//! ones damped. The Python was `async` only because storage was; this is synchronous. Phases are taken relative
//! to `reference_time` (default "now"), so a naive `created_at` raises the `TypeError` Python raised
//! ([`NaiveAwareMismatch`]).

use crate::pytime::{NaiveAwareMismatch, PyDateTime};
use crate::retrieval::data_types::{EdgeType, NodeRef};
use crate::retrieval::interference::{InterferenceScorer, InterferenceType};
use crate::retrieval::storage::NeuralGraphStorage;
use indexmap::{IndexMap, IndexSet};
use std::rc::Rc;

/// A node in the propagating wavefront.
#[derive(Clone, Debug)]
pub struct WavefrontNode {
    pub node: NodeRef,
    pub activation: f64,
    pub phase: f64,
    pub interference_type: InterferenceType,
    pub hop_depth: i64,
    /// Node ids from the seed to this node.
    pub path: Vec<String>,
}

/// Result of a propagation.
#[derive(Clone, Debug)]
pub struct WavefrontResult {
    /// Every node that propagated, highest activation first.
    pub nodes: Vec<WavefrontNode>,
    pub total_propagations: i64,
    pub constructive_count: i64,
    pub destructive_count: i64,
    pub partial_count: i64,
    pub max_depth_reached: i64,
}

fn count(itype: InterferenceType, constructive: &mut i64, destructive: &mut i64, partial: &mut i64) {
    match itype {
        InterferenceType::Constructive => *constructive += 1,
        InterferenceType::Destructive => *destructive += 1,
        InterferenceType::Partial => *partial += 1,
    }
}

/// Diagonal wavefront propagation over a storage backend.
#[derive(Clone)]
pub struct WavefrontPropagator {
    storage: Rc<dyn NeuralGraphStorage>,
    temporal_decay: f64,
    hierarchy_decay: f64,
    min_activation: f64,
}

impl WavefrontPropagator {
    /// Python defaults: `temporal_decay = 0.85`, `hierarchy_decay = 0.92`, `min_activation = 0.15`.
    pub fn new(
        storage: Rc<dyn NeuralGraphStorage>,
        temporal_decay: f64,
        hierarchy_decay: f64,
        min_activation: f64,
    ) -> Self {
        WavefrontPropagator { storage, temporal_decay, hierarchy_decay, min_activation }
    }

    /// The propagator with Python's default decays.
    pub fn with_defaults(storage: Rc<dyn NeuralGraphStorage>) -> Self {
        Self::new(storage, 0.85, 0.92, 0.15)
    }

    /// Propagate the query wave from the seeds (Python defaults: 5 time hops, 2 hierarchy hops, reference
    /// time "now" in UTC).
    pub fn propagate(
        &self,
        query_wave: &IndexMap<String, f64>,
        seed_nodes: &[NodeRef],
        max_time_hops: i64,
        max_hierarchy_hops: i64,
        reference_time: Option<PyDateTime>,
    ) -> Result<WavefrontResult, NaiveAwareMismatch> {
        let reference_time = reference_time.unwrap_or_else(PyDateTime::now_utc);
        let mut visited: IndexSet<String> = IndexSet::new();
        let mut wavefront: IndexMap<String, WavefrontNode> = IndexMap::new();
        let mut results: Vec<WavefrontNode> = Vec::new();
        let mut total_propagations = 0;
        let (mut constructive, mut destructive, mut partial) = (0, 0, 0);
        let mut max_depth_reached = 0;

        for node in seed_nodes {
            let node_id = node.borrow().node_id.clone();
            if visited.contains(&node_id) {
                continue;
            }
            let (phase, (activation, itype)) = {
                let n = node.borrow();
                let phase = InterferenceScorer::compute_temporal_phase(&n.created_at, Some(&reference_time))?;
                (phase, InterferenceScorer::score_memory_match(query_wave, &n, Some(&reference_time))?)
            };
            let wf_node = WavefrontNode {
                node: node.clone(),
                activation: f64::min(1.0, activation + 0.5),
                phase,
                interference_type: itype,
                hop_depth: 0,
                path: vec![node_id.clone()],
            };
            wavefront.insert(node_id.clone(), wf_node);
            visited.insert(node_id);
            count(itype, &mut constructive, &mut destructive, &mut partial);
        }

        let max_sweeps = max_time_hops + max_hierarchy_hops;
        for _sweep in 0..max_sweeps.max(0) {
            if wavefront.is_empty() {
                break;
            }
            let mut next_wavefront: IndexMap<String, WavefrontNode> = IndexMap::new();
            for wf_node in wavefront.values() {
                if wf_node.activation < self.min_activation {
                    continue;
                }
                total_propagations += 1;
                max_depth_reached = max_depth_reached.max(wf_node.hop_depth);
                results.push(wf_node.clone());

                let temporal_hops_left = max_time_hops - wf_node.hop_depth;
                let hierarchy_hops_left = max_hierarchy_hops - wf_node.hop_depth.div_euclid(2);
                if temporal_hops_left > 0 || hierarchy_hops_left > 0 {
                    let neighbors = self.propagate_from_node(
                        wf_node,
                        query_wave,
                        &reference_time,
                        &visited,
                        temporal_hops_left > 0,
                        hierarchy_hops_left > 0,
                    )?;
                    for neighbor in neighbors {
                        let nid = neighbor.node.borrow().node_id.clone();
                        if !next_wavefront.contains_key(&nid) {
                            count(neighbor.interference_type, &mut constructive, &mut destructive, &mut partial);
                            visited.insert(nid.clone());
                            next_wavefront.insert(nid, neighbor);
                        }
                    }
                }
            }
            wavefront = next_wavefront;
        }

        // list.sort(reverse=True) is stable: equal activations keep their order
        results.sort_by(|a, b| b.activation.partial_cmp(&a.activation).unwrap_or(std::cmp::Ordering::Equal));
        Ok(WavefrontResult {
            nodes: results,
            total_propagations,
            constructive_count: constructive,
            destructive_count: destructive,
            partial_count: partial,
            max_depth_reached,
        })
    }

    /// Activated neighbours of one wavefront node (at most the first 8 matching edges are followed).
    fn propagate_from_node(
        &self,
        source: &WavefrontNode,
        query_wave: &IndexMap<String, f64>,
        reference_time: &PyDateTime,
        visited: &IndexSet<String>,
        allow_temporal: bool,
        allow_hierarchy: bool,
    ) -> Result<Vec<WavefrontNode>, NaiveAwareMismatch> {
        let mut neighbors = Vec::new();
        let mut edge_types = Vec::new();
        if allow_temporal {
            edge_types.extend([EdgeType::Temporal, EdgeType::Semantic, EdgeType::Entity]);
        }
        if allow_hierarchy {
            edge_types.push(EdgeType::Hierarchy);
        }
        let source_id = source.node.borrow().node_id.clone();
        let mut edges = self.storage.get_edges_from(&source_id, Some(&edge_types));
        edges.truncate(8);

        for edge in edges {
            let (target_id, edge_type) = {
                let e = edge.borrow();
                (e.target_id.clone(), e.edge_type)
            };
            if visited.contains(&target_id) {
                continue;
            }
            let Some(target_node) = self.storage.get_node(&target_id) else { continue };
            let (target_phase, result) = {
                let t = target_node.borrow();
                let phase = InterferenceScorer::compute_temporal_phase(&t.created_at, Some(reference_time))?;
                (phase, InterferenceScorer::compute_interference(query_wave, &t.wave_amplitudes, 0.0, phase))
            };
            let decay = if edge_type == EdgeType::Hierarchy { self.hierarchy_decay } else { self.temporal_decay };
            let edge_weight = edge.borrow_mut().effective_weight();
            let mut new_activation = source.activation * decay * edge_weight.abs() * (result.activation + 0.3);
            match result.interference_type {
                InterferenceType::Constructive => new_activation *= 1.3,
                InterferenceType::Destructive => new_activation *= 0.5,
                InterferenceType::Partial => {}
            }
            if new_activation < self.min_activation * 0.5 {
                continue;
            }
            let mut path = source.path.clone();
            path.push(target_id);
            neighbors.push(WavefrontNode {
                node: target_node,
                activation: f64::min(1.0, new_activation),
                phase: target_phase,
                interference_type: result.interference_type,
                hop_depth: source.hop_depth + 1,
                path,
            });
        }
        Ok(neighbors)
    }
}

/// Retrieval by diagonal sweep: wavefront activation adjusted by interference type and hop depth.
#[derive(Clone)]
pub struct DiagonalSweepRetriever {
    propagator: WavefrontPropagator,
    constructive_boost: f64,
    destructive_penalty: f64,
}

impl DiagonalSweepRetriever {
    /// Python defaults: `constructive_boost = 0.4`, `destructive_penalty = 0.5`.
    pub fn new(propagator: WavefrontPropagator, constructive_boost: f64, destructive_penalty: f64) -> Self {
        DiagonalSweepRetriever { propagator, constructive_boost, destructive_penalty }
    }

    /// Top `limit` `(node, score)` pairs (Python default limit 20), propagating 5 time and 2 hierarchy hops.
    pub fn retrieve(
        &self,
        query_wave: &IndexMap<String, f64>,
        seed_nodes: &[NodeRef],
        limit: usize,
    ) -> Result<Vec<(NodeRef, f64)>, NaiveAwareMismatch> {
        let result = self.propagator.propagate(query_wave, seed_nodes, 5, 2, None)?;
        let mut scored: Vec<(NodeRef, f64)> = Vec::new();
        for wf_node in result.nodes {
            let mut score = wf_node.activation;
            match wf_node.interference_type {
                InterferenceType::Constructive => score += self.constructive_boost,
                InterferenceType::Destructive => score *= self.destructive_penalty,
                InterferenceType::Partial => {}
            }
            let depth_penalty = 1.0 / (1.0 + 0.1 * wf_node.hop_depth as f64);
            score *= depth_penalty;
            scored.push((wf_node.node, score));
        }
        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        scored.truncate(limit);
        Ok(scored)
    }
}

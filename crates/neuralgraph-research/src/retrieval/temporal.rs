//! Temporal and causal chains, summation, Hebbian learning and refractory periods (port of
//! `retrieval/temporal.py`).
//!
//! The manager creates BEFORE edges between consecutive memories (delay and weight from the time gap) and causal
//! edges, follows both kinds of chain (activating the temporal edges it walks: LTP), sums recent incoming
//! signal, records co-activation, applies STDP and LTP decay, tracks refractory periods, boosts candidates whose
//! timestamps match a query window, and decays temporal edges whose direction contradicts the timestamps.
//! Everything reads "now" (`datetime.now(timezone.utc)`), pinned in tests. The Python methods were `async` only
//! because storage was; these are synchronous.
//!
//! Python bug kept: `set_refractory_period` without a (truthy) duration read `self._config.refractory_period_ms`,
//! a field `TemporalConfig` does not have, so it raised `AttributeError` ([`EngineError::AttributeError`]).

use crate::pytime::{delta_days, total_seconds, PyDateTime};
use crate::retrieval::data_types::{
    edge_ref, generate_edge_id, timedelta_ms, EdgeRef, EdgeType, NeuralEdge, NodeLayer, NodeRef,
};
use crate::retrieval::hierarchy::{sort_by_created_at, EngineError};
use crate::retrieval::storage::NeuralGraphStorage;
use chrono::TimeDelta;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_repr_str, py_truthy};
use serde_json::{json, Map, Value};
use std::rc::Rc;

/// `x or default` for an optional float (`None` and `0.0` fall back).
fn or_default(x: Option<f64>, default: f64) -> f64 {
    match x {
        Some(v) if v != 0.0 => v,
        _ => default,
    }
}

/// `timedelta(hours=h)`: whole hours exactly, the fraction rounded to microseconds (half-even), like CPython.
pub fn timedelta_hours(hours: f64) -> TimeDelta {
    let whole = hours.trunc();
    let frac_us = ((hours - whole) * 3_600_000_000.0).round_ties_even();
    TimeDelta::microseconds(whole as i64 * 3_600_000_000 + frac_us as i64)
}

/// Configuration for temporal chain management.
#[derive(Clone, Debug, PartialEq)]
pub struct TemporalConfig {
    pub max_time_gap_hours: f64,
    pub min_propagation_delay_ms: f64,
    pub max_propagation_delay_ms: f64,
    pub summation_window_hours: f64,
    pub summation_threshold: f64,
    pub causal_confidence_threshold: f64,
    pub causal_time_order_required: bool,
    pub ltp_activation_threshold: i64,
    pub ltp_max_boost: f64,
    pub ltp_decay_rate: f64,
    pub temporal_consistency_enabled: bool,
    pub temporal_overlap_boost: f64,
    pub temporal_exact_match_boost: f64,
    pub temporal_proximity_window_days: f64,
    pub temporal_proximity_boost: f64,
    pub conflict_decay_rate: f64,
    pub conflict_detection_enabled: bool,
}

impl Default for TemporalConfig {
    fn default() -> Self {
        TemporalConfig {
            max_time_gap_hours: 24.0,
            min_propagation_delay_ms: 10.0,
            max_propagation_delay_ms: 10000.0,
            summation_window_hours: 24.0,
            summation_threshold: 0.5,
            causal_confidence_threshold: 0.6,
            causal_time_order_required: true,
            ltp_activation_threshold: 2,
            ltp_max_boost: 3.0,
            ltp_decay_rate: 0.01,
            temporal_consistency_enabled: true,
            temporal_overlap_boost: 1.3,
            temporal_exact_match_boost: 1.5,
            temporal_proximity_window_days: 7.0,
            temporal_proximity_boost: 1.15,
            conflict_decay_rate: 0.5,
            conflict_detection_enabled: true,
        }
    }
}

/// `get_temporal_context` result.
#[derive(Clone, Debug, Default)]
pub struct TemporalContext {
    pub before: Vec<NodeRef>,
    pub after: Vec<NodeRef>,
}

/// `str(tuple(sorted(dims)))`, e.g. `('entity', 'temporal')`, `('entity',)`, `()`.
fn py_tuple_repr(dims: &[String]) -> String {
    let mut sorted: Vec<&String> = dims.iter().collect();
    sorted.sort();
    match sorted.len() {
        0 => "()".to_string(),
        1 => format!("({},)", py_repr_str(sorted[0])),
        _ => format!("({})", sorted.iter().map(|d| py_repr_str(d)).collect::<Vec<_>>().join(", ")),
    }
}

/// Manager of temporal edges, causal chains and Hebbian dynamics.
#[derive(Clone)]
pub struct TemporalChainManager {
    storage: Rc<dyn NeuralGraphStorage>,
    config: TemporalConfig,
}

impl TemporalChainManager {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<TemporalConfig>) -> Self {
        TemporalChainManager { storage, config: config.unwrap_or_default() }
    }

    pub fn config(&self) -> &TemporalConfig {
        &self.config
    }

    pub fn storage(&self) -> &Rc<dyn NeuralGraphStorage> {
        &self.storage
    }

    // ---------------------------------------------------------------------------------------------
    // Temporal edges
    // ---------------------------------------------------------------------------------------------

    /// A TEMPORAL edge source -> target: delay 10 ms per second of gap, clamped to the configured range,
    /// weight `exp(-delay_hours / max_time_gap_hours)`. Naive timestamps are taken as UTC.
    pub fn create_temporal_edge(&self, source_node: &NodeRef, target_node: &NodeRef, direction: &str) -> EdgeRef {
        let (source_id, source_time) = {
            let s = source_node.borrow();
            (s.node_id.clone(), s.created_at.with_utc_if_naive())
        };
        let (target_id, target_time) = {
            let t = target_node.borrow();
            (t.node_id.clone(), t.created_at.with_utc_if_naive())
        };
        let time_diff_seconds = total_seconds(target_time.assume_utc() - source_time.assume_utc()).abs();
        let delay_ms = f64::min(
            self.config.max_propagation_delay_ms,
            f64::max(self.config.min_propagation_delay_ms, time_diff_seconds * 10.0),
        );
        let hours_diff = delay_ms / (1000.0 * 3600.0);
        let base_weight = (-hours_diff / self.config.max_time_gap_hours).exp();
        let mut edge = NeuralEdge::new(generate_edge_id(), source_id, target_id, EdgeType::Temporal);
        edge.base_weight = base_weight;
        edge.propagation_delay_ms = delay_ms;
        edge.confidence = 1.0;
        let mut metadata = Map::new();
        metadata.insert("direction".into(), json!(direction));
        metadata.insert("time_diff_seconds".into(), json!(time_diff_seconds));
        edge.metadata = metadata;
        let edge = edge_ref(edge);
        self.storage.save_edge(&edge);
        edge
    }

    /// BEFORE edges between time-consecutive nodes, skipping gaps above `max_time_gap_hours`.
    pub fn build_temporal_chain(&self, nodes: &[NodeRef], _session_key: &str) -> Result<Vec<EdgeRef>, EngineError> {
        if nodes.len() < 2 {
            return Ok(Vec::new());
        }
        let mut sorted_nodes = nodes.to_vec();
        sort_by_created_at(&mut sorted_nodes)?;
        let mut edges = Vec::new();
        for pair in sorted_nodes.windows(2) {
            let (source, target) = (&pair[0], &pair[1]);
            let source_time = source.borrow().created_at.with_utc_if_naive();
            let target_time = target.borrow().created_at.with_utc_if_naive();
            let hours_diff = total_seconds(target_time.assume_utc() - source_time.assume_utc()) / 3600.0;
            if hours_diff > self.config.max_time_gap_hours {
                continue;
            }
            edges.push(self.create_temporal_edge(source, target, "before"));
        }
        tracing::info!("Built temporal chain with {} edges", edges.len());
        Ok(edges)
    }

    /// Chain the layer's nodes that have no temporal edge yet.
    pub fn auto_link_temporal(&self, session_key: &str, layer: NodeLayer) -> Result<Vec<EdgeRef>, EngineError> {
        let nodes = self.storage.get_nodes_by_layer(session_key, layer);
        let temporal = [EdgeType::Temporal];
        let unlinked: Vec<NodeRef> = nodes
            .into_iter()
            .filter(|n| {
                let id = n.borrow().node_id.clone();
                self.storage.get_edges_from(&id, Some(&temporal)).is_empty()
                    && self.storage.get_edges_to(&id, Some(&temporal)).is_empty()
            })
            .collect();
        if unlinked.len() < 2 {
            return Ok(Vec::new());
        }
        self.build_temporal_chain(&unlinked, session_key)
    }

    // ---------------------------------------------------------------------------------------------
    // Causal edges
    // ---------------------------------------------------------------------------------------------

    /// A CAUSAL edge cause -> effect (gate threshold 0.4), or `None` below the confidence threshold or when
    /// the cause is later than the effect (Python defaults: confidence 0.8, type "causes").
    pub fn create_causal_edge(
        &self,
        cause_node: &NodeRef,
        effect_node: &NodeRef,
        confidence: f64,
        causal_type: &str,
    ) -> Option<EdgeRef> {
        if confidence < self.config.causal_confidence_threshold {
            return None;
        }
        let (cause_id, cause_time) = {
            let c = cause_node.borrow();
            (c.node_id.clone(), c.created_at.with_utc_if_naive())
        };
        let (effect_id, effect_time) = {
            let e = effect_node.borrow();
            (e.node_id.clone(), e.created_at.with_utc_if_naive())
        };
        if self.config.causal_time_order_required && cause_time.assume_utc() > effect_time.assume_utc() {
            return None;
        }
        let mut edge = NeuralEdge::new(generate_edge_id(), cause_id, effect_id, EdgeType::Causal);
        edge.base_weight = 1.0;
        edge.confidence = confidence;
        edge.gate_threshold = 0.4;
        let mut metadata = Map::new();
        metadata.insert("causal_type".into(), json!(causal_type));
        edge.metadata = metadata;
        let edge = edge_ref(edge);
        self.storage.save_edge(&edge);
        Some(edge)
    }

    // ---------------------------------------------------------------------------------------------
    // Chain traversal
    // ---------------------------------------------------------------------------------------------

    /// Walk the strongest temporal edge per hop ("backward" = predecessors, anything else successors), at most
    /// `max_hops`, ignoring edges with `|weight| < min_weight`; each edge walked is activated (LTP) and saved.
    /// Python defaults: "backward", 5, 0.1.
    pub fn follow_temporal_chain(
        &self,
        start_node: &NodeRef,
        direction: &str,
        max_hops: usize,
        min_weight: f64,
    ) -> Vec<(NodeRef, EdgeRef)> {
        let mut chain = Vec::new();
        let mut current_id = start_node.borrow().node_id.clone();
        let mut visited: IndexSet<String> = IndexSet::from([current_id.clone()]);
        let temporal = [EdgeType::Temporal];
        for _ in 0..max_hops {
            let edges = if direction == "backward" {
                self.storage.get_edges_to(&current_id, Some(&temporal))
            } else {
                self.storage.get_edges_from(&current_id, Some(&temporal))
            };
            if edges.is_empty() {
                break;
            }
            let mut valid: Vec<(EdgeRef, f64)> = edges
                .into_iter()
                .map(|e| {
                    let w = e.borrow_mut().effective_weight().abs();
                    (e, w)
                })
                .filter(|(_, w)| *w >= min_weight)
                .collect();
            if valid.is_empty() {
                break;
            }
            valid.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            let best_edge = valid.swap_remove(0).0;
            let next_id = {
                let b = best_edge.borrow();
                if direction == "backward" {
                    b.source_id.clone()
                } else {
                    b.target_id.clone()
                }
            };
            if visited.contains(&next_id) {
                break;
            }
            let Some(next_node) = self.storage.get_node(&next_id) else { break };
            chain.push((next_node, best_edge.clone()));
            visited.insert(next_id.clone());
            current_id = next_id;
            best_edge.borrow_mut().activate();
            self.storage.save_edge(&best_edge);
        }
        chain
    }

    /// Walk the most confident causal edge per hop ("causes" = backwards, anything else forwards); Python
    /// defaults: "effects", 3.
    pub fn follow_causal_chain(
        &self,
        start_node: &NodeRef,
        direction: &str,
        max_hops: usize,
    ) -> Vec<(NodeRef, EdgeRef)> {
        let mut chain = Vec::new();
        let mut current_id = start_node.borrow().node_id.clone();
        let mut visited: IndexSet<String> = IndexSet::from([current_id.clone()]);
        let causal = [EdgeType::Causal];
        for _ in 0..max_hops {
            let mut edges = if direction == "causes" {
                self.storage.get_edges_to(&current_id, Some(&causal))
            } else {
                self.storage.get_edges_from(&current_id, Some(&causal))
            };
            if edges.is_empty() {
                break;
            }
            edges.sort_by(|a, b| {
                b.borrow().confidence.partial_cmp(&a.borrow().confidence).unwrap_or(std::cmp::Ordering::Equal)
            });
            let best_edge = edges.swap_remove(0);
            let next_id = {
                let b = best_edge.borrow();
                if direction == "causes" {
                    b.source_id.clone()
                } else {
                    b.target_id.clone()
                }
            };
            if visited.contains(&next_id) {
                break;
            }
            let Some(next_node) = self.storage.get_node(&next_id) else { break };
            chain.push((next_node, best_edge));
            visited.insert(next_id.clone());
            current_id = next_id;
        }
        chain
    }

    // ---------------------------------------------------------------------------------------------
    // Temporal summation
    // ---------------------------------------------------------------------------------------------

    /// Sum of `effective_weight * exp(-hours_ago / window)` over incoming edges activated within the window
    /// (Python: `window_hours or config.summation_window_hours`).
    pub fn temporal_summation(&self, node: &NodeRef, window_hours: Option<f64>) -> f64 {
        let window = or_default(window_hours, self.config.summation_window_hours);
        let now = PyDateTime::now_utc().assume_utc();
        let window_start = now - timedelta_hours(window);
        let node_id = node.borrow().node_id.clone();
        let edges = self.storage.get_edges_to(&node_id, None);
        let recent: Vec<EdgeRef> = edges
            .into_iter()
            .filter(|e| e.borrow().last_activated.with_utc_if_naive().assume_utc() > window_start)
            .collect();
        if recent.is_empty() {
            return 0.0;
        }
        let mut summed_signal = 0.0;
        for edge in &recent {
            let mut e = edge.borrow_mut();
            let last_activated = e.last_activated.with_utc_if_naive().assume_utc();
            let hours_ago = total_seconds(now - last_activated) / 3600.0;
            summed_signal += e.effective_weight() * (-hours_ago / window).exp();
        }
        summed_signal
    }

    /// Whether the summation reaches `threshold or config.summation_threshold`.
    pub fn check_summation_threshold(&self, node: &NodeRef, threshold: Option<f64>) -> bool {
        let thresh = or_default(threshold, self.config.summation_threshold);
        self.temporal_summation(node, None) >= thresh
    }

    // ---------------------------------------------------------------------------------------------
    // Long-term potentiation
    // ---------------------------------------------------------------------------------------------

    /// Strengthen (creating if needed, base weight 0.3) one CO_ACTIVATION edge per pair of the nodes, from
    /// the smaller id to the larger. Returns the number of edges saved (Python default context "retrieval").
    pub fn record_co_activation(&self, nodes: &[NodeRef], context: &str) -> usize {
        if nodes.len() < 2 {
            return 0;
        }
        let node_ids: Vec<String> = nodes.iter().map(|n| n.borrow().node_id.clone()).collect();
        let mut pairs: Vec<(String, String)> = Vec::new();
        for (i, id1) in node_ids.iter().enumerate() {
            for id2 in &node_ids[i + 1..] {
                let (a, b) = if id1 <= id2 { (id1, id2) } else { (id2, id1) };
                pairs.push((a.clone(), b.clone()));
            }
        }
        let existing = self.storage.get_edges_batch(&pairs, Some(EdgeType::CoActivation));
        let mut edges_to_save = Vec::new();
        for ((id1, id2), edge) in pairs.into_iter().zip(existing) {
            let edge = edge.unwrap_or_else(|| {
                let mut e = NeuralEdge::new(generate_edge_id(), id1, id2, EdgeType::CoActivation);
                e.base_weight = 0.3;
                let mut metadata = Map::new();
                metadata.insert("context".into(), json!(context));
                e.metadata = metadata;
                edge_ref(e)
            });
            edge.borrow_mut().activate();
            edges_to_save.push(edge);
        }
        self.storage.save_edges_batch(&edges_to_save);
        edges_to_save.len()
    }

    /// Spike-timing dependent plasticity on the temporal edge source -> target: pre-before-post
    /// (`time_delta_ms > 0`) adds `0.1 * exp(-dt/20)` to the LTP boost (capped), otherwise subtracts
    /// `0.05 * exp(dt/20)` (floored at 1.0). False when there is no such edge.
    pub fn apply_stdp(&self, source_id: &str, target_id: &str, time_delta_ms: f64) -> bool {
        let Some(edge) = self.storage.get_edge_between(source_id, target_id, Some(EdgeType::Temporal)) else {
            return false;
        };
        let tau = 20.0;
        {
            let mut e = edge.borrow_mut();
            if time_delta_ms > 0.0 {
                let stdp_factor = (-time_delta_ms / tau).exp();
                e.ltp_boost = f64::min(self.config.ltp_max_boost, e.ltp_boost + 0.1 * stdp_factor);
            } else {
                let stdp_factor = (time_delta_ms / tau).exp();
                e.ltp_boost = f64::max(1.0, e.ltp_boost - 0.05 * stdp_factor);
            }
            e.invalidate_weight_cache();
        }
        self.storage.save_edge(&edge);
        true
    }

    /// LTP boost of the co-activation edge between two nodes (0.0 without one).
    pub fn get_ltp_strength(&self, node1: &NodeRef, node2: &NodeRef) -> f64 {
        let (a, b) = (node1.borrow().node_id.clone(), node2.borrow().node_id.clone());
        let (source_id, target_id) = if a <= b { (a, b) } else { (b, a) };
        match self.storage.get_edge_between(&source_id, &target_id, Some(EdgeType::CoActivation)) {
            Some(edge) => edge.borrow().ltp_boost,
            None => 0.0,
        }
    }

    /// Decay every boosted edge's LTP towards 1.0 by `exp(-rate * days_since_activation)` (rate:
    /// `decay_factor or config.ltp_decay_rate`), snapping below 1.01 to 1.0. Returns the edges touched.
    pub fn decay_ltp(&self, session_key: &str, decay_factor: Option<f64>) -> usize {
        let decay = or_default(decay_factor, self.config.ltp_decay_rate);
        let mut decayed = 0;
        for edge in self.storage.get_all_edges(session_key) {
            let boosted = edge.borrow().ltp_boost > 1.0;
            if boosted {
                {
                    let mut e = edge.borrow_mut();
                    let days = e.days_since_activation();
                    e.ltp_boost = 1.0 + (e.ltp_boost - 1.0) * (-decay * days).exp();
                    if e.ltp_boost < 1.01 {
                        e.ltp_boost = 1.0;
                    }
                }
                self.storage.save_edge(&edge);
                decayed += 1;
            }
        }
        decayed
    }

    // ---------------------------------------------------------------------------------------------
    // Analysis
    // ---------------------------------------------------------------------------------------------

    /// Up to `before_count` predecessors and `after_count` successors (Python defaults 3 and 3); walking the
    /// chains activates their edges.
    pub fn get_temporal_context(&self, node: &NodeRef, before_count: usize, after_count: usize) -> TemporalContext {
        let before =
            self.follow_temporal_chain(node, "backward", before_count, 0.1).into_iter().map(|(n, _)| n).collect();
        let after = self.follow_temporal_chain(node, "forward", after_count, 0.1).into_iter().map(|(n, _)| n).collect();
        TemporalContext { before, after }
    }

    /// Transitions between dominant wave-dimension sets (amplitude >= 0.3) that occur at least
    /// `min_occurrences` times, most frequent first: `{"source_dimensions", "target_dimensions",
    /// "occurrences"}` with the dimensions as Python tuple reprs (Python default `min_occurrences` 2).
    pub fn find_temporal_patterns(&self, session_key: &str, min_occurrences: usize) -> Vec<Value> {
        let temporal_edges: Vec<EdgeRef> = self
            .storage
            .get_all_edges(session_key)
            .into_iter()
            .filter(|e| e.borrow().edge_type == EdgeType::Temporal)
            .collect();
        if temporal_edges.len() < min_occurrences {
            return Vec::new();
        }
        let mut transitions: IndexMap<(String, String), usize> = IndexMap::new();
        for edge in &temporal_edges {
            let (sid, tid) = {
                let e = edge.borrow();
                (e.source_id.clone(), e.target_id.clone())
            };
            let (Some(source), Some(target)) = (self.storage.get_node(&sid), self.storage.get_node(&tid)) else {
                continue;
            };
            let source_key = py_tuple_repr(&source.borrow().get_dominant_wave_dimensions(0.3));
            let target_key = py_tuple_repr(&target.borrow().get_dominant_wave_dimensions(0.3));
            *transitions.entry((source_key, target_key)).or_insert(0) += 1;
        }
        let mut patterns: Vec<(usize, Value)> = transitions
            .into_iter()
            .filter(|(_, count)| *count >= min_occurrences)
            .map(|((s, t), count)| {
                (count, json!({"source_dimensions": s, "target_dimensions": t, "occurrences": count}))
            })
            .collect();
        patterns.sort_by_key(|p| std::cmp::Reverse(p.0));
        patterns.into_iter().map(|(_, p)| p).collect()
    }

    // ---------------------------------------------------------------------------------------------
    // Refractory periods
    // ---------------------------------------------------------------------------------------------

    /// Whether "now" is before the node's `refractory_until` (naive taken as UTC).
    pub fn is_in_refractory(&self, node: &NodeRef) -> bool {
        let Some(until) = node.borrow().refractory_until else { return false };
        PyDateTime::now_utc().assume_utc() < until.with_utc_if_naive().assume_utc()
    }

    /// Remaining refractory time in milliseconds (0.0 when not refractory).
    pub fn get_refractory_remaining(&self, node: &NodeRef) -> f64 {
        let Some(until) = node.borrow().refractory_until else { return 0.0 };
        let now = PyDateTime::now_utc().assume_utc();
        let until = until.with_utc_if_naive().assume_utc();
        if now >= until {
            return 0.0;
        }
        let remaining = total_seconds(until - now) * 1000.0;
        f64::max(0.0, remaining)
    }

    /// Put the node into refractory for `duration_ms` from now and save it. Without a truthy duration this
    /// is the Python's `AttributeError` (see the module docs).
    pub fn set_refractory_period(&self, node: &NodeRef, duration_ms: Option<f64>) -> Result<(), EngineError> {
        let duration = match duration_ms {
            Some(d) if d != 0.0 => d,
            _ => {
                return Err(EngineError::AttributeError(
                    "'TemporalConfig' object has no attribute 'refractory_period_ms'".into(),
                ))
            }
        };
        node.borrow_mut().refractory_until = Some(PyDateTime::now_utc().add(timedelta_ms(duration)));
        self.storage.save_node(node);
        Ok(())
    }

    // ---------------------------------------------------------------------------------------------
    // Temporal consistency boosts
    // ---------------------------------------------------------------------------------------------

    /// `(boost, reason)` for a node against a query time window (either end open) and temporal markers.
    pub fn compute_temporal_consistency_boost(
        &self,
        node: &NodeRef,
        query_time_start: Option<PyDateTime>,
        query_time_end: Option<PyDateTime>,
        query_temporal_markers: Option<&[String]>,
    ) -> (f64, String) {
        let cfg = &self.config;
        if !cfg.temporal_consistency_enabled {
            return (1.0, "temporal_boost_disabled".into());
        }
        let n = node.borrow();
        let mut boost = 1.0;
        let mut reason = "no_temporal_match".to_string();
        let node_time = n.created_at.with_utc_if_naive().assume_utc();
        match (query_time_start, query_time_end) {
            (Some(start), Some(end)) => {
                let (start, end) = (start.with_utc_if_naive().assume_utc(), end.with_utc_if_naive().assume_utc());
                if start <= node_time && node_time <= end {
                    boost = cfg.temporal_exact_match_boost;
                    reason = "exact_temporal_overlap".into();
                } else {
                    let days_from_start = delta_days(node_time - start).abs();
                    let days_from_end = delta_days(node_time - end).abs();
                    let min_days = days_from_start.min(days_from_end);
                    if min_days as f64 <= cfg.temporal_proximity_window_days {
                        boost = cfg.temporal_proximity_boost;
                        reason = format!("temporal_proximity ({min_days} days)");
                    }
                }
            }
            (Some(start), None) => {
                if node_time >= start.with_utc_if_naive().assume_utc() {
                    boost = cfg.temporal_overlap_boost;
                    reason = "temporal_after_match".into();
                }
            }
            (None, Some(end)) => {
                if node_time <= end.with_utc_if_naive().assume_utc() {
                    boost = cfg.temporal_overlap_boost;
                    reason = "temporal_before_match".into();
                }
            }
            (None, None) => {}
        }

        if let Some(markers) = query_temporal_markers.filter(|m| !m.is_empty()) {
            if boost < cfg.temporal_overlap_boost {
                let content_lower = n.content.to_lowercase();
                let matched: Vec<&String> =
                    markers.iter().filter(|m| content_lower.contains(&m.to_lowercase())).collect();
                if !matched.is_empty() {
                    boost = if cfg.temporal_overlap_boost > boost { cfg.temporal_overlap_boost } else { boost };
                    let first: Vec<&str> = matched.iter().take(2).map(|m| m.as_str()).collect();
                    reason = format!("temporal_marker_match: {}", first.join(", "));
                }
            }
        }

        if !n.metadata.is_empty()
            && boost < cfg.temporal_overlap_boost
            && n.metadata.get("timestamp").is_some_and(py_truthy)
        {
            boost = if cfg.temporal_proximity_boost > boost { cfg.temporal_proximity_boost } else { boost };
            if reason == "no_temporal_match" {
                reason = "has_timestamp_metadata".into();
            }
        }
        (boost, reason)
    }

    /// Scores multiplied by their temporal consistency boost, re-sorted (stable) best first; unchanged when
    /// the feature is disabled.
    pub fn apply_temporal_consistency_boosts(
        &self,
        candidates: Vec<(NodeRef, f64)>,
        query_time_start: Option<PyDateTime>,
        query_time_end: Option<PyDateTime>,
        query_temporal_markers: Option<&[String]>,
    ) -> Vec<(NodeRef, f64)> {
        if !self.config.temporal_consistency_enabled {
            return candidates;
        }
        let mut boosted: Vec<(NodeRef, f64)> = candidates
            .into_iter()
            .map(|(node, score)| {
                let (boost, _reason) = self.compute_temporal_consistency_boost(
                    &node,
                    query_time_start,
                    query_time_end,
                    query_temporal_markers,
                );
                (node, score * boost)
            })
            .collect();
        boosted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        boosted
    }

    /// Multiply by `conflict_decay_rate` the base weight of temporal edges whose `direction` metadata contradicts
    /// the endpoints' timestamps by more than `tolerance_hours` (Python default 24). Returns the count decayed.
    pub fn decay_conflicting_edges(
        &self,
        session_key: &str,
        _reference_time: PyDateTime,
        tolerance_hours: f64,
    ) -> usize {
        if !self.config.conflict_detection_enabled {
            return 0;
        }
        let tolerance = total_seconds(timedelta_hours(tolerance_hours));
        let mut decayed = 0;
        let temporal_edges: Vec<EdgeRef> = self
            .storage
            .get_all_edges(session_key)
            .into_iter()
            .filter(|e| e.borrow().edge_type == EdgeType::Temporal)
            .collect();
        for edge in temporal_edges {
            let (sid, tid, direction) = {
                let e = edge.borrow();
                (e.source_id.clone(), e.target_id.clone(), e.metadata.get("direction").cloned())
            };
            let (Some(source), Some(target)) = (self.storage.get_node(&sid), self.storage.get_node(&tid)) else {
                continue;
            };
            let source_time = source.borrow().created_at.with_utc_if_naive().assume_utc();
            let target_time = target.borrow().created_at.with_utc_if_naive().assume_utc();
            let time_diff = total_seconds(target_time - source_time);
            let conflict = match direction.as_ref().and_then(Value::as_str) {
                Some("before") => time_diff < -tolerance,
                Some("after") => time_diff > tolerance,
                _ => false,
            };
            if conflict {
                {
                    let mut e = edge.borrow_mut();
                    e.base_weight *= self.config.conflict_decay_rate;
                    e.invalidate_weight_cache();
                }
                self.storage.save_edge(&edge);
                decayed += 1;
            }
        }
        decayed
    }
}

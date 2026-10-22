//! Edge gating functions (port of `retrieval/gating.py`).
//!
//! Every edge type has its own gate deciding whether, and how strongly, an edge passes signal. A gate reads the
//! edge (whose `effective_weight` caches per activation state, hence `&mut NeuralEdge`) and a free-form context
//! (`dict[str, Any]` in Python, a JSON object here; the keys each gate reads are listed on it). Registries:
//! [`EdgeGateRegistry`] (one gate per edge type, configured by [`GateConfig`]) and [`ProfiledGateRegistry`]
//! (the same gates scaled per edge type by a [`GateProfile`]); both implement [`GateRegistry`], which is what
//! the retriever and the electron pool hold.
//!
//! Python's `math.exp` raised `OverflowError` for arguments above ~709.78 (a sigmoid fed an input hundreds of
//! units below its threshold, or `soft_threshold` with a tiny `softness`); Rust's `exp` returns infinity and the
//! sigmoid its limit 0.0. No gate reaches that range with weights in [0, 3].

use crate::retrieval::data_types::{EdgeType, NeuralEdge};
use indexmap::IndexMap;
use serde_json::{Map, Value};
use std::rc::Rc;

/// The context dictionary handed to gates.
pub type GateContext = Map<String, Value>;

/// A gate function: `(edge, context) -> gate value`.
pub type GateFn = Rc<dyn Fn(&mut NeuralEdge, &GateContext) -> f64>;

/// `min(a, b)`: the first argument unless the second is strictly smaller (NaN never replaces).
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `max(a, b)`: the first argument unless the second is strictly greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `context.get(key, default)` for a numeric entry.
fn ctx_f64(context: &GateContext, key: &str, default: f64) -> f64 {
    context.get(key).and_then(Value::as_f64).unwrap_or(default)
}

/// Standard sigmoid with adjustable steepness and threshold (Python defaults: 5.0, 0.0).
pub fn sigmoid(x: f64, steepness: f64, threshold: f64) -> f64 {
    1.0 / (1.0 + (-steepness * (x - threshold)).exp())
}

/// `max(0, x - threshold)` (Python default threshold: 0.0).
pub fn relu(x: f64, threshold: f64) -> f64 {
    py_max(0.0, x - threshold)
}

/// Leaky ReLU: the full value above the threshold, `leak * (x - threshold)` below (defaults 0.0, 0.1).
pub fn leaky_relu(x: f64, threshold: f64, leak: f64) -> f64 {
    if x >= threshold {
        return x - threshold;
    }
    leak * (x - threshold)
}

/// Smooth threshold: `x * sigmoid(x, 1/softness, threshold)`; hard cut-off when `softness <= 0`
/// (defaults 0.3, 0.1).
pub fn soft_threshold(x: f64, threshold: f64, softness: f64) -> f64 {
    if softness <= 0.0 {
        return if x >= threshold { x } else { 0.0 };
    }
    x * sigmoid(x, 1.0 / softness, threshold)
}

/// Configuration for a gate registry.
#[derive(Clone, Debug, PartialEq)]
pub struct GateConfig {
    pub threshold: f64,
    pub steepness: f64,
    pub recency_half_life_days: f64,
    pub ltp_weight: f64,
    pub confidence_weight: f64,
}

impl Default for GateConfig {
    fn default() -> Self {
        GateConfig {
            threshold: 0.3,
            steepness: 5.0,
            recency_half_life_days: 7.0,
            ltp_weight: 0.3,
            confidence_weight: 0.3,
        }
    }
}

/// What the retriever and the electron pool need from a registry (Python duck-typed `compute_gate`).
pub trait GateRegistry {
    /// Gate value for an edge; Python's `context=None` is an empty context.
    fn compute_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64;
    /// The active configuration.
    fn config(&self) -> &GateConfig;
}

/// One specialised gate per edge type.
#[derive(Clone, Debug, Default)]
pub struct EdgeGateRegistry {
    config: GateConfig,
}

impl EdgeGateRegistry {
    pub fn new(config: Option<GateConfig>) -> Self {
        EdgeGateRegistry { config: config.unwrap_or_default() }
    }

    pub fn config(&self) -> &GateConfig {
        &self.config
    }

    /// The gate for an edge type. Python mapped all six types, so the fallback `_default_gate` is never
    /// returned from here (it stays reachable through [`EdgeGateRegistry::default_gate`]).
    pub fn get_gate(&self, edge_type: EdgeType) -> impl Fn(&mut NeuralEdge, &GateContext) -> f64 + '_ {
        move |edge: &mut NeuralEdge, context: &GateContext| self.apply_gate(edge_type, edge, context)
    }

    fn apply_gate(&self, edge_type: EdgeType, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        match edge_type {
            EdgeType::Temporal => self.temporal_gate(edge, context),
            EdgeType::Semantic => self.semantic_gate(edge, context),
            EdgeType::Entity => self.entity_gate(edge, context),
            EdgeType::Causal => self.causal_gate(edge, context),
            EdgeType::Hierarchy => self.hierarchy_gate(edge, context),
            EdgeType::CoActivation => self.co_activation_gate(edge, context),
        }
    }

    /// Gate value for an edge through its type's gate.
    pub fn compute_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        let edge_type = edge.edge_type;
        self.apply_gate(edge_type, edge, context)
    }

    /// Recency, conduction delay and LTP: `sigmoid(effective_weight * recency * delay_factor)`.
    pub fn temporal_gate(&self, edge: &mut NeuralEdge, _context: &GateContext) -> f64 {
        let age_days = edge.days_since_activation();
        let recency_signal = (-age_days / self.config.recency_half_life_days).exp();
        let delay_factor = 1.0 - (edge.propagation_delay_ms / 1000.0) * 0.1;
        let delay_factor = py_max(0.5, delay_factor);
        let raw = edge.effective_weight() * recency_signal * delay_factor;
        sigmoid(raw, self.config.steepness, 0.3)
    }

    /// Hard ReLU cut-off on `|effective_weight|`; context `similarity_threshold` (default 0.4).
    pub fn semantic_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        let similarity = edge.effective_weight().abs();
        let threshold = ctx_f64(context, "similarity_threshold", 0.4);
        relu(similarity, threshold)
    }

    /// Smooth blend of distance-decayed weight, co-occurrence, LTP and recency; context `entity_recency`
    /// (default 1.0), edge metadata `message_distance` (default 0).
    pub fn entity_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        let co_occurrence_signal = 1.0 - (-(edge.activation_count as f64) / 3.0).exp();
        let ltp_signal = (edge.ltp_boost - 1.0) / 2.0;
        let entity_recency = ctx_f64(context, "entity_recency", 1.0);
        let message_distance = edge.metadata.get("message_distance").and_then(Value::as_f64).unwrap_or(0.0);
        let distance_decay = (-message_distance / 15.0).exp();
        let raw = 0.35 * edge.effective_weight() * distance_decay
            + 0.25 * co_occurrence_signal
            + 0.20 * edge.confidence * ltp_signal
            + 0.20 * entity_recency;
        sigmoid(raw, 2.0, 0.15)
    }

    /// Strict: zero below confidence 0.6, then a steep sigmoid on `effective_weight * confidence`.
    pub fn causal_gate(&self, edge: &mut NeuralEdge, _context: &GateContext) -> f64 {
        if edge.confidence < 0.6 {
            return 0.0;
        }
        sigmoid(edge.effective_weight() * edge.confidence, 10.0, 0.4)
    }

    /// Always open, scaled by context `consolidation_factor` (default 1.0) and biased by `direction`
    /// ("up" x1.2, "down" x1.1), capped at 1.0.
    pub fn hierarchy_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        let consolidation_factor = ctx_f64(context, "consolidation_factor", 1.0);
        let direction = context.get("direction").and_then(Value::as_str).unwrap_or("both");
        let mut base_gate = edge.effective_weight() * consolidation_factor;
        if direction == "up" {
            base_gate *= 1.2;
        } else if direction == "down" {
            base_gate *= 1.1;
        }
        py_min(1.0, base_gate)
    }

    /// Hebbian link: closed below context `min_activations` (default 2), then a sigmoid on LTP and weight.
    pub fn co_activation_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        let min_activations = ctx_f64(context, "min_activations", 2.0);
        if (edge.activation_count as f64) < min_activations {
            return 0.0;
        }
        let ltp_signal = (edge.ltp_boost - 1.0) / 2.0;
        let combined_signal = 0.7 * ltp_signal + 0.3 * edge.effective_weight();
        sigmoid(combined_signal, self.config.steepness, 0.3)
    }

    /// Fallback gate: suppression factor for inhibitory edges, `|weight|` for excitatory ones. Unreachable
    /// through [`EdgeGateRegistry::get_gate`] (every edge type has a gate), ported for completeness.
    pub fn default_gate(&self, edge: &mut NeuralEdge, _context: &GateContext) -> f64 {
        if edge.sign < 0.0 {
            py_max(0.0, 1.0 - edge.effective_weight().abs())
        } else {
            edge.effective_weight().abs()
        }
    }
}

impl GateRegistry for EdgeGateRegistry {
    fn compute_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        EdgeGateRegistry::compute_gate(self, edge, context)
    }

    fn config(&self) -> &GateConfig {
        &self.config
    }
}

/// Standalone inhibitory gate: 1.0 for excitatory edges, `max(0, 1 - |weight| * inhibition_strength)` for
/// inhibitory ones (context `inhibition_strength`, default 1.0).
pub fn inhibitory_gate(edge: &mut NeuralEdge, context: &GateContext) -> f64 {
    if edge.sign >= 0.0 {
        return 1.0;
    }
    let strength = ctx_f64(context, "inhibition_strength", 1.0);
    let suppression = edge.effective_weight().abs() * strength;
    py_max(0.0, 1.0 - suppression)
}

/// Several gates combined by "multiply", "min", "max" or "mean" (anything else: the first gate's value).
#[derive(Clone)]
pub struct CompositeGate {
    gates: Vec<GateFn>,
    combination: String,
}

impl CompositeGate {
    pub fn new(combination: &str) -> Self {
        CompositeGate { gates: Vec::new(), combination: combination.to_string() }
    }

    /// Add a gate (chainable).
    pub fn add_gate(&mut self, gate_fn: GateFn) -> &mut Self {
        self.gates.push(gate_fn);
        self
    }

    pub fn call(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        if self.gates.is_empty() {
            return 1.0;
        }
        let values: Vec<f64> = self.gates.iter().map(|g| g(edge, context)).collect();
        match self.combination.as_str() {
            "multiply" => {
                let mut result = 1.0;
                for v in &values {
                    result *= v;
                }
                result
            }
            "min" => values[1..].iter().fold(values[0], |m, v| py_min(m, *v)),
            "max" => values[1..].iter().fold(values[0], |m, v| py_max(m, *v)),
            "mean" => {
                let mut sum = 0.0;
                for v in &values {
                    sum += v;
                }
                sum / values.len() as f64
            }
            _ => values[0],
        }
    }

    /// The composite as a [`GateFn`] (so it can be nested or wrapped by an [`AdaptiveGate`]).
    pub fn into_gate_fn(self) -> GateFn {
        Rc::new(move |edge: &mut NeuralEdge, context: &GateContext| self.call(edge, context))
    }
}

/// A gate whose pass threshold adapts to the candidate count.
#[derive(Clone)]
pub struct AdaptiveGate {
    base_gate: GateFn,
    min_threshold: f64,
    max_threshold: f64,
    current_threshold: f64,
}

impl AdaptiveGate {
    /// Python defaults: `min_threshold = 0.1`, `max_threshold = 0.7`; starts half way.
    pub fn new(base_gate: GateFn, min_threshold: f64, max_threshold: f64) -> Self {
        AdaptiveGate {
            base_gate,
            min_threshold,
            max_threshold,
            current_threshold: (min_threshold + max_threshold) / 2.0,
        }
    }

    pub fn current_threshold(&self) -> f64 {
        self.current_threshold
    }

    /// Raise the threshold by 0.05 above twice the target count, lower it below half (Python default
    /// `target_count = 50`).
    pub fn adapt_threshold(&mut self, candidate_count: i64, target_count: i64) {
        if candidate_count > target_count * 2 {
            self.current_threshold = py_min(self.max_threshold, self.current_threshold + 0.05);
        } else if (candidate_count as f64) < target_count as f64 / 2.0 {
            self.current_threshold = py_max(self.min_threshold, self.current_threshold - 0.05);
        }
    }

    pub fn call(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        let base_value = (self.base_gate)(edge, context);
        if base_value >= self.current_threshold {
            base_value
        } else {
            0.0
        }
    }
}

/// Strict thresholds for high-precision retrieval.
pub fn create_strict_registry() -> EdgeGateRegistry {
    EdgeGateRegistry::new(Some(GateConfig {
        threshold: 0.5,
        steepness: 8.0,
        recency_half_life_days: 3.0,
        ltp_weight: 0.2,
        confidence_weight: 0.4,
    }))
}

/// Permissive thresholds for high-recall retrieval.
pub fn create_permissive_registry() -> EdgeGateRegistry {
    EdgeGateRegistry::new(Some(GateConfig {
        threshold: 0.15,
        steepness: 3.0,
        recency_half_life_days: 14.0,
        ltp_weight: 0.4,
        confidence_weight: 0.2,
    }))
}

/// The default configuration.
pub fn create_balanced_registry() -> EdgeGateRegistry {
    EdgeGateRegistry::new(Some(GateConfig::default()))
}

/// Per-query-type weighting of edge types (single-hop vs multi-hop routing).
#[derive(Clone, Debug, PartialEq)]
pub struct GateProfile {
    pub name: String,
    pub semantic_weight: f64,
    pub entity_weight: f64,
    pub temporal_weight: f64,
    pub causal_weight: f64,
    pub hierarchy_weight: f64,
    pub co_activation_weight: f64,
    pub base_config: Option<GateConfig>,
    /// Below 1.0 favours known paths, above explores (applies to every edge type but semantic and entity).
    pub exploration_factor: f64,
}

impl GateProfile {
    /// `GateProfile(name=...)`: every weight 1.0, no base config.
    pub fn new(name: &str) -> Self {
        GateProfile {
            name: name.to_string(),
            semantic_weight: 1.0,
            entity_weight: 1.0,
            temporal_weight: 1.0,
            causal_weight: 1.0,
            hierarchy_weight: 1.0,
            co_activation_weight: 1.0,
            base_config: None,
            exploration_factor: 1.0,
        }
    }
}

/// `SINGLE_HOP_PROFILE`: favour semantic and entity edges, exploit known paths.
pub fn single_hop_profile() -> GateProfile {
    GateProfile {
        name: "single_hop".into(),
        semantic_weight: 1.3,
        entity_weight: 1.4,
        temporal_weight: 0.8,
        causal_weight: 0.7,
        hierarchy_weight: 0.9,
        co_activation_weight: 0.6,
        exploration_factor: 0.7,
        base_config: Some(GateConfig {
            threshold: 0.35,
            steepness: 6.0,
            recency_half_life_days: 7.0,
            ltp_weight: 0.25,
            confidence_weight: 0.35,
        }),
    }
}

/// `MULTI_HOP_PROFILE`: favour causal and temporal chains, explore more.
pub fn multi_hop_profile() -> GateProfile {
    GateProfile {
        name: "multi_hop".into(),
        semantic_weight: 0.9,
        entity_weight: 1.1,
        temporal_weight: 1.2,
        causal_weight: 1.4,
        hierarchy_weight: 1.2,
        co_activation_weight: 1.0,
        exploration_factor: 1.3,
        base_config: Some(GateConfig {
            threshold: 0.25,
            steepness: 4.0,
            recency_half_life_days: 10.0,
            ltp_weight: 0.35,
            confidence_weight: 0.25,
        }),
    }
}

/// `TEMPORAL_QUERY_PROFILE`.
pub fn temporal_query_profile() -> GateProfile {
    GateProfile {
        name: "temporal".into(),
        semantic_weight: 0.8,
        entity_weight: 1.0,
        temporal_weight: 1.5,
        causal_weight: 1.2,
        hierarchy_weight: 0.7,
        co_activation_weight: 0.8,
        exploration_factor: 1.1,
        base_config: Some(GateConfig {
            threshold: 0.3,
            steepness: 5.0,
            recency_half_life_days: 14.0,
            ltp_weight: 0.2,
            confidence_weight: 0.3,
        }),
    }
}

/// `OPEN_DOMAIN_PROFILE`.
pub fn open_domain_profile() -> GateProfile {
    GateProfile {
        name: "open_domain".into(),
        semantic_weight: 1.1,
        entity_weight: 1.0,
        temporal_weight: 1.0,
        causal_weight: 1.0,
        hierarchy_weight: 1.0,
        co_activation_weight: 1.2,
        exploration_factor: 1.2,
        base_config: Some(GateConfig {
            threshold: 0.2,
            steepness: 4.0,
            recency_half_life_days: 7.0,
            ltp_weight: 0.4,
            confidence_weight: 0.2,
        }),
    }
}

/// `GATE_PROFILES`, in Python's declaration order.
pub fn gate_profiles() -> IndexMap<&'static str, GateProfile> {
    IndexMap::from([
        ("single_hop", single_hop_profile()),
        ("multi_hop", multi_hop_profile()),
        ("temporal", temporal_query_profile()),
        ("open_domain", open_domain_profile()),
        ("default", GateProfile::new("default")),
    ])
}

/// The profile for a query type, `default` for unknown types.
pub fn get_profile(query_type: &str) -> GateProfile {
    let mut profiles = gate_profiles();
    match profiles.shift_remove(query_type) {
        Some(p) => p,
        None => profiles.shift_remove("default").expect("default profile"),
    }
}

/// A plain registry with the profile's base configuration (no per-type weighting).
pub fn create_profiled_registry(profile: &GateProfile) -> EdgeGateRegistry {
    EdgeGateRegistry::new(Some(profile.base_config.clone().unwrap_or_default()))
}

/// A registry that scales each gate by its profile's edge-type weight (and the exploration factor for
/// non-semantic, non-entity edges), capped at 1.0.
#[derive(Clone, Debug)]
pub struct ProfiledGateRegistry {
    base: EdgeGateRegistry,
    profile: GateProfile,
}

impl ProfiledGateRegistry {
    /// Defaults to the single-hop profile.
    pub fn new(profile: Option<GateProfile>) -> Self {
        let profile = profile.unwrap_or_else(single_hop_profile);
        let config = profile.base_config.clone().unwrap_or_default();
        ProfiledGateRegistry { base: EdgeGateRegistry::new(Some(config)), profile }
    }

    pub fn profile(&self) -> &GateProfile {
        &self.profile
    }

    /// The underlying per-type gates (they read the registry's current configuration).
    pub fn base(&self) -> &EdgeGateRegistry {
        &self.base
    }

    pub fn compute_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        let base_value = self.base.compute_gate(edge, context);
        let edge_type = edge.edge_type;
        let mut weight = match edge_type {
            EdgeType::Semantic => self.profile.semantic_weight,
            EdgeType::Entity => self.profile.entity_weight,
            EdgeType::Temporal => self.profile.temporal_weight,
            EdgeType::Causal => self.profile.causal_weight,
            EdgeType::Hierarchy => self.profile.hierarchy_weight,
            EdgeType::CoActivation => self.profile.co_activation_weight,
        };
        if !matches!(edge_type, EdgeType::Semantic | EdgeType::Entity) {
            weight *= self.profile.exploration_factor;
        }
        py_min(1.0, base_value * weight)
    }

    /// Switch to a named profile; unknown names are ignored, and a profile without a base config (`default`)
    /// keeps the current gate configuration, as in Python.
    pub fn set_profile(&mut self, profile_name: &str) {
        if let Some(p) = gate_profiles().shift_remove(profile_name) {
            self.profile = p;
            if let Some(c) = &self.profile.base_config {
                self.base.config = c.clone();
            }
        }
    }
}

impl GateRegistry for ProfiledGateRegistry {
    fn compute_gate(&self, edge: &mut NeuralEdge, context: &GateContext) -> f64 {
        ProfiledGateRegistry::compute_gate(self, edge, context)
    }

    fn config(&self) -> &GateConfig {
        &self.base.config
    }
}

/// Registry tuned for single-hop queries.
pub fn create_single_hop_registry() -> ProfiledGateRegistry {
    ProfiledGateRegistry::new(Some(single_hop_profile()))
}

/// Registry tuned for multi-hop queries.
pub fn create_multi_hop_registry() -> ProfiledGateRegistry {
    ProfiledGateRegistry::new(Some(multi_hop_profile()))
}

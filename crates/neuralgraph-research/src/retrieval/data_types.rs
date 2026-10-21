//! Data types of the neural memory graph (port of `retrieval/data_types.py`).
//!
//! Nodes and edges are shared, mutable objects in the Python engine: the same `NeuralNode` instance sits in the
//! pooled store and in a per-agent store, and saving an edge updates its source node's fairness counters in
//! place. [`NodeRef`]/[`EdgeRef`] (`Rc<RefCell<_>>`) keep that reference semantics. The engine is single-threaded
//! (asyncio in Python, one task here), so `Rc` suffices and borrow checks catch accidental aliasing at runtime.

use crate::pytime::{total_seconds, PyDateTime};
use chrono::TimeDelta;
use indexmap::IndexMap;
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::rc::Rc;

/// Shared handle to a node (a Python object reference).
pub type NodeRef = Rc<RefCell<NeuralNode>>;
/// Shared handle to an edge.
pub type EdgeRef = Rc<RefCell<NeuralEdge>>;

/// Wrap a node into a shared handle.
pub fn node_ref(node: NeuralNode) -> NodeRef {
    Rc::new(RefCell::new(node))
}

/// Wrap an edge into a shared handle.
pub fn edge_ref(edge: NeuralEdge) -> EdgeRef {
    Rc::new(RefCell::new(edge))
}

/// Hierarchical layer (`NodeLayer`, an `IntEnum`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum NodeLayer {
    Message = 0,
    Episode = 1,
    Topic = 2,
    Persona = 3,
    Temporal = 4,
}

impl NodeLayer {
    pub const ALL: [NodeLayer; 5] =
        [NodeLayer::Message, NodeLayer::Episode, NodeLayer::Topic, NodeLayer::Persona, NodeLayer::Temporal];

    /// `.value`
    pub fn value(self) -> i64 {
        self as i64
    }

    /// `.name`
    pub fn name(self) -> &'static str {
        match self {
            NodeLayer::Message => "MESSAGE",
            NodeLayer::Episode => "EPISODE",
            NodeLayer::Topic => "TOPIC",
            NodeLayer::Persona => "PERSONA",
            NodeLayer::Temporal => "TEMPORAL",
        }
    }

    /// `NodeLayer(value)`
    pub fn from_value(v: i64) -> Option<NodeLayer> {
        NodeLayer::ALL.into_iter().find(|l| l.value() == v)
    }
}

/// Edge type (`EdgeType`, a `str` enum).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum EdgeType {
    Temporal,
    Semantic,
    Entity,
    Causal,
    Hierarchy,
    CoActivation,
}

impl EdgeType {
    pub const ALL: [EdgeType; 6] = [
        EdgeType::Temporal,
        EdgeType::Semantic,
        EdgeType::Entity,
        EdgeType::Causal,
        EdgeType::Hierarchy,
        EdgeType::CoActivation,
    ];

    /// `.value`
    pub fn value(self) -> &'static str {
        match self {
            EdgeType::Temporal => "temporal",
            EdgeType::Semantic => "semantic",
            EdgeType::Entity => "entity",
            EdgeType::Causal => "causal",
            EdgeType::Hierarchy => "hierarchy",
            EdgeType::CoActivation => "co_activation",
        }
    }

    /// `.name`
    pub fn name(self) -> &'static str {
        match self {
            EdgeType::Temporal => "TEMPORAL",
            EdgeType::Semantic => "SEMANTIC",
            EdgeType::Entity => "ENTITY",
            EdgeType::Causal => "CAUSAL",
            EdgeType::Hierarchy => "HIERARCHY",
            EdgeType::CoActivation => "CO_ACTIVATION",
        }
    }

    /// `EdgeType(value)`
    pub fn from_value(v: &str) -> Option<EdgeType> {
        EdgeType::ALL.into_iter().find(|t| t.value() == v)
    }
}

/// Consolidation state (`ConsolidationState`, a `str` enum).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ConsolidationState {
    Active,
    Consolidating,
    Archived,
    Evicted,
}

impl ConsolidationState {
    pub const ALL: [ConsolidationState; 4] = [
        ConsolidationState::Active,
        ConsolidationState::Consolidating,
        ConsolidationState::Archived,
        ConsolidationState::Evicted,
    ];

    pub fn value(self) -> &'static str {
        match self {
            ConsolidationState::Active => "active",
            ConsolidationState::Consolidating => "consolidating",
            ConsolidationState::Archived => "archived",
            ConsolidationState::Evicted => "evicted",
        }
    }

    pub fn from_value(v: &str) -> Option<ConsolidationState> {
        ConsolidationState::ALL.into_iter().find(|s| s.value() == v)
    }
}

fn days_since(t: &PyDateTime) -> f64 {
    let now = PyDateTime::now_utc().assume_utc();
    total_seconds(now - t.assume_utc()) / 86400.0
}

/// Weighted, gated edge with Hebbian (LTP) dynamics.
#[derive(Clone, Debug)]
pub struct NeuralEdge {
    pub edge_id: String,
    pub source_id: String,
    pub target_id: String,
    pub edge_type: EdgeType,
    pub base_weight: f64,
    pub sign: f64,
    pub confidence: f64,
    pub context_embedding: Option<Vec<f64>>,
    pub created_at: PyDateTime,
    pub last_activated: PyDateTime,
    pub activation_count: i64,
    pub propagation_delay_ms: f64,
    pub ltp_boost: f64,
    pub ltp_decay_rate: f64,
    pub gate_threshold: f64,
    pub metadata: Map<String, Value>,
    /// `_cached_weight` / `_cached_weight_key`: the first `effective_weight` computed for an activation state is
    /// reused until the next activation, even though "now" moves on (the Python cache did exactly that).
    cached_weight: Option<(f64, (PyDateTime, i64, u64))>,
}

impl NeuralEdge {
    /// `NeuralEdge(edge_id, source_id, target_id, edge_type)` with every other field at its default.
    pub fn new(
        edge_id: impl Into<String>,
        source_id: impl Into<String>,
        target_id: impl Into<String>,
        edge_type: EdgeType,
    ) -> Self {
        let now = PyDateTime::now_utc();
        NeuralEdge {
            edge_id: edge_id.into(),
            source_id: source_id.into(),
            target_id: target_id.into(),
            edge_type,
            base_weight: 1.0,
            sign: 1.0,
            confidence: 1.0,
            context_embedding: None,
            created_at: now,
            last_activated: now,
            activation_count: 0,
            propagation_delay_ms: 0.0,
            ltp_boost: 1.0,
            ltp_decay_rate: 0.01,
            gate_threshold: 0.3,
            metadata: Map::new(),
            cached_weight: None,
        }
    }

    pub fn with_weight(mut self, base_weight: f64) -> Self {
        self.base_weight = base_weight;
        self
    }

    pub fn with_metadata(mut self, metadata: Map<String, Value>) -> Self {
        self.metadata = metadata;
        self
    }

    /// Record an activation and update LTP ("fire together, wire together").
    pub fn activate(&mut self) {
        self.activation_count += 1;
        self.last_activated = PyDateTime::now_utc();
        self.ltp_boost = f64::min(3.0, 1.0 + 0.15 * (1.0 + self.activation_count as f64).ln());
        self.cached_weight = None;
    }

    pub fn days_since_activation(&self) -> f64 {
        days_since(&self.last_activated)
    }

    pub fn days_since_creation(&self) -> f64 {
        days_since(&self.created_at)
    }

    /// Effective weight with recency decay and decaying LTP, cached per activation state.
    pub fn effective_weight(&mut self) -> f64 {
        let key = (self.last_activated, self.activation_count, self.ltp_boost.to_bits());
        if let Some((w, k)) = &self.cached_weight {
            if *k == key {
                return *w;
            }
        }
        let days = self.days_since_activation();
        let recency_factor = (-days / 30.0).exp();
        let ltp_decay = (-self.ltp_decay_rate * days).exp();
        let current_ltp = 1.0 + (self.ltp_boost - 1.0) * ltp_decay;
        let w = self.sign * self.base_weight * self.confidence * recency_factor * current_ltp;
        self.cached_weight = Some((w, key));
        w
    }

    /// Sigmoid gate on `input_signal * |effective_weight|`.
    pub fn gate(&mut self, input_signal: f64, steepness: f64) -> f64 {
        let raw_signal = input_signal * self.effective_weight().abs();
        1.0 / (1.0 + (-steepness * (raw_signal - self.gate_threshold)).exp())
    }

    pub fn to_dict(&self) -> Value {
        json!({
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value(),
            "base_weight": self.base_weight,
            "sign": self.sign,
            "confidence": self.confidence,
            "context_embedding": self.context_embedding,
            "created_at": self.created_at.isoformat(),
            "last_activated": self.last_activated.isoformat(),
            "activation_count": self.activation_count,
            "propagation_delay_ms": self.propagation_delay_ms,
            "ltp_boost": self.ltp_boost,
            "ltp_decay_rate": self.ltp_decay_rate,
            "gate_threshold": self.gate_threshold,
            "metadata": self.metadata,
        })
    }

    /// `NeuralEdge.from_dict`; `None` where Python would raise (missing keys, unknown edge type).
    pub fn from_dict(d: &Map<String, Value>) -> Option<Self> {
        let now = PyDateTime::now_utc();
        let dt = |k: &str| match d.get(k) {
            Some(Value::String(s)) => PyDateTime::fromisoformat(s),
            _ => Some(now),
        };
        Some(NeuralEdge {
            edge_id: d.get("edge_id")?.as_str()?.to_string(),
            source_id: d.get("source_id")?.as_str()?.to_string(),
            target_id: d.get("target_id")?.as_str()?.to_string(),
            edge_type: EdgeType::from_value(d.get("edge_type")?.as_str()?)?,
            base_weight: get_f64(d, "base_weight", 1.0),
            sign: get_f64(d, "sign", 1.0),
            confidence: get_f64(d, "confidence", 1.0),
            context_embedding: d.get("context_embedding").and_then(vec_f64),
            created_at: dt("created_at")?,
            last_activated: dt("last_activated")?,
            activation_count: d.get("activation_count").and_then(Value::as_i64).unwrap_or(0),
            propagation_delay_ms: get_f64(d, "propagation_delay_ms", 0.0),
            ltp_boost: get_f64(d, "ltp_boost", 1.0),
            ltp_decay_rate: get_f64(d, "ltp_decay_rate", 0.01),
            gate_threshold: get_f64(d, "gate_threshold", 0.3),
            metadata: d.get("metadata").and_then(Value::as_object).cloned().unwrap_or_default(),
            cached_weight: None,
        })
    }
}

fn get_f64(d: &Map<String, Value>, k: &str, default: f64) -> f64 {
    d.get(k).and_then(Value::as_f64).unwrap_or(default)
}

fn vec_f64(v: &Value) -> Option<Vec<f64>> {
    v.as_array().map(|a| a.iter().filter_map(Value::as_f64).collect())
}

/// Node with layer, activation and consolidation state.
#[derive(Clone, Debug)]
pub struct NeuralNode {
    pub node_id: String,
    pub layer: NodeLayer,
    pub content: String,
    pub embedding: Option<Vec<f64>>,
    pub wave_amplitudes: IndexMap<String, f64>,
    pub activation_level: f64,
    pub last_activated: PyDateTime,
    pub refractory_until: Option<PyDateTime>,
    pub heat_score: f64,
    pub importance_score: f64,
    pub consolidation_state: ConsolidationState,
    pub parent_id: Option<String>,
    pub child_ids: Vec<String>,
    pub entity_ids: Vec<String>,
    pub session_key: String,
    pub created_at: PyDateTime,
    pub updated_at: PyDateTime,
    pub source_memory_ids: Vec<String>,
    pub metadata: Map<String, Value>,
    pub edge_type_counts: IndexMap<String, i64>,
    pub dominant_dimension: String,
    pub diversity_score: f64,
    /// `_activation_count`, created on the first `activate()` (Python used `hasattr`).
    pub activation_count: i64,
}

impl NeuralNode {
    /// `NeuralNode(node_id, layer, content)` with every other field at its default.
    pub fn new(node_id: impl Into<String>, layer: NodeLayer, content: impl Into<String>) -> Self {
        let now = PyDateTime::now_utc();
        NeuralNode {
            node_id: node_id.into(),
            layer,
            content: content.into(),
            embedding: None,
            wave_amplitudes: IndexMap::new(),
            activation_level: 0.0,
            last_activated: now,
            refractory_until: None,
            heat_score: 0.3,
            importance_score: 0.5,
            consolidation_state: ConsolidationState::Active,
            parent_id: None,
            child_ids: Vec::new(),
            entity_ids: Vec::new(),
            session_key: String::new(),
            created_at: now,
            updated_at: now,
            source_memory_ids: Vec::new(),
            metadata: Map::new(),
            edge_type_counts: IndexMap::new(),
            dominant_dimension: String::new(),
            diversity_score: 0.5,
            activation_count: 0,
        }
    }

    /// Track the edge-type mix for the fairness score (normalised Shannon diversity).
    pub fn update_edge_counts(&mut self, edge_type: &str) {
        *self.edge_type_counts.entry(edge_type.to_string()).or_insert(0) += 1;
        if !self.edge_type_counts.is_empty() {
            let num_types = self.edge_type_counts.len();
            let total_edges: i64 = self.edge_type_counts.values().sum();
            if total_edges > 0 && num_types > 1 {
                let mut entropy = 0.0;
                for count in self.edge_type_counts.values() {
                    if *count > 0 {
                        let p = *count as f64 / total_edges as f64;
                        entropy -= p * p.ln();
                    }
                }
                let max_entropy = (num_types as f64).ln();
                self.diversity_score = if max_entropy > 0.0 { entropy / max_entropy } else { 0.5 };
            } else {
                self.diversity_score = 0.5;
            }
        }
    }

    /// The dimension with the largest wave amplitude, if it exceeds 0.2 (first one wins ties, like `max`).
    pub fn update_dominant_dimension(&mut self) {
        if let Some((dim, amp)) = py_max_item(&self.wave_amplitudes) {
            self.dominant_dimension = if amp > 0.2 { dim } else { String::new() };
        }
    }

    pub fn get_fairness_weight(&self, query_wave_amplitudes: &IndexMap<String, f64>) -> f64 {
        if query_wave_amplitudes.is_empty() || self.dominant_dimension.is_empty() {
            return 1.0;
        }
        let Some((qdim, qamp)) = py_max_item(query_wave_amplitudes) else { return 1.0 };
        let query_dominant = if qamp > 0.2 { qdim } else { String::new() };
        if query_dominant.is_empty() {
            return 1.0;
        }
        if self.dominant_dimension == query_dominant {
            return 1.5 + self.diversity_score * 0.5;
        }
        0.7 + self.diversity_score * 0.3
    }

    pub fn is_in_refractory(&self) -> bool {
        match &self.refractory_until {
            None => false,
            Some(r) => PyDateTime::now_utc().assume_utc() < r.assume_utc(),
        }
    }

    /// `speaker_id`: `metadata["speaker"]`, falling back to the legacy `metadata["producer_id"]`.
    pub fn speaker_id(&self) -> String {
        for key in ["speaker", "producer_id"] {
            match self.metadata.get(key) {
                Some(Value::String(s)) if !s.is_empty() => return s.clone(),
                Some(v) if neuralgraph_memory::pycompat::py_truthy(v) => {
                    return neuralgraph_memory::pycompat::py_str(v);
                }
                _ => {}
            }
        }
        String::new()
    }

    /// Activate with an adaptive refractory period and an LTP-style heat boost; returns the activation level.
    pub fn activate(&mut self, signal_strength: f64, refractory_ms: Option<f64>) -> f64 {
        let mut signal = signal_strength;
        if self.is_in_refractory() {
            signal *= 0.3;
        }
        self.activation_level = f64::min(1.0, self.activation_level + signal);
        self.last_activated = PyDateTime::now_utc();
        self.activation_count += 1;
        let refractory = match refractory_ms {
            Some(ms) => ms,
            None => {
                let mut ms = match self.layer {
                    NodeLayer::Message => 50.0,
                    NodeLayer::Episode => 100.0,
                    NodeLayer::Topic => 200.0,
                    NodeLayer::Persona => 500.0,
                    NodeLayer::Temporal => 100.0,
                };
                if self.activation_count > 10 {
                    ms *= 1.5;
                }
                ms
            }
        };
        self.refractory_until = Some(PyDateTime::now_utc().add(timedelta_ms(refractory)));
        let base_boost = 0.2;
        let recency_bonus = 0.05 * f64::min(1.0, self.activation_count as f64 / 5.0);
        let heat_boost = base_boost + recency_bonus;
        self.heat_score = f64::min(10.0, self.heat_score + heat_boost);
        self.activation_level
    }

    pub fn decay_activation(&mut self, decay_rate: f64) {
        self.activation_level = f64::max(0.0, self.activation_level - decay_rate);
    }

    pub fn days_since_creation(&self) -> f64 {
        days_since(&self.created_at)
    }

    pub fn days_since_activation(&self) -> f64 {
        days_since(&self.last_activated)
    }

    pub fn get_dominant_wave_dimensions(&self, threshold: f64) -> Vec<String> {
        self.wave_amplitudes.iter().filter(|(_, a)| **a >= threshold).map(|(d, _)| d.clone()).collect()
    }

    pub fn to_dict(&self) -> Value {
        json!({
            "node_id": self.node_id,
            "layer": self.layer.value(),
            "content": self.content,
            "embedding": self.embedding,
            "wave_amplitudes": self.wave_amplitudes,
            "activation_level": self.activation_level,
            "last_activated": self.last_activated.isoformat(),
            "refractory_until": self.refractory_until.map(|r| r.isoformat()),
            "heat_score": self.heat_score,
            "importance_score": self.importance_score,
            "consolidation_state": self.consolidation_state.value(),
            "parent_id": self.parent_id,
            "child_ids": self.child_ids,
            "entity_ids": self.entity_ids,
            "session_key": self.session_key,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source_memory_ids": self.source_memory_ids,
            "metadata": self.metadata,
            "edge_type_counts": self.edge_type_counts,
            "dominant_dimension": self.dominant_dimension,
            "diversity_score": self.diversity_score,
        })
    }

    /// `NeuralNode.from_dict`; `None` where Python would raise.
    pub fn from_dict(d: &Map<String, Value>) -> Option<Self> {
        let now = PyDateTime::now_utc();
        let dt = |k: &str| match d.get(k) {
            Some(Value::String(s)) => PyDateTime::fromisoformat(s),
            _ => Some(now),
        };
        let strings = |k: &str| -> Vec<String> {
            d.get(k)
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
                .unwrap_or_default()
        };
        let refractory_until = match d.get("refractory_until") {
            Some(Value::String(s)) if !s.is_empty() => Some(PyDateTime::fromisoformat(s)?),
            _ => None,
        };
        Some(NeuralNode {
            node_id: d.get("node_id")?.as_str()?.to_string(),
            layer: NodeLayer::from_value(d.get("layer")?.as_i64()?)?,
            content: d.get("content")?.as_str()?.to_string(),
            embedding: d.get("embedding").and_then(vec_f64),
            wave_amplitudes: d
                .get("wave_amplitudes")
                .and_then(Value::as_object)
                .map(|o| o.iter().filter_map(|(k, v)| v.as_f64().map(|f| (k.clone(), f))).collect())
                .unwrap_or_default(),
            activation_level: get_f64(d, "activation_level", 0.0),
            last_activated: dt("last_activated")?,
            refractory_until,
            heat_score: get_f64(d, "heat_score", 0.5),
            importance_score: get_f64(d, "importance_score", 0.5),
            consolidation_state: ConsolidationState::from_value(
                d.get("consolidation_state").and_then(Value::as_str).unwrap_or("active"),
            )?,
            parent_id: d.get("parent_id").and_then(Value::as_str).map(str::to_string),
            child_ids: strings("child_ids"),
            entity_ids: strings("entity_ids"),
            session_key: d.get("session_key").and_then(Value::as_str).unwrap_or("").to_string(),
            created_at: dt("created_at")?,
            updated_at: dt("updated_at")?,
            source_memory_ids: strings("source_memory_ids"),
            metadata: d.get("metadata").and_then(Value::as_object).cloned().unwrap_or_default(),
            edge_type_counts: d
                .get("edge_type_counts")
                .and_then(Value::as_object)
                .map(|o| o.iter().filter_map(|(k, v)| v.as_i64().map(|c| (k.clone(), c))).collect())
                .unwrap_or_default(),
            dominant_dimension: d.get("dominant_dimension").and_then(Value::as_str).unwrap_or("").to_string(),
            diversity_score: get_f64(d, "diversity_score", 0.5),
            activation_count: 0,
        })
    }
}

/// `max(d.items(), key=lambda x: x[1])`: the first item holding the maximum value.
fn py_max_item(d: &IndexMap<String, f64>) -> Option<(String, f64)> {
    let mut best: Option<(String, f64)> = None;
    for (k, v) in d {
        match &best {
            // only a strictly greater value replaces the current best (NaN never does)
            Some((_, bv)) if v.partial_cmp(bv) != Some(std::cmp::Ordering::Greater) => {}
            _ => best = Some((k.clone(), *v)),
        }
    }
    best
}

/// `timedelta(milliseconds=ms)`: rounded to whole microseconds (round-half-even), like CPython.
pub fn timedelta_ms(ms: f64) -> TimeDelta {
    TimeDelta::microseconds((ms * 1000.0).round_ties_even() as i64)
}

/// One stage of the multi-stage retrieval pipeline.
#[derive(Clone, Debug)]
pub struct RetrievalStageConfig {
    pub name: String,
    pub max_candidates: usize,
    pub edge_types: Vec<EdgeType>,
    pub layer_filter: Option<Vec<NodeLayer>>,
    pub max_hops: usize,
    pub score_threshold: f64,
    pub use_vector_search: bool,
}

impl RetrievalStageConfig {
    pub fn to_dict(&self) -> Value {
        json!({
            "name": self.name,
            "max_candidates": self.max_candidates,
            "edge_types": self.edge_types.iter().map(|e| e.value()).collect::<Vec<_>>(),
            "layer_filter": self.layer_filter.as_ref().filter(|l| !l.is_empty()).map(|l| l.iter().map(|x| x.value()).collect::<Vec<_>>()),
            "max_hops": self.max_hops,
            "score_threshold": self.score_threshold,
            "use_vector_search": self.use_vector_search,
        })
    }
}

/// Result of a neural retrieval.
#[derive(Clone, Debug)]
pub struct RetrievalResult {
    pub query_text: String,
    pub nodes: Vec<(NodeRef, f64)>,
    pub stages_executed: Vec<String>,
    pub total_candidates_seen: usize,
    pub co_activations_recorded: usize,
    pub total_time_ms: f64,
}

impl RetrievalResult {
    pub fn to_dict(&self) -> Value {
        json!({
            "query_text": self.query_text,
            "nodes": self.nodes.iter().map(|(n, s)| json!([n.borrow().to_dict(), s])).collect::<Vec<_>>(),
            "stages_executed": self.stages_executed,
            "total_candidates_seen": self.total_candidates_seen,
            "co_activations_recorded": self.co_activations_recorded,
            "total_time_ms": self.total_time_ms,
        })
    }
}

/// Result of a consolidation cycle.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ConsolidationResult {
    pub session_key: String,
    pub edges_pruned: i64,
    pub nodes_merged: i64,
    pub hubs_created: i64,
    pub nodes_promoted: i64,
    pub nodes_evicted: i64,
    pub total_time_ms: f64,
}

impl ConsolidationResult {
    pub fn to_dict(&self) -> Value {
        json!({
            "session_key": self.session_key,
            "edges_pruned": self.edges_pruned,
            "nodes_merged": self.nodes_merged,
            "hubs_created": self.hubs_created,
            "nodes_promoted": self.nodes_promoted,
            "nodes_evicted": self.nodes_evicted,
            "total_time_ms": self.total_time_ms,
        })
    }
}

thread_local! {
    static DETERMINISTIC_UUIDS: std::cell::Cell<Option<u64>> = const { std::cell::Cell::new(None) };
}

/// Make every [`uuid4`] on this thread a counter, `00000000-0000-4000-0000-000000000001` onwards (`Some(0)`),
/// or random again (`None`). The golden generators patch Python's `uuid.uuid4` the same way
/// (`_reference.ordered_ids`), one counter shared by every module.
pub fn set_deterministic_uuids(start: Option<u64>) {
    DETERMINISTIC_UUIDS.with(|c| c.set(start));
}

/// `str(uuid.uuid4())` — every module that minted a uuid goes through here.
pub fn uuid4() -> String {
    let next = DETERMINISTIC_UUIDS.with(|c| {
        c.get().map(|n| {
            c.set(Some(n + 1));
            n + 1
        })
    });
    match next {
        Some(n) => format!("00000000-0000-4000-0000-{n:012x}"),
        None => uuid::Uuid::new_v4().to_string(),
    }
}

/// `str(uuid.uuid4())`.
pub fn generate_node_id() -> String {
    uuid4()
}

/// `str(uuid.uuid4())`.
pub fn generate_edge_id() -> String {
    uuid4()
}

/// Cosine similarity in float64, summed sequentially like the Python generator expressions.
pub fn cosine_similarity(a: &[f64], b: &[f64]) -> f64 {
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return 0.0;
    }
    let mut dot = 0.0;
    for (x, y) in a.iter().zip(b) {
        dot += x * y;
    }
    let mut na = 0.0;
    for x in a {
        na += x * x;
    }
    let mut nb = 0.0;
    for y in b {
        nb += y * y;
    }
    let (na, nb) = (na.sqrt(), nb.sqrt());
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    dot / (na * nb)
}

/// Sigmoid with steepness and threshold.
pub fn sigmoid(x: f64, steepness: f64, threshold: f64) -> f64 {
    1.0 / (1.0 + (-steepness * (x - threshold)).exp())
}

/// ReLU with threshold.
pub fn relu(x: f64, threshold: f64) -> f64 {
    f64::max(0.0, x - threshold)
}

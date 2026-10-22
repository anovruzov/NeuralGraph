//! Electron-flow retrieval with "Tesla resonance" (port of `retrieval/electron.py`).
//!
//! A query enters the graph as a burst of [`Electron`]s at the vector-search seeds. Each step every electron
//! follows the (at most 8) strongest gated outgoing edges, its energy scaled by edge weight, gate value and the
//! target's fairness weight, its wave signature blended 30% with the node it leaves; arriving energy is scaled
//! by the resonance between the electron's signature and the node's wave amplitudes and accumulates as
//! excitatory or inhibitory charge. Nodes whose net charge crosses the threshold fire (boosted by phase
//! coherence) and go refractory. The fired nodes, then the remaining charged ones, are the result.
//! Simulation time is a step counter (1 ms per step); the only wall-clock reads are the edges' recency inside
//! the gates and `effective_weight`.
//!
//! Python behaviour kept:
//! - `ElectronRetriever.retrieve` first tried `from memmachine.common.domain_classifier import SemanticRouter`;
//!   that module is not part of this repository, so the `ImportError` fallback always ran (no context
//!   engineering, no entity filter, no extra stages). Only the fallback is ported; `_apply_entity_boost` is
//!   ported but was reachable only from the missing branch.
//! - `ElectronRetrieverConfig.resonance_amplification` / `resonance_dampening` were never read:
//!   [`calculate_resonance`] uses the module constants.
//! - An electron injected without a wave signature (`query_wave_amplitudes=None` given straight to the pool)
//!   carries `None`; propagating it raised `AttributeError` (`None.copy()` / `None.keys()`).
//! - Wave dimensions are unions of Python `set`s, iterated query/electron keys first, then the node's (the order
//!   the fixture generator forces), so the float sums are reproducible.

use crate::retrieval::data_types::{uuid4, NeuralEdge, NodeRef, RetrievalResult};
use crate::retrieval::dialogue_linker::DialogueLinker;
use crate::retrieval::gating::{EdgeGateRegistry, GateConfig, GateContext};
use crate::retrieval::interference::py_mod;
use crate::retrieval::pytext::py_max;
use crate::retrieval::retriever::{sort_desc, RetrieverError, WaveAmplitudes};
use crate::retrieval::storage::NeuralGraphStorage;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::py_prefix;
use serde_json::json;
use std::f64::consts::PI;
use std::rc::Rc;
use std::time::Instant;

/// Hippocampal theta rhythm.
pub const THETA_FREQUENCY_HZ: f64 = 6.0;
pub const THETA_PERIOD_MS: f64 = 1000.0 / THETA_FREQUENCY_HZ;

/// Natural frequency of each wave dimension, in declaration order.
pub const DIMENSION_BASE_FREQUENCIES: [(&str, f64); 9] = [
    ("temporal", 7.83),
    ("entity", 14.1),
    ("relational", 21.0),
    ("action", 30.0),
    ("state", 40.0),
    ("spatial", 10.0),
    ("causal", 25.0),
    ("emotional", 6.0),
    ("quantitative", 12.0),
];

/// Energy factor at perfect resonance.
pub const RESONANCE_AMPLIFICATION: f64 = 3.0;
/// Energy factor at perfect anti-resonance.
pub const RESONANCE_DAMPENING: f64 = 0.3;

/// `DIMENSION_BASE_FREQUENCIES.get(dim, 7.83)`.
pub fn dimension_base_frequency(dim: &str) -> f64 {
    DIMENSION_BASE_FREQUENCIES.iter().find(|(d, _)| *d == dim).map(|(_, f)| *f).unwrap_or(7.83)
}

/// Resonance factor between an electron's signature and a node's amplitudes: 1.0 when either is empty or
/// negligible, otherwise `1 + (A - 1) * cos²` for a positive cosine and `1 + (D - 1) * cos²` for a negative one.
pub fn calculate_resonance(electron_signature: Option<&WaveAmplitudes>, node_amplitudes: &WaveAmplitudes) -> f64 {
    let Some(sig) = electron_signature.filter(|s| !s.is_empty()) else { return 1.0 };
    if node_amplitudes.is_empty() {
        return 1.0;
    }
    let (mut dot_product, mut electron_magnitude, mut node_magnitude) = (0.0, 0.0, 0.0);
    let all_dims: IndexSet<&String> = sig.keys().chain(node_amplitudes.keys()).collect();
    for dim in all_dims {
        let e_val = sig.get(dim).copied().unwrap_or(0.0);
        let n_val = node_amplitudes.get(dim).copied().unwrap_or(0.0);
        dot_product += e_val * n_val;
        electron_magnitude += e_val * e_val;
        node_magnitude += n_val * n_val;
    }
    let electron_magnitude = electron_magnitude.sqrt();
    let node_magnitude = node_magnitude.sqrt();
    if electron_magnitude < 0.001 || node_magnitude < 0.001 {
        return 1.0;
    }
    let cosine_sim = dot_product / (electron_magnitude * node_magnitude);
    if cosine_sim >= 0.0 {
        1.0 + (RESONANCE_AMPLIFICATION - 1.0) * cosine_sim.powf(2.0)
    } else {
        1.0 + (RESONANCE_DAMPENING - 1.0) * cosine_sim.powf(2.0)
    }
}

/// `f"e-{uuid.uuid4().hex[:12]}"`.
pub fn generate_electron_id() -> String {
    format!("e-{}", py_prefix(&uuid4().replace('-', ""), 12))
}

// =============================================================================================================
// Electron
// =============================================================================================================

/// The charge carrier.
#[derive(Clone, Debug, PartialEq)]
pub struct Electron {
    pub electron_id: String,
    /// +1 excitatory, -1 inhibitory.
    pub charge: f64,
    pub energy: f64,
    /// Carrier frequency in Hz.
    pub frequency: f64,
    /// Theta phase in [0, 2π).
    pub phase: f64,
    pub source_node_id: String,
    pub current_node_id: String,
    pub birth_time_ms: f64,
    pub arrival_time_ms: f64,
    pub hop_count: i64,
    pub path: Vec<String>,
    /// `None` only for electrons injected without a wave signature.
    pub wave_signature: Option<WaveAmplitudes>,
    pub decay_factor: f64,
}

impl Default for Electron {
    /// `Electron()`: every field at its default (a fresh id).
    fn default() -> Self {
        Electron {
            electron_id: generate_electron_id(),
            charge: 1.0,
            energy: 1.0,
            frequency: 7.83,
            phase: 0.0,
            source_node_id: String::new(),
            current_node_id: String::new(),
            birth_time_ms: 0.0,
            arrival_time_ms: 0.0,
            hop_count: 0,
            path: Vec::new(),
            wave_signature: Some(WaveAmplitudes::new()),
            decay_factor: 0.95,
        }
    }
}

impl Electron {
    /// The electron at the edge's target: energy scaled by `|weight| * decay`, charge by the edge sign, phase
    /// advanced by the conduction delay, signature blended with the node being left (Python default blend 0.3).
    pub fn propagate(
        &self,
        edge: &mut NeuralEdge,
        source_node_wave_amplitudes: Option<&WaveAmplitudes>,
        wave_blend_factor: f64,
    ) -> Result<Electron, RetrieverError> {
        let new_energy = self.energy * edge.effective_weight().abs() * self.decay_factor;
        let new_charge = self.charge * edge.sign;
        let phase_advance = (edge.propagation_delay_ms / THETA_PERIOD_MS) * 2.0 * PI;
        let new_phase = py_mod(self.phase + phase_advance, 2.0 * PI);
        let new_wave_signature = match (source_node_wave_amplitudes.filter(|w| !w.is_empty()), &self.wave_signature) {
            (Some(node), Some(sig)) => Self::blend_wave_signatures(sig, node, wave_blend_factor),
            (None, Some(sig)) => sig.clone(),
            (Some(_), None) => {
                return Err(RetrieverError::AttributeError("'NoneType' object has no attribute 'keys'".into()))
            }
            (None, None) => {
                return Err(RetrieverError::AttributeError("'NoneType' object has no attribute 'copy'".into()))
            }
        };
        let mut path = self.path.clone();
        path.push(edge.target_id.clone());
        Ok(Electron {
            electron_id: generate_electron_id(),
            charge: new_charge,
            energy: new_energy,
            frequency: self.frequency,
            phase: new_phase,
            source_node_id: self.source_node_id.clone(),
            current_node_id: edge.target_id.clone(),
            birth_time_ms: self.birth_time_ms,
            arrival_time_ms: self.arrival_time_ms + edge.propagation_delay_ms,
            hop_count: self.hop_count + 1,
            path,
            wave_signature: Some(new_wave_signature),
            decay_factor: self.decay_factor,
        })
    }

    /// `(1 - blend) * electron + blend * node` over the union of dimensions.
    pub fn blend_wave_signatures(
        electron_sig: &WaveAmplitudes,
        node_sig: &WaveAmplitudes,
        blend_factor: f64,
    ) -> WaveAmplitudes {
        let all_dims: IndexSet<&String> = electron_sig.keys().chain(node_sig.keys()).collect();
        all_dims
            .into_iter()
            .map(|dim| {
                let e_val = electron_sig.get(dim).copied().unwrap_or(0.0);
                let n_val = node_sig.get(dim).copied().unwrap_or(0.0);
                (dim.clone(), (1.0 - blend_factor) * e_val + blend_factor * n_val)
            })
            .collect()
    }

    /// Combined energy of two electrons after phase/charge interference (never negative).
    pub fn interfere_with(&self, other: &Electron) -> f64 {
        let phase_diff = py_mod((self.phase - other.phase).abs(), 2.0 * PI);
        let interference_factor = phase_diff.cos();
        let charge_alignment = self.charge * other.charge;
        let combined = (self.energy + other.energy) * (1.0 + 0.5 * interference_factor * charge_alignment);
        py_max(0.0, combined)
    }

    pub fn is_excitatory(&self) -> bool {
        self.charge > 0.0
    }

    pub fn is_inhibitory(&self) -> bool {
        self.charge < 0.0
    }

    pub fn effective_charge(&self) -> f64 {
        self.charge * self.energy
    }
}

// =============================================================================================================
// Accumulated charge
// =============================================================================================================

/// Charge summed at one node.
#[derive(Clone, Debug, PartialEq)]
pub struct AccumulatedCharge {
    pub node_id: String,
    pub excitatory_charge: f64,
    pub inhibitory_charge: f64,
    pub electron_count: i64,
    pub last_arrival_ms: f64,
    /// Running mean of `cos(phase)` of the arrivals.
    pub phase_coherence: f64,
}

impl AccumulatedCharge {
    pub fn new(node_id: impl Into<String>) -> Self {
        AccumulatedCharge {
            node_id: node_id.into(),
            excitatory_charge: 0.0,
            inhibitory_charge: 0.0,
            electron_count: 0,
            last_arrival_ms: 0.0,
            phase_coherence: 0.0,
        }
    }

    pub fn net_charge(&self) -> f64 {
        self.excitatory_charge - self.inhibitory_charge.abs()
    }

    pub fn total_energy(&self) -> f64 {
        self.excitatory_charge + self.inhibitory_charge.abs()
    }

    /// Python default threshold 0.5.
    pub fn exceeds_threshold(&self, threshold: f64) -> bool {
        self.net_charge() >= threshold
    }
}

// =============================================================================================================
// Electron pool
// =============================================================================================================

/// The circuit: electrons in flight, charge per node, refractory periods, firing record.
pub struct ElectronPool {
    storage: Rc<dyn NeuralGraphStorage>,
    theta_period_ms: f64,
    firing_threshold: f64,
    refractory_ms: f64,
    max_hops: i64,
    energy_floor: f64,
    electrons: Vec<Electron>,
    node_charge: IndexMap<String, AccumulatedCharge>,
    node_wave_amplitudes: IndexMap<String, WaveAmplitudes>,
    refractory_until: IndexMap<String, f64>,
    current_time_ms: f64,
    fired_nodes: Vec<(String, f64, f64)>,
    gate_registry: EdgeGateRegistry,
    query_wave_amplitudes: WaveAmplitudes,
}

impl ElectronPool {
    /// Python defaults: 6 Hz, threshold 0.5, refractory 2 ms, 5 hops, energy floor 0.1.
    pub fn new(
        storage: Rc<dyn NeuralGraphStorage>,
        theta_frequency_hz: f64,
        firing_threshold: f64,
        refractory_ms: f64,
        max_hops: i64,
        energy_floor: f64,
    ) -> Self {
        ElectronPool {
            storage,
            theta_period_ms: 1000.0 / theta_frequency_hz,
            firing_threshold,
            refractory_ms,
            max_hops,
            energy_floor,
            electrons: Vec::new(),
            node_charge: IndexMap::new(),
            node_wave_amplitudes: IndexMap::new(),
            refractory_until: IndexMap::new(),
            current_time_ms: 0.0,
            fired_nodes: Vec::new(),
            gate_registry: EdgeGateRegistry::new(Some(GateConfig {
                threshold: 0.15,
                steepness: 3.0,
                recency_half_life_days: 7.0,
                ltp_weight: 0.3,
                confidence_weight: 0.3,
            })),
            query_wave_amplitudes: WaveAmplitudes::new(),
        }
    }

    /// The pool with Python's defaults.
    pub fn with_defaults(storage: Rc<dyn NeuralGraphStorage>) -> Self {
        Self::new(storage, 6.0, 0.5, 2.0, 5, 0.1)
    }

    pub fn theta_period_ms(&self) -> f64 {
        self.theta_period_ms
    }

    pub fn electrons(&self) -> &[Electron] {
        &self.electrons
    }

    pub fn node_charge(&self) -> &IndexMap<String, AccumulatedCharge> {
        &self.node_charge
    }

    pub fn node_wave_amplitudes(&self) -> &IndexMap<String, WaveAmplitudes> {
        &self.node_wave_amplitudes
    }

    pub fn refractory_until(&self) -> &IndexMap<String, f64> {
        &self.refractory_until
    }

    pub fn current_time_ms(&self) -> f64 {
        self.current_time_ms
    }

    /// `(node_id, time, charge)` of every firing so far.
    pub fn fired_nodes(&self) -> &[(String, f64, f64)] {
        &self.fired_nodes
    }

    pub fn query_wave_amplitudes(&self) -> &WaveAmplitudes {
        &self.query_wave_amplitudes
    }

    /// Inject one excitatory electron per non-refractory seed, carrying the query's wave signature at the
    /// frequency of its dominant dimension, and charge the seed (Python default energy 1.0).
    pub fn inject_query_burst(
        &mut self,
        query_wave_amplitudes: Option<&WaveAmplitudes>,
        seed_node_ids: &[String],
        initial_energy: f64,
    ) -> Vec<Electron> {
        self.query_wave_amplitudes = query_wave_amplitudes.cloned().unwrap_or_default();
        let mut dominant_frequency = 7.83;
        if let Some(w) = query_wave_amplitudes.filter(|w| !w.is_empty()) {
            let mut best: Option<(&String, f64)> = None;
            for (k, v) in w {
                if best.is_none_or(|(_, b)| *v > b) {
                    best = Some((k, *v));
                }
            }
            dominant_frequency = dimension_base_frequency(best.map(|(k, _)| k.as_str()).unwrap_or("temporal"));
        }
        let mut injected = Vec::new();
        for node_id in seed_node_ids {
            if self.is_refractory(node_id) {
                continue;
            }
            let electron = Electron {
                charge: 1.0,
                energy: initial_energy,
                frequency: dominant_frequency,
                phase: 0.0,
                source_node_id: node_id.clone(),
                current_node_id: node_id.clone(),
                birth_time_ms: self.current_time_ms,
                arrival_time_ms: self.current_time_ms,
                path: vec![node_id.clone()],
                wave_signature: query_wave_amplitudes.cloned(),
                ..Electron::default()
            };
            self.electrons.push(electron.clone());
            self.accumulate_charge_with_resonance(&electron);
            injected.push(electron);
        }
        injected
    }

    /// The node's wave amplitudes, fetched once and cached (`{}` for unknown nodes).
    fn cached_waves(&mut self, node_id: &str) -> WaveAmplitudes {
        if let Some(w) = self.node_wave_amplitudes.get(node_id) {
            return w.clone();
        }
        let w = self.storage.get_node(node_id).map(|n| n.borrow().wave_amplitudes.clone()).unwrap_or_default();
        self.node_wave_amplitudes.insert(node_id.to_string(), w.clone());
        w
    }

    /// One step: every live electron (energy at least the floor, fewer than `max_hops` hops, not on a
    /// refractory node) propagates through its 8 highest-gated outgoing edges (gate > 0.05), energy scaled by
    /// gate x target fairness; the survivors charge their targets. Then nodes fire and time advances 1 ms.
    /// Returns the number of new electrons.
    pub fn propagate_step(&mut self) -> Result<usize, RetrieverError> {
        let mut new_electrons: Vec<Electron> = Vec::new();
        let electrons = std::mem::take(&mut self.electrons);
        for electron in &electrons {
            if electron.energy < self.energy_floor
                || electron.hop_count >= self.max_hops
                || self.is_refractory(&electron.current_node_id)
            {
                continue;
            }
            let edges = self.storage.get_edges_from(&electron.current_node_id, None);
            let source_wave_amps = self.cached_waves(&electron.current_node_id);

            let mut gate_context = GateContext::new();
            gate_context.insert("query_wave".into(), json!(self.query_wave_amplitudes));
            gate_context.insert("current_time_ms".into(), json!(self.current_time_ms));
            gate_context.insert("electron_energy".into(), json!(electron.energy));
            gate_context.insert("hop_count".into(), json!(electron.hop_count));
            let mut gated_edges = Vec::new();
            for edge in edges {
                let gate_value = self.gate_registry.compute_gate(&mut edge.borrow_mut(), &gate_context);
                if gate_value > 0.05 {
                    gated_edges.push((edge, gate_value));
                }
            }
            sort_desc(&mut gated_edges);
            for (edge, gate_value) in gated_edges.into_iter().take(8) {
                let target_id = edge.borrow().target_id.clone();
                let fairness_weight = match self.storage.get_node(&target_id) {
                    Some(t) => t.borrow().get_fairness_weight(&self.query_wave_amplitudes),
                    None => 1.0,
                };
                let combined_weight = gate_value * fairness_weight;
                let mut new_electron = electron.propagate(&mut edge.borrow_mut(), Some(&source_wave_amps), 0.3)?;
                new_electron.energy *= combined_weight;
                if new_electron.energy >= self.energy_floor {
                    self.accumulate_charge_with_resonance(&new_electron);
                    new_electrons.push(new_electron);
                }
            }
        }
        let created = new_electrons.len();
        self.electrons = new_electrons;
        self.check_firing_thresholds();
        self.current_time_ms += 1.0;
        Ok(created)
    }

    /// Step until no electron is left or `max_steps` (Python default 50) ran; then the total firing charge per
    /// node, highest first.
    pub fn propagate_until_settled(&mut self, max_steps: usize) -> Result<Vec<(String, f64)>, RetrieverError> {
        for _ in 0..max_steps {
            self.propagate_step()?;
            if self.electrons.is_empty() {
                break;
            }
        }
        let mut aggregated: IndexMap<String, f64> = IndexMap::new();
        for (node_id, _, charge) in &self.fired_nodes {
            *aggregated.entry(node_id.clone()).or_insert(0.0) += charge;
        }
        let mut out: Vec<(String, f64)> = aggregated.into_iter().collect();
        sort_desc(&mut out);
        Ok(out)
    }

    /// Add the electron's energy, scaled by its resonance with the node, to the node's charge and update the
    /// phase coherence.
    pub fn accumulate_charge_with_resonance(&mut self, electron: &Electron) {
        let node_id = electron.current_node_id.clone();
        self.node_charge.entry(node_id.clone()).or_insert_with(|| AccumulatedCharge::new(node_id.clone()));
        let node_amplitudes = self.cached_waves(&node_id);
        let resonance_factor = calculate_resonance(electron.wave_signature.as_ref(), &node_amplitudes);
        let resonant_energy = electron.energy * resonance_factor;
        let acc = self.node_charge.get_mut(&node_id).expect("inserted above");
        if electron.is_excitatory() {
            acc.excitatory_charge += resonant_energy;
        } else {
            acc.inhibitory_charge += resonant_energy.abs();
        }
        acc.electron_count += 1;
        acc.last_arrival_ms = electron.arrival_time_ms;
        if acc.electron_count > 1 {
            acc.phase_coherence = (acc.phase_coherence * (acc.electron_count - 1) as f64 + electron.phase.cos())
                / acc.electron_count as f64;
        } else {
            acc.phase_coherence = electron.phase.cos();
        }
    }

    /// `_accumulate_charge`, the legacy version without resonance or coherence (unused by the Python).
    pub fn accumulate_charge(&mut self, electron: &Electron) {
        let node_id = electron.current_node_id.clone();
        let acc = self.node_charge.entry(node_id.clone()).or_insert_with(|| AccumulatedCharge::new(node_id));
        if electron.is_excitatory() {
            acc.excitatory_charge += electron.energy;
        } else {
            acc.inhibitory_charge += electron.energy.abs();
        }
        acc.electron_count += 1;
        acc.last_arrival_ms = electron.arrival_time_ms;
    }

    /// Fire every non-refractory node at or above the threshold, its net charge scaled by
    /// `1 + 0.2 * phase_coherence`.
    fn check_firing_thresholds(&mut self) {
        let mut nodes_to_fire = Vec::new();
        for (node_id, acc) in &self.node_charge {
            if acc.exceeds_threshold(self.firing_threshold) && !self.is_refractory(node_id) {
                let coherence_boost = 1.0 + 0.2 * acc.phase_coherence;
                nodes_to_fire.push((node_id.clone(), acc.net_charge() * coherence_boost));
            }
        }
        for (node_id, charge) in nodes_to_fire {
            self.fire_node(&node_id, charge);
        }
    }

    /// Record the firing, start the refractory period, reset the node's charge.
    fn fire_node(&mut self, node_id: &str, charge: f64) {
        self.fired_nodes.push((node_id.to_string(), self.current_time_ms, charge));
        self.refractory_until.insert(node_id.to_string(), self.current_time_ms + self.refractory_ms);
        self.node_charge.insert(node_id.to_string(), AccumulatedCharge::new(node_id));
    }

    pub fn is_refractory(&self, node_id: &str) -> bool {
        self.current_time_ms < self.refractory_until.get(node_id).copied().unwrap_or(0.0)
    }

    /// Nodes with positive net charge, highest first.
    pub fn get_accumulated_charges(&self) -> Vec<(String, f64)> {
        let mut result: Vec<(String, f64)> = self
            .node_charge
            .iter()
            .filter(|(_, acc)| acc.net_charge() > 0.0)
            .map(|(id, acc)| (id.clone(), acc.net_charge()))
            .collect();
        sort_desc(&mut result);
        result
    }

    /// Clear everything for a new query.
    pub fn reset(&mut self) {
        self.electrons.clear();
        self.node_charge.clear();
        self.node_wave_amplitudes.clear();
        self.refractory_until.clear();
        self.fired_nodes.clear();
        self.current_time_ms = 0.0;
    }
}

// =============================================================================================================
// Electron retriever
// =============================================================================================================

/// Configuration for electron-based retrieval.
#[derive(Clone, Debug, PartialEq)]
pub struct ElectronRetrieverConfig {
    pub theta_frequency_hz: f64,
    pub firing_threshold: f64,
    pub refractory_ms: f64,
    pub max_hops: i64,
    pub energy_floor: f64,
    pub seed_count: usize,
    pub initial_energy: f64,
    pub max_propagation_steps: usize,
    /// Never read (see the module docs).
    pub resonance_amplification: f64,
    /// Never read (see the module docs).
    pub resonance_dampening: f64,
    pub final_limit: usize,
}

impl Default for ElectronRetrieverConfig {
    fn default() -> Self {
        ElectronRetrieverConfig {
            theta_frequency_hz: 6.0,
            firing_threshold: 0.5,
            refractory_ms: 2.0,
            max_hops: 5,
            energy_floor: 0.1,
            seed_count: 20,
            initial_energy: 1.0,
            max_propagation_steps: 50,
            resonance_amplification: 3.0,
            resonance_dampening: 0.3,
            final_limit: 20,
        }
    }
}

/// Retrieval by electron flow.
pub struct ElectronRetriever {
    storage: Rc<dyn NeuralGraphStorage>,
    config: ElectronRetrieverConfig,
    pool: ElectronPool,
}

impl ElectronRetriever {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<ElectronRetrieverConfig>) -> Self {
        let config = config.unwrap_or_default();
        let pool = ElectronPool::new(
            storage.clone(),
            config.theta_frequency_hz,
            config.firing_threshold,
            config.refractory_ms,
            config.max_hops,
            config.energy_floor,
        );
        ElectronRetriever { storage, config, pool }
    }

    pub fn config(&self) -> &ElectronRetrieverConfig {
        &self.config
    }

    /// The pool, as the last retrieval left it.
    pub fn pool(&self) -> &ElectronPool {
        &self.pool
    }

    /// Seed by vector search, inject, propagate until settled; the fired nodes (topped up with charged ones
    /// up to `2 * limit`) ranked by charge (`limit` `None`/0: `config.final_limit`).
    pub fn retrieve(
        &mut self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: Option<usize>,
        query_wave_amplitudes: Option<&WaveAmplitudes>,
    ) -> Result<RetrievalResult, RetrieverError> {
        let start = Instant::now();
        let final_limit = limit.filter(|l| *l != 0).unwrap_or(self.config.final_limit);
        // the `ImportError` fallback: memmachine's SemanticRouter does not exist here
        let context_amplitudes: WaveAmplitudes = query_wave_amplitudes.cloned().unwrap_or_default();
        let entity_filter: Vec<String> = Vec::new();
        let mut stages: Vec<String> = Vec::new();

        self.pool.reset();
        let seeds = self.storage.vector_search(query_embedding, session_key, self.config.seed_count, None);
        let seed_ids: Vec<String> = seeds.iter().map(|(n, _)| n.borrow().node_id.clone()).collect();
        if seed_ids.is_empty() {
            stages.push("no_seeds".into());
            return Ok(RetrievalResult {
                query_text: query_text.to_string(),
                nodes: vec![],
                stages_executed: stages,
                total_candidates_seen: 0,
                co_activations_recorded: 0,
                total_time_ms: 0.0,
            });
        }
        stages.push("seed_selection".into());
        self.pool.inject_query_burst(Some(&context_amplitudes), &seed_ids, self.config.initial_energy);
        stages.push("electron_injection".into());
        let mut fired_nodes = self.pool.propagate_until_settled(self.config.max_propagation_steps)?;
        stages.extend(["tesla_resonance", "electron_propagation", "threshold_detection"].map(String::from));

        if fired_nodes.len() < final_limit {
            let accumulated = self.pool.get_accumulated_charges();
            let existing_ids: IndexSet<String> = fired_nodes.iter().map(|(id, _)| id.clone()).collect();
            for (node_id, charge) in accumulated {
                if !existing_ids.contains(&node_id) && fired_nodes.len() < final_limit * 2 {
                    fired_nodes.push((node_id, charge));
                }
            }
        }
        if !entity_filter.is_empty() {
            let shown: Vec<&str> = entity_filter.iter().take(3).map(String::as_str).collect();
            stages.push(format!("entity_filter:{}", shown.join(",")));
            fired_nodes = self.apply_entity_boost(&fired_nodes, &entity_filter);
        }
        let mut results: Vec<(NodeRef, f64)> = Vec::new();
        for (node_id, charge) in fired_nodes.iter().take(final_limit) {
            if let Some(node) = self.storage.get_node(node_id) {
                results.push((node, *charge));
            }
        }
        Ok(RetrievalResult {
            query_text: query_text.to_string(),
            nodes: results,
            stages_executed: stages,
            total_candidates_seen: fired_nodes.len(),
            co_activations_recorded: 0,
            total_time_ms: start.elapsed().as_secs_f64() * 1000.0,
        })
    }

    /// Double the charge of nodes whose content mentions one of the entities (case-insensitive); re-rank.
    pub fn apply_entity_boost(&self, fired_nodes: &[(String, f64)], entity_filter: &[String]) -> Vec<(String, f64)> {
        const ENTITY_BOOST: f64 = 2.0;
        let entity_lower: Vec<String> = entity_filter.iter().map(|e| e.to_lowercase()).collect();
        let mut boosted: Vec<(String, f64)> = fired_nodes
            .iter()
            .map(|(node_id, charge)| {
                let matched = self.storage.get_node(node_id).is_some_and(|n| {
                    let content_lower = n.borrow().content.to_lowercase();
                    entity_lower.iter().any(|e| content_lower.contains(e.as_str()))
                });
                (node_id.clone(), if matched { charge * ENTITY_BOOST } else { *charge })
            })
            .collect();
        sort_desc(&mut boosted);
        boosted
    }

    /// Add every message the dialogue linker ties to a fired one, at `charge * link_strength * 0.7` (keeping
    /// the larger value), re-ranked. Without a linker the input comes back unchanged.
    pub fn expand_with_dialogue_links(
        &self,
        fired_nodes: &[(String, f64)],
        dialogue_linker: Option<&DialogueLinker>,
    ) -> Vec<(String, f64)> {
        let Some(linker) = dialogue_linker else { return fired_nodes.to_vec() };
        const LINK_BOOST: f64 = 0.7;
        let mut expanded: IndexMap<String, f64> = IndexMap::new();
        for (node_id, charge) in fired_nodes {
            expanded.insert(node_id.clone(), *charge);
        }
        for (node_id, charge) in fired_nodes {
            for (linked_id, link_strength, _link_type) in linker.get_linked_messages(node_id) {
                let derived_charge = charge * link_strength * LINK_BOOST;
                if expanded.get(&linked_id).is_none_or(|old| *old < derived_charge) {
                    expanded.insert(linked_id, derived_charge);
                }
            }
        }
        let mut result: Vec<(String, f64)> = expanded.into_iter().collect();
        sort_desc(&mut result);
        result
    }
}

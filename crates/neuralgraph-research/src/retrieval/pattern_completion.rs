//! CA3-style pattern completion and theta-gamma slot organisation (port of `retrieval/pattern_completion.py`).
//!
//! Seeds (a partial cue) spread activation through the session's learned connections (SEMANTIC and ENTITY
//! edges, both directions) and, for unconnected pairs, through embedding similarity, for a few "gamma cycles"
//! with decay and top-k sparsity, until the activation change falls below a threshold.
//!
//! numpy: the only numpy arithmetic is `_compute_connection_weight`, a float64 cosine
//! `np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))`. `np.linalg.norm` of a 1-D float64 array is
//! `sqrt(x.dot(x))`, and float64 `dot` is BLAS `ddot` (Accelerate on macOS), so [`crate::numpy::ddot`] and
//! [`crate::numpy::norm_f64`] reproduce it bit for bit there (pinned by the `pattern_completion` fixture).
//!
//! `_compute_activation_delta` summed over `set(old) | set(new)`, an order that changed with Python's hash seed;
//! here it is old keys then new ones (insertion order). The delta only decides convergence against a threshold.

use crate::numpy::{ddot, norm_f64};
use crate::retrieval::data_types::{EdgeType, NodeRef};
use crate::retrieval::storage::NeuralGraphStorage;
use indexmap::{IndexMap, IndexSet};
use std::rc::Rc;

fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `min(1.0, max(0.0, x))`.
fn clamp01(x: f64) -> f64 {
    py_min(1.0, py_max(0.0, x))
}

/// Configuration for CA3-style pattern completion.
#[derive(Clone, Debug, PartialEq)]
pub struct PatternCompletionConfig {
    /// Gamma cycles.
    pub max_iterations: usize,
    /// Stop when the mean activation change falls below this.
    pub convergence_threshold: f64,
    /// Embedding similarity above which two nodes count as connected.
    pub connectivity_radius: f64,
    pub activation_decay: f64,
    pub spreading_factor: f64,
    /// Keep the top-k most active nodes per iteration.
    pub sparsity_k: usize,
    /// Compute the Hopfield energy of the final state.
    pub use_energy_function: bool,
}

impl Default for PatternCompletionConfig {
    fn default() -> Self {
        PatternCompletionConfig {
            max_iterations: 3,
            convergence_threshold: 0.01,
            connectivity_radius: 0.3,
            activation_decay: 0.8,
            spreading_factor: 0.5,
            sparsity_k: 20,
            use_energy_function: true,
        }
    }
}

/// Activation state during completion.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ActivationState {
    /// node id -> activation in [0, 1], in insertion order.
    pub activations: IndexMap<String, f64>,
    /// Hopfield energy (lower = more stable).
    pub energy: f64,
    pub iteration: usize,
    pub converged: bool,
}

/// CA3-inspired attractor dynamics over a session's nodes.
pub struct CA3PatternCompleter {
    storage: Rc<dyn NeuralGraphStorage>,
    config: PatternCompletionConfig,
    embedding_cache: IndexMap<String, Vec<f64>>,
    /// node id -> [(connected node id, weight)] from SEMANTIC/ENTITY edges.
    edge_cache: IndexMap<String, Vec<(String, f64)>>,
    /// The state of the last [`CA3PatternCompleter::complete_pattern`] run (Python only logged it).
    last_state: ActivationState,
}

impl CA3PatternCompleter {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<PatternCompletionConfig>) -> Self {
        CA3PatternCompleter {
            storage,
            config: config.unwrap_or_default(),
            embedding_cache: IndexMap::new(),
            edge_cache: IndexMap::new(),
            last_state: ActivationState::default(),
        }
    }

    pub fn config(&self) -> &PatternCompletionConfig {
        &self.config
    }

    /// Iterations, convergence and energy of the last completion.
    pub fn last_state(&self) -> &ActivationState {
        &self.last_state
    }

    pub fn embedding_cache(&self) -> &IndexMap<String, Vec<f64>> {
        &self.embedding_cache
    }

    pub fn edge_cache(&self) -> &IndexMap<String, Vec<(String, f64)>> {
        &self.edge_cache
    }

    /// Complete a partial pattern: `(node, activation)` pairs above 0.1, most active first. Without
    /// `all_nodes` the session's nodes are fetched; an empty node set returns the seeds unchanged.
    pub fn complete_pattern(
        &mut self,
        seed_nodes: &[(NodeRef, f64)],
        session_key: &str,
        all_nodes: Option<Vec<NodeRef>>,
    ) -> Vec<(NodeRef, f64)> {
        if seed_nodes.is_empty() {
            return Vec::new();
        }
        let all_nodes = all_nodes.unwrap_or_else(|| self.storage.get_nodes_by_session(session_key, None));
        if all_nodes.is_empty() {
            return seed_nodes.to_vec();
        }
        self.build_embedding_cache(&all_nodes);
        self.build_edge_cache(&all_nodes);
        let mut state = self.initialize_state(seed_nodes);

        for iteration in 0..self.config.max_iterations {
            state.iteration = iteration;
            let new_activations = self.spread_activation_hybrid(&state, &all_nodes);
            let delta = self.compute_activation_delta(&state.activations, &new_activations);
            if delta < self.config.convergence_threshold {
                state.converged = true;
                break;
            }
            state.activations = self.enforce_sparsity(new_activations);
        }
        if self.config.use_energy_function {
            state.energy = self.compute_energy(&state.activations, &all_nodes);
        }
        let result = self.build_result(&state, &all_nodes);
        tracing::info!(
            "Pattern completion: {} seeds -> {} nodes ({} iterations, converged={})",
            seed_nodes.len(),
            result.len(),
            state.iteration + 1,
            state.converged
        );
        self.last_state = state;
        result
    }

    /// Cache non-empty embeddings by node id.
    pub fn build_embedding_cache(&mut self, nodes: &[NodeRef]) {
        self.embedding_cache.clear();
        for node in nodes {
            let n = node.borrow();
            if let Some(e) = n.embedding.as_ref().filter(|e| !e.is_empty()) {
                self.embedding_cache.insert(n.node_id.clone(), e.clone());
            }
        }
    }

    /// Connections from SEMANTIC and ENTITY edges (entity weights x1.2): outgoing edges first, then incoming
    /// ones from nodes not already connected, restricted to `nodes`.
    pub fn build_edge_cache(&mut self, nodes: &[NodeRef]) {
        self.edge_cache.clear();
        let node_ids: IndexSet<String> = nodes.iter().map(|n| n.borrow().node_id.clone()).collect();
        for node in nodes {
            let node_id = node.borrow().node_id.clone();
            let mut connections: Vec<(String, f64)> = Vec::new();
            for edge in self.storage.get_edges_from(&node_id, Some(&[EdgeType::Semantic])) {
                let target = edge.borrow().target_id.clone();
                if node_ids.contains(&target) {
                    let w = edge.borrow_mut().effective_weight();
                    connections.push((target, w));
                }
            }
            for edge in self.storage.get_edges_from(&node_id, Some(&[EdgeType::Entity])) {
                let target = edge.borrow().target_id.clone();
                if node_ids.contains(&target) {
                    let w = edge.borrow_mut().effective_weight();
                    connections.push((target, w * 1.2));
                }
            }
            for (edge_type, factor) in [(EdgeType::Semantic, None), (EdgeType::Entity, Some(1.2))] {
                for edge in self.storage.get_edges_to(&node_id, Some(&[edge_type])) {
                    let source = edge.borrow().source_id.clone();
                    if node_ids.contains(&source) && !connections.iter().any(|c| c.0 == source) {
                        let w = edge.borrow_mut().effective_weight();
                        connections.push((source, factor.map_or(w, |f| w * f)));
                    }
                }
            }
            self.edge_cache.insert(node_id, connections);
        }
    }

    /// One gamma cycle over edges (weight 0.7) and, for unconnected pairs, embedding similarity (0.3).
    pub fn spread_activation_hybrid(&self, state: &ActivationState, all_nodes: &[NodeRef]) -> IndexMap<String, f64> {
        let active_nodes: Vec<(&String, f64)> =
            state.activations.iter().filter(|(_, a)| **a > 0.1).map(|(id, a)| (id, *a)).collect();
        if active_nodes.is_empty() {
            return state.activations.clone();
        }
        let empty: Vec<(String, f64)> = Vec::new();
        let mut new_activations = IndexMap::new();
        for node in all_nodes {
            let node_id = node.borrow().node_id.clone();
            let node_emb = self.embedding_cache.get(&node_id);
            let old_activation = state.activations.get(&node_id).copied().unwrap_or(0.0);
            let mut new_act = self.config.activation_decay * old_activation;

            let node_connections = self.edge_cache.get(&node_id).unwrap_or(&empty);
            let connection_targets: IndexSet<&String> = node_connections.iter().map(|c| &c.0).collect();
            let mut edge_spreading = 0.0;
            for (active_id, active_level) in &active_nodes {
                if **active_id == node_id {
                    continue;
                }
                if let Some((_, weight)) = node_connections.iter().find(|(t, _)| t == *active_id) {
                    edge_spreading += weight * active_level;
                }
            }

            let mut embedding_spreading = 0.0;
            if let Some(node_emb) = node_emb {
                for (active_id, active_level) in &active_nodes {
                    if **active_id == node_id || connection_targets.contains(active_id) {
                        continue;
                    }
                    let Some(active_emb) = self.embedding_cache.get(*active_id) else { continue };
                    let weight = self.compute_connection_weight(node_emb, active_emb);
                    if weight > self.config.connectivity_radius {
                        embedding_spreading += weight * active_level;
                    }
                }
            }
            let total_spreading = 0.7 * edge_spreading + 0.3 * embedding_spreading;
            new_act += self.config.spreading_factor * total_spreading;
            new_activations.insert(node_id, clamp01(new_act));
        }
        new_activations
    }

    /// Seed activations clamped to [0, 1].
    pub fn initialize_state(&self, seed_nodes: &[(NodeRef, f64)]) -> ActivationState {
        let mut state = ActivationState::default();
        for (node, score) in seed_nodes {
            state.activations.insert(node.borrow().node_id.clone(), clamp01(*score));
        }
        state
    }

    /// The embedding-only spreading rule (`_spread_activation`). Unused by Python's `complete_pattern` (which
    /// calls the hybrid rule); ported for completeness.
    pub fn spread_activation(&self, state: &ActivationState, all_nodes: &[NodeRef]) -> IndexMap<String, f64> {
        let active_nodes: Vec<(&String, f64)> =
            state.activations.iter().filter(|(_, a)| **a > 0.1).map(|(id, a)| (id, *a)).collect();
        if active_nodes.is_empty() {
            return state.activations.clone();
        }
        let mut new_activations = IndexMap::new();
        for node in all_nodes {
            let node_id = node.borrow().node_id.clone();
            let Some(node_emb) = self.embedding_cache.get(&node_id) else { continue };
            let old_activation = state.activations.get(&node_id).copied().unwrap_or(0.0);
            let mut new_act = self.config.activation_decay * old_activation;
            let mut spreading_sum = 0.0;
            for (active_id, active_level) in &active_nodes {
                if **active_id == node_id {
                    continue;
                }
                let Some(active_emb) = self.embedding_cache.get(*active_id) else { continue };
                let weight = self.compute_connection_weight(node_emb, active_emb);
                if weight > self.config.connectivity_radius {
                    spreading_sum += weight * active_level;
                }
            }
            new_act += self.config.spreading_factor * spreading_sum;
            new_activations.insert(node_id, clamp01(new_act));
        }
        new_activations
    }

    /// Positive cosine similarity, computed exactly as numpy did (float64 BLAS dot and norms).
    pub fn compute_connection_weight(&self, emb1: &[f64], emb2: &[f64]) -> f64 {
        let norm1 = norm_f64(emb1);
        let norm2 = norm_f64(emb2);
        if norm1 == 0.0 || norm2 == 0.0 {
            return 0.0;
        }
        let similarity = ddot(emb1, emb2) / (norm1 * norm2);
        py_max(0.0, similarity)
    }

    /// Mean absolute activation change over the union of ids (old ids first, then new ones).
    pub fn compute_activation_delta(&self, old: &IndexMap<String, f64>, new: &IndexMap<String, f64>) -> f64 {
        let all_ids: IndexSet<&String> = old.keys().chain(new.keys()).collect();
        if all_ids.is_empty() {
            return 0.0;
        }
        let mut total_delta = 0.0;
        for nid in &all_ids {
            let n = new.get(*nid).copied().unwrap_or(0.0);
            let o = old.get(*nid).copied().unwrap_or(0.0);
            total_delta += (n - o).abs();
        }
        total_delta / all_ids.len() as f64
    }

    /// Keep the `sparsity_k` most active nodes (stable for ties).
    pub fn enforce_sparsity(&self, activations: IndexMap<String, f64>) -> IndexMap<String, f64> {
        if activations.len() <= self.config.sparsity_k {
            return activations;
        }
        let mut sorted: Vec<(String, f64)> = activations.into_iter().collect();
        sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        sorted.truncate(self.config.sparsity_k);
        sorted.into_iter().collect()
    }

    /// Hopfield energy `E = -sum_{i<j} w_ij s_i s_j` over the active nodes with embeddings.
    pub fn compute_energy(&self, activations: &IndexMap<String, f64>, _all_nodes: &[NodeRef]) -> f64 {
        let mut energy = 0.0;
        let active_ids: Vec<&String> = activations.keys().collect();
        for (i, id1) in active_ids.iter().enumerate() {
            let Some(emb1) = self.embedding_cache.get(*id1) else { continue };
            let s1 = activations[*id1];
            for id2 in &active_ids[i + 1..] {
                let Some(emb2) = self.embedding_cache.get(*id2) else { continue };
                let s2 = activations[*id2];
                let w = self.compute_connection_weight(emb1, emb2);
                energy -= w * s1 * s2;
            }
        }
        energy
    }

    /// `(node, activation)` for activations above 0.1 whose node is known, most active first.
    pub fn build_result(&self, state: &ActivationState, all_nodes: &[NodeRef]) -> Vec<(NodeRef, f64)> {
        let mut lookup: IndexMap<String, NodeRef> = IndexMap::new();
        for n in all_nodes {
            lookup.insert(n.borrow().node_id.clone(), n.clone());
        }
        let mut result: Vec<(NodeRef, f64)> = Vec::new();
        for (node_id, activation) in &state.activations {
            if *activation > 0.1 {
                if let Some(node) = lookup.get(node_id) {
                    result.push((node.clone(), *activation));
                }
            }
        }
        result.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        result
    }
}

/// Theta-gamma phase coding: retrieved memories grouped into 7±2 gamma slots by dominant wave dimension.
#[derive(Clone, Debug)]
pub struct ThetaGammaOrganizer {
    pub gamma_slots: usize,
}

impl Default for ThetaGammaOrganizer {
    fn default() -> Self {
        ThetaGammaOrganizer { gamma_slots: 7 }
    }
}

impl ThetaGammaOrganizer {
    pub fn new(gamma_slots: usize) -> Self {
        ThetaGammaOrganizer { gamma_slots }
    }

    /// Nodes distributed over the slots. Panics when there are nodes but zero slots (Python raised
    /// `ZeroDivisionError` there).
    pub fn organize_by_phase(
        &self,
        nodes: &[(NodeRef, f64)],
        query_wave_amplitudes: Option<&IndexMap<String, f64>>,
    ) -> Vec<Vec<(NodeRef, f64)>> {
        let mut slots: Vec<Vec<(NodeRef, f64)>> = vec![Vec::new(); self.gamma_slots];
        for (node, score) in nodes {
            let slot_idx = self.compute_slot_assignment(node, query_wave_amplitudes);
            slots[slot_idx].push((node.clone(), *score));
        }
        slots
    }

    /// Slot of the node's dominant wave dimension (first strictly largest amplitude above 0; "temporal" when
    /// none), modulo the slot count.
    pub fn compute_slot_assignment(&self, node: &NodeRef, _query_waves: Option<&IndexMap<String, f64>>) -> usize {
        let n = node.borrow();
        if n.wave_amplitudes.is_empty() {
            return 0;
        }
        let mut max_amp = 0.0;
        let mut dominant_dim = "temporal";
        for (dim, amp) in &n.wave_amplitudes {
            if *amp > max_amp {
                max_amp = *amp;
                dominant_dim = dim;
            }
        }
        let base_slot = match dominant_dim {
            "temporal" => 0,
            "entity" => 1,
            "action" => 2,
            "relational" => 3,
            "state" => 4,
            "spatial" => 5,
            "emotional" => 6,
            _ => 0,
        };
        assert!(self.gamma_slots > 0, "integer modulo by zero (ZeroDivisionError in Python)");
        base_slot % self.gamma_slots
    }

    /// Round-robin over the slots: every slot's first item, then every second item, and so on.
    pub fn flatten_organized(&self, slots: &[Vec<(NodeRef, f64)>]) -> Vec<(NodeRef, f64)> {
        let mut result = Vec::new();
        let max_depth = slots.iter().map(Vec::len).max().unwrap_or(0);
        for depth in 0..max_depth {
            for slot in slots {
                if let Some(item) = slot.get(depth) {
                    result.push(item.clone());
                }
            }
        }
        result
    }
}

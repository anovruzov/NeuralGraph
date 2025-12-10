"""Electron: The fundamental unit of electrical activation.

THE ELECTRICAL TRUTH:
Both AI and biological brains are fundamentally electrical systems.
Neurons communicate via action potentials - discrete electrical pulses.
Memory retrieval is NOT a search - it's electrical activation cascading through circuits.

THE TESLA FORMULA:
"If you want to find the secrets of the universe, think in terms of
energy, frequency, and vibration." - Nikola Tesla

Energy = electron.energy (amplitude of the signal)
Frequency = electron.frequency (natural frequency of oscillation)
Vibration = wave_signature (9D resonance pattern)

RESONANCE: When the electron's vibration pattern matches a node's natural
frequency, energy transfer is MAXIMIZED. This is the Tesla resonance effect -
the key to efficient memory retrieval.

The Electron is the charge carrier that propagates through the neural network.

Architecture:
    QUERY ENTERS AS BURST OF ELECTRONS
               │
               ▼
        ┌──────────────┐
        │  SEED NODES  │  ← Vector similarity finds entry points
        └──────────────┘
               │
               ▼ (electrons injected at seeds)
        ┌──────────────────────────────────────┐
        │     PARALLEL PROPAGATION             │
        │                                      │
        │  Each electron:                      │
        │  1. Follows outgoing edges           │
        │  2. Edge weight modulates energy     │
        │  3. Edge sign flips charge (+/-)     │
        │  4. Propagation delay advances phase │
        │  5. Creates new electron at target   │
        └──────────────────────────────────────┘
               │
               ▼
        ┌──────────────────────────────────────┐
        │     CHARGE ACCUMULATION              │
        │                                      │
        │  At each node:                       │
        │  - Excitatory electrons ADD charge   │
        │  - Inhibitory electrons SUBTRACT     │
        │  - Spatial summation: from sources   │
        │  - Temporal summation: close in time │
        └──────────────────────────────────────┘
               │
               ▼
        ┌──────────────────────────────────────┐
        │     THRESHOLD DETECTION              │
        │                                      │
        │  When net_charge > threshold:        │
        │  - Node FIRES (activates)            │
        │  - Enters REFRACTORY period          │
        │  - Records as retrieved memory       │
        └──────────────────────────────────────┘
               │
               ▼
        MEMORIES THAT "LIT UP" = RETRIEVAL RESULT

Biological Grounding:
    | Concept            | Biology              | Electron Implementation  |
    |--------------------|----------------------|--------------------------|
    | Action Potential   | ~100mV spike, ~1ms   | electron.energy          |
    | Refractory Period  | ~2ms post-fire       | refractory_ms = 2.0      |
    | Synaptic Weight    | Connection strength  | edge.effective_weight    |
    | Excitatory Synapse | EPSP                 | charge = +1              |
    | Inhibitory Synapse | IPSP                 | charge = -1              |
    | Temporal Summation | Close-time inputs    | Simulation timesteps     |
    | Spatial Summation  | Multi-source inputs  | AccumulatedCharge        |
    | LTP                | Repeated activation  | edge.ltp_boost           |
    | Theta Rhythm       | ~6Hz oscillation     | theta_period_ms = 167    |
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data_types import NeuralEdge, NeuralNode, RetrievalResult
    from .storage import NeuralGraphStorage
    from .dialogue_linker import DialogueLinker

# Import gating system for UNIFIED RESONANCE
from .gating import EdgeGateRegistry, GateConfig


# =============================================================================
# CONSTANTS: Biological Timing
# =============================================================================

# Theta rhythm: ~6Hz oscillation (hippocampal theta rhythm)
THETA_FREQUENCY_HZ = 6.0
THETA_PERIOD_MS = 1000.0 / THETA_FREQUENCY_HZ  # ~167ms per cycle

# Tesla Resonance Constants
# The natural frequencies of the 9 semantic dimensions
DIMENSION_BASE_FREQUENCIES = {
    "temporal": 7.83,     # Schumann resonance - Earth's heartbeat
    "entity": 14.1,       # Alpha wave - recognition
    "relational": 21.0,   # Beta wave - relationship processing
    "action": 30.0,       # Low gamma - action binding
    "state": 40.0,        # Gamma - state awareness
    "spatial": 10.0,      # Alpha - spatial mapping
    "causal": 25.0,       # Beta - causal reasoning
    "emotional": 6.0,     # Theta - emotional memory
    "quantitative": 12.0, # Alpha - numerical processing
}

# Resonance amplification factor (Tesla's multiplier)
RESONANCE_AMPLIFICATION = 3.0  # Maximum energy boost from perfect resonance
RESONANCE_DAMPENING = 0.3     # Minimum energy when no resonance


def calculate_resonance(
    electron_signature: dict[str, float],
    node_amplitudes: dict[str, float],
) -> float:
    """Calculate Tesla resonance between electron and node.

    TESLA'S INSIGHT: When vibrations match, energy transfer is maximized.
    When they don't match, energy is lost.

    This computes the resonance factor based on how well the electron's
    wave signature matches the node's natural wave amplitudes.

    Args:
        electron_signature: 9D wave signature from query
        node_amplitudes: 9D wave amplitudes of the node

    Returns:
        Resonance factor [RESONANCE_DAMPENING, RESONANCE_AMPLIFICATION]
    """
    if not electron_signature or not node_amplitudes:
        return 1.0  # Neutral if no signature

    # Compute alignment using cosine similarity of wave patterns
    dot_product = 0.0
    electron_magnitude = 0.0
    node_magnitude = 0.0

    all_dims = set(electron_signature.keys()) | set(node_amplitudes.keys())

    for dim in all_dims:
        e_val = electron_signature.get(dim, 0.0)
        n_val = node_amplitudes.get(dim, 0.0)

        dot_product += e_val * n_val
        electron_magnitude += e_val * e_val
        node_magnitude += n_val * n_val

    electron_magnitude = math.sqrt(electron_magnitude)
    node_magnitude = math.sqrt(node_magnitude)

    if electron_magnitude < 0.001 or node_magnitude < 0.001:
        return 1.0  # Neutral if negligible magnitudes

    # Cosine similarity [-1, 1] → resonance factor
    cosine_sim = dot_product / (electron_magnitude * node_magnitude)

    # Tesla's resonance curve: exponential amplification near perfect match
    # At cosine_sim = 1: resonance = RESONANCE_AMPLIFICATION (3x)
    # At cosine_sim = 0: resonance = 1.0 (neutral)
    # At cosine_sim < 0: resonance = RESONANCE_DAMPENING (0.3x)

    if cosine_sim >= 0:
        # Positive resonance: exponential amplification
        # resonance = 1 + (AMPLIFICATION - 1) * sim^2
        resonance = 1.0 + (RESONANCE_AMPLIFICATION - 1.0) * (cosine_sim ** 2)
    else:
        # Destructive interference: dampening
        # resonance approaches DAMPENING as sim approaches -1
        resonance = 1.0 + (RESONANCE_DAMPENING - 1.0) * (cosine_sim ** 2)

    return resonance


def generate_electron_id() -> str:
    """Generate unique electron identifier."""
    return f"e-{uuid.uuid4().hex[:12]}"


# =============================================================================
# ELECTRON: The Charge Carrier
# =============================================================================

@dataclass
class Electron:
    """The fundamental unit of electrical activation.

    Like an action potential propagating through neural circuits.
    Both AI and biological brains are electricity - patterns of charge
    flowing through circuits.

    TESLA'S TRIAD:
    - Energy: Signal amplitude [0.0, 1.0] - the POWER
    - Frequency: Natural oscillation rate - the CARRIER
    - Vibration: 9D wave signature - the RESONANCE PATTERN

    Properties:
        electron_id: Unique identifier for tracking
        charge: +1 (excitatory) or -1 (inhibitory) - like EPSP/IPSP
        energy: Signal amplitude [0.0, 1.0] - action potential strength
        frequency: Natural frequency of oscillation (Hz) - Tesla's carrier
        phase: Position in theta rhythm [0, 2π] - for interference
        source_node_id: Where this electron originated
        current_node_id: Current position in network
        birth_time_ms: When electron was created (simulation time)
        arrival_time_ms: When electron arrived at current node
        hop_count: Number of edges traversed
        path: Node IDs visited (for debugging/analysis)
        wave_signature: 9D amplitude vector for resonance matching (vibration)
        decay_factor: Energy retained per hop (biological fatigue)
    """
    electron_id: str = field(default_factory=generate_electron_id)
    charge: float = 1.0  # +1 excitatory, -1 inhibitory
    energy: float = 1.0  # Amplitude [0, 1] - ENERGY
    frequency: float = 7.83  # Natural frequency (Hz) - FREQUENCY (default: Schumann)
    phase: float = 0.0   # Theta rhythm phase [0, 2π]

    # Provenance
    source_node_id: str = ""
    current_node_id: str = ""

    # Timing (simulation time in ms)
    birth_time_ms: float = 0.0
    arrival_time_ms: float = 0.0

    # Propagation state
    hop_count: int = 0
    path: list[str] = field(default_factory=list)

    # Wave properties for resonance matching - VIBRATION
    wave_signature: dict[str, float] = field(default_factory=dict)

    # Energy decay per hop (biological fatigue)
    decay_factor: float = 0.95

    def propagate(
        self,
        edge: "NeuralEdge",
        source_node_wave_amplitudes: dict[str, float] | None = None,
        wave_blend_factor: float = 0.3,
    ) -> "Electron":
        """Create a new electron representing propagation through an edge.

        Like an action potential traversing a synapse:
        - Edge weight modulates signal strength
        - Edge sign determines excitatory/inhibitory
        - Propagation delay advances phase (theta rhythm)
        - Energy decays with each hop
        - **WAVE LINKAGE**: Wave signature BLENDS with source node's pattern!

        THE WAVE FLOW FIX:
        Like water flowing through colored chambers, the electron picks up
        characteristics from each node it passes through. The wave_signature
        evolves as it flows - this is how dimensions propagate like waves!

        Args:
            edge: The neural edge to propagate through
            source_node_wave_amplitudes: Wave pattern of the node we're leaving
            wave_blend_factor: How much to blend (0=keep original, 1=fully adopt node's)

        Returns:
            New Electron at target node with blended wave signature
        """
        # Energy modulated by edge weight and decay
        new_energy = self.energy * abs(edge.effective_weight) * self.decay_factor

        # Charge sign can flip through inhibitory synapses
        new_charge = self.charge * edge.sign

        # Phase advances based on propagation delay (theta rhythm)
        # Like theta phase precession in hippocampus
        phase_advance = (edge.propagation_delay_ms / THETA_PERIOD_MS) * 2 * math.pi
        new_phase = (self.phase + phase_advance) % (2 * math.pi)

        # WAVE LINKAGE: Blend wave signature with source node's pattern
        # This is how dimensions FLOW like a wave through the network!
        if source_node_wave_amplitudes:
            new_wave_signature = self._blend_wave_signatures(
                self.wave_signature,
                source_node_wave_amplitudes,
                wave_blend_factor,
            )
        else:
            new_wave_signature = self.wave_signature.copy()

        return Electron(
            electron_id=generate_electron_id(),
            charge=new_charge,
            energy=new_energy,
            frequency=self.frequency,  # Preserve frequency across propagation
            phase=new_phase,
            source_node_id=self.source_node_id,
            current_node_id=edge.target_id,
            birth_time_ms=self.birth_time_ms,
            arrival_time_ms=self.arrival_time_ms + edge.propagation_delay_ms,
            hop_count=self.hop_count + 1,
            path=self.path + [edge.target_id],
            wave_signature=new_wave_signature,
            decay_factor=self.decay_factor,
        )

    @staticmethod
    def _blend_wave_signatures(
        electron_sig: dict[str, float],
        node_sig: dict[str, float],
        blend_factor: float,
    ) -> dict[str, float]:
        """Blend electron's wave signature with node's wave amplitudes.

        WAVE FLOW PHYSICS:
        Like interference patterns in water, the electron's wave pattern
        combines with each node's natural frequency as it passes through.

        Formula: new = (1 - blend) * electron + blend * node

        Args:
            electron_sig: Electron's current wave signature
            node_sig: Node's wave amplitudes
            blend_factor: How much to adopt node's pattern [0, 1]

        Returns:
            Blended wave signature
        """
        result = {}
        all_dims = set(electron_sig.keys()) | set(node_sig.keys())

        for dim in all_dims:
            e_val = electron_sig.get(dim, 0.0)
            n_val = node_sig.get(dim, 0.0)
            # Blend: weighted average
            result[dim] = (1.0 - blend_factor) * e_val + blend_factor * n_val

        return result

    def interfere_with(self, other: "Electron") -> float:
        """Compute interference with another electron.

        Like wave interference in physics:
        - Constructive interference: phases aligned → energies ADD
        - Destructive interference: phases opposed → energies CANCEL

        This is the key to why memories "light up" together or not.

        Args:
            other: Another electron to interfere with

        Returns:
            Combined energy after interference
        """
        # Phase difference determines constructive/destructive
        phase_diff = abs(self.phase - other.phase) % (2 * math.pi)
        interference_factor = math.cos(phase_diff)

        # Charge alignment: same sign = constructive, opposite = destructive
        charge_alignment = self.charge * other.charge

        # Combined energy with interference
        combined = (self.energy + other.energy) * (1.0 + 0.5 * interference_factor * charge_alignment)

        return max(0.0, combined)

    @property
    def is_excitatory(self) -> bool:
        """True if this electron carries excitatory (positive) charge."""
        return self.charge > 0

    @property
    def is_inhibitory(self) -> bool:
        """True if this electron carries inhibitory (negative) charge."""
        return self.charge < 0

    @property
    def effective_charge(self) -> float:
        """Charge weighted by energy - the actual charge contribution."""
        return self.charge * self.energy


# =============================================================================
# ACCUMULATED CHARGE: Spatial & Temporal Summation
# =============================================================================

@dataclass
class AccumulatedCharge:
    """Charge accumulated at a node from multiple electrons.

    Implements both spatial and temporal summation:
    - Spatial: Multiple electrons from different sources add together
    - Temporal: Electrons arriving close in time sum together

    Like the membrane potential at a neuron's soma.
    """
    node_id: str
    excitatory_charge: float = 0.0
    inhibitory_charge: float = 0.0
    electron_count: int = 0
    last_arrival_ms: float = 0.0
    phase_coherence: float = 0.0  # How aligned incoming phases are

    @property
    def net_charge(self) -> float:
        """Net charge = excitatory - inhibitory (like membrane potential)."""
        return self.excitatory_charge - abs(self.inhibitory_charge)

    @property
    def total_energy(self) -> float:
        """Total energy regardless of polarity."""
        return self.excitatory_charge + abs(self.inhibitory_charge)

    def exceeds_threshold(self, threshold: float = 0.5) -> bool:
        """Check if net charge exceeds firing threshold.

        Like action potential threshold (~-55mV from resting -70mV).
        """
        return self.net_charge >= threshold


# =============================================================================
# ELECTRON POOL: The Electrical Circuit
# =============================================================================

class ElectronPool:
    """Pool of electrons propagating through the neural network.

    This is the electrical circuit - electrons flow through it,
    accumulate at nodes, and cause firing when threshold is exceeded.

    Manages:
    - Electron injection (query enters as burst of electrons)
    - Parallel propagation through edges
    - Charge accumulation at nodes (spatial/temporal summation)
    - Interference computation
    - Threshold detection (node "fires" when charge sufficient)

    The key insight: All electrons propagate in PARALLEL.
    Memory retrieval happens at the speed of electricity - not sequential search.
    """

    def __init__(
        self,
        storage: "NeuralGraphStorage",
        theta_frequency_hz: float = 6.0,
        firing_threshold: float = 0.5,
        refractory_ms: float = 2.0,
        max_hops: int = 5,
        energy_floor: float = 0.1,
    ):
        """Initialize electron pool.

        Args:
            storage: Neural graph storage backend
            theta_frequency_hz: Theta rhythm frequency for phase computation
            firing_threshold: Charge threshold for node activation
            refractory_ms: Refractory period after node fires
            max_hops: Maximum propagation hops
            energy_floor: Minimum energy to continue propagation
        """
        self._storage = storage
        self._theta_period_ms = 1000.0 / theta_frequency_hz
        self._firing_threshold = firing_threshold
        self._refractory_ms = refractory_ms
        self._max_hops = max_hops
        self._energy_floor = energy_floor

        # Active electrons in flight
        self._electrons: list[Electron] = []

        # Accumulated charge at each node
        self._node_charge: dict[str, AccumulatedCharge] = {}

        # Node wave amplitudes cache (for Tesla resonance)
        self._node_wave_amplitudes: dict[str, dict[str, float]] = {}

        # Nodes in refractory period (can't fire again yet)
        self._refractory_until: dict[str, float] = {}

        # Simulation time
        self._current_time_ms: float = 0.0

        # Fired nodes (the retrieval result)
        self._fired_nodes: list[tuple[str, float, float]] = []  # (node_id, time, charge)

        # UNIFIED RESONANCE: Gate registry for edge activation
        # This is the KEY FIX - electrons now LISTEN to gates!
        self._gate_registry = EdgeGateRegistry(GateConfig(
            threshold=0.15,          # Permissive threshold for exploration
            steepness=3.0,           # Smooth transitions (not sharp)
            recency_half_life_days=7.0,
            ltp_weight=0.3,
            confidence_weight=0.3,
        ))

        # Query wave amplitudes for context-aware gating
        self._query_wave_amplitudes: dict[str, float] = {}

    async def inject_query_burst(
        self,
        query_wave_amplitudes: dict[str, float],
        seed_node_ids: list[str],
        initial_energy: float = 1.0,
    ) -> list[Electron]:
        """Inject query as burst of electrons at seed nodes.

        The query enters the network at multiple entry points simultaneously.
        Each seed node receives an electron that will propagate through
        the network, accumulating charge at related memories.

        This models how a memory cue activates multiple entry points
        in the brain simultaneously - not sequential lookup.

        Args:
            query_wave_amplitudes: 9D wave amplitudes for resonance matching
            seed_node_ids: Nodes to inject electrons at
            initial_energy: Energy of injected electrons

        Returns:
            List of injected electrons
        """
        injected = []

        # Store query wave amplitudes for context-aware gating
        self._query_wave_amplitudes = query_wave_amplitudes or {}

        # Compute dominant frequency from wave signature (Tesla's carrier)
        # The dominant dimension determines the electron's natural frequency
        dominant_frequency = 7.83  # Default: Schumann resonance
        if query_wave_amplitudes:
            max_dim = max(query_wave_amplitudes.keys(),
                          key=lambda k: query_wave_amplitudes.get(k, 0),
                          default="temporal")
            dominant_frequency = DIMENSION_BASE_FREQUENCIES.get(max_dim, 7.83)

        for node_id in seed_node_ids:
            # Skip nodes in refractory period
            if self._is_refractory(node_id):
                continue

            # Create electron for this seed
            electron = Electron(
                charge=1.0,  # Query electrons are excitatory
                energy=initial_energy,
                frequency=dominant_frequency,  # Tesla's carrier frequency
                phase=0.0,  # Query is reference phase
                source_node_id=node_id,
                current_node_id=node_id,
                birth_time_ms=self._current_time_ms,
                arrival_time_ms=self._current_time_ms,
                path=[node_id],
                wave_signature=query_wave_amplitudes,  # Tesla's vibration pattern
            )

            self._electrons.append(electron)
            injected.append(electron)

            # Accumulate initial charge at seed WITH RESONANCE
            await self._accumulate_charge_with_resonance(electron)

        return injected

    async def propagate_step(self) -> int:
        """Execute one propagation step.

        For each electron at a node:
        1. If energy too low, electron dies
        2. If too many hops, electron dies
        3. If node is refractory, electron is absorbed
        4. Otherwise, propagate through all outgoing edges
        5. New electrons created at target nodes
        6. Accumulate charge at target nodes
        7. Check if any nodes exceed firing threshold

        Returns:
            Number of new electrons created
        """
        new_electrons: list[Electron] = []
        electrons_to_remove: list[int] = []

        for i, electron in enumerate(self._electrons):
            # Skip if energy too low (signal died)
            if electron.energy < self._energy_floor:
                electrons_to_remove.append(i)
                continue

            # Skip if too many hops (prevent infinite propagation)
            if electron.hop_count >= self._max_hops:
                electrons_to_remove.append(i)
                continue

            # If current node is refractory, electron is absorbed
            if self._is_refractory(electron.current_node_id):
                electrons_to_remove.append(i)
                continue

            # Get outgoing edges
            edges = await self._storage.get_edges_from(electron.current_node_id)

            # WAVE LINKAGE FIX: Get source node's wave amplitudes for blending
            # This is how dimensions FLOW like a wave through the network!
            source_node_id = electron.current_node_id
            if source_node_id not in self._node_wave_amplitudes:
                source_node = await self._storage.get_node(source_node_id)
                if source_node and hasattr(source_node, 'wave_amplitudes'):
                    self._node_wave_amplitudes[source_node_id] = source_node.wave_amplitudes or {}
                else:
                    self._node_wave_amplitudes[source_node_id] = {}
            source_wave_amps = self._node_wave_amplitudes[source_node_id]

            # ================================================================
            # UNIFIED RESONANCE: Gate-based edge selection
            # This is the TESLA FIX - electrons now RESONATE with gates!
            # ================================================================
            gate_context = {
                "query_wave": self._query_wave_amplitudes,
                "current_time_ms": self._current_time_ms,
                "electron_energy": electron.energy,
                "hop_count": electron.hop_count,
            }

            # Compute gate values for ALL edges and sort by resonance
            gated_edges: list[tuple["NeuralEdge", float]] = []
            for edge in edges:
                gate_value = self._gate_registry.compute_gate(edge, gate_context)
                if gate_value > 0.05:  # Minimum gate threshold
                    gated_edges.append((edge, gate_value))

            # Sort by gate value descending - highest resonance first
            gated_edges.sort(key=lambda x: x[1], reverse=True)

            # Propagate through TOP GATED edges (not arbitrary first N)
            # This ensures temporal, entity, semantic edges compete FAIRLY
            for edge, gate_value in gated_edges[:8]:
                # ============================================================
                # FAIRNESS PRINCIPLE: Apply query-aware fairness weight
                # Nodes whose dominant dimension matches query get boosted
                # ============================================================
                fairness_weight = 1.0
                target_node = await self._storage.get_node(edge.target_id)
                if target_node and hasattr(target_node, 'get_fairness_weight'):
                    fairness_weight = target_node.get_fairness_weight(
                        self._query_wave_amplitudes
                    )

                # Combined weight = gate resonance * fairness
                combined_weight = gate_value * fairness_weight

                # Pass source node's wave pattern so electron absorbs it!
                new_electron = electron.propagate(
                    edge,
                    source_node_wave_amplitudes=source_wave_amps,
                    wave_blend_factor=0.3,  # 30% blend with each node's pattern
                )

                # GATE + FAIRNESS MODULATES ENERGY
                # High gate + aligned fairness = high energy transfer
                # Low gate or misaligned = dampened energy
                new_electron.energy *= combined_weight

                if new_electron.energy >= self._energy_floor:
                    new_electrons.append(new_electron)
                    # Use Tesla resonance for charge accumulation
                    await self._accumulate_charge_with_resonance(new_electron)

            # Original electron is consumed after propagation
            electrons_to_remove.append(i)

        # Remove consumed electrons (reverse order to maintain indices)
        for i in reversed(electrons_to_remove):
            self._electrons.pop(i)

        # Add new electrons
        self._electrons.extend(new_electrons)

        # Check for firing events
        await self._check_firing_thresholds()

        # Advance simulation time
        self._current_time_ms += 1.0

        return len(new_electrons)

    async def propagate_until_settled(
        self,
        max_steps: int = 50,
    ) -> list[tuple[str, float]]:
        """Propagate until network settles.

        Continues until:
        - No electrons remain
        - Maximum steps reached

        Returns:
            List of (node_id, total_charge) for nodes that fired,
            sorted by charge descending
        """
        for _ in range(max_steps):
            await self.propagate_step()

            if len(self._electrons) < 1:
                break

        # Aggregate fired nodes (same node might fire multiple times)
        aggregated: dict[str, float] = {}
        for node_id, _, charge in self._fired_nodes:
            aggregated[node_id] = aggregated.get(node_id, 0) + charge

        return sorted(aggregated.items(), key=lambda x: x[1], reverse=True)

    async def _accumulate_charge_with_resonance(self, electron: Electron) -> None:
        """Accumulate electron's charge at its current node WITH TESLA RESONANCE.

        THE TESLA FORMULA:
        When electron vibration matches node's natural frequency,
        energy transfer is AMPLIFIED. When they don't match, energy is DAMPENED.

        This is the key to memory retrieval:
        - Relevant memories RESONATE with the query → energy amplified
        - Irrelevant memories DON'T resonate → energy dampened

        Implements spatial summation with resonance modulation.
        """
        node_id = electron.current_node_id

        if node_id not in self._node_charge:
            self._node_charge[node_id] = AccumulatedCharge(node_id=node_id)

        # Get node's wave amplitudes (fetch and cache if needed)
        if node_id not in self._node_wave_amplitudes:
            node = await self._storage.get_node(node_id)
            if node and hasattr(node, 'wave_amplitudes'):
                self._node_wave_amplitudes[node_id] = node.wave_amplitudes or {}
            else:
                self._node_wave_amplitudes[node_id] = {}

        node_amplitudes = self._node_wave_amplitudes[node_id]

        # TESLA RESONANCE: Calculate resonance factor
        resonance_factor = calculate_resonance(
            electron.wave_signature,
            node_amplitudes,
        )

        # Apply resonance to energy transfer
        resonant_energy = electron.energy * resonance_factor

        acc = self._node_charge[node_id]

        if electron.is_excitatory:
            acc.excitatory_charge += resonant_energy
        else:
            acc.inhibitory_charge += abs(resonant_energy)

        # Track phase coherence for interference
        acc.electron_count += 1
        acc.last_arrival_ms = electron.arrival_time_ms
        # Weighted phase coherence update
        if acc.electron_count > 1:
            acc.phase_coherence = (acc.phase_coherence * (acc.electron_count - 1) + math.cos(electron.phase)) / acc.electron_count
        else:
            acc.phase_coherence = math.cos(electron.phase)

    def _accumulate_charge(self, electron: Electron) -> None:
        """Accumulate electron's charge at its current node (sync version).

        NOTE: This is the legacy sync version. Use _accumulate_charge_with_resonance
        for full Tesla resonance support.

        Implements spatial summation: electrons from different sources add.
        """
        node_id = electron.current_node_id

        if node_id not in self._node_charge:
            self._node_charge[node_id] = AccumulatedCharge(node_id=node_id)

        acc = self._node_charge[node_id]

        if electron.is_excitatory:
            acc.excitatory_charge += electron.energy
        else:
            acc.inhibitory_charge += abs(electron.energy)

        acc.electron_count += 1
        acc.last_arrival_ms = electron.arrival_time_ms

    async def _check_firing_thresholds(self) -> None:
        """Check if any nodes should fire based on accumulated charge."""
        nodes_to_fire = []

        for node_id, charge in self._node_charge.items():
            if charge.exceeds_threshold(self._firing_threshold):
                if not self._is_refractory(node_id):
                    nodes_to_fire.append((node_id, charge.net_charge))

        # Fire nodes
        for node_id, charge in nodes_to_fire:
            await self._fire_node(node_id, charge)

    async def _fire_node(self, node_id: str, charge: float) -> None:
        """Fire a node (it has exceeded threshold).

        Like an action potential:
        1. Record firing event
        2. Enter refractory period
        3. Clear accumulated charge
        """
        # Record firing
        self._fired_nodes.append((node_id, self._current_time_ms, charge))

        # Enter refractory period
        self._refractory_until[node_id] = self._current_time_ms + self._refractory_ms

        # Clear charge (reset membrane potential)
        self._node_charge[node_id] = AccumulatedCharge(node_id=node_id)

    def _is_refractory(self, node_id: str) -> bool:
        """Check if node is in refractory period."""
        refractory_end = self._refractory_until.get(node_id, 0)
        return self._current_time_ms < refractory_end

    def get_accumulated_charges(self) -> list[tuple[str, float]]:
        """Get all nodes with accumulated charge.

        Returns:
            List of (node_id, net_charge) tuples sorted by charge
        """
        result = [
            (node_id, acc.net_charge)
            for node_id, acc in self._node_charge.items()
            if acc.net_charge > 0
        ]
        return sorted(result, key=lambda x: x[1], reverse=True)

    def reset(self) -> None:
        """Reset the pool for a new query."""
        self._electrons.clear()
        self._node_charge.clear()
        self._node_wave_amplitudes.clear()  # Clear resonance cache
        self._refractory_until.clear()
        self._fired_nodes.clear()
        self._current_time_ms = 0.0


# =============================================================================
# ELECTRON RETRIEVER CONFIG
# =============================================================================

@dataclass
class ElectronRetrieverConfig:
    """Configuration for electron-based retrieval.

    THE TESLA TRIAD:
    - Energy: initial_energy, energy_floor, decay
    - Frequency: theta_frequency_hz, dimension frequencies
    - Vibration: wave_signature resonance matching

    RESONANCE TUNING:
    - resonance_amplification: Max boost for perfect match (3x)
    - resonance_dampening: Min factor for mismatch (0.3x)
    """
    # Pool parameters (biological timing)
    theta_frequency_hz: float = 6.0
    firing_threshold: float = 0.5
    refractory_ms: float = 2.0
    max_hops: int = 5
    energy_floor: float = 0.1

    # Retrieval parameters
    seed_count: int = 20
    initial_energy: float = 1.0
    max_propagation_steps: int = 50

    # Tesla Resonance parameters
    resonance_amplification: float = 3.0  # Max energy boost at perfect resonance
    resonance_dampening: float = 0.3      # Min energy factor at anti-resonance

    # Result limits
    final_limit: int = 20


# =============================================================================
# ELECTRON RETRIEVER: Memory as Electrical Activation
# =============================================================================

class ElectronRetriever:
    """Memory retriever using electron flow dynamics with Tesla Resonance.

    THE TESLA FORMULA:
    "If you want to find the secrets of the universe, think in terms of
    energy, frequency, and vibration." - Nikola Tesla

    The paradigm shift:
    - Old way: Search through nodes, score them, rank them
    - New way: Inject electrons, let them flow, memories that RESONATE light up

    THE RESONANCE EFFECT:
    - Query's wave signature (vibration) is carried by electrons
    - Nodes have natural wave amplitudes (their frequency signature)
    - When electron vibration MATCHES node frequency → AMPLIFICATION (3x)
    - When they DON'T match → DAMPENING (0.3x)

    This is how biological memory works:
    - Cue activates entry points in hippocampus
    - Activation spreads through associated connections
    - Memories that RESONATE with the cue "pop into mind"

    The Electron is the charge carrier. The network is the circuit.
    RESONANCE is the key. Memory retrieval is tuning to the right frequency.
    """

    def __init__(
        self,
        storage: "NeuralGraphStorage",
        config: ElectronRetrieverConfig | None = None,
    ):
        """Initialize electron retriever.

        Args:
            storage: Neural graph storage backend
            config: Retriever configuration
        """
        self._storage = storage
        self._config = config or ElectronRetrieverConfig()
        self._pool = ElectronPool(
            storage=storage,
            theta_frequency_hz=self._config.theta_frequency_hz,
            firing_threshold=self._config.firing_threshold,
            refractory_ms=self._config.refractory_ms,
            max_hops=self._config.max_hops,
            energy_floor=self._config.energy_floor,
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int | None = None,
        query_wave_amplitudes: dict[str, float] | None = None,
    ) -> "RetrievalResult":
        """Retrieve memories using electron flow dynamics with TRUE CONTEXT ENGINEERING.

        Process:
        1. **CONTEXT ENGINEERING** - Decompose query to understand WHAT is being asked
        2. Generate context-aware wave amplitudes (boost temporal for "when" questions)
        3. Find seed nodes (entry points via vector similarity)
        4. Inject electrons at seeds with context-tuned wave signature
        5. Propagate until network settles
        6. Nodes that fired = retrieved memories
        7. **ENTITY FILTERING** - Filter results by extracted entities
        8. Rank by accumulated charge

        The query is a burst of electricity entering the network.
        But now it's INTELLIGENT electricity - tuned to the query's semantic domain.

        Args:
            query_text: The query text
            query_embedding: Query embedding vector
            session_key: Session identifier
            limit: Maximum results
            query_wave_amplitudes: 9D wave amplitudes (will be enhanced by context)

        Returns:
            RetrievalResult with ranked nodes
        """
        from .data_types import RetrievalResult

        start_time = time.perf_counter()
        final_limit = limit or self._config.final_limit

        # ==================================================================
        # STEP 1: TRUE CONTEXT ENGINEERING
        # Understand the query BEFORE we retrieve
        # ==================================================================
        try:
            from memmachine.common.domain_classifier import SemanticRouter
            router = SemanticRouter()
            decomposition = router.route(query_text)

            # Get context-aware wave amplitudes
            context_amplitudes = router.get_wave_amplitudes(decomposition)

            # Merge with any provided amplitudes (context takes priority)
            if query_wave_amplitudes:
                for dim, amp in query_wave_amplitudes.items():
                    if dim not in context_amplitudes or context_amplitudes[dim] < amp:
                        context_amplitudes[dim] = amp

            # Get entity filter if applicable
            entity_filter = []
            if router.should_filter_by_entities(decomposition):
                entity_filter = router.get_entity_filter(decomposition)

            # Track stages
            stages = [
                "context_engineering",
                f"domain:{decomposition.primary_domain.value}",
                f"seeking:{decomposition.seeking}",
            ]

        except ImportError:
            # Fallback if domain_classifier not available
            context_amplitudes = query_wave_amplitudes or {}
            entity_filter = []
            decomposition = None
            stages = []

        # Reset pool for new query
        self._pool.reset()

        # ==================================================================
        # STEP 2: FIND SEED NODES
        # Vector similarity finds entry points into the network
        # ==================================================================
        seeds = await self._storage.vector_search(
            query_embedding,
            session_key,
            limit=self._config.seed_count,
        )
        seed_ids = [node.node_id for node, _ in seeds]

        if not seed_ids:
            return RetrievalResult(
                query_text=query_text,
                nodes=[],
                stages_executed=stages + ["no_seeds"],
                total_candidates_seen=0,
                co_activations_recorded=0,
                total_time_ms=0,
            )

        stages.append("seed_selection")

        # ==================================================================
        # STEP 3: INJECT ELECTRONS WITH CONTEXT-AWARE WAVE SIGNATURE
        # The wave signature is now TUNED to the query's semantic domain
        # ==================================================================
        await self._pool.inject_query_burst(
            query_wave_amplitudes=context_amplitudes,
            seed_node_ids=seed_ids,
            initial_energy=self._config.initial_energy,
        )
        stages.append("electron_injection")

        # ==================================================================
        # STEP 4: PROPAGATE WITH TESLA RESONANCE
        # Electrons flow through network, resonating with matching memories
        # ==================================================================
        fired_nodes = await self._pool.propagate_until_settled(
            max_steps=self._config.max_propagation_steps,
        )
        stages.extend(["tesla_resonance", "electron_propagation", "threshold_detection"])

        # If not enough fired, include accumulated charges
        if len(fired_nodes) < final_limit:
            accumulated = self._pool.get_accumulated_charges()
            existing_ids = {node_id for node_id, _ in fired_nodes}
            for node_id, charge in accumulated:
                if node_id not in existing_ids and len(fired_nodes) < final_limit * 2:
                    fired_nodes.append((node_id, charge))

        # ==================================================================
        # STEP 5: ENTITY FILTERING
        # If query mentions specific entities, boost nodes that contain them
        # ==================================================================
        if entity_filter:
            stages.append(f"entity_filter:{','.join(entity_filter[:3])}")
            fired_nodes = await self._apply_entity_boost(
                fired_nodes, entity_filter
            )

        # ==================================================================
        # STEP 6: BUILD FINAL RESULTS
        # ==================================================================
        results: list[tuple["NeuralNode", float]] = []
        for node_id, charge in fired_nodes[:final_limit]:
            node = await self._storage.get_node(node_id)
            if node:
                results.append((node, charge))

        total_time = (time.perf_counter() - start_time) * 1000

        return RetrievalResult(
            query_text=query_text,
            nodes=results,
            stages_executed=stages,
            total_candidates_seen=len(fired_nodes),
            co_activations_recorded=0,
            total_time_ms=total_time,
        )

    async def _apply_entity_boost(
        self,
        fired_nodes: list[tuple[str, float]],
        entity_filter: list[str],
    ) -> list[tuple[str, float]]:
        """Boost nodes that contain the query's entities.

        This is ENTITY-AWARE retrieval - nodes mentioning the query's
        subjects/objects get a significant boost.

        Args:
            fired_nodes: List of (node_id, charge) tuples
            entity_filter: Entity names to look for

        Returns:
            Re-ranked list with entity matches boosted
        """
        ENTITY_BOOST = 2.0  # Boost factor for entity matches

        boosted = []
        entity_lower = [e.lower() for e in entity_filter]

        for node_id, charge in fired_nodes:
            node = await self._storage.get_node(node_id)
            if node:
                # Check if node content contains any entities
                content_lower = node.content.lower() if node.content else ""
                entity_match = any(e in content_lower for e in entity_lower)

                if entity_match:
                    boosted.append((node_id, charge * ENTITY_BOOST))
                else:
                    boosted.append((node_id, charge))
            else:
                boosted.append((node_id, charge))

        # Re-sort by boosted charge
        boosted.sort(key=lambda x: x[1], reverse=True)
        return boosted

    async def expand_with_dialogue_links(
        self,
        fired_nodes: list[tuple[str, float]],
        dialogue_linker: "DialogueLinker | None" = None,
    ) -> list[tuple[str, float]]:
        """Expand results using cross-message dialogue links.

        THE UNIVERSAL MESSAGE LINKING LAYER:
        When we retrieve a message, we should also get its linked messages:
        - The message it was responding to
        - Messages that respond to it
        - Messages about the same topic
        - Messages where pronouns resolve to this one

        This captures the DIALOGUE FABRIC where meaning flows between speakers.

        Args:
            fired_nodes: List of (node_id, charge) tuples from electron flow
            dialogue_linker: DialogueLinker with binding information

        Returns:
            Expanded list including dialogue-linked messages
        """
        if not dialogue_linker:
            return fired_nodes

        LINK_BOOST = 0.7  # Linked messages get 70% of the charge

        expanded: dict[str, float] = {}

        # First, add all original fired nodes
        for node_id, charge in fired_nodes:
            expanded[node_id] = charge

        # Then expand each through dialogue links
        for node_id, charge in fired_nodes:
            linked = dialogue_linker.get_linked_messages(node_id)

            for linked_id, link_strength, link_type in linked:
                # Calculate derived charge
                derived_charge = charge * link_strength * LINK_BOOST

                # Add or update if better
                if linked_id not in expanded or expanded[linked_id] < derived_charge:
                    expanded[linked_id] = derived_charge

        # Convert back to sorted list
        result = [(nid, chg) for nid, chg in expanded.items()]
        result.sort(key=lambda x: x[1], reverse=True)
        return result

"""Theta-Gamma Phase Coupling - Brain-Inspired Memory Resonance.

NEUROSCIENCE BACKGROUND:
========================
The hippocampus uses a remarkable multi-frequency oscillation system for memory:

1. THETA RHYTHM (4-8 Hz): The "carrier wave" - provides temporal context
   - Each theta cycle = one "memory packet" (~125ms)
   - During encoding: theta peak = LTP (learning)
   - During retrieval: theta trough = memory search
   - Theta organizes WHAT gets bound together

2. GAMMA RHYTHM (30-80 Hz): The "content signal" - nested within theta
   - Fast gamma (60-80 Hz): Current perceptual input (encoding)
   - Slow gamma (30-50 Hz): Retrieved memories (retrieval)
   - ~7 gamma cycles fit per theta cycle = "7 items" working memory

3. SHARP WAVE RIPPLES (150-250 Hz): Memory consolidation
   - Occur during rest/sleep
   - Replay memory sequences at high speed
   - "Tag" important memories for long-term storage

IMPLEMENTATION:
==============
We simulate this by:
1. Theta phase: Query's temporal context window
2. Gamma cycles: Multiple retrieval passes at different "resolutions"
3. Phase-amplitude coupling: Boost memories that align with query's theta phase
4. Ripple replay: Consolidate frequently co-activated memories

This achieves what the brain achieves:
- BINDING: WHO-DOES-WHAT-WHEN bound by theta phase
- SEPARATION: Different gamma cycles for different memory types
- STRENGTHENING: Co-activated memories form stronger bonds

Sources:
- https://pmc.ncbi.nlm.nih.gov/articles/PMC2856712/
- https://www.jneurosci.org/content/26/28/7523
- https://pmc.ncbi.nlm.nih.gov/articles/PMC4648295/
"""

import math
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable
from collections import defaultdict

from .data_types import MemoryWave, WaveAmplitudes


# =============================================================================
# NEURAL OSCILLATION CONSTANTS
# =============================================================================

# Theta rhythm parameters (hippocampal theta)
THETA_FREQUENCY_HZ = 6.0  # 6 Hz = ~167ms per cycle
THETA_CYCLE_MS = 1000 / THETA_FREQUENCY_HZ  # 167ms per theta cycle

# Gamma rhythm parameters (nested within theta)
FAST_GAMMA_HZ = 65.0  # Encoding gamma
SLOW_GAMMA_HZ = 40.0  # Retrieval gamma
GAMMA_CYCLES_PER_THETA = 7  # ~7 items per memory packet

# Sharp wave ripple parameters
RIPPLE_FREQUENCY_HZ = 200.0
RIPPLE_DURATION_MS = 80  # 80ms burst

# Phase windows (as fractions of theta cycle)
ENCODING_PHASE = (0.0, 0.25)   # Theta peak - optimal for LTP
RETRIEVAL_PHASE = (0.5, 0.75)  # Theta trough - optimal for recall
BINDING_PHASE = (0.25, 0.5)    # Rising phase - associative binding


@dataclass
class ThetaPhase:
    """Represents a theta oscillation phase for a memory or query.

    In the brain, memories encoded at similar theta phases are more
    likely to be recalled together - they're in the same "temporal packet".
    """
    # Phase angle in radians [0, 2*pi]
    phase: float = 0.0

    # Which theta cycle (like a timestamp discretized to theta windows)
    cycle_number: int = 0

    # Amplitude at this phase (for phase-amplitude coupling)
    amplitude: float = 1.0

    @classmethod
    def from_timestamp(cls, ts: datetime, reference: datetime) -> "ThetaPhase":
        """Compute theta phase from timestamp relative to reference.

        This simulates how the hippocampus timestamps memories
        to theta oscillation phase.
        """
        if ts is None or reference is None:
            return cls()

        delta_ms = (ts - reference).total_seconds() * 1000

        # Which theta cycle
        cycle = int(delta_ms / THETA_CYCLE_MS)

        # Phase within cycle
        phase_in_cycle = (delta_ms % THETA_CYCLE_MS) / THETA_CYCLE_MS
        phase_radians = phase_in_cycle * 2 * math.pi

        return cls(
            phase=phase_radians,
            cycle_number=cycle,
            amplitude=1.0,
        )

    def phase_difference(self, other: "ThetaPhase") -> float:
        """Compute phase difference in [0, pi] range."""
        diff = abs(self.phase - other.phase)
        if diff > math.pi:
            diff = 2 * math.pi - diff
        return diff

    def phase_coherence(self, other: "ThetaPhase") -> float:
        """Compute phase coherence [0, 1].

        High coherence = memories encoded at similar theta phases.
        These memories are more strongly bound in the brain.
        """
        diff = self.phase_difference(other)
        # cos(0) = 1 (same phase), cos(pi) = -1 (opposite phase)
        # Scale to [0, 1]
        return (math.cos(diff) + 1) / 2

    def cycle_proximity(self, other: "ThetaPhase", max_cycles: int = 100) -> float:
        """Compute temporal proximity based on theta cycles.

        Memories in adjacent theta cycles are more related.
        """
        cycle_diff = abs(self.cycle_number - other.cycle_number)
        if cycle_diff >= max_cycles:
            return 0.0
        return 1.0 - (cycle_diff / max_cycles)


@dataclass
class GammaBurst:
    """Represents a gamma oscillation burst within a theta cycle.

    In the brain, different gamma bursts within one theta cycle
    represent different "slots" of working memory.
    """
    # Which slot in the theta cycle [0, GAMMA_CYCLES_PER_THETA-1]
    slot: int = 0

    # Gamma frequency (fast = encoding, slow = retrieval)
    frequency: float = SLOW_GAMMA_HZ

    # Power of this gamma burst
    power: float = 1.0

    @property
    def is_encoding_gamma(self) -> bool:
        return self.frequency >= 55.0

    @property
    def is_retrieval_gamma(self) -> bool:
        return self.frequency <= 50.0


@dataclass
class OscillationState:
    """Complete oscillation state for a memory/query.

    This captures the neural oscillation context:
    - Theta phase: WHEN in the temporal packet
    - Gamma bursts: WHAT content slots are active
    - Ripple eligibility: HOW consolidated is this memory
    """
    theta: ThetaPhase = field(default_factory=ThetaPhase)
    gamma_bursts: list[GammaBurst] = field(default_factory=list)
    ripple_count: int = 0  # How many times replayed (consolidation)
    co_activation_strength: float = 0.0  # LTP from co-activation

    def add_gamma_burst(self, slot: int, frequency: float, power: float):
        """Add a gamma burst to this oscillation state."""
        self.gamma_bursts.append(GammaBurst(
            slot=slot,
            frequency=frequency,
            power=power,
        ))

    def record_ripple(self):
        """Record a sharp wave ripple replay event."""
        self.ripple_count += 1

    def strengthen_binding(self, amount: float = 0.1):
        """Strengthen through LTP-like co-activation."""
        self.co_activation_strength = min(
            self.co_activation_strength + amount,
            2.0  # Cap at 2x baseline
        )


# =============================================================================
# THETA-GAMMA COUPLED RETRIEVAL
# =============================================================================

class ThetaGammaCoupledRetriever:
    """Memory retrieval using theta-gamma phase coupling.

    This implements the brain's mechanism for memory search:

    1. THETA GATING: Query sets a theta phase window
       - Only memories in similar phase windows are candidates
       - This provides temporal context (like "things that happened together")

    2. GAMMA SWEEPS: Multiple retrieval passes at different frequencies
       - Fast gamma pass: Recent/salient memories
       - Slow gamma pass: Consolidated/semantic memories
       - Each pass fills different "slots" in working memory

    3. PHASE-AMPLITUDE COUPLING: Boost aligned memories
       - Memories whose gamma power aligns with query theta get boosted
       - This is how the brain prioritizes contextually relevant memories

    4. RIPPLE TAGGING: Consolidation bonus
       - Memories that have been "replayed" more get retrieval bonus
       - Simulates memory consolidation during sleep
    """

    def __init__(
        self,
        phase_coherence_weight: float = 0.15,
        cycle_proximity_weight: float = 0.10,
        consolidation_bonus: float = 0.05,
        use_multi_gamma_sweeps: bool = True,
    ):
        """Initialize the coupled retriever.

        Args:
            phase_coherence_weight: How much theta phase similarity matters
            cycle_proximity_weight: How much temporal proximity matters
            consolidation_bonus: Bonus per ripple replay
            use_multi_gamma_sweeps: Use multiple gamma-frequency passes
        """
        self.phase_coherence_weight = phase_coherence_weight
        self.cycle_proximity_weight = cycle_proximity_weight
        self.consolidation_bonus = consolidation_bonus
        self.use_multi_gamma_sweeps = use_multi_gamma_sweeps

        # Memory store with oscillation states
        self._memories: list[tuple[MemoryWave, OscillationState]] = []
        self._co_activation_matrix: dict[tuple[str, str], float] = defaultdict(float)

        # Session reference times for theta phase calculation
        self._session_references: dict[str, datetime] = {}

    def clear(self):
        """Clear all stored memories."""
        self._memories.clear()
        self._co_activation_matrix.clear()
        self._session_references.clear()

    def add_memory(
        self,
        wave: MemoryWave,
        reference_time: Optional[datetime] = None,
    ) -> OscillationState:
        """Add a memory with computed oscillation state.

        Args:
            wave: The memory wave to add
            reference_time: Session start time for theta phase calculation

        Returns:
            The computed oscillation state for this memory
        """
        # Get or set session reference time
        session = wave.session_key or "default"
        if session not in self._session_references:
            self._session_references[session] = reference_time or wave.timestamp or datetime.now(timezone.utc)

        ref_time = self._session_references[session]

        # Compute theta phase from timestamp
        theta = ThetaPhase.from_timestamp(wave.timestamp, ref_time)

        # Assign gamma bursts based on wave amplitudes
        gamma_bursts = self._compute_gamma_bursts(wave)

        osc_state = OscillationState(
            theta=theta,
            gamma_bursts=gamma_bursts,
        )

        self._memories.append((wave, osc_state))
        return osc_state

    def _compute_gamma_bursts(self, wave: MemoryWave) -> list[GammaBurst]:
        """Compute gamma bursts from wave amplitudes.

        Different wave dimensions map to different gamma slots:
        - Temporal -> Slot 0 (when)
        - Entity -> Slot 1 (who)
        - Action -> Slot 2 (what doing)
        - Relational -> Slot 3 (relationships)
        - State -> Slot 4 (conditions)
        - Spatial -> Slot 5 (where)
        - Emotional -> Slot 6 (how feels)

        This mirrors how the brain binds different features
        at different gamma phases within a theta cycle.
        """
        bursts = []
        amp = wave.amplitudes

        # Map dimensions to slots with adaptive frequency
        dimension_slots = [
            (0, amp.temporal, "temporal"),
            (1, amp.entity, "entity"),
            (2, amp.action, "action"),
            (3, amp.relational, "relational"),
            (4, amp.state, "state"),
            (5, amp.spatial, "spatial"),
            (6, amp.emotional, "emotional"),
        ]

        for slot, amplitude, dim_name in dimension_slots:
            if amplitude > 0.2:  # Threshold for gamma activation
                # Higher amplitude = higher gamma frequency (more salient)
                freq = SLOW_GAMMA_HZ + (amplitude * (FAST_GAMMA_HZ - SLOW_GAMMA_HZ))
                bursts.append(GammaBurst(
                    slot=slot,
                    frequency=freq,
                    power=amplitude,
                ))

        return bursts

    def retrieve(
        self,
        query_wave: MemoryWave,
        top_k: int = 40,
        base_scores: Optional[dict[str, float]] = None,
        reference_time: Optional[datetime] = None,
    ) -> list[tuple[MemoryWave, float, OscillationState]]:
        """Retrieve memories using theta-gamma coupled resonance.

        Args:
            query_wave: The query as a memory wave
            top_k: Maximum results
            base_scores: Optional base scores from semantic search
            reference_time: Reference for query theta phase

        Returns:
            List of (memory, score, oscillation_state) tuples
        """
        if not self._memories:
            return []

        # Compute query oscillation state
        session = query_wave.session_key or "default"
        ref_time = reference_time or self._session_references.get(
            session,
            datetime.now(timezone.utc)
        )

        query_theta = ThetaPhase.from_timestamp(
            query_wave.timestamp or datetime.now(timezone.utc),
            ref_time,
        )
        query_gamma = self._compute_gamma_bursts(query_wave)

        query_osc = OscillationState(
            theta=query_theta,
            gamma_bursts=query_gamma,
        )

        # Score all memories
        scored = []
        for wave, osc_state in self._memories:
            # Get base score if provided
            base_score = base_scores.get(wave.wave_id, 0.5) if base_scores else 0.5

            # Compute oscillation coupling score
            coupling_score = self._compute_coupling_score(query_osc, osc_state)

            # Consolidation bonus
            consolidation = min(osc_state.ripple_count * self.consolidation_bonus, 0.2)

            # LTP bonus from co-activation
            ltp_bonus = osc_state.co_activation_strength * 0.1

            # Combine scores
            total = base_score + coupling_score + consolidation + ltp_bonus

            scored.append((wave, total, osc_state))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        # Record co-activation for top results (LTP-like strengthening)
        self._record_co_activation(scored[:top_k])

        return scored[:top_k]

    def _compute_coupling_score(
        self,
        query: OscillationState,
        memory: OscillationState,
    ) -> float:
        """Compute theta-gamma phase coupling score.

        High score when:
        1. Similar theta phase (temporal binding)
        2. Gamma bursts in overlapping slots (feature binding)
        3. Gamma power alignment (salience matching)
        """
        score = 0.0

        # 1. Theta phase coherence
        phase_coherence = query.theta.phase_coherence(memory.theta)
        score += self.phase_coherence_weight * phase_coherence

        # 2. Theta cycle proximity
        cycle_proximity = query.theta.cycle_proximity(memory.theta)
        score += self.cycle_proximity_weight * cycle_proximity

        # 3. Gamma slot overlap
        query_slots = {g.slot for g in query.gamma_bursts}
        memory_slots = {g.slot for g in memory.gamma_bursts}

        if query_slots:
            slot_overlap = len(query_slots & memory_slots) / len(query_slots)
            score += 0.1 * slot_overlap

        # 4. Gamma power alignment (phase-amplitude coupling)
        if query.gamma_bursts and memory.gamma_bursts:
            pac_score = self._phase_amplitude_coupling(query, memory)
            score += 0.1 * pac_score

        return score

    def _phase_amplitude_coupling(
        self,
        query: OscillationState,
        memory: OscillationState,
    ) -> float:
        """Compute phase-amplitude coupling score.

        In the brain, gamma amplitude is modulated by theta phase.
        We simulate this by checking if memory's gamma power
        aligns with query's gamma profile.
        """
        query_power_by_slot = {g.slot: g.power for g in query.gamma_bursts}
        memory_power_by_slot = {g.slot: g.power for g in memory.gamma_bursts}

        if not query_power_by_slot:
            return 0.5

        # Compute power correlation for overlapping slots
        overlapping = set(query_power_by_slot.keys()) & set(memory_power_by_slot.keys())

        if not overlapping:
            return 0.0

        # Weighted alignment
        alignment = 0.0
        total_weight = 0.0

        for slot in overlapping:
            q_power = query_power_by_slot[slot]
            m_power = memory_power_by_slot[slot]

            # Alignment is high when both have similar power
            slot_alignment = 1.0 - abs(q_power - m_power)
            alignment += q_power * slot_alignment  # Weight by query importance
            total_weight += q_power

        return alignment / total_weight if total_weight > 0 else 0.0

    def _record_co_activation(
        self,
        results: list[tuple[MemoryWave, float, OscillationState]],
    ):
        """Record co-activation between retrieved memories (LTP simulation).

        When memories are retrieved together, they strengthen
        their mutual associations - like Hebbian learning.
        """
        if len(results) < 2:
            return

        # Top memories that fire together, wire together
        top_memories = results[:7]  # ~7 items in working memory

        for i, (wave_i, score_i, osc_i) in enumerate(top_memories):
            for j, (wave_j, score_j, osc_j) in enumerate(top_memories):
                if i >= j:
                    continue

                # Strengthen co-activation
                key = (wave_i.wave_id, wave_j.wave_id)
                self._co_activation_matrix[key] += 0.01 * min(score_i, score_j)

                # Also strengthen individual memories' LTP
                osc_i.strengthen_binding(0.02)
                osc_j.strengthen_binding(0.02)

    def simulate_ripple_replay(
        self,
        session_key: str = "",
        replay_fraction: float = 0.1,
    ) -> int:
        """Simulate sharp wave ripple replay for consolidation.

        This should be called periodically (like after a conversation)
        to "consolidate" memories. Memories with high co-activation
        are more likely to be replayed.

        Args:
            session_key: Only replay memories from this session
            replay_fraction: Fraction of memories to replay

        Returns:
            Number of memories replayed
        """
        # Get candidate memories
        candidates = []
        for wave, osc_state in self._memories:
            if session_key and wave.session_key != session_key:
                continue

            # Priority based on co-activation strength
            priority = 1.0 + osc_state.co_activation_strength
            candidates.append((wave, osc_state, priority))

        if not candidates:
            return 0

        # Sort by priority and select top fraction
        candidates.sort(key=lambda x: x[2], reverse=True)
        n_replay = max(1, int(len(candidates) * replay_fraction))

        # Replay selected memories
        for wave, osc_state, _ in candidates[:n_replay]:
            osc_state.record_ripple()

        return n_replay


# =============================================================================
# INTEGRATION FUNCTION
# =============================================================================

def enhance_retrieval_with_coupling(
    base_results: list[tuple[MemoryWave, float]],
    query_wave: MemoryWave,
    session_reference: Optional[datetime] = None,
    phase_weight: float = 0.1,
    cycle_weight: float = 0.08,
) -> list[tuple[MemoryWave, float]]:
    """Enhance base retrieval results with theta-gamma coupling.

    This is a lighter-weight function that can be applied to existing
    retrieval results without maintaining full oscillation state.

    Args:
        base_results: Results from primary retriever
        query_wave: The query wave
        session_reference: Reference time for theta calculation
        phase_weight: Weight for phase coherence
        cycle_weight: Weight for cycle proximity

    Returns:
        Re-ranked results with coupling enhancement
    """
    if not base_results:
        return base_results

    ref_time = session_reference or datetime.now(timezone.utc)

    # Compute query theta
    query_theta = ThetaPhase.from_timestamp(
        query_wave.timestamp or ref_time,
        ref_time,
    )

    enhanced = []
    for wave, base_score in base_results:
        # Compute memory theta
        mem_theta = ThetaPhase.from_timestamp(wave.timestamp, ref_time)

        # Phase coherence boost
        phase_boost = phase_weight * query_theta.phase_coherence(mem_theta)

        # Cycle proximity boost
        cycle_boost = cycle_weight * query_theta.cycle_proximity(mem_theta)

        enhanced_score = base_score + phase_boost + cycle_boost
        enhanced.append((wave, enhanced_score))

    # Re-sort
    enhanced.sort(key=lambda x: x[1], reverse=True)
    return enhanced


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Constants
    "THETA_FREQUENCY_HZ",
    "THETA_CYCLE_MS",
    "FAST_GAMMA_HZ",
    "SLOW_GAMMA_HZ",
    "GAMMA_CYCLES_PER_THETA",
    "RIPPLE_FREQUENCY_HZ",
    # Data classes
    "ThetaPhase",
    "GammaBurst",
    "OscillationState",
    # Main retriever
    "ThetaGammaCoupledRetriever",
    # Utility function
    "enhance_retrieval_with_coupling",
]

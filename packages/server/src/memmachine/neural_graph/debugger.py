"""RetrievalDebugger: THE NAPOLEON PRINCIPLE

Napoleon didn't just look at the battle - he traced back every troop movement
to understand how positions were won or lost.

This debugger does the same for memory retrieval:
1. TRACE FORWARD: See exactly how electrons flow from query to answer
2. TRACE BACKWARD: Backpropagate from the answer to see where information decayed
3. VISUALIZE: Show the path, the charges, the resonances, the failures

THE QUESTION: "When did Caroline visit Paris?"
THE ANSWER: Should be "March 15th"

DEBUGGER OUTPUT:
================================================================================
QUERY DECOMPOSITION
================================================================================
  Original: "When did Caroline visit Paris?"
  Domain: TEMPORAL (confidence: 0.95)
  Seeking: WHEN
  Subjects: [Caroline]
  Objects: [Paris]
  Actions: [visit]

================================================================================
SEED NODES (Entry Points)
================================================================================
  [1] node_abc123 (score: 0.89)
      Content: "Caroline mentioned she loves traveling..."
      Wave: {temporal: 0.2, entity: 0.8, spatial: 0.3}
      PROBLEM: No Paris, no date!

  [2] node_def456 (score: 0.85)
      Content: "Paris is beautiful in spring..."
      Wave: {temporal: 0.1, spatial: 0.9, entity: 0.2}
      PROBLEM: No Caroline!

  [3] node_ghi789 (score: 0.82)
      Content: "On March 15th, Caroline arrived in Paris..."  <-- TARGET!
      Wave: {temporal: 0.9, entity: 0.7, spatial: 0.8}
      MATCH: Has Caroline + Paris + Date!

================================================================================
ELECTRON PROPAGATION (Step by Step)
================================================================================
  Step 0: Inject at seeds
    - Electron e1 at node_abc123, energy=1.0, phase=0
    - Electron e2 at node_def456, energy=1.0, phase=0
    - Electron e3 at node_ghi789, energy=1.0, phase=0  <-- TARGET

  Step 1: First propagation
    - e1 → node_xxx (energy: 1.0 → 0.72, decay: 0.28)
      Edge weight: 0.76, resonance: 0.95
    - e2 → node_yyy (energy: 1.0 → 0.31, decay: 0.69)  <-- HEAVY DECAY
      Edge weight: 0.45, resonance: 0.72
    - e3 → node_zzz (energy: 1.0 → 0.89, decay: 0.11)  <-- STRONG!
      Edge weight: 0.92, resonance: 0.98

  Step 2: ...

================================================================================
CHARGE ACCUMULATION
================================================================================
  node_ghi789: charge=2.45 (FIRED at step 3)
    - From e3: +1.0 (initial injection)
    - From e7: +0.89 (propagated from node_zzz)
    - From e12: +0.56 (propagated from node_www)

  node_abc123: charge=0.34 (below threshold 0.5)
    - From e1: +0.34 (weak return)
    PROBLEM: Never accumulated enough charge!

================================================================================
BACKPROPAGATION ANALYSIS (Napoleon's View)
================================================================================
  TARGET NODE: node_ghi789 "On March 15th, Caroline arrived in Paris..."

  WHY DID IT FIRE?
    ✓ Strong initial seed (vector similarity: 0.82)
    ✓ High temporal wave amplitude (0.9) matched query domain (TEMPORAL)
    ✓ Entity match: "Caroline" in content
    ✓ Spatial match: "Paris" in content

  WHY DID OTHERS FAIL?
    ✗ node_abc123: No temporal marker, no Paris
    ✗ node_def456: No Caroline, weak temporal signal
    ✗ node_jkl012: Had Caroline but no temporal info - DECAYED

  DECAY ANALYSIS:
    Electrons from node_jkl012 lost 68% energy by hop 3 because:
    - Edge to temporal nodes had weight 0.3 (weak connection)
    - Resonance factor was 0.4 (temporal mismatch)
    - Wave signature drifted away from temporal dimension

================================================================================
RECOMMENDATIONS
================================================================================
  1. Node node_jkl012 needs stronger temporal edges
  2. Wave amplitude for "temporal" should be higher on date nodes
  3. Entity "Caroline" should create direct edges to her activities
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data_types import NeuralNode, RetrievalResult
    from .electron import Electron, AccumulatedCharge, ElectronPool
    from .storage import NeuralGraphStorage


# =============================================================================
# DEBUG TRACE STRUCTURES
# =============================================================================

@dataclass
class ElectronTrace:
    """Trace of a single electron's journey through the network."""
    electron_id: str
    source_node_id: str
    path: list[str] = field(default_factory=list)
    energy_history: list[float] = field(default_factory=list)
    phase_history: list[float] = field(default_factory=list)
    wave_signature_history: list[dict[str, float]] = field(default_factory=list)
    decay_reasons: list[str] = field(default_factory=list)
    resonance_factors: list[float] = field(default_factory=list)
    edge_weights: list[float] = field(default_factory=list)
    final_destination: str = ""
    died_at_step: int = -1
    death_reason: str = ""

    def total_decay(self) -> float:
        """Calculate total energy decay."""
        if len(self.energy_history) < 2:
            return 0.0
        return self.energy_history[0] - self.energy_history[-1]

    def decay_percentage(self) -> float:
        """Calculate percentage of energy lost."""
        if len(self.energy_history) < 2 or self.energy_history[0] == 0:
            return 0.0
        return (self.total_decay() / self.energy_history[0]) * 100


@dataclass
class NodeChargeTrace:
    """Trace of charge accumulation at a node."""
    node_id: str
    node_content: str = ""
    node_wave_amplitudes: dict[str, float] = field(default_factory=dict)

    # Charge history
    charge_contributions: list[tuple[str, float, int]] = field(default_factory=list)  # (electron_id, charge, step)
    total_excitatory: float = 0.0
    total_inhibitory: float = 0.0

    # Firing info
    fired: bool = False
    fired_at_step: int = -1
    final_charge: float = 0.0

    # Analysis
    entity_matches: list[str] = field(default_factory=list)
    temporal_markers: list[str] = field(default_factory=list)
    spatial_markers: list[str] = field(default_factory=list)

    @property
    def net_charge(self) -> float:
        return self.total_excitatory - abs(self.total_inhibitory)


@dataclass
class PropagationStep:
    """Record of a single propagation step."""
    step_number: int
    active_electrons: int
    new_electrons: int
    electrons_died: int
    nodes_fired: list[str] = field(default_factory=list)
    total_charge_in_system: float = 0.0


@dataclass
class BackpropAnalysis:
    """Napoleon's backpropagation analysis - trace from answer back to query."""
    target_node_id: str
    target_content: str
    target_found: bool
    target_rank: int

    # Why it succeeded/failed
    success_factors: list[str] = field(default_factory=list)
    failure_factors: list[str] = field(default_factory=list)

    # Decay analysis
    critical_decay_points: list[dict[str, Any]] = field(default_factory=list)

    # Recommendations
    recommendations: list[str] = field(default_factory=list)


@dataclass
class RetrievalDebugReport:
    """Complete debug report for a retrieval operation."""
    # Query info
    query_text: str
    query_domain: str = ""
    query_seeking: str = ""
    query_subjects: list[str] = field(default_factory=list)
    query_objects: list[str] = field(default_factory=list)
    query_actions: list[str] = field(default_factory=list)
    context_amplitudes: dict[str, float] = field(default_factory=dict)

    # Seed analysis
    seed_nodes: list[dict[str, Any]] = field(default_factory=list)

    # Propagation trace
    propagation_steps: list[PropagationStep] = field(default_factory=list)
    electron_traces: list[ElectronTrace] = field(default_factory=list)

    # Charge accumulation
    node_charges: list[NodeChargeTrace] = field(default_factory=list)

    # Final results
    fired_nodes: list[str] = field(default_factory=list)
    result_nodes: list[dict[str, Any]] = field(default_factory=list)

    # Backpropagation analysis
    backprop: BackpropAnalysis | None = None

    # Expected answer (for analysis)
    expected_answer: str = ""
    got_correct: bool = False

    def to_string(self) -> str:
        """Generate human-readable debug report."""
        lines = []
        lines.append("=" * 80)
        lines.append("RETRIEVAL DEBUG REPORT - THE NAPOLEON VIEW")
        lines.append("=" * 80)

        # Query decomposition
        lines.append("\n" + "=" * 80)
        lines.append("QUERY DECOMPOSITION")
        lines.append("=" * 80)
        lines.append(f"  Original: \"{self.query_text}\"")
        lines.append(f"  Domain: {self.query_domain}")
        lines.append(f"  Seeking: {self.query_seeking}")
        lines.append(f"  Subjects: {self.query_subjects}")
        lines.append(f"  Objects: {self.query_objects}")
        lines.append(f"  Actions: {self.query_actions}")
        lines.append(f"  Context Amplitudes: {self.context_amplitudes}")

        # Seed nodes
        lines.append("\n" + "=" * 80)
        lines.append("SEED NODES (Entry Points)")
        lines.append("=" * 80)
        for i, seed in enumerate(self.seed_nodes[:10]):
            lines.append(f"  [{i+1}] {seed.get('node_id', 'unknown')[:12]}... (score: {seed.get('score', 0):.3f})")
            content = seed.get('content', '')[:80]
            lines.append(f"      Content: \"{content}...\"")
            lines.append(f"      Wave: {seed.get('wave_amplitudes', {})}")
            # Analysis
            has_subject = any(s.lower() in content.lower() for s in self.query_subjects) if self.query_subjects else False
            has_object = any(o.lower() in content.lower() for o in self.query_objects) if self.query_objects else False
            if has_subject and has_object:
                lines.append(f"      ✓ MATCH: Has subjects and objects!")
            elif not has_subject and self.query_subjects:
                lines.append(f"      ✗ PROBLEM: Missing subjects {self.query_subjects}")
            elif not has_object and self.query_objects:
                lines.append(f"      ✗ PROBLEM: Missing objects {self.query_objects}")

        # Propagation summary
        lines.append("\n" + "=" * 80)
        lines.append("PROPAGATION SUMMARY")
        lines.append("=" * 80)
        for step in self.propagation_steps[:10]:
            lines.append(f"  Step {step.step_number}: {step.active_electrons} active, {step.new_electrons} new, {step.electrons_died} died")
            if step.nodes_fired:
                lines.append(f"    FIRED: {step.nodes_fired}")

        # Top electron traces
        lines.append("\n" + "=" * 80)
        lines.append("ELECTRON TRACES (Top 5 by energy)")
        lines.append("=" * 80)
        sorted_traces = sorted(self.electron_traces, key=lambda t: t.energy_history[0] if t.energy_history else 0, reverse=True)
        for trace in sorted_traces[:5]:
            lines.append(f"  Electron {trace.electron_id[:8]}...")
            lines.append(f"    Source: {trace.source_node_id[:12]}...")
            lines.append(f"    Path length: {len(trace.path)} hops")
            lines.append(f"    Energy: {trace.energy_history[0] if trace.energy_history else 0:.3f} → {trace.energy_history[-1] if trace.energy_history else 0:.3f}")
            lines.append(f"    Decay: {trace.decay_percentage():.1f}%")
            if trace.death_reason:
                lines.append(f"    Death: {trace.death_reason} at step {trace.died_at_step}")

        # Charge accumulation
        lines.append("\n" + "=" * 80)
        lines.append("CHARGE ACCUMULATION (Top 10)")
        lines.append("=" * 80)
        sorted_charges = sorted(self.node_charges, key=lambda n: n.net_charge, reverse=True)
        for nc in sorted_charges[:10]:
            status = "FIRED ✓" if nc.fired else f"charge={nc.net_charge:.3f}"
            lines.append(f"  {nc.node_id[:12]}... ({status})")
            lines.append(f"    Content: \"{nc.node_content[:60]}...\"")
            lines.append(f"    Contributions: {len(nc.charge_contributions)} electrons")
            if nc.entity_matches:
                lines.append(f"    Entity matches: {nc.entity_matches}")

        # Backpropagation analysis
        if self.backprop:
            lines.append("\n" + "=" * 80)
            lines.append("BACKPROPAGATION ANALYSIS (Napoleon's View)")
            lines.append("=" * 80)
            lines.append(f"  Target: \"{self.backprop.target_content[:60]}...\"")
            lines.append(f"  Found: {self.backprop.target_found} (rank: {self.backprop.target_rank})")
            lines.append("")
            lines.append("  SUCCESS FACTORS:")
            for f in self.backprop.success_factors:
                lines.append(f"    ✓ {f}")
            lines.append("")
            lines.append("  FAILURE FACTORS:")
            for f in self.backprop.failure_factors:
                lines.append(f"    ✗ {f}")
            lines.append("")
            lines.append("  CRITICAL DECAY POINTS:")
            for dp in self.backprop.critical_decay_points:
                lines.append(f"    - {dp}")
            lines.append("")
            lines.append("  RECOMMENDATIONS:")
            for r in self.backprop.recommendations:
                lines.append(f"    → {r}")

        # Final verdict
        lines.append("\n" + "=" * 80)
        lines.append("FINAL VERDICT")
        lines.append("=" * 80)
        if self.expected_answer:
            lines.append(f"  Expected: \"{self.expected_answer}\"")
        lines.append(f"  Got correct: {self.got_correct}")
        lines.append(f"  Total nodes fired: {len(self.fired_nodes)}")
        lines.append(f"  Results returned: {len(self.result_nodes)}")

        lines.append("\n" + "=" * 80)

        return "\n".join(lines)


# =============================================================================
# RETRIEVAL DEBUGGER
# =============================================================================

class RetrievalDebugger:
    """Debug retrieval operations step by step.

    THE NAPOLEON PRINCIPLE:
    Don't just see the result - trace every movement that led to it.
    Understand where energy was lost, where connections failed,
    where the answer should have been found but wasn't.
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self._storage = storage
        self._current_report: RetrievalDebugReport | None = None
        self._electron_traces: dict[str, ElectronTrace] = {}
        self._node_charges: dict[str, NodeChargeTrace] = {}
        self._step_count = 0

    def start_debug(self, query_text: str, expected_answer: str = "") -> None:
        """Start debugging a new retrieval operation."""
        self._current_report = RetrievalDebugReport(
            query_text=query_text,
            expected_answer=expected_answer,
        )
        self._electron_traces = {}
        self._node_charges = {}
        self._step_count = 0

    def record_query_decomposition(
        self,
        domain: str,
        seeking: str,
        subjects: list[str],
        objects: list[str],
        actions: list[str],
        context_amplitudes: dict[str, float],
    ) -> None:
        """Record query decomposition results."""
        if not self._current_report:
            return
        self._current_report.query_domain = domain
        self._current_report.query_seeking = seeking
        self._current_report.query_subjects = subjects
        self._current_report.query_objects = objects
        self._current_report.query_actions = actions
        self._current_report.context_amplitudes = context_amplitudes

    async def record_seed_nodes(
        self,
        seeds: list[tuple["NeuralNode", float]],
    ) -> None:
        """Record seed node selection."""
        if not self._current_report:
            return

        for node, score in seeds:
            self._current_report.seed_nodes.append({
                "node_id": node.node_id,
                "content": node.content,
                "score": score,
                "wave_amplitudes": node.wave_amplitudes if hasattr(node, 'wave_amplitudes') else {},
            })

    def record_electron_injection(
        self,
        electron_id: str,
        source_node_id: str,
        initial_energy: float,
        initial_phase: float,
        wave_signature: dict[str, float],
    ) -> None:
        """Record electron injection at a seed node."""
        trace = ElectronTrace(
            electron_id=electron_id,
            source_node_id=source_node_id,
            path=[source_node_id],
            energy_history=[initial_energy],
            phase_history=[initial_phase],
            wave_signature_history=[wave_signature.copy()],
        )
        self._electron_traces[electron_id] = trace

    def record_electron_propagation(
        self,
        electron_id: str,
        from_node: str,
        to_node: str,
        energy_before: float,
        energy_after: float,
        edge_weight: float,
        resonance_factor: float,
        new_phase: float,
        new_wave_signature: dict[str, float],
    ) -> None:
        """Record an electron propagating through an edge."""
        if electron_id not in self._electron_traces:
            return

        trace = self._electron_traces[electron_id]
        trace.path.append(to_node)
        trace.energy_history.append(energy_after)
        trace.phase_history.append(new_phase)
        trace.wave_signature_history.append(new_wave_signature.copy())
        trace.edge_weights.append(edge_weight)
        trace.resonance_factors.append(resonance_factor)

        # Calculate decay reason
        decay = energy_before - energy_after
        decay_pct = (decay / energy_before) * 100 if energy_before > 0 else 0
        if decay_pct > 30:
            if edge_weight < 0.5:
                trace.decay_reasons.append(f"Weak edge ({edge_weight:.2f}) at hop {len(trace.path)-1}")
            if resonance_factor < 0.7:
                trace.decay_reasons.append(f"Low resonance ({resonance_factor:.2f}) at hop {len(trace.path)-1}")

    def record_electron_death(
        self,
        electron_id: str,
        reason: str,  # "energy_floor", "max_hops", "refractory"
    ) -> None:
        """Record when an electron dies."""
        if electron_id not in self._electron_traces:
            return
        trace = self._electron_traces[electron_id]
        trace.died_at_step = self._step_count
        trace.death_reason = reason
        if trace.path:
            trace.final_destination = trace.path[-1]

    async def record_charge_accumulation(
        self,
        node_id: str,
        electron_id: str,
        charge_amount: float,
        is_excitatory: bool,
    ) -> None:
        """Record charge accumulation at a node."""
        if node_id not in self._node_charges:
            node = await self._storage.get_node(node_id)
            self._node_charges[node_id] = NodeChargeTrace(
                node_id=node_id,
                node_content=node.content if node else "",
                node_wave_amplitudes=node.wave_amplitudes if node and hasattr(node, 'wave_amplitudes') else {},
            )

            # Analyze content for entities
            if node and self._current_report:
                content_lower = node.content.lower() if node.content else ""
                for subj in self._current_report.query_subjects:
                    if subj.lower() in content_lower:
                        self._node_charges[node_id].entity_matches.append(subj)
                for obj in self._current_report.query_objects:
                    if obj.lower() in content_lower:
                        self._node_charges[node_id].entity_matches.append(obj)

        nc = self._node_charges[node_id]
        nc.charge_contributions.append((electron_id, charge_amount, self._step_count))

        if is_excitatory:
            nc.total_excitatory += charge_amount
        else:
            nc.total_inhibitory += charge_amount

    def record_node_firing(self, node_id: str, charge: float) -> None:
        """Record when a node fires."""
        if node_id in self._node_charges:
            nc = self._node_charges[node_id]
            nc.fired = True
            nc.fired_at_step = self._step_count
            nc.final_charge = charge

        if self._current_report:
            self._current_report.fired_nodes.append(node_id)

    def record_propagation_step(
        self,
        active_electrons: int,
        new_electrons: int,
        electrons_died: int,
        nodes_fired: list[str],
    ) -> None:
        """Record a propagation step."""
        step = PropagationStep(
            step_number=self._step_count,
            active_electrons=active_electrons,
            new_electrons=new_electrons,
            electrons_died=electrons_died,
            nodes_fired=nodes_fired,
        )
        if self._current_report:
            self._current_report.propagation_steps.append(step)
        self._step_count += 1

    async def record_final_results(
        self,
        results: list[tuple["NeuralNode", float]],
    ) -> None:
        """Record final retrieval results."""
        if not self._current_report:
            return

        for node, score in results:
            self._current_report.result_nodes.append({
                "node_id": node.node_id,
                "content": node.content,
                "score": score,
            })

    def analyze_backprop(self, target_content: str = "") -> BackpropAnalysis:
        """Perform Napoleon's backpropagation analysis.

        Trace backward from the expected answer to understand:
        - Why did it succeed/fail to be retrieved?
        - Where did energy decay?
        - What connections were missing?
        """
        if not self._current_report:
            return BackpropAnalysis(
                target_node_id="",
                target_content=target_content,
                target_found=False,
                target_rank=-1,
            )

        # Find target in results
        target_found = False
        target_rank = -1
        target_node_id = ""

        target_lower = target_content.lower() if target_content else ""
        expected_lower = self._current_report.expected_answer.lower() if self._current_report.expected_answer else ""

        for i, result in enumerate(self._current_report.result_nodes):
            content = result.get("content", "").lower()
            if target_lower and target_lower in content:
                target_found = True
                target_rank = i + 1
                target_node_id = result.get("node_id", "")
                break
            elif expected_lower and expected_lower in content:
                target_found = True
                target_rank = i + 1
                target_node_id = result.get("node_id", "")
                break

        analysis = BackpropAnalysis(
            target_node_id=target_node_id,
            target_content=target_content or self._current_report.expected_answer,
            target_found=target_found,
            target_rank=target_rank,
        )

        # Analyze success/failure factors
        if target_found:
            analysis.success_factors.append(f"Found at rank {target_rank}")

            # Check if it was a seed
            for seed in self._current_report.seed_nodes:
                if seed.get("node_id") == target_node_id:
                    analysis.success_factors.append(f"Was a seed node (score: {seed.get('score', 0):.3f})")
                    break

            # Check charge accumulation
            if target_node_id in self._node_charges:
                nc = self._node_charges[target_node_id]
                if nc.fired:
                    analysis.success_factors.append(f"Node fired with charge {nc.final_charge:.3f}")
                if nc.entity_matches:
                    analysis.success_factors.append(f"Entity matches: {nc.entity_matches}")
        else:
            analysis.failure_factors.append("Target not found in results")

            # Analyze why
            # Check if it was even a seed
            was_seed = False
            for seed in self._current_report.seed_nodes:
                if target_lower in seed.get("content", "").lower():
                    was_seed = True
                    analysis.failure_factors.append(f"Was a seed but didn't make final results")
                    break

            if not was_seed:
                analysis.failure_factors.append("Never selected as seed (vector similarity too low?)")

            # Check for entity mismatches
            if self._current_report.query_subjects:
                analysis.failure_factors.append(f"Required entities: {self._current_report.query_subjects}")

        # Find critical decay points
        for trace in self._electron_traces.values():
            if trace.decay_percentage() > 50:
                analysis.critical_decay_points.append({
                    "electron": trace.electron_id[:8],
                    "decay": f"{trace.decay_percentage():.1f}%",
                    "reasons": trace.decay_reasons[:3],
                })

        # Generate recommendations
        if not target_found:
            if not was_seed:
                analysis.recommendations.append("Increase seed count or improve embedding quality")
            analysis.recommendations.append("Check if temporal/entity edges exist to target node")
            if self._current_report.query_domain == "temporal":
                analysis.recommendations.append("Boost temporal wave amplitude for 'when' queries")
        elif target_rank > 5:
            analysis.recommendations.append("Target found but ranked low - boost entity matching")
            analysis.recommendations.append("Consider stronger temporal resonance for date nodes")

        return analysis

    def finish_debug(self) -> RetrievalDebugReport:
        """Finish debugging and return the complete report."""
        if not self._current_report:
            return RetrievalDebugReport(query_text="")

        # Add electron traces
        self._current_report.electron_traces = list(self._electron_traces.values())

        # Add node charges
        self._current_report.node_charges = list(self._node_charges.values())

        # Run backprop analysis
        self._current_report.backprop = self.analyze_backprop()

        # Check if got correct
        if self._current_report.expected_answer:
            expected_lower = self._current_report.expected_answer.lower()
            for result in self._current_report.result_nodes[:5]:
                if expected_lower in result.get("content", "").lower():
                    self._current_report.got_correct = True
                    break

        return self._current_report


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

async def debug_retrieval(
    query: str,
    expected_answer: str,
    storage: "NeuralGraphStorage",
    retriever: Any,
    session_key: str,
    embedding: list[float],
) -> RetrievalDebugReport:
    """Convenience function to debug a single retrieval.

    Returns a full debug report showing exactly how the answer was formed.
    """
    debugger = RetrievalDebugger(storage)
    debugger.start_debug(query, expected_answer)

    # The retriever needs to be instrumented to call debugger methods
    # This is a simplified version - full instrumentation would be in the retriever

    result = await retriever.retrieve(
        query_text=query,
        query_embedding=embedding,
        session_key=session_key,
    )

    await debugger.record_final_results(result.nodes)

    return debugger.finish_debug()

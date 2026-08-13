"""Deterministic tests for the Tesseract coordination vertical slice."""

from __future__ import annotations

import unittest
from dataclasses import replace

from NeuralGraph.coordination.adapters import (
    MockMemoryNodeAdapter,
    NeuralGraphMemoryAdapter,
    PrivateMemoryRecord,
)
from NeuralGraph.coordination.contracts import (
    AuthorizationContext,
    Availability,
    CapabilityDescriptor,
    ClaimEnvelope,
    InterventionTarget,
    LearningDisposition,
    LearningSignal,
    PolicyStatus,
    QueryRequest,
    TraceEventType,
)
from NeuralGraph.coordination.core import (
    ClaimNormalizer,
    LineageAnalyzer,
    RepairPlanner,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
    canonical_json,
)
from NeuralGraph.coordination.experiment import run_experiment, run_strategy
from NeuralGraph.coordination.fixture import (
    REQUIRED_SCOPE,
    REQUIRED_SLOTS,
    build_runtime,
    generate_fixture,
)


def make_coordinator(runtime):
    return TesseractCoordinator(
        runtime.registry,
        Router(runtime.registry, runtime.failure),
        ClaimNormalizer(),
        RuleBasedSynthesizer(REQUIRED_SLOTS),
        LineageAnalyzer(),
        runtime.traces,
    )


class AdapterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_node_can_only_query_its_own_private_records(self):
        runtime = build_runtime(11, "boundary")
        exports = await runtime.registry.adapter_for("node-a").query(runtime.fixture.query)
        claims = [claim for export in exports for claim in export.claims]
        self.assertEqual({claim.producer_node_id for claim in claims}, {"node-a"})
        self.assertEqual({claim.content["slot"] for claim in claims}, {"left"})
        self.assertNotIn(runtime.fixture.right_symbol, canonical_json(exports))

    async def test_coordination_export_contains_contracts_not_graph_objects(self):
        runtime = build_runtime(12, "boundary")
        execution = await make_coordinator(runtime).execute(runtime.fixture.query, "boundary")
        self.assertTrue(all(isinstance(claim, ClaimEnvelope) for claim in execution.claims))
        payload = canonical_json(execution.exports)
        self.assertNotIn("NeuralNode", payload)
        self.assertNotIn("NeuralEdge", payload)
        self.assertNotIn("embedding", payload)

    async def test_denied_evidence_is_not_exported(self):
        runtime = build_runtime(13, "boundary")
        denied = replace(
            runtime.fixture.query,
            authorization=AuthorizationContext(scopes=("wrong-scope",)),
        )
        exports = await runtime.registry.adapter_for("node-a").query(denied)
        self.assertTrue(exports)
        self.assertTrue(all(not export.claims for export in exports))
        self.assertTrue(all(export.trace.policy_status is PolicyStatus.DENIED for export in exports))

    async def test_redaction_does_not_leak_content_or_trace_metadata(self):
        runtime = build_runtime(14, "boundary")
        protected = "PROTECTED_VALUE_9YQ"
        record = PrivateMemoryRecord(
            memory_id="secret-memory", capability_id="secret-capability",
            slot="secret-slot", value=protected, confidence=0.8,
            source_ids=("secret-source",), parent_memory_ids=("secret-parent",),
            lineage_root_ids=("secret-root",), failure_domains=("secret-domain",),
            edge_path=("secret-edge",), required_scope="secret-scope",
            policy_mode=PolicyStatus.REDACTED,
        )
        adapter = MockMemoryNodeAdapter(
            "redacted-node", (record,), runtime.failure, runtime.clock.now
        )
        request = replace(
            runtime.fixture.query,
            requested_capability="secret-capability",
            authorization=AuthorizationContext(
                scopes=("secret-scope",), allowed_node_ids=("redacted-node",),
                allow_redacted=True,
            ),
        )
        exports = await adapter.query(request)
        payload = canonical_json(exports)
        for secret in (
            protected, "secret-memory", "secret-source", "secret-parent",
            "secret-root", "secret-domain", "secret-edge",
        ):
            self.assertNotIn(secret, payload)
        self.assertEqual(exports[0].claims[0].content["value"], "[REDACTED]")
        self.assertEqual(exports[0].trace.memory_ids, ())

    async def test_learning_requires_receiver_decision_and_never_auto_commits(self):
        runtime = build_runtime(15, "boundary")
        signal = LearningSignal(
            signal_id="signal-1", sender_node_id="node-a", receiver_node_id="node-d",
            proposed_content={"candidate": "value"}, evidence_refs=("evidence-1",),
        )
        decision = await runtime.registry.adapter_for("node-d").propose_learning(signal)
        self.assertEqual(decision.disposition, LearningDisposition.TENTATIVE)
        self.assertIsNone(decision.committed_memory_id)
        mismatch = await runtime.registry.adapter_for("node-a").propose_learning(signal)
        self.assertEqual(mismatch.disposition, LearningDisposition.REJECTED)

    async def test_real_adapter_does_not_invent_missing_lineage(self):
        runtime = build_runtime(16, "boundary")

        class LocalNode:
            node_id = "local-memory-1"
            content = "locally held fact"
            source_memory_ids = ["known-source-memory"]

        async def retrieve(_request):
            return [(LocalNode(), 0.88)]

        capability = CapabilityDescriptor(
            capability_id=runtime.fixture.query.requested_capability,
            node_id="real-node",
            description="local NeuralGraph retrieval",
            query_types=("composition",),
            policy_scope=(REQUIRED_SCOPE,),
            availability=Availability.AVAILABLE,
        )
        adapter = NeuralGraphMemoryAdapter(
            "real-node", capability, retrieve,
            lambda _node, _authorization: PolicyStatus.ALLOWED,
            runtime.clock.now,
        )
        exports = await adapter.query(runtime.fixture.query)
        claim = exports[0].claims[0]
        self.assertEqual(claim.source_ids, ("known-source-memory",))
        self.assertEqual(claim.lineage_root_ids, ())
        self.assertEqual(claim.failure_domains, ())
        self.assertEqual(exports[0].trace.edge_path, ())


class FailureAndLineageTests(unittest.IsolatedAsyncioTestCase):
    async def _claims(self, runtime, preferred=("node-a", "node-b", "node-c")):
        return (await make_coordinator(runtime).execute(
            runtime.fixture.query, "test", preferred
        )).claims

    async def test_correlated_copies_collapse_to_one_independent_root_coalition(self):
        runtime = build_runtime(21, "lineage")
        execution = await make_coordinator(runtime).execute(runtime.fixture.query, "before")
        self.assertEqual(execution.metrics.apparent_replica_count, 3)
        self.assertEqual(execution.metrics.unique_lineage_root_count, 2)
        self.assertEqual(execution.metrics.reconstruction_coalition_count, 1)
        self.assertEqual(execution.metrics.minimal_support_coalition_size, 2)
        self.assertEqual(execution.metrics.minimal_failure_domain_cut, 1)

    async def test_independent_root_remains_distinct(self):
        runtime = build_runtime(22, "lineage")
        execution = await make_coordinator(runtime).execute(
            runtime.fixture.query, "independent", ("node-a", "node-c", "node-d")
        )
        self.assertEqual(execution.metrics.unique_lineage_root_count, 3)
        self.assertEqual(execution.metrics.unique_failure_domain_count, 3)
        self.assertEqual(execution.metrics.reconstruction_coalition_count, 2)

    async def test_removing_shared_root_invalidates_every_dependent_copy(self):
        runtime = build_runtime(23, "lineage")
        runtime.failure.apply(InterventionTarget.LINEAGE_ROOT, "R2", "test")
        execution = await make_coordinator(runtime).execute(runtime.fixture.query, "failed")
        self.assertEqual({root for claim in execution.claims for root in claim.lineage_root_ids}, {"R1"})
        self.assertFalse(execution.synthesis.success)
        blocked = [
            event for event in runtime.traces.events
            if event.event_type is TraceEventType.POLICY_BLOCK
        ]
        self.assertEqual({event.details["node_id"] for event in blocked}, {"node-b", "node-c"})

    async def test_all_failure_toggles_are_reversible_and_non_destructive(self):
        cases = (
            (InterventionTarget.NODE, "node-b"),
            (InterventionTarget.MEMORY, "memory-b"),
            (InterventionTarget.SOURCE, "source-2"),
            (InterventionTarget.LINEAGE_ROOT, "R2"),
            (InterventionTarget.EDGE, "edge-b-right"),
            (InterventionTarget.AUTHORIZATION_SCOPE, REQUIRED_SCOPE),
            (InterventionTarget.EVIDENCE, "memory-b"),
        )
        for index, (target_type, target_id) in enumerate(cases):
            runtime = build_runtime(30 + index, "failure")
            adapter = runtime.registry.adapter_for("node-b")
            runtime.failure.apply(target_type, target_id, "test")
            blocked = await adapter.query(runtime.fixture.query)
            self.assertTrue(all(not export.claims for export in blocked), target_type)
            runtime.failure.clear(target_type, target_id)
            restored = await adapter.query(runtime.fixture.query)
            self.assertTrue(any(export.claims for export in restored), target_type)
            self.assertIsNotNone(runtime.failure.records[0].ended_at)

        runtime = build_runtime(39, "route")
        runtime.failure.apply(InterventionTarget.ROUTE, "node-b", "test")
        route = await Router(runtime.registry, runtime.failure).select(runtime.fixture.query)
        self.assertNotIn("node-b", route)
        runtime.failure.clear(InterventionTarget.ROUTE, "node-b")
        restored_route = await Router(runtime.registry, runtime.failure).select(runtime.fixture.query)
        self.assertIn("node-b", restored_route)


class EndToEndExperimentTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_loss_and_independent_recovery(self):
        result = await run_strategy(20260813, "lineage_targeted_repair")
        phases = result["phases"]
        self.assertTrue(phases["before_failure"]["synthesis"]["success"])
        self.assertFalse(phases["after_failure"]["synthesis"]["success"])
        self.assertTrue(phases["after_repair"]["synthesis"]["success"])
        self.assertEqual(result["repair"]["selected_node_id"], "node-d")
        self.assertFalse(result["repair"]["verification_results"][0]["valid"])
        self.assertEqual(result["repair"]["verification_results"][0]["lineage_root_ids"], ["R2"])
        self.assertEqual(result["repair"]["verification_results"][0]["reason"], "correlated support")
        self.assertEqual(result["repair"]["verification_results"][1]["lineage_root_ids"], ["R3"])
        self.assertTrue(all(result["privacy_assertions"].values()))

    async def test_replica_count_repair_does_not_restore_capability(self):
        result = await run_strategy(20260813, "replica_count")
        self.assertIsNone(result["repair"]["selected_node_id"])
        self.assertFalse(result["phases"]["after_repair"]["synthesis"]["success"])
        self.assertTrue(all(
            verification["lineage_root_ids"] == ["R2"]
            for verification in result["repair"]["verification_results"]
        ))

    async def test_trace_explains_route_failure_selection_and_repair(self):
        result = await run_strategy(55, "lineage_targeted_repair")
        types = [event["event_type"] for event in result["trace_events"]]
        for expected in (
            "route_selected", "failure_injected", "policy_block",
            "verification_requested", "verification_result", "repair_completed",
            "capability_evaluated",
        ):
            self.assertIn(expected, types)
        failure = next(event for event in result["trace_events"] if event["event_type"] == "failure_injected")
        self.assertEqual(failure["details"]["target_id"], "R2")
        repaired = next(event for event in result["trace_events"] if event["event_type"] == "repair_completed")
        self.assertEqual(repaired["details"]["selected_node_id"], "node-d")

    async def test_same_seed_replays_exactly(self):
        first = await run_experiment(101)
        second = await run_experiment(101)
        self.assertEqual(canonical_json(first), canonical_json(second))

    async def test_different_seeds_rotate_symbols_but_preserve_structure(self):
        first = generate_fixture(101, "seed")
        second = generate_fixture(102, "seed")
        self.assertNotEqual(first.expected_answer, second.expected_answer)
        self.assertEqual(tuple(first.records_by_node), tuple(second.records_by_node))
        one = await run_experiment(101)
        two = await run_experiment(102)
        self.assertEqual(one["strategy_summary"], two["strategy_summary"])
        self.assertNotIn(first.left_symbol, first.query.content)
        self.assertNotIn(first.right_symbol, first.query.content)


if __name__ == "__main__":
    unittest.main()

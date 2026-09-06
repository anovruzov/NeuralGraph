"""Deterministic tests for the Tesseract coordination vertical slice."""

from __future__ import annotations

import json
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
    CapabilityRegistry,
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


REAL_NODE_ID = "real-node"


class LocalNodeStub:
    """Stand-in for a locally owned NeuralGraph node.

    Mirrors the attribute names `NeuralGraphMemoryAdapter` reads from a real
    `NeuralNode` (`node_id`, `content`, `source_memory_ids`) plus the two payload
    fields that must never cross the boundary (`metadata`, `embedding`).
    """

    def __init__(
        self,
        node_id="local-memory-1",
        content="locally held fact",
        source_memory_ids=(),
        metadata=None,
        embedding=None,
    ):
        self.node_id = node_id
        self.content = content
        self.source_memory_ids = list(source_memory_ids)
        self.metadata = {} if metadata is None else metadata
        self.embedding = [] if embedding is None else embedding


class NotAMemoryNode:
    """Locally retrieved object without the identifier the adapter requires."""

    content = "malformed payload MUST_NOT_LEAK_2F8"


def allow_everything(_node, _authorization):
    return PolicyStatus.ALLOWED


def make_real_capability(runtime, node_id=REAL_NODE_ID):
    return CapabilityDescriptor(
        capability_id=runtime.fixture.query.requested_capability,
        node_id=node_id,
        description="local NeuralGraph retrieval",
        query_types=("composition",),
        policy_scope=(REQUIRED_SCOPE,),
        availability=Availability.AVAILABLE,
    )


def make_real_adapter(runtime, retrieve, policy=allow_everything, **kwargs):
    return NeuralGraphMemoryAdapter(
        REAL_NODE_ID,
        make_real_capability(runtime),
        retrieve,
        policy,
        runtime.clock.now,
        **kwargs,
    )


def retrieve_all(*nodes, confidence=0.9):
    async def retrieve(_request):
        return [(node, confidence) for node in nodes]

    return retrieve


class RealAdapterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Boundary behaviour of the adapter that faces real NeuralGraph storage."""

    async def test_query_for_another_capability_retrieves_and_exports_nothing(self):
        runtime = build_runtime(41, "real-adapter")
        calls = []

        async def retrieve(request):
            calls.append(request.query_id)
            return [(LocalNodeStub(), 0.9)]

        adapter = make_real_adapter(runtime, retrieve)
        for requested in ("some-other-capability", None):
            with self.subTest(requested_capability=requested):
                mismatched = replace(runtime.fixture.query, requested_capability=requested)
                self.assertEqual(await adapter.query(mismatched), ())
        self.assertEqual(calls, [])
        matched = await adapter.query(runtime.fixture.query)
        self.assertEqual(len(matched), 1)
        self.assertEqual(calls, [runtime.fixture.query.query_id])

    async def test_capability_is_unavailable_to_an_unauthorized_context(self):
        runtime = build_runtime(42, "real-adapter")
        adapter = make_real_adapter(runtime, retrieve_all(LocalNodeStub()))
        capability = make_real_capability(runtime)
        authorized = AuthorizationContext(
            scopes=(REQUIRED_SCOPE,), allowed_node_ids=(REAL_NODE_ID,)
        )
        unauthorized = AuthorizationContext(scopes=("some-other-scope",))
        wrong_node = AuthorizationContext(
            scopes=(REQUIRED_SCOPE,), allowed_node_ids=("node-a",)
        )

        self.assertEqual(await adapter.describe_capabilities(authorized), (capability,))
        for context in (unauthorized, wrong_node):
            with self.subTest(context=context.scopes + context.allowed_node_ids):
                descriptors = await adapter.describe_capabilities(context)
                self.assertEqual(len(descriptors), 1)
                self.assertIs(descriptors[0].availability, Availability.UNAVAILABLE)
                self.assertEqual(descriptors[0].capability_id, capability.capability_id)
                self.assertEqual(descriptors[0].policy_scope, capability.policy_scope)

    async def test_router_excludes_a_node_that_is_unavailable_to_the_requester(self):
        runtime = build_runtime(43, "real-adapter")
        registry = CapabilityRegistry()
        registry.register(make_real_adapter(runtime, retrieve_all(LocalNodeStub())))
        router = Router(registry, runtime.failure)
        authorized = replace(
            runtime.fixture.query,
            authorization=AuthorizationContext(
                scopes=(REQUIRED_SCOPE,), allowed_node_ids=(REAL_NODE_ID,)
            ),
        )
        unauthorized = replace(
            runtime.fixture.query,
            authorization=AuthorizationContext(scopes=("some-other-scope",)),
        )

        self.assertEqual(await router.select(authorized), (REAL_NODE_ID,))
        self.assertEqual(await router.select(unauthorized), ())

    async def test_trace_id_stays_bound_to_its_memory_under_reversed_order(self):
        runtime = build_runtime(44, "real-adapter")
        first = LocalNodeStub("local-memory-1", "fact one")
        second = LocalNodeStub("local-memory-2", "fact two")
        retrieved = [(first, 0.91), (second, 0.82)]

        async def retrieve(_request):
            return list(retrieved)

        adapter = make_real_adapter(runtime, retrieve)
        forward = await adapter.query(runtime.fixture.query)
        retrieved.reverse()
        backward = await adapter.query(runtime.fixture.query)

        forward_binding = {
            export.trace.memory_ids[0]: export.trace.trace_id for export in forward
        }
        backward_binding = {
            export.trace.memory_ids[0]: export.trace.trace_id for export in backward
        }
        self.assertEqual(sorted(forward_binding), ["local-memory-1", "local-memory-2"])
        self.assertEqual(len(set(forward_binding.values())), 2)
        self.assertEqual(forward_binding, backward_binding)

    async def test_trace_id_is_memory_bound_on_allowed_and_denied_paths(self):
        runtime = build_runtime(45, "real-adapter")
        node = LocalNodeStub("local-memory-1", "fact one")
        expected = MockMemoryNodeAdapter._opaque_id(
            "trace", runtime.fixture.query.query_id, REAL_NODE_ID, "local-memory-1"
        )

        allowed = await make_real_adapter(runtime, retrieve_all(node)).query(
            runtime.fixture.query
        )
        denied = await make_real_adapter(
            runtime,
            retrieve_all(node),
            policy=lambda _node, _authorization: PolicyStatus.DENIED,
        ).query(runtime.fixture.query)

        self.assertEqual(allowed[0].trace.trace_id, expected)
        self.assertEqual(denied[0].trace.trace_id, expected)

    async def test_denied_export_leaks_neither_content_nor_identifiers(self):
        runtime = build_runtime(46, "real-adapter")
        node = LocalNodeStub(
            node_id="PRIVATE_MEMORY_ID_4KQ",
            content="PRIVATE_CONTENT_8ZR",
            source_memory_ids=["PRIVATE_SOURCE_1WB"],
            metadata={"note": "PRIVATE_METADATA_5XT"},
            embedding=[0.9876543, 0.1234567],
        )
        adapter = make_real_adapter(
            runtime,
            retrieve_all(node),
            policy=lambda _node, _authorization: PolicyStatus.DENIED,
        )

        exports = await adapter.query(runtime.fixture.query)
        payload = canonical_json(exports)

        for secret in (
            "PRIVATE_MEMORY_ID_4KQ", "PRIVATE_CONTENT_8ZR", "PRIVATE_SOURCE_1WB",
            "PRIVATE_METADATA_5XT", "0.9876543",
        ):
            self.assertNotIn(secret, payload)
        trace = exports[0].trace
        self.assertEqual(exports[0].claims, ())
        self.assertIs(trace.policy_status, PolicyStatus.DENIED)
        self.assertEqual(
            (trace.memory_ids, trace.source_ids, trace.parent_memory_ids,
             trace.lineage_root_ids, trace.edge_path),
            ((), (), (), (), ()),
        )

    async def test_redacted_policy_fails_closed_without_a_claim(self):
        """Documents current behaviour: only ALLOWED produces a claim."""
        runtime = build_runtime(47, "real-adapter")
        node = LocalNodeStub(content="PRIVATE_CONTENT_8ZR")
        adapter = make_real_adapter(
            runtime,
            retrieve_all(node),
            policy=lambda _node, _authorization: PolicyStatus.REDACTED,
        )

        exports = await adapter.query(runtime.fixture.query)

        self.assertEqual(exports[0].claims, ())
        self.assertIs(exports[0].trace.policy_status, PolicyStatus.REDACTED)
        self.assertEqual(exports[0].trace.memory_ids, ())
        self.assertNotIn("PRIVATE_CONTENT_8ZR", canonical_json(exports))

    async def test_claim_projection_may_not_export_raw_node_state(self):
        runtime = build_runtime(48, "real-adapter")
        secret = "PROTECTED_VALUE_7KQ"
        node = LocalNodeStub(
            metadata={"note": secret}, embedding=[0.9876543, 0.1234567]
        )
        projections = {
            "metadata": lambda n: {"text": str(n.content), "metadata": n.metadata},
            "embedding": lambda n: {"text": str(n.content), "embedding": n.embedding},
            "node": lambda n: {"node": n},
        }

        for key, projection in projections.items():
            with self.subTest(offending_key=key):
                adapter = make_real_adapter(
                    runtime, retrieve_all(node), claim_projection=projection
                )
                with self.assertRaises(ValueError) as raised:
                    await adapter.query(runtime.fixture.query)
                message = f"{raised.exception}|{raised.exception!r}"
                self.assertIn(key, message)
                for leak in (secret, "0.9876543", "locally held fact", "note"):
                    self.assertNotIn(leak, message)

    async def test_claim_content_must_be_a_flat_mapping_of_exportable_scalars(self):
        runtime = build_runtime(49, "real-adapter")
        node = LocalNodeStub(metadata={"note": "PROTECTED_VALUE_7KQ"})

        async def resolver_with_bad_content(local_node):
            return {"claim_content": {"blob": local_node.metadata}}

        rejected = {
            "not_a_dict": {"claim_projection": lambda n: str(n.content)},
            "non_string_key": {"claim_projection": lambda n: {n: "value"}},
            "nested_mapping": {"claim_projection": lambda n: {"nested": {"a": 1}}},
            "resolver_supplied": {"lineage_resolver": resolver_with_bad_content},
        }
        for case, kwargs in rejected.items():
            with self.subTest(case=case):
                adapter = make_real_adapter(runtime, retrieve_all(node), **kwargs)
                with self.assertRaises(ValueError) as raised:
                    await adapter.query(runtime.fixture.query)
                message = f"{raised.exception}|{raised.exception!r}"
                self.assertNotIn("PROTECTED_VALUE_7KQ", message)
                self.assertNotIn("locally held fact", message)

        accepted = {"text": "value", "count": 2, "score": 0.5, "flag": True, "absent": None}
        adapter = make_real_adapter(
            runtime, retrieve_all(node), claim_projection=lambda _n: dict(accepted)
        )
        exports = await adapter.query(runtime.fixture.query)
        self.assertEqual(exports[0].claims[0].content, accepted)

    async def test_default_claim_content_is_the_unchanged_text_projection(self):
        runtime = build_runtime(50, "real-adapter")
        node = LocalNodeStub(content="locally held fact")

        exports = await make_real_adapter(runtime, retrieve_all(node)).query(
            runtime.fixture.query
        )

        self.assertEqual(exports[0].claims[0].content, {"text": "locally held fact"})

    async def test_claim_projection_replaces_the_default_content(self):
        runtime = build_runtime(51, "real-adapter")
        node = LocalNodeStub(content="locally held fact")
        adapter = make_real_adapter(
            runtime,
            retrieve_all(node),
            claim_projection=lambda n: {"slot": "left", "value": str(n.content)[:7]},
        )

        exports = await adapter.query(runtime.fixture.query)

        self.assertEqual(exports[0].claims[0].content, {"slot": "left", "value": "locally"})

    async def test_async_lineage_resolver_is_awaited_and_omissions_stay_empty(self):
        runtime = build_runtime(52, "real-adapter")
        node = LocalNodeStub(source_memory_ids=["known-source-memory"])
        resolved = []

        async def resolver(local_node):
            resolved.append(local_node.node_id)
            return {"lineage_root_ids": ("root-1",), "edge_path": ("edge-1",)}

        adapter = make_real_adapter(runtime, retrieve_all(node), lineage_resolver=resolver)
        exports = await adapter.query(runtime.fixture.query)
        claim = exports[0].claims[0]

        self.assertEqual(resolved, ["local-memory-1"])
        self.assertEqual(claim.lineage_root_ids, ("root-1",))
        self.assertEqual(exports[0].trace.edge_path, ("edge-1",))
        self.assertEqual(claim.source_ids, ("known-source-memory",))
        self.assertEqual(claim.parent_memory_ids, ())
        self.assertEqual(claim.failure_domains, ())

    async def test_object_without_node_id_is_rejected_without_leaking_payload(self):
        runtime = build_runtime(53, "real-adapter")
        policies = {
            "allowed": allow_everything,
            "denied": lambda _node, _authorization: PolicyStatus.DENIED,
        }

        for name, policy in policies.items():
            with self.subTest(policy=name):
                adapter = make_real_adapter(
                    runtime, retrieve_all(NotAMemoryNode()), policy=policy
                )
                with self.assertRaises(TypeError) as raised:
                    await adapter.query(runtime.fixture.query)
                message = f"{raised.exception}|{raised.exception!r}"
                self.assertIn("NotAMemoryNode", message)
                self.assertNotIn("MUST_NOT_LEAK_2F8", message)


class DivergentViewContent(dict):
    """Dict subclass whose ``items()`` view disagrees with its stored pairs.

    An owner-supplied projection is caller-controlled code, so it may hand the
    boundary a mapping that reports one thing and stores another.  Validation
    and export must therefore read the *same* view.
    """

    def items(self):
        return [("text", "sanitized projection")]


class HostileReprKey(str):
    """``str`` subclass smuggling payload through ``repr`` and an attribute."""

    def __new__(cls, value, secret="SECRET_ON_KEY_ATTR_4T"):
        key = super().__new__(cls, value)
        key.smuggled = secret
        return key

    def __repr__(self):
        return "SECRET_FROM_REPR_9Z"


class RealAdapterProjectionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    """The claim payload that crosses the boundary must be the validated one."""

    async def test_exported_content_is_the_validated_view_not_hidden_storage(self):
        runtime = build_runtime(54, "real-adapter")
        smuggling = DivergentViewContent({
            "embedding": [0.9876543, 0.1234567],
            "metadata_note": "PROTECTED_VALUE_7KQ",
        })
        adapter = make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: smuggling,
        )

        exports = await adapter.query(runtime.fixture.query)
        content = exports[0].claims[0].content
        payload = canonical_json(exports)

        self.assertEqual(content, {"text": "sanitized projection"})
        self.assertIs(type(content), dict)
        for leak in (
            "PROTECTED_VALUE_7KQ", "0.9876543", "0.1234567",
            "embedding", "metadata_note",
        ):
            self.assertNotIn(leak, payload)

    async def test_rejection_message_ignores_a_caller_controlled_key_repr(self):
        runtime = build_runtime(55, "real-adapter")
        adapter = make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: {HostileReprKey("text"): object()},
        )

        with self.assertRaises(ValueError) as raised:
            await adapter.query(runtime.fixture.query)

        message = f"{raised.exception}|{raised.exception!r}"
        self.assertNotIn("SECRET_FROM_REPR_9Z", message)
        self.assertIn("'text'", message)
        self.assertIn("object", message)

    async def test_exported_keys_are_plain_strings_carrying_no_hidden_state(self):
        runtime = build_runtime(55, "real-adapter")
        adapter = make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: {HostileReprKey("text"): "sanitized"},
        )

        exports = await adapter.query(runtime.fixture.query)
        content = exports[0].claims[0].content
        (exported_key,) = content

        self.assertEqual(content, {"text": "sanitized"})
        self.assertIs(type(exported_key), str)
        self.assertIsNone(getattr(exported_key, "smuggled", None))
        self.assertNotIn("SECRET_ON_KEY_ATTR_4T", canonical_json(exports))

    async def test_blank_node_identifier_is_rejected_like_a_missing_one(self):
        """A blank id would give two distinct memories one claim_id and trace_id."""
        runtime = build_runtime(56, "real-adapter")

        for label, node_id in (("empty", ""), ("whitespace", "   ")):
            with self.subTest(node_id=label):
                adapter = make_real_adapter(
                    runtime,
                    retrieve_all(LocalNodeStub(
                        node_id=node_id, content="BLANK_ID_CONTENT_3JX"
                    )),
                )
                with self.assertRaises(TypeError) as raised:
                    await adapter.query(runtime.fixture.query)
                message = f"{raised.exception}|{raised.exception!r}"
                self.assertIn("LocalNodeStub", message)
                self.assertNotIn("BLANK_ID_CONTENT_3JX", message)


class HostileReprValue(str):
    """``str`` subclass smuggling payload through ``repr`` and an attribute.

    The value channel is the one that actually carries payload, so a subclass
    reaching an exported claim keeps a hidden reference to owner-side state and
    a ``__repr__`` that any log line, test dump or traceback would render.
    """

    def __new__(cls, value, secret="SECRET_ON_VALUE_ATTR_6R"):
        exported = super().__new__(cls, value)
        exported.smuggled = secret
        return exported

    def __repr__(self):
        return "SECRET_FROM_VALUE_REPR_1M"


class HostileReprInt(int):
    """``int`` subclass smuggling payload through ``repr`` and an attribute.

    ``str`` is not the only exportable type that can be subclassed: ``int`` and
    ``float`` can be too, so type-checking a value proves what it *is* while
    still letting owner-side state ride across the boundary.
    """

    def __new__(cls, value, secret="SECRET_ON_INT_ATTR_3N"):
        exported = super().__new__(cls, value)
        exported.hidden = secret
        return exported

    def __repr__(self):
        return "SECRET_FROM_INT_REPR_7K"


class HostileReprFloat(float):
    """``float`` subclass smuggling payload through ``repr`` and an attribute."""

    def __new__(cls, value, secret="SECRET_ON_FLOAT_ATTR_5W"):
        exported = super().__new__(cls, value)
        exported.hidden = secret
        return exported

    def __repr__(self):
        return "SECRET_FROM_FLOAT_REPR_2H"


class LyingFiniteFloat(float):
    """``float`` subclass whose ``__float__`` hides a non-finite real value."""

    def __float__(self):
        return 1.5


class CollapsingIdentifier(str):
    """``str`` subclass whose ``__str__`` collapses every value to one token."""

    def __str__(self):
        return "COLLAPSED_TO_ONE_ID"


def reject_json_constant(name):
    """``json.loads`` calls this only for the non-RFC constants NaN/Infinity."""
    raise AssertionError(f"non-RFC JSON constant crossed the boundary: {name}")


class RealAdapterValueBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Claim content values are boundary-crossing payload, so they are coerced.

    Type-checking a value proves what it *is*; it does not stop a ``str``
    subclass from carrying hidden owner-side state past the boundary.
    """

    async def test_exported_values_are_plain_strings_carrying_no_hidden_state(self):
        runtime = build_runtime(57, "real-adapter")
        adapter = make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: {"value": HostileReprValue("sanitized")},
        )

        exports = await adapter.query(runtime.fixture.query)
        content = exports[0].claims[0].content
        exported_value = content["value"]

        self.assertEqual(content, {"value": "sanitized"})
        self.assertIs(type(exported_value), str)
        self.assertIsNone(getattr(exported_value, "smuggled", None))
        for marker in ("SECRET_ON_VALUE_ATTR_6R", "SECRET_FROM_VALUE_REPR_1M"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, canonical_json(exports))
                self.assertNotIn(marker, repr(exports))

    async def test_hostile_value_repr_cannot_leak_through_a_coordinated_run(self):
        runtime = build_runtime(57, "real-adapter")
        request = replace(
            runtime.fixture.query,
            authorization=AuthorizationContext(
                scopes=(REQUIRED_SCOPE,), allowed_node_ids=(REAL_NODE_ID,)
            ),
        )
        registry = CapabilityRegistry()
        registry.register(make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: {
                "slot": "left", "value": HostileReprValue("sanitized")
            },
        ))
        coordinator = TesseractCoordinator(
            registry,
            Router(registry, runtime.failure),
            ClaimNormalizer(),
            RuleBasedSynthesizer(REQUIRED_SLOTS),
            LineageAnalyzer(),
            runtime.traces,
        )

        execution = await coordinator.execute(request, "real-adapter")
        value = execution.claims[0].content["value"]

        self.assertIs(type(value), str)
        self.assertIsNone(getattr(value, "smuggled", None))
        for marker in ("SECRET_ON_VALUE_ATTR_6R", "SECRET_FROM_VALUE_REPR_1M"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, repr(execution))
                self.assertNotIn(marker, canonical_json(execution))

    async def test_non_finite_content_values_are_rejected(self):
        """NaN and Infinity are not JSON; a conforming parser rejects them."""
        runtime = build_runtime(58, "real-adapter")
        for label, value in (
            ("nan", float("nan")),
            ("infinity", float("inf")),
            ("negative_infinity", float("-inf")),
        ):
            with self.subTest(value=label):
                adapter = make_real_adapter(
                    runtime,
                    retrieve_all(LocalNodeStub(content="NON_FINITE_CONTENT_5PQ")),
                    claim_projection=lambda _node, value=value: {"score": value},
                )
                with self.assertRaises(ValueError) as raised:
                    await adapter.query(runtime.fixture.query)
                message = f"{raised.exception}|{raised.exception!r}"
                self.assertIn("'score'", message)
                self.assertIn("finite", message)
                self.assertNotIn("NON_FINITE_CONTENT_5PQ", message)

    async def test_finite_scalars_and_bools_still_cross_the_boundary(self):
        """Positive control: coercion rejects hidden state, not ordinary values."""
        runtime = build_runtime(59, "real-adapter")
        payload = {
            "flag": True,
            "off": False,
            "count": 3,
            "score": 0.5,
            "text": "ok",
            "missing": None,
        }
        adapter = make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: dict(payload),
        )

        exports = await adapter.query(runtime.fixture.query)
        content = exports[0].claims[0].content

        self.assertEqual(content, payload)
        self.assertIs(content["flag"], True)
        self.assertIs(content["off"], False)
        self.assertIs(type(content["count"]), int)
        self.assertIs(type(content["score"]), float)
        self.assertIs(type(content["text"]), str)
        # A conforming parser accepts the serialization the boundary produces.
        json.loads(canonical_json(exports), parse_constant=reject_json_constant)

    async def test_exported_int_values_are_plain_ints_carrying_no_hidden_state(self):
        """``str`` is not the only exportable type a projection can subclass.

        Feeding an *exact* ``int`` and asserting ``type(...) is int`` is a
        tautology on a builtin; only a subclass shows whether the boundary
        coerces the value or merely type-checks it.
        """
        runtime = build_runtime(60, "real-adapter")
        adapter = make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: {"count": HostileReprInt(7)},
        )

        exports = await adapter.query(runtime.fixture.query)
        content = exports[0].claims[0].content
        exported_value = content["count"]

        self.assertEqual(content, {"count": 7})
        self.assertIs(type(exported_value), int)
        self.assertIsNone(getattr(exported_value, "hidden", None))
        for marker in ("SECRET_ON_INT_ATTR_3N", "SECRET_FROM_INT_REPR_7K"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, canonical_json(exports))
                self.assertNotIn(marker, repr(exports))
                self.assertNotIn(marker, str(content))

    async def test_exported_float_values_are_plain_floats_carrying_no_hidden_state(self):
        """Same coercion obligation for the other subclassable numeric type."""
        runtime = build_runtime(61, "real-adapter")
        adapter = make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: {"score": HostileReprFloat(1.5)},
        )

        exports = await adapter.query(runtime.fixture.query)
        content = exports[0].claims[0].content
        exported_value = content["score"]

        self.assertEqual(content, {"score": 1.5})
        self.assertIs(type(exported_value), float)
        self.assertIsNone(getattr(exported_value, "hidden", None))
        for marker in ("SECRET_ON_FLOAT_ATTR_5W", "SECRET_FROM_FLOAT_REPR_2H"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, canonical_json(exports))
                self.assertNotIn(marker, repr(exports))
                self.assertNotIn(marker, str(content))

    async def test_hostile_numeric_subclasses_cannot_survive_a_coordinated_run(self):
        """The coercion has to hold through the whole coordinated execution."""
        runtime = build_runtime(62, "real-adapter")
        request = replace(
            runtime.fixture.query,
            authorization=AuthorizationContext(
                scopes=(REQUIRED_SCOPE,), allowed_node_ids=(REAL_NODE_ID,)
            ),
        )
        registry = CapabilityRegistry()
        registry.register(make_real_adapter(
            runtime, retrieve_all(LocalNodeStub()),
            claim_projection=lambda _node: {
                "slot": "left",
                "value": "sanitized",
                "count": HostileReprInt(7),
                "score": HostileReprFloat(1.5),
            },
        ))
        coordinator = TesseractCoordinator(
            registry,
            Router(registry, runtime.failure),
            ClaimNormalizer(),
            RuleBasedSynthesizer(REQUIRED_SLOTS),
            LineageAnalyzer(),
            runtime.traces,
        )

        execution = await coordinator.execute(request, "real-adapter")
        content = execution.claims[0].content

        self.assertEqual((content["count"], content["score"]), (7, 1.5))
        self.assertIs(type(content["count"]), int)
        self.assertIs(type(content["score"]), float)
        for key in ("count", "score"):
            self.assertIsNone(getattr(content[key], "hidden", None))
        for marker in (
            "SECRET_ON_INT_ATTR_3N", "SECRET_FROM_INT_REPR_7K",
            "SECRET_ON_FLOAT_ATTR_5W", "SECRET_FROM_FLOAT_REPR_2H",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, repr(execution))
                self.assertNotIn(marker, canonical_json(execution))
                self.assertNotIn(marker, str(content))

    async def test_float_subclass_hiding_a_non_finite_value_is_still_rejected(self):
        """Coercion must not run ahead of the finiteness check.

        ``math.isfinite`` and ``float.__float__`` both read the real stored
        double, so a subclass advertising a finite ``__float__`` cannot talk the
        boundary into exporting ``NaN`` or ``Infinity``.
        """
        runtime = build_runtime(63, "real-adapter")
        for label, raw in (("nan", "nan"), ("infinity", "inf")):
            with self.subTest(value=label):
                adapter = make_real_adapter(
                    runtime,
                    retrieve_all(LocalNodeStub(content="LYING_FLOAT_CONTENT_9VD")),
                    claim_projection=lambda _node, raw=raw: {
                        "score": LyingFiniteFloat(raw)
                    },
                )
                with self.assertRaises(ValueError) as raised:
                    await adapter.query(runtime.fixture.query)
                message = f"{raised.exception}|{raised.exception!r}"
                self.assertIn("'score'", message)
                self.assertIn("finite", message)
                self.assertNotIn("LYING_FLOAT_CONTENT_9VD", message)

    async def test_collapsing_identifier_str_cannot_merge_two_memories(self):
        """Two memories keep two identities even if ``__str__`` collapses them."""
        runtime = build_runtime(59, "real-adapter")
        adapter = make_real_adapter(runtime, retrieve_all(
            LocalNodeStub(node_id=CollapsingIdentifier("memory-left"), content="LEFT_1"),
            LocalNodeStub(node_id=CollapsingIdentifier("memory-right"), content="RIGHT_1"),
        ))

        exports = await adapter.query(runtime.fixture.query)
        memory_ids = tuple(export.trace.memory_ids[0] for export in exports)

        self.assertEqual(len(exports), 2)
        self.assertEqual(set(memory_ids), {"memory-left", "memory-right"})
        self.assertEqual({type(memory_id) for memory_id in memory_ids}, {str})
        self.assertEqual(len({export.trace.trace_id for export in exports}), 2)
        self.assertEqual(
            len({claim.claim_id for export in exports for claim in export.claims}), 2
        )
        self.assertNotIn("COLLAPSED_TO_ONE_ID", canonical_json(exports))


if __name__ == "__main__":
    unittest.main()

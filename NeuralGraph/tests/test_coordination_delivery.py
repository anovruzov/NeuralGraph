"""Duplicate-delivery behaviour at the coordinator boundary.

A worker that retries, or a transport that re-delivers, can hand the
coordinator the *same* policy-filtered export twice.  The real adapter already
collapses repeats of one memory inside a single node
(``NeuralGraphMemoryAdapter.query``), but nothing above the adapter was covered:
the coordinator receives whatever each node's ``query`` returns, applies the
claim budget, and only then normalizes.

These tests pin down three properties of duplicate delivery:

1. A re-delivered claim is counted once.  Apparent replica count and coalition
   metrics must describe distinct evidence, not transport chatter.
2. A duplicate never evicts a distinct claim from the claim budget.  The budget
   bounds *distinct* evidence the requester pays for; paying twice for one
   memory would silently starve another worker's evidence.
3. The bytes actually transferred are still charged.  ``exported_bytes`` is a
   transport-cost metric and the duplicate really did cross the wire.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from NeuralGraph.coordination.contracts import QueryBudget
from NeuralGraph.coordination.core import (
    ClaimNormalizer,
    LineageAnalyzer,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
)
from NeuralGraph.coordination.fixture import REQUIRED_SLOTS, build_runtime


class RedeliveringAdapter:
    """Wraps a node adapter and delivers every export ``copies`` times.

    This simulates an at-least-once transport or a worker retry.  It is a test
    double only: it changes nothing about what the wrapped node exports, it only
    repeats it.
    """

    def __init__(self, inner, copies: int = 2) -> None:
        self._inner = inner
        self._copies = copies
        self.node_id = inner.node_id

    async def describe_capabilities(self, context):
        return await self._inner.describe_capabilities(context)

    async def query(self, request):
        exports = await self._inner.query(request)
        return tuple(export for export in exports for _ in range(self._copies))

    async def verify(self, request):
        return await self._inner.verify(request)

    async def propose_learning(self, signal):
        return await self._inner.propose_learning(signal)


def _coordinator(runtime):
    return TesseractCoordinator(
        runtime.registry,
        Router(runtime.registry, runtime.failure),
        ClaimNormalizer(),
        RuleBasedSynthesizer(REQUIRED_SLOTS),
        LineageAnalyzer(),
        runtime.traces,
    )


def _wrap(runtime, node_id: str, copies: int = 2) -> None:
    # CapabilityRegistry keeps adapters in a private dict; wrapping in place is
    # the smallest way to inject a duplicating transport for exactly one node.
    adapters = runtime.registry._adapters
    adapters[node_id] = RedeliveringAdapter(adapters[node_id], copies)


class DuplicateDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_redelivered_claim_is_counted_once(self):
        clean = build_runtime(71, "delivery")
        reference = await _coordinator(clean).execute(clean.fixture.query, "clean")

        runtime = build_runtime(71, "delivery")
        _wrap(runtime, "node-b", copies=3)
        execution = await _coordinator(runtime).execute(runtime.fixture.query, "dup")

        self.assertEqual(execution.route, reference.route)
        self.assertEqual(
            [claim.claim_id for claim in execution.claims],
            [claim.claim_id for claim in reference.claims],
        )
        self.assertEqual(execution.synthesis, reference.synthesis)
        self.assertEqual(execution.metrics, reference.metrics)
        self.assertEqual(execution.metrics.apparent_replica_count, 3)
        # Two of the three copies from node-b were duplicates.
        self.assertEqual(execution.duplicate_claim_count, 2)
        self.assertEqual(reference.duplicate_claim_count, 0)

    async def test_duplicate_does_not_evict_a_distinct_claim_from_the_budget(self):
        runtime = build_runtime(72, "delivery")
        _wrap(runtime, "node-a", copies=2)
        query = replace(
            runtime.fixture.query,
            budget=QueryBudget(max_nodes=2, max_claims=2, max_verifications=2),
        )
        execution = await _coordinator(runtime).execute(
            query, "budget", preferred_node_ids=("node-a", "node-b")
        )
        self.assertEqual(execution.route, ("node-a", "node-b"))
        self.assertEqual(
            {claim.producer_node_id for claim in execution.claims}, {"node-a", "node-b"}
        )
        self.assertTrue(execution.synthesis.success)
        self.assertEqual(execution.synthesis.answer, runtime.fixture.expected_answer)
        self.assertEqual(execution.duplicate_claim_count, 1)

    async def test_budget_still_bounds_distinct_claims(self):
        runtime = build_runtime(73, "delivery")
        _wrap(runtime, "node-a", copies=2)
        query = replace(
            runtime.fixture.query,
            budget=QueryBudget(max_nodes=3, max_claims=2, max_verifications=2),
        )
        execution = await _coordinator(runtime).execute(
            query, "bounded", preferred_node_ids=("node-a", "node-b", "node-c")
        )
        # Three distinct claims were offered; the budget admits two, and the
        # duplicate of node-a's claim is not one of them.
        self.assertEqual(len(execution.claims), 2)
        self.assertEqual(execution.duplicate_claim_count, 1)
        self.assertEqual(
            {claim.producer_node_id for claim in execution.claims}, {"node-a", "node-b"}
        )

    async def test_transferred_bytes_are_still_charged_for_duplicates(self):
        clean = build_runtime(74, "delivery")
        reference = await _coordinator(clean).execute(clean.fixture.query, "clean")
        runtime = build_runtime(74, "delivery")
        _wrap(runtime, "node-b", copies=2)
        execution = await _coordinator(runtime).execute(runtime.fixture.query, "dup")
        self.assertGreater(execution.exported_bytes, reference.exported_bytes)
        self.assertEqual(len(execution.exports), len(reference.exports) + 1)
        self.assertEqual(execution.policy_violations, 0)


if __name__ == "__main__":
    unittest.main()

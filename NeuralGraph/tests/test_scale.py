"""Gate 4 (scale) and Gate 5 (generalization) claims, pinned.

The headline benchmark fixes one capability shape. These tests pin what happens
when that shape varies, along both axes that a reviewer would push on: more
premises (arity K) and more holders per premise (replication H), with lineage
independence (I) as the variable actually under test.

The load-bearing assertions here are the *negative* and the *self-critical*
ones. If correlated replication ever starts surviving a lineage-root failure,
the paper's central claim is wrong. And if the conservatism cost of
lineage-aware repair ever silently disappears, the paper would be overclaiming.
"""

from __future__ import annotations

import asyncio
import unittest

from NeuralGraph.coordination.benchmark import DEFAULT_SEED
from NeuralGraph.coordination.scale import (
    CapabilitySpec,
    build_placement,
    route_preference,
    run_point,
    run_scale_sweep,
)

ARITIES = (2, 3, 5, 8)
HOLDERS = (2, 3)


def _point(k: int, h: int, i: int, policy: str, intervention: str):
    return asyncio.run(run_point(CapabilitySpec(k, h, i), policy, intervention))


class GeneralizationTests(unittest.TestCase):
    """Gate 5: the mechanism is not specific to reconstructing a pair."""

    def test_reconstruction_succeeds_for_every_capability_arity(self):
        for k in ARITIES:
            for h in HOLDERS:
                with self.subTest(slots=k, holders=h):
                    point = _point(k, h, 2, "lineage_aware", "node_failure")
                    self.assertTrue(
                        point["baseline_correct"],
                        f"a {k}-premise capability failed to reconstruct at all",
                    )

    def test_answer_requires_every_premise(self):
        """An 8-premise answer must compose 8 distinct values, not repeat one."""
        spec = CapabilitySpec(8, 2, 2)
        placement = build_placement(spec, DEFAULT_SEED, "lineage_aware")
        values = {
            record.value
            for records in placement.records_by_node.values()
            for record in records
        }
        self.assertEqual(len(values), 8)

    def test_bounded_route_covers_every_premise_at_any_arity(self):
        """Interleaved preference is what makes the bounded route viable at scale."""
        for k in ARITIES:
            spec = CapabilitySpec(k, 3, 2)
            preference = route_preference(spec)
            placement = build_placement(spec, DEFAULT_SEED, "lineage_aware")
            reachable = preference[:placement.max_nodes]
            covered = {
                record.slot
                for node in reachable
                for record in placement.records_by_node[node]
            }
            with self.subTest(slots=k):
                self.assertEqual(covered, set(spec.slot_names))


class ReplicaCountBuysNothingAtAnyScaleTests(unittest.TestCase):
    """Gate 4: the central negative claim, swept across K and H."""

    def test_correlated_replicas_never_survive_a_lineage_root_failure(self):
        for k in ARITIES:
            for h in HOLDERS:
                for policy in ("source_count", "lineage_aware"):
                    with self.subTest(slots=k, holders=h, policy=policy):
                        point = _point(k, h, 1, policy, "lineage_root_failure")
                        self.assertTrue(point["baseline_correct"])
                        self.assertFalse(
                            point["capability_survival"],
                            f"correlated replication survived at K={k} H={h}; "
                            "the paper's central claim would be wrong",
                        )

    def test_adding_holders_does_not_help_when_they_share_a_root(self):
        """H=3 is no better than H=2 while I stays 1."""
        for k in ARITIES:
            with self.subTest(slots=k):
                two = _point(k, 2, 1, "lineage_aware", "lineage_root_failure")
                three = _point(k, 3, 1, "lineage_aware", "lineage_root_failure")
                self.assertFalse(two["capability_survival"])
                self.assertFalse(three["capability_survival"])
                self.assertGreater(three["record_count"], two["record_count"])

    def test_independent_roots_restore_survival_at_every_scale(self):
        for k in ARITIES:
            for h in HOLDERS:
                with self.subTest(slots=k, holders=h):
                    point = _point(k, h, 2, "lineage_aware", "lineage_root_failure")
                    self.assertTrue(point["capability_survival"])


class RepairEfficiencyTests(unittest.TestCase):
    def test_lineage_awareness_finds_independent_support_in_fewer_verifications(self):
        """With survival equal, the policies differ in what the search costs."""
        result = asyncio.run(run_scale_sweep(DEFAULT_SEED))
        lineage = result["survival_by_independence"]["lineage_aware::lineage_root_failure::I2"]
        replicas = result["survival_by_independence"]["source_count::lineage_root_failure::I2"]
        self.assertEqual(lineage["survival_rate"], 1.0)
        self.assertEqual(replicas["survival_rate"], 1.0)
        self.assertLess(lineage["mean_recovery_steps"], replicas["mean_recovery_steps"])


class ConservatismCostTests(unittest.TestCase):
    """The honest tradeoff: lineage-aware repair is not free, and not always best.

    Under a plain node failure with correlated replicas, a surviving replica
    would have worked -- the lineage was never the problem, the node was. But
    lineage-aware repair excludes the failed claim's root, so it refuses every
    correlated holder and loses a capability that replica-count repair keeps.

    This is a real cost of treating correlation as disqualifying regardless of
    what actually failed, and it is asserted here so the paper cannot quietly
    present lineage-awareness as dominating on every axis.
    """

    def test_replica_count_beats_lineage_awareness_under_plain_node_failure(self):
        for k in ARITIES:
            for h in HOLDERS:
                with self.subTest(slots=k, holders=h):
                    replicas = _point(k, h, 1, "source_count", "node_failure")
                    lineage = _point(k, h, 1, "lineage_aware", "node_failure")
                    self.assertTrue(
                        replicas["capability_survival"],
                        "a surviving correlated replica should serve a node failure",
                    )
                    self.assertFalse(
                        lineage["capability_survival"],
                        "lineage-aware repair is expected to be over-conservative here; "
                        "if it now survives, the documented tradeoff has changed",
                    )

    def test_the_cost_disappears_once_independent_support_exists(self):
        """The conservatism only bites when there is nothing independent to find."""
        for k in ARITIES:
            with self.subTest(slots=k):
                point = _point(k, 2, 2, "lineage_aware", "node_failure")
                self.assertTrue(point["capability_survival"])


class SweepDeterminismTests(unittest.TestCase):
    def test_sweep_is_deterministic(self):
        first = asyncio.run(run_scale_sweep(DEFAULT_SEED))
        second = asyncio.run(run_scale_sweep(DEFAULT_SEED))
        self.assertEqual(first["points"], second["points"])

    def test_survival_is_invariant_across_scale_in_every_cell(self):
        """Whatever the verdict is, it must not depend on K or H."""
        result = asyncio.run(run_scale_sweep(DEFAULT_SEED))
        for key, row in sorted(result["survival_by_independence"].items()):
            with self.subTest(cell=key):
                self.assertTrue(
                    row["invariant_across_scale"],
                    f"{key} changed verdict across capability shapes",
                )

    def test_cost_grows_with_arity(self):
        result = asyncio.run(run_scale_sweep(DEFAULT_SEED))
        costs = [
            result["cost_by_arity"][f"K{k}"]["mean_bytes_moved"] for k in ARITIES
        ]
        self.assertEqual(costs, sorted(costs), "bytes moved should rise with arity")
        for k in ARITIES:
            with self.subTest(slots=k):
                self.assertTrue(result["cost_by_arity"][f"K{k}"]["all_baselines_correct"])


if __name__ == "__main__":
    unittest.main()

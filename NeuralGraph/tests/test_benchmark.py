"""Tests for the capability-survival benchmark.

These lock the claims the paper rests on.  If a future change makes
`lineage_aware_repair` stop surviving a lineage-root failure, or lets
`full_replication` start surviving one, that is a result changing underneath the
argument and the suite must say so loudly rather than quietly re-baselining.

Several tests here assert *failure* (a strategy losing the capability).  Those
are the load-bearing ones: the paper's claim is that replica count does not buy
survival, so a replica-count strategy that suddenly survived would invalidate
the argument just as surely as a lineage-aware one that stopped.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from NeuralGraph.coordination.benchmark import (
    DEFAULT_SEED,
    INTERVENTIONS,
    STRATEGIES,
    build_runtime,
    generate_symbols,
    observed_lineage,
    order_repair_candidates,
    run_benchmark,
    run_cell,
    run_sweep,
    structural_diversity,
)


def _cell(strategy: str, intervention: str, seed: int = DEFAULT_SEED):
    return asyncio.run(run_cell(seed, strategy, intervention))


class MatrixShapeTests(unittest.TestCase):
    def test_matrix_is_eight_by_nine(self):
        self.assertEqual(len(STRATEGIES), 8)
        self.assertEqual(len(INTERVENTIONS), 9)

    def test_every_cell_runs_and_reports_required_metrics(self):
        result = asyncio.run(run_benchmark(DEFAULT_SEED))
        required = {
            "capability_survival",
            "silent_forgetting",
            "unrecovered_silent_forgetting",
            "recovery_steps",
            "bytes_moved",
            "independent_support_before",
            "structural_diversity",
            "privacy",
            "redundancy_efficiency",
        }
        for strategy in STRATEGIES:
            for intervention in INTERVENTIONS:
                with self.subTest(strategy=strategy, intervention=intervention):
                    cell = result["cells"][f"{strategy}::{intervention}"]
                    self.assertTrue(required.issubset(cell), f"missing keys in {cell.keys()}")

    def test_every_coordinated_strategy_reconstructs_before_intervention(self):
        """A strategy that cannot do the task at all cannot lose it informatively."""
        for strategy in STRATEGIES:
            if strategy == "isolated_local":
                continue
            with self.subTest(strategy=strategy):
                cell = _cell(strategy, "node_failure")
                self.assertTrue(
                    cell["baseline_correct"],
                    f"{strategy} failed to reconstruct before any intervention",
                )

    def test_isolated_local_never_reconstructs(self):
        """The floor: one node holding one premise cannot produce the pair."""
        cell = _cell("isolated_local", "node_failure")
        self.assertFalse(cell["baseline_correct"])
        self.assertFalse(cell["capability_survival"])


class DeterminismTests(unittest.TestCase):
    def test_same_seed_produces_identical_json(self):
        first = json.dumps(asyncio.run(run_benchmark(DEFAULT_SEED)), sort_keys=True)
        second = json.dumps(asyncio.run(run_benchmark(DEFAULT_SEED)), sort_keys=True)
        self.assertEqual(first, second)

    def test_different_seeds_change_symbols_but_not_the_verdicts(self):
        """Survival is structural: it must not depend on which opaque symbols were drawn."""
        base = _cell("lineage_aware_repair", "lineage_root_failure", DEFAULT_SEED)
        other = _cell("lineage_aware_repair", "lineage_root_failure", DEFAULT_SEED + 977)
        self.assertNotEqual(
            generate_symbols(DEFAULT_SEED).left,
            generate_symbols(DEFAULT_SEED + 977).left,
        )
        self.assertEqual(base["capability_survival"], other["capability_survival"])

    def test_sweep_flags_the_stochastic_strategy_and_only_that_one(self):
        result = asyncio.run(run_sweep(DEFAULT_SEED, 12))
        self.assertFalse(result["per_strategy"]["random_path_diversification"]["fully_deterministic"])
        for strategy in STRATEGIES:
            if strategy == "random_path_diversification":
                continue
            with self.subTest(strategy=strategy):
                self.assertTrue(
                    result["per_strategy"][strategy]["fully_deterministic"],
                    f"{strategy} varied across seeds but should be deterministic",
                )


class ReplicaCountIsNotSurvivalTests(unittest.TestCase):
    """The paper's central negative claim, asserted from three directions."""

    def test_three_replicas_do_not_survive_their_shared_root_failing(self):
        cell = _cell("fixed_distributed_replication", "lineage_root_failure")
        self.assertTrue(cell["baseline_correct"])
        self.assertFalse(cell["capability_survival"])

    def test_full_replication_does_not_survive_a_root_failure_either(self):
        """Eight records, twice the storage of any other strategy, and it still dies."""
        cell = _cell("full_replication", "lineage_root_failure")
        self.assertEqual(cell["record_count"], 8)
        self.assertTrue(cell["baseline_correct"])
        self.assertFalse(cell["capability_survival"])

    def test_source_count_repair_spends_its_budget_on_correlated_nodes(self):
        cell = _cell("source_count_repair", "lineage_root_failure")
        self.assertFalse(cell["capability_survival"])
        self.assertGreater(cell["recovery_steps"], 0, "it did attempt a repair")
        self.assertIsNone(cell["repair_selected_node"], "but found no valid support")

    def test_lineage_aware_repair_survives_the_same_failure_at_lower_storage(self):
        lineage = _cell("lineage_aware_repair", "lineage_root_failure")
        replication = _cell("full_replication", "lineage_root_failure")
        self.assertTrue(lineage["capability_survival"])
        self.assertFalse(replication["capability_survival"])
        self.assertLess(lineage["record_count"], replication["record_count"])


class MinCutPredictsSurvivalTests(unittest.TestCase):
    """min-cut is validated as predictive, not assumed."""

    def test_only_the_oracle_placement_has_a_min_cut_above_one(self):
        cuts = {}
        for strategy in STRATEGIES:
            runtime = build_runtime(DEFAULT_SEED, strategy)
            cuts[strategy] = structural_diversity(runtime.placement)["structural_min_cut"]
        self.assertEqual(cuts["oracle_min_cut"], 2)
        for strategy, cut in cuts.items():
            if strategy == "oracle_min_cut":
                continue
            with self.subTest(strategy=strategy):
                self.assertLessEqual(cut, 1)

    def test_worst_single_domain_failure_is_survived_only_by_min_cut_two(self):
        for strategy in STRATEGIES:
            runtime = build_runtime(DEFAULT_SEED, strategy)
            cut = structural_diversity(runtime.placement)["structural_min_cut"]
            cell = _cell(strategy, "worst_single_domain_failure")
            with self.subTest(strategy=strategy, min_cut=cut):
                self.assertEqual(
                    cell["capability_survival"],
                    cut >= 2,
                    f"{strategy} (min-cut {cut}) survival did not match its min-cut prediction",
                )

    def test_the_intervention_is_not_tautological(self):
        """It fails one domain, never the whole cut -- otherwise nothing could survive."""
        cell = _cell("oracle_min_cut", "worst_single_domain_failure")
        applications = cell["intervention_plan"]["applications"]
        self.assertTrue(applications, "the intervention must actually do something")

        struck_roots = {app["target_id"] for app in applications}
        runtime = build_runtime(DEFAULT_SEED, "oracle_min_cut")
        struck_domains = {
            domain
            for records in runtime.placement.records_by_node.values()
            for record in records
            if struck_roots & set(record.lineage_root_ids)
            for domain in record.failure_domains
        }

        # Exactly one domain, and strictly fewer than the min-cut. Failing a
        # whole cut would sever every coalition by definition, so every strategy
        # would score zero and the cell would carry no information at all.
        self.assertEqual(len(struck_domains), 1)
        cut_size = structural_diversity(runtime.placement)["structural_min_cut"]
        self.assertLess(
            len(struck_domains), cut_size,
            "the intervention struck a full cut, making the result tautological",
        )


class RepairPolicyBoundaryTests(unittest.TestCase):
    """Repair policies may use exported contracts only, never node-private records."""

    def test_observed_lineage_comes_only_from_exported_claims(self):
        runtime = build_runtime(DEFAULT_SEED, "lineage_aware_repair")
        baseline = asyncio.run(runtime.coordinator.execute(
            runtime.query, phase="before_intervention",
            preferred_node_ids=runtime.placement.node_ids,
        ))
        lineage = observed_lineage(baseline)
        # node-d is never contacted under the bounded route, so a policy that
        # reads only exported claims cannot know it exists.  If this ever starts
        # including node-d, the route bound has silently changed and the whole
        # repair comparison is void.
        self.assertNotIn("node-d", lineage)
        contacted = set(baseline.route)
        self.assertTrue(set(lineage).issubset(contacted))

    def test_lineage_aware_ordering_demotes_correlated_candidates(self):
        lineage = {
            "node-b": (frozenset({"R2"}), frozenset({"FD2"})),
            "node-c": (frozenset({"R2"}), frozenset({"FD2"})),
            "node-d": (frozenset({"R3"}), frozenset({"FD3"})),
        }
        ordered = order_repair_candidates(
            "lineage_aware", ("node-b", "node-c", "node-d"), lineage, ("R2",), ("FD2",), 1,
        )
        self.assertEqual(ordered[0], "node-d")

    def test_source_count_ordering_promotes_correlated_candidates(self):
        """The intuition under test: 'more copies means safer' ranks replicas first."""
        lineage = {
            "node-b": (frozenset({"R2"}), frozenset({"FD2"})),
            "node-c": (frozenset({"R2"}), frozenset({"FD2"})),
            "node-d": (frozenset({"R3"}), frozenset({"FD3"})),
        }
        ordered = order_repair_candidates(
            "source_count", ("node-b", "node-c", "node-d"), lineage, (), (), 1,
        )
        self.assertEqual(ordered[-1], "node-d")

    def test_ordering_is_stable_for_deterministic_policies(self):
        lineage = {node: (frozenset({"R1"}), frozenset({"FD1"})) for node in ("n1", "n2", "n3")}
        first = order_repair_candidates("lineage_aware", ("n3", "n1", "n2"), lineage, (), (), 5)
        second = order_repair_candidates("lineage_aware", ("n1", "n2", "n3"), lineage, (), (), 9)
        self.assertEqual(first, second)


class PrivacyTests(unittest.TestCase):
    def test_centralized_holds_the_full_answer_and_is_flagged(self):
        cell = _cell("centralized", "node_failure")
        self.assertTrue(cell["privacy"]["single_node_holds_full_answer"])

    def test_distributed_strategies_never_let_one_node_hold_the_answer(self):
        for strategy in (
            "fixed_distributed_replication",
            "source_count_repair",
            "random_path_diversification",
            "lineage_aware_repair",
            "oracle_min_cut",
        ):
            with self.subTest(strategy=strategy):
                cell = _cell(strategy, "node_failure")
                self.assertFalse(cell["privacy"]["single_node_holds_full_answer"])

    def test_no_strategy_leaks_the_answer_into_the_query_or_a_single_memory(self):
        for strategy in STRATEGIES:
            with self.subTest(strategy=strategy):
                cell = _cell(strategy, "node_failure")
                self.assertFalse(cell["privacy"]["answer_present_in_query"])
                self.assertFalse(cell["privacy"]["complete_answer_in_a_single_memory"])

    def test_denied_responses_never_carry_payload(self):
        """Interventions produce denials; none of them may carry structural identifiers."""
        for intervention in INTERVENTIONS:
            with self.subTest(intervention=intervention):
                cell = _cell("lineage_aware_repair", intervention)
                self.assertEqual(cell["privacy"]["payload_bearing_denials"], 0)
                self.assertEqual(cell["privacy"]["policy_violations"], 0)


class SilentForgettingTests(unittest.TestCase):
    def test_a_system_that_never_had_the_capability_has_not_forgotten_it(self):
        cell = _cell("isolated_local", "node_failure")
        self.assertFalse(cell["baseline_correct"])
        self.assertFalse(cell["silent_forgetting"])

    def test_centralized_failure_is_silent_and_unrecovered(self):
        cell = _cell("centralized", "lineage_root_failure")
        self.assertTrue(cell["baseline_correct"])
        self.assertFalse(cell["capability_survival"])
        self.assertTrue(cell["unrecovered_silent_forgetting"])

    def test_confidence_exceeds_coverage_when_the_capability_is_lost(self):
        """The signature of collective forgetting: high confidence, missing support."""
        cell = _cell("fixed_distributed_replication", "lineage_root_failure")
        self.assertFalse(cell["capability_survival"])
        self.assertGreater(cell["post_intervention_confidence"], cell["post_intervention_coverage"])
        self.assertGreater(cell["confidence_valid_support_gap"], 0.0)

    def test_transient_and_unrecovered_forgetting_are_distinguished(self):
        repaired = _cell("lineage_aware_repair", "lineage_root_failure")
        self.assertTrue(repaired["capability_survival"])
        self.assertFalse(repaired["unrecovered_silent_forgetting"])


class OrderingOfResultsTests(unittest.TestCase):
    def test_survival_is_monotone_in_lineage_sophistication(self):
        result = asyncio.run(run_sweep(DEFAULT_SEED, 6))
        rate = {s: result["per_strategy"][s]["capability_survival_rate"] for s in STRATEGIES}
        self.assertLess(rate["isolated_local"], rate["centralized"])
        self.assertLess(rate["centralized"], rate["fixed_distributed_replication"])
        self.assertLessEqual(rate["fixed_distributed_replication"], rate["random_path_diversification"])
        self.assertLess(rate["random_path_diversification"], rate["lineage_aware_repair"])
        self.assertLess(rate["lineage_aware_repair"], rate["oracle_min_cut"])

    def test_lineage_awareness_beats_full_replication_on_both_axes(self):
        """Higher survival at lower storage is the Pareto claim, asserted directly."""
        result = asyncio.run(run_sweep(DEFAULT_SEED, 6))
        lineage = result["per_strategy"]["lineage_aware_repair"]
        replication = result["per_strategy"]["full_replication"]
        self.assertGreater(
            lineage["capability_survival_rate"], replication["capability_survival_rate"]
        )
        self.assertLess(lineage["mean_bytes_moved"], replication["mean_bytes_moved"])


if __name__ == "__main__":
    unittest.main()

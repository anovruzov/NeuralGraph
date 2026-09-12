"""Pinned-artifact regression: committed results must be byte-reproducible.

Every number in the paper comes from the JSON in
``NeuralGraph/coordination/artifacts/``.  These tests regenerate each artifact
from the canonical command and compare it byte for byte against what is
committed, so a silent change in benchmark semantics cannot quietly restate the
paper's results.

If one of these fails, the correct response is never to refresh the artifact
until the suite is green again.  It is to work out *why* the output moved, and
then re-pin deliberately with the reason recorded in the commit message.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from pathlib import Path

from NeuralGraph.coordination.benchmark import run_benchmark, run_sweep
from NeuralGraph.coordination.experiment import run_experiment
from NeuralGraph.coordination.scale import run_scale_sweep

ARTIFACTS = Path(__file__).resolve().parents[1] / "coordination" / "artifacts"
PINNED_SEED = 20260813


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serialize(result: dict) -> str:
    """Exactly what the CLIs write, so a comparison is meaningful."""
    return json.dumps(result, sort_keys=True, indent=2) + "\n"


def _expected_sums() -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in (ARTIFACTS / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split()
        sums[name.lstrip("*")] = digest
    return sums


class ArtifactReproducibilityTests(unittest.TestCase):
    def test_sha256sums_covers_every_committed_json_artifact(self):
        on_disk = {path.name for path in ARTIFACTS.glob("*.json")}
        # SHA256SUMS also pins the rendered figures (see test_figures.py); this
        # test owns the JSON half of it.
        recorded = {name for name in _expected_sums() if not name.startswith("figures/")}
        self.assertEqual(on_disk, recorded)

    def test_committed_files_match_their_recorded_digests(self):
        for name, digest in sorted(_expected_sums().items()):
            with self.subTest(artifact=name):
                actual = hashlib.sha256(
                    (ARTIFACTS / name).read_bytes()
                ).hexdigest()
                self.assertEqual(actual, digest, f"{name} does not match SHA256SUMS")

    def test_experiment_artifact_regenerates_byte_identically(self):
        regenerated = _serialize(asyncio.run(run_experiment(PINNED_SEED)))
        committed = (ARTIFACTS / f"experiment_seed{PINNED_SEED}.json").read_text(encoding="utf-8")
        self.assertEqual(
            _sha256(regenerated), _sha256(committed),
            "the experiment no longer reproduces its pinned artifact",
        )

    def test_benchmark_artifact_regenerates_byte_identically(self):
        regenerated = _serialize(asyncio.run(run_benchmark(PINNED_SEED)))
        committed = (ARTIFACTS / f"benchmark_seed{PINNED_SEED}.json").read_text(encoding="utf-8")
        self.assertEqual(
            _sha256(regenerated), _sha256(committed),
            "the benchmark no longer reproduces its pinned artifact",
        )

    def test_scale_artifact_regenerates_byte_identically(self):
        regenerated = _serialize(asyncio.run(run_scale_sweep(PINNED_SEED)))
        committed = (ARTIFACTS / f"scale_seed{PINNED_SEED}.json").read_text(encoding="utf-8")
        self.assertEqual(
            _sha256(regenerated), _sha256(committed),
            "the scale sweep no longer reproduces its pinned artifact",
        )

    def test_sweep_artifact_regenerates_byte_identically(self):
        regenerated = _serialize(asyncio.run(run_sweep(PINNED_SEED, 30)))
        committed = (
            ARTIFACTS / f"benchmark_sweep30_seed{PINNED_SEED}.json"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            _sha256(regenerated), _sha256(committed),
            "the 30-seed sweep no longer reproduces its pinned artifact",
        )


class HeadlineNumbersTests(unittest.TestCase):
    """The specific figures a reader would quote, read back from the artifact."""

    @classmethod
    def setUpClass(cls):
        cls.sweep = json.loads(
            (ARTIFACTS / f"benchmark_sweep30_seed{PINNED_SEED}.json").read_text(encoding="utf-8")
        )

    def test_replica_count_strategies_score_zero_on_lineage_root_failure(self):
        for strategy in ("fixed_distributed_replication", "source_count_repair", "full_replication"):
            with self.subTest(strategy=strategy):
                self.assertEqual(
                    self.sweep["survival_matrix"][strategy]["lineage_root_failure"], 0.0
                )

    def test_lineage_aware_repair_scores_one_on_lineage_root_failure(self):
        self.assertEqual(
            self.sweep["survival_matrix"]["lineage_aware_repair"]["lineage_root_failure"], 1.0
        )

    def test_random_diversification_lands_near_its_theoretical_two_thirds(self):
        """Independent node in the first two of three candidates: 2/3.

        Agreement with the closed-form value is the check that the harness is
        measuring the mechanism it claims to measure, rather than an artefact.
        """
        observed = self.sweep["survival_matrix"]["random_path_diversification"][
            "lineage_root_failure"
        ]
        self.assertAlmostEqual(observed, 2 / 3, delta=0.15)

    def test_only_the_oracle_survives_the_worst_single_domain_failure(self):
        column = {
            strategy: self.sweep["survival_matrix"][strategy]["worst_single_domain_failure"]
            for strategy in self.sweep["strategies"]
        }
        self.assertEqual(column["oracle_min_cut"], 1.0)
        for strategy, rate in column.items():
            if strategy == "oracle_min_cut":
                continue
            with self.subTest(strategy=strategy):
                self.assertEqual(rate, 0.0)

    def test_no_distributed_strategy_survives_a_partition_that_isolates_one_holder(self):
        """The honest cost of distribution, stated as a test so it cannot be dropped."""
        column = self.sweep["survival_matrix"]
        for strategy in (
            "fixed_distributed_replication",
            "source_count_repair",
            "random_path_diversification",
            "lineage_aware_repair",
            "oracle_min_cut",
        ):
            with self.subTest(strategy=strategy):
                self.assertEqual(column[strategy]["network_partition"], 0.0)
        self.assertEqual(column["centralized"]["network_partition"], 1.0)


if __name__ == "__main__":
    unittest.main()

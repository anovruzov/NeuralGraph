import csv
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sensitivity_sweep import AXES, ORDER, paired_verdict, run_cell, summarize, comparisons


class SensitivityHarnessTests(unittest.TestCase):
    def test_axes_cover_the_protocol_parameters(self):
        required = {
            "corrupt_root_prob",
            "root_zipf_exponent",
            "question_budget_per_claim",
            "min_independent_roots",
            "candidate_pool_size",
        }
        self.assertEqual(required, set(AXES))

    def test_every_axis_includes_its_base_value(self):
        with open(ROOT / "configs" / "fast.json") as f:
            base = json.load(f)
        for axis, values in AXES.items():
            self.assertIn(base[axis], values, axis)

    def test_base_cell_reproduces_the_main_sweep(self):
        """The sweep harness must reuse the simulator's seeding exactly.

        Otherwise a sensitivity cell at the base configuration would not be
        comparable with the headline table.
        """
        raw = ROOT / "results" / "raw_runs.csv"
        if not raw.exists():
            self.skipTest("results/raw_runs.csv not present; run run_fast.sh first")
        with open(ROOT / "configs" / "fast.json") as f:
            cfg = json.load(f)
        with open(raw, newline="") as f:
            expected = {
                r["strategy"]: float(r["accuracy"])
                for r in csv.DictReader(f)
                if int(r["agent_scale"]) == 100 and int(r["seed"]) == 0
            }
        if not expected:
            self.skipTest("no matching rows in results/raw_runs.csv")
        cell = run_cell(cfg, 100, 0)
        for strategy, accuracy in expected.items():
            self.assertAlmostEqual(accuracy, cell[strategy]["accuracy"], places=12, msg=strategy)

    def test_paired_verdict_orientation(self):
        # Lower is better for a harm metric: a negative raw delta is a win.
        mu, sem, verdict = paired_verdict([-0.05, -0.04, -0.06, -0.05], direction=-1)
        self.assertGreater(mu, 0)
        self.assertEqual(verdict, "win")
        # The same deltas on a higher-is-better metric are a loss.
        _, _, verdict = paired_verdict([-0.05, -0.04, -0.06, -0.05], direction=1)
        self.assertEqual(verdict, "loss")
        # Noise straddling zero must not be called either way.
        _, _, verdict = paired_verdict([0.05, -0.05, 0.04, -0.06], direction=1)
        self.assertEqual(verdict, "tie")

    def test_sweep_rows_summarize_and_compare(self):
        with open(ROOT / "configs" / "smoke.json") as f:
            cfg = dict(json.load(f))
        cfg["n_claims"] = 60
        cfg["strategies"] = ORDER
        rows = []
        for value in AXES["corrupt_root_prob"][:2]:
            c = dict(cfg)
            c["corrupt_root_prob"] = value
            for seed in (0, 1, 2):
                for strategy, metrics in run_cell(c, 100, seed).items():
                    rows.append({
                        "evidence_class": "synthetic_simulation",
                        "axis": "corrupt_root_prob",
                        "axis_value": value,
                        "is_base_value": int(value == 0.14),
                        "agent_scale": 100,
                        "seed": seed,
                        "strategy": strategy,
                        **metrics,
                    })
        summary = summarize(rows)
        self.assertEqual(len(summary), 2 * len(ORDER))
        for r in summary:
            self.assertEqual(r["runs"], 3)
            self.assertTrue(0.0 <= r["accuracy_mean"] <= 1.0)

        comps = comparisons(rows)
        # 2 focus policies x 4 peers x 4 metrics x 2 cells
        self.assertEqual(len(comps), 2 * 4 * 4 * 2)
        for c in comps:
            self.assertIn(c["verdict"], {"win", "tie", "loss"})
            self.assertEqual(c["n_seeds"], 3)
            self.assertNotEqual(c["focus"], c["baseline"])


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from continual_discovery_sim import generate_claim, aggregate, run_strategy


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ROOT / "configs" / "smoke.json") as f:
            cls.cfg = json.load(f)

    def test_deterministic_generation(self):
        a = generate_claim(np.random.default_rng(123), 100, self.cfg)
        b = generate_claim(np.random.default_rng(123), 100, self.cfg)
        self.assertEqual(a.truth, b.truth)
        self.assertTrue(np.array_equal(a.root_ids, b.root_ids))
        self.assertTrue(np.array_equal(a.answers, b.answers))
        self.assertTrue(np.array_equal(a.initial_idx, b.initial_idx))

    def test_aggregate_bounds(self):
        w = generate_claim(np.random.default_rng(7), 100, self.cfg)
        p, c, r, x = aggregate(w, w.initial_idx.tolist())
        self.assertIn(p, (0,1))
        self.assertTrue(0.5 <= c <= 1.0)
        self.assertTrue(r >= 1)
        self.assertTrue(0.0 <= x <= 0.5)

    def test_strategy_metrics_are_valid(self):
        rng = np.random.default_rng(11)
        worlds = [generate_claim(rng, 100, self.cfg) for _ in range(40)]
        for idx, s in enumerate(self.cfg["strategies"]):
            m = run_strategy(worlds, s, np.random.default_rng(100+idx), self.cfg)
            for key in ["accuracy","false_confident_consensus_rate","conflict_detection_rate","resolved_gap_rate"]:
                self.assertTrue(0.0 <= m[key] <= 1.0, (s,key,m[key]))
            self.assertTrue(0.0 <= m["independent_roots_per_question"] <= 1.0)
            self.assertTrue(0.0 <= m["questions_per_claim"] <= self.cfg["question_budget_per_claim"])

    def test_none_asks_zero_questions(self):
        rng = np.random.default_rng(5)
        worlds = [generate_claim(rng, 100, self.cfg) for _ in range(25)]
        m = run_strategy(worlds, "none", np.random.default_rng(1), self.cfg)
        self.assertEqual(m["total_questions"], 0)


if __name__ == "__main__":
    unittest.main()

"""The figures must agree with the pinned artifacts, and stay deterministic.

A figure that drifts from the tables is worse than no figure: it looks like
evidence. These tests re-emit both figures and check every plotted value against
the artifact it claims to come from.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tools.build_paper_figures import ARTIFACTS, SWEEP, build, figure1, figure3, verify_digests

FIGDIR = Path("docs/paper/figures")


class DigestTests(unittest.TestCase):
    def test_pinned_artifacts_verify_before_any_figure_is_emitted(self):
        self.assertEqual(verify_digests(), [])


class DeterminismTests(unittest.TestCase):
    def test_figures_are_byte_identical_on_re_emit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            written = build(Path(tmp))
            for path in written:
                committed = FIGDIR / path.name
                self.assertTrue(committed.exists(), f"{path.name} not committed")
                self.assertEqual(path.read_text(), committed.read_text(),
                                 f"{path.name} drifted from the committed copy")


class Figure3ValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sweep = json.loads((ARTIFACTS / SWEEP).read_text(encoding="utf-8"))
        cls.svg = figure3(cls.sweep)

    def test_every_plotted_survival_matches_the_artifact(self):
        plotted = dict(re.findall(r">([a-z_]+)\s+(\d\.\d{3})<", self.svg))
        self.assertEqual(len(plotted), 8, "expected all eight strategies plotted")
        for name, value in plotted.items():
            expected = self.sweep["per_strategy"][name]["capability_survival_rate"]
            self.assertAlmostEqual(float(value), expected, places=3, msg=name)

    def test_the_oracle_is_marked_as_an_upper_bound(self):
        self.assertIn("upper bound", self.svg)

    def test_no_hand_typed_number_survives_an_artifact_change(self):
        """Mutating the artifact in memory must move the figure."""
        mutated = json.loads(json.dumps(self.sweep))
        mutated["per_strategy"]["lineage_aware_repair"]["capability_survival_rate"] = 0.123
        self.assertIn("0.123", figure3(mutated))


class Figure1ContentTests(unittest.TestCase):
    def test_names_the_shared_root_and_the_independent_one(self):
        svg = figure1()
        for token in ("node-a", "node-b", "node-c", "node-d", "R2", "R3", "FD2", "FD3"):
            self.assertIn(token, svg, token)

    def test_states_the_min_cut_conclusion(self):
        self.assertIn("minimum failure-domain cut is 2", figure1())

    def test_matches_the_fixture_source(self):
        """If the fixture's roots change, the figure must not keep claiming the
        old structure."""
        source = Path("NeuralGraph/coordination/fixture.py").read_text(encoding="utf-8")
        self.assertIn('"source-2-copy"', source)
        self.assertIn('"R2"', source)
        self.assertIn('"R3"', source)


if __name__ == "__main__":
    unittest.main()

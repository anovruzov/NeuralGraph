"""The paper's figures must be regenerable, byte-stable, and true to the data.

A figure is a result. `docs/PAPER.md` puts figure 3 at the centre of the
argument, so a figure that drifts from the artifact it plots is the same kind of
defect as a benchmark whose numbers moved silently -- except harder to catch,
because nobody diffs a picture. These tests close that gap: they regenerate
every figure, compare it byte for byte against what is committed, and check the
values drawn in the SVG against the artifact fields they came from.

If one fails, the response is never to refresh the SVG until it is green. Find
out why the drawing moved, then re-pin deliberately with the reason in the
commit message -- the same rule as `test_artifact_reproducibility.py`.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from NeuralGraph.coordination import figures

ARTIFACTS = Path(__file__).resolve().parents[1] / "coordination" / "artifacts"
FIGURE_DIR = ARTIFACTS / "figures"


def _artifact(name: str) -> dict:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def _texts(svg: str) -> list[str]:
    return re.findall(r"<text[^>]*>([^<]*)</text>", svg)


class DeclaredOrderTests(unittest.TestCase):
    """The figures declare their own axis order, so it can fall out of sync."""

    def test_intervention_order_covers_the_benchmark_exactly(self):
        sweep = _artifact("benchmark_sweep30_seed20260813.json")
        self.assertEqual(
            sorted(figures.INTERVENTION_ORDER), sorted(sweep["interventions"]),
            "an intervention was added to the benchmark but not to the figure",
        )

    def test_strategy_order_covers_the_benchmark_exactly(self):
        sweep = _artifact("benchmark_sweep30_seed20260813.json")
        self.assertEqual(sorted(figures.STRATEGY_ORDER), sorted(sweep["strategies"]))

    def test_every_strategy_has_a_short_name(self):
        self.assertEqual(sorted(figures.SHORT_NAME), sorted(figures.STRATEGY_ORDER))

    def test_strategies_are_ordered_by_survival(self):
        """The heatmap's staircase is a claim about ordering, not a coincidence."""
        sweep = _artifact("benchmark_sweep30_seed20260813.json")
        rates = [
            sweep["per_strategy"][strategy]["capability_survival_rate"]
            for strategy in figures.STRATEGY_ORDER
        ]
        self.assertEqual(rates, sorted(rates))


class DeterminismTests(unittest.TestCase):
    def test_rendering_twice_is_byte_identical(self):
        self.assertEqual(figures.render_all(), figures.render_all())

    def test_committed_figures_match_a_fresh_render(self):
        for name, svg in sorted(figures.render_all().items()):
            with self.subTest(figure=name):
                path = FIGURE_DIR / name
                self.assertTrue(path.exists(), f"{name} is not committed")
                self.assertEqual(
                    path.read_text(encoding="utf-8"), svg,
                    f"{name} no longer matches the artifact it plots",
                )

    def test_check_mode_passes_against_the_committed_figures(self):
        self.assertEqual(figures.main(["--check"]), 0)

    def test_no_host_dependent_content(self):
        """No path, timestamp or version string may reach the output."""
        for name, svg in sorted(figures.render_all().items()):
            with self.subTest(figure=name):
                self.assertNotIn("/home/", svg)
                # The SVG namespace is the one URL allowed, and it is never
                # fetched; anything else means the figure depends on a host.
                self.assertEqual(svg.count("http"), 1)
                self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)
                self.assertNotRegex(svg, r"20\d\d-\d\d-\d\dT")


class WellFormednessTests(unittest.TestCase):
    def test_every_figure_parses_as_xml(self):
        for name, svg in sorted(figures.render_all().items()):
            with self.subTest(figure=name):
                root = ElementTree.fromstring(svg)
                self.assertTrue(root.tag.endswith("svg"))

    def test_no_figure_references_an_external_resource(self):
        """A figure that fetches anything is not reproducible in a paper build."""
        for name, svg in sorted(figures.render_all().items()):
            with self.subTest(figure=name):
                self.assertNotIn("<image", svg)
                self.assertNotIn("xlink:href", svg)
                self.assertNotIn("@import", svg)

    def test_marks_stay_inside_the_canvas(self):
        """Cheap guard against a figure silently growing off its own viewBox."""
        for name, svg in sorted(figures.render_all().items()):
            with self.subTest(figure=name):
                root = ElementTree.fromstring(svg)
                width = float(root.attrib["width"])
                height = float(root.attrib["height"])
                for element in root.iter():
                    tag = element.tag.rsplit("}", 1)[-1]
                    if tag in ("text", "circle", "rect", "line"):
                        for attribute in ("x", "cx", "x1", "x2"):
                            if attribute in element.attrib:
                                self.assertLessEqual(float(element.attrib[attribute]), width)
                        for attribute in ("y", "cy", "y1", "y2"):
                            if attribute in element.attrib:
                                self.assertLessEqual(float(element.attrib[attribute]), height)


class FidelityTests(unittest.TestCase):
    """The drawn numbers must be the artifact's numbers, not a retelling."""

    def test_heatmap_prints_every_cell_of_the_sweep(self):
        sweep = _artifact("benchmark_sweep30_seed20260813.json")
        svg = figures.figure_survival_matrix(sweep)
        printed = _texts(svg)
        for strategy in figures.STRATEGY_ORDER:
            for intervention in figures.INTERVENTION_ORDER:
                rate = sweep["cells"][f"{strategy}::{intervention}"]["survival_rate"]
                with self.subTest(cell=f"{strategy}::{intervention}"):
                    self.assertIn(f"{rate:.2f}", printed)
        self.assertEqual(
            len([t for t in printed if re.fullmatch(r"[01]\.\d\d", t)]),
            len(figures.STRATEGY_ORDER) * (len(figures.INTERVENTION_ORDER) + 1),
            "every cell plus one row mean per strategy",
        )

    def test_heatmap_row_means_match_per_strategy(self):
        sweep = _artifact("benchmark_sweep30_seed20260813.json")
        svg = figures.figure_survival_matrix(sweep)
        for strategy in figures.STRATEGY_ORDER:
            rate = sweep["per_strategy"][strategy]["capability_survival_rate"]
            with self.subTest(strategy=strategy):
                self.assertIn(f"{rate:.2f}", _texts(svg))

    def test_pareto_places_lineage_aware_above_and_left_of_full_replication(self):
        """C4 is a dominance claim; if the geometry inverts, the figure lies."""
        sweep = _artifact("benchmark_sweep30_seed20260813.json")
        lineage = sweep["per_strategy"]["lineage_aware_repair"]
        full = sweep["per_strategy"]["full_replication"]
        self.assertGreater(lineage["capability_survival_rate"], full["capability_survival_rate"])
        self.assertLess(lineage["mean_bytes_moved"], full["mean_bytes_moved"])

    def test_scale_grid_draws_one_mark_per_recorded_point(self):
        scale = _artifact("scale_seed20260813.json")
        svg = figures.figure_scale(scale)
        self.assertEqual(svg.count("<circle"), len(scale["points"]))

    def test_scale_grid_rows_are_uniform_exactly_where_the_artifact_says(self):
        scale = _artifact("scale_seed20260813.json")
        rows: dict[tuple, set[bool]] = {}
        for point in scale["points"]:
            spec = point["spec"]
            key = (point["repair_policy"], point["intervention"], spec["independent_roots"])
            rows.setdefault(key, set()).add(point["capability_survival"])
        for key, verdicts in sorted(rows.items()):
            with self.subTest(row=key):
                self.assertEqual(len(verdicts), 1, "a row is not invariant across K and H")

    def test_silent_forgetting_lanes_partition_the_cells(self):
        benchmark = _artifact("benchmark_seed20260813.json")
        svg = figures.figure_silent_forgetting(benchmark)
        counts = [int(t.split()[0]) for t in _texts(svg) if t.endswith(" cells")]
        self.assertEqual(sum(counts), len(benchmark["cells"]))

    def test_the_lost_lane_reaches_the_highest_confidence_drawn(self):
        """The figure's headline. If this inverts, the claim is gone."""
        benchmark = _artifact("benchmark_seed20260813.json")
        kept = [
            cell["post_intervention_confidence"]
            for cell in benchmark["cells"].values() if cell["capability_survival"]
        ]
        lost = [
            cell["post_intervention_confidence"]
            for cell in benchmark["cells"].values()
            if not cell["capability_survival"] and cell["post_intervention_confidence"] > 0
        ]
        self.assertTrue(lost)
        self.assertGreaterEqual(max(lost), max(kept))


class PinningTests(unittest.TestCase):
    def test_sha256sums_covers_every_committed_figure(self):
        recorded = {
            line.split()[1].lstrip("*")
            for line in (ARTIFACTS / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        on_disk = {f"figures/{path.name}" for path in FIGURE_DIR.glob("*.svg")}
        self.assertEqual(on_disk, {name for name in recorded if name.startswith("figures/")})

    def test_every_figure_matches_its_recorded_digest(self):
        for line in (ARTIFACTS / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, name = line.split()
            name = name.lstrip("*")
            if not name.startswith("figures/"):
                continue
            with self.subTest(figure=name):
                actual = hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)


if __name__ == "__main__":
    unittest.main()

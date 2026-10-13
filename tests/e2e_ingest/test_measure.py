"""S7: the measure job's pure pieces (``research/e2e_ingest/measure.py``, ``report.py``'s c5 and the measure
recomputation of M12): the phase plan and budgets (M14), the re-keying of the sweep's events (M7), one organisation
per rate step (section 3.6), the phase bookkeeping (an overrun or an exception is recorded, never raised), the
scaling row of section 10, criterion 5's check with one mutation per assertion, the recomputation of a measure
report from its samples and from the size reports beside it, the simulator's columns (M24), and the summary.

The live measure job (a real fabric, the sweep, the measurement graphs) is exercised by the loop's local run and
the CI job; nothing here starts a broker. Everything is synthetic.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from research.e2e_ingest import measure as ms
from research.e2e_ingest import report as rp
from research.e2e_ingest.suites import EVIDENCE
from tests.e2e_ingest.test_harness import DROP, green_report, put

SIZES = ("200", "600", "2000")


def size_doc(size: int) -> dict[str, Any]:
    """A green end-to-end report of one size with the samples the recomputation reads."""
    doc = green_report()
    doc["world"].update({"target_users": size, "seed": 0})
    doc["timings"] = {"world_pipeline_seconds": 4.5, "world_detection_seconds": 0.4, "main_pass_seconds": 150.0,
                      "world_seconds": 5.0}
    doc["run"].update({"seconds": 300.0, "job": "e2e"})
    doc["routing"]["tesseract"].update({"rankings": 180, "nodes_min": 1458, "nodes_median": 1805, "nodes_max": 2574,
                                        "samples": [{"seconds": s} for s in (0.5, 0.7, 1.2)]})
    doc["routing"]["question_to_verdict"].update({"samples": [{"seconds": s} for s in (2.2, 3.4)],
                                                  "per_site_samples": [{"seconds": s} for s in (0.7, 1.3)]})
    doc["routing"].update({"candidates_detected": 61, "statuses": {"supported": 47},
                           "candidates_asked": rp.MEASURE_CANDIDATES[size]})
    doc["publish"]["created_to_applied"]["samples"] = [{"seconds": s} for s in (0.5, 1.5)]
    for block, key, fields in rp.RECOMPUTE:
        node = rp._get(doc, block)
        for name, (sample_key, pct) in fields.items():
            node[name] = rp.stats.percentile([s[sample_key] for s in node[key]], pct)
    doc["lineage"]["rows"] = [{"reconstructable": True, "source_events_complete": True}] * 20
    doc["checks"] = rp.run_checks(doc, ["c7_guards", "c8_emergence_load", "c9_benchmark"])
    doc["checks"]["c0_real_fabric_keyed"] = rp.c0_real_fabric_keyed(doc, in_ci=False)
    return doc


def green_measure() -> dict[str, Any]:
    """A measure report in which every assertion of c5 holds."""
    phases = [{"name": n, "completed": True, "budget_seconds": 300.0, "seconds": 100.0, "reason": None}
              for n in ms.phase_plan(rp.MEASURE_SIZES)]
    sizes = {}
    for size in SIZES:
        doc = size_doc(int(size))
        sizes[size] = {"target_users": int(size), "completed": True, "reason": None, "row": rp.scaling_row(doc),
                       "checks": doc["checks"], "report": f"size-{size}/{rp.REPORT_NAME}"}
    graphs = {}
    for n in rp.MEASURE_NODE_COUNTS:
        samples = [{"seconds": 0.001 * n * k} for k in (1.0, 1.1, 1.3)]
        graphs[str(n)] = {"nodes": n, "queries": rp.FIXED_QUERIES, "samples": samples, "build_seconds": 0.001 * n,
                          "p50": rp.stats.percentile([s["seconds"] for s in samples], 50),
                          "p95": rp.stats.percentile([s["seconds"] for s in samples], 95), "completed": True}
    step_samples = [{"seconds": s} for s in (0.0, 1.0, 1.0, 0.0)]
    steps = [{"rate": 2, "events": 121, "duration_seconds": 60.3, "apply_p50": 0.5, "apply_p95": 1.0, "n": 121,
              "duplicate": 0, "applied_all": True, "rate_held": True, "samples": step_samples,
              "achieved_events_per_second": 2.0}]
    steps[0]["apply_p50"] = rp.stats.percentile([s["seconds"] for s in step_samples], 50)
    steps[0]["apply_p95"] = rp.stats.percentile([s["seconds"] for s in step_samples], 95)
    return {"kind": rp.KIND, "schema_version": rp.SCHEMA_VERSION, "synthetic": True, "measurement": False,
            "run": {"job": "measure", "code_commit": "a" * 40, "code_dirty_start": False, "code_dirty_end": False,
                    "suites_evidence": EVIDENCE}, "runner": {"cpu_model": "a cpu", "cpu_count": 4},
            "phases": phases, "sizes_requested": [int(s) for s in SIZES], "sizes": sizes,
            "tesseract_nodes": {"graphs": graphs, "completed": True},
            "throughput": {"steps": steps, "sustained_events_per_second": 2, "stopped": "last_step"}}


class PlanTests(unittest.TestCase):
    """M14's order and budgets; M7's keys; section 3.6's organisations."""

    def test_the_phase_plan_is_the_smallest_size_then_the_sweep_and_the_graphs_then_the_rest(self) -> None:
        self.assertEqual(ms.phase_plan((2000, 600, 200)), ["size-200", "rate-sweep", "tesseract-nodes", "size-600",
                                                           "size-2000"])
        self.assertEqual(ms.phase_plan((200,)), ["size-200", "rate-sweep", "tesseract-nodes"])
        self.assertEqual(ms.phase_plan((600,)), ["size-600", "rate-sweep", "tesseract-nodes"])
        with self.assertRaises(ValueError):
            ms.phase_plan(())

    def test_the_budgets_sum_to_42_of_45_minutes_and_every_phase_has_one(self) -> None:
        options = ms.MeasureOptions(out=Path("x"))
        self.assertEqual(sum(options.budgets.values()), 42 * 60.0)
        for name in ms.phase_plan(rp.MEASURE_SIZES):
            self.assertIn(name, options.budgets)
        self.assertEqual(ms.budget_seconds("size-2000"), 15 * 60.0)
        self.assertEqual(rp.MEASURE_CANDIDATES, {200: 60, 600: 60, 2000: 20})

    def test_rekey_and_the_step_organisations(self) -> None:
        event = {"type": "collective.verdict", "idempotency_key": "q1:site-a:1", "payload": {}}
        rekeyed = ms.rekey(event, "rate-04", 7)
        self.assertEqual(rekeyed["idempotency_key"], "rate-04:7:q1:site-a:1")
        self.assertEqual(event["idempotency_key"], "q1:site-a:1")           # the original is untouched
        self.assertEqual(rekeyed["payload"], {})
        self.assertEqual([ms.sweep_org(r) for r in rp.MEASURE_RATE_STEPS],
                         ["emergence-rate-02", "emergence-rate-04", "emergence-rate-08", "emergence-rate-16",
                          "emergence-rate-32"])
        # agent ids are one namespace across organisations in the service, so each step registers its own HQ
        self.assertEqual([ms.sweep_agent(r) for r in (2, 32)], ["hq-rate-02", "hq-rate-32"])

    def test_parse_sizes(self) -> None:
        self.assertEqual(ms.parse_sizes("200,600,2000"), (200, 600, 2000))
        self.assertEqual(ms.parse_sizes("200"), (200,))
        for bad in ("", "300", "200,x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ms.parse_sizes(bad)


class PhaseTests(unittest.TestCase):
    """The bookkeeping: a phase's block, the report written after it, an overrun and an exception recorded."""

    def run_phase(self, name: str, work: Any, budgets: dict[str, float]) -> tuple[ms.MeasureRun, dict[str, Any]]:
        tmp = Path(tempfile.mkdtemp(prefix="e2e-measure-"))
        options = ms.MeasureOptions(out=tmp, sizes=(200,), budgets=budgets)
        run = ms.MeasureRun(options=options)
        run.doc = {"kind": rp.KIND, "phases": [], "phase": None, "completed": False}
        block = run.phase(name, work)
        return run, block

    def test_a_completed_phase_inside_its_budget_is_recorded_and_the_report_written(self) -> None:
        run, block = self.run_phase("x", lambda budget: {"completed": True, "budget_seen": budget}, {"x": 60.0})
        self.assertEqual(block["budget_seen"], 60.0)
        entry = run.doc["phases"][0]
        self.assertEqual((entry["name"], entry["completed"], entry["reason"], entry["budget_seconds"]),
                         ("x", True, None, 60.0))
        self.assertGreaterEqual(entry["seconds"], 0.0)
        self.assertTrue(run.doc["completed"])
        self.assertEqual(run.doc["phase"], "x")
        written = json.loads(run.path.read_text(encoding="utf-8"))
        self.assertEqual(written["phases"][0]["name"], "x")

    def test_an_overrun_is_completed_false_with_reason_budget(self) -> None:
        run, block = self.run_phase("x", lambda budget: {"completed": True}, {"x": 0.0})
        self.assertEqual((block["completed"], block["reason"]), (False, "budget"))
        self.assertEqual((run.doc["phases"][0]["completed"], run.doc["phases"][0]["reason"]), (False, "budget"))
        self.assertFalse(run.doc["completed"])

    def test_an_exception_is_recorded_never_raised(self) -> None:
        def work(budget: float) -> dict[str, Any]:
            raise ValueError("the broker did not start")
        run, block = self.run_phase("x", work, {"x": 60.0})
        self.assertFalse(block["completed"])
        self.assertEqual(block["reason"], "ValueError: the broker did not start")
        self.assertEqual(run.doc["phases"][0]["reason"], "ValueError: the broker did not start")


class ScalingRowTests(unittest.TestCase):
    def test_the_row_carries_section_10s_columns_from_the_size_report(self) -> None:
        row = rp.scaling_row(size_doc(200))
        self.assertEqual((row["target_users"], row["users"], row["sites"], row["records"]), (200, 309, 7, 700))
        self.assertEqual(row["nodes_per_site"], {"min": 100, "median": 100, "max": 100})
        self.assertEqual((row["pipeline_seconds"], row["detection_seconds"], row["wall_seconds"]), (4.5, 0.4, 300.0))
        self.assertEqual((row["candidates"], row["rankings"]), (rp.MEASURE_CANDIDATES[200], 180))
        self.assertEqual(row["tesseract_p50"], rp.stats.percentile([0.5, 0.7, 1.2], 50))
        self.assertEqual(row["created_to_applied_p95"], rp.stats.percentile([0.5, 1.5], 95))
        self.assertEqual(row["publishes"], 80)
        for name in rp.MEASURE_ROW_FIELDS:
            with self.subTest(field=name):
                self.assertTrue(rp._number(row[name]), name)


class C5Tests(unittest.TestCase):
    """Criterion 5's check: a green measure report passes; one mutation per assertion."""

    def setUp(self) -> None:
        self.doc = green_measure()

    def test_a_green_measure_report_passes(self) -> None:
        check = rp.c5_measured(self.doc)
        self.assertEqual(check["failed"], [])
        self.assertTrue(check["ok"])

    def mutations(self) -> list[tuple[str, str, Any]]:
        out = [
            ("every_phase_completed", "phases.1.completed", False),
            ("every_requested_size_completed", "sizes.600.completed", False),
            ("tesseract_600_nodes", "tesseract_nodes.graphs.600.nodes", 599),
            ("tesseract_5882_nodes", "tesseract_nodes.graphs.5882.queries", 19),
            ("tesseract_20000_nodes", "tesseract_nodes.graphs.20000.p95", None),
            ("sweep_first_step_rate_2", "throughput.steps.0.rate", 4),
            ("sweep_first_step_events_120", "throughput.steps.0.events", 119),
            ("sweep_first_step_60s", "throughput.steps.0.duration_seconds", 59.9),
            ("sweep_first_step_apply_p95_within_bound", "throughput.steps.0.apply_p95", 1.5),
            ("sweep_first_step_rate_held", "throughput.steps.0.rate_held", False),
            ("sweep_samples_are_the_events", "throughput.steps.0.n", 120),
            ("sustained_rate_numeric", "throughput.sustained_events_per_second", None),
            ("runner_cpu_named", "runner.cpu_model", None),
        ]
        for size in SIZES:
            out += [(f"size_{size}_row_numeric", f"sizes.{size}.row.tesseract_p95", None),
                    (f"size_{size}_candidates", f"sizes.{size}.row.candidates", 1),
                    (f"size_{size}_checks_ok", f"sizes.{size}.checks.c8_emergence_load.ok", False)]
        return out

    def test_every_assertion_catches_its_own_mutation(self) -> None:
        for assertion, path, value in self.mutations():
            with self.subTest(assertion=assertion):
                check = rp.c5_measured(put(self.doc, path, value))
                self.assertFalse(check["ok"])
                self.assertIn(assertion, check["failed"])

    def test_every_assertion_has_a_mutation(self) -> None:
        self.assertEqual(len({a for a, _, _ in self.mutations()}), rp.c5_measured(self.doc)["assertions"])

    def test_a_size_asks_the_detected_candidates_when_the_world_holds_fewer_than_the_cap(self) -> None:
        """The 200-user world detects 55 candidates, under the cap of 60: it asks all 55 and c5 accepts that;
        fewer than the detected count, or fewer than the floor of 20, it does not."""
        fewer = put(put(self.doc, "sizes.200.row.candidates_detected", 55), "sizes.200.row.candidates", 55)
        self.assertNotIn("size_200_candidates", rp.c5_measured(fewer)["failed"])
        self.assertIn("size_200_candidates", rp.c5_measured(put(fewer, "sizes.200.row.candidates", 54))["failed"])
        few = put(put(self.doc, "sizes.200.row.candidates_detected", 19), "sizes.200.row.candidates", 19)
        self.assertIn("size_200_candidates", rp.c5_measured(few)["failed"])
        self.assertIn("size_2000_candidates",
                      rp.c5_measured(put(self.doc, "sizes.2000.row.candidates_detected", None))["failed"])

    def test_a_removed_block_fails_too(self) -> None:
        for path in ("throughput", "tesseract_nodes", "sizes.2000"):
            with self.subTest(path=path):
                self.assertFalse(rp.c5_measured(put(self.doc, path, DROP))["ok"])


class RecomputeTests(unittest.TestCase):
    """M12 over a measure report: the graphs' and the steps' percentiles from their samples, the scaling rows from
    the size reports beside the report, which are themselves recomputed."""

    def test_a_report_whose_aggregates_are_its_samples_agrees_given_the_size_reports(self) -> None:
        doc = green_measure()
        with tempfile.TemporaryDirectory(prefix="e2e-measure-") as tmp:
            base = Path(tmp)
            for size in SIZES:
                rp.write(base / f"size-{size}" / rp.REPORT_NAME, size_doc(int(size)))
            self.assertEqual(rp.recompute(doc, base_dir=base), [])
            self.assertEqual(rp.recompute_measure(doc, base), [])
            # a typed-in percentile, a typed-in row and a missing size report are each named
            typed = put(doc, "tesseract_nodes.graphs.600.p50", 9.0)
            self.assertTrue(any("graphs.600.p50" in p for p in rp.recompute(typed, base_dir=base)))
            self.assertTrue(any("steps[0].apply_p95" in p
                                for p in rp.recompute(put(doc, "throughput.steps.0.apply_p95", 0.0), base_dir=base)))
            self.assertTrue(any("sizes.600.row" in p
                                for p in rp.recompute(put(doc, "sizes.600.row.wall_seconds", 1.0), base_dir=base)))
            (base / "size-2000" / rp.REPORT_NAME).unlink()
            self.assertTrue(any("sizes.2000" in p for p in rp.recompute(doc, base_dir=base)))
        self.assertTrue(any("no size report" in p for p in rp.recompute(doc)))      # no base dir


class SimulatorTests(unittest.TestCase):
    """M24: the simulator's columns carry the allocation name and the five metrics, never a tier label."""

    def test_the_columns_at_200_users_are_numeric_labelled_and_free_of_tier_labels(self) -> None:
        from research.mycelic.models import ALLOCATIONS
        columns, reason = ms.simulator_columns(200, 0)
        self.assertIsNone(reason)
        self.assertEqual((columns["simulated"], columns["synthetic"], columns["measurement"]), (True, True, False))
        self.assertEqual(columns["allocation"], ms.ALLOCATION)
        self.assertEqual(sorted(columns["architectures"]), sorted(ms.ARCHITECTURES))
        for name, metrics in columns["architectures"].items():
            for metric in ms.METRICS:
                with self.subTest(architecture=name, metric=metric):
                    self.assertTrue(rp._number(metrics[metric]))
        text = json.dumps(columns)
        self.assertEqual(rp.forbidden_strings(text, sorted(set(ALLOCATIONS[ms.ALLOCATION]))), [])
        self.assertEqual(ms.simulator_columns(200, 0, budget_seconds=0.0), (None, "budget"))


class SummaryTests(unittest.TestCase):
    def test_the_summary_names_the_phases_the_rows_the_graphs_the_steps_and_the_checks(self) -> None:
        doc = green_measure()
        doc["run"]["seconds"] = 1200.0
        doc["completed"] = True
        doc["scaling_table"] = [doc["sizes"][s]["row"] for s in SIZES]
        doc["largest_size_completed"] = 2000
        doc["checks"] = {"c5_measured": rp.c5_measured(doc)}
        doc["verdict"] = rp.verdict(doc["checks"], suites_green=False)
        text = ms.summary(doc)
        self.assertIn("measurement: false", text)
        for needle in ("phase size-200", "phase rate-sweep", "size 2000:", "tesseract 20000 nodes", "sweep 2/s",
                       "sustained events/s: 2", "c5_measured: ok"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()

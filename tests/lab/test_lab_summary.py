"""Summaries: every number outside a code span is read from a run file (the sources list says which, in order), the
first line says when nothing measures a model, the classes are kept apart, the size stays under GitHub's cap with
whole rows, and free text can never break out of its code span. With ``--log`` the summary and its run files are
also printed into the job log, framed so that a reader of the log can cut out and verify each file and the runner
acts on no workflow command in them.

One dry run of ``lab/requests/plumbing-001.json`` (with ``lab/models.json``) is shared; model-class trees are copies
of it, mutated and re-sealed (``helpers.DryTree``). Nothing here measures a model.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from lab import notes, summary
from lab.units import CHECK_KEYS, display_class
from mycelic.collective.experiments.common import write_json_atomic
from mycelic.collective.jsonio import canonical_dumps
from tests.lab.helpers import (LAB_MANIFEST, PLUMBING_001, ROOT, DryTree, check_sources, kill_mentioning, lab_cli,
                               lab_env, span_text, split_code_spans)

PLUMBING = notes.PLUMBING_CHECK_LINE


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


LOG_BEGIN_RE = re.compile(r"=== MYCELIC-LAB (\S+) BEGIN lines=(0|[1-9][0-9]*) sha256=([0-9a-f]{64}) ===")
LOG_SINGLE_RE = re.compile(r"=== MYCELIC-LAB (\S+) (ABSENT|UNREADABLE sha256=(?:[0-9a-f]{64}|none)"
                           r"|TOO-LARGE bytes=(?:0|[1-9][0-9]*) sha256=[0-9a-f]{64}) ===")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_log(test: unittest.TestCase, out: str) -> list[tuple[str, str, list[str] | None, str | None]]:
    """The blocks of a ``--log`` output as ``(label, state, body lines or None, sha256 or None)``, after checking the
    frame (``::stop-commands::<T>`` first, ``::<T>::`` last, ``T`` the sha256 of the lines between and nowhere else),
    that every line between is a block line, a body line or the INDEX line (last), that no body line starts with
    ``::`` or the marker prefix and that the INDEX line lists every block's state."""
    test.assertTrue(out.endswith("\n"), out[-200:])
    lines = out[:-1].split("\n")
    frame = re.fullmatch(r"::stop-commands::([0-9a-f]{64})", lines[0])
    test.assertIsNotNone(frame, lines[0])
    token = frame.group(1)
    test.assertEqual(lines[-1], f"::{token}::")
    test.assertEqual(out.count(token), 2)
    test.assertEqual(token, _sha256("\n".join(lines[1:-1]).encode("utf-8")))
    blocks: list[tuple[str, str, list[str] | None, str | None]] = []
    i = 1
    while i < len(lines) - 2:
        begin = LOG_BEGIN_RE.fullmatch(lines[i])
        if begin is not None:
            label, n, sha = begin.group(1), int(begin.group(2)), begin.group(3)
            body = lines[i + 1:i + 1 + n]
            test.assertEqual(lines[i + 1 + n], f"=== MYCELIC-LAB {label} END ===")
            for line in body:
                test.assertFalse(line.startswith(("=== MYCELIC-LAB", "::")), line)
            blocks.append((label, str(n), body, sha))
            i += n + 2
            continue
        single = LOG_SINGLE_RE.fullmatch(lines[i])
        test.assertIsNotNone(single, lines[i])
        blocks.append((single.group(1), single.group(2), None, None))
        i += 1
    test.assertEqual(i, len(lines) - 2)
    states = [state if body is not None else state.split()[0].lower() for _, state, body, _ in blocks]
    test.assertEqual(lines[-2], "=== MYCELIC-LAB INDEX " + " ".join(f"{label}={state}" for (label, _, _, _), state
                                                                     in zip(blocks, states)) + " ===")
    return blocks


def log_cli(*args: str) -> subprocess.CompletedProcess[bytes]:
    """``python -m lab.summary ARGS --log``, stdout as bytes (it is UTF-8 whatever the locale)."""
    return subprocess.run([sys.executable, "-m", "lab.summary", *args, "--log"], cwd=ROOT, env=lab_env(),
                          capture_output=True, timeout=300, stdin=subprocess.DEVNULL)


def _section(md: str, heading: str) -> str:
    """The text from the heading line to the next heading of the same or a higher level."""
    lines = md.splitlines()
    start = next(i for i, line in enumerate(lines) if line.endswith(heading) and line.startswith("#"))
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("#") and len(lines[i]) - len(lines[i].lstrip("#")) <= level), len(lines))
    return "\n".join(lines[start:end])


class DryRunSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-summary-"))
        cls.out = cls.tmp / "D"
        # -S: no site-packages, as on a runner before anything is installed
        cls.done = subprocess.run([sys.executable, "-S", "-m", "lab.dryrun", "--request", str(PLUMBING_001), "--out",
                                   str(cls.out)], cwd=ROOT, env=lab_env(), capture_output=True, text=True, timeout=600,
                                  stdin=subprocess.DEVNULL)
        cls.summaries = [(cls.out / "plan", cls.out / "plan" / "summary.md", cls.out / "plan" / "summary.sources.json")]
        for shard in sorted((cls.out / "shards").iterdir()):
            cls.summaries.append((shard, shard / "summary" / "summary.md", shard / "summary" / "summary.sources.json"))
        cls.summaries.append((cls.out / "report", cls.out / "report" / "report.md",
                              cls.out / "report" / "report.sources.json"))

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def setUp(self) -> None:
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        self.work = Path(tempfile.mkdtemp(prefix="lab-summary-t-", dir=self.tmp))

    def test_sources_match_files_dry_run(self) -> None:
        self.assertEqual(len(self.summaries), 7)
        modes = ["plan", "shard", "shard", "shard", "shard", "shard", "report"]
        for (root, md_path, sources_path), mode in zip(self.summaries, modes):
            with self.subTest(summary=md_path.relative_to(self.out).as_posix()):
                md = md_path.read_text(encoding="utf-8")
                sources = _json(sources_path)
                self.assertEqual((sources["schema_version"], sources["kind"], sources["mode"]),
                                 (1, "lab_summary_sources", mode))
                self.assertGreater(check_sources(self, md, sources["sources"], root), 3)
                text, entries = summary.RENDERERS[mode](root)
                self.assertEqual((text, entries), (md, sources["sources"]))
                self.assertLessEqual(len(md.encode("utf-8")), summary.MAX_SUMMARY_BYTES)
                self.assertNotIn(notes.TRUNCATED, md)

    def _model_tree(self) -> tuple[DryTree, Path]:
        tree = DryTree(self.out, self.work / "T", manifest=LAB_MANIFEST)
        tree.set_plan(lambda plan: plan.update(result_class="real", provider="llama-server"))
        tree.make_real("s002-fake-a", model_units=("e3-fake-a",))
        tree.make_real("s003-fake-b", units=("g0-fake-b",))
        report_dir = self.work / "R"
        code, stdout, stderr, report = tree.aggregate(report_dir)
        self.assertEqual(code, 0, stderr)
        return tree, report_dir

    def test_sources_match_files_model_tree(self) -> None:
        tree, report_dir = self._model_tree()
        md, entries = summary.render_report(report_dir)
        self.assertEqual(md.splitlines()[0], f"## {notes.HEADINGS['report']}")
        self.assertGreater(check_sources(self, md, entries, report_dir), 20)
        model = _section(md, notes.HEADINGS["model"])
        self.assertIn("`e3-fake-a`", model)
        self.assertNotIn("`g0-fake-b`", model)
        self.assertIn("`g0-fake-b`", _section(md, notes.HEADINGS["unverified"]))
        for shard in ("s002-fake-a", "s003-fake-b"):
            text, shard_entries = summary.render_shard(tree.path(shard))
            check_sources(self, text, shard_entries, tree.path(shard))
        first = summary.render_shard(tree.path("s002-fake-a"))[0].splitlines()[0]
        second = summary.render_shard(tree.path("s003-fake-b"))[0].splitlines()[0]
        self.assertEqual((first, second), (f"## {notes.HEADINGS['shard']} `s002-fake-a`", notes.NO_MEASUREMENT_LINE))
        # the changed plan no longer matches its preregistration, so E1 is not compared, and says why
        report = _json(report_dir / "report.json")
        self.assertEqual((report["e1"]["compared"], report["e1"]["reason"]), (False, notes.PREREG_MISSING))
        self.assertIn(notes.PREREG_MISSING, _section(md, notes.HEADINGS["e1"]).splitlines())

    def test_sim_tables_in_the_dry_run(self) -> None:
        report = _json(self.out / "report" / "report.json")
        (row,) = report["sim"]
        (sizing,) = report["sim_sizing"]
        self.assertEqual((row["unit"], row["display_class"], row["measurement"]), ("sim-fake-a-s1", "plumbing", False))
        self.assertEqual((sizing["unit"], sizing["status"], sizing["source"]), ("sim-fake-a-s1", "ok", "scorecard"))
        (group,) = report["notes"]["sim_world_digest"]
        self.assertEqual((group["units"], group["consistent"]), (["sim-fake-a-s1"], True))
        md, entries = self.summaries[-1][1].read_text(encoding="utf-8"), _json(self.summaries[-1][2])["sources"]
        plumbing = _section(md, notes.HEADINGS["plumbing"])
        for key in ("sim", "sim-lifts", "sim-pushdown"):
            self.assertIn(f"#### {notes.HEADINGS[key]}", plumbing.splitlines())
            self.assertIn("`sim-fake-a-s1`", _section(plumbing, notes.HEADINGS[key]))
        sizing_md = _section(md, notes.HEADINGS["sizing"])
        self.assertIn("`sim-fake-a-s1`", sizing_md)
        self.assertIn(notes.SIZING_NOTE, sizing_md)
        self.assertIn(notes.SIM_WORLD_SAME, md)
        self.assertNotIn(notes.SIM_WORLD_DIFFERS, md)
        for key in row["notes"]:
            self.assertIn(notes.SIM_NOTES[key], md)
        pointers = {e["pointer"] for e in entries}
        for name in row["channels"]:
            self.assertIn(f"/sim/0/channels/{name}/found", pointers)
        for key in ("records_done", "estimate_s", "suggested_minutes"):
            self.assertIn(f"/sim_sizing/0/{key}", pointers)
        shard_md = (self.out / "shards" / "s001-fake-a" / "summary" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("`sim-fake-a-s1`", shard_md)

    def test_by_construction_in_the_dry_run(self) -> None:
        """The simulation's and X1's lifts over S and R_mf carry the harness's own label: report.json keeps each
        scorecard's by-construction entries and the report prints them, sourced, under each lifts table."""
        report = _json(self.out / "report" / "report.json")
        (sim,), (x1,) = report["sim"], report["x1"]
        for row in (sim, x1):
            with self.subTest(unit=row["unit"]):
                self.assertEqual(row["by_construction"], [
                    {"channel": name, "visibility": "narrative_only", "label": notes.BY_CONSTRUCTION_LABEL,
                     "recall": 0.0, "units": 3, "found": 0} for name in ("S", "R_mf")])
        md, entries = self.summaries[-1][1].read_text(encoding="utf-8"), _json(self.summaries[-1][2])["sources"]
        lines = md.splitlines()
        heading = "#### " + notes.HEADINGS["by-construction"]
        self.assertEqual((lines.count(heading), lines.count(notes.BY_CONSTRUCTION_NOTE)), (2, 2))
        sim_at, x1_at = [i for i, line in enumerate(lines) if line == heading]
        self.assertLess(lines.index("#### " + notes.HEADINGS["sim-lifts"]), sim_at)
        self.assertLess(sim_at, lines.index("#### " + notes.HEADINGS["sim-pushdown"]))
        self.assertLess(lines.index("#### " + notes.HEADINGS["x1-lifts"]), x1_at)
        for unit, name in (("sim-fake-a-s1", "S"), ("sim-fake-a-s1", "R_mf"), ("x1", "S"), ("x1", "R_mf")):
            self.assertIn(f"| `{unit}` | `{name}` | `narrative_only` | 3 | 0 | 0.000 | by construction, not a result |",
                          lines)
        pointers = {e["pointer"] for e in entries}
        for key in ("sim", "x1"):
            for j in range(2):
                for field in ("units", "found", "recall"):
                    self.assertIn(f"/{key}/0/by_construction/{j}/{field}", pointers)

    def test_x1_warnings_in_the_dry_run(self) -> None:
        report = _json(self.out / "report" / "report.json")
        (x1,) = report["x1"]
        self.assertTrue(x1["warnings"])
        md = self.summaries[-1][1].read_text(encoding="utf-8")
        (line,) = [line for line in md.splitlines() if line.startswith("- `x1`: ")]
        self.assertTrue(line.endswith(f"; {notes.COLUMNS['warnings']}: " + ", ".join(f"`{w}`" for w in x1["warnings"])),
                        line)

    def test_experiment_sections_in_the_dry_run(self) -> None:
        report = _json(self.out / "report" / "report.json")
        self.assertEqual((report["e1"]["compared"], report["e1"]["display_class"]), (True, "plumbing"))
        self.assertEqual([r["unit"] for r in report["e2"]], ["e2-fake-a"])
        self.assertEqual([r["unit"] for r in report["x1"]], ["x1"])
        self.assertEqual((report["openfda"], report["e2_sizing"]), ([], []))
        md = self.summaries[-1][1].read_text(encoding="utf-8")
        plumbing = _section(md, notes.HEADINGS["plumbing"])
        for key in ("e1", "e1-endpoints", "e1-paired", "e2", "e2-ratio", "e2-candidates", "x1", "x1-lifts"):
            self.assertIn(f"#### {notes.HEADINGS[key]}", plumbing.splitlines(), key)
        self.assertNotIn(notes.HEADINGS["openfda"], md)
        self.assertIn(summary.ids(notes.E1_LABELS["generator_text"]), plumbing.splitlines())
        self.assertIn(notes.E1_VERDICTS_WITHHELD, plumbing.splitlines())
        self.assertIn(summary.ids(notes.X1_LABEL), plumbing.splitlines())
        self.assertIn(notes.G0_BELOW_PROTOCOL, _section(plumbing, notes.HEADINGS["g0"]).splitlines())
        plan_md = self.summaries[0][1].read_text(encoding="utf-8")
        prereg = _section(plan_md, notes.HEADINGS["prereg"])
        self.assertIn("`E1` labels: source `generator`, records 40, claims ", prereg)
        self.assertIn("`X1` prereg sha `", prereg)
        self.assertIn("`E2` prereg sha `", prereg)
        pointers = {e["pointer"] for e in _json(self.summaries[0][2])["sources"] if e["file"] == "prereg/prereg.json"}
        self.assertEqual(pointers, {"/e1/labels/records", "/e1/labels/claims", "/e2/rehearsal/candidates/total",
                                    "/e2/rehearsal/candidates/seeds", "/e2/rehearsal/calls/judge_record",
                                    "/e2/rehearsal/central_raw_records/max"})

    def test_plumbing_first_line_and_heading(self) -> None:
        self.assertEqual(self.done.stdout.splitlines()[:2], ["plan: 12 units in 5 shards (plumbing)",
                                                             "prereg: e1 yes x1 yes e2 yes"])
        self.assertEqual(self.done.stdout.splitlines()[-1],
                         "lab: aggregate units 12 shards 5 class plumbing measurements false lock unchanged")
        for _, md_path, _ in self.summaries:
            with self.subTest(summary=md_path.name):
                self.assertEqual(md_path.read_text(encoding="utf-8").splitlines()[0], PLUMBING)
        report_md = self.summaries[-1][1].read_text(encoding="utf-8")
        report = _json(self.out / "report" / "report.json")
        self.assertEqual((report["result_class"], report["contains_measurements"]), ("plumbing", False))
        self.assertEqual({u["display_class"] for u in report["units"]}, {"plumbing"})
        self.assertIn(f"### {notes.HEADINGS['plumbing']}", report_md.splitlines())
        self.assertNotIn(notes.HEADINGS["model"], report_md)
        self.assertIn(notes.PLUMBING_BANNER, report_md)

    def test_no_measurement_first_line(self) -> None:
        tree = DryTree(self.out, self.work / "T", manifest=LAB_MANIFEST)
        tree.set_plan(lambda plan: plan.update(result_class="real", provider="llama-server"))
        shards = {"s001-fake-a": ("sim-fake-a-s1", "e1-fake-a-r1"),
                  "s002-fake-a": ("e1-fake-a-r2", "e1-fake-a-r3", "e2-fake-a", "e3-fake-a"),
                  "s003-fake-b": ("g0-fake-b", "e1-fake-b-r1", "e1-fake-b-r2"),
                  "s004-fake-b": ("e1-fake-b-r3", "e3-fake-b"), "s005-none": ()}
        for shard, units in shards.items():
            tree.make_real(shard, units=units)
        # a real run's model-free unit: the shard's provider, no fake rows, class no-model
        tree.edit("s005-none", "units/x1/unit.json", lambda r: r.update(provider="llama-server"))
        tree.reseal("s005-none")
        code, _, stderr, report = tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        classes = {u["unit"]: u["display_class"] for u in report["units"]}
        self.assertEqual(classes, {**{u: "unverified" for units in shards.values() for u in units}, "x1": "no-model"})
        self.assertEqual((report["result_class"], report["contains_measurements"]), ("real", False))
        self.assertEqual(report["e1"]["display_class"], "unverified")
        md, entries = summary.render_report(self.work / "R")
        self.assertEqual(md.splitlines()[0], notes.NO_MEASUREMENT_LINE)
        self.assertNotIn(notes.HEADINGS["model"], md)
        check_sources(self, md, entries, self.work / "R")
        for shard in shards:
            self.assertEqual(summary.render_shard(tree.path(shard))[0].splitlines()[0], notes.NO_MEASUREMENT_LINE)

    def test_class_separation(self) -> None:
        checks = dict.fromkeys(CHECK_KEYS, True)
        model = {"status": "ok", "measurement_class": "model", "kind": "gguf", "provider": "llama-server",
                 "fake_rows": 0, "class_checks": checks}
        real = {"result_class": "real", "provider": "llama-server"}
        cases = [
            (model, real, "model"),
            ({**model, "status": "result_fail"}, real, "model"),
            ({**model, "fake_rows": 3}, real, "plumbing"),
            ({**model, "provider": "fake"}, real, "plumbing"),
            ({**model, "kind": "fake"}, real, "plumbing"),
            ({**model, "measurement_class": "plumbing"}, real, "plumbing"),
            (model, {"result_class": "plumbing", "provider": "llama-server"}, "plumbing"),
            (model, {"result_class": "real", "provider": "fake"}, "plumbing"),
            ({**model, "class_checks": {**checks, "ledger_host": False}}, real, "unverified"),
            ({**model, "class_checks": {**checks, "ledger_host": 1}}, real, "unverified"),
            ({**model, "class_checks": {k: v for k, v in checks.items() if k != "participation"}}, real, "unverified"),
            ({**model, "class_checks": None}, real, "unverified"),
            ({**model, "fake_rows": None}, real, "unverified"),
            ({**model, "fake_rows": False}, real, "unverified"),
            ({**model, "measurement_class": "unverified"}, real, "unverified"),
            (model, None, "unverified"),
            ({**model, "status": "failed"}, real, "no-result"),
            ({**model, "status": "skipped"}, real, "no-result"),
            ({**model, "kind": "none", "measurement_class": "no-model", "fake_rows": None}, real, "no-model"),
        ]
        for record, provenance, expected in cases:
            with self.subTest(record=record, provenance=provenance):
                self.assertEqual(display_class(record, provenance), expected)
        tree = DryTree(self.out, self.work / "T", manifest=LAB_MANIFEST)
        tree.set_plan(lambda plan: plan.update(result_class="real", provider="llama-server"))
        tree.make_real("s002-fake-a", model_units=("e3-fake-a",))
        tree.make_real("s003-fake-b", model_units=("g0-fake-b",))
        tree.make_real("s004-fake-b", model_units=("e3-fake-b",))
        tree.edit("s003-fake-b", "units/g0-fake-b/unit.json", lambda r: r.update(fake_rows=5))
        tree.edit("s004-fake-b", "units/e3-fake-b/unit.json",
                  lambda r: r["class_checks"].update(model_served=False))
        tree.reseal("s003-fake-b")
        tree.reseal("s004-fake-b")
        code, _, stderr, report = tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        classes = {u["unit"]: u["display_class"] for u in report["units"]}
        others = {u: "plumbing" for u in ("e1-fake-a-r1", "e1-fake-a-r2", "e1-fake-a-r3", "e1-fake-b-r1",
                                          "e1-fake-b-r2", "e1-fake-b-r3", "e2-fake-a", "x1")}
        self.assertEqual(classes, {"e3-fake-a": "model", "g0-fake-b": "plumbing", "e3-fake-b": "unverified",
                                   "sim-fake-a-s1": "plumbing", **others})
        self.assertEqual([(r["unit"], r["display_class"]) for r in report["sim"]], [("sim-fake-a-s1", "plumbing")])
        md, _ = summary.render_report(self.work / "R")
        model = _section(md, notes.HEADINGS["model"])
        self.assertNotIn("`g0-fake-b`", model)
        self.assertNotIn("`e3-fake-b`", model)
        self.assertNotIn("`sim-fake-a-s1`", model)
        self.assertNotIn(notes.HEADINGS["sim"], model)
        plumbing = _section(md, notes.HEADINGS["plumbing"])
        self.assertIn("`g0-fake-b`", plumbing)
        self.assertIn("`sim-fake-a-s1`", _section(plumbing, notes.HEADINGS["sim"]))
        self.assertIn("`e3-fake-b`", _section(md, notes.HEADINGS["unverified"]))

    def test_size_cap(self) -> None:
        plan = _json(self.out / "plan" / "plan.json")
        unit = plan["units"][0]
        plan["units"] = [{**unit, "unit": f"e3-m{i:04d}", "shard": f"s{i // 6 + 1:03d}-m"} for i in range(1536)]
        plan["shards"] = [{**plan["shards"][0], "shard": f"s{j + 1:03d}-m",
                           "units": [f"e3-m{i:04d}" for i in range(6 * j, 6 * j + 6)]} for j in range(256)]
        (self.work / "P").mkdir()
        write_json_atomic(self.work / "P" / "plan.json", plan)
        md, entries = summary.render_plan(self.work / "P")
        self.assertLessEqual(len(md.encode("utf-8")), 900_000)
        check_sources(self, md, entries, self.work / "P")

        report = _json(self.out / "report" / "report.json")
        shard, unit, cell = report["shards"][0], report["units"][0], report["e3"][0]
        report["shards"] = [{**shard, "shard": f"s{j + 1:03d}-m"} for j in range(256)]
        report["units"] = [{**unit, "unit": f"e3-m{i:04d}"} for i in range(1536)]
        report["e3"] = [{**cell, "unit": f"e3-m{i // 8:04d}", "concurrency": i % 8 + 1} for i in range(12288)]
        report["unit_count"], report["shard_count"] = 1536, 256
        (self.work / "R").mkdir()
        write_json_atomic(self.work / "R" / "report.json", report)
        md, entries = summary.render_report(self.work / "R")
        self.assertLessEqual(len(md.encode("utf-8")), 900_000)
        self.assertGreater(len(md.encode("utf-8")), 800_000)
        self.assertEqual(md.rstrip("\n").splitlines()[-1], notes.TRUNCATED)
        self.assertEqual(md.count(notes.TRUNCATED), 1)
        check_sources(self, md, entries, self.work / "R")

        shard_dir = self.summaries[2][0]
        full, full_entries = summary.render_shard(shard_dir)
        self.assertNotIn(notes.TRUNCATED, full)
        half = summary.RESERVE_BYTES + len(full.encode("utf-8")) // 2
        for cap in (3000, half):
            with self.subTest(cap=cap):
                md, entries = summary.render_shard(shard_dir, cap=cap)
                self.assertLessEqual(len(md.encode("utf-8")), cap)
                self.assertEqual(md.rstrip("\n").splitlines()[-1], notes.TRUNCATED)
                self.assertEqual(md.splitlines()[0], PLUMBING)
                self.assertLess(len(entries), len(full_entries))
                self.assertEqual(entries, full_entries[:len(entries)])
                self.assertTrue(md.endswith("\n\n" + notes.TRUNCATED + "\n"))
                self.assertTrue(full.startswith(md[:-len(notes.TRUNCATED) - 2]))
                check_sources(self, md, entries, shard_dir)
        self.assertGreater(len(summary.render_shard(shard_dir, cap=half)[1]), 0)

    def test_plan_error_nothing_and_missing(self) -> None:
        error_dir = self.work / "E"
        error_dir.mkdir()
        write_json_atomic(error_dir / "plan-error.json", {
            "schema_version": 1, "kind": "lab_plan_error", "source": "request", "request": "lab/requests/x-1.json",
            "path": "$.job_minutes", "problem": "must be an int in [45, 330]"})
        md, entries = summary.render_plan(error_dir)
        self.assertEqual(md.splitlines()[0], f"## {notes.HEADINGS['refused']}")
        for text in ("`$.job_minutes`", "`must be an int in [45, 330]`", "`lab/requests/x-1.json`", "`request`",
                     notes.PLAN_FIX_HINT):
            self.assertIn(text, md)
        self.assertEqual(entries, [])
        check_sources(self, md, entries, error_dir)

        for notice, requests in ((notes.DELETE_ONLY, []),
                                 (notes.MERGE_SEVERAL, ["lab/requests/m0.json", "lab/requests/m1.json"])):
            nothing = self.work / f"N{len(requests)}"
            nothing.mkdir()
            write_json_atomic(nothing / "plan-nothing.json", {"schema_version": 1, "kind": "lab_plan_nothing",
                                                              "notice": notice, "requests": requests})
            md, entries = summary.render_plan(nothing)
            with self.subTest(notice=notice):
                self.assertEqual(md.splitlines()[0], f"## {notes.HEADINGS['nothing']}")
                self.assertIn("\n" + notice + "\n", md)
                self.assertIn(notes.DISPATCH_BY_HAND, md)
                for path in requests:
                    self.assertIn(f"`{path}`", md)
                check_sources(self, md, entries, nothing)

        (self.work / "empty").mkdir()
        for render, sentence in ((summary.render_plan, notes.NO_PLAN), (summary.render_report, notes.NO_REPORT),
                                 (summary.render_shard, notes.UNSEALED)):
            for directory in (self.work / "missing", self.work / "empty"):
                with self.subTest(render=render.__name__, directory=directory.name):
                    md, entries = render(directory)
                    self.assertIn(sentence, md)
                    self.assertTrue(md.splitlines()[0].startswith("## "), md)
                    self.assertEqual(entries, [])
                    check_sources(self, md, entries, directory)
        self.assertFalse((self.work / "missing").exists())
        unsealed = self.work / "U"
        shutil.copytree(self.summaries[1][0], unsealed)
        (unsealed / "status.json").unlink()
        md, entries = summary.render_shard(unsealed)
        self.assertEqual(md.splitlines()[0], PLUMBING)
        self.assertIn(notes.UNSEALED, md)

    def test_code_span_escaping(self) -> None:
        texts = ["plain", "a`b", "a``b", "a```b", "`lead", "trail`", " lead", "trail ", "`", "``", "a|b|c", "[x](y)",
                 "https://x.y/z", "<b>bold</b>", "digits 12.5 and -3", "\\`slash", "  ", "\ttab\nline"]
        for text in texts:
            for table in (False, True):
                with self.subTest(text=text, table=table):
                    rendered = summary.code(text, table=table)
                    outside, spans = split_code_spans(f"x {rendered} y")
                    self.assertEqual(outside, "x   y")
                    self.assertEqual(len(spans), 1)
                    shown = "".join(c if c.isprintable() else "�" for c in text)
                    self.assertEqual(span_text(spans[0], table=table), shown)
                    if table:
                        self.assertIsNone(re.search(r"(?<!\\)\|", rendered))
        self.assertEqual(summary.code(""), "n/a")
        self.assertEqual(summary.code(None), "n/a")
        self.assertEqual(summary.code("x\x00y"), "`x�y`")

        purpose = "Run `x` | see [link](https://e.x/a?b=1) <img> 42 ```fence``` done"
        plan = _json(self.out / "plan" / "plan.json")
        plan["request"]["purpose"] = purpose
        plan["models"]["fake-a"]["alias"] = "a|b`c"
        (self.work / "P").mkdir()
        write_json_atomic(self.work / "P" / "plan.json", plan)
        md, entries = summary.render_plan(self.work / "P")
        check_sources(self, md, entries, self.work / "P")
        outside, spans = split_code_spans(md)
        self.assertNotIn("<", outside)
        self.assertNotIn("[", outside)
        self.assertIn(purpose, [span_text(s) for s in spans])
        purpose_lines = [line for line in md.splitlines() if purpose.split()[0] in line and "|" in line]
        self.assertTrue(all(line.startswith(notes.COLUMNS["purpose"]) for line in purpose_lines), purpose_lines)
        table_cells = [span_text(s, table=True) for s in spans]
        self.assertIn("a|b`c", table_cells)

    def test_cli(self) -> None:
        root = self.summaries[1][0]
        step_summary = self.work / "step-summary.md"
        step_summary.write_text("before\n", encoding="utf-8")
        md_out, sources_out = self.work / "a" / "b" / "s.md", self.work / "c" / "s.json"
        r = lab_cli("lab.summary", "shard", "--dir", str(root), "--append-to", str(step_summary),
                    "--md-out", str(md_out), "--sources-out", str(sources_out))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        md = md_out.read_text(encoding="utf-8")
        self.assertEqual(step_summary.read_text(encoding="utf-8"), "before\n" + md)
        self.assertEqual(_json(sources_out)["sources"], summary.render_shard(root)[1])
        r = lab_cli("lab.summary", "shard", "--dir", str(root), "--append-to", str(step_summary))
        self.assertEqual(step_summary.read_text(encoding="utf-8"), "before\n" + md + md)
        r = lab_cli("lab.summary", "nonsense", "--dir", str(root))
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        inside = ROOT / "mycelic" / "lab-summary-never-created.md"
        r = lab_cli("lab.summary", "report", "--dir", str(root), "--md-out", str(inside))
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertFalse(inside.exists())
        blocker = self.work / "file"
        blocker.write_text("x")
        r = lab_cli("lab.summary", "plan", "--dir", str(root), "--md-out", str(blocker / "s.md"))
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        r = lab_cli("lab.summary", "report", "--dir", str(self.work / "absent"), "--md-out", str(self.work / "r.md"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn(notes.NO_REPORT, (self.work / "r.md").read_text(encoding="utf-8"))

    # ----------------------------------------------------------------------------------------------- --log

    def test_log_report(self) -> None:
        root = self.out / "report"
        md_out = self.work / "r.md"
        done = log_cli("report", "--dir", str(root), "--md-out", str(md_out))
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        blocks = parse_log(self, done.stdout.decode("utf-8"))
        self.assertEqual([(label, body is not None) for label, _, body, _ in blocks],
                         [("report/report.json", True), ("report/report.md", True),
                          ("report/lock-candidate.json", True)])
        (_, _, report_body, report_sha), (_, _, md_body, md_sha), (_, _, lock_body, lock_sha) = blocks
        report_bytes = (root / "report.json").read_bytes()
        report = json.loads("\n".join(report_body))
        self.assertEqual(report, _json(root / "report.json"))
        self.assertEqual(report_sha, _sha256(report_bytes))
        self.assertEqual(report_sha, _sha256((canonical_dumps(report) + "\n").encode("utf-8")))
        self.assertEqual((report_body[0], report_body[-1]), ("{", "}"))
        self.assertIn(f' "banner": {json.dumps(report["banner"], ensure_ascii=False)},', report_body)
        md = md_out.read_text(encoding="utf-8")
        self.assertEqual("\n".join(md_body) + "\n", md)
        self.assertEqual(md, summary.render_report(root)[0])
        self.assertEqual(md_sha, _sha256(md_out.read_bytes()))
        self.assertEqual(md_body[0], PLUMBING)
        lock_text = (root / "lock-candidate.json").read_text(encoding="utf-8")
        self.assertEqual("\n".join(lock_body) + "\n", lock_text)
        self.assertEqual(lock_sha, _sha256((root / "lock-candidate.json").read_bytes()))
        self.assertEqual([state for _, state, _, _ in blocks],
                         [str(len(report_body)), str(len(md_body)), str(len(lock_body))])
        self.assertGreater(len(report_body), 100)

    def test_log_shard_and_plan(self) -> None:
        shard = self.summaries[1][0]
        done = log_cli("shard", "--dir", str(shard))
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        (prov_label, _, prov_body, prov_sha), (md_label, _, md_body, md_sha) = parse_log(self,
                                                                                    done.stdout.decode("utf-8"))
        self.assertEqual((prov_label, md_label), ("shard/provenance.json", "shard/summary.md"))
        prov = json.loads("\n".join(prov_body))
        self.assertEqual(prov, _json(shard / "provenance.json"))
        self.assertEqual(prov_sha, _sha256((shard / "provenance.json").read_bytes()))
        self.assertEqual(prov_sha, _sha256((canonical_dumps(prov) + "\n").encode("utf-8")))
        md = summary.render_shard(shard)[0]
        self.assertEqual(("\n".join(md_body) + "\n", md_sha), (md, _sha256(md.encode("utf-8"))))
        self.assertEqual(md, (shard / "summary" / "summary.md").read_text(encoding="utf-8"))
        self.assertIn(f"`{prov['host']['cpu']['model_name']}`", md)

        plan_dir = self.out / "plan"
        done = log_cli("plan", "--dir", str(plan_dir))
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        ((label, state, body, sha),) = parse_log(self, done.stdout.decode("utf-8"))
        md = (plan_dir / "summary.md").read_text(encoding="utf-8")
        self.assertEqual((label, state), ("plan/summary.md", str(len(md.splitlines()))))
        self.assertEqual(("\n".join(body) + "\n", sha), (md, _sha256(md.encode("utf-8"))))
        self.assertEqual(body[0], PLUMBING)

    def test_log_absent_and_unreadable(self) -> None:
        done = log_cli("report", "--dir", str(self.work / "absent"))
        self.assertEqual(done.returncode, 0)
        blocks = parse_log(self, done.stdout.decode("utf-8"))
        absent_md = summary.render_report(self.work / "absent")[0]
        self.assertEqual([(label, state) for label, state, _, _ in blocks],
                         [("report/report.json", "ABSENT"), ("report/report.md", str(len(absent_md.splitlines()))),
                          ("report/lock-candidate.json", "ABSENT")])
        self.assertEqual("\n".join(blocks[1][2]) + "\n", absent_md)
        self.assertIn(notes.NO_REPORT, blocks[1][2])
        self.assertFalse((self.work / "absent").exists())
        done = log_cli("shard", "--dir", str(self.work / "absent"))
        self.assertEqual([state for _, state, _, _ in parse_log(self, done.stdout.decode("utf-8"))][0], "ABSENT")

        root = self.work / "R"
        shutil.copytree(self.out / "report", root)
        md = summary.render_report(root)[0]
        cases = [
            ("report.json", b"not json\n", "report/report.json", "UNREADABLE sha256=" + _sha256(b"not json\n")),
            ("lock-candidate.json", b"\xff\n", "report/lock-candidate.json", "UNREADABLE sha256=" + _sha256(b"\xff\n")),
            ("lock-candidate.json", b"{}", "report/lock-candidate.json", "UNREADABLE sha256=" + _sha256(b"{}")),
            ("lock-candidate.json", b"{\r\n}\r\n", "report/lock-candidate.json",
             "UNREADABLE sha256=" + _sha256(b"{\r\n}\r\n")),
            ("lock-candidate.json", b"{\n::warning::x\n}\n", "report/lock-candidate.json",
             "UNREADABLE sha256=" + _sha256(b"{\n::warning::x\n}\n")),
            ("lock-candidate.json", b"{\n=== MYCELIC-LAB x END ===\n}\n", "report/lock-candidate.json",
             "UNREADABLE sha256=" + _sha256(b"{\n=== MYCELIC-LAB x END ===\n}\n")),
            ("report.json", b"{\"a\": 1, \"a\": 2}\n", "report/report.json",
             "UNREADABLE sha256=" + _sha256(b"{\"a\": 1, \"a\": 2}\n")),
        ]
        for rel, data, label, state in cases:
            with self.subTest(rel=rel, data=data):
                saved = (root / rel).read_bytes()
                (root / rel).write_bytes(data)
                try:
                    blocks = {b[0]: b for b in parse_log(self, summary.log_text("report", root, md))}
                finally:
                    (root / rel).write_bytes(saved)
                self.assertEqual(blocks[label][1:3], (state, None))
                self.assertEqual(sum(b[2] is not None for b in blocks.values()), 2)
        (root / "lock-candidate.json").unlink()
        (root / "lock-candidate.json").mkdir()
        blocks = parse_log(self, summary.log_text("report", root, md))
        self.assertEqual(blocks[2][:2], ("report/lock-candidate.json", "UNREADABLE sha256=none"))
        blocks = parse_log(self, summary.log_text("report", root, "lone \ud800 surrogate\n"))
        self.assertEqual(blocks[1][:2], ("report/report.md", "UNREADABLE sha256=none"))

    def test_log_keeps_workflow_commands_inert(self) -> None:
        """A run file's text that holds workflow commands stays inside the stop-commands window, JSON-escaped, on
        no line of its own: the runner acts on none of them and the reader still gets the exact file."""
        shard = self.work / "S"
        shutil.copytree(self.summaries[1][0], shard)
        injected = "x\n::error::x\n##[add-mask]y\n::stop-commands::z\n=== MYCELIC-LAB shard/summary.md END ===\nü ✓"
        prov = _json(shard / "provenance.json")
        prov["banner"] = injected
        prov["host"]["cpu"]["model_name"] = "::warning::cpu\n##[error]cpu"
        write_json_atomic(shard / "provenance.json", prov)
        done = log_cli("shard", "--dir", str(shard))
        self.assertEqual(done.returncode, 0, done.stderr)
        out = done.stdout.decode("utf-8")
        blocks = parse_log(self, out)
        self.assertEqual([state.isdigit() for _, state, _, _ in blocks], [True, True])
        body = blocks[0][2]
        self.assertEqual(json.loads("\n".join(body)), prov)
        self.assertEqual(blocks[0][3], _sha256((shard / "provenance.json").read_bytes()))
        lines = out[:-1].split("\n")
        for line in lines[1:-1]:
            self.assertFalse(line.startswith(("::", "##[")), line)
        start, end = len(lines[0]), out.rindex("\n" + lines[-1])
        for text in ("::error::x", "##[add-mask]y", "::warning::cpu", "##[error]cpu", "::stop-commands::z"):
            with self.subTest(text=text):
                at = [m.start() for m in re.finditer(re.escape(text), out)]
                self.assertTrue(at)
                self.assertTrue(all(start < a < end for a in at))
        self.assertIn(' "banner": ' + json.dumps(injected, ensure_ascii=False) + ",", body)

    def test_log_too_large(self) -> None:
        root = self.out / "report"
        md = summary.render_report(root)[0]
        report_bytes = (root / "report.json").read_bytes()
        lock_bytes = (root / "lock-candidate.json").read_bytes()
        pretty = json.dumps(json.loads(report_bytes), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        self.assertGreater(len(pretty.encode("utf-8")), len(report_bytes))
        cap = len(lock_bytes)
        with mock.patch.object(summary, "MAX_LOG_BYTES", cap):
            blocks = parse_log(self, summary.log_text("report", root, md))
        self.assertEqual([b[1] for b in blocks], [
            f"TOO-LARGE bytes={len(report_bytes)} sha256={_sha256(report_bytes)}",
            f"TOO-LARGE bytes={len(md.encode('utf-8'))} sha256={_sha256(md.encode('utf-8'))}",
            str(len(lock_bytes.decode("utf-8").splitlines()))])
        with mock.patch.object(summary, "MAX_LOG_BYTES", cap - 1):
            blocks = parse_log(self, summary.log_text("report", root, md))
        self.assertEqual(blocks[2][1], f"TOO-LARGE bytes={cap} sha256={_sha256(lock_bytes)}")
        # the file fits, its pretty body does not: the body's size, the file's hash, and no part of the body
        with mock.patch.object(summary, "MAX_LOG_BYTES", len(report_bytes)):
            out = summary.log_text("report", root, md)
        blocks = parse_log(self, out)
        self.assertEqual(blocks[0][1], f"TOO-LARGE bytes={len(pretty.encode('utf-8'))} sha256={_sha256(report_bytes)}")
        self.assertNotIn('"banner"', out)
        self.assertEqual(summary.MAX_LOG_BYTES, 2_000_000)

    def test_log_changes_no_output_and_no_exit_code(self) -> None:
        root = self.summaries[1][0]
        outputs = {}
        for flag in ((), ("--log",)):
            out = self.work / ("with" if flag else "without")
            step_summary = out / "step.md"
            r = lab_cli("lab.summary", "shard", "--dir", str(root), "--append-to", str(step_summary), "--md-out",
                        str(out / "s.md"), "--sources-out", str(out / "s.json"), *flag)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout == "", not flag)
            outputs[flag] = [(out / name).read_bytes() for name in ("step.md", "s.md", "s.json")]
        self.assertEqual(outputs[()], outputs[("--log",)])
        # an output inside a forbidden root: exit 2 and nothing printed; an output that cannot be written: exit 2,
        # the log already printed
        done = log_cli("report", "--dir", str(self.out / "report"), "--md-out",
                       str(ROOT / "mycelic" / "lab-summary-never-created.md"))
        self.assertEqual((done.returncode, done.stdout), (2, b""))
        blocker = self.work / "file"
        blocker.write_text("x")
        done = log_cli("plan", "--dir", str(self.out / "plan"), "--md-out", str(blocker / "s.md"))
        self.assertEqual(done.returncode, 2)
        self.assertEqual([label for label, _, _, _ in parse_log(self, done.stdout.decode("utf-8"))],
                         ["plan/summary.md"])

    def test_new_notes_constants_digit_free(self) -> None:
        names = ("PLUMBING_CHECK_LINE", "PLUMBING_HOSTED_LINE", "NO_MEASUREMENT_LINE", "TRUNCATED", "NO_PLAN",
                 "NO_REPORT", "UNSEALED", "NOT_RUN", "NO_ARTIFACT", "OTHER_PLAN", "ALTERED", "AMBIGUOUS_ARTIFACTS",
                 "FILES_DIFFER", "UNIT_RECORD_INVALID", "STEP_FAILED", "CPU_MODELS_DIFFER", "WORLD_DIGEST_DIFFERS",
                 "WORLD_DIGEST_SAME", "NOT_PINNED", "DISPATCH_BY_HAND", "PLAN_FIX_HINT", "LOCK_UNCHANGED", "LOCK_NEW",
                 "LOCK_CONFLICT_NOTE", "LOCK_NOT_COMPUTED", "G0_MODEL_PATH", "BY_CONSTRUCTION_LABEL",
                 "BY_CONSTRUCTION_NOTE", "OPENFDA_SAW_RECALLS", "OPENFDA_WARNED", "OPENFDA_FALSE_ALARM_SCOPE",
                 "E1_SCORES_NOTE", "E1_SCORES_ONLY", "E1_SCORES_REFUSED", "E1_SCORES_UNPINNED", "REAGGREGATION_LINE",
                 "REAGGREGATE_NO_CHANGE", "REAGGREGATE_BAD_NAME")
        values = [(name, getattr(notes, name)) for name in names]
        values += [(f"HEADINGS.{k}", v) for k, v in notes.HEADINGS.items()]
        values += [(f"COLUMNS.{k}", v) for k, v in notes.COLUMNS.items()]
        values += [(f"NOTES.{k}", v) for k, v in notes.NOTES.items()] + [("PLUMBING_BANNER", notes.PLUMBING_BANNER)]
        values += [("PLUMBING_HOSTED_BANNER", notes.PLUMBING_HOSTED_BANNER)]
        self.assertEqual(notes.PLUMBING_CHECK_LINE, "PLUMBING CHECK: no model was run")
        for name, value in values:
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
                self.assertIsNone(re.search(r"[0-9`<\[|]", value))
        for word in ("p50", "p95", "P50", "P95"):
            self.assertNotIn(word, " ".join(notes.COLUMNS.values()))
        sim_names = ("SIM_PROJECTED", "SIM_LOW_PARTICIPATION", "SIZING_NOTE", "SIM_WORLD_SAME", "SIM_WORLD_DIFFERS")
        sim_values = [(name, getattr(notes, name)) for name in sim_names]
        for table in ("SIM_CHANNEL_LABELS", "SIM_LIFT_LABELS", "SIM_NOTES", "SIM_MEASUREMENT_REASONS"):
            sim_values += [(f"{table}.{k}", v) for k, v in getattr(notes, table).items()]
        for name, value in sim_values:
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
                self.assertIsNone(re.search(r"[0-9`<\[|]", value))
        new_keys = ("sim", "sim-lifts", "sim-pushdown", "sizing", "channel", "found", "patterns", "recall",
                    "p_at_forty", "ap", "alerts", "false_alarms", "lift", "estimate", "ci_low", "ci_high",
                    "candidates", "true", "supported", "ap_pushdown", "ap_stats", "raw_text", "fallback_share",
                    "records_done", "extract_median_s", "judge_median_s", "estimate_minutes", "suggested_minutes",
                    "plant", "weeks", "found_net", "chance_found", "model_path_problems", "decision_metric", "diff")
        for key in new_keys:
            with self.subTest(key=key):
                self.assertIn(key, {**notes.HEADINGS, **notes.COLUMNS})
                self.assertIsNone(re.search(r"[0-9]", key))
        self.assertEqual(list(notes.SIM_CHANNEL_LABELS), ["X_model", "X_lexical", "S", "R_mf", "U", "single_site",
                                                          "rules"])
        self.assertEqual({k: notes.COLUMNS[k] for k in ("found_net", "chance_found", "model_path_problems",
                                                        "decision_metric", "diff")},
                         {"found_net": "found net of chance", "chance_found": "chance finds",
                          "model_path_problems": "model path problems", "decision_metric": "decision metric",
                          "diff": "difference"})
        for key in ("plan", "refused", "nothing", "shard", "report", "shards", "units", "no-result", "model",
                    "unverified", "plumbing", "no-model", "e3", "g0", "latency", "provision", "lock", "notes"):
            self.assertIn(key, notes.HEADINGS)
        self.assertEqual(notes.HEADINGS["model"], "Model on runner CPU")
        self.assertEqual(notes.HEADINGS["unverified"], "Unverified: not measurements")
        self.assertEqual(notes.HEADINGS["plumbing"], "Plumbing checks (fake provider): not model measurements")


class OpenFDACaveatTests(unittest.TestCase):
    """The openFDA section says, before its tables, whether the requester saw recall outcomes first, what the replay
    warned and what its false alarms cover; X1's warnings follow its eligibility."""

    def render(self, rows: list[dict[str, Any]], key: str = "openfda") -> tuple[str, list[dict[str, Any]], Path]:
        tmp = Path(tempfile.mkdtemp(prefix="lab-summary-openfda-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        report = {"kind": "lab_report", "result_class": "real", "contains_measurements": False, "unit_count": 0,
                  "shard_count": 0, key: rows,
                  "units": [{"unit": r["unit"], "experiment": key, "model": None, "status": "ok",
                             "display_class": "no-model", "shard": "s001-none", "wall_s": 1.0} for r in rows]}
        write_json_atomic(tmp / "report.json", report)
        md, sources = summary.render_report(tmp)
        check_sources(self, md, sources, tmp)
        return md, sources, tmp

    @staticmethod
    def row(unit: str, seen: Any, warnings: Any) -> dict[str, Any]:
        channel = {"in_scope": 3, "found": 0, "recall_rate": 0.0, "median_lead_days": None, "post_recall_alerts": 0,
                   "false_alarms": 0, "false_alarms_per_week": 0.0, "reason": None}
        return {"unit": unit, "display_class": "no-model", "data_label": "public",
                "recall_outcomes_seen_before_prereg": seen, "warnings": warnings,
                "channels": {name: dict(channel) for name in ("R_mf", "S", "X")},
                "fetch": {"event": {"AAA": {"total": 4, "fetched": 4, "truncated": False, "reason": None}}},
                "sheets": {"n1": None, "e1": None}}

    def test_declaration_and_warnings_before_the_table(self) -> None:
        warning = ("fewer_sites_than_min_sites: fewer sites than the detectors' min_sites, so no cross-site "
                   "candidate can form")
        md, _, _ = self.render([self.row("openfda", True, [warning, "low_partition_coverage: fewer than 0.5"])])
        lines = md.splitlines()
        table = next(i for i, line in enumerate(lines) if line.startswith("| unit | channel | recalls in scope"))
        for sentence in (notes.OPENFDA_LABEL, notes.OPENFDA_SAW_RECALLS, notes.OPENFDA_WARNED,
                         notes.OPENFDA_FALSE_ALARM_SCOPE):
            with self.subTest(sentence=sentence[:40]):
                self.assertEqual(lines.count(sentence), 1)
                self.assertLess(lines.index(sentence), table)
        line = (f"- `openfda`: {notes.COLUMNS['saw_recalls']} yes; {notes.COLUMNS['warnings']}: `{warning}`, "
                "`low_partition_coverage: fewer than 0.5`")
        self.assertIn(line, lines)
        self.assertLess(lines.index(line), table)

    def test_no_declaration_and_no_warnings(self) -> None:
        md, _, _ = self.render([self.row("openfda", False, []), self.row("openfda-b", None, None)])
        self.assertNotIn(notes.OPENFDA_SAW_RECALLS, md)
        self.assertNotIn(notes.OPENFDA_WARNED, md)
        self.assertIn(notes.OPENFDA_FALSE_ALARM_SCOPE, md.splitlines())
        self.assertIn(f"- `openfda`: {notes.COLUMNS['saw_recalls']} no; {notes.COLUMNS['warnings']}: none",
                      md.splitlines())
        self.assertIn(f"- `openfda-b`: {notes.COLUMNS['saw_recalls']} n/a; {notes.COLUMNS['warnings']}: n/a",
                      md.splitlines())

    def test_x1_warnings_after_eligibility(self) -> None:
        row = {"unit": "x1", "display_class": "no-model", "stamps": {"blind": False, "extractor": "lexical"},
               "channels": {}, "lifts": {}, "x1": {"eligible": False, "reasons": ["fewer than 20 patterns"]},
               "warnings": ["fewer than 10 patterns: the lift intervals are unstable"], "by_construction": None}
        md, _, _ = self.render([row], key="x1")
        self.assertIn(f"- `x1`: {notes.COLUMNS['eligible']} no; {notes.COLUMNS['blind']} no; "
                      f"{notes.COLUMNS['extractor']} `lexical`; {notes.COLUMNS['reason']}: `fewer than 20 patterns`; "
                      f"{notes.COLUMNS['warnings']}: `fewer than 10 patterns: the lift intervals are unstable`",
                      md.splitlines())
        self.assertNotIn(notes.HEADINGS["by-construction"], md)


class G0ModelPathColumnTests(unittest.TestCase):
    """The G0 table's model path problems column: only when a row counts them, every count sourced."""

    def render(self, problems: list[Any]) -> tuple[str, list[dict[str, Any]], Path]:
        tmp = Path(tempfile.mkdtemp(prefix="lab-summary-g0-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        rows = [{"unit": f"g0-m{i}", "model": f"m{i}", "display_class": "unverified", "pack": "device_quality",
                 "seed": 1, "records": 1000, "passed": False, "canaries_planted": 20, "hit_count": 0,
                 "shingle_overlap_bytes": 0, "positive_control": {"canary_hits": 20}, "world_digest": "a" * 64,
                 "protocol_records": 1000, "below_protocol": False, "model_path_problems": n}
                for i, n in enumerate(problems)]
        report = {"kind": "lab_report", "result_class": "real", "contains_measurements": False, "unit_count": 0,
                  "shard_count": 0, "g0": rows,
                  "units": [{"unit": r["unit"], "experiment": "g0", "model": r["model"], "status": "invalid",
                             "display_class": "unverified", "shard": "s001-x", "wall_s": 1.0} for r in rows]}
        write_json_atomic(tmp / "report.json", report)
        md, sources = summary.render_report(tmp)
        return md, sources, tmp

    def test_column_only_with_counts(self) -> None:
        md, sources, root = self.render([None, None])
        check_sources(self, md, sources, root)
        self.assertNotIn(notes.COLUMNS["model_path_problems"], md)
        md, sources, root = self.render([2, None])
        check_sources(self, md, sources, root)
        g0 = _section(md, notes.HEADINGS["g0"]).splitlines()
        self.assertIn("| unit | model | pack | seed | records | passed | canaries planted | canary hits | shingle "
                      "overlap bytes | positive control hits | model path problems | world digest |", g0)
        self.assertIn("| `g0-m0` | `m0` | `device_quality` | 1 | 1000 | no | 20 | 0 | 0 | 20 | 2 | `aaaaaaaaaaaa` |",
                      g0)
        self.assertIn("| `g0-m1` | `m1` | `device_quality` | 1 | 1000 | no | 20 | 0 | 0 | 20 | n/a | `aaaaaaaaaaaa` "
                      "|", g0)
        self.assertEqual([e["pointer"] for e in sources if e["pointer"].endswith("model_path_problems")],
                         ["/g0/0/model_path_problems"])


if __name__ == "__main__":
    unittest.main()

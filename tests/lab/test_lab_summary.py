"""Summaries: every number outside a code span is read from a run file (the sources list says which, in order), the
first line says when nothing measures a model, the classes are kept apart, the size stays under GitHub's cap with
whole rows, and free text can never break out of its code span.

One dry run of ``lab/requests/plumbing-001.json`` (with ``lab/models.json``) is shared; model-class trees are copies
of it, mutated and re-sealed (``helpers.DryTree``). Nothing here measures a model.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from lab import notes, summary
from lab.units import CHECK_KEYS, display_class
from mycelic.collective.experiments.common import write_json_atomic
from tests.lab.helpers import (LAB_MANIFEST, PLUMBING_001, ROOT, DryTree, check_sources, kill_mentioning, lab_cli,
                               lab_env, span_text, split_code_spans)

PLUMBING = notes.PLUMBING_CHECK_LINE


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
        self.assertEqual(len(self.summaries), 4)
        modes = ["plan", "shard", "shard", "report"]
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
        tree.make_real("s001-fake-a", model_units=("e3-fake-a",))
        tree.make_real("s002-fake-b", units=("g0-fake-b",))
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
        for shard in ("s001-fake-a", "s002-fake-b"):
            text, shard_entries = summary.render_shard(tree.path(shard))
            check_sources(self, text, shard_entries, tree.path(shard))
        first = summary.render_shard(tree.path("s001-fake-a"))[0].splitlines()[0]
        second = summary.render_shard(tree.path("s002-fake-b"))[0].splitlines()[0]
        self.assertEqual((first, second), (f"## {notes.HEADINGS['shard']} `s001-fake-a`", notes.NO_MEASUREMENT_LINE))

    def test_plumbing_first_line_and_heading(self) -> None:
        self.assertEqual(self.done.stdout.splitlines()[0], "plan: 3 units in 2 shards (plumbing)")
        self.assertEqual(self.done.stdout.splitlines()[-1],
                         "lab: aggregate units 3 shards 2 class plumbing measurements false lock unchanged")
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
        tree.make_real("s001-fake-a", units=("e3-fake-a",))
        tree.make_real("s002-fake-b", units=("g0-fake-b", "e3-fake-b"))
        code, _, stderr, report = tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        self.assertEqual({u["display_class"] for u in report["units"]}, {"unverified"})
        self.assertEqual((report["result_class"], report["contains_measurements"]), ("real", False))
        md, entries = summary.render_report(self.work / "R")
        self.assertEqual(md.splitlines()[0], notes.NO_MEASUREMENT_LINE)
        self.assertNotIn(notes.HEADINGS["model"], md)
        check_sources(self, md, entries, self.work / "R")
        for shard in ("s001-fake-a", "s002-fake-b"):
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
        tree.make_real("s001-fake-a", model_units=("e3-fake-a",))
        tree.make_real("s002-fake-b", model_units=("g0-fake-b", "e3-fake-b"))
        tree.edit("s002-fake-b", "units/g0-fake-b/unit.json", lambda r: r.update(fake_rows=5))
        tree.edit("s002-fake-b", "units/e3-fake-b/unit.json",
                  lambda r: r["class_checks"].update(model_served=False))
        tree.reseal("s002-fake-b")
        code, _, stderr, report = tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        classes = {u["unit"]: u["display_class"] for u in report["units"]}
        self.assertEqual(classes, {"e3-fake-a": "model", "g0-fake-b": "plumbing", "e3-fake-b": "unverified"})
        md, _ = summary.render_report(self.work / "R")
        model = _section(md, notes.HEADINGS["model"])
        self.assertNotIn("`g0-fake-b`", model)
        self.assertNotIn("`e3-fake-b`", model)
        self.assertIn("`g0-fake-b`", _section(md, notes.HEADINGS["plumbing"]))
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

    def test_new_notes_constants_digit_free(self) -> None:
        names = ("PLUMBING_CHECK_LINE", "NO_MEASUREMENT_LINE", "TRUNCATED", "NO_PLAN", "NO_REPORT", "UNSEALED",
                 "NOT_RUN", "NO_ARTIFACT", "OTHER_PLAN", "ALTERED", "AMBIGUOUS_ARTIFACTS", "FILES_DIFFER",
                 "UNIT_RECORD_INVALID", "STEP_FAILED", "CPU_MODELS_DIFFER", "WORLD_DIGEST_DIFFERS", "WORLD_DIGEST_SAME",
                 "NOT_PINNED", "DISPATCH_BY_HAND", "PLAN_FIX_HINT", "LOCK_UNCHANGED", "LOCK_NEW",
                 "LOCK_CONFLICT_NOTE", "LOCK_NOT_COMPUTED")
        values = [(name, getattr(notes, name)) for name in names]
        values += [(f"HEADINGS.{k}", v) for k, v in notes.HEADINGS.items()]
        values += [(f"COLUMNS.{k}", v) for k, v in notes.COLUMNS.items()]
        values += [(f"NOTES.{k}", v) for k, v in notes.NOTES.items()] + [("PLUMBING_BANNER", notes.PLUMBING_BANNER)]
        self.assertEqual(notes.PLUMBING_CHECK_LINE, "PLUMBING CHECK: no model was run")
        for name, value in values:
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
                self.assertIsNone(re.search(r"[0-9`<\[|]", value))
        for word in ("p50", "p95", "P50", "P95"):
            self.assertNotIn(word, " ".join(notes.COLUMNS.values()))
        for key in ("plan", "refused", "nothing", "shard", "report", "shards", "units", "no-result", "model",
                    "unverified", "plumbing", "no-model", "e3", "g0", "latency", "provision", "lock", "notes"):
            self.assertIn(key, notes.HEADINGS)
        self.assertEqual(notes.HEADINGS["model"], "Model on runner CPU")
        self.assertEqual(notes.HEADINGS["unverified"], "Unverified: not measurements")
        self.assertEqual(notes.HEADINGS["plumbing"], "Plumbing checks (fake provider): not model measurements")


if __name__ == "__main__":
    unittest.main()

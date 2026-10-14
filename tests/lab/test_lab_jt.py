"""Tests of the term-drafting kind ``jt`` (accuracy test A003, build 1: ``docs/collective/A003/CHOICE-A003.md`` as
amended C1 to C32, "The build plan"; ``docs/collective/A003/BUILD-A003.md``) and of step (0), A002's golden outputs.

No NHTSA record is read and no artifact is downloaded: every archive here is ``tests/lab/j4_data.py``'s or
``tests/lab/j3_data.py``'s synthetic one, every value invented.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.lab import a002_golden


class _Golden:
    """The j3 template dry run on the code under test, run once for the class (about two minutes)."""

    _tmp: tempfile.TemporaryDirectory[str] | None = None
    proc = None
    out: Path | None = None

    @classmethod
    def get(cls) -> type[_Golden]:
        if cls._tmp is None:
            cls._tmp = tempfile.TemporaryDirectory(prefix="lab-a002-golden-")
            cls.proc, cls.out = a002_golden.dry_run(Path(cls._tmp.name))
        return cls


class A002GoldenTests(unittest.TestCase):
    """Step (0) and C30: the j3 template dry run's plan summary, ``report.json`` and ``report.md`` equal the bytes
    recorded on the base commit (``tests/lab/data/a002_golden/``), after the normalisation of
    ``tests/lab/a002_golden.py`` (the synthetic archive's sha256, the values that name the code, the machine's and
    the clock's figures, the manifest's model file names), and nothing else is excluded."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Golden.get()

    def test_the_dry_run_ran(self) -> None:
        self.assertEqual(self.dry.proc.returncode, 0, self.dry.proc.stdout[-3000:] + self.dry.proc.stderr[-3000:])
        self.assertIn("prereg: e1 no x1 no e2 no j1 no j3 yes", self.dry.proc.stdout.splitlines())

    def test_each_output_equals_the_recorded_bytes(self) -> None:
        found = a002_golden.outputs(self.dry.out)
        self.assertEqual(sorted(found), sorted(a002_golden.OUTPUTS))
        for name, data in found.items():
            with self.subTest(output=name):
                expected = (a002_golden.GOLDEN / name).read_bytes()
                if data != expected:
                    got, want = data.decode("utf-8").split("\n"), expected.decode("utf-8").split("\n")
                    first = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
                    self.fail(f"{name} differs from the recording at line {first + 1}")

    def test_the_recording_holds_only_the_named_tokens(self) -> None:
        summary = (a002_golden.GOLDEN / "summary.md").read_text(encoding="utf-8")
        report_md = (a002_golden.GOLDEN / "report.md").read_text(encoding="utf-8")
        report = json.loads((a002_golden.GOLDEN / "report.json").read_bytes())
        self.assertEqual(summary.count(a002_golden.COMMIT), 1)
        self.assertEqual(summary.count(a002_golden.PREREG), 1)
        self.assertEqual(summary.count(a002_golden.ARCHIVE), 1)
        self.assertEqual(report_md.count(a002_golden.ARCHIVE), 1)
        self.assertEqual((report["plan"]["git_sha"], report["plan"]["sha256"]), (a002_golden.COMMIT, a002_golden.PLAN))
        self.assertEqual(report["j3"]["exclusion"]["complaint_file_sha256"], a002_golden.ARCHIVE)
        # what is pinned: every score, count and hash of the draw, as the template dry run printed them
        self.assertEqual(report["j3"]["labels"]["sha256"][:12], "f95b4ed9db9c")
        self.assertEqual(report["j3"]["comparisons"], {"this_cycle": 9, "before": 12, "since_loop_began": 21})
        self.assertIn("excluded refs file sha `c1e1a63cee69`; chain file lines 450", summary)

    def test_the_normalisation_leaves_a_changed_figure_visible(self) -> None:
        raw = (self.dry.out / "report" / "report.json").read_bytes()
        doc = json.loads(raw)
        doc["j3"]["comparisons"]["since_loop_began"] = 22
        from mycelic.collective.jsonio import canonical_dumps
        changed = (canonical_dumps(doc) + "\n").encode("utf-8")
        self.assertNotEqual(a002_golden.normalise_json(changed), a002_golden.normalise_json(raw))
        doc = json.loads(raw)
        doc["units"][0]["wall_s"] = 99.0
        doc["j3"]["exclusion"]["complaint_file_sha256"] = "0" * 64
        timed = (canonical_dumps(doc) + "\n").encode("utf-8")
        self.assertEqual(a002_golden.normalise_json(timed), a002_golden.normalise_json(raw))
        md = (self.dry.out / "report" / "report.md").read_text(encoding="utf-8")
        self.assertNotEqual(a002_golden.normalise_md(md.replace("0.700", "0.701")), a002_golden.normalise_md(md))
        with self.assertRaises(a002_golden.GoldenError):
            a002_golden.normalise_json(json.dumps(json.loads(raw), indent=1).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()

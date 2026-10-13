"""M2 and M11: the suites CLI (``research/e2e_ingest/suites.py``) and criterion 10's check over its rows.

A tiny pytest file in a temporary directory stands in for a suite: the CLI runs it with junit output, writes its
row, and the reader binds the row to the junit file beside it. The seven real suites are not run here (the loop and
the CI jobs run them); their names, floors and zero-skip scopes are compared with the brief by
``test_workflow.SettingsTests``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from mycelic.collective.experiments.common import write_json_atomic
from mycelic.collective.jsonio import sha256_hex
from research.e2e_ingest import report as rp
from research.e2e_ingest import suites as su
from tests.e2e_ingest.helpers import suite_rows
from tests.e2e_ingest.test_harness import put

TINY = '''
import unittest


class TinyTests(unittest.TestCase):
    def test_one(self):
        self.assertTrue(True)

    def test_two(self):
        self.assertEqual(1 + 1, 2)

    @unittest.skip("a skipped case")
    def test_skipped(self):
        pass
'''

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="2" skipped="3" tests="10" time="12.5">
<testcase classname="tests.mycelic.test_jetstream.JetStreamTests" name="test_a" time="0.1">
<skipped message="no broker"/></testcase>
<testcase classname="tests.mycelic.test_jetstream.JetStreamTests" name="test_b" time="0.1">
<skipped message="no broker"/></testcase>
<testcase classname="tests.mycelic.test_api.ApiTests" name="test_c" time="0.1"><skipped message="x"/></testcase>
<testcase classname="tests.mycelic.test_api.ApiTests" name="test_d" time="0.1"/>
</testsuite></testsuites>
"""


class JunitTests(unittest.TestCase):
    def test_the_counts_are_summed_and_the_scoped_skips_counted_by_classname(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.xml"
            path.write_text(JUNIT, encoding="utf-8")
            whole = su.junit_summary(path, "")
            self.assertEqual((whole["tests"], whole["failures"], whole["errors"], whole["skipped"]), (10, 2, 1, 3))
            self.assertEqual((whole["duration_s"], whole["skipped_in_scope"], whole["testcases"]), (12.5, 3, 4))
            scoped = su.junit_summary(path, su.JETSTREAM_CLASSNAME)
            self.assertEqual(scoped["skipped_in_scope"], 2)
            self.assertIsNone(su.junit_summary(path, None)["skipped_in_scope"])


class RunSuiteTests(unittest.TestCase):
    """The CLI on a tiny suite: the junit file, the row, the binding, the reader and ``--verify``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="e2e-suites-")
        cls.src = Path(cls.tmp) / "src"
        cls.src.mkdir()
        (cls.src / "test_tiny.py").write_text(TINY, encoding="utf-8")
        cls.out = Path(cls.tmp) / "suites"
        cls.suite = su.Suite("test_tiny.py", "tiny", "core", 2, zero_skip_scope="")
        cls.row = su.run_suite(cls.suite, cls.out, sha="b" * 40, cwd=cls.src)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_row_counts_the_junit_file_and_binds_to_it(self) -> None:
        row = self.row
        self.assertEqual((row["suite"], row["slug"], row["group"], row["floor"], row["sha"]),
                         ("test_tiny.py", "tiny", "core", 2, "b" * 40))
        self.assertEqual((row["tests"], row["failures"], row["errors"], row["skipped"], row["skipped_in_scope"]),
                         (3, 0, 0, 1, 1))
        self.assertEqual((row["exit_code"], row["junit_present"], row["junit_file"]), (0, True, "tiny.xml"))
        self.assertEqual(row["junit_sha256"], sha256_hex((self.out / "tiny.xml").read_bytes()))
        self.assertTrue(su.green(row))
        self.assertEqual(json.loads((self.out / "tiny.json").read_text(encoding="utf-8")), row)
        self.assertTrue((self.out / "tiny.log").is_file())
        self.assertEqual(row["command"][1:5], ["-m", "pytest", "test_tiny.py", "-q"])

    def test_a_suite_that_leaves_no_junit_file_is_one_error_never_green(self) -> None:
        # an interpreter that exits before pytest writes anything (the junit file is pytest's own)
        silent = su.Suite("test_tiny.py", "silent", "core", 1)
        row = su.run_suite(silent, self.out, sha="b" * 40, cwd=self.src, python="/bin/false")
        self.assertEqual((row["junit_present"], row["errors"], row["junit_sha256"]), (False, 1, None))
        self.assertNotEqual(row["exit_code"], 0)
        self.assertFalse(su.green(row))

    def test_a_suite_pytest_cannot_find_collects_nothing_and_is_never_green(self) -> None:
        missing = su.Suite("test_missing.py", "missing", "core", 1)
        row = su.run_suite(missing, self.out, sha="b" * 40, cwd=self.src)
        self.assertNotEqual(row["exit_code"], 0)
        self.assertEqual(row["tests"], 0)
        self.assertFalse(su.green(row))
        self.assertFalse(su.green({**self.row, "tests": 0}))

    def test_the_reader_binds_every_row_to_its_junit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for suite in su.SUITES:
                shutil.copy(self.out / "tiny.xml", out / f"{suite.slug}.xml")
                row = {**self.row, "suite": suite.name, "slug": suite.slug, "junit_file": f"{suite.slug}.xml"}
                write_json_atomic(out / f"{suite.slug}.json", row)
            found = su.read_evidence(out)
            self.assertEqual((found["evidence"], found["problems"], len(found["rows"])), (su.EVIDENCE, [], 7))
            self.assertTrue(all(r["junit_bound"] for r in found["rows"]))
            self.assertEqual(su.read_evidence(out, "lab")["rows"][0]["suite"], "tests/lab")
            # a tampered junit file unbinds its row; a missing row is a problem; neither is evidence
            (out / "tests-lab.xml").write_text(JUNIT, encoding="utf-8")
            found = su.read_evidence(out)
            self.assertIsNone(found["evidence"])
            self.assertEqual([r["junit_bound"] for r in found["rows"] if r["suite"] == "tests/lab"], [False])
            self.assertEqual(su.read_evidence(out, "core")["evidence"], su.EVIDENCE)
            (out / "tests-onboard.json").unlink()
            found = su.read_evidence(out)
            self.assertEqual(len(found["rows"]), 6)
            self.assertTrue(any("tests/onboard" in p for p in found["problems"]))
            self.assertIsNone(su.read_evidence(None)["evidence"])
            block = su.evidence_block(out)
            self.assertEqual(sorted(block), ["needs_results", "suites", "suites_dir", "suites_evidence",
                                             "suites_problems"])

    def test_verify_reads_a_directory_and_exits_as_its_rows_say(self) -> None:
        self.assertEqual(su.main(["--out", str(self.out), "--verify"]), 1)        # six of the seven are missing
        self.assertEqual(su.main(["--out", str(self.out), "--suite", "nope"]), 2)
        self.assertEqual(su.main(["--out", str(self.out), "--group", "lab", "--suite", "tests/mycelic"]), 2)

    def test_needs_results_come_from_the_workflows_environment(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in su.NEEDS_ENV.values():
                os.environ.pop(name, None)
            self.assertIsNone(su.needs_results())
            os.environ[su.NEEDS_ENV["suites-core"]] = "success"
            self.assertEqual(su.needs_results(), {"suites-core": "success", "suites-lab": None})


class C10Tests(unittest.TestCase):
    """Criterion 10 over the rows: a green run block passes, one mutation per assertion, the CI-only assertions."""

    def setUp(self) -> None:
        self.doc: dict[str, Any] = {"run": {"code_commit": "a" * 40, "code_dirty_start": False,
                                            "code_dirty_end": False, "suites": suite_rows("a" * 40),
                                            "suites_evidence": su.EVIDENCE,
                                            "needs_results": {"suites-core": "success", "suites-lab": "success"},
                                            "github_run_id": "123"}}

    def test_a_green_run_block_passes_locally_and_in_ci(self) -> None:
        for in_ci in (False, True):
            with self.subTest(in_ci=in_ci):
                check = rp.c10_suites_green(self.doc, in_ci=in_ci)
                self.assertEqual(check["failed"], [])
                self.assertTrue(check["ok"])
        self.assertEqual(rp.c10_suites_green(self.doc, in_ci=True)["assertions"],
                         rp.c10_suites_green(self.doc, in_ci=False)["assertions"] + 3)

    def mutations(self) -> list[tuple[str, str, Any]]:
        out = [("evidence_is_junit", "run.suites_evidence", None),
               ("code_clean_at_start", "run.code_dirty_start", True),
               ("code_clean_at_end", "run.code_dirty_end", True)]
        for i, suite in enumerate(su.SUITES):
            out += [(f"{suite.slug}:present", f"run.suites.{i}.suite", "another"),
                    (f"{suite.slug}:sha_is_the_commit", f"run.suites.{i}.sha", "c" * 40),
                    (f"{suite.slug}:no_failures", f"run.suites.{i}.failures", 1),
                    (f"{suite.slug}:no_errors", f"run.suites.{i}.errors", 1),
                    (f"{suite.slug}:tests_at_or_above_floor", f"run.suites.{i}.tests", suite.floor - 1),
                    (f"{suite.slug}:junit_bound", f"run.suites.{i}.junit_bound", False)]
            if suite.zero_skip_scope is not None:
                out.append((f"{suite.slug}:no_skips_in_scope", f"run.suites.{i}.skipped_in_scope", 1))
        return out

    def test_every_assertion_catches_its_own_mutation(self) -> None:
        for assertion, path, value in self.mutations():
            with self.subTest(assertion=assertion):
                check = rp.c10_suites_green(put(self.doc, path, value), in_ci=False)
                self.assertFalse(check["ok"])
                self.assertIn(assertion, check["failed"])

    def test_every_assertion_has_a_mutation(self) -> None:
        self.assertEqual(len({a for a, _, _ in self.mutations()}),
                         rp.c10_suites_green(self.doc, in_ci=False)["assertions"])

    def test_the_ci_only_assertions(self) -> None:
        for assertion, path, value in (("needs_suites-core_success", "run.needs_results.suites-core", "failure"),
                                       ("needs_suites-lab_success", "run.needs_results.suites-lab", None),
                                       ("github_run_id_set", "run.github_run_id", None)):
            with self.subTest(assertion=assertion):
                check = rp.c10_suites_green(put(self.doc, path, value), in_ci=True)
                self.assertIn(assertion, check["failed"])
                self.assertNotIn(assertion, rp.c10_suites_green(put(self.doc, path, value), in_ci=False)["failed"])

    def test_an_unknown_commit_never_matches_a_row(self) -> None:
        doc = put(self.doc, "run.code_commit", "unknown")
        for row in doc["run"]["suites"]:
            row["sha"] = "unknown"
        self.assertIn("tests-lab:sha_is_the_commit", rp.c10_suites_green(doc, in_ci=False)["failed"])

    def test_suites_green_needs_the_evidence_and_the_check(self) -> None:
        doc = dict(self.doc)
        doc["checks"] = {"c10_suites_green": rp.c10_suites_green(doc, in_ci=False)}
        self.assertTrue(rp.suites_green(doc))
        self.assertFalse(rp.suites_green(put(doc, "run.suites_evidence", None)))
        self.assertFalse(rp.suites_green(put(doc, "checks.c10_suites_green.ok", False)))
        self.assertFalse(rp.suites_green({"run": {"suites_evidence": su.EVIDENCE}}))


if __name__ == "__main__":
    unittest.main()

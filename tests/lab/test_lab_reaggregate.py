"""Re-aggregation (``lab.reaggregate``): the request file, which request a push or a dispatch runs, and how a run's
downloaded artifacts are laid out for ``lab.aggregate``. The aggregate's stamp and its summary line are tested with
the aggregate (``test_lab_aggregate.ReaggregationTests``); the workflow with the workflow (``test_lab_workflow``).
Nothing here runs a unit.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from lab import reaggregate
from lab.notes import (BRANCH_DELETED, DEFAULT_BRANCH, DELETE_ONLY, NOT_A_BRANCH, REAGGREGATE_BAD_NAME,
                       REAGGREGATE_NO_CHANGE)
from tests.lab.helpers import GitWorld, call_main, write_json

GOOD = {"run_id": 37910989965, "purpose": "Re-read a finished run with the per-model scores."}


def _text(obj: Any) -> str:
    return json.dumps(obj, indent=2) + "\n"


class RequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-reagg-request-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def load(self, obj: Any) -> dict[str, Any]:
        path = self.tmp / "r.json"
        path.write_text(obj if isinstance(obj, str) else _text(obj), encoding="utf-8")
        return reaggregate.load_request(path)

    def refused(self, obj: Any) -> tuple[str, str]:
        with self.assertRaises(reaggregate.ReaggregateError) as caught:
            self.load(obj)
        return caught.exception.path, caught.exception.problem

    def test_a_good_request(self) -> None:
        got = self.load(GOOD)
        self.assertEqual((got["run_id"], got["purpose"]), (GOOD["run_id"], GOOD["purpose"]))
        self.assertEqual(len(got["sha256"]), 64)
        self.assertTrue(got["path"].endswith("/r.json"), got["path"])

    def test_refusals(self) -> None:
        self.refused("{")
        self.assertEqual(self.refused([GOOD])[0], "$")
        self.assertEqual(self.refused({"run_id": 1})[0], "$")
        self.assertEqual(self.refused({**GOOD, "extra": 1})[0], "$")
        for run_id in (0, -1, True, "1", 1.5, 10 ** 20):
            with self.subTest(run_id=run_id):
                self.assertEqual(self.refused({**GOOD, "run_id": run_id})[0], "$.run_id")
        for purpose in ("", "   ", "two\nlines", "x" * (reaggregate.MAX_PURPOSE + 1), 3):
            with self.subTest(purpose=str(purpose)[:10]):
                self.assertEqual(self.refused({**GOOD, "purpose": purpose})[0], "$.purpose")
        (self.tmp / "dir.json").mkdir()
        with self.assertRaises(reaggregate.ReaggregateError):
            reaggregate.load_request(self.tmp / "dir.json")
        (self.tmp / "link.json").symlink_to(self.tmp / "r.json")
        with self.assertRaises(reaggregate.ReaggregateError):
            reaggregate.load_request(self.tmp / "link.json")


class FindTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-reagg-find-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.world = GitWorld(self.tmp)
        self.world.git("checkout", "-q", "-b", "lab")

    def push(self, before: str, **kwargs: Any) -> reaggregate.Found:
        w = self.world
        after = w.commit(**kwargs)
        w.push("lab")
        return reaggregate.find(w.push_event(before=before, after=after, branch="lab"), "push", w.clone(after))

    def test_one_request(self) -> None:
        found = self.push(self.world.base, files={"lab/reaggregate/r-002.json": _text(GOOD)})
        self.assertEqual((found.outcome, found.request["path"], found.request["run_id"]),
                         ("run", "lab/reaggregate/r-002.json", GOOD["run_id"]))

    def test_the_newest_changed_file_runs(self) -> None:
        w = self.world
        w.commit({"lab/reaggregate/b-old.json": _text({**GOOD, "run_id": 1})}, message="older")
        found = self.push(w.base, files={"lab/reaggregate/a-new.json": _text({**GOOD, "run_id": 2})},
                          message="newer")
        self.assertEqual((found.outcome, found.request["path"], found.request["run_id"]),
                         ("run", "lab/reaggregate/a-new.json", 2))

    def test_deletions_only_run_nothing(self) -> None:
        w = self.world
        before = w.commit({"lab/reaggregate/r.json": _text(GOOD)})
        w.push("lab")
        found = self.push(before, delete=("lab/reaggregate/r.json",))
        self.assertEqual((found.outcome, found.notices, found.request), ("nothing", [DELETE_ONLY], None))

    def test_no_request_change_and_a_bad_name_are_errors(self) -> None:
        w = self.world
        found = self.push(w.base, files={"lab/reaggregate/README.md": "notes\n"})
        self.assertEqual((found.outcome, found.error), ("error", f"event: {REAGGREGATE_NO_CHANGE}"))
        found = self.push(w.head(), files={"lab/reaggregate/Bad_Name.json": _text(GOOD)})
        self.assertEqual((found.outcome, found.error), ("error", f"event: {REAGGREGATE_BAD_NAME}"))
        found = self.push(w.head(), files={"lab/reaggregate/bad-body.json": _text({"run_id": 1})})
        self.assertEqual(found.outcome, "error")
        self.assertTrue(found.error.startswith("$: "), found.error)

    def test_pushes_that_run_nothing(self) -> None:
        w = self.world
        after = w.commit({"lab/reaggregate/r.json": _text(GOOD)})
        w.push("lab")
        clone = w.clone(after)
        for event, notice in ((w.push_event(before=w.base, after=after, branch="lab", deleted=True), BRANCH_DELETED),
                              (w.push_event(before=w.base, after=after, branch="lab", ref="refs/tags/t"),
                               NOT_A_BRANCH),
                              (w.push_event(before=w.base, after=after, branch="main"), DEFAULT_BRANCH)):
            with self.subTest(notice=notice):
                found = reaggregate.find(event, "push", clone)
                self.assertEqual((found.outcome, found.notices), ("nothing", [notice]))

    def test_dispatch(self) -> None:
        w = self.world
        w.commit({"lab/reaggregate/r.json": _text(GOOD)})
        clone = w.clone(w.push("lab"))
        found = reaggregate.find(w.event(inputs={"request": "lab/reaggregate/r.json"}), "workflow_dispatch", clone)
        self.assertEqual((found.outcome, found.request["path"]), ("run", "lab/reaggregate/r.json"))
        for request in ("lab/reaggregate/missing.json", "lab/requests/r.json", "../r.json"):
            with self.subTest(request=request):
                found = reaggregate.find(w.event(inputs={"request": request}), "workflow_dispatch", clone)
                self.assertEqual(found.outcome, "error")

    def test_cli_outputs(self) -> None:
        w = self.world
        after = w.commit({"lab/reaggregate/r.json": _text(GOOD)})
        clone = w.clone(w.push("lab"))
        event = w.push_event(before=w.base, after=after, branch="lab")
        output = self.tmp / "github-output"
        cwd = Path.cwd()
        os.chdir(clone)
        try:
            code, stdout, stderr = call_main(reaggregate, ["find", "--event-path", str(event), "--event-name", "push",
                                                           "--github-output", str(output)])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, f"reaggregate: lab/reaggregate/r.json run {GOOD['run_id']}\n")
        self.assertEqual(output.read_text(encoding="utf-8"),
                         f"request=lab/reaggregate/r.json\nrun_id={GOOD['run_id']}\n")


class SortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-reagg-sort-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.arts = self.tmp / "arts"
        for name in ("lab-plan-7-1", "lab-plan-7-2", "lab-prov-7-1-server", "lab-prov-7-2-gguf-x",
                     "lab-run-7-1-s001-a", "lab-run-7-2-s001-a", "lab-run-7-1-s002-b", "lab-report-real-7-1",
                     "lab-run-8-1-s001-a", "lab-plan-8-1"):
            (self.arts / name).mkdir(parents=True)
            (self.arts / name / "marker.txt").write_text(name, encoding="utf-8")
        write_json(self.arts / "lab-plan-7-2" / "plan.json", {"retention_days": 30})

    def sort(self, out: Path, run_id: str = "7") -> tuple[int, str, str]:
        return call_main(reaggregate, ["sort", "--artifacts", str(self.arts), "--run-id", run_id, "--out", str(out),
                                       "--github-output", str(self.tmp / "out.txt")])

    def test_layout(self) -> None:
        out = self.tmp / "in"
        code, stdout, stderr = self.sort(out)
        self.assertEqual(code, 0, stderr)
        self.assertEqual((out / "plan" / "marker.txt").read_text(encoding="utf-8"), "lab-plan-7-2")
        self.assertEqual(sorted(p.name for p in (out / "provision").iterdir()),
                         ["lab-prov-7-1-server", "lab-prov-7-2-gguf-x"])
        self.assertEqual(sorted(p.name for p in (out / "shards").iterdir()),
                         ["lab-run-7-1-s001-a", "lab-run-7-1-s002-b", "lab-run-7-2-s001-a"])
        self.assertEqual(sorted(p.name for p in self.arts.iterdir()),
                         ["lab-plan-7-1", "lab-plan-8-1", "lab-report-real-7-1", "lab-run-8-1-s001-a"])
        self.assertEqual(stdout.splitlines()[-1], "lab: reaggregate sort plan lab-plan-7-2 provision 2 shards 3 "
                                                  "ignored 4")
        self.assertEqual((self.tmp / "out.txt").read_text(encoding="utf-8"), "retention_days=30\n")

    def test_refusals(self) -> None:
        out = self.tmp / "busy"
        out.mkdir()
        (out / "x").write_text("x", encoding="utf-8")
        code, _, stderr = self.sort(out)
        self.assertEqual(code, 2)
        self.assertIn("must be absent or an empty directory", stderr)
        code, _, stderr = self.sort(self.tmp / "in", run_id="9")
        self.assertEqual(code, 2)
        self.assertIn("no plan artifact of run 9", stderr)
        self.assertEqual(len(list(self.arts.iterdir())), 10)


if __name__ == "__main__":
    unittest.main()

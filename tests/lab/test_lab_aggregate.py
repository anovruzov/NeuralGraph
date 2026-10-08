"""The aggregate report: which shard artifact counts (newest attempt, ambiguous, other plan, altered, unsealed, no
artifact), what each unit gave, the E3, G0 and latency rows read from the shards' own files, the provision rows and
the lock candidate. Trees are copies of one dry run (E3, G0 and the simulation), mutated and re-sealed as the
workflow would have sealed them; lock tests provision against the loopback stubs. Nothing here measures a model.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from lab import aggregate as lab_aggregate
from lab import provision
from lab.notes import (AMBIGUOUS_ARTIFACTS, ALTERED, FILES_DIFFER, LOCK_CONFLICT, NO_ARTIFACT, OTHER_PLAN,
                       PLUMBING_BANNER, STEP_FAILED, UNSEALED)
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.stats import percentile
from tests.lab.helpers import (MANIFEST_TEST, ROOT, DryTree, StubWorld, call_main, canonical_json, dry_run,
                               kill_mentioning, plumbing_min, sim_block, write_json)

SHARDS = ("s001-fake-a", "s002-fake-b")
UNITS = ("e3-fake-a", "e3-fake-b", "g0-fake-a", "g0-fake-b", "sim-fake-a-s1")
SIM_UNIT = "sim-fake-a-s1"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(obj: Any) -> list[str]:
    if isinstance(obj, dict):
        return [k for key, value in obj.items() for k in (key, *_keys(value))]
    if isinstance(obj, list):
        return [k for value in obj for k in _keys(value)]
    return []


def _aggregate(out: Path, *, plan: Path, shards: Path, provision_dir: Path,
               manifest: Path = MANIFEST_TEST) -> tuple[int, str, str, Any]:
    code, stdout, stderr = call_main(lab_aggregate, ["--plan", str(plan), "--provision", str(provision_dir),
                                                     "--shards", str(shards), "--manifest", str(manifest),
                                                     "--out", str(out)])
    report = _json(out / "report.json") if (out / "report.json").exists() else None
    return code, stdout, stderr, report


class AggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-aggregate-"))
        request = plumbing_min()
        request["experiments"]["g0"]["models"] = ["fake-a", "fake-b"]
        request["experiments"]["sim"] = sim_block(models=["fake-a"])
        cls.request = write_json(cls.tmp / "both-g0.json", request)
        cls.out = cls.tmp / "D"
        cls.done = dry_run(cls.out, cls.request, MANIFEST_TEST)

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def setUp(self) -> None:
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        self.work = Path(tempfile.mkdtemp(prefix="t-", dir=self.tmp))
        self.tree = DryTree(self.out, self.work / "T")
        self.runs = 0

    def aggregate(self, **kwargs: Any) -> dict[str, Any]:
        self.runs += 1
        code, stdout, stderr, report = self.tree.aggregate(self.work / f"R{self.runs}", **kwargs)
        self.assertEqual(code, 0, stderr)
        self.assertRegex(stdout, r"\Alab: aggregate units [0-9]+ shards [0-9]+ class (plumbing|real) measurements "
                                 r"(true|false) lock [a-z_]+\n\Z")
        return report

    @staticmethod
    def shard(report: dict[str, Any], shard_id: str) -> dict[str, Any]:
        return next(s for s in report["shards"] if s["shard"] == shard_id)

    @staticmethod
    def unit(report: dict[str, Any], unit_id: str) -> dict[str, Any]:
        return next(u for u in report["units"] if u["unit"] == unit_id)

    def artifacts(self) -> Path:
        """The shards as download-artifact lays out several artifacts of run 100, attempt 1."""
        arts = self.work / "arts"
        for shard in SHARDS:
            shutil.copytree(self.tree.path(shard), arts / f"lab-run-100-1-{shard}")
        return arts

    # ------------------------------------------------------------------------------------------- shard choice

    def test_newest_attempt_and_ambiguous(self) -> None:
        arts = self.artifacts()
        retry = arts / "lab-run-100-2-s001-fake-a"
        shutil.copytree(self.tree.path("s001-fake-a"), retry)
        record = _json(retry / "units" / "e3-fake-a" / "unit.json")
        record["wall_s"] = 99.5
        (retry / "units" / "e3-fake-a" / "unit.json").write_bytes(canonical_json(record))
        self.tree.reseal("s001-fake-a", attempt=2, path=retry)
        report = self.aggregate(shards=arts)
        shard = self.shard(report, "s001-fake-a")
        self.assertEqual((shard["state"], shard["run_attempt"], shard["artifact"]),
                         ("sealed", 2, "lab-run-100-2-s001-fake-a"))
        self.assertEqual(self.unit(report, "e3-fake-a")["wall_s"], 99.5)
        self.assertEqual(self.shard(report, "s002-fake-b")["artifact"], "lab-run-100-1-s002-fake-b")

        shutil.copytree(retry, arts / "lab-run-101-2-s001-fake-a")
        report = self.aggregate(shards=arts)
        shard = self.shard(report, "s001-fake-a")
        self.assertEqual((shard["state"], shard["state_reason"], shard["artifact"]),
                         ("ambiguous", AMBIGUOUS_ARTIFACTS, None))
        for uid in ("e3-fake-a", "g0-fake-a", SIM_UNIT):
            self.assertEqual((self.unit(report, uid)["status"], self.unit(report, uid)["status_reason"],
                              self.unit(report, uid)["display_class"]), ("excluded", AMBIGUOUS_ARTIFACTS, "no-result"))
        self.assertEqual((report["sim"], report["sim_sizing"]), ([], []))
        self.assertEqual(self.unit(report, "e3-fake-b")["status"], "ok")

    def test_single_artifact_layout_and_missing_dirs(self) -> None:
        report = self.aggregate(shards=self.tree.path("s002-fake-b"))
        self.assertEqual([s["state"] for s in report["shards"]], ["no_artifact", "sealed"])
        self.assertEqual({u["unit"]: u["status"] for u in report["units"]},
                         {"e3-fake-a": "not_run", "e3-fake-b": "ok", "g0-fake-a": "not_run", "g0-fake-b": "ok",
                          SIM_UNIT: "not_run"})
        self.assertIsNone(self.shard(report, "s002-fake-b")["artifact"])
        report = self.aggregate(shards=self.work / "absent", provision=self.work / "absent-too")
        self.assertEqual([s["state"] for s in report["shards"]], ["no_artifact", "no_artifact"])
        self.assertEqual({(u["status"], u["status_reason"]) for u in report["units"]}, {("not_run", NO_ARTIFACT)})
        self.assertEqual((report["e3"], report["g0"], report["sim"], report["sim_sizing"], report["latency"],
                          report["provision"]), ([], [], [], [], [], []))
        self.assertEqual(report["notes"]["cpu_models"], [])

    def test_missing_shard_and_prepare_failure_reasons(self) -> None:
        shutil.rmtree(self.tree.path("s002-fake-b"))
        failed = self.tree.path("s001-fake-a")
        shutil.rmtree(failed)
        problem = "provisioning failed for model fake-a"
        write_json(failed / "provision" / "prepare-error.json",
                   {"schema_version": 1, "kind": "lab_prepare_error", "problem": problem, "exit_code": 2})
        self.tree.reseal("s001-fake-a", steps={"prepare": "failure", "run": "skipped"})
        status = _json(failed / "status.json")
        self.assertEqual(status["prepare"], {"present": True, "needed": None, "prepared": False, "problem": problem})
        self.assertEqual(status["missing_units"], ["g0-fake-a", SIM_UNIT, "e3-fake-a"])
        report = self.aggregate()
        shard = self.shard(report, "s001-fake-a")
        self.assertEqual((shard["state"], shard["prepare_problem"], shard["failed_step"]),
                         ("sealed", problem, "prepare"))
        for uid in ("e3-fake-a", "g0-fake-a", SIM_UNIT):
            self.assertEqual((self.unit(report, uid)["status"], self.unit(report, uid)["status_reason"]),
                             ("not_run", problem))
        self.assertEqual(report["sim_sizing"], [])
        for uid in ("e3-fake-b", "g0-fake-b"):
            self.assertEqual((self.unit(report, uid)["status"], self.unit(report, uid)["status_reason"]),
                             ("not_run", NO_ARTIFACT))
        self.assertEqual(self.shard(report, "s002-fake-b")["state"], "no_artifact")

        shutil.rmtree(failed)
        self.tree.reseal("s001-fake-a", steps={"get-plan": "failure", "prepare": "skipped"})
        report = self.aggregate()
        self.assertEqual(self.unit(report, "e3-fake-a")["status_reason"], f"{STEP_FAILED} get-plan")

    def test_plan_sha_mismatch_excluded(self) -> None:
        other = self.work / "other-plan.json"
        plan = self.tree.plan
        plan["request"]["purpose"] = "Another plan."
        other.write_bytes(canonical_json(plan))
        self.tree.plan_path, kept = other, self.tree.plan_path
        self.tree.reseal("s001-fake-a")
        self.tree.plan_path = kept
        self.assertNotEqual(_json(self.tree.path("s001-fake-a", "status.json"))["plan_sha256"],
                            self.tree.plan_sha256())
        report = self.aggregate()
        self.assertEqual((self.shard(report, "s001-fake-a")["state"], self.shard(report, "s001-fake-a")
                          ["state_reason"]), ("other_plan", OTHER_PLAN))
        self.assertEqual((self.unit(report, "e3-fake-a")["status"], self.unit(report, "e3-fake-a")["status_reason"]),
                         ("excluded", OTHER_PLAN))
        self.assertEqual(self.shard(report, "s002-fake-b")["state"], "sealed")

        self.tree.reseal("s001-fake-a")
        self.tree.edit("s002-fake-b", "provenance.json", lambda p: p["plan"].update(sha256="0" * 64))
        self.tree.reseal("s002-fake-b")
        report = self.aggregate()
        self.assertEqual([s["state"] for s in report["shards"]], ["sealed", "other_plan"])

        self.tree.edit("s002-fake-b", "provenance.json", lambda p: p["plan"].update(sha256=self.tree.plan_sha256()))
        self.tree.reseal("s002-fake-b", plan=False)
        self.assertIsNone(_json(self.tree.path("s002-fake-b", "status.json"))["plan_sha256"])
        self.assertEqual([s["state"] for s in self.aggregate()["shards"]], ["sealed", "sealed"])

    def test_altered_artifact_and_files_differ(self) -> None:
        base = self.tree.path("s001-fake-a")
        cases = {
            "changed": lambda: (base / "units" / "e3-fake-a" / "stdout.log").write_text("changed"),
            "missing": lambda: (base / "routing" / "e3-fake-a.json").unlink(),
            "extra": lambda: (base / "extra.txt").write_text("x"),
            "hidden": lambda: (base / ".provenance.json.123.tmp").write_text("{}"),
            "symlink": lambda: (base / "link").symlink_to(base / "provenance.json"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.tree.reseal("s001-fake-a")
                mutate()
                report = self.aggregate()
                shard = self.shard(report, "s001-fake-a")
                self.assertEqual((shard["state"], shard["state_reason"]), ("altered", ALTERED))
                self.assertEqual({self.unit(report, u)["status"] for u in ("e3-fake-a", "g0-fake-a", SIM_UNIT)},
                                 {"excluded"})
                for path in (base / "extra.txt", base / ".provenance.json.123.tmp", base / "link"):
                    if path.is_symlink() or path.exists():
                        path.unlink()
        (base / "units" / ".unit.json.4242.tmp").write_text("{")
        self.tree.reseal("s001-fake-a")
        self.assertFalse((base / "units" / ".unit.json.4242.tmp").exists())
        self.assertEqual(self.shard(self.aggregate(), "s001-fake-a")["state"], "sealed")
        write_json(base / "summary" / "summary.md", {"rendered": "after the seal"})
        self.assertEqual(self.shard(self.aggregate(), "s001-fake-a")["state"], "sealed")

        record = _json(base / "units" / "e3-fake-a" / "unit.json")
        e3 = next(rel for rel in record["files"] if rel.endswith("/e3.json"))
        (base / e3).write_bytes((base / e3).read_bytes().replace(b'"measured":2', b'"measured":3'))
        self.tree.reseal("s001-fake-a")
        report = self.aggregate()
        self.assertEqual(self.shard(report, "s001-fake-a")["state"], "sealed")
        unit = self.unit(report, "e3-fake-a")
        self.assertEqual((unit["status"], unit["status_reason"], unit["display_class"]),
                         ("excluded", FILES_DIFFER, "no-result"))
        self.assertNotIn("e3-fake-a", {row["unit"] for row in report["e3"]})
        self.assertEqual(self.unit(report, "g0-fake-a")["status"], "ok")

    def test_unsealed_shard(self) -> None:
        (self.tree.path("s001-fake-a") / "status.json").unlink()
        report = self.aggregate()
        self.assertEqual((self.shard(report, "s001-fake-a")["state"], self.shard(report, "s001-fake-a")
                          ["state_reason"]), ("unsealed", UNSEALED))
        self.assertEqual(self.unit(report, "e3-fake-a")["status"], "excluded")

    # ------------------------------------------------------------------------------------------- values

    def test_report_values_equal_unit_files(self) -> None:
        report = self.aggregate()
        self.assertEqual([u["unit"] for u in report["units"]], sorted(UNITS))
        cells = 0
        for unit in report["units"]:
            root = self.tree.path(unit["shard"])
            record = _json(root / "units" / unit["unit"] / "unit.json")
            provenance = _json(root / "provenance.json")
            for key in lab_aggregate.UNIT_FIELDS:
                self.assertEqual(unit[key], record[key], (unit["unit"], key))
            self.assertEqual(unit["cpu_model"], provenance["host"]["cpu"]["model_name"])
            if unit["experiment"] == "e3":
                result = _json(root / "runs" / "e3" / unit["run_id"] / "e3.json")
                rows = [r for r in report["e3"] if r["unit"] == unit["unit"]]
                self.assertEqual(len(rows), len(result["cells"]))
                for row, cell in zip(rows, result["cells"]):
                    for key in lab_aggregate.E3_FIELDS:
                        self.assertEqual(row[key], cell[key], key)
                    self.assertEqual((row["measurement"], row["display_class"], row["model"]),
                                     (False, "plumbing", unit["model"]))
                    cells += 1
            elif unit["experiment"] == "sim":
                scorecard = _json(root / "runs" / "sim" / unit["run_id"] / "scorecard.json")
                (row,) = [r for r in report["sim"] if r["unit"] == unit["unit"]]
                for name, block in scorecard["channels"].items():
                    for key in lab_aggregate.SIM_CHANNEL_FIELDS:
                        self.assertEqual(row["channels"][name][key], block[key], (name, key))
                for name, block in scorecard["lifts"].items():
                    for key in lab_aggregate.SIM_LIFT_FIELDS:
                        self.assertEqual(row["lifts"][name][key], block[key], (name, key))
                pushdown = scorecard["pushdown"]
                self.assertEqual(row["pushdown"], {"n": pushdown["n"], "n_true": pushdown["n_true"],
                                                   "supported": pushdown["statuses"]["supported"],
                                                   "ap_pushdown": pushdown["ap_pushdown"],
                                                   "ap_stats_only": pushdown["ap_stats_only"]})
                world = scorecard["world"]
                self.assertEqual((row["plant"], row["seed"], row["weeks"], row["records"], row["world_digest"]),
                                 (world["plant"], world["seed"], world["weeks"], world["records"]["total"],
                                  world["world_digest"]))
                self.assertEqual((row["raw_text_crossed"], row["fallback_share"], row["notes"], row["measurement"],
                                  row["display_class"]),
                                 (scorecard["raw_text_crossed"], scorecard["extraction"]["fallback_share"],
                                  scorecard["notes"], False, "plumbing"))
                self.assertNotIn("items", json.dumps(row))
            else:
                leakage = _json(root / "runs" / "g0" / unit["run_id"] / "leakage.json")
                (row,) = [r for r in report["g0"] if r["unit"] == unit["unit"]]
                for key in lab_aggregate.G0_FIELDS:
                    self.assertEqual(row[key], leakage[key], key)
                self.assertEqual(row["positive_control"], {k: leakage["positive_control"][k]
                                                           for k in ("canary_hits", "shingle_overlap_bytes")})
                self.assertNotIn("hits", row)
                self.assertNotIn("shingle_hits", row)
        self.assertEqual(cells, 8)
        self.assertEqual(report["shard_count"], 2)
        self.assertEqual(report["unit_count"], 5)
        self.assertEqual(len(report["sim"]), 1)
        self.assertEqual(report["plan"]["sha256"], self.tree.plan_sha256())

    def test_latency_percentiles_from_ledgers(self) -> None:
        report = self.aggregate()
        expected: dict[tuple[Any, ...], list[float]] = {}
        for unit in report["units"]:
            run = self.tree.path(unit["shard"]) / "runs" / unit["experiment"] / unit["run_id"]
            for ledger in sorted(run.rglob("*ledger.jsonl")):
                for row in read_ledger(ledger):
                    if row["ok"] is True and isinstance(row["latency_ms"], (int, float)):
                        key = ("plumbing", unit["model"], unit["cpu_model"], unit["experiment"], row["task"])
                        expected.setdefault(key, []).append(row["latency_ms"])
        self.assertEqual(len(report["latency"]), len(expected))
        self.assertGreater(len(expected), 4)
        for row in report["latency"]:
            values = expected[(row["display_class"], row["model"], row["cpu_model"], row["experiment"], row["task"])]
            self.assertEqual((row["n"], row["p50_ms"], row["p95_ms"]),
                             (len(values), round(percentile(values, 50), 3), round(percentile(values, 95), 3)))

    def test_no_pooling_across_cpu_models(self) -> None:
        def same_model(plan: dict[str, Any]) -> None:
            next(u for u in plan["units"] if u["unit"] == "e3-fake-b")["model"] = "fake-a"

        self.tree.set_plan(same_model)
        e3_rows = [r for r in self.aggregate()["latency"] if r["experiment"] == "e3"]
        self.assertEqual({(r["model"], r["n"]) for r in e3_rows}, {("fake-a", 12)})
        self.tree.edit("s002-fake-b", "provenance.json", lambda p: p["host"]["cpu"].update(model_name="Other CPU"))
        self.tree.reseal("s002-fake-b")
        report = self.aggregate()
        e3_rows = [r for r in report["latency"] if r["experiment"] == "e3"]
        self.assertEqual(len(e3_rows), 4)
        self.assertEqual({r["n"] for r in e3_rows}, {6})
        self.assertEqual(len({r["cpu_model"] for r in e3_rows}), 2)
        self.assertEqual(len(report["notes"]["cpu_models"]), 2)
        self.assertIn("Other CPU", report["notes"]["cpu_models"])

    def test_world_digest_mismatch(self) -> None:
        groups = self.aggregate()["notes"]["world_digest"]
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["consistent"], groups[0]["units"], len(groups[0]["digests"])),
                         (True, ["g0-fake-a", "g0-fake-b"], 1))
        record = _json(self.tree.path("s002-fake-b", "units/g0-fake-b/unit.json"))
        rel = next(r for r in record["files"] if r.endswith("leakage.json"))
        leakage = _json(self.tree.path("s002-fake-b", rel))
        leakage["world_digest"] = "f" * 64
        self.tree.replace_unit_file("s002-fake-b", "g0-fake-b", rel, canonical_json(leakage))
        self.tree.reseal("s002-fake-b")
        (group,) = self.aggregate()["notes"]["world_digest"]
        self.assertFalse(group["consistent"])
        self.assertEqual(group["units"], ["g0-fake-a", "g0-fake-b"])
        self.assertEqual(len(group["digests"]), 2)
        self.assertIn("f" * 64, group["digests"])
        self.assertEqual((group["pack"], group["seed"], group["records"]), ("device_quality", 7, 50))

    def test_sim_sizing_rows(self) -> None:
        report = self.aggregate()
        record = _json(self.tree.path("s001-fake-a", f"units/{SIM_UNIT}/unit.json"))
        run = f"runs/sim/{record['run_id']}"
        scorecard = _json(self.tree.path("s001-fake-a", f"{run}/scorecard.json"))
        (sizing,) = report["sim_sizing"]
        projection = scorecard["projection"]
        self.assertEqual(sizing, {
            "unit": SIM_UNIT, "model": "fake-a", "cpu_model": self.unit(report, SIM_UNIT)["cpu_model"],
            "status": "ok", "display_class": "plumbing", "source": "scorecard", "records_done": 1031,
            "records_total": 1031, "extract_record_s_p50": projection["measured"]["extract_record_s_p50"],
            "judge_s_p50": projection["measured"]["judge_s_p50"], "estimate_s": projection["estimate_s"],
            "suggested_minutes": projection["suggested_minutes"]})
        (group,) = report["notes"]["sim_world_digest"]
        self.assertEqual(group, {"plant": "sim_small", "seed": 1, "weeks": 34,
                                 "digests": [scorecard["world"]["world_digest"]], "units": [SIM_UNIT],
                                 "consistent": True})

        # a timed-out unit has no result row, but its sizing row reads the progress file it left
        self.tree.edit("s001-fake-a", f"units/{SIM_UNIT}/unit.json",
                       lambda r: r.update(status="timed_out", status_reason="timed out"))
        self.tree.replace_unit_file("s001-fake-a", SIM_UNIT, f"{run}/scorecard.json", canonical_json({"kind": "x"}))
        self.tree.reseal("s001-fake-a")
        report = self.aggregate()
        self.assertEqual(self.unit(report, SIM_UNIT)["display_class"], "no-result")
        self.assertEqual((report["sim"], report["notes"]["sim_world_digest"]), ([], []))
        (sizing,) = report["sim_sizing"]
        progress = _json(self.tree.path("s001-fake-a", f"{run}/progress.json"))
        self.assertEqual(sizing, {
            "unit": SIM_UNIT, "model": "fake-a", "cpu_model": self.unit(report, SIM_UNIT)["cpu_model"],
            "status": "timed_out", "display_class": "no-result", "source": "progress",
            "records_done": progress["records"]["done"], "records_total": progress["records"]["total"],
            "extract_record_s_p50": progress["latency_s"]["extract_record"]["p50"],
            "judge_s_p50": progress["latency_s"]["judge_record"]["p50"], "estimate_s": progress["estimate_s"],
            "suggested_minutes": progress["suggested_minutes"]})

        # nothing that reads as either file: no sizing row
        self.tree.replace_unit_file("s001-fake-a", SIM_UNIT, f"{run}/progress.json", b"{")
        self.tree.reseal("s001-fake-a")
        self.assertEqual(self.aggregate()["sim_sizing"], [])

    def test_deterministic_report_bytes(self) -> None:
        self.aggregate()
        self.aggregate()
        for name in ("report.json", "lock-candidate.json", "plan.json"):
            self.assertEqual((self.work / "R1" / name).read_bytes(), (self.work / "R2" / name).read_bytes(), name)

    def test_cli_refusals(self) -> None:
        busy = self.work / "busy"
        busy.mkdir()
        (busy / "x").write_text("x")
        code, _, stderr, _ = self.tree.aggregate(busy)
        self.assertEqual(code, 2, stderr)
        for content in (b"not json", b'{"kind": "lab_report"}', canonical_json({**self.tree.plan, "units": [
                {"unit": "../x", "run_id": "x", "shard": "s001-fake-a", "experiment": "e3"}]})):
            bad = self.work / "bad-plan.json"
            bad.write_bytes(content)
            code, _, _, _ = self.tree.aggregate(self.work / "bad-out", plan=bad)
            self.assertEqual(code, 2)
            self.assertFalse((self.work / "bad-out").exists())
        inside = ROOT / "mycelic" / "lab-aggregate-never-created"
        code, _, _, _ = self.tree.aggregate(inside)
        self.assertEqual(code, 2)
        self.assertFalse(inside.exists())

    def test_plumbing_dry_run_report(self) -> None:
        self.assertEqual(self.done.stdout.splitlines()[-1],
                         "lab: aggregate units 5 shards 2 class plumbing measurements false lock unchanged")
        report_dir = self.out / "report"
        report = _json(report_dir / "report.json")
        self.assertEqual((report["kind"], report["result_class"], report["contains_measurements"], report["banner"]),
                         ("lab_report", "plumbing", False, PLUMBING_BANNER))
        self.assertEqual((report_dir / "plan.json").read_bytes(), (self.out / "plan" / "plan.json").read_bytes())
        self.assertEqual(report["lock"], {"status": "unchanged", "new_entries": 0, "problem": None})
        self.assertEqual(_json(report_dir / "lock-candidate.json"),
                         _json(MANIFEST_TEST.with_name("manifest-test.lock.json")))
        self.assertEqual({s["state"] for s in report["shards"]}, {"sealed"})
        self.assertEqual({u["display_class"] for u in report["units"]}, {"plumbing"})
        self.assertEqual(report["ignored_artifacts"], [])
        self.assertEqual((report["e1"], report["e2"], report["e2_sizing"], report["x1"], report["openfda"]),
                         (None, [], [], [], []))
        self.assertEqual([(g["unit"], g["records"], g["protocol_records"], g["below_protocol"]) for g in report["g0"]],
                         [("g0-fake-a", 50, 1000, True), ("g0-fake-b", 50, 1000, True)])
        self.assertFalse((report_dir / "e1").exists())
        # no clock value in the report; precision_at_40 (precision in the top forty) is the one non-time match
        keys = [k for k in _keys(report) if re.search(r"(^|_)(at|time|date|epoch|ts)(_|$)", k)]
        self.assertEqual(sorted(set(keys)), ["precision_at_40"])


# --------------------------------------------------------------------------------------------------- provision and lock

class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-aggregate-lock-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))

    def world(self, **kwargs: Any) -> StubWorld:
        world = StubWorld(self.tmp / f"w{len(list(self.tmp.iterdir()))}", **kwargs)
        self.addCleanup(world.close)
        return world

    def aggregate(self, world: StubWorld, name: str) -> tuple[dict[str, Any], Path]:
        out = self.tmp / name
        code, _, stderr, report = _aggregate(out, plan=world.plan_path, shards=self.tmp / "no-shards",
                                             provision_dir=world.records, manifest=world.manifest_path)
        self.assertEqual(code, 0, stderr)
        return report, out

    def merge_lock(self, world: StubWorld, name: str) -> bytes:
        out = self.tmp / name
        code, _, stderr = call_main(provision, ["merge-lock", "--manifest", str(world.manifest_path),
                                                "--candidates", str(world.records), "--out", str(out)])
        self.assertEqual(code, 0, stderr)
        return out.read_bytes()

    def test_lock_new_entry_matches_merge_lock(self) -> None:
        world = self.world()
        code, _, stderr, _ = world.provision("server")
        self.assertEqual(code, 0, stderr)
        report, out = self.aggregate(world, "A")
        self.assertEqual(report["lock"], {"status": "new_entries", "new_entries": 1, "problem": None})
        self.assertEqual((out / "lock-candidate.json").read_bytes(), self.merge_lock(world, "merged-A.json"))
        self.assertEqual([u["status"] for u in report["units"]], ["not_run", "not_run"])
        code, _, stderr, _ = world.provision("gguf")
        self.assertEqual(code, 0, stderr)
        report, out = self.aggregate(world, "B")
        self.assertEqual(report["lock"]["new_entries"], 2)
        self.assertEqual((out / "lock-candidate.json").read_bytes(), self.merge_lock(world, "merged-B.json"))
        self.assertEqual(report["result_class"], "real")
        self.assertIsNone(report["banner"])

    def test_lock_conflict_and_not_computed(self) -> None:
        def lock(w: StubWorld) -> dict[str, Any]:
            return {"schema_version": 1, "models": {},
                    "server": {"tag": w.tag, "asset": w.asset, "sha256": hashlib.sha256(w.tarball).hexdigest()}}

        world = self.world(lock=lock)
        code, _, stderr, record = world.provision("server")
        self.assertEqual((code, record["verified_by"]), (0, "lock"), stderr)
        report, out = self.aggregate(world, "same")
        self.assertEqual(report["lock"], {"status": "unchanged", "new_entries": 0, "problem": None})
        path = world.records / "server" / "provision.json"
        record["server"]["sha256"] = "0" * 64
        path.write_bytes(canonical_json(record))
        report, out = self.aggregate(world, "conflict")
        self.assertEqual(report["lock"]["status"], "conflict")
        self.assertEqual(report["lock"]["problem"], f"{LOCK_CONFLICT}: $.server")
        self.assertIsNone(report["lock"]["new_entries"])
        self.assertFalse((out / "lock-candidate.json").exists())

        world.manifest_path.write_text(world.manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        report, out = self.aggregate(world, "changed")
        self.assertEqual(report["lock"], {"status": "not_computed", "new_entries": None, "problem": None})
        self.assertFalse((out / "lock-candidate.json").exists())

    def test_provision_rows_newest_attempt(self) -> None:
        world = self.world()
        world.provision_all()
        report, _ = self.aggregate(world, "one")
        rows = [(r["target"], r["run_attempt"], r["verified"], r["plan_matches"]) for r in report["provision"]]
        self.assertEqual(rows, [("server", 1, True, True), ("gguf", 1, True, True)])
        self.assertEqual({r["source"] for r in report["provision"]}, {"download"})
        server = _json(world.records / "server" / "provision.json")
        write_json(world.records / "server-2" / "provision.json",
                          {**server, "run_attempt": 2, "cache": {**server["cache"], "hit": True},
                           "plan_sha256": "1" * 64})
        report, _ = self.aggregate(world, "two")
        row = report["provision"][0]
        self.assertEqual((row["target"], row["run_attempt"], row["source"], row["plan_matches"]),
                         ("server", 2, "cache", False))
        self.assertEqual(report["lock"]["new_entries"], 1)
        write_json(world.records / "server-2b" / "provision.json", {**server, "run_attempt": 2})
        report, out = self.aggregate(world, "ambiguous")
        self.assertEqual([r["state"] for r in report["provision"]], ["ambiguous"])
        self.assertEqual(report["lock"]["status"], "not_computed")
        self.assertFalse((out / "lock-candidate.json").exists())


if __name__ == "__main__":
    unittest.main()

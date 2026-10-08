"""The experiment adapters: E1 (generator labels, preregistration, repeats, the comparison and its honest labels), E2
(pushdown with a long-context serving class, its worst-case payload and projection), X1, and the openFDA replay with
its N1 and E1 labelling sheets. Nothing here measures a model: every server is a fake or a stub, every openFDA record
comes from a loopback stub, and every report says ``plumbing`` or ``measurement: false``.

One dry run of ``tests/lab/data/requests/all-experiments.json`` (against the openFDA stub, with a sentinel openFDA key
in its environment) is shared by the classes that read it; :class:`~tests.lab.helpers.DryTree` copies of it are
mutated and re-sealed for the comparison cases.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

from lab import ROOT as LAB_ROOT
from lab import aggregate as lab_aggregate
from lab import openfda as lab_openfda
from lab import prereg as lab_prereg
from lab import shard as lab_shard
from lab import units
from lab.goldlabels import GOLD_KEYS, GoldLabelsError, build_labels
from lab.notes import (COLUMNS, CONTEXT_TOO_SMALL, E1_COMPARE_FAILED, E1_ENDPOINT_EXCLUDED, E1_LABELS,
                       E1_NO_REFERENCE, E1_VERDICTS_WITHHELD, E2_LABELS, HARNESS_USAGE, HEADINGS,
                       OPENFDA_FALSE_ALARM_SCOPE, OPENFDA_LABEL, OPENFDA_RATE_LIMITED, OPENFDA_SAW_RECALLS,
                       OPENFDA_UNREACHABLE, PREREG_MISSING, SHEETS_LABEL, STEP_SKIPPED, X1_LABEL)
from lab.plan import serving_class
from lab.prereg import PreregError, e1_endpoint, load_prereg
from lab.request import OPENFDA_CAP, openfda_budget_problem, openfda_requests
from lab.responder import Responder
from lab.summary import ids, render_report
from lab.warmup import e2_worst_payloads
from mycelic.collective.experiments import e1_extract, e2_pushdown
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import parse_routing
from mycelic.collective.inference.tasks import render_messages
from mycelic.collective.jsonio import canonical_bytes
from mycelic.collective.packs import loader
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.loader import load_pack
from mycelic.collective.schemacheck import compile as compile_schema
from tests.lab.helpers import (LAB_MANIFEST, MANIFEST_TEST, MODEL_KEY, ROOT, DryTree, StubWorld, call_main,
                               check_sources, dry_run, kill_mentioning, make_plan, plumbing_min, run_plan, run_prereg,
                               write_json)
from tests.lab.stubs.openfda_stub import OpenFDAStub, fixture

ALL_EXPERIMENTS = ROOT / "tests" / "lab" / "data" / "requests" / "all-experiments.json"
SENTINEL = "Qz77OpenFdaKeySnTl"
KEY_VAR = lab_openfda.KEY_VAR
ARG_RE = re.compile(r"--[a-z][a-z0-9-]*=.*", re.S)
DIRTY_OVERRIDE = "--allow-" + "dirty"
DQ = load_pack("device_quality")
SITES = [s["id"] for s in DQ.generator["sites"]]
STATUS_LINE_RE = re.compile(r"lab: unit [a-z0-9-]+ status [a-z_]+ class [a-z-]+ exit (-?[0-9]+|none) "
                            r"wall_s [0-9]+\.[0-9]")
E2_BLOCK = {"minutes": 5, "pack": "device_quality", "plant": "plant_e2_smoke", "seeds": [1], "weeks": 52,
            "eval_from": 20, "eval_to": 51, "top_n": 5, "min_candidates": 5, "bootstrap_b": 1000}
E1_BLOCK = {"minutes": 5, "labels": {"source": "generator", "pack": "device_quality", "n": 40, "seed": 1},
            "reference": "fake-b", "margin_points": 5, "runs": 3, "seed": 1, "bootstrap_b": 1000}
X1_BLOCK = {"minutes": 5, "pack": "device_quality", "plant": "plant_smoke", "seeds": [1], "weeks": 52,
            "eval_from": 26, "eval_to": 51, "bootstrap_b": 1000}
OPENFDA_BLOCK = {"minutes": 10, "pack": "device_quality", "product_codes": ["AAA", "BBB", "CCC"],
                 "date_from": "20240101", "date_to": "20241229", "max_records_per_code": 2000,
                 "manufacturers": ["ACME Devices"], "manufacturer_field": "device[].manufacturer_d_name",
                 "partition_field": "event_location", "saw_recall_outcomes": "no", "n1_sheet": {"n": 30, "seed": 1},
                 "e1_sheet": {"n": 20, "seed": 1}}
OPENFDA_FILES = ("cache/events/manifest.json", "cache/recalls/manifest.json", "runs/replay/prereg/prereg.json",
                 "runs/replay/signals/signals.json", "runs/replay/signals/phase1.json",
                 "runs/replay/score/replay.json", "runs/n1/n1/sample.json", "runs/n1/n1/sheet.csv",
                 "runs/n1/n1/sheet.jsonl", "runs/e1/e1/prepare.json", "runs/e1/e1/records.jsonl",
                 "runs/e1/e1/sheet.csv")
E2_LEDGER_RE = re.compile(r"runs/e2/[A-Za-z0-9._-]+/work/seed-[0-9]+/edge/site-[a-z0-9-]+\.ledger\.jsonl")


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _request(**experiments: Any) -> dict[str, Any]:
    obj = plumbing_min()
    obj["experiments"] = experiments
    return obj


def _record(root: Path, unit: str) -> dict[str, Any]:
    return _json(next(Path(root).glob(f"shards/*/units/{unit}/unit.json")))


def _shard_of(root: Path, unit: str) -> str:
    return next(Path(root).glob(f"shards/*/units/{unit}/unit.json")).parents[2].name


class _Shared:
    """The all-experiments dry run, made once for the module."""

    tmp: Path | None = None
    out: Path | None = None
    done: subprocess.CompletedProcess[str] | None = None
    requests: list[dict[str, Any]] = []

    @classmethod
    def get(cls) -> "type[_Shared]":
        if cls.done is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-experiments-"))
            cls.out = cls.tmp / "D"
            with OpenFDAStub(fixture()) as stub:
                cls.done = dry_run(cls.out, ALL_EXPERIMENTS, openfda_base_url=stub.base_url, **{KEY_VAR: SENTINEL})
                cls.requests = list(stub.requests)
        return cls


def tearDownModule() -> None:
    if _Shared.tmp is not None:
        kill_mentioning(str(_Shared.tmp))
        shutil.rmtree(_Shared.tmp, True)


# --------------------------------------------------------------------------------------------------- the dry run

class AllExperimentsDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shared = _Shared.get()
        cls.out, cls.done, cls.requests = shared.out, shared.done, shared.requests

    def setUp(self) -> None:
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)

    def test_stdout_order(self) -> None:
        plan = _json(self.out / "plan" / "plan.json")
        lines = self.done.stdout.splitlines()
        units_in_order = [u for s in plan["shards"] for u in s["units"]]
        self.assertEqual(lines[0], f"plan: {len(plan['units'])} units in {len(plan['shards'])} shards (plumbing)")
        self.assertEqual(lines[1], "prereg: e1 yes x1 yes e2 yes")
        self.assertEqual([line.split()[2] for line in lines[2:-1]], units_in_order)
        self.assertTrue(all(STATUS_LINE_RE.fullmatch(line) for line in lines[2:-1]), lines)
        self.assertEqual(lines[-1], f"lab: aggregate units {len(plan['units'])} shards {len(plan['shards'])} class "
                                    "plumbing measurements false lock unchanged")
        self.assertEqual(len(lines), len(units_in_order) + 3)
        self.assertEqual({line.split()[4] for line in lines[2:-1]}, {"ok"})

    def test_e1_comparison(self) -> None:
        e1 = _json(self.out / "report" / "e1" / "e1" / "compare" / "e1.json")
        self.assertIs(e1["measurement"], False)
        self.assertEqual(e1["verdicts_withheld"], "measurement_false")
        self.assertTrue(e1["paired"])
        for entry in e1["paired"].values():
            self.assertIsNone(entry["non_inferior"])
            self.assertIsNone(entry["kill_flag"])
        block = _json(self.out / "report" / "report.json")["e1"]
        self.assertEqual((block["compared"], block["verdicts_shown"], block["allow_incomplete"], block["reason"]),
                         (True, False, False, None))
        self.assertEqual((block["label"], block["display_class"], block["excluded"]),
                         ("generator_text", "plumbing", []))
        self.assertEqual(block["e1_json"], "e1/e1/compare/e1.json")
        self.assertEqual(sorted(block["endpoints"]), ["fake-a", "fake-b"])
        self.assertEqual(sorted(block["paired"]), ["fake-a"])
        paired, entry = block["paired"]["fake-a"], e1["paired"]["fake-a"]
        self.assertEqual(paired["against"], "fake-b")
        # the harness's paired entry has one of two shapes: the decision on micro field F1 (a field_f1 object, the
        # per-record mean difference under per_record_field_f1) or on the per-record mean field F1 at the top level
        if isinstance(entry.get("field_f1"), dict):
            expected = ("micro_field_f1", entry["field_f1"]["diff"], *entry["field_f1"]["ci95"],
                        entry["per_record_field_f1"]["mean_diff"], entry["per_record_field_f1"]["sign_p"],
                        entry["withheld_reason"])
        else:
            expected = ("per_record_mean_field_f1", entry["mean_diff"], *entry["ci95"], entry["mean_diff"],
                        entry["sign_p"], None)
        self.assertEqual((paired["decision_metric"], paired["diff"], paired["ci_low"], paired["ci_high"],
                          paired["mean_diff"], paired["sign_p"], paired["withheld_reason"]), expected)
        self.assertIsNotNone(paired["ci_low"])
        self.assertEqual((paired["n"], paired["underpowered"]), (entry["n"], entry["underpowered"]))
        self.assertTrue(all(u["included"] for u in block["units"]))
        self.assertEqual(len(block["units"]), 6)
        self.assertNotIn("created_at", json.dumps(block))

    def test_e2_outputs(self) -> None:
        record = _record(self.out, "e2-fake-a")
        run = f"runs/e2/{record['run_id']}"
        doc = _json(self.out / "shards" / _shard_of(self.out, "e2-fake-a") / run / "e2.json")
        self.assertIs(doc["stamps"]["measurement"], False)
        self.assertIs(doc["stamps"]["below_protocol_minimum"], True)
        self.assertEqual(sorted(record["files"]), sorted([f"{run}/e2.json", f"{run}/central.ledger.jsonl",
                                                         *(f"{run}/work/seed-1/edge/site-{s}.ledger.jsonl"
                                                           for s in SITES)]))
        self.assertEqual(record["notes"], ["plumbing", "synthetic", "runner_hardware", "text_only_scan"])
        self.assertEqual(sorted(record["routing_files"]),
                         sorted(["routing/e2-fake-a/central.json",
                                 *(f"routing/e2-fake-a/sites/{s}.json" for s in SITES)]))
        self.assertEqual(record["routing_sha256"], record["routing_files"]["routing/e2-fake-a/central.json"])
        self.assertEqual(record["participation"]["required"], ["judge_record", "judge_candidate_raw"])
        self.assertIsNone(record["projection"])

    def test_x1_outputs(self) -> None:
        record = _record(self.out, "x1")
        run = f"runs/x1/{record['run_id']}"
        scorecard = _json(self.out / "shards" / _shard_of(self.out, "x1") / run / "scorecard.json")
        self.assertIs(scorecard["stamps"]["measurement"], False)
        self.assertEqual(scorecard["stamps"]["extractor"], "lexical")
        self.assertEqual(sorted(record["files"]), [f"{run}/labels.json", f"{run}/scorecard.json"])
        self.assertEqual((record["kind"], record["measurement_class"], record["routing_sha256"]),
                         ("none", "no-model", None))
        self.assertEqual(record["notes"], ["synthetic"])

    def test_openfda_outputs(self) -> None:
        record = _record(self.out, "openfda")
        self.assertEqual((record["status"], record["status_reason"], record["argv"], record["harness_measurement"]),
                         ("ok", None, [], None))
        base = f"runs/openfda/{record['run_id']}"
        self.assertEqual(sorted(record["files"]), sorted(f"{base}/{rel}" for rel in OPENFDA_FILES))
        self.assertEqual([s["step"] for s in record["steps"]], ["fetch-events", "replay-prereg", "replay-signals",
                                                                "fetch-recalls", "replay-score", "n1-sample",
                                                                "e1-prepare"])
        self.assertEqual({s["status"] for s in record["steps"]}, {"ok"})
        self.assertEqual(record["notes"], ["public_data"])
        self.assertEqual(sorted(record["logs"]), sorted(f"{s['step']}.{n}" for s in record["steps"]
                                                       for n in ("stdout", "stderr")))
        shard = self.out / "shards" / _shard_of(self.out, "openfda")
        self.assertEqual([p for p in shard.rglob("*") if "pages" in p.parts or p.name.startswith("page-")], [])
        provenance = _json(shard / "provenance.json")
        self.assertEqual(provenance["server"], {"kind": "none"})
        self.assertIsNone(provenance["model"])
        self.assertEqual(provenance["openfda"]["api_key"], "present")
        self.assertTrue(provenance["openfda"]["base_url_override"].startswith("http://127.0.0.1:"))
        plan = _json(self.out / "plan" / "plan.json")
        flags = {entry["shard"]: entry["openfda"] for entry in plan["matrix"]["include"]}
        self.assertEqual(flags, {s["shard"]: "openfda" in s["units"] for s in plan["shards"]})
        self.assertEqual(sum(flags.values()), 1)

    def test_collected_path_allowlist(self) -> None:
        forbidden = re.compile(r"\.sqlite3|/private/|/pages/|(^|/)work/")
        checked = 0
        for path in self.out.glob("shards/*/units/*/unit.json"):
            for rel in _json(path)["files"]:
                checked += 1
                if forbidden.search(rel):
                    self.assertRegex(rel, E2_LEDGER_RE)
        for shard in self.out.glob("shards/*"):
            for path in (shard / "runs").rglob("*"):
                rel = path.relative_to(shard).as_posix()
                if path.is_file():
                    checked += 1
                    if forbidden.search(rel):
                        self.assertRegex(rel, E2_LEDGER_RE)
        self.assertGreater(checked, 30)
        self.assertEqual([p for p in self.out.rglob("*") if ".sqlite3" in p.name], [])
        self.assertEqual([p for p in self.out.glob("shards/*/work")], [])

    def test_sentinel_nowhere_and_the_stub_saw_it(self) -> None:
        needle = SENTINEL.encode()
        files = [p for p in self.out.rglob("*") if p.is_file()]
        self.assertGreater(len(files), 100)
        self.assertEqual([p for p in files if needle in p.read_bytes()], [])
        self.assertNotIn(SENTINEL, self.done.stdout + self.done.stderr)
        self.assertTrue(self.requests)
        self.assertEqual({r["api_key"] for r in self.requests}, {SENTINEL})
        self.assertEqual({r["dataset"] for r in self.requests}, {"event", "recall"})

    def test_summaries_put_labels_before_tables_and_source_every_number(self) -> None:
        md = (self.out / "report" / "report.md").read_text(encoding="utf-8")
        lines = md.splitlines()

        def index(text: str) -> int:
            return lines.index(text)

        def first_table_after(i: int) -> int:
            return next(j for j in range(i, len(lines)) if lines[j].startswith("| "))

        e2_heading = "#### " + HEADINGS["e2"]
        for heading, label in (("#### " + HEADINGS["e1"], ids(E1_LABELS["generator_text"])),
                               (e2_heading, ids(E2_LABELS["synthetic"])), (e2_heading, E2_LABELS["below_protocol"]),
                               (e2_heading, E2_LABELS["self_central"]), ("#### " + HEADINGS["x1"], ids(X1_LABEL)),
                               ("#### " + HEADINGS["openfda"], OPENFDA_LABEL),
                               ("#### " + HEADINGS["openfda"], OPENFDA_FALSE_ALARM_SCOPE),
                               ("#### " + HEADINGS["sheets"], ids(SHEETS_LABEL))):
            with self.subTest(label=label[:40]):
                self.assertEqual((lines.count(heading), lines.count(label)), (1, 1))
                self.assertLess(index(heading), index(label))
                self.assertLess(index(label), first_table_after(index(heading)))
        self.assertIn(E1_VERDICTS_WITHHELD, lines)
        # the request declared it did not see recall outcomes first: no hindsight sentence, the declaration shown
        (row,) = _json(self.out / "report" / "report.json")["openfda"]
        self.assertIs(row["recall_outcomes_seen_before_prereg"], False)
        self.assertNotIn(OPENFDA_SAW_RECALLS, lines)
        declared = f"- `openfda`: {COLUMNS['saw_recalls']} no; {COLUMNS['warnings']}: "
        (line,) = [line for line in lines if line.startswith(declared)]
        self.assertEqual(line, declared + (", ".join(f"`{w}`" for w in row["warnings"]) or "none"))
        self.assertLess(index(line), first_table_after(index("#### " + HEADINGS["openfda"])))
        self.assertNotIn("| non-inferior", md)
        self.assertNotIn("| bar verdict", md)
        for mode, directory, md_name, src_name in (("plan", self.out / "plan", "summary.md", "summary.sources.json"),
                                                   ("report", self.out / "report", "report.md",
                                                    "report.sources.json")):
            with self.subTest(mode=mode):
                text = (directory / md_name).read_text(encoding="utf-8")
                sources = _json(directory / src_name)["sources"]
                self.assertGreater(check_sources(self, text, sources, directory), 10)
        plan_md = (self.out / "plan" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("### Preregistration, fixed before any model runs", plan_md)

    def test_routing_pins_equal_the_preregistered_endpoints(self) -> None:
        prereg = _json(self.out / "plan" / "prereg" / "e1" / "prereg" / "prereg.json")
        pinned = {e["name"]: {f: e[f] for f in e1_extract.PINNED_ENDPOINT_FIELDS} for e in prereg["endpoints"]}
        plan = _json(self.out / "plan" / "plan.json")
        files = sorted(self.out.glob("shards/*/routing/e1-*.json"))
        self.assertEqual(len(files), 6)
        for path in files:
            doc = _json(path)
            (name, endpoint), = doc["endpoints"].items()
            # pinned as the harness pins them: the parsed endpoint's attributes, a mapping as a JSON object
            parsed = parse_routing(doc, check_env=False).endpoints[name]
            now = {f: getattr(parsed, f) for f in e1_extract.PINNED_ENDPOINT_FIELDS}
            self.assertEqual({f: dict(v) if isinstance(v, Mapping) else v for f, v in now.items()}, pinned[name])
            other = e1_endpoint(plan["models"][name], "http://other.invalid/v1")
            self.assertEqual({**endpoint, "base_url": None}, {**other, "base_url": None})
            self.assertEqual(doc["routes"], {})


# --------------------------------------------------------------------------------------------------- E1 compare

class E1CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shared = _Shared.get()
        cls.source = shared.out

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="e1-", dir=_Shared.tmp))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.tree = DryTree(self.source, self.work / "T", manifest=LAB_MANIFEST)

    def remove_unit(self, unit: str) -> None:
        shard = _shard_of(self.tree.root, unit)
        shutil.rmtree(self.tree.path(shard, f"units/{unit}"))
        self.tree.reseal(shard)

    def test_relocated_run_dirs_still_compare(self) -> None:
        report = self.source / "report" / "e1"
        moved = self.work / "moved"
        shutil.copytree(report / "runs", moved / "runs")
        shutil.copy2(report / "prereg.json", moved / "prereg.json")
        argv = lab_aggregate.e1_compare_argv(moved / "prereg.json", sorted((moved / "runs").iterdir()), moved, False)
        done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((moved / "e1" / "compare" / "e1.json").is_file())

    def test_reference_repeat_missing_means_no_comparison(self) -> None:
        self.remove_unit("e1-fake-b-r2")
        code, _, stderr, report = self.tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        block = report["e1"]
        self.assertEqual((block["compared"], block["reason"], block["e1_json"]), (False, E1_NO_REFERENCE, None))
        self.assertEqual(block["excluded"], ["fake-b"])
        self.assertFalse((self.work / "R" / "e1" / "e1").exists())
        self.assertFalse(any(u["included"] for u in block["units"]))

    def test_non_reference_repeat_missing_is_left_out_and_stamped(self) -> None:
        self.remove_unit("e1-fake-a-r1")
        out = self.work / "R"
        code, _, stderr, report = self.tree.aggregate(out)
        self.assertEqual(code, 0, stderr)
        block = report["e1"]
        self.assertEqual((block["compared"], block["allow_incomplete"], block["excluded"]), (True, True, ["fake-a"]))
        self.assertEqual(block["endpoints_without_runs"], ["fake-a"])
        e1 = _json(out / "e1" / "e1" / "compare" / "e1.json")
        self.assertEqual((e1["allow_incomplete"], e1["endpoints_without_runs"]), (True, ["fake-a"]))
        self.assertEqual(sorted(p.name for p in (out / "e1" / "runs").iterdir()),
                         sorted(u["run_id"] for u in self.tree.plan["units"] if u["unit"].startswith("e1-fake-b-")))
        md, sources = render_report(out)
        self.assertIn(E1_ENDPOINT_EXCLUDED, md.splitlines())
        check_sources(self, md, sources, out)

    def test_a_tampered_unit_is_excluded_and_its_model_left_out(self) -> None:
        unit = "e1-fake-a-r2"
        shard = _shard_of(self.tree.root, unit)
        record = self.tree.read(shard, f"units/{unit}/unit.json")
        rel = f"runs/e1/{record['run_id']}/predictions.jsonl"
        self.tree.path(shard, rel).write_bytes(self.tree.path(shard, rel).read_bytes() + b"\n")
        self.tree.reseal(shard)
        code, _, stderr, report = self.tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        row = next(u for u in report["units"] if u["unit"] == unit)
        self.assertEqual(row["status"], "excluded")
        block = report["e1"]
        self.assertEqual((block["compared"], block["excluded"], block["allow_incomplete"]), (True, ["fake-a"], True))

    def test_a_rewritten_record_is_refused_by_the_harness_hashes(self) -> None:
        unit = "e1-fake-a-r2"
        shard = _shard_of(self.tree.root, unit)
        record = self.tree.read(shard, f"units/{unit}/unit.json")
        rel = f"runs/e1/{record['run_id']}/predictions.jsonl"
        self.tree.replace_unit_file(shard, unit, rel, self.tree.path(shard, rel).read_bytes() + b"\n")
        self.tree.reseal(shard)
        code, _, stderr, report = self.tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((report["e1"]["compared"], report["e1"]["reason"]), (False, E1_COMPARE_FAILED))

    def test_model_class_without_measurement_withholds_verdicts(self) -> None:
        e1_units = [u["unit"] for u in self.tree.plan["units"] if u["experiment"] == "e1"]
        for shard in sorted({_shard_of(self.tree.root, u) for u in e1_units}):
            self.tree.make_real(shard, model_units=tuple(u for u in e1_units if _shard_of(self.tree.root, u) == shard))
        out = self.work / "R"
        code, _, stderr, report = self.tree.aggregate(out)
        self.assertEqual(code, 0, stderr)
        block = report["e1"]
        self.assertEqual({u["display_class"] for u in block["units"]}, {"model"})
        self.assertEqual((block["compared"], block["display_class"], block["measurement"], block["verdicts_shown"]),
                         (True, "model", False, False))
        md, sources = render_report(out)
        self.assertNotIn("| non-inferior |", md)
        self.assertIn(E1_VERDICTS_WITHHELD, md.splitlines())
        check_sources(self, md, sources, out)

    def test_a_foreign_prereg_is_refused(self) -> None:
        (self.tree.root / "plan" / "prereg" / "e1" / "labels.json").write_bytes(b"{}\n")
        code, _, stderr, report = self.tree.aggregate(self.work / "R")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((report["e1"]["compared"], report["e1"]["reason"]), (False, PREREG_MISSING))


class E1LabelOrderTests(unittest.TestCase):
    """A crafted report's E1 block: the label always first, the verdict columns only for a shown measurement."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-e1-label-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def report(self, **changes: Any) -> tuple[str, list[dict[str, Any]], Path]:
        block = {
            "compared": True, "reason": None, "label": "generator_text", "display_class": "model",
            "labels": {"source": "generator", "pack": "device_quality", "n": 600, "seed": 1, "records": 600,
                       "claims": 900, "sha256": "a" * 64},
            "underpowered_below": 600, "kill_below": 0.8, "margin": 0.05, "runs": 3, "reference": "m-b",
            "units": [], "excluded": [], "allow_incomplete": False, "endpoints_without_runs": [], "measurement": True,
            "verdicts_shown": True, "e1_json": "e1/e1/compare/e1.json",
            "endpoints": {name: {"runs": 3, **{f: {"value": 0.9, "ci_low": 0.85, "ci_high": 0.95}
                                               for f in lab_aggregate.E1_F1},
                                 "json_validity_rate": 1.0, "valid_after_repair_rate": 1.0, "exact_match": {},
                                 "latency_ms_p50": 900.0, "latency_ms_p95": 1500.0, "model_mismatch": False}
                          for name in ("m-a", "m-b")},
            "paired": {"m-a": {"against": "m-b", "n": 600, "decision_metric": "micro_field_f1", "diff": 0.02,
                               "ci_low": -0.02, "ci_high": 0.04, "mean_diff": 0.01, "sign_p": 0.5,
                               "underpowered": False, "non_inferior": True, "kill_flag": False,
                               "withheld_reason": None}}}
        block.update(changes)
        report = {"schema_version": 1, "kind": "lab_report", "result_class": "real", "contains_measurements": True,
                  "banner": None, "request": {}, "plan": {}, "unit_count": 0, "shard_count": 0, "shards": [],
                  "units": [], "e1": block, "lock": {}, "notes": {}}
        write_json(self.tmp / "report.json", report)
        md, sources = render_report(self.tmp)
        return md, sources, self.tmp

    def test_label_before_verdict_table(self) -> None:
        md, sources, root = self.report()
        lines = md.splitlines()
        label = lines.index(ids(E1_LABELS["generator_text"]))
        header = next(i for i, line in enumerate(lines) if "| non-inferior | kill flag |" in line)
        self.assertLess(label, header)
        self.assertNotIn(E1_VERDICTS_WITHHELD, lines)
        self.assertEqual(lines[header + 2].count("|"), 13)
        self.assertIn("| model | against | paired records | decision metric | difference | interval low | interval "
                      "high | mean difference | sign test p | underpowered | non-inferior | kill flag |", lines)
        self.assertIn("| `m-a` | `m-b` | 600 | `micro_field_f1` | 0.020 | -0.020 | 0.040 | 0.010 | 0.500 | no | yes "
                      "| no |", lines)
        check_sources(self, md, sources, root)

    def test_withheld_without_verdict_columns(self) -> None:
        md, sources, root = self.report(verdicts_shown=False, measurement=False)
        self.assertNotIn("| non-inferior |", md)
        self.assertNotIn("| kill flag |", md)
        self.assertIn(E1_VERDICTS_WITHHELD, md.splitlines())
        check_sources(self, md, sources, root)

    def test_not_compared_shows_the_reason_and_no_table(self) -> None:
        md, sources, root = self.report(compared=False, reason=E1_NO_REFERENCE, endpoints={}, paired={})
        lines = md.splitlines()
        self.assertIn(E1_NO_REFERENCE, lines)
        self.assertLess(lines.index(ids(E1_LABELS["generator_text"])), lines.index(E1_NO_REFERENCE))
        self.assertNotIn("Extraction per model", md)
        check_sources(self, md, sources, root)

    def test_fixtures_label(self) -> None:
        md, _, _ = self.report(label="fixtures")
        self.assertIn(ids(E1_LABELS["fixtures"]), md.splitlines())


# --------------------------------------------------------------------------------------------------- E2 serving

def _e2_request(minutes: int = 5) -> dict[str, Any]:
    obj = plumbing_min()
    obj["provider"] = json.loads(MANIFEST_TEST.read_text(encoding="utf-8"))["server"]["program"]
    obj["models"] = [MODEL_KEY]
    obj["experiments"] = {"e2": {**E2_BLOCK, "minutes": minutes}}
    return obj


class E2ServingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-e2serve-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))

    def world(self, *, ctx: int, minutes: int = 5, delay: float = 0.0) -> StubWorld:
        world = StubWorld(self.tmp, request=_e2_request(minutes), model={"e2_ctx_per_slot": ctx},
                          server_config={"pack": "device_quality", "chat_delay_s": delay})
        self.addCleanup(world.close)
        world.provision_all()
        return world

    def run_spied(self, world: StubWorld) -> tuple[dict[str, Any], list[list[str]]]:
        out = self.tmp / "S"
        code, _, stderr = world.prepare(out)
        self.assertEqual(code, 0, stderr)
        seen: list[list[str]] = []
        real = units.run_process

        def spy(argv: list[str], *args: Any, **kwargs: Any) -> Any:
            seen.append(list(argv))
            return real(argv, *args, **kwargs)

        with mock.patch.object(units, "run_process", spy):
            world.run(out)
        return _json(out / "units" / "e2-tiny-gguf" / "unit.json"), seen

    def test_worst_case_payload_beyond_the_slot_skips_before_any_harness(self) -> None:
        world = self.world(ctx=8192)
        record, seen = self.run_spied(world)
        self.assertEqual(record["status"], "skipped")
        self.assertTrue(record["status_reason"].startswith("context too small: judge_candidate_raw"),
                        record["status_reason"])
        self.assertIn("raise e2_ctx_per_slot", record["status_reason"])
        self.assertEqual(record["argv"], [])
        self.assertFalse([a for a in seen if "mycelic.collective.experiments.e2_pushdown" in a])
        argv = world.stub_files("argv")[-1]
        self.assertEqual(argv[argv.index("-np") + 1], "1")
        self.assertEqual(argv[argv.index("-c") + 1], "8192")
        self.assertTrue(CONTEXT_TOO_SMALL.startswith("context too small: {task}"))

    def test_projection_above_the_budget_skips_with_the_projection(self) -> None:
        world = self.world(ctx=32768, minutes=1, delay=0.25)
        record, seen = self.run_spied(world)
        self.assertEqual(record["status"], "skipped", record["status_reason"])
        self.assertTrue(record["status_reason"].startswith("projected "), record["status_reason"])
        self.assertEqual(record["argv"], [])
        self.assertFalse([a for a in seen if "mycelic.collective.experiments.e2_pushdown" in a])
        projection = record["projection"]
        inputs = projection["inputs"]
        rehearsal = load_prereg(world.plan_path).manifest["e2"]["rehearsal"]
        self.assertEqual((inputs["pipeline_s"], inputs["judge_record_calls"], inputs["raw_records_sum"],
                          inputs["raw_records_max"], inputs["allowed_calls"]),
                         (rehearsal["pipeline_s"], rehearsal["calls"]["judge_record"],
                          rehearsal["central_raw_records"]["sum"], rehearsal["central_raw_records"]["max"],
                          rehearsal["calls"]["judge_candidate_allowed"]))
        expected = (inputs["pipeline_s"] + inputs["judge_record_calls"] * inputs["judge_s"]
                    + inputs["raw_records_sum"] / inputs["raw_records_max"] * inputs["raw_s"]
                    + inputs["allowed_calls"] * inputs["allowed_s"])
        self.assertAlmostEqual(projection["projected_s"], expected, places=2)
        self.assertGreater(projection["projected_s"], 0.9 * 60)
        self.assertEqual((projection["budget_s"], projection["share"], projection["exceeds"]), (60.0, 0.9, True))
        self.assertEqual(projection["suggested_minutes"], math.ceil(1.25 * projection["projected_s"] / 60))
        for key in ("judge_s", "raw_s", "allowed_s"):
            self.assertGreaterEqual(inputs[key], 0.25)
        argv = world.stub_files("argv")[-1]
        self.assertEqual((argv[argv.index("-np") + 1], argv[argv.index("-c") + 1]), ("1", "32768"))

    def test_an_altered_prereg_starts_nothing(self) -> None:
        world = self.world(ctx=32768)
        rehearsal = world.plan_path.parent / "prereg" / "e2" / "rehearsal.json"
        rehearsal.write_bytes(rehearsal.read_bytes() + b"\n")
        starts_before = len(world.stub_files("argv"))
        record, seen = self.run_spied(world)
        self.assertEqual((record["status"], record["status_reason"], record["argv"]), ("failed", PREREG_MISSING, []))
        self.assertEqual(seen, [])
        argv = world.stub_files("argv")
        self.assertEqual(len(argv), starts_before + 1)
        self.assertEqual(argv[-1], ["--version"])
        self.assertIsNone(_json(self.tmp / "S" / "provenance.json")["prereg"])

    def test_projection_without_warmup_rows_is_none(self) -> None:
        rehearsal = {"pipeline_s": 1.0, "calls": {"judge_record": 10, "judge_candidate_raw": 2,
                                                  "judge_candidate_allowed": 2},
                     "central_raw_records": {"max": 4, "sum": 6}}
        rows = [{"task": "judge_record", "ok": True, "latency_ms": 100.0},
                {"task": "judge_candidate_raw", "ok": True, "latency_ms": 2000.0}]
        self.assertIsNone(units.e2_projection(rehearsal, rows, 600))
        rows.append({"task": "judge_candidate_allowed", "ok": True, "latency_ms": 500.0})
        got = units.e2_projection(rehearsal, rows, 600)
        self.assertAlmostEqual(got["projected_s"], 1.0 + 10 * 0.1 + 6 / 4 * 2.0 + 2 * 0.5)
        self.assertFalse(got["exceeds"])
        self.assertEqual(got["suggested_minutes"], 1)


class E2PayloadShapeTests(unittest.TestCase):
    """The worst-case payload has the shape e2_pushdown sends its central comparator, and bounds every real one."""

    def test_worst_payload_shape_and_bound(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lab-e2shape-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        plan, plan_path = make_plan(tmp, _request(e2={**E2_BLOCK, "models": ["fake-a"]}))
        prereg = load_prereg(plan_path)
        unit = next(u for u in plan["units"] if u["experiment"] == "e2")
        seen: list[dict[str, Any]] = []
        responder = Responder(DQ)

        def recording(request_json: Any) -> dict[str, Any]:
            name = request_json["response_format"]["json_schema"]["name"]
            if name == "judge_candidate_raw":
                seen.append(request_payload(request_json))
            return responder(request_json)

        server = FakeOpenAIServer("valid", responder=recording)
        server.start()
        self.addCleanup(server.stop)
        out = tmp / "S"
        units.write_routing(unit, plan, plan["models"]["fake-a"], server.base_url, out, prereg)
        argv = units.build_argv(unit, out, out / "routing" / "unused.json", prereg=prereg.dir)
        self.assertEqual(argv[2:4], ["mycelic.collective.experiments.e2_pushdown", "run"])
        code, _, stderr = call_main(e2_pushdown, argv[3:])
        self.assertEqual(code, 0, stderr)
        rehearsal = prereg.manifest["e2"]["rehearsal"]
        raw, allowed = e2_worst_payloads(unit, rehearsal)
        self.assertTrue(seen)
        self.assertEqual(len(raw["records"]), rehearsal["central_raw_records"]["max"])
        self.assertEqual(len(allowed["records"]), rehearsal["central_raw_records"]["max"])
        worst_text = sum(len(r["text"].encode("utf-8")) for r in raw["records"])
        for payload in seen:
            self.assertEqual(sorted(payload), sorted(raw))
            self.assertEqual(sorted(payload["window"]), sorted(raw["window"]))
            self.assertEqual(sorted(payload["question"]), sorted(raw["question"]))
            self.assertLessEqual(len(payload["records"]), rehearsal["central_raw_records"]["max"])
            for record in payload["records"]:
                self.assertEqual(sorted(record), sorted(raw["records"][0]))
                self.assertEqual(sorted(record["entities"]), sorted(raw["records"][0]["entities"]))
            self.assertGreaterEqual(worst_text, sum(len(r["text"].encode("utf-8")) for r in payload["records"]))
        self.assertEqual(max(len(p["records"]) for p in seen), rehearsal["central_raw_records"]["max"])


# --------------------------------------------------------------------------------------------------- openFDA shards

def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class OpenFDAShardTests(unittest.TestCase):
    """``lab.shard run`` in-process on the model-free shard of an openFDA plan, its fetches pointed at the stub."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-openfda-"))
        block = {**OPENFDA_BLOCK, "manufacturers": ["ACME Devices", "ACME Devices, Inc.", "A&B Co."]}
        cls.plan, cls.plan_path = make_plan(cls.tmp / "p", _request(openfda=block))
        cls.plan_x1, cls.plan_x1_path = make_plan(cls.tmp / "px", _request(openfda=block, x1=X1_BLOCK))
        cls.fixture = fixture()
        cls.runs = 0

    @classmethod
    def tearDownClass(cls) -> None:
        kill_mentioning(str(cls.tmp))
        shutil.rmtree(cls.tmp, True)

    def run_shard(self, base_url: str, *, key: str | None = SENTINEL, plan_path: Path | None = None
                  ) -> tuple[int, dict[str, Any], Path, list[tuple[list[str], dict[str, str]]]]:
        type(self).runs += 1
        out = self.tmp / f"S{self.runs}"
        plan_path = plan_path or self.plan_path
        plan = _json(plan_path)
        shard = next(s["shard"] for s in plan["shards"] if "openfda" in s["units"])
        seen: list[tuple[list[str], dict[str, str]]] = []
        real = units.run_process

        def spy(argv: list[str], env: dict[str, str], *args: Any, **kwargs: Any) -> Any:
            seen.append((list(argv), dict(env)))
            return real(argv, env, *args, **kwargs)

        environ = {k: v for k, v in os.environ.items() if k != KEY_VAR}
        if key is not None:
            environ[KEY_VAR] = key
        with mock.patch.dict(os.environ, environ, clear=True), mock.patch.object(units, "run_process", spy):
            code, _, stderr = call_main(lab_shard, ["run", "--plan", str(plan_path), "--shard", shard, "--out",
                                                    str(out), "--provider", "fake", f"--openfda-base-url={base_url}"])
        return code, _json(out / "units" / "openfda" / "unit.json"), out, seen

    def steps(self, record: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {s["step"]: s for s in record["steps"]}

    def test_only_the_fetches_see_the_key(self) -> None:
        with OpenFDAStub(self.fixture) as stub:
            url = stub.base_url
            code, record, out, seen = self.run_shard(url, plan_path=self.plan_x1_path)
        self.assertEqual(code, 0)
        self.assertEqual(record["status"], "ok", record["status_reason"])
        holders = [argv for argv, env in seen if KEY_VAR in env]
        self.assertEqual([a[3:5] for a in holders], [["fetch", "--dataset=event"], ["fetch", "--dataset=recall"]])
        self.assertTrue(all(env[KEY_VAR] == SENTINEL for argv, env in seen if KEY_VAR in env))
        self.assertTrue(all(f"--api-key-env={KEY_VAR}" in argv for argv in holders))
        others = [argv for argv, env in seen if KEY_VAR not in env]
        self.assertTrue(any("mycelic.collective.evaluate.harness" in a for a in others))
        self.assertEqual(sum("mycelic.collective.experiments.openfda_replay" in a for a in others), 3)
        for argv, env in seen:
            self.assertNotIn(SENTINEL, " ".join(argv))
            self.assertTrue(set(env) <= {*units.ENV_ALLOWLIST, "PYTHONUNBUFFERED", KEY_VAR}, sorted(env))
        self.assertEqual({r["api_key"] for r in stub.requests}, {SENTINEL})
        files = [p for p in out.rglob("*") if p.is_file()] + [p for p in self.plan_x1_path.parent.rglob("*")
                                                              if p.is_file()]
        self.assertEqual([p for p in files if SENTINEL.encode() in p.read_bytes()], [])
        provenance = _json(out / "provenance.json")
        self.assertEqual(provenance["openfda"], {"api_key": "present", "base_url_override": url})
        for argv, _ in seen:
            for arg in argv[4:]:
                self.assertRegex(arg, ARG_RE)

    def test_no_key_no_flag_and_no_key_on_the_wire(self) -> None:
        for key in (None, ""):
            with self.subTest(key=key), OpenFDAStub(self.fixture) as stub:
                code, record, out, seen = self.run_shard(stub.base_url, key=key)
                self.assertEqual((code, record["status"]), (0, "ok"))
                self.assertFalse([a for argv, _ in seen for a in argv if a.startswith("--api-key-env")])
                self.assertFalse([env for _, env in seen if KEY_VAR in env])
                self.assertEqual({r["api_key"] for r in stub.requests}, {None})
                self.assertEqual(_json(out / "provenance.json")["openfda"]["api_key"], "absent")

    def test_unreachable_host(self) -> None:
        code, record, out, _ = self.run_shard(f"http://127.0.0.1:{_closed_port()}")
        self.assertEqual(code, 1)
        self.assertEqual((record["status"], record["status_reason"]), ("failed", OPENFDA_UNREACHABLE))
        steps = self.steps(record)
        self.assertEqual((steps["fetch-events"]["status"], steps["fetch-events"]["exit_code"]), ("failed", 3))
        for name in ("replay-prereg", "replay-signals", "fetch-recalls", "replay-score", "n1-sample", "e1-prepare"):
            self.assertEqual((steps[name]["status"], steps[name]["reason"]), ("skipped", STEP_SKIPPED), name)
        self.assertEqual(record["files"], {})

    def test_rate_limited(self) -> None:
        with OpenFDAStub(self.fixture, fail_429=True) as stub:
            code, record, _, _ = self.run_shard(stub.base_url)
        self.assertEqual((code, record["status"], record["status_reason"]), (1, "failed", OPENFDA_RATE_LIMITED))
        self.assertGreater(len(stub.requests), 1)

    def test_every_code_not_found(self) -> None:
        with OpenFDAStub(self.fixture, all_404=True) as stub:
            code, record, _, _ = self.run_shard(stub.base_url)
        self.assertEqual(code, 1)
        steps = self.steps(record)
        self.assertEqual(steps["fetch-events"]["status"], "ok")
        self.assertEqual((steps["replay-signals"]["status"], steps["replay-signals"]["reason"]),
                         ("failed", f"{HARNESS_USAGE}: replay-signals"))
        self.assertEqual((record["status"], record["status_reason"]), ("failed", f"{HARNESS_USAGE}: replay-signals"))
        self.assertEqual((steps["n1-sample"]["status"], steps["e1-prepare"]["status"]), ("ok", "ok"))
        self.assertEqual((steps["fetch-recalls"]["status"], steps["replay-score"]["status"]), ("skipped", "skipped"))
        base = f"runs/openfda/{record['run_id']}"
        self.assertIn(f"{base}/cache/events/manifest.json", record["files"])
        self.assertIn(f"{base}/runs/n1/n1/sheet.csv", record["files"])
        self.assertNotIn(f"{base}/cache/recalls/manifest.json", record["files"])

    def test_manufacturer_spellings_are_single_argv_elements(self) -> None:
        params = self.plan["units"][0]["params"]
        steps = dict(lab_openfda.step_argvs(params, Path("/r"), key=False, base_url=None))
        prereg = steps["replay-prereg"]
        for name in ("ACME Devices", "ACME Devices, Inc.", "A&B Co."):
            self.assertIn(f"--manufacturer={name}", prereg)
            self.assertIn(f"--recalling-firm={name}", prereg)
        for step, argv in steps.items():
            self.assertEqual(argv[:2], [sys.executable, "-m"])
            for arg in argv[4:]:
                self.assertRegex(arg, ARG_RE, step)
            self.assertNotIn(DIRTY_OVERRIDE, " ".join(argv))
        self.assertEqual(list(steps), ["fetch-events", "replay-prereg", "replay-signals", "fetch-recalls",
                                       "replay-score", "n1-sample", "e1-prepare"])


# --------------------------------------------------------------------------------------------------- budget, labels

class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-budget-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def plan(self, key: str | None) -> tuple[int, str, str]:
        block = {**OPENFDA_BLOCK, "product_codes": ["AAA", "BBB", "CCC", "DDD", "EEE"],
                 "max_records_per_code": 25000}
        path = write_json(self.tmp / "budget.json", _request(openfda=block))
        environ = {k: v for k, v in os.environ.items() if k != "LAB_HAS_OPENFDA_KEY"}
        if key is not None:
            environ["LAB_HAS_OPENFDA_KEY"] = key
        with mock.patch.dict(os.environ, environ, clear=True):
            return run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out",
                             str(self.tmp / f"out-{key}")])

    def test_without_a_key_the_budget_refuses(self) -> None:
        for key in (None, "false", ""):
            code, _, stderr = self.plan(key)
            self.assertEqual(code, 2)
            first = stderr.splitlines()[0]
            self.assertTrue(first.startswith("error: $.experiments.openfda.max_records_per_code: the fetch would make "
                                             "about 250 openFDA requests"), first)
            self.assertIn("without an API key", first)
            self.assertIn("MYCELIC_LAB_OPENFDA_API_KEY", first)

    def test_with_a_key_it_plans(self) -> None:
        code, stdout, stderr = self.plan("true")
        self.assertEqual(code, 0, stderr)
        plan = _json(self.tmp / "out-true" / "plan.json")
        self.assertEqual(plan["units"][0]["params"]["requests_estimate"], 250)

    def test_request_count_and_caps(self) -> None:
        self.assertEqual(openfda_requests(["AAA"], 1000), 2)
        self.assertEqual(openfda_requests(["AAA"], 1001), 4)
        self.assertEqual(openfda_requests(["AAA", "BBB", "CCC"], 1), 6)
        self.assertEqual(OPENFDA_CAP, {False: 100, True: 1000})
        self.assertIsNone(openfda_budget_problem(1000, True))
        self.assertIn("more than the 1000 allowed with an API key", openfda_budget_problem(1001, True))
        self.assertIsNone(openfda_budget_problem(100, False))
        self.assertIn("more than the 100 allowed without an API key", openfda_budget_problem(101, False))


class GoldLabelsTests(unittest.TestCase):
    def test_generator_labels(self) -> None:
        data, record = build_labels("generator", "device_quality", 40, 1)
        again, _ = build_labels("generator", "device_quality", 40, 1)
        self.assertEqual(data, again)
        lines = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        self.assertEqual(len(lines), 40)
        refs = [line["record"]["record_ref"] for line in lines]
        self.assertEqual(refs, sorted(refs))
        self.assertTrue(all(line["record"]["origin_ref"] is None for line in lines))
        for line in lines:
            self.assertEqual(sorted(line), ["gold", "record"])
            for item in line["gold"]:
                self.assertEqual(sorted(item), sorted(GOLD_KEYS))
        self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((record["n"], record["seed"], record["sites"], record["weeks"], record["records"]),
                         (40, 1, 6, 104, 40))
        self.assertEqual(record["claims"], sum(len(line["gold"]) for line in lines))
        self.assertGreater(record["available"], 2000)
        self.assertRegex(record["world_digest"], r"[0-9a-f]{64}")
        other, _ = build_labels("generator", "device_quality", 40, 2)
        self.assertNotEqual(other, data)
        tmp = Path(tempfile.mkdtemp(prefix="lab-gold-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "labels.jsonl").write_bytes(data)
        labels, sha = e1_extract.read_labels(tmp / "labels.jsonl", DQ, Canonicaliser(DQ))
        self.assertEqual((len(labels), sha), (40, record["sha256"]))

    def test_fixture_labels_are_the_pack_records(self) -> None:
        for pack in ("device_quality", "claims_integrity"):
            data, record = build_labels("fixtures", pack)
            self.assertEqual(data, (loader.BUILTIN_ROOT / pack / "fixtures" / "records.jsonl").read_bytes())
            self.assertEqual((record["source"], record["n"], record["seed"], record["available"],
                              record["world_digest"]), ("fixtures", None, None, None, None))

    def test_more_than_the_world_holds(self) -> None:
        _, small = build_labels("generator", "claims_integrity", 40, 1)
        _, large = build_labels("generator", "device_quality", 40, 1)
        self.assertLess(small["available"], large["available"])
        with self.assertRaises(GoldLabelsError) as caught:
            build_labels("generator", "claims_integrity", small["available"] + 1, 1)
        self.assertEqual(caught.exception.problem,
                         f"the generator world holds only {small['available']} independent records")
        build_labels("generator", "device_quality", small["available"] + 1, 1)


# --------------------------------------------------------------------------------------------------- prereg

class PreregTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-prereg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))

    def plan_only(self, request: dict[str, Any], name: str = "p") -> Path:
        path = write_json(self.tmp / f"{name}.json", request)
        code, _, stderr = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out",
                                    str(self.tmp / name)])
        self.assertEqual(code, 0, stderr)
        return self.tmp / name / "plan.json"

    def test_manifest_lists_every_file_and_the_plan(self) -> None:
        plan, plan_path = make_plan(self.tmp, _request(e1=E1_BLOCK, x1=X1_BLOCK, e2={**E2_BLOCK, "models": ["fake-a"]}))
        root = plan_path.parent
        manifest = _json(root / "prereg" / "prereg.json")
        self.assertEqual(manifest["plan_sha256"], hashlib.sha256(plan_path.read_bytes()).hexdigest())
        files = sorted(p.relative_to(root).as_posix() for p in (root / "prereg").rglob("*") if p.is_file())
        self.assertEqual(sorted(manifest["files"]), [f for f in files if f != "prereg/prereg.json"])
        for rel, meta in manifest["files"].items():
            data = (root / rel).read_bytes()
            self.assertEqual(meta, {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        self.assertEqual(sorted(manifest["files"]), ["prereg/e1/labels.json", "prereg/e1/labels.jsonl",
                                                     "prereg/e1/prereg/prereg.json", "prereg/e1/routing.json",
                                                     "prereg/e2/rehearsal.json", "prereg/x1/e2/prereg.json",
                                                     "prereg/x1/x1/prereg.json"])
        self.assertEqual((manifest["e1"]["endpoints"], manifest["e1"]["reference"]), (["fake-a", "fake-b"], "fake-b"))
        self.assertEqual(manifest["e2"]["plant_path"],
                         "mycelic/collective/packs/data/device_quality/fixtures/plant_e2_smoke.json")
        self.assertEqual(manifest["e2"]["rehearsal"], _json(root / "prereg" / "e2" / "rehearsal.json"))
        self.assertEqual(load_prereg(plan_path).manifest, manifest)
        self.assertEqual(sorted(p.name for p in (root / "prereg-logs").iterdir()),
                         sorted(f"{step}.{n}.log" for step in ("e1-prereg", "x1-prereg", "x1-check-plant",
                                                              "e2-prereg", "e2-check-plant", "e2-rehearsal")
                                for n in ("stdout", "stderr")))
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith("lab-rehearsal-")], [])
        routing = _json(root / "prereg" / "e1" / "routing.json")
        self.assertEqual(routing, lab_prereg.e1_routing_doc(plan["models"], ["fake-a", "fake-b"],
                                                            lab_prereg.PLACEHOLDER_BASE_URL))

    def test_tampering_is_refused_by_the_shard_and_the_aggregate(self) -> None:
        plan, plan_path = make_plan(self.tmp, _request(e1=E1_BLOCK, e3=plumbing_min()["experiments"]["e3"]))
        labels = plan_path.parent / "prereg" / "e1" / "labels.jsonl"
        labels.write_bytes(labels.read_bytes().replace(b"\n", b" \n", 1))
        with self.assertRaises(PreregError):
            load_prereg(plan_path)
        shard = next(s for s in plan["shards"] if any(u.startswith("e1-") for u in s["units"]))
        out = self.tmp / "S"
        code, stdout, stderr = call_main(lab_shard, ["run", "--plan", str(plan_path), "--shard", shard["shard"],
                                                     "--out", str(out), "--provider", "fake"])
        self.assertEqual(code, 1)
        records = {p.parent.name: _json(p) for p in out.glob("units/*/unit.json")}
        for uid, record in records.items():
            if uid.startswith("e1-"):
                self.assertEqual((record["status"], record["status_reason"], record["argv"]),
                                 ("failed", PREREG_MISSING, []), uid)
            else:
                self.assertEqual(record["status"], "ok", uid)
        self.assertIsNone(_json(out / "provenance.json")["prereg"])
        self.assertFalse(any(p.parts[-2] == "e1" for p in out.glob("runs/*/*")))
        lab_shard.seal(out, shard["shard"], {"run": "failure"}, str(plan_path))
        shards = self.tmp / "shards"
        shards.mkdir()
        shutil.move(str(out), shards / shard["shard"])
        code, _, stderr = call_main(lab_aggregate, ["--plan", str(plan_path), "--provision", str(self.tmp / "none"),
                                                    "--shards", str(shards), "--manifest", str(MANIFEST_TEST),
                                                    "--out", str(self.tmp / "R")])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(_json(self.tmp / "R" / "report.json")["e1"]["reason"], PREREG_MISSING)

    def test_a_plant_that_does_not_fit_is_a_plan_error(self) -> None:
        plan_path = self.plan_only(_request(x1={**X1_BLOCK, "weeks": 40, "eval_to": 39}))
        code, stdout, stderr = run_prereg(plan_path)
        self.assertEqual(code, 2)
        self.assertEqual(stderr.splitlines()[0],
                         "error: $.experiments.x1.plant: the plant does not fit the preregistered world")
        self.assertIn("::error file=", stdout)
        error = _json(plan_path.parent / "plan-error.json")
        self.assertEqual((error["source"], error["path"], error["problem"]),
                         ("prereg", "$.experiments.x1.plant", "the plant does not fit the preregistered world"))
        self.assertFalse((plan_path.parent / "prereg" / "prereg.json").exists())
        from lab.summary import render_plan
        md, _ = render_plan(plan_path.parent)
        self.assertIn("## Lab plan refused", md)
        self.assertIn("`prereg`", md)

    def test_too_few_rehearsal_candidates(self) -> None:
        # the claims_integrity smoke world detects about a dozen candidates per seed, fewer than the 20 required
        block = {**E2_BLOCK, "models": ["fake-a"], "pack": "claims_integrity", "plant": "plant_smoke",
                 "eval_from": 26, "top_n": 20, "min_candidates": 20}
        plan_path = self.plan_only(_request(e2=block))
        code, _, stderr = run_prereg(plan_path)
        self.assertEqual(code, 2, stderr)
        self.assertTrue(stderr.startswith("error: $.experiments.e2: the model-free rehearsal refused"), stderr)
        self.assertEqual(_json(plan_path.parent / "plan-error.json")["path"], "$.experiments.e2")
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith("lab-rehearsal-")], [])

    def test_existing_prereg_and_unreadable_plan_are_usage_errors(self) -> None:
        plan_path = self.plan_only(_request(e3=plumbing_min()["experiments"]["e3"]))
        (plan_path.parent / "prereg").mkdir()
        code, _, stderr = run_prereg(plan_path)
        self.assertEqual(code, 2)
        self.assertFalse((plan_path.parent / "plan-error.json").exists())
        code, _, _ = run_prereg(self.tmp / "missing" / "plan.json")
        self.assertEqual(code, 2)
        self.assertFalse((self.tmp / "missing").exists())

    def test_a_plan_without_prereg_units_writes_nulls(self) -> None:
        plan_path = self.plan_only(_request(e3=plumbing_min()["experiments"]["e3"]))
        code, stdout, stderr = run_prereg(plan_path)
        self.assertEqual((code, stdout), (0, "prereg: e1 no x1 no e2 no\n"), stderr)
        manifest = load_prereg(plan_path).manifest
        self.assertEqual((manifest["e1"], manifest["x1"], manifest["e2"], manifest["files"]), (None, None, None, {}))

    def test_rehearsal_counts_equal_the_ledger_rows(self) -> None:
        plan, plan_path = make_plan(self.tmp, _request(e2={**E2_BLOCK, "models": ["fake-a"]}))
        runs = self.tmp / "again"
        unit = next(u for u in plan["units"] if u["experiment"] == "e2")
        code, _, stderr = call_main(e2_pushdown, [
            "run", f"--x1-prereg={plan_path.parent / 'prereg' / 'x1' / 'e2' / 'prereg.json'}",
            f"--plant={LAB_ROOT / unit['params']['plant_path']}", "--run-id=rehearsal", "--top-n=5",
            "--min-candidates=5", "--allow-external-raw=synthetic", "--data-label=synthetic",
            "--deadline-seconds=3600", "--bootstrap-b=1000", "--bootstrap-seed=1", f"--runs-dir={runs}"])
        self.assertEqual(code, 0, stderr)
        run = runs / "e2" / "rehearsal"
        doc = lab_prereg.rehearsal_doc(run, 1.0)
        site_rows = [r for p in run.glob("work/seed-*/edge/site-*.ledger.jsonl") for r in read_ledger(p)]
        central_rows = read_ledger(run / "central.ledger.jsonl")
        self.assertEqual(doc["calls"]["judge_record"],
                         sum(1 for r in site_rows if r["task"] == "judge_record" and r["attempt"] in (0, 1)))
        for task in ("judge_candidate_raw", "judge_candidate_allowed"):
            self.assertEqual(doc["calls"][task], sum(1 for r in central_rows if r["task"] == task))
        manifest = load_prereg(plan_path).manifest["e2"]["rehearsal"]
        self.assertEqual({k: manifest[k] for k in ("calls", "candidates", "central_raw_records",
                                                   "central_allowed_records", "worst", "e2_content_hash")},
                         {k: doc[k] for k in ("calls", "candidates", "central_raw_records", "central_allowed_records",
                                              "worst", "e2_content_hash")})
        items = _json(run / "e2.json")["items"]
        top = max(i["central_raw_records"] for i in items)
        self.assertEqual(doc["central_raw_records"]["max"], top)
        worst = min((i for i in items if i["central_raw_records"] == top), key=lambda i: (i["seed"], i["key"]))
        self.assertEqual(doc["worst"], {"seed": worst["seed"], "key": worst["key"]})


# --------------------------------------------------------------------------------------------------- plan

class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-g5plan-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))

    def test_units_and_params(self) -> None:
        plan = _json(_Shared.get().out / "plan" / "plan.json")
        by_id = {u["unit"]: u for u in plan["units"]}
        e1 = [u for u in plan["units"] if u["experiment"] == "e1"]
        self.assertEqual([u["unit"] for u in e1], [f"e1-{m}-r{k}" for m in ("fake-a", "fake-b") for k in (1, 2, 3)])
        self.assertEqual(by_id["e1-fake-a-r2"]["params"],
                         {"pack": "device_quality", "labels": {"source": "generator", "pack": "device_quality",
                                                               "n": 40, "seed": 1},
                          "endpoint": "fake-a", "reference": "fake-b", "repeat": 2, "runs": 3, "margin_points": 5,
                          "seed": 1, "bootstrap_b": 1000})
        self.assertEqual(by_id["e1-fake-a-r2"]["seeds"], [1])
        e2 = by_id["e2-fake-a"]
        self.assertEqual(e2["params"], {
            "pack": "device_quality", "plant": "plant_e2_smoke",
            "plant_path": "mycelic/collective/packs/data/device_quality/fixtures/plant_e2_smoke.json", "sites": 6,
            "seeds": [1], "seed": 1, "weeks": 52, "eval_from": 20, "eval_to": 51, "grace_weeks": 4,
            "tie_salt": "lab-e2", "detector_author": "mycelic engineering", "top_n": 5, "min_candidates": 5,
            "central": "self", "bootstrap_b": 1000, "bootstrap_seed": 1, "central_context_tokens": 32768})
        x1 = by_id["x1"]
        self.assertEqual((x1["model"], x1["kind"], x1["seeds"]), (None, "none", [1]))
        self.assertEqual(sorted(x1["params"]), sorted(set(e2["params"]) - {"top_n", "min_candidates", "central",
                                                                           "central_context_tokens"}))
        self.assertEqual(x1["params"]["tie_salt"], "lab-x1")
        openfda = by_id["openfda"]
        self.assertEqual((openfda["model"], openfda["kind"], openfda["seeds"], openfda["env"]), (None, "none", [], []))
        self.assertEqual(openfda["params"]["requests_estimate"], 12)
        self.assertEqual(openfda["params"]["recalling_firms"], ["ACME Devices"])
        self.assertTrue(all(u["needs_secret"] is False and u["env"] == [] for u in plan["units"]))

    def test_placement_by_serving_class(self) -> None:
        self.assertEqual([serving_class(e) for e in ("e1", "e2", "e3", "g0", "sim", "x1", "openfda")],
                         ["quality", "e2", "e3", "quality", "quality", "quality", "quality"])
        plan = _json(_Shared.get().out / "plan" / "plan.json")
        by_id = {u["unit"]: u for u in plan["units"]}
        rank = {"quality": 0, "e2": 1, "e3": 2}
        for shard in plan["shards"]:
            ranks = [rank[serving_class(by_id[u]["experiment"])] for u in shard["units"]]
            self.assertEqual(ranks, sorted(ranks), shard["shard"])
        self.assertEqual([(s["shard"], s["units"]) for s in plan["shards"]], [
            ("s001-fake-a", ["sim-fake-a-s1", "e1-fake-a-r1", "e1-fake-a-r2", "e1-fake-a-r3", "e2-fake-a",
                             "e3-fake-a"]),
            ("s002-fake-b", ["g0-fake-b", "e1-fake-b-r1", "e1-fake-b-r2", "e1-fake-b-r3", "e3-fake-b"]),
            ("s003-none", ["openfda", "x1"])])

    def test_plan_is_byte_deterministic_and_holds_no_prereg(self) -> None:
        for name in ("a", "b"):
            code, _, stderr = run_plan(["--request", str(ALL_EXPERIMENTS), "--manifest", str(LAB_MANIFEST), "--out",
                                        str(self.tmp / name)])
            self.assertEqual(code, 0, stderr)
        data = (self.tmp / "a" / "plan.json").read_bytes()
        self.assertEqual(data, (self.tmp / "b" / "plan.json").read_bytes())
        self.assertEqual(data, (_Shared.get().out / "plan" / "plan.json").read_bytes())
        self.assertNotIn(b"rehearsal", data)

    def test_a_none_shard_needs_no_prepare_under_a_real_provider(self) -> None:
        request = plumbing_min()
        request["provider"] = json.loads(MANIFEST_TEST.read_text(encoding="utf-8"))["server"]["program"]
        request["models"] = [MODEL_KEY]
        request["experiments"] = {"x1": X1_BLOCK}
        plan, plan_path = make_plan(self.tmp, request)
        self.assertEqual([(s["shard"], s["kind"]) for s in plan["shards"]], [("s001-none", "none")])
        self.assertEqual(plan["result_class"], "real")
        out = self.tmp / "S"
        code, stdout, stderr = call_main(lab_shard, ["run", "--plan", str(plan_path), "--shard", "s001-none",
                                                     "--out", str(out)])
        self.assertEqual(code, 0, stderr)
        record = _json(out / "units" / "x1" / "unit.json")
        self.assertEqual((record["status"], record["measurement_class"]), ("ok", "no-model"))
        provenance = _json(out / "provenance.json")
        self.assertEqual((provenance["server"], provenance["model"], provenance["result_class"]),
                         ({"kind": "none"}, None, "real"))
        self.assertEqual(units.display_class(record, provenance), "no-model")

    def test_bad_openfda_base_url_is_a_usage_error(self) -> None:
        plan_path = _Shared.get().out / "plan" / "plan.json"
        for url in ("ftp://x", "http://a b", "127.0.0.1:9"):
            code, _, stderr = call_main(lab_shard, ["run", "--plan", str(plan_path), "--shard", "s003-none",
                                                    "--out", str(self.tmp / "never"), "--provider", "fake",
                                                    f"--openfda-base-url={url}"])
            self.assertEqual(code, 2, url)
            self.assertFalse((self.tmp / "never").exists())


# --------------------------------------------------------------------------------------------------- responder, guards

class ResponderTests(unittest.TestCase):
    def body(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        compiled = compile_schema(e2_pushdown.CENTRAL_SCHEMA)
        return {"response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": compiled.full}},
                "messages": render_messages(e2_pushdown.central_task(name), payload, compiled)}

    def test_central_scores(self) -> None:
        responder = Responder(None)
        for name in ("judge_candidate_raw", "judge_candidate_allowed"):
            payloads = [{"question": {"entity_id": f"SD-{i}"}, "records": [{"text": "t" * i}]} for i in range(12)]
            scores = []
            for payload in payloads:
                body = self.body(name, payload)
                self.assertEqual(request_payload(body), payload)
                got = responder(body)
                self.assertEqual(sorted(got), ["score"])
                self.assertEqual(got, responder(body))
                expected = int(hashlib.sha256(canonical_bytes(request_payload(body))).hexdigest(), 16) % 101
                self.assertEqual(got["score"], expected)
                self.assertTrue(0 <= got["score"] <= 100)
                scores.append(got["score"])
            self.assertGreater(len(set(scores)), 1)


class GuardTests(unittest.TestCase):
    def test_no_forbidden_subcommand_in_lab(self) -> None:
        for path in sorted((LAB_ROOT / "lab").rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                with self.subTest(path=path.name, line=number):
                    self.assertNotIn("label-check", line)
                    self.assertFalse("n1_narratives" in line and "score" in line, line)

    def test_new_adapters_argv(self) -> None:
        plan = _json(_Shared.get().out / "plan" / "plan.json")
        modules = {"e1": "mycelic.collective.experiments.e1_extract",
                   "e2": "mycelic.collective.experiments.e2_pushdown", "x1": "mycelic.collective.evaluate.harness"}
        seen = set()
        for unit in plan["units"]:
            if unit["experiment"] not in modules:
                continue
            argv = units.build_argv(unit, Path("/out"), Path("/out/routing/x.json"), prereg=Path("/plan"))
            self.assertEqual(argv[:4], [sys.executable, "-m", modules[unit["experiment"]], "run"])
            for arg in argv[4:]:
                self.assertRegex(arg, ARG_RE)
            self.assertNotIn(DIRTY_OVERRIDE, " ".join(argv))
            seen.add(unit["experiment"])
            with self.assertRaises(ValueError):
                units.build_argv(unit, Path("/out"), Path("/out/routing/x.json"))
        self.assertEqual(seen, set(modules))
        e1 = next(u for u in plan["units"] if u["unit"] == "e1-fake-a-r1")
        argv = units.build_argv(e1, Path("/out"), Path("/out/routing/e1-fake-a-r1.json"), prereg=Path("/plan"))
        self.assertIn("--prereg=/plan/prereg/e1/prereg/prereg.json", argv)
        self.assertIn("--labels=/plan/prereg/e1/labels.jsonl", argv)
        self.assertIn("--endpoint=fake-a", argv)
        self.assertIn("--repeat=1", argv)

    def test_e2_central_context_flag(self) -> None:
        plan = _json(_Shared.get().out / "plan" / "plan.json")
        e2 = next(u for u in plan["units"] if u["experiment"] == "e2")
        self.assertEqual(e2["params"]["central_context_tokens"], 32768)
        for present in (True, False):
            with self.subTest(present=present), mock.patch.object(units, "E2_CENTRAL_CONTEXT", present):
                argv = units.build_argv(e2, Path("/out"), Path("/out/routing/x.json"), prereg=Path("/plan"))
                flags = [a for a in argv if a.startswith("--central-context-tokens")]
                self.assertEqual(flags, ["--central-context-tokens=32768"] if present else [])
                self.assertTrue(all(ARG_RE.fullmatch(a) for a in argv[4:]))
        changed = {**e2, "params": {**e2["params"], "central_context_tokens": 65536}}
        with mock.patch.object(units, "E2_CENTRAL_CONTEXT", True):
            argv = units.build_argv(changed, Path("/out"), Path("/out/routing/x.json"), prereg=Path("/plan"))
        self.assertIn("--central-context-tokens=65536", argv)

    def test_e2_central_context_follows_the_installed_harness(self) -> None:
        import argparse
        (run,) = [a.choices["run"] for a in e2_pushdown._parser()._actions
                  if isinstance(a, argparse._SubParsersAction)]
        options = {o for a in run._actions for o in a.option_strings}
        self.assertEqual(units.E2_CENTRAL_CONTEXT, "--central-context-tokens" in options)
        self.assertIn("--central-routing", options)

    def test_compare_argv_has_absolute_run_dirs(self) -> None:
        argv = lab_aggregate.e1_compare_argv(Path("p.json"), [Path("rel/b-2"), Path("rel/a-1")], Path("out"), False)
        i = argv.index("--run-dirs")
        dirs = argv[i + 1:i + 3]
        self.assertTrue(all(Path(d).is_absolute() for d in dirs))
        self.assertEqual([Path(d).name for d in dirs], ["a-1", "b-2"])
        rest = [a for j, a in enumerate(argv[4:], start=4) if j not in (i, i + 1, i + 2)]
        self.assertTrue(all(ARG_RE.fullmatch(a) for a in rest), rest)
        self.assertNotIn("--allow-incomplete", argv)
        self.assertEqual(lab_aggregate.e1_compare_argv(Path("p"), [], Path("o"), True)[-1], "--allow-incomplete")


if __name__ == "__main__":
    unittest.main()

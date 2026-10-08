"""E1, the pre-registered extraction harness, rehearsed end to end against local fake servers only.

Every command runs in-process through ``e1_extract.main``. The worktree's collective code is uncommitted while a
gate is built, so ``e1_extract.code_dirty`` is patched to report a clean tree except where a test is about the
dirty-code rule. Nothing here is a measurement: every server is a fake, so every result says ``measurement: false``.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import math
import random
import shutil
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from mycelic.collective import stats
from mycelic.collective.edge.extract import ModelExtractor
from mycelic.collective.experiments import e1_extract
from mycelic.collective.experiments.e1_extract import (claim_tuples, exact_flags, exact_summary, field_values,
                                                       format_gold_cell, micro_f1, parse_gold_cell, record_counts,
                                                       record_field_f1, verdicts)
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.jsonio import canonical_dumps, load_json_file, sha256_hex
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import BUILTIN_ROOT, load_pack
from tests.mycelic.test_collective_guards import BUILTIN_PACKS

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PREREG = ROOT / "docs" / "collective" / "examples" / "e1.prereg.example.json"
LOOPBACK = ("127.0.0.1", "::1", "localhost")
_real_create_connection = socket.create_connection


def _loopback_only(address, *args, **kwargs):
    if address[0] not in LOOPBACK:
        raise OSError(f"test guard: refusing a non-loopback connection to {address[0]}")
    return _real_create_connection(address, *args, **kwargs)


def setUpModule() -> None:
    socket.create_connection = _loopback_only


def tearDownModule() -> None:
    socket.create_connection = _real_create_connection


PACKS = {pid: load_pack(pid) for pid in BUILTIN_PACKS}
WORLDS = {pid: generate(PACKS[pid], seed=7, sites=6, weeks=52) for pid in PACKS}


def independent(pid: str, n: int) -> list[dict[str, Any]]:
    return [r for r in WORLDS[pid].records if r["origin_ref"] is None and r["narrative"]][:n]


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = e1_extract.main(argv)
    return code, out.getvalue(), err.getvalue()


def snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")} if root.exists() else set()


def responder(pid: str, records: list[dict[str, Any]], *, inject: bool = False) -> Callable[[dict], dict]:
    """Persona A answers with the generator's gold; with ``inject`` (persona B), records whose
    sha256(record_ref) mod 4 == 0 get a deterministic error (the first predicate's negation flipped, or the first
    entity-only claim dropped)."""
    by_text = {r["narrative"]: r for r in records}

    def respond(request: dict) -> dict:
        rec = by_text[request_payload(request)["text"]]
        items = [{"entity_type": None if g["entity_text"] is None else g["entity_type"],
                  "entity_text": g["entity_text"], "predicate": g["predicate"], "negated": g["negated"]}
                 for g in WORLDS[pid].gold[rec["record_ref"]]]
        if inject and int(hashlib.sha256(rec["record_ref"].encode()).hexdigest(), 16) % 4 == 0 and items:
            with_predicate = [i for i in items if i["predicate"] is not None]
            if with_predicate:
                with_predicate[0]["negated"] = not with_predicate[0]["negated"]
            else:
                items = items[1:]
        return {"claims": items}

    return respond


class E1Case(unittest.TestCase):
    pid = "device_quality"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.runs = self.dir / "runs"
        patcher = mock.patch.object(e1_extract, "code_dirty", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *argv: str, expect: int | None = 0) -> tuple[int, str, str]:
        code, out, err = run_cli([*argv])
        if expect is not None:
            self.assertEqual(code, expect, out + err)
        return code, out, err

    def server(self, persona: str = "valid", **kwargs) -> FakeOpenAIServer:
        srv = FakeOpenAIServer(persona, **kwargs).start()
        self.addCleanup(srv.stop)
        return srv

    def records_file(self, records: list[dict[str, Any]], name: str = "records-in.jsonl") -> Path:
        path = self.dir / name
        path.write_text("".join(canonical_dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def labels(self, pid: str, records: list[dict[str, Any]], name: str = "labels.jsonl") -> Path:
        """prepare (records source), a sheet filled from the generator's gold, then label-check."""
        run_id = f"prep-{name.split('.')[0]}"
        source = self.records_file(records, f"in-{name}")
        self.cli("prepare", "--pack", pid, "--source", "records", "--input", str(source), "--n", str(len(records)),
                 "--seed", "3", "--run-id", run_id, "--runs-dir", str(self.runs))
        prep = self.runs / "e1" / run_id
        header, body = e1_extract.read_sheet(prep / "sheet.csv")
        rows = [header, *body]
        gold_col, ref_col = rows[0].index("gold_claims"), rows[0].index("record_ref")
        for row in rows[1:]:
            row[gold_col] = format_gold_cell(WORLDS[pid].gold[row[ref_col]])
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        sheet = self.dir / f"sheet-{name}.csv"
        sheet.write_text(buf.getvalue(), encoding="utf-8")
        out = self.dir / name
        self.cli("label-check", "--pack", pid, "--records", str(prep / "records.jsonl"), "--sheet", str(sheet),
                 "--out", str(out))
        return out

    def routing(self, endpoints: dict[str, dict[str, Any]], name: str = "routing.json") -> Path:
        path = self.dir / name
        path.write_text(json.dumps({"schema_version": 1, "endpoints": endpoints, "routes": {}}), encoding="utf-8")
        return path

    @staticmethod
    def oc(srv: FakeOpenAIServer, boundary: str = "site:a", model: str = "tag", **extra) -> dict[str, Any]:
        return {"provider": "openai_compat", "boundary": boundary, "base_url": srv.base_url, "model": model, **extra}

    def prereg(self, labels: Path, routing: Path, *extra: str, pack: str | None = None, run_id: str = "pre",
               endpoints: tuple[str, ...] = ("model-a", "model-b"), reference: str = "model-a",
               data_label: str = "synthetic", expect: int | None = 0) -> tuple[Path, str]:
        args = ["prereg", "--pack", pack or self.pid, "--labels", str(labels), "--routing", str(routing)]
        for e in endpoints:
            args += ["--endpoint", e]
        args += ["--reference", reference, "--margin", "5", "--runs", "3", "--seed", "11", "--bootstrap-b", "1000",
                 "--data-label", data_label, "--boundary", "site:a", "--run-id", run_id, "--runs-dir",
                 str(self.runs), *extra]
        _, _, err = self.cli(*args, expect=expect)
        return self.runs / "e1" / run_id / "prereg.json", err

    def run_one(self, prereg: Path, labels: Path, routing: Path, endpoint: str, repeat: int, *extra: str,
                run_id: str | None = None, expect: int | None = 0) -> tuple[Path, str]:
        run_id = run_id or f"{endpoint}-{repeat}"
        _, out, err = self.cli("run", "--prereg", str(prereg), "--labels", str(labels), "--routing", str(routing),
                               "--endpoint", endpoint, "--repeat", str(repeat), "--run-id", run_id, "--runs-dir",
                               str(self.runs), *extra, expect=expect)
        return self.runs / "e1" / run_id, err

    def compare(self, prereg: Path, dirs: list[Path], *extra: str, run_id: str = "cmp",
                expect: int | None = 0) -> tuple[Path, str]:
        _, _, err = self.cli("compare", "--prereg", str(prereg), "--run-dirs", *map(str, dirs), "--run-id", run_id,
                             "--runs-dir", str(self.runs), *extra, expect=expect)
        return self.runs / "e1" / run_id / "e1.json", err

    def flow(self, n: int = 12, *, pack: str | None = None, served_b: str | None = None) -> dict[str, Any]:
        """Labels, two fake servers (A gold, B gold with injected errors), a routing file and a prereg."""
        records = independent(self.pid, n)
        labels = self.labels(self.pid, records)
        a = self.server(responder=responder(self.pid, records))
        b = self.server(responder=responder(self.pid, records, inject=True), served_model=served_b)
        routing = self.routing({"model-a": self.oc(a, model="tag-a"), "model-b": self.oc(b, model="tag-b")})
        prereg, _ = self.prereg(labels, routing, pack=pack)
        return {"records": records, "labels": labels, "a": a, "b": b, "routing": routing, "prereg": prereg}

    def all_runs(self, env: dict[str, Any], *extra: str) -> list[Path]:
        dirs = []
        for endpoint in ("model-a", "model-b"):
            for k in (1, 2, 3):
                d, _ = self.run_one(env["prereg"], env["labels"], env["routing"], endpoint, k, *extra)
                dirs.append(d)
        return dirs


# =================================================================================================== metrics

def claim(t: str, i: str, p: str | None = None, neg: bool = False) -> dict[str, Any]:
    return {"entity_type": t, "entity_id": i, "predicate": p, "negated": neg}


class E1MetricTests(unittest.TestCase):
    def test_field_values(self) -> None:
        self.assertEqual(field_values([claim("product", "SD-9", "crack"), claim("lot", "L1", "leak", True),
                                       claim("lot", "L1")]),
                         {("product", "SD-9"), ("lot", "L1"), ("predicate", "crack"), ("predicate", "not:leak")})

    def test_micro_f1_and_undefined(self) -> None:
        self.assertEqual(micro_f1([(1, 0, 1), (2, 1, 0)]), {"value": 0.75, "tp": 3, "fp": 1, "fn": 1})
        self.assertIsNone(micro_f1([(0, 0, 0), (0, 0, 0)])["value"])
        self.assertIsNone(micro_f1([])["value"])

    def test_per_record_field_f1(self) -> None:
        self.assertEqual(record_field_f1([], []), 1.0)
        self.assertEqual(record_field_f1([claim("lot", "L1")], []), 0.0)
        self.assertEqual(record_field_f1([], [claim("lot", "L1")]), 0.0)
        self.assertEqual(record_field_f1([claim("lot", "L1", "leak")], [claim("lot", "L1", "crack")]), 0.5)

    def test_claim_tuples_and_partial_metrics(self) -> None:
        predicted = [claim("product", "SD-9", "crack"), claim("lot", "L1"), claim("lot", "L2", "leak", True)]
        gold = [claim("product", "SD-9", "crack"), claim("lot", "L2", "leak"), claim("supplier", "V1")]
        self.assertEqual(claim_tuples(predicted), {("product", "SD-9", "crack", False), ("lot", "L2", "leak", True)})
        counts = record_counts(predicted, gold)
        self.assertEqual(counts["claim"], (1, 1, 1))
        self.assertEqual(counts["entity"], (2, 1, 1))
        self.assertEqual(counts["predicate"], (1, 1, 1))
        self.assertEqual(counts["field"], (3, 2, 2))

    def test_exact_match_per_flagged_type(self) -> None:
        flags = [exact_flags([claim("lot", "L1"), claim("lot", "L2")], [claim("lot", "L1"), claim("lot", "L2")],
                             ["lot", "supplier"]),
                 exact_flags([claim("lot", "L1")], [claim("lot", "L1"), claim("lot", "L2")], ["lot", "supplier"]),
                 exact_flags([claim("supplier", "V1")], [], ["lot", "supplier"])]
        self.assertEqual(flags[0], {"lot": True, "supplier": None})
        self.assertEqual(flags[1], {"lot": False, "supplier": None})
        self.assertEqual(flags[2], {"lot": None, "supplier": None})
        summary = exact_summary([[f] for f in flags], ["lot", "supplier"], ci=True)
        lo, hi = stats.wilson(1, 2)
        self.assertEqual(summary["lot"], {"n": 2, "matches": 1, "rate": 0.5, "ci_low": lo, "ci_high": hi})
        self.assertEqual(summary["supplier"], {"n": 0, "matches": 0, "rate": None, "ci_low": None, "ci_high": None})

    def test_exact_match_counts_records_not_repeats(self) -> None:
        """Three runs of four records: n is 4 records, not 12 (record, run) pairs, so the Wilson interval is not
        narrowed by repeats; a record matches only when it matches in every run."""
        hit, miss = {"lot": True}, {"lot": False}
        records = [[hit, hit, hit], [hit, hit, hit], [hit, miss, hit], [miss, miss, miss]]
        summary = exact_summary(records, ["lot"], ci=True)
        lo, hi = stats.wilson(2, 4)
        self.assertEqual(summary["lot"], {"n": 4, "matches": 2, "rate": 0.5, "ci_low": lo, "ci_high": hi})
        same = exact_summary([[hit] * 3] * 3 + [[miss] * 3], ["lot"], ci=True)["lot"]
        once = exact_summary([[hit]] * 3 + [[miss]], ["lot"], ci=True)["lot"]
        self.assertEqual(same, once)            # identical repeats add no precision

    def test_bootstrap_is_deterministic_for_a_seed(self) -> None:
        counts = [(1, 0, 0), (0, 1, 1), (3, 0, 1)]
        self.assertEqual(stats.bootstrap_f1(counts, B=1000, seed="e1:1:a:field_f1"),
                         stats.bootstrap_f1(counts, B=1000, seed="e1:1:a:field_f1"))

    def test_margin_is_stored_as_a_fraction(self) -> None:
        self.assertEqual(e1_extract.parse_margin("5"), 0.05)
        self.assertEqual(e1_extract.parse_margin("0.05"), 0.05)
        self.assertEqual(e1_extract.parse_margin("1"), 0.01)
        self.assertEqual(e1_extract.parse_margin("20"), 0.2)
        for bad in ("0", "25", "-1", "abc", "nan", "inf", "", "1e999"):
            with self.subTest(margin=bad), self.assertRaises(e1_extract.UsageError):
                e1_extract.parse_margin(bad)


class VerdictFunctionTests(unittest.TestCase):
    def test_the_verdict_table(self) -> None:
        just_above = math.nextafter(-0.05, 0.0)
        just_below_kill = math.nextafter(0.80, 0.0)
        table = [
            ((-0.05, 0.05, 0.80), (False, False)),
            ((just_above, 0.05, 0.80), (True, False)),
            ((-0.05, 0.05, just_below_kill), (False, True)),
            ((0.01, 0.05, 0.95), (True, False)),
            ((-0.2, 0.05, 0.5), (False, True)),
            ((None, 0.05, 0.9), (None, False)),
            ((0.0, 0.05, None), (True, None)),
            ((None, 0.05, None), (None, None)),
        ]
        for (ci_low, margin, abs_f1), (non_inferior, kill) in table:
            with self.subTest(ci_low=ci_low, abs_f1=abs_f1):
                self.assertEqual(verdicts(ci_low, margin, abs_f1), {"non_inferior": non_inferior, "kill_flag": kill})


# =================================================================================================== prepare

def openfda_event(key: int, text: str | None, *, text_type: str = "Description of Event or Problem") -> dict:
    event = {"mdr_report_key": key, "date_received": "20240305", "device": [{"model_number": "SD-12",
                                                                               "lot_number": "L20045"}],
             "product_problems": ["Crack", "Not In The Value Map"]}
    if text is not None:
        event["mdr_text"] = [{"text_type_code": text_type, "text": text}]
    return event


class E1PrepareTests(E1Case):
    def openfda_cache(self, events: list[dict], data_label: str = "public", name: str = "cache") -> Path:
        cache = self.dir / name
        page = cache / "pages" / "device_event" / "AAA" / "page-0000000.json"
        page.parent.mkdir(parents=True)
        data = json.dumps({"meta": {"results": {"total": len(events)}}, "results": events}).encode()
        page.write_bytes(data)
        manifest = {"kind": "openfda_cache", "schema_version": 1, "dataset": "event", "data_label": data_label,
                    "query": {"product_codes": ["AAA"]}, "per_code": {"AAA": {"total": len(events)}},
                    "pages": [{"product_code": "AAA", "path": page.relative_to(cache).as_posix(),
                               "sha256": sha256_hex(data)}]}
        (cache / "manifest.json").write_text(json.dumps(manifest))
        return cache

    def test_openfda_source(self) -> None:
        events = [openfda_event(i, f"Narrative {i}: the housing cracked.") for i in range(1, 5)]
        events += [openfda_event(10, None), openfda_event(11, "Other text", text_type="Additional Narrative")]
        cache = self.openfda_cache(events)
        self.cli("prepare", "--pack", "device_quality", "--source", "openfda", "--cache", str(cache), "--n", "10",
                 "--seed", "1", "--run-id", "ofda", "--runs-dir", str(self.runs))
        out = self.runs / "e1" / "ofda"
        doc = load_json_file(out / "prepare.json")
        self.assertEqual((doc["kind"], doc["data_label"], doc["source"], doc["mapping"]),
                         ("e1_prepare", "public", "openfda", "mapping_openfda"))
        self.assertEqual((doc["n_requested"], doc["n_written"], doc["shortfall"], doc["eligible"]), (10, 4, 6, 4))
        self.assertEqual(doc["rejected"], {"missing_narrative": 2})
        self.assertEqual(doc["unmapped_code_values"], 4)
        self.assertIs(doc["measurement"], False)
        self.assertEqual(doc["records_sha256"], sha256_hex((out / "records.jsonl").read_bytes()))
        header = (out / "sheet.csv").read_text().splitlines()[0]
        self.assertEqual(header, "row,record_ref,language,received_date,codes,entities.lot,entities.product,"
                                 "narrative,gold_claims,labeller_note")
        records = [json.loads(line) for line in (out / "records.jsonl").read_text().splitlines()]
        self.assertEqual({r["site"] for r in records}, {"public"})
        self.assertEqual({r["synthetic"] for r in records}, {False})
        local = self.openfda_cache(events[:2], data_label="synthetic", name="local-cache")
        self.cli("prepare", "--pack", "device_quality", "--source", "openfda", "--cache", str(local), "--n", "2",
                 "--seed", "1", "--run-id", "local", "--runs-dir", str(self.runs))
        doc = load_json_file(self.runs / "e1" / "local" / "prepare.json")
        self.assertEqual((doc["data_label"], doc["n_written"]), ("synthetic", 2))
        self.assertIn('"synthetic":true', (self.runs / "e1" / "local" / "records.jsonl").read_text())

    def test_jsonl_and_records_sources(self) -> None:
        rows = [{"complaint_no": f"C-{i}", "plant": "plant-ashvale", "received_on": "2024-03-04", "lang": "en",
                 "problem_codes": [], "model": "SD-9", "description": f"Unit {i} leaked.", "secret": "CANARY"}
                for i in range(5)]
        path = self.dir / "partner.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.cli("prepare", "--pack", "device_quality", "--source", "jsonl", "--input", str(path), "--site", "x",
                 "--n", "3", "--seed", "1", "--run-id", "partner", "--runs-dir", str(self.runs))
        doc = load_json_file(self.runs / "e1" / "partner" / "prepare.json")
        self.assertEqual((doc["data_label"], doc["n_written"]), ("partner", 3))
        self.assertNotIn("CANARY", (self.runs / "e1" / "partner" / "records.jsonl").read_text())
        records = independent("claims_integrity", 8)
        self.cli("prepare", "--pack", "claims_integrity", "--source", "records", "--input",
                 str(self.records_file(records)), "--n", "8", "--seed", "1", "--run-id", "rec", "--runs-dir",
                 str(self.runs))
        doc = load_json_file(self.runs / "e1" / "rec" / "prepare.json")
        self.assertEqual((doc["data_label"], doc["n_written"], doc["mapping"]), ("synthetic", 8, "mapping"))

    def test_the_sample_is_seeded(self) -> None:
        path = self.records_file(independent(self.pid, 30))
        outs = []
        for run_id, seed in (("s1", "4"), ("s2", "4"), ("s3", "5")):
            self.cli("prepare", "--pack", self.pid, "--source", "records", "--input", str(path), "--n", "10",
                     "--seed", seed, "--run-id", run_id, "--runs-dir", str(self.runs))
            outs.append((self.runs / "e1" / run_id / "records.jsonl").read_bytes())
        self.assertEqual(outs[0], outs[1])
        self.assertNotEqual(outs[0], outs[2])

    def test_dry_run_creates_nothing(self) -> None:
        before = snapshot(self.dir)
        _, out, _ = self.cli("prepare", "--pack", self.pid, "--source", "openfda", "--cache",
                             str(self.dir / "missing"), "--n", "10", "--seed", "1", "--run-id", "dry", "--runs-dir",
                             str(self.runs), "--dry-run")
        self.assertTrue(out.startswith("dry-run: e1_extract prepare"))
        self.assertIn("would need: openFDA cache", out)
        self.assertEqual(snapshot(self.dir), before)
        self.cli("prepare", "--pack", "claims_integrity", "--source", "openfda", "--cache", str(self.dir), "--n",
                 "10", "--seed", "1", "--run-id", "dry", "--runs-dir", str(self.runs), "--dry-run", expect=2)
        self.cli("prepare", "--pack", "no_such_pack", "--source", "records", "--input", "x", "--seed", "1",
                 "--run-id", "dry", "--runs-dir", str(self.runs), expect=2)
        self.assertEqual(snapshot(self.dir), before)


# =================================================================================================== labels

class E1LabelTests(E1Case):
    def test_grammar(self) -> None:
        items = parse_gold_cell("lot:L12345@crack; component:battery door@!leak;@overheat ;product:SD-9")
        self.assertEqual(items, [
            {"entity_type": "lot", "entity_text": "L12345", "predicate": "crack", "negated": False},
            {"entity_type": "component", "entity_text": "battery door", "predicate": "leak", "negated": True},
            {"entity_type": None, "entity_text": None, "predicate": "overheat", "negated": False},
            {"entity_type": "product", "entity_text": "SD-9", "predicate": None, "negated": False}])
        self.assertEqual(parse_gold_cell(" - "), [])
        for bad, problem in (("", "unlabelled"), ("   ", "unlabelled"), (None, "unlabelled"), ("lot:", "item 1"),
                             ("@", "item 1"), ("lot:L1@CRACK", "item 1"), ("a;;b", "item 1"), ("x y", "item 1"),
                             ("lot:L1@crack;", "item 2")):
            with self.subTest(cell=bad), self.assertRaisesRegex(ValueError, problem):
                parse_gold_cell(bad)

    def test_format_round_trip(self) -> None:
        for r in independent(self.pid, 60):
            gold = WORLDS[self.pid].gold[r["record_ref"]]
            parsed = parse_gold_cell(format_gold_cell(gold))
            self.assertEqual(parsed, [{"entity_type": None if g["entity_text"] is None else g["entity_type"],
                                       "entity_text": g["entity_text"], "predicate": g["predicate"],
                                       "negated": g["negated"]} for g in gold])
        with self.assertRaises(ValueError):
            format_gold_cell([{"entity_type": "lot", "entity_text": "L1;L2", "predicate": None, "negated": False}])

    def sheet(self, records: list[dict], cells: list[str], *, bom: bool = False, header: list[str] | None = None,
              extra: list[list[str]] | None = None) -> Path:
        pack = PACKS[self.pid]
        expected = e1_extract._sheet_header(pack, "mapping")
        rows = [header or expected]
        for i, (r, cell) in enumerate(zip(records, cells), start=1):
            row = [str(i), r["record_ref"], r["language"] or "", r["received_date"], "", *[""] * 4, r["narrative"],
                   cell, ""]
            rows.append(row)
        rows += extra or []
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n").writerows(rows)
        path = self.dir / "sheet.csv"
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + buf.getvalue().encode("utf-8"))
        return path

    def records_jsonl(self, records: list[dict]) -> Path:
        path = self.dir / "records.jsonl"
        path.write_text("".join(canonical_dumps(r) + "\n" for r in records))
        return path

    def base_records(self) -> list[dict]:
        out = []
        for i in range(6):
            r = json.loads(json.dumps(independent(self.pid, 6)[i]))
            r["entities"]["product"] = ["SD-9"] if i != 5 else []
            out.append(r)
        return out

    def test_every_problem_is_listed_by_row(self) -> None:
        records = self.base_records()
        cells = ["", "lot:L1234@crack", "widget:x", "@nope", "-", "@crack"]
        dup = [["7", records[0]["record_ref"], "", "", "", "", "", "", "", "", "-", ""]]
        sheet = self.sheet(records, cells, extra=dup)
        out = self.dir / "labels.jsonl"
        _, _, err = self.cli("label-check", "--pack", self.pid, "--records", str(self.records_jsonl(records)),
                             "--sheet", str(sheet), "--out", str(out), expect=2)
        refs = [r["record_ref"] for r in records]
        for line in (f"row 1 ({refs[0]}): unlabelled", f"row 2 ({refs[1]}): not canonical: lot:L1234",
                     f"row 3 ({refs[2]}): unknown entity type widget", f"row 4 ({refs[3]}): unknown predicate nope",
                     f"row 6 ({refs[5]}): no primary entity for @crack", f"row 7 ({refs[0]}): duplicate record_ref"):
            self.assertIn(line, err)
        self.assertNotIn(f"({refs[4]})", err)
        self.assertFalse(out.exists())

    def test_success_with_a_bom_sorted_canonical_output_and_refusals(self) -> None:
        records = self.base_records()[:3]
        cells = ["@crack", "product:sd-9@!leak; product:SD-9@!leak", "-"]
        sheet = self.sheet(list(reversed(records)), list(reversed(cells)), bom=True)
        out = self.dir / "labels.jsonl"
        _, stdout, _ = self.cli("label-check", "--pack", self.pid, "--records", str(self.records_jsonl(records)),
                                "--sheet", str(sheet), "--out", str(out))
        data = out.read_bytes()
        self.assertIn(sha256_hex(data), stdout)
        lines = [json.loads(line) for line in data.decode().splitlines()]
        self.assertEqual([line["record"]["record_ref"] for line in lines], sorted(r["record_ref"] for r in records))
        self.assertEqual(data.decode(), "".join(canonical_dumps(line) + "\n" for line in lines))
        by_ref = {line["record"]["record_ref"]: line["gold"] for line in lines}
        self.assertEqual(by_ref[records[0]["record_ref"]], [claim("product", "SD-9", "crack")])
        self.assertEqual(by_ref[records[1]["record_ref"]], [claim("product", "SD-9", "leak", True)])
        self.assertEqual(by_ref[records[2]["record_ref"]], [])
        self.cli("label-check", "--pack", self.pid, "--records", str(self.records_jsonl(records)), "--sheet",
                 str(sheet), "--out", str(out), expect=2)

    def test_a_narrative_longer_than_the_csv_field_limit_is_labelled(self) -> None:
        records = [dict(r) for r in independent(self.pid, 3)]
        records[0]["narrative"] += " " + "filler words here. " * 8000
        self.assertGreater(len(records[0]["narrative"]), csv.field_size_limit())
        limit = csv.field_size_limit()
        out = self.labels(self.pid, records, name="labels-long.jsonl")
        self.assertEqual(csv.field_size_limit(), limit)
        lines = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertIn(records[0]["narrative"], [line["record"]["narrative"] for line in lines])

    def test_header_and_record_sets_are_checked(self) -> None:
        records = self.base_records()[:3]
        bad_header = e1_extract._sheet_header(PACKS[self.pid], "mapping")
        bad_header[-2] = "gold"
        sheet = self.sheet(records, ["-", "-", "-"], header=bad_header)
        _, _, err = self.cli("label-check", "--pack", self.pid, "--records", str(self.records_jsonl(records)),
                             "--sheet", str(sheet), "--out", str(self.dir / "l.jsonl"), expect=2)
        self.assertIn("header", err)
        sheet = self.sheet(records[:2], ["-", "-"], extra=[["3", "not-a-record", "", "", "", "", "", "", "", "",
                                                            "-", ""]])
        _, _, err = self.cli("label-check", "--pack", self.pid, "--records", str(self.records_jsonl(records)),
                             "--sheet", str(sheet), "--out", str(self.dir / "l.jsonl"), expect=2)
        self.assertIn("(not-a-record): record_ref not in the records file", err)
        self.assertIn(f"({records[2]['record_ref']}): record missing from the sheet", err)


# =================================================================================================== prereg, pins

class E1PreregTests(E1Case):
    def test_prereg_keys_match_the_example(self) -> None:
        env = self.flow(6)
        doc = load_json_file(env["prereg"])
        example = json.loads(EXAMPLE_PREREG.read_text())
        self.assertEqual(set(doc), set(example))
        self.assertEqual(set(doc), set(e1_extract.PREREG_KEYS))
        for key in ("pack", "labels"):
            self.assertEqual(set(doc[key]), set(example[key]))
        self.assertEqual(set(doc["endpoints"][0]), set(example["endpoints"][0]))
        self.assertEqual((doc["margin"], doc["kill_below"], doc["underpowered_below"]), (0.05, 0.8, 600))
        self.assertEqual(doc["labels"]["sha256"], sha256_hex(env["labels"].read_bytes()))
        self.assertEqual(doc["code_hash"], e1_extract.e1_code_hash())
        self.assertIn("mycelic/collective/edge/extract.py", doc["code_files"])
        self.assertIn("mycelic/collective/inference/runtime.py", doc["code_files"])
        self.assertEqual([e["name"] for e in doc["endpoints"]], ["model-a", "model-b"])
        self.assertEqual(doc["endpoints"][1]["model"], "tag-b")

    def test_argument_errors_exit_2_and_create_nothing(self) -> None:
        env = self.flow(4)
        before = snapshot(self.runs)
        cases = [
            ("--margin", "0"), ("--margin", "25"), ("--margin", "-1"), ("--runs", "2"), ("--bootstrap-b", "999"),
        ]
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                args = ["prereg", "--pack", self.pid, "--labels", str(env["labels"]), "--routing", str(env["routing"]),
                        "--endpoint", "model-a", "--endpoint", "model-b", "--reference", "model-a", "--margin", "5",
                        "--runs", "3", "--seed", "1", "--bootstrap-b", "1000", "--data-label", "synthetic",
                        "--boundary", "site:a", "--run-id", "bad", "--runs-dir", str(self.runs)]
                args[args.index(flag) + 1] = value
                self.cli(*args, expect=2)
        _, err = self.prereg(env["labels"], env["routing"], run_id="bad", reference="model-c", expect=2)
        self.assertIn("reference not among the evaluated endpoints", err)
        _, err = self.prereg(env["labels"], env["routing"], run_id="bad", endpoints=("model-a", "model-a"),
                             expect=2)
        self.assertIn("2 distinct", err)
        _, err = self.prereg(env["labels"], env["routing"], run_id="bad", endpoints=("model-a", "model-x"),
                             expect=2)
        self.assertIn("not in the routing file", err)
        fake = self.routing({"model-a": self.oc(env["a"]), "model-b": {"provider": "fake", "boundary": "site:a"}},
                            name="fake.json")
        _, err = self.prereg(env["labels"], fake, run_id="bad", expect=2)
        self.assertIn("fake provider", err)
        _, err = self.prereg(env["labels"], env["routing"], run_id="bad", data_label="public", expect=2)
        self.assertIn("synthetic exactly when", err)
        _, err = self.prereg(env["labels"], env["routing"], run_id="pre", expect=2)
        self.assertIn("already exists", err)
        self.assertEqual(snapshot(self.runs), before)

    def test_pinned_values_are_enforced_by_run_and_compare(self) -> None:
        env = self.flow(6)
        dirs = self.all_runs(env)
        e1, _ = self.compare(env["prereg"], dirs)
        self.assertTrue(e1.is_file())
        before = snapshot(self.runs)

        def refused(*, run_args: dict | None = None, compare_args: tuple = (), prereg: Path | None = None,
                    labels: Path | None = None, routing: Path | None = None, dirs_: list | None = None,
                    needle: str) -> None:
            if run_args is not None:
                _, err = self.run_one(prereg or env["prereg"], labels or env["labels"], routing or env["routing"],
                                      "model-a", 1, run_id="refused", expect=2)
                self.assertIn(needle, err)
            if dirs_ is not None:
                _, err = self.compare(prereg or env["prereg"], dirs_, *compare_args, run_id="refused", expect=2)
                self.assertIn(needle, err)
            self.assertEqual(snapshot(self.runs), before)

        refused(run_args={}, dirs_=dirs, prereg=self.dir / "missing.json", needle="missing")
        edited_labels = self.dir / "labels-edited.jsonl"
        edited_labels.write_bytes(env["labels"].read_bytes() + b"\n")
        refused(run_args={}, labels=edited_labels, needle="labels sha256 differs")
        refused(dirs_=dirs, compare_args=("--margin", "3"), needle="margin differs")
        refused(dirs_=dirs, compare_args=("--margin", "0.05", "--reference", "model-b"), needle="--reference differs")
        for field, value, needle in (("model", "other-tag", "model differs"),
                                     ("boundary", "site:b", "boundary differs"),
                                     ("response_format", "json_object", "response_format differs"),
                                     ("transport_schema", "reduced", "transport_schema differs"),
                                     # regression (audit r2): the thinking controls change what a model returns
                                     ("reasoning_effort", "none", "reasoning_effort differs"),
                                     ("chat_template_kwargs", {"enable_thinking": False},
                                      "chat_template_kwargs differs"),
                                     ("provider", "fake", "fake provider")):
            routing = json.loads(env["routing"].read_text())
            routing["endpoints"]["model-a"][field] = value
            if value == "fake":
                del routing["endpoints"]["model-a"]["base_url"]
            path = self.dir / f"routing-{field}.json"
            path.write_text(json.dumps(routing))
            with self.subTest(field=field):
                refused(run_args={}, routing=path, needle=needle)
        with mock.patch.object(e1_extract, "e1_code_hash", lambda: "0" * 64):
            refused(run_args={}, dirs_=dirs, needle="e1_code_hash differs")
        refused(dirs_=dirs + [dirs[0]], needle="appears twice")
        refused(dirs_=dirs[1:], needle="repeats must be exactly 1..3")
        tampered = self.dir / "prereg-tampered.json"
        doc = load_json_file(env["prereg"])
        doc["seed"] = 12
        tampered.write_text(canonical_dumps(doc) + "\n")
        refused(dirs_=dirs, prereg=tampered, needle="prereg_sha256")

    def test_a_changed_vocabulary_is_refused(self) -> None:
        copy = self.dir / "pack-copy"
        shutil.copytree(BUILTIN_ROOT / self.pid, copy)
        env = self.flow(6, pack=str(copy))
        d, _ = self.run_one(env["prereg"], env["labels"], env["routing"], "model-a", 1)
        vocab = json.loads((copy / "vocabulary.json").read_text())
        vocab["predicates"]["crack"]["lexicon"]["en"].append("hairline split")
        (copy / "vocabulary.json").write_text(json.dumps(vocab))
        before = snapshot(self.runs)
        _, err = self.run_one(env["prereg"], env["labels"], env["routing"], "model-a", 2, expect=2)
        self.assertIn("pack vocabulary_hash differs", err)
        _, err = self.compare(env["prereg"], [d], run_id="cmp2", expect=2)
        self.assertEqual(snapshot(self.runs), before)

    def test_dirty_code_needs_allow_dirty_which_is_stamped(self) -> None:
        env = self.flow(5)
        for state in (True, "unknown"):
            with self.subTest(state=state), mock.patch.object(e1_extract, "code_dirty", lambda: state):
                before = snapshot(self.runs)
                _, err = self.prereg(env["labels"], env["routing"], run_id=f"dirty-{state}", expect=2)
                self.assertIn("--allow-dirty", err)
                _, err = self.run_one(env["prereg"], env["labels"], env["routing"], "model-a", 1, expect=2)
                self.assertIn("--allow-dirty", err)
                self.assertEqual(snapshot(self.runs), before)
        with mock.patch.object(e1_extract, "code_dirty", lambda: True):
            dirs = self.all_runs(env, "--allow-dirty")
            self.compare(env["prereg"], dirs, expect=2)
            e1, _ = self.compare(env["prereg"], dirs, "--allow-dirty")
        run = load_json_file(dirs[0] / "run.json")
        self.assertEqual((run["allow_dirty"], run["code_dirty"]), (True, True))
        self.assertIs(load_json_file(e1)["allow_dirty"], True)

    def test_incomplete_runs_and_the_inner_join(self) -> None:
        env = self.flow(9)
        dirs = [self.run_one(env["prereg"], env["labels"], env["routing"], "model-a", k)[0] for k in (1, 2, 3)]
        real = ModelExtractor.extract
        for k in (1, 2, 3):
            calls = {"n": 0}

            def interrupt(self_, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] > 5:
                    raise KeyboardInterrupt
                return real(self_, *args, **kwargs)

            with mock.patch.object(ModelExtractor, "extract", interrupt):
                d, err = self.run_one(env["prereg"], env["labels"], env["routing"], "model-b", k, expect=130)
            run = load_json_file(d / "run.json")
            self.assertEqual((run["complete"], run["records_done"], run["records_total"]), (False, 5, 9))
            self.assertIn("interrupted", err)
            dirs.append(d)
        _, err = self.compare(env["prereg"], dirs, expect=2)
        self.assertIn("--allow-incomplete", err)
        e1, _ = self.compare(env["prereg"], dirs, "--allow-incomplete")
        doc = load_json_file(e1)
        self.assertIs(doc["allow_incomplete"], True)
        self.assertEqual(doc["paired"]["model-b"]["n"], 5)
        self.assertEqual(doc["paired"]["model-b"]["dropped"], {"only_model": 0, "only_reference": 4})

    def test_a_pre_registered_endpoint_without_runs_is_refused_unless_allowed(self) -> None:
        records = independent(self.pid, 6)
        labels = self.labels(self.pid, records)
        servers = {name: self.server(responder=responder(self.pid, records, inject=name != "model-a"))
                   for name in ("model-a", "model-b", "model-c")}
        routing = self.routing({name: self.oc(srv) for name, srv in servers.items()})
        prereg, _ = self.prereg(labels, routing, endpoints=("model-a", "model-b", "model-c"))
        dirs = [self.run_one(prereg, labels, routing, e, k)[0] for e in ("model-a", "model-b") for k in (1, 2, 3)]
        before = snapshot(self.runs)
        _, err = self.compare(prereg, dirs, expect=2)
        self.assertIn("pre-registered endpoints without runs: model-c", err)
        self.assertEqual(snapshot(self.runs), before)
        doc = load_json_file(self.compare(prereg, dirs, "--allow-incomplete")[0])
        self.assertEqual((doc["allow_incomplete"], doc["endpoints_without_runs"]), (True, ["model-c"]))
        self.assertEqual(sorted(doc["endpoints"]), ["model-a", "model-b"])
        full = load_json_file(self.compare(prereg, dirs + [self.run_one(prereg, labels, routing, "model-c", k)[0]
                                                           for k in (1, 2, 3)], run_id="cmp-all")[0])
        self.assertEqual((full["allow_incomplete"], full["endpoints_without_runs"]), (False, []))

    def test_edited_predictions_or_ledger_and_unfinished_runs_are_refused(self) -> None:
        env = self.flow(6)
        dirs = self.all_runs(env)
        for d in dirs:
            run = load_json_file(d / "run.json")
            self.assertEqual(run["predictions_sha256"], sha256_hex((d / "predictions.jsonl").read_bytes()))
            self.assertEqual(run["ledger_sha256"], sha256_hex((d / "ledger.jsonl").read_bytes()))
        before = snapshot(self.runs)

        def refused(path: Path, data: bytes, needle: str) -> None:
            original = path.read_bytes()
            path.write_bytes(data)
            try:
                _, err = self.compare(env["prereg"], dirs, "--allow-incomplete", run_id="refused", expect=2)
                self.assertIn(needle, err)
                self.assertEqual(snapshot(self.runs), before)
            finally:
                path.write_bytes(original)

        target = dirs[3]                                              # model-b, repeat 1
        lines = [json.loads(line) for line in (target / "predictions.jsonl").read_text().splitlines()]
        for line in lines:                                            # the reviewer's tamper: every record perfect
            line["counts"] = {k: [sum(v), 0, 0] for k, v in line["counts"].items()}
            line["field_f1"] = 1.0
        tampered = "".join(canonical_dumps(line) + "\n" for line in lines).encode()
        refused(target / "predictions.jsonl", tampered, "predictions.jsonl sha256 of run")
        ledger = (target / "ledger.jsonl").read_bytes()
        refused(target / "ledger.jsonl", ledger.splitlines(keepends=True)[0], "ledger.jsonl sha256 of run")
        run = load_json_file(target / "run.json")
        refused(target / "run.json", (canonical_dumps({**run, "finished_at": None}) + "\n").encode(),
                "never finished writing run.json")
        refused(target / "run.json", (canonical_dumps({**run, "repeat": "1"}) + "\n").encode(),
                "not a valid e1 run.json (repeat)")
        self.compare(env["prereg"], dirs)                             # restored files compare again

    def test_malformed_or_edited_preregs_exit_2_without_a_request(self) -> None:
        env = self.flow(4)
        dirs = self.all_runs(env)
        requests = (len(env["a"].chat_requests), len(env["b"].chat_requests))
        before = snapshot(self.runs)
        doc = load_json_file(env["prereg"])
        edits = [("runs", "3", "runs, seed or bootstrap_b"), ("endpoints", {}, "endpoints"),
                 ("pack", [], "pack"), ("margin", 5, "margin or kill_below"),
                 ("allow_external_raw", "public", "allow_external_raw differs from data_label"),
                 ("data_label", "partner", "synthetic exactly when"),
                 ("reference", "model-z", "endpoint names or reference")]
        for i, (key, value, needle) in enumerate(edits):
            path = self.dir / f"prereg-edit-{i}.json"
            path.write_text(canonical_dumps({**doc, key: value}) + "\n")
            with self.subTest(key=key):
                _, err = self.run_one(path, env["labels"], env["routing"], "model-a", 1, run_id="edited", expect=2)
                self.assertIn(needle, err)
                if key != "data_label":
                    _, err = self.compare(path, dirs, run_id="edited", expect=2)
                    self.assertIn(needle, err)
        self.assertEqual(snapshot(self.runs), before)
        self.assertEqual((len(env["a"].chat_requests), len(env["b"].chat_requests)), requests)

    def test_public_needs_records_from_a_public_source(self) -> None:
        records = [{**r, "synthetic": False} for r in independent(self.pid, 4)]
        partner = self.labels(self.pid, records, name="labels-partner.jsonl")
        public = self.labels(self.pid, [{**r, "site": "public"} for r in records], name="labels-public.jsonl")
        srv = self.server(responder=responder(self.pid, records))
        routing = self.routing({"model-a": self.oc(srv), "model-b": self.oc(srv)})
        _, err = self.prereg(partner, routing, run_id="pub-1", data_label="public", expect=2)
        self.assertIn("public source", err)
        self.assertFalse((self.runs / "e1" / "pub-1").exists())
        prereg, _ = self.prereg(public, routing, run_id="pub-2", data_label="public")
        self.assertEqual(load_json_file(prereg)["data_label"], "public")
        self.prereg(partner, routing, run_id="pub-3", data_label="partner")

    def test_a_different_served_model_is_recorded(self) -> None:
        env = self.flow(4, served_b="something-else")
        dirs = self.all_runs(env)
        run = load_json_file(dirs[-1] / "run.json")
        self.assertEqual((run["model_requested"], run["models_served"], run["model_mismatch"]),
                         ("tag-b", ["something-else"], True))
        self.assertEqual(load_json_file(dirs[0] / "run.json")["model_mismatch"], False)
        doc = load_json_file(self.compare(env["prereg"], dirs)[0])
        self.assertEqual(doc["endpoints"]["model-b"]["models_served"], ["something-else"])
        self.assertIs(doc["endpoints"]["model-b"]["model_mismatch"], True)

    def test_dry_runs_create_nothing(self) -> None:
        env = self.flow(4)
        before = snapshot(self.dir)
        for args in (["run", "--prereg", str(env["prereg"]), "--labels", str(env["labels"]), "--routing",
                      str(env["routing"]), "--endpoint", "model-a", "--repeat", "1", "--run-id", "dry"],
                     ["run", "--prereg", str(self.dir / "missing.json"), "--labels", "x", "--routing", "y",
                      "--endpoint", "model-a", "--repeat", "1", "--run-id", "dry"],
                     ["compare", "--prereg", str(env["prereg"]), "--run-dirs", str(self.dir / "missing-run"),
                      "--run-id", "dry"],
                     ["prereg", "--pack", self.pid, "--labels", str(env["labels"]), "--routing", str(env["routing"]),
                      "--endpoint", "model-a", "--endpoint", "model-b", "--reference", "model-a", "--margin", "5",
                      "--runs", "3", "--seed", "1", "--data-label", "synthetic", "--boundary", "site:a", "--run-id",
                      "dry"],
                     ["label-check", "--pack", self.pid, "--records", str(self.dir / "nope.jsonl"), "--sheet",
                      str(self.dir / "nope.csv"), "--out", str(self.dir / "nope-labels.jsonl")]):
            with self.subTest(command=args[0]):
                _, out, _ = self.cli(*args, "--runs-dir", str(self.runs), "--dry-run")
                self.assertTrue(out.startswith(f"dry-run: e1_extract {args[0]}"), out)
        self.assertEqual(snapshot(self.dir), before)
        self.assertEqual(env["a"].chat_requests, [])


# =================================================================================================== smoke

class E1SmokeTests(E1Case):
    def test_device_quality_smoke_with_two_fake_endpoints(self) -> None:
        env = self.flow(120)
        dirs = self.all_runs(env)
        self.assertEqual((len(env["a"].chat_requests), len(env["b"].chat_requests)), (360, 360))
        e1, _ = self.compare(env["prereg"], dirs)
        doc = load_json_file(e1)
        self.assertEqual((doc["kind"], doc["experiment"], doc["measurement"], doc["data_label"]),
                         ("e1", "E1", False, "synthetic"))
        self.assertEqual((doc["primary_metric"], doc["margin"], doc["reference"], doc["verdicts_withheld"]),
                         ("field_f1", 0.05, "model-a", "measurement_false"))
        for name, block in doc["endpoints"].items():
            with self.subTest(endpoint=name):
                for metric in ("field_f1", "claim_f1", "entity_f1", "predicate_f1"):
                    self.assertEqual(set(block[metric]), {"value", "ci_low", "ci_high", "B", "seed"})
                    self.assertEqual(block[metric]["B"], 1000)
                    self.assertLessEqual(block[metric]["ci_low"], block[metric]["value"])
                self.assertEqual(block["json_validity_rate"], 1.0)
                for t in ("lot", "supplier"):
                    self.assertGreaterEqual(block["exact_match"][t]["n"], 5)
                    self.assertEqual(block["exact_match"][t]["n"],         # records, not record x run pairs
                                     sum(1 for r in env["records"]
                                         if any(g["entity_type"] == t for g in WORLDS[self.pid].gold[r["record_ref"]])))
                    self.assertEqual(set(block["exact_match"][t]), {"n", "matches", "rate", "ci_low", "ci_high"})
                self.assertIsNotNone(block["latency_ms_p50"])
                self.assertIsNotNone(block["latency_ms_p95"])
                self.assertIn("field_f1", block["run_sd"])
                self.assertEqual([r["repeat"] for r in block["per_run"]], [1, 2, 3])
                self.assertEqual(block["runs"], 3)
        a = doc["endpoints"]["model-a"]
        self.assertEqual((a["field_f1"]["value"], a["claim_f1"]["value"]), (1.0, 1.0))
        self.assertEqual(a["exact_match"]["lot"]["rate"], 1.0)
        paired = doc["paired"]["model-b"]
        self.assertEqual(set(paired), {"against", "n", "dropped", "field_f1", "per_record_field_f1", "underpowered",
                                       "non_inferior", "kill_flag", "withheld_reason"})
        self.assertEqual(paired["withheld_reason"], "measurement_false")
        self.assertEqual((paired["against"], paired["n"], paired["dropped"]),
                         ("model-a", 120, {"only_model": 0, "only_reference": 0}))
        primary = paired["field_f1"]
        self.assertEqual(set(primary), {"endpoint", "reference", "diff", "ci95", "B", "seed", "undefined"})
        self.assertEqual((primary["reference"], primary["B"], primary["seed"], primary["undefined"]),
                         (1.0, 1000, "e1:11:model-b:field_f1_diff", 0))
        self.assertEqual(primary["endpoint"], doc["endpoints"]["model-b"]["field_f1"]["value"])
        self.assertEqual(primary["diff"], primary["endpoint"] - primary["reference"])
        self.assertLess(primary["ci95"][1], 0)
        per_record = paired["per_record_field_f1"]
        self.assertEqual(set(per_record), {"mean_diff", "ci95", "B", "seed", "sign", "sign_p"})
        self.assertLess(per_record["mean_diff"], 0)
        self.assertLess(per_record["ci95"][1], 0)
        self.assertLess(per_record["sign_p"], 0.05)
        self.assertIs(paired["underpowered"], True)
        self.assertIsNone(paired["non_inferior"])
        self.assertIsNone(paired["kill_flag"])
        self.assertNotIn("model-a", doc["paired"])
        for d in dirs:
            run = load_json_file(d / "run.json")
            self.assertEqual((run["complete"], run["measurement"], run["data_label"], run["listed_fake"]),
                             (True, False, "synthetic", True))
            rows = read_ledger(d / "ledger.jsonl")
            self.assertEqual(len(rows), 120)
            self.assertEqual({r["attempt"] for r in rows}, {1})
        texts = [r["narrative"] for r in env["records"]]
        for path in [e1, *(d / "run.json" for d in dirs)]:
            body = path.read_text(encoding="utf-8")
            for text in texts:
                for i in range(0, max(1, len(text) - 30), 15):
                    self.assertNotIn(text[i:i + 30], body)

    def test_reduced_smoke_on_every_other_builtin_pack(self) -> None:
        # B4: was the claims pack's reduced smoke; the exact-match types are now read from each pack
        others = [pid for pid in BUILTIN_PACKS if pid != "device_quality"]
        self.assertIn("claims_integrity", others)
        base = self.dir
        for pid in others:
            with self.subTest(pack=pid):
                self.pid, self.dir = pid, base / pid                  # each pack's own files and runs
                self.dir.mkdir()
                self.runs = self.dir / "runs"
                exact_match = sorted(t for t, et in PACKS[pid].entity_types.items() if et.exact_match_metric)
                if pid == "claims_integrity":
                    self.assertEqual(exact_match, ["clinic", "repair_shop"])
                env = self.flow(40)
                dirs = self.all_runs(env)
                doc = load_json_file(self.compare(env["prereg"], dirs)[0])
                self.assertEqual(doc["endpoints"]["model-a"]["field_f1"]["value"], 1.0)
                self.assertEqual(sorted(doc["endpoints"]["model-a"]["exact_match"]), exact_match)
                self.assertIs(doc["measurement"], False)


def plain_server(respond: Callable[[dict], dict], *, fail_every: int = 0) -> ThreadingHTTPServer:
    """A minimal OpenAI-compatible server that is not a fake (no fake header or model listing), so a run against it
    is a measurement; with ``fail_every`` it answers 503 for every record whose text hashes to 0 mod that number."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
            pass

        def _send(self, status: int, obj: Any) -> None:
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._send(200, {"object": "list", "data": [{"id": "m", "object": "model"}]})

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = request_payload(body)["text"]
            if fail_every and int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % fail_every == 0:
                return self._send(503, {"error": {"message": "server busy"}})
            message = {"role": "assistant", "content": json.dumps(respond(body))}
            self._send(200, {"model": "m", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 10}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class TransportFailureTests(E1Case):
    def test_a_flaky_server_is_not_scored_as_model_errors_and_withholds_the_verdict(self) -> None:
        # regression: two servers return the same gold answers; B answers 503 for about one record in four. The
        # failures are the server's: B's F1 and JSON validity stay those of the model, and the verdict is withheld
        records = independent(self.pid, 40)
        labels = self.labels(self.pid, records)
        servers = {"model-a": plain_server(responder(self.pid, records)),
                   "model-b": plain_server(responder(self.pid, records), fail_every=4)}
        for srv in servers.values():
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        routing = self.routing({name: {"provider": "openai_compat", "boundary": "site:a", "model": "m",
                                       "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "max_retries": 0}
                                for name, srv in servers.items()})
        prereg, _ = self.prereg(labels, routing)
        dirs = [self.run_one(prereg, labels, routing, name, k)[0] for name in servers for k in (1, 2, 3)]
        failed = [r for r in records
                  if int(hashlib.sha256(r["narrative"].encode("utf-8")).hexdigest(), 16) % 4 == 0]
        self.assertTrue(0 < len(failed) < len(records))
        run_b = load_json_file(dirs[-1] / "run.json")
        self.assertIs(run_b["measurement"], True)
        m = run_b["metrics"]
        self.assertEqual(m["failures"], {"transport": {"records": len(failed), "by_kind": {"http_5xx": len(failed)}},
                                         "model": {"records": 0, "by_kind": {}}})
        self.assertEqual(m["scored_records"], len(records) - len(failed))
        self.assertEqual((m["field_f1"]["value"], m["json_validity_rate"], m["valid_after_repair_rate"]),
                         (1.0, 1.0, 1.0))
        lines = [json.loads(line) for line in (dirs[-1] / "predictions.jsonl").read_text().splitlines()]
        self.assertEqual(sum(1 for p in lines if not p["scored"]), len(failed))
        self.assertTrue(all(p["error_kind"] == "http_5xx" for p in lines if not p["scored"]))
        doc = load_json_file(self.compare(prereg, dirs)[0])
        self.assertIs(doc["measurement"], True)
        b = doc["endpoints"]["model-b"]
        self.assertEqual((b["field_f1"]["value"], b["json_validity_rate"]), (1.0, 1.0))
        self.assertEqual(b["transport_failure_share"], 3 * len(failed) / (3 * len(records)))
        self.assertEqual(doc["endpoints"]["model-a"]["transport_failure_share"], 0.0)
        paired = doc["paired"]["model-b"]
        self.assertEqual((paired["field_f1"]["diff"], paired["per_record_field_f1"]["mean_diff"]), (0.0, 0.0))
        self.assertEqual(paired["dropped"], {"only_model": 0, "only_reference": len(failed)})
        self.assertEqual((paired["non_inferior"], paired["kill_flag"]), (None, None))
        self.assertIn("transport failures above 1% of the record runs of model-b", paired["withheld_reason"])


class PrimaryMetricVerdictTests(E1Case):
    """Audit r3: non-inferiority is judged on the pre-registered primary metric, micro field F1, not on the per-record
    mean field F1, where every claim-free record both sides leave empty adds an exact 0 difference."""

    def hand_run(self, prereg: Path, tag: str, endpoint: str, repeat: int,
                 rows: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]) -> Path:
        """A finished, measured run written directly (no server): one scored prediction per (ref, claims, gold)."""
        out = self.runs / "e1" / f"hand-{tag}-{endpoint}-{repeat}"
        out.mkdir(parents=True)
        lines = [{"record_ref": ref, "claims": claims, "ok": True, "error_kind": None, "scored": True,
                  "truncated": False, "drops": {}, "counts": record_counts(claims, gold),
                  "exact": exact_flags(claims, gold, ["lot", "supplier"]), "field_f1": record_field_f1(claims, gold)}
                 for ref, claims, gold in rows]
        (out / "predictions.jsonl").write_text("".join(canonical_dumps(line) + "\n" for line in lines),
                                               encoding="utf-8")
        run = {"kind": "e1_run", "endpoint": endpoint, "repeat": repeat, "complete": True, "measurement": True,
               "models_served": ["m"], "model_mismatch": False, "model_requested": "m",
               "prereg_sha256": sha256_hex(prereg.read_bytes()), "ledger_sha256": None,
               "predictions_sha256": sha256_hex((out / "predictions.jsonl").read_bytes()),
               "finished_at": "2026-01-01T00:00:00Z"}
        (out / "run.json").write_text(canonical_dumps(run), encoding="utf-8")
        return out

    def scenario(self, prereg: Path, empty_share: float) -> dict[str, Any]:
        """The finder's probe: 600 records, three lot claims (one predicate) on each record that has claims; the
        model drops two of three claims on 25% of those, the reference drops one on 5%. Every run of an endpoint
        returns the same predictions."""
        rng = random.Random(1)
        model, reference = [], []
        for r in range(600):
            ref = f"rec-{r:04d}"
            if r < int(600 * empty_share):
                model.append((ref, [], []))
                reference.append((ref, [], []))
                continue
            gold = [claim("lot", f"L{r * 10 + j:05d}", "leak") for j in range(3)]
            reference.append((ref, gold if rng.random() >= 0.05 else gold[1:], gold))
            model.append((ref, gold if rng.random() >= 0.25 else gold[2:], gold))
        tag = f"empty{int(empty_share * 100)}"
        dirs = [self.hand_run(prereg, tag, name, k, rows)
                for name, rows in (("model-a", reference), ("model-b", model)) for k in (1, 2, 3)]
        return load_json_file(self.compare(prereg, dirs, run_id=f"cmp-{tag}")[0])

    def test_a_model_beyond_the_margin_in_field_f1_is_not_non_inferior_however_many_records_are_claim_free(self) \
            -> None:
        prereg = self.flow(4)["prereg"]
        docs = {}
        for empty_share in (0.5, 0.0):
            with self.subTest(empty_share=empty_share):
                doc = docs[empty_share] = self.scenario(prereg, empty_share)
                self.assertIs(doc["measurement"], True)
                paired = doc["paired"]["model-b"]
                primary = paired["field_f1"]
                self.assertEqual((paired["n"], paired["withheld_reason"], paired["underpowered"]), (600, None, False))
                self.assertEqual(primary["endpoint"], doc["endpoints"]["model-b"]["field_f1"]["value"])
                self.assertEqual(primary["reference"], doc["endpoints"]["model-a"]["field_f1"]["value"])
                self.assertLess(primary["diff"], -doc["margin"])          # beyond the 5-point margin
                self.assertLessEqual(primary["ci95"][0], -doc["margin"])
                self.assertIs(paired["non_inferior"], False)
                self.assertIs(paired["kill_flag"], False)
        # the case the finding names: with half the records claim-free, the per-record mean difference's interval
        # lies inside the margin, so judging it would have called this model non-inferior
        diluted = docs[0.5]["paired"]["model-b"]
        self.assertGreater(diluted["per_record_field_f1"]["ci95"][0], -0.05)
        self.assertLess(diluted["field_f1"]["diff"], -0.05)
        self.assertIs(diluted["non_inferior"], False)

    def test_claim_free_records_leave_the_decision_unchanged(self) -> None:
        a = [(5, 0, 1), (2, 1, 0), (0, 0, 3), (4, 0, 0)]
        b = [(6, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0)]
        boot = stats.paired_bootstrap_f1(a, b, B=500, seed="s")
        padded = stats.paired_bootstrap_f1(a + [(0, 0, 0)] * 40, b + [(0, 0, 0)] * 40, B=500, seed="s")
        self.assertEqual((padded["f1_a"], padded["f1_b"], padded["diff"]), (boot["f1_a"], boot["f1_b"], boot["diff"]))
        self.assertEqual(boot["diff"], stats.f1_from_counts(11, 1, 4) - 1.0)


class ExternalRawTests(E1Case):
    def setup_endpoints(self, n: int = 6, *, synthetic: bool = True) -> dict[str, Any]:
        records = independent(self.pid, n)
        if not synthetic:
            records = [{**r, "synthetic": False} for r in records]
        labels = self.labels(self.pid, records, name=f"labels-{synthetic}.jsonl")
        site = self.server(responder=responder(self.pid, records))
        external = self.server(responder=responder(self.pid, records))
        routing = self.routing({"site-model": self.oc(site, model="site-tag"),
                                "frontier-ref": self.oc(external, boundary="external", model="ref-tag")},
                               name=f"routing-{synthetic}.json")
        return {"labels": labels, "routing": routing, "site": site, "external": external}

    def test_external_raw_is_refused_unless_exempt(self) -> None:
        env = self.setup_endpoints()
        partner = self.setup_endpoints(synthetic=False)
        cases = [((), env, "synthetic"), (("--allow-external-raw", "public"), env, "synthetic"),
                 ((), partner, "partner"), (("--allow-external-raw", "public"), partner, "partner"),
                 (("--allow-external-raw", "synthetic"), partner, "partner")]
        for i, (extra, e, label) in enumerate(cases):
            with self.subTest(extra=extra, data_label=label):
                _, err = self.prereg(e["labels"], e["routing"], *extra, run_id=f"ext-{i}",
                                     endpoints=("site-model", "frontier-ref"), reference="frontier-ref",
                                     data_label=label, expect=2)
                self.assertTrue("may not receive raw records" in err or "must equal --data-label" in err, err)
                self.assertFalse((self.runs / "e1" / f"ext-{i}").exists())
        self.assertEqual(env["external"].requests, [])
        self.assertEqual(partner["external"].requests, [])

    def test_synthetic_data_with_the_synthetic_exemption_runs(self) -> None:
        env = self.setup_endpoints()
        prereg, _ = self.prereg(env["labels"], env["routing"], "--allow-external-raw", "synthetic",
                                endpoints=("site-model", "frontier-ref"), reference="frontier-ref")
        dirs = []
        for endpoint in ("site-model", "frontier-ref"):
            for k in (1, 2, 3):
                dirs.append(self.run_one(prereg, env["labels"], env["routing"], endpoint, k)[0])
        e1, _ = self.compare(prereg, dirs)
        self.assertEqual(load_json_file(prereg)["allow_external_raw"], "synthetic")
        self.assertEqual(load_json_file(dirs[-1] / "run.json")["allow_external_raw"], "synthetic")
        self.assertEqual(load_json_file(e1)["allow_external_raw"], "synthetic")
        modes = {r["endpoint"]: r["boundary_mode"] for d in dirs for r in read_ledger(d / "ledger.jsonl")}
        self.assertEqual(modes, {"site-model": "own", "frontier-ref": "external_raw_exempt"})


if __name__ == "__main__":
    unittest.main()

"""B3: X5, leakage beyond text, measured by an HQ-level red team on synthetic worlds (synthetic, same-author, internal
only). The split, the variant pack copies, the lexical rows, the fact extractors on hand-built artifacts, the pure
attacks on constructed mini-worlds, the A6 probe against a real site and verifier, the statistics and labels, the
prereg and run refusals, the x5.json schema, portability and self-scan, determinism across hash seeds, the controls,
LEAKAGE section 12 and the import closure of the code hash.

One tiny prereg and run (device pack, n = 400, one seed, the default variant with its two derived variants and both
simulated transforms, B = 100) is made in ``setUpModule`` and shared. Temporary directories only; nothing here is a
result.
"""
from __future__ import annotations

import contextlib
import copy
import inspect
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from mycelic.collective import leakage
from mycelic.collective.edge.egress import CHANNELS, SUPPRESSED, check_artifact, read_log
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.verify import SiteVerifier
from mycelic.collective.experiments import x5_attacks as xa
from mycelic.collective.experiments import x5_inference as x5
from mycelic.collective.experiments.common import ROOT, code_files
from mycelic.collective.experiments.g0_canary import DAY_TIME, build_context, run_stages
from mycelic.collective.jsonio import canonical_dumps, strict_load
from mycelic.collective.packs.loader import load_pack
from mycelic.collective.pushdown.questions import build_question
from mycelic.collective.runfiles import content_hash, portability_problems
from tests.mycelic.test_collective_x1_sealed import format_value, lookup

PACKS = ("device_quality", "claims_integrity")
LEAKAGE_MD = ROOT / "docs" / "collective" / "LEAKAGE.md"
MARK_BEGIN, MARK_END = "<!-- x5:begin -->", "<!-- x5:end -->"
TINY = ["--packs", "device_quality", "--n", "400", "--seeds", "11", "--shadow-seeds", "12",
        "--variants", "default,k1_reference,a5_injected", "--primary-b", "100", "--exploratory-b", "100"]
DETERMINISM = ["--packs", "device_quality", "--n", "200", "--seeds", "11", "--shadow-seeds", "12",
               "--variants", "default", "--attacks", "A1_calibrated,A2,A3,A5",
               "--artifact-types", "cells_codes,cells_text,verdicts_passive,all", "--simulated", "none",
               "--primary-b", "100", "--exploratory-b", "100"]
FIXTURE: dict[str, Any] = {}


def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = x5.main(argv)
    return code, out.getvalue(), err.getvalue()


def make_prereg(runs: Path, run_id: str, extra: list[str]) -> Path:
    code, out, err = run_main(["prereg", "--run-id", run_id, "--runs-dir", str(runs), "--allow-dirty", *extra])
    if code != 0:
        raise AssertionError(f"prereg failed: {err}")
    return runs / "x5" / run_id / "prereg.json"


def setUpModule() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="collective-x5-test-"))
    FIXTURE["tmp"] = tmp
    prereg = make_prereg(tmp / "runs", "tiny", TINY)
    code, out, err = run_main(["run", "--prereg", str(prereg), "--run-id", "tiny-run", "--runs-dir",
                               str(tmp / "runs"), "--work-dir", str(tmp / "work"), "--allow-dirty"])
    FIXTURE.update(prereg=prereg, code=code, out=out, err=err, run=tmp / "runs" / "x5" / "tiny-run")


def tearDownModule() -> None:
    shutil.rmtree(FIXTURE["tmp"], ignore_errors=True)


def tiny_doc() -> dict[str, Any]:
    return strict_load((FIXTURE["run"] / "x5.json").read_bytes())


def tiny_prereg() -> dict[str, Any]:
    return strict_load(FIXTURE["prereg"].read_bytes())


# --------------------------------------------------------------------------------------------------- split

class SplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = load_pack("device_quality")
        cls.world = x5.build_world(cls.pack, 7, 300)

    def test_the_split_is_identical_across_calls_and_hash_seeds(self) -> None:
        records = self.world.records
        first = x5.split_members(records, 7)
        self.assertEqual(first, x5.split_members(records, 7))
        self.assertNotEqual(first, x5.split_members(records, 8))
        code = ("import json\nfrom mycelic.collective.experiments import x5_inference as x5\n"
                "from mycelic.collective.packs.loader import load_pack\n"
                "w = x5.build_world(load_pack('device_quality'), 7, 300)\n"
                "print(json.dumps(sorted(r for r, m in x5.split_members(w.records, 7).items() if m)))\n")
        for seed in ("0", "1"):
            r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                               env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": seed})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout), sorted(ref for ref, m in first.items() if m))

    def test_a_forwarded_copy_follows_its_origin(self) -> None:
        member = self.world.member
        copies = [r for r in self.world.records if r["origin_ref"] is not None]
        self.assertTrue(copies)
        for r in copies:
            self.assertEqual(member[r["record_ref"]], member[r["origin_ref"]])

    def test_strata_are_balanced_single_class_strata_dropped_and_copies_never_targets(self) -> None:
        keys = x5.record_keys(x5.Variant("default", "pipeline", self.pack, self.pack.egress.k, 1), self.world)
        targets, dropped = x5.a1_targets(self.world, keys)
        per: dict[tuple[Any, ...], list[int]] = {}
        for known, member, cluster in targets:
            per.setdefault(cluster, [0, 0])[int(member)] += 1
        self.assertTrue(per)
        for counts in per.values():
            self.assertEqual(counts[0], counts[1])
        strata: dict[tuple[str, int], set[bool]] = {}
        for r in self.world.originals():
            strata.setdefault((r["site"], self.world.week_of[r["record_ref"]]), set()).add(
                self.world.member[r["record_ref"]])
        self.assertEqual(dropped, sum(1 for classes in strata.values() if len(classes) == 1))
        self.assertGreater(dropped, 0)
        forwarded = {r["record_ref"] for r in self.world.records if r["origin_ref"] is not None}
        self.assertFalse(forwarded & {known.ref for known, _, _ in targets})


# --------------------------------------------------------------------------------------------------- variants

class VariantPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="collective-x5-variants-"))
        cls.packs = {p: load_pack(p) for p in PACKS}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, pack: str, variant: str, **kw: Any) -> Any:
        dest = Path(tempfile.mkdtemp(dir=self.tmp)) / variant
        return x5.write_variant(self.packs[pack], variant, dest, volume_factor=4, **kw)

    def changed(self, base: Any, copy_: Any) -> set[str]:
        return {name for name, value in copy_.hashes().items() if value != base.hashes()[name]}

    def test_bucket_edges(self) -> None:
        device, claims = self.packs["device_quality"], self.packs["claims_integrity"]
        self.assertEqual(x5.bucket_edges(device, 10), [10, 50])
        self.assertEqual(x5.bucket_edges(device, 2), [2, 10, 50])
        self.assertEqual(x5.bucket_edges(claims, 10), [10, 50])
        self.assertEqual(x5.bucket_edges(claims, 2), [2, 10, 50])

    def test_every_copy_loads_and_changes_exactly_its_hashes(self) -> None:
        for name in PACKS:
            base = self.packs[name]
            with self.subTest(pack=name):
                k2, k10 = self.copy(name, "k2"), self.copy(name, "k10")
                self.assertEqual((k2.egress.k, list(k2.egress.verdict_count_buckets)), (2, [2, 10, 50]))
                self.assertEqual((k10.egress.k, list(k10.egress.verdict_count_buckets)), (10, [10, 50]))
                self.assertEqual(self.changed(base, k2), {"config_hash", "detector_hash"})
                self.assertEqual(self.changed(base, k10), {"config_hash", "detector_hash"})
                rmd = self.copy(name, "rmd_flipped")
                self.assertEqual(rmd.egress.require_master_data, not base.egress.require_master_data)
                self.assertEqual(self.changed(base, rmd), {"config_hash"})
                minus = self.copy(name, "minus_type")
                self.assertEqual(self.changed(base, minus), {"config_hash", "vocabulary_hash"})
                self.assertNotIn(x5.removed_type(base), minus.egress.egress_entity_types)
                volume = self.copy(name, "volume")
                self.assertEqual(self.changed(base, volume), {"fixtures_hash"})
                self.assertEqual([list(s["weekly_volume"]) for s in volume.generator["sites"]],
                                 [[4 * v for v in s["weekly_volume"]] for s in base.generator["sites"]])
                same = self.copy(name, f"k{base.egress.k}")
                self.assertEqual(same.hashes(), base.hashes())

    def test_minus_type_edits_the_question_templates(self) -> None:
        for name in PACKS:
            base, minus = self.packs[name], self.copy(name, "minus_type")
            removed = x5.removed_type(base)
            with self.subTest(pack=name):
                for template_id, template in minus.questions.items():
                    self.assertNotIn(removed, template.entity_types)
                    self.assertEqual(set(template.entity_types),
                                     set(base.questions[template_id].entity_types) - {removed})
                self.assertEqual(set(minus.questions), {t for t, q in base.questions.items()
                                                        if set(q.entity_types) - {removed}})

    def test_removing_a_type_a_draft_template_needs_is_refused(self) -> None:
        with self.assertRaises(x5.UsageError) as ctx:
            self.copy("device_quality", "minus_type", entity_type="lot")
        self.assertIn("device_quality", str(ctx.exception))
        self.assertIn("lot", str(ctx.exception))

    def test_the_minus_type_and_the_a5_fields_are_rules(self) -> None:
        for name in PACKS:
            pack = self.packs[name]
            self.assertEqual(x5.removed_type(pack), pack.mapping()["primary_entity_type"])
            persons = pack.generator["persons"]
            self.assertEqual(x5.a5_fields(pack), sorted(f for f in persons if persons[f]["kind"] == "name"))
        self.assertEqual(x5.removed_type(self.packs["device_quality"]), "product")
        self.assertEqual(x5.removed_type(self.packs["claims_integrity"]), "repair_shop")
        self.assertEqual(x5.a5_fields(self.packs["device_quality"]), ["clinician_name", "patient_name"])
        self.assertEqual(x5.a5_fields(self.packs["claims_integrity"]), ["claimant_name"])


# --------------------------------------------------------------------------------------------------- lexical rows

class LexicalRowsTests(unittest.TestCase):
    def test_lexical_rows_equal_what_a_fake_mode_site_stores(self) -> None:
        for name in PACKS:
            pack = load_pack(name)
            world = x5.build_world(pack, 5, 150)
            with self.subTest(pack=name), tempfile.TemporaryDirectory() as tmp:
                run = x5.run_world(x5.Variant("default", "pipeline", pack, pack.egress.k, 1), world,
                                   Path(tmp) / "w", a6=None, max_a6_days=0)
                self.assertFalse((Path(tmp) / "w").exists())
                expected = {site: rows for site, rows in run.rows.items() if rows}
                self.assertTrue(expected)
                self.assertEqual(x5.lexical_rows(pack, world.members, world.master), expected)


# --------------------------------------------------------------------------------------------------- facts

def stub_world(pack: Any, *, master: dict[str, dict[str, tuple[str, ...]]] | None = None,
               last_week: int = 120) -> x5.WorldData:
    sites = ("site-a", "site-b")
    return x5.WorldData(seed=1, records=(), gold={}, master=master or {s: {} for s in sites}, sites=sites,
                        start=date(2024, 1, 1), weeks=last_week + 1, digest="0" * 64, member={}, week_of={},
                        last_week=last_week)


def facts_of(facts: list[xa.Fact], field: str, **match: Any) -> list[xa.Fact]:
    return [f for f in facts if f.field == field and all(getattr(f, k) == v for k, v in match.items())]


class FactExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = load_pack("device_quality")
        cls.claims = load_pack("claims_integrity")

    def test_week_indexes_across_a_year_boundary(self) -> None:
        ex = x5.Extractor(self.pack, stub_world(self.pack))
        self.assertEqual(ex.week("2024-W01"), 0)
        self.assertEqual(ex.week("2024-W52"), 51)
        self.assertEqual(ex.week("2025-W01"), 52)
        self.assertEqual(ex.week("2026-W53"), 52 + 52 + 52)
        self.assertEqual(ex.week("2027-W01"), 52 + 52 + 53)

    def bundle(self, cells: list[dict[str, Any]], after: str | None = None) -> dict[str, Any]:
        return {"after": after, "as_of": "2024-03-10", "cells": cells, "closed_through": "2024-W06",
                "config_hash": self.pack.config_hash, "k": 3, "pack": self.pack.id, "schema_version": 1,
                "site": "site-a"}

    def cell(self, week: str, channel: str, n: Any) -> dict[str, Any]:
        out = {"channel": channel, "entity_id": "L10001", "entity_type": "lot", "iso_week": week, "n": n,
               "n_reporters": n if n == SUPPRESSED else 1, "n_roots": n, "predicate": "leak"}
        if n != SUPPRESSED:
            out["res_conf_min"] = 1.0
        return out

    def test_cells_by_channel_with_coverage_and_the_four_week_span(self) -> None:
        ex = x5.Extractor(self.pack, stub_world(self.pack))
        body = self.bundle([self.cell("2024-W02", "codes", SUPPRESSED), self.cell("2024-W03", "text_only", 4)],
                           after="2024-W01")
        codes = ex.cells("cells_codes", [body], CHANNELS[:1])
        n = facts_of(codes, "n")
        self.assertEqual([(f.first, f.last, f.channel, f.lo, f.hi) for f in n], [(1, 1, "codes", 1, 2)])
        covered = facts_of(codes, "covered")
        self.assertEqual([(f.first, f.last, f.channel, f.lo, f.hi) for f in covered],
                         [(w, w, "codes", 0, 0) for w in range(1, 6)])
        text = ex.cells("cells_text", [body], CHANNELS[1:])
        self.assertEqual([(f.lo, f.hi) for f in facts_of(text, "n")], [(4, 4)])
        self.assertEqual([(f.lo, f.hi) for f in facts_of(text, "n_reporters")], [(1, 1)])
        four = x5.Extractor(self.pack, stub_world(self.pack)).cells("cells_codes", [body], CHANNELS[:1], period=4,
                                                                   missing_hi=2)
        self.assertEqual([(f.first, f.last) for f in facts_of(four, "n")], [(1, 4)])
        self.assertEqual([(f.first, f.last, f.hi) for f in facts_of(four, "covered")], [(0, 3, 2), (4, 7, 2)])
        self.assertTrue(facts_of(codes, "string", text="L10001"))

    def test_usage_calls(self) -> None:
        ex = x5.Extractor(self.pack, stub_world(self.pack))
        group = {"calls": 7, "endpoint": "site-fake", "errors": {"timeout": "suppressed"}, "fake": True,
                 "ok": "suppressed", "task": "extract_claims", "tokens_in_missing": "suppressed",
                 "tokens_out_missing": "suppressed"}
        small = {**group, "calls": SUPPRESSED, "ok": SUPPRESSED, "errors": {}, "tokens_in_missing": 0,
                 "tokens_out_missing": 0}
        body = {**self.bundle([]), "groups": [group, small]}
        del body["cells"]
        facts = ex.usage([body])
        self.assertEqual([(f.lo, f.hi, f.entity_id) for f in facts_of(facts, "calls")], [(7, 7, None), (1, 2, None)])
        self.assertTrue(facts_of(facts, "string", text="suppressed"))

    def verdict(self, verdict: str, **kw: Any) -> dict[str, Any]:
        body = {"entity_records_bucket": None, "evidence_ref": None, "newest_week": None, "pack": self.pack.id,
                "quality": "ok", "question_id": "q" * 64, "reason": None, "reporters_bucket": None,
                "roots_bucket": None, "site": "site-a", "support_bucket": None, "truncated": False,
                "verdict": verdict, "window": {"start_week": "2024-W02", "end_week": "2024-W07"}}
        body.update(kw)
        return body

    def test_verdicts(self) -> None:
        world = stub_world(self.pack, master={"site-a": {"lot": ("L10001",)}, "site-b": {}})
        ex = x5.Extractor(self.pack, world)
        params = {"entity_type": "lot", "entity_id": "L10001", "predicate": "leak"}
        window = {"start_week": "2024-W02", "end_week": "2024-W07"}
        out: list[xa.Fact] = []
        ex.verdict(out, "v", self.verdict("confirm", support_bucket="3-9", roots_bucket="<k",
                                          reporters_bucket="<k", newest_week="2024-W04"), params, window, "site-a")
        support = facts_of(out, "support")
        self.assertIn((1, 6, 3, 9), [(f.first, f.last, f.lo, f.hi) for f in support])
        self.assertIn((3, 3, 1, None), [(f.first, f.last, f.lo, f.hi) for f in support])
        self.assertEqual(sorted((f.first, f.lo, f.hi) for f in support if f.first == f.last and f.first > 3),
                         [(4, 0, 0), (5, 0, 0), (6, 0, 0)])
        self.assertEqual([(f.lo, f.hi) for f in facts_of(out, "roots")], [(1, 2)])
        out = []
        ex.verdict(out, "v", self.verdict("confirm", support_bucket="10-49", roots_bucket="3-9",
                                          reporters_bucket="3-9", newest_week="2024-W07", truncated=True),
                   params, window, "site-a")
        self.assertEqual([(f.lo, f.hi) for f in facts_of(out, "support") if f.first != f.last], [(10, None)])
        out = []
        ex.verdict(out, "v", self.verdict("refute", entity_records_bucket="<k"), params, window, "site-a")
        self.assertEqual([(f.predicate, f.lo, f.hi) for f in facts_of(out, "support")], [("leak", 0, 0)])
        self.assertEqual([(f.predicate, f.lo, f.hi) for f in facts_of(out, "entity_records")], [(None, 1, 2)])
        out = []
        ex.verdict(out, "v", self.verdict("unknown"), params, window, "site-a")
        self.assertEqual(sorted((f.field, f.predicate, f.lo, f.hi) for f in out),
                         [("entity_records", None, 0, 0), ("support", None, 0, 0)])
        out = []
        ex.verdict(out, "v", self.verdict("unknown"), params, window, "site-b")    # not the site's master data
        self.assertEqual(out, [])
        for reason in ("budget", "no_secret"):
            out = []
            ex.verdict(out, "v", self.verdict("unknown", reason=reason), params, window, "site-a")
            self.assertEqual(out, [], reason)

    def packet(self) -> dict[str, Any]:
        return {"candidate_key": "lot:L10001:leak", "co_mentions": [{"entity_id": "SD-10", "entity_type": "product",
                                                                      "n": "3-9"}],
                "codes": [{"code": "ILL-0201", "n": "suppressed"}, {"code": "ILL-9001", "n": "3-9"}],
                "evidence_ref": "a" * 16, "followup_key": "act:c-" + "b" * 32 + ":evidence_packet:" + "c" * 16,
                "pack": self.pack.id, "pack_hash": self.pack.config_hash, "question_id": "b" * 64,
                "reporters_bucket": "<k", "roots_bucket": "3-9", "schema_version": 1, "site": "site-b",
                "status": "ok", "support_bucket": "3-9", "truncated": False, "verdict": "confirm",
                "window": {"end_week": "2024-W07", "start_week": "2024-W02"}}

    def test_packets(self) -> None:
        ex = x5.Extractor(self.pack, stub_world(self.pack))
        facts = ex.packets([{"body": self.packet()}])
        codes = facts_of(facts, "code")
        self.assertEqual(sorted((f.predicate, f.lo, f.hi) for f in codes),
                         sorted([(self.pack.codes["ILL-0201"].predicate, 1, None),
                                 (self.pack.codes["ILL-9001"].predicate, 3, 9)]))
        self.assertEqual([(f.entity_id, f.predicate, f.lo, f.hi, f.site) for f in facts_of(facts, "co_mention")],
                         [("SD-10", "leak", 3, 9, "site-b")])
        self.assertEqual([(f.lo, f.hi) for f in facts_of(facts, "support")], [(3, 9)])

    def test_followup_hq_results_and_run_files(self) -> None:
        ex = x5.Extractor(self.pack, stub_world(self.pack))
        context = x5.ConclusionContext((1, 6), "leak", ("site-a", "site-b"))
        key = "act:c-" + "b" * 32 + ":capa_initiation_draft:" + "d" * 16
        entries = [{"seq": 1, "kind": "drafted", "key": key, "payload": {"draft": {"title": "Lot L10001 and SD-10"}}},
                   {"seq": 2, "kind": "executed", "key": key, "payload": {"result": {"packets": [self.packet()]}}}]
        outbox = [{"key": key, "draft": {"title": "SD-10"}}]
        central = [{"task": "draft_followup", "endpoint": "hq-fake"}]
        facts = ex.followup(entries, outbox, central, lambda k: context if x5.conclusion_of(k) else None)
        presence = facts_of(facts, "presence")
        self.assertEqual(sorted({(f.entity_id, f.site, f.predicate, f.first, f.last) for f in presence}),
                         [("L10001", "site-a", "leak", 1, 6), ("L10001", "site-b", "leak", 1, 6),
                          ("SD-10", "site-a", "leak", 1, 6), ("SD-10", "site-b", "leak", 1, 6)])
        self.assertTrue(facts_of(facts, "co_mention"))
        self.assertTrue(facts_of(facts, "string", text="hq-fake"))
        self.assertEqual(x5.conclusion_of(key), "c-" + "b" * 32)
        self.assertIsNone(x5.conclusion_of("not-a-key"))
        result = {"candidates": [{"entity_type": "lot", "entity_id": "L10001", "predicate": "leak",
                                  "snapshot": {"window": ["2024-W02", "2024-W07"], "contributing_sites": ["site-a"],
                                               "d2": {"sites": [{"site": "site-a", "c": 2}, {"site": "site-b",
                                                                                          "c": None}]}}}]}
        body = {"candidate_key": "lot:L10001:leak",
                "gate": {"used": [{"site": "site-a", "verdict": "confirm"}, {"site": "site-b", "verdict": "refute"}],
                         "support": {"newest_week": "2024-W05", "confirming_sites": ["site-a"]}}}
        hq = ex.hq_results(result, [(body, {"window": {"start_week": "2024-W02", "end_week": "2024-W07"}})])
        support = facts_of(hq, "support")
        self.assertIn(("site-a", 1, 6, 2, None), [(f.site, f.first, f.last, f.lo, f.hi) for f in support])
        self.assertIn(("site-b", 1, 6, 0, 0), [(f.site, f.first, f.last, f.lo, f.hi) for f in support])
        self.assertIn(("site-a", 4, 4, 1, None), [(f.site, f.first, f.last, f.lo, f.hi) for f in support])
        self.assertEqual(sorted((f.first, f.lo) for f in support if f.site == "site-a" and f.first > 4),
                         [(5, 0), (6, 0)])
        self.assertEqual(len(facts_of(hq, "presence", site="site-a")), 2)
        trace = {"questions": [{"question_id": "b" * 32, "candidate_key": "lot:L10001:leak",
                                "window": {"start_week": "2024-W02", "end_week": "2024-W07"}, "text": "q"}],
                 "conclusions": [{"conclusion_id": "c-" + "b" * 32, "status": "supported", "reasons": [],
                                  "verdicts": [{"site": "site-a", "verdict": "confirm", "reason": None,
                                                "support_bucket": "3-9", "roots_bucket": "<k",
                                                "reporters_bucket": "<k", "entity_records_bucket": None,
                                                "evidence_ref": "a" * 16}]}]}
        approvals = [*entries, {"kind": "ledger_head", "entries": 2}]
        run = ex.run_files(trace, approvals, {"kind": "g0_scorecard"}, [{"task": "draft_followup"}])
        # the trace's confirm (no truncation flag in a run file: open above) and the executed packet in approvals
        self.assertEqual(sorted((f.site, f.lo, f.hi) for f in facts_of(run, "support")),
                         [("site-a", 3, None), ("site-b", 3, 9)])
        self.assertTrue(facts_of(run, "presence", site="site-a", entity_id="SD-10"))

    def test_string_facts_are_deduplicated(self) -> None:
        ex = x5.Extractor(self.pack, stub_world(self.pack))
        out: list[xa.Fact] = []
        ex.strings(out, "x", "site-a", 1, 1, {"a": "same", "b": ["same", "other"]})
        ex.strings(out, "x", "site-a", 1, 1, ["same"])
        self.assertEqual(sorted(f.text for f in out), ["other", "same"])
        ex.strings(out, "x", "site-a", 2, 2, ["same"])
        self.assertEqual(len(out), 3)


# --------------------------------------------------------------------------------------------------- attacks

def F(site: str | None, first: int, last: int, field: str, lo: int, hi: int | None, *, entity: tuple[str, str] |
      None = ("lot", "L1"), predicate: str | None = "leak", channel: str | None = None,
      text: str | None = None) -> xa.Fact:
    t, e = entity if entity is not None else (None, None)
    return xa.Fact("t", site, first, last, t, e, predicate, channel, field, lo, hi, text)


def covered(site: str, weeks: range, hi: int = 0) -> list[xa.Fact]:
    return [F(site, w, w, "covered", 0, hi, entity=None, predicate=None, channel=c) for w in weeks for c in CHANNELS]


class AttackTests(unittest.TestCase):
    def test_the_channels_are_the_boundarys(self) -> None:
        self.assertEqual(xa.CHANNELS, CHANNELS)

    def test_a1_threshold_tie_rule_fixed_rule_and_coin(self) -> None:
        self.assertEqual(xa.calibrate([0.0, 0.5, 1.0, 1.0], [False, False, True, True]), 1.0)
        self.assertEqual(xa.calibrate([0.0, 0.5, 1.0], [False, True, True]), 0.5)
        self.assertEqual(xa.calibrate([0.2, 0.4], [True, False]), 0.2)        # a tie at 1 of 2: the smaller value
        self.assertEqual(xa.calibrate([None, None], [True, False]), 1.0)
        self.assertEqual(xa.a1_predict(0.5, False, 0.5), (True, True))
        self.assertEqual(xa.a1_predict(0.5, False, None), (False, True))
        self.assertEqual(xa.a1_predict(1.0, False, None), (True, True))
        self.assertEqual(xa.a1_predict(None, True, 0.5), (True, False))
        self.assertEqual(xa.a1_coin(3, "r-1"), xa.a1_coin(3, "r-1"))
        self.assertTrue(150 <= sum(xa.a1_coin(3, f"r-{i}") for i in range(400)) <= 250)
        index = xa.FactIndex([F("s", 4, 4, "n", 1, 2, channel="codes"), F("s", 0, 9, "support", 1, None,
                                                                          entity=("lot", "L2"), predicate="crack")])
        known = xa.A1Known("s", 4, "r", (("lot", "L1", "leak", "codes"), ("lot", "L1", "leak", "text_only"),
                                         ("lot", "L2", "crack", "codes")), False)
        self.assertAlmostEqual(xa.a1_score(known, index), 2 / 3)
        self.assertIsNone(xa.a1_score(xa.A1Known("s", 4, "r", (), True), index))

    def test_a2_support_argmax_prior_tie_break_and_fallback(self) -> None:
        prior = xa.A2Prior(by_entity_site={("lot", "L1", "s"): {"crack": 2}}, by_entity={("lot", "L1"): {"leak": 5}},
                           by_type={"lot": {"overheat": 9}}, overall={"alarm_failure": 20})
        predicates = ["alarm_failure", "crack", "leak", "overheat"]
        known = xa.A2Known("s", 3, (("lot", "L1"), ("product", "SD-1")), ("lot", "L1"))
        self.assertEqual(xa.a2_predict(known, xa.FactIndex([]), prior, predicates), ("crack", False))
        index = xa.FactIndex([F("s", 3, 3, "n", 1, 2, predicate="leak"),
                              F("s", 3, 3, "n", 1, 2, entity=("product", "SD-1"), predicate="overheat"),
                              F("s", 3, 3, "n", 1, 2, entity=("product", "SD-1"), predicate="leak")])
        self.assertEqual(xa.a2_predict(known, index, prior, predicates), ("leak", True))
        tie = xa.FactIndex([F("s", 3, 3, "n", 1, 2, predicate="overheat"), F("s", 3, 3, "n", 1, 2, predicate="leak")])
        self.assertEqual(xa.a2_predict(known, tie, prior, predicates), ("leak", True))       # P(p | e*) decides
        other = xa.A2Known("s2", 3, (("lot", "L9"),), ("lot", "L9"))
        self.assertEqual(xa.a2_prior_prediction(other, prior, predicates), "overheat")     # P(p | type)
        empty = xa.A2Prior({}, {}, {}, {})
        self.assertEqual(xa.a2_prior_prediction(other, empty, predicates), "alarm_failure")  # sorted id

    def test_a3_recovers_n_1_from_a_suppressed_window_sum_and_another_suppressed_week(self) -> None:
        facts = [F("s", 0, 0, "n", 1, 2, channel="codes"), F("s", 1, 1, "n", 1, 2, channel="codes"),
                 *covered("s", range(0, 2)), F("s", 0, 1, "support", 1, 2)]
        known = xa.A3Known("s", 0, "lot", "L1", "leak", "codes", True)
        self.assertEqual(xa.a3_interval(known, xa.FactIndex(facts), 3), (1, 1))
        self.assertEqual(xa.a3_predict(known, xa.FactIndex(facts), 3, {1: 1, 2: 9}), (1, True))
        self.assertEqual(xa.a3_interval(known, xa.FactIndex(facts[:2] + facts[-1:-1]), 3), (1, 2))

    def test_a3_narrows_inside_a_bucket_from_overlapping_windows(self) -> None:
        # k = 5: weeks 0..5 sum to 5-9, weeks 1..6 to '<k'; cells '<k' at 0, 3 and 6, every other week empty
        facts = [F("s", w, w, "n", 1, 4, channel="codes") for w in (0, 3, 6)]
        facts += [f for f in covered("s", range(0, 7)) if not (f.channel == "codes" and f.first in (0, 3, 6))]
        facts += [F("s", 0, 5, "support", 5, 9), F("s", 1, 6, "support", 1, 4)]
        known = xa.A3Known("s", 0, "lot", "L1", "leak", "codes", True)
        self.assertEqual(xa.a3_interval(known, xa.FactIndex(facts), 5), (2, 4))
        prior = {1: 50, 2: 10, 3: 2, 4: 1}
        self.assertEqual(xa.a3_predict(known, xa.FactIndex(facts), 5, prior), (2, True))
        self.assertEqual(xa.mode_in(prior, 1, 4), 1)

    def test_a3_reads_the_zeros_after_the_newest_week(self) -> None:
        # a confirm over weeks 0..5 with support '<k' (k = 3) whose newest week is 2: weeks 3..5 hold nothing
        window = [F("s", 0, 5, "support", 1, 2), F("s", 2, 2, "support", 1, None)]
        zeros = [F("s", w, w, "support", 0, 0) for w in (3, 4, 5)]
        after = xa.A3Known("s", 4, "lot", "L1", "leak", "codes", False)
        self.assertEqual(xa.a3_interval(after, xa.FactIndex(window), 3), (1, 2))
        self.assertIsNone(xa.a3_interval(after, xa.FactIndex(window + zeros), 3))
        self.assertEqual(xa.a3_predict(after, xa.FactIndex(window + zeros), 3, {1: 3, 2: 4}), (2, False))
        # the text channel covered with no text cell and a presence in week 0: the newest week's count is pinned
        text = [f for f in covered("s", range(0, 6)) if f.channel == "text_only"]
        at_newest = xa.A3Known("s", 2, "lot", "L1", "leak", "codes", True)
        facts = [*window, *zeros, *text, F("s", 0, 0, "presence", 1, None)]
        self.assertEqual(xa.a3_interval(at_newest, xa.FactIndex(facts), 3), (1, 1))
        self.assertEqual(xa.a3_interval(at_newest, xa.FactIndex([*window, *zeros, *text]), 3), (1, 2))

    def test_a3_falls_back_to_the_prior_on_an_empty_interval(self) -> None:
        facts = [F("s", 0, 0, "n", 1, 2, channel="codes"), F("s", 0, 0, "support", 5, None)]
        known = xa.A3Known("s", 0, "lot", "L1", "leak", "codes", True)
        self.assertIsNone(xa.a3_interval(known, xa.FactIndex([*facts, *covered("s", range(0, 1))]), 3))
        self.assertEqual(xa.a3_predict(known, xa.FactIndex([*facts, *covered("s", range(0, 1))]), 3, {1: 1, 2: 0}),
                         (1, False))

    def test_a4_falls_back_at_suppressed_counts_and_decides_on_exact_ones(self) -> None:
        known = xa.A4Known("s", 2, "lot", "L1", "leak", "codes")
        suppressed = xa.FactIndex([F("s", 2, 2, "n", 1, 2, channel="codes"),
                                   F("s", 2, 2, "n_reporters", 1, 2, channel="codes")])
        self.assertEqual(xa.a4_predict(known, suppressed, False), (False, False))
        self.assertEqual(xa.a4_predict(known, suppressed, True), (True, False))
        one = xa.FactIndex([F("s", 2, 2, "n", 2, 2, channel="codes"),
                            F("s", 2, 2, "n_reporters", 1, 1, channel="codes")])
        self.assertEqual(xa.a4_predict(known, one, False), (True, True))
        distinct = xa.FactIndex([F("s", 2, 2, "n", 2, 2, channel="codes"),
                                 F("s", 2, 2, "n_reporters", 2, 2, channel="codes")])
        self.assertEqual(xa.a4_predict(known, distinct, True), (False, True))
        other_channel = xa.FactIndex([F("s", 2, 2, "n_reporters", 1, 1, channel="text_only")])
        self.assertEqual(xa.a4_predict(known, other_channel, False), (False, False))

    def test_a5_finds_an_injected_name_and_otherwise_equals_its_baseline(self) -> None:
        names = ["BERGER", "NOVAK", "OKAFOR"]
        prior = {"BERGER": 5, "NOVAK": 9, "OKAFOR": 1}
        targets = [(xa.A5Known("s", w, "f"), name) for w, name in ((1, "BERGER"), (2, "OKAFOR"), (3, "NOVAK"))]
        injected = xa.FactIndex([F("s", w, w, "string", 0, None, entity=None, predicate=None, text=name)
                                 for (k, name), w in zip(targets, (1, 2, 3))])
        empty = xa.FactIndex([F("s", 1, 9, "string", 0, None, entity=None, predicate=None, text="lot L1 leak")])
        hits = [xa.outcome(("c", k.week), xa.a5_predict(k, injected, names, prior)[0], xa.prior_mode(prior), name,
                           True) for k, name in targets]
        self.assertEqual(sum(o.correct for o in hits), 3)
        misses = [xa.outcome(("c", k.week), xa.a5_predict(k, empty, names, prior)[0], xa.prior_mode(prior), name,
                             False) for k, name in targets]
        self.assertEqual([xa.value("A5", o) for o in misses], [0.0, 0.0, 0.0])
        self.assertEqual(xa.a5_predict(targets[0][0], empty, names, prior), ("NOVAK", False))

    def test_a6_maps_verdicts_to_presence(self) -> None:
        self.assertEqual(xa.a6_predict("confirm"), (True, True))
        self.assertEqual(xa.a6_predict("refute"), (True, True))
        self.assertEqual(xa.a6_predict("unknown"), (False, True))
        self.assertEqual(xa.a6_predict(None), (False, False))

    def test_no_attack_function_takes_a_truth_argument(self) -> None:
        for fn in (xa.a1_score, xa.a1_predict, xa.a2_predict, xa.a2_prior_prediction, xa.a3_interval, xa.a3_predict,
                   xa.a4_predict, xa.a5_predict, xa.a6_predict):
            with self.subTest(fn=fn.__name__):
                names = set(inspect.signature(fn).parameters)
                self.assertFalse(names & {"truth", "member", "members", "actual", "label", "outcome"}, names)


class A1PerTypeTests(unittest.TestCase):
    """Audit round 4, finding 3: an artifact type's A1 entries read only the keys of the channels it can carry and,
    for A1_calibrated, the threshold calibrated on that type's own shadow facts (never the cells union's)."""

    MIXED = xa.A1Known("s", 4, "m", (("lot", "L1", "leak", "codes"), ("lot", "L2", "leak", "text_only")), False)
    OTHER = xa.A1Known("s", 4, "o", (("lot", "L3", "leak", "codes"), ("lot", "L2", "leak", "text_only")), True)

    def indexes(self) -> dict[str, xa.FactIndex]:
        codes = [F("s", 4, 4, "n", 1, 2, channel="codes")]
        text = [F("s", 4, 4, "n", 1, 2, entity=("lot", "L2"), channel="text_only")]
        return {"cells_codes": xa.FactIndex(codes), "cells_text": xa.FactIndex(text), "all": xa.FactIndex(codes + text),
                "allowed_fields_reference": xa.FactIndex(codes)}

    def test_the_channels_each_type_carries(self) -> None:
        self.assertEqual(xa.A1_CHANNELS, {"cells_codes": ("codes",), "cells_text": ("text_only",),
                                          "allowed_fields_reference": ("codes",)})
        self.assertEqual(set(xa.A1_CALIBRATED_TYPES),
                         {"cells_codes", "cells_text", "all", "allowed_fields_reference", "allowed_plus_all"})
        index = self.indexes()["cells_codes"]
        # the structural cap the review found: on a codes-only index a member with a text key cannot reach 1
        self.assertEqual(xa.a1_score(self.MIXED, index), 0.5)
        self.assertEqual(xa.a1_score(self.MIXED, index, xa.A1_CHANNELS["cells_codes"]), 1.0)
        self.assertEqual(xa.a1_score(self.OTHER, index, xa.A1_CHANNELS["cells_codes"]), 0.0)
        text_only = xa.A1Known("s", 4, "t", (("lot", "L2", "leak", "text_only"),), True)
        self.assertIsNone(xa.a1_score(text_only, index, xa.A1_CHANNELS["cells_codes"]))

    def test_a1_calibrated_needs_a_shadow_analogue_and_a1_fixed_does_not(self) -> None:
        for typ in xa.ARTIFACT_TYPES:
            calibrated = xa.applicability("default", "pipeline", 3, "A1_calibrated", typ)
            fixed = xa.applicability("default", "pipeline", 3, "A1_fixed", typ)
            with self.subTest(typ=typ):
                if typ == "usage_summary":
                    self.assertEqual(calibrated, ("not_applicable", xa.USAGE_REASON))
                    self.assertEqual(fixed, ("not_applicable", xa.USAGE_REASON))
                elif typ in xa.A1_CALIBRATED_TYPES:
                    self.assertEqual((calibrated, fixed), (("applicable", None), ("applicable", None)))
                else:
                    self.assertEqual(calibrated, ("not_applicable", xa.A1_SHADOW_REASON))
                    self.assertEqual(fixed, ("applicable", None))
        for typ in xa.K1_TYPES:
            self.assertEqual(xa.applicability("k1_reference", "derived", 1, "A1_calibrated", typ), ("applicable", None))

    def test_evaluate_applies_each_types_own_threshold_and_channels(self) -> None:
        pack = load_pack("device_quality")
        shadow = x5.Shadow(thresholds={}, a1_targets=0, a2=xa.A2Prior({}, {}, {}, {}), a3={}, a3_cells=0, a4_pairs=0,
                           a4_same=0, a5={f: {"X": 1} for f in x5.a5_fields(pack)}, a5_targets=0, a6_share={},
                           a6_windows=0, predicate_of={})
        targets = x5.Targets(a1=[(self.MIXED, True, (1, "s", 4)), (self.OTHER, False, (1, "s", 4))])
        pairs = [(a, t) for a in ("A1_calibrated", "A1_fixed") for t in ("cells_codes", "cells_text", "all")]

        def correct(thresholds: dict[str, float]) -> dict[tuple[str, str], list[bool]]:
            acc: x5.Outcomes = {}
            x5.evaluate(acc, pairs, self.indexes(), targets, shadow, thresholds=thresholds, k_target=1, pack=pack)
            return {key: [o.correct for o in outs] for key, outs in acc.items()}

        own = correct({"cells_codes": 1.0, "cells_text": 1.0, "all": 1.0})
        self.assertEqual(own[("A1_fixed", "cells_codes")], [True, True])      # scores 1 and 0 on the codes keys
        self.assertEqual(own[("A1_calibrated", "cells_codes")], [True, True])
        self.assertEqual(own[("A1_fixed", "cells_text")], [True, False])      # both score 1 on the shared text key
        self.assertEqual(own[("A1_calibrated", "all")], [True, True])         # 1 and 0.5 on every key
        moved = correct({"cells_codes": 1.0, "cells_text": 1.0, "all": 0.5})
        self.assertEqual(moved[("A1_calibrated", "all")], [True, False])
        self.assertEqual(moved[("A1_calibrated", "cells_codes")], own[("A1_calibrated", "cells_codes")])
        with self.assertRaises(KeyError):
            correct({"all": 1.0})                                              # no borrowed threshold

    def test_shadow_thresholds_are_calibrated_per_type_on_its_own_facts(self) -> None:
        settings_types = {"variant": ["cells_codes", "cells_text", "all", "allowed_fields_reference", "allowed_plus_all"],
                          "k1_reference": ["cells_codes", "cells_text", "all"],
                          "drop_lt_k": ["cells_codes", "cells_text", "all"]}
        self.assertEqual(x5.a1_threshold_types("variant", ["all", "verdicts_passive", "cells_text"]),
                         ["cells_text", "all"])
        for pid in PACKS:
            pack = load_pack(pid)
            world = x5.build_world(pack, 12, 400)
            variant = x5.Variant("default", "pipeline", pack, pack.egress.k, 1)
            settings = [x5.CellSetting("variant", pack.egress.k), x5.CellSetting("k1_reference", 1),
                        x5.CellSetting("drop_lt_k", pack.egress.k, drop=True)]
            calls: list[list[float | None]] = []

            def recording(scores: Any, members: Any) -> float:
                calls.append(list(scores))
                return real(scores, members)

            real = xa.calibrate
            with mock.patch.object(xa, "calibrate", recording):
                shadow = x5.shadow_stats(variant, [world], settings, pack.egress.k,
                                         [t for t in xa.ARTIFACT_TYPES if t != "verdicts_passive"])
            self.assertEqual({name: list(v) for name, v in shadow.thresholds.items()}, settings_types)
            # recomputed apart: each type's own shadow facts, only the keys of its channels, in the same order
            rows = x5.lexical_rows(pack, world.members, world.master)
            targets, _ = x5.a1_targets(world, x5.record_keys(variant, world))
            members = [m for _, m, _ in targets]
            reference = x5.Extractor(pack, world).reference(x5.r_mf_cells(
                pack, world.members, master_data=world.master, last_week=world.iso(world.last_week)))
            expected, mixed = [], 0
            for setting in settings:
                bodies = x5.rebuild_bodies(rows, pack, world, setting)
                missing = setting.k - 1 if setting.drop else 0
                codes = x5.Extractor(pack, world).cells("cells_codes", bodies, CHANNELS[:1], missing_hi=missing,
                                                        k=setting.k)
                text = x5.Extractor(pack, world).cells("cells_text", bodies, CHANNELS[1:], missing_hi=missing,
                                                       k=setting.k)
                facts = {"cells_codes": codes, "cells_text": text, "all": codes + text,
                         "allowed_fields_reference": reference, "allowed_plus_all": codes + text + reference}
                for typ in settings_types[setting.name]:
                    index = xa.FactIndex(facts[typ])
                    scores = [xa.a1_score(known, index, xa.A1_CHANNELS.get(typ)) for known, _, _ in targets]
                    expected.append(scores)
                    with self.subTest(pack=pid, setting=setting.name, typ=typ):
                        self.assertEqual(shadow.thresholds[setting.name][typ], xa.calibrate(scores, members))
                if setting.name == "variant":
                    index = xa.FactIndex(codes)
                    for known, member, _ in targets:
                        if not member or not any(ch == "codes" for *_, ch in known.keys):
                            continue
                        # a member's own codes keys all have a cell, whatever its text keys
                        self.assertEqual(xa.a1_score(known, index, xa.A1_CHANNELS["cells_codes"]), 1.0, known.ref)
                        mixed += any(ch == "text_only" for *_, ch in known.keys)
            self.assertEqual(calls, expected, pid)
            self.assertGreater(mixed, 0, pid)

    def test_the_tiny_run_carries_a_threshold_per_type_and_no_borrowed_one(self) -> None:
        doc, prereg = tiny_doc(), tiny_prereg()
        types = prereg["artifact_types"]
        shadow = doc["packs"]["device_quality"]["variants"]["default"]["shadow"]
        self.assertEqual(sorted(shadow["a1_thresholds"]), sorted(["variant", "k1_reference", *x5.SIMULATED]))
        for name, per_type in shadow["a1_thresholds"].items():
            self.assertEqual(sorted(per_type), sorted(x5.a1_threshold_types(name, types)), name)
        results = doc["results"]["device_quality"]["default"]
        for typ in types:
            with self.subTest(typ=typ):
                if typ in xa.A1_CALIBRATED_TYPES:
                    self.assertEqual(results["A1_calibrated"][typ]["status"], "run")
                elif typ != "usage_summary":
                    self.assertEqual((results["A1_calibrated"][typ]["status"], results["A1_calibrated"][typ]["reason"]),
                                     ("not_applicable", xa.A1_SHADOW_REASON))
                    self.assertEqual(results["A1_fixed"][typ]["status"], "run")
        broken = copy.deepcopy(doc)
        broken["packs"]["device_quality"]["variants"]["default"]["shadow"]["a1_thresholds"]["variant"] = 1.0
        self.assertTrue(x5.union_problems(x5.results_schema(prereg), broken, ("code_dirty",)))


# --------------------------------------------------------------------------------------------------- A6 probe

class A6ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="collective-x5-a6-"))
        cls.pack = load_pack("device_quality")
        cls.world = x5.build_world(cls.pack, 5, 200)
        cls.ctx = build_context(cls.pack, cls.world.members, cls.world.master, cls.tmp, mode="fake", seed=5,
                                routing=None, sites=cls.world.sites)
        run_stages(cls.ctx)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe(self, targets: dict[str, list[tuple[str, str, str]]], start: date) -> tuple[Any, int, int]:
        hq = self.tmp / "hq"
        before = (len(read_log(hq / "receive.jsonl")), len(read_log(hq / "questions.jsonl")))
        windows = x5.a6_windows(self.world, self.pack.egress.min_window_weeks)
        result = x5.a6_probe(self.pack, self.tmp, self.ctx.clock, sites=self.world.sites, master=self.world.master,
                             targets=targets, windows=windows, world=self.world, start_day=start, max_days=100)
        return result, *before

    def test_every_window_is_answered_in_ceil_w_over_b_days_from_the_day_after_as_of(self) -> None:
        site = sorted(self.world.sites)[0]
        t, e = x5.askable(self.pack, self.world.master[site])[0]
        windows = x5.a6_windows(self.world, self.pack.egress.min_window_weeks)
        as_of = date.fromisoformat(self.ctx.as_of)
        result, receive, asked = self.probe({site: [(t, e, "leak")]}, as_of + timedelta(days=1))
        budget = self.pack.egress.question_budget_per_entity_per_day
        self.assertEqual(result.days_needed, math.ceil(len(windows) / budget))
        self.assertEqual(result.budget_responses, 0)
        self.assertEqual(len(result.windows), len(windows))
        self.assertTrue(all(w[-1] is not None for w in result.windows))
        new_verdicts = [r for r in read_log(self.tmp / "hq" / "receive.jsonl")[receive:]]
        new_questions = [r for r in read_log(self.tmp / "hq" / "questions.jsonl")[asked:]]
        self.assertEqual({r["artifact_type"] for r in new_verdicts}, {"verdict"})
        self.assertEqual({r["artifact_type"] for r in new_questions}, {"question"})
        self.assertEqual(len(new_verdicts), len(windows))

    def test_a_budget_answer_is_asked_again_later_and_never_read_as_absence(self) -> None:
        site = sorted(self.world.sites)[1]
        t, e = x5.askable(self.pack, self.world.master[site])[1]
        day = date.fromisoformat(self.ctx.as_of) + timedelta(days=30)
        budget = self.pack.egress.question_budget_per_entity_per_day
        self.ctx.clock.set(day.isoformat() + DAY_TIME)
        edge_site = EdgeSite(self.pack, site, self.tmp / "edge", runtime=None, clock=self.ctx.clock,
                             master_data=self.world.master[site], hq_dir=self.tmp / "hq")
        try:
            verifier = SiteVerifier(edge_site, runtime=None, clock=self.ctx.clock, demo_seed=5)
            for i in range(budget):                         # HQ's own questions use the day's budget first
                window = {"start_week": self.world.iso(i), "end_week": self.world.iso(i + 7)}
                verifier.answer(build_question(self.pack, entity_type=t, entity_id=e, predicate="crack",
                                               window=window, as_of=day.isoformat()))
        finally:
            edge_site.close()
        result, _, _ = self.probe({site: [(t, e, "leak")]}, day)
        windows = x5.a6_windows(self.world, self.pack.egress.min_window_weeks)
        self.assertEqual(result.budget_responses, 1)
        self.assertEqual(result.days_needed, 1 + math.ceil(len(windows) / budget))
        self.assertTrue(all(w[-1] in ("confirm", "refute", "unknown") for w in result.windows))


# --------------------------------------------------------------------------------------------------- statistics

def outcomes(values: list[tuple[int, bool, bool]]) -> list[xa.Outcome]:
    return [xa.Outcome((c,), a, b, True) for c, a, b in values]


class StatsLabelTests(unittest.TestCase):
    def test_label_boundaries(self) -> None:
        self.assertEqual(xa.label(0.0, 0.3), xa.INCONCLUSIVE)
        self.assertEqual(xa.label(1e-12, 0.3), xa.LEAK)
        self.assertEqual(xa.label(-0.05, 0.05), xa.AT_CHANCE)
        self.assertEqual(xa.label(-0.0500001, 0.04), xa.INCONCLUSIVE)
        self.assertEqual(xa.label(-0.04, 0.0500001), xa.INCONCLUSIVE)
        self.assertEqual(xa.label(None, None), xa.INCONCLUSIVE)
        self.assertEqual(xa.LABELS, ("leak", "at_chance", "inconclusive"))

    def test_no_target_is_inconclusive_without_an_advantage(self) -> None:
        entry = xa.summarise("A2", [], B=100, seed="s", primary=True, family_size=8)
        self.assertEqual((entry["n_targets"], entry["advantage"], entry["label"], entry["label_bonferroni"]),
                         (0, None, xa.INCONCLUSIVE, xa.INCONCLUSIVE))

    def test_the_bonferroni_interval_is_nested_and_from_the_same_seed(self) -> None:
        data = outcomes([(i % 40, i % 3 == 0, i % 5 == 0) for i in range(400)])
        entry = xa.summarise("A2", data, B=300, seed="x5:t", primary=True, family_size=8)
        adv, adj = entry["advantage"], entry["advantage_bonferroni"]
        self.assertEqual(adj["alpha"], 0.00625)
        self.assertLessEqual(adj["ci_low"], adv["ci_low"])
        self.assertGreaterEqual(adj["ci_high"], adv["ci_high"])
        again = xa.summarise("A2", data, B=300, seed="x5:t", primary=True, family_size=8)
        self.assertEqual(entry, again)
        exploratory = xa.summarise("A2", data, B=300, seed="x5:t", primary=False, family_size=8)
        self.assertEqual((exploratory["label_bonferroni"], exploratory["advantage_bonferroni"],
                          exploratory["family"]), (None, None, "exploratory"))

    def test_values_and_the_incremental_entry(self) -> None:
        self.assertEqual(xa.value("A1_calibrated", xa.Outcome((1,), True, False, True)), 1.0)
        self.assertEqual(xa.value("A1_fixed", xa.Outcome((1,), False, True, True)), -1.0)
        self.assertEqual(xa.value("A3", xa.Outcome((1,), True, True, True)), 0.0)
        self.assertEqual(xa.value("A3", xa.Outcome((1,), False, True, True)), -1.0)
        plus = outcomes([(i, True, False) for i in range(20)])
        reference = outcomes([(i, i % 2 == 0, False) for i in range(20)])
        inc = xa.incremental("A2", plus, reference, B=200, seed="s")
        self.assertEqual(inc["n_targets"], 20)
        self.assertAlmostEqual(inc["advantage"]["estimate"], 0.5)
        with self.assertRaises(ValueError):
            xa.incremental("A2", plus, reference[:5], B=200, seed="s")
        a1 = xa.summarise("A1_calibrated", outcomes([(i, i < 15, False) for i in range(20)]), B=200, seed="s",
                          primary=False, family_size=1)
        self.assertEqual((a1["accuracy"], a1["baseline_accuracy"]), (0.75, 0.5))
        self.assertAlmostEqual(a1["advantage"]["estimate"], 0.5)

    def test_primary_entries_use_primary_b_and_the_others_exploratory_b(self) -> None:
        doc, prereg = tiny_doc(), tiny_prereg()
        primary = {(p["pack"], p["attack"], p["variant"], p["artifact_type"]) for p in prereg["primary"]}
        self.assertEqual(len(primary), 4)
        for pid, variants in doc["results"].items():
            for vid, attacks in variants.items():
                for attack, types in attacks.items():
                    for typ, entry in types.items():
                        if entry["status"] != "run" or entry["advantage"] is None:
                            continue
                        is_primary = (pid, attack, vid, typ) in primary
                        self.assertEqual(entry["primary"], is_primary)
                        self.assertEqual(entry["advantage"]["B"], prereg["bootstrap"]["primary_B" if is_primary
                                                                                      else "exploratory_B"])
                        self.assertEqual(entry["advantage"]["seed"], f"x5:{pid}:{vid}:{attack}:{typ}")


# --------------------------------------------------------------------------------------------------- prereg and run

class PreregRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="collective-x5-prereg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_closed_prereg_schema_refuses_tampering(self) -> None:
        doc = tiny_prereg()
        self.assertEqual(x5.prereg_problems(doc), [])
        for path, value in ((("extra",), 1), (("world", "sites"), 7), (("rules", "alpha"), 0.1),
                            (("stamps", "measurement"), True), (("bootstrap", "seed"), "bad seed"),
                            (("primary",), [{"pack": "device_quality", "attack": "A5", "variant": "default",
                                              "artifact_type": "all"}])):
            bad = copy.deepcopy(doc)
            node = bad
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path):
                self.assertTrue(x5.prereg_problems(bad))

    def test_the_seed_rule(self) -> None:
        commit = "1" * 40
        seeds, examined = x5.derive_seeds(commit, 8)
        expected = [1 + int(__import__("hashlib").sha256(f"x5:{commit}:{i}".encode()).hexdigest()[:8], 16) % 100000
                    for i in range(len(examined))]
        self.assertEqual([c["value"] for c in examined], expected)
        self.assertEqual(seeds, [c["value"] for c in examined if c["status"] == "accepted"])
        self.assertEqual(len(set(seeds)), 8)
        with mock.patch.object(x5, "seed_candidate", side_effect=lambda c, i: [5, 5, 7, 9][i]):
            got, seen = x5.derive_seeds(commit, 3)
        self.assertEqual(got, [5, 7, 9])
        self.assertEqual([c["status"] for c in seen], ["accepted", "duplicate", "accepted", "accepted"])
        with self.assertRaises(x5.UsageError):
            x5.derive_seeds("unknown", 8)
        overlapping = [*DETERMINISM]
        overlapping[overlapping.index("--shadow-seeds") + 1] = "11"
        code, _, err = run_main(["prereg", "--run-id", "p", "--runs-dir", str(self.tmp), "--allow-dirty",
                                 *overlapping])
        self.assertEqual(code, 2)
        self.assertIn("disjoint", err)

    def test_prereg_refuses_dirty_code_and_an_existing_run_directory(self) -> None:
        with mock.patch.object(x5, "code_dirty", return_value=True):
            code, _, err = run_main(["prereg", "--run-id", "p", "--runs-dir", str(self.tmp), *DETERMINISM])
        self.assertEqual(code, 2)
        self.assertIn("--allow-dirty", err)
        self.assertFalse((self.tmp / "x5").exists())
        (self.tmp / "x5" / "p").mkdir(parents=True)
        code, _, err = run_main(["prereg", "--run-id", "p", "--runs-dir", str(self.tmp), "--allow-dirty",
                                 *DETERMINISM])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def tampered(self, edit: Any) -> Path:
        doc = tiny_prereg()
        edit(doc)
        path = self.tmp / "tampered.json"
        path.write_text(canonical_dumps(doc) + "\n", encoding="utf-8")
        return path

    def run_prereg(self, path: Path) -> tuple[int, str]:
        code, _, err = run_main(["run", "--prereg", str(path), "--run-id", "r", "--runs-dir", str(self.tmp),
                                 "--work-dir", str(self.tmp / "work"), "--allow-dirty"])
        return code, err

    def test_run_refuses_changed_pins_and_names_each(self) -> None:
        def pins(doc: dict[str, Any]) -> None:
            doc["code_hash"] = "0" * 64
            doc["packs"][0]["detector_hash"] = "0" * 64
        code, err = self.run_prereg(self.tampered(pins))
        self.assertEqual(code, 2)
        self.assertIn("code_hash", err)
        self.assertIn("device_quality.detector_hash", err)

        def copies(doc: dict[str, Any]) -> None:
            doc["packs"][0]["variants"][0]["hashes"]["config_hash"] = "0" * 64
            doc["world"]["digests"][1]["world_digest"] = "0" * 64
        code, err = self.run_prereg(self.tampered(copies))
        self.assertEqual(code, 2)
        self.assertIn("device_quality.default.config_hash", err)
        d = tiny_prereg()["world"]["digests"][1]
        self.assertIn(f"device_quality/{d['kind']}/{d['seed']}", err)
        self.assertFalse((self.tmp / "work").exists())
        self.assertFalse((self.tmp / "x5").exists())

    def test_dry_runs_create_nothing_and_print_the_estimate(self) -> None:
        before = sorted(p.as_posix() for p in self.tmp.rglob("*"))
        code, out, _ = run_main(["prereg", "--run-id", "p", "--runs-dir", str(self.tmp), "--packs",
                                 "device_quality,claims_integrity", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("dry-run: experiments.x5_inference prereg"))
        code, out, _ = run_main(["run", "--prereg", str(FIXTURE["prereg"]), "--run-id", "r", "--runs-dir",
                                 str(self.tmp), "--work-dir", str(self.tmp / "work"), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertRegex(out.splitlines()[-1], r"^estimate: pipelines 1 \(cap 60; over cap: none\), worlds 1, shadow "
                                               r"worlds 1, a6 questions ~\d+, bootstrap cells ~\d+ \(primary 2x4 at "
                                               r"B=100, others at B=100\)$")
        self.assertEqual(sorted(p.as_posix() for p in self.tmp.rglob("*")), before)

    def test_the_cap_marks_whole_variants_not_run_in_order(self) -> None:
        variants = ["default", "k2", "k10", "rmd_flipped", "minus_type", "volume"]
        plan, needed = x5.pipeline_plan(["a", "b"], variants, seeds=[1, 2, 3, 4, 5], volume_seeds=[1, 2],
                                        volume_shadow={"a": [9], "b": [9]}, max_pipelines=13)
        self.assertEqual(needed, 54)
        self.assertEqual(plan[("a", "default")], (True, None))
        self.assertEqual(plan[("a", "k2")], (True, None))
        self.assertEqual(plan[("a", "k10")], (False, x5.CAP_REASON))
        self.assertEqual(plan[("a", "volume")], (True, None))
        self.assertEqual(plan[("b", "default")], (False, x5.CAP_REASON))
        again, _ = x5.pipeline_plan(["a", "b"], variants, seeds=[1, 2, 3, 4, 5], volume_seeds=[1, 2],
                                    volume_shadow={"a": [9], "b": []}, max_pipelines=60)
        self.assertEqual(again[("b", "volume")], (False, x5.NO_VOLUME_SHADOW_REASON))


# --------------------------------------------------------------------------------------------------- x5.json

class SchemaScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc, cls.prereg = tiny_doc(), tiny_prereg()
        cls.schema = x5.results_schema(cls.prereg)

    def problems(self, doc: Any) -> list[Any]:
        return x5.union_problems(self.schema, doc, ("code_dirty",))

    def test_the_tiny_run_validates_and_carries_its_stamps(self) -> None:
        self.assertEqual(FIXTURE["code"], 0, FIXTURE["err"])
        self.assertEqual(self.problems(self.doc), [])
        self.assertEqual((self.doc["stamps"]["synthetic"], self.doc["stamps"]["internal_only"],
                          self.doc["stamps"]["measurement"]), (True, True, False))

    def test_the_schema_refuses_tampering(self) -> None:
        cases = []
        extra = copy.deepcopy(self.doc)
        extra["results"]["device_quality"]["default"]["A2"]["all"]["extra"] = 1
        cases.append(extra)
        missing = copy.deepcopy(self.doc)
        del missing["results"]["device_quality"]["default"]["A2"]["packets"]
        cases.append(missing)
        bad_label = copy.deepcopy(self.doc)
        bad_label["results"]["device_quality"]["default"]["A2"]["all"]["label"] = "no leak"
        cases.append(bad_label)
        long_digest = copy.deepcopy(self.doc)
        long_digest["packs"]["device_quality"]["variants"]["default"]["worlds"][0]["world_digest"] = "a" * 64
        cases.append(long_digest)
        for i, doc in enumerate(cases):
            with self.subTest(case=i):
                self.assertTrue(self.problems(doc))

    def test_portability_and_the_content_hash(self) -> None:
        blob = (canonical_dumps(self.doc) + "\n").encode("utf-8")
        self.assertEqual(portability_problems(blob, forbidden=[str(FIXTURE["tmp"]), str(ROOT)]), [])
        planted = copy.deepcopy(self.doc)
        planted["results"]["device_quality"]["default"]["A6"]["packets"]["reason"] = "/home/someone/x"
        self.assertIn("absolute_path", portability_problems(canonical_dumps(planted).encode(), forbidden=[]))
        planted["results"]["device_quality"]["default"]["A6"]["packets"]["reason"] = "a" * 64
        self.assertIn("hex64", portability_problems(canonical_dumps(planted).encode(), forbidden=[]))
        moved = {**self.doc, "created_at": "x", "run_id": "other", "timings": {"total_s": 1.0}}
        self.assertEqual(content_hash(moved, x5.CONTENT_HASH_EXCLUDES), self.doc["content_hash"])
        self.assertEqual(content_hash(self.doc, x5.CONTENT_HASH_EXCLUDES), self.doc["content_hash"])

    def test_the_self_scan_refuses_narrative_and_person_values(self) -> None:
        pack = load_pack("device_quality")
        world = x5.build_world(pack, 11, 400)
        inputs = x5.ScanInputs()
        inputs.add(world, pack)
        blob = (canonical_dumps(self.doc) + "\n").encode("utf-8")
        self.assertEqual(x5.self_scan(blob, {pack.id: pack}, {pack.id: inputs}), [])
        record = next(r for r in world.records if len(r["narrative"]) > 60)
        person = next(v for v in record["persons"].values() if isinstance(v, str) and " " in v)
        for planted, problem in ((record["narrative"][5:40], "narrative text"),
                                 (person.upper(), "a person, surname or reporter value"),
                                 (person.split()[-1].lower(), "a person, surname or reporter value"),
                                 (record["reporter"], "a person, surname or reporter value")):
            doc = copy.deepcopy(self.doc)
            doc["results"]["device_quality"]["default"]["A6"]["packets"]["reason"] = f"see {planted} here"
            data = (canonical_dumps(doc) + "\n").encode("utf-8")
            with self.subTest(problem=problem, planted=planted[:12]):
                self.assertIn(f"{pack.id}: {problem}", x5.self_scan(data, {pack.id: pack}, {pack.id: inputs}))

    def test_every_matrix_entry_is_present_with_its_shape(self) -> None:
        prereg = self.prereg
        for pid in [p["id"] for p in prereg["packs"]]:
            for vid in prereg["variants"]:
                for attack in prereg["attacks"]:
                    for typ in prereg["artifact_types"]:
                        entry = self.doc["results"][pid][vid][attack][typ]
                        with self.subTest(entry=(pid, vid, attack, typ)):
                            self.assertIn(entry["status"], xa.STATUSES)
                            if entry["status"] != "run":
                                self.assertTrue(entry["reason"])
                                continue
                            for key in ("n_targets", "coverage", "accuracy", "accuracy_wilson",
                                        "baseline_accuracy", "advantage", "label"):
                                self.assertIsNotNone(entry[key], key)
                            if typ == "allowed_plus_all":
                                self.assertIsNotNone(entry["incremental"])
        self.assertEqual(self.doc["rules"]["applicability"], prereg["applicability"])
        self.assertEqual({k: v for k, v in self.doc["rules"].items()
                          if k not in ("bootstrap", "applicability", "selection")}, prereg["rules"])
        self.assertEqual(self.doc["primary_family"]["entries"], prereg["primary"])
        self.assertEqual(self.doc["not_covered"], prereg["not_covered_mapping"])


# --------------------------------------------------------------------------------------------------- determinism

class DeterminismTests(unittest.TestCase):
    def test_two_processes_under_different_hash_seeds_write_the_same_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="collective-x5-det-") as tmp:
            prereg = make_prereg(Path(tmp) / "runs", "det", DETERMINISM)
            docs = []
            for seed in ("0", "1"):
                r = subprocess.run([sys.executable, "-m", "mycelic.collective.experiments.x5_inference", "run",
                                    "--prereg", str(prereg), "--run-id", f"det-{seed}", "--runs-dir",
                                    str(Path(tmp) / "runs"), "--work-dir", str(Path(tmp) / f"work-{seed}"),
                                    "--allow-dirty"], cwd=ROOT, capture_output=True, text=True, timeout=300,
                                   env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": seed,
                                        "TMPDIR": tmp})
                self.assertIn(r.returncode, (0, 1), r.stderr)
                self.assertFalse((Path(tmp) / f"work-{seed}").exists())
                docs.append(strict_load((Path(tmp) / "runs" / "x5" / f"det-{seed}" / "x5.json").read_bytes()))
            self.assertEqual(docs[0]["content_hash"], docs[1]["content_hash"])
            strip = [{k: v for k, v in d.items() if k not in ("created_at", "run_id", "timings")} for d in docs]
            self.assertEqual(canonical_dumps(strip[0]), canonical_dumps(strip[1]))


# --------------------------------------------------------------------------------------------------- controls

class ControlTests(unittest.TestCase):
    def results(self, positive_label: str, negative: tuple[str, float, float]) -> dict[str, Any]:
        def entry(label: str, lo: float, hi: float) -> dict[str, Any]:
            return {**xa.empty_entry("run", None), "label": label,
                    "advantage": {"estimate": 0.0, "ci_low": lo, "ci_high": hi, "B": 1, "seed": "s",
                                  "method": "m", "n_clusters": 1}}
        out: dict[str, Any] = {"default": {"A5": {"all": entry(*negative)}}, "k1_reference": {}, "a5_injected": {}}
        for attack in ("A1_calibrated", "A2", "A3", "A4"):
            out["k1_reference"][attack] = {"all": entry(positive_label, 0.1, 0.3)}
        out["a5_injected"]["A5"] = {"all": entry(xa.LEAK, 0.1, 0.3)}
        return {"device_quality": out}

    def test_the_control_rules_and_their_exit_codes(self) -> None:
        good = x5.controls_block(self.results(xa.LEAK, (xa.AT_CHANCE, -0.01, 0.01)), ["device_quality"])
        self.assertTrue(good["all_hold"])
        self.assertEqual(x5.control_exit(good), (0, []))
        blind = x5.controls_block(self.results(xa.INCONCLUSIVE, (xa.AT_CHANCE, -0.01, 0.01)), ["device_quality"])
        code, messages = x5.control_exit(blind)
        self.assertEqual(code, 2)
        self.assertIn("harness cannot detect a known leak: device_quality/k1_reference/A1_calibrated", messages)
        for negative in ((xa.LEAK, 0.1, 0.2), (xa.INCONCLUSIVE, 0.01, 0.2), (xa.AT_CHANCE, 0.001, 0.04)):
            with self.subTest(negative=negative):
                bad = x5.controls_block(self.results(xa.LEAK, negative), ["device_quality"])
                self.assertFalse(bad["all_hold"])
                self.assertEqual(x5.control_exit(bad)[0], 1)

    def test_the_tiny_run_holds_its_controls_where_it_can(self) -> None:
        controls = tiny_doc()["controls"]
        self.assertTrue(all(c["holds"] for c in controls["negative"]))
        self.assertEqual(len(controls["negative"]), 1)
        a5 = [c for c in controls["positive"] if c["variant"] == "a5_injected"]
        self.assertEqual([c["holds"] for c in a5], [True])

    def test_injected_cells_pass_the_extractor_and_the_boundary_would_refuse_them(self) -> None:
        pack = load_pack("device_quality")
        world = x5.build_world(pack, 11, 400)
        targets = x5.a5_targets(pack, world)[:3]
        bundle = {"after": None, "as_of": "2025-01-05", "cells": [], "closed_through": world.iso(world.last_week),
                  "config_hash": pack.config_hash, "k": pack.egress.k, "pack": pack.id, "schema_version": 1,
                  "site": targets[0][0].site}
        injected = x5.inject_a5([bundle], [t for t in targets if t[0].site == bundle["site"]], pack, world)
        self.assertTrue(injected[0]["cells"])
        facts = x5.Extractor(pack, world).cells("cells_text", injected, CHANNELS[1:])
        names = {f.text for f in facts if f.field == "string"}
        self.assertTrue({t[1] for t in targets if t[0].site == bundle["site"]} <= names)
        problem = check_artifact(pack, bundle["site"], "cells_bundle", injected[0])
        self.assertIsNotNone(problem)
        self.assertEqual(problem[1], "id_format")


# --------------------------------------------------------------------------------------------------- LEAKAGE section 12

COLUMN_FIELDS = {"n": ("n_targets",), "coverage": ("coverage",), "accuracy": ("accuracy",),
                 "baseline": ("baseline_accuracy",), "advantage": ("advantage", "estimate"), "label": ("label",),
                 "Bonferroni label": ("label_bonferroni",), "cells below k": ("cells_lt_k",),
                 "members with keys": ("members_with_keys",),
                 "members with every key present": ("members_all_keys_present_share",),
                 "exact cells": ("cells_exact",), "exact counts correct": ("exact_correct_share",),
                 "expected": ("expected",), "holds": ("holds",), "item": ("item",),
                 "artifact type": ("artifact_type",), "reason": ("reason",), "cells before": ("cells_before",),
                 "cells after": ("cells_after",), "removed cell share": ("removed_cell_share",)}


def expected_cell(column: str, node: dict[str, Any]) -> str:
    """The independent parser's display of one cell (not x5_inference's own ``cell``)."""
    if column in ("95% CI", "Bonferroni CI"):
        interval = node["advantage" if column == "95% CI" else "advantage_bonferroni"]
        if interval is None:
            return "null"
        return f"[{format_value(interval['ci_low'])}, {format_value(interval['ci_high'])}]"
    if column == "attacks":
        return ", ".join(node["attacks"])
    value: Any = node
    for key in COLUMN_FIELDS[column]:
        value = None if value is None else value[key]
    return format_value(value)


def parse_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    tables, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        if lines[i].startswith("| Pointer |"):
            header = [c.strip() for c in lines[i].strip("|").split(" | ")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("| `"):
                rows.append([c.strip() for c in re.split(r"(?<!\\)\|", lines[i].strip())[1:-1]])
                i += 1
            tables.append((header, rows))
        else:
            i += 1
    return tables


class LeakageSectionTests(unittest.TestCase):
    def test_leakage_md_has_section_12_with_its_fixed_texts(self) -> None:
        text = LEAKAGE_MD.read_text(encoding="utf-8")
        self.assertIn("## 12. X5: leakage beyond text (the published figure)", text)
        section = text[text.index("## 12. X5"):]
        flat = " ".join(section.split())
        for fixed in (xa.STATEMENT, *xa.SENTENCES.values(), *xa.BAR_SENTENCES.values(),
                      *xa.LABEL_RULES.values()):
            self.assertIn(" ".join(fixed.split()), flat)
        self.assertEqual(text.count(MARK_BEGIN), 1)
        self.assertEqual(text.count(MARK_END), 1)
        self.assertLess(text.index(MARK_BEGIN), text.index(MARK_END))

    def test_the_rehearsal_before_the_freeze_is_disclosed(self) -> None:
        # regression (audit round 4, finding 2): a full rehearsal wrote an outcome before the code and bar froze, and
        # the docs said the bar was fixed before running and the plan was written before any x5.json existed
        text = LEAKAGE_MD.read_text(encoding="utf-8")
        section = " ".join(text[text.index("## 12. X5"):].split())
        self.assertNotIn("against a pass bar fixed before running", section)
        disclosure = section[section.index("**Rehearsal disclosure.**"):]
        self.assertLess(section.index("**Rehearsal disclosure.**"), section.index(MARK_BEGIN))
        for fact in ("seeds derived from 202dd531", "n = 1000", "explicit seeds 101 and shadow 102",
                     "exited 0 after 632 s", "A run exits 0 only after writing `x5.json` and `leakage_section.md`",
                     "the outcome counts as seen", "deleted without a sha256",
                     "pre-registered only with respect to the B3b prereg's own seeds",
                     '"No X5 prereg was made and no x5.json written" is wrong'):
            self.assertIn(fact, disclosure)
        self.assertIn(x5.REHEARSAL_NOTE, x5.PREREG_NOTES)
        self.assertFalse([n for n in x5.PREREG_NOTES if "before any attack outcome" in n])
        self.assertEqual(tiny_prereg()["notes"], x5.PREREG_NOTES)
        integration = " ".join((ROOT / "docs" / "collective" / "INTEGRATION.md").read_text(encoding="utf-8").split())
        self.assertNotIn("(written before any B3 prereg or `x5.json` exists)", integration)
        self.assertIn("**Correction (audit round 4):** that run was the whole experiment", integration)
        runbook = " ".join((ROOT / "docs" / "collective" / "RUNBOOK.md").read_text(encoding="utf-8").split())
        self.assertIn("**A rehearsal is an outcome.**", runbook)

    def test_every_row_of_the_rendered_section_resolves_and_matches(self) -> None:
        doc = tiny_doc()
        section = (FIXTURE["run"] / "leakage_section.md").read_text(encoding="utf-8")
        self.assertEqual(section, x5.leakage_section(doc))
        self.assertTrue(section.startswith(doc["bar"]["sentence"]))
        self.assertIn(xa.STATEMENT, section)
        family = doc["primary_family"]["size"]
        for p in doc["primary_family"]["entries"]:
            entry = doc["results"][p["pack"]]["default"][p["attack"]]["all"]
            adv = entry["advantage"]
            sentence = xa.SENTENCES[(entry["label"], entry["label_bonferroni"])].format(
                pack=p["pack"], attack_name=xa.ATTACK_NAMES[p["attack"]], estimate=format_value(adv["estimate"]),
                ci_low=format_value(adv["ci_low"]), ci_high=format_value(adv["ci_high"]), family_size=family)
            self.assertIn(sentence, section)
        tables = parse_tables(section)
        self.assertGreater(len(tables), 6)
        rows = 0
        for header, table_rows in tables:
            for row in table_rows:
                self.assertEqual(len(row), len(header))
                ptr = row[0].strip("`")
                status, node = lookup(doc, ptr)
                self.assertEqual(status, "ok", ptr)
                for column, shown in zip(header[1:], row[1:]):
                    self.assertEqual(shown, expected_cell(column, node), (ptr, column))
                rows += 1
        self.assertGreater(rows, 20)

    def test_the_bar_sentence_follows_the_stored_labels(self) -> None:
        doc = tiny_doc()
        primary = [doc["results"][p["pack"]]["default"][p["attack"]]["all"] for p in doc["primary_family"]["entries"]]
        self.assertEqual(doc["bar"]["outcome"], x5.bar_outcome(primary))
        leak = {"label": xa.LEAK, "label_bonferroni": xa.LEAK}
        chance = {"label": xa.AT_CHANCE, "label_bonferroni": xa.AT_CHANCE}
        weak = {"label": xa.AT_CHANCE, "label_bonferroni": xa.INCONCLUSIVE}
        self.assertEqual(x5.bar_outcome([chance, leak]), "fails")
        self.assertEqual(x5.bar_outcome([chance, chance]), "met")
        self.assertEqual(x5.bar_outcome([chance, weak]), "undecided")
        self.assertEqual(x5.bar_outcome([]), "undecided")

    def test_the_not_covered_mapping_covers_every_x5_item(self) -> None:
        mapping = x5.not_covered_mapping()
        items = [item for item in leakage.NOT_COVERED if "X5" in item]
        self.assertEqual(len(items), 7)
        self.assertEqual([m["item"] for m in mapping], items)
        for m in mapping:
            self.assertTrue(m["attacks"] or (m["reason"] or "").startswith("not attacked: "), m["item"])
            self.assertTrue(set(m["attacks"]) <= set(xa.ATTACKS))


# --------------------------------------------------------------------------------------------------- import closure

class ImportClosureTests(unittest.TestCase):
    def test_every_module_loaded_with_x5_inference_is_in_the_code_hash(self) -> None:
        code = ("import json, sys\nimport mycelic.collective.experiments.x5_inference\n"
                "print(json.dumps(sorted(m.__file__ for n, m in sys.modules.items()\n"
                "    if n.startswith('mycelic.collective') and getattr(m, '__file__', None))))\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        loaded = {Path(f).resolve().relative_to(ROOT.resolve()).as_posix() for f in json.loads(r.stdout)}
        covered_files = set(code_files(x5.X5_CODE_FILES))
        self.assertEqual(sorted(loaded - covered_files), [])
        self.assertIn("mycelic/collective/experiments/x5_attacks.py", covered_files)
        self.assertEqual(covered_files, set(x5.x5_code_files()))


if __name__ == "__main__":
    unittest.main()

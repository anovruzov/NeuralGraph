"""G6: E2, pushdown verification against central reading on planted synthetic worlds (``experiments/e2_pushdown``).

Every run here is synthetic and same-author and uses fakes (the lexical judge behind an in-process fake at every site,
and the rehearsal-only fake central handlers), so ``measurement`` is false and the 0.90 bar verdict is withheld. No
figure a test prints or asserts is a measurement or a product number. Temporary directories only; nothing connects
anywhere (the routing files of the refusal tests name a loopback port that is never contacted).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.evaluate import harness as H
from mycelic.collective.evaluate.plant import load_plant, plant
from mycelic.collective.experiments import e2_pushdown as E2
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.leakage import CANARY_PREFIX, Artifact, Manifest, scan
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import BUILTIN_ROOT, load_pack
from mycelic.collective.pushdown.gate import STATUS_RANK, STATUSES
from tests.mycelic.test_collective_edge import pack_copy
from tests.mycelic.test_collective_guards import path_snapshot

ROOT = Path(__file__).resolve().parents[2]
DQ = load_pack("device_quality")
E2_PLANT = BUILTIN_ROOT / "device_quality" / "fixtures" / "plant_e2_smoke.json"
RAW_FLAGS = ("--allow-external-raw", "synthetic", "--data-label", "synthetic")
SEEDS = (1, 2, 3, 4, 5)
SYNTHETIC_NOTE = "synthetic, same-author world; fakes everywhere; not a measurement"


def cli(main: Callable[[list[str]], int], argv: Sequence[Any]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def write_prereg(runs: Path, seeds: str, run_id: str) -> Path:
    """An X1 prereg over the device pack: 6 sites, 52 weeks, evaluation weeks 20..51."""
    code, _, err = cli(H.main, ["prereg", "--pack", "device_quality", "--seeds", seeds, "--sites", 6, "--weeks", 52,
                                "--eval-from", 20, "--eval-to", 51, "--tie-salt", "e2-smoke", "--detector-author",
                                "Mycelic Engineering", "--run-id", run_id, "--runs-dir", runs, "--allow-dirty"])
    assert code == 0, err
    return runs / "x1" / run_id / "prereg.json"


def e2_argv(prereg: Path, runs: Path, run_id: str, *extra: Any) -> list[Any]:
    return ["run", "--x1-prereg", prereg, "--plant", E2_PLANT, "--run-id", run_id, "--runs-dir", runs, "--allow-dirty",
            *extra]


def world_texts(seeds: Sequence[int]) -> tuple[list[str], set[str]]:
    """Every narrative and record ref of the planted E2 worlds of ``seeds``."""
    spec = load_plant(E2_PLANT, DQ)
    narratives, refs = [], set()
    for seed in seeds:
        world = generate(DQ, seed, 6, 52)
        for r in list(world.records) + list(plant(world, spec, DQ).records):
            narratives.append(r["narrative"])
            refs.add(r["record_ref"])
    return narratives, refs


def shingle_report(paths: Mapping[str, Path], narratives: list[str]) -> dict[str, Any]:
    manifest = Manifest(pack_id=DQ.id, config_hash=DQ.config_hash, prefix=CANARY_PREFIX, canaries=())
    return scan([Artifact("e2_run_file", label, path=p) for label, p in paths.items()], manifest, narratives, DQ)


class E2SmokeTests(unittest.TestCase):
    """The plant_e2_smoke fixture (same-author, not blind, never a result) over five seeds, fakes everywhere."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.runs = Path(cls._tmp.name) / "runs"
        prereg = write_prereg(cls.runs, ",".join(map(str, SEEDS)), "pre")
        cls.code, cls.out, cls.err = cli(E2.main, e2_argv(prereg, cls.runs, "smoke", *RAW_FLAGS, "--bootstrap-b", 1000))
        cls.run_dir = cls.runs / "e2" / "smoke"
        cls.doc = json.loads((cls.run_dir / "e2.json").read_text(encoding="utf-8")) if cls.code == 0 else None
        print(f"\n[{SYNTHETIC_NOTE}] {cls.out.strip()}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.assertEqual(self.code, 0, self.out + self.err)

    def test_the_file_is_schema_valid_and_stamped(self) -> None:
        d = self.doc
        self.assertEqual(E2.e2_problems(d), [])
        self.assertEqual((d["kind"], d["schema_version"], d["run_id"]), ("e2_pushdown", 1, "smoke"))
        self.assertEqual(d["stamps"], {"synthetic": True, "internal_only": True, "measurement": False,
                                       "same_author_pack": DQ.same_author_as_code, "below_protocol_minimum": False,
                                       "allow_dirty": True, "secret_mode": "seeded-demo", "data_label": "synthetic"})
        self.assertEqual({k: d["hashes"][k] for k in DQ.hashes()}, DQ.hashes())
        self.assertEqual(d["hashes"]["code_hash"], E2.e2_code_hash())
        self.assertEqual(d["condition_labels"], E2.CONDITION_LABELS)
        self.assertIn("STRATEGY's R for this task", d["condition_labels"]["central_allowed"])
        self.assertIn("raw record text", d["condition_labels"]["central_raw"])
        self.assertIn("synthetic worlds only", d["condition_labels"]["central_raw"])
        self.assertEqual((d["endpoints"]["site_judge"], d["endpoints"]["central_judge"]), ("fake", "fake"))
        self.assertTrue(all(p.endswith(":fake") for p in d["endpoints"]["providers"]))
        self.assertEqual(d["settings"]["k"], 40)
        self.assertTrue(any("Synthetic and same-author" in note for note in d["notes"]))

    def test_the_four_conditions_and_the_paired_ratio(self) -> None:
        d = self.doc
        self.assertEqual(sorted(d["conditions"]), sorted(E2.CONDITIONS))
        for name in E2.CONDITIONS:
            with self.subTest(condition=name):
                c = d["conditions"][name]
                for key in ("ap", "ap_ci_low", "ap_ci_high", "precision_at_k", "p_ci_low", "p_ci_high"):
                    self.assertIsInstance(c[key], float, key)
                self.assertLessEqual(c["ap_ci_low"], c["ap_ci_high"])
                self.assertLessEqual(c["p_ci_low"], c["p_ci_high"])
        self.assertEqual((d["bootstrap"]["B"], d["bootstrap"]["method"], d["bootstrap"]["n"]),
                         (1000, "paired percentile", d["candidates"]["total"]))
        ratio = d["ratio"]
        self.assertEqual((ratio["numerator"], ratio["denominator"], ratio["epsilon"]),
                         ("pushdown", "central_raw", 0.01))
        self.assertIsInstance(ratio["estimate"], float)
        self.assertLessEqual(ratio["ci_low"], ratio["ci_high"])
        self.assertAlmostEqual(ratio["estimate"],
                               d["conditions"]["pushdown"]["ap"] / d["conditions"]["central_raw"]["ap"])

    def test_raw_text_bytes_per_condition(self) -> None:
        d = self.doc
        raw = d["raw_text_bytes"]
        self.assertEqual((raw["stats_only"], raw["central_allowed"], raw["pushdown"]), (0, 0, 0))
        self.assertGreater(raw["central_raw"], 0)
        scan_doc = d["raw_text_scan"]
        self.assertEqual(scan_doc["method"], E2.SCAN_METHOD)
        self.assertGreater(scan_doc["central_raw"], 0)                   # the in-memory payloads carry narrative
        self.assertEqual((scan_doc["central_allowed"], scan_doc["pushdown"]), (0, 0))
        self.assertGreater(scan_doc["artifacts"]["pushdown"]["items"], 0)
        self.assertGreater(scan_doc["artifacts"]["central_allowed"]["bytes"], 0)

    def test_the_bar_is_withheld_under_fakes(self) -> None:
        d = self.doc
        self.assertEqual((d["stamps"]["measurement"], d["verdict"], d["verdicts_withheld"]), (False, None, True))
        self.assertIn("fake", d["withheld_reason"])
        self.assertIn("verdict=withheld", self.out)
        self.assertIn("(synthetic, internal only)", self.out)

    def test_candidates_items_and_scores(self) -> None:
        d = self.doc
        cands = d["candidates"]
        self.assertGreaterEqual(cands["total"], 300)
        self.assertEqual((cands["seeds"], [s["seed"] for s in cands["by_seed"]]), (5, list(SEEDS)))
        self.assertEqual(sum(s["candidates"] for s in cands["by_seed"]), cands["total"])
        self.assertTrue(all(s["candidates"] == min(60, s["detected"]) for s in cands["by_seed"]))
        self.assertEqual(sum(cands["by_label"].values()), cands["total"])
        self.assertGreater(cands["by_label"]["true"], 0)
        self.assertEqual(len(d["items"]), cands["total"])
        self.assertEqual(d["items"], sorted(d["items"], key=lambda i: (i["seed"], i["key"])))
        for item in d["items"]:
            self.assertIn(item["label"], E2.LABELS)
            self.assertIn(item["status"], STATUSES)
            self.assertEqual(item["conclusion_id"], "c-" + item["question_id"][:32])
            self.assertEqual(item["scores"]["pushdown"] // E2.STATUS_SCALE, STATUS_RANK[item["status"]])
            if item["central_raw_records"] == 0:
                self.assertEqual(item["scores"]["central_raw"], 0)
            if item["central_allowed_records"] == 0:
                self.assertEqual(item["scores"]["central_allowed"], 0)
            self.assertLessEqual(item["central_raw_records"], E2.CENTRAL_MAX_RECORDS)
        pd = d["pushdown"]
        self.assertEqual(sum(pd["statuses"].values()), cands["total"])
        self.assertEqual(pd["statuses"], {s: sum(1 for i in d["items"] if i["status"] == s) for s in STATUSES})
        self.assertEqual((pd["budget_unknowns"], pd["timeouts"], pd["errors"]),
                         (pd["verdicts"]["unknown"]["budget"], pd["verdicts"]["unknown"]["timeout"],
                          pd["verdicts"]["unknown"]["error"]))
        self.assertGreater(pd["routes"]["sibling"], 0)

    def test_resolvability_of_the_supported_conclusions(self) -> None:
        res = self.doc["pushdown"]["resolvability"]
        self.assertEqual(res["supported"], self.doc["pushdown"]["statuses"]["supported"])
        self.assertGreater(res["supported"], 0)
        self.assertEqual(res["share"], res["resolvable"] / res["supported"])
        self.assertGreaterEqual(res["share"], 0.95)
        self.assertIsInstance(res["extraction_miss_confirmations"], int)

    def test_no_narrative_record_ref_or_text_in_the_run_files(self) -> None:
        narratives, refs = world_texts(SEEDS)
        files = {"e2.json": self.run_dir / "e2.json", "central.ledger.jsonl": self.run_dir / "central.ledger.jsonl"}
        report = shingle_report(files, narratives)
        self.assertEqual((report["hit_count"], report["shingle_overlap_bytes"]), (0, 0))
        self.assertEqual(sorted(entry["label"] for entry in report["scanned"]), sorted(files))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9-]+-[0-9]{6}", ref) for ref in refs))
        for label, path in files.items():
            tokens = set(re.findall(r"[a-z0-9-]+-[0-9]{6}", path.read_text(encoding="utf-8")))
            with self.subTest(file=label):
                self.assertEqual(tokens & refs, set())
        central = read_ledger(self.run_dir / "central.ledger.jsonl")
        self.assertTrue(central)
        self.assertTrue(all(re.fullmatch(r"e2:[0-9]+:(raw|allowed)", row["ref"]) for row in central))
        self.assertEqual({row["task"] for row in central}, set(E2.CENTRAL_TASKS))
        for ledger in (self.run_dir / "work").glob("seed-*/edge/site-*.ledger.jsonl"):
            self.assertTrue(all(re.fullmatch(r"j:[0-9a-f]{12}:[0-9]+", row["ref"]) for row in read_ledger(ledger)))
        self.assertEqual([p.name for p in self.run_dir.glob("*.ledger.jsonl")], ["central.ledger.jsonl"])

    def test_the_content_hash(self) -> None:
        d = self.doc
        self.assertEqual(d["content_hash_excludes"], list(E2.CONTENT_HASH_EXCLUDES))
        self.assertEqual(d["content_hash"], E2.content_hash(d))
        self.assertNotEqual(d["content_hash"], E2.content_hash({**d, "items": d["items"][1:]}))
        self.assertEqual(d["content_hash"], E2.content_hash({**d, "run_id": "other", "created_at": "x",
                                                             "timings": {}, "paths": {}}))


class E2UnitTests(unittest.TestCase):
    def test_the_bar_verdict_is_withheld_without_a_measurement_or_a_ratio(self) -> None:
        ratio = {"estimate": 0.95, "ci_low": 0.91, "ci_high": 0.99}
        verdict, reason = E2.bar_verdict(False, ratio, 0)
        self.assertIsNone(verdict)
        self.assertIn("fake", reason)
        verdict, reason = E2.bar_verdict(True, {"estimate": None, "ci_low": None, "ci_high": None}, 0)
        self.assertIsNone(verdict)
        self.assertIn("undefined", reason)
        verdict, reason = E2.bar_verdict(True, ratio, 0)
        self.assertEqual((verdict, reason), ({"bar": 0.9, "ratio": 0.95, "ratio_at_least_bar": True,
                                              "ci_low_at_least_bar": True, "pushdown_raw_text_bytes_zero": True,
                                              "pass": True}, None))
        self.assertFalse(E2.bar_verdict(True, {**ratio, "ci_low": 0.89}, 0)[0]["pass"])
        self.assertFalse(E2.bar_verdict(True, ratio, 1)[0]["pass"])
        self.assertTrue(E2.bar_verdict(True, {"estimate": 0.9, "ci_low": 0.9, "ci_high": 1.0}, 0)[0]["pass"])
        self.assertFalse(E2.bar_verdict(True, {"estimate": 0.95, "ci_low": None, "ci_high": None}, 0)[0]["pass"])

    def test_central_allowed_reads_only_the_allowed_fields(self) -> None:
        from tests.mycelic.test_collective_evaluate import RecordingRecord
        world = generate(DQ, 1, 6, 34)
        source = next(r for r in world.records if r["codes"] and r["entities"]["product"])
        reads: list[str] = []
        record = RecordingRecord({**source, "entities": RecordingRecord(source["entities"], reads, "entities.")}, reads)
        view = E2.allowed_view(DQ, record)
        allowed = set(DQ.egress.central_allowed_fields) | {"entities"}
        self.assertEqual(sorted(set(reads) - allowed), [])
        self.assertIn("entities.product", reads)
        self.assertEqual(sorted(view), ["codes", "entities", "received_date", "site"])
        self.assertNotIn(source["narrative"], json.dumps(view))
        self.assertEqual(view["entities"]["product"], list(source["entities"]["product"]))
        with tempfile.TemporaryDirectory() as tmp:
            fields = [f for f in DQ.egress.central_allowed_fields if f not in ("codes", "entities.product")]
            narrowed = pack_copy(Path(tmp), "device_quality", {("egress.json", "central_allowed_fields"): fields})
        reads.clear()
        view = E2.allowed_view(narrowed, record)
        self.assertEqual((view["codes"], view["entities"]["product"]), ([], []))
        self.assertNotIn("codes", reads)
        self.assertNotIn("entities.product", reads)

    def test_the_fake_central_score(self) -> None:
        self.assertEqual(E2._score([]), 0)
        self.assertEqual(E2._score([{"site": "a"}] * 4), 34)
        self.assertEqual(E2._score([{"site": s} for s in "abcde" for _ in range(3)]), 100)


class E2RefusalTests(unittest.TestCase):
    """Every refusal exits 2 and writes nothing; one seed is below the protocol minimum."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.runs = cls.tmp / "runs"
        cls.prereg = write_prereg(cls.runs, "1", "one")
        cls.site_ids = json.loads(cls.prereg.read_text(encoding="utf-8"))["world"]["site_ids"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def refused(self, run_id: str, *extra: Any, message: str | None = None) -> str:
        before = path_snapshot(self.tmp)
        code, out, err = cli(E2.main, e2_argv(self.prereg, self.runs, run_id, *extra))
        self.assertEqual(code, 2, out + err)
        self.assertEqual(path_snapshot(self.tmp), before)
        self.assertFalse((self.runs / "e2" / run_id).exists())
        if message is not None:
            self.assertIn(message, err)
        return err

    def site_routing(self, name: str, changes: Mapping[str, Mapping[str, Any]]) -> Path:
        """A directory of one routing file per prereg site, each routing judge_record to its own site boundary;
        ``changes`` replaces a site's endpoint spec (or adds an escalation endpoint under ``escalate``)."""
        directory = self.tmp / name
        directory.mkdir()
        for sid in self.site_ids:
            endpoint = {"provider": "openai_compat", "boundary": f"site:{sid}", "base_url": "http://127.0.0.1:9/v1",
                        "model": "m-tag"}
            route: dict[str, Any] = {"endpoint": "judge"}
            endpoints = {"judge": endpoint}
            change = dict(changes.get(sid, {}))
            if "escalate" in change:
                endpoints["up"] = {**endpoint, **change.pop("escalate")}
                route["escalate_to"] = "up"
            endpoint.update(change)
            (directory / f"{sid}.json").write_text(json.dumps({
                "schema_version": 1, "endpoints": endpoints, "routes": {"judge_record": route}}), encoding="utf-8")
        return directory

    def test_central_raw_needs_both_synthetic_flags(self) -> None:
        for i, flags in enumerate(((), ("--allow-external-raw", "synthetic"), ("--data-label", "synthetic"),
                                   ("--allow-external-raw", "partner", "--data-label", "synthetic"),
                                   ("--allow-external-raw", "synthetic", "--data-label", "public"))):
            with self.subTest(flags=flags):
                self.refused(f"flags-{i}", *flags, message=E2.RAW_FLAGS_MESSAGE)

    def test_a_judge_route_that_may_leave_its_site_is_refused(self) -> None:
        first, second = self.site_ids[0], self.site_ids[1]
        for i, change in enumerate(({"boundary": "central"}, {"boundary": f"site:{second}"},
                                    {"escalate": {"boundary": "central"}},
                                    {"escalate": {"boundary": "external"}})):
            with self.subTest(change=change):
                directory = self.site_routing(f"routing-{i}", {first: change})
                self.refused(f"route-{i}", *RAW_FLAGS, "--site-routing", directory,
                             message=f"the judge route of site {first} may leave the site")
        fake = self.site_routing("routing-fake", {first: {"provider": "fake"}})
        self.refused("route-fake", *RAW_FLAGS, "--site-routing", fake)
        missing = self.site_routing("routing-missing", {})
        (missing / f"{second}.json").unlink()
        self.refused("route-missing", *RAW_FLAGS, "--site-routing", missing)

    def test_a_central_routing_on_a_site_or_fake_endpoint_is_refused(self) -> None:
        for i, endpoint in enumerate(({"provider": "openai_compat", "boundary": f"site:{self.site_ids[0]}",
                                       "base_url": "http://127.0.0.1:9/v1", "model": "m-tag"},
                                      {"provider": "fake", "boundary": "central"})):
            path = self.tmp / f"central-{i}.json"
            path.write_text(json.dumps({"schema_version": 1, "endpoints": {"c": endpoint},
                                        "routes": {task: {"endpoint": "c"} for task in E2.CENTRAL_TASKS}}),
                            encoding="utf-8")
            with self.subTest(endpoint=endpoint["boundary"]):
                self.refused(f"central-{i}", *RAW_FLAGS, "--central-routing", path)

    def test_a_small_bootstrap_is_refused(self) -> None:
        self.refused("boot", *RAW_FLAGS, "--bootstrap-b", 999, message="--bootstrap-b must be >= 1000")

    def test_below_the_protocol_minimum_needs_min_candidates(self) -> None:
        code, out, err = cli(E2.main, e2_argv(self.prereg, self.runs, "below", *RAW_FLAGS, "--bootstrap-b", 1000))
        self.assertEqual(code, 2, out + err)
        self.assertIn("(protocol minimum 300 over 5); pass --min-candidates N to run below it (stamped)", err)
        self.assertFalse((self.runs / "e2" / "below" / "e2.json").exists())
        code, out, err = cli(E2.main, e2_argv(self.prereg, self.runs, "too-few", *RAW_FLAGS, "--bootstrap-b", 1000,
                                              "--min-candidates", 1000))
        self.assertEqual(code, 2, out + err)
        self.assertIn("fewer than --min-candidates 1000", err)
        self.assertFalse((self.runs / "e2" / "too-few" / "e2.json").exists())
        code, out, err = cli(E2.main, e2_argv(self.prereg, self.runs, "stamped", *RAW_FLAGS, "--bootstrap-b", 1000,
                                              "--min-candidates", 10))
        self.assertEqual(code, 0, out + err)
        d = json.loads((self.runs / "e2" / "stamped" / "e2.json").read_text(encoding="utf-8"))
        self.assertEqual(E2.e2_problems(d), [])
        self.assertEqual((d["stamps"]["below_protocol_minimum"], d["candidates"]["seeds"],
                          d["settings"]["min_candidates"]), (True, 1, 10))
        self.assertEqual((d["verdict"], d["verdicts_withheld"]), (None, True))
        before = path_snapshot(self.tmp)
        code, out, err = cli(E2.main, e2_argv(self.prereg, self.runs, "stamped", *RAW_FLAGS, "--min-candidates", 10))
        self.assertEqual(code, 2, out + err)
        self.assertIn("run directory already exists", err)
        self.assertEqual(path_snapshot(self.tmp), before)

    def test_a_dry_run_creates_nothing(self) -> None:
        before = path_snapshot(self.tmp)
        code, out, err = cli(E2.main, e2_argv(self.prereg, self.runs, "dry", *RAW_FLAGS, "--dry-run"))
        self.assertEqual(code, 0, out + err)
        self.assertTrue(out.startswith("dry-run: experiments.e2_pushdown run"), out)
        self.assertIn(f"would write: {self.runs / 'e2' / 'dry' / 'e2.json'}", out)
        code, out, err = cli(E2.main, e2_argv(self.tmp / "missing.json", self.runs, "dry", *RAW_FLAGS,
                                              "--site-routing", self.tmp / "no-dir", "--dry-run"))
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"would need: prereg file {self.tmp / 'missing.json'}", out)
        self.assertIn(f"would need: site routing directory {self.tmp / 'no-dir'}", out)
        self.assertEqual(path_snapshot(self.tmp), before)


class E2DeterminismTests(unittest.TestCase):
    def test_two_hash_seeds_give_the_same_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            prereg = write_prereg(runs, "1", "one")
            docs = []
            for hash_seed in ("0", "1"):
                run_id = f"det-{hash_seed}"
                argv = [str(a) for a in e2_argv(prereg, runs, run_id, *RAW_FLAGS, "--min-candidates", 10,
                                                "--top-n", 12, "--bootstrap-b", 1000)]
                r = subprocess.run([sys.executable, "-m", "mycelic.collective.experiments.e2_pushdown", *argv],
                                   cwd=ROOT, capture_output=True, text=True, timeout=600,
                                   env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": hash_seed})
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                docs.append(json.loads((runs / "e2" / run_id / "e2.json").read_text(encoding="utf-8")))
            self.assertEqual(docs[0]["candidates"]["total"], 12)
            self.assertEqual(docs[0]["content_hash"], docs[1]["content_hash"])
            self.assertEqual(docs[0]["items"], docs[1]["items"])
            self.assertNotEqual(docs[0]["run_id"], docs[1]["run_id"])


if __name__ == "__main__":
    unittest.main()

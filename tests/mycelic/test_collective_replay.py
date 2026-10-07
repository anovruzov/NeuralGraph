"""G5: the openFDA public replay on a test-generated, openFDA-shaped fixture: events derived from a planted G2 world and
recalls built by the test, both served by local OpenFDAStub servers and fetched with ``connectors.openfda.fetch``.

Nothing here touches api.fda.gov: the caches are synthetic (``data_label: synthetic``), so every replay.json says
``measurement: false``. Any figure a test prints is from a synthetic, same-author world and is not a measurement.
"""
from __future__ import annotations

import builtins
import contextlib
import io
import json
import math
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence
from unittest import mock

from mycelic.collective.connectors import openfda
from mycelic.collective.evaluate import plant as P
from mycelic.collective.evaluate.baselines import closing_date
from mycelic.collective.experiments import openfda_replay as R
from mycelic.collective.jsonio import canonical_dumps, sha256_hex
from mycelic.collective.packs.connector import field_values
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import load_pack
from tests.mycelic.test_collective_edge import pack_copy
from tests.mycelic.test_collective_guards import path_snapshot
from tests.mycelic.test_collective_week1 import OpenFDAStub

DQ = load_pack("device_quality")
LABEL = "public data, artificial partitioning, not a confidentiality demonstration"
PLANTED_CODE, IP_CODE, OTHER_CODE = "AAA", "BBB", "CCC"
PROBLEM_OF = {"ILL-0101": "Crack", "ILL-0201": "Fluid/Blood Leak", "ILL-0301": "Overheating of Device",
              "ILL-0601": "Occlusion Within Device", "ILL-0801": "Detachment of Device or Device Component"}
MAKER = "ACME Devices"
DESCRIPTION = "Description of Event or Problem"
SITES = [s["id"] for s in DQ.generator["sites"]]


def cli(argv: Sequence[Any]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = R.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def event(record: dict[str, Any], code: str, **changes: Any) -> dict[str, Any]:
    product = (record["entities"]["product"] or [None])[0]
    out = {"mdr_report_key": record["record_ref"], "date_received": record["received_date"].replace("-", ""),
           "event_location": record["site"].upper(),
           "device": [{"manufacturer_d_name": MAKER, "model_number": product,
                       "lot_number": (record["entities"]["lot"] or [None])[0], "device_report_product_code": code}],
           "product_problems": [PROBLEM_OF[c] for c in record["codes"] if c in PROBLEM_OF],
           "mdr_text": [{"text_type_code": DESCRIPTION, "text": record["narrative"]}]}
    out.update(changes)
    return out


def fetch(dataset: str, pages: dict[str, list[dict[str, Any]]], out: Path, date_from: str = "20240101",
          date_to: str = "20250131") -> None:
    with OpenFDAStub(pages) as stub:
        openfda.fetch(dataset, sorted(pages), date_from, date_to, out, base_url=stub.base_url, sleep=lambda s: None)


def prereg_argv(cache: Path, runs: Path, run_id: str = "pre", *extra: Any, pack: Any = "device_quality",
                partition: str = "event_location") -> list[Any]:
    return ["prereg", "--pack", pack, "--events-cache", cache, "--manufacturer", MAKER, "--manufacturer-field",
            "device[].manufacturer_d_name", "--partition-field", partition, "--date-from", "20240101", "--date-to",
            "20241229", "--tie-salt", "replay-test", "--saw-recall-outcomes", "no", "--run-id", run_id,
            "--runs-dir", runs, "--allow-dirty", *extra]


class Recorder:
    """Records Path.read_bytes and open() calls and the replay's write_json_atomic, in order."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []

    @contextlib.contextmanager
    def active(self) -> Any:
        read_bytes, real_open, write = Path.read_bytes, builtins.open, R.write_json_atomic
        log = self.log

        def recording_read(path: Path) -> bytes:
            log.append(("read", str(Path(path).resolve())))
            return read_bytes(path)

        def recording_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(file, (str, Path)):
                log.append(("open", str(Path(file).resolve())))
            return real_open(file, *args, **kwargs)

        def recording_write(path: Any, obj: Any) -> str:
            digest = write(path, obj)
            log.append(("write", str(Path(path).resolve())))
            return digest

        with mock.patch.object(Path, "read_bytes", recording_read), \
                mock.patch.object(builtins, "open", recording_open), \
                mock.patch.object(R, "write_json_atomic", recording_write):
            yield self

    def first(self, kinds: tuple[str, ...], under: Path) -> int | None:
        root = str(under.resolve())
        return next((i for i, (kind, path) in enumerate(self.log) if kind in kinds and path.startswith(root)), None)


class OpenFDAReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        world = generate(DQ, 3, 6, 52)
        spec = P.parse_plant({
            "kind": "plant_spec", "schema_version": 1, "pack": "device_quality", "prereg_sha256": None,
            "planted_by": "replay test", "planter_saw_detector_code": True, "notes": "",
            "patterns": [{"id": "p1", "entity_type": "product", "entity_id": "SD-40", "predicate": "overheat",
                          "sites": SITES[:3], "start_week": 30, "weeks": 8, "rate_per_week": 2,
                          "visibility": "narrative_only", "language": "en"}], "decoys": []}, DQ)
        planted = P.plant(world, spec, DQ)
        pages: dict[str, list[dict[str, Any]]] = {PLANTED_CODE: [], IP_CODE: [], OTHER_CODE: []}
        for r in world.records:
            product = (r["entities"]["product"] or ["SD-9"])[0]
            code = IP_CODE if product.upper().startswith("IP") else OTHER_CODE
            region = {"region": "north"} if r["record_ref"].endswith("0") else {}
            pages[code].append(event(r, code, **region))
        for r in planted.records:
            pages[PLANTED_CODE].append(event(r, PLANTED_CODE))
        base = pages[IP_CODE][0]
        pages[OTHER_CODE] += [
            {**base},                                                                    # same key on another page
            {**base, "mdr_report_key": "x-other", "device": [{"manufacturer_d_name": "OTHERCO"}]},
            {**base, "mdr_report_key": "x-case", "device": [{"manufacturer_d_name": MAKER.lower()}]},
            {**base, "mdr_report_key": "x-missing", "device": [{"model_number": "SD-9"}]},
            {**base, "mdr_report_key": "x-shape", "device": {"manufacturer_d_name": MAKER}},
            {k: v for k, v in {**base, "mdr_report_key": "x-nodate"}.items() if k != "date_received"},
            {**base, "mdr_report_key": "x-baddate", "date_received": "20241345"},
            {**base, "mdr_report_key": "x-late", "date_received": "20250115"},
        ]
        cls.pages = pages
        cls.events = cls.tmp / "events"
        fetch("event", pages, cls.events)
        cls.runs = cls.tmp / "runs"
        code, out, err = cli(prereg_argv(cls.events, cls.runs))
        assert code == 0, err
        cls.prereg = cls.runs / "replay" / "pre" / "prereg.json"
        cls.recorder = Recorder()
        with cls.recorder.active():
            code, out, err = cli(["signals", "--prereg", cls.prereg, "--run-id", "sig", "--runs-dir", cls.runs,
                                  "--allow-dirty"])
            assert code == 0, err
            cls.signals_dir = cls.runs / "replay" / "sig"
            cls.signals = json.loads((cls.signals_dir / "signals.json").read_text(encoding="utf-8"))
            cls.recalls = cls.tmp / "recalls"
            cls.recall_records = cls.build_recalls()
            fetch("recall", {PLANTED_CODE: cls.recall_records}, cls.recalls)
            cls.score_log_start = len(cls.recorder.log)
            code, out, err = cli(["score", "--prereg", cls.prereg, "--signals", cls.signals_dir, "--recalls-cache",
                                  cls.recalls, "--run-id", "score", "--runs-dir", cls.runs, "--allow-dirty"])
            assert code == 0, err
        cls.score_out = out
        cls.replay = json.loads((cls.runs / "replay" / "score" / "replay.json").read_text(encoding="utf-8"))
        print(f"\n[synthetic, same-author world; not a measurement] {out.strip()}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @classmethod
    def build_recalls(cls) -> list[dict[str, Any]]:
        first = min(a["available_date"] for a in cls.signals["channels"]["X"]["alerts"]
                    if PLANTED_CODE in a["product_codes"])
        cls.first_planted_alert = first
        d0 = date.fromisoformat(first)
        cls.init_after = (d0 + timedelta(days=10)).isoformat()
        cls.init_before = (d0 - timedelta(days=3)).isoformat()

        def recall(init: str, **changes: Any) -> dict[str, Any]:
            return {"product_code": PLANTED_CODE, "recalling_firm": MAKER, "event_date_initiated": init,
                    "root_cause_description": "Device Design", **changes}

        after = recall(cls.init_after)
        return [after, dict(after), recall(cls.init_before.replace("-", "")),
                recall(cls.init_after, recalling_firm="OTHERCO"), recall("2024/10/01"),
                recall(cls.init_after, product_code="ZZZ"), recall("2025-01-20"), recall("2024-03-01")]

    def channel(self, name: str) -> dict[str, Any]:
        return self.replay["channels"][name]["summary"]

    def item(self, init: str) -> dict[str, Any]:
        return next(i for i in self.replay["recalls"]["items"] if i["event_date_initiated"] == init)

    # ------------------------------------------------------------------ prereg
    def test_prereg_pins_and_has_no_recall_argument(self) -> None:
        doc = json.loads(self.prereg.read_text(encoding="utf-8"))
        self.assertEqual((doc["kind"], doc["label"]), ("replay_prereg", LABEL))
        self.assertEqual({k: doc["pack"][k] for k in DQ.hashes()}, DQ.hashes())
        self.assertEqual((doc["code_hash"], doc["code_files"]), (R.replay_code_hash(), R.replay_code_files()))
        self.assertIn("mycelic/collective/connectors/openfda.py", doc["code_files"])
        self.assertIn("mycelic/collective/experiments/openfda_replay.py", doc["code_files"])
        manifest = openfda.load_cache(self.events)
        self.assertEqual(doc["events_cache"]["manifest_sha256"], manifest.manifest_sha256)
        self.assertEqual(doc["events_cache"]["data_label"], "synthetic")
        self.assertEqual(doc["settings"]["recalling_firms"], [MAKER])
        self.assertFalse(doc["recall_outcomes_seen_before_prereg"])
        for name in ("prereg", "signals"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                R.main([name, "--help"])
            options = {word for word in out.getvalue().split() if word.startswith("--")}
            # a firm name and the analyst's declaration; never a recall cache or path
            self.assertEqual({o for o in options if "recall" in o},
                             {"--saw-recall-outcomes", "--recalling-firm"} if name == "prereg" else set(), name)

    def test_prereg_refusals_exit_2_and_write_nothing(self) -> None:
        before = path_snapshot(self.tmp)
        argvs = (prereg_argv(self.tmp / "absent-cache", self.runs, "p1"),
                 prereg_argv(self.events, self.runs, "p2", pack="claims_integrity"),
                 prereg_argv(self.recalls, self.runs, "p3"),
                 prereg_argv(self.events, self.runs, "pre"),
                 [*prereg_argv(self.events, self.runs, "p4"), "--manufacturer-field", "device..name"],
                 [*prereg_argv(self.events, self.runs, "p5"), "--min-partition-coverage", "0"],
                 [*prereg_argv(self.events, self.runs, "p6"), "--date-to", "20250301"],
                 [*prereg_argv(self.events, self.runs, "p7"), "--date-to", "20240301"],
                 [*prereg_argv(self.events, self.runs, "p8"), "--lookback-weeks", "0"])
        for argv in argvs:
            with self.subTest(argv=argv[-4:]):
                code, _, err = cli(argv)
                self.assertEqual(code, 2, err)
        with mock.patch.object(R, "code_dirty", return_value="unknown"):
            argv = [a for a in prereg_argv(self.events, self.runs, "p9") if a != "--allow-dirty"]
            code, _, err = cli(argv)
        self.assertEqual(code, 2)
        self.assertIn("--allow-dirty", err)
        self.assertEqual(path_snapshot(self.tmp), before)

    # ------------------------------------------------------------------ order
    def test_signals_and_phase1_are_written_before_the_recall_cache_is_read(self) -> None:
        log = self.recorder.log
        phase1 = self.signals_dir / "phase1.json"
        signals_write = self.recorder.first(("write",), self.signals_dir / "signals.json")
        phase1_write = self.recorder.first(("write",), phase1)
        first_recall = self.recorder.first(("read", "open"), self.recalls)
        self.assertIsNotNone(signals_write)
        self.assertIsNotNone(first_recall)
        self.assertLess(signals_write, phase1_write)
        self.assertLess(phase1_write, first_recall)
        recorded = json.loads(phase1.read_text(encoding="utf-8"))
        self.assertEqual(recorded["signals_sha256"],
                         sha256_hex((self.signals_dir / "signals.json").read_bytes()))
        self.assertEqual(recorded["signals_file"], "signals.json")
        score_reads = [path for kind, path in log[self.score_log_start:] if kind in ("read", "open")]
        signals_read = score_reads.index(str((self.signals_dir / "signals.json").resolve()))
        recall_read = next(i for i, p in enumerate(score_reads) if p.startswith(str(self.recalls.resolve())))
        self.assertLess(signals_read, recall_read)
        self.assertEqual(self.replay["signals_sha256"], recorded["signals_sha256"])

    def test_score_refuses_tampered_signals_without_opening_the_recall_cache(self) -> None:
        copy_dir = self.tmp / "tampered-signals"
        shutil.copytree(self.signals_dir, copy_dir, ignore=shutil.ignore_patterns("work"))
        data = (copy_dir / "signals.json").read_bytes()
        (copy_dir / "signals.json").write_bytes(data.replace(b'"measurement":false', b'"measurement":true '))
        recorder = Recorder()
        with recorder.active():
            code, _, err = cli(["score", "--prereg", self.prereg, "--signals", copy_dir, "--recalls-cache",
                                self.recalls, "--run-id", "tampered", "--runs-dir", self.runs, "--allow-dirty"])
        self.assertEqual(code, 2)
        self.assertIn("refusing to open the recalls", err)
        self.assertIsNone(recorder.first(("read", "open"), self.recalls))
        self.assertFalse((self.runs / "replay" / "tampered").exists())

    # ------------------------------------------------------------------ scoring
    def test_only_pre_initiation_alerts_count_as_found(self) -> None:
        after = self.item(self.init_after)
        x = after["by_channel"]["X"]
        self.assertTrue(x["found_pre"])
        self.assertEqual(x["first_signal"]["available_date"], self.first_planted_alert)
        self.assertEqual(x["first_signal"]["lead_days"], 10)
        self.assertLess(x["first_signal"]["available_date"], self.init_after)
        before = self.item(self.init_before)
        self.assertFalse(before["by_channel"]["X"]["found_pre"])
        self.assertIsNone(before["by_channel"]["X"]["first_signal"])
        self.assertGreater(before["by_channel"]["X"]["post_recall_alerts"], 0)
        summary = self.channel("X")
        self.assertEqual((summary["in_scope"], summary["found"], summary["recall_rate"]), (2, 1, 0.5))
        self.assertEqual(summary["median_lead_days"], 10)
        self.assertGreater(summary["post_recall_alerts"], 0)
        self.assertEqual(self.channel("S")["found"], 0)
        # the same alert is a pre-initiation signal of one recall and a post-recall alert of the other
        alert = next(a for a in self.signals["channels"]["X"]["alerts"]
                     if a["available_date"] == self.first_planted_alert and PLANTED_CODE in a["product_codes"])
        self.assertTrue(self.init_before <= alert["available_date"] < self.init_after)

    def test_false_alarms_cover_every_product_code_of_the_manufacturer(self) -> None:
        self.assertEqual(self.signals["product_codes"], [PLANTED_CODE, IP_CODE, OTHER_CODE])
        lookback, post = 26, 26
        windows = [(date.fromisoformat(i) - timedelta(days=7 * lookback), date.fromisoformat(i) + timedelta(
            days=7 * post)) for i in (self.init_after, self.init_before)]
        for name in ("X", "S", "R_mf"):
            alerts = self.signals["channels"][name]["alerts"]
            matched = [a for a in alerts if PLANTED_CODE in a["product_codes"]
                       and any(lo <= date.fromisoformat(a["available_date"]) <= hi for lo, hi in windows)]
            summary = self.channel(name)
            with self.subTest(channel=name):
                self.assertEqual(summary["false_alarms"], len(alerts) - len(matched))
                self.assertEqual(summary["false_alarms_per_week"],
                                 summary["false_alarms"] / self.signals["weeks"]["evaluated_weeks"])
                self.assertEqual(summary["denominator_note"], "false alarms per week count alerts on every product "
                                                              "code of the manufacturer, not only recalled ones")
        never_recalled = [a for a in self.signals["channels"]["X"]["alerts"]
                          if PLANTED_CODE not in a["product_codes"]]
        self.assertTrue(never_recalled)
        self.assertGreaterEqual(self.channel("X")["false_alarms"], len(never_recalled))

    def test_every_channel_reports_its_alerts_and_a_circular_shift_null(self) -> None:
        weeks = self.signals["weeks"]["evaluated_weeks"]
        for name in ("X", "S", "R_mf"):
            with self.subTest(channel=name):
                summary = self.channel(name)
                alerts = self.signals["channels"][name]["alerts"]
                self.assertEqual((summary["alerts"], summary["alerts_per_week"]), (len(alerts), len(alerts) / weeks))
                chance = summary["chance"]
                self.assertEqual((chance["shifts"], len(chance["per_recall"])), (weeks, summary["in_scope"]))
                self.assertEqual(chance["method"], R.CHANCE_METHOD)
                self.assertAlmostEqual(chance["expected_found"], math.fsum(chance["per_recall"]), delta=1e-12)
                self.assertEqual(summary["found_minus_expected"], summary["found"] - chance["expected_found"])
                self.assertGreaterEqual(chance["p_value"], 1 / weeks)    # shift 0 is the observed alignment
        self.assertIn("(chance ", self.score_out)

    def test_the_circular_shift_null_by_hand(self) -> None:
        first = date(2024, 6, 2)
        alerts = [{"week": "2024-W23", "rank": 1, "key": "k", "score": 0.5, "available_date": "2024-06-09",
                   "product_codes": ["A"]}]
        recall = {"evaluable": True, "product_code": "A", "event_date_initiated": "2024-06-12"}
        # 4 evaluated weeks, look-back 1 week ([06-05, 06-12)): the alert at +7 days is in it at shift 0 only; at
        # shifts 1, 2 and 3 it moves to +14, +21 and +0 (wrapped)
        chance = R.chance_null([recall], alerts, 1, first, 4, 1)
        self.assertEqual((chance["expected_found"], chance["per_recall"], chance["p_value"]), (0.25, [0.25], 0.25))
        self.assertEqual((chance["recall_rate"], chance["median_lead_days"], chance["shifts"]), (0.25, 3, 4))
        other = {**recall, "product_code": "B"}
        self.assertEqual(R.chance_null([other], alerts, 1, first, 4, 0)["p_value"], 1.0)
        none = R.chance_null([], alerts, 1, first, 4, 0)
        self.assertEqual((none["expected_found"], none["recall_rate"], none["p_value"]), (0.0, None, None))

    def test_alert_volume_alone_is_not_credited_above_chance(self) -> None:
        # regression: recalls at arbitrary dates every 14 days on the two background codes, which no plant touches.
        # X raises many alerts on those codes and "finds" every recall; its own null expects nearly as many
        first = date.fromisoformat(closing_date(DQ, self.signals["weeks"]["evaluated_from"]))
        last = date.fromisoformat(closing_date(DQ, self.signals["weeks"]["last"]))
        items, d = [], first + timedelta(days=30)
        while d < last:
            items += [{"evaluable": True, "product_code": code, "event_date_initiated": d.isoformat()}
                      for code in (IP_CODE, OTHER_CODE)]
            d += timedelta(days=14)
        weeks = self.signals["weeks"]["evaluated_weeks"]
        x = R._channel_score(items, self.signals["channels"]["X"]["alerts"], 26, 26, weeks, first)["summary"]
        s = R._channel_score(items, self.signals["channels"]["S"]["alerts"], 26, 26, weeks, first)["summary"]
        self.assertEqual(x["found"], len(items))
        self.assertGreater(x["alerts"], s["alerts"])
        self.assertGreater(x["chance"]["expected_found"], 0.9 * len(items))
        self.assertLess(x["found_minus_expected"], 0.1 * len(items))
        self.assertGreater(x["chance"]["p_value"], 0.05)

    def test_alert_product_codes_come_from_the_alerts_own_window(self) -> None:
        weeks = self.signals["weeks"]
        for a in self.signals["channels"]["X"]["alerts"]:
            self.assertTrue(weeks["evaluated_from"] <= a["week"] <= weeks["last"])
            self.assertEqual(a["product_codes"], sorted(set(a["product_codes"])))
        planted_key = "product:SD-40:overheat"
        keys = {a["key"] for a in self.signals["channels"]["X"]["alerts"] if PLANTED_CODE in a["product_codes"]}
        self.assertIn(planted_key, keys)
        self.assertEqual(self.signals["weeks"]["evaluated_from"], "2024-W20")
        self.assertEqual(self.signals["weeks"]["evaluated_weeks"], 33)

    def test_data_rules(self) -> None:
        maker = self.signals["manufacturer"]
        self.assertEqual((maker["other_manufacturer"], maker["manufacturer_missing"],
                          maker["manufacturer_bad_type"]), (2, 1, 1))
        events = self.signals["events"]
        self.assertEqual((events["duplicates"], events["out_of_range"]), (1, 1))
        self.assertEqual(events["rejected"], {"bad_date": 1, "missing_received_date": 1})
        self.assertEqual(events["mapped"], maker["events_matched"] - 3)
        recalls = self.replay["recalls"]
        self.assertEqual({k: recalls[k] for k in ("cache_records", "duplicates", "other_firm", "bad_date",
                                                  "out_of_range", "not_evaluable")},
                         {"cache_records": 8, "duplicates": 1, "other_firm": 1, "bad_date": 1, "out_of_range": 1,
                          "not_evaluable": 1})
        unmatched = recalls["unmatched_product_code"]
        self.assertEqual([(u["product_code"], u["event_date_initiated"]) for u in unmatched],
                         [("ZZZ", self.init_after)])
        self.assertRegex(unmatched[0]["recall_id"], "^[0-9a-f]{16}$")
        early = self.item("2024-03-01")
        self.assertEqual((early["evaluable"], early["by_channel"]), (False, None))
        self.assertEqual(len([i for i in recalls["items"] if i["product_code"] == PLANTED_CODE]), 3)
        self.assertEqual(self.item(self.init_after)["root_cause_description"], "Device Design")

    def test_coverage_partition_and_labels(self) -> None:
        coverage = self.signals["coverage"]
        self.assertEqual(sorted(coverage), sorted(R.COVERAGE_FIELDS))
        matched = self.signals["manufacturer"]["events_matched"]
        for name, entry in coverage.items():
            self.assertEqual(entry["share"], entry["count"] / matched, name)
        self.assertEqual(coverage["manufacturer_field"]["count"], matched)
        self.assertLess(coverage["date_received"]["count"], matched)
        partition = self.signals["partition"]
        self.assertEqual([s["site"] for s in partition["sites"]], sorted(f"p-{s}" for s in SITES))
        self.assertEqual((partition["unpartitioned_events"], partition["low_coverage"]), (0, False))
        self.assertEqual(self.signals["master_data_note"],
                         "public data has no ERP; structured ids seen at the site stand in for it")
        for doc in (self.signals, self.replay):
            self.assertEqual((doc["label"], doc["data_label"]), (LABEL, "synthetic"))
        self.assertFalse(self.signals["measurement"])
        self.assertFalse(self.replay["measurement"])
        self.assertIn(LABEL, self.score_out)
        self.assertIn("data_label=synthetic", self.score_out)
        self.assertEqual(self.replay["coverage"], coverage)

    def test_a_low_coverage_partition_runs_with_the_unpartitioned_bucket(self) -> None:
        code, _, err = cli(prereg_argv(self.events, self.runs, "pre-region", partition="region"))
        self.assertEqual(code, 0, err)
        code, _, err = cli(["signals", "--prereg", self.runs / "replay" / "pre-region" / "prereg.json", "--run-id",
                            "sig-region", "--runs-dir", self.runs, "--allow-dirty"])
        self.assertEqual(code, 0, err)
        doc = json.loads((self.runs / "replay" / "sig-region" / "signals.json").read_text(encoding="utf-8"))
        partition = doc["partition"]
        self.assertTrue(partition["low_coverage"])
        self.assertLess(partition["coverage"]["share"], 0.5)
        self.assertEqual(partition["unpartitioned_label"],
                         "unpartitioned: events without a partition value, labelled as such")
        sites = {s["site"]: s for s in partition["sites"]}
        self.assertEqual(sorted(sites), ["p-north", "unpartitioned"])
        self.assertEqual(sites["unpartitioned"]["value"], None)
        self.assertEqual(sites["unpartitioned"]["events"], partition["unpartitioned_events"])
        self.assertTrue(any(w.startswith("low_partition_coverage:") for w in doc["warnings"]))

    def test_slugs_collisions_and_too_many_partitions(self) -> None:
        sites = R.slug_sites(["Plant A", "plant-a", "!!!", "PLANT A", "Werk Ä"])
        self.assertEqual(sites["PLANT A"], "p-plant-a")
        for value in ("Plant A", "plant-a", "!!!"):
            self.assertEqual(sites[value], "p-" + sha256_hex(value)[:12])
        self.assertEqual(sites["Werk Ä"], "p-werk")
        self.assertEqual(len(set(sites.values())), len(sites))
        self.assertEqual(R.slug_sites(["x" * 80])["x" * 80], "p-" + "x" * 48)
        many = self.tmp / "many-events"
        record = {"mdr_report_key": "m", "date_received": "20240610",
                  "device": [{"manufacturer_d_name": MAKER, "model_number": "SD-9"}],
                  "mdr_text": [{"text_type_code": DESCRIPTION, "text": "The SD-9 overheated."}]}
        fetch("event", {"DDD": [{**record, "mdr_report_key": f"m{i}", "region": f"r{i}"} for i in range(1000)]},
              many, date_to="20241231")
        code, _, err = cli(prereg_argv(many, self.runs, "pre-many", partition="region"))
        self.assertEqual(code, 0, err)
        code, _, err = cli(["signals", "--prereg", self.runs / "replay" / "pre-many" / "prereg.json", "--run-id",
                            "sig-many", "--runs-dir", self.runs, "--allow-dirty"])
        self.assertEqual(code, 2)
        self.assertIn("more than 999 partitions", err)

    def test_few_sites_and_low_resolution_are_warned(self) -> None:
        tiny = self.tmp / "tiny-events"
        rows = [{"mdr_report_key": f"t{i}", "date_received": (date(2024, 1, 2) + timedelta(days=5 * i)).strftime(
                     "%Y%m%d"),
                 "device": [{"manufacturer_d_name": MAKER, "model_number": f"MODEL {9000 + i} X"}],
                 "mdr_text": [{"text_type_code": DESCRIPTION, "text": "The device overheated."}]} for i in range(60)]
        fetch("event", {"EEE": rows}, tiny, date_to="20241231")
        code, _, err = cli(prereg_argv(tiny, self.runs, "pre-tiny", partition="nowhere"))
        self.assertEqual(code, 0, err)
        code, _, err = cli(["signals", "--prereg", self.runs / "replay" / "pre-tiny" / "prereg.json", "--run-id",
                            "sig-tiny", "--runs-dir", self.runs, "--allow-dirty"])
        self.assertEqual(code, 0, err)
        doc = json.loads((self.runs / "replay" / "sig-tiny" / "signals.json").read_text(encoding="utf-8"))
        warnings = {w.split(":")[0]: w for w in doc["warnings"]}
        self.assertEqual(sorted(warnings), ["fewer_sites_than_min_sites", "low_partition_coverage", "low_resolution"])
        self.assertIn("freeze a vocabulary for them before the run", warnings["low_resolution"])
        self.assertEqual(doc["coverage"]["resolved_entity"]["count"], 0)
        self.assertEqual([s["site"] for s in doc["partition"]["sites"]], ["unpartitioned"])

    def test_field_values(self) -> None:
        row = {"device": [{"name": " ACME "}, {"name": 7}, {"name": ""}, {"other": "x"}], "flat": "v", "num": 3}
        self.assertEqual(field_values(row, "device[].name"), ["ACME", "7"])
        self.assertEqual(field_values(row, "flat"), ["v"])
        self.assertEqual(field_values(row, "absent.path"), [])
        self.assertIsNone(field_values({"device": {"name": "x"}}, "device[].name"))     # not a list where [] fans out
        self.assertIsNone(field_values({"flat": {"a": 1}}, "flat"))                     # not a scalar
        self.assertIsNone(field_values({"flat": True}, "flat"))
        self.assertIsNone(field_values("not a row", "flat"))
        texts = {"t": [{"kind": "a", "text": "one"}, {"kind": "b", "text": "two"}]}
        self.assertEqual(field_values(texts, "t[].text", {"field": "kind", "in": ["b"]}), ["two"])
        for path in ("", "a..b", "a b", "a[]]", "-a", "a.[]"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                field_values(row, path)

    # ------------------------------------------------------------------ refusals
    def test_every_subcommand_exits_2_on_a_missing_or_changed_input(self) -> None:
        for sub in ("signals", "score"):
            with self.subTest(sub=sub, case="no --prereg"), self.assertRaises(SystemExit) as cm:
                with contextlib.redirect_stderr(io.StringIO()):
                    R.main([sub, "--run-id", "x"])
            self.assertEqual(cm.exception.code, 2)
        absent = self.tmp / "absent.json"
        common = ["--runs-dir", self.runs, "--allow-dirty"]
        score_args = ["--signals", self.signals_dir, "--recalls-cache", self.recalls]
        for sub, extra in (("signals", []), ("score", score_args)):
            with self.subTest(sub=sub, case="missing prereg"):
                code, _, err = cli([sub, "--prereg", absent, "--run-id", f"{sub}-absent", *extra, *common])
                self.assertEqual(code, 2)
                self.assertIn("cannot read prereg", err)
            with self.subTest(sub=sub, case="code hash"), mock.patch.object(R, "replay_code_hash",
                                                                            return_value="0" * 64):
                code, _, err = cli([sub, "--prereg", self.prereg, "--run-id", f"{sub}-code", *extra, *common])
                self.assertEqual((code, err.strip()), (2, "error: pinned values differ from the prereg: code_hash"))
        pack = pack_copy(self.tmp, "device_quality")
        code, _, err = cli(prereg_argv(self.events, self.runs, "pre-copy", pack=pack.directory))
        self.assertEqual(code, 0, err)
        detectors = Path(pack.directory) / "detectors.json"
        doc = json.loads(detectors.read_text(encoding="utf-8"))
        doc["alert_budget_per_week"] = 7
        detectors.write_text(json.dumps(doc), encoding="utf-8")
        copy_prereg = self.runs / "replay" / "pre-copy" / "prereg.json"
        for sub, extra in (("signals", []), ("score", score_args)):
            with self.subTest(sub=sub, case="pack"):
                code, _, err = cli([sub, "--prereg", copy_prereg, "--run-id", f"{sub}-pack", *extra, *common])
                self.assertEqual((code, err.strip()),
                                 (2, "error: pinned values differ from the prereg: config_hash, detector_hash"))
        events_copy = self.tmp / "events-copy"
        shutil.copytree(self.events, events_copy)
        code, _, err = cli(prereg_argv(events_copy, self.runs, "pre-manifest"))
        self.assertEqual(code, 0, err)
        manifest = json.loads((events_copy / "manifest.json").read_text(encoding="utf-8"))
        manifest["fetched_at"] = "2030-01-01T00:00:00.000Z"
        (events_copy / "manifest.json").write_text(canonical_dumps(manifest) + "\n", encoding="utf-8")
        code, _, err = cli(["signals", "--prereg", self.runs / "replay" / "pre-manifest" / "prereg.json",
                            "--run-id", "signals-manifest", *common])
        self.assertEqual((code, err.strip()),
                         (2, "error: pinned values differ from the prereg: events manifest_sha256"))
        code, _, err = cli(prereg_argv(self.events, self.runs, "pre-other"))
        self.assertEqual(code, 0, err)
        code, _, err = cli(["score", "--prereg", self.runs / "replay" / "pre-other" / "prereg.json", *score_args,
                            "--run-id", "score-other", *common])
        self.assertEqual(code, 2)
        self.assertIn("another prereg", err)
        for run_id in ("signals-absent", "score-absent", "signals-code", "score-code", "signals-pack", "score-pack",
                       "signals-manifest", "score-other"):
            self.assertFalse((self.runs / "replay" / run_id).exists(), run_id)

    def test_dry_runs_create_nothing(self) -> None:
        before = path_snapshot(self.tmp)
        common = ["--runs-dir", self.runs, "--allow-dirty", "--dry-run"]
        argvs = ([*prereg_argv(self.events, self.runs, "dry"), "--dry-run"],
                 [*prereg_argv(self.tmp / "absent-cache", self.runs, "dry"), "--dry-run"],
                 ["signals", "--prereg", self.prereg, "--run-id", "dry", *common],
                 ["signals", "--prereg", self.tmp / "absent.json", "--run-id", "dry", *common],
                 ["score", "--prereg", self.prereg, "--signals", self.signals_dir, "--recalls-cache", self.recalls,
                  "--run-id", "dry", *common],
                 ["score", "--prereg", self.tmp / "absent.json", "--signals", self.tmp / "absent-signals",
                  "--recalls-cache", self.tmp / "absent-recalls", "--run-id", "dry", *common])
        for argv in argvs:
            with self.subTest(argv=argv[:2]):
                code, out, err = cli(argv)
                self.assertEqual(code, 0, err)
                self.assertTrue(out.startswith(f"dry-run: experiments.openfda_replay {argv[0]}"), out)
        self.assertEqual(path_snapshot(self.tmp), before)
        code, _, _ = cli(["signals", "--prereg", self.prereg, "--run-id", "sig", *common])
        self.assertEqual(code, 2)                      # the run directory exists


if __name__ == "__main__":
    unittest.main()

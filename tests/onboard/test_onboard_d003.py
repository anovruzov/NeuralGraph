"""D003 (``docs/collective/onboard/CHOICE-D003.md``), offline: the fetch, the split, the onboard package's changes P1
to P13, the hand copies, the settings, the workflow, the full arm and the report's guard.

Every record here is invented: ``tests/onboard/openfda_fake.py`` serves a virtual openFDA-shaped archive on
127.0.0.1, whose maker names, report keys, narratives, lots, UDIs, report numbers and patient values are made up.
No public record is read and no request leaves this machine.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ONBOARD_DOCS = ROOT / "docs" / "collective" / "onboard"
SETTINGS_PATH = ONBOARD_DOCS / "D003-settings.json"
SETTINGS = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
CHOICE = ONBOARD_DOCS / "CHOICE-D003.md"
WORKFLOW = ROOT / ".github" / "workflows" / "onboard-d003.yml"
D002_WORKFLOW = ROOT / ".github" / "workflows" / "onboard-run.yml"
HAND = ONBOARD_DOCS / "d003-hand"
DQ = ROOT / "mycelic" / "collective" / "packs" / "data" / "device_quality"

sys.path.insert(0, str(ROOT))
from mycelic.collective import stats  # noqa: E402
from mycelic.collective.onboard import check as C  # noqa: E402
from mycelic.collective.onboard import draft as D  # noqa: E402
from mycelic.collective.onboard import report as REP  # noqa: E402
from mycelic.collective.onboard import score as SC  # noqa: E402
from mycelic.collective.onboard.__main__ import main as onboard_main  # noqa: E402
from mycelic.collective.onboard.exports import parse_export, read_export  # noqa: E402
from mycelic.collective.packs.canonical import folded  # noqa: E402
from mycelic.collective.packs.loader import load_pack_dir  # noqa: E402
from tests.onboard.openfda_fake import MAKERS, Archive, FakeServer  # noqa: E402


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


F = _load("fetch_openfda", ROOT / "tools" / "onboard" / "fetch_openfda.py")
ARM = SETTINGS["arms"]["maude"]
COLUMNS = ARM["columns"]
UA = ARM["fetch"]["user_agent"]
# what the archive invents and nothing may carry past the export: names; and fields the export never holds
NAMES = [*MAKERS, "Otherside Implants Inc"]
NEVER_EXPORTED = ("LOTX", "UDIX", "RPTX-", "PATIENTAGEX", "Inventbrand", "MDL-", "Manufacturer comment never exported",
                  "Malfunction")


def run_fetch(out: Path, settings: dict[str, Any] | None = None, archive: Archive | None = None,
              **mode: Any) -> tuple[int, str, str, FakeServer]:
    """The fetch's CLI against a fresh local server; (exit code, stdout, stderr, server)."""
    settings = settings or SETTINGS
    spath = out.parent / f"{out.name}-settings.json"
    spath.write_text(json.dumps(settings))
    server = FakeServer(archive or Archive(), **mode)
    stdout, stderr = io.StringIO(), io.StringIO()
    with server, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = F.main(["fetch", "--settings", str(spath), "--out", str(out), "--base-url", server.url],
                      sleep=lambda s: None)
    return code, stdout.getvalue(), stderr.getvalue(), server


def chosen_names(archive: Archive | None = None) -> list[str]:
    """The archive's makers in the order the selection takes them (most training reports first)."""
    a = archive or Archive()
    train = {n: a.total(n, date(2021, 1, 1), date(2022, 12, 31)) for n in a.makers}
    return sorted(train, key=lambda n: (-train[n], n.encode("utf-8")))[:5]


def export_keys(directory: Path) -> set[str]:
    return {json.loads(x)["mdr_report_key"] for f in directory.glob("d*.jsonl") for x in f.read_text().splitlines()}


def python_env(tmp: Path, **extra: str) -> dict[str, str]:
    """The environment for a workflow step under bash: ``python`` is this interpreter."""
    import os
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    if not (bin_dir / "python").exists():
        (bin_dir / "python").symlink_to(sys.executable)
    return dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", **extra)


def lines(text: str, key: str) -> list[dict[str, Any]]:
    return [json.loads(x)[key] for x in text.splitlines() if x.startswith("{") and key in json.loads(x)]


class TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# =================================================================================================== the fetch

class FetchRun(unittest.TestCase):
    """One fetch with D003's own settings against the default archive, shared by the tests that read it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "split"
        cls.code, cls.stdout, cls.stderr, cls.server = run_fetch(cls.out)
        cls.companies = json.loads((cls.out / "companies.json").read_text())
        cls.record = json.loads((cls.out / "fetch.json").read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


class FetchSelectionTests(FetchRun):
    def test_the_fetch_finishes(self) -> None:
        self.assertEqual(self.code, 0, self.stderr)

    def test_the_selection_counts(self) -> None:
        sel = lines(self.stdout, "selection")[0]
        self.assertEqual(sel["candidates"], 11)
        self.assertEqual(sel["excluded"], {"placeholder": 2, "not_searchable": 1, "variant": 1, "too_few": 1})
        self.assertEqual((sel["qualifying"], sel["chosen"]), (6, 5))

    def test_the_companies_in_order_by_training_reports(self) -> None:
        a = Archive()
        train = {n: a.total(n, date(2021, 1, 1), date(2022, 12, 31)) for n in MAKERS}
        order = chosen_names(a)
        printed = lines(self.stdout, "company")
        self.assertEqual([c["label"] for c in printed], ["d1", "d2", "d3", "d4", "d5"])
        self.assertEqual([c["counts"]["train"] for c in printed], [train[n] for n in order])
        self.assertTrue(all(c["exact_check"] == "passed" for c in printed))

    def test_the_choice_function_and_its_exclusions(self) -> None:
        rule = ARM["companies"]
        train = {"Zeta Co": 9000, "UNK": 9900, "n/a": 9800, "0000": 9700, 'Bad "Name"': 9600, "Zeta Co.": 8000,
                 "Alpha Inc": 9000, "Tiny Inc": 1999, "Beta GmbH": 5000, "x" * 121: 9500}
        test = {k: 5000 for k in train}
        test["Beta GmbH"] = 999
        chosen, counts = F.choose(train, test, rule)
        # ties by UTF-8 bytes: Alpha before Zeta; Zeta Co. is Zeta Co's variant; Beta has too few test reports
        self.assertEqual(chosen, ["Alpha Inc", "Zeta Co"])
        self.assertEqual(counts["excluded"], {"placeholder": 3, "not_searchable": 2, "variant": 1, "too_few": 2})
        self.assertFalse(F.searchable("a\x07b", 120))
        self.assertTrue(F.searchable("aéb", 120))
        self.assertTrue(F.placeholder("  N/A ", rule["placeholders"]))

    def test_the_companies_file_holds_counts_only(self) -> None:
        text = (self.out / "companies.json").read_text() + (self.out / "source.json").read_text()
        for name in NAMES:
            self.assertNotIn(name, text)
        self.assertNotRegex(text, r"(?<![0-9])20[0-9]{6}(?![0-9])")       # no YYYYMMDD date (E4)
        for label, entry in self.companies.items():
            self.assertEqual(entry["file"], f"{label}.jsonl")
            for w in ("train", "test"):
                self.assertGreaterEqual(entry[f"kept_days_{w}"], ARM["fetch"]["min_days"])
                self.assertGreaterEqual(entry[f"with_narrative_{w}"], ARM["fetch"]["min_reports"][w])

    def test_a_company_under_its_minimum_is_dropped_not_replaced(self) -> None:
        exports = lines(self.stdout, "export")
        dropped = [e for e in exports if not e["used"]]
        self.assertEqual([e["label"] for e in dropped], ["d5"])
        self.assertEqual(dropped[0]["not_used"], "report_minimum")
        self.assertEqual(sorted(self.companies), ["d1", "d2", "d3", "d4"])

    def test_the_request_budget_and_the_counts_printed(self) -> None:
        req = lines(self.stdout, "requests")[-1]
        self.assertLessEqual(req["calls"], 2 + 5 * (2 + 2 + 70 + 50))
        self.assertEqual(req["attempts"], self.server.mode["requests"])
        self.assertLessEqual(max(self.server.mode["skips"]), 1300)        # E11: never past skip 1,300
        for w in lines(self.stdout, "walk"):
            self.assertLessEqual(w["pages"], ARM["fetch"]["budgets"][w["window"]])

    def test_every_request_carries_the_user_agent(self) -> None:
        self.assertEqual(set(self.server.mode["agents"]), {UA})


class FetchExportTests(FetchRun):
    def rows(self, label: str) -> list[dict[str, Any]]:
        return [json.loads(x) for x in (self.out / f"{label}.jsonl").read_text().splitlines()]

    def test_the_seven_columns_in_order_and_nothing_else(self) -> None:
        for label in ("d1", "d2", "d3", "d4", "d5"):
            for row in self.rows(label):
                keys = [k for k in COLUMNS if k in row]
                self.assertEqual(list(row), keys)
                self.assertEqual(set(COLUMNS) - set(row), set() if COLUMNS[3] in row else {COLUMNS[3]})
                self.assertEqual(row["received_day"], row["date_received"])
                self.assertEqual(row["maker_entity"], label.upper())
                self.assertTrue(all(isinstance(p, str) for p in row["product_problems[]"]))

    def test_makers_lists_every_chosen_name_the_dropped_one_included(self) -> None:
        chosen = chosen_names()
        for label in ("d1", "d5"):
            self.assertEqual(self.rows(label)[0]["makers[]"], chosen)

    def test_sorted_by_report_key(self) -> None:
        keys = [r["mdr_report_key"] for r in self.rows("d1")]
        self.assertEqual(keys, sorted(keys, key=F.key_order))
        self.assertEqual(len(keys), len(set(keys)))

    def test_only_the_description_is_the_narrative_and_no_other_field_is_written(self) -> None:
        text = "".join((self.out / f"d{i}.jsonl").read_text() for i in range(1, 6))
        for needle in NEVER_EXPORTED:
            self.assertNotIn(needle, text)
        self.assertNotIn("Otherside Implants Inc", text)          # a report naming another maker is dropped

    def test_the_drafter_reads_the_export(self) -> None:
        export = read_export(self.out / "d1.jsonl")
        self.assertEqual(export.format, "jsonl")
        self.assertEqual(dict(export.rejected), {})
        self.assertEqual(export.columns, tuple(COLUMNS))
        cell = export.value(0, "product_problems[]")
        self.assertIsInstance(cell, tuple)

    def test_kept_days_are_complete_whole_days(self) -> None:
        a = Archive()
        names = chosen_names(a)
        for kd in lines(self.stdout, "kept_days"):
            maker = names[int(kd["label"][1:]) - 1]
            for item in kd["days"]:
                day, total = item.split()
                self.assertEqual(int(total), a.count(maker, date(int(day[:4]), int(day[4:6]), int(day[6:]))))
            self.assertEqual(kd["days"], sorted(kd["days"]))
            digest = hashlib.sha256("\n".join(kd["days"]).encode("utf-8")).hexdigest()
            self.assertEqual(digest, kd["sha256"])
        days = {r["received_day"] for r in self.rows("d2")}
        kept = {x.split()[0] for kd in lines(self.stdout, "kept_days") if kd["label"] == "d2" for x in kd["days"]}
        self.assertEqual(days, kept)

    def test_the_source_record_holds_sizes_and_hashes(self) -> None:
        for label, entry in self.record["companies"].items():
            data = (self.out / entry["file"]).read_bytes()
            self.assertEqual(entry["bytes"], len(data))
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())

    def test_nothing_but_counts_is_printed(self) -> None:
        printed = self.stdout + self.stderr
        for needle in (*NAMES, *NEVER_EXPORTED, "http://", "search=", "date_received:", "ZQXMSG", "Template report"):
            self.assertNotIn(needle, printed)
        self.assertEqual(set(re.findall(r"[0-9A-Za-z]+", printed)) & export_keys(self.out), set())
        rows = self.rows("d1")
        for r in rows[:50]:
            if "mdr_text" in r:
                self.assertNotIn(r["mdr_text"][:30], printed)


class FetchWalkTests(TempDir):
    def test_the_day_order_is_seeded(self) -> None:
        a = F.day_order(date(2021, 1, 1), date(2022, 12, 31), "d003:days:train:d1")
        b = F.day_order(date(2021, 1, 1), date(2022, 12, 31), "d003:days:train:d1")
        c = F.day_order(date(2021, 1, 1), date(2022, 12, 31), "d003:days:train:d2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(sorted(a), F.window_days(date(2021, 1, 1), date(2022, 12, 31)))
        days = F.window_days(date(2021, 1, 1), date(2022, 12, 31))
        random.Random("d003:days:train:d1").shuffle(days)
        self.assertEqual(a, days)

    def test_paging_that_shifts_once_is_refetched_and_kept_and_always_is_left_out(self) -> None:
        maker = list(MAKERS)[0]
        a = Archive()
        order = F.day_order(date(2021, 1, 1), date(2022, 12, 31), "d003:days:train:d1")
        multi = [d for d in order[:60] if 100 < a.count(maker, d) <= 1400]
        if not multi:   # make a two-page day on the walk
            a.makers[maker] = (150, a.makers[maker][1])
            multi = [d for d in order[:60] if 100 < a.count(maker, d) <= 1400]
        once, always = F.ymd(multi[0]), F.ymd(multi[1])
        a.unstable = {(maker, once): "once", (maker, always): "always"}
        code, out, err, _ = run_fetch(self.tmp / "s", archive=a)
        walk = next(w for w in lines(out, "walk") if w["label"] == "d1" and w["window"] == "train")
        self.assertGreaterEqual(walk["refetched"], 2)
        self.assertGreaterEqual(walk["incomplete"]["keys"], 1)
        self.assertGreater(walk["duplicate_keys"], 0)
        kept = next(k for k in lines(out, "kept_days") if k["label"] == "d1" and k["window"] == "train")["days"]
        self.assertIn(once, {x.split()[0] for x in kept})
        self.assertNotIn(always, {x.split()[0] for x in kept})

    def test_an_exact_check_that_differs_twice_drops_the_company(self) -> None:
        a = Archive()
        names = chosen_names(a)
        a.exact_off = {names[1]: 3}
        code, out, err, _ = run_fetch(self.tmp / "s", archive=a)
        printed = {c["label"]: c["exact_check"] for c in lines(out, "company")}
        self.assertEqual(printed["d2"], "failed")
        exported = {e["label"]: e for e in lines(out, "export")}
        self.assertEqual((exported["d2"]["used"], exported["d2"]["not_used"], exported["d2"]["bytes"]),
                         (False, "exact_check", None))
        companies = json.loads((self.tmp / "s" / "companies.json").read_text()) if code == 0 else {}
        self.assertNotIn("d2", companies)
        self.assertFalse((self.tmp / "s" / "d2.jsonl").exists())
        if code == 0:
            row = json.loads((self.tmp / "s" / "d1.jsonl").read_text().splitlines()[0])
            self.assertIn(names[1], row["makers[]"])          # E12: its name stays in makers[]

    def test_fewer_than_three_companies_stop_before_any_day(self) -> None:
        a = Archive()
        for n in list(a.makers)[2:]:
            del a.makers[n]
        code, out, err, server = run_fetch(self.tmp / "s", archive=a)
        self.assertEqual(code, 1)
        self.assertIn("FetchStop", err)
        self.assertEqual(server.mode.get("skips", []), [])     # no page of a day was asked for
        self.assertFalse((self.tmp / "s" / "companies.json").exists())

    def test_the_attempt_cap_stops_the_fetch_by_its_class_name(self) -> None:
        s = copy.deepcopy(SETTINGS)
        s["arms"]["maude"]["fetch"]["attempt_cap"] = 20
        code, out, err, server = run_fetch(self.tmp / "s", settings=s, throttle=10**6)
        self.assertEqual(code, 2)
        self.assertIn("AttemptCap", err)
        self.assertEqual(server.mode["requests"], 20)
        self.assertEqual(lines(out, "requests")[-1], {"attempts": 20, "calls": 11})

    def test_a_server_error_after_the_connectors_retries_stops_with_its_status_and_code(self) -> None:
        code, out, err, server = run_fetch(self.tmp / "s", always_500=True)
        self.assertEqual(code, 2)
        self.assertEqual(server.mode["requests"], 7)              # the connector's 6 retries, then the answer
        self.assertIn("FetchHTTPError (HTTP 500, error code SERVER_ERROR)", err)
        self.assertNotIn("ZQXMSG", out + err)

    def test_a_429_is_retried_by_the_connector_and_every_attempt_counts(self) -> None:
        code, out, err, server = run_fetch(self.tmp / "s", throttle=25)
        self.assertEqual(code, 0, err)
        req = lines(out, "requests")[-1]
        self.assertEqual(server.mode["throttled"], 25)
        self.assertEqual(req["attempts"], req["calls"] + 25)
        self.assertEqual(req["attempts"], server.mode["requests"])

    def test_retries_count_against_the_cap(self) -> None:
        """E9: a 429 on every other attempt doubles the attempts; the walk's 612 calls then pass the cap of 800."""
        code, out, err, server = run_fetch(self.tmp / "s", throttle=10**6)
        self.assertEqual(code, 2)
        self.assertIn("AttemptCap", err)
        self.assertEqual(server.mode["requests"], 800)

    def test_malformed_records_and_answers_print_no_value(self) -> None:
        """E13: a key that is not a number, a date that does not parse, a problem that is not a string, and a total
        that is not a number: none of their strings reaches stdout or stderr."""
        maker = list(MAKERS)[0]
        order = F.day_order(date(2021, 1, 1), date(2022, 12, 31), "d003:days:train:d1")
        a = Archive()
        busy = [F.ymd(d) for d in order[:40] if 0 < a.count(maker, d) <= 100]
        a.malformed = {(maker, busy[0]): "key", (maker, busy[1]): "date", (maker, busy[2]): "problem"}
        code, out, err, _ = run_fetch(self.tmp / "s", archive=a)
        self.assertEqual(code, 0, err)
        for needle in ("KEYNOTANUMBERZQX", "DATENOTPARSEDZQX", "PROBLEMNOTSTRINGZQX"):
            self.assertNotIn(needle, out + err)
        walk = next(w for w in lines(out, "walk") if w["label"] == "d1" and w["window"] == "train")
        self.assertGreaterEqual(walk["incomplete"]["day"], 1)
        export = (self.tmp / "s" / "d1.jsonl").read_text()
        self.assertIn("KEYNOTANUMBERZQX", export)              # kept, written, sorted after the keys of digits
        self.assertNotIn("PROBLEMNOTSTRINGZQX", export)
        code, out, err, _ = run_fetch(self.tmp / "t", bad_total=True)
        self.assertEqual(code, 2)
        self.assertIn("ValueError", err)
        self.assertNotIn("TOTALZQX", out + err)

    def test_a_refused_request_prints_its_status_and_code_only(self) -> None:
        err = F.describe(F.FetchHTTPError(403, "API_KEY_MISSING"))
        self.assertEqual(err, "FetchHTTPError (HTTP 403, error code API_KEY_MISSING)")
        self.assertEqual(F._error_code(b'{"error": {"code": "a name", "message": "x"}}'), None)
        self.assertEqual(F.describe(KeyError("a value")), "KeyError")

    def test_the_dry_run_touches_nothing(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = F.main(["fetch", "--settings", str(SETTINGS_PATH), "--out", str(self.tmp / "o"), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("would need: network api.fda.gov", out.getvalue())
        self.assertFalse((self.tmp / "o").exists())


# =================================================================================================== settings

class SettingsTests(unittest.TestCase):
    """``D003-settings.json`` against CHOICE-D003.md (sections 2, 5, 6, 8, 12 and the amendment)."""

    def test_identity_and_schema(self) -> None:
        s = SETTINGS
        self.assertEqual((s["experiment"], s["kind"], s["choice"], s["language"], s["timeout_minutes"]),
                         ("D003", "onboard_d001_settings", "docs/collective/onboard/CHOICE-D003.md", "en", 180))
        self.assertEqual(s["criteria"], ["C1", "C2", "M1", "M2", "M3"])
        self.assertEqual(s["m1_settings"], ["docs/collective/onboard/D001-settings.json",
                                            "docs/collective/onboard/D002-settings.json"])
        self.assertEqual(list(s["arms"]), ["maude"])

    def test_d002s_parameters_with_the_nine_generic_terms(self) -> None:
        d002 = json.loads((ONBOARD_DOCS / "D002-settings.json").read_text())
        params = dict(SETTINGS["params"])
        nine = params.pop("non_specific_labels")
        self.assertEqual(params, d002["params"])
        self.assertEqual(params["floor_sites"], 3)                 # E4
        coverage = json.loads((ROOT / "docs/strategy/data/field-coverage.json").read_text())["definitions"]
        self.assertEqual(nine, coverage["generic_problems"])
        self.assertEqual(len(nine), 9)
        self.assertEqual(SETTINGS["bootstrap"], {"B": d002["bootstrap"]["B"], "alpha": d002["bootstrap"]["alpha"],
                                                 "seed_prefix": "d003:boot"})
        self.assertEqual((SETTINGS["sample"], SETTINGS["permute"]),
                         ({"seed_prefix": "d003:sample"}, {"seed_prefix": "d003:permute"}))
        self.assertEqual(SETTINGS["report_guard"], d002["report_guard"])

    def test_the_columns_and_roles_of_e5(self) -> None:
        self.assertEqual(COLUMNS, ["mdr_report_key", "date_received", "product_problems[]", "mdr_text",
                                   "received_day", "maker_entity", "makers[]"])
        self.assertEqual(ARM["roles"], {"record_id": "mdr_report_key", "date": "date_received",
                                        "category": "product_problems[]", "narrative": "mdr_text",
                                        "site": "received_day", "entities": [], "reporter": None,
                                        "forbidden": ["makers[]"]})
        self.assertEqual((ARM["train"], ARM["test"], ARM["per_company"]),
                         (["2021-01-01", "2022-12-31"], ["2023-01-01", "2024-12-31"], 200))

    def test_the_company_and_fetch_values(self) -> None:
        c, f = ARM["companies"], ARM["fetch"]
        self.assertEqual((c["field"], c["count_limit"], c["count"], c["labels"], c["min_companies"],
                          c["max_name_chars"], c["min_train_reports"], c["min_test_reports"]),
                         ("device.manufacturer_d_name", 100, 5, ["d1", "d2", "d3", "d4", "d5"], 3, 120, 2000, 1000))
        coverage = json.loads((ROOT / "docs/strategy/data/field-coverage.json").read_text())["definitions"]
        self.assertEqual(c["placeholders"], coverage["placeholder_lots"])
        self.assertEqual((f["base_url"], f["endpoint"], f["page_limit"], f["budgets"], f["day_caps"], f["min_days"],
                          f["min_reports"], f["attempt_cap"], f["exact_check_repeats"], f["day_refetches"],
                          f["days_seed_prefix"], f["text_type"], f["user_agent"]),
                         ("https://api.fda.gov", "device/event", 100, {"train": 70, "test": 50},
                          {"train": 14, "test": 10}, 5, {"train": 1000, "test": 500}, 800, 1, 1, "d003:days",
                          "Description of Event or Problem", "mycelic-onboard (pack-drafting test on public data)"))
        d002 = json.loads((ONBOARD_DOCS / "D002-settings.json").read_text())
        self.assertIn(f["user_agent"], (ROOT / "tools/onboard/fetch_nhtsa.py").read_text())
        self.assertEqual(f["budgets"]["train"] // 5, f["day_caps"]["train"])    # a fifth of the budget (E11)
        self.assertEqual(f["budgets"]["test"] // 5, f["day_caps"]["test"])
        self.assertIsNotNone(d002)

    def test_the_criteria_and_hand_packs(self) -> None:
        c1, c2 = ARM["criterion"]
        self.assertEqual((c1["id"], c1["margin"], c1["interval"], c1["controls"]),
                         ("C1", 0.1, "cluster", ["majority_prior", "set_prior", "permuted_labels", "label_names",
                                                 "label_words"]))
        self.assertEqual((c2["id"], c2["against"], c2["space"], c2["interval"], c2["controls"], c2["min_records"],
                          c2["guard"], c2["reported_margin"]),
                         ("C2", "device_quality", "reach", "cluster", ["all_reached", "most_frequent_reached"], 100,
                          True, 0.05))
        self.assertEqual(ARM["hand_packs"], {"device_quality": "docs/collective/onboard/d003-hand/device_quality",
                                             "device_quality_own":
                                                 "docs/collective/onboard/d003-hand/device_quality_own"})
        self.assertEqual(ARM["reported"], {"echo_free": ["label", "word"], "masked_repeats": True,
                                           "single_narrative_terms": True, "reader_guard": True, "kept_days": True})

    def test_the_download_code_is_the_fetch_and_what_it_imports(self) -> None:
        import ast
        tree = ast.parse((ROOT / ARM["download_script"]).read_text())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules.add(node.module)
        local = sorted(m for m in modules if m.startswith("mycelic.") or (ROOT / "tools/market" / f"{m}.py").exists())
        paths = sorted(f"{m.replace('.', '/')}.py" if m.startswith("mycelic.") else f"tools/market/{m}.py"
                       for m in local)
        self.assertEqual(ARM["download_code"], [ARM["download_script"], *paths])
        self.assertIn("mycelic/collective/connectors/openfda.py", paths)
        self.assertFalse(any(p.startswith("mycelic/collective/onboard/") for p in paths))

    def test_m1_names_and_no_package_hit(self) -> None:
        names = C.column_names(SETTINGS)
        for n in ("mdr_report_key", "product_problems", "makers", "maker_entity", "received_day", "odino",
                  "NARRATIVE", "components"):
            self.assertIn(n, names)
        self.assertEqual(C.package_hits(ROOT / "mycelic/collective/onboard", names), {})
        self.assertEqual(REP.download_code(SETTINGS), sorted(ARM["download_code"]))


# =================================================================================================== hand copies

def expected_copy(name: str) -> dict[str, Any]:
    """CHOICE-D003.md 6.2 (a) to (c) with E1 and E5: device_quality's files with only the recorded changes."""
    maps = {"device_quality": json.loads((ROOT / "lab/packs/device_quality_bd/mapping_openfda.json").read_text()),
            "device_quality_own": json.loads((DQ / "mapping_openfda.json").read_text())}
    out: dict[str, Any] = {}
    out["mapping.json"] = {"record_ref": "mdr_report_key", "site": "received_day",
                           "received_date": {"path": "date_received", "format": "yyyymmdd"}, "language": None,
                           "codes": [{"path": "product_problems[]",
                                      "value_map": maps[name]["codes"][0]["value_map"]}],
                           "entities": {"maker": ["maker_entity"]}, "primary_entity_type": "maker",
                           "narrative": [{"path": "mdr_text", "where": None}], "persons": {}, "reporter": None,
                           "origin_ref": None, "origin_site": None, "required": ["narrative"]}
    vocab = json.loads((DQ / "vocabulary.json").read_text())
    vocab["entity_types"]["maker"] = {"label": "Company label (D003)", "id_format": None, "separator": None,
                                      "case": "upper", "strip_leading_zeros": False, "exact_match_metric": False,
                                      "egress": False, "ids": ["D1", "D2", "D3", "D4", "D5"]}
    out["vocabulary.json"] = vocab
    aliases = json.loads((DQ / "aliases.json").read_text())
    aliases["maker"] = {f"d003 maker d{i}": f"D{i}" for i in range(1, 6)}
    out["aliases.json"] = aliases
    gen = json.loads((DQ / "generator.json").read_text())
    gen["universe"]["maker"] = ["D1", "D2", "D3", "D4", "D5"]
    gen["fill_rates"] = {"maker": 1.0}
    gen["persons"] = {}
    for preds in gen["narratives"].values():
        for tpl in preds.values():
            for key in ("affirmed", "negated"):
                tpl[key] = [re.sub(r"\{person:[a-z_]+\} ", "", t) for t in tpl[key]]
    out["generator.json"] = gen
    egress = json.loads((DQ / "egress.json").read_text())
    egress["never_fields"] = [f for f in egress["never_fields"] if not f.startswith("persons.")]
    out["egress.json"] = egress
    fixtures = []
    for line in (DQ / "fixtures/records.jsonl").read_text().splitlines():
        obj = json.loads(line)
        obj["record"]["entities"] = {"maker": []}
        obj["record"]["persons"] = {}
        fixtures.append(obj)
    out["fixtures/records.jsonl"] = fixtures
    return out


class HandCopyTests(unittest.TestCase):
    def test_each_copy_is_device_quality_with_only_the_recorded_changes(self) -> None:
        for name in ("device_quality", "device_quality_own"):
            want = expected_copy(name)
            base = HAND / name
            files = sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
            self.assertEqual(files, sorted(p.relative_to(DQ).as_posix() for p in DQ.rglob("*") if p.is_file()))
            for rel in files:
                with self.subTest(copy=name, file=rel):
                    got = (base / rel).read_bytes()
                    if rel in want and rel.endswith(".jsonl"):
                        self.assertEqual([json.loads(x) for x in got.decode().splitlines()], want[rel])
                    elif rel in want:
                        self.assertEqual(json.loads(got), want[rel])
                    else:
                        self.assertEqual(got, (DQ / rel).read_bytes())

    def test_the_reading_vocabulary_is_unchanged(self) -> None:
        dq = load_pack_dir(DQ)
        for name in ("device_quality", "device_quality_own"):
            hand = load_pack_dir(HAND / name)
            self.assertEqual(hand.predicates, dq.predicates)
            self.assertEqual(hand.negation, dq.negation)
            self.assertEqual(hand.negation_window, dq.negation_window)
            self.assertEqual(hand.extraction, dq.extraction)
            self.assertEqual(hand.codes, dq.codes)
            self.assertEqual({t: v for t, v in hand.entity_types.items() if t != "maker"}, dict(dq.entity_types))

    def test_the_value_maps(self) -> None:
        big = load_pack_dir(HAND / "device_quality").mapping()["codes"][0]["value_map"]
        own = load_pack_dir(HAND / "device_quality_own").mapping()["codes"][0]["value_map"]
        self.assertEqual((len(big), len(own)), (22, 6))
        self.assertTrue(set(own) <= set(big))
        dq = load_pack_dir(DQ)
        self.assertEqual(sum(1 for c in big.values() if dq.codes[c].specific), 21)
        self.assertEqual(sum(1 for c in own.values() if dq.codes[c].specific), 5)

    def test_e1_the_primary_entity_resolves_and_a_lower_case_label_is_counted(self) -> None:
        for name in ("device_quality", "device_quality_own"):
            hand = load_pack_dir(HAND / name)
            row = {"mdr_report_key": "1", "date_received": "20230105", "received_day": "20230105",
                   "mdr_text": "It was cracked when it arrived.", "maker_entity": "D1"}
            preds, counts = SC.read_records_counted(hand, [row], SC.generic_predicates(hand))
            self.assertEqual(preds, [frozenset({"crack"})])
            self.assertEqual(counts, {"rejected": 0, "no_primary": 0, "structured_unresolved": 0, "no_entity": 0})
            preds, counts = SC.read_records_counted(hand, [dict(row, maker_entity="d1")], SC.generic_predicates(hand))
            self.assertEqual(preds, [frozenset()])
            self.assertEqual(counts, {"rejected": 0, "no_primary": 1, "structured_unresolved": 1, "no_entity": 1})


# =================================================================================================== P1 to P13

def toy(rows: list[tuple[str, str, str, str]]) -> tuple[Any, Any]:
    """An export in D003's layout from (key, day, problems joined by ';', text) and its draft."""
    data = "".join(json.dumps({"mdr_report_key": k, "date_received": d, "product_problems[]": p.split(";"),
                               "mdr_text": t, "received_day": d, "maker_entity": "D1",
                               "makers[]": ["Invented Maker Corp"]}) + "\n" for k, d, p, t in rows)
    export = parse_export(data.encode())
    roles = SC.arm_roles(SETTINGS, "maude")
    return export, roles


def toy_rows(n_days: int = 6, per: int = 20, extra: str = "") -> list[tuple[str, str, str, str]]:
    rng = random.Random(7)
    rows = []
    cues = {"Crack": "cracked split", "Fluid/Blood Leak": "leaking seepage",
            "Adverse Event Without Identified Device or Use Problem": "reviewed concluded"}
    k = 0
    for day in range(n_days):
        for cat, cue in cues.items():
            for _ in range(per):
                k += 1
                filler = " ".join(rng.sample(["nurse", "pump", "ward", "staff", "shift", "unit", "visit"], 4))
                rows.append((str(10000 + k), f"202101{day + 10:02d}", cat, f"{cue} {filler}{extra}."))
    return rows


class NonSpecificLabelTests(unittest.TestCase):
    """P4: the settings' generic terms go to the other bucket; without them they are specific."""

    def test_the_generic_term_is_specific_without_p4_and_other_with_it(self) -> None:
        export, roles = toy(toy_rows())
        window = D.parse_window("2021-01-01", "2022-12-31")
        params = dict(SETTINGS["params"])
        with_p4 = D.draft_export(export, roles, window, "p4_on", params=params)
        params.pop("non_specific_labels")
        without = D.draft_export(export, roles, window, "p4_off", params=params)
        labels_on = set(with_p4.plan.labels.values())
        labels_off = set(without.plan.labels.values())
        self.assertNotIn("Adverse Event Without Identified Device or Use Problem", labels_on)
        self.assertIn("Adverse Event Without Identified Device or Use Problem", labels_off)
        lang = D.load_language("en")
        self.assertIs(D.with_non_specific(lang, {}), lang)
        self.assertIn(folded("Insufficient Information"), D.with_non_specific(lang, SETTINGS["params"]).non_specific)


class ControlTests(unittest.TestCase):
    """P9 (E6): label words and the set prior."""

    def plan(self) -> Any:
        r = []
        for label, n in (("Fluid/Blood Leak", 80), ("Blood Contamination of Device", 60), ("Crack", 55),
                         ("Device Leak Unspecified", 52)):
            r += [(label, n)]
        return D.Plan(ids=("fluid_blood_leak", "blood_contamination_of_device", "crack", "device_leak_unspecified"),
                      of_key={}, labels={"fluid_blood_leak": "Fluid/Blood Leak",
                                         "blood_contamination_of_device": "Blood Contamination of Device",
                                         "crack": "Crack", "device_leak_unspecified": "Device Leak Unspecified"},
                      codes={}, counts={"fluid_blood_leak": 80, "blood_contamination_of_device": 60, "crack": 55,
                                        "device_leak_unspecified": 52},
                      value_map={}, other_id="other_category", other_code="D-999", split=D.Split((), (), ()))

    def test_label_words(self) -> None:
        lang = D.load_language("en")
        self.assertEqual(D.label_words("Fluid/Blood Leak", lang), ["fluid", "blood", "leak"])
        self.assertEqual(D.label_words("No Known Impact Or Consequence To Patient", lang),
                         ["known", "impact", "consequence", "patient"])
        self.assertEqual(D.label_words("Crack", lang), ["crack"])
        self.assertEqual(D.label_words("Pin 3 X-ray", lang), [])
        lex = D.label_word_lexicon(self.plan(), lang)
        self.assertEqual(lex["fluid_blood_leak"], ["fluid", "blood", "leak"])
        self.assertEqual(lex["blood_contamination_of_device"], ["contamination", "device"])
        self.assertEqual(lex["device_leak_unspecified"], ["unspecified"])
        refusal = D.Refusal([], ["Invented Fluid Holdings"], SETTINGS["params"])
        lex = D.label_word_lexicon(self.plan(), lang, refusal)
        self.assertEqual(lex["fluid_blood_leak"], ["blood", "leak"])          # a refused word is no term
        self.assertEqual(D.refused_label_words(self.plan(), lang, refusal), 1)

    def test_the_set_prior(self) -> None:
        self.assertEqual(D.set_prior([("a", "b"), ("a", "b"), ("a",), ("a",), (), ()]), ("a",))
        self.assertEqual(D.set_prior([("b",), ("a", "c"), ("b",), ("a", "c")]), ("b",))
        self.assertEqual(D.set_prior([("b",), ("a",)]), ("a",))
        self.assertEqual(D.set_prior([("a", "b"), ("a", "b"), ("c",)]), ("a", "b"))
        self.assertEqual(D.set_prior([(), ()]), ())


class ClusterTests(unittest.TestCase):
    """P8 (E3): the deciding intervals resample (company, day) clusters through stats, unchanged."""

    def data(self) -> tuple[list[frozenset[str]], list[frozenset[str]], list[frozenset[str]], list[str], list[str]]:
        rng = random.Random(11)
        f = frozenset
        golds = [f({rng.choice("ab")}) for _ in range(60)]
        a = [f({rng.choice("ab")}) if rng.random() < 0.8 else g for g in golds]
        b = [f({"a"}) for _ in golds]
        comps = [rng.choice(["d2", "d1"]) for _ in golds]
        days = [rng.choice(["20230102", "20230101", "20230309"]) for _ in golds]
        return a, b, golds, comps, days

    def test_the_cluster_difference_is_stats_on_cluster_sums(self) -> None:
        a, b, golds, comps, days = self.data()
        got = SC.cluster_difference(a, b, golds, comps, days, B=300, seed="d003:boot:maude")
        keys = sorted(set(zip(comps, days)))
        sums = {k: [[0, 0, 0], [0, 0, 0]] for k in keys}
        for pa, pb, g, k in zip(a, b, golds, zip(comps, days)):
            for side, p in ((0, pa), (1, pb)):
                tp, fp, fn = SC.record_counts(p, g)
                sums[k][side][0] += tp
                sums[k][side][1] += fp
                sums[k][side][2] += fn
        want = stats.paired_bootstrap_f1([tuple(sums[k][0]) for k in keys], [tuple(sums[k][1]) for k in keys], B=300,
                                         seed="d003:boot:maude")
        self.assertEqual((got["ci_low"], got["ci_high"], got["diff"]), (want["ci_low"], want["ci_high"], want["diff"]))
        self.assertEqual(got["clusters"], len(keys))
        rec = SC.difference(a, b, golds, B=300, seed="d003:boot:maude")
        self.assertAlmostEqual(got["diff"], rec["diff"])
        self.assertEqual(got["exact_diff"], rec["exact_diff"])

    def test_the_reader_interval_and_the_order_of_clusters(self) -> None:
        a, b, golds, comps, days = self.data()
        members, k = SC.cluster_members(comps, days)
        self.assertEqual(k, len(set(zip(comps, days))))
        first = min(range(len(comps)), key=lambda i: (comps[i], days[i]))
        self.assertEqual(members[first], 0)
        got = SC.cluster_f1(a, golds, comps, days, B=200, seed="s")
        counts = SC.cluster_counts([SC.record_counts(p, g) for p, g in zip(a, golds)], members, k)
        self.assertEqual(got["ci_low"], stats.bootstrap_f1(counts, B=200, seed="s")["ci_low"])


class CriterionTests(unittest.TestCase):
    """P1, P2, P7, E8: D003's C1 and C2 decisions on crafted pooled values."""

    c1 = ARM["criterion"][0]
    c2 = ARM["criterion"][1]

    def pooled(self, diff: Fraction, ci_low: float) -> dict[str, Any]:
        readers = {r: {"micro": {"f1": 0.5}} for r in ("drafted", *self.c1["controls"])}
        readers["drafted"]["micro"]["f1"] = 0.7
        readers["label_words"]["micro"]["f1"] = 0.6
        days = {"diff": float(diff), "exact_diff": diff, "ci_low": ci_low, "ci_high": 0.3, "clusters": 12}
        records = {"diff": float(diff), "ci_low": 0.05, "ci_high": 0.2}
        return {"readers": readers, "compared": {r: {"days": days, "records": records} for r in self.c1["controls"]}}

    def test_c1_is_decided_by_the_days_interval_and_the_exact_margin(self) -> None:
        ok = SC.criterion_days(self.c1, self.pooled(Fraction(1, 10), 0.001), [], 5)
        self.assertTrue(ok["passed"])
        self.assertEqual(ok["best_control"], "label_words")
        self.assertFalse(SC.criterion_days(self.c1, self.pooled(Fraction(99, 1000), 0.01), [], 5)["passed"])
        self.assertFalse(SC.criterion_days(self.c1, self.pooled(Fraction(2, 10), 0.0), [], 5)["passed"])
        self.assertFalse(SC.criterion_days(self.c1, self.pooled(Fraction(2, 10), 0.1), ["d2"], 5)["passed"])

    def space(self, with_gold: int, lows: dict[str, float]) -> dict[str, Any]:
        return {"scored": 400, "with_gold": with_gold, "matched_names": {"d1": 3},
                "compared": {r: {"days": {"diff": 0.1, "ci_low": v, "ci_high": 0.2, "clusters": 30}}
                             for r, v in lows.items()}}

    def test_c2_is_superiority_over_all_three(self) -> None:
        lows = {"device_quality": 0.01, "all_reached": 0.02, "most_frequent_reached": 0.03}
        guard = {"rejected": 0, "no_primary": 0}
        self.assertTrue(SC.criterion_reach(self.c2, self.space(100, lows), guard, [], 5)["passed"])
        for r in lows:
            with self.subTest(reader=r):
                low = dict(lows, **{r: 0.0})
                c = SC.criterion_reach(self.c2, self.space(100, low), guard, [], 5)
                self.assertFalse(c["passed"])
                self.assertTrue(c["decided"])
        c = SC.criterion_reach(self.c2, self.space(100, dict(lows, device_quality=-0.04)), guard, [], 5)
        reported = [v for k, v in c["exact"]["comparisons"].items() if "reported only" in k]
        self.assertEqual(reported, [True])
        self.assertFalse(c["passed"])

    def test_c2_not_decided_below_its_minimum_or_when_the_guard_fails(self) -> None:
        lows = {"device_quality": 0.1, "all_reached": 0.1, "most_frequent_reached": 0.1}
        c = SC.criterion_reach(self.c2, self.space(99, lows), {"rejected": 0, "no_primary": 0}, [], 5)
        self.assertEqual((c["passed"], c["decided"]), (False, False))
        self.assertIn("fewer than 100", c["reason"])
        c = SC.criterion_reach(self.c2, self.space(500, lows), {"rejected": 0, "no_primary": 2}, [], 5)
        self.assertEqual((c["passed"], c["decided"]), (False, False))
        self.assertIn("without a primary entity", c["reason"])
        detail = REP._criterion_detail(c, REP.report_text(SETTINGS))
        self.assertTrue(detail.startswith("not decided (the deciding hand copy"))


class EchoTests(unittest.TestCase):
    """P6 and E7: the whole-label and word echoes, and digit-masked repeats."""

    def test_echoes_and_masked_repeats(self) -> None:
        rows = toy_rows(n_days=6, per=20)
        rows += [("90001", "20230105", "Crack", "The housing showed a crack near the seam."),
                 ("90002", "20230105", "Fluid/Blood Leak", "Blood was seen at the port, ref 551."),
                 ("90003", "20230106", "Fluid/Blood Leak", "Blood was seen at the port, ref 978."),
                 ("90004", "20230107", "Crack", "Nothing in the words says so.")]
        export, roles = toy(rows)
        d = D.draft_export(export, roles, D.parse_window("2021-01-01", "2022-12-31"), "echo_pack",
                           params=SETTINGS["params"])
        sample, _ = SC.draw_sample(export, d, D.parse_window("2023-01-01", "2024-12-31"), "s", 200, "d1")
        by = {s.ref: s for s in sample}
        self.assertEqual(sorted(by), ["90001", "90002", "90003", "90004"])
        order = [by[k] for k in sorted(by)]
        self.assertEqual(SC.label_echo(order, d.plan), [True, False, False, False])
        self.assertEqual(SC.word_echo(order, d), [True, True, True, False])
        test = D.parse_window("2023-01-01", "2024-12-31")
        self.assertEqual(SC.masked_repeats(order, export, d, test), [False, True, True, False])
        self.assertEqual(SC.masked("Ref 12 and 3.5"), "ref 0 and 0.0")

    def test_terms_resting_on_one_narrative(self) -> None:
        rows = toy_rows(n_days=6, per=20)
        rows += [(str(80000 + i), f"2021013{i % 2}", "Crack", "Zygomorphic gizmo cracked split.") for i in range(12)]
        export, roles = toy(rows)
        d = D.draft_export(export, roles, D.parse_window("2021-01-01", "2022-12-31"), "single_pack",
                           params=dict(SETTINGS["params"], floor_sites=2))
        got = SC.single_narrative_terms(d, "unlearned-")
        self.assertGreaterEqual(got["lexicon_single"], 1)
        self.assertLessEqual(got["printed_single"], got["printed_terms"])
        self.assertIn("zygomorphic", {t for p in d.plan.ids for t in d.learned[p]})


class ExemptionTests(TempDir):
    """P13 (E15): FDA's generic terms leave the n-gram scans only; every other check still reads them."""

    def test_a_narrative_quoting_a_generic_term(self) -> None:
        rows = toy_rows(extra=". Coded as adverse event without identified device or use problem")
        export, roles = toy(rows)
        window = D.parse_window("2021-01-01", "2022-12-31")
        for exempt in (True, False):
            with self.subTest(exempt=exempt):
                params = dict(SETTINGS["params"])
                if not exempt:
                    params.pop("non_specific_labels")
                    params["non_specific_labels"] = []
                d = D.draft_export(export, roles, window, f"p13_{int(exempt)}", params=SETTINGS["params"])
                D.write_pack(d.files, self.tmp / f"pack{int(exempt)}")
                res = C.check_pack(self.tmp / f"pack{int(exempt)}", export, roles, window, params=params)
                self.assertEqual(res["checks"]["ngram"]["passed"], exempt)
                if exempt:
                    self.assertEqual(res["checks"]["ngram"]["exempt_strings"], 1)
                    self.assertEqual(res["checks"]["ngram"]["within_value_map"], 0)
                else:
                    self.assertNotIn("exempt_strings", res["checks"]["ngram"])
        self.assertEqual(C.exempt_labels({}), frozenset())

    def test_the_guard_exempts_and_counts(self) -> None:
        rows = toy_rows(extra=". Coded as adverse event without identified device or use problem")
        export, roles = toy(rows)
        sent = REP.company_sentinels(export, roles, SETTINGS["params"])
        c = {"draft": {"categories": {"passing_floor": [
                {"label": "Adverse Event Without Identified Device or Use Problem"}, {"label": "Crack"}]},
             "predicates": []}, "controls": {}}
        exempt = C.exempt_labels(SETTINGS["params"])
        hits = REP.company_hits(c, sent, 8, "unlearned-", exempt)
        self.assertEqual((hits["narrative_ngrams"], hits["exempt_strings"], hits["within_labels"]), (0, 1, 0))
        hits = REP.company_hits(c, sent, 8, "unlearned-")
        self.assertEqual(hits["narrative_ngrams"], 1)
        self.assertNotIn("exempt_strings", hits)


class EarlierSettingsTests(unittest.TestCase):
    """P12: the column names of D001 and D002 join D003's in M1 and in rule 1.7's label check."""

    def test_column_names(self) -> None:
        names = C.column_names(SETTINGS)
        alone = C.column_names({k: v for k, v in SETTINGS.items() if k != "m1_settings"})
        self.assertIn("NARRATIVE", names)
        self.assertNotIn("NARRATIVE", alone)
        self.assertTrue(alone < names)
        d002 = json.loads((ONBOARD_DOCS / "D002-settings.json").read_text())
        self.assertEqual(C.earlier_settings(d002), [])
        self.assertEqual(C.label_hits(["components", "Crack", "makers"], names), 2)


class ReportTextTests(unittest.TestCase):
    """P3 and P5: the listed criteria and the settings' sentences; D002's text when the settings give none."""

    def test_d002_text_by_default(self) -> None:
        text = REP.report_text(None)
        self.assertEqual(text, REP.D002_TEXT)
        self.assertEqual(REP.listed_criteria({}), ("C1", "C2", "M1", "M2", "M3", "M4"))
        self.assertTrue(REP.needs_audit({}))

    def test_d003_lists_five_and_reads_no_audit(self) -> None:
        self.assertEqual(REP.listed_criteria(SETTINGS), ("C1", "C2", "M1", "M2", "M3"))
        self.assertFalse(REP.needs_audit(SETTINGS))
        text = REP.report_text(SETTINGS)
        for key in ("verdict", "below_criteria", "intervals", "roles_note", "matched_names"):
            self.assertNotEqual(text[key], REP.D002_TEXT[key])
        self.assertNotIn("MSHA", " ".join(SETTINGS["report_text"].values()))
        self.assertNotIn("pack/", text["below_criteria"])


# =================================================================================================== the full arm

class FullArmRun(unittest.TestCase):
    """The workflow's steps after the settings check, on the local archive: fetch, the marker, score, report (no
    audit), with D003's settings but a smaller bootstrap and sample so that the test is quick."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        s = copy.deepcopy(SETTINGS)
        s["bootstrap"]["B"] = 200
        s["arms"]["maude"]["per_company"] = 60
        cls.settings_path = tmp / "settings.json"
        cls.settings_path.write_text(json.dumps(s))
        cls.code, cls.fetch_out, cls.fetch_err, _ = run_fetch(tmp / "split", settings=s)
        cls.steps = {}
        for name, argv in (("score", ["score", "--settings", str(cls.settings_path), "--arm", "maude", "--exports",
                                      str(tmp / "split"), "--out", str(tmp / "arms" / "maude")]),
                           ("report", ["report", "--settings", str(cls.settings_path), "--arms",
                                       str(tmp / "arms" / "maude"), "--out", str(tmp / "report")])):
            r = subprocess.run([sys.executable, "-B", "-m", "mycelic.collective.onboard", *argv], cwd=ROOT,
                               capture_output=True, text=True)
            cls.steps[name] = r
        cls.arm = json.loads((tmp / "arms" / "maude" / "arm.json").read_text())
        cls.report = json.loads((tmp / "report" / "report.json").read_text())
        cls.md = (tmp / "report" / "report.md").read_text()
        cls.split = tmp / "split"
        cls.tmp = tmp

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


class FullArmTests(FullArmRun):
    def test_every_step_runs(self) -> None:
        self.assertEqual(self.code, 0, self.fetch_err)
        self.assertEqual(self.steps["score"].returncode, 0, self.steps["score"].stderr)
        self.assertIn(self.steps["report"].returncode, (0, 1), self.steps["report"].stderr)
        self.assertIn("=== D003 report.json BEGIN", self.steps["report"].stdout)
        self.assertEqual(self.steps["report"].stderr, "")

    def test_the_report_takes_five_criteria_and_no_audit(self) -> None:
        self.assertEqual(list(self.report["criteria"]), ["C1", "C2", "M1", "M2", "M3"])
        self.assertIsNone(self.report["audit"])
        self.assertNotIn("Pilot audit", self.md)
        self.assertIn(self.report["verdict"], ("pass", "fail"))
        self.assertTrue(self.md.splitlines()[2].endswith(SETTINGS["report_text"]["verdict"].format(exp="D003")))
        for key in ("below_criteria", "intervals", "roles_note", "label_words"):
            self.assertIn(SETTINGS["report_text"][key], self.md)

    def test_the_arm_holds_both_criteria_from_one_sample(self) -> None:
        crit = self.arm["criterion"]
        self.assertEqual([c["id"] for c in crit], ["C1", "C2"])
        self.assertIn(crit[0]["best_control"], ARM["criterion"][0]["controls"])
        self.assertEqual(crit[1]["against"], "device_quality")
        self.assertIn("kept_days", crit[0])
        self.assertEqual(set(self.arm["pooled"]["readers"]), {"drafted", *ARM["criterion"][0]["controls"]})

    def test_the_cluster_space_subset_and_guard_blocks(self) -> None:
        pooled = self.arm["pooled"]
        self.assertEqual(sum(pooled["cluster"]["per_company"].values()), pooled["cluster"]["clusters"])
        self.assertEqual(set(self.arm["space"]), {"device_quality", "device_quality_own"})
        self.assertEqual(set(self.arm["subsets"]), set(SC.SUBSETS))
        self.assertEqual(set(self.arm["reader_guard"]), {"drafted", "device_quality", "device_quality_own"})
        self.assertEqual(self.arm["reader_guard"]["device_quality"]["no_primary"], 0)
        for c in self.arm["companies"].values():
            self.assertEqual(set(c["hand"]), {"device_quality", "device_quality_own"})
            self.assertIn("single_narrative_terms", c)
            self.assertIn("echo", c)

    def test_the_drafted_packs_pass_the_floor_counted_by_days(self) -> None:
        for label, c in self.arm["companies"].items():
            self.assertTrue(c["check"]["passed"], label)
            labels = {x["label"] for x in c["draft"]["categories"]["passing_floor"]}
            specific = {p["label"] for p in c["draft"]["predicates"]}
            for generic in SETTINGS["params"]["non_specific_labels"]:
                self.assertNotIn(generic.title(), specific)
            self.assertTrue(labels)

    def test_nothing_identifying_leaves(self) -> None:
        outputs = self.md + json.dumps(self.report) + json.dumps(self.arm) + self.steps["score"].stdout + \
            self.steps["report"].stdout
        for needle in (*NAMES, *NEVER_EXPORTED, "Template report"):
            self.assertNotIn(needle, outputs)
        keys = set()
        days = set()
        for f in self.split.glob("d*.jsonl"):
            for line in f.read_text().splitlines():
                row = json.loads(line)
                keys.add(row["mdr_report_key"])
                days.add(row["received_day"])
        tokens = set(re.findall(r"[0-9A-Za-z]+", self.md + json.dumps(self.report)))
        self.assertEqual(tokens & keys, set())
        self.assertEqual(tokens & days, set())

    def test_the_uploaded_packs_hold_no_name_or_key(self) -> None:
        packs = "".join(p.read_text() for p in (self.tmp / "arms" / "maude").rglob("pack/*") if p.is_file())
        for needle in NAMES:
            self.assertNotIn(needle, packs)


class GuardTests(FullArmRun):
    """The report's guard on D003's refused columns (makers[], the record key, the received day) and the fields the
    export never holds (patient, report number, lot, UDI): a planted value withholds the report."""

    def rerun(self, mutate: Any) -> dict[str, Any]:
        arm = copy.deepcopy(self.arm)
        mutate(arm)
        d = self.tmp / f"guard-{random.Random().randrange(10**9)}"
        (d / "maude").mkdir(parents=True)
        (d / "maude" / "arm.json").write_text(json.dumps(arm))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            return REP.run_report(SETTINGS, str(self.settings_path), "0" * 64, [d / "maude"], None, d / "report",
                                  lambda: "0" * 40)

    def test_a_maker_name_or_a_word_of_one_withholds(self) -> None:
        names = chosen_names()
        for planted, kind in ((names[4], "refused_equal"), (names[2].split()[0].lower(), "refused_name_word")):
            with self.subTest(planted=planted):
                def plant(arm: dict[str, Any], s: str = planted) -> None:
                    arm["companies"]["d1"]["draft"]["predicates"][0]["first_terms"][0] = s
                doc = self.rerun(plant)
                self.assertEqual(doc["verdict"], "withheld")
                self.assertGreaterEqual(doc["guard_hits"]["maude"][kind], 1)

    def test_a_record_key_or_a_kept_day_in_the_report_withholds(self) -> None:
        row = json.loads((self.split / "d2.jsonl").read_text().splitlines()[3])
        for planted in (row["mdr_report_key"], row["received_day"]):
            with self.subTest(planted=planted):
                def plant(arm: dict[str, Any], s: str = planted) -> None:
                    arm["companies"]["d1"]["error_note"] = f"see {s}"
                doc = self.rerun(plant)
                self.assertEqual(doc["verdict"], "withheld")
                self.assertTrue(any(doc["backstop_hits"].values()))

    def test_a_clean_arm_clears(self) -> None:
        doc = self.rerun(lambda arm: None)
        self.assertNotEqual(doc["verdict"], "withheld")

    def test_patient_report_lot_and_udi_values_never_reach_the_export(self) -> None:
        text = "".join(f.read_text() for f in self.split.glob("*"))
        for needle in ("LOTX", "UDIX", "RPTX-", "PATIENTAGEX", '"patient', '"report_number', '"lot_number', '"udi',
                       '"device', '"brand_name', '"model_number'):
            self.assertNotIn(needle, text)


# =================================================================================================== the workflow

def _yaml() -> Any:
    try:
        import yaml
    except ImportError:
        return None
    return yaml


class WorkflowTests(TempDir):
    text = WORKFLOW.read_text(encoding="utf-8")

    def step_run(self, name: str) -> str:
        lines_ = self.text.splitlines()
        start = next(i for i, line in enumerate(lines_) if line.strip() == f"- name: {name}")
        body, inside = [], False
        for line in lines_[start + 1:]:
            if line.startswith("      - "):
                break
            if line.strip() == "run: |":
                inside = True
                continue
            if line.strip().startswith("run: ") and not inside:
                return line.strip()[5:]
            if inside:
                body.append(line)
        indent = min(len(x) - len(x.lstrip()) for x in body if x.strip())
        return "\n".join(x[indent:] for x in body).strip("\n")

    def test_triggers_permissions_and_pins(self) -> None:
        self.assertIn('paths: ["docs/collective/onboard/run-003.json"]', self.text)
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("timeout-minutes: 180", self.text)
        pins = set(re.findall(r"uses: (\S+)", self.text))
        self.assertEqual(pins, set(re.findall(r"uses: (\S+)", D002_WORKFLOW.read_text())))
        self.assertIn("persist-credentials: false", self.text)

    def test_the_steps_in_order_and_nothing_before_the_marker_is_conditional(self) -> None:
        names = re.findall(r"- name: (.+)", self.text)
        self.assertEqual(names, ["offline tests", "settings check (run-003.json's experiment and sha256 against its "
                                 "settings file)", "fetch, select and split (a failure here is not a run)",
                                 "run start", "score maude", "report", "collect the files to upload"])
        before = self.text.split("- name: run start")[0]
        self.assertNotIn("if:", before)
        self.assertIn('echo "=== $EXPERIMENT RUN-START ==="', self.text)
        self.assertEqual(self.step_run("fetch, select and split (a failure here is not a run)"),
                         'python tools/onboard/fetch_openfda.py fetch --settings "$SETTINGS" --out work/split/maude')
        self.assertNotIn("--audit", self.step_run("report"))
        self.assertIn("tests.onboard.test_onboard_d003", self.step_run("offline tests"))
        self.assertIn("tests.onboard.test_d002_reproducible", self.step_run("offline tests"))
        self.assertIn("name: onboard-${{ env.EXPERIMENT }}", self.text)

    def test_the_upload_never_takes_the_exports(self) -> None:
        script = self.step_run("collect the files to upload").split("<<'EOF'\n", 1)[1].rsplit("\nEOF", 1)[0]
        work = self.tmp
        (work / "work" / "report").mkdir(parents=True)
        (work / "work" / "report" / "report.json").write_text(json.dumps({"verdict": "fail"}))
        (work / "work" / "report" / "report.md").write_text("# r\n")
        (work / "work" / "split" / "maude").mkdir(parents=True)
        (work / "work" / "split" / "maude" / "d1.jsonl").write_text("{}\n")
        (work / "work" / "split" / "maude" / "fetch.json").write_text("{}\n")
        arm = work / "work" / "arms" / "maude"
        for company, passed in (("d1", True), ("d2", False)):
            (arm / company / "pack").mkdir(parents=True)
            (arm / company / "pack" / "pack.json").write_text("{}")
            (arm / company / "check.json").write_text(json.dumps({"passed": passed}))
        (arm / "arm.json").write_text("{}")
        (work / "work" / "upload").mkdir()
        subprocess.run([sys.executable, "-c", script], cwd=work, check=True, capture_output=True)
        got = sorted(p.relative_to(work / "work" / "upload").as_posix()
                     for p in (work / "work" / "upload").rglob("*") if p.is_file())
        self.assertEqual(got, ["maude/arm.json", "maude/d1/pack/pack.json", "report.json", "report.md"])

    def settings_check(self, run: dict[str, Any] | None, settings_text: str | None = None) -> tuple[int, str, str]:
        root = self.tmp / "repo"
        (root / "docs/collective/onboard").mkdir(parents=True, exist_ok=True)
        text = settings_text if settings_text is not None else SETTINGS_PATH.read_text()
        (root / "docs/collective/onboard/D003-settings.json").write_text(text)
        run_file = root / "docs/collective/onboard/run-003.json"
        if run is not None:
            run_file.write_text(json.dumps(run))
        elif run_file.exists():
            run_file.unlink()
        env_file = root / "github_env"
        env_file.write_text("")
        script = self.step_run("settings check (run-003.json's experiment and sha256 against its settings file)")
        r = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=root, capture_output=True, text=True,
                           env=python_env(self.tmp, GITHUB_ENV=str(env_file)))
        return r.returncode, r.stdout + r.stderr, env_file.read_text()

    def test_the_settings_check(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("no bash")
        sha = hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest()
        good = {"experiment": "D003", "settings": "docs/collective/onboard/D003-settings.json",
                "settings_sha256": sha}
        code, out, env = self.settings_check(good)
        self.assertEqual(code, 0, out)
        self.assertEqual(env, "SETTINGS=docs/collective/onboard/D003-settings.json\nEXPERIMENT=D003\n")
        for bad, why in ((dict(good, experiment="D002"), "not a D003 run file"),
                         (dict(good, settings_sha256="0" * 64), "the settings file differs from the run file")):
            with self.subTest(why=why):
                code, out, env = self.settings_check(bad)
                self.assertNotEqual(code, 0)
                self.assertIn(why, out)
                self.assertEqual(env, "")
        other = SETTINGS_PATH.read_text().replace('"experiment": "D003"', '"experiment": "D004"')
        code, out, env = self.settings_check(dict(good, settings_sha256=hashlib.sha256(other.encode()).hexdigest()),
                                             other)
        self.assertNotEqual(code, 0)
        self.assertIn("name different experiments", out)
        code, out, env = self.settings_check(None)
        self.assertNotEqual(code, 0)

    def test_parses_as_yaml(self) -> None:
        yaml = _yaml()
        if yaml is None:
            self.skipTest("PyYAML is not installed")
        doc = yaml.safe_load(self.text)
        self.assertEqual(list(doc["jobs"]), ["onboard"])


class D002FrozenTests(unittest.TestCase):
    """D002's rule, settings and run file, D001's and the vehicle hand pack do not change; onboard-run.yml refuses a
    D003 run file before any download."""

    PINS = {"docs/collective/onboard/D002-settings.json":
            "8bb9ba6ab9c8d4dca14ac86aa8df89f8cd1926b5355b9908ec3b71020c42c0e5",
            "docs/collective/onboard/D001-settings.json":
            "f319aae9bbc503440f0e2d509e7784066fc7ccc775c6924f02741a7e6a792c90"}

    def test_the_settings_are_frozen(self) -> None:
        for rel, sha in self.PINS.items():
            self.assertEqual(hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(), sha)
        run = json.loads((ONBOARD_DOCS / "run-002.json").read_text())
        self.assertEqual(run, {"experiment": "D002", "settings": "docs/collective/onboard/D002-settings.json",
                               "settings_sha256": self.PINS["docs/collective/onboard/D002-settings.json"]})
        self.assertFalse((ONBOARD_DOCS / "run-003.json").exists())

    def test_onboard_run_refuses_a_d003_run_file(self) -> None:
        """A push of run-003.json starts onboard-run.yml too: its settings check takes the newest run file, which is
        run-003.json, and refuses it before any download, setting nothing."""
        if shutil.which("bash") is None:
            self.skipTest("no bash")
        text = D002_WORKFLOW.read_text()
        self.assertIn('paths: ["docs/collective/onboard/run-*.json"]', text)
        holder = type("Holder", (), {"text": text})()
        script = WorkflowTests.step_run(holder, "settings check (the run file's experiment and sha256 against its "
                                                "settings file)")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            onboard = repo / "docs" / "collective" / "onboard"
            onboard.mkdir(parents=True)
            for name in ("D002-settings.json", "D003-settings.json", "run-001.json", "run-002.json"):
                shutil.copy(ONBOARD_DOCS / name, onboard / name)
            (onboard / "run-003.json").write_text(json.dumps(
                {"experiment": "D003", "settings": "docs/collective/onboard/D003-settings.json",
                 "settings_sha256": hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest()}))
            env_file = Path(tmp) / "github_env"
            env_file.write_text("")
            r = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=repo, capture_output=True,
                               text=True, env=python_env(Path(tmp), GITHUB_ENV=str(env_file)))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("AssertionError: not a D002 run file", r.stdout + r.stderr)
            self.assertIn("run file: docs/collective/onboard/run-003.json", r.stdout)
            self.assertEqual(env_file.read_text(), "")

    def test_the_rule_is_as_committed_with_no_run(self) -> None:
        text = CHOICE.read_text()
        self.assertEqual(text.split("## Runs", 1)[1].strip(), "None yet.")
        self.assertEqual(hashlib.sha256(CHOICE.read_bytes()).hexdigest(),
                         "78dd1176087a1da4575c0f1b714ac2345eaa47022ad3193108ffe0e8cb24752a")


if __name__ == "__main__":
    unittest.main()

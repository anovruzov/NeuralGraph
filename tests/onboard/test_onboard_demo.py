"""The drafter demo (``demo/onboard/run_demo.py``), offline, on a synthetic accident file in MSHA's layout that the
tests write: every field in double quotes, pipe-delimited, dates mm/dd/yyyy, the header bare. Every value is invented.
No public record is read.

Markers are planted in the identifying columns and in a few narratives (an operator's name, controller, operator and
contractor ids, mine ids, document numbers, a person's name and a sentence). Every printed or written output is
scanned for them, and for any eight consecutive words of any narrative. The reading numbers are checked against
``score.run_arm`` on the same export, and the audit against D002's M4 path (``onboard export`` and
``pilot.audit run``)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import random
import re
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "docs" / "collective" / "onboard" / "D002-settings.json"
SETTINGS = json.loads(SETTINGS_PATH.read_text())
COLUMNS = SETTINGS["arms"]["msha"]["columns"]
WORKFLOW = ROOT / ".github" / "workflows" / "onboard-demo.yml"
RUN_WORKFLOW = ROOT / ".github" / "workflows" / "onboard-run.yml"
DEMO_DIR = ROOT / "demo" / "onboard"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


RD = _load("run_demo", DEMO_DIR / "run_demo.py")

from mycelic.collective.onboard import report as R  # noqa: E402
from mycelic.collective.onboard import score as S  # noqa: E402
from mycelic.collective.onboard.__main__ import main as onboard_main  # noqa: E402
from mycelic.collective.onboard.check import ngram_hits  # noqa: E402
from mycelic.collective.onboard.exports import read_export  # noqa: E402
from mycelic.collective.pilot import audit as A  # noqa: E402

# --------------------------------------------------------------------------------------------------- the file

CUES = {"FALL OF ROOF OR BACK": ("roof", "rock", "bolter", "brow", "rib"),
        "HANDLING OF MATERIALS": ("lifting", "carrying", "bag", "strained", "pallet"),
        "SLIP OR FALL OF PERSON": ("slipped", "icy", "walkway", "stairs", "ladder"),
        "POWERED HAULAGE": ("shuttle", "scoop", "conveyor", "haul", "trolley"),
        "MACHINERY": ("crusher", "pinch", "guard", "drill", "belt"),
        "NO VALUE FOUND": ("assorted", "various", "general")}
FILLER = ("employee", "working", "shift", "crew", "morning", "reported", "nearby", "section", "worker", "tool",
          "area", "while", "during", "noticed", "returned", "afternoon")
RARE = ("EXPLODING VESSELS UNDER PRESSURE", ("vessel", "pressure"))
# planted markers: none may appear in any output
OPERATOR = {"controller_id": "C00417", "controller_name": "Quillfeather Mining Holdings",
            "operator_id": "P90417", "operator_name": "Quillfeather Ridge Operations", "contractor_id": "ZXQ7"}
OTHER_OPERATOR = {"controller_id": "C00888", "controller_name": "Brackenridge Aggregates Group",
                  "operator_id": "P90888", "operator_name": "Brackenridge Hollow Quarry", "contractor_id": "WVK9"}
SMALL_OPERATOR = {"controller_id": "C00999", "controller_name": "Tamsworth Pebble Concern",
                  "operator_id": "P90999", "operator_name": "Tamsworth Pebble Pit", "contractor_id": "JJQ2"}
PERSON = "Ezekiel Thornbury"
SENTENCE = "The zebra lantern quietly hummed beneath the copper gantry all night long"
NAME_WORD = "quillfeather"          # a word of the controller's name, written in many narratives: refused as a term


def mine_id(base: int, i: int) -> str:
    return f"{base + i:07d}"


class Writer:
    """Accident rows in MSHA's layout; document numbers in order."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.lines: list[str] = ["|".join(COLUMNS)]
        self.doc = 0
        self.narratives: list[str] = []
        self.ids: dict[str, set[str]] = {"documents": set(), "mines": set(), "closed": set()}

    def narrative(self, cat: str, extra: str = "") -> str:
        """Two cue words of the category, filler, and in one narrative of three a cue word of another category, so
        that no reader is perfect."""
        words = CUES.get(cat, RARE[1] if cat == RARE[0] else ())
        noise = []
        if self.rng.random() < 0.35:
            noise = [self.rng.choice(CUES[self.rng.choice(sorted(c for c in CUES if c != cat))])]
        first = " ".join(self.rng.sample(words, 2) + noise + self.rng.sample(FILLER, 5))
        second = " ".join(self.rng.sample(FILLER, 4) + [str(self.rng.randint(2, 99)), "feet"])
        return f"{first.capitalize()}. {second.capitalize()}.{(' ' + extra) if extra else ''}"

    def add(self, op: dict[str, str], mine: str, day: date, cat: str, text: str | None = None) -> None:
        self.doc += 1
        doc = f"2201{self.doc:08d}"
        closed = f"9901{self.doc:08d}"
        narrative = self.narrative(cat) if text is None else text
        row = dict.fromkeys(COLUMNS, "")
        row.update({"MINE_ID": mine, "CONTROLLER_ID": op["controller_id"], "CONTROLLER_NAME": op["controller_name"],
                    "OPERATOR_ID": op["operator_id"], "OPERATOR_NAME": op["operator_name"],
                    "CONTRACTOR_ID": op["contractor_id"], "DOCUMENT_NO": doc, "CLOSED_DOC_NO": closed,
                    "FIPS_STATE_CD": "54", "ACCIDENT_DT": day.strftime("%m/%d/%Y"), "CAL_YR": str(day.year),
                    "CLASSIFICATION": cat, "NARRATIVE": narrative, "COAL_METAL_IND": "C"})
        self.lines.append("|".join('"' + row[c].replace('"', '""') + '"' for c in COLUMNS))
        self.narratives.append(narrative)
        self.ids["documents"].add(doc)
        self.ids["mines"].add(mine)
        self.ids["closed"].add(closed)

    def day(self, first: date, last: date) -> date:
        return first + timedelta(days=self.rng.randrange((last - first).days + 1))

    def data(self) -> bytes:
        return ("\r\n".join(self.lines) + "\r\n").encode("utf-8")


def accident_file(seed: int = 7, scale: int = 1, fillers: int = 0) -> Writer:
    """Three operators by construction. The first (c1) has the most training rows: five mines, six filed categories
    (one non-specific) and one under the floor, a background of held-out rows and, in 2023, a burst of one category at
    three mines. A word of its controller's name is written into many of its narratives, its person's name and a
    sentence into a few. The second qualifies too (c2); the third has too few held-out rows. ``scale`` multiplies the
    rows; ``fillers`` adds that many small non-qualifying operators (for a file of MSHA's size)."""
    w = Writer(seed)
    rng = w.rng
    mines = [mine_id(4612001, i) for i in range(5)]
    train = (date(2015, 1, 1), date(2021, 12, 31))
    for cat in sorted(CUES):
        for _ in range(60 * scale):
            text = None
            if cat == "MACHINERY" and rng.random() < 0.4:
                text = w.narrative(cat, f"The {NAME_WORD} crusher was idle.")
            w.add(OPERATOR, rng.choice(mines), w.day(*train), cat, text)
    for i in range(4):
        w.add(OPERATOR, mines[i % 2], w.day(*train), RARE[0])
    w.add(OPERATOR, mines[0], date(2018, 5, 7), "MACHINERY", w.narrative("MACHINERY", f"{PERSON} saw it."))
    w.add(OPERATOR, mines[1], date(2019, 6, 3), "HANDLING OF MATERIALS",
          w.narrative("HANDLING OF MATERIALS", f"{PERSON} helped."))
    for _ in range(3):
        w.add(OPERATOR, mines[2], w.day(*train), "POWERED HAULAGE", w.narrative("POWERED HAULAGE", SENTENCE + "."))
    start = date(2022, 1, 3)
    for week in range(156):
        monday = start + timedelta(days=7 * week)
        for _ in range(2 * scale):
            cat = rng.choice(sorted(CUES))
            w.add(OPERATOR, rng.choice(mines), monday + timedelta(days=rng.randrange(5)), cat)
        if 60 <= week < 68:
            for mine in mines[:3]:
                for _ in range(4 * scale):
                    w.add(OPERATOR, mine, monday + timedelta(days=rng.randrange(5)), "HANDLING OF MATERIALS")
    other = [mine_id(4713001, i) for i in range(4)]
    for cat in sorted(CUES)[:5]:
        for _ in range(55 * scale):
            w.add(OTHER_OPERATOR, rng.choice(other), w.day(*train), cat)
    for _ in range(120 * scale):
        w.add(OTHER_OPERATOR, rng.choice(other), w.day(date(2022, 1, 1), date(2024, 12, 31)), rng.choice(sorted(CUES)))
    small = [mine_id(4814001, i) for i in range(3)]
    for _ in range(50):
        w.add(SMALL_OPERATOR, rng.choice(small), w.day(*train), "MACHINERY")
    for _ in range(20):
        w.add(SMALL_OPERATOR, rng.choice(small), w.day(date(2022, 1, 1), date(2024, 12, 31)), "MACHINERY")
    for k in range(fillers):
        op = {"controller_id": f"F{k:05d}", "controller_name": f"Filler Holdings {k}", "operator_id": f"Q{k:05d}",
              "operator_name": f"Filler Pit {k}", "contractor_id": ""}
        for _ in range(130):
            w.add(op, mine_id(5000000 + 2 * k, 0), w.day(date(2005, 1, 1), date(2024, 12, 31)),
                  rng.choice(sorted(CUES)))
    return w


def write_raw(raw: Path, w: Writer) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Accidents.txt", w.data())
    (raw / "Accidents.zip").write_bytes(buf.getvalue())
    (raw / "Accidents_Definition_File.txt").write_text("CLASSIFICATION\tThe circumstances.\n\nNARRATIVE\tThe text.\n")
    (raw / "download.json").write_text(json.dumps([{"file": "Accidents.zip", "bytes": len(buf.getvalue()),
                                                    "sha256": "0" * 64}]))


def small_settings(path: Path, B: int = 200) -> Path:
    """D002's settings with fewer bootstrap draws, so the tests run in seconds. The demo then says the settings are
    not D002's."""
    s = json.loads(SETTINGS_PATH.read_text())
    s["bootstrap"]["B"] = B
    path.write_text(json.dumps(s, indent=2, sort_keys=True))
    return path


def secrets(w: Writer) -> list[str]:
    out = [PERSON, SENTENCE, NAME_WORD, *w.ids["documents"], *w.ids["mines"], *w.ids["closed"]]
    for op in (OPERATOR, OTHER_OPERATOR, SMALL_OPERATOR):
        out += list(op.values())
    out += PERSON.split()
    return sorted(set(out))


def token_hits(text: str, values: list[str]) -> list[str]:
    """Values found in ``text`` as whole tokens, case-insensitive (a sha256 or a count can hold a digit run)."""
    low = text.lower()
    return [v for v in values if re.search(r"(?<![0-9a-z])" + re.escape(v.lower()) + r"(?![0-9a-z])", low)]


# --------------------------------------------------------------------------------------------------- one demo run

class DemoRun(unittest.TestCase):
    """One run of the demo for c1 on the synthetic file, shared by the tests below."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.writer = accident_file()
        write_raw(cls.tmp / "raw", cls.writer)
        cls.settings = small_settings(cls.tmp / "settings.json")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cls.code = RD.main(["--raw", str(cls.tmp / "raw"), "--out", str(cls.tmp / "out"), "--company", "c1",
                                "--settings", str(cls.settings), "--work", str(cls.tmp / "work"), "--markers"])
        cls.stdout, cls.stderr = out.getvalue(), err.getvalue()
        cls.json_text = (cls.tmp / "out" / "demo.json").read_text()
        cls.html_text = (cls.tmp / "out" / "demo.html").read_text()
        cls.doc = json.loads(cls.json_text)
        cls.steps = {s["id"]: s for s in cls.doc["steps"]}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


class EveryStepTests(DemoRun):
    def test_every_step_runs_in_order_with_its_time(self) -> None:
        self.assertEqual(self.code, 0, self.stderr)
        self.assertEqual(self.doc["status"], "shown")
        self.assertEqual([s["id"] for s in self.doc["steps"]], ["a", "b", "c", "d", "e"])
        for sid in "abcd":
            self.assertIsInstance(self.steps[sid]["seconds"], float)
            self.assertGreaterEqual(self.steps[sid]["seconds"], 0.0)
        self.assertIsNone(self.steps["e"]["seconds"])
        for label in ("(a) read the export", "(b) draft the pack", "(c) read the held-out years",
                      "(d) count across the mines", "guard"):
            self.assertIn(label, self.stderr)
        positions = [self.stdout.index(f"({sid}) ") for sid in "abcde"]
        self.assertEqual(positions, sorted(positions))

    def test_the_export_read(self) -> None:
        a = self.steps["a"]["data"]
        self.assertEqual(a["file"]["columns"], 57)
        self.assertEqual(a["file"]["date_format"], "M/D/YYYY")
        self.assertEqual(a["file"]["rows"], len(self.writer.lines) - 1)
        self.assertEqual((a["file"]["operators"], a["file"]["qualifying"], a["file"]["used"]), (3, 2, 2))
        self.assertEqual(a["export"]["columns"], 57)
        self.assertEqual(a["roles"]["narrative"], "NARRATIVE")
        self.assertEqual(a["roles"]["refused"], SETTINGS["arms"]["msha"]["roles"]["forbidden"])
        self.assertIn("Dates read as M/D/YYYY", self.stdout)

    def test_the_drafted_pack(self) -> None:
        b = self.steps["b"]["data"]
        self.assertTrue(b["privacy_floor"]["passed"])
        self.assertEqual(b["categories"]["specific"], 5)
        self.assertEqual(b["categories"]["below_floor"], 1)
        labels = [p["label"] for p in b["predicates"]]
        self.assertIn("FALL OF ROOF OR BACK", labels)
        self.assertNotIn("NO VALUE FOUND", labels)
        roof = next(p for p in b["predicates"] if p["label"] == "FALL OF ROOF OR BACK")
        self.assertIn("roof", roof["first_terms"])
        self.assertLessEqual(len(roof["first_terms"]), 10)
        # each predicate line exactly as D002's report.render prints it
        for p in b["predicates"]:
            line = (f"{p['label']} ({p['records']} corpus records; {p['refused_assignable']} refused terms would have "
                    f"been assigned): {', '.join(p['first_terms'])}")
            self.assertIn(line, self.stdout)
        self.assertGreater(b["refusal"]["terms"], 0)            # the controller's name word was refused

    def test_the_reading_says_the_settings_are_not_d002s(self) -> None:
        c = self.steps["c"]["data"]
        self.assertEqual(c["d002"]["status"], "not_comparable")
        self.assertIn("These settings are not D002's", self.stdout)
        self.assertIn("D002's run 38024536763 recorded drafted 0.568 and majority prior 0.360 for c1", self.stdout)
        self.assertEqual(c["sample"]["drawn"], SETTINGS["arms"]["msha"]["per_company"])
        self.assertEqual(set(c["readers"]), set(S.READERS))

    def test_the_audit(self) -> None:
        d = self.steps["d"]["data"]
        self.assertEqual(d["mines"], 5)
        self.assertEqual([m["mine"] for m in d["per_mine"]], ["m01", "m02", "m03", "m04", "m05"])
        self.assertEqual(d["outcomes"], 0)
        self.assertGreater(d["cells"], 0)
        self.assertGreater(d["suppressed_cells"], 0)
        self.assertGreater(d["alerts"]["X"], 0)
        self.assertTrue(d["review_list"])
        for item in d["review_list"]:
            self.assertEqual(sorted(item), ["category", "channels", "first_week", "last_week", "mines"])
            self.assertRegex(item["first_week"], r"^\d{4}-W\d{2}$")
            self.assertIsInstance(item["mines"], int)
        self.assertTrue(any(i["category"] == "HANDLING OF MATERIALS" and i["mines"] >= 2 for i in d["review_list"]))
        self.assertIn("A count under 3 leaves as '<3'", self.stdout)
        self.assertIn("match no outcome on record", self.stdout)

    def test_the_markers_hold_demo_json(self) -> None:
        m = re.search(r"=== onboard-demo demo\.json BEGIN lines=(\d+) sha256=([0-9a-f]{64}) ===\n(.*?)"
                      r"=== onboard-demo demo\.json END ===", self.stdout, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(3), self.json_text)
        import hashlib
        self.assertEqual(m.group(2), hashlib.sha256(self.json_text.encode("utf-8")).hexdigest())

    def test_console_json_and_html_hold_the_same_lines(self) -> None:
        console = self.stdout.split("=== onboard-demo")[0]
        for step in self.doc["steps"]:
            for block in step["blocks"]:
                for line in block.get("lines", []) + block.get("items", []):
                    self.assertIn(line, console)
                    self.assertIn(__import__("html").escape(line), self.html_text)


class NothingIdentifyingLeavesTests(DemoRun):
    def outputs(self) -> dict[str, str]:
        return {"stdout": self.stdout, "stderr": self.stderr, "demo.json": self.json_text, "demo.html": self.html_text}

    def test_the_out_directory_holds_only_the_two_files(self) -> None:
        self.assertEqual(sorted(p.name for p in (self.tmp / "out").iterdir()), ["demo.html", "demo.json"])

    def test_no_planted_marker_is_printed_or_written(self) -> None:
        values = secrets(self.writer)
        self.assertGreater(len(values), 1000)
        for name, text in self.outputs().items():
            with self.subTest(output=name):
                self.assertEqual(token_hits(text, values), [])

    def test_no_eight_words_of_any_narrative(self) -> None:
        for name, text in self.outputs().items():
            with self.subTest(output=name):
                self.assertEqual(ngram_hits([text], self.writer.narratives, 8), set())

    def test_the_scan_would_find_a_planted_marker(self) -> None:
        planted = f"{self.stdout} mine {sorted(self.writer.ids['mines'])[0]} {OPERATOR['controller_name']}"
        # the mine id, the controller's name and the name's word written into narratives
        self.assertEqual(len(token_hits(planted, secrets(self.writer))), 3)
        self.assertTrue(ngram_hits([self.writer.narratives[5]], self.writer.narratives, 8))


# --------------------------------------------------------------------------------------------------- equal numbers

class SameNumbersTests(DemoRun):
    """The demo's reading numbers are the onboard score functions' numbers for the same export (``run_arm``, D002's
    per-company step); its audit is D002's M4 path (``onboard export`` then ``pilot.audit run``)."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        settings, sha = S.load_settings(cls.settings)
        companies = json.loads((cls.tmp / "work" / "split" / "companies.json").read_text())
        exports = cls.tmp / "arm-in"
        exports.mkdir()
        (exports / "c1.txt").write_bytes((cls.tmp / "work" / "split" / companies["c1"]["file"]).read_bytes())
        (exports / "companies.json").write_text(json.dumps({"c1": companies["c1"]}))
        cls.arm = S.run_arm(settings, sha, "msha", exports, cls.tmp / "arm", lambda: "test")
        with contextlib.redirect_stdout(io.StringIO()):
            cls.demo_doc = RD.run(cls.tmp / "raw", "c1", cls.settings, cls.tmp / "work2", lambda line: None)[0]

    def test_every_reader_equals_run_arm(self) -> None:
        mine = {s["id"]: s for s in self.demo_doc["steps"]}["c"]["data"]["readers"]
        theirs = self.arm["companies"]["c1"]["readers"]
        for r in S.READERS:
            with self.subTest(reader=r):
                t = theirs[r]
                self.assertEqual(mine[r]["f1"], t["micro"]["f1"])
                self.assertEqual((mine[r]["ci_low"], mine[r]["ci_high"]), (t["micro"]["ci_low"], t["micro"]["ci_high"]))
                self.assertEqual((mine[r]["precision"], mine[r]["recall"]), (t["micro"]["precision"],
                                                                             t["micro"]["recall"]))
                self.assertEqual(mine[r]["macro_f1"], t["macro"]["f1"])
                self.assertEqual(mine[r]["coverage"], t["coverage"])
                self.assertEqual(mine[r]["records"], t["records"])
        self.assertEqual({s["id"]: s for s in self.demo_doc["steps"]}["c"]["data"]["sample"],
                         self.arm["companies"]["c1"]["sample"])

    def test_the_written_json_holds_the_same_numbers_rounded(self) -> None:
        theirs = self.arm["companies"]["c1"]["readers"]
        for r in S.READERS:
            self.assertEqual(self.steps["c"]["data"]["readers"][r]["f1"], S.rounded(theirs[r]["micro"]["f1"]))

    def test_the_drafted_terms_equal_run_arm(self) -> None:
        mine = {s["id"]: s for s in self.demo_doc["steps"]}["b"]["data"]["predicates"]
        theirs = self.arm["companies"]["c1"]["draft"]["predicates"]
        self.assertEqual([(p["label"], p["records"], p["first_terms"]) for p in mine],
                         [(p["label"], p["records"], p["first_terms"]) for p in theirs])
        self.assertTrue(self.arm["companies"]["c1"]["check"]["passed"])

    def test_the_audit_equals_d002s_m4_path(self) -> None:
        tmp = self.tmp / "m4"
        settings = json.loads(self.settings.read_text())
        test = settings["arms"]["msha"]["test"]
        roles = self.tmp / "arm" / "c1" / "roles.json"
        pack = self.tmp / "arm" / "c1" / "pack"
        split = self.tmp / "work" / "split"
        with contextlib.redirect_stdout(io.StringIO()):
            code = onboard_main(["export", "--export", str(split / "c1.txt"), "--roles", str(roles), "--pack",
                                 str(pack), "--from", test[0], "--to", test[1], "--out", str(tmp / "records.jsonl")])
            self.assertEqual(code, 0)
            (tmp / "outcomes.csv").write_text("outcome_id,opened,entity_type,entity_id,predicate\n")
            code = A.main(["run", "--pack", str(pack), "--records", str(tmp / "records.jsonl"), "--outcomes",
                           str(tmp / "outcomes.csv"), "--out", str(tmp / "audit")])
            self.assertEqual(code, 0)
        audit = json.loads((tmp / "audit" / "audit.json").read_text())
        summary = R.audit_summary(tmp / "audit" / "audit.json")
        d = {s["id"]: s for s in self.demo_doc["steps"]}["d"]["data"]
        self.assertEqual((d["records"], d["mines"], d["weeks_evaluated"], len(d["review_list"])),
                         (summary["records"], summary["sites"], summary["weeks_evaluated"], summary["review_list"]))
        self.assertEqual(d["alerts"], summary["alerts"])
        self.assertEqual([(i["mines"], i["channels"], i["first_week"], i["last_week"]) for i in d["review_list"]],
                         [(len(e["sites"]), e["channels"], min(e["alert_weeks"]), max(e["alert_weeks"]))
                          for e in audit["review"]])

    def test_the_cells_counted_are_every_cell_the_sites_sent(self) -> None:
        d = {s["id"]: s for s in self.demo_doc["steps"]}["d"]["data"]
        self.assertEqual(d["cells"], sum(m["cells"] for m in d["per_mine"]))
        self.assertTrue(all(m["weekly_bundles"] > 0 for m in d["per_mine"]))
        self.assertIs(A.run_pipeline, __import__("mycelic.collective.evaluate.baselines",
                                                  fromlist=["run_pipeline"]).run_pipeline)   # the watcher put it back


# --------------------------------------------------------------------------------------------------- D002's record

class D002RecordTests(unittest.TestCase):
    def test_the_choice_file_is_read(self) -> None:
        rec = RD.d002_record()
        self.assertTrue(rec["found"])
        self.assertEqual(rec["run"], "38024536763")
        self.assertEqual(rec["best_control"], "majority_prior")
        self.assertEqual(rec["companies"], {"c1": {"drafted": "0.568", "best_control": "0.360"},
                                            "c2": {"drafted": "0.604", "best_control": "0.355"},
                                            "c3": {"drafted": "0.558", "best_control": "0.260"},
                                            "c4": {"drafted": "0.530", "best_control": "0.320"},
                                            "c5": {"drafted": "0.532", "best_control": "0.425"}})
        self.assertEqual(rec["audit"], {"company": "c1", "records": "1,033", "sites": "10", "weeks": "139",
                                        "review_list": "4"})

    def test_a_file_without_the_figures_is_said_to_lack_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "choice.md"
            p.write_text("# A choice\n\n## Runs\n\nNone yet.\n")
            rec = RD.d002_record(p)
            self.assertFalse(rec["found"])
            cmp = RD.compare_reading("c1", {}, rec, True)
            self.assertEqual(cmp["status"], "not_recorded")
            self.assertIn("were not found", RD.reproduce_line(cmp, "c1"))
            self.assertFalse(RD.d002_record(Path(tmp) / "missing.md")["found"])

    def test_the_d002_settings_are_the_run_files(self) -> None:
        _, sha = S.load_settings(SETTINGS_PATH)
        self.assertEqual(sha, RD.run_file_sha())

    def fake(self, drafted: float, prior: float) -> dict[str, Any]:
        return {r: {"micro": {"f1": v}} for r, v in (("drafted", drafted), ("majority_prior", prior),
                                                       ("permuted_labels", 0.01), ("label_names", 0.04))}

    def test_reproduced_differs_and_not_comparable(self) -> None:
        rec = RD.d002_record()
        same = RD.compare_reading("c1", self.fake(0.56801, 0.35996), rec, True)
        self.assertEqual(same["status"], "reproduced")
        self.assertIn("These reproduce D002's run 38024536763 for c1", RD.reproduce_line(same, "c1"))
        other = RD.compare_reading("c1", self.fake(0.571, 0.36), rec, True)
        self.assertEqual(other["status"], "differs")
        self.assertIn("These differ from D002's run 38024536763", RD.reproduce_line(other, "c1"))
        self.assertEqual(RD.compare_reading("c1", self.fake(0.568, 0.36), rec, False)["status"], "not_comparable")
        self.assertEqual(RD.compare_reading("c9", self.fake(0.568, 0.36), rec, True)["status"], "not_recorded")

    def test_three_places_as_d002_printed_them(self) -> None:
        # D002's report rounds to four places in the file, then shows three: 0.56749 became 0.5675, shown 0.568
        self.assertEqual(RD.f3(0.56749), "0.568")
        self.assertEqual(f"{0.56749:.3f}", "0.567")
        self.assertEqual(RD.f3(0.56744), "0.567")
        self.assertEqual(RD.f3(None), "n/a")

    def test_the_audit_comparison(self) -> None:
        rec = RD.d002_record()
        data = {"records": 1033, "mines": 10, "weeks_evaluated": 139, "review_list": [{}] * 4}
        self.assertEqual(RD.compare_audit("c1", data, rec, True)["status"], "reproduced")
        self.assertEqual(RD.compare_audit("c1", dict(data, mines=9), rec, True)["status"], "differs")
        self.assertEqual(RD.compare_audit("c1", data, rec, False)["status"], "not_comparable")
        self.assertEqual(RD.compare_audit("c2", data, rec, True)["status"], "not_recorded")


# --------------------------------------------------------------------------------------------------- the guard

class GuardTests(DemoRun):
    def run_with(self, *patches: Any) -> tuple[int, str, str, Path]:
        out = Path(tempfile.mkdtemp(dir=self.tmp))
        o, e = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
                code = RD.main(["--raw", str(self.tmp / "raw"), "--out", str(out / "out"), "--settings",
                                str(self.settings)])
        return code, o.getvalue(), e.getvalue(), out / "out"

    def test_a_mine_id_in_the_rendered_text_withholds_everything(self) -> None:
        mine = sorted(self.writer.ids["mines"])[0]
        code, stdout, _, out = self.run_with(mock.patch.object(RD, "REVIEW_MEANS", RD.REVIEW_MEANS + f" {mine}"))
        self.assertEqual(code, 1)
        doc = json.loads((out / "demo.json").read_text())
        self.assertEqual(doc["status"], "withheld")
        self.assertEqual(doc["guard"]["report_site"], 1)
        for text in (stdout, (out / "demo.json").read_text(), (out / "demo.html").read_text()):
            self.assertEqual(token_hits(text, secrets(self.writer)), [])
            self.assertNotIn("FALL OF ROOF", text)

    def test_an_operator_name_in_the_rendered_text_withholds_everything(self) -> None:
        code, stdout, _, out = self.run_with(
            mock.patch.object(RD, "REVIEW_MEANS", RD.REVIEW_MEANS + " " + OTHER_OPERATOR["operator_name"]))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((out / "demo.json").read_text())["guard"]["refused_value"], 1)
        self.assertEqual(token_hits(stdout, secrets(self.writer)), [])

    def test_a_printed_label_holding_a_refused_value_withholds_everything(self) -> None:
        original = RD.printed_strings_entry

        def planted(b: Any, c: Any) -> dict[str, Any]:
            entry = original(b, c)
            facts = json.loads(json.dumps(entry["draft"]))
            facts["predicates"][0]["label"] = OPERATOR["controller_name"]
            return dict(entry, draft=facts)

        code, stdout, _, out = self.run_with(mock.patch.object(RD, "printed_strings_entry", planted))
        self.assertEqual(code, 1)
        doc = json.loads((out / "demo.json").read_text())
        self.assertEqual(doc["status"], "withheld")
        self.assertGreaterEqual(doc["guard"]["refused_equal"], 1)
        self.assertNotIn("Quillfeather", stdout)

    def test_an_error_text_naming_a_mine_is_withheld(self) -> None:
        export = read_export(self.tmp / "work" / "split" / "c1.txt")
        guard = RD.Guard(json.loads(self.settings.read_text()), export, ())
        mine = sorted(self.writer.ids["mines"])[0]
        self.assertNotIn(mine, guard.message(f"site {mine} rejected or duplicated records"))
        self.assertEqual(guard.message("no category reaches the predicate minimum"),
                         "no category reaches the predicate minimum")


# --------------------------------------------------------------------------------------------------- the html

class HtmlTests(DemoRun):
    def test_self_contained(self) -> None:
        low = self.html_text.lower()
        for needle in ("<script", "<link", "src=", "@import", "url(", "http://", "https://", "<iframe", "<img"):
            self.assertNotIn(needle, low)
        self.assertTrue(low.startswith("<!doctype html>"))

    def test_light_dark_and_phone_width(self) -> None:
        self.assertIn("prefers-color-scheme: dark", self.html_text)
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', self.html_text)
        self.assertIn("overflow-x: auto", self.html_text)
        self.assertIn("max-width: 880px", self.html_text)


# --------------------------------------------------------------------------------------------------- usage

class UsageTests(unittest.TestCase):
    def call(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = RD.main(list(argv))
        return code, err.getvalue()

    def test_errors_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            code, err = self.call("--raw", str(t / "none"), "--out", str(t / "out"))
            self.assertEqual(code, 2)
            self.assertIn("fetch_msha.py download", err)
            self.assertEqual(self.call("--raw", str(t), "--out", str(t / "out"), "--company", "C00417")[0], 2)
            code, err = self.call("--raw", str(t), "--out", str(t / "out"), "--work", str(t / "out" / "w"))
            self.assertEqual(code, 2)
            self.assertIn("cannot be --out or inside it", err)
            (t / "busy").mkdir()
            (t / "busy" / "x").write_text("x")
            self.assertEqual(self.call("--raw", str(t), "--out", str(t / "out"), "--work", str(t / "busy"))[0], 2)
            self.assertFalse((t / "out").exists())

    def test_an_operator_the_split_did_not_make(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            write_raw(t / "raw", accident_file())
            code, err = self.call("--raw", str(t / "raw"), "--out", str(t / "out"), "--company", "c4",
                                  "--settings", str(small_settings(t / "s.json")))
            self.assertEqual(code, 2)
            self.assertIn("no operator c4", err)
            self.assertEqual(token_hits(err, [OPERATOR["controller_id"], OTHER_OPERATOR["controller_id"]]), [])


# --------------------------------------------------------------------------------------------------- the workflow

def _yaml() -> Any:
    try:
        import yaml
    except ImportError:
        return None
    return yaml


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text()
        yaml = _yaml()
        cls.wf = yaml.safe_load(cls.text) if yaml is not None else None

    def test_triggers_permissions_and_timeout(self) -> None:
        if self.wf is None:
            self.skipTest("PyYAML is not installed")
        on = self.wf[True] if True in self.wf else self.wf["on"]
        self.assertIn("workflow_dispatch", on)
        self.assertEqual(on["push"]["paths"], ["demo/onboard/record-*.json"])
        self.assertEqual(self.wf["permissions"], {"contents": "read"})
        job = next(iter(self.wf["jobs"].values()))
        self.assertLessEqual(job["timeout-minutes"], 60)

    def test_actions_are_pinned_as_in_the_run_workflow(self) -> None:
        pins = set(re.findall(r"uses: (\S+@[0-9a-f]{40})", RUN_WORKFLOW.read_text()))
        used = re.findall(r"uses: (\S+)", self.text)
        self.assertTrue(used)
        for ref in used:
            self.assertIn(ref, pins)
        self.assertIn("persist-credentials: false", self.text)

    def test_the_steps(self) -> None:
        text = self.text
        order = [text.index(s) for s in ("tests.onboard.test_onboard_demo", "fetch_msha.py download",
                                         "demo/onboard/run_demo.py", "upload-artifact")]
        self.assertEqual(order, sorted(order))
        self.assertIn("--markers", text)
        self.assertIn('--company "$COMPANY"', text)
        self.assertIn("demo/onboard/record-*.json", text)
        self.assertIn("work/demo/demo.html", text)
        self.assertIn("work/demo/demo.json", text)
        self.assertNotIn("work/raw\n", text.split("upload-artifact")[1])

    def test_the_company_check_refuses_anything_but_a_label(self) -> None:
        self.assertIn("^c[1-9][0-9]?$", self.text)

    def test_no_record_file_is_committed(self) -> None:
        self.assertEqual(sorted(DEMO_DIR.glob("record-*.json")), [])


# --------------------------------------------------------------------------------------------------- the docs

class DocsTests(unittest.TestCase):
    def test_every_rate_in_the_docs_is_one_d002_recorded(self) -> None:
        recorded = set(re.findall(r"\d\.\d{3}", (ROOT / RD.CHOICE).read_text()))
        for name in ("README.md", "SCRIPT.md"):
            text = (DEMO_DIR / name).read_text()
            with self.subTest(doc=name):
                found = set(re.findall(r"(?<![\d.])\d\.\d{3}(?!\d)", text))
                self.assertTrue(found <= recorded, sorted(found - recorded))

    def test_the_readme_names_the_two_commands(self) -> None:
        text = (DEMO_DIR / "README.md").read_text()
        self.assertIn("python tools/onboard/fetch_msha.py download --out", text)
        self.assertIn("python demo/onboard/run_demo.py --raw", text)

    def test_the_talk_track_quotes_no_count_of_its_own(self) -> None:
        text = (DEMO_DIR / "SCRIPT.md").read_text()
        # beyond its time marks (0:00) and the test's name (D002) it may name only: the operator label c1, its length
        # (2 minutes) and the suppression rule the output prints ('<3')
        rest = re.sub(r"\b\d:\d{2}\b|\bD002\b", "", text)
        numbers = set(re.findall(r"\d[\d,.]*\d|\d", rest))
        self.assertEqual(numbers, {"1", "2", "3"})
        self.assertIn("'<3'", text)
        self.assertEqual(len(re.findall(r"^\| \d:\d{2}–\d:\d{2} \|", text, re.M)), 6)
        self.assertIn("| 1:50–2:00 |", text)


if __name__ == "__main__":
    unittest.main()

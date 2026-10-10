"""The drafter demo (``demo/onboard/run_demo.py``), offline, on a synthetic accident file in MSHA's layout that the
tests write: every field in double quotes, pipe-delimited, dates mm/dd/yyyy, the header bare. Every value is invented.
No public record is read.

Markers are planted in the identifying columns and in a few narratives (an operator's name, controller, operator and
contractor ids, mine ids, document numbers, a person's name and a sentence). Every printed or written output is
scanned for them, and for any eight consecutive words of any narrative. Two more files plant names in tens of
narratives, so that the drafter learns them as terms: capitalised in one, and in the other in capitals inside
mixed-case text, only in narratives written all in capitals, in lower case (words of the operators' names) and as a
place whose first word is also a common word. None may be printed. The reading numbers are checked against
``score.run_arm`` on the same export, and the audit against D002's M4 path (``onboard export`` and
``pilot.audit run``). What left the mines carries the mines' labels only, the mines behind each counted item come
from HQ's cells, and the review list's reference items are never shown as coming from the weekly cells."""
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
from mycelic.collective.onboard.exports import ExportError, read_export  # noqa: E402
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


def accident_file(seed: int = 7, scale: int = 1, fillers: int = 0, operator: dict[str, str] | None = None,
                  extra: Any = None, upper: Any = None) -> Writer:
    """Three operators by construction. The first (c1) has the most training rows: five mines, six filed categories
    (one non-specific) and one under the floor, a background of held-out rows and, in 2023, a burst of one category at
    three mines. A word of its controller's name is written into many of its narratives, its person's name and a
    sentence into a few. The second qualifies too (c2); the third has too few held-out rows. ``scale`` multiplies the
    rows; ``fillers`` adds that many small non-qualifying operators (for a file of MSHA's size). ``operator`` replaces
    c1's identifying values, and ``extra(category, k)`` gives text added to the k-th training narrative of a category,
    which is then written all in capitals when ``upper(category, k)`` is true (neither by default: the file is then the
    same)."""
    w = Writer(seed)
    rng = w.rng
    op = operator or OPERATOR
    mines = [mine_id(4612001, i) for i in range(5)]
    train = (date(2015, 1, 1), date(2021, 12, 31))
    for cat in sorted(CUES):
        for k in range(60 * scale):
            text = None
            if cat == "MACHINERY" and rng.random() < 0.4:
                text = w.narrative(cat, f"The {NAME_WORD} crusher was idle.")
            elif extra is not None and extra(cat, k):
                text = w.narrative(cat, extra(cat, k))
                if upper is not None and upper(cat, k):
                    text = text.upper()
            w.add(op, rng.choice(mines), w.day(*train), cat, text)
    for i in range(4):
        w.add(op, mines[i % 2], w.day(*train), RARE[0])
    w.add(op, mines[0], date(2018, 5, 7), "MACHINERY", w.narrative("MACHINERY", f"{PERSON} saw it."))
    w.add(op, mines[1], date(2019, 6, 3), "HANDLING OF MATERIALS",
          w.narrative("HANDLING OF MATERIALS", f"{PERSON} helped."))
    for _ in range(3):
        w.add(op, mines[2], w.day(*train), "POWERED HAULAGE", w.narrative("POWERED HAULAGE", SENTENCE + "."))
    start = date(2022, 1, 3)
    for week in range(156):
        monday = start + timedelta(days=7 * week)
        for _ in range(2 * scale):
            cat = rng.choice(sorted(CUES))
            w.add(op, rng.choice(mines), monday + timedelta(days=rng.randrange(5)), cat)
        if 60 <= week < 68:
            for mine in mines[:3]:
                for _ in range(4 * scale):
                    w.add(op, mine, monday + timedelta(days=rng.randrange(5)), "HANDLING OF MATERIALS")
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

_SHARED: dict[str, Any] = {}


def shared_run() -> dict[str, Any]:
    """The one run of the demo every :class:`DemoRun` reads (made once per test process; its directory is removed
    by ``tearDownModule``). Tests that need a directory of their own make it under ``tmp``, with a new name."""
    if not _SHARED:
        holder = tempfile.TemporaryDirectory()
        tmp = Path(holder.name)
        writer = accident_file()
        write_raw(tmp / "raw", writer)
        settings = small_settings(tmp / "settings.json")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = RD.main(["--raw", str(tmp / "raw"), "--out", str(tmp / "out"), "--company", "c1",
                            "--settings", str(settings), "--work", str(tmp / "work"), "--markers"])
        _SHARED.update(holder=holder, tmp=tmp, writer=writer, settings=settings, code=code, stdout=out.getvalue(),
                       stderr=err.getvalue())
    return _SHARED


def tearDownModule() -> None:
    if _SHARED:
        _SHARED.pop("holder").cleanup()
        _SHARED.clear()


class DemoRun(unittest.TestCase):
    """One run of the demo for c1 on the synthetic file, shared by the tests below."""

    @classmethod
    def setUpClass(cls) -> None:
        run = shared_run()
        cls.tmp, cls.writer, cls.settings = run["tmp"], run["writer"], run["settings"]
        cls.code, cls.stdout, cls.stderr = run["code"], run["stdout"], run["stderr"]
        cls.json_text = (cls.tmp / "out" / "demo.json").read_text()
        cls.html_text = (cls.tmp / "out" / "demo.html").read_text()
        cls.doc = json.loads(cls.json_text)
        cls.steps = {s["id"]: s for s in cls.doc["steps"]}

    def block(self, step: str, title_start: str) -> dict[str, Any]:
        found = [b for b in self.steps[step]["blocks"] if (b.get("title") or "").startswith(title_start)]
        self.assertEqual(len(found), 1, title_start)
        return found[0]


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
        self.assertEqual((a["file"]["controllers"], a["file"]["qualifying"], a["file"]["used"]), (3, 2, 2))
        self.assertEqual(a["split_column"], "CONTROLLER_ID")
        self.assertEqual(a["export"]["columns"], 57)
        self.assertEqual(a["roles"]["narrative"], "NARRATIVE")
        self.assertEqual(a["roles"]["refused"], SETTINGS["arms"]["msha"]["roles"]["forbidden"])
        self.assertIn("Dates read as M/D/YYYY", self.stdout)

    def test_the_controllers_are_named_as_controllers(self) -> None:
        # D002 splits by CONTROLLER_ID: the count is of controllers, and the screen says what D002 calls an operator
        self.assertIn("Controllers in the file: 3. A controller is a parent company that can run several operators "
                      "and mines. D002 splits the file by controller (CONTROLLER_ID) and calls each one an operator",
                      self.stdout)
        self.assertNotIn("Operators in the file", self.stdout)

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
        # nothing in this file is written like a name, so each predicate line is exactly as D002's report prints it
        self.assertEqual(b["names"], {"withheld_terms": 0, "refused_word": 0, "name_shaped": 0,
                                      "narratives_in_capitals": 0})
        for p in b["predicates"]:
            self.assertEqual(p["withheld_terms"], 0)
            line = (f"{p['label']} ({p['records']} corpus records; {p['refused_assignable']} refused terms would have "
                    f"been assigned): {', '.join(p['first_terms'])}")
            self.assertIn(line + "\n", self.stdout)
        self.assertGreater(b["refusal"]["terms"], 0)            # the controller's name word was refused

    def test_the_reading_says_the_settings_are_not_d002s(self) -> None:
        c = self.steps["c"]["data"]
        self.assertEqual(c["d002"]["status"], "not_comparable")
        self.assertIn("These settings are not D002's", self.stdout)
        self.assertIn("D002's run 38024536763 recorded drafted 0.568 and majority prior 0.360 for c1", self.stdout)
        self.assertEqual(c["sample"]["drawn"], SETTINGS["arms"]["msha"]["per_company"])
        self.assertEqual(set(c["readers"]), set(S.READERS))

    def test_the_input_is_compared_with_the_file_d002_read(self) -> None:
        raw = (self.tmp / "raw" / "Accidents.zip").read_bytes()
        self.assertEqual(self.doc["input"], {"file": "Accidents.zip", "bytes": len(raw),
                                             "sha256": __import__("hashlib").sha256(raw).hexdigest(),
                                             "d002": "revised"})
        self.assertIn("not the file D002 read, which was 52,269,752 bytes, sha256 62d0c861a5c3...", self.stdout)

    def test_the_audit(self) -> None:
        d = self.steps["d"]["data"]
        self.assertEqual(d["mines"], 5)
        self.assertEqual([m["mine"] for m in d["per_mine"]], ["m01", "m02", "m03", "m04", "m05"])
        self.assertTrue(d["same_under_own_ids"])
        self.assertEqual(d["outcomes"], 0)
        self.assertGreater(d["cells"], 0)
        self.assertGreater(d["suppressed_cells"], 0)
        self.assertGreater(d["sent"]["bytes"], 0)
        self.assertEqual(d["sent"]["bundles"], sum(m["weekly_bundles"] for m in d["per_mine"]))
        self.assertGreater(d["alerts"]["X"], 0)
        self.assertEqual((d["window_weeks"], d["min_sites"]), (8, 2))
        review = d["review"]
        self.assertEqual(review["total"], len(review["from_cells"]) + len(review["reference_only"]))
        self.assertTrue(review["from_cells"])
        for item in review["from_cells"]:
            self.assertEqual(sorted(item), ["alerts", "also", "category", "channels", "first_week", "last_week",
                                            "mines"])
            self.assertTrue(set(item["channels"]) <= {"X", "S"} and item["channels"])
            self.assertTrue(set(item["also"]) <= {"R_mf"})
            self.assertRegex(item["first_week"], r"^\d{4}-W\d{2}$")
            self.assertGreaterEqual(item["mines"], d["min_sites"])
        self.assertTrue(any(i["category"] == "HANDLING OF MATERIALS" and i["mines"] >= 2
                            for i in review["from_cells"]))
        self.assertIn("a count under 3 leaves as '<k', with k = 3 in its bundle", self.stdout)
        self.assertIn("none matches an outcome on record", self.stdout)

    def test_the_header_says_what_is_configured_for_this_field(self) -> None:
        header = "\n".join(self.doc["header"])
        self.assertNotIn("It holds no code for this field", header)
        self.assertIn("The code: the drafter (the onboard package) names no column of this source. The settings file "
                      "names them: the 57 columns it expects, 5 roles, 7 refused columns, and CONTROLLER_ID, which "
                      "splits the file by controller. tools/onboard/fetch_msha.py (", header)
        lines = len((ROOT / "tools" / "onboard" / "fetch_msha.py").read_text().splitlines())
        self.assertIn(f"tools/onboard/fetch_msha.py ({lines} lines) is MSHA's download and split code.", header)
        self.assertTrue(any(line.startswith('Not "no configuration".') for line in self.doc["not_shown"]))
        self.assertIn('  - Not "no configuration". The settings file names this source\'s columns, and fetch_msha.py '
                      "is download code for it", self.stdout)

    def test_the_outcomes_line_leaves_the_comparators_off_the_review_list(self) -> None:
        self.assertIn("Outcomes given: 0. So every X, S and R_mf alert lands on the review list, grouped by pattern; P "
                      "and PRR add nothing to it.", self.stdout)
        self.assertNotIn("So every alert lands on the review list", self.stdout)

    def test_the_markers_hold_demo_json(self) -> None:
        m = re.search(r"=== onboard-demo demo\.json BEGIN lines=(\d+) sha256=([0-9a-f]{64}) ===\n(.*?)"
                      r"=== onboard-demo demo\.json END ===", self.stdout, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(3), self.json_text)
        import hashlib
        self.assertEqual(m.group(2), hashlib.sha256(self.json_text.encode("utf-8")).hexdigest())

    def test_the_refused_columns_line_names_every_use(self) -> None:
        # CONTROLLER_ID's values cut the file, and the guard reads every refused value to check the outputs
        self.assertIn("Values of 7 columns are read only to split the file (CONTROLLER_ID), to refuse and withhold "
                      "terms, and to check every output, and are never shown: CONTROLLER_ID, CONTROLLER_NAME,",
                      self.stdout)
        self.assertNotIn("are read only to refuse terms", self.stdout)

    def test_the_mines_are_sites_inside_this_process(self) -> None:
        lines = self.steps["d"]["blocks"][0]["lines"]
        self.assertEqual(lines[1], "Each mine runs as its own site inside this process, built from c1's export on "
                                   "this machine, and nothing is sent over a network. What left a mine is what "
                                   "crossed from its site's store to HQ's, as HQ's receive log holds it.")
        self.assertTrue(lines[2].startswith("Each mine's cells carry its label (m01 to m05), never its id."))
        self.assertNotIn("Each mine sends its counts", self.stdout)

    def test_console_json_and_html_hold_the_same_lines(self) -> None:
        console = self.stdout.split("=== onboard-demo")[0]
        for step in self.doc["steps"]:
            for block in step["blocks"]:
                for line in block.get("lines", []) + block.get("items", []):
                    self.assertIn(line, console)
                    self.assertIn(__import__("html").escape(line), self.html_text)


class ReviewListTests(DemoRun):
    """The review list in two parts: an item R_mf alone raised is never shown as coming from the weekly counts."""

    def test_this_file_has_items_of_both_kinds(self) -> None:
        review = self.steps["d"]["data"]["review"]
        self.assertTrue(review["from_cells"])
        self.assertTrue(review["reference_only"])
        for item in review["reference_only"]:
            self.assertEqual(item["channels"], ["R_mf"])

    def test_a_reference_item_is_only_in_the_reference_list(self) -> None:
        review = self.steps["d"]["data"]["review"]
        counted = self.block("d", "The review list, from the weekly cells (X or S)")
        reference = self.block("d", "Reference only, raised by R_mf alone")
        counted_categories = {i["category"] for i in review["from_cells"]}
        for item in review["reference_only"]:
            if item["category"] in counted_categories:
                continue                                # the same label on both lists is a different pattern key
            self.assertFalse(any(line.startswith(item["category"] + ":") for line in counted["items"]))
            self.assertTrue(any(line.startswith(item["category"] + ":") for line in reference["items"]))
            self.assertNotIn(item["category"], self.steps["e"]["blocks"][0]["lines"][0])
        self.assertEqual(len(counted["items"]), len(review["from_cells"]))
        self.assertEqual(len(reference["items"]), len(review["reference_only"]))

    def test_step_e_counts_each_part(self) -> None:
        review = self.steps["d"]["data"]["review"]
        line = self.steps["e"]["blocks"][0]["lines"][0]
        self.assertTrue(line.startswith(f"Items from the weekly cells: {len(review['from_cells'])}. Each is a "
                                        "category whose counts rose at 2 or more of c1's mines in the same weeks"))
        self.assertIn(f"Reference only, from R_mf: {len(review['reference_only'])}. Those need record-level codes",
                      line)

    def test_the_alert_lines_lead_with_x_and_s(self) -> None:
        d = self.steps["d"]["data"]
        lines = [b for b in self.steps["d"]["blocks"] if b["kind"] == "text"][1]["lines"]
        self.assertTrue(lines[0].startswith(f"Alerts from the weekly cells alone: X {d['alerts']['X']}, "
                                            f"S {d['alerts']['S']}."))
        self.assertNotIn("R_mf", lines[0])
        self.assertTrue(lines[1].startswith("Reference channels, which count record-level codes centrally and are "
                                            f"not what left the mines: R_mf {d['alerts']['R_mf']}"))

    def test_blocks_never_put_an_r_mf_item_with_the_counts(self) -> None:
        data = {"k": 3, "window_weeks": 8, "min_sites": 2, "records": 10, "mines": 2, "weeks_evaluated": 1,
                "evaluated_from": "2023-W01", "evaluated_to": "2023-W02", "same_under_own_ids": True,
                "own_ids": {"records": 10, "sites": 2, "weeks": 1, "review_list": 2},
                "per_mine": [{"mine": "m01", "weekly_bundles": 1, "cells": 1, "suppressed_cells": 0},
                             {"mine": "m02", "weekly_bundles": 1, "cells": 1, "suppressed_cells": 1}],
                "sent": {"bytes": 1, "bundles": 2}, "outcomes": 0,
                "alerts": {"X": 1, "S": 0, "R_mf": 1, "P": 0, "PRR": 0},
                "review": {"total": 2,
                           "from_cells": [{"category": "COUNTED PATTERN", "channels": ["X"], "also": [],
                                            "first_week": "2023-W02", "last_week": "2023-W02", "alerts": 1,
                                            "mines": 2}],
                           "reference_only": [{"category": "RECORD LEVEL PATTERN", "channels": ["R_mf"],
                                               "first_week": "2023-W02", "last_week": "2023-W02", "mines": 2}]}}
        blocks = RD.blocks_audit(data, {"status": "not_recorded"}, "c1", 2)
        means = RD.blocks_means(data, "c1")[0]["lines"][0]
        counted = next(b for b in blocks if (b.get("title") or "").startswith("The review list, from the weekly"))
        reference = next(b for b in blocks if (b.get("title") or "").startswith("Reference only"))
        self.assertEqual(counted["items"], ["COUNTED PATTERN: X alerts in week 2023-W02; 2 mines sent a cell of it "
                                            "in the 8 weeks up to one of them"])
        self.assertEqual(reference["items"], ["RECORD LEVEL PATTERN: R_mf alerts in week 2023-W02; 2 mines with "
                                              "records of it in the 8 weeks up to one of them"])
        self.assertNotIn("RECORD LEVEL PATTERN", means)
        self.assertIn("Items from the weekly cells: 1.", means)
        self.assertIn("Reference only, from R_mf: 1.", means)

    def audit_data(self, **changes: Any) -> dict[str, Any]:
        data = {"k": 3, "window_weeks": 8, "min_sites": 2, "records": 10, "mines": 2, "weeks_evaluated": 1,
                "evaluated_from": "2023-W01", "evaluated_to": "2023-W02", "same_under_own_ids": True,
                "own_ids": {"records": 10, "sites": 2, "weeks": 1, "review_list": 0},
                "per_mine": [{"mine": "m01", "weekly_bundles": 1, "cells": 1, "suppressed_cells": 0}],
                "sent": {"bytes": 1, "bundles": 1}, "outcomes": 0, "alerts": {"X": 0, "S": 0},
                "review": {"total": 0, "from_cells": [], "reference_only": []}}
        data.update(changes)
        return data

    def test_the_own_ids_run_is_shown_when_it_differs(self) -> None:
        rec = RD.d002_record()
        own = {"records": 1033, "sites": 10, "weeks": 139, "review_list": 5}
        cmp = RD.compare_audit("c1", own, rec, True)
        self.assertEqual(cmp["status"], "differs")
        data = self.audit_data(same_under_own_ids=False, own_ids=own)
        lines = RD.blocks_audit(data, cmp, "c1", 5)[0]["lines"]
        # the figures D002's are compared with are on screen, beside D002's
        self.assertIn("The same audit under the mines' own ids, as D002's M4 ran it, gives a different result: 1,033 "
                      "records, 10 mines, 139 weeks, a review list of 5. D002's figures are compared with that run.",
                      lines[2])
        self.assertIn("D002's run 38024536763 recorded 1,033 records, 10 mines, 139 weeks, a review list of 4 for c1: "
                      "these differ.", lines[3])
        same = RD.blocks_audit(self.audit_data(), {"status": "not_recorded"}, "c1", 5)[0]["lines"]
        self.assertTrue(same[2].endswith("gives the same alerts, review list and counts."))

    def test_the_mines_of_a_counted_item_come_from_hqs_cells(self) -> None:
        doc = {"review": [{"key": "k:1:a", "predicate": "a", "channels": ["X"], "alert_weeks": ["2023-W01"],
                           "sites": ["m01", "m02"]}]}
        alerts = {"X": [{"key": "k:1:a", "week": "2023-W01", "sites": ["m01", "m02"]}], "S": []}
        asked = []

        def cells(name: str, a: Any) -> set[str]:
            asked.append((name, a["key"], a["week"]))
            return {"m01", "m02"}

        counted, _ = RD.review_parts(doc, alerts, str, cells)
        self.assertEqual(counted[0]["mines"], 2)
        self.assertEqual(asked, [("X", "k:1:a", "2023-W01")])
        # a mine the audit counts from a site's own store but no cell at HQ carries: nothing is shown
        with self.assertRaises(RD.DemoError):
            RD.review_parts(doc, alerts, str, lambda name, a: {"m01"})
        with self.assertRaises(RD.DemoError):
            RD.review_parts(doc, alerts, str, lambda name, a: {"m01", "m02", "m03"})

    def test_review_parts_splits_by_channel(self) -> None:
        doc = {"review": [
            {"key": "k:1:a", "predicate": "a", "channels": ["X", "R_mf"], "alert_weeks": ["2023-W01", "2023-W09"],
             "sites": ["m01", "m02", "m03"]},
            {"key": "k:1:b", "predicate": "b", "channels": ["R_mf"], "alert_weeks": ["2023-W05"],
             "sites": ["m01", "m02"]}]}
        alerts = {"X": [{"key": "k:1:a", "week": "2023-W01", "sites": ["m01", "m02"]}], "S": []}
        sent = {("X", "k:1:a", "2023-W01"): {"m01", "m02"}}
        cells = lambda name, a: set(sent.get((name, a["key"], a["week"]), ()))  # noqa: E731
        counted, reference = RD.review_parts(doc, alerts, lambda p: p.upper(), cells)
        # the counted item keeps only its X alert's week and mines, and says R_mf also raised it
        self.assertEqual(counted, [{"category": "A", "channels": ["X"], "also": ["R_mf"], "first_week": "2023-W01",
                                    "last_week": "2023-W01", "alerts": 1, "mines": 2}])
        self.assertEqual(reference, [{"category": "B", "channels": ["R_mf"], "first_week": "2023-W05",
                                      "last_week": "2023-W05", "mines": 2}])
        with self.assertRaises(RD.DemoError):           # an X alert with no review item: the parts are not trusted
            RD.review_parts(doc, {"X": [*alerts["X"], {"key": "k:1:z", "week": "2023-W02", "sites": []}], "S": []},
                            str, cells)


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

    def test_the_guard_scanned_what_left_the_mines(self) -> None:
        self.assertEqual(self.doc["guard"]["sent"], {"sent_refused_value": 0, "sent_report_record_id": 0,
                                                     "sent_report_site": 0})
        self.assertEqual(self.doc["guard"]["unread"], 0)


class WhatLeftTheMinesTests(DemoRun):
    """Every bundle and cell that leaves a mine carries the mine's label, never its id; the labelled audit's result
    is the same as D002's M4 path under the mines' own ids."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.calls: list[dict[str, Any]] = []
        original = A.run_pipeline

        def recording(*args: Any, **kwargs: Any) -> Any:
            pipeline = original(*args, **kwargs)
            bundles, cells = pipeline.store.detection_inputs(pipeline.as_of, "X")
            log = (Path(kwargs["workdir"]) / "hq" / "receive.jsonl").read_text()
            cls.calls.append({"site_ids": list(kwargs["site_ids"]), "bundle_sites": {b.site for b in bundles},
                              "cell_sites": {c.site for c in cells}, "log": log})
            return pipeline

        with mock.patch.object(A, "run_pipeline", recording), contextlib.redirect_stdout(io.StringIO()):
            cls.demo_doc = RD.run(cls.tmp / "raw", "c1", cls.settings, cls.tmp / "work-sites", lambda line: None)[0]

    def test_the_labelled_audit_sends_labels_only(self) -> None:
        self.assertEqual(len(self.calls), 2)              # the labelled audit, then D002's path under the own ids
        labelled, own = self.calls
        labels = {"m01", "m02", "m03", "m04", "m05"}
        self.assertEqual(set(labelled["site_ids"]), labels)
        self.assertEqual(labelled["bundle_sites"], labels)
        self.assertEqual(labelled["cell_sites"], labels)
        self.assertEqual(token_hits(labelled["log"], sorted(self.writer.ids["mines"] | self.writer.ids["documents"])),
                         [])
        self.assertEqual(ngram_hits([labelled["log"]], self.writer.narratives, 8), set())
        # the comparison run is D002's M4 path, under the mines' own ids; the table does not describe it
        self.assertTrue(set(own["site_ids"]) <= set(self.writer.ids["mines"]))
        self.assertEqual(len(own["site_ids"]), 5)

    def test_the_labels_follow_the_order_of_the_ids(self) -> None:
        self.assertEqual(RD.mine_labels(["4612003", "4612001", "4612002", "4612001"]),
                         {"4612001": "m01", "4612002": "m02", "4612003": "m03"})
        self.assertEqual(RD.mine_labels(str(1000 + i) for i in range(100))["1099"], "m100")

    def test_the_table_title_no_longer_says_no_mine_id_leaves_by_assertion_alone(self) -> None:
        title = self.block("d", "What left each mine").get("title")
        self.assertIn("under its label", title)
        self.assertIn("bytes that left were scanned for the record ids, mine ids and refused values of the 2 "
                      "operators in the split, as D002's guard reads them: none found.", title)

    def test_the_cells_are_as_the_table_title_says(self) -> None:
        title = self.block("d", "What left each mine").get("title")
        self.assertIn("weekly cells only, under its label, one per category, week and channel that had a record. A "
                      "cell holds counts (a count under 3 leaves as '<k', with k = 3 in its bundle) and, with 3 or "
                      "more records, the lowest match confidence, one of the pack's fixed levels.", title)
        self.assertNotIn("'<3'", self.stdout)
        self.assertNotIn("weekly counts only", self.stdout)
        records = [json.loads(line) for line in self.calls[0]["log"].splitlines()]
        self.assertTrue(records)
        counts = ("n", "n_roots", "n_reporters")
        for r in records:
            self.assertEqual(r["artifact_type"], "cells_bundle")
            self.assertEqual(r["body"]["k"], 3)
            for cell in r["body"]["cells"]:
                self.assertLessEqual(set(cell), {"entity_type", "entity_id", "predicate", "iso_week", "channel",
                                                 *counts, "res_conf_min"})
                for f in counts:
                    self.assertTrue(cell[f] == "<k" or (isinstance(cell[f], int) and cell[f] >= 3), cell[f])
                self.assertEqual("res_conf_min" in cell, isinstance(cell["n"], int))

    def test_the_mines_of_an_item_must_match_hqs_cells(self) -> None:
        original = RD.Watch.cell_sites

        def extra_mine(watch: Any, run_channel: str, alert: Any) -> set[str]:
            return original(watch, run_channel, alert) | {"m99"}

        err = io.StringIO()
        with mock.patch.object(RD.Watch, "cell_sites", extra_mine), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            code = RD.main(["--raw", str(self.tmp / "raw"), "--out", str(Path(tempfile.mkdtemp(dir=self.tmp))),
                            "--settings", str(self.settings)])
        self.assertEqual(code, 2)
        self.assertIn("the mines behind an X or S alert differ between HQ's cells and the audit", err.getvalue())

    def test_a_mine_id_in_what_left_withholds_everything(self) -> None:
        mine = sorted(self.writer.ids["mines"])[0]
        original = RD.Watch.active

        @contextlib.contextmanager
        def leaky(watch: Any) -> Any:
            with original(watch):
                yield watch
            watch.sent = (watch.sent or "") + f'{{"site": "{mine}"}}\n'

        out = Path(tempfile.mkdtemp(dir=self.tmp))
        with mock.patch.object(RD.Watch, "active", leaky), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = RD.main(["--raw", str(self.tmp / "raw"), "--out", str(out), "--settings", str(self.settings)])
        doc = json.loads((out / "demo.json").read_text())
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "withheld")
        self.assertEqual(doc["guard"]["sent_report_site"], 1)


# --------------------------------------------------------------------------------------------------- names

NAMED = {"person": "Okonkwo Vasquez", "place": "Beckleyville", "other_word": "Brackenridge", "own_word": "Rox"}
NAMED_OPERATOR = dict(OPERATOR, controller_name="Rox Quillfeather Holdings")


def named_extra(cat: str, k: int) -> str:
    """Names that recur in many narratives of one category at every mine, mid-sentence: a person, a place, a word of
    another operator's controller name and a three-letter word of this operator's own."""
    if k >= 45:
        return ""
    return {"POWERED HAULAGE": f"It was reported to {NAMED['person']} at once.",
            "HANDLING OF MATERIALS": f"The crew drove in from {NAMED['place']} that day.",
            "FALL OF ROOF OR BACK": f"A {NAMED['other_word']} hauler stood by.",
            "SLIP OR FALL OF PERSON": f"It happened on the {NAMED['own_word']} walkway."}.get(cat, "")


HOMOGRAPH = "Mill Creek"            # a place whose first word is also a common word the narratives write in lower case


def written_extra(cat: str, k: int) -> str:
    """The same names written the ways the case check used to miss: the person in capitals inside mixed-case text,
    the place only in narratives written all in capitals (:func:`written_upper`), the other operator's word and the
    operator's own three-letter word in lower case, and a place whose first word the narratives also write in lower
    case as a common word (8 of the uses), most often written as a name."""
    if cat == "MACHINERY":
        if k < 45:
            return f"The pump at {HOMOGRAPH} was down."
        return "The mill was shut." if k < 53 else ""
    if k >= 45:
        return ""
    return {"POWERED HAULAGE": f"It was reported to {NAMED['person'].upper()} at once.",
            "HANDLING OF MATERIALS": f"The crew drove in from {NAMED['place']} that day.",
            "FALL OF ROOF OR BACK": f"A {NAMED['other_word'].lower()} hauler stood by.",
            "SLIP OR FALL OF PERSON": f"It happened on the {NAMED['own_word'].lower()} walkway."}.get(cat, "")


def written_upper(cat: str, k: int) -> bool:
    return cat == "HANDLING OF MATERIALS" and k < 45


class NameShapedWordsTests(unittest.TestCase):
    def test_capitalised_away_from_a_sentence_start(self) -> None:
        names, caps = RD.name_shaped_words(["The crew met Okonkwo near the belt.", "Then okonkwo left.",
                                            "Rock fell. The rock was loose. A rock hit Vasquez.",
                                            "It was Vasquez again.", "EE SLIPPED ON ICE NEAR VASQUEZ."])
        self.assertIn("vasquez", names)
        self.assertNotIn("rock", names)                  # capitalised only at a sentence start, lower case elsewhere
        self.assertIn("okonkwo", names)                  # written like a name in one of its two uses: half
        self.assertNotIn("near", names)                  # in lower case in the mixed-case narrative
        self.assertIn("slipped", names)                  # only in a narrative written all in capitals: never lower
        self.assertEqual(caps, 1)

    def test_a_word_seen_only_at_sentence_starts(self) -> None:
        names, _ = RD.name_shaped_words(["Okonkwo slipped. Okonkwo fell.", "the crew. Crew left."])
        self.assertIn("okonkwo", names)
        self.assertNotIn("crew", names)

    def test_half(self) -> None:
        texts = ["It was Brackenridge 1.", "It was Brackenridge 2.", "It was brackenridge 3.", "by brackenridge."]
        self.assertIn("brackenridge", RD.name_shaped_words(texts)[0])           # two of four: at least half
        self.assertNotIn("brackenridge", RD.name_shaped_words(texts[1:])[0])     # one of three

    def test_capitals_inside_mixed_case_text(self) -> None:
        # the reviewer's case: a name in capitals in narratives that otherwise use lower case
        names, caps = RD.name_shaped_words(["It was reported to OKONKWO VASQUEZ at once.",
                                            "The crew told OKONKWO about it."])
        self.assertIn("okonkwo", names)
        self.assertIn("vasquez", names)
        self.assertNotIn("crew", names)
        self.assertEqual(caps, 0)

    def test_a_word_only_in_narratives_written_all_in_capitals(self) -> None:
        names, caps = RD.name_shaped_words(["THE CREW DROVE IN FROM BECKLEYVILLE.", "The crew left early."])
        self.assertIn("beckleyville", names)
        self.assertIn("drove", names)                    # no case to read: withheld too
        self.assertNotIn("crew", names)                  # in lower case elsewhere
        self.assertEqual(caps, 1)

    def test_a_name_that_is_also_a_common_word(self) -> None:
        texts = [f"The pump at Rose Hill was down {i}." for i in range(5)] + ["The water rose.", "It rose again."]
        names, _ = RD.name_shaped_words(texts)
        self.assertIn("rose", names)                     # written as a name in five of its seven uses
        self.assertIn("hill", names)
        self.assertNotIn("rose", RD.name_shaped_words(texts[:1] + texts[5:])[0])   # one of three: under half

    def test_a_name_written_in_lower_case_is_not_caught(self) -> None:
        # the limit the screen states: a name the narratives write in lower case reads as a plain word
        names, _ = RD.name_shaped_words(["it was reported to okonkwo.", "then okonkwo left."])
        self.assertNotIn("okonkwo", names)
        self.assertTrue(any("in lower case" in line and "is not caught" in line for line in RD.NOT_SHOWN))

    def test_a_term_is_withheld_when_any_of_its_words_is(self) -> None:
        names = frozenset({"okonkwo"})
        self.assertTrue(RD.name_shaped("supervisor okonkwo", names))
        self.assertFalse(RD.name_shaped("roof fall", names))

    def test_the_reason_a_term_is_withheld(self) -> None:
        names, refused = frozenset({"okonkwo"}), frozenset({"rox", "brackenridge"})
        self.assertEqual(RD.withheld_reason("rox walkway", names, refused), "refused_word")
        self.assertEqual(RD.withheld_reason("okonkwo brackenridge", names, refused), "refused_word")
        self.assertEqual(RD.withheld_reason("supervisor okonkwo", names, refused), "name_shaped")
        self.assertIsNone(RD.withheld_reason("roof fall", names, refused))


class RefusedWordsTests(unittest.TestCase):
    def test_every_word_of_a_refused_value_a_term_could_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            write_raw(t / "raw", accident_file(operator=NAMED_OPERATOR))
            settings = json.loads(SETTINGS_PATH.read_text())
            with contextlib.redirect_stdout(io.StringIO()):
                RD.load_fetch().split(SETTINGS_PATH, t / "raw", t / "split")
            exports = [read_export(t / "split" / f"{c}.txt") for c in ("c1", "c2")]
        _, words = RD.refused_index(exports, S.arm_roles(settings, "msha"), settings["params"])
        # a three-letter word of c1's own controller name, below D002's name-word minimum of four letters
        self.assertIn("rox", words)
        # the words of c2's names, which D002's refusal for c1 does not read
        for w in ("brackenridge", "aggregates", "hollow", "quarry"):
            self.assertIn(w, words)
        self.assertTrue(all(w.isalpha() and len(w) >= 3 for w in words))


class PlantedNamesRun:
    """One run of the demo on a file with names planted in tens of narratives (``extra`` and ``upper``, see
    :func:`accident_file`)."""

    extra: Any = None
    upper: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.writer = accident_file(seed=11, operator=NAMED_OPERATOR, extra=cls.extra, upper=cls.upper)
        write_raw(cls.tmp / "raw", cls.writer)
        cls.settings = small_settings(cls.tmp / "settings.json")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cls.code = RD.main(["--raw", str(cls.tmp / "raw"), "--out", str(cls.tmp / "out"), "--settings",
                                str(cls.settings), "--work", str(cls.tmp / "work")])
        cls.stdout, cls.stderr = out.getvalue(), err.getvalue()
        cls.doc = json.loads((cls.tmp / "out" / "demo.json").read_text())
        cls.outputs = {"stdout": cls.stdout, "stderr": cls.stderr,
                       "demo.json": (cls.tmp / "out" / "demo.json").read_text(),
                       "demo.html": (cls.tmp / "out" / "demo.html").read_text()}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def words(self) -> list[str]:
        return sorted({w.lower() for v in NAMED.values() for w in v.split()})

    def drafted_terms(self) -> list[str]:
        settings = json.loads(self.settings.read_text())
        work = Path(tempfile.mkdtemp(dir=self.tmp))
        with contextlib.redirect_stdout(io.StringIO()):
            export, _ = RD.step_export(self.settings, settings, self.tmp / "raw", work, "c1")
        b = RD.step_draft(settings, export, "c1", work)
        self.assertTrue(b["check"]["passed"])
        return " ".join(t for p in b["draft"].facts["predicates"] for t in p["first_terms"]).split()

    def test_the_names_clear_the_drafters_floor(self) -> None:
        terms = self.drafted_terms()
        for word in self.words():
            with self.subTest(word=word):
                self.assertIn(word, terms)

    def test_no_name_is_printed_or_written(self) -> None:
        self.assertEqual(self.code, 0, self.stderr)
        self.assertEqual(self.doc["status"], "shown")
        for name, text in self.outputs.items():
            with self.subTest(output=name):
                self.assertEqual(token_hits(text, self.words()), [])


class NamesInNarrativesTests(PlantedNamesRun, unittest.TestCase):
    """A person's name, a place, a word of another operator's controller name and a three-letter word of the
    operator's own controller name, each capitalised in tens of narratives at every mine: the drafter learns them as
    terms, and the demo prints none of them."""

    extra = staticmethod(named_extra)

    def test_the_withheld_terms_are_counted(self) -> None:
        b = {s["id"]: s for s in self.doc["steps"]}["b"]["data"]
        self.assertGreaterEqual(b["names"]["withheld_terms"], 4)
        self.assertEqual(b["names"]["withheld_terms"], sum(p["withheld_terms"] for p in b["predicates"]))
        self.assertEqual(b["names"]["withheld_terms"], b["names"]["refused_word"] + b["names"]["name_shaped"])
        self.assertIn(f"Terms withheld from the screen: {b['names']['withheld_terms']}. "
                      f"{b['names']['refused_word']} hold a word of a refused value (a name or an id) of an operator "
                      f"in the split. {b['names']['name_shaped']} hold a word c1's narratives write like a name",
                      self.stdout)
        self.assertIn("more withheld from the screen)", self.stdout)


class NamesWrittenOtherwiseTests(PlantedNamesRun, unittest.TestCase):
    """The same names written the ways the case check used to miss (:func:`written_extra`): in capitals inside
    mixed-case text, only in narratives written all in capitals, in lower case when they are words of an operator's
    names, and a place whose first word is also a common word. The drafter learns them all; the demo prints none."""

    extra = staticmethod(written_extra)
    upper = staticmethod(written_upper)

    def words(self) -> list[str]:
        return sorted({*super().words(), *HOMOGRAPH.lower().split()})

    def test_the_planted_texts_are_written_as_described(self) -> None:
        narratives = self.writer.narratives
        self.assertTrue(any("OKONKWO VASQUEZ" in t and any(ch.islower() for ch in t) for t in narratives))
        beckley = [t for t in narratives if "BECKLEYVILLE" in t.upper()]
        self.assertGreaterEqual(len(beckley), 20)
        self.assertTrue(all(not any(ch.islower() for ch in t) for t in beckley))
        self.assertTrue(any(" brackenridge hauler" in t for t in narratives))
        self.assertTrue(any(" rox walkway" in t for t in narratives))
        common = sum(1 for t in narratives if " mill was shut" in t)
        self.assertTrue(0 < common < sum(1 for t in narratives if HOMOGRAPH in t))

    def test_the_reasons_are_counted(self) -> None:
        b = {s["id"]: s for s in self.doc["steps"]}["b"]["data"]
        self.assertGreaterEqual(b["names"]["refused_word"], 2)         # brackenridge and rox, in lower case
        self.assertGreaterEqual(b["names"]["name_shaped"], 4)          # okonkwo, vasquez, beckleyville, mill
        self.assertGreaterEqual(b["names"]["narratives_in_capitals"], 20)
        self.assertIn(f"Narratives written all in capitals, whose case is not read: "
                      f"{b['names']['narratives_in_capitals']}.", self.stdout)


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
        self.assertTrue(d["same_under_own_ids"])
        self.assertEqual((d["records"], d["mines"], d["weeks_evaluated"], d["review"]["total"]),
                         (summary["records"], summary["sites"], summary["weeks_evaluated"], summary["review_list"]))
        self.assertEqual(d["alerts"], summary["alerts"])
        # the reference part is exactly the audit's items with R_mf alone, in its order
        self.assertEqual([(i["mines"], i["channels"], i["first_week"], i["last_week"])
                          for i in d["review"]["reference_only"]],
                         [(len(e["sites"]), e["channels"], min(e["alert_weeks"]), max(e["alert_weeks"]))
                          for e in audit["review"] if e["channels"] == ["R_mf"]])
        # the counted part: the audit's other items, in its order; an item with no R_mf alert is the audit's as is,
        # and one R_mf also raised keeps only its X and S alerts' weeks and mines
        others = [e for e in audit["review"] if e["channels"] != ["R_mf"]]
        self.assertEqual(len(d["review"]["from_cells"]), len(others))
        for item, e in zip(d["review"]["from_cells"], others):
            self.assertEqual(item["channels"] + item["also"], e["channels"])
            self.assertLessEqual(item["mines"], len(e["sites"]))
            self.assertTrue(min(e["alert_weeks"]) <= item["first_week"] <= item["last_week"]
                            <= max(e["alert_weeks"]))
            if not item["also"]:
                self.assertEqual((item["mines"], item["first_week"], item["last_week"]),
                                 (len(e["sites"]), min(e["alert_weeks"]), max(e["alert_weeks"])))
        self.assertEqual(sum(i["alerts"] for i in d["review"]["from_cells"]), d["alerts"]["X"] + d["alerts"]["S"])

    def test_the_watch_puts_every_function_back(self) -> None:
        d = {s["id"]: s for s in self.demo_doc["steps"]}["d"]["data"]
        self.assertEqual(d["cells"], sum(m["cells"] for m in d["per_mine"]))
        self.assertTrue(all(m["weekly_bundles"] > 0 for m in d["per_mine"]))
        baselines = __import__("mycelic.collective.evaluate.baselines", fromlist=["run_pipeline"])
        self.assertIs(A.run_pipeline, baselines.run_pipeline)
        self.assertIs(A.hq_results, baselines.hq_results)
        self.assertEqual(A._alerts.__module__, A.__name__)
        self.assertEqual(A._alerts.__qualname__, "_alerts")


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

    def test_a_difference_says_whether_the_file_or_the_code_changed(self) -> None:
        rec = RD.d002_record()
        lines = {s: RD.reproduce_line(RD.compare_reading("c1", self.fake(0.571, 0.36), rec, True, s), "c1")
                 for s in ("same", "revised", "unknown")}
        self.assertIn("the input is the file D002 read (the same size and sha256 prefix), so the code differs",
                      lines["same"])
        self.assertIn("the input is not the file D002 read: MSHA has revised the file since D002's run",
                      lines["revised"])
        self.assertIn("so the file and the code cannot be told apart as the cause", lines["unknown"])

    def test_the_recorded_input(self) -> None:
        recorded = RD.d002_input()
        self.assertEqual({k: recorded[k] for k in ("file", "bytes", "sha256_prefix", "run")},
                         {"file": "Accidents.zip", "bytes": 52269752, "sha256_prefix": "62d0c861a5c3",
                          "run": "38024536763"})
        # the same download as D001's committed record of that day
        self.assertIn("| `Accidents.zip` | 52,269,752 | `62d0c861a5c3…` |",
                      (ROOT / "docs" / "collective" / "onboard" / "CHOICE-D001.md").read_text())
        same = {"file": "Accidents.zip", "bytes": 52269752, "sha256": "62d0c861a5c3" + "0" * 52}
        self.assertEqual(RD.compare_input(same, recorded), "same")
        self.assertEqual(RD.compare_input(dict(same, bytes=52269753), recorded), "revised")
        self.assertEqual(RD.compare_input(dict(same, sha256="f" * 64), recorded), "revised")
        self.assertEqual(RD.compare_input(same, None), "unknown")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(RD.d002_input(Path(tmp) / "missing.json"))
            (Path(tmp) / "bad.json").write_text('{"file": "Accidents.zip", "bytes": "many"}')
            self.assertIsNone(RD.d002_input(Path(tmp) / "bad.json"))
        self.assertIn("the file D002 read", RD.input_line(same, "same", recorded))
        self.assertIn("MSHA has revised the file since D002's run",
                      RD.input_line(dict(same, bytes=1), "revised", recorded))

    def test_three_places_as_d002_printed_them(self) -> None:
        # D002's report rounds to four places in the file, then shows three: 0.56749 became 0.5675, shown 0.568
        self.assertEqual(RD.f3(0.56749), "0.568")
        self.assertEqual(f"{0.56749:.3f}", "0.567")
        self.assertEqual(RD.f3(0.56744), "0.567")
        self.assertEqual(RD.f3(None), "n/a")

    def test_the_audit_comparison(self) -> None:
        rec = RD.d002_record()
        own = {"records": 1033, "sites": 10, "weeks": 139, "review_list": 4}
        self.assertEqual(RD.compare_audit("c1", own, rec, True)["status"], "reproduced")
        self.assertEqual(RD.compare_audit("c1", dict(own, sites=9), rec, True)["status"], "differs")
        self.assertEqual(RD.compare_audit("c1", own, rec, False)["status"], "not_comparable")
        self.assertEqual(RD.compare_audit("c2", own, rec, True)["status"], "not_recorded")


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

    def guard(self) -> Any:
        export = read_export(self.tmp / "work" / "split" / "c1.txt")
        return RD.Guard(json.loads(self.settings.read_text()), export, ())

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

    def test_an_unreadable_export_of_the_split_withholds_everything(self) -> None:
        original = RD.read_export

        def failing(path: Any) -> Any:
            if Path(path).name == "c2.txt":
                raise ExportError("cannot read it")
            return original(path)

        code, stdout, _, out = self.run_with(mock.patch.object(RD, "read_export", failing))
        self.assertEqual(code, 1)
        doc = json.loads((out / "demo.json").read_text())
        self.assertEqual(doc["status"], "withheld")
        self.assertEqual(doc["guard"]["unread"], 1)
        self.assertNotIn("FALL OF ROOF", stdout)

    def test_an_error_text_naming_a_mine_is_withheld(self) -> None:
        guard = self.guard()
        mine = sorted(self.writer.ids["mines"])[0]
        self.assertNotIn(mine, guard.message(f"site {mine} rejected or duplicated records"))
        self.assertEqual(guard.message("no category reaches the predicate minimum"),
                         "no category reaches the predicate minimum")

    def test_an_error_text_with_narrative_words_names_or_name_words_is_withheld(self) -> None:
        guard = self.guard()
        narrative = next(t for t in self.writer.narratives if len(t.split()) >= 10)
        for text in (f"bad value: {narrative}",                          # eight words of a narrative
                     f"cannot map {NAME_WORD} to a predicate",             # a word of the controller's name
                     f"row from {OPERATOR['contractor_id']} rejected"):    # a refused value, whole
            with self.subTest(text=text[:30]):
                self.assertEqual(guard.message(text), RD.WITHHELD_ERROR)
        named = RD.Guard.__new__(RD.Guard)
        named.__dict__.update(guard.__dict__, names=frozenset({"okonkwo"}))
        self.assertEqual(named.message("a row of Okonkwo was rejected"), RD.WITHHELD_ERROR)

    def test_the_loader_error_is_trimmed_as_d002_trims_it(self) -> None:
        original = RD.step_draft

        def unloadable(*args: Any) -> dict[str, Any]:
            b = original(*args)
            return dict(b, pack=None, loader_error="lexicon.json: entry 'roof fall at the far heading' is bad")

        code, _, stderr, _ = self.run_with(mock.patch.object(RD, "step_draft", unloadable))
        self.assertEqual(code, 2)
        self.assertIn("error: DraftError: the drafted pack does not load (lexicon.json)", stderr)
        self.assertNotIn("far heading", stderr)

    def test_a_step_a_error_is_withheld_when_no_export_can_check_it(self) -> None:
        mine = sorted(self.writer.ids["mines"])[0]
        fetch = RD.load_fetch()

        class Broken:
            FILES = fetch.FILES

            @staticmethod
            def split(*args: Any) -> Any:
                raise ValueError(f"a bad row at mine {mine}")

        code, _, stderr, _ = self.run_with(mock.patch.object(RD, "load_fetch", lambda: Broken))
        self.assertEqual(code, 2)
        self.assertIn("error: ValueError: (its text is withheld: no export could be read to check it)", stderr)
        self.assertNotIn(mine, stderr)

    def test_an_unexpected_error_prints_its_class_only(self) -> None:
        mine = sorted(self.writer.ids["mines"])[0]

        def boom(*args: Any) -> Any:
            raise RuntimeError(f"mine {mine}")

        code, stdout, stderr, out = self.run_with(mock.patch.object(RD, "run", boom))
        self.assertEqual(code, 2)
        self.assertEqual(stderr.strip(), "error: RuntimeError (its text is not shown)")
        self.assertNotIn("Traceback", stderr)
        self.assertFalse(out.exists())


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

    def test_the_newest_request_file_is_taken_in_version_order(self) -> None:
        line = next(line for line in self.text.splitlines() if "REQUEST=$(" in line)
        self.assertIn("sort -V", line)
        self.assertNotRegex(line, r"\| sort \|")
        sorter = __import__("shutil").which("sort")
        if sorter is None:
            self.skipTest("no sort on this machine")
        names = "demo/onboard/record-9.json\ndemo/onboard/record-10.json\ndemo/onboard/record-2.json\n"
        r = __import__("subprocess").run([sorter, "-V"], input=names, capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.splitlines()[-1], "demo/onboard/record-10.json")

    def test_the_company_check_refuses_anything_but_a_label(self) -> None:
        self.assertIn("^c[1-9][0-9]?$", self.text)

    def test_no_record_file_is_committed(self) -> None:
        self.assertEqual(sorted(DEMO_DIR.glob("record-*.json")), [])

    def test_the_regular_ci_runs_these_tests(self) -> None:
        text = (ROOT / ".github" / "workflows" / "mycelic.yml").read_text()
        self.assertIn("python -m pytest tests/onboard -q -p no:warnings", text)
        for path in ('"demo/onboard/**"', '"tools/onboard/**"'):
            self.assertIn(path, text.split("pull_request")[0])


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
        self.assertIn("## The D002 figures it compares with", text)
        self.assertNotIn("figures it reproduces", text)

    def test_the_talk_track_quotes_no_count_of_its_own(self) -> None:
        text = (DEMO_DIR / "SCRIPT.md").read_text()
        # beyond its time marks (0:00) and the test's name (D002) it may name only: the operator label c1, its length
        # (2 minutes) and the suppression threshold the output prints (a count under 3)
        rest = re.sub(r"\b\d:\d{2}\b|\bD002\b", "", text)
        numbers = set(re.findall(r"\d[\d,.]*\d|\d", rest))
        self.assertEqual(numbers, {"1", "2", "3"})
        self.assertIn("'<k'", text)
        self.assertNotIn("'<3'", text)
        self.assertEqual(len(re.findall(r"^\| \d:\d{2}–\d:\d{2} \|", text, re.M)), 6)
        self.assertIn("| 1:50–2:00 |", text)

    def test_the_talk_track_claims_only_what_the_guard_checks(self) -> None:
        text = (DEMO_DIR / "SCRIPT.md").read_text()
        self.assertNotIn("no name, id or narrative is on screen", text)
        self.assertNotIn("They are never shown", text)
        self.assertNotIn("read only to refuse words", text)
        self.assertIn("no id or narrative is on screen", text)
        self.assertIn("Do not say that no name is on screen", text)
        beat = next(line for line in text.splitlines() if line.startswith("| 0:15–0:30 |"))
        self.assertIn("read only to split the file, to refuse and hold back words, and to check the output", beat)

    def test_the_talk_track_says_what_the_screen_shows_of_the_pack_and_the_mines(self) -> None:
        text = (DEMO_DIR / "SCRIPT.md").read_text()
        b = next(line for line in text.splitlines() if line.startswith("| 0:30–1:00 |"))
        self.assertIn("Each filed category with enough records at enough mines, and a specific label, becomes a "
                      "predicate.", b)
        self.assertIn("Every word passed the term floor, and the privacy check passed.", b)
        self.assertNotIn("Every word passed a floor, shown here", b)
        d = next(line for line in text.splitlines() if line.startswith("| 1:25–1:50 |"))
        self.assertIn("In the pipeline, each mine runs as its own site and keeps its records; here they all run in "
                      "this one process.", d)
        self.assertIn("a count under 3 leaves as '<k'", d)
        self.assertNotIn("Each mine keeps its records. Only weekly counts leave", d)

    def test_the_readme_names_its_sources_and_the_terms_the_guard_reads(self) -> None:
        text = " ".join((DEMO_DIR / "README.md").read_text().split())
        self.assertIn("The figures D002 recorded are read from its choice file and from `d002-input.json`", text)
        self.assertIn("every first term of each predicate, shown or withheld: the only terms the screen can print",
                      text)
        self.assertNotIn("every drafted term, shown or not", text)
        self.assertIn("leaves as the string `'<k'`, and each bundle carries k (3)", text)
        self.assertNotIn("`'<3'`", text)
        self.assertIn("Each mine runs as its own site inside this one process", text)
        self.assertIn("so the demo does not claim that no name is on screen", text)
        self.assertNotIn("nine of ten", text)

    def test_the_talk_track_says_two_commands_and_reads_only_the_counted_items(self) -> None:
        text = (DEMO_DIR / "SCRIPT.md").read_text()
        self.assertNotIn("One command points the drafter", text)
        self.assertIn("Two commands: one downloads the file, one runs the drafter on it", text)
        beat = next(line for line in text.splitlines() if line.startswith("| 1:25–1:50 |"))
        self.assertNotIn("Read the review list", beat)
        self.assertIn("reference only", beat)
        self.assertIn("record-level codes", beat)
        self.assertIn("From those cells alone", beat)


if __name__ == "__main__":
    unittest.main()

"""D002 stays reproducible (``docs/collective/onboard/CHOICE-D003.md``, section 11 and E18).

A fixed synthetic input in D002's two layouts (MSHA's quoted, pipe-delimited accident file and NHTSA's tab-delimited
complaint archive, every value invented) goes through the steps the D002 workflow runs after its downloads, with
``docs/collective/onboard/D002-settings.json``: both splits, both arms scored, the export and pilot audit of the
audit company, and the report. ``arm.json`` of both arms, ``report.json`` and ``report.md`` must equal the bytes
recorded from the code D002 ran (commit ``9070788``, whose onboard package, download code and pilot audit are those
of ``d428243`` and ``7f4766d``), except the values that name the code (``code_commit``, ``code_files``, ``files``,
``code_hash``), the report line that prints them, and the test's own working path (``exports_dir``).

The expected files under ``tests/onboard/data/d002_reproducible/`` were recorded before any onboard change, by::

    git worktree add <dir> 9070788
    python tests/onboard/test_d002_reproducible.py record --root <dir> --out tests/onboard/data/d002_reproducible

which runs this module's generator and steps against the code in ``<dir>`` and writes the normalised outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ROOT / "tests" / "onboard" / "data" / "d002_reproducible"
SETTINGS_REL = "docs/collective/onboard/D002-settings.json"
RECORDED_AT = "9070788"
OUTPUTS = ("msha.arm.json", "nhtsa.arm.json", "report.json", "report.md")
CODE_KEYS = ("code_commit", "code_files", "files", "code_hash")
PATH_KEYS = ("exports_dir",)
CODE_LINE = re.compile(r"^- Code commit `[^`]*`; code hash of the onboard package and the download code `[^`]*`;",
                       re.M)
CODE_LINE_NORMAL = "- Code commit `<code>`; code hash of the onboard package and the download code `<code>`;"

# --------------------------------------------------------------------------------------------------- the input

FILLER = ("employee", "shift", "crew", "morning", "reported", "nearby", "section", "worker", "tool", "area",
          "while", "during", "noticed", "returned", "afternoon", "later", "evening", "station")
MSHA_CUES = {"FALL OF ROOF OR BACK": ("roof", "rock", "bolter", "brow"),
             "HANDLING OF MATERIALS": ("lifting", "carrying", "pallet", "strained"),
             "SLIP OR FALL OF PERSON": ("slipped", "icy", "walkway", "stairs"),
             "NO VALUE FOUND": ("assorted", "various")}
CONTROLLERS = (  # id, name, operator id, operator name, mine base, training rows per specific category
    ("CTRL117", "Varrowmere Holdings Combine", "OPR5117", "Varrowmere Upland Pits", 11700, 60),
    ("CTRL204", "Quistlebay Mineral Trust", "OPR5204", "Quistlebay Lower Shafts", 20400, 56),
    ("CTRL339", "Hobbledene Stone Consortium", "OPR5339", "Hobbledene Gravel Yard", 33900, 52),
    ("CTRL450", "Fennickwold Quarry Union", "OPR5450", "Fennickwold Chalk Face", 45000, 58))
NHTSA_CUES = {"ENGINE": ("stalled", "misfire", "idling", "revving"),
              "AIR BAGS": ("deploy", "inflated", "airbag", "collision"),
              "SERVICE BRAKES": ("pedal", "stopping", "braking", "grinding"),
              "STEERING": ("steering", "veered", "pulling", "drifted")}
NHTSA_FILLER = ("driving", "highway", "speed", "noticed", "dealer", "repair", "morning", "traffic", "suddenly",
                "parking", "street", "warning", "mileage", "garage", "commute", "weekend")
MAKES = ("FORD", "CHEVROLET", "JEEP", "HONDA", "NISSAN", "DODGE")
STATES = ("TX", "CA", "OH", "FL", "NY")


def _text(rng: random.Random, cues: tuple[str, ...], filler: tuple[str, ...]) -> str:
    first = " ".join(rng.sample(cues, 2) + rng.sample(filler, 5))
    second = " ".join(rng.sample(filler, 4))
    return first.capitalize() + ". " + second.capitalize() + "."


def _day(rng: random.Random, first: date, last: date) -> date:
    return first + timedelta(days=rng.randrange((last - first).days + 1))


def msha_file(columns: list[str]) -> bytes:
    """The accident file as the date probe shows MSHA's: header bare, every value in double quotes, dates
    mm/dd/yyyy, CRLF line ends. Three controllers qualify (the fourth has too few test rows); each one's test rows
    are mostly copies of its training narratives, so that only a few test records are eligible."""
    rng = random.Random("d002-reproducible:msha")
    lines = ["|".join(columns)]
    n = 0

    def row(ctrl: tuple[Any, ...], mine: str, day: date, cat: str, text: str) -> str:
        nonlocal n
        n += 1
        values = dict.fromkeys(columns, "")
        values.update({"MINE_ID": mine, "CONTROLLER_ID": ctrl[0], "CONTROLLER_NAME": ctrl[1], "OPERATOR_ID": ctrl[2],
                       "OPERATOR_NAME": ctrl[3], "DOCUMENT_NO": f"DOC{n:07d}", "SUBUNIT": "UNDERGROUND",
                       "ACCIDENT_DT": f"{day.month:02d}/{day.day:02d}/{day.year}", "CAL_YR": str(day.year),
                       "DEGREE_INJURY": "DAYS AWAY FROM WORK ONLY", "FIPS_STATE_CD": "54",
                       "CLASSIFICATION": cat, "ACCIDENT_TYPE": "OTHER", "NARRATIVE": text,
                       "COAL_METAL_IND": "C"})
        return "|".join('"' + values[c].replace('"', '""') + '"' for c in columns)

    for k, ctrl in enumerate(CONTROLLERS):
        mines = [f"MN{ctrl[4] + i:05d}" for i in range(1, 5)]
        texts: list[tuple[str, str]] = []
        for cat, cues in MSHA_CUES.items():
            count = ctrl[5] if cat != "NO VALUE FOUND" else 15
            for i in range(count):
                text = _text(rng, cues, FILLER)
                if i % 9 == 4:
                    text = text[:-1] + ' and "quoted" words | with a pipe.'
                if k == 1 and cat == "FALL OF ROOF OR BACK" and i % 3 == 1:
                    text = text[:-1] + " near quistlebay."      # a word of a forbidden name: a refused term
                texts.append((cat, text))
                lines.append(row(ctrl, mines[i % 4], _day(rng, date(2015, 1, 1), date(2021, 12, 31)), cat, text))
        tests = 100 if k < 3 else 40
        for i in range(tests):
            if i < 18:
                cat = list(MSHA_CUES)[i % 3]
                text = _text(rng, MSHA_CUES[cat], FILLER)
            else:
                cat, text = texts[(i * 7) % len(texts)]
            lines.append(row(ctrl, mines[i % 4], _day(rng, date(2022, 1, 1), date(2024, 12, 31)), cat, text))
    lines.append('"too"|"few"|"fields"')
    return ("\r\n".join(lines) + "\r\n").encode("latin-1")


def nhtsa_file() -> bytes:
    """The complaint archive's one text file: tab-delimited, 51 fields, the positions ``nhtsa_export`` reads. Every
    make has four component categories in 2020-2022 and twelve new narratives in 2023-2024, beside copies of
    training narratives; some complaints name two components (two rows, one complaint)."""
    rng = random.Random("d002-reproducible:nhtsa")
    out = []
    odino = 11000000

    def row(make: str, comp: str, state: str, day: date, text: str, model: str, year: str, number: int) -> str:
        cells = [""] * 51
        cells[1], cells[3], cells[4], cells[5] = str(number), make, model, year
        cells[11], cells[13], cells[16], cells[19], cells[45] = comp, state, day.strftime("%Y%m%d"), text, "V"
        cells[50] = "Owner Never Exported"
        return "\t".join(cells)

    for make in MAKES:
        texts: list[tuple[str, str]] = []
        for cat, cues in NHTSA_CUES.items():
            for i in range(54):
                odino += 1
                text = _text(rng, cues, NHTSA_FILLER)
                if make == "FORD" and cat == "ENGINE" and i % 4 == 1:
                    text = text[:-1] + " on the modela."        # a word of a vehicle id: a refused term
                texts.append((cat, text))
                day = _day(rng, date(2020, 1, 1), date(2022, 12, 31))
                model, year = ("MODELA", "2019") if i % 2 else ("MODELB", "2020")
                out.append(row(make, f"{cat}:SUB PART", STATES[i % 5], day, text, model, year, odino))
                if i % 11 == 3:
                    other = list(NHTSA_CUES)[(list(NHTSA_CUES).index(cat) + 1) % 4]
                    out.append(row(make, other, STATES[i % 5], day, text, model, year, odino))
        for i in range(30):
            odino += 1
            if i < 12:
                cat = list(NHTSA_CUES)[i % 4]
                text = _text(rng, NHTSA_CUES[cat], NHTSA_FILLER)
            else:
                cat, text = texts[(i * 5) % len(texts)]
            day = _day(rng, date(2023, 1, 1), date(2024, 12, 31))
            out.append(row(make, cat, STATES[i % 5], day, text, "MODELA", "2019", odino))
    return ("\n".join(out) + "\n").encode("latin-1")


def _zip(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        z.writestr(info, data)
    return buf.getvalue()


def write_raw(work: Path, settings: dict[str, Any]) -> None:
    """The two downloads as the download step writes them, ``download.json`` included."""
    msha, nhtsa = work / "raw" / "msha", work / "raw" / "nhtsa"
    msha.mkdir(parents=True)
    nhtsa.mkdir(parents=True)
    files = {msha / "Accidents.zip": _zip("Accidents.txt", msha_file(settings["arms"]["msha"]["columns"])),
             msha / "Accidents_Definition_File.txt":
                 ("DOCUMENT_NO\tThe document number of the accident.\n\nMINE_ID\tThe mine.\n\n"
                  "ACCIDENT_DT\tThe accident date, mm/dd/yyyy.\n\nNARRATIVE\tWhat happened.\nIn words.\n\n"
                  "CLASSIFICATION\tThe circumstances.\n").encode("latin-1"),
             nhtsa / "COMPLAINTS_RECEIVED_2020-2024.zip": _zip("COMPLAINTS_RECEIVED_2020-2024.txt", nhtsa_file())}
    for path, data in files.items():
        path.write_bytes(data)
    for raw in (msha, nhtsa):
        facts = [{"file": p.name, "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                 for p, d in files.items() if p.parent == raw]
        (raw / "download.json").write_text(json.dumps(facts, indent=1, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------------------- the steps

def run_steps(root: Path, work: Path) -> dict[str, Any]:
    """The D002 workflow's steps after its downloads, run with ``root`` as the working directory (so the code under
    ``root`` runs) and ``work`` for every file. Returns the exit codes and the outputs' bytes."""
    settings = json.loads((root / SETTINGS_REL).read_text(encoding="utf-8"))
    write_raw(work, settings)
    py = [sys.executable, "-B"]
    codes: dict[str, int] = {}

    def step(name: str, *args: str) -> None:
        r = subprocess.run([*py, *args], cwd=root, capture_output=True, text=True)
        codes[name] = r.returncode

    step("split msha", "tools/onboard/fetch_msha.py", "split", "--settings", SETTINGS_REL, "--raw",
         str(work / "raw" / "msha"), "--out", str(work / "split" / "msha"))
    step("split nhtsa", "tools/onboard/fetch_nhtsa.py", "split", "--settings", SETTINGS_REL, "--raw",
         str(work / "raw" / "nhtsa"), "--out", str(work / "split" / "nhtsa"))
    for arm in ("msha", "nhtsa"):
        step(f"score {arm}", "-m", "mycelic.collective.onboard", "score", "--settings", SETTINGS_REL, "--arm", arm,
             "--exports", str(work / "split" / arm), "--out", str(work / "arms" / arm))
    a = settings["arms"]["msha"]
    company = a["audit_company"]
    listing = json.loads((work / "split" / "msha" / "companies.json").read_text(encoding="utf-8"))
    (work / "audit-in").mkdir(parents=True)
    step("export", "-m", "mycelic.collective.onboard", "export", "--export",
         str(work / "split" / "msha" / listing[company]["file"]), "--roles",
         str(work / "arms" / "msha" / company / "roles.json"), "--pack", str(work / "arms" / "msha" / company / "pack"),
         "--from", a["test"][0], "--to", a["test"][1], "--out", str(work / "audit-in" / "records.jsonl"))
    (work / "audit-in" / "outcomes.csv").write_text("outcome_id,opened,entity_type,entity_id,predicate\n")
    step("audit", "-m", "mycelic.collective.pilot.audit", "run", "--pack",
         str(work / "arms" / "msha" / company / "pack"), "--records", str(work / "audit-in" / "records.jsonl"),
         "--outcomes", str(work / "audit-in" / "outcomes.csv"), "--out", str(work / "audit"))
    step("report", "-m", "mycelic.collective.onboard", "report", "--settings", SETTINGS_REL, "--arms",
         str(work / "arms" / "msha"), str(work / "arms" / "nhtsa"), "--audit", str(work / "audit" / "audit.json"),
         "--out", str(work / "report"))
    files = {"msha.arm.json": work / "arms" / "msha" / "arm.json", "nhtsa.arm.json": work / "arms" / "nhtsa" / "arm.json",
             "report.json": work / "report" / "report.json", "report.md": work / "report" / "report.md"}
    return {"codes": codes, "outputs": {k: p.read_bytes() for k, p in files.items() if p.is_file()}}


def _blank(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<code>" if k in CODE_KEYS else "<path>" if k in PATH_KEYS else _blank(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_blank(v) for v in value]
    return value


def _dump(doc: Any) -> bytes:
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def normalise(name: str, data: bytes) -> bytes:
    """The output with the values that name the code and the working path blanked; nothing else changes. A JSON
    file is parsed and written again as the onboard code writes it, which must give its own bytes back."""
    if name.endswith(".json"):
        doc = json.loads(data)
        if _dump(doc) != data:
            raise AssertionError(f"{name} does not round-trip, so it cannot be normalised losslessly")
        return _dump(_blank(doc))
    text = data.decode("utf-8")
    if len(CODE_LINE.findall(text)) != 1:
        raise AssertionError("report.md must print the code line exactly once")
    return CODE_LINE.sub(CODE_LINE_NORMAL, text).encode("utf-8")


def record(root: Path, out: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="d002-reproducible-") as tmp:
        got = run_steps(root, Path(tmp))
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"recorded_at": RECORDED_AT, "codes": got["codes"], "files": {}}
    for name in OUTPUTS:
        data = normalise(name, got["outputs"][name])
        (out / name).write_bytes(data)
        manifest["files"][name] = hashlib.sha256(data).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


# --------------------------------------------------------------------------------------------------- the test

class D002ReproducibleTests(unittest.TestCase):
    """D002's settings on the fixed input give D002's bytes on this code."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="d002-reproducible-")
        cls.got = run_steps(ROOT, Path(cls._tmp.name))
        cls.manifest = json.loads((EXPECTED / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_expected_files_were_recorded_from_the_code_d002_ran(self) -> None:
        self.assertEqual(self.manifest["recorded_at"], RECORDED_AT)
        for name in OUTPUTS:
            with self.subTest(file=name):
                data = (EXPECTED / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), self.manifest["files"][name])

    def test_every_step_exits_as_it_did(self) -> None:
        self.assertEqual(self.got["codes"], self.manifest["codes"])

    def test_the_outputs_are_d002s_bytes(self) -> None:
        for name in OUTPUTS:
            with self.subTest(file=name):
                self.assertIn(name, self.got["outputs"])
                self.assertEqual(normalise(name, self.got["outputs"][name]).decode("utf-8"),
                                 (EXPECTED / name).read_text(encoding="utf-8"))

    def test_the_run_is_a_full_d002_run(self) -> None:
        report = json.loads(self.got["outputs"]["report.json"])
        self.assertEqual(report["experiment"], "D002")
        self.assertEqual(sorted(report["criteria"]), ["C1", "C2", "M1", "M2", "M3", "M4"])
        self.assertIn(report["verdict"], ("pass", "fail"))
        for arm in ("msha", "nhtsa"):
            doc = json.loads(self.got["outputs"][f"{arm}.arm.json"])
            self.assertEqual(doc["errors"], [])
            self.assertTrue(all(c["check"]["passed"] for c in doc["companies"].values()))
        self.assertEqual(len(json.loads(self.got["outputs"]["msha.arm.json"])["companies"]), 3)
        self.assertEqual(len(json.loads(self.got["outputs"]["nhtsa.arm.json"])["companies"]), 6)

    def test_only_the_code_values_and_the_path_are_blanked(self) -> None:
        def strip(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: strip(v) for k, v in value.items() if k not in CODE_KEYS + PATH_KEYS}
            if isinstance(value, list):
                return [strip(v) for v in value]
            return value

        for name in OUTPUTS[:3]:
            with self.subTest(file=name):
                raw = json.loads(self.got["outputs"][name])
                norm = json.loads(normalise(name, self.got["outputs"][name]))
                self.assertEqual(strip(raw), strip(norm))
        raw = self.got["outputs"]["report.md"].decode("utf-8").splitlines()
        norm = normalise("report.md", self.got["outputs"]["report.md"]).decode("utf-8").splitlines()
        self.assertEqual(len(raw), len(norm))
        changed = [b for a, b in zip(raw, norm) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0].startswith(CODE_LINE_NORMAL))

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="record D002's outputs on the fixed input from a code root")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("record")
    r.add_argument("--root", required=True)
    r.add_argument("--out", required=True)
    args = p.parse_args(argv)
    manifest = record(Path(args.root).resolve(), Path(args.out))
    print(json.dumps(manifest, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A synthetic accident file in MSHA's layout for the L001 tests and the lab's dry run (no MSHA record is read).

    python -m tests.lab.l1_data --out DIR [--seed 1]

writes ``DIR/Accidents.zip`` (``Accidents.txt`` inside: every field in double quotes, pipe-delimited, dates
mm/dd/yyyy, as ``tools/onboard/fetch_msha.py split`` reads MSHA's file), ``Accidents_Definition_File.txt`` and
``download.json``. Every value is invented. Operator c1 (the controller with the most training rows) has eight mines,
five specific filed categories and one non-specific one; its held-out years have a flat background, one record every
three weeks at each mine in a rotating category, and one burst written only in the narratives: in 2023, four of its
mines file two records a week for four weeks under other categories, each narrative describing a slip or fall. The
drafted pack then has five specific predicates, and c1's audit raises exactly one X alert (slip or fall of person) and
no S alert, the shape L001's rule needs (K11). A second operator qualifies too (c2); nothing else does.

:data:`OPERATOR` and :func:`secrets` hold every identifying value, so a test can look for each in every output.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "docs" / "collective" / "onboard" / "D002-settings.json"
COLUMNS = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))["arms"]["msha"]["columns"]
CUES = {"FALL OF ROOF OR BACK": ("roof", "rock", "bolter", "brow", "rib"),
        "HANDLING OF MATERIALS": ("lifting", "carrying", "bag", "strained", "pallet"),
        "SLIP OR FALL OF PERSON": ("slipped", "icy", "walkway", "stairs", "ladder"),
        "POWERED HAULAGE": ("shuttle", "scoop", "conveyor", "haul", "trolley"),
        "MACHINERY": ("crusher", "pinch", "guard", "drill", "belt"),
        "NO VALUE FOUND": ("assorted", "various", "general")}
FILLER = ("employee", "working", "shift", "crew", "morning", "reported", "nearby", "section", "worker", "tool",
          "area", "while", "during", "noticed", "returned", "afternoon")
ALERT_CATEGORY = "SLIP OR FALL OF PERSON"
OPERATOR = {"controller_id": "C00417", "controller_name": "Quillfeather Mining Holdings",
            "operator_id": "P90417", "operator_name": "Quillfeather Ridge Operations", "contractor_id": "ZXQ7"}
OTHER_OPERATOR = {"controller_id": "C00888", "controller_name": "Brackenridge Aggregates Group",
                  "operator_id": "P90888", "operator_name": "Brackenridge Hollow Quarry", "contractor_id": "WVK9"}
MINES = 8
BURST_WEEKS = (80, 84)          # held-out weeks from 2022-01-03: 2023-W29 to 2023-W32
BURST_MINES = 4
PERIOD = 3                      # one background record every three weeks at each mine


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

    def narrative(self, cue: str) -> str:
        first = " ".join(self.rng.sample(CUES[cue], 2) + self.rng.sample(FILLER, 5))
        second = " ".join(self.rng.sample(FILLER, 4) + [str(self.rng.randint(2, 99)), "feet"])
        return f"{first.capitalize()}. {second.capitalize()}."

    def add(self, op: dict[str, str], mine: str, day: date, category: str, cue: str | None = None) -> None:
        self.doc += 1
        doc, closed = f"2201{self.doc:08d}", f"9901{self.doc:08d}"
        text = self.narrative(cue or category)
        row = dict.fromkeys(COLUMNS, "")
        row.update({"MINE_ID": mine, "CONTROLLER_ID": op["controller_id"], "CONTROLLER_NAME": op["controller_name"],
                    "OPERATOR_ID": op["operator_id"], "OPERATOR_NAME": op["operator_name"],
                    "CONTRACTOR_ID": op["contractor_id"], "DOCUMENT_NO": doc, "CLOSED_DOC_NO": closed,
                    "FIPS_STATE_CD": "54", "ACCIDENT_DT": day.strftime("%m/%d/%Y"), "CAL_YR": str(day.year),
                    "CLASSIFICATION": category, "NARRATIVE": text, "COAL_METAL_IND": "C"})
        self.lines.append("|".join('"' + row[c].replace('"', '""') + '"' for c in COLUMNS))
        if op is OPERATOR:
            self.narratives.append(text)
        self.ids["documents"].add(doc)
        self.ids["mines"].add(mine)
        self.ids["closed"].add(closed)

    def day(self, first: date, last: date) -> date:
        return first + timedelta(days=self.rng.randrange((last - first).days + 1))

    def data(self) -> bytes:
        return ("\r\n".join(self.lines) + "\r\n").encode("utf-8")


def accident_file(seed: int = 1) -> Writer:
    w = Writer(seed)
    rng = w.rng
    mines = [mine_id(4612001, i) for i in range(MINES)]
    cats = sorted(CUES)
    train = (date(2015, 1, 1), date(2021, 12, 31))
    for cat in cats:
        for _ in range(60):
            w.add(OPERATOR, rng.choice(mines), w.day(*train), cat)
    start = date(2022, 1, 3)
    others = [c for c in cats if c != ALERT_CATEGORY]
    for week in range(156):
        monday = start + timedelta(days=7 * week)
        for i, mine in enumerate(mines):
            if (week + i) % PERIOD == 0:
                w.add(OPERATOR, mine, monday + timedelta(days=(week + i) % 5),
                      cats[((week + i) // PERIOD + i) % len(cats)])
        if BURST_WEEKS[0] <= week < BURST_WEEKS[1]:
            for i, mine in enumerate(mines[:BURST_MINES]):
                for j in range(2):
                    w.add(OPERATOR, mine, monday + timedelta(days=rng.randrange(5)),
                          others[(week * 2 + j + i) % len(others)], ALERT_CATEGORY)
    other = [mine_id(4713001, i) for i in range(4)]
    for cat in cats:
        if cat == "NO VALUE FOUND":
            continue
        for _ in range(55):
            w.add(OTHER_OPERATOR, rng.choice(other), w.day(*train), cat)
    for _ in range(120):
        w.add(OTHER_OPERATOR, rng.choice(other), w.day(date(2022, 1, 1), date(2024, 12, 31)), rng.choice(cats))
    return w


def write_raw(raw: Path, w: Writer) -> Path:
    """The three files ``fetch_msha.py download`` writes, with the writer's rows; returns the zip's path."""
    raw.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Accidents.txt", w.data())
    data = buf.getvalue()
    (raw / "Accidents.zip").write_bytes(data)
    (raw / "Accidents_Definition_File.txt").write_text("CLASSIFICATION\tThe circumstances.\n\nNARRATIVE\tThe text.\n",
                                                        encoding="utf-8")
    import hashlib
    (raw / "download.json").write_text(json.dumps([{"file": "Accidents.zip", "bytes": len(data),
                                                    "sha256": hashlib.sha256(data).hexdigest()}]), encoding="utf-8")
    return raw / "Accidents.zip"


def secrets(w: Writer) -> list[str]:
    """Every identifying value of the file: document numbers, mine ids, closed document numbers and the operators'
    names and ids."""
    out = [*w.ids["documents"], *w.ids["mines"], *w.ids["closed"]]
    for op in (OPERATOR, OTHER_OPERATOR):
        out += list(op.values())
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.lab.l1_data", description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args(argv)
    path = write_raw(Path(args.out), accident_file(args.seed))
    print(f"wrote {path} (synthetic: every value invented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

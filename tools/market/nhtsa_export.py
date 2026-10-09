#!/usr/bin/env python
"""Public replay V001's inputs: one make's NHTSA complaints as a pilot export, its recalls as the outcomes.

    python tools/market/nhtsa_export.py --run docs/collective/replay/vehicles/run-001.json
        --pack docs/collective/replay/vehicles/pack --out vexport

Applies the rule of `docs/collective/replay/vehicles/CHOICE-V001.md` to NHTSA's public flat files and writes the two
inputs of ``mycelic.collective.pilot.audit run``:

* ``export.csv``: one row per complaint (``ODINO``) of the run's make received (``LDATE``) in the run's window, product
  type vehicle, with a two-letter consumer state and a known model year: ``odino``, ``state`` (lower-cased: each state
  is a site), ``received``, ``components[]`` (the complaint's top-level component categories; a category outside the
  pack's becomes ``UNKNOWN OR OTHER``), ``vehicle`` (make-model-year, ``vehicle_pack.vehicle_id``) and ``summary``
  (the narrative). Nothing else: no VIN, city, dealer, incident state or operator's name.
* ``outcomes.csv``: one row per recall campaign and vehicle of the make whose Part 573 report was received
  (``RCDATE``) in the window, vehicle recalls only: ``outcome_id`` (``CAMPNO/vehicle``), ``opened``, the vehicle, and
  the predicate of the recall's component category when the campaign names exactly one of the pack's categories for
  that vehicle (else empty: any predicate).

It prints the counts of what it kept and dropped; it never prints a complaint or a recall. The pure steps are tested
offline; the downloads run where static.nhtsa.gov is reachable (the vehicle-replay workflow).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vehicle_pack import GENERIC_CATEGORY, category_of, vehicle_id  # noqa: E402

BASE = "https://static.nhtsa.gov/odi/ffdd"
COMPLAINTS_URL = f"{BASE}/cmpl/COMPLAINTS_RECEIVED_2020-2024.zip"
RECALLS_URL = f"{BASE}/rcl/FLAT_RCL_POST_2010.zip"
# 0-based field positions (CMPL.txt and RCL.txt, as the probe printed them)
C_ODINO, C_MAKE, C_MODEL, C_YEAR, C_COMP, C_STATE, C_LDATE, C_DESCR, C_PROD = 1, 3, 4, 5, 11, 13, 16, 19, 45
R_CAMPNO, R_MAKE, R_MODEL, R_YEAR, R_COMP, R_TYPE, R_RCDATE = 1, 2, 3, 4, 6, 10, 15
_STATE = re.compile(r"[A-Z]{2}")
_DATE = re.compile(r"\d{8}")


def _field(row: Sequence[str], i: int) -> str:
    return row[i].strip() if i < len(row) else ""


def complaints(rows: Iterable[Sequence[str]], make: str, date_from: str, date_to: str,
               categories: set[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The export rows (one per ODINO, in ODINO order) and the counts of what was kept and dropped."""
    counts: Counter[str] = Counter()
    by: dict[str, dict[str, Any]] = {}
    for row in rows:
        if _field(row, C_MAKE).upper() != make:
            continue
        counts["make_rows"] += 1
        day = _field(row, C_LDATE)
        if not _DATE.fullmatch(day) or not date_from <= day <= date_to:
            counts["outside_window"] += 1
            continue
        if _field(row, C_PROD) not in ("", "V"):
            counts["not_vehicle"] += 1
            continue
        state = _field(row, C_STATE).upper()
        vid = vehicle_id(make, _field(row, C_MODEL), _field(row, C_YEAR))
        if _STATE.fullmatch(state) is None:
            counts["no_state"] += 1
            continue
        if vid is None:
            counts["no_model_year"] += 1
            continue
        odino = _field(row, C_ODINO)
        cat = category_of(_field(row, C_COMP))
        cat = cat if cat in categories else GENERIC_CATEGORY
        entry = by.get(odino)
        if entry is None:
            by[odino] = {"odino": odino, "state": state.lower(), "received": day, "components": [cat],
                         "vehicle": vid, "summary": _field(row, C_DESCR)}
            continue
        if entry["vehicle"] != vid:
            counts["second_vehicle_rows"] += 1
            continue
        if cat not in entry["components"]:
            entry["components"].append(cat)
        if not entry["summary"]:
            entry["summary"] = _field(row, C_DESCR)
    out = [dict(by[k], components=sorted(by[k]["components"])) for k in sorted(by)]
    counts["complaints"] = len(out)
    counts["without_narrative"] = sum(1 for r in out if not r["summary"])
    return out, dict(sorted(counts.items()))


def recalls(rows: Iterable[Sequence[str]], make: str, date_from: str, date_to: str,
            predicate_of: Mapping[str, str]) -> tuple[list[dict[str, str]], dict[str, int]]:
    """The outcome rows (one per campaign and vehicle) and the counts of what was kept and dropped."""
    counts: Counter[str] = Counter()
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if _field(row, R_MAKE).upper() != make:
            continue
        counts["make_rows"] += 1
        if _field(row, R_TYPE) != "V":
            counts["not_vehicle"] += 1
            continue
        day = _field(row, R_RCDATE)
        if not _DATE.fullmatch(day) or not date_from <= day <= date_to:
            counts["outside_window"] += 1
            continue
        vid = vehicle_id(make, _field(row, R_MODEL), _field(row, R_YEAR))
        if vid is None:
            counts["no_model_year"] += 1
            continue
        key = (_field(row, R_CAMPNO), vid)
        entry = by.setdefault(key, {"opened": day, "predicates": set()})
        entry["opened"] = min(entry["opened"], day)
        pred = predicate_of.get(category_of(_field(row, R_COMP)))
        if pred is not None:
            entry["predicates"].add(pred)
    out = []
    for (campno, vid), entry in sorted(by.items()):
        preds = sorted(entry["predicates"])
        d = entry["opened"]
        out.append({"outcome_id": f"{campno}/{vid}", "opened": f"{d[:4]}-{d[4:6]}-{d[6:]}", "entity_type": "vehicle",
                    "entity_id": vid, "predicate": preds[0] if len(preds) == 1 else ""})
    counts["outcomes"] = len(out)
    counts["campaigns"] = len({k[0] for k in by})
    return out, dict(sorted(counts.items()))


def pack_categories(pack_dir: Path) -> tuple[set[str], dict[str, str]]:
    """The pack's category names and each specific category's predicate (from its mapping and codes)."""
    mapping = json.loads((pack_dir / "mapping.json").read_text(encoding="utf-8"))
    codes = json.loads((pack_dir / "codes.json").read_text(encoding="utf-8"))
    value_map = mapping["codes"][0]["value_map"]
    predicate_of = {c: codes[code]["predicate"] for c, code in value_map.items() if codes[code]["specific"]}
    return set(value_map), predicate_of


def _rows(blob: bytes) -> Iterator[list[str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            with z.open(name) as fh:
                for line in io.TextIOWrapper(fh, encoding="latin-1", newline=""):
                    yield line.rstrip("\r\n").split("\t")


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mycelic-vehicle-replay"})
    with urllib.request.urlopen(req, timeout=600) as resp:   # noqa: S310 (fixed https host)
        return resp.read()


def write_outcomes(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["outcome_id", "opened", "entity_type", "entity_id", "predicate"])
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--run", required=True, help="the run file: make, date_from, date_to")
    p.add_argument("--pack", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    make, first, last = run["make"].upper(), run["date_from"], run["date_to"]
    categories, predicate_of = pack_categories(Path(args.pack))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mycelic.collective.pilot.audit import write_csv  # noqa: E402
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    export, c_counts = complaints(_rows(_fetch(COMPLAINTS_URL)), make, first, last, categories)
    write_csv(export, out / "export.csv")
    outcomes, r_counts = recalls(_rows(_fetch(RECALLS_URL)), make, first, last, predicate_of)
    write_outcomes(outcomes, out / "outcomes.csv")
    print(json.dumps({"make": make, "window": [first, last], "complaints": c_counts, "recalls": r_counts},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

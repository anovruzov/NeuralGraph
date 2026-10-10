#!/usr/bin/env python
"""The shape of MSHA's date columns, and what the definition file says about them. Shapes only, no values.

    python tools/onboard/date_probe.py --out date-probe.json

D001's first run (run 38017322304) stopped at the split: no fixed date format parsed enough of ACCIDENT_DT. This
probe downloads Accidents.zip and Accidents_Definition_File.txt, the same two files D001 downloaded, and records for
every column whose name ends in _DT the ten most common value shapes with their counts (each digit becomes 9, each
upper-case letter A, each lower-case letter a; every other character is kept) and the empty share. It also records
the definition file's lines that mention a date. It prints and writes no value of any record.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

BASE = "https://arlweb.msha.gov/OpenGovernmentData/DataSets/"
UA = "mycelic-onboard-probe (schema-only research probe)"


def fetch(name: str) -> bytes:
    req = urllib.request.Request(BASE + name, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as resp:   # noqa: S310 (fixed https host)
        return resp.read()


def shape(value: str) -> str:
    return "".join("9" if c.isdigit() else "A" if c.isupper() else "a" if c.islower() else c for c in value)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out: dict = {"source": BASE, "columns": {}, "definition_lines": []}
    with zipfile.ZipFile(io.BytesIO(fetch("Accidents.zip"))) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        with z.open(name) as fh:
            lines = io.TextIOWrapper(fh, encoding="latin-1", newline="")
            header = lines.readline().rstrip("\r\n").split("|")
            cols = [i for i, h in enumerate(header) if h.endswith("_DT")]
            shapes = {header[i]: Counter() for i in cols}
            rows = 0
            for line in lines:
                parts = line.rstrip("\r\n").split("|")
                rows += 1
                for i in cols:
                    v = parts[i].strip() if i < len(parts) else ""
                    shapes[header[i]][shape(v) if v else "<empty>"] += 1
    out["rows"] = rows
    for col, c in shapes.items():
        out["columns"][col] = {"top_shapes": c.most_common(10), "distinct_shapes": len(c)}
    for line in fetch("Accidents_Definition_File.txt").decode("latin-1").splitlines():
        if "_DT" in line or "date" in line.lower():
            out["definition_lines"].append(line.strip())
    text = json.dumps(out, indent=1) + "\n"
    Path(args.out).write_text(text, encoding="utf-8")
    print("=== DATE-PROBE BEGIN ===")
    print(text, end="")
    print("=== DATE-PROBE END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

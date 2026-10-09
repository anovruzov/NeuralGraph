#!/usr/bin/env python
"""Can NHTSA's public vehicle complaint files feed a second public replay? Schema and volume only.

    python tools/market/nhtsa_probe.py --out nhtsa-probe.json

Downloads the field definitions (``CMPL.txt``, ``RCL.txt``) and the complaint flat file for complaints received
2020-2024, and reads the complaint rows: field count per row, rows per year of ``DATEA`` (date added), the share with
a state, a narrative and a component, the ten makes with most complaints added in 2019 (before any window a replay
would use) and the ten most frequent top-level components. From the recall flat file it reads **only the number of
rows and fields per row**: no make, date, component or text of any recall is printed or written, so a replay's rule
can be fixed before any outcome is seen. Runs where static.nhtsa.gov is reachable (the market-count workflow).
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
from typing import Any, Iterator

BASE = "https://static.nhtsa.gov/odi/ffdd"
CMPL_DOC, RCL_DOC = f"{BASE}/cmpl/CMPL.txt", f"{BASE}/rcl/RCL.txt"
CMPL_FILES = (f"{BASE}/cmpl/COMPLAINTS_RECEIVED_2015-2019.zip", f"{BASE}/cmpl/COMPLAINTS_RECEIVED_2020-2024.zip")
RCL_FILE = f"{BASE}/rcl/FLAT_RCL.zip"
# CMPL.txt's field order (1-based there); checked against the printed definitions
CMPL_FIELDS = {"MAKETXT": 3, "MODELTXT": 4, "YEARTXT": 5, "COMPDESC": 11, "STATE": 13, "DATEA": 15, "CDESCR": 19}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mycelic-market-probe"})
    with urllib.request.urlopen(req, timeout=300) as resp:   # noqa: S310 (fixed https host)
        return resp.read()


def rows(blob: bytes) -> Iterator[list[str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            with z.open(name) as fh:
                for line in io.TextIOWrapper(fh, encoding="latin-1", newline=""):
                    yield line.rstrip("\r\n").split("\t")


def probe() -> dict[str, Any]:
    out: dict[str, Any] = {"docs": {}}
    for name, url in (("CMPL.txt", CMPL_DOC), ("RCL.txt", RCL_DOC)):
        try:
            out["docs"][name] = fetch(url).decode("latin-1")[:12000]
        except Exception as exc:                                  # noqa: BLE001 (report and go on)
            out["docs"][name] = f"error: {exc.__class__.__name__}: {exc}"
    widths: Counter[int] = Counter()
    years: Counter[str] = Counter()
    makes_2019: Counter[str] = Counter()
    components: Counter[str] = Counter()
    have = Counter()
    n = 0
    for url in CMPL_FILES:
        try:
            blob = fetch(url)
        except Exception as exc:                                  # noqa: BLE001
            out.setdefault("errors", []).append(f"{url}: {exc.__class__.__name__}: {exc}")
            continue
        out.setdefault("sizes", {})[url.rsplit("/", 1)[1]] = len(blob)
        for f in rows(blob):
            n += 1
            widths[len(f)] += 1
            if len(f) <= max(CMPL_FIELDS.values()):
                continue
            year = f[CMPL_FIELDS["DATEA"]][:4]
            years[year] += 1
            if year == "2019":
                makes_2019[f[CMPL_FIELDS["MAKETXT"]].strip()] += 1
            comp = f[CMPL_FIELDS["COMPDESC"]].strip()
            components[comp.split(":")[0]] += 1
            have["state"] += bool(f[CMPL_FIELDS["STATE"]].strip())
            have["narrative"] += bool(f[CMPL_FIELDS["CDESCR"]].strip())
            have["component"] += bool(comp)
    out["complaints"] = {"rows": n, "fields_per_row": dict(widths.most_common(5)),
                         "rows_by_year_added": dict(sorted(years.items())),
                         "share": {k: v / n if n else None for k, v in sorted(have.items())},
                         "top_makes_added_2019": makes_2019.most_common(10),
                         "top_components": components.most_common(40)}
    try:
        rcl = fetch(RCL_FILE)
        rwidths: Counter[int] = Counter(len(f) for f in rows(rcl))
        out["recalls"] = {"rows": sum(rwidths.values()), "fields_per_row": dict(rwidths.most_common(5)),
                          "note": "row and field counts only; no recall content read"}
    except Exception as exc:                                      # noqa: BLE001
        out["recalls"] = {"error": f"{exc.__class__.__name__}: {exc}"}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    result = {"kind": "nhtsa_probe", **probe()}
    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("=== NHTSA-PROBE BEGIN ===")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    print("=== NHTSA-PROBE END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

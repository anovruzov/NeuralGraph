#!/usr/bin/env python
"""Public replay 002's inputs: the most reported identifier values and problem terms before the replay's window.

    python tools/market/openfda_id_shapes.py --out id-shapes.json --word BECTON --codes JKA,FOZ,FMI,MDB
        --date-from 20210101 --date-to 20221231 [--top 100]

Written for `docs/collective/replay/CHOICE-002.md`, whose rule is fixed before this runs. For each product code it
asks openFDA's device events for the ``--top`` most reported values of ``device.lot_number``,
``device.model_number``, ``device.catalog_number`` and ``product_problems`` among the reports received
(``date_received``) in the given dates from manufacturer names containing ``--word``, with their report counts
(``count=<field>.exact``: one request per code and field). The dates end before the replay's window starts, so
nothing here comes from the weeks the replay scores, and the recall dataset is never queried.
``tools/market/replay_pack.py`` turns the output into the replay's pack.

The network side runs where api.fda.gov is reachable (the market-count workflow).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any, Sequence

FIELDS = ("device.lot_number", "device.model_number", "device.catalog_number", "product_problems")


def probe(word: str, codes: Sequence[str], date_from: str, date_to: str, top: int) -> dict[str, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import openfda_coverage as cov  # noqa: E402

    window = f"date_received:[{date_from}+TO+{date_to}]"
    reports: dict[str, int] = {}
    fields: dict[str, dict[str, Any]] = {f: {} for f in FIELDS}
    for code in codes:
        search = f"{window}+AND+device.device_report_product_code:\"{code}\"+AND+device.manufacturer_d_name:{word}"
        reports[code] = cov.total(search)
        for field in FIELDS:
            try:
                doc = cov._get({"search": search, "count": f"{field}.exact", "limit": top}) or {}
            except urllib.error.HTTPError as err:
                fields[field][code] = {"error": f"HTTP {err.code}"}
                continue
            fields[field][code] = [[str(r["term"]), int(r["count"])] for r in doc.get("results", [])]
        print(f"id shapes: {code} {reports[code]} reports", file=sys.stderr)
    return {"reports": reports, "fields": fields}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--word", required=True)
    p.add_argument("--codes", required=True, help="comma-separated")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--top", type=int, default=100)
    args = p.parse_args(argv)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    found = probe(args.word, codes, args.date_from, args.date_to, args.top)
    result = {"kind": "openfda_id_shapes", "word": args.word, "codes": codes, "date_from": args.date_from,
              "date_to": args.date_to, "top": args.top, **found,
              "note": "the most reported values per code and field among reports received in the dates from names "
                      "containing the word, with report counts; events only, no recall data"}
    text = json.dumps(result, indent=1, sort_keys=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print("=== ID-SHAPES BEGIN ===")
    print(text)
    print("=== ID-SHAPES END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

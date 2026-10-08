#!/usr/bin/env python
"""Can a manufacturer's device reports be split into its sites? (openFDA MAUDE, public data, model-free)

    python tools/market/openfda_sites.py --out sites.json [--year 2024] [--group NAME ...]

The public replay (STRATEGY 9.3) works within one manufacturer and needs a field that splits its reports into
"sites". This probe checks the candidate the reports themselves carry: the device manufacturer's name and country on
each report (``device.manufacturer_d_name``, ``device.manufacturer_d_country``), which name the plant or legal entity
that made the device. For each group (a word matched in ``device.manufacturer_d_name``) it counts the year's reports by
manufacturer name and by country and reports how many of each carry at least 100 reports, the largest ten of each,
and the share of the group's reports the largest name holds (a split dominated by one name is not much of a split).

It reads no recall data and chooses nothing: the replay's manufacturer, codes, window and partition field are the
founder's choice, made before any recall outcome is looked at. The network side runs where api.fda.gov is reachable
(the market-count workflow); ``summarise`` is pure and tested offline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

# the largest groups in the establishment count (run 37851835003), as one word each of their reported names
DEFAULT_GROUPS = ("ABBOTT", "STRYKER", "PHILIPS", "MEDTRONIC", "GE", "BOSTON", "BAXTER", "BECTON")
MIN_REPORTS = 100
TOP = 10


def summarise(by_name: Iterable[Mapping[str, Any]], by_country: Iterable[Mapping[str, Any]],
              total: int) -> dict[str, Any]:
    """openFDA count results (``term``/``count``) for one group, as the split it offers."""
    names = sorted(((str(r["term"]), int(r["count"])) for r in by_name), key=lambda kv: (-kv[1], kv[0]))
    countries = sorted(((str(r["term"]), int(r["count"])) for r in by_country), key=lambda kv: (-kv[1], kv[0]))
    return {"reports": total,
            "names_with_min_reports": sum(1 for _, c in names if c >= MIN_REPORTS),
            "countries_with_min_reports": sum(1 for _, c in countries if c >= MIN_REPORTS),
            "largest_name_share": round(names[0][1] / total, 4) if names and total else None,
            "top_names": [{"name": n, "reports": c} for n, c in names[:TOP]],
            "top_countries": [{"country": n, "reports": c} for n, c in countries[:TOP]]}


def probe(year: int, groups: Iterable[str]) -> dict[str, Any]:
    # the HTTP helper and the window are the coverage probe's (same host, same retry rules)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import openfda_coverage as cov  # noqa: E402

    out: dict[str, Any] = {"kind": "openfda_site_split", "dataset": "device/event", "year": year,
                           "min_reports": MIN_REPORTS, "groups": {}}
    for group in groups:
        search = f"{cov._window(year)}+AND+device.manufacturer_d_name:{group}"
        total = cov.total(search)
        names = (cov._get({"search": search, "count": "device.manufacturer_d_name.exact", "limit": 100}) or {})
        countries = (cov._get({"search": search, "count": "device.manufacturer_d_country.exact", "limit": 100}) or {})
        out["groups"][group] = summarise(names.get("results", []), countries.get("results", []), total)
        print(f"sites: {group} {total} reports", file=sys.stderr)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--year", type=int, default=2024)
    p.add_argument("--group", action="append", help="a word of the manufacturer's reported name (repeatable)")
    args = p.parse_args(argv)
    result = probe(args.year, args.group or DEFAULT_GROUPS)
    result["note"] = ("a group is every report whose device manufacturer name contains the word; that can include "
                      "unrelated firms sharing the word (GE, BOSTON), so read the top names before using a split")
    text = json.dumps(result, indent=1, sort_keys=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print("=== SITES BEGIN ===")
    print(text)
    print("=== SITES END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

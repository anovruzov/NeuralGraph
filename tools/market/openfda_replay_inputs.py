#!/usr/bin/env python
"""The inputs a public replay's choice rule needs: event volumes per code and year, and recalling-firm spellings.

    python tools/market/openfda_replay_inputs.py --out replay-inputs.json --word BECTON
        --codes JKA,FOZ,FMI,FMF,FPA,MDB [--first-year 2019] [--last-year 2024] [--cap 10000]

Written for `docs/collective/replay/CHOICE-001.md`, whose rule is fixed before this runs. For each candidate code and
calendar year it counts the device reports received (``date_received``) from every manufacturer (what the replay's
fetch reads) and from the names containing ``--word``; :func:`choose_window` then applies the rule's step 4 (the
longest window of whole years ending ``--last-year`` in which at least ``--min-codes`` codes have at most ``--cap``
reports, and the first ``--max-codes`` fitting codes in the given order). For the rule's step 6 it lists the recall
dataset's ``recalling_firm`` names that contain the word or begin with the word "BD": **names only**; no count, date,
product code or cause of any recall is printed or written.

The network side runs where api.fda.gov is reachable (the market-count workflow); :func:`choose_window` and
:func:`firm_names` are pure and tested offline.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

RECALL_BASE = "https://api.fda.gov/device/recall.json"
MAX_FIRMS = 10
_BD_WORD = re.compile(r"BD\b")


def choose_window(totals: Mapping[str, Mapping[int, int]], order: Sequence[str], first_year: int, last_year: int,
                  cap: int, max_codes: int = 5, min_codes: int = 3) -> dict[str, Any]:
    """The rule's step 4: the longest window of whole years ending ``last_year`` (starting no earlier than
    ``first_year``) in which at least ``min_codes`` codes of ``order`` have at most ``cap`` reports, and the first
    ``max_codes`` fitting codes in ``order``. A code or year missing from ``totals`` counts as not fitting."""
    for start in range(first_year, last_year + 1):
        years = range(start, last_year + 1)
        fitting = []
        for code in order:
            per_year = totals.get(code, {})
            if all(y in per_year for y in years) and sum(per_year[y] for y in years) <= cap:
                fitting.append(code)
        if len(fitting) >= min_codes:
            codes = fitting[:max_codes]
            return {"run": True, "date_from": f"{start}0101", "date_to": f"{last_year}1231", "years": len(years),
                    "codes": codes, "reports": {c: sum(totals[c][y] for y in years) for c in codes},
                    "not_fitting": [c for c in order if c not in fitting]}
    return {"run": False, "reason": f"no window of whole years ending {last_year} holds {min_codes} codes with at most "
                                    f"{cap} reports each"}


def firm_names(terms: Iterable[str], word: str) -> list[str]:
    """The rule's step 6: recalling-firm names containing ``word`` (case-insensitive) or beginning with the word "BD",
    in the given order, without repeats, at most :data:`MAX_FIRMS`."""
    out: list[str] = []
    for term in terms:
        name = str(term)
        upper = name.strip().upper()
        if (word.upper() in upper or _BD_WORD.match(upper)) and name not in out:
            out.append(name)
    return out[:MAX_FIRMS]


# --------------------------------------------------------------------------------------------------- network side

def probe(word: str, codes: Sequence[str], first_year: int, last_year: int) -> dict[str, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import openfda_coverage as cov  # noqa: E402

    totals: dict[str, dict[int, int]] = {}
    group: dict[str, dict[int, int]] = {}
    for code in codes:
        totals[code], group[code] = {}, {}
        for year in range(first_year, last_year + 1):
            search = f"{cov._window(year)}+AND+device.device_report_product_code:\"{code}\""
            totals[code][year] = cov.total(search)
            group[code][year] = cov.total(f"{search}+AND+device.manufacturer_d_name:{word}")
        print(f"replay inputs: {code} {sum(totals[code].values())} reports", file=sys.stderr)
    terms: list[str] = []
    for query in (word, "BD"):
        cov.BASE, saved = RECALL_BASE, cov.BASE
        try:
            doc = cov._get({"search": f"recalling_firm:{query}", "count": "recalling_firm.exact", "limit": 100}) or {}
        finally:
            cov.BASE = saved
        terms += [str(r["term"]) for r in doc.get("results", [])]   # count order; the counts are dropped here
    return {"totals": totals, "group": group, "firm_names": firm_names(terms, word)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--word", required=True)
    p.add_argument("--codes", required=True, help="comma-separated, in the rule's order")
    p.add_argument("--first-year", type=int, default=2019)
    p.add_argument("--last-year", type=int, default=2024)
    p.add_argument("--cap", type=int, default=10000)
    p.add_argument("--max-codes", type=int, default=5)
    p.add_argument("--min-codes", type=int, default=3)
    args = p.parse_args(argv)
    order = [c.strip() for c in args.codes.split(",") if c.strip()]
    found = probe(args.word, order, args.first_year, args.last_year)
    result = {"kind": "openfda_replay_inputs", "word": args.word, "order": order, "first_year": args.first_year,
              "last_year": args.last_year, "cap": args.cap,
              "totals": {c: {str(y): n for y, n in v.items()} for c, v in found["totals"].items()},
              "group": {c: {str(y): n for y, n in v.items()} for c, v in found["group"].items()},
              "window": choose_window(found["totals"], order, args.first_year, args.last_year, args.cap,
                                      args.max_codes, args.min_codes),
              "recalling_firm_names": found["firm_names"],
              "note": "event counts by date_received from every manufacturer (totals) and from names containing the "
                      "word (group); recalling-firm names only, no recall count, date, code or cause"}
    text = json.dumps(result, indent=1, sort_keys=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print("=== REPLAY-INPUTS BEGIN ===")
    print(text)
    print("=== REPLAY-INPUTS END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

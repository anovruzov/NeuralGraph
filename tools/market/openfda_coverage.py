#!/usr/bin/env python
"""How often real device complaint reports carry the fields the collective reads (openFDA MAUDE, public data).

    python tools/market/openfda_coverage.py --out coverage.json [--year 2024] [--codes 10] [--sample 100]

The constructed codes-miss scenario (B1b) failed because the structured fields allowed to leave a site (lot,
product), filled at the rates the pack generator assumes (lot 0.6, product 0.95), carried the case to the central
baselines about as early as the narratives carried it to X. Whether real reports look like that is an empirical
question this script measures, model-free, on FDA's public device adverse-event reports (MAUDE via openFDA):

* the product codes with the most reports received in one calendar year (``date_received``, never an event date);
* for each, from the API's own counts: reports with a lot number, a model number, a catalog number, any narrative
  text, a coded device problem, and the manufacturer's country;
* for each, from a sample of reports (the first ``--sample`` the API returns for the query and as many from the middle
  of its result set; not a random sample): the share whose lot number is a real value rather than a placeholder
  (``UNK``, ``NI``, ``N/A``, ...), the share whose coded device problems are all generic (the codes that say no
  problem was identified or no code applies), and the most frequent coded problems, so a reader can judge the list.

It reads no recall data and scores nothing: it describes the fields, it does not test the product. The network side
runs where api.fda.gov is reachable (the market-count workflow); the counting side (``summarise_sample``) is pure and
tested offline. Output: one JSON document, also printed between ``COVERAGE`` markers.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

BASE = "https://api.fda.gov/device/event.json"
# coded device problems that say no specific problem was identified (FDA device problem terms, matched lower-case)
GENERIC_PROBLEMS = (
    "adverse event without identified device or use problem",
    "appropriate term/code not available",
    "insufficient information",
    "no apparent adverse event",
    "unknown (for use when the device problem is not known)",
    "no known impact or consequence to patient",
)
PLACEHOLDER_LOTS = frozenset({"", "unk", "unknown", "ni", "na", "n/a", "n.a.", "none", "not applicable", "nr",
                              "not available", "unavailable", "asku", "ask", "-", "0", "no information", "not provided",
                              "not known", "notavailable", "masked", "redacted"})
FIELDS = {                     # what is counted with the API's own _exists_ queries
    "lot_number": "device.lot_number",
    "model_number": "device.model_number",
    "catalog_number": "device.catalog_number",
    "narrative": "mdr_text.text",
    "device_problem": "product_problems",
    "manufacturer_country": "device.manufacturer_d_country",
}
TOP_PROBLEMS = 8


def real_lot(value: Any) -> bool:
    """A lot number that names a lot: not empty and not a placeholder."""
    return isinstance(value, str) and value.strip().lower() not in PLACEHOLDER_LOTS


def generic_only(problems: Iterable[str]) -> bool:
    """True when every coded device problem is generic (or none is coded)."""
    return all(p.strip().lower() in GENERIC_PROBLEMS for p in problems)


def summarise_sample(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Shares over a sample of MAUDE event records: real lot numbers, narratives present, generic-only coded
    problems, and the most frequent coded problems."""
    n = lots = narratives = generic = coded = 0
    terms: Counter[str] = Counter()
    for rec in records:
        n += 1
        devices = rec.get("device") or []
        if any(real_lot(d.get("lot_number")) for d in devices if isinstance(d, dict)):
            lots += 1
        if any(isinstance(t, dict) and str(t.get("text") or "").strip() for t in rec.get("mdr_text") or []):
            narratives += 1
        problems = [p for p in rec.get("product_problems") or [] if isinstance(p, str)]
        if problems:
            coded += 1
            if generic_only(problems):
                generic += 1
        terms.update(p.strip() for p in problems)

    def share(k: int, d: int) -> float | None:
        return round(k / d, 4) if d else None
    return {"records": n, "real_lot_share": share(lots, n), "narrative_share": share(narratives, n),
            "coded_share": share(coded, n), "generic_only_share_of_coded": share(generic, coded),
            "top_problems": [{"problem": t, "records": c} for t, c in sorted(terms.items(),
                                                                                key=lambda kv: (-kv[1], kv[0]))
                             [:TOP_PROBLEMS]]}


# --------------------------------------------------------------------------------------------------- network side

def _get(params: Mapping[str, Any], *, retries: int = 4) -> dict[str, Any] | None:
    url = BASE + "?" + urllib.parse.urlencode(params, safe=":[]+_.")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:   # noqa: S310 (fixed https host)
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            if err.code == 404:                                     # openFDA: no matching records
                return None
            if err.code in (429, 500, 502, 503) and attempt + 1 < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt + 1 < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    return None


def _window(year: int) -> str:
    return f"date_received:[{year}0101+TO+{year}1231]"


def total(search: str) -> int:
    doc = _get({"search": search, "limit": 1})
    return int(doc["meta"]["results"]["total"]) if doc else 0


def top_codes(year: int, n: int) -> list[dict[str, Any]]:
    doc = _get({"search": _window(year), "count": "device.device_report_product_code.exact", "limit": n})
    return [{"code": r["term"], "reports": r["count"]} for r in (doc or {}).get("results", [])]


def sample(search: str, size: int, middle: int) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for skip in sorted({0, max(0, min(middle, 25000 - size))}):
        doc = _get({"search": search, "limit": size, "skip": skip})
        out += (doc or {}).get("results", [])
    return out


def probe(year: int, codes: int, size: int) -> dict[str, Any]:
    window = _window(year)
    result: dict[str, Any] = {"kind": "openfda_field_coverage", "dataset": "device/event", "year": year,
                              "reports_in_year": total(window), "codes": []}
    for entry in top_codes(year, codes):
        search = f"{window}+AND+device.device_report_product_code:\"{entry['code']}\""
        n = total(search)
        counts = {name: total(f"{search}+AND+_exists_:{field}") for name, field in FIELDS.items()}
        recs = sample(search, size, n // 2)
        print(f"coverage: {entry['code']} {n} reports, sample {len(recs)}", file=sys.stderr)
        result["codes"].append({"code": entry["code"], "reports": n,
                                "field_present_share": {k: (round(v / n, 4) if n else None)
                                                        for k, v in counts.items()},
                                "sample": summarise_sample(recs)})
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--year", type=int, default=2024)
    p.add_argument("--codes", type=int, default=10)
    p.add_argument("--sample", type=int, default=100)
    args = p.parse_args(argv)
    result = probe(args.year, args.codes, args.sample)
    result["definitions"] = {"generic_problems": list(GENERIC_PROBLEMS), "placeholder_lots": sorted(PLACEHOLDER_LOTS),
                             "sample": "the first --sample reports the API returns for the query and as many from "
                                       "the middle of its result set; not a random sample"}
    text = json.dumps(result, indent=1, sort_keys=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print("=== COVERAGE BEGIN ===")
    print(text)
    print("=== COVERAGE END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

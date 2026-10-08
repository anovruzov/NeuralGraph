#!/usr/bin/env python
"""Count medical-device owner/operators by registered establishments and countries (openFDA, public data).

    python tools/market/openfda_establishments.py --out market.json [--index URL] [--files PATH ...]

STRATEGY section 8.2 sizes the beachhead as device makers "with at least 3 manufacturing or complaint-handling sites
in at least 2 countries", with an account count N that no source backed. This script replaces that guess with a
count: it reads FDA's establishment registration and device listing data (openFDA's bulk ``device/registrationlisting``
files), groups the registered establishments by owner/operator, and counts the owners in each band.

What it measures and what it does not:

* an **establishment** is one FDA registration number; an owner/operator is FDA's ``owner_operator_number``. A
  corporate group can hold several owner/operator numbers, so the script also reports a second grouping by a
  normalised firm name, which merges some of them (and can wrongly merge unrelated firms with the same name);
* only establishments registered with FDA appear: makers that sell into the US, plus foreign sites that must register.
  A maker selling only outside the US is missing, so the count is a floor for the world, not the world;
* "manufacturing or complaint-handling" is read from ``establishment_type`` strings: a type containing
  ``manufactur`` or ``complaint`` counts, a pure distributor or importer does not.

The network side (``fetch``) runs where api.fda.gov is reachable (the market-count workflow on GitHub Actions); the
counting side (``count``) is pure and tested offline. Output: one JSON document, printed between markers as well.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

INDEX = "https://api.fda.gov/download.json"
BANDS = ((2, 1), (3, 2), (5, 2), (5, 3), (10, 3))      # (at least n establishments, in at least c countries)
QUALIFYING = ("manufactur", "complaint")               # establishment_type substrings that count as a site
TOP = 25
_SUFFIX = re.compile(r"\b(inc|incorporated|llc|ltd|limited|gmbh|ag|sa|sas|spa|srl|bv|nv|co|corp|corporation|company|"
                     r"plc|kk|ab|as|oy|pty|se|lp|llp|the)\b")
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def normalise_name(name: str) -> str:
    """A firm name folded for grouping: lower case, punctuation and legal-form suffixes removed, spaces collapsed."""
    folded = _NONWORD.sub(" ", name.lower())
    return " ".join(_SUFFIX.sub(" ", folded).split())


def qualifying(types: Iterable[str]) -> bool:
    return any(q in t.lower() for t in types for q in QUALIFYING)


def establishments(records: Iterable[Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """``{registration_number: {owner, firm, country, types}}`` from openFDA registration-listing records (a
    registration can appear in several records; its types are unioned), and the count of records skipped for each
    missing field."""
    out: dict[str, dict[str, Any]] = {}
    skipped: dict[str, int] = defaultdict(int)
    for rec in records:
        reg = rec.get("registration") or {}
        number = reg.get("registration_number")
        owner = (reg.get("owner_operator") or {}).get("owner_operator_number")
        country = reg.get("iso_country_code")
        if not number:
            skipped["registration_number"] += 1
            continue
        if not owner:
            skipped["owner_operator_number"] += 1
            continue
        if not country:
            skipped["iso_country_code"] += 1
            continue
        types = rec.get("establishment_type") or []
        if isinstance(types, str):
            types = [types]
        entry = out.setdefault(number, {"owner": owner, "firm": (reg.get("owner_operator") or {}).get("firm_name")
                                        or reg.get("name") or "", "country": country, "types": set()})
        entry["types"].update(t for t in types if isinstance(t, str))
    return out, dict(skipped)


def _bands(groups: Mapping[str, Mapping[str, set[str]]]) -> list[dict[str, Any]]:
    return [{"min_establishments": n, "min_countries": c,
             "groups": sum(1 for g in groups.values() if len(g["sites"]) >= n and len(g["countries"]) >= c)}
            for n, c in BANDS]


def _grouped(est: Mapping[str, Mapping[str, Any]], key: str, only_qualifying: bool) -> dict[str, dict[str, set[str]]]:
    groups: dict[str, dict[str, set[str]]] = {}
    for number, e in est.items():
        if only_qualifying and not qualifying(e["types"]):
            continue
        k = e["owner"] if key == "owner" else normalise_name(e["firm"]) or e["owner"]
        g = groups.setdefault(k, {"sites": set(), "countries": set(), "names": set()})
        g["sites"].add(number)
        g["countries"].add(e["country"])
        g["names"].add(e["firm"])
    return groups


def count(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The bands for each grouping (owner/operator number, normalised firm name) and each site definition (every
    establishment, or manufacturing and complaint-handling ones only), plus the largest groups by sites."""
    est, skipped = establishments(records)
    views = {}
    for key in ("owner", "firm_name"):
        for only in (False, True):
            groups = _grouped(est, key, only)
            top = sorted(groups.items(), key=lambda kv: (-len(kv[1]["sites"]), -len(kv[1]["countries"]), kv[0]))
            views[f"{key}/{'manufacturing_or_complaint' if only else 'all_types'}"] = {
                "groups": len(groups), "bands": _bands(groups),
                "top": [{"name": sorted(g["names"])[0] if g["names"] else k, "sites": len(g["sites"]),
                         "countries": len(g["countries"])} for k, g in top[:TOP]]}
    return {"establishments": len(est), "qualifying_establishments": sum(1 for e in est.values()
                                                                        if qualifying(e["types"])),
            "countries": len({e["country"] for e in est.values()}), "skipped_records": skipped, "views": views}


# --------------------------------------------------------------------------------------------------- network side

def _get(url: str, timeout: float = 120) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:      # noqa: S310 (fixed https URLs from the index)
        return resp.read()


def partition_urls(index_url: str) -> list[str]:
    index = json.loads(_get(index_url))
    parts = index["results"]["device"]["registrationlisting"]["partitions"]
    return [p["file"] for p in parts]


def records_from_zip(data: bytes) -> Iterator[Mapping[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.endswith(".json"):
                yield from json.loads(zf.read(name))["results"]


def fetch(index_url: str) -> tuple[list[str], Iterator[Mapping[str, Any]]]:
    urls = partition_urls(index_url)

    def gen() -> Iterator[Mapping[str, Any]]:
        for url in urls:
            print(f"market-count: reading {url}", file=sys.stderr)
            yield from records_from_zip(_get(url, timeout=600))
    return urls, gen()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--index", default=INDEX)
    p.add_argument("--files", nargs="*", help="local partition zips instead of the network (testing)")
    args = p.parse_args(argv)
    if args.files:
        sources = [str(f) for f in args.files]
        records: Iterable[Mapping[str, Any]] = (r for f in args.files for r in records_from_zip(Path(f).read_bytes()))
    else:
        sources, records = fetch(args.index)
    result = {"kind": "openfda_establishment_count", "dataset": "device/registrationlisting", "sources": sources,
              "bands_definition": "at least n establishments in at least c countries", **count(records)}
    text = json.dumps(result, indent=1, sort_keys=True)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print("=== MARKET-COUNT BEGIN ===")
    print(text)
    print("=== MARKET-COUNT END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

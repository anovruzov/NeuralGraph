#!/usr/bin/env python
"""Can the public CFPB consumer complaint database feed a test in a field no pack covers? Schema and volume only.

    python tools/onboard/cfpb_probe.py --out cfpb-probe.json

Asks the database's public search API for the number of complaints, with and without a consumer narrative, per year
received (2019 to 2025), the field names of one complaint (names only, no values), and the API's own aggregations
(the 30 largest buckets of each, as key and count). It also asks for the size of the bulk CSV export. It prints and
writes no complaint's narrative or other values. Every request's outcome is recorded, so an unreachable host is a
result, not a crash.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"
BULK = ("https://files.consumerfinance.gov/ccdb/complaints.csv.zip", "https://files.consumerfinance.gov/ccdb/complaints.json.zip")
YEARS = range(2019, 2026)
TOP = 30


def get(url: str, *, method: str = "GET") -> tuple[int | None, dict[str, str], bytes, str | None]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "mycelic-onboard-probe"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:   # noqa: S310 (fixed https hosts)
            return resp.status, dict(resp.headers), resp.read() if method == "GET" else b"", None
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b"", f"HTTPError {e.code}"
    except Exception as e:   # noqa: BLE001 (the probe records every failure)
        return None, {}, b"", f"{type(e).__name__}: {e}"


def query(**params: Any) -> tuple[dict[str, Any], Any]:
    url = API + "?" + urllib.parse.urlencode(params)
    status, _, body, err = get(url)
    outcome: dict[str, Any] = {"params": params, "status": status, "error": err}
    data = None
    if body:
        try:
            data = json.loads(body)
        except ValueError as e:
            outcome["error"] = f"not JSON: {e}"
    return outcome, data


def total(data: Any) -> int | None:
    hits = data.get("hits") if isinstance(data, dict) else None
    t = hits.get("total") if isinstance(hits, dict) else None
    if isinstance(t, dict):
        t = t.get("value")
    return t if isinstance(t, int) else None


def buckets(node: Any, path: str, out: dict[str, list[list[Any]]]) -> None:
    """Every bucket list under the aggregations, keyed by its path; top TOP buckets as [key, count]."""
    if isinstance(node, dict):
        if isinstance(node.get("buckets"), list):
            rows = [[b.get("key"), b.get("doc_count")] for b in node["buckets"] if isinstance(b, dict)]
            out[path] = {"n_buckets": len(rows), "top": sorted(rows, key=lambda r: -(r[1] or 0))[:TOP]}
            return
        for k, v in node.items():
            buckets(v, f"{path}.{k}" if path else k, out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    probe: dict[str, Any] = {"api": API, "requests": [], "years": {}, "fields": None, "aggregations": None, "bulk": []}
    for y in YEARS:
        row: dict[str, Any] = {}
        for label, extra in (("all", {}), ("with_narrative", {"has_narrative": "true"})):
            o, d = query(size=1, no_aggs="true", date_received_min=f"{y}-01-01", date_received_max=f"{y + 1}-01-01", **extra)
            probe["requests"].append(o)
            row[label] = total(d)
        probe["years"][str(y)] = row
    o, d = query(size=1, has_narrative="true", date_received_min="2023-01-01", date_received_max="2025-01-01")
    probe["requests"].append(o)
    if isinstance(d, dict):
        hits = (d.get("hits") or {}).get("hits") or []
        if hits and isinstance(hits[0], dict):
            probe["fields"] = sorted((hits[0].get("_source") or {}).keys())
        aggs: dict[str, Any] = {}
        buckets(d.get("aggregations") or {}, "", aggs)
        probe["aggregations"] = aggs
        probe["aggregations_scope"] = "complaints with a narrative received 2023-01-01 to 2025-01-01"
        probe["aggregations_total"] = total(d)
    for url in BULK:
        status, headers, _, err = get(url, method="HEAD")
        probe["bulk"].append({"url": url, "status": status, "error": err,
                              "content_length": headers.get("Content-Length") or headers.get("content-length")})
    text = json.dumps(probe, sort_keys=True, indent=1) + "\n"
    Path(args.out).write_text(text, encoding="utf-8")
    print("=== CFPB-PROBE BEGIN ===")
    print(text, end="")
    print("=== CFPB-PROBE END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

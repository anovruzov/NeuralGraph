#!/usr/bin/env python
"""Which public record sources with free text and a filed category can the lab's runners read? Schema only.

    python tools/onboard/source_probe.py --out source-probe.json

For each candidate (below) it records the HTTP status, size and type. For a reachable zip or CSV of at most MAX_BYTES it
also records, per member, the header's field names, the delimiter and the number of rows. For a JSON API it records the
reported total and the field names of one result. It prints and writes no field value of any record. Each request
names itself honestly (User-Agent below); a refusal is recorded as a result and never retried another way.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

UA = "mycelic-onboard-probe (schema-only research probe)"
MAX_BYTES = 400_000_000
# (name, kind, url): kind "file" is a zip or delimited text; "json" is an API returning JSON; "page" lists the page's
# links to zip, csv or xlsx files (their names only)
CANDIDATES = (
    ("msha_accidents", "file", "https://arlweb.msha.gov/OpenGovernmentData/DataSets/Accidents.zip"),
    ("msha_mines", "file", "https://arlweb.msha.gov/OpenGovernmentData/DataSets/Mines.zip"),
    ("msha_ogd_page", "page", "https://arlweb.msha.gov/OpenGovernmentData/OGIMSHA.asp"),
    ("msha_new_page", "page", "https://www.msha.gov/data-and-reports/mine-data-retrieval-system"),
    ("osha_severe_page", "page", "https://www.osha.gov/severe-injury-reports"),
    ("openfda_device_event", "json", "https://api.fda.gov/device/event.json?search=date_received:[20240101+TO+20241231]&limit=1"),
    ("cpsc_recalls_api", "json", "https://www.saferproducts.gov/RestWebServices/Recall?format=json&RecallDateStart=2024-01-01&RecallDateEnd=2024-01-31"),
    ("cpsc_data_page", "page", "https://www.cpsc.gov/Data"),
)


def request(url: str, method: str = "GET") -> tuple[int | None, dict[str, str], Any, str | None]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    try:
        resp = urllib.request.urlopen(req, timeout=180)   # noqa: S310 (fixed https hosts)
        return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp, None
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, None, f"HTTPError {e.code}"
    except Exception as e:   # noqa: BLE001 (the probe records every failure)
        return None, {}, None, f"{type(e).__name__}: {e}"


def table_shape(fh: io.TextIOBase) -> dict[str, Any]:
    head = fh.readline()
    delim = max(("|", "\t", ","), key=head.count)
    fields = next(csv.reader([head.rstrip("\r\n")], delimiter=delim))
    n = sum(1 for _ in fh)
    return {"delimiter": delim, "fields": fields, "rows_after_header": n}


def probe_file(url: str) -> dict[str, Any]:
    status, headers, resp, err = request(url)
    out: dict[str, Any] = {"status": status, "error": err, "content_type": headers.get("content-type"),
                           "content_length": headers.get("content-length")}
    if resp is None:
        return out
    blob = resp.read(MAX_BYTES + 1)
    out["bytes_read"] = len(blob)
    if len(blob) > MAX_BYTES:
        out["note"] = "larger than the probe reads; not opened"
        return out
    try:
        if blob[:2] == b"PK":
            members = []
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for info in z.infolist():
                    m: dict[str, Any] = {"name": info.filename, "size": info.file_size}
                    if re.search(r"\.(txt|csv|tsv)$", info.filename, re.I):
                        with z.open(info) as fh:
                            m.update(table_shape(io.TextIOWrapper(fh, encoding="latin-1", newline="")))
                    members.append(m)
            out["members"] = members
        else:
            out.update(table_shape(io.StringIO(blob.decode("latin-1"))))
    except Exception as e:   # noqa: BLE001
        out["parse_error"] = f"{type(e).__name__}: {e}"
    return out


def probe_json(url: str) -> dict[str, Any]:
    status, headers, resp, err = request(url)
    out: dict[str, Any] = {"status": status, "error": err, "content_type": headers.get("content-type")}
    if resp is None:
        return out
    try:
        data = json.loads(resp.read(5_000_000))
    except Exception as e:   # noqa: BLE001
        out["parse_error"] = f"{type(e).__name__}: {e}"
        return out
    if isinstance(data, dict):
        out["top_keys"] = sorted(data)
        meta = data.get("meta") or {}
        out["total"] = ((meta.get("results") or {}).get("total")) if isinstance(meta, dict) else None
        results = data.get("results")
    else:
        results = data
        out["n_results"] = len(data) if isinstance(data, list) else None
    if isinstance(results, list) and results and isinstance(results[0], dict):
        out["result_fields"] = sorted(results[0])
    return out


def probe_page(url: str) -> dict[str, Any]:
    status, headers, resp, err = request(url)
    out: dict[str, Any] = {"status": status, "error": err, "content_type": headers.get("content-type")}
    if resp is None:
        return out
    html = resp.read(3_000_000).decode("utf-8", "replace")
    links = sorted(set(re.findall(r"""href=["']([^"']+\.(?:zip|csv|xlsx|txt))["']""", html, re.I)))
    out["data_links"] = links[:80]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    probe = {"user_agent": UA, "sources": {}}
    for name, kind, url in CANDIDATES:
        fn = {"file": probe_file, "json": probe_json, "page": probe_page}[kind]
        probe["sources"][name] = {"url": url, "kind": kind, **fn(url)}
    text = json.dumps(probe, sort_keys=True, indent=1) + "\n"
    Path(args.out).write_text(text, encoding="utf-8")
    print("=== SOURCE-PROBE BEGIN ===")
    print(text, end="")
    print("=== SOURCE-PROBE END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

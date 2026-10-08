#!/usr/bin/env python
"""Why does the collective's openFDA connector get refused? A diagnostic for one events query (no recall data).

    python tools/market/openfda_connector_check.py --code JKA --date-from 20230101 --date-to 20241231

Lab run 37861226513's ``fetch-events`` step exited 2 within a second ("an HTTP error the connector does not retry,
or a bad query"). This builds the exact page URL ``connectors.openfda.page_url`` makes for the query, requests it
once, and prints the HTTP status and openFDA's ``error.code`` and ``error.message`` (never a record); then the same
query with ``:``, ``[``, ``]``, ``"`` and ``+`` left unencoded, as ``tools/market`` sends it; then the connector CLI
itself for 5 records, printing its exit code and stderr. Events dataset only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def status_of(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:   # noqa: S310 (fixed https host)
            doc = json.loads(resp.read())
            return f"HTTP {resp.status}, total {doc.get('meta', {}).get('results', {}).get('total')}"
    except urllib.error.HTTPError as err:
        try:
            error = json.loads(err.read(65536)).get("error", {})
        except ValueError:
            error = {}
        return f"HTTP {err.code}, error.code {error.get('code')!r}, error.message {error.get('message')!r}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--code", required=True)
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    args = p.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from mycelic.collective.connectors import openfda as conn  # noqa: E402

    url = conn.page_url(conn.DEFAULT_BASE_URL, "event", args.code, args.date_from, args.date_to, 5, 0)
    print("connector url:", url)
    print("connector url ->", status_of(url))
    search = f'device.device_report_product_code:"{args.code}"+AND+date_received:[{args.date_from}+TO+{args.date_to}]'
    alt = conn.DEFAULT_BASE_URL + "/device/event.json?" + urllib.parse.urlencode(
        {"search": search, "limit": 5, "skip": 0}, safe=':[]+"')
    print("unencoded url:", alt)
    print("unencoded url ->", status_of(alt))
    with tempfile.TemporaryDirectory() as tmp:
        done = subprocess.run([sys.executable, "-m", "mycelic.collective.connectors.openfda", "fetch", "--dataset",
                               "event", "--product-codes", args.code, "--date-from", args.date_from, "--date-to",
                               args.date_to, "--out", str(Path(tmp) / "events"), "--max-records", "5"],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)
    print("connector cli exit:", done.returncode)
    print("connector cli stderr:", done.stderr.strip()[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

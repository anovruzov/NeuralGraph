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
    p.add_argument("--lab-request", help="also run the lab's own fetch-events command for this request file")
    args = p.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from mycelic.collective.connectors import openfda as conn  # noqa: E402

    for limit in (5, 100, 1000):
        url = conn.page_url(conn.DEFAULT_BASE_URL, "event", args.code, args.date_from, args.date_to, limit, 0)
        print(f"connector url (limit {limit}):", url)
        print(f"connector url (limit {limit}) ->", status_of(url))
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
    if args.lab_request:
        # the lab's own fetch-events command for the request, run as the lab runs it (events only)
        import os
        from lab import ROOT as LAB_ROOT, openfda, units
        from lab.manifest import load_manifest
        from lab.request import validate
        params = validate(json.loads(Path(args.lab_request).read_text()),
                          load_manifest(ROOT / "lab" / "models.json"))["experiments"]["openfda"]
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp) / "r"
            (r / "cache").mkdir(parents=True)
            argv = openfda.step_argvs(params, r, key=False, base_url=None)[0][1]
            print("lab fetch-events argv:", " ".join(argv[1:]))
            res = units.run_process(argv, units.subprocess_env(os.environ, []), cwd=LAB_ROOT, timeout_s=1200,
                                    stdout_path=r / "out.log", stderr_path=r / "err.log")
            print("lab fetch-events exit:", res.exit_code, "wall_s:", round(res.wall_s, 1))
            print("lab fetch-events stdout:", (r / "out.log").read_text()[:2000])
            print("lab fetch-events stderr:", (r / "err.log").read_text()[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())

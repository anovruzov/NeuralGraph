#!/usr/bin/env python
"""D001's NHTSA arm input: the 2020-2024 complaint file, one export per make (CHOICE-D001, rule 2.2).

    python tools/onboard/fetch_nhtsa.py download --out DIR
    python tools/onboard/fetch_nhtsa.py split --settings FILE --raw DIR --out DIR

Two steps, so that a download failure is told apart from a run (rule 9):

* ``download`` fetches ``COMPLAINTS_RECEIVED_2020-2024.zip`` (the file of the vehicle replays and of R001 and R002,
  ``tools/market/nhtsa_export.COMPLAINTS_URL``) with ``download.json``; it prints the size and sha256, nothing else.
* ``split`` writes, for each make of the settings, the rows ``tools/market/nhtsa_export.complaints`` builds (one row
  per complaint, product type vehicle, a two-letter state, a known model year, one vehicle) over both windows, with
  one change from the replays: ``components[]`` holds every top-level component name as filed, none folded into the
  catch-all. A ``reporter`` column repeats each row's own complaint number, which ``pack-v2/`` reads. It writes
  ``<MAKE>.csv`` with the settings' columns in their order, ``companies.json`` with the counts of what was kept and
  dropped, and ``source.json`` with the download facts. It prints counts only.

This is per-field code, and so are the two ``tools/market`` modules it imports (``nhtsa_export.py`` and
``vehicle_pack.py``): the report gives the line count of all three, and its code hash covers them (amendment A8
of CHOICE-D001). It never prints a field value.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "market"))
import nhtsa_export  # noqa: E402

FILE = "COMPLAINTS_RECEIVED_2020-2024.zip"
UA = "mycelic-onboard-d001 (drafting test D001 on public data)"
REPORTER = "reporter"
LIST_SEP = ";"


class _Every:
    """A category set that holds every name: ``complaints`` then keeps each component name as filed."""

    def __contains__(self, item: object) -> bool:
        return True


def fetch(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=900) as resp:   # noqa: S310 (fixed https host)
        return resp.read()


def download(out: Path, fetcher: Callable[[str], bytes] = fetch) -> list[dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    data = fetcher(nhtsa_export.COMPLAINTS_URL)
    (out / FILE).write_bytes(data)
    facts = [{"file": FILE, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}]
    (out / "download.json").write_text(json.dumps(facts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for f in facts:
        print(json.dumps(f, sort_keys=True))
    return facts


def _ymd(day: str) -> str:
    return day.replace("-", "")


def rows_csv(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> bytes:
    """The rows as CSV with the given columns in order; a ``[]`` column holds its list joined by ``;``."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for r in rows:
        cells = []
        for col in columns:
            v = r[col.removesuffix("[]")]
            cells.append(LIST_SEP.join(v) if isinstance(v, list) else v)
        writer.writerow(cells)
    return buf.getvalue().encode("utf-8")


def split(settings_path: Path, raw: Path, out: Path) -> dict[str, Any]:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    arm = settings["arms"]["nhtsa"]
    first = min(arm["train"][0], arm["test"][0])
    last = max(arm["train"][1], arm["test"][1])
    blob = (raw / FILE).read_bytes()
    with zipfile.ZipFile(io.BytesIO(blob)):
        pass
    out.mkdir(parents=True, exist_ok=True)
    companies = {}
    for make in arm["companies"]["makes"]:
        rows, counts = nhtsa_export.complaints(nhtsa_export._rows(blob), make, _ymd(first), _ymd(last), _Every(),
                                               REPORTER)
        name = f"{make}.csv"
        (out / name).write_bytes(rows_csv(rows, arm["columns"]))
        companies[make] = {"file": name, **counts}
    downloads = json.loads((raw / "download.json").read_text(encoding="utf-8")) \
        if (raw / "download.json").is_file() else None
    source = {"arm": "nhtsa", "downloads": downloads, "window": [first, last], "makes": len(companies)}
    (out / "companies.json").write_text(json.dumps(companies, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "source.json").write_text(json.dumps(source, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"window": [first, last],
                      "makes": {k: {c: v for c, v in e.items() if c != "file"} for k, e in companies.items()}},
                     sort_keys=True))
    return {"companies": companies, "source": source}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("download", help="fetch the complaint file")
    d.add_argument("--out", required=True)
    s = sub.add_parser("split", help="one export per make")
    s.add_argument("--settings", required=True)
    s.add_argument("--raw", required=True)
    s.add_argument("--out", required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "download":
            download(Path(args.out))
        else:
            split(Path(args.settings), Path(args.raw), Path(args.out))
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as err:
        print(f"error: {err.__class__.__name__}: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""D001's MSHA arm input: the public accident file, split into one export per controller (CHOICE-D001, rule 2.1).

    python tools/onboard/fetch_msha.py download --out DIR
    python tools/onboard/fetch_msha.py split --settings FILE --raw DIR --out DIR

Two steps, so that a download failure is told apart from a run (rule 9):

* ``download`` fetches ``Accidents.zip`` and ``Accidents_Definition_File.txt`` (``docs/collective/onboard/SOURCES.md``)
  and writes them with ``download.json``. It prints each file's size and sha256, nothing else.
* ``split`` reads ``Accidents.txt`` from the zip, checks that every declared column is in its header (a missing one is
  an error; any other difference from the settings' expected columns is reported by name), and applies the company
  rule: the controllers with the most training rows that have a narrative, among those with enough test rows that
  have a narrative and enough distinct mines in their training rows; ties go to the smaller controller id as a string.
  The date column is parsed with ``mycelic.collective.onboard.roles`` (the drafter's own rule). It writes ``c1.txt``
  and onwards, each holding the original header and that controller's original lines, unchanged;
  ``companies.json`` with the counts the rule used and no controller id; ``source.json`` with the download facts and
  counts; and ``definitions.json`` with the definition file's lines for the settings' definition columns. It prints
  counts only.

This is per-field code: the report gives its line count. It never prints a field value.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mycelic.collective.onboard.draft import load_language  # noqa: E402
from mycelic.collective.onboard.exports import choose_delimiter, decode  # noqa: E402
from mycelic.collective.onboard.roles import choose_date_format, parse_date  # noqa: E402

BASE = "https://arlweb.msha.gov/OpenGovernmentData/DataSets"
FILES = (("Accidents.zip", f"{BASE}/Accidents.zip"),
         ("Accidents_Definition_File.txt", f"{BASE}/Accidents_Definition_File.txt"))
MEMBER = "accidents.txt"
UA = "mycelic-onboard-d001 (drafting test D001 on public data)"
LINE = re.compile(r"\r\n|\r|\n")
MAX_DEFINITION_LINES = 8


class SplitError(ValueError):
    pass


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=900) as resp:   # noqa: S310 (fixed https host)
        return resp.read()


def download(out: Path, fetcher: Callable[[str], bytes] = fetch) -> list[dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    facts = []
    for name, url in FILES:
        data = fetcher(url)
        (out / name).write_bytes(data)
        facts.append({"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    (out / "download.json").write_text(json.dumps(facts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for f in facts:
        print(json.dumps(f, sort_keys=True))
    return facts


def lines_with_ends(text: str) -> list[tuple[str, str]]:
    """``(line, its end)`` pairs; a line ends at ``\\r\\n``, ``\\r`` or ``\\n`` as the drafter splits them."""
    out, start = [], 0
    for m in LINE.finditer(text):
        out.append((text[start:m.start()], m.group()))
        start = m.end()
    if start < len(text):
        out.append((text[start:], ""))
    return out


def member_text(blob: bytes) -> tuple[str, str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if n.lower().rsplit("/", 1)[-1] == MEMBER]
        if len(names) != 1:
            raise SplitError("the zip holds no single accident file")
        return decode(z.read(names[0]))


def choose_companies(stats: Mapping[str, Mapping[str, Any]], rule: Mapping[str, Any]) -> list[str]:
    """Rule 2.1: the qualifying controllers (enough test rows with a narrative, enough training mines), ranked by
    training rows with a narrative, ties by the smaller id as a string; at most ``count``."""
    qualifying = [c for c, s in stats.items()
                  if s["test_rows_with_narrative"] >= rule["min_test_rows"]
                  and len(s["training_mines"]) >= rule["min_training_sites"]]
    ranked = sorted(qualifying, key=lambda c: (-stats[c]["training_rows_with_narrative"], c))
    return ranked[:rule["count"]]


def definition_lines(text: str, columns: Sequence[str], known: Sequence[str]) -> dict[str, list[str]]:
    """For each column, the first line naming it as a whole word and the lines after it up to a blank line, a line
    naming another known column, or the cap."""
    lines = [line for line, _ in lines_with_ends(text)]

    def names(line: str, col: str) -> bool:
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(col)}(?![A-Za-z0-9_])", line) is not None

    out = {}
    for col in columns:
        found: list[str] = []
        for i, line in enumerate(lines):
            if names(line, col):
                found.append(line.strip())
                for nxt in lines[i + 1:i + MAX_DEFINITION_LINES]:
                    if not nxt.strip() or any(names(nxt, k) for k in known):
                        break
                    found.append(nxt.strip())
                break
        out[col] = found
    return out


def split(settings_path: Path, raw: Path, out: Path) -> dict[str, Any]:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    arm = settings["arms"]["msha"]
    roles, rule = arm["roles"], arm["companies"]
    lang = load_language(settings["language"])
    text, encoding = member_text((raw / FILES[0][0]).read_bytes())
    lines = lines_with_ends(text)
    while lines and not lines[0][0].strip():
        lines.pop(0)
    if not lines:
        raise SplitError("the accident file is empty")
    header_line, header_end = lines[0]
    delim, _ = choose_delimiter(header_line)
    header = [h.strip() for h in header_line.split(delim)]
    declared = [roles[k] for k in ("record_id", "site", "date", "narrative", "category")] + list(roles["forbidden"])
    declared.append(rule["column"])
    missing = [c for c in declared if c not in header]
    if missing:
        raise SplitError(f"declared columns missing from the header: {', '.join(missing)}")
    at = {c: header.index(c) for c in declared}
    expected = arm["columns"]
    # per row only what the rule reads: (line index, controller, mine, date, has a narrative)
    rows: list[tuple[int, str, str, str, bool]] = []
    rejected: dict[str, int] = {}
    c_at, m_at, d_at, n_at = (at[rule["column"]], at[roles["site"]], at[roles["date"]], at[roles["narrative"]])
    for i, (line, _) in enumerate(lines[1:], start=1):
        if line == "":
            rejected["blank_line"] = rejected.get("blank_line", 0) + 1
            continue
        fields = line.split(delim)
        if len(fields) != len(header):
            rejected["wrong_width"] = rejected.get("wrong_width", 0) + 1
            continue
        rows.append((i, fields[c_at].strip(), fields[m_at].strip(), fields[d_at].strip(), bool(fields[n_at].strip())))
    dates = [r[3] for r in rows if r[3]]
    fmt = choose_date_format(dates, lang.months, settings["params"]["date_min_share"])
    if fmt is None:
        raise SplitError("no date format parses enough of the date column")
    train = [date.fromisoformat(d) for d in arm["train"]]
    test = [date.fromisoformat(d) for d in arm["test"]]
    stats: dict[str, dict[str, Any]] = {}
    lines_of: dict[str, list[int]] = {}
    no_controller = 0
    for i, controller, mine, day_text, has_text in rows:
        if not controller:
            no_controller += 1
            continue
        lines_of.setdefault(controller, []).append(i)
        s = stats.setdefault(controller, {"training_rows_with_narrative": 0, "test_rows_with_narrative": 0,
                                          "training_mines": set()})
        day = parse_date(day_text or None, fmt, lang.months)
        if day is not None and train[0] <= day <= train[1]:
            if mine:
                s["training_mines"].add(mine)
            s["training_rows_with_narrative"] += int(has_text)
        elif day is not None and test[0] <= day <= test[1]:
            s["test_rows_with_narrative"] += int(has_text)
    chosen = choose_companies(stats, rule)
    out.mkdir(parents=True, exist_ok=True)
    companies = {}
    for label, controller in zip(rule["labels"], chosen):
        body = header_line + header_end + "".join(lines[i][0] + lines[i][1] for i in lines_of[controller])
        name = f"{label}.txt"
        (out / name).write_bytes(body.encode(encoding))
        s = stats[controller]
        companies[label] = {"file": name, "rows": len(lines_of[controller]),
                            "training_rows_with_narrative": s["training_rows_with_narrative"],
                            "test_rows_with_narrative": s["test_rows_with_narrative"],
                            "training_mines": len(s["training_mines"])}
    downloads = json.loads((raw / "download.json").read_text(encoding="utf-8")) \
        if (raw / "download.json").is_file() else None
    qualifying = sum(1 for c in stats if c in set(choose_companies(stats, dict(rule, count=len(stats)))))
    source = {"arm": "msha", "downloads": downloads, "member_encoding": encoding, "rows": len(rows),
              "rejected": dict(sorted(rejected.items())), "rows_without_controller": no_controller,
              "date_format": fmt, "controllers": len(stats), "qualifying": qualifying, "used": len(companies),
              "header": {"columns": len(header), "missing_expected": [c for c in expected if c not in header],
                         "unexpected": [c for c in header if c not in expected]}}
    definitions_path = raw / FILES[1][0]
    definitions = definition_lines(decode(definitions_path.read_bytes())[0], arm["definition_columns"], expected) \
        if definitions_path.is_file() else {}
    (out / "companies.json").write_text(json.dumps(companies, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "source.json").write_text(json.dumps(source, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "definitions.json").write_text(json.dumps(definitions, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "rejected": source["rejected"], "controllers": len(stats),
                      "qualifying": qualifying, "used": len(companies),
                      "companies": {k: {c: v for c, v in e.items() if c != "file"} for k, e in companies.items()},
                      "header_columns": len(header), "date_format": fmt}, sort_keys=True))
    return {"companies": companies, "source": source}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("download", help="fetch the accident file and its definitions")
    d.add_argument("--out", required=True)
    s = sub.add_parser("split", help="one export per qualifying controller")
    s.add_argument("--settings", required=True)
    s.add_argument("--raw", required=True)
    s.add_argument("--out", required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "download":
            download(Path(args.out))
        else:
            split(Path(args.settings), Path(args.raw), Path(args.out))
    except (SplitError, OSError, zipfile.BadZipFile, KeyError, ValueError) as err:
        print(f"error: {err.__class__.__name__}: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

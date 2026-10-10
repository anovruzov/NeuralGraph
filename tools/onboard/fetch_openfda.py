#!/usr/bin/env python
"""D003's input: openFDA device adverse-event reports for the makers with the most reports, one export per maker.

    python tools/onboard/fetch_openfda.py fetch --settings FILE --out DIR [--base-url URL] [--dry-run]

The rule is ``docs/collective/onboard/CHOICE-D003.md``, sections 2.1 to 2.6 as amended by E5 and E9 to E13. One
command selects, fetches and splits; the run starts after it has finished and printed its counts (section 12), so a
failure here is not a run.

* **HTTP** (2.1, E9): the pinned connector's ``build_opener`` (the environment's proxy, no redirects) and ``_get``
  (429 and 5xx retried, an unreachable host retried twice), never modified. The opener is wrapped so that every HTTP
  attempt, retries included, is counted; the 801st raises :class:`AttemptCap` before it is sent, which ``_get`` does
  not retry. Each request carries D002's User-Agent. A query is encoded as the connector's ``page_url`` encodes it.
* **The companies** (2.2, E12): the two count lists of ``device.manufacturer_d_name.exact`` (top 100 per window);
  the names in both, in the order of most training-window reports (ties by the name's UTF-8 bytes), less the four
  exclusions in order (a placeholder, not searchable, a variant spelling of a name chosen before it, too few
  reports); the first five are ``d1`` to ``d5``.
* **The exact-name check** (2.3, E10): per company and window, the total of the ``.exact`` search must equal the
  name's count in that window's list; asked once more when it differs; a company that still differs is dropped.
* **The walk** (2.3, E10, E11): the window's days in the order of ``random.Random("d003:days:<window>:<label>")``,
  whole days only, under a page budget and a per-day cap; an incomplete day is fetched once more, then left out.
* **The minimums** (2.3, E11, E12): reports with a narrative and kept days per window; fewer than three companies
  left stops the fetch.
* **The export** (E5): ``d<i>.jsonl``, one object per kept report, sorted by ``mdr_report_key`` (keys of digits by
  value, then the rest as text), with the seven columns in their order and nothing else.

It writes ``companies.json`` and ``source.json`` (counts only, windows written ``YYYY-MM-DD``: the score copies the
second into ``arm.json``) and ``fetch.json`` (the source record: every count, and each export's bytes and sha256).
It prints counts, the kept days with their totals and that list's sha256, and nothing else: never a name, a record
key, a URL, a query, any other value of a record, openFDA's error message, an exception's text or a traceback. An
error is printed by its exception's class name only, with the HTTP status and openFDA's ``error.code`` for an HTTP
error.

This is per-field code (M1): the report gives its line count and that of every module it imports beyond the standard
library and the onboard package (the settings' ``download_code``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote_plus, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "market"))
from mycelic.collective.connectors.openfda import _get, build_opener  # noqa: E402
from mycelic.collective.experiments.n1_narratives import narrative  # noqa: E402
from mycelic.collective.packs.canonical import folded  # noqa: E402
from openfda_establishments import normalise_name  # noqa: E402

ARM = "maude"
WINDOWS = ("train", "test")
EXCLUSIONS = ("placeholder", "not_searchable", "variant", "too_few")
INCOMPLETE = ("keys", "day", "company")
_ERROR_CODE_RE = re.compile(r"[A-Z_]{1,40}", re.ASCII)
_LETTER = re.compile(r"[^\W\d_]")
_DIGITS = re.compile(r"[0-9]+", re.ASCII)


class FetchStop(Exception):
    """A stated stop before the run starts (section 2.6 as amended by E13); its text is the fetch's own words."""


class AttemptCap(Exception):
    """E9: the next HTTP attempt would be past the cap. Not an ``OSError`` or ``HTTPException``: ``_get`` does not
    retry it."""


class FetchHTTPError(Exception):
    """An HTTP answer the fetch cannot use: its status and openFDA's ``error.code`` only, never ``error.message``."""

    def __init__(self, status: int, code: str | None) -> None:
        super().__init__(f"HTTP {status}, error code {code}")
        self.status = status
        self.code = code


# --------------------------------------------------------------------------------------------------- HTTP (E9)

class CountingOpener:
    """The connector's opener, counting every HTTP attempt (``_get`` retries call ``open`` again) and refusing the
    one past ``cap`` before it is sent."""

    def __init__(self, inner: Any, cap: int) -> None:
        self.inner = inner
        self.cap = cap
        self.attempts = 0

    def open(self, url: str, *args: Any, **kwargs: Any) -> Any:
        if self.attempts >= self.cap:
            raise AttemptCap()
        self.attempts += 1
        return self.inner.open(url, *args, **kwargs)


class Client:
    """``_get`` through the counting opener; counts the calls. :meth:`query` returns the parsed body of a 200, or
    None for openFDA's 404 ``NOT_FOUND`` (no report); any other answer raises :class:`FetchHTTPError`."""

    def __init__(self, base_url: str, user_agent: str, cap: int, opener: Any = None,
                 sleep: Callable[[float], Any] = time.sleep) -> None:
        parts = urlsplit(base_url)
        self.base = base_url.rstrip("/")
        self.origin = f"{parts.scheme}://{parts.netloc}"
        inner = opener if opener is not None else build_opener(self.base)
        inner.addheaders = [("User-Agent", user_agent)]
        self.opener = CountingOpener(inner, cap)
        self.sleep = sleep
        self.calls = 0

    def url(self, endpoint: str, params: Sequence[tuple[str, str]]) -> str:
        """As the connector's ``page_url`` encodes a query: ``urlencode`` with ``quote_plus``."""
        return f"{self.base}/{endpoint}.json?{urlencode(list(params), quote_via=quote_plus)}"

    def query(self, endpoint: str, params: Sequence[tuple[str, str]]) -> dict[str, Any] | None:
        self.calls += 1
        status, body = _get(self.opener, self.url(endpoint, params), self.origin, self.sleep)
        if status == 200:
            doc = json.loads(body)
            if not isinstance(doc, dict):
                raise ValueError()
            return doc
        code = _error_code(body)
        if status == 404 and code == "NOT_FOUND":
            return None
        raise FetchHTTPError(status, code)


def _error_code(body: bytes) -> str | None:
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError):
        return None
    err = doc.get("error") if isinstance(doc, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE_RE.fullmatch(code) else None


def _total(doc: Mapping[str, Any] | None) -> int:
    if doc is None:
        return 0
    total = doc["meta"]["results"]["total"]
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError()
    return total


def _results(doc: Mapping[str, Any] | None) -> list[Any]:
    if doc is None:
        return []
    results = doc["results"]
    if not isinstance(results, list):
        raise ValueError()
    return results


# --------------------------------------------------------------------------------------------------- 2.2, E12

def ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


def window_search(first: date, last: date) -> str:
    """E12: dates written ``YYYYMMDD``, as the connector writes them."""
    return f"date_received:[{ymd(first)} TO {ymd(last)}]"


def name_search(field: str, name: str, first: date, last: date) -> str:
    return f'{field}.exact:"{name}" AND {window_search(first, last)}'


def count_list(client: Client, endpoint: str, field: str, first: date, last: date, limit: int) -> dict[str, int]:
    """One count request: the names with the most reports in the window, with their counts. Kept in memory only."""
    doc = client.query(endpoint, [("search", window_search(first, last)), ("count", f"{field}.exact"),
                                  ("limit", str(limit))])
    out: dict[str, int] = {}
    for row in _results(doc):
        term, count = row["term"], row["count"]
        if not isinstance(term, str) or isinstance(count, bool) or not isinstance(count, int):
            raise ValueError()
        out[term] = count
    return out


def placeholder(name: str, placeholders: Iterable[str]) -> bool:
    """Exclusion 1: the folded name (``packs.canonical.folded``) equals a placeholder value, or holds no letter."""
    return folded(name) in {folded(p) for p in placeholders} or _LETTER.search(name) is None


def searchable(name: str, max_chars: int) -> bool:
    """Exclusion 2: no double quote, backslash or control character, and at most ``max_chars`` characters."""
    return (len(name) <= max_chars and '"' not in name and "\\" not in name
            and not any(unicodedata.category(ch) == "Cc" for ch in name))


def choose(train: Mapping[str, int], test: Mapping[str, int], rule: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Section 2.2 with E12: the candidates (names in both lists) in order of most training-window reports, ties by
    the name's UTF-8 bytes; each excluded by the first of the four exclusions it meets, the variant test against the
    names that qualified before it; the first ``count`` qualifying names are chosen. Also the counts it prints."""
    candidates = sorted((n for n in train if n in test), key=lambda n: (-train[n], n.encode("utf-8")))
    excluded = dict.fromkeys(EXCLUSIONS, 0)
    qualifying: list[str] = []
    seen: set[str] = set()
    for n in candidates:
        if placeholder(n, rule["placeholders"]):
            reason = "placeholder"
        elif not searchable(n, rule["max_name_chars"]):
            reason = "not_searchable"
        elif normalise_name(n) in seen:
            reason = "variant"
        elif train[n] < rule["min_train_reports"] or test[n] < rule["min_test_reports"]:
            reason = "too_few"
        else:
            qualifying.append(n)
            seen.add(normalise_name(n))
            continue
        excluded[reason] += 1
    chosen = qualifying[:rule["count"]]
    return chosen, {"train_names": len(train), "test_names": len(test), "candidates": len(candidates),
                    "excluded": excluded, "qualifying": len(qualifying), "chosen": len(chosen)}


# --------------------------------------------------------------------------------------------------- 2.3, E10, E11

def window_days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def day_order(first: date, last: date, seed: str) -> list[date]:
    """Step 1: the window's days sorted by date, shuffled by ``random.Random(seed)``."""
    days = window_days(first, last)
    random.Random(seed).shuffle(days)
    return days


def names_company(record: Any, name: str) -> bool:
    return isinstance(record, dict) and any(isinstance(d, dict) and d.get("manufacturer_d_name") == name
                                            for d in record.get("device") or [])


def other_maker(record: Mapping[str, Any], name: str) -> bool:
    """E12: a device entry whose name is present, not blank after stripping, and not the company's exact name."""
    for d in record.get("device") or []:
        if isinstance(d, dict):
            v = d.get("manufacturer_d_name")
            if isinstance(v, str) and v.strip() and v != name:
                return True
    return False


def day_check(records: Sequence[Any], total: int, day: str, name: str) -> tuple[str | None, int, int]:
    """Step 4 as E10 has it: (the reason the day is incomplete or None, duplicate keys, missing keys)."""
    keys = [r.get("mdr_report_key") if isinstance(r, dict) else None for r in records]
    distinct = {k for k in keys if isinstance(k, str)}
    duplicates = len(keys) - len(set(keys))
    missing = max(total - len(distinct), 0)
    if len(distinct) != total or duplicates or any(not isinstance(k, str) for k in keys):
        return "keys", duplicates, missing
    if any(r.get("date_received") != day for r in records):
        return "day", duplicates, missing
    if any(not names_company(r, name) for r in records):
        return "company", duplicates, missing
    return None, duplicates, missing


class Walk:
    """One company's walk over one window (steps 1 to 4, E10, E11): its kept records by day and its counts."""

    def __init__(self, client: Client, endpoint: str, field: str, name: str, budget: int, cap: int,
                 limit: int, refetches: int) -> None:
        self.client, self.endpoint, self.field, self.name = client, endpoint, field, name
        self.budget, self.cap, self.limit, self.refetches = budget, cap, limit, refetches
        self.kept: dict[str, tuple[int, list[dict[str, Any]]]] = {}
        self.counts = {"days_walked": 0, "days_kept": 0, "too_large_cap": 0, "too_large_budget": 0, "empty": 0,
                       "refetched": 0, "incomplete": dict.fromkeys(INCOMPLETE, 0), "duplicate_keys": 0,
                       "missing_keys": 0, "pages": 0}

    def page(self, day: date, skip: int) -> dict[str, Any] | None:
        self.budget -= 1
        self.counts["pages"] += 1
        return self.client.query(self.endpoint, [("search", name_search(self.field, self.name, day, day)),
                                                 ("limit", str(self.limit)), ("skip", str(skip))])

    def fetch_day(self, day: date) -> tuple[str, int, list[Any]]:
        """One fetch of a day: ("empty" | "too_large_cap" | "too_large_budget" | "fetched", T, records)."""
        first = self.page(day, 0)
        if first is None:
            return "empty", 0, []
        total = _total(first)
        if total == 0:
            return "empty", 0, []
        pages = math.ceil(total / self.limit)
        if pages > self.cap:
            return "too_large_cap", total, []
        if pages - 1 > self.budget:
            return "too_large_budget", total, []
        records = list(_results(first))
        for k in range(1, pages):
            records += _results(self.page(day, k * self.limit))
        return "fetched", total, records

    def run(self, days: Sequence[date]) -> None:
        for day in days:
            if self.budget <= 0:
                break
            self.counts["days_walked"] += 1
            outcome, total, records = self.fetch_day(day)
            if outcome != "fetched":
                self.counts["empty" if outcome == "empty" else outcome] += 1
                continue
            reason, dup, miss = day_check(records, total, ymd(day), self.name)
            self.counts["duplicate_keys"] += dup
            self.counts["missing_keys"] += miss
            tries = 0
            while reason is not None and tries < self.refetches and self.budget >= math.ceil(total / self.limit):
                tries += 1
                self.counts["refetched"] += 1
                outcome, total, records = self.fetch_day(day)
                if outcome != "fetched":
                    reason = "keys"
                    break
                reason, dup, miss = day_check(records, total, ymd(day), self.name)
                self.counts["duplicate_keys"] += dup
                self.counts["missing_keys"] += miss
            if reason is not None:
                self.counts["incomplete"][reason] += 1
                continue
            self.kept[ymd(day)] = (total, records)
            self.counts["days_kept"] += 1

    def kept_days(self) -> tuple[list[str], str]:
        """E11: each kept day as ``YYYYMMDD T``, sorted by date, and the sha256 of those lines joined by a newline."""
        lines = [f"{d} {self.kept[d][0]}" for d in sorted(self.kept)]
        return lines, hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------------- E5

def key_order(key: str) -> tuple[int, int, str]:
    """Record keys of digits by value first, then the rest as text."""
    return (0, int(key), key) if _DIGITS.fullmatch(key) else (1, 0, key)


def export_row(record: Mapping[str, Any], day: str, label: str, makers: Sequence[str], text_type: str,
               columns: Sequence[str]) -> dict[str, Any]:
    """E5: the seven columns, in the settings' order, and nothing else; ``mdr_text`` absent when no description
    entry holds text (section 4, ``n1_narratives.narrative`` with that one type)."""
    values: dict[str, Any] = {
        columns[0]: record["mdr_report_key"],
        columns[1]: record["date_received"],
        columns[2]: [p for p in record.get("product_problems") or [] if isinstance(p, str)],
        columns[3]: narrative(record, [text_type]) or None,
        columns[4]: day,
        columns[5]: label.upper(),
        columns[6]: list(makers),
    }
    return {k: v for k, v in values.items() if v is not None}


def write_export(rows: Sequence[Mapping[str, Any]], path: Path) -> tuple[int, str]:
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    path.write_bytes(data)
    return len(data), hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------------------- the fetch

def emit(printer: Callable[[str], None], what: str, payload: Any) -> None:
    printer(json.dumps({what: payload}, sort_keys=True, ensure_ascii=True))


def fetch(settings: Mapping[str, Any], out: Path, *, base_url: str | None = None, opener: Any = None,
          sleep: Callable[[float], Any] = time.sleep, printer: Callable[[str], None] = print) -> dict[str, Any]:
    arm = settings["arms"][ARM]
    rule, f = arm["companies"], arm["fetch"]
    columns = arm["columns"]
    windows = {w: (date.fromisoformat(arm[w][0]), date.fromisoformat(arm[w][1])) for w in WINDOWS}
    client = Client(base_url or f["base_url"], f["user_agent"], f["attempt_cap"], opener=opener, sleep=sleep)
    endpoint, field = f["endpoint"], rule["field"]
    record: dict[str, Any] = {"windows": {w: [a.isoformat(), b.isoformat()] for w, (a, b) in windows.items()}}
    try:
        lists = {w: count_list(client, endpoint, field, *windows[w], rule["count_limit"]) for w in WINDOWS}
        chosen, selection = choose(lists["train"], lists["test"], rule)
        record["selection"] = selection
        emit(printer, "selection", selection)
        if len(chosen) < rule["min_companies"]:
            raise FetchStop(f"{len(chosen)} companies qualify, fewer than {rule['min_companies']}")
        labels = dict(zip(rule["labels"], chosen))
        per: dict[str, dict[str, Any]] = {}
        for label, name in labels.items():
            entry: dict[str, Any] = {"counts": {w: lists[w][name] for w in WINDOWS}, "exact_check": "passed"}
            for w in WINDOWS:
                for attempt in range(1 + f["exact_check_repeats"]):
                    total = _total(client.query(endpoint, [("search", name_search(field, name, *windows[w])),
                                                           ("limit", "1")]))
                    if total == lists[w][name]:
                        if attempt:
                            entry["exact_check"] = "passed_on_repeat"
                        break
                else:
                    entry["exact_check"] = "failed"
                if entry["exact_check"] == "failed":
                    break
            per[label] = entry
            emit(printer, "company", {"label": label, "counts": entry["counts"], "exact_check": entry["exact_check"]})
        walkable = [lbl for lbl in labels if per[lbl]["exact_check"] != "failed"]
        for lbl in labels:
            if lbl not in walkable:
                per[lbl]["used"], per[lbl]["not_used"] = False, "exact_check"
                emit(printer, "export", {"label": lbl, "bytes": None, "sha256": None, "used": False,
                                         "not_used": "exact_check"})
        if len(walkable) < rule["min_companies"]:
            raise FetchStop(f"{len(walkable)} companies pass the exact-name check, fewer than {rule['min_companies']}")
        makers = list(chosen)
        out.mkdir(parents=True, exist_ok=True)
        for label in walkable:
            name = labels[label]
            rows: list[dict[str, Any]] = []
            entry = per[label]
            entry["windows"] = {}
            for w in WINDOWS:
                walk = Walk(client, endpoint, field, name, f["budgets"][w], f["day_caps"][w], f["page_limit"],
                            f["day_refetches"])
                walk.run(day_order(*windows[w], f"{f['days_seed_prefix']}:{w}:{label}"))
                kept, dropped = 0, 0
                window_rows = []
                for day in sorted(walk.kept):
                    for rec in walk.kept[day][1]:
                        if other_maker(rec, name):
                            dropped += 1
                            continue
                        window_rows.append(export_row(rec, day, label, makers, f["text_type"], columns))
                        kept += 1
                with_text = sum(1 for r in window_rows if columns[3] in r)
                lines, digest = walk.kept_days()
                entry["windows"][w] = {**walk.counts, "reports_kept": kept, "with_narrative": with_text,
                                       "other_maker_dropped": dropped, "kept_days_sha256": digest}
                emit(printer, "walk", {"label": label, "window": w, **entry["windows"][w]})
                emit(printer, "kept_days", {"label": label, "window": w, "days": lines, "sha256": digest})
                rows += window_rows
            rows.sort(key=lambda r: key_order(r[columns[0]]))
            entry["file"] = f"{label}.jsonl"
            entry["bytes"], entry["sha256"] = write_export(rows, out / entry["file"])
            ws = entry["windows"]
            short = [w for w in WINDOWS if ws[w]["with_narrative"] < f["min_reports"][w]]
            few_days = [w for w in WINDOWS if ws[w]["days_kept"] < f["min_days"]]
            entry["used"] = not short and not few_days
            if not entry["used"]:
                entry["not_used"] = "report_minimum" if short else "day_minimum"
            emit(printer, "export", {"label": label, "bytes": entry["bytes"], "sha256": entry["sha256"],
                                     "used": entry["used"], "not_used": entry.get("not_used")})
        used = [lbl for lbl in labels if per[lbl].get("used")]
        record["companies"] = per
        if len(used) < rule["min_companies"]:
            raise FetchStop(f"{len(used)} companies remain, fewer than {rule['min_companies']}")
        companies = {lbl: {"file": per[lbl]["file"], "list_train": per[lbl]["counts"]["train"],
                           "list_test": per[lbl]["counts"]["test"],
                           **{f"kept_days_{w}": per[lbl]["windows"][w]["days_kept"] for w in WINDOWS},
                           **{f"reports_{w}": per[lbl]["windows"][w]["reports_kept"] for w in WINDOWS},
                           **{f"with_narrative_{w}": per[lbl]["windows"][w]["with_narrative"] for w in WINDOWS}}
                     for lbl in used}
        source = {"arm": ARM, "windows": record["windows"], "selection": selection, "used": len(used),
                  "not_used": {lbl: per[lbl]["not_used"] for lbl in labels if not per[lbl].get("used")}}
        (out / "companies.json").write_text(json.dumps(companies, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        (out / "source.json").write_text(json.dumps(source, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        record["used"] = used
        return record
    finally:
        record["requests"] = {"calls": client.calls, "attempts": client.opener.attempts}
        emit(printer, "requests", record["requests"])
        if out.is_dir():
            (out / "fetch.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def describe(err: BaseException) -> str:
    """E13: an error by its class name only; an HTTP error also by its status and openFDA's ``error.code``."""
    if isinstance(err, FetchHTTPError):
        return f"{err.__class__.__name__} (HTTP {err.status}, error code {err.code})"
    if isinstance(err, FetchStop):
        return f"{err.__class__.__name__}: {err}"
    return err.__class__.__name__


def main(argv: Sequence[str] | None = None, *, sleep: Callable[[float], Any] = time.sleep) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("fetch", help="select the companies, fetch their days and write one export each")
    g.add_argument("--settings", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--base-url", help="another openFDA-shaped host (the tests' local server)")
    g.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    try:
        settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
        if args.dry_run:
            f = settings["arms"][ARM]["fetch"]
            host = urlsplit(args.base_url or f["base_url"]).hostname
            print("dry-run: fetch_openfda fetch")
            print(f"would need: network {host}")
            print(f"would write: {Path(args.out) / 'd<i>.jsonl'}, companies.json, source.json, fetch.json")
            return 0
        record = fetch(settings, Path(args.out), base_url=args.base_url, sleep=sleep)
    except FetchStop as err:
        print(f"fetch stopped before the run: {describe(err)}", file=sys.stderr)
        return 1
    except Exception as err:   # noqa: BLE001 (E13: every exception, by its class name only)
        print(f"fetch stopped before the run: {describe(err)}", file=sys.stderr)
        return 2
    print(json.dumps({"used": len(record["used"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

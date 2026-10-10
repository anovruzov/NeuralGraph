"""Latency test L001's guard (rule 12 and K9): every file a run uploads is scanned for MSHA values before any summary
reads it and before upload.

    python -m lab.l1 guard --plan PLAN --dir DIR [--dir DIR ...] [--raw RAW]

The scan is D002's last guard and backstop (``mycelic.collective.onboard.report``) over whole files, with the values of
c1 to c5 (the operators of D002's split of the file, :func:`build`):

* ``record_id`` and ``mine_id``: the backstop's values (``report.Backstop``: every record id and site value of at least
  ``reference_inside_min_chars`` characters, folded, found as whole tokens), over every operator's export;
* ``refused_value``: every value of a refused column of every operator's export, folded, of at least four characters
  that holds a letter or of at least five (the lengths of D002's refusal), found as whole words (``draft.ValueIndex``);
* ``narrative_ngrams``: any run of ``report_guard.ngram`` (8) words of one of c1's narratives (``check.ngrams``).

A file with any hit is withheld: its bytes are replaced by :func:`stub` (its hit counts by kind, nothing else) and the
command exits 1, which fails the job. A stub is never scanned again. When the file cannot be split or an export of the
split cannot be read, nothing can be checked, so every scanned file is withheld (``unread``). Nothing is exempt: a hit
on a number is a hit (K12). The directories under a shard root that the seal removes and never uploads (``work/``,
``server/bin``, ``server/home``, ``server/tmp``) are not scanned.

The console gets fixed lines with counts only: one per directory (``l1 guard: <name> files <n> withheld <n> ...``), and
for an error its class name and code location (:func:`error_line`), never its text.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective.jsonio import canonical_dumps, strict_load
from mycelic.collective.onboard import draft as D
from mycelic.collective.onboard import report as R
from mycelic.collective.onboard import score as S
from mycelic.collective.onboard.check import ngrams
from mycelic.collective.onboard.exports import read_export
from mycelic.collective.packs.canonical import folded

from . import ROOT
from .l1path import ARM, COMPANY, FETCH, SETTINGS, load_script

KINDS = ("record_id", "mine_id", "refused_value", "narrative_ngrams", "unread")
STUB_KIND = "lab_l1_withheld"
SKIP_DIRS = ("work", "server/bin", "server/home", "server/tmp")


def error_line(prefix: str, err: BaseException) -> str:
    """K9: an error as its class name and code location (the innermost frame of this repository), never its text."""
    frames = traceback.extract_tb(err.__traceback__)
    where = "unknown"
    for frame in reversed(frames):
        path = Path(frame.filename)
        try:
            rel = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        where = f"{rel}:{frame.lineno}"
        break
    return f"{prefix}: {err.__class__.__name__} at {where}"


def stub(hits: Mapping[str, int]) -> bytes:
    """What a withheld file holds: its hit counts by kind."""
    return (canonical_dumps({"schema_version": 1, "kind": STUB_KIND,
                             "hits": {k: int(hits.get(k, 0)) for k in KINDS}}) + "\n").encode("utf-8")


def is_stub(data: bytes) -> bool:
    if len(data) > 400 or not data.startswith(b"{"):
        return False
    try:
        doc = strict_load(data.strip())
    except Exception:            # noqa: BLE001 (any parse failure: not a stub)
        return False
    return isinstance(doc, dict) and doc.get("kind") == STUB_KIND


class Guard:
    """The values of the operators of the split (see the module docstring), built once; :meth:`hits` scans a text."""

    def __init__(self, settings: Mapping[str, Any], exports: Sequence[Any], own: Any) -> None:
        roles = S.arm_roles(settings, ARM)
        params = settings["params"]
        refusal = params["refusal"]
        self.backstop = R.Backstop(refusal["reference_inside_min_chars"])
        values: set[str] = set()
        for export in exports:
            self.backstop.add(export, roles)
            _, forbidden = D.refused_values(export, roles)
            for v in forbidden:
                f = folded(v)
                if (len(f) >= refusal["forbidden_inside_min_chars"] and any(ch.isalpha() for ch in f)) \
                        or len(f) >= refusal["reference_inside_min_chars"]:
                    values.add(f)
        self.refused = D.ValueIndex(values)
        self.ngram = int(settings["report_guard"]["ngram"])
        self.grams: set[tuple[str, ...]] = set()
        for narrative in R.company_sentinels(own, roles, params).narratives:
            self.grams |= ngrams(narrative, self.ngram)
        self.counts = {"record_ids": len(self.backstop.values["report_record_id"]),
                       "mine_ids": len(self.backstop.values["report_site"]), "refused_values": len(values),
                       "operators": len(exports)}

    def hits(self, text: str) -> dict[str, int]:
        found = self.backstop.hits([text])
        return {"record_id": found["report_record_id"], "mine_id": found["report_site"],
                "refused_value": len(self.refused.found_all(folded(text))),
                "narrative_ngrams": len(ngrams(text, self.ngram) & self.grams), "unread": 0}


def build(raw: Path, settings_path: Path | None = None) -> Guard | None:
    """The guard from the file in ``raw`` (D002's split into a temporary directory that is deleted), or None when the
    file cannot be split or an export cannot be read: then nothing can be checked."""
    settings_path = settings_path or ROOT / SETTINGS
    settings, _ = S.load_settings(settings_path)
    fetch = load_script(FETCH)
    with tempfile.TemporaryDirectory(prefix="lab-l1-guard-") as tmp:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                result = fetch.split(settings_path, raw, Path(tmp))
            companies = result["companies"]
            exports = [read_export(Path(tmp) / companies[label]["file"])
                       for label in sorted(companies, key=lambda c: int(c[1:]))]
            own = read_export(Path(tmp) / companies[COMPANY]["file"])
        except Exception:        # noqa: BLE001 (an unreadable split is no guard: every file is withheld)
            return None
    return Guard(settings, exports, own)


def files(root: Path) -> list[Path]:
    """Every regular file under ``root`` the run uploads (symlinks not followed; :data:`SKIP_DIRS` left out)."""
    out = []
    for directory, dirnames, filenames in os.walk(root):
        rel_dir = Path(directory).relative_to(root).as_posix()
        dirnames[:] = sorted(d for d in dirnames
                             if (f"{rel_dir}/{d}" if rel_dir != "." else d) not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(directory) / name
            if not path.is_symlink() and path.is_file():
                out.append(path)
    return out


def scan_dir(root: Path, guard: Guard | None) -> dict[str, Any]:
    """Scan every file under ``root``; withhold each with a hit (or every one without a guard). The counts."""
    total = dict.fromkeys(KINDS, 0)
    scanned = withheld = 0
    for path in files(root):
        data = path.read_bytes()
        if is_stub(data):
            continue
        scanned += 1
        found = guard.hits(data.decode("utf-8", errors="replace")) if guard is not None else {
            **dict.fromkeys(KINDS, 0), "unread": 1}
        if any(found.values()):
            path.write_bytes(stub(found))
            withheld += 1
            for k in KINDS:
                total[k] += found[k]
    return {"files": scanned, "withheld": withheld, "hits": total}


def guard_line(name: str, result: Mapping[str, Any]) -> str:
    hits = " ".join(f"{k} {result['hits'][k]}" for k in KINDS)
    return f"l1 guard: {name} files {result['files']} withheld {result['withheld']} {hits}"


def run(dirs: Iterable[Path], raw: Path, *, fetcher: Any = None) -> int:
    """The guard over each directory (see the module docstring): 0 when nothing was withheld, else 1. The file is
    fetched into ``raw`` when it is not there (``fetcher`` replaces the download in tests)."""
    zip_path = raw / "Accidents.zip"
    if not zip_path.is_file():
        try:
            fetch = load_script(FETCH)
            with contextlib.redirect_stdout(io.StringIO()):
                fetch.download(raw, **({"fetcher": fetcher} if fetcher is not None else {}))
        except Exception as err:   # noqa: BLE001 (a failed fetch leaves no guard: every file is withheld)
            print(error_line("l1 guard: fetch", err), flush=True)
    guard = build(raw) if zip_path.is_file() else None
    if guard is None:
        print("l1 guard: the file could not be split or read; every file is withheld", flush=True)
    code = 0
    for root in dirs:
        if not root.is_dir():
            continue
        result = scan_dir(root, guard)
        print(guard_line(root.name, result), flush=True)
        code = 1 if result["withheld"] else code
    return code


def main_guard(dirs: Sequence[str], raw: str) -> int:
    try:
        return run([Path(d) for d in dirs], Path(raw))
    except Exception as err:       # noqa: BLE001 (K9: class and location only)
        print(error_line("l1 guard", err), file=sys.stderr, flush=True)
        return 2

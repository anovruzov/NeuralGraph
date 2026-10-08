"""Markdown summaries of a lab plan, one shard or the aggregate report, in which every number is read from a run file.

    python -m lab.summary plan|shard|report --dir DIR [--append-to FILE] [--md-out FILE] [--sources-out FILE]

``plan`` reads ``DIR/plan-error.json``, ``DIR/plan-nothing.json`` or ``DIR/plan.json``; ``shard`` reads a shard
root's ``status.json``, ``provenance.json`` and ``units/*/unit.json``; ``report`` reads ``DIR/report.json`` only. A
missing ``DIR`` or file is rendered as a fixed sentence, never an error. ``--append-to`` appends the Markdown (the
job's ``GITHUB_STEP_SUMMARY``), ``--md-out`` writes it, ``--sources-out`` writes where each number came from::

    {"schema_version": 1, "kind": "lab_summary_sources", "mode", "sources": [{"text", "file", "pointer", "style"}]}

in the order the numbers appear: ``file`` is relative to ``DIR``, ``pointer`` an RFC 6901 JSON pointer into it and
``STYLES[style](value) == text`` (``min1`` shows seconds as minutes). Nothing else in a summary is a number: every
identifier, path, hash, date, version, CPU name, step name and reason goes into a code span (:func:`code`), the
request's purpose only into a code span of its own paragraph, booleans are the words yes and no, and every fixed
sentence comes from ``notes`` (which holds no digit). A value that is missing renders ``n/a`` and one of the wrong type
``invalid``; neither is a source.

A report's class sections hold, for its sim rows, a channels table (each channel key's meaning listed under it), a
lifts table and a pushdown table; after the class sections, a sizing table of every sim unit (whatever its result)
with what it measured and the minutes it suggests for the next request; the notes add the sim world-digest groups and
the sim notes the rows carry.

The first line says what the numbers are not: ``PLUMBING CHECK: no model was run`` for a plumbing plan, shard or
report, ``NO MEASUREMENT: ...`` for a real shard or report without a unit of display class ``model``
(``units.display_class``); otherwise (a model unit present, or the class unknown: no provenance, plan or report) the
summary starts with its heading. That first line is always kept. After it a
summary holds whole rows only, while its size plus the next row stays under the cap less :data:`RESERVE_BYTES` (the
cap is :data:`MAX_SUMMARY_BYTES`; GitHub takes at most 1 MiB per step); the first row that does not fit is replaced
by the truncation sentence, which ends the summary. The sentence holds no count: totals are elsewhere, sourced.

Exit 2 for a bad argument, an output that cannot be written or an output inside ``mycelic/``, ``research/``,
``NeuralGraph/`` or ``.github/``; else 0. Nothing is printed on stdout.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from mycelic.collective.jsonio import StrictJsonError, strict_load

from . import EXIT_OK, EXIT_USAGE, forbidden_root
from .notes import (BRANCH_DELETED, COLUMNS, CPU_MODELS_DIFFER, DEFAULT_BRANCH, DELETE_ONLY, DISPATCH_BY_HAND,
                    HEADINGS, LOCK_CONFLICT_NOTE, LOCK_NEW, LOCK_NOT_COMPUTED, LOCK_UNCHANGED, MERGE_SEVERAL,
                    NO_MEASUREMENT_LINE, NO_PLAN, NO_REPORT, NOT_A_BRANCH, NOT_PINNED, NOTES, PLAN_FIX_HINT,
                    PLUMBING_CHECK_LINE, SIM_CHANNEL_LABELS, SIM_LIFT_LABELS, SIM_NOTES, SIM_WORLD_DIFFERS,
                    SIM_WORLD_SAME, SIZING_NOTE, TRUNCATED, UNSEALED, WORLD_DIGEST_DIFFERS, WORLD_DIGEST_SAME)
from .units import display_class

MAX_SUMMARY_BYTES = 900_000
RESERVE_BYTES = 4096
MODES = ("plan", "shard", "report")
SHA_SHOWN = 12
STYLES: dict[str, Callable[[Any], str]] = {
    "int": str,
    "f1": "{:.1f}".format,
    "f3": "{:.3f}".format,
    "gib1": lambda v: "{:.1f}".format(v / 2 ** 30),
    "min1": lambda v: "{:.1f}".format(v / 60),
}
KNOWN_NOTICES = (DELETE_ONLY, BRANCH_DELETED, DEFAULT_BRANCH, NOT_A_BRANCH, MERGE_SEVERAL)
LOCK_SENTENCES = {"unchanged": LOCK_UNCHANGED, "new_entries": LOCK_NEW, "conflict": LOCK_CONFLICT_NOTE,
                  "not_computed": LOCK_NOT_COMPUTED}
CLASS_ORDER = ("model", "unverified", "plumbing", "no-model")
_INDEX_RE = re.compile(r"0|[1-9][0-9]*", re.ASCII)
_BACKTICKS_RE = re.compile(r"`+")


# --------------------------------------------------------------------------------------------------- values

def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901: ``""`` is the document; ``~1`` is ``/`` and ``~0`` is ``~``; list indexes are decimal without
    leading zeros. Raises LookupError when the pointer does not resolve."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise LookupError(pointer)
    node = doc
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and _INDEX_RE.fullmatch(token) and int(token) < len(node):
            node = node[int(token)]
        else:
            raise LookupError(pointer)
    return node


def pointer(*parts: Any) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


class Sources:
    """The files under one root and the numbers read from them, in the order they are rendered. :meth:`num` puts
    its entry on a scratch list; :meth:`take` hands the scratch list to the row being added, so a row that is not
    rendered leaves no source behind."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._docs: dict[str, Any] = {}
        self._scratch: list[dict[str, Any]] = []
        self.entries: list[dict[str, Any]] = []

    def doc(self, rel: str) -> Any:
        if rel not in self._docs:
            failed = False
            try:
                obj = strict_load((self.root / rel).read_bytes())
            except (OSError, StrictJsonError):
                failed = True
            self._docs[rel] = None if failed else obj
        return self._docs[rel]

    def num(self, rel: str, ptr: str, style: str) -> str:
        doc = self.doc(rel)
        try:
            value = resolve_pointer(doc, ptr) if doc is not None else None
        except LookupError:
            value = None
        if value is None:
            return "n/a"
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or (style == "int" and not isinstance(value, int))):
            return "invalid"
        text = STYLES[style](value)
        self._scratch.append({"text": text, "file": rel, "pointer": ptr, "style": style})
        return text

    def take(self) -> list[dict[str, Any]]:
        taken, self._scratch = self._scratch, []
        return taken


def code(text: Any, *, table: bool = False) -> str:
    """An inline code span: a fence one backtick longer than the longest backtick run inside, a space of padding on
    both sides when the text starts or ends with a backtick or a space, non-printable characters as U+FFFD and, in a
    table cell, ``|`` as ``\\|``. No text is the plain word ``n/a``."""
    text = "" if text is None else str(text)
    if text == "":
        return "n/a"
    text = "".join(c if c.isprintable() else "�" for c in text)
    fence = "`" * (max((len(m) for m in _BACKTICKS_RE.findall(text)), default=0) + 1)
    pad = " " if (text[0] in "` " or text[-1] in "` ") and text.strip(" ") != "" else ""
    if table:
        text = text.replace("|", "\\|")
    return f"{fence}{pad}{text}{pad}{fence}"


def yes_no(value: Any) -> str:
    return "yes" if value is True else "no" if value is False else "n/a"


def short(sha: Any) -> str | None:
    return sha[:SHA_SHOWN] if isinstance(sha, str) else None


def _get(obj: Any, *keys: Any) -> Any:
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and 0 <= key < len(obj):
            obj = obj[key]
        else:
            return None
    return obj


# --------------------------------------------------------------------------------------------------- the document

class _Doc:
    """Whole rows under a byte cap. A row is one or more lines; its sources are those taken since the last row."""

    def __init__(self, sources: Sources, cap: int) -> None:
        self.sources = sources
        self.cap = cap
        self.lines: list[str] = []
        self.size = 0
        self.full = False

    def add(self, text: str, *, keep: bool = False) -> bool:
        """Add a row unless the summary is full; ``keep`` (the first line only) adds it whatever the size."""
        entries = self.sources.take()
        if self.full:
            return False
        size = len(text.encode("utf-8")) + 1
        if not keep and self.size + size >= self.cap - RESERVE_BYTES:
            self.full = True
            self.lines += ["", TRUNCATED]          # the blank line ends a table, so the sentence is not a table row
            self.size += len(TRUNCATED.encode("utf-8")) + 2
            return False
        self.lines.append(text)
        self.size += size
        self.sources.entries += entries
        return True

    def table(self, columns: list[str], rows: Callable[[], Any]) -> None:
        """A blank line, the header row, then each row the generator yields (a list of cell texts)."""
        if not self.add("\n| " + " | ".join(COLUMNS[c] for c in columns) + " |\n|" + " --- |" * len(columns)):
            return
        for cells in rows():
            if not self.add("| " + " | ".join(cells) + " |"):
                return

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _first_line(doc: _Doc, plumbing: bool, measured: bool | None) -> None:
    if plumbing:
        doc.add(PLUMBING_CHECK_LINE, keep=True)
    elif measured is False:
        doc.add(NO_MEASUREMENT_LINE, keep=True)


def _heading(doc: _Doc, key: str, level: int = 2, suffix: str = "") -> None:
    prefix = "" if not doc.lines else "\n"
    doc.add(f"{prefix}{'#' * level} {HEADINGS[key]}{suffix}")


# --------------------------------------------------------------------------------------------------- plan

def render_plan(root: Path, cap: int = MAX_SUMMARY_BYTES) -> tuple[str, list[dict[str, Any]]]:
    src = Sources(root)
    doc = _Doc(src, cap)
    root = Path(root)
    if (root / "plan-error.json").exists():
        error = src.doc("plan-error.json")
        _heading(doc, "refused")
        for key in ("source", "request", "path", "problem"):
            if key != "request" or _get(error, "request") is not None:
                doc.add(f"- {COLUMNS[key]}: {code(_get(error, key))}")
        doc.add("\n" + PLAN_FIX_HINT)
    elif (root / "plan-nothing.json").exists():
        nothing = src.doc("plan-nothing.json")
        _heading(doc, "nothing")
        notice = _get(nothing, "notice")
        doc.add("\n" + (notice if notice in KNOWN_NOTICES else f"{COLUMNS['notice']}: {code(notice)}"))
        requests = _get(nothing, "requests")
        if isinstance(requests, list) and requests:
            doc.add(f"\n- {COLUMNS['requests']}: " + ", ".join(code(r) for r in requests))
        doc.add("\n" + DISPATCH_BY_HAND)
    elif (root / "plan.json").exists():
        _render_plan_body(doc, src, src.doc("plan.json"))
    else:
        _heading(doc, "plan")
        doc.add("\n" + NO_PLAN)
    return doc.text(), src.entries


def _render_plan_body(doc: _Doc, src: Sources, plan: Any) -> None:
    f = "plan.json"
    _first_line(doc, _get(plan, "result_class") == "plumbing", None)
    _heading(doc, "plan")
    request = _get(plan, "request")
    doc.add(f"\n- {COLUMNS['request']}: {code(_get(request, 'path'))}, {COLUMNS['name']} "
            f"{code(_get(request, 'name'))}, {COLUMNS['sha']} {code(short(_get(request, 'sha256')))}")
    doc.add(f"- {COLUMNS['commit']}: {code(_get(plan, 'git_sha'))}")
    doc.add(f"- {COLUMNS['provider']}: {code(_get(plan, 'provider'))}")
    doc.add(f"- {COLUMNS['job_minutes']}: {src.num(f, '/job_minutes', 'int')}; {COLUMNS['max_parallel']}: "
            f"{src.num(f, '/max_parallel', 'int')}; {COLUMNS['retention_days']}: "
            f"{src.num(f, '/retention_days', 'int')}")
    doc.add(f"\n{COLUMNS['purpose']} {code(_get(request, 'purpose'))}")

    models = _get(plan, "models")
    models = models if isinstance(models, dict) else {}

    def model_rows() -> Any:
        for key in sorted(models):
            entry = models[key]
            gguf, lock = _get(entry, "gguf"), _get(entry, "lock")
            if _get(entry, "kind") != "gguf":
                pin = code(None)
            elif isinstance(lock, dict):
                pin = f"{code(lock.get('commit'), table=True)} {code(short(lock.get('sha256')), table=True)}"
            else:
                pin = NOT_PINNED
            yield [code(key, table=True), code(_get(entry, "kind"), table=True), code(_get(entry, "alias"), table=True),
                   code(_get(gguf, "repo"), table=True), code(_get(gguf, "file"), table=True),
                   code(_get(gguf, "revision"), table=True), pin]

    _heading(doc, "models", 3)
    doc.table(["key", "kind", "alias", "repo", "file", "revision", "pin"], model_rows)

    shards = _get(plan, "shards")
    shards = shards if isinstance(shards, list) else []

    def shard_rows() -> Any:
        for i, shard in enumerate(shards):
            units = _get(shard, "units")
            listed = ", ".join(code(u, table=True) for u in units) if isinstance(units, list) and units else "n/a"
            yield [code(_get(shard, "shard"), table=True), code(_get(shard, "model"), table=True),
                   code(_get(shard, "kind"), table=True), listed,
                   src.num(f, pointer("shards", i, "planned_minutes"), "int"),
                   src.num(f, pointer("shards", i, "timeout_minutes"), "int")]

    _heading(doc, "shards", 3)
    doc.table(["shard", "model", "kind", "units", "planned_minutes", "timeout_minutes"], shard_rows)

    units = _get(plan, "units")
    units = units if isinstance(units, list) else []

    def unit_rows() -> Any:
        for i, unit in enumerate(units):
            yield [code(_get(unit, "unit"), table=True), code(_get(unit, "experiment"), table=True),
                   code(_get(unit, "model"), table=True), src.num(f, pointer("units", i, "minutes"), "int"),
                   src.num(f, pointer("units", i, "seeds", 0), "int"), code(_get(unit, "shard"), table=True)]

    _heading(doc, "units", 3)
    doc.table(["unit", "experiment", "model", "minutes", "seed", "shard"], unit_rows)

    provision = _get(plan, "provision")
    if isinstance(provision, list) and provision:
        _heading(doc, "provision", 3)
        doc.table(["entry", "cache_key"], lambda: ([code(_get(p, "entry"), table=True),
                                                   code(_get(p, "cache_key"), table=True)] for p in provision))


# --------------------------------------------------------------------------------------------------- shard

def _unit_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.glob("units/*/unit.json"))


def render_shard(root: Path, cap: int = MAX_SUMMARY_BYTES) -> tuple[str, list[dict[str, Any]]]:
    root = Path(root)
    src = Sources(root)
    doc = _Doc(src, cap)
    status, prov = src.doc("status.json"), src.doc("provenance.json")
    prov = prov if isinstance(prov, dict) else None
    plumbing = prov is not None and (prov.get("result_class") != "real" or prov.get("provider") == "fake")
    sealed = isinstance(status, dict) and status.get("kind") == "lab_shard_status"
    records = [(rel, src.doc(rel)) for rel in _unit_files(root)] if sealed else []
    classes = [display_class(r, prov) if isinstance(r, dict) else "no-result" for _, r in records]
    _first_line(doc, plumbing, ("model" in classes) if prov is not None else None)
    shard = _get(status, "shard") if sealed else _get(prov, "shard")
    _heading(doc, "shard", suffix=f" {code(shard)}")
    if not sealed:
        doc.add("\n" + UNSEALED)
        return doc.text(), src.entries

    _heading(doc, "state", 3)
    doc.add(f"\n- {COLUMNS['failed_step']}: {code(status.get('failed_step'))}")
    doc.add(f"- {COLUMNS['prepare_problem']}: {code(_get(status, 'prepare', 'problem'))}")
    doc.add(f"- {COLUMNS['provenance_complete']}: {yes_no(_get(status, 'provenance', 'complete'))}; "
            f"{COLUMNS['interrupted']}: {yes_no(_get(status, 'provenance', 'interrupted'))}")
    doc.add(f"- {COLUMNS['run_attempt']}: {src.num('status.json', '/run_attempt', 'int')}")
    counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
    doc.add(f"- {COLUMNS['counts']}: " + (", ".join(
        f"{code(name)} {src.num('status.json', pointer('counts', name), 'int')}" for name in sorted(counts))
        or "n/a"))
    missing = status.get("missing_units")
    if isinstance(missing, list) and missing:
        doc.add(f"- {COLUMNS['missing_units']}: " + ", ".join(code(u) for u in missing))

    if prov is not None:
        _heading(doc, "host", 3)
        p = "provenance.json"
        doc.add(f"\n- {COLUMNS['cpu']}: {code(_get(prov, 'host', 'cpu', 'model_name'))}; {COLUMNS['nproc']}: "
                f"{src.num(p, '/host/nproc_available', 'int')}; {COLUMNS['mem_gib']}: "
                f"{src.num(p, '/host/mem_total_bytes', 'gib1')}")
        doc.add(f"- {COLUMNS['os']}: {code(_get(prov, 'host', 'os_release', 'PRETTY_NAME'))}; {COLUMNS['python']}: "
                f"{code(_get(prov, 'host', 'python'))}")
        server, model = _get(prov, "server"), _get(prov, "model")
        if _get(server, "kind") == "model":
            _heading(doc, "server", 3)
            doc.add(f"\n- {COLUMNS['server_tag']}: {code(_get(server, 'tag'))}; {COLUMNS['server_version']}: "
                    f"{code(_get(server, 'version'))}; {COLUMNS['server_sha']}: {code(short(_get(server, 'sha256')))}"
                    f"; {COLUMNS['verified_by']} {code(_get(server, 'verified_by'))}")
            doc.add(f"- {COLUMNS['model_repo']}: {code(_get(model, 'repo'))}; {COLUMNS['model_file']}: "
                    f"{code(_get(model, 'file'))}; {COLUMNS['model_commit']}: {code(_get(model, 'commit'))}; "
                    f"{COLUMNS['model_sha']}: {code(short(_get(model, 'sha256')))}; {COLUMNS['verified_by']} "
                    f"{code(_get(model, 'verified_by'))}")

    def unit_rows() -> Any:
        for (rel, record), cls in zip(records, classes):
            yield [code(_get(record, "unit") or rel.split("/")[1], table=True),
                   code(_get(record, "status"), table=True), cls, src.num(rel, "/exit_code", "int"),
                   src.num(rel, "/wall_s", "f1"), code(_get(record, "status_reason"), table=True)]

    _heading(doc, "units", 3)
    doc.table(["unit", "status", "class", "exit_code", "wall_s", "reason"], unit_rows)
    return doc.text(), src.entries


# --------------------------------------------------------------------------------------------------- report

def render_report(root: Path, cap: int = MAX_SUMMARY_BYTES) -> tuple[str, list[dict[str, Any]]]:
    src = Sources(Path(root))
    doc = _Doc(src, cap)
    report = src.doc("report.json")
    if not (isinstance(report, dict) and report.get("kind") == "lab_report"):
        _heading(doc, "report")
        doc.add("\n" + NO_REPORT)
        return doc.text(), src.entries
    f = "report.json"

    def rows(key: str) -> list[tuple[int, dict[str, Any]]]:
        items = report.get(key)
        return [(i, r) for i, r in enumerate(items) if isinstance(r, dict)] if isinstance(items, list) else []

    units = rows("units")
    _first_line(doc, report.get("result_class") == "plumbing", report.get("contains_measurements") is True)
    _heading(doc, "report")
    if isinstance(report.get("banner"), str):
        doc.add("\n" + report["banner"])
    request, plan = report.get("request"), report.get("plan")
    doc.add(f"\n- {COLUMNS['request']}: {code(_get(request, 'path'))}, {COLUMNS['name']} "
            f"{code(_get(request, 'name'))}, {COLUMNS['sha']} {code(short(_get(request, 'sha256')))}")
    doc.add(f"- {COLUMNS['plan_sha']}: {code(short(_get(plan, 'sha256')))}; {COLUMNS['commit']}: "
            f"{code(_get(plan, 'git_sha'))}; {COLUMNS['provider']}: {code(_get(plan, 'provider'))}")
    doc.add(f"- {COLUMNS['unit_count']}: {src.num(f, '/unit_count', 'int')}; {COLUMNS['shard_count']}: "
            f"{src.num(f, '/shard_count', 'int')}")
    doc.add(f"\n{COLUMNS['purpose']} {code(_get(request, 'purpose'))}")

    def shard_rows() -> Any:
        for i, s in rows("shards"):
            reason = s.get("state_reason") if s.get("state_reason") is not None else s.get("prepare_problem")
            yield [code(s.get("shard"), table=True), code(s.get("model"), table=True),
                   code(s.get("state"), table=True), src.num(f, pointer("shards", i, "run_attempt"), "int"),
                   code(s.get("failed_step"), table=True), src.num(f, pointer("shards", i, "exit_code"), "int"),
                   src.num(f, pointer("shards", i, "wall_s"), "f1"), code(_get(s, "host", "cpu_model"), table=True),
                   code(reason, table=True)]

    _heading(doc, "shards", 3)
    doc.table(["shard", "model", "state", "attempt", "step", "exit_code", "wall_s", "cpu", "reason"], shard_rows)

    no_result = [(i, u) for i, u in units if u.get("display_class") == "no-result"]
    if no_result:
        _heading(doc, "no-result", 3)
        doc.table(["unit", "status", "reason"], lambda: ([code(u.get("unit"), table=True),
                                                         code(u.get("status"), table=True),
                                                         code(u.get("status_reason"), table=True)]
                                                        for _, u in no_result))
    for cls in CLASS_ORDER:
        if any(u.get("display_class") == cls for _, u in units):
            _class_section(doc, src, cls, units, rows)
    sizing = rows("sim_sizing")
    if sizing:
        _sizing_section(doc, src, sizing)

    provision = rows("provision")
    if provision:
        _heading(doc, "provision", 3)

        def provision_rows() -> Any:
            for i, r in provision:
                yield [code(r.get("target") or r.get("state"), table=True), code(r.get("key"), table=True),
                       src.num(f, pointer("provision", i, "run_attempt"), "int"), yes_no(r.get("verified")),
                       code(r.get("verified_by"), table=True), yes_no(r.get("first_use")),
                       code(r.get("source"), table=True), code(short(r.get("sha256")), table=True),
                       yes_no(r.get("plan_matches")), src.num(f, pointer("provision", i, "total_s"), "f1"),
                       code(r.get("problem"), table=True)]

        doc.table(["target", "key", "attempt", "verified", "verified_by", "first_use", "source", "sha",
                   "plan_matches", "total_s", "problem"], provision_rows)

    lock = report.get("lock") if isinstance(report.get("lock"), dict) else {}
    _heading(doc, "lock", 3)
    doc.add(f"\n- {COLUMNS['lock_status']}: {code(lock.get('status'))}; {COLUMNS['new_entries']}: "
            f"{src.num(f, '/lock/new_entries', 'int')}")
    if lock.get("problem") is not None:
        doc.add(f"- {COLUMNS['problem']}: {code(lock.get('problem'))}")
    if lock.get("status") in LOCK_SENTENCES:
        doc.add("\n" + LOCK_SENTENCES[lock["status"]])

    _report_notes(doc, src, report, units)
    return doc.text(), src.entries


def _class_section(doc: _Doc, src: Sources, cls: str, units: list[tuple[int, dict[str, Any]]],
                   rows: Callable[[str], list[tuple[int, dict[str, Any]]]]) -> None:
    f = "report.json"
    _heading(doc, cls, 3)
    mine = [(i, u) for i, u in units if u.get("display_class") == cls]
    doc.table(["unit", "experiment", "model", "shard", "status", "wall_s"], lambda: (
        [code(u.get("unit"), table=True), code(u.get("experiment"), table=True), code(u.get("model"), table=True),
         code(u.get("shard"), table=True), code(u.get("status"), table=True),
         src.num(f, pointer("units", i, "wall_s"), "f1")] for i, u in mine))
    e3 = [(i, r) for i, r in rows("e3") if r.get("display_class") == cls]
    if e3:
        _heading(doc, "e3", 4)

        def e3_rows() -> Any:
            for i, r in e3:
                at = ("e3", i)
                yield [code(r.get("unit"), table=True), code(r.get("model"), table=True),
                       code(r.get("cpu_model"), table=True), code(r.get("workload"), table=True),
                       src.num(f, pointer(*at, "concurrency"), "int"), src.num(f, pointer(*at, "measured"), "int"),
                       src.num(f, pointer(*at, "ok"), "int"), src.num(f, pointer(*at, "e2e_s", "p50"), "f3"),
                       src.num(f, pointer(*at, "e2e_s", "p95"), "f3"), src.num(f, pointer(*at, "ttft_s", "p50"), "f3"),
                       src.num(f, pointer(*at, "decode_tok_s", "p50"), "f1"),
                       src.num(f, pointer(*at, "throughput", "requests_per_s"), "f3"), yes_no(r.get("measurement"))]

        doc.table(["unit", "model", "cpu", "workload", "concurrency", "measured", "ok", "e2e_median", "e2e_p95",
                   "ttft_median", "decode_median", "requests_per_s", "harness_measurement"], e3_rows)
    g0 = [(i, r) for i, r in rows("g0") if r.get("display_class") == cls]
    if g0:
        _heading(doc, "g0", 4)

        def g0_rows() -> Any:
            for i, r in g0:
                at = ("g0", i)
                yield [code(r.get("unit"), table=True), code(r.get("model"), table=True),
                       code(r.get("pack"), table=True), src.num(f, pointer(*at, "seed"), "int"),
                       src.num(f, pointer(*at, "records"), "int"), yes_no(r.get("passed")),
                       src.num(f, pointer(*at, "canaries_planted"), "int"),
                       src.num(f, pointer(*at, "hit_count"), "int"),
                       src.num(f, pointer(*at, "shingle_overlap_bytes"), "int"),
                       src.num(f, pointer(*at, "positive_control", "canary_hits"), "int"),
                       code(short(r.get("world_digest")), table=True)]

        doc.table(["unit", "model", "pack", "seed", "records", "passed", "canaries", "hits", "shingle_bytes",
                   "control_hits", "world"], g0_rows)
    sim = [(i, r) for i, r in rows("sim") if r.get("display_class") == cls]
    if sim:
        _sim_tables(doc, src, sim)
    latency = [(i, r) for i, r in rows("latency") if r.get("display_class") == cls]
    if latency:
        _heading(doc, "latency", 4)
        doc.table(["model", "cpu", "experiment", "task", "n", "median_ms", "p95_ms"], lambda: (
            [code(r.get("model"), table=True), code(r.get("cpu_model"), table=True),
             code(r.get("experiment"), table=True), code(r.get("task"), table=True),
             src.num(f, pointer("latency", i, "n"), "int"), src.num(f, pointer("latency", i, "p50_ms"), "f1"),
             src.num(f, pointer("latency", i, "p95_ms"), "f1")] for i, r in latency))


def _sim_tables(doc: _Doc, src: Sources, sim: list[tuple[int, dict[str, Any]]]) -> None:
    """The channels, lifts and pushdown tables of one class's sim rows, each followed by what its keys mean."""
    f = "report.json"

    def present(key: str, order: dict[str, str]) -> list[str]:
        found = {name for _, r in sim if isinstance(r.get(key), dict) for name in r[key]}
        return [name for name in order if name in found]

    channels = present("channels", SIM_CHANNEL_LABELS)

    def channel_rows() -> Any:
        for i, r in sim:
            for name in channels:
                if not isinstance(_get(r, "channels", name), dict):
                    continue
                at = ("sim", i, "channels", name)
                yield [code(r.get("unit"), table=True), code(r.get("model"), table=True), code(name, table=True),
                       src.num(f, pointer(*at, "found"), "int"), src.num(f, pointer(*at, "units"), "int"),
                       src.num(f, pointer(*at, "recall"), "f3"), src.num(f, pointer(*at, "precision_at_40"), "f3"),
                       src.num(f, pointer(*at, "average_precision"), "f3"), src.num(f, pointer(*at, "alerts"), "int"),
                       src.num(f, pointer(*at, "false_alarms"), "int")]

    _heading(doc, "sim", 4)
    doc.table(["unit", "model", "channel", "found", "patterns", "recall", "p_at_forty", "ap", "alerts", "false_alarms"],
              channel_rows)
    for name in channels:
        doc.add(("\n" if name == channels[0] else "") + f"- {code(name)}: {SIM_CHANNEL_LABELS[name]}")
    lifts = present("lifts", SIM_LIFT_LABELS)

    def lift_rows() -> Any:
        for i, r in sim:
            for name in lifts:
                if not isinstance(_get(r, "lifts", name), dict):
                    continue
                at = ("sim", i, "lifts", name)
                yield [code(r.get("unit"), table=True), code(name, table=True),
                       src.num(f, pointer(*at, "estimate"), "f3"), src.num(f, pointer(*at, "ci_low"), "f3"),
                       src.num(f, pointer(*at, "ci_high"), "f3")]

    _heading(doc, "sim-lifts", 4)
    doc.table(["unit", "lift", "estimate", "ci_low", "ci_high"], lift_rows)
    for name in lifts:
        doc.add(("\n" if name == lifts[0] else "") + f"- {code(name)}: {SIM_LIFT_LABELS[name]}")

    def pushdown_rows() -> Any:
        for i, r in sim:
            at = ("sim", i, "pushdown")
            yield [code(r.get("unit"), table=True), src.num(f, pointer(*at, "n"), "int"),
                   src.num(f, pointer(*at, "n_true"), "int"), src.num(f, pointer(*at, "supported"), "int"),
                   src.num(f, pointer(*at, "ap_pushdown"), "f3"), src.num(f, pointer(*at, "ap_stats_only"), "f3"),
                   src.num(f, pointer("sim", i, "raw_text_crossed"), "int"),
                   src.num(f, pointer("sim", i, "fallback_share"), "f3")]

    _heading(doc, "sim-pushdown", 4)
    doc.table(["unit", "candidates", "true", "supported", "ap_pushdown", "ap_stats", "raw_text", "fallback_share"],
              pushdown_rows)


def _sizing_section(doc: _Doc, src: Sources, sizing: list[tuple[int, dict[str, Any]]]) -> None:
    f = "report.json"
    _heading(doc, "sizing", 3)

    def sizing_rows() -> Any:
        for i, r in sizing:
            at = ("sim_sizing", i)
            yield [code(r.get("unit"), table=True), code(r.get("model"), table=True),
                   code(r.get("cpu_model"), table=True), code(r.get("status"), table=True),
                   code(r.get("display_class"), table=True), src.num(f, pointer(*at, "records_done"), "int"),
                   src.num(f, pointer(*at, "records_total"), "int"),
                   src.num(f, pointer(*at, "extract_record_s_p50"), "f1"),
                   src.num(f, pointer(*at, "judge_s_p50"), "f1"), src.num(f, pointer(*at, "estimate_s"), "min1"),
                   src.num(f, pointer(*at, "suggested_minutes"), "int")]

    doc.table(["unit", "model", "cpu", "status", "class", "records_done", "records", "extract_median_s",
               "judge_median_s", "estimate_minutes", "suggested_minutes"], sizing_rows)
    doc.add("\n" + SIZING_NOTE)


def _report_notes(doc: _Doc, src: Sources, report: dict[str, Any], units: list[tuple[int, dict[str, Any]]]) -> None:
    f = "report.json"
    notes = report.get("notes") if isinstance(report.get("notes"), dict) else {}
    _heading(doc, "notes", 3)
    cpus = notes.get("cpu_models") if isinstance(notes.get("cpu_models"), list) else []
    doc.add(f"\n- {COLUMNS['cpu_models']}: " + (", ".join(code(c) for c in cpus) or "n/a"))
    if len(cpus) > 1:
        doc.add("\n" + CPU_MODELS_DIFFER)
    groups = notes.get("world_digest") if isinstance(notes.get("world_digest"), list) else []
    for i, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        at = ("notes", "world_digest", i)
        digests = group.get("digests") if isinstance(group.get("digests"), list) else []
        listed = group.get("units") if isinstance(group.get("units"), list) else []
        doc.add(f"\n- {COLUMNS['pack']} {code(group.get('pack'))} {code(group.get('pack_version'))}, "
                f"{COLUMNS['seed']} {src.num(f, pointer(*at, 'seed'), 'int')}, {COLUMNS['records']} "
                f"{src.num(f, pointer(*at, 'records'), 'int')}: {COLUMNS['digests']} "
                + ", ".join(code(short(d)) for d in digests) + f"; {COLUMNS['units']} "
                + ", ".join(code(u) for u in listed) + f"; {COLUMNS['consistent']} {yes_no(group.get('consistent'))}")
        doc.add("\n" + (WORLD_DIGEST_SAME if group.get("consistent") is True else WORLD_DIGEST_DIFFERS))
    sim_groups = notes.get("sim_world_digest") if isinstance(notes.get("sim_world_digest"), list) else []
    for i, group in enumerate(sim_groups):
        if not isinstance(group, dict):
            continue
        at = ("notes", "sim_world_digest", i)
        digests = group.get("digests") if isinstance(group.get("digests"), list) else []
        listed = group.get("units") if isinstance(group.get("units"), list) else []
        doc.add(f"\n- {COLUMNS['plant']} {code(group.get('plant'))}, {COLUMNS['seed']} "
                f"{src.num(f, pointer(*at, 'seed'), 'int')}, {COLUMNS['weeks']} "
                f"{src.num(f, pointer(*at, 'weeks'), 'int')}: {COLUMNS['digests']} "
                + ", ".join(code(short(d)) for d in digests) + f"; {COLUMNS['units']} "
                + ", ".join(code(u) for u in listed) + f"; {COLUMNS['consistent']} {yes_no(group.get('consistent'))}")
        doc.add("\n" + (SIM_WORLD_SAME if group.get("consistent") is True else SIM_WORLD_DIFFERS))
    present = {n for _, u in units if isinstance(u.get("notes"), list) for n in u["notes"]}
    for key, sentence in NOTES.items():
        if key in present:
            doc.add("\n" + sentence)
    sim_rows = report.get("sim") if isinstance(report.get("sim"), list) else []
    sim_present = {n for r in sim_rows if isinstance(r, dict) and isinstance(r.get("notes"), list) for n in r["notes"]}
    for key, sentence in SIM_NOTES.items():
        if key in sim_present:
            doc.add("\n" + sentence)
    ignored = report.get("ignored_artifacts")
    if isinstance(ignored, list) and ignored:
        doc.add(f"\n- {COLUMNS['ignored']}: " + ", ".join(code(a) for a in ignored))
    skipped = report.get("skipped")
    if isinstance(skipped, list) and skipped:
        doc.add(f"\n- {COLUMNS['skipped']}: " + ", ".join(code(json.dumps(s, sort_keys=True)) for s in skipped))


RENDERERS = {"plan": render_plan, "shard": render_shard, "report": render_report}


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.summary",
                                description="Render a plan, shard or report summary from its run files.")
    p.add_argument("mode", choices=MODES)
    p.add_argument("--dir", required=True)
    p.add_argument("--append-to", help="append the Markdown here (the step summary)")
    p.add_argument("--md-out", help="write the Markdown here")
    p.add_argument("--sources-out", help="write where each number came from here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    outputs = [path for path in (args.append_to, args.md_out, args.sources_out) if path]
    for path in outputs:
        name = forbidden_root(path)
        if name is not None:
            print(f"error: no output may lie inside {name}/", file=sys.stderr)
            return EXIT_USAGE
    text, entries = RENDERERS[args.mode](Path(args.dir))
    sources = {"schema_version": 1, "kind": "lab_summary_sources", "mode": args.mode, "sources": entries}
    try:
        if args.append_to:
            Path(args.append_to).parent.mkdir(parents=True, exist_ok=True)
            with open(args.append_to, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        if args.md_out:
            Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.md_out).write_text(text, encoding="utf-8", newline="\n")
        if args.sources_out:
            Path(args.sources_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.sources_out).write_text(json.dumps(sources, indent=1) + "\n", encoding="utf-8")
    except OSError:
        print("error: a summary output cannot be written", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

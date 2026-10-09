"""Markdown summaries of a lab plan, one shard or the aggregate report, in which every number is read from a run file.

    python -m lab.summary plan|shard|report --dir DIR [--append-to FILE] [--md-out FILE] [--sources-out FILE]
                          [--log]

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

A plan summary adds the units the plan skipped (unit, experiment, model and reason: a known sentence plain, anything
else in a code span), the hosted calls each hosted model may make and the plan's bound (``plan.json``'s ``hosted``),
and, when the plan's ``prereg/prereg.json`` exists, what was preregistered: the E1 labels, the X1 and E2 prereg hashes
and the E2 rehearsal's candidates, judge calls and largest central reading (every number from that file). A hosted
model's alias column shows its model id. A shard summary of a shard with hosted units adds its hosted endpoint from
``provenance.json``: the scheme and ``host:port`` (no path is ever recorded), each secret's state, the problem, and per
key the model id, the preflight's status and reason, the calls used and the shard's share, and whether the host listed
the model.

A report's class sections hold, for its sim rows, a channels table (each channel key's meaning listed under it; the
found net of chance and chance finds columns only when a row has them, that is when the harness ran a no-plant control),
a lifts table, the by-construction table (:func:`_by_construction_table`) and a pushdown table; then E2 (its labels
first: synthetic, below the protocol minimum when a row is, the central comparator being the model itself; then the
conditions, ratio and candidates tables; the bar verdict only for a row whose central comparator is not the model
itself), X1 (its label, then the channels and lifts tables, each row's eligibility and the harness's warnings, then the
by-construction table), openFDA (its label and what its measurement flag means, the hindsight sentence when a requester
declared seeing recall outcomes first and the warning sentence when the replay warned, the scope of its false alarms,
each row's declaration and warnings, then the channels and fetch tables, then the sheets label and the sheets table)
and, once, in the class section of its display class, E1: its label, then the endpoints table (with each model's
zero-claim share), then, when the comparison carries them, the drops table (each model's post-processing counts by
reason, pooled over repeats, after :data:`~lab.notes.E1_DROPS_NOTE`), then the paired table,
whose non-inferiority and kill-flag columns appear only when the block's ``verdicts_shown`` (a model measurement, or a
hosted API result) and are otherwise replaced by the withheld sentence; a block that was not compared shows its reason
instead. When hosted models were among the endpoints, the hosted label follows the E1 label. The paired table names each
row's ``decision_metric`` (micro field F1, or the per-record mean field F1 of a harness without it) and its difference
and interval, then the per-record mean difference and the sign test. The class sections come in the order of
:data:`CLASS_ORDER`: ``model``, then ``hosted-api`` (hosted API results, never measured on this runner), then the rest.
A G0 table gains a protocol records column, after its note, when a scan was below the protocol size, and a model path
problems column when a row counts them. After the class sections, a sizing table of every sim unit (whatever its result)
with what it measured and the minutes it suggests for the next request, then the E2 sizing table, then (with hosted
keys) the hosted calls and estimated cost table followed by its note; the notes add the sim world-digest groups and the
sim notes the rows carry, each followed by the units that carry it when some sim rows do not.

The first line says what the numbers are not. For a plumbing plan, shard or report it is ``PLUMBING CHECK: no model
was run``, or :data:`~lab.notes.PLUMBING_HOSTED_LINE` when it holds hosted units, whose calls go to the configured
host even in a plumbing check (a plan shard with ``needs_secret``, a shard provenance with a ``hosted`` block, a
report with ``hosted`` keys). It is ``NO MEASUREMENT: ...`` for a real shard or report without a unit of display
class ``model`` or ``hosted-api`` (``units.display_class``; for a report, ``contains_measurements`` and
``contains_hosted`` both not true); otherwise (such a unit present, or the class unknown: no provenance, plan or
report) the summary starts with its heading. That first line is always kept. After it a summary holds whole rows
only, while its size plus the next row stays under the cap less :data:`RESERVE_BYTES` (the cap is
:data:`MAX_SUMMARY_BYTES`; GitHub takes at most 1 MiB per step); the first row that does not fit is replaced by the
truncation sentence, which ends the summary. The sentence holds no count: totals are elsewhere, sourced.

Exit 2 for a bad argument, an output that cannot be written or an output inside ``mycelic/``, ``research/``,
``NeuralGraph/`` or ``.github/``; else 0.

Without ``--log`` nothing is printed on stdout. With ``--log`` the files a reader of the job log needs are printed
there too, after the output check and before any output is written (:data:`LOG_BLOCKS`): ``plan`` prints
``plan/summary.md``; ``shard`` prints ``shard/provenance.json``, then ``shard/summary.md``; ``report`` prints
``report/report.json``, then ``report/report.md``, then ``report/lock-candidate.json``. A ``.md`` block is the
Markdown the other outputs receive; ``provenance.json`` and ``report.json`` are printed pretty (sorted keys, indent
one, literal non-ASCII; the files are canonical JSON, so ``canonical_dumps`` of the parsed body plus a newline is the
file) and ``lock-candidate.json`` verbatim. A block is ``=== MYCELIC-LAB <label> BEGIN lines=<n> sha256=<hex> ===``
(the hash of the file's bytes, or of the Markdown's UTF-8), its lines, then ``=== MYCELIC-LAB <label> END ===``. A
missing file is the single line ``=== MYCELIC-LAB <label> ABSENT ===``; a file that cannot be read, decoded or
parsed, or whose text cannot be printed line for line (no final newline, a carriage return, or a line that starts
with ``::`` or the marker prefix), is ``=== MYCELIC-LAB <label> UNREADABLE sha256=<hex|none> ===``; a file or body
over :data:`MAX_LOG_BYTES` is ``=== MYCELIC-LAB <label> TOO-LARGE bytes=<n> sha256=<hex> ===`` (``n`` is the
file's size, or the body's when only the body is over), never a part of it. The last block line is
``=== MYCELIC-LAB INDEX <label>=<state> ... ===``, a state being the line count, ``absent``, ``unreadable`` or
``too-large``, so a reader of the log's tail knows how much to read. ``::stop-commands::<T>`` before the blocks and
``::<T>::`` after them, where ``T`` is the sha256 of the lines between, keep the runner from acting on a workflow
command in the printed text. Nothing else is printed: never ``plan.json``, ``status.json``, a sources file,
a unit record, a log, a ledger or a harness file. Everything printed is already public (a step summary or an
artifact), and provenance never holds a base URL's path or a key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from mycelic.collective.jsonio import StrictJsonError, strict_load

from . import EXIT_OK, EXIT_USAGE, forbidden_root
from .notes import (BRANCH_DELETED, BY_CONSTRUCTION_LABEL, BY_CONSTRUCTION_NOTE, COLUMNS, CPU_MODELS_DIFFER,
                    DEFAULT_BRANCH, DELETE_ONLY, DISPATCH_BY_HAND, E1_COMPARE_FAILED, E1_DROPS_NOTE,
                    E1_ENDPOINT_EXCLUDED, E1_HOSTED_LABEL, E1_LABELS, E1_NO_REFERENCE, E1_VERDICTS_WITHHELD,
                    E1_WITHOUT_HOSTED, E2_CENTRAL_HOSTED_SKIPPED, E2_LABELS, E2_SIZING_NOTE, G0_BELOW_PROTOCOL,
                    HEADINGS, HOSTED_COST_NOTE, HOSTED_SECRETS_MISSING, LOCK_CONFLICT_NOTE, LOCK_NEW,
                    LOCK_NOT_COMPUTED, LOCK_UNCHANGED, MERGE_SEVERAL, NO_MEASUREMENT_LINE, NO_PLAN, NO_REPORT,
                    NOT_A_BRANCH, NOT_PINNED, NOTES, OPENFDA_FALSE_ALARM_SCOPE, OPENFDA_LABEL, OPENFDA_PUBLIC_FLAG,
                    OPENFDA_SAW_RECALLS, OPENFDA_WARNED, PLAN_FIX_HINT, PLUMBING_CHECK_LINE, PLUMBING_HOSTED_LINE,
                    PREREG_MISSING, SHEETS_LABEL, SIM_CHANNEL_LABELS, SIM_LIFT_LABELS, SIM_NOTES, SIM_WORLD_DIFFERS,
                    SIM_WORLD_SAME, SIZING_NOTE, TRUNCATED, UNSEALED, WORLD_DIGEST_DIFFERS, WORLD_DIGEST_SAME, X1_LABEL)
from .units import display_class

MAX_SUMMARY_BYTES = 900_000
RESERVE_BYTES = 4096
MAX_LOG_BYTES = 2_000_000
LOG_MARK = "=== MYCELIC-LAB"
# per mode, the blocks ``--log`` prints, most needed last (a log is read from its tail): (label, file under DIR or
# None for the rendered Markdown, style)
LOG_BLOCKS: dict[str, tuple[tuple[str, str | None, str], ...]] = {
    "plan": (("plan/summary.md", None, "md"),),
    "shard": (("shard/provenance.json", "provenance.json", "pretty"), ("shard/summary.md", None, "md")),
    "report": (("report/report.json", "report.json", "pretty"), ("report/report.md", None, "md"),
               ("report/lock-candidate.json", "lock-candidate.json", "raw")),
}
MODES = ("plan", "shard", "report")
SHA_SHOWN = 12
STYLES: dict[str, Callable[[Any], str]] = {
    "int": str,
    "f1": "{:.1f}".format,
    "f3": "{:.3f}".format,
    "f6": "{:.6f}".format,
    "gib1": lambda v: "{:.1f}".format(v / 2 ** 30),
    "min1": lambda v: "{:.1f}".format(v / 60),
}
KNOWN_NOTICES = (DELETE_ONLY, BRANCH_DELETED, DEFAULT_BRANCH, NOT_A_BRANCH, MERGE_SEVERAL)
LOCK_SENTENCES = {"unchanged": LOCK_UNCHANGED, "new_entries": LOCK_NEW, "conflict": LOCK_CONFLICT_NOTE,
                  "not_computed": LOCK_NOT_COMPUTED}
CLASS_ORDER = ("model", "hosted-api", "unverified", "plumbing", "no-model")
PLAN_SKIP_REASONS = (HOSTED_SECRETS_MISSING, E1_WITHOUT_HOSTED, E2_CENTRAL_HOSTED_SKIPPED)
E1_REASONS = (PREREG_MISSING, E1_NO_REFERENCE, E1_COMPARE_FAILED)
PREREG_FILE = "prereg/prereg.json"


def ids(sentence: str) -> str:
    """A notes sentence with its experiment-id placeholders filled with code spans."""
    return sentence.format(e_one=code("E1"), e_two=code("E2"), x_one=code("X1"), n_one=code("N1"))
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


def listed(values: Any) -> str:
    """Free texts (a harness's warnings, X1's reasons), each in a code span: ``none`` for an empty list, ``n/a`` for
    no list."""
    if not isinstance(values, list):
        return "n/a"
    return ", ".join(code(v) for v in values) if values else "none"


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


def _first_line(doc: _Doc, plumbing: bool, measured: bool | None, hosted: bool = False) -> None:
    if plumbing:
        doc.add(PLUMBING_HOSTED_LINE if hosted else PLUMBING_CHECK_LINE, keep=True)
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
    shards = _get(plan, "shards")
    hosted = isinstance(shards, list) and any(_get(s, "needs_secret") is True for s in shards)
    _first_line(doc, _get(plan, "result_class") == "plumbing", None, hosted)
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
            alias = _get(entry, "model") if _get(entry, "kind") == "hosted" else _get(entry, "alias")
            yield [code(key, table=True), code(_get(entry, "kind"), table=True), code(alias, table=True),
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

    skipped = _get(plan, "skipped")
    skipped = [s for s in skipped if isinstance(s, dict)] if isinstance(skipped, list) else []
    if skipped:
        _heading(doc, "plan-skipped", 3)
        doc.table(["unit", "experiment", "model", "reason"], lambda: (
            [code(s.get("unit"), table=True), code(s.get("experiment"), table=True), code(s.get("model"), table=True),
             s["reason"] if s.get("reason") in PLAN_SKIP_REASONS else code(s.get("reason"), table=True)]
            for s in skipped))
    hosted = _get(plan, "hosted")
    if isinstance(hosted, dict) and hosted:
        _heading(doc, "plan-hosted", 3)
        doc.table(["key", "model_id", "max_calls", "bound"], lambda: (
            [code(key, table=True), code(_get(hosted, key, "model"), table=True),
             src.num(f, pointer("hosted", key, "max_calls"), "int"), src.num(f, pointer("hosted", key, "bound"), "int")]
            for key in sorted(hosted)))

    provision = _get(plan, "provision")
    if isinstance(provision, list) and provision:
        _heading(doc, "provision", 3)
        doc.table(["entry", "cache_key"], lambda: ([code(_get(p, "entry"), table=True),
                                                   code(_get(p, "cache_key"), table=True)] for p in provision))
    if (src.root / PREREG_FILE).exists():
        _prereg_section(doc, src)


def _prereg_section(doc: _Doc, src: Sources) -> None:
    f = PREREG_FILE
    manifest = src.doc(f)
    _heading(doc, "prereg", 3)
    files = _get(manifest, "files")

    def sha(rel: str) -> str:
        return code(short(_get(files, rel, "sha256")))

    names = {"e1": code("E1"), "x1": code("X1"), "e2": code("E2")}
    e1 = _get(manifest, "e1")
    if isinstance(e1, dict):
        records, claims = src.num(f, "/e1/labels/records", "int"), src.num(f, "/e1/labels/claims", "int")
        source = code(_get(e1, "labels", "source"))
        doc.add(f"\n- {names['e1']} {COLUMNS['labels']}: {COLUMNS['label_source']} {source}, "
                f"{COLUMNS['records']} {records}, {COLUMNS['claims']} {claims}, {COLUMNS['sha']} "
                f"{code(short(_get(e1, 'labels', 'sha256')))}; {COLUMNS['prereg']} {sha(str(_get(e1, 'prereg')))}")
    for name in ("x1", "e2"):
        block = _get(manifest, name)
        if isinstance(block, dict):
            doc.add(f"- {names[name]} {COLUMNS['prereg']} {sha(str(_get(block, 'prereg')))}")
    rehearsal = _get(manifest, "e2", "rehearsal")
    if isinstance(rehearsal, dict):
        at = ("e2", "rehearsal")
        counts = [src.num(f, pointer(*at, *keys), "int") for keys in (("candidates", "total"), ("candidates", "seeds"),
                                                                       ("calls", "judge_record"),
                                                                       ("central_raw_records", "max"))]
        doc.add(f"- {COLUMNS['rehearsal']}: {COLUMNS['candidates']} {counts[0]}, {COLUMNS['seeds']} {counts[1]}, "
                f"{COLUMNS['judge_calls']} {counts[2]}, {COLUMNS['raw_max']} {counts[3]}")


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
    _first_line(doc, plumbing, ("model" in classes or "hosted-api" in classes) if prov is not None else None,
                prov is not None and prov.get("hosted") is not None)
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
        if isinstance(_get(prov, "hosted"), dict):
            _hosted_section(doc, src, prov["hosted"])

    def unit_rows() -> Any:
        for (rel, record), cls in zip(records, classes):
            yield [code(_get(record, "unit") or rel.split("/")[1], table=True),
                   code(_get(record, "status"), table=True), cls, src.num(rel, "/exit_code", "int"),
                   src.num(rel, "/wall_s", "f1"), code(_get(record, "status_reason"), table=True)]

    _heading(doc, "units", 3)
    doc.table(["unit", "status", "class", "exit_code", "wall_s", "reason"], unit_rows)
    return doc.text(), src.entries


def _hosted_section(doc: _Doc, src: Sources, hosted: dict[str, Any]) -> None:
    """The shard's hosted endpoint from ``provenance.json``: the secrets' states, scheme and host (never a path), the
    problem, and per key the preflight, the calls used and the shard's share."""
    p = "provenance.json"
    _heading(doc, "shard-hosted", 3)
    secrets = hosted.get("secrets") if isinstance(hosted.get("secrets"), dict) else {}
    doc.add(f"\n- {COLUMNS['scheme']}: {code(hosted.get('scheme'))}; {COLUMNS['hosted_host']}: "
            f"{code(hosted.get('host'))}")
    if secrets:
        doc.add("- " + ", ".join(f"{code(name)} {code(secrets[name])}" for name in sorted(secrets)))
    if hosted.get("problem") is not None:
        doc.add(f"- {COLUMNS['problem']}: {code(hosted.get('problem'))}")
    keys = hosted.get("keys") if isinstance(hosted.get("keys"), dict) else {}
    doc.table(["key", "model_id", "preflight", "reason", "calls_used", "shard_share", "listed"], lambda: (
        [code(key, table=True), code(_get(keys, key, "model"), table=True),
         code(_get(keys, key, "preflight", "status"), table=True),
         code(_get(keys, key, "preflight", "reason"), table=True),
         src.num(p, pointer("hosted", "keys", key, "calls_used"), "int"),
         src.num(p, pointer("hosted", "keys", key, "share"), "int"),
         yes_no(_get(keys, key, "models_list", "model_listed"))] for key in sorted(keys)))


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
    _first_line(doc, report.get("result_class") == "plumbing",
                report.get("contains_measurements") is True or report.get("contains_hosted") is True,
                isinstance(report.get("hosted"), dict) and bool(report["hosted"]))
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
    e1 = report.get("e1") if isinstance(report.get("e1"), dict) else None
    for cls in CLASS_ORDER:
        if any(u.get("display_class") == cls for _, u in units) or (e1 is not None and e1.get("display_class") == cls):
            _class_section(doc, src, cls, units, rows, e1)
    sizing = rows("sim_sizing")
    if sizing:
        _sizing_section(doc, src, sizing)
    e2_sizing = rows("e2_sizing")
    if e2_sizing:
        _e2_sizing_section(doc, src, e2_sizing)
    if isinstance(report.get("hosted"), dict) and report["hosted"]:
        _hosted_cost_section(doc, src, report["hosted"])

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
                   rows: Callable[[str], list[tuple[int, dict[str, Any]]]], e1: dict[str, Any] | None = None) -> None:
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
        below = any(r.get("below_protocol") is True for _, r in g0)
        if below:
            doc.add("\n" + G0_BELOW_PROTOCOL)

        model_path = any(r.get("model_path_problems") is not None for _, r in g0)

        def g0_rows() -> Any:
            for i, r in g0:
                at = ("g0", i)
                head = [code(r.get("unit"), table=True), code(r.get("model"), table=True),
                        code(r.get("pack"), table=True), src.num(f, pointer(*at, "seed"), "int"),
                        src.num(f, pointer(*at, "records"), "int")]
                protocol = [src.num(f, pointer(*at, "protocol_records"), "int")] if below else []
                cells = [*head, *protocol, yes_no(r.get("passed")),
                         src.num(f, pointer(*at, "canaries_planted"), "int"),
                         src.num(f, pointer(*at, "hit_count"), "int"),
                         src.num(f, pointer(*at, "shingle_overlap_bytes"), "int"),
                         src.num(f, pointer(*at, "positive_control", "canary_hits"), "int")]
                if model_path:
                    cells.append(src.num(f, pointer(*at, "model_path_problems"), "int"))
                yield [*cells, code(short(r.get("world_digest")), table=True)]

        doc.table(["unit", "model", "pack", "seed", "records", *(["protocol_records"] if below else []), "passed",
                   "canaries", "hits", "shingle_bytes", "control_hits",
                   *(["model_path_problems"] if model_path else []), "world"], g0_rows)
    sim = [(i, r) for i, r in rows("sim") if r.get("display_class") == cls]
    if sim:
        _sim_tables(doc, src, sim)
    e2 = [(i, r) for i, r in rows("e2") if r.get("display_class") == cls]
    if e2:
        _e2_tables(doc, src, e2)
    x1 = [(i, r) for i, r in rows("x1") if r.get("display_class") == cls]
    if x1:
        _x1_tables(doc, src, x1)
    openfda = [(i, r) for i, r in rows("openfda") if r.get("display_class") == cls]
    if openfda:
        _openfda_tables(doc, src, openfda)
    if e1 is not None and e1.get("display_class") == cls:
        _e1_tables(doc, src, e1)
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
    net = any(_get(r, "channels", name, "found_net") is not None for _, r in sim for name in channels)

    def channel_rows() -> Any:
        for i, r in sim:
            for name in channels:
                if not isinstance(_get(r, "channels", name), dict):
                    continue
                at = ("sim", i, "channels", name)
                cells = [code(r.get("unit"), table=True), code(r.get("model"), table=True), code(name, table=True),
                         src.num(f, pointer(*at, "found"), "int")]
                if net:
                    cells += [src.num(f, pointer(*at, "found_net"), "int"),
                              src.num(f, pointer(*at, "chance_found"), "int")]
                yield [*cells, src.num(f, pointer(*at, "units"), "int"), src.num(f, pointer(*at, "recall"), "f3"),
                       src.num(f, pointer(*at, "precision_at_40"), "f3"),
                       src.num(f, pointer(*at, "average_precision"), "f3"), src.num(f, pointer(*at, "alerts"), "int"),
                       src.num(f, pointer(*at, "false_alarms"), "int")]

    _heading(doc, "sim", 4)
    doc.table(["unit", "model", "channel", "found", *(["found_net", "chance_found"] if net else []), "patterns",
               "recall", "p_at_forty", "ap", "alerts", "false_alarms"], channel_rows)
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
    _by_construction_table(doc, src, "sim", sim)

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


def _e2_tables(doc: _Doc, src: Sources, e2: list[tuple[int, dict[str, Any]]]) -> None:
    """E2's labels, then its conditions, ratio and candidates tables (and the bar verdict, never for a central
    comparator that is the model itself)."""
    f = "report.json"
    _heading(doc, "e2", 4)
    doc.add("\n" + ids(E2_LABELS["synthetic"]))
    if any(r.get("below_protocol_minimum") is not False for _, r in e2):
        doc.add("\n" + E2_LABELS["below_protocol"])
    if any(r.get("central") == "self" for _, r in e2):
        doc.add("\n" + E2_LABELS["self_central"])

    def condition_rows() -> Any:
        for i, r in e2:
            conditions = r.get("conditions") if isinstance(r.get("conditions"), dict) else {}
            for name in sorted(conditions):
                at = ("e2", i, "conditions", name)
                yield [code(r.get("unit"), table=True), code(name, table=True), src.num(f, pointer(*at, "ap"), "f3"),
                       src.num(f, pointer(*at, "ap_ci_low"), "f3"), src.num(f, pointer(*at, "ap_ci_high"), "f3"),
                       src.num(f, pointer(*at, "precision_at_k"), "f3")]

    doc.table(["unit", "condition", "ap", "ci_low", "ci_high", "p_at_k"], condition_rows)
    _heading(doc, "e2-ratio", 4)
    doc.table(["unit", "central", "ratio", "ci_low", "ci_high", "pushdown_raw"], lambda: (
        [code(r.get("unit"), table=True), code(r.get("central"), table=True),
         src.num(f, pointer("e2", i, "ratio", "estimate"), "f3"), src.num(f, pointer("e2", i, "ratio", "ci_low"), "f3"),
         src.num(f, pointer("e2", i, "ratio", "ci_high"), "f3"),
         src.num(f, pointer("e2", i, "raw_text_bytes", "pushdown"), "int")] for i, r in e2))
    judged = [(i, r) for i, r in e2 if r.get("central") != "self"]
    if judged:
        doc.table(["unit", "verdict", "withheld"], lambda: (
            [code(r.get("unit"), table=True), yes_no(_get(r, "verdict", "pass")),
             code(r.get("withheld_reason"), table=True)] for _, r in judged))
    _heading(doc, "e2-candidates", 4)
    doc.table(["unit", "candidates", "seeds", "true", "decoy", "background", "protocol_min"], lambda: (
        [code(r.get("unit"), table=True), src.num(f, pointer("e2", i, "candidates", "total"), "int"),
         src.num(f, pointer("e2", i, "candidates", "seeds"), "int"),
         *(src.num(f, pointer("e2", i, "candidates", "by_label", label), "int")
           for label in ("true", "decoy", "background")),
         src.num(f, pointer("e2", i, "protocol_min_candidates"), "int")] for i, r in e2))


def _x1_tables(doc: _Doc, src: Sources, x1: list[tuple[int, dict[str, Any]]]) -> None:
    f = "report.json"
    _heading(doc, "x1", 4)
    doc.add("\n" + ids(X1_LABEL))

    def channel_rows() -> Any:
        for i, r in x1:
            channels = r.get("channels") if isinstance(r.get("channels"), dict) else {}
            for name in sorted(channels):
                at = ("x1", i, "channels", name)
                yield [code(r.get("unit"), table=True), code(name, table=True),
                       src.num(f, pointer(*at, "found"), "int"), src.num(f, pointer(*at, "units"), "int"),
                       src.num(f, pointer(*at, "recall"), "f3"), src.num(f, pointer(*at, "precision_at_40"), "f3"),
                       src.num(f, pointer(*at, "average_precision"), "f3"), src.num(f, pointer(*at, "alerts"), "int"),
                       src.num(f, pointer(*at, "false_alarms"), "int")]

    doc.table(["unit", "channel", "found", "patterns", "recall", "p_at_forty", "ap", "alerts", "false_alarms"],
              channel_rows)

    def lift_rows() -> Any:
        for i, r in x1:
            lifts = r.get("lifts") if isinstance(r.get("lifts"), dict) else {}
            for name in sorted(lifts):
                at = ("x1", i, "lifts", name)
                yield [code(r.get("unit"), table=True), code(name, table=True),
                       src.num(f, pointer(*at, "estimate"), "f3"), src.num(f, pointer(*at, "ci_low"), "f3"),
                       src.num(f, pointer(*at, "ci_high"), "f3")]

    _heading(doc, "x1-lifts", 4)
    doc.table(["unit", "lift", "estimate", "ci_low", "ci_high"], lift_rows)
    for _, r in x1:
        reasons = _get(r, "x1", "reasons")
        shown = ", ".join(code(reason) for reason in reasons) if isinstance(reasons, list) and reasons else "n/a"
        doc.add(f"\n- {code(r.get('unit'))}: {COLUMNS['eligible']} {yes_no(_get(r, 'x1', 'eligible'))}; "
                f"{COLUMNS['blind']} {yes_no(_get(r, 'stamps', 'blind'))}; {COLUMNS['extractor']} "
                f"{code(_get(r, 'stamps', 'extractor'))}; {COLUMNS['reason']}: {shown}; {COLUMNS['warnings']}: "
                f"{listed(r.get('warnings'))}")
    _by_construction_table(doc, src, "x1", x1)


def _by_construction_table(doc: _Doc, src: Sources, key: str, rows: list[tuple[int, dict[str, Any]]]) -> None:
    """The baselines each row's plant blinds by construction (the ``by_construction`` entries the scorecard carries,
    with the harness's own label), after what that means for the lifts; nothing when no row has an entry."""
    f = "report.json"
    marked = [(i, r) for i, r in rows if isinstance(r.get("by_construction"), list) and r["by_construction"]]
    if not marked:
        return
    _heading(doc, "by-construction", 4)
    doc.add("\n" + BY_CONSTRUCTION_NOTE)

    def entry_rows() -> Any:
        for i, r in marked:
            for j, entry in enumerate(r["by_construction"]):
                if not isinstance(entry, dict):
                    continue
                at = (key, i, "by_construction", j)
                label = entry.get("label")
                yield [code(r.get("unit"), table=True), code(entry.get("channel"), table=True),
                       code(entry.get("visibility"), table=True), src.num(f, pointer(*at, "units"), "int"),
                       src.num(f, pointer(*at, "found"), "int"), src.num(f, pointer(*at, "recall"), "f3"),
                       label if label == BY_CONSTRUCTION_LABEL else code(label, table=True)]

    doc.table(["unit", "channel", "visibility", "patterns", "found", "recall", "label"], entry_rows)


def _openfda_tables(doc: _Doc, src: Sources, rows_: list[tuple[int, dict[str, Any]]]) -> None:
    f = "report.json"
    _heading(doc, "openfda", 4)
    doc.add("\n" + OPENFDA_LABEL)
    doc.add("\n" + OPENFDA_PUBLIC_FLAG)
    if any(r.get("recall_outcomes_seen_before_prereg") is True for _, r in rows_):
        doc.add("\n" + OPENFDA_SAW_RECALLS)
    if any(isinstance(r.get("warnings"), list) and r["warnings"] for _, r in rows_):
        doc.add("\n" + OPENFDA_WARNED)
    doc.add("\n" + OPENFDA_FALSE_ALARM_SCOPE)
    for n, (_, r) in enumerate(rows_):
        doc.add(("\n" if n == 0 else "") + f"- {code(r.get('unit'))}: {COLUMNS['saw_recalls']} "
                f"{yes_no(r.get('recall_outcomes_seen_before_prereg'))}; {COLUMNS['warnings']}: "
                f"{listed(r.get('warnings'))}")

    def channel_rows() -> Any:
        for i, r in rows_:
            channels = r.get("channels") if isinstance(r.get("channels"), dict) else {}
            for name in sorted(channels):
                at = ("openfda", i, "channels", name)
                yield [code(r.get("unit"), table=True), code(name, table=True),
                       src.num(f, pointer(*at, "in_scope"), "int"), src.num(f, pointer(*at, "found"), "int"),
                       src.num(f, pointer(*at, "recall_rate"), "f3"),
                       src.num(f, pointer(*at, "median_lead_days"), "f1"),
                       src.num(f, pointer(*at, "post_recall_alerts"), "int"),
                       src.num(f, pointer(*at, "false_alarms"), "int"),
                       src.num(f, pointer(*at, "false_alarms_per_week"), "f3"),
                       code(_get(channels, name, "reason"), table=True)]

    doc.table(["unit", "channel", "in_scope", "found", "recall_rate", "lead_days", "post_alerts", "false_alarms",
               "per_week", "channel_reason"], channel_rows)

    def fetch_rows() -> Any:
        for i, r in rows_:
            fetch = r.get("fetch") if isinstance(r.get("fetch"), dict) else {}
            for dataset in sorted(fetch):
                codes = fetch[dataset] if isinstance(fetch[dataset], dict) else {}
                for product in sorted(codes):
                    at = ("openfda", i, "fetch", dataset, product)
                    yield [code(dataset, table=True), code(product, table=True),
                           src.num(f, pointer(*at, "total"), "int"), src.num(f, pointer(*at, "fetched"), "int"),
                           yes_no(_get(codes, product, "truncated")), code(_get(codes, product, "reason"), table=True)]

    _heading(doc, "openfda-fetch", 4)
    doc.table(["dataset", "code", "total", "fetched", "truncated", "reason"], fetch_rows)
    _heading(doc, "sheets", 4)
    doc.add("\n" + ids(SHEETS_LABEL))

    def sheet_rows() -> Any:
        for i, r in rows_:
            for sheet, done in (("n1", "n_sampled"), ("e1", "n_written")):
                if isinstance(_get(r, "sheets", sheet), dict):
                    at = ("openfda", i, "sheets", sheet)
                    yield [code(sheet.upper(), table=True), src.num(f, pointer(*at, "n_requested"), "int"),
                           src.num(f, pointer(*at, done), "int")]

    doc.table(["sheet", "requested", "written"], sheet_rows)


def _e1_tables(doc: _Doc, src: Sources, e1: dict[str, Any]) -> None:
    """E1's label first, then its endpoints, drops (when the comparison carries them) and paired tables; the verdict
    columns only when ``verdicts_shown``."""
    f = "report.json"
    _heading(doc, "e1", 4)
    if e1.get("label") in E1_LABELS:
        doc.add("\n" + ids(E1_LABELS[e1["label"]]))
    if isinstance(e1.get("hosted_endpoints"), list) and e1["hosted_endpoints"]:
        doc.add("\n" + ids(E1_HOSTED_LABEL))
    if e1.get("compared") is not True:
        reason = e1.get("reason")
        doc.add("\n" + (reason if reason in E1_REASONS else f"{COLUMNS['reason']}: {code(reason)}"))
        return
    doc.add(f"\n- {COLUMNS['labels']}: {COLUMNS['label_source']} {code(_get(e1, 'labels', 'source'))}, "
            f"{COLUMNS['pack']} {code(_get(e1, 'labels', 'pack'))}, {COLUMNS['records']} "
            f"{src.num(f, '/e1/labels/records', 'int')}, {COLUMNS['claims']} {src.num(f, '/e1/labels/claims', 'int')}, "
            f"{COLUMNS['sha']} {code(short(_get(e1, 'labels', 'sha256')))}")
    doc.add(f"- {COLUMNS['reference']} {code(e1.get('reference'))}; {COLUMNS['margin']} "
            f"{src.num(f, '/e1/margin', 'f3')}; {COLUMNS['runs']} {src.num(f, '/e1/runs', 'int')}; "
            f"{COLUMNS['underpowered_below']} {src.num(f, '/e1/underpowered_below', 'int')} {COLUMNS['pairs']}; "
            f"{COLUMNS['kill_below']} {src.num(f, '/e1/kill_below', 'f3')}")
    endpoints = e1.get("endpoints") if isinstance(e1.get("endpoints"), dict) else {}
    _heading(doc, "e1-endpoints", 4)
    doc.table(["model", "runs", "field_f1", "ci_low", "ci_high", "claim_f1", "json_validity", "zero_claim_share",
               "p50_ms", "mismatch"],
              lambda: ([code(name, table=True), src.num(f, pointer("e1", "endpoints", name, "runs"), "int"),
                        src.num(f, pointer("e1", "endpoints", name, "field_f1", "value"), "f3"),
                        src.num(f, pointer("e1", "endpoints", name, "field_f1", "ci_low"), "f3"),
                        src.num(f, pointer("e1", "endpoints", name, "field_f1", "ci_high"), "f3"),
                        src.num(f, pointer("e1", "endpoints", name, "claim_f1", "value"), "f3"),
                        src.num(f, pointer("e1", "endpoints", name, "json_validity_rate"), "f3"),
                        src.num(f, pointer("e1", "endpoints", name, "zero_claim_share"), "f3"),
                        src.num(f, pointer("e1", "endpoints", name, "latency_ms_p50"), "f1"),
                        yes_no(_get(endpoints, name, "model_mismatch"))] for name in sorted(endpoints)))
    dropped = [name for name in sorted(endpoints) if isinstance(_get(endpoints, name, "drops"), dict)]
    if dropped:
        _heading(doc, "e1-drops", 4)
        doc.add("\n" + E1_DROPS_NOTE)
        doc.table(["model", "drop_reason", "drop_count"], lambda: (
            [code(name, table=True), code(reason, table=True),
             src.num(f, pointer("e1", "endpoints", name, "drops", reason), "int")]
            for name in dropped for reason in sorted(endpoints[name]["drops"])))
    paired = e1.get("paired") if isinstance(e1.get("paired"), dict) else {}
    shown = e1.get("verdicts_shown") is True
    _heading(doc, "e1-paired", 4)

    def paired_rows() -> Any:
        for name in sorted(paired):
            at = ("e1", "paired", name)
            verdicts = [yes_no(_get(paired, name, "non_inferior")), yes_no(_get(paired, name, "kill_flag"))]
            yield [code(name, table=True), code(_get(paired, name, "against"), table=True),
                   src.num(f, pointer(*at, "n"), "int"), code(_get(paired, name, "decision_metric"), table=True),
                   src.num(f, pointer(*at, "diff"), "f3"), src.num(f, pointer(*at, "ci_low"), "f3"),
                   src.num(f, pointer(*at, "ci_high"), "f3"), src.num(f, pointer(*at, "mean_diff"), "f3"),
                   src.num(f, pointer(*at, "sign_p"), "f3"), yes_no(_get(paired, name, "underpowered")),
                   *(verdicts if shown else [])]

    doc.table(["model", "against", "pairs", "decision_metric", "diff", "ci_low", "ci_high", "mean_diff", "sign_p",
               "underpowered", *(["non_inferior", "kill_flag"] if shown else [])], paired_rows)
    if not shown:
        doc.add("\n" + E1_VERDICTS_WITHHELD)
    left_out = e1.get("endpoints_without_runs")
    if isinstance(left_out, list) and left_out:
        doc.add("\n" + E1_ENDPOINT_EXCLUDED)
        doc.add(f"\n- {COLUMNS['model']}: " + ", ".join(code(m) for m in left_out))


def _hosted_cost_section(doc: _Doc, src: Sources, hosted: dict[str, Any]) -> None:
    f = "report.json"
    _heading(doc, "hosted", 3)
    doc.table(["key", "model_id", "max_calls", "bound", "n", "preflight_calls", "tokens_in", "tokens_out",
               "tokens_missing", "priced", "estimated_usd"], lambda: (
        [code(key, table=True), code(_get(hosted, key, "model"), table=True),
         *(src.num(f, pointer("hosted", key, name), "int")
           for name in ("max_calls", "bound", "calls", "preflight_calls", "tokens_in", "tokens_out", "tokens_missing")),
         yes_no(_get(hosted, key, "priced")), src.num(f, pointer("hosted", key, "estimated_usd"), "f6")]
        for key in sorted(hosted)))
    doc.add("\n" + HOSTED_COST_NOTE)


def _e2_sizing_section(doc: _Doc, src: Sources, sizing: list[tuple[int, dict[str, Any]]]) -> None:
    f = "report.json"
    _heading(doc, "e2-sizing", 3)
    doc.table(["unit", "model", "cpu", "status", "class", "projected_minutes", "budget_minutes", "share", "exceeds",
               "suggested_minutes"], lambda: (
        [code(r.get("unit"), table=True), code(r.get("model"), table=True), code(r.get("cpu_model"), table=True),
         code(r.get("status"), table=True), code(r.get("display_class"), table=True),
         src.num(f, pointer("e2_sizing", i, "projected_s"), "min1"),
         src.num(f, pointer("e2_sizing", i, "budget_s"), "min1"), src.num(f, pointer("e2_sizing", i, "share"), "f1"),
         yes_no(r.get("exceeds")), src.num(f, pointer("e2_sizing", i, "suggested_minutes"), "int")]
        for i, r in sizing))
    doc.add("\n" + E2_SIZING_NOTE)


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
    sim_rows = [r for r in (report.get("sim") if isinstance(report.get("sim"), list) else []) if isinstance(r, dict)]
    for key, sentence in SIM_NOTES.items():
        carriers = [r.get("unit") for r in sim_rows if isinstance(r.get("notes"), list) and key in r["notes"]]
        if carriers:
            doc.add("\n" + sentence)
            if len(carriers) < len(sim_rows):
                doc.add(f"- {COLUMNS['units']}: " + ", ".join(code(u) for u in carriers))
    ignored = report.get("ignored_artifacts")
    if isinstance(ignored, list) and ignored:
        doc.add(f"\n- {COLUMNS['ignored']}: " + ", ".join(code(a) for a in ignored))
    skipped = report.get("skipped")
    if isinstance(skipped, list) and skipped:
        doc.add(f"\n- {COLUMNS['skipped']}: " + ", ".join(code(json.dumps(s, sort_keys=True)) for s in skipped))


RENDERERS = {"plan": render_plan, "shard": render_shard, "report": render_report}


# --------------------------------------------------------------------------------------------------- the job log

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _log_lines(body: str) -> list[str] | None:
    """The lines of a body that can be printed line for line (it ends with a newline, holds no carriage return and
    no line starts with ``::`` or the marker prefix), else None."""
    if body == "":
        return []
    if not body.endswith("\n") or "\r" in body:
        return None
    lines = body[:-1].split("\n")
    if any(line.startswith(("::", LOG_MARK)) for line in lines):
        return None
    return lines


def _log_block(label: str, root: Path, rel: str | None, style: str, md: str) -> tuple[list[str], str]:
    """One block's lines and its INDEX entry: the rendered Markdown (``rel`` None), or the file ``root / rel``
    pretty-printed (``pretty``) or verbatim (``raw``)."""
    head = f"{LOG_MARK} {label}"
    if rel is None:
        try:
            data = md.encode("utf-8")
        except UnicodeEncodeError:
            return [f"{head} UNREADABLE sha256=none ==="], "unreadable"
    else:
        try:
            data = (root / rel).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return [f"{head} ABSENT ==="], "absent"
        except OSError:
            return [f"{head} UNREADABLE sha256=none ==="], "unreadable"
    sha = _sha256(data)
    unreadable = [f"{head} UNREADABLE sha256={sha} ==="], "unreadable"
    if len(data) > MAX_LOG_BYTES:
        return [f"{head} TOO-LARGE bytes={len(data)} sha256={sha} ==="], "too-large"
    try:
        if rel is None:
            body = md
        elif style == "pretty":
            body = json.dumps(strict_load(data), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        else:
            body = data.decode("utf-8")
        size = len(body.encode("utf-8"))
    except (ValueError, TypeError, RecursionError):  # StrictJsonError and the Unicode errors are ValueErrors
        return unreadable
    if size > MAX_LOG_BYTES:
        return [f"{head} TOO-LARGE bytes={size} sha256={sha} ==="], "too-large"
    lines = _log_lines(body)
    if lines is None:
        return unreadable
    return [f"{head} BEGIN lines={len(lines)} sha256={sha} ===", *lines, f"{head} END ==="], str(len(lines))


def log_text(mode: str, root: Path, md: str) -> str:
    """What ``--log`` prints for ``mode``: its blocks (``md`` is the rendered summary) and the INDEX line, between
    ``::stop-commands::<T>`` and ``::<T>::``, ``T`` being the sha256 of the lines between."""
    inner: list[str] = []
    index: list[str] = []
    for label, rel, style in LOG_BLOCKS[mode]:
        lines, state = _log_block(label, Path(root), rel, style, md)
        inner += lines
        index.append(f"{label}={state}")
    inner.append(f"{LOG_MARK} INDEX {' '.join(index)} ===")
    joined = "\n".join(inner)
    token = _sha256(joined.encode("utf-8"))
    return f"::stop-commands::{token}\n{joined}\n::{token}::\n"


def _print_log(text: str) -> None:
    """UTF-8 on stdout whatever its encoding, flushed; a stdout that cannot be written is reported on stderr and
    changes no exit code."""
    try:
        sys.stdout.flush()
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            buffer.write(text.encode("utf-8"))
            buffer.flush()
    except OSError:
        print("error: the log blocks cannot be printed", file=sys.stderr)


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.summary",
                                description="Render a plan, shard or report summary from its run files.")
    p.add_argument("mode", choices=MODES)
    p.add_argument("--dir", required=True)
    p.add_argument("--append-to", help="append the Markdown here (the step summary)")
    p.add_argument("--md-out", help="write the Markdown here")
    p.add_argument("--sources-out", help="write where each number came from here")
    p.add_argument("--log", action="store_true", help="also print the summary and its run files on stdout, between "
                                                       "markers (for the job log)")
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
    if args.log:
        _print_log(log_text(args.mode, Path(args.dir), text))
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

#!/usr/bin/env python
"""The pack drafter on public data: two commands, real records, D002's code.

    python tools/onboard/fetch_msha.py download --out RAW
    python demo/onboard/run_demo.py --raw RAW --out OUT [--company c1] [--settings FILE] [--work DIR] [--markers]

It points D002's pipeline (``docs/collective/onboard/CHOICE-D002.md``, run 38024536763) at MSHA's public accident
file, a field no pack covers, for one operator. D002 splits the file by controller (a parent company, column
``CONTROLLER_ID``) and calls each one an operator, c1 to c5. It uses the existing code only, in D002's order:

* **(a) the export read:** ``tools/onboard/fetch_msha.py split`` (MSHA's download and split code) with the settings
  (D002's by default) cuts the file into one export per operator, c1 to c5 by D002's rule; the operator's export is
  read by the drafter's own reader. The settings file names this source's columns; the drafter's code names none;
* **(b) the drafted pack:** :func:`mycelic.collective.onboard.draft.draft_export` over the training years, then the
  loader and the privacy floor (:func:`mycelic.collective.onboard.check.check_pack`). A drafted term is withheld from
  the screen when one of its words is a word of a refused value of an operator in the split, or when the operator's
  narratives write that word like a name or never in lower case (:func:`withheld_reason`);
* **(c) reading the held-out years:** the sample, the three controls and the metrics, with the functions of
  :mod:`mycelic.collective.onboard.score` in the order its per-company step calls them, so the numbers are D002's for
  that operator when the file and the settings are D002's. The figures D002 recorded are read from its choice file,
  and the input file's size and sha256 are compared with the ones D002 recorded (``demo/onboard/d002-input.json``);
* **(d) the cross-mine audit:** the normalised export of the held-out years, each mine id replaced by its label (m01
  onwards, in the order of the ids), through :func:`mycelic.collective.pilot.audit.audit` with no outcomes. Each mine
  runs as its own site inside this process, from the operator's export; nothing is sent over a network. Every bundle
  and cell that crosses from a mine's store to HQ's carries its label, never its id. The same audit also runs under
  the mines' own ids, as D002's M4 ran it, and the two results are compared. What left each mine is counted from the
  audit's own collective store while it runs, and the bytes that left are scanned by the guard. The review list is
  shown in two parts: the items X or S raised (they read only the weekly cells that left the mines, and the mines
  behind each are counted from those cells), and the items R_mf alone raised (a reference channel that counts
  record-level codes centrally);
* **(e)** one line on what the review list means.

**What it never prints or writes:** a narrative or any record text, a record id, a mine id, an operator or controller
name or id. Operators are c1 to c5 (D002's labels); mines are m01 onwards. Before anything is printed or written, the
strings that came from records (category labels and first terms) go through D002's last guard against the operator's
own export (:func:`mycelic.collective.onboard.report.company_hits`), and the whole rendered text and the bytes that left
the mines are scanned for every record id and mine id of every operator in the split (D002's backstop,
:class:`mycelic.collective.onboard.report.Backstop`) and for every value of a refused column of at least four
characters. An export of the split that cannot be read counts as a hit. A hit withholds everything but the hit counts,
and the exit code is 1. Error texts go through the same checks.

``--work`` holds the split exports, the drafted pack and the controls: record data, never under ``--out``. Without it a
temporary directory is used and deleted. ``--out`` gets ``demo.json`` and ``demo.html`` (self-contained: no script,
no link, light and dark). The console version goes to stdout; one progress line per step goes to stderr. Exit codes:
0 shown; 1 withheld, or the drafted pack failed its privacy floor; 2 a usage, input or run error (its text shown
only when the guard finds none of the split's values in it; never a traceback).
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import html
import importlib.util
import io
import json
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mycelic.collective.experiments.common import code_commit  # noqa: E402
from mycelic.collective.onboard import draft as D  # noqa: E402
from mycelic.collective.onboard import report as R  # noqa: E402
from mycelic.collective.onboard import score as S  # noqa: E402
from mycelic.collective.onboard.check import check_pack, ngram_hits  # noqa: E402
from mycelic.collective.onboard.exports import ExportError, read_export  # noqa: E402
from mycelic.collective.packs.canonical import folded  # noqa: E402
from mycelic.collective.packs.connector import map_rows  # noqa: E402
from mycelic.collective.pilot import audit as A  # noqa: E402

KIND = "onboard_drafter_demo"
SCHEMA_VERSION = 3
ARM = "msha"
DEFAULT_SETTINGS = "docs/collective/onboard/D002-settings.json"
RUN_FILE = "docs/collective/onboard/run-002.json"
CHOICE = "docs/collective/onboard/CHOICE-D002.md"
D002_INPUT = "demo/onboard/d002-input.json"
FETCH = "tools/onboard/fetch_msha.py"
COMPANY_RE = re.compile(r"c[1-9][0-9]?", re.ASCII)
WORD_RE = re.compile(r"[^\W_]+")
SENTENCE_END = frozenset(".!?")
NAME_SHARE = 0.5          # a word is written like a name in at least half of its lower-case and mid-sentence uses
READER_NAMES = {"drafted": "drafted pack", "majority_prior": "majority prior", "permuted_labels": "permuted labels",
                "label_names": "label names"}
CHECK_NAMES = {"loader": "the loader", "term_floor": "the term floor", "value_floor": "the value floor",
               "refused_strings": "refused strings", "refused_terms": "refused terms", "template": "the template",
               "ngram": "narrative n-grams", "labels": "column-name labels"}
COUNT_CHANNELS = ("X", "S")             # read only the weekly cells that left the mines
REFERENCE_CHANNEL = "R_mf"              # record-level codes counted centrally; on the review list, apart
COMPARATORS = ("P", "PRR")              # record-level comparators; never on the review list
TITLE = "A pack drafted from an export alone"
NOT_SHOWN = (
    "No early warning is measured. The demo has no outcomes, so nothing here says the alerts come early or at all.",
    "The review list is unjudged. No one has checked whether any item is a real problem.",
    "The reading score uses the filed categories as the answer key. They are not checked labels.",
    "The data are public records written to a regulator, not a company's own files.",
    "Not \"no configuration\". The settings file names this source's columns, and fetch_msha.py is download code "
    "for it (D002's M1 counts such code). The drafter's code names no column.",
    "Operators are c1 to c5 and mines m01 onwards. The guard looks in every output for the record ids, mine ids and "
    "refused values (operator and controller names and ids among them) of the operators in the split, as D002's "
    "guard reads them. A drafted term is withheld from the screen when one of its words is a word of those refused "
    "values, or when the operator's narratives write that word like a name (with a capital or in capitals) or never "
    "in lower case. Case is not read at the start of a sentence, after a full stop (so after 'Mr.' or 'Dr.') or in "
    "narratives written all in capitals. A name that is not such a word is not caught when the narratives write it in "
    "lower case in most of its uses, or in lower case even once with its other uses only where case is not read: the "
    "screen does not show that no name is on it.",
)
REVIEW_MEANS = ("Items from the weekly cells: {counted}. Each is a category whose counts rose at {min_sites} or more "
                "of {company}'s mines in the same weeks, and none matches an outcome on record, because the demo has "
                "no outcomes file. Only the operator's own people could say which are real. Reference only, from "
                "R_mf: {reference}. Those need record-level codes counted centrally, which this setup does not send.")
WITHHELD_ERROR = "(its text is withheld: it may name a record value)"
UNCHECKED_ERROR = "(its text is withheld: no export could be read to check it)"


class DemoError(ValueError):
    """A usage or input error: a missing file, an unknown company, settings without the arm."""


# --------------------------------------------------------------------------------------------------- small helpers

def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def f3(value: Any) -> str:
    """A rate as D002's report prints it: rounded to four places in the file, then shown to three."""
    return R._f(S.rounded(value))


def n(value: int) -> str:
    return f"{value:,}"


def secs(value: float) -> str:
    return f"{value:.1f} s"


def joined(items: Sequence[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def weeks_text(first: str, last: str) -> str:
    return f"week {first}" if first == last else f"weeks {first} to {last}"


def load_fetch() -> Any:
    spec = importlib.util.spec_from_file_location("fetch_msha", ROOT / FETCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def text_block(*lines: str) -> dict[str, Any]:
    return {"kind": "text", "lines": [line for line in lines if line]}


def list_block(items: Sequence[str], title: str | None = None) -> dict[str, Any]:
    return {"kind": "list", "title": title, "items": list(items)}


def table_block(columns: Sequence[str], rows: Sequence[Sequence[str]], title: str | None = None) -> dict[str, Any]:
    return {"kind": "table", "title": title, "columns": list(columns), "rows": [list(r) for r in rows]}


class Clock:
    """Wall time per step (``time.perf_counter``), with one progress line per step on stderr."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.start = time.perf_counter()

    @contextlib.contextmanager
    def step(self, label: str, seconds: dict[str, float], key: str) -> Any:
        t0 = time.perf_counter()
        yield
        seconds[key] = time.perf_counter() - t0
        self.emit(f"{label}: {secs(seconds[key])}")

    def total(self) -> float:
        return time.perf_counter() - self.start


# --------------------------------------------------------------------------------------------------- D002's record

def d002_record(path: Path | None = None) -> dict[str, Any]:
    """What D002 recorded under "Runs" in its choice file: the run id, the arm's best control, each operator's drafted
    and best-control micro F1 (three places, as written there) and M4's audit counts. Strings as written; ``found``
    is False when the file or a figure cannot be read, and the demo then says so."""
    path = path or ROOT / CHOICE
    out: dict[str, Any] = {"source": rel(path), "found": False, "run": None, "best_control": None,
                           "companies": {}, "audit": None}
    try:
        text = " ".join(path.read_text(encoding="utf-8").split())
    except OSError:
        return out
    if "## Runs" not in text:
        return out
    runs = text.split("## Runs", 1)[1]
    m = re.search(r"\[Run (\d+)\]\(https://github\.com/\S+?/actions/runs/(\d+)\)", runs)
    if m is not None and m.group(1) == m.group(2):
        out["run"] = m.group(1)
    m = re.search(r"against the best control, the ([a-z ]+?), (\d\.\d{3})", runs)
    if m is not None and m.group(1).replace(" ", "_") in S.CONTROLS:
        out["best_control"] = m.group(1).replace(" ", "_")
    m = re.search(r"drafted F1 against the best control: MSHA (.+?)\. NHTSA", runs)
    if m is not None:
        for label, drafted, best in re.findall(r"(c\d+) (\d\.\d{3}) / (\d\.\d{3})", m.group(1)):
            out["companies"][label] = {"drafted": drafted, "best_control": best}
    m = re.search(r"on (c\d+)'s drafted pack: ([\d,]+) records, ([\d,]+) sites, ([\d,]+) weeks, a review list of "
                  r"([\d,]+)", runs)
    if m is not None:
        out["audit"] = {"company": m.group(1), "records": m.group(2), "sites": m.group(3), "weeks": m.group(4),
                        "review_list": m.group(5)}
    out["found"] = bool(out["run"] and out["best_control"] and out["companies"])
    return out


def run_file_sha(path: Path | None = None) -> str | None:
    try:
        return json.loads((path or ROOT / RUN_FILE).read_text(encoding="utf-8")).get("settings_sha256")
    except (OSError, ValueError, AttributeError):
        return None


def d002_input(path: Path | None = None) -> dict[str, Any] | None:
    """The input file D002 read, as recorded: its name, size and the first hex digits of its sha256. None when the
    record cannot be read."""
    try:
        rec = json.loads((path or ROOT / D002_INPUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (isinstance(rec, dict) and isinstance(rec.get("file"), str) and isinstance(rec.get("bytes"), int)
          and isinstance(rec.get("sha256_prefix"), str) and re.fullmatch(r"[0-9a-f]{8,64}", rec["sha256_prefix"]))
    return rec if ok else None


def input_facts(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def compare_input(facts: Mapping[str, Any], recorded: Mapping[str, Any] | None) -> str:
    """``same`` when the input has the size and the sha256 prefix D002 recorded, ``revised`` when it has not, and
    ``unknown`` when there is no record to compare with."""
    if recorded is None or recorded["file"] != facts["file"]:
        return "unknown"
    same = facts["bytes"] == recorded["bytes"] and facts["sha256"].startswith(recorded["sha256_prefix"])
    return "same" if same else "revised"


def input_line(facts: Mapping[str, Any], status: str, recorded: Mapping[str, Any] | None) -> str:
    head = f"Input: {facts['file']}, {n(facts['bytes'])} bytes, sha256 {facts['sha256'][:12]}..."
    if status == "same":
        return f"{head}: the file D002 read (D002 recorded the same size and sha256 prefix)."
    if status == "revised":
        return (f"{head}: not the file D002 read, which was {n(recorded['bytes'])} bytes, sha256 "
                f"{recorded['sha256_prefix']}... So MSHA has revised the file since D002's run, or this is another "
                "file.")
    return f"{head}. D002's record of its input was not found, so the two cannot be compared."


# --------------------------------------------------------------------------------------------------- (a) the export

def step_export(settings_path: Path, settings: Mapping[str, Any], raw: Path, work: Path,
                company: str) -> tuple[Any, dict[str, Any]]:
    """The split (``fetch_msha.py split``, its printed counts kept out of the console) and the operator's export,
    read by the drafter's reader. The input file's size and sha256 are taken first."""
    fetch = load_fetch()
    for name, _ in fetch.FILES[:1]:
        if not (raw / name).is_file():
            raise DemoError(f"{raw / name} is missing: run python {FETCH} download --out {raw} first")
    facts = input_facts(raw / fetch.FILES[0][0])
    split_dir = work / "split"
    with contextlib.redirect_stdout(io.StringIO()):
        result = fetch.split(settings_path, raw, split_dir)
    companies, source = result["companies"], result["source"]
    if company not in companies:
        raise DemoError(f"the split made no operator {company} (it made {', '.join(sorted(companies)) or 'none'})")
    export = read_export(split_dir / companies[company]["file"])
    rule = companies[company]
    roles = S.arm_roles(settings, ARM)
    data = {"file": {"rows": source["rows"], "rejected": dict(source["rejected"]),
                     "columns": source["header"]["columns"],
                     "date_format": source["date_format"], "controllers": source["controllers"],
                     "qualifying": source["qualifying"], "used": source["used"],
                     "labels": sorted(companies, key=lambda c: int(c[1:]))},
            "export": {"company": company, "rows": len(export), "columns": len(export.columns),
                       "rejected": dict(export.rejected), "encoding": export.encoding,
                       **{k: rule[k] for k in ("training_rows_with_narrative", "test_rows_with_narrative",
                                               "training_mines")}},
            "roles": {**roles.single(), "refused": list(roles.forbidden)},
            "split_column": settings["arms"][ARM]["companies"]["column"]}
    return export, {"data": data, "split_dir": split_dir, "companies": companies, "input": facts}


def blocks_export(data: Mapping[str, Any], company: str) -> list[dict[str, Any]]:
    f, e, r = data["file"], data["export"], data["roles"]
    rejected = sum(f["rejected"].values())
    used = f["labels"][0] if len(f["labels"]) == 1 else f"{f['labels'][0]} to {f['labels'][-1]}"
    return [
        text_block(f"MSHA's accident file: {n(f['rows'])} rows read, {n(rejected)} rejected, {f['columns']} columns. "
                   f"Dates read as {f['date_format']}.",
                   f"Controllers in the file: {n(f['controllers'])}. A controller is a parent company that can run "
                   f"several operators and mines. D002 splits the file by controller ({data['split_column']}) and "
                   f"calls each one an operator: {n(f['qualifying'])} qualify by D002's rule, and {f['used']} are "
                   f"used, {used} in D002's order.",
                   f"{company}'s export: {n(e['rows'])} rows, {e['columns']} columns, "
                   f"{n(sum(e['rejected'].values()))} rejected. {n(e['training_rows_with_narrative'])} training rows "
                   f"and {n(e['test_rows_with_narrative'])} held-out rows have a narrative, from "
                   f"{e['training_mines']} mines in the training years."),
        table_block(("Role", "Column"), [("record id", r["record_id"]), ("site (a mine)", r["site"]),
                                         ("date", r["date"]), ("narrative", r["narrative"]),
                                         ("filed category (the answer key)", r["category"])],
                    title="The roles come from the settings file: data, not code."),
        text_block(f"Values of {len(r['refused'])} columns are read only to "
                   + (f"split the file ({data['split_column']}), " if data["split_column"] in r["refused"] else "")
                   + "to refuse and withhold terms, and to check every output, and are never shown: "
                   + ", ".join(r["refused"]) + ". Every other column is ignored."),
    ]


# --------------------------------------------------------------------------------------------------- names

def name_shaped_words(narratives: Iterable[str], share: float = NAME_SHARE) -> tuple[frozenset[str], int]:
    """The words (folded) of the narratives that are not plain words, and how many narratives have no case to read.

    A narrative with no lower-case letter is written all in capitals: its words are taken, but their case is not read
    (the narrative is counted). In the others, a use of a word is in lower case (anywhere), or written like a name: a
    capital first letter, or all capitals, away from the start of a sentence. A word is plain when it has a lower-case
    use and fewer than ``share`` of its lower-case and name-like uses are name-like. Every other word is returned:
    a word written like a name in at least ``share`` of those uses (in capitals inside mixed-case text included), and
    a word never written in lower case (seen only at sentence starts, or only in narratives written all in capitals).
    A name the narratives write in lower case often enough, or that is also a common word written so, is plain."""
    name_like: Counter[str] = Counter()
    lower: Counter[str] = Counter()
    seen: set[str] = set()
    caps = 0
    for text in narratives:
        readable = any(ch.islower() for ch in text)
        if not readable:
            caps += int(any(ch.isupper() for ch in text))
        prev = None
        for m in WORD_RE.finditer(text):
            at_start = prev is None or any(ch in SENTENCE_END for ch in text[prev:m.start()])
            prev = m.end()
            word = m.group()
            if not any(ch.isalpha() for ch in word):
                continue
            words = D.words_of(folded(word))
            seen.update(words)
            if not readable:
                continue
            if not any(ch.isupper() for ch in word):
                lower.update(words)
            elif not at_start:
                name_like.update(words)
    plain = {w for w in lower if name_like[w] < share * (name_like[w] + lower[w])}
    return frozenset(seen - plain), caps


def name_shaped(term: str, names: frozenset[str]) -> bool:
    return any(w in names for w in D.words_of(folded(term)))


def withheld_reason(term: str, names: frozenset[str], refused_words: frozenset[str]) -> str | None:
    """Why a drafted term is kept off the screen, or None: ``refused_word`` when one of its words is a word of a
    refused value of an operator in the split, else ``name_shaped`` when one is not a plain word of the operator's
    narratives (:func:`name_shaped_words`)."""
    words = D.words_of(folded(term))
    if any(w in refused_words for w in words):
        return "refused_word"
    if any(w in names for w in words):
        return "name_shaped"
    return None


# --------------------------------------------------------------------------------------------------- (b) the draft

def step_draft(settings: Mapping[str, Any], export: Any, company: str, work: Path) -> dict[str, Any]:
    """D002's per-company draft: the pack written and loaded, then the privacy floor checked."""
    spec, params = settings["arms"][ARM], settings["params"]
    roles = S.arm_roles(settings, ARM)
    lang = D.load_language(roles.language)
    template = D.load_template()
    train = D.parse_window(*spec["train"])
    pack_id = D.slug(f"{ARM} {company}", params, template["ids.json"]["id_prefix"])
    d = D.draft_export(export, roles, train, pack_id, params=params, lang=lang, template=template)
    pack_dir = work / company / "pack"
    D.write_pack(d.files, pack_dir)
    pack, error = D.load_written(pack_dir)
    checked = check_pack(pack_dir, export, roles, train, params=params, lang=lang, template=template,
                         settings=settings)
    return {"draft": d, "pack": pack, "loader_error": error, "check": checked, "pack_dir": pack_dir,
            "roles": roles, "template": template, "train": train}


def data_draft(b: Mapping[str, Any], settings: Mapping[str, Any], names: frozenset[str], refused_words: frozenset[str],
               caps: int) -> dict[str, Any]:
    d, checked = b["draft"], b["check"]
    facts, params = d.facts, settings["params"]
    shown = bool(checked["passed"])
    prefix = b["template"]["ids.json"]["placeholder_prefix"]
    predicates = []
    reasons: Counter[str] = Counter()
    for p in facts["predicates"]:
        if not shown:
            predicates.append({"code": p["code"], "records": p["records"], "terms": p["terms"]})
            continue
        kept = []
        for t in p["first_terms"]:
            why = None if t.startswith(prefix) else withheld_reason(t, names, refused_words)
            if why is None:
                kept.append(t)
            else:
                reasons[why] += 1
        predicates.append({"label": p["label"], "records": p["records"], "first_terms": kept,
                           "withheld_terms": len(p["first_terms"]) - len(kept),
                           "refused_assignable": p["refused_assignable"]})
    return {"window": list(facts["training"]["window"]), "training_rows": facts["training"]["rows"],
            "corpus": facts["training"]["corpus"], "floor": {"records": params["floor_records"],
                                                            "sites": params["floor_sites"],
                                                            "predicate_records": params["predicate_min_records"]},
            "categories": {"passing_floor": len(facts["categories"]["passing_floor"]),
                           "specific": facts["categories"]["specific"], "other": facts["categories"]["other"],
                           "below_floor": facts["categories"]["below_floor"],
                           "refused": facts["categories"]["refused"]},
            "terms": {k: facts["terms"][k] for k in ("total", "eligible", "refused")},
            "placeholders": facts["placeholders"],
            "refusal": {k: facts["refusal"][k] for k in ("categories", "terms", "assignable")},
            "privacy_floor": {"passed": shown, "checks": {k: bool(v["passed"]) for k, v in checked["checks"].items()}},
            "loader_passed": b["pack"] is not None,
            "names": {"withheld_terms": sum(p.get("withheld_terms", 0) for p in predicates),
                      "refused_word": reasons["refused_word"], "name_shaped": reasons["name_shaped"],
                      "narratives_in_capitals": caps},
            "predicates": predicates}


def predicate_line(p: Mapping[str, Any]) -> str:
    """One predicate as D002's report prints it (``report.render``), less any term kept off the screen
    (:func:`withheld_reason`), whose count is added at the end."""
    line = (f"{p['label']} ({p['records']} corpus records; {p['refused_assignable']} refused terms would have been "
            f"assigned): {', '.join(p['first_terms']) or 'no term shown'}")
    if p.get("withheld_terms"):
        line += f" ({p['withheld_terms']} more withheld from the screen)"
    return line


def blocks_draft(data: Mapping[str, Any], company: str) -> list[dict[str, Any]]:
    c, fl = data["categories"], data["floor"]
    first, last = data["window"]
    floor = data["privacy_floor"]
    checks = ", ".join(CHECK_NAMES.get(k, k) for k in floor["checks"])
    out = [text_block(
        f"Training years {first} to {last}: {n(data['training_rows'])} rows, {n(data['corpus'])} with a narrative. "
        "The drafter learns only from these.",
        f"Filed categories that passed the floor (at least {fl['records']} records at {fl['sites']} mines): "
        f"{c['passing_floor']}. Predicates among them (at least {fl['predicate_records']} records and a specific "
        f"label): {c['specific']}. Other bucket: {c['other']}. Below the floor, left out: {c['below_floor']}.",
        f"The refusal (the refused columns' values, record ids and mine ids) removed "
        f"{data['refusal']['categories']} categories and {n(data['refusal']['terms'])} floor-passing terms.",
        (f"The privacy floor passed: {checks}." if floor["passed"] else
         "The privacy floor failed. Labels and terms are withheld, as D002 withholds them."))]
    if floor["passed"]:
        names = data["names"]
        out.append(list_block([predicate_line(p) for p in data["predicates"]],
                              title=f"Predicates and their first terms, as D002's report prints them "
                                    f"({n(data['terms']['total'])} terms in all; {data['placeholders']} predicates "
                                    "learned none):"))
        out.append(text_block(
            f"Terms withheld from the screen: {n(names['withheld_terms'])}. {n(names['refused_word'])} hold a word of "
            f"a refused value (a name or an id) of an operator in the split. {n(names['name_shaped'])} hold a word "
            f"{company}'s narratives write like a name (with a capital or in capitals away from a sentence start, in "
            "at least half of its uses that show case) or never write in lower case. Narratives written all in "
            f"capitals, whose case is not read: {n(names['narratives_in_capitals'])}."))
    else:
        out.append(list_block([f"{p['code']}: {p['records']} corpus records, {p['terms']} terms"
                               for p in data["predicates"]], title="Predicates by code:"))
    return out


# --------------------------------------------------------------------------------------------------- (c) reading

def step_read(settings: Mapping[str, Any], export: Any, b: Mapping[str, Any], company: str,
              work: Path) -> dict[str, Any]:
    """Rule 3 to 5 for one operator, with :mod:`.score`'s functions in the order ``score._company`` calls them, so
    each reader's metrics are the ones D002's arm file holds for this operator (same seeds, same B)."""
    spec, params, boot = settings["arms"][ARM], settings["params"], settings["bootstrap"]
    d, pack, template = b["draft"], b["pack"], b["template"]
    test = D.parse_window(*spec["test"])
    seed = f"{settings['sample']['seed_prefix']}:{ARM}:{company}"
    sample, counts = S.draw_sample(export, d, test, seed, spec["per_company"], company)
    in_text = S.label_in_text(sample, d.plan)
    golds = [s.gold for s in sample]
    top = min(d.plan.ids, key=lambda p: (-d.plan.counts[p], p))
    permuted = D.permuted_labels(d.corpus, f"{settings['permute']['seed_prefix']}:{ARM}:{company}")
    perm_lex = D.with_placeholders(D.assign_terms(d.table, d.eligible, D.record_labels(permuted, d.plan), d.plan.ids,
                                                  params), template["ids.json"])
    name_lex = D.with_placeholders(D.label_name_lexicon(d.plan, d.lang, d.refusal), template["ids.json"])
    rows = S.drafted_rows(sample, export, d)
    other = {d.plan.other_id}
    preds: dict[str, list[frozenset[str]]] = {}
    rejected: dict[str, int] = {}
    preds["drafted"], rejected["drafted"] = S.read_records(pack, rows, other)
    preds["majority_prior"] = [frozenset({top}) for _ in sample]
    preds["permuted_labels"], rejected["permuted_labels"] = S.read_records(
        S.control_pack(d, perm_lex, work / "controls" / company, "permuted_labels"), rows, other)
    preds["label_names"], rejected["label_names"] = S.read_records(
        S.control_pack(d, name_lex, work / "controls" / company, "label_names"), rows, other)
    labels = [company] * len(sample)
    boot_seed = f"{boot['seed_prefix']}:{ARM}"
    readers = {r: S.reader_metrics(labels, preds[r], golds, B=boot["B"], seed=boot_seed) for r in S.READERS}
    f1 = {r: readers[r]["micro"]["f1"] for r in S.CONTROLS}
    best = max(S.CONTROLS, key=lambda r: (f1[r] if f1[r] is not None else -1.0, -S.CONTROLS.index(r)))
    diff = S.difference(preds["drafted"], preds[best], golds, B=boot["B"], seed=boot_seed)
    return {"sample": counts, "label_in_text": in_text, "readers": readers, "best_control": best,
            "difference": diff, "reader_rejected": rejected, "majority_predicate": top, "window": test.to_json(),
            "B": boot["B"]}


def compare_reading(company: str, readers: Mapping[str, Any], record: Mapping[str, Any], is_d002: bool,
                    input_status: str = "unknown") -> dict[str, Any]:
    """The demo's drafted and best-control F1 for this operator against what D002 recorded, both to three places,
    with whether the input file is the one D002 read."""
    fig = record["companies"].get(company)
    best = record["best_control"]
    out: dict[str, Any] = {"run": record["run"], "source": record["source"], "recorded": fig, "best_control": best,
                           "input": input_status}
    if not record["found"] or fig is None or best is None:
        out["status"] = "not_recorded"
        return out
    out["demo"] = {"drafted": f3(readers["drafted"]["micro"]["f1"]), "best_control": f3(readers[best]["micro"]["f1"])}
    same = out["demo"] == fig
    out["status"] = "not_comparable" if not is_d002 else ("reproduced" if same else "differs")
    return out


def data_read(c: Mapping[str, Any], cmp: Mapping[str, Any]) -> dict[str, Any]:
    readers = {}
    for r in S.READERS:
        m = c["readers"][r]
        readers[r] = {"f1": m["micro"]["f1"], "ci_low": m["micro"]["ci_low"], "ci_high": m["micro"]["ci_high"],
                      "precision": m["micro"]["precision"], "recall": m["micro"]["recall"],
                      "macro_f1": m["macro"]["f1"], "coverage": m["coverage"], "records": m["records"]}
    dif = c["difference"]
    return {"window": c["window"], "sample": dict(c["sample"]), "label_in_text": c["label_in_text"],
            "readers": readers, "best_control": c["best_control"],
            "difference": {"against": c["best_control"], "diff": dif["diff"], "ci_low": dif["ci_low"],
                           "ci_high": dif["ci_high"]},
            "bootstrap_B": c["B"], "d002": dict(cmp)}


def why_differs(input_status: str) -> str:
    """Why numbers made with D002's settings differ from D002's run, from the input comparison."""
    return {"same": "The settings are D002's and the input is the file D002 read (the same size and sha256 prefix), "
                    "so the code differs from that run's.",
            "revised": "The settings are D002's, but the input is not the file D002 read: MSHA has revised the file "
                       "since D002's run, or this is another file.",
            "unknown": "The settings are D002's. D002's record of its input was not found, so the file and the code "
                       "cannot be told apart as the cause."}[input_status]


def reproduce_line(cmp: Mapping[str, Any], company: str) -> str:
    rec, best = cmp.get("recorded"), cmp.get("best_control")
    run = cmp.get("run") or "(no run id found)"
    if cmp["status"] == "not_recorded":
        return f"D002's figures for {company} were not found in {cmp['source']}, so nothing is compared."
    said = f"drafted {rec['drafted']} and {READER_NAMES[best]} {rec['best_control']}"
    if cmp["status"] == "reproduced":
        return (f"These reproduce D002's run {run} for {company}: it recorded {said}. The demo gives the same to "
                "three places.")
    if cmp["status"] == "differs":
        return (f"These differ from D002's run {run} for {company}, which recorded {said}. "
                + why_differs(cmp.get("input", "unknown")))
    return (f"These settings are not D002's (their sha256 is not the one {RUN_FILE} names), so the numbers are not "
            f"D002's. D002's run {run} recorded {said} for {company}.")


def blocks_read(data: Mapping[str, Any], company: str) -> list[dict[str, Any]]:
    s, cmp = data["sample"], data["d002"]
    first, last = data["window"]
    rec = cmp.get("recorded") or {}
    best = cmp.get("best_control")
    rows = []
    for r in S.READERS:
        m = data["readers"][r]
        d002 = "not recorded"
        if r == "drafted" and rec:
            d002 = rec["drafted"]
        elif r == best and rec:
            d002 = rec["best_control"]
        rows.append((READER_NAMES[r], f3(m["f1"]), f"{f3(m['ci_low'])} to {f3(m['ci_high'])}", d002))
    dif = data["difference"]
    return [
        text_block(f"Held-out years {first} to {last}. {s['drawn']} records drawn from {n(s['eligible'])} eligible "
                   "(a narrative, a filed category that is a predicate, no copy of a training narrative).",
                   "Each reader sees only the narrative. The filed category is the answer key.",
                   f"{data['label_in_text']} of the {s['drawn']} narratives hold their own filed label."),
        table_block(("Reader", "Micro F1", "95% interval", f"D002 for {company}"), rows,
                    title=f"Micro F1, with a bootstrap interval over these records ({n(data['bootstrap_B'])} draws):"),
        text_block(f"Drafted minus the best control here ({READER_NAMES[dif['against']]}): {f3(dif['diff'])}, 95% "
                   f"interval {f3(dif['ci_low'])} to {f3(dif['ci_high'])}.",
                   reproduce_line(cmp, company)),
    ]


# --------------------------------------------------------------------------------------------------- (d) the audit

def mine_labels(sites: Iterable[str]) -> dict[str, str]:
    """m01 onwards, in the order of the mine ids (the order the audit runs its sites in). Never a mine id."""
    ordered = sorted(set(sites))
    width = max(2, len(str(len(ordered))))
    return {site: f"m{rank:0{width}d}" for rank, site in enumerate(ordered, start=1)}


class Watch:
    """While the labelled audit runs: the cells each site sent (the audit's own collective store holds every weekly
    cell that left a site), the bytes that left the sites (HQ's receive log), the X and S alerts as the audit computed
    them, sites included, and the cells each of those runs reads at HQ, so that the mines behind an alert are counted
    from what left them (:meth:`cell_sites`). Read-only; the audit's result is unchanged, and every patched function
    is put back."""

    def __init__(self) -> None:
        self.cells: dict[str, dict[str, int]] = {}
        self.sent: str | None = None
        self.sent_bytes = 0
        self.bundles = 0
        self.alerts: dict[str, list[dict[str, Any]]] = {}
        self.cell_weeks: dict[str, dict[str, list[tuple[str, str]]]] = {}
        self.weeks: list[str] = []
        self.window = 0
        self._hq: dict[str, Any] | None = None

    def cell_sites(self, run_channel: str, alert: Mapping[str, Any]) -> set[str]:
        """The sites with a cell at HQ, of the channels ``run_channel`` reads, for the alert's key in the detectors'
        window up to the alert's week: the weeks :func:`.audit._alerts` takes its sites from."""
        at = self.weeks.index(alert["week"])
        lo = self.weeks[max(0, at - self.window + 1)]
        return {site for week, site in self.cell_weeks.get(run_channel, {}).get(alert["key"], ())
                if lo <= week <= alert["week"]}

    @contextlib.contextmanager
    def active(self) -> Any:
        run_pipeline, hq_results, alerts = A.run_pipeline, A.hq_results, A._alerts

        def watched_pipeline(*args: Any, **kwargs: Any) -> Any:
            pipeline = run_pipeline(*args, **kwargs)
            bundles, cells = pipeline.store.detection_inputs(pipeline.as_of, "X")
            for b in bundles:
                self.cells.setdefault(b.site, {"bundles": 0, "cells": 0, "suppressed": 0})["bundles"] += 1
            for c in cells:
                entry = self.cells.setdefault(c.site, {"bundles": 0, "cells": 0, "suppressed": 0})
                entry["cells"] += 1
                entry["suppressed"] += int(c.n is None)
            self.bundles = len(bundles)
            self.weeks, self.window = list(pipeline.weeks), pipeline.pack.detectors["window_weeks"]
            for name in COUNT_CHANNELS:
                keyed: dict[str, list[tuple[str, str]]] = {}
                for c in pipeline.store.detection_inputs(pipeline.as_of, name)[1]:
                    keyed.setdefault(f"{c.entity_type}:{c.entity_id}:{c.predicate}", []).append((c.iso_week, c.site))
                self.cell_weeks[name] = keyed
            workdir = kwargs.get("workdir")
            log = Path(workdir) / "hq" / "receive.jsonl" if workdir is not None else None
            if log is not None and log.is_file():
                data = log.read_bytes()
                self.sent, self.sent_bytes = data.decode("utf-8"), len(data)
            return pipeline

        def watched_hq(*args: Any, **kwargs: Any) -> Any:
            self._hq = hq_results(*args, **kwargs)
            return self._hq

        def watched_alerts(result: Any, pipeline: Any, run_channel: str, evaluated_from: str) -> Any:
            items = alerts(result, pipeline, run_channel, evaluated_from)
            if self._hq is not None and run_channel in COUNT_CHANNELS and result is self._hq.get(run_channel):
                self.alerts[run_channel] = items
            return items

        A.run_pipeline, A.hq_results, A._alerts = watched_pipeline, watched_hq, watched_alerts
        try:
            yield self
        finally:
            A.run_pipeline, A.hq_results, A._alerts = run_pipeline, hq_results, alerts


def relabelled(doc: Mapping[str, Any], labels: Mapping[str, str]) -> dict[str, Any]:
    """An audit document with every site id replaced by its label (the export's sites and each review item's)."""
    out = copy.deepcopy(dict(doc))
    out["export"]["sites"] = sorted(labels[s] for s in out["export"]["sites"])
    for e in out["review"]:
        e["sites"] = sorted(labels[s] for s in e["sites"])
    return out


def canonical(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, default=str)


def step_audit(settings: Mapping[str, Any], export: Any, b: Mapping[str, Any]) -> dict[str, Any]:
    """D002's M4 with the mines labelled: the normalised export of the held-out years (``onboard export``), each mine
    id replaced by its label, and the pilot audit with an outcomes file of no rows, every audit setting at its
    default. Then the same audit under the mines' own ids, D002's M4 path, and whether the two agree."""
    spec = settings["arms"][ARM]
    d, pack = b["draft"], b["pack"]
    test = D.parse_window(*spec["test"])
    rows, rejected = D.normalised_rows(export, b["roles"], d.dated, test, b["template"], d.etypes)
    key = b["template"]["ids.json"]["normalised"]["site"]
    labels = mine_labels(r[key] for r in rows)
    named = [{**r, key: labels[r[key]]} for r in rows]
    mapped = map_rows(named, pack)
    watch = Watch()
    with watch.active():
        doc = A.audit(pack, named, [])
    own = A.audit(pack, rows, [])
    same = canonical(relabelled(own, labels)) == canonical(doc)
    for name in COUNT_CHANNELS:
        summary = doc["channels"][name]["summary"]
        if name not in watch.alerts or summary is None or len(watch.alerts[name]) != summary["alerts"]:
            raise DemoError(f"the {name} alerts could not be read from the audit")
    own_summary = {"records": own["export"]["records"], "sites": len(own["export"]["sites"]),
                   "weeks": own["weeks"]["evaluated_weeks"], "review_list": len(own["review"])}
    return {"doc": doc, "watch": watch, "same_under_own_ids": same, "own": own_summary,
            "normalised_rejected": rejected, "mapping_rejected": dict(mapped.rejected), "k": pack.egress.k}


def review_parts(doc: Mapping[str, Any], alerts: Mapping[str, Sequence[Mapping[str, Any]]],
                 label: Callable[[str], str], cell_sites: Callable[[str, Mapping[str, Any]], set[str]]
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The review list in two parts. First, each item X or S raised, with the weeks of its X and S alerts and the
    mines that sent a cell of it, in the window up to one of them, to HQ (``cell_sites``, from the cells those runs
    read). Those mines must be the ones the audit took from the sites' own stores for the same alerts, or nothing is
    shown. Second, each item R_mf alone raised, as the audit lists it. An item never moves from the second part to the
    first."""
    keyed = {name: [a for a in alerts.get(name, ())] for name in COUNT_CHANNELS}
    counted, reference = [], []
    for e in doc["review"]:
        by = [name for name in e["channels"] if name in COUNT_CHANNELS]
        if by:
            hits = [(name, a) for name in by for a in keyed[name] if a["key"] == e["key"]]
            if not hits:
                raise DemoError("an X or S item of the review list has no X or S alert")
            mines: set[str] = set()
            for name, a in hits:
                sent = cell_sites(name, a)
                if sent != set(a["sites"]):
                    raise DemoError("the mines behind an X or S alert differ between HQ's cells and the audit")
                mines |= sent
            weeks = sorted({a["week"] for _, a in hits})
            counted.append({"category": label(e["predicate"]), "channels": by,
                            "also": [name for name in e["channels"] if name not in COUNT_CHANNELS],
                            "first_week": weeks[0], "last_week": weeks[-1], "alerts": len(hits),
                            "mines": len(mines)})
        else:
            weeks = sorted(e["alert_weeks"])
            reference.append({"category": label(e["predicate"]), "channels": list(e["channels"]),
                              "first_week": weeks[0], "last_week": weeks[-1], "mines": len(e["sites"])})
    raised = {a["key"] for name in COUNT_CHANNELS for a in keyed[name]}
    if raised != {e["key"] for e in doc["review"] if set(e["channels"]) & set(COUNT_CHANNELS)}:
        raise DemoError("the X and S alerts do not match the review list")
    return counted, reference


def data_audit(a: Mapping[str, Any], pack: Any, shown: bool) -> dict[str, Any]:
    doc, watch = a["doc"], a["watch"]
    sites = list(doc["export"]["sites"])
    vocab = pack.predicates
    codes = {c.predicate: code for code, c in pack.codes.items()}

    def label(predicate: str) -> str:
        return vocab[predicate].label if shown and predicate in vocab else codes.get(predicate, "a predicate")

    per_mine = []
    for site in sites:
        c = watch.cells.get(site, {"bundles": 0, "cells": 0, "suppressed": 0})
        per_mine.append({"mine": site, "weekly_bundles": c["bundles"], "cells": c["cells"],
                         "suppressed_cells": c["suppressed"]})
    alerts = {}
    for name in A.channel_names(doc):
        s = doc["channels"][name]["summary"]
        alerts[name] = s["alerts"] if s is not None else None
    counted, reference = review_parts(doc, watch.alerts, label, watch.cell_sites)
    det = pack.detectors
    w = doc["weeks"]
    return {"records": doc["export"]["records"], "mines": len(sites), "weeks_evaluated": w["evaluated_weeks"],
            "evaluated_from": w["evaluated_from"], "evaluated_to": w["last"], "k": a["k"],
            "rows_rejected": sum(a["normalised_rejected"].values()) + sum(a["mapping_rejected"].values()),
            "per_mine": per_mine, "cells": sum(m["cells"] for m in per_mine),
            "suppressed_cells": sum(m["suppressed_cells"] for m in per_mine),
            "sent": {"bytes": watch.sent_bytes, "bundles": watch.bundles},
            "same_under_own_ids": a["same_under_own_ids"], "own_ids": dict(a["own"]), "alerts": alerts,
            "outcomes": doc["outcomes"]["given"],
            "window_weeks": det["window_weeks"],
            "min_sites": min(det["burst"]["min_sites"], det["cooccurrence"]["min_sites"]),
            "review": {"total": len(doc["review"]), "from_cells": counted, "reference_only": reference}}


def compare_audit(company: str, own: Mapping[str, Any], record: Mapping[str, Any], is_d002: bool) -> dict[str, Any]:
    """D002's M4 counts against the same audit under the mines' own ids (D002's path)."""
    rec = record.get("audit")
    if not rec or rec["company"] != company:
        return {"status": "not_recorded", "recorded": None, "run": record.get("run")}
    demo = {"records": n(own["records"]), "sites": n(own["sites"]), "weeks": n(own["weeks"]),
            "review_list": n(own["review_list"])}
    same = demo == {k: rec[k] for k in demo}
    return {"status": "not_comparable" if not is_d002 else ("reproduced" if same else "differs"),
            "recorded": rec, "demo": demo, "run": record.get("run")}


def counted_item(r: Mapping[str, Any], window: int) -> str:
    also = f" ({joined(r['also'])} also raised it)" if r["also"] else ""
    return (f"{r['category']}: {joined(r['channels'])} alerts in {weeks_text(r['first_week'], r['last_week'])}; "
            f"{r['mines']} mines sent a cell of it in the {window} weeks up to one of them{also}")


def reference_item(r: Mapping[str, Any], window: int) -> str:
    return (f"{r['category']}: {joined(r['channels'])} alerts in {weeks_text(r['first_week'], r['last_week'])}; "
            f"{r['mines']} mines with records of it in the {window} weeks up to one of them")


def own_ids_line(data: Mapping[str, Any]) -> str:
    """Whether the audit under the mines' own ids (D002's M4 path) gave the labelled run's result, and its figures
    when it did not: D002's recorded counts are compared with that run."""
    if data["same_under_own_ids"]:
        return ("The same audit under the mines' own ids, as D002's M4 ran it, gives the same alerts, review list and "
                "counts.")
    o = data["own_ids"]
    return (f"The same audit under the mines' own ids, as D002's M4 ran it, gives a different result: "
            f"{n(o['records'])} records, {n(o['sites'])} mines, {n(o['weeks'])} weeks, a review list of "
            f"{n(o['review_list'])}. D002's figures are compared with that run.")


def blocks_audit(data: Mapping[str, Any], cmp: Mapping[str, Any], company: str,
                 operators: int) -> list[dict[str, Any]]:
    k, window = data["k"], data["window_weeks"]
    labels = [m["mine"] for m in data["per_mine"]]
    named = labels[0] if len(labels) == 1 else f"{labels[0]} to {labels[-1]}" if labels else "none"
    lines = [f"{company}'s held-out years through the audit: {n(data['records'])} records at {data['mines']} mines, "
             f"{data['weeks_evaluated']} weeks evaluated ({data['evaluated_from']} to {data['evaluated_to']}). "
             "The weeks before build each series' history.",
             f"Each mine runs as its own site inside this process, built from {company}'s export on this machine, "
             "and nothing is sent over a network. What left a mine is what crossed from its site's store to HQ's, as "
             "HQ's receive log holds it.",
             f"Each mine's cells carry its label ({named}), never its id. " + own_ids_line(data)]
    rec = cmp.get("recorded")
    if cmp["status"] != "not_recorded":
        said = (f"{rec['records']} records, {rec['sites']} mines, {rec['weeks']} weeks, a review list of "
                f"{rec['review_list']}")
        lines.append({"reproduced": f"D002's run {cmp['run']} recorded {said} for {company}: the same.",
                      "differs": f"D002's run {cmp['run']} recorded {said} for {company}: these differ.",
                      "not_comparable": f"D002's run {cmp['run']} recorded {said} for {company}, with D002's "
                                        "settings; these settings differ."}[cmp["status"]])
    out = [text_block(*lines)]
    out.append(table_block(
        ("Mine", "Weekly bundles", "Cells", f"Cells with a count under {k}"),
        [(m["mine"], n(m["weekly_bundles"]), n(m["cells"]), n(m["suppressed_cells"])) for m in data["per_mine"]],
        title=(f"What left each mine: weekly cells only, under its label, one per category, week and channel that "
               f"had a record. A cell holds counts (a count under {k} leaves as '<k', with k = {k} in its bundle) "
               f"and, with {k} or more records, the lowest match confidence, one of the pack's fixed levels. The "
               f"{n(data['sent']['bytes'])} bytes that left were scanned for the record ids, mine ids and refused "
               f"values of the {operators} operators in the split, as D002's guard reads them: none found. No "
               "narrative or record id leaves.")))
    alerts = data["alerts"]
    counted = [f"{name} {n(alerts[name])}" for name in COUNT_CHANNELS if alerts.get(name) is not None]
    refs = [f"{name} {n(alerts[name])}" for name in (REFERENCE_CHANNEL, *COMPARATORS) if alerts.get(name) is not None]
    notrun = [name for name, v in alerts.items() if v is None]
    alert_lines = [f"Alerts from the weekly cells alone: {', '.join(counted) or 'none run'}. X reads the codes and "
                   f"text cells, S the codes cells only. Each raises an alert only when a category's counts rise at "
                   f"{data['min_sites']} or more mines in the same weeks."]
    if refs:
        alert_lines.append(f"Reference channels, which count record-level codes centrally and are not what left the "
                           f"mines: {', '.join(refs)}.")
    if notrun:
        alert_lines.append(f"Not run: {', '.join(notrun)}.")
    alert_lines.append(f"Outcomes given: {data['outcomes']}. So every X, S and R_mf alert lands on the review list, "
                       "grouped by pattern; P and PRR add nothing to it.")
    out.append(text_block(*alert_lines))
    review = data["review"]
    out.append(list_block([counted_item(r, window) for r in review["from_cells"]] or ["None."],
                          title=f"The review list, from the weekly cells (X or S): {len(review['from_cells'])} of "
                                f"{review['total']}."))
    out.append(list_block([reference_item(r, window) for r in review["reference_only"]] or ["None."],
                          title=f"Reference only, raised by R_mf alone: {len(review['reference_only'])} of "
                                f"{review['total']}. They need record-level codes counted centrally, which this setup "
                                "does not send."))
    return out


def blocks_means(data: Mapping[str, Any], company: str) -> list[dict[str, Any]]:
    review = data["review"]
    return [text_block(REVIEW_MEANS.format(counted=len(review["from_cells"]), min_sites=data["min_sites"],
                                           company=company, reference=len(review["reference_only"])))]


# --------------------------------------------------------------------------------------------------- the guard

def refused_index(exports: Iterable[Any], roles: Any, params: Mapping[str, Any]) -> tuple[D.ValueIndex, frozenset[str]]:
    """Every value of a refused column of at least four characters that holds a letter, or of at least five (the
    lengths of D002's refusal), folded, over the given exports; and every word of a refused value that a drafted term
    could hold (letters only, at least ``token_min_letters`` of them), short ones and single-word values included."""
    p = params["refusal"]
    values: set[str] = set()
    words: set[str] = set()
    for export in exports:
        _, forbidden = D.refused_values(export, roles)
        for v in forbidden:
            f = folded(v)
            if (len(f) >= p["forbidden_inside_min_chars"] and any(ch.isalpha() for ch in f)) \
                    or len(f) >= p["reference_inside_min_chars"]:
                values.add(f)
            words.update(w for w in D.words_of(f) if w.isalpha() and len(w) >= params["token_min_letters"])
    return D.ValueIndex(values), frozenset(words)


def printed_strings_entry(b: Mapping[str, Any], c: Mapping[str, Any] | None) -> dict[str, Any]:
    """The operator's record strings in the shape of an arm file's company entry, for D002's last guard: the category
    labels and each predicate's first terms (the only terms the screen can print), shown or withheld."""
    entry: dict[str, Any] = {"draft": b["draft"].facts, "pack": {"loader": {"error": b["loader_error"]}},
                             "controls": {"majority_prior": {"predicate": c["majority_predicate"]} if c else {}}}
    if not b["check"]["passed"]:
        entry["strings_withheld"] = True
    return entry


class Guard:
    """D002's last guard over the printed record strings, its backstop over the whole rendered text and the bytes
    that left the mines, and a scan for refused-column values. Built from the operator's own export (the last guard,
    its narratives and the words they write like a name) and every operator's export in the split (the backstop and
    the scan)."""

    def __init__(self, settings: Mapping[str, Any], own: Any, others: Iterable[Any]) -> None:
        self.settings = settings
        self.roles = S.arm_roles(settings, ARM)
        params = settings["params"]
        exports = [own, *others]
        self.operators = len(exports)
        self.ngram = settings["report_guard"]["ngram"]
        self.sentinels = R.company_sentinels(own, self.roles, params)
        self.backstop = R.Backstop(params["refusal"]["reference_inside_min_chars"])
        for e in exports:
            self.backstop.add(e, self.roles)
        self.refused, self.refused_words = refused_index(exports, self.roles, params)
        self.names, self.caps = name_shaped_words(self.sentinels.narratives)

    def strings(self, entry: Mapping[str, Any]) -> dict[str, int]:
        prefix = D.load_template()["ids.json"]["placeholder_prefix"]
        return R.company_hits(entry, self.sentinels, self.ngram, prefix)

    def texts(self, texts: Sequence[str]) -> dict[str, int]:
        found = self.backstop.hits(texts)
        found["refused_value"] = len(self.refused.found_all("\n".join(folded(t) for t in texts)))
        return found

    def sent(self, text: str | None) -> dict[str, int]:
        """The bytes that left the mines: every record id, mine id and refused value of the split. Unread bytes are
        a hit: the claim that none left is then not made."""
        if text is None:
            return {"sent_unread": 1}
        return {f"sent_{k}": v for k, v in self.texts([text]).items()}

    def message(self, text: str) -> str:
        """An error text, or nothing of it when it holds a record id, a mine id, a refused value or one of its
        words, eight words of a narrative, or a word that is not a plain word of the narratives."""
        found = self.texts([text])
        found["refused_own"] = int(self.sentinels.refusal.term(text) is not None)
        found["refused_word"] = int(any(w in self.refused_words for w in D.words_of(folded(text))))
        found["narrative_ngrams"] = len(ngram_hits([text], self.sentinels.narratives, self.ngram))
        found["name_shaped"] = int(name_shaped(text, self.names))
        return text if not any(found.values()) else WITHHELD_ERROR

    def summary(self) -> dict[str, int]:
        return {"record_ids": len(self.backstop.values["report_record_id"]),
                "mine_ids": len(self.backstop.values["report_site"]),
                "refused_values": sum(len(v) for v in self.refused.by_run.values()) + len(self.refused.general),
                "refused_words": len(self.refused_words), "operators": self.operators,
                "narratives_in_capitals": self.caps}


def read_split(split_dir: Path, companies: Mapping[str, Any], skip: str | None) -> tuple[list[Any], int]:
    """Every export of the split but ``skip``'s, and how many could not be read."""
    exports, unread = [], 0
    for label, entry in sorted(companies.items()):
        if label == skip:
            continue
        try:
            exports.append(read_export(split_dir / entry["file"]))
        except (ExportError, OSError, KeyError, TypeError):
            unread += 1
    return exports, unread


def error_guard(settings: Mapping[str, Any], split_dir: Path, company: str) -> Guard | None:
    """A guard for an error text from step (a): the split's exports, the operator's own first. None (the text is then
    withheld) when the split wrote none, or any of them cannot be read."""
    try:
        companies = json.loads((split_dir / "companies.json").read_text(encoding="utf-8"))
        own = read_export(split_dir / companies[company]["file"])
    except (OSError, ValueError, KeyError, TypeError, ExportError):
        return None
    others, unread = read_split(split_dir, companies, company)
    return None if unread else Guard(settings, own, others)


def guarded(err: BaseException, guard: Guard | None) -> str:
    return f"{err.__class__.__name__}: {UNCHECKED_ERROR if guard is None else guard.message(str(err))}"


# --------------------------------------------------------------------------------------------------- rendering

def render_console(doc: Mapping[str, Any]) -> str:
    out = [doc["title"], doc["subtitle"], ""]
    out += [f"  {line}" for line in doc["header"]]
    for step in doc["steps"]:
        head = f"({step['id']}) {step['title']}"
        if step.get("seconds") is not None:
            head += f"  [{secs(step['seconds'])}]"
        out += ["", head]
        for block in step["blocks"]:
            if block.get("title"):
                out.append(f"  {block['title']}")
            if block["kind"] == "text":
                out += [f"  {line}" for line in block["lines"]]
            elif block["kind"] == "list":
                out += [f"    - {item}" for item in block["items"]]
            else:
                widths = [max(len(str(r[i])) for r in [block["columns"], *block["rows"]])
                          for i in range(len(block["columns"]))]
                for r in [block["columns"], *block["rows"]]:
                    out.append("    " + "  ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)).rstrip())
    out += ["", "What this does not show:"] + [f"  - {line}" for line in doc["not_shown"]]
    out += ["", doc["footer"]]
    return "\n".join(out) + "\n"


CSS = """
:root { color-scheme: light dark; --bg: #f7f6f2; --card: #ffffff; --fg: #1c1e21; --muted: #5d636b; --line: #e2e0d8;
  --accent: #2c6a58; --pill: #e7f0ec; --warn: #8a4b0f; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #121416; --card: #1b1e21; --fg: #e8eaed; --muted: #a0a7b0; --line: #2e3338; --accent: #86c9b3;
    --pill: #1f302a; --warn: #f2b06b; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 880px; margin: 0 auto; padding: 28px 16px 56px; }
h1 { font-size: 1.6rem; line-height: 1.25; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 18px; }
.meta { color: var(--muted); font-size: .9rem; margin: 0 0 8px; padding-left: 0; list-style: none; }
.meta li { margin: 2px 0; overflow-wrap: anywhere; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px;
  margin: 16px 0; }
h2 { font-size: 1.1rem; margin: 0 0 10px; display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.step { color: var(--accent); font-variant-numeric: tabular-nums; }
.time { margin-left: auto; font-size: .85rem; font-weight: 600; color: var(--accent); background: var(--pill);
  border-radius: 999px; padding: 2px 10px; font-variant-numeric: tabular-nums; }
p { margin: 6px 0; }
.btitle { color: var(--muted); font-size: .92rem; margin: 12px 0 6px; }
ul { margin: 6px 0; padding-left: 20px; }
li { margin: 4px 0; overflow-wrap: anywhere; }
.wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; font-size: .95rem; }
th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; }
.note { border-color: var(--line); background: transparent; }
.withheld { border-color: var(--warn); }
.withheld h2 { color: var(--warn); }
footer { color: var(--muted); font-size: .85rem; margin-top: 20px; overflow-wrap: anywhere; }
"""


def render_html(doc: Mapping[str, Any]) -> str:
    e = html.escape
    parts = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta name="color-scheme" content="light dark">', f"<title>{e(doc['title'])}</title>",
             f"<style>{CSS}</style>", "</head>", "<body>", "<main>", f"<h1>{e(doc['title'])}</h1>",
             f'<p class="sub">{e(doc["subtitle"])}</p>', '<ul class="meta">']
    parts += [f"<li>{e(line)}</li>" for line in doc["header"]]
    parts.append("</ul>")
    for step in doc["steps"]:
        cls = ' class="withheld"' if step["id"] == "!" else ""
        timing = f'<span class="time">{e(secs(step["seconds"]))}</span>' if step.get("seconds") is not None else ""
        parts.append(f'<section{cls}><h2><span class="step">({e(step["id"])})</span> {e(step["title"])}'
                     f"{timing}</h2>")
        for block in step["blocks"]:
            if block.get("title"):
                parts.append(f'<p class="btitle">{e(block["title"])}</p>')
            if block["kind"] == "text":
                parts += [f"<p>{e(line)}</p>" for line in block["lines"]]
            elif block["kind"] == "list":
                parts.append("<ul>" + "".join(f"<li>{e(item)}</li>" for item in block["items"]) + "</ul>")
            else:
                head = "".join(f"<th>{e(c)}</th>" for c in block["columns"])
                body = "".join("<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in r) + "</tr>" for r in block["rows"])
                parts.append(f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>'
                             "</table></div>")
        parts.append("</section>")
    parts.append('<section class="note"><h2>What this does not show</h2><ul>'
                 + "".join(f"<li>{e(line)}</li>" for line in doc["not_shown"]) + "</ul></section>")
    parts += [f"<footer>{e(doc['footer'])}</footer>", "</main>", "</body>", "</html>"]
    return "\n".join(parts) + "\n"


def json_text(doc: Mapping[str, Any]) -> str:
    return json.dumps(S.rounded(doc), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------------------------------- the run

def withheld_doc(company: str, hits: Mapping[str, Any]) -> dict[str, Any]:
    """What replaces every output when the guard finds something: fixed text and the hit counts by kind, nothing
    else from the run (no label, term, count, time or hash)."""
    counts = {k: int(v) for k, v in hits.items() if isinstance(v, int) and not isinstance(v, bool)}
    kinds = ", ".join(f"{k} {v}" for k, v in counts.items() if v)
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "status": "withheld", "title": TITLE, "company": company,
            "subtitle": f"MSHA mine accidents, public data. Operator {company}.",
            "header": ["The guard found a record value in what would be shown, or could not check it. Nothing from "
                       "the run is shown."],
            "steps": [{"id": "!", "title": "Withheld", "seconds": None,
                       "blocks": [text_block(f"Hits by kind: {kinds}. Every output is withheld. "
                                             "The exit code is 1.")]}],
            "guard": counts, "not_shown": list(NOT_SHOWN), "footer": "Withheld by the guard."}


def code_line(settings: Mapping[str, Any]) -> str:
    """What is per-field here: the settings file's columns and the download code, counted from the files."""
    spec = settings["arms"][ARM]
    roles = S.arm_roles(settings, ARM)
    lines = R.script_lines(settings).get(FETCH)
    size = f"{n(lines)} lines" if lines is not None else "missing"
    return (f"The code: the drafter (the onboard package) names no column of this source. The settings file names "
            f"them: the {len(spec['columns'])} columns it expects, {len(roles.single())} roles, "
            f"{len(roles.forbidden)} refused columns, and {spec['companies']['column']}, which splits the file by "
            f"controller. {FETCH} ({size}) is MSHA's download and split code.")


def run(raw: Path, company: str, settings_path: Path, work: Path,
        emit: Callable[[str], None]) -> tuple[dict[str, Any], str, str, str]:
    """Every step; returns (demo document, console text, json text, html text)."""
    settings, sha = S.load_settings(settings_path)
    if ARM not in settings.get("arms", {}):
        raise DemoError(f"the settings hold no {ARM} arm")
    record = d002_record()
    recorded_input = d002_input()
    is_d002 = sha == run_file_sha()
    clock = Clock(emit)
    seconds: dict[str, float] = {}

    try:
        with clock.step("(a) read the export", seconds, "a"):
            export, a = step_export(settings_path, settings, raw, work, company)
    except DemoError:
        raise
    except Exception as err:                       # noqa: BLE001 (no traceback: it could print a record value)
        raise DemoError(guarded(err, error_guard(settings, work / "split", company))) from None
    try:
        with clock.step("(b) draft the pack", seconds, "b"):
            b = step_draft(settings, export, company, work)
        if b["pack"] is None:
            raise D.DraftError(f"the drafted pack does not load ({str(b['loader_error']).split(': ', 1)[0]})")
        with clock.step("(c) read the held-out years", seconds, "c"):
            c = step_read(settings, export, b, company, work)
        with clock.step("(d) count across the mines", seconds, "d"):
            d = step_audit(settings, export, b)
    except Exception as err:                       # noqa: BLE001 (no traceback: it could print a record value)
        # a later step's text could name a record value (the audit's pipeline names a site): it is shown only when
        # the guard finds none of the split's values in it
        others, unread = read_split(a["split_dir"], a["companies"], company)
        raise DemoError(guarded(err, None if unread else Guard(settings, export, others))) from None

    with clock.step("guard", seconds, "guard"):
        others, unread = read_split(a["split_dir"], a["companies"], company)
        guard = Guard(settings, export, others)
        string_hits = guard.strings(printed_strings_entry(b, c))
        sent_hits = guard.sent(d["watch"].sent)
        d["watch"].sent = None

    shown = bool(b["check"]["passed"])
    input_status = compare_input(a["input"], recorded_input)
    data_a = a["data"]
    data_b = data_draft(b, settings, guard.names, guard.refused_words, guard.caps)
    cmp_c = compare_reading(company, c["readers"], record, is_d002, input_status)
    data_c = data_read(c, cmp_c)
    data_d = data_audit(d, b["pack"], shown)
    cmp_d = compare_audit(company, d["own"], record, is_d002)
    data_d["d002"] = cmp_d
    operators = len(a["companies"])

    header = [f"Settings: {rel(settings_path)}, sha256 {sha[:12]}..., "
              + ("the settings D002's run file names." if is_d002 else f"not the settings {RUN_FILE} names."),
              code_line(settings),
              input_line(a["input"], input_status, recorded_input)]
    steps = [
        {"id": "a", "title": "Read the export", "seconds": seconds["a"], "blocks": blocks_export(data_a, company),
         "data": data_a},
        {"id": "b", "title": f"Draft a pack from {company}'s training years", "seconds": seconds["b"],
         "blocks": blocks_draft(data_b, company), "data": data_b},
        {"id": "c", "title": "Read the held-out years", "seconds": seconds["c"], "blocks": blocks_read(data_c, company),
         "data": data_c},
        {"id": "d", "title": f"Count across {company}'s mines", "seconds": seconds["d"],
         "blocks": blocks_audit(data_d, cmp_d, company, operators), "data": data_d},
        {"id": "e", "title": "What the review list means", "seconds": None, "blocks": blocks_means(data_d, company)},
    ]
    base: dict[str, Any] = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "status": "shown" if shown else "privacy_floor_failed",
        "title": TITLE, "subtitle": f"MSHA mine accidents, public data. Operator {company}, a field no pack covers.",
        "company": company,
        "settings": {"path": rel(settings_path), "sha256": sha, "experiment": settings.get("experiment"),
                     "is_d002": is_d002},
        "input": {**a["input"], "d002": input_status}, "code_commit": code_commit(), "header": header,
        "steps": steps, "not_shown": list(NOT_SHOWN), "footer": ""}
    g = guard.summary()
    guard_line = (f"Guard: {string_hits['strings']} printed strings from records checked against {company}'s own "
                  f"export (its record ids, mine ids, refused values and their name words, and any {guard.ngram} "
                  f"words of a narrative); {n(data_b['names']['withheld_terms'])} drafted terms withheld from the "
                  f"screen (a word of a refused value, or one written like a name or never in lower case); "
                  f"{n(g['record_ids'])} record ids, {n(g['mine_ids'])} mine ids and "
                  f"{n(g['refused_values'])} refused values of the {operators} operators in the split looked for in "
                  f"every output and in the bytes that left the mines. Found: none. Guard {secs(seconds['guard'])}.")
    base["footer"] = (f"{guard_line} Total {secs(clock.total())}. Code commit {base['code_commit'][:12]}. "
                      f"D002's figures are read from {record['source']} and {D002_INPUT}.")
    base["guard"] = {"strings": string_hits, **g, "unread": unread, "sent": sent_hits}
    base["seconds"] = dict(seconds, total=clock.total())

    hits = {**{k: v for k, v in string_hits.items() if k != "strings"}, "unread": unread, **sent_hits}
    if any(hits.values()):
        doc = withheld_doc(company, hits)
    else:
        doc = base
        found = guard.texts([render_console(doc), json_text(doc), render_html(doc)])
        if any(found.values()):
            doc = withheld_doc(company, found)
    return doc, render_console(doc), json_text(doc), render_html(doc)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python demo/onboard/run_demo.py", description=__doc__.split("\n")[0])
    p.add_argument("--raw", required=True, help="the directory fetch_msha.py download wrote")
    p.add_argument("--out", required=True, help="where demo.json and demo.html go")
    p.add_argument("--company", default="c1", help="the operator, c1 to c5 (default c1)")
    p.add_argument("--settings", default=DEFAULT_SETTINGS, help=f"the settings file (default {DEFAULT_SETTINGS})")
    p.add_argument("--work", help="where the split exports and the drafted pack go (record data; default: a "
                                  "temporary directory, deleted)")
    p.add_argument("--markers", action="store_true", help="also print demo.json between markers with its sha256")
    args = p.parse_args(argv)
    if COMPANY_RE.fullmatch(args.company) is None:
        print("error: --company is c1, c2 and so on", file=sys.stderr)
        return 2
    out = Path(args.out).resolve()
    settings_path = Path(args.settings)
    settings_path = settings_path if settings_path.is_absolute() else (Path.cwd() / settings_path)
    if not settings_path.is_file() and (ROOT / args.settings).is_file():
        settings_path = ROOT / args.settings

    def emit(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        with contextlib.ExitStack() as stack:
            if args.work:
                work = Path(args.work).resolve()
                if work == out or out in work.parents:
                    raise DemoError("--work holds record data: it cannot be --out or inside it")
                if work.exists() and any(work.iterdir()):
                    raise DemoError(f"{work} is not empty: pick a new --work")
            else:
                work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="onboard-demo-")))
            doc, console, text_json, text_html = run(Path(args.raw), args.company, settings_path, work, emit)
    except (DemoError, S.ScoreError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:                       # noqa: BLE001 (never a traceback: it could print a record value)
        print(f"error: {err.__class__.__name__} (its text is not shown)", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    data_json = text_json.encode("utf-8")
    (out / "demo.json").write_bytes(data_json)
    (out / "demo.html").write_text(text_html, encoding="utf-8")
    sys.stdout.write(console)
    if args.markers:
        print(R.block("demo.json", data_json, "onboard-demo"))
    print(f"wrote {out / 'demo.json'} and {out / 'demo.html'} (demo.json sha256 "
          f"{hashlib.sha256(data_json).hexdigest()})", file=sys.stderr)
    return {"shown": 0}.get(doc["status"], 1)


if __name__ == "__main__":
    sys.exit(main())

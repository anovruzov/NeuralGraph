#!/usr/bin/env python
"""The pack drafter on public data: one command, real records, D002's code.

    python tools/onboard/fetch_msha.py download --out RAW
    python demo/onboard/run_demo.py --raw RAW --out OUT [--company c1] [--settings FILE] [--work DIR] [--markers]

It points D002's pipeline (``docs/collective/onboard/CHOICE-D002.md``, run 38024536763) at MSHA's public accident
file, a field no pack covers, for one mine operator. It uses the existing code only, in D002's order:

* **(a) the export read:** ``tools/onboard/fetch_msha.py split`` with the settings (D002's by default) cuts the file
  into one export per operator, c1 to c5 by D002's rule; the operator's export is read by the drafter's own reader;
* **(b) the drafted pack:** :func:`mycelic.collective.onboard.draft.draft_export` over the training years, then the
  loader and the privacy floor (:func:`mycelic.collective.onboard.check.check_pack`);
* **(c) reading the held-out years:** the sample, the three controls and the metrics, with the functions of
  :mod:`mycelic.collective.onboard.score` in the order its per-company step calls them, so the numbers are D002's for
  that operator when the file and the settings are D002's. The figures D002 recorded are read from its choice file
  and shown beside them;
* **(d) the cross-mine audit:** the normalised export of the held-out years and
  :func:`mycelic.collective.pilot.audit.audit` with no outcomes, as D002's M4 ran it. What left each mine is counted
  from the audit's own collective store (the weekly cells), while the audit runs;
* **(e)** one line on what the review list means.

**What it never prints or writes:** a narrative or any record text, a record id, a mine id, an operator or controller
name or id. Operators are c1 to c5 (D002's labels); mines are m01 onwards. Before anything is printed or written, the
strings that came from records (category labels and drafted terms) go through D002's last guard against the operator's
own export (:func:`mycelic.collective.onboard.report.company_hits`), and the whole rendered text is scanned for every
record id and mine id of every operator in the split (D002's backstop, :class:`mycelic.collective.onboard.report.
Backstop`) and for every value of a refused column of at least four characters. A hit withholds everything but the hit
counts, and the exit code is 1.

``--work`` holds the split exports, the drafted pack and the controls: record data, never under ``--out``. Without it a
temporary directory is used and deleted. ``--out`` gets ``demo.json`` and ``demo.html`` (self-contained: no script,
no link, light and dark). The console version goes to stdout; one progress line per step goes to stderr. Exit codes:
0 shown; 1 withheld, or the drafted pack failed its privacy floor; 2 a usage, input or run error (its text shown
only when it names none of the operator's values; never a traceback).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import importlib.util
import io
import json
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mycelic.collective.experiments.common import code_commit  # noqa: E402
from mycelic.collective.onboard import draft as D  # noqa: E402
from mycelic.collective.onboard import report as R  # noqa: E402
from mycelic.collective.onboard import score as S  # noqa: E402
from mycelic.collective.onboard.check import check_pack  # noqa: E402
from mycelic.collective.onboard.exports import ExportError, read_export  # noqa: E402
from mycelic.collective.packs.canonical import folded  # noqa: E402
from mycelic.collective.packs.connector import map_rows  # noqa: E402
from mycelic.collective.pilot import audit as A  # noqa: E402

KIND = "onboard_drafter_demo"
ARM = "msha"
DEFAULT_SETTINGS = "docs/collective/onboard/D002-settings.json"
RUN_FILE = "docs/collective/onboard/run-002.json"
CHOICE = "docs/collective/onboard/CHOICE-D002.md"
FETCH = "tools/onboard/fetch_msha.py"
COMPANY_RE = re.compile(r"c[1-9][0-9]?", re.ASCII)
READER_NAMES = {"drafted": "drafted pack", "majority_prior": "majority prior", "permuted_labels": "permuted labels",
                "label_names": "label names"}
CHECK_NAMES = {"loader": "the loader", "term_floor": "the term floor", "value_floor": "the value floor",
               "refused_strings": "refused strings", "refused_terms": "refused terms", "template": "the template",
               "ngram": "narrative n-grams", "labels": "column-name labels"}
CHANNEL_NOTE = {"X": "codes and text cells", "S": "codes cells only"}
REFERENCE_CHANNELS = ("R_mf", "P", "PRR")
TITLE = "A pack drafted from an export alone"
NOT_SHOWN = (
    "No early warning is measured. The demo has no outcomes, so nothing here says the alerts come early or at all.",
    "The review list is unjudged. No one has checked whether any item is a real problem.",
    "The reading score uses the filed categories as the answer key. They are not checked labels.",
    "The data are public records written to a regulator, not a company's own files.",
    "Operators are c1 to c5 and mines m01 onwards. No name or id is shown.",
)
REVIEW_MEANS = ("The review list holds patterns seen at several of {company}'s mines that match no outcome on record, "
                "because the demo has no outcomes file. Only the operator's own people could say which are real.")


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


# --------------------------------------------------------------------------------------------------- (a) the export

def step_export(settings_path: Path, settings: Mapping[str, Any], raw: Path, work: Path,
                company: str) -> tuple[Any, dict[str, Any]]:
    """The split (``fetch_msha.py split``, its printed counts kept out of the console) and the operator's export,
    read by the drafter's reader."""
    fetch = load_fetch()
    for name, _ in fetch.FILES[:1]:
        if not (raw / name).is_file():
            raise DemoError(f"{raw / name} is missing: run python {FETCH} download --out {raw} first")
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
                     "date_format": source["date_format"], "operators": source["controllers"],
                     "qualifying": source["qualifying"], "used": source["used"],
                     "labels": sorted(companies, key=lambda c: int(c[1:]))},
            "export": {"company": company, "rows": len(export), "columns": len(export.columns),
                       "rejected": dict(export.rejected), "encoding": export.encoding,
                       **{k: rule[k] for k in ("training_rows_with_narrative", "test_rows_with_narrative",
                                               "training_mines")}},
            "roles": {**roles.single(), "refused": list(roles.forbidden)}}
    return export, {"data": data, "split_dir": split_dir, "companies": companies}


def blocks_export(data: Mapping[str, Any], company: str) -> list[dict[str, Any]]:
    f, e, r = data["file"], data["export"], data["roles"]
    rejected = sum(f["rejected"].values())
    used = f["labels"][0] if len(f["labels"]) == 1 else f"{f['labels'][0]} to {f['labels'][-1]}"
    return [
        text_block(f"MSHA's accident file: {n(f['rows'])} rows read, {n(rejected)} rejected, {f['columns']} columns. "
                   f"Dates read as {f['date_format']}.",
                   f"Operators in the file: {n(f['operators'])}. {n(f['qualifying'])} qualify by D002's rule; "
                   f"{f['used']} are used, {used} in D002's order.",
                   f"{company}'s export: {n(e['rows'])} rows, {e['columns']} columns, "
                   f"{n(sum(e['rejected'].values()))} rejected. {n(e['training_rows_with_narrative'])} training rows "
                   f"and {n(e['test_rows_with_narrative'])} held-out rows have a narrative, from "
                   f"{e['training_mines']} mines in the training years."),
        table_block(("Role", "Column"), [("record id", r["record_id"]), ("site (a mine)", r["site"]),
                                         ("date", r["date"]), ("narrative", r["narrative"]),
                                         ("filed category (the answer key)", r["category"])],
                    title="The roles come from the settings file: data, not code."),
        text_block(f"Values of {len(r['refused'])} columns are read only to refuse terms, and never shown: "
                   + ", ".join(r["refused"]) + ". Every other column is ignored."),
    ]


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


def data_draft(b: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    d, checked = b["draft"], b["check"]
    facts, params = d.facts, settings["params"]
    shown = bool(checked["passed"])
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
            "predicates": ([{"label": p["label"], "records": p["records"], "first_terms": list(p["first_terms"]),
                             "refused_assignable": p["refused_assignable"]} for p in facts["predicates"]] if shown
                           else [{"code": p["code"], "records": p["records"], "terms": p["terms"]}
                                 for p in facts["predicates"]])}


def predicate_line(p: Mapping[str, Any]) -> str:
    """One predicate exactly as D002's report prints it (``report.render``)."""
    return (f"{p['label']} ({p['records']} corpus records; {p['refused_assignable']} refused terms would have been "
            f"assigned): {', '.join(p['first_terms'])}")


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
        out.append(list_block([predicate_line(p) for p in data["predicates"]],
                              title=f"Predicates and their first terms, as D002's report prints them "
                                    f"({n(data['terms']['total'])} terms in all; {data['placeholders']} predicates "
                                    "learned none):"))
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


def compare_reading(company: str, readers: Mapping[str, Any], record: Mapping[str, Any],
                    is_d002: bool) -> dict[str, Any]:
    """The demo's drafted and best-control F1 for this operator against what D002 recorded, both to three places."""
    fig = record["companies"].get(company)
    best = record["best_control"]
    out: dict[str, Any] = {"run": record["run"], "source": record["source"], "recorded": fig, "best_control": best}
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
        return (f"These differ from D002's run {run} for {company}, which recorded {said}. The settings are D002's, "
                "so the input file or the code differs from that run's.")
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

@contextlib.contextmanager
def watching_cells(sink: dict[str, dict[str, int]]) -> Any:
    """While the audit runs, count the cells each site sent: the audit's own collective store holds every weekly cell
    that left a site. Read-only; the audit's result is unchanged."""
    original = A.run_pipeline

    def watched(*args: Any, **kwargs: Any) -> Any:
        pipeline = original(*args, **kwargs)
        bundles, cells = pipeline.store.detection_inputs(pipeline.as_of, "X")
        for b in bundles:
            sink.setdefault(b.site, {"bundles": 0, "cells": 0, "suppressed": 0})["bundles"] += 1
        for c in cells:
            entry = sink.setdefault(c.site, {"bundles": 0, "cells": 0, "suppressed": 0})
            entry["cells"] += 1
            entry["suppressed"] += int(c.n is None)
        return pipeline

    A.run_pipeline = watched
    try:
        yield sink
    finally:
        A.run_pipeline = original


def step_audit(settings: Mapping[str, Any], export: Any, b: Mapping[str, Any]) -> dict[str, Any]:
    """D002's M4: the normalised export of the held-out years (``onboard export``) and the pilot audit with an
    outcomes file of no rows, every audit setting at its default."""
    spec = settings["arms"][ARM]
    d, pack = b["draft"], b["pack"]
    test = D.parse_window(*spec["test"])
    rows, rejected = D.normalised_rows(export, b["roles"], d.dated, test, b["template"], d.etypes)
    mapped = map_rows(rows, pack)
    cells: dict[str, dict[str, int]] = {}
    with watching_cells(cells):
        doc = A.audit(pack, rows, [])
    return {"doc": doc, "cells": cells, "normalised_rejected": rejected, "mapping_rejected": dict(mapped.rejected),
            "k": pack.egress.k}


def mine_labels(sites: Sequence[str], cells: Mapping[str, Mapping[str, int]]) -> dict[str, str]:
    """m01 onwards, by cells sent (most first), ties in the audit's own order. Never a mine id."""
    order = sorted(range(len(sites)), key=lambda i: (-cells.get(sites[i], {}).get("cells", 0), i))
    width = max(2, len(str(len(sites))))
    return {sites[i]: f"m{rank:0{width}d}" for rank, i in enumerate(order, start=1)}


def data_audit(a: Mapping[str, Any], pack: Any, shown: bool) -> dict[str, Any]:
    doc, cells = a["doc"], a["cells"]
    sites = list(doc["export"]["sites"])
    labels = mine_labels(sites, cells)
    vocab = pack.predicates
    codes = {c.predicate: code for code, c in pack.codes.items()}

    def label(predicate: str) -> str:
        return vocab[predicate].label if shown and predicate in vocab else codes.get(predicate, "a predicate")

    per_mine = []
    for site in sorted(sites, key=lambda s: labels[s]):
        c = cells.get(site, {"bundles": 0, "cells": 0, "suppressed": 0})
        per_mine.append({"mine": labels[site], "weekly_bundles": c["bundles"], "cells": c["cells"],
                         "suppressed_cells": c["suppressed"]})
    alerts = {}
    for name in A.channel_names(doc):
        s = doc["channels"][name]["summary"]
        alerts[name] = s["alerts"] if s is not None else None
    review = []
    for e in doc["review"]:
        weeks = sorted(e["alert_weeks"])
        review.append({"category": label(e["predicate"]), "first_week": weeks[0], "last_week": weeks[-1],
                       "mines": len(e["sites"]), "channels": list(e["channels"])})
    w = doc["weeks"]
    return {"records": doc["export"]["records"], "mines": len(sites), "weeks_evaluated": w["evaluated_weeks"],
            "evaluated_from": w["evaluated_from"], "evaluated_to": w["last"], "k": a["k"],
            "rows_rejected": sum(a["normalised_rejected"].values()) + sum(a["mapping_rejected"].values()),
            "per_mine": per_mine, "cells": sum(m["cells"] for m in per_mine),
            "suppressed_cells": sum(m["suppressed_cells"] for m in per_mine), "alerts": alerts,
            "outcomes": doc["outcomes"]["given"], "review_list": review}


def compare_audit(company: str, data: Mapping[str, Any], record: Mapping[str, Any], is_d002: bool) -> dict[str, Any]:
    rec = record.get("audit")
    if not rec or rec["company"] != company:
        return {"status": "not_recorded", "recorded": None, "run": record.get("run")}
    demo = {"records": n(data["records"]), "sites": n(data["mines"]), "weeks": n(data["weeks_evaluated"]),
            "review_list": n(len(data["review_list"]))}
    same = demo == {k: rec[k] for k in demo}
    return {"status": "not_comparable" if not is_d002 else ("reproduced" if same else "differs"),
            "recorded": rec, "demo": demo, "run": record.get("run")}


def blocks_audit(data: Mapping[str, Any], cmp: Mapping[str, Any], company: str) -> list[dict[str, Any]]:
    k = data["k"]
    lines = [f"{company}'s held-out years through the audit: {n(data['records'])} records at {data['mines']} mines, "
             f"{data['weeks_evaluated']} weeks evaluated ({data['evaluated_from']} to {data['evaluated_to']}). "
             "The weeks before build each series' history."]
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
        title=(f"What left each mine: weekly counts only, one cell per category, week and channel. A count under {k} "
               f"leaves as '<{k}'. No narrative, record id or mine id leaves.")))
    ran = [f"{name} {n(v)}" for name, v in data["alerts"].items() if name in CHANNEL_NOTE and v is not None]
    refs = [f"{name} {n(v)}" for name, v in data["alerts"].items() if name in REFERENCE_CHANNELS and v is not None]
    notrun = [name for name, v in data["alerts"].items() if v is None]
    alert_lines = [f"Alerts from the weekly counts: {', '.join(ran)} (X reads the codes and text cells, S the codes "
                   "cells only)."]
    if refs:
        alert_lines.append(f"Reference channels that read record-level codes centrally: {', '.join(refs)}.")
    if notrun:
        alert_lines.append(f"Not run: {', '.join(notrun)}.")
    alert_lines.append(f"Outcomes given: {data['outcomes']}. So every alert lands on the review list.")
    out.append(text_block(*alert_lines))
    items = [f"{r['category']}: "
             + (f"week {r['first_week']}" if r["first_week"] == r["last_week"]
                else f"weeks {r['first_week']} to {r['last_week']}")
             + f", {r['mines']} mines, {joined(r['channels'])}" for r in data["review_list"]]
    out.append(list_block(items or ["None."], title=f"The review list ({len(data['review_list'])}):"))
    return out


# --------------------------------------------------------------------------------------------------- the guard

def refused_index(exports: Iterable[Any], roles: Any, params: Mapping[str, Any]) -> D.ValueIndex:
    """Every value of a refused column of at least four characters that holds a letter, or of at least five (the
    lengths of D002's refusal), folded, over the given exports."""
    p = params["refusal"]
    values: set[str] = set()
    for export in exports:
        _, forbidden = D.refused_values(export, roles)
        for v in forbidden:
            f = folded(v)
            if (len(f) >= p["forbidden_inside_min_chars"] and any(ch.isalpha() for ch in f)) \
                    or len(f) >= p["reference_inside_min_chars"]:
                values.add(f)
    return D.ValueIndex(values)


def printed_strings_entry(b: Mapping[str, Any], c: Mapping[str, Any] | None) -> dict[str, Any]:
    """The operator's printed record strings in the shape of an arm file's company entry, for D002's last guard."""
    entry: dict[str, Any] = {"draft": b["draft"].facts, "pack": {"loader": {"error": b["loader_error"]}},
                             "controls": {"majority_prior": {"predicate": c["majority_predicate"]} if c else {}}}
    if not b["check"]["passed"]:
        entry["strings_withheld"] = True
    return entry


class Guard:
    """D002's last guard over the printed record strings, its backstop over the whole rendered text, and a scan for
    refused-column values. Built from the operator's own export (the guard) and every operator's export in the split
    (the backstop and the scan)."""

    def __init__(self, settings: Mapping[str, Any], own: Any, others: Iterable[Any]) -> None:
        self.settings = settings
        self.roles = S.arm_roles(settings, ARM)
        params = settings["params"]
        exports = [own, *others]
        self.sentinels = R.company_sentinels(own, self.roles, params)
        self.backstop = R.Backstop(params["refusal"]["reference_inside_min_chars"])
        for e in exports:
            self.backstop.add(e, self.roles)
        self.refused = refused_index(exports, self.roles, params)

    def strings(self, entry: Mapping[str, Any]) -> dict[str, int]:
        prefix = D.load_template()["ids.json"]["placeholder_prefix"]
        return R.company_hits(entry, self.sentinels, self.settings["report_guard"]["ngram"], prefix)

    def texts(self, texts: Sequence[str]) -> dict[str, int]:
        found = self.backstop.hits(texts)
        found["refused_value"] = len(self.refused.found_all("\n".join(folded(t) for t in texts)))
        return found

    def message(self, text: str) -> str:
        """An error text, or nothing of it when it holds a record id, a mine id or a refused value."""
        return text if not any(self.texts([text]).values()) else "(its text is withheld: it names a record value)"

    def summary(self) -> dict[str, int]:
        return {"record_ids": len(self.backstop.values["report_record_id"]),
                "mine_ids": len(self.backstop.values["report_site"]),
                "refused_values": sum(len(v) for v in self.refused.by_run.values()) + len(self.refused.general)}


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
    return {"kind": KIND, "schema_version": 1, "status": "withheld", "title": TITLE, "company": company,
            "subtitle": f"MSHA mine accidents, public data. Operator {company}.",
            "header": ["The guard found a record value in what would be shown. Nothing from the run is shown."],
            "steps": [{"id": "!", "title": "Withheld", "seconds": None,
                       "blocks": [text_block(f"Hits by kind: {kinds}. Every output is withheld. "
                                             "The exit code is 1.")]}],
            "guard": counts, "not_shown": list(NOT_SHOWN), "footer": "Withheld by the guard."}


def run(raw: Path, company: str, settings_path: Path, work: Path,
        emit: Callable[[str], None]) -> tuple[dict[str, Any], str, str, str]:
    """Every step; returns (demo document, console text, json text, html text)."""
    settings, sha = S.load_settings(settings_path)
    if ARM not in settings.get("arms", {}):
        raise DemoError(f"the settings hold no {ARM} arm")
    record = d002_record()
    is_d002 = sha == run_file_sha()
    clock = Clock(emit)
    seconds: dict[str, float] = {}
    downloads = None
    if (raw / "download.json").is_file():
        downloads = json.loads((raw / "download.json").read_text(encoding="utf-8"))

    try:
        with clock.step("(a) read the export", seconds, "a"):
            export, a = step_export(settings_path, settings, raw, work, company)
    except (ValueError, KeyError, zipfile.BadZipFile) as err:       # the split's and the reader's own texts
        if isinstance(err, DemoError):
            raise
        raise DemoError(f"{err.__class__.__name__}: {err}") from None
    try:
        with clock.step("(b) draft the pack", seconds, "b"):
            b = step_draft(settings, export, company, work)
        if b["pack"] is None:
            raise D.DraftError(f"the drafted pack does not load ({b['loader_error']})")
        with clock.step("(c) read the held-out years", seconds, "c"):
            c = step_read(settings, export, b, company, work)
        with clock.step("(d) count across the mines", seconds, "d"):
            d = step_audit(settings, export, b)
    except Exception as err:                       # noqa: BLE001 (no traceback: it could print a record value)
        # a later step's text could name a record value (the audit's pipeline names a site): it is shown only when
        # the operator's own values are not in it
        raise DemoError(f"{err.__class__.__name__}: {Guard(settings, export, ()).message(str(err))}") from None

    shown = bool(b["check"]["passed"])
    data_a = a["data"]
    data_b = data_draft(b, settings)
    cmp_c = compare_reading(company, c["readers"], record, is_d002)
    data_c = data_read(c, cmp_c)
    data_d = data_audit(d, b["pack"], shown)
    cmp_d = compare_audit(company, data_d, record, is_d002)
    data_d["d002"] = cmp_d

    with clock.step("guard", seconds, "guard"):
        others = []
        for label, entry in sorted(a["companies"].items()):
            if label != company:
                with contextlib.suppress(ExportError):
                    others.append(read_export(a["split_dir"] / entry["file"]))
        guard = Guard(settings, export, others)
        string_hits = guard.strings(printed_strings_entry(b, c))

    d002_name = settings.get("experiment")
    header = [f"Settings: {rel(settings_path)}, sha256 {sha[:12]}..., "
              + ("the settings D002's run file names." if is_d002 else f"not the settings {RUN_FILE} names."),
              "The code: D002's pipeline (the onboard package, fetch_msha.py split, the pilot audit). It holds no code "
              "for this field: the roles are data in the settings file."]
    for item in downloads or ():
        header.append(f"Input: {item['file']}, {n(item['bytes'])} bytes, sha256 {item['sha256'][:12]}...")
    steps = [
        {"id": "a", "title": "Read the export", "seconds": seconds["a"], "blocks": blocks_export(data_a, company),
         "data": data_a},
        {"id": "b", "title": f"Draft a pack from {company}'s training years", "seconds": seconds["b"],
         "blocks": blocks_draft(data_b, company), "data": data_b},
        {"id": "c", "title": "Read the held-out years", "seconds": seconds["c"], "blocks": blocks_read(data_c, company),
         "data": data_c},
        {"id": "d", "title": f"Count across {company}'s mines", "seconds": seconds["d"],
         "blocks": blocks_audit(data_d, cmp_d, company), "data": data_d},
        {"id": "e", "title": "What the review list means", "seconds": None,
         "blocks": [text_block(REVIEW_MEANS.format(company=company))]},
    ]
    base: dict[str, Any] = {
        "kind": KIND, "schema_version": 1, "status": "shown" if shown else "privacy_floor_failed",
        "title": TITLE, "subtitle": f"MSHA mine accidents, public data. Operator {company}, a field no pack covers.",
        "company": company,
        "settings": {"path": rel(settings_path), "sha256": sha, "experiment": d002_name, "is_d002": is_d002},
        "input": downloads, "code_commit": code_commit(), "header": header, "steps": steps,
        "not_shown": list(NOT_SHOWN), "footer": ""}
    g = guard.summary()
    guard_line = (f"Guard: {string_hits['strings']} printed strings from records checked against {company}'s own "
                  f"export; {n(g['record_ids'])} record ids, {n(g['mine_ids'])} mine ids and {n(g['refused_values'])} "
                  f"refused values of the {len(a['companies'])} operators in the split looked for in every output. "
                  f"Found: none. Guard {secs(seconds['guard'])}.")
    base["footer"] = (f"{guard_line} Total {secs(clock.total())}. Code commit {base['code_commit'][:12]}. "
                      f"D002's figures are read from {record['source']}.")
    base["guard"] = {"strings": string_hits, **guard.summary()}
    base["seconds"] = dict(seconds, total=clock.total())

    hits = {k: v for k, v in string_hits.items() if k != "strings"}
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

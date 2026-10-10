"""The pilot without a hand-built pack: draft one from your own export, check it, and audit your history with it.

    python -m mycelic.collective.pilot.start run --export FILE --roles ROLES.json [--outcomes FILE] --out DIR
        [--train-until YYYY-MM-DD] [--work DIR] [--dry-run]
    python -m mycelic.collective.pilot.start example --out DIR [--seed 1] [--dry-run]

``example`` writes a synthetic export of a made-up company, its roles file and an outcomes file to try ``run`` on
(:func:`write_example`; its planted pattern and the detectors share an author). ``run`` chains code that exists,
unchanged, in the order ``demo/onboard/run_demo.py`` chains it, for any company and field:

1. **Read the export** with the drafter's reader (:func:`.onboard.exports.read_export`: CSV, pipe- or tab-delimited
   text with a header, or JSON lines; fields in double quotes). ``--roles`` names the export's own columns
   (:func:`load_roles`, checked strictly; an error names the missing role).
2. **Draft a pack** (:func:`.onboard.draft.draft_export`, D002's defaults) from the records dated on or before
   ``--train-until``. By default that is the date of the record at the three-quarter mark of the dated records in date
   order, so the last quarter (fewer when records share that date) comes after it (:func:`default_train_until`).
3. **Check it** (:func:`.onboard.check.check_pack`): the loader and the privacy floor of rule 1.7. When the floor fails
   the run stops: ``pilot.md`` and ``pilot.json`` then hold the check's counts and no label or term, and the exit
   code is 1.
4. **Audit the records after ``--train-until``** with the drafted pack (:func:`.audit.audit`, every setting at its
   default, comparators on), and with the issues in ``--outcomes`` when it is given. Each site value is replaced by a
   label, ``s01`` onwards in the sorted order of the site column's values, before the audit reads a row, so every
   bundle and cell that crosses from a site's store to HQ's carries a label. What each site sent is counted from the
   audit's own collective store while it runs (:class:`Watch`, which wraps three of the audit's functions read-only
   and puts them back).
5. **Write ``pilot.md`` and ``pilot.json``**: the drafted predicates and their first terms; the floor result; the
   weekly cells each site sent; the alerts by channel, the weekly-cell channels (X, S) apart from the reference
   channels (R_mf, P, PRR); the review list in the same two parts (items X or S raised, with the sites counted from
   HQ's cells, and items R_mf alone raised); with outcomes, each channel's issues found before opening against what
   the same alerts at random times find; and a fixed list of what the run does not show.

**The outcomes file** (optional) is a CSV with ``outcome_id``, ``opened`` (``YYYY-MM-DD``) and ``category``: the filed
category the issue was about, as the export writes it (matched after folding case and accents), or empty for any
category. Other columns are ignored. An issue whose category is not a drafted predicate is not scored, and is counted
by why. Issues are ``i01`` onwards, in the order of the file's rows, and are scored on the whole export (the pack's
scope entity), so an alert on any record of that category counts.

**The guard** (:class:`Guard`), before anything is written: the strings ``pilot.md`` prints that came from records
(category labels, predicate ids and first terms, entity ids) go through the onboard report's last guard against the
export (:func:`.onboard.report.company_hits`: its one refusal and every narrative's eight-word runs); the whole of
``pilot.md`` and every string of ``pilot.json`` are scanned for the export's record ids and site values
(:class:`.onboard.report.Backstop`, values of at least five characters), for every value of a refused column or site
of at least four characters holding a letter (or five), and for eight consecutive words of any narrative
(:func:`.onboard.check.ngram_hits`); and the bytes that left the sites for the same values. Numbers in ``pilot.json``
are counts the run computed and are not scanned; ``pilot.md`` prints every count of 1,000 or more with separators. A
hit replaces both files with the hit counts by kind and nothing else; the exit code is 1. An error text after the
export is read is shown only when the same scans find nothing in it.

``--work`` keeps record data: the drafted pack (``pack/``, which ``pilot.audit run --pack`` reads), the audit's own
``audit/audit.json`` and ``audit.md`` (which name record ids, under site and issue labels), ``sites.csv`` and
``issues.csv`` (label to your own value). It can never be ``--out`` or inside it. Without it a temporary directory is
used and deleted. Nothing is sent anywhere. Exit codes: 0 shown; 1 the privacy floor failed or the guard withheld the
report; 2 a usage, input or run error (never a traceback).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import dataclasses
import hashlib
import json
import random
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..experiments.common import DryRun
from ..jsonio import StrictJsonError, strict_load
from ..packs.canonical import folded
from ..onboard import draft as D
from ..onboard import report as R
from ..onboard.check import check_pack, json_strings, ngram_hits
from ..onboard.exports import Export, ExportError, is_list_column, read_export
from ..onboard.roles import SINGLE_ROLES, Roles, RolesError, roles_from_json
from . import audit as A

CLI = "pilot.start"
KIND = "pilot_start"
SCHEMA_VERSION = 1
PACK_ID = "drafted_pilot"
DEFAULT_LANGUAGE = "en"
HELD_OUT = Fraction(1, 4)
COUNT_CHANNELS = ("X", "S")                 # read only the weekly cells that left the sites
REFERENCE_CHANNELS = ("R_mf", "P", "PRR")   # record-level codes counted centrally; never what left a site
OUTCOME_COLUMNS = ("outcome_id", "opened", "category")
NOT_SCORED = ("other_bucket", "below_floor", "refused", "not_in_training")
LABEL = "local pilot: computed inside your environment from your own export; nothing is sent anywhere"
TITLE = "Pilot: a pack drafted from your own export"
REQUIRED_ROLES = (*SINGLE_ROLES, "forbidden")
OPTIONAL_ROLES = ("entities", "reporter")
ROLE_HELP = {
    "record_id": "the column holding each record's own id",
    "site": "the column naming the plant, branch, line or region each record comes from",
    "date": "the column holding each record's date",
    "narrative": "the column holding each record's free text",
    "category": "the column holding the category each record was filed under (a list column, name[], is allowed)",
    "forbidden": "the refused columns: names, ids and anything that must never be printed or learned; [] when none",
}
SCALAR_ROLES = ("record_id", "site", "date", "narrative")
CHECK_NAMES = {"loader": "the loader", "term_floor": "the term floor", "value_floor": "the value floor",
               "refused_strings": "refused strings", "refused_terms": "refused terms", "template": "the template",
               "ngram": "narrative n-grams"}
NOT_SCORED_SAYS = {"other_bucket": "in the other bucket (under the predicate minimum, or not a specific label)",
                   "below_floor": "under the privacy floor in the training records",
                   "refused": "refused (a string of it is a record id, site or refused value)",
                   "not_in_training": "not filed in any training record with a narrative"}
NOT_SHOWN = (
    "That any alert finds a real problem. The review list is unjudged: only your own people can say which item is a "
    "missed problem, a known one never written up, or noise.",
    "Early warning without outcomes. With no outcomes file nothing is measured; with one, a found count means "
    "something only as far as it exceeds what the same alerts at random times find, and one export's issues are few.",
    "A reviewed pack. Its predicates are your export's own filed categories and its terms are words counted from your "
    "narratives, by rule. A category filed inconsistently is learned inconsistently; nobody checked a term.",
    "A pre-registered test. This is one run on one export, with no rule committed before it, unlike D002 and D003.",
    "Anything about the records before the cut. They are used to draft only; the audit reads the records after it, "
    "and its first weeks build each series' history, so a short held-out period evaluates few weeks or none.",
    "That no name is printed. The guard looks for your record ids, site values and refused-column values, and for "
    "eight words of any narrative. A person's name written only in the narratives, in no refused column, is not "
    "looked for, and a drafted term can be one; read the first terms before you pass the report on.",
)
WITHHELD_ERROR = "(its text is withheld: it may name a record value)"


class StartError(ValueError):
    """A usage or input error: a roles file that is not right, a column the export lacks, a cut that leaves nothing."""


# --------------------------------------------------------------------------------------------------- small helpers

def n(value: int) -> str:
    """A count as ``pilot.md`` prints it: with separators from 1,000 (no run of five digits for the guard to read)."""
    return f"{value:,}"


def f2(value: Any) -> str:
    return "n/a" if value is None else (f"{value:.2f}" if isinstance(value, float) else str(value))


def joined(items: Sequence[str]) -> str:
    items = list(items)
    if not items:
        return "none"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def weeks_text(first: str, last: str) -> str:
    return f"week {first}" if first == last else f"weeks {first} to {last}"


def file_facts(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def labels_for(values: Iterable[str], prefix: str) -> dict[str, str]:
    """``prefix`` and a rank from 01, in the sorted order of the distinct values."""
    ordered = sorted(set(values))
    width = max(2, len(str(len(ordered))))
    return {v: f"{prefix}{rank:0{width}d}" for rank, v in enumerate(ordered, start=1)}


# --------------------------------------------------------------------------------------------------- the roles file

def roles_from_obj(obj: Any) -> Roles:
    """The roles file, checked strictly: ``{"language": "en", "roles": {...}}`` with ``language`` optional
    (default ``en``). The roles object names one column for each of ``record_id``, ``site``, ``date``, ``narrative``
    and ``category``, and lists the refused columns under ``forbidden`` (``[]`` when none); ``entities`` (a list) and
    ``reporter`` (a column or null) are optional. Record id, site, date and narrative cannot be list columns. Every
    missing role is named, with what it is."""
    if not isinstance(obj, dict):
        raise StartError('the roles file is a JSON object: {"language": "en", "roles": {...}}')
    unknown = sorted(set(obj) - {"language", "roles"})
    if unknown:
        raise StartError(f"the roles file has unknown keys {', '.join(unknown)} (it holds language and roles)")
    if "roles" not in obj:
        raise StartError('the roles file has no "roles" object naming your columns')
    language = obj.get("language", DEFAULT_LANGUAGE)
    if not isinstance(language, str) or re.fullmatch(r"[a-z]{2,3}", language) is None:
        raise StartError("language must be a language code such as en")
    if not (D.LANG_DIR / f"{language}.json").is_file():
        have = sorted(p.stem for p in D.LANG_DIR.glob("*.json"))
        raise StartError(f"no language file for {language!r} (there is one for {', '.join(have)})")
    raw = obj["roles"]
    if not isinstance(raw, dict):
        raise StartError('"roles" must be an object naming your columns')
    missing = [r for r in REQUIRED_ROLES if r not in raw]
    if missing:
        raise StartError("the roles file is missing " + "; ".join(f'the role "{r}" ({ROLE_HELP[r]})' for r in missing))
    unknown = sorted(set(raw) - set(REQUIRED_ROLES) - set(OPTIONAL_ROLES))
    if unknown:
        raise StartError(f"the roles file has unknown roles {', '.join(unknown)} (the roles are "
                         f"{', '.join((*REQUIRED_ROLES, *OPTIONAL_ROLES))})")
    for r in SINGLE_ROLES:
        value = raw[r]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise StartError(f'the role "{r}" must name one column ({ROLE_HELP[r]})')
    for r in SCALAR_ROLES:
        if is_list_column(raw[r]):
            raise StartError(f'the role "{r}" cannot be a list column (a name ending in []): {ROLE_HELP[r]}')
    for key in ("forbidden", "entities"):
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(isinstance(c, str) and c.strip() and c == c.strip()
                                                  for c in value):
            raise StartError(f'"{key}" must be a list of column names')
    reporter = raw.get("reporter")
    if reporter is not None and (not isinstance(reporter, str) or not reporter.strip() or reporter != reporter.strip()):
        raise StartError('"reporter" must name one column, or be null')
    full = {r: raw[r] for r in SINGLE_ROLES}
    full.update(entities=list(raw.get("entities", [])), reporter=reporter, forbidden=list(raw["forbidden"]))
    names = [full[r] for r in SINGLE_ROLES] + full["entities"] + full["forbidden"] \
        + ([reporter] if reporter is not None else [])
    repeated = sorted({c for c in names if names.count(c) > 1})
    if repeated:
        raise StartError(f"a column takes at most one role: {', '.join(repeated)} is named more than once")
    try:
        return roles_from_json({"language": language, "roles": full})
    except RolesError as err:
        raise StartError(f"the roles file: {err}") from None


def load_roles(path: str | Path) -> Roles:
    try:
        obj = strict_load(Path(path).read_bytes())
    except OSError as exc:
        raise StartError(f"cannot read the roles file {path} ({exc.__class__.__name__})") from None
    except StrictJsonError as err:
        raise StartError(f"the roles file {path} is not JSON ({err})") from None
    return roles_from_obj(obj)


def check_columns(roles: Roles, export: Export) -> None:
    """Every column the roles name must be a column of the export (its header, or its JSON keys)."""
    named = [(r, getattr(roles, r)) for r in SINGLE_ROLES] + [("forbidden", c) for c in roles.forbidden] \
        + [("entities", c) for c in roles.entities] + ([("reporter", roles.reporter)] if roles.reporter else [])
    for role, column in named:
        if not export.has(column):
            have = ", ".join(c for c in export.columns if c)
            raise StartError(f'the export has no column {column!r}, which the roles file names for "{role}" '
                             f"(the export's columns: {have})")


# --------------------------------------------------------------------------------------------------- the cut

def default_train_until(dates: Sequence[date]) -> date:
    """The date of the record at the three-quarter mark of ``dates`` in date order: every record after that date is
    held out, which is the last quarter of the dated records (rounded down), fewer when records share that date."""
    ordered = sorted(dates)
    if not ordered:
        raise StartError("no record has a date the drafter can read")
    k = len(ordered) - int(len(ordered) * HELD_OUT)
    return ordered[k - 1]


def parse_day(value: str, what: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise StartError(f"{what} must be a date YYYY-MM-DD") from None


def labelled_export(export: Export, roles: Roles, labels: Mapping[str, str]) -> Export:
    """The export with every site value replaced by its label: what the audit reads, so no site value reaches a
    site's store, a bundle or a cell."""
    i = export.column_index(roles.site)
    rows = tuple(row[:i] + (labels.get(row[i], row[i]) if isinstance(row[i], str) else row[i],) + row[i + 1:]
                 for row in export.rows)
    return dataclasses.replace(export, rows=rows)


# --------------------------------------------------------------------------------------------------- outcomes

def read_issues(path: Path, d: D.Draft, scope: tuple[str, str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """The outcomes file in your own terms, as the audit's outcome rows: label (``i01`` onwards, in row order),
    opened, the scope entity and the drafted predicate of the issue's category (empty for any). Issues whose category
    is no drafted predicate are left out and counted by why. Errors name a line, never a value."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in OUTCOME_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise StartError(f"the outcomes file lacks the columns {', '.join(missing)} (it needs "
                                 f"{', '.join(OUTCOME_COLUMNS)})")
            rows = list(reader)
    except OSError as exc:
        raise StartError(f"cannot read the outcomes file ({exc.__class__.__name__})") from None
    plan = d.plan
    reasons: dict[str, set[str]] = {"other_bucket": {c.key for c in plan.split.other},
                                    "below_floor": {c.key for c in plan.split.below},
                                    "refused": {c.key for c in plan.refused}}
    width = max(2, len(str(len(rows))))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    not_scored = dict.fromkeys(NOT_SCORED, 0)
    labels: dict[str, str] = {}
    for line, row in enumerate(rows, start=2):
        oid = (row.get("outcome_id") or "").strip()
        if not oid or oid in seen:
            raise StartError(f"the outcomes file, line {line}: outcome_id is empty or repeated")
        seen.add(oid)
        parse_day((row.get("opened") or "").strip(), f"the outcomes file, line {line}: opened")
        label = f"i{line - 1:0{width}d}"
        labels[label] = oid
        category = (row.get("category") or "").strip()
        predicate = ""
        if category:
            key = folded(category)
            predicate = plan.of_key.get(key, "")
            if not predicate:
                why = next((r for r in ("other_bucket", "below_floor", "refused") if key in reasons[r]),
                           "not_in_training")
                not_scored[why] += 1
                continue
        out.append({"outcome_id": label, "opened": (row.get("opened") or "").strip(), "entity_type": scope[0],
                    "entity_id": scope[1], "predicate": predicate})
    return out, {"rows": len(rows), "scored": len(out), "not_scored": not_scored, "labels": labels}


def write_outcome_rows(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["outcome_id", "opened", "entity_type", "entity_id", "predicate"])
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------------------------------- the watch

class Watch:
    """While the audit runs: the bundles and cells each site sent (the audit's own collective store holds every
    weekly cell that left a site), the bytes that left (HQ's receive log), the X and S alerts as the audit computed
    them, and the cells each of those runs read at HQ, so that the sites behind an alert are counted from what left
    them (:meth:`cell_sites`). Read-only: the audit's result is unchanged, and every wrapped function is put back."""

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
        window up to the alert's week."""
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


# --------------------------------------------------------------------------------------------------- the guard

class Guard:
    """The onboard report's last guard over the strings printed from records, its backstop over the whole output, a
    scan for refused-column and site values, and an eight-word scan of the narratives; all built from the export."""

    def __init__(self, export: Export, roles: Roles, params: Mapping[str, Any]) -> None:
        self.ngram = params["ngram_tokens"]
        self.prefix = D.load_template()["ids.json"]["placeholder_prefix"]
        self.sentinels = R.company_sentinels(export, roles, params)
        p = params["refusal"]
        self.backstop = R.Backstop(p["reference_inside_min_chars"])
        self.backstop.add(export, roles)
        _, refused = D.refused_values(export, roles)
        sites = [v for r in range(len(export)) for v in D._cells(export.value(r, roles.site))]
        values = set()
        for v in (*refused, *sites):
            f = folded(v)
            if (len(f) >= p["forbidden_inside_min_chars"] and any(ch.isalpha() for ch in f)) \
                    or len(f) >= p["reference_inside_min_chars"]:
                values.add(f)
        self.refused = D.ValueIndex(values)
        self.refused_count = len(values)

    def strings(self, entry: Mapping[str, Any], extra: Sequence[str] = ()) -> dict[str, int]:
        """The onboard last guard (:func:`.onboard.report.company_hits`) over the draft's printed strings, plus the
        same refusal and n-gram scan over ``extra`` (entity ids a review item prints)."""
        hits = R.company_hits(entry, self.sentinels, self.ngram, self.prefix)
        for s in extra:
            kind = self.sentinels.refusal.string(s)
            if kind is not None:
                hits[f"refused_{kind}"] += 1
        hits["narrative_ngrams"] += len(ngram_hits(extra, self.sentinels.narratives, self.ngram))
        hits["strings"] += len(extra)
        return hits

    def texts(self, texts: Sequence[str], ngrams: bool = True) -> dict[str, int]:
        found = self.backstop.hits(texts)
        found["refused_value"] = len(self.refused.found_all("\n".join(folded(t) for t in texts)))
        if ngrams:
            found["narrative_ngrams"] = len(ngram_hits(texts, self.sentinels.narratives, self.ngram))
        return found

    def sent(self, text: str | None) -> dict[str, int]:
        """The bytes that left the sites: record ids, site values and refused values. Unread bytes are a hit."""
        if text is None:
            return {"sent_unread": 1}
        return {f"sent_{k}": v for k, v in self.texts([text], ngrams=False).items()}

    def message(self, text: str) -> str:
        found = self.texts([text])
        found["refused_own"] = int(self.sentinels.refusal.term(text) is not None)
        return text if not any(found.values()) else WITHHELD_ERROR

    def summary(self) -> dict[str, int]:
        return {"record_ids": len(self.backstop.values["report_record_id"]),
                "site_values": len(self.backstop.values["report_site"]),
                "refused_and_site_values": self.refused_count, "narratives": len(self.sentinels.narratives),
                "ngram_words": self.ngram}


# --------------------------------------------------------------------------------------------------- the run

@dataclasses.dataclass
class Computed:
    """Everything the report is made of, before it is rendered or guarded."""

    doc: dict[str, Any]
    guard: Guard
    entry: dict[str, Any]
    extra_strings: list[str]
    sent: str | None


def _split(dated: D.Dated, train_until: date) -> dict[str, Any]:
    days = sorted(day for day in dated.dates if day is not None)
    return {"date_format": dated.format, "dated": len(days), "undated": dated.rejected,
            "first": days[0].isoformat(), "last": days[-1].isoformat(), "train_until": train_until.isoformat(),
            "training_records": sum(1 for day in days if day <= train_until),
            "held_out_records": sum(1 for day in days if day > train_until)}


def _draft_data(d: D.Draft, shown: bool, params: Mapping[str, Any]) -> dict[str, Any]:
    facts = d.facts
    prefix = d.template["ids.json"]["placeholder_prefix"]
    predicates = []
    for p in facts["predicates"]:
        item = {"code": p["code"], "records": p["records"], "terms": p["terms"]}
        if shown:
            item.update(id=p["id"], label=p["label"],
                        first_terms=[t for t in p["first_terms"] if not t.startswith(prefix)])
        predicates.append(item)
    c = facts["categories"]
    return {"pack_id": PACK_ID, "window": facts["training"]["window"], "training_rows": facts["training"]["rows"],
            "corpus": facts["training"]["corpus"],
            "floor": {"records": params["floor_records"], "sites": params["floor_sites"],
                      "predicate_records": params["predicate_min_records"]},
            "categories": {"passing_floor": len(c["passing_floor"]), "specific": c["specific"], "other": c["other"],
                           "below_floor": c["below_floor"], "refused": c["refused"]},
            "terms": {k: facts["terms"][k] for k in ("total", "eligible", "refused")},
            "placeholders": facts["placeholders"], "entity_types": len(facts["entity_types"]),
            "refusal": {k: facts["refusal"][k] for k in ("categories", "terms")}, "predicates": predicates}


FLOOR_COUNTS = ("terms", "spellings", "entity_ids", "strings", "tokens", "narratives", "failures", "failing_file")


def _floor_data(checked: Mapping[str, Any], loader_error: str | None) -> dict[str, Any]:
    """Each check's verdict and counts (never the check's file list or error text: a failing pack's file names say
    where a record value sits, and a loader error can quote one); the loader's failing file name only."""
    checks = {}
    for name, v in checked["checks"].items():
        item = {k: v[k] for k in ("passed", *FLOOR_COUNTS) if k in v}
        if name == "loader" and loader_error:
            item["failing_file"] = loader_error.split(": ", 1)[0]
        checks[name] = item
    return {"passed": bool(checked["passed"]), "checks": checks}


def _scope(template: Mapping[str, Any]) -> tuple[str, str]:
    t, spec = next(iter(template["vocabulary_base.json"]["entity_types"].items()))
    return t, spec["ids"][0]


def review_parts(doc: Mapping[str, Any], watch: Watch, pack: Any, scope: tuple[str, str]
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """The review list in two parts: each item X or S raised, with the weeks of its X and S alerts and the sites that
    sent HQ a cell of it in the window up to one of them (counted from the cells those runs read; they must be the
    sites the audit took from the sites' own stores for the same alerts); then each item R_mf alone raised, with the
    sites the audit lists. Also the entity ids the items print."""
    counted, reference, extra = [], [], []
    for e in doc["review"]:
        label = pack.predicates[e["predicate"]].label if e["predicate"] in pack.predicates else e["predicate"]
        entity = None
        if (e["entity_type"], e["entity_id"]) != scope:
            et = pack.entity_types.get(e["entity_type"])
            entity = f"{et.label if et is not None else e['entity_type']} {e['entity_id']}"
            extra.append(e["entity_id"])
        by = [name for name in e["channels"] if name in COUNT_CHANNELS]
        if by:
            hits = [(name, a) for name in by for a in watch.alerts.get(name, ()) if a["key"] == e["key"]]
            if not hits:
                raise StartError("an X or S item of the review list has no X or S alert")
            sites: set[str] = set()
            for name, a in hits:
                sent = watch.cell_sites(name, a)
                if sent != set(a["sites"]):
                    raise StartError("the sites behind an X or S alert differ between HQ's cells and the audit")
                sites |= sent
            weeks = sorted({a["week"] for _, a in hits})
            counted.append({"predicate": label, "entity": entity, "channels": by,
                            "also": [c for c in e["channels"] if c not in COUNT_CHANNELS], "first_week": weeks[0],
                            "last_week": weeks[-1], "alerts": len(hits), "sites": sorted(sites),
                            "records": e["records"]})
        else:
            weeks = sorted(e["alert_weeks"])
            reference.append({"predicate": label, "entity": entity, "channels": list(e["channels"]),
                              "first_week": weeks[0], "last_week": weeks[-1], "sites": list(e["sites"]),
                              "records": e["records"]})
    # with no issue in scope every X and S alert is on the review list; an issue's alerts leave it
    raised = {a["key"] for name in COUNT_CHANNELS for a in watch.alerts.get(name, ())}
    listed = {e["key"] for e in doc["review"] if set(e["channels"]) & set(COUNT_CHANNELS)}
    if not listed <= raised or (doc["outcomes"]["in_scope"] == 0 and listed != raised):
        raise StartError("the X and S alerts do not match the review list")
    return counted, reference, extra


def _audit_data(doc: Mapping[str, Any], watch: Watch, pack: Any, scope: tuple[str, str], rejected: Mapping[str, int],
                all_sites: int) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    sites = list(doc["export"]["sites"])
    per_site = []
    for site in sites:
        c = watch.cells.get(site, {"bundles": 0, "cells": 0, "suppressed": 0})
        per_site.append({"site": site, "weekly_bundles": c["bundles"], "cells": c["cells"],
                         "suppressed_cells": c["suppressed"]})
    channels: dict[str, Any] = {}
    for name in A.channel_names(doc):
        s = doc["channels"][name]["summary"]
        channels[name] = {"alerts": s["alerts"] if s is not None else None,
                          "reason": doc["channels"][name].get("reason")}
    counted, reference, extra = review_parts(doc, watch, pack, scope)
    det, w = pack.detectors, doc["weeks"]
    audit = {"records": doc["export"]["records"], "rows": doc["export"]["rows"],
             "rows_rejected": sum(rejected.values()) + sum(doc["export"]["rejected"].values()),
             "sites_in_export": all_sites, "sites": sites, "weeks_first": w["first"], "weeks_last": w["last"],
             "evaluated_from": w["evaluated_from"], "evaluated_weeks": w["evaluated_weeks"],
             "history_weeks": det["window_weeks"] + det["min_history_weeks"] - 1, "window_weeks": det["window_weeks"],
             "min_sites": min(det["burst"]["min_sites"], det["cooccurrence"]["min_sites"]), "k": pack.egress.k,
             "per_site": per_site, "cells": sum(s["cells"] for s in per_site),
             "suppressed_cells": sum(s["suppressed_cells"] for s in per_site),
             "sent": {"bytes": watch.sent_bytes, "bundles": watch.bundles},
             "alerts": {"weekly_cells": {c: channels[c] for c in COUNT_CHANNELS if c in channels},
                        "reference": {c: channels[c] for c in REFERENCE_CHANNELS if c in channels}},
             "settings": dict(doc["settings"])}
    review = {"total": len(doc["review"]), "from_cells": counted, "reference_only": reference}
    return audit, review, extra


def _outcome_data(doc: Mapping[str, Any], issues: Mapping[str, Any], pack: Any,
                  rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    names = A.channel_names(doc)
    about = {r["outcome_id"]: (pack.predicates[r["predicate"]].label if r["predicate"] else None) for r in rows}
    channels = {}
    for name in names:
        s = doc["channels"][name]["summary"]
        channels[name] = None if s is None else {
            k: s[k] for k in ("outcomes", "found", "median_lead_days", "expected_found", "p_value",
                              "expected_found_excluding_own_post", "p_value_excluding_own_post", "alerts")}
    by = {name: {r["outcome_id"]: r for r in doc["channels"][name]["by_outcome"]} for name in names}
    first = names[0]
    by_issue = []
    for r in doc["channels"][first]["by_outcome"]:
        oid = r["outcome_id"]
        cells = {}
        for name in names:
            x = by[name].get(oid)
            cells[name] = None if x is None else {"found": x["found"], "lead_days": x["lead_days"],
                                                   "post_alerts": x["post_alerts"]}
        by_issue.append({"issue": oid, "category": about.get(oid), "opened": r["opened"], "channels": cells})
    o = doc["outcomes"]
    return {"given": issues["rows"], "scored": issues["scored"], "not_scored": dict(issues["not_scored"]),
            "in_scope": o["in_scope"], "out_of_scope": list(o["out_of_scope"]),
            "lookback_weeks": doc["settings"]["lookback_weeks"], "post_weeks": doc["settings"]["post_weeks"],
            "channels": channels, "by_issue": by_issue}


def compute(export_path: Path, roles_path: Path, outcomes_path: Path | None, train_until: str | None, work: Path,
            emit: Callable[[str], None] = lambda line: None, keep: bool = False) -> Computed:
    """Steps 1 to 4 in ``work`` (the drafted pack goes there; with ``keep``, the audit's own files and the label
    maps too). Raises :class:`StartError` for anything that stops the run, its text shown only when the guard finds
    nothing in it; a failed privacy floor is not an error: the result then holds the check and no audit."""
    t0 = time.perf_counter()
    roles = load_roles(roles_path)
    try:
        export = read_export(export_path)
    except ExportError as err:
        raise StartError(f"the export: {err}") from None
    check_columns(roles, export)
    if not len(export):
        raise StartError("the export has no row")
    params, template = D.load_params(), D.load_template()
    lang = D.load_language(roles.language)
    guard = Guard(export, roles, params)
    emit(f"(1) read the export: {time.perf_counter() - t0:.1f} s")
    try:
        return _compute(export, roles, params, template, lang, guard, export_path, roles_path, outcomes_path,
                        train_until, work, emit, keep)
    except StartError as err:
        raise StartError(guard.message(str(err))) from None
    except (D.DraftError, A.AuditError, ExportError, RolesError) as err:
        raise StartError(f"{err.__class__.__name__}: {guard.message(str(err))}") from None


def _compute(export: Export, roles: Roles, params: Mapping[str, Any], template: Mapping[str, Any], lang: D.Language,
             guard: Guard, export_path: Path, roles_path: Path, outcomes_path: Path | None, train_until: str | None,
             work: Path, emit: Callable[[str], None], keep: bool) -> Computed:
    t0 = time.perf_counter()
    dated = D.date_column(export, roles, lang, params)
    days = [day for day in dated.dates if day is not None]
    cut = parse_day(train_until, "--train-until") if train_until is not None else default_train_until(days)
    if not days or min(days) > cut:
        raise StartError("no record is dated on or before --train-until: there is nothing to draft from")
    if max(days) <= cut:
        raise StartError("no record is dated after --train-until: there is nothing left to audit")
    train = D.Window(min(days), cut)
    held = D.Window(cut + timedelta(days=1), max(days))
    d = D.draft_export(export, roles, train, PACK_ID, params=params, lang=lang, template=template)
    pack_dir = work / "pack"
    D.write_pack(d.files, pack_dir)
    pack, loader_error = D.load_written(pack_dir)
    emit(f"(2) draft the pack: {time.perf_counter() - t0:.1f} s")
    t0 = time.perf_counter()
    checked = check_pack(pack_dir, export, roles, train, params=params, lang=lang, template=template)
    shown = bool(checked["passed"]) and pack is not None
    emit(f"(3) check the privacy floor: {time.perf_counter() - t0:.1f} s")
    export_facts = {**file_facts(export_path), "format": export.format, "encoding": export.encoding,
                    "columns": len(export.columns), "rows": len(export), "rejected": dict(export.rejected),
                    "blank_lines": export.blank_lines}
    roles_doc = {**file_facts(roles_path), "language": roles.language, "roles": roles.single(),
                 "list_category": is_list_column(roles.category), "refused_columns": list(roles.forbidden),
                 "entities": list(roles.entities), "reporter": roles.reporter}
    doc: dict[str, Any] = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "status": "shown" if shown else "privacy_floor_failed",
        "label": LABEL,
        "inputs": {"export": export_facts, "roles": roles_doc,
                   "outcomes": None if outcomes_path is None else file_facts(outcomes_path)},
        "split": {**_split(dated, cut), "train_until_given": train_until is not None},
        "draft": _draft_data(d, shown, params), "floor": _floor_data(checked, loader_error),
        "audit": None, "review": None, "outcomes": None, "not_shown": list(NOT_SHOWN)}
    entry: dict[str, Any] = {"draft": d.facts, "pack": {"loader": {"error": loader_error}}, "controls": {}}
    if not shown:
        entry["strings_withheld"] = True
        return Computed(doc=doc, guard=guard, entry=entry, extra_strings=[], sent=None)

    t0 = time.perf_counter()
    site_values = [v for r in range(len(export)) for v in D._cells(export.value(r, roles.site))]
    labels = labels_for(site_values, "s")
    rows, rejected = D.normalised_rows(labelled_export(export, roles, labels), roles, d.dated, held, template,
                                       d.etypes)
    scope = _scope(template)
    outcome_rows: list[dict[str, str]] = []
    issues: dict[str, Any] | None = None
    if outcomes_path is not None:
        outcome_rows, issues = read_issues(outcomes_path, d, scope)
        write_outcome_rows(outcome_rows, work / "outcomes.csv")
        outcomes = A.read_outcomes(work / "outcomes.csv", pack)
    else:
        outcomes = []
    watch = Watch()
    try:
        with watch.active():
            audit_doc = A.audit(pack, rows, outcomes)
    except A.AuditError as err:
        raise StartError(f"the audit of the records after --train-until ({held.first} to {held.last}): {err}; an "
                         "earlier --train-until leaves more weeks to audit") from None
    for name in COUNT_CHANNELS:
        s = audit_doc["channels"][name]["summary"]
        if name not in watch.alerts or s is None or len(watch.alerts[name]) != s["alerts"]:
            raise StartError(f"the {name} alerts could not be read from the audit")
    audit, review, extra = _audit_data(audit_doc, watch, pack, scope, rejected, len(labels))
    doc["audit"], doc["review"] = audit, review
    if issues is not None:
        doc["outcomes"] = _outcome_data(audit_doc, issues, pack, outcome_rows)
    if keep:
        A._write(audit_doc, work / "audit")
        _write_map(work / "sites.csv", ("site", "value"), sorted((v, k) for k, v in labels.items()))
        if issues is not None:
            _write_map(work / "issues.csv", ("issue", "outcome_id"), sorted(issues["labels"].items()))
    emit(f"(4) audit the held-out records: {time.perf_counter() - t0:.1f} s")
    return Computed(doc=doc, guard=guard, entry=entry, extra_strings=extra, sent=watch.sent)


def _write_map(path: Path, header: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# --------------------------------------------------------------------------------------------------- rendering

def json_text(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def ordered(names: Iterable[str]) -> list[str]:
    """Channel names in the report's fixed order (X, S, R_mf, P, PRR), whatever order a mapping holds them in: the
    report reads the same from ``pilot.json`` reloaded, whose keys are sorted."""
    names = set(names)
    return [c for c in (*COUNT_CHANNELS, *REFERENCE_CHANNELS) if c in names] + sorted(
        names - set(COUNT_CHANNELS) - set(REFERENCE_CHANNELS))


def _channel_counts(channels: Mapping[str, Any]) -> str:
    parts = [f"{name} {n(channels[name]['alerts'])}" if channels[name]["alerts"] is not None else f"{name} not run"
             for name in ordered(channels)]
    return ", ".join(parts) or "none run"


def _review_item(r: Mapping[str, Any], window: int, counted: bool) -> str:
    about = r["predicate"] + (f" ({r['entity']})" if r["entity"] else "")
    sites = f"{n(len(r['sites']))} sites ({', '.join(r['sites'])})" if r["sites"] else "no site"
    if counted:
        also = f"; {joined(r['also'])} also raised it" if r["also"] else ""
        return (f"{about}: {joined(r['channels'])} alerts in {weeks_text(r['first_week'], r['last_week'])}; {sites} "
                f"sent a cell of it in the {window} weeks up to one of them; {n(r['records'])} records{also}")
    return (f"{about}: {joined(r['channels'])} alerts in {weeks_text(r['first_week'], r['last_week'])}; {sites} with "
            f"records of it in the {window} weeks up to one of them; {n(r['records'])} records")


def _md_export(doc: Mapping[str, Any]) -> list[str]:
    e, s = doc["inputs"]["export"], doc["split"]
    rejected = sum(e["rejected"].values())
    how = "given (--train-until)" if s["train_until_given"] else \
        "the default: the last quarter of the dated records come after it"
    return ["## The export", "",
            f"- {n(e['rows'])} rows read ({e['format']}, {e['encoding']}), {n(e['columns'])} columns; "
            f"{n(rejected)} rows rejected by the reader.",
            f"- Dates read as {s['date_format']}: {n(s['dated'])} records dated, {n(s['undated'])} not.",
            f"- Drafted from the {n(s['training_records'])} records dated {s['first']} to {s['train_until']} "
            f"(the cut is {how}).",
            f"- Audited: the {n(s['held_out_records'])} records dated after {s['train_until']}, to {s['last']}, which "
            "the draft never read.", ""]


def _md_draft(doc: Mapping[str, Any]) -> list[str]:
    dr = doc["draft"]
    c, fl = dr["categories"], dr["floor"]
    out = ["## 1. The drafted pack", "",
           f"Filed categories that passed the floor (at least {fl['records']} training records at {fl['sites']} "
           f"sites): {n(c['passing_floor'])}. Predicates among them (at least {fl['predicate_records']} records and "
           f"a specific label): {n(c['specific'])}. Other bucket: {n(c['other'])}. Below the floor, left out: "
           f"{n(c['below_floor'])}. Refused (a string of theirs is a record id, site or refused value): "
           f"{n(c['refused'])}.",
           "",
           f"Terms: {n(dr['terms']['total'])} in all, counted from {n(dr['corpus'])} training narratives; "
           f"{n(dr['placeholders'])} predicates learned none. The refusal removed {n(dr['refusal']['terms'])} "
           "floor-passing terms.", ""]
    if doc["status"] == "privacy_floor_failed":
        out += ["The privacy floor failed, so labels and terms are withheld. Predicates by code:", ""]
        out += [f"- {p['code']}: {n(p['records'])} training records, {n(p['terms'])} terms" for p in dr["predicates"]]
        return out + [""]
    out += ["| Predicate | Training records | Terms | First terms |", "|---|---|---|---|"]
    for p in dr["predicates"]:
        out.append(f"| {p['label']} | {n(p['records'])} | {n(p['terms'])} | "
                   f"{', '.join(p['first_terms']) or 'no term learned'} |")
    return out + [""]


def _md_floor(doc: Mapping[str, Any]) -> list[str]:
    fl = doc["floor"]
    out = ["## 2. The privacy floor", "",
           ("Passed: every check below held, so the pack's labels and terms may be shown." if fl["passed"] else
            "Failed: the run stops here. No label or term is shown and no audit ran."), "",
           "| Check | Result | Counts |", "|---|---|---|"]
    checks = fl["checks"]
    for name in [c for c in CHECK_NAMES if c in checks] + sorted(c for c in checks if c not in CHECK_NAMES):
        v = checks[name]
        counts = ", ".join(f"{k.replace('_', ' ')} {n(v[k]) if isinstance(v[k], int) else v[k]}"
                           for k in FLOOR_COUNTS if k in v)
        out.append(f"| {CHECK_NAMES.get(name, name)} | {'passed' if v['passed'] else 'failed'} | {counts} |")
    return out + [""]


def _md_audit(doc: Mapping[str, Any]) -> list[str]:
    a, rv = doc["audit"], doc["review"]
    k, window = a["k"], a["window_weeks"]
    out = ["## 3. What left each site", "",
           f"{n(a['sites_in_export'])} sites in the export, {n(len(a['sites']))} with audited records. Each ran as its "
           "own site inside this process, under its label; nothing was sent over a network. What left a site is what "
           "crossed from its store to HQ's: weekly cells only, one per category, week and channel that had a "
           f"record. A cell holds counts (a count under {k} leaves as '<k') and no record id or text.", "",
           f"Audited: {n(a['records'])} records; {n(a['rows_rejected'])} held-out rows not used. Weeks evaluated: "
           f"{n(a['evaluated_weeks'])} ({a['evaluated_from']} to {a['weeks_last']}); the {a['history_weeks']} weeks "
           "before build each series' history.", "",
           f"| Site | Weekly bundles | Cells | Cells with a count under {k} |", "|---|---|---|---|"]
    out += [f"| {s['site']} | {n(s['weekly_bundles'])} | {n(s['cells'])} | {n(s['suppressed_cells'])} |"
            for s in a["per_site"]]
    out += ["", f"In all {n(a['cells'])} cells ({n(a['suppressed_cells'])} under {k}) in {n(a['sent']['bundles'])} "
                f"bundles, {n(a['sent']['bytes'])} bytes. The guard scanned those bytes.", "",
            "## 4. Alerts by channel", "",
            f"- From the weekly cells that left the sites: {_channel_counts(a['alerts']['weekly_cells'])}. X reads "
            f"the code and text cells, S the code cells only; each alerts only when a category's counts rise at "
            f"{a['min_sites']} or more sites in the same weeks.",
            f"- Reference channels, which count record-level codes centrally and are not what left the sites: "
            f"{_channel_counts(a['alerts']['reference'])}.", "",
            "## 5. The review list", "",
            f"{n(rv['total'])} items: what the detectors flagged that matches no issue given. Each needs a person's "
            "look.",
            "", f"From the weekly cells (X or S): {n(len(rv['from_cells']))}.", ""]
    out += [f"- {_review_item(r, window, True)}" for r in rv["from_cells"]] or ["- None."]
    out += ["", f"Reference only, raised by R_mf alone: {n(len(rv['reference_only']))}. They need record-level codes "
                "counted centrally, which this setup does not send.", ""]
    out += [f"- {_review_item(r, window, False)}" for r in rv["reference_only"]] or ["- None."]
    return out + [""]


def _md_outcomes(doc: Mapping[str, Any]) -> list[str]:
    o = doc["outcomes"]
    out = ["## 6. Against the issues you acted on", ""]
    if o is None:
        return out + ["No outcomes file was given, so nothing here measures whether any alert came early, or at all. "
                      "Every X, S and R_mf alert is on the review list; P and PRR add nothing to it.", ""]
    skipped = [f"{n(o['not_scored'][k])} {NOT_SCORED_SAYS[k]}" for k in NOT_SCORED if o["not_scored"].get(k)]
    out += [f"- Issues given: {n(o['given'])}. Scored: {n(o['scored'])}, whose category is a drafted predicate, or "
            "empty for any category.",
            f"- Not scored, because their category is not a drafted predicate: {'; '.join(skipped) or 'none'}.",
            f"- In scope, with their {o['lookback_weeks']}-week look-back in the evaluated weeks: {n(o['in_scope'])}"
            + (f". Out of scope: {', '.join(o['out_of_scope'])}." if o["out_of_scope"] else "."), "",
            "| Channel | Found before opening | Median days earlier | Expected by chance | p | Expected by chance, "
            "reactive alerts left out | p | Alerts |", "|---|---|---|---|---|---|---|---|"]
    for name in ordered(o["channels"]):
        s = o["channels"][name]
        if s is None:
            out.append(f"| {name} | not run | | | | | | |")
            continue
        out.append(f"| {name} | {n(s['found'])} of {n(s['outcomes'])} | {f2(s['median_lead_days'])} | "
                   f"{f2(s['expected_found'])} | {f2(s['p_value'])} | {f2(s['expected_found_excluding_own_post'])} | "
                   f"{f2(s['p_value_excluding_own_post'])} | {n(s['alerts'])} |")
    out += ["", "A found count means something only as far as it exceeds what chance gives: the same alerts at random "
                "times (the circular-shift null) would find the expected number. Complaints that react to an opened "
                "issue inflate it, so the second pair of columns leaves each issue's own alerts after its opening "
                "out. How far either null can be read is in docs/collective/PILOT.md.", ""]
    if o["by_issue"]:
        names = ordered(o["channels"])
        out += ["| Issue | Category | Opened | " + " | ".join(names) + " |", "|---|---|---|" + "---|" * len(names)]
        for r in o["by_issue"]:
            cells = []
            for name in names:
                x = r["channels"][name]
                cells.append("n/a" if x is None else (f"{x['lead_days']} days earlier" if x["found"]
                                                      else "not flagged"))
            out.append(f"| {r['issue']} | {r['category'] or 'any category'} | {r['opened']} | "
                       + " | ".join(cells) + " |")
        out.append("")
    return out


def render_md(doc: Mapping[str, Any]) -> str:
    """``pilot.md`` from ``pilot.json``'s content alone."""
    if doc["status"] == "withheld":
        return "\n".join([f"# {TITLE}", "", f"_{doc['label']}._", "", doc["withheld"], ""]) + "\n"
    out = [f"# {TITLE}", "", f"_{doc['label']}._", "",
           "Sites are s01 onwards, numbered in the sorted order of the site column's values; issues are i01 onwards, "
           "in the order of the outcomes file's rows. No record id, site value, refused-column value or narrative "
           "text is printed.", ""]
    out += _md_export(doc) + _md_draft(doc) + _md_floor(doc)
    if doc["audit"] is not None:
        out += _md_audit(doc) + _md_outcomes(doc)
    out += ["## What this does not show", ""] + [f"- {line}" for line in doc["not_shown"]] + [""]
    g = doc.get("guard")
    if g:
        out += [f"Guard: {n(g['strings'])} printed strings from records checked against the export; "
                f"{n(g['record_ids'])} record ids, {n(g['site_values'])} site values and "
                f"{n(g['refused_and_site_values'])} refused and site values looked for in this file, in pilot.json "
                f"and in the bytes that left the sites, and {g['ngram_words']} words in a row of "
                f"{n(g['narratives'])} narratives. Found: none.", ""]
    return "\n".join(out)


def withheld_doc(hits: Mapping[str, int]) -> dict[str, Any]:
    """What replaces both files when the guard finds something: the hit counts by kind, nothing else from the run."""
    counts = {k: int(v) for k, v in sorted(hits.items()) if v}
    kinds = ", ".join(f"{k} {v}" for k, v in counts.items())
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "status": "withheld", "label": LABEL, "guard": counts,
            "withheld": f"The guard found a record value in what would be written. Hits by kind: {kinds}. Nothing "
                        "from the run is shown; the exit code is 1."}


def present(c: Computed) -> tuple[dict[str, Any], str, int]:
    """The guard, then the files: (pilot.json's content, pilot.md, exit code)."""
    hits = dict(c.guard.strings(c.entry, c.extra_strings))
    strings = hits.pop("strings")
    if c.doc["audit"] is not None:
        hits.update(c.guard.sent(c.sent))
    if any(hits.values()):
        doc = withheld_doc(hits)
        return doc, render_md(doc), 1
    doc = dict(c.doc, guard={"strings": strings, **c.guard.summary()})
    md = render_md(doc)
    found = c.guard.texts([md, *json_strings(json.loads(json_text(doc)))])
    if any(found.values()):
        doc = withheld_doc(found)
        return doc, render_md(doc), 1
    return doc, md, 0 if doc["status"] == "shown" else 1


def write(out: Path, doc: Mapping[str, Any], md: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "pilot.json").write_text(json_text(doc), encoding="utf-8")
    (out / "pilot.md").write_text(md, encoding="utf-8")


# --------------------------------------------------------------------------------------------------- the example

EXAMPLE_START = date(2021, 1, 4)
EXAMPLE_WEEKS = 209
EXAMPLE_PER_WEEK = 6
EXAMPLE_TYPES = {"Slip or trip": ("slipped", "tripped", "wet", "floor", "spill"),
                 "Manual handling": ("lifting", "carrying", "strained", "pallet", "crate"),
                 "Vehicle movement": ("forklift", "reversing", "reach", "truck", "loading"),
                 "Racking strike": ("racking", "upright", "beam", "struck", "aisle"),
                 "Cut or laceration": ("knife", "blade", "cut", "sharp", "strapping"),
                 "Other": ("misc", "various", "general")}
EXAMPLE_FILLER = ("colleague", "shift", "team", "morning", "noticed", "nearby", "while", "during", "returned",
                  "afternoon", "worker", "section", "evening", "attended", "first", "aid")
EXAMPLE_DEPOTS = ("Ashford Hub", "Brindley Park", "Calder Vale", "Dunmore Cross", "Elmstead Yard", "Fenwick Point")
EXAMPLE_PEOPLE = ("Rowan Pike", "Imogen Sallow", "Theo Marchetti", "Priya Lindqvist", "Callum Ortega")
# type, first week, end week (2024-09-09 to 2024-10-27), depots, records a week at each
EXAMPLE_BURST = ("Manual handling", 192, 199, 3, 3)
EXAMPLE_ISSUES = (("CAPA-2024-012", "2024-06-03", "Other"), ("CAPA-2024-031", "2024-11-11", "manual handling"),
                  ("CAPA-2024-040", "2024-12-02", ""))
EXAMPLE_ROLES = {"language": "en",
                 "roles": {"record_id": "incident_no", "site": "depot", "date": "reported_on",
                           "narrative": "what_happened", "category": "incident_type",
                           "forbidden": ["reported_by", "staff_no"]}}


def example_narrative(rng: Any, kind: str) -> str:
    words = EXAMPLE_TYPES[kind]
    noise = [rng.choice(EXAMPLE_TYPES[rng.choice(sorted(k for k in EXAMPLE_TYPES if k != kind))])] \
        if rng.random() < 0.3 else []
    first = " ".join(rng.sample(words, 2) + noise + rng.sample(EXAMPLE_FILLER, 5))
    second = " ".join(rng.sample(EXAMPLE_FILLER, 4) + [str(rng.randint(2, 40)), "minutes"])
    return f"{first.capitalize()}. {second.capitalize()}."


def write_example(out: Path, seed: int = 1) -> dict[str, Path]:
    """A synthetic incident export of a made-up company with six depots, four years long, in its own column names
    (``EXAMPLE_ROLES`` names them), with one pattern planted in its last year: one incident type rising at three
    depots for some weeks. Also its roles file and three issues acted on (one two weeks after the pattern ends, one
    about any type, one about the non-specific type). Every value is invented; the planted pattern and the detectors
    share an author. Dates are written D.M.YYYY."""
    rng = random.Random(f"pilot-start-example:{seed}")
    kinds = sorted(EXAMPLE_TYPES)
    rows: list[tuple[date, str, str]] = []
    burst, first, end, depots, per = EXAMPLE_BURST
    for week in range(EXAMPLE_WEEKS):
        monday = EXAMPLE_START + timedelta(days=7 * week)
        for _ in range(EXAMPLE_PER_WEEK):
            rows.append((monday + timedelta(days=rng.randrange(7)), rng.choice(EXAMPLE_DEPOTS), rng.choice(kinds)))
        if first <= week < end:
            for depot in EXAMPLE_DEPOTS[:depots]:
                rows += [(monday + timedelta(days=rng.randrange(5)), depot, burst) for _ in range(per)]
    rows.sort(key=lambda r: r[0])
    out.mkdir(parents=True, exist_ok=True)
    paths = {"export": out / "export.csv", "roles": out / "roles.json", "outcomes": out / "outcomes.csv"}
    roles = EXAMPLE_ROLES["roles"]
    with open(paths["export"], "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([roles["record_id"], roles["site"], roles["date"], roles["narrative"], roles["category"],
                    *roles["forbidden"]])
        for i, (day, depot, kind) in enumerate(rows, start=1):
            w.writerow([f"INC-{day.year}-{i:05d}", depot, day.strftime("%d.%m.%Y"), example_narrative(rng, kind),
                        kind, rng.choice(EXAMPLE_PEOPLE), f"S-{10000 + rng.randrange(90000)}"])
    paths["roles"].write_text(json.dumps(EXAMPLE_ROLES, indent=1) + "\n", encoding="utf-8")
    with open(paths["outcomes"], "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(OUTCOME_COLUMNS)
        w.writerows(EXAMPLE_ISSUES)
    return paths


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.pilot.start", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="draft a pack from your export, check it, and audit the later records with it")
    r.add_argument("--export", required=True, help="your export: CSV, pipe- or tab-delimited text, or JSON lines")
    r.add_argument("--roles", required=True, help="the roles file naming your columns")
    r.add_argument("--outcomes", help="the issues you acted on: outcome_id, opened, category")
    r.add_argument("--out", required=True, help="where pilot.md and pilot.json go")
    r.add_argument("--train-until", help="draft from the records dated on or before this date, YYYY-MM-DD (default: "
                                         "the date that leaves the last quarter of dated records after it)")
    r.add_argument("--work", help="keep the drafted pack and the audit's own files here (record data; never --out "
                                  "or inside it; default: a temporary directory, deleted)")
    e = sub.add_parser("example", help="write a synthetic export, its roles file and an outcomes file to try run on")
    e.add_argument("--out", required=True, help="where export.csv, roles.json and outcomes.csv go")
    e.add_argument("--seed", type=int, default=1)
    for q in (r, e):
        q.add_argument("--dry-run", action="store_true", help="say what would be read and written; touch nothing")
    return p


def _dry(args: argparse.Namespace) -> int:
    dry = DryRun(f"{CLI} {args.command}")
    if args.command == "example":
        for name in ("export.csv", "roles.json", "outcomes.csv"):
            dry.write(str(Path(args.out) / name))
        return dry.emit()
    for value in (args.export, args.roles, args.outcomes):
        if value is not None and not Path(value).is_file():
            dry.need(f"file {value}")
    for name in ("pilot.json", "pilot.md"):
        dry.write(str(Path(args.out) / name))
    if args.work:
        for name in ("pack", "audit", "sites.csv", *(("outcomes.csv", "issues.csv") if args.outcomes else ())):
            dry.write(str(Path(args.work) / name))
    return dry.emit()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.dry_run:
        return _dry(args)
    if args.command == "example":
        try:
            paths = write_example(Path(args.out), seed=args.seed)
        except OSError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        print(f"pilot start example: a synthetic export, its roles file and outcomes file -> "
              f"{', '.join(str(p) for p in paths.values())}")
        return 0

    def emit(line: str) -> None:
        print(f"pilot start: {line}", file=sys.stderr, flush=True)

    out = Path(args.out).resolve()
    try:
        with contextlib.ExitStack() as stack:
            if args.work:
                work = Path(args.work).resolve()
                if work == out or out in work.parents:
                    raise StartError("--work holds record data: it cannot be --out or inside it")
                if work.exists() and any(work.iterdir()):
                    raise StartError(f"{work} is not empty: pick a new --work")
                work.mkdir(parents=True, exist_ok=True)
            else:
                work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="pilot-start-")))
            computed = compute(Path(args.export), Path(args.roles), Path(args.outcomes) if args.outcomes else None,
                               args.train_until, work, emit, keep=bool(args.work))
            doc, md, code = present(computed)
    except (StartError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:                       # noqa: BLE001 (never a traceback: it could print a record value)
        print(f"error: {err.__class__.__name__} (its text is not shown)", file=sys.stderr)
        return 2
    write(out, doc, md)
    if doc["status"] == "withheld":
        print("pilot start: withheld by the guard (hit counts only) -> " + str(out / "pilot.md"), file=sys.stderr)
    elif doc["status"] == "privacy_floor_failed":
        failed = [CHECK_NAMES.get(k, k) for k, v in doc["floor"]["checks"].items() if not v["passed"]]
        print(f"error: the drafted pack failed its privacy floor ({', '.join(failed)}): its labels and terms are "
              f"withheld and no audit ran -> {out / 'pilot.md'}", file=sys.stderr)
    else:
        a = doc["audit"]
        alerts = _channel_counts({**a["alerts"]["weekly_cells"], **a["alerts"]["reference"]})
        print(f"pilot start: {doc['draft']['categories']['specific']} predicates drafted, privacy floor passed; "
              f"{a['records']} records audited at {len(a['sites'])} sites; alerts {alerts}; review list "
              f"{doc['review']['total']} -> {out / 'pilot.md'}")
    return code


if __name__ == "__main__":
    sys.exit(main())

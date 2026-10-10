"""A drafting test's report: both arms, the checks, the audit summary and the six criteria (rules 6 and 8, amended).

    python -m mycelic.collective.onboard report --settings FILE --arms DIR [DIR ...] [--audit FILE] --out DIR

* **C1 and C2** come from the arms (``arm.json``'s ``criterion``), each from the arm the settings give it to. The
  report prints each criterion's deciding values unrounded beside the comparison it makes (amendment A8).
* **M1, no code per field:** both arms ran the same code commit and the same onboard code; no file of the onboard
  package holds a column name of either source (:func:`.check.package_hits`); no drafted label equals one; the
  download code's line counts are given: each arm's download script and every module it imports from ``tools/``
  (the settings' ``download_code``), which the code hash also covers.
* **M2, the packs load:** every company of both arms was drafted and its pack passed the loader's check.
* **M3, the privacy floor:** every drafted pack passed rule 1.7, and the last guard found nothing.
* **M4, the audit:** ``audit.json`` exists, holds at least one record, and channels X and S ran (no reason given,
  an alert timeline present).

**The last guard** (rule 8 as amended, A5 and A10; :func:`guard_arm`, :func:`guard_hits`) runs before anything is
written. Per company, it reads the strings the report prints that came from records (:func:`company_strings`: category
labels, predicate ids, lexicon terms, the majority prior's id and error texts) and checks them against that company's
own export only: a string the export's one :class:`.draft.Refusal` refuses (built by :func:`.draft.export_refusal`, the
drafter's and the check's own function; a term by its term rule), or one that holds ``ngram`` consecutive tokens of
one of its narratives, is a hit. A value of one company never refuses another company's string. The report's fixed
words, its keys, the settings' labels and the definition lines are not from records and are not scanned.

**The backstop** (A11; :class:`Backstop`) then scans the whole rendered text of both files, and the parsed strings of
``report.json``, for the record ids and site values of at least ``reference_inside_min_chars`` characters of every
company of every arm: exact after folding, as whole tokens.

A guard hit, a backstop match, or an export the guard cannot read writes a report holding only the hit counts by kind
(per arm and per company) and the verdict ``withheld``, and M3 fails; the workflow then uploads nothing else.

Both files are printed between markers, ``=== <experiment> report.json BEGIN lines=<n> sha256=<hex> ===`` and
``... END ===``. The experiment id (``D001``, ``D002``) comes from the settings (:func:`.score.experiment_id`), and so
do the report's title and ``report.json``'s ``experiment``. The ``kind`` fields name the file schemas D001 defined,
which D002 keeps.

**D003** (``docs/collective/onboard/CHOICE-D003.md``, section 11 and E18) adds, each only when its settings ask:
the criteria the verdict takes (P3: without M4 the report reads no audit file and prints no audit section); several
criteria from one arm (P1); the report's source-specific sentences from the settings' ``report_text`` (P5); the
days intervals (P8), C2's space (P7), the reader guard (P10), the echo and template measures (P6, P11) in their own
tables; M1's names from the earlier settings files (P12, in :func:`.check.column_names`); and the last guard's
exemption of FDA's generic terms from its n-gram scan, with its count of hits inside printed labels (P13). Without
them, D002's report, byte for byte.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..experiments.common import code_hash
from ..jsonio import strict_load
from ..packs.canonical import folded
from .check import column_names, exempt_labels, json_strings, ngram_hits, package_hits, within_spellings
from .draft import REFUSAL_KINDS, Refusal, ValueIndex, _cells, export_refusal, load_template
from .exports import Export, ExportError, read_export
from .roles import Roles
from .score import EXACT, ROOT, arm_roles, criterion_specs, experiment_id, load_companies, rounded, tree_hashes

CRITERIA = ("C1", "C2", "M1", "M2", "M3", "M4")
ONBOARD_DIR = "mycelic/collective/onboard"
TOOLS_DIR = "tools/onboard"
AUDIT_CHANNELS = ("X", "S")
GUARD_KINDS = (*(f"refused_{k}" for k in REFUSAL_KINDS), "narrative_ngrams")
BACKSTOP_KINDS = ("report_record_id", "report_site")
GUARD_SAYS = {"refused_equal": "equals a value of a record-id, site or forbidden column of its own company",
              "refused_inside": "holds such a value as whole words",
              "refused_name_word": "equals a word of a forbidden value of two or more words",
              "narrative_ngrams": "holds consecutive words of a narrative of its own company"}
BACKSTOP_SAYS = {"report_record_id": "a record id of some company",
                 "report_site": "a site value of some company"}


def _f(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _exact(value: Any) -> str:
    """A deciding value as the comparison saw it: a float in full (``repr``), anything else as text."""
    if value is None:
        return "n/a"
    return repr(value) if isinstance(value, float) else str(value)


def load_arms(dirs: Sequence[str | Path]) -> dict[str, dict[str, Any]]:
    arms = {}
    for d in dirs:
        path = Path(d) / "arm.json"
        if path.is_file():
            doc = strict_load(path.read_bytes())
            arms[doc["arm"]] = doc
    return arms


def audit_summary(path: str | Path) -> dict[str, Any] | None:
    """The audit's counts: records, sites, weeks evaluated, alerts per channel and the review list's length. Never a
    record reference or a site. A channel that ran has no reason and an alert timeline, one entry per alert (the
    audit writes its counts block exactly then)."""
    p = Path(path)
    if not p.is_file():
        return None
    doc = strict_load(p.read_bytes())
    channels = doc.get("channels", {})
    ran = {name: ch.get("reason") is None and isinstance(ch.get("alert_timeline"), list)
           for name, ch in sorted(channels.items())}
    return {"records": doc["export"]["records"], "sites": len(doc["export"]["sites"]),
            "weeks_evaluated": doc["weeks"]["evaluated_weeks"],
            "alerts": {name: len(ch["alert_timeline"]) if ran[name] else None for name, ch in sorted(channels.items())},
            "channels_run": ran, "review_list": len(doc.get("review", []))}


def download_code(settings: Mapping[str, Any]) -> list[str]:
    """Amendment A8: every arm's download script and the modules it imports from ``tools/``, sorted."""
    paths: set[str] = set()
    for arm in settings["arms"].values():
        paths.update(arm.get("download_code") or [arm["download_script"]])
    return sorted(paths)


def script_lines(settings: Mapping[str, Any]) -> dict[str, int | None]:
    """M1's per-field code: the line count of each file of :func:`download_code` (None for a missing file)."""
    out: dict[str, int | None] = {}
    for path in download_code(settings):
        p = ROOT / path
        out[path] = len(p.read_text(encoding="utf-8").splitlines()) if p.is_file() else None
    return out


def code_files(settings: Mapping[str, Any]) -> dict[str, str]:
    """sha256 of every file the code hash covers: the onboard package, ``tools/onboard`` and the download code."""
    files = tree_hashes((ONBOARD_DIR, TOOLS_DIR))
    for path in download_code(settings):
        p = ROOT / path
        if p.is_file():
            files[path] = hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def needs_audit(settings: Mapping[str, Any]) -> bool:
    """D003's change P3: the audit file is read only when the settings list no criteria (D001, D002: the six) or
    list M4."""
    listed = settings.get("criteria")
    return listed is None or "M4" in listed


def listed_criteria(settings: Mapping[str, Any]) -> tuple[str, ...]:
    """The criteria the verdict takes: the settings' list (P3), else the six."""
    return tuple(settings.get("criteria") or CRITERIA)


def arm_criterion(doc: Mapping[str, Any], cid: str) -> Mapping[str, Any] | None:
    """An arm's criterion by id: its one (D001, D002) or one of its list (P1)."""
    c = doc["criterion"]
    return next((x for x in (c if isinstance(c, list) else [c]) if x.get("id") == cid), None)


def criteria(settings: Mapping[str, Any], arms: Mapping[str, Mapping[str, Any]],
             audit: Mapping[str, Any] | None, guard_clean: bool) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, spec in settings["arms"].items():
        for cs in criterion_specs(spec):
            cid = cs["id"]
            doc = arms.get(name)
            found = arm_criterion(doc, cid) if doc is not None else None
            out[cid] = dict(found) if found is not None else {"id": cid, "passed": False,
                                                              "reason": f"the {name} arm did not finish"}
    names = column_names(settings)
    commits = {doc["code_commit"] for doc in arms.values()}
    code = {json.dumps(doc["code_files"], sort_keys=True) for doc in arms.values()}
    pkg = package_hits(ROOT / ONBOARD_DIR, names)
    labels = 0
    for doc in arms.values():
        for c in doc["companies"].values():
            if "error" in c or c.get("strings_withheld"):
                continue
            labels += sum(1 for x in c["draft"]["categories"]["passing_floor"] if x["label"] in names)
    lines = script_lines(settings)
    all_arms = set(arms) == set(settings["arms"])
    out["M1"] = {"passed": bool(all_arms and len(commits) == 1 and "unknown" not in commits and len(code) == 1
                                and not pkg and labels == 0 and all(v is not None for v in lines.values())),
                 "code_commits": len(commits), "package_hits": pkg, "label_hits": labels,
                 "download_code_lines": lines, "download_code_total": sum(v or 0 for v in lines.values())}
    drafted, loaded, errors = 0, 0, 0
    for doc in arms.values():
        for c in doc["companies"].values():
            if "error" in c:
                errors += 1
                continue
            drafted += 1
            loaded += int(bool(c["pack"]["loader"]["passed"]))
    out["M2"] = {"passed": bool(all_arms and errors == 0 and drafted > 0 and loaded == drafted),
                 "drafted": drafted, "loaded": loaded, "not_drafted": errors}
    checked = sum(1 for doc in arms.values() for c in doc["companies"].values()
                  if "error" not in c and c["check"]["passed"])
    out["M3"] = {"passed": bool(all_arms and errors == 0 and drafted > 0 and checked == drafted and guard_clean),
                 "packs": drafted, "passed_floor": checked, "last_guard_clean": guard_clean}
    if needs_audit(settings):
        ok = audit is not None and audit["records"] >= 1 and all(audit["channels_run"].get(ch)
                                                                 for ch in AUDIT_CHANNELS)
        out["M4"] = {"passed": bool(ok), "audit": audit}
    return {k: out.get(k, {"id": k, "passed": False, "reason": "not computed"}) for k in listed_criteria(settings)
            if k in out or settings.get("criteria") is not None}


# --------------------------------------------------------------------------------------------------- the last guard

class Sentinels:
    """What one company's printed strings must not carry (amendments A5 and A10): the one :class:`.draft.Refusal` of
    that company's own export, as the drafter and the check built it, and its narratives for the n-gram scan."""

    def __init__(self, refusal: Refusal, narratives: list[str]) -> None:
        self.refusal = refusal
        self.narratives = narratives


def company_sentinels(export: Export, roles: Roles, params: Mapping[str, Any]) -> Sentinels:
    """One company's sentinels from its own export: :func:`.draft.export_refusal` (every row's record-id, site and
    forbidden values) and every narrative, as the check reads them."""
    narratives = [] if not export.has(roles.narrative) else \
        [n for n in (export.value(r, roles.narrative) for r in range(len(export))) if n is not None]
    return Sentinels(export_refusal(export, roles, params), narratives)


class Backstop:
    """Amendment A11: the record ids and the site values of at least ``min_chars`` characters (folded) of every company
    of every arm, found in the rendered report as whole tokens."""

    def __init__(self, min_chars: int) -> None:
        self.min_chars = min_chars
        self.values: dict[str, set[str]] = {k: set() for k in BACKSTOP_KINDS}

    def add(self, export: Export, roles: Roles) -> None:
        for kind, column in zip(BACKSTOP_KINDS, (roles.record_id, roles.site)):
            if not export.has(column):
                continue
            for r in range(len(export)):
                for v in _cells(export.value(r, column)):
                    f = folded(v)
                    if len(f) >= self.min_chars:
                        self.values[kind].add(f)

    def hits(self, texts: Sequence[str]) -> dict[str, int]:
        """How many distinct values of each kind the texts hold (folded, whole tokens)."""
        body = "\n".join(folded(t) for t in texts)
        return {kind: len(ValueIndex(self.values[kind]).found_all(body)) for kind in BACKSTOP_KINDS}


def company_strings(c: Mapping[str, Any], placeholder_prefix: str) -> list[tuple[str, str]]:
    """``(kind, string)`` for every string of one company's arm entry that the report prints and that came from
    records: category labels, predicate ids (placeholders and the majority prior's among them), lexicon terms and
    error texts. A company whose strings were withheld (its pack failed the floor) has only its error texts."""
    if "error" in c:
        return [("error", c["error"])]
    out: list[tuple[str, str]] = []
    loader_error = c.get("pack", {}).get("loader", {}).get("error")
    if loader_error:
        out.append(("error", loader_error))
    if c.get("strings_withheld"):
        return out
    out += [("label", x["label"]) for x in c["draft"]["categories"]["passing_floor"]]
    for p in c["draft"]["predicates"]:
        out += [("label", p["label"]), ("id", p["id"])]
        out += [("id" if t.startswith(placeholder_prefix) else "term", t) for t in p["first_terms"]]
    top = c.get("controls", {}).get("majority_prior", {}).get("predicate")
    if top:
        out.append(("id", top))
    for p in c.get("controls", {}).get("set_prior", {}).get("predicates") or ():
        out.append(("id", p))
    return out


EXEMPT_COUNTS = ("exempt_strings", "within_labels")


def company_hits(c: Mapping[str, Any], sent: Sentinels, ngram: int, placeholder_prefix: str,
                 exempt: frozenset[str] = frozenset()) -> dict[str, int]:
    """Rule 8's last guard over one company (amendment A10): its printed strings against its own sentinels. With
    D003's change P13, a string that folds equal to one of FDA's generic terms (``exempt``) is left out of the n-gram
    scan only, and the n-gram hits that lie wholly within a printed label are counted."""
    strings = company_strings(c, placeholder_prefix)
    kinds: Counter[str] = Counter()
    for kind, s in strings:
        refused = sent.refusal.term(s) if kind == "term" else sent.refusal.string(s)
        if refused is not None:
            kinds[f"refused_{refused}"] += 1
    scanned = [s for _, s in strings if folded(s) not in exempt]
    hits = ngram_hits(scanned, sent.narratives, ngram)
    kinds["narrative_ngrams"] = len(hits)
    out = {**{k: kinds[k] for k in GUARD_KINDS}, "strings": len(strings)}
    if exempt:
        out["exempt_strings"] = len(strings) - len(scanned)
        out["within_labels"] = within_spellings(hits, [s for kind, s in strings if kind == "label"], ngram)
    return out


def guard_hits(doc: Mapping[str, Any], sents: Mapping[str, Sentinels], ngram: int,
               placeholder_prefix: str, exempt: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Rule 8's last guard over one arm, company by company (amendment A10): each company's printed strings against
    its own sentinels only. The hit counts by kind summed over the arm, the strings read, each company's counts, and
    the companies with no sentinels (``unread``: the guard cannot clear them)."""
    totals: Counter[str] = Counter()
    per: dict[str, dict[str, int]] = {}
    unread = 0
    for label, c in sorted(doc.get("companies", {}).items()):
        sent = sents.get(label)
        if sent is None:
            unread += 1
            continue
        per[label] = company_hits(c, sent, ngram, placeholder_prefix, exempt)
        totals.update(per[label])
    out = {**{k: totals[k] for k in GUARD_KINDS}, "strings": totals["strings"], "unread": unread,
           "companies": per}
    if exempt:
        out.update({k: totals[k] for k in EXEMPT_COUNTS})
    return out


def guard_arm(settings: Mapping[str, Any], name: str, doc: Mapping[str, Any], ngram: int, placeholder_prefix: str,
              backstop: Backstop) -> dict[str, Any]:
    """One arm's guard: each company's export (``doc["exports_dir"]`` and its ``companies.json``) read once, for that
    company's sentinels and for the backstop's values. A company whose export cannot be read is unread; so is a
    listed company the arm file lacks, and an arm whose companies file cannot be read is at least one unread."""
    roles = arm_roles(settings, name)
    exports = Path(doc["exports_dir"])
    in_doc = doc.get("companies", {})
    try:
        listed = load_companies(exports)
    except ValueError:
        listed = None
    sents: dict[str, Sentinels] = {}
    unread_listed = 0
    for label, entry in (listed or {}).items():
        try:
            export = read_export(exports / entry["file"])
        except ExportError:
            unread_listed += int(label not in in_doc)
            continue
        backstop.add(export, roles)
        if label in in_doc:
            sents[label] = company_sentinels(export, roles, settings["params"])
        else:
            unread_listed += 1
    hits = guard_hits(doc, sents, ngram, placeholder_prefix, exempt_labels(settings["params"]))
    hits["unread"] += unread_listed
    if listed is None:
        hits["unread"] = max(hits["unread"], 1)
    return hits


def exempt_counts(arms: Mapping[str, Mapping[str, Any]], hits: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """D003's change P13, counted for M3: per arm, the printed strings and pack strings left out of the n-gram scans
    as FDA's generic terms, and the n-gram hits that lie wholly within a printed label (the last guard) or within a
    value-map spelling (rule 1.7's check). A hit still fails M3; the counts tell a long FDA term from a fragment."""
    out: dict[str, Any] = {}
    for name, doc in sorted(arms.items()):
        checks = [c["check"]["checks"]["ngram"] for c in doc.get("companies", {}).values() if "error" not in c]
        out[name] = {"guard_exempt_strings": hits.get(name, {}).get("exempt_strings", 0),
                     "guard_within_labels": hits.get(name, {}).get("within_labels", 0),
                     "check_exempt_strings": sum(c.get("exempt_strings", 0) for c in checks),
                     "check_within_value_map": sum(c.get("within_value_map", 0) for c in checks)}
    return out


def guard_clear(hits: Mapping[str, Mapping[str, Any]]) -> bool:
    return not any(h[k] for h in hits.values() for k in (*GUARD_KINDS, "unread"))


# --------------------------------------------------------------------------------------------------- the files

def build_report(settings: Mapping[str, Any], settings_path: str, settings_sha256: str,
                 arms: Mapping[str, Mapping[str, Any]], audit: Mapping[str, Any] | None,
                 commit: str) -> dict[str, Any]:
    slim = {}
    for name, doc in sorted(arms.items()):
        slim[name] = {k: v for k, v in doc.items() if k not in ("exports_dir", "code_files")}
    files = code_files(settings)
    return {"kind": "onboard_d001_report", "schema_version": 1, "experiment": experiment_id(settings),
            "settings": {"path": settings_path, "sha256": settings_sha256}, "code_commit": commit,
            "code_hash": code_hash([ROOT / rel for rel in files]), "files": files,
            "downloads": {name: (doc.get("source") or {}).get("downloads") for name, doc in sorted(arms.items())},
            "definitions": {name: doc["definitions"] for name, doc in sorted(arms.items()) if doc.get("definitions")},
            "arms": slim, "audit": audit}


def _reader_rows(readers: Mapping[str, Any]) -> list[str]:
    rows = ["| Reader | Micro P | Micro R | Micro F1 | 95% interval | Macro F1 | 95% interval | Coverage |",
            "|---|---|---|---|---|---|---|---|"]
    for r, m in readers.items():
        mi, ma = m["micro"], m["macro"]
        rows.append(f"| {r} | {_f(mi['precision'])} | {_f(mi['recall'])} | {_f(mi['f1'])} | "
                    f"{_f(mi.get('ci_low'))} to {_f(mi.get('ci_high'))} | {_f(ma['f1'])} | "
                    f"{_f(ma['ci_low'])} to {_f(ma['ci_high'])} | {_f(m['coverage'])} |")
    return rows


def _refusal_detail(r: Mapping[str, Any]) -> str:
    """Amendment A14: what the refusal removed over an arm, in one phrase of counts."""
    return (f"refused categories {r['categories']} (corpus rows {r['rows']} of {r['corpus']}, share {_f(r['share'])}; "
            f"with R rows and a specific label {r['with_minimum']}); refused floor-passing terms {r['terms']} (would "
            f"have been assigned {r['assignable']}, of them within K {r['within_cap']}); label-name parts refused "
            f"{r['label_parts']}; companies {r['companies']}")


# D002's source-specific sentences; D003's change P5 replaces each one its settings' ``report_text`` gives
D002_TEXT = {
    "verdict": "{exp} passes only if all six criteria pass.",
    "below_criteria": "C2 compares the drafted pack with `pack/`, whose lexicons are mostly the component names "
                      "themselves: a pass means no worse than that list of names, within the margin.",
    "intervals": "Every interval resamples the sampled records of these companies. It is not an interval for the "
                 "field in general.",
    "roles_note": "Roles right: the inference's header words and markers were chosen knowing both sources' headers "
                  "(rule 1.2, amendment A9). The count says little, and for MSHA nothing.",
    "matched_names": "matched names per make",
    "audit_heading": "## Pilot audit (M4)",
    "not_decided": "not decided: {reason}",
    "kept_days": "kept days per company (training/test)",
    "label_words": "",
}


def report_text(settings: Mapping[str, Any] | None) -> dict[str, str]:
    """The report's source-specific sentences: D002's, each replaced by the settings' ``report_text`` (P5)."""
    return {**D002_TEXT, **((settings or {}).get("report_text") or {})}


def _exact_value(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} {_exact(v)}" for k, v in value.items())
    return _exact(value)


def _criterion_detail(c: Mapping[str, Any], text: Mapping[str, str] = D002_TEXT) -> str:
    """One criterion's detail: C1 and C2 by their unrounded deciding values and each comparison's outcome, and what
    the refusal removed (A14); the rest by their counts. D003's C2 when not decided (E8) says so first, with its
    reason, and the kept days per company stand beside C1 and C2 (E11)."""
    detail = []
    if c.get("decided") is False:
        detail.append(text["not_decided"].format(reason=c.get("reason")))
    exact = c.get(EXACT)
    if exact is not None:
        for key in ("diff", "diff_fraction", "ci_low", "ci_high"):
            if key in exact:
                detail.append(f"{key} {_exact_value(exact[key])}")
        detail += [f"{test}: {'yes' if ok else 'no'}" for test, ok in exact["comparisons"].items()]
    for key in ("best_control", "against", "records", "reason", "drafted", "loaded", "packs", "passed_floor",
                "last_guard_clean", "label_hits", "code_commits", "download_code_total"):
        if key in c and c[key] is not None:
            detail.append(f"{key} {_f(c[key])}")
    for key in ("scored", "min_records", "clusters", "records_ci_low", "records_ci_high"):
        if key in c and c[key] is not None:
            detail.append(f"{key} {_f(c[key])}")
    if c.get("matched_names"):
        detail.append(text["matched_names"] + " " + ", ".join(f"{k} {v}" for k, v in c["matched_names"].items()))
    if c.get("kept_days"):
        detail.append(text["kept_days"] + " " + ", ".join(f"{k} {_f(v.get('train'))}/{_f(v.get('test'))}"
                                                          for k, v in c["kept_days"].items()))
    if c.get("refusal"):
        detail.append("what the refusal removed: " + _refusal_detail(c["refusal"]))
    for arm, v in (c.get("ngram_exempt") or {}).items():
        detail.append(f"{arm}: FDA's generic terms left out of the n-gram scans (P13): check {v['check_exempt_strings']} "
                      f"strings, guard {v['guard_exempt_strings']}; n-gram hits inside a value-map spelling "
                      f"{v['check_within_value_map']}, inside a printed label {v['guard_within_labels']}")
    return "; ".join(detail)


def _arm_criteria(arm: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    c = arm.get("criterion") or {}
    return list(c) if isinstance(c, list) else [c]


def _interval(d: Mapping[str, Any] | None) -> str:
    d = d or {}
    return f"{_f(d.get('ci_low'))} to {_f(d.get('ci_high'))}"


def _days_rows(pooled: Mapping[str, Any]) -> list[str]:
    """D003's E3 (P8): each pooled reader's micro F1 with its interval over (company, received day) clusters, and
    each drafted-minus-control difference with both intervals."""
    cl = pooled["cluster"]
    rows = ["", f"Clusters (company, received day): {cl['clusters']} in all; per company "
            + ", ".join(f"{k} {v}" for k, v in cl["per_company"].items()) + ".", "",
            "| Reader | Micro F1 | 95% interval, days | Clusters |", "|---|---|---|---|"]
    for r, m in cl["readers"].items():
        rows.append(f"| {r} | {_f(m.get('f1'))} | {_interval(m)} | {_f(m.get('clusters'))} |")
    rows += ["", "| Drafted minus | Difference | 95% interval, days | 95% interval, records |", "|---|---|---|---|"]
    for r, cmp in pooled.get("compared", {}).items():
        rows.append(f"| {r} | {_f(cmp['days'].get('diff'))} | {_interval(cmp['days'])} | "
                    f"{_interval(cmp['records'])} |")
    return rows


def _space_rows(arm: Mapping[str, Any]) -> list[str]:
    """D003's C2 space (E2, P7): per hand copy, the records scored, the readers and the differences, and the hand
    predicates each company's drafted names reach."""
    rows: list[str] = []
    for h, sp in arm["space"].items():
        rows += ["", f"C2's space under the value map of `{h}`: {sp['scored']} records scored, {sp['with_gold']} with "
                 f"a nonempty gold.", "",
                 "| Reader | Micro P | Micro R | Micro F1 | 95% interval, days | 95% interval, records |",
                 "|---|---|---|---|---|---|"]
        for r, m in sp["readers"].items():
            mi = m["micro"]
            rows.append(f"| {r} | {_f(mi['precision'])} | {_f(mi['recall'])} | {_f(mi['f1'])} | {_interval(m['days'])} "
                        f"| {_interval(mi)} |")
        rows += ["", "| Drafted minus | Difference | 95% interval, days | 95% interval, records |", "|---|---|---|---|"]
        for r, cmp in sp["compared"].items():
            rows.append(f"| {r} | {_f(cmp['days'].get('diff'))} | {_interval(cmp['days'])} | "
                        f"{_interval(cmp['records'])} |")
        rows += ["", f"`{h}` in its own space: micro F1 {_f(sp['own_space']['micro']['f1'])} on "
                 f"{sp['own_space']['records']} records. Reached hand predicates per company: "
                 + "; ".join(f"{label} " + (", ".join(c["hand"][h]["reach"]) or "none")
                             + f" (most frequent: {c['hand'][h]['most_frequent_reached'] or 'none'})"
                             for label, c in arm["companies"].items() if "error" not in c and h in c.get("hand", {}))
                 + "."]
    return rows


def _subset_rows(arm: Mapping[str, Any]) -> list[str]:
    """D003's E7 and P6, reported only: C1's and C2's comparisons on the records with no echo or masked repeat."""
    rows = ["", "Reported only, deciding nothing: the comparisons on the sampled records without an echo of their own "
            "filed labels (whole label; label word) and on those without a digit-masked repeat.", "",
            "| Records | Kept | Drafted minus | Difference | 95% interval, days | 95% interval, records |",
            "|---|---|---|---|---|---|"]
    for key, sub in arm["subsets"].items():
        c1 = sub["c1"]
        rows.append(f"| {key} | {sub['records']} | {c1['against']} (C1) | {_f(c1['days'].get('diff'))} | "
                    f"{_interval(c1['days'])} | {_interval(c1['records'])} |")
        for r, cmp in (sub.get("c2") or {}).get("compared", {}).items():
            rows.append(f"| {key}, C2 space | {sub['c2']['scored']} ({sub['c2']['with_gold']} with a gold) | {r} (C2) "
                        f"| {_f(cmp['days'].get('diff'))} | {_interval(cmp['days'])} | {_interval(cmp['records'])} |")
    return rows


def _guard_rows(arm: Mapping[str, Any]) -> list[str]:
    """D003's E1 guard (P10): per company and reader, counts only."""
    rows = ["", "Reader guard (E1): rows the reader's mapping rejected, records without a primary entity, structured "
            "values left unresolved and predicates dropped for want of an entity.", "",
            "| Company | Reader | Rejected | Without a primary entity | Structured unresolved | No-entity drops |",
            "|---|---|---|---|---|---|"]
    for label, c in arm["companies"].items():
        for r, g in (c.get("reader_guard") or {}).items():
            rows.append(f"| {label} | {r} | {g['rejected']} | {g['no_primary']} | {g['structured_unresolved']} | "
                        f"{g['no_entity']} |")
    return rows


def _company_extra_rows(arm: Mapping[str, Any]) -> list[str]:
    """D003's reported measures per company (E4, E7, E11, P11): kept days, clusters, echoes, masked repeats, terms
    resting on one narrative, and the label words the refusal took out of that control."""
    rows = ["", "| Company | Kept days, training | Kept days, test | Clusters | Whole-label echo | Word echo | "
            "Masked repeats | Lexicon terms in one narrative | Printed terms in one narrative | Label words refused |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for label, c in arm["companies"].items():
        if "error" in c:
            continue
        e = c.get("echo") or {}
        t = c.get("single_narrative_terms") or {}
        lw = (c.get("controls") or {}).get("label_words") or {}
        rows.append(f"| {label} | {_f(c['counts'].get('kept_days_train'))} | {_f(c['counts'].get('kept_days_test'))} | "
                    f"{_f(c.get('clusters'))} | {_f(e.get('label_echo'))} | {_f(e.get('word_echo'))} | "
                    f"{_f(e.get('masked_repeat'))} | {_f(t.get('lexicon_single'))} of {_f(t.get('lexicon_terms'))} | "
                    f"{_f(t.get('printed_single'))} of {_f(t.get('printed_terms'))} | {_f(lw.get('refused'))} |")
    return rows


def render(doc: Mapping[str, Any], settings: Mapping[str, Any] | None = None) -> str:
    """``report.md``. Without settings that change it (P3, P5), D002's text byte for byte; D003's tables (P6 to P11)
    appear only when the arm holds them."""
    text = report_text(settings)
    exp = doc["experiment"]
    lines = [f"# {exp}: can a pack be drafted from an export alone?", "",
             f"Verdict: **{doc['verdict']}**. " + text["verdict"].format(exp=exp), "",
             "| Criterion | Passes | Detail |", "|---|---|---|"]
    for cid in (listed_criteria(settings) if settings is not None else CRITERIA):
        c = doc["criteria"].get(cid, {})
        lines.append(f"| {cid} | {'yes' if c.get('passed') else 'no'} | {_criterion_detail(c, text)} |")
    lines += ["", "Every number below comes from the run. Filed categories are the answer key, not checked labels.",
              text["below_criteria"], ""]
    for name, arm in doc["arms"].items():
        lines += [f"## Arm {name}", ""]
        pooled = arm.get("pooled", {})
        lines += [f"Sampled records, pooled: {pooled.get('records')}. Share whose narrative holds its own filed label: "
                  f"{_f(pooled.get('label_in_text_share'))}.", ""]
        sel = (arm.get("source") or {}).get("selection")
        if sel:
            src = arm["source"]
            ex = sel["excluded"]
            dropped = ", ".join(f"{k} ({v})" for k, v in sorted((src.get("not_used") or {}).items()))
            lines += [f"Companies: {sel['candidates']} names in both count lists; excluded as a placeholder "
                      f"{ex.get('placeholder')}, not searchable {ex.get('not_searchable')}, a variant spelling "
                      f"{ex.get('variant')}, too few reports {ex.get('too_few')}; {sel['qualifying']} qualify, "
                      f"{sel['chosen']} chosen, {src.get('used')} used" + (f"; not used: {dropped}" if dropped else "")
                      + ".", ""]
        if pooled.get("echo"):
            e = pooled["echo"]
            lines += [f"Shares of the sampled records, pooled: holding a word of their own filed label "
                      f"{_f(e.get('word_echo'))}; repeating a training or another test narrative once digits are "
                      f"masked {_f(e.get('masked_repeat'))}.", ""]
        lines += [text["intervals"], "",
                  "label_names is a mechanical split of each label at '/', ',', ';', brackets and the joining words. "
                  "It is not how a person would start a hand pack.", ""]
        if "label_words" in pooled.get("readers", {}) and text["label_words"]:
            lines += [text["label_words"], ""]
        lines += _reader_rows(pooled.get("readers", {}))
        lines += ["", "| Drafted minus | Difference | 95% interval |", "|---|---|---|"]
        for r, dif in pooled.get("differences", {}).items():
            lines.append(f"| {r} | {_f(dif.get('diff'))} | {_f(dif.get('ci_low'))} to {_f(dif.get('ci_high'))} |")
        if pooled.get("cluster"):
            lines += _days_rows(pooled)
        matched = arm.get("matched")
        if matched:
            lines += ["", "Matched label space (rule 2.2):", "",
                      "| Hand pack | Records | Drafted F1 | Hand F1 | Drafted minus hand | 95% interval | "
                      "Hand F1, own space |", "|---|---|---|---|---|---|---|"]
            for h, m in matched["hand"].items():
                lines.append(f"| {h} | {m['records']} | {_f(m['drafted']['micro']['f1'])} | "
                             f"{_f(m['hand']['micro']['f1'])} | {_f(m['difference'].get('diff'))} | "
                             f"{_f(m['difference'].get('ci_low'))} to {_f(m['difference'].get('ci_high'))} | "
                             f"{_f(m['own_space']['micro']['f1'])} |")
        if arm.get("space"):
            lines += _space_rows(arm)
        if arm.get("subsets"):
            lines += _subset_rows(arm)
        if any(c.get("reader_guard") for c in arm["companies"].values()):
            lines += _guard_rows(arm)
        crits = _arm_criteria(arm)
        best = next((c.get("best_control") for c in crits if c.get("best_control")), None)
        refusal = crits[0].get("refusal") if crits else None
        if refusal:
            lines += ["", "What the refusal removed (amendment A14), summed over the companies: "
                          + _refusal_detail(refusal) + ". No refused label or term is printed."]
        lines += ["", "| Company | Corpus | Predicates | Placeholders | Other share | Refused categories: count, "
                      "corpus rows, share | Refused terms: count, would be assigned, within K | Label-name parts "
                      "refused | Drawn | Drafted F1 | Best control F1 | Roles right (see note) | Floor |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for label, c in arm["companies"].items():
            if "error" in c:
                lines.append(f"| {label} | not drafted: {c['error']} | | | | | | | | | | | |")
                continue
            dr = c["draft"]
            rf = dr["refusal"]
            best_f1 = c["readers"][best]["micro"]["f1"] if best in c.get("readers", {}) else None
            lines.append(f"| {label} | {dr['training']['corpus']} | {dr['categories']['specific']} | "
                         f"{dr['placeholders']} | {_f(dr['other_bucket_share'])} | "
                         f"{rf['categories']}, {rf['rows']}, {_f(rf['share'])} | "
                         f"{rf['terms']}, {rf['assignable']}, {rf['within_cap']} | "
                         f"{c['controls']['label_names']['refused']} | {c['sample']['drawn']} | "
                         f"{_f(c['readers']['drafted']['micro']['f1'])} | {_f(best_f1)} | "
                         f"{dr['roles']['right']} of 5 | {'passed' if c['check']['passed'] else 'failed'} |")
        if any("echo" in c or "single_narrative_terms" in c for c in arm["companies"].values()):
            lines += _company_extra_rows(arm)
        lines += ["", "Refused categories: the corpus rows of each, largest first: "
                  + "; ".join(f"{label} " + (", ".join(str(n) for n in c["draft"]["refusal"]["category_rows"])
                                             or "none")
                              for label, c in arm["companies"].items() if "error" not in c) + "."]
        lines += ["", text["roles_note"]]
        for label, c in arm["companies"].items():
            if "error" in c:
                continue
            lines += ["", f"### {name} {label}: predicates and first terms", ""]
            if c.get("strings_withheld"):
                lines.append("Labels and terms withheld: this pack failed the privacy floor.")
                continue
            for p in c["draft"]["predicates"]:
                lines.append(f"- {p['label']} ({p['records']} corpus records; {p['refused_assignable']} refused terms "
                             f"would have been assigned): {', '.join(p['first_terms'])}")
    if "M4" in doc["criteria"]:
        a = doc.get("audit")
        lines += ["", text["audit_heading"], ""]
        if a is None:
            lines.append("No audit.json.")
        else:
            lines.append(f"Records {a['records']}; sites {a['sites']}; weeks evaluated {a['weeks_evaluated']}; "
                         f"review list {a['review_list']}; alerts by channel: "
                         + ", ".join(f"{k} {_f(v)}" for k, v in a["alerts"].items()) + ".")
    lines += ["", "## Code and inputs", "",
              f"- Code commit `{doc['code_commit']}`; code hash of the onboard package and the download code "
              f"`{doc['code_hash']}`; settings sha256 `{doc['settings']['sha256']}`."]
    for path, n in doc["criteria"]["M1"]["download_code_lines"].items():
        lines.append(f"- `{path}`: {_f(n)} lines (per-field code: the download code).")
    for name, downloads in doc["downloads"].items():
        for item in downloads or ():
            lines.append(f"- {name} download `{item['file']}`: {item['bytes']} bytes, sha256 `{item['sha256']}`.")
    for name, defs in doc["definitions"].items():
        lines += ["", f"### {name}: the definition file's lines for the declared columns", ""]
        for column, text_lines in defs.items():
            lines.append(f"- `{column}`:")
            lines += [f"  > {line}" for line in text_lines] or ["  > (no line names it)"]
    return "\n".join(lines) + "\n"


def withheld(experiment: str, hits: Mapping[str, Mapping[str, Any]], unread: int = 0,
             backstop: Mapping[str, int] | None = None) -> tuple[dict[str, Any], str]:
    """The report that replaces a report the last guard did not clear: a hit, a backstop match, or an export it could
    not read. It names the kinds of hit, per arm and per company, never a string."""
    backstop = dict(backstop or dict.fromkeys(BACKSTOP_KINDS, 0))
    totals = {k: sum(h.get(k, 0) for h in hits.values()) for k in GUARD_KINDS}
    found = [k for k in GUARD_KINDS if totals[k]]
    matched = [k for k in BACKSTOP_KINDS if backstop.get(k)]
    said = []
    if found:
        said.append("The last guard found a printed label, id, term or error text that "
                    + "; or that ".join(GUARD_SAYS[k] for k in found) + ".")
    if matched:
        said.append("The backstop found in the rendered report, as a whole token, "
                    + " and ".join(BACKSTOP_SAYS[k] for k in matched) + ".")
    if unread:
        said.append(f"The last guard could not read {unread} export(s), so it cannot clear the report.")
    reasons = found + matched
    reason = "the last guard found: " + ", ".join(reasons) if reasons else "the last guard could not read every export"
    by_company = {name: {label: {k: v for k, v in h.items() if k in GUARD_KINDS and v}
                         for label, h in sorted(arm.get("companies", {}).items())}
                  for name, arm in sorted(hits.items())}
    doc = {"kind": "onboard_d001_report", "schema_version": 1, "experiment": experiment, "verdict": "withheld",
           "guard_hits": {name: {k: v for k, v in h.items() if k != "companies"} for name, h in sorted(hits.items())},
           "guard_hits_by_company": by_company, "backstop_hits": backstop, "exports_unread": unread,
           "criteria": {"M3": {"passed": False, "reason": reason}}}
    companies = "; ".join(f"{name} {label} " + ", ".join(f"{k} {v}" for k, v in kinds.items())
                          for name, per in by_company.items() for label, kinds in per.items() if kinds)
    md = (f"# {experiment} report withheld\n\n" + " ".join(said) + " The report, the arm files and the drafted packs "
          "are withheld, and M3 fails. Hits by kind: " + ", ".join(f"{k} {totals[k]}" for k in GUARD_KINDS)
          + "; backstop " + ", ".join(f"{k} {backstop.get(k, 0)}" for k in BACKSTOP_KINDS)
          + f"; exports unread {unread}." + (f" Hits by company: {companies}." if companies else "") + "\n")
    return doc, md


def block(name: str, data: bytes, experiment: str) -> str:
    text = data.decode("utf-8")
    n = len(text.splitlines())
    return (f"=== {experiment} {name} BEGIN lines={n} sha256={hashlib.sha256(data).hexdigest()} ===\n{text}"
            + ("" if text.endswith("\n") else "\n") + f"=== {experiment} {name} END ===")


def run_report(settings: Mapping[str, Any], settings_path: str, settings_sha256: str, arm_dirs: Sequence[str | Path],
               audit_path: str | Path | None, out: str | Path, commit: Callable[[], str],
               emit: Callable[[str], None] = print) -> dict[str, Any]:
    experiment = experiment_id(settings)
    arms = load_arms(arm_dirs)
    audit = audit_summary(audit_path) if audit_path is not None else None
    prefix = load_template()["ids.json"]["placeholder_prefix"]
    ngram = settings["report_guard"]["ngram"]
    backstop = Backstop(settings["params"]["refusal"]["reference_inside_min_chars"])
    hits = {name: guard_arm(settings, name, doc, ngram, prefix, backstop) for name, doc in sorted(arms.items())}
    unread = sum(h["unread"] for h in hits.values())
    if not guard_clear(hits):
        doc, text_md = withheld(experiment, hits, unread)
    else:
        doc = build_report(settings, settings_path, settings_sha256, arms, audit, commit())
        doc["criteria"] = criteria(settings, arms, audit, guard_clean=True)
        doc["criteria"]["M3"]["exports_unread"] = 0
        doc["criteria"]["M3"]["guard_strings"] = {name: h["strings"] for name, h in sorted(hits.items())}
        doc["criteria"]["M3"]["backstop_values"] = {k: len(v) for k, v in sorted(backstop.values.items())}
        if exempt_labels(settings["params"]):
            doc["criteria"]["M3"]["ngram_exempt"] = exempt_counts(arms, hits)
        doc["verdict"] = "pass" if all(c["passed"] for c in doc["criteria"].values()) else "fail"
        doc = rounded(doc)
        # D001's and D002's settings change nothing in the rendering: the call stays as D002 made it
        text_md = render(doc, settings) if settings.get("report_text") or settings.get("criteria") else render(doc)
        text_json = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        found = backstop.hits([text_json, text_md, *json_strings(doc)])
        if any(found.values()):
            doc, text_md = withheld(experiment, hits, unread, found)
    text_json = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    data_json, data_md = text_json.encode("utf-8"), text_md.encode("utf-8")
    (out / "report.json").write_bytes(data_json)
    (out / "report.md").write_bytes(data_md)
    emit(block("report.json", data_json, experiment))
    emit(block("report.md", data_md, experiment))
    return doc

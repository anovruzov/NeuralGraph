"""D001's report: both arms, the checks, the audit summary and the six criteria (rules 6 and 8, as amended).

    python -m mycelic.collective.onboard report --settings FILE --arms DIR [DIR ...] --audit FILE --out DIR

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

**The last guard** (rule 8 as amended, A5; :func:`guard_hits`) runs before anything is written. Per arm, it reads the
strings the report prints that came from records (:func:`printed_strings`: category labels, predicate ids, lexicon
terms and error texts) and checks them against that arm's exports, every row of every company: a string the one
:class:`.draft.Refusal` refuses (a term by its term rule), or one that holds ``ngram`` consecutive tokens of a
narrative, is a hit. The report's fixed words, its keys, the settings' labels and the definition lines are not from
records and are not scanned. A hit, or an export the guard cannot read, writes a report holding only the hit counts by
kind and the verdict ``withheld``, and M3 fails; the workflow then uploads nothing else.

Both files are printed between markers, ``=== D001 report.json BEGIN lines=<n> sha256=<hex> ===`` and ``... END ===``.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..experiments.common import code_hash
from ..jsonio import strict_load
from .check import column_names, ngram_hits, package_hits
from .draft import REFUSAL_KINDS, Refusal, _cells, load_template
from .exports import ExportError, read_export
from .score import EXACT, ROOT, arm_roles, load_companies, rounded, tree_hashes

CRITERIA = ("C1", "C2", "M1", "M2", "M3", "M4")
ONBOARD_DIR = "mycelic/collective/onboard"
TOOLS_DIR = "tools/onboard"
AUDIT_CHANNELS = ("X", "S")
GUARD_KINDS = (*(f"refused_{k}" for k in REFUSAL_KINDS), "narrative_ngrams")
GUARD_SAYS = {"refused_equal": "equals a value of a record-id, site or forbidden column",
              "refused_inside": "holds such a value as whole words",
              "refused_name_word": "equals a word of a forbidden value of two or more words",
              "narrative_ngrams": "holds consecutive words of a narrative"}


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


def criteria(settings: Mapping[str, Any], arms: Mapping[str, Mapping[str, Any]],
             audit: Mapping[str, Any] | None, guard_clean: bool) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, spec in settings["arms"].items():
        cid = spec["criterion"]["id"]
        doc = arms.get(name)
        out[cid] = dict(doc["criterion"]) if doc is not None else {"id": cid, "passed": False,
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
    ok = audit is not None and audit["records"] >= 1 and all(audit["channels_run"].get(ch) for ch in AUDIT_CHANNELS)
    out["M4"] = {"passed": bool(ok), "audit": audit}
    return {k: out[k] for k in CRITERIA if k in out}


# --------------------------------------------------------------------------------------------------- the last guard

class Sentinels:
    """What one arm's printed strings must not carry (amendment A5): the arm's one :class:`.draft.Refusal`, from every
    row of every company export of the arm, its narratives for the n-gram scan, and how many exports it could not
    read."""

    def __init__(self, refusal: Refusal, narratives: list[str], unread: int = 0) -> None:
        self.refusal = refusal
        self.narratives = narratives
        self.unread = unread


def arm_sentinels(settings: Mapping[str, Any], name: str, doc: Mapping[str, Any]) -> Sentinels:
    """One arm's sentinels from its exports (``doc["exports_dir"]``): every row's record-id, site and forbidden
    values, and every narrative."""
    roles = arm_roles(settings, name)
    exports = Path(doc["exports_dir"])
    references: list[str] = []
    forbidden: list[str] = []
    narratives: list[str] = []
    unread = 0
    try:
        companies = load_companies(exports)
    except ValueError:
        return Sentinels(Refusal((), (), settings["params"]), [], 1)
    for entry in companies.values():
        try:
            export = read_export(exports / entry["file"])
        except ExportError:
            unread += 1
            continue
        for r in range(len(export)):
            if export.has(roles.narrative):
                n = export.value(r, roles.narrative)
                if n is not None:
                    narratives.append(n)
            for col in (roles.record_id, roles.site):
                if export.has(col):
                    references.extend(_cells(export.value(r, col)))
            for col in roles.forbidden:
                if export.has(col):
                    forbidden.extend(_cells(export.value(r, col)))
    return Sentinels(Refusal(references, forbidden, settings["params"]), narratives, unread)


def sentinels(settings: Mapping[str, Any], arms: Mapping[str, Mapping[str, Any]]) -> dict[str, Sentinels]:
    """Each arm's own sentinels: an arm's strings are never checked against another arm's values."""
    return {name: arm_sentinels(settings, name, doc) for name, doc in sorted(arms.items())}


def printed_strings(doc: Mapping[str, Any], placeholder_prefix: str) -> list[tuple[str, str]]:
    """``(kind, string)`` for every string of an arm file that the report prints and that came from records: category
    labels, predicate ids (placeholders and the majority prior's among them), lexicon terms and error texts. A company
    whose strings were withheld (its pack failed the floor) has none."""
    out: list[tuple[str, str]] = []
    for c in doc.get("companies", {}).values():
        if "error" in c:
            out.append(("error", c["error"]))
            continue
        loader_error = c.get("pack", {}).get("loader", {}).get("error")
        if loader_error:
            out.append(("error", loader_error))
        if c.get("strings_withheld"):
            continue
        out += [("label", x["label"]) for x in c["draft"]["categories"]["passing_floor"]]
        for p in c["draft"]["predicates"]:
            out += [("label", p["label"]), ("id", p["id"])]
            out += [("id" if t.startswith(placeholder_prefix) else "term", t) for t in p["first_terms"]]
        top = c.get("controls", {}).get("majority_prior", {}).get("predicate")
        if top:
            out.append(("id", top))
    return out


def guard_hits(doc: Mapping[str, Any], sent: Sentinels, ngram: int, placeholder_prefix: str) -> dict[str, int]:
    """Rule 8's last guard over one arm (amendment A5): hit counts by kind, and how many strings it read."""
    strings = printed_strings(doc, placeholder_prefix)
    kinds: Counter[str] = Counter()
    for kind, s in strings:
        refused = sent.refusal.term(s) if kind == "term" else sent.refusal.string(s)
        if refused is not None:
            kinds[f"refused_{refused}"] += 1
    kinds["narrative_ngrams"] = len(ngram_hits([s for _, s in strings], sent.narratives, ngram))
    return {**{k: kinds[k] for k in GUARD_KINDS}, "strings": len(strings)}


# --------------------------------------------------------------------------------------------------- the files

def build_report(settings: Mapping[str, Any], settings_path: str, settings_sha256: str,
                 arms: Mapping[str, Mapping[str, Any]], audit: Mapping[str, Any] | None,
                 commit: str) -> dict[str, Any]:
    slim = {}
    for name, doc in sorted(arms.items()):
        slim[name] = {k: v for k, v in doc.items() if k not in ("exports_dir", "code_files")}
    files = code_files(settings)
    return {"kind": "onboard_d001_report", "schema_version": 1, "experiment": "D001",
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


def _criterion_detail(c: Mapping[str, Any]) -> str:
    """One criterion's detail: C1 and C2 by their unrounded deciding values and each comparison's outcome; the rest
    by their counts."""
    detail = []
    exact = c.get(EXACT)
    if exact is not None:
        for key in ("diff", "diff_fraction", "ci_low", "ci_high"):
            if key in exact:
                detail.append(f"{key} {_exact(exact[key])}")
        detail += [f"{test}: {'yes' if ok else 'no'}" for test, ok in exact["comparisons"].items()]
    for key in ("best_control", "against", "records", "reason", "drafted", "loaded", "packs", "passed_floor",
                "last_guard_clean", "label_hits", "code_commits", "download_code_total"):
        if key in c and c[key] is not None:
            detail.append(f"{key} {_f(c[key])}")
    if c.get("matched_names"):
        detail.append("matched names per make " + ", ".join(f"{k} {v}" for k, v in c["matched_names"].items()))
    return "; ".join(detail)


def render(doc: Mapping[str, Any]) -> str:
    lines = ["# D001: can a pack be drafted from an export alone?", "",
             f"Verdict: **{doc['verdict']}**. D001 passes only if all six criteria pass.", "",
             "| Criterion | Passes | Detail |", "|---|---|---|"]
    for cid in CRITERIA:
        c = doc["criteria"].get(cid, {})
        lines.append(f"| {cid} | {'yes' if c.get('passed') else 'no'} | {_criterion_detail(c)} |")
    lines += ["", "Every number below comes from the run. Filed categories are the answer key, not checked labels.",
              "C2 compares the drafted pack with `pack/`, whose lexicons are mostly the component names themselves: "
              "a pass means no worse than that list of names, within the margin.", ""]
    for name, arm in doc["arms"].items():
        lines += [f"## Arm {name}", ""]
        pooled = arm.get("pooled", {})
        lines += [f"Sampled records, pooled: {pooled.get('records')}. Share whose narrative holds its own filed label: "
                  f"{_f(pooled.get('label_in_text_share'))}.", "",
                  "Every interval resamples the sampled records of these companies. It is not an interval for the "
                  "field in general.", "",
                  "label_names is a mechanical split of each label at '/', ',', ';', brackets and the joining words. "
                  "It is not how a person would start a hand pack.", ""]
        lines += _reader_rows(pooled.get("readers", {}))
        lines += ["", "| Drafted minus | Difference | 95% interval |", "|---|---|---|"]
        for r, dif in pooled.get("differences", {}).items():
            lines.append(f"| {r} | {_f(dif.get('diff'))} | {_f(dif.get('ci_low'))} to {_f(dif.get('ci_high'))} |")
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
        best = (arm.get("criterion") or {}).get("best_control")
        lines += ["", "| Company | Corpus | Predicates | Placeholders | Other share | Refused categories | "
                      "Refused terms | Drawn | Drafted F1 | Best control F1 | Roles right (see note) | Floor |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for label, c in arm["companies"].items():
            if "error" in c:
                lines.append(f"| {label} | not drafted: {c['error']} | | | | | | | | | | |")
                continue
            dr = c["draft"]
            best_f1 = c["readers"][best]["micro"]["f1"] if best in c.get("readers", {}) else None
            lines.append(f"| {label} | {dr['training']['corpus']} | {dr['categories']['specific']} | "
                         f"{dr['placeholders']} | {_f(dr['other_bucket_share'])} | {dr['categories']['refused']} | "
                         f"{dr['terms']['refused']} | {c['sample']['drawn']} | "
                         f"{_f(c['readers']['drafted']['micro']['f1'])} | {_f(best_f1)} | "
                         f"{dr['roles']['right']} of 5 | {'passed' if c['check']['passed'] else 'failed'} |")
        lines += ["", "Roles right: the inference's header words and markers were chosen knowing both sources' "
                      "headers (rule 1.2, amendment A9). The count says little, and for MSHA nothing."]
        for label, c in arm["companies"].items():
            if "error" in c:
                continue
            lines += ["", f"### {name} {label}: predicates and first terms", ""]
            if c.get("strings_withheld"):
                lines.append("Labels and terms withheld: this pack failed the privacy floor.")
                continue
            for p in c["draft"]["predicates"]:
                lines.append(f"- {p['label']} ({p['records']} corpus records): {', '.join(p['first_terms'])}")
    a = doc.get("audit")
    lines += ["", "## Pilot audit (M4)", ""]
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
        for column, text in defs.items():
            lines.append(f"- `{column}`:")
            lines += [f"  > {line}" for line in text] or ["  > (no line names it)"]
    return "\n".join(lines) + "\n"


def withheld(hits: Mapping[str, Mapping[str, int]], unread: int = 0) -> tuple[dict[str, Any], str]:
    """The report that replaces a report the last guard did not clear: a hit, or an export it could not read. It
    names the kinds of hit, never a string."""
    totals = {k: sum(h.get(k, 0) for h in hits.values()) for k in GUARD_KINDS}
    found = [k for k in GUARD_KINDS if totals[k]]
    said = []
    if found:
        said.append("The last guard found a printed label, id, term or error text that "
                    + "; or that ".join(GUARD_SAYS[k] for k in found) + ".")
    if unread:
        said.append(f"The last guard could not read {unread} export(s), so it cannot clear the report.")
    reason = "the last guard found: " + ", ".join(found) if found else "the last guard could not read every export"
    doc = {"kind": "onboard_d001_report", "schema_version": 1, "experiment": "D001", "verdict": "withheld",
           "guard_hits": {name: dict(h) for name, h in sorted(hits.items())}, "exports_unread": unread,
           "criteria": {"M3": {"passed": False, "reason": reason}}}
    md = ("# D001 report withheld\n\n" + " ".join(said) + " The report, the arm files and the drafted packs are "
          "withheld, and M3 fails. Hits by kind: " + ", ".join(f"{k} {totals[k]}" for k in GUARD_KINDS)
          + f"; exports unread {unread}.\n")
    return doc, md


def block(name: str, data: bytes) -> str:
    text = data.decode("utf-8")
    n = len(text.splitlines())
    return (f"=== D001 {name} BEGIN lines={n} sha256={hashlib.sha256(data).hexdigest()} ===\n{text}"
            + ("" if text.endswith("\n") else "\n") + f"=== D001 {name} END ===")


def run_report(settings: Mapping[str, Any], settings_path: str, settings_sha256: str, arm_dirs: Sequence[str | Path],
               audit_path: str | Path, out: str | Path, commit: Callable[[], str],
               emit: Callable[[str], None] = print) -> dict[str, Any]:
    arms = load_arms(arm_dirs)
    audit = audit_summary(audit_path)
    sents = sentinels(settings, arms)
    prefix = load_template()["ids.json"]["placeholder_prefix"]
    ngram = settings["report_guard"]["ngram"]
    hits = {name: guard_hits(doc, sents[name], ngram, prefix) for name, doc in sorted(arms.items())}
    unread = sum(s.unread for s in sents.values())
    if unread or any(h[k] for h in hits.values() for k in GUARD_KINDS):
        doc, text_md = withheld(hits, unread)
    else:
        doc = build_report(settings, settings_path, settings_sha256, arms, audit, commit())
        doc["criteria"] = criteria(settings, arms, audit, guard_clean=True)
        doc["criteria"]["M3"]["exports_unread"] = 0
        doc["criteria"]["M3"]["guard_strings"] = {name: h["strings"] for name, h in sorted(hits.items())}
        doc["verdict"] = "pass" if all(c["passed"] for c in doc["criteria"].values()) else "fail"
        doc = rounded(doc)
        text_md = render(doc)
    text_json = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    data_json, data_md = text_json.encode("utf-8"), text_md.encode("utf-8")
    (out / "report.json").write_bytes(data_json)
    (out / "report.md").write_bytes(data_md)
    emit(block("report.json", data_json))
    emit(block("report.md", data_md))
    return doc

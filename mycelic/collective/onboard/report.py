"""D001's report: both arms, the checks, the audit summary and the six criteria (rules 6 and 8).

    python -m mycelic.collective.onboard report --settings FILE --arms DIR [DIR ...] --audit FILE --out DIR

* **C1 and C2** come from the arms (``arm.json``'s ``criterion``), each from the arm the settings give it to.
* **M1, no code per field:** both arms ran the same code commit and the same onboard code; no file of the onboard
  package holds a column name of either source (:func:`.check.package_hits`); no drafted label equals one; the
  download scripts' line counts are given.
* **M2, the packs load:** every company of both arms was drafted and its pack passed the loader's check.
* **M3, the privacy floor:** every drafted pack passed rule 1.7, and the last guard found nothing.
* **M4, the audit:** ``audit.json`` exists, holds at least one record, and channels X and S ran (no reason given,
  an alert timeline present).

**The last guard** (rule 8, :func:`guard_hits`) runs before anything is written: the report's text is scanned for any
``ngram`` consecutive tokens of a narrative of either arm's exports, any record id or site value of at least
``ref_min_chars`` characters, and any forbidden value of at least ``forbidden_min_chars`` characters that holds a
letter, each as whole words (``find_bounded`` on the text as written). A hit writes a report holding only the hit
counts by kind and the verdict ``withheld``, and M3 fails. An export the guard cannot read withholds the report the
same way: an incomplete guard cannot clear it.

Both files are printed between markers, ``=== D001 report.json BEGIN lines=<n> sha256=<hex> ===`` and ``... END ===``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..experiments.common import code_hash
from ..jsonio import strict_load
from ..packs.canonical import find_bounded
from .check import column_names, ngram_hits, package_hits
from .draft import ValueIndex, _cells
from .exports import ExportError, read_export
from .score import ROOT, arm_roles, load_companies, rounded, tree_hashes

CRITERIA = ("C1", "C2", "M1", "M2", "M3", "M4")
ONBOARD_DIR = "mycelic/collective/onboard"
TOOLS_DIR = "tools/onboard"
AUDIT_CHANNELS = ("X", "S")
_RUN = re.compile(r"[^\W_]+")


def _f(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


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


def script_lines(settings: Mapping[str, Any]) -> dict[str, int]:
    out = {}
    for arm in settings["arms"].values():
        path = arm["download_script"]
        p = ROOT / path
        out[path] = len(p.read_text(encoding="utf-8").splitlines()) if p.is_file() else None
    return dict(sorted(out.items()))


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
            if "error" in c:
                continue
            labels += sum(1 for x in c["draft"]["categories"]["passing_floor"] if x["label"] in names)
    lines = script_lines(settings)
    all_arms = set(arms) == set(settings["arms"])
    out["M1"] = {"passed": bool(all_arms and len(commits) == 1 and "unknown" not in commits and len(code) == 1
                                and not pkg and labels == 0 and all(v is not None for v in lines.values())),
                 "code_commits": len(commits), "package_hits": pkg, "label_hits": labels,
                 "download_script_lines": lines}
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
    """What the report must never hold, read from both arms' exports: narratives, record ids, site values and
    forbidden values (rule 8's lengths)."""

    def __init__(self, narratives: list[str], refs: Iterable[str], forbidden: Iterable[str],
                 unread: int = 0) -> None:
        self.narratives = narratives
        self.refs = ValueIndex(refs)
        self.forbidden = ValueIndex(forbidden)
        self.unread = unread


def sentinels(settings: Mapping[str, Any], arms: Mapping[str, Mapping[str, Any]]) -> Sentinels:
    guard = settings["report_guard"]
    narratives: list[str] = []
    refs: set[str] = set()
    forbidden: set[str] = set()
    unread = 0
    for name, doc in arms.items():
        roles = arm_roles(settings, name)
        exports = Path(doc["exports_dir"])
        try:
            companies = load_companies(exports)
        except ValueError:
            unread += 1
            continue
        for entry in companies.values():
            try:
                export = read_export(exports / entry["file"])
            except ExportError:
                unread += 1
                continue
            for r in range(len(export)):
                n = export.value(r, roles.narrative) if export.has(roles.narrative) else None
                if n is not None:
                    narratives.append(n)
                for col in (roles.record_id, roles.site):
                    if export.has(col):
                        refs.update(v for v in _cells(export.value(r, col)) if len(v) >= guard["ref_min_chars"])
                for col in roles.forbidden:
                    if export.has(col):
                        forbidden.update(v for v in _cells(export.value(r, col))
                                         if len(v) >= guard["forbidden_min_chars"] and any(ch.isalpha() for ch in v))
    return Sentinels(narratives, refs, forbidden, unread)


def _all_found(index: ValueIndex, text: str) -> set[str]:
    found = set()
    for run in set(_RUN.findall(text)):
        for v in index.by_run.get(run, ()):
            if find_bounded(v, text):
                found.add(v)
    for v in index.general:
        if find_bounded(v, text):
            found.add(v)
    return found


def guard_hits(texts: Sequence[str], sent: Sentinels, ngram: int) -> dict[str, int]:
    """Rule 8's last guard over the report's texts: hit counts by kind (distinct n-grams or values)."""
    joined = "\n".join(texts)
    return {"narrative_ngrams": len(ngram_hits(texts, sent.narratives, ngram)),
            "record_ids_or_sites": len(_all_found(sent.refs, joined)),
            "forbidden_values": len(_all_found(sent.forbidden, joined))}


# --------------------------------------------------------------------------------------------------- the files

def build_report(settings: Mapping[str, Any], settings_path: str, settings_sha256: str,
                 arms: Mapping[str, Mapping[str, Any]], audit: Mapping[str, Any] | None,
                 commit: str) -> dict[str, Any]:
    slim = {}
    for name, doc in sorted(arms.items()):
        slim[name] = {k: v for k, v in doc.items() if k not in ("exports_dir", "code_files")}
    files = tree_hashes((ONBOARD_DIR, TOOLS_DIR))
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


def render(doc: Mapping[str, Any]) -> str:
    lines = ["# D001: can a pack be drafted from an export alone?", "",
             f"Verdict: **{doc['verdict']}**. D001 passes only if all six criteria pass.", "",
             "| Criterion | Passes | Detail |", "|---|---|---|"]
    for cid in CRITERIA:
        c = doc["criteria"].get(cid, {})
        detail = []
        for key in ("diff", "ci_low", "ci_high", "best_control", "against", "records", "reason", "drafted", "loaded",
                    "packs", "passed_floor", "last_guard_clean", "label_hits", "code_commits"):
            if key in c and c[key] is not None:
                detail.append(f"{key} {_f(c[key])}")
        lines.append(f"| {cid} | {'yes' if c.get('passed') else 'no'} | {'; '.join(detail)} |")
    lines += ["", "Every number below comes from the run. Filed categories are the answer key, not checked labels.", ""]
    for name, arm in doc["arms"].items():
        lines += [f"## Arm {name}", ""]
        pooled = arm.get("pooled", {})
        lines += [f"Sampled records, pooled: {pooled.get('records')}. Share whose narrative holds its own filed label: "
                  f"{_f(pooled.get('label_in_text_share'))}.", ""]
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
        lines += ["", "| Company | Corpus | Predicates | Placeholders | Other share | Drawn | Drafted F1 | "
                      "Roles right | Floor |", "|---|---|---|---|---|---|---|---|---|"]
        for label, c in arm["companies"].items():
            if "error" in c:
                lines.append(f"| {label} | not drafted: {c['error']} | | | | | | | |")
                continue
            dr = c["draft"]
            lines.append(f"| {label} | {dr['training']['corpus']} | {dr['categories']['specific']} | "
                         f"{dr['placeholders']} | {_f(dr['other_bucket_share'])} | {c['sample']['drawn']} | "
                         f"{_f(c['readers']['drafted']['micro']['f1'])} | {dr['roles']['right']} of 5 | "
                         f"{'passed' if c['check']['passed'] else 'failed'} |")
        for label, c in arm["companies"].items():
            if "error" in c:
                continue
            lines += ["", f"### {name} {label}: predicates and first terms", ""]
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
              f"- Code commit `{doc['code_commit']}`; code hash of the onboard package and its download scripts "
              f"`{doc['code_hash']}`; settings sha256 `{doc['settings']['sha256']}`."]
    for path, n in doc["criteria"]["M1"]["download_script_lines"].items():
        lines.append(f"- `{path}`: {_f(n)} lines (per-field code: the download script).")
    for name, downloads in doc["downloads"].items():
        for item in downloads or ():
            lines.append(f"- {name} download `{item['file']}`: {item['bytes']} bytes, sha256 `{item['sha256']}`.")
    for name, defs in doc["definitions"].items():
        lines += ["", f"### {name}: the definition file's lines for the declared columns", ""]
        for column, text in defs.items():
            lines.append(f"- `{column}`:")
            lines += [f"  > {line}" for line in text] or ["  > (no line names it)"]
    return "\n".join(lines) + "\n"


def withheld(hits: Mapping[str, int], unread: int = 0) -> tuple[dict[str, Any], str]:
    """The report that replaces a report the last guard did not clear: a hit, or an export it could not read."""
    if unread:
        reason = "the last guard could not read every export"
        said = f"The last guard could not read {unread} export(s), so it cannot clear the report."
    else:
        reason = "the last guard found record text in the report"
        said = "The last guard found what the report must never hold."
    doc = {"kind": "onboard_d001_report", "schema_version": 1, "experiment": "D001", "verdict": "withheld",
           "guard_hits": dict(hits), "exports_unread": unread,
           "criteria": {"M3": {"passed": False, "reason": reason}}}
    md = (f"# D001 report withheld\n\n{said} The report is withheld and M3 fails. Hits by kind: "
          + ", ".join(f"{k} {v}" for k, v in hits.items()) + f"; exports unread {unread}.\n")
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
    sent = sentinels(settings, arms)
    doc = build_report(settings, settings_path, settings_sha256, arms, audit, commit())
    doc["criteria"] = criteria(settings, arms, audit, guard_clean=sent.unread == 0)
    doc["criteria"]["M3"]["exports_unread"] = sent.unread
    doc["verdict"] = "pass" if all(c["passed"] for c in doc["criteria"].values()) else "fail"
    doc = rounded(doc)
    text_json = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    text_md = render(doc)
    hits = guard_hits([text_json, text_md], sent, settings["report_guard"]["ngram"])
    if any(hits.values()) or sent.unread:
        doc, text_md = withheld(hits, sent.unread)
        text_json = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    data_json, data_md = text_json.encode("utf-8"), text_md.encode("utf-8")
    (out / "report.json").write_bytes(data_json)
    (out / "report.md").write_bytes(data_md)
    emit(block("report.json", data_json))
    emit(block("report.md", data_md))
    return doc

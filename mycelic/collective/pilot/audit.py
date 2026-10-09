"""The pilot signal audit: what the detectors would have flagged in a company's history, against what it acted on.

    python -m mycelic.collective.pilot.audit run --pack P --records FILE --outcomes FILE --out DIR
        [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--lookback-weeks 26] [--post-weeks 26] [--tie-salt S]
    python -m mycelic.collective.pilot.audit demo --pack P --out DIR [--seed 1] [--weeks 52]

Both take ``--dry-run`` (the common contract: what would be read and written, and nothing touched).

**Inputs.** ``--pack`` is a built-in pack id or a pack directory (a company's copy, whose ``mapping.json`` names its
own export's fields: the first week of a pilot). ``--records`` is the export, one row per complaint, incident or
claim: JSON lines shaped as the mapping says, or CSV whose header names are the mapping's paths (``a.b`` nests,
``x[]`` is a list written ``v1;v2``). Every row needs the mapping's site field, so each plant, company or line of
business is a site. ``--outcomes`` is a CSV of the issues the company later acted on, one per row: ``outcome_id``,
``opened`` (``YYYY-MM-DD``), ``entity_type`` and ``entity_id`` (what the issue was about, in the pack's types) and
an optional ``predicate`` (the failure, in the pack's predicates; empty means any).

**What runs.** The pipeline of the lab and the public replay (``evaluate.baselines``): each site ingests its own rows
and extracts claims lexically, emits k-suppressed weekly cells, and HQ runs the detectors over them (X: codes and
text; S: codes only), beside model-free R over the record-level fields the pack lets leave. Site stores live in a
temporary directory that is deleted at the end. Nothing is sent anywhere.

**What it reports.** For each channel and each outcome whose look-back falls in the evaluated weeks: whether an alert
on its entity (and predicate, when given) was available in the ``lookback`` weeks before it was opened, and how many
days earlier; alerts in the ``post`` weeks after it are listed apart, never counted. Each channel's found count comes
with the replay's circular-shift null (``experiments.openfda_replay.chance_null``): the expected number found by an
alert series of the same shape at random times, and the share of shifts that do at least as well. Complaints react to
an issue once it is opened, so that null also rotates each outcome's own reactive alerts (those in its ``post`` weeks)
into its look-back, which inflates the expected count whenever complaints follow outcomes, with or without an earlier
signal. Each channel therefore also reports the same null with every outcome's own post-opening alerts left out of its
rotated timeline (``expected_found_excluding_own_post``, ``p_value_excluding_own_post``; the p-value counts shifts at
least as good as the found count, plus one, over the shifts plus one, since shift zero no longer is the observed
alignment). Every channel also lists its alert timeline (``alert_timeline``: available date, week and keys of each
alert), so a later reader can re-score without re-running. Then the **review
list**: every alerted pattern that matches no outcome, with the sites and record ids behind it, for the company's own
reviewers. It is what the detectors saw that no one acted on: a missed issue, a known one never written up, or noise.

``demo`` builds a synthetic history for a pack with a smoke plant (its generator and ``fixtures/plant_smoke.json``), writes it as the
mapping's CSV export with one outcome per planted pattern, opened two weeks after the pattern ends, and audits it. It
shows the audit running end to end in that pack's field; the plant and the detectors share an author, so its numbers
are not evidence that the audit finds real problems.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..detect.store import RUN_CHANNELS
from ..edge.weeks import iso_week, week_monday
from ..evaluate.baselines import (EvaluationError, closing_date, exact_result, hq_results, r_mf_cells,
                                  run_pipeline)
from ..experiments.common import DryRun, UsageError, fail, write_json_atomic
from ..experiments.openfda_replay import chance_null, replay_weeks
from ..packs.canonical import Canonicaliser
from ..packs.connector import ConnectorError, field_values, map_rows
from ..packs.loader import FrozenPack, PackError, is_builtin_ref, load_pack

CLI = "pilot.audit"
KIND = "pilot_audit"
SCHEMA_VERSION = 1
ENTERPRISE = "pilot"
CHANNELS = ("X", "S", "R_mf")
LABEL = "local audit: computed inside your environment from your own export; nothing is sent anywhere"
DEMO_LABEL = ("synthetic demo: generated records and planted patterns written by the same author as the detectors; "
              "it shows the audit running in this field, not that it finds real problems")
CHANNEL_NOTES = {
    "X": "X: HQ's detectors over the codes and text cells that leave the sites, k-suppressed",
    "S": "S: the same detectors over the codes cells only; a pattern written only in the narratives is invisible to it",
    "R_mf": "R, model-free: the same detectors over record-level counts of the fields the pack lets leave",
}
OUTCOME_COLUMNS = ("outcome_id", "opened", "entity_type", "entity_id")
LIST_SEP = ";"
DEMO_OPEN_AFTER_WEEKS = 2
MAX_REVIEW_REFS = 50


class AuditError(ValueError):
    pass


@dataclass(frozen=True)
class Outcome:
    outcome_id: str
    opened: str
    entity_type: str
    entity_id: str
    predicate: str | None

    @property
    def key(self) -> str:
        base = f"{self.entity_type}:{self.entity_id}"
        return base if self.predicate is None else f"{base}:{self.predicate}"


# --------------------------------------------------------------------------------------------------- the export

def set_path(row: dict[str, Any], path: str, value: Any) -> bool:
    """Write ``value`` at a mapping path (``a.b`` nests, a last segment ``x[]`` takes a list); False for a path
    through a list (``a[].b``), which a flat export cannot spell."""
    parts = path.split(".")
    if any(p.endswith("[]") for p in parts[:-1]):
        return False
    node = row
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            return False
    last = parts[-1]
    if last.endswith("[]"):
        node[last[:-2]] = list(value) if isinstance(value, (list, tuple)) else [value]
    else:
        node[last] = value
    return True


def read_csv_rows(path: str | Path) -> list[dict[str, Any]]:
    """CSV rows as mapping-shaped dicts: the header names are mapping paths; an empty cell is absent."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for raw in reader:
            row: dict[str, Any] = {}
            for header, cell in raw.items():
                if header is None or cell is None or cell == "":
                    continue
                value: Any = ([v.strip() for v in cell.split(LIST_SEP) if v.strip()] if header.endswith("[]")
                              else cell)
                set_path(row, header, value)
            rows.append(row)
    return rows


def read_rows(path: str | Path) -> list[Any]:
    p = Path(path)
    try:
        if p.suffix.lower() == ".csv":
            return read_csv_rows(p)
        rows = []
        for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    raise AuditError(f"{p}: line {n} is not JSON") from None
        return rows
    except OSError as exc:
        raise AuditError(f"cannot read {p} ({exc.__class__.__name__})") from None


def export_row(record: Mapping[str, Any], mapping: Mapping[str, Any]) -> dict[str, Any]:
    """What a company's export of ``record`` looks like under ``mapping`` (the inverse of the connector, for the
    fields a flat export can carry): the demo's input, and a template for a pilot's first export."""
    row: dict[str, Any] = {}
    set_path(row, mapping["record_ref"], record["record_ref"])
    set_path(row, mapping["site"], record["site"])
    spec = mapping["received_date"]
    day = record["received_date"]
    set_path(row, spec["path"], day.replace("-", "") if spec["format"] == "yyyymmdd" else day)
    if mapping["language"] is not None and record.get("language"):
        set_path(row, mapping["language"], record["language"])
    if mapping["codes"] and record["codes"]:
        cspec = mapping["codes"][0]
        if cspec["value_map"] is None:
            codes = list(record["codes"])
        else:
            inverse: dict[str, str] = {}
            for term in sorted(cspec["value_map"]):
                inverse.setdefault(cspec["value_map"][term], term)
            codes = [inverse[c] for c in record["codes"] if c in inverse]
        if codes:
            set_path(row, cspec["path"], codes if cspec["path"].endswith("[]") else codes[0])
    for t, paths in mapping["entities"].items():
        ids = list(record["entities"].get(t, []))
        if ids and paths:
            set_path(row, paths[0], ids if paths[0].endswith("[]") else ids[0])
    narrative = next((n["path"] for n in mapping["narrative"] if n["where"] is None), None)
    if narrative is not None:
        set_path(row, narrative, record["narrative"])
    for field, path in mapping["persons"].items():
        if record["persons"].get(field) is not None:
            set_path(row, path, record["persons"][field])
    for key in ("reporter", "origin_ref", "origin_site"):
        if mapping.get(key) is not None and record.get(key) is not None:
            set_path(row, mapping[key], record[key])
    return row


def write_csv(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    def flat(obj: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
        out: dict[str, str] = {}
        for k, v in obj.items():
            name = f"{prefix}{k}"
            if isinstance(v, dict):
                out.update(flat(v, f"{name}."))
            elif isinstance(v, list):
                out[f"{name}[]"] = LIST_SEP.join(str(x) for x in v)
            else:
                out[name] = str(v)
        return out
    flats = [flat(r) for r in rows]
    headers = sorted({h for f in flats for h in f})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(flats)


def read_outcomes(path: str | Path, pack: FrozenPack) -> list[Outcome]:
    canon = Canonicaliser(pack)
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in OUTCOME_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise AuditError(f"{path}: missing columns {', '.join(missing)}")
            rows = list(reader)
    except OSError as exc:
        raise AuditError(f"cannot read {path} ({exc.__class__.__name__})") from None
    out: list[Outcome] = []
    seen: set[str] = set()
    for n, row in enumerate(rows, start=2):
        oid = (row["outcome_id"] or "").strip()
        if not oid or oid in seen:
            raise AuditError(f"{path}: line {n}: outcome_id empty or repeated")
        seen.add(oid)
        try:
            opened = date.fromisoformat((row["opened"] or "").strip()).isoformat()
        except ValueError:
            raise AuditError(f"{path}: line {n}: opened must be YYYY-MM-DD") from None
        t = (row["entity_type"] or "").strip()
        if t not in pack.entity_types:
            raise AuditError(f"{path}: line {n}: entity_type {t!r} is not a type of pack {pack.id}")
        m = canon.resolve_exact(t, (row["entity_id"] or "").strip())
        if m is None:
            raise AuditError(f"{path}: line {n}: entity_id does not resolve as a {t} of pack {pack.id}")
        pred = (row.get("predicate") or "").strip() or None
        if pred is not None and pred not in pack.predicates:
            raise AuditError(f"{path}: line {n}: predicate {pred!r} is not a predicate of pack {pack.id}")
        out.append(Outcome(oid, opened, t, m.entity_id, pred))
    return out


# --------------------------------------------------------------------------------------------------- the audit

def _days(value: str) -> date:
    return date.fromisoformat(value)


def _master_data(pack: FrozenPack, records: Sequence[Mapping[str, Any]],
                 site_ids: Sequence[str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """The structured ids seen at each site stand in for its master data, as in the public replay."""
    canon = Canonicaliser(pack)
    master: dict[str, dict[str, set[str]]] = {sid: {} for sid in site_ids}
    for r in records:
        for t in sorted(r["entities"]):
            if pack.entity_types[t].id_format is None:
                continue
            for value in r["entities"][t]:
                m = canon.resolve_exact(t, value)
                if m is not None:
                    master[r["site"]].setdefault(t, set()).add(m.entity_id)
    return {sid: {t: tuple(sorted(master[sid][t])) for t in sorted(master[sid])} for sid in site_ids}


def _coverage(records: Sequence[Mapping[str, Any]], pack: FrozenPack) -> dict[str, Any]:
    canon = Canonicaliser(pack)
    n = len(records)

    def resolved(r: Mapping[str, Any]) -> bool:
        return any(canon.resolve_exact(t, v) is not None for t, values in r["entities"].items() for v in values)

    counts = {"codes": sum(bool(r["codes"]) for r in records),
              "narrative": sum(bool(r["narrative"]) for r in records),
              "resolved_entity": sum(resolved(r) for r in records)}
    return {k: {"count": v, "share": v / n if n else None} for k, v in counts.items()}


def _alerts(result: Mapping[str, Any], pipeline: Any, run_channel: str, evaluated_from: str) -> list[dict[str, Any]]:
    window = pipeline.pack.detectors["window_weeks"]
    pos = {w: i for i, w in enumerate(pipeline.weeks)}
    support: dict[str, list[tuple[str, str, str]]] = {}
    for sid in sorted(pipeline.sites):
        for row in pipeline.sites[sid].store.emission_inputs(None, pipeline.weeks[-1]):
            if row.channel in RUN_CHANNELS[run_channel]:
                key = f"{row.entity_type}:{row.entity_id}:{row.predicate}"
                support.setdefault(key, []).append((row.count_week, sid, row.record_ref))
    items = []
    for a in result["alerts"]:
        if a["week"] < evaluated_from:
            continue
        lo = pipeline.weeks[max(0, pos[a["week"]] - window + 1)]
        rows = sorted({(s, ref) for week, s, ref in support.get(a["key"], []) if lo <= week <= a["week"]})
        entity_type, entity_id, predicate = a["key"].split(":", 2)
        items.append({"week": a["week"], "available_date": closing_date(pipeline.pack, a["week"]),
                      "rank": a["rank"], "score": a["score"], "key": a["key"], "entity_type": entity_type,
                      "entity_id": entity_id, "predicate": predicate, "sites": sorted({s for s, _ in rows}),
                      "record_refs": [ref for _, ref in rows]})
    return items


def _match_keys(alert: Mapping[str, Any]) -> list[str]:
    base = f"{alert['entity_type']}:{alert['entity_id']}"
    return [base, f"{base}:{alert['predicate']}"]


def score_channel(outcomes: Sequence[Outcome], alerts: Sequence[Mapping[str, Any]], *, lookback: int, post: int,
                  available_first: date, evaluated_weeks: int) -> dict[str, Any]:
    """Found, lead days, post-outcome alerts and the circular-shift null for one channel's alerts."""
    keyed = [(a, _match_keys(a)) for a in alerts]
    explained: set[int] = set()
    rows = []
    for o in outcomes:
        opened = _days(o.opened)
        pre, after = [], []
        for i, (a, keys) in enumerate(keyed):
            if o.key not in keys:
                continue
            day = _days(a["available_date"])
            if opened - timedelta(days=7 * lookback) <= day < opened:
                pre.append(a)
                explained.add(i)
            elif opened <= day <= opened + timedelta(days=7 * post):
                after.append(a)
                explained.add(i)
        first = min(pre, key=lambda a: (a["available_date"], a["week"], a["rank"])) if pre else None
        rows.append({"outcome_id": o.outcome_id, "key": o.key, "opened": o.opened, "found": first is not None,
                     "lead_days": None if first is None else (opened - _days(first["available_date"])).days,
                     "first_alert_week": None if first is None else first["week"], "post_alerts": len(after)})
    found = sum(r["found"] for r in rows)
    leads = sorted(r["lead_days"] for r in rows if r["found"])
    chance = chance_null([{"event_date_initiated": o.opened, "product_code": o.key} for o in outcomes],
                         [{"available_date": a["available_date"], "product_codes": keys} for a, keys in keyed],
                         lookback, available_first, evaluated_weeks, found)
    own = null_excluding_own_post(outcomes, keyed, lookback=lookback, post=post, available_first=available_first,
                                  evaluated_weeks=evaluated_weeks, found=found)
    return {"summary": {"outcomes": len(rows), "found": found, "alerts": len(alerts),
                        "median_lead_days": leads[len(leads) // 2] if leads else None,
                        "expected_found": chance["expected_found"], "p_value": chance["p_value"],
                        "expected_found_excluding_own_post": own["expected_found"],
                        "p_value_excluding_own_post": own["p_value"],
                        "unexplained_alerts": len(alerts) - len(explained)},
            "by_outcome": rows, "unexplained": [i for i in range(len(alerts)) if i not in explained],
            "alert_timeline": [{"available_date": a["available_date"], "week": a["week"], "keys": sorted(keys)}
                               for a, keys in keyed]}


def null_excluding_own_post(outcomes: Sequence[Outcome], keyed: Sequence[tuple[Mapping[str, Any], Any]], *,
                            lookback: int, post: int, available_first: date, evaluated_weeks: int,
                            found: int) -> dict[str, Any]:
    """``chance_null``'s rotation, outcome by outcome, with that outcome's own post-opening alerts (on its key,
    available from its opening to ``post`` weeks after) left out of the timeline rotated against it. Shift zero is
    then no longer the observed alignment, so the p-value is (1 + shifts at least as good as ``found``) / (1 +
    shifts)."""
    if not outcomes or evaluated_weeks <= 0:
        return {"expected_found": None, "p_value": None}
    period = 7 * evaluated_weeks
    totals = [0] * evaluated_weeks
    for o in outcomes:
        opened = _days(o.opened)
        start, end = opened - timedelta(days=7 * lookback), opened + timedelta(days=7 * post)
        offsets = [(_days(a["available_date"]) - available_first).days for a, keys in keyed
                   if o.key in keys and not opened <= _days(a["available_date"]) <= end]
        for shift in range(evaluated_weeks):
            if any(start <= available_first + timedelta(days=(d + 7 * shift) % period) < opened for d in offsets):
                totals[shift] += 1
    return {"expected_found": sum(totals) / evaluated_weeks,
            "p_value": (1 + sum(1 for x in totals if x >= found)) / (1 + evaluated_weeks)}


def audit(pack: FrozenPack, rows: Sequence[Any], outcomes: Sequence[Outcome], *, date_from: str | None = None,
          date_to: str | None = None, lookback: int = 26, post: int = 26, tie_salt: str = "pilot",
          synthetic: bool = False) -> dict[str, Any]:
    if pack.mapping()["site"] is None:
        raise AuditError(f"pack {pack.id}'s mapping.json has no site field: a multi-site export needs one")
    try:
        mapped = map_rows(rows, pack, synthetic=synthetic)
    except ConnectorError as exc:
        raise AuditError(str(exc)) from None
    records = list(mapped.records)
    if not records:
        raise AuditError("no row of the export maps to a record (check the pack's mapping.json against the export)")
    days = sorted(r["received_date"][:10] for r in records)
    first, last = date_from or days[0], date_to or days[-1]
    records = [r for r in records if first <= r["received_date"][:10] <= last]
    weeks = replay_weeks(first, last)
    d = pack.detectors
    evaluated_index = d["window_weeks"] + d["min_history_weeks"] - 1
    if len(weeks) <= evaluated_index:
        raise AuditError(f"the export spans {len(weeks)} weeks; the detectors need more than {evaluated_index}")
    site_ids = sorted({r["site"] for r in records})
    master = _master_data(pack, records, site_ids)
    channels: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="mycelic-pilot-") as tmp:
        pipeline = run_pipeline(pack, records, site_ids=site_ids, master_data=master, weeks=weeks, workdir=tmp,
                                enterprise=ENTERPRISE)
        try:
            hq = hq_results(pipeline.store, as_of=pipeline.as_of, tie_salt=tie_salt)
            for name in ("X", "S"):
                channels[name] = {"alerts": _alerts(hq[name], pipeline, name, weeks[evaluated_index]), "reason": None}
            try:
                cells = r_mf_cells(pack, records, master_data=master, last_week=weeks[-1])
                result = exact_result(pack, pipeline.org, cells, as_of=pipeline.as_of, run_channel="S",
                                      tie_salt=tie_salt)
                channels["R_mf"] = {"alerts": _alerts(result, pipeline, "S", weeks[evaluated_index]), "reason": None}
            except EvaluationError as err:
                channels["R_mf"] = {"alerts": None, "reason": str(err)}
        finally:
            pipeline.close()
    available_first = _days(closing_date(pack, weeks[evaluated_index]))
    available_last = _days(closing_date(pack, weeks[-1]))
    evaluated_weeks = len(weeks) - evaluated_index
    in_scope = [o for o in outcomes if _days(o.opened) - timedelta(days=7 * lookback) <= available_last
                and available_first < _days(o.opened)]
    review: dict[str, dict[str, Any]] = {}
    scored: dict[str, Any] = {}
    for name in CHANNELS:
        alerts = channels[name]["alerts"]
        if alerts is None:
            scored[name] = {"summary": None, "by_outcome": [], "reason": channels[name]["reason"]}
            continue
        s = score_channel(in_scope, alerts, lookback=lookback, post=post, available_first=available_first,
                          evaluated_weeks=evaluated_weeks)
        scored[name] = {"summary": s["summary"], "by_outcome": s["by_outcome"], "alert_timeline": s["alert_timeline"],
                        "reason": None}
        for i in s["unexplained"]:
            a = alerts[i]
            entry = review.setdefault(a["key"], {"key": a["key"], "entity_type": a["entity_type"],
                                                 "entity_id": a["entity_id"], "predicate": a["predicate"],
                                                 "first_available": a["available_date"], "channels": [],
                                                 "alert_weeks": [], "sites": set(), "record_refs": set()})
            entry["first_available"] = min(entry["first_available"], a["available_date"])
            if name not in entry["channels"]:
                entry["channels"].append(name)
            if a["week"] not in entry["alert_weeks"]:
                entry["alert_weeks"].append(a["week"])
            entry["sites"].update(a["sites"])
            entry["record_refs"].update(a["record_refs"])
    review_list = []
    for entry in sorted(review.values(), key=lambda e: (e["first_available"], e["key"])):
        refs = sorted(entry["record_refs"])
        review_list.append({**entry, "alert_weeks": sorted(entry["alert_weeks"]), "sites": sorted(entry["sites"]),
                            "records": len(refs), "record_refs": refs[:MAX_REVIEW_REFS]})
    return {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "label": DEMO_LABEL if synthetic else LABEL,
        "pack": {"id": pack.id, "version": pack.version, **pack.hashes()},
        "export": {"rows": mapped.rows, "records": len(records), "rejected": dict(mapped.rejected),
                   "sites": site_ids, "date_from": first, "date_to": last,
                   "coverage": _coverage(records, pack)},
        "weeks": {"first": weeks[0], "last": weeks[-1], "evaluated_from": weeks[evaluated_index],
                  "evaluated_weeks": evaluated_weeks},
        "settings": {"lookback_weeks": lookback, "post_weeks": post, "tie_salt": tie_salt},
        "outcomes": {"given": len(outcomes), "in_scope": len(in_scope),
                     "out_of_scope": [o.outcome_id for o in outcomes if o not in in_scope]},
        "channels": scored, "channel_notes": CHANNEL_NOTES, "review": review_list}


# --------------------------------------------------------------------------------------------------- the report

def _num(value: Any, fmt: str = "{:.2f}") -> str:
    return "n/a" if value is None else (fmt.format(value) if isinstance(value, float) else str(value))


def render(doc: Mapping[str, Any]) -> str:
    e, w = doc["export"], doc["weeks"]
    cov = e["coverage"]
    lines = [f"# Signal audit: {doc['pack']['id']}", "", f"_{doc['label']}._", "",
             f"- Export: {e['records']} records from {len(e['sites'])} sites, {e['date_from']} to {e['date_to']}; "
             f"{e['rows'] - e['records']} rows not used ({', '.join(f'{k} {v}' for k, v in e['rejected'].items()) or 'none rejected'}).",
             f"- Records with an identifier the pack resolves: {_num(cov['resolved_entity']['share'], '{:.1%}')}; "
             f"with a code: {_num(cov['codes']['share'], '{:.1%}')}; with a narrative: "
             f"{_num(cov['narrative']['share'], '{:.1%}')}.",
             f"- Weeks evaluated: {w['evaluated_weeks']} ({w['evaluated_from']} to {w['last']}); the weeks before "
             "build each series' history.",
             f"- Issues you acted on: {doc['outcomes']['given']} given, {doc['outcomes']['in_scope']} with a look-back "
             f"in the evaluated weeks.", "",
             "## Would it have flagged them earlier?", "",
             "| Channel | Issues found before opening | Median days earlier | Expected by chance | p | "
             "Expected by chance, reactive alerts left out | p | Alerts | Alerts matching no issue |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name in CHANNELS:
        s = doc["channels"][name]["summary"]
        if s is None:
            lines.append(f"| {name} | not run: {doc['channels'][name]['reason']} | | | | | | | |")
            continue
        lines.append(f"| {name} | {s['found']} of {s['outcomes']} | {_num(s['median_lead_days'])} | "
                     f"{_num(s['expected_found'])} | {_num(s['p_value'])} | "
                     f"{_num(s.get('expected_found_excluding_own_post'))} | "
                     f"{_num(s.get('p_value_excluding_own_post'))} | {s['alerts']} | {s['unexplained_alerts']} |")
    lines += [""] + [f"- {doc['channel_notes'][n]}" for n in CHANNELS]
    lines += ["", "A found count means something only as far as it exceeds what chance gives: the same alerts at random "
              "times (the circular-shift null) would find the expected number. Complaints that react to an opened "
              "issue inflate that number, so the second pair of columns leaves each issue's own alerts after its "
              "opening out of the timeline rotated against it.", "", "## Per issue", "",
              "| Issue | About | Opened | " + " | ".join(CHANNELS) + " |", "|---|---|---|" + "---|" * len(CHANNELS)]
    by = {n: {r["outcome_id"]: r for r in doc["channels"][n]["by_outcome"]} for n in CHANNELS}
    for oid in [r["outcome_id"] for r in doc["channels"]["X"]["by_outcome"]] or []:
        first = by["X"][oid]
        cells = []
        for n in CHANNELS:
            r = by[n].get(oid)
            cells.append("n/a" if r is None else (f"{r['lead_days']} days earlier" if r["found"] else "not flagged"))
        lines.append(f"| {oid} | `{first['key']}` | {first['opened']} | " + " | ".join(cells) + " |")
    lines += ["", "## Review list: flagged, but no issue on record", "",
              "What the detectors saw that matches no issue you gave: a missed problem, a known one never written up, "
              "or noise. Each needs a person's look; the record ids are your own.", ""]
    if not doc["review"]:
        lines.append("None.")
    else:
        lines += ["| Pattern | First available | Channels | Sites | Records | First records |", "|---|---|---|---|---|---|"]
        for r in doc["review"]:
            lines.append(f"| `{r['key']}` | {r['first_available']} | {', '.join(r['channels'])} | "
                         f"{len(r['sites'])} | {r['records']} | {', '.join(r['record_refs'][:5])} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------------- the demo

def demo_inputs(pack: FrozenPack, out: Path, *, seed: int, weeks: int) -> tuple[Path, Path]:
    """A synthetic export (CSV) and one outcome per planted pattern, for a pack with a smoke plant."""
    from ..evaluate.plant import load_plant, plant
    from ..packs.generator import generate

    plant_path = Path(pack.directory) / "fixtures" / "plant_smoke.json"
    if not plant_path.is_file():
        raise AuditError(f"pack {pack.id} has no fixtures/plant_smoke.json to plant from")
    spec = load_plant(plant_path, pack)
    world = generate(pack, seed, len(pack.generator["sites"]), weeks)
    planted = plant(world, spec, pack)
    mapping = pack.mapping()
    out.mkdir(parents=True, exist_ok=True)
    records_path, outcomes_path = out / "export.csv", out / "outcomes.csv"
    write_csv([export_row(r, mapping) for r in (*world.records, *planted.records)], records_path)
    start = date.fromisoformat(world.params["start"])
    with open(outcomes_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["outcome_id", "opened", "entity_type", "entity_id", "predicate", "title"])
        for p in spec.patterns:
            week = iso_week(start + timedelta(days=7 * (p.end_week + DEMO_OPEN_AFTER_WEEKS)))
            writer.writerow([f"ISSUE-{p.id}", week_monday(week).isoformat(), p.entity_type, p.entity_id,
                             p.predicate, f"planted pattern {p.id} ({p.visibility})"])
    return records_path, outcomes_path


# --------------------------------------------------------------------------------------------------- CLI

def _load(ref: str) -> FrozenPack:
    try:
        return load_pack(ref if is_builtin_ref(ref) else Path(ref))
    except PackError as err:
        raise UsageError(f"pack: {err}") from None


def _write(doc: Mapping[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out / "audit.json", doc)
    (out / "audit.md").write_text(render(doc), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.pilot.audit", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="audit an export against the issues acted on")
    r.add_argument("--pack", required=True)
    r.add_argument("--records", required=True)
    r.add_argument("--outcomes", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--date-from")
    r.add_argument("--date-to")
    d = sub.add_parser("demo", help="audit a synthetic history of a built-in pack")
    d.add_argument("--pack", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--seed", type=int, default=1)
    d.add_argument("--weeks", type=int, default=52)
    for q in (r, d):
        q.add_argument("--lookback-weeks", type=int, default=26)
        q.add_argument("--post-weeks", type=int, default=26)
        q.add_argument("--tie-salt", default="pilot")
        q.add_argument("--dry-run", action="store_true", help="say what would be read and written; touch nothing")
    args = p.parse_args(argv)
    if args.dry_run:
        dry = DryRun(f"{CLI} {args.command}")
        if not is_builtin_ref(args.pack) and not Path(args.pack).is_dir():
            dry.need(f"pack directory {args.pack}")
        if args.command == "run":
            for path in (args.records, args.outcomes):
                if not Path(path).is_file():
                    dry.need(f"file {path}")
        else:
            for name in ("export.csv", "outcomes.csv"):
                dry.write(str(Path(args.out) / name))
        for name in ("audit.json", "audit.md"):
            dry.write(str(Path(args.out) / name))
        return dry.emit()
    try:
        pack = _load(args.pack)
        out = Path(args.out)
        if args.command == "demo":
            records_path, outcomes_path = demo_inputs(pack, out, seed=args.seed, weeks=args.weeks)
            rows, synthetic, first, last = read_rows(records_path), True, None, None
        else:
            records_path, outcomes_path = Path(args.records), Path(args.outcomes)
            rows, synthetic, first, last = read_rows(records_path), False, args.date_from, args.date_to
        outcomes = read_outcomes(outcomes_path, pack)
        doc = audit(pack, rows, outcomes, date_from=first, date_to=last, lookback=args.lookback_weeks,
                    post=args.post_weeks, tie_salt=args.tie_salt, synthetic=synthetic)
    except (AuditError, UsageError) as exc:
        return fail(str(exc))
    _write(doc, out)
    summary = ", ".join(f"{n} {doc['channels'][n]['summary']['found']}/{doc['channels'][n]['summary']['outcomes']}"
                        for n in CHANNELS if doc["channels"][n]["summary"] is not None)
    print(f"pilot audit: {doc['label']}; found before opening {summary}; review list {len(doc['review'])} "
          f"-> {out / 'audit.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

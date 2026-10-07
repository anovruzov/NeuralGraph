"""The openFDA public replay (STRATEGY section 9.3), in three commands that can only run in this order.

    python -m mycelic.collective.experiments.openfda_replay prereg --pack P --events-cache DIR --manufacturer NAME
        --manufacturer-field PATH --partition-field PATH --date-from YYYYMMDD --date-to YYYYMMDD --tie-salt S
        --saw-recall-outcomes yes|no --run-id ID [--recalling-firm NAME] [--min-partition-coverage 0.5]
        [--lookback-weeks 26] [--post-weeks 26] [--allow-dirty]
    python -m mycelic.collective.experiments.openfda_replay signals --prereg FILE --run-id ID [--allow-dirty]
    python -m mycelic.collective.experiments.openfda_replay score --prereg FILE --signals DIR --recalls-cache DIR
        --run-id ID [--allow-dirty]

Every subcommand takes ``--runs-dir`` (default ``runs``) and ``--dry-run`` (the common contract). Exit codes: 0 ok;
2 usage, pinned-value or order error; 130 interrupted. Every file says :data:`LABEL` and the caches' data label.

**No look-ahead.** ``prereg`` pins the pack's four hashes, the replay code's hash (:data:`REPLAY_CODE_FILES`), the
events cache's manifest sha256 and every setting, and stamps the analyst's own declaration whether recall outcomes
were seen before it. It has no recall argument. ``signals`` refuses any changed pin, then runs phase 1 on the events
alone and writes ``signals.json`` and then ``phase1.json`` (the sha256 of ``signals.json`` as written); it never
receives a recall path. ``score`` re-checks the pins, refuses a ``signals.json`` whose bytes differ from
``phase1.json`` or that was made under another prereg, and only then opens the recall cache.

**Phase 1** (one manufacturer, never across manufacturers): events in page order, each with its page's product code;
a repeated record ref (the pack's openFDA mapping) is counted and the first kept; the manufacturer field's stripped
values must equal one of the names exactly (case-sensitive, no fuzzy merge); a missing, malformed or out-of-range
received date is counted (missing and malformed through the connector's rejections); the partition field's first
value becomes a site (``p-`` plus a slug, or a sha-based id on an empty slug or a collision; ``unpartitioned`` when
absent; more than :data:`MAX_PARTITIONS` exits 2). Records are re-keyed to the pack's own record fields (the site
store checks records against the pack's default mapping); public data has no ERP, so the structured ids seen at a
site stand in for its master data. Then the real pipeline (``evaluate.baselines``) and the X, S and R (model-free)
channels; each alert carries its available date (its week's closing date) and the product codes of the records whose
claims (in the channel's cell channels) hold its key inside its window.

**Scoring.** A recall is found by a channel when an alert on its product code is available in ``[initiation -
7 * lookback_weeks, initiation)``; the earliest gives the lead in days. Alerts available from the initiation through
``post_weeks`` after it are listed as post-recall alerts, never found and never false alarms. False alarms are the
evaluated weeks' alerts that match no in-scope recall's window, over every product code of the manufacturer.
``measurement`` is true only when both caches hold public data.

**Chance.** Matching on a product code alone credits a channel for raising many alerts: a channel that alerts on a
code every few weeks "finds" a recall of that code at almost any date. So each channel's summary carries its alert
count and a circular-shift null (:func:`chance_null`): the channel's alert timeline is rotated by each whole number
of weeks ``s`` in ``0 .. evaluated_weeks - 1`` (an alert available ``t`` days after the first evaluated closing date
moves to ``(t + 7 s) mod (7 * evaluated_weeks)``), which keeps how many alerts each code has and how they cluster,
while the recalls stay at their real dates and are scored by the same look-back rule. ``expected_found`` is the mean
found count over the shifts, ``per_recall`` each recall's share of shifts in which it is found, ``p_value`` the share
of shifts (shift 0, the observed alignment, included) whose found count is at least the observed one, so it is never
below ``1 / evaluated_weeks``, and ``median_lead_days`` the median lead over every shift's found recalls. A channel's
``found`` means something only as far as it exceeds its own ``expected_found``. The scorer does not match on the
predicate or the cause: ``root_cause_description`` is free text with no mapping to the pack's predicates.
"""
from __future__ import annotations

import argparse
import bisect
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .. import schemacheck, stats
from ..connectors.openfda import Cache, CacheError, load_cache, record_coverage
from ..detect.detectors import TIE_SALT_RE
from ..detect.store import RUN_CHANNELS
from ..edge.weeks import iso_week, next_week
from ..evaluate.baselines import (REPLAY_ENTERPRISE, EvaluationError, Pipeline, closing_date, exact_result,
                                  hq_results, r_mf_cells, run_pipeline)
from ..evaluate.harness import (EVAL_CODE_FILES, EVAL_DIRTY_PATHS, arr_schema, check_clean, obj_schema,
                                pinned_differences, typed_schema, union_problems)
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from ..packs.canonical import Canonicaliser
from ..packs.connector import COMPACT_DATE_RE, field_values, map_rows, valid_date
from ..packs.loader import PATH_RE, FrozenPack, PackError, is_builtin_ref, load_pack
from .common import (ROOT, DryRun, UsageError, check_run_id, code_commit, code_dirty, code_files, code_hash, fail,
                     run_dir, utc_clock, write_json_atomic)

CLI = "experiments.openfda_replay"
KIND = "replay"
SCHEMA_VERSION = 1
LABEL = "public data, artificial partitioning, not a confidentiality demonstration"
REPLAY_CODE_FILES = (*EVAL_CODE_FILES, "mycelic/collective/connectors/openfda.py",
                     "mycelic/collective/experiments/openfda_replay.py")
REPLAY_DIRTY_PATHS = (*EVAL_DIRTY_PATHS, "mycelic/collective/connectors/openfda.py",
                      "mycelic/collective/experiments/openfda_replay.py")
OPENFDA_MAPPING = "mapping_openfda"
REPLAY_CHANNELS = ("X", "S", "R_mf")
# STRATEGY 9.3: the recall fields verified in FDA's own schema; their contents are unverified
RECALL_DATE, RECALL_CODE, RECALL_FIRM, RECALL_CAUSE = ("event_date_initiated", "product_code", "recalling_firm",
                                                       "root_cause_description")
UNPARTITIONED = "unpartitioned"
UNPARTITIONED_LABEL = "unpartitioned: events without a partition value, labelled as such"
SITE_PREFIX = "p-"
MAX_SLUG = 48
MAX_PARTITIONS = 999
MAX_MANUFACTURERS = 20
MAX_NAME = 200
MAX_LOOKBACK = 104
LOW_RESOLUTION_BELOW = 0.5
COVERAGE_FIELDS = ("date_received", "narrative", "product_problems", "mapped_codes", "model_number", "lot_number",
                   "resolved_entity", "partition_field", "manufacturer_field")
DENOMINATOR_NOTE = ("false alarms per week count alerts on every product code of the manufacturer, not only recalled "
                    "ones")
CHANCE_METHOD = ("circular shift: the channel's alert timeline rotated by each whole number of weeks within the "
                 "evaluated weeks, the recalls at their real dates; shift 0 is the observed alignment")
CHANCE_NOTE = ("found counts mean something only as far as they exceed expected_found: matching on the product code "
               "alone credits a channel for raising many alerts")
MASTER_DATA_NOTE = "public data has no ERP; structured ids seen at the site stand in for it"
_SLUG_RUN = re.compile(r"[a-z0-9]+", re.ASCII)


def replay_code_files() -> list[str]:
    return code_files(REPLAY_CODE_FILES)


def replay_code_hash() -> str:
    return code_hash([ROOT / p for p in replay_code_files()])


def _dirty_state() -> bool | str:
    return code_dirty(list(REPLAY_DIRTY_PATHS))


_STR = typed_schema("string")
_HEX = typed_schema("string", pattern="[0-9a-f]{64}")
_DATE8 = typed_schema("string", pattern="[0-9]{8}")
PREREG_SCHEMA = schemacheck.compile(obj_schema({
    "kind": typed_schema("string", const="replay_prereg"), "schema_version": typed_schema("integer", const=1),
    "run_id": _STR, "created_at": _STR, "label": typed_schema("string", const=LABEL),
    "pack": obj_schema({"ref": _STR, "id": _STR, "version": _STR, "config_hash": _HEX, "vocabulary_hash": _HEX,
                        "detector_hash": _HEX, "fixtures_hash": _HEX}),
    "code_hash": _HEX, "code_files": arr_schema(_STR), "code_commit": _STR,
    "code_dirty": typed_schema("boolean", nullable=True), "allow_dirty": typed_schema("boolean"),
    "events_cache": obj_schema({"path": _STR, "manifest_sha256": _HEX,
                                "data_label": typed_schema("string", enum=["public", "synthetic"]),
                                "query": obj_schema({"product_codes": arr_schema(_STR), "date_from": _DATE8,
                                                     "date_to": _DATE8, "limit": typed_schema("integer"),
                                                     "max_records": typed_schema("integer", nullable=True)})}),
    "settings": obj_schema({
        "manufacturers": arr_schema(typed_schema("string", minLength=1, maxLength=MAX_NAME), 1, MAX_MANUFACTURERS),
        "manufacturer_field": _STR, "recalling_firms": arr_schema(typed_schema("string", minLength=1,
                                                                               maxLength=MAX_NAME), 1),
        "partition_field": _STR, "min_partition_coverage": typed_schema("number"), "date_from": _DATE8,
        "date_to": _DATE8, "lookback_weeks": typed_schema("integer"), "post_weeks": typed_schema("integer"),
        "tie_salt": typed_schema("string", pattern=TIE_SALT_RE.pattern)}),
    "recall_outcomes_seen_before_prereg": typed_schema("boolean")}))


# --------------------------------------------------------------------------------------------------- settings

def _iso(compact: str) -> str | None:
    """YYYYMMDD as YYYY-MM-DD, None when it is not a calendar date."""
    if not isinstance(compact, str) or COMPACT_DATE_RE.fullmatch(compact) is None:
        return None
    value = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return value if valid_date(value) else None


def received_day(row: Any, mapping: Mapping[str, Any]) -> str | None:
    """The received date of an event through the mapping's received_date path and format, else None."""
    values = field_values(row, mapping["received_date"]["path"])
    if not values:
        return None
    if mapping["received_date"]["format"] == "yyyymmdd":
        return _iso(values[0])
    return values[0] if valid_date(values[0]) else None


def replay_weeks(date_from: str, date_to: str) -> list[str]:
    """The ISO weeks from date_from's week to date_to's week (both YYYY-MM-DD)."""
    weeks = [iso_week(date_from)]
    while weeks[-1] < iso_week(date_to):
        weeks.append(next_week(weeks[-1]))
    return weeks


def check_settings(pack: FrozenPack, settings: Mapping[str, Any], query: Mapping[str, Any]) -> None:
    """Every setting rule; the same at prereg time and when a later command re-reads the prereg."""
    if OPENFDA_MAPPING not in pack.mappings:
        raise UsageError(f"pack {pack.id} has no {OPENFDA_MAPPING}.json") from None
    names = settings["manufacturers"]
    if not 1 <= len(names) <= MAX_MANUFACTURERS or len(set(names)) != len(names):
        raise UsageError(f"--manufacturer: give 1 to {MAX_MANUFACTURERS} distinct names") from None
    for name in [*names, *settings["recalling_firms"]]:
        if not 1 <= len(name) <= MAX_NAME or not name.isprintable():
            raise UsageError(f"--manufacturer and --recalling-firm names must be 1 to {MAX_NAME} printable "
                             "characters") from None
    for flag, key in (("--manufacturer-field", "manufacturer_field"), ("--partition-field", "partition_field")):
        if PATH_RE.fullmatch(settings[key]) is None:
            raise UsageError(f"{flag} must be a field path such as a.b[].c") from None
    if not 0 < settings["min_partition_coverage"] <= 1:
        raise UsageError("--min-partition-coverage must be in (0, 1]") from None
    first, last = _iso(settings["date_from"]), _iso(settings["date_to"])
    if first is None or last is None or first > last:
        raise UsageError("--date-from and --date-to must be calendar dates YYYYMMDD, in order") from None
    if not (query["date_from"] <= settings["date_from"] and settings["date_to"] <= query["date_to"]):
        raise UsageError("--date-from and --date-to must lie inside the events cache's query range") from None
    need = pack.detectors["window_weeks"] + pack.detectors["min_history_weeks"] + 1
    if len(replay_weeks(first, last)) < need:
        raise UsageError(f"the date range must span at least {need} ISO weeks (window_weeks + min_history_weeks "
                         "+ 1)") from None
    if not 1 <= settings["lookback_weeks"] <= MAX_LOOKBACK or not 0 <= settings["post_weeks"] <= MAX_LOOKBACK:
        raise UsageError(f"--lookback-weeks must be in [1, {MAX_LOOKBACK}] and --post-weeks in "
                         f"[0, {MAX_LOOKBACK}]") from None
    if TIE_SALT_RE.fullmatch(settings["tie_salt"]) is None:
        raise UsageError("--tie-salt must be 1 to 128 printable ASCII characters") from None


def _load_pack(ref: str) -> FrozenPack:
    try:
        return load_pack(ref)
    except PackError as err:
        raise UsageError(f"pack: {err}") from None


def _cache(path: str, dataset: str) -> Cache:
    try:
        cache = load_cache(path)
    except CacheError as err:
        raise UsageError(f"{dataset} cache: {err}") from None
    if cache.manifest["dataset"] != dataset:
        raise UsageError(f"{path} is not an openFDA {dataset} cache") from None
    return cache


def read_prereg(path: str | Path) -> tuple[dict[str, Any], bytes]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read prereg {path} ({exc.__class__.__name__})") from None
    doc = None
    try:
        doc = strict_load(data)
    except StrictJsonError as err:
        raise UsageError(f"prereg {path} is not strict JSON ({err.reason})") from None
    problems = union_problems(PREREG_SCHEMA, doc, ("code_dirty",))
    if problems:
        raise UsageError(f"prereg {path} is not a replay prereg.json ({problems[0][0]} {problems[0][1]})") from None
    return doc, data


def _checked_prereg(args: argparse.Namespace, dry: DryRun | None) -> tuple[dict[str, Any], str, FrozenPack]:
    """The prereg, its sha256 and its pack, after the pins (every differing name listed) and the dirty rule."""
    prereg, data = read_prereg(args.prereg)
    pack = _load_pack(prereg["pack"]["ref"])
    differing = pinned_differences(prereg, pack, replay_code_hash())
    if differing:
        raise UsageError(f"pinned values differ from the prereg: {', '.join(differing)}") from None
    check_clean(args.allow_dirty, dry, _dirty_state())
    check_settings(pack, prereg["settings"], prereg["events_cache"]["query"])
    return prereg, sha256_hex(data), pack


# --------------------------------------------------------------------------------------------------- prereg

def cmd_prereg(args: argparse.Namespace) -> int:
    try:
        check_run_id(args.run_id)
        out_dir = run_dir(args.runs_dir, KIND, args.run_id)
        dry = DryRun(f"{CLI} prereg") if args.dry_run else None
        settings = {"manufacturers": list(args.manufacturer), "manufacturer_field": args.manufacturer_field,
                    "recalling_firms": list(args.recalling_firm or args.manufacturer),
                    "partition_field": args.partition_field, "min_partition_coverage": args.min_partition_coverage,
                    "date_from": args.date_from, "date_to": args.date_to, "lookback_weeks": args.lookback_weeks,
                    "post_weeks": args.post_weeks, "tie_salt": args.tie_salt}
        missing = []
        if not is_builtin_ref(args.pack) and not Path(args.pack).exists():
            missing.append(f"pack {args.pack}")
        if not Path(args.events_cache).is_dir():
            missing.append(f"events cache {args.events_cache}")
        if dry is not None and missing:
            for need in missing:
                dry.need(need)
            dry.write(str(out_dir / "prereg.json"))
            return dry.emit()
        pack = _load_pack(args.pack)
        cache = _cache(args.events_cache, "event")
        check_settings(pack, settings, cache.manifest["query"])
        dirty = _dirty_state()
        check_clean(args.allow_dirty, dry, dirty)
        if dry is not None:
            dry.write(str(out_dir / "prereg.json"))
            return dry.emit()
    except UsageError as exc:
        return fail(str(exc))
    query = cache.manifest["query"]
    doc = {
        "kind": "replay_prereg", "schema_version": SCHEMA_VERSION, "run_id": args.run_id, "created_at": utc_clock(),
        "label": LABEL,
        "pack": {"ref": args.pack if is_builtin_ref(args.pack) else str(Path(args.pack).resolve()), "id": pack.id,
                 "version": pack.version, **pack.hashes()},
        "code_hash": replay_code_hash(), "code_files": replay_code_files(), "code_commit": code_commit(),
        "code_dirty": dirty, "allow_dirty": bool(args.allow_dirty),
        "events_cache": {"path": str(Path(args.events_cache).resolve()), "manifest_sha256": cache.manifest_sha256,
                         "data_label": cache.manifest["data_label"],
                         "query": {"product_codes": list(query["product_codes"]), "date_from": query["date_from"],
                                   "date_to": query["date_to"], "limit": query["limit"],
                                   "max_records": query["max_records"]}},
        "settings": settings, "recall_outcomes_seen_before_prereg": args.saw_recall_outcomes == "yes",
    }
    out_dir.mkdir(parents=True)
    digest = write_json_atomic(out_dir / "prereg.json", doc)
    print(f"replay prereg: wrote {out_dir / 'prereg.json'} sha256 {digest} ({LABEL}; "
          f"data_label={doc['events_cache']['data_label']})")
    return 0


# --------------------------------------------------------------------------------------------------- phase 1

def slug_sites(values: Sequence[str]) -> dict[str, str]:
    """Partition value -> site id, assigned in sorted value order: ``p-`` plus the value's lower-cased ASCII
    alphanumeric runs joined by ``-`` (at most 48 characters), or ``p-`` plus 12 hex of its sha256 when the slug is
    empty or already taken."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for value in sorted(set(values)):
        slug = "-".join(_SLUG_RUN.findall(value.lower()))[:MAX_SLUG].strip("-")
        site = SITE_PREFIX + slug if slug else ""
        if not slug or site in taken:
            site = SITE_PREFIX + sha256_hex(value)[:12]
        taken.add(site)
        out[value] = site
    return out


def site_record(record: Mapping[str, Any], pack: FrozenPack) -> dict[str, Any]:
    """An openFDA-mapped record re-keyed to the pack's own record fields (entity types and person fields of its
    default mapping), which is what a site store accepts."""
    mapping = pack.mapping()
    extra = sorted(t for t in record["entities"] if t not in mapping["entities"])
    if extra:
        raise UsageError(f"the pack's {OPENFDA_MAPPING} names an entity type its default mapping lacks") from None
    return {**record, "entities": {t: list(record["entities"].get(t, [])) for t in sorted(mapping["entities"])},
            "persons": {f: record["persons"].get(f) for f in sorted(mapping["persons"])}}


def _share(count: int, total: int) -> dict[str, Any]:
    return {"count": count, "share": count / total if total else None}


def _coverage(rows: Sequence[Mapping[str, Any]], pack: FrozenPack, settings: Mapping[str, Any]) -> dict[str, Any]:
    mapping = pack.mapping(OPENFDA_MAPPING)
    canon = Canonicaliser(pack)
    id_types = sorted(t for t in mapping["entities"] if pack.entity_types[t].id_format is not None)
    counts = dict.fromkeys(COVERAGE_FIELDS, 0)
    for row in rows:
        counts["date_received"] += received_day(row, mapping) is not None
        counts["narrative"] += any(field_values(row, n["path"], n["where"]) for n in mapping["narrative"])
        problems = mapped = False
        for spec in mapping["codes"]:
            values = field_values(row, spec["path"]) or []
            problems = problems or bool(values)
            mapped = mapped or any(spec["value_map"] is None or v in spec["value_map"] for v in values)
        counts["product_problems"] += problems
        counts["mapped_codes"] += mapped
        present = record_coverage(row)
        counts["model_number"] += present["model_number"]
        counts["lot_number"] += present["lot_number"]
        counts["resolved_entity"] += any(canon.resolve_exact(t, v) is not None for t in id_types
                                         for path in mapping["entities"][t] for v in (field_values(row, path) or []))
        counts["partition_field"] += bool(field_values(row, settings["partition_field"]))
        counts["manufacturer_field"] += bool(field_values(row, settings["manufacturer_field"]))
    return {name: _share(counts[name], len(rows)) for name in COVERAGE_FIELDS}


def _alert_items(result: Mapping[str, Any], pipeline: Pipeline, run_channel: str, code_of: Mapping[str, str],
                 evaluated_from: str) -> list[dict[str, Any]]:
    window = pipeline.pack.detectors["window_weeks"]
    pos = {w: i for i, w in enumerate(pipeline.weeks)}
    claims: dict[str, list[tuple[str, str]]] = {}
    for sid in sorted(pipeline.sites):
        for row in pipeline.sites[sid].store.emission_inputs(None, pipeline.weeks[-1]):
            if row.channel in RUN_CHANNELS[run_channel]:
                claims.setdefault(f"{row.entity_type}:{row.entity_id}:{row.predicate}", []).append(
                    (row.count_week, row.record_ref))
    items = []
    for a in result["alerts"]:
        if a["week"] < evaluated_from:
            continue
        lo = pipeline.weeks[max(0, pos[a["week"]] - window + 1)]
        codes = sorted({code_of[ref] for week, ref in claims.get(a["key"], []) if lo <= week <= a["week"]})
        items.append({"week": a["week"], "available_date": closing_date(pipeline.pack, a["week"]), "rank": a["rank"],
                      "key": a["key"], "score": a["score"], "product_codes": codes})
    return items


def phase1(pack: FrozenPack, prereg: Mapping[str, Any], cache: Cache, workdir: Path) -> dict[str, Any]:
    s = prereg["settings"]
    mapping = pack.mapping(OPENFDA_MAPPING)
    first, last = _iso(s["date_from"]), _iso(s["date_to"])
    cache_records = duplicates = 0
    seen: set[str] = set()
    rows: list[tuple[str, Any]] = []
    for code, row in cache.records():
        cache_records += 1
        refs = field_values(row, mapping["record_ref"])
        if refs:
            if refs[0] in seen:
                duplicates += 1
                continue
            seen.add(refs[0])
        rows.append((code, row))
    names = frozenset(s["manufacturers"])
    maker = dict.fromkeys(("events_matched", "other_manufacturer", "manufacturer_missing", "manufacturer_bad_type"), 0)
    matched: list[tuple[str, Any]] = []
    for code, row in rows:
        values = field_values(row, s["manufacturer_field"])
        if values is None:
            maker["manufacturer_bad_type"] += 1
        elif not values:
            maker["manufacturer_missing"] += 1
        elif any(v in names for v in values):
            maker["events_matched"] += 1
            matched.append((code, row))
        else:
            maker["other_manufacturer"] += 1
    coverage = _coverage([row for _, row in matched], pack, s)
    in_range: list[tuple[str, Any, str | None]] = []
    out_of_range = 0
    for code, row in matched:
        day = received_day(row, mapping)       # a missing or malformed date is left to the connector's rejections
        if day is not None and not first <= day <= last:
            out_of_range += 1
            continue
        values = field_values(row, s["partition_field"])
        in_range.append((code, row, values[0] if values else None))
    sites_of = slug_sites([v for _, _, v in in_range if v is not None])
    groups: dict[str, list[tuple[str, Any]]] = {}
    for code, row, value in in_range:
        groups.setdefault(sites_of[value] if value is not None else UNPARTITIONED, []).append((code, row))
    if len(groups) > MAX_PARTITIONS:
        raise UsageError(f"the partition field gives more than {MAX_PARTITIONS} partitions") from None
    synthetic = cache.manifest["data_label"] != "public"
    rejected: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    code_of: dict[str, str] = {}
    for site in sorted(groups):
        mapped = map_rows([row for _, row in groups[site]], pack, mapping=OPENFDA_MAPPING, site=site,
                          synthetic=synthetic)
        for reason, n in mapped.rejected.items():
            rejected[reason] = rejected.get(reason, 0) + n
        refs = {}
        for code, row in groups[site]:
            values = field_values(row, mapping["record_ref"])
            if values:
                refs[values[0]] = code
        for record in mapped.records:
            code_of[record["record_ref"]] = refs[record["record_ref"]]
            records.append(site_record(record, pack))
    if not records:
        raise UsageError("no event of the manufacturer in the date range maps to a record") from None
    site_ids = sorted({r["site"] for r in records})
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
    master_data = {sid: {t: tuple(sorted(master[sid][t])) for t in sorted(master[sid])} for sid in site_ids}
    weeks = replay_weeks(first, last)
    d = pack.detectors
    evaluated_index = d["window_weeks"] + d["min_history_weeks"] - 1
    pipeline = run_pipeline(pack, records, site_ids=site_ids, master_data=master_data, weeks=weeks, workdir=workdir,
                            enterprise=REPLAY_ENTERPRISE)
    try:
        hq = hq_results(pipeline.store, as_of=pipeline.as_of, tie_salt=s["tie_salt"])
        channels: dict[str, dict[str, Any]] = {}
        for name in ("X", "S"):
            channels[name] = {"alerts": _alert_items(hq[name], pipeline, name, code_of, weeks[evaluated_index]),
                              "candidates": len(hq[name]["candidates"]), "status": hq[name]["status"],
                              "reason": None}
        reason = None
        try:
            cells = r_mf_cells(pack, records, master_data=master_data, last_week=weeks[-1])
        except EvaluationError as err:
            cells, reason = None, str(err)
        if cells is None:
            channels["R_mf"] = {"alerts": None, "candidates": None, "status": None, "reason": reason}
        else:
            result = exact_result(pack, pipeline.org, cells, as_of=pipeline.as_of, run_channel="S",
                                  tie_salt=s["tie_salt"])
            channels["R_mf"] = {"alerts": _alert_items(result, pipeline, "S", code_of, weeks[evaluated_index]),
                                "candidates": len(result["candidates"]), "status": result["status"],
                                "reason": None}
    finally:
        pipeline.close()
    partition_cov = coverage["partition_field"]
    low = partition_cov["share"] is not None and partition_cov["share"] < s["min_partition_coverage"]
    warnings = []
    if low:
        warnings.append(f"low_partition_coverage: fewer than {s['min_partition_coverage']} of the events carry the "
                        "partition field; the rest are in the unpartitioned site")
    if len(site_ids) < d["burst"]["min_sites"]:
        warnings.append("fewer_sites_than_min_sites: fewer sites than the detectors' min_sites, so no cross-site "
                        "candidate can form")
    resolved = coverage["resolved_entity"]["share"]
    if resolved is not None and resolved < LOW_RESOLUTION_BELOW:
        warnings.append("low_resolution: the pack's id formats resolve few of this manufacturer's ids; freeze a "
                        "vocabulary for them before the run")
    values_of = {site: value for value, site in sites_of.items()}
    return {
        "manufacturer": {"names": list(s["manufacturers"]), "field": s["manufacturer_field"], **maker},
        "events": {"cache_records": cache_records, "duplicates": duplicates, "out_of_range": out_of_range,
                   "rejected": dict(sorted(rejected.items())), "mapped": len(records)},
        "coverage": coverage,
        "partition": {"field": s["partition_field"], "coverage": partition_cov,
                      "min_coverage": s["min_partition_coverage"], "low_coverage": low,
                      "unpartitioned_events": len(groups.get(UNPARTITIONED, [])),
                      "unpartitioned_label": UNPARTITIONED_LABEL,
                      "sites": [{"site": site, "value": values_of.get(site), "events": len(groups[site])}
                                for site in sorted(groups)]},
        "master_data_note": MASTER_DATA_NOTE,
        "product_codes": sorted(set(code_of.values())),
        "weeks": {"first": weeks[0], "last": weeks[-1], "evaluated_from": weeks[evaluated_index],
                  "evaluated_weeks": len(weeks) - evaluated_index},
        "channels": channels, "warnings": warnings}


def cmd_signals(args: argparse.Namespace) -> int:
    dry = DryRun(f"{CLI} signals") if args.dry_run else None
    try:
        out_dir = run_dir(args.runs_dir, KIND, check_run_id(args.run_id))
        if dry is not None and not Path(args.prereg).is_file():
            dry.need(f"prereg file {args.prereg}")
        else:
            prereg, prereg_sha, pack = _checked_prereg(args, dry)
            events = prereg["events_cache"]
            if dry is not None and not Path(events["path"]).is_dir():
                dry.need(f"events cache {events['path']}")
            else:
                cache = _cache(events["path"], "event")
                if cache.manifest_sha256 != events["manifest_sha256"]:
                    raise UsageError("pinned values differ from the prereg: events manifest_sha256") from None
        if dry is not None:
            for name in ("signals.json", "phase1.json", "work/"):
                dry.write(str(out_dir / name))
            return dry.emit()
        out_dir.mkdir(parents=True)
        body = phase1(pack, prereg, cache, out_dir / "work")
    except KeyboardInterrupt:
        print("replay signals: interrupted; the run directory is partial", file=sys.stderr)
        return 130
    except (UsageError, EvaluationError) as exc:
        return fail(str(exc))
    doc = {"kind": "replay_signals", "schema_version": SCHEMA_VERSION, "run_id": args.run_id,
           "created_at": utc_clock(), "label": LABEL, "data_label": events["data_label"], "measurement": False,
           "prereg_sha256": prereg_sha, "events_manifest_sha256": events["manifest_sha256"],
           "pinned": {**pack.hashes(), "code_hash": prereg["code_hash"]},
           "code_commit": code_commit(), "code_dirty": _dirty_state(), "allow_dirty": bool(args.allow_dirty), **body}
    signals_sha = write_json_atomic(out_dir / "signals.json", doc)
    write_json_atomic(out_dir / "phase1.json", {"signals_sha256": signals_sha, "signals_file": "signals.json",
                                                "written_at": utc_clock()})
    counts = " ".join(f"{name}={len(c['alerts']) if c['alerts'] is not None else 'null'}"
                      for name, c in body["channels"].items())
    print(f"replay signals: {LABEL}; data_label={events['data_label']}; events matched "
          f"{body['manufacturer']['events_matched']}, mapped {body['events']['mapped']}, sites "
          f"{len(body['partition']['sites'])}; alerts {counts} -> {out_dir / 'signals.json'}")
    return 0


# --------------------------------------------------------------------------------------------------- score

def _recall_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if COMPACT_DATE_RE.fullmatch(value) is not None:
        return _iso(value)
    return value if valid_date(value) else None


def _days(value: str) -> date:
    return date.fromisoformat(value)


def read_signals(directory: str | Path, prereg_sha: str) -> tuple[dict[str, Any], str]:
    """signals.json and its sha256, after its bytes are checked against phase1.json and its prereg against this
    one."""
    directory = Path(directory)
    try:
        phase = strict_load((directory / "phase1.json").read_bytes())
        data = (directory / "signals.json").read_bytes()
    except (OSError, StrictJsonError) as exc:
        raise UsageError(f"cannot read the signals run in {directory} ({exc.__class__.__name__})") from None
    if not isinstance(phase, dict) or sha256_hex(data) != phase.get("signals_sha256"):
        raise UsageError("signals.json differs from the sha256 phase1.json recorded; refusing to open the recalls") \
            from None
    doc = strict_load(data)
    if doc.get("prereg_sha256") != prereg_sha:
        raise UsageError("the signals run was made under another prereg") from None
    return doc, phase["signals_sha256"]


def chance_null(in_scope: Sequence[Mapping[str, Any]], alerts: Sequence[Mapping[str, Any]], lookback: int,
                available_first: date, evaluated_weeks: int, found: int) -> dict[str, Any]:
    """The circular-shift null of one channel (see the module docstring): the channel's alert timeline rotated by
    each whole number of weeks in turn, within the evaluated weeks, against the recalls at their real dates."""
    period = 7 * evaluated_weeks
    hits = [0] * len(in_scope)
    totals, leads = [], []
    for shift in range(evaluated_weeks):
        moved: dict[str, list[date]] = {}
        for a in alerts:
            offset = ((_days(a["available_date"]) - available_first).days + 7 * shift) % period
            for code in a["product_codes"]:
                moved.setdefault(code, []).append(available_first + timedelta(days=offset))
        for dates in moved.values():
            dates.sort()
        total = 0
        for j, r in enumerate(in_scope):
            init = _days(r["event_date_initiated"])
            dates = moved.get(r["product_code"], [])
            i = bisect.bisect_left(dates, init - timedelta(days=7 * lookback))
            if i < len(dates) and dates[i] < init:
                hits[j] += 1
                total += 1
                leads.append((init - dates[i]).days)
        totals.append(total)
    expected = math.fsum(totals) / evaluated_weeks
    return {"method": CHANCE_METHOD, "shifts": evaluated_weeks, "expected_found": expected,
            "recall_rate": expected / len(in_scope) if in_scope else None,
            "per_recall": [h / evaluated_weeks for h in hits],
            "p_value": sum(1 for t in totals if t >= found) / evaluated_weeks if in_scope else None,
            "median_lead_days": stats.percentile(leads, 50), "note": CHANCE_NOTE}


def _channel_score(items: Sequence[Mapping[str, Any]], alerts: Sequence[Mapping[str, Any]],
                   lookback: int, post: int, evaluated_weeks: int, available_first: date) -> dict[str, Any]:
    in_scope = [r for r in items if r["evaluable"]]
    by_recall = []
    matched: set[int] = set()
    post_alerts: set[int] = set()
    for r in in_scope:
        init = _days(r["event_date_initiated"])
        pre, after = [], 0
        for i, a in enumerate(alerts):
            if r["product_code"] not in a["product_codes"]:
                continue
            available = _days(a["available_date"])
            if init - timedelta(days=7 * lookback) <= available < init:
                pre.append(a)
                matched.add(i)
            elif init <= available <= init + timedelta(days=7 * post):
                after += 1
                matched.add(i)
                post_alerts.add(i)
        first = min(pre, key=lambda a: (a["available_date"], a["week"], a["rank"])) if pre else None
        by_recall.append({"found_pre": bool(pre), "post_recall_alerts": after,
                          "first_signal": None if first is None else {
                              "week": first["week"], "available_date": first["available_date"],
                              "lead_days": (init - _days(first["available_date"])).days}})
    found = [b for b in by_recall if b["found_pre"]]
    false = len(alerts) - len(matched)
    chance = chance_null(in_scope, alerts, lookback, available_first, evaluated_weeks, len(found))
    summary = {"in_scope": len(in_scope), "found": len(found),
               "recall_rate": len(found) / len(in_scope) if in_scope else None,
               "median_lead_days": stats.percentile([b["first_signal"]["lead_days"] for b in found], 50),
               "alerts": len(alerts), "alerts_per_week": len(alerts) / evaluated_weeks,
               "found_minus_expected": len(found) - chance["expected_found"], "chance": chance,
               "post_recall_alerts": len(post_alerts), "false_alarms": false,
               "false_alarms_per_week": false / evaluated_weeks, "denominator_note": DENOMINATOR_NOTE,
               "reason": None}
    return {"summary": summary, "by_recall": by_recall}


def score(pack: FrozenPack, prereg: Mapping[str, Any], signals: Mapping[str, Any], cache: Cache) -> dict[str, Any]:
    s = prereg["settings"]
    first, last = _iso(s["date_from"]), _iso(s["date_to"])
    firms = frozenset(s["recalling_firms"])
    codes = frozenset(signals["product_codes"])
    weeks = signals["weeks"]
    counts = dict.fromkeys(("cache_records", "duplicates", "bad_record", "other_firm", "bad_date", "out_of_range",
                            "not_evaluable"), 0)
    seen: set[str] = set()
    unmatched, items = [], []
    for _, record in cache.records():
        counts["cache_records"] += 1
        data = None
        try:
            data = canonical_bytes(record)
        except StrictJsonError:
            data = None
        if data is None or not isinstance(record, dict):
            counts["bad_record"] += 1
            continue
        digest = sha256_hex(data)
        if digest in seen:
            counts["duplicates"] += 1
            continue
        seen.add(digest)
        if record.get(RECALL_FIRM) not in firms:
            counts["other_firm"] += 1
            continue
        init = _recall_date(record.get(RECALL_DATE))
        if init is None:
            counts["bad_date"] += 1
            continue
        if not first <= init <= last:
            counts["out_of_range"] += 1
            continue
        recall_id = digest[:16]
        code = record.get(RECALL_CODE)
        if not isinstance(code, str) or code not in codes:
            unmatched.append({"recall_id": recall_id, "product_code": code if isinstance(code, str) else None,
                              "event_date_initiated": init})
            continue
        items.append({"recall_id": recall_id, "product_code": code, "event_date_initiated": init,
                      "recalling_firm": record[RECALL_FIRM],
                      "root_cause_description": record.get(RECALL_CAUSE) if isinstance(record.get(RECALL_CAUSE),
                                                                                       str) else None,
                      "evaluable": False, "by_channel": None})
    available_first = _days(closing_date(pack, weeks["evaluated_from"]))
    available_last = _days(closing_date(pack, weeks["last"]))
    for item in items:
        init = _days(item["event_date_initiated"])
        lo = init - timedelta(days=7 * s["lookback_weeks"])
        item["evaluable"] = lo <= available_last and available_first < init
        counts["not_evaluable"] += int(not item["evaluable"])
    channels: dict[str, Any] = {}
    per_item: dict[str, list[Any]] = {}
    for name in REPLAY_CHANNELS:
        channel = signals["channels"][name]
        if channel["alerts"] is None:
            channels[name] = {"summary": None, "reason": channel["reason"]}
            continue
        result = _channel_score(items, channel["alerts"], s["lookback_weeks"], s["post_weeks"],
                                weeks["evaluated_weeks"], available_first)
        channels[name] = {"summary": result["summary"], "reason": None}
        per_item[name] = result["by_recall"]
    evaluable = [item for item in items if item["evaluable"]]
    for j, item in enumerate(evaluable):
        item["by_channel"] = {name: per_item[name][j] if name in per_item else None for name in REPLAY_CHANNELS}
    return {"recalls": {**counts, "unmatched_product_code": unmatched, "items": items}, "channels": channels}


def cmd_score(args: argparse.Namespace) -> int:
    dry = DryRun(f"{CLI} score") if args.dry_run else None
    try:
        out_dir = run_dir(args.runs_dir, KIND, check_run_id(args.run_id))
        if dry is not None and not Path(args.prereg).is_file():
            dry.need(f"prereg file {args.prereg}")
        else:
            prereg, prereg_sha, pack = _checked_prereg(args, dry)
            if dry is not None and not (Path(args.signals) / "phase1.json").is_file():
                dry.need(f"signals run {args.signals}")
            else:
                signals, signals_sha = read_signals(args.signals, prereg_sha)
        if dry is not None:
            if not Path(args.recalls_cache).is_dir():
                dry.need(f"recalls cache {args.recalls_cache}")
            dry.write(str(out_dir / "replay.json"))
            return dry.emit()
        cache = _cache(args.recalls_cache, "recall")
    except UsageError as exc:
        return fail(str(exc))
    body = score(pack, prereg, signals, cache)
    events_label, recalls_label = prereg["events_cache"]["data_label"], cache.manifest["data_label"]
    both_public = events_label == "public" and recalls_label == "public"
    doc = {"kind": "openfda_replay", "schema_version": SCHEMA_VERSION, "run_id": args.run_id,
           "created_at": utc_clock(), "label": LABEL, "data_label": "public" if both_public else "synthetic",
           "measurement": both_public,
           "recall_outcomes_seen_before_prereg": prereg["recall_outcomes_seen_before_prereg"],
           "prereg_sha256": prereg_sha, "signals_sha256": signals_sha,
           "recalls_manifest_sha256": cache.manifest_sha256, "pinned": {**pack.hashes(),
                                                                        "code_hash": prereg["code_hash"]},
           "code_commit": code_commit(), "code_dirty": _dirty_state(), "allow_dirty": bool(args.allow_dirty),
           **body, "coverage": signals["coverage"], "warnings": list(signals["warnings"])}
    out_dir.mkdir(parents=True)
    write_json_atomic(out_dir / "replay.json", doc)
    found = " ".join(f"{name}={c['summary']['found']}/{c['summary']['in_scope']} (chance "
                     f"{c['summary']['chance']['expected_found']:.1f}, {c['summary']['alerts']} alerts)"
                     if c["summary"] is not None else f"{name}=null" for name, c in body["channels"].items())
    print(f"replay score: {LABEL}; data_label={doc['data_label']}; measurement={str(both_public).lower()}; "
          f"found before initiation {found} -> {out_dir / 'replay.json'}")
    return 0


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.openfda_replay",
                                description=f"openFDA public replay ({LABEL}): prereg, then signals, then score.")
    sub = p.add_subparsers(dest="command", required=True)

    def common(s: argparse.ArgumentParser) -> None:
        s.add_argument("--runs-dir", default="runs")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--allow-dirty", action="store_true")

    s = sub.add_parser("prereg", help="freeze and hash the settings before any recall outcome is opened")
    s.add_argument("--pack", required=True, help="a built-in pack id or a pack directory with mapping_openfda.json")
    s.add_argument("--events-cache", required=True, help="an openFDA device event cache (connectors.openfda fetch)")
    s.add_argument("--manufacturer", required=True, action="append", help="exact name; repeat for spellings")
    s.add_argument("--manufacturer-field", required=True, help="field path of the manufacturer name in an event")
    s.add_argument("--recalling-firm", action="append", default=None, help="exact name; default: the manufacturers")
    s.add_argument("--partition-field", required=True, help="field path whose value becomes the (artificial) site")
    s.add_argument("--min-partition-coverage", type=float, default=0.5)
    s.add_argument("--date-from", required=True, help="YYYYMMDD")
    s.add_argument("--date-to", required=True, help="YYYYMMDD")
    s.add_argument("--lookback-weeks", type=int, default=26)
    s.add_argument("--post-weeks", type=int, default=26)
    s.add_argument("--tie-salt", required=True)
    s.add_argument("--saw-recall-outcomes", required=True, choices=("yes", "no"),
                   help="self-declared: did you look at this manufacturer's recalls before this prereg?")
    s.add_argument("--run-id", required=True)
    common(s)
    s = sub.add_parser("signals", help="phase 1: alerts from the events alone, frozen and hashed")
    s.add_argument("--prereg", required=True)
    s.add_argument("--run-id", required=True)
    common(s)
    s = sub.add_parser("score", help="phase 2: open the recall cache and score the frozen signals")
    s.add_argument("--prereg", required=True)
    s.add_argument("--signals", required=True, help="the signals run directory")
    s.add_argument("--recalls-cache", required=True, help="an openFDA device recall cache")
    s.add_argument("--run-id", required=True)
    common(s)
    return p


COMMANDS = {"prereg": cmd_prereg, "signals": cmd_signals, "score": cmd_score}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

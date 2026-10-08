"""Evaluation only: the real G3 to G4 pipeline over a world, and the channels every result is reported against.

This module may read the world and the site stores, which no deployable component may; nothing here is a deployable
channel except X and S, which are exactly what HQ computes. Every channel returns a G4 result JSON or normalised alert
events ``{week, rank, key, score, site}``; a G4 result also gives its candidate weeks (:func:`detector_candidates`).

* **X and S** (:func:`hq_results`): ``detect`` over HQ's collective store alone, as deployed.
* **R (model-free)** (:func:`r_mf_cells`): the same detector code over record-level, unsuppressed counts built only
  from the pack's ``central_allowed_fields``. A record is read only through ``record[field]`` for an allowed
  top-level field and ``record["entities"][t]`` for an allowed ``entities.t``; it is never iterated, and narrative,
  persons, reporter and origin are never read (both built-in packs allow neither). Without origin markers R-mf cannot
  drop forwarded copies and counts each record as its own root and reporter, so ``n_roots = n_reporters = n``. It
  applies the cells' egress-type and master-data rules, so it differs from S only by record level, no suppression and
  no forwarded removal. It is not STRATEGY section 6.1's R, which may include a frontier model reading the allowed
  fields; E2 approximates that.
* **U** (:func:`u_cells`): every extracted claim of every non-forwarded record, both channels, record level,
  unsuppressed, exact roots and reporters; no master-data rule (the egress-type rule stays, and both packs declare
  every type egress). A reference, not a deployable system.
* **k=1 ablation** (:func:`k1_cells`): the sites' own cells (master-data rule included) without suppression,
  bypassing the Boundary; only with ``ablation_k1=True``.
* **single_site** (:func:`single_site_alerts`): each site alone, G4's D2 site test on its exact weekly counts, with
  G4's cooldown per (site, key) and one alert budget shared by all sites.
* **rules** (:func:`rule_alerts`): the pack's rules over the X cells, one event per episode start, unranked.

The exact channels (U, R-mf, k=1) can never raise G4's ``few_reporters`` flag, which needs a suppressed reporter
count. The pipeline clock is constant and every iteration is sorted, so every result here is byte-stable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .. import stats
from ..detect.detectors import DetectorConfig, detect, run_detection
from ..detect.org import OrgConfig, parse_org
from ..detect.rules import series_key
from ..detect.store import RUN_CHANNELS, BundleRow, CellRow, CollectiveStore
from ..edge.extract import codes_channel, pair
from ..edge.records import InputRow
from ..edge.site import EdgeSite, build_cells
from ..edge.weeks import closed_through, iso_week, local_date, week_monday, week_sunday
from ..jsonio import sha256_hex
from ..packs.canonical import Canonicaliser

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack

CHANNELS = ("X", "S", "R_mf", "U", "rules", "single_site")
ABLATION_CHANNELS = ("X_k1", "S_k1")
CHANNEL_LABELS = {
    "X": "X: codes and text-only cells that left the sites (k-suppressed); detectors D2 to D7",
    "S": "S: codes cells only (no model, no narrative); the same detectors",
    "R_mf": "R (model-free): the same detectors over record-level, unsuppressed counts built only from the pack's "
            "central_allowed_fields (structured codes and structured ids, never narrative). Not STRATEGY section "
            "6.1's R (the best central system, including a frontier model, reading the allowed fields); E2 (G6) "
            "approximates that with its central_allowed condition.",
    "U": "U (reference): every extracted claim at record level, unsuppressed, with exact roots and reporters; not a "
         "deployable system",
    "rules": "rules: the pack's hand-written rules over the X cells (episode starts; unranked; no budget)",
    "single_site": "single_site: each site alone, a per-site Poisson exceedance over its own records (exact counts), "
                   "sharing the same total weekly alert budget",
}
ABLATION_LABEL = ("k=1 ablation: cells counted without suppression at the sites, bypassing the Boundary; detector "
                  "parameters unchanged; internal only")
EVAL_ENTERPRISE = "eval"
REPLAY_ENTERPRISE = "replay"
EXACT_BUNDLE = "exact:{channel}:{site}"


class EvaluationError(ValueError):
    pass


def org_for_sites(site_ids: Sequence[str], enterprise: str) -> OrgConfig:
    """Site i (in the given order) sits at ``<enterprise>/region-<i // 2>/site-<i:03d>``: hierarchy segments forbid
    '.', so a site id is never a segment. No metric reads the decision unit."""
    return parse_org({"schema_version": 1, "enterprise": enterprise, "sites": [
        {"site_id": s, "unit_path": f"{enterprise}/region-{i // 2}/site-{i:03d}", "country": "ZZ", "display_name": s}
        for i, s in enumerate(site_ids)]})


def world_weeks(start: str, n: int) -> list[str]:
    first = date.fromisoformat(start)
    return [iso_week(first + timedelta(days=7 * i)) for i in range(n)]


def closing_date(pack: "FrozenPack", week: str) -> str:
    """The first date at which ``week`` is closed for the pack."""
    return (week_sunday(week) + timedelta(days=pack.egress.close_lag_days)).isoformat()


def previous_week(week: str) -> str:
    return iso_week(week_monday(week) - timedelta(days=7))


# --------------------------------------------------------------------------------------------------- the pipeline

@dataclass
class Pipeline:
    pack: "FrozenPack"
    org: OrgConfig
    weeks: tuple[str, ...]
    as_of: str
    sites: dict[str, EdgeSite]
    store: CollectiveStore

    def close(self) -> None:
        self.store.close()
        for site in self.sites.values():
            site.close()


def run_pipeline(pack: "FrozenPack", records: Sequence[Mapping[str, Any]], *, site_ids: Sequence[str],
                 master_data: Mapping[str, Mapping[str, Sequence[str]]], weeks: Sequence[str], workdir: str | Path,
                 enterprise: str = EVAL_ENTERPRISE) -> Pipeline:
    """Each site ingests its records (in list order) and extracts them lexically; every site then emits its cells at
    each week's closing date through its Boundary; HQ ingests the receive log. Any rejection or duplicate raises."""
    workdir = Path(workdir)
    weeks = tuple(weeks)
    as_of = closing_date(pack, weeks[-1])
    stamp = (date.fromisoformat(as_of) + timedelta(days=1)).isoformat() + "T00:00:00Z"

    def clock() -> str:
        return stamp

    org = org_for_sites(site_ids, enterprise)
    sites: dict[str, EdgeSite] = {}
    store = None
    try:
        for sid in site_ids:
            site = sites[sid] = EdgeSite(pack, sid, workdir / "edge", runtime=None, clock=clock,
                                         master_data=master_data[sid], hq_dir=workdir / "hq")
            got = site.ingest([r for r in records if r["site"] == sid])
            if got.duplicates or sum(got.rejected.values()):
                raise EvaluationError(f"site {sid} rejected or duplicated records") from None
            site.extract("lexical")
        for week in weeks:
            for sid in site_ids:
                sites[sid].emit_cells(closing_date(pack, week))
        store = CollectiveStore(workdir / "hq" / "collective.sqlite3", pack, org, clock=clock)
        report = store.ingest_log(workdir / "hq" / "receive.jsonl")
        if report.duplicates or sum(report.rejected.values()):
            raise EvaluationError("HQ rejected or duplicated a bundle") from None
    except BaseException:
        if store is not None:
            store.close()
        for site in sites.values():
            site.close()
        raise
    return Pipeline(pack=pack, org=org, weeks=weeks, as_of=as_of, sites=sites, store=store)


# --------------------------------------------------------------------------------------------------- channels

def hq_results(store: CollectiveStore, *, as_of: str, tie_salt: str) -> dict[str, dict[str, Any]]:
    """X and S exactly as HQ computes them, from the collective store alone."""
    return {channel: detect(store, as_of=as_of, run_channel=channel, tie_salt=tie_salt) for channel in ("X", "S")}


def exact_result(pack: "FrozenPack", org: OrgConfig, cells: Mapping[str, Sequence[Mapping[str, Any]]], *, as_of: str,
                 run_channel: str, tie_salt: str) -> dict[str, Any]:
    """G4's ``run_detection`` over exact cells (``site -> cell dicts``): one synthetic bundle per org site covering
    every week, so every site counts as reported, as in the weekly pipeline."""
    through = closed_through(as_of, pack.egress.close_lag_days)
    bundles, rows = [], []
    for site in sorted(org.sites):
        sha = sha256_hex(EXACT_BUNDLE.format(channel=run_channel, site=site))
        bundles.append(BundleRow(sha256=sha, site=site, as_of=as_of, after=None, closed_through=through))
        for c in cells.get(site, ()):
            if c["channel"] in RUN_CHANNELS[run_channel]:
                rows.append(CellRow(site=site, entity_type=c["entity_type"], entity_id=c["entity_id"],
                                    predicate=c["predicate"], iso_week=c["iso_week"], channel=c["channel"],
                                    n=c["n"], n_roots=c["n_roots"], n_reporters=c["n_reporters"],
                                    res_conf_min=c.get("res_conf_min"), bundle=sha, as_of=as_of))
    return run_detection(pack, org, bundles=bundles, cells=rows, as_of=as_of, run_channel=run_channel,
                         tie_salt=tie_salt)


def _site_inputs(pipeline: Pipeline, sid: str) -> list[InputRow]:
    return pipeline.sites[sid].store.emission_inputs(None, pipeline.weeks[-1])


def u_cells(pipeline: Pipeline) -> dict[str, list[dict[str, Any]]]:
    return {sid: build_cells(_site_inputs(pipeline, sid), pipeline.pack, master={}, require_master_data=False,
                             k=1)[0] for sid in sorted(pipeline.sites)}


def k1_cells(pipeline: Pipeline, *, ablation_k1: bool) -> dict[str, list[dict[str, Any]]]:
    if ablation_k1 is not True:
        raise EvaluationError("the k=1 ablation needs ablation_k1=True") from None
    return {sid: build_cells(_site_inputs(pipeline, sid), pipeline.pack, master=pipeline.sites[sid].master, k=1)[0]
            for sid in sorted(pipeline.sites)}


def _project(record: Mapping[str, Any], pack: "FrozenPack", allowed: frozenset[str]) -> dict[str, Any]:
    """A record with exactly the record keys, holding only allowed fields and neutral values elsewhere. ``record`` is
    read by subscript only, for allowed fields: never iterated, never ``.get``."""
    mapping = pack.mapping()
    entities = {t: (list(record["entities"][t]) if f"entities.{t}" in allowed else [])
                for t in sorted(mapping["entities"])}
    return {"record_ref": "rmf", "site": record["site"], "received_date": record["received_date"],
            "language": None, "codes": list(record["codes"]) if "codes" in allowed else [], "entities": entities,
            "narrative": "", "persons": {f: None for f in sorted(mapping["persons"])}, "reporter": None,
            "origin_ref": None, "origin_site": None, "synthetic": True}


def r_mf_cells(pack: "FrozenPack", records: Sequence[Mapping[str, Any]], *,
               master_data: Mapping[str, Mapping[str, Sequence[str]]],
               last_week: str) -> dict[str, list[dict[str, Any]]]:
    allowed = frozenset(pack.egress.central_allowed_fields)
    if "site" not in allowed or "received_date" not in allowed:
        raise EvaluationError("R (model-free) needs site and received_date among central_allowed_fields") from None
    canonicalisers: dict[str, Canonicaliser] = {}
    rows: dict[str, list[InputRow]] = {}
    for i, record in enumerate(records):
        site = record["site"]
        day = local_date(record["received_date"])
        if day is None or iso_week(day) > last_week:
            continue
        if "origin_site" in allowed:
            origin = record["origin_site"]
            if origin is not None and origin != site:
                continue
        if site not in canonicalisers:
            canonicalisers[site] = Canonicaliser(pack, known=master_data[site])
        projected = _project(record, pack, allowed)
        ref = f"rmf:{i}"
        for c in pair(codes_channel(projected, pack, canonicalisers[site]), None):
            rows.setdefault(site, []).append(InputRow(
                count_week=iso_week(day), record_ref=ref, root_ref=ref, reporter_id=ref, entity_type=c.entity_type,
                entity_id=c.entity_id, predicate=c.predicate, channel=c.channel, res_conf=c.res_conf))
    return {site: build_cells(rows[site], pack, master=master_data[site], k=1)[0] for site in sorted(rows)}


# --------------------------------------------------------------------------------------------------- alert events

def detector_alerts(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"week": a["week"], "rank": a["rank"], "key": a["key"], "score": a["score"], "site": None}
            for a in result["alerts"]]


def detector_candidates(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One ``{week, key}`` event per step at which a key was a detector candidate, after G4's stale filter, cooling
    or not; a key that only rules hit has none."""
    return [{"week": w, "key": c["key"]} for c in result["candidates"] for w in c["candidate_weeks"]]


def rule_alerts(result_x: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One event per episode start: a week in the key's union of rule-hit weeks whose previous ISO week is not."""
    union: dict[str, set[str]] = {}
    for hit in result_x["rule_hits"]:
        union.setdefault(hit["key"], set()).update(hit["weeks"])
    events = [{"week": w, "rank": None, "key": key, "score": None, "site": None}
              for key in sorted(union) for w in sorted(union[key]) if previous_week(w) not in union[key]]
    return sorted(events, key=lambda e: (e["week"], e["key"]))


def _prefix(values: Sequence[int]) -> list[int]:
    out = [0] * (len(values) + 1)
    for i, v in enumerate(values):
        out[i + 1] = out[i] + v
    return out


def single_site_alerts(pipeline: Pipeline, *, tie_salt: str) -> list[dict[str, Any]]:
    """G4's D2 site test on each site's exact weekly counts (codes plus text_only), its cooldown per (site, key), and
    one ``alert_budget_per_week`` shared by every site; ties ordered by ``sha256(tie_salt|site|key)``."""
    cfg = DetectorConfig.from_pack(pipeline.pack)
    pos = {w: i for i, w in enumerate(pipeline.weeks)}
    nw = len(pipeline.weeks)
    log_alpha = math.log(cfg.alpha_site)
    series: dict[tuple[str, str], list[int]] = {}
    start: dict[str, int] = {}
    for sid, cells in u_cells(pipeline).items():
        for c in cells:
            i = pos[c["iso_week"]]
            counts = series.setdefault((sid, series_key(c["entity_type"], c["entity_id"], c["predicate"])), [0] * nw)
            counts[i] += c["n"]
            start[sid] = min(start.get(sid, nw), i)
    prefixes = {sk: _prefix(series[sk]) for sk in sorted(series)}

    def span(prefix: list[int], lo: int, hi: int) -> int:
        lo = max(lo, 0)
        return prefix[hi + 1] - prefix[lo] if hi >= lo else 0

    events: list[dict[str, Any]] = []
    cooling: dict[tuple[str, str], int] = {}
    for i, week in enumerate(pipeline.weeks):
        found: dict[tuple[str, str], float] = {}
        for (sid, key), prefix in prefixes.items():
            history = max(0, i - cfg.window_weeks - start[sid] + 1)
            if history < cfg.min_history_weeks:
                continue
            c = span(prefix, i - cfg.window_weeks + 1, i)
            if c < 1:
                continue
            b_eff = min(cfg.baseline_weeks, history)
            top = i - cfg.window_weeks
            lam = max(cfg.lambda_floor, span(prefix, top - b_eff + 1, top) / b_eff)
            logp = stats.poisson_logsf(c, lam * cfg.window_weeks)
            if logp < log_alpha:
                found[(sid, key)] = 0.0 - logp
        for sk in sorted(cooling):
            if sk in found:
                cooling[sk] = 0
            else:
                cooling[sk] += 1
                if cooling[sk] >= cfg.cooldown_weeks:
                    del cooling[sk]
        ranked = sorted((sk for sk in sorted(found) if sk not in cooling),
                        key=lambda sk: (-found[sk], sha256_hex(f"{tie_salt}|{sk[0]}|{sk[1]}")))
        for rank, (sid, key) in enumerate(ranked[:cfg.alert_budget_per_week], start=1):
            events.append({"week": week, "rank": rank, "key": key, "score": found[(sid, key)], "site": sid})
            if cfg.cooldown_weeks > 0:
                cooling[(sid, key)] = 0
    return events

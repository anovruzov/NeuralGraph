"""Scoring: the fabric, the logs and the plant labels in; found, chance, false conclusions, the statistics and the
verdict out (``docs/collective/ROUTING-SPIKE.md`` 3.5 and 5).

The scorer reads only the fabric (each world's ``fabric/mycelic.db``, through the fabric's own store and
``mycelic.lineage.reconstruct``), HQ's receive log, the sites' egress logs and the plant labels. It never reads HQ's
internal tables.

Pattern p is **found** by route label L in a planted world when L's organisation holds an active raw note by L's agent
that (1) is the memory of a ``collective.conclusion`` event whose metadata names p's entity type, entity id and
predicate and a snapshot week inside p's found window; (2) has ``value`` ``supported``; (3) has lineage the fabric
reconstructs, whose source events include ``collective.verdict`` events from at least ``min_confirming_sites``
distinct sites, each ``confirm`` with a support bucket other than ``'<k'``; (4) each of those verdict events carries the
sha256 of a verdict row from that site in HQ's receive log and in that site's egress log. A find is a **chance find**
when the same seed's no-plant world, the same label, has a ``supported`` conclusion on p's key whose snapshot week is in
p's found window and no later than the planted world's earliest find. **Found net** is found and not a chance find.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective import stats
from mycelic.collective.edge.egress import SUPPRESSED, read_log
from mycelic.lineage import reconstruct
from mycelic.store import MycelicStore

from .publish import CONCLUSION_EVENT, VERDICT_EVENT, agent_of, org_of

PRIMARY = ("R", "U")
SECONDARY_PAIRS = (("R1", "U", None), ("R0", "U", None), ("P", "U", None), ("R", "U", 2), ("R", "U", 3),
                   ("R", "A", None), ("R", "O", None))
ARM_ORDER = ("R", "U", "A", "R1", "R0", "O", "P")


# --------------------------------------------------------------------------------------------------- logs

def log_index(world_dir: str | Path, site_ids: Sequence[str]) -> tuple[set[tuple[str, str]], dict[str, set[str]]]:
    """((site, sha256) of every verdict row in HQ's receive log, site -> sha256 of its egress log's verdict rows)."""
    world_dir = Path(world_dir)
    receive = {(row["site"], row["sha256"]) for row in read_log(world_dir / "hq" / "receive.jsonl")
               if row["artifact_type"] == "verdict"}
    egress = {site: {row["sha256"] for row in read_log(world_dir / "edge" / f"site-{site}.egress.jsonl")
                     if row["artifact_type"] == "verdict"} for site in site_ids}
    return receive, egress


# --------------------------------------------------------------------------------------------------- the fabric

def fabric_conclusions(db_path: str | Path, labels: Sequence[str], *, min_confirming_sites: int,
                       receive: set[tuple[str, str]], egress: Mapping[str, set[str]]) -> dict[str, list[dict[str, Any]]]:
    """Every conclusion note per route label, with the checks of 3.5 items 3 and 4."""
    store = MycelicStore(db_path)
    try:
        out: dict[str, list[dict[str, Any]]] = {}
        for label in labels:
            org, agent = org_of(label), agent_of(label)
            notes = store.list_memories(org, layers=["agent"], operator="agent_observation", producer_id=agent,
                                        status="active", limit=1_000_000, newest_first=False)
            rows = []
            for note in notes:
                md = note.metadata
                graph = reconstruct(store, note.memory_id, visible=lambda m: True)
                events = store.get_events(graph["evidence"]["source_event_ids"])
                conclusion = [e for e in events.values() if e.kind == "agent.event" and e.org_id == org
                              and e.payload.get("type") == CONCLUSION_EVENT]
                verdicts = [e.payload["payload"] for e in events.values() if e.kind == "agent.event"
                            and e.org_id == org and e.payload.get("type") == VERDICT_EVENT
                            and e.payload["payload"].get("question_id") == md.get("question_id")]
                confirming = [v for v in verdicts if v.get("source") == "site" and v.get("verdict") == "confirm"
                              and v.get("support_bucket") not in (None, SUPPRESSED)]
                confirm_sites = sorted({v["site"] for v in confirming})
                sha_ok = all((v["site"], v["sha256"]) in receive and v["sha256"] in egress.get(v["site"], set())
                             for v in confirming)
                status = md.get("value")
                reconstructable = bool(graph["evidence"]["reconstructable"])
                rows.append({
                    "memory_id": note.memory_id, "key": md.get("candidate_key"), "entity_type": md.get("entity_type"),
                    "entity_id": md.get("entity_id"), "predicate": md.get("predicate"),
                    "week": md.get("snapshot_week"), "question_id": md.get("question_id"), "status": status,
                    "conclusion_event": len(conclusion) == 1, "reconstructable": reconstructable,
                    "verdict_sites": sorted({v["site"] for v in verdicts}), "confirm_sites": confirm_sites,
                    "sha_ok": sha_ok,
                    "lineage_ok": (len(conclusion) == 1 and reconstructable
                                   and len(confirm_sites) >= min_confirming_sites and sha_ok),
                })
            out[label] = rows
        return out
    finally:
        asyncio.run(store.close())


def qualifying(row: Mapping[str, Any]) -> bool:
    return row["status"] == "supported" and row["lineage_ok"]


# --------------------------------------------------------------------------------------------------- found

def finds(conclusions: Sequence[Mapping[str, Any]], patterns: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """pattern id -> the earliest snapshot week of a qualifying conclusion true for it."""
    out: dict[str, str] = {}
    for p in patterns:
        weeks = [c["week"] for c in conclusions if qualifying(c) and c["entity_type"] == p["entity_type"]
                 and c["entity_id"] == p["entity_id"] and c["predicate"] == p["predicate"]
                 and p["found_from"] <= c["week"] <= p["found_to"]]
        if weeks:
            out[p["id"]] = min(weeks)
    return out


def chance_finds(found: Mapping[str, str], noplant: Sequence[Mapping[str, Any]],
                 patterns: Sequence[Mapping[str, Any]]) -> set[str]:
    """The finds whose key the no-plant world concluded ``supported`` in the found window, no later."""
    by_id = {p["id"]: p for p in patterns}
    out = set()
    for pid, week in found.items():
        p = by_id[pid]
        if any(c["status"] == "supported" and c["key"] == p["key"] and p["found_from"] <= c["week"] <= p["found_to"]
               and c["week"] <= week for c in noplant):
            out.add(pid)
    return out


def label_counts(planted: Sequence[Mapping[str, Any]], noplant: Sequence[Mapping[str, Any]],
                 patterns: Sequence[Mapping[str, Any]], candidate_labels: Mapping[str, Mapping[str, Any]],
                 noplant_candidates: int) -> dict[str, Any]:
    """One route label in one seed: found, chance, found net (all and by stratum), no-plant false conclusions and the
    planted world's false conclusions."""
    found = finds(planted, patterns)
    chance = chance_finds(found, noplant, patterns)
    net = sorted(set(found) - chance)
    sites_of = {p["id"]: len(p["sites"]) for p in patterns}
    fc = sum(1 for c in noplant if c["status"] == "supported")
    false_planted: dict[str, int] = {}
    for c in planted:
        lab = candidate_labels.get(c["question_id"])
        if c["status"] == "supported" and lab is not None and lab["label"] != "true":
            name = "background" if lab["label"] == "background" else f"decoy:{lab['decoy_class']}"
            false_planted[name] = false_planted.get(name, 0) + 1
    return {"found": len(found), "chance": len(chance), "found_net": len(net),
            "found_net_2site": sum(1 for pid in net if sites_of[pid] == 2),
            "found_net_3site": sum(1 for pid in net if sites_of[pid] == 3),
            "found_ids": sorted(found), "chance_ids": sorted(chance),
            "noplant_supported": fc, "noplant_candidates": noplant_candidates,
            "noplant_rate": fc / noplant_candidates if noplant_candidates else 0.0,
            "planted_false_supported": dict(sorted(false_planted.items())),
            "planted_false_supported_total": sum(false_planted.values())}


def arm_value(per_label: Mapping[str, Mapping[str, Any]], arm: str, field: str) -> float:
    """An arm's value: the label's own, or for U the exact mean over every subset label."""
    if arm == "U":
        subsets = sorted(label for label in per_label if label.startswith("U"))
        return math.fsum(float(per_label[label][field]) for label in subsets) / len(subsets)
    return float(per_label[arm][field])


# --------------------------------------------------------------------------------------------------- statistics

def bootstrap(a: Sequence[float], b: Sequence[float], seed: str, B: int, alpha: float) -> dict[str, Any]:
    return stats.paired_bootstrap(list(a), list(b), B=B, seed=seed, alpha=alpha)


def primary_statistic(f_r: Sequence[float], f_u: Sequence[float], *, B: int = 10000, alpha: float = 0.05,
                      seed: str = "routing-spike:primary") -> dict[str, Any]:
    res = bootstrap(f_r, f_u, seed, B, alpha)
    return {**res, "pass": res["ci_low"] is not None and res["ci_low"] > 0}


def noplant_statistic(r_r: Sequence[float], r_u: Sequence[float], *, bar: float = 0.05, B: int = 10000,
                      alpha: float = 0.05, seed: str = "routing-spike:noplant") -> dict[str, Any]:
    res = bootstrap(r_r, r_u, seed, B, alpha)
    return {**res, "bar": bar, "pass": res["ci_high"] is not None and res["ci_high"] <= bar}


def verdict(checks: Mapping[str, Mapping[str, Any]], primary: Mapping[str, Any],
            noplant: Mapping[str, Any]) -> dict[str, Any]:
    """Pass, fail or withheld (section 5.3)."""
    failed = sorted(name for name, c in checks.items() if not c["ok"])
    if failed:
        return {"verdict": "withheld", "failed_checks": failed}
    if primary["pass"] and noplant["pass"]:
        return {"verdict": "pass", "failed_checks": []}
    return {"verdict": "fail", "failed_checks": [],
            "missed": [name for name, ok in (("primary", primary["pass"]), ("noplant", noplant["pass"])) if not ok]}


def summarise(seeds: Sequence[int], per_seed: Mapping[int, Mapping[str, Mapping[str, Any]]], *, B: int, alpha: float,
              bar: float, primary_seed: str, noplant_seed: str) -> dict[str, Any]:
    """Per-seed arm values, the primary and no-plant statistics and the secondary comparisons."""
    arms = ARM_ORDER
    table = {arm: {field: [arm_value(per_seed[s], arm, field) for s in seeds]
                   for field in ("found", "chance", "found_net", "found_net_2site", "found_net_3site",
                                 "noplant_supported", "noplant_rate", "planted_false_supported_total")}
             for arm in arms}
    primary = primary_statistic(table["R"]["found_net"], table["U"]["found_net"], B=B, alpha=alpha,
                                seed=primary_seed)
    noplant = noplant_statistic(table["R"]["noplant_rate"], table["U"]["noplant_rate"], bar=bar, B=B, alpha=alpha,
                                seed=noplant_seed)
    secondary = []
    for a, b, stratum in SECONDARY_PAIRS:
        field = "found_net" if stratum is None else f"found_net_{stratum}site"
        seed = f"routing-spike:{a}-{b}" + ("" if stratum is None else f":{stratum}-site")
        res = bootstrap(table[a][field], table[b][field], seed, B, alpha)
        secondary.append({"a": a, "b": b, "stratum": stratum, "field": field, **res})
    totals = {}
    for arm in arms:
        n_total = sum(per_seed[s][arm if arm != "U" else "U01"]["noplant_candidates"] for s in seeds)
        fc_total = math.fsum(table[arm]["noplant_supported"])
        totals[arm] = {"found_net_total": math.fsum(table[arm]["found_net"]),
                       "found_total": math.fsum(table[arm]["found"]), "chance_total": math.fsum(table[arm]["chance"]),
                       "noplant_supported_total": fc_total, "noplant_candidates_total": n_total,
                       "noplant_pooled_rate": fc_total / n_total if n_total else 0.0,
                       "planted_false_supported_total": math.fsum(table[arm]["planted_false_supported_total"])}
    return {"per_seed": table, "primary": primary, "noplant": noplant, "secondary": secondary, "totals": totals}


def by_decoy_class(per_seed: Mapping[int, Mapping[str, Mapping[str, Any]]], seeds: Iterable[int]) -> dict[str, Any]:
    """Planted-world false conclusions per arm and class (U: mean over subsets), summed over seeds."""
    out: dict[str, dict[str, float]] = {}
    for arm in ARM_ORDER:
        acc: dict[str, float] = {}
        for s in seeds:
            labels = [lab for lab in per_seed[s] if lab.startswith("U")] if arm == "U" else [arm]
            for lab in labels:
                for name, n in per_seed[s][lab]["planted_false_supported"].items():
                    acc[name] = acc.get(name, 0.0) + n / len(labels)
        out[arm] = dict(sorted(acc.items()))
    return out

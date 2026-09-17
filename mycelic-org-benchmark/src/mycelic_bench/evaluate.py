"""Evaluation: match system claims against ground truth and compute metrics.

This is the only module (besides `world.py`) allowed to touch `Effect` and
`World.p_true`.  Systems hand over their *knowledge base* as a list of
`ClaimRecord`s (cell, label, sign, scope unit, layer, round accepted,
confidence, lineage units, support) and the evaluator does the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .vocab import N_LABELS, cell_index
from .world import Effect, World, cell_match_mask

LAYERS5 = ("worker", "team", "department", "region", "executive")
LAYER_RANK = {l: i for i, l in enumerate(LAYERS5)}


@dataclass
class ClaimRecord:
    cell: int
    label: int
    sign: int
    scope_layer: str
    scope_unit: int          # unit index at scope_layer (-1 = organisation-wide / unknown)
    layer: str               # layer of the knowledge base holding it
    round_accepted: int
    confidence: float
    independent_support: float
    replica_count: int
    contributing_units: list[str] = field(default_factory=list)
    origin_attack: str | None = None
    status: str = "accepted"
    conditional: bool = False
    claim_id: str = ""
    valid_to: int | None = None
    disputed: bool = False


def unit_index(world: World, unit_id: str) -> tuple[str, int]:
    org = world.org
    if unit_id == "EXEC":
        return "executive", 0
    for layer, ids in (("team", org.team_ids), ("department", org.department_ids), ("region", org.region_ids)):
        if unit_id in ids:
            return layer, ids.index(unit_id)
    if unit_id.startswith("W"):
        return "worker", int(unit_id[1:])
    if unit_id.startswith("S"):   # squad -> its team
        return "team", int(unit_id[1:5])
    if unit_id.startswith("V"):   # division -> region-ish; treat as executive-wide
        return "executive", 0
    return "executive", 0


def scope_mask(world: World, layer: str, unit: int) -> np.ndarray | None:
    if layer == "executive" or unit < 0:
        return None
    return world.org.membership(layer)[world.worker] == unit


def scopes_nested(world: World, a: tuple[str, int], b: tuple[str, int]) -> bool:
    """True if one scope's subtree contains the other's."""
    (la, ua), (lb, ub) = a, b
    if la == "executive" or lb == "executive":
        return True
    if LAYER_RANK[la] < LAYER_RANK[lb]:
        (la, ua), (lb, ub) = (lb, ub), (la, ua)
    # now la is the higher layer; check ub's ancestor at la equals ua
    org = world.org
    w = np.flatnonzero(org.membership(lb) == ub)
    if len(w) == 0:
        return False
    return int(org.membership(la)[w[0]]) == ua


class PopulationTruth:
    """Noise-free truth of an association at a scope: uses World.p_true."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.ci = cell_index()
        self._cache: dict[tuple, tuple[float, float]] = {}

    def evaluate(self, cell: int, label: int, layer: str, unit: int, rounds: tuple[int, int] | None = None) -> tuple[float, float]:
        key = (cell, label, layer, unit, rounds)
        if key in self._cache:
            return self._cache[key]
        w = self.world
        base_mask = scope_mask(w, layer, unit)
        m_all = np.ones(w.n, dtype=bool) if base_mask is None else base_mask.copy()
        if rounds is not None:
            m_all &= (w.round >= rounds[0]) & (w.round < rounds[1])
        cm = cell_match_mask(w.attrs, cell)
        inside = m_all & cm
        if inside.sum() == 0:
            self._cache[key] = (0.0, 0.0)
            return self._cache[key]
        p_in = float(w.p_true[inside, label].mean())
        pairs = self.ci.decode(int(cell))
        best_out = -1.0
        if len(pairs) == 1:
            out = m_all & ~cm
            best_out = float(w.p_true[out, label].mean()) if out.any() else 0.0
        else:
            for drop in range(len(pairs)):
                sub = np.ones(w.n, dtype=bool)
                for j, (a, v) in enumerate(pairs):
                    if j != drop:
                        sub &= w.attrs[:, a] == v
                out = m_all & sub & ~cm
                if out.any():
                    best_out = max(best_out, float(w.p_true[out, label].mean()))
        if best_out < 0:
            best_out = float(w.p_true[m_all & ~cm, label].mean()) if (m_all & ~cm).any() else 0.0
        self._cache[key] = (p_in, best_out)
        return self._cache[key]

    def attack_fraction(self, cell: int, layer: str, unit: int) -> float:
        """Fraction of records matching the cell (at scope) that carry an attack tag."""
        w = self.world
        if not (w.attack_tag > 0).any():
            return 0.0
        key = ("atk", cell, layer, unit)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        base_mask = scope_mask(w, layer, unit)
        m = cell_match_mask(w.attrs, cell)
        if base_mask is not None:
            m &= base_mask
        frac = float((w.attack_tag[m] > 0).mean()) if m.any() else 0.0
        self._cache[key] = frac  # type: ignore[assignment]
        return frac


def match_effects(world: World, claims: list[ClaimRecord], effect_min: float = 0.08) -> dict[str, Any]:
    """Classify each claim and compute discovery metrics per effect kind."""
    ci = cell_index()
    pt = PopulationTruth(world)
    truth_effects = [e for e in world.effects if e.kind in ("base_rate", "local", "cross_team", "global", "temporal", "contradiction")
                     and e.delta > 0 and e.true_side]
    by_sig: dict[tuple[int, int], list[Effect]] = {}
    for e in truth_effects:
        by_sig.setdefault((e.cell, e.label), []).append(e)
    cells_by_label: dict[int, list[Effect]] = {}
    for e in truth_effects:
        cells_by_label.setdefault(e.label, []).append(e)

    def effect_scope(e: Effect) -> tuple[str, int]:
        if e.scope_layer in ("team", "department", "region"):
            return e.scope_layer, int(e.scope_unit)
        return "executive", 0

    classified: list[dict[str, Any]] = []
    discovered: dict[str, dict[str, Any]] = {}   # effect_id -> first matching claim info
    for c in claims:
        if c.status not in ("accepted", "contested"):
            continue
        cat = "false"
        matched: Effect | None = None
        # exact
        for e in by_sig.get((c.cell, c.label), []):
            if e.sign == c.sign and scopes_nested(world, (c.scope_layer, c.scope_unit), effect_scope(e)):
                # temporal: the claim must be alive when the phase was active
                if e.kind == "temporal":
                    ok = c.round_accepted < (e.valid_to or 10**9) + 2 and (c.valid_to is None or c.valid_to >= e.valid_from)
                    if not ok:
                        continue
                cat, matched = "exact", e
                break
        if matched is None:
            cset = set(ci.decode(int(c.cell)))
            for e in cells_by_label.get(c.label, []):
                if e.sign != c.sign or not scopes_nested(world, (c.scope_layer, c.scope_unit), effect_scope(e)):
                    continue
                eset = set(ci.decode(int(e.cell)))
                if eset < cset:
                    cat, matched = "over_specified", e
                    break
                if cset < eset:
                    cat, matched = "under_specified", e
                    break
        p_in, p_out = pt.evaluate(int(c.cell), int(c.label), c.scope_layer, c.scope_unit)
        pop_true = (p_in - p_out) * c.sign >= effect_min / 4
        if cat == "false" and pop_true:
            cat = "confounded_true"
        attack_frac = pt.attack_fraction(int(c.cell), c.scope_layer, c.scope_unit)
        if c.origin_attack or (not pop_true and attack_frac >= 0.3):
            cat = "poison" if not pop_true else cat
        classified.append({"claim_id": c.claim_id, "cell": int(c.cell), "label": int(c.label), "sign": c.sign,
                           "scope": f"{c.scope_layer}:{c.scope_unit}", "layer": c.layer, "category": cat, "status": c.status,
                           "disputed": c.disputed,
                           "effect_id": matched.effect_id if matched else None, "population_true": bool(pop_true),
                           "p_in": p_in, "p_out": p_out, "confidence": c.confidence, "round": c.round_accepted,
                           "attack_fraction": attack_frac,
                           "is": c.independent_support, "replica": c.replica_count, "attack": c.origin_attack})
        if matched is not None and cat == "exact":
            d = discovered.get(matched.effect_id)
            better = d is None or (c.status == "accepted" and d["status"] != "accepted") or \
                (c.status == d["status"] and c.round_accepted < d["round"])
            if better:
                discovered[matched.effect_id] = {"round": c.round_accepted, "layer": c.layer, "scope": f"{c.scope_layer}:{c.scope_unit}",
                                                 "claim_id": c.claim_id, "is": c.independent_support, "status": c.status,
                                                 "contributing_units": c.contributing_units, "confidence": c.confidence,
                                                 "scope_layer": c.scope_layer}
    # per-kind recall
    metrics: dict[str, Any] = {}
    acc = [c for c in classified if c["status"] == "accepted"]
    n_acc = len(acc)
    cats = {k: sum(1 for c in acc if c["category"] == k) for k in ("exact", "under_specified", "over_specified", "confounded_true", "false", "poison")}
    metrics["n_accepted"] = n_acc
    metrics["n_contested"] = sum(1 for c in classified if c["status"] == "contested")
    metrics["contested_false"] = sum(1 for c in classified if c["status"] == "contested" and not c["population_true"])
    metrics["contested_true"] = sum(1 for c in classified if c["status"] == "contested" and c["population_true"])
    metrics["categories"] = cats
    classified_all = classified
    classified = acc
    metrics["precision_strict"] = cats["exact"] / n_acc if n_acc else 0.0
    metrics["precision_lenient"] = (cats["exact"] + cats["under_specified"] + cats["confounded_true"]) / n_acc if n_acc else 0.0
    metrics["false_discovery_rate"] = (cats["false"] + cats["poison"] + cats["over_specified"]) / n_acc if n_acc else 0.0
    metrics["false_association_rate"] = sum(1 for c in classified if not c["population_true"]) / n_acc if n_acc else 0.0
    metrics["false_but_disputed"] = sum(1 for c in classified if not c["population_true"] and c.get("disputed"))
    metrics["n_disputed"] = sum(1 for c in classified if c.get("disputed"))
    for kind in ("local", "cross_team", "global", "temporal", "contradiction"):
        es = [e for e in truth_effects if e.kind == kind]
        if kind == "temporal":
            # count groups whose active phases were tracked: recall per phase
            pass
        n_e = len(es)
        found = [e for e in es if e.effect_id in discovered and discovered[e.effect_id]["status"] == "accepted"]
        found_any = [e for e in es if e.effect_id in discovered]
        metrics[f"recall_{kind}"] = len(found) / n_e if n_e else float("nan")
        metrics[f"recall_{kind}_lenient"] = len(found_any) / n_e if n_e else float("nan")
        metrics[f"n_{kind}"] = n_e
        if kind in ("cross_team", "global"):
            ttd = [discovered[e.effect_id]["round"] for e in found]
            metrics[f"ttd_{kind}_median"] = float(np.median(ttd)) if ttd else float("nan")
            metrics[f"ttd_{kind}_mean"] = float(np.mean(ttd)) if ttd else float("nan")
            # layers needed: layer where first accepted vs ground-truth minimum layer
            extra = []
            for e in found:
                fl = discovered[e.effect_id]["layer"]
                if fl in LAYER_RANK and e.min_layer in LAYER_RANK:
                    extra.append(LAYER_RANK[fl] - LAYER_RANK[e.min_layer])
            metrics[f"layers_beyond_min_{kind}"] = float(np.mean(extra)) if extra else float("nan")
    # calibration (ECE over accepted claims: confidence vs population truth)
    if classified:
        conf = np.array([c["confidence"] for c in classified]); tr = np.array([c["population_true"] for c in classified], dtype=float)
        bins = np.clip((conf * 10).astype(int), 0, 9)
        ece = 0.0
        for b in range(10):
            m = bins == b
            if m.any():
                ece += m.mean() * abs(conf[m].mean() - tr[m].mean())
        metrics["ece"] = float(ece)
        metrics["mean_confidence"] = float(conf.mean())
    else:
        metrics["ece"] = float("nan"); metrics["mean_confidence"] = float("nan")
    metrics["discovered"] = discovered
    metrics["classified"] = classified_all
    return metrics


def evidence_metrics(world: World, discovered: dict[str, dict[str, Any]], hier=None) -> dict[str, Any]:
    """Evidence coverage / source diversity / lineage correctness for discovered effects.
    Uses the claim's contributing units (lineage pointers) against the true
    distribution of matching interactions."""
    org = world.org
    cov, div, lin = [], [], []
    for eid, d in discovered.items():
        e = next(x for x in world.effects if x.effect_id == eid)
        m = world.effect_active_mask(e) & ~world.is_copy
        if not m.any():
            continue
        units = d.get("contributing_units") or []
        layer_of_units = None
        # map contributing units to teams / departments / regions
        true_teams = set(int(t) for t in np.unique(world.team[m]))
        true_depts = set(int(t) for t in np.unique(world.department[m]))
        true_regs = set(int(t) for t in np.unique(world.region[m]))
        claimed = set()
        for u in units:
            layer, idx = unit_index(world, u)
            claimed.add((layer, idx))
        if not claimed:
            continue
        # evidence coverage: fraction of matching interactions inside claimed units
        covered = np.zeros(world.n, dtype=bool)
        correct = 0
        for layer, idx in claimed:
            if layer == "executive":
                covered |= True; correct += 1
                continue
            sm = org.membership(layer)[world.worker] == idx
            covered |= sm
            if (sm & m).any():
                correct += 1
        cov.append(float((covered & m).sum() / m.sum()))
        lin.append(correct / len(claimed))
        div.append(len({idx for layer, idx in claimed}))
    return {"evidence_coverage": float(np.mean(cov)) if cov else float("nan"),
            "lineage_correctness": float(np.mean(lin)) if lin else float("nan"),
            "source_diversity": float(np.mean(div)) if div else float("nan")}


def transition_fidelity(world: World, kb_by_layer: dict[str, list[ClaimRecord]], effect_min: float = 0.08) -> dict[str, Any]:
    """Track every true effect through layer transitions (DESIGN.md §10)."""
    layers = [l for l in LAYERS5 if l in kb_by_layer]
    truth = [e for e in world.effects if e.kind in ("local", "cross_team", "global") and e.delta > 0]
    per_layer_match = {}
    for l in layers:
        m = match_effects(world, kb_by_layer[l], effect_min)
        per_layer_match[l] = m
    table: dict[str, dict[str, float]] = {}
    prev = None
    for l in layers:
        m = per_layer_match[l]
        disc = m["discovered"]
        row: dict[str, float] = {}
        row["useful_fact_recall"] = sum(1 for e in truth if e.effect_id in disc) / max(len(truth), 1)
        row["precision_strict"] = m["precision_strict"]
        row["false_promotion_rate"] = m["false_discovery_rate"]
        row["n_accepted"] = m["n_accepted"]
        cl = [c for c in m["classified"] if c["status"] == "accepted"]
        exact_ids = [c["effect_id"] for c in cl if c["category"] == "exact"]
        row["duplicated"] = (len(exact_ids) - len(set(exact_ids))) / max(len(set(exact_ids)), 1)
        row["distorted"] = float(np.mean([abs((c["p_in"] - c["p_out"]) - next(e.delta for e in world.effects if e.effect_id == c["effect_id"])) > 0.5 *
                                          next(e.delta for e in world.effects if e.effect_id == c["effect_id"])
                                          for c in cl if c["category"] == "exact"])) if exact_ids else 0.0
        if prev is not None:
            pd = per_layer_match[prev]["discovered"]
            row["survived"] = sum(1 for eid in pd if eid in disc) / max(len(pd), 1)
            row["dropped"] = sum(1 for eid in pd if eid not in disc) / max(len(pd), 1)
        else:
            row["survived"] = 1.0; row["dropped"] = 0.0
        # independent support preserved: |IS_est - IS_true| <= 1 at that layer
        is_ok, lin_ok = [], []
        for eid, d in disc.items():
            e = next(x for x in world.effects if x.effect_id == eid)
            t = e.true_independent_support.get(d.get("scope_layer", l))
            if t is not None:
                is_ok.append(abs(d["is"] - t) <= max(1.0, 0.25 * t))
            lin_ok.append(bool(d.get("contributing_units")))
        row["independent_support_preserved"] = float(np.mean(is_ok)) if is_ok else float("nan")
        row["lineage_retention"] = float(np.mean(lin_ok)) if lin_ok else float("nan")
        table[l] = row
        prev = l
    return {"table": table, "per_layer": {l: {k: v for k, v in per_layer_match[l].items() if k not in ("classified", "discovered")} for l in layers}}


def temporal_metrics(world: World, kb_snapshots: dict[int, list[ClaimRecord]]) -> dict[str, Any]:
    """Executive-kernel freshness across rounds for temporal revision groups."""
    groups: dict[str, list[Effect]] = {}
    for e in world.effects:
        if e.kind == "temporal":
            groups.setdefault(e.group, []).append(e)
    if not groups or not kb_snapshots:
        return {"revision_accuracy": float("nan"), "stale_persistence": float("nan"), "revision_latency": float("nan"),
                "catastrophic_overwrite": float("nan"), "old_evidence_retention": float("nan")}
    correct, stale, lat, total = 0, 0, [], 0
    superseded_retained, superseded_total = 0, 0
    for r, kb in sorted(kb_snapshots.items()):
        believed = {(c.cell, c.label) for c in kb if c.status == "accepted" and c.sign > 0}
        retained = {(c.cell, c.label) for c in kb if c.status == "superseded"}
        for gid, es in groups.items():
            active = any(e.is_active(r) and e.delta > 0 for e in es)
            key = (es[0].cell, es[0].label)
            total += 1
            if (key in believed) == active:
                correct += 1
            elif key in believed and not active:
                stale += 1
            if not active and key in retained:
                superseded_retained += 1
            if not active and (key in retained or key in believed):
                superseded_total += 1
    # revision latency: rounds from a phase switch (delta -> 0) until belief drops
    for gid, es in groups.items():
        key = (es[0].cell, es[0].label)
        for e in es:
            if e.delta == 0 and e.valid_from > 0:
                # was it believed just before the switch?
                before = kb_snapshots.get(e.valid_from - 1)
                if before is None or key not in {(c.cell, c.label) for c in before if c.status == "accepted"}:
                    continue
                dropped_at = None
                for r in range(e.valid_from, (e.valid_to or world.n_rounds)):
                    kb = kb_snapshots.get(r)
                    if kb is not None and key not in {(c.cell, c.label) for c in kb if c.status == "accepted"}:
                        dropped_at = r; break
                lat.append((dropped_at - e.valid_from) if dropped_at is not None else (e.valid_to or world.n_rounds) - e.valid_from)
    return {"revision_accuracy": correct / max(total, 1), "stale_persistence": stale / max(total, 1),
            "revision_latency": float(np.mean(lat)) if lat else float("nan"),
            "old_evidence_retention": superseded_retained / max(superseded_total, 1) if superseded_total else float("nan"),
            "catastrophic_overwrite": 1 - superseded_retained / max(superseded_total, 1) if superseded_total else float("nan")}


def contradiction_metrics(world: World, conflicts: list[dict[str, Any]], kb: list[ClaimRecord]) -> dict[str, Any]:
    """Detection and resolution quality per contradiction shape.

    biased_positive: the positive claim is false -> correct = detected and NOT accepted organisation-wide.
    biased_null:     the positive claim is true  -> correct = accepted (possibly after a resolved conflict).
    conditional:     both sides true            -> correct = split by region or kept open; wrong = one side wins.
    """
    groups: dict[str, list[Effect]] = {}
    for e in world.effects:
        if e.kind == "contradiction":
            groups.setdefault(e.group, []).append(e)
    if not groups:
        return {"contradiction_f1": float("nan")}
    conf_by_sig: dict[tuple[int, int], dict[str, Any]] = {}
    for c in conflicts:
        k = (c["cell"], c["label"])
        prev = conf_by_sig.get(k)
        if prev is None or c.get("round_opened", 0) < prev.get("round_opened", 0):
            conf_by_sig[k] = c
    wide_accepted = {(c.cell, c.label) for c in kb if c.status == "accepted" and c.sign > 0 and c.scope_layer in ("executive", "region", "department")}
    any_accepted = {(c.cell, c.label) for c in kb if c.status == "accepted" and c.sign > 0}
    conditional_accepted = {(c.cell, c.label) for c in kb if c.status == "accepted" and c.sign > 0 and c.conditional}
    gt_keys = {(es[0].cell, es[0].label) for es in groups.values()}
    tp = sum(1 for k in gt_keys if k in conf_by_sig)
    fn = len(gt_keys) - tp
    fp = sum(1 for k in conf_by_sig if k not in gt_keys)
    correct = incorrect = unresolved_ok = n_cond = 0
    detect_rounds = [conf_by_sig[k].get("round_opened", 0) for k in gt_keys if k in conf_by_sig]
    per_shape: dict[str, dict[str, int]] = {}
    for gid, es in groups.items():
        key = (es[0].cell, es[0].label)
        shape = es[0].shape or ("conditional" if es[0].conditional else "biased_null")
        ps = per_shape.setdefault(shape, {"n": 0, "correct": 0, "incorrect": 0, "detected": 0})
        ps["n"] += 1
        c = conf_by_sig.get(key)
        if c is not None:
            ps["detected"] += 1
        if shape == "biased_positive":
            ok = key not in wide_accepted
            bad = key in wide_accepted
        elif shape == "biased_null":
            ok = key in any_accepted
            bad = (key not in any_accepted) and c is not None
        else:  # conditional
            n_cond += 1
            ok = (key in conditional_accepted) or (c is not None and c.get("status") in ("contested", "open", "split_conditional"))
            bad = (key in wide_accepted and key not in conditional_accepted and c is not None and c.get("status") == "resolved")
            if ok:
                unresolved_ok += 1
        correct += int(ok); incorrect += int(bad)
        ps["correct"] += int(ok); ps["incorrect"] += int(bad)
    prec = tp / max(tp + fp, 1); rec = tp / max(tp + fn, 1)
    return {"contradiction_precision": prec, "contradiction_recall": rec,
            "contradiction_f1": 2 * prec * rec / max(prec + rec, 1e-9),
            "correct_resolution_rate": correct / max(len(groups), 1), "incorrect_resolution_rate": incorrect / max(len(groups), 1),
            "unresolved_when_appropriate": unresolved_ok / n_cond if n_cond else float("nan"),
            "time_to_detect_mean": float(np.mean(detect_rounds)) if detect_rounds else float("nan"),
            "n_conflicts_raised": len(conf_by_sig), "per_shape": per_shape}


def privacy_metrics(world: World, canaries_exposed: list[str], bytes_off_device: int, raw_bytes: int,
                    promoted_cells_small: int, promoted_cells_total: int) -> dict[str, Any]:
    all_canaries = {c for c in world.canary if c}
    exposed = {c for c in canaries_exposed if c}
    return {"raw_sensitive_leakage": len(exposed & all_canaries) / max(len(all_canaries), 1),
            "n_canaries_exposed": len(exposed & all_canaries), "n_canaries": len(all_canaries),
            "bytes_off_device": bytes_off_device, "raw_bytes": raw_bytes,
            "fraction_raw_exposed": bytes_off_device / max(raw_bytes, 1),
            "reconstructability": promoted_cells_small / max(promoted_cells_total, 1)}


def raw_record_bytes(world: World, idx: np.ndarray | None = None) -> int:
    """Approximate serialized size of raw records (JSON) without rendering every one."""
    n = world.n if idx is None else len(idx)
    if n == 0:
        return 0
    sample = np.arange(min(n, 200)) if idx is None else idx[: min(len(idx), 200)]
    import json
    avg = np.mean([len(json.dumps(world.record(int(i)))) for i in sample])
    return int(avg * n)

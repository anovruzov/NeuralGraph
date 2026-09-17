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


def _n_min_round(world: World, e: Effect) -> int:
    """Round at which the world first held ``e.n_min`` matching, active, non-copy interactions anywhere
    (the time origin of time-to-discovery, DESIGN.md 10)."""
    r = np.sort(world.round[world.effect_active_mask(e) & ~world.is_copy])
    if len(r) == 0:
        return 0
    return int(r[min(max(int(e.n_min), 1), len(r)) - 1])


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
    is_at_scope: dict[str, list[tuple[str, float, str]]] = {}
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
        pop_true_strict = (p_in - p_out) * c.sign >= effect_min / 2   # sensitivity: half the claim's own threshold
        if cat == "false" and pop_true:
            cat = "confounded_true"
        attack_frac = pt.attack_fraction(int(c.cell), c.scope_layer, c.scope_unit)
        if c.origin_attack or (not pop_true and attack_frac >= 0.3):
            cat = "poison" if not pop_true else cat
        classified.append({"claim_id": c.claim_id, "cell": int(c.cell), "label": int(c.label), "sign": c.sign,
                           "scope": f"{c.scope_layer}:{c.scope_unit}", "layer": c.layer, "category": cat, "status": c.status,
                           "disputed": c.disputed,
                           "effect_id": matched.effect_id if matched else None, "population_true": bool(pop_true),
                           "population_true_strict": bool(pop_true_strict),
                           "p_in": p_in, "p_out": p_out, "confidence": c.confidence, "round": c.round_accepted,
                           "attack_fraction": attack_frac,
                           "is": c.independent_support, "replica": c.replica_count, "attack": c.origin_attack})
        if matched is not None and cat == "exact":
            es_layer = effect_scope(matched)[0]
            if c.scope_layer == es_layer or (es_layer == "executive" and c.scope_layer in ("region", "executive")):
                is_at_scope.setdefault(matched.effect_id, []).append((c.status, c.independent_support, c.scope_layer))
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
    metrics["false_association_rate_strict"] = sum(1 for c in classified if not c["population_true_strict"]) / n_acc if n_acc else 0.0
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
            # `ttd_*` = first round the effect was accepted at the root (absolute; only discovered effects, so it is
            # conditional on discovery and must be read next to recall).  `ttd_*_rel_nmin_*` is the DESIGN.md 10
            # definition (minus the round when the world first held n_min matching interactions anywhere); it is
            # negative when a system detects an effect with fewer records than the generator's n_min (computed at
            # power_alpha=1e-8, far below the shared test's effective BH threshold), which is why it is diagnostic only.
            ttd = [discovered[e.effect_id]["round"] for e in found]
            rel = [r - _n_min_round(world, e) for r, e in zip(ttd, found)]
            metrics[f"ttd_{kind}_median"] = float(np.median(ttd)) if ttd else float("nan")
            metrics[f"ttd_{kind}_mean"] = float(np.mean(ttd)) if ttd else float("nan")
            metrics[f"ttd_{kind}_rel_nmin_median"] = float(np.median(rel)) if rel else float("nan")
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
    for eid, lst in is_at_scope.items():
        if eid in discovered:
            acc = [x for x in lst if x[0] == "accepted"] or lst
            discovered[eid]["is_at_scope"] = float(np.median([x[1] for x in acc]))
            discovered[eid]["is_scope_layer"] = acc[0][2]
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
        # fraction of exactly-matched effects reported by more than one accepted claim at this layer (in [0, 1];
        # the former "extra claims per effect" ratio exceeded 1 as soon as an effect had three claims)
        n_per_effect: dict[Any, int] = {}
        for eid in exact_ids:
            n_per_effect[eid] = n_per_effect.get(eid, 0) + 1
        row["duplicated"] = sum(1 for v in n_per_effect.values() if v > 1) / max(len(n_per_effect), 1)
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
            layer_t = d.get("is_scope_layer")
            t = e.true_independent_support.get(layer_t) if layer_t else None
            if t is not None and "is_at_scope" in d:
                is_ok.append(abs(d["is_at_scope"] - t) <= max(1.0, 0.25 * t))
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
    scoped_keys = {(e.cell, e.label) for e in world.effects if e.kind in ("local", "cross_team") and e.delta > 0}
    tp = sum(1 for k in gt_keys if k in conf_by_sig)
    fn = len(gt_keys) - tp
    # a conflict about a genuinely scoped effect (one unit sees it, siblings do not) is a legitimate
    # scope disagreement; conflicts about other signatures are real data disagreements too (projections,
    # specialization confounds) and are reported separately rather than counted as false contradictions
    n_scope = sum(1 for k in conf_by_sig if k not in gt_keys and k in scoped_keys)
    fp = sum(1 for k in conf_by_sig if k not in gt_keys and k not in scoped_keys)
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
            "n_conflicts_raised": len(conf_by_sig), "n_scope_disagreements": n_scope, "n_other_conflicts": fp,
            "other_conflict_rate": fp / max(len(conf_by_sig), 1), "per_shape": per_shape}


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


def _true_cell_rate(world: World, e: Effect) -> float:
    """Noise-free rate of the effect's label inside its cell (and scope): what a perfect perceiver's
    evidence would show.  NaN when the world carries no population truth."""
    pt = getattr(world, "p_true", None)
    if pt is None:
        return float("nan")
    mask = cell_match_mask(world.attrs, e.cell)
    if e.scope_layer in ("team", "department", "region"):
        mask &= world.org.membership(e.scope_layer)[world.worker] == e.scope_unit
    if not mask.any():
        return float("nan")
    return float(pt[mask, e.label].mean())


def _attenuated(k: int, n: int, true_rate: float, factor: float = 0.7) -> bool:
    """True when the system's perceived label rate is below `factor` x the noise-free rate: the evidence
    reached the tester but perception (attribute omission, label drop) attenuated it."""
    return n > 0 and true_rate == true_rate and (k / n) < factor * true_rate


def failure_reasons_from_probe(world: World, m: dict[str, Any], probe, kinds: tuple[str, ...] = ("cross_team", "global")) -> dict[str, int]:
    """Failure-reason classification for systems without a `Hierarchy` (baselines).

    HOOK (added for baselines.py): the system may not read ground truth, so it hands over `probe(cell, label,
    scope_layer, scope_unit) -> dict` describing its *own* state for a signature: `order_supported` (bool),
    `tested_scope` / `tested_wide` (was the signature ever tested at that scope / organisation-wide),
    `n_scope`, `k_scope`, `n_wide`, `k_wide` (its counts), optional `retrieved_n` (RAG context matches),
    `votes_for` (vote systems) and `untested_reason`.  This function maps those facts plus the effect's
    truth (n_min, delta, validity, attack fraction) to the task's reason categories.
    """
    pt = PopulationTruth(world)
    disc = m.get("discovered", {})
    reasons: dict[str, int] = {}
    for e in world.effects:
        if e.kind not in kinds or not e.true_side or e.delta <= 0:
            continue
        if e.effect_id in disc and disc[e.effect_id]["status"] == "accepted":
            continue
        if e.scope_layer in ("team", "department", "region"):
            sl, su = e.scope_layer, int(e.scope_unit)
        else:
            sl, su = "executive", 0
        f = probe(int(e.cell), int(e.label), sl, su) or {}
        tested_scope = bool(f.get("tested_scope", False))
        tested_wide = bool(f.get("tested_wide", False))
        if not f.get("order_supported", True):
            r = "compression_loss"
        elif e.effect_id in disc and disc[e.effect_id]["status"] == "contested":
            r = "contradiction_mishandling"
        elif not (tested_scope or tested_wide):
            r = str(f.get("untested_reason") or "retrieval_failure")
        elif pt.attack_fraction(int(e.cell), sl, su) >= 0.3:
            r = "poison_contamination"
        elif tested_scope or sl == "executive":
            n, k = int(f.get("n_scope", 0)), int(f.get("k_scope", 0))
            if f.get("retrieved_n") is not None and int(f["retrieved_n"]) < e.n_min:
                r = "retrieval_failure"
            elif n < e.n_min:
                r = "statistical_power"
            elif f.get("votes_for") is not None:
                r = "insufficient_independent_support"
            elif k / max(n, 1) < e.delta * 0.35 or _attenuated(k, n, _true_cell_rate(world, e)):
                r = "model_reasoning_failure"
            elif e.valid_to is not None:
                r = "temporal_staleness"
            else:
                r = "statistical_power"   # the evidence was there and un-attenuated; the shared test at BH q did not accept it
        else:
            r = "routing_failure"   # tested only in a wider pool where the scoped effect is diluted
        reasons[r] = reasons.get(r, 0) + 1
    return reasons


def failure_reasons_hierarchy(world: World, m: dict[str, Any], hier, policy) -> dict[str, int]:
    """Classify why each hidden (cross-team/global) effect was NOT discovered at the root of a `Hierarchy`.

    Moved here from runner.py (integration): it reads `world.effects`, which only this module (and
    world/attacks/failures/routing) may do.  `hier` / `policy` are duck-typed hierarchy state.

    Categories follow the task's list: retrieval failure (n/a for hierarchy), aggregation loss (evidence never
    pooled where it was needed), compression loss (cell order or cell suppression removed it), routing failure
    (n/a), insufficient independent support, contradiction mishandling, privacy/security suppression, poison
    contamination, temporal staleness, model reasoning failure (perception noise erased the signal),
    hierarchy isolation (found at a lower layer but never forwarded), statistical power.
    """
    from .sketch import COL_K0, COL_N
    ci = cell_index()
    reasons: dict[str, int] = {}
    root = hier.root_node()
    disc = m["discovered"]
    for e in world.effects:
        if e.kind not in ("cross_team", "global") or (e.effect_id in disc and disc[e.effect_id]["status"] == "accepted"):
            continue
        order = int(ci.order_of(np.array([e.cell]))[0])
        cnt = root.cumulative.lookup(np.array([e.cell]))[0]
        n_root = int(cnt[COL_N]); k_root = int(cnt[COL_K0 + e.label])
        all_claims = [(node, c) for node in hier.nodes.values() for (cell, label, sign, scope), c in node.claims.items()
                      if cell == e.cell and label == e.label]
        statuses = {c.status for _, c in all_claims}
        found_below = any(c.status == "accepted" and node.unit_id != root.unit_id for node, c in all_claims)
        if e.effect_id in disc and disc[e.effect_id]["status"] == "contested":
            r = "contradiction_mishandling"
        elif "quarantined" in {c.status for node in hier.nodes.values() for c in node.quarantined if c.cell == e.cell and c.label == e.label}:
            r = "privacy_or_security_suppression"
        elif "rejected" in statuses:
            r = "contradiction_mishandling"
        elif found_below:
            r = "hierarchy_isolation"
        elif "proposed" in statuses:
            r = "insufficient_independent_support"
        elif order > policy.sketch_order and n_root == 0:
            r = "compression_loss"
        elif n_root < 0.6 * e.unit_counts.get("executive", 0):
            r = "compression_loss" if n_root > 0 else "aggregation_loss"
        elif n_root < e.n_min:
            r = "statistical_power"
        elif k_root / max(n_root, 1) < e.delta * 0.35 or _attenuated(k_root, n_root, _true_cell_rate(world, e)):
            r = "model_reasoning_failure"   # perception attenuated the rate the root sees by > 30 %
        elif len(root.child_cell_counts(e.cell)) < 2 and len(root.children) >= 2:
            r = "insufficient_independent_support"   # the two-child synthesis rule: one child holds all the evidence
        elif e.valid_to is not None:
            r = "temporal_staleness"
        else:
            r = "statistical_power"   # evidence present and un-attenuated; the shared test at BH q did not accept it
        reasons[r] = reasons.get(r, 0) + 1
    return reasons

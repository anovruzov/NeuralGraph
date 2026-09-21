"""The experiment suite.

Each function writes one JSONL of raw per-run metrics to artifacts/.  Nothing
here aggregates or scores; analysis.py does that, so the raw rows always
survive and can be re-analysed.

Calibrated hyper-parameters are loaded from artifacts/calibration.json and are
never changed per-experiment.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import models as M
from .evalm import evaluate
from .models import ALLOCATIONS, ANCHORS, allocation, tier_from_q
from .org import DEFAULT_FANIN, ENT, REGION, SITE, TEAM, USER
from .runner import ART, ARCHS, build_world, hier_cfg, run_arch
from .systems import (HierRunner, central_triage, chunked_long_context,
                      flat_rag, long_context,
                      map_reduce, oracle_retrieval, random_rank,
                      recursive_summary)

EVAL_SEEDS = (0, 1, 2, 3, 4)
SCALES = (2_000, 10_000, 50_000)


from .runner import CAL


def _hier(**kw):
    base = dict(triage_prior_weight=float(CAL["triage_prior_weight"]),
                question_frac=float(CAL["question_frac"]),
                w_dispersion=float(CAL.get("w_dispersion", 0.0)),
                w_synchrony=float(CAL.get("w_synchrony", 0.0)),
                w_attribution=float(CAL.get("w_attribution", 0.0)))
    base.update(kw)
    return hier_cfg(**base)


def _row(name: str, world, res, extra: Dict) -> Dict:
    m = evaluate(world.corpus, world.gold, res)
    return {"arch": name, "n_users": len(world.org.user_ids),
            "n_records": int(len(world.corpus.recs)),
            "n_gold": len(world.gold.discoverable), **extra, **m}


def _write(rows: List[Dict], fname: str) -> str:
    """No-op for rows already streamed; kept so callers read naturally."""
    return os.path.join(ART, fname)


class _Stream:
    """Append every row to disk as it is produced.

    Checkpointing requirement: a run that dies at 80% must not lose 80% of the
    work.  Every experiment appends immediately and the analysis reads the
    JSONL back, so partial runs are still usable.
    """

    def __init__(self, fname: str):
        self.path = os.path.join(ART, fname)
        self.fh = open(self.path, "a")

    def add(self, row: Dict) -> Dict:
        self.fh.write(json.dumps(row) + "\n")
        self.fh.flush()
        return row

    def close(self):
        self.fh.close()


def _run_named(name: str, world, alloc, seed: int, cfg_over=None):
    spec = ARCHS.get(name, {})
    kind = spec.get("kind", "hier")
    c = world.corpus
    kt = alloc[ENT]
    if kind == "flat_rag":
        return flat_rag(c, kt, seed, int(CAL["flat_budget"]),
                        near_miss=world.near_miss)
    if kind == "long_context":
        return long_context(c, kt, seed, near_miss=world.near_miss)
    if kind == "map_reduce":
        return map_reduce(c, alloc, seed, int(CAL["mr_budget"]),
                          ul=world.user_layer(alloc[USER], seed),
                          near_miss=world.near_miss)
    if kind == "recursive":
        return recursive_summary(c, alloc, seed,
                                 ul=world.user_layer(alloc[USER], seed),
                                 near_miss=world.near_miss)
    if kind == "chunked_ctx":
        return chunked_long_context(c, alloc, seed, near_miss=world.near_miss)
    if kind == "central_triage":
        return central_triage(c, alloc, seed,
                              ul=world.user_layer(alloc[USER], seed),
                              near_miss=world.near_miss,
                              kernel_ko_cap=int(CAL.get("ct_kernel_ko_cap", 0)))
    if kind == "oracle":
        return oracle_retrieval(c, alloc, seed,
                                ul=world.user_layer(alloc[USER], seed),
                                near_miss=world.near_miss)
    if kind == "randrank":
        base = map_reduce(c, alloc, seed, int(CAL["mr_budget"]),
                          ul=world.user_layer(alloc[USER], seed),
                          near_miss=world.near_miss)
        return random_rank(base, seed)
    over = dict(spec.get("cfg", {}))
    if cfg_over:
        over.update(cfg_over)
    return HierRunner(c, alloc, _hier(**over), seed=seed,
                      ul=world.user_layer(alloc[USER], seed),
                      near_miss=world.near_miss).run()


# ---------------------------------------------------------------------------
# E1 - baselines across scales
# ---------------------------------------------------------------------------

def e1_baselines(scales: Sequence[int] = SCALES, seeds=EVAL_SEEDS,
                 alloc_name: str = "back-loaded",
                 fname: str = "e1_baselines.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    alloc = allocation(alloc_name)
    for scale in scales:
        for seed in seeds:
            w = build_world(scale, seed)
            for a in ARCHS:
                t = time.time()
                res = _run_named(a, w, alloc, seed)
                r = _row(a, w, res, {"scale": scale, "seed": seed,
                                     "alloc": alloc_name,
                                     "runtime_s": round(time.time() - t, 2)})
                rows.append(_st.add(r))
                print(f"  {scale:6d}/{seed} {a:20s} AP={r['average_precision']:.4f} "
                      f"found={r['found_anywhere_in_register']:.2f} "
                      f"R@100={r['recall_at_100']:.3f} "
                      f"cu={r['compute_units']:.2e}", flush=True)
            w.clear_cache()
    _st.close()
    return rows


def e1c_more_seeds(scales: Sequence[int] = (2_000, 10_000),
                   seeds=(5, 6, 7, 8, 9),
                   alloc_name: str = "back-loaded",
                   fname: str = "e1_baselines.jsonl") -> List[Dict]:
    """A second block of seeds for the headline comparisons.

    With five paired seeds an exact sign test cannot go below p = 0.0625 even
    when the result is unanimous; that is a floor of the design, not of the
    effect.  Ten seeds take a unanimous result to p = 0.002.  Rows go into the
    same file so the analysis simply sees more seeds.
    """
    return e1_baselines(scales=scales, seeds=seeds, alloc_name=alloc_name,
                        fname=fname)


def e1b_extra(scales: Sequence[int] = SCALES, seeds=EVAL_SEEDS,
              archs=("A2_chunked_ctx", "B4_central_triage",
                     "I_mycelic_completion"),
              alloc_name: str = "back-loaded",
              fname: str = "e1b_extra.jsonl") -> List[Dict]:
    """Architectures added after the first E1 run; same worlds, same seeds,
    same calibrated knobs, so the rows are directly comparable."""
    rows = []
    _st = _Stream(fname)
    alloc = allocation(alloc_name)
    for scale in scales:
        for seed in seeds:
            w = build_world(scale, seed)
            for a in archs:
                t = time.time()
                res = _run_named(a, w, alloc, seed)
                r = _row(a, w, res, {"scale": scale, "seed": seed,
                                     "alloc": alloc_name,
                                     "runtime_s": round(time.time() - t, 2)})
                rows.append(_st.add(r))
                print(f"  {scale:6d}/{seed} {a:22s} "
                      f"AP={r['average_precision']:.4f} "
                      f"found={r['found_anywhere_in_register']:.2f} "
                      f"cov2={r['evidence_coverage_2links']:.2f} "
                      f"cu={r['compute_units']:.2e}", flush=True)
            w.clear_cache()
    _st.close()
    return rows


# ---------------------------------------------------------------------------
# E2 - ablations (one feature removed at a time from the full system)
# ---------------------------------------------------------------------------

ABLATIONS: Dict[str, Dict] = {
    "full": {},
    "-lineage": dict(lineage=False),
    "-independence": dict(independence=False),
    "-contradiction": dict(contradiction=False),
    "-temporal": dict(temporal=False),
    "-adaptive_routing": dict(adaptive_routing=False),
    "-questions": dict(questions=False),
    "-question_targeting": dict(question_targeting=False),
    "-downward_retrieval": dict(downward_retrieval=False, questions=False),
    "-cross_links": dict(cross_links=False),
    "-sketch_channel": dict(sketch_channel=False),
    "-adaptive_abstraction": dict(adaptive_abstraction=False),
    "-triage_prior": dict(triage_prior_weight=0.0),
    "+evidence_verification": dict(verify_evidence=True),
    "+source_dispersion": dict(w_dispersion=1.5),
    "+chain_completion": dict(chain_completion=True),
    "-foreign_filter": dict(foreign_only_evidence=False),
    "-synthesis_restriction": dict(restrict_synthesis_to_triage=False),
}


def e2_ablations(scale: int = 10_000, seeds=EVAL_SEEDS,
                 alloc_name: str = "back-loaded",
                 fname: str = "e2_ablations.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    alloc = allocation(alloc_name)
    for seed in seeds:
        w = build_world(scale, seed)
        for name, over in ABLATIONS.items():
            cfg = dict(downward_retrieval=True, questions=True,
                       cross_links=True)
            cfg.update(over)
            t = time.time()
            res = HierRunner(w.corpus, alloc, _hier(**cfg), seed=seed,
                             ul=w.user_layer(alloc[USER], seed),
                             near_miss=w.near_miss).run()
            r = _row("H" + name, w, res,
                     {"scale": scale, "seed": seed, "ablation": name,
                      "alloc": alloc_name, "runtime_s": round(time.time() - t, 2)})
            rows.append(_st.add(r))
            print(f"  {seed} {name:24s} AP={r['average_precision']:.4f} "
                  f"found={r['found_anywhere_in_register']:.2f} "
                  f"rare={r['rare_signal_recall']:.3f} "
                  f"lin={r['lineage_accuracy']:.2f} "
                  f"indep={r['independent_evidence_accuracy']:.2f} "
                  f"cu={r['compute_units']:.2e}", flush=True)
        w.clear_cache()
    _st.close()
    return rows


# ---------------------------------------------------------------------------
# E3 - compute allocation across levels
# ---------------------------------------------------------------------------

def e3_allocation(scale: int = 10_000, seeds=(0, 1, 2),
                  fname: str = "e3_allocation.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    for seed in seeds:
        w = build_world(scale, seed)
        for aname in ALLOCATIONS:
            alloc = allocation(aname)
            for arch in ("H_mycelic_full", "B2_map_reduce", "E_hier_lineage"):
                t = time.time()
                res = _run_named(arch, w, alloc, seed)
                r = _row(arch, w, res,
                         {"scale": scale, "seed": seed, "alloc": aname,
                          "tiers": "->".join(ALLOCATIONS[aname]),
                          "runtime_s": round(time.time() - t, 2)})
                rows.append(_st.add(r))
                print(f"  {seed} {aname:14s} {arch:16s} AP={r['average_precision']:.4f} "
                      f"found={r['found_anywhere_in_register']:.2f} "
                      f"cu={r['compute_units']:.2e}", flush=True)
        w.clear_cache()
    _st.close()
    return rows


def e3b_level_marginal(scale: int = 10_000, seeds=(0, 1, 2),
                       fname: str = "e3b_level_marginal.jsonl") -> List[Dict]:
    """Marginal value of capability AT EACH LEVEL, holding the rest fixed.

    Baseline: every level at 'small-7b'.  Then upgrade exactly one level to
    'frontier' and measure what changes.  This is the experiment that decides
    where compute should actually go.
    """
    rows = []
    _st = _Stream(fname)
    levels = ["user", "team", "dept", "site", "region", "enterprise"]
    for seed in seeds:
        w = build_world(scale, seed)
        for up in range(-1, 6):
            names = ["small-7b"] * 6
            if up >= 0:
                names[up] = "frontier"
            alloc = [ANCHORS[n] for n in names]
            for arch in ("H_mycelic_full", "B2_map_reduce"):
                t = time.time()
                res = _run_named(arch, w, alloc, seed)
                r = _row(arch, w, res,
                         {"scale": scale, "seed": seed,
                          "upgraded_level": ("none" if up < 0 else levels[up]),
                          "alloc": "+".join(names),
                          "runtime_s": round(time.time() - t, 2)})
                rows.append(_st.add(r))
                print(f"  {seed} upgrade={('none' if up<0 else levels[up]):11s} "
                      f"{arch:16s} AP={r['average_precision']:.4f} "
                      f"found={r['found_anywhere_in_register']:.2f} "
                      f"cu={r['compute_units']:.2e}", flush=True)
        w.clear_cache()
    _st.close()
    return rows


def e3c_q_sweep(scale: int = 10_000, seeds=(0, 1, 2),
                fname: str = "e3c_q_sweep.jsonl") -> List[Dict]:
    """Uniform capability sweep: the function from operator quality to
    architecture quality, which is the claim the study can actually make."""
    rows = []
    _st = _Stream(fname)
    for seed in seeds:
        w = build_world(scale, seed)
        for q in (0.10, 0.28, 0.44, 0.58, 0.72, 0.88, 1.00):
            alloc = [tier_from_q(q, name=f"q{q:.2f}",
                                 params_b=float(3.0 * 40.0 ** q),
                                 ctx=int(8000 * 128 ** q))] * 6
            for arch in ("H_mycelic_full", "B2_map_reduce", "E_hier_lineage"):
                res = _run_named(arch, w, alloc, seed)
                r = _row(arch, w, res, {"scale": scale, "seed": seed, "q": q,
                                        "alloc": f"uniform-q{q:.2f}"})
                rows.append(_st.add(r))
                print(f"  {seed} q={q:.2f} {arch:16s} AP={r['average_precision']:.4f} "
                      f"found={r['found_anywhere_in_register']:.2f} "
                      f"cu={r['compute_units']:.2e}", flush=True)
        w.clear_cache()
    _st.close()
    return rows


# ---------------------------------------------------------------------------
# E4 - organisational fan-in
# ---------------------------------------------------------------------------

def e4_fanin(scale: int = 10_000, seeds=(0, 1, 2),
             fname: str = "e4_fanin.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    alloc = allocation("back-loaded")
    for seed in seeds:
        for team_size in (6, 8, 10, 12, 15):
            fan = dict(DEFAULT_FANIN)
            m, s_, lo, hi = fan[TEAM]
            fan[TEAM] = (float(team_size), s_, lo, hi)
            w = build_world(scale, seed, org_kwargs={"fanin": fan})
            for arch in ("H_mycelic_full", "E_hier_lineage", "B2_map_reduce"):
                res = _run_named(arch, w, alloc, seed)
                r = _row(arch, w, res,
                         {"scale": scale, "seed": seed, "team_size": team_size,
                          "n_teams": len(w.org.levels[TEAM]),
                          "alloc": "back-loaded"})
                rows.append(_st.add(r))
                print(f"  {seed} team={team_size:2d} {arch:16s} "
                      f"AP={r['average_precision']:.4f} "
                      f"found={r['found_anywhere_in_register']:.2f} "
                      f"cu={r['compute_units']:.2e}", flush=True)
            w.clear_cache()
    _st.close()
    return rows


def e4b_dept_fanin(scale: int = 10_000, seeds=(0, 1, 2),
                   fname: str = "e4b_dept_fanin.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    alloc = allocation("back-loaded")
    from .org import DEPT
    for seed in seeds:
        for teams_per_dept in (4, 7, 10, 14):
            fan = dict(DEFAULT_FANIN)
            m, s_, lo, hi = fan[DEPT]
            fan[DEPT] = (float(teams_per_dept), s_, lo, hi)
            w = build_world(scale, seed, org_kwargs={"fanin": fan})
            for arch in ("H_mycelic_full", "E_hier_lineage"):
                res = _run_named(arch, w, alloc, seed)
                rows.append(_st.add(_row(arch, w, res,
                                 {"scale": scale, "seed": seed,
                                  "teams_per_dept": teams_per_dept,
                                  "n_depts": len(w.org.levels[DEPT]),
                                  "alloc": "back-loaded"})))
                print(f"  {seed} t/dept={teams_per_dept:2d} {arch:16s} "
                      f"AP={rows[-1]['average_precision']:.4f}", flush=True)
            w.clear_cache()
    _st.close()
    return rows


# ---------------------------------------------------------------------------
# E5 - adversarial conditions
# ---------------------------------------------------------------------------

ADVERSARIAL: Dict[str, Dict] = {
    "clean":               {},
    "high_cross_site_noise": {"corpus": {"p_cross_site_entity": 0.15}},
    "decoy_heavy":         {"corpus": {"decoy_ratio": 3.0}},
    "duplicate_flood":     {"corpus": {"p_dup": 0.30}},
    "stale_flood":         {"corpus": {"p_stale": 0.25}},
    "false_report_flood":  {"corpus": {"p_false": 0.15}},
    "rare_only":           {"corpus": {"rare_fraction": 1.0}},
    "malicious_nodes_5pct": {"cfg": {"malicious_frac": 0.05}},
    "unavailable_sites_20pct": {"cfg": {"unavailable_frac": 0.20}},
    "approx_index_bloom":  {"cfg": {"bloom_fp": 0.02}},
    "extreme_imbalance":   {"org": {"n_regions": 3}},
}


def e5_adversarial(scale: int = 10_000, seeds=(0, 1, 2),
                   fname: str = "e5_adversarial.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    alloc = allocation("back-loaded")
    for seed in seeds:
        for cond, spec in ADVERSARIAL.items():
            w = build_world(scale, seed,
                            corpus_cfg=spec.get("corpus"),
                            org_kwargs=spec.get("org"))
            for arch in ("H_mycelic_full", "B2_map_reduce", "B_long_context",
                         "E_hier_lineage"):
                res = _run_named(arch, w, alloc, seed,
                                 cfg_over=spec.get("cfg"))
                r = _row(arch, w, res,
                         {"scale": scale, "seed": seed, "condition": cond,
                          "alloc": "back-loaded"})
                rows.append(_st.add(r))
                print(f"  {seed} {cond:24s} {arch:16s} "
                      f"AP={r['average_precision']:.4f} "
                      f"FDR={r['false_discovery_rate']:.3f} "
                      f"decoy={r['decoy_acceptance_all']:.3f} "
                      f"dupErr={r['dup_inflation_support_error']:.2f}",
                      flush=True)
            w.clear_cache()
    _st.close()
    return rows


# ---------------------------------------------------------------------------
# E6 - cost / quality frontier
# ---------------------------------------------------------------------------

def e6_frontier(scale: int = 10_000, seeds=(0, 1, 2),
                fname: str = "e6_frontier.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    alloc = allocation("back-loaded")
    for seed in seeds:
        w = build_world(scale, seed)
        for qf in (0.0, 0.05, 0.15, 0.35, 0.55, 1.0):
            cfg = dict(downward_retrieval=True, questions=qf > 0.0,
                       cross_links=True, question_frac=max(qf, 1e-6))
            res = HierRunner(w.corpus, alloc, _hier(**cfg), seed=seed,
                             ul=w.user_layer(alloc[USER], seed),
                             near_miss=w.near_miss).run()
            rows.append(_st.add(_row("H_frontier", w, res,
                             {"scale": scale, "seed": seed, "question_frac": qf,
                              "alloc": "back-loaded", "knob": "question_frac"})))
            print(f"  {seed} qfrac={qf:.2f} AP={rows[-1]['average_precision']:.4f} "
                  f"found={rows[-1]['found_anywhere_in_register']:.2f} "
                  f"cu={rows[-1]['compute_units']:.2e}", flush=True)
        for b in (300, 900, 2500, 8000, 20000):
            res = map_reduce(w.corpus, alloc, seed, b,
                             ul=w.user_layer(alloc[USER], seed),
                             near_miss=w.near_miss)
            rows.append(_st.add(_row("B2_frontier", w, res,
                             {"scale": scale, "seed": seed, "mr_budget": b,
                              "alloc": "back-loaded", "knob": "mr_budget"})))
            print(f"  {seed} mr_budget={b} AP={rows[-1]['average_precision']:.4f} "
                  f"cu={rows[-1]['compute_units']:.2e}", flush=True)
        w.clear_cache()
    _st.close()
    return rows


# ---------------------------------------------------------------------------
# E7 - capability-shape sensitivity
# ---------------------------------------------------------------------------

def e7_shape(scale: int = 10_000, seeds=(0, 1, 2),
             fname: str = "e7_shape.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    for shape in ("convex", "linear", "concave"):
        M.HARD_SHAPE = shape
        M.ANCHORS.update({k: M.anchor(k) for k in M.ANCHOR_Q})
        for seed in seeds:
            w = build_world(scale, seed)
            for aname in ("flat-small", "naive-ladder", "back-loaded",
                          "kernel-only", "two-step", "front-loaded"):
                alloc = allocation(aname)
                for arch in ("H_mycelic_full", "B2_map_reduce"):
                    res = _run_named(arch, w, alloc, seed)
                    rows.append(_st.add(_row(arch, w, res,
                                             {"scale": scale, "seed": seed,
                                              "shape": shape, "alloc": aname})))
                    print(f"  {shape:8s} {seed} {aname:14s} {arch:16s} "
                          f"AP={rows[-1]['average_precision']:.4f} "
                          f"cu={rows[-1]['compute_units']:.2e}", flush=True)
            w.clear_cache()
    M.HARD_SHAPE = "convex"
    M.ANCHORS.update({k: M.anchor(k) for k in M.ANCHOR_Q})
    _st.close()
    return rows


# ---------------------------------------------------------------------------
# E8 - semantic cross-links vs the strict org tree
# ---------------------------------------------------------------------------

def e8_crosslinks(scales=(2_000, 10_000, 50_000), seeds=(0, 1, 2),
                  fname: str = "e8_crosslinks.jsonl") -> List[Dict]:
    rows = []
    _st = _Stream(fname)
    alloc = allocation("back-loaded")
    for scale in scales:
        for seed in seeds:
            w = build_world(scale, seed)
            for xl in (False, True):
                for deg in ((4,) if xl else (0,)):
                    cfg = dict(downward_retrieval=True, questions=True,
                               cross_links=xl, cross_link_degree=max(1, deg))
                    res = HierRunner(w.corpus, alloc, _hier(**cfg), seed=seed,
                                     ul=w.user_layer(alloc[USER], seed),
                                     near_miss=w.near_miss).run()
                    rows.append(_st.add(_row("H", w, res,
                                     {"scale": scale, "seed": seed,
                                      "cross_links": xl, "degree": deg,
                                      "alloc": "back-loaded"})))
                    print(f"  {scale} {seed} xlinks={xl} "
                          f"AP={rows[-1]['average_precision']:.4f} "
                          f"found={rows[-1]['found_anywhere_in_register']:.2f} "
                          f"cu={rows[-1]['compute_units']:.2e}", flush=True)
            w.clear_cache()
    _st.close()
    return rows


def e1d_b4_capped(scales: Sequence[int] = SCALES, seeds=EVAL_SEEDS,
                  alloc_name: str = "back-loaded",
                  fname: str = "e1d_b4_capped.jsonl") -> List[Dict]:
    """The centralised-triage control WITH its calibrated evidence budget.

    Kept in a separate file from the uncapped run so the two are reported side
    by side: the difference between them is exactly the value of the
    evidence-selection discipline that the hierarchy gets for free from its
    propagation budget.
    """
    rows = []
    _st = _Stream(fname)
    alloc = allocation(alloc_name)
    cap = int(CAL.get("ct_kernel_ko_cap") or 0)
    for scale in scales:
        for seed in seeds:
            w = build_world(scale, seed)
            res = central_triage(w.corpus, alloc, seed,
                                 ul=w.user_layer(alloc[USER], seed),
                                 near_miss=w.near_miss, kernel_ko_cap=cap)
            r = _row("B4_central_triage_capped", w, res,
                     {"scale": scale, "seed": seed, "alloc": alloc_name,
                      "kernel_ko_cap": cap})
            rows.append(_st.add(r))
            print(f"  {scale:6d}/{seed} B4_capped(cap={cap}) "
                  f"AP={r['average_precision']:.4f} "
                  f"found={r['found_anywhere_in_register']:.3f} "
                  f"cu={r['compute_units']:.2e}", flush=True)
            w.clear_cache()
    _st.close()
    return rows


def e11_verified(scales: Sequence[int] = SCALES, seeds=EVAL_SEEDS,
                 archs=("J_mycelic_verified",),
                 alloc_name: str = "back-loaded",
                 fname: str = "e1b_extra.jsonl") -> List[Dict]:
    """The evidence-verification architecture, on the same worlds and seeds as
    E1 so the rows are directly comparable."""
    return e1b_extra(scales=scales, seeds=seeds, archs=archs,
                     alloc_name=alloc_name, fname=fname)


def e10_scale_trend(scales: Sequence[int] = (100_000,), seeds=(0, 1, 2),
                    archs=("A_flat_rag", "B_long_context", "B2_map_reduce",
                           "A2_chunked_ctx", "E_hier_lineage",
                           "G_hier_questions", "H_mycelic_full",
                           "J_mycelic_verified", "Y_oracle_retrieval"),
                    alloc_name: str = "back-loaded",
                    fname: str = "e10_scale_trend.jsonl") -> List[Dict]:
    """One scale beyond the brief, to test whether the ordering at 50k is the
    end of the story or a crossing point.

    The centralised long-context baseline reads a fixed 1M tokens whatever the
    enterprise size, so its coverage falls as 1/N by construction; the question
    is whether the hierarchy's falls more slowly in practice.  Measured, not
    extrapolated.
    """
    rows = []
    _st = _Stream(fname)
    alloc = allocation(alloc_name)
    for scale in scales:
        for seed in seeds:
            w = build_world(scale, seed)
            for a in archs:
                t = time.time()
                res = _run_named(a, w, alloc, seed)
                r = _row(a, w, res, {"scale": scale, "seed": seed,
                                     "alloc": alloc_name,
                                     "runtime_s": round(time.time() - t, 2)})
                rows.append(_st.add(r))
                print(f"  {scale:7d}/{seed} {a:22s} "
                      f"AP={r['average_precision']:.4f} "
                      f"found={r['found_anywhere_in_register']:.3f} "
                      f"cov2={r['evidence_coverage_2links']:.3f} "
                      f"rare={r['rare_signal_recall']:.3f} "
                      f"cu={r['compute_units']:.2e} "
                      f"({time.time()-t:.0f}s)", flush=True)
            w.clear_cache()
    _st.close()
    return rows


def e9_privacy(scales: Sequence[int] = SCALES, seeds=(0, 1),
               alloc_name: str = "back-loaded",
               fname: str = "e9_privacy.jsonl") -> List[Dict]:
    """Privacy / propagation-volume accounting, separated into three things
    that are usually conflated:

      raw text leaving the owning agent   - the actual confidentiality cost
      extracted claims leaving the agent  - abstracted, no surface text
      index/sketch metadata leaving it    - entity + predicate bitmask only

    Deterministic enough that two seeds suffice.
    """
    rows = []
    _st = _Stream(fname)
    alloc = allocation(alloc_name)
    for scale in scales:
        for seed in seeds:
            w = build_world(scale, seed)
            for a in ARCHS:
                res = _run_named(a, w, alloc, seed)
                r = _row(a, w, res, {"scale": scale, "seed": seed,
                                     "alloc": alloc_name})
                rows.append(_st.add(r))
                print(f"  {scale:6d}/{seed} {a:22s} "
                      f"raw={r['raw_text_exposure_fraction']:.4f} "
                      f"claims={r['claim_exposure_fraction']:.4f} "
                      f"sketch={r['sketch_entries_leaving_node']:.3e} "
                      f"found={r['found_anywhere_in_register']:.2f}", flush=True)
            w.clear_cache()
    _st.close()
    return rows


ALL = {
    "e1": e1_baselines, "e1b": e1b_extra, "e1c": e1c_more_seeds, "e2": e2_ablations, "e3": e3_allocation,
    "e3b": e3b_level_marginal, "e3c": e3c_q_sweep, "e4": e4_fanin,
    "e4b": e4b_dept_fanin, "e5": e5_adversarial, "e6": e6_frontier,
    "e7": e7_shape, "e8": e8_crosslinks, "e9": e9_privacy, "e10": e10_scale_trend,
    "e11": e11_verified, "e1d": e1d_b4_capped,
}


if __name__ == "__main__":
    import sys
    which = sys.argv[1:] or ["e1"]
    for w_ in which:
        print(f"=== {w_} ===", flush=True)
        t0 = time.time()
        ALL[w_]()
        print(f"=== {w_} done in {time.time()-t0:.0f}s ===", flush=True)

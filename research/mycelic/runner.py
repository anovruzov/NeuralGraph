"""Experiment driver: builds the world once per (scale, seed), then runs every
architecture against it and writes raw per-run metrics to JSONL.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import models as M
from .corpus import Corpus, build_corpus, make_gold
from .evalm import evaluate
from .models import ALLOCATIONS, ANCHORS, Tier, allocation, uniform_alloc
from .ops import _near_miss_map, stem_rep_map
from .org import ENT, Org, USER, build_org
from .systems import (HierConfig, HierRunner, RunResult, flat_rag, long_context,
                      central_triage, map_reduce, oracle_retrieval,
                      random_rank,
                      recursive_summary, user_extract)

ART = os.path.join(os.path.dirname(__file__), "artifacts")
os.makedirs(ART, exist_ok=True)


@dataclass
class World:
    org: Org
    corpus: Corpus
    gold: object
    near_miss: np.ndarray
    stem: np.ndarray
    seed: int
    scale: int
    _ul_cache: Dict[str, object] = field(default_factory=dict)

    def user_layer(self, tier: Tier, seed: int):
        key = f"{tier.name}:{seed}"
        if key not in self._ul_cache:
            rng = np.random.default_rng(50_000 + seed * 13 + int(tier.q * 1000))
            self._ul_cache[key] = user_extract(self.corpus, tier, rng,
                                               near_miss=self.near_miss)
        return self._ul_cache[key]

    def clear_cache(self):
        self._ul_cache.clear()


def build_world(scale: int, seed: int, corpus_cfg: Optional[Dict] = None,
                org_kwargs: Optional[Dict] = None) -> World:
    org = build_org(scale, seed=seed, **(org_kwargs or {}))
    cp = build_corpus(org, seed=seed, cfg=corpus_cfg)
    gold = make_gold(cp)
    nm = _near_miss_map(cp, np.random.default_rng(99 + seed))
    st = stem_rep_map(cp)
    return World(org=org, corpus=cp, gold=gold, near_miss=nm, stem=st,
                 seed=seed, scale=scale)


# ---------------------------------------------------------------------------
# Standard architecture registry
# ---------------------------------------------------------------------------

BASE_BUDGETS = (6, 18, 42, 90, 130)


def hier_cfg(**kw) -> HierConfig:
    c = HierConfig(budgets=BASE_BUDGETS)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


ARCHS: Dict[str, Dict] = {
    # --- centralised baselines ---
    "A_flat_rag":        {"kind": "flat_rag"},
    "B_long_context":    {"kind": "long_context"},
    "B2_map_reduce":     {"kind": "map_reduce"},
    "B4_central_triage": {"kind": "central_triage"},
    "C_recursive_sum":   {"kind": "recursive"},
    # --- hierarchy family ---
    "D_hier_nolineage":  {"kind": "hier", "cfg": dict(
        lineage=False, independence=False, contradiction=False,
        downward_retrieval=False, questions=False, cross_links=False)},
    "E_hier_lineage":    {"kind": "hier", "cfg": dict(
        downward_retrieval=False, questions=False, cross_links=False)},
    "F_hier_retrieval":  {"kind": "hier", "cfg": dict(
        downward_retrieval=True, questions=False, cross_links=False)},
    "G_hier_questions":  {"kind": "hier", "cfg": dict(
        downward_retrieval=True, questions=True, cross_links=False)},
    "H_mycelic_full":    {"kind": "hier", "cfg": dict(
        downward_retrieval=True, questions=True, cross_links=True)},
    "I_mycelic_completion": {"kind": "hier", "cfg": dict(
        downward_retrieval=True, questions=True, cross_links=True,
        chain_completion=True)},
    # --- reference controls, clearly not deployable systems ---
    "Y_oracle_retrieval": {"kind": "oracle"},
    "Z_random_rank":      {"kind": "randrank"},
}


def run_arch(name: str, world: World, alloc: List[Tier], seed: int,
             flat_budget: Optional[int] = None,
             mr_budget: int = 900,
             cfg_over: Optional[Dict] = None) -> RunResult:
    spec = ARCHS[name] if name in ARCHS else cfg_over or {}
    kind = spec.get("kind", "hier")
    c = world.corpus
    kt = alloc[ENT]
    if kind == "flat_rag":
        budget = flat_budget if flat_budget is not None else min(kt.ctx, 120_000)
        return flat_rag(c, kt, seed, budget, near_miss=world.near_miss)
    if kind == "long_context":
        return long_context(c, kt, seed, near_miss=world.near_miss)
    if kind == "map_reduce":
        return map_reduce(c, alloc, seed, mr_budget,
                          ul=world.user_layer(alloc[USER], seed),
                          near_miss=world.near_miss)
    if kind == "central_triage":
        return central_triage(c, alloc, seed,
                              ul=world.user_layer(alloc[USER], seed),
                              near_miss=world.near_miss)
    if kind == "oracle":
        return oracle_retrieval(c, alloc, seed,
                                ul=world.user_layer(alloc[USER], seed),
                                near_miss=world.near_miss)
    if kind == "randrank":
        base = map_reduce(c, alloc, seed, mr_budget,
                          ul=world.user_layer(alloc[USER], seed),
                          near_miss=world.near_miss)
        return random_rank(base, seed)
    if kind == "recursive":
        return recursive_summary(c, alloc, seed,
                                 ul=world.user_layer(alloc[USER], seed),
                                 near_miss=world.near_miss)
    over = dict(spec.get("cfg", {}))
    if cfg_over:
        over.update(cfg_over.get("cfg", cfg_over))
    cfg = hier_cfg(**over)
    return HierRunner(c, alloc, cfg, seed=seed,
                      ul=world.user_layer(alloc[USER], seed),
                      near_miss=world.near_miss).run()


def run_matrix(scales: Sequence[int], seeds: Sequence[int],
               archs: Sequence[str], alloc_name: str = "back-loaded",
               out: str = "baselines.jsonl", corpus_cfg: Optional[Dict] = None,
               extra_tags: Optional[Dict] = None,
               verbose: bool = True) -> List[Dict]:
    rows: List[Dict] = []
    path = os.path.join(ART, out)
    with open(path, "a") as fh:
        for scale in scales:
            for seed in seeds:
                t0 = time.time()
                w = build_world(scale, seed, corpus_cfg=corpus_cfg)
                alloc = allocation(alloc_name)
                for a in archs:
                    t1 = time.time()
                    res = run_arch(a, w, alloc, seed)
                    met = evaluate(w.corpus, w.gold, res)
                    row = {"arch": a, "scale": scale, "n_users": len(w.org.user_ids),
                           "n_records": int(len(w.corpus.recs)), "seed": seed,
                           "alloc": alloc_name, "runtime_s": round(time.time() - t1, 2),
                           **(extra_tags or {}), **met}
                    fh.write(json.dumps(row) + "\n")
                    rows.append(row)
                    if verbose:
                        print(f"  {a:20s} AP={met['average_precision']:.4f} "
                              f"Rp={met['r_precision']:.3f} "
                              f"R@100={met['recall_at_100']:.3f} "
                              f"found={met['found_anywhere_in_register']:.2f} "
                              f"rare={met['rare_signal_recall']:.3f} "
                              f"cu={met['compute_units']:.2e} "
                              f"({time.time()-t1:.1f}s)", flush=True)
                w.clear_cache()
                if verbose:
                    print(f"scale={scale} seed={seed} done in "
                          f"{time.time()-t0:.1f}s", flush=True)
    return rows


def summarise(rows: List[Dict], keys: Sequence[str],
              group: str = "arch") -> Dict[str, Dict[str, Tuple[float, float]]]:
    out: Dict[str, Dict[str, Tuple[float, float]]] = {}
    gs = sorted({r[group] for r in rows})
    for g in gs:
        sub = [r for r in rows if r[group] == g]
        out[g] = {k: (float(np.mean([r[k] for r in sub])),
                      float(np.std([r[k] for r in sub], ddof=1))
                      if len(sub) > 1 else 0.0) for k in keys}
    return out


if __name__ == "__main__":
    import sys
    scales = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["400"])]
    seeds = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["0"])]
    rows = run_matrix(scales, seeds, list(ARCHS), out="smoke.jsonl")
    from .evalm import HEADLINE
    s = summarise(rows, ["discovery_recall", "discovery_precision",
                         "rare_signal_recall", "decoy_acceptance_all",
                         "compute_units"])
    for k, v in s.items():
        print(k, {kk: round(vv[0], 3) for kk, vv in v.items()})

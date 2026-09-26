"""Paired A/B of one variant against a base architecture on identical worlds.

This is the measurement every hypothesis has to pass before it is taken
further.  It runs the base and the variant on the same (scale, seed) worlds,
so the seed-to-seed variance that dominates unpaired comparisons cancels, and
reports the paired difference with a bootstrap interval and an exact sign
test for the metrics that matter on the Pareto frontier.

    # a configuration variant of the hierarchy
    python3 -m research.mycelic.quick_paired --tag h07 \\
        --cfg '{"sketch_min_support": 1}'

    # a structurally new architecture registered in runner.ARCHS
    python3 -m research.mycelic.quick_paired --tag v1 --variant-arch V1_something

    # against a different base, more seeds
    python3 -m research.mycelic.quick_paired --tag x --cfg '{...}' \\
        --base G_hier_questions --scale 10000 --seeds 0,1,2,3,4

Rows go to artifacts/quick_<tag>.jsonl (never into the headline files).
Calibrated knobs are applied to base and variant alike; a variant overrides
only what it names.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Dict, List, Optional, Sequence

import numpy as np

from .analysis import boot_ci, sign_test
from .evalm import evaluate
from .models import allocation
from .runner import ARCHS, ART, build_world, run_arch

METRICS = [
    ("found_anywhere_in_register", "found", True),
    ("average_precision", "AP", True),
    ("recall_at_100", "R@100", True),
    ("rare_signal_recall", "rare recall", True),
    ("evidence_coverage_2links", "evidence cov.", True),
    ("false_discovery_rate", "FDR", False),
    ("decoy_acceptance_all", "decoy acc.", False),
    ("independent_evidence_accuracy", "indep. acc.", True),
    ("lineage_accuracy", "lineage", True),
    ("contradiction_f1", "contra F1", True),
    ("compute_units", "compute", False),
    ("inference_calls", "calls", False),
    ("raw_text_exposure_fraction", "raw text out", False),
    ("claim_exposure_fraction", "claims out", False),
]


def run_pair(base: str, variant: str, cfg_over: Optional[Dict], scale: int,
             seeds: Sequence[int], tag: str, alloc_name: str = "back-loaded",
             verbose: bool = True, ranker_ab: bool = False) -> List[Dict]:
    rows: List[Dict] = []
    path = os.path.join(ART, f"quick_{tag}.jsonl")
    alloc = allocation(alloc_name)
    with open(path, "a") as fh:
        for seed in seeds:
            t0 = time.time()
            w = build_world(scale, seed)
            for side, (label, arch, over) in enumerate(((base, base, None),
                                                        (variant, variant, cfg_over))):
                t1 = time.time()
                if ranker_ab:
                    # the variant may be the base itself, so switch on the
                    # SIDE, not the label (which is why the first batch of
                    # A/B rows came back identical)
                    from .runner import apply_ranker
                    apply_ranker(side == 1)
                if over is not None and arch in ARCHS:
                    res = run_arch(arch, w, alloc, seed, cfg_over={"cfg": over})
                else:
                    res = run_arch(arch, w, alloc, seed)
                met = evaluate(w.corpus, w.gold, res)
                from . import runner as _rn
                row = {"arch": label, "variant_of": arch, "cfg_over": over,
                       "scale": scale, "seed": seed, "tag": tag,
                       # which ranker this row ran with, so ON/OFF is logged
                       # rather than inferred from the metrics
                       "ranker": ("forced-on" if _rn.FORCE_RANKER else "forced-off"
                                  if _rn.FORCE_RANKER is not None else "cal"),
                       "ranker_l2": (_rn.CAL.get("ranker") or {}).get("l2"),
                       "runtime_s": round(time.time() - t1, 1), **met}
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                rows.append(row)
                if verbose:
                    print(f"  seed {seed} {label:24s} found={met['found_anywhere_in_register']:.3f} "
                          f"AP={met['average_precision']:.4f} "
                          f"cov={met['evidence_coverage_2links']:.3f} "
                          f"rare={met['rare_signal_recall']:.3f} "
                          f"cu={met['compute_units']:.2e} ({time.time() - t1:.0f}s)",
                          flush=True)
            w.clear_cache()
    return rows


def report(rows: List[Dict], base: str, variant: str) -> str:
    kb = {r["seed"]: r for r in rows if r["arch"] == base}
    kv = {r["seed"]: r for r in rows if r["arch"] == variant}
    seeds = sorted(set(kb) & set(kv))
    lines = [f"**{variant} vs {base}** — {len(seeds)} paired seeds", "",
             "| metric | base | variant | Δ | 95% CI | better on | sign p | verdict |",
             "|---|---:|---:|---:|---|---:|---:|---|"]
    for key, label, higher_better in METRICS:
        b = np.array([kb[s][key] for s in seeds], dtype=float)
        v = np.array([kv[s][key] for s in seeds], dtype=float)
        d = v - b
        m, lo, hi = boot_ci(d)
        k, n, p = sign_test(d.tolist())
        wins = k if higher_better else (n - k)
        if abs(m) < 1e-12:
            verdict = "same"
        elif (lo > 0 and higher_better) or (hi < 0 and not higher_better):
            verdict = "**better**"
        elif (hi < 0 and higher_better) or (lo > 0 and not higher_better):
            verdict = "**worse**"
        else:
            verdict = "inside noise"
        fmt = "{:.2e}" if key in ("compute_units", "inference_calls") else "{:.4f}"
        lines.append(f"| {label} | {fmt.format(b.mean())} | {fmt.format(v.mean())} | "
                     f"{'+' if m >= 0 else ''}{fmt.format(m)} | "
                     f"[{'+' if lo >= 0 else ''}{fmt.format(lo)}, "
                     f"{'+' if hi >= 0 else ''}{fmt.format(hi)}] | "
                     f"{wins}/{n} | {p:.3f} | {verdict} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--base", default="H_mycelic_full")
    ap.add_argument("--variant-arch", default=None,
                    help="an ARCHS key; defaults to the base with --cfg applied")
    ap.add_argument("--cfg", default=None, help="JSON HierConfig overrides")
    ap.add_argument("--scale", type=int, default=10_000)
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--alloc", default="back-loaded")
    ap.add_argument("--ranker", default="cal",
                    help="cal (use calibration.json's ranker if present) | off | "
                         "ab (base without, variant with)")
    a = ap.parse_args()
    from .runner import apply_ranker
    apply_ranker({"off": False, "on": True}.get(a.ranker, None))
    over = json.loads(a.cfg) if a.cfg else None
    variant = a.variant_arch or a.base
    label = a.variant_arch or f"{a.base}+{a.tag}"
    if a.ranker == "ab" and over is None:
        over = {}      # same config; the ranker is the only difference
    seeds = [int(x) for x in a.seeds.split(",")]
    rows = run_pair(a.base, variant, over, a.scale, seeds, a.tag, a.alloc,
                    ranker_ab=(a.ranker == "ab"))
    # relabel the variant rows so the report can tell them apart
    for i, r in enumerate(rows):
        if i % 2 == 1:
            r["arch"] = label
    print()
    print(report(rows, a.base, label))


if __name__ == "__main__":
    main()

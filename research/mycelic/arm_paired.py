"""Multi-arm paired runs on identical worlds, each arm with its own ranker.

quick_paired compares one variant against one base and can only switch the
ranker on or off.  Ranker selection needs to compare two fitted rankers on
the same worlds, and the question-budget sweep needs several arms at once,
so this runs N arms per (scale, seed) world and reports every arm against
the first one with the same bootstrap / sign-test table.

    python3 -m research.mycelic.arm_paired --tag qf_v3 --scale 10000 --seeds 0,1,2,3,4 \\
        --arm 'qf0.50|H_mycelic_full|artifacts/calibration.v3.json|{"question_frac":0.5,"batched_descent":true}' \\
        --arm 'qf0.65|H_mycelic_full|artifacts/calibration.v3.json|{"question_frac":0.65,"batched_descent":true}'

An arm is  label | architecture | ranker | HierConfig overrides (JSON or empty).
The ranker field is  none  (hand-set logistic),  cal  (whatever
calibration.json holds, with its per-architecture adoption), or the path of
a calibration file whose "ranker" entry is forced on for that arm.  Rows go
to artifacts/quick_<tag>.jsonl with arch = label, never into the headline
files.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple

from .evalm import evaluate
from .models import allocation
from .quick_paired import report
from .runner import ARCHS, ART, CAL, build_world, run_arch, apply_ranker

Arm = Tuple[str, str, str, Optional[Dict]]


def parse_arm(spec: str) -> Arm:
    parts = spec.split("|", 3)
    if len(parts) < 3:
        raise SystemExit(f"arm needs label|arch|ranker[|cfg-json]: {spec!r}")
    label, arch, ranker = parts[0].strip(), parts[1].strip(), parts[2].strip()
    cfg = json.loads(parts[3]) if len(parts) == 4 and parts[3].strip() else None
    if arch not in ARCHS:
        raise SystemExit(f"unknown architecture {arch!r}")
    return label, arch, ranker, cfg


def _set_ranker(spec: str, original: Optional[Dict]) -> None:
    """Point runner.CAL['ranker'] at the ranker this arm asks for."""
    if spec == "none":
        apply_ranker(False)
    elif spec == "cal":
        if original is None:
            CAL.pop("ranker", None)
        else:
            CAL["ranker"] = original
        apply_ranker(None)
    else:
        path = spec if os.path.isabs(spec) else os.path.join(os.path.dirname(ART), spec)
        if not os.path.exists(path):
            path = os.path.join(ART, os.path.basename(spec))
        CAL["ranker"] = json.load(open(path))["ranker"]
        apply_ranker(True)


def run_arms(arms: Sequence[Arm], scale: int, seeds: Sequence[int], tag: str,
             alloc_name: str = "back-loaded", verbose: bool = True) -> List[Dict]:
    rows: List[Dict] = []
    path = os.path.join(ART, f"quick_{tag}.jsonl")
    alloc = allocation(alloc_name)
    original = copy.deepcopy(CAL.get("ranker"))
    with open(path, "a") as fh:
        for seed in seeds:
            w = build_world(scale, seed)
            for label, arch, ranker, cfg in arms:
                t1 = time.time()
                _set_ranker(ranker, original)
                if cfg:
                    res = run_arch(arch, w, alloc, seed, cfg_over={"cfg": cfg})
                else:
                    res = run_arch(arch, w, alloc, seed)
                met = evaluate(w.corpus, w.gold, res)
                row = {"arch": label, "variant_of": arch, "cfg_over": cfg, "ranker": ranker,
                       "scale": scale, "seed": seed, "tag": tag,
                       "runtime_s": round(time.time() - t1, 1), **met}
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                rows.append(row)
                if verbose:
                    print(f"  seed {seed} {label:24s} found={met['found_anywhere_in_register']:.3f} "
                          f"AP={met['average_precision']:.4f} "
                          f"cov={met['evidence_coverage_2links']:.3f} "
                          f"rare={met['rare_signal_recall']:.3f} "
                          f"decoy={met['decoy_acceptance_all']:.3f} "
                          f"cu={met['compute_units']:.2e} calls={met['inference_calls']:.2e} "
                          f"({time.time() - t1:.0f}s)", flush=True)
            w.clear_cache()
    _set_ranker("cal", original)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--arm", action="append", required=True,
                    help="label|arch|none,cal,<calibration file>|{HierConfig overrides}")
    ap.add_argument("--scale", type=int, default=10_000)
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--alloc", default="back-loaded")
    a = ap.parse_args()
    arms = [parse_arm(s) for s in a.arm]
    seeds = [int(x) for x in a.seeds.split(",")]
    rows = run_arms(arms, a.scale, seeds, a.tag, a.alloc)
    base = arms[0][0]
    for label, _, _, _ in arms[1:]:
        print()
        print(report(rows, base, label))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fast synthetic Continual Discovery mechanism probe.

Outputs one CSV row per (agent_scale, seed, strategy).
The simulator is intentionally synthetic and should never be represented
as a production-scale deployment benchmark.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np


@dataclass
class ClaimWorld:
    truth: int
    agent_ids: np.ndarray
    root_ids: np.ndarray
    answers: np.ndarray
    initial_idx: np.ndarray


def _root_weights(n_roots: int, exponent: float) -> np.ndarray:
    ranks = np.arange(1, n_roots + 1, dtype=float)
    w = 1.0 / np.power(ranks, exponent)
    return w / w.sum()


def generate_claim(rng: np.random.Generator, n_agents: int, cfg: dict) -> ClaimWorld:
    truth = int(rng.integers(0, 2))
    n_roots = max(2, int(rng.poisson(cfg["mean_roots_per_claim"] - 2)) + 2)
    pool = min(int(cfg["candidate_pool_size"]), n_agents)
    agent_ids = rng.choice(n_agents, size=pool, replace=False)

    weights = _root_weights(n_roots, float(cfg["root_zipf_exponent"]))
    root_ids = rng.choice(n_roots, size=pool, replace=True, p=weights)

    # Each root emits one underlying proposition value. Corrupted roots are
    # deliberately wrong; non-corrupted roots are usually correct but retain
    # a small independent root error rate to avoid a perfectly clean world.
    root_answers = np.empty(n_roots, dtype=np.int8)
    for r in range(n_roots):
        if rng.random() < float(cfg["corrupt_root_prob"]):
            root_answers[r] = 1 - truth
        else:
            root_answers[r] = truth if rng.random() < float(cfg["root_correct_prob"]) else 1 - truth

    answers = root_answers[root_ids].copy()
    flips = rng.random(pool) < float(cfg["local_flip_prob"])
    answers[flips] = 1 - answers[flips]

    initial_n = min(int(cfg["initial_observers"]), pool)
    initial_idx = np.sort(rng.choice(pool, size=initial_n, replace=False))
    return ClaimWorld(truth, agent_ids, root_ids, answers, initial_idx)


def aggregate(world: ClaimWorld, observed: List[int]) -> Tuple[int, float, int, float]:
    """Root-balanced aggregation.

    Returns prediction, confidence, number of roots, root-conflict fraction.
    """
    by_root: Dict[int, List[int]] = {}
    for i in observed:
        by_root.setdefault(int(world.root_ids[i]), []).append(int(world.answers[i]))

    root_votes = []
    for vals in by_root.values():
        ones = sum(vals)
        zeros = len(vals) - ones
        # Tie is resolved deterministically toward 1; ties are rare and the
        # same rule applies to every strategy.
        root_votes.append(1 if ones >= zeros else 0)

    if not root_votes:
        return 0, 0.5, 0, 0.0

    ones = sum(root_votes)
    zeros = len(root_votes) - ones
    pred = 1 if ones >= zeros else 0
    confidence = max(ones, zeros) / len(root_votes)
    oppose = zeros if pred == 1 else ones
    conflict = oppose / len(root_votes)
    return pred, float(confidence), len(root_votes), float(conflict)


def choose_unseen_random(rng: np.random.Generator, world: ClaimWorld, observed_set: set[int]) -> int | None:
    available = [i for i in range(len(world.agent_ids)) if i not in observed_set]
    if not available:
        return None
    return int(rng.choice(available))


def choose_unseen_root(rng: np.random.Generator, world: ClaimWorld, observed_set: set[int]) -> int | None:
    seen_roots = {int(world.root_ids[i]) for i in observed_set}
    candidates = [i for i in range(len(world.agent_ids)) if i not in observed_set and int(world.root_ids[i]) not in seen_roots]
    if candidates:
        return int(rng.choice(candidates))
    return choose_unseen_random(rng, world, observed_set)


def has_candidate_conflict(world: ClaimWorld) -> bool:
    # Root-level latent conflict in the candidate coalition, computed from the
    # majority answer among descendants of each root.
    root_vals: Dict[int, List[int]] = {}
    for r, a in zip(world.root_ids.tolist(), world.answers.tolist()):
        root_vals.setdefault(int(r), []).append(int(a))
    votes = set()
    for vals in root_vals.values():
        votes.add(1 if sum(vals) >= (len(vals) - sum(vals)) else 0)
    return len(votes) > 1


def observed_conflict(world: ClaimWorld, observed: List[int]) -> bool:
    root_vals: Dict[int, List[int]] = {}
    for i in observed:
        root_vals.setdefault(int(world.root_ids[i]), []).append(int(world.answers[i]))
    votes = set()
    for vals in root_vals.values():
        votes.add(1 if sum(vals) >= (len(vals) - sum(vals)) else 0)
    return len(votes) > 1


def run_strategy(worlds: List[ClaimWorld], strategy: str, rng: np.random.Generator, cfg: dict) -> dict:
    correct = 0
    false_conf = 0
    questions = 0
    new_roots = 0
    conflict_possible = 0
    conflict_found = 0
    initial_gaps = 0
    resolved_gaps = 0
    final_roots_total = 0

    budget = int(cfg["question_budget_per_claim"])
    uncertainty_threshold = float(cfg["uncertainty_threshold"])
    hi = float(cfg["high_confidence_threshold"])
    min_roots = int(cfg["min_independent_roots"])

    for world in worlds:
        observed = [int(i) for i in world.initial_idx.tolist()]
        observed_set = set(observed)
        init_pred, init_conf, init_roots, init_conflict = aggregate(world, observed)
        gap = (init_pred != world.truth) or (init_conf < hi)
        initial_gaps += int(gap)

        if has_candidate_conflict(world):
            conflict_possible += 1

        for _ in range(budget):
            pred, conf, n_roots, conflict = aggregate(world, observed)
            pick = None

            if strategy == "none":
                break
            elif strategy == "random":
                pick = choose_unseen_random(rng, world, observed_set)
            elif strategy == "uncertainty":
                if conf >= uncertainty_threshold:
                    break
                pick = choose_unseen_random(rng, world, observed_set)
            elif strategy == "lineage":
                if n_roots >= min_roots:
                    break
                pick = choose_unseen_root(rng, world, observed_set)
            elif strategy == "continual":
                uncertainty = max(0.0, 1.0 - conf)
                lineage_weakness = max(0.0, (min_roots - n_roots) / max(1, min_roots))
                conflict_signal = conflict
                score = (
                    float(cfg["continual_uncertainty_weight"]) * uncertainty
                    + float(cfg["continual_lineage_weight"]) * lineage_weakness
                    + float(cfg["continual_conflict_weight"]) * conflict_signal
                )
                # Stop when the current epistemic state is sufficiently strong.
                if conf >= hi and n_roots >= min_roots and conflict < 0.20:
                    break
                if score <= 0:
                    break
                pick = choose_unseen_root(rng, world, observed_set)
            else:
                raise ValueError(f"Unknown strategy: {strategy}")

            if pick is None:
                break
            old_roots = {int(world.root_ids[i]) for i in observed_set}
            observed.append(pick)
            observed_set.add(pick)
            questions += 1
            if int(world.root_ids[pick]) not in old_roots:
                new_roots += 1

        pred, conf, n_roots, conflict = aggregate(world, observed)
        final_roots_total += n_roots
        correct += int(pred == world.truth)
        false_conf += int((pred != world.truth) and (conf >= hi))
        conflict_found += int(has_candidate_conflict(world) and observed_conflict(world, observed))
        if gap and pred == world.truth and conf >= hi:
            resolved_gaps += 1

    n = len(worlds)
    return {
        "accuracy": correct / n,
        "false_confident_consensus_rate": false_conf / n,
        "questions_per_claim": questions / n,
        "independent_roots_per_question": (new_roots / questions) if questions else 0.0,
        "conflict_detection_rate": (conflict_found / conflict_possible) if conflict_possible else 0.0,
        "resolved_gap_rate": (resolved_gaps / initial_gaps) if initial_gaps else 0.0,
        "final_independent_roots": final_roots_total / n,
        "total_questions": questions,
        "total_new_roots": new_roots,
        "n_claims": n,
    }


def run(cfg: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    t_all = time.perf_counter()

    for n_agents in cfg["agent_scales"]:
        for seed in cfg["seeds"]:
            world_rng = np.random.default_rng(int(seed) + int(n_agents) * 1_000_003)
            worlds = [generate_claim(world_rng, int(n_agents), cfg) for _ in range(int(cfg["n_claims"]))]
            for s_idx, strategy in enumerate(cfg["strategies"]):
                rng = np.random.default_rng(int(seed) * 1009 + int(n_agents) * 9176 + s_idx * 7919)
                t0 = time.perf_counter()
                metrics = run_strategy(worlds, strategy, rng, cfg)
                elapsed = time.perf_counter() - t0
                row = {
                    "experiment_name": cfg["experiment_name"],
                    "evidence_class": "synthetic_simulation",
                    "agent_scale": int(n_agents),
                    "seed": int(seed),
                    "strategy": strategy,
                    "runtime_sec": elapsed,
                    **metrics,
                }
                rows.append(row)
                print(f"N={n_agents:>5} seed={seed:>2} {strategy:<11} acc={metrics['accuracy']:.3f} false_conf={metrics['false_confident_consensus_rate']:.3f} q/claim={metrics['questions_per_claim']:.2f}")

    fields = list(rows[0].keys())
    with (out_dir / "raw_runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    with (out_dir / "run_config.json").open("w") as f:
        json.dump(cfg, f, indent=2)

    with (out_dir / "RUN_METADATA.txt").open("w") as f:
        f.write("Evidence class: synthetic simulation / mechanism probe\n")
        f.write(f"Elapsed wall time: {time.perf_counter() - t_all:.3f} sec\n")
        f.write("Do not present these outputs as a real 10,000-process deployment.\n")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--out", default="results")
    args = p.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    run(cfg, Path(args.out))


if __name__ == "__main__":
    main()

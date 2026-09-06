"""Experiment driver: builds worlds, applies failure conditions, records rows.

Raw outputs are append-only: every invocation writes into a fresh
results/<experiment_id>/ directory recording the git commit, the full config,
the seed list, a timestamp, machine information and dependency versions.
Nothing is ever overwritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import socket
import subprocess
import sys
import zlib
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .evaluate import ClaimResult, Condition, PairIndex, evaluate, precompute
from .failures import corrupt_nodes, corrupt_roots, make_alive, partition_components, partition_updates
from .questioning import QuestionPlan, apply_late_reverify, build_questioning_system, clear_late_reverify
from .systems import SYSTEM_IDS, SYSTEM_LABELS, build_system
from .workload import build_workload, condition_truth, score
from .world import build_world, world_summary

ROOT = Path(__file__).resolve().parents[1]


def stable_hash(text: str) -> int:
    """Process-independent hash.

    Python's built-in ``hash`` for strings is salted per process (PYTHONHASHSEED),
    so seeding any RNG from it makes a run irreproducible across invocations even
    with a fixed seed list.  crc32 is stable forever.
    """
    return zlib.crc32(text.encode()) & 0xFFFFFFFF
REPO = Path(__file__).resolve().parents[3]


# -------------------------------------------------------------------------
# provenance
# -------------------------------------------------------------------------

def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO), text=True).strip()
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        return bool(subprocess.check_output(["git", "status", "--porcelain"],
                                            cwd=str(REPO), text=True).strip())
    except Exception:
        return False


def environment_info() -> dict:
    import scipy
    return {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "hostname": socket.gethostname(),
    }


# -------------------------------------------------------------------------
# per-seed state
# -------------------------------------------------------------------------

class SeedRun:
    def __init__(self, cfg: dict, seed: int, systems: list[str]):
        self.cfg = cfg
        self.seed = seed
        self.systems = list(systems)
        self.world = build_world(seed, cfg)
        self.wl = build_workload(self.world, np.random.default_rng(seed * 31 + 5), cfg)
        self.pl: dict = {}
        self.idx: dict = {}
        self.qcost: dict = {}
        self.build_seconds = {}
        for s in self.systems:
            if s in ("B7", "B3Q"):
                continue
            t0 = time.perf_counter()
            rng = np.random.default_rng(seed * 7919 + stable_hash(s) % 10_000)
            p = build_system(s, self.world, cfg, rng)
            self.pl[s] = p
            self.idx[s] = precompute(self.world, p)
            self.build_seconds[s] = time.perf_counter() - t0

    def build_questioner(self, name: str, base: str, plan: QuestionPlan,
                         node_corrupt=None, root_corrupt=None):
        t0 = time.perf_counter()
        rng = np.random.default_rng(self.seed * 7919 + 4242)
        p, cost = build_questioning_system(self.world, self.cfg, rng, base=base, plan=plan,
                                           node_corrupt=node_corrupt, root_corrupt=root_corrupt,
                                           system_name=name)
        self.pl[name] = p
        self.idx[name] = precompute(self.world, p)
        self.qcost[name] = cost
        self.build_seconds[name] = time.perf_counter() - t0
        return p


# -------------------------------------------------------------------------
# condition grid
# -------------------------------------------------------------------------

def conditions_for(cfg: dict) -> list[dict]:
    grid = []
    for regime, sevs in cfg["failure_grid"].items():
        for s in sevs:
            grid.append({"regime": regime, "severity": float(s), "corruption_type": "none",
                         "corruption_level": 0.0, "partition_parts": 0, "phase": "post_failure"})
    for ctype in cfg.get("corruption_types", []):
        for lvl in cfg.get("corruption_levels", []):
            grid.append({"regime": cfg.get("corruption_base_regime", "random"),
                         "severity": float(cfg.get("corruption_base_severity", 0.3)),
                         "corruption_type": ctype, "corruption_level": float(lvl),
                         "partition_parts": 0, "phase": "post_failure"})
    for parts in cfg.get("partition_parts", []):
        for phase in ("during_partition", "after_reconnect"):
            grid.append({"regime": "partition", "severity": 0.0, "corruption_type": "none",
                         "corruption_level": 0.0, "partition_parts": int(parts), "phase": phase})
    return grid


# -------------------------------------------------------------------------
# partition handling
# -------------------------------------------------------------------------

def _split_codes(pl, comp, origin_comp, updated):
    """Placements outside the origin's component still hold the pre-update value."""
    return updated[pl.pl_claim] & (comp[pl.pl_node] != origin_comp[pl.pl_claim])


def evaluate_partition(w, pl, idx, wl, cfg, comp, origin_comp, updated, phase,
                       node_corrupt, root_corrupt, variant, n_variants):
    saved = pl.code_override
    base = np.full(pl.n_placements, -1, np.int8) if saved is None else saved.copy()
    forced = np.where(_split_codes(pl, comp, origin_comp, updated), np.int8(1), base).astype(np.int8)
    pl.code_override = forced
    try:
        n_parts = int(comp.max() + 1)
        q_comp = comp[wl.querier]
        out = None
        for p in range(n_parts):
            sel = np.flatnonzero(q_comp == p)
            if sel.size == 0:
                continue
            cond = Condition(
                alive=np.ones(w.n_nodes, bool),
                reachable=(comp == p) if phase == "during_partition" else None,
                node_corrupt=node_corrupt, root_corrupt=root_corrupt, querier=wl.querier,
                n_false_variants=n_variants, node_false_variant=variant,
                max_probe=int(cfg.get("max_probe", 8)),
                parallel_probe=int(cfg.get("parallel_probe", 4)))
            r = evaluate(w, pl, idx, cond)
            if out is None:
                out = ClaimResult(**{k: np.copy(v) for k, v in r.__dict__.items()})
            else:
                for k, v in r.__dict__.items():
                    getattr(out, k)[sel] = v[sel]
            if phase != "during_partition":
                break        # reconnected: one pass covers every querier
        return out, cond
    finally:
        pl.code_override = saved


def partition_truth(w, comp, origin_comp, updated, node_corrupt, root_corrupt, variant, n_variants):
    stale = updated[w.item_claim] & (comp[w.item_home] != origin_comp[w.item_claim])
    codes = np.where(stale, 1, w.item_stale.astype(np.int64))
    if root_corrupt is not None:
        codes = np.where(root_corrupt[w.item_root], 2, codes)
    if node_corrupt is not None:
        var = variant[w.item_home].astype(np.int64) if variant is not None else 0
        codes = np.where(node_corrupt[w.item_home], 2 + var, codes)
    v = 2 + max(1, n_variants)
    present = np.zeros(w.n_claims * v, bool)
    present[w.item_claim.astype(np.int64) * v + codes] = True
    n_distinct = present.reshape(w.n_claims, v).sum(axis=1)
    return {"contradiction_label": n_distinct > 1,
            "multi_source_label": w.claim_n_roots >= 2,
            "reconstruction_mask": np.zeros(w.n_claims, bool),
            "frac_home_evidence_alive": np.ones(w.n_claims),
            "partition_updated": updated}


# -------------------------------------------------------------------------
# rows
# -------------------------------------------------------------------------

def _row(meta, cond_spec, seed, system, pl, qcost, extra_msgs, w, metrics) -> dict:
    row = {**meta, **cond_spec, "seed": seed, "system": system,
           "system_label": SYSTEM_LABELS.get(system, system),
           "aggregation": pl.aggregation,
           "storage_units": int(pl.storage_units),
           "setup_messages": int(pl.setup_messages),
           "question_messages": int(qcost.messages) + int(extra_msgs) if qcost else int(extra_msgs),
           "question_writes": int(qcost.writes) if qcost else 0,
           "n_questions_asked": int(qcost.n_questions) if qcost else 0}
    row.update(metrics)
    m = w.n_claims
    row["n_claims"] = m
    row["storage_per_claim"] = row["storage_units"] / m
    total = row["setup_messages"] + row["question_messages"] + metrics["query_messages"]
    row["total_messages"] = total
    row["messages_per_claim"] = total / m
    ks = metrics["knowledge_survival"]
    # undefined rather than unbounded for B0, which publishes nothing and sends
    # no messages: an infinite efficiency ratio would be meaningless
    row["redundancy_efficiency"] = (ks / row["storage_per_claim"]
                                    if row["storage_per_claim"] > 0 else float("nan"))
    row["communication_efficiency"] = (ks * m) / total if total > 0 else float("nan")
    return row


def run_seed(run: SeedRun, meta: dict, grid: list[dict], sink) -> int:
    w, wl, cfg = run.world, run.wl, run.cfg
    n = 0
    for cond_spec in grid:
        key = json.dumps(cond_spec, sort_keys=True)
        # one stream for everything drawn once per condition (corruption masks,
        # partition structure) ...
        rng = np.random.default_rng((run.seed * 1_000_003 + stable_hash(key)) % (2**32))
        # ... and a separate, *identical* stream handed to every system for the
        # failure draw, so that all systems face the same realised failure.  Only
        # the white-box attack differs between systems, and only because it is
        # computed against each system's own placement.
        failure_seed = (run.seed * 2_654_435_761 + stable_hash("failure|" + key)) % (2**32)

        node_corrupt = root_corrupt = variant = None
        n_variants = 1
        ctype, lvl = cond_spec["corruption_type"], cond_spec["corruption_level"]
        if ctype == "node_coordinated":
            node_corrupt, _ = corrupt_nodes(w, rng, lvl, coordinated=True)
        elif ctype == "node_uncoordinated":
            n_variants = int(cfg.get("n_false_variants", 4))
            node_corrupt, variant = corrupt_nodes(w, rng, lvl, coordinated=False,
                                                  n_variants=n_variants)
        elif ctype == "root":
            root_corrupt = corrupt_roots(w, rng, lvl)

        # questioning systems are rebuilt per corruption context: re-verification
        # against a corrupt origin installs the false value
        corruption_key = (ctype, lvl, int(run.seed))
        for name, base in run_questioners(run):
            if run.qcost.get(name) is not None and getattr(run, "_ckey", None) == corruption_key:
                continue
            plan = QuestionPlan(strategy=cfg.get("question_strategy", "hybrid"),
                                budget_per_claim=float(cfg.get("question_budget", 0.6)),
                                equalize_storage=bool(cfg.get("question_equalize_storage", True)))
            run.build_questioner(name, base, plan, node_corrupt, root_corrupt)
        run._ckey = corruption_key

        partitioned = cond_spec["regime"] == "partition"
        if partitioned:
            comp = partition_components(w, rng, int(cond_spec["partition_parts"]))
            updated, origin_comp = partition_updates(
                w, rng, comp, float(cfg.get("partition_update_fraction", 0.3)))
            truth = partition_truth(w, comp, origin_comp, updated, node_corrupt,
                                    root_corrupt, variant, n_variants)

        for s in run.systems:
            pl, idx = run.pl[s], run.idx[s]
            qcost = run.qcost.get(s)
            extra_msgs = 0
            t0 = time.perf_counter()
            if partitioned:
                if qcost is not None and cond_spec["phase"] == "after_reconnect":
                    extra_msgs = apply_late_reverify(pl)
                res, cond = evaluate_partition(w, pl, idx, wl, cfg, comp, origin_comp, updated,
                                               cond_spec["phase"], node_corrupt, root_corrupt,
                                               variant, n_variants)
                clear_late_reverify(pl)
            else:
                alive = make_alive(w, cond_spec["regime"], cond_spec["severity"],
                                   np.random.default_rng(failure_seed), pl=pl, idx=idx)
                cond = Condition(alive=alive, reachable=None, node_corrupt=node_corrupt,
                                 root_corrupt=root_corrupt, querier=wl.querier,
                                 n_false_variants=n_variants, node_false_variant=variant,
                                 max_probe=int(cfg.get("max_probe", 8)),
                                 parallel_probe=int(cfg.get("parallel_probe", 4)))
                res = evaluate(w, pl, idx, cond)
                truth = condition_truth(w, cond)
            metrics = score(w, wl, res, cond, truth, agg_is_lineage=(pl.aggregation == "lineage"))
            metrics["eval_seconds"] = time.perf_counter() - t0
            metrics["build_seconds"] = run.build_seconds.get(s, 0.0)
            sink(_row(meta, cond_spec, run.seed, s, pl, qcost, extra_msgs, w, metrics))
            n += 1
    return n


def run_questioners(run: SeedRun):
    out = []
    for s in run.systems:
        if s == "B7":
            out.append(("B7", "B6"))
        elif s == "B3Q":
            out.append(("B3Q", "B3"))
    return out


# -------------------------------------------------------------------------
# entry point
# -------------------------------------------------------------------------

class CsvSink:
    def __init__(self, path: Path):
        self.path = path
        self.fh = open(path, "w", newline="")
        self.writer = None
        self.n = 0

    def __call__(self, row: dict):
        if self.writer is None:
            self.writer = csv.DictWriter(self.fh, fieldnames=list(row.keys()))
            self.writer.writeheader()
        self.writer.writerow(row)
        self.n += 1
        if self.n % 200 == 0:
            self.fh.flush()

    def close(self):
        self.fh.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Large-scale Agentic Web stress test")
    ap.add_argument("--config", required=True)
    ap.add_argument("--seeds", default=None, help="path to seeds.json (default: experiment root)")
    ap.add_argument("--n-seeds", type=int, default=None)
    ap.add_argument("--out", default=None, help="results subdirectory name")
    ap.add_argument("--systems", default=None, help="comma separated system ids")
    ap.add_argument("--tag", default="main")
    ap.add_argument("--index-name", default=None,
                    help="logical name recorded in results/index.jsonl for the analysis step")
    ap.add_argument("--set", action="append", default=[],
                    help="override a config key, e.g. --set budget_k=8")
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text())
    for override in args.set:
        key, _, val = override.partition("=")
        try:
            cfg[key] = json.loads(val)
        except json.JSONDecodeError:
            cfg[key] = val
    cfg["overrides"] = args.set
    seeds_path = Path(args.seeds) if args.seeds else ROOT / "seeds.json"
    all_seeds = json.loads(seeds_path.read_text())["seeds"]
    n_seeds = args.n_seeds or int(cfg.get("n_seeds", 30))
    seeds = all_seeds[:n_seeds]

    systems = (args.systems.split(",") if args.systems
               else cfg.get("systems", SYSTEM_IDS))
    grid = conditions_for(cfg)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    exp_id = args.out or f"{cfg['name']}_{args.tag}_{stamp}"
    out_dir = ROOT / "results" / exp_id
    out_dir.mkdir(parents=True, exist_ok=False)

    meta = {"experiment_id": exp_id, "config_name": cfg["name"],
            "n_agents": int(cfg["n_agents"]), "git_commit": git_commit(),
            "git_dirty": git_dirty(),
            "timestamp_utc": datetime.now(timezone.utc).isoformat()}

    manifest = {
        "experiment_id": exp_id, "tag": args.tag, "config": cfg,
        "config_path": str(Path(args.config).resolve()),
        "seeds": seeds, "n_seeds": len(seeds), "systems": systems,
        "n_conditions": len(grid), "conditions": grid,
        "git_commit": meta["git_commit"], "git_dirty": meta["git_dirty"],
        "started_utc": meta["timestamp_utc"], "environment": environment_info(),
        "command": " ".join(sys.argv),
    }

    sink = CsvSink(out_dir / "rows.csv")
    t_start = time.time()
    per_seed_seconds = []
    world_stats = None
    print(f"[{exp_id}] N={cfg['n_agents']} seeds={len(seeds)} systems={systems} "
          f"conditions={len(grid)} -> {len(seeds)*len(grid)*len(systems)} rows", flush=True)
    for i, seed in enumerate(seeds):
        t0 = time.time()
        run = SeedRun(cfg, seed, systems)
        if world_stats is None:
            world_stats = world_summary(run.world)
            world_stats["n_questions_per_seed"] = int(run.wl.n_questions)
            print("  world:", json.dumps(world_stats), flush=True)
        run_seed(run, meta, grid, sink)
        dt = time.time() - t0
        per_seed_seconds.append(dt)
        print(f"  seed {seed} ({i+1}/{len(seeds)}) {dt:.1f}s", flush=True)
    sink.close()

    manifest.update({
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.time() - t_start,
        "per_seed_seconds": per_seed_seconds,
        "n_rows": sink.n,
        "world_summary": world_stats,
        "questions_per_seed": world_stats.get("n_questions_per_seed"),
        "total_question_instances": world_stats.get("n_questions_per_seed", 0) * len(seeds),
    })
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if args.index_name:
        # append-only index so the analysis step can find runs without guessing,
        # while raw outputs stay immutable
        with open(ROOT / "results" / "index.jsonl", "a") as fh:
            fh.write(json.dumps({"index_name": args.index_name, "experiment_id": exp_id,
                                 "dir": str(out_dir.relative_to(ROOT)),
                                 "config_name": cfg["name"], "n_agents": int(cfg["n_agents"]),
                                 "n_seeds": len(seeds), "overrides": args.set,
                                 "finished_utc": manifest["finished_utc"],
                                 "runtime_seconds": manifest["runtime_seconds"],
                                 "n_rows": sink.n}) + "\n")
    print(f"[{exp_id}] {sink.n} rows in {manifest['runtime_seconds']:.1f}s -> {out_dir}", flush=True)
    return out_dir


if __name__ == "__main__":
    main()

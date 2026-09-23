"""Shared experiment harness (MODULE_SPEC.md Task H).

Every family runner (`experiments/<family>/run.py`) is a thin script on top of
three functions defined here:

* `parse_args()`  - the common CLI (`--tier`, `--seeds`, `--seed-offset`, `--systems`,
  `--set k=v`, `--results DIR`, `--quick`, `--jobs N`, `--tag`).
* `make_cfg(tier, overrides)` - the resolved configuration for a tier (merges the
  tier's `org` / `world` blocks from `configs/experiments.yaml`, then dotted overrides).
* `run_grid(family, systems, seeds, cfg, world_factory, per_run, conditions)` - the grid
  executor: one world per seed (cached across systems and conditions), optional
  attack / failure hooks per condition, `runner.run_system`, provenance manifests
  under `results/raw/<family>/<run_id>/` and one flat row per run appended to
  `results/processed/<family>.csv`.

Design rules honoured here (DESIGN.md §1, §12, §13):
* a run that raises is recorded with `status: failed` (manifest + CSV row) and is
  never deleted; a run that cannot start because another module is missing
  (ImportError) is recorded with `status: skipped` and a clear log line;
* processed CSVs are append-only; every invocation gets a fresh `batch_id` that
  prefixes its run ids, so re-running a family adds rows instead of replacing them
  (`rm -rf results/raw results/processed` resets everything);
* `--jobs N` runs seeds in separate forked processes (one seed end-to-end per
  process, at most N alive) so peak memory stays bounded to one world plus one
  system per process; only the parent writes the CSV.
"""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field, asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

BENCH_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = BENCH_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

import numpy as np  # noqa: E402

from mycelic_bench.config import (DEFAULT_RESULTS, apply_dotted, deep_update, load_config,  # noqa: E402
                                  parse_scalar, validate)
from mycelic_bench.manifest import _jsonable, append_processed_row, write_manifest  # noqa: E402

TIERS = ("tier1", "tier2", "tier3")

# Condition columns that every processed CSV carries (defaults filled from the effective policy).
STANDARD_CONDITION_COLUMNS = (
    "malicious_fraction", "detector", "layers", "compression", "sketch_order", "cadence", "profile", "profile_kind",
    "quote_policy", "k_anonymity", "questioning", "failure_kind", "failure_rate",
)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
def log(msg: str, *, family: str | None = None) -> None:
    stamp = time.strftime("%H:%M:%S")
    prefix = f"[{family}] " if family else ""
    print(f"{stamp} {prefix}{msg}", flush=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(description: str = "", argv: list[str] | None = None,
               add_arguments: Callable[[argparse.ArgumentParser], None] | None = None) -> argparse.Namespace:
    """Common CLI for every family runner.  `add_arguments(parser)` adds family flags."""
    ap = argparse.ArgumentParser(description=description, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--tier", default="tier1", choices=list(TIERS), help="organisation tier (configs/experiments.yaml)")
    ap.add_argument("--seeds", type=int, default=None,
                    help="number of seeds (default: the tier's `seeds`; 1 with --quick)")
    ap.add_argument("--seed-offset", dest="seed_offset", type=int, default=0, help="first seed")
    ap.add_argument("--systems", default=None, help="comma-separated system names (default: the family's list)")
    ap.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                    help="dotted config override, repeatable (e.g. --set org.n_workers=300)")
    ap.add_argument("--results", default=str(DEFAULT_RESULTS), help="results root directory")
    ap.add_argument("--quick", action="store_true", help="reduced sweep grid for a dry run (1 seed unless --seeds)")
    ap.add_argument("--jobs", type=int, default=1, help="parallel seed processes (one seed end-to-end per process)")
    ap.add_argument("--tag", default="", help="free-form tag stored in every manifest and row")
    if add_arguments is not None:
        add_arguments(ap)
    args = ap.parse_args(argv)
    args.systems = [s.strip() for s in args.systems.split(",") if s.strip()] if args.systems else None
    args.results = Path(args.results)
    args.jobs = max(1, int(args.jobs))
    return args


def seeds_from_args(args: argparse.Namespace, cfg: dict[str, Any]) -> list[int]:
    """Seed list: `--seeds N` (or the tier's default; 1 with --quick) starting at `--seed-offset`."""
    n = args.seeds
    if n is None:
        n = 1 if args.quick else int(cfg.get("tiers", {}).get(args.tier, {}).get("seeds", 5))
    return list(range(int(args.seed_offset), int(args.seed_offset) + int(n)))


def resolve_systems(args: argparse.Namespace, default: list[str], cfg: dict[str, Any] | None = None,
                    family: str | None = None) -> list[str]:
    systems = list(args.systems) if args.systems else list(default)
    if cfg is not None:
        unknown = [s for s in systems if s not in cfg.get("systems", {})]
        if unknown:
            log(f"note: systems not in configs/experiments.yaml (need a Condition.sys_cfg): {unknown}", family=family)
    return systems


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def make_cfg(tier: str, overrides: Iterable[str] | dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolved configuration for `tier`: base configs, the tier's `org`/`world` blocks, then dotted overrides.

    `org.n_regions` is derived from `departments_per_region` (set to 0) unless the tier block or an
    override pins it, so tier2/tier3 organisations get a real region layer.
    """
    if tier not in TIERS:
        raise KeyError(f"unknown tier {tier!r}; expected one of {TIERS}")
    cfg = load_config()
    tiers = cfg.get("tiers", {})
    block = tiers.get(tier, {})
    upd: dict[str, Any] = {}
    for k in ("org", "world", "policy", "attacks", "security", "models"):
        if k in block:
            upd[k] = block[k]
    cfg = deep_update(cfg, upd)
    if "n_regions" not in block.get("org", {}):
        cfg["org"]["n_regions"] = 0
    cfg["tier"] = tier
    if overrides:
        items = overrides.items() if isinstance(overrides, dict) else [(o.partition("=")[0], o.partition("=")[2]) for o in overrides]
        for k, v in items:
            apply_dotted(cfg, str(k).strip(), parse_scalar(str(v).strip()) if isinstance(v, str) else v)
    validate(cfg)
    return cfg


def with_overrides(cfg: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Deep copy of `cfg` with dotted-key overrides applied (and validated)."""
    out = copy.deepcopy(cfg)
    for k, v in (overrides or {}).items():
        apply_dotted(out, k, v)
    if overrides:
        validate(out)
    return out


def world_key(cfg: dict[str, Any], seed: int) -> str:
    """Cache key of a generated world: everything `generate_world` reads."""
    sec = cfg.get("security", {})
    payload = {"org": cfg.get("org"), "world": cfg.get("world"), "seed": seed,
               "rho": [sec.get("rho_team"), sec.get("rho_department"), sec.get("rho_region")]}
    return hashlib.sha256(json.dumps(_jsonable(payload), sort_keys=True).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------
@dataclass
class Condition:
    """One point of a sweep, applied to every system of the grid (or `systems`).

    columns          - flat columns recorded in the CSV for this point (e.g. {"malicious_fraction": 0.1});
    cfg_overrides    - dotted config overrides for this point (changing `org`/`world` regenerates the world);
    policy_overrides - `Policy` field overrides for hierarchy systems (win over the system's sys_cfg) and,
                       for baselines, merged into cfg["policy"];
    sys_cfg          - replaces the system's entry from configs/experiments.yaml (e.g. a custom topology);
    sys_cfg_update   - merged into the system's entry;
    attack           - {"fraction": f, "type_mix": {...}} -> attacks.apply_attacks before the runs of this point;
    failure          - dict of FailurePlan kwargs (seed added) or callable(world, cfg, seed, system) -> plan;
    systems          - restrict this point to these systems; seeds - restrict to these seeds.
    """
    name: str = "default"
    columns: dict[str, Any] = field(default_factory=dict)
    cfg_overrides: dict[str, Any] = field(default_factory=dict)
    policy_overrides: dict[str, Any] = field(default_factory=dict)
    sys_cfg: dict[str, Any] | None = None
    sys_cfg_update: dict[str, Any] = field(default_factory=dict)
    attack: dict[str, Any] | None = None
    failure: Any = None
    systems: list[str] | None = None
    seeds: list[int] | None = None

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.=+-]+", "-", self.name).strip("-")[:80] or "default"


@dataclass
class RunContext:
    """What `per_run(ctx)` hooks receive after a successful run."""
    family: str
    world: Any
    cfg: dict[str, Any]
    seed: int
    system: str
    sys_cfg: dict[str, Any]
    condition: Condition
    result: dict[str, Any]
    metrics: dict[str, Any]
    run_dir: Path
    seed_metrics: dict[tuple[str, str], dict[str, Any]]   # (condition name, system) -> metrics of earlier runs of this seed
    attack_plan: Any = None
    failure_plan: Any = None
    tier: str = ""
    quick: bool = False
    runtime_s: float = 0.0
    peak_rss_mb: float = 0.0


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------
_LONG_LIST = 16


def flatten(obj: Any, prefix: str = "", out: dict[str, Any] | None = None, sep: str = ".") -> dict[str, Any]:
    """Flatten nested dicts/lists into dotted keys; NaN/None -> '' ; long scalar lists -> JSON string."""
    if out is None:
        out = {}
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    if isinstance(obj, dict):
        if not obj:
            if prefix:
                out[prefix] = ""
            return out
        for k, v in obj.items():
            flatten(v, f"{prefix}{sep}{k}" if prefix else str(k), out, sep)
        return out
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    if isinstance(obj, (list, tuple)):
        if len(obj) == 0:
            out[prefix] = ""
        elif len(obj) > _LONG_LIST and all(not isinstance(x, (dict, list, tuple)) for x in obj):
            out[prefix] = json.dumps(_jsonable(list(obj)))
        else:
            for i, v in enumerate(obj):
                flatten(v, f"{prefix}{sep}{i}", out, sep)
        return out
    v = _jsonable(obj)
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        v = ""
    out[prefix] = v
    return out


# --------------------------------------------------------------------------
# Resource sampling
# --------------------------------------------------------------------------
def current_rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 2**20
    except Exception:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


class RssSampler:
    """Samples resident memory on a background thread; `.peak_mb` after the block."""

    def __init__(self, interval: float = 0.2) -> None:
        self.interval = interval
        self.peak_mb = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.peak_mb = max(self.peak_mb, current_rss_mb())
            self._stop.wait(self.interval)

    def __enter__(self) -> "RssSampler":
        self.peak_mb = current_rss_mb()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.peak_mb = max(self.peak_mb, current_rss_mb())


# --------------------------------------------------------------------------
# World snapshot (attacks mutate records in place; restore between conditions)
# --------------------------------------------------------------------------
_WORLD_ARRAYS = ("attrs", "true_labels", "observed_labels", "worker", "round", "score", "confidence", "task_id",
                 "fingerprint", "origin_worker", "is_copy", "sensitive", "sensitive_kind", "mention_mask", "attack_tag")
_WORLD_LISTS = ("canary", "injection_payload")


class WorldSnapshot:
    def __init__(self, world: Any) -> None:
        self.arrays = {k: np.copy(getattr(world, k)) for k in _WORLD_ARRAYS if getattr(world, k, None) is not None}
        self.lists = {k: list(getattr(world, k)) for k in _WORLD_LISTS if getattr(world, k, None) is not None}
        self.worker_attack = np.copy(world.org.worker_attack)
        self.hash = world.dataset_sha256

    def restore(self, world: Any) -> None:
        for k, v in self.arrays.items():
            cur = getattr(world, k)
            if isinstance(cur, np.ndarray) and cur.shape == v.shape and cur.dtype == v.dtype:
                cur[...] = v
            else:
                setattr(world, k, np.copy(v))
        for k, v in self.lists.items():
            setattr(world, k, list(v))
        world.org.worker_attack[...] = self.worker_attack
        world.dataset_sha256 = self.hash


# --------------------------------------------------------------------------
# Helpers for rows / manifests
# --------------------------------------------------------------------------
def effective_policy(cfg: dict[str, Any], sys_cfg: dict[str, Any], policy_overrides: dict[str, Any] | None):
    from mycelic_bench.hierarchy import Policy
    overrides = {k: v for k, v in sys_cfg.items() if k not in ("kind", "layers", "local_slm_classifier")}
    overrides.update(policy_overrides or {})
    return Policy.from_cfg(cfg, **overrides)


def resolve_sys_cfg(cfg: dict[str, Any], system: str, cond: Condition) -> dict[str, Any]:
    if cond.sys_cfg is not None:
        base = dict(cond.sys_cfg)
    elif system in cfg.get("systems", {}):
        base = dict(cfg["systems"][system])
    else:
        raise KeyError(f"system {system!r} is not in configs/experiments.yaml and the condition gives no sys_cfg")
    base.update(cond.sys_cfg_update or {})
    return base


def _cadence_value(cadence: Any) -> Any:
    if isinstance(cadence, dict):
        vals = sorted({int(v) for v in cadence.values()})
        return vals[0] if len(vals) == 1 else json.dumps(cadence, sort_keys=True)
    return cadence


def condition_columns(cfg: dict[str, Any], system: str, sys_cfg: dict[str, Any], cond: Condition) -> dict[str, Any]:
    """Standard condition columns with defaults derived from the effective policy / config."""
    models = cfg.get("models", {})
    edge = models.get("edge_profile")
    measured = bool(models.get("profiles", {}).get(edge, {}).get("measured", False))
    cols: dict[str, Any] = {c: "" for c in STANDARD_CONDITION_COLUMNS}
    cols["malicious_fraction"] = float(cond.attack.get("fraction", 0.0)) if cond.attack else 0.0
    cols["profile"] = edge
    cols["profile_kind"] = "measured" if measured else "synthetic"
    if isinstance(cond.failure, dict):
        cols["failure_kind"] = cond.failure.get("kind", "")
        cols["failure_rate"] = cond.failure.get("rate", "")
    kind = sys_cfg.get("kind", "hierarchy")
    if kind == "hierarchy":
        try:
            pol = effective_policy(cfg, sys_cfg, cond.policy_overrides)
            cols.update({"detector": pol.detector, "compression": pol.compression, "sketch_order": pol.sketch_order,
                         "cadence": _cadence_value(pol.cadence), "quote_policy": pol.quote_policy,
                         "k_anonymity": pol.k_anonymity, "questioning": pol.questioning})
        except Exception:  # pragma: no cover - policy construction failures surface in the run itself
            pass
        cols["layers"] = int(sys_cfg.get("layers", cfg.get("org", {}).get("layers", 5)))
    else:
        cols["layers"] = 0
        cols["detector"] = "none"
    cols.update(cond.columns or {})
    return cols


def _write_json(path: Path, obj: Any) -> None:
    with open(path, "w") as f:
        json.dump(_jsonable(obj), f, indent=1)


def _write_jsonl(path: Path, rows: Iterable[Any]) -> int:
    n = 0
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(_jsonable(r), separators=(",", ":")) + "\n")
            n += 1
    return n


def new_batch_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)


def processed_csv(results_root: Path, family: str) -> Path:
    return Path(results_root) / "processed" / f"{family}.csv"


# --------------------------------------------------------------------------
# One seed end-to-end (runs in the parent for --jobs 1, in a forked child otherwise)
# --------------------------------------------------------------------------
@dataclass
class SeedJob:
    family: str
    seed: int
    systems: list[str]
    conditions: list[Condition]
    cfg: dict[str, Any]
    world_factory: Callable[[dict[str, Any], int], Any] | None
    per_run: Callable[[RunContext], dict[str, Any] | None] | None
    results_root: Path
    batch_id: str
    tag: str
    tier: str
    quick: bool
    snapshot_every: int = 1
    argv: list[str] = field(default_factory=lambda: list(sys.argv))


def run_id_for(job: SeedJob, cond: Condition, system: str) -> str:
    return f"{job.batch_id}_{system}_{cond.slug}_s{job.seed}"


def planned_runs(job: SeedJob) -> list[tuple[Condition, str, str]]:
    out = []
    for cond in job.conditions:
        if cond.seeds is not None and job.seed not in cond.seeds:
            continue
        for system in (cond.systems or job.systems):
            out.append((cond, system, run_id_for(job, cond, system)))
    return out


def _base_row(job: SeedJob, cond: Condition, system: str, run_id: str, sys_cfg: dict[str, Any] | None,
              cfg: dict[str, Any], world: Any | None) -> dict[str, Any]:
    org = cfg.get("org", {})
    row: dict[str, Any] = {
        "family": job.family, "run_id": run_id, "batch_id": job.batch_id, "status": "", "system": system,
        "kind": (sys_cfg or {}).get("kind", ""), "seed": job.seed, "tier": job.tier, "tag": job.tag, "condition": cond.name,
        "n_workers": int(org.get("n_workers", 0)), "n_interactions": int(world.n) if world is not None else "",
        "n_rounds": int(org.get("n_rounds", 0)),
        "n_teams": int(world.org.n_teams) if world is not None else "",
        "n_departments": int(world.org.n_departments) if world is not None else "",
        "n_regions": int(world.org.n_regions) if world is not None else "",
        "dataset_sha256": (world.dataset_sha256[:16] if world is not None else ""),
    }
    if sys_cfg is not None:
        row.update(condition_columns(cfg, system, sys_cfg, cond))
    else:
        row.update({c: "" for c in STANDARD_CONDITION_COLUMNS})
        row.update(cond.columns or {})
    row.update({"runtime_s": "", "world_gen_s": "", "peak_rss_mb": "", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "error": ""})
    return row


def _record_not_ok(job: SeedJob, cond: Condition, system: str, run_id: str, status: str, err: str, cfg: dict[str, Any],
                   sys_cfg: dict[str, Any] | None, world: Any | None, runtime: float, emit: Callable[[dict[str, Any]], None],
                   tb: str | None = None) -> dict[str, Any]:
    run_dir = job.results_root / "raw" / job.family / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "log.txt", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {status}: {err}\n")
        if tb:
            f.write(tb + "\n")
    try:
        write_manifest(run_dir, family=job.family, run_id=run_id, system=system, seed=job.seed, cfg=cfg, metrics=None,
                       dataset_sha256=world.dataset_sha256 if world is not None else "", runtime_s=runtime, status=status,
                       error=err, outputs={"log": str(run_dir / "log.txt")},
                       extra={"tier": job.tier, "tag": job.tag, "batch_id": job.batch_id, "condition": _condition_dict(cond),
                              "sys_cfg": sys_cfg, "argv": job.argv, "quick": job.quick})
    except Exception as e:  # pragma: no cover - never lose the row because the manifest failed
        log(f"could not write manifest for {run_id}: {e}", family=job.family)
    row = _base_row(job, cond, system, run_id, sys_cfg, cfg, world)
    row["status"] = status
    row["error"] = err[:500]
    row["runtime_s"] = round(runtime, 3)
    _write_json(run_dir / "row.json", row)
    emit(row)
    return row


def _condition_dict(cond: Condition) -> dict[str, Any]:
    return {"name": cond.name, "columns": cond.columns, "cfg_overrides": cond.cfg_overrides,
            "policy_overrides": cond.policy_overrides, "sys_cfg": cond.sys_cfg, "sys_cfg_update": cond.sys_cfg_update,
            "attack": cond.attack, "failure": cond.failure if isinstance(cond.failure, dict) else
            (None if cond.failure is None else str(cond.failure)), "systems": cond.systems, "seeds": cond.seeds}


def run_seed(job: SeedJob, emit: Callable[[dict[str, Any]], None]) -> list[dict[str, Any]]:
    """Run every (condition, system) of one seed; `emit(row)` is called per finished run."""
    from mycelic_bench.runner import run_system
    from mycelic_bench.world import generate_world, ground_truth_summary

    fam = job.family
    rows: list[dict[str, Any]] = []
    world_cache: dict[str, tuple[Any, float]] = {}
    seed_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    factory = job.world_factory or generate_world

    def _emit(row: dict[str, Any]) -> None:
        rows.append(row)
        emit(row)

    for cond in job.conditions:
        if cond.seeds is not None and job.seed not in cond.seeds:
            continue
        systems = list(cond.systems or job.systems)
        # ---- configuration and world for this condition -------------------------------------------
        try:
            cond_cfg = with_overrides(job.cfg, cond.cfg_overrides)
            if cond.policy_overrides:
                cond_cfg["policy"] = {**cond_cfg.get("policy", {}), **cond.policy_overrides}
        except Exception as e:
            tb = traceback.format_exc()
            log(f"seed {job.seed} condition {cond.name}: bad configuration: {e}", family=fam)
            for system in systems:
                _record_not_ok(job, cond, system, run_id_for(job, cond, system), "failed", f"configuration error: {e}",
                               job.cfg, None, None, 0.0, _emit, tb)
            continue
        wkey = world_key(cond_cfg, job.seed)
        if wkey not in world_cache:
            world_cache.clear()
            gc.collect()
            t0 = time.perf_counter()
            try:
                world = factory(cond_cfg, job.seed)
            except Exception as e:
                tb = traceback.format_exc()
                log(f"seed {job.seed} condition {cond.name}: world generation failed: {e}", family=fam)
                for system in systems:
                    _record_not_ok(job, cond, system, run_id_for(job, cond, system), "failed", f"world generation failed: {e}",
                                   cond_cfg, None, None, time.perf_counter() - t0, _emit, tb)
                continue
            gen_s = time.perf_counter() - t0
            world_cache[wkey] = (world, gen_s)
            log(f"seed {job.seed}: world {wkey} generated in {gen_s:.1f}s (n={world.n}, teams={world.org.n_teams}, "
                f"departments={world.org.n_departments}, regions={world.org.n_regions})", family=fam)
        world, gen_s = world_cache[wkey]
        gt_summary = ground_truth_summary(world)
        # ---- attack (mutates the world; restored after the condition) ----------------------------
        plan = None
        attack_hook = None
        attack_info: dict[str, Any] = {}
        snapshot: WorldSnapshot | None = None
        if cond.attack and float(cond.attack.get("fraction", 0.0) or 0.0) > 0:
            frac = float(cond.attack["fraction"])
            try:
                from mycelic_bench import attacks as attacks_mod
            except ImportError as e:
                log(f"SKIP seed {job.seed} condition {cond.name}: attacks module unavailable ({e})", family=fam)
                for system in systems:
                    _record_not_ok(job, cond, system, run_id_for(job, cond, system), "skipped",
                                   f"attacks module unavailable: {e}", cond_cfg, None, world, 0.0, _emit)
                continue
            snapshot = WorldSnapshot(world)
            t0 = time.perf_counter()
            try:
                plan = attacks_mod.apply_attacks(world, cond_cfg, job.seed, fraction=frac, type_mix=cond.attack.get("type_mix"))
            except Exception as e:
                tb = traceback.format_exc()
                log(f"seed {job.seed} condition {cond.name}: apply_attacks failed: {e}", family=fam)
                snapshot.restore(world)
                for system in systems:
                    _record_not_ok(job, cond, system, run_id_for(job, cond, system), "failed", f"apply_attacks failed: {e}",
                                   cond_cfg, None, world, time.perf_counter() - t0, _emit, tb)
                continue
            attack_hook = getattr(plan, "hook", None)
            try:
                summary = plan.summary() if hasattr(plan, "summary") else {}
            except Exception:
                summary = {}
            mal = getattr(plan, "malicious_workers", None)
            attack_info = {"fraction": frac, "type_mix": cond.attack.get("type_mix"), "summary": summary,
                           "n_malicious_workers": int(len(mal)) if mal is not None else None,
                           "attack_records": int((world.attack_tag > 0).sum()), "apply_s": time.perf_counter() - t0}
            log(f"seed {job.seed} condition {cond.name}: attacks applied (fraction={frac}, "
                f"malicious={attack_info['n_malicious_workers']}, records={attack_info['attack_records']})", family=fam)
        # ---- systems ------------------------------------------------------------------------------
        try:
            for system in systems:
                run_id = run_id_for(job, cond, system)
                run_dir = job.results_root / "raw" / fam / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                log_path = run_dir / "log.txt"
                try:
                    sys_cfg = resolve_sys_cfg(cond_cfg, system, cond)
                except KeyError as e:
                    _record_not_ok(job, cond, system, run_id, "failed", str(e), cond_cfg, None, world, 0.0, _emit)
                    continue
                kind = sys_cfg.get("kind", "hierarchy")
                with open(log_path, "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} start {run_id} system={system} kind={kind} "
                            f"seed={job.seed} condition={cond.name} dataset={world.dataset_sha256[:16]}\n")
                log(f"seed {job.seed} | {cond.name} | {system} ...", family=fam)
                t0 = time.perf_counter()
                failure_plan = None
                failure_info: dict[str, Any] | None = None
                hook = attack_hook
                try:
                    if cond.failure is not None:
                        if callable(cond.failure):
                            failure_plan = cond.failure(world, cond_cfg, job.seed, system)
                        else:
                            from mycelic_bench.failures import FailurePlan
                            kw = dict(cond.failure)
                            kw.setdefault("seed", job.seed)
                            failure_plan = FailurePlan(**kw)
                        if failure_plan is not None and hasattr(failure_plan, "wrap_hook"):
                            hook = failure_plan.wrap_hook(attack_hook)
                        failure_info = cond.failure if isinstance(cond.failure, dict) else {"plan": repr(failure_plan)}
                    kwargs: dict[str, Any] = {"attack_hook": hook, "failure_plan": failure_plan, "snapshot_every": job.snapshot_every}
                    if kind == "hierarchy":
                        kwargs["policy_overrides"] = dict(cond.policy_overrides) if cond.policy_overrides else None
                    with RssSampler() as rss:
                        result = run_system(world, cond_cfg, job.seed, system, sys_cfg, **kwargs)
                    wall = time.perf_counter() - t0
                    metrics = dict(result.get("metrics", {}))
                    ctx = RunContext(family=fam, world=world, cfg=cond_cfg, seed=job.seed, system=system, sys_cfg=sys_cfg,
                                     condition=cond, result=result, metrics=metrics, run_dir=run_dir, seed_metrics=seed_metrics,
                                     attack_plan=plan, failure_plan=failure_plan, tier=job.tier, quick=job.quick,
                                     runtime_s=wall, peak_rss_mb=rss.peak_mb)
                    extra_cols: dict[str, Any] = {}
                    if job.per_run is not None:
                        try:
                            extra_cols = dict(job.per_run(ctx) or {})
                        except Exception as e:
                            log(f"per_run hook failed for {run_id}: {e}", family=fam)
                            extra_cols = {"per_run_error": str(e)[:200]}
                            with open(log_path, "a") as f:
                                f.write("per_run hook failed:\n" + traceback.format_exc() + "\n")
                except ImportError as e:
                    wall = time.perf_counter() - t0
                    log(f"SKIP {system} ({cond.name}, seed {job.seed}): module unavailable: {e}", family=fam)
                    _record_not_ok(job, cond, system, run_id, "skipped", f"module unavailable: {e}", cond_cfg, sys_cfg, world, wall,
                                   _emit, traceback.format_exc())
                    continue
                except Exception as e:
                    wall = time.perf_counter() - t0
                    log(f"FAILED {system} ({cond.name}, seed {job.seed}) after {wall:.1f}s: {type(e).__name__}: {e}", family=fam)
                    _record_not_ok(job, cond, system, run_id, "failed", f"{type(e).__name__}: {e}", cond_cfg, sys_cfg, world, wall,
                                   _emit, traceback.format_exc())
                    continue
                # ---- outputs ----------------------------------------------------------------------
                outputs = {"metrics": str(run_dir / "metrics.json"), "claims": str(run_dir / "claims.jsonl"),
                           "ground_truth_summary": str(run_dir / "ground_truth_summary.json"), "log": str(log_path),
                           "row": str(run_dir / "row.json")}
                _write_json(run_dir / "metrics.json", metrics)
                n_claims = _write_jsonl(run_dir / "claims.jsonl", result.get("classified", []) or [])
                _write_json(run_dir / "ground_truth_summary.json", gt_summary)
                pol_eff = None
                if kind == "hierarchy":
                    try:
                        pol_eff = asdict(effective_policy(cond_cfg, sys_cfg, cond.policy_overrides))
                    except Exception:
                        pol_eff = None
                extra = {"tier": job.tier, "tag": job.tag, "batch_id": job.batch_id, "condition": _condition_dict(cond),
                         "sys_cfg": sys_cfg, "policy_effective": pol_eff, "attack": attack_info or None, "failure": failure_info,
                         "peak_rss_run_mb": rss.peak_mb, "world_gen_s": gen_s, "n_interactions": int(world.n),
                         "n_claims_written": n_claims, "derived": extra_cols, "quick": job.quick, "argv": job.argv,
                         "wall_runtime_s": wall}
                write_manifest(run_dir, family=fam, run_id=run_id, system=system, seed=job.seed, cfg=cond_cfg, metrics=metrics,
                               dataset_sha256=world.dataset_sha256, runtime_s=wall, status="ok", outputs=outputs, extra=extra)
                row = _base_row(job, cond, system, run_id, sys_cfg, cond_cfg, world)
                row["status"] = "ok"
                row["runtime_s"] = round(wall, 3)
                row["world_gen_s"] = round(gen_s, 3)
                row["peak_rss_mb"] = round(rss.peak_mb, 1)
                row.update({f"metrics.{k}": v for k, v in flatten(metrics).items()})
                row.update(flatten(extra_cols))
                _write_json(run_dir / "row.json", row)
                with open(log_path, "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ok runtime_s={wall:.2f} peak_rss_mb={rss.peak_mb:.0f} "
                            f"n_accepted={metrics.get('n_accepted')} recall_global={metrics.get('recall_global')}\n")
                log(f"seed {job.seed} | {cond.name} | {system}: ok in {wall:.1f}s (rss {rss.peak_mb:.0f} MB) "
                    f"recall_global={_fmt(metrics.get('recall_global'))} recall_cross_team={_fmt(metrics.get('recall_cross_team'))} "
                    f"precision={_fmt(metrics.get('precision_strict'))} n_accepted={metrics.get('n_accepted')}", family=fam)
                seed_metrics[(cond.name, system)] = metrics
                _emit(row)
                del result, ctx
                gc.collect()
        finally:
            if plan is not None:
                try:
                    if hasattr(plan, "restore"):
                        plan.restore()
                except Exception as e:  # pragma: no cover
                    log(f"plan.restore() failed ({e}); restoring from snapshot", family=fam)
                if snapshot is not None:
                    snapshot.restore(world)
    return rows


def _fmt(v: Any) -> str:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "nan"
        return f"{float(v):.3f}"
    except Exception:
        return str(v)


# --------------------------------------------------------------------------
# Grid executor
# --------------------------------------------------------------------------
def _child_main(job: SeedJob, queue: Any) -> None:
    try:
        run_seed(job, lambda row: queue.put(("row", job.seed, row)))
        queue.put(("done", job.seed, None))
    except BaseException as e:  # pragma: no cover - reported to the parent
        queue.put(("crash", job.seed, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
        raise


def run_grid(family: str, systems: list[str], seeds: Iterable[int], cfg: dict[str, Any],
             world_factory: Callable[[dict[str, Any], int], Any] | None = None,
             per_run: Callable[[RunContext], dict[str, Any] | None] | None = None,
             conditions: list[Condition] | None = None, *, results_root: Path | str | None = None, jobs: int = 1,
             tag: str = "", tier: str | None = None, quick: bool = False, snapshot_every: int = 1,
             batch_id: str | None = None) -> list[dict[str, Any]]:
    """Run `systems x seeds x conditions`; returns every CSV row (ok, failed and skipped) of this invocation."""
    results_root = Path(results_root or DEFAULT_RESULTS)
    conditions = list(conditions) if conditions else [Condition()]
    seeds = list(seeds)
    tier = tier or str(cfg.get("tier", ""))
    batch_id = batch_id or new_batch_id()
    csv_path = processed_csv(results_root, family)
    jobs_list = [SeedJob(family=family, seed=s, systems=list(systems), conditions=conditions, cfg=cfg, world_factory=world_factory,
                         per_run=per_run, results_root=results_root, batch_id=batch_id, tag=tag, tier=tier, quick=quick,
                         snapshot_every=snapshot_every) for s in seeds]
    n_planned = sum(len(planned_runs(j)) for j in jobs_list)
    log(f"batch {batch_id}: {len(seeds)} seed(s) x {len(conditions)} condition(s) x {len(systems)} system(s) = {n_planned} runs; "
        f"jobs={jobs}; results -> {results_root}", family=family)
    t_start = time.perf_counter()
    all_rows: list[dict[str, Any]] = []

    def emit(row: dict[str, Any]) -> None:
        append_processed_row(csv_path, row)
        all_rows.append(row)

    if jobs <= 1 or len(jobs_list) <= 1:
        for job in jobs_list:
            run_seed(job, emit)
    else:
        _run_parallel(jobs_list, jobs, emit, family)
    n_ok = sum(1 for r in all_rows if r.get("status") == "ok")
    n_fail = sum(1 for r in all_rows if r.get("status") == "failed")
    n_skip = sum(1 for r in all_rows if r.get("status") == "skipped")
    log(f"batch {batch_id} done in {time.perf_counter() - t_start:.1f}s: {n_ok} ok, {n_fail} failed, {n_skip} skipped "
        f"-> {csv_path}", family=family)
    return all_rows


def _run_parallel(jobs_list: list[SeedJob], jobs: int, emit: Callable[[dict[str, Any]], None], family: str) -> None:
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    queue = ctx.Queue()
    pending = list(jobs_list)
    running: dict[int, tuple[Any, SeedJob]] = {}
    seen: dict[int, set[str]] = {}
    while pending or running:
        while pending and len(running) < jobs:
            job = pending.pop(0)
            p = ctx.Process(target=_child_main, args=(job, queue), daemon=False)
            p.start()
            running[job.seed] = (p, job)
            seen[job.seed] = set()
            log(f"seed {job.seed}: started worker pid {p.pid}", family=family)
        # drain messages
        try:
            kind, seed, payload = queue.get(timeout=0.5)
        except Exception:
            kind = None
        if kind == "row":
            emit(payload)
            seen[seed].add(payload.get("run_id", ""))
        elif kind == "crash":
            log(f"seed {seed}: worker crashed: {payload.splitlines()[0] if payload else ''}", family=family)
        # reap finished workers
        for seed in list(running):
            p, job = running[seed]
            if p.is_alive():
                continue
            p.join()
            # drain anything the worker queued before exiting
            while True:
                try:
                    kind2, seed2, payload2 = queue.get(timeout=0.2)
                except Exception:
                    break
                if kind2 == "row":
                    emit(payload2)
                    seen[seed2].add(payload2.get("run_id", ""))
            missing = [(c, s, rid) for c, s, rid in planned_runs(job) if rid not in seen[seed]]
            if p.exitcode != 0 or missing:
                for cond, system, rid in missing:
                    row_path = job.results_root / "raw" / job.family / rid / "row.json"
                    if row_path.exists():
                        try:
                            with open(row_path) as f:
                                emit(json.load(f))
                            continue
                        except Exception:
                            pass
                    err = f"worker process for seed {seed} exited with code {p.exitcode} before this run finished"
                    log(f"seed {seed}: recording {system} ({cond.name}) as failed: {err}", family=family)
                    try:
                        cond_cfg = with_overrides(job.cfg, cond.cfg_overrides)
                    except Exception:
                        cond_cfg = job.cfg
                    _record_not_ok(job, cond, system, rid, "failed", err, cond_cfg, None, None, 0.0, emit)
            del running[seed]
            log(f"seed {seed}: worker finished (exit code {p.exitcode})", family=family)


# --------------------------------------------------------------------------
# Post-processing helpers used by runners (headline table etc.)
# --------------------------------------------------------------------------
def load_processed(results_root: Path | str, family: str):
    """pandas DataFrame of a processed CSV (empty frame when missing)."""
    import pandas as pd
    p = processed_csv(Path(results_root), family)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, low_memory=False)


def mean_sd(values: Iterable[Any]) -> tuple[float, float, int]:
    arr = np.array([float(v) for v in values if v not in ("", None) and not (isinstance(v, float) and math.isnan(v))], dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        return float("nan"), float("nan"), 0
    return float(arr.mean()), float(arr.std(ddof=1)) if len(arr) > 1 else 0.0, int(len(arr))


def fmt_mean_sd(values: Iterable[Any], digits: int = 3, scale: float = 1.0, unit: str = "") -> str:
    m, s, n = mean_sd(values)
    if n == 0:
        return "n/a"
    return f"{m * scale:.{digits}f} ± {s * scale:.{digits}f}{unit}"


def cadence_dict(c: int) -> dict[str, int]:
    return {"team": int(c), "department": int(c), "region": int(c), "executive": int(c)}


def runner_banner(family: str, args: argparse.Namespace, cfg: dict[str, Any], seeds: list[int], systems: list[str],
                  conditions: list[Condition]) -> None:
    org = cfg["org"]
    log(f"tier={args.tier} quick={args.quick} seeds={seeds} jobs={args.jobs} results={args.results}", family=family)
    log(f"org: n_workers={org['n_workers']} rounds={org['n_rounds']} ipw={org['interactions_per_worker']} "
        f"edge_profile={cfg['models']['edge_profile']} overrides={args.overrides}", family=family)
    log(f"systems={systems}", family=family)
    log(f"conditions ({len(conditions)}): {[c.name for c in conditions]}", family=family)

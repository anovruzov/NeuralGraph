#!/usr/bin/env python3
"""Generate one synthetic workforce dataset and its ground truth (MODULE_SPEC.md Task I.2).

    python data/generators/generate.py --tier tier1 --seed 0 --out data/
    python data/generators/generate.py --tier tier1 --seed 3 --set org.n_workers=300 --out /tmp/x

Writes two files (see data/README.md for the schemas):

    <out>/synthetic_workforce/<tier>_seed<k>.jsonl.gz
        one JSON object per line: ``World.record(i)`` (worker / team / department / region ids, task id,
        the nine structured attributes, prompt, model_response, worker_score, error_labels, rationale,
        confidence, timestamp, round, sensitive) plus ``interaction_id`` (the record's index) and
        ``sensitive_kind`` (the canary kind or null).
    <out>/ground_truth/<tier>_seed<k>.json
        the hidden effects (``Effect.to_dict()`` each: cell, label, delta, kind, validity window, regions,
        scope, minimum discovery layer, n_min, per-layer evidence distribution, true independent support),
        ``ground_truth_summary(world)``, the version-release rounds and the resolved org/world config.

The world is exactly the one the experiment runners generate for the same tier, seed and overrides
(``experiments.common.make_cfg`` + ``world.generate_world``), so ``dataset_sha256`` in the ground-truth file
matches the manifests under results/raw/.  Everything is synthetic: no production data of any company is
read or reproduced (data/README.md).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

BENCH_ROOT = Path(__file__).resolve().parents[2]
for p in (BENCH_ROOT / "src", BENCH_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mycelic_bench.manifest import _jsonable, git_commit  # noqa: E402
from mycelic_bench.vocab import SENSITIVE_KINDS  # noqa: E402
from mycelic_bench.world import generate_world, ground_truth_summary  # noqa: E402

TIERS = ("tier1", "tier2", "tier3")


def make_cfg(tier: str, overrides: list[str]) -> dict[str, Any]:
    """The experiment harness' resolved configuration for a tier (falls back to a local merge)."""
    try:
        from experiments.common import make_cfg as harness_make_cfg
        return harness_make_cfg(tier, overrides)
    except ImportError:  # pragma: no cover - experiments/ absent (data generator used standalone)
        from mycelic_bench.config import apply_dotted, deep_update, load_config, parse_scalar, validate
        cfg = load_config()
        block = cfg.get("tiers", {}).get(tier)
        if block is None:
            raise KeyError(f"unknown tier {tier!r}")
        cfg = deep_update(cfg, {k: v for k, v in block.items() if k in ("org", "world", "policy", "attacks", "security", "models")})
        if "n_regions" not in block.get("org", {}):
            cfg["org"]["n_regions"] = 0
        cfg["tier"] = tier
        for o in overrides:
            k, _, v = o.partition("=")
            apply_dotted(cfg, k.strip(), parse_scalar(v.strip()))
        validate(cfg)
        return cfg


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(world, i: int) -> dict[str, Any]:
    rec = {"interaction_id": int(i)}
    rec.update(world.record(i))
    k = int(world.sensitive_kind[i])
    rec["sensitive_kind"] = SENSITIVE_KINDS[k] if 0 <= k < len(SENSITIVE_KINDS) else None
    return rec


def write_records(world, path: Path, gzip_level: int = 6) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = (lambda p: gzip.open(p, "wt", encoding="utf-8", compresslevel=gzip_level)) if path.suffix == ".gz" \
        else (lambda p: open(p, "w", encoding="utf-8"))
    n = 0
    with opener(path) as f:
        for i in range(world.n):
            f.write(json.dumps(_jsonable(record(world, i)), separators=(",", ":")) + "\n")
            n += 1
    return n


def ground_truth(world, cfg: dict[str, Any], tier: str, seed: int, overrides: list[str]) -> dict[str, Any]:
    return {
        "tier": tier, "seed": int(seed), "overrides": list(overrides),
        "generator": "data/generators/generate.py", "git_commit": git_commit(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provenance": "fully synthetic (mycelic_bench.world.generate_world); no production data of any organisation",
        "dataset_sha256": world.dataset_sha256, "n_interactions": int(world.n), "n_rounds": int(world.n_rounds),
        "release_round": {str(k): int(v) for k, v in world.release_round.items()},
        "config": {"org": cfg.get("org", {}), "world": {k: v for k, v in cfg.get("world", {}).items() if not k.startswith("_")}},
        "summary": ground_truth_summary(world),
        "effects": [e.to_dict() for e in world.effects],
    }


def generate(tier: str, seed: int, out: Path, overrides: list[str] | None = None, gzip_records: bool = True,
             quiet: bool = False) -> dict[str, Any]:
    overrides = list(overrides or [])
    cfg = make_cfg(tier, overrides)
    t0 = time.perf_counter()
    world = generate_world(cfg, seed)
    gen_s = time.perf_counter() - t0
    stem = f"{tier}_seed{seed}"
    rec_path = out / "synthetic_workforce" / (f"{stem}.jsonl.gz" if gzip_records else f"{stem}.jsonl")
    gt_path = out / "ground_truth" / f"{stem}.json"
    t1 = time.perf_counter()
    n = write_records(world, rec_path)
    gt = ground_truth(world, cfg, tier, seed, overrides)
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(_jsonable(gt), f, indent=1)
    write_s = time.perf_counter() - t1
    info = {
        "tier": tier, "seed": seed, "n_records": n, "n_effects": len(world.effects), "dataset_sha256": world.dataset_sha256,
        "records_path": str(rec_path), "records_bytes": rec_path.stat().st_size, "records_sha256": sha256_of(rec_path),
        "ground_truth_path": str(gt_path), "ground_truth_bytes": gt_path.stat().st_size, "ground_truth_sha256": sha256_of(gt_path),
        "world_gen_s": round(gen_s, 2), "write_s": round(write_s, 2),
        "effects_by_kind": gt["summary"]["effects_by_kind"],
    }
    if not quiet:
        print(f"[generate] {stem}: {n} records, {len(world.effects)} effects ({info['effects_by_kind']}), "
              f"world {gen_s:.1f}s, write {write_s:.1f}s")
        print(f"[generate]   {rec_path}  {info['records_bytes'] / 1e6:.2f} MB  sha256 {info['records_sha256'][:16]}")
        print(f"[generate]   {gt_path}  {info['ground_truth_bytes'] / 1e6:.2f} MB  sha256 {info['ground_truth_sha256'][:16]}")
        print(f"[generate]   dataset_sha256 {world.dataset_sha256}")
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", default="tier1", choices=list(TIERS))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=1, help="generate this many consecutive seeds starting at --seed")
    ap.add_argument("--out", default=str(BENCH_ROOT / "data"), help="output root (creates synthetic_workforce/ and ground_truth/)")
    ap.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                    help="dotted config override, repeatable (e.g. --set org.n_workers=300)")
    ap.add_argument("--no-gzip", action="store_true", help="write plain .jsonl instead of .jsonl.gz")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    for s in range(args.seed, args.seed + max(1, args.seeds)):
        generate(args.tier, s, out, args.overrides, gzip_records=not args.no_gzip, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())

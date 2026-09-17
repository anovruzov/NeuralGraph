"""Reproducibility manifests (DESIGN.md §13).

Every run writes `results/raw/<family>/<run_id>/manifest.json` with git commit,
resolved configuration, backend + profiles, seed, timestamp, hardware,
runtime, peak RSS, dataset hash, output paths, metrics and status.  Failed
runs are recorded with `status: failed` and never deleted.
"""

from __future__ import annotations

import json
import os
import platform
import resource
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import ROOT


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def hardware() -> dict[str, Any]:
    mem_gb = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    mem_gb = round(int(line.split()[1]) / 1e6, 1)
    except Exception:
        pass
    return {"platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "ram_gb": mem_gb, "python": platform.python_version(), "numpy": np.__version__, "gpu": "none"}


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, float) and (x != x):
        return None
    if isinstance(x, Path):
        return str(x)
    return x


def write_manifest(run_dir: Path, *, family: str, run_id: str, system: str, seed: int, cfg: dict[str, Any],
                   metrics: dict[str, Any] | None, dataset_sha256: str, runtime_s: float, status: str = "ok",
                   error: str | None = None, outputs: dict[str, str] | None = None, extra: dict[str, Any] | None = None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    models = cfg.get("models", {})
    man = {
        "family": family, "run_id": run_id, "system": system, "seed": seed,
        "git_commit": git_commit(), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": models.get("backend", "simulated-slm"),
        "edge_profile": models.get("edge_profile"), "frontier_profile": models.get("frontier_profile"),
        "profiles": {k: v for k, v in models.get("profiles", {}).items() if k in (models.get("edge_profile"), models.get("frontier_profile"))},
        "limitation": "No real LLM was reachable in the producing environment; edge/frontier behaviour is the simulated profile "
                      "(DESIGN.md §9). Replace with measured profiles via scripts/validate_with_real_slm.py.",
        "config": _jsonable({k: v for k, v in cfg.items() if not k.startswith("_")}),
        "hardware": hardware(), "runtime_s": runtime_s, "peak_rss_mb": peak_rss_mb(),
        "dataset_sha256": dataset_sha256, "status": status, "error": error,
        "outputs": outputs or {}, "metrics": _jsonable(metrics) if metrics is not None else None,
    }
    if extra:
        man.update(_jsonable(extra))
    path = run_dir / "manifest.json"
    with open(path, "w") as f:
        json.dump(man, f, indent=1)
    return path


def append_processed_row(csv_path: Path, row: dict[str, Any]) -> None:
    import csv
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    row = {k: _jsonable(v) for k, v in row.items()}
    exists = csv_path.exists()
    existing_cols: list[str] = []
    if exists:
        with open(csv_path) as f:
            existing_cols = next(csv.reader(f), [])
    cols = existing_cols or list(row)
    new_cols = [c for c in row if c not in cols]
    if new_cols and exists:
        # rewrite with the extended header
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        cols = cols + new_cols
        with open(csv_path, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=cols)
            wr.writeheader()
            for r in rows:
                wr.writerow(r)
    with open(csv_path, "a", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        if not exists:
            wr.writeheader()
        wr.writerow({c: row.get(c, "") for c in cols})

"""Reproducibility manifests and processed-CSV rows (DESIGN.md §13)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from mycelic_bench.config import load_config
from mycelic_bench.manifest import append_processed_row, git_commit, hardware, peak_rss_mb, write_manifest

REQUIRED_KEYS = [
    "family", "run_id", "system", "seed", "git_commit", "timestamp", "backend", "edge_profile", "frontier_profile",
    "profiles", "limitation", "config", "hardware", "runtime_s", "peak_rss_mb", "dataset_sha256", "status", "error",
    "outputs", "metrics",
]


def test_write_manifest_is_valid_json_with_required_keys(tmp_path: Path) -> None:
    cfg = load_config(overrides=["org.n_workers=300"])
    metrics = {"recall_global": np.float64(0.5), "n": np.int64(3), "arr": np.arange(3), "nan": float("nan"),
               "nested": {"path": Path("/x"), "v": np.float32(1.5)}}
    path = write_manifest(tmp_path / "run", family="aggregation", run_id="r0", system="B7_mycelic", seed=0, cfg=cfg,
                          metrics=metrics, dataset_sha256="abc", runtime_s=1.5, outputs={"metrics": "metrics.json"},
                          extra={"note": "x"})
    assert path == tmp_path / "run" / "manifest.json" and path.exists()
    man = json.loads(path.read_text())
    for k in REQUIRED_KEYS:
        assert k in man, k
    assert man["status"] == "ok" and man["error"] is None and man["note"] == "x"
    assert man["backend"] == "simulated-slm" and man["edge_profile"] == cfg["models"]["edge_profile"]
    assert set(man["profiles"]) == {cfg["models"]["edge_profile"], cfg["models"]["frontier_profile"]}
    assert "No real LLM" in man["limitation"]
    assert man["metrics"]["recall_global"] == 0.5 and man["metrics"]["n"] == 3 and man["metrics"]["arr"] == [0, 1, 2]
    assert man["metrics"]["nan"] is None and man["metrics"]["nested"]["path"] == "/x"
    assert man["config"]["org"]["n_workers"] == 300 and not any(k.startswith("_") for k in man["config"])
    assert man["hardware"]["cpu_count"] == hardware()["cpu_count"]
    assert man["runtime_s"] == 1.5 and man["peak_rss_mb"] > 0 and man["outputs"] == {"metrics": "metrics.json"}


def test_failed_run_manifest(tmp_path: Path) -> None:
    cfg = load_config()
    path = write_manifest(tmp_path / "bad", family="f", run_id="r", system="B1_central_keyword", seed=3, cfg=cfg,
                          metrics=None, dataset_sha256="", runtime_s=0.0, status="failed", error="boom")
    man = json.loads(path.read_text())
    assert man["status"] == "failed" and man["error"] == "boom" and man["metrics"] is None


def test_helpers_do_not_crash() -> None:
    assert isinstance(git_commit(), str) and len(git_commit()) > 0
    assert peak_rss_mb() > 0
    hw = hardware()
    assert "platform" in hw and "numpy" in hw


def test_append_processed_row_extends_header(tmp_path: Path) -> None:
    csv_path = tmp_path / "processed" / "aggregation.csv"
    append_processed_row(csv_path, {"system": "B7", "seed": 0, "recall": np.float64(0.5)})
    append_processed_row(csv_path, {"system": "B6", "seed": 1, "recall": 0.4})
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    assert [r["system"] for r in rows] == ["B7", "B6"] and rows[0]["recall"] == "0.5"
    # a row with new columns rewrites the header and keeps old rows aligned
    append_processed_row(csv_path, {"system": "B8", "seed": 2, "recall": 0.6, "poison_rate": 0.1, "extra": "x"})
    with open(csv_path) as f:
        header = next(csv.reader(f))
        f.seek(0)
        rows = list(csv.DictReader(f))
    assert header == ["system", "seed", "recall", "poison_rate", "extra"]
    assert len(rows) == 3
    assert rows[0]["poison_rate"] == "" and rows[2]["poison_rate"] == "0.1" and rows[2]["extra"] == "x"
    # a row missing columns is padded, NaN becomes empty
    append_processed_row(csv_path, {"system": "B9", "seed": 3, "recall": float("nan")})
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    assert rows[3]["recall"] == "" and rows[3]["extra"] == "" and len(rows) == 4
    assert all(set(r) == set(header) for r in rows)

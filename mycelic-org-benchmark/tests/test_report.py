"""experiments/report.py on a tiny synthetic processed CSV (two systems, four seeds, one condition): the Markdown
names both systems and carries the paired-comparison table, summary.json parses with the expected keys, a failed
row is named and excluded, a NaN is counted, and missing families / a missing results directory never crash it."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments import report  # noqa: E402

SYSTEMS = ["B6_hier_no_lineage", "B7_mycelic"]
SEEDS = [1, 2, 3, 4]


def _rows(rng: np.random.Generator) -> list[dict]:
    rows = []
    for s in SYSTEMS:
        base = 0.4 if s == "B6_hier_no_lineage" else 0.6
        for k in SEEDS:
            rows.append({"family": "aggregation", "run_id": f"b0_{s}_default_s{k}", "batch_id": "b0", "status": "ok", "system": s,
                         "kind": "hierarchy", "seed": k, "tier": "tier1", "condition": "default", "n_workers": 300,
                         "malicious_fraction": 0.0, "detector": "none", "layers": 5, "timestamp": f"2026-01-01T00:00:0{k}Z",
                         "runtime_s": 1.0 + rng.random(), "error": "",
                         "metrics.recall_global": float(np.clip(base + 0.05 * rng.standard_normal(), 0, 1)),
                         "metrics.recall_cross_team": float(np.clip(base + 0.1 + 0.05 * rng.standard_normal(), 0, 1)),
                         "metrics.precision_strict": float(np.clip(0.3 + 0.1 * rng.standard_normal(), 0, 1)),
                         "metrics.bytes_transmitted": 1e5 * (1 + rng.random()),
                         "metrics.ttd_global_median": "" if k == 2 else 5 + k,      # one NaN: counted, never a crash
                         "metrics.compression_ratio": 4.0 + rng.random()})
    rows.append({**rows[-1], "run_id": "b0_B7_mycelic_default_s9", "seed": 9, "status": "failed", "error": "RuntimeError: boom"})
    return rows


def _write(path: Path, rows: list[dict]) -> None:
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_report_on_tiny_csv(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write(results / "processed" / "aggregation.csv", _rows(np.random.default_rng(0)))
    out_md, summary = tmp_path / "docs" / "RESULTS.md", results / "summary.json"
    assert report.main(["--results", str(results), "--out", str(out_md), "--summary", str(summary), "--n-boot", "200"]) == 0

    text = out_md.read_text(encoding="utf-8")
    for s in SYSTEMS:
        assert f"`{s}`" in text
    assert "### Paired comparisons vs `B7_mycelic`" in text
    assert "| system | n pairs | mean | ref. mean | Δ | 95% CI (bootstrap) | 95% CI (paired t) | t p | Holm p | Wilcoxon p | Holm p (W) | d_z |" in text
    assert "| `B6_hier_no_lineage` | 4 |" in text                        # four paired seeds
    assert "Failed / skipped runs (1" in text and "b0_B7_mycelic_default_s9" in text and "RuntimeError: boom" in text
    assert "(NaN 1)" in text                                               # the missing ttd value is counted
    assert "Not run / not available" in text and "`poisoning`" in text    # absent families are named explicitly

    js = json.loads(summary.read_text(encoding="utf-8"))
    for k in ("generated_at", "git_commit", "baseline", "n_boot", "sources", "families_missing", "families"):
        assert k in js
    src = js["sources"][0]
    assert src["family"] == "aggregation" and src["rows"] == 9 and src["rows_ok"] == 8 and len(src["sha256"]) == 64
    assert "poisoning" in js["families_missing"]
    fam = js["families"]["aggregation"]
    assert fam["n_ok"] == 8 and fam["n_failed"] == 1 and fam["seeds"] == SEEDS and fam["condition_columns"] == []
    assert fam["failed_runs"][0]["run_id"] == "b0_B7_mycelic_default_s9"
    systems = fam["conditions"]["single condition"]["systems"]
    assert set(systems) == set(SYSTEMS)
    b6 = systems["B6_hier_no_lineage"]
    assert b6["n"] == 4 and b6["seeds"] == SEEDS
    m = b6["metrics"]["metrics.recall_global"]
    assert m["n"] == 4 and m["n_nan"] == 0 and all(m[k] is not None for k in ("mean", "sd", "min", "max"))
    assert b6["metrics"]["metrics.ttd_global_median"]["n_nan"] == 1 and b6["metrics"]["metrics.ttd_global_median"]["n"] == 3
    p = b6["paired_vs_baseline"]["metrics.recall_global"]
    assert p["n_pairs"] == 4 and {"diff", "ci_low", "ci_high", "t_p", "wilcoxon_p", "holm_p", "cohen_dz"} <= set(p)
    assert p["diff"] < 0                                                   # B6 was generated below B7
    assert systems["B7_mycelic"]["paired_vs_baseline"] == {}               # the baseline is not compared with itself


def test_report_survives_missing_results(tmp_path: Path) -> None:
    out_md, summary = tmp_path / "RESULTS.md", tmp_path / "summary.json"
    assert report.main(["--results", str(tmp_path / "nothing"), "--out", str(out_md), "--summary", str(summary)]) == 0
    assert "No processed CSV found" in out_md.read_text(encoding="utf-8")
    assert json.loads(summary.read_text(encoding="utf-8"))["families"] == {}

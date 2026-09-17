"""Figures and tables (MODULE_SPEC.md Task G) run on a tiny synthetic processed-CSV fixture: every figure that
has its CSV is written as PNG + PDF, missing CSVs degrade to a warning, failed rows are dropped, and the
markdown tables appear."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYSTEMS = ["B1_central_keyword", "B3_central_llm_summary", "B6_hier_no_lineage", "B7_mycelic", "ORACLE_central_stats"]
LAYERS = ["team", "department", "region", "executive"]


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _metrics(rng, system: str, seed: int, **extra) -> dict:
    base = {"B1_central_keyword": 0.55, "B3_central_llm_summary": 0.45, "B6_hier_no_lineage": 0.35, "B7_mycelic": 0.5,
            "ORACLE_central_stats": 0.7}[system]
    central = system.startswith(("B1", "B2", "B3", "ORACLE"))
    row = {
        "family": "x", "run_id": f"{system}_s{seed}", "batch_id": "b0", "status": "ok", "system": system, "kind": "baseline" if central else "hierarchy",
        "seed": seed, "tier": "tier1", "condition": "default", "n_workers": 300, "malicious_fraction": 0.0, "detector": "none",
        "layers": 0 if central else 5, "compression": "order_capped", "sketch_order": 3, "cadence": 1, "profile": "sim-3b",
        "profile_kind": "synthetic", "quote_policy": "none", "k_anonymity": 3, "questioning": "none", "runtime_s": 3.0 + rng.random(),
        "metrics.runtime_s": 2.0 + rng.random(), "metrics.peak_rss_mb": 300 + 50 * rng.random(),
        "metrics.recall_global": float(np.clip(base + 0.05 * rng.standard_normal(), 0, 1)),
        "metrics.recall_cross_team": float(np.clip(base + 0.1 + 0.05 * rng.standard_normal(), 0, 1)),
        "metrics.recall_local": float(np.clip(base + 0.2 + 0.05 * rng.standard_normal(), 0, 1)),
        "metrics.precision_strict": float(np.clip(0.3 + 0.1 * rng.standard_normal(), 0, 1)),
        "metrics.poison.poison_promotion_rate": float(np.clip(0.1 * rng.random(), 0, 1)),
        "metrics.poison.poison_by_layer.team": 3, "metrics.poison.poison_by_layer.department": 2,
        "metrics.poison.poison_by_layer.region": 1, "metrics.poison.poison_by_layer.executive": 1,
        "metrics.raw_sensitive_leakage": 1.0 if central else 0.0, "metrics.fraction_raw_exposed": 1.0 if central else 0.02,
        "metrics.reconstructability": 0.0, "metrics.compression_ratio": 1.0 if central else 5.0 + rng.random(),
        "metrics.cost_usd_est": 2.0 + rng.random() if central else 0.05, "metrics.bytes_transmitted": 2.6e6 if central else 4e5 + 1e4 * rng.random(),
        "metrics.tokens_to_cloud": 4e5 if central else 0, "metrics.ttd_global_median": 10 + rng.integers(0, 10),
        "metrics.ttd_cross_team_median": 8 + rng.integers(0, 10), "metrics.latency_ms_est": 100 + 10 * rng.random(),
        "metrics.energy_j_est": 50.0, "metrics.failure_reasons.statistical_power": int(rng.integers(0, 4)),
        "metrics.failure_reasons.compression_loss": int(rng.integers(0, 2)),
    }
    for l in LAYERS:
        row[f"metrics.fidelity.{l}.useful_fact_recall"] = float(np.clip(0.4 + 0.1 * rng.random(), 0, 1))
        row[f"metrics.fidelity.{l}.survived"] = 1.0
        row[f"metrics.fidelity.{l}.dropped"] = 0.0
    row.update(extra)
    return row


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> Path:
    rng = np.random.default_rng(0)
    root = tmp_path_factory.mktemp("results")
    proc = root / "processed"
    seeds = [0, 1, 2]
    agg = [_metrics(rng, s, k, family="aggregation") for s in SYSTEMS for k in seeds]
    agg.append(_metrics(rng, "B7_mycelic", 9, family="aggregation", status="failed"))   # must be dropped, not plotted
    _write(proc / "aggregation.csv", agg)
    _write(proc / "headline.csv", [_metrics(rng, s, k, family="headline", malicious_fraction=0.1) for s in SYSTEMS for k in seeds])
    _write(proc / "scaling.csv", [_metrics(rng, s, k, family="scaling", n_workers=n) for s in ("B7_mycelic", "B1_central_keyword")
                                  for n in (100, 1000, 10000) for k in seeds])
    _write(proc / "compression.csv", [_metrics(rng, "B7_mycelic", k, family="compression", compression=c, sketch_order=o, cadence=1,
                                               **{"metrics.compression_ratio": r})
                                      for (c, o, r) in (("full_sketch", 3, 0.8), ("order_capped", 3, 1.2), ("order_capped", 2, 4.0),
                                                        ("significant_cells_only", 3, 9.0), ("claims_only", 1, 60.0)) for k in seeds])
    _write(proc / "poisoning.csv", [_metrics(rng, s, k, family="poisoning", malicious_fraction=f) for s in ("B6_hier_no_lineage", "B7_mycelic", "B1_central_keyword")
                                    for f in (0.0, 0.1, 0.3) for k in seeds])
    _write(proc / "privacy.csv", [_metrics(rng, "B7_mycelic", k, family="privacy", quote_policy=q, k_anonymity=kk) for q, kk in
                                  (("none", 3), ("none", 0), ("redacted", 3), ("raw", 0)) for k in seeds]
           + [_metrics(rng, s, k, family="privacy", quote_policy="raw", k_anonymity=0) for s in ("B1_central_keyword",) for k in seeds])
    _write(proc / "models.csv", [_metrics(rng, s, k, family="models", profile=p) for s in ("B7_mycelic",) for p in ("sim-1b", "sim-7b", "sim-perfect")
                                 for k in seeds])
    _write(proc / "hierarchy.csv", [_metrics(rng, "B7_mycelic", k, family="hierarchy", layers=L) for L in (1, 3, 5) for k in seeds])
    _write(proc / "failures.csv", [dict(_metrics(rng, s, k, family="failures", failure_kind=fk, failure_rate=fr), knowledge_survival=float(rng.random()),
                                        useful_discovery_survival=float(rng.random()), lineage_survival=float(rng.random()))
                                   for s in ("B7_mycelic", "B6_hier_no_lineage") for fk in ("random_team", "partition") for fr in (0.05, 0.2) for k in seeds])
    return root


def test_figures_run_on_fixture(results: Path, tmp_path: Path) -> None:
    from experiments import figures
    out = tmp_path / "figs"
    status = figures.main(["--results", str(results), "--out", str(out), "--strict"])
    assert status and all(status.values()), status
    for name, _ in figures.FIGURES:
        assert (out / f"{name}.png").exists() and (out / f"{name}.png").stat().st_size > 1000, name
        assert (out / f"{name}.pdf").exists(), name
    assert len(list(out.glob("*.png"))) == len(figures.FIGURES)


def test_figures_degrade_gracefully_without_csvs(tmp_path: Path, capsys) -> None:
    from experiments import figures
    empty = tmp_path / "empty"
    (empty / "processed").mkdir(parents=True)
    out = tmp_path / "figs"
    status = figures.main(["--results", str(empty), "--out", str(out)])
    assert status and not any(status.values())
    assert not list(out.glob("*.png"))
    assert "missing CSV" in capsys.readouterr().out
    # a single CSV produces only the figures that need it
    rng = np.random.default_rng(1)
    _write(empty / "processed" / "aggregation.csv", [_metrics(rng, s, k) for s in ("B7_mycelic", "B6_hier_no_lineage") for k in (0, 1)])
    status = figures.main(["--results", str(empty), "--out", str(out), "--strict"])
    assert status["F5_cross_team_discovery"] and status["F7_accuracy_vs_cost"] and status["investor_comparison"] and status["fig_headline_table"]
    assert not status["F1_discovery_vs_org_size"] and not status["F6_knowledge_survival"]


def test_tables_run_on_fixture(results: Path, tmp_path: Path) -> None:
    from experiments import tables
    out = tmp_path / "tables"
    written = tables.main(["--results", str(results), "--out", str(out), "--n-boot", "200", "--families", "aggregation,headline"])
    for name in ("executive_comparison", "layer_fidelity", "failure_reasons", "stats_aggregation", "stats_headline"):
        assert written.get(name) is not None and Path(written[name]).exists(), name
    exec_md = (out / "executive_comparison.md").read_text()
    assert "B7 Mycelic" in exec_md and "Oracle (upper bound)" in exec_md and "|" in exec_md
    stats_md = (out / "stats_aggregation.md").read_text()
    assert "Cohen" in stats_md and "Holm" in stats_md and "B7_mycelic" in stats_md
    fid = (out / "layer_fidelity.md").read_text()
    assert "useful_fact_recall" in fid and "executive" in fid
    # no processed CSVs -> tables skipped with warnings, nothing raised
    empty = tmp_path / "empty2"
    (empty / "processed").mkdir(parents=True)
    written = tables.main(["--results", str(empty), "--out", str(tmp_path / "t2"), "--n-boot", "50"])
    assert all(v is None for v in written.values())

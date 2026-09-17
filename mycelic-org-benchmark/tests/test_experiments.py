"""Experiment harness (MODULE_SPEC.md Task H): the aggregation runner works programmatically on the small
config into a temporary results root, writes manifests / metrics / claims per run and one flat CSV row per
run, and records failed runs with `status: failed` instead of dropping them."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SMALL = [
    "org.n_workers=300", "org.n_rounds=8", "world.n_local_findings=20", "world.n_cross_team_findings=5",
    "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2",
]
SET_ARGS = [a for o in SMALL for a in ("--set", o)]


def _read_csv(path: Path) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def aggregation_results(tmp_path_factory) -> Path:
    from experiments.aggregation import run as agg
    results = tmp_path_factory.mktemp("results")
    rc = agg.main(["--tier", "tier1", "--quick", "--seeds", "1", "--systems", "B7_mycelic,B6_hier_no_lineage",
                   "--only", "aggregation", "--results", str(results), "--tag", "pytest"] + SET_ARGS)
    assert rc == 0
    return results


def test_aggregation_runner_writes_csv_manifests_and_metrics(aggregation_results: Path) -> None:
    results = aggregation_results
    csv_path = results / "processed" / "aggregation.csv"
    assert csv_path.exists()
    rows = _read_csv(csv_path)
    assert [r["system"] for r in rows] == ["B7_mycelic", "B6_hier_no_lineage"]
    cols = set(rows[0])
    for c in ("family", "run_id", "batch_id", "status", "system", "kind", "seed", "tier", "tag", "condition", "n_workers",
              "n_interactions", "n_rounds", "n_teams", "n_departments", "n_regions", "dataset_sha256", "malicious_fraction",
              "detector", "layers", "compression", "sketch_order", "cadence", "profile", "profile_kind", "quote_policy",
              "k_anonymity", "questioning", "runtime_s", "world_gen_s", "peak_rss_mb", "timestamp", "error",
              "metrics.n_accepted", "metrics.precision_strict", "metrics.recall_local", "metrics.recall_cross_team",
              "metrics.recall_global", "metrics.false_association_rate", "metrics.compression_ratio",
              "metrics.raw_sensitive_leakage", "metrics.bytes_transmitted", "metrics.tokens_to_cloud", "metrics.cost_usd_est",
              "metrics.poison.poison_promotion_rate", "metrics.fidelity.executive.useful_fact_recall",
              "metrics.categories.exact"):
        assert c in cols, c
    for r in rows:
        assert r["status"] == "ok" and r["family"] == "aggregation" and r["tier"] == "tier1" and r["tag"] == "pytest"
        assert r["kind"] == "hierarchy" and r["n_workers"] == "300" and r["n_interactions"] == "3000" and r["n_rounds"] == "8"
        assert r["profile_kind"] == "synthetic" and r["profile"] == "sim-3b"
        assert float(r["runtime_s"]) > 0 and float(r["peak_rss_mb"]) > 0 and float(r["world_gen_s"]) >= 0
        assert 0.0 <= float(r["metrics.recall_local"]) <= 1.0 and int(float(r["metrics.n_accepted"])) > 0
        assert float(r["metrics.raw_sensitive_leakage"]) == 0.0 and r["metrics.recall_global"] == ""   # NaN -> empty
        assert r["error"] == ""
        run_dir = results / "raw" / "aggregation" / r["run_id"]
        assert run_dir.is_dir()
        for name in ("manifest.json", "metrics.json", "claims.jsonl", "ground_truth_summary.json", "log.txt", "row.json"):
            assert (run_dir / name).exists(), name
        man = json.loads((run_dir / "manifest.json").read_text())
        for k in ("git_commit", "config", "backend", "edge_profile", "frontier_profile", "profiles", "seed", "timestamp",
                  "hardware", "runtime_s", "peak_rss_mb", "dataset_sha256", "outputs", "metrics", "status", "limitation",
                  "policy_effective", "sys_cfg", "condition", "batch_id", "tier"):
            assert k in man, k
        assert man["status"] == "ok" and man["backend"] == "simulated-slm" and man["system"] == r["system"]
        assert man["dataset_sha256"].startswith(r["dataset_sha256"]) and man["config"]["org"]["n_workers"] == 300
        assert man["metrics"]["n_accepted"] == int(float(r["metrics.n_accepted"]))
        metrics = json.loads((run_dir / "metrics.json").read_text())
        assert metrics["n_accepted"] == man["metrics"]["n_accepted"] and "fidelity" in metrics and "poison" in metrics
        claims = [json.loads(line) for line in (run_dir / "claims.jsonl").read_text().splitlines()]
        assert len(claims) == man["n_claims_written"] and all("category" in c and "cell" in c for c in claims)
        assert sum(1 for c in claims if c["status"] == "accepted") == metrics["n_accepted"]
    # both systems ran on the same world (paired by seed)
    assert rows[0]["dataset_sha256"] == rows[1]["dataset_sha256"] and rows[0]["batch_id"] == rows[1]["batch_id"]
    assert rows[0]["detector"] == "none" and rows[0]["compression"] == "order_capped" and rows[0]["sketch_order"] == "3"


def test_failed_runs_are_recorded_not_dropped(tmp_path: Path) -> None:
    from experiments.common import Condition, make_cfg, run_grid
    cfg = make_cfg("tier1", SMALL)
    conds = [Condition(name="default"), Condition(name="bad-config", cfg_overrides={"policy.compression": "no_such_policy"})]
    rows = run_grid("smoke", ["B6_hier_no_lineage", "NOT_A_SYSTEM"], [0], cfg, conditions=conds, results_root=tmp_path, tier="tier1",
                    quick=True)
    assert len(rows) == 4
    by = {(r["condition"], r["system"]): r for r in rows}
    assert by[("default", "B6_hier_no_lineage")]["status"] == "ok"
    assert by[("default", "NOT_A_SYSTEM")]["status"] == "failed" and "not in configs/experiments.yaml" in by[("default", "NOT_A_SYSTEM")]["error"]
    assert by[("bad-config", "B6_hier_no_lineage")]["status"] == "failed" and "no_such_policy" in by[("bad-config", "B6_hier_no_lineage")]["error"]
    csv_rows = _read_csv(tmp_path / "processed" / "smoke.csv")
    assert len(csv_rows) == 4 and sorted(r["status"] for r in csv_rows) == ["failed", "failed", "failed", "ok"]
    for r in csv_rows:
        run_dir = tmp_path / "raw" / "smoke" / r["run_id"]
        man = json.loads((run_dir / "manifest.json").read_text())
        assert man["status"] == r["status"]
        if r["status"] == "failed":
            assert man["error"] and man["metrics"] is None and (run_dir / "log.txt").exists()
            assert "Traceback" in (run_dir / "log.txt").read_text() or "configuration error" in man["error"] or "not in configs" in man["error"]
    # a second invocation appends (append-only processed CSV) and never deletes earlier rows
    run_grid("smoke", ["B6_hier_no_lineage"], [0], cfg, conditions=[Condition(name="default")], results_root=tmp_path, tier="tier1", quick=True)
    assert len(_read_csv(tmp_path / "processed" / "smoke.csv")) == 5
    assert len(list((tmp_path / "raw" / "smoke").iterdir())) == 5


def test_make_cfg_and_flatten() -> None:
    from experiments.common import flatten, make_cfg, with_overrides, world_key
    cfg = make_cfg("tier1", SMALL)
    assert cfg["org"]["n_workers"] == 300 and cfg["org"]["n_rounds"] == 8 and cfg["tier"] == "tier1"
    assert cfg["org"]["n_regions"] == 0 and cfg["world"]["n_global_findings"] == 3
    with pytest.raises(KeyError):
        make_cfg("tier9")
    cfg2 = with_overrides(cfg, {"org.n_workers": 400})
    assert cfg2["org"]["n_workers"] == 400 and cfg["org"]["n_workers"] == 300
    assert world_key(cfg, 0) != world_key(cfg2, 0) and world_key(cfg, 0) != world_key(cfg, 1)
    flat = flatten({"a": {"b": 1, "c": [1, 2]}, "nan": float("nan"), "long": list(range(40)), "empty": {}})
    assert flat["a.b"] == 1 and flat["a.c.0"] == 1 and flat["a.c.1"] == 2 and flat["nan"] == "" and flat["empty"] == ""
    assert isinstance(flat["long"], str) and json.loads(flat["long"]) == list(range(40))

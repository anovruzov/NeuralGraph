"""Baselines (MODULE_SPEC.md Task A): every baseline runs through `runner.run_system`, returns the metric
dictionary of the spec with sane ranges, and reads no ground truth.

The small config (300 workers, 8 rounds, 3000 records) keeps the whole file under a minute; the
absolute recall values are not asserted (they are what the Tier-1 experiments measure), only their
well-formedness and the invariants that follow from the information flow:

* centralized systems ship every raw record -> raw_sensitive_leakage == 1.0, bytes_off_device == raw bytes;
* B0 never leaves the device -> leakage 0.0, bytes_off_device 0, compression ratio undefined (None);
* ORACLE is an upper-bound reference on exact data: no accepted claim is population-false.
"""

from __future__ import annotations

import ast
import functools
import math
from pathlib import Path

import numpy as np
import pytest

from mycelic_bench.config import load_config
from mycelic_bench.runner import run_system
from mycelic_bench.world import generate_world

SMALL = [
    "org.n_workers=300", "org.n_rounds=8", "world.n_local_findings=20", "world.n_cross_team_findings=5",
    "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2",
]
BASELINES = ["B0_isolated", "B1_central_keyword", "B2_central_rag", "B3_central_llm_summary", "B4_majority_vote",
             "ORACLE_central_stats"]
CENTRALIZED = [b for b in BASELINES if b != "B0_isolated"]

# MODULE_SPEC.md: "Metric dictionary every system must return"
REQUIRED_METRICS = [
    "n_accepted", "categories", "precision_strict", "precision_lenient", "false_discovery_rate", "false_association_rate",
    "recall_local", "recall_cross_team", "recall_global", "recall_temporal", "recall_contradiction", "ttd_global_median",
    "ttd_cross_team_median", "evidence_coverage", "lineage_correctness", "source_diversity", "ece", "revision_accuracy",
    "stale_persistence", "contradiction_f1", "raw_sensitive_leakage", "n_canaries_exposed", "bytes_off_device",
    "fraction_raw_exposed", "reconstructability", "bytes_transmitted", "compression_ratio", "tokens", "tokens_to_cloud",
    "model_calls", "latency_ms_est", "energy_j_est", "cost_usd_est", "runtime_s", "poison", "failure_reasons", "fidelity",
]
POISON_KEYS = ["attack_records", "poison_claims_at_root", "poison_promotion_rate"]
FRACTIONS = ["precision_strict", "precision_lenient", "false_discovery_rate", "false_association_rate", "recall_local",
             "recall_cross_team", "recall_global", "recall_temporal", "recall_contradiction", "raw_sensitive_leakage",
             "fraction_raw_exposed", "reconstructability", "evidence_coverage", "lineage_correctness"]
NON_NEGATIVE = ["bytes_off_device", "bytes_transmitted", "tokens", "tokens_to_cloud", "model_calls", "latency_ms_est",
                "energy_j_est", "cost_usd_est", "runtime_s", "n_accepted", "n_canaries_exposed"]


def _isnan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


@functools.lru_cache(maxsize=None)
def small_world():
    cfg = load_config(overrides=SMALL)
    return cfg, generate_world(cfg, 0)


@functools.lru_cache(maxsize=None)
def run(name: str):
    cfg, w = small_world()
    return run_system(w, cfg, 0, name)


@pytest.mark.parametrize("name", BASELINES)
def test_metric_dictionary_and_ranges(name: str) -> None:
    cfg, w = small_world()
    res = run(name)
    assert set(res) >= {"metrics", "classified", "discovered", "snapshots"}
    m = res["metrics"]
    missing = [k for k in REQUIRED_METRICS if k not in m]
    assert not missing, missing
    n_groups = len({e.group for e in w.effects if e.kind == "contradiction"})
    if n_groups:
        assert "correct_resolution_rate" in m and "incorrect_resolution_rate" in m
        assert 0.0 <= m["correct_resolution_rate"] <= 1.0 and 0.0 <= m["incorrect_resolution_rate"] <= 1.0
    for k in FRACTIONS:
        v = m[k]
        assert _isnan(v) or 0.0 <= float(v) <= 1.0, (k, v)
    for k in NON_NEGATIVE:
        assert float(m[k]) >= 0, (k, m[k])
    assert isinstance(m["categories"], dict) and set(m["categories"]) >= {"exact", "false", "poison"}
    assert m["n_accepted"] == sum(m["categories"].values())
    for k in POISON_KEYS:
        assert k in m["poison"]
    assert m["poison"]["attack_records"] == 0 and m["poison"]["poison_claims_at_root"] == 0   # clean world
    assert isinstance(m["failure_reasons"], dict) and all(isinstance(v, int) and v >= 0 for v in m["failure_reasons"].values())
    assert set(m["fidelity"]) == {"executive"} and "useful_fact_recall" in m["fidelity"]["executive"]
    # per-kind counts match the world; recall is NaN only when the kind is absent
    kinds = {"local": "recall_local", "cross_team": "recall_cross_team", "global": "recall_global"}
    for kind, key in kinds.items():
        n_e = sum(1 for e in w.effects if e.kind == kind)
        assert m[f"n_{kind}"] == n_e
        assert (n_e == 0) == _isnan(m[key]), (kind, m[key])
    assert m["ece"] >= 0 or _isnan(m["ece"])
    # snapshots: one knowledge base per round (cadence may leave early rounds empty but present)
    assert set(res["snapshots"]) == set(range(w.n_rounds))
    # every classified claim carries the evaluator's fields
    for c in res["classified"][:50]:
        assert {"claim_id", "cell", "label", "sign", "scope", "category", "status", "population_true"} <= set(c)


def test_centralized_ship_everything_and_leak_every_canary() -> None:
    cfg, w = small_world()
    from mycelic_bench.evaluate import raw_record_bytes
    raw = raw_record_bytes(w)
    n_canaries = sum(1 for c in w.canary if c)
    assert n_canaries > 0
    for name in CENTRALIZED:
        m = run(name)["metrics"]
        assert m["raw_sensitive_leakage"] == 1.0, name
        assert m["n_canaries_exposed"] == n_canaries, name
        assert m["bytes_off_device"] == m["bytes_transmitted"] > 0, name
        assert abs(m["bytes_off_device"] - raw) <= 0.02 * raw, (name, m["bytes_off_device"], raw)
        assert 0.98 <= m["fraction_raw_exposed"] <= 1.02, name
        assert m["compression_ratio"] is not None and 0.98 <= m["compression_ratio"] <= 1.02, name
        assert m["bytes_above_team"] == 0
        assert m["stats"]["records_received"] == w.n, name          # every record crosses the wire
        assert m["stats"]["records_ingested"] <= w.n, name          # exact copies are stored once (content hash)


def test_b0_never_leaves_the_device() -> None:
    cfg, w = small_world()
    m = run("B0_isolated")["metrics"]
    assert m["raw_sensitive_leakage"] == 0.0 and m["n_canaries_exposed"] == 0
    assert m["bytes_off_device"] == 0 and m["bytes_transmitted"] == 0 and m["fraction_raw_exposed"] == 0.0
    assert m["compression_ratio"] is None            # raw / 0 -> reported as None (MODULE_SPEC Task A)
    assert m["tokens_to_cloud"] == 0 and m["tokens"] > 0 and m["model_calls"] > 0
    assert m["cost_usd_est"] >= 0
    # claims are worker-scoped only
    for c in run("B0_isolated")["classified"]:
        assert c["scope"].startswith("worker:")


def test_cloud_accounting_for_frontier_systems() -> None:
    """B1-B4 bill an analyst / reader / retrieval to the frontier model; ORACLE is a reference (no cloud tokens)."""
    for name in ("B1_central_keyword", "B2_central_rag", "B3_central_llm_summary", "B4_majority_vote"):
        m = run(name)["metrics"]
        assert m["tokens_to_cloud"] > 0 and m["cost_usd_est"] > 0, name
    m3 = run("B3_central_llm_summary")["metrics"]
    cfg, w = small_world()
    # B3 reads every record's text at least once (window chunks) -> tokens >= 180 per record chunked
    window = int(cfg["systems"]["B3_central_llm_summary"].get("window_records", 200))
    assert m3["tokens_to_cloud"] >= (w.n // window) * window * 180
    m2 = run("B2_central_rag")["metrics"]
    assert m2["tokens_to_cloud"] >= m3["tokens_to_cloud"] or m2["stats"]["queries_used"] > 0


def test_oracle_is_an_upper_bound_on_exact_data() -> None:
    cfg, w = small_world()
    mo = run("ORACLE_central_stats")["metrics"]
    # exact, copy-deduplicated data: nothing population-false is accepted on a clean world
    assert mo["false_association_rate"] <= 0.05   # BH at q=0.05 on exact data
    assert mo["categories"]["false"] == 0 and mo["categories"]["poison"] == 0
    assert mo["n_accepted"] > 0 and mo["recall_local"] > 0.0
    assert mo["tokens_to_cloud"] == 0
    # any claims it accepts at scopes are nested in the org
    scopes = {c["scope"].split(":")[0] for c in run("ORACLE_central_stats")["classified"]}
    assert scopes <= {"executive", "region", "department", "team"}


def test_baselines_are_deterministic_per_seed() -> None:
    cfg, w = small_world()
    a = run_system(w, cfg, 0, "B1_central_keyword")["metrics"]
    b = run_system(w, cfg, 0, "B1_central_keyword")["metrics"]
    for k in ("n_accepted", "recall_local", "precision_strict", "bytes_off_device", "tokens_to_cloud"):
        assert a[k] == b[k], k


def test_baselines_module_reads_no_ground_truth() -> None:
    """Static check on baselines.py: no `Effect` import and no `.effects` / `.p_true` / `.true_labels` access
    (the same rule tests/test_no_cheating.py enforces tree-wide)."""
    path = Path(__file__).resolve().parents[1] / "src" / "mycelic_bench" / "baselines.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    forbidden_attrs = {"effects", "p_true", "true_labels", "effect_active_mask", "effect_matches", "canary"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "world":
            names = {a.name for a in node.names}
            if names & {"Effect", "GroundTruth"} or "*" in names:
                bad.append(f"line {node.lineno}: imports {sorted(names)} from world")
        elif isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
            bad.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in forbidden_attrs:
            bad.append(f"line {node.lineno}: [{node.slice.value!r}]")
    assert not bad, "\n".join(bad)
    # and the view it receives carries no truth arrays
    cfg, w = small_world()
    view = w.records()
    assert not any(hasattr(view, a) for a in ("effects", "p_true", "true_labels", "canary", "is_copy"))
    # the record dict handed to centralized systems does not contain true labels either
    rec = w.record(0)
    assert "true_labels" not in rec and "error_labels" in rec


def test_all_baselines_use_the_shared_search() -> None:
    src = (Path(__file__).resolve().parents[1] / "src" / "mycelic_bench" / "baselines.py").read_text()
    assert "from .hypothesis import" in src and "search" in src and "rate_test" in src
    assert "scipy.stats" not in src

"""Tests for mycelic_bench.stats (paired, seed-matched statistics)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from mycelic_bench import stats as st


def test_paired_bootstrap_ci_coverage():
    """Paired bootstrap CI covers the true mean shift ~95% of the time over 200 replications."""
    true_shift = 0.3
    n_pairs = 20
    covered = 0
    reps = 200
    for rep in range(reps):
        rng = np.random.default_rng(1000 + rep)
        world = rng.normal(0.5, 0.15, n_pairs)          # shared per-seed world effect
        b = world + rng.normal(0, 0.05, n_pairs)
        a = world + true_shift + rng.normal(0, 0.05, n_pairs)
        res = st.paired_summary(a, b, n_boot=1500, seed=rep)
        assert res["n"] == n_pairs
        assert res["ci_low"] <= res["diff"] <= res["ci_high"]
        if res["ci_low"] <= true_shift <= res["ci_high"]:
            covered += 1
    coverage = covered / reps
    # nominal 0.95; percentile bootstrap at n=20 undercovers slightly, and 200 reps has SE ~ 1.5%
    assert 0.89 <= coverage <= 0.995, coverage


def test_paired_summary_reports_effect_size_with_every_p():
    rng = np.random.default_rng(0)
    a = rng.normal(0.6, 0.1, 30)
    b = a - 0.05 + rng.normal(0, 0.02, 30)
    res = st.paired_summary(a, b, n_boot=2000)
    for key in ("mean_a", "mean_b", "diff", "ci_low", "ci_high", "t_p", "wilcoxon_p", "cohen_dz", "n"):
        assert key in res
    assert not math.isnan(res["cohen_dz"])
    assert 0 <= res["t_p"] <= 1 and 0 <= res["wilcoxon_p"] <= 1
    assert res["t_p"] < 0.001 and res["wilcoxon_p"] < 0.001
    assert res["diff"] == pytest.approx(res["mean_a"] - res["mean_b"])


def test_cohen_dz_sign_and_magnitude():
    rng = np.random.default_rng(3)
    b = rng.normal(0.4, 0.1, 25)
    a = b + 0.1 + rng.normal(0, 0.03, 25)
    up = st.paired_summary(a, b, n_boot=500)
    down = st.paired_summary(b, a, n_boot=500)
    assert up["cohen_dz"] > 0 and up["diff"] > 0
    assert down["cohen_dz"] < 0 and down["diff"] < 0
    assert up["cohen_dz"] == pytest.approx(-down["cohen_dz"])
    d = a - b
    assert up["cohen_dz"] == pytest.approx(d.mean() / d.std(ddof=1))
    same = st.paired_summary(a, a, n_boot=200)
    assert same["cohen_dz"] == 0.0 and same["diff"] == 0.0 and same["t_p"] == 1.0 and same["wilcoxon_p"] == 1.0


def test_paired_summary_degenerate_and_nan_cases():
    const = st.paired_summary([2.0, 2.0, 2.0], [1.0, 1.0, 1.0], n_boot=100)
    assert const["diff"] == 1.0 and math.isinf(const["cohen_dz"]) and const["cohen_dz"] > 0
    assert const["ci_low"] == const["ci_high"] == 1.0
    single = st.paired_summary([1.0], [0.5], n_boot=100)
    assert single["n"] == 1 and math.isnan(single["t_p"]) and math.isnan(single["cohen_dz"])
    with_nan = st.paired_summary([1.0, np.nan, 3.0, 4.0], [0.5, 1.0, np.nan, 3.0], n_boot=100)
    assert with_nan["n"] == 2 and with_nan["diff"] == pytest.approx(0.75)
    empty = st.paired_summary([], [], n_boot=100)
    assert empty["n"] == 0 and math.isnan(empty["diff"])
    with pytest.raises(ValueError):
        st.paired_summary([1, 2, 3], [1, 2])


def test_holm_monotone_and_not_below_raw():
    rng = np.random.default_rng(7)
    for _ in range(50):
        m = int(rng.integers(1, 15))
        p = rng.uniform(0, 1, m) ** rng.uniform(0.5, 3)
        adj = st.holm(p)
        assert adj.shape == p.shape
        assert np.all(adj >= p - 1e-12)
        assert np.all(adj <= 1.0)
        order = np.argsort(p)
        assert np.all(np.diff(adj[order]) >= -1e-12)     # monotone in the raw ordering
    # hand-checked example: p = [0.01, 0.04, 0.03] -> [0.03, 0.04, 0.06]
    assert st.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    # NaN entries are ignored and stay NaN; family size excludes them
    adj = st.holm([0.01, np.nan, 0.5])
    assert math.isnan(adj[1]) and adj[0] == pytest.approx(0.02) and adj[2] == pytest.approx(0.5)
    assert st.holm([]).size == 0
    assert st.holm([0.2]) == pytest.approx([0.2])


def test_summarize_t_ci():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    s = st.summarize(x)
    assert s["n"] == 5 and s["mean"] == 3.0 and s["sd"] == pytest.approx(np.std(x, ddof=1))
    # t(0.975, 4) = 2.776 -> half width 2.776 * sd / sqrt(5)
    half = 2.7764451 * s["sd"] / math.sqrt(5)
    assert s["ci_low"] == pytest.approx(3.0 - half, rel=1e-5) and s["ci_high"] == pytest.approx(3.0 + half, rel=1e-5)
    one = st.summarize([2.0])
    assert one["n"] == 1 and one["mean"] == 2.0 and math.isnan(one["sd"]) and math.isnan(one["ci_low"])
    none = st.summarize([np.nan, np.nan])
    assert none["n"] == 0 and math.isnan(none["mean"])


def test_summarize_groups():
    df = pd.DataFrame({"system": ["A"] * 3 + ["B"] * 2, "x": [1, 2, 3, 10, 12], "m": [0.1, 0.2, 0.3, 0.5, np.nan]})
    t = st.summarize_groups(df, "m", "system")
    assert list(t.columns) == ["system", "mean", "sd", "ci_low", "ci_high", "n"]
    assert t.set_index("system").loc["A", "n"] == 3 and t.set_index("system").loc["B", "n"] == 1
    with pytest.raises(KeyError):
        st.summarize_groups(df, "missing", "system")


def test_compare_systems_pairs_by_seed_and_holm_corrects_family():
    rng = np.random.default_rng(11)
    seeds = np.arange(30)
    world = rng.normal(0.5, 0.1, 30)
    rows = []
    for s in seeds:
        rows.append({"system": "B7_mycelic", "seed": s, "m": world[s]})
        rows.append({"system": "B1_central_keyword", "seed": s, "m": world[s] - 0.08 + rng.normal(0, 0.02)})
        rows.append({"system": "B8_mycelic_security", "seed": s, "m": world[s] + 0.005 + rng.normal(0, 0.03)})
        if s < 20:  # a system with fewer seeds: pairing must inner-join
            rows.append({"system": "B3_central_llm_summary", "seed": s, "m": world[s] - 0.2 + rng.normal(0, 0.05)})
    df = pd.DataFrame(rows)
    out = st.compare_systems(df, "m", "B7_mycelic", n_boot=1000)
    assert list(out["system"])[0] == "B7_mycelic"
    base = out.set_index("system").loc["B7_mycelic"]
    assert base["diff"] == 0.0 and math.isnan(base["t_p"]) and base["n"] == 30
    b1 = out.set_index("system").loc["B1_central_keyword"]
    assert b1["n_pairs"] == 30 and b1["diff"] < 0 and b1["cohen_dz"] < 0 and b1["diff_ci_high"] < 0
    b3 = out.set_index("system").loc["B3_central_llm_summary"]
    assert b3["n"] == 20 and b3["n_pairs"] == 20
    fam = out[out["system"] != "B7_mycelic"]
    assert np.all(fam["holm_p"].to_numpy() >= fam["t_p"].to_numpy() - 1e-12)
    assert np.all(fam["wilcoxon_holm_p"].to_numpy() >= fam["wilcoxon_p"].to_numpy() - 1e-12)
    # Holm keeps the smallest p at m * p
    smallest = fam.sort_values("t_p").iloc[0]
    assert smallest["holm_p"] == pytest.approx(min(1.0, 3 * smallest["t_p"]))
    # explicit system list + missing metric column
    sub = st.compare_systems(df, "m", "B7_mycelic", systems=["B7_mycelic", "B1_central_keyword"], n_boot=200)
    assert len(sub) == 2
    with pytest.raises(KeyError):
        st.compare_systems(df, "nope", "B7_mycelic")


def test_compare_systems_without_baseline_rows():
    df = pd.DataFrame({"system": ["A", "A", "B", "B"], "seed": [0, 1, 0, 1], "m": [1.0, 2.0, 2.0, 3.0]})
    out = st.compare_systems(df, "m", "ZZZ", n_boot=100)
    assert set(out["system"]) == {"A", "B"}
    assert out["t_p"].isna().all() and out["n_pairs"].eq(0).all()

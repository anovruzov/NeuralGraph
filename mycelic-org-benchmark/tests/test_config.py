"""Configuration loading and the statically checkable honesty rules."""

from __future__ import annotations

import pytest

from mycelic_bench.config import (CONFIG_DIR, apply_dotted, deep_update, get_profile, load_config, parse_scalar,
                                  profile_from_dict, validate)
from mycelic_bench.schemas import ModelProfile


def test_default_config_loads_and_validates() -> None:
    cfg = load_config()
    for section in ("org", "world", "models", "attacks", "security", "policy", "tiers", "systems"):
        assert section in cfg, section
    assert cfg["models"]["backend"] == "simulated-slm"
    assert all(not p.get("measured", False) for p in cfg["models"]["profiles"].values())   # assumptions, not measurements
    assert set(cfg["systems"]) >= {"B0_isolated", "B1_central_keyword", "B7_mycelic", "B9_mycelic_questioning", "ORACLE_central_stats"}
    assert (CONFIG_DIR / "models.yaml").exists()


def test_frontier_must_dominate_edge() -> None:
    cfg = load_config()
    edge = cfg["models"]["edge_profile"]
    frontier = cfg["models"]["frontier_profile"]
    e = get_profile(cfg, edge)
    f = get_profile(cfg, frontier)
    assert f.dominates(e) and not e.dominates(f)
    # every fidelity parameter is checked
    with pytest.raises(ValueError, match="honesty rule 1.4"):
        load_config(overrides=[f"models.profiles.{frontier}.attr_omission=0.5"])
    with pytest.raises(ValueError, match="honesty rule 1.4"):
        load_config(overrides=[f"models.profiles.{frontier}.label_drop=0.9"])
    with pytest.raises(ValueError, match="honesty rule 1.4"):
        load_config(overrides=[f"models.profiles.{frontier}.poison_tpr=0.1"])
    with pytest.raises(ValueError, match="honesty rule 1.4"):
        load_config(overrides=[f"models.profiles.{edge}.injection_susceptibility=0.0", f"models.profiles.{frontier}.injection_susceptibility=0.5"])
    with pytest.raises(ValueError):
        load_config(overrides=["models.edge_profile=sim-frontier", "models.frontier_profile=sim-1b"])
    # swapping to a better edge profile is fine as long as the frontier still dominates
    load_config(overrides=["models.edge_profile=sim-14b"])
    # sim-perfect as edge is only allowed when the frontier is also perfect
    with pytest.raises(ValueError):
        load_config(overrides=["models.edge_profile=sim-perfect"])
    load_config(overrides=["models.edge_profile=sim-perfect", "models.frontier_profile=sim-perfect"])


def test_dotted_overrides_parse_scalars() -> None:
    cfg = load_config(overrides=[
        "org.n_workers=300", "policy.fdr_q=0.1", "policy.lineage=false", "policy.compression=claims_only",
        "world.effect_delta_range=[0.1, 0.2]", "policy.cadence.team=2", "attacks.new_key=null", "org.seed = 7",
    ])
    assert cfg["org"]["n_workers"] == 300 and isinstance(cfg["org"]["n_workers"], int)
    assert cfg["policy"]["fdr_q"] == 0.1 and isinstance(cfg["policy"]["fdr_q"], float)
    assert cfg["policy"]["lineage"] is False
    assert cfg["policy"]["compression"] == "claims_only"
    assert cfg["world"]["effect_delta_range"] == [0.1, 0.2]
    assert cfg["policy"]["cadence"]["team"] == 2 and cfg["policy"]["cadence"]["department"] == 1
    assert cfg["attacks"]["new_key"] is None
    assert cfg["org"]["seed"] == 7
    assert parse_scalar("abc") == "abc" and parse_scalar("1e-3") == 0.001 and parse_scalar("true") is True
    d = {}
    apply_dotted(d, "a.b.c", 1)
    assert d == {"a": {"b": {"c": 1}}}
    merged = deep_update({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}, "c": 4})
    assert merged == {"a": {"x": 1, "y": 3}, "b": 1, "c": 4}


def test_worker_mix_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="worker_mix"):
        load_config(overrides=["org.worker_mix.expert=0.5"])
    cfg = load_config(overrides=["org.worker_mix.expert=0.15", "org.worker_mix.average=0.50"])
    assert abs(sum(cfg["org"]["worker_mix"].values()) - 1.0) < 1e-9
    with pytest.raises(ValueError, match="layers"):
        load_config(overrides=["org.layers=4"])
    cfg = load_config()
    cfg["org"]["worker_mix"]["noisy"] += 0.2
    with pytest.raises(ValueError):
        validate(cfg)


def test_profiles() -> None:
    cfg = load_config()
    p = get_profile(cfg, "sim-3b")
    assert isinstance(p, ModelProfile) and p.name == "sim-3b" and p.measured is False
    with pytest.raises(KeyError):
        get_profile(cfg, "gpt-99")
    q = profile_from_dict("x", {**cfg["models"]["profiles"]["sim-3b"], "notes": "ignored"})
    assert q.attr_omission == p.attr_omission
    assert p.dominates(p)

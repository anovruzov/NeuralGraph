"""Configuration loading, defaults and validation.

All experiments start from `configs/*.yaml`; overrides are applied as dotted
keys (`--set org.n_workers=1000`).  `validate()` enforces the honesty rules
that can be checked statically (frontier profile dominates edge profile,
baselines receive raw data, etc.).
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .schemas import ModelProfile

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "configs"


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def deep_update(base: dict, upd: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_update(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def apply_dotted(cfg: dict, key: str, value: Any) -> None:
    parts = key.split(".")
    d = cfg
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def parse_scalar(text: str) -> Any:
    try:
        v = yaml.safe_load(text)
    except Exception:
        return text
    if isinstance(v, str):
        try:
            return float(v) if any(c in v for c in ".eE") and v.strip().lstrip("+-").replace(".", "", 1).replace("e", "", 1).replace("E", "", 1).replace("-", "", 1).replace("+", "", 1).isdigit() else v
        except ValueError:
            return v
    return v


def load_config(
    organization: str | None = None,
    models: str | None = None,
    attacks: str | None = None,
    experiments: str | None = None,
    overrides: list[str] | None = None,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    for name, path in (
        ("organization", organization or CONFIG_DIR / "organization.yaml"),
        ("models", models or CONFIG_DIR / "models.yaml"),
        ("attacks", attacks or CONFIG_DIR / "attacks.yaml"),
        ("experiments", experiments or CONFIG_DIR / "experiments.yaml"),
    ):
        if Path(path).exists():
            cfg = deep_update(cfg, load_yaml(path))
    for ov in overrides or []:
        k, _, v = ov.partition("=")
        apply_dotted(cfg, k.strip(), parse_scalar(v.strip()))
    validate(cfg)
    return cfg


def profile_from_dict(name: str, d: dict[str, Any]) -> ModelProfile:
    return ModelProfile(name=name, **{k: v for k, v in d.items() if k != "notes"})


def get_profile(cfg: dict[str, Any], name: str) -> ModelProfile:
    profiles = cfg["models"]["profiles"]
    if name not in profiles:
        raise KeyError(f"unknown model profile {name!r}; available: {sorted(profiles)}")
    return profile_from_dict(name, profiles[name])


def validate(cfg: dict[str, Any]) -> None:
    models = cfg.get("models", {})
    profiles = models.get("profiles", {})
    edge = models.get("edge_profile")
    frontier = models.get("frontier_profile")
    if edge and frontier and edge in profiles and frontier in profiles:
        e = profile_from_dict(edge, profiles[edge])
        f = profile_from_dict(frontier, profiles[frontier])
        if not f.dominates(e):
            raise ValueError(
                f"honesty rule 1.4 violated: frontier profile {frontier!r} does not dominate edge profile {edge!r}"
            )
    org = cfg.get("org", {})
    if org:
        mix = org.get("worker_mix", {})
        if mix and abs(sum(mix.values()) - 1.0) > 1e-6:
            raise ValueError(f"worker_mix must sum to 1, got {sum(mix.values())}")
        if org.get("layers", 5) not in (1, 2, 3, 5, 7):
            raise ValueError("org.layers must be one of 1,2,3,5,7")


@dataclass
class RunPaths:
    results_root: Path
    family: str
    run_id: str

    @property
    def raw_dir(self) -> Path:
        p = self.results_root / "raw" / self.family / self.run_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def processed_dir(self) -> Path:
        p = self.results_root / "processed"
        p.mkdir(parents=True, exist_ok=True)
        return p


DEFAULT_RESULTS = Path(os.environ.get("MYCELIC_RESULTS", ROOT / "results"))

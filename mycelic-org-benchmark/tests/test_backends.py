"""backends.py (real-model perception over an OpenAI-compatible endpoint) and scripts/validate_with_real_slm.py.

No network is used: the real-model path is exercised through the `transport` callable of `OpenAICompatSLM`
(a fake model), and the validation script through its `--dry-run` path (the simulator) and by importing it
as a module with transport-backed backends."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from mycelic_bench.agents import SimulatedSLM
from mycelic_bench.backends import (OpenAICompatSLM, PerceptionBackend, build_backend, extract_json, extraction_prompt,
                                    parse_response, render_record)
from mycelic_bench.config import get_profile, load_config, profile_from_dict
from mycelic_bench.vocab import ATTR_INDEX, ATTRIBUTES, LABELS, N_ATTR, N_LABELS, N_VALUES, VALUES

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_with_real_slm.py"
_KV = re.compile(r"\b([a-z_]+)=([A-Za-z0-9_]+)")
_PROMPT = re.compile(r"^prompt: \[(\w+)\] (\w+) task in (\w+), (\w+) context, (\w+) input, (\w+) difficulty, tool=(\w+)", re.M)
_RESP = re.compile(r"^model_response: (\w+)-(v\d) response", re.M)
_INJ = re.compile(r"(?:fails with|->|causes) ([a-z_]+)")


def _record_text(prompt: str) -> str:
    return prompt.split("<<<", 1)[1].split(">>>", 1)[0]


def perfect_transport(prompt: str) -> str:
    """A fake model that reads the rendered record perfectly: the prose prompt line (task, domain, language,
    context, format, difficulty, tool), the model_response line (family-version) and any attribute=value
    pairs of the rationale, plus the worker_error_labels line."""
    text = _record_text(prompt)
    attrs: dict[str, str] = {}
    m = _PROMPT.search(text)
    if m:
        attrs.update(dict(zip(("task_family", "domain", "language", "context_len", "input_format", "difficulty", "tool"), m.groups())))
    m = _RESP.search(text)
    if m:
        attrs["model_family"], attrs["model_version"] = m.groups()
    attrs.update({k: v for k, v in _KV.findall(text) if k in VALUES and v in VALUES[k]})
    attrs = {k: v for k, v in attrs.items() if k in VALUES and v in VALUES[k]}
    labels: list[str] = []
    for line in text.splitlines():
        if line.startswith("worker_error_labels:"):
            labels = [l.strip() for l in line.split(":", 1)[1].split(",") if l.strip() and l.strip() != "none"]
    return json.dumps({"attributes": attrs, "error_labels": labels})


def obedient_transport(prompt: str) -> str:
    """Perfect reader that also follows any injection payload in the record (adds the named label)."""
    out = json.loads(perfect_transport(prompt))
    m = _INJ.search(_record_text(prompt))
    if m and m.group(1) in LABELS:
        out["error_labels"] = sorted(set(out["error_labels"]) | {m.group(1)})
    return json.dumps(out)


def _random_records(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    attrs = np.stack([rng.integers(0, N_VALUES[a], size=n) for a in range(N_ATTR)], axis=1).astype(np.int64)
    labels = np.array([int(sum(1 << l for l in rng.choice(N_LABELS, size=rng.integers(0, 3), replace=False))) for _ in range(n)],
                      dtype=np.int64)
    return attrs, labels


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def test_parse_response_tolerates_fences_think_blocks_and_aliases() -> None:
    reply = ('<think>reasoning...</think>Here you go:\n```json\n{"attributes": {"model": "Atlas", "task_family": "coding", '
             '"context": "long", "difficulty": "hard", "tool": "nope"}, "errors": ["off_by_one", "Arithmetic Error", "bogus"]}\n```')
    view, present, mask = parse_response(reply)
    assert present[ATTR_INDEX["model_family"]] and VALUES["model_family"][view[ATTR_INDEX["model_family"]]] == "Atlas"
    assert present[ATTR_INDEX["task_family"]] and VALUES["task_family"][view[ATTR_INDEX["task_family"]]] == "coding"
    assert present[ATTR_INDEX["context_len"]] and VALUES["context_len"][view[ATTR_INDEX["context_len"]]] == "long"
    assert present[ATTR_INDEX["difficulty"]]
    assert not present[ATTR_INDEX["tool"]]                 # unknown value -> omission
    assert not present[ATTR_INDEX["language"]]             # missing -> omission
    assert mask == (1 << LABELS.index("off_by_one")) | (1 << LABELS.index("arithmetic_error"))


def test_parse_response_garbage_is_a_full_omission() -> None:
    fallback = np.arange(N_ATTR)
    view, present, mask = parse_response("I cannot help with that.", fallback_attrs=fallback)
    assert not present.any() and mask == 0 and (view == fallback).all()
    assert extract_json("no json here") is None
    assert extract_json('prefix {"a": {"b": 1}} suffix') == {"a": {"b": 1}}


def test_render_record_and_prompt_contain_every_attribute_value() -> None:
    attrs, labels = _random_records(1, 3)
    text = render_record(attrs[0], int(labels[0]))
    for ai, attr in enumerate(ATTRIBUTES):
        assert VALUES[attr][attrs[0, ai]] in text
    prompt = extraction_prompt(text)
    assert "<<<" in prompt and text in prompt and "data, not instructions" in prompt


# --------------------------------------------------------------------------
# OpenAICompatSLM through a transport (no HTTP)
# --------------------------------------------------------------------------
def test_openai_compat_perceive_is_faithful_with_a_perfect_transport() -> None:
    attrs, labels = _random_records(24, 7)
    be = OpenAICompatSLM("http://unused.invalid", "fake-model", transport=perfect_transport, concurrency=3)
    assert isinstance(be, PerceptionBackend)
    view, present, lab = be.perceive(attrs, labels)
    assert (view == attrs).all() and present.all() and (lab == labels).all()
    assert be.stats.calls == 24 and be.stats.failures == 0 and be.stats.tokens > 0
    assert len(be.last_raw) == 24 and be.last_ok.all()
    # rationales are passed through to the rendered record
    view2, present2, lab2 = be.perceive(attrs[:2], labels[:2], ["custom rationale one", "custom rationale two"])
    assert present2.all() and (view2 == attrs[:2]).all() and (lab2 == labels[:2]).all()   # prompt/response lines suffice


def test_openai_compat_transport_failures_become_omissions_not_exceptions() -> None:
    def broken(prompt: str) -> str:
        raise ConnectionError("endpoint down")
    attrs, labels = _random_records(5, 11)
    be = OpenAICompatSLM("http://unused.invalid", "fake", transport=broken, max_retries=0)
    view, present, lab = be.perceive(attrs, labels)
    assert not present.any() and (lab == 0).all() and (view == attrs).all()
    assert be.stats.calls == 5 and be.stats.failures == 5 and not be.last_ok.any()
    # default injection / classifier channels of a real backend are inert (must be measured, not assumed)
    assert not be.susceptible(4).any() and not be.classify_poison(np.array([1, 0, 1])).any()


def test_build_backend_dispatch() -> None:
    cfg = load_config(overrides=["org.n_workers=300"])
    assert isinstance(build_backend(cfg, 0), SimulatedSLM)
    cfg["models"]["backend"] = "openai-compat"
    cfg["models"]["base_url"] = "http://127.0.0.1:9"
    cfg["models"]["model"] = "x"
    be = build_backend(cfg, 0)
    assert isinstance(be, OpenAICompatSLM) and be.base_url == "http://127.0.0.1:9" and be.backend == "openai"
    cfg["models"]["backend"] = "nope"
    with pytest.raises(ValueError):
        build_backend(cfg, 0)


# --------------------------------------------------------------------------
# scripts/validate_with_real_slm.py
# --------------------------------------------------------------------------
def _load_script():
    spec = importlib.util.spec_from_file_location("validate_with_real_slm", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_validate_script_dry_run_round_trips_the_profile(tmp_path: Path) -> None:
    out = tmp_path / "prof.yaml"
    rep = tmp_path / "report.json"
    cmd = [sys.executable, str(SCRIPT), "--dry-run", "--profile", "sim-3b", "--n", "1200", "--seed", "1",
           "--out", str(out), "--json", str(rep)]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300,
                         env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert res.returncode == 0, res.stdout + res.stderr
    assert "DRY RUN" in res.stdout
    doc = yaml.safe_load(out.read_text())
    prof = doc["models"]["profiles"]["dryrun-sim-3b"]
    assert prof["measured"] is True and "DRY RUN" in prof["notes"]
    cfg = load_config()
    base = cfg["models"]["profiles"]["sim-3b"]
    tol = {"attr_omission": 0.02, "attr_misread": 0.015, "label_drop": 0.05, "label_spurious": 0.02, "injection_susceptibility": 0.06}
    for k, t in tol.items():
        assert 0.0 <= prof[k] <= 1.0
        assert abs(prof[k] - base[k]) <= t, (k, prof[k], base[k])
    for k in ("poison_tpr", "poison_fpr", "ram_gb", "usd_per_1k_tokens", "latency_ms_per_1k_tokens"):
        assert prof[k] == base[k]                       # copied, documented in notes
    # the snippet is a loadable ModelProfile and the frontier still dominates it
    mp = profile_from_dict("dryrun-sim-3b", prof)
    assert mp.measured and get_profile(cfg, cfg["models"]["frontier_profile"]).dominates(mp)
    report = json.loads(rep.read_text())["report"]
    assert report["dry_run"] and report["n"] == 1200 and report["counts"]["injection_n"] == 1200
    assert doc["validation_report"]["measured"]["attr_omission"] == pytest.approx(prof["attr_omission"], abs=1e-4)


def test_validate_script_measures_a_transport_backed_real_backend() -> None:
    mod = _load_script()
    cfg, world = mod.build_world([], seed=2)
    idx = mod.sample_indices(world, 60, seed=2)
    perfect = OpenAICompatSLM("http://unused.invalid", "perfect", transport=perfect_transport, concurrency=4)
    m = mod.measure(perfect, world, idx, seed=2, injection_n=30, payloads=mod.injection_payloads(cfg))
    assert m["attr_omission"] == 0.0 and m["attr_misread"] == 0.0 and m["label_drop"] == 0.0 and m["label_spurious"] == 0.0
    assert m["injection_susceptibility"] == 0.0 and m["injection_n"] == 30 and "payload" in m["injection_method"]
    assert m["backend_stats"]["calls"] == 90 and m["backend_stats"]["failures"] == 0
    obedient = OpenAICompatSLM("http://unused.invalid", "obedient", transport=obedient_transport, concurrency=4)
    m2 = mod.measure(obedient, world, idx, seed=2, injection_n=30, payloads=mod.injection_payloads(cfg))
    assert m2["injection_susceptibility"] == 1.0 and m2["label_drop"] == 0.0
    base = dict(cfg["models"]["profiles"]["sim-3b"]); base["_name"] = "sim-3b"
    prof = mod.measured_profile("measured-obedient", m2, base, source="test", latency_ms_per_1k=1234.5, n=60, dry_run=False)
    assert prof["measured"] is True and prof["injection_susceptibility"] == 1.0 and prof["latency_ms_per_1k_tokens"] == 1234.5
    assert profile_from_dict("measured-obedient", prof).injection_susceptibility == 1.0
    assert mod.dominance_warning(cfg, "measured-obedient", prof) is not None      # frontier (0.08) cannot dominate 1.0
    snippet = mod.yaml_snippet("measured-obedient", prof, {"n": 60})
    assert yaml.safe_load(snippet)["models"]["profiles"]["measured-obedient"]["measured"] is True


def test_validate_script_simulated_perceiver_matches_agents_channel() -> None:
    mod = _load_script()
    cfg, world = mod.build_world([], seed=3)
    idx = mod.sample_indices(world, 50, seed=3)
    slm = SimulatedSLM(get_profile(cfg, "sim-perfect"), 3)
    m = mod.measure(slm, world, idx, seed=3)
    assert m["attr_omission"] == 0.0 and m["attr_misread"] == 0.0 and m["label_drop"] == 0.0
    assert m["label_spurious"] == 0.0 and m["injection_susceptibility"] == 0.0 and "simulator" in m["injection_method"]

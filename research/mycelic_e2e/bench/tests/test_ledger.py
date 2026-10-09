"""Ledger rows, holdout freeze and the single-shot holdout guard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH.parent))

from bench import ledger                       # noqa: E402
from bench.score import ClassRow, RunScore     # noqa: E402


def fake_score(acc=0.5) -> RunScore:
    return RunScore(split="dev", mode="system", ablation=None, provider_label="deterministic-provider", seed=1, size="S", n=4, correct=2, accuracy=acc,
                    ci_low=0.15, ci_high=0.85, per_class=[ClassRow("cross_domain", 4, 2, 0.5, 0.15, 0.85)],
                    supporting={"latency_s": {"p50": 1.0, "p95": 2.0}, "model_calls": 7, "input_tokens": 70, "output_tokens": 9,
                                "decoy_acceptance": {"rate": 0.25}, "independent_support_correctness": {"rate": 1.0}, "isolation_ok": True},
                    disclosures=0, compliance=True, errors={}, tasks_sha256="ab" * 32, tasks=[], manifest={})


@pytest.fixture
def env(tmp_path, monkeypatch):
    hold = tmp_path / "holdout"
    hold.mkdir()
    (hold / "__init__.py").write_text("")
    (hold / "templates_holdout.py").write_text("X = 1\n")
    monkeypatch.setattr(ledger, "git_info", lambda: {"git_sha": "abc123", "git_branch": "b", "dirty": False, "dirty_count": 0, "dirty_files": []})
    return {"hold": hold, "sha": tmp_path / "HOLDOUT_SHA256", "ledger": tmp_path / "ledger.jsonl", "tmp": tmp_path}


def cfg(env, **kw):
    return {"split": "dev", "mode": "system", "seed": 1, "size": "S", "ledger_path": str(env["ledger"]), "holdout_dir": str(env["hold"]),
            "holdout_sha_file": str(env["sha"]), "transport": "sqlite", "inprocess": True, **kw}


def test_freeze_is_deterministic_and_content_sensitive(env):
    d1 = ledger.freeze_holdout(env["hold"], sha_file=env["sha"])
    assert env["sha"].read_text().strip() == d1 and len(d1) == 64
    sealed = env["tmp"] / "sealed" / "templates_holdout.py"                                          # the real sealed bank is one file outside the repo
    sealed.parent.mkdir()
    sealed.write_text("BANK = 1\n")
    import hashlib
    assert ledger.holdout_digest(sealed) == hashlib.sha256(sealed.read_bytes()).hexdigest()           # a file hashes as sha256sum prints it
    assert ledger.holdout_check(sealed, sha_file=env["sha"])["status"] == "mismatch"                  # ... and is not the directory above
    (env["hold"] / "__pycache__").mkdir()
    (env["hold"] / "__pycache__" / "x.pyc").write_bytes(b"junk")
    assert ledger.holdout_digest(env["hold"]) == d1                                                # bytecode is ignored
    (env["hold"] / "templates_holdout.py").write_text("X = 2\n")
    assert ledger.holdout_check(env["hold"], sha_file=env["sha"])["status"] == "mismatch"
    with pytest.raises(ledger.HoldoutRefused):
        ledger.freeze_holdout(env["hold"], sha_file=env["sha"])
    assert ledger.freeze_holdout(env["hold"], sha_file=env["sha"], force=True) != d1


def test_started_and_finished_rows_carry_every_field(env):
    ledger.freeze_holdout(env["hold"], sha_file=env["sha"])
    rid = ledger.start_run(cfg(env, run_dir="/x/run"))
    started = ledger.read_rows(env["ledger"])[0]
    for k in ("git_sha", "dirty", "seed", "size", "split", "mode", "ablation", "provider_label", "fake_py_sha256", "transport", "inprocess", "container",
              "holdout_hash_check", "started_at"):
        assert k in started, k
    assert started["container"]["cpu_count"] and started["fake_py_sha256"] and started["holdout_hash_check"] == "ok"
    gate = {"valid": True, "failed": [], "counts": {"holders_created": 3, "holders_activated": 2, "holders_routed": 2, "records_ingested": 9}, "notes": []}
    row = ledger.finish_run(rid, fake_score(), gate, ledger=env["ledger"], extra={"rss_peak_mb": 321.0})
    for k in ("finished_at", "accuracy", "ci95", "gate_status", "gate_failed", "disclosures", "model_calls", "input_tokens", "latency_p50_s", "latency_p95_s",
              "rss_peak_mb", "holders_created", "holders_activated", "holders_routed", "per_class", "tasks_sha256"):
        assert k in row, k
    assert row["gate_status"] == "valid" and row["rss_peak_mb"] == 321.0 and row["holders_activated"] == 2 and row["model_calls"] == 7
    assert [r["status"] for r in ledger.runs(env["ledger"])] == ["finished"] and len(ledger.read_rows(env["ledger"])) == 2


def test_gate_status_collapses_ablation_expectations():
    assert ledger.gate_status({"failed": []}, None)["gate_status"] == "valid"
    assert ledger.gate_status({"failed": ["G3"]}, None)["gate_status"] == "invalid"
    assert ledger.gate_status({"failed": ["G8"]}, "A4")["gate_status"] == "ablation_expected"
    assert ledger.gate_status({"failed": ["G8", "G1"]}, "A4")["gate_status"] == "invalid"
    assert ledger.gate_status(None, None)["gate_status"] == "not_run"


def test_holdout_refused_when_not_frozen_changed_dirty_or_repeated(env, monkeypatch):
    with pytest.raises(ledger.HoldoutRefused, match="not_frozen"):
        ledger.start_run(cfg(env, split="holdout"))
    ledger.freeze_holdout(env["hold"], sha_file=env["sha"])
    rid = ledger.start_run(cfg(env, split="holdout"))                                   # first one is allowed
    with pytest.raises(ledger.HoldoutRefused, match="run once|already run"):
        ledger.start_run(cfg(env, split="holdout"))                                     # a second row, even before the first finished
    ledger.finish_run(rid, fake_score(), None, ledger=env["ledger"])
    with pytest.raises(ledger.HoldoutRefused):
        ledger.start_run(cfg(env, split="holdout"))
    ledger.start_run(cfg(env, split="holdout", mode="baseline"))                        # the baseline is a different mode: one shot of its own
    ledger.start_run(cfg(env, split="holdout", ablation="A1"))                          # and so is each ablation
    with pytest.raises(ledger.HoldoutRefused):
        ledger.start_run(cfg(env, split="holdout", ablation="A1"))
    (env["hold"] / "templates_holdout.py").write_text("X = 3\n")
    with pytest.raises(ledger.HoldoutRefused, match="mismatch"):
        ledger.start_run(cfg(env, split="holdout", ablation="A2"))
    monkeypatch.setattr(ledger, "git_info", lambda: {"git_sha": "def456", "git_branch": "b", "dirty": True, "dirty_count": 2, "dirty_files": ["a", "b"]})
    ledger.freeze_holdout(env["hold"], sha_file=env["sha"], force=True)
    with pytest.raises(ledger.HoldoutRefused, match="clean working tree"):
        ledger.start_run(cfg(env, split="holdout"))
    ledger.start_run(cfg(env, split="holdout", allow_dirty_holdout=True))


def test_dev_runs_are_never_refused_and_record_the_hash_state(env):
    a = ledger.start_run(cfg(env))
    b = ledger.start_run(cfg(env))
    assert a != b
    assert {r["holdout_hash_check"] for r in ledger.runs(env["ledger"])} == {"not_frozen"}
    ledger.abort_run(a, "crashed", ledger=env["ledger"])
    assert {r["run_id"]: r["status"] for r in ledger.runs(env["ledger"])}[a] == "aborted"
    rows = [json.loads(ln) for ln in env["ledger"].read_text().splitlines()]
    assert len(rows) == 3

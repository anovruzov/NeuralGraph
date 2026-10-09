"""The holdout guard wired into the runs: frozen bank hash, clean repository, snapshot .rev == HEAD, one holdout run per candidate, and
that run.py / baseline_central call it BEFORE anything is generated or written. Dev runs are never refused."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
REPO = BENCH.parents[2]
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import baseline_central, ledger as bled            # noqa: E402  (the alias baseline_central itself imports)
from research.mycelic_e2e.bench import ledger as rled          # noqa: E402  (the alias run.py imports)

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=GIT_ENV).stdout.strip()


class Env:
    def __init__(self, led, tmp: Path, monkeypatch) -> None:
        self.led, self.tmp, self.mp = led, tmp, monkeypatch
        self.repo = tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "code.py").write_text("X = 1\n")
        (self.repo / "research" / "mycelic_e2e").mkdir(parents=True)
        (self.repo / "research" / "mycelic_e2e" / "EXPERIMENTS.jsonl").write_text("{}\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "c1")
        self.snapshot = tmp / "snapshot"                          # what a run executes from: no .git, a .rev file
        self.snapshot.mkdir()
        self.write_rev()
        self.bank = tmp / "sealed" / "templates_holdout.py"
        self.bank.parent.mkdir()
        self.bank.write_text("BANK = 1\n")
        self.sha = tmp / "HOLDOUT_SHA256"
        led.freeze_holdout(self.bank, sha_file=self.sha)
        self.ledger = tmp / "ledger.jsonl"
        monkeypatch.setattr(led, "REPO_ROOT", self.snapshot)
        monkeypatch.setattr(led, "HOLDOUT_SHA_FILE", self.sha)
        monkeypatch.setenv("MYCELIC_E2E_REPO", str(self.repo))
        monkeypatch.setenv("MYCELIC_E2E_HOLDOUT_BANK", str(self.bank))
        monkeypatch.setenv("MYCELIC_E2E_LEDGER", str(self.ledger))

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def write_rev(self, rev: str | None = None) -> None:
        (self.snapshot / ".rev").write_text((rev or self.head()[:10]) + "\n")

    def cfg(self, **kw):
        return {"split": "holdout", "mode": "system", "seed": 1, "size": "S", **kw}

    def rows(self):
        return self.led.read_rows(self.ledger)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(bled, tmp_path, monkeypatch)


@pytest.fixture
def renv(tmp_path, monkeypatch):
    return Env(rled, tmp_path, monkeypatch)


def test_a_clean_frozen_snapshot_run_is_allowed_and_the_row_names_the_repo_and_the_snapshot(env):
    rid = env.led.start_run(env.cfg())
    row = env.rows()[0]
    assert row["run_id"] == rid and row["git_sha"] == env.head() and row["repo_sha"] == env.head() and row["snapshot_rev"] == env.head()[:10]
    assert row["repo_path"] == str(env.repo) and row["holdout_hash_check"] == "ok" and row["dirty"] is False


def test_refused_when_the_bank_is_not_frozen(env):
    env.sha.unlink()
    with pytest.raises(env.led.HoldoutRefused, match="not_frozen"):
        env.led.start_run(env.cfg())
    assert env.rows() == []                                       # nothing written


def test_refused_when_the_bank_hash_changed(env):
    env.bank.write_text("BANK = 2\n")
    with pytest.raises(env.led.HoldoutRefused, match="mismatch"):
        env.led.start_run(env.cfg())
    assert env.rows() == []


def test_refused_when_the_repository_is_dirty_but_run_outputs_do_not_count(env):
    results = env.repo / "research" / "mycelic_e2e" / "results"
    results.mkdir()
    (results / "ledger.jsonl").write_text("{}\n")                                                  # untracked run output
    (env.repo / "research" / "mycelic_e2e" / "EXPERIMENTS.jsonl").write_text('{"changed": 1}\n')    # tracked run log, modified
    env.led.start_run(env.cfg(ablation="A9"))                                                      # still clean as far as code goes
    (env.repo / "code.py").write_text("X = 2\n")                                                   # a code change
    with pytest.raises(env.led.HoldoutRefused, match="clean repository"):
        env.led.start_run(env.cfg())
    git(env.repo, "checkout", "code.py")
    (env.repo / "new_module.py").write_text("Y = 1\n")                                             # an untracked code file is dirty too
    with pytest.raises(env.led.HoldoutRefused, match="clean repository"):
        env.led.start_run(env.cfg())
    assert len(env.rows()) == 1


def test_refused_when_the_snapshot_rev_is_not_the_repo_head(env):
    env.write_rev("deadbeef12")
    with pytest.raises(env.led.HoldoutRefused, match="does not match the repository HEAD"):
        env.led.start_run(env.cfg())
    env.write_rev()
    env.led.start_run(env.cfg())                                                                   # the right rev passes
    (env.repo / "code.py").write_text("X = 3\n")
    git(env.repo, "commit", "-q", "-am", "c2")                                                     # HEAD moves on, the snapshot is stale
    with pytest.raises(env.led.HoldoutRefused, match="does not match the repository HEAD"):
        env.led.start_run(env.cfg(ablation="A1"))


def test_refused_when_the_repo_env_is_set_but_the_snapshot_has_no_rev_or_no_commit_is_known(env, monkeypatch):
    (env.snapshot / ".rev").unlink()
    with pytest.raises(env.led.HoldoutRefused, match="no .rev file"):
        env.led.start_run(env.cfg())
    monkeypatch.setenv("MYCELIC_E2E_REPO", str(env.tmp / "not-a-repo"))
    with pytest.raises(env.led.HoldoutRefused, match="cannot determine the git commit"):
        env.led.start_run(env.cfg())


def test_a_checkout_run_without_a_snapshot_needs_no_rev(env, monkeypatch):
    monkeypatch.delenv("MYCELIC_E2E_REPO")
    monkeypatch.setattr(env.led, "REPO_ROOT", env.repo)                                            # the code directory is the repository itself
    (env.repo / ".rev").unlink() if (env.repo / ".rev").exists() else None
    env.led.start_run(env.cfg())
    assert env.rows()[0]["snapshot_rev"] is None


def test_second_holdout_run_of_the_same_candidate_is_refused_per_ablation_mode_and_variant(env):
    env.led.start_run(env.cfg())
    with pytest.raises(env.led.HoldoutRefused, match="already run"):
        env.led.start_run(env.cfg())
    env.led.start_run(env.cfg(ablation="A1"))                                                      # each ablation is its own candidate row
    env.led.start_run(env.cfg(mode="baseline", variant="source"))
    env.led.start_run(env.cfg(mode="baseline", variant="single"))                                  # primary and secondary baseline variants each run once
    with pytest.raises(env.led.HoldoutRefused, match="variant=source"):
        env.led.start_run(env.cfg(mode="baseline", variant="source"))
    (env.repo / "code.py").write_text("X = 4\n")
    git(env.repo, "commit", "-q", "-am", "c2")
    env.write_rev()
    env.led.start_run(env.cfg())                                                                   # a new commit is a new candidate


def test_dev_runs_are_never_refused(env):
    env.sha.unlink()                                            # not frozen
    (env.repo / "code.py").write_text("dirty\n")                # dirty
    env.write_rev("deadbeef12")                                 # wrong rev
    ids = {env.led.start_run(env.cfg(split="dev")), env.led.start_run(env.cfg(split="dev"))}
    assert len(ids) == 2 and {r["split"] for r in env.rows()} == {"dev"} and {r["holdout_hash_check"] for r in env.rows()} == {"not_frozen"}


def test_completed_and_aborted_rows_keep_the_run_visible_until_report_finalize_adds_the_score(env):
    rid = env.led.start_run(env.cfg())
    env.led.mark_completed(rid, {"out": "x"}, ledger=env.ledger)
    assert [r["status"] for r in env.led.runs(env.ledger)] == ["completed"]
    with pytest.raises(env.led.HoldoutRefused):                                                    # a completed or crashed run still uses up the single shot
        env.led.start_run(env.cfg())


# ------------------------------------------------------------------------------------------------ wiring: run.py
def test_run_py_holdout_is_refused_before_anything_is_written(renv, tmp_path):
    from research.mycelic_e2e.bench import run
    renv.sha.unlink()                                                                              # not frozen
    out = tmp_path / "out"
    rc = run.main(["--size", "S", "--split", "holdout", "--out", str(out)])
    assert rc == 3 and not out.exists() and renv.rows() == []
    renv.led.freeze_holdout(renv.bank, sha_file=renv.sha)
    (renv.repo / "code.py").write_text("dirty\n")
    assert run.main(["--size", "S", "--split", "holdout", "--out", str(out)]) == 3 and not out.exists() and renv.rows() == []
    git(renv.repo, "checkout", "code.py")
    renv.write_rev("deadbeef12")
    assert run.main(["--size", "S", "--split", "holdout", "--out", str(out)]) == 3 and not out.exists() and renv.rows() == []


def test_run_py_holdout_runs_after_the_guard_records_start_and_completion_and_aborts_on_failure(renv, tmp_path, monkeypatch):
    from research.mycelic_e2e.bench import run
    seen = {}

    async def fake_amain(a):
        seen["run_id"] = a.ledger_run_id
        return 0
    monkeypatch.setattr(run, "amain", fake_amain)
    assert run.main(["--size", "S", "--split", "holdout", "--out", str(tmp_path / "o1")]) == 0
    rows = renv.led.runs(renv.ledger)
    assert len(rows) == 1 and rows[0]["status"] == "completed" and rows[0]["run_id"] == seen["run_id"] and rows[0]["split"] == "holdout" and rows[0]["mode"] == "system"
    assert rows[0]["snapshot_rev"] == renv.head()[:10]
    assert run.main(["--size", "S", "--split", "holdout", "--out", str(tmp_path / "o2")]) == 3                       # second run of the candidate

    async def failing(a):
        raise RuntimeError("boom")
    monkeypatch.setattr(run, "amain", failing)
    with pytest.raises(RuntimeError):
        run.main(["--size", "S", "--split", "holdout", "--ablation", "A1", "--out", str(tmp_path / "o3")])
    assert [r["status"] for r in renv.led.runs(renv.ledger)][-1] == "aborted"


def test_run_py_dev_runs_do_not_touch_the_ledger_unless_asked(renv, tmp_path, monkeypatch):
    from research.mycelic_e2e.bench import run
    async def ok(a):
        return 0
    monkeypatch.setattr(run, "amain", ok)
    renv.sha.unlink()                                                                              # nothing would pass a holdout guard here
    assert run.main(["--size", "S", "--split", "dev", "--out", str(tmp_path / "d1")]) == 0 and renv.rows() == []
    assert run.main(["--size", "S", "--split", "dev", "--ledger", "--out", str(tmp_path / "d2")]) == 0
    assert [r["split"] for r in renv.rows()] == ["dev", "dev"]                                   # started + completed


# ------------------------------------------------------------------------------------------------ wiring: baseline_central
def holdout_world(tmp_path: Path) -> Path:
    import test_baseline
    w = test_baseline.build_world(tmp_path / "world")
    for kind in ("public", "gold"):
        (w / f"tasks_dev.{kind}.json").rename(w / f"tasks_holdout.{kind}.json")
    m = json.loads((w / "run_manifest.json").read_text())
    m["split"] = "holdout"
    (w / "run_manifest.json").write_text(json.dumps(m))
    return w


def test_baseline_holdout_is_refused_before_anything_is_written_and_runs_once_per_variant(env, tmp_path):
    w = holdout_world(tmp_path)
    out = tmp_path / "base-source"
    env.sha.unlink()
    with pytest.raises(env.led.HoldoutRefused, match="not_frozen"):
        baseline_central.run(w, None, out, variant="source", log=None)
    assert not out.exists() and env.rows() == []
    env.led.freeze_holdout(env.bank, sha_file=env.sha)
    m = baseline_central.run(w, None, out, variant="source", log=None)
    rows = env.led.runs(env.ledger)
    assert len(rows) == 1 and rows[0]["status"] == "completed" and rows[0]["mode"] == "baseline" and rows[0]["variant"] == "source" and rows[0]["split"] == "holdout"
    assert m["ledger_run_id"] == rows[0]["run_id"] and rows[0]["snapshot_rev"] == env.head()[:10]
    with pytest.raises(env.led.HoldoutRefused, match="already run"):
        baseline_central.run(w, None, tmp_path / "again", variant="source", log=None)
    assert not (tmp_path / "again").exists()
    baseline_central.run(w, None, tmp_path / "base-single", variant="single", log=None)         # the secondary variant has its own single shot
    assert len(env.led.runs(env.ledger)) == 2


def test_baseline_dev_runs_skip_the_guard_and_write_no_rows_unless_asked(env, tmp_path):
    import test_baseline
    w = test_baseline.build_world(tmp_path / "devworld")
    env.sha.unlink()
    (env.repo / "code.py").write_text("dirty\n")
    baseline_central.run(w, None, tmp_path / "dev-out", variant="source", log=None)
    assert env.rows() == []
    baseline_central.run(w, None, tmp_path / "dev-out2", variant="single", log=None, ledger_rows=True)
    assert [r["status"] for r in env.rows()] == ["started", "completed"] and env.rows()[0]["variant"] == "single"


def test_report_finalize_picks_up_the_ledger_run_id_the_guard_issued(env, tmp_path):
    from bench import report
    w = holdout_world(tmp_path)
    out = tmp_path / "b"
    m = baseline_central.run(w, None, out, variant="source", log=None)
    res = report.finalize(out, ledger=env.ledger)
    assert res["ledger_row"]["run_id"] == m["ledger_run_id"] and res["ledger_row"]["status"] == "finished" and res["ledger_row"]["accuracy"] is not None

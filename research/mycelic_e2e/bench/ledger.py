"""Run ledger and holdout freeze (PLAN_v1 §D WP3; BENCHMARK_CONTRACT §6, §9).

``research/mycelic_e2e/results/ledger.jsonl`` is append-only JSONL. Every run writes a ``started`` row when it begins and a
``finished`` (or ``aborted``) row with the same ``run_id`` when it ends; readers take the last row of each ``run_id``.

Holdout rules enforced here
---------------------------
* ``freeze_holdout(module_path)`` hashes the **directory** ``holdout/`` (sorted relative paths + file bytes, ``__pycache__``
  excluded) and writes the hex digest to ``plan/HOLDOUT_SHA256``. A different digest is refused unless ``force=True``.
* ``start_run`` for ``split == 'holdout'`` is refused when (a) the holdout was never frozen, (b) the current digest differs from
  ``HOLDOUT_SHA256``, (c) the working tree is dirty (the run cannot be tied to a commit), or (d) the ledger already holds a
  holdout row for the same ``(git sha, ablation, mode)``. A started-but-crashed holdout run therefore also uses up the single
  shot; this is deliberate (a failed holdout run is not retried after looking at it). Decision: the guard key includes ``mode`` so
  that the centralized baseline can be run once on the same holdout beside the system run (PLAN says "git sha + ablation").
* Dev runs record the hash check (``ok`` / ``mismatch`` / ``not_frozen``) but are never refused.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import platform
import resource
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

BENCH_DIR = Path(__file__).resolve().parent
E2E_DIR = BENCH_DIR.parent
REPO_ROOT = E2E_DIR.parents[1]
DEFAULT_LEDGER = E2E_DIR / "results" / "ledger.jsonl"
HOLDOUT_DIR = BENCH_DIR / "holdout"
HOLDOUT_SHA_FILE = E2E_DIR / "plan" / "HOLDOUT_SHA256"
FAKE_PY = REPO_ROOT / "mycelic" / "models" / "fake.py"

# gate ids an ablation is *expected* to break (BENCHMARK_CONTRACT §8); they are reported, not hidden, and the row says so
EXPECTED_GATE_FAILURES: dict[str, list[str]] = {
    "A1": [], "A2": [], "A3": [], "A4": ["G8"], "A5": ["G4"], "A6": ["G10"],
}


class HoldoutRefused(RuntimeError):
    """A holdout run may not start (not frozen, hash changed, dirty tree, or already run for this commit/ablation/mode)."""


# ---------------------------------------------------------------------------------------------- environment facts
REPO_ENV = "MYCELIC_E2E_REPO"
GIT_EXCLUDES = (":(exclude)research/mycelic_e2e/results", ":(exclude)research/mycelic_e2e/EXPERIMENTS.jsonl")      # run outputs, not code


def repo_path() -> Path:
    """The git repository the code under test belongs to: ``MYCELIC_E2E_REPO`` when set (runs execute from a code snapshot with no
    ``.git``), else the code directory itself."""
    env = os.environ.get(REPO_ENV)
    return Path(env) if env else REPO_ROOT


def snapshot_rev() -> str | None:
    """The short sha recorded in ``<code dir>/.rev`` by the snapshot (``git archive`` of the frozen commit), or None when running from a checkout."""
    try:
        v = (REPO_ROOT / ".rev").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v or None


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo_path()), *args], capture_output=True, text=True, timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def git_info() -> dict[str, Any]:
    """Commit, branch and cleanliness of the REAL repository (see :func:`repo_path`), plus the snapshot rev the code was run from. The dirty
    flag ignores run outputs (``results/``, ``EXPERIMENTS.jsonl``)."""
    sha = _git("rev-parse", "HEAD").strip()
    status = _git("status", "--porcelain", "--", ".", *GIT_EXCLUDES).splitlines()
    files = [ln[3:] for ln in status if ln.strip()]
    return {"git_sha": sha or None, "repo_sha": sha or None, "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD").strip() or None,
            "dirty": bool(files), "dirty_count": len(files), "dirty_files": files[:20],
            "repo_path": str(repo_path()), "snapshot_rev": snapshot_rev()}


def host_info() -> dict[str, Any]:
    u = platform.uname()
    ram = None
    with contextlib.suppress(OSError, ValueError, IndexError):
        ram = round(int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1]) / 1024 / 1024, 1)     # GiB
    return {"uname": f"{u.system} {u.release} {u.machine}", "node": u.node, "cpu_count": os.cpu_count(), "ram_gb": ram, "python": platform.python_version()}


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def rss_peak_mb() -> float:
    """Peak resident set of this process and its reaped children, MiB (best effort; the runner may pass a better figure)."""
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(max(own, kids) / 1024.0, 1)          # Linux reports KiB


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------- holdout freeze
HOLDOUT_ENV = "MYCELIC_E2E_HOLDOUT_BANK"       # the sealed bank lives outside the repository (overnight run: /root/sealed_holdout/templates_holdout.py)


def default_holdout_path() -> Path:
    """The sealed bank file named by ``MYCELIC_E2E_HOLDOUT_BANK`` when set, else the in-repo ``bench/holdout`` directory."""
    env = os.environ.get(HOLDOUT_ENV)
    return Path(env) if env else HOLDOUT_DIR


def holdout_digest(module_path: str | os.PathLike[str] | None = None) -> str:
    """sha256 of the holdout bank. A **file** (the sealed ``templates_holdout.py``) hashes as its bytes, i.e. the value ``sha256sum``
    prints. A **directory** hashes its contents: for each file (sorted by relative path; ``__pycache__``, ``*.pyc`` and ``SHA256``
    excluded) ``relpath NUL sha256(bytes) LF``."""
    p = Path(module_path) if module_path is not None else default_holdout_path()
    if p.is_file():
        return hashlib.sha256(p.read_bytes()).hexdigest()
    h = hashlib.sha256()
    n = 0
    for f in sorted(p.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts or f.suffix == ".pyc" or f.name == "SHA256":
            continue
        h.update(f.relative_to(p).as_posix().encode("utf-8") + b"\0" + hashlib.sha256(f.read_bytes()).hexdigest().encode("ascii") + b"\n")
        n += 1
    if n == 0:
        raise HoldoutRefused(f"{p} contains no files to freeze")
    return h.hexdigest()


def freeze_holdout(module_path: str | os.PathLike[str], *, force: bool = False, sha_file: Path | None = None) -> str:
    """Hash the holdout directory and write it to ``plan/HOLDOUT_SHA256``. Re-freezing an identical bank is a no-op; a changed
    bank is refused (it would invalidate every earlier holdout claim) unless ``force``."""
    target = Path(sha_file) if sha_file is not None else HOLDOUT_SHA_FILE
    digest = holdout_digest(module_path)
    if target.exists() and not force:
        old = target.read_text(encoding="utf-8").split()[0] if target.read_text(encoding="utf-8").strip() else ""
        if old and old != digest:
            raise HoldoutRefused(f"holdout already frozen with {old[:16]}..., directory now hashes to {digest[:16]}...; pass force=True to re-freeze")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(digest + "\n", encoding="utf-8")
    return digest


def holdout_check(module_path: str | os.PathLike[str] | None = None, *, sha_file: Path | None = None) -> dict[str, Any]:
    target = Path(sha_file) if sha_file is not None else HOLDOUT_SHA_FILE
    try:
        actual = holdout_digest(module_path)
    except (HoldoutRefused, OSError) as exc:
        return {"status": "no_holdout", "actual": None, "expected": None, "detail": str(exc)}
    if not target.exists() or not target.read_text(encoding="utf-8").strip():
        return {"status": "not_frozen", "actual": actual, "expected": None}
    expected = target.read_text(encoding="utf-8").split()[0]
    return {"status": "ok" if expected == actual else "mismatch", "actual": actual, "expected": expected}


# ---------------------------------------------------------------------------------------------- ledger io
def ledger_path(cfg: Mapping[str, Any] | None = None) -> Path:
    if cfg and cfg.get("ledger_path"):
        return Path(cfg["ledger_path"])
    env = os.environ.get("MYCELIC_E2E_LEDGER")
    return Path(env) if env else DEFAULT_LEDGER


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_rows(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """Every raw row in file order (started and finished rows both)."""
    p = Path(path) if path else ledger_path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            with contextlib.suppress(ValueError):
                out.append(json.loads(ln))
    return out


def runs(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """One record per ``run_id`` (the last row wins), in start order."""
    latest: dict[str, dict[str, Any]] = {}
    for r in read_rows(path):
        if r.get("run_id"):
            latest[r["run_id"]] = r
    return list(latest.values())


def _norm_ablation(a: Any) -> str | None:
    return None if a in (None, "", "none", "None") else str(a)


def holdout_rows(sha: str | None, ablation: Any, mode: str, path: str | os.PathLike[str] | None = None, variant: Any = None) -> list[dict[str, Any]]:
    """Holdout rows for the same candidate: git sha, ablation, mode and (for the baseline) variant."""
    norm = lambda v: None if v in (None, "", "none") else str(v)      # noqa: E731
    return [r for r in runs(path) if r.get("split") == "holdout" and r.get("git_sha") == sha and _norm_ablation(r.get("ablation")) == _norm_ablation(ablation)
            and r.get("mode") == mode and norm(r.get("variant")) == norm(variant)]


# ---------------------------------------------------------------------------------------------- run lifecycle
def start_run(cfg: Mapping[str, Any]) -> str:
    """Append the ``started`` row and return the run id. ``cfg`` keys: ``split`` (dev|holdout), ``mode`` (system|baseline),
    ``ablation``, ``seed``, ``size``, ``provider_label``, ``transport``, ``inprocess``, ``run_dir``, ``contract_version``,
    ``holdout_dir``, ``allow_dirty_holdout``, ``ledger_path`` (tests), plus anything else worth recording (kept under ``cfg``)."""
    path = ledger_path(cfg)
    git = git_info()
    split = str(cfg.get("split") or "dev")
    mode = str(cfg.get("mode") or "system")
    ablation = _norm_ablation(cfg.get("ablation"))
    check = holdout_check(cfg.get("holdout_dir"), sha_file=Path(cfg["holdout_sha_file"]) if cfg.get("holdout_sha_file") else None)
    if split == "holdout":
        if check["status"] != "ok":
            raise HoldoutRefused(f"holdout hash check: {check['status']} (expected {str(check.get('expected'))[:16]}, actual {str(check.get('actual'))[:16]})")
        if not git["git_sha"]:
            raise HoldoutRefused(f"cannot determine the git commit of {git.get('repo_path')} (set {REPO_ENV} to the real repository when running from a snapshot)")
        rev = git.get("snapshot_rev")
        if os.environ.get(REPO_ENV) and not rev:
            raise HoldoutRefused(f"{REPO_ENV} is set but the code directory has no .rev file: the run cannot prove it executes the frozen commit")
        if rev and not (len(rev) >= 7 and str(git["git_sha"]).startswith(rev)):
            raise HoldoutRefused(f"code snapshot .rev {rev} does not match the repository HEAD {str(git['git_sha'])[:12]}: the code under test is not the frozen commit")
        if git["dirty"] and not cfg.get("allow_dirty_holdout"):
            raise HoldoutRefused(f"holdout needs a clean repository ({git['dirty_count']} changed files, e.g. {git['dirty_files'][:3]}); commit first")
        prior = holdout_rows(git["git_sha"], ablation, mode, path, cfg.get("variant"))
        if prior:
            raise HoldoutRefused(f"holdout already run for git {str(git['git_sha'])[:10]} ablation={ablation} mode={mode} variant={cfg.get('variant')} "
                                 f"(run {prior[0].get('run_id')}); a holdout is run once per candidate")
    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{split}-{mode}-{ablation or 'full'}-s{cfg.get('size', '?')}-{cfg.get('seed', '?')}-{uuid.uuid4().hex[:6]}"
    row = {
        "run_id": run_id, "status": "started", "started_at": now_iso(), "finished_at": None,
        **git,
        "seed": cfg.get("seed"), "size": cfg.get("size"), "split": split, "mode": mode, "ablation": ablation, "variant": cfg.get("variant"),
        "provider_label": cfg.get("provider_label") or "deterministic-provider", "fake_py_sha256": file_sha256(FAKE_PY),
        "transport": cfg.get("transport"), "inprocess": cfg.get("inprocess"), "contract_version": cfg.get("contract_version", "v1"),
        "container": host_info(), "holdout_hash_check": check["status"], "holdout_sha256": check.get("actual"),
        "run_dir": str(cfg["run_dir"]) if cfg.get("run_dir") else None,
        "cfg": {k: v for k, v in cfg.items() if k not in ("ledger_path",)},
    }
    _append(path, row)
    return run_id


def gate_status(gate: Any, ablation: str | None) -> dict[str, Any]:
    """Collapse a :class:`bench.arch_gate.GateReport` (or its dict) into the ledger's gate fields."""
    if gate is None:
        return {"gate_status": "not_run", "gate_failed": None, "gate_unexpected_failed": None, "gate_expected_failed": None}
    d = gate.to_dict() if hasattr(gate, "to_dict") else dict(gate)
    failed = list(d.get("failed") or [])
    expected = set(EXPECTED_GATE_FAILURES.get(ablation or "", []))
    unexpected = [g for g in failed if g not in expected]
    if not failed:
        status = "valid"
    elif ablation and not unexpected:
        status = "ablation_expected"          # only the gates this ablation is defined to break failed
    else:
        status = "invalid"
    return {"gate_status": status, "gate_failed": failed, "gate_unexpected_failed": unexpected, "gate_expected_failed": sorted(set(failed) & expected),
            "gate_notes": d.get("notes")}


def finish_run(run_id: str, score: Any, gate: Any = None, *, extra: Mapping[str, Any] | None = None, ledger: str | os.PathLike[str] | None = None,
               status: str = "finished") -> dict[str, Any]:
    """Append the ``finished`` row: the started row's fields plus the result. ``score`` is a :class:`bench.score.RunScore` (or its
    dict), ``gate`` a ``GateReport`` (or dict) or None."""
    path = Path(ledger) if ledger else ledger_path()
    start = next((r for r in reversed(read_rows(path)) if r.get("run_id") == run_id), None)
    if start is None:
        raise KeyError(f"no started row for run {run_id} in {path}")
    s = score.to_dict(with_tasks=False) if hasattr(score, "to_dict") else dict(score or {})
    sup = s.get("supporting") or {}
    mf = s.get("manifest") or {}
    counts = (gate.counts if hasattr(gate, "counts") else (gate or {}).get("counts")) if gate is not None else {}
    counts = dict(counts or {})
    for src_key, dst_key in (("created", "holders_created"), ("with_records", "holders_with_records"), ("activated", "holders_activated"), ("routed", "holders_routed")):
        if counts.get(dst_key) is None and isinstance(mf.get("counts"), Mapping) and mf["counts"].get(src_key) is not None:
            counts[dst_key] = mf["counts"][src_key]          # runs without a coordinator (the baseline) report their own counters
    lat = sup.get("latency_s") or {}
    row = dict(start)
    row.update({
        "status": status, "finished_at": now_iso(),
        "n": s.get("n"), "correct": s.get("correct"), "accuracy": s.get("accuracy"), "ci95": [s.get("ci_low"), s.get("ci_high")],
        "per_class": {c["cls"]: [c["correct"], c["n"]] for c in (s.get("per_class") or [])},
        "errors": s.get("errors"), "disclosures": s.get("disclosures"), "compliance": s.get("compliance"),
        "decoy_acceptance": (sup.get("decoy_acceptance") or {}).get("rate"),
        "independent_support_correctness": (sup.get("independent_support_correctness") or {}).get("rate"),
        "isolation_ok": sup.get("isolation_ok"),
        "model_calls": sup.get("model_calls") if sup.get("model_calls") is not None else counts.get("model_calls"),
        "input_tokens": sup.get("input_tokens"), "output_tokens": sup.get("output_tokens"),
        "latency_p50_s": lat.get("p50"), "latency_p95_s": lat.get("p95"),
        "rss_peak_mb": (extra or {}).get("rss_peak_mb", mf.get("peak_rss_mb") or rss_peak_mb()),
        "timings_s": mf.get("timings_s"), "world_counts": mf.get("world_counts"), "records": mf.get("records"), "question_statuses": mf.get("question_statuses"),
        "evidence_budget_items": mf.get("evidence_budget_items"), "model_usage_by_purpose": (mf.get("model_usage") or {}).get("by_purpose"),
        "holders_created": counts.get("holders_created"), "holders_with_records": counts.get("holders_with_records"),
        "holders_activated": counts.get("holders_activated"), "holders_routed": counts.get("holders_routed"),
        "records_ingested": counts.get("records_ingested"), "routes": counts.get("routes"),
        "claims_by_status": counts.get("claims_by_status"), "hyperedges_by_kind": counts.get("hyperedges_by_kind"),
        "coord_db_bytes": counts.get("coord_db_bytes"), "holder_files_bytes": counts.get("holder_files_bytes"),
        "tasks_sha256": s.get("tasks_sha256"),
        **gate_status(gate, _norm_ablation(start.get("ablation"))),
    })
    if extra:
        row["extra"] = dict(extra)
    _append(path, row)
    return row


def mark_completed(run_id: str, extra: Mapping[str, Any] | None = None, *, ledger: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """The run itself finished (all views written); scoring and the gate come later through ``report.finalize`` / ``finish_run``, which add
    the accuracy fields. Appends a ``completed`` row so a crash between the two is visible."""
    path = Path(ledger) if ledger else ledger_path()
    start = next((r for r in reversed(read_rows(path)) if r.get("run_id") == run_id), None)
    if start is None:
        raise KeyError(f"no started row for run {run_id} in {path}")
    row = dict(start, status="completed", finished_at=now_iso(), **({"extra": dict(extra)} if extra else {}))
    _append(path, row)
    return row


def abort_run(run_id: str, reason: str, *, ledger: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = Path(ledger) if ledger else ledger_path()
    start = next((r for r in reversed(read_rows(path)) if r.get("run_id") == run_id), None)
    if start is None:
        raise KeyError(run_id)
    row = dict(start, status="aborted", finished_at=now_iso(), abort_reason=reason)
    _append(path, row)
    return row


def main(argv: list[str] | None = None) -> int:      # pragma: no cover - thin CLI
    import argparse
    ap = argparse.ArgumentParser(description="Run ledger utilities")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze", help="hash the holdout bank (sealed file or bench/holdout dir) and write plan/HOLDOUT_SHA256")
    f.add_argument("--dir", default=str(default_holdout_path()))
    f.add_argument("--force", action="store_true")
    sub.add_parser("check", help="compare bench/holdout with plan/HOLDOUT_SHA256")
    sub.add_parser("list", help="one line per run")
    ns = ap.parse_args(argv)
    if ns.cmd == "freeze":
        print(freeze_holdout(ns.dir, force=ns.force))
    elif ns.cmd == "check":
        print(json.dumps(holdout_check(), indent=2))
    else:
        for r in runs():
            print(f"{r['run_id']:<70} {r.get('status'):<9} acc={r.get('accuracy')} gate={r.get('gate_status')}")
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())

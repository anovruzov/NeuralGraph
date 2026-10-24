"""Serve the arena console.

    python -m research.arena --swarm $SCRATCH/swarm_out --run $SCRATCH/runs/official-ce0e357/seed-0 \\
        --repo . --branch HEAD --state ./runs/arena --runs-root $SCRATCH/runs/arena --port 8765

``--swarm`` is the directory of per-agent stamp dirs; ``--run`` attaches an existing EMERGENCE run (the official
seed-0 run, say) so the harness panels have data before any run is started from the console; ``--runs-root`` is
where runs started with the Start control go.  The harness scenarios run the real ``research.e2e_ingest run``
in process mode and need ``MYCELIC_NATS_SERVER_BIN`` in the environment, exactly as the harness itself does.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .server import App, Config, ConsoleServer, Scenario

ROOT = Path(__file__).resolve().parents[2]


def scenarios(python: str, jobs: int) -> dict[str, Scenario]:
    base = [python, "-m", "research.e2e_ingest", "run", "--world", "200", "--mode", "process", "--jobs", str(jobs)]

    def short(run_dir: Path, run_id: str) -> list[str]:
        # the LiveRunTests configuration: few candidates and fixed queries, so a run takes minutes, not hours
        return base + ["--seed", "0", "--top-n", "20", "--queries", "2", "--out", str(run_dir), "--run-id", run_id]

    def seed0(run_dir: Path, run_id: str) -> list[str]:
        return base + ["--seed", "0", "--out", str(run_dir), "--run-id", run_id]

    return {"short": Scenario("short", "seed 0, 20 candidates, 2 fixed queries, drills on (~5 min)", short),
            "seed0": Scenario("seed0", "seed 0, full candidate list, drills on (~20 min)", seed0)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.arena")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--state", default=str(ROOT / "runs" / "arena"), help="event log and source positions")
    ap.add_argument("--swarm", help="directory of per-agent stamp directories")
    ap.add_argument("--run", help="an existing EMERGENCE run directory to attach")
    ap.add_argument("--run-log", help="that run's stdout log, if kept")
    ap.add_argument("--repo", default=str(ROOT), help="git repository for COMMIT events")
    ap.add_argument("--branch", default="HEAD")
    ap.add_argument("--runs-root", help="where runs started from the console go")
    ap.add_argument("--python", default=sys.executable, help="interpreter for the harness")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--poll", type=float, default=0.5)
    a = ap.parse_args(argv)

    roots = [Path(a.state)]
    for p in (a.swarm, a.run, a.repo, a.runs_root):
        if p:
            roots.append(Path(p))
    cfg = Config(state_dir=Path(a.state), swarm_dir=Path(a.swarm) if a.swarm else None,
                 run_dir=Path(a.run) if a.run else None, run_log=Path(a.run_log) if a.run_log else None,
                 repo=Path(a.repo) if a.repo else None, branch=a.branch,
                 runs_root=Path(a.runs_root) if a.runs_root else None, scenarios=scenarios(a.python, a.jobs),
                 harness_cwd=ROOT, harness_env={"PYTHONPATH": str(ROOT)}, poll_seconds=a.poll, artifact_roots=roots)
    if a.runs_root and not os.environ.get("MYCELIC_NATS_SERVER_BIN"):
        print("note: MYCELIC_NATS_SERVER_BIN is not set; the Start control will fail until it is", file=sys.stderr)
    app = App(cfg)
    srv = ConsoleServer(app, a.host, a.port)
    srv.start()
    print(f"arena console: http://{a.host}:{srv.port}/   events: {app.store.path}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

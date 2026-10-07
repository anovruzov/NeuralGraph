"""Run a whole request locally, every shard against the fake server: the lab's plumbing check without weights.

    python -m lab.dryrun --request PATH --out DIR [--manifest lab/models.json]

``DIR`` must be absent or empty. The plan goes to ``DIR/plan`` (``lab.plan``, in-process); each shard then runs as
``python -m lab.shard run --provider=fake`` into ``DIR/shards/<shard>`` and is sealed with the run step's outcome,
exactly as the workflow will do it. Every record says ``plumbing``: a dry run never measures a model. Aggregation
across shards arrives in G3.

Exit 2 when the plan fails (or ``DIR`` is not empty); 0 when every unit ran to a valid result; 1 otherwise (a unit
invalid, failed, timed out or interrupted, the budget exhausted, or a shard that did not finish).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import StrictJsonError, strict_load

from . import EXIT_OK, EXIT_UNIT, EXIT_USAGE, ROOT
from . import plan as lab_plan

DEFAULT_MANIFEST = ROOT / "lab" / "models.json"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.dryrun",
                                description="Plan a request and run every shard against the fake server.")
    p.add_argument("--request", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--manifest", default=os.path.relpath(DEFAULT_MANIFEST))
    return p


def _shard(*args: str) -> int:
    return subprocess.run([sys.executable, "-m", "lab.shard", *args], cwd=ROOT, stdin=subprocess.DEVNULL).returncode


def _status(shard_dir: Path) -> dict[str, Any] | None:
    failed = False
    try:
        status = strict_load((shard_dir / "status.json").read_bytes())
    except (OSError, StrictJsonError):
        failed = True
    return None if failed or not isinstance(status, dict) else status


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(os.path.abspath(args.out))
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        print("error: --out must be absent or an empty directory", file=sys.stderr)
        return EXIT_USAGE
    plan_dir = out / "plan"
    if lab_plan.main(["--request", args.request, "--manifest", args.manifest, "--out", str(plan_dir)]) != EXIT_OK:
        return EXIT_USAGE
    plan = strict_load((plan_dir / "plan.json").read_bytes())
    failing = False
    for shard in plan["shards"]:
        shard_dir = out / "shards" / shard["shard"]
        code = _shard("run", f"--plan={plan_dir / 'plan.json'}", f"--shard={shard['shard']}", f"--out={shard_dir}",
                      "--provider=fake")
        outcome = "success" if code == EXIT_OK else "failure"
        sealed = _shard("seal", f"--out={shard_dir}", f"--shard={shard['shard']}", f"--step-outcome=run={outcome}")
        status = _status(shard_dir)
        failing = (failing or code != EXIT_OK or sealed != EXIT_OK or status is None
                   or status.get("missing_units") != [])
    return EXIT_UNIT if failing else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

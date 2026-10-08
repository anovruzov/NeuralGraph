"""Run a whole request locally, every shard against the fake server: the lab's plumbing check without weights.

    python -m lab.dryrun --request PATH --out DIR [--manifest lab/models.json] [--openfda-base-url URL]

``DIR`` must be absent or empty. The steps are the workflow's, in the same order and with the same files:

1. the plan goes to ``DIR/plan`` (``lab.plan``, in-process), and its preregistration to ``DIR/plan/prereg``
   (``lab.prereg``, in-process; it runs the harnesses' own prereg steps and E2's model-free rehearsal);
2. each shard runs as ``python -m lab.shard run --provider=fake`` into ``DIR/shards/<shard>`` (with
   ``--openfda-base-url`` when given: a test stub in place of api.fda.gov), is sealed with the
   run step's outcome and the plan (``seal --plan``), and its summary is rendered to
   ``DIR/shards/<shard>/summary/summary.md`` and ``summary.sources.json``, as the run job does after its seal;
3. the plan summary is rendered to ``DIR/plan/summary.md`` and ``summary.sources.json``;
4. the shards are aggregated in-process into ``DIR/report`` (``lab.aggregate``, with ``DIR/provision`` as the
   provision records, absent in a dry run) and the report summary rendered to ``DIR/report/report.md`` and
   ``report.sources.json``.

Every record says ``plumbing``: a dry run never measures a model. stdout is the plan line, the prereg line, one line
per unit and the aggregate line. Without ``--openfda-base-url`` an openFDA unit fetches from api.fda.gov itself.
The plan reads ``LAB_HAS_HOSTED`` from the environment as the workflow's plan step does: unless it is ``true``, the
hosted units are skipped. With it, and with the two hosted variables set (``lab.hosted``: the key and the base URL),
the hosted units call the configured host for real (``--provider fake`` replaces only the local model server), and
are still labelled plumbing.

Exit 2 when the plan, the preregistration, the aggregate or a summary fails (or ``DIR`` is not empty); 1 when a unit
did not run to a valid result (invalid, failed, timed out, interrupted or skipped, or a shard that did not finish);
else 0.
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
from . import aggregate as lab_aggregate
from . import plan as lab_plan
from . import prereg as lab_prereg
from . import summary as lab_summary

DEFAULT_MANIFEST = ROOT / "lab" / "models.json"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.dryrun",
                                description="Plan a request and run every shard against the fake server.")
    p.add_argument("--request", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--manifest", default=os.path.relpath(DEFAULT_MANIFEST))
    p.add_argument("--openfda-base-url", help="tests only: an openFDA stub for the openFDA unit's fetches")
    return p


def _shard(*args: str) -> int:
    sys.stdout.flush()
    return subprocess.run([sys.executable, "-m", "lab.shard", *args], cwd=ROOT, stdin=subprocess.DEVNULL).returncode


def _summary(mode: str, directory: Path, md: Path, sources: Path) -> int:
    return lab_summary.main([mode, "--dir", str(directory), "--md-out", str(md), "--sources-out", str(sources)])


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
    plan_path = plan_dir / "plan.json"
    if lab_plan.main(["--request", args.request, "--manifest", args.manifest, "--out", str(plan_dir)]) != EXIT_OK:
        return EXIT_USAGE
    sys.stdout.flush()
    if lab_prereg.main(["--plan", str(plan_path)]) != EXIT_OK:
        return EXIT_USAGE
    plan = strict_load(plan_path.read_bytes())
    failing, summaries_ok = False, True
    openfda = [f"--openfda-base-url={args.openfda_base_url}"] if args.openfda_base_url is not None else []
    for shard in plan["shards"]:
        shard_dir = out / "shards" / shard["shard"]
        code = _shard("run", f"--plan={plan_path}", f"--shard={shard['shard']}", f"--out={shard_dir}",
                      "--provider=fake", *openfda)
        outcome = "success" if code == EXIT_OK else "failure"
        sealed = _shard("seal", f"--out={shard_dir}", f"--shard={shard['shard']}", f"--plan={plan_path}",
                        f"--step-outcome=run={outcome}")
        summaries_ok &= _summary("shard", shard_dir, shard_dir / "summary" / "summary.md",
                                 shard_dir / "summary" / "summary.sources.json") == EXIT_OK
        status = _status(shard_dir)
        failing = (failing or code != EXIT_OK or sealed != EXIT_OK or status is None
                   or status.get("missing_units") != [])
    summaries_ok &= _summary("plan", plan_dir, plan_dir / "summary.md", plan_dir / "summary.sources.json") == EXIT_OK
    report_dir = out / "report"
    sys.stdout.flush()
    aggregated = lab_aggregate.main(["--plan", str(plan_path), "--provision", str(out / "provision"),
                                     "--shards", str(out / "shards"), "--manifest", args.manifest,
                                     "--out", str(report_dir)])
    if aggregated != EXIT_OK:
        return EXIT_USAGE
    summaries_ok &= _summary("report", report_dir, report_dir / "report.md",
                             report_dir / "report.sources.json") == EXIT_OK
    if not summaries_ok:
        return EXIT_USAGE
    return EXIT_UNIT if failing else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

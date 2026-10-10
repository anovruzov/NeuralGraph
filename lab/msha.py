"""MSHA's accident file in the lab workflow: the two steps of latency test L001 (``lab.l1``) that run outside a unit.

    python -m lab.msha cache-key --plan PLAN --github-output FILE
    python -m lab.msha guard --plan PLAN --dir DIR [--dir DIR ...] [--raw DIR]

``docs/collective/L001/CHOICE-L001.md`` is the rule; K9, K10 and K14 are its amendments these steps carry out.

**cache-key** (the plan job, after the preregistration, K10): appends ``l1_cache_key=<key>`` to ``FILE`` (the job's
``GITHUB_OUTPUT``). The key is the verified preregistration's ``l1.cache_key`` (``lab.prereg.l1_cache_key``:
``lab-msha-<16 hex of the file's sha256>``), or empty when the plan has no L1 units or its preregistration does not
verify. The plan job saves ``lab-msha`` beside the plan's directory (``lab.l1.raw_dir``, where the preregistration
downloaded the file) in the workflow's cache under that key, and the run and aggregate jobs restore it from there
before anything reads it; a unit that misses the cache downloads the file and checks its sha256 itself.

**guard** (K9, K14): :mod:`lab.l1guard` over every file under each ``DIR`` (the plan directory, a shard root before and
after its seal, the report directory), always before ``lab.summary`` reads the directory and before upload, with the
values that ``PLAN``'s own text holds left out of its value sets (K14: ``PLAN`` is passed to the guard, which reads it
and prints the number left out, never a value). ``RAW`` defaults to ``lab.l1.raw_dir`` of the plan's directory. A plan
without L1 units is left alone (exit 0); a plan that an earlier guard withheld (its stub) was an L1 plan, so the guard
runs, with nothing left out (K14). Exit 1 when a file was withheld, which fails the job.

The console gets fixed lines only; an error is its class name and code location (``lab.l1guard.error_line``).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import l1 as lab_l1
from .l1guard import error_line, is_stub, main_guard
from .prereg import PreregError, load_prereg


def withheld(plan_path: str) -> bool:
    """Whether the plan at ``plan_path`` is a file an L1 guard withheld (its stub): only an L1 plan's guard writes
    one, so the guard still runs over the directories, with nothing left out (K14)."""
    try:
        return is_stub(Path(plan_path).read_bytes())
    except OSError:
        return False


def cache_key(plan_path: str) -> str:
    """The verified preregistration's L1 cache key, or ``""`` (see the module docstring)."""
    if not lab_l1.plan_has_l1(plan_path):
        return ""
    try:
        block = load_prereg(plan_path).manifest.get("l1")
    except PreregError:
        return ""
    key = block.get("cache_key") if isinstance(block, dict) else None
    return key if isinstance(key, str) else ""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.msha", description="MSHA's file in the lab workflow (L001).")
    sub = p.add_subparsers(dest="command", required=True)
    key = sub.add_parser("cache-key", help="the file's cache key for the workflow (K10)")
    key.add_argument("--plan", required=True)
    key.add_argument("--github-output", required=True)
    guard = sub.add_parser("guard", help="scan what a run uploads (rule 12, K9)")
    guard.add_argument("--plan", required=True)
    guard.add_argument("--dir", action="append", required=True)
    guard.add_argument("--raw")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "cache-key":
            key = cache_key(args.plan)
            with open(args.github_output, "a", encoding="utf-8") as fh:
                fh.write(f"l1_cache_key={key}\n")
            print(f"msha cache-key: {'yes' if key else 'none'}", flush=True)
            return 0
        if not lab_l1.plan_has_l1(args.plan) and not withheld(args.plan):
            print("l1 guard: no l1 units", flush=True)
            return 0
        raw = args.raw or str(lab_l1.raw_dir(Path(os.path.abspath(args.plan)).parent))
        return main_guard(args.dir, raw, args.plan)
    except Exception as err:       # noqa: BLE001 (K9: an error's class and location, never its text)
        print(error_line(f"msha {args.command}", err), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())

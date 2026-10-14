"""Stand-ins for the term run's draft step (``python -m lab.jt draft``), which ``lab.prereg`` runs as a subprocess
under its step runner (``tests/lab/test_lab_jt.py`` puts this module and one of its commands in
``lab.prereg.JT_MODULE`` and ``lab.prereg.JT_DRAFT_COMMAND``).

    python -m tests.lab.stubs.jt_stub COMMAND --export FILE --out DIR

Each command takes the draft's two flags and stands for one way the step can end:

* ``sleep``: never finishes (the step's timeout);
* ``kill``: the process sends itself SIGKILL, as the kernel's out-of-memory killer does;
* ``memory``: ``lab.jt``'s own draft command whose drafter raises ``MemoryError`` (its exit code for that);
* ``refuse``: ``lab.jt``'s own draft command whose drafter refuses the export (``DraftError``);
* ``floor``: ``lab.jt``'s own draft command, unchanged but for the check, whose n-gram scan is made to fail;
* ``nothing``: exits 0 and writes no result;
* ``garbage``: exits 0 with a result that is not the draft's document.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any
from unittest import mock

COMMANDS = ("sleep", "kill", "memory", "refuse", "floor", "nothing", "garbage")


def _draft(argv: list[str]) -> int:
    from lab import jt

    return jt.main(["draft", *argv])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(prog="python -m tests.lab.stubs.jt_stub")
    p.add_argument("command", choices=COMMANDS)
    p.add_argument("--export", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    flags = ["--export", args.export, "--out", args.out]
    if args.command == "sleep":
        time.sleep(600)
        return 0
    if args.command == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
        return 0
    if args.command in ("nothing", "garbage"):
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        if args.command == "garbage":
            (out / "draft.json").write_text("[1, 2, 3]\n", encoding="utf-8")
        return 0
    from lab import jt
    from mycelic.collective.onboard.draft import DraftError

    if args.command == "memory":
        with mock.patch.object(jt, "run_draft", side_effect=MemoryError()):
            return _draft(flags)
    if args.command == "refuse":
        with mock.patch.object(jt, "run_draft", side_effect=DraftError("no specific predicate")):
            return _draft(flags)
    real = jt.check_pack

    def failing(*a: Any, **k: Any) -> dict[str, Any]:
        result = real(*a, **k)
        result["checks"]["ngram"].update(passed=False, failures=1)
        result["passed"] = False
        return result

    with mock.patch.object(jt, "check_pack", failing):
        return _draft(flags)


if __name__ == "__main__":
    sys.exit(main())

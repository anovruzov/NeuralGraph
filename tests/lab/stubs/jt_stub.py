"""Stand-ins for the term run's draft step (``python -m lab.jt draft``), which ``lab.prereg`` runs as a subprocess
under its step runner (``tests/lab/test_lab_jt.py`` puts this module and one of its commands in
``lab.prereg.JT_MODULE`` and ``lab.prereg.JT_DRAFT_COMMAND``).

    python -m tests.lab.stubs.jt_stub COMMAND --export FILE --out DIR

Each command takes the draft's two flags and stands for one way the step can end:

* ``sleep``: sleeps far longer than any test waits (:data:`SLEEP_S`), then runs ``lab.jt``'s own draft, which writes
  a passing result: only the step's timeout can refuse it;
* ``kill``: the process sends itself SIGKILL, as the kernel's out-of-memory killer does;
* ``memory``: ``lab.jt``'s own draft command whose drafter raises ``MemoryError`` (its exit code for that);
* ``refuse``: ``lab.jt``'s own draft command whose drafter refuses the export (``DraftError``);
* ``crash``: ``lab.jt``'s own draft command whose draft raises ``RuntimeError`` (its exit code for any other error);
* ``uncaught``: the process raises before the draft runs, so Python exits 1 with no result;
* ``floor``: ``lab.jt``'s own draft command, unchanged but for the check, whose n-gram scan is made to fail;
* ``floorless``: exits with the failed check's code and writes no result;
* ``unchecked``: ``lab.jt``'s own draft, then the result's check rewritten to failed, and exit 0;
* ``nothing``: exits 0 and writes no result;
* ``garbage``: exits 0 with a result that is not the draft's document.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any
from unittest import mock

COMMANDS = ("sleep", "kill", "memory", "refuse", "crash", "uncaught", "floor", "floorless", "unchecked", "nothing",
            "garbage")
SLEEP_S = 3600


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
        time.sleep(SLEEP_S)
        return _draft(flags)
    if args.command == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
        return 0
    if args.command == "uncaught":
        raise RuntimeError("an error before the draft")
    if args.command in ("nothing", "garbage", "floorless"):
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        if args.command == "garbage":
            (out / "draft.json").write_text("[1, 2, 3]\n", encoding="utf-8")
        if args.command == "floorless":
            from lab import jt

            return jt.EXIT_FLOOR
        return 0
    from lab import jt
    from mycelic.collective.onboard.draft import DraftError

    if args.command == "memory":
        with mock.patch.object(jt, "run_draft", side_effect=MemoryError()):
            return _draft(flags)
    if args.command == "refuse":
        with mock.patch.object(jt, "run_draft", side_effect=DraftError("no specific predicate")):
            return _draft(flags)
    if args.command == "crash":
        with mock.patch.object(jt, "run_draft", side_effect=RuntimeError("a fault inside the draft")):
            return _draft(flags)
    if args.command == "unchecked":
        code = _draft(flags)
        path = Path(args.out) / jt.DRAFT_RESULT
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["check"]["passed"] = False
        path.write_text(json.dumps(doc), encoding="utf-8")
        return code
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

"""File-level fault injection (PLAN_v1 §B.2). The planner decides which holder gets which fault (``Plan.fault_plan``); this module
applies the ones that act on a source file's lines. Faults that act on the run (replay, restart mid-ingest) are carried to the
feeder as flags in the sources manifest. Nothing here sees gold: only record lines and the holder-keyed fault flags."""
from __future__ import annotations

import json
import random
from typing import Any

MALFORMED_LINES = ('{"type": "document", "id": "broken-1", "text": "this line is cut off', "this is not json at all",
                   json.dumps({"type": "document", "text": "a record without an id must be counted, not stop the import"}), "[1, 2, 3")


def apply_file_faults(lines: list[str], flags: dict[str, Any], rng: random.Random, *, rid_of_line: list[str | None]) -> list[str]:
    """``lines`` are record lines (header excluded) in chronological order; ``rid_of_line[i]`` is the record id of line i (or None).
    Returns the lines as the exporter wrote them with the faults of ``flags`` applied."""
    out = list(lines)
    rids = list(rid_of_line)
    if flags.get("out_of_order"):
        # newest first: an older version of a record is delivered after its newer version
        order = list(range(len(out)))[::-1]
        out = [out[i] for i in order]
        rids = [rids[i] for i in order]
    dup = set(flags.get("duplicate") or [])
    if dup:
        nl, nr = [], []
        for ln, rid in zip(out, rids):
            nl.append(ln)
            nr.append(rid)
            if rid in dup:
                nl.append(ln)
                nr.append(rid)
        out, rids = nl, nr
    if flags.get("malformed"):
        for bad in MALFORMED_LINES:
            out.insert(rng.randrange(len(out) + 1), bad)
    return out

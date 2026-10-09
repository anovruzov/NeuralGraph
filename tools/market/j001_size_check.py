#!/usr/bin/env python
"""Size check for judge test J001: how long one J1 unit (one model, one part) takes at the call times the lab has.

    python tools/market/j001_size_check.py

A unit asks 50 questions (150 records in 6 parts, two questions a record), one call each. Its budget is 150 minutes
less ``lab.units.SIM_BUDGET_MARGIN_S``. The per-call times are figures the lab recorded, each named with its source;
two rows are bounds for one judge call on a CPU: a first-token time measured with a prompt larger than any judge
prompt, plus a reply at the judge's 256-token cap at the decode rate measured there.

It also renders the messages of a judge call with the narrative at its 6,000-character cut, and of E3's extraction
workload, with the repository's own ``render_messages``, and prints their characters, so the first claim can be
checked. No network, no model.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lab import j1  # noqa: E402
from lab.units import SIM_BUDGET_MARGIN_S  # noqa: E402
from mycelic.collective.edge.verify import judge_schema, judge_task  # noqa: E402
from mycelic.collective.experiments.e3_latency import WORKLOADS, filler  # noqa: E402
from mycelic.collective.inference.tasks import render_messages  # noqa: E402
from mycelic.collective.packs.loader import load_pack  # noqa: E402
from mycelic.collective.schemacheck import compile as compile_schema  # noqa: E402

RECORDS, PARTS, MINUTES = 150, 6, 150          # CHOICE-J001.md, rule 9
CUT = 6000                                     # judge_payload's narrative cut (CHOICE-J001.md, rule 3)
# (seconds a call, source)
MEDIANS = (
    (4.0, "g0-001, a-4b judge calls, median (docs/lab/RESULTS.md, run 8)"),
    (25.8, "R002, a-4b extraction calls, median (CHOICE-R002.md, run 1)"),
    (84.981, "check-001, a-4b E3 extraction workload, median, AMD EPYC 9V74 (RESULTS.md, run 1)"),
    (177.0, "R002, a-4b extraction calls, 95th percentile (CHOICE-R002.md, run 1)"),
)
# (first-token seconds, decode tokens a second, source): E3's extraction workload at concurrency 1
BOUNDS = (
    (75.934, 7.0, "check-001, a-4b, AMD EPYC 9V74 (RESULTS.md, run 1)"),
    (25.681, 4.2, "main-001, a-4b, Intel Xeon Platinum 8573C, R002's CPU (RESULTS.md, run 5)"),
)


def prompt_chars() -> tuple[int, int]:
    pack = load_pack(ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack")
    words = filler(5000, "j001-size").split()
    text = ""
    for word in words:
        if len(text) + len(word) + 1 > CUT:
            break
        text = (text + " " + word).strip()
    record = {"record_ref": "X1", "language": "en", "codes": [], "entities": {"vehicle": ["FORD-F150-2020"]},
              "narrative": text, "persons": {}}
    question = {"entity_type": "vehicle", "entity_id": "FORD-F150-2020", "predicate": "electrical_system"}
    judge = render_messages(judge_task(), j1.payload(pack, question, record), compile_schema(judge_schema()))
    task, schema, n_words = WORKLOADS["extraction"]
    e3 = render_messages(task, {"text": filler(n_words, "j001-size")}, compile_schema(schema))
    return sum(len(m["content"]) for m in judge), sum(len(m["content"]) for m in e3)


def main() -> int:
    questions = RECORDS // PARTS * 2
    budget = MINUTES * 60 - SIM_BUDGET_MARGIN_S
    judge_chars, e3_chars = prompt_chars()
    print(f"questions a part {questions}; budget {budget} s ({budget / 60:.1f} min)")
    print(f"prompt characters: judge call at the {CUT}-character cut {judge_chars}; E3 extraction workload {e3_chars}")
    print(f"judge max_tokens {judge_task().max_tokens}")
    for seconds, source in MEDIANS:
        minutes = questions * seconds / 60
        print(f"{seconds} s a call ({source}): {minutes:.1f} min a part, "
              f"budget over that {budget / (questions * seconds):.1f}")
    for ttft, rate, source in BOUNDS:
        call = ttft + judge_task().max_tokens / rate
        print(f"bound {ttft} s + {judge_task().max_tokens} tokens at {rate}/s = {call:.1f} s a call ({source}): "
              f"{questions * call / 60:.1f} min a part, budget over that {budget / (questions * call):.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

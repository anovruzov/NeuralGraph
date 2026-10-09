#!/usr/bin/env python
"""Check for judge test J001's record-blind control: the first control (leave one record out) against the amended one
(the four predicates filed most often in the whole complaint file), and the predicate-only bound.

    python tools/market/j001_control_check.py

Every judge here is record-blind: it sees a question's predicate and kind count, never the record.

- ``leave-one-out``: the control of rule 5 as first amended. A question is confirmed when its predicate was asked more
  often as a positive than as a negative among the other records' questions; a tie refutes.
- ``top four``: the control of rule 5 as amended again. A question is confirmed when its predicate is one of the four
  filed most often in the whole complaint file (``nhtsa-probe.json``'s component counts, all makes and all years,
  mapped to the pack's predicates by ``j001_prior_probe.weights``). It uses no label of the records it is scored on.
- ``bound``: the most any judge that sees only the predicate can score on the questions: per predicate, the larger of
  its positive and its negative count, summed, over all the questions. It is fitted to the answers it is scored on, so
  it is optimistic.

Each record has one positive and one negative question, so balanced accuracy is the share of questions answered right.

It prints two things. First, an exactly balanced draw: 8 constructed records, each predicate asked as often as a
positive as as a negative. Then draws of 150 records with constructed labels: each record is filed under predicates
drawn with the whole-file counts as weights, one per record, or one, two or three with chances 0.70, 0.23 and 0.07
(1.37 on average, near R001's labels: 206 claims on 150 records). Their questions are drawn by J001's own code
(``lab.j1.build_questions``, the amended rule 2), and, for comparison, with rule 2's first uniform negatives. For each
judge and draw rule, the mean, the smallest and the largest balanced accuracy over 40 draws. No network, no model.
"""
from __future__ import annotations

import importlib.util
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lab import j1  # noqa: E402
from lab.goldlabels import NHTSA_PACK  # noqa: E402
from mycelic.collective.jsonio import canonical_dumps  # noqa: E402
from mycelic.collective.packs.loader import load_pack  # noqa: E402

N, DRAWS, K = 150, 40, 4
SHAPES = (("one filed predicate a record", (1.0, 0.0, 0.0)), ("1.37 filed predicates a record", (0.70, 0.23, 0.07)))


def _probe() -> Any:
    spec = importlib.util.spec_from_file_location("j001_prior_probe", Path(__file__).with_name("j001_prior_probe.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def leave_one_out(qs: list[dict[str, Any]]) -> list[bool]:
    """Confirm per question, by the first amended rule 5."""
    out = []
    for q in qs:
        pos = sum(1 for o in qs if o["record_ref"] != q["record_ref"] and o["predicate"] == q["predicate"]
                  and o["kind"] == "positive")
        neg = sum(1 for o in qs if o["record_ref"] != q["record_ref"] and o["predicate"] == q["predicate"]
                  and o["kind"] == "negative")
        out.append(pos > neg)
    return out


def top(qs: list[dict[str, Any]], four: set[str]) -> list[bool]:
    return [q["predicate"] in four for q in qs]


def balanced_accuracy(qs: list[dict[str, Any]], confirm: list[bool]) -> float:
    right = sum(c == (q["kind"] == "positive") for q, c in zip(qs, confirm))
    return right / len(qs)


def bound(qs: list[dict[str, Any]]) -> float:
    counts: dict[str, list[int]] = {}
    for q in qs:
        counts.setdefault(q["predicate"], [0, 0])[q["kind"] == "negative"] += 1
    return sum(max(c) for c in counts.values()) / len(qs)


def labels(n: int, seed: int, shape: tuple[float, ...], weights: dict[str, int]) -> tuple[bytes, float]:
    names = sorted(weights)
    rng = random.Random(f"j001-control:{seed}")
    lines, filed_total = [], 0
    for i in range(n):
        k = rng.choices((1, 2, 3), shape)[0]
        filed: set[str] = set()
        while len(filed) < k:
            filed.add(rng.choices(names, [weights[p] for p in names])[0])
        filed_total += len(filed)
        record = {"record_ref": f"R{i:05d}", "language": "en", "codes": [], "entities": {"vehicle": ["V-1"]},
                  "narrative": "constructed"}
        gold = [{"entity_type": "vehicle", "entity_id": "V-1", "predicate": p} for p in sorted(filed)]
        lines.append(canonical_dumps({"record": record, "gold": gold}) + "\n")
    return "".join(lines).encode("utf-8"), filed_total / n


def uniform(pack: Any, qs: list[dict[str, Any]], seed: int = 1) -> list[dict[str, Any]]:
    """The same questions with rule 2's first negatives: uniform over the candidates."""
    return [dict(q, predicate=random.Random(f"j1:{seed}:{q['record_ref']}:negative").choice(
        j1.candidates(pack, q["filed"]))) if q["kind"] == "negative" else q for q in qs]


def balanced_case(four: set[str]) -> list[dict[str, Any]]:
    """8 records over 4 predicates (2 of them among the top four): record i asks P[i % 4] as its positive and
    P[(i + 1) % 4] as its negative, so each predicate is asked twice as a positive and twice as a negative."""
    names = sorted(four)[:2] + ["seats", "tires"]
    qs = []
    for i in range(8):
        for kind, p in (("positive", names[i % 4]), ("negative", names[(i + 1) % 4])):
            qs.append({"record_ref": f"B{i}", "kind": kind, "predicate": p})
    return qs


def spread(values: list[float]) -> str:
    return f"mean {statistics.mean(values):.3f} min {min(values):.3f} max {max(values):.3f}"


def main() -> int:
    probe = _probe()
    weights = probe.weights()
    order = sorted(weights, key=lambda p: (-weights[p], p))
    four = set(order[:K])
    pack = load_pack(ROOT / NHTSA_PACK)
    print(f"the four predicates filed most in the whole file: {', '.join(order[:K])}")
    qs = balanced_case(four)
    print(f"\nexactly balanced draw, {len(qs) // 2} records: leave-one-out "
          f"{balanced_accuracy(qs, leave_one_out(qs)):.3f}, top four {balanced_accuracy(qs, top(qs, four)):.3f}, "
          f"bound {bound(qs):.3f}")
    judges: dict[str, Callable[[list[dict[str, Any]]], float]] = {
        "leave-one-out": lambda q: balanced_accuracy(q, leave_one_out(q)),
        "top four": lambda q: balanced_accuracy(q, top(q, four)),
        "bound": bound,
    }
    for name, shape in SHAPES:
        results: dict[tuple[str, str], list[float]] = {}
        filed, near = [], []
        for seed in range(DRAWS):
            data, per_record = labels(N, seed, shape, weights)
            filed.append(per_record)
            amended = j1.read_questions(j1.build_questions(pack, data, seed=1, parts=1)[0])
            asked: dict[str, list[int]] = {}
            for q in amended:
                asked.setdefault(q["predicate"], [0, 0])[q["kind"] == "negative"] += 1
            near.append(sum(sum(c) for c in asked.values() if 0 <= c[0] - c[1] <= 1))
            for rule, questions in (("amended", amended), ("first, uniform", uniform(pack, amended))):
                for judge, score in judges.items():
                    results.setdefault((rule, judge), []).append(score(questions))
        print(f"\n{name}: {DRAWS} draws of {N} records, {statistics.mean(filed):.2f} filed predicates a record on "
              f"average")
        print(f"  amended draw: questions on a predicate asked as often as a positive as as a negative, or once more "
              f"as a positive: {spread([float(x) for x in near])} of {2 * N}")
        for (rule, judge), values in results.items():
            print(f"  {rule:15s} {judge:14s} balanced accuracy {spread(values)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

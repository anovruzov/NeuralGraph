"""Balanced evaluation sets: 60 questions per run, 15 from each category.

Why balanced rather than proportional. The corpus is 55% multi_hop and 6%
open_domain, so a proportional 60-question sample would carry ~4 open_domain
questions and a single flip would move that category by 25 points. Equal
allocation gives every category the same resolution, which is what makes the
per-category insight the loop is steered by trustworthy.

What balance costs, and it must be said in the report rather than buried: the
overall accuracy of a balanced set is **not** an estimate of corpus accuracy.
It is a mean over four equally-weighted categories, and the corpus is not
equally weighted. Corpus-weighted accuracy is reported alongside it, computed
by reweighting the same per-category rates by their true corpus shares, so the
two are never confused.

The set is drawn once and frozen. Every round scores the *same* question IDs,
which is what makes round-over-round comparison paired rather than two
independent samples.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from evaluation.improvement_loop.corpus import CATEGORIES, Splits

PER_CATEGORY = 15
ROUND_SIZE = PER_CATEGORY * len(CATEGORIES)

# Distinct from the split seed so that re-drawing the round set cannot
# accidentally reproduce split boundaries.
ROUND_SEED = 20260825001


@dataclass(frozen=True)
class EvalSet:
    name: str
    seed: int
    per_category: int
    question_ids: tuple[int, ...]
    by_category: dict[str, tuple[int, ...]]
    source_split: str
    corpus_digest: str

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["question_ids"] = list(d["question_ids"])
        d["by_category"] = {k: list(v) for k, v in sorted(d["by_category"].items())}
        return d

    @property
    def digest(self) -> str:
        """Identity of this evaluation set. Any membership change moves it."""
        return hashlib.sha256(
            json.dumps(sorted(self.question_ids)).encode("utf-8")
        ).hexdigest()[:16]


def build_eval_set(
    name: str,
    split_ids: tuple[int, ...],
    source_split: str,
    records: list[dict[str, Any]],
    corpus_digest: str,
    per_category: int = PER_CATEGORY,
    seed: int = ROUND_SEED,
) -> EvalSet:
    """Draw `per_category` questions of each category from one split.

    Raises if a category cannot supply enough questions rather than silently
    returning a smaller, unbalanced set -- a quietly short category would make
    its per-category rate incomparable across runs.
    """
    by_id = {int(r["id"]): r for r in records}
    pool: dict[str, list[int]] = defaultdict(list)
    for qid in split_ids:
        pool[str(by_id[qid].get("category"))].append(int(qid))

    chosen: dict[str, tuple[int, ...]] = {}
    for category in CATEGORIES:
        ids = sorted(pool.get(category, ()))
        if len(ids) < per_category:
            raise ValueError(
                f"{name}: category {category!r} has {len(ids)} questions in "
                f"{source_split}, need {per_category}. Refusing to emit an "
                "unbalanced set."
            )
        rng = random.Random(f"{seed}:{name}:{category}")
        rng.shuffle(ids)
        chosen[category] = tuple(sorted(ids[:per_category]))

    flat = tuple(sorted(q for ids in chosen.values() for q in ids))
    assert len(flat) == per_category * len(CATEGORIES)
    assert len(set(flat)) == len(flat), "duplicate question in eval set"

    return EvalSet(
        name=name,
        seed=seed,
        per_category=per_category,
        question_ids=flat,
        by_category=chosen,
        source_split=source_split,
        corpus_digest=corpus_digest,
    )


def build_all(splits: Splits, records: list[dict[str, Any]]) -> dict[str, EvalSet]:
    """The two frozen evaluation sets: validation (every round) and locked test."""
    return {
        "validation": build_eval_set(
            "validation", splits.validation, "validation", records, splits.corpus_digest
        ),
        "locked_test": build_eval_set(
            "locked_test", splits.locked_test, "locked_test", records, splits.corpus_digest
        ),
    }


def verify_eval_sets(sets: dict[str, EvalSet], splits: Splits) -> None:
    """Raise on leakage between the evaluation sets or out of their splits."""
    val = set(sets["validation"].question_ids)
    test = set(sets["locked_test"].question_ids)

    if val & test:
        raise ValueError(
            f"EVAL SET LEAKAGE: {len(val & test)} ids in both validation and "
            f"locked test: {sorted(val & test)[:5]}"
        )
    if not val <= set(splits.validation):
        raise ValueError("validation eval set contains ids outside the validation split")
    if not test <= set(splits.locked_test):
        raise ValueError("locked-test eval set contains ids outside the locked-test split")
    if val & set(splits.development):
        raise ValueError("validation eval set overlaps the development split")
    if test & set(splits.development):
        raise ValueError("locked-test eval set overlaps the development split")


def corpus_weights(records: list[dict[str, Any]]) -> dict[str, float]:
    """True category shares, used to reweight a balanced score back to corpus scale."""
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[str(record.get("category"))] += 1
    total = sum(counts.values())
    return {k: v / total for k, v in sorted(counts.items())}


def save_eval_sets(sets: dict[str, EvalSet], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        name: dict(s.as_dict(), digest=s.digest) for name, s in sorted(sets.items())
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_eval_sets(path: Path) -> dict[str, EvalSet]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for name, d in payload.items():
        out[name] = EvalSet(
            name=d["name"],
            seed=d["seed"],
            per_category=d["per_category"],
            question_ids=tuple(d["question_ids"]),
            by_category={k: tuple(v) for k, v in d["by_category"].items()},
            source_split=d["source_split"],
            corpus_digest=d["corpus_digest"],
        )
    return out

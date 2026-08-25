"""Freeze the LoCoMo corpus and cut deterministic, stratified splits.

Split integrity is the load-bearing property of the whole improvement loop. If
the development set and the locked test set overlap -- or if membership drifts
between rounds -- then every reported gain is unfalsifiable. So:

* The corpus is hashed before any code changes. Every later artifact carries
  that digest, and a mismatch invalidates the run rather than being repaired.
* Splits are cut from a recorded seed and stored as explicit question-ID lists,
  not recomputed on demand. Recomputation is how membership silently changes
  when an upstream sort or dict order moves.
* Stratification is by (category, conversation) jointly. Category alone would
  let a whole conversation land in one split, and conversations differ enough
  that a per-conversation effect would be indistinguishable from a real fix.

Nothing here calls a model or reads a gold answer. It is pure bookkeeping, and
it is the part that has to be right before any money is spent.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

CORPUS_PATH = Path("demo/maximal.json")

# Recorded in the artifact, not chosen here. Changing it re-cuts every split and
# invalidates comparison against previously saved rounds.
SPLIT_SEED = 20260825

DEV_FRACTION = 0.40
VAL_FRACTION = 0.20
TEST_FRACTION = 0.40

CATEGORIES = ("single_hop", "multi_hop", "temporal", "open_domain")


def load_corpus(path: Path = CORPUS_PATH) -> list[dict[str, Any]]:
    """Return the corpus records. Raises rather than returning a partial list."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = payload.get("results")
    if not isinstance(records, list) or not records:
        raise ValueError(f"{path}: no 'results' list")
    return records


def corpus_digest(records: Iterable[dict[str, Any]]) -> str:
    """Membership digest: question identity only, never answers.

    Deliberately excludes ``gold_answer``, ``generated_answer`` and ``correct``.
    The digest must detect a change in *which questions are asked*; it must not
    change merely because a model produced different output, or the digest
    could not be used to prove membership stability across rounds.
    """
    h = hashlib.sha256()
    for record in sorted(records, key=lambda r: int(r["id"])):
        h.update(
            json.dumps(
                {
                    "id": int(record["id"]),
                    "category": record.get("category"),
                    "conversation": str(record.get("conversation")),
                    "question": record.get("question"),
                },
                sort_keys=True,
            ).encode("utf-8")
        )
    return h.hexdigest()


def _stratum(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("category")), str(record.get("conversation")))


@dataclass(frozen=True)
class Splits:
    seed: int
    corpus_digest: str
    corpus_size: int
    development: tuple[int, ...]
    validation: tuple[int, ...]
    locked_test: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("development", "validation", "locked_test"):
            d[key] = list(d[key])
        return d

    @property
    def all_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self.development + self.validation + self.locked_test))


def cut_splits(records: list[dict[str, Any]], seed: int = SPLIT_SEED) -> Splits:
    """Cut 40/20/40 development/validation/locked-test, stratified jointly.

    Within each (category, conversation) stratum the IDs are sorted, then
    shuffled by a stratum-local RNG seeded from the global seed and the stratum
    key. A stratum-local seed keeps a stratum's assignment stable even if an
    unrelated stratum changes size, which matters when the corpus is filtered.
    """
    by_stratum: dict[tuple[str, str], list[int]] = defaultdict(list)
    for record in records:
        by_stratum[_stratum(record)].append(int(record["id"]))

    dev: list[int] = []
    val: list[int] = []
    test: list[int] = []

    for key in sorted(by_stratum):
        ids = sorted(by_stratum[key])
        rng = random.Random(f"{seed}:{key[0]}:{key[1]}")
        rng.shuffle(ids)
        n = len(ids)
        # Largest-remainder allocation. Plain rounding loses or duplicates a
        # question when a stratum is small, and the splits must partition
        # exactly -- an off-by-one here is a leak.
        raw = {
            "dev": n * DEV_FRACTION,
            "val": n * VAL_FRACTION,
            "test": n * TEST_FRACTION,
        }
        counts = {k: int(v) for k, v in raw.items()}
        remainder = n - sum(counts.values())
        for k, _ in sorted(raw.items(), key=lambda kv: (-(kv[1] % 1), kv[0]))[:remainder]:
            counts[k] += 1
        assert sum(counts.values()) == n

        dev.extend(ids[: counts["dev"]])
        val.extend(ids[counts["dev"]: counts["dev"] + counts["val"]])
        test.extend(ids[counts["dev"] + counts["val"]:])

    splits = Splits(
        seed=seed,
        corpus_digest=corpus_digest(records),
        corpus_size=len(records),
        development=tuple(sorted(dev)),
        validation=tuple(sorted(val)),
        locked_test=tuple(sorted(test)),
    )
    verify_splits(splits, records)
    return splits


def verify_splits(splits: Splits, records: list[dict[str, Any]]) -> None:
    """Raise on any integrity violation. Called on cut and on every load."""
    dev, val, test = set(splits.development), set(splits.validation), set(splits.locked_test)

    overlaps = [
        ("development/validation", dev & val),
        ("development/locked_test", dev & test),
        ("validation/locked_test", val & test),
    ]
    for label, shared in overlaps:
        if shared:
            raise ValueError(
                f"SPLIT LEAKAGE between {label}: {len(shared)} shared ids, "
                f"e.g. {sorted(shared)[:5]}"
            )

    corpus_ids = {int(r["id"]) for r in records}
    covered = dev | val | test
    if covered != corpus_ids:
        missing, extra = corpus_ids - covered, covered - corpus_ids
        raise ValueError(
            f"splits do not partition the corpus: {len(missing)} missing, {len(extra)} extra"
        )

    if len(dev) + len(val) + len(test) != len(corpus_ids):
        raise ValueError("duplicate ids within a split")

    digest = corpus_digest(records)
    if digest != splits.corpus_digest:
        raise ValueError(
            "BENCHMARK MEMBERSHIP CHANGED\n"
            f"  splits were cut against {splits.corpus_digest}\n"
            f"  corpus now hashes to    {digest}\n"
            "Stop. Do not re-cut splits to make this pass."
        )


def save_splits(splits: Splits, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(splits.as_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def load_splits(path: Path) -> Splits:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Splits(
        seed=d["seed"],
        corpus_digest=d["corpus_digest"],
        corpus_size=d["corpus_size"],
        development=tuple(d["development"]),
        validation=tuple(d["validation"]),
        locked_test=tuple(d["locked_test"]),
    )


def split_summary(splits: Splits, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-split, per-category counts. Used in the baseline artifact."""
    by_id = {int(r["id"]): r for r in records}
    out: dict[str, Any] = {
        "seed": splits.seed,
        "corpus_digest": splits.corpus_digest,
        "corpus_size": splits.corpus_size,
    }
    for name, ids in (
        ("development", splits.development),
        ("validation", splits.validation),
        ("locked_test", splits.locked_test),
    ):
        counts: dict[str, int] = defaultdict(int)
        convs: dict[str, int] = defaultdict(int)
        for qid in ids:
            counts[str(by_id[qid].get("category"))] += 1
            convs[str(by_id[qid].get("conversation"))] += 1
        out[name] = {
            "n": len(ids),
            "fraction": round(len(ids) / splits.corpus_size, 4),
            "by_category": dict(sorted(counts.items())),
            "conversations": len(convs),
        }
    return out

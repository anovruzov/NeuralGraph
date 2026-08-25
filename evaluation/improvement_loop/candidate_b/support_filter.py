"""Per-item evidence-support filtering, decided from question + evidence only.

Candidate B raised item recall but also raised unsupported items, because it
asked the model to police its own grounding. This filters each emitted item
against the retrieved evidence *after* generation and *before* scoring, so
grounding is enforced by a deterministic rule rather than by instruction.

The filter never reads the gold answer or a judge verdict. It sees the question
and the retrieved evidence, which is exactly what a deployed system would have.
A filter that consulted gold would be an oracle and its recall would be
meaningless.

Three rules are implemented separately so they can be compared rather than
blended by taste:

* ``lexical``  — the whole normalised item appears in the normalised evidence.
  Strictest; rejects any paraphrase.
* ``containment`` — every content token of the item appears somewhere in the
  evidence. Tolerates reordering and morphology, rejects invented nouns.
* ``coverage`` — a tunable fraction of the item's content tokens appear.
  ``coverage(1.0)`` is ``containment``; lower thresholds trade precision for
  paraphrase tolerance.

The danger this code carries is dropping *correct* items, which would look like
a precision win while silently costing recall. `filter_items` therefore reports
what it rejected and why, and the accompanying tests assert that a supported
paraphrase survives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

_STOP = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did", "has",
    "have", "had", "he", "she", "it", "they", "we", "i", "his", "her", "their",
    "its", "my", "our", "that", "this", "these", "those", "as", "by", "from",
}

RULES = ("lexical", "containment", "coverage")


def normalise(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def content_tokens(text: str) -> list[str]:
    return [t for t in normalise(text).split() if t not in _STOP and len(t) > 2]


def _stem(token: str) -> str:
    """Crude suffix strip so 'paintings' matches 'painting'.

    Deliberately shallow: aggressive stemming would collapse distinct items,
    which is the one failure mode worse than keeping an unsupported one.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


@dataclass(frozen=True)
class Decision:
    item: str
    kept: bool
    rule: str
    matched: int
    total: int
    rationale: str


def _supported(item: str, evidence: str, rule: str, threshold: float) -> Decision:
    ev_norm = normalise(evidence)
    ev_tokens = {_stem(t) for t in ev_norm.split()}
    item_norm = normalise(item)
    tokens = content_tokens(item)

    if not item_norm:
        return Decision(item, False, rule, 0, 0, "empty after normalisation")

    if rule == "lexical":
        ok = item_norm in ev_norm
        return Decision(item, ok, rule, int(ok), 1,
                        "verbatim in evidence" if ok else "not verbatim in evidence")

    if not tokens:
        # Function-word-only items ("yes", "no") carry no content to check, so
        # fall back to the verbatim test rather than passing them for free.
        ok = item_norm in ev_norm
        return Decision(item, ok, rule, int(ok), 0,
                        "no content tokens; verbatim check")

    matched = sum(1 for t in tokens if _stem(t) in ev_tokens)
    if rule == "containment":
        ok = matched == len(tokens)
        return Decision(item, ok, rule, matched, len(tokens),
                        f"{matched}/{len(tokens)} content tokens in evidence")

    ok = (matched / len(tokens)) >= threshold
    return Decision(item, ok, rule, matched, len(tokens),
                    f"{matched}/{len(tokens)} = {matched/len(tokens):.2f} vs {threshold:.2f}")


def filter_items(items: Iterable[str], evidence: str, rule: str = "containment",
                 threshold: float = 0.6) -> tuple[list[str], list[Decision]]:
    """Return (kept items in original order, every decision made).

    Order is preserved, so retrieval relevance ordering survives the filter.
    """
    if rule not in RULES:
        raise ValueError(f"unknown rule {rule!r}; expected one of {RULES}")
    decisions = [_supported(i, evidence, rule, threshold) for i in items]
    return [d.item for d in decisions if d.kept], decisions


def evaluate_rule(rows: list[dict[str, Any]], rule: str, threshold: float,
                  grade_fn, gold_of, evidence_of) -> dict[str, Any]:
    """Score a rule over cached rows without any model or judge call.

    ``rows`` carry the generated items; ``grade_fn`` is the project grader.
    Gold is used only to *score* the outcome, never to make a filter decision.
    """
    import statistics

    before_p, before_r, before_f = [], [], []
    after_p, after_r, after_f = [], [], []
    unsup_before = unsup_after = 0
    exact_before = exact_after = 0
    dropped_correct = 0
    dropped_total = 0

    for row in rows:
        items = row["items"]
        evidence = evidence_of(row)
        gold = gold_of(row)
        kept, decisions = filter_items(items, evidence, rule, threshold)

        gb = grade_fn(gold, items).__dict__
        ga = grade_fn(gold, kept).__dict__
        before_p.append(gb["precision"]); before_r.append(gb["recall"]); before_f.append(gb["f1"])
        after_p.append(ga["precision"]);  after_r.append(ga["recall"]);  after_f.append(ga["f1"])
        exact_before += int(gb["exact_set_match"]); exact_after += int(ga["exact_set_match"])

        ev_norm = normalise(evidence)
        unsup_before += sum(1 for i in items if normalise(i) and normalise(i) not in ev_norm)
        unsup_after += sum(1 for i in kept if normalise(i) and normalise(i) not in ev_norm)

        # A dropped item that the grader would have credited is a real loss.
        gold_norm = normalise(gold)
        for d in decisions:
            if not d.kept:
                dropped_total += 1
                if normalise(d.item) and normalise(d.item) in gold_norm:
                    dropped_correct += 1

    n = len(rows) or 1
    mean = lambda xs: round(statistics.mean(xs), 4)  # noqa: E731
    return {
        "rule": rule, "threshold": threshold, "n": len(rows),
        "item_precision": {"before": mean(before_p), "after": mean(after_p),
                           "delta": round(mean(after_p) - mean(before_p), 4)},
        "item_recall": {"before": mean(before_r), "after": mean(after_r),
                        "delta": round(mean(after_r) - mean(before_r), 4)},
        "item_f1": {"before": mean(before_f), "after": mean(after_f),
                    "delta": round(mean(after_f) - mean(before_f), 4)},
        "exact_set_match": {"before": exact_before, "after": exact_after,
                            "delta": exact_after - exact_before},
        "unsupported_items": {"before": unsup_before, "after": unsup_after,
                              "delta": unsup_after - unsup_before},
        "items_dropped": dropped_total,
        "correct_items_dropped": dropped_correct,
    }

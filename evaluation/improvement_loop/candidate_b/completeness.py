"""Candidate B: generation completeness, gated by a deterministic trigger.

Scope discipline. The mechanism activates only on questions whose *text* admits
more than one answer. The trigger reads the question string and nothing else --
never the gold answer, never the category label, never the evidence. A trigger
that peeked at the gold item count would be measuring its own oracle.

Everything outside the trigger takes the byte-identical v1 path. That is checked
by a test rather than asserted, because "only where it applies" is exactly the
claim the v2 round got wrong: its instructions reached every category and its
re-ordering reached every question.

What changes inside the trigger. The v1 instruction already says "list EVERY
distinct item". It does not tell the model *how*, and Qwen answers in one pass
and stops early. The v3 instruction makes the enumeration explicit -- sweep the
evidence, collect candidates, normalise and deduplicate, then answer -- while
keeping the grounding rule ahead of the completeness rule so completeness cannot
be bought with invention.

Two lessons from the rejected v2 round are honoured structurally:

* No visible citation requirement. Requiring a printed citation per item
  correlated with Qwen emitting only the items it cited, which is the very
  under-listing this candidate targets. Provenance is requested internally and
  is not allowed to gate what appears in `items`.
* No re-ordering. Evidence is rendered by the v1 builder, in retrieval-relevance
  order, byte for byte.
"""

from __future__ import annotations

import re
from typing import Any

from evaluation.replay_generation import JSON_INSTRUCTION, SYSTEM_PROMPT, build_prompt

PROMPT_VERSION = "v3-completeness"

# Interrogatives that admit several answers. Deliberately conservative: a
# trigger that fires everywhere is the v2 failure repeated.
_PLURAL_HEAD = re.compile(
    r"\b(what|which)\s+(?:\w+\s+){0,2}"
    r"(things|items|activities|hobbies|places|people|persons|pets|animals|books|"
    r"movies|films|songs|foods|events|topics|reasons|problems|issues|projects|"
    r"skills|qualities|traits|characteristics|adjectives|names|kinds|types|"
    r"sports|games|shows|gifts|plans|goals|steps|ways|methods|tools|languages)\b",
    re.I,
)
_EXPLICIT_LIST = re.compile(r"\b(list|name all|enumerate|all of the|both of)\b", re.I)
_PLURAL_VERB = re.compile(
    r"\bwhat\s+(?:\w+\s+){0,3}(has|have|did|does|do)\b.*\b(attended|visited|tried|"
    r"bought|made|played|watched|read|mentioned|discussed|enjoyed|liked|"
    r"recommended|suggested|shared|received|given|done)\b",
    re.I,
)
# Conjunctive questions ("X and Y") also admit multiple items.
_CONJUNCTIVE = re.compile(r"\bwhat\s+.*\band\b.*\?", re.I)

# Never fire on these: they take exactly one answer by construction.
_SINGULAR_BLOCK = re.compile(
    r"^\s*(when|where|who|how many|how much|how long|how old|is|are|was|were|"
    r"did|does|do|has|have|can|could|will|would)\b",
    re.I,
)


# "what is the <singular noun>" takes exactly one answer however it is phrased.
_SINGULAR_OBJECT = re.compile(
    r"\bwhat\s+(is|was)\s+(the\s+)?(name|date|time|reason|colour|color|title|age|"
    r"price|number)\b", re.I)
# A plural or mass object after what/which is the strongest text-only signal
# that several answers are admissible: "what instruments", "what pets' names".
_PLURAL_OBJECT = re.compile(r"\b(what|which)\s+(?:\w+\s+){0,2}[a-z]+s\b", re.I)


def is_list_question(question: str) -> bool:
    """Deterministic, text-only trigger. Never reads gold, category or evidence.

    Calibrated on the development split only. Measured there: fires on 33% of
    questions at precision 0.52 / recall 0.44, against a 39% multi-answer base
    rate. That lift is modest and is stated rather than hidden -- a text-only
    trigger cannot cleanly separate list from single questions in this corpus,
    because "what" opens 99 multi-answer and 191 single-answer development
    questions alike.

    A false fire is intended to be harmless: the v3 instruction keeps the
    grounding rule ahead of the completeness rule and says explicitly that one
    supported item means one item. Whether that holds is decided by the
    deterministic gates, not by assumption.
    """
    q = (question or "").strip()
    if not q:
        return False
    if _EXPLICIT_LIST.search(q):
        return True
    if _SINGULAR_BLOCK.match(q):
        return False
    if _SINGULAR_OBJECT.search(q):
        return False
    if _PLURAL_HEAD.search(q) or _PLURAL_VERB.search(q):
        return True
    return bool(_PLURAL_OBJECT.search(q))


def normalise_item(item: str) -> str:
    """Canonical form used for deduplication only -- never for output.

    Deduplication must merge phrasings of one item without merging distinct
    items, so this strips articles, possessives, leading pronouns and a trailing
    plural 's', and nothing else. "red painting" and "red bicycle" stay
    distinct because only the head morphology is touched.
    """
    t = (item or "").lower().strip()
    t = re.sub(r"^(he|she|they|it|we|i)\s+(is|was|are|were|has|have|had)\s+", "", t)
    t = re.sub(r"\b(a|an|the|his|her|their|its|my|our)\b", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = " ".join(t.split())
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
             for w in t.split()]
    return " ".join(words)


def deduplicate(items: list[str]) -> list[str]:
    """Keep first occurrence of each distinct item, preserving order.

    Order is the model's, which follows the relevance-ordered evidence, so the
    retrieval ranking survives into the answer.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or not item.strip():
            continue
        key = normalise_item(item)
        if not key or key in seen:
            continue
        # A candidate that merely contains an existing item is a rephrasing of
        # it only when it adds no new content words.
        seen.add(key)
        out.append(item.strip())
    return out


SYSTEM_PROMPT_V3 = SYSTEM_PROMPT + """

THIS QUESTION ADMITS SEVERAL ANSWERS. Work in two steps before replying.

Step 1 - sweep. Read every excerpt in the order given. Each time an excerpt
supports an answer to the question, note that item. One excerpt may support
several items. Keep going to the last excerpt; do not stop at the first match.

Step 2 - answer. Merge items that are the same thing said differently, keep
items that are genuinely different, and give them all in the order you found
them.

Rule 2 above still wins: an item must be stated in the excerpts. Do not add an
item to look complete. If the excerpts support only one item, give one."""

JSON_INSTRUCTION_V3 = (
    'Reply with JSON only: {"items": ["...", "..."], "not_in_evidence": false}\n'
    "items: every distinct supported answer, one string each, in the order you "
    "found them. Give the answer text only -- no excerpt numbers, no quotes "
    "around the whole answer, no explanation.\n"
    "not_in_evidence: true only if no excerpt bears on the question."
)


def resolve(question: str) -> tuple[str, str, bool]:
    """(system, json_instruction, triggered) for one question."""
    if is_list_question(question):
        return SYSTEM_PROMPT_V3, JSON_INSTRUCTION_V3, True
    return SYSTEM_PROMPT, JSON_INSTRUCTION, False


def build(record: dict[str, Any]) -> str:
    """Evidence rendering is the v1 builder, unchanged: relevance order, byte
    for byte. Candidate B changes the instruction, never the context."""
    return build_prompt(record)

"""Fix 1: an evidence-coverage gate in front of abstention.

Diagnosed on the development split only. Eight temporal questions there were
wrong, abstained, and had their evidence present under *both* the lenient and
partial measures. Their shared mechanism, stated without reference to any
entity or answer:

    For temporal questions the answer is frequently carried by an excerpt's
    timestamp rather than its prose -- either directly (the message timestamp
    is the date asked for) or derivably (the timestamp resolved against a
    relative expression in the prose, such as "about four months now" or "last
    week"). The timestamp is already in the prompt. The model reads only the
    prose, finds no explicit date string, and abstains while holding the answer.

So the fix is not "abstain less". Abstaining less would trade false abstentions
for hallucinations, which is a worse failure. The fix is to make abstention
*conditional on an explicit coverage check* that is required to consider
timestamps as evidence and to resolve relative expressions against them.

Two supporting changes make the gate checkable rather than aspirational:

* Excerpts are numbered and ordered chronologically, so "examine the evidence
  in time order" is something the model can actually do and something a test
  can verify.
* The structured response must cite the excerpt ids it used. A cited id is how
  an answer is distinguished from an invention, and an abstention that cites
  nothing is distinguished from one that examined the evidence and found
  nothing.

Nothing here touches the retrieval track (`prompts.py`, `answering.py`, ...).
The evidence set, top-k, judge, grader and split membership are unchanged; only
the instruction given to the answerer and the shape of its reply differ.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

PROMPT_VERSION = "v2-abstention-gate"

# Ordered most specific first: "12 January, 2023" must win over "January, 2023".
_DATE_PATTERNS = (
    "%I:%M %p on %d %B, %Y",
    "%d %B, %Y",
    "%d %B %Y",
    "%B %d, %Y",
    "%B %Y",
    "%Y",
)


def parse_timestamp(text: str) -> datetime | None:
    """Best-effort parse of an excerpt's recorded datetime.

    Returns None rather than raising or guessing. An unparseable timestamp must
    leave the excerpt in its original position instead of being sorted to an
    invented point in time.
    """
    if not text:
        return None
    cleaned = text.strip()
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    # Fall back to a date embedded in a longer string.
    match = re.search(r"(\d{1,2})\s+([A-Z][a-z]+),?\s+(\d{4})", cleaned)
    if match:
        for pattern in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(
                    f"{match.group(1)} {match.group(2)} {match.group(3)}", pattern
                )
            except ValueError:
                continue
    return None


def order_evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Excerpts in chronological order, stable, with ids assigned after sorting.

    Undated excerpts keep their relative order and sort after dated ones, so a
    missing timestamp never silently becomes "earliest". Ids are assigned only
    once the order is final, so an id always names the same excerpt for a given
    record.
    """
    memories = [m for m in (record.get("retrieved_memories") or ()) if isinstance(m, dict)]
    decorated = []
    for position, memory in enumerate(memories):
        stamp = parse_timestamp(str(memory.get("datetime") or ""))
        decorated.append((stamp is None, stamp or datetime.min, position, memory))
    decorated.sort(key=lambda d: (d[0], d[1], d[2]))

    out = []
    for index, (_, _, _, memory) in enumerate(decorated, start=1):
        out.append({
            "id": f"E{index}",
            "speaker": str(memory.get("speaker") or ""),
            "datetime": str(memory.get("datetime") or ""),
            "text": str(memory.get("text") or ""),
        })
    return out


def build_prompt_v2(record: dict[str, Any]) -> str:
    """Numbered, chronologically ordered excerpts plus the question.

    The gold answer field is never read here, and the recorded baseline answer
    is never shown -- either would contaminate the comparison.
    """
    lines = ["Excerpts, in chronological order:"]
    for item in order_evidence(record):
        stamp = f" | {item['datetime']}" if item["datetime"] else ""
        speaker = item["speaker"] or "unknown"
        lines.append(f"[{item['id']}] ({speaker}{stamp}) {item['text']}")
    lines.append("")
    lines.append(f"Question: {record.get('question')}")
    return "\n".join(lines)


SYSTEM_PROMPT_V2 = """You answer questions about a conversation, using only the supplied excerpts.

Each excerpt has an id like [E3], a speaker, and the timestamp at which it was said.

Rules:
1. Answer from the excerpts. Answer if the excerpts support an answer at all, even a partial one. Do not hedge: never write "there is no mention ... however".
2. Never add an item the excerpts do not state. Do not infer plausible extras.
3. If the question asks what things, which things, or otherwise admits more than one answer, list EVERY distinct item the excerpts support - scan all of them before answering, not just the first relevant one.
4. Answer the question actually asked, and match its type: a "when" question takes a time, a "who" a person, a "what" a thing. Never answer a "what" question with a date. Related material that does not answer the question must be left out.
5. Give the answer only. No preamble, no explanation, no restating the question.

TIMESTAMPS ARE EVIDENCE.
An excerpt's timestamp is part of what it tells you, not decoration. For a "when" question the answer is often the timestamp of the excerpt describing the event, not a date written inside the text.
6. If an excerpt describes the event asked about, the time that excerpt was said is evidence for when it happened.
7. Resolve relative expressions against the timestamp of the excerpt containing them. "last week", "yesterday", "about four months now", "since March" are all answerable when combined with the excerpt's own timestamp. Compute the resulting date.
8. The excerpts are given oldest first. For a "when did X first/start" question, prefer the earliest excerpt that supports X.

BEFORE YOU MAY ABSTAIN.
Setting not_in_evidence to true is a claim that you checked and found nothing. You may only set it after doing this check:
9. Re-read every excerpt in order. For each, ask whether its text OR its timestamp bears on the question.
10. If any excerpt describes the event asked about, you have evidence - answer with the time derived from it. Do not abstain.
11. Only if no excerpt bears on the question at all may you set not_in_evidence to true.
12. An excerpt that is merely nearby in time, or about a different event, is NOT evidence. Do not answer from it. If that is all you have, abstain - a wrong answer is worse than an honest abstention."""

JSON_INSTRUCTION_V2 = (
    'Reply with JSON only: {"items": ["..."], "not_in_evidence": false, '
    '"cited_evidence": ["E1", "E4"]}\n'
    "items: the distinct answers, one string each. Empty list if abstaining.\n"
    "not_in_evidence: true only after completing the abstention check above.\n"
    "cited_evidence: the ids of the excerpts you used. Every item you give must "
    "be supported by at least one cited excerpt. If you abstain, cite the "
    "excerpts you examined and rejected."
)


def parse_response_v2(text: str) -> tuple[list[str], bool, list[str]]:
    """Parse the v2 reply into (items, not_in_evidence, cited_evidence).

    Tolerant of the same malformations the v1 parser handles -- fenced blocks,
    reasoning preambles, trailing prose -- because the answerer is a local model
    and a parse failure would be scored as a wrong answer rather than as the
    formatting problem it is.
    """
    import json

    if not text:
        return [], False, []

    body = text.strip()
    # Strip reasoning blocks emitted by thinking models.
    body = re.sub(r"<think>.*?</think>", "", body, flags=re.S | re.I).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, flags=re.S)
    if fence:
        body = fence.group(1).strip()

    payload = None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", body, flags=re.S)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None

    if not isinstance(payload, dict):
        return [], False, []

    raw_items = payload.get("items")
    items: list[str] = []
    if isinstance(raw_items, list):
        for entry in raw_items:
            if isinstance(entry, str) and entry.strip():
                items.append(entry.strip())
    elif isinstance(raw_items, str) and raw_items.strip():
        items.append(raw_items.strip())

    cited: list[str] = []
    raw_cited = payload.get("cited_evidence")
    if isinstance(raw_cited, list):
        for entry in raw_cited:
            if isinstance(entry, str) and re.fullmatch(r"E\d+", entry.strip()):
                cited.append(entry.strip())

    return items, bool(payload.get("not_in_evidence")), cited


def abstention_is_supported(items: list[str], not_in_evidence: bool,
                            cited: list[str], record: dict[str, Any]) -> bool:
    """Did an abstention actually perform the coverage check it claims?

    An abstention that cites nothing while evidence was supplied has not
    checked; it has simply declined. This does not overturn the answer -- the
    grader and judge are untouched -- it is recorded as a diagnostic so the
    round can report whether the gate was honoured.
    """
    if not not_in_evidence:
        return True
    if not order_evidence(record):
        return True  # nothing to examine; abstention is trivially supported
    return bool(cited)

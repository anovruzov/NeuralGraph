"""Evidence-validated LLM querying for speaker profiles."""

from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

from .benchmarking import retrieval_tokens
from .openai_client import openai_text

PROFILE_MODEL = os.environ.get("NEURALGRAPH_PROFILE_MODEL", "gpt-5.6-terra")


def _tokens(text: str) -> set[str]:
    return retrieval_tokens(text)


def rank_profile_messages(question: str, messages: list[str], limit: int = 40) -> list[str]:
    """Select relevant profile messages without using an expected answer."""

    query_tokens = _tokens(question)
    ranked: list[tuple[float, int, str]] = []
    for index, message in enumerate(messages):
        message_tokens = _tokens(message)
        overlap = len(query_tokens & message_tokens)
        # Preserve recency only as a tie-breaker; relevance remains dominant.
        score = overlap * 2.0 + (index / max(1, len(messages))) * 0.05
        if overlap:
            ranked.append((score, index, message))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [message for _score, _index, message in ranked[:limit]]

    # Sparse wording can hide a cross-message link. Add a small recent tail so
    # the model can connect pronouns and state changes without receiving every
    # message in a long conversation.
    for message in messages[-8:]:
        if message not in selected and len(selected) < limit:
            selected.append(message)
    return selected


def _valid_evidence_quotes(evidence: Any, searchable_text: str) -> list[str]:
    if not isinstance(evidence, list):
        return []
    haystack = " ".join(searchable_text.lower().split())
    valid: list[str] = []
    for item in evidence:
        quote = " ".join(str(item).strip().split())
        if len(quote) >= 8 and quote.lower() in haystack:
            valid.append(quote)
    return valid


async def query_profile_with_llm(
    session: aiohttp.ClientSession,
    speaker_name: str,
    profile_dict: dict[str, Any],
    question: str,
) -> dict[str, Any]:
    """Answer from one speaker's profile and require verifiable quotations."""

    all_messages = [str(message) for message in profile_dict.get("all_messages", [])]
    selected_messages = rank_profile_messages(question, all_messages)
    extracted_facts = [str(fact) for fact in profile_dict.get("extracted_facts", [])]

    evidence_lines = [f"MESSAGE {index}: {message}" for index, message in enumerate(selected_messages, 1)]
    fact_lines = [f"FACT {index}: {fact}" for index, fact in enumerate(extracted_facts[:80], 1)]
    profile_summary = "\n".join(evidence_lines + fact_lines)

    query_prompt = f"""Answer a question about exactly one speaker.

TARGET SPEAKER: {speaker_name}

PROFILE EVIDENCE:
{profile_summary}

QUESTION: {question}

RULES:
1. Use only facts supported by TARGET SPEAKER's profile evidence.
2. Do not import facts from any other person named in the question.
3. A message that asks a question is not evidence of its answer.
4. Return the smallest complete answer. Use comma-separated items for lists.
5. Include one or more exact evidence quotes copied from PROFILE EVIDENCE.
6. If the answer is absent or attribution is uncertain, answer NOT_FOUND with no quotes.

Return ONLY valid JSON:
{{"answer":"answer or NOT_FOUND","evidence":["exact quote"]}}"""

    try:
        response_text = await openai_text(
            session,
            query_prompt,
            instructions="Answer only from the supplied profile evidence and return valid JSON.",
            model=PROFILE_MODEL,
            max_output_tokens=300,
            timeout_seconds=45,
            reasoning_effort="low",
        )
        if response_text.startswith("```"):
            response_text = response_text.split("```", 2)[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        result = json.loads(response_text.strip())
    except Exception as exc:
        return {
            "found": False,
            "answer": None,
            "confidence": 0.0,
            "evidence": [],
            "error": str(exc),
        }

    answer_raw = result.get("answer", "")
    answer = ", ".join(str(item) for item in answer_raw) if isinstance(answer_raw, list) else str(answer_raw).strip()
    searchable = "\n".join(selected_messages + extracted_facts[:80])
    evidence = _valid_evidence_quotes(result.get("evidence"), searchable)

    if not answer or answer.upper() == "NOT_FOUND" or not evidence:
        return {"found": False, "answer": None, "confidence": 0.0, "evidence": evidence}

    confidence = min(0.95, 0.65 + 0.15 * len(evidence))
    return {
        "found": True,
        "answer": answer,
        "confidence": confidence,
        "evidence": evidence,
    }

"""Answering and embedding helpers for benchmark + service usage."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

import aiohttp

from .openai_client import DEFAULT_OPENAI_MODEL, openai_text
from .prompts import LIST_QUESTION_ANSWER_PROMPT, AGGREGATION_ANSWER_PROMPT


@dataclass
class AnsweringConfig:
    """Configuration for LLM answering + embeddings."""
    answer_model: str = DEFAULT_OPENAI_MODEL
    answer_timeout_seconds: float = 60.0
    max_output_tokens: int = 256
    embedding_dimensions: int = 1024


TEMPORAL_ANSWER_PROMPT = """Answer using ONLY the memories below.

TEMPORAL REASONING RULES:
1. Each memory has [MESSAGE_DATETIME=...] showing WHEN it was said.
2. If present, [RESOLVED_DATE=...] provides a canonical date for the event.
3. If present, [RESOLVED_RELATIVE=...] provides precomputed resolutions for relative phrases.
4. If the memory says "yesterday" or "last week", compute the actual date using MESSAGE_DATETIME.
   - MESSAGE_DATETIME=2023-05-08 + "yesterday" = May 7, 2023
   - MESSAGE_DATETIME=2023-05-08 + "last week" = early May 2023
5. If the memory states an explicit date (e.g., "on May 5th"), use that date.
6. Do NOT just return MESSAGE_DATETIME - that's when the message was sent, not when the event happened.
7. If the date cannot be computed from the memories, say "Not mentioned in the memories".
8. Output ONLY the date/time period (concise).
9. Match the granularity: if memory says "in May" -> answer "May 2023", not a specific day.
10. First match the named person and exact event. Do not select a date from a
    different event merely because it is nearby or more recent.
11. Prefer an event-specific RESOLVED_RELATIVE value over MESSAGE_DATETIME.

MEMORIES:
{context}

QUESTION: {question}

Answer:"""


OPEN_DOMAIN_INFER_PROMPT = """Infer the answer from these memories.

RULES:
1. Output ONLY the answer - no explanations, no "Based on...", no bullets.
2. For Yes/No: just "Yes" or "No"
3. For lists: comma-separated format
4. Use conversation evidence plus ordinary world knowledge only when the
   question explicitly asks what is likely, possible, or implied.
5. Never transfer a fact from one speaker to another.
6. If there is no evidence about the named person or event, output exactly:
   NOT_FOUND

MEMORIES:
{context}

QUESTION: {question}

Answer:"""


OPEN_DOMAIN_WORLD_PROMPT = """Answer this as a general knowledge question.

RULES:
1. Ignore the conversation memories entirely.
2. Be concise: 1-2 sentences.
3. If uncertain, say "I'm not sure."
4. If the question is A-or-B or Yes/No, answer in that format.

QUESTION: {question}

Answer:"""


STRICT_ANSWER_PROMPT = """Extract the answer from the memories below.

RULES:
1. Output ONLY the answer - no explanations, no "Based on...", no bullets.
2. If multiple items, use comma-separated format: item1, item2, item3
3. Treat [SPEAKER=name] as binding attribution. A fact spoken by or about one
   person is not automatically true of another person.
4. [IMAGE] captions are valid memory evidence.
5. A message that merely asks the same question is not evidence of an answer.
6. For Yes/No questions, answer Yes or No only when the memories support it.
7. If the named person's answer is not supported, output exactly: NOT_FOUND

EXAMPLES:
Q: What is John's job? → Nurse
Q: What are Mary's pets' names? → Luna, Bailey
Q: What has Tom painted? → Sunset, horse
Q: Where did Sarah move from? → Sweden

MEMORIES:
{context}

QUESTION: {question}

Answer:"""


ADVERSARIAL_ANSWER_PROMPT = """Verify whether the memories support the exact claim in the question.

RULES:
1. Speaker identity is strict: do not copy another person's fact to the named person.
2. Reject altered premises, swapped names, unsupported negations, and plausible guesses.
3. Output ONLY the supported answer.
4. If the exact fact is absent, output exactly: NOT_FOUND

MEMORIES:
{context}

QUESTION: {question}

Answer:"""


async def get_embedding(
    session: aiohttp.ClientSession,
    text: str,
    config: AnsweringConfig | None = None,
) -> list[float]:
    """Create a deterministic local feature-hash embedding.

    Anthropic does not provide an embeddings endpoint. A signed word/bigram
    hash keeps the graph path self-contained and deterministic, while the
    independent lexical ranker supplies high-recall exact matching.
    """

    cfg = config or AnsweringConfig()
    dimensions = max(64, cfg.embedding_dimensions)
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    features = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]
    vector = [0.0] * dimensions
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        index = raw % dimensions
        vector[index] += 1.0 if raw & 1 else -1.0

    magnitude = math.sqrt(sum(value * value for value in vector))
    if not magnitude:
        return vector
    return [value / magnitude for value in vector]


async def generate_answer(
    session: aiohttp.ClientSession,
    question: str,
    context: str,
    mode: str = "STRICT",
    config: AnsweringConfig | None = None,
) -> str:
    cfg = config or AnsweringConfig()

    if mode == "TEMPORAL":
        prompt = TEMPORAL_ANSWER_PROMPT.format(context=context, question=question)
    elif mode == "LIST":
        prompt = LIST_QUESTION_ANSWER_PROMPT.format(context=context, question=question)
    elif mode == "AGGREGATION":
        prompt = AGGREGATION_ANSWER_PROMPT.format(context=context, question=question)
    elif mode in {"INFERENTIAL", "OPEN_DOMAIN_INFER"}:
        prompt = OPEN_DOMAIN_INFER_PROMPT.format(context=context, question=question)
    elif mode == "OPEN_DOMAIN_WORLD":
        prompt = OPEN_DOMAIN_WORLD_PROMPT.format(question=question)
    elif mode == "ADVERSARIAL":
        prompt = ADVERSARIAL_ANSWER_PROMPT.format(context=context, question=question)
    else:
        prompt = STRICT_ANSWER_PROMPT.format(context=context, question=question)

    try:
        return await openai_text(
            session,
            prompt,
            instructions="Answer the memory question exactly as instructed. Return only the requested answer.",
            model=cfg.answer_model,
            max_output_tokens=cfg.max_output_tokens,
            timeout_seconds=cfg.answer_timeout_seconds,
            reasoning_effort="medium",
        )
    except Exception as exc:
        return f"Error: {exc}"

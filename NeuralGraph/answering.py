"""Answering and embedding helpers for benchmark + service usage."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from . import llm_backend
from .prompts import LIST_QUESTION_ANSWER_PROMPT, AGGREGATION_ANSWER_PROMPT


@dataclass
class AnsweringConfig:
    """Configuration for LLM answering + embeddings."""
    ollama_base_url: str = llm_backend.LLM_BASE_URL  # any OpenAI-compatible or Ollama URL
    answer_model: str = llm_backend.LLM_MODEL
    embedding_model: str = llm_backend.EMBED_MODEL
    answer_timeout_seconds: float = 60.0
    embedding_timeout_seconds: float = 30.0
    num_predict: int = 120  # 60 truncated LIST/AGGREGATION answers mid-list


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

MEMORIES:
{context}

QUESTION: {question}

Answer:"""


OPEN_DOMAIN_INFER_PROMPT = """Infer the answer from these memories.

RULES:
1. Output ONLY the answer - no explanations, no "Based on...", no bullets.
2. For Yes/No: just "Yes" or "No"
3. For lists: comma-separated format
4. Use ONLY evidence from memories, do not guess.
5. If unclear, give your best inference in 1-5 words.

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


# H5: open-domain questions need the memories PLUS world knowledge. Used for
# OPEN_DOMAIN_WORLD when OPEN_DOMAIN_KEEP_CONTEXT=1 (mode OPEN_DOMAIN_WORLD_MEM)
# and for OPEN_DOMAIN_INFER when OPEN_DOMAIN_INFER_WORLD=1 (mode OPEN_DOMAIN_INFER_WORLD).
OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT = """Answer the question about the people in these conversation memories.

RULES:
1. The memories are your PRIMARY evidence: base the answer on what they say about the person.
2. You MAY combine them with general world knowledge (e.g. what a hobby, book, place, or brand implies).
3. Give a short, direct answer: a few words, or Yes/No, optionally followed by one brief reason.
4. Never say you are unsure, never say the answer is not in the memories: always commit to the most likely answer.
5. No preamble, no bullets, no "Based on the memories".

MEMORIES:
{context}

QUESTION: {question}

Answer:"""


STRICT_ANSWER_PROMPT = """Extract the answer from the memories below.

RULES:
1. Output ONLY the answer - no explanations, no "Based on...", no bullets.
2. If multiple items, use comma-separated format: item1, item2, item3
3. Match the person asked about (check speaker metadata).
4. If not found, output exactly: Not found

EXAMPLES:
Q: What is John's job? → Nurse
Q: What are Mary's pets' names? → Luna, Bailey
Q: What has Tom painted? → Sunset, horse
Q: Where did Sarah move from? → Sweden

MEMORIES:
{context}

QUESTION: {question}

Answer:"""


async def get_embedding(
    session: aiohttp.ClientSession,
    text: str,
    config: AnsweringConfig | None = None,
) -> list[float]:
    cfg = config or AnsweringConfig()
    try:
        return await llm_backend.llm_embed(
            session, text,
            model=cfg.embedding_model, base_url=cfg.ollama_base_url,
            timeout_seconds=cfg.embedding_timeout_seconds,
        )
    except Exception:
        return []


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
    elif mode == "OPEN_DOMAIN_INFER":
        prompt = OPEN_DOMAIN_INFER_PROMPT.format(context=context, question=question)
    elif mode == "OPEN_DOMAIN_WORLD":
        prompt = OPEN_DOMAIN_WORLD_PROMPT.format(question=question)
    elif mode in ("OPEN_DOMAIN_WORLD_MEM", "OPEN_DOMAIN_INFER_WORLD"):
        prompt = OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT.format(context=context, question=question)
    else:
        prompt = STRICT_ANSWER_PROMPT.format(context=context, question=question)

    try:
        return await llm_backend.llm_generate(
            session, prompt,
            model=cfg.answer_model, base_url=cfg.ollama_base_url,
            temperature=0, max_tokens=cfg.num_predict,
            timeout_seconds=cfg.answer_timeout_seconds,
        )
    except Exception as e:
        return f"Error: {e}"

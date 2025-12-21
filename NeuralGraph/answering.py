"""Answering and embedding helpers for benchmark + service usage."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from .prompts import LIST_QUESTION_ANSWER_PROMPT, AGGREGATION_ANSWER_PROMPT


@dataclass
class AnsweringConfig:
    """Configuration for LLM answering + embeddings."""
    ollama_base_url: str = "http://localhost:11434"
    answer_model: str = "qwen2.5:7b-instruct"
    embedding_model: str = "nomic-embed-text"
    answer_timeout_seconds: float = 60.0
    embedding_timeout_seconds: float = 30.0
    num_predict: int = 60  # Keep answers concise - no verbose explanations


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
        async with session.post(
            f"{cfg.ollama_base_url}/api/embeddings",
            json={"model": cfg.embedding_model, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=cfg.embedding_timeout_seconds),
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
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
    else:
        prompt = STRICT_ANSWER_PROMPT.format(context=context, question=question)

    try:
        async with session.post(
            f"{cfg.ollama_base_url}/api/generate",
            json={
                "model": cfg.answer_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": cfg.num_predict},
            },
            timeout=aiohttp.ClientTimeout(total=cfg.answer_timeout_seconds),
        ) as response:
            result = await response.json()
            return result.get("response", "").strip()
    except Exception as e:
        return f"Error: {e}"

"""Answering and embedding helpers for benchmark + service usage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass

import aiohttp

from .prompts import LIST_QUESTION_ANSWER_PROMPT, AGGREGATION_ANSWER_PROMPT


# ---------------------------------------------------------------------------
# Embedding cache
#
# Hosted embedding endpoints are often the scarcest resource in a benchmark run
# (Gemini's free tier allows 1000 embeddings per day, while one LOCOMO pass
# needs ~7400). Re-embedding an unchanged corpus on every run wastes that budget
# and makes iteration impossible, so embeddings are cached on disk by
# (model, text) and re-used across runs.
#
# Set NG_EMBED_CACHE to a file path to enable. Unset means no caching.
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache_conn = None
EMBED_CACHE_STATS = {"hit": 0, "miss": 0, "store": 0}


def _cache() -> sqlite3.Connection | None:
    global _cache_conn
    path = os.environ.get("NG_EMBED_CACHE")
    if not path:
        return None
    if _cache_conn is None:
        _cache_conn = sqlite3.connect(path, check_same_thread=False)
        _cache_conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vec TEXT)"
        )
        _cache_conn.commit()
    return _cache_conn


def _cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()


def cache_get(model: str, text: str) -> list[float] | None:
    conn = _cache()
    if conn is None:
        return None
    with _cache_lock:
        row = conn.execute(
            "SELECT vec FROM embeddings WHERE key = ?", (_cache_key(model, text),)
        ).fetchone()
    if row is None:
        EMBED_CACHE_STATS["miss"] += 1
        return None
    EMBED_CACHE_STATS["hit"] += 1
    return json.loads(row[0])


def cache_put(model: str, text: str, vec: list[float]) -> None:
    conn = _cache()
    if conn is None or not vec:
        return
    with _cache_lock:
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (key, vec) VALUES (?, ?)",
            (_cache_key(model, text), json.dumps(vec)),
        )
        conn.commit()
    EMBED_CACHE_STATS["store"] += 1


def unwrap_api_error(body):
    """Return the error dict from an API response, or None if it is not an error.

    Some providers (Gemini among them) return errors as a single-element JSON
    *list* rather than an object. Parsing those with body.get(...) raises
    "'list' object has no attribute 'get'", which callers then record as if it
    were a model answer - a rate-limit response silently becomes a wrong answer
    and the measured accuracy is nonsense. Normalize both shapes here.
    """
    if isinstance(body, list):
        body = body[0] if body else {}
    if isinstance(body, dict):
        return body.get("error")
    return None


def _retry_delay_seconds(error: dict, default: float) -> float:
    """Seconds to wait before retrying, honoring a server-supplied RetryInfo."""
    for detail in (error.get("details") or []):
        delay = detail.get("retryDelay")
        if isinstance(delay, str):
            m = re.match(r"([\d.]+)s", delay)
            if m:
                return min(float(m.group(1)) + 1.0, 90.0)
    m = re.search(r"retry in ([\d.]+)s", str(error.get("message", "")), re.I)
    if m:
        return min(float(m.group(1)) + 1.0, 90.0)
    return default


async def post_json_with_retry(session, url, *, headers, payload, timeout, max_attempts=5):
    """POST JSON, retrying on rate limits and transient server errors.

    Returns (body, error). Exactly one is non-None.
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            async with session.post(url, headers=headers, json=payload, timeout=timeout) as response:
                body = await response.json()
                error = unwrap_api_error(body)
                if error is None and response.status < 400:
                    return body, None
                last_error = error or {"code": response.status, "message": str(body)[:200]}
                if response.status not in (429, 500, 502, 503, 504):
                    return None, last_error
                wait = _retry_delay_seconds(last_error, default=2.0 * (2 ** attempt))
        except asyncio.TimeoutError:
            last_error = {"code": "timeout", "message": "request timed out"}
            wait = 2.0 * (2 ** attempt)
        except Exception as exc:  # noqa: BLE001 - surfaced to caller below
            return None, {"code": "exception", "message": str(exc)}
        if attempt < max_attempts - 1:
            await asyncio.sleep(wait)
    return None, last_error


@dataclass
class AnsweringConfig:
    """Configuration for LLM answering + embeddings.

    Two providers are supported:

    - "ollama" (default): local models over the Ollama HTTP API. Swapping the
      answer model is just a model string, e.g. answer_model="llama3.1:8b".
    - "openai_compatible": any endpoint speaking the OpenAI chat/embeddings
      API. This covers hosted Llama (Groq, Together, Fireworks) as well as
      self-hosted vLLM and llama.cpp servers.

    The API key is always read from the environment at call time, never stored
    on the config and never committed.
    """
    provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    answer_model: str = "qwen2.5:7b-instruct"
    embedding_model: str = "nomic-embed-text"
    answer_timeout_seconds: float = 60.0
    embedding_timeout_seconds: float = 30.0
    num_predict: int = 60  # Keep answers concise - no verbose explanations

    # Used only when provider == "openai_compatible".
    api_base_url: str = "https://api.groq.com/openai/v1"
    api_key_env: str = "LLAMA_API_KEY"

    # Reasoning models (Gemini 3.x, o-series) spend tokens on hidden thinking
    # before emitting an answer, and that spend counts against max_tokens. With
    # a short cap like num_predict=60 the thinking consumes the entire budget
    # and the response comes back empty. Set this to "none" for extraction-style
    # benchmarks; leave None to omit the field for providers that reject it.
    reasoning_effort: str | None = None

    def api_key(self) -> str:
        """Resolve the API key from the environment. Empty string if unset."""
        return os.environ.get(self.api_key_env, "")


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

    cached = cache_get(cfg.embedding_model, text)
    if cached is not None:
        return cached

    timeout = aiohttp.ClientTimeout(total=cfg.embedding_timeout_seconds)
    try:
        if cfg.provider == "openai_compatible":
            result, error = await post_json_with_retry(
                session,
                f"{cfg.api_base_url}/embeddings",
                headers={"Authorization": f"Bearer {cfg.api_key()}"},
                payload={"model": cfg.embedding_model, "input": text},
                timeout=timeout,
            )
            if error is not None:
                return []
            data = result.get("data") or []
            vec = data[0].get("embedding", []) if data else []
            cache_put(cfg.embedding_model, text, vec)
            return vec

        async with session.post(
            f"{cfg.ollama_base_url}/api/embeddings",
            json={"model": cfg.embedding_model, "prompt": text},
            timeout=timeout,
        ) as response:
            result = await response.json()
            vec = result.get("embedding", [])
            cache_put(cfg.embedding_model, text, vec)
            return vec
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

    timeout = aiohttp.ClientTimeout(total=cfg.answer_timeout_seconds)
    try:
        if cfg.provider == "openai_compatible":
            payload = {
                "model": cfg.answer_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": cfg.num_predict,
            }
            if cfg.reasoning_effort is not None:
                payload["reasoning_effort"] = cfg.reasoning_effort
            result, error = await post_json_with_retry(
                session,
                f"{cfg.api_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key()}"},
                payload=payload,
                timeout=timeout,
            )
            if error is not None:
                return f"Error: {error.get('code')}: {str(error.get('message'))[:120]}"
            choices = result.get("choices") or []
            if not choices:
                return "Error: no choices returned"
            return (choices[0].get("message", {}).get("content") or "").strip()

        async with session.post(
            f"{cfg.ollama_base_url}/api/generate",
            json={
                "model": cfg.answer_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": cfg.num_predict},
            },
            timeout=timeout,
        ) as response:
            result = await response.json()
            return result.get("response", "").strip()
    except Exception as e:
        return f"Error: {e}"

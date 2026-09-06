"""Single LLM/embedding backend for the benchmark path.

Supports two servers behind one interface:
- LM Studio / any OpenAI-compatible server (default): POST /v1/chat/completions, /v1/embeddings
- Ollama: POST /api/generate, /api/embeddings

Backend is chosen from the base URL (port 11434 => Ollama) unless LLM_BACKEND is set.
All settings can be overridden with env vars:
    LLM_BASE_URL   (default http://127.0.0.1:1234)
    LLM_MODEL      (default google/gemma-4-e4b)
    EMBED_MODEL    (default text-embedding-nomic-embed-text-v1.5)
    LLM_BACKEND    ("openai" | "ollama", default inferred)
    LLM_REASONING_EFFORT (default "none"; disables thinking on Gemma-4/Qwen-3 in LM Studio)
"""
from __future__ import annotations

import os
import re

import aiohttp

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:1234").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemma-4-e4b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "none")

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def backend_for(base_url: str) -> str:
    forced = os.environ.get("LLM_BACKEND")
    if forced:
        return forced
    return "ollama" if ":11434" in base_url else "openai"


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


async def llm_generate(
    session: aiohttp.ClientSession,
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 256,
    timeout_seconds: float = 60.0,
) -> str:
    """Return the model's text for a single-turn prompt. Raises on HTTP/network error."""
    base_url = (base_url or LLM_BASE_URL).rstrip("/")
    model = model or LLM_MODEL
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    if backend_for(base_url) == "ollama":
        async with session.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return _strip_thinking(data.get("response", ""))

    async with session.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            # Gemma/Qwen "thinking" models burn the token budget on reasoning; turn it off.
            "reasoning_effort": REASONING_EFFORT,
        },
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
        msg = data.get("choices", [{}])[0].get("message", {})
        return _strip_thinking(msg.get("content") or "")


async def llm_embed(
    session: aiohttp.ClientSession,
    text: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 30.0,
) -> list[float]:
    """Return an embedding vector. Raises on HTTP/network error."""
    base_url = (base_url or LLM_BASE_URL).rstrip("/")
    model = model or EMBED_MODEL
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    if backend_for(base_url) == "ollama":
        async with session.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("embedding", [])

    async with session.post(
        f"{base_url}/v1/embeddings",
        json={"model": model, "input": text},
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return data.get("data", [{}])[0].get("embedding", [])

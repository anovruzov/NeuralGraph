"""Small async client for OpenAI's Responses API."""

from __future__ import annotations

import os
from typing import Any

import aiohttp


OPENAI_API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1").rstrip("/")
DEFAULT_OPENAI_MODEL = os.environ.get("NEURALGRAPH_OPENAI_MODEL", "gpt-5.6-sol")


class OpenAIAPIError(RuntimeError):
    """Raised when the Responses API cannot produce usable text."""


def openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def extract_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content", []):
            if isinstance(block, dict) and block.get("type") == "output_text":
                parts.append(str(block.get("text", "")))
    return "".join(parts).strip()


async def openai_text(
    session: aiohttp.ClientSession,
    prompt: str,
    *,
    instructions: str = "",
    model: str | None = None,
    max_output_tokens: int = 256,
    timeout_seconds: float = 90.0,
    reasoning_effort: str = "low",
) -> str:
    api_key = openai_api_key()
    if not api_key:
        raise OpenAIAPIError("OPENAI_API_KEY is not configured")

    request: dict[str, Any] = {
        "model": model or DEFAULT_OPENAI_MODEL,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort},
        "store": False,
    }
    if instructions:
        request["instructions"] = instructions

    async with session.post(
        f"{OPENAI_API_URL}/responses",
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        json=request,
        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
    ) as response:
        payload = await response.json(content_type=None)
        if response.status != 200:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = error.get("message", f"HTTP {response.status}")
            raise OpenAIAPIError(f"OpenAI request failed: {message}")

    text = extract_response_text(payload)
    if not text:
        raise OpenAIAPIError("OpenAI returned no output text")
    return text

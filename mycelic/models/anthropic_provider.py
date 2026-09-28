"""Anthropic Messages API provider (docs/mycelic/DECISIONS.md D7).

Raw HTTP on the shared ``aiohttp`` stack rather than the SDK, so the server has one HTTP client and one retry
policy for every provider. Request shape follows the ``claude-api`` skill reference: ``POST {base_url}/v1/messages``
with ``x-api-key`` / ``anthropic-version`` headers, top-level ``system`` plus ``messages``, ``max_tokens`` and
(where the model still accepts it) ``temperature``; ``usage.input_tokens`` / ``usage.output_tokens`` are mapped
onto :class:`Completion`.

Two model-generation details matter here and are handled per model id:

* Sampling parameters (``temperature``) return HTTP 400 on Claude Opus 4.7+, Opus 5 / 5.5, Sonnet 5 and the
  Fable/Mythos line, so they are sent only to models that accept them (Haiku 4.5, Sonnet/Opus 4.6 and earlier).
  ``send_temperature`` overrides the auto-detection.
* Opus 5 / 5.5, Sonnet 5 and Fable think by default. Thinking blocks are skipped when reading the reply (only
  ``text`` blocks count) and ``thinking_headroom_tokens`` is added to ``max_tokens`` so adaptive thinking cannot
  starve the JSON answer. ``effort`` (``low``..``max``) is passed as ``output_config.effort`` when set.

JSON output is requested through the system prompt (no beta features); the router parses and repairs the text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import aiohttp

from .base import Completion, ModelError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"

# Defaults per tier when configuration says "anthropic:" without a model (ids from the claude-api skill reference).
DEFAULT_MODELS = {"light": "claude-haiku-4-5", "standard": "claude-sonnet-5", "heavy": "claude-opus-5-5"}

JSON_INSTRUCTION = ("Respond with a single valid JSON object and nothing else: no prose before or after it, no code fences, "
                    "no comments.")

# Models that reject temperature/top_p/top_k (400) and models that run adaptive thinking when ``thinking`` is omitted.
_NO_SAMPLING_RE = re.compile(r"claude-(?:fable|mythos|opus-5|opus-4-(?:7|8)|sonnet-5)(?:$|[-@:])")
_THINKS_BY_DEFAULT_RE = re.compile(r"claude-(?:fable|mythos|opus-5|sonnet-5)(?:$|[-@:])")
_RETRYABLE_STATUS = frozenset({408, 409, 429})


def accepts_sampling(model: str) -> bool:
    return not _NO_SAMPLING_RE.search((model or "").lower())


def thinks_by_default(model: str) -> bool:
    return bool(_THINKS_BY_DEFAULT_RE.search((model or "").lower()))


def split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Top-level ``system`` text (system messages joined) and the user/assistant turns."""
    system_parts, turns = [], []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "") or ""
        if role == "system":
            if content:
                system_parts.append(content)
        else:
            turns.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    return "\n\n".join(system_parts), turns


def _error_message(status: int, text: str) -> str:
    try:
        data = json.loads(text)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return f"HTTP {status} {err.get('type', 'error')}: {err['message']}"
    except (ValueError, AttributeError):
        pass
    return f"HTTP {status}: {text[:300]}"


class AnthropicProvider:
    """``ModelProvider`` for the Anthropic Messages API.

    Args:
        api_key: sent as ``x-api-key``.
        base_url: API origin (``ANTHROPIC_BASE_URL``); ``/v1/messages`` is appended.
        max_parallel: bound on concurrent requests.
        retries: additional attempts after network errors, 408/409/429 and 5xx (incl. 529 overloaded).
        backoff: base seconds of the exponential backoff (``Retry-After`` is honoured when present).
        timeout: default per-request total timeout in seconds.
        session: an ``aiohttp.ClientSession`` to share; when omitted one is created lazily and closed by ``close()``.
        effort: optional ``output_config.effort`` (``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max``).
        thinking: optional explicit ``thinking`` parameter (e.g. ``{"type": "adaptive"}``); omitted by default.
        send_temperature: force sending (True) or omitting (False) ``temperature``; ``None`` decides per model.
        thinking_headroom_tokens: added to ``max_tokens`` for models that think by default.
    """

    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, *, max_parallel: int = 4, retries: int = 2,
                 backoff: float = 1.0, timeout: float = 120.0, session: aiohttp.ClientSession | None = None,
                 api_version: str = API_VERSION, effort: str | None = None, thinking: dict[str, Any] | None = None,
                 send_temperature: bool | None = None, thinking_headroom_tokens: int = 8000) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.max_parallel = max(1, int(max_parallel))
        self.retries = max(0, int(retries))
        self.backoff = max(0.0, float(backoff))
        self.timeout = float(timeout)
        self.api_version = api_version
        self.effort = effort or None
        self.thinking = dict(thinking) if thinking else None
        self.send_temperature = send_temperature
        self.thinking_headroom_tokens = max(0, int(thinking_headroom_tokens))
        self._session = session
        self._owns_session = session is None
        self._session_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(self.max_parallel)
        if not self.api_key:
            logger.warning("anthropic provider configured without an API key; calls will fail with 401")

    # ------------------------------------------------------------------ request
    def build_request(self, messages: list[dict[str, str]], *, model: str, max_tokens: int, temperature: float,
                      json_mode: bool) -> dict[str, Any]:
        system, turns = split_system(messages)
        if json_mode:
            system = (system + "\n\n" if system else "") + JSON_INSTRUCTION
        thinking = self.thinking
        thinking_on = (thinking is not None and thinking.get("type") != "disabled") or (thinking is None and thinks_by_default(model))
        body: dict[str, Any] = {"model": model, "max_tokens": int(max_tokens) + (self.thinking_headroom_tokens if thinking_on else 0),
                                "messages": turns}
        if system:
            body["system"] = system
        send_temp = accepts_sampling(model) if self.send_temperature is None else self.send_temperature
        if send_temp:
            body["temperature"] = float(temperature)
        if thinking is not None:
            body["thinking"] = thinking
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        return body

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
                self._owns_session = True
            return self._session

    async def _post(self, body: dict[str, Any], timeout: float) -> tuple[int, str, Any]:
        session = await self._get_session()
        headers = {"x-api-key": self.api_key, "anthropic-version": self.api_version, "content-type": "application/json"}
        async with session.post(f"{self.base_url}/v1/messages", json=body, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            return resp.status, await resp.text(), resp.headers

    async def complete(self, messages: list[dict[str, str]], *, model: str, max_tokens: int = 1024, temperature: float = 0.0,
                       json_mode: bool = True, timeout: float = 120.0) -> Completion:
        body = self.build_request(messages, model=model, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode)
        t0 = time.perf_counter()
        last_error = "no attempt made"
        for attempt in range(self.retries + 1):
            retry_after: float | None = None
            try:
                async with self._sem:
                    status, text, headers = await self._post(body, timeout or self.timeout)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_error = f"network error: {exc.__class__.__name__}: {exc}"
            else:
                if status < 400:
                    return self._parse(text, model, int((time.perf_counter() - t0) * 1000))
                last_error = _error_message(status, text)
                if status not in _RETRYABLE_STATUS and status < 500:
                    raise ModelError(f"anthropic {model}: {last_error}")
                ra = headers.get("retry-after") if headers is not None else None
                if ra and str(ra).strip().isdigit():
                    retry_after = min(60.0, float(ra))
            if attempt < self.retries:
                delay = max(self.backoff * (2 ** attempt), retry_after or 0.0)
                logger.warning("anthropic %s failed (attempt %d/%d): %s; retrying in %.1fs", model, attempt + 1, self.retries + 1,
                               last_error, delay)
                if delay:
                    await asyncio.sleep(delay)
        raise ModelError(f"anthropic {model} failed after {self.retries + 1} attempts: {last_error}")

    # ------------------------------------------------------------------ response
    def _parse(self, text: str, model: str, latency_ms: int) -> Completion:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ModelError(f"anthropic {model}: response is not JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise ModelError(f"anthropic {model}: unexpected response shape")
        stop = data.get("stop_reason")
        if stop == "refusal":
            details = data.get("stop_details") or {}
            raise ModelError(f"anthropic {model}: request refused ({details.get('category') or 'unspecified'})")
        parts = [b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) + \
            int(usage.get("cache_creation_input_tokens") or 0)
        if stop == "max_tokens":
            logger.warning("anthropic %s: reply truncated at max_tokens", model)
        return Completion(text="".join(parts), provider=self.name, model=str(data.get("model") or model),
                          input_tokens=input_tokens, output_tokens=int(usage.get("output_tokens") or 0),
                          latency_ms=latency_ms, raw={"id": data.get("id"), "stop_reason": stop, "usage": usage})

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

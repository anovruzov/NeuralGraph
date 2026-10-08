"""Anthropic Messages API provider on the official ``anthropic`` Python SDK (docs/mycelic/DECISIONS.md D7).

The SDK owns the transport details (headers, retries with backoff for 408/409/429/5xx and connection errors,
``retry-after``); this module owns what Mycelic needs on top: a per-model capability table, JSON-mode instructions,
a hard per-call timeout from settings, refusal handling, and usage mapping onto :class:`Completion`.

Per-model request rules (from the ``claude-api`` reference, cached 2026-10-06):

* Sampling (``temperature``) is rejected (HTTP 400) by the Fable / Mythos line, Opus 5 / 5.5, Opus 4.8 / 4.7,
  Sonnet 5 / 5.5 and Haiku 5.5, so it is only sent to models that accept it (Haiku 4.5, Sonnet / Opus 4.6 and older).
* ``output_config.effort`` is accepted by Fable / Mythos, Opus 5.x and 4.5-4.8, Sonnet 5.x / 4.6 and Haiku 5.5, and
  rejected by Haiku 4.5 and Sonnet 4.5, so a configured effort is only sent where it is accepted.
* Fable / Mythos, Opus 5 / 5.5, Sonnet 5 / 5.5 and Haiku 5.5 think by default: ``thinking_headroom_tokens`` is added to
  ``max_tokens`` so adaptive thinking cannot starve the JSON answer, and only ``text`` blocks are read.
* Refusals: for Claude Fable 5.1, Opus 5.5, Opus 5 and Sonnet 5.5 the server-side fallback is enabled by default
  (``fallbacks="default"`` with the ``server-side-fallback-2026-07-01`` beta), so a classifier decline is retried on a
  suitable model inside the same call. ``fallbacks="off"`` disables it (needed behind gateways that do not support the
  beta). A refusal that survives the fallback raises :class:`ModelError`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .base import Completion, ModelError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
# tier defaults when MYCELIC_MODEL_<TIER> is "anthropic:" without a model: the current generation of each line
DEFAULT_MODELS = {"light": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "heavy": "claude-opus-5-5"}
JSON_INSTRUCTION = "Return a single valid JSON object and nothing else: no prose, no code fences."
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_NO_SAMPLING_RE = re.compile(r"claude-(?:fable|mythos|opus-5|opus-4-(?:7|8)|sonnet-5|haiku-5)(?:$|[-@:.])")
_THINKS_BY_DEFAULT_RE = re.compile(r"claude-(?:fable|mythos|opus-5|sonnet-5|haiku-5)(?:$|[-@:.])")
_NO_EFFORT_RE = re.compile(r"claude-(?:haiku-4|sonnet-4-5|sonnet-4-0|opus-4-0|opus-4-1|3)(?:$|[-@:.])")
_FALLBACK_MODELS_RE = re.compile(r"claude-(?:fable-5-1|opus-5-5|opus-5|sonnet-5-5)(?:$|[-@:])")


def _bare(model: str) -> str:
    """Strip a platform prefix (``us.anthropic.``, ``anthropic.``) so the rules apply to the model family."""
    return model.split("anthropic.")[-1]


def accepts_sampling(model: str) -> bool:
    return not _NO_SAMPLING_RE.search(_bare(model))


def thinks_by_default(model: str) -> bool:
    return bool(_THINKS_BY_DEFAULT_RE.search(_bare(model)))


def accepts_effort(model: str) -> bool:
    return not _NO_EFFORT_RE.search(_bare(model))


def supports_fallbacks(model: str) -> bool:
    return bool(_FALLBACK_MODELS_RE.search(_bare(model)))


def split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Anthropic takes the system prompt as a top-level field: join every system message, keep the rest in order."""
    system = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    rest = [{"role": m["role"], "content": m.get("content", "")} for m in messages if m.get("role") in ("user", "assistant")]
    return system, rest


class AnthropicProvider:
    """``ModelProvider`` for the Claude API.

    Args:
        api_key: server-side secret (never logged or returned).
        base_url: API base (``ANTHROPIC_BASE_URL``); an empty value means the public API.
        max_parallel: concurrent requests from this process.
        retries: SDK ``max_retries`` (retryable statuses and connection errors only).
        timeout: per-request timeout in seconds when the caller passes none (``MYCELIC_MODEL_TIMEOUT_SECONDS``).
        effort: optional ``output_config.effort`` for models that accept it.
        thinking: optional explicit ``thinking`` object (most callers leave it unset).
        send_temperature: force (True) or suppress (False) ``temperature``; ``None`` follows the capability table.
        thinking_headroom_tokens: added to ``max_tokens`` for models that think by default.
        fallbacks: ``"default"`` (server-side refusal fallback where supported) or ``"off"``.
        backoff: accepted for compatibility; the SDK schedules its own backoff and honours ``retry-after``.
    """

    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, *, max_parallel: int = 4, retries: int = 2, backoff: float = 1.0,
                 timeout: float = 120.0, effort: str | None = None, thinking: dict[str, Any] | None = None, send_temperature: bool | None = None,
                 thinking_headroom_tokens: int = 8000, fallbacks: str = "default", client: Any = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.retries = max(0, int(retries))
        self.timeout = float(timeout)
        self.effort = effort or None
        self.thinking = thinking
        self.send_temperature = send_temperature
        self.thinking_headroom_tokens = max(0, int(thinking_headroom_tokens))
        self.fallbacks = fallbacks if fallbacks in ("default", "off") else "default"
        self._sem = asyncio.Semaphore(max(1, int(max_parallel)))
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - the package is in requirements-mycelic.txt
                raise ModelError("the anthropic package is not installed (pip install -r requirements-mycelic.txt)") from exc
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key, base_url=self.base_url, max_retries=self.retries, timeout=self.timeout)
        return self._client

    def build_request(self, messages: list[dict[str, str]], *, model: str, max_tokens: int, temperature: float | None,
                      json_mode: bool) -> tuple[dict[str, Any], bool]:
        """Keyword arguments for ``messages.create`` and whether the beta endpoint (refusal fallback) is used."""
        system, rest = split_system(messages)
        if json_mode:
            system = f"{system}\n\n{JSON_INSTRUCTION}".strip()
        kwargs: dict[str, Any] = {"model": model, "max_tokens": int(max_tokens), "messages": rest}
        if system:
            kwargs["system"] = system
        if thinks_by_default(model) and self.thinking is None:
            kwargs["max_tokens"] = int(max_tokens) + self.thinking_headroom_tokens
        send_temp = self.send_temperature if self.send_temperature is not None else accepts_sampling(model)
        if send_temp and temperature is not None:
            # SDK 1.x dropped sampling parameters from create() (current models reject them); the older models that still
            # accept temperature receive it as an extra body field
            kwargs["extra_body"] = {"temperature": float(temperature)}
        if self.effort and accepts_effort(model):
            kwargs["output_config"] = {"effort": self.effort}
        if self.thinking is not None:
            kwargs["thinking"] = self.thinking
        beta = self.fallbacks == "default" and supports_fallbacks(model)
        if beta:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs, beta

    async def complete(self, messages: list[dict[str, str]], *, model: str, max_tokens: int = 1024, temperature: float | None = 0.0,
                       json_mode: bool = True, timeout: float | None = None) -> Completion:
        import anthropic

        client = self._get_client()
        kwargs, beta = self.build_request(messages, model=model, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode)
        per_call = float(timeout) if timeout is not None else self.timeout
        t0 = time.perf_counter()
        try:
            async with self._sem:
                api = client.with_options(timeout=per_call)
                resp = await (api.beta.messages.create(**kwargs) if beta else api.messages.create(**kwargs))
        except anthropic.APIStatusError as exc:
            detail = exc.message
            body = getattr(exc, "body", None)
            if isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("message"):
                detail = body["error"]["message"]
            retryable = exc.status_code in (408, 409, 429) or exc.status_code >= 500
            attempts = f" after {self.retries + 1} attempts" if retryable else ""
            raise ModelError(f"anthropic HTTP {exc.status_code}{attempts}: {detail}"[:500]) from exc
        except anthropic.APITimeoutError as exc:
            raise ModelError(f"anthropic request timed out after {per_call:.0f}s ({self.retries + 1} attempts)") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError(f"anthropic network error after {self.retries + 1} attempts: {exc}"[:500]) from exc
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            raise ModelError(f"model refused the request (category {category or 'unspecified'})")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        usage = resp.usage
        input_tokens = int(usage.input_tokens or 0) + int(getattr(usage, "cache_read_input_tokens", 0) or 0) + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        return Completion(text=text, provider=self.name, model=str(resp.model or model), input_tokens=input_tokens,
                          output_tokens=int(usage.output_tokens or 0), latency_ms=latency_ms,
                          raw={"id": resp.id, "stop_reason": resp.stop_reason, "beta_fallbacks": beta})

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            finally:
                self._client = None

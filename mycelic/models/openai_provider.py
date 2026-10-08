"""OpenAI-compatible chat and embedding providers, plus the deterministic hash embedder (D7).

``OpenAICompatProvider`` speaks ``POST {base_url}/chat/completions`` so one class serves OpenAI, Ollama's ``/v1``,
LM Studio and any compatible gateway. JSON is always requested in the system prompt (every server honours
that); ``response_format: {"type": "json_object"}`` is added only for ``api.openai.com`` (or when forced),
because local servers differ in whether they accept it. OpenAI's reasoning models (``o*``, ``gpt-5*``) reject
``temperature`` and want ``max_completion_tokens``; both are handled per model id.

``OpenAICompatEmbeddings`` calls ``POST {base_url}/embeddings``. ``HashEmbeddings`` wraps NeuralGraph's
deterministic bag-of-words embedding so tests and the demonstration need no embedding server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp

from NeuralGraph.chat_memory.llm import fake_embedding

from .base import Completion, ModelError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODELS = {"light": "gpt-5-mini", "standard": "gpt-5", "heavy": "gpt-5"}
DEFAULT_EMBED_MODEL = "text-embedding-3-small"

JSON_INSTRUCTION = ("Respond with a single valid JSON object and nothing else: no prose before or after it, no code fences, "
                    "no comments.")

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_OPENAI_REASONING_RE = re.compile(r"^(?:o\d|gpt-5)")
_RETRYABLE_STATUS = frozenset({408, 409, 429})


def is_openai_host(base_url: str) -> bool:
    host = (urlparse(base_url or "").hostname or "").lower()
    return host == "api.openai.com" or host.endswith(".openai.com")


def _error_message(status: int, text: str) -> str:
    try:
        data = json.loads(text)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return f"HTTP {status} {err.get('type') or err.get('code') or 'error'}: {err['message']}"
        if isinstance(err, str):
            return f"HTTP {status}: {err}"
    except (ValueError, AttributeError):
        pass
    return f"HTTP {status}: {text[:300]}"


class _HttpBase:
    """Shared session, semaphore and retry loop for the OpenAI-compatible endpoints."""

    name = "openai"

    def __init__(self, api_key: str, base_url: str, *, max_parallel: int, retries: int, backoff: float, timeout: float,
                 session: aiohttp.ClientSession | None) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.max_parallel = max(1, int(max_parallel))
        self.retries = max(0, int(retries))
        self.backoff = max(0.0, float(backoff))
        self.timeout = float(timeout)
        self._session = session
        self._owns_session = session is None
        self._session_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(self.max_parallel)

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
                self._owns_session = True
            return self._session

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def _post_with_retries(self, path: str, body: dict[str, Any], *, timeout: float, what: str) -> dict[str, Any]:
        last_error = "no attempt made"
        for attempt in range(self.retries + 1):
            retry_after: float | None = None
            try:
                session = await self._get_session()
                async with self._sem:
                    async with session.post(f"{self.base_url}{path}", json=body, headers=self._headers(),
                                            timeout=aiohttp.ClientTimeout(total=timeout if timeout else self.timeout)) as resp:
                        status, text, headers = resp.status, await resp.text(), resp.headers
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_error = f"network error: {exc.__class__.__name__}: {exc}"
            else:
                if status < 400:
                    try:
                        data = json.loads(text)
                    except ValueError as exc:
                        raise ModelError(f"{what}: response is not JSON ({exc})") from exc
                    if not isinstance(data, dict):
                        raise ModelError(f"{what}: unexpected response shape")
                    return data
                last_error = _error_message(status, text)
                if status not in _RETRYABLE_STATUS and status < 500:
                    raise ModelError(f"{what}: {last_error}")
                ra = headers.get("retry-after")
                if ra and str(ra).strip().isdigit():
                    retry_after = min(60.0, float(ra))
            if attempt < self.retries:
                delay = max(self.backoff * (2 ** attempt), retry_after or 0.0)
                logger.warning("%s failed (attempt %d/%d): %s; retrying in %.1fs", what, attempt + 1, self.retries + 1, last_error, delay)
                if delay:
                    await asyncio.sleep(delay)
        raise ModelError(f"{what} failed after {self.retries + 1} attempts: {last_error}")

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


class OpenAICompatProvider(_HttpBase):
    """``ModelProvider`` for ``/chat/completions``.

    ``json_response_format``: ``None`` sends ``response_format`` only to api.openai.com; ``True``/``False`` force it.
    ``extra_body`` is merged into every request (e.g. ``{"reasoning_effort": "none"}`` for LM Studio thinking models).
    """

    name = "openai"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, *, max_parallel: int = 4, retries: int = 2,
                 backoff: float = 1.0, timeout: float = 120.0, session: aiohttp.ClientSession | None = None,
                 json_response_format: bool | None = None, extra_body: dict[str, Any] | None = None) -> None:
        super().__init__(api_key, base_url, max_parallel=max_parallel, retries=retries, backoff=backoff, timeout=timeout, session=session)
        self.json_response_format = json_response_format
        self.extra_body = dict(extra_body or {})

    def build_request(self, messages: list[dict[str, str]], *, model: str, max_tokens: int, temperature: float,
                      json_mode: bool) -> dict[str, Any]:
        msgs = [{"role": m.get("role", "user"), "content": m.get("content", "") or ""} for m in messages]
        if json_mode:
            if msgs and msgs[0]["role"] == "system":
                msgs[0] = {"role": "system", "content": (msgs[0]["content"] + "\n\n" if msgs[0]["content"] else "") + JSON_INSTRUCTION}
            else:
                msgs.insert(0, {"role": "system", "content": JSON_INSTRUCTION})
        openai_host = is_openai_host(self.base_url)
        reasoning = openai_host and bool(_OPENAI_REASONING_RE.match((model or "").lower()))
        body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False}
        if reasoning:
            body["max_completion_tokens"] = int(max_tokens)
        else:
            body["max_tokens"] = int(max_tokens)
            body["temperature"] = float(temperature)
        use_format = openai_host if self.json_response_format is None else self.json_response_format
        if json_mode and use_format:
            body["response_format"] = {"type": "json_object"}
        body.update(self.extra_body)
        return body

    async def complete(self, messages: list[dict[str, str]], *, model: str, max_tokens: int = 1024, temperature: float = 0.0,
                       json_mode: bool = True, timeout: float | None = None) -> Completion:
        body = self.build_request(messages, model=model, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode)
        t0 = time.perf_counter()
        data = await self._post_with_retries("/chat/completions", body, timeout=timeout, what=f"openai {model}")
        choices = data.get("choices") or []
        msg = (choices[0].get("message") if choices and isinstance(choices[0], dict) else None) or {}
        content = msg.get("content")
        if isinstance(content, list):  # some servers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = _THINK_RE.sub("", content or "").strip()
        usage = data.get("usage") or {}
        finish = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
        if finish == "length":
            logger.warning("openai %s: reply truncated at max_tokens", model)
        return Completion(text=text, provider=self.name, model=str(data.get("model") or model),
                          input_tokens=int(usage.get("prompt_tokens") or 0), output_tokens=int(usage.get("completion_tokens") or 0),
                          latency_ms=int((time.perf_counter() - t0) * 1000),
                          raw={"id": data.get("id"), "finish_reason": finish, "usage": usage})


class OpenAICompatEmbeddings(_HttpBase):
    """``EmbeddingProvider`` for ``/embeddings``. ``dim`` is learned from the first reply."""

    name = "openai"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_EMBED_MODEL, *, max_parallel: int = 4,
                 retries: int = 2, backoff: float = 1.0, timeout: float = 60.0, session: aiohttp.ClientSession | None = None,
                 batch_size: int = 64, dim: int | None = None) -> None:
        super().__init__(api_key, base_url, max_parallel=max_parallel, retries=retries, backoff=backoff, timeout=timeout, session=session)
        self.model = model or DEFAULT_EMBED_MODEL
        self.batch_size = max(1, int(batch_size))
        self.dim = dim
        self.total_tokens = 0

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = await self._post_with_retries("/embeddings", {"model": self.model, "input": texts}, timeout=self.timeout,
                                             what=f"embeddings {self.model}")
        items = data.get("data") or []
        if len(items) != len(texts):
            raise ModelError(f"embeddings {self.model}: expected {len(texts)} vectors, got {len(items)}")
        items = sorted(items, key=lambda d: int(d.get("index", 0)))
        vecs = [[float(x) for x in (d.get("embedding") or [])] for d in items]
        if any(not v for v in vecs):
            raise ModelError(f"embeddings {self.model}: empty vector returned")
        self.dim = self.dim or len(vecs[0])
        usage = data.get("usage") or {}
        self.total_tokens += int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0)
        return vecs

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(await self._embed_batch([t if t.strip() else " " for t in texts[i:i + self.batch_size]]))
        return out


class HashEmbeddings:
    """Deterministic bag-of-words embedding (unit norm), from :func:`NeuralGraph.chat_memory.llm.fake_embedding`."""

    name = "hash"

    def __init__(self, dim: int = 256) -> None:
        self.dim = max(8, int(dim))
        self.model = f"hash-bow-{self.dim}"
        self.closed = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [fake_embedding(t or "", self.dim) for t in texts]

    async def close(self) -> None:
        self.closed = True

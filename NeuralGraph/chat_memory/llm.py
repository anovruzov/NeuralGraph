"""LLM client abstraction for the chat-memory worker.

The worker never talks to ``llm_backend`` directly; it talks to an :class:`LLMClient`.
That gives us two things:

* ``BackendLLMClient`` wraps :mod:`NeuralGraph.llm_backend` (LM Studio or Ollama) with one
  shared ``aiohttp`` session, a semaphore that bounds how many Qwen calls run in parallel,
  and bounded retries with exponential backoff for transient HTTP/network failures.
* ``FakeLLMClient`` is a deterministic, server-free stand-in used by the unit tests. It answers
  prompts from a script keyed on a prompt marker, and produces hash-based embeddings whose
  cosine similarity behaves sensibly (identical text -> 1.0, shared words -> higher).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when a generation/embedding call fails after all retries."""


@runtime_checkable
class LLMClient(Protocol):
    """Minimal async interface the worker and retriever depend on."""

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float = 120.0,
    ) -> str: ...

    async def embed(self, text: str) -> list[float]: ...

    async def embed_many(self, texts: list[str]) -> list[list[float]]: ...

    async def close(self) -> None: ...


@dataclass
class LLMStats:
    generate_calls: int = 0
    generate_failures: int = 0
    generate_seconds: float = 0.0
    embed_calls: int = 0
    embed_failures: int = 0
    embed_seconds: float = 0.0
    prompt_chars: int = 0
    completion_chars: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------------------------
# Real backend
# ---------------------------------------------------------------------------------------------


class BackendLLMClient:
    """LLM/embedding client over :mod:`NeuralGraph.llm_backend`.

    Args:
        base_url: server URL (default: ``llm_backend.LLM_BASE_URL``; env ``LLM_BASE_URL``).
        model: generation model (default: ``llm_backend.LLM_MODEL``; env ``LLM_MODEL``).
        embed_model: embedding model (default: ``llm_backend.EMBED_MODEL``; env ``EMBED_MODEL``).
        max_parallel: upper bound on concurrent generate+embed HTTP calls. Match it to the server
            (Ollama: ``OLLAMA_NUM_PARALLEL``; LM Studio: its parallel-request setting).
        retries: additional attempts after the first failure (network / 5xx / timeout).
        backoff_seconds: base of the exponential backoff between attempts.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
        max_parallel: int = 4,
        retries: int = 2,
        backoff_seconds: float = 1.0,
    ) -> None:
        from .. import llm_backend  # local import: keeps aiohttp out of the fake path

        self._backend = llm_backend
        self.base_url = (base_url or llm_backend.LLM_BASE_URL).rstrip("/")
        self.model = model or llm_backend.LLM_MODEL
        self.embed_model = embed_model or llm_backend.EMBED_MODEL
        self.max_parallel = max(1, int(max_parallel))
        self.retries = max(0, int(retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self._sem = asyncio.Semaphore(self.max_parallel)
        self._session = None
        self._session_lock = asyncio.Lock()
        self.stats = LLMStats()

    async def _get_session(self):
        import aiohttp

        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            return self._session

    async def _with_retries(self, what: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        last: BaseException | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._sem:
                    return await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network, HTTP status, timeout, JSON decode
                last = exc
                if attempt < self.retries:
                    delay = self.backoff_seconds * (2 ** attempt)
                    logger.warning("%s failed (attempt %d/%d): %s; retrying in %.1fs",
                                   what, attempt + 1, self.retries + 1, exc, delay)
                    await asyncio.sleep(delay)
        raise LLMError(f"{what} failed after {self.retries + 1} attempts: {last}") from last

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float = 120.0,
    ) -> str:
        session = await self._get_session()
        t0 = time.perf_counter()
        self.stats.generate_calls += 1
        self.stats.prompt_chars += len(prompt)

        async def call():
            return await self._backend.llm_generate(
                session, prompt, model=self.model, base_url=self.base_url,
                temperature=temperature, max_tokens=max_tokens, timeout_seconds=timeout,
            )

        try:
            out = await self._with_retries("generate", call)
        except LLMError:
            self.stats.generate_failures += 1
            raise
        finally:
            self.stats.generate_seconds += time.perf_counter() - t0
        out = out or ""
        self.stats.completion_chars += len(out)
        return out

    async def embed(self, text: str) -> list[float]:
        session = await self._get_session()
        t0 = time.perf_counter()
        self.stats.embed_calls += 1

        async def call():
            vec = await self._backend.llm_embed(
                session, text, model=self.embed_model, base_url=self.base_url, timeout_seconds=60.0,
            )
            if not vec:
                raise LLMError("empty embedding returned")
            return [float(x) for x in vec]

        try:
            return await self._with_retries("embed", call)
        except LLMError:
            self.stats.embed_failures += 1
            raise
        finally:
            self.stats.embed_seconds += time.perf_counter() - t0

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        # Bounded by the semaphore inside embed(); gather keeps ordering.
        return list(await asyncio.gather(*(self.embed(t) for t in texts)))

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


# ---------------------------------------------------------------------------------------------
# Deterministic fake for tests
# ---------------------------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def fake_embedding(text: str, dim: int = 256) -> list[float]:
    """Deterministic bag-of-words hashing embedding (unit norm).

    Shared words push vectors together, so cosine similarity is a usable proxy for
    lexical overlap in tests. Identical text -> identical vector.
    """
    from .textutil import stem

    vec = [0.0] * dim
    words = [stem(w) for w in _WORD_RE.findall(text.lower())]
    if not words:
        vec[0] = 1.0
        return vec
    for w in words:
        h = hashlib.blake2b(w.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


@dataclass
class FakeLLMClient:
    """Scripted LLM for deterministic tests.

    ``responder`` is called with the prompt and must return the completion text. The default
    responder looks up ``script`` entries: a list of ``(marker, response)`` pairs; the first marker
    found in the prompt wins. When nothing matches, ``default_response`` is returned.

    ``fail_times`` makes the first N generate() calls raise LLMError (for retry/backoff tests).
    ``latency`` adds an ``asyncio.sleep`` per call so parallelism can be observed.
    """

    script: list[tuple[str, str | Callable[[str], str]]] = field(default_factory=list)
    default_response: str = "{}"
    responder: Callable[[str], str] | None = None
    dim: int = 256
    fail_times: int = 0
    latency: float = 0.0
    prompts: list[str] = field(default_factory=list)
    embedded: list[str] = field(default_factory=list)
    stats: LLMStats = field(default_factory=LLMStats)
    in_flight: int = 0
    max_in_flight: int = 0
    closed: bool = False

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float = 120.0,
    ) -> str:
        self.prompts.append(prompt)
        self.stats.generate_calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.latency:
                await asyncio.sleep(self.latency)
            if self.fail_times > 0:
                self.fail_times -= 1
                self.stats.generate_failures += 1
                raise LLMError("scripted failure")
            if self.responder is not None:
                return self.responder(prompt)
            for marker, response in self.script:
                if marker in prompt:
                    return response(prompt) if callable(response) else response
            return self.default_response
        finally:
            self.in_flight -= 1

    async def embed(self, text: str) -> list[float]:
        self.embedded.append(text)
        self.stats.embed_calls += 1
        if self.latency:
            await asyncio.sleep(self.latency / 4)
        return fake_embedding(text, self.dim)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]

    async def close(self) -> None:
        self.closed = True

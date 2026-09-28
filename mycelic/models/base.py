"""Model providers, tiered routing and the usage ledger (docs/mycelic/DECISIONS.md D7).

* :class:`ModelProvider` — ``complete(messages, ...)`` returns a :class:`Completion` with token counts. Providers:
  ``fake`` (deterministic, ``mycelic.models.fake``), ``anthropic`` (``mycelic.models.anthropic_provider``),
  ``openai`` (``mycelic.models.openai_provider``: OpenAI, Ollama ``/v1``, LM Studio, any compatible server).
* :class:`EmbeddingProvider` — ``embed(texts)``; ``hash`` (deterministic bag-of-words, from NeuralGraph) or
  ``openai``-compatible ``/embeddings``.
* :class:`ModelRouter` — ``run_task(task, input, *, tenant_id, goal_id, question_id, tier=None)`` picks the tier from
  tenant policy (``model_tiers`` maps task -> tier) unless overridden, renders the prompt for the task, calls the
  provider, parses/validates the JSON output against :mod:`mycelic.models.tasks`, records usage and returns the
  parsed dict. Escalation: when a ``light`` call fails validation twice the router retries once on the next tier
  (configurable), never above ``max_tier``.
* :class:`UsageLedger` — records every call in ``model_usage`` with cost from a configurable price table.

Secrets (API keys) are read from settings on the server; nothing here is ever serialized to the UI except
provider and model names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

TIERS = ("light", "standard", "heavy")


class ModelError(RuntimeError):
    """Provider failure after retries, or an output that could not be made to satisfy the task schema."""


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def complete(self, messages: list[dict[str, str]], *, model: str, max_tokens: int = 1024, temperature: float = 0.0,
                       json_mode: bool = True, timeout: float = 120.0) -> Completion: ...

    async def close(self) -> None: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    model: str
    dim: int | None

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def close(self) -> None: ...


@dataclass
class ModelCall:
    """One recorded call (what the ledger writes)."""
    tenant_id: str | None
    provider: str
    model: str
    tier: str
    purpose: str
    goal_id: str | None
    question_id: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    ok: bool
    error: str | None = None


class UsageLedger(Protocol):
    async def record(self, call: ModelCall) -> None: ...

    def totals(self, tenant_id: str | None = None, *, goal_id: str | None = None, since: str | None = None) -> dict[str, Any]: ...


class ModelRouter(Protocol):
    """See module docstring. Implemented in :mod:`mycelic.models.router`."""
    tiers: dict[str, str]

    async def run_task(self, task: str, input: dict[str, Any], *, tenant_id: str | None, goal_id: str | None = None,
                       question_id: str | None = None, tier: str | None = None, max_tier: str | None = None,
                       policy_tiers: dict[str, str] | None = None) -> dict[str, Any]: ...

    async def complete_text(self, messages: list[dict[str, str]], *, tier: str, tenant_id: str | None, purpose: str,
                            goal_id: str | None = None, question_id: str | None = None, max_tokens: int = 1024) -> Completion: ...

    def describe(self) -> dict[str, Any]:
        """Provider/model per tier, embedding provider, price table — never keys."""
        ...

    async def close(self) -> None: ...


def build_router(settings: Any, db: Any) -> "ModelRouter":
    from .router import DefaultModelRouter
    return DefaultModelRouter.from_settings(settings, db)


def build_embedder(settings: Any) -> "EmbeddingProvider":
    from .router import build_embedding_provider
    return build_embedding_provider(settings)

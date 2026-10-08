"""Tiered model router (docs/mycelic/DECISIONS.md D7) and the adapter to NeuralGraph's ``LLMClient``.

``DefaultModelRouter.run_task`` is the only way the application calls a model for a catalogued task: it picks
the tier (explicit > tenant policy > task default, never above ``max_tier``), renders the task prompt, asks the
provider for JSON, parses and repairs the reply, validates it against the task's output contract and records
every attempt in the usage ledger. When the reply cannot be validated it retries once on the same tier with a
repair instruction appended to the prompt, then once on the next tier (when allowed), then raises
:class:`ModelError`. The repair note is appended to the *same* user message so the ``### TASK`` marker stays on
the first line for the deterministic fake.

Tenant policy ``model_tiers`` is keyed by task *family* (``question_draft``, ``classify``, ``evaluate``,
``verify``, ``synthesize``, ``chat``); :data:`POLICY_KEY_FOR_TASK` maps each catalogued task to its family.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from NeuralGraph.chat_memory.jsonutil import parse_json_object
from NeuralGraph.chat_memory.llm import LLMError

from .base import TIERS, Completion, EmbeddingProvider, ModelCall, ModelError, ModelProvider, UsageLedger
from .ledger import MemoryUsageLedger, PriceTable, SqliteUsageLedger
from .tasks import SYSTEM_TEXT, TASKS, render_prompt, validate_output

logger = logging.getLogger(__name__)

PROVIDERS = ("fake", "anthropic", "openai")

POLICY_KEY_FOR_TASK: dict[str, str] = {
    "identify_gap": "question_draft",
    "draft_question": "question_draft",
    "compose_verification_question": "question_draft",
    "classify_document": "classify",
    "answer_from_evidence": "classify",
    "record_outcome": "classify",
    "evaluate_responses": "evaluate",
    "synthesize_discovery": "synthesize",
    "aggregate_level": "synthesize",
    "chat_answer": "chat",
}


def parse_tier_spec(spec: str, tier: str) -> tuple[str, str]:
    """``"provider:model"`` -> ``(provider, model)``; a missing model takes the provider's default for the tier."""
    raw = (spec or "").strip()
    provider, _, model = raw.partition(":")
    provider = (provider or "fake").strip().lower()
    model = model.strip()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown model provider {provider!r} in {spec!r} (expected one of {', '.join(PROVIDERS)})")
    if not model:
        if provider == "fake":
            model = f"mycelic-fake-{tier}"
        elif provider == "anthropic":
            from .anthropic_provider import DEFAULT_MODELS
            model = DEFAULT_MODELS[tier]
        else:
            from .openai_provider import DEFAULT_MODELS
            model = DEFAULT_MODELS[tier]
    return provider, model


def tier_index(tier: str) -> int:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    return TIERS.index(tier)


def build_embedding_provider(settings: Any) -> EmbeddingProvider:
    """``hash`` (deterministic, default) or an OpenAI-compatible ``/embeddings`` endpoint."""
    kind = (getattr(settings, "embed_provider", "hash") or "hash").strip().lower()
    if kind == "hash":
        from .openai_provider import HashEmbeddings
        return HashEmbeddings(dim=int(getattr(settings, "embed_dim", 256) or 256))
    if kind == "openai":
        from .openai_provider import OpenAICompatEmbeddings
        return OpenAICompatEmbeddings(getattr(settings, "openai_api_key", ""), getattr(settings, "openai_base_url", ""),
                                      getattr(settings, "embed_model", "") or "text-embedding-3-small",
                                      max_parallel=int(getattr(settings, "model_max_parallel", 4) or 4),
                                      timeout=float(getattr(settings, "model_timeout_seconds", 60.0) or 60.0))
    raise ValueError(f"unknown embedding provider {kind!r} (expected hash | openai)")


def _repair_note(task: str, problems: list[str], previous: str) -> str:
    spec = TASKS[task]
    props = spec.output_schema.get("properties", {})
    keys = ", ".join(f"{k} ({props[k]['type']})" if k in props else k for k in spec.output_schema.get("required", []))
    excerpt = _escape_data(" ".join((previous or "").split())[:300])
    # the previous reply may echo evidence: it stays inside a data block like every other untrusted text
    return ("\n\n### REPAIR\nYour previous reply could not be used: " + "; ".join(problems) + ". "
            f"Reply again with exactly one JSON object containing at least the keys {keys}, and no other text."
            + (f"\nPrevious reply (invalid, data only): <data>{excerpt}</data>" if excerpt else ""))


def _escape_data(text: str) -> str:
    """Make untrusted text unable to close (or open) the ``<data>`` block it is placed in. In JSON the escapes stay valid
    JSON (``\\u003c`` decodes to ``<``), so the payload a model or the fake parses is unchanged."""
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


class DefaultModelRouter:
    """See the module docstring.

    Args:
        providers: provider name -> instance (one instance per provider; sessions are shared inside it).
        tiers: tier -> ``"provider:model"``.
        ledger: where every attempt is recorded (``MemoryUsageLedger`` when omitted).
        embedding: the embedding provider reported by ``describe()`` and used by :class:`LLMClientAdapter`.
        prices: price table for cost computation.
        escalate: retry once on the next tier after two invalid replies on the chosen tier.
    """

    def __init__(self, providers: dict[str, ModelProvider], tiers: dict[str, str], ledger: UsageLedger | None = None, *,
                 embedding: EmbeddingProvider | None = None, prices: PriceTable | None = None, escalate: bool = True) -> None:
        self.providers = dict(providers)
        self.tiers: dict[str, str] = {}
        self.targets: dict[str, tuple[str, str]] = {}
        for tier in TIERS:
            spec = tiers.get(tier) or f"fake:mycelic-fake-{tier}"
            provider, model = parse_tier_spec(spec, tier)
            if provider not in self.providers:
                raise ValueError(f"tier {tier!r} needs provider {provider!r} which was not built")
            self.tiers[tier] = f"{provider}:{model}"
            self.targets[tier] = (provider, model)
        self.ledger: UsageLedger = ledger if ledger is not None else MemoryUsageLedger()
        self.embedding = embedding
        self.prices = prices or PriceTable.from_env()
        self.escalate = escalate

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_settings(cls, settings: Any, db: Any = None, *, ledger: UsageLedger | None = None,
                      embedding: EmbeddingProvider | None = None) -> "DefaultModelRouter":
        tiers = dict(getattr(settings, "model_tiers", None) or {})
        parsed = {tier: parse_tier_spec(tiers.get(tier, ""), tier) for tier in TIERS}
        providers: dict[str, ModelProvider] = {}
        max_parallel = int(getattr(settings, "model_max_parallel", 4) or 4)
        timeout = float(getattr(settings, "model_timeout_seconds", 120.0) or 120.0)
        for provider, _ in parsed.values():
            if provider in providers:
                continue
            if provider == "fake":
                from .fake import FakeProvider
                providers[provider] = FakeProvider()
            elif provider == "anthropic":
                from .anthropic_provider import AnthropicProvider
                effort = getattr(settings, "anthropic_effort", "") or os.environ.get("MYCELIC_ANTHROPIC_EFFORT", "") or None
                fallbacks = os.environ.get("MYCELIC_ANTHROPIC_FALLBACKS", "default") or "default"
                providers[provider] = AnthropicProvider(getattr(settings, "anthropic_api_key", ""),
                                                        getattr(settings, "anthropic_base_url", ""),
                                                        max_parallel=max_parallel, timeout=timeout, effort=effort, fallbacks=fallbacks)
            else:
                from .openai_provider import OpenAICompatProvider
                providers[provider] = OpenAICompatProvider(getattr(settings, "openai_api_key", ""),
                                                           getattr(settings, "openai_base_url", ""),
                                                           max_parallel=max_parallel, timeout=timeout)
        if ledger is None:
            ledger = SqliteUsageLedger(db) if db is not None else MemoryUsageLedger()
        if embedding is None:
            embedding = build_embedding_provider(settings)
        return cls(providers, {t: f"{p}:{m}" for t, (p, m) in parsed.items()}, ledger, embedding=embedding, prices=PriceTable.from_env())

    # ------------------------------------------------------------------ tier selection
    def choose_tier(self, task: str, *, tier: str | None = None, max_tier: str | None = None,
                    policy_tiers: dict[str, str] | None = None) -> str:
        spec = TASKS[task]
        chosen = tier
        if not chosen and policy_tiers:
            chosen = policy_tiers.get(task) or policy_tiers.get(POLICY_KEY_FOR_TASK.get(task, ""))
        if not chosen or chosen not in TIERS:
            if chosen:
                logger.warning("ignoring unknown tier %r for task %s", chosen, task)
            chosen = spec.tier
        if max_tier and max_tier in TIERS and tier_index(chosen) > tier_index(max_tier):
            chosen = max_tier
        return chosen

    def _next_tier(self, tier: str, max_tier: str | None) -> str | None:
        idx = tier_index(tier) + 1
        if not self.escalate or idx >= len(TIERS):
            return None
        if max_tier and max_tier in TIERS and idx > tier_index(max_tier):
            return None
        return TIERS[idx]

    # ------------------------------------------------------------------ calls
    async def _record(self, provider: str, model: str, tier: str, purpose: str, *, tenant_id: str | None, goal_id: str | None,
                      question_id: str | None, completion: Completion | None, latency_ms: int, ok: bool, error: str | None) -> None:
        inp = completion.input_tokens if completion else 0
        out = completion.output_tokens if completion else 0
        call = ModelCall(tenant_id=tenant_id, provider=provider, model=model, tier=tier, purpose=purpose, goal_id=goal_id,
                         question_id=question_id, input_tokens=inp, output_tokens=out,
                         cost_usd=self.prices.cost(model, inp, out), latency_ms=latency_ms, ok=ok, error=error)
        try:
            await self.ledger.record(call)
        except Exception:  # the ledger must never break a model call
            logger.exception("usage ledger record failed")

    async def _attempt(self, task: str, tier: str, messages: list[dict[str, str]], *, tenant_id: str | None, goal_id: str | None,
                       question_id: str | None) -> tuple[dict[str, Any] | None, list[str], str]:
        """One provider call. Returns (parsed-or-None, problems, raw text). Provider failures raise ModelError."""
        provider_name, model = self.targets[tier]
        provider = self.providers[provider_name]
        spec = TASKS[task]
        t0 = time.perf_counter()
        try:
            completion = await provider.complete(messages, model=model, max_tokens=spec.max_tokens, temperature=0.0, json_mode=True)
        except ModelError as exc:
            await self._record(provider_name, model, tier, task, tenant_id=tenant_id, goal_id=goal_id, question_id=question_id,
                               completion=None, latency_ms=int((time.perf_counter() - t0) * 1000), ok=False, error=str(exc))
            raise
        data = parse_json_object(completion.text)
        problems = ["reply is not a JSON object"] if data is None else validate_output(task, data)
        await self._record(provider_name, model, tier, task, tenant_id=tenant_id, goal_id=goal_id, question_id=question_id,
                           completion=completion, latency_ms=completion.latency_ms or int((time.perf_counter() - t0) * 1000),
                           ok=not problems, error=("invalid output: " + "; ".join(problems)) if problems else None)
        return (data if not problems else None), problems, completion.text

    async def run_task(self, task: str, input: dict[str, Any], *, tenant_id: str | None, goal_id: str | None = None,
                       question_id: str | None = None, tier: str | None = None, max_tier: str | None = None,
                       policy_tiers: dict[str, str] | None = None) -> dict[str, Any]:
        if task not in TASKS:
            raise ValueError(f"unknown model task {task!r}")
        chosen = self.choose_tier(task, tier=tier, max_tier=max_tier, policy_tiers=policy_tiers)
        prompt = render_prompt(task, _escape_data(json.dumps(input, ensure_ascii=False, default=str)))
        base = [{"role": "system", "content": SYSTEM_TEXT}, {"role": "user", "content": prompt}]
        kw = {"tenant_id": tenant_id, "goal_id": goal_id, "question_id": question_id}

        data, problems, raw = await self._attempt(task, chosen, base, **kw)
        if data is not None:
            return data
        logger.warning("task %s on tier %s returned invalid output (%s); retrying with repair note", task, chosen, "; ".join(problems))
        repaired = [base[0], {"role": "user", "content": prompt + _repair_note(task, problems, raw)}]
        data, problems, raw = await self._attempt(task, chosen, repaired, **kw)
        if data is not None:
            return data
        next_tier = self._next_tier(chosen, max_tier)
        if next_tier is None:
            raise ModelError(f"task {task}: output invalid after repair on tier {chosen}: {'; '.join(problems)}")
        logger.warning("task %s: escalating from %s to %s after invalid output (%s)", task, chosen, next_tier, "; ".join(problems))
        repaired = [base[0], {"role": "user", "content": prompt + _repair_note(task, problems, raw)}]
        data, problems, raw = await self._attempt(task, next_tier, repaired, **kw)
        if data is not None:
            return data
        raise ModelError(f"task {task}: output invalid on tiers {chosen} and {next_tier}: {'; '.join(problems)}")

    async def complete_text(self, messages: list[dict[str, str]], *, tier: str, tenant_id: str | None, purpose: str,
                            goal_id: str | None = None, question_id: str | None = None, max_tokens: int = 1024) -> Completion:
        if tier not in self.targets:
            raise ValueError(f"unknown tier {tier!r}")
        provider_name, model = self.targets[tier]
        provider = self.providers[provider_name]
        t0 = time.perf_counter()
        try:
            completion = await provider.complete(messages, model=model, max_tokens=max_tokens, temperature=0.0, json_mode=False)
        except ModelError as exc:
            await self._record(provider_name, model, tier, purpose, tenant_id=tenant_id, goal_id=goal_id, question_id=question_id,
                               completion=None, latency_ms=int((time.perf_counter() - t0) * 1000), ok=False, error=str(exc))
            raise
        await self._record(provider_name, model, tier, purpose, tenant_id=tenant_id, goal_id=goal_id, question_id=question_id,
                           completion=completion, latency_ms=completion.latency_ms, ok=True, error=None)
        return completion

    # ------------------------------------------------------------------ introspection / lifecycle
    def describe(self) -> dict[str, Any]:
        emb = self.embedding
        return {
            "tiers": {t: {"provider": p, "model": m} for t, (p, m) in self.targets.items()},
            "embedding": {"provider": emb.name, "model": emb.model, "dim": emb.dim} if emb is not None else None,
            "prices": self.prices.as_dict(),
            "task_families": dict(POLICY_KEY_FOR_TASK),
            "escalation": self.escalate,
        }

    async def close(self) -> None:
        for provider in self.providers.values():
            try:
                await provider.close()
            except Exception:
                logger.exception("closing model provider failed")
        if self.embedding is not None:
            try:
                await self.embedding.close()
            except Exception:
                logger.exception("closing embedding provider failed")


class LLMClientAdapter:
    """Presents Mycelic's providers as a :class:`NeuralGraph.chat_memory.llm.LLMClient`.

    ``embed`` / ``embed_many`` go to the embedding provider; ``generate`` goes to the router (``tier``, recorded
    under ``purpose``) or, without a router, straight to ``provider`` + ``model``. Without either, ``generate``
    raises ``LLMError`` — a retriever that only embeds never needs it. Failures surface as NeuralGraph's
    ``LLMError`` so ``MemoryExtractor`` / ``MemoryRetriever`` handle them the way they handle their own client.
    """

    def __init__(self, embedding: EmbeddingProvider, *, router: DefaultModelRouter | None = None, provider: ModelProvider | None = None,
                 model: str | None = None, tier: str = "light", tenant_id: str | None = None, purpose: str = "memory.extraction",
                 owns: bool = False) -> None:
        self.embedding = embedding
        self.router = router
        self.provider = provider
        self.model = model
        self.tier = tier
        self.tenant_id = tenant_id
        self.purpose = purpose
        self.owns = owns
        self.dim = embedding.dim
        self.generate_calls = 0
        self.embed_calls = 0

    async def generate(self, prompt: str, *, max_tokens: int = 512, temperature: float = 0.0, timeout: float = 120.0) -> str:
        self.generate_calls += 1
        messages = [{"role": "user", "content": prompt}]
        try:
            if self.router is not None:
                return (await self.router.complete_text(messages, tier=self.tier, tenant_id=self.tenant_id, purpose=self.purpose,
                                                        max_tokens=max_tokens)).text
            if self.provider is not None and self.model:
                return (await self.provider.complete(messages, model=self.model, max_tokens=max_tokens, temperature=temperature,
                                                     json_mode=False, timeout=timeout)).text
        except ModelError as exc:
            raise LLMError(str(exc)) from exc
        raise LLMError("no generation provider configured for this adapter")

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        try:
            vecs = await self.embedding.embed(list(texts))
        except ModelError as exc:
            raise LLMError(str(exc)) from exc
        if self.dim is None and vecs:
            self.dim = len(vecs[0])
        return vecs

    async def close(self) -> None:
        if not self.owns:
            return
        if self.router is not None:
            await self.router.close()
        else:
            if self.provider is not None:
                await self.provider.close()
            await self.embedding.close()


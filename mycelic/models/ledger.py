"""Usage ledger and price table (docs/mycelic/DECISIONS.md D7: every model call is recorded in ``model_usage``).

The router computes the cost of a call from :class:`PriceTable` and hands a :class:`ModelCall` to the ledger;
the ledger only stores and aggregates. Prices are USD per million tokens. The built-in table is a snapshot
(Anthropic rates from the ``claude-api`` skill reference, cached 2026-06-24; OpenAI rates from general
knowledge) and is **configurable; verify against the provider pricing pages**: ``MYCELIC_MODEL_PRICES_JSON``
merges overrides, e.g. ``{"claude-sonnet-5": {"input": 2.0, "output": 10.0}, "my-local-model": [0, 0]}``.
Fake and unknown (local) models cost 0.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from ..db.coord import CoordDB, rows_to_dicts
from ..util import now_iso
from .base import ModelCall

logger = logging.getLogger(__name__)

PRICES_ENV = "MYCELIC_MODEL_PRICES_JSON"

# model -> (USD per 1M input tokens, USD per 1M output tokens). Configurable; verify against provider pricing pages.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic (claude-api skill reference, cached 2026-06-24)
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-5-5": (0.10, 0.50),      # prompts up to 100K tokens; $0.50 / $2.50 beyond (not modelled)
    "claude-sonnet-5-5": (2.0, 10.0),
    # OpenAI (general knowledge; verify)
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "o3": (2.0, 8.0),
    "o4-mini": (1.10, 4.40),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}

_PROVIDER_PREFIX_RE = re.compile(r"^(?:[a-z]{2}\.)?anthropic\.")


def _normalize_model(model: str) -> str:
    m = (model or "").strip().lower()
    m = m.split("/")[-1] if "/" in m and not m.startswith("gpt") else m  # "org/model" on some gateways
    return _PROVIDER_PREFIX_RE.sub("", m)


@dataclass
class PriceTable:
    prices: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_PRICES))

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "PriceTable":
        table = cls()
        raw = (env if env is not None else os.environ).get(PRICES_ENV, "")
        if raw:
            try:
                table.merge(json.loads(raw))
            except (ValueError, TypeError) as exc:
                logger.warning("%s is not valid JSON (%s); using built-in prices", PRICES_ENV, exc)
        return table

    def merge(self, overrides: dict[str, Any]) -> None:
        for model, price in (overrides or {}).items():
            if isinstance(price, dict):
                pair = (float(price.get("input", 0.0)), float(price.get("output", 0.0)))
            elif isinstance(price, (list, tuple)) and len(price) == 2:
                pair = (float(price[0]), float(price[1]))
            else:
                logger.warning("ignoring price override for %r: expected {input, output} or [input, output]", model)
                continue
            self.prices[str(model).strip().lower()] = pair

    def lookup(self, model: str) -> tuple[float, float] | None:
        """Exact match first, then the longest price key the model id extends (``claude-haiku-4-5-20251001`` ->
        ``claude-haiku-4-5``); ``None`` for unknown (local / fake) models."""
        m = _normalize_model(model)
        if m in self.prices:
            return self.prices[m]
        best: str | None = None
        for key in self.prices:
            if (m.startswith(key + "-") or m.startswith(key + "@") or m.startswith(key + ":")) and (best is None or len(key) > len(best)):
                best = key
        return self.prices[best] if best else None

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pair = self.lookup(model)
        if pair is None:
            return 0.0
        return round((max(0, input_tokens) * pair[0] + max(0, output_tokens) * pair[1]) / 1_000_000, 8)

    def as_dict(self) -> dict[str, dict[str, float]]:
        return {m: {"input_per_million": p[0], "output_per_million": p[1]} for m, p in sorted(self.prices.items())}


def _tier_bucket() -> dict[str, Any]:
    return {"calls": 0, "tokens": 0, "cost_usd": 0.0}


class SqliteUsageLedger:
    """``UsageLedger`` on the coordination database."""

    def __init__(self, db: CoordDB) -> None:
        self.db = db

    async def record(self, call: ModelCall) -> None:
        async with self.db.tx() as c:
            c.execute(
                "INSERT INTO model_usage(tenant_id, at, provider, model, tier, purpose, goal_id, question_id, input_tokens, "
                "output_tokens, cost_usd, latency_ms, ok, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (call.tenant_id, now_iso(), call.provider, call.model, call.tier, call.purpose, call.goal_id, call.question_id,
                 int(call.input_tokens or 0), int(call.output_tokens or 0), float(call.cost_usd or 0.0), int(call.latency_ms or 0),
                 1 if call.ok else 0, (call.error or None) and str(call.error)[:2000]),
            )

    @staticmethod
    def _where(tenant_id: str | None, goal_id: str | None, since: str | None) -> tuple[str, list[Any]]:
        clauses, args = [], []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            args.append(tenant_id)
        if goal_id is not None:
            clauses.append("goal_id = ?")
            args.append(goal_id)
        if since:
            clauses.append("at >= ?")
            args.append(since)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def totals(self, tenant_id: str | None = None, *, goal_id: str | None = None, since: str | None = None) -> dict[str, Any]:
        where, args = self._where(tenant_id, goal_id, since)
        r = self.db.one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS i, COALESCE(SUM(output_tokens), 0) AS o, "
            "COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS failures "
            "FROM model_usage" + where, args)
        out = {"calls": int(r["calls"]), "input_tokens": int(r["i"]), "output_tokens": int(r["o"]),
               "tokens": int(r["i"]) + int(r["o"]), "cost_usd": round(float(r["cost"]), 8), "failures": int(r["failures"]),
               "by_tier": {}, "by_model": {}}
        for key, col in (("by_tier", "tier"), ("by_model", "model")):
            for row in self.db.all(
                    f"SELECT {col} AS k, COUNT(*) AS calls, COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens, "
                    f"COALESCE(SUM(cost_usd), 0) AS cost FROM model_usage{where} GROUP BY {col} ORDER BY {col}", args):
                out[key][row["k"]] = {"calls": int(row["calls"]), "tokens": int(row["tokens"]), "cost_usd": round(float(row["cost"]), 8)}
        return out

    def recent(self, limit: int = 50, *, tenant_id: str | None = None, since: str | None = None) -> list[dict[str, Any]]:
        where, args = self._where(tenant_id, None, since)
        rows = self.db.all(f"SELECT * FROM model_usage{where} ORDER BY id DESC LIMIT ?", args + [max(1, int(limit))])
        out = rows_to_dicts(rows)
        for d in out:
            d["ok"] = bool(d.get("ok"))
        return out


class MemoryUsageLedger:
    """In-process ledger for tests and for a router built without a coordination DB."""

    def __init__(self) -> None:
        self.calls: list[ModelCall] = []

    async def record(self, call: ModelCall) -> None:
        self.calls.append(call)

    def totals(self, tenant_id: str | None = None, *, goal_id: str | None = None, since: str | None = None) -> dict[str, Any]:
        rows = [c for c in self.calls if (tenant_id is None or c.tenant_id == tenant_id) and (goal_id is None or c.goal_id == goal_id)]
        out: dict[str, Any] = {"calls": len(rows), "input_tokens": sum(c.input_tokens for c in rows),
                               "output_tokens": sum(c.output_tokens for c in rows), "cost_usd": round(sum(c.cost_usd for c in rows), 8),
                               "failures": sum(1 for c in rows if not c.ok), "by_tier": {}, "by_model": {}}
        out["tokens"] = out["input_tokens"] + out["output_tokens"]
        for c in rows:
            for key, k in (("by_tier", c.tier), ("by_model", c.model)):
                b = out[key].setdefault(k, _tier_bucket())
                b["calls"] += 1
                b["tokens"] += c.input_tokens + c.output_tokens
                b["cost_usd"] = round(b["cost_usd"] + c.cost_usd, 8)
        return out

    def recent(self, limit: int = 50, *, tenant_id: str | None = None, since: str | None = None) -> list[dict[str, Any]]:
        rows = [c for c in reversed(self.calls) if tenant_id is None or c.tenant_id == tenant_id]
        return [dict(c.__dict__) for c in rows[: max(1, int(limit))]]

"""The usage ledger: one JSON line per logical model attempt, holding numbers and labels, never text.

Ported (adapted, not merged) from origin/claude/mycelic-implementation-vr034p@388aa30, ``mycelic/models/ledger.py``:
kept the idea that every attempt is recorded with tokens, latency, cost and outcome, and that prices are
configuration. Dropped from the port: the built-in price table and its environment overrides, "an unknown model
costs 0" (an endpoint without a configured price is recorded as ``unpriced`` with a null cost), missing token
counts recorded as 0 (they are null), the ``error`` column that held ``str(exc)`` (replaced by a fixed
``error_kind``), the SQLite table, tenant/goal/question columns and the async API.

Row keys (exactly :data:`LEDGER_KEYS`; ``append`` refuses anything else):

* identity: ``ts`` (from the runtime's injected clock), ``run_id``, ``task``, ``ref`` (an opaque caller handle,
  never a record id), ``attempt`` (0 refused before any I/O, 1 primary, 2 repair, 3 escalation), ``escalated_from``;
* where: ``endpoint``, ``provider``, ``model_requested``, ``model_served``, ``host`` (``host:port`` or
  ``in-process``), ``proxy`` (true when the request went through the environment's proxy, which only an endpoint
  that may use one does: ``Endpoint.uses_env_proxy``; audit round 3), ``boundary`` (the runtime's),
  ``endpoint_boundary``, ``boundary_mode`` (own | simulated | external_raw_exempt | structured_egress | refused),
  ``data_label`` (synthetic | public | partner);
* outcome: ``ok``, ``error_kind``, ``http_status``, ``finish_reason``, ``transport_retries``;
* usage: ``tokens_in``, ``tokens_out`` (null when the server did not report them), ``latency_ms``, ``ttft_ms``,
  ``cost_usd``, ``cost_basis`` (per_token | per_hour | unpriced | fake), ``fake_marker``.

A site's ledger stays at the site. :func:`usage_summary` (``summarise`` over a whole file) reduces it to per
(task, endpoint) counts, token sums and latency percentiles, without ``ts``, ``ref``, ``host``, ``run_id`` or model
names. What actually crosses a site boundary is ``edge.site``'s windowed, k-suppressed form of :func:`summarise`
over the ledger rows of closed weeks (G3).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from ..jsonio import canonical_dumps, strict_load
from ..stats import percentile

if TYPE_CHECKING:
    from .routing import Endpoint

LEDGER_KEYS = frozenset({
    "ts", "run_id", "task", "ref", "endpoint", "provider", "model_requested", "model_served", "host", "proxy",
    "boundary", "endpoint_boundary", "boundary_mode", "data_label", "attempt", "escalated_from", "ok", "error_kind",
    "http_status", "finish_reason", "transport_retries", "tokens_in", "tokens_out", "latency_ms", "ttft_ms",
    "cost_usd", "cost_basis", "fake_marker",
})
BOUNDARY_MODES = ("own", "simulated", "external_raw_exempt", "structured_egress", "refused")
COST_BASES = ("per_token", "per_hour", "unpriced", "fake")


def cost(endpoint: "Endpoint", tokens_in: int | None, tokens_out: int | None,
         latency_ms: float | None) -> tuple[float | None, str]:
    if endpoint.provider == "fake":
        return None, "fake"
    price = endpoint.price
    if price is None:
        return None, "unpriced"
    if price.per_mtok_in is not None and price.per_mtok_out is not None:
        if tokens_in is None or tokens_out is None:
            return None, "per_token"
        return round((tokens_in * price.per_mtok_in + tokens_out * price.per_mtok_out) / 1e6, 10), "per_token"
    if latency_ms is None:
        return None, "per_hour"
    return round(price.usd_per_hour * latency_ms / 3.6e6, 10), "per_hour"


class UsageLedger:
    """Append-only JSONL file; one ``write`` per row under a lock, so concurrent rows never interleave."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def append(self, row: dict[str, Any]) -> None:
        keys = set(row)
        if keys != LEDGER_KEYS:
            missing, extra = sorted(LEDGER_KEYS - keys), sorted(keys - LEDGER_KEYS)
            raise ValueError(f"ledger row keys differ: missing {missing}, unexpected {extra}") from None
        line = canonical_dumps(row) + "\n"
        with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()


def read_ledger(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(Path(path).read_bytes().split(b"\n"), start=1):
        if not line:
            continue
        row = strict_load(line)
        if not isinstance(row, dict) or set(row) != LEDGER_KEYS:
            raise ValueError(f"ledger line {number} does not have the {len(LEDGER_KEYS)} ledger keys") from None
        rows.append(row)
    return rows


def usage_summary(path: str | Path) -> dict[str, Any]:
    return summarise(read_ledger(path))


def summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The usage summary of ledger rows: per (task, endpoint) counts, token sums and latency percentiles."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["task"], row["endpoint"]), []).append(row)
    out = []
    for (task, endpoint), rows in sorted(groups.items()):
        errors: dict[str, int] = {}
        for r in rows:
            if not r["ok"] and r["error_kind"]:
                errors[r["error_kind"]] = errors.get(r["error_kind"], 0) + 1
        latencies = [r["latency_ms"] for r in rows if r["attempt"] >= 1 and r["latency_ms"] is not None]
        out.append({
            "task": task, "endpoint": endpoint, "calls": len(rows), "ok": sum(1 for r in rows if r["ok"]),
            "errors": errors,
            "tokens_in": sum(r["tokens_in"] for r in rows if r["tokens_in"] is not None),
            "tokens_out": sum(r["tokens_out"] for r in rows if r["tokens_out"] is not None),
            "tokens_in_missing": sum(1 for r in rows if r["tokens_in"] is None),
            "tokens_out_missing": sum(1 for r in rows if r["tokens_out"] is None),
            "latency_ms_p50": percentile(latencies, 50), "latency_ms_p95": percentile(latencies, 95),
            "fake": any(r["fake_marker"] for r in rows),
        })
    return {"kind": "usage_summary", "schema_version": 1, "groups": out}

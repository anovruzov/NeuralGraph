"""The fabric: agents' memory servers as peers of one another.

A ``Fabric`` holds peers (``InProcessPeer`` for deterministic runs,
``McpPeerAdapter`` for real processes over stdio), the coordinator, a
reversible failure injector, a trace log and the hash-chained memory bus.
It offers two things no single agent's memory can:

- **Collective recall.**  Ask for keyed facts across peers.  The coordinator
  routes (bus hints first), collects policy-filtered claims, drops re-delivered
  duplicates, composes one value per key, scores fragility (how many failure
  domains must fail before the answer is lost), and repairs through the
  lineage-aware verification handshake when a key is missing.  Without keys it
  is federated recall: every peer's hits, merged and deduplicated across peers,
  with producers, roots and domains on each.
- **Forecast.**  Before anything fails: for every peer and every declared
  failure domain, mask it at the orchestrator (peers' stores untouched), run
  the same recall, and report which keys the collective would lose.  This is
  the game's pre-mortem oracle over real memories.

No peer sees another peer's store.  Everything that moves is a contract from
``coordination/contracts.py``; private memories cross as payload-free
denials.  Nothing here modifies the graph engine.

Deterministic demo (pinned in ``NeuralGraph/mcp/artifacts/``)::

    python3.11 -m NeuralGraph.mcp.fabric --demo --output NeuralGraph/mcp/artifacts/fabric_demo.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..coordination.bus import MemoryBus
from ..coordination.contracts import (
    AuthorizationContext,
    InterventionTarget,
    QueryBudget,
    QueryRequest,
)
from ..coordination.core import (
    CapabilityRegistry,
    ClaimNormalizer,
    FailureInjector,
    LineageAnalyzer,
    LogicalClock,
    QueryExecution,
    RepairPlanner,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
    TraceLogger,
    to_jsonable,
)
from ..coordination.mcp_peer import InProcessPeer, McpPeerAdapter
from .memory import HashedEmbedder, MemoryEngine, fingerprint

CAPABILITY_ID = "memory"
SCOPE = "memory:read"


class Fabric:
    def __init__(self, run_id: str = "fabric", seed: int = 20260914, bus_path: str | Path | None = None) -> None:
        self.clock = LogicalClock(seed)
        self.traces = TraceLogger(run_id, self.clock)
        self.failure = FailureInjector(run_id, seed, self.clock, self.traces)
        self.registry = CapabilityRegistry()
        self._keys: dict[str, tuple[str, ...]] = {}
        self._bus_path = Path(bus_path) if bus_path else None
        self.bus = MemoryBus.load(self._bus_path, self.clock.now) if self._bus_path and self._bus_path.exists() else MemoryBus(self.clock.now)
        self._peers: list[Any] = []
        self._seq = 0

    # ----- peers ---------------------------------------------------------
    def add_in_process(self, engine: MemoryEngine) -> InProcessPeer:
        peer = InProcessPeer(engine, self.failure, self.clock.now, self._keys.get)
        self.registry.register(peer); self._peers.append(peer); return peer

    def add_mcp(self, node_id: str, command: str, args: list[str], env: dict[str, str] | None = None, timeout_seconds: float = 20.0) -> McpPeerAdapter:
        peer = McpPeerAdapter(node_id, command, args, env, self.failure, self.clock.now, timeout_seconds, self._keys.get)
        self.registry.register(peer); self._peers.append(peer); return peer

    @property
    def node_ids(self) -> tuple[str, ...]:
        return self.registry.node_ids()

    async def close(self) -> None:
        for peer in self._peers:
            if hasattr(peer, "close"):
                await peer.close()
        if self._bus_path:
            self.bus.dump(self._bus_path)

    # ----- requests ------------------------------------------------------
    def _request(self, query: str, keys: tuple[str, ...], limit: int, max_nodes: int | None, max_verifications: int) -> QueryRequest:
        self._seq += 1
        digest = hashlib.sha256(f"{self._seq}|{query}|{','.join(keys)}".encode("utf-8")).hexdigest()[:16]
        request = QueryRequest(
            query_id=f"q_{digest}", content=query, requester_id="fabric", issued_at=self.clock.now(),
            authorization=AuthorizationContext(scopes=(SCOPE,)), requested_capability=CAPABILITY_ID,
            budget=QueryBudget(max_nodes=max_nodes or max(1, len(self._peers)), max_claims=max(1, limit) * max(1, len(self._peers)), max_verifications=max_verifications),
        )
        self._keys[request.query_id] = keys
        return request

    def _coordinator(self, slots: tuple[str, ...]) -> TesseractCoordinator:
        return TesseractCoordinator(self.registry, Router(self.registry, self.failure), ClaimNormalizer(), RuleBasedSynthesizer(slots), LineageAnalyzer(), self.traces)

    # ----- collective recall --------------------------------------------
    async def collective_recall(self, query: str, keys: tuple[str, ...] | list[str] = (), limit: int = 8,
                                max_nodes: int | None = None, repair: bool = True, record: bool = True) -> dict[str, Any]:
        keys = tuple(k.strip() for k in keys if k and k.strip())
        request = self._request(query, keys, limit, max_nodes, 2)
        slots = keys if keys else ("memory",)
        coordinator = self._coordinator(slots)
        hint = self.bus.route_hint(request)
        execution = await coordinator.execute(request, "collective_recall", preferred_node_ids=hint or None)
        repair_info: dict[str, Any] | None = None
        if keys and not execution.synthesis.success and repair:
            missing = tuple(k for k in keys if k not in execution.synthesis.covered_slots)
            compromised_roots = tuple(sorted({r for e in execution.exports for c in e.claims for r in c.lineage_root_ids if self.failure.is_active(InterventionTarget.LINEAGE_ROOT, r)}))
            candidates = tuple(n for n in self.node_ids if n not in execution.route)
            decision = await RepairPlanner(self.registry, self.traces).plan(request, missing, candidates, compromised_roots, ())
            repair_info = to_jsonable(decision)
            if decision.selected_node_id:
                preferred = (decision.selected_node_id,) + tuple(n for n in execution.route if n != decision.selected_node_id)
                execution = await coordinator.execute(request, "collective_recall_repaired", preferred_node_ids=preferred)
        entry = self.bus.append(request, execution) if record else None
        return self._report(request, keys, execution, repair_info, entry)

    def _report(self, request: QueryRequest, keys: tuple[str, ...], execution: QueryExecution, repair_info: dict[str, Any] | None, entry: Any) -> dict[str, Any]:
        denials = [e.trace for e in execution.exports if not e.claims]
        report: dict[str, Any] = {
            "query_id": request.query_id, "route": list(execution.route),
            "claims": len(execution.claims), "duplicates_dropped": execution.duplicate_claim_count,
            "denied_exports": len(denials), "transport_failures": sum(1 for t in denials if t.retrieval_operator == "transport_failure"),
            "metrics": to_jsonable(execution.metrics), "bus_entry": {"seq": entry.seq, "hash": entry.hash, "peers": list(entry.peer_entry_ids)} if entry else None,
            "repair": repair_info,
        }
        if keys:
            answer = {}
            for claim in execution.claims:
                if claim.claim_id in execution.synthesis.selected_claim_ids:
                    answer[claim.content["slot"]] = {"value": claim.content["value"], "producer": claim.producer_node_id, "confidence": claim.confidence,
                                                     "lineage_root_ids": list(claim.lineage_root_ids), "failure_domains": list(claim.failure_domains)}
            report.update({"keys": list(keys), "complete": execution.synthesis.success, "covered": list(execution.synthesis.covered_slots),
                           "missing": [k for k in keys if k not in execution.synthesis.covered_slots], "answer": answer,
                           "support_by_key": {k: sorted({c.producer_node_id for c in execution.claims if c.content.get("slot") == k}) for k in keys}})
        else:
            merged: dict[str, dict[str, Any]] = {}
            for claim in sorted(execution.claims, key=lambda c: (-c.confidence, c.producer_node_id, c.claim_id)):
                fp = " ".join(fingerprint(str(claim.content.get("value", ""))))
                item = merged.setdefault(fp, {"value": claim.content.get("value"), "kind": claim.content.get("kind"), "confidence": claim.confidence, "producers": [], "lineage_root_ids": [], "failure_domains": []})
                item["producers"].append(claim.producer_node_id)
                item["lineage_root_ids"].extend(r for r in claim.lineage_root_ids if r not in item["lineage_root_ids"])
                item["failure_domains"].extend(d for d in claim.failure_domains if d not in item["failure_domains"])
                item["confidence"] = max(item["confidence"], claim.confidence)
            hits = sorted(merged.values(), key=lambda i: (-i["confidence"], -len(i["producers"])))
            for h in hits:
                h["replicas"] = len(h["producers"]); h["independent_domains"] = len(set(h["failure_domains"]))
            report["hits"] = hits
        return report

    # ----- forecast ------------------------------------------------------
    async def forecast(self, query: str, keys: tuple[str, ...] | list[str], limit: int = 8, max_nodes: int | None = None) -> dict[str, Any]:
        keys = tuple(k.strip() for k in keys if k and k.strip())
        baseline = await self.collective_recall(query, keys, limit, max_nodes, repair=True, record=False)
        roots_by_domain: dict[str, set[str]] = {}
        request = self._request(query, keys, limit, max_nodes, 0)
        coordinator = self._coordinator(keys)
        base_exec = await coordinator.execute(request, "forecast_baseline")
        for e in base_exec.exports:
            for c in e.claims:
                for d in c.failure_domains:
                    roots_by_domain.setdefault(d, set()).update(c.lineage_root_ids)

        async def masked(target: InterventionTarget, ids: tuple[str, ...], label: str) -> dict[str, Any]:
            for i in ids:
                self.failure.apply(target, i, reason=f"forecast: {label}", expected_effect="counterfactual, cleared after the probe")
            try:
                probe = await self.collective_recall(query, keys, limit, max_nodes, repair=True, record=False)
            finally:
                for i in ids:
                    self.failure.clear(target, i)
            return {"complete": probe["complete"], "lost": probe["missing"], "repaired": bool(probe["repair"] and probe["repair"].get("selected_node_id"))}

        by_node = {n: await masked(InterventionTarget.ROUTE, (n,), f"node {n}") for n in self.node_ids}
        by_domain = {d: await masked(InterventionTarget.LINEAGE_ROOT, tuple(sorted(r)), f"domain {d}") for d, r in sorted(roots_by_domain.items())}
        return {
            "keys": list(keys), "baseline_complete": baseline["complete"], "baseline_missing": baseline["missing"],
            "minimal_failure_domain_cut": baseline["metrics"]["minimal_failure_domain_cut"],
            "single_node_failures_that_forget": sorted(n for n, r in by_node.items() if not r["complete"]),
            "single_domain_failures_that_forget": sorted(d for d, r in by_domain.items() if not r["complete"]),
            "by_node": by_node, "by_domain": by_domain,
        }


# ---------------------------------------------------------------- demo
class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def _ids(prefix: str):
    n = 0

    def make() -> str:
        nonlocal n
        n += 1
        return f"{prefix}-{n:03d}"
    return make


async def run_demo(tmp: Path) -> dict[str, Any]:
    """Three agents, three domains: what the collective knows and what it would forget."""
    clock = _Clock()
    fabric = Fabric(run_id="fabric_demo", seed=20260914)
    engines = {}
    for name, domain in (("agent-a", "laptop-a"), ("agent-b", "office-b"), ("agent-c", "office-b"), ("agent-d", "home-d")):
        engines[name] = MemoryEngine(tmp / f"{name}.db", session_key=name, clock=clock, embedder=HashedEmbedder(), failure_domain=domain, id_factory=_ids(name))
    a, b, c, d = (engines[n] for n in ("agent-a", "agent-b", "agent-c", "agent-d"))
    await a.remember("The staging database is Postgres 16 on Railway.", kind="fact", key="env.staging_db")
    await a.remember("Ali uses Zed as the editor.", kind="preference", key="user.editor")
    await a.remember("Staging API token lives in the vault at secrets/staging.", kind="fact", key="env.staging_token", private=True)
    await b.remember("Standup is at 10:00 Berlin time.", kind="fact", key="team.standup")
    await b.remember("The staging database is Postgres 16 on Railway.", kind="fact", key="env.staging_db")
    await c.remember("Standup is at 10:00 Berlin time.", kind="fact", key="team.standup")  # replica in the same domain as b
    await d.remember("Standup is at 10:00 Berlin time.", kind="fact", key="team.standup")  # independent domain
    await d.remember("Deploys run from GitHub Actions on push to main.", kind="convention")
    for e in engines.values():
        fabric.add_in_process(e)
    keys = ("env.staging_db", "user.editor", "team.standup")
    collective = await fabric.collective_recall("what does the team know about the environment", keys)
    federated = await fabric.collective_recall("staging database", ())
    forecast = await fabric.forecast("what does the team know about the environment", keys)
    # a private keyed fact is asked for: it crosses as a payload-free denial and stays missing
    private_probe = await fabric.collective_recall("staging token", ("env.staging_token",))
    # a peer dies: repair reaches an independent holder
    fabric.failure.apply(InterventionTarget.ROUTE, "neuralgraph:agent-b", reason="demo: agent-b offline")
    after_b = await fabric.collective_recall("what does the team know about the environment", keys, max_nodes=2)
    fabric.failure.clear(InterventionTarget.ROUTE, "neuralgraph:agent-b")
    bus_ok = fabric.bus.verify() == 0
    private_leaked = any("secrets/staging" in json.dumps(x) for x in (collective, federated, forecast, after_b, private_probe, to_jsonable(fabric.traces.events), fabric.bus.to_jsonable()))
    for e in engines.values():
        e.close()
    return {
        "schema_version": "1.0", "peers": list(fabric.node_ids), "keys": list(keys),
        "collective": collective, "federated": federated, "forecast": forecast, "after_agent_b_offline": after_b, "private_probe": private_probe,
        "bus_intact": bus_ok, "bus_entries": len(fabric.bus.entries), "private_memory_leaked": private_leaked,
        "trace_events": len(fabric.traces.events),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.demo:
        parser.error("only --demo is available from the command line; use the fabric server for live peers")
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(run_demo(Path(tmp)))
    payload = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared fixtures for the Mycelic tests: an in-process service with a temporary database."""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mycelic.aggregation import CONSOLIDATABLE, contributing_agents, lineage_roots
from mycelic.auth import Principal
from mycelic.config import Settings
from mycelic.hierarchy import ancestors, child_unit_of, unit_at_layer
from mycelic.metrics import Metrics
from mycelic.service import MycelicService
from mycelic.store import MycelicStore
from mycelic.transport import InProcessTransport, Transport

ADMIN_TOKEN = "test-admin-token-0123456789abcdef0123456789"

DEMO_RULE = {
    "rule_id": "component_supply_risk",
    "target_layer": "enterprise",
    "required_slots": ["transport_disruption", "supplier_buffer_low", "demand_commitment"],
    "conclusion": ("Supply risk for {entity}: inbound transport is disrupted ({slot:transport_disruption}); "
                   "the supplier buffer is thin ({slot:supplier_buffer_low}); demand is committed ({slot:demand_commitment})."),
    "topic_prefix": "supply:",
    "min_agents": 3,
    "min_teams": 2,
    "kind": "risk",
}


#: the schema-2 DDL exactly as released (before log-applied state and label normalisation), for migration tests
V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orgs (
    org_id     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id     TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES orgs(org_id),
    display_name TEXT NOT NULL,
    path         TEXT NOT NULL UNIQUE,
    scopes       TEXT NOT NULL DEFAULT '[]',
    key_hash     TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agents_org ON agents(org_id, status);

CREATE TABLE IF NOT EXISTS memories (
    rid               INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id         TEXT NOT NULL UNIQUE,
    org_id            TEXT NOT NULL,
    layer             TEXT NOT NULL,
    scope             TEXT NOT NULL,
    text              TEXT NOT NULL,
    topic             TEXT,
    slot              TEXT,
    entity            TEXT,
    kind              TEXT NOT NULL,
    confidence        REAL NOT NULL,
    support           INTEGER NOT NULL DEFAULT 1,
    independent_teams INTEGER NOT NULL DEFAULT 1,
    producer_id       TEXT NOT NULL,
    operator          TEXT NOT NULL,
    rule_id           TEXT,
    agg_key           TEXT,
    event_id          TEXT,
    visibility        TEXT NOT NULL DEFAULT 'team',
    status            TEXT NOT NULL DEFAULT 'active',
    superseded_by     TEXT,
    created_at        TEXT NOT NULL,
    applied_at        TEXT,
    source_event_ids  TEXT NOT NULL DEFAULT '[]',
    local_ref         TEXT,
    metadata          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memories_org_status ON memories(org_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(org_id, scope);
CREATE INDEX IF NOT EXISTS idx_memories_topic ON memories(org_id, topic, status);
CREATE INDEX IF NOT EXISTS idx_memories_entity ON memories(org_id, entity, status);
CREATE INDEX IF NOT EXISTS idx_memories_agg ON memories(org_id, operator, scope, agg_key, status);
-- at most one *active* derived memory per (unit, operator, key): supersession is the only way to replace it
CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_active_agg ON memories(org_id, operator, scope, agg_key)
    WHERE status='active' AND agg_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS lineage_edges (
    child_id       TEXT NOT NULL,
    parent_id      TEXT NOT NULL,
    contributed_by TEXT NOT NULL,
    parent_layer   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (child_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_lineage_parent ON lineage_edges(parent_id);

CREATE TABLE IF NOT EXISTS events (
    rid          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    org_id       TEXT NOT NULL,
    agent_id     TEXT,
    subject      TEXT NOT NULL,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    js_seq       INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    published_at TEXT,
    applied_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status, rid);
CREATE INDEX IF NOT EXISTS idx_events_org ON events(org_id, kind, rid);

CREATE TABLE IF NOT EXISTS rules (
    rule_id        TEXT PRIMARY KEY,
    org_id         TEXT,
    target_layer   TEXT NOT NULL,
    required_slots TEXT NOT NULL,
    conclusion     TEXT NOT NULL,
    topic_prefix   TEXT,
    min_agents     INTEGER NOT NULL DEFAULT 2,
    min_teams      INTEGER NOT NULL DEFAULT 1,
    kind           TEXT NOT NULL DEFAULT 'risk',
    enabled        INTEGER NOT NULL DEFAULT 1,
    metadata       TEXT NOT NULL DEFAULT '{}',
    updated_at     TEXT NOT NULL,
    sources        TEXT NOT NULL DEFAULT '["agent_observation"]',
    emits_slot     TEXT,
    emits_topic    TEXT,
    min_units      TEXT NOT NULL DEFAULT '{}',
    corroborate    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    principal TEXT NOT NULL,
    action    TEXT NOT NULL,
    target    TEXT,
    detail    TEXT NOT NULL DEFAULT '{}',
    remote    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
INSERT INTO meta(key, value) VALUES ('schema_version', '2');
"""


@dataclass
class HeldTransport(InProcessTransport):
    """An in-process log whose consumer receives nothing while ``hold`` is set (from construction): publishing
    still appends to the log, so tests can build up consumer lag and release it.  A fetch that was already waiting
    when ``hold`` was set gives what it received back to the log, undelivered."""

    hold: bool = True

    async def fetch(self, batch: int, timeout: float) -> list:
        if not self.hold:
            out = await super().fetch(batch, timeout)
            if not self.hold:
                return out
            for d in reversed(out):            # held while this fetch was waiting: back to the log, undelivered
                self._inflight.pop(d.seq, None)
                self._delivered[d.seq] -= 1
                self._redeliver.insert(0, d.seq)
        await asyncio.sleep(0.05)
        return []


def settings(tmp: str | Path, **overrides: Any) -> Settings:
    base = dict(host="127.0.0.1", port=0, db_path=str(Path(tmp) / "mycelic.db"), nats_url=None,
                admin_token=ADMIN_TOKEN, rate_limit_rps=0.0, min_support=2, publish_interval_seconds=0.05)
    base.update(overrides)
    s = Settings(**base)
    s.validate()
    return s


class ServiceHarness:
    """One service over the in-process transport, plus helpers to register agents and act as them."""

    def __init__(self, *, transport: Transport | None = None, **overrides: Any) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings(self.tmp.name, **overrides)
        self.service = MycelicService(self.settings, transport=transport or InProcessTransport(), metrics=Metrics())
        self.keys: dict[str, str] = {}

    async def start(self) -> "ServiceHarness":
        await self.service.start()
        return self

    async def close(self) -> None:
        await self.service.close()
        self.tmp.cleanup()

    async def register(self, agent_id: str, *, team: str, department: str = "ops", subsidiary: str = "nw-gmbh",
                       region: str = "emea", enterprise: str = "northwind", scopes: list[str] | None = None) -> Principal:
        body = {"enterprise": enterprise, "region": region, "subsidiary": subsidiary, "department": department,
                "team": team, "agent_id": agent_id}
        if scopes is not None:
            body["scopes"] = scopes
        agent, key = await self.service.register_agent(body)
        self.keys[agent_id] = key
        return self.principal(agent_id)

    def principal(self, agent_id: str) -> Principal:
        return self.service.authenticate(f"Bearer {self.keys[agent_id]}")

    @property
    def admin(self) -> Principal:
        return self.service.authenticate(f"Bearer {ADMIN_TOKEN}")

    async def observe(self, agent_id: str, text: str, **fields: Any) -> str:
        m, _ = await self.service.ingest_memory(self.principal(agent_id), {"text": text, **fields})
        return m.memory_id

    async def settle(self, timeout: float = 10.0) -> None:
        assert await self.service.wait_idle(timeout), "service did not become idle"


async def drain_outbox(service: MycelicService, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while service.store.stats()["outbox_pending"]:
        assert asyncio.get_running_loop().time() < deadline, "outbox did not drain"
        await asyncio.sleep(0.02)


def broken_chains(store: MycelicStore, org_id: str) -> list[str]:
    """Active memories that rest, directly or anywhere below, on a memory that is not active."""
    out = []
    for m in store.list_memories(org_id, status="active", limit=100_000):
        stack, seen = [m.memory_id], set()
        while stack:
            for e in store.parents_of(stack.pop()):
                if e.parent_id not in seen:
                    seen.add(e.parent_id)
                    parent = store.get_memory(e.parent_id)
                    if parent is None or parent.status != "active":
                        out.append(f"{m.memory_id} rests on {e.parent_id} ({parent.status if parent else 'missing'})")
                    stack.append(e.parent_id)
    return out


async def step(service: MycelicService, transport: InProcessTransport) -> dict[str, Any]:
    """Deliver and apply exactly the next event of the log, whatever ``hold`` says."""
    [d] = await InProcessTransport.fetch(transport, 1, 1.0)
    await service._handle_delivery(d)
    return json.loads(d.data)


# ---------------------------------------------------------------------- an inline log, its rebuild, and invariants
async def pump(service: MycelicService, log: list[dict[str, Any]]) -> None:
    """Publish and apply every pending event in outbox order, appending each one's wire form to ``log`` (sequence =
    position in ``log``): the consumer of a service that was never started, so nothing runs in the background."""
    while True:
        rows = service.store.pending_events(1000)
        if not rows:
            return
        for ev in rows:
            seq = len(log) + 1
            async with service.store.transaction() as tx:
                tx.mark_published(ev.event_id, seq)
            log.append(json.loads(json.dumps(service._event_wire(ev))))
            await service.apply_event(log[-1], seq=seq)


async def rebuild(log: list[dict[str, Any]], tmp: str | Path, **overrides: Any) -> MycelicService:
    """A fresh in-memory node that applied ``log`` in order, as a replay does (derived events published, never queued)."""
    s = MycelicService(settings(tmp, **overrides), store=MycelicStore(":memory:"), transport=InProcessTransport(), metrics=Metrics())
    s._replay_target = len(log)
    for seq, event in enumerate(log, start=1):
        await s.apply_event(event, seq=seq)
    s._replay_target = None
    return s


def memory_history(store: MycelicStore) -> dict[str, tuple]:
    """Every row's layer, status, support, version link, text and what it quotes (a rebuild reproduces all of it)."""
    out = {}
    for r in store._conn.execute("SELECT memory_id, layer, status, support, text, metadata FROM memories"):
        meta = json.loads(r["metadata"])
        out[r["memory_id"]] = (r["layer"], r["status"], r["support"], meta.get("version_of"), r["text"],
                               json.dumps(meta.get("statements")), json.dumps(meta.get("statement_origins")),
                               meta.get("private_observations"))
    return out


def lineage_edge_set(store: MycelicStore) -> set[tuple[str, str]]:
    return {(r["child_id"], r["parent_id"]) for r in store._conn.execute("SELECT child_id, parent_id FROM lineage_edges")}


def applied_rule_rows(store: MycelicStore) -> list[tuple]:
    return [tuple(r) for r in store._conn.execute("SELECT rule_id, org_id, applied_seq, snapshot FROM applied_rules ORDER BY rule_id")]


def table_dump(store: MycelicStore, table: str) -> list[tuple]:
    return [tuple(r) for r in store._conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def invariant_violations(service: MycelicService, org_id: str) -> list[str]:
    """I1-I6 over one organization's state as applied (see ``test_aggregation_invariants``).  Every key that should be
    evaluated is enumerated here from the memories themselves, not with the aggregator's own enumeration helpers."""
    st, agg, ms = service.store, service.aggregator, service.aggregator.min_support
    bad: list[str] = []
    rows = st.list_memories(org_id, status=None, limit=1_000_000)
    by_id = {m.memory_id: m for m in rows}
    active = [m for m in rows if m.status == "active"]
    derived = [m for m in active if m.operator != "agent_observation"]
    # I1: one active memory per (unit, operator, key)
    for r in st._conn.execute("""SELECT operator, scope, COALESCE(agg_key, '') AS k, COUNT(*) AS n FROM memories
                                 WHERE org_id=? AND status='active' AND operator!='agent_observation'
                                 GROUP BY operator, scope, k HAVING n > 1""", (org_id,)):
        bad.append(f"I1 {r['n']} active for {r['operator']} {r['scope']} {r['k']}")
    for m in derived:
        parents = [by_id.get(e.parent_id) for e in st.parents_of(m.memory_id)]
        # I2: an active derived memory rests on existing active memories only
        if not parents or any(p is None or p.status != "active" for p in parents):
            bad.append(f"I2 {m.memory_id} ({m.operator} {m.scope}) parents {[p.status if p else 'missing' for p in parents]}")
            continue
        # I3: a consolidation stands for enough direct children of its unit, on its own topic
        if m.operator == "topic_consolidation":
            effective = 1 if m.metadata.get("promoted_from") else ms
            inside = all(p.scope.startswith(m.scope + "/") and p.topic == m.topic for p in parents)
            children = sorted({child_unit_of(p.scope, m.scope) for p in parents}) if inside else []
            if (not inside or len(children) < effective or children != m.metadata.get("children")
                    or m.metadata.get("effective_min_support") != effective):
                bad.append(f"I3 {m.memory_id} at {m.scope} on {m.topic}: children {children} (meta {m.metadata.get('children')}), "
                           f"effective {effective}, parents inside {inside}")
        # I4: support and teams are those of the roots, and the roots are the parents' roots
        roots = m.metadata.get("roots") or []
        root_mems = [by_id.get(r) for r in roots]
        if (any(r is None for r in root_mems) or set(roots) != {r for p in parents for r in lineage_roots(p)}
                or m.support != len({a for r in root_mems for a in contributing_agents(r)})
                or m.independent_teams != len({unit_at_layer(r.scope, "team") for r in root_mems})):
            bad.append(f"I4 {m.memory_id} support {m.support}/{m.independent_teams} roots {len(roots)}")
        # I5 soundness: re-planning reproduces it, under an applied, enabled rule covering its org and layer
        if m.operator == "slot_composition" and agg.rule_for(m) is None:
            bad.append(f"I5 {m.memory_id} rests on rule {m.rule_id} that no longer applies to it")
            continue
        plan = agg.plan_for(m)
        if plan is None or plan.memory is None or plan.memory.memory_id != m.memory_id:
            bad.append(f"I5 {m.memory_id} ({m.operator} {m.scope} {m.metadata.get('agg_key')}) re-plans to "
                       f"{None if plan is None or plan.memory is None else plan.memory.memory_id}")
    # I5 completeness: wherever an operator holds there is a memory with its id, and nowhere else
    evidence = [m for m in st.list_memories(org_id, status="active", latest=True, limit=1_000_000)]
    topic_keys = sorted({(unit, m.topic) for m in evidence if m.topic and m.operator in CONSOLIDATABLE
                         for unit in ancestors(m.scope, include_self=False)})
    for unit, topic in topic_keys:
        plan = agg.plan_consolidation(org_id, unit, topic)
        current = st.current_derived(org_id, "topic_consolidation", unit, topic)
        if (plan.memory is None) != (current is None) or (current is not None and plan.memory.memory_id != current.memory_id):
            bad.append(f"I5c consolidation {unit} {topic}: plan {plan.memory.memory_id if plan.memory else None} "
                       f"current {current.memory_id if current else None}")
    for rule in st.list_applied_rules(org_id):
        keys = {(unit_at_layer(m.scope, rule.target_layer), m.entity) for m in evidence
                if m.operator in rule.sources and m.slot in rule.required_slots
                and (not rule.topic_prefix or (m.topic or "").startswith(rule.topic_prefix))}
        for unit, entity in sorted((k for k in keys if k[0]), key=lambda k: (k[0], k[1] is not None, k[1] or "")):
            plan = agg.plan_rule(rule, org_id, unit, entity)
            current = st.current_derived(org_id, "slot_composition", unit, f"{rule.rule_id}:{entity or '*'}")
            if (plan.memory is None) != (current is None) or (current is not None and plan.memory.memory_id != current.memory_id):
                bad.append(f"I5c rule {rule.rule_id} {unit} {entity}: plan {plan.memory.memory_id if plan.memory else None} "
                           f"current {current.memory_id if current else None}")
    # I6: with min_support 1 every shared topical note reaches the consolidation of every unit above it
    if ms == 1:
        for m in evidence:
            if m.operator == "agent_observation" and m.topic:
                for unit in ancestors(m.scope, include_self=False):
                    current = st.current_derived(org_id, "topic_consolidation", unit, m.topic)
                    if current is None or m.memory_id not in current.metadata.get("roots", []):
                        bad.append(f"I6 {m.memory_id} on {m.topic} does not reach {unit}")
    return bad


def rebuild_differences(live: MycelicService, rebuilt: MycelicService) -> list[str]:
    """I7: what a rebuild of the log does not reproduce (memories, lineage edges, applied rules)."""
    out = []
    a, b = memory_history(live.store), memory_history(rebuilt.store)
    for mid in sorted(set(a) | set(b)):
        if a.get(mid) != b.get(mid):
            out.append(f"I7 {mid}: live {a.get(mid)} rebuilt {b.get(mid)}")
    if lineage_edge_set(live.store) != lineage_edge_set(rebuilt.store):
        out.append(f"I7 lineage edges differ: {sorted(lineage_edge_set(live.store) ^ lineage_edge_set(rebuilt.store))[:5]}")
    if applied_rule_rows(live.store) != applied_rule_rows(rebuilt.store):
        out.append("I7 applied_rules differ")
    return out


async def full_reaggregation_pass(service: MycelicService) -> int:
    """Run every step of ``reaggregate_step`` over every organization inline; the number of memories it changed."""
    changed = 0
    for org in service.store.memory_org_ids():
        cursor: dict[str, Any] | None = None
        while True:
            async with service.store.transaction() as tx:
                derivations, n, cursor = service.aggregator.reaggregate_step(tx, org, cursor, limit=service.reaggregate_batch)
                service._emit_derived(tx, derivations, "2026-10-07T00:00:00+00:00")
            changed += n
            if cursor is None:
                break
    return changed

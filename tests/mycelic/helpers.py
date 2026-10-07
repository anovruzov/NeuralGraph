"""Shared fixtures for the Mycelic tests: an in-process service with a temporary database."""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mycelic.auth import Principal
from mycelic.config import Settings
from mycelic.metrics import Metrics
from mycelic.service import MycelicService
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

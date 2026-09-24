"""SQLite persistence for Mycelic: agents, memories, lineage, the event log (which doubles as the transport
outbox), rules and the audit log.

Design (the same shape as ``NeuralGraph.chat_memory.store``, which has been running the assistant):

* One ``sqlite3`` connection in WAL mode, used only from the asyncio event-loop thread.  Every write goes
  through :meth:`transaction` (``BEGIN IMMEDIATE`` ... ``COMMIT``) under an ``asyncio.Lock``, so a multi-row
  write is atomic and never interleaves across ``await`` points.
* Writes that must be atomic together are composed by the service inside ONE transaction: "store the memory +
  append its event to the outbox", "mark the event applied + write the derived memories + lineage edges +
  append the derived events".  A crash between two of those steps therefore leaves nothing half-done.
* ``events`` is both the durable log of what this node accepted and the outbox: rows start ``pending``, the
  publisher marks them ``published`` with their JetStream sequence, and the consumer marks them ``applied``.
  Duplicate deliveries are rejected by primary key (``event_id``), which is also the JetStream ``Nats-Msg-Id``.
* ``revision`` increases on every memory write so the retrieval index knows when to rebuild.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from .hierarchy import LAYERS
from .models import Agent, EventRecord, LineageEdge, Memory, Rule, now_iso, utcnow

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_DDL = """
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
    updated_at     TEXT NOT NULL
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
"""


def _j(v: Any) -> str:
    return json.dumps(v if v is not None else {}, ensure_ascii=False, sort_keys=True)


def _jl(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


def row_memory(r: sqlite3.Row) -> Memory:
    return Memory(
        memory_id=r["memory_id"], org_id=r["org_id"], layer=r["layer"], scope=r["scope"], text=r["text"],
        topic=r["topic"], slot=r["slot"], entity=r["entity"], kind=r["kind"], confidence=float(r["confidence"]),
        support=int(r["support"]), independent_teams=int(r["independent_teams"]), producer_id=r["producer_id"],
        operator=r["operator"], rule_id=r["rule_id"], event_id=r["event_id"], visibility=r["visibility"],
        status=r["status"], superseded_by=r["superseded_by"], created_at=r["created_at"], applied_at=r["applied_at"],
        source_event_ids=_jl(r["source_event_ids"], []), local_ref=r["local_ref"], metadata=_jl(r["metadata"], {}),
    )


def row_agent(r: sqlite3.Row) -> Agent:
    return Agent(
        agent_id=r["agent_id"], org_id=r["org_id"], display_name=r["display_name"], path=r["path"],
        scopes=_jl(r["scopes"], []), key_prefix=r["key_prefix"], status=r["status"], created_at=r["created_at"],
        last_seen_at=r["last_seen_at"], metadata=_jl(r["metadata"], {}),
    )


def row_event(r: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=r["event_id"], kind=r["kind"], org_id=r["org_id"], agent_id=r["agent_id"], subject=r["subject"],
        payload=_jl(r["payload"], {}), status=r["status"], js_seq=r["js_seq"], attempts=int(r["attempts"]),
        last_error=r["last_error"], created_at=r["created_at"], published_at=r["published_at"], applied_at=r["applied_at"],
    )


def row_rule(r: sqlite3.Row) -> Rule:
    return Rule(
        rule_id=r["rule_id"], org_id=r["org_id"], target_layer=r["target_layer"],
        required_slots=_jl(r["required_slots"], []), conclusion=r["conclusion"], topic_prefix=r["topic_prefix"],
        min_agents=int(r["min_agents"]), min_teams=int(r["min_teams"]), kind=r["kind"], enabled=bool(r["enabled"]),
        metadata=_jl(r["metadata"], {}),
    )


def row_edge(r: sqlite3.Row) -> LineageEdge:
    return LineageEdge(child_id=r["child_id"], parent_id=r["parent_id"], contributed_by=r["contributed_by"],
                       parent_layer=r["parent_layer"], created_at=r["created_at"])


class Tx:
    """Synchronous write handle valid inside ``async with store.transaction() as tx``."""

    def __init__(self, store: "MycelicStore", conn: sqlite3.Connection) -> None:
        self._store = store
        self.c = conn

    # ---- agents
    def ensure_org(self, org_id: str, name: str | None = None) -> None:
        self.c.execute("INSERT OR IGNORE INTO orgs(org_id, name, created_at) VALUES (?, ?, ?)",
                       (org_id, name or org_id, now_iso()))

    def insert_agent(self, agent: Agent, key_hash: str) -> None:
        self.ensure_org(agent.org_id)
        self.c.execute(
            """INSERT INTO agents(agent_id, org_id, display_name, path, scopes, key_hash, key_prefix, status,
                                  created_at, last_seen_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent.agent_id, agent.org_id, agent.display_name, agent.path, _j(agent.scopes), key_hash,
             agent.key_prefix, agent.status, agent.created_at or now_iso(), agent.last_seen_at, _j(agent.metadata)),
        )

    def set_agent_status(self, agent_id: str, status: str) -> bool:
        cur = self.c.execute("UPDATE agents SET status=? WHERE agent_id=?", (status, agent_id))
        return cur.rowcount > 0

    def rotate_agent_key(self, agent_id: str, key_hash: str, key_prefix: str) -> bool:
        cur = self.c.execute("UPDATE agents SET key_hash=?, key_prefix=? WHERE agent_id=?", (key_hash, key_prefix, agent_id))
        return cur.rowcount > 0

    # ---- memories
    def memory_exists(self, memory_id: str) -> bool:
        return self.c.execute("SELECT 1 FROM memories WHERE memory_id=?", (memory_id,)).fetchone() is not None

    def insert_memory(self, m: Memory) -> bool:
        """Insert if absent. Returns False when the id already exists (idempotent replay)."""
        if self.memory_exists(m.memory_id):
            return False
        self.c.execute(
            """INSERT INTO memories(memory_id, org_id, layer, scope, text, topic, slot, entity, kind, confidence,
                                    support, independent_teams, producer_id, operator, rule_id, agg_key, event_id,
                                    visibility, status, superseded_by, created_at, applied_at, source_event_ids,
                                    local_ref, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m.memory_id, m.org_id, m.layer, m.scope, m.text, m.topic, m.slot, m.entity, m.kind, m.confidence,
             m.support, m.independent_teams, m.producer_id, m.operator, m.rule_id, m.metadata.get("agg_key"),
             m.event_id, m.visibility, m.status, m.superseded_by, m.created_at, m.applied_at,
             _j(m.source_event_ids), m.local_ref, _j(m.metadata)),
        )
        self._store._bump()
        return True

    def set_memory_status(self, memory_id: str, status: str, *, superseded_by: str | None = None,
                          reason: str | None = None) -> bool:
        row = self.c.execute("SELECT metadata FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            return False
        meta = _jl(row["metadata"], {})
        if reason:
            meta["status_reason"] = reason
        self.c.execute("UPDATE memories SET status=?, superseded_by=COALESCE(?, superseded_by), metadata=? WHERE memory_id=?",
                       (status, superseded_by, _j(meta), memory_id))
        self._store._bump()
        return True

    def reactivate_memory(self, memory_id: str, *, applied_at: str, metadata: dict[str, Any]) -> None:
        """A derived memory whose exact coalition returns (after a retraction) becomes the active version again."""
        self.c.execute("UPDATE memories SET status='active', superseded_by=NULL, applied_at=?, metadata=? WHERE memory_id=?",
                       (applied_at, _j(metadata), memory_id))
        self._store._bump()

    def set_applied(self, memory_id: str, applied_at: str) -> None:
        self.c.execute("UPDATE memories SET applied_at=COALESCE(applied_at, ?) WHERE memory_id=?", (applied_at, memory_id))

    def add_lineage_edges(self, edges: Iterable[LineageEdge]) -> None:
        self.c.executemany(
            "INSERT OR IGNORE INTO lineage_edges(child_id, parent_id, contributed_by, parent_layer, created_at) VALUES (?, ?, ?, ?, ?)",
            [(e.child_id, e.parent_id, e.contributed_by, e.parent_layer, e.created_at) for e in edges],
        )

    # ---- events / outbox
    def event_status(self, event_id: str) -> str | None:
        row = self.c.execute("SELECT status FROM events WHERE event_id=?", (event_id,)).fetchone()
        return row["status"] if row else None

    def insert_event(self, e: EventRecord) -> bool:
        """Append to the log/outbox. Returns False if the event id is already known."""
        if self.event_status(e.event_id) is not None:
            return False
        self.c.execute(
            """INSERT INTO events(event_id, kind, org_id, agent_id, subject, payload, status, js_seq, attempts,
                                  last_error, created_at, published_at, applied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (e.event_id, e.kind, e.org_id, e.agent_id, e.subject, _j(e.payload), e.status, e.js_seq, e.attempts,
             e.last_error, e.created_at or now_iso(), e.published_at, e.applied_at),
        )
        return True

    def mark_applied(self, event_id: str, js_seq: int | None, applied_at: str) -> None:
        # an event delivered from the stream is by definition published, whatever the publisher has recorded so far
        self.c.execute("""UPDATE events SET status='applied', applied_at=?, js_seq=COALESCE(?, js_seq),
                          published_at=COALESCE(published_at, CASE WHEN ? IS NOT NULL THEN ? ELSE NULL END) WHERE event_id=?""",
                       (applied_at, js_seq, js_seq, applied_at, event_id))

    def mark_published(self, event_id: str, js_seq: int | None) -> None:
        self.c.execute("""UPDATE events SET status=CASE WHEN status='pending' THEN 'published' ELSE status END,
                          published_at=COALESCE(published_at, ?), js_seq=COALESCE(js_seq, ?) WHERE event_id=?""",
                       (now_iso(), js_seq, event_id))

    def mark_publish_failed(self, event_id: str, error: str) -> None:
        self.c.execute("UPDATE events SET attempts=attempts+1, last_error=? WHERE event_id=?", (error[:500], event_id))

    def mark_failed(self, event_id: str, error: str) -> None:
        self.c.execute("UPDATE events SET status='failed', attempts=attempts+1, last_error=? WHERE event_id=?",
                       (error[:500], event_id))

    # ---- rules
    def upsert_rule(self, rule: Rule) -> None:
        self.c.execute(
            """INSERT INTO rules(rule_id, org_id, target_layer, required_slots, conclusion, topic_prefix, min_agents,
                                 min_teams, kind, enabled, metadata, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rule_id) DO UPDATE SET org_id=excluded.org_id, target_layer=excluded.target_layer,
                 required_slots=excluded.required_slots, conclusion=excluded.conclusion, topic_prefix=excluded.topic_prefix,
                 min_agents=excluded.min_agents, min_teams=excluded.min_teams, kind=excluded.kind, enabled=excluded.enabled,
                 metadata=excluded.metadata, updated_at=excluded.updated_at""",
            (rule.rule_id, rule.org_id, rule.target_layer, _j(rule.required_slots), rule.conclusion, rule.topic_prefix,
             rule.min_agents, rule.min_teams, rule.kind, 1 if rule.enabled else 0, _j(rule.metadata), now_iso()),
        )

    def delete_rule(self, rule_id: str) -> bool:
        return self.c.execute("DELETE FROM rules WHERE rule_id=?", (rule_id,)).rowcount > 0

    # ---- meta / audit
    def set_meta(self, key: str, value: str) -> None:
        self.c.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, value))

    def audit(self, principal: str, action: str, target: str | None = None, detail: dict[str, Any] | None = None,
              remote: str | None = None) -> None:
        self.c.execute("INSERT INTO audit_log(at, principal, action, target, detail, remote) VALUES (?, ?, ?, ?, ?, ?)",
                       (now_iso(), principal, action, target, _j(detail or {}), remote))


class MycelicStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = ":memory:" if str(db_path) == ":memory:" else str(Path(db_path).expanduser())
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self.revision = 0
        self._init_schema()

    # ------------------------------------------------------------------ lifecycle
    def _init_schema(self) -> None:
        c = self._conn
        if self.db_path != ":memory:":
            try:
                c.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
        # FULL, not NORMAL: the consumer acks a JetStream message right after the commit that applied it, so the
        # commit must survive an OS crash or power loss too, not only a process crash.
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=5000")
        c.executescript(_DDL)
        row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            c.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        elif int(row["value"]) > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {row['value']} is newer than this code ({SCHEMA_VERSION})")

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def _bump(self) -> None:
        self.revision += 1

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Tx]:
        async with self._lock:
            c = self._conn
            c.execute("BEGIN IMMEDIATE")
            try:
                yield Tx(self, c)
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with self.transaction() as tx:
            tx.set_meta(key, value)

    # ------------------------------------------------------------------ agents
    async def create_agent(self, agent: Agent, key_hash: str) -> Agent:
        async with self.transaction() as tx:
            tx.insert_agent(agent, key_hash)
        return self.get_agent(agent.agent_id)  # type: ignore[return-value]

    def get_agent(self, agent_id: str) -> Agent | None:
        r = self._conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return row_agent(r) if r else None

    def get_agent_credentials(self, agent_id: str) -> tuple[Agent, str] | None:
        r = self._conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return (row_agent(r), r["key_hash"]) if r else None

    async def list_agents(self, org_id: str | None = None, *, include_revoked: bool = False) -> list[Agent]:
        sql, args = "SELECT * FROM agents WHERE 1=1", []
        if org_id:
            sql += " AND org_id=?"; args.append(org_id)
        if not include_revoked:
            sql += " AND status='active'"
        sql += " ORDER BY path"
        return [row_agent(r) for r in self._conn.execute(sql, args).fetchall()]

    async def touch_agent(self, agent_id: str, at: str | None = None) -> None:
        async with self.transaction() as tx:
            tx.c.execute("UPDATE agents SET last_seen_at=? WHERE agent_id=?", (at or now_iso(), agent_id))

    def child_units(self, org_id: str, unit: str) -> list[str]:
        """Direct child units of ``unit`` according to the registered (active) agents' paths."""
        depth = unit.count("/") + 2
        rows = self._conn.execute("SELECT path FROM agents WHERE org_id=? AND status='active' AND (path=? OR substr(path, 1, ?)=?)",
                                  (org_id, unit, len(unit) + 1, unit + "/")).fetchall()
        return sorted({"/".join(r["path"].split("/")[:depth]) for r in rows if r["path"].count("/") + 1 >= depth})

    async def list_orgs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM orgs ORDER BY org_id").fetchall()]

    # ------------------------------------------------------------------ memories (reads)
    def get_memory(self, memory_id: str) -> Memory | None:
        r = self._conn.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        return row_memory(r) if r else None

    def get_memories(self, ids: Iterable[str]) -> dict[str, Memory]:
        ids = list(dict.fromkeys(ids))
        out: dict[str, Memory] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT * FROM memories WHERE memory_id IN ({','.join('?' * len(chunk))})"
            for r in self._conn.execute(q, chunk).fetchall():
                out[r["memory_id"]] = row_memory(r)
        return out

    def list_memories(self, org_id: str, *, scope: str | None = None, layers: Iterable[str] | None = None,
                      status: str | None = "active", topic: str | None = None, entity: str | None = None,
                      slot: str | None = None, operator: str | None = None, producer_id: str | None = None,
                      since: str | None = None, limit: int = 200, newest_first: bool = True) -> list[Memory]:
        sql, args = "SELECT * FROM memories WHERE org_id=?", [org_id]
        if scope:
            sql += " AND (scope=? OR substr(scope, 1, ?)=?)"; args += [scope, len(scope) + 1, scope + "/"]
        if layers:
            ls = list(layers)
            sql += f" AND layer IN ({','.join('?' * len(ls))})"; args += ls
        if status:
            sql += " AND status=?"; args.append(status)
        if topic:
            sql += " AND topic=?"; args.append(topic)
        if entity:
            sql += " AND entity=?"; args.append(entity)
        if slot:
            sql += " AND slot=?"; args.append(slot)
        if operator:
            sql += " AND operator=?"; args.append(operator)
        if producer_id:
            sql += " AND producer_id=?"; args.append(producer_id)
        if since:
            sql += " AND created_at>=?"; args.append(since)
        sql += " ORDER BY rid " + ("DESC" if newest_first else "ASC") + " LIMIT ?"
        args.append(int(limit))
        return [row_memory(r) for r in self._conn.execute(sql, args).fetchall()]

    def current_derived(self, org_id: str, operator: str, scope: str, agg_key: str) -> Memory | None:
        r = self._conn.execute(
            "SELECT * FROM memories WHERE org_id=? AND operator=? AND scope=? AND agg_key=? AND status='active' ORDER BY rid DESC LIMIT 1",
            (org_id, operator, scope, agg_key)).fetchone()
        return row_memory(r) if r else None

    def visible_rows(self, org_id: str, *, agent_path: str | None, team_path: str | None, scope: str | None,
                     min_layer_index: int = 0, statuses: tuple[str, ...] = ("active",)) -> list[Memory]:
        """Memories a principal may read, optionally restricted to a unit subtree.

        ``agent_path``/``team_path`` None means an administrator (everything in the org).  For an agent: agent-layer
        memories of its own team (or any with ``visibility='org'``), and derived memories of every unit the agent
        belongs to (the memory's scope is an ancestor-or-self of the agent's path).  Prefix tests use ``substr``
        rather than ``LIKE`` because ``_`` is a LIKE wildcard and a legal path character.
        """
        sql = f"SELECT * FROM memories WHERE org_id=? AND status IN ({','.join('?' * len(statuses))})"
        args: list[Any] = [org_id, *statuses]
        if agent_path is not None and team_path is not None:
            sql += (" AND ((layer='agent' AND (visibility='org' OR substr(scope, 1, ?)=?))"
                    " OR (layer!='agent' AND (scope=? OR substr(?, 1, length(scope)+1)=scope||'/')))")
            args += [len(team_path) + 1, team_path + "/", agent_path, agent_path]
        if scope:
            sql += " AND (scope=? OR substr(scope, 1, ?)=?)"; args += [scope, len(scope) + 1, scope + "/"]
        if min_layer_index > 0:
            ls = list(LAYERS[min_layer_index:])
            sql += f" AND layer IN ({','.join('?' * len(ls))})"; args += ls
        sql += " ORDER BY rid"
        return [row_memory(r) for r in self._conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------ lineage (reads)
    def parents_of(self, child_id: str) -> list[LineageEdge]:
        rows = self._conn.execute("SELECT * FROM lineage_edges WHERE child_id=? ORDER BY parent_id", (child_id,)).fetchall()
        return [row_edge(r) for r in rows]

    def children_of(self, parent_id: str) -> list[LineageEdge]:
        rows = self._conn.execute("SELECT * FROM lineage_edges WHERE parent_id=? ORDER BY child_id", (parent_id,)).fetchall()
        return [row_edge(r) for r in rows]

    def dependents_of(self, memory_id: str, *, max_nodes: int = 10_000) -> list[str]:
        """Every memory that (transitively) derives from ``memory_id``, nearest first."""
        seen: list[str] = []
        frontier = [memory_id]
        visited = {memory_id}
        while frontier and len(seen) < max_nodes:
            nxt = []
            for pid in frontier:
                for e in self.children_of(pid):
                    if e.child_id not in visited:
                        visited.add(e.child_id)
                        seen.append(e.child_id)
                        nxt.append(e.child_id)
            frontier = nxt
        return seen

    # ------------------------------------------------------------------ events (reads)
    def get_event(self, event_id: str) -> EventRecord | None:
        r = self._conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return row_event(r) if r else None

    def get_events(self, ids: Iterable[str]) -> dict[str, EventRecord]:
        ids = list(dict.fromkeys(ids))
        out: dict[str, EventRecord] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT * FROM events WHERE event_id IN ({','.join('?' * len(chunk))})"
            for r in self._conn.execute(q, chunk).fetchall():
                out[r["event_id"]] = row_event(r)
        return out

    def pending_events(self, limit: int = 100) -> list[EventRecord]:
        rows = self._conn.execute("SELECT * FROM events WHERE status='pending' ORDER BY rid LIMIT ?", (limit,)).fetchall()
        return [row_event(r) for r in rows]

    def list_events(self, org_id: str, *, agent_id: str | None = None, kind: str | None = None, status: str | None = None,
                    limit: int = 100) -> list[EventRecord]:
        sql, args = "SELECT * FROM events WHERE org_id=?", [org_id]
        if agent_id:
            sql += " AND agent_id=?"; args.append(agent_id)
        if kind:
            sql += " AND kind=?"; args.append(kind)
        if status:
            sql += " AND status=?"; args.append(status)
        sql += " ORDER BY rid DESC LIMIT ?"; args.append(int(limit))
        return [row_event(r) for r in self._conn.execute(sql, args).fetchall()]

    def event_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM events GROUP BY status").fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def max_applied_seq(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(js_seq), 0) AS s FROM events WHERE status='applied'").fetchone()
        return int(row["s"])

    # ------------------------------------------------------------------ rules
    def list_rules(self, org_id: str | None = None, *, enabled_only: bool = True) -> list[Rule]:
        sql, args = "SELECT * FROM rules WHERE 1=1", []
        if org_id is not None:
            sql += " AND (org_id IS NULL OR org_id=?)"; args.append(org_id)
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY rule_id"
        return [row_rule(r) for r in self._conn.execute(sql, args).fetchall()]

    def get_rule(self, rule_id: str) -> Rule | None:
        r = self._conn.execute("SELECT * FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
        return row_rule(r) if r else None

    # ------------------------------------------------------------------ audit / stats
    async def audit(self, principal: str, action: str, target: str | None = None, detail: dict[str, Any] | None = None,
                    remote: str | None = None) -> None:
        async with self.transaction() as tx:
            tx.audit(principal, action, target, detail, remote)

    async def prune_audit(self, older_than_days: int) -> int:
        cutoff = (utcnow() - timedelta(days=older_than_days)).isoformat(timespec="seconds")
        async with self.transaction() as tx:
            return tx.c.execute("DELETE FROM audit_log WHERE at < ?", (cutoff,)).rowcount

    def recent_audit(self, limit: int = 50, *, org_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = _jl(d.get("detail"), {})
            if org_id is None or d["detail"].get("org_id") == org_id:
                out.append(d)
        return out

    def stats(self) -> dict[str, Any]:
        c = self._conn
        by_layer = {layer: 0 for layer in LAYERS}
        for r in c.execute("SELECT layer, COUNT(*) AS n FROM memories WHERE status='active' GROUP BY layer").fetchall():
            by_layer[r["layer"]] = int(r["n"])
        by_status = {r["status"]: int(r["n"]) for r in c.execute("SELECT status, COUNT(*) AS n FROM memories GROUP BY status").fetchall()}
        cutoff = (utcnow() - timedelta(minutes=15)).isoformat(timespec="seconds")
        active_agents = int(c.execute("SELECT COUNT(*) AS n FROM agents WHERE status='active' AND last_seen_at>=?", (cutoff,)).fetchone()["n"])
        registered = int(c.execute("SELECT COUNT(*) AS n FROM agents WHERE status='active'").fetchone()["n"])
        events = self.event_counts()
        return {
            "memories_by_layer": by_layer,
            "memories_by_status": by_status,
            "lineage_edges": int(c.execute("SELECT COUNT(*) AS n FROM lineage_edges").fetchone()["n"]),
            "events": events,
            "outbox_pending": events.get("pending", 0),
            "registered_agents": registered,
            "active_agents": active_agents,
            "orgs": int(c.execute("SELECT COUNT(*) AS n FROM orgs").fetchone()["n"]),
            "rules": int(c.execute("SELECT COUNT(*) AS n FROM rules WHERE enabled=1").fetchone()["n"]),
            "last_applied_seq": self.max_applied_seq(),
            "revision": self.revision,
        }

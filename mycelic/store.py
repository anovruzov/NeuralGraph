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
* Every memory row carries a keyed digest of its content (a raw note's ``expires_at`` included when it has one), its
  parents and its derivation metadata (``integrity.py`` says exactly what is covered), written by the statement that
  inserts it, so a rebuild reproduces it.  Covered content
  never changes after insert, with one exception: a producer's re-attestation sets a raw note's ``attested_at`` and
  signs the row again (:meth:`Tx.set_attested`, only after its digest checked ``ok``).  A future migration that rewrites
  covered content must re-sign the rows it rewrites.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from .hierarchy import LAYERS
from .integrity import Keyring, canonical, check_memory
from .models import OPERATORS, Agent, EventRecord, LineageEdge, Memory, Rule, canonical_label, canonical_rule_body, now_iso, utcnow

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]  # Windows: no advisory lock (development only)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 7

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
    metadata     TEXT NOT NULL DEFAULT '{}',
    log_status   TEXT              -- status as of the last applied registry event (NULL: registration not applied yet;
                                   -- 'removed' is terminal)
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
    metadata          TEXT NOT NULL DEFAULT '{}',
    apply_seq         INTEGER,         -- position in apply order (NULL until the consumer applies the memory)
    digest            TEXT,            -- keyed digest of the row's content and parents (integrity.py), NULL until backfilled
    digest_key_id     TEXT,            -- the key that signed it ('none' when unkeyed)
    digest_origin     TEXT,            -- 'write' (signed by the insert) or 'backfill' (signed at start-up after the upgrade)
    expires_at        TEXT,            -- a raw note's expiry (UTC, seconds); the sweep retracts it through the log after it
    attested_at       TEXT,            -- when the producer last re-attested a raw note (UTC, seconds)
    retired_at        TEXT             -- when the row stopped being active (superseded or retracted); NULL while active
);
CREATE INDEX IF NOT EXISTS idx_memories_org_status ON memories(org_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_org_op_status ON memories(org_id, operator, status);
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

-- the rules as of the last applied rule event: what aggregation evaluates (``rules`` is what the admin API wrote)
CREATE TABLE IF NOT EXISTS applied_rules (
    rule_id     TEXT PRIMARY KEY,
    org_id      TEXT,
    snapshot    TEXT NOT NULL,
    applied_seq INTEGER NOT NULL DEFAULT 0
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

# indexes on columns that a migration adds: created only after ``_migrate`` (``_DDL`` runs on the old schema first)
_POST_MIGRATION_DDL = """
CREATE INDEX IF NOT EXISTS idx_memories_apply_seq ON memories(apply_seq);
CREATE INDEX IF NOT EXISTS idx_memories_expiry ON memories(expires_at, rid) WHERE status='active' AND expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memories_retired ON memories(retired_at, rid)
    WHERE retired_at IS NOT NULL AND operator != 'agent_observation';
"""

_RULE_FIELDS = frozenset(Rule.__dataclass_fields__)
_NEXT_APPLY_SEQ = "(SELECT COALESCE(MAX(apply_seq), 0) + 1 FROM memories)"


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
        expires_at=r["expires_at"], attested_at=r["attested_at"],
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
        metadata=_jl(r["metadata"], {}), sources=_jl(r["sources"], ["agent_observation"]), emits_slot=r["emits_slot"],
        emits_topic=r["emits_topic"],
        min_units={slot: {layer: int(n) for layer, n in per.items()} for slot, per in _jl(r["min_units"], {}).items()
                   if isinstance(per, dict)},
        corroborate=bool(r["corroborate"]),
    )


def snapshot_rule(snapshot: str) -> Rule:
    return Rule(**{k: v for k, v in _jl(snapshot, {}).items() if k in _RULE_FIELDS})


def row_edge(r: sqlite3.Row) -> LineageEdge:
    return LineageEdge(child_id=r["child_id"], parent_id=r["parent_id"], contributed_by=r["contributed_by"],
                       parent_layer=r["parent_layer"], created_at=r["created_at"])


class Tx:
    """Synchronous write handle valid inside ``async with store.transaction() as tx``."""

    def __init__(self, store: "MycelicStore", conn: sqlite3.Connection) -> None:
        self._store = store
        self.c = conn
        #: the log time of the event this transaction applies (its ``created_at``, never after now), set by the apply:
        #: a row it retires counts as retired from then (``retired_at``), so a rebuild from the log stamps the time the
        #: live node stamped; None (local maintenance) stamps now
        self.log_time: str | None = None

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

    def set_agent_log_status(self, agent_id: str, status: str) -> bool:
        """Record the registry as the log has it (apply side). Returns False when nothing changed."""
        cur = self.c.execute("UPDATE agents SET log_status=? WHERE agent_id=? AND log_status IS NOT ?", (status, agent_id, status))
        return cur.rowcount > 0

    def rotate_agent_key(self, agent_id: str, key_hash: str, key_prefix: str) -> bool:
        cur = self.c.execute("UPDATE agents SET key_hash=?, key_prefix=? WHERE agent_id=?", (key_hash, key_prefix, agent_id))
        return cur.rowcount > 0

    # ---- memories
    def memory_exists(self, memory_id: str) -> bool:
        return self.c.execute("SELECT 1 FROM memories WHERE memory_id=?", (memory_id,)).fetchone() is not None

    def insert_memory(self, m: Memory, *, parent_ids: Iterable[str] = ()) -> bool:
        """Insert if absent. Returns False when the id already exists (idempotent replay).

        A memory inserted as applied (``applied_at`` set: apply side) takes the next position in apply order; one
        written by the API gets it when the consumer applies its event (:meth:`set_applied`).  Both a live node and
        a rebuild therefore number every row in the same order, whatever the consumer lag was.

        The same statement writes the row's digest (origin ``write``) over its content and, for a derived memory, the
        ``parent_ids`` its lineage edges will name; nothing ever rewrites it.
        """
        if self.memory_exists(m.memory_id):
            return False
        digest, kid = self._store.keyring.sign(canonical(m, parent_ids))
        self.c.execute(
            f"""INSERT INTO memories(memory_id, org_id, layer, scope, text, topic, slot, entity, kind, confidence,
                                    support, independent_teams, producer_id, operator, rule_id, agg_key, event_id,
                                    visibility, status, superseded_by, created_at, applied_at, source_event_ids,
                                    local_ref, metadata, apply_seq, digest, digest_key_id, digest_origin, expires_at,
                                    attested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       CASE WHEN ? IS NOT NULL THEN {_NEXT_APPLY_SEQ} END, ?, ?, 'write', ?, ?)""",
            (m.memory_id, m.org_id, m.layer, m.scope, m.text, m.topic, m.slot, m.entity, m.kind, m.confidence,
             m.support, m.independent_teams, m.producer_id, m.operator, m.rule_id,
             m.metadata.get("agg_key") if m.operator != "agent_observation" else None,
             m.event_id, m.visibility, m.status, m.superseded_by, m.created_at, m.applied_at,
             _j(m.source_event_ids), m.local_ref, _j(m.metadata), m.applied_at, digest, kid, m.expires_at, m.attested_at),
        )
        self._store._bump()
        return True

    def _lifecycle(self, memory_id: str, status: str, superseded_by: str | None, metadata: dict[str, Any],
                   extra: dict[str, Any] | None = None) -> bool:
        """Write a row's status, ``superseded_by``, metadata and ``extra`` columns, and sign it again under its digest's
        origin (the digest covers the lifecycle, ``integrity.LIFECYCLE_FIELDS``), only if its digest checks ``ok`` as it
        is stored now: an edited row is never re-signed, and a row without a digest is left to the backfill."""
        r = self.c.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        if r is None:
            return False
        keyring, m = self._store.keyring, row_memory(r)
        parents = [] if m.operator == "agent_observation" else [
            e["parent_id"] for e in self.c.execute("SELECT parent_id FROM lineage_edges WHERE child_id=?", (memory_id,))]
        # when the row stopped being active, in the log's time (``log_time``), kept through later status changes (the
        # retention of retired derived rows counts from it), cleared when it is active again
        retired_at = None if status == "active" else (r["retired_at"] or self.log_time or now_iso())
        columns = {"status": status, "superseded_by": superseded_by, "metadata": _j(metadata), "retired_at": retired_at,
                   **(extra or {})}
        if check_memory(keyring, m, parents, r["digest"], r["digest_key_id"], r["digest_origin"]) == "ok":
            m.status, m.superseded_by, m.metadata = status, superseded_by, metadata
            columns["digest"], columns["digest_key_id"] = keyring.sign(canonical(m, parents), origin=r["digest_origin"])
        self.c.execute(f"UPDATE memories SET {', '.join(f'{k}=?' for k in columns)} WHERE memory_id=?",
                       (*columns.values(), memory_id))
        self._store._bump()
        return True

    def set_memory_status(self, memory_id: str, status: str, *, superseded_by: str | None = None,
                          reason: str | None = None) -> bool:
        row = self.c.execute("SELECT metadata, superseded_by FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            return False
        meta = _jl(row["metadata"], {})
        if reason:
            meta["status_reason"] = reason
        return self._lifecycle(memory_id, status, superseded_by if superseded_by is not None else row["superseded_by"], meta)

    def set_attested(self, memory_id: str, attested_at: str) -> bool:
        """Record a producer's re-attestation of a raw note and sign the row again with origin ``write``: the one write of
        covered content after insert.  Nothing is written (False) unless the row is a raw note whose digest checks ``ok``
        as it is stored now, so an attestation never launders an edited row."""
        r = self.c.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        if r is None or r["operator"] != "agent_observation":
            return False
        keyring, m = self._store.keyring, row_memory(r)
        if check_memory(keyring, m, (), r["digest"], r["digest_key_id"], r["digest_origin"]) != "ok":
            return False
        m.attested_at = attested_at
        digest, kid = keyring.sign(canonical(m))
        self.c.execute("UPDATE memories SET attested_at=?, digest=?, digest_key_id=?, digest_origin='write' WHERE memory_id=?",
                       (attested_at, digest, kid, memory_id))
        self._store._bump()
        return True

    def reactivate_memory(self, memory_id: str, *, applied_at: str, metadata: dict[str, Any]) -> None:
        """A derived memory whose exact coalition returns (after a retraction) becomes the active version again."""
        self._lifecycle(memory_id, "active", None, metadata, {"applied_at": applied_at})

    def set_applied(self, memory_id: str, applied_at: str) -> None:
        self.c.execute(f"UPDATE memories SET applied_at=COALESCE(applied_at, ?), apply_seq=COALESCE(apply_seq, {_NEXT_APPLY_SEQ}) "
                       "WHERE memory_id=?", (applied_at, memory_id))

    def backfill_digests(self, *, after_rid: int, below_rid: int | None, limit: int) -> tuple[int, int]:
        """Sign up to ``limit`` rows without a digest after ``after_rid`` (and below ``below_rid``) with origin
        ``backfill``; returns (rows signed, last rid seen).  A row that has a digest is never touched."""
        sql, args = "SELECT * FROM memories WHERE digest IS NULL AND rid > ?", [after_rid]
        if below_rid is not None:
            sql += " AND rid < ?"; args.append(below_rid)
        rows = self.c.execute(sql + " ORDER BY rid LIMIT ?", [*args, int(limit)]).fetchall()
        if not rows:
            return 0, after_rid
        parents: dict[str, list[str]] = {}
        ids = [r["memory_id"] for r in rows]
        for e in self.c.execute(f"SELECT child_id, parent_id FROM lineage_edges WHERE child_id IN ({','.join('?' * len(ids))})",
                                ids).fetchall():
            parents.setdefault(e["child_id"], []).append(e["parent_id"])
        signed = []
        for r in rows:
            digest, kid = self._store.keyring.sign(canonical(row_memory(r), parents.get(r["memory_id"], ())), origin="backfill")
            signed.append((digest, kid, r["rid"]))
        self.c.executemany("UPDATE memories SET digest=?, digest_key_id=?, digest_origin='backfill' WHERE rid=? AND digest IS NULL",
                           signed)
        return len(signed), rows[-1]["rid"]

    def resign_lifecycle(self, *, after_rid: int, below_rid: int, limit: int) -> tuple[int, int, int]:
        """The schema-6 upgrade: sign again, in the form that covers the lifecycle, up to ``limit`` rows after
        ``after_rid`` and below ``below_rid`` (the rows that existed before the upgrade) that are not active or are
        superseded, each only if its digest checks ``ok`` in the form it was signed in before (the lifecycle left out),
        under its own origin; a row that already checks in the new form (signed by the start-up backfill after an upgrade
        from schema 3) is left alone.  Returns (rows signed, rows that check in neither form and were left as they are,
        last rid seen)."""
        rows = self.c.execute("SELECT * FROM memories WHERE rid > ? AND rid < ? AND digest IS NOT NULL "
                              "AND (status != 'active' OR superseded_by IS NOT NULL) ORDER BY rid LIMIT ?",
                              (after_rid, below_rid, int(limit))).fetchall()
        if not rows:
            return 0, 0, after_rid
        parents: dict[str, list[str]] = {}
        ids = [r["memory_id"] for r in rows]
        for e in self.c.execute(f"SELECT child_id, parent_id FROM lineage_edges WHERE child_id IN ({','.join('?' * len(ids))})",
                                ids).fetchall():
            parents.setdefault(e["child_id"], []).append(e["parent_id"])
        keyring, signed, left = self._store.keyring, [], 0
        for r in rows:
            m = row_memory(r)
            ps = () if m.operator == "agent_observation" else parents.get(m.memory_id, ())
            if check_memory(keyring, m, ps, r["digest"], r["digest_key_id"], r["digest_origin"]) == "ok":
                continue
            before = row_memory(r)
            before.status, before.superseded_by = "active", None
            if check_memory(keyring, before, ps, r["digest"], r["digest_key_id"], r["digest_origin"]) != "ok":
                left += 1
                continue
            digest, kid = keyring.sign(canonical(m, ps), origin=r["digest_origin"])
            signed.append((digest, kid, r["rid"]))
        self.c.executemany("UPDATE memories SET digest=?, digest_key_id=? WHERE rid=?", signed)
        return len(signed), left, rows[-1]["rid"]

    def add_lineage_edges(self, edges: Iterable[LineageEdge]) -> None:
        self.c.executemany(
            "INSERT OR IGNORE INTO lineage_edges(child_id, parent_id, contributed_by, parent_layer, created_at) VALUES (?, ?, ?, ?, ?)",
            [(e.child_id, e.parent_id, e.contributed_by, e.parent_layer, e.created_at) for e in edges],
        )

    def prune_retired(self, cutoff: str, *, limit: int) -> tuple[int, int, int]:
        """Delete up to ``limit`` derived memories retired (superseded or retracted) at or before ``cutoff`` that no
        memory rests on any more, with their lineage edges and their ``memory.derived`` event rows; returns (memories,
        edges, events) deleted.  Raw notes, active rows, a row another one names as a parent (its child is retired later
        or at the same time, so it goes first and the parent at a later call) and a row whose derived event is still in
        the outbox are kept.  Local maintenance: a replay from the log derives the deleted versions again, and deletes
        them again once it reaches the log time they were deleted at here (``MycelicService._replay_sweep``)."""
        rows = self.c.execute(
            """SELECT m.memory_id, m.event_id FROM memories m
               WHERE m.retired_at IS NOT NULL AND m.retired_at <= ? AND m.operator != 'agent_observation'
                 AND m.status != 'active'
                 AND NOT EXISTS (SELECT 1 FROM lineage_edges e WHERE e.parent_id = m.memory_id)
                 AND NOT EXISTS (SELECT 1 FROM events v WHERE v.event_id = m.event_id AND v.status = 'pending')
               ORDER BY m.retired_at, m.rid LIMIT ?""", (cutoff, int(limit))).fetchall()
        if not rows:
            return 0, 0, 0
        ids = [r["memory_id"] for r in rows]
        events = [r["event_id"] for r in rows if r["event_id"]]
        marks = ",".join("?" * len(ids))
        edges = self.c.execute(f"DELETE FROM lineage_edges WHERE child_id IN ({marks})", ids).rowcount
        deleted_events = self.c.execute(
            f"DELETE FROM events WHERE kind='memory.derived' AND status != 'pending' AND event_id IN ({','.join('?' * len(events))})",
            events).rowcount if events else 0
        memories = self.c.execute(f"DELETE FROM memories WHERE memory_id IN ({marks})", ids).rowcount
        return memories, edges, deleted_events

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
                                 min_teams, kind, enabled, metadata, updated_at, sources, emits_slot, emits_topic,
                                 min_units, corroborate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rule_id) DO UPDATE SET org_id=excluded.org_id, target_layer=excluded.target_layer,
                 required_slots=excluded.required_slots, conclusion=excluded.conclusion, topic_prefix=excluded.topic_prefix,
                 min_agents=excluded.min_agents, min_teams=excluded.min_teams, kind=excluded.kind, enabled=excluded.enabled,
                 metadata=excluded.metadata, updated_at=excluded.updated_at, sources=excluded.sources,
                 emits_slot=excluded.emits_slot, emits_topic=excluded.emits_topic, min_units=excluded.min_units,
                 corroborate=excluded.corroborate""",
            (rule.rule_id, rule.org_id, rule.target_layer, _j(rule.required_slots), rule.conclusion, rule.topic_prefix,
             rule.min_agents, rule.min_teams, rule.kind, 1 if rule.enabled else 0, _j(rule.metadata), now_iso(),
             _j(rule.sources), rule.emits_slot, rule.emits_topic, _j(rule.min_units), 1 if rule.corroborate else 0),
        )

    def delete_rule(self, rule_id: str) -> bool:
        return self.c.execute("DELETE FROM rules WHERE rule_id=?", (rule_id,)).rowcount > 0

    def upsert_applied_rule(self, rule: Rule, applied_seq: int) -> None:
        self.c.execute("""INSERT INTO applied_rules(rule_id, org_id, snapshot, applied_seq) VALUES (?, ?, ?, ?)
                          ON CONFLICT(rule_id) DO UPDATE SET org_id=excluded.org_id, snapshot=excluded.snapshot,
                            applied_seq=excluded.applied_seq""",
                       (rule.rule_id, rule.org_id, _j(rule.to_dict()), int(applied_seq)))

    def delete_applied_rule(self, rule_id: str) -> bool:
        return self.c.execute("DELETE FROM applied_rules WHERE rule_id=?", (rule_id,)).rowcount > 0

    def has_unapplied_rule_event(self, rule_id: str, *, other_than: str) -> bool:
        """Is a rule event for ``rule_id`` that this node wrote still waiting to be applied?  Then ``rules`` already
        holds a newer version than an event replayed from the stream."""
        rows = self.c.execute("""SELECT payload FROM events WHERE kind IN ('rule.upserted', 'rule.deleted')
                                 AND status IN ('pending', 'published') AND event_id != ?""", (other_than,)).fetchall()
        return any(_jl(r["payload"], {}).get("rule_id") == rule_id for r in rows)

    # ---- meta / audit
    def set_meta(self, key: str, value: str) -> None:
        self.c.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, value))

    def delete_meta(self, key: str) -> None:
        self.c.execute("DELETE FROM meta WHERE key=?", (key,))

    def audit(self, principal: str, action: str, target: str | None = None, detail: dict[str, Any] | None = None,
              remote: str | None = None) -> None:
        self.c.execute("INSERT INTO audit_log(at, principal, action, target, detail, remote) VALUES (?, ?, ?, ?, ?, ?)",
                       (now_iso(), principal, action, target, _j(detail or {}), remote))


class DatabaseLocked(RuntimeError):
    """Another process (or another service in this process) already writes to this database."""


class DatabaseCorrupt(RuntimeError):
    """The database file fails SQLite's ``quick_check`` at open, so the service refuses to start on it."""


def acquire_db_lock(db_path: str | Path) -> int | None:
    """Take the single-writer lock on ``<db>.lock`` or raise; returns the descriptor that holds it.

    Two writers on one SQLite file would each run their own consumer and publisher against the same outbox and
    ``last_applied_seq``, so the second one is refused before it touches the schema.  ``flock`` (not ``lockf``)
    because it also conflicts between two ``open()`` calls in one process; the kernel drops it when the holder
    dies, so a stale ``.lock`` file never blocks.  The file is never unlinked: that would race with a new holder.
    """
    if str(db_path) == ":memory:" or fcntl is None:
        return None
    path = Path(db_path).expanduser()
    lock_path = Path(str(path) + ".lock")
    fd: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(str(path.resolve()) + ".lock")      # two spellings or symlinks of one file contend
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = ""
        try:
            pid = os.pread(fd, 32, 0).decode("ascii", "replace").strip()  # type: ignore[arg-type]
            holder = f", pid {pid}" if pid.isdigit() else ""
        except OSError:
            pass
        os.close(fd)  # type: ignore[arg-type]
        raise DatabaseLocked(f"another Mycelic process is using {path} (lock {lock_path}{holder}); "
                             "stop it first: one instance per database") from None
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        raise RuntimeError(f"cannot lock {lock_path} for {path}: {exc.strerror or exc}") from exc
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass
    return fd


def release_db_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


class MycelicStore:
    def __init__(self, db_path: str | Path, *, lock_fd: int | None = None) -> None:
        """``lock_fd`` is a lock from :func:`acquire_db_lock`; the store owns it once constructed and releases it
        in :meth:`close`.  If construction fails the caller still owns it."""
        self._lock_fd: int | None = None
        self.db_path = ":memory:" if str(db_path) == ":memory:" else str(Path(db_path).expanduser())
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self.revision = 0
        self.keyring = Keyring()                    # unkeyed until the service hands it the configured keyring
        try:
            self._init_schema()
        except BaseException:
            self._conn.close()
            raise
        self._lock_fd = lock_fd

    # ------------------------------------------------------------------ lifecycle
    def _init_schema(self) -> None:
        c = self._conn
        if self.db_path != ":memory:":
            self._quick_check()                     # before this connection reads (or replays a write-ahead log into) it
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
        elif int(row["value"]) < SCHEMA_VERSION:
            self._migrate(int(row["value"]))
        c.executescript(_POST_MIGRATION_DDL)

    def _quick_check(self) -> None:
        """Refuse a database that fails SQLite's ``quick_check`` (it reads the whole file: 0.3 s for 370 MB in the cache).

        The usual cause is a backup copied over the database while the ``-wal`` and ``-shm`` files of the crashed or
        killed process that used it were left next to it: SQLite then replays that write-ahead log onto the older file,
        and the result mixes pages of both (DEPLOYMENT.md section 4, "Restoring the database").  Serving it would answer
        from rows that are neither the backup nor the lost state, with a ``last_applied_seq`` that hides the gap from
        recovery, so nothing is written to it.
        """
        path = Path(self.db_path)
        if not path.exists() or path.stat().st_size == 0:
            return                                  # a new database
        # on a read-only connection of its own: closing it can never checkpoint a write-ahead log into the file
        ro = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = [r[0] for r in ro.execute("PRAGMA quick_check").fetchall()]
        except sqlite3.DatabaseError as exc:
            rows = [str(exc)]
        finally:
            ro.close()
        if rows == ["ok"]:
            return
        wal = " ".join(f"{self.db_path}{suffix}" for suffix in ("-wal", "-shm") if os.path.exists(self.db_path + suffix))
        logger.error("database %s fails quick_check: %s", self.db_path, "; ".join(rows)[:2000])
        raise DatabaseCorrupt(
            f"database {self.db_path} fails SQLite's quick_check ({' '.join(rows[0].split())[:200]}); nothing was written "
            "to it. If a backup was copied over it, stop the service, remove the database's -wal and -shm files"
            + (f" ({wal})" if wal else "") + ", copy the backup in again and start (DEPLOYMENT.md section 4, \"Restoring "
            "the database\"); otherwise restore a backup that way, or move the database aside to rebuild it from the "
            "stream")

    def _migrate(self, from_version: int) -> None:
        """Forward-only migrations, applied in one transaction (any failure leaves the database as it was).

        * 2: the composition columns of ``rules`` (with defaults).
        * 3: log-applied state and canonical labels.  ``agents.log_status`` starts as ``status``; ``memories.apply_seq``
          numbers the applied rows in their current order; raw observations and rules get canonical labels
          (derived rows keep theirs: their ids embed the key they were built under, so they stay until
          re-aggregation, flagged by ``meta.reaggregate_pending``); ``applied_rules`` starts as the canonical rules.
        * 4: memories.digest, digest_key_id, digest_origin (NULL for existing rows until the start-up backfill signs
          them: the service holds the key, the store does not).
        * 5: memories.expires_at and attested_at, NULL for existing rows, so their digests are unchanged.
        * 6: digests cover the lifecycle (``integrity.LIFECYCLE_FIELDS``).  No column changes: ``meta.lifecycle_resign_below``
          records the first rid after the rows that existed, and the start-up step (the service holds the key) signs
          again those of them that are not active (``Tx.resign_lifecycle``), then deletes it; and, when derived memories
          exist, ``meta.reaggregate_pending``, because a conclusion now travels up next to its unit's consolidation and a
          unit with fewer registered children than MIN_SUPPORT keeps what it promotes.
        * 7: memories.retired_at, when a row stopped being active (not covered by its digest).  Rows that are not active
          already get the time of the upgrade, so the retention of retired derived rows (``Tx.prune_retired``) counts
          from it.
        """
        c = self._conn
        c.execute("BEGIN IMMEDIATE")
        try:
            if from_version < 2:
                have = {r["name"] for r in c.execute("PRAGMA table_info(rules)").fetchall()}
                for name, ddl in (("sources", "TEXT NOT NULL DEFAULT '[\"agent_observation\"]'"), ("emits_slot", "TEXT"),
                                  ("emits_topic", "TEXT"), ("min_units", "TEXT NOT NULL DEFAULT '{}'"),
                                  ("corroborate", "INTEGER NOT NULL DEFAULT 0")):
                    if name not in have:
                        c.execute(f"ALTER TABLE rules ADD COLUMN {name} {ddl}")
            if from_version < 3:
                if "log_status" not in {r["name"] for r in c.execute("PRAGMA table_info(agents)").fetchall()}:
                    c.execute("ALTER TABLE agents ADD COLUMN log_status TEXT")
                c.execute("UPDATE agents SET log_status=status")
                if "apply_seq" not in {r["name"] for r in c.execute("PRAGMA table_info(memories)").fetchall()}:
                    c.execute("ALTER TABLE memories ADD COLUMN apply_seq INTEGER")
                c.execute("UPDATE memories SET apply_seq=rid WHERE applied_at IS NOT NULL AND apply_seq IS NULL")
                changed = []
                for r in c.execute("SELECT rid, topic, slot, entity FROM memories WHERE operator='agent_observation'").fetchall():
                    labels = (canonical_label(r["topic"]), canonical_label(r["slot"]), canonical_label(r["entity"]))
                    if labels != (r["topic"], r["slot"], r["entity"]):
                        changed.append((*labels, r["rid"]))
                c.executemany("UPDATE memories SET topic=?, slot=?, entity=? WHERE rid=?", changed)
                for r in c.execute("SELECT * FROM rules").fetchall():
                    rule = Rule(**canonical_rule_body(row_rule(r).to_dict()))
                    c.execute("""UPDATE rules SET required_slots=?, conclusion=?, topic_prefix=?, emits_slot=?, emits_topic=?,
                                 min_units=? WHERE rule_id=?""",
                              (_j(rule.required_slots), rule.conclusion, rule.topic_prefix, rule.emits_slot, rule.emits_topic,
                               _j(rule.min_units), rule.rule_id))
                    c.execute("INSERT OR REPLACE INTO applied_rules(rule_id, org_id, snapshot, applied_seq) VALUES (?, ?, ?, 0)",
                              (rule.rule_id, rule.org_id, _j(rule.to_dict())))
                c.execute("INSERT INTO meta(key, value) VALUES ('reaggregate_pending', '1') "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            if from_version < 4:
                have = {r["name"] for r in c.execute("PRAGMA table_info(memories)").fetchall()}
                for name in ("digest", "digest_key_id", "digest_origin"):
                    if name not in have:
                        c.execute(f"ALTER TABLE memories ADD COLUMN {name} TEXT")
            if from_version < 5:
                have = {r["name"] for r in c.execute("PRAGMA table_info(memories)").fetchall()}
                for name in ("expires_at", "attested_at"):
                    if name not in have:
                        c.execute(f"ALTER TABLE memories ADD COLUMN {name} TEXT")
            if from_version < 6:
                below = int(c.execute("SELECT COALESCE(MAX(rid), 0) + 1 AS n FROM memories").fetchone()["n"])
                if c.execute("SELECT 1 FROM memories WHERE status != 'active' OR superseded_by IS NOT NULL LIMIT 1").fetchone():
                    c.execute("INSERT INTO meta(key, value) VALUES ('lifecycle_resign_below', ?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(below),))
                if c.execute("SELECT 1 FROM memories WHERE operator != 'agent_observation' LIMIT 1").fetchone():
                    c.execute("INSERT INTO meta(key, value) VALUES ('reaggregate_pending', '1') "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            if from_version < 7:
                if "retired_at" not in {r["name"] for r in c.execute("PRAGMA table_info(memories)").fetchall()}:
                    c.execute("ALTER TABLE memories ADD COLUMN retired_at TEXT")
                c.execute("UPDATE memories SET retired_at=? WHERE status != 'active' AND retired_at IS NULL", (now_iso(),))
            c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")
        logger.info("migrated database schema %d -> %d", from_version, SCHEMA_VERSION)

    def snapshot_reader(self) -> "MycelicStore":
        """A read-only store on its own connection to the same file, for a read that runs in another thread (downward
        verification): every read method works on it, and inside ``BEGIN`` on its connection they all see one committed
        snapshot (WAL), whatever the writer does meanwhile.  Create, use and close it (``reader._conn.close()``) in one
        thread; never for ``:memory:``, whose second connection would be another, empty database."""
        if self.db_path == ":memory:":
            raise ValueError("an in-memory database has no second connection")
        reader = object.__new__(MycelicStore)
        reader._lock_fd = None
        reader.db_path = self.db_path
        reader._conn = sqlite3.connect(self.db_path, isolation_level=None)
        reader._conn.row_factory = sqlite3.Row
        reader._conn.execute("PRAGMA query_only=ON")
        reader._conn.execute("PRAGMA busy_timeout=5000")
        reader._lock = asyncio.Lock()
        reader.revision = self.revision
        reader.keyring = self.keyring
        return reader

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]
            release_db_lock(self._lock_fd)
            self._lock_fd = None

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

    def get_agents(self, agent_ids: Iterable[str]) -> dict[str, Agent]:
        ids = list(dict.fromkeys(agent_ids))
        out: dict[str, Agent] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in self._conn.execute(f"SELECT * FROM agents WHERE agent_id IN ({','.join('?' * len(chunk))})", chunk).fetchall():
                out[r["agent_id"]] = row_agent(r)
        return out

    def agent_log_status(self, agent_id: str) -> str | None:
        """The agent's status as the log has applied it (``active``, ``revoked`` or the terminal ``removed``), or None."""
        r = self._conn.execute("SELECT log_status FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return r["log_status"] if r else None

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
        """Direct child units of ``unit`` according to the paths of the agents whose registration the consumer has
        applied and whose revocation it has not: the registry as of the event being applied, as a rebuild sees it."""
        depth = unit.count("/") + 2
        rows = self._conn.execute("SELECT path FROM agents WHERE org_id=? AND log_status='active' AND (path=? OR substr(path, 1, ?)=?)",
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
                      since: str | None = None, limit: int = 200, newest_first: bool = True,
                      applied_only: bool = False, operators: Iterable[str] | None = None,
                      slots: Iterable[str] | None = None, topic_prefix: str | None = None, null_entity: bool = False,
                      exclude_subtrees: Iterable[str] = (), latest: bool = False) -> list[Memory]:
        """Memories matching every filter given.  Prefix and subtree filters use ``substr``, never ``LIKE`` (``_``
        and ``%`` are literal characters of labels and unit names).

        ``latest``: only applied rows, the newest ``limit`` of them in apply order, returned oldest first.  That is
        how aggregation reads candidates: the same rows on a live node and on a rebuild, and new evidence is never
        crowded out by old.
        """
        sql, args = "SELECT * FROM memories WHERE org_id=?", [org_id]
        if operators:
            ops = list(operators)
            sql += f" AND operator IN ({','.join('?' * len(ops))})"; args += ops
        if applied_only:                       # only what the consumer has applied: aggregation must not see ahead
            sql += " AND applied_at IS NOT NULL"
        if latest:
            sql += " AND apply_seq IS NOT NULL"
        if scope:
            sql += " AND (scope=? OR substr(scope, 1, ?)=?)"; args += [scope, len(scope) + 1, scope + "/"]
        for sub in exclude_subtrees:
            sql += " AND NOT (scope=? OR substr(scope, 1, ?)=?)"; args += [sub, len(sub) + 1, sub + "/"]
        if slots:
            ss = list(slots)
            sql += f" AND slot IN ({','.join('?' * len(ss))})"; args += ss
        if topic_prefix:
            sql += " AND substr(topic, 1, ?)=?"; args += [len(topic_prefix), topic_prefix]
        if null_entity:
            sql += " AND entity IS NULL"
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
        if latest:
            sql = f"SELECT * FROM ({sql} ORDER BY apply_seq DESC LIMIT ?) ORDER BY apply_seq"
        else:
            sql += " ORDER BY rid " + ("DESC" if newest_first else "ASC") + " LIMIT ?"
        args.append(int(limit))
        return [row_memory(r) for r in self._conn.execute(sql, args).fetchall()]

    def active_notes(self, org_id: str, producer_id: str, *, applied_only: bool) -> list[Memory]:
        """The active raw notes of one producer: with ``applied_only`` those the consumer has applied, in apply order (the
        same rows in the same order on a live node and on a rebuild), else every one, unapplied rows included."""
        sql = "SELECT * FROM memories WHERE org_id=? AND producer_id=? AND operator='agent_observation' AND status='active'"
        if applied_only:
            sql += " AND apply_seq IS NOT NULL ORDER BY apply_seq, memory_id"
        else:
            sql += " ORDER BY rid"
        return [row_memory(r) for r in self._conn.execute(sql, (org_id, producer_id)).fetchall()]

    def active_note_count(self, org_id: str) -> int:
        """The organization's active raw notes, applied or still on their way through the log (what
        ``MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG`` caps), counted on the covering index ``idx_memories_org_op_status``."""
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM memories WHERE org_id=? AND operator='agent_observation' "
                                      "AND status='active'", (org_id,)).fetchone()["n"])

    def expired_notes(self, cutoff: str, *, after: tuple[str, int] | None, limit: int) -> list[tuple[int, Memory]]:
        """(rid, memory) of up to ``limit`` active memories whose ``expires_at`` is at or before ``cutoff``, in (expires_at,
        rid) order after the key ``after`` (from the start when None): the expiry sweep's page, read through the partial
        index ``idx_memories_expiry``."""
        sql, args = "SELECT * FROM memories WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= ?", [cutoff]
        if after is not None:
            sql += " AND (expires_at, rid) > (?, ?)"; args += [after[0], int(after[1])]
        rows = self._conn.execute(sql + " ORDER BY expires_at, rid LIMIT ?", [*args, int(limit)]).fetchall()
        return [(int(r["rid"]), row_memory(r)) for r in rows]

    def due_attestations(self, org_id: str, producer_id: str, *, cutoff: str, now: str, limit: int) -> list[dict[str, Any]]:
        """The producer's notes worth re-attesting: active and applied raw notes, not expired at ``now``, that an active
        derived memory rests on directly, last ingested or attested (``fresh_at``) at or before ``cutoff``; stalest first
        (then by id), each with its labels, times and the number of active derived memories resting on it."""
        rows = self._conn.execute(
            """SELECT m.memory_id, m.topic, m.slot, m.entity, e.created_at AS ingested_at, m.attested_at, m.expires_at,
                      MAX(e.created_at, COALESCE(m.attested_at, '')) AS fresh_at,
                      (SELECT COUNT(*) FROM lineage_edges l JOIN memories c ON c.memory_id = l.child_id
                       WHERE l.parent_id = m.memory_id AND c.status='active') AS dependents
               FROM memories m JOIN events e ON e.event_id = m.event_id
               WHERE m.org_id=? AND m.producer_id=? AND m.operator='agent_observation' AND m.status='active'
                 AND m.apply_seq IS NOT NULL AND (m.expires_at IS NULL OR m.expires_at > ?)
                 AND EXISTS (SELECT 1 FROM lineage_edges l JOIN memories c ON c.memory_id = l.child_id
                             WHERE l.parent_id = m.memory_id AND c.status='active')
                 AND MAX(e.created_at, COALESCE(m.attested_at, '')) <= ?
               ORDER BY fresh_at, m.memory_id LIMIT ?""", (org_id, producer_id, now, cutoff, int(limit))).fetchall()
        return [{k: r[k] for k in ("memory_id", "topic", "slot", "entity", "ingested_at", "attested_at", "expires_at",
                                   "dependents")} for r in rows]

    def current_derived(self, org_id: str, operator: str, scope: str, agg_key: str) -> Memory | None:
        r = self._conn.execute(
            "SELECT * FROM memories WHERE org_id=? AND operator=? AND scope=? AND agg_key=? AND status='active' ORDER BY rid DESC LIMIT 1",
            (org_id, operator, scope, agg_key)).fetchone()
        return row_memory(r) if r else None

    # reads that enumerate what aggregation has to reconcile (rule and registry events, the re-aggregation job): all
    # ordered, evidence read only once applied, prefixes compared with ``substr``
    def memory_org_ids(self) -> list[str]:
        return [r["org_id"] for r in self._conn.execute("SELECT DISTINCT org_id FROM memories ORDER BY org_id").fetchall()]

    def has_memories(self) -> bool:
        return self._conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone() is not None

    def active_conclusions(self, rule_id: str, org_id: str | None = None) -> list[Memory]:
        """The active conclusions of one rule, in one organization or in all of them."""
        out: list[Memory] = []
        for org in [org_id] if org_id is not None else self.memory_org_ids():
            rows = self._conn.execute("""SELECT * FROM memories WHERE org_id=? AND operator='slot_composition' AND status='active'
                                         AND rule_id=? ORDER BY scope, agg_key, memory_id""", (org, rule_id)).fetchall()
            out.extend(row_memory(r) for r in rows)
        return out

    def distinct_scope_entity(self, org_id: str, *, operators: Iterable[str], slots: Iterable[str],
                              topic_prefix: str | None = None) -> list[tuple[str, str | None]]:
        """Distinct (scope, entity) of the applied active memories that could fill one of ``slots``."""
        ops, ss = list(operators), list(slots)
        sql = (f"SELECT DISTINCT scope, entity FROM memories WHERE org_id=? AND status='active' AND apply_seq IS NOT NULL "
               f"AND operator IN ({','.join('?' * len(ops))}) AND slot IN ({','.join('?' * len(ss))})")
        args: list[Any] = [org_id, *ops, *ss]
        if topic_prefix:
            sql += " AND substr(topic, 1, ?)=?"; args += [len(topic_prefix), topic_prefix]
        return [(r["scope"], r["entity"]) for r in self._conn.execute(sql + " ORDER BY scope, entity", args).fetchall()]

    def distinct_scope_topic(self, org_id: str, *, operators: Iterable[str] = OPERATORS) -> list[tuple[str, str]]:
        """Distinct (scope, topic) of the applied active memories with a topic that consolidations take as evidence."""
        ops = list(operators)
        rows = self._conn.execute(f"""SELECT DISTINCT scope, topic FROM memories WHERE org_id=? AND status='active'
                                      AND apply_seq IS NOT NULL AND topic IS NOT NULL AND operator IN ({','.join('?' * len(ops))})
                                      ORDER BY scope, topic""", [org_id, *ops]).fetchall()
        return [(r["scope"], r["topic"]) for r in rows]

    def promotion_topics(self, org_id: str, unit: str) -> list[str]:
        """Topics whose consolidation at ``unit`` depends on how many child units it has: those it promotes now, and
        those of its direct children's consolidations (which it would promote with fewer children)."""
        rows = self._conn.execute("""SELECT DISTINCT topic FROM memories WHERE org_id=? AND operator='topic_consolidation'
                                     AND status='active' AND topic IS NOT NULL
                                     AND ((scope=? AND json_extract(metadata, '$.promoted_from') IS NOT NULL)
                                          OR (substr(scope, 1, ?)=? AND instr(substr(scope, ?), '/')=0))
                                     ORDER BY topic""",
                                  (org_id, unit, len(unit) + 1, unit + "/", len(unit) + 2)).fetchall()
        return [r["topic"] for r in rows]

    def active_derived_page(self, org_id: str, after: list[Any] | None, limit: int) -> list[tuple[list[Any], Memory]]:
        """The next ``limit`` active derived memories of an organization after the key ``after``, each with its key
        (layer rank team=1 … enterprise=5, scope, operator, agg_key or '', memory_id): lower layers first."""
        rank = "CASE layer " + " ".join(f"WHEN '{layer}' THEN {i}" for i, layer in enumerate(LAYERS) if i) + f" ELSE {len(LAYERS)} END"
        key = f"({rank}), scope, operator, COALESCE(agg_key, ''), memory_id"
        sql = (f"SELECT *, {rank} AS layer_rank, COALESCE(agg_key, '') AS agg_key_or_empty FROM memories WHERE org_id=? "
               "AND status='active' AND operator IN ('topic_consolidation', 'slot_composition')")
        args: list[Any] = [org_id]
        if after is not None:
            sql += f" AND ({key}) > (?, ?, ?, ?, ?)"; args += list(after)
        sql += f" ORDER BY {key} LIMIT ?"; args.append(int(limit))
        return [([r["layer_rank"], r["scope"], r["operator"], r["agg_key_or_empty"], r["memory_id"]], row_memory(r))
                for r in self._conn.execute(sql, args).fetchall()]

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

    # ------------------------------------------------------------------ integrity (reads)
    def integrity_of(self, ids: Iterable[str]) -> dict[str, tuple[str | None, str | None, str | None]]:
        """(digest, key id, origin) of each memory found, by id (see ``integrity.check_memory``)."""
        ids = list(dict.fromkeys(ids))
        out: dict[str, tuple[str | None, str | None, str | None]] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT memory_id, digest, digest_key_id, digest_origin FROM memories WHERE memory_id IN ({','.join('?' * len(chunk))})"
            for r in self._conn.execute(q, chunk).fetchall():
                out[r["memory_id"]] = (r["digest"], r["digest_key_id"], r["digest_origin"])
        return out

    def backfill_bound(self) -> int | None:
        """The rid of the first row that still carries a digest signed at insert (origin other than ``backfill``): the
        start-up backfill signs nothing at or after it (a later row without a digest had its digest removed).  Removing
        the digests of every row up to a given one moves the bound past it (SECURITY.md §3)."""
        row = self._conn.execute("SELECT rid FROM memories WHERE digest IS NOT NULL AND digest_origin IS NOT 'backfill' "
                                 "ORDER BY rid LIMIT 1").fetchone()
        return int(row["rid"]) if row else None

    def count_unsigned(self, from_rid: int) -> int:
        """Rows without a digest after ``from_rid``."""
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM memories WHERE digest IS NULL AND rid > ?",
                                      (from_rid,)).fetchone()["n"])

    # ------------------------------------------------------------------ lineage (reads)
    def parents_of(self, child_id: str) -> list[LineageEdge]:
        rows = self._conn.execute("SELECT * FROM lineage_edges WHERE child_id=? ORDER BY parent_id", (child_id,)).fetchall()
        return [row_edge(r) for r in rows]

    def parents_of_many(self, child_ids: Iterable[str]) -> dict[str, list[LineageEdge]]:
        """The lineage edges of each child, in ``parent_id`` order; a child without edges is absent."""
        ids = list(dict.fromkeys(child_ids))
        out: dict[str, list[LineageEdge]] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT * FROM lineage_edges WHERE child_id IN ({','.join('?' * len(chunk))}) ORDER BY child_id, parent_id"
            for r in self._conn.execute(q, chunk).fetchall():
                out.setdefault(r["child_id"], []).append(row_edge(r))
        return out

    def children_of(self, parent_id: str) -> list[LineageEdge]:
        rows = self._conn.execute("SELECT * FROM lineage_edges WHERE parent_id=? ORDER BY child_id", (parent_id,)).fetchall()
        return [row_edge(r) for r in rows]

    def dependents_of(self, memory_id: str, *, active_only: bool = False, max_nodes: int = 100_000) -> list[str]:
        """Memories that (transitively) derive from ``memory_id``, nearest first and at most ``max_nodes`` of them
        (a caller that gets exactly ``max_nodes`` must assume the walk was cut).  Each level is in ``child_id``
        order, so the order is the same on every node.  ``active_only`` walks through active memories only."""
        seen: list[str] = []
        frontier = [memory_id]
        visited = {memory_id}
        while frontier and len(seen) < max_nodes:
            found: set[str] = set()
            for i in range(0, len(frontier), 500):
                chunk = frontier[i:i + 500]
                marks = ",".join("?" * len(chunk))
                if active_only:
                    q = (f"SELECT e.child_id FROM lineage_edges e JOIN memories m ON m.memory_id=e.child_id "
                         f"WHERE e.parent_id IN ({marks}) AND m.status='active' ORDER BY e.child_id")
                else:
                    q = f"SELECT child_id FROM lineage_edges WHERE parent_id IN ({marks}) ORDER BY child_id"
                found.update(r["child_id"] for r in self._conn.execute(q, chunk).fetchall())
            frontier = sorted(found - visited)[: max_nodes - len(seen)]
            visited.update(frontier)
            seen.extend(frontier)
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

    def lifecycle_events(self, org_id: str, kinds: Iterable[str], *, since: Iterable[str] = ()) -> dict[str, list[tuple[str, str]]]:
        """(event id, status) of every event of ``kinds`` in an organization, by the memory its payload targets, in log
        order.  An event whose payload is not JSON or names no memory id is skipped.  With ``since`` (event ids), only the
        events logged from the first of them on: an event can target a memory only once the memory's own event is here."""
        ks = list(kinds)
        ids = list(dict.fromkeys(since))
        first: int | None = None
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            r = self._conn.execute(f"SELECT MIN(rid) AS rid FROM events WHERE event_id IN ({','.join('?' * len(chunk))})",
                                   chunk).fetchone()
            if r["rid"] is not None:
                first = r["rid"] if first is None else min(first, r["rid"])
        sql = f"""SELECT event_id, status, CASE WHEN json_valid(payload) THEN json_extract(payload, '$.memory_id') END AS target
                  FROM events WHERE org_id=? AND kind IN ({','.join('?' * len(ks))})"""
        args: list[Any] = [org_id, *ks]
        if first is not None:
            sql += " AND rid >= ?"; args.append(first)
        rows = self._conn.execute(sql + " ORDER BY rid", args).fetchall()
        out: dict[str, list[tuple[str, str]]] = {}
        for r in rows:
            if isinstance(r["target"], str):
                out.setdefault(r["target"], []).append((r["event_id"], r["status"]))
        return out

    def removal_events(self, org_id: str, agent_ids: Iterable[str]) -> dict[str, list[tuple[str, str]]]:
        """(event id, status) of every ``agent.removed`` event in an organization, by the agent its payload names, in log
        order, for the given agents.  An event whose payload is not JSON or names no agent id is skipped."""
        ids = set(agent_ids)
        if not ids:
            return {}
        rows = self._conn.execute("""SELECT event_id, status, CASE WHEN json_valid(payload) THEN json_extract(payload, '$.agent_id') END
                                     AS target FROM events WHERE org_id=? AND kind='agent.removed' ORDER BY rid""",
                                  (org_id,)).fetchall()
        out: dict[str, list[tuple[str, str]]] = {}
        for r in rows:
            if isinstance(r["target"], str) and r["target"] in ids:
                out.setdefault(r["target"], []).append((r["event_id"], r["status"]))
        return out

    def unapplied_updates(self, org_id: str, memory_ids: Iterable[str]) -> dict[str, list[str]]:
        """The producer updates of ``memory_ids`` still on their way through the log: by the id each one supersedes
        (``metadata.version_of``), the ids of the raw notes not applied yet whose event is pending or published (an update
        whose event was terminated as failed waits for nothing)."""
        ids = list(dict.fromkeys(memory_ids))
        out: dict[str, list[str]] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self._conn.execute(
                f"""SELECT m.memory_id, CASE WHEN json_valid(m.metadata) THEN json_extract(m.metadata, '$.version_of') END AS target
                    FROM memories m JOIN events e ON e.event_id = m.event_id
                    WHERE m.apply_seq IS NULL AND m.org_id=? AND m.operator='agent_observation'
                      AND e.status IN ('pending', 'published') AND target IN ({','.join('?' * len(chunk))})
                    ORDER BY m.rid""", [org_id, *chunk]).fetchall()
            for r in rows:
                out.setdefault(r["target"], []).append(r["memory_id"])
        return out

    def unapplied_events(self, org_id: str) -> int:
        """Events of an organization (and the deployment-wide ``_``) not applied yet, derived events aside."""
        return int(self._conn.execute("""SELECT COUNT(*) AS n FROM events WHERE status IN ('pending', 'published')
                                         AND kind != 'memory.derived' AND org_id IN (?, '_')""", (org_id,)).fetchone()["n"])

    def event_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM events GROUP BY status").fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def max_applied_seq(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(js_seq), 0) AS s FROM events WHERE status='applied'").fetchone()
        return int(row["s"])

    def max_apply_seq(self) -> int:
        """The newest position in memory apply order (0 when nothing was applied)."""
        return int(self._conn.execute("SELECT COALESCE(MAX(apply_seq), 0) AS s FROM memories").fetchone()["s"])

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

    def list_applied_rules(self, org_id: str) -> list[Rule]:
        """The enabled rules as of the last applied rule event: what aggregation evaluates."""
        rows = self._conn.execute("SELECT snapshot FROM applied_rules WHERE org_id IS NULL OR org_id=? ORDER BY rule_id",
                                  (org_id,)).fetchall()
        return [rule for rule in (snapshot_rule(r["snapshot"]) for r in rows) if rule.enabled]

    def get_applied_rule(self, rule_id: str) -> Rule | None:
        r = self._conn.execute("SELECT snapshot FROM applied_rules WHERE rule_id=?", (rule_id,)).fetchone()
        return snapshot_rule(r["snapshot"]) if r else None

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

    def memories_by_layer(self, org_id: str | None = None) -> dict[str, int]:
        by_layer = {layer: 0 for layer in LAYERS}
        sql, args = "SELECT layer, COUNT(*) AS n FROM memories WHERE status='active'", []
        if org_id is not None:
            sql += " AND org_id=?"; args.append(org_id)
        for r in self._conn.execute(sql + " GROUP BY layer", args).fetchall():
            by_layer[r["layer"]] = int(r["n"])
        return by_layer

    def stats(self) -> dict[str, Any]:
        c = self._conn
        by_layer = self.memories_by_layer()
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
            "expiry_overdue": int(c.execute("SELECT COUNT(*) AS n FROM memories WHERE status='active' AND expires_at IS NOT NULL "
                                            "AND expires_at <= ?", (now_iso(),)).fetchone()["n"]),
        }

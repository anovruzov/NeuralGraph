"""Split a domain subtree out of a shard into its own file (docs/mycelic/INGESTION.md §7.7).

States, persisted in s0 (``shard_migrations`` + ``shard_migration_records``), each step resumable from its checkpoint and
safe to interrupt at any point:

``planned``      the migration row and the target's ``shard_map`` row (``provisioning``) exist.
``provisioning`` the target file is created (``auto_vacuum=INCREMENTAL``, ``secure_delete``) with the full holder schema.
``copying``      the source records whose primary domain is in the partition are copied in batches, ordered by rowid, each
                 batch one target transaction: documents and versions, chunk messages and chats, memories (same ids and
                 embeddings), memory sources, entities and aliases, memory links, relations with their
                 ``relation_evidence``, ``ingest_records``/``ingest_versions``/``applied_events``, ``record_memories``,
                 ``record_entities``, memberships, their append-only history and examples. Writes keep going to the source
                 (routing is sticky).
``catching_up``  records changed since the copy started (``record_locator.change_seq``) and new records of the partition are
                 re-copied (a copy *replaces* the target's rows for the record), until fewer than 100 change in a pass.
``cutover``      under the source shard's write gate: the final delta, a full fingerprint sweep of every copied record, then
                 ONE s0 transaction moves ``record_locator`` to the target, makes the target ``active`` and updates
                 ``domain_shard_counts``. Writes waiting on the gate re-route to the target afterwards.
``cleanup``      the moved records' rows are purged from the source (content blanked, then the rows deleted, with
                 ``secure_delete`` on), FTS segments merged, the WAL truncated.
``done``

Nothing is lost or duplicated: a copy replaces, a re-run re-copies, the locator moves in one transaction, the source is
purged only after the switch. Deletions during the migration win: a purge of a source record changes its fingerprint
(and bumps its ``change_seq``), so the purged state is what reaches the target. Exports stay in s0; references keep
resolving through the locator.
"""
from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from typing import Any, Iterable, Sequence

from ..util import j, jl, now_iso, sha256
from .domains import UNCLASSIFIED
from .linking import drop_record_sync, recompute_relation_sync
from .shards import DEFAULT_SHARD, OPENABLE, ShardSet, ShardSpec, subtree

logger = logging.getLogger(__name__)

STATES = ("planned", "provisioning", "copying", "catching_up", "cutover", "cleanup", "done")
TERMINAL = ("done", "aborted", "failed")
BATCH_RECORDS = 200                 # §7.6: bulk transactions are capped at 200 records or 250 ms
BATCH_MS = 250.0
CATCHUP_THRESHOLD = 100
MAX_CATCHUP_PASSES = 20


class MigrationInterrupted(RuntimeError):
    """Raised by the test hook ``crash_at`` to stop a migration at a precise point, as a crash would."""


class SplitRefused(ValueError):
    def __init__(self, message: str, code: str = "refused") -> None:
        super().__init__(message)
        self.code = code


def _in(ids: Sequence[Any]) -> str:
    return ",".join("?" * len(ids))


def _chunks(xs: Sequence[Any], n: int = 500) -> Iterable[Sequence[Any]]:
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ---------------------------------------------------------------------------------------------- views
def _row_view(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["partition"] = jl(d.get("partition"), {})
    cp = jl(d.pop("checkpoint", None), {})
    d["checkpoint"] = {k: v for k, v in cp.items() if isinstance(v, (int, float, str)) or v is None}
    return d


def migration_view(conn: sqlite3.Connection, migration_id: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM shard_migrations WHERE migration_id=?", (migration_id,)).fetchone()
    return _row_view(r) if r is not None else None


def migration_rows(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    return [_row_view(r) for r in conn.execute("SELECT * FROM shard_migrations ORDER BY created_at DESC, migration_id LIMIT ?", (int(limit),))]


def unfinished(conn: sqlite3.Connection) -> list[str]:
    return [r["migration_id"] for r in conn.execute(f"SELECT migration_id FROM shard_migrations WHERE state NOT IN ({_in(TERMINAL)}) ORDER BY created_at",
                                                    TERMINAL)]


# ---------------------------------------------------------------------------------------------- per-record copy / evict
def record_memory_ids_sync(c: sqlite3.Connection, record_id: str) -> list[str]:
    ids = [r[0] for r in c.execute("SELECT memory_id FROM memories WHERE chat_id=? ORDER BY rid", (record_id,))]
    ids += [r[0] for r in c.execute("SELECT memory_id FROM record_memories WHERE record_id=?", (record_id,))]
    return list(dict.fromkeys(ids))


def record_message_ids_sync(c: sqlite3.Connection, record_id: str, mem_ids: Sequence[str]) -> list[str]:
    """The record's own provenance messages: its chunk messages (in its own chat or its conversation's chat)."""
    ids = [r[0] for r in c.execute("SELECT message_id FROM messages WHERE chat_id=? ORDER BY rid", (record_id,))]
    chunk_mems = [r[0] for r in c.execute("SELECT memory_id FROM memories WHERE chat_id=?", (record_id,))]
    for part in _chunks(chunk_mems):
        ids += [r[0] for r in c.execute(f"SELECT DISTINCT message_id FROM memory_sources WHERE memory_id IN ({_in(part)})", list(part))]
    return list(dict.fromkeys(ids))


_FP_SQL = """SELECT r.record_id, r.change_seq, r.deletion_status, r.last_ingested_at, r.current_order_key, r.content_hash, r.metadata_hash,
                    r.primary_domain_id, r.version_count, r.visibility, d.status AS doc_status, d.version AS doc_version, d.updated_at AS doc_updated,
                    (SELECT COUNT(*) || ':' || COALESCE(MAX(updated_at), '') FROM memories WHERE chat_id=r.record_id) AS mems,
                    (SELECT COUNT(*) || ':' || COALESCE(MAX(updated_at), '') FROM domain_memberships WHERE record_id=r.record_id) AS dms,
                    (SELECT COALESCE(MAX(id), 0) FROM domain_membership_history WHERE record_id=r.record_id) AS hist,
                    (SELECT COUNT(*) FROM relation_evidence WHERE record_id=r.record_id) AS rev,
                    (SELECT COUNT(*) FROM record_entities WHERE record_id=r.record_id) AS rents
             FROM ingest_records r LEFT JOIN documents d ON d.doc_id = r.record_id"""


def fingerprints_sync(c: sqlite3.Connection, record_ids: Sequence[str]) -> dict[str, str]:
    """A content-free digest of each record's state in one shard; any write to the record changes it."""
    out: dict[str, str] = {}
    for part in _chunks(list(record_ids)):
        for r in c.execute(f"{_FP_SQL} WHERE r.record_id IN ({_in(part)})", list(part)):
            out[r["record_id"]] = sha256(j({k: r[k] for k in r.keys()}))[:32]
    return out


def _copy_rows(s: sqlite3.Connection, t: sqlite3.Connection, table: str, where: str, args: Sequence[Any], *, mode: str = "INSERT OR IGNORE",
               exclude: Sequence[str] = ("rid",), order: str = "") -> int:
    rows = s.execute(f"SELECT * FROM {table} WHERE {where}{(' ORDER BY ' + order) if order else ''}", list(args)).fetchall()
    if not rows:
        return 0
    cols = [k for k in rows[0].keys() if k not in exclude]
    t.executemany(f"{mode} INTO {table}({', '.join(cols)}) VALUES ({_in(cols)})", [tuple(r[k] for k in cols) for r in rows])
    return len(rows)


def evict_record_sync(c: sqlite3.Connection, record_id: str, *, now: str, keep_applied: bool) -> dict[str, int]:
    """Remove every row of one record from one shard file without touching the control tables (no tombstone, no export
    scrub, no event: the record lives on in the other shard). Memories are blanked before they are deleted so nothing
    survives in FTS, and ``secure_delete`` overwrites the freed pages. Used for the source at cleanup and to replace a
    target copy during catch-up."""
    mem_ids = record_memory_ids_sync(c, record_id)
    msg_ids = record_message_ids_sync(c, record_id, mem_ids)
    entity_ids: set[str] = set()
    chats: set[str] = set()
    for part in _chunks(mem_ids):
        entity_ids.update(r[0] for r in c.execute(f"SELECT DISTINCT entity_id FROM memory_entities WHERE memory_id IN ({_in(part)})", list(part)))
        c.execute(f"""UPDATE memories SET status='retracted', text='', subject='', subject_name='', speaker='', embedding=NULL, embedding_dim=NULL,
                       metadata='{{}}', text_hash='', updated_at=? WHERE memory_id IN ({_in(part)})""", (now, *part))
        c.execute(f"DELETE FROM memory_entities WHERE memory_id IN ({_in(part)})", list(part))
        c.execute(f"DELETE FROM memory_sources WHERE memory_id IN ({_in(part)})", list(part))
        c.execute(f"DELETE FROM memory_links WHERE source_id IN ({_in(part)}) OR target_id IN ({_in(part)})", (*part, *part))
        c.execute(f"""DELETE FROM relations WHERE memory_id IN ({_in(part)}) AND observation_count <= 1
                       AND relation_id NOT IN (SELECT relation_id FROM relation_evidence)""", list(part))
        c.execute(f"UPDATE relations SET memory_id=NULL, message_id=NULL WHERE memory_id IN ({_in(part)})", list(part))
        c.execute(f"DELETE FROM memories WHERE memory_id IN ({_in(part)})", list(part))
    for part in _chunks(msg_ids):
        chats.update(r[0] for r in c.execute(f"SELECT DISTINCT chat_id FROM messages WHERE message_id IN ({_in(part)})", list(part)))
        c.execute(f"UPDATE relations SET message_id=NULL WHERE message_id IN ({_in(part)})", list(part))
        c.execute(f"DELETE FROM jobs WHERE ref_id IN ({_in(part)})", list(part))
        c.execute(f"DELETE FROM memory_sources WHERE message_id IN ({_in(part)})", list(part))
        c.execute(f"DELETE FROM messages WHERE message_id IN ({_in(part)})", list(part))
    for chat_id in chats:
        n = int(c.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat_id,)).fetchone()[0])
        if n == 0 and chat_id == record_id:
            c.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
        else:
            c.execute("""UPDATE chats SET message_count=?, updated_at=?,
                         participants=(SELECT COALESCE(json_group_array(DISTINCT speaker), '[]') FROM messages WHERE chat_id=?) WHERE chat_id=?""",
                      (n, now, chat_id, chat_id))
    entity_ids.update(r[0] for r in c.execute("SELECT entity_id FROM record_entities WHERE record_id=?", (record_id,)))
    rels = [r[0] for r in c.execute("SELECT relation_id FROM relation_evidence WHERE record_id=?", (record_id,))]
    for part in _chunks(rels):
        for r in c.execute(f"SELECT subject_id, object_id FROM relations WHERE relation_id IN ({_in(part)})", list(part)):
            entity_ids.update((r[0], r[1]))
    drop_record_sync(c, record_id, now)                       # its evidence goes; edges it touched are recomputed here
    c.execute("DELETE FROM record_entities WHERE record_id=?", (record_id,))
    c.execute("DELETE FROM record_memories WHERE record_id=?", (record_id,))
    c.execute("DELETE FROM domain_memberships WHERE record_id=?", (record_id,))
    c.execute("DELETE FROM domain_examples WHERE record_id=?", (record_id,))
    c.execute("DELETE FROM ingest_versions WHERE record_id=?", (record_id,))
    if not keep_applied:
        c.execute("DELETE FROM applied_events WHERE record_id=?", (record_id,))
    c.execute("DELETE FROM document_versions WHERE doc_id=?", (record_id,))
    c.execute("DELETE FROM documents WHERE doc_id=?", (record_id,))
    c.execute("DELETE FROM ingest_records WHERE record_id=?", (record_id,))
    for e in sorted(entity_ids):
        used = c.execute("SELECT 1 FROM memory_entities WHERE entity_id=? LIMIT 1", (e,)).fetchone() \
            or c.execute("SELECT 1 FROM relations WHERE (subject_id=? OR object_id=?) AND status<>'retracted' LIMIT 1", (e, e)).fetchone() \
            or c.execute("SELECT 1 FROM record_entities WHERE entity_id=? LIMIT 1", (e,)).fetchone() \
            or c.execute("SELECT 1 FROM entity_identities WHERE entity_id=? LIMIT 1", (e,)).fetchone()
        if used:
            c.execute("UPDATE entities SET mention_count=(SELECT COUNT(*) FROM memory_entities WHERE entity_id=?) WHERE entity_id=?", (e, e))
        else:
            c.execute("DELETE FROM relations WHERE (subject_id=? OR object_id=?) AND status='retracted' "
                      "AND relation_id NOT IN (SELECT relation_id FROM relation_evidence)", (e, e))
            c.execute("DELETE FROM entity_aliases WHERE entity_id=?", (e,))
            c.execute("DELETE FROM entities WHERE entity_id=?", (e,))
    return {"memories": len(mem_ids), "messages": len(msg_ids)}


def copy_record_sync(s: sqlite3.Connection, t: sqlite3.Connection, record_id: str, *, history_after: int, now: str) -> dict[str, Any]:
    """Copy one record from shard ``s`` into shard ``t`` (replacing what ``t`` has for it). Same ids everywhere; the
    target recomputes what is per-shard (relation status, entity mention counts, chat counters)."""
    evict_record_sync(t, record_id, now=now, keep_applied=True)
    mem_ids = record_memory_ids_sync(s, record_id)
    msg_ids = record_message_ids_sync(s, record_id, mem_ids)
    # chats first (messages reference them), then messages and memories in their original order
    chat_ids = {record_id}
    for part in _chunks(msg_ids):
        chat_ids.update(r[0] for r in s.execute(f"SELECT DISTINCT chat_id FROM messages WHERE message_id IN ({_in(part)})", list(part)))
    _copy_rows(s, t, "chats", f"chat_id IN ({_in(sorted(chat_ids))})", sorted(chat_ids))
    for part in _chunks(msg_ids):
        _copy_rows(s, t, "messages", f"message_id IN ({_in(part)})", part, order="rid")
    entity_ids: set[str] = set()
    for part in _chunks(mem_ids):
        _copy_rows(s, t, "memories", f"memory_id IN ({_in(part)})", part, order="rid")
        _copy_rows(s, t, "memory_sources", f"memory_id IN ({_in(part)})", part)
        entity_ids.update(r[0] for r in s.execute(f"SELECT DISTINCT entity_id FROM memory_entities WHERE memory_id IN ({_in(part)})", list(part)))
    entity_ids.update(r[0] for r in s.execute("SELECT entity_id FROM record_entities WHERE record_id=?", (record_id,)))
    rel_ids = [r[0] for r in s.execute("SELECT DISTINCT relation_id FROM relation_evidence WHERE record_id=?", (record_id,))]
    for part in _chunks(mem_ids):
        rel_ids += [r[0] for r in s.execute(f"SELECT relation_id FROM relations WHERE memory_id IN ({_in(part)})", list(part))]
    rel_ids = list(dict.fromkeys(rel_ids))
    for part in _chunks(rel_ids):
        for r in s.execute(f"SELECT subject_id, object_id FROM relations WHERE relation_id IN ({_in(part)})", list(part)):
            entity_ids.update((r[0], r[1]))
    ents = sorted(entity_ids)
    for part in _chunks(ents):
        _copy_rows(s, t, "entities", f"entity_id IN ({_in(part)})", part)
        _copy_rows(s, t, "entity_aliases", f"entity_id IN ({_in(part)})", part)
    for part in _chunks(mem_ids):
        _copy_rows(s, t, "memory_entities", f"memory_id IN ({_in(part)})", part)
        _copy_rows(s, t, "memory_links", f"source_id IN ({_in(part)}) OR target_id IN ({_in(part)})", (*part, *part))
    for part in _chunks(rel_ids):
        # one row per typed edge per shard: an edge the target already has keeps its row and gains this record's evidence
        for r in s.execute(f"SELECT * FROM relations WHERE relation_id IN ({_in(part)})", list(part)).fetchall():
            cols = list(r.keys())
            t.execute(f"INSERT INTO relations({', '.join(cols)}) VALUES ({_in(cols)}) ON CONFLICT(subject_id, predicate, object_id) DO NOTHING",
                      tuple(r[k] for k in cols))
    _copy_rows(s, t, "relation_evidence", "record_id=?", (record_id,), mode="INSERT OR REPLACE")
    touched: set[str] = set()
    for part in _chunks(rel_ids):
        for r in s.execute(f"SELECT relation_id, subject_id, predicate, object_id FROM relations WHERE relation_id IN ({_in(part)})", list(part)).fetchall():
            real = t.execute("SELECT relation_id FROM relations WHERE subject_id=? AND predicate=? AND object_id=?",
                             (r["subject_id"], r["predicate"], r["object_id"])).fetchone()
            if real is None:
                continue
            if real[0] != r["relation_id"]:
                # the target knew this edge under another id: its evidence follows the target's row
                t.execute("UPDATE OR REPLACE relation_evidence SET relation_id=? WHERE relation_id=? AND record_id=?", (real[0], r["relation_id"], record_id))
            touched.add(real[0])
    for rid in touched:
        if t.execute("SELECT 1 FROM relation_evidence WHERE relation_id=? LIMIT 1", (rid,)).fetchone():
            recompute_relation_sync(t, rid, now)
    for e in ents:
        t.execute("UPDATE entities SET mention_count=(SELECT COUNT(*) FROM memory_entities WHERE entity_id=?) WHERE entity_id=?", (e, e))
    _copy_rows(s, t, "documents", "doc_id=?", (record_id,), mode="INSERT OR REPLACE")
    _copy_rows(s, t, "document_versions", "doc_id=?", (record_id,), mode="INSERT OR REPLACE")
    _copy_rows(s, t, "ingest_records", "record_id=?", (record_id,), mode="INSERT OR REPLACE")
    _copy_rows(s, t, "ingest_versions", "record_id=?", (record_id,), mode="INSERT OR REPLACE")
    _copy_rows(s, t, "applied_events", "record_id=?", (record_id,), mode="INSERT OR IGNORE")
    _copy_rows(s, t, "record_memories", "record_id=?", (record_id,), mode="INSERT OR REPLACE")
    _copy_rows(s, t, "record_entities", "record_id=?", (record_id,), mode="INSERT OR REPLACE")
    _copy_rows(s, t, "domain_memberships", "record_id=?", (record_id,), mode="INSERT OR REPLACE")
    _copy_rows(s, t, "domain_examples", "record_id=?", (record_id,), mode="INSERT OR REPLACE")
    # the history is append-only on both sides: rows already copied are never copied twice
    hist = s.execute("SELECT * FROM domain_membership_history WHERE record_id=? AND id>? ORDER BY id", (record_id, int(history_after))).fetchall()
    if hist:
        cols = [k for k in hist[0].keys() if k != "id"]
        t.executemany(f"INSERT INTO domain_membership_history({', '.join(cols)}) VALUES ({_in(cols)})", [tuple(r[k] for k in cols) for r in hist])
    for chat_id in chat_ids:
        t.execute("""UPDATE chats SET message_count=(SELECT COUNT(*) FROM messages WHERE chat_id=?),
                     participants=(SELECT COALESCE(json_group_array(DISTINCT speaker), '[]') FROM messages WHERE chat_id=?) WHERE chat_id=?""",
                  (chat_id, chat_id, chat_id))
    return {"history_max": max([int(history_after)] + [int(r["id"]) for r in hist]), "memories": len(mem_ids), "messages": len(msg_ids)}


def domain_counts_sync(c: sqlite3.Connection) -> dict[str, int]:
    return {r[0]: int(r[1]) for r in c.execute("""SELECT dm.domain_id, COUNT(*) FROM domain_memberships dm JOIN ingest_records r ON r.record_id = dm.record_id
                                                   WHERE dm.status='active' AND r.deletion_status IN ('live', 'redacted') GROUP BY dm.domain_id""")}


# ---------------------------------------------------------------------------------------------- the migration
class SplitMigration:
    def __init__(self, shards: ShardSet, migration_id: str) -> None:
        self.shards = shards
        self.migration_id = migration_id
        self.crash_at: str | None = None
        self.batch_records = BATCH_RECORDS
        self.catchup_threshold = CATCHUP_THRESHOLD

    # ------------------------------------------------------------------ plan / load
    @classmethod
    async def plan(cls, shards: ShardSet, domain_ids: Sequence[str], *, requested_by: str, from_shard: str = DEFAULT_SHARD) -> "SplitMigration":
        tax = shards.control.taxonomy()
        ids: list[str] = []
        for d in domain_ids or []:
            rid = tax.resolve(str(d).strip())
            if rid and rid not in ids:
                ids.append(rid)
        if not ids:
            raise SplitRefused("name at least one domain to split", "no_domains")
        if len(ids) > 8:
            raise SplitRefused("a split moves at most 8 domain subtrees", "too_many_domains")
        for d in ids:
            dom = tax.domains.get(d)
            if dom is None or d == UNCLASSIFIED:
                raise SplitRefused(f"unknown domain {d!r}", "unknown_domain")
            if dom.status != "active":
                raise SplitRefused(f"domain {d!r} is deprecated", "deprecated_domain")
        if any(ids[i] in tax.ancestors(ids[k]) for i in range(len(ids)) for k in range(len(ids)) if i != k):
            raise SplitRefused("the domains overlap (one is inside another)", "overlap")
        src = shards.spec(from_shard)
        if src is None or src.status != "active":
            raise SplitRefused(f"shard {from_shard} is not active", "source_inactive")
        if src.domain_ids and not all(any(r == d or r in tax.ancestors(d) for r in src.domain_ids) for d in ids):
            raise SplitRefused("a data shard can only be split into subtrees of its own partition", "outside_partition")
        new_tree = subtree(tax, ids)
        for s in shards.specs(statuses=OPENABLE):
            if s.shard_id in (from_shard, DEFAULT_SHARD) or not s.domain_ids:
                continue
            if new_tree & subtree(tax, s.domain_ids):
                raise SplitRefused(f"domain subtree already has its own shard ({s.shard_id})", "overlap")
        if len(shards.specs(statuses=OPENABLE)) >= shards.thresholds.max_shards:
            raise SplitRefused(f"this holder already has {shards.thresholds.max_shards} shards", "quota")
        if unfinished(shards.conn):
            raise SplitRefused("another split of this holder is still running", "busy")
        from ..evidence.migrate import latest_holder_version
        mid = "smg_" + secrets.token_hex(8)
        target = "shd_" + secrets.token_hex(8)
        now = now_iso()

        def fn(c):
            if c.execute(f"SELECT 1 FROM shard_migrations WHERE state NOT IN ({_in(TERMINAL)}) LIMIT 1", TERMINAL).fetchone():
                raise SplitRefused("another split of this holder is still running", "busy")
            ordinal = int(c.execute("SELECT COALESCE(MAX(ordinal), 0) + 1 FROM shard_map").fetchone()[0])
            c.execute("""INSERT INTO shard_map(shard_id, ordinal, file_name, domain_ids, status, schema_version, created_at, updated_at)
                         VALUES (?, ?, ?, ?, 'provisioning', ?, ?, ?)""", (target, ordinal, f"{target}.db", j(ids), latest_holder_version(), now, now))
            c.execute("""INSERT INTO shard_migrations(migration_id, kind, from_shard, to_shard, partition, state, checkpoint, requested_by, created_at, updated_at)
                         VALUES (?, 'split', ?, ?, ?, 'planned', '{}', ?, ?, ?)""", (mid, from_shard, target, j({"domain_ids": ids}), requested_by, now, now))
        await shards.control.store.run_in_tx(fn)
        return cls(shards, mid)

    @classmethod
    def load(cls, shards: ShardSet, migration_id: str) -> "SplitMigration":
        if migration_view(shards.conn, migration_id) is None:
            raise KeyError(migration_id)
        return cls(shards, migration_id)

    # ------------------------------------------------------------------ state
    def row(self) -> dict[str, Any]:
        r = self.shards.conn.execute("SELECT * FROM shard_migrations WHERE migration_id=?", (self.migration_id,)).fetchone()
        if r is None:
            raise KeyError(self.migration_id)
        d = dict(r)
        d["partition"] = jl(d["partition"], {})
        d["checkpoint"] = jl(d["checkpoint"], {})
        return d

    def view(self) -> dict[str, Any]:
        return migration_view(self.shards.conn, self.migration_id) or {}

    @property
    def domain_ids(self) -> list[str]:
        return list(self.row()["partition"].get("domain_ids") or [])

    def _hook(self, point: str) -> None:
        if self.crash_at == point:
            self.crash_at = None
            raise MigrationInterrupted(point)

    async def _set(self, state: str, *, checkpoint: dict[str, Any] | None = None, extra_sql: Any = None, **cols: Any) -> None:
        now = now_iso()

        def fn(c):
            sets, args = ["state=?", "updated_at=?"], [state, now]
            if checkpoint is not None:
                sets.append("checkpoint=?")
                args.append(j(checkpoint))
            for k, v in cols.items():
                sets.append(f"{k}=?")
                args.append(v)
            c.execute(f"UPDATE shard_migrations SET {', '.join(sets)} WHERE migration_id=?", (*args, self.migration_id))
            if extra_sql is not None:
                extra_sql(c)
        await self.shards.control.store.run_in_tx(fn)

    async def _checkpoint(self, cp: dict[str, Any]) -> None:
        await self.shards.control.store.run_in_tx(lambda c: c.execute("UPDATE shard_migrations SET checkpoint=?, updated_at=? WHERE migration_id=?",
                                                                      (j(cp), now_iso(), self.migration_id)))

    # ------------------------------------------------------------------ run
    async def run(self, *, crash_at: str | None = None, batch_records: int | None = None) -> dict[str, Any]:
        """Advance until ``done`` (or a terminal state). Re-entrant: running it again resumes from the checkpoint."""
        if crash_at is not None:
            self.crash_at = crash_at
        if batch_records:
            self.batch_records = int(batch_records)
        await self.shards.recover()
        handlers = {"planned": self._planned, "provisioning": self._provision, "copying": self._copy, "catching_up": self._catch_up,
                    "cutover": self._cutover, "cleanup": self._cleanup}
        while True:
            state = self.row()["state"]
            if state in TERMINAL:
                return self.view()
            try:
                await handlers[state]()
            except MigrationInterrupted:
                raise
            except Exception as exc:
                code = getattr(exc, "code", None) or type(exc).__name__
                await self.shards.control.store.run_in_tx(lambda c: c.execute(
                    "UPDATE shard_migrations SET error_code=?, updated_at=? WHERE migration_id=?", (str(code)[:64], now_iso(), self.migration_id)))
                raise

    # ------------------------------------------------------------------ steps
    async def _planned(self) -> None:
        self._hook("planned")
        await self._set("provisioning", started_at=now_iso(), error_code=None)

    def _target_spec(self) -> ShardSpec:
        spec = self.shards.spec(self.row()["to_shard"])
        if spec is None:
            raise KeyError(self.row()["to_shard"])
        return spec

    def _source(self) -> Any:
        return self.shards.store(self.row()["from_shard"])

    def _target(self) -> Any:
        return self.shards.store(self.row()["to_shard"])

    async def _provision(self) -> None:
        self.shards.open_new(self._target_spec())             # idempotent: an existing file is reopened (migrations rerun as no-ops)
        self._hook("provisioning")
        seq_row = self.shards.conn.execute("SELECT value FROM holder_meta WHERE key='ingest_change_seq'").fetchone()
        seq = int(seq_row["value"]) if seq_row else 0
        await self._set("copying", checkpoint={"copy_start_seq": seq, "caught_up_seq": seq, "last_rowid": 0, "copied": 0, "passes": 0,
                                               "writer_id": self.shards.writer_id})

    def _partition_filter(self) -> tuple[str, list[Any]]:
        tax = self.shards.control.taxonomy()
        roots = self.domain_ids
        tree = sorted(subtree(tax, roots))
        clauses = [f"primary_domain_id IN ({_in(tree)})"] + ["substr(primary_domain_id, 1, ?) = ?" for _ in roots]
        args: list[Any] = list(tree)
        for r in roots:
            args += [len(r) + 1, r + "."]
        return "(" + " OR ".join(clauses) + ")", args

    async def _copy_batch(self, record_ids: list[str]) -> list[dict[str, Any]]:
        """Copy (or re-copy) records into the target in one target transaction; returns their new checkpoint rows."""
        if not record_ids:
            return []
        src, dst = self._source(), self._target()
        known = {r["record_id"]: r for r in self.shards.conn.execute(
            f"SELECT record_id, history_max FROM shard_migration_records WHERE migration_id=? AND record_id IN ({_in(record_ids)})",
            (self.migration_id, *record_ids))}
        fps = fingerprints_sync(src.store._conn, record_ids)
        out: list[dict[str, Any]] = []

        def fn(c):
            now = now_iso()
            for rid in record_ids:
                rk = src.store._conn.execute("SELECT record_key FROM ingest_records WHERE record_id=?", (rid,)).fetchone()
                if rk is None:
                    continue
                res = copy_record_sync(src.store._conn, c, rid, history_after=int((known.get(rid) or {"history_max": 0})["history_max"]), now=now)
                out.append({"record_id": rid, "record_key": rk[0], "fingerprint": fps.get(rid, ""), "history_max": res["history_max"]})
        await dst.store.run_in_tx(fn)
        return out

    async def _record_copies(self, rows: list[dict[str, Any]], cp: dict[str, Any]) -> None:
        def fn(c):
            c.executemany("""INSERT INTO shard_migration_records(migration_id, record_id, record_key, state, fingerprint, history_max)
                             VALUES (?, ?, ?, 'copied', ?, ?) ON CONFLICT(migration_id, record_id) DO UPDATE SET fingerprint=excluded.fingerprint,
                             history_max=excluded.history_max""",
                          [(self.migration_id, r["record_id"], r["record_key"], r["fingerprint"], r["history_max"]) for r in rows])
            c.execute("UPDATE shard_migrations SET checkpoint=?, updated_at=? WHERE migration_id=?", (j(cp), now_iso(), self.migration_id))
        await self.shards.control.store.run_in_tx(fn)

    async def _copy(self) -> None:
        cp = dict(self.row()["checkpoint"])
        where, args = self._partition_filter()
        src = self._source()
        while True:
            rows = src.store._conn.execute(f"SELECT rowid, record_id FROM ingest_records WHERE rowid>? AND {where} ORDER BY rowid LIMIT ?",
                                           (int(cp.get("last_rowid") or 0), *args, self.batch_records)).fetchall()
            if not rows:
                break
            t0 = time.perf_counter()
            batch: list[str] = []
            last = int(cp.get("last_rowid") or 0)
            for r in rows:                                  # cap each transaction by size and (roughly) by time
                batch.append(r["record_id"])
                last = int(r["rowid"])
                if (time.perf_counter() - t0) * 1000 > BATCH_MS:
                    break
            copied = await self._copy_batch(batch)
            self._hook("copying:batch")
            cp.update(last_rowid=last, copied=int(cp.get("copied") or 0) + len(copied))
            await self._record_copies(copied, cp)
        self._hook("copying")
        await self._set("catching_up", checkpoint=cp)

    def _changed_since(self, seq: int) -> tuple[list[str], int]:
        """Records of the partition (or already copied) that changed after ``seq``, and the highest change seen."""
        src_id = self.row()["from_shard"]
        rows = self.shards.conn.execute("SELECT record_id, change_seq FROM record_locator WHERE shard_id=? AND change_seq>? ORDER BY change_seq",
                                        (src_id, int(seq))).fetchall()
        if not rows:
            return [], int(seq)
        top = max(int(r["change_seq"]) for r in rows)
        ids = [r["record_id"] for r in rows]
        mine = {r[0] for part in _chunks(ids) for r in self.shards.conn.execute(
            f"SELECT record_id FROM shard_migration_records WHERE migration_id=? AND record_id IN ({_in(part)})", (self.migration_id, *part))}
        where, args = self._partition_filter()
        src = self._source()
        for part in _chunks(ids):
            mine.update(r[0] for r in src.store._conn.execute(f"SELECT record_id FROM ingest_records WHERE record_id IN ({_in(part)}) AND {where}",
                                                              (*part, *args)))
        return [i for i in ids if i in mine], top

    async def _recopy(self, ids: list[str], cp: dict[str, Any], *, point: str) -> int:
        """Re-copy the records whose source state differs from what was copied (or that were never copied)."""
        if not ids:
            return 0
        stored = {r["record_id"]: r["fingerprint"] for part in _chunks(ids) for r in self.shards.conn.execute(
            f"SELECT record_id, fingerprint FROM shard_migration_records WHERE migration_id=? AND record_id IN ({_in(part)})", (self.migration_id, *part))}
        current = fingerprints_sync(self._source().store._conn, ids)
        stale = [i for i in ids if i in current and stored.get(i) != current[i]]
        n = 0
        for part in _chunks(stale, self.batch_records):
            copied = await self._copy_batch(list(part))
            self._hook(point)
            n += len(copied)
            await self._record_copies(copied, cp)
        return n

    async def _catch_up(self) -> None:
        cp = dict(self.row()["checkpoint"])
        while True:
            ids, top = self._changed_since(int(cp.get("caught_up_seq") or 0))
            n = await self._recopy(ids, cp, point="catching_up:batch")
            cp.update(caught_up_seq=top, passes=int(cp.get("passes") or 0) + 1, recopied=int(cp.get("recopied") or 0) + n)
            await self._checkpoint(cp)
            if len(ids) < self.catchup_threshold or int(cp["passes"]) >= MAX_CATCHUP_PASSES:
                break
        self._hook("catching_up")
        await self._set("cutover", checkpoint=cp)

    async def _cutover(self) -> None:
        self._hook("cutover:start")
        row = self.row()
        cp = dict(row["checkpoint"])
        src_id, dst_id = row["from_shard"], row["to_shard"]
        src, dst = self._source(), self._target()
        async with self.shards.gate(src_id):
            # the final delta: everything changed since the last pass, then every copied record whose source state moved on
            ids, top = self._changed_since(int(cp.get("caught_up_seq") or 0))
            await self._recopy(ids, cp, point="cutover:batch")
            where, args = self._partition_filter()
            partition = [r[0] for r in src.store._conn.execute(f"SELECT record_id FROM ingest_records WHERE {where}", args)]
            copied = [r[0] for r in self.shards.conn.execute("SELECT record_id FROM shard_migration_records WHERE migration_id=?", (self.migration_id,))]
            await self._recopy(list(dict.fromkeys(partition + copied)), cp, point="cutover:batch")
            cp.update(caught_up_seq=max(top, int(cp.get("caught_up_seq") or 0)))
            self._hook("cutover")
            moved = [r[0] for r in self.shards.conn.execute("SELECT record_id FROM shard_migration_records WHERE migration_id=? AND state='copied'",
                                                            (self.migration_id,))]
            counts = domain_counts_sync(dst.store._conn)
            now = now_iso()
            cp.update(moved=len(moved), writer_id=self.shards.writer_id)

            def switch(c):
                for part in _chunks(moved):
                    c.execute(f"UPDATE record_locator SET shard_id=?, updated_at=? WHERE shard_id=? AND record_id IN ({_in(part)})",
                              (dst_id, now, src_id, *part))
                    c.execute(f"UPDATE deletion_tombstones SET shard_id=? WHERE record_id IN ({_in(part)})", (dst_id, *part))
                c.execute("UPDATE shard_migration_records SET state='moved' WHERE migration_id=? AND state='copied'", (self.migration_id,))
                c.execute("UPDATE shard_map SET status='active', updated_at=? WHERE shard_id=?", (now, dst_id))
                c.execute("DELETE FROM domain_shard_counts WHERE shard_id=?", (dst_id,))
                for d, n in counts.items():
                    c.execute("INSERT OR REPLACE INTO domain_shard_counts(domain_id, shard_id, records) VALUES (?, ?, ?)", (d, dst_id, n))
                c.execute("UPDATE shard_migrations SET state='cleanup', checkpoint=?, cutover_at=?, updated_at=? WHERE migration_id=?",
                          (j(cp), now, now, self.migration_id))
            await self.shards.control.store.run_in_tx(switch)
            self._hook("cutover:switched")
        self.shards.invalidate([src_id, dst_id])

    async def _cleanup(self) -> None:
        row = self.row()
        cp = dict(row["checkpoint"])
        src_id = row["from_shard"]
        src = self._source()
        while True:
            ids = [r[0] for r in self.shards.conn.execute("SELECT record_id FROM shard_migration_records WHERE migration_id=? AND state='moved' LIMIT ?",
                                                          (self.migration_id, self.batch_records))]
            if not ids:
                break
            async with self.shards.gate(src_id):
                def fn(c, ids=ids):
                    now = now_iso()
                    for rid in ids:
                        # a record still routed to the source is not moved (never happens after the switch; guarded anyway)
                        loc = self.shards.conn.execute("SELECT shard_id FROM record_locator WHERE record_id=?", (rid,)).fetchone()
                        if loc is None or loc[0] == src_id:
                            continue
                        evict_record_sync(c, rid, now=now, keep_applied=src_id == DEFAULT_SHARD)
                await src.store.run_in_tx(fn)
            self._hook("cleanup:batch")
            cp["cleaned"] = int(cp.get("cleaned") or 0) + len(ids)
            await self.shards.control.store.run_in_tx(lambda c, ids=ids: (
                c.execute(f"UPDATE shard_migration_records SET state='cleaned' WHERE migration_id=? AND record_id IN ({_in(ids)})", (self.migration_id, *ids)),
                c.execute("UPDATE shard_migrations SET checkpoint=?, updated_at=? WHERE migration_id=?", (j(cp), now_iso(), self.migration_id))))
        # purged bytes leave the file: merge FTS segments, truncate the WAL, return free pages where the file allows it
        await src.store.optimize_fts()
        mode = src.store._conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        if int(mode) == 2:
            src.store._conn.execute("PRAGMA incremental_vacuum")
        counts = domain_counts_sync(src.store._conn)
        self._hook("cleanup")

        def done(c):
            c.execute("DELETE FROM domain_shard_counts WHERE shard_id=?", (src_id,))
            for d, n in counts.items():
                c.execute("INSERT OR REPLACE INTO domain_shard_counts(domain_id, shard_id, records) VALUES (?, ?, ?)", (d, src_id, n))
            c.execute("UPDATE shard_migrations SET state='done', checkpoint=?, finished_at=?, updated_at=? WHERE migration_id=?",
                      (j(cp), now_iso(), now_iso(), self.migration_id))
        await self.shards.control.store.run_in_tx(done)
        self.shards.invalidate([src_id, row["to_shard"]])

    # ------------------------------------------------------------------ abort (before cutover only)
    async def abort(self) -> dict[str, Any]:
        row = self.row()
        if row["state"] in TERMINAL:
            return self.view()
        if row["state"] in ("cutover", "cleanup") and row.get("cutover_at"):
            raise SplitRefused("the split is past its cutover; run the reverse migration instead", "past_cutover")
        dst_id = row["to_shard"]
        spec = self.shards.spec(dst_id)
        await self.shards.close_shard(dst_id)
        if spec is not None:
            try:
                path = self.shards.path_for(spec.file_name)
                for suffix in ("", "-wal", "-shm", ".lock"):
                    p = path.with_name(path.name + suffix)
                    if p.exists():
                        p.unlink()
            except Exception:
                logger.exception("could not remove the target file of aborted split %s", self.migration_id)

        def fn(c):
            c.execute("UPDATE shard_map SET status='retired', updated_at=? WHERE shard_id=?", (now_iso(), dst_id))
            c.execute("DELETE FROM shard_migration_records WHERE migration_id=?", (self.migration_id,))
            c.execute("UPDATE shard_migrations SET state='aborted', finished_at=?, updated_at=? WHERE migration_id=?", (now_iso(), now_iso(), self.migration_id))
        await self.shards.control.store.run_in_tx(fn)
        return self.view()

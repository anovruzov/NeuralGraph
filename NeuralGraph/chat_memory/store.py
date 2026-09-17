"""SQLite persistence for cross-chat memory: raw messages, derived memories, entities,
relations, links, provenance, and a durable job queue for the background worker.

Design notes
------------
* One ``sqlite3`` connection, WAL mode, used only from the asyncio event-loop thread. Every
  write goes through :meth:`_tx` (``BEGIN IMMEDIATE`` ... ``COMMIT``) under an ``asyncio.Lock`` so
  multi-statement writes are atomic and never interleave across ``await`` points.
* The worker computes an extraction *plan* with the LLM first and then commits it with
  :meth:`apply_plan` in ONE transaction that also marks the messages processed and the jobs done.
  A crash before that commit leaves nothing behind, so a retried job starts clean.
* Job leasing preserves per-chat order (a job is only leasable when no earlier-``seq`` job of the
  same chat is still queued/leased) while different chats proceed in parallel.
* Memory text is indexed in FTS5 (``memories_fts``) when the SQLite build has it; otherwise the
  keyword channel falls back to an in-process BM25 (rank_bm25) over active memories.
* ``revision`` increases on every memory/entity/relation write; retrieval caches key on it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import struct
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import (
    ChatMessage,
    Entity,
    Job,
    Memory,
    MemoryLink,
    Relation,
    iso,
    new_id,
    now_iso,
    parse_iso,
    utcnow,
)
from .textutil import content_hash, norm_entity, normalize_ws, tokenize

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id       TEXT PRIMARY KEY,
    title         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    participants  TEXT NOT NULL DEFAULT '[]',
    message_count INTEGER NOT NULL DEFAULT 0,
    metadata      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    rid          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL UNIQUE,
    chat_id      TEXT NOT NULL REFERENCES chats(chat_id),
    seq          INTEGER NOT NULL,
    speaker      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL,
    sent_at      TEXT,
    ingested_at  TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'pending',
    processed_at TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}',
    UNIQUE(chat_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_seq ON messages(chat_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

CREATE TABLE IF NOT EXISTS memories (
    rid                  INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id            TEXT NOT NULL UNIQUE,
    text                 TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    subject              TEXT NOT NULL,
    subject_name         TEXT NOT NULL,
    speaker              TEXT NOT NULL,
    chat_id              TEXT NOT NULL,
    importance           REAL NOT NULL,
    confidence           REAL NOT NULL,
    event_time           TEXT,
    event_time_precision TEXT NOT NULL DEFAULT 'none',
    observed_at          TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'active',
    superseded_by        TEXT,
    version              INTEGER NOT NULL DEFAULT 1,
    text_hash            TEXT NOT NULL,
    embedding            BLOB,
    embedding_dim        INTEGER,
    access_count         INTEGER NOT NULL DEFAULT 0,
    last_accessed_at     TEXT,
    metadata             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject, status);
CREATE INDEX IF NOT EXISTS idx_memories_chat ON memories(chat_id);
CREATE INDEX IF NOT EXISTS idx_memories_text_hash ON memories(text_hash);
CREATE INDEX IF NOT EXISTS idx_memories_observed ON memories(observed_at);

CREATE TABLE IF NOT EXISTS memory_sources (
    memory_id  TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (memory_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_sources_msg ON memory_sources(message_id);

CREATE TABLE IF NOT EXISTS entities (
    entity_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT '',
    mention_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at  TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_entities_mentions ON entities(mention_count DESC);

CREATE TABLE IF NOT EXISTS entity_aliases (
    alias     TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id)
);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'mention',
    PRIMARY KEY (memory_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON memory_entities(entity_id);

CREATE TABLE IF NOT EXISTS relations (
    relation_id       TEXT PRIMARY KEY,
    subject_id        TEXT NOT NULL,
    predicate         TEXT NOT NULL,
    object_id         TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 0.5,
    chat_id           TEXT NOT NULL,
    memory_id         TEXT,
    message_id        TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    observation_count INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    metadata          TEXT NOT NULL DEFAULT '{}',
    UNIQUE (subject_id, predicate, object_id)
);
CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_id);

CREATE TABLE IF NOT EXISTS memory_links (
    source_id  TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    link_type  TEXT NOT NULL,
    weight     REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, link_type)
);
CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    chat_id      TEXT,
    ref_id       TEXT,
    seq          INTEGER,
    dedupe_key   TEXT UNIQUE,
    status       TEXT NOT NULL DEFAULT 'queued',
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at TEXT NOT NULL,
    leased_until TEXT,
    worker_id    TEXT,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    finished_at  TEXT,
    payload      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_avail ON jobs(status, available_at);
CREATE INDEX IF NOT EXISTS idx_jobs_chat_seq ON jobs(chat_id, seq);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    ref    TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
);
"""

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text, content='memories', content_rowid='rid', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.rid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rid, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.rid, new.text);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, content='messages', content_rowid='rid', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.rid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rid, old.text);
END;
"""


def _pack(vec: list[float] | None) -> bytes | None:
    if vec is None:
        return None
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes | None) -> list[float] | None:
    if blob is None:
        return None
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _j(v: Any) -> str:
    return json.dumps(v if v is not None else {}, ensure_ascii=False, sort_keys=True)


def _jl(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


class LostLease(RuntimeError):
    """Raised when a worker tries to commit a plan for jobs it no longer holds (lease expired and re-leased)."""


FUNCTIONAL_PREDICATES = frozenset({
    "lives_in", "works_at", "works_as", "married_to", "employed_by", "age", "birthday", "born_in", "based_in",
    "relationship_status", "current_project", "current_job", "nationality", "primary_language", "timezone",
    "studies_at", "job_title", "phone", "email", "home_city", "hometown", "partner_of", "dating",
})


class ChatMemoryStore:
    """SQLite-backed store. See module docstring."""

    def __init__(self, db_path: str | Path = "~/.neuralgraph/chat_memory.db") -> None:
        self.db_path = ":memory:" if str(db_path) == ":memory:" else str(Path(db_path).expanduser())
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self.revision = 0
        self.has_fts = False
        self._bm25_cache: tuple[int, list[str], Any] | None = None
        self._init_schema()

    # ------------------------------------------------------------------ setup / lifecycle
    def _init_schema(self) -> None:
        c = self._conn
        if self.db_path != ":memory:":
            try:
                c.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=5000")
        c.executescript(_DDL)
        try:
            c.executescript(_FTS_DDL)
            self.has_fts = True
        except sqlite3.OperationalError as exc:  # FTS5 not compiled in
            logger.warning("FTS5 unavailable (%s); keyword search falls back to in-process BM25", exc)
            self.has_fts = False
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

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Synchronous ``BEGIN IMMEDIATE`` transaction (call only while holding ``self._lock``)."""
        c = self._conn
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def _bump(self) -> None:
        self.revision += 1
        self._bm25_cache = None

    # ------------------------------------------------------------------ meta
    async def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with self._lock:
            with self._tx() as c:
                c.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))

    # ------------------------------------------------------------------ chats & messages
    def _row_message(self, r: sqlite3.Row) -> ChatMessage:
        return ChatMessage(
            message_id=r["message_id"], chat_id=r["chat_id"], seq=r["seq"], speaker=r["speaker"], role=r["role"],
            text=r["text"], sent_at=r["sent_at"], ingested_at=r["ingested_at"], content_hash=r["content_hash"],
            status=r["status"], processed_at=r["processed_at"], metadata=_jl(r["metadata"], {}),
        )

    async def upsert_chat(self, chat_id: str, *, title: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        async with self._lock:
            with self._tx() as c:
                self._upsert_chat_sync(c, chat_id, title=title, metadata=metadata)

    def _upsert_chat_sync(self, c: sqlite3.Connection, chat_id: str, *, title: str | None = None,
                          metadata: dict[str, Any] | None = None) -> None:
        now = now_iso()
        c.execute(
            """INSERT INTO chats(chat_id, title, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 title = COALESCE(excluded.title, chats.title),
                 updated_at = excluded.updated_at,
                 metadata = CASE WHEN excluded.metadata = '{}' THEN chats.metadata ELSE excluded.metadata END""",
            (chat_id, title, now, now, _j(metadata)),
        )

    async def add_message(
        self,
        chat_id: str,
        speaker: str,
        text: str,
        *,
        role: str = "",
        sent_at: str | None = None,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        enqueue: bool = True,
        debounce_seconds: float = 0.0,
        max_attempts: int = 5,
    ) -> tuple[ChatMessage, bool]:
        """Append a message to a chat and (optionally) enqueue its extraction job.

        Idempotent: re-adding the same (chat, speaker, text, sent_at, explicit message_id) returns the
        stored row with ``created=False``. Never calls the LLM.
        """
        text = text if isinstance(text, str) else str(text)
        speaker = normalize_ws(speaker) or "unknown"
        chash = content_hash(chat_id, speaker, text, sent_at or "", message_id or "")
        async with self._lock:
            with self._tx() as c:
                existing = c.execute("SELECT * FROM messages WHERE content_hash=?", (chash,)).fetchone()
                if existing is not None:
                    return self._row_message(existing), False
                if message_id is not None:
                    dup = c.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
                    if dup is not None:
                        return self._row_message(dup), False
                self._upsert_chat_sync(c, chat_id)
                row = c.execute("SELECT COALESCE(MAX(seq), -1) + 1 AS nxt FROM messages WHERE chat_id=?", (chat_id,)).fetchone()
                seq = int(row["nxt"])
                mid = message_id or new_id("msg")
                now = now_iso()
                c.execute(
                    """INSERT INTO messages(message_id, chat_id, seq, speaker, role, text, sent_at, ingested_at,
                                            content_hash, status, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (mid, chat_id, seq, speaker, role or "", text, sent_at, now, chash, _j(metadata)),
                )
                c.execute(
                    """UPDATE chats SET message_count = message_count + 1, updated_at = ?,
                       participants = (SELECT json_group_array(DISTINCT speaker) FROM messages WHERE chat_id = ?)
                       WHERE chat_id = ?""",
                    (now, chat_id, chat_id),
                )
                if enqueue:
                    avail = iso(utcnow() + timedelta(seconds=max(0.0, debounce_seconds)))
                    self._enqueue_sync(c, "extract", chat_id=chat_id, ref_id=mid, seq=seq,
                                       dedupe_key=f"extract:{mid}", available_at=avail, max_attempts=max_attempts)
                msg = self._row_message(c.execute("SELECT * FROM messages WHERE message_id=?", (mid,)).fetchone())
        return msg, True

    async def get_message(self, message_id: str) -> ChatMessage | None:
        r = self._conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
        return self._row_message(r) if r else None

    async def get_messages(self, chat_id: str, *, after_seq: int | None = None, before_seq: int | None = None,
                           limit: int | None = None, newest_first: bool = False) -> list[ChatMessage]:
        sql = "SELECT * FROM messages WHERE chat_id=?"
        args: list[Any] = [chat_id]
        if after_seq is not None:
            sql += " AND seq > ?"; args.append(after_seq)
        if before_seq is not None:
            sql += " AND seq < ?"; args.append(before_seq)
        sql += " ORDER BY seq " + ("DESC" if newest_first else "ASC")
        if limit is not None:
            sql += " LIMIT ?"; args.append(int(limit))
        return [self._row_message(r) for r in self._conn.execute(sql, args).fetchall()]

    async def get_messages_by_ids(self, message_ids: Iterable[str]) -> list[ChatMessage]:
        ids = [m for m in dict.fromkeys(message_ids)]
        if not ids:
            return []
        out: list[ChatMessage] = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT * FROM messages WHERE message_id IN ({','.join('?' * len(chunk))})"
            out.extend(self._row_message(r) for r in self._conn.execute(q, chunk).fetchall())
        order = {m: i for i, m in enumerate(ids)}
        out.sort(key=lambda m: order.get(m.message_id, 0))
        return out

    async def context_before(self, chat_id: str, seq: int, n: int) -> list[ChatMessage]:
        """The ``n`` messages preceding ``seq`` in the chat, oldest first."""
        if n <= 0:
            return []
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE chat_id=? AND seq<? ORDER BY seq DESC LIMIT ?", (chat_id, seq, n)
        ).fetchall()
        return [self._row_message(r) for r in reversed(rows)]

    async def set_message_status(self, message_ids: Iterable[str], status: str) -> None:
        ids = list(message_ids)
        if not ids:
            return
        now = now_iso() if status in ("processed", "skipped", "failed") else None
        async with self._lock:
            with self._tx() as c:
                c.executemany("UPDATE messages SET status=?, processed_at=COALESCE(?, processed_at) WHERE message_id=?",
                              [(status, now, m) for m in ids])

    async def list_chats(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM chats ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["participants"] = _jl(d.get("participants"), [])
            d["metadata"] = _jl(d.get("metadata"), {})
            out.append(d)
        return out

    async def message_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM messages GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    async def search_messages(self, query: str, limit: int = 20, chat_id: str | None = None) -> list[tuple[ChatMessage, float]]:
        """Keyword search over raw messages (FTS5; falls back to LIKE)."""
        toks = tokenize(query)
        if not toks:
            return []
        if self.has_fts:
            match = " OR ".join(f'"{t}"' for t in dict.fromkeys(tokenize(query, do_stem=False)))
            sql = ("SELECT m.*, bm25(messages_fts) AS s FROM messages_fts f JOIN messages m ON m.rid = f.rowid "
                   "WHERE messages_fts MATCH ?")
            args: list[Any] = [match]
            if chat_id:
                sql += " AND m.chat_id = ?"; args.append(chat_id)
            sql += " ORDER BY s LIMIT ?"; args.append(limit)
            rows = self._conn.execute(sql, args).fetchall()
            return [(self._row_message(r), -float(r["s"])) for r in rows]
        sql = "SELECT * FROM messages WHERE " + " OR ".join(["text LIKE ?"] * len(toks))
        args = [f"%{t}%" for t in toks]
        if chat_id:
            sql += " AND chat_id = ?"; args.append(chat_id)
        sql += " LIMIT ?"; args.append(limit)
        return [(self._row_message(r), 1.0) for r in self._conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------------ jobs
    def _row_job(self, r: sqlite3.Row) -> Job:
        return Job(
            job_id=r["job_id"], kind=r["kind"], chat_id=r["chat_id"], ref_id=r["ref_id"], seq=r["seq"],
            dedupe_key=r["dedupe_key"], status=r["status"], attempts=r["attempts"], max_attempts=r["max_attempts"],
            available_at=r["available_at"], leased_until=r["leased_until"], worker_id=r["worker_id"],
            last_error=r["last_error"], created_at=r["created_at"], updated_at=r["updated_at"],
            finished_at=r["finished_at"], payload=_jl(r["payload"], {}),
        )

    def _enqueue_sync(self, c: sqlite3.Connection, kind: str, *, chat_id: str | None, ref_id: str | None,
                      seq: int | None, dedupe_key: str | None, available_at: str | None,
                      max_attempts: int = 5, payload: dict[str, Any] | None = None) -> int | None:
        now = now_iso()
        cur = c.execute(
            """INSERT INTO jobs(kind, chat_id, ref_id, seq, dedupe_key, status, available_at, max_attempts,
                                created_at, updated_at, payload)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
               ON CONFLICT(dedupe_key) DO NOTHING""",
            (kind, chat_id, ref_id, seq, dedupe_key, available_at or now, max_attempts, now, now, _j(payload)),
        )
        return cur.lastrowid if cur.rowcount else None

    async def enqueue_job(self, kind: str, *, chat_id: str | None = None, ref_id: str | None = None,
                          seq: int | None = None, dedupe_key: str | None = None, available_at: str | None = None,
                          max_attempts: int = 5, payload: dict[str, Any] | None = None) -> int | None:
        async with self._lock:
            with self._tx() as c:
                return self._enqueue_sync(c, kind, chat_id=chat_id, ref_id=ref_id, seq=seq, dedupe_key=dedupe_key,
                                          available_at=available_at, max_attempts=max_attempts, payload=payload)

    async def lease_job(self, worker_id: str, lease_seconds: float, kinds: Iterable[str] | None = None,
                        batch_size: int = 1) -> list[Job]:
        """Lease the oldest runnable job, plus (for ``extract`` jobs) up to ``batch_size-1`` directly
        following queued messages of the same chat (contiguous ``seq``), so one LLM pass covers a turn.

        Per-chat ordering: a job is runnable only when no earlier-seq job of the same chat is
        queued or leased. Returns ``[]`` when nothing is runnable.
        """
        now = now_iso()
        until = iso(utcnow() + timedelta(seconds=lease_seconds))
        kind_list = list(kinds) if kinds else None
        async with self._lock:
            with self._tx() as c:
                sql = """SELECT j.* FROM jobs j
                         WHERE j.status = 'queued' AND j.available_at <= ?
                           AND (j.chat_id IS NULL OR NOT EXISTS (
                                 SELECT 1 FROM jobs j2
                                 WHERE j2.chat_id = j.chat_id AND j2.job_id <> j.job_id
                                   AND j2.status IN ('queued', 'leased') AND j2.seq < j.seq))"""
                args: list[Any] = [now]
                if kind_list:
                    sql += f" AND j.kind IN ({','.join('?' * len(kind_list))})"; args.extend(kind_list)
                sql += " ORDER BY j.available_at, j.job_id LIMIT 1"
                head = c.execute(sql, args).fetchone()
                if head is None:
                    return []
                leased = [head]
                if head["kind"] == "extract" and batch_size > 1 and head["chat_id"] is not None:
                    rows = c.execute(
                        """SELECT * FROM jobs WHERE kind='extract' AND chat_id=? AND status='queued'
                           AND seq > ? AND available_at <= ? ORDER BY seq LIMIT ?""",
                        (head["chat_id"], head["seq"], now, batch_size - 1),
                    ).fetchall()
                    prev = head["seq"]
                    for r in rows:
                        if r["seq"] != prev + 1:
                            break
                        leased.append(r)
                        prev = r["seq"]
                ids = [r["job_id"] for r in leased]
                c.executemany(
                    "UPDATE jobs SET status='leased', leased_until=?, worker_id=?, attempts=attempts+1, updated_at=? WHERE job_id=?",
                    [(until, worker_id, now, i) for i in ids],
                )
                out = [self._row_job(c.execute("SELECT * FROM jobs WHERE job_id=?", (i,)).fetchone()) for i in ids]
        return out

    async def heartbeat(self, job_ids: Iterable[str | int], lease_seconds: float) -> None:
        ids = [int(i) for i in job_ids]
        if not ids:
            return
        until = iso(utcnow() + timedelta(seconds=lease_seconds))
        async with self._lock:
            with self._tx() as c:
                c.executemany("UPDATE jobs SET leased_until=?, updated_at=? WHERE job_id=? AND status='leased'",
                              [(until, now_iso(), i) for i in ids])

    async def complete_jobs(self, job_ids: Iterable[int]) -> None:
        ids = [int(i) for i in job_ids]
        if not ids:
            return
        async with self._lock:
            with self._tx() as c:
                self._complete_jobs_sync(c, ids)

    def _complete_jobs_sync(self, c: sqlite3.Connection, ids: list[int]) -> None:
        now = now_iso()
        c.executemany("UPDATE jobs SET status='done', finished_at=?, updated_at=?, leased_until=NULL WHERE job_id=?",
                      [(now, now, i) for i in ids])

    async def fail_jobs(self, job_ids: Iterable[int], error: str, backoff_seconds: float) -> dict[int, str]:
        """Mark leased jobs failed: requeue with backoff, or ``dead`` when attempts are exhausted.

        Returns ``{job_id: new_status}``.
        """
        ids = [int(i) for i in job_ids]
        result: dict[int, str] = {}
        if not ids:
            return result
        now = now_iso()
        avail = iso(utcnow() + timedelta(seconds=max(0.0, backoff_seconds)))
        err = (error or "")[:2000]
        async with self._lock:
            with self._tx() as c:
                for i in ids:
                    r = c.execute("SELECT attempts, max_attempts, ref_id, kind FROM jobs WHERE job_id=?", (i,)).fetchone()
                    if r is None:
                        continue
                    if r["attempts"] >= r["max_attempts"]:
                        c.execute("UPDATE jobs SET status='dead', last_error=?, finished_at=?, updated_at=?, leased_until=NULL WHERE job_id=?",
                                  (err, now, now, i))
                        if r["kind"] == "extract" and r["ref_id"]:
                            c.execute("UPDATE messages SET status='failed', processed_at=? WHERE message_id=? AND status IN ('pending','processing')",
                                      (now, r["ref_id"]))
                        result[i] = "dead"
                    else:
                        c.execute("UPDATE jobs SET status='queued', last_error=?, available_at=?, updated_at=?, leased_until=NULL, worker_id=NULL WHERE job_id=?",
                                  (err, avail, now, i))
                        result[i] = "queued"
        return result

    async def requeue_expired_leases(self) -> int:
        """Return leased jobs whose lease expired (worker crash) to the queue."""
        now = now_iso()
        async with self._lock:
            with self._tx() as c:
                cur = c.execute(
                    "UPDATE jobs SET status='queued', leased_until=NULL, worker_id=NULL, updated_at=?, "
                    "last_error=COALESCE(last_error, 'lease expired') WHERE status='leased' AND leased_until < ?",
                    (now, now),
                )
                n = cur.rowcount
                if n:
                    c.execute("UPDATE messages SET status='pending' WHERE status='processing' AND message_id IN "
                              "(SELECT ref_id FROM jobs WHERE status='queued' AND kind='extract')")
        return n

    async def job_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    async def pending_jobs(self, kinds: Iterable[str] | None = None) -> int:
        kind_list = list(kinds) if kinds else None
        sql = "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','leased')"
        args: list[Any] = []
        if kind_list:
            sql += f" AND kind IN ({','.join('?' * len(kind_list))})"; args.extend(kind_list)
        return int(self._conn.execute(sql, args).fetchone()["n"])

    async def next_available_at(self) -> str | None:
        r = self._conn.execute("SELECT MIN(available_at) AS a FROM jobs WHERE status='queued'").fetchone()
        return r["a"] if r else None

    async def get_job(self, job_id: int) -> Job | None:
        r = self._conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row_job(r) if r else None

    async def list_jobs(self, status: str | None = None, limit: int = 100) -> list[Job]:
        if status:
            rows = self._conn.execute("SELECT * FROM jobs WHERE status=? ORDER BY job_id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY job_id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_job(r) for r in rows]

    async def retry_dead_jobs(self, limit: int = 1000) -> int:
        now = now_iso()
        async with self._lock:
            with self._tx() as c:
                cur = c.execute(
                    "UPDATE jobs SET status='queued', attempts=0, available_at=?, updated_at=?, finished_at=NULL "
                    "WHERE job_id IN (SELECT job_id FROM jobs WHERE status='dead' ORDER BY job_id LIMIT ?)",
                    (now, now, limit),
                )
                c.execute("UPDATE messages SET status='pending' WHERE status='failed' AND message_id IN "
                          "(SELECT ref_id FROM jobs WHERE status='queued' AND kind='extract')")
                return cur.rowcount

    async def prune_jobs(self, older_than_days: float = 7.0, statuses: tuple[str, ...] = ("done",)) -> int:
        cutoff = iso(utcnow() - timedelta(days=older_than_days))
        async with self._lock:
            with self._tx() as c:
                cur = c.execute(
                    f"DELETE FROM jobs WHERE status IN ({','.join('?' * len(statuses))}) AND finished_at < ?",
                    (*statuses, cutoff),
                )
                return cur.rowcount

    # ------------------------------------------------------------------ memories
    def _row_memory(self, r: sqlite3.Row, with_embedding: bool = True) -> Memory:
        return Memory(
            memory_id=r["memory_id"], text=r["text"], kind=r["kind"], subject=r["subject"], subject_name=r["subject_name"],
            speaker=r["speaker"], chat_id=r["chat_id"], importance=r["importance"], confidence=r["confidence"],
            event_time=r["event_time"], event_time_precision=r["event_time_precision"], observed_at=r["observed_at"],
            created_at=r["created_at"], updated_at=r["updated_at"], status=r["status"], superseded_by=r["superseded_by"],
            version=r["version"], text_hash=r["text_hash"],
            embedding=_unpack(r["embedding"]) if with_embedding else None,
            access_count=r["access_count"], last_accessed_at=r["last_accessed_at"], metadata=_jl(r["metadata"], {}),
        )

    def _insert_memory_sync(self, c: sqlite3.Connection, m: Memory, source_message_ids: Iterable[str],
                            entity_ids: Iterable[str]) -> None:
        c.execute(
            """INSERT INTO memories(memory_id, text, kind, subject, subject_name, speaker, chat_id, importance, confidence,
                                    event_time, event_time_precision, observed_at, created_at, updated_at, status,
                                    superseded_by, version, text_hash, embedding, embedding_dim, access_count,
                                    last_accessed_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m.memory_id, m.text, m.kind, m.subject, m.subject_name, m.speaker, m.chat_id, float(m.importance),
             float(m.confidence), m.event_time, m.event_time_precision or "none", m.observed_at, m.created_at,
             m.updated_at, m.status, m.superseded_by, int(m.version), m.text_hash or content_hash(m.text.lower()),
             _pack(m.embedding), len(m.embedding) if m.embedding else None, int(m.access_count), m.last_accessed_at,
             _j(m.metadata)),
        )
        c.executemany("INSERT OR IGNORE INTO memory_sources(memory_id, message_id) VALUES (?, ?)",
                      [(m.memory_id, s) for s in dict.fromkeys(source_message_ids)])
        c.executemany("INSERT OR IGNORE INTO memory_entities(memory_id, entity_id) VALUES (?, ?)",
                      [(m.memory_id, e) for e in dict.fromkeys(entity_ids) if e])

    async def insert_memory(self, m: Memory, *, source_message_ids: Iterable[str] = (), entity_ids: Iterable[str] = ()) -> None:
        async with self._lock:
            with self._tx() as c:
                self._insert_memory_sync(c, m, source_message_ids, entity_ids)
            self._bump()

    async def get_memory(self, memory_id: str, *, with_sources: bool = False) -> Memory | None:
        r = self._conn.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        if r is None:
            return None
        m = self._row_memory(r)
        if with_sources:
            self._attach_sources_sync([m])
        return m

    def _attach_sources_sync(self, mems: list[Memory]) -> None:
        for m in mems:
            m.source_message_ids = [r["message_id"] for r in self._conn.execute(
                "SELECT ms.message_id FROM memory_sources ms JOIN messages msg ON msg.message_id = ms.message_id "
                "WHERE ms.memory_id=? ORDER BY msg.chat_id, msg.seq", (m.memory_id,)).fetchall()]
            m.entity_ids = [r["entity_id"] for r in self._conn.execute(
                "SELECT entity_id FROM memory_entities WHERE memory_id=? ORDER BY entity_id", (m.memory_id,)).fetchall()]

    async def get_memories_by_ids(self, memory_ids: Iterable[str], *, with_sources: bool = False) -> list[Memory]:
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return []
        out: list[Memory] = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self._conn.execute(f"SELECT * FROM memories WHERE memory_id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            out.extend(self._row_memory(r) for r in rows)
        order = {m: i for i, m in enumerate(ids)}
        out.sort(key=lambda m: order.get(m.memory_id, 0))
        if with_sources:
            self._attach_sources_sync(out)
        return out

    async def find_active_by_text_hash(self, text_hash: str) -> Memory | None:
        r = self._conn.execute("SELECT * FROM memories WHERE text_hash=? AND status='active' LIMIT 1", (text_hash,)).fetchone()
        return self._row_memory(r) if r else None

    async def update_memory(self, memory_id: str, **fields: Any) -> None:
        allowed = {"text", "kind", "subject", "subject_name", "importance", "confidence", "event_time",
                   "event_time_precision", "status", "superseded_by", "version", "embedding", "metadata", "observed_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update fields {sorted(bad)}")
        if not fields:
            return
        sets, args = [], []
        for k, v in fields.items():
            if k == "embedding":
                sets.append("embedding=?"); args.append(_pack(v))
                sets.append("embedding_dim=?"); args.append(len(v) if v else None)
            elif k == "metadata":
                sets.append("metadata=?"); args.append(_j(v))
            elif k == "text":
                sets.append("text=?"); args.append(v)
                sets.append("text_hash=?"); args.append(content_hash(normalize_ws(v).lower()))
            else:
                sets.append(f"{k}=?"); args.append(v)
        sets.append("updated_at=?"); args.append(now_iso())
        args.append(memory_id)
        async with self._lock:
            with self._tx() as c:
                c.execute(f"UPDATE memories SET {', '.join(sets)} WHERE memory_id=?", args)
            self._bump()

    def _supersede_sync(self, c: sqlite3.Connection, old_id: str, new_id_: str, link_type: str = "supersedes") -> None:
        now = now_iso()
        c.execute("UPDATE memories SET status='superseded', superseded_by=?, updated_at=? WHERE memory_id=? AND status='active'",
                  (new_id_, now, old_id))
        c.execute("INSERT OR REPLACE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, 1.0, ?)",
                  (new_id_, old_id, link_type, now))
        # relations that were grounded only in the old memory follow it into history
        c.execute("UPDATE relations SET status='superseded', updated_at=? WHERE memory_id=? AND status='active' "
                  "AND observation_count <= 1", (now, old_id))

    async def supersede(self, old_id: str, new_id_: str, link_type: str = "supersedes") -> None:
        async with self._lock:
            with self._tx() as c:
                self._supersede_sync(c, old_id, new_id_, link_type)
            self._bump()

    async def retract(self, memory_id: str, reason: str = "") -> bool:
        async with self._lock:
            with self._tx() as c:
                cur = c.execute("UPDATE memories SET status='retracted', updated_at=? WHERE memory_id=? AND status<>'retracted'",
                                (now_iso(), memory_id))
                c.execute("UPDATE relations SET status='retracted', updated_at=? WHERE memory_id=?", (now_iso(), memory_id))
                if reason:
                    self._log_sync(c, "retract", memory_id, {"reason": reason})
            self._bump()
            return cur.rowcount > 0

    async def add_link(self, source_id: str, target_id: str, link_type: str, weight: float = 1.0) -> None:
        async with self._lock:
            with self._tx() as c:
                c.execute("INSERT OR REPLACE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
                          (source_id, target_id, link_type, float(weight), now_iso()))
            self._bump()

    async def links_for(self, memory_id: str) -> list[MemoryLink]:
        rows = self._conn.execute(
            "SELECT * FROM memory_links WHERE source_id=? OR target_id=? ORDER BY weight DESC, created_at", (memory_id, memory_id)
        ).fetchall()
        return [MemoryLink(r["source_id"], r["target_id"], r["link_type"], r["weight"], r["created_at"]) for r in rows]

    async def add_memory_sources(self, memory_id: str, message_ids: Iterable[str]) -> None:
        async with self._lock:
            with self._tx() as c:
                c.executemany("INSERT OR IGNORE INTO memory_sources(memory_id, message_id) VALUES (?, ?)",
                              [(memory_id, s) for s in dict.fromkeys(message_ids)])

    async def touch_access(self, memory_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return
        now = now_iso()
        async with self._lock:
            with self._tx() as c:
                c.executemany("UPDATE memories SET access_count=access_count+1, last_accessed_at=? WHERE memory_id=?",
                              [(now, i) for i in ids])

    async def list_memories(
        self,
        *,
        status: str | None = "active",
        subject: str | None = None,
        speaker: str | None = None,
        chat_id: str | None = None,
        kinds: Iterable[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        order: str = "observed_at DESC",
        with_sources: bool = False,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status=?"; args.append(status)
        if subject:
            sql += " AND subject=?"; args.append(norm_entity(subject))
        if speaker:
            sql += " AND speaker=?"; args.append(speaker)
        if chat_id:
            sql += " AND chat_id=?"; args.append(chat_id)
        kl = list(kinds) if kinds else None
        if kl:
            sql += f" AND kind IN ({','.join('?' * len(kl))})"; args.extend(kl)
        if since:
            sql += " AND COALESCE(event_time, observed_at) >= ?"; args.append(since)
        if until:
            sql += " AND COALESCE(event_time, observed_at) <= ?"; args.append(until)
        if order not in ("observed_at DESC", "observed_at ASC", "importance DESC", "created_at DESC", "created_at ASC"):
            raise ValueError("unsupported order")
        sql += f" ORDER BY {order}, rid"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"; args.extend([int(limit), int(offset)])
        out = [self._row_memory(r) for r in self._conn.execute(sql, args).fetchall()]
        if with_sources:
            self._attach_sources_sync(out)
        return out

    async def memory_counts(self) -> dict[str, Any]:
        by_status = {r["status"]: r["n"] for r in self._conn.execute("SELECT status, COUNT(*) AS n FROM memories GROUP BY status")}
        by_kind = {r["kind"]: r["n"] for r in self._conn.execute("SELECT kind, COUNT(*) AS n FROM memories WHERE status='active' GROUP BY kind")}
        by_subject = {r["subject"]: r["n"] for r in self._conn.execute(
            "SELECT subject, COUNT(*) AS n FROM memories WHERE status='active' GROUP BY subject ORDER BY n DESC LIMIT 20")}
        return {"by_status": by_status, "active_by_kind": by_kind, "active_by_subject": by_subject}

    async def active_embedding_rows(self) -> list[tuple[str, str, str, str, str, str | None, list[float]]]:
        """(memory_id, subject, speaker, chat_id, kind, event_or_observed, embedding) for active memories with embeddings."""
        rows = self._conn.execute(
            "SELECT memory_id, subject, speaker, chat_id, kind, COALESCE(event_time, observed_at) AS t, embedding "
            "FROM memories WHERE status='active' AND embedding IS NOT NULL ORDER BY rid"
        ).fetchall()
        return [(r["memory_id"], r["subject"], r["speaker"], r["chat_id"], r["kind"], r["t"], _unpack(r["embedding"])) for r in rows]

    async def index_rows(self, *, status: str = "active") -> list[dict[str, Any]]:
        """Light-weight rows for the retrieval index (embedding may be None)."""
        rows = self._conn.execute(
            "SELECT memory_id, subject, speaker, chat_id, kind, importance, observed_at, event_time, embedding "
            "FROM memories WHERE status=? ORDER BY rid", (status,)
        ).fetchall()
        return [{"memory_id": r["memory_id"], "subject": r["subject"], "speaker": r["speaker"], "chat_id": r["chat_id"],
                 "kind": r["kind"], "importance": r["importance"], "observed_at": r["observed_at"],
                 "event_time": r["event_time"], "embedding": _unpack(r["embedding"])} for r in rows]

    async def entity_ids_for_memories(self, memory_ids: Iterable[str]) -> dict[str, list[str]]:
        ids = list(dict.fromkeys(memory_ids))
        out: dict[str, list[str]] = {i: [] for i in ids}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in self._conn.execute(
                f"SELECT memory_id, entity_id FROM memory_entities WHERE memory_id IN ({','.join('?' * len(chunk))}) ORDER BY entity_id", chunk
            ):
                out[r["memory_id"]].append(r["entity_id"])
        return out

    async def history(self, memory_id: str, limit: int = 20) -> list[Memory]:
        """Chain of memories this one superseded (newest first), following 'supersedes'/'contradicts' links."""
        out: list[Memory] = []
        cur = memory_id
        seen = {cur}
        while len(out) < limit:
            r = self._conn.execute(
                "SELECT target_id FROM memory_links WHERE source_id=? AND link_type IN ('supersedes','contradicts') ORDER BY created_at DESC LIMIT 1",
                (cur,)).fetchone()
            if r is None or r["target_id"] in seen:
                break
            m = await self.get_memory(r["target_id"])
            if m is None:
                break
            out.append(m)
            seen.add(m.memory_id)
            cur = m.memory_id
        return out

    async def memories_missing_embedding(self, limit: int = 100) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE status='active' AND embedding IS NULL ORDER BY rid LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_memory(r) for r in rows]

    async def keyword_candidates(self, query: str, limit: int = 50, *, status: str = "active") -> list[tuple[str, float]]:
        """Keyword channel over memory text: FTS5 bm25 when available, else in-process BM25."""
        toks = tokenize(query)
        if not toks:
            return []
        if self.has_fts:
            match = " OR ".join(f'"{t}"' for t in dict.fromkeys(tokenize(query, do_stem=False)))
            rows = self._conn.execute(
                "SELECT m.memory_id AS id, bm25(memories_fts) AS s FROM memories_fts f JOIN memories m ON m.rid = f.rowid "
                "WHERE memories_fts MATCH ? AND m.status = ? ORDER BY s LIMIT ?",
                (match, status, limit),
            ).fetchall()
            return [(r["id"], -float(r["s"])) for r in rows]
        # fallback: rank_bm25 over active memories, cached per revision
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:  # pragma: no cover - dependency present in this repo
            return []
        cache = self._bm25_cache
        if cache is None or cache[0] != self.revision:
            rows = self._conn.execute("SELECT memory_id, text FROM memories WHERE status=? ORDER BY rid", (status,)).fetchall()
            ids = [r["memory_id"] for r in rows]
            docs = [tokenize(r["text"]) for r in rows]
            cache = (self.revision, ids, BM25Okapi(docs) if docs else None)
            self._bm25_cache = cache
        _, ids, bm25 = cache
        if not ids or bm25 is None:
            return []
        scores = bm25.get_scores(toks)
        ranked = sorted(((ids[i], float(s)) for i, s in enumerate(scores) if s > 0), key=lambda x: -x[1])
        return ranked[:limit]

    # ------------------------------------------------------------------ entities & relations
    def _row_entity(self, r: sqlite3.Row) -> Entity:
        return Entity(entity_id=r["entity_id"], name=r["name"], type=r["type"], mention_count=r["mention_count"],
                      first_seen_at=r["first_seen_at"], last_seen_at=r["last_seen_at"], metadata=_jl(r["metadata"], {}))

    def _upsert_entity_sync(self, c: sqlite3.Connection, entity_id: str, name: str, etype: str, seen_at: str | None,
                            mentions: int = 1, aliases: Iterable[str] = ()) -> None:
        now = now_iso()
        seen = seen_at or now
        c.execute(
            """INSERT INTO entities(entity_id, name, type, mention_count, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE SET
                 mention_count = entities.mention_count + excluded.mention_count,
                 type = CASE WHEN entities.type = '' THEN excluded.type ELSE entities.type END,
                 name = CASE WHEN length(excluded.name) > length(entities.name) AND lower(excluded.name) = lower(entities.name)
                             THEN excluded.name ELSE entities.name END,
                 first_seen_at = CASE WHEN entities.first_seen_at IS NULL OR excluded.first_seen_at < entities.first_seen_at
                                      THEN excluded.first_seen_at ELSE entities.first_seen_at END,
                 last_seen_at = CASE WHEN entities.last_seen_at IS NULL OR excluded.last_seen_at > entities.last_seen_at
                                     THEN excluded.last_seen_at ELSE entities.last_seen_at END""",
            (entity_id, name, etype or "", int(mentions), seen, seen),
        )
        for a in aliases:
            a = norm_entity(a)
            if a and a != entity_id:
                c.execute("INSERT OR IGNORE INTO entity_aliases(alias, entity_id) VALUES (?, ?)", (a, entity_id))

    async def upsert_entity(self, entity_id: str, name: str, etype: str = "", *, seen_at: str | None = None,
                            mentions: int = 1, aliases: Iterable[str] = ()) -> Entity:
        async with self._lock:
            with self._tx() as c:
                self._upsert_entity_sync(c, entity_id, name, etype, seen_at, mentions, aliases)
            self._bump()
        return await self.get_entity(entity_id)  # type: ignore[return-value]

    async def get_entity(self, entity_id: str) -> Entity | None:
        r = self._conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        if r is None:
            return None
        e = self._row_entity(r)
        e.aliases = [a["alias"] for a in self._conn.execute("SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias", (entity_id,))]
        return e

    async def resolve_alias(self, alias: str) -> str | None:
        key = norm_entity(alias)
        if not key:
            return None
        r = self._conn.execute("SELECT entity_id FROM entities WHERE entity_id=?", (key,)).fetchone()
        if r:
            return r["entity_id"]
        r = self._conn.execute("SELECT entity_id FROM entity_aliases WHERE alias=?", (key,)).fetchone()
        return r["entity_id"] if r else None

    async def add_alias(self, alias: str, entity_id: str) -> None:
        async with self._lock:
            with self._tx() as c:
                c.execute("INSERT OR REPLACE INTO entity_aliases(alias, entity_id) VALUES (?, ?)", (norm_entity(alias), entity_id))
            self._bump()

    async def top_entities(self, limit: int = 100, *, etype: str | None = None) -> list[Entity]:
        if etype:
            rows = self._conn.execute("SELECT * FROM entities WHERE type=? ORDER BY mention_count DESC, entity_id LIMIT ?", (etype, limit)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM entities ORDER BY mention_count DESC, entity_id LIMIT ?", (limit,)).fetchall()
        return [self._row_entity(r) for r in rows]

    async def entities_for_chat(self, chat_id: str, limit: int = 60) -> list[Entity]:
        rows = self._conn.execute(
            """SELECT e.*, COUNT(*) AS n FROM entities e
               JOIN memory_entities me ON me.entity_id = e.entity_id
               JOIN memories m ON m.memory_id = me.memory_id
               WHERE m.chat_id = ? GROUP BY e.entity_id ORDER BY n DESC, e.mention_count DESC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()
        return [self._row_entity(r) for r in rows]

    async def all_entity_keys(self) -> dict[str, str]:
        """{alias_or_key: entity_id} for every entity and alias (for in-query entity matching)."""
        out = {r["entity_id"]: r["entity_id"] for r in self._conn.execute("SELECT entity_id FROM entities")}
        out.update({r["alias"]: r["entity_id"] for r in self._conn.execute("SELECT alias, entity_id FROM entity_aliases")})
        return out

    async def memories_for_entities(self, entity_ids: Iterable[str], limit: int = 200, *, status: str = "active") -> list[tuple[str, str]]:
        ids = list(dict.fromkeys(e for e in entity_ids if e))
        if not ids:
            return []
        rows = self._conn.execute(
            f"""SELECT me.memory_id, me.entity_id FROM memory_entities me JOIN memories m ON m.memory_id = me.memory_id
                WHERE me.entity_id IN ({','.join('?' * len(ids))}) AND m.status = ? ORDER BY m.importance DESC, m.observed_at DESC LIMIT ?""",
            (*ids, status, limit),
        ).fetchall()
        return [(r["memory_id"], r["entity_id"]) for r in rows]

    async def entity_memory_counts(self, entity_ids: Iterable[str]) -> dict[str, int]:
        ids = list(dict.fromkeys(e for e in entity_ids if e))
        if not ids:
            return {}
        rows = self._conn.execute(
            f"""SELECT me.entity_id, COUNT(*) AS n FROM memory_entities me JOIN memories m ON m.memory_id = me.memory_id
                WHERE me.entity_id IN ({','.join('?' * len(ids))}) AND m.status='active' GROUP BY me.entity_id""", ids,
        ).fetchall()
        return {r["entity_id"]: r["n"] for r in rows}

    def _row_relation(self, r: sqlite3.Row) -> Relation:
        return Relation(relation_id=r["relation_id"], subject_id=r["subject_id"], predicate=r["predicate"], object_id=r["object_id"],
                        confidence=r["confidence"], chat_id=r["chat_id"], memory_id=r["memory_id"], message_id=r["message_id"],
                        status=r["status"], observation_count=r["observation_count"], created_at=r["created_at"],
                        updated_at=r["updated_at"], metadata=_jl(r["metadata"], {}))

    def _upsert_relation_sync(self, c: sqlite3.Connection, subject_id: str, predicate: str, object_id: str, *,
                              confidence: float, chat_id: str, memory_id: str | None, message_id: str | None) -> str:
        now = now_iso()
        rid = new_id("rel")
        c.execute(
            """INSERT INTO relations(relation_id, subject_id, predicate, object_id, confidence, chat_id, memory_id, message_id,
                                     status, observation_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
               ON CONFLICT(subject_id, predicate, object_id) DO UPDATE SET
                 observation_count = relations.observation_count + 1,
                 confidence = MAX(relations.confidence, excluded.confidence),
                 status = 'active',
                 memory_id = COALESCE(excluded.memory_id, relations.memory_id),
                 message_id = COALESCE(excluded.message_id, relations.message_id),
                 updated_at = excluded.updated_at""",
            (rid, subject_id, predicate, object_id, float(confidence), chat_id, memory_id, message_id, now, now),
        )
        r = c.execute("SELECT relation_id FROM relations WHERE subject_id=? AND predicate=? AND object_id=?",
                      (subject_id, predicate, object_id)).fetchone()
        if predicate in FUNCTIONAL_PREDICATES:
            # single-valued predicate: the newest observation wins, older objects become history
            c.execute(
                "UPDATE relations SET status='superseded', updated_at=? WHERE subject_id=? AND predicate=? "
                "AND object_id<>? AND status='active'",
                (now, subject_id, predicate, object_id),
            )
        return r["relation_id"]

    async def upsert_relation(self, subject_id: str, predicate: str, object_id: str, *, confidence: float = 0.6,
                              chat_id: str = "", memory_id: str | None = None, message_id: str | None = None) -> Relation:
        async with self._lock:
            with self._tx() as c:
                rid = self._upsert_relation_sync(c, subject_id, predicate, object_id, confidence=confidence,
                                                 chat_id=chat_id, memory_id=memory_id, message_id=message_id)
            self._bump()
        return self._row_relation(self._conn.execute("SELECT * FROM relations WHERE relation_id=?", (rid,)).fetchone())

    async def relations_for(self, entity_id: str, *, status: str | None = "active", limit: int = 200) -> list[Relation]:
        sql = "SELECT * FROM relations WHERE (subject_id=? OR object_id=?)"
        args: list[Any] = [entity_id, entity_id]
        if status:
            sql += " AND status=?"; args.append(status)
        sql += " ORDER BY observation_count DESC, confidence DESC, updated_at DESC LIMIT ?"; args.append(limit)
        return [self._row_relation(r) for r in self._conn.execute(sql, args).fetchall()]

    async def neighbors(self, entity_ids: Iterable[str], *, limit: int = 100) -> dict[str, list[tuple[str, str, float]]]:
        """1-hop typed neighbourhood: {entity_id: [(neighbour_id, predicate, confidence)]} over active relations."""
        ids = list(dict.fromkeys(e for e in entity_ids if e))
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT subject_id, predicate, object_id, confidence FROM relations WHERE status='active' "
            f"AND (subject_id IN ({ph}) OR object_id IN ({ph})) ORDER BY observation_count DESC, confidence DESC LIMIT ?",
            (*ids, *ids, limit),
        ).fetchall()
        out: dict[str, list[tuple[str, str, float]]] = {i: [] for i in ids}
        want = set(ids)
        for r in rows:
            if r["subject_id"] in want:
                out[r["subject_id"]].append((r["object_id"], r["predicate"], r["confidence"]))
            if r["object_id"] in want and r["object_id"] != r["subject_id"]:
                out[r["object_id"]].append((r["subject_id"], f"inverse:{r['predicate']}", r["confidence"]))
        return out

    async def relation_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._conn.execute("SELECT status, COUNT(*) AS n FROM relations GROUP BY status")}

    async def entity_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"])

    # ------------------------------------------------------------------ atomic plan application
    async def apply_plan(self, plan: "ExtractionPlan", *, worker_id: str | None = None) -> dict[str, int]:
        """Commit one extraction plan atomically (memories, entities, relations, links, sources, message + job status).

        This is the only write path the worker uses for extraction results, which is what makes a crash
        mid-job harmless: either everything below is visible, or nothing is. When ``worker_id`` is given the
        commit is fenced: every job in the plan must still be leased by that worker, otherwise nothing is
        written and :class:`LostLease` is raised (the job was re-leased by someone else after this worker's
        lease expired, and that other worker owns the result now).
        """
        counts = {"memories_added": 0, "memories_superseded": 0, "duplicates": 0, "entities": 0, "relations": 0, "links": 0}
        now = now_iso()
        async with self._lock:
            with self._tx() as c:
                if worker_id is not None and plan.job_ids:
                    ph = ",".join("?" * len(plan.job_ids))
                    held = c.execute(
                        f"SELECT COUNT(*) AS n FROM jobs WHERE job_id IN ({ph}) AND status='leased' AND worker_id=?",
                        (*plan.job_ids, worker_id),
                    ).fetchone()["n"]
                    if held != len(plan.job_ids):
                        raise LostLease(f"{len(plan.job_ids) - held} of {len(plan.job_ids)} jobs no longer leased by {worker_id}")
                for e in plan.entities:
                    self._upsert_entity_sync(c, e["entity_id"], e["name"], e.get("type", ""), e.get("seen_at"),
                                             mentions=int(e.get("mentions", 1)), aliases=e.get("aliases", ()))
                    counts["entities"] += 1
                for m, src_ids, ent_ids in plan.new_memories:
                    self._insert_memory_sync(c, m, src_ids, ent_ids)
                    counts["memories_added"] += 1
                for old_id, new_mid, link_type in plan.supersedes:
                    self._supersede_sync(c, old_id, new_mid, link_type)
                    counts["memories_superseded"] += 1
                for mem_id, src_ids in plan.duplicate_sources:
                    c.executemany("INSERT OR IGNORE INTO memory_sources(memory_id, message_id) VALUES (?, ?)",
                                  [(mem_id, s) for s in dict.fromkeys(src_ids)])
                    c.execute("UPDATE memories SET updated_at=?, metadata=json_set(metadata, '$.observations', "
                              "COALESCE(json_extract(metadata, '$.observations'), 1) + 1) WHERE memory_id=?", (now, mem_id))
                    counts["duplicates"] += 1
                for s, p, o, conf, mem_id, msg_id in plan.relations:
                    self._upsert_relation_sync(c, s, p, o, confidence=conf, chat_id=plan.chat_id, memory_id=mem_id, message_id=msg_id)
                    counts["relations"] += 1
                for a, b, lt, w in plan.links:
                    c.execute("INSERT OR REPLACE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
                              (a, b, lt, float(w), now))
                    counts["links"] += 1
                if plan.processed_message_ids:
                    c.executemany("UPDATE messages SET status='processed', processed_at=? WHERE message_id=?",
                                  [(now, m) for m in plan.processed_message_ids])
                if plan.skipped_message_ids:
                    c.executemany("UPDATE messages SET status='skipped', processed_at=? WHERE message_id=?",
                                  [(now, m) for m in plan.skipped_message_ids])
                if plan.job_ids:
                    self._complete_jobs_sync(c, [int(j) for j in plan.job_ids])
                for kind, ref, detail in plan.audit:
                    self._log_sync(c, kind, ref, detail)
            self._bump()
        return counts

    # ------------------------------------------------------------------ audit & stats
    def _log_sync(self, c: sqlite3.Connection, kind: str, ref: str | None, detail: dict[str, Any] | None) -> None:
        c.execute("INSERT INTO audit_log(at, kind, ref, detail) VALUES (?, ?, ?, ?)", (now_iso(), kind, ref, _j(detail)))

    async def log_event(self, kind: str, ref: str | None = None, detail: dict[str, Any] | None = None) -> None:
        async with self._lock:
            with self._tx() as c:
                self._log_sync(c, kind, ref, detail)

    async def recent_events(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = self._conn.execute("SELECT * FROM audit_log WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "at": r["at"], "kind": r["kind"], "ref": r["ref"], "detail": _jl(r["detail"], {})} for r in rows]

    async def stats(self) -> dict[str, Any]:
        chats = int(self._conn.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"])
        return {
            "db_path": self.db_path,
            "has_fts": self.has_fts,
            "revision": self.revision,
            "chats": chats,
            "messages": await self.message_counts(),
            "memories": await self.memory_counts(),
            "entities": await self.entity_count(),
            "relations": await self.relation_counts(),
            "links": int(self._conn.execute("SELECT COUNT(*) AS n FROM memory_links").fetchone()["n"]),
            "jobs": await self.job_counts(),
        }


class ExtractionPlan:
    """Everything the worker decided for one batch of messages, applied atomically by :meth:`ChatMemoryStore.apply_plan`."""

    def __init__(self, chat_id: str, job_ids: Iterable[int] = ()) -> None:
        self.chat_id = chat_id
        self.job_ids: list[int] = [int(j) for j in job_ids]
        self.entities: list[dict[str, Any]] = []                      # {entity_id, name, type, seen_at, mentions, aliases}
        self.new_memories: list[tuple[Memory, list[str], list[str]]] = []   # (memory, source_message_ids, entity_ids)
        self.supersedes: list[tuple[str, str, str]] = []              # (old_id, new_id, link_type)
        self.duplicate_sources: list[tuple[str, list[str]]] = []      # (existing_memory_id, source_message_ids)
        self.relations: list[tuple[str, str, str, float, str | None, str | None]] = []  # (s, p, o, conf, memory_id, message_id)
        self.links: list[tuple[str, str, str, float]] = []            # (source_id, target_id, link_type, weight)
        self.processed_message_ids: list[str] = []
        self.skipped_message_ids: list[str] = []
        self.audit: list[tuple[str, str | None, dict[str, Any]]] = []

    def summary(self) -> dict[str, int]:
        return {
            "new_memories": len(self.new_memories), "supersedes": len(self.supersedes),
            "duplicates": len(self.duplicate_sources), "relations": len(self.relations), "links": len(self.links),
            "entities": len(self.entities), "processed": len(self.processed_message_ids), "skipped": len(self.skipped_message_ids),
        }

"""Holder-side SQLite store: NeuralGraph's ``ChatMemoryStore`` plus Mycelic's document, export and idempotency tables.

Why a subclass in the *same* file rather than a second database: an ingest must land atomically — the document
row, its chunk messages (provenance), the derived memories, the entity rows and the transport idempotency marker
either all exist or none do. The parent store already gives one ``sqlite3`` connection, ``BEGIN IMMEDIATE``
transactions under an ``asyncio.Lock`` and ``_bump()`` for retrieval caches; :meth:`run_in_tx` exposes that
discipline so :class:`mycelic.evidence.service.EvidenceStore` can compose the parent's ``*_sync`` helpers with
the tables added here inside one transaction.

Tables added (docs/mycelic/DECISIONS.md D2, D11):

* ``documents`` / ``document_versions`` — the raw evidence and its history; ``source_root_id`` is the content
  fingerprint used for independent-support counting.
* ``exports`` — every opaque ``ref_id`` the coordinator has ever been given, mapped to the memory and document it
  came from. ``memory_id`` and ``doc_id`` never leave the holder; the coordinator only sees ``ref_id``.
* ``processed_messages`` — transport idempotency: ``msg_id`` of every envelope acted on, with the stored outcome so a
  redelivery can re-publish the same result without repeating the effect.
* ``holder_meta`` — small key/value state (last ingest time, counters, schema version).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable, Iterable, TypeVar

from NeuralGraph.chat_memory.models import ChatMessage, Memory, now_iso
from NeuralGraph.chat_memory.store import ChatMemoryStore
from NeuralGraph.chat_memory.textutil import content_hash

from ..util import j, jl

logger = logging.getLogger(__name__)

MYCELIC_SCHEMA_VERSION = 1

T = TypeVar("T")

_MYCELIC_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id         TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'note',
    text           TEXT NOT NULL,
    source_root_id TEXT NOT NULL,
    origin_id      TEXT,
    observed_at    TEXT,
    domains        TEXT NOT NULL DEFAULT '[]',
    summary        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',     -- active | revised | retracted
    version        INTEGER NOT NULL DEFAULT 1,
    chars          INTEGER NOT NULL DEFAULT 0,
    uploaded_by    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_root ON documents(source_root_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status, updated_at);

CREATE TABLE IF NOT EXISTS document_versions (
    doc_id      TEXT NOT NULL,
    version     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    title       TEXT NOT NULL,
    observed_at TEXT,
    at          TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (doc_id, version)
);

CREATE TABLE IF NOT EXISTS exports (
    ref_id            TEXT PRIMARY KEY,
    memory_id         TEXT NOT NULL,
    doc_id            TEXT NOT NULL,
    question_id       TEXT NOT NULL,
    disclosed_excerpt TEXT NOT NULL DEFAULT '',
    disclosure_level  TEXT NOT NULL DEFAULT 'excerpt',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exports_memory ON exports(memory_id);
CREATE INDEX IF NOT EXISTS idx_exports_doc ON exports(doc_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exports_memory_question ON exports(memory_id, question_id);

CREATE TABLE IF NOT EXISTS processed_messages (
    msg_id       TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    outcome      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS holder_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class AlreadyProcessed(RuntimeError):
    """The transport message was already acted on; ``outcome`` is what was stored the first time."""

    def __init__(self, msg_id: str, outcome: dict[str, Any]) -> None:
        super().__init__(f"message {msg_id} already processed")
        self.msg_id = msg_id
        self.outcome = outcome


class MycelicMemoryStore(ChatMemoryStore):
    """See module docstring. Every public method keeps the parent's lock/transaction discipline."""

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        super()._init_schema()
        c = self._conn
        c.executescript(_MYCELIC_DDL)
        row = c.execute("SELECT value FROM holder_meta WHERE key='mycelic_schema_version'").fetchone()
        if row is None:
            c.execute("INSERT INTO holder_meta(key, value) VALUES ('mycelic_schema_version', ?)", (str(MYCELIC_SCHEMA_VERSION),))
        elif int(row["value"]) > MYCELIC_SCHEMA_VERSION:
            raise RuntimeError(f"holder schema {row['value']} is newer than this code ({MYCELIC_SCHEMA_VERSION})")

    # ------------------------------------------------------------------ transactions
    async def run_in_tx(self, fn: Callable[[sqlite3.Connection], T], *, idempotency_key: str | None = None, op: str = "") -> T:
        """Run ``fn(connection)`` inside one ``BEGIN IMMEDIATE`` transaction under the store lock.

        With ``idempotency_key`` (a transport ``msg_id``) the ``processed_messages`` row is claimed *first*, inside the
        same transaction as the effect, and its outcome (``{"op": op, "result": fn's return}``) is written before the
        commit. A second delivery therefore either sees the marker and raises :class:`AlreadyProcessed` with the stored
        outcome, or races into the same transaction and is rolled back by the primary-key conflict. ``fn`` must be
        synchronous and must not await anything.
        """
        async with self._lock:
            with self._tx() as c:
                if idempotency_key:
                    self._claim_processed_sync(c, idempotency_key)
                result = fn(c)
                if idempotency_key:
                    self._set_outcome_sync(c, idempotency_key, {"op": op, "result": result})
            self._bump()
        return result

    # ------------------------------------------------------------------ processed messages (transport idempotency)
    def _claim_processed_sync(self, c: sqlite3.Connection, msg_id: str) -> None:
        try:
            c.execute("INSERT INTO processed_messages(msg_id, processed_at, outcome) VALUES (?, ?, '{}')", (msg_id, now_iso()))
        except sqlite3.IntegrityError:
            row = c.execute("SELECT outcome FROM processed_messages WHERE msg_id=?", (msg_id,)).fetchone()
            raise AlreadyProcessed(msg_id, jl(row["outcome"] if row else None, {})) from None

    @staticmethod
    def _set_outcome_sync(c: sqlite3.Connection, msg_id: str, outcome: dict[str, Any]) -> None:
        c.execute("UPDATE processed_messages SET outcome=? WHERE msg_id=?", (j(outcome), msg_id))

    async def processed_outcome(self, msg_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT outcome FROM processed_messages WHERE msg_id=?", (msg_id,)).fetchone()
        return jl(row["outcome"], {}) if row else None

    async def mark_processed(self, msg_id: str, outcome: dict[str, Any] | None = None) -> bool:
        """Standalone marker (for messages with no store effect). Returns False when it already existed."""
        async with self._lock:
            with self._tx() as c:
                try:
                    c.execute("INSERT INTO processed_messages(msg_id, processed_at, outcome) VALUES (?, ?, ?)",
                              (msg_id, now_iso(), j(outcome or {})))
                except sqlite3.IntegrityError:
                    return False
        return True

    async def processed_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM processed_messages").fetchone()["n"])

    # ------------------------------------------------------------------ holder meta
    async def get_holder_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM holder_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    @staticmethod
    def _set_holder_meta_sync(c: sqlite3.Connection, key: str, value: str) -> None:
        c.execute("INSERT INTO holder_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    async def set_holder_meta(self, key: str, value: str) -> None:
        async with self._lock:
            with self._tx() as c:
                self._set_holder_meta_sync(c, key, value)

    @staticmethod
    def _increment_meta_sync(c: sqlite3.Connection, key: str, by: int = 1) -> int:
        row = c.execute("SELECT value FROM holder_meta WHERE key=?", (key,)).fetchone()
        n = int(row["value"]) + by if row else by
        MycelicMemoryStore._set_holder_meta_sync(c, key, str(n))
        return n

    # ------------------------------------------------------------------ documents
    @staticmethod
    def _row_document(r: sqlite3.Row | None, *, with_text: bool = False) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        d["domains"] = jl(d.get("domains"), [])
        if not with_text:
            d.pop("text", None)
        return d

    def _get_document_sync(self, c: sqlite3.Connection, doc_id: str, *, with_text: bool = False) -> dict[str, Any] | None:
        return self._row_document(c.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone(), with_text=with_text)

    async def get_document(self, doc_id: str, *, with_text: bool = False) -> dict[str, Any] | None:
        return self._get_document_sync(self._conn, doc_id, with_text=with_text)

    async def list_documents(self, *, status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM documents", []
        if status:
            sql += " WHERE status=?"; args.append(status)
        sql += " ORDER BY updated_at DESC, doc_id LIMIT ? OFFSET ?"; args.extend([int(limit), int(offset)])
        return [self._row_document(r) for r in self._conn.execute(sql, args).fetchall()]  # type: ignore[misc]

    async def documents_by_root(self, source_root_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM documents WHERE source_root_id=? ORDER BY created_at", (source_root_id,)).fetchall()
        return [self._row_document(r) for r in rows]  # type: ignore[misc]

    async def document_counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM documents GROUP BY status").fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    @staticmethod
    def _insert_document_sync(c: sqlite3.Connection, doc: dict[str, Any], text: str) -> None:
        c.execute(
            """INSERT INTO documents(doc_id, title, kind, text, source_root_id, origin_id, observed_at, domains, summary, status,
                                     version, chars, uploaded_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc["doc_id"], doc["title"], doc.get("kind") or "note", text, doc["source_root_id"], doc.get("origin_id"),
             doc.get("observed_at"), j(list(doc.get("domains") or [])), doc.get("summary") or "", doc.get("status") or "active",
             int(doc.get("version") or 1), int(doc.get("chars") or len(text)), doc.get("uploaded_by"), doc["created_at"], doc["updated_at"]),
        )

    @staticmethod
    def _update_document_sync(c: sqlite3.Connection, doc_id: str, **fields: Any) -> None:
        allowed = {"title", "kind", "text", "source_root_id", "observed_at", "domains", "summary", "status", "version", "chars", "updated_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update document fields {sorted(bad)}")
        if not fields:
            return
        sets, args = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(j(list(v)) if k == "domains" else v)
        args.append(doc_id)
        c.execute(f"UPDATE documents SET {', '.join(sets)} WHERE doc_id=?", args)

    @staticmethod
    def _add_version_sync(c: sqlite3.Connection, doc_id: str, version: int, text: str, title: str, observed_at: str | None,
                          at: str, reason: str) -> None:
        c.execute("INSERT OR REPLACE INTO document_versions(doc_id, version, text, title, observed_at, at, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (doc_id, int(version), text, title, observed_at, at, reason or ""))

    async def document_versions(self, doc_id: str, *, with_text: bool = False) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM document_versions WHERE doc_id=? ORDER BY version", (doc_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if not with_text:
                d.pop("text", None)
            out.append(d)
        return out

    async def document_version_text(self, doc_id: str, version: int) -> str | None:
        r = self._conn.execute("SELECT text FROM document_versions WHERE doc_id=? AND version=?", (doc_id, int(version))).fetchone()
        return r["text"] if r else None

    # ------------------------------------------------------------------ chunk messages (provenance)
    def _insert_message_sync(self, c: sqlite3.Connection, chat_id: str, speaker: str, text: str, *, sent_at: str | None,
                             message_id: str, metadata: dict[str, Any] | None = None, status: str = "processed",
                             title: str | None = None, enqueue: bool = False) -> ChatMessage:
        """Synchronous twin of the parent's ``add_message`` for use inside :meth:`run_in_tx`.

        Chunk messages are provenance rows, not chat turns: they are marked ``processed`` (the memory is created in
        the same transaction) unless ``enqueue`` asks NeuralGraph's extraction worker to also run over them.
        """
        dup = c.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
        if dup is not None:
            return self._row_message(dup)
        self._upsert_chat_sync(c, chat_id, title=title)
        seq = int(c.execute("SELECT COALESCE(MAX(seq), -1) + 1 AS nxt FROM messages WHERE chat_id=?", (chat_id,)).fetchone()["nxt"])
        now = now_iso()
        status = "pending" if enqueue else status
        c.execute(
            """INSERT INTO messages(message_id, chat_id, seq, speaker, role, text, sent_at, ingested_at, content_hash, status,
                                    processed_at, metadata)
               VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, chat_id, seq, speaker or "document", text, sent_at, now,
             content_hash(chat_id, speaker or "document", text, sent_at or "", message_id), status,
             now if status == "processed" else None, j(metadata)),
        )
        c.execute(
            """UPDATE chats SET message_count = message_count + 1, updated_at = ?,
               participants = (SELECT json_group_array(DISTINCT speaker) FROM messages WHERE chat_id = ?)
               WHERE chat_id = ?""",
            (now, chat_id, chat_id),
        )
        if enqueue:
            self._enqueue_sync(c, "extract", chat_id=chat_id, ref_id=message_id, seq=seq, dedupe_key=f"extract:{message_id}",
                               available_at=None)
        return self._row_message(c.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone())

    # ------------------------------------------------------------------ document memories
    @staticmethod
    def _document_memory_ids_sync(c: sqlite3.Connection, doc_id: str, *, status: str | None = "active") -> list[str]:
        """Chunk memories of a document (``chat_id`` is the document id). ``status=None`` means every non-retracted row."""
        if status:
            rows = c.execute("SELECT memory_id FROM memories WHERE chat_id=? AND status=? ORDER BY rid", (doc_id, status)).fetchall()
        else:
            rows = c.execute("SELECT memory_id FROM memories WHERE chat_id=? AND status<>'retracted' ORDER BY rid", (doc_id,)).fetchall()
        return [r["memory_id"] for r in rows]

    async def document_memory_ids(self, doc_id: str, *, status: str | None = "active") -> list[str]:
        return self._document_memory_ids_sync(self._conn, doc_id, status=status)

    async def document_memories(self, doc_id: str, *, status: str | None = "active") -> list[Memory]:
        ids = await self.document_memory_ids(doc_id, status=status)
        return await self.get_memories_by_ids(ids, with_sources=True)

    def _retract_memories_sync(self, c: sqlite3.Connection, memory_ids: Iterable[str], reason: str = "") -> int:
        ids = [m for m in dict.fromkeys(memory_ids) if m]
        if not ids:
            return 0
        now = now_iso()
        n = 0
        for mid in ids:
            n += c.execute("UPDATE memories SET status='retracted', updated_at=? WHERE memory_id=? AND status<>'retracted'", (now, mid)).rowcount
            c.execute("UPDATE relations SET status='retracted', updated_at=? WHERE memory_id=?", (now, mid))
        if reason:
            self._log_sync(c, "retract", None, {"memory_ids": ids, "reason": reason})
        return n

    # ------------------------------------------------------------------ exports (opaque evidence references)
    @staticmethod
    def _row_export(r: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(r) if r is not None else None

    @staticmethod
    def _record_export_sync(c: sqlite3.Connection, *, ref_id: str, memory_id: str, doc_id: str, question_id: str,
                            disclosed_excerpt: str, disclosure_level: str, created_at: str | None = None) -> str:
        """Insert the export unless (memory, question) already has one; returns the ``ref_id`` in force."""
        existing = c.execute("SELECT ref_id FROM exports WHERE memory_id=? AND question_id=?", (memory_id, question_id)).fetchone()
        if existing is not None:
            c.execute("UPDATE exports SET disclosed_excerpt=?, disclosure_level=? WHERE ref_id=?",
                      (disclosed_excerpt, disclosure_level, existing["ref_id"]))
            return existing["ref_id"]
        c.execute("INSERT INTO exports(ref_id, memory_id, doc_id, question_id, disclosed_excerpt, disclosure_level, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (ref_id, memory_id, doc_id, question_id, disclosed_excerpt, disclosure_level, created_at or now_iso()))
        return ref_id

    async def record_export(self, **kw: Any) -> str:
        async with self._lock:
            with self._tx() as c:
                return self._record_export_sync(c, **kw)

    async def get_export(self, ref_id: str) -> dict[str, Any] | None:
        return self._row_export(self._conn.execute("SELECT * FROM exports WHERE ref_id=?", (ref_id,)).fetchone())

    async def find_export(self, memory_id: str, question_id: str) -> dict[str, Any] | None:
        return self._row_export(self._conn.execute("SELECT * FROM exports WHERE memory_id=? AND question_id=?", (memory_id, question_id)).fetchone())

    async def exports_for_question(self, question_id: str) -> dict[str, dict[str, Any]]:
        """{memory_id: export} for one question, so a re-asked question reuses its reference ids."""
        rows = self._conn.execute("SELECT * FROM exports WHERE question_id=?", (question_id,)).fetchall()
        return {r["memory_id"]: dict(r) for r in rows}

    @staticmethod
    def _exports_for_memories_sync(c: sqlite3.Connection, memory_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = [m for m in dict.fromkeys(memory_ids) if m]
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = c.execute(f"SELECT * FROM exports WHERE memory_id IN ({','.join('?' * len(chunk))}) ORDER BY created_at, ref_id", chunk).fetchall()
            out.extend(dict(r) for r in rows)
        return out

    async def exports_for_memories(self, memory_ids: Iterable[str]) -> list[dict[str, Any]]:
        return self._exports_for_memories_sync(self._conn, memory_ids)

    @staticmethod
    def _exports_for_document_sync(c: sqlite3.Connection, doc_id: str) -> list[dict[str, Any]]:
        rows = c.execute("SELECT * FROM exports WHERE doc_id=? ORDER BY created_at, ref_id", (doc_id,)).fetchall()
        return [dict(r) for r in rows]

    async def exports_for_document(self, doc_id: str) -> list[dict[str, Any]]:
        return self._exports_for_document_sync(self._conn, doc_id)

    async def export_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM exports").fetchone()["n"])

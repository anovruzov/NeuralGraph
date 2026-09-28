"""``CoordDB``: the coordination database connection.

Same discipline as ``NeuralGraph.chat_memory.store``: one ``sqlite3`` connection per process in WAL mode,
every write inside ``BEGIN IMMEDIATE`` under an ``asyncio.Lock`` (``async with db.tx() as c:``), reads
straight on the connection. Multiple processes (API, worker, embedded holders) may open the same file;
``busy_timeout`` serialises writers and every consumer is idempotent, so a retry after ``SQLITE_BUSY`` is safe.

The outbox (``emit``) is the only way live updates leave the system: write the event in the same
transaction as the change and the API's SSE tailer will deliver it after an authorization filter.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from ..util import j, jl, now_iso
from .migrate import apply_migrations

logger = logging.getLogger(__name__)


class CoordDB:
    def __init__(self, path: str | Path, *, migrate: bool = True) -> None:
        self.path = ":memory:" if str(path) == ":memory:" else str(Path(path).expanduser())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        c = self._conn
        if self.path != ":memory:":
            with contextlib.suppress(sqlite3.DatabaseError):
                c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        if migrate:
            apply_migrations(c)
        self.revision = 0
        self._wakers: list[asyncio.Event] = []

    # ------------------------------------------------------------------ lifecycle
    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    @contextlib.asynccontextmanager
    async def tx(self) -> AsyncIterator[sqlite3.Connection]:
        """Atomic write transaction. Never ``await`` anything but this DB inside the block."""
        async with self._lock:
            c = self._conn
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")
                self.revision += 1
        for w in self._wakers:
            w.set()

    def add_waker(self, ev: asyncio.Event) -> None:
        """Register an event set after every committed transaction (workers use it instead of polling)."""
        self._wakers.append(ev)

    def remove_waker(self, ev: asyncio.Event) -> None:
        with contextlib.suppress(ValueError):
            self._wakers.remove(ev)

    def data_version(self) -> int:
        try:
            return int(self._conn.execute("PRAGMA data_version").fetchone()[0])
        except sqlite3.Error:
            return 0

    # ------------------------------------------------------------------ reads
    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, tuple(args)).fetchone()

    def all(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, tuple(args)).fetchall()

    def scalar(self, sql: str, args: Iterable[Any] = (), default: Any = None) -> Any:
        r = self.one(sql, args)
        return default if r is None else r[0]

    # ------------------------------------------------------------------ outbox
    @staticmethod
    def emit_sync(c: sqlite3.Connection, tenant_id: str, kind: str, *, ref_type: str | None = None,
                  ref_id: str | None = None, payload: dict[str, Any] | None = None,
                  audience: dict[str, Any] | None = None) -> int:
        """Append an event inside the caller's transaction. ``audience`` limits who may receive it:
        ``{"unit_ids": [...], "user_ids": [...], "visibility": "unit|org|private", "roles": [...]}``."""
        cur = c.execute(
            "INSERT INTO events(tenant_id, kind, ref_type, ref_id, payload, audience, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, kind, ref_type, ref_id, j(payload), j(audience), now_iso()),
        )
        return int(cur.lastrowid)

    async def emit(self, tenant_id: str, kind: str, **kw: Any) -> int:
        async with self.tx() as c:
            return self.emit_sync(c, tenant_id, kind, **kw)

    def events_after(self, last_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.all("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (last_id, limit))
        return [row_to_dict(r, json_fields=("payload", "audience")) for r in rows]

    def last_event_id(self) -> int:
        return int(self.scalar("SELECT COALESCE(MAX(id), 0) FROM events", default=0))

    # ------------------------------------------------------------------ audit
    @staticmethod
    def audit_sync(c: sqlite3.Connection, tenant_id: str | None, actor_type: str, actor_id: str | None, action: str, *,
                   resource_type: str | None = None, resource_id: str | None = None, outcome: str = "ok",
                   detail: dict[str, Any] | None = None, request_id: str | None = None) -> None:
        c.execute(
            "INSERT INTO audit_log(tenant_id, at, actor_type, actor_id, action, resource_type, resource_id, outcome, detail, request_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, now_iso(), actor_type, actor_id, action, resource_type, resource_id, outcome, j(detail), request_id),
        )

    async def audit(self, tenant_id: str | None, actor_type: str, actor_id: str | None, action: str, **kw: Any) -> None:
        async with self.tx() as c:
            self.audit_sync(c, tenant_id, actor_type, actor_id, action, **kw)


def row_to_dict(r: sqlite3.Row | None, *, json_fields: Iterable[str] = ()) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for f in json_fields:
        if f in d:
            d[f] = jl(d[f], {} if not (isinstance(d[f], str) and d[f].startswith("[")) else [])
    return d


def rows_to_dicts(rows: Iterable[sqlite3.Row], *, json_fields: Iterable[str] = ()) -> list[dict[str, Any]]:
    jf = tuple(json_fields)
    return [row_to_dict(r, json_fields=jf) for r in rows]  # type: ignore[misc]

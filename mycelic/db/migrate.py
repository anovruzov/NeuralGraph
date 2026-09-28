"""Numbered SQL migrations for the coordination database.

Files live in ``mycelic/db/migrations/NNNN_name.sql`` and are applied in order inside one transaction each;
``schema_migrations`` records what ran. A migration is never edited after it ships — add a new file.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from ..util import now_iso

logger = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def _files() -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _NAME_RE.match(p.name)
        if not m:
            raise RuntimeError(f"bad migration file name: {p.name}")
        out.append((int(m.group(1)), m.group(2), p))
    return out


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")


def migration_status(conn: sqlite3.Connection) -> dict:
    _ensure_table(conn)
    applied = {r[0]: r[1] for r in conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")}
    available = _files()
    pending = [(v, n) for v, n, _ in available if v not in applied]
    return {"applied": [{"version": v, "name": n} for v, n in sorted(applied.items())],
            "pending": [{"version": v, "name": n} for v, n in pending],
            "current": max(applied) if applied else 0, "latest": max((v for v, _, _ in available), default=0)}


def apply_migrations(conn: sqlite3.Connection, *, target: int | None = None) -> list[int]:
    """Apply every pending migration (up to ``target``). Returns the versions applied."""
    _ensure_table(conn)
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    done: list[int] = []
    for version, name, path in _files():
        if version in applied or (target is not None and version > target):
            continue
        sql = path.read_text(encoding="utf-8")
        # executescript() commits any pending transaction first, so the BEGIN/COMMIT pair goes inside the script
        script = "BEGIN;\n" + sql + f"\nINSERT INTO schema_migrations(version, name, applied_at) VALUES ({version}, '{name}', '{now_iso()}');\nCOMMIT;\n"
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        logger.info("applied migration %04d_%s", version, name)
        done.append(version)
    return done

"""Numbered SQL migrations for holder files (docs/mycelic/INGESTION.md §5.1).

Files live in ``mycelic/evidence/migrations/NNNN_name.sql``. Version 1 is the baseline that
``MycelicMemoryStore._init_schema`` creates with ``CREATE TABLE IF NOT EXISTS`` (``_MYCELIC_DDL``); every later version is a
file here. Each file runs as one script inside ``BEGIN``/``COMMIT`` (``executescript`` commits any pending transaction
first, so the pair goes inside the script, the same trick as ``mycelic/db/migrate.py``) together with the row that records
it in ``holder_schema_migrations``. A failing file is rolled back completely and the error is raised, so a holder file is
never left half-migrated.

Rules:

* Migrations are immutable once shipped: the stored checksum of an applied file must match the file on disk, or opening
  the holder fails loudly (a silently edited migration would mean two holders with the same version and different schemas).
* A file whose recorded versions are newer than this code refuses to open (an old binary never writes a newer schema).
* Migrations only add tables, columns, indexes and triggers, so a v1 file keeps working after it is upgraded.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from ..util import now_iso

logger = logging.getLogger(__name__)

HOLDER_MIGRATIONS_DIR = Path(__file__).with_name("migrations")
BASELINE_VERSION = 1
_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class HolderSchemaError(RuntimeError):
    """The holder file cannot be brought to this code's schema (newer file, edited migration, bad file name)."""


@lru_cache(maxsize=1)
def migration_files() -> tuple[tuple[int, str, str, str], ...]:
    """``(version, name, checksum, sql)`` for every migration file, in version order."""
    out = []
    for p in sorted(HOLDER_MIGRATIONS_DIR.glob("*.sql")):
        m = _NAME_RE.match(p.name)
        if not m:
            raise HolderSchemaError(f"bad holder migration file name: {p.name}")
        version = int(m.group(1))
        if version <= BASELINE_VERSION:
            raise HolderSchemaError(f"holder migration {p.name}: versions up to {BASELINE_VERSION} are the baseline")
        sql = p.read_text(encoding="utf-8")
        out.append((version, m.group(2), hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql))
    versions = [v for v, *_ in out]
    if len(versions) != len(set(versions)):
        raise HolderSchemaError("duplicate holder migration versions")
    return tuple(out)


def latest_holder_version() -> int:
    files = migration_files()
    return max([BASELINE_VERSION] + [v for v, *_ in files])


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS holder_schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                 "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO holder_schema_migrations(version, name, checksum, applied_at) VALUES (?, 'baseline', 'baseline', ?)",
                 (BASELINE_VERSION, now_iso()))


def holder_migration_status(conn: sqlite3.Connection) -> dict:
    _ensure_table(conn)
    applied = {int(r[0]): r[1] for r in conn.execute("SELECT version, name FROM holder_schema_migrations ORDER BY version")}
    files = migration_files()
    return {"applied": [{"version": v, "name": n} for v, n in sorted(applied.items())],
            "pending": [{"version": v, "name": n} for v, n, _c, _s in files if v not in applied],
            "current": max(applied) if applied else 0, "latest": latest_holder_version()}


def apply_holder_migrations(conn: sqlite3.Connection) -> list[int]:
    """Bring one holder file to the latest holder schema. Returns the versions applied by this call.

    Must run outside any transaction and before the store is shared with asyncio work (it runs at open time).
    Finally sets ``holder_meta.mycelic_schema_version`` to the highest applied version.
    """
    if conn.in_transaction:
        raise HolderSchemaError("apply_holder_migrations must run outside a transaction")
    _ensure_table(conn)
    rows = {int(r[0]): (r[1], r[2]) for r in conn.execute("SELECT version, name, checksum FROM holder_schema_migrations")}
    latest = latest_holder_version()
    newer = [v for v in rows if v > latest]
    if newer:
        raise HolderSchemaError(f"holder schema {max(newer)} is newer than this code ({latest})")
    done: list[int] = []
    for version, name, checksum, sql in migration_files():
        if version in rows:
            if rows[version][1] != checksum:
                raise HolderSchemaError(f"holder migration {version:04d}_{name} changed after it was applied (checksum mismatch)")
            continue
        insert = ("INSERT INTO holder_schema_migrations(version, name, checksum, applied_at) "
                  f"VALUES ({version}, '{name}', '{checksum}', '{now_iso()}');")
        try:
            conn.executescript("BEGIN;\n" + sql + "\n" + insert + "\nCOMMIT;\n")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        logger.info("holder migration %04d_%s applied", version, name)
        done.append(version)
    current = int(conn.execute("SELECT MAX(version) FROM holder_schema_migrations").fetchone()[0])
    conn.execute("INSERT INTO holder_meta(key, value) VALUES ('mycelic_schema_version', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(current),))
    return done

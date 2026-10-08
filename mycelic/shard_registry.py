"""The coordinator's shard registry (docs/mycelic/INGESTION.md §5.3, §7.2, §7.6, §7.9).

Each holder's ``shard_map`` is the authority for routing; ``coord.shards`` mirrors it through the heartbeat so
administrators see every shard of the tenant (partition, status, health, stats, last backup) and so a writer lease can be
held per shard. ``coord.shard_migrations`` mirrors the holder's split migrations. Only counts, bytes, latencies, tenant
taxonomy ids and states are stored: never content, never personal domains, never file paths of external holders.

A heartbeat comes from the holder process itself (embedded, or an external holder over HTTP), so every field is checked
and coerced here rather than trusted.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable, Mapping

from .util import j, jl, now_iso, plus_seconds

SHARD_ID_RE = re.compile(r"^(s0|shd_[0-9a-f]{8,32})$")
MIGRATION_ID_RE = re.compile(r"^smg_[0-9a-f]{8,32}$")
DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}(\.[a-z0-9][a-z0-9-]{0,40}){0,3}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
SHARD_STATUSES = ("provisioning", "active", "draining", "readonly", "retired")
HEALTH = ("ok", "hot", "degraded", "offline")
MIGRATION_STATES = ("planned", "provisioning", "copying", "catching_up", "cutover", "cleanup", "done", "aborted", "failed")
LEASE_SECONDS = 30.0
MAX_SHARDS_REPORTED = 16


def _numbers(d: Any, *, limit: int = 32) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(d, Mapping):
        return out
    for k, v in list(d.items())[:limit]:
        if not isinstance(k, str) or len(k) > 40:
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) or v is None:
            out[k] = v
        elif isinstance(v, list) and len(v) <= 16 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            out[k] = list(v)
        elif k == "at" and isinstance(v, str) and len(v) <= 40:
            out[k] = v
    return out


def _domains(ids: Any, known: set[str] | None) -> list[str]:
    out: list[str] = []
    for d in ids if isinstance(ids, list) else []:
        if not isinstance(d, str):
            continue
        if d == "personal" or d.startswith("personal."):
            if "personal" not in out:
                out.append("personal")
            continue
        if DOMAIN_RE.match(d) and (known is None or d in known) and d not in out:
            out.append(d)
    return out[:16]


def _partition(p: Any, known: set[str] | None) -> dict[str, Any]:
    if not isinstance(p, Mapping):
        return {}
    out: dict[str, Any] = {}
    ids = _domains(p.get("domain_ids"), known)
    if ids:
        out["domain_ids"] = ids
    for k in ("time_from", "time_to"):
        if isinstance(p.get(k), str) and len(p[k]) <= 40:
            out[k] = p[k]
    return out


def mirror_shards_sync(c: sqlite3.Connection, *, tenant_id: str, holder_id: str, mode: str, report: Mapping[str, Any],
                       known_domains: set[str] | None = None) -> int:
    """Upsert the holder's shard rows and migration rows from a heartbeat ``shards`` report. Returns the rows written."""
    now = now_iso()
    n = 0
    recs: dict[str, dict[str, Any]] = {}
    for rec in (report.get("recommendations") or [])[:MAX_SHARDS_REPORTED] if isinstance(report.get("recommendations"), list) else []:
        if isinstance(rec, Mapping) and isinstance(rec.get("shard_id"), str) and SHARD_ID_RE.match(rec["shard_id"]):
            recs[rec["shard_id"]] = {"shard_id": rec["shard_id"], "action": str(rec.get("action") or "split")[:20],
                                     "domain_ids": _domains(rec.get("domain_ids"), known_domains),
                                     "signals": _numbers(rec.get("signals")), "metric": str(rec.get("metric") or "")[:20],
                                     "moves": _numbers(rec.get("moves"))}
    items = report.get("items") if isinstance(report.get("items"), list) else []
    for it in items[:MAX_SHARDS_REPORTED]:
        if not isinstance(it, Mapping):
            continue
        sid = it.get("shard_id")
        if not isinstance(sid, str) or not SHARD_ID_RE.match(sid):
            continue
        status = it.get("status") if it.get("status") in SHARD_STATUSES else "active"
        health = it.get("health") if it.get("health") in HEALTH else "ok"
        try:
            ordinal = int(it.get("ordinal") or 0)
            schema = int(it.get("schema_version") or 0)
        except (TypeError, ValueError):
            continue
        fname = it.get("file_name") if isinstance(it.get("file_name"), str) and FILE_RE.match(it["file_name"]) else ("evidence.db" if sid == "s0" else f"{sid}.db")
        placement = f"embedded:{holder_id}/{fname}" if mode == "embedded" else "external"
        backup = it.get("last_backup_at") if isinstance(it.get("last_backup_at"), str) and len(it["last_backup_at"]) <= 40 else None
        writer = it.get("writer_id") if isinstance(it.get("writer_id"), str) and len(it["writer_id"]) <= 120 else None
        rec = recs.get(sid)
        row = c.execute("SELECT tenant_id, writer_id, writer_lease_until FROM shards WHERE holder_id=? AND shard_id=?", (holder_id, sid)).fetchone()
        if row is not None and row["tenant_id"] != tenant_id:
            continue
        if row is None:
            if c.execute("SELECT 1 FROM shards WHERE holder_id=? AND ordinal=? AND shard_id<>?", (holder_id, ordinal, sid)).fetchone():
                c.execute("DELETE FROM shards WHERE holder_id=? AND ordinal=? AND shard_id<>? AND status='retired'", (holder_id, ordinal, sid))
            c.execute("""INSERT OR IGNORE INTO shards(shard_id, tenant_id, holder_id, ordinal, partition, placement, status, schema_version, stats, health,
                                                      recommendation, last_backup_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (sid, tenant_id, holder_id, ordinal, j(_partition(it.get("partition"), known_domains)), placement, status, schema,
                       j(_numbers(it.get("stats"))), health, j(rec) if rec else None, backup, now, now))
        else:
            c.execute("""UPDATE shards SET ordinal=?, partition=?, placement=?, status=?, schema_version=?, stats=?, health=?, recommendation=?,
                                last_backup_at=COALESCE(?, last_backup_at), updated_at=? WHERE holder_id=? AND shard_id=?""",
                      (ordinal, j(_partition(it.get("partition"), known_domains)), placement, status, schema, j(_numbers(it.get("stats"))), health,
                       j(rec) if rec else None, backup, now, holder_id, sid))
        if writer and status != "retired":
            acquire_lease_sync(c, holder_id=holder_id, shard_id=sid, writer_id=writer)
        n += 1
    migs = report.get("migrations") if isinstance(report.get("migrations"), list) else []
    for m in migs[:20]:
        if isinstance(m, Mapping):
            n += record_migration_sync(c, tenant_id=tenant_id, holder_id=holder_id, migration=m, known_domains=known_domains)
    return n


def acquire_lease_sync(c: sqlite3.Connection, *, holder_id: str, shard_id: str, writer_id: str, seconds: float = LEASE_SECONDS) -> bool:
    """The §7.6 writer lease: taken when free, expired, or already ours; renewed by every heartbeat."""
    now = now_iso()
    cur = c.execute("""UPDATE shards SET writer_id=?, writer_lease_until=? WHERE holder_id=? AND shard_id=?
                       AND (writer_id IS NULL OR writer_lease_until IS NULL OR writer_lease_until < ? OR writer_id=?)""",
                    (writer_id, plus_seconds(seconds), holder_id, shard_id, now, writer_id))
    return cur.rowcount == 1


def record_migration_sync(c: sqlite3.Connection, *, tenant_id: str, holder_id: str, migration: Mapping[str, Any],
                          known_domains: set[str] | None = None) -> int:
    mid = migration.get("migration_id")
    if not isinstance(mid, str) or not MIGRATION_ID_RE.match(mid):
        return 0
    row = c.execute("SELECT tenant_id, holder_id FROM shard_migrations WHERE migration_id=?", (mid,)).fetchone()
    if row is not None and (row["tenant_id"] != tenant_id or row["holder_id"] != holder_id):
        return 0
    state = migration.get("state") if migration.get("state") in MIGRATION_STATES else "failed"
    frm = migration.get("from_shard") if isinstance(migration.get("from_shard"), str) and SHARD_ID_RE.match(migration["from_shard"]) else "s0"
    to = migration.get("to_shard") if isinstance(migration.get("to_shard"), str) and SHARD_ID_RE.match(migration["to_shard"]) else ""
    ts = {k: (migration.get(k) if isinstance(migration.get(k), str) and len(migration[k]) <= 40 else None)
          for k in ("started_at", "cutover_at", "finished_at")}
    code = str(migration.get("error_code"))[:64] if migration.get("error_code") else None
    who = str(migration.get("requested_by") or "")[:80]
    cp = _numbers(migration.get("checkpoint"))
    part = j(_partition(migration.get("partition"), known_domains))
    now = now_iso()
    if row is None:
        c.execute("""INSERT INTO shard_migrations(migration_id, tenant_id, holder_id, kind, from_shard, to_shard, partition, state, checkpoint, requested_by,
                                                  started_at, cutover_at, finished_at, error_code, updated_at) VALUES (?, ?, ?, 'split', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (mid, tenant_id, holder_id, frm, to, part, state, j(cp), who, ts["started_at"], ts["cutover_at"], ts["finished_at"], code, now))
    else:
        c.execute("""UPDATE shard_migrations SET state=?, checkpoint=?, partition=?, to_shard=?, started_at=COALESCE(?, started_at),
                            cutover_at=COALESCE(?, cutover_at), finished_at=COALESCE(?, finished_at), error_code=?, updated_at=? WHERE migration_id=?""",
                  (state, j(cp), part, to, ts["started_at"], ts["cutover_at"], ts["finished_at"], code, now, mid))
    return 1


def shard_view(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"shard_id": r["shard_id"], "holder_id": r["holder_id"], "ordinal": int(r["ordinal"]), "partition": jl(r["partition"], {}),
            "placement": r["placement"], "status": r["status"], "health": r["health"], "stats": jl(r["stats"], {}),
            "last_backup_at": r["last_backup_at"], "writer_id": r["writer_id"], "writer_lease_until": r["writer_lease_until"],
            "schema_version": int(r["schema_version"]), "updated_at": r["updated_at"]}


def migration_view(r: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("migration_id", "holder_id", "kind", "from_shard", "to_shard", "state", "requested_by", "started_at", "cutover_at", "finished_at",
            "error_code", "updated_at")
    out = {k: r[k] for k in keys if k in r.keys()}
    out["partition"] = jl(r["partition"], {})
    out["checkpoint"] = jl(r["checkpoint"], {})
    return out


def list_shards(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items, recs = [], []
    for r in rows:
        items.append(shard_view(r))
        rec = jl(r["recommendation"], None) if r["recommendation"] else None
        if rec:
            recs.append({**rec, "holder_id": r["holder_id"]})
    return items, recs

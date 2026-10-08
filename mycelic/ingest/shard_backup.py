"""Backup and restore of one holder's shards (docs/mycelic/INGESTION.md §7.8, §9.6 "Backups").

* :func:`backup_shards` takes an online SQLite backup of every shard file in the holder's ``shard_map`` (the backup API,
  so writers keep going), with a manifest: the shard map snapshot, each file's sha256 and holder schema versions, and the
  holder's ``change_seq``. Each shard's per-shard write gate is held during the copy, so the files form one consistent
  cut of the pipeline's writes (the doc's "pause the queue lease, back up all shards, resume").
* :func:`restore_shards` puts the files back (the files it replaces are moved aside, never deleted) and, before the holder
  goes online, replays deletions: the tombstones of the store being replaced (newer than the backup), the restored
  tombstones, and the coordinator's ``deletion_ledger`` rows when given (matched by ``sha256(record_key)``). Every record
  they name is purged again, wherever its shard is, so deleted content never comes back from a backup.

Deleted content may still exist inside backup files until they expire (``backup_retention_days``): that is the documented
upper bound; a restore never brings it back into a live store.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from datetime import timedelta

from ..util import iso, jl, now_iso, utcnow
from .intents import TOMBSTONE_RETENTION_DAYS
from .shards import DEFAULT_SHARD, QUERYABLE, SHARD_FILE_RE

MANIFEST = "manifest.json"
FORMAT = "mycelic-holder-shards"
FORMAT_VERSION = 1


def ledger_key(record_key: str) -> str:
    """The coordinator's ``deletion_ledger.record_key_hash`` (unlinkable outside the holder)."""
    return hashlib.sha256(str(record_key).encode("utf-8")).hexdigest()


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _schema(path: Path) -> list[int]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return [int(r[0]) for r in conn.execute("SELECT version FROM holder_schema_migrations ORDER BY version")]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


async def backup_shards(store: Any, out_dir: str | Path) -> dict[str, Any]:
    """Online backup of every live shard file of the holder ``store`` (its s0 ``EvidenceStore``) into ``out_dir``."""
    from ..observability import sqlite_backup
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shards = store.shards
    specs = shards.specs(statuses=QUERYABLE)
    started = now_iso()
    files: list[dict[str, Any]] = []
    async with contextlib.AsyncExitStack() as stack:
        for spec in specs:                                    # one consistent cut of the pipeline's writes
            await stack.enter_async_context(shards.gate(spec.shard_id))
        seq_row = store.store._conn.execute("SELECT value FROM holder_meta WHERE key='ingest_change_seq'").fetchone()
        for spec in specs:
            if spec.shard_id == DEFAULT_SHARD:
                src, name = Path(store.path), "evidence.db"
            else:
                src, name = shards.path_for(spec.file_name), spec.file_name
                await shards.store(spec.shard_id).store.checkpoint_wal()
            info = await asyncio.to_thread(sqlite_backup, src, out / name)
            files.append({"shard_id": spec.shard_id, "file_name": name, **info, "schema": _schema(out / name)})
    finished = now_iso()
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "holder_id": store.holder_id, "tenant_id": store.tenant_id,
                "started_at": started, "backup_at": finished, "change_seq": int(seq_row["value"]) if seq_row else 0,
                "shard_map": [{"shard_id": s.shard_id, "ordinal": s.ordinal, "file_name": s.file_name, "domain_ids": list(s.domain_ids),
                               "status": s.status} for s in specs], "files": files}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    def mark(c):
        for f in files:
            c.execute("UPDATE shard_map SET last_backup_at=? WHERE shard_id=?", (finished, f["shard_id"]))
    await store.store.run_in_tx(mark)
    return manifest


def _tombstones(path: Path) -> list[dict[str, Any]]:
    """The content-free tombstones of a holder file (read-only), or nothing when the file is missing or unreadable."""
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM deletion_tombstones WHERE resurrectable=0")]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


async def restore_shards(backup_dir: str | Path, s0_path: str | Path, *, holder_id: str, tenant_id: str,
                         ledger: Iterable[Mapping[str, Any]] | None = None, llm: Any = None, owner_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Restore a holder's shard files from :func:`backup_shards` output, then replay deletions before the holder goes online.

    The holder must be stopped (no store open on these files). ``s0_path`` is the holder's ``evidence.db``; data shard files
    are restored next to it. Returns ``{restored, moved_aside, replay}``."""
    from ..evidence.service import EvidenceStore
    src = Path(backup_dir)
    manifest = json.loads((src / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT or int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise ValueError("not a holder shard backup this code can restore")
    if manifest.get("holder_id") != holder_id or manifest.get("tenant_id") != tenant_id:
        raise ValueError("the backup belongs to another holder")         # a shard file resolves only through its own holder
    s0 = Path(s0_path)
    base = s0.parent
    newer = _tombstones(s0)                    # deletions the live store knows about, including those after the backup
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    moved, restored = [], []
    for f in manifest.get("files") or []:
        name = str(f.get("file_name") or "")
        if f.get("shard_id") == DEFAULT_SHARD:
            dst = s0
        elif SHARD_FILE_RE.match(name) and name.startswith("shd_") and name.endswith(".db"):
            dst = base / name
        else:
            raise ValueError(f"unsafe shard file name in the backup: {name!r}")
        if _sha256_file(src / name) != f.get("sha256"):
            raise ValueError(f"backup file {name} does not match its manifest checksum")
        if dst.exists():
            aside = dst.with_name(f"{dst.name}.pre-restore.{stamp}")
            dst.rename(aside)
            moved.append(str(aside))
        for suffix in ("-wal", "-shm", "-journal"):
            side = Path(str(dst) + suffix)
            if side.exists():
                side.unlink()
        shutil.copyfile(src / name, dst)
        restored.append(str(dst))
    store = EvidenceStore(s0, holder_id=holder_id, tenant_id=tenant_id, llm=llm, owner_ids=list(owner_ids))
    try:
        replay = await replay_deletions(store, tombstones=newer, ledger=ledger)
    finally:
        await store.close()
    return {"restored": restored, "moved_aside": moved, "replay": replay, "backup_at": manifest.get("backup_at")}


async def replay_deletions(store: Any, *, tombstones: Iterable[Mapping[str, Any]] = (), ledger: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Re-apply deletions to a (restored) holder: every tombstone given, every tombstone the store already has, and every
    ledger row whose hash matches a record of the holder. Content of a named record is purged in whichever shard holds it;
    the tombstone keeps the original reason and references so late events cannot resurrect the record."""
    await store.shards.recover()
    c = store.store._conn
    wanted: dict[str, dict[str, Any]] = {}
    for t in [*tombstones, *(dict(r) for r in c.execute("SELECT * FROM deletion_tombstones WHERE resurrectable=0"))]:
        if t.get("record_key") and not int(t.get("resurrectable") or 0):
            wanted.setdefault(str(t["record_key"]), dict(t))
    if ledger:
        by_hash = {ledger_key(r["record_key"]): (r["record_key"], r["record_id"]) for r in c.execute("SELECT record_key, record_id FROM record_locator")}
        for row in ledger:
            hit = by_hash.get(str(row.get("record_key_hash") or ""))
            if hit is not None:
                wanted.setdefault(hit[0], {"record_key": hit[0], "record_id": hit[1], "reason": row.get("reason") or "deleted_at_source",
                                           "requested_at": row.get("requested_at"), "affected_ref_ids": row.get("ref_ids") or "[]"})
    purged = 0
    for rk, t in wanted.items():
        loc = c.execute("SELECT record_id, shard_id FROM record_locator WHERE record_key=?", (rk,)).fetchone()
        rid = loc["record_id"] if loc is not None else t.get("record_id")
        if not rid:
            continue
        owner = store._doc_store(rid)
        doc = await owner.store.get_document(rid)
        live = owner.store._conn.execute("SELECT deletion_status FROM ingest_records WHERE record_id=?", (rid,)).fetchone()
        content = doc is not None and doc["status"] in ("active", "revised", "redacted", "suspended")
        if content or (live is not None and live[0] not in ("purged",)):
            if content:
                await store.delete_document(rid, str(t.get("reason") or "restore_replay"), status="deleted")
            else:
                await owner.store.run_in_tx(lambda cc, rid=rid: cc.execute("UPDATE ingest_records SET deletion_status='purged' WHERE record_id=?", (rid,)))
            purged += 1
        refs = t.get("affected_ref_ids")
        refs = refs if isinstance(refs, str) else json.dumps(list(refs or []))

        def keep(cc, rk=rk, rid=rid, t=t, refs=refs, shard=(loc["shard_id"] if loc is not None else DEFAULT_SHARD)):
            now = now_iso()
            expires = t.get("expires_at") or iso(utcnow() + timedelta(days=TOMBSTONE_RETENTION_DAYS))
            cc.execute("""INSERT INTO deletion_tombstones(record_key, record_id, shard_id, reason, order_key, resurrectable, requested_at, purged_at,
                                                          propagated_at, affected_ref_ids, expires_at)
                          VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                          ON CONFLICT(record_key) DO UPDATE SET resurrectable=0, purged_at=COALESCE(deletion_tombstones.purged_at, excluded.purged_at),
                          propagated_at=COALESCE(deletion_tombstones.propagated_at, excluded.propagated_at)""",
                       (rk, rid, shard, str(t.get("reason") or "restore_replay"), t.get("order_key"), t.get("requested_at") or now, now,
                        t.get("propagated_at"), refs, expires))
            cc.execute("UPDATE record_locator SET deletion_status='purged', updated_at=? WHERE record_key=?", (now, rk))
        await store.store.run_in_tx(keep)
    return {"tombstones": len(wanted), "purged": purged}


def ledger_rows(db: Any, *, tenant_id: str, holder_id: str, since: str | None = None) -> list[dict[str, Any]]:
    """The coordinator's content-free deletion ledger for one holder (rows requested after ``since``)."""
    sql, args = "SELECT * FROM deletion_ledger WHERE tenant_id=? AND holder_id=?", [tenant_id, holder_id]
    if since:
        sql += " AND requested_at>?"
        args.append(since)
    return [dict(r, ref_ids=jl(r["ref_ids"], [])) for r in db.all(sql, args)]

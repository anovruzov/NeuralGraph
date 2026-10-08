"""Control-shard operations owed by a write to a data shard (docs/mycelic/INGESTION.md §7.1, §9.1).

SQLite commits one file at a time. A record that lives in a data shard has its content in that shard and its catalog
entries in the control shard ``s0`` (locator, queue, outbox, tombstones, exports, idempotency outcomes). The write is
therefore two commits, ordered and idempotent:

1. the shard transaction writes the effect, its ``applied_events`` marker, and the list of control ops below into
   ``shard_control_intents`` (same transaction, so the ops exist exactly when the effect does);
2. the ops are applied to ``s0`` in one transaction, and the intent row is then deleted from the shard.

A crash between 1 and 2 leaves the intent row behind; :func:`apply_ops_sync` is replayed on the next open (or before the
next pipeline pass) and every op is written so that applying it twice changes nothing. When the record lives in ``s0``
itself the same ops run inside the effect transaction (:meth:`MycelicMemoryStore.control_op`), which is exactly what the
single-store code did before sharding.

Ops carry ids, hashes, counts and domain ids only: never content.
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any, Callable, Iterable, Mapping

from ..util import iso, j, now_iso, now_precise, parse_iso

TOMBSTONE_RETENTION_DAYS = 400


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def _locator(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    from .store import IngestStore
    IngestStore.upsert_locator_sync(c, record_key=p["record_key"], record_id=p["record_id"], shard_id=p["shard_id"], kind=p["kind"],
                                    order_key=p.get("order_key"), content_hash=p.get("content_hash"), metadata_hash=p.get("metadata_hash"),
                                    deletion_status=p["deletion_status"], change_seq=int(p["change_seq"]))


def _locator_status(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    """A purge outside the pipeline (the owner's retract): the locator follows, and the change is visible to a resharding
    catch-up through ``change_seq`` (§9.6 H1 step 9)."""
    row = c.execute("SELECT value FROM holder_meta WHERE key='ingest_change_seq'").fetchone()
    seq = int(row["value"]) + 1 if row else 1
    c.execute("INSERT INTO holder_meta(key, value) VALUES ('ingest_change_seq', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(seq),))
    c.execute("UPDATE record_locator SET deletion_status=?, change_seq=?, updated_at=? WHERE record_id=?",
              (p["deletion_status"], seq, p.get("now") or now_precise(), p["record_id"]))


def _touch(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    """A change to a record that does not go through the pipeline (a domain correction): bump its ``change_seq`` so a
    resharding catch-up sees it."""
    row = c.execute("SELECT value FROM holder_meta WHERE key='ingest_change_seq'").fetchone()
    seq = int(row["value"]) + 1 if row else 1
    c.execute("INSERT INTO holder_meta(key, value) VALUES ('ingest_change_seq', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(seq),))
    c.execute("UPDATE record_locator SET change_seq=?, updated_at=? WHERE record_id=?", (seq, now_precise(), p["record_id"]))


def _applied(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    # s0 keeps every applied event of the holder (the dedupe index), wherever the effect landed
    c.execute("INSERT OR IGNORE INTO applied_events(event_key, record_id, outcome, applied_at) VALUES (?, ?, ?, ?)",
              (p["event_key"], p["record_id"], p["outcome"], p.get("at") or now_iso()))


def _ack(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    """The effect is durable in its shard: the queue item is finished whoever holds its lease now (a re-lease after a
    crash finds the item done, and ``applied_events`` makes any re-run a duplicate)."""
    now = now_precise()
    c.execute("""UPDATE ingest_queue SET status='done', outcome=?, payload=NULL, payload_bytes=0, finished_at=?, updated_at=?, leased_until=NULL
                 WHERE item_id=? AND status IN ('leased', 'queued')""", (p["outcome"], now, now, int(p["item_id"])))


def _domain_counts(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    for d, n in (p.get("counts") or {}).items():
        c.execute("INSERT OR REPLACE INTO domain_shard_counts(domain_id, shard_id, records) VALUES (?, ?, ?)", (d, p["shard_id"], int(n)))


def _batch(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    from .store import IngestStore
    IngestStore.note_batch_sync(c, source_app=p["source_app"], domains=list(p.get("domains") or []))


def _outbox(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    from .store import IngestStore
    IngestStore.add_outbox_sync(c, p["msg_id"], p["kind"], dict(p.get("payload") or {}))


def _tombstone(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    from .store import IngestStore
    IngestStore.upsert_tombstone_sync(c, record_key=p["record_key"], record_id=p["record_id"], reason=p["reason"], order_key=p.get("order_key"),
                                      resurrectable=bool(p.get("resurrectable")), affected_ref_ids=list(p.get("affected_ref_ids") or []),
                                      purged=bool(p.get("purged")), shard_id=p.get("shard_id") or "s0")


def _tombstone_min(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    """The content-free tombstone a purge leaves whichever path purged (``_retire_ingest_rows_sync``)."""
    now = p.get("now") or now_iso()
    expires = (parse_iso(now) or parse_iso(now_iso())) + timedelta(days=TOMBSTONE_RETENTION_DAYS)  # type: ignore[operator]
    c.execute("""INSERT OR IGNORE INTO deletion_tombstones(record_key, record_id, shard_id, reason, order_key, resurrectable, requested_at,
                                                           purged_at, affected_ref_ids, expires_at)
                 VALUES (?, ?, ?, ?, NULL, 0, ?, ?, '[]', ?)""", (p["record_key"], p["record_id"], p.get("shard_id") or "s0", p.get("reason") or "purged",
                                                                  now, now, iso(expires)))


def _drop_tombstone(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    c.execute("DELETE FROM deletion_tombstones WHERE record_key=? AND resurrectable=1", (p["record_key"],))


def _discard_queue(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    """Queued or dead-lettered payloads for a deleted record leave the queue (content goes with the record)."""
    now = p.get("now") or now_iso()
    if p.get("content_only"):
        c.execute("UPDATE ingest_queue SET payload=NULL, payload_bytes=0, status='discarded', updated_at=? WHERE record_key=? "
                  "AND status IN ('queued', 'dead') AND kind NOT IN ('deletion', 'redaction') AND item_id<>?",
                  (now, p["record_key"], int(p.get("except_item") or -1)))
    else:
        c.execute("UPDATE ingest_queue SET payload=NULL, payload_bytes=0, status='discarded', updated_at=? WHERE record_key=? AND status IN ('queued', 'dead')",
                  (now, p["record_key"]))


def _exports_scrub(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    """The export ledger keeps the reference ids (later events still name them) and drops the disclosed excerpts."""
    c.execute("UPDATE exports SET disclosed_excerpt='' WHERE doc_id=?", (p["doc_id"],))
    ids = [m for m in p.get("memory_ids") or [] if m]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        c.execute(f"UPDATE exports SET disclosed_excerpt='' WHERE memory_id IN ({_in(part)})", part)


def _scrub_outcomes(c: sqlite3.Connection, p: Mapping[str, Any]) -> None:
    from ..evidence.store import MycelicMemoryStore
    MycelicMemoryStore._scrub_outcomes_sync(c, p["doc_id"], set(p.get("ref_ids") or []), tombstone=dict(p.get("tombstone") or {}))


CONTROL_OPS: dict[str, Callable[[sqlite3.Connection, Mapping[str, Any]], None]] = {
    "locator": _locator, "locator_status": _locator_status, "touch": _touch, "applied": _applied, "ack": _ack, "domain_counts": _domain_counts,
    "batch": _batch, "outbox": _outbox, "tombstone": _tombstone, "tombstone_min": _tombstone_min, "drop_tombstone": _drop_tombstone,
    "discard_queue": _discard_queue, "exports_scrub": _exports_scrub, "scrub_outcomes": _scrub_outcomes,
}


def run_op_sync(c: sqlite3.Connection, name: str, params: Mapping[str, Any]) -> None:
    fn = CONTROL_OPS.get(name)
    if fn is None:
        raise ValueError(f"unknown control op {name!r}")
    fn(c, params)


def apply_ops_sync(c: sqlite3.Connection, ops: Iterable[Mapping[str, Any]]) -> int:
    n = 0
    for op in ops:
        params = dict(op)
        run_op_sync(c, str(params.pop("op")), params)
        n += 1
    return n


def write_intent_sync(c: sqlite3.Connection, ops: list[dict[str, Any]], *, intent_id: str) -> None:
    c.execute("INSERT INTO shard_control_intents(intent_id, kind, payload, created_at) VALUES (?, 'tx', ?, ?)",
              (intent_id, j({"ops": ops}), now_precise()))

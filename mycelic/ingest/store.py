"""Typed accessors over the holder tables of INGESTION.md §5.2 (connectors, sources, records, locator, tombstones,
outbox, metrics). Every ``*_sync`` method runs inside the caller's transaction; the async readers use the store's single
connection (all SQLite access stays on the event-loop thread)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Mapping

from ..util import j, jl, new_id, now_iso, now_precise, parse_iso, iso
from .contract import SourceDescriptor
from .crypto import SealedCredentials
from .events import CanonicalEvent, Permissions

TOMBSTONE_RETENTION_DAYS = 400


@dataclass
class SourceRow:
    source_id: str
    connector_id: str
    source_type: str
    external_id: str
    name: str
    parent_external_id: str | None
    selection: str
    selection_reason: str
    visibility: str
    member_ids: list[str]
    membership_ref: str | None
    exportable: bool
    disclosure: str | None
    default_domain_ids: list[str]
    sensitivity: str
    retention_policy: str
    allow_external_models: int | None
    access_state: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def permissions(self) -> Permissions:
        return Permissions(self.visibility, tuple(self.member_ids), self.membership_ref)

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(source_type=self.source_type, external_id=self.external_id, name=self.name,
                                parent_external_id=self.parent_external_id, visibility=self.visibility, member_ids=tuple(self.member_ids),
                                membership_ref=self.membership_ref, suggested_domain_ids=tuple(self.default_domain_ids), metadata=dict(self.metadata))


class IngestStore:
    def __init__(self, store: Any, *, shardset: Any = None) -> None:
        self.store = store               # MycelicMemoryStore (the control shard s0)
        self.shardset = shardset         # the holder's ShardSet: records may live in data shards (INGESTION.md §7)

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store._conn

    def _sharded(self) -> bool:
        return self.shardset is not None and self.shardset.has_data_shards()

    def record_conns(self) -> list[sqlite3.Connection]:
        """Connections of every shard that holds records (just s0 until a split)."""
        if not self._sharded():
            return [self.conn]
        return [st.store._conn for _spec, st in self.shardset.open_stores(statuses=("active", "draining", "readonly"))]

    def _record_conn(self, record_id: str) -> sqlite3.Connection:
        if not self._sharded():
            return self.conn
        return self.shardset.store_of_record(record_id).store._conn

    async def tx(self, fn):
        return await self.store.run_in_tx(fn)

    # ------------------------------------------------------------------ counters
    @staticmethod
    def next_change_seq_sync(c: sqlite3.Connection) -> int:
        r = c.execute("SELECT value FROM holder_meta WHERE key='ingest_change_seq'").fetchone()
        n = int(r["value"]) + 1 if r else 1
        c.execute("INSERT INTO holder_meta(key, value) VALUES ('ingest_change_seq', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(n),))
        return n

    # ------------------------------------------------------------------ connectors
    def insert_connector_sync(self, c: sqlite3.Connection, row: Mapping[str, Any]) -> None:
        now = now_iso()
        c.execute("""INSERT INTO connectors(connector_id, connector_type, source_app, manifest_version, display_name, source_account_id, auth_account_id,
                                            account_label, auth_kind, granted_scopes, ownership, mode, status, status_code, config, created_by,
                                            created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)""",
                  (row["connector_id"], row["connector_type"], row["source_app"], row["manifest_version"], row["display_name"], row["source_account_id"],
                   row.get("auth_account_id") or "", row.get("account_label") or "", row.get("auth_kind") or "none", j(list(row.get("granted_scopes") or [])),
                   row.get("ownership") or "personal", row.get("mode") or "pull", row.get("status") or "active", j(dict(row.get("config") or {})),
                   row["created_by"], now, now))

    def get_connector(self, connector_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM connectors WHERE connector_id=?", (connector_id,)).fetchone()
        return self._connector(r)

    @staticmethod
    def _connector(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        d["config"] = jl(d.get("config"), {})
        d["granted_scopes"] = jl(d.get("granted_scopes"), [])
        d["health"] = jl(d.get("health"), {})
        return d

    def list_connectors(self) -> list[dict[str, Any]]:
        return [self._connector(r) for r in self.conn.execute("SELECT * FROM connectors ORDER BY created_at, connector_id").fetchall()]  # type: ignore[misc]

    @staticmethod
    def set_connector_status_sync(c: sqlite3.Connection, connector_id: str, status: str, code: str = "", *, success: bool = False) -> None:
        now = now_iso()
        c.execute("UPDATE connectors SET status=?, status_code=?, updated_at=?, last_sync_at=?, last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END "
                  "WHERE connector_id=?", (status, code, now, now, 1 if success else 0, now, connector_id))

    # ------------------------------------------------------------------ credentials
    @staticmethod
    def put_credentials_sync(c: sqlite3.Connection, connector_id: str, sealed: SealedCredentials, *, expires_at: str | None = None) -> None:
        now = now_iso()
        c.execute("""INSERT INTO connector_credentials(connector_id, kid, wrapped_dek, ciphertext, aad, expires_at, created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(connector_id) DO UPDATE SET kid=excluded.kid, wrapped_dek=excluded.wrapped_dek,
                     ciphertext=excluded.ciphertext, aad=excluded.aad, expires_at=excluded.expires_at, updated_at=excluded.updated_at""",
                  (connector_id, sealed.kid, sealed.wrapped_dek, sealed.ciphertext, sealed.aad, expires_at, now, now))

    def get_credentials(self, connector_id: str) -> SealedCredentials | None:
        r = self.conn.execute("SELECT kid, wrapped_dek, ciphertext, aad FROM connector_credentials WHERE connector_id=?", (connector_id,)).fetchone()
        if r is None:
            return None
        return SealedCredentials(kid=r["kid"], wrapped_dek=bytes(r["wrapped_dek"]), ciphertext=bytes(r["ciphertext"]), aad=r["aad"])

    @staticmethod
    def delete_credentials_sync(c: sqlite3.Connection, connector_id: str) -> int:
        """Crypto-shredding: the wrapped DEK goes with the row."""
        return c.execute("DELETE FROM connector_credentials WHERE connector_id=?", (connector_id,)).rowcount

    # ------------------------------------------------------------------ sources
    @staticmethod
    def _source(r: sqlite3.Row | None) -> SourceRow | None:
        if r is None:
            return None
        return SourceRow(source_id=r["source_id"], connector_id=r["connector_id"], source_type=r["source_type"], external_id=r["external_id"],
                         name=r["name"], parent_external_id=r["parent_external_id"], selection=r["selection"], selection_reason=r["selection_reason"],
                         visibility=r["visibility"], member_ids=jl(r["member_ids"], []), membership_ref=r["membership_ref"],
                         exportable=bool(r["exportable"]), disclosure=r["disclosure"], default_domain_ids=jl(r["default_domain_ids"], []),
                         sensitivity=r["sensitivity"], retention_policy=r["retention_policy"], allow_external_models=r["allow_external_models"],
                         access_state=r["access_state"], metadata=jl(r["metadata"], {}))

    def get_source(self, source_id: str) -> SourceRow | None:
        return self._source(self.conn.execute("SELECT * FROM connector_sources WHERE source_id=?", (source_id,)).fetchone())

    def source_by_external(self, connector_id: str, external_id: str, source_type: str | None = None) -> SourceRow | None:
        if source_type:
            r = self.conn.execute("SELECT * FROM connector_sources WHERE connector_id=? AND source_type=? AND external_id=?",
                                  (connector_id, source_type, external_id)).fetchone()
        else:
            r = self.conn.execute("SELECT * FROM connector_sources WHERE connector_id=? AND external_id=? ORDER BY source_type LIMIT 1",
                                  (connector_id, external_id)).fetchone()
        return self._source(r)

    def list_sources(self, connector_id: str, *, selection: str | None = None) -> list[SourceRow]:
        sql, args = "SELECT * FROM connector_sources WHERE connector_id=?", [connector_id]
        if selection:
            sql += " AND selection=?"
            args.append(selection)
        return [self._source(r) for r in self.conn.execute(sql + " ORDER BY discovered_at, source_id", args).fetchall()]  # type: ignore[misc]

    def upsert_source_sync(self, c: sqlite3.Connection, connector_id: str, desc: SourceDescriptor, *, selection: str, reason: str,
                           exportable: bool) -> tuple[str, bool]:
        """Returns ``(source_id, created)``. An existing source keeps its owner decisions (selection, opt-ins, mappings);
        its ACL is refreshed from the provider."""
        now = now_iso()
        r = c.execute("SELECT source_id, visibility FROM connector_sources WHERE connector_id=? AND source_type=? AND external_id=?",
                      (connector_id, desc.source_type, desc.external_id)).fetchone()
        if r is not None:
            c.execute("""UPDATE connector_sources SET name=?, parent_external_id=?, visibility=?, member_ids=?, membership_ref=?, metadata=?, updated_at=?
                         WHERE source_id=?""", (desc.name, desc.parent_external_id, desc.visibility, j(sorted(set(desc.member_ids))),
                                                desc.membership_ref, j(dict(desc.metadata)), now, r["source_id"]))
            if desc.visibility == "private" and r["visibility"] != "private":
                # an opt-in given for a public or members channel does not carry over to a private one: the owner decides again
                c.execute("UPDATE connector_sources SET exportable=0 WHERE source_id=?", (r["source_id"],))
            return r["source_id"], False
        sid = new_id("src")
        c.execute("""INSERT INTO connector_sources(source_id, connector_id, source_type, external_id, name, parent_external_id, selection, selection_reason,
                                                   visibility, member_ids, membership_ref, exportable, default_domain_ids, metadata, discovered_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (sid, connector_id, desc.source_type, desc.external_id, desc.name, desc.parent_external_id, selection, reason, desc.visibility,
                   j(sorted(set(desc.member_ids))), desc.membership_ref, 1 if exportable else 0, j(list(desc.suggested_domain_ids)),
                   j(dict(desc.metadata)), now, now))
        return sid, True

    @staticmethod
    def update_source_sync(c: sqlite3.Connection, source_id: str, **fields: Any) -> None:
        allowed = {"selection", "selection_reason", "exportable", "disclosure", "default_domain_ids", "sensitivity", "retention_policy",
                   "allow_external_models", "access_state", "access_lost_at", "last_synced_at", "visibility", "member_ids", "membership_ref"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update source fields {sorted(bad)}")
        if not fields:
            return
        sets, args = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            if k in ("default_domain_ids", "member_ids"):
                args.append(j(list(v)))
            elif k == "exportable":
                args.append(1 if v else 0)
            else:
                args.append(v)
        sets.append("updated_at=?")
        args.extend([now_iso(), source_id])
        c.execute(f"UPDATE connector_sources SET {', '.join(sets)} WHERE source_id=?", args)

    @staticmethod
    def set_membership_sync(c: sqlite3.Connection, membership_ref: str, member_ids: Iterable[str]) -> None:
        now = now_iso()
        c.execute("DELETE FROM acl_memberships WHERE membership_ref=?", (membership_ref,))
        c.executemany("INSERT OR IGNORE INTO acl_memberships(membership_ref, member_id, updated_at) VALUES (?, ?, ?)",
                      [(membership_ref, str(m), now) for m in set(member_ids) if m])

    # ------------------------------------------------------------------ exclusions
    @staticmethod
    def add_exclusion_sync(c: sqlite3.Connection, *, connector_id: str | None, scope: str, match: str, action: str, reason: str, created_by: str) -> str:
        eid = new_id("exc")
        c.execute("""INSERT INTO source_exclusions(exclusion_id, connector_id, scope, match, action, reason, created_by, created_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (eid, connector_id, scope, match, action, reason, created_by, now_iso()))
        return eid

    def exclusions(self, connector_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM source_exclusions WHERE (connector_id IS NULL OR connector_id=?) AND action='exclude'",
                                 (connector_id,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ locator / records / tombstones
    def get_locator(self, record_key: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM record_locator WHERE record_key=?", (record_key,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def upsert_locator_sync(c: sqlite3.Connection, *, record_key: str, record_id: str, shard_id: str, kind: str, order_key: str | None,
                            content_hash: str | None, metadata_hash: str | None, deletion_status: str, change_seq: int) -> None:
        c.execute("""INSERT INTO record_locator(record_key, record_id, shard_id, kind, current_order_key, content_hash, metadata_hash, deletion_status,
                                                change_seq, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(record_key) DO UPDATE SET current_order_key=excluded.current_order_key, content_hash=excluded.content_hash,
                     metadata_hash=excluded.metadata_hash, deletion_status=excluded.deletion_status, change_seq=excluded.change_seq,
                     updated_at=excluded.updated_at""",
                  (record_key, record_id, shard_id, kind, order_key, content_hash, metadata_hash, deletion_status, change_seq, now_precise()))

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        r = self._record_conn(record_id).execute("SELECT * FROM ingest_records WHERE record_id=?", (record_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        for k, default in (("permissions", {}), ("flags", []), ("participant_entity_ids", [])):
            d[k] = jl(d.get(k), default)
        return d

    def _records_where(self, column: str, value: str, live_only: bool) -> list[dict[str, Any]]:
        sql = f"SELECT record_id FROM ingest_records WHERE {column}=?" + (" AND deletion_status='live'" if live_only else "")
        if not self._sharded():
            return [self.get_record(r["record_id"]) for r in self.conn.execute(sql, (value,)).fetchall()]  # type: ignore[misc]
        out: list[dict[str, Any]] = []
        for spec, st in self.shardset.open_stores(statuses=("active", "draining", "readonly")):
            for r in st.store._conn.execute(sql, (value,)).fetchall():
                # a record copied by a split and not yet cleaned up from its source is counted where the locator says it lives
                if self.shardset.shard_of_record(r["record_id"]) == spec.shard_id:
                    rec = self.get_record(r["record_id"])
                    if rec is not None:
                        out.append(rec)
        return out

    def records_of_source(self, source_id: str, *, live_only: bool = True) -> list[dict[str, Any]]:
        return self._records_where("source_id", source_id, live_only)

    def records_of_connector(self, connector_id: str, *, live_only: bool = True) -> list[dict[str, Any]]:
        return self._records_where("connector_id", connector_id, live_only)

    @staticmethod
    def write_record_sync(c: sqlite3.Connection, ev: CanonicalEvent, *, source_id: str | None, primary_domain_id: str | None,
                          content_changed_at: str | None, flags: Iterable[str], author_entity_id: str | None, participant_entity_ids: list[str],
                          normalizer_version: str, change_seq: int, new_version: bool) -> None:
        now = now_iso()
        perms = ev.permissions.to_dict()
        r = c.execute("SELECT record_id FROM ingest_records WHERE record_id=?", (ev.record_id,)).fetchone()
        conv = ev.conversation_record_id()
        if r is None:
            c.execute("""INSERT INTO ingest_records(record_id, record_key, object_key, kind, connector_id, source_id, source_app, source_account_id,
                                                    source_object_type, source_object_id, conversation_record_id, thread_record_id, parent_record_id,
                                                    author_entity_id, participant_entity_ids, created_at_src, updated_at_src, content_changed_at,
                                                    first_ingested_at, last_ingested_at, current_version, current_order_key, version_count, content_hash,
                                                    metadata_hash, source_root_id, root_known, root_method, primary_domain_id, visibility, permissions,
                                                    sensitivity, flags, retention_policy, deletion_status, normalizer_version, schema_version, change_seq)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, ?)""",
                      (ev.record_id, ev.record_key, ev.object_key, ev.kind, ev.connector_id, source_id, ev.source_app, ev.source_account_id,
                       ev.source_object_type, ev.source_object_id, conv, ev.thread_id, ev.parent_message_id, author_entity_id, j(participant_entity_ids),
                       ev.created_at, ev.updated_at, content_changed_at, now, now, ev.source_version, ev.order_key, ev.content_hash, ev.metadata_hash,
                       ev.source_root_id, 1 if ev.root_known else 0, ev.root_method, primary_domain_id, ev.permissions.visibility, j(perms),
                       ev.sensitivity, j(sorted(set(flags))), ev.retention_policy.policy_id, normalizer_version, ev.schema_version, change_seq))
            return
        sets = ["connector_id=?", "source_id=COALESCE(?, source_id)", "updated_at_src=?", "last_ingested_at=?", "current_order_key=?", "metadata_hash=?",
                "primary_domain_id=COALESCE(?, primary_domain_id)", "visibility=?", "permissions=?", "sensitivity=?", "flags=?", "deletion_status='live'",
                "normalizer_version=?", "change_seq=?", "participant_entity_ids=?"]
        args: list[Any] = [ev.connector_id, source_id, ev.updated_at, now, ev.order_key, ev.metadata_hash, primary_domain_id, ev.permissions.visibility,
                           j(perms), ev.sensitivity, j(sorted(set(flags))), normalizer_version, change_seq, j(participant_entity_ids)]
        if new_version:
            sets += ["current_version=?", "version_count=version_count+1", "content_hash=?", "source_root_id=?", "root_known=?", "root_method=?",
                     "content_changed_at=?"]
            args += [ev.source_version, ev.content_hash, ev.source_root_id, 1 if ev.root_known else 0, ev.root_method, content_changed_at]
        c.execute(f"UPDATE ingest_records SET {', '.join(sets)} WHERE record_id=?", (*args, ev.record_id))

    @staticmethod
    def add_version_sync(c: sqlite3.Connection, ev: CanonicalEvent, *, kind: str, doc_version: int | None, text_retained: bool = False) -> None:
        c.execute("""INSERT OR IGNORE INTO ingest_versions(record_id, version_key, source_version, order_key, kind, content_hash, metadata_hash,
                                                            source_event_id, text_retained, doc_version, observed_at, ingested_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (ev.record_id, ev.version_key + ("#" + kind if kind in ("deletion", "redaction", "historical") else ""), ev.source_version,
                   ev.order_key, kind, ev.content_hash, ev.metadata_hash, ev.source_event_id, 1 if text_retained else 0, doc_version, ev.observed_at,
                   now_iso()))

    @staticmethod
    def mark_applied_sync(c: sqlite3.Connection, event_key: str, record_id: str, outcome: str) -> None:
        c.execute("INSERT INTO applied_events(event_key, record_id, outcome, applied_at) VALUES (?, ?, ?, ?)", (event_key, record_id, outcome, now_iso()))

    def applied(self, event_key: str) -> str | None:
        r = self.conn.execute("SELECT outcome FROM applied_events WHERE event_key=?", (event_key,)).fetchone()
        return r["outcome"] if r else None

    def get_tombstone(self, record_key: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM deletion_tombstones WHERE record_key=?", (record_key,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def upsert_tombstone_sync(c: sqlite3.Connection, *, record_key: str, record_id: str, reason: str, order_key: str | None, resurrectable: bool,
                              affected_ref_ids: list[str], purged: bool, shard_id: str = "s0") -> None:
        now = now_iso()
        expires = iso((parse_iso(now)) + timedelta(days=TOMBSTONE_RETENTION_DAYS))  # type: ignore[operator]
        c.execute("""INSERT INTO deletion_tombstones(record_key, record_id, shard_id, reason, order_key, resurrectable, requested_at, purged_at,
                                                     affected_ref_ids, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(record_key) DO UPDATE SET reason=excluded.reason, order_key=excluded.order_key, resurrectable=excluded.resurrectable,
                     purged_at=COALESCE(excluded.purged_at, deletion_tombstones.purged_at), affected_ref_ids=excluded.affected_ref_ids""",
                  (record_key, record_id, shard_id, reason, order_key, 1 if resurrectable else 0, now, now if purged else None, j(affected_ref_ids), expires))

    @staticmethod
    def drop_tombstone_sync(c: sqlite3.Connection, record_key: str) -> None:
        c.execute("DELETE FROM deletion_tombstones WHERE record_key=? AND resurrectable=1", (record_key,))

    # ------------------------------------------------------------------ outbox (publish stage)
    @staticmethod
    def add_outbox_sync(c: sqlite3.Connection, msg_id: str, kind: str, payload: Mapping[str, Any]) -> None:
        c.execute("INSERT OR IGNORE INTO ingest_outbox(msg_id, kind, payload, created_at) VALUES (?, ?, ?, ?)", (msg_id, kind, j(dict(payload)), now_precise()))

    def pending_outbox(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM ingest_outbox WHERE sent_at IS NULL ORDER BY created_at, msg_id LIMIT ?", (int(limit),)).fetchall()
        return [dict(r, payload=jl(r["payload"], {})) for r in rows]

    @staticmethod
    def mark_sent_sync(c: sqlite3.Connection, msg_id: str) -> None:
        now = now_iso()
        c.execute("UPDATE ingest_outbox SET sent_at=? WHERE msg_id=?", (now, msg_id))

    # ------------------------------------------------------------------ batch accumulator for ingest_result
    @staticmethod
    def note_batch_sync(c: sqlite3.Connection, *, source_app: str, domains: Iterable[str]) -> None:
        r = c.execute("SELECT value FROM holder_meta WHERE key='ingest_batch_pending'").fetchone()
        b = jl(r["value"], {}) if r else {}
        b["records"] = int(b.get("records", 0)) + 1
        b.setdefault("by_app", {})[source_app] = int(b.get("by_app", {}).get(source_app, 0)) + 1
        for d in domains:
            b.setdefault("by_domain", {})[d] = int(b.get("by_domain", {}).get(d, 0)) + 1
        c.execute("INSERT INTO holder_meta(key, value) VALUES ('ingest_batch_pending', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (j(b),))

    # ------------------------------------------------------------------ metrics
    @staticmethod
    def add_metrics_sync(c: sqlite3.Connection, rows: Iterable[tuple[str, str, str, int, float, float]]) -> None:
        now = now_iso()
        for connector_id, stage, outcome, count, total_ms, max_ms in rows:
            c.execute("""INSERT INTO ingest_stage_metrics(connector_id, stage, outcome, count, total_ms, max_ms, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(connector_id, stage, outcome) DO UPDATE SET count=count+excluded.count, total_ms=total_ms+excluded.total_ms,
                         max_ms=MAX(max_ms, excluded.max_ms), updated_at=excluded.updated_at""",
                      (connector_id, stage, outcome, int(count), float(total_ms), float(max_ms), now))

    def metrics(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM ingest_stage_metrics ORDER BY connector_id, stage, outcome").fetchall()]

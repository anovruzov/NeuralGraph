"""Owner operations on one holder's ingestion (docs/mycelic/INGESTION.md §9.2, §10.1, §10.2, §6.5, §9.6).

* **Who connects what.** Only the holder owner connects a source. Org-wide connectors feed *unit* holders; personal
  connectors feed *user* holders (product decision 2). The pairing is enforced here, whatever the caller.
* **Nothing is ingested silently.** Discovered sources start ``pending_review`` unless an owner auto-include rule matches;
  direct messages are excluded by default even under auto-include. Private sources are not exportable until the owner opts
  them in (:meth:`IngestService.set_source`).
* **Credentials** are sealed with the :class:`~mycelic.ingest.crypto.TokenVault` before they touch the database; disconnecting
  deletes the row, which shreds the per-connector key.
* **Owner deletions** (a record, a source, a whole connector) run through the same deletion path as provider deletions, at
  priority ``delete``, synchronously.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import json
from typing import Any, Iterable

from ..util import jl, new_id, now_iso
from .contract import ConnectorError, Credentials, WebhookNotice, get_logger
from .crypto import VaultUnavailable
from .domains import add_personal_domain_sync, correct_domains_sync, personal_domain_id
from .events import VISIBILITIES, CanonicalEvent
from .pipeline import IngestPipeline, SyncReport
from .registry import ManifestError
from .store import IngestStore, SourceRow

logger = get_logger(__name__)

DM_SOURCE_TYPES = ("dm", "mpim", "group_dm")
NOTICE_AUTH_STATUS = {"auth_expired": "auth_expired", "auth_revoked": "revoked", "insufficient_scope": "paused"}
EXCLUSION_SCOPES = ("source_type", "source", "conversation", "author", "label", "title_regex", "body_regex")


class IngestService:
    def __init__(self, pipeline: IngestPipeline) -> None:
        self.p = pipeline
        self.db = pipeline.db

    # ------------------------------------------------------------------ helpers
    def _require_owner(self, actor: str) -> None:
        owners = self.p.evidence.owner_ids
        if owners and actor not in owners:
            raise PermissionError("only the holder owner can manage this holder's connectors")

    def _connector(self, connector_id: str) -> dict[str, Any]:
        con = self.db.get_connector(connector_id)
        if con is None:
            raise KeyError(connector_id)
        return con

    # ------------------------------------------------------------------ connect
    async def add_connector(self, connector_type: str, *, created_by: str, config: dict[str, Any] | None = None, ownership: str | None = None,
                            credentials: Credentials | None = None, display_name: str | None = None) -> dict[str, Any]:
        self._require_owner(created_by)
        cls = self.p.registry.get(connector_type)
        manifest = cls.manifest
        if self.p.enabled_types is not None and connector_type not in self.p.enabled_types:
            raise PermissionError(f"connector type {connector_type!r} is not enabled for this tenant")
        ownership = ownership or ("org" if self.p.holder_kind == "unit" else "personal")
        if (ownership == "org") != (self.p.holder_kind == "unit"):
            raise ValueError("org-wide connectors feed unit holders and personal connectors feed user holders")
        if ownership not in manifest.ownership:
            raise ValueError(f"connector type {connector_type!r} does not support {ownership} connections")
        if credentials is not None and credentials.kind != "none" and self.p.vault is None:
            raise VaultUnavailable("connector credentials need a configured vault (MYCELIC_SECRET_KEY)")
        cfg = dict(config or {})
        instance = self.p.registry.create(connector_type)          # refuses scaffold connectors
        connector_id = new_id("con")
        source_app = str(cfg.get("source_app") or manifest.connector_type)
        pending = f"pending:{connector_id}"
        row = {"connector_id": connector_id, "connector_type": connector_type, "source_app": source_app, "manifest_version": manifest.version,
               "display_name": display_name or manifest.display_name, "source_account_id": pending, "auth_account_id": pending,
               "auth_kind": credentials.kind if credentials else "none", "ownership": ownership, "status": "pending", "config": cfg,
               "created_by": created_by, "mode": "+".join(sorted(manifest.modes))}

        def insert(c):
            self.db.insert_connector_sync(c, row)
            if credentials is not None and credentials.kind != "none":
                sealed = self.p.vault.seal(tenant_id=self.p.tenant_id, holder_id=self.p.holder_id, connector_id=connector_id, credentials=credentials)
                IngestStore.put_credentials_sync(c, connector_id, sealed, expires_at=credentials.expires_at)
        await self.p.store.run_in_tx(insert)
        try:
            result = await instance.connect(self.p.context(self._connector(connector_id)))
        except Exception:
            await self.p.store.run_in_tx(lambda c: (IngestStore.delete_credentials_sync(c, connector_id),
                                                    c.execute("DELETE FROM connectors WHERE connector_id=?", (connector_id,))))
            raise

        def activate(c):
            dup = c.execute("SELECT connector_id FROM connectors WHERE connector_type=? AND source_app=? AND source_account_id=? AND auth_account_id=? "
                            "AND connector_id<>? AND status<>'disconnected'",
                            (connector_type, source_app, result.source_account_id, result.auth_account_id, connector_id)).fetchone()
            if dup is not None:
                raise ValueError("this account is already connected to this holder")
            # a disconnected connection of the same account keeps its rows (sources, checkpoints, records refer to it) but
            # gives up its identity slot; records keep their record_key, so the reconnect re-reads without duplicates
            c.execute("UPDATE connectors SET auth_account_id = auth_account_id || '#retired:' || connector_id "
                      "WHERE connector_type=? AND source_app=? AND source_account_id=? AND auth_account_id=? AND status='disconnected'",
                      (connector_type, source_app, result.source_account_id, result.auth_account_id))
            c.execute("""UPDATE connectors SET source_account_id=?, auth_account_id=?, account_label=?, granted_scopes=json(?), status='active', updated_at=?
                         WHERE connector_id=?""", (result.source_account_id, result.auth_account_id, result.account_label,
                                                   _json_list(result.granted_scopes), now_iso(), connector_id))
        try:
            await self.p.store.run_in_tx(activate)
        except Exception:
            await self.p.store.run_in_tx(lambda c: (IngestStore.delete_credentials_sync(c, connector_id),
                                                    c.execute("DELETE FROM connectors WHERE connector_id=?", (connector_id,))))
            raise
        con = self._connector(connector_id)
        con["warnings"] = list(result.warnings)
        return con

    # ------------------------------------------------------------------ sources
    async def discover_sources(self, connector_id: str) -> dict[str, int]:
        con = self._connector(connector_id)
        instance = self.p.connector(connector_id)
        ctx = self.p.context(con)
        auto = con["config"].get("auto_include")
        counts = {"discovered": 0, "pending_review": 0, "included": 0, "excluded": 0, "new": 0}
        async for desc in instance.discover_sources(ctx):
            if desc.visibility not in VISIBILITIES:
                desc = dataclasses.replace(desc, visibility="private")
            if desc.source_type in DM_SOURCE_TYPES:
                selection, reason = "excluded", "default_dm_excluded"
            elif auto is True or (isinstance(auto, list) and any(fnmatch.fnmatch(desc.external_id, p) or fnmatch.fnmatch(desc.name, p) for p in auto)):
                selection, reason = "included", "auto_rule"
            else:
                selection, reason = "pending_review", "discovered"

            def upsert(c, desc=desc, selection=selection, reason=reason):
                sid, created = self.db.upsert_source_sync(c, connector_id, desc, selection=selection, reason=reason,
                                                          exportable=desc.visibility != "private")
                if desc.membership_ref and desc.member_ids:
                    IngestStore.set_membership_sync(c, desc.membership_ref, desc.member_ids)
                return created
            created = await self.p.store.run_in_tx(upsert)
            counts["discovered"] += 1
            counts["new"] += 1 if created else 0
            current = self.db.source_by_external(connector_id, desc.external_id, desc.source_type)
            if current is not None:
                counts[current.selection] = counts.get(current.selection, 0) + 1
        return counts

    def sources(self, connector_id: str, *, selection: str | None = None) -> list[SourceRow]:
        return self.db.list_sources(connector_id, selection=selection)

    async def set_source(self, source_id: str, *, actor: str, selection: str | None = None, exportable: bool | None = None,
                         default_domain_ids: Iterable[str] | None = None, disclosure: str | None = None, sensitivity: str | None = None,
                         allow_external_models: int | None = None, existing_records: str = "keep") -> SourceRow:
        """Owner decisions on one source. Excluding a source that already has records asks keep or delete."""
        self._require_owner(actor)
        src = self.db.get_source(source_id)
        if src is None:
            raise KeyError(source_id)
        fields: dict[str, Any] = {}
        if selection is not None:
            if selection not in ("included", "excluded", "pending_review"):
                raise ValueError("selection must be included, excluded or pending_review")
            fields.update(selection=selection, selection_reason="owner")
        if exportable is not None:
            fields["exportable"] = bool(exportable)
        if default_domain_ids is not None:
            ids = [self.p.taxonomy.resolve(d) for d in default_domain_ids]
            unknown = [d for d in ids if d not in self.p.taxonomy.domains]
            if unknown:
                raise KeyError(unknown[0])
            fields["default_domain_ids"] = ids
        if disclosure is not None:
            fields["disclosure"] = disclosure
        if sensitivity is not None:
            fields["sensitivity"] = sensitivity
        if allow_external_models is not None:
            fields["allow_external_models"] = int(allow_external_models)
        await self.p.store.run_in_tx(lambda c: IngestStore.update_source_sync(c, source_id, **fields))
        if selection == "excluded" and existing_records == "delete":
            await self._delete_records(self.db.records_of_source(source_id), reason="excluded_after_ingest")
        return self.db.get_source(source_id)  # type: ignore[return-value]

    async def add_exclusion(self, *, actor: str, scope: str, match: str, connector_id: str | None = None, reason: str = "") -> str:
        self._require_owner(actor)
        if scope not in EXCLUSION_SCOPES:
            raise ValueError(f"scope must be one of {', '.join(EXCLUSION_SCOPES)}")
        if scope.endswith("_regex") and len(match) > 256:
            raise ValueError("exclusion patterns are limited to 256 characters")
        return await self.p.store.run_in_tx(lambda c: IngestStore.add_exclusion_sync(c, connector_id=connector_id, scope=scope, match=match,
                                                                                    action="exclude", reason=reason, created_by=actor))

    # ------------------------------------------------------------------ domains
    async def add_personal_domain(self, slug: str, name: str, *, actor: str, keywords: Iterable[str] = (), description: str = "",
                                  parent_slug: str | None = None) -> str:
        """``personal.<holder>.<slug>``: lives only in this holder, never published, never routes across the organization."""
        self._require_owner(actor)
        await self.p.store.run_in_tx(lambda c: add_personal_domain_sync(c, self.p.taxonomy, self.p.holder_id, slug, name, keywords=list(keywords),
                                                                        description=description, parent_slug=parent_slug))
        self.p.reload_taxonomy()
        return personal_domain_id(self.p.holder_id, slug)

    async def correct_domains(self, record_id: str, *, actor: str, add: Iterable[str] = (), remove: Iterable[str] = (), primary: str | None = None,
                              reason: str = "") -> list[dict[str, Any]]:
        """A person's correction: sticky against reclassification, recorded in the append-only history, and used as a
        labelled example for the affected domains' centroids."""
        self._require_owner(actor)
        if self.db.get_record(record_id) is None:
            raise KeyError(record_id)
        add, remove = list(add), list(remove)

        def fn(c):
            before = {r["domain_id"] for r in c.execute("SELECT domain_id FROM domain_memberships WHERE record_id=? AND status='active'", (record_id,))}
            out = correct_domains_sync(c, record_id, add=add, remove=remove, primary=primary, actor_id=actor, reason=reason, taxonomy=self.p.taxonomy)
            active = self.p._domains_after_sync(c, record_id)
            self.p._recount_domains_sync(c, before | set(active), "s0")
            return out
        out = await self.p.store.run_in_tx(fn)
        self.p.classifier.centroids.invalidate([self.p.taxonomy.resolve(d) for d in add + remove])
        return out

    # ------------------------------------------------------------------ deletion
    def _deletion_event(self, rec: dict[str, Any], reason: str) -> CanonicalEvent:
        return CanonicalEvent.create(kind="deletion", tenant_id=self.p.tenant_id, holder_id=self.p.holder_id, connector_id=rec["connector_id"],
                                     source_app=rec["source_app"], source_account_id=rec["source_account_id"],
                                     source_object_type=rec["source_object_type"], source_object_id=rec["source_object_id"],
                                     observed_at=now_iso(), updated_at=now_iso(), deletion_status="purged", tiebreak=f"owner:{reason}",
                                     hints={"reason": reason, "source_id": rec.get("source_id")})

    async def _delete_records(self, records: list[dict[str, Any]], *, reason: str) -> dict[str, Any]:
        records = [r for r in records if r]
        by_connector: dict[str, list[CanonicalEvent]] = {}
        for rec in records:
            by_connector.setdefault(rec["connector_id"], []).append(self._deletion_event(rec, reason))
        for cid, events in by_connector.items():
            await self.p.queue.enqueue(cid, "owner", events, priority_class="delete")
        report = await self.p.process_available()
        affected: list[str] = []
        for rec in records:
            t = self.db.get_tombstone(rec["record_key"])
            if t:
                affected.extend(jl(t["affected_ref_ids"], []))
        return {"records": len(records), "affected_ref_ids": affected, "processed": report.processed}

    async def delete_record(self, record_id: str, *, actor: str, reason: str = "owner_deleted") -> dict[str, Any]:
        self._require_owner(actor)
        rec = self.db.get_record(record_id)
        if rec is None:
            raise KeyError(record_id)
        return await self._delete_records([rec], reason=reason)

    async def delete_source(self, source_id: str, *, actor: str) -> dict[str, Any]:
        self._require_owner(actor)
        src = self.db.get_source(source_id)
        if src is None:
            raise KeyError(source_id)
        await self.p.store.run_in_tx(lambda c: IngestStore.update_source_sync(c, source_id, selection="excluded", selection_reason="owner_deleted"))
        try:
            con = self._connector(src.connector_id)
            await self.p.connector(src.connector_id).delete_source(self.p.context(con), src.descriptor())
        except (ManifestError, KeyError):
            pass
        except Exception as exc:     # provider-side cleanup failing never keeps data the owner deleted
            logger.warning("provider-side cleanup for a source of connector %s failed: %s", src.connector_id, type(exc).__name__)
        return await self._delete_records(self.db.records_of_source(source_id), reason="owner_deleted_source")

    async def disconnect(self, connector_id: str, *, actor: str, data: str = "keep", revoke: bool = False) -> dict[str, Any]:
        """Revoke at the provider when asked, shred the credentials, stop syncing; ``data='delete'`` also purges every
        record of the connector."""
        self._require_owner(actor)
        if data not in ("keep", "delete"):
            raise ValueError("data must be keep or delete")
        con = self._connector(connector_id)
        try:
            await self.p.connector(connector_id).disconnect(self.p.context(con), revoke_at_provider=revoke)
        except Exception as exc:
            logger.warning("provider-side disconnect for connector %s failed: %s", connector_id, type(exc).__name__)

        def fn(c):
            IngestStore.delete_credentials_sync(c, connector_id)
            IngestStore.set_connector_status_sync(c, connector_id, "disconnected", "")
            c.execute("UPDATE ingest_queue SET status='discarded', payload=NULL, payload_bytes=0 WHERE connector_id=? AND status IN ('queued','dead') "
                      "AND kind NOT IN ('deletion','redaction')", (connector_id,))
        await self.p.store.run_in_tx(fn)
        self.p._instances.pop(connector_id, None)
        http = self.p._http.pop(connector_id, None)          # its session and ETag cache go with the connection
        if http is not None and hasattr(http, "close"):
            await http.close()
        out: dict[str, Any] = {"connector_id": connector_id, "status": "disconnected", "records_deleted": 0}
        if data == "delete":
            res = await self._delete_records(self.db.records_of_connector(connector_id), reason="disconnect")
            out["records_deleted"] = res["records"]
            out["affected_ref_ids"] = res["affected_ref_ids"]
        return out

    # ------------------------------------------------------------------ webhooks (notify, then fetch)
    async def handle_notice(self, connector_id: str, notice: WebhookNotice) -> SyncReport:
        """A thin, content-free notice for one connector: deduplicated on the delivery id, then the connector fetches the
        authoritative state with the owner's grant and the page goes through the same admission and queue as a pull."""
        con = self._connector(connector_id)
        report = SyncReport(connector_id)
        if con["status"] != "active":
            report.error_code = f"connector_{con['status']}"
            return report

        def claim(c):
            cur = c.execute("INSERT OR IGNORE INTO ingest_deliveries(delivery_key, connector_id, received_at) VALUES (?, ?, ?)",
                            (f"{connector_id}:{notice.delivery_id}", connector_id, now_iso()))
            return cur.rowcount == 1
        if not await self.p.store.run_in_tx(claim):
            report.duplicates += 1
            return report
        instance = self.p.connector(connector_id)
        ctx = self.p.context(con)
        if not notice.source_external_id:
            # a connection-level notice (grant revoked, app uninstalled): no source and nothing to admit; the connector
            # confirms it and raises the auth error the provider signalled
            await self._connection_notice(instance, ctx, notice, report)
        else:
            src = self.db.source_by_external(connector_id, notice.source_external_id)
            if src is None or src.selection != "included" or src.access_state != "ok":
                report.excluded += 1
                return report
            await self.p._run_stream(instance, ctx, con, src, "webhook", lambda cur: instance.handle_webhook(ctx, notice), "live", "webhook", None,
                                     report)
        status = NOTICE_AUTH_STATUS.get(report.error_code or "")
        if status:          # the same connection states a pull sync records for these errors
            await self.p.store.run_in_tx(lambda c: IngestStore.set_connector_status_sync(c, connector_id, status, report.error_code or ""))
        return report

    @staticmethod
    async def _connection_notice(instance: Any, ctx: Any, notice: WebhookNotice, report: SyncReport) -> None:
        try:
            async for page in instance.handle_webhook(ctx, notice):
                report.pages += 1
                report.excluded += len(page.items)          # without a source nothing can be admitted
        except ConnectorError as exc:
            report.error_code = exc.code
        except Exception as exc:                            # a broken connector never breaks the notice path
            report.error_code = "connector_crash"
            logger.warning("connection notice for %s failed: %s", report.connector_id, type(exc).__name__)


def _json_list(values: Iterable[str]) -> str:
    return json.dumps(list(values))

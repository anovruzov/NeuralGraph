"""``IngestRuntime``: one holder's ingestion, running next to its ``HolderService`` (docs/mycelic/INGESTION.md §2.2, §3.6,
§3.7, E9).

It owns the holder's :class:`~mycelic.ingest.pipeline.IngestPipeline` and the owner-facing
:class:`~mycelic.ingest.service.IngestService`, and does three things:

* **Scheduling.** A tick every ``tick_seconds``. Each tick requeues expired leases, polls every active connector whose
  interval is due (incremental streams first, then at most a few backfill pages while backpressure allows), processes
  the queue, and publishes the content-free batch outputs. One tick runs at a time, which keeps the single-writer rule
  per shard: within one holder, fetching and writing are serialized.
* **Control.** :meth:`control` executes the connector actions the coordinator forwards on behalf of an authorized user
  (connect, discover, choose sources, sync, pause, disconnect, delete). The coordinator has already checked the
  caller's authority; for a user-owned holder the service checks again that the actor is the owner. Replies carry
  connector metadata, counts and source names. Source names are owner-visible, and they travel only in short-lived,
  scrubbed ``connector_reply`` envelopes.
* **Webhook notices.** :meth:`on_notice` passes a thin, content-free notice to the connector, which fetches the
  authoritative state with the holder's own grant. A lost notice is repaired by the next poll; webhooks only cut
  latency.

Credentials never travel in clear. A token the owner enters in the coordinator UI reaches the holder sealed with a
key derived from the holder's route key (:func:`mycelic.ingest.crypto.open_transfer`). The holder then re-seals it
with its own vault and drops the transfer copy.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..util import now_iso
from .contract import Credentials, Secret, WebhookNotice, get_logger
from .pipeline import IngestPipeline, SyncReport
from .registry import ConnectorRegistry
from .service import IngestService

logger = get_logger(__name__)

# per tick: incremental pages per stream, and backfill pages per connector (backfill never starves live work)
INCREMENTAL_PAGES_PER_TICK = 20
BACKFILL_PAGES_PER_TICK = 5
PROCESS_ITEMS_PER_TICK = 500
MIN_POLL_SECONDS = 30.0
MEMBERSHIP_ACTIONS = frozenset({"member_left_channel", "member_joined_channel", "member_removed", "permission_changed"})

# what a connector looks like outside the holder: metadata and counts, never content or credentials
_CONNECTOR_FIELDS = ("connector_id", "connector_type", "display_name", "account_label", "source_account_id", "auth_kind", "ownership", "mode", "status",
                     "status_code", "granted_scopes", "created_at", "updated_at", "last_sync_at", "last_success_at", "health")
_SOURCE_FIELDS = ("source_id", "connector_id", "source_type", "external_id", "name", "selection", "selection_reason", "visibility",
                  "exportable", "disclosure", "default_domain_ids", "sensitivity", "access_state")


class ControlError(ValueError):
    """A control request that cannot be honoured (unknown action or connector, refused by the owner check)."""


def connector_view(con: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: con.get(k) for k in _CONNECTOR_FIELDS if k in con}
    out["config"] = {k: v for k, v in (con.get("config") or {}).items() if k in ("poll_seconds", "auto_include", "slack_app_class", "keep_versions",
                                                                                "backfill_days", "include_dms", "source_app")}
    return out


def source_view(src: Any) -> dict[str, Any]:
    d = dataclasses.asdict(src) if dataclasses.is_dataclass(src) else dict(src)
    out = {k: d.get(k) for k in _SOURCE_FIELDS}
    out["members"] = len(d.get("member_ids") or [])          # a count: member ids stay in the holder
    return out


def notice_from_payload(p: Mapping[str, Any]) -> WebhookNotice:
    return WebhookNotice(connector_type=str(p["connector_type"]), delivery_id=str(p["delivery_id"]), external_account_id=str(p.get("external_account_id") or ""),
                         source_external_id=str(p.get("source_external_id") or ""), action=str(p.get("action") or ""),
                         object_refs=tuple(dict(r) for r in (p.get("object_refs") or [])), occurred_at=p.get("occurred_at"))


def notice_to_payload(n: WebhookNotice) -> dict[str, Any]:
    return {"connector_type": n.connector_type, "delivery_id": n.delivery_id, "external_account_id": n.external_account_id,
            "source_external_id": n.source_external_id, "action": n.action, "object_refs": [dict(r) for r in n.object_refs],
            "occurred_at": n.occurred_at}


def _report(r: SyncReport) -> dict[str, Any]:
    return {k: getattr(r, k) for k in ("streams", "pages", "raw_items", "enqueued", "duplicates", "excluded", "normalize_errors", "error_code", "yielded")}


class IngestRuntime:
    """See module docstring. ``publisher`` is the holder's ``HolderService`` (or anything with ``publish_ingest_output``)."""

    def __init__(self, evidence: Any, *, holder_kind: str = "user", publisher: Any | None = None, vault: Any | None = None,
                 router: Any | None = None, registry: ConnectorRegistry | None = None, enabled_connector_types: Iterable[str] | None = None,
                 http_factory: Callable[..., Any] | None = None, tick_seconds: float = 5.0, batch_debounce_seconds: float = 30.0,
                 clock: Callable[[], datetime] | None = None, import_root: str | Path | None = None) -> None:
        if registry is None:
            # the process-wide registry, shared with the API (webhook verification, catalog): one set of connector classes
            from .connectors import register_builtin
            registry = register_builtin()
        self.pipeline = IngestPipeline(evidence, holder_kind=holder_kind, registry=registry, vault=vault, router=router, publisher=publisher,
                                       enabled_connector_types=enabled_connector_types, batch_debounce_seconds=batch_debounce_seconds, clock=clock,
                                       http_factory=http_factory)
        self.service = IngestService(self.pipeline)
        self.holder_id = evidence.holder_id
        # file-based connectors driven from the coordinator may only read inside this directory (the holder's imports)
        self.import_root = Path(import_root).expanduser().resolve() if import_root else None
        self.tick_seconds = float(tick_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._last_poll: dict[str, float] = {}
        self._tick_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.running = False
        self.counters = {"ticks": 0, "polls": 0, "notices": 0, "controls": 0, "errors": 0}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop(), name=f"ingest-{self.holder_id}")

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        with contextlib.suppress(Exception):
            await self.pipeline.aclose()            # the connections' HTTP clients

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.counters["errors"] += 1
                logger.exception("ingest tick failed for holder %s", self.holder_id)
            await asyncio.sleep(self.tick_seconds)

    # ------------------------------------------------------------------ scheduling
    def _poll_interval(self, con: Mapping[str, Any]) -> float:
        cfg = con.get("config") or {}
        try:
            manifest = self.pipeline.registry.get(con["connector_type"]).manifest
            default = float(manifest.default_poll_seconds)
        except Exception:
            default = 300.0
        return max(MIN_POLL_SECONDS, float(cfg.get("poll_seconds") or default))

    def _due(self, con: Mapping[str, Any], now: float) -> bool:
        last = self._last_poll.get(con["connector_id"])
        return last is None or now - last >= self._poll_interval(con)

    async def tick(self, *, force: bool = False) -> dict[str, Any]:
        """One scheduler pass. ``force`` polls every active connector regardless of its interval (tests, "sync now")."""
        async with self._tick_lock:
            self.counters["ticks"] += 1
            out: dict[str, Any] = {"polled": [], "processed": 0, "published": 0}
            now = time.monotonic()
            for con in self.pipeline.db.list_connectors():
                if con["status"] != "active" or not (force or self._due(con, now)):
                    continue
                self._last_poll[con["connector_id"]] = now
                self.counters["polls"] += 1
                try:
                    inc = await self.pipeline.sync(con["connector_id"], mode="incremental", max_pages=INCREMENTAL_PAGES_PER_TICK)
                    back = None
                    if self.pipeline.queue.fetch_allowed("backfill"):
                        back = await self.pipeline.sync(con["connector_id"], mode="backfill", max_pages=BACKFILL_PAGES_PER_TICK)
                    out["polled"].append({"connector_id": con["connector_id"], "incremental": _report(inc), "backfill": _report(back) if back else None})
                except Exception as exc:
                    self.counters["errors"] += 1
                    logger.warning("poll of connector %s failed: %s", con["connector_id"], type(exc).__name__)
            if not out["polled"] and self._idle():
                return out
            rep = await self.pipeline.process_available(max_items=PROCESS_ITEMS_PER_TICK)
            out["processed"], out["published"] = rep.processed, rep.published
            return out

    def _idle(self) -> bool:
        """Nothing queued, leased, unpublished or waiting in a batch: a holder without connectors costs three reads a tick."""
        c = self.pipeline.store._conn
        if c.execute("SELECT 1 FROM ingest_queue WHERE status IN ('queued','leased') LIMIT 1").fetchone():
            return False
        if c.execute("SELECT 1 FROM ingest_outbox WHERE sent_at IS NULL LIMIT 1").fetchone():
            return False
        r = c.execute("SELECT value FROM holder_meta WHERE key='ingest_batch_pending'").fetchone()
        return r is None or r["value"] in ("{}", "", None)

    async def drain(self, *, rounds: int = 20) -> dict[str, Any]:
        """Poll everything now and process until the queue is empty (tests and the demonstration)."""
        total = {"processed": 0, "published": 0, "ticks": 0}
        for i in range(rounds):
            r = await self.tick(force=i == 0)
            total["ticks"] += 1
            total["processed"] += r["processed"]
            total["published"] += r["published"]
            if not r["processed"] and i > 0:
                break
        await self.pipeline.flush_batch(force=True)
        total["published"] += await self.pipeline.publish_pending()
        return total

    # ------------------------------------------------------------------ webhook notices
    async def on_notice(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """``connector_notice`` envelopes: ``{connector_id, notice}``. Unknown or inactive connectors are ignored (the
        coordinator's routes may be a moment behind a disconnect)."""
        self.counters["notices"] += 1
        cid = str(payload.get("connector_id") or "")
        con = self.pipeline.db.get_connector(cid)
        if con is None:
            return {"connector_id": cid, "ignored": "unknown connector"}
        notice = notice_from_payload(payload.get("notice") or {})
        if notice.connector_type != con["connector_type"]:
            return {"connector_id": cid, "ignored": "connector type mismatch"}
        async with self._tick_lock:
            report = await self.service.handle_notice(cid, notice)
            if notice.action in MEMBERSHIP_ACTIONS and not report.duplicates:
                # someone left (or joined) a channel: refresh the source's member list now, so records narrow at their next use
                with contextlib.suppress(Exception):
                    await self.service.discover_sources(cid)
            rep = await self.pipeline.process_available(max_items=PROCESS_ITEMS_PER_TICK)
        return {"connector_id": cid, "report": _report(report), "processed": rep.processed}

    # ------------------------------------------------------------------ control
    def _connector_or_raise(self, connector_id: str) -> dict[str, Any]:
        con = self.pipeline.db.get_connector(str(connector_id or ""))
        if con is None:
            raise ControlError("unknown connector")
        return con

    def describe(self, connector_id: str | None = None) -> dict[str, Any]:
        cons = [c for c in self.pipeline.db.list_connectors() if connector_id is None or c["connector_id"] == connector_id]
        items = []
        for con in cons:
            v = connector_view(con)
            srcs = self.pipeline.db.list_sources(con["connector_id"])
            v["counts"] = {"sources_included": sum(1 for s in srcs if s.selection == "included"),
                           "sources_pending": sum(1 for s in srcs if s.selection == "pending_review"),
                           "sources_excluded": sum(1 for s in srcs if s.selection == "excluded"),
                           "records": len(self.pipeline.db.records_of_connector(con["connector_id"]))}
            items.append(v)
        return {"items": items}

    def _check_paths(self, config: Mapping[str, Any]) -> None:
        """A control request comes from the coordinator, not from the owner's own machine: any file path it names must
        resolve inside the holder's import directory, so no request can make a holder read other files on its host."""
        paths = config.get("paths")
        if paths is None:
            return
        if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
            raise ControlError("config.paths must be a list of file names")
        if self.import_root is None:
            raise ControlError("this holder accepts no file paths from the coordinator")
        for raw in paths:
            target = (self.import_root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
            if target != self.import_root and self.import_root not in target.parents:
                raise ControlError("config.paths must stay inside the holder's import directory")

    # ------------------------------------------------------------------ records and their domains ("why this domain")
    def _audience(self, actor: str) -> Any:
        from .acl import Audience
        owners = set(self.pipeline.evidence.owner_ids or ())
        return Audience(principal_ids=frozenset([actor]) if actor else frozenset(), complete=bool(actor), owner=bool(actor) and actor in owners)

    def _record_visible(self, actor: str, record_id: str) -> bool:
        ev = self.pipeline.evidence
        acl = ev._record_acl_sync(ev.store._conn, record_id)
        return acl is not None and ev._acl_allows(acl, self._audience(actor))[0]

    def _memberships(self, record_id: str, *, full: bool = False) -> list[dict[str, Any]]:
        import json as _json
        from .domains import is_personal
        c = self.pipeline.store._conn
        cols = "domain_id, confidence, method, is_primary, model_version, taxonomy_version, evidence, corrected_by, updated_at" if full else \
            "domain_id, confidence, method, is_primary"
        out = []
        for r in c.execute(f"SELECT {cols} FROM domain_memberships WHERE record_id=? AND status='active' ORDER BY is_primary DESC, confidence DESC", (record_id,)):
            d = dict(r)
            d["is_primary"] = bool(d["is_primary"])
            d["personal"] = is_personal(d["domain_id"])
            d["path"] = self.pipeline.taxonomy.path(d["domain_id"]) if d["domain_id"] in self.pipeline.taxonomy.domains else d["domain_id"]
            if full:
                d["evidence"] = _json.loads(d["evidence"] or "{}")
            out.append(d)
        return out

    def _records(self, actor: str, p: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The holder's live records the actor may read (the owner: all), newest first, with their domains. Titles are shown
        to readers the source ACL admits, and only to them."""
        c = self.pipeline.store._conn
        limit = max(1, min(200, int(p.get("limit") or 50)))
        sql = ("SELECT r.record_id, r.kind, r.source_app, r.source_object_type, r.created_at_src, r.visibility, d.title, substr(d.text, 1, 160) AS snippet FROM ingest_records r "
               "LEFT JOIN documents d ON d.doc_id = r.record_id WHERE r.deletion_status='live' AND r.kind <> 'conversation'")
        args: list[Any] = []
        if p.get("domain_id"):
            sql += " AND r.record_id IN (SELECT record_id FROM domain_memberships WHERE domain_id=? AND status='active')"
            args.append(str(p["domain_id"]))
        if p.get("source_app"):
            sql += " AND r.source_app=?"
            args.append(str(p["source_app"]))
        sql += " ORDER BY COALESCE(r.created_at_src, '') DESC, r.record_id LIMIT ?"
        args.append(limit * 4)
        out = []
        for r in c.execute(sql, args).fetchall():
            if not self._record_visible(actor, r["record_id"]):
                continue
            out.append({"record_id": r["record_id"], "kind": r["kind"], "source_app": r["source_app"], "object_type": r["source_object_type"],
                        "created_at": r["created_at_src"], "visibility": r["visibility"], "title": r["title"] or "", "snippet": r["snippet"] or "",
                        "domains": self._memberships(r["record_id"])})
            if len(out) >= limit:
                break
        return out

    def _record_detail(self, actor: str, record_id: str) -> dict[str, Any]:
        """Why a record sits in its domains: each membership's method, confidence and evidence (rule ids, similarity, a
        model's rationale; never quotes), the append-only history, and the typed edges it supports."""
        import json as _json
        if not record_id or not self._record_visible(actor, record_id):
            raise ControlError("unknown record")
        c = self.pipeline.store._conn
        r = c.execute("SELECT r.record_id, r.kind, r.source_app, r.source_object_type, r.created_at_src, r.visibility, r.source_root_id, r.root_method, "
                      "d.title, substr(d.text, 1, 400) AS snippet FROM ingest_records r LEFT JOIN documents d ON d.doc_id = r.record_id WHERE r.record_id=?",
                      (record_id,)).fetchone()
        history = [{"domain_id": h["domain_id"], "action": h["action"], "method": h["method"], "actor_type": h["actor_type"], "reason": h["reason"], "at": h["at"],
                    "before": _json.loads(h["before"] or "{}"), "after": _json.loads(h["after"] or "{}")}
                   for h in c.execute("SELECT * FROM domain_membership_history WHERE record_id=? ORDER BY id DESC LIMIT 50", (record_id,))]
        edges = [{"subject": e["subject_id"], "predicate": e["predicate"], "object": e["object_id"], "modality": e["modality"], "confidence": e["confidence"],
                  "status": e["status"]}
                 for e in c.execute("SELECT rel.subject_id, rel.predicate, rel.object_id, rel.status, ev.modality, ev.confidence FROM relation_evidence ev "
                                    "JOIN relations rel ON rel.relation_id = ev.relation_id WHERE ev.record_id=? ORDER BY rel.predicate", (record_id,))]
        entities = [x["entity_id"] for x in c.execute("SELECT entity_id FROM record_entities WHERE record_id=? AND role IN ('mention','reference') ORDER BY entity_id",
                                                     (record_id,))]
        return {"record": {"record_id": r["record_id"], "kind": r["kind"], "source_app": r["source_app"], "object_type": r["source_object_type"],
                           "created_at": r["created_at_src"], "visibility": r["visibility"], "title": r["title"] or "", "snippet": r["snippet"] or "",
                           "source_root_id": r["source_root_id"],
                           "root_method": r["root_method"]},
                "domains": self._memberships(record_id, full=True), "history": history, "edges": edges, "entities": entities}

    async def control(self, action: str, p: Mapping[str, Any], *, actor: str, credentials: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """One connector action. ``credentials`` (already decrypted from the transfer envelope) is used once, re-sealed by
        the holder's vault inside :meth:`IngestService.add_connector`, and never returned."""
        self.counters["controls"] += 1
        svc = self.service
        try:
            if action == "connectors.list":
                return self.describe()
            if action == "connector.get":
                con = self._connector_or_raise(p.get("connector_id"))
                out = self.describe(con["connector_id"])["items"][0]
                return {"connector": out}
            if action == "connector.add":
                self._check_paths(p.get("config") or {})
                creds = None
                if credentials:
                    creds = Credentials(kind=str(credentials.get("kind") or "pat"),
                                        access_token=Secret(str(credentials["access_token"])) if credentials.get("access_token") else None,
                                        refresh_token=Secret(str(credentials["refresh_token"])) if credentials.get("refresh_token") else None,
                                        expires_at=credentials.get("expires_at"), extra=dict(credentials.get("extra") or {}))
                cfg = dict(p.get("config") or {})
                if cfg.get("paths") is not None and self.import_root is not None:
                    cfg["paths"] = [str((self.import_root / x).resolve()) for x in cfg["paths"]]
                con = await svc.add_connector(str(p["connector_type"]), created_by=actor, config=cfg,
                                              credentials=creds, display_name=p.get("display_name"))
                counts = await svc.discover_sources(con["connector_id"]) if p.get("discover", True) else {}
                return {"connector": {**connector_view(con), "warnings": list(con.get("warnings") or [])}, "discovered": counts}
            if action == "sources.discover":
                con = self._connector_or_raise(p.get("connector_id"))
                return {"discovered": await svc.discover_sources(con["connector_id"])}
            if action == "sources.list":
                con = self._connector_or_raise(p.get("connector_id"))
                return {"items": [source_view(s) for s in svc.sources(con["connector_id"], selection=p.get("selection"))]}
            if action == "sources.update":
                con = self._connector_or_raise(p.get("connector_id"))
                known = {s.source_id for s in svc.sources(con["connector_id"])}
                out = []
                for ch in p.get("changes") or []:
                    if ch.get("source_id") not in known:
                        raise ControlError("unknown source for this connector")
                    src = await svc.set_source(ch["source_id"], actor=actor, selection=ch.get("selection"), exportable=ch.get("exportable"),
                                               default_domain_ids=ch.get("default_domain_ids"), disclosure=ch.get("disclosure"),
                                               sensitivity=ch.get("sensitivity"), existing_records=str(p.get("existing_records") or "keep"))
                    out.append(source_view(src))
                return {"items": out}
            if action == "connector.sync":
                con = self._connector_or_raise(p.get("connector_id"))
                mode = str(p.get("mode") or "incremental")
                async with self._tick_lock:
                    report = await self.pipeline.sync(con["connector_id"], mode=mode, max_pages=p.get("max_pages"))
                    rep = await self.pipeline.process_available(max_items=PROCESS_ITEMS_PER_TICK)
                self._last_poll[con["connector_id"]] = time.monotonic()
                return {"report": _report(report), "processed": rep.processed, "published": rep.published}
            if action == "connector.update":
                con = self._connector_or_raise(p.get("connector_id"))
                svc._require_owner(actor)
                status = p.get("status")
                if status not in (None, "active", "paused"):
                    raise ControlError("status must be active or paused")
                cfg = dict(con.get("config") or {})
                for k, v in (p.get("config") or {}).items():
                    if k in ("poll_seconds", "auto_include", "keep_versions", "backfill_days"):
                        cfg[k] = v

                def fn(c):
                    if status is not None and con["status"] in ("active", "paused"):
                        c.execute("UPDATE connectors SET status=? WHERE connector_id=?", (status, con["connector_id"]))
                    c.execute("UPDATE connectors SET config=?, updated_at=? WHERE connector_id=?", (_dumps(cfg), now_iso(), con["connector_id"]))
                await self.pipeline.store.run_in_tx(fn)
                return {"connector": connector_view(self._connector_or_raise(con["connector_id"]))}
            if action == "connector.disconnect":
                con = self._connector_or_raise(p.get("connector_id"))
                return await svc.disconnect(con["connector_id"], actor=actor, data=str(p.get("data") or "keep"), revoke=bool(p.get("revoke")))
            if action == "source.delete":
                con = self._connector_or_raise(p.get("connector_id"))
                if p.get("source_id") not in {s.source_id for s in svc.sources(con["connector_id"])}:
                    raise ControlError("unknown source for this connector")
                return await svc.delete_source(str(p["source_id"]), actor=actor)
            if action == "records.list":
                return {"items": self._records(actor, p)}
            if action == "record.get":
                return self._record_detail(actor, str(p.get("record_id") or ""))
            if action == "record.domains":
                rid = str(p.get("record_id") or "")
                if not self._record_visible(actor, rid):
                    raise ControlError("unknown record")
                await svc.correct_domains(rid, actor=actor, add=list(p.get("add") or []), remove=list(p.get("remove") or []),
                                          primary=p.get("primary"), reason=str(p.get("reason") or "")[:300])
                return self._record_detail(actor, rid)
            if action == "queue.status":
                return {"classes": self.pipeline.queue.counts_sync(), "dead": self.pipeline.queue.dead_letters_sync(limit=int(p.get("limit") or 50))}
        except PermissionError as exc:
            raise ControlError(str(exc)) from None
        except KeyError as exc:
            raise ControlError(f"unknown {exc.args[0] if exc.args else 'item'}") from None
        raise ControlError(f"unknown connector action {action!r}")


def _dumps(v: Any) -> str:
    import json
    return json.dumps(v, sort_keys=True)

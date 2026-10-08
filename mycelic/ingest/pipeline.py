"""The ingestion pipeline of one holder (docs/mycelic/INGESTION.md §9).

Fetch side (:meth:`IngestPipeline.sync`), per page of a connector stream::

    fetch -> admit (source authorization) -> normalize -> admit (exclusions, tombstones, quotas) -> redact -> enqueue+checkpoint

Process side (:meth:`IngestPipeline.process_available`), per leased queue item::

    dedupe -> classify -> route (s0) -> write (EvidenceStore primitives; doc_id = record_id, observed_at = last content change)
           -> publish (outbox written in the write transaction, sent afterwards)

Every stage is metered separately (:class:`StageMeter`, persisted in ``ingest_stage_metrics``: counts and latency, never
content). Isolation properties:

* **A connector failure never corrupts the holder's graph.** Connector code only produces raw items and events; nothing is
  written until a page commits atomically with its cursor, an exception inside a page leaves the previous checkpoint in
  force, and a malformed item is counted and skipped (its content is not kept). Each record's effect — document, chunks,
  memories, catalog rows, memberships, locator, idempotency marker, queue ack and outbox envelope — is ONE transaction.
* **Replays are no-ops.** ``event_key`` is unique in the queue and recorded in ``applied_events`` with the effect.
* **Deletions stick.** A tombstone stops later events (late webhooks, re-imports) from resurrecting a record.
* **Content is data.** Nothing here reads instructions from content; flagged records never reach a model.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Iterable, Mapping, Protocol

from ..evidence.service import HashEmbedder, chunk_text
from ..util import j, jl, new_id, now_iso, parse_iso, sha256
from .acl import narrow
from .contract import (AuthExpired, AuthRevoked, ConnectorContext, ConnectorError, ConnectorLimits, Credentials, CursorInvalid,
                       InsufficientScope, NoHttp, PermanentError, RateLimited, SourceUnavailable, connector_logger, get_logger)
from .crypto import TokenVault, VaultUnavailable
from .domains import (DomainClassifier, Membership, RecordFeatures, Taxonomy, active_memberships_sync, apply_memberships_sync,
                      default_taxonomy, embed_model_name, install_taxonomy_sync, load_taxonomy_sync, publishable)
from .events import CONTENT_KINDS, CanonicalEvent
from .normalize import FLAG_SECRET, detect, mask_secrets
from .queue import IngestQueue, LostLease, QueueItem, StaleCheckpoint
from .registry import ConnectorRegistry, registry as default_registry
from .shards import DEFAULT_SHARD, ShardRouter
from .store import IngestStore, SourceRow

logger = get_logger(__name__)

STAGES = ("fetch", "admit", "normalize", "redact", "enqueue", "dedupe", "classify", "route", "write", "publish")
THROTTLED_CLASSES = ("backfill", "reindex", "maintenance")
SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_STOP_STREAM_CODES = ("auth_expired", "auth_revoked", "insufficient_scope", "quota_exceeded")


class Publisher(Protocol):
    """Where envelopes go: ``HolderService.publish_ingest_output`` (signed, on the tenant subjects) in a holder process."""
    async def publish_ingest_output(self, kind: str, payload: dict[str, Any], *, msg_id: str) -> bool: ...


# ---------------------------------------------------------------------------------------------- metering
class StageMeter:
    """Counts and latency per (connector, stage, outcome). In memory, flushed to ``ingest_stage_metrics``."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], list[float]] = {}
        self.totals: Counter[tuple[str, str]] = Counter()

    def record(self, connector_id: str, stage: str, outcome: str, ms: float = 0.0, *, count: int = 1) -> None:
        if count <= 0:
            return
        row = self._rows.setdefault((connector_id or "", stage, outcome), [0, 0.0, 0.0])
        row[0] += count
        row[1] += ms
        row[2] = max(row[2], ms)
        self.totals[(stage, outcome)] += count

    @contextlib.contextmanager
    def time(self, connector_id: str, stage: str, outcome: str = "ok"):
        box = {"outcome": outcome, "count": 1}
        t0 = time.perf_counter()
        try:
            yield box
        except BaseException:
            self.record(connector_id, stage, "error", (time.perf_counter() - t0) * 1000)
            raise
        self.record(connector_id, stage, box["outcome"], (time.perf_counter() - t0) * 1000, count=box["count"])

    def drain(self) -> list[tuple[str, str, str, int, float, float]]:
        rows = [(cid, st, oc, int(v[0]), v[1], v[2]) for (cid, st, oc), v in self._rows.items()]
        self._rows.clear()
        return rows


@dataclass
class SyncReport:
    connector_id: str
    streams: list[str] = field(default_factory=list)
    pages: int = 0
    raw_items: int = 0
    enqueued: int = 0
    duplicates: int = 0
    excluded: int = 0
    normalize_errors: int = 0
    error_code: str | None = None
    yielded: bool = False          # stopped early by backpressure; resumes from the committed cursor
    fenced: bool = False           # a stale runner's page was rejected by checkpoint fencing


@dataclass
class ProcessReport:
    processed: int = 0
    outcomes: Counter = field(default_factory=Counter)
    published: int = 0

    def add(self, outcome: str) -> None:
        self.processed += 1
        self.outcomes[outcome] += 1


# ---------------------------------------------------------------------------------------------- context services
class VaultSecrets:
    """``SecretAccessor`` over ``connector_credentials``: decrypts on demand, never caches on the context."""

    def __init__(self, pipeline: "IngestPipeline", connector_id: str) -> None:
        self.pipeline = pipeline
        self.connector_id = connector_id

    async def get(self) -> Credentials:
        sealed = self.pipeline.db.get_credentials(self.connector_id)
        if sealed is None:
            return Credentials(kind="none")
        if self.pipeline.vault is None:
            raise VaultUnavailable("no vault configured for connector credentials")
        return self.pipeline.vault.open(sealed, tenant_id=self.pipeline.tenant_id, holder_id=self.pipeline.holder_id, connector_id=self.connector_id)

    async def replace(self, creds: Credentials) -> None:
        sealed = self.pipeline.db.get_credentials(self.connector_id)
        vault = self.pipeline.vault
        if vault is None:
            raise VaultUnavailable("no vault configured for connector credentials")
        ids = {"tenant_id": self.pipeline.tenant_id, "holder_id": self.pipeline.holder_id, "connector_id": self.connector_id}
        new = vault.replace_credentials(sealed, credentials=creds, **ids) if sealed else vault.seal(credentials=creds, **ids)
        await self.pipeline.store.run_in_tx(lambda c: IngestStore.put_credentials_sync(c, self.connector_id, new, expires_at=creds.expires_at))

    def refresh_lock(self) -> asyncio.Lock:
        return self.pipeline._refresh_locks.setdefault(self.connector_id, asyncio.Lock())


class StoreCheckpoints:
    def __init__(self, queue: IngestQueue, connector_id: str) -> None:
        self.queue = queue
        self.connector_id = connector_id

    async def load(self, stream: str):
        return (await self.queue.load_checkpoint(self.connector_id, stream))[0]


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def person_entity_id(app: str, author_id: str | None) -> str | None:
    """Cross-app identity: an email address is the same person in every app; other ids are namespaced by app."""
    if not author_id:
        return None
    a = str(author_id).strip().lower()
    return f"person:{a}" if "@" in a else f"person:{app}:{a}"


# ---------------------------------------------------------------------------------------------- the pipeline
class IngestPipeline:
    """One holder's ingestion. ``evidence`` is the holder's :class:`~mycelic.evidence.service.EvidenceStore` (shard s0)."""

    def __init__(self, evidence: Any, *, holder_kind: str = "user", registry: ConnectorRegistry | None = None, vault: TokenVault | None = None,
                 router: Any | None = None, publisher: Publisher | None = None, enabled_connector_types: Iterable[str] | None = None,
                 max_records: int | None = None, batch_debounce_seconds: float = 30.0, worker_id: str | None = None,
                 lease_seconds: float = 120.0, watermarks: dict[str, tuple[int, int]] | None = None,
                 clock: Callable[[], datetime] | None = None, taxonomy: Taxonomy | None = None) -> None:
        if holder_kind not in ("user", "unit"):
            raise ValueError("holder_kind must be user or unit")
        self.evidence = evidence
        self.store = evidence.store
        self.tenant_id = evidence.tenant_id
        self.holder_id = evidence.holder_id
        self.holder_kind = holder_kind
        self.registry = registry or default_registry
        self.vault = vault
        self.publisher = publisher
        self.enabled_types = set(enabled_connector_types) if enabled_connector_types is not None else None
        self.max_records = max_records
        self.batch_debounce_seconds = float(batch_debounce_seconds)
        self.worker_id = worker_id or f"ingest-{self.holder_id}-{new_id('w')[-8:]}"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = IngestStore(self.store)
        self.queue = IngestQueue(self.store, lease_seconds=lease_seconds, watermarks=watermarks)
        self.meter = StageMeter()
        self._instances: dict[str, Any] = {}
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self.taxonomy = self._ensure_taxonomy(taxonomy)
        self.shards = ShardRouter({DEFAULT_SHARD: evidence}, taxonomy=self.taxonomy)
        self.classifier = DomainClassifier(self.taxonomy, conn=self.store._conn, router=router, tenant_id=self.tenant_id)

    # ------------------------------------------------------------------ taxonomy
    def _ensure_taxonomy(self, tax: Taxonomy | None) -> Taxonomy:
        c = self.store._conn
        if tax is not None or not c.execute("SELECT 1 FROM domains WHERE scope='tenant' LIMIT 1").fetchone():
            install = tax or default_taxonomy()
            with self.store._tx() as cc:
                install_taxonomy_sync(cc, install)
        loaded = load_taxonomy_sync(c)
        self.evidence.set_taxonomy(loaded)
        return loaded

    def reload_taxonomy(self) -> Taxonomy:
        self.taxonomy = load_taxonomy_sync(self.store._conn)
        self.evidence.set_taxonomy(self.taxonomy)
        self.shards.taxonomy = self.taxonomy
        self.classifier.taxonomy = self.taxonomy
        self.classifier.centroids.invalidate()
        return self.taxonomy

    # ------------------------------------------------------------------ connectors
    def connector(self, connector_id: str) -> Any:
        if connector_id not in self._instances:
            con = self.db.get_connector(connector_id)
            if con is None:
                raise KeyError(connector_id)
            self._instances[connector_id] = self.registry.create(con["connector_type"])
        return self._instances[connector_id]

    def context(self, con: Mapping[str, Any]) -> ConnectorContext:
        cfg = dict(con.get("config") or {})
        limits = ConnectorLimits(**{k: v for k, v in (cfg.get("limits") or {}).items() if k in ConnectorLimits.__dataclass_fields__})
        return ConnectorContext(tenant_id=self.tenant_id, holder_id=self.holder_id, connector_id=con["connector_id"],
                                connector_type=con["connector_type"], source_app=con["source_app"], source_account_id=con["source_account_id"],
                                auth_account_id=con.get("auth_account_id") or "", config=cfg, limits=limits, http=NoHttp(),
                                secrets=VaultSecrets(self, con["connector_id"]), checkpoints=StoreCheckpoints(self.queue, con["connector_id"]),
                                log=connector_logger(con["connector_type"], con["connector_id"]), clock=self.clock)

    # ================================================================== fetch side
    async def sync(self, connector_id: str, *, mode: str = "incremental", source_ids: Iterable[str] | None = None, max_pages: int | None = None,
                   priority_class: str | None = None) -> SyncReport:
        """Pull one connector's included sources. ``mode``: ``incremental`` (class live) or ``backfill`` (windows, class
        backfill, throttled by backpressure). Stops at ``max_pages`` pages per stream when given."""
        con = self.db.get_connector(connector_id)
        if con is None:
            raise KeyError(connector_id)
        report = SyncReport(connector_id)
        if con["status"] != "active":
            report.error_code = f"connector_{con['status']}"
            return report
        if self.enabled_types is not None and con["connector_type"] not in self.enabled_types:
            report.error_code = "connector_type_disabled"
            return report
        connector = self.connector(connector_id)
        ctx = self.context(con)
        wanted = set(source_ids) if source_ids is not None else None
        sources = [s for s in self.db.list_sources(connector_id, selection="included")
                   if s.access_state == "ok" and (wanted is None or s.source_id in wanted)]
        for src in sources:
            desc = src.descriptor()
            if mode == "incremental":
                stream = f"incr:{src.external_id}"
                await self._run_stream(connector, ctx, con, src, stream, lambda cur, d=desc: connector.incremental_sync(ctx, d, cur),
                                       priority_class or "live", "incremental", max_pages, report)
            elif mode == "backfill":
                anchor = _utc(parse_iso(con["created_at"]) or self.clock())
                for w in connector.plan_backfill(ctx, desc, anchor=anchor):
                    await self._run_stream(connector, ctx, con, src, w.stream,
                                           lambda cur, d=desc, win=w: connector.initial_backfill(ctx, d, win, cur),
                                           priority_class or "backfill", "backfill", max_pages, report, window=(w.start, w.end))
                    if report.yielded or report.error_code in _STOP_STREAM_CODES:
                        break
            else:
                raise ValueError("mode must be incremental or backfill")
            if report.error_code in _STOP_STREAM_CODES:
                break
        ok = report.error_code is None

        def done(c):
            if ok:
                IngestStore.set_connector_status_sync(c, connector_id, "active", "", success=True)
            elif report.error_code in ("auth_expired", "auth_revoked", "insufficient_scope", "quota_exceeded"):
                status = {"auth_expired": "auth_expired", "auth_revoked": "revoked", "insufficient_scope": "paused",
                          "quota_exceeded": "quota_exceeded"}[report.error_code]
                IngestStore.set_connector_status_sync(c, connector_id, status, report.error_code)
            else:
                IngestStore.set_connector_status_sync(c, connector_id, "active", report.error_code or "")
        await self.store.run_in_tx(done)
        await self.flush_metrics()
        return report

    async def _run_stream(self, connector: Any, ctx: ConnectorContext, con: Mapping[str, Any], src: SourceRow, stream: str,
                          factory: Callable[[Any], AsyncIterator[Any]], cls: str, phase: str, max_pages: int | None, report: SyncReport,
                          window: tuple[str, str] | None = None) -> None:
        cid = con["connector_id"]
        cursor, version = await self.queue.load_checkpoint(cid, stream)
        if phase == "backfill":
            row = self.store._conn.execute("SELECT status FROM connector_checkpoints WHERE connector_id=? AND stream=?", (cid, stream)).fetchone()
            if row is not None and row["status"] == "idle" and version > 0:
                return                                  # this window is complete
        report.streams.append(stream)
        agen = factory(cursor)
        pages = 0
        try:
            while True:
                if cls in THROTTLED_CLASSES and not self.queue.fetch_allowed(cls):
                    report.yielded = True               # resume later from the committed cursor
                    break
                t0 = time.perf_counter()
                try:
                    page = await agen.__anext__()
                except StopAsyncIteration:
                    break
                self.meter.record(cid, "fetch", "ok", (time.perf_counter() - t0) * 1000, count=max(1, page.raw_count))
                report.raw_items += page.raw_count
                events = self._prepare_page(page, connector, ctx, con, src, report)
                new_cursor = connector.checkpoint(page, cursor)
                with self.meter.time(cid, "enqueue") as box:
                    res = await self.queue.commit_page(cid, stream, events, new_cursor, priority_class=cls, expected_version=version, phase=phase,
                                                       items_seen=page.raw_count, window=window, has_more=page.has_more)
                    box["count"] = max(1, res.enqueued)
                if res.duplicates:
                    self.meter.record(cid, "enqueue", "duplicate", count=res.duplicates)
                report.enqueued += res.enqueued
                report.duplicates += res.duplicates
                report.pages += 1
                version = res.version
                cursor = new_cursor or cursor
                pages += 1
                if (max_pages and pages >= max_pages) or ctx.cancelled.is_set():
                    break
        except StaleCheckpoint:
            report.fenced = True
            self.meter.record(cid, "enqueue", "fenced")
        except RateLimited as exc:
            report.error_code = exc.code
            await self.queue.set_stream_status(cid, stream, "paused", exc.code)
        except (AuthExpired, AuthRevoked, InsufficientScope) as exc:
            report.error_code = exc.code
            await self.queue.set_stream_status(cid, stream, "error", exc.code)
        except SourceUnavailable as exc:
            report.error_code = exc.code
            await self.store.run_in_tx(lambda c: IngestStore.update_source_sync(c, src.source_id, access_state="lost", access_lost_at=now_iso()))
        except CursorInvalid as exc:
            report.error_code = exc.code
            await self._reset_cursor(cid, stream, version)
        except ConnectorError as exc:
            report.error_code = exc.code
            await self.queue.set_stream_status(cid, stream, "error", exc.code)
        except Exception as exc:          # a broken connector stops its own stream, nothing else
            report.error_code = "connector_crash"
            logger.warning("connector %s stream failed: %s", cid, type(exc).__name__)
            await self.queue.set_stream_status(cid, stream, "error", "connector_crash")
        finally:
            with contextlib.suppress(Exception):
                await agen.aclose()

    async def _reset_cursor(self, connector_id: str, stream: str, version: int) -> None:
        def fn(c):
            c.execute("UPDATE connector_checkpoints SET cursor='{}', version=version+1, status='error', last_error_code='cursor_invalid' "
                      "WHERE connector_id=? AND stream=? AND version=?", (connector_id, stream, version))
        await self.store.run_in_tx(fn)

    # ---- admit / normalize / admit / redact (pure, before the page transaction)
    def _prepare_page(self, page: Any, connector: Any, ctx: ConnectorContext, con: Mapping[str, Any], src: SourceRow,
                      report: SyncReport) -> list[CanonicalEvent]:
        cid = con["connector_id"]
        exclusions = self.db.exclusions(cid)
        out: list[CanonicalEvent] = []
        for raw in page.items:
            with self.meter.time(cid, "admit") as box:
                if getattr(raw, "source", None) is None or raw.source.external_id != src.external_id:
                    box["outcome"] = "excluded:foreign_source"
                    report.excluded += 1
                    continue
            with self.meter.time(cid, "normalize") as box:
                try:
                    events = list(connector.normalize(raw, ctx))
                except Exception as exc:   # the item is skipped; its content is not kept anywhere
                    box["outcome"] = "error"
                    report.normalize_errors += 1
                    logger.info("normalize failed for an item of connector %s: %s", cid, getattr(exc, "code", type(exc).__name__))
                    continue
                valid = [e for e in events if not self._event_problems(e, con)]
                if len(valid) != len(events):
                    box["outcome"] = "error"
                    report.normalize_errors += len(events) - len(valid)
                events = valid
            for ev in events:
                with self.meter.time(cid, "admit") as box:
                    reason = self._admit_event(ev, src, exclusions)
                    if reason:
                        box["outcome"] = f"excluded:{reason}"
                        report.excluded += 1
                        continue
                with self.meter.time(cid, "redact") as box:
                    ev, n = self._redact(ev, src, ctx)
                    if n:
                        box["outcome"] = "masked"
                out.append(ev)
        return out

    def _event_problems(self, ev: CanonicalEvent, con: Mapping[str, Any]) -> list[str]:
        problems = ev.validate()
        # a connector can only produce records for its own holder, connection and identity namespace
        if (ev.tenant_id, ev.holder_id, ev.connector_id) != (self.tenant_id, self.holder_id, con["connector_id"]):
            problems.append("identity")
        if ev.source_app != con["source_app"] or ev.source_account_id != con["source_account_id"]:
            problems.append("namespace")
        return problems

    def _admit_event(self, ev: CanonicalEvent, src: SourceRow, exclusions: list[dict[str, Any]]) -> str | None:
        tomb = self.db.get_tombstone(ev.record_key)
        if tomb is not None and not tomb["resurrectable"] and not ev.hints.get("resurrect"):
            return "tombstoned"
        for x in exclusions:
            scope, match = x["scope"], str(x["match"])
            if scope == "source_type" and src.source_type == match:
                return "source_type"
            if scope == "source" and src.external_id == match:
                return "source"
            if scope == "conversation" and ev.conversation_id == match:
                return "conversation"
            if scope == "author" and ev.author_id == match:
                return "author"
            if scope == "label" and match in (ev.hints.get("labels") or ()):
                return "label"
            if scope in ("title_regex", "body_regex") and len(match) <= 256:
                hay = ev.title if scope == "title_regex" else ev.body[:20000]
                try:
                    if re.search(match, hay, re.IGNORECASE):
                        return scope
                except re.error:
                    continue
        if self.max_records is not None and ev.kind in CONTENT_KINDS and self.db.get_locator(ev.record_key) is None:
            live = int(self.store._conn.execute("SELECT COUNT(*) AS n FROM ingest_records WHERE deletion_status='live'").fetchone()["n"])
            if live >= self.max_records:
                return "quota"
        return None

    def _redact(self, ev: CanonicalEvent, src: SourceRow, ctx: ConnectorContext) -> tuple[CanonicalEvent, int]:
        """Flags (secrets, injection markers), credential masking, body cap, source sensitivity and ACL narrowing."""
        flags = set(ev.hints.get("flags") or ()) | detect(ev.title, ev.body)
        title, n1 = mask_secrets(ev.title)
        body, n2 = mask_secrets(ev.body)
        limit = int(ctx.limits.max_body_bytes)
        if len(body.encode("utf-8")) > limit:
            body = body.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
            flags.add("truncated")
        sens = max((ev.sensitivity, src.sensitivity, "restricted" if FLAG_SECRET in flags else "public"),
                   key=lambda s: SENSITIVITY_RANK.get(s, 1))
        hints = dict(ev.hints, flags=sorted(flags), source_id=src.source_id)
        return ev.with_(title=title, body=body, sensitivity=sens, permissions=narrow(src.permissions, ev.permissions), hints=hints), n1 + n2

    # ================================================================== process side
    async def process_available(self, *, max_items: int | None = None, worker_id: str | None = None) -> ProcessReport:
        """Lease and process queued items until the queue is empty (or ``max_items``), then publish."""
        worker = worker_id or self.worker_id
        report = ProcessReport()
        await self.queue.requeue_expired()
        purged = 0
        while max_items is None or report.processed < max_items:
            items = await self.queue.lease(worker, n=1)
            if not items:
                break
            outcome = await self.process_item(items[0], worker)
            report.add(outcome)
            if outcome in ("delete", "redaction"):
                purged += 1
        if purged:
            await self.store.optimize_fts()
        await self.flush_batch()
        report.published = await self.publish_pending()
        await self.flush_metrics()
        return report

    def dedupe(self, ev: CanonicalEvent) -> str:
        """§4.3 / §4.6 against ``record_locator``, ``applied_events`` and tombstones (read-only)."""
        if self.db.applied(ev.event_key):
            return "duplicate"
        loc = self.db.get_locator(ev.record_key)
        tomb = self.db.get_tombstone(ev.record_key)
        if (tomb is not None and not tomb["resurrectable"]) or (loc and loc["deletion_status"] in ("deleted_at_source", "purged")):
            if ev.hints.get("resurrect") and ev.kind in CONTENT_KINDS:
                return "new" if loc is None else "update"
            return "duplicate" if ev.kind in ("deletion", "redaction") else "late_after_delete"
        if ev.kind == "deletion":
            return "delete"
        if ev.kind == "redaction":
            return "redaction"
        if ev.kind == "message_version":
            return "historical"
        if ev.kind in ("conversation",):
            return "conversation"
        if loc is None:
            return "new"
        current = loc["current_order_key"] or ""
        if loc["deletion_status"] == "redacted":
            return "update" if ev.order_key > current else "historical"
        if ev.order_key < current:
            return "historical"
        if ev.content_hash != loc["content_hash"]:
            return "update"
        if ev.metadata_hash != loc["metadata_hash"]:
            return "metadata_only"
        return "duplicate"

    async def process_item(self, item: QueueItem, worker: str) -> str:
        cid = item.connector_id
        try:
            ev = item.event()
        except Exception:
            await self.queue.fail(item.item_id, worker, "payload_invalid", retryable=False)
            self.meter.record(cid, "dedupe", "error")
            return "dead"
        try:
            with self.meter.time(cid, "dedupe") as box:
                outcome = self.dedupe(ev)
                box["outcome"] = outcome
            if outcome in ("duplicate", "late_after_delete", "historical"):
                with self.meter.time(cid, "write", outcome):
                    await self._write_noop(item, worker, ev, outcome)
                return outcome
            src = self.db.get_source(ev.hints.get("source_id")) if ev.hints.get("source_id") else None
            memberships: list[Membership] | None = None
            embeddings: list[list[float] | None] | None = None
            if outcome in ("new", "update", "metadata_only") and ev.kind in CONTENT_KINDS:
                embedder = self._embedder_for(ev, src)
                vector = None
                if outcome in ("new", "update"):
                    chunks = chunk_text(ev.body or ev.title, target=self.evidence.chunk_chars)
                    embeddings = await self._embed(embedder, chunks)
                    vector = _mean([e for e in embeddings if e])
                else:
                    vector = self._record_vector(ev.record_id)
                with self.meter.time(cid, "classify") as box:
                    memberships = await self.classifier.classify(self._features(ev, src, vector, embedder), embedder=embedder)
                    box["outcome"] = memberships[0].method if memberships else "none"
            with self.meter.time(cid, "route"):
                primary = memberships[0].domain_id if memberships else None
                shard = self.shards.route_write_sync(self.store._conn, ev.record_key, primary_domain_id=primary, created_at=ev.created_at)
                store = self.shards.writer(shard)
            with self.meter.time(cid, "write", outcome):
                await self._write(store, shard, item, worker, ev, outcome, memberships, embeddings, src)
            return outcome
        except LostLease:
            self.meter.record(cid, "write", "lost_lease")
            return "lost"
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            permanent = isinstance(exc, (PermanentError, ValueError, KeyError, TypeError))
            res = await self.queue.fail(item.item_id, worker, str(code), retryable=not permanent)
            logger.warning("ingest item %s failed (%s): %s", item.item_id, code, res)
            return "dead" if res == "dead" else "retry"

    # ---- helpers for classify
    def _embedder_for(self, ev: CanonicalEvent, src: SourceRow | None) -> Any:
        # restricted content and sources that forbid external models use the local hash embedder only
        if ev.sensitivity == "restricted" or (src is not None and src.allow_external_models == 0):
            return HashEmbedder()
        return self.evidence.llm

    @staticmethod
    async def _embed(embedder: Any, chunks: list[str]) -> list[list[float] | None]:
        out: list[list[float] | None] = []
        for ch in chunks:
            try:
                v = await embedder.embed(ch)
                out.append([float(x) for x in v] if v else None)
            except Exception as exc:
                logger.warning("embedding failed (%s); chunk indexed without a vector", type(exc).__name__)
                out.append(None)
        return out

    def _record_vector(self, record_id: str) -> list[float] | None:
        import struct
        rows = self.store._conn.execute("SELECT embedding FROM memories WHERE chat_id=? AND status='active' AND embedding IS NOT NULL", (record_id,)).fetchall()
        vecs = [list(struct.unpack(f"{len(r['embedding']) // 4}f", r["embedding"])) for r in rows]
        return _mean(vecs)

    def _features(self, ev: CanonicalEvent, src: SourceRow | None, vector: list[float] | None, embedder: Any) -> RecordFeatures:
        conv = ev.conversation_record_id()
        dist: dict[str, float] = {}
        n = 0
        if conv:
            rows = self.store._conn.execute("""SELECT dm.domain_id, COUNT(*) AS n FROM domain_memberships dm JOIN ingest_records r ON r.record_id = dm.record_id
                                               WHERE r.conversation_record_id=? AND dm.status='active' AND dm.is_primary=1 AND r.deletion_status='live'
                                               GROUP BY dm.domain_id""", (conv,)).fetchall()
            n = sum(int(r["n"]) for r in rows)
            dist = {r["domain_id"]: int(r["n"]) / n for r in rows} if n else {}
        return RecordFeatures(record_id=ev.record_id, title=ev.title, text=ev.body, source_app=ev.source_app,
                              container_kind=src.source_type if src else "", container_name=str(ev.hints.get("container_name") or (src.name if src else "")),
                              labels=tuple(ev.hints.get("labels") or ()), author=ev.author_id or "",
                              default_domain_ids=tuple(src.default_domain_ids) if src else (), domain_hints=tuple(ev.hints.get("domain_hints") or ()),
                              conversation_distribution=dist, conversation_size=n, sensitivity=ev.sensitivity, flags=tuple(ev.hints.get("flags") or ()),
                              allow_llm=not (src is not None and src.allow_external_models == 0), vector=vector, embed_model=embed_model_name(embedder))

    # ---- write stage
    def _finish_sync(self, c, *, ev: CanonicalEvent, item: QueueItem, worker: str, outcome: str, shard: str, deletion_status: str,
                     order_key: str | None = None, content_hash: str | None = None, metadata_hash: str | None = None) -> int:
        seq = IngestStore.next_change_seq_sync(c)
        IngestStore.upsert_locator_sync(c, record_key=ev.record_key, record_id=ev.record_id, shard_id=shard, kind=ev.kind, order_key=order_key,
                                        content_hash=content_hash, metadata_hash=metadata_hash, deletion_status=deletion_status, change_seq=seq)
        IngestStore.mark_applied_sync(c, ev.event_key, ev.record_id, outcome)
        self.queue.ack_sync(c, item.item_id, worker, outcome)
        return seq

    def _domains_after_sync(self, c, record_id: str) -> list[str]:
        """Documents and chunk memories carry the record's active domains (primary first), including human corrections."""
        active = [m["domain_id"] for m in active_memberships_sync(c, record_id)]
        c.execute("UPDATE documents SET domains=? WHERE doc_id=?", (j(active), record_id))
        c.execute("UPDATE memories SET metadata=json_set(metadata, '$.domains', json(?)) WHERE chat_id=? AND status='active'", (j(active), record_id))
        c.execute("UPDATE ingest_records SET primary_domain_id=? WHERE record_id=?", (active[0] if active else None, record_id))
        return active

    @staticmethod
    def _recount_domains_sync(c, domain_ids: Iterable[str], shard: str) -> None:
        for d in set(domain_ids):
            n = c.execute("""SELECT COUNT(*) AS n FROM domain_memberships dm JOIN ingest_records r ON r.record_id = dm.record_id
                             WHERE dm.domain_id=? AND dm.status='active' AND r.deletion_status IN ('live', 'redacted')""", (d,)).fetchone()["n"]
            c.execute("INSERT OR REPLACE INTO domain_shard_counts(domain_id, shard_id, records) VALUES (?, ?, ?)", (d, shard, int(n)))

    def _record_entities_sync(self, c, ev: CanonicalEvent) -> tuple[str | None, list[str]]:
        author = person_entity_id(ev.source_app, ev.author_id)
        participants = [p for p in (person_entity_id(ev.source_app, x) for x in ev.participant_ids) if p]
        c.execute("DELETE FROM record_entities WHERE record_id=? AND method='structural'", (ev.record_id,))
        rows = []
        if author:
            rows.append((ev.record_id, author, "author", 1.0, "structural"))
        rows += [(ev.record_id, p, "participant", 1.0, "structural") for p in participants if p != author]
        conv = ev.conversation_record_id()
        if conv:
            rows.append((ev.record_id, f"conversation:{conv}", "container", 1.0, "structural"))
        c.executemany("INSERT OR IGNORE INTO record_entities(record_id, entity_id, role, confidence, method) VALUES (?, ?, ?, ?, ?)", rows)
        return author, participants

    def _entities_for(self, ev: CanonicalEvent) -> list[tuple[str, str, str]]:
        author = person_entity_id(ev.source_app, ev.author_id)
        return [(author, str(ev.author_id), "person")] if author else []

    def _evidence_event_sync(self, c, ev: CanonicalEvent, event: str, payload: dict[str, Any]) -> None:
        msg_id = f"evidence:{ev.record_id}:{sha256(ev.order_key, event)[:16]}"
        IngestStore.add_outbox_sync(c, msg_id, "evidence_event", {"event": event, "doc_id": None, "document": None, "request_msg_id": None, **payload})

    async def _write(self, store: Any, shard: str, item: QueueItem, worker: str, ev: CanonicalEvent, outcome: str,
                     memberships: list[Membership] | None, embeddings: list[list[float] | None] | None, src: SourceRow | None) -> None:
        source_id = src.source_id if src else ev.hints.get("source_id")
        normalizer = self._normalizer_version(ev.connector_id)
        changed = ev.updated_at or ev.created_at or ev.observed_at
        flags = list(ev.hints.get("flags") or ())
        proposed = [m.domain_id for m in memberships or []]
        meta = {"record_id": ev.record_id, "source_app": ev.source_app, "record_kind": ev.kind}
        title = ev.title or f"{ev.source_app} {ev.kind}"
        body = ev.body or ev.title
        conv = ev.conversation_record_id()
        container = ev.hints.get("container_name")

        if outcome == "new":
            ran = {}

            def extra_new(c, w):
                seq = self._finish_sync(c, ev=ev, item=item, worker=worker, outcome=outcome, shard=shard, deletion_status="live",
                                        order_key=ev.order_key, content_hash=ev.content_hash, metadata_hash=ev.metadata_hash)
                author, participants = self._record_entities_sync(c, ev)
                IngestStore.write_record_sync(c, ev, source_id=source_id, primary_domain_id=proposed[0] if proposed else None, content_changed_at=changed,
                                              flags=flags, author_entity_id=author, participant_entity_ids=participants, normalizer_version=normalizer,
                                              change_seq=seq, new_version=True)
                IngestStore.add_version_sync(c, ev, kind=ev.kind, doc_version=w["version"])
                c.executemany("INSERT OR IGNORE INTO record_memories(record_id, memory_id, doc_version, chunk_index, origin) VALUES (?, ?, ?, ?, 'chunk')",
                              [(ev.record_id, mid, w["version"], i) for i, mid in enumerate(w["memory_ids"])])
                apply_memberships_sync(c, ev.record_id, list(memberships or []))
                active = self._domains_after_sync(c, ev.record_id)
                self._recount_domains_sync(c, active, shard)
                if conv and container:
                    self.store._upsert_chat_sync(c, conv, title=str(container))
                IngestStore.note_batch_sync(c, source_app=ev.source_app, domains=publishable(active))
                ran["ok"] = True

            await store.ingest_document(title, body, kind=ev.kind, doc_id=ev.record_id, observed_at=changed, domains=proposed or None,
                                        origin_id=ev.record_key, source_root_id=ev.source_root_id or "", root_known=ev.root_known,
                                        conversation_chat_id=conv, speaker=ev.author_id or ev.source_app, extra_metadata=meta,
                                        entities=self._entities_for(ev), audit_detail="ids", classify=False, embeddings=embeddings, extra_sync=extra_new)
            if not ran:
                raise PermanentError("a document with this record id already exists", code="record_exists")
            return

        if outcome == "update":
            before = self.db.get_record(ev.record_id) or {}

            def extra_update(c, w):
                seq = self._finish_sync(c, ev=ev, item=item, worker=worker, outcome=outcome, shard=shard, deletion_status="live",
                                        order_key=ev.order_key, content_hash=ev.content_hash, metadata_hash=ev.metadata_hash)
                author, participants = self._record_entities_sync(c, ev)
                IngestStore.write_record_sync(c, ev, source_id=source_id, primary_domain_id=None, content_changed_at=changed, flags=flags,
                                              author_entity_id=author, participant_entity_ids=participants, normalizer_version=normalizer,
                                              change_seq=seq, new_version=True)
                IngestStore.add_version_sync(c, ev, kind=ev.kind, doc_version=w["version"])
                c.executemany("INSERT OR IGNORE INTO record_memories(record_id, memory_id, doc_version, chunk_index, origin) VALUES (?, ?, ?, ?, 'chunk')",
                              [(ev.record_id, mid, w["version"], i) for i, mid in enumerate(w["memory_ids"])])
                old = [m["domain_id"] for m in active_memberships_sync(c, ev.record_id)]
                apply_memberships_sync(c, ev.record_id, list(memberships or []), reason="update")
                active = self._domains_after_sync(c, ev.record_id)
                self._recount_domains_sync(c, set(old) | set(active), shard)
                IngestStore.drop_tombstone_sync(c, ev.record_key)       # content shown again after a redaction
                self._evidence_event_sync(c, ev, "revised", {"affected_ref_ids": list(w.get("affected_ref_ids") or []),
                                                              "new_source_root_id": ev.source_root_id or "",
                                                              "previous_source_root_id": before.get("source_root_id"),
                                                              "reason": "source_edit", "version": w["version"]})

            await store.revise_document(ev.record_id, body, title=title, observed_at=changed, reason="source_edit", domains=proposed or None,
                                        source_root_id=ev.source_root_id or "", root_known=ev.root_known, conversation_chat_id=conv,
                                        speaker=ev.author_id or ev.source_app, extra_metadata=meta, entities=self._entities_for(ev), audit_detail="ids",
                                        classify=False, embeddings=embeddings, extra_sync=extra_update)
            return

        if outcome == "metadata_only":
            def extra_meta(c, _w):
                seq = self._finish_sync(c, ev=ev, item=item, worker=worker, outcome=outcome, shard=shard, deletion_status="live",
                                        order_key=ev.order_key, content_hash=ev.content_hash, metadata_hash=ev.metadata_hash)
                author, participants = self._record_entities_sync(c, ev)
                IngestStore.write_record_sync(c, ev, source_id=source_id, primary_domain_id=None, content_changed_at=None, flags=flags,
                                              author_entity_id=author, participant_entity_ids=participants, normalizer_version=normalizer,
                                              change_seq=seq, new_version=False)
                IngestStore.add_version_sync(c, ev, kind="metadata", doc_version=None)
                old = [m["domain_id"] for m in active_memberships_sync(c, ev.record_id)]
                if memberships is not None:
                    apply_memberships_sync(c, ev.record_id, list(memberships), reason="metadata")
                active = self._domains_after_sync(c, ev.record_id)
                self._recount_domains_sync(c, set(old) | set(active), shard)

            await store.update_document_metadata(ev.record_id, extra_sync=extra_meta)
            return

        if outcome in ("delete", "redaction"):
            status = "deleted" if outcome == "delete" else "redacted"
            reason = str(ev.hints.get("reason") or (ev.deletion_status if outcome == "delete" else "redaction"))
            doc = await store.store.get_document(ev.record_id)
            loc = self.db.get_locator(ev.record_key)

            def extra_delete(c, w):
                old = [r["domain_id"] for r in c.execute("SELECT DISTINCT domain_id FROM domain_membership_history WHERE record_id=?", (ev.record_id,))]
                rec_status = "purged" if outcome == "delete" else "redacted"
                self._finish_sync(c, ev=ev, item=item, worker=worker, outcome=outcome, shard=shard, deletion_status=rec_status,
                                  order_key=ev.order_key if outcome == "redaction" else (loc or {}).get("current_order_key"),
                                  content_hash=(loc or {}).get("content_hash"), metadata_hash=(loc or {}).get("metadata_hash"))
                c.execute("UPDATE ingest_records SET deletion_status=? WHERE record_id=?", (rec_status, ev.record_id))
                IngestStore.add_version_sync(c, ev, kind="deletion" if outcome == "delete" else "redaction", doc_version=None)
                affected = list(w.get("affected_ref_ids") or [])
                IngestStore.upsert_tombstone_sync(c, record_key=ev.record_key, record_id=ev.record_id, reason=reason, order_key=ev.order_key,
                                                  resurrectable=outcome == "redaction", affected_ref_ids=affected, purged=True)
                self._recount_domains_sync(c, old, shard)
                if w.get("doc_id"):
                    # interim value until the coordinator accepts 'deleted' (INGESTION.md G10): 'retracted' withdraws the refs
                    self._evidence_event_sync(c, ev, "retracted", {"affected_ref_ids": affected, "reason": reason, "deletion": outcome})

            if doc is not None:
                await store.delete_document(ev.record_id, reason, status=status, extra_sync=extra_delete, optimize=False)
            else:
                await self.store.run_in_tx(lambda c: extra_delete(c, {}))
            return

        if outcome == "conversation":
            def conv_fn(c):
                seq = self._finish_sync(c, ev=ev, item=item, worker=worker, outcome=outcome, shard=shard, deletion_status="live",
                                        order_key=ev.order_key, content_hash=ev.content_hash, metadata_hash=ev.metadata_hash)
                self.store._upsert_chat_sync(c, ev.record_id, title=ev.title or None)
                IngestStore.write_record_sync(c, ev, source_id=source_id, primary_domain_id=None, content_changed_at=changed, flags=flags,
                                              author_entity_id=None, participant_entity_ids=[], normalizer_version=normalizer, change_seq=seq,
                                              new_version=True)
            await self.store.run_in_tx(conv_fn)
            return
        raise PermanentError(f"unknown outcome {outcome}", code="bad_outcome")

    def _normalizer_version(self, connector_id: str) -> str:
        """The manifest version of the connector that produced the record (re-normalization on reindex compares it)."""
        con = self.db.get_connector(connector_id)
        try:
            return self.registry.get(con["connector_type"]).manifest.version if con else "0.0.0"
        except KeyError:
            return "0.0.0"

    async def _write_noop(self, item: QueueItem, worker: str, ev: CanonicalEvent, outcome: str) -> None:
        def fn(c):
            if outcome == "historical":
                IngestStore.add_version_sync(c, ev, kind="historical", doc_version=None)
            if not self.db.applied(ev.event_key):
                IngestStore.mark_applied_sync(c, ev.event_key, ev.record_id, outcome)
            self.queue.ack_sync(c, item.item_id, worker, outcome)
        await self.store.run_in_tx(fn)

    # ================================================================== publish
    async def flush_batch(self, *, force: bool = False) -> str | None:
        """Turn the pending counters into one batched, title-free ``ingest_result`` (debounced per holder)."""
        c = self.store._conn
        r = c.execute("SELECT value FROM holder_meta WHERE key='ingest_batch_pending'").fetchone()
        pending = jl(r["value"], {}) if r else {}
        if not int(pending.get("records") or 0):
            return None
        last = c.execute("SELECT value FROM holder_meta WHERE key='ingest_batch_flushed_at'").fetchone()
        if not force and last is not None:
            elapsed = (self.clock() - _utc(parse_iso(last["value"]) or self.clock())).total_seconds()
            if elapsed < self.batch_debounce_seconds:
                return None

        def fn(cc):
            seq = self.store._increment_meta_sync(cc, "ingest_batch_seq")
            by_domain = {d: n for d, n in (pending.get("by_domain") or {}).items() if publishable([d])}
            payload = {"document": None, "title": "", "doc_id": None, "error": None, "request_msg_id": None, "domains": sorted(by_domain),
                       "batch": {"records": int(pending["records"]), "by_app": dict(pending.get("by_app") or {}), "by_domain": by_domain}}
            msg_id = f"ingestbatch:{self.holder_id}:{seq}"
            IngestStore.add_outbox_sync(cc, msg_id, "ingest_result", payload)
            self.store._set_holder_meta_sync(cc, "ingest_batch_pending", "{}")
            self.store._set_holder_meta_sync(cc, "ingest_batch_flushed_at", self.clock().isoformat(timespec="seconds"))
            return msg_id
        return await self.store.run_in_tx(fn)

    async def publish_pending(self) -> int:
        """Send outbox envelopes in order; each is marked sent only after the publisher accepted it (at-least-once; the
        deterministic msg_id makes the transport de-duplicate a re-send)."""
        if self.publisher is None:
            return 0
        sent = 0
        for row in self.db.pending_outbox():
            t0 = time.perf_counter()
            try:
                await self.publisher.publish_ingest_output(row["kind"], dict(row["payload"]), msg_id=row["msg_id"])
            except Exception as exc:
                self.meter.record("", "publish", "error", (time.perf_counter() - t0) * 1000)
                logger.warning("publishing %s failed (%s); it stays in the outbox", row["kind"], type(exc).__name__)
                break
            self.meter.record("", "publish", row["kind"], (time.perf_counter() - t0) * 1000)

            def mark(c, msg_id=row["msg_id"], kind=row["kind"]):
                IngestStore.mark_sent_sync(c, msg_id)
                if kind == "evidence_event":
                    record_id = msg_id.split(":")[1]
                    c.execute("UPDATE deletion_tombstones SET propagated_at=? WHERE record_id=? AND propagated_at IS NULL", (now_iso(), record_id))
            await self.store.run_in_tx(mark)
            sent += 1
        return sent

    async def flush_metrics(self) -> None:
        rows = self.meter.drain()
        if rows:
            await self.store.run_in_tx(lambda c: IngestStore.add_metrics_sync(c, rows))


def _mean(vecs: list[list[float]]) -> list[float] | None:
    vecs = [v for v in vecs if v]
    if not vecs:
        return None
    dim = len(vecs[0])
    same = [v for v in vecs if len(v) == dim]
    return [sum(v[i] for v in same) / len(same) for i in range(dim)]

"""``MycelicService``: the one object the API, MCP tools and CLI talk to.

It owns the store, the transport and the aggregator and runs the two background loops that make the data
path real:

* **publisher** — drains the outbox (``events.status='pending'``) into JetStream, marking rows published with
  their stream sequence; a broker outage leaves rows pending and the loop retries with backoff;
* **consumer** — pulls from the durable consumer, applies each event inside one transaction (record the event,
  upsert the memory it carries, run aggregation, enqueue derived events) and acks only after the commit.

The complete path is therefore::

    agent -> POST /memory -> [txn: memory + event(pending)] -> publisher -> JetStream (file store)
          -> consumer -> apply [txn: applied + derived memories + lineage + derived events(pending)]
          -> publisher -> JetStream -> consumer -> apply (idempotent no-op) ...
    POST /query -> retrieval over what the caller may see -> answer + lineage

Recovery: on start, a database that has never applied anything while the stream already holds events is
treated as lost state and the durable consumer is reset so the whole log replays; ``replay()`` does the same on
demand.  Because derived ids are deterministic, a replay converges on exactly the state it rebuilds.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from .aggregation import MYCELIC_PRODUCER, Aggregator, Derivation
from .auth import Authenticator, Principal, RateLimiter, generate_api_key
from .config import Settings
from .hierarchy import AgentPath, HierarchyError, LAYERS, layer_index, split_path
from .lineage import LineageNotFound, reconstruct
from .metrics import Metrics
from .models import (
    ALL_SCOPES, DEFAULT_AGENT_SCOPES, MEMORY_KINDS, OPERATORS, VISIBILITY, Agent, EventRecord, LineageEdge, Memory, Rule,
    content_hash, new_id, now_iso, parse_iso,
)
from .retrieval import Retriever
from .store import MycelicStore, Tx
from .transport import Transport, build_transport, subject_for

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
#: metadata keys the aggregator owns; an agent may not set them on a raw observation
RESERVED_METADATA_KEYS = frozenset({"agg_key", "promoted_from", "version_of", "contributing_agents", "contributing_teams",
                                    "children", "child_layer", "parent_count", "fragility", "slots", "candidates",
                                    "effective_min_support", "registered_child_units", "status_reason", "reactivated_at",
                                    "roots", "evidence", "corroborated_units", "rule_chain", "fragility_scored_candidates"})
_SLOT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


class ValidationError(ValueError):
    """Bad client input; the API maps it to 400 with the message."""


class Forbidden(PermissionError):
    pass


class NotFound(KeyError):
    pass


def _s(body: dict[str, Any], key: str, *, required: bool = False, max_len: int = 200, pattern: re.Pattern | None = None) -> str | None:
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise ValidationError(f"'{key}' is required")
        return None
    if not isinstance(v, str):
        raise ValidationError(f"'{key}' must be a string")
    v = v.strip()
    if not v and required:
        raise ValidationError(f"'{key}' is required")
    if len(v) > max_len:
        raise ValidationError(f"'{key}' is longer than {max_len} characters")
    if pattern and v and not pattern.match(v):
        raise ValidationError(f"'{key}' contains characters outside {pattern.pattern}")
    return v or None


def _f(body: dict[str, Any], key: str, default: float) -> float:
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValidationError(f"'{key}' must be a number")
    if not 0.0 <= float(v) <= 1.0:
        raise ValidationError(f"'{key}' must be between 0 and 1")
    return float(v)


def _iso(body: dict[str, Any], key: str) -> str | None:
    v = _s(body, key, max_len=40)
    if v is None:
        return None
    dt = parse_iso(v)
    if dt is None:
        raise ValidationError(f"'{key}' must be an ISO-8601 timestamp")
    return dt.isoformat(timespec="seconds")


def _small_dict(body: dict[str, Any], key: str, max_bytes: int) -> dict[str, Any]:
    v = body.get(key)
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValidationError(f"'{key}' must be an object")
    if len(json.dumps(v, ensure_ascii=False)) > max_bytes:
        raise ValidationError(f"'{key}' is larger than {max_bytes} bytes")
    return v


class MycelicService:
    def __init__(self, settings: Settings, *, store: MycelicStore | None = None, transport: Transport | None = None,
                 metrics: Metrics | None = None) -> None:
        self.settings = settings
        self.metrics = metrics or Metrics()
        self.store = store or MycelicStore(settings.db_path)
        self.transport = transport or build_transport(settings, on_state_change=self._transport_state)
        self.aggregator = Aggregator(self.store, min_support=settings.min_support)
        self.retriever = Retriever(self.store)
        self.auth = Authenticator(self.store, admin_token=settings.admin_token)
        self.limiter = RateLimiter(rps=settings.rate_limit_rps, burst=settings.rate_limit_burst)
        self.started_at: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._outbox_wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._consumer_running = False
        self._publisher_running = False
        self._replay_target: int | None = None
        self._last_seen_touch: dict[str, float] = {}
        self.metrics.info.labels(VERSION).set(1)

    # ------------------------------------------------------------------ lifecycle
    async def start(self, *, background: bool = True) -> None:
        self.started_at = now_iso()
        self._stop.clear()
        self.load_rules_file()
        try:
            pruned = await self.store.prune_audit(self.settings.audit_retention_days)
            if pruned:
                logger.info("pruned %d audit rows older than %d days", pruned, self.settings.audit_retention_days)
        except Exception:
            logger.exception("audit pruning failed")
        await self._connect_with_retry(first=True)
        if background:
            self._tasks = [asyncio.create_task(self._transport_keeper(), name="mycelic-transport"),
                           asyncio.create_task(self._publisher_loop(), name="mycelic-publisher"),
                           asyncio.create_task(self._consumer_loop(), name="mycelic-consumer")]
        logger.info("mycelic %s started (db=%s, transport=%s)", VERSION, self.store.db_path, self.transport.name)

    async def stop(self) -> None:
        self._stop.set()
        self._outbox_wake.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        await self.transport.close()

    async def close(self) -> None:
        await self.stop()
        await self.store.close()

    def _transport_state(self, connected: bool) -> None:
        self.metrics.transport_connected.set(1 if connected else 0)
        if connected:
            self._outbox_wake.set()

    async def _connect_with_retry(self, *, first: bool) -> bool:
        try:
            await self.transport.connect()
        except Exception as exc:
            logger.warning("transport connect failed: %s: %s", type(exc).__name__, exc)
            self.metrics.transport_connected.set(0)
            return False
        self.metrics.transport_connected.set(1)
        await self._recover_if_needed()
        return True

    async def _set_replay_target(self, target: int | None) -> None:
        """Remember how far a replay must go, in memory and in the database, so a crash mid-rebuild resumes it."""
        self._replay_target = target
        async with self.store.transaction() as tx:
            if target is None:
                tx.delete_meta("replay_target_seq")
            else:
                tx.set_meta("replay_target_seq", str(target))

    async def _recover_if_needed(self) -> None:
        """Bring a database that is out of step with the stream back in sync.

        * never applied anything while the stream has events (fresh volume, lost database): replay the whole log;
        * applied less than the durable consumer has acknowledged (database restored from a backup), or the
          durable consumer is brand new while the database is not (consumer lost): recreate the consumer at
          ``last_applied_seq + 1`` so the events since then are delivered again (applying is idempotent);
        * the stream is shorter than what the database applied (stream purged or recreated): keep serving from
          the database, start counting from the new stream, and say so loudly;
        * a replay recorded in the database that did not finish (crash mid-rebuild): keep reporting not-ready
          until it completes.
        """
        last = self.store.get_meta("last_applied_seq")
        info = await self.transport.info()
        count = info.get("stream_messages") or 0
        last_seq = int(info.get("last_seq") or count or 0)
        if last is None:
            if count:
                logger.warning("fresh database but the stream holds %d events: replaying the full log to rebuild state", count)
                await self.transport.reset_consumer()
                await self._set_replay_target(last_seq)
                self.metrics.recoveries.labels("replay_fresh_db").inc()
            return
        applied = int(last)
        if last_seq < applied and info.get("connected"):
            logger.error("the stream ends at seq %d but this database applied up to %d: the stream was purged or recreated; "
                         "serving from the database, replay is impossible until events accumulate again", last_seq, applied)
            self.metrics.recoveries.labels("stream_behind_database").inc()
            async with self.store.transaction() as tx:
                tx.set_meta("last_applied_seq", "0")
                tx.delete_meta("replay_target_seq")
                tx.audit("mycelic", "recovery.stream_behind_database", None, {"stream_last_seq": last_seq, "applied": applied})
            self._replay_target = None
            return
        floor_getter = getattr(self.transport, "consumer_ack_floor", None)
        floor = await floor_getter() if floor_getter else None
        delivered = int(info.get("consumer_delivered_seq") or 0)
        behind = floor is not None and applied < floor
        fresh_consumer = floor is not None and floor == 0 and delivered == 0 and applied > 0
        if behind or fresh_consumer:
            kind = "replay_restored_backup" if behind else "consumer_recreated"
            logger.warning("database applied up to seq %d but the consumer %s: re-delivering from %d", applied,
                           f"acknowledged up to {floor} (restored backup?)" if behind else "is new (consumer lost?)", applied + 1)
            await self.transport.reset_consumer(start_seq=applied + 1)
            await self._set_replay_target(last_seq if last_seq > applied else None)
            self.metrics.recoveries.labels(kind).inc()
            return
        pending = self.store.get_meta("replay_target_seq")
        if pending is not None:
            if applied < int(pending):
                logger.warning("resuming an unfinished replay to seq %s (applied %d)", pending, applied)
                self._replay_target = int(pending)
                self.metrics.recoveries.labels("replay_resumed").inc()
            else:
                await self._set_replay_target(None)

    def load_rules_file(self) -> int:
        """Apply the rules file at start. A rule that differs from what is stored is upserted *and* appended to the
        event log, so a rebuild from the stream ends with the same rules as this node; identical rules are skipped."""
        path = self.settings.rules_file
        if not path:
            return 0
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = data.get("rules", data) if isinstance(data, dict) else data
        n = 0
        for item in rules:
            rule = self._rule_from_body(item)
            self._check_rule_cycles(rule)
            existing = self.store.get_rule(rule.rule_id)
            if existing is not None and existing.to_dict() == rule.to_dict():
                continue
            c = self.store._conn
            c.execute("BEGIN IMMEDIATE")
            try:
                tx = Tx(self.store, c)
                tx.upsert_rule(rule)
                tx.insert_event(self._admin_event("rule.upserted", rule.org_id or "_", rule.to_dict()))
                tx.audit("rules_file", "rule.upsert", rule.rule_id, {"source": path, "target_layer": rule.target_layer})
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
            n += 1
        if n:
            logger.info("loaded %d changed rules from %s", n, path)
            self._outbox_wake.set()
        return n

    # ------------------------------------------------------------------ background loops
    async def _transport_keeper(self) -> None:
        """Establish the transport when there is no live client; a client that is reconnecting is left alone."""
        delay = 1.0
        while not self._stop.is_set():
            if getattr(self.transport, "needs_connect", not self.transport.connected):
                ok = await self._connect_with_retry(first=False)
                delay = 1.0 if ok else min(15.0, delay * 2)
                if ok:
                    self.metrics.recoveries.labels("transport_reconnect").inc()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay if not self.transport.connected else 2.0)
            except asyncio.TimeoutError:
                pass

    async def _publisher_loop(self) -> None:
        self._publisher_running = True
        backoff = self.settings.publish_interval_seconds
        try:
            while not self._stop.is_set():
                if not self.transport.connected:
                    await self._sleep(1.0)
                    continue
                rows = self.store.pending_events(self.settings.publish_batch)
                if not rows:
                    self._outbox_wake.clear()
                    try:
                        await asyncio.wait_for(self._outbox_wake.wait(), timeout=self.settings.publish_interval_seconds)
                    except asyncio.TimeoutError:
                        pass
                    continue
                failed = False
                for ev in rows:
                    wire = json.dumps(self._event_wire(ev), ensure_ascii=False).encode("utf-8")
                    try:
                        seq = await self.transport.publish(ev.subject, wire, ev.event_id, headers=self._sign(wire))
                    except Exception as exc:
                        name = type(exc).__name__
                        if name in ("MaxPayloadError", "BadRequestError") or len(wire) > self.settings.max_event_bytes:
                            # permanent: the broker will never take this event; record it and move on so the log continues
                            async with self.store.transaction() as tx:
                                tx.mark_failed(ev.event_id, f"{name}: {exc}")
                                tx.audit("mycelic", "event.unpublishable", ev.event_id, {"error": f"{name}: {exc}", "bytes": len(wire)})
                            self.metrics.events_failed.labels("publish_permanent").inc()
                            logger.error("event %s cannot be published (%s: %s); marked failed", ev.event_id, name, exc)
                            continue
                        failed = True
                        async with self.store.transaction() as tx:
                            tx.mark_publish_failed(ev.event_id, f"{name}: {exc}")
                        self.metrics.events_failed.labels("publish").inc()
                        logger.warning("publish of %s failed: %s: %s", ev.event_id, name, exc)
                        break
                    async with self.store.transaction() as tx:
                        tx.mark_published(ev.event_id, seq)
                    self.metrics.events_published.labels(ev.kind).inc()
                if failed:
                    await self._sleep(min(10.0, backoff))
                    backoff = min(10.0, backoff * 2)
                else:
                    backoff = self.settings.publish_interval_seconds
        finally:
            self._publisher_running = False

    async def _consumer_loop(self) -> None:
        self._consumer_running = True
        backoff = 0.5
        try:
            while not self._stop.is_set():
                if not self.transport.connected:
                    await self._sleep(0.5)
                    continue
                try:
                    deliveries = await self.transport.fetch(self.settings.consume_batch, timeout=1.0)
                except Exception as exc:
                    logger.warning("fetch failed: %s: %s", type(exc).__name__, exc)
                    await self._sleep(backoff)
                    backoff = min(10.0, backoff * 2)
                    continue
                backoff = 0.5
                if not deliveries:
                    self._idle.set()
                    continue
                self._idle.clear()
                for d in deliveries:
                    await self._handle_delivery(d)
                self._idle.set()
        finally:
            self._consumer_running = False

    async def _handle_delivery(self, d: Any) -> None:
        t0 = time.perf_counter()
        if self.settings.event_signing_key and not self._verify(d.data, getattr(d, "headers", {}) or {}):
            # not ours: whoever published it did not hold the signing key.  Drop it loudly and permanently.
            self.metrics.events_failed.labels("signature").inc()
            logger.error("event at seq %s has a missing or invalid signature; terminated", d.seq)
            await d.term()
            async with self.store.transaction() as tx:
                tx.audit("mycelic", "event.rejected", None, {"reason": "invalid signature", "seq": d.seq, "subject": d.subject})
                self._progress_in_tx(tx, d.seq)
            self._note_progress(d.seq)
            return
        try:
            event = json.loads(d.data.decode("utf-8"))
            if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
                raise ValidationError("event without event_id")
            result = await self.apply_event(event, seq=d.seq)
        except Exception as exc:
            self.metrics.events_failed.labels("apply").inc()
            if d.num_delivered >= self.settings.nats_max_deliver:
                logger.error("event at seq %s failed %d times, terminating: %s: %s", d.seq, d.num_delivered, type(exc).__name__, exc)
                await d.term()
                try:
                    eid = json.loads(d.data.decode("utf-8")).get("event_id")
                except Exception:
                    eid = None
                async with self.store.transaction() as tx:
                    if isinstance(eid, str) and tx.event_status(eid) is not None:
                        tx.mark_failed(eid, f"{type(exc).__name__}: {exc}")
                    tx.audit("mycelic", "event.failed", eid if isinstance(eid, str) else None,
                             {"error": f"{type(exc).__name__}: {exc}", "seq": d.seq})
                    self._progress_in_tx(tx, d.seq)
                self._note_progress(d.seq)
            else:
                logger.warning("event at seq %s failed (attempt %d): %s: %s", d.seq, d.num_delivered, type(exc).__name__, exc)
                # back off *before* the nak so the message stays in flight and nothing overtakes it
                await self._sleep(min(10.0, 0.25 * (2 ** d.num_delivered)))
                await d.nak()
            return
        await d.ack()
        self.metrics.aggregation_latency.observe(time.perf_counter() - t0)
        self.metrics.events_applied.labels(event.get("kind", "?"), result).inc()
        self._note_progress(d.seq)

    def _progress_in_tx(self, tx: Tx, seq: int | None) -> None:
        """Record the stream position inside the transaction that consumed it (applied, rejected or terminated)."""
        if seq is None:
            return
        tx.set_meta("last_applied_seq", str(seq))
        if self._replay_target is not None and seq >= self._replay_target:
            tx.delete_meta("replay_target_seq")

    def _note_progress(self, seq: int | None) -> None:
        if self._replay_target is None:
            return
        self.metrics.replay_events.inc()
        if seq is not None and seq >= self._replay_target:
            logger.info("replay complete at seq %s", seq)
            self._replay_target = None

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def wait_idle(self, timeout: float = 10.0) -> bool:
        """Wait until the outbox is empty and the consumer has nothing pending (tests, demos, CLI)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = await self.transport.info()
            pending = self.store.stats()["outbox_pending"]
            if pending == 0 and self._idle.is_set() and info.get("consumer_pending", 0) == 0 and self._replay_target is None:
                # one more fetch round so a just-published event is applied
                await asyncio.sleep(0.25)
                info = await self.transport.info()
                if self.store.stats()["outbox_pending"] == 0 and self._idle.is_set() and info.get("consumer_pending", 0) == 0:
                    return True
            await asyncio.sleep(0.1)
        return False

    # ------------------------------------------------------------------ wire format
    @staticmethod
    def _event_wire(ev: EventRecord) -> dict[str, Any]:
        return {"event_id": ev.event_id, "kind": ev.kind, "org_id": ev.org_id, "agent_id": ev.agent_id,
                "created_at": ev.created_at, "payload": ev.payload, "schema": 1}

    def _sign(self, wire: bytes) -> dict[str, str]:
        """HMAC-SHA256 over the exact bytes published, so the consumer can tell its own events from injected ones."""
        key = self.settings.event_signing_key
        if not key:
            return {}
        return {"Mycelic-Signature": "v1=" + hmac.new(key.encode("utf-8"), wire, hashlib.sha256).hexdigest()}

    def _verify(self, wire: bytes, headers: dict[str, str]) -> bool:
        key = self.settings.event_signing_key
        if not key:
            return True
        expected = "v1=" + hmac.new(key.encode("utf-8"), wire, hashlib.sha256).hexdigest()
        given = headers.get("Mycelic-Signature", "")
        return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))

    def public_view(self, m: Memory | dict[str, Any], principal: Principal) -> dict[str, Any]:
        """What a caller may see of a memory: metadata that names other teams' agents is reserved for admins."""
        d = m.to_dict() if isinstance(m, Memory) else dict(m)
        if principal.is_admin or (d.get("layer") == "agent" and d.get("producer_id") == principal.id):
            return d
        meta = d.get("metadata") or {}
        allowed = {"agg_key", "child_layer", "parent_count", "version_of", "fragility", "contributing_teams", "children",
                   "promoted_from", "effective_min_support", "corroborated_units", "rule_chain"}
        if principal.has("lineage:read"):        # the root ids are exactly what GET /lineage/{id} shows this caller
            allowed.add("roots")
        d["metadata"] = {k: v for k, v in meta.items() if k in allowed}
        d["local_ref"] = None
        return d

    # ------------------------------------------------------------------ authentication helpers
    def authenticate(self, authorization: str | None) -> Principal:
        p = self.auth.authenticate(authorization)
        if p.kind == "agent":
            now = time.monotonic()
            last = self._last_seen_touch.get(p.id, 0.0)
            if now - last > 30.0:
                self._last_seen_touch[p.id] = now
                asyncio.get_running_loop().create_task(self.store.touch_agent(p.id))
        return p

    # ------------------------------------------------------------------ ingest
    def _validate_memory_body(self, principal: Principal, body: dict[str, Any], *, source_event_ids: list[str] | None = None) -> Memory:
        if principal.kind != "agent" or principal.agent is None:
            raise Forbidden("only agents can write memories (administrators register agents)")
        if not principal.has("memory:write"):
            raise Forbidden("missing scope memory:write")
        claimed = _s(body, "agent_id")
        if claimed and claimed != principal.id:
            raise Forbidden("agents may only write as themselves")
        text = _s(body, "text", required=True, max_len=self.settings.max_text_chars)
        kind = _s(body, "kind", max_len=40) or "observation"
        if kind not in MEMORY_KINDS:
            raise ValidationError(f"'kind' must be one of {MEMORY_KINDS}")
        visibility = _s(body, "visibility", max_len=10) or "team"
        if visibility not in VISIBILITY:
            raise ValidationError(f"'visibility' must be one of {VISIBILITY}")
        srcs = body.get("source_event_ids") or []
        if not isinstance(srcs, list) or len(srcs) > 50 or any(not isinstance(x, str) or not _ID_RE.match(x) for x in srcs):
            raise ValidationError("'source_event_ids' must be a list of at most 50 ids")
        if srcs:
            found = self.store.get_events(srcs)
            # one message for unknown and foreign ids alike, so the check is not an oracle for other organizations
            if any(eid not in found or found[eid].org_id != principal.org_id for eid in srcs):
                raise ValidationError("'source_event_ids' must reference events recorded by your organization")
        srcs = list(dict.fromkeys([*srcs, *(source_event_ids or [])]))
        meta = {k: v for k, v in _small_dict(body, "metadata", 4096).items() if k not in RESERVED_METADATA_KEYS}
        observed_at = _iso(body, "observed_at") or now_iso()
        idem = _s(body, "idempotency_key", max_len=200)
        memory_id = f"mem_{content_hash(principal.id, idem)[:22]}" if idem else new_id("mem")
        return Memory(
            memory_id=memory_id, org_id=principal.org_id or "", layer="agent", scope=principal.path or "",
            text=text or "", topic=_s(body, "topic", max_len=200), slot=_s(body, "slot", max_len=100, pattern=_SLOT_RE),
            entity=_s(body, "entity", max_len=200), kind=kind, confidence=_f(body, "confidence", 0.8), support=1,
            independent_teams=1, producer_id=principal.id, operator="agent_observation", rule_id=None, event_id=None,
            visibility=visibility, status="active", created_at=observed_at, applied_at=None, source_event_ids=srcs,
            local_ref=_s(body, "local_ref", max_len=200), metadata=meta,
        )

    async def ingest_memory(self, principal: Principal, body: dict[str, Any], *, remote: str | None = None) -> tuple[Memory, bool]:
        """Store a memory and append its event to the outbox in one transaction. Returns (memory, created)."""
        if not isinstance(body, dict):
            raise ValidationError("body must be a JSON object")
        m = self._validate_memory_body(principal, body)
        event = EventRecord(event_id=new_id("evt"), kind="memory.observed", org_id=m.org_id, agent_id=principal.id,
                            subject=subject_for(m.org_id, "memory.observed"), payload={}, created_at=now_iso())
        m.event_id = event.event_id
        event.payload = m.to_dict()
        self._check_event_size(event)
        async with self.store.transaction() as tx:
            existing = self.store.get_memory(m.memory_id)
            if existing is not None:
                return existing, False
            tx.insert_memory(m)
            tx.insert_event(event)
            tx.audit(principal.id, "memory.ingest", m.memory_id, {"org_id": m.org_id, "topic": m.topic, "slot": m.slot,
                                                                  "event_id": event.event_id}, remote)
        self.metrics.memories_ingested.labels("agent").inc()
        self._outbox_wake.set()
        return m, True

    async def ingest_events(self, principal: Principal, items: list[dict[str, Any]], *, remote: str | None = None) -> list[dict[str, Any]]:
        if principal.kind != "agent":
            raise Forbidden("only agents can publish events")
        if not principal.has("events:write"):
            raise Forbidden("missing scope events:write")
        if not isinstance(items, list) or not items:
            raise ValidationError("'events' must be a non-empty list")
        if len(items) > self.settings.max_batch:
            raise ValidationError(f"at most {self.settings.max_batch} events per request")
        prepared: list[tuple[EventRecord, Memory | None]] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValidationError(f"events[{i}] must be an object")
            etype = _s(item, "type", required=True, max_len=100, pattern=_ID_RE)
            occurred_at = _iso(item, "occurred_at") or now_iso()
            payload = _small_dict(item, "payload", 16 * 1024)
            idem = _s(item, "idempotency_key", max_len=200)
            event_id = f"evt_{content_hash(principal.id, 'event', idem)[:22]}" if idem else new_id("evt")
            record = EventRecord(event_id=event_id, kind="agent.event", org_id=principal.org_id or "", agent_id=principal.id,
                                 subject=subject_for(principal.org_id or "", "agent.event"),
                                 payload={"type": etype, "occurred_at": occurred_at, "agent_id": principal.id,
                                          "path": principal.path, "payload": payload}, created_at=now_iso())
            mem = None
            if item.get("memory") is not None:
                if not isinstance(item["memory"], dict):
                    raise ValidationError(f"events[{i}].memory must be an object")
                mem = self._validate_memory_body(principal, item["memory"], source_event_ids=[event_id])
                if idem and not item["memory"].get("idempotency_key"):
                    # the event's idempotency key also makes its embedded memory idempotent (domain-separated)
                    mem.memory_id = f"mem_{content_hash(principal.id, 'event-memory', idem)[:22]}"
            self._check_event_size(record)
            prepared.append((record, mem))
        results = []
        async with self.store.transaction() as tx:
            for record, mem in prepared:
                created = tx.insert_event(record)
                entry: dict[str, Any] = {"event_id": record.event_id, "created": created}
                if mem is not None:
                    mev = EventRecord(event_id=new_id("evt"), kind="memory.observed", org_id=mem.org_id, agent_id=principal.id,
                                      subject=subject_for(mem.org_id, "memory.observed"), payload={}, created_at=now_iso())
                    mem.event_id = mev.event_id
                    mev.payload = mem.to_dict()
                    if self.store.get_memory(mem.memory_id) is None:
                        tx.insert_memory(mem)
                        tx.insert_event(mev)
                        self.metrics.memories_ingested.labels("agent").inc()
                        entry["memory_id"] = mem.memory_id
                        entry["memory_event_id"] = mev.event_id
                    else:
                        entry["memory_id"] = mem.memory_id
                        entry["memory_created"] = False
                results.append(entry)
            tx.audit(principal.id, "events.ingest", None, {"org_id": principal.org_id, "count": len(prepared)}, remote)
        self.metrics.events_received.inc(len(prepared))
        self._outbox_wake.set()
        return results

    def _check_event_size(self, ev: EventRecord) -> None:
        size = len(json.dumps(self._event_wire(ev), ensure_ascii=False).encode("utf-8"))
        if size > self.settings.max_event_bytes:
            raise ValidationError(f"event is {size} bytes; the limit is {self.settings.max_event_bytes}")

    async def retract(self, principal: Principal, memory_id: str, reason: str = "retracted by producer", *, remote: str | None = None) -> EventRecord:
        m = self.store.get_memory(memory_id)
        if m is None or not principal.can_read(m):
            raise NotFound(memory_id)
        if not principal.is_admin and m.producer_id != principal.id:
            raise Forbidden("only the producing agent or an administrator can retract a memory")
        if m.operator != "agent_observation":
            raise ValidationError("derived memories are recomputed from their evidence and cannot be retracted directly; "
                                  "retract the contributing observations (the roots in GET /lineage/{id}) instead")
        event = EventRecord(event_id=new_id("evt"), kind="memory.retracted", org_id=m.org_id, agent_id=principal.id,
                            subject=subject_for(m.org_id, "memory.retracted"),
                            payload={"memory_id": memory_id, "reason": reason[:200], "by": principal.id}, created_at=now_iso())
        async with self.store.transaction() as tx:
            tx.insert_event(event)
            tx.audit(principal.id, "memory.retract", memory_id, {"org_id": m.org_id, "reason": reason[:200]}, remote)
        self._outbox_wake.set()
        return event

    # ------------------------------------------------------------------ apply (consumer side)
    async def apply_event(self, event: dict[str, Any], *, seq: int | None = None) -> str:
        """Apply one event from the log. Idempotent: an already-applied event id is a no-op ('duplicate')."""
        event_id = event["event_id"]
        kind = event.get("kind")
        payload = event.get("payload") or {}
        org_id = event.get("org_id") or payload.get("org_id") or ""
        now = now_iso()
        async with self.store.transaction() as tx:
            status = tx.event_status(event_id)
            if status == "applied":
                if seq is not None:
                    tx.mark_applied(event_id, seq, now)
                    self._progress_in_tx(tx, seq)
                return "duplicate"
            if status is None:
                # Not in this database: the event came from the stream (rebuild after data loss, or another writer).
                tx.insert_event(EventRecord(event_id=event_id, kind=kind or "unknown", org_id=org_id, agent_id=event.get("agent_id"),
                                            subject=subject_for(org_id or "unknown", kind or "unknown"), payload=payload,
                                            status="published", js_seq=seq, created_at=event.get("created_at") or now))
            derivations: list[Derivation] = []
            result = "applied"
            if kind == "memory.observed":
                m = self._memory_from_payload(payload)
                m.applied_at = now
                agent = self.store.get_agent(m.producer_id)
                if agent is None or agent.org_id != m.org_id or agent.path != m.scope or m.layer != "agent":
                    # Anything that can publish to the stream could claim to be an agent; the registry is the
                    # authority.  (Registrations travel through the same log ahead of the agent's first memory.)
                    tx.audit("mycelic", "event.rejected", event_id, {"reason": "producer not registered for this scope",
                                                                    "producer_id": m.producer_id, "scope": m.scope})
                    tx.mark_applied(event_id, seq, now)
                    self._progress_in_tx(tx, seq)
                    self.metrics.events_failed.labels("validate").inc()
                    return "rejected"
                if tx.insert_memory(m):
                    self.metrics.memories_ingested.labels("agent")  # replayed memories were counted when first ingested
                else:
                    tx.set_applied(m.memory_id, now)
                    m = self.store.get_memory(m.memory_id) or m
                if m.status == "active":
                    derivations = self.aggregator.derive_for(tx, m)
            elif kind == "memory.derived":
                m = self._memory_from_payload(payload)
                m.applied_at = m.applied_at or now
                if tx.insert_memory(m):
                    edges = [LineageEdge(child_id=m.memory_id, parent_id=p["parent_id"], contributed_by=p["contributed_by"],
                                         parent_layer=p["parent_layer"], created_at=p.get("created_at") or now)
                             for p in payload.get("parents", []) if isinstance(p, dict) and p.get("parent_id")]
                    tx.add_lineage_edges(edges)
                    old = payload.get("supersedes")
                    if isinstance(old, str):
                        prev = self.store.get_memory(old)
                        if prev is not None and prev.status == "active":
                            tx.set_memory_status(old, "superseded", superseded_by=m.memory_id, reason="coalition changed")
                    self.metrics.derived.labels(m.layer, m.operator).inc()
                else:
                    result = "duplicate"
            elif kind == "memory.retracted":
                mid = payload.get("memory_id")
                m = self.store.get_memory(mid) if isinstance(mid, str) else None
                if m is not None and m.status == "active" and m.operator == "agent_observation":
                    tx.set_memory_status(mid, "retracted", reason=str(payload.get("reason") or "retracted"))
                    retired = self.aggregator.retire_dependents(tx, mid, "evidence retracted")
                    derivations = self.aggregator.derive_for(tx, m)
                    # a conclusion that lost one piece of evidence may still hold on the rest: re-evaluate it
                    derivations += self.aggregator.reevaluate(tx, retired)
                else:
                    # derived memories are a function of their evidence: they can only go away with it
                    tx.audit("mycelic", "event.ignored", event_id, {"reason": "retraction target is not an active raw observation"})
                    result = "ignored"
            elif kind == "agent.event":
                pass
            elif kind == "agent.registered":
                agent = self._agent_from_payload(payload)
                if self.store.get_agent(agent.agent_id) is None:
                    tx.insert_agent(agent, str(payload.get("key_hash") or ""))
                else:
                    result = "duplicate"
            elif kind == "agent.revoked":
                tx.set_agent_status(str(payload.get("agent_id")), "revoked")
            elif kind == "agent.key_rotated":
                tx.rotate_agent_key(str(payload.get("agent_id")), str(payload.get("key_hash") or ""), str(payload.get("key_prefix") or ""))
            elif kind == "rule.upserted":
                tx.upsert_rule(self._rule_from_body(payload))
            elif kind == "rule.deleted":
                tx.delete_rule(str(payload.get("rule_id")))
            else:
                result = "ignored"
            for d in derivations:
                dev = EventRecord(event_id=f"evt_d{content_hash(d.memory.memory_id)[:22]}", kind="memory.derived",
                                  org_id=d.memory.org_id, agent_id=None, subject=subject_for(d.memory.org_id, "memory.derived"),
                                  payload={}, created_at=now)
                if self._replay_target is not None:
                    # During a replay the stream already carries the original derived events (they follow the
                    # observations that produced them), so re-deriving must not append duplicates to the log.
                    dev.status = "published"
                    dev.published_at = now
                d.memory.event_id = dev.event_id
                dev.payload = d.event_payload()
                tx.c.execute("UPDATE memories SET event_id=? WHERE memory_id=?", (dev.event_id, d.memory.memory_id))
                tx.insert_event(dev)
                self.metrics.derived.labels(d.memory.layer, d.memory.operator).inc()
            tx.mark_applied(event_id, seq, now)
            self._progress_in_tx(tx, seq)
        if derivations:
            self._outbox_wake.set()
        return result

    @staticmethod
    def _agent_from_payload(p: dict[str, Any]) -> Agent:
        for k in ("agent_id", "org_id", "path"):
            if not isinstance(p.get(k), str) or not p[k]:
                raise ValidationError(f"agent payload lacks '{k}'")
        AgentPath.parse(p["path"])
        if p["agent_id"] == MYCELIC_PRODUCER:
            raise ValidationError(f"agent_id '{MYCELIC_PRODUCER}' is reserved for derived memories")
        raw_scopes = p.get("scopes")
        return Agent(agent_id=p["agent_id"], org_id=p["org_id"], display_name=p.get("display_name") or p["agent_id"],
                     path=p["path"], scopes=list(DEFAULT_AGENT_SCOPES) if raw_scopes is None else list(raw_scopes),
                     key_prefix=p.get("key_prefix") or "",
                     status=p.get("status") or "active", created_at=p.get("created_at") or now_iso(),
                     metadata=dict(p.get("metadata") or {}))

    @staticmethod
    def _memory_from_payload(p: dict[str, Any]) -> Memory:
        required = ("memory_id", "org_id", "layer", "scope", "text")
        for k in required:
            if not isinstance(p.get(k), str) or not p[k]:
                raise ValidationError(f"memory payload lacks '{k}'")
        if p["layer"] not in LAYERS:
            raise ValidationError("memory payload has an unknown layer")
        split_path(p["scope"])
        return Memory(
            memory_id=p["memory_id"], org_id=p["org_id"], layer=p["layer"], scope=p["scope"], text=p["text"],
            topic=p.get("topic"), slot=p.get("slot"), entity=p.get("entity"), kind=p.get("kind") or "observation",
            confidence=float(p.get("confidence", 0.5)), support=int(p.get("support", 1)),
            independent_teams=int(p.get("independent_teams", 1)), producer_id=p.get("producer_id") or "unknown",
            operator=p.get("operator") or "agent_observation", rule_id=p.get("rule_id"), event_id=p.get("event_id"),
            visibility=p.get("visibility") or "team", status=p.get("status") or "active", superseded_by=None,
            created_at=p.get("created_at") or now_iso(), applied_at=p.get("applied_at"),
            source_event_ids=list(p.get("source_event_ids") or []), local_ref=p.get("local_ref"),
            metadata=dict(p.get("metadata") or {}),
        )

    # ------------------------------------------------------------------ reads
    def get_memory(self, principal: Principal, memory_id: str) -> Memory:
        if not principal.has("memory:read"):
            raise Forbidden("missing scope memory:read")
        m = self.store.get_memory(memory_id)
        if m is None or not principal.can_read(m):
            raise NotFound(memory_id)
        return m

    def query(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        if not principal.has("memory:read"):
            raise Forbidden("missing scope memory:read")
        if not isinstance(body, dict):
            raise ValidationError("body must be a JSON object")
        t0 = time.perf_counter()
        text = _s(body, "query", max_len=2000) or _s(body, "text", max_len=2000) or ""
        scope = _s(body, "scope", max_len=400)
        org_id = principal.org_id
        if scope:
            try:
                split_path(scope)
            except HierarchyError as exc:
                raise ValidationError(str(exc)) from exc
            if principal.is_admin:
                org_id = scope.split("/", 1)[0]
            elif not principal.can_query_scope(scope):
                raise Forbidden("scope is outside the units this agent belongs to")
        else:
            scope = principal.org_id
            if principal.is_admin:
                org_id = _s(body, "org_id", max_len=64)
                if not org_id:
                    raise ValidationError("administrators must pass 'scope' or 'org_id'")
                scope = org_id
        min_layer = _s(body, "min_layer", max_len=20) or "agent"
        layer_index(min_layer)
        k = body.get("k", 10)
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
            raise ValidationError("'k' must be an integer between 1 and 100")
        include_lineage = bool(body.get("include_lineage", True)) and principal.has("lineage:read")
        hits = self.retriever.search(org_id or "", text, visible=principal.can_read, scope=scope, min_layer=min_layer, k=k,
                                     topic=_s(body, "topic", max_len=200), entity=_s(body, "entity", max_len=200))
        answer = None
        lineage = None
        if hits:
            top = hits[0].memory
            summary = self._lineage_summary(principal, top)
            answer = {"memory_id": top.memory_id, "text": top.text, "layer": top.layer, "scope": top.scope,
                      "confidence": top.confidence, "support": top.support, "independent_teams": top.independent_teams,
                      "operator": top.operator, "rule_id": top.rule_id, "created_at": top.created_at, "lineage": summary}
            if include_lineage:
                lineage = self.lineage(principal, top.memory_id)
        latency = time.perf_counter() - t0
        self.metrics.retrieval_latency.observe(latency)
        return {"query": text, "scope": scope, "min_layer": min_layer, "answer": answer,
                "results": [h.to_dict() for h in hits], "lineage": lineage, "latency_ms": round(latency * 1000, 2)}

    def _lineage_summary(self, principal: Principal, m: Memory) -> dict[str, Any]:
        try:
            g = reconstruct(self.store, m.memory_id, visible=principal.can_read, full=principal.owns)
        except LineageNotFound:
            return {"available": False}
        return {"available": True, "contributing_agents": len(g["contributing_agents"]) + g["redacted_contributions"],
                "contributing_teams": len(g["contributing_teams"]), "roots": len(g["roots"]), "layers": g["layers"],
                "reconstructable": g["evidence"]["reconstructable"], "first_observed_at": g["timeline"]["first_observed_at"]}

    def lineage(self, principal: Principal, memory_id: str) -> dict[str, Any]:
        if not principal.has("lineage:read"):
            raise Forbidden("missing scope lineage:read")
        m = self.store.get_memory(memory_id)
        if m is None or not principal.can_read(m):
            self.metrics.lineage_results.labels("not_found").inc()
            raise NotFound(memory_id)
        t0 = time.perf_counter()
        try:
            g = reconstruct(self.store, memory_id, visible=principal.can_read, full=principal.owns)
        except Exception:
            self.metrics.lineage_results.labels("failure").inc()
            raise
        self.metrics.lineage_latency.observe(time.perf_counter() - t0)
        self.metrics.lineage_results.labels("success" if g["evidence"]["reconstructable"] else "incomplete").inc()
        return g

    def list_memories(self, principal: Principal, *, scope: str | None = None, layers: list[str] | None = None,
                      limit: int = 50, status: str = "active") -> list[Memory]:
        if not principal.has("memory:read"):
            raise Forbidden("missing scope memory:read")
        org_id = principal.org_id
        if scope:
            split_path(scope)
            if principal.is_admin:
                org_id = scope.split("/", 1)[0]
            elif not principal.can_query_scope(scope):
                raise Forbidden("scope is outside the units this agent belongs to")
        if org_id is None:
            raise ValidationError("administrators must pass 'scope'")
        rows = self.store.visible_rows(org_id, agent_path=principal.path, team_path=principal.team_path, scope=scope,
                                       statuses=(status,))
        if layers:
            rows = [m for m in rows if m.layer in layers]
        rows.sort(key=lambda m: m.created_at, reverse=True)
        return rows[: max(1, min(500, limit))]

    # ------------------------------------------------------------------ admin
    # Registrations, revocations, key rotations and rules are written to the database *and* appended to the
    # event log in the same transaction, so a database rebuilt from the stream has its agents and rules back
    # (the stream carries key hashes, never keys).  ``apply`` treats these events as idempotent upserts.
    def _admin_event(self, kind: str, org_id: str, payload: dict[str, Any]) -> EventRecord:
        return EventRecord(event_id=new_id("evt"), kind=kind, org_id=org_id, agent_id=None,
                           subject=subject_for(org_id, kind), payload=payload, created_at=now_iso())

    async def register_agent(self, body: dict[str, Any], *, remote: str | None = None) -> tuple[Agent, str]:
        if not isinstance(body, dict):
            raise ValidationError("body must be a JSON object")
        try:
            ap = AgentPath.from_parts(
                enterprise=_s(body, "enterprise", max_len=64) or _s(body, "org_id", max_len=64) or "",
                region=_s(body, "region", max_len=64), subsidiary=_s(body, "subsidiary", max_len=64),
                department=_s(body, "department", max_len=64), team=_s(body, "team", max_len=64),
                agent_id=_s(body, "agent_id", required=True, max_len=64) or "")
        except HierarchyError as exc:
            raise ValidationError(str(exc)) from exc
        if ap.agent_id == MYCELIC_PRODUCER:
            raise ValidationError(f"agent_id '{MYCELIC_PRODUCER}' is reserved for derived memories")
        scopes = body.get("scopes")
        if scopes is None:
            scopes = list(DEFAULT_AGENT_SCOPES)
        if not isinstance(scopes, list) or any(s not in ALL_SCOPES or s == "admin" for s in scopes):
            raise ValidationError(f"'scopes' must be a subset of {[s for s in ALL_SCOPES if s != 'admin']}")
        display = _s(body, "display_name", max_len=120) or ap.agent_id
        key, key_hash, prefix = generate_api_key(ap.agent_id)
        agent = Agent(agent_id=ap.agent_id, org_id=ap.org_id, display_name=display, path=ap.path, scopes=list(scopes),
                      key_prefix=prefix, status="active", created_at=now_iso(), metadata=_small_dict(body, "metadata", 2048))
        event = self._admin_event("agent.registered", agent.org_id, {**agent.to_dict(), "key_hash": key_hash})
        async with self.store.transaction() as tx:
            if self.store.get_agent(ap.agent_id) is not None:
                raise ValidationError(f"agent '{ap.agent_id}' already exists")
            tx.insert_agent(agent, key_hash)
            tx.insert_event(event)
            tx.audit("admin", "agent.register", agent.agent_id, {"org_id": agent.org_id, "path": agent.path, "scopes": scopes}, remote)
        self._outbox_wake.set()
        return agent, key

    async def revoke_agent(self, agent_id: str, *, remote: str | None = None) -> bool:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            return False
        async with self.store.transaction() as tx:
            ok = tx.set_agent_status(agent_id, "revoked")
            if ok:
                tx.insert_event(self._admin_event("agent.revoked", agent.org_id, {"agent_id": agent_id}))
                tx.audit("admin", "agent.revoke", agent_id, {"org_id": agent.org_id}, remote)
        self._outbox_wake.set()
        return ok

    async def rotate_agent_key(self, agent_id: str, *, remote: str | None = None) -> str | None:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            return None
        key, key_hash, prefix = generate_api_key(agent_id)
        async with self.store.transaction() as tx:
            tx.rotate_agent_key(agent_id, key_hash, prefix)
            tx.insert_event(self._admin_event("agent.key_rotated", agent.org_id,
                                              {"agent_id": agent_id, "key_hash": key_hash, "key_prefix": prefix}))
            tx.audit("admin", "agent.rotate_key", agent_id, {"org_id": agent.org_id}, remote)
        self._outbox_wake.set()
        return key

    def _rule_from_body(self, body: dict[str, Any]) -> Rule:
        if not isinstance(body, dict):
            raise ValidationError("rule must be an object")
        rule_id = _s(body, "rule_id", required=True, max_len=100, pattern=_ID_RE) or ""
        target = _s(body, "target_layer", required=True, max_len=20) or ""
        if target not in LAYERS[1:]:
            raise ValidationError(f"'target_layer' must be one of {LAYERS[1:]}")
        slots = body.get("required_slots")
        if not isinstance(slots, list) or not slots or any(not isinstance(s, str) or not _SLOT_RE.match(s) for s in slots):
            raise ValidationError("'required_slots' must be a non-empty list of slot names")
        if len(dict.fromkeys(slots)) > 6:
            raise ValidationError("'required_slots' may name at most 6 slots")
        conclusion = _s(body, "conclusion", required=True, max_len=2000) or ""
        kind = _s(body, "kind", max_len=40) or "risk"
        if kind not in MEMORY_KINDS:
            raise ValidationError(f"'kind' must be one of {MEMORY_KINDS}")
        min_agents = body.get("min_agents", 2)
        min_teams = body.get("min_teams", 1)
        for name, v in (("min_agents", min_agents), ("min_teams", min_teams)):
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValidationError(f"'{name}' must be a positive integer")
        sources = body.get("sources", ["agent_observation"])
        if not isinstance(sources, list) or not sources or any(s not in OPERATORS for s in sources):
            raise ValidationError(f"'sources' must be a non-empty subset of {OPERATORS}")
        emits_slot = _s(body, "emits_slot", max_len=100, pattern=_SLOT_RE)
        if emits_slot and emits_slot in slots:
            raise ValidationError("'emits_slot' must not be one of the rule's own required slots")
        min_units = body.get("min_units") or {}
        ok = isinstance(min_units, dict) and all(
            slot in slots and isinstance(per, dict) and per and all(
                layer in LAYERS[1:] and not isinstance(n, bool) and isinstance(n, int) and n >= 1 for layer, n in per.items())
            for slot, per in min_units.items())
        if not ok:
            raise ValidationError(f"'min_units' must map required slots to {{layer: positive integer}} with layers in {LAYERS[1:]}")
        return Rule(rule_id=rule_id, target_layer=target, required_slots=list(dict.fromkeys(slots)), conclusion=conclusion,
                    topic_prefix=_s(body, "topic_prefix", max_len=200), min_agents=min_agents, min_teams=min_teams, kind=kind,
                    org_id=_s(body, "org_id", max_len=64), enabled=bool(body.get("enabled", True)),
                    metadata=_small_dict(body, "metadata", 2048), sources=list(dict.fromkeys(sources)), emits_slot=emits_slot,
                    emits_topic=_s(body, "emits_topic", max_len=200),
                    min_units={slot: {layer: int(n) for layer, n in per.items()} for slot, per in min_units.items()},
                    corroborate=bool(body.get("corroborate", False)))

    @staticmethod
    def _rule_feeds(p: Rule, c: Rule) -> bool:
        """Could a conclusion of ``p`` be evidence for ``c``?"""
        if not p.emits_slot or p.emits_slot not in c.required_slots:
            return False
        if not ({"slot_composition", "topic_consolidation"} & set(c.sources)):
            return False
        if LAYERS.index(p.target_layer) > LAYERS.index(c.target_layer):
            return False
        if p.org_id and c.org_id and p.org_id != c.org_id:
            return False
        topic = p.emits_topic or p.topic_prefix or p.rule_id
        return not c.topic_prefix or topic.startswith(c.topic_prefix)

    def _check_rule_cycles(self, new: Rule) -> None:
        """Refuse a rule that would (transitively) feed on its own conclusions; the aggregator also guards at apply time."""
        if not new.enabled:
            return
        graph = {r.rule_id: r for r in self.store.list_rules(new.org_id, enabled_only=False) if r.enabled and r.rule_id != new.rule_id}
        graph[new.rule_id] = new
        stack, seen = [new.rule_id], set()
        while stack:
            rid = stack.pop()
            for other in graph.values():
                if self._rule_feeds(graph[rid], other):
                    if other.rule_id == new.rule_id:
                        raise ValidationError(f"rule '{new.rule_id}' would form a cycle through slot '{graph[rid].emits_slot}' with '{rid}'")
                    if other.rule_id not in seen:
                        seen.add(other.rule_id)
                        stack.append(other.rule_id)

    async def upsert_rule(self, body: dict[str, Any], *, remote: str | None = None) -> Rule:
        rule = self._rule_from_body(body)
        self._check_rule_cycles(rule)
        async with self.store.transaction() as tx:
            tx.upsert_rule(rule)
            tx.insert_event(self._admin_event("rule.upserted", rule.org_id or "_", rule.to_dict()))
            tx.audit("admin", "rule.upsert", rule.rule_id, {"target_layer": rule.target_layer, "slots": rule.required_slots}, remote)
        self._outbox_wake.set()
        return rule

    async def delete_rule(self, rule_id: str, *, remote: str | None = None) -> bool:
        rule = self.store.get_rule(rule_id)
        if rule is None:
            return False
        async with self.store.transaction() as tx:
            ok = tx.delete_rule(rule_id)
            if ok:
                tx.insert_event(self._admin_event("rule.deleted", rule.org_id or "_", {"rule_id": rule_id}))
                tx.audit("admin", "rule.delete", rule_id, {}, remote)
        self._outbox_wake.set()
        return ok

    async def replay(self, *, remote: str | None = None) -> dict[str, Any]:
        """Re-deliver the whole log to this instance. Applying is idempotent; missing derived state is rebuilt."""
        info = await self.transport.info()
        await self.transport.reset_consumer()
        await self._set_replay_target(info.get("last_seq") or info.get("stream_messages") or None)
        self.metrics.recoveries.labels("replay_requested").inc()
        await self.store.audit("admin", "replay", None, {"target_seq": self._replay_target}, remote)
        return {"replaying": True, "target_seq": self._replay_target}

    # ------------------------------------------------------------------ health
    async def health(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        status = "ok"
        try:
            stats = self.store.stats()
            checks["db"] = {"ok": True, "path": self.store.db_path}
        except Exception as exc:
            stats = {}
            checks["db"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            status = "failing"
        try:
            tinfo = await self.transport.info()
        except Exception as exc:
            tinfo = {"connected": False, "error": f"{type(exc).__name__}: {exc}"}
        checks["transport"] = tinfo
        checks["publisher"] = {"running": self._publisher_running, "outbox_pending": stats.get("outbox_pending")}
        checks["consumer"] = {"running": self._consumer_running, "last_applied_seq": stats.get("last_applied_seq"),
                              "pending": tinfo.get("consumer_pending"), "replaying_to_seq": self._replay_target}
        if status == "ok" and (not tinfo.get("connected") or not self._consumer_running or not self._publisher_running):
            status = "degraded"
        if self.metrics:
            self.metrics.refresh_from_stats(stats)
        return {"status": status, "version": VERSION, "instance": self.settings.instance_id, "started_at": self.started_at,
                "checks": checks, "stats": stats}

    async def ready(self) -> tuple[bool, dict[str, Any]]:
        """Readiness: database open, background loops running, no replay in progress.

        A broker outage does *not* make the service unready by default: the outbox exists precisely so agents can
        keep writing through one.  Set ``MYCELIC_READY_REQUIRES_NATS=true`` to change that.
        """
        h = await self.health()
        ok = h["checks"]["db"]["ok"] and self._consumer_running and self._publisher_running and self._replay_target is None
        if self.settings.ready_requires_nats and not h["checks"]["transport"].get("connected"):
            ok = False
        return ok, {"ready": ok, "status": h["status"], "replaying_to_seq": self._replay_target,
                    "transport_connected": bool(h["checks"]["transport"].get("connected"))}

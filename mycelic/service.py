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
import json
import logging
import math
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from . import verification
from .aggregation import MYCELIC_PRODUCER, Aggregator, Derivation
from .auth import Authenticator, Principal, RateLimiter, generate_api_key
from .config import Settings
from .hierarchy import AgentPath, HierarchyError, LAYERS, layer_index, split_path, validate_segment
from .integrity import Keyring
from .lineage import LineageNotFound, reconstruct
from .metrics import Metrics
from .models import (
    ALL_SCOPES, DEFAULT_AGENT_SCOPES, DERIVATION_VERSION, MAX_VALUE_CHARS, MEMORY_KINDS, OPERATORS, VISIBILITY, Agent,
    EventRecord, Memory, Rule, canonical_label, canonical_rule_body, canonical_value, content_hash, new_id, now_iso, parse_iso,
    utc_seconds, utcnow,
)
from .retrieval import Retriever
from .store import MycelicStore, Tx, acquire_db_lock, release_db_lock
from .transport import Transport, build_transport, subject_for
from .version import VERSION

logger = logging.getLogger(__name__)

#: metadata keys the aggregator owns; an agent may not set them on a raw observation
RESERVED_METADATA_KEYS = frozenset({"agg_key", "promoted_from", "version_of", "contributing_agents", "contributing_teams",
                                    "children", "child_layer", "parent_count", "fragility", "slots", "candidates",
                                    "effective_min_support", "registered_child_units", "status_reason", "reactivated_at",
                                    "roots", "evidence", "corroborated_units", "rule_chain", "fragility_scored_candidates",
                                    "derivation", "statements", "statement_origins", "private_observations"})
_SLOT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
#: meta key counting the derived events a replay ignored (summarised in one audit row when the replay completes)
_REPLAY_IGNORED = "replay_ignored_derived"
#: meta keys counting the deliveries a replay consumed and those of them whose signature it rejected, written in the
#: transaction that consumed each one and judged when the replay completes (``_finish_replay_in_tx``)
_REPLAY_SEEN = "replay_seen"
_REPLAY_SIG_REJECTED = "replay_sig_rejected"
#: meta key of the readiness block a replay over MYCELIC_REPLAY_MAX_REJECT_RATIO writes; the service never deletes it
_READY_BLOCK = "ready_block"
READY_BLOCK_REASON = "signature_rejections: rebuild with the corrected keyring required"
_BLOCK_REMEDY = ("Stop the service, add the key that signed those events to MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS (keeping the "
                 "current key), move the database aside and start the service so it rebuilds from the stream (DEPLOYMENT.md "
                 "section 4, 'Signing-key mistakes'); if the stream really holds that many forged events (each audited as "
                 "event.rejected), raise MYCELIC_REPLAY_MAX_REJECT_RATIO to at least the recorded ratio and restart instead.")


def _recorded_ratio(rejected: int, seen: int) -> float:
    """``rejected / seen`` rounded *up* to 6 decimal places, in integers (so 83/160 stays 0.51875): the ratio a replay
    records and /admin/status shows.  The block is judged on the exact quotient, so MYCELIC_REPLAY_MAX_REJECT_RATIO set to
    this value, as written, lifts it, where a ratio rounded down (2/7 to 0.285714) would not."""
    return -(-rejected * 10**6 // seen) / 10**6
#: a verification costs one rate-limit token per this many nodes walked, on top of its request's own token: at about
#: 0.15 ms per node (verification.py, Cost) a token buys about 37 ms of walk
VERIFY_NODES_PER_TOKEN = 250
#: and at least this many tokens per ``1 / rps`` seconds the walk took, so a principal that keeps walking is charged
#: twice what its bucket refills meanwhile and is in debt (refused) after its first walk past solvency, whatever the
#: walk's size per node
VERIFY_TIME_PRICE = 2.0
#: how far ahead a note's ``expires_at`` may be (ten years, the bound of ``max_leaf_age``)
MAX_EXPIRY_SECONDS = verification.MAX_LEAF_AGE_SECONDS


class ValidationError(ValueError):
    """Bad client input; the API maps it to 400 with the message."""


class Forbidden(PermissionError):
    pass


class NotFound(KeyError):
    pass


class Conflict(Exception):
    """A request that cannot be applied yet because an earlier one on the same memory is still on its way through the
    log; the API maps it to 409 and MCP to an error result."""


class RateLimited(Exception):
    """A principal whose verifications put its rate-limit bucket in debt; the API maps it to 429 and MCP to an error
    result."""


class QuotaExceeded(Exception):
    """A write that would take its organization past ``MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG`` active raw notes; the API
    maps it to 507 and MCP to an error result."""


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


def _label(body: dict[str, Any], key: str, *, max_len: int, pattern: re.Pattern | None = None) -> str | None:
    """A topic, slot or entity in its canonical spelling (``models.canonical_label``); length and charset are
    checked on the canonical form, which is what is stored and compared."""
    v = canonical_label(_s(body, key, max_len=max_len))
    if v is not None and len(v) > max_len:
        raise ValidationError(f"'{key}' is longer than {max_len} characters")
    if pattern and v and not pattern.match(v):
        raise ValidationError(f"'{key}' contains characters outside {pattern.pattern}")
    return v


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


def _expiry(body: dict[str, Any]) -> str | None:
    """``expires_at`` in UTC with second precision, or None (absent or empty).  Syntax only: :func:`_check_expiry` checks
    the window when the note is about to be stored."""
    v = _s(body, "expires_at", max_len=40)
    if v is None:
        return None
    out = utc_seconds(v)
    if out is None:
        raise ValidationError("'expires_at' must be an ISO-8601 timestamp")
    return out


def _check_expiry(expires_at: str | None, now: datetime) -> None:
    """A new note's expiry, as stored, must lie in the future and at most :data:`MAX_EXPIRY_SECONDS` ahead."""
    at = parse_iso(expires_at)
    if at is None:
        return
    if at <= now:
        raise ValidationError("'expires_at' must be in the future")
    if (at - now).total_seconds() > MAX_EXPIRY_SECONDS:
        raise ValidationError(f"'expires_at' must be at most {MAX_EXPIRY_SECONDS} seconds (ten years) ahead")


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
    #: how often the status task refreshes what /health and /ready report (seconds)
    status_interval = 2.0
    #: a broker call slower than this leaves the previous status in place, marked stale (seconds)
    status_timeout = 2.0
    #: items one re-aggregation step reconciles, in one transaction (see ``Aggregator.reaggregate_step``): 5 keeps a
    #: step's work under 0.3 s at 5,000 notes in one organization (10 took up to 0.5 s, 50 up to 1.3 s); its commit
    #: comes on top and can stall on a slow fsync
    reaggregate_batch = 5
    #: rows one start-up backfill batch signs, in one transaction (see ``_backfill_integrity``): 500 took at most 0.11 s
    #: (median 0.06 s) on 22,685 rows whose consolidations rest on up to 400 roots, about 7,900 rows/s with its commits
    #: (1,000 took up to 0.17 s); keep a batch under 0.25 s
    backfill_batch = 500
    #: expired notes one sweep queues a retraction for, in one transaction (``sweep_expired``): at the default
    #: MYCELIC_EXPIRY_SWEEP_SECONDS of 30, 200 a minute
    expiry_batch = 100
    #: downward verifications that walk at once, all callers together, each in a worker thread on its own read snapshot
    #: (one more per caller waits for that caller's previous walk, the rest queue)
    verify_concurrency = 2

    def __init__(self, settings: Settings, *, store: MycelicStore | None = None, transport: Transport | None = None,
                 metrics: Metrics | None = None) -> None:
        self.settings = settings
        self.metrics = metrics or Metrics()
        self.keyring = Keyring.from_settings(settings)
        if store is None:
            # one writer per database file, refused before anything touches the schema; an injected store is the
            # caller's business (tests rebuilding into ':memory:')
            fd = acquire_db_lock(settings.db_path)
            try:
                store = MycelicStore(settings.db_path, lock_fd=fd)
            except BaseException:
                release_db_lock(fd)
                raise
        self.store = store
        self.store.keyring = self.keyring           # memory digests are signed by the insert, with the event key
        self.transport = transport or build_transport(settings, on_state_change=self._transport_state)
        self.aggregator = Aggregator(self.store, min_support=settings.min_support, on_event=self._aggregation_event)
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
        # what /health and /ready answer from, refreshed by the status task: probes never wait on the broker or the db
        self._transport_info: dict[str, Any] = {}
        self._stats: dict[str, Any] = {}
        self._status_at: float | None = None          # monotonic time of the last refresh whose transport call succeeded
        self._status_ok = False
        self._db_error: str | None = None
        self._closed = False
        self._reaggregation: dict[str, Any] = {"state": "idle"}
        self._reaggregate_task: asyncio.Task | None = None
        self._expiry_cursor: tuple[str, int] | None = None      # (expires_at, rid) after which the next sweep reads
        self._expiry: dict[str, Any] = {"last_sweep_at": None, "last_queued": None}
        self._replay_judgement: tuple[int, int, bool] | None = None   # (seen, rejected, blocked) until its commit is noted
        self._publish_error: tuple[str, float] | None = None           # (last error, monotonic time of the first) while failing
        # the database has not been judged against the stream since a failed recovery (a broker that could not report its
        # stream, a delivery the database may not have recorded): the consumer does so before it fetches again
        self._resync_pending = False
        self._verify_slots = asyncio.Semaphore(self.verify_concurrency)
        self._verify_callers: dict[str, list[Any]] = {}                # limiter key -> [lock, verifications holding or awaiting it]
        # read here and after every replay completes, so /ready answers from memory and the block survives a restart
        self._ready_block = self._load_ready_block()
        self.metrics.info.labels(VERSION).set(1)

    # ------------------------------------------------------------------ lifecycle
    async def start(self, *, background: bool = True) -> None:
        self.started_at = now_iso()
        self._stop.clear()
        self.load_rules_file()
        if self._reaggregate_reason() is not None and not self.store.has_memories():
            # nothing derived to converge, and a replay into this database derives with this code and configuration
            async with self.store.transaction() as tx:
                self._record_derivation_meta(tx)
        try:
            pruned = await self.store.prune_audit(self.settings.audit_retention_days)
            if pruned:
                logger.info("pruned %d audit rows older than %d days", pruned, self.settings.audit_retention_days)
        except Exception:
            logger.exception("audit pruning failed")
        await self._note_signing_keys()
        if self._ready_block is not None:
            logger.error("readiness is blocked: an earlier replay into this database rejected the signature of %s of the %s "
                         "events it consumed (recorded ratio %s, %s): /ready stays 503. %s", self._ready_block.get("rejected", "?"),
                         self._ready_block.get("seen", "?"), self._ready_block.get("ratio", "?"),
                         self._ready_block.get("at", "time unknown"), _BLOCK_REMEDY)
        await self._backfill_integrity()              # before the loops: /health answers meanwhile, /ready does not
        await self._resign_lifecycle()
        await self._connect_with_retry(first=True)
        await self.refresh_status()                   # the first /ready already answers from a warm snapshot
        if background:
            self._tasks = [asyncio.create_task(self._transport_keeper(), name="mycelic-transport"),
                           asyncio.create_task(self._publisher_loop(), name="mycelic-publisher"),
                           asyncio.create_task(self._consumer_loop(), name="mycelic-consumer"),
                           asyncio.create_task(self._status_loop(), name="mycelic-status")]
            if self.settings.expiry_sweep_seconds > 0:
                self._tasks.append(asyncio.create_task(self._expiry_loop(), name="mycelic-expiry"))
            reason = self._reaggregate_reason()
            if reason is not None:
                # derived state from an older release, another MIN_SUPPORT or before a migration: converge it in the
                # background (it waits for a replay that recovery just started)
                self._start_reaggregation(None, reason)
        logger.info("mycelic %s started (db=%s, transport=%s)", VERSION, self.store.db_path, self.transport.name)

    async def stop(self) -> None:
        """Stop the loops and the transport within ``shutdown_timeout_seconds`` (plus at least 0.5 s for the transport).

        Cutting either step short loses nothing: an event is acked only after the transaction that applied it
        committed, the outbox is durable, a cancelled apply rolls back (``Tx`` rolls back on any BaseException),
        and whatever was fetched but not acked is redelivered and applied as an idempotent duplicate (a publish
        cancelled before ``mark_published`` is deduplicated by its ``Nats-Msg-Id``).
        """
        deadline = time.monotonic() + self.settings.shutdown_timeout_seconds
        self._stop.set()
        self._outbox_wake.set()
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
            for t in done:
                if not t.cancelled():
                    t.exception()                     # retrieved, so asyncio does not log it at exit
            for t in pending:
                logger.warning("task %s did not stop within the shutdown budget", t.get_name())
        remaining = max(0.5, deadline - time.monotonic())
        try:
            await asyncio.wait_for(self.transport.close(), timeout=remaining)
        except asyncio.TimeoutError:
            logger.warning("transport did not close within %.1fs; dropping the connection (unacked events are redelivered)", remaining)
            abort = getattr(self.transport, "abort", None)
            if abort is not None:
                abort()

    async def close(self) -> None:
        """Stop, then close the store (which releases the database lock). A second call does nothing."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.stop()
        finally:
            await self.store.close()

    def _aggregation_event(self, name: str, labels: dict[str, str]) -> None:
        if name == "inconsistency":
            self.metrics.aggregation_inconsistency.labels(labels["kind"]).inc()
        elif name == "truncated":
            self.metrics.aggregation_truncated.labels(labels["what"]).inc()

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
        try:
            await self._recover_if_needed()
        except Exception as exc:
            logger.warning("could not bring the database in step with the stream yet (%s: %s); the consumer does so "
                           "before it fetches", type(exc).__name__, exc)
            self._resync_pending = True
        return True

    async def _note_signing_keys(self) -> None:
        """Record the current key id in ``meta.known_key_ids`` (first-use order) and warn about every key recorded at an
        earlier start that is no longer configured: the rows it signed read ``unknown_key`` and the events it signed are
        rejected until it is listed again.  A key retired before this release first started here is never recorded."""
        try:
            known = json.loads(self.store.get_meta("known_key_ids") or "[]")
        except json.JSONDecodeError:
            known = []
        known = [k for k in known if isinstance(k, str)] if isinstance(known, list) else []
        if self.keyring.keyed and self.keyring.key_id not in known:
            known.append(self.keyring.key_id)
            async with self.store.transaction() as tx:
                tx.set_meta("known_key_ids", json.dumps(known))
        missing = [k for k in known if k not in self.keyring.key_ids]
        if missing:
            logger.warning("signing keys %s were current at an earlier start of this database but are not configured: memories "
                           "they signed read unknown_key and events they signed are rejected; add the keys to "
                           "MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS", ", ".join(missing))

    async def _backfill_integrity(self) -> int:
        """Give every memory written before schema 4 a digest (origin ``backfill``), ``backfill_batch`` rows per
        transaction, yielding between batches; returns the rows signed.

        Runs once: ``meta.integrity_backfill_complete`` ends it for good.  It never signs a row after the first row that
        still carries a digest signed at insert (``store.backfill_bound``): such a row had its digest removed, and
        signing it would turn an edit into a pass.  The bound and the flag live in the database, so someone who can
        write to it can still have a row re-signed, by removing the flag and the digests of every row up to that one;
        what they cannot avoid is this backfill, which is logged, audited and counted.  Only the start that completes
        the upgrade from schema 3 or earlier signs rows legitimately (SECURITY.md §3).  A cancelled or failed batch
        rolls back and the next start resumes from the rows still without a digest.
        """
        if self.store.get_meta("integrity_backfill_complete") == "1":
            return 0
        bound = self.store.backfill_bound()
        signed = batches = refused = 0
        after = 0
        while True:
            async with self.store.transaction() as tx:
                n, after = tx.backfill_digests(after_rid=after, below_rid=bound, limit=self.backfill_batch)
                signed += n
                batches += 1
                last = n < self.backfill_batch
                if last:
                    refused = self.store.count_unsigned(bound) if bound is not None else 0
                    tx.set_meta("integrity_backfill_complete", "1")
                    if signed + refused:
                        tx.audit("mycelic", "integrity.backfill", None, {"rows": signed, "batches": batches, "refused": refused,
                                                                        "key_id": self.keyring.key_id})
            self.metrics.integrity_backfilled.inc(n)
            if last:
                break
            await asyncio.sleep(0)
        if signed:
            logger.warning("signed %d memories that had no digest (origin backfill, key %s): expected once, when the backfill "
                           "after an upgrade from schema 3 or earlier completes; at any other time digests were removed from "
                           "the database", signed, self.keyring.key_id)
        if refused:
            logger.error("%d memories written after digests were introduced have no digest; they are not re-signed: digests "
                         "were removed from the database", refused)
        return signed

    async def _resign_lifecycle(self) -> int:
        """The upgrade to schema 6, whose digests cover a row's lifecycle (status, ``superseded_by``): sign again every
        row that existed before it and is not active or is superseded, ``backfill_batch`` rows per transaction, each only
        if its digest checks in the form it was signed in (``Tx.resign_lifecycle``); returns the rows signed.

        Runs once: the migration records ``meta.lifecycle_resign_below`` and the last batch deletes it.  Like the
        backfill (:meth:`_backfill_integrity`), someone who can write the database could record it again and have rows
        re-signed at the next start; what they cannot avoid is this step, which is logged and audited
        (``integrity.lifecycle_resign``), so any such row after the upgrade's own means the database was edited."""
        below = self.store.get_meta("lifecycle_resign_below")
        if below is None:
            return 0
        signed = left = batches = 0
        after = 0
        while True:
            async with self.store.transaction() as tx:
                start = after
                n, bad, after = tx.resign_lifecycle(after_rid=after, below_rid=int(below), limit=self.backfill_batch)
                signed += n
                left += bad
                batches += 1
                last = after == start              # no row after the previous batch is left to look at
                if last:
                    tx.delete_meta("lifecycle_resign_below")
                    tx.audit("mycelic", "integrity.lifecycle_resign", None, {"rows": signed, "left": left, "batches": batches,
                                                                            "key_id": self.keyring.key_id})
            if last:
                break
            await asyncio.sleep(0)
        if signed:
            logger.warning("signed the lifecycle of %d memories that were not active before the upgrade to schema 6 (key %s): "
                           "expected once, at that upgrade; at any other time the database was edited", signed,
                           self.keyring.key_id)
        if left:
            logger.error("%d memories that were not active before the upgrade to schema 6 do not match their digest and were "
                         "not signed again: they were edited before the upgrade", left)
        return signed

    async def _set_replay_target(self, target: int | None) -> None:
        """Remember how far a replay must go, in memory and in the database, so a crash mid-rebuild resumes it.  A new
        target starts the replay's delivery counts at zero; None ends the replay and judges them."""
        self._replay_target = target
        async with self.store.transaction() as tx:
            if target is None:
                tx.delete_meta("replay_target_seq")
                self._finish_replay_in_tx(tx)
            else:
                tx.set_meta("replay_target_seq", str(target))
                tx.set_meta(_REPLAY_SEEN, "0")
                tx.set_meta(_REPLAY_SIG_REJECTED, "0")
        self._note_replay_judgement()

    def _summarise_replay_in_tx(self, tx: Tx) -> None:
        """One audit row for the derived events a replay ignored, instead of one per event."""
        count = self.store.get_meta(_REPLAY_IGNORED)
        if count is not None:
            tx.audit("mycelic", "event.ignored", None, {"reason": "derived_not_reproduced", "count": int(count), "replay": True})
            tx.delete_meta(_REPLAY_IGNORED)

    def _finish_replay_in_tx(self, tx: Tx, *, judge: bool = True) -> None:
        """In the transaction that ends a replay: summarise the derived events it ignored and judge its deliveries.

        When more than MYCELIC_REPLAY_MAX_REJECT_RATIO of the deliveries it consumed had their signature rejected (the
        wrong key, or a key missing from the keyring), everything those events carried is missing from this database, so
        ``meta.ready_block`` is written and /ready stays 503: a rebuild with the wrong key never passes for a good one.
        Only a rebuild into a fresh database, or a ratio raised to the recorded one and a restart, lifts it; nothing here
        deletes it.  ``judge`` False (a replay that can never complete) discards the counts.  The judgement is stashed
        for :meth:`_note_replay_judgement`, which runs after the commit (a later end of the same replay, after a failed
        ack, finds no counts and leaves it for that)."""
        self._summarise_replay_in_tx(tx)
        seen = int(self.store.get_meta(_REPLAY_SEEN) or 0)
        rejected = int(self.store.get_meta(_REPLAY_SIG_REJECTED) or 0)
        tx.delete_meta(_REPLAY_SEEN)
        tx.delete_meta(_REPLAY_SIG_REJECTED)
        if not judge or seen <= 0 or rejected <= 0:
            return
        ratio = rejected / seen
        blocked = ratio > self.settings.replay_max_reject_ratio
        tx.audit("mycelic", "recovery.signature_rejections", None, {"seen": seen, "rejected": rejected,
                                                                    "ratio": _recorded_ratio(rejected, seen),
                                                                    "max_ratio": self.settings.replay_max_reject_ratio,
                                                                    "blocked": blocked})
        if blocked:
            tx.set_meta(_READY_BLOCK, json.dumps({"reason": "signature_rejections", "seen": seen, "rejected": rejected,
                                                  "at": now_iso()}))
        self._replay_judgement = (seen, rejected, blocked)

    def _note_replay_judgement(self) -> None:
        """After the commit that ended a replay: count and log its judgement, and read the readiness block again."""
        judged, self._replay_judgement = self._replay_judgement, None
        if judged is not None:
            seen, rejected, blocked = judged
            if blocked:
                self.metrics.recoveries.labels("replay_signature_rejections").inc()
                logger.error("the replay rejected the signature of %d of the %d events it consumed (recorded ratio %s), more "
                             "than MYCELIC_REPLAY_MAX_REJECT_RATIO (%s): what they carried is missing and /ready stays 503. %s",
                             rejected, seen, _recorded_ratio(rejected, seen), self.settings.replay_max_reject_ratio,
                             _BLOCK_REMEDY)
            else:
                logger.warning("the replay rejected the signature of %d of the %d events it consumed (within "
                               "MYCELIC_REPLAY_MAX_REJECT_RATIO %s; each is audited as event.rejected)", rejected, seen,
                               self.settings.replay_max_reject_ratio)
        self._ready_block = self._load_ready_block()

    def _load_ready_block(self) -> dict[str, Any] | None:
        """``meta.ready_block`` with its ``ratio`` and the configured ``max_ratio``, or None: no block, or one recorded at a
        ratio the configuration now allows (MYCELIC_REPLAY_MAX_REJECT_RATIO raised to it).  A block that does not parse
        holds, because a block must fail safe."""
        raw = self.store.get_meta(_READY_BLOCK)
        if raw is None:
            return None
        try:
            block = json.loads(raw)
            seen, rejected = int(block["seen"]), int(block["rejected"])
            ratio = rejected / seen
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            return {"reason": "signature_rejections"}
        if not ratio > self.settings.replay_max_reject_ratio:
            return None
        return {**block, "ratio": _recorded_ratio(rejected, seen), "max_ratio": self.settings.replay_max_reject_ratio}

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
        if info.get("error") or "last_seq" not in info:
            # the broker did not say how long the stream is (a timeout, JetStream not ready yet after a restart): judged on
            # that, the database would look ahead of a purged stream, so judge nothing; the consumer judges before its
            # next fetch (``_resync_pending``)
            raise ConnectionError(f"the broker did not report the stream's state: {info.get('error') or 'not connected'}")
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
                self._finish_replay_in_tx(tx, judge=False)         # that replay can never complete: its counts are partial
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
        """Establish the transport when there is no live client; a client that is reconnecting is left alone.  Once a
        client is back (or a publish or status read found no stream), make sure the stream and the durable consumer
        still exist (:meth:`_resync_transport`).  An error it did not expect is logged, counted
        (``mycelic_loop_errors_total{loop="transport"}``) and retried at the next round."""
        delay = 1.0
        while not self._stop.is_set():
            try:
                if getattr(self.transport, "needs_connect", not self.transport.connected):
                    ok = await self._connect_with_retry(first=False)
                    delay = 1.0 if ok else min(15.0, delay * 2)
                    if ok:
                        self.metrics.recoveries.labels("transport_reconnect").inc()
                if self.transport.connected and getattr(self.transport, "resync_needed", False):
                    await self._resync_transport()
            except Exception:
                logger.exception("the transport keeper failed; retrying")
                self.metrics.loop_errors.labels("transport").inc()
                delay = min(15.0, delay * 2)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay if not self.transport.connected else 2.0)
            except asyncio.TimeoutError:
                pass

    async def _resync_transport(self) -> None:
        """A broker that came back without its JetStream state (volume lost, stream deleted) has no stream and no durable
        consumer: recreate them and bring the database in step with the new log, as a start would
        (:meth:`_recover_if_needed`: the stream is shorter than the database, so the log restarts from its new sequence),
        so the outbox drains without a restart.  A reconnect to a broker that kept its state changes nothing."""
        if await self.transport.resync():
            self.metrics.recoveries.labels("broker_state_reset").inc()
            async with self.store.transaction() as tx:
                tx.audit("mycelic", "recovery.broker_state_reset", None, {"stream": self.settings.nats_stream,
                                                                          "consumer": self.settings.nats_consumer})
            try:
                await self._recover_if_needed()
            except Exception:
                self._resync_pending = True             # the consumer judges before its next fetch
                raise
            self._outbox_wake.set()

    async def _publisher_loop(self) -> None:
        """Publish the outbox in order.  Nothing ends the loop but ``stop``: an error it did not expect (the database
        full or failing, say) is logged with its traceback, counted (``mycelic_loop_errors_total{loop="publisher"}``) and
        backed off from, and the next round starts again from the outbox, which the failed transaction left as it was."""
        self._publisher_running = True
        backoff = self.settings.publish_interval_seconds
        try:
            while not self._stop.is_set():
                if not self.transport.connected:
                    await self._sleep(1.0)
                    continue
                try:
                    failed = await self._publish_pending()
                except Exception:
                    logger.exception("the publisher loop failed; backing off and retrying")
                    self.metrics.loop_errors.labels("publisher").inc()
                    failed = True
                if failed:
                    await self._sleep(min(10.0, backoff))
                    backoff = min(10.0, backoff * 2)
                else:
                    backoff = self.settings.publish_interval_seconds
        finally:
            self._publisher_running = False

    async def _publish_pending(self) -> bool:
        """One round of the publisher: publish the oldest pending events, or wait for new ones.  True when a publish failed
        (the caller backs off); the failure is kept in ``_publish_error`` until a publish succeeds."""
        rows = self.store.pending_events(self.settings.publish_batch)
        if not rows:
            self._outbox_wake.clear()
            try:
                await asyncio.wait_for(self._outbox_wake.wait(), timeout=self.settings.publish_interval_seconds)
            except asyncio.TimeoutError:
                pass
            return False
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
                if self._publish_error is None:
                    self._publish_error = (f"{name}: {exc}", time.monotonic())
                else:
                    self._publish_error = (f"{name}: {exc}", self._publish_error[1])
                async with self.store.transaction() as tx:
                    tx.mark_publish_failed(ev.event_id, f"{name}: {exc}")
                self.metrics.events_failed.labels("publish").inc()
                logger.warning("publish of %s failed: %s: %s", ev.event_id, name, exc)
                return True
            self._publish_error = None
            async with self.store.transaction() as tx:
                tx.mark_published(ev.event_id, seq)
            self.metrics.events_published.labels(ev.kind).inc()
        return False

    async def _consumer_loop(self) -> None:
        """Fetch and apply the log in order.  Nothing ends the loop but ``stop``: an error it did not expect while handling
        a delivery (the database full or failing while it records a rejected or terminated event, say) is logged with its
        traceback, counted (``mycelic_loop_errors_total{loop="consumer"}``) and backed off from.  The broker may have
        settled that delivery although the database did not record it, so before fetching again the consumer is brought
        back in step with the database exactly as at a start (:meth:`_recover_if_needed`: whatever the database has not
        recorded is delivered again).  A recovery the broker could not answer (at a start, after a broker state reset or
        here) is retried the same way, and nothing is fetched until it has run (``_resync_pending``)."""
        self._consumer_running = True
        backoff = 0.5
        try:
            while not self._stop.is_set():
                if not self.transport.connected:
                    await self._sleep(0.5)
                    continue
                if self._resync_pending:
                    try:
                        await self._recover_if_needed()
                        self._resync_pending = False
                    except Exception:
                        logger.exception("the consumer could not resynchronise with the database; backing off and retrying")
                        self.metrics.loop_errors.labels("consumer").inc()
                        await self._sleep(backoff)
                        backoff = min(10.0, backoff * 2)
                        continue
                try:
                    deliveries = await self.transport.fetch(self.settings.consume_batch, timeout=1.0)
                except Exception as exc:
                    logger.warning("fetch failed: %s: %s", type(exc).__name__, exc)
                    await self._sleep(backoff)
                    backoff = min(10.0, backoff * 2)
                    continue
                if not deliveries:
                    backoff = 0.5
                    self._idle.set()
                    continue
                self._idle.clear()
                try:
                    for d in deliveries:
                        await self._handle_delivery(d)
                    backoff = 0.5
                except Exception:
                    logger.exception("the consumer loop failed while handling a delivery; backing off, then resynchronising "
                                     "with the database")
                    self.metrics.loop_errors.labels("consumer").inc()
                    self._resync_pending = True
                    await self._sleep(backoff)
                    backoff = min(10.0, backoff * 2)
                self._idle.set()
        finally:
            self._consumer_running = False

    async def _handle_delivery(self, d: Any) -> None:
        t0 = time.perf_counter()
        if self.keyring.keyed and not self._verify(d.data, getattr(d, "headers", {}) or {}):
            # not ours: whoever published it did not hold the signing key.  Drop it loudly and permanently.
            self.metrics.events_failed.labels("signature").inc()
            logger.error("event at seq %s has a missing or invalid signature; terminated", d.seq)
            await d.term()
            async with self.store.transaction() as tx:
                tx.audit("mycelic", "event.rejected", None, {"reason": "invalid signature", "seq": d.seq, "subject": d.subject})
                if self._replay_target is not None and d.seq is not None:
                    self._count_meta_in_tx(tx, _REPLAY_SIG_REJECTED)    # before the progress that may end the replay
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
        """Record the stream position inside the transaction that consumed it (applied, rejected or terminated); during
        a replay, count the delivery, and end the replay at its target."""
        if seq is None:
            return
        tx.set_meta("last_applied_seq", str(seq))
        if self._replay_target is not None:
            self._count_meta_in_tx(tx, _REPLAY_SEEN)
            if seq >= self._replay_target:
                tx.delete_meta("replay_target_seq")
                self._finish_replay_in_tx(tx)

    def _count_meta_in_tx(self, tx: Tx, key: str) -> None:
        tx.set_meta(key, str(int(self.store.get_meta(key) or 0) + 1))

    def _note_progress(self, seq: int | None) -> None:
        if self._replay_target is None:
            return
        self.metrics.replay_events.inc()
        if seq is not None and seq >= self._replay_target:
            logger.info("replay complete at seq %s", seq)
            self._replay_target = None
            self._note_replay_judgement()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _reaggregating(self) -> bool:
        return self._reaggregate_task is not None and not self._reaggregate_task.done()

    async def wait_idle(self, timeout: float = 10.0) -> bool:
        """Wait until the outbox is empty, the consumer has nothing pending and no re-aggregation job runs (tests,
        demos, CLI)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = await self.transport.info()
            pending = self.store.stats()["outbox_pending"]
            if (pending == 0 and self._idle.is_set() and info.get("consumer_pending", 0) == 0 and self._replay_target is None
                    and not self._reaggregating()):
                # one more fetch round so a just-published event is applied
                await asyncio.sleep(0.25)
                info = await self.transport.info()
                if (self.store.stats()["outbox_pending"] == 0 and self._idle.is_set() and info.get("consumer_pending", 0) == 0
                        and not self._reaggregating()):
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
        signature = self.keyring.event_signature(wire)
        return {"Mycelic-Signature": signature} if signature else {}

    # event signatures (``verify`` further down is the downward verification of a memory)
    def _verify(self, wire: bytes, headers: dict[str, str]) -> bool:
        """Signed by the current key or by one of the previous keys (events published before a rotation)."""
        return self.keyring.verify_event(wire, headers.get("Mycelic-Signature", ""))

    def public_view(self, m: Memory | dict[str, Any], principal: Principal) -> dict[str, Any]:
        """What a caller may see of a memory: metadata that names other teams' agents is reserved for admins, and the
        text of a memory that is not active is reserved for its producer and admins."""
        d = m.to_dict() if isinstance(m, Memory) else dict(m)
        if principal.is_admin or (d.get("layer") == "agent" and d.get("producer_id") == principal.id):
            return d
        meta = d.get("metadata") or {}
        allowed = {"agg_key", "child_layer", "parent_count", "version_of", "fragility", "contributing_teams", "children",
                   "promoted_from", "effective_min_support", "corroborated_units", "rule_chain", "statements",
                   "statement_origins", "private_observations"}
        if d.get("operator") != "agent_observation":
            # what the aggregator derived; a raw note's own metadata (free-form, so an earlier client's 'value' or
            # 'conflict' may be anything) stays its producer's and the administrators', as in earlier releases
            allowed |= {"value", "conflict"}
        if principal.has("lineage:read"):        # the root ids are exactly what GET /lineage/{id} shows this caller
            allowed.add("roots")
        d["metadata"] = {k: v for k, v in meta.items() if k in allowed}
        if isinstance(meta.get("derivation"), dict):
            # the version and the rule's digest (or min_support); the rule's full snapshot is for administrators
            d["metadata"]["derivation"] = {k: v for k, v in meta["derivation"].items() if k != "rule"}
        d["local_ref"] = None
        if d.get("status") != "active":
            # ``Principal.can_read_text`` for a caller that already passed ``can_read``: the memory keeps its shape and
            # its status, its text is withheld (the key is absent whenever the text is shown)
            d["text"] = ""
            d["text_withheld"] = d.get("status")
            d["metadata"].pop("statements", None)
            d["metadata"].pop("statement_origins", None)
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
    def _validate_memory_body(self, principal: Principal, body: dict[str, Any], *, source_event_ids: list[str] | None = None,
                              allow_supersedes: bool = False) -> Memory:
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
        expires_at = _expiry(body)
        supersedes = _s(body, "supersedes", max_len=200, pattern=_ID_RE)
        if supersedes is not None and not allow_supersedes:
            raise ValidationError("'supersedes' is accepted by POST /memory only")
        meta = {k: v for k, v in _small_dict(body, "metadata", 4096).items() if k not in RESERVED_METADATA_KEYS}
        # the note's structured claim for its slot and entity, kept as metadata.value: aggregation compares it (a dispute
        # is never corroboration).  metadata was free-form before, so a metadata.value sent without 'value' is stored as
        # sent, whatever it is, and claims something only if it is a short string (``models.canonical_value``)
        value = _label(body, "value", max_len=MAX_VALUE_CHARS)
        if value is not None:
            if "value" in meta and canonical_value(meta["value"]) != value:
                raise ValidationError("'value' and 'metadata.value' differ")
            meta["value"] = value
        if supersedes is not None:
            meta["version_of"] = supersedes             # reserved: only an update sets it, and the digest covers it
        observed_at = _iso(body, "observed_at") or now_iso()
        idem = _s(body, "idempotency_key", max_len=200)
        memory_id = f"mem_{content_hash(principal.id, idem)[:22]}" if idem else new_id("mem")
        return Memory(
            memory_id=memory_id, org_id=principal.org_id or "", layer="agent", scope=principal.path or "",
            text=text or "", topic=_label(body, "topic", max_len=200), slot=_label(body, "slot", max_len=100, pattern=_SLOT_RE),
            entity=_label(body, "entity", max_len=200), kind=kind, confidence=_f(body, "confidence", 0.8), support=1,
            independent_teams=1, producer_id=principal.id, operator="agent_observation", rule_id=None, event_id=None,
            visibility=visibility, status="active", created_at=observed_at, applied_at=None, source_event_ids=srcs,
            local_ref=_s(body, "local_ref", max_len=200), metadata=meta, expires_at=expires_at,
        )

    async def ingest_memory(self, principal: Principal, body: dict[str, Any], *, remote: str | None = None) -> tuple[Memory, bool]:
        """Store a memory and append its event to the outbox in one transaction. Returns (memory, created).

        With ``supersedes`` the memory is a producer's update: a complete new note (nothing is inherited) that replaces
        one of the caller's own active raw notes when its event applies (``metadata.version_of``)."""
        if not isinstance(body, dict):
            raise ValidationError("body must be a JSON object")
        m = self._validate_memory_body(principal, body, allow_supersedes=True)
        supersedes = m.metadata.get("version_of")
        if supersedes == m.memory_id:
            raise ValidationError("'supersedes' names the memory this idempotency_key already identifies; use a new "
                                  "idempotency_key for the update")
        event = EventRecord(event_id=new_id("evt"), kind="memory.observed", org_id=m.org_id, agent_id=principal.id,
                            subject=subject_for(m.org_id, "memory.observed"), payload={}, created_at=now_iso())
        m.event_id = event.event_id
        event.payload = m.to_dict()
        self._check_event_size(event)
        async with self.store.transaction() as tx:
            self._check_writer(principal)
            existing = self.store.get_memory(m.memory_id)
            if existing is not None:
                return existing, False          # a resend: whatever its expiry or its target says by now
            _check_expiry(m.expires_at, utcnow())
            if supersedes is not None:
                # an update replaces an active note, at most one per target is pending (409), so it is not capped: the
                # count is back where it was once it applies
                self._check_update(principal, supersedes)
            else:
                self._check_quota(m.org_id)
            tx.insert_memory(m)
            tx.insert_event(event)
            if supersedes is not None:
                tx.audit(principal.id, "memory.update", m.memory_id, {"org_id": m.org_id, "supersedes": supersedes,
                                                                      "event_id": event.event_id}, remote)
            else:
                tx.audit(principal.id, "memory.ingest", m.memory_id, {"org_id": m.org_id, "topic": m.topic, "slot": m.slot,
                                                                      "event_id": event.event_id}, remote)
        self.metrics.memories_ingested.labels("agent").inc()
        self._outbox_wake.set()
        return m, True

    def _check_update(self, principal: Principal, target: str) -> None:
        """May ``principal`` update ``target`` now?  Its own active raw note, with no retraction (an expiry's included) and
        no other update of it still on its way through the log (Conflict then: retry once that has applied)."""
        old = self.store.get_memory(target)
        if old is None or not principal.can_read(old):
            raise NotFound(target)
        if old.operator != "agent_observation":
            raise ValidationError("'supersedes' must name a raw observation: derived memories are recomputed from their evidence")
        if old.producer_id != principal.id:
            raise Forbidden("only the producing agent can update a memory")
        if old.status != "active":
            raise ValidationError("'supersedes' names a memory that is not active (it was superseded or retracted)")
        since = [old.event_id] if isinstance(old.event_id, str) else []
        retractions = self.store.lifecycle_events(old.org_id, ("memory.retracted",), since=since).get(target, [])
        if any(status in ("pending", "published") for _, status in retractions) or self.store.unapplied_updates(old.org_id, [target]):
            raise Conflict("a retraction or another update of this memory is waiting to be applied; retry once it has been applied")

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
        new = 0                                 # embedded memories this batch inserts
        async with self.store.transaction() as tx:
            self._check_writer(principal)
            now = utcnow()
            cap = self.settings.max_active_memories_per_org
            active = self.store.active_note_count(principal.org_id or "") if cap > 0 else 0
            for record, mem in prepared:
                created = tx.insert_event(record)
                entry: dict[str, Any] = {"event_id": record.event_id, "created": created}
                if mem is not None:
                    mev = EventRecord(event_id=new_id("evt"), kind="memory.observed", org_id=mem.org_id, agent_id=principal.id,
                                      subject=subject_for(mem.org_id, "memory.observed"), payload={}, created_at=now_iso())
                    mem.event_id = mev.event_id
                    mev.payload = mem.to_dict()
                    if self.store.get_memory(mem.memory_id) is None:
                        _check_expiry(mem.expires_at, now)          # refused: the whole batch rolls back
                        if cap > 0 and active + new + 1 > cap:      # so is this: nothing of the batch is written
                            self._refuse_quota(mem.org_id, cap, active, batch_adds=new + 1)
                        tx.insert_memory(mem)
                        tx.insert_event(mev)
                        new += 1
                        entry["memory_id"] = mem.memory_id
                        entry["memory_event_id"] = mev.event_id
                    else:
                        entry["memory_id"] = mem.memory_id
                        entry["memory_created"] = False
                results.append(entry)
            tx.audit(principal.id, "events.ingest", None, {"org_id": principal.org_id, "count": len(prepared)}, remote)
        self.metrics.memories_ingested.labels("agent").inc(new)     # after the commit: a refused batch counts nothing
        self.metrics.events_received.inc(len(prepared))
        self._outbox_wake.set()
        return results

    def _check_quota(self, org_id: str) -> None:
        """Inside the write's transaction: refuse (:class:`QuotaExceeded`) a new note that would take the organization past
        MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG active raw notes, applied or still on their way through the log.  A note
        leaves the count when its retraction (an expiry's or an agent removal's included) applies; derived memories,
        updates and everything the consumer applies are never capped, so a rebuild applies the whole log."""
        cap = self.settings.max_active_memories_per_org
        if cap > 0:
            active = self.store.active_note_count(org_id)
            if active + 1 > cap:
                self._refuse_quota(org_id, cap, active)

    def _refuse_quota(self, org_id: str, cap: int, active: int, *, batch_adds: int | None = None) -> None:
        """Count the refused request and raise :class:`QuotaExceeded` (once per request: its transaction rolls back)."""
        self.metrics.quota_rejections.inc()
        batch = f", this batch adds {batch_adds}" if batch_adds is not None else ""
        raise QuotaExceeded(f"organization '{org_id}' is at its limit of {cap} active notes (MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG; "
                            f"{active} active now{batch}): retract notes it no longer needs (a retraction counts once it has "
                            "been applied) or ask the operator to raise the limit")

    def _check_writer(self, principal: Principal) -> None:
        """Inside the write's transaction: is the agent still active?  A request authenticated before a revocation committed
        must not write after it (the store lock orders the two transactions)."""
        agent = self.store.get_agent(principal.id)
        if agent is None or agent.status != "active":
            raise Forbidden("agent revoked")

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
            if not principal.is_admin:
                self._check_writer(principal)
            tx.insert_event(event)
            tx.audit(principal.id, "memory.retract", memory_id, {"org_id": m.org_id, "reason": reason[:200]}, remote)
        self._outbox_wake.set()
        return event

    async def attest(self, principal: Principal, memory_id: str, body: dict[str, Any], *, remote: str | None = None,
                     now: datetime | None = None) -> tuple[EventRecord, bool]:
        """The producer re-attests one of its own active raw notes: ``still_true`` true appends a ``memory.attested``
        event, which sets the note's ``attested_at`` when it applies (freshness for verification), false retracts the
        note.  Returns the event and ``still_true``.  Self-attestation: it adds freshness, not independent assurance.
        ``now`` is a test hook."""
        if principal.kind != "agent":
            raise Forbidden("only the producing agent can attest a memory")
        if not principal.has("memory:write"):
            raise Forbidden("missing scope memory:write")
        if not isinstance(body, dict):
            raise ValidationError("body must be a JSON object")
        still_true = body.get("still_true")
        if not isinstance(still_true, bool):
            raise ValidationError("'still_true' must be true or false")
        reason = _s(body, "reason", max_len=200)
        m = self.store.get_memory(memory_id)
        if m is None or not principal.can_read(m):
            raise NotFound(memory_id)
        if m.operator != "agent_observation":
            raise ValidationError("only a raw observation can be attested: derived memories are recomputed from their evidence")
        if m.producer_id != principal.id:
            raise Forbidden("only the producing agent can attest a memory")
        if m.status != "active":
            raise ValidationError("the memory is not active (it was superseded or retracted)")
        at = now or utcnow()
        expires = parse_iso(m.expires_at)
        if expires is not None and expires <= at:
            raise ValidationError("the memory has expired")
        if not still_true:
            return await self.retract(principal, memory_id, reason or "no longer true (attestation)", remote=remote), False
        event = EventRecord(event_id=new_id("evt"), kind="memory.attested", org_id=m.org_id, agent_id=principal.id,
                            subject=subject_for(m.org_id, "memory.attested"),
                            payload={"memory_id": memory_id, "by": principal.id,
                                     "at": at.astimezone(timezone.utc).isoformat(timespec="seconds")}, created_at=now_iso())
        async with self.store.transaction() as tx:
            self._check_writer(principal)
            tx.insert_event(event)
            tx.audit(principal.id, "memory.attest", memory_id, {"org_id": m.org_id, "at": event.payload["at"]}, remote)
        self._outbox_wake.set()
        return event, True

    def due_attestations(self, principal: Principal, *, older_than: int = 0, limit: int = 50,
                         now: datetime | None = None) -> list[dict[str, Any]]:
        """The caller's own notes worth re-attesting (``MycelicStore.due_attestations``): last ingested or attested at least
        ``older_than`` seconds ago, stalest first, at most ``limit``.  ``now`` is a test hook."""
        if principal.kind != "agent":
            raise Forbidden("only agents have notes to attest")
        if not principal.has("memory:read"):
            raise Forbidden("missing scope memory:read")
        if isinstance(older_than, bool) or not isinstance(older_than, int) or not 0 <= older_than <= MAX_EXPIRY_SECONDS:
            raise ValidationError(f"'older_than' must be an integer between 0 and {MAX_EXPIRY_SECONDS} seconds")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValidationError("'limit' must be an integer between 1 and 500")
        at = (now or utcnow()).astimezone(timezone.utc)
        return self.store.due_attestations(principal.org_id or "", principal.id,
                                           cutoff=(at - timedelta(seconds=older_than)).isoformat(timespec="seconds"),
                                           now=at.isoformat(timespec="seconds"), limit=limit)

    # ------------------------------------------------------------------ apply (consumer side)
    async def apply_event(self, event: dict[str, Any], *, seq: int | None = None) -> str:
        """Apply one event from the log. Idempotent: an already-applied event id is a no-op ('duplicate')."""
        event_id = event["event_id"]
        kind = event.get("kind")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        org_id = event.get("org_id") or payload.get("org_id") or ""
        now = now_iso()
        ignored: str | None = None              # the reason an event had no effect (mycelic_events_ignored_total)
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
                # a payload from another release may leave optional fields out: the note's event and time then come from
                # the envelope, so a rebuild stores the same row
                m = self._memory_from_payload(payload, event_id=event_id, created_at=event.get("created_at") or now)
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
                if m.status == "active" and self.store.agent_log_status(m.producer_id) == "removed":
                    # logged after its producer's removal (a write that raced the revocation, or an injected event): it
                    # is applied retracted, as the removal would have, and never offered upward
                    tx.set_memory_status(m.memory_id, "retracted", reason="agent removed")
                    tx.audit("mycelic", "memory.observed_after_removal", m.memory_id, {"org_id": m.org_id,
                                                                                      "agent_id": m.producer_id})
                elif m.status == "active":
                    derivations = self._apply_observed(tx, m)
            elif kind == "memory.derived":
                # Informational.  A derived memory exists only because this node derived it from applied evidence
                # (a replay re-derives it when it applies that evidence), so nothing on the stream can plant a
                # conclusion, a lineage edge or a supersession: the payload is never inserted.
                mid = payload.get("memory_id")
                if isinstance(mid, str) and self.store.get_memory(mid) is not None:
                    result = "duplicate"
                else:
                    result, ignored = "ignored", "derived_not_reproduced"
                    if self._replay_target is None:
                        tx.audit("mycelic", "event.ignored", event_id, {"reason": ignored, "org_id": org_id,
                                                                       "memory_id": mid if isinstance(mid, str) else None})
                    else:                       # a rebuild over an older log can ignore thousands: one summary row
                        tx.set_meta(_REPLAY_IGNORED, str(int(self.store.get_meta(_REPLAY_IGNORED) or 0) + 1))
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
                    tx.audit("mycelic", "event.ignored", event_id, {"reason": "retraction target is not an active raw observation",
                                                                   "org_id": org_id})
                    result, ignored = "ignored", "retraction_target"
            elif kind == "memory.attested":
                ignored = self._apply_attestation(tx, payload)
                if ignored:
                    result = "ignored"
                    mid = payload.get("memory_id")
                    tx.audit("mycelic", "memory.attest_ignored", mid if isinstance(mid, str) else None,
                             {"org_id": org_id, "reason": ignored})
            elif kind == "agent.event":
                pass
            elif kind == "agent.registered":
                agent = self._agent_from_payload(payload)
                if self.store.get_agent(agent.agent_id) is None:
                    tx.insert_agent(agent, str(payload.get("key_hash") or ""))
                known = self.store.get_agent(agent.agent_id) or agent
                before = self.aggregator.registry_counts(known.org_id, known.path)
                # aggregation counts a unit's children from the registry as applied (``log_status``), never from
                # what the API has already written, so a live node and a rebuild count the same children; a removed
                # agent stays removed
                if (self.store.agent_log_status(agent.agent_id) == "removed"
                        or not tx.set_agent_log_status(agent.agent_id, "active")):
                    result = "duplicate"
                else:                           # a unit with one more child unit may no longer promote its only child
                    derivations = self.aggregator.registry_changed(tx, known.org_id, known.path, before)
            elif kind == "agent.revoked":
                agent_id = str(payload.get("agent_id"))
                known = self.store.get_agent(agent_id)
                before = self.aggregator.registry_counts(known.org_id, known.path) if known is not None else {}
                tx.set_agent_status(agent_id, "revoked")
                # 'removed' is terminal: a later plain revocation leaves it (and the handling of late notes) in place
                if (self.store.agent_log_status(agent_id) != "removed" and tx.set_agent_log_status(agent_id, "revoked")
                        and known is not None):
                    # the agent's notes stay evidence; a unit left with fewer child units may promote again
                    derivations = self.aggregator.registry_changed(tx, known.org_id, known.path, before)
            elif kind == "agent.removed":
                # the agent leaves: its key is revoked and every note it has is retracted in this one apply
                agent_id = str(payload.get("agent_id"))
                known = self.store.get_agent(agent_id)
                if known is not None:
                    before = self.aggregator.registry_counts(known.org_id, known.path)
                    tx.set_agent_status(agent_id, "revoked")
                    # the registry first, so what the retractions re-derive already counts the unit's children without it
                    changed = tx.set_agent_log_status(agent_id, "removed")
                    notes = self.store.active_notes(known.org_id, agent_id, applied_only=True)
                    derivations = self.aggregator.retract_notes(tx, notes, "agent removed")
                    if changed:
                        derivations += self.aggregator.registry_changed(tx, known.org_id, known.path, before)
                    tx.audit("mycelic", "agent.removed", agent_id, {"org_id": known.org_id, "retracted": len(notes),
                                                                   "derivations": len(derivations)})
            elif kind == "agent.key_rotated":
                tx.rotate_agent_key(str(payload.get("agent_id")), str(payload.get("key_hash") or ""), str(payload.get("key_prefix") or ""))
            elif kind == "rule.upserted":
                # aggregation evaluates the rules as applied, in log order (``applied_rules``); the rule is applied to
                # everything applied before it, after it is recorded, so what it retires is re-evaluated under it
                rule = self._rule_from_body(payload)
                tx.upsert_applied_rule(rule, self.store.max_apply_seq())
                if self._writes_admin_table(tx, status, event_id, rule.rule_id):
                    tx.upsert_rule(rule)
                derivations = self.aggregator.evaluate_rule(tx, rule)
            elif kind == "rule.deleted":
                rule_id = str(payload.get("rule_id"))
                tx.delete_applied_rule(rule_id)
                if self._writes_admin_table(tx, status, event_id, rule_id):
                    tx.delete_rule(rule_id)
                derivations = self.aggregator.withdraw_rule(tx, rule_id)
            else:
                result, ignored = "ignored", "unknown_kind"
            self._emit_derived(tx, derivations, now)
            tx.mark_applied(event_id, seq, now)
            self._progress_in_tx(tx, seq)
        if ignored:
            self.metrics.events_ignored.labels(ignored).inc()
        if derivations:
            self._outbox_wake.set()
        return result

    def _apply_observed(self, tx: Tx, m: Memory) -> list[Derivation]:
        """Offer an applied raw note upward; when it is a producer's update (``metadata.version_of``) of a note that is
        still the producer's own active raw note, that note is superseded first and everything resting on it re-derived.
        An update whose target is gone, inactive or not the producer's applies as a plain note and is audited."""
        target = m.metadata.get("version_of")
        if target is None:
            return self.aggregator.derive_for(tx, m)
        old = self.store.get_memory(target) if isinstance(target, str) else None
        reason = ("target not found" if old is None else "self" if old.memory_id == m.memory_id
                  else "not raw" if old.operator != "agent_observation"
                  else "other producer" if (old.producer_id, old.org_id) != (m.producer_id, m.org_id)
                  else "target not active" if old.status != "active" else None)
        if reason is not None:
            tx.audit("mycelic", "memory.update_conflict", m.memory_id, {"org_id": m.org_id, "supersedes": target, "reason": reason})
            return self.aggregator.derive_for(tx, m)
        tx.set_memory_status(old.memory_id, "superseded", superseded_by=m.memory_id, reason="updated by producer")
        retired = self.aggregator.retire_dependents(tx, old.memory_id, "evidence superseded")
        derivations = self.aggregator.derive_for(tx, m) + self.aggregator.derive_for(tx, old)
        derivations += self.aggregator.reevaluate(tx, retired)
        tx.audit("mycelic", "memory.updated", old.memory_id, {"org_id": m.org_id, "by": m.memory_id})
        return derivations

    def _apply_attestation(self, tx: Tx, payload: dict[str, Any]) -> str | None:
        """Set a raw note's ``attested_at`` from a ``memory.attested`` event; the reason it was ignored, or None.  Only
        the producer's attestation of its active raw note counts, and only one dated at or after the note's ingest and
        after its last attestation (so a late older one never moves it back); a row whose digest does not check is never
        re-signed."""
        mid = payload.get("memory_id")
        m = self.store.get_memory(mid) if isinstance(mid, str) else None
        stamp = utc_seconds(payload.get("at"))
        at = parse_iso(stamp)
        ev = self.store.get_event(m.event_id) if m is not None and isinstance(m.event_id, str) else None
        ingested = parse_iso(ev.created_at) if ev is not None else None
        if (m is None or m.status != "active" or m.operator != "agent_observation" or payload.get("by") != m.producer_id
                or at is None or ingested is None or at < ingested):
            return "attestation_target"
        last = parse_iso(m.attested_at)
        if last is not None and at <= last:
            return "attestation_not_newer"
        if not tx.set_attested(m.memory_id, stamp):
            return "attestation_integrity"
        tx.audit("mycelic", "memory.attested", m.memory_id, {"org_id": m.org_id, "at": stamp})
        return None

    def _emit_derived(self, tx: Tx, derivations: list[Derivation], now: str) -> None:
        """Append one ``memory.derived`` event per derivation, in the transaction that persisted it."""
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

    @staticmethod
    def _writes_admin_table(tx: Tx, status: str | None, event_id: str, rule_id: str) -> bool:
        """``rules`` holds what the admin API or the rules file wrote on this node, so a rule event writes it only when
        the event came from the stream alone (rebuild, another writer) and no newer local change of the same rule
        is still on its way through the log: a replayed old version never overwrites a newer one."""
        return status is None and not tx.has_unapplied_rule_event(rule_id, other_than=event_id)

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
    def _memory_from_payload(p: dict[str, Any], *, event_id: str | None = None, created_at: str | None = None) -> Memory:
        """The raw note a ``memory.observed`` payload carries; fields it leaves out take their defaults (``event_id`` and
        ``created_at`` those of the event that carries it), and fields this release does not know are ignored."""
        required = ("memory_id", "org_id", "layer", "scope", "text")
        for k in required:
            if not isinstance(p.get(k), str) or not p[k]:
                raise ValidationError(f"memory payload lacks '{k}'")
        if p["layer"] not in LAYERS:
            raise ValidationError("memory payload has an unknown layer")
        split_path(p["scope"])
        for k in ("topic", "slot", "entity"):
            if p.get(k) is not None and not isinstance(p[k], str):
                raise ValidationError(f"memory payload has a non-string '{k}'")
        expires_at = p.get("expires_at")
        if expires_at is not None:
            expires_at = utc_seconds(expires_at)
            if expires_at is None:
                raise ValidationError("memory payload has an invalid 'expires_at'")
        # the log is authoritative, so no charset check: an event written before labels were normalised is
        # applied in today's spelling, exactly as the migration rewrote the rows it produced
        return Memory(
            memory_id=p["memory_id"], org_id=p["org_id"], layer=p["layer"], scope=p["scope"], text=p["text"],
            topic=canonical_label(p.get("topic")), slot=canonical_label(p.get("slot")), entity=canonical_label(p.get("entity")),
            kind=p.get("kind") or "observation",
            confidence=float(p.get("confidence", 0.5)), support=int(p.get("support", 1)),
            independent_teams=int(p.get("independent_teams", 1)), producer_id=p.get("producer_id") or "unknown",
            operator=p.get("operator") or "agent_observation", rule_id=p.get("rule_id"), event_id=p.get("event_id") or event_id,
            visibility=p.get("visibility") or "team", status=p.get("status") or "active", superseded_by=None,
            created_at=p.get("created_at") or created_at or now_iso(), applied_at=p.get("applied_at"),
            source_event_ids=list(p.get("source_event_ids") or []), local_ref=p.get("local_ref"),
            metadata=dict(p.get("metadata") or {}), expires_at=expires_at,
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
                                     topic=_label(body, "topic", max_len=200), entity=_label(body, "entity", max_len=200))
        answer = None
        lineage = None
        if hits:
            top = hits[0].memory               # retrieval indexes active memories only, so its text is never withheld
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
            g = reconstruct(self.store, m.memory_id, visible=principal.can_read, full=principal.owns,
                            readable_text=principal.can_read_text)
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
            g = reconstruct(self.store, memory_id, visible=principal.can_read, full=principal.owns,
                            readable_text=principal.can_read_text)
        except Exception:
            self.metrics.lineage_results.labels("failure").inc()
            raise
        self.metrics.lineage_latency.observe(time.perf_counter() - t0)
        self.metrics.lineage_results.labels("success" if g["evidence"]["reconstructable"] else "incomplete").inc()
        return g

    async def verify(self, principal: Principal, memory_id: str, *, max_leaf_age: int | None = None,
                     remote: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        """Downward verification of a memory the caller may read (``verification.py``): was it derived correctly, and is
        it still true?  Unknown and unreadable ids are the same NotFound, as for lineage.  ``max_leaf_age`` (seconds)
        also checks how long ago each readable raw note was ingested; ``now`` is a test hook.

        The walk never holds the event loop: it runs in a worker thread on a read-only snapshot of the database
        (:meth:`MycelicStore.snapshot_reader`; an in-memory database is walked on the loop, under the store lock), at most
        ``verify_concurrency`` at once for all callers and one at a time per caller, so /health, /ready and every other
        request keep answering while a large DAG is walked.

        The walk is priced after the fact, on top of the request's own token, into debt if need be: the larger of
        ``ceil(nodes / VERIFY_NODES_PER_TOKEN)`` and ``ceil(VERIFY_TIME_PRICE * seconds walked * rps)`` tokens from the
        caller's rate-limit bucket (:meth:`RateLimiter.take`), so a caller that keeps walking pays more than its bucket
        refills.  A caller in debt is refused (:class:`RateLimited`) before anything is read, and again once its turn to
        walk comes, so a JSON-RPC batch, whose messages run back to back with no middleware between them, and concurrent
        requests of one caller walk at most once past solvency.  A successful call is charged, counted by verdict and
        reason and audited; a refused one (Forbidden, ValidationError, NotFound, RateLimited) is none of these.  The reason
        counters take the codes of the caller's own report (``hidden_*`` for a node it may not read, no hidden warning),
        because ``/metrics`` answers any agent key when ``MYCELIC_METRICS_TOKEN`` is unset; the audit row keeps the codes
        before redaction."""
        if not principal.has("lineage:read"):
            raise Forbidden("missing scope lineage:read")
        if not isinstance(memory_id, str) or not _ID_RE.fullmatch(memory_id):
            raise ValidationError("'memory_id' must be an id of at most 200 characters [A-Za-z0-9_.:-]")
        if max_leaf_age is not None and (isinstance(max_leaf_age, bool) or not isinstance(max_leaf_age, int)
                                         or not 1 <= max_leaf_age <= verification.MAX_LEAF_AGE_SECONDS):
            raise ValidationError(f"'max_leaf_age' must be an integer between 1 and {verification.MAX_LEAF_AGE_SECONDS} seconds")
        t0 = time.perf_counter()
        key = principal.limiter_key
        # nothing in here awaits, so the lookup never sees a transaction half-way through its body
        async with self.store._lock:
            self._refuse_if_in_debt(key)
            m = self.store.get_memory(memory_id)
            if m is None or not principal.can_read(m):
                raise NotFound(memory_id)
        async with self._verification_turn(key):
            self._refuse_if_in_debt(key)            # a walk of this caller that ran while it waited may have spent it all
            walk_t0 = time.perf_counter()
            result = await self._walk(principal, memory_id, now=now or utcnow(), max_leaf_age=max_leaf_age)
            walked = time.perf_counter() - walk_t0
            self.limiter.take(key, max(math.ceil(result.report["summary"]["nodes"] / VERIFY_NODES_PER_TOKEN),
                                       math.ceil(VERIFY_TIME_PRICE * walked * max(0.0, self.limiter.rps))))
        verdict = result.report["verdict"]
        self.metrics.verification_latency.observe(time.perf_counter() - t0)
        self.metrics.verifications.labels(verdict).inc()
        # the codes as the caller sees them: diffing /metrics around its own call must not tell an agent what its report
        # redacted (an administrator's report, and so its count, has every code)
        for code in sorted({r["code"] for r in result.report["reasons"]} | {w["code"] for w in result.report["warnings"]}):
            self.metrics.verification_reasons.labels(code).inc()
        # the codes before redaction: the audit log is for administrators
        await self.store.audit(principal.id, "memory.verify", memory_id, {"org_id": m.org_id, "verdict": verdict,
                                                                          "nodes": result.report["summary"]["nodes"],
                                                                          "reasons": result.codes}, remote)
        return result.report

    def _refuse_if_in_debt(self, key: str) -> None:
        if self.limiter.in_debt(key):
            self.metrics.auth_failures.labels("rate_limited").inc()
            raise RateLimited("rate limit exceeded")

    @asynccontextmanager
    async def _verification_turn(self, key: str) -> AsyncIterator[None]:
        """One walk at a time per caller (``key``), ``verify_concurrency`` at a time for everyone."""
        entry = self._verify_callers.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0], self._verify_slots:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                del self._verify_callers[key]

    async def _walk(self, principal: Principal, memory_id: str, *, now: datetime,
                    max_leaf_age: int | None) -> verification.VerificationResult:
        """``verification.verify`` on a committed snapshot, in a worker thread (an in-memory database: on the loop, under
        the store lock).  A memory is never deleted, so the id found under the lock is in the snapshot."""
        def run(store: MycelicStore) -> verification.VerificationResult:
            planner = Aggregator(store, min_support=self.aggregator.min_support, clock=self.aggregator.clock,
                                 max_candidates=self.aggregator.max_candidates, max_dependents=self.aggregator.max_dependents)
            planner._cap_warned = self.aggregator._cap_warned
            return verification.verify(store, planner, self.keyring, memory_id, principal=principal, now=now,
                                       max_nodes=self.settings.verify_max_nodes, max_leaf_age=max_leaf_age)

        if self.store.db_path == ":memory:":
            async with self.store._lock:
                return run(self.store)

        def in_snapshot() -> verification.VerificationResult:
            reader = self.store.snapshot_reader()
            try:
                reader._conn.execute("BEGIN")
                try:
                    return run(reader)
                finally:
                    reader._conn.execute("ROLLBACK")
            finally:
                reader._conn.close()

        return await asyncio.to_thread(in_snapshot)

    async def query_and_verify(self, principal: Principal, body: dict[str, Any], *, remote: str | None = None) -> dict[str, Any]:
        """POST /query: :meth:`query`, and with ``"verify": true`` the answer's verification summary (``verdict``, both
        answers and the report-level reasons as the caller sees them) as ``answer.verification``.  Only for callers holding
        lineage:read, like the embedded lineage graph; without the flag, or without an answer, the response is exactly
        :meth:`query`'s.  No freshness bound applies here: ``GET /verify/{id}?max_leaf_age=`` has it."""
        flag = body.get("verify") if isinstance(body, dict) else None
        if flag is not None and not isinstance(flag, bool):
            raise ValidationError("'verify' must be true or false")
        res = self.query(principal, body)
        if flag is True and res["answer"] is not None and principal.has("lineage:read"):
            report = await self.verify(principal, res["answer"]["memory_id"], remote=remote)
            res["answer"]["verification"] = {k: report[k] for k in ("verdict", "derived_correctly", "still_true", "reasons")}
        return res

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

    async def revoke_agent(self, agent_id: str, *, retract: bool = False, remote: str | None = None) -> dict[str, Any] | None:
        """Revoke an agent's key (None for an unknown id).  With ``retract`` the agent is removed: one ``agent.removed``
        event retracts every note it has when the event applies, and ``retracted`` counts the notes active now (applied
        or still on their way through the log; a settled repeat counts 0).  Every call appends an event."""
        agent = self.store.get_agent(agent_id)
        if agent is None:
            return None
        async with self.store.transaction() as tx:
            tx.set_agent_status(agent_id, "revoked")
            if not retract:
                tx.insert_event(self._admin_event("agent.revoked", agent.org_id, {"agent_id": agent_id}))
                tx.audit("admin", "agent.revoke", agent_id, {"org_id": agent.org_id}, remote)
                result: dict[str, Any] = {"agent_id": agent_id, "revoked": True}
            else:
                n = len(self.store.active_notes(agent.org_id, agent_id, applied_only=False))
                tx.insert_event(self._admin_event("agent.removed", agent.org_id, {"agent_id": agent_id, "org_id": agent.org_id}))
                tx.audit("admin", "agent.remove", agent_id, {"org_id": agent.org_id, "active_notes": n}, remote)
                result = {"agent_id": agent_id, "revoked": True, "retracted": n}
        self._outbox_wake.set()
        return result

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
        body = canonical_rule_body(body)          # validated (and stored) in the spelling memories are compared in
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
        return not c.topic_prefix or (p.conclusion_topic() or "").startswith(c.topic_prefix)

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
        """Re-deliver the whole log to this instance. Applying is idempotent; missing derived state is rebuilt.

        It cannot undo a readiness block (:meth:`_finish_replay_in_tx`): it runs all the same, and the answer and its
        audit row carry a ``warning``; only a replay over the ratio again rewrites the block, none removes it."""
        blocked = self._ready_block is not None
        info = await self.transport.info()
        await self.transport.reset_consumer()
        await self._set_replay_target(info.get("last_seq") or info.get("stream_messages") or None)
        self.metrics.recoveries.labels("replay_requested").inc()
        res: dict[str, Any] = {"replaying": True, "target_seq": self._replay_target}
        if blocked:
            res["warning"] = ("readiness stays blocked: a replay into this database cannot restore what an earlier replay "
                              "lost by rejecting signatures, because the events that depended on the rejected ones were "
                              "applied without them and are never applied again; stop the service, add the key that signed "
                              "them to MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS, move the database aside and start it to rebuild "
                              "(DEPLOYMENT.md section 4, 'Signing-key mistakes')")
        await self.store.audit("admin", "replay", None, {k: v for k, v in res.items() if k != "replaying"}, remote)
        return res

    # ------------------------------------------------------------------ re-aggregation (local maintenance)
    # Derived ids embed the derivation version, MIN_SUPPORT and each rule's digest, so state derived by an older
    # release or under another MIN_SUPPORT is converged by re-aggregating it.  That is local maintenance, not an
    # event: a rebuild from the log derives the converged state directly.
    def _reaggregate_reason(self) -> str | None:
        """Why the derived state may not be what this code and configuration derive, or None."""
        if self.store.get_meta("reaggregate_pending") is not None:
            return "pending"
        if self.store.get_meta("derivation_version") != str(DERIVATION_VERSION):
            return "derivation_version"
        if self.store.get_meta("min_support") != str(self.aggregator.min_support):
            return "min_support"
        return None

    def _record_derivation_meta(self, tx: Tx) -> None:
        tx.set_meta("derivation_version", str(DERIVATION_VERSION))
        tx.set_meta("min_support", str(self.aggregator.min_support))
        tx.delete_meta("reaggregate_pending")

    def _start_reaggregation(self, orgs: list[str] | None, reason: str) -> bool:
        """Start the re-aggregation job unless one is running (False then: the running job is not replaced)."""
        if self._reaggregating():
            return False
        self._reaggregation = {"state": "running", "reason": reason, "scope": orgs or "all", "org_id": None, "phase": None,
                               "steps": 0, "changed": 0, "started_at": now_iso(), "finished_at": None, "error": None}
        self._reaggregate_task = asyncio.create_task(self._reaggregate_job(orgs, reason), name="mycelic-reaggregate")
        # stop() cancels it with the loops; earlier runs that finished are dropped, so repeated runs do not pile up
        self._tasks = [t for t in self._tasks if not t.done()] + [self._reaggregate_task]
        return True

    async def _reaggregate_job(self, orgs: list[str] | None, reason: str) -> None:
        """Re-aggregate ``orgs`` (all organizations when None) in steps of ``reaggregate_batch`` items, one transaction
        each, yielding to the event loop between steps, so the node keeps serving (and applying) while it converges.

        Nothing runs while a replay is in progress (it derives everything itself, and a step's derived events would be
        marked published); the job waits and then starts again from the first organization.  A complete run over every
        organization records the derivation meta; an interrupted or failed one leaves it, so the next start re-runs.
        """
        state = self._reaggregation                         # set by _start_reaggregation, reported by health()
        try:
            restart = True
            while restart:
                restart = False
                for org in list(orgs) if orgs else self.store.memory_org_ids():
                    cursor: dict[str, Any] | None = None
                    steps = changed = 0
                    state.update(org_id=org, phase="derived")
                    while True:
                        if self._replay_target is not None:
                            state["state"] = "waiting_for_replay"
                            while self._replay_target is not None:
                                if self._stop.is_set():
                                    state.update(state="interrupted", finished_at=now_iso())
                                    return
                                await self._sleep(0.5)
                            state["state"] = "running"
                            restart = True                  # the replay may have changed what was converged already
                            break
                        async with self.store.transaction() as tx:
                            if self._replay_target is not None:
                                continue                    # a replay started while this step waited for the lock
                            derivations, n, cursor = self.aggregator.reaggregate_step(tx, org, cursor, limit=self.reaggregate_batch)
                            self._emit_derived(tx, derivations, now_iso())
                            steps += 1
                            changed += n
                            if cursor is None:
                                tx.audit("mycelic", "aggregation.reaggregate", None,
                                         {"org_id": org, "changed": changed, "steps": steps, "reason": reason})
                        self.metrics.reaggregation_steps.inc()
                        state.update(steps=state["steps"] + 1, changed=state["changed"] + n,
                                     phase=cursor["phase"] if cursor else None)
                        if derivations:
                            self._outbox_wake.set()
                        await asyncio.sleep(0)              # one step at a time: probes and applies run in between
                        if cursor is None:
                            break
                    if restart:
                        break
            if orgs is None:
                async with self.store.transaction() as tx:
                    self._record_derivation_meta(tx)
            state.update(state="done", org_id=None, phase=None, finished_at=now_iso())
            logger.info("re-aggregation (%s) done: %d steps, %d memories changed", reason, state["steps"], state["changed"])
        except asyncio.CancelledError:
            state.update(state="interrupted", finished_at=now_iso())
            raise
        except Exception as exc:
            logger.exception("re-aggregation (%s) failed; the next start retries it", reason)
            state.update(state="failed", error=f"{type(exc).__name__}: {exc}", finished_at=now_iso())

    async def reaggregate(self, body: dict[str, Any], *, remote: str | None = None) -> dict[str, Any]:
        """Start re-aggregating one organization (``org_id``) or, when it is absent or null, all of them; ``started`` is
        False while a job runs."""
        if not isinstance(body, dict):
            raise ValidationError("body must be a JSON object")
        org_id = _s(body, "org_id", max_len=64)
        if org_id is None and body.get("org_id") is not None:
            # an empty string must not silently widen the run to every organization
            raise ValidationError("'org_id' must not be empty (omit it to re-aggregate every organization)")
        if org_id is not None:
            validate_segment(org_id, "org_id")
        started = self._start_reaggregation([org_id] if org_id else None, "requested")
        await self.store.audit("admin", "reaggregate", None, {"org_id": org_id, "started": started}, remote)
        return {"started": started, "org_id": org_id}

    # ------------------------------------------------------------------ expiry
    # An expired note is withdrawn through the log like any retraction, so a rebuild reproduces it without a clock.
    # Aggregation never reads the clock: an expired note counts until its retraction applies (retrieval and
    # verification read it, and hide it or report it at once).
    async def sweep_expired(self, *, now: datetime | None = None) -> int:
        """Queue one ``memory.retracted`` event (reason ``expired``) for each of up to ``expiry_batch`` active notes whose
        ``expires_at`` is past, in one transaction; returns how many events were new (``now`` is a test hook).

        The page is read after the previous sweep's cursor, which moves past a full page whether or not its events were
        new and wraps around after a short one, so a backlog is covered page by page.  An event id is a function of the
        memory id (``evt_x...``), so a note selected again while its retraction is still in the outbox (a broker outage)
        or after a restored backup gets no second event.  Nothing runs during a replay, which re-delivers what earlier
        sweeps queued.
        """
        if self._replay_target is not None:
            return 0
        cutoff = (now or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds")
        queued = 0
        async with self.store.transaction() as tx:
            rows = self.store.expired_notes(cutoff, after=self._expiry_cursor, limit=self.expiry_batch)
            for _, m in rows:
                event = EventRecord(event_id=f"evt_x{content_hash(m.memory_id, 'expired')[:22]}", kind="memory.retracted",
                                    org_id=m.org_id, agent_id=None, subject=subject_for(m.org_id, "memory.retracted"),
                                    payload={"memory_id": m.memory_id, "reason": "expired", "by": MYCELIC_PRODUCER,
                                             "expires_at": m.expires_at}, created_at=now_iso())
                if tx.insert_event(event):
                    tx.audit("mycelic", "memory.expired", m.memory_id, {"org_id": m.org_id, "expires_at": m.expires_at,
                                                                       "event_id": event.event_id})
                    queued += 1
        self._expiry_cursor = (rows[-1][1].expires_at, rows[-1][0]) if len(rows) >= self.expiry_batch else None
        self._expiry.update(last_sweep_at=now_iso(), last_queued=queued)
        self.metrics.memories_expired.inc(queued)
        if queued:
            self._outbox_wake.set()
        return queued

    async def _expiry_loop(self) -> None:
        while not self._stop.is_set():
            await self._sleep(self.settings.expiry_sweep_seconds)
            if self._stop.is_set():
                break
            try:
                await self.sweep_expired()
            except Exception:
                logger.exception("expiry sweep failed")

    # ------------------------------------------------------------------ health
    async def _transport_info_bounded(self) -> tuple[dict[str, Any], bool]:
        """``transport.info()`` within ``status_timeout``: a stalled broker (SIGSTOP, network black hole) keeps the
        client "connected" while every JetStream request waits for its own 5 s timeout."""
        try:
            return await asyncio.wait_for(self.transport.info(), self.status_timeout), True
        except asyncio.TimeoutError:
            return {"connected": self.transport.connected, "error": f"transport.info() timed out after {self.status_timeout}s"}, False
        except Exception as exc:
            return {"connected": self.transport.connected, "error": f"{type(exc).__name__}: {exc}"}, False

    async def refresh_status(self) -> None:
        """Refresh the snapshot /health and /ready answer from: database stats, then the transport (bounded)."""
        try:
            self._stats = self.store.stats()
            self._db_error = None
        except Exception as exc:
            self._db_error = f"{type(exc).__name__}: {exc}"
        self.metrics.refresh_from_stats(self._stats)
        info, ok = await self._transport_info_bounded()
        if ok:
            self._transport_info = info
            self._status_at = time.monotonic()
            self._status_ok = True
            if "consumer_pending" in info:
                self.metrics.consumer_pending.set(int(info["consumer_pending"]))
        else:
            self._transport_info = {**self._transport_info, "error": info["error"]}
            self._status_ok = False

    async def _status_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh_status()
            except Exception:
                logger.exception("status refresh failed")
            await self._sleep(self.status_interval)

    def transport_check(self) -> dict[str, Any]:
        """The last transport snapshot with ``connected`` read live (a killed broker shows at once) and its age.
        ``stale``: never refreshed, the last refresh failed or timed out, or the status task stopped refreshing."""
        age = None if self._status_at is None else time.monotonic() - self._status_at
        stale = age is None or not self._status_ok or age > 3 * self.status_interval
        return {**self._transport_info, "connected": self.transport.connected, "stale": stale,
                "age_seconds": None if age is None else round(age, 1)}

    def _db_check(self) -> dict[str, Any]:
        error = self._db_error if self.store.is_open else "the database is closed"
        check: dict[str, Any] = {"ok": error is None, "path": self.store.db_path}
        if error is not None:
            check["error"] = error
        return check

    def _dead_loops(self) -> list[str]:
        """Background loops that ended although the service was not stopped (each loop catches its own errors, so only a
        defect gets here): /health then answers 503, so the liveness probe restarts the process.  The re-aggregation job
        is not a loop: it ends when it is done."""
        return sorted(t.get_name() for t in self._tasks
                      if t.done() and t is not self._reaggregate_task and t.get_name() != "mycelic-reaggregate"
                      and not self._stop.is_set())

    def _status(self, db: dict[str, Any], tinfo: dict[str, Any]) -> str:
        if not db["ok"] or self._dead_loops():
            return "failing"
        # a readiness block is degraded, never failing: /health stays 200, so the probes on it never restart the pod;
        # so is a transport error (the stream missing after a broker state reset, say) and a publish that keeps failing
        if (not tinfo["connected"] or tinfo["stale"] or not self._consumer_running or not self._publisher_running
                or self._ready_block is not None or tinfo.get("error") or self._publish_error is not None):
            return "degraded"
        return "ok"

    async def health(self, *, live: bool = False) -> dict[str, Any]:
        """Service health from the status snapshot: no database query and no broker call, so a probe answers at
        once whatever the broker does.  ``live=True`` (``/admin/status``) refreshes the snapshot first, bounded by
        ``status_timeout`` per broker call."""
        if live:
            await self.refresh_status()
        stats = self._stats
        tinfo = self.transport_check()
        checks: dict[str, Any] = {"db": self._db_check(), "transport": tinfo}
        checks["publisher"] = {"running": self._publisher_running, "outbox_pending": stats.get("outbox_pending")}
        if self._publish_error is not None:
            checks["publisher"]["last_error"] = self._publish_error[0]
            checks["publisher"]["failing_seconds"] = round(time.monotonic() - self._publish_error[1], 1)
        dead = self._dead_loops()
        if dead:
            checks["dead_loops"] = dead
        checks["consumer"] = {"running": self._consumer_running, "last_applied_seq": stats.get("last_applied_seq"),
                              "pending": tinfo.get("consumer_pending"), "replaying_to_seq": self._replay_target,
                              "ready_block": self._ready_block}
        checks["reaggregation"] = dict(self._reaggregation)
        checks["expiry"] = {"enabled": self.settings.expiry_sweep_seconds > 0,
                            "interval_seconds": self.settings.expiry_sweep_seconds, "overdue": stats.get("expiry_overdue"),
                            **self._expiry}
        return {"status": self._status(checks["db"], tinfo), "version": VERSION, "instance": self.settings.instance_id, "started_at": self.started_at,
                "checks": checks, "stats": stats}

    async def ready(self) -> tuple[bool, dict[str, Any]]:
        """Readiness: database open, background loops running, no replay in progress, and no replay that rejected the
        signature of more than MYCELIC_REPLAY_MAX_REJECT_RATIO of its events (``reason`` then; see
        :meth:`_finish_replay_in_tx`).  From memory only, like /health: no database query and no broker call.

        A broker outage does *not* make the service unready by default: the outbox exists precisely so agents can
        keep writing through one.  Set ``MYCELIC_READY_REQUIRES_NATS=true`` to change that.
        """
        db = self._db_check()
        tinfo = self.transport_check()
        ok = (db["ok"] and self._consumer_running and self._publisher_running and self._replay_target is None
              and self._ready_block is None)
        if self.settings.ready_requires_nats and not tinfo["connected"]:
            ok = False
        detail = {"ready": ok, "status": self._status(db, tinfo), "replaying_to_seq": self._replay_target,
                  "transport_connected": tinfo["connected"]}
        if self._ready_block is not None:
            detail["reason"] = READY_BLOCK_REASON
        return ok, detail

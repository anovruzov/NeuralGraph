"""The holder-local durable queue: idempotency, leases, retries with backoff, dead letters, priority classes and
backpressure (live is served before backfill)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from mycelic.ingest.events import CanonicalEvent
from mycelic.ingest.queue import IngestQueue, LostLease, StaleCheckpoint, backoff_seconds
from mycelic.ingest.service import IngestService

from .ingest_support import TENANT, connect_export, make_pipeline, make_store, write_jsonl

PAST = "2000-01-01T00:00:00.000000+00:00"


def ev(i: int, *, kind: str = "message", obj: str | None = None, updated: str | None = None) -> CanonicalEvent:
    return CanonicalEvent.create(kind=kind, tenant_id=TENANT, holder_id="hold_q", connector_id="con_q", source_app="app", source_account_id="acct",
                                 source_object_type="message", source_object_id=obj or f"o{i}", observed_at="2026-01-01T00:00:00Z",
                                 created_at="2026-01-01T00:00:00Z", updated_at=updated, body="" if kind == "deletion" else f"body {i}")


@pytest.fixture
async def q(tmp_path: Path):
    from mycelic.ingest.store import IngestStore
    store = make_store(tmp_path, "q")
    queue = IngestQueue(store.store)
    await store.store.run_in_tx(lambda c: IngestStore(store.store).insert_connector_sync(c, {
        "connector_id": "con_q", "connector_type": "local_export", "source_app": "app", "manifest_version": "1.0.0", "display_name": "q",
        "source_account_id": "acct", "created_by": "usr"}))
    yield queue
    await store.close()


def make_due(queue: IngestQueue) -> None:
    queue.conn.execute("UPDATE ingest_queue SET available_at=? WHERE status='queued'", (PAST,))


async def test_enqueue_is_idempotent_on_event_key(q: IngestQueue) -> None:
    first = await q.enqueue("con_q", "s", [ev(1), ev(2)])
    again = await q.enqueue("con_q", "s", [ev(1), ev(2), ev(3)])
    assert (first.enqueued, first.duplicates) == (2, 0) and (again.enqueued, again.duplicates) == (1, 2)
    assert q.conn.execute("SELECT COUNT(*) FROM ingest_queue").fetchone()[0] == 3


async def test_commit_page_is_atomic_and_fenced(q: IngestQueue) -> None:
    res = await q.commit_page("con_q", "incr:x", [ev(1)], None, priority_class="live", expected_version=0)
    assert res.version == 1
    with pytest.raises(StaleCheckpoint):
        await q.commit_page("con_q", "incr:x", [ev(2)], None, priority_class="live", expected_version=0)   # a zombie runner
    assert q.conn.execute("SELECT COUNT(*) FROM ingest_queue").fetchone()[0] == 1                        # its page rolled back


async def test_retry_with_backoff_then_dead_letter(q: IngestQueue) -> None:
    await q.enqueue("con_q", "s", [ev(1)])
    q.conn.execute("UPDATE ingest_queue SET max_attempts=3")
    outcomes = []
    for attempt in range(3):
        make_due(q)
        [item] = await q.lease("w1")
        assert item.attempts == attempt + 1
        outcomes.append(await q.fail(item.item_id, "w1", "transient"))
        if outcomes[-1] == "queued":
            row = q.conn.execute("SELECT available_at, status, last_error_code FROM ingest_queue").fetchone()
            assert row["status"] == "queued" and row["available_at"] > PAST and row["last_error_code"] == "transient"
            assert await q.lease("w1") == []                      # backoff: not available yet
    assert outcomes == ["queued", "queued", "dead"]
    dead = q.dead_letters_sync()
    assert len(dead) == 1 and dead[0]["last_error_code"] == "transient" and "body" not in str(dead)
    assert await q.retry_dead([dead[0]["item_id"]]) == 1
    [item] = await q.lease("w2")
    assert item.attempts == 1
    assert await q.fail(item.item_id, "w2", "bad_payload", retryable=False) == "dead"
    assert await q.discard_dead([item.item_id]) == 1
    assert q.conn.execute("SELECT payload FROM ingest_queue").fetchone()[0] is None    # content leaves with a discard
    assert 4 <= backoff_seconds(1) <= 6 and backoff_seconds(30) <= 900 * 1.2


async def test_rate_limits_park_without_consuming_an_attempt(q: IngestQueue) -> None:
    await q.enqueue("con_q", "s", [ev(1)])
    [item] = await q.lease("w1")
    assert await q.park(item.item_id, "w1", seconds=60) == "queued"
    row = q.conn.execute("SELECT attempts, status FROM ingest_queue").fetchone()
    assert (row["attempts"], row["status"]) == (0, "queued")


async def test_expired_leases_are_swept_and_zombies_cannot_ack(q: IngestQueue) -> None:
    await q.enqueue("con_q", "s", [ev(1)])
    [item] = await q.lease("w1", lease_seconds=0.001)
    q.conn.execute("UPDATE ingest_queue SET leased_until=?", (PAST,))
    assert await q.requeue_expired() == 1
    [again] = await q.lease("w2")
    with pytest.raises(LostLease):
        await q.ack(item.item_id, "w1", "new")
    assert await q.fail(item.item_id, "w1", "x") == "lost"
    await q.ack(again.item_id, "w2", "new")
    row = q.conn.execute("SELECT status, payload FROM ingest_queue").fetchone()
    assert row["status"] == "done" and row["payload"] is None


async def test_deletes_first_then_weighted_fairness(q: IngestQueue) -> None:
    await q.enqueue("con_q", "bf", [ev(i) for i in range(30)], priority_class="backfill")
    await q.enqueue("con_q", "live", [ev(100 + i) for i in range(30)], priority_class="live")
    await q.enqueue("con_q", "del", [ev(200, kind="deletion")], priority_class="live")
    order = []
    for _ in range(19):
        [item] = await q.lease("w")
        order.append(item.priority_class)
        await q.ack(item.item_id, "w", "ok")
    assert order[0] == "delete"                                   # deletions are always served first
    window = Counter(order[1:19])
    assert window["live"] == 16 and window["backfill"] == 2        # 8:1 when both classes have work


async def test_per_record_ordering(q: IngestQueue) -> None:
    newer = ev(1, obj="same", updated="2026-02-02T00:00:00Z")
    older = ev(2, obj="same", updated="2026-02-01T00:00:00Z")
    await q.enqueue("con_q", "s", [newer], priority_class="live")
    await q.enqueue("con_q", "s", [older], priority_class="backfill")
    [first] = await q.lease("w")
    assert first.order_key == older.order_key                     # the older version of a record goes first
    assert await q.lease("w") == []                               # and the newer one waits behind it
    await q.ack(first.item_id, "w", "ok")
    [second] = await q.lease("w")
    assert second.order_key == newer.order_key


async def test_fetch_watermarks_have_hysteresis(tmp_path: Path) -> None:
    store = make_store(tmp_path, "wm")
    q = IngestQueue(store.store, watermarks={"backfill": (5, 2)})
    await q.enqueue("con_q", "s", [ev(i) for i in range(4)], priority_class="backfill")
    assert q.fetch_allowed("backfill")
    await q.enqueue("con_q", "s", [ev(10)], priority_class="backfill")
    assert not q.fetch_allowed("backfill")                        # at the high watermark: stop
    for _ in range(2):
        [it] = await q.lease("w")
        await q.ack(it.item_id, "w", "ok")
    assert not q.fetch_allowed("backfill")                        # between the marks: still stopped
    [it] = await q.lease("w")
    await q.ack(it.item_id, "w", "ok")
    assert q.fetch_allowed("backfill")                            # at the low watermark: resume
    assert q.fetch_allowed("delete")
    await store.close()


async def test_backpressure_serves_live_before_backfill(tmp_path: Path) -> None:
    """A large backfill stops fetching above its watermark (resuming later from the committed cursor), and live items
    enqueued meanwhile are processed before the remaining backfill."""
    store = make_store(tmp_path)
    pipe = make_pipeline(store, watermarks={"backfill": (6, 2)})
    svc = IngestService(pipe)
    hist = write_jsonl(tmp_path / "history.jsonl", [{"type": "message", "id": f"h{i}", "author": "a", "created_at": f"2025-01-{i + 1:02d}T00:00:00Z",
                                                      "text": f"Historical note {i} about the archive."} for i in range(20)],
                       header={"id": "history", "source_type": "channel", "visibility": "public"})
    live = write_jsonl(tmp_path / "live.jsonl", [{"type": "message", "id": f"l{i}", "author": "a", "created_at": "2026-09-01T00:00:00Z",
                                                  "text": f"Live update {i} on today's incident."} for i in range(3)],
                       header={"id": "live", "source_type": "channel", "visibility": "public"})
    bf = await connect_export(svc, [hist], source_app="archive", limits={"page_size": 2, "backfill_window_days": 3650})
    lv = await connect_export(svc, [live], source_app="teamchat")
    first = await pipe.sync(bf["connector_id"], mode="backfill")
    assert first.yielded and first.enqueued == 6                 # stopped at the high watermark
    await pipe.sync(lv["connector_id"])                          # live work arrives meanwhile
    leased = []
    for _ in range(5):
        [it] = await pipe.queue.lease("w")
        leased.append(it.priority_class)
        await pipe.queue.fail(it.item_id, "w", "probe", retryable=True)   # put back; we only observe the order
        make_due(pipe.queue)
    assert leased[:3] == ["live", "live", "live"]
    await pipe.process_available()
    # the backfill resumes from its committed cursor until it is done, with nothing fetched twice into the queue
    for _ in range(10):
        r = await pipe.sync(bf["connector_id"], mode="backfill")
        await pipe.process_available()
        if not r.yielded:
            break
    ids = [r[0] for r in store.store._conn.execute("SELECT source_object_id FROM ingest_records WHERE source_app='archive'")]
    assert sorted(ids) == sorted(f"h{i}" for i in range(20))
    assert store.store._conn.execute("SELECT COUNT(*) FROM ingest_queue WHERE stream LIKE 'backfill:%'").fetchone()[0] == 20
    await store.close()

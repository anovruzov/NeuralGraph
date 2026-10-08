"""Transport tests: ``SqliteTransport`` (deterministic, offline) and ``NatsTransport`` (needs a server).

The SQLite tests use short ``poll_interval`` / ``ack_wait`` values and wait on observable state instead of
sleeping for fixed durations, so they are quick and do not depend on scheduler timing. The NATS tests run only
when ``MYCELIC_TEST_NATS_URL`` points at a JetStream-enabled server (``nats-server -js``) and nats-py is
importable; each uses its own stream name and deletes it afterwards.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Callable

import pytest

from mycelic.db import CoordDB
from mycelic.transport import Envelope, Subjects, TransportError
from mycelic.transport.sqlite_transport import SqliteTransport, subject_matches
from mycelic.util import parse_iso, plus_seconds, token

TENANT = "t1"
POLL = 0.02
ACK_WAIT = 0.1


async def wait_until(pred: Callable[[], bool], *, timeout: float = 5.0, step: float = 0.01) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError("condition not met within %.1fs" % timeout)
        await asyncio.sleep(step)


async def settle(seconds: float = 0.15) -> None:
    """Give consumers several poll intervals to (not) deliver something before asserting a negative."""
    await asyncio.sleep(seconds)


def question(holder: str = "h1", **payload: object) -> Envelope:
    return Envelope.new(Subjects.holder_inbox(TENANT, holder), "question", TENANT, {"text": "q", **payload})


def response(**payload: object) -> Envelope:
    return Envelope.new(Subjects.responses(TENANT), "response", TENANT, payload)


class Recorder:
    """Handler that records envelopes and can be told to fail the first N deliveries of a msg_id
    (every msg_id, or only those in ``fail_only``)."""

    def __init__(self, fail_first: int = 0, fail_only: set[str] | None = None) -> None:
        self.seen: list[Envelope] = []
        self.attempts: dict[str, int] = {}
        self.fail_first = fail_first
        self.fail_only = fail_only

    async def __call__(self, env: Envelope) -> None:
        n = self.attempts.get(env.msg_id, 0) + 1
        self.attempts[env.msg_id] = n
        if n <= self.fail_first and (self.fail_only is None or env.msg_id in self.fail_only):
            raise RuntimeError(f"simulated failure #{n} for {env.msg_id}")
        self.seen.append(env)

    @property
    def msg_ids(self) -> list[str]:
        return [e.msg_id for e in self.seen]


@pytest.fixture
async def transport(db: CoordDB) -> SqliteTransport:
    t = SqliteTransport(db, poll_interval=POLL)
    await t.start()
    yield t
    await t.close()


# ---------------------------------------------------------------------------- unit: subject matching
def test_subject_matching_follows_nats_rules() -> None:
    assert subject_matches("mycelic.t1.responses", "mycelic.t1.responses")
    assert not subject_matches("mycelic.t1.responses", "mycelic.t1.responses.extra")
    assert subject_matches("mycelic.t1.>", "mycelic.t1.responses")
    assert subject_matches("mycelic.t1.>", "mycelic.t1.holder.h1.inbox")
    assert not subject_matches("mycelic.t1.>", "mycelic.t1")
    assert not subject_matches("mycelic.t1.>", "mycelic.t10.responses")
    assert subject_matches("mycelic.*.holder.h1.>", "mycelic.t1.holder.h1.raw")
    assert not subject_matches("mycelic.*.holder.h1.>", "mycelic.t1.holder.h2.raw")
    assert subject_matches("mycelic.*.responses", "mycelic.t9.responses")
    assert not subject_matches("mycelic.*.responses", "mycelic.t9.x.responses")


# ---------------------------------------------------------------------------- publish
async def test_publish_dedupes_on_msg_id(transport: SqliteTransport, db: CoordDB) -> None:
    env = question()
    assert await transport.publish(env) is True
    assert await transport.publish(env) is False
    copy = Envelope.new(env.subject, env.kind, TENANT, {"text": "different body, same id"}, msg_id=env.msg_id)
    assert await transport.publish(copy) is False
    assert db.scalar("SELECT COUNT(*) FROM transport_messages WHERE msg_id = ?", (env.msg_id,)) == 1
    row = db.one("SELECT published_at, expires_at FROM transport_messages WHERE msg_id = ?", (env.msg_id,))
    assert row["expires_at"] > row["published_at"]
    stats = await transport.stats()
    assert stats["messages"] == 1 and stats["transport"] == "sqlite"


# ---------------------------------------------------------------------------- durable delivery
async def test_durable_consumer_receives_messages_published_before_and_after_subscribe(transport: SqliteTransport, db: CoordDB) -> None:
    before = [question(n=i) for i in range(3)]
    for env in before:
        await transport.publish(env)
    rec = Recorder()
    subject = Subjects.holder_inbox(TENANT, "h1")
    sub = await transport.subscribe(subject, consumer="holder-h1", handler=rec, ack_wait=ACK_WAIT)
    await wait_until(lambda: len(rec.seen) == 3)
    after = [question(n=i) for i in range(3, 6)]
    for env in after:
        await transport.publish(env)
    await wait_until(lambda: len(rec.seen) == 6)
    assert rec.msg_ids == [e.msg_id for e in before + after]          # in publish order
    assert all(e.payload["text"] == "q" and e.tenant_id == TENANT for e in rec.seen)
    assert sub.delivered == 6 and sub.failed == 0
    last_id = db.scalar("SELECT MAX(id) FROM transport_messages")
    await wait_until(lambda: transport.cursor("holder-h1", subject) == last_id)
    assert db.scalar("SELECT COUNT(*) FROM transport_processed WHERE consumer = 'holder-h1'") == 6
    # subscribing again with the same consumer name is idempotent: same handle, no second task
    assert await transport.subscribe(subject, consumer="holder-h1", handler=rec, ack_wait=ACK_WAIT) is sub


async def test_wildcard_prefix_subscription(transport: SqliteTransport) -> None:
    rec = Recorder()
    await transport.subscribe(Subjects.core_inbound(TENANT), consumer="core-t1", handler=rec, ack_wait=ACK_WAIT)
    wanted = [response(a=1), Envelope.new(Subjects.evidence_events(TENANT), "evidence_event", TENANT, {}),
              Envelope.new(Subjects.holder_inbox(TENANT, "h1"), "question", TENANT, {})]
    other_tenant = Envelope.new(Subjects.responses("t10"), "response", "t10", {})    # shares the byte prefix "mycelic.t1"
    bare = Envelope.new("mycelic.t1", "control", TENANT, {})                          # '>' needs at least one more token
    for env in [wanted[0], other_tenant, wanted[1], bare, wanted[2]]:
        await transport.publish(env)
    await wait_until(lambda: len(rec.seen) == 3)
    await settle()
    assert rec.msg_ids == [e.msg_id for e in wanted]


async def test_star_wildcard_subscription(transport: SqliteTransport) -> None:
    rec = Recorder()
    await transport.subscribe("mycelic.*.responses", consumer="all-responses", handler=rec, ack_wait=ACK_WAIT)
    a = Envelope.new(Subjects.responses("t1"), "response", "t1", {})
    b = Envelope.new(Subjects.responses("t2"), "response", "t2", {})
    c = Envelope.new("mycelic.t1.holder.h1.responses", "response", "t1", {})
    for env in (a, b, c):
        await transport.publish(env)
    await wait_until(lambda: len(rec.seen) == 2)
    await settle()
    assert rec.msg_ids == [a.msg_id, b.msg_id]


# ---------------------------------------------------------------------------- failure and redelivery
async def test_handler_failure_is_redelivered_after_ack_wait_without_advancing_cursor(transport: SqliteTransport, db: CoordDB) -> None:
    subject = Subjects.holder_inbox(TENANT, "h1")
    first, second = question(n=1), question(n=2)
    rec = Recorder(fail_first=2, fail_only={first.msg_id})
    await transport.publish(first)
    await transport.publish(second)
    sub = await transport.subscribe(subject, consumer="holder-h1", handler=rec, ack_wait=ACK_WAIT)
    await wait_until(lambda: rec.attempts.get(first.msg_id) == 1)
    assert transport.cursor("holder-h1", subject) == 0              # nothing acknowledged
    assert not transport.is_processed("holder-h1", first.msg_id)
    assert rec.attempts.get(second.msg_id) is None                   # head-of-line: later messages wait
    await wait_until(lambda: rec.attempts.get(first.msg_id) == 2)
    assert transport.cursor("holder-h1", subject) == 0
    await wait_until(lambda: rec.msg_ids == [first.msg_id, second.msg_id])
    assert rec.attempts[first.msg_id] == 3 and rec.attempts[second.msg_id] == 1
    await wait_until(lambda: sub.delivered == 2)
    assert sub.failed == 2
    await wait_until(lambda: transport.cursor("holder-h1", subject) == db.scalar("SELECT MAX(id) FROM transport_messages"))


async def test_processed_table_prevents_double_handling_after_crash(transport: SqliteTransport, db: CoordDB) -> None:
    """The handler's own store is keyed by msg_id, so a redelivery is harmless; and a processed marker whose
    cursor advance was lost (crash between the handler's commit and the transport's) stops a second delivery."""
    handled: dict[str, int] = {}                                     # the handler's idempotent effect store

    async def handler(env: Envelope) -> None:
        first_time = env.msg_id not in handled
        handled[env.msg_id] = handled.get(env.msg_id, 0) + 1
        if first_time and env.payload.get("crash"):
            raise RuntimeError("crash after recording the effect")

    subject = Subjects.holder_inbox(TENANT, "h1")
    crashy = question(crash=True)
    await transport.publish(crashy)
    sub = await transport.subscribe(subject, consumer="holder-h1", handler=handler, ack_wait=ACK_WAIT)
    await wait_until(lambda: transport.is_processed("holder-h1", crashy.msg_id))
    await wait_until(lambda: sub.delivered == 1)
    assert handled[crashy.msg_id] == 2 and sub.failed == 1
    await sub.close()

    # simulate a consumer whose cursor was rebuilt (or a second process) while the marker survived
    marked = question(n="already handled elsewhere")
    fresh = question(n="new")
    await transport.publish(marked)
    await transport.publish(fresh)
    async with db.tx() as c:
        c.execute("INSERT INTO transport_processed(consumer, msg_id, processed_at) VALUES (?, ?, ?)", ("holder-h1", marked.msg_id, "2000-01-01T00:00:00+00:00"))
        c.execute("UPDATE transport_cursors SET last_id = 0 WHERE consumer = ? AND subject = ?", ("holder-h1", subject))
    await transport.subscribe(subject, consumer="holder-h1", handler=handler, ack_wait=ACK_WAIT)
    await wait_until(lambda: fresh.msg_id in handled)
    await settle()
    assert handled[crashy.msg_id] == 2                               # still exactly the two attempts from before
    assert marked.msg_id not in handled
    assert handled[fresh.msg_id] == 1
    assert transport.cursor("holder-h1", subject) == db.scalar("SELECT MAX(id) FROM transport_messages")


async def test_resubscribe_with_same_consumer_reuses_cursor(transport: SqliteTransport) -> None:
    subject = Subjects.holder_inbox(TENANT, "h1")
    first, second = question(n=1), question(n=2)
    await transport.publish(first)
    rec1 = Recorder()
    sub = await transport.subscribe(subject, consumer="holder-h1", handler=rec1, ack_wait=ACK_WAIT)
    await wait_until(lambda: rec1.msg_ids == [first.msg_id])
    await wait_until(lambda: transport.cursor("holder-h1", subject) == 1)
    await sub.close()
    await transport.publish(second)
    rec2 = Recorder()
    await transport.subscribe(subject, consumer="holder-h1", handler=rec2, ack_wait=ACK_WAIT)
    await wait_until(lambda: rec2.msg_ids == [second.msg_id])
    await settle()
    assert rec2.msg_ids == [second.msg_id] and rec1.msg_ids == [first.msg_id]


async def test_two_consumers_have_independent_cursors(transport: SqliteTransport) -> None:
    subject = Subjects.responses(TENANT)
    envs = [response(n=i) for i in range(3)]
    for env in envs:
        await transport.publish(env)
    fast, slow = Recorder(), Recorder(fail_first=1)
    await transport.subscribe(subject, consumer="core-a", handler=fast, ack_wait=ACK_WAIT)
    await transport.subscribe(subject, consumer="core-b", handler=slow, ack_wait=0.5)
    await wait_until(lambda: len(fast.seen) == 3)
    await wait_until(lambda: transport.cursor("core-a", subject) == 3)
    assert transport.cursor("core-b", subject) < 3                    # b is still retrying its first message
    await wait_until(lambda: len(slow.seen) == 3, timeout=10)
    assert fast.msg_ids == slow.msg_ids == [e.msg_id for e in envs]
    stats = await transport.stats()
    assert {c["consumer"] for c in stats["cursors"]} == {"core-a", "core-b"}
    assert all(c["lag"] == 0 for c in stats["cursors"])


# ---------------------------------------------------------------------------- request / reply
async def test_request_reply_round_trip(transport: SqliteTransport) -> None:
    async def responder(env: Envelope) -> None:
        assert env.reply_to and env.reply_to.startswith("_INBOX.")
        await transport.reply(env, {"excerpt": "policy-approved text", "ref_id": env.payload["ref_id"]}, kind="raw_reply")

    await transport.subscribe(Subjects.holder_raw(TENANT, "h1"), consumer="holder-h1-raw", handler=responder, ack_wait=ACK_WAIT)
    req = Envelope.new(Subjects.holder_raw(TENANT, "h1"), "raw_request", TENANT, {"ref_id": "ref_1"})
    rep = await transport.request(req, timeout=5.0)
    assert rep.kind == "raw_reply" and rep.payload == {"excerpt": "policy-approved text", "ref_id": "ref_1"}
    assert rep.subject == req.reply_to and rep.headers["in_reply_to"] == req.msg_id and rep.tenant_id == TENANT
    # the raw reply was consumed by its reader: its text is not kept in the coordination DB
    assert transport.db.one("SELECT 1 FROM transport_messages WHERE subject=?", (req.reply_to,)) is None
    # the request itself (no content) is short-lived
    exp = transport.db.one("SELECT published_at, expires_at FROM transport_messages WHERE msg_id=?", (req.msg_id,))
    assert (parse_iso(exp["expires_at"]) - parse_iso(exp["published_at"])).total_seconds() <= 301


async def test_request_retry_finds_the_original_reply(transport: SqliteTransport) -> None:
    async def responder(env: Envelope) -> None:
        await transport.reply(env, {"stats": 1}, kind="control_reply")

    await transport.subscribe(Subjects.holder_control(TENANT, "h1"), consumer="holder-h1-ctl", handler=responder, ack_wait=ACK_WAIT)
    req = Envelope.new(Subjects.holder_control(TENANT, "h1"), "control", TENANT, {"action": "stats"})
    rep = await transport.request(req, timeout=5.0)
    # a retried request (same msg_id) is deduplicated and still finds the reply of the original
    again = Envelope.new(req.subject, req.kind, TENANT, req.payload, msg_id=req.msg_id)
    rep2 = await transport.request(again, timeout=1.0)
    assert rep2.msg_id == rep.msg_id


async def test_handled_content_is_scrubbed_and_reply_to_is_signed(transport: SqliteTransport) -> None:
    got: list[Envelope] = []

    async def holder(env: Envelope) -> None:
        got.append(env)

    observed: list[Envelope] = []

    async def core_wildcard(env: Envelope) -> None:
        observed.append(env)

    # a wildcard observer (the core's consumer) handles it too, but only the destination's handling removes the content
    await transport.subscribe("mycelic.>", consumer="core-inbound", handler=core_wildcard, ack_wait=ACK_WAIT)
    await transport.subscribe(Subjects.holder_ingest(TENANT, "h1"), consumer="holder-h1-ingest", handler=holder, ack_wait=ACK_WAIT, content_owner=True)
    await transport.publish(Envelope.new(Subjects.holder_ingest(TENANT, "h1"), "ingest", TENANT, {"doc_id": "d1", "text": "confidential roster"}, msg_id="ing-1"))
    for _ in range(100):
        if got:
            break
        await asyncio.sleep(0.02)
    assert got and got[0].payload["text"] == "confidential roster"
    for _ in range(50):
        row = transport.db.one("SELECT payload FROM transport_messages WHERE msg_id='ing-1'")
        if "confidential" not in row["payload"]:
            break
        await asyncio.sleep(0.02)
    assert "confidential" not in row["payload"] and json.loads(row["payload"])["payload"] == {"scrubbed": True, "doc_id": "d1"}
    signed = Envelope.new("s", "raw_request", TENANT, {"ref_id": "r"}, msg_id="m1")
    signed.reply_to = "_INBOX.a"
    signed.sign("k")
    signed.reply_to = "_INBOX.attacker"
    assert not signed.verify("k")


async def test_request_without_responder_times_out(transport: SqliteTransport) -> None:
    req = Envelope.new(Subjects.holder_raw(TENANT, "nobody"), "raw_request", TENANT, {})
    with pytest.raises(TransportError):
        await transport.request(req, timeout=0.1)
    with pytest.raises(TransportError):
        await transport.reply(Envelope.new("x", "k", TENANT, {}), {})   # no reply_to


# ---------------------------------------------------------------------------- retention
async def test_prune_removes_expired_rows(db: CoordDB) -> None:
    t = SqliteTransport(db, retention_seconds=3600, poll_interval=POLL)
    old, new = question(n="old"), question(n="new")
    await t.publish(old)
    async with db.tx() as c:
        c.execute("UPDATE transport_messages SET published_at = ?, expires_at = ? WHERE msg_id = ?",
                  (plus_seconds(-7200), plus_seconds(-3600), old.msg_id))
        c.execute("INSERT INTO transport_processed(consumer, msg_id, processed_at) VALUES ('c', ?, ?)", (old.msg_id, plus_seconds(-7000)))
        c.execute("INSERT INTO transport_processed(consumer, msg_id, processed_at) VALUES ('c', ?, ?)", ("msg_recent", plus_seconds(-10)))
    await t.publish(new)
    assert (await t.stats())["expired"] == 1
    assert await t.prune() == 1
    assert [r["msg_id"] for r in db.all("SELECT msg_id FROM transport_messages")] == [new.msg_id]
    assert [r["msg_id"] for r in db.all("SELECT msg_id FROM transport_processed")] == ["msg_recent"]
    assert await t.prune() == 0
    # the `now` override makes the window deterministic without touching rows
    assert await t.prune(now=plus_seconds(3601)) == 1
    assert db.scalar("SELECT COUNT(*) FROM transport_messages") == 0
    await t.close()


# ---------------------------------------------------------------------------- lifecycle
async def test_close_cancels_consumer_tasks(db: CoordDB) -> None:
    t = SqliteTransport(db, poll_interval=POLL)
    await t.start()
    subs = [await t.subscribe(Subjects.responses(TENANT), consumer=f"c{i}", handler=Recorder(), ack_wait=ACK_WAIT) for i in range(3)]
    await asyncio.sleep(0.05)
    assert len(db._wakers) == 3
    await t.close()
    assert all(s._task.done() for s in subs)
    assert db._wakers == []
    assert (await t.stats())["subscriptions"] == []
    with pytest.raises(TransportError):
        await t.publish(question())
    # a closed transport can be started again
    await t.start()
    assert await t.publish(question()) is True
    await t.close()


async def test_second_process_on_the_same_file_sees_messages(tmp_path: Path) -> None:
    """Two CoordDB instances on one coord.db stand in for two processes: the waker is in-process only, so
    delivery across them relies on polling and SQLite's WAL."""
    path = tmp_path / "shared.db"
    db_a, db_b = CoordDB(path), CoordDB(path)
    publisher, consumer = SqliteTransport(db_a, poll_interval=POLL), SqliteTransport(db_b, poll_interval=POLL)
    rec = Recorder()
    try:
        await consumer.subscribe(Subjects.responses(TENANT), consumer="core", handler=rec, ack_wait=ACK_WAIT)
        env = response(from_process="a")
        await publisher.publish(env)
        await wait_until(lambda: rec.msg_ids == [env.msg_id])
        await wait_until(lambda: db_a.scalar("SELECT last_id FROM transport_cursors WHERE consumer = 'core'") == 1)
    finally:
        await consumer.close()
        await publisher.close()
        await db_a.close()
        await db_b.close()


# ---------------------------------------------------------------------------- NATS (integration, opt-in)
NATS_URL = os.environ.get("MYCELIC_TEST_NATS_URL", "")
try:
    import nats  # noqa: F401
    HAVE_NATS = True
except ImportError:
    HAVE_NATS = False

nats_only = pytest.mark.skipif(not (NATS_URL and HAVE_NATS), reason="set MYCELIC_TEST_NATS_URL to a JetStream server and install nats-py")


@nats_only
async def test_nats_publish_subscribe_request_round_trip() -> None:
    from mycelic.transport.nats_transport import NatsTransport, consumer_name

    assert consumer_name("core.t1/inbound x") == "core_t1_inbound_x"
    stream = "MYCELIC_TEST_" + token(6).replace("-", "_").replace("_", "").upper()[:10]
    t = NatsTransport(NATS_URL, stream=stream, retention_seconds=600, fetch_timeout=0.3)
    await t.start()
    try:
        assert t.duplicate_window == 120
        env = question(n=1)
        assert await t.publish(env) is True
        assert await t.publish(env) is False                         # Nats-Msg-Id dedupe
        rec = Recorder(fail_first=1)
        subject = Subjects.holder_inbox(TENANT, "h1")
        sub = await t.subscribe(subject, consumer="holder.h1", handler=rec, ack_wait=0.5)
        assert await t.subscribe(subject, consumer="holder.h1", handler=rec, ack_wait=0.5) is sub
        await wait_until(lambda: rec.msg_ids == [env.msg_id], timeout=10)
        assert rec.attempts[env.msg_id] == 2 and sub.failed == 1 and sub.delivered == 1
        later = question(n=2)
        await t.publish(later)
        await wait_until(lambda: rec.msg_ids == [env.msg_id, later.msg_id], timeout=10)

        async def responder(req: Envelope) -> None:
            await t.reply(req, {"echo": req.payload}, kind="raw_reply")

        await t.subscribe(Subjects.holder_raw(TENANT, "h1"), consumer="holder.h1.raw", handler=responder, ack_wait=5)
        req = Envelope.new(Subjects.holder_raw(TENANT, "h1"), "raw_request", TENANT, {"ref_id": "r1"})
        rep = await t.request(req, timeout=10)
        assert rep.kind == "raw_reply" and rep.payload == {"echo": {"ref_id": "r1"}} and rep.headers["in_reply_to"] == req.msg_id
        with pytest.raises(TransportError):
            await t.request(Envelope.new(Subjects.holder_raw(TENANT, "nobody"), "raw_request", TENANT, {}), timeout=0.3)
        stats = await t.stats()
        assert stats["stream"] == stream and stats["messages"] >= 3 and stats["connected"]
        assert stats["max_age_seconds"] == 600 and stats["duplicate_window_seconds"] == 120
        assert await t.prune() == 0
    finally:
        with contextlib.suppress(Exception):
            await t.delete_stream()
        await t.close()


@nats_only
async def test_nats_durable_consumer_is_shared_across_processes() -> None:
    from mycelic.transport.nats_transport import NatsTransport

    stream = "MYCELIC_TEST_" + token(6).replace("-", "_").replace("_", "").upper()[:10]
    first = NatsTransport(NATS_URL, stream=stream, retention_seconds=600, fetch_timeout=0.3)
    second = NatsTransport(NATS_URL, stream=stream, retention_seconds=600, manage_stream=False, fetch_timeout=0.3)
    await first.start()
    try:
        rec1 = Recorder()
        subject = Subjects.responses(TENANT)
        a = response(n=1)
        await first.publish(a)
        await first.subscribe(subject, consumer="core", handler=rec1, ack_wait=1)
        await wait_until(lambda: rec1.msg_ids == [a.msg_id], timeout=10)
        await first.close()
        # a second process binding the same durable only sees what was published after the ack
        await second.start()
        b = response(n=2)
        await second.publish(b)
        rec2 = Recorder()
        await second.subscribe(subject, consumer="core", handler=rec2, ack_wait=1)
        await wait_until(lambda: rec2.msg_ids == [b.msg_id], timeout=10)
        await settle(0.5)
        assert rec2.msg_ids == [b.msg_id]
        with pytest.raises(TransportError):
            await NatsTransport(NATS_URL, stream=stream + "X", retention_seconds=600, manage_stream=False).start()
        with pytest.raises(TransportError):
            await NatsTransport("nats://127.0.0.1:1", retention_seconds=600, connect_timeout=1).start()   # nothing listens
        with contextlib.suppress(Exception):
            await second.delete_stream()
    finally:
        await second.close()
        await first.close()


@nats_only
async def test_nats_connector_control_reaches_an_external_holder_sealed_and_leaves_no_copy(tmp_path) -> None:
    """The coordinator asks an external holder to connect an app over JetStream: the token travels sealed for that
    holder, the holder opens it once, answers on the reply subject, and the request is deleted from the stream."""
    from mycelic.evidence import EvidenceStore
    from mycelic.holder.service import HolderService
    from mycelic.ingest.crypto import seal_transfer
    from mycelic.transport.nats_transport import NatsTransport

    class Runtime:
        def __init__(self) -> None:
            self.creds = None

        async def control(self, action, params, *, actor, credentials=None):
            self.creds = credentials
            return {"action": action, "actor": actor}

        async def on_notice(self, payload):
            return {}

    stream = "MYCELIC_TEST_" + token(6).replace("-", "_").replace("_", "").upper()[:10]
    core = NatsTransport(NATS_URL, stream=stream, retention_seconds=600, fetch_timeout=0.3)
    holder_side = NatsTransport(NATS_URL, stream=stream, retention_seconds=600, manage_stream=False, fetch_timeout=0.3)
    await core.start()
    await holder_side.start()
    store = EvidenceStore(tmp_path / "h.db", holder_id="hold_x", tenant_id=TENANT)
    svc = HolderService(store, holder_side, holder_id="hold_x", tenant_id=TENANT, route_key="rk-x", ingest=Runtime(), transport_heartbeat=False)
    try:
        await svc.start()
        env = Envelope.new(Subjects.holder_control(TENANT, "hold_x"), "connector_control", TENANT,
                           {"action": "connector.add", "actor": "usr_ana", "params": {"connector_type": "github"}}, msg_id="cc_nats_1")
        env.payload["credentials_sealed"] = seal_transfer("rk-x", "hold_x", "cc_nats_1", {"kind": "pat", "access_token": "ghp_" + "b" * 36})
        rep = await core.request(env, timeout=10, sign_key="rk-x")
        assert rep.kind == "connector_reply" and rep.payload.get("action") == "connector.add" and rep.payload.get("actor") == "usr_ana"
        assert svc.ingest.creds["access_token"].startswith("ghp_")
        # the request (sealed token included) is gone from the stream once the holder handled it
        await asyncio.sleep(0.5)
        info = await core._js.stream_info(stream)
        found = []
        for seq in range(info.state.first_seq, info.state.last_seq + 1):
            with contextlib.suppress(Exception):
                m = await core._js.get_msg(stream, seq)
                found.append(m.data)
        assert not any(b"credentials_sealed" in (d or b"") for d in found)
    finally:
        await svc.stop()
        await store.close()
        with contextlib.suppress(Exception):
            await core.delete_stream()
        await holder_side.close()
        await core.close()

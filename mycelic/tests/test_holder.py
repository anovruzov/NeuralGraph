"""HolderService / EmbeddedHolders / standalone process tests over the transport.

Uses ``mycelic.transport.sqlite_transport.SqliteTransport`` when it exists and an in-memory ``Transport`` stub with
the same contract otherwise. Everything is offline and deterministic.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from NeuralGraph.chat_memory.llm import FakeLLMClient
from mycelic.evidence import EvidenceStore
from mycelic.holder import EmbeddedHolders, HolderService
from mycelic.holder.process import build_local_app, run_holder
from mycelic.holder.service import heartbeat_msg_id, manual_response_msg_id
from mycelic.transport.base import Envelope, Subjects, Subscription, TransportError
from mycelic.util import new_id

from .test_evidence import OPS_LOG, ROBOTS_2026, StubRouter, question

try:
    from mycelic.transport.sqlite_transport import SqliteTransport
except ImportError:   # not written yet
    SqliteTransport = None

TENANT = "ten_test"
HOLDER = "hold_h1"
ROUTE_KEY = "route-secret-key"


class MemoryTransport:
    """In-memory stand-in with the ``Transport`` contract: msg_id de-duplication, wildcard subjects, at-least-once
    delivery (a raising handler is retried), request/reply on private ``_INBOX`` subjects."""

    name = "memory"

    def __init__(self) -> None:
        self._subs: list[tuple[str, str, Callable[[Envelope], Any]]] = []
        self._seen: set[str] = set()
        self.log: list[Envelope] = []
        self._waiters: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    @staticmethod
    def _matches(pattern: str, subject: str) -> bool:
        return subject == pattern or (pattern.endswith(".>") and subject.startswith(pattern[:-1]))

    async def publish(self, env: Envelope) -> bool:
        if env.msg_id in self._seen:
            return False
        self._seen.add(env.msg_id)
        self.log.append(env)
        fut = self._waiters.get(env.subject)
        if fut is not None and not fut.done():
            fut.set_result(env)
            return True
        for pattern, _consumer, handler in self._subs:
            if self._matches(pattern, env.subject):
                t = asyncio.create_task(self._deliver(handler, Envelope.from_dict(env.to_dict())))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)
        return True

    async def _deliver(self, handler: Callable[[Envelope], Any], env: Envelope) -> None:
        for _ in range(3):
            try:
                await handler(env)
                return
            except Exception:
                await asyncio.sleep(0.02)

    async def subscribe(self, subject: str, *, consumer: str, handler: Callable[[Envelope], Any], ack_wait: float = 60.0) -> Subscription:
        self._subs.append((subject, consumer, handler))
        return Subscription(subject, consumer)

    async def request(self, env: Envelope, *, timeout: float = 10.0) -> Envelope:
        env.reply_to = env.reply_to or f"_INBOX.{new_id('rq')}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[env.reply_to] = fut
        try:
            await self.publish(env)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TransportError("no reply") from None
        finally:
            self._waiters.pop(env.reply_to, None)

    async def reply(self, request: Envelope, payload: dict[str, Any], *, kind: str = "reply") -> None:
        if request.reply_to:
            await self.publish(Envelope.new(request.reply_to, kind, request.tenant_id, payload))

    async def stats(self) -> dict[str, Any]:
        return {"messages": len(self.log), "subscriptions": len(self._subs)}

    async def prune(self) -> int:
        return 0


class Collector:
    """Records envelopes of one kind (the responses subject also carries heartbeats)."""

    def __init__(self, kind: str | None = None) -> None:
        self.kind = kind
        self.items: list[Envelope] = []

    async def __call__(self, env: Envelope) -> None:
        if self.kind is None or env.kind == self.kind:
            self.items.append(env)


async def wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def signed(subject: str, kind: str, payload: dict[str, Any], *, msg_id: str | None = None, key: str = ROUTE_KEY, tenant: str = TENANT) -> Envelope:
    return Envelope.new(subject, kind, tenant, payload, msg_id=msg_id).sign(key)


@pytest.fixture
async def transport(db):
    t = SqliteTransport(db, retention_seconds=3600) if SqliteTransport is not None else MemoryTransport()
    await t.start()
    yield t
    await t.close()


@pytest.fixture
async def holder(tmp_path: Path, transport):
    store = EvidenceStore(tmp_path / "h1.db", holder_id=HOLDER, tenant_id=TENANT, llm=FakeLLMClient(), router=StubRouter())
    beats: list[dict[str, Any]] = []
    svc = HolderService(store, transport, holder_id=HOLDER, tenant_id=TENANT, route_key=ROUTE_KEY, heartbeat=beats.append,
                        heartbeat_interval=1000.0)
    svc.beats = beats  # type: ignore[attr-defined]
    await svc.start()
    yield svc
    await svc.stop()
    await store.close()


async def collect(transport, subject: str, kind: str | None = None, name: str = "test-core") -> Collector:
    c = Collector(kind)
    await transport.subscribe(subject, consumer=f"{name}-{new_id('c')}", handler=c)
    return c


# ---------------------------------------------------------------------------------------------- questions


async def test_question_end_to_end(holder: HolderService, transport) -> None:
    doc = await holder.store.ingest_document("Ops incident log", OPS_LOG, observed_at="2026-03-15")
    responses = await collect(transport, Subjects.responses(TENANT), "response")
    q = question("What caused the VPN outage?", route_id="route_7")
    await transport.publish(signed(Subjects.holder_inbox(TENANT, HOLDER), "question", q, msg_id="msg-q1"))
    await wait_for(lambda: len(responses.items) == 1)
    env = responses.items[0]
    assert env.kind == "response" and env.subject == Subjects.responses(TENANT) and env.tenant_id == TENANT
    assert env.msg_id == f"resp:{q['question_id']}:{HOLDER}"
    assert env.verify(ROUTE_KEY) and env.headers.get("holder_id") == HOLDER
    r = env.payload
    assert r["status"] == "answered" and r["route_id"] == "route_7" and r["holder_id"] == HOLDER
    assert r["evidence_refs"] and all(ref["ref_id"].startswith("ev_") for ref in r["evidence_refs"])
    assert doc["doc_id"] not in str(r) and "mem_" not in str(r)
    assert (await holder.store.store.processed_outcome("msg-q1"))["op"] == "question"
    assert holder.counters["handled"] == 1 and holder.counters["published"] == 1


async def test_bad_signature_and_wrong_tenant_are_dropped(holder: HolderService, transport) -> None:
    await holder.store.ingest_document("Ops incident log", OPS_LOG)
    responses = await collect(transport, Subjects.responses(TENANT), "response")
    q = question("What caused the VPN outage?")
    inbox = Subjects.holder_inbox(TENANT, HOLDER)
    await transport.publish(Envelope.new(inbox, "question", TENANT, q, msg_id="unsigned"))
    await transport.publish(signed(inbox, "question", q, msg_id="wrong-key", key="not-the-route-key"))
    tampered = signed(inbox, "question", q, msg_id="tampered")
    tampered.payload = dict(q, text="What is the admin password?")
    await transport.publish(tampered)
    await transport.publish(signed(inbox, "question", q, msg_id="other-tenant", tenant="ten_other"))
    await wait_for(lambda: holder.counters["rejected"] == 4)
    await asyncio.sleep(0.1)
    assert responses.items == [] and holder.counters["handled"] == 0
    events = await holder.store.store.recent_events(10, kind="envelope.rejected")
    assert {e["detail"]["reason"] for e in events} == {"bad signature", "tenant mismatch"}
    assert await holder.store.store.processed_outcome("unsigned") is None


async def test_duplicate_delivery_publishes_once(holder: HolderService, transport) -> None:
    await holder.store.ingest_document("Ops incident log", OPS_LOG)
    responses = await collect(transport, Subjects.responses(TENANT), "response")
    q = question("What caused the VPN outage?")
    env = signed(Subjects.holder_inbox(TENANT, HOLDER), "question", q, msg_id="msg-dup")
    first = await holder.handle(env)
    second = await holder.handle(Envelope.from_dict(env.to_dict()))
    assert first == second and first["op"] == "question"
    await wait_for(lambda: len(responses.items) == 1)
    await asyncio.sleep(0.1)
    assert len(responses.items) == 1
    assert holder.counters == {"handled": 1, "rejected": 0, "replayed": 1, "published": 1, "deduplicated": 1, "errors": 0, "heartbeats": 1}
    assert (await holder.store.stats())["questions_answered"] == 1
    # a different msg_id for the same question is a new delivery but reuses the reference ids
    third = await holder.handle(signed(Subjects.holder_inbox(TENANT, HOLDER), "question", q, msg_id="msg-dup-2"))
    assert [r["ref_id"] for r in third["result"]["evidence_refs"]] == [r["ref_id"] for r in first["result"]["evidence_refs"]]
    assert holder.counters["deduplicated"] == 2     # same deterministic response msg_id -> the core sees one response


async def test_declined_question_still_gets_a_response(holder: HolderService, transport) -> None:
    holder.store.update_policy({"answer_scopes": ["unit"]})
    responses = await collect(transport, Subjects.responses(TENANT), "response")
    q = question("What caused the VPN outage?", visibility="org")
    await transport.publish(signed(Subjects.holder_inbox(TENANT, HOLDER), "question", q))
    await wait_for(lambda: len(responses.items) == 1)
    assert responses.items[0].payload["status"] == "declined" and responses.items[0].verify(ROUTE_KEY)


# ---------------------------------------------------------------------------------------------- ingest / revise / retract


async def test_ingest_revise_retract_over_transport(holder: HolderService, transport) -> None:
    results = await collect(transport, Subjects.ingest_results(TENANT))
    events = await collect(transport, Subjects.evidence_events(TENANT))
    responses = await collect(transport, Subjects.responses(TENANT), "response")
    ingest = {"title": "Ops incident log", "text": OPS_LOG, "observed_at": "2026-03-15", "domains": ["ops"], "uploaded_by": "ana"}
    await transport.publish(signed(Subjects.holder_ingest(TENANT, HOLDER), "ingest", ingest, msg_id="ing-1"))
    await wait_for(lambda: len(results.items) == 1)
    env = results.items[0]
    doc = env.payload["document"]
    assert env.kind == "ingest_result" and env.msg_id == f"ingest:{doc['doc_id']}" and env.payload["holder_id"] == HOLDER
    assert env.verify(ROUTE_KEY)
    assert env.payload["request_msg_id"] == "ing-1" and env.payload["error"] is None
    assert doc["chunks"] >= 2 and doc["domains"] == ["ops"] and doc["status"] == "active"
    # redelivery of the ingest request: same document, same result msg_id -> deduplicated
    await holder.handle(signed(Subjects.holder_ingest(TENANT, HOLDER), "ingest", ingest, msg_id="ing-1"))
    assert len(await holder.store.list_documents()) == 1 and holder.counters["replayed"] == 1

    q = question("What caused the VPN outage?")
    await transport.publish(signed(Subjects.holder_inbox(TENANT, HOLDER), "question", q))
    await wait_for(lambda: len(responses.items) == 1)
    refs = {r["ref_id"] for r in responses.items[0].payload["evidence_refs"]}
    assert refs

    revise = {"doc_id": doc["doc_id"], "text": "Incident 2026-03-04: VPN outage.\n\nThe VPN outage was caused by a misconfigured firewall rule.",
              "reason": "correction"}
    await transport.publish(signed(Subjects.holder_ingest(TENANT, HOLDER), "revise", revise, msg_id="rev-1"))
    await wait_for(lambda: len(events.items) == 1)
    ev = events.items[0]
    assert ev.kind == "evidence_event" and ev.msg_id == "evidence:rev-1" and ev.verify(ROUTE_KEY)
    assert ev.payload["event"] == "revised" and ev.payload["doc_id"] == doc["doc_id"] and ev.payload["holder_id"] == HOLDER
    assert set(ev.payload["affected_ref_ids"]) == refs and ev.payload["version"] == 2 and ev.payload["reason"] == "correction"
    assert ev.payload["new_source_root_id"] != doc["source_root_id"] == ev.payload["previous_source_root_id"]

    await transport.publish(signed(Subjects.holder_ingest(TENANT, HOLDER), "retract", {"doc_id": doc["doc_id"], "reason": "withdrawn"}, msg_id="ret-1"))
    await wait_for(lambda: len(events.items) == 2)
    ev = events.items[1]
    assert ev.payload["event"] == "retracted" and set(ev.payload["affected_ref_ids"]) == refs and ev.payload["reason"] == "withdrawn"
    assert (await holder.store.document(doc["doc_id"]))["status"] == "retracted"


async def test_malformed_requests_get_error_outcomes_not_retries(holder: HolderService, transport) -> None:
    events = await collect(transport, Subjects.evidence_events(TENANT))
    results = await collect(transport, Subjects.ingest_results(TENANT))
    await transport.publish(signed(Subjects.holder_ingest(TENANT, HOLDER), "revise", {"text": "no doc id"}, msg_id="bad-1"))
    await transport.publish(signed(Subjects.holder_ingest(TENANT, HOLDER), "retract", {"doc_id": "doc_missing"}, msg_id="bad-2"))
    await transport.publish(signed(Subjects.holder_ingest(TENANT, HOLDER), "ingest", {"title": "x", "text": "  "}, msg_id="bad-3"))
    await wait_for(lambda: len(events.items) == 2 and len(results.items) == 1)
    assert {e.payload["event"] for e in events.items} == {"error"} and all(e.payload["reason"] for e in events.items)
    assert results.items[0].payload["document"] is None and "empty" in results.items[0].payload["error"]
    assert holder.counters["errors"] == 3
    assert (await holder.store.store.processed_outcome("bad-1"))["error"]
    await asyncio.sleep(0.1)
    assert len(events.items) == 2      # not redelivered


# ---------------------------------------------------------------------------------------------- raw / control / heartbeat


async def test_raw_request_reply(holder: HolderService, transport) -> None:
    doc = await holder.store.ingest_document("Ops incident log", OPS_LOG)
    resp = await holder.store.answer_question(question("What caused the VPN outage?"))
    ref_id = resp["evidence_refs"][0]["ref_id"]
    reply = await transport.request(signed(Subjects.holder_raw(TENANT, HOLDER), "raw_request", {"ref_id": ref_id, "grant_token": "gt"}), timeout=3)
    assert reply.kind == "raw_reply"
    assert reply.payload["doc_id"] == doc["doc_id"] and reply.payload["text"] == OPS_LOG.strip() and reply.payload["version"] == 1
    missing = await transport.request(signed(Subjects.holder_raw(TENANT, HOLDER), "raw_request", {"ref_id": "ev_nope"}), timeout=3)
    assert missing.payload["error"] and missing.payload["ref_id"] == "ev_nope"
    with pytest.raises(TransportError):
        await transport.request(Envelope.new(Subjects.holder_raw(TENANT, HOLDER), "raw_request", TENANT, {"ref_id": ref_id}), timeout=0.3)
    events = await holder.store.store.recent_events(10, kind="raw.request")
    assert {e["detail"]["granted"] for e in events} == {True, False}


async def test_control_stats_and_reload_policy(holder: HolderService, transport) -> None:
    await holder.store.ingest_document("Ops incident log", OPS_LOG)
    reply = await transport.request(signed(Subjects.holder_control(TENANT, HOLDER), "control", {"action": "stats"}), timeout=3)
    assert reply.kind == "control_reply" and reply.payload["stats"]["documents"] == 1 and reply.payload["stats"]["running"] is True
    reply = await transport.request(signed(Subjects.holder_control(TENANT, HOLDER), "control",
                                           {"action": "reload_policy", "export_policy": {"disclosure": "summary"}, "domains": ["ops"]}), timeout=3)
    assert reply.payload["export_policy"]["disclosure"] == "summary" and reply.payload["domains"] == ["ops"]
    assert holder.store.export_policy["disclosure"] == "summary"
    bad = await transport.request(signed(Subjects.holder_control(TENANT, HOLDER), "control", {"action": "explode"}), timeout=3)
    assert "unknown control action" in bad.payload["error"]


async def test_control_manual_response_publishes_a_human_response(holder: HolderService, transport) -> None:
    doc = await holder.store.ingest_document("Ops incident log", OPS_LOG, observed_at="2026-03-15")
    responses = await collect(transport, Subjects.responses(TENANT), "response")
    q = question("What caused the VPN outage?", route_id="route_9")
    control = {"action": "manual_response", "question": q, "content": "It was the expired gateway certificate; see my log.", "doc_ids": [doc["doc_id"]]}
    env = signed(Subjects.holder_control(TENANT, HOLDER), "control", control, msg_id="ctl-h1")
    reply = await transport.request(env, timeout=3)
    assert reply.kind == "control_reply" and reply.payload["status"] == "answered" and reply.payload["route_id"] == "route_9"
    await wait_for(lambda: len(responses.items) == 1)
    out = responses.items[0]
    assert out.msg_id == manual_response_msg_id(q["question_id"], HOLDER, [doc["doc_id"]], control["content"])
    assert out.msg_id.startswith(f"resp:{q['question_id']}:{HOLDER}:human:") and out.verify(ROUTE_KEY)
    p = out.payload
    assert p["holder_id"] == HOLDER and p["content"] == control["content"] and p["provenance"]["retrieval_operator"] == "human"
    assert len(p["evidence_refs"]) == 1 and p["evidence_refs"][0]["ref_id"].startswith("ev_") and doc["doc_id"] not in str(p)
    # redelivery of the control envelope replays the stored outcome under the same response msg_id -> deduplicated
    await holder.handle(signed(Subjects.holder_control(TENANT, HOLDER), "control", control, msg_id="ctl-h1"))
    await asyncio.sleep(0.1)
    assert len(responses.items) == 1 and holder.counters["replayed"] == 1 and holder.counters["deduplicated"] == 1
    bad = await transport.request(signed(Subjects.holder_control(TENANT, HOLDER), "control", {"action": "manual_response", "question": {}, "content": "x", "doc_ids": []}), timeout=3)
    assert "question_id" in bad.payload["error"]


async def test_heartbeat_over_transport_and_callback(holder: HolderService, transport) -> None:
    beats = await collect(transport, Subjects.responses(TENANT), "heartbeat")
    await holder.store.ingest_document("Ops incident log", OPS_LOG)
    stats = await holder.heartbeat_once()          # same minute bucket as the start-up beat -> transport de-duplicates
    assert holder.beats[-1] is stats and stats["documents"] == 1 and stats["service"]["handled"] == 0 and stats["running"] is True
    assert holder.beats[0]["documents"] == 0      # the start-up beat
    await wait_for(lambda: len(beats.items) >= 1)
    hb = beats.items[0]
    assert hb.msg_id == heartbeat_msg_id(HOLDER) or hb.msg_id.startswith(f"hb:{HOLDER}:")
    assert hb.kind == "heartbeat" and hb.verify(ROUTE_KEY) and hb.payload["holder_id"] == HOLDER and "documents" in hb.payload["stats"]
    assert holder.counters["heartbeats"] >= 1 and holder.counters["published"] == 0


# ---------------------------------------------------------------------------------------------- embedded holders


async def test_embedded_holders_answer_from_their_own_evidence(tmp_path: Path, db, org, transport) -> None:
    tenant = await org.create_tenant("Acme", "acme")
    tid = tenant["tenant_id"]
    ana = await org.create_user(tid, "ana@example.com", "Ana")
    ben = await org.create_user(tid, "ben@example.com", "Ben")
    h_ana, _ = await org.register_holder(tid, owner_type="user", owner_id=ana["user_id"], name="Ana's evidence", mode="embedded", domains=["ops"])
    h_ben, _ = await org.register_holder(tid, owner_type="user", owner_id=ben["user_id"], name="Ben's evidence", mode="embedded", domains=["ops"])
    h_ext, _ = await org.register_holder(tid, owner_type="user", owner_id=ben["user_id"], name="external", mode="external")
    settings = SimpleNamespace(holders_dir=str(tmp_path / "holders"))
    embedded = EmbeddedHolders(settings, db, org, transport, llm_factory=FakeLLMClient, router=StubRouter(), heartbeat_interval=1000.0)
    await embedded.start()
    assert set(embedded.holder_ids()) == {h_ana["holder_id"], h_ben["holder_id"]}
    assert embedded.get(h_ext["holder_id"]) is None
    assert (tmp_path / "holders" / h_ana["holder_id"] / "evidence.db").exists()
    assert org.get_holder(h_ana["holder_id"])["status"] == "online" and org.get_holder(h_ben["holder_id"])["status"] == "online"

    await embedded.get(h_ana["holder_id"]).ingest_document("Ana's incident notes", OPS_LOG, observed_at="2026-03-15")
    await embedded.get(h_ben["holder_id"]).ingest_document("Ben's VPN notes",
                                                           "VPN notes.\n\nThe VPN outage on 4 March happened because the gateway ran out of licences "
                                                           "when the contractor team joined; the certificate was fine.", observed_at="2026-03-16")
    responses = await collect(transport, Subjects.responses(tid), "response")
    q = question("What caused the VPN outage?", candidate_domains=["ops"])
    for h in (h_ana, h_ben):
        key = org.holder_secret_row(h["holder_id"])["route_key"]
        await transport.publish(signed(Subjects.holder_inbox(tid, h["holder_id"]), "question", dict(q, route_id=f"route:{h['holder_id']}"),
                                       msg_id=f"q:{h['holder_id']}", key=key, tenant=tid))
    await wait_for(lambda: len(responses.items) == 2)
    by_holder = {env.payload["holder_id"]: env.payload for env in responses.items}
    assert set(by_holder) == {h_ana["holder_id"], h_ben["holder_id"]}
    assert all(env.verify(org.holder_secret_row(env.payload["holder_id"])["route_key"]) for env in responses.items)
    a, b = by_holder[h_ana["holder_id"]], by_holder[h_ben["holder_id"]]
    assert a["status"] == b["status"] == "answered"
    assert "certificate" in a["content"] and "licences" in b["content"] and a["content"] != b["content"]
    assert {r["ref_id"] for r in a["evidence_refs"]}.isdisjoint({r["ref_id"] for r in b["evidence_refs"]})
    assert {r["source_root_id"] for r in a["evidence_refs"]}.isdisjoint({r["source_root_id"] for r in b["evidence_refs"]})
    assert a["route_id"] == f"route:{h_ana['holder_id']}"

    # the export policy is re-read from the registry before each question (hot reload without restart)
    await org.update_holder(h_ana["holder_id"], export_policy={"answer_scopes": ["org"], "disclosure": "excerpt"})
    key = org.holder_secret_row(h_ana["holder_id"])["route_key"]
    await transport.publish(signed(Subjects.holder_inbox(tid, h_ana["holder_id"]), "question", question("What caused the VPN outage?"),
                                   msg_id="q:ana:2", key=key, tenant=tid))
    await wait_for(lambda: len(responses.items) == 3)
    assert responses.items[2].payload["status"] == "declined"

    await embedded.service(h_ana["holder_id"]).heartbeat_once()
    stats = org.get_holder(h_ana["holder_id"])["stats"]
    assert stats["documents"] == 1 and stats["memories"] >= 2 and stats["handled"] == 2
    await embedded.stop()
    assert org.get_holder(h_ana["holder_id"])["status"] == "offline"
    assert embedded.holder_ids() == []


async def test_embedded_holders_accept_a_shared_embedder(tmp_path: Path, db, org, transport) -> None:
    class ListEmbedder:   # the Mycelic EmbeddingProvider shape (build_embedder)
        name, model, dim = "hash", "hash-256", 256

        def __init__(self) -> None:
            self.calls = 0

        async def embed(self, texts):
            from NeuralGraph.chat_memory.llm import fake_embedding
            self.calls += 1
            return [fake_embedding(t) for t in texts]

    tenant = await org.create_tenant("Acme", "acme")
    ana = await org.create_user(tenant["tenant_id"], "ana@example.com", "Ana")
    h, _ = await org.register_holder(tenant["tenant_id"], owner_type="user", owner_id=ana["user_id"], name="Ana", mode="embedded")
    provider = ListEmbedder()
    embedded = EmbeddedHolders(SimpleNamespace(holders_dir=str(tmp_path / "holders")), db, org, transport, router=None, embedder=provider,
                               heartbeat_interval=1000.0)
    await embedded.start()
    doc = await embedded.get(h["holder_id"]).ingest_document("Ops incident log", OPS_LOG)
    assert provider.calls == doc["chunks"]
    hits = await embedded.get(h["holder_id"]).search("VPN certificate", k=2)
    assert hits and hits[0]["doc_id"] == doc["doc_id"]
    await embedded.stop()


# ---------------------------------------------------------------------------------------------- standalone process


async def test_local_owner_api(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    store = EvidenceStore(tmp_path / "local.db", holder_id=HOLDER, tenant_id=TENANT, llm=FakeLLMClient())
    auth = {"Authorization": "Bearer holder-key"}
    async with TestClient(TestServer(build_local_app(store, "holder-key"))) as client:
        assert (await client.get("/stats")).status == 401
        assert (await client.get("/stats", headers={"Authorization": "Bearer wrong"})).status == 401
        r = await client.post("/documents", json={"title": "Ops incident log", "text": OPS_LOG, "observed_at": "2026-03-15"}, headers=auth)
        assert r.status == 201
        doc = (await r.json())["document"]
        assert doc["chunks"] >= 2 and doc["status"] == "active"
        r = await client.get("/search", params={"q": "VPN certificate", "k": "2"}, headers=auth)
        hits = (await r.json())["results"]
        assert hits and hits[0]["doc_id"] == doc["doc_id"]
        assert (await (await client.get("/stats", headers=auth)).json())["documents"] == 1
        assert (await client.post("/documents", json={"title": "x", "text": ""}, headers=auth)).status == 400
        assert (await client.post("/documents/doc_missing/retract", json={"reason": "x"}, headers=auth)).status == 404
        r = await client.post(f"/documents/{doc['doc_id']}/revise", json={"text": ROBOTS_2026, "reason": "r"}, headers=auth)
        assert (await r.json())["document"]["version"] == 2
        r = await client.get(f"/documents/{doc['doc_id']}", headers=auth)
        assert (await r.json())["text"] == ROBOTS_2026
        assert len((await (await client.get("/documents", headers=auth)).json())["items"]) == 1
    await store.close()


async def test_run_holder_with_injected_transport_and_core(tmp_path: Path, transport) -> None:
    class FakeCore:
        def __init__(self) -> None:
            self.beats: list[tuple[str, dict[str, Any]]] = []

        async def bootstrap(self) -> dict[str, Any]:
            raise AssertionError("bootstrap was injected")

        async def heartbeat(self, stats: dict[str, Any], *, status: str = "online") -> dict[str, Any]:
            self.beats.append((status, stats))
            return {"ok": True, "export_policy": {"disclosure": "summary"}}

        async def close(self) -> None:
            return None

    boot = {"holder_id": HOLDER, "tenant_id": TENANT, "route_key": ROUTE_KEY, "export_policy": {"answer_scopes": ["unit", "org"]},
            "domains": ["ops"], "heartbeat_seconds": 0.05}
    core = FakeCore()
    stop = asyncio.Event()
    hb = await collect(transport, Subjects.responses(TENANT), "heartbeat")
    task = asyncio.create_task(run_holder(SimpleNamespace(transport="memory"), holder_id=HOLDER, key="k", core_url="http://core", data_dir=tmp_path,
                                          stop=stop, transport=transport, bootstrap=boot, core=core, llm=FakeLLMClient(), router=StubRouter()))
    await wait_for(lambda: len(core.beats) >= 2 and len(hb.items) >= 1)
    assert hb.items[0].payload["holder_id"] == HOLDER and hb.items[0].verify(ROUTE_KEY)
    results = await collect(transport, Subjects.ingest_results(TENANT))
    responses = await collect(transport, Subjects.responses(TENANT), "response")
    await transport.publish(signed(Subjects.holder_ingest(TENANT, HOLDER), "ingest", {"title": "Ops incident log", "text": OPS_LOG}, msg_id="p-ing"))
    await wait_for(lambda: len(results.items) == 1)
    await transport.publish(signed(Subjects.holder_inbox(TENANT, HOLDER), "question", question("What caused the VPN outage?"), msg_id="p-q"))
    await wait_for(lambda: len(responses.items) == 1)
    r = responses.items[0].payload
    assert r["status"] == "answered" and all(ref["disclosure_level"] == "summary" for ref in r["evidence_refs"])   # heartbeat reply applied
    assert (tmp_path / HOLDER / "evidence.db").exists()
    stop.set()
    summary = await asyncio.wait_for(task, 5)
    assert summary["holder_id"] == HOLDER and summary["counters"]["handled"] == 2
    assert core.beats[-1][0] == "offline" and core.beats[-1][1]["documents"] == 1

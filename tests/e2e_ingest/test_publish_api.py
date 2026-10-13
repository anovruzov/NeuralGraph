"""S3: the event schema against the spike's, the idempotency keys of section 7, the publisher's retries and
watermark, and, against a real nats-server and ``ProcessDriver``: signed events verify under the driver's key,
re-posts are not created, and forged publishes straight to NATS are terminated and counted exactly once each.

Every world here is synthetic; no record text exists. A missing nats-server fails these tests (M11)."""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import sha256_hex
from mycelic.integrity import Keyring
from mycelic.models import canonical_label
from mycelic.sdk import MycelicError
from research.e2e_ingest import drills
from research.e2e_ingest import publish_api as P
from research.routing_spike import publish as spike
from research.routing_spike.hq import Received
from tests.e2e_ingest.helpers import (DQ, LiveFabric, expected_event_id, expected_memory_id, nats_binary_version,
                                      nats_server_bin)

QID = "a" * 64
QUESTION = {"schema_version": 1, "pack": DQ.id, "pack_hash": DQ.config_hash, "question_id": QID,
            "candidate_key": "lot:L20046:crack", "template_id": "count_predicate_on_entity",
            "params": {"entity_type": "lot", "entity_id": "L20046", "predicate": "crack"},
            "window": {"start_week": "2024-W20", "end_week": "2024-W27"}, "as_of": "2024-07-20"}
CONCLUSION_ID = "c-" + QID[:32]


def received(site: str, kind: str = "confirm", support: str | None = "3-9") -> Received:
    body = {"verdict": kind, "reason": None, "support_bucket": support if kind == "confirm" else None,
            "roots_bucket": support if kind == "confirm" else None,
            "reporters_bucket": support if kind == "confirm" else None,
            "newest_week": "2024-W27" if kind == "confirm" else None, "verdict_id": sha256_hex(site + kind)}
    return Received(site, QID, "site", body, sha256_hex(f"{site}|{kind}|{support}"))


RECEIVED = [received("plant-ashvale"), received("plant-fennick"), received("werk-dornhagen", "refute")]
ROUTE = [r.site for r in RECEIVED]


def our_events(verdict_event_ids: list[str] | None = None, status: str = "supported") -> list[dict[str, Any]]:
    """The three verdict events of the fixture, or (given their ids) the conclusion event."""
    if verdict_event_ids is None:
        return [P.verdict_event(QUESTION, site=r.site, seq=1, source=r.source, body=r.body, sha256=r.sha256)
                for r in RECEIVED]
    memory = P.conclusion_memory(DQ, QUESTION, status=status, entity_type="lot", candidate_key="lot:L20046:crack",
                                 conclusion_id=CONCLUSION_ID, snapshot_week="2024-W27", as_of="2024-07-20",
                                 route=ROUTE, verdict_event_ids=verdict_event_ids)
    return [P.conclusion_event(QUESTION, conclusion_id=CONCLUSION_ID, version=1, status=status, route=ROUTE,
                               verdict_event_ids=verdict_event_ids, memory=memory)]


# =================================================================================================== the schema

class SchemaTests(unittest.TestCase):
    def test_event_types_and_conclusion_text_equal_the_spikes(self) -> None:
        self.assertEqual((P.VERDICT_EVENT, P.CONCLUSION_EVENT), (spike.VERDICT_EVENT, spike.CONCLUSION_EVENT))
        for status in ("supported", "hypothesis", "stale", "contested", "rejected"):
            self.assertEqual(P.conclusion_text(DQ, QUESTION, status), spike.conclusion_text(DQ, QUESTION, status))
        other = dict(QUESTION, params={"entity_type": "product", "entity_id": "SD-9", "predicate": "leak"},
                     window={"start_week": "2024-W02", "end_week": "2024-W09"})
        self.assertEqual(P.conclusion_text(DQ, other, "supported"), spike.conclusion_text(DQ, other, "supported"))

    def test_idempotency_keys_are_the_store_primary_keys(self) -> None:
        self.assertEqual(P.verdict_key(QID, "plant-ashvale", 2), f"{QID}:plant-ashvale:2")
        self.assertEqual(P.conclusion_key(CONCLUSION_ID, 3), f"{CONCLUSION_ID}:3")
        [verdict, *_] = our_events()
        self.assertEqual(verdict["idempotency_key"], f"{QID}:plant-ashvale:1")
        [conclusion] = our_events(["evt_1", "evt_2", "evt_3"])
        self.assertEqual(conclusion["idempotency_key"], f"{CONCLUSION_ID}:1")
        self.assertEqual(conclusion["payload"]["verdict_event_ids"], ["evt_1", "evt_2", "evt_3"])
        self.assertEqual(conclusion["memory"]["source_event_ids"], ["evt_1", "evt_2", "evt_3"])

    def test_payloads_and_memory_equal_what_the_spikes_fabric_publishes(self) -> None:
        """The spike publishes the same Received rows into its in-process fabric; its stored events and memory are
        the reference for every key of ours (``publish.py:80-102``)."""
        from mycelic.store import MycelicStore

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fabric" / "mycelic.db"

            async def publish() -> dict[str, Any]:
                fabric = spike.Fabric(db, DQ)
                await fabric.start(["R"])
                try:
                    return await fabric.publish("R", QUESTION, entity_type="lot", candidate_key="lot:L20046:crack",
                                                snapshot_week="2024-W27", as_of="2024-07-20", route=ROUTE,
                                                received=RECEIVED, status="supported")
                finally:
                    await fabric.close()

            ids = asyncio.run(publish())
            store = MycelicStore(db)
            try:
                events = store.get_events(ids["verdict_event_ids"] + [ids["conclusion_event_id"]])
                note = store.get_memory(ids["memory_id"])
            finally:
                asyncio.run(store.close())
        theirs = {e.payload["payload"]["site"]: e.payload["payload"] for eid, e in events.items()
                  if eid in ids["verdict_event_ids"]}
        for event in our_events():
            with self.subTest(site=event["payload"]["site"]):
                self.assertEqual(event["payload"], theirs[event["payload"]["site"]])
                self.assertEqual(event["type"], events[ids["verdict_event_ids"][0]].payload["type"])
        [conclusion] = our_events(ids["verdict_event_ids"])
        their_conclusion = events[ids["conclusion_event_id"]].payload
        self.assertEqual(conclusion["payload"], their_conclusion["payload"])
        self.assertEqual(conclusion["type"], their_conclusion["type"])
        memory = conclusion["memory"]
        # the stored note's sources are the verdict events plus the conclusion event itself (``service.py:1229``);
        # the service stores the entity and slot labels in canonical form (``models.canonical_label``)
        self.assertEqual((memory["text"], canonical_label(memory["entity"]), canonical_label(memory["slot"]),
                          memory["topic"], memory["kind"], memory["visibility"],
                          memory["source_event_ids"] + [ids["conclusion_event_id"]]),
                         (note.text, note.entity, note.slot, note.topic, note.kind, note.visibility,
                          list(note.source_event_ids)))
        self.assertEqual(memory["entity"], "L20046")
        self.assertEqual({k: note.metadata[k] for k in memory["metadata"]}, memory["metadata"])
        self.assertEqual(note.metadata["value"], memory["value"])

    def test_an_hq_record_has_no_sha256_and_a_site_verdict_keeps_hqs(self) -> None:
        hq = {"question_id": QID, "site": "plant-x", "verdict": "unknown", "reason": "error", "source": "hq"}
        payload = P.verdict_payload(QUESTION, site="plant-x", source="hq", body=hq, sha256=None)
        self.assertEqual((payload["verdict"], payload["reason"], payload["source"], payload["sha256"],
                          payload["verdict_id"], payload["support_bucket"]),
                         ("unknown", "error", "hq", None, None, None))
        site = P.verdict_payload(QUESTION, site="plant-a", source="site", body=RECEIVED[0].body,
                                 sha256=RECEIVED[0].sha256)
        self.assertEqual(site["sha256"], RECEIVED[0].sha256)
        self.assertEqual(sorted(site), sorted(["site", "question_id", "source", "verdict_id", "verdict", "reason",
                                               "support_bucket", "roots_bucket", "reporters_bucket", "newest_week",
                                               "window", "sha256"]))


# =================================================================================================== the publisher

class FakeClient:
    """A stand-in for ``MycelicClient`` whose ``publish_events`` answers from a script of statuses or results."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    def publish_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(events)
        step = self.script.pop(0)
        if isinstance(step, int):
            raise MycelicError(step, f"status {step}")
        return {"results": [step], "status": "accepted"}


class PublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sleeps: list[float] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def publisher(self, client: FakeClient, **kw: Any) -> P.Publisher:
        return P.Publisher(client, watermark=self.dir / "wm.json",  # type: ignore[arg-type]
                           ledger=self.dir / "ledger.jsonl", sleep=self.sleeps.append,
                           clock=lambda: "2026-10-11T00:00:00.000Z", **kw)

    def test_retryable_statuses_are_retried_with_backoff_and_counted(self) -> None:
        [event] = our_events()[:1]
        client = FakeClient([503, 0, {"event_id": "evt_x", "created": True}])
        result = self.publisher(client, retries=5, backoff=0.25).publish(event)
        self.assertEqual((result.attempts, result.created, result.ambiguous, len(client.calls)), (3, True, False, 3))
        self.assertEqual(self.sleeps, [0.25, 0.5])

    def test_a_non_retryable_status_raises_at_once(self) -> None:
        [event] = our_events()[:1]
        client = FakeClient([400])
        with self.assertRaises(P.PublishError):
            self.publisher(client).publish(event)
        self.assertEqual((len(client.calls), self.sleeps), (1, []))

    def test_exhausted_retries_raise(self) -> None:
        [event] = our_events()[:1]
        client = FakeClient([0, 0, 0])
        with self.assertRaises(P.PublishError):
            self.publisher(client, retries=2).publish(event)
        self.assertEqual(len(client.calls), 3)

    def test_a_duplicate_after_a_failed_attempt_is_ambiguous(self) -> None:
        [event] = our_events()[:1]
        client = FakeClient([0, {"event_id": "evt_x", "created": False}])
        result = self.publisher(client).publish(event)
        self.assertEqual((result.attempts, result.created, result.ambiguous), (2, False, True))
        clean = self.publisher(FakeClient([{"event_id": "evt_x", "created": False}])).publish(event)
        self.assertEqual((clean.attempts, clean.created, clean.ambiguous), (1, False, False))

    def test_watermark_and_ledger_follow_every_publish_and_continue_across_instances(self) -> None:
        events = our_events()
        client = FakeClient([{"event_id": "evt_1", "created": True}, {"event_id": "evt_2", "created": False}])
        first = self.publisher(client)
        first.publish(events[0])
        wm = json.loads((self.dir / "wm.json").read_text())
        self.assertEqual((wm["publishes"], wm["created"], wm["duplicate"], wm["last_key"], wm["last_event_id"]),
                         (1, 1, 0, events[0]["idempotency_key"], "evt_1"))
        first.publish(events[1])
        wm = json.loads((self.dir / "wm.json").read_text())
        self.assertEqual((wm["publishes"], wm["created"], wm["duplicate"], wm["last_event_id"]), (2, 1, 1, "evt_2"))
        lines = [json.loads(line) for line in (self.dir / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual([(row["event_id"], row["created"], row["type"]) for row in lines],
                         [("evt_1", True, P.VERDICT_EVENT), ("evt_2", False, P.VERDICT_EVENT)])
        second = self.publisher(FakeClient([{"event_id": "evt_3", "created": True, "memory_id": "mem_3",
                                             "memory_event_id": "evt_m3"}]))
        result = second.publish(our_events(["evt_1", "evt_2"])[0])
        self.assertEqual((result.memory_id, result.memory_event_id, result.memory_created), ("mem_3", "evt_m3", True))
        self.assertEqual((second.watermark["publishes"], second.publishes, second.created), (3, 1, 1))
        self.assertEqual(P.event_ids(second.results), {"agent.event": ["evt_3"], "memory.observed": ["evt_m3"]})

    def test_only_the_two_collective_event_types_with_a_key_are_published(self) -> None:
        pub = self.publisher(FakeClient([]))
        with self.assertRaises(P.PublishError):
            pub.publish({"type": "other.thing", "idempotency_key": "k", "payload": {}})
        with self.assertRaises(P.PublishError):
            pub.publish({"type": P.VERDICT_EVENT, "payload": {}})
        self.assertEqual(pub.results, [])


# =================================================================================================== the live fabric

class LivePublishTests(unittest.TestCase):
    """A real nats-server and a real service under ``E2EProcessDriver``: signed, idempotent, exactly once."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="e2e-publish-")
        cls.fabric = LiveFabric(Path(cls.tmp.name) / "fabric")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fabric.close()
        cls.tmp.cleanup()

    def test_signed_events_verify_under_the_drivers_key_and_re_posts_are_not_created(self) -> None:
        fabric = self.fabric
        driver = fabric.driver
        self.assertTrue(driver.nats_bin and driver.signing_key)
        here = Path(self.tmp.name)
        pub = P.Publisher(fabric.hq, watermark=here / "wm.json", ledger=here / "ledger.jsonl")
        verdicts = pub.publish_all(our_events())
        [conclusion] = pub.publish_all(our_events([r.event_id for r in verdicts]))
        for event, result in zip(our_events(), verdicts):
            self.assertEqual((result.event_id, result.created, result.memory_id, result.attempts),
                             (expected_event_id(event["idempotency_key"]), True, None, 1))
        key = f"{CONCLUSION_ID}:1"
        self.assertEqual((conclusion.event_id, conclusion.created, conclusion.memory_id, conclusion.memory_created),
                         (expected_event_id(key), True, expected_memory_id(key), True))
        self.assertIsNotNone(conclusion.memory_event_id)

        # the same four POSTs again: the same ids, nothing created, no memory
        again = pub.publish_all(our_events()) + pub.publish_all(our_events([r.event_id for r in verdicts]))
        self.assertEqual([r.event_id for r in again], [r.event_id for r in verdicts] + [conclusion.event_id])
        self.assertEqual({(r.created, r.ambiguous) for r in again}, {(False, False)})
        self.assertEqual((again[-1].memory_id, again[-1].memory_created, again[-1].memory_event_id),
                         (conclusion.memory_id, False, None))
        self.assertEqual((pub.publishes, pub.created, pub.duplicate), (8, 4, 4))

        status = fabric.wait_idle()
        counts = drills.fabric_counts(status)
        self.assertEqual((counts["transport"], counts["outbox_pending"], counts["consumer_pending"]),
                         ("nats-jetstream", 0, 0))
        self.assertGreater(counts["stream_messages"], 0)

        # every stream message, by sequence, verifies under Keyring(driver.signing_key); every API id is there once
        report = drills.read_stream(driver)
        self.assertEqual((report.fetched, report.foreign), (counts["stream_messages"], 0))
        self.assertEqual(report.verified, report.fetched)
        self.assertEqual((report.kinds["agent.event"], report.kinds["memory.observed"]), (4, 1))
        self.assertEqual(counts["stream_messages"], sum(report.kinds.values()) + report.foreign)
        ids = P.event_ids(pub.results)
        self.assertEqual((len(ids["agent.event"]), len(set(ids["agent.event"])), ids["memory.observed"]),
                         (8, 4, [conclusion.memory_event_id]))
        self.assertEqual(report.ids_in_stream(sorted(set(ids["agent.event"])) + ids["memory.observed"]), (5, 0))
        # the check is the driver's key and nothing weaker: a wrong key verifies none of them
        wrong = Keyring("not-the-drivers-key-0123456789abcdef0123")
        self.assertEqual(drills.read_stream(driver, keyring=wrong).verified, 0)

        # the service's own view: every one of our events applied, once, with applied_at set
        rows = {e["event_id"]: e for e in fabric.events()}
        for eid in set(ids["agent.event"]) | set(ids["memory.observed"]):
            with self.subTest(event=eid):
                self.assertEqual((rows[eid]["status"], rows[eid]["applied_at"] is not None), ("applied", True))
        self.assertEqual(sum(1 for e in rows.values() if e["kind"] == "agent.event"), 4)
        note = fabric.admin.get_memory(conclusion.memory_id)
        # the service adds the conclusion event's own id to the memory's sources (``service.py:1229``)
        self.assertEqual((note["metadata"]["value"], note["metadata"]["conclusion_id"], note["source_event_ids"]),
                         ("supported", CONCLUSION_ID, [r.event_id for r in verdicts] + [conclusion.event_id]))

        # the broker version, read from the live broker and from the binary
        self.assertRegex(report.broker_version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(report.broker_version, drills.broker_version(driver))
        self.assertEqual(report.broker_version, nats_binary_version(nats_server_bin()))


class ForgeryTests(unittest.TestCase):
    """M8 with a real broker: three forged publishes straight to NATS, each terminated and counted exactly once."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="e2e-forgery-")
        cls.fabric = LiveFabric(Path(cls.tmp.name) / "fabric")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fabric.close()
        cls.tmp.cleanup()

    def test_forged_publishes_are_terminated_counted_exactly_once_and_absent_from_the_db(self) -> None:
        fabric, driver = self.fabric, self.fabric.driver
        pub = P.Publisher(fabric.hq)
        verdicts = pub.publish_all(our_events())
        [conclusion] = pub.publish_all(our_events([r.event_id for r in verdicts]))
        fabric.wait_idle()
        before = drills.read_stream(driver)
        self.assertEqual((before.foreign, fabric.signature_failures()), (0, 0))
        ours = sorted(set(P.event_ids(pub.results)["agent.event"])) + P.event_ids(pub.results)["memory.observed"]

        def counter_is(n: int) -> bool:
            return fabric.signature_failures() == n

        forged = []
        forged.append(drills.forge_unsigned(driver, org_id=P.ORG_ID, memory_id="mem_forged_e2e_1",
                                            producer_id=P.AGENT_ID, scope=fabric.agent_path))
        self.assertTrue(drills.wait_for(lambda: counter_is(1), 10.0), "forgery 1 was not counted exactly once")
        forged.append(drills.forge_wrong_key(driver, org_id=P.ORG_ID, memory_id="mem_forged_e2e_2",
                                             producer_id=P.AGENT_ID, scope=fabric.agent_path))
        self.assertTrue(drills.wait_for(lambda: counter_is(2), 10.0), "forgery 2 was not counted exactly once")
        original = next(m for m in before.messages if m.verified and m.kind == "memory.observed"
                        and m.event_id == conclusion.memory_event_id)
        forged.append(drills.forge_replay(driver, seq=original.seq, memory_id="mem_forged_e2e_3"))
        self.assertTrue(drills.wait_for(lambda: counter_is(3), 10.0), "forgery 3 was not counted exactly once")
        self.assertEqual(len(set(forged)), 3)
        time.sleep(1.0)
        self.assertEqual(fabric.signature_failures(), 3, "the counter moved after the third forgery")

        for memory_id in ("mem_forged_e2e_1", "mem_forged_e2e_2", "mem_forged_e2e_3"):
            with self.subTest(memory=memory_id), self.assertRaises(MycelicError) as cm:
                fabric.admin.get_memory(memory_id)
            self.assertEqual(cm.exception.status, 404)
        self.assertEqual(fabric.admin.get_memory(conclusion.memory_id)["memory_id"], conclusion.memory_id)

        after = drills.read_stream(driver)
        self.assertEqual((after.foreign, after.verified), (3, after.fetched - 3))
        self.assertEqual(after.kinds, before.kinds)
        self.assertEqual(after.ids_in_stream(ours), (len(ours), 0))
        self.assertEqual({m.seq for m in after.messages if not m.verified}, set(forged))
        self.assertEqual([m.signed for m in after.messages if m.seq in forged], [False, True, True])
        status = fabric.wait_idle()
        counts = drills.fabric_counts(status)
        self.assertEqual((counts["stream_messages"], counts["consumer_pending"], counts["outbox_pending"]),
                         (after.fetched, 0, 0))
        rejected = [a for a in fabric.admin._request("GET", "/admin/audit", params={"limit": 1000})["audit"]
                    if a.get("action") == "event.rejected"]
        self.assertEqual(len(rejected), 3)
        self.assertTrue(all(re.search(r"seq", json.dumps(a)) for a in rejected))

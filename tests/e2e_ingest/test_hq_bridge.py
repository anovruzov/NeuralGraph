"""S4: the read-only HQ-to-fabric bridge on a hand-built HQ store in the orchestrator's shape, then against a real
nats-server and ``ProcessDriver``: N = 80 publishes with the broker killed after k = N // 2 and restarted (every
event applied exactly once, ``outbox_pending`` 0), a bridge rerun from an empty watermark (0 new memory ids,
``stream_messages`` unchanged), the watermark resume, and the import guards of criterion 7.

Every world here is synthetic; no record text exists. A missing nats-server fails these tests (M11)."""
from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from mycelic.collective.jsonio import sha256_hex
from research.e2e_ingest import drills
from research.e2e_ingest import hq_bridge as B
from research.e2e_ingest import publish_api as P
from research.routing_spike.run import FORBIDDEN_HQ, imports_loaded, static_imports
from tests.e2e_ingest.helpers import (DQ, ROOT, SNAPSHOT_WEEK, LiveFabric, StoreSpec, build_hq_store,
                                      expected_event_id, expected_memory_id)

BRIDGE_FORBIDDEN = FORBIDDEN_HQ + ("research.e2e_ingest.site_graph", "mycelic.service")


class FakeClient:
    """Answers every POST as the service would for a new key, so the bridge can be exercised without a broker."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def publish_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        [event] = events
        self.calls.append(event)
        entry: dict[str, Any] = {"event_id": expected_event_id(event["idempotency_key"]), "created": True}
        if event.get("memory") is not None:
            entry["memory_id"] = expected_memory_id(event["idempotency_key"])
            entry["memory_event_id"] = "evt_mem_" + sha256_hex(event["idempotency_key"])[:16]
        return {"results": [entry], "status": "accepted"}


# =================================================================================================== the reader

class StoreReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.spec: StoreSpec = build_hq_store(Path(self.tmp.name))
        self.reader = B.HqStoreReader(self.spec.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_fixture_has_the_shape_of_the_floors(self) -> None:
        self.assertEqual(self.reader.counts(), {"pd_questions": 20, "pd_routes": 60, "pd_verdicts": 60,
                                                "pd_conclusions": 20})
        self.assertEqual((self.spec.verdict_rows, self.spec.conclusions, self.spec.publishes), (60, 20, 80))
        self.assertGreaterEqual(self.spec.publishes, 80)
        self.assertEqual(sorted(set(self.spec.statuses.values())), ["hypothesis", "supported"])
        self.assertEqual(sum(1 for s in self.spec.verdict_sources.values() if "hq" in s.values()), 2)

    def test_conclusions_come_in_created_at_conclusion_id_version_order(self) -> None:
        rows = self.reader.conclusions()
        self.assertEqual(len(rows), 20)
        keys = [B.Position.of(r) for r in rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([r.question_id for r in rows], [q["question_id"] for q in self.spec.questions])

    def test_plan_reads_the_route_in_rank_order_the_verdicts_and_the_snapshot_week(self) -> None:
        bridge = B.HqBridge(self.spec.path, DQ, P.Publisher(FakeClient()))  # type: ignore[arg-type]
        for row in self.reader.conclusions():
            with self.subTest(conclusion=row.conclusion_id):
                plan = bridge.plan(row)
                qid = row.question_id
                self.assertEqual(plan.route, self.spec.routes[qid])
                self.assertEqual(plan.question["question_id"], qid)
                self.assertEqual(plan.snapshot_week, SNAPSHOT_WEEK)
                self.assertEqual(plan.run_id, self.spec.run_id)
                self.assertEqual([(v.site, v.seq) for v in plan.verdicts], sorted((s, 1) for s in plan.route))
                self.assertEqual({v.site: v.source for v in plan.verdicts}, self.spec.verdict_sources[qid])
                self.assertEqual(plan.row.status, self.spec.statuses[qid])

    def test_events_for_a_plan_follow_section_7_and_an_hq_record_has_no_sha256(self) -> None:
        bridge = B.HqBridge(self.spec.path, DQ, P.Publisher(FakeClient()))  # type: ignore[arg-type]
        rows = self.reader.conclusions()
        with_hq = next(r for r in rows if "hq" in self.spec.verdict_sources[r.question_id].values())
        plan = bridge.plan(with_hq)
        verdicts = bridge.events_for(plan)
        self.assertEqual([e["idempotency_key"] for e in verdicts],
                         [P.verdict_key(plan.row.question_id, v.site, v.seq) for v in plan.verdicts])
        by_site = {e["payload"]["site"]: e for e in verdicts}
        for v in plan.verdicts:
            with self.subTest(site=v.site):
                payload = by_site[v.site]["payload"]
                self.assertEqual(payload["source"], v.source)
                self.assertEqual(payload["sha256"], v.sha256 if v.source == "site" else None)
                self.assertEqual(payload["verdict"], v.verdict)
                self.assertEqual(payload["window"], plan.question["window"])
        [conclusion] = bridge.events_for(plan, ["evt_a", "evt_b", "evt_c"])
        self.assertEqual(conclusion["idempotency_key"], P.conclusion_key(plan.row.conclusion_id, 1))
        memory = conclusion["memory"]
        self.assertEqual((memory["value"], memory["entity"], memory["slot"], memory["source_event_ids"]),
                         (plan.row.status, plan.question["params"]["entity_id"], "crack", ["evt_a", "evt_b", "evt_c"]))
        self.assertEqual(memory["metadata"]["snapshot_week"], SNAPSHOT_WEEK)
        self.assertEqual(memory["metadata"]["routed_sites"], list(plan.route))
        self.assertEqual(memory["metadata"]["route_label"], "R")
        self.assertEqual(memory["text"], P.conclusion_text(DQ, plan.question, plan.row.status))

    def test_the_bridge_without_a_broker_publishes_n_events_in_order_and_moves_the_watermark(self) -> None:
        client = FakeClient()
        wm = Path(self.tmp.name) / "bridge" / "watermark.json"
        bridge = B.HqBridge(self.spec.path, DQ, P.Publisher(client), watermark=wm)  # type: ignore[arg-type]
        report = bridge.run()
        self.assertEqual((report.conclusions_pending, report.conclusions, report.verdict_events,
                          report.conclusion_events, report.publishes, report.created, report.duplicate),
                         (20, 20, 60, 20, 80, 80, 0))
        self.assertTrue(report.store_unchanged)
        self.assertEqual(len(report.memory_ids_created), 20)
        types = [e["type"] for e in client.calls]
        self.assertEqual(types, ([P.VERDICT_EVENT] * 3 + [P.CONCLUSION_EVENT]) * 20)
        for i, row in enumerate(report.rows):
            with self.subTest(i=i):
                conclusion_call = client.calls[4 * i + 3]
                self.assertEqual(conclusion_call["payload"]["verdict_event_ids"], row.verdict_event_ids)
                self.assertEqual(row.verdict_event_ids, [expected_event_id(c["idempotency_key"])
                                                         for c in client.calls[4 * i:4 * i + 3]])
        self.assertEqual(B.load_watermark(wm), B.Position.of(self.reader.conclusions()[-1]))
        self.assertEqual(bridge.pending(), [])
        self.assertEqual(bridge.run().publishes, 0)
        text = json.dumps(report.to_dict())
        for word in ("narrative", "evidence_ref", "record_ref", "verdict_id", "support_bucket"):
            self.assertNotIn(word, text)

    def test_the_watermark_resumes_where_a_run_stopped(self) -> None:
        wm = Path(self.tmp.name) / "bridge" / "watermark.json"
        client = FakeClient()
        bridge = B.HqBridge(self.spec.path, DQ, P.Publisher(client), watermark=wm)  # type: ignore[arg-type]
        first = bridge.run(limit=5)
        self.assertEqual((first.conclusions_pending, first.conclusions, first.publishes), (20, 5, 20))
        second = B.HqBridge(self.spec.path, DQ, P.Publisher(client), watermark=wm)  # type: ignore[arg-type]
        self.assertEqual(len(second.pending()), 15)
        report = second.run()
        self.assertEqual((report.conclusions_pending, report.conclusions, report.publishes), (15, 15, 60))
        self.assertEqual([r.conclusion_id for r in first.rows] + [r.conclusion_id for r in report.rows],
                         [self.spec.conclusion_id(q["question_id"]) for q in self.spec.questions])
        self.assertEqual(len({c["idempotency_key"] for c in client.calls}), 80)


class BridgeRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_missing_store_is_refused(self) -> None:
        publisher = P.Publisher(FakeClient())  # type: ignore[arg-type]
        bridge = B.HqBridge(Path(self.tmp.name) / "nowhere.sqlite3", DQ, publisher)
        with self.assertRaises(B.BridgeError):
            bridge.pending()

    def test_a_question_without_a_detection_run_is_refused_not_guessed(self) -> None:
        spec = build_hq_store(Path(self.tmp.name) / "norun", questions=2, run_id=None)
        bridge = B.HqBridge(spec.path, DQ, P.Publisher(FakeClient()))  # type: ignore[arg-type]
        with self.assertRaises(B.BridgeError) as cm:
            bridge.run()
        self.assertIn("names no detection run", str(cm.exception))

    def test_a_question_whose_candidate_was_never_saved_is_refused(self) -> None:
        spec = build_hq_store(Path(self.tmp.name) / "nocand", questions=2, save_run=False)
        bridge = B.HqBridge(spec.path, DQ, P.Publisher(FakeClient()))  # type: ignore[arg-type]
        with self.assertRaises(B.BridgeError) as cm:
            bridge.run()
        self.assertIn("no candidate snapshot week", str(cm.exception))

    def test_a_verdict_whose_stored_sha256_does_not_hold_is_refused(self) -> None:
        spec = build_hq_store(Path(self.tmp.name) / "sha", questions=1)
        bridge = B.HqBridge(spec.path, DQ, P.Publisher(FakeClient()))  # type: ignore[arg-type]
        real = bridge.reader.verdicts

        def tampered(qid: str) -> tuple[Any, ...]:
            return tuple(v._replace(sha256="0" * 64) for v in real(qid))

        bridge.reader.verdicts = tampered  # type: ignore[method-assign]
        with self.assertRaises(B.BridgeError) as cm:
            bridge.run()
        self.assertIn("sha256 does not match", str(cm.exception))

    def test_nothing_is_published_when_a_later_row_cannot_be_planned(self) -> None:
        spec = build_hq_store(Path(self.tmp.name) / "half", questions=3, save_run=False)
        client = FakeClient()
        with self.assertRaises(B.BridgeError):
            B.HqBridge(spec.path, DQ, P.Publisher(client)).run()  # type: ignore[arg-type]
        self.assertEqual(client.calls, [])

    def test_a_question_under_another_pack_and_a_body_that_disagrees_with_its_row_are_refused(self) -> None:
        from mycelic.collective.packs.loader import load_pack

        spec = build_hq_store(Path(self.tmp.name) / "pack", questions=1)
        publisher = P.Publisher(FakeClient())  # type: ignore[arg-type]
        other = B.HqBridge(spec.path, load_pack("claims_integrity"), publisher)
        with self.assertRaises(B.BridgeError) as cm:
            other.run()
        self.assertIn("another pack hash", str(cm.exception))
        bridge = B.HqBridge(spec.path, DQ, P.Publisher(FakeClient()))  # type: ignore[arg-type]
        real = bridge.reader.verdicts

        def flipped(qid: str) -> tuple[Any, ...]:
            # the row says refute where the (correctly hashed) body says confirm
            rows = []
            for v in real(qid):
                body = dict(json.loads(v.body))
                body["verdict"] = "confirm" if body["verdict"] != "confirm" else "refute"
                data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                rows.append(v._replace(body=data, sha256=sha256_hex(data)))
            return tuple(rows)

        bridge.reader.verdicts = flipped  # type: ignore[method-assign]
        with self.assertRaises(B.BridgeError) as cm:
            bridge.run()
        self.assertIn("disagrees with its row", str(cm.exception))


# =================================================================================================== the live fabric

class LiveBridgeTests(unittest.TestCase):
    """The bridge against a real nats-server and service: N = 80, the broker killed after k = 40 and restarted,
    the rerun, the resume."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="e2e-bridge-")
        cls.root = Path(cls.tmp.name)
        cls.spec = build_hq_store(cls.root / "hq")
        cls.fabric = LiveFabric(cls.root / "fabric")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fabric.close()
        cls.tmp.cleanup()

    def bridge(self, name: str, on_publish: Any = None) -> B.HqBridge:
        here = self.root / name
        publisher = P.Publisher(self.fabric.hq, watermark=here / "publish_watermark.json",
                                ledger=here / "publishes.jsonl", on_publish=on_publish)
        return B.HqBridge(self.spec.path, DQ, publisher, watermark=here / "watermark.json")

    def test_broker_killed_mid_publish_then_restarted_applies_every_event_once_and_the_rerun_adds_nothing(self) -> None:
        fabric, driver, spec = self.fabric, self.fabric.driver, self.spec
        n = spec.publishes
        k = n // 2
        self.assertGreaterEqual(n, 80)
        self.assertTrue(1 <= k <= n - 10)
        killed_at: list[int] = []

        def kill_after_k(count: int, result: P.PublishResult) -> None:
            if count == k:
                driver.kill("nats")
                killed_at.append(count)
                time.sleep(1.0)

        # ---- drill 3: the main publish with the broker kill
        sha_before = B.HqStoreReader(spec.path).file_sha256()
        bridge = self.bridge("run1", on_publish=kill_after_k)
        report = bridge.run()
        self.assertEqual(killed_at, [k])
        self.assertEqual((report.publishes, report.verdict_events, report.conclusion_events, report.created,
                          report.duplicate, report.ambiguous), (n, 60, 20, n, 0, 0))
        self.assertTrue(report.store_unchanged)
        self.assertEqual(report.store_sha256_before, sha_before)
        outage = fabric.counts()
        self.assertGreaterEqual(outage["outbox_pending"], 1, "writes during the outage must sit in the outbox")
        self.assertEqual((outage["connected"], outage["status"]), (False, "degraded"))

        driver.start("nats")
        status = fabric.wait_idle(180.0)
        counts = drills.fabric_counts(status)
        self.assertEqual((counts["outbox_pending"], counts["consumer_pending"], counts["connected"]), (0, 0, True))
        self.assertGreaterEqual(counts["reconnects"], 1)

        ids = P.event_ids(bridge.publisher.results)
        api_ids = ids["agent.event"] + ids["memory.observed"]
        self.assertEqual((len(ids["agent.event"]), len(set(ids["agent.event"])), len(ids["memory.observed"])),
                         (n, n, 20))
        rows = {e["event_id"]: e for e in fabric.events()}
        for eid in api_ids:
            with self.subTest(event=eid):
                self.assertEqual((rows[eid]["status"], rows[eid]["applied_at"] is not None), ("applied", True))
        self.assertEqual(sum(1 for e in rows.values() if e["kind"] == "agent.event"), n)
        self.assertEqual(sum(1 for e in rows.values() if e["kind"] == "memory.observed"), 20)

        # ---- M3: every stream message by sequence verifies under the driver's key; every API id exactly once
        stream = drills.read_stream(driver)
        self.assertEqual((stream.fetched, stream.foreign), (counts["stream_messages"], 0))
        self.assertEqual(stream.verified, stream.fetched)
        self.assertEqual((stream.kinds["agent.event"], stream.kinds["memory.observed"]), (n, 20))
        self.assertEqual(stream.ids_in_stream(api_ids), (len(api_ids), 0))
        self.assertEqual(counts["stream_messages"], sum(stream.kinds.values()) + stream.foreign)

        # ---- the notes the fabric holds are the conclusions, no more
        notes = fabric.admin.list_memories(scope=P.ORG_ID, limit=500)
        self.assertEqual(sorted(m["memory_id"] for m in notes), sorted(report.memory_ids))
        self.assertEqual(len(report.memory_ids), 20)
        by_question = {m["metadata"]["question_id"]: m for m in notes}
        for row in report.rows:
            with self.subTest(conclusion=row.conclusion_id):
                note = by_question[row.question_id]
                # the service adds the conclusion event's own id to the memory's sources (``service.py:1229``)
                self.assertEqual((note["metadata"]["value"], note["metadata"]["snapshot_week"],
                                  note["metadata"]["routed_sites"], note["source_event_ids"], note["producer_id"]),
                                 (spec.statuses[row.question_id], SNAPSHOT_WEEK, list(spec.routes[row.question_id]),
                                  row.verdict_event_ids + [row.conclusion_event_id], P.AGENT_ID))
                self.assertEqual(note["memory_id"], expected_memory_id(P.conclusion_key(row.conclusion_id, 1)))
        lineage = fabric.admin.lineage(report.rows[0].memory_id)
        self.assertTrue(lineage["evidence"]["reconstructable"])
        self.assertTrue(set(report.rows[0].verdict_event_ids) <= set(lineage["evidence"]["source_event_ids"]))

        # ---- drill 7, bridge half: a rerun from an empty watermark creates nothing and leaves the stream alone
        before = fabric.counts()
        rerun = self.bridge("run2").run()
        self.assertEqual((rerun.publishes, rerun.created, rerun.duplicate, rerun.ambiguous), (n, 0, n, 0))
        self.assertEqual((rerun.memory_ids_created, rerun.event_ids_created), ([], []))
        self.assertEqual(sorted(rerun.memory_ids), sorted(report.memory_ids))
        self.assertEqual([r.verdict_event_ids for r in rerun.rows], [r.verdict_event_ids for r in report.rows])
        after = drills.fabric_counts(fabric.wait_idle())
        self.assertEqual(after["stream_messages"], before["stream_messages"])
        self.assertEqual(sorted(m["memory_id"] for m in fabric.admin.list_memories(scope=P.ORG_ID, limit=500)),
                         sorted(report.memory_ids))
        self.assertEqual(len(fabric.events()), len(rows))
        self.assertEqual(B.HqStoreReader(spec.path).file_sha256(), sha_before)

        # ---- the first bridge's own watermark: nothing pending, a third run publishes nothing
        resumed = self.bridge("run1")
        self.assertEqual(resumed.pending(), [])
        self.assertEqual(resumed.run().publishes, 0)
        text = json.dumps(report.to_dict()) + json.dumps(rerun.to_dict())
        self.assertNotIn("narrative", text)
        self.assertIsNone(re.search(r"\[d[0-9]{3}\]", text))


# =================================================================================================== criterion 7

class ImportGuardTests(unittest.TestCase):
    """c7: the bridge modules import none of ``FORBIDDEN_HQ``, the site module or ``mycelic.service``, statically and
    in a fresh interpreter (``run.py:97-126``)."""

    def test_publish_api_and_hq_bridge_import_nothing_forbidden(self) -> None:
        for name in ("publish_api", "hq_bridge"):
            with self.subTest(module=name):
                path = ROOT / "research" / "e2e_ingest" / f"{name}.py"
                static = [m for m in static_imports(path)
                          if any(m == f or m.startswith(f + ".") for f in BRIDGE_FORBIDDEN)]
                self.assertEqual(static, [])
                self.assertEqual(imports_loaded(f"research.e2e_ingest.{name}", BRIDGE_FORBIDDEN), [])

    def test_the_drills_load_neither_numpy_nor_the_retrieval_package_nor_the_service(self) -> None:
        self.assertEqual(imports_loaded("research.e2e_ingest.drills",
                                        ("numpy", "NeuralGraph.research.retrieval", "NeuralGraph.llm_backend",
                                         "mycelic.service", "research.routing_spike.site_process")), [])

    def test_the_bridge_opens_the_store_read_only_and_has_no_write_statement(self) -> None:
        source = (ROOT / "research" / "e2e_ingest" / "hq_bridge.py").read_text(encoding="utf-8")
        connects = [line for line in source.splitlines() if "sqlite3.connect(" in line]
        self.assertTrue(connects)
        self.assertTrue(all("mode=ro" in line and "uri=True" in line for line in connects), connects)
        for statement in ("INSERT", "UPDATE", "DELETE", "BEGIN", "CREATE", "DROP", "PRAGMA"):
            self.assertNotRegex(source, rf"\b{statement}\b")

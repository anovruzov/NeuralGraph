"""The arena console's contracts.  Every fixture here is SYNTHETIC and says so in its name: these tests exercise
the store, the tails and the server with hand-written files, never with a claim that a swarm ran."""
from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from research.arena import concurrency
from research.arena.events import Event, EventStore
from research.arena.server import App, Config, ConsoleServer
from research.arena.sources import GitSource, HarnessSource, SwarmSource, Tail

SYNTHETIC = "SYNTHETIC FIXTURE -- not a run"


class EventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ids_are_monotonic_persisted_and_replayable_from_any_point(self) -> None:
        s = EventStore(self.path, keep=3)
        for i in range(5):
            s.append(tag="LOG", source="t", msg=f"{SYNTHETIC} {i}")
        self.assertEqual([e.id for e in s.since(0)], [1, 2, 3, 4, 5])          # older than the ring: from disk
        self.assertEqual([e.id for e in s.since(3)], [4, 5])                    # inside the ring
        self.assertEqual(s.latest_id, 5)
        s.close()
        s2 = EventStore(self.path, keep=3)                                     # a restart continues the sequence
        self.assertEqual(s2.latest_id, 5)
        self.assertEqual(s2.append(tag="LOG", source="t", msg=SYNTHETIC).id, 6)
        lines = [json.loads(l) for l in self.path.read_text().splitlines() if l]
        self.assertEqual([l["id"] for l in lines], [1, 2, 3, 4, 5, 6])
        s2.close()

    def test_unknown_tag_is_refused_and_wait_returns_on_append(self) -> None:
        s = EventStore(self.path)
        with self.assertRaises(ValueError):
            s.append(tag="VIBES", source="t", msg=SYNTHETIC)
        got: list[Event] = []
        t = threading.Thread(target=lambda: got.extend(s.wait(0, 5.0)))
        t.start()
        time.sleep(0.05)
        s.append(tag="SPAWN", source="t", msg=SYNTHETIC, agent="a1")
        t.join(5)
        self.assertEqual([e.tag for e in got], ["SPAWN"])
        self.assertEqual(s.wait(1, 0.05), [])                                   # times out empty, does not block
        s.close()


class TailTests(unittest.TestCase):
    def test_reads_incrementally_and_holds_a_partial_line(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.jsonl"
            t = Tail(p)
            self.assertEqual(t.read(), [])                                      # absent file is not an error
            p.write_bytes(b'{"a":1}\n{"b":')
            self.assertEqual(t.read(), [(1, '{"a":1}')])
            with open(p, "ab") as fh:
                fh.write(b'2}\n')
            self.assertEqual(t.read(), [(2, '{"b":2}')])
            self.assertEqual(t.read(), [])


class SwarmSourceTests(unittest.TestCase):
    def test_stamps_become_spawn_artifact_complete_once_each_and_the_registry_states_follow(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "swarm"
            (root / "A1").mkdir(parents=True)
            (root / "A1" / "START").write_text("2026-10-24T15:48:27Z")
            store = EventStore(Path(d) / "ev.jsonl")
            src = SwarmSource(root, roles={"A1": {"family": SYNTHETIC}})
            src.poll(store)
            self.assertEqual([e.tag for e in store.since(0)], ["SPAWN"])
            self.assertEqual(store.since(0)[0].at, "2026-10-24T15:48:27Z")
            self.assertEqual(src.registry()[0]["state"], "RUNNING")
            (root / "A1" / "OUT.md").write_text("x")
            (root / "A1" / "END").write_text("2026-10-24T15:58:00Z")
            src.poll(store)
            src.poll(store)                                                     # idempotent
            self.assertEqual([e.tag for e in store.since(0)], ["SPAWN", "ARTIFACT", "COMPLETE"])
            self.assertEqual(src.registry()[0]["state"], "COMPLETE")
            self.assertEqual(src.registry()[0]["artifacts"], ["OUT.md"])
            # a restart with the snapshot re-emits nothing
            src2 = SwarmSource(root)
            src2.restore(src.snapshot())
            src2.poll(store)
            self.assertEqual(len(store.since(0)), 3)
            store.close()


class HarnessSourceTests(unittest.TestCase):
    def test_wire_frames_retrievals_and_report_checks_become_typed_events_with_refs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            (run / "main" / "hq").mkdir(parents=True)
            (run / "main" / "edge").mkdir(parents=True)
            body = json.dumps({"question_id": "q" * 64, "candidate_key": "entity:E-X:chain_y",
                               "window": {"start_week": "2024-W07", "end_week": "2024-W12"}})
            req = {"direction": "request", "site": "s1", "bytes": 1, "sha256": "0" * 64,
                   "frame": json.dumps({"kind": "question", "body": body})}
            rep = {"direction": "response", "site": "s1", "bytes": 1, "sha256": "1" * 64,
                   "frame": json.dumps({"kind": "answer", "body": json.dumps({"question_id": "q" * 64, "verdict": "confirm"})})}
            (run / "main" / "hq" / "wire.jsonl").write_text(json.dumps(req) + "\n" + json.dumps(rep) + "\n")
            (run / "main" / "edge" / "site-s1.tesseract.jsonl").write_text(
                json.dumps({"question_id": "q" * 64, "seconds": 0.5, "nodes": 10, "returned": 3, "read": 2}) + "\n")
            (run / "e2e-ingest-report.json").write_text(json.dumps(
                {"phase": "questions", "checks": {"c9_synthetic": {"ok": False, "failed": ["x"], "assertions": 2}},
                 "verdict": {"pass": False, "checks_pass": False, "reason": SYNTHETIC, "unevaluated_required": ["c10"]}}))
            store = EventStore(Path(d) / "ev.jsonl")
            src = HarnessSource(run, run_id="synthetic")
            src.poll(store)
            tags = [e.tag for e in store.since(0)]
            self.assertEqual(sorted(tags), sorted(["ASK", "REPLY", "ROUTE", "PHASE", "FAIL", "FAIL"]))
            ask = next(e for e in store.since(0) if e.tag == "ASK")
            self.assertEqual(ask.ref["line"], 1)
            self.assertTrue(ask.ref["path"].endswith("wire.jsonl"))
            self.assertEqual(ask.data["candidate_key"], "entity:E-X:chain_y")
            reply = next(e for e in store.since(0) if e.tag == "REPLY")
            self.assertEqual((reply.agent, reply.data["verdict"]), ("s1", "confirm"))
            self.assertEqual(src.summary()["sites"], ["s1"])
            src.poll(store)                                                     # nothing new: nothing emitted
            self.assertEqual(len(store.since(0)), 6)
            store.close()


class ConcurrencyTests(unittest.TestCase):
    def test_omega_is_the_largest_set_of_overlapping_intervals(self) -> None:
        I = concurrency.Interval
        ivs = [I("a", 0, 10), I("b", 2, 12), I("c", 4, 6), I("d", 11, 20), I("e", 20, 25)]
        self.assertEqual(concurrency.max_overlap(ivs, now=30), (3, 4))          # a, b, c at t=4
        self.assertEqual(concurrency.max_overlap([I("a", 0, 1), I("b", 1, 2)], now=5)[0], 1)   # touching != overlapping
        self.assertEqual(concurrency.max_overlap([I("a", 0, None), I("b", 1, None)], now=5)[0], 2)  # open ends at now
        self.assertEqual(concurrency.max_overlap([], now=5), (0, None))

    def test_stats_count_states_separately_and_never_invent_queue_latency(self) -> None:
        reg = [{"id": "a", "state": "RUNNING", "start": "2026-10-24T15:00:00Z", "end": None},
               {"id": "b", "state": "COMPLETE", "start": "2026-10-24T15:00:00Z", "end": "2026-10-24T15:10:00Z"},
               {"id": "c", "state": "QUEUED", "start": None, "end": None}]
        now = concurrency.parse_ts("2026-10-24T15:20:00Z")
        s = concurrency.stats(reg, now)
        self.assertEqual((s["registered"], s["queued"], s["running"], s["complete"]), (3, 1, 1, 1))
        self.assertEqual((s["omega_now"], s["omega_peak"]), (1, 2))
        self.assertIsNone(s["queue_latency"])
        self.assertIsNone(s["model_requests_in_flight"])
        self.assertAlmostEqual(s["mean_runtime_s"], 600.0)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.swarm = d / "swarm"
        (self.swarm / "Z1").mkdir(parents=True)
        (self.swarm / "Z1" / "START").write_text("2026-10-24T15:48:27Z")
        cfg = Config(state_dir=d / "state", swarm_dir=self.swarm, poll_seconds=0.05, artifact_roots=[self.swarm])
        self.app = App(cfg)
        self.srv = ConsoleServer(self.app, "127.0.0.1", 0)
        self.srv.start()
        self.base = f"http://127.0.0.1:{self.srv.port}"
        time.sleep(0.2)

    def tearDown(self) -> None:
        self.srv.stop()
        self.tmp.cleanup()

    def _get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_state_page_and_artifact_allowlist(self) -> None:
        code, body = self._get("/state")
        st = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(st["agents"][0]["state"], "RUNNING")
        self.assertEqual(st["concurrency"]["running"], 1)
        self.assertEqual(self._get("/")[0], 200)
        self.assertIn(b"ARENA", self._get("/")[1])
        self.assertEqual(self._get("/artifact?path=" + str(self.swarm / "Z1" / "START"))[1], b"2026-10-24T15:48:27Z")
        self.assertEqual(self._get("/artifact?path=/etc/hostname")[0], 403)

    def test_sse_resumes_from_last_event_id_in_order(self) -> None:
        for i in range(3):
            self.app.store.append(tag="LOG", source="t", msg=f"{SYNTHETIC} {i}")
        last = self.app.store.latest_id
        s = socket.create_connection(("127.0.0.1", self.srv.port), timeout=5)
        s.sendall(f"GET /events HTTP/1.0\r\nHost: x\r\nLast-Event-ID: {last - 1}\r\n\r\n".encode())
        buf = b""
        deadline = time.time() + 5
        while b"\nid: %d\n" % last not in buf and time.time() < deadline:
            buf += s.recv(65536)
        s.close()
        text = buf.decode()
        self.assertIn("event: snapshot", text)
        ids = [int(l.split(": ")[1]) for l in text.splitlines() if l.startswith("id: ")]
        self.assertEqual(ids, [last])                                           # only what is newer than the cursor

    def test_controls_refuse_destruction_without_confirm_and_unknown_actions(self) -> None:
        def post(obj: dict) -> tuple[int, dict]:
            req = urllib.request.Request(self.base + "/control", data=json.dumps(obj).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        self.assertEqual(post({"action": "stop"})[0], 409)                     # no run, and no confirm
        self.assertEqual(post({"action": "dance"})[0], 400)
        code, body = post({"action": "start", "scenario": "short"})
        self.assertEqual(code, 409)                                             # no --runs-root / scenarios configured
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()

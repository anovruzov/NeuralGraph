"""The persistent site graph: the re-implemented constructors differ from the spike's in the named hunks only (M17);
verdict bytes equal the spike's site process on the spike's seed-1 planted world; on the 200-user EMERGENCE world,
criterion 8 (one node per own record, node ids the record refs, read from the graph file, M9), criterion 3's site
half (re-ingest: same node ids, same count, same top 10 and full order on the 20 fixed queries at every site), the
``as_of`` bound, crash and resume by the watermark, and process mode with a kill and a restart.

No assertion message here carries a record's ref or text: ids are compared through digests.
"""
from __future__ import annotations

import difflib
import inspect
import os
import signal
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mycelic.collective.edge.egress import check_artifact
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.jsonio import canonical_bytes, sha256_hex, strict_load
from NeuralGraph.research.retrieval.sqlite_storage import SQLiteNeuralGraphStorage
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest import site_graph as sg
from research.routing_spike import wire
from research.routing_spike.run import site_configs as spike_site_configs
from research.routing_spike.site_process import SiteProcess, TesseractRetrieval, query_text, write_config
from research.routing_spike.world import backup_sqlite, restore_store
from tests.routing_spike.helpers import questions as spike_questions
from tests.routing_spike.helpers import small_world


def _hunks(a, b) -> list[str]:
    """The changed lines (stripped) of the unified diff between two functions' sources."""
    sa = [line.strip() for line in inspect.getsource(a).splitlines()]
    sb = [line.strip() for line in inspect.getsource(b).splitlines()]
    return [line for line in difflib.unified_diff(sa, sb, lineterm="", n=0)
            if (line.startswith("-") or line.startswith("+")) and not line.startswith(("---", "+++"))]


def _order_digest(ids) -> str:
    """A digest of an ordered list of ids (the order matters)."""
    return sha256_hex("\n".join(ids))


def _leaks(text: str, prefixes) -> int:
    return (len(ew.RECORD_REF_RE.findall(text)) + len(ew.NARRATIVE_MARKER_RE.findall(text))
            + sum(1 for p in prefixes if p in text))


def _diag(edge: Path, sid: str) -> list[dict]:
    path = edge / f"site-{sid}.tesseract.jsonl"
    return [strict_load(line) for line in path.read_bytes().split(b"\n") if line] if path.exists() else []


def _received(edge: Path, sid: str) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{edge / f'site-{sid}.sqlite3'}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT record_ref, received_date FROM records WHERE forwarded_in = 0 "
                                 "ORDER BY seq").fetchall())
    finally:
        conn.close()


# --------------------------------------------------------------------------------------------------- M17

class SourceHunkTests(unittest.TestCase):
    """Each re-implemented ``__init__`` is the spike's, with exactly the named hunks (M17)."""

    def test_the_persistent_retrieval_init_drops_the_unlink_and_syncs_instead_of_building(self) -> None:
        self.assertEqual(_hunks(TesseractRetrieval.__init__, sg.PersistentTesseractRetrieval.__init__), [
            '-for suffix in ("", "-wal", "-shm"):',
            '-Path(str(path) + suffix).unlink(missing_ok=True)',
            '-self.nodes = self._loop.run_until_complete(self._build())',
            '-self.build_seconds = 0.0',
            '+self.graph_path = path',
            '+self.sync: dict[str, Any] = {}',
            '+t0 = time.perf_counter()',
            '+self.nodes = self._loop.run_until_complete(self._sync())',
            '+self.build_seconds = time.perf_counter() - t0',
        ])

    def test_the_site_process_init_replaces_step_5_only(self) -> None:
        self.assertEqual(_hunks(SiteProcess.__init__, sg.E2ESiteProcess.__init__), [
            '-self.retrieval = TesseractRetrieval(self, graph_path=config.graph_path,',
            '-max_records=config.max_records, embed_dim=config.embed_dim)',
            '+self.retrieval = PersistentTesseractRetrieval(self, graph_path=config.graph_path,',
            '+max_records=config.max_records,',
            '+embed_dim=config.embed_dim)',
        ])

    def test_the_child_endpoint_init_changes_the_module_and_the_logged_init_only(self) -> None:
        self.assertEqual(_hunks(wire.ChildProcessEndpoint.__init__, sg.E2EChildProcessEndpoint.__init__), [
            '-super().__init__(site_id, log_path)',
            '+wire._Logged.__init__(self, site_id, log_path)',
            '-env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")',
            '-self._proc = subprocess.Popen([python or sys.executable, "-m", "research.routing_spike.site_process",',
            '-"--config", str(config_path)], cwd=str(ROOT), env=env,',
            '+env["PYTHONPATH"] = str(wire.ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")',
            '+self._proc = subprocess.Popen([python or sys.executable, "-m", MODULE,',
            '+"--config", str(config_path)], cwd=str(wire.ROOT), env=env,',
        ])
        self.assertEqual(sg.MODULE, "research.e2e_ingest.site_graph")

    def test_the_inherited_methods_are_the_spikes_own(self) -> None:
        for name in ("_own_records", "_local_retrieve", "union", "entity_matched", "retrieve", "close"):
            self.assertNotIn(name, sg.PersistentTesseractRetrieval.__dict__, name)
            self.assertIs(getattr(sg.PersistentTesseractRetrieval, name), getattr(TesseractRetrieval, name))
        self.assertEqual(sorted(k for k in sg.PersistentTesseractRetrieval.__dict__ if not k.startswith("__")),
                         ["_node", "_pending", "_set_watermark", "_sync", "_watermark", "name", "rank"])
        self.assertEqual([k for k in sg.E2ESiteProcess.__dict__ if not k.startswith("__")], [])
        for name in ("handle", "diag", "close"):
            self.assertIs(getattr(sg.E2ESiteProcess, name), getattr(SiteProcess, name))
        self.assertEqual([k for k in sg.E2EChildProcessEndpoint.__dict__ if not k.startswith("__")], ["pid"])
        for name in ("request", "close", "log"):
            self.assertIs(getattr(sg.E2EChildProcessEndpoint, name), getattr(wire.ChildProcessEndpoint, name))


# --------------------------------------------------------------------------------------------------- the spike's world

class SpikeWorldTests(unittest.TestCase):
    """The spike's seed-1 planted world, four candidates: byte identity with the spike's site process, a restart in
    process, and a child process killed and reopened."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.world = small_world(Path(cls.tmp.name) / "w", seed=1, planted=True, top_n=4)
        cls.questions = spike_questions(cls.world)
        cls.prefixes = sorted({r["narrative"][:24] for r in cls.world.records if len(r["narrative"]) >= 24})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.pipeline.store.close()
        cls.tmp.cleanup()

    def _copy(self, name: str) -> tuple[Path, Path]:
        base = Path(self.tmp.name) / name
        for sid in self.world.site_ids:
            restore_store(sid, self.world.workdir / "pristine", base / "edge")
        return base / "edge", base / "hq"

    def _configs(self, edge: Path, hq: Path):
        return spike_site_configs(self.world, edge=edge, hq_dir=hq)

    def test_verdict_bytes_equal_the_spikes_site_process_at_every_site(self) -> None:
        self.assertEqual(len(self.questions), 4)
        spike_edge, spike_hq = self._copy("spike")
        mine_edge, mine_hq = self._copy("mine")
        pairs = same = 0
        for sid in self.world.site_ids:
            spike = SiteProcess(self._configs(spike_edge, spike_hq)[sid])
            mine = sg.E2ESiteProcess(self._configs(mine_edge, mine_hq)[sid])
            try:
                self.assertIsInstance(mine.retrieval, sg.PersistentTesseractRetrieval)
                self.assertEqual(mine.retrieval.nodes, spike.retrieval.nodes)
                self.assertEqual(sorted(mine.retrieval._by_ref), sorted(spike.retrieval._by_ref))
                self.assertEqual(mine.handle("describe", b""), spike.handle("describe", b""))
                for q in self.questions:
                    pairs += 1
                    same += spike.handle("question", canonical_bytes(q)) == mine.handle("question", canonical_bytes(q))
            finally:
                spike.close()
                mine.close()
        self.assertEqual((pairs, same), (24, 24))
        # the spike's graph is rebuilt at every start; the persistent one is not: the file keeps its nodes, and the
        # watermark is the seq of the last own record (this world forwards records between sites, so own records
        # are fewer than the store's rows)
        for sid in self.world.site_ids:
            conn = sqlite3.connect(f"file:{mine_edge / f'site-{sid}.sqlite3'}?mode=ro", uri=True)
            try:
                last_own = conn.execute("SELECT MAX(seq) FROM records WHERE forwarded_in = 0").fetchone()[0]
                own = conn.execute("SELECT COUNT(*) FROM records WHERE forwarded_in = 0").fetchone()[0]
                rows = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            finally:
                conn.close()
            graph = mine_edge / f"site-{sid}.graph.sqlite3"
            with self.subTest(site=sid):
                self.assertEqual(sg.graph_watermark(graph, sid), last_own)
                self.assertEqual(len(sg.graph_node_ids(graph, sid)), own)
                self.assertGreaterEqual(last_own, own)
                self.assertGreater(rows, own)

    def test_a_restart_in_process_adds_no_node_and_answers_the_remaining_questions_identically(self) -> None:
        sid = self.world.site_ids[1]
        ref_edge, ref_hq = self._copy("ref")
        reference = sg.E2ESiteProcess(self._configs(ref_edge, ref_hq)[sid])
        try:
            expected = [reference.handle("question", canonical_bytes(q)) for q in self.questions]
            nodes = reference.retrieval.nodes
        finally:
            reference.close()
        edge, hq = self._copy("restart")
        cfg = self._configs(edge, hq)[sid]
        first = sg.E2ESiteProcess(cfg)
        try:
            self.assertEqual(first.retrieval.sync["nodes_added"], nodes)
            got = [first.handle("question", canonical_bytes(q)) for q in self.questions[:2]]
            ids_before = sg.graph_node_ids(cfg.graph_path, sid)
        finally:
            first.close()
        second = sg.E2ESiteProcess(cfg)
        try:
            self.assertEqual(second.retrieval.sync["nodes_added"], 0)
            self.assertEqual(second.retrieval.sync["nodes_upserted"], 0)
            self.assertEqual(second.retrieval.nodes, nodes)
            self.assertEqual(sg.node_ids_digest(sg.graph_node_ids(cfg.graph_path, sid)), sg.node_ids_digest(ids_before))
            got += [second.handle("question", canonical_bytes(q)) for q in self.questions[2:]]
        finally:
            second.close()
        self.assertEqual(got, expected)
        self.assertEqual([row["nodes_added"] for row in sg.read_sync_log(edge, sid)], [nodes, 0])

    def test_a_child_process_killed_and_reopened_answers_identically(self) -> None:
        sid = self.world.site_ids[2]
        ref_edge, ref_hq = self._copy("ref-child")
        reference = sg.E2ESiteProcess(self._configs(ref_edge, ref_hq)[sid])
        try:
            descriptor = reference.handle("describe", b"")
            expected = [reference.handle("question", canonical_bytes(q)) for q in self.questions]
            nodes = reference.retrieval.nodes
        finally:
            reference.close()
        edge, hq = self._copy("child")
        hq.mkdir(parents=True, exist_ok=True)              # HQ's directory, where the pipeline would have put it
        cfg = self._configs(edge, hq)[sid]
        path = write_config(cfg, Path(self.tmp.name) / "child" / "configs" / f"site-{sid}.json")
        endpoint = sg.E2EChildProcessEndpoint(sid, path, log_path=hq / "wire.jsonl")
        try:
            self.assertEqual(endpoint.request("describe", b""), descriptor)
            got = [endpoint.request("question", canonical_bytes(q)) for q in self.questions[:2]]
            os.kill(endpoint.pid, signal.SIGKILL)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and endpoint._proc.poll() is None:
                time.sleep(0.05)
            self.assertIsNotNone(endpoint._proc.poll())
            with self.assertRaises(wire.WireError) as caught:
                endpoint.request("question", canonical_bytes(self.questions[2]))
            self.assertEqual(caught.exception.code, "closed")
        finally:
            endpoint.close()
        reopened = sg.E2EChildProcessEndpoint(sid, path, log_path=hq / "wire.jsonl")
        try:
            got += [reopened.request("question", canonical_bytes(q)) for q in self.questions[2:]]
        finally:
            reopened.close()
        self.assertEqual(got, expected)
        self.assertEqual([row["nodes_added"] for row in sg.read_sync_log(edge, sid)], [nodes, 0])
        self.assertEqual(len(sg.graph_node_ids(cfg.graph_path, sid)), nodes)
        frames = (hq / "wire.jsonl").read_text(encoding="utf-8")
        self.assertEqual(_leaks(frames, self.prefixes), 0)


# --------------------------------------------------------------------------------------------------- EMERGENCE

class EmergenceGraphTests(unittest.TestCase):
    """The 200-user world for real: every site's persistent graph from its pipeline store."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.world = ew.build_emergence_world(200, 0, base / "w")
        w = cls.world
        for site in w.pipeline.sites.values():
            site.close()
        for sid in w.site_ids:
            backup_sqlite(w.workdir / "edge" / f"site-{sid}.sqlite3", w.workdir / "pristine" / f"site-{sid}.sqlite3")
        cls.questions = ew.fixed_questions(w, 20)
        cls.expected_refs = {sid: [r["record_ref"] for r in w.records if r["site"] == sid] for sid in w.site_ids}
        cls.prefixes = ew.narrative_prefixes(w.corpus)
        cls.edge, cls.hq = base / "main" / "edge", base / "main" / "hq"
        for sid in w.site_ids:
            restore_store(sid, w.workdir / "pristine", cls.edge)
        cls.configs = ew.site_configs(w, edge=cls.edge, hq_dir=cls.hq)
        cls.built: dict[str, dict] = {}
        for sid in w.site_ids:
            process = sg.E2ESiteProcess(cls.configs[sid])
            try:
                cls.built[sid] = {"nodes": process.retrieval.nodes, "sync": dict(process.retrieval.sync),
                                  "build_seconds": process.retrieval.build_seconds,
                                  "held_digest": sg.node_ids_digest(list(process.retrieval._by_ref))}
            finally:
                process.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.pipeline.store.close()
        cls.tmp.cleanup()

    def _fresh(self, name: str, sid: str):
        base = Path(self.tmp.name) / name
        restore_store(sid, self.world.workdir / "pristine", base / "edge")
        return ew.site_configs(self.world, edge=base / "edge", hq_dir=base / "hq")[sid]

    def test_c8_every_site_graph_holds_one_node_per_own_record_with_the_record_refs_as_ids(self) -> None:
        w = self.world
        total = 0
        for sid in w.site_ids:
            cfg, built = self.configs[sid], self.built[sid]
            ids = sg.graph_node_ids(cfg.graph_path, sid)              # mode=ro, after the process exited (M9)
            expected = self.expected_refs[sid]
            with self.subTest(site=sid):
                self.assertEqual(len(ids), w.per_site_records[sid])
                self.assertEqual(len(ids), built["nodes"])
                self.assertEqual(sg.node_ids_digest(ids), sg.node_ids_digest(expected))
                self.assertEqual(built["held_digest"], sg.node_ids_digest(expected))
                self.assertEqual(len(set(ids)), len(ids))
                self.assertEqual(sg.graph_watermark(cfg.graph_path, sid), len(expected))
                self.assertEqual(built["sync"]["nodes_added"], len(expected))
                self.assertEqual(built["sync"]["nodes_before"], 0)
                self.assertEqual(built["sync"]["seq_before"], 0)
                self.assertGreater(built["build_seconds"], 0.0)
                self.assertGreaterEqual(built["build_seconds"], built["sync"]["seconds"])
            total += len(ids)
        self.assertEqual(total, w.counts["records"])
        self.assertEqual(sum(w.per_site_records.values()), 12964)
        counts = sorted(self.built[s]["nodes"] for s in w.site_ids)
        self.assertEqual((counts[0], counts[-1]), (1458, 2574))

    def test_c3_reingest_leaves_the_node_ids_the_count_and_the_20_fixed_rankings_unchanged_at_every_site(self) -> None:
        w = self.world
        self.assertEqual(len(self.questions), 20)
        texts = [(query_text(w.pack, q), q["as_of"]) for q in self.questions]
        for sid in w.site_ids:
            cfg = self.configs[sid]
            before_ids = sg.graph_node_ids(cfg.graph_path, sid)
            process = sg.E2ESiteProcess(cfg)
            try:
                self.assertEqual(process.retrieval.sync["nodes_added"], 0)
                before = [process.retrieval.rank(text, as_of) for text, as_of in texts]
            finally:
                process.close()
            # drill 7: the site ingests its records again; the store reports every one a duplicate
            records = [r for r in w.records if r["site"] == sid]
            site = EdgeSite(w.pack, sid, self.edge, runtime=None, clock=lambda: f"{w.as_of}T12:00:00Z",
                            master_data=w.master_data[sid], hq_dir=self.hq)
            try:
                got = site.ingest(records)
            finally:
                site.close()
            with self.subTest(site=sid, step="reingest"):
                self.assertEqual((got.ingested, got.duplicates, sum(got.rejected.values())), (0, len(records), 0))
            process = sg.E2ESiteProcess(cfg)
            try:
                sync = dict(process.retrieval.sync)
                after = [process.retrieval.rank(text, as_of) for text, as_of in texts]
                nodes = process.retrieval.nodes
            finally:
                process.close()
            after_ids = sg.graph_node_ids(cfg.graph_path, sid)
            with self.subTest(site=sid, step="sync"):
                self.assertEqual((sync["nodes_added"], sync["nodes_upserted"]), (0, 0))
                self.assertEqual(nodes, len(before_ids))
                self.assertEqual(len(after_ids), len(before_ids))
                self.assertEqual(sg.node_ids_digest(after_ids), sg.node_ids_digest(before_ids))
            for i, (b, a) in enumerate(zip(before, after)):
                with self.subTest(site=sid, query=i):
                    self.assertEqual(len(b), len(a))
                    self.assertGreater(len(b), 0)
                    self.assertEqual(_order_digest(b[:10]), _order_digest(a[:10]))
                    self.assertEqual(_order_digest(b), _order_digest(a))

    def test_the_ranking_and_the_answers_see_only_records_received_on_or_before_as_of(self) -> None:
        w = self.world
        sid = max(w.site_ids, key=lambda s: w.per_site_records[s])
        cfg = self._fresh("asof", sid)
        edge = Path(cfg.edge_dir)
        received = _received(edge, sid)
        self.assertEqual(len(received), w.per_site_records[sid])
        process = sg.E2ESiteProcess(cfg)
        try:
            # the ranking alone
            # Tesseract returns the nodes above its charge threshold, every one received on or before as_of
            q = self.questions[0]
            text = query_text(w.pack, q)
            ranked = process.retrieval.rank(text, q["as_of"])
            self.assertGreater(len(ranked), 0)
            self.assertEqual(len(set(ranked)), len(ranked))
            self.assertTrue(all(received[ref] <= q["as_of"] for ref in ranked))
            self.assertLessEqual(len(ranked), sum(1 for day in received.values() if day <= q["as_of"]))
            self.assertEqual(process.retrieval.rank(text, q["as_of"], 10), ranked[:10])
            first_day = min(received.values())
            early = process.retrieval.rank(text, first_day)
            self.assertTrue(all(received[ref] <= first_day for ref in early))
            self.assertLessEqual(len(early), sum(1 for day in received.values() if day <= first_day))
            self.assertLess(len(early), len(process.retrieval.rank(text, w.as_of)))
            with self.assertRaises(ValueError):
                process.retrieval.rank(text, "2024-01-01T12:00:00Z")
            # the answers: one row of site diagnostics per question whose verdict read records
            verdicts = []
            for q in self.questions:
                data = process.handle("question", canonical_bytes(q))
                body = strict_load(data)
                self.assertIsNone(check_artifact(w.pack, sid, "verdict", body))
                self.assertEqual(_leaks(data.decode("utf-8"), self.prefixes), 0)
                verdicts.append(body["verdict"])
        finally:
            process.close()
        rows = {row["question_id"]: row for row in _diag(edge, sid)}
        by_id = {q["question_id"]: q for q in self.questions}
        self.assertTrue(rows)
        for qid, row in rows.items():
            as_of = by_id[qid]["as_of"]
            with self.subTest(question=qid[:16]):
                self.assertEqual(row["nodes"], w.per_site_records[sid])
                self.assertEqual(row["nodes_as_of"], sum(1 for day in received.values() if day <= as_of))
                self.assertTrue(all(received[ref] <= as_of for ref in row["refs"]))
                self.assertEqual(row["read"], len(row["refs"]))
                self.assertLessEqual(row["read"], ew.MAX_RECORDS)
                self.assertGreater(row["seconds"], 0.0)
        self.assertIn("confirm", verdicts)
        self.assertEqual(_leaks((edge / f"site-{sid}.egress.jsonl").read_text(encoding="utf-8"), self.prefixes), 0)
        self.assertEqual(_leaks(sg.sync_log_path(edge, sid).read_text(encoding="utf-8"), self.prefixes), 0)

    def test_a_crash_during_the_build_resumes_from_the_watermark_to_the_same_graph(self) -> None:
        w = self.world
        sid = max(w.site_ids, key=lambda s: w.per_site_records[s])
        total = w.per_site_records[sid]
        cfg = self._fresh("crash", sid)
        reference = self.built[sid]["held_digest"]
        original = SQLiteNeuralGraphStorage.save_nodes_batch
        calls = {"n": 0}

        async def failing(self_storage, nodes):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated crash before the third batch")
            return await original(self_storage, nodes)

        batch = 400
        with mock.patch.object(sg, "BATCH", batch), \
                mock.patch.object(SQLiteNeuralGraphStorage, "save_nodes_batch", failing):
            with self.assertRaises(RuntimeError):
                sg.E2ESiteProcess(cfg)
        # two batches committed, the watermark after them, nothing of the third
        self.assertEqual(len(sg.graph_node_ids(cfg.graph_path, sid)), 2 * batch)
        self.assertEqual(sg.graph_watermark(cfg.graph_path, sid), 2 * batch)
        resumed = sg.E2ESiteProcess(cfg)
        try:
            sync = dict(resumed.retrieval.sync)
            nodes = resumed.retrieval.nodes
        finally:
            resumed.close()
        self.assertEqual((sync["seq_before"], sync["nodes_before"], sync["nodes_added"], sync["nodes"]),
                         (2 * batch, 2 * batch, total - 2 * batch, total))
        self.assertEqual(nodes, total)
        self.assertEqual(sg.graph_watermark(cfg.graph_path, sid), total)
        self.assertEqual(sg.node_ids_digest(sg.graph_node_ids(cfg.graph_path, sid)), reference)
        # a watermark behind the nodes (a crash between a batch's commit and its watermark): the records past it are
        # upserted again, no node is added or lost, the graph is the same
        conn = sqlite3.connect(cfg.graph_path)
        try:
            conn.execute(f"UPDATE {sg.SYNC_TABLE} SET seq = ? WHERE session_key = ?", (batch, sg.SESSION_PREFIX + sid))
            conn.commit()
        finally:
            conn.close()
        again = sg.E2ESiteProcess(cfg)
        try:
            sync = dict(again.retrieval.sync)
        finally:
            again.close()
        self.assertEqual((sync["seq_before"], sync["nodes_added"], sync["nodes_upserted"], sync["nodes"]),
                         (batch, 0, total - batch, total))
        self.assertEqual(sg.graph_watermark(cfg.graph_path, sid), total)
        self.assertEqual(sg.node_ids_digest(sg.graph_node_ids(cfg.graph_path, sid)), reference)
        self.assertEqual([row["nodes_added"] for row in sg.read_sync_log(Path(cfg.edge_dir), sid)],
                         [total - 2 * batch, 0])

    def test_process_mode_answers_byte_identically_and_survives_a_kill(self) -> None:
        w = self.world
        sid = min(w.site_ids, key=lambda s: w.per_site_records[s])
        asked = self.questions[:6]
        ref_cfg = self._fresh("proc-ref", sid)
        reference = sg.E2ESiteProcess(ref_cfg)
        try:
            descriptor = reference.handle("describe", b"")
            expected = [reference.handle("question", canonical_bytes(q)) for q in asked]
            nodes = reference.retrieval.nodes
        finally:
            reference.close()
        cfg = self._fresh("proc", sid)
        hq = Path(cfg.hq_dir)
        hq.mkdir(parents=True, exist_ok=True)              # HQ's directory, where the pipeline would have put it
        path = write_config(cfg, Path(self.tmp.name) / "proc" / "configs" / f"site-{sid}.json")
        endpoint = sg.E2EChildProcessEndpoint(sid, path, log_path=hq / "wire.jsonl")
        try:
            self.assertEqual(endpoint.request("describe", b""), descriptor)
            got = [endpoint.request("question", canonical_bytes(q)) for q in asked[:3]]
            os.kill(endpoint.pid, signal.SIGKILL)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and endpoint._proc.poll() is None:
                time.sleep(0.05)
            with self.assertRaises(wire.WireError):
                endpoint.request("question", canonical_bytes(asked[3]))
        finally:
            endpoint.close()
        reopened = sg.E2EChildProcessEndpoint(sid, path, log_path=hq / "wire.jsonl")
        try:
            got += [reopened.request("question", canonical_bytes(q)) for q in asked[3:]]
        finally:
            reopened.close()
        self.assertEqual(got, expected)
        self.assertEqual(nodes, w.per_site_records[sid])
        self.assertEqual([row["nodes_added"] for row in sg.read_sync_log(Path(cfg.edge_dir), sid)], [nodes, 0])
        self.assertEqual(len(sg.graph_node_ids(cfg.graph_path, sid)), nodes)
        # every frame HQ sent or received is logged, the request the killed process never answered included
        frames = wire.read_frames(hq / "wire.jsonl")
        self.assertEqual(sum(1 for f in frames if f["direction"] == "request"), 1 + 3 + 1 + 3)
        answered = 0
        for f in frames:
            if f["direction"] == "response" and f["frame"]:
                try:
                    wire.decode_response(f["frame"].encode("utf-8"))
                    answered += 1
                except wire.WireError:
                    pass
        self.assertEqual(answered, 1 + 6)
        self.assertEqual(_leaks((hq / "wire.jsonl").read_text(encoding="utf-8"), self.prefixes), 0)
        endpoints = sg.open_endpoints({sid: cfg}, "in_process", hq / "wire.jsonl", Path(self.tmp.name) / "proc" / "c2")
        try:
            self.assertEqual(endpoints[sid].request("describe", b""), descriptor)
        finally:
            sg.close_endpoints(endpoints)
        with self.assertRaises(ValueError):
            sg.open_endpoints({sid: cfg}, "threads", hq / "wire.jsonl", Path(self.tmp.name) / "proc" / "c3")


if __name__ == "__main__":
    unittest.main()

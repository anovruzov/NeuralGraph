"""The site process: equivalence with the shipped verifier, what leaves a site, and the adapter's place."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from mycelic.collective.edge.egress import VERDICT_KEYS, check_artifact
from mycelic.collective.edge.records import RecordStore
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.verify import SiteVerifier, retrieve
from mycelic.collective.jsonio import canonical_bytes, strict_load
from mycelic.collective.packs.canonical import Canonicaliser
from research.routing_spike import wire
from research.routing_spike.run import site_configs
from research.routing_spike.site_process import SiteProcess, node_text, query_text
from research.routing_spike.world import restore_store

from .helpers import questions, small_world


class SiteProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.world = small_world(Path(cls.tmp.name) / "w", seed=1, planted=True, top_n=3)
        cls.questions = questions(cls.world)
        cls.site = cls.world.site_ids[0]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.pipeline.store.close()
        cls.tmp.cleanup()

    def _copy(self, name: str) -> tuple[Path, Path]:
        base = Path(self.tmp.name) / name
        for sid in self.world.site_ids:
            restore_store(sid, self.world.workdir / "pristine", base / "edge")
        return base / "edge", base / "hq"

    def test_with_the_shipped_retrieval_swapped_in_the_bytes_equal_site_verifier_answer(self) -> None:
        world = self.world
        clock_value = {"ts": "2024-01-01T12:00:00Z"}
        shipped_edge, shipped_hq = self._copy("shipped")
        mine_edge, mine_hq = self._copy("mine")
        configs = site_configs(world, retrieval="shipped", max_records=world.pack.egress.verify_max_records,
                               edge=mine_edge, hq_dir=mine_hq)
        compared = 0
        for sid in world.site_ids:
            site = EdgeSite(world.pack, sid, shipped_edge, runtime=None, clock=lambda: clock_value["ts"],
                            master_data=world.master_data[sid], hq_dir=shipped_hq)
            process = SiteProcess(configs[sid])
            try:
                verifier = SiteVerifier(site, runtime=None, clock=lambda: clock_value["ts"], demo_seed=world.seed)
                for q in self.questions:
                    clock_value["ts"] = f"{q['as_of']}T12:00:00Z"
                    expected = canonical_bytes(verifier.answer(q))
                    self.assertEqual(process.handle("question", canonical_bytes(q)), expected)
                    compared += 1
            finally:
                process.close()
                site.close()
        self.assertEqual(compared, len(world.site_ids) * len(self.questions))

    def test_only_the_closed_verdict_leaves_and_no_record_handle_or_text_is_in_it(self) -> None:
        world = self.world
        edge, hq = self._copy("leaves")
        config = site_configs(world, edge=edge, hq_dir=hq)[self.site]
        process = SiteProcess(config)
        try:
            refs = [r["record_ref"] for r in world.records if r["site"] == self.site]
            narratives = [r["narrative"] for r in world.records if r["site"] == self.site and len(r["narrative"]) > 24]
            confirms = 0
            for q in self.questions:
                data = process.handle("question", canonical_bytes(q))
                body = strict_load(data)
                self.assertEqual(sorted(body), sorted(VERDICT_KEYS))
                self.assertIsNone(check_artifact(world.pack, self.site, "verdict", body))
                text = data.decode()
                self.assertFalse(any(ref in text for ref in refs))
                self.assertFalse(any(n[:24] in text for n in narratives))
                self.assertIsNone(re.search(r"\b\d{2,}\b", re.sub(r"[0-9a-f]{16,64}|\d{4}-W\d{2}", "", text)))
                confirms += body["verdict"] == "confirm"
            self.assertGreater(confirms, 0)
            descriptor = process.handle("describe", b"")
            self.assertEqual(descriptor, canonical_bytes(wire.descriptor(self.site, world.pack.id,
                                                                         world.pack.config_hash,
                                                                         sorted(world.pack.questions))))
        finally:
            process.close()
        diag = (edge / f"site-{self.site}.tesseract.jsonl").read_bytes()
        self.assertIn(b'"refs"', diag)

    def _diag(self, edge: Path) -> list[dict]:
        return [strict_load(line) for line in (edge / f"site-{self.site}.tesseract.jsonl").read_bytes()
                .split(b"\n") if line]

    def _entity_matched(self, edge: Path, q: dict) -> set[str]:
        """Harness side: the in-window own records the shipped retrieval matches to the question's entity."""
        store = RecordStore(edge / f"site-{self.site}.sqlite3", site_id=self.site, pack_id=self.world.pack.id,
                            config_hash=self.world.pack.config_hash)
        try:
            canon = Canonicaliser(self.world.pack, known=self.world.master_data[self.site])
            records, _ = retrieve(store, canon, entity_type=q["params"]["entity_type"],
                                  entity_id=q["params"]["entity_id"], window=q["window"], cap=100_000)
            return {r.record_ref for r in records}
        finally:
            store.close()

    def test_tesseract_reads_at_most_max_records_and_marks_truncation(self) -> None:
        world = self.world
        for cap in (2, 50):
            with self.subTest(cap=cap):
                edge, hq = self._copy(f"cap-{cap}")
                config = site_configs(world, edge=edge, hq_dir=hq, max_records=cap)[self.site]
                process = SiteProcess(config)
                try:
                    for q in self.questions:
                        process.handle("question", canonical_bytes(q))
                    rows = {r["question_id"]: r for r in self._diag(edge)}
                    self.assertTrue(rows)
                    for row in rows.values():
                        self.assertLessEqual(row["read"], cap)
                        self.assertEqual(row["nodes"], process.retrieval.nodes)
                    flags = []
                    for q in self.questions:
                        stored = process.verifier.audit(strict_load(process.handle("question", canonical_bytes(q)))
                                                        ["verdict_id"])
                        if stored is None:
                            continue
                        self.assertLessEqual(stored.judged, cap)
                        row = rows[q["question_id"]]
                        matched = self._entity_matched(edge, q)
                        unread = matched - set(row["refs"])
                        # truncated: some in-window record about the entity was not read; not "the window held
                        # more than the cap" (Run 1's flag)
                        self.assertEqual(stored.truncated, bool(unread))
                        self.assertEqual(row["entity_matched"], len(matched))
                        self.assertEqual(row["entity_matched_unread"], len(unread))
                        flags.append(stored.truncated)
                    if cap == 2:
                        self.assertIn(True, flags)
                finally:
                    process.close()

    def test_the_ranking_sees_only_records_received_on_or_before_as_of(self) -> None:
        world = self.world
        edge, hq = self._copy("asof")
        process = SiteProcess(site_configs(world, edge=edge, hq_dir=hq)[self.site])
        try:
            for q in self.questions:
                process.handle("question", canonical_bytes(q))
        finally:
            process.close()
        own = [r for r in world.records if r["site"] == self.site
               and not (r["origin_site"] is not None and r["origin_site"] != self.site)]
        rows = {r["question_id"]: r for r in self._diag(edge)}
        later = 0
        for q in self.questions:
            row = rows[q["question_id"]]
            visible = sum(1 for r in own if r["received_date"][:10] <= q["as_of"])
            self.assertEqual(row["nodes_as_of"], visible)
            later += row["nodes"] - visible
        self.assertGreater(later, 0)      # the graph does hold records received after these questions' as_of

    def test_records_received_after_as_of_cannot_crowd_the_ranking(self) -> None:
        from datetime import date, datetime, timedelta, timezone

        from mycelic.collective.edge.weeks import iso_week
        world, q = self.world, self.questions[0]

        def answer(name: str, *, inject: bool, unbounded: bool = False) -> tuple[bytes, list[str], int]:
            edge, hq = self._copy(name)
            process = SiteProcess(site_configs(world, edge=edge, hq_dir=hq)[self.site])
            try:
                r = process.retrieval
                if inject:
                    # 200 records received a day after as_of whose text is the query itself: visible to the ranking,
                    # they would take every store's top places
                    day = (date.fromisoformat(q["as_of"]) + timedelta(days=1)).isoformat()
                    at = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
                    text = query_text(world.pack, q)
                    nodes = [r._NeuralNode(node_id=f"future-{i:03d}", layer=r._MESSAGE, content=text,
                                           embedding=r._embed(text, r._dim), session_key=r.session, created_at=at,
                                           updated_at=at, last_activated=at,
                                           metadata={"iso_week": iso_week(day), "received_date": day})
                             for i in range(200)]
                    r._loop.run_until_complete(r._storage.save_nodes_batch(nodes))
                if unbounded:
                    r._tesseract._storage = r._storage      # Run 1's ranking: the whole session
                data = process.handle("question", canonical_bytes(q))
            finally:
                process.close()
            row = self._diag(edge)[-1]
            return data, row["refs"], row["in_window_returned"]

        plain = answer("crowd-plain", inject=False)
        injected = answer("crowd-injected", inject=True)
        self.assertEqual(injected, plain)
        self.assertGreater(plain[2], 0)
        # the check can fail: with the whole session visible, the later records crowd the in-window ones out
        crowded = answer("crowd-unbounded", inject=True, unbounded=True)
        self.assertNotEqual(crowded[1], plain[1])

    def test_the_graph_holds_one_node_per_own_record_with_its_received_date(self) -> None:
        import asyncio
        world = self.world
        edge, hq = self._copy("graph")
        process = SiteProcess(site_configs(world, edge=edge, hq_dir=hq)[self.site])
        try:
            own = [r for r in world.records if r["site"] == self.site
                   and not (r["origin_site"] is not None and r["origin_site"] != self.site)]
            nodes = asyncio.new_event_loop().run_until_complete(
                process.retrieval._storage.get_nodes_by_session(process.retrieval.session))
            self.assertEqual(len(nodes), len(own))
            by_ref = {r["record_ref"]: r for r in own}
            for node in nodes[:50]:
                self.assertEqual(node.created_at.date().isoformat(), by_ref[node.node_id]["received_date"][:10])
                self.assertTrue(node.content.startswith(by_ref[node.node_id]["narrative"]))
                self.assertEqual(len(node.embedding), 256)
        finally:
            process.close()

    def test_query_and_node_text_follow_the_design(self) -> None:
        q = self.questions[0]
        text = query_text(self.world.pack, q)
        self.assertTrue(text.startswith("In the last "))
        self.assertIn(q["params"]["entity_id"], text)
        from mycelic.collective.edge.records import WindowRecord
        rec = WindowRecord("r1", "2024-W10", "r1", None, "en", ["ILL-B"], {"lot": ["L1"], "product": []}, "Cracked.")
        self.assertEqual(node_text(rec), "Cracked.\nentities: lot L1 | codes: ILL-B")


if __name__ == "__main__":
    unittest.main()

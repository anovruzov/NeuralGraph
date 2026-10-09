"""The site process: equivalence with the shipped verifier, what leaves a site, and the adapter's place."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from mycelic.collective.edge.egress import VERDICT_KEYS, check_artifact
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.verify import SiteVerifier
from mycelic.collective.jsonio import canonical_bytes, strict_load
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

    def test_tesseract_reads_at_most_max_records_and_marks_truncation(self) -> None:
        world = self.world
        edge, hq = self._copy("cap")
        config = site_configs(world, edge=edge, hq_dir=hq, max_records=2)[self.site]
        process = SiteProcess(config)
        try:
            for q in self.questions:
                process.handle("question", canonical_bytes(q))
            rows = [strict_load(line) for line in (edge / f"site-{self.site}.tesseract.jsonl").read_bytes()
                    .split(b"\n") if line]
            self.assertTrue(rows)
            for row in rows:
                self.assertLessEqual(row["read"], 2)
                self.assertEqual(row["nodes"], process.retrieval.nodes)
            for q in self.questions:
                stored = process.verifier.audit(strict_load(process.handle("question", canonical_bytes(q)))
                                                ["verdict_id"])
                if stored is not None:
                    self.assertLessEqual(stored.judged, 2)
                    own = next(r["own_in_window"] for r in rows if r["question_id"] == q["question_id"])
                    self.assertEqual(stored.truncated, own > 2)
        finally:
            process.close()

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

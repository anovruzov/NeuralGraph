"""The memory bus: hash chain, horizontal peer links, route hints, privacy, persistence."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from NeuralGraph.coordination.bus import (
    GENESIS_HASH,
    MemoryBus,
    execute_on_bus,
    similarity,
    sketch,
)
from NeuralGraph.coordination.contracts import InterventionTarget
from NeuralGraph.coordination.core import (
    ClaimNormalizer,
    LineageAnalyzer,
    Router,
    RuleBasedSynthesizer,
    TesseractCoordinator,
    canonical_json,
)
from NeuralGraph.coordination.fixture import REQUIRED_SLOTS, build_runtime


def coordinator(runtime):
    return TesseractCoordinator(
        runtime.registry, Router(runtime.registry, runtime.failure), ClaimNormalizer(),
        RuleBasedSynthesizer(REQUIRED_SLOTS), LineageAnalyzer(), runtime.traces,
    )


def similar_query(query, nonce="QX_DIFFERENT_NONCE"):
    return replace(query, query_id=query.query_id + "_b", content=f"Reconstruct the two-part capability identified by {nonce}")


def unrelated_query(query):
    return replace(query, query_id=query.query_id + "_c", content="inventory count for warehouse seven, morning shift")


class SketchTests(unittest.TestCase):
    def test_sketch_holds_hashes_not_tokens(self):
        s = sketch("Reconstruct the capability identified by QX_SECRET")
        self.assertTrue(all(len(h) == 16 and int(h, 16) >= 0 for h in s))
        self.assertNotIn("qx_secret", " ".join(s))
        self.assertEqual(s, sketch("reconstruct THE capability identified by qx_secret"))

    def test_similarity_orders_like_jaccard(self):
        a = sketch("reconstruct the two-part capability identified by alpha")
        b = sketch("reconstruct the two-part capability identified by beta")
        c = sketch("inventory count for warehouse seven")
        self.assertGreater(similarity(a, b), 0.5)
        self.assertEqual(similarity(a, c), 0.0)
        self.assertEqual(similarity(a, ()), 0.0)


class MemoryBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_chain_is_intact_and_tamper_evident(self):
        runtime = build_runtime(81, "bus")
        bus = MemoryBus(runtime.clock.now)
        coord = coordinator(runtime)
        q = runtime.fixture.query
        for phase in ("one", "two", "three"):
            bus.append(q, await coord.execute(q, phase))
        self.assertEqual(bus.verify(), 0)
        self.assertEqual(bus.entries[0].prev_hash, GENESIS_HASH)
        self.assertEqual(bus.entries[1].prev_hash, bus.entries[0].hash)
        # tamper with the middle entry's outcome: the chain breaks there
        tampered = list(bus.entries)
        tampered[1] = replace(tampered[1], success=not tampered[1].success)
        bus._entries = tampered
        self.assertEqual(bus.verify(), 2)

    async def test_similar_queries_link_horizontally_and_unrelated_do_not(self):
        runtime = build_runtime(82, "bus")
        bus = MemoryBus(runtime.clock.now)
        coord = coordinator(runtime)
        q = runtime.fixture.query
        first = bus.append(q, await coord.execute(q, "first"))
        second = bus.append(similar_query(q), await coord.execute(similar_query(q), "second"))
        third = bus.append(unrelated_query(q), await coord.execute(unrelated_query(q), "third"))
        self.assertEqual(first.peer_entry_ids, ())
        self.assertEqual(second.peer_entry_ids, (first.entry_id,))
        self.assertGreater(second.peer_similarity[0], 0.5)
        self.assertEqual(third.peer_entry_ids, ())
        self.assertEqual(bus.peer_graph()[second.entry_id], (first.entry_id,))

    async def test_route_hint_prefers_peers_that_produced_selected_evidence(self):
        runtime = build_runtime(83, "bus")
        bus = MemoryBus(runtime.clock.now)
        coord = coordinator(runtime)
        q = runtime.fixture.query
        seeded = await coord.execute(q, "seed", preferred_node_ids=("node-d", "node-a", "node-b"))
        self.assertTrue(seeded.synthesis.success)
        self.assertEqual(set(seeded.synthesis.selected_node_ids), {"node-a", "node-d"})
        bus.append(q, seeded)
        hint = bus.route_hint(similar_query(q))
        self.assertEqual(set(hint), {"node-a", "node-d"})
        self.assertEqual(bus.route_hint(unrelated_query(q)), ())
        # the orchestrator loop uses the hint: the route starts with the hinted nodes
        execution, entry = await execute_on_bus(coord, bus, similar_query(q), "hinted")
        self.assertEqual(set(execution.route[:2]), {"node-a", "node-d"})
        self.assertEqual(entry.peer_entry_ids, (bus.entries[0].entry_id,))
        # without the bus the same query would have routed in registration order
        plain = await coord.execute(similar_query(q), "plain")
        self.assertEqual(plain.route, ("node-a", "node-b", "node-c"))

    async def test_hint_ignores_failed_peers(self):
        runtime = build_runtime(84, "bus")
        bus = MemoryBus(runtime.clock.now)
        coord = coordinator(runtime)
        q = runtime.fixture.query
        runtime.failure.apply(InterventionTarget.LINEAGE_ROOT, "R2", "test")
        failed = await coord.execute(q, "failed")
        self.assertFalse(failed.synthesis.success)
        bus.append(q, failed)
        self.assertEqual(bus.route_hint(similar_query(q)), ())

    async def test_nothing_private_crosses_onto_the_bus(self):
        runtime = build_runtime(85, "bus")
        bus = MemoryBus(runtime.clock.now)
        coord = coordinator(runtime)
        q = runtime.fixture.query
        bus.append(q, await coord.execute(q, "p"))
        serialized = canonical_json(bus.entries) + "\n".join(bus.dump_lines()) if hasattr(bus, "dump_lines") else canonical_json(bus.entries)
        f = runtime.fixture
        for secret in (f.left_symbol, f.right_symbol, f.expected_answer, q.content):
            self.assertNotIn(secret, serialized)
        # the nonce token from the query is not on the bus either, only hashes
        nonce = q.content.split()[-1]
        self.assertNotIn(nonce, serialized)
        self.assertNotIn(nonce.lower(), serialized)

    async def test_persistence_round_trip_and_corruption_detection(self):
        runtime = build_runtime(86, "bus")
        bus = MemoryBus(runtime.clock.now)
        coord = coordinator(runtime)
        q = runtime.fixture.query
        for phase in ("a", "b"):
            bus.append(q, await coord.execute(q, phase))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bus.jsonl"
            bus.dump(path)
            loaded = MemoryBus.load(path, runtime.clock.now)
            self.assertEqual(loaded.entries, bus.entries)
            self.assertEqual(loaded.verify(), 0)
            self.assertEqual(loaded.head_hash, bus.head_hash)
            text = path.read_text(encoding="utf-8").replace('"success":true', '"success":false', 1)
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                MemoryBus.load(path, runtime.clock.now)

    async def test_same_seed_replays_identical_hashes(self):
        hashes = []
        for _ in range(2):
            runtime = build_runtime(87, "bus")
            bus = MemoryBus(runtime.clock.now)
            coord = coordinator(runtime)
            q = runtime.fixture.query
            bus.append(q, await coord.execute(q, "x"))
            bus.append(similar_query(q), await coord.execute(similar_query(q), "y"))
            hashes.append(tuple(e.hash for e in bus.entries))
        self.assertEqual(hashes[0], hashes[1])

    def test_threshold_is_validated(self):
        with self.assertRaises(ValueError):
            MemoryBus(lambda: "2025-01-01T00:00:00Z", similarity_threshold=0.0)


if __name__ == "__main__":
    unittest.main()

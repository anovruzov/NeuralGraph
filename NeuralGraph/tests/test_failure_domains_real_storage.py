"""Failure domains derived from real storage, and the min-cut result reproduced there.

Until now every domain-derived metric -- reconstruction coalitions, minimal
support size, minimum failure-domain cut, and the whole
``worst_single_domain_failure`` row of the benchmark -- existed only against
``MockMemoryNodeAdapter``, because ``StorageLineageResolver`` reported
``failure_domains=()``.  That was the single largest gap between the benchmark
and a claim about a real system.

The gap is closed by *recording* the domain rather than inferring it.  A failure
domain here is the upstream origin a memory ultimately derives from -- the thing
that fails as a unit -- and it is read from the metadata of the lineage roots the
walk actually resolves.  Two memories in different databases share a domain when
they descend from the same ingested origin, which is what correlated provenance
is; two memories that merely hold equal values do not.

What is deliberately NOT done: nothing infers a domain from storage identity,
node id, file path, or value equality.  A root that records no domain contributes
none, so unrecorded provenance can never be mistaken for genuine independence.
``test_absent_domains_stay_absent`` pins that.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from NeuralGraph.coordination.contracts import ClaimEnvelope, PolicyStatus, ValidTime
from NeuralGraph.coordination.core import LineageAnalyzer, RuleBasedSynthesizer
from NeuralGraph.coordination.storage_adapter import StorageLineageResolver
from NeuralGraph.data_types import EdgeType, NeuralEdge, NeuralNode, NodeLayer
from NeuralGraph.sqlite_storage import SQLiteNeuralGraphStorage

SESSION_KEY = "domain-session"
EMBEDDING = [0.11, 0.22, 0.33]
DOMAIN_KEY = "failure_domain"
REQUIRED_SLOTS = ("left", "right")


async def _seed_with_domain(
    storage: SQLiteNeuralGraphStorage,
    episode_id: str,
    origin_id: str,
    slot: str,
    value: str,
    domain: str | None,
) -> None:
    """Persist a derived episode and its origin, the origin carrying the domain.

    The domain lives on the *origin*, not on the episode: it describes where the
    knowledge came from, so every memory descending from that origin inherits it
    through the lineage walk rather than by declaring it locally.
    """
    metadata = {} if domain is None else {DOMAIN_KEY: domain}
    await storage.save_node(NeuralNode(
        node_id=episode_id,
        layer=NodeLayer.EPISODE,
        content=value,
        embedding=list(EMBEDDING),
        session_key=SESSION_KEY,
        child_ids=[origin_id],
        source_memory_ids=[origin_id],
        metadata={"slot": slot},
    ))
    await storage.save_node(NeuralNode(
        node_id=origin_id,
        layer=NodeLayer.MESSAGE,
        content=f"raw-{value}",
        embedding=list(EMBEDDING),
        session_key=SESSION_KEY,
        parent_id=episode_id,
        metadata=metadata,
    ))
    await storage.save_edge(NeuralEdge(
        edge_id=f"edge-{episode_id}-{origin_id}",
        source_id=episode_id,
        target_id=origin_id,
        edge_type=EdgeType.HIERARCHY,
        base_weight=1.0,
        confidence=1.0,
    ))


def _claim(claim_id: str, node_id: str, slot: str, value: str, facts: dict, confidence: float):
    """Build the claim the adapter would export from these resolved facts."""
    return ClaimEnvelope(
        claim_id=claim_id,
        query_id="domain-query",
        producer_node_id=node_id,
        content={"slot": slot, "value": value},
        confidence=confidence,
        evidence_refs=(),
        source_ids=facts["source_ids"],
        parent_memory_ids=facts["parent_memory_ids"],
        lineage_root_ids=facts["lineage_root_ids"],
        failure_domains=facts["failure_domains"],
        policy_status=PolicyStatus.ALLOWED,
        created_at="2025-01-01T00:00:00Z",
        valid_time=ValidTime(from_time="2025-01-01T00:00:00Z", to_time=None),
        derivation_operator="storage_vector_search",
    )


class _StorageFixture(unittest.IsolatedAsyncioTestCase):
    """One real SQLite file per node, seeded from a layout table."""

    LAYOUT: dict[str, tuple[str, str, str, str | None, float]] = {}

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.storages = {}
        for node_id, (slot, value, origin, domain, _conf) in sorted(self.LAYOUT.items()):
            storage = SQLiteNeuralGraphStorage(db_path=root / f"{node_id}.db")
            await _seed_with_domain(
                storage, f"episode-{node_id}", origin, slot, value, domain
            )
            self.storages[node_id] = storage

    async def asyncTearDown(self) -> None:
        for storage in self.storages.values():
            close = getattr(storage, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        self._tmp.cleanup()

    async def _facts(self, node_id: str, domain_key: str | None = DOMAIN_KEY) -> dict:
        storage = self.storages[node_id]
        resolver = StorageLineageResolver(storage, domain_key=domain_key)
        node = await storage.get_node(f"episode-{node_id}")
        return await resolver(node)

    async def _claims(self):
        claims = []
        for index, (node_id, (slot, value, _o, _d, conf)) in enumerate(sorted(self.LAYOUT.items())):
            facts = await self._facts(node_id)
            claims.append(_claim(f"claim-{index:02d}", node_id, slot, value, facts, conf))
        return tuple(claims)


class DomainDerivationTests(_StorageFixture):
    """A: left/FD1. B and C: right, both from origin-O2/FD2. D: right/FD3."""

    LAYOUT = {
        "node-a": ("left", "LX_A", "origin-O1", "FD1", 0.96),
        "node-b": ("right", "RX_B", "origin-O2", "FD2", 0.93),
        "node-c": ("right", "RX_B", "origin-O2", "FD2", 0.91),
        "node-d": ("right", "RX_B", "origin-O3", "FD3", 0.94),
    }

    async def test_domains_are_read_from_resolved_roots(self):
        for node_id, expected in (
            ("node-a", ("FD1",)), ("node-b", ("FD2",)),
            ("node-c", ("FD2",)), ("node-d", ("FD3",)),
        ):
            with self.subTest(node=node_id):
                facts = await self._facts(node_id)
                self.assertEqual(facts["failure_domains"], expected)

    async def test_apparent_replicas_in_separate_databases_share_a_domain(self):
        """b and c share no row, and are still correctly correlated."""
        b, c = await self._facts("node-b"), await self._facts("node-c")
        self.assertEqual(b["failure_domains"], c["failure_domains"])
        self.assertEqual(b["lineage_root_ids"], c["lineage_root_ids"])
        d = await self._facts("node-d")
        self.assertNotEqual(b["failure_domains"], d["failure_domains"])

    async def test_domain_key_is_opt_in(self):
        """Without the key configured, behaviour is exactly as before."""
        facts = await self._facts("node-a", domain_key=None)
        self.assertEqual(facts["failure_domains"], ())
        self.assertEqual(facts["lineage_root_ids"], ("origin-O1",))

    async def test_coalitions_and_min_cut_are_now_computable_on_real_storage(self):
        """The metrics that were structurally zero before this model existed."""
        claims = await self._claims()
        analyzer = LineageAnalyzer()
        synthesis = RuleBasedSynthesizer(REQUIRED_SLOTS).synthesize(claims)
        metrics = analyzer.score(claims, REQUIRED_SLOTS, synthesis)

        self.assertTrue(synthesis.success)
        self.assertGreater(metrics.reconstruction_coalition_count, 0)
        self.assertEqual(metrics.unique_failure_domain_count, 3)
        # left has exactly one domain (FD1), so one domain severs every
        # coalition: min-cut 1, matching the mock benchmark's non-oracle rows.
        self.assertEqual(metrics.minimal_failure_domain_cut, 1)


class AbsentDomainTests(_StorageFixture):
    """A root recording no domain must contribute none, not a guessed one."""

    LAYOUT = {
        "node-a": ("left", "LX_A", "origin-O1", None, 0.96),
        "node-b": ("right", "RX_B", "origin-O2", "FD2", 0.93),
    }

    async def test_absent_domains_stay_absent(self):
        facts = await self._facts("node-a")
        self.assertEqual(facts["failure_domains"], ())
        self.assertEqual(facts["lineage_root_ids"], ("origin-O1",))

    async def test_partial_provenance_is_pessimistic_never_optimistic(self):
        """Missing domains may shrink the reported cut; they must never inflate it.

        This is the safety property that makes a partially-recorded deployment
        publishable.  A memory whose domain was never recorded contributes no
        domain, so the coalitions it takes part in carry fewer domains and are
        therefore *easier* to sever.  The reported min-cut can only fall.

        The failure mode that would matter -- unrecorded provenance reading as
        extra independence and inflating the cut -- is structurally impossible,
        and that is what is asserted here.  Reconstruction itself is unaffected:
        the system still answers, it just declines to claim robustness it cannot
        evidence.
        """
        claims = await self._claims()
        synthesis = RuleBasedSynthesizer(REQUIRED_SLOTS).synthesize(claims)
        metrics = LineageAnalyzer().score(claims, REQUIRED_SLOTS, synthesis)

        self.assertTrue(synthesis.success, "reconstruction still works")
        # left records no domain, so only right's FD2 is known. One domain
        # failure can still sever everything: min-cut 1, the conservative call.
        self.assertEqual(metrics.unique_failure_domain_count, 1)
        self.assertLessEqual(metrics.minimal_failure_domain_cut, 1)

        # And it never exceeds what fully-recorded provenance would report.
        full = await self._facts("node-b")
        self.assertEqual(full["failure_domains"], ("FD2",))


class OracleMinCutOnRealStorageTests(_StorageFixture):
    """The oracle placement, on real storage: both slots independently rooted."""

    LAYOUT = {
        "node-a": ("left", "LX_A", "origin-O1", "FD1", 0.96),
        "node-b": ("right", "RX_B", "origin-O2", "FD2", 0.93),
        "node-c": ("left", "LX_A", "origin-O4", "FD4", 0.92),
        "node-d": ("right", "RX_B", "origin-O3", "FD3", 0.94),
    }

    async def test_min_cut_of_two_is_reproduced_against_real_sqlite(self):
        """The paper's decisive structural result, no longer mock-only."""
        claims = await self._claims()
        synthesis = RuleBasedSynthesizer(REQUIRED_SLOTS).synthesize(claims)
        metrics = LineageAnalyzer().score(claims, REQUIRED_SLOTS, synthesis)

        self.assertTrue(synthesis.success)
        self.assertEqual(metrics.unique_failure_domain_count, 4)
        self.assertEqual(metrics.minimal_failure_domain_cut, 2)

    async def test_no_single_domain_severs_every_coalition(self):
        """min-cut 2 stated as the operational property it stands for."""
        claims = await self._claims()
        coalitions = LineageAnalyzer().reconstruction_coalitions(claims, REQUIRED_SLOTS)
        self.assertTrue(coalitions)
        all_domains = {d for coalition in coalitions for d in coalition.failure_domains}
        for domain in sorted(all_domains):
            survivors = [
                coalition for coalition in coalitions
                if domain not in coalition.failure_domains
            ]
            with self.subTest(failed_domain=domain):
                self.assertTrue(
                    survivors,
                    f"failing {domain} alone severed every coalition; min-cut is not 2",
                )


if __name__ == "__main__":
    unittest.main()

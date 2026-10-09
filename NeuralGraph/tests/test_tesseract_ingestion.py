"""Regression tests for Tesseract retrieval and the LoCoMo benchmark ingestion path.

Everything runs offline: embeddings come from the deterministic hash embedder
(``NeuralGraph.chat_memory.llm.fake_embedding``) and the LoCoMo data from
``research/datasets/locomo10.json``. The harness under test is
``research/benchmarks/retrieval_eval.ingest``, which builds nodes through the
same shared helper as ``runner.py`` (``NeuralGraph/research/retrieval/benchmark_ingest.py``).

Covered:
1. SQLite storage implements ``get_neighbors`` and ranks like the in-memory store.
2. Embedding failures stop ingestion with a count; retrieval tolerates an empty query vector.
3. Ingested nodes keep dia_id, session index, image caption and the message date.
4. A query that is the exact text of a stored message ranks that message first.
5. Category names follow the dataset's evidence counts; result metadata names the judge that ran.
6. Rankings do not depend on the Python hash seed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from NeuralGraph.chat_memory.llm import fake_embedding
from NeuralGraph.research.retrieval.benchmark_ingest import (
    EmbeddingFailureError,
    build_message_node,
    check_embeddings,
    flatten_locomo,
)
from NeuralGraph.research.retrieval.data_types import EdgeType, NeuralNode, NodeLayer
from NeuralGraph.research.retrieval.sqlite_storage import SQLiteNeuralGraphStorage
from NeuralGraph.research.retrieval.storage import NeuralGraphStorage
from NeuralGraph.research.retrieval.tesseract import Tesseract, TemporalStore, _speaker_charge

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARKS = REPO_ROOT / "research" / "benchmarks"
LOCOMO_PATH = REPO_ROOT / "research" / "datasets" / "locomo10.json"


def load_locomo() -> list[dict]:
    with open(LOCOMO_PATH) as f:
        return json.load(f)


def first_sessions(conv: dict, n_sessions: int) -> dict:
    """The first ``n_sessions`` sessions of a LoCoMo conversation, same shape."""
    c = conv["conversation"]
    keep = {k: v for k, v in c.items() if not re.fullmatch(r"session_\d+(_date_time)?", k)}
    for i in range(1, n_sessions + 1):
        keep[f"session_{i}"] = c[f"session_{i}"]
        keep[f"session_{i}_date_time"] = c[f"session_{i}_date_time"]
    return {"conversation": keep, "qa": conv.get("qa", [])}


def retrieval_eval_module():
    """research/benchmarks/retrieval_eval.py (imports runner.py as a sibling module)."""
    if str(BENCHMARKS) not in sys.path:
        sys.path.insert(0, str(BENCHMARKS))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import retrieval_eval  # noqa: PLC0415

    return retrieval_eval


@contextlib.contextmanager
def hash_embeddings(module, fail_indices: set[int] = frozenset()):
    """Replace the harness's cached embedding call with the hash embedder.

    Messages whose position is in ``fail_indices`` get ``[]``, which is what a
    failed embedding call yields.
    """
    async def fake_cached_embeddings(http, key, texts):
        return [[] if i in fail_indices else fake_embedding(t) for i, t in enumerate(texts)]

    original = module.cached_embeddings
    module.cached_embeddings = fake_cached_embeddings
    try:
        yield
    finally:
        module.cached_embeddings = original


def tiny_conversation() -> dict:
    """LoCoMo-shaped conversation: two sessions a year apart, one image turn."""
    return {
        "conversation": {
            "speaker_a": "Caroline",
            "speaker_b": "Melanie",
            "session_1_date_time": "1:56 pm on 8 May, 2022",
            "session_1": [
                {"speaker": "Caroline", "dia_id": "D1:1", "text": "Hey Mel! I went to a support group yesterday."},
                {"speaker": "Melanie", "dia_id": "D1:2", "text": "That's great, Caroline! Look what I made.",
                 "blip_caption": "a photo of a painting of a sunrise over a lake"},
                {"speaker": "Caroline", "dia_id": "D1:3", "text": "Lovely colours. When did you paint it?"},
            ],
            "session_2_date_time": "7:55 pm on 9 June, 2023",
            "session_2": [
                {"speaker": "Melanie", "dia_id": "D2:1", "text": "I painted that lake sunrise last year."},
                {"speaker": "Caroline", "dia_id": "D2:2", "text": "I am thinking about adoption agencies."},
            ],
        },
        "qa": [],
    }


# =============================================================================
# 1. SQLite get_neighbors and ranking parity
# =============================================================================


class SQLiteParityTests(unittest.IsolatedAsyncioTestCase):
    """The same corpus in SQLite and in memory gives the same Tesseract top-10."""

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.sqlite = SQLiteNeuralGraphStorage(db_path=Path(self._tmpdir.name) / "parity.db")
        module = retrieval_eval_module()
        self.conv = first_sessions(load_locomo()[0], 3)
        with hash_embeddings(module):
            self.memory, _linker, self.nodes, _speakers = await module.ingest(None, 0, self.conv)
        for node in await self.memory.get_nodes_by_session("conv_0"):
            await self.sqlite.save_node(node)
        self.edges = await self.memory.get_all_edges("conv_0")
        for edge in self.edges:
            await self.sqlite.save_edge(edge)

    async def asyncTearDown(self) -> None:
        self.sqlite.close()
        self._tmpdir.cleanup()

    def test_get_neighbors_is_part_of_the_storage_interface(self) -> None:
        self.assertIn("get_neighbors", NeuralGraphStorage.__abstractmethods__)

    async def test_get_neighbors_matches_in_memory(self) -> None:
        self.assertTrue(any(e.edge_type == EdgeType.TEMPORAL for e in self.edges))
        for node in self.nodes[:12]:
            for direction in ("outgoing", "incoming", "both"):
                for edge_types in (None, [EdgeType.TEMPORAL], [EdgeType.ENTITY, EdgeType.TEMPORAL]):
                    with self.subTest(node=node.node_id, direction=direction, edge_types=edge_types):
                        mem = await self.memory.get_neighbors(node.node_id, edge_types=edge_types, direction=direction)
                        sql = await self.sqlite.get_neighbors(node.node_id, edge_types=edge_types, direction=direction)
                        self.assertEqual(
                            sorted((n.node_id, e.edge_id) for n, e in sql),
                            sorted((n.node_id, e.edge_id) for n, e in mem),
                        )
                        self.assertTrue(all(n.content for n, _ in sql))

    async def test_tesseract_top10_identical_on_both_stores(self) -> None:
        questions = [qa["question"] for qa in self.conv["qa"] if qa.get("category") != 5][:20]
        queries = questions + [n.content for n in self.nodes[::6]]
        self.assertGreaterEqual(len(queries), 25)
        for q in queries:
            qv = fake_embedding(q)
            mem = await Tesseract(self.memory).retrieve(q, qv, "conv_0", limit=10)
            sql = await Tesseract(self.sqlite).retrieve(q, qv, "conv_0", limit=10)
            with self.subTest(query=q):
                self.assertEqual([n.node_id for n, _ in sql], [n.node_id for n, _ in mem])
                self.assertEqual(len(sql), 10)


# =============================================================================
# 2. Embedding failures are counted, retrieval survives an empty query vector
# =============================================================================


class EmbeddingFailureTests(unittest.IsolatedAsyncioTestCase):
    def test_check_embeddings_raises_with_the_count(self) -> None:
        with self.assertRaises(EmbeddingFailureError) as ctx:
            check_embeddings([[1.0], [], [0.5], None, []], where="conv_7")
        self.assertEqual(ctx.exception.failed, 3)
        self.assertEqual(ctx.exception.total, 5)
        self.assertIn("3 of 5 messages could not be embedded (conv_7)", str(ctx.exception))

    def test_check_embeddings_allow_missing_returns_the_count(self) -> None:
        self.assertEqual(check_embeddings([[1.0], [], [0.5]], allow_missing=True), 1)
        self.assertEqual(check_embeddings([[1.0], [0.5]]), 0)

    async def test_harness_ingest_stops_on_failed_embeddings(self) -> None:
        module = retrieval_eval_module()
        conv = tiny_conversation()
        with hash_embeddings(module, fail_indices={1, 3}):
            with self.assertRaises(EmbeddingFailureError) as ctx:
                await module.ingest(None, 0, conv, allow_missing=False)
        self.assertIn("2 of 5 messages could not be embedded", str(ctx.exception))

    async def test_harness_ingest_allow_missing_skips_and_keeps_ids(self) -> None:
        module = retrieval_eval_module()
        with hash_embeddings(module, fail_indices={1}):
            storage, _linker, nodes, _speakers = await module.ingest(None, 0, tiny_conversation(), allow_missing=True)
        self.assertEqual([n.node_id for n in nodes], ["msg_0_0", "msg_0_2", "msg_0_3", "msg_0_4"])
        self.assertEqual(len(await storage.get_nodes_by_session("conv_0")), 4)

    async def test_retrieve_with_empty_query_embedding_skips_semantic_term(self) -> None:
        module = retrieval_eval_module()
        with hash_embeddings(module):
            storage, _linker, nodes, _speakers = await module.ingest(None, 0, first_sessions(load_locomo()[0], 2))
        tesseract = Tesseract(storage)
        target = nodes[5]
        with self.assertLogs("NeuralGraph.research.retrieval.tesseract", level="WARNING"):
            results = await tesseract.retrieve(target.content, [], "conv_0", limit=10)
        self.assertEqual(len(results), 10)
        self.assertIn(target.node_id, [n.node_id for n, _ in results])
        # Same ranking as a query vector that matches nothing: the semantic term is simply absent.
        orthogonal = [0.0] * len(target.embedding)
        same = await tesseract.retrieve(target.content, orthogonal, "conv_0", limit=10)
        self.assertEqual([n.node_id for n, _ in results], [n.node_id for n, _ in same])


# =============================================================================
# 3. Ingested nodes keep dia_id, session index, caption and the message date
# =============================================================================


class IngestionFieldTests(unittest.IsolatedAsyncioTestCase):
    def test_flatten_keeps_fields_and_marks_captions(self) -> None:
        messages = flatten_locomo(tiny_conversation())
        self.assertEqual([m["dia_id"] for m in messages], ["D1:1", "D1:2", "D1:3", "D2:1", "D2:2"])
        self.assertEqual([m["session_index"] for m in messages], [1, 1, 1, 2, 2])
        image = messages[1]
        self.assertEqual(image["raw_text"], "That's great, Caroline! Look what I made.")
        self.assertEqual(image["image_caption"], "a photo of a painting of a sunrise over a lake")
        self.assertEqual(
            image["text"],
            "That's great, Caroline! Look what I made. [image: a photo of a painting of a sunrise over a lake]",
        )
        self.assertIsNone(messages[0]["image_caption"])
        self.assertEqual(messages[0]["text"], messages[0]["raw_text"])

    async def test_harness_nodes_carry_dia_id_session_caption_and_year(self) -> None:
        module = retrieval_eval_module()
        with hash_embeddings(module):
            _storage, _linker, nodes, _speakers = await module.ingest(None, 0, tiny_conversation())
        by_dia = {n.metadata["dia_id"]: n for n in nodes}
        self.assertEqual(sorted(by_dia), ["D1:1", "D1:2", "D1:3", "D2:1", "D2:2"])
        for dia, session, year in (("D1:1", 1, 2022), ("D1:3", 1, 2022), ("D2:1", 2, 2023), ("D2:2", 2, 2023)):
            with self.subTest(dia_id=dia):
                node = by_dia[dia]
                self.assertEqual(node.metadata["session_index"], session)
                self.assertEqual(node.created_at.year, year)
                self.assertIsNone(node.metadata["image_caption"])
        image = by_dia["D1:2"]
        self.assertEqual(image.created_at, datetime(2022, 5, 8, 13, 56))
        self.assertEqual(image.metadata["image_caption"], "a photo of a painting of a sunrise over a lake")
        self.assertIn("[image: a photo of a painting of a sunrise over a lake]", image.content)
        self.assertEqual(image.embedding, fake_embedding(image.content))

    def test_unparseable_session_date_keeps_default_created_at(self) -> None:
        msg = {"speaker": "A", "text": "hello there", "datetime": "sometime", "dia_id": "D1:1",
               "session_index": 1, "image_caption": None}
        node = build_message_node("n1", "s", msg, fake_embedding("hello there"))
        self.assertIsNotNone(node.created_at)
        self.assertEqual(node.metadata["datetime"], "sometime")

    def test_temporal_store_resolves_relative_years_against_the_message_date(self) -> None:
        """'last year' in a 2023 message is 2022, whatever clock stamped created_at."""
        store = TemporalStore(storage=None)
        content = "I painted that lake sunrise last year, it's special to me."
        query = "did melanie paint a sunrise in 2022?"
        qv = np.array(fake_embedding(query), dtype=np.float32)

        def charge(created_at: datetime, message_date: str | None) -> float:
            metadata = {"speaker": "Melanie"}
            if message_date:
                metadata["datetime"] = message_date
            node = NeuralNode(node_id="x", layer=NodeLayer.MESSAGE, content=content, embedding=fake_embedding(content),
                              session_key="s", created_at=created_at, metadata=metadata)
            keywords = store._extract_temporal_keywords(query)
            topics = store._extract_topic_keywords(query) - {"melanie"}
            return store._compute_temporal_charge(node, qv, query, keywords, topics, {"melanie"}, None, 14.0)

        dated = charge(datetime(2023, 5, 8, 13, 56), None)  # created_at = message date (harness now)
        clock = charge(datetime(2031, 1, 1), "1:56 pm on 8 May, 2023")  # ingestion clock + metadata date
        stale = charge(datetime(2031, 1, 1), None)  # ingestion clock only: 'last year' = 2030
        self.assertEqual(dated, clock)
        self.assertGreater(dated, stale)


# =============================================================================
# 4. Exact-text self-retrieval is not outranked by speaker matches
# =============================================================================


class ExactTextSelfRetrievalTests(unittest.IsolatedAsyncioTestCase):
    """A query equal to a stored message's text ranks that message first.

    LoCoMo conversation 1 (index 1) with the hash embedder, 200 messages spread
    evenly over the conversation. Before the speaker term became additive with a
    near-exact text match counted as a speaker match, a capitalised speaker name in
    the query lifted every message of that speaker above the exact match (433/600
    top-1 over all ten conversations, versus 599/600 for plain cosine).
    """

    def test_near_exact_text_gets_the_speaker_credit(self) -> None:
        node = NeuralNode(node_id="n", layer=NodeLayer.MESSAGE, content="Thanks, Dave!",
                          metadata={"speaker": "Calvin"})
        self.assertEqual(_speaker_charge(node, {"Dave"}, cosine=0.2), 0.0)
        self.assertEqual(_speaker_charge(node, {"Calvin"}, cosine=0.2), 1.0)
        self.assertEqual(_speaker_charge(node, {"Dave"}, cosine=0.99), 1.0)
        self.assertEqual(_speaker_charge(node, set(), cosine=1.0), 1.0)

    async def test_exact_text_top1_at_least_99_percent(self) -> None:
        module = retrieval_eval_module()
        with hash_embeddings(module):
            storage, _linker, nodes, _speakers = await module.ingest(None, 1, load_locomo()[1])
        self.assertGreaterEqual(len(nodes), 200)
        picks = sorted({round(i * (len(nodes) - 1) / 199) for i in range(200)})
        self.assertEqual(len(picks), 200)
        tesseract = Tesseract(storage)
        hits, misses = 0, []
        for idx in picks:
            target = nodes[idx]
            results = await tesseract.retrieve(target.content, fake_embedding(target.content), "conv_1", limit=10)
            if results and results[0][0].node_id == target.node_id:
                hits += 1
            else:
                misses.append((target.node_id, target.content[:60]))
        self.assertGreaterEqual(hits / len(picks), 0.99, f"{hits}/{len(picks)} top-1; misses: {misses}")


# =============================================================================
# 5. Category names and judge metadata in runner.py
# =============================================================================


class RunnerLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        retrieval_eval_module()
        import runner  # noqa: PLC0415

        self.runner = runner

    def test_category_names_follow_evidence_counts(self) -> None:
        evidence: dict[int, list[int]] = {}
        for conv in load_locomo():
            for qa in conv["qa"]:
                refs = [p for e in qa.get("evidence", []) for p in re.split(r"[;,\s]+", e) if p]
                evidence.setdefault(qa.get("category"), []).append(len(refs))
        mean = {cat: sum(v) / len(v) for cat, v in evidence.items()}
        multi = max((1, 4), key=lambda c: mean[c])
        single = min((1, 4), key=lambda c: mean[c])
        self.assertEqual(self.runner.CATEGORIES[multi], "multi_hop")
        self.assertEqual(self.runner.CATEGORIES[single], "single_hop")
        self.assertEqual(self.runner.CATEGORIES[2], "temporal")
        self.assertEqual(self.runner.CATEGORIES[3], "open_domain")

    def test_result_metadata_names_the_judge_that_ran(self) -> None:
        from NeuralGraph import llm_backend  # noqa: PLC0415

        r = self.runner
        saved = (r.OUTPUT_PATH, r.USE_OPENAI_JUDGE, r.JUDGE_MODEL, llm_backend.LLM_MODEL)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                r.OUTPUT_PATH = Path(tmp) / "run.json"
                stats = {cat: {"correct": 0, "total": 0} for cat in r.CATEGORIES.values()}
                for use_key, judge, local, expected in ((True, "a-4b", "a-0p5b", "a-4b"),
                                                        (False, "a-4b", "a-1p5b", "a-1p5b"),
                                                        (False, "a-4b", "", "unknown")):
                    r.USE_OPENAI_JUDGE, r.JUDGE_MODEL, llm_backend.LLM_MODEL = use_key, judge, local
                    r.save_results([], stats)
                    with self.subTest(use_key=use_key, local=local):
                        meta = json.loads(r.OUTPUT_PATH.read_text())["metadata"]
                        self.assertEqual(meta["judge"], expected)
                        self.assertEqual(meta["missing_embeddings"], {"messages": 0, "questions": 0})
            finally:
                r.OUTPUT_PATH, r.USE_OPENAI_JUDGE, r.JUDGE_MODEL, llm_backend.LLM_MODEL = saved


# =============================================================================
# 6. Rankings do not depend on the hash seed
# =============================================================================

_RANKING_SCRIPT = r"""
import asyncio, json, re, sys
repo = sys.argv[1]
sys.path[:0] = [repo, repo + "/research/benchmarks"]
import retrieval_eval
from NeuralGraph.chat_memory.llm import fake_embedding
from NeuralGraph.research.retrieval.tesseract import Tesseract

async def fake(http, key, texts):
    return [fake_embedding(t) for t in texts]

retrieval_eval.cached_embeddings = fake
conv = json.load(open(repo + "/research/datasets/locomo10.json"))[0]
async def main():
    storage, _l, nodes, _s = await retrieval_eval.ingest(None, 0, conv)
    t = Tesseract(storage)
    qs = [qa["question"] for qa in conv["qa"] if qa.get("category") != 5][:40]
    out = []
    for q in qs:
        res = await t.retrieve(q, fake_embedding(q), "conv_0", limit=50)
        out.append([[n.node_id, c] for n, c in res[:10]])
    print(json.dumps(out))
asyncio.run(main())
"""


class HashSeedDeterminismTests(unittest.TestCase):
    def _rankings(self, seed: str) -> list:
        env = dict(os.environ, PYTHONHASHSEED=seed, EMB_CACHE_DIR=tempfile.gettempdir())
        proc = subprocess.run(
            [sys.executable, "-c", _RANKING_SCRIPT, str(REPO_ROOT)],
            capture_output=True, text=True, env=env, timeout=600,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_top10_identical_across_hash_seeds(self) -> None:
        a, b = self._rankings("0"), self._rankings("1")
        self.assertEqual(len(a), 40)
        differing = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        self.assertEqual(differing, [], f"{len(differing)} of 40 queries ranked differently")
        # Equal charges are ordered by node id.
        for ranking in a:
            for (id1, c1), (id2, c2) in zip(ranking, ranking[1:]):
                if c1 == c2:
                    self.assertLess(id1, id2)


if __name__ == "__main__":
    unittest.main()

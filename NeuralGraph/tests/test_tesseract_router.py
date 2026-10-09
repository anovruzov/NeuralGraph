"""Tesseract's query-type router: ``detect_query_type`` and the store fusion it drives.

The router scores a question on five types. Four have a store (temporal, entity,
multi_hop -> reasoning, adversarial); OPEN marks questions for external retrieval and
has no store. ``Tesseract._fuse_results`` turns the scores into one weight per store.

These tests pin what the router does, including the parts the 2026-10-09 handoff
called broken and this file leaves as they are: 895 of the 1,536 LoCoMo questions
that cite evidence score highest on OPEN (a speaker's name is not a session marker),
and a cross-site question fires no marker and gets near-uniform store weights.

The fix they cover: a route with no store (OPEN first, or no marker) used to fuse
each store's top 40 / 50 / 40 / 30 only, so gold evidence outside all four lists
could never be retrieved. Such a route now falls back to every store: each store
passes every node it charges, and fusion sums all four charges.
"""

from __future__ import annotations

import unittest

from NeuralGraph.chat_memory.llm import fake_embedding
from NeuralGraph.research.retrieval.data_types import NeuralNode, NodeLayer
from NeuralGraph.research.retrieval.tesseract import (
    STORE_DEPTHS,
    STORE_TYPES,
    QueryType,
    Tesseract,
    _build_novelty_context,
    _expand_temporal_query,
    detect_query_type,
    routed_store_type,
)
from NeuralGraph.tests.test_tesseract_ingestion import hash_embeddings, load_locomo, retrieval_eval_module

STORE_OF = {
    QueryType.TEMPORAL: "temporal",
    QueryType.ENTITY: "entity",
    QueryType.MULTI_HOP: "reasoning",
    QueryType.ADVERSARIAL: "adversarial",
}


def top_type(scores: dict[str, float]) -> str:
    """Highest-scoring type; the first in declaration order wins a tie."""
    return max(scores, key=scores.get)


def store_weights(scores: dict[str, float]) -> dict[str, float]:
    """The weight _fuse_results gives each store: fuse one node per store, charge 1.0 each."""
    tesseract = Tesseract(storage=None)

    def one(name: str) -> list:
        return [(NeuralNode(node_id=name, layer=NodeLayer.MESSAGE, content=name), 1.0)]

    fused = tesseract._fuse_results(
        temporal_results=one("temporal"),
        entity_results=one("entity"),
        reasoning_results=one("reasoning"),
        adversarial_results=one("adversarial"),
        type_weights=scores,
    )
    return {node.node_id: charge for node, charge in fused}


class DetectQueryTypeTests(unittest.TestCase):
    def test_store_markers(self) -> None:
        cases = {
            "When did Caroline go to the LGBTQ support group?": QueryType.TEMPORAL,
            "What is Caroline's identity?": QueryType.ENTITY,
            "How many children does Melanie have?": QueryType.MULTI_HOP,
            "Did Caroline never paint?": QueryType.ADVERSARIAL,
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                scores = detect_query_type(question)
                self.assertEqual(top_type(scores), expected)
                self.assertEqual(scores[QueryType.OPEN], 0.0)

    def test_scores_are_capped_and_every_type_is_present(self) -> None:
        scores = detect_query_type("When did Caroline first go and when did she not go after all, and then before?")
        self.assertEqual(set(scores), {QueryType.TEMPORAL, QueryType.ENTITY, QueryType.MULTI_HOP,
                                       QueryType.ADVERSARIAL, QueryType.OPEN})
        self.assertTrue(all(0.0 <= v <= 1.0 for v in scores.values()))
        self.assertEqual(scores[QueryType.TEMPORAL], 1.0)

    def test_world_knowledge_question_goes_to_open(self) -> None:
        scores = detect_query_type("What is photosynthesis?")
        self.assertEqual(top_type(scores), QueryType.OPEN)
        self.assertEqual(scores[QueryType.OPEN], 1.0)

    def test_named_speaker_is_not_a_session_marker(self) -> None:
        """No store marker fires, so OPEN gets its 0.3 base score; a pronoun suppresses it."""
        named = detect_query_type("What did Caroline research?")
        self.assertEqual(named, {QueryType.TEMPORAL: 0.0, QueryType.ENTITY: 0.0, QueryType.MULTI_HOP: 0.0,
                                 QueryType.ADVERSARIAL: 0.0, QueryType.OPEN: 0.3})
        pronoun = detect_query_type("What did she research?")
        self.assertEqual(set(pronoun.values()), {0.0})

    def test_cross_site_question_fires_no_store_marker(self) -> None:
        scores = detect_query_type("Which complaints are rising across sites?")
        self.assertEqual(top_type(scores), QueryType.OPEN)
        self.assertEqual([scores[t] for t in STORE_OF], [0.0, 0.0, 0.0, 0.0])

    def test_locomo_routes(self) -> None:
        """Top type over the 1,536 category 1-4 LoCoMo questions that cite evidence."""
        m = retrieval_eval_module()
        routes: dict[str, int] = {}
        for conv in load_locomo():
            for qa in conv["qa"]:
                if qa.get("category") in m.CATEGORIES and m.evidence_ids(qa):
                    scores = detect_query_type(qa["question"])
                    route = top_type(scores) if max(scores.values()) > 0 else "none"
                    routes[route] = routes.get(route, 0) + 1
        self.assertEqual(sum(routes.values()), 1536)
        self.assertEqual(routes, {QueryType.OPEN: 895, QueryType.TEMPORAL: 404, QueryType.ENTITY: 149,
                                  QueryType.MULTI_HOP: 58, QueryType.ADVERSARIAL: 3, "none": 27})


class FusionWeightTests(unittest.TestCase):
    def test_weights_sum_to_one_and_every_store_counts(self) -> None:
        for question in ("When did Caroline go to the LGBTQ support group?", "What is Caroline's identity?",
                         "What did Caroline research?", "What did she research?", "What is photosynthesis?"):
            with self.subTest(question=question):
                weights = store_weights(detect_query_type(question))
                self.assertEqual(set(weights), set(STORE_OF.values()))
                self.assertAlmostEqual(sum(weights.values()), 1.0, places=9)
                self.assertTrue(all(w > 0 for w in weights.values()))

    def test_dominant_store_type_takes_most_weight(self) -> None:
        for question, store in (("When did Caroline go to the LGBTQ support group?", "temporal"),
                                ("What is Caroline's identity?", "entity"),
                                ("How many children does Melanie have?", "reasoning"),
                                ("Did Caroline never paint?", "adversarial")):
            with self.subTest(question=question):
                weights = store_weights(detect_query_type(question))
                self.assertEqual(max(weights, key=weights.get), store)
                self.assertGreater(weights[store], 0.5)

    def test_open_weight_is_spread_over_every_store(self) -> None:
        """OPEN has no store: its weight goes 0.25 / 0.35 / 0.25 / 0.15 to the four stores."""
        weights = store_weights(detect_query_type("Which complaints are rising across sites?"))
        rounded = {k: round(v, 2) for k, v in weights.items()}
        self.assertEqual(rounded, {"temporal": 0.25, "entity": 0.31, "reasoning": 0.25, "adversarial": 0.19})
        open_only = store_weights({QueryType.OPEN: 1.0})
        self.assertAlmostEqual(open_only["entity"] - open_only["adversarial"], (0.35 - 0.15) * 1.05 / 1.25)

    def test_no_marker_weights_are_near_uniform(self) -> None:
        weights = store_weights(detect_query_type("What did she research?"))
        self.assertEqual({k: round(v, 2) for k, v in weights.items()},
                         {"temporal": 0.25, "entity": 0.27, "reasoning": 0.25, "adversarial": 0.23})


class RoutedStoreTypeTests(unittest.TestCase):
    def test_store_routes(self) -> None:
        cases = {
            "When did Caroline go to the LGBTQ support group?": QueryType.TEMPORAL,
            "What is Caroline's identity?": QueryType.ENTITY,
            "How many children does Melanie have?": QueryType.MULTI_HOP,
            "Did Caroline never paint?": QueryType.ADVERSARIAL,
            "Where did Caroline move from 4 years ago?": QueryType.ENTITY,  # 0.7 entity beats 0.6 temporal
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(routed_store_type(detect_query_type(question)), expected)

    def test_routes_without_a_store(self) -> None:
        for question in ("What is photosynthesis?", "What did Caroline research?", "What did she research?",
                         "Which complaints are rising across sites?", "What are Melanie's pets' names?"):
            with self.subTest(question=question):
                self.assertIsNone(routed_store_type(detect_query_type(question)))

    def test_ties(self) -> None:
        self.assertEqual(routed_store_type({QueryType.ENTITY: 0.6, QueryType.OPEN: 0.6}), QueryType.ENTITY)
        self.assertEqual(routed_store_type({QueryType.ENTITY: 0.4, QueryType.MULTI_HOP: 0.4}), QueryType.ENTITY)
        self.assertIsNone(routed_store_type({}))

    def test_locomo_questions_without_a_store_route(self) -> None:
        """895 OPEN-first plus 27 with no marker: 922 of the 1,536 questions that cite evidence."""
        m = retrieval_eval_module()
        none = total = 0
        for conv in load_locomo():
            for qa in conv["qa"]:
                if qa.get("category") in m.CATEGORIES and m.evidence_ids(qa):
                    total += 1
                    none += routed_store_type(detect_query_type(qa["question"])) is None
        self.assertEqual((none, total), (922, 1536))

    def test_coverage_script_routes_the_same_way(self) -> None:
        """research/benchmarks/router_coverage.py repeats the rule so it can run on older code."""
        retrieval_eval_module()
        import router_coverage  # noqa: PLC0415

        for conv in load_locomo():
            for qa in conv["qa"]:
                scores = detect_query_type(qa["question"])
                self.assertEqual(router_coverage.route(scores), routed_store_type(scores) or "none", qa["question"])


class NoStoreFallbackTests(unittest.IsolatedAsyncioTestCase):
    """A route with no store falls back to every store's full candidate list.

    LoCoMo conversation 0 with the hash embedder. Before the fix, each store passed
    only its top 40 / 50 / 40 / 30 to fusion whatever the route, and gold evidence of
    questions routed to OPEN fell outside all four lists.
    """

    async def test_candidates_by_route(self) -> None:
        m = retrieval_eval_module()
        conv = load_locomo()[0]
        with hash_embeddings(m):
            storage, _linker, nodes, _speakers = await m.ingest(None, 0, conv)
        tesseract = Tesseract(storage)
        everything = len(nodes)

        async def store_lists(question: str, depth: dict[str, int]) -> list[set[str]]:
            """Each store's candidates at the given depths, called as Tesseract.retrieve calls them."""
            scores = detect_query_type(question)
            effective = _expand_temporal_query(question) if scores[QueryType.TEMPORAL] > 0.3 else question
            qv = fake_embedding(question)
            novelty = _build_novelty_context(question, nodes)
            calls = (
                tesseract._temporal_store.retrieve(effective, qv, "conv_0", None, limit=depth[QueryType.TEMPORAL],
                                                   all_nodes=nodes, novelty_context=novelty),
                tesseract._entity_store.retrieve(question, qv, "conv_0", limit=depth[QueryType.ENTITY],
                                                 all_nodes=nodes, novelty_context=novelty),
                tesseract._reasoning_store.retrieve(question, qv, "conv_0", limit=depth[QueryType.MULTI_HOP],
                                                    all_nodes=nodes, novelty_context=novelty),
                tesseract._adversarial_store.retrieve(question, qv, "conv_0", limit=depth[QueryType.ADVERSARIAL],
                                                      all_nodes=nodes, novelty_context=novelty),
            )
            return [{n.node_id for n, _ in await call} for call in calls]

        questions = [qa for qa in conv["qa"] if qa.get("category") in m.CATEGORIES and m.evidence_ids(qa)]
        no_store = [qa for qa in questions if routed_store_type(detect_query_type(qa["question"])) is None][:12]
        routed = [qa for qa in questions if routed_store_type(detect_query_type(qa["question"]))][:4]
        self.assertEqual((len(no_store), len(routed)), (12, 4))
        dia = {n.node_id: n.metadata["dia_id"] for n in nodes}

        rescued = 0
        for qa in no_store:
            q = qa["question"]
            with self.subTest(route=None, question=q):
                got = await tesseract.retrieve(q, fake_embedding(q), "conv_0", limit=everything)
                candidates = {n.node_id for n, _ in got}
                full = set().union(*await store_lists(q, dict.fromkeys(STORE_TYPES, everything)))
                capped = set().union(*await store_lists(q, STORE_DEPTHS))
                self.assertLessEqual(full, candidates)  # every store's every candidate, nothing cut
                self.assertLessEqual(len(candidates - full), 6 * 3)  # plus temporal-momentum neighbours
                gold = set(m.evidence_ids(qa))
                self.assertLessEqual(gold, {dia[i] for i in candidates})
                rescued += len(gold - {dia[i] for i in capped})
        self.assertGreater(rescued, 0)  # gold the capped lists missed is now a candidate

        for qa in routed:
            q = qa["question"]
            with self.subTest(route=routed_store_type(detect_query_type(q)), question=q):
                got = await tesseract.retrieve(q, fake_embedding(q), "conv_0", limit=everything)
                capped = set().union(*await store_lists(q, STORE_DEPTHS))
                extra = {n.node_id for n, _ in got} - capped
                self.assertLessEqual(len(extra), 6 * 3)  # only temporal-momentum neighbours of the top 6


if __name__ == "__main__":
    unittest.main()

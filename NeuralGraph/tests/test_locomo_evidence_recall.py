"""Evidence recall by dia_id in the LoCoMo retrieval harness (research/benchmarks/retrieval_eval.py).

Each LoCoMo question cites the turns that answer it (``qa["evidence"]``, dia_ids such
as ``D1:3``). The harness scores, per question, the share of those dia_ids found among
the dia_ids of the first k retrieved messages (k = 10 and 50), averaged per category.

Everything runs offline with the hash embedder (see test_tesseract_ingestion.py).
"""

from __future__ import annotations

import re
import unittest

from NeuralGraph.research.retrieval.data_types import NeuralNode, NodeLayer
from NeuralGraph.tests.test_tesseract_ingestion import (
    first_sessions,
    hash_embeddings,
    load_locomo,
    retrieval_eval_module,
    tiny_conversation,
)


def node(dia_id: str | None, text: str = "hello") -> NeuralNode:
    metadata = {"speaker": "A"}
    if dia_id is not None:
        metadata["dia_id"] = dia_id
    return NeuralNode(node_id=f"n_{dia_id}", layer=NodeLayer.MESSAGE, content=text, metadata=metadata)


class EvidenceIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = retrieval_eval_module()

    def test_joined_entries_are_split(self) -> None:
        ids = self.m.evidence_ids
        self.assertEqual(ids({"evidence": ["D8:6; D9:17"]}), ["D8:6", "D9:17"])
        self.assertEqual(ids({"evidence": ["D9:1 D4:4 D4:6"]}), ["D9:1", "D4:4", "D4:6"])
        self.assertEqual(ids({"evidence": ["D1:3", "D2:4,D2:5"]}), ["D1:3", "D2:4", "D2:5"])
        self.assertEqual(ids({"evidence": "D3:1"}), ["D3:1"])

    def test_repeats_and_malformed_tokens_are_dropped(self) -> None:
        ids = self.m.evidence_ids
        self.assertEqual(ids({"evidence": ["D1:18", "D", "D1:20", "D1:18"]}), ["D1:18", "D1:20"])
        self.assertEqual(ids({"evidence": ["D:11:26", "D20:21"]}), ["D20:21"])
        self.assertEqual(ids({"evidence": []}), [])
        self.assertEqual(ids({}), [])

    def test_locomo_questions_with_evidence(self) -> None:
        """1,536 of the 1,540 category 1-4 questions cite evidence; 4 category-3 questions cite none."""
        runner_categories = self.m.CATEGORIES
        with_ev, without = 0, []
        joined = 0
        for conv in load_locomo():
            for qa in conv["qa"]:
                if qa.get("category") not in runner_categories:
                    continue
                ids = self.m.evidence_ids(qa)
                if ids:
                    with_ev += 1
                else:
                    without.append(qa["category"])
                joined += any(re.search(r"[;\s]", e.strip()) for e in qa.get("evidence", []))
                self.assertTrue(all(re.fullmatch(r"D\d+:\d+", i) for i in ids))
        self.assertEqual(with_ev, 1536)
        self.assertEqual(without, [3, 3, 3, 3])
        self.assertEqual(joined, 4)


class EvidenceRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = retrieval_eval_module()

    def test_share_of_gold_within_k(self) -> None:
        recall = self.m.evidence_recall
        retrieved = ["D9:9", "D1:1", None, "D1:2", "D1:1"]
        self.assertEqual(recall(["D1:1", "D1:2", "D3:3"], retrieved, 1), 0.0)
        self.assertAlmostEqual(recall(["D1:1", "D1:2", "D3:3"], retrieved, 2), 1 / 3)
        self.assertAlmostEqual(recall(["D1:1", "D1:2", "D3:3"], retrieved, 10), 2 / 3)
        self.assertEqual(recall(["D1:1"], retrieved, 50), 1.0)

    def test_no_gold_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            self.m.evidence_recall([], ["D1:1"], 10)

    def test_score_question_cuts_at_10_and_50(self) -> None:
        res = [(node(f"D1:{i}"), 1.0 - i / 100) for i in range(1, 61)]
        h = self.m.new_counts()
        # Gold at rank 5 (inside 10), rank 30 (inside 50) and rank 55 (outside 50).
        self.m.score_question(h, {"question": "q", "answer": "zzz", "evidence": ["D1:5; D1:30", "D1:55"]}, res)
        self.assertEqual((h["n"], h["n_ev"]), (1, 1))
        self.assertAlmostEqual(h["e10"], 1 / 3)
        self.assertAlmostEqual(h["e50"], 2 / 3)
        self.assertEqual((h["r10"], h["r50"]), (0, 0))

    def test_question_without_evidence_counts_for_substring_only(self) -> None:
        h = self.m.new_counts()
        self.m.score_question(h, {"question": "q", "answer": "hello", "evidence": []}, [(node(None), 1.0)])
        self.assertEqual((h["n"], h["r10"], h["n_ev"], h["e10"], h["e50"]), (1, 1, 0, 0.0, 0.0))


class HarnessEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """evaluate() reads dia_id off the retrieved nodes and reports ev@10 / ev@50."""

    async def test_tiny_conversation_table(self) -> None:
        m = retrieval_eval_module()
        conv = tiny_conversation()
        conv["qa"] = [
            {"question": "I am thinking about adoption agencies.", "answer": "adoption agencies",
             "evidence": ["D2:2"], "category": 4},
            {"question": "What did Melanie paint and when?", "answer": "a lake sunrise",
             "evidence": ["D1:2; D2:1", "D9:9"], "category": 1},
            {"question": "What is a sunrise?", "answer": "morning", "evidence": [], "category": 3},
            {"question": "Adversarial", "answer": "x", "evidence": ["D1:1"], "category": 5},
        ]
        with hash_embeddings(m):
            hits = await m.evaluate([conv], variants=["embed", "tesseract"], only_conv=set(), only_cat=set(),
                                    log=lambda _msg: None)
        for v in ("embed", "tesseract"):
            with self.subTest(variant=v):
                self.assertEqual(sorted(hits[v]), ["multi_hop", "open_domain", "single_hop"])
                single, multi, open_ = hits[v]["single_hop"], hits[v]["multi_hop"], hits[v]["open_domain"]
                self.assertEqual((single["n"], single["n_ev"], single["e10"], single["e50"]), (1, 1, 1.0, 1.0))
                # D9:9 is not in the conversation, so at most 2 of the 3 gold ids can be found.
                self.assertEqual((multi["n"], multi["n_ev"]), (1, 1))
                self.assertAlmostEqual(multi["e50"], 2 / 3)
                self.assertEqual((open_["n"], open_["n_ev"]), (1, 0))
        lines = m.format_table(hits)
        self.assertEqual(lines[0].split(), ["variant", "category", "n", "recall@10", "recall@50", "recall@all",
                                            "n_ev", "ev@10", "ev@50"])
        all_row = next(line.split() for line in lines if line.startswith("embed") and " ALL " in line)
        self.assertEqual(all_row[2], "3")
        self.assertEqual(all_row[6:9], ["2", "83.3%", "83.3%"])  # (1 + 2/3) / 2
        open_row = next(line.split() for line in lines if line.startswith("embed") and "open_domain" in line)
        self.assertEqual(open_row[6:9], ["0", "-", "-"])

    async def test_oracle_retrieval_scores_full_evidence_recall(self) -> None:
        """A retriever that returns each question's gold turns first scores ev@10 = 100%."""
        m = retrieval_eval_module()
        conv = first_sessions(load_locomo()[0], 3)
        dia_ids = {msg["dia_id"] for s in (1, 2, 3) for msg in conv["conversation"][f"session_{s}"]}
        conv["qa"] = [qa for qa in conv["qa"] if qa.get("category") in m.CATEGORIES
                      and 0 < len(m.evidence_ids(qa)) <= 10 and set(m.evidence_ids(qa)) <= dia_ids]
        self.assertGreaterEqual(len(conv["qa"]), 10)

        async def oracle(variant, question, qvec, storage, tesseract, linker, nodes, speakers, conv_idx):
            qa = next(q for q in conv["qa"] if q["question"] == question)
            gold = m.evidence_ids(qa)
            first = [n for n in nodes if n.metadata["dia_id"] in gold]
            rest = [n for n in nodes if n.metadata["dia_id"] not in gold]
            return [(n, 1.0) for n in first + rest][:m.TOP_K]

        saved = m.retrieve
        m.retrieve = oracle
        try:
            with hash_embeddings(m):
                hits = await m.evaluate([conv], variants=["oracle"], only_conv=set(), only_cat=set(),
                                        log=lambda _msg: None)
        finally:
            m.retrieve = saved
        counts = list(hits["oracle"].values())
        self.assertEqual(sum(h["n_ev"] for h in counts), len(conv["qa"]))
        for h in counts:
            self.assertEqual(h["e10"], h["n_ev"])
            self.assertEqual(h["e50"], h["n_ev"])


if __name__ == "__main__":
    unittest.main()

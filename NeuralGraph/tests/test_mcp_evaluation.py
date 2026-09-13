"""The pinned memory evaluation regenerates byte-for-byte and its claims hold.

Rule 2 in CLAUDE.md applies: never refresh the artifact to get green. If the
regenerated output differs, find out what moved, then re-pin deliberately.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from pathlib import Path

from NeuralGraph.mcp import evaluation

ARTIFACTS = Path(__file__).resolve().parents[1] / "mcp" / "artifacts"


class EvaluationArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = asyncio.run(evaluation.run_evaluation())
        cls.payload = json.dumps(cls.result, sort_keys=True, indent=2) + "\n"

    def test_json_artifact_is_byte_identical(self):
        pinned = (ARTIFACTS / "memory_eval.json").read_text(encoding="utf-8")
        self.assertEqual(pinned, self.payload, "memory_eval.json moved; diff it, then re-pin deliberately")

    def test_markdown_artifact_is_byte_identical(self):
        pinned = (ARTIFACTS / "memory_eval.md").read_text(encoding="utf-8")
        self.assertEqual(pinned, evaluation.render_markdown(self.result))

    def test_sha256sums_match_the_files(self):
        for line in (ARTIFACTS / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split()
            self.assertEqual(hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest(), digest, name)

    def test_each_component_helps_its_own_category_and_all_beats_baseline(self):
        c = self.result["configurations"]
        self.assertGreater(c["spelling"]["by_category"]["misspelled"]["p_at_1"], c["baseline"]["by_category"]["misspelled"]["p_at_1"])
        self.assertGreater(c["thesaurus"]["by_category"]["paraphrase"]["p_at_1"], c["baseline"]["by_category"]["paraphrase"]["p_at_1"])
        self.assertGreater(c["names"]["by_category"]["name"]["p_at_1"], c["baseline"]["by_category"]["name"]["p_at_1"])
        self.assertGreater(c["all"]["overall"]["p_at_1"], c["baseline"]["overall"]["p_at_1"])
        for cat in ("exact", "paraphrase", "misspelled", "name", "keyed"):
            self.assertGreaterEqual(c["all"]["by_category"][cat]["p_at_1"], c["baseline"]["by_category"][cat]["p_at_1"], cat)
        self.assertGreaterEqual(c["all"]["overall"]["p_at_1"], 0.85)
        self.assertGreaterEqual(c["all"]["overall"]["r_at_5"], 0.90)

    def test_confidence_gate_answers_no_unanswerable_query(self):
        gate = self.result["configurations"]["all"]["confidence_gate"]["0.34"]
        self.assertEqual(gate["negatives_answered"], 0)
        self.assertGreaterEqual(gate["positives_answered"], 34)


if __name__ == "__main__":
    unittest.main()

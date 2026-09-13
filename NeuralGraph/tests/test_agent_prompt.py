"""The agent prompt is generated from the contracts and the doc is in sync."""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path

from NeuralGraph.coordination import agent_prompt
from NeuralGraph.coordination.contracts import (
    ClaimEnvelope,
    LearningSignal,
    QueryRequest,
    RetrievalTrace,
    VerificationRequest,
    VerificationResult,
)

ROOT = Path(__file__).resolve().parents[2]


class AgentPromptTests(unittest.TestCase):
    def test_every_contract_field_is_named(self):
        text = agent_prompt.render_node_prompt("node-x", "cap", ("scope:a",), ("left", "right"))
        for cls in (QueryRequest, ClaimEnvelope, RetrievalTrace, VerificationRequest, VerificationResult, LearningSignal):
            for f in fields(cls):
                self.assertIn(f"`{f.name}`", text, f"{cls.__name__}.{f.name} missing from prompt")

    def test_invariants_and_nevers_are_present(self):
        text = agent_prompt.render_node_prompt()
        for line in agent_prompt.INVARIANTS:
            self.assertIn(line, text)
        for line in agent_prompt.NEVER:
            self.assertIn(line, text)
        self.assertIn(str(agent_prompt.ASK_THRESHOLD), text)
        self.assertIn(str(agent_prompt.MAX_QUESTION_ROUNDS), text)
        self.assertIn("does not yet route them", text)  # honesty about questioning

    def test_parameters_are_substituted(self):
        text = agent_prompt.render_node_prompt("node-q", "opaque_pair_reconstruction", ("capability:opaque-pair",), ("left", "right"))
        self.assertIn("node `node-q`", text)
        self.assertIn("`opaque_pair_reconstruction`", text)
        self.assertIn("capability:opaque-pair", text)
        self.assertIn("left, right", text)
        self.assertNotIn("<your-node-id>", text)

    def test_document_is_in_sync(self):
        doc = ROOT / "docs" / "AGENT_PROMPT.md"
        self.assertTrue(doc.exists(), "run: python -m NeuralGraph.coordination.agent_prompt --write docs/AGENT_PROMPT.md")
        self.assertEqual(doc.read_text(encoding="utf-8"), agent_prompt.render_document())

    def test_rendering_is_deterministic(self):
        self.assertEqual(agent_prompt.render_document(), agent_prompt.render_document())


if __name__ == "__main__":
    unittest.main()

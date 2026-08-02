"""Regression tests for a leakage-free, multimodal LoCoMo harness."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path

from NeuralGraph.benchmarking import (
    check_evidence_recall,
    compose_memory_content,
    extract_target_speakers,
    fuse_ranked_candidates,
    is_abstention,
    rank_nodes_lexically,
)
from NeuralGraph.answering import get_embedding
from NeuralGraph.llm_profile_query import query_profile_with_llm
from NeuralGraph.openai_client import extract_response_text, openai_text
from NeuralGraph.speaker_profiles import UniversalSpeakerProfiler


@dataclass
class DummyNode:
    node_id: str
    content: str
    speaker_id: str


def test_multimodal_caption_is_ingested_without_image_query() -> None:
    message = {
        "text": "Take a look at this.",
        "blip_caption": "a black and white pottery bowl",
        "query": "secret image search keywords",
    }
    content = compose_memory_content(message)
    assert "[IMAGE] a black and white pottery bowl" in content
    assert "secret image search keywords" not in content


def test_target_speaker_matching_is_exact() -> None:
    speakers = ["Mel", "Melanie", "Caroline"]
    assert extract_target_speakers("What did Melanie paint?", speakers) == ["Melanie"]
    assert extract_target_speakers("What did Caroline and Melanie paint?", speakers) == [
        "Caroline",
        "Melanie",
    ]


def test_lexical_ranker_prefers_named_speaker_and_caption_fact() -> None:
    nodes = [
        DummyNode("wrong", "[IMAGE] a black and white pottery bowl", "Caroline"),
        DummyNode("right", "I made this.\n[IMAGE] a black and white pottery bowl", "Melanie"),
        DummyNode("noise", "We went hiking near a lake.", "Melanie"),
    ]
    ranked = rank_nodes_lexically(
        "Did Melanie make the black and white bowl?",
        nodes,
        speakers=["Caroline", "Melanie"],
    )
    assert ranked[0][0].node_id == "right"


def test_rank_fusion_keeps_unique_candidates() -> None:
    first = DummyNode("one", "one", "A")
    second = DummyNode("two", "two", "B")
    fused = fuse_ranked_candidates([(first, 0.8)], [(second, 4.0), (first, 3.0)])
    assert [node.node_id for node, _score in fused] == ["one", "two"]


def test_retrieval_recall_uses_evidence_ids_not_answer_text() -> None:
    memories = [
        {"dia_id": "D1:2", "text": "no gold string here"},
        {"dia_id": "D1:3", "text": "still no gold string"},
    ]
    assert check_evidence_recall(["D1:3"], memories, top_k=2) == (True, 2)
    assert check_evidence_recall(["D1:3"], memories, top_k=1) == (False, 0)


def test_adversarial_null_answers_require_abstention() -> None:
    assert is_abstention("NOT_FOUND")
    assert is_abstention("Not mentioned in the memories")
    assert not is_abstention("Sweden")


def test_local_embeddings_are_deterministic_and_normalized() -> None:
    first = asyncio.run(get_embedding(None, "Melanie made a pottery bowl"))
    second = asyncio.run(get_embedding(None, "Melanie made a pottery bowl"))
    assert first == second
    assert len(first) == 1024
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_openai_response_text_ignores_non_message_items() -> None:
    payload = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "answer"}],
            },
        ]
    }
    assert extract_response_text(payload) == "answer"


def test_openai_client_uses_environment_key_without_storing_it(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, content_type=None):
            return {
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "NOT_FOUND"}],
                }],
            }

    class FakeSession:
        def post(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs
            return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    session = FakeSession()
    answer = asyncio.run(openai_text(session, "question", model="gpt-5.6-sol"))
    assert answer == "NOT_FOUND"
    assert session.url.endswith("/responses")
    assert session.kwargs["headers"]["authorization"] == "Bearer test-only-key"
    assert "authorization" not in session.kwargs["json"]


def test_profile_interfaces_cannot_receive_gold_answers() -> None:
    assert "gold" not in inspect.signature(query_profile_with_llm).parameters
    assert "gold" not in inspect.signature(UniversalSpeakerProfiler.query_single_hop).parameters


def test_runner_is_category_blind_and_contains_no_embedded_key() -> None:
    runner = Path(__file__).parents[2] / "demo" / "runner.py"
    source = runner.read_text(encoding="utf-8")
    assert "sk-proj-" not in source
    assert "sk-ant-api" not in source
    assert "api/generate" not in source
    assert "OPENAI_API_KEY" in source
    assert "category ==" not in source
    assert "category_id ==" not in source
    assert '5: "adversarial"' in source
    assert "check_evidence_recall" in source

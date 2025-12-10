"""Debug Retrieval: THE NAPOLEON VIEW

Run this script to see EXACTLY how answers are formed (or fail to form).
Traces every electron, every charge, every decay point.

Usage:
    python evaluation/debug_retrieval.py

This will:
1. Load a sample conversation
2. Run failing queries through the debugger
3. Print detailed trace of answer formation
4. Show backpropagation analysis (where things went wrong)
"""

import asyncio
import json
import sys
import os
import aiohttp

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime

# Config
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"


# Category mappings
CATEGORY_MAP = {
    1: "factual",
    2: "temporal",
    3: "inference",
}


async def get_embedding_async(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Get embedding for text via Ollama."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except Exception as e:
        print(f"Embedding error: {e}")
        return []


def extract_messages(conversation: dict) -> list[dict]:
    """Extract all messages from conversation sessions."""
    messages = []
    # Find all session keys (session_1, session_2, etc.)
    session_keys = sorted([k for k in conversation.keys() if k.startswith("session_") and k[-1].isdigit()])

    for session_key in session_keys:
        session = conversation.get(session_key, [])
        if isinstance(session, list):
            for msg in session:
                messages.append({
                    "speaker": msg.get("speaker", "Unknown"),
                    "text": msg.get("text", ""),
                    "dia_id": msg.get("dia_id", ""),
                })
    return messages


class EpisodeLike:
    """Minimal Episode-like object for neural graph ingestion."""
    def __init__(self, content: str, uid: str, speaker: str, session_key: str, msg_idx: int):
        self.content = content
        self.uid = uid
        self.producer_id = speaker
        self.created_at = datetime.now()
        self.filterable_metadata = {
            "speaker": speaker,
            "session_key": session_key,
            "msg_idx": msg_idx,
            "entity_ids": [],
        }
        self.importance_score = 0.5


async def main():
    """Run debug analysis on failing queries."""

    # Import here to ensure path is set
    from src.memmachine.neural_graph.service import NeuralGraphService, NeuralGraphServiceConfig
    from src.memmachine.neural_graph.debugger import RetrievalDebugger
    from src.memmachine.common.domain_classifier import SemanticRouter, classify_query

    print("=" * 80)
    print("RETRIEVAL DEBUGGER - THE NAPOLEON VIEW")
    print("Trace exactly how answers are formed (or fail to form)")
    print("=" * 80)

    # Load LoCoMo data
    locomo_path = Path("evaluation/locomo/locomo10.json")
    if not locomo_path.exists():
        print(f"ERROR: {locomo_path} not found")
        return

    with open(locomo_path) as f:
        data = json.load(f)

    # Get first sample
    sample = data[0]
    conversation = sample["conversation"]
    qa_list = sample["qa"]

    conv_name = f"{conversation['speaker_a']} & {conversation['speaker_b']}"
    messages = extract_messages(conversation)

    print(f"\nConversation: {conv_name}")
    print(f"Messages: {len(messages)}")
    print(f"Questions: {len(qa_list)}")

    # Initialize service with electron retriever
    config = NeuralGraphServiceConfig(
        enabled=True,
        use_electron_retriever=True,
        hierarchy_enabled=True,
        temporal_enabled=True,
        consolidation_enabled=False,
        auto_temporal_linking=True,
        auto_episode_segmentation=False,
    )
    service = NeuralGraphService(config=config)

    session_key = "debug_session"

    # Ingest conversation
    print("\n" + "-" * 80)
    print("INGESTING CONVERSATION...")
    print("-" * 80)

    async with aiohttp.ClientSession() as http_session:
        # Get embeddings
        print("Getting embeddings...")
        embeddings = []
        for i, msg in enumerate(messages):
            if i % 50 == 0:
                print(f"  Embedding {i}/{len(messages)}...")
            emb = await get_embedding_async(http_session, msg["text"])
            embeddings.append(emb)

        # Create episodes
        episodes = []
        for i, msg in enumerate(messages):
            episode = EpisodeLike(
                content=msg["text"],
                uid=f"{session_key}_msg_{i}",
                speaker=msg["speaker"],
                session_key=session_key,
                msg_idx=i,
            )
            episodes.append(episode)

        # Ingest into neural graph
        result = await service.process_episodes(
            episodes=episodes,
            embeddings=embeddings,
            session_key=session_key,
        )
        print(f"Ingested {result.nodes_created} nodes")

        # Find temporal questions (category == 2)
        print("\n" + "-" * 80)
        print("ANALYZING TEMPORAL QUESTIONS...")
        print("-" * 80)

        temporal_questions = [q for q in qa_list if q.get("category") == 2][:5]

        print(f"\nFound {len(temporal_questions)} temporal questions to debug")

        # Debug each question
        for i, q in enumerate(temporal_questions):
            question = q['question']
            answer = str(q['answer'])

            print("\n" + "=" * 80)
            print(f"DEBUG #{i+1}: {question}")
            print("=" * 80)
            print(f"Expected Answer: {answer}")

            # First, show query decomposition
            print("\n--- QUERY DECOMPOSITION ---")
            decomp = classify_query(question)
            print(decomp)

            # Get embedding
            query_embedding = await get_embedding_async(http_session, question)

            # Create debugger
            debugger = RetrievalDebugger(service._storage)
            debugger.start_debug(question, answer)

            # Record decomposition
            debugger.record_query_decomposition(
                domain=decomp.primary_domain.value,
                seeking=decomp.seeking,
                subjects=decomp.subjects,
                objects=decomp.objects,
                actions=decomp.actions,
                context_amplitudes=decomp.boost_dimensions,
            )

            # Get seeds manually to debug
            print("\n--- SEED SELECTION ---")
            seeds = await service._storage.vector_search(
                query_embedding,
                session_key,
                limit=20,
            )
            await debugger.record_seed_nodes(seeds)

            print(f"Found {len(seeds)} seed nodes:")
            for j, (node, score) in enumerate(seeds[:10]):
                content_preview = node.content[:60] if node.content else "???"
                has_answer = answer.lower() in node.content.lower() if node.content else False
                marker = " <-- CONTAINS ANSWER!" if has_answer else ""
                print(f"  [{j+1}] score={score:.3f} | \"{content_preview}...\"{marker}")

                # Check entity match
                for subj in decomp.subjects:
                    if subj.lower() in (node.content or "").lower():
                        print(f"       ^ Contains entity: {subj}")

            # Run actual retrieval
            print("\n--- RETRIEVAL RESULTS ---")
            retrieval_result = await service.retrieve(
                query_text=question,
                query_embedding=query_embedding,
                session_key=session_key,
                limit=10,
            )

            await debugger.record_final_results(retrieval_result.nodes)

            print(f"Stages: {retrieval_result.stages_executed}")
            print(f"Results: {len(retrieval_result.nodes)}")

            got_correct = False
            for j, (node, score) in enumerate(retrieval_result.nodes[:10]):
                content_preview = node.content[:60] if node.content else "???"
                has_answer = answer.lower() in node.content.lower() if node.content else False
                if has_answer:
                    got_correct = True
                    print(f"  [{j+1}] score={score:.3f} | \"{content_preview}...\" <-- CORRECT!")
                else:
                    print(f"  [{j+1}] score={score:.3f} | \"{content_preview}...\"")

            # Backpropagation analysis
            print("\n--- BACKPROPAGATION ANALYSIS ---")
            report = debugger.finish_debug()
            report.got_correct = got_correct

            if report.backprop:
                print(f"Target found: {report.backprop.target_found}")
                print(f"Target rank: {report.backprop.target_rank}")
                print("\nSuccess factors:")
                for f in report.backprop.success_factors:
                    print(f"  + {f}")
                print("\nFailure factors:")
                for f in report.backprop.failure_factors:
                    print(f"  - {f}")
                print("\nRecommendations:")
                for r in report.backprop.recommendations:
                    print(f"  > {r}")

            # Verdict
            print("\n--- VERDICT ---")
            print(f"Got correct answer: {got_correct}")

            if not got_correct:
                # Deep dive - where is the answer?
                print("\n--- SEARCHING FOR ANSWER IN ALL NODES ---")
                all_nodes = await service._storage.get_all_nodes(session_key)
                answer_nodes = []
                for node in all_nodes:
                    if answer.lower() in (node.content or "").lower():
                        answer_nodes.append(node)

                if answer_nodes:
                    print(f"Found {len(answer_nodes)} nodes containing the answer:")
                    for node in answer_nodes[:5]:
                        print(f"  - {node.node_id[:12]}...")
                        print(f"    Content: \"{node.content[:80]}...\"")
                        print(f"    Wave: {node.wave_amplitudes if hasattr(node, 'wave_amplitudes') else 'N/A'}")

                        # Check if it was a seed
                        was_seed = any(s[0].node_id == node.node_id for s in seeds)
                        print(f"    Was seed: {was_seed}")

                        if not was_seed:
                            # Why wasn't it selected as seed?
                            # Compute its similarity to query
                            node_embedding = await get_embedding_async(http_session, node.content)
                            from numpy import dot
                            from numpy.linalg import norm
                            if node_embedding and query_embedding:
                                sim = dot(query_embedding, node_embedding) / (norm(query_embedding) * norm(node_embedding))
                                print(f"    Vector similarity to query: {sim:.3f}")
                                if sim < 0.7:
                                    print(f"    ^ LOW SIMILARITY - This is why it wasn't a seed!")
                else:
                    print("Answer not found in ANY node! Data ingestion problem?")

            print("\n")

    # Cleanup
    await service.close()

    print("\n" + "=" * 80)
    print("DEBUG COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

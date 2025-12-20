"""
Call Center Benchmark Runner
Tests NeuralGraph memory system on realistic call center conversation
to verify the system generalizes beyond LoCoMo benchmark.

Uses the SAME architecture as demo/runner.py to ensure fair evaluation.
"""

import json
import asyncio
import aiohttp
import sys
import re
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from NeuralGraph import NeuralNode, NeuralEdge, NodeLayer, EdgeType, generate_edge_id
from NeuralGraph.storage import InMemoryNeuralGraphStorage
from NeuralGraph.tesseract import Tesseract
from NeuralGraph.dialogue_linker import DialogueLinker
from NeuralGraph.temporal_utils import (
    parse_datetime_flexible,
    resolve_relative_dates,
    generate_temporal_tokens,
    infer_query_mode,
)
from NeuralGraph.speaker_profiles import UniversalSpeakerProfiler
from NeuralGraph.llm_profile_extractor import extract_facts_parallel

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

# Optional: GPT-4o for judging (set to None for substring matching)
OPENAI_API_KEY = None  # Set your key here for GPT-4o judging

BENCHMARK_PATH = Path(__file__).parent / "call_center_benchmark.json"
OUTPUT_PATH = Path(__file__).parent / "call_center_results.json"

TOP_K = 50


def extract_keywords(text: str) -> list[str]:
    """Simple keyword extraction for indexing."""
    words = re.findall(r'\b[a-z]{3,}\b', text.lower())
    stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had', 'her', 'was', 'one', 'our'}
    return [w for w in words if w not in stopwords]


async def get_embedding(session, text: str) -> list[float]:
    """Get embedding from Ollama."""
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


async def generate_answer(session, question: str, context: str, mode: str = "STRICT") -> str:
    """Generate answer using Ollama."""
    if mode == "TEMPORAL":
        prompt = f"""Answer using ONLY the memories below.

TEMPORAL RULES:
1. If the event date is explicitly resolved in text, use that.
2. Output ONLY the date/time period (concise).
3. If the date cannot be determined, say "Not mentioned in the memories".

MEMORIES:
{context}

QUESTION: {question}

Answer (date/time only):"""
    elif mode == "AGGREGATION":
        prompt = f"""Answer by AGGREGATING information from the memories below.

AGGREGATION RULES:
1. SCAN ALL PROVIDED MEMORIES - the answer may be scattered across multiple memories.
2. BE SPECIFIC - extract exact names, places, items.
3. BE COMPLETE - find ALL items mentioned.
4. If not found, say "Not mentioned in the memories".

MEMORIES:
{context}

QUESTION: {question}

Answer (be specific and complete):"""
    else:
        prompt = f"""Answer the question using ONLY the memories below.

RULES:
1. Speaker metadata shows WHO said what.
2. Use only facts EXPLICITLY supported by the provided memories.
3. Keep answer short: 1-2 sentences.
4. If not found, say "Not mentioned in the memories".

MEMORIES:
{context}

QUESTION: {question}

Answer:"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0, "num_predict": 150}},
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            result = await response.json()
            return result.get("response", "").strip()
    except Exception as e:
        return f"Error: {e}"


async def judge_answer(session, question: str, expected: str, actual: str) -> tuple[bool, str]:
    """Judge if the answer is correct."""
    expected_lower = expected.lower().strip()
    actual_lower = actual.lower().strip()

    # Exact match
    if expected_lower in actual_lower:
        return True, "exact_match"

    # Key terms matching
    key_terms = re.findall(r'\b[a-z0-9]{3,}\b', expected_lower)
    key_terms = [t for t in key_terms if t not in {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all'}]

    if key_terms:
        matches = sum(1 for t in key_terms if t in actual_lower)
        if matches >= len(key_terms) * 0.6:
            return True, f"key_terms ({matches}/{len(key_terms)})"

    # GPT-4o judging if available
    if OPENAI_API_KEY:
        prompt = f"""Judge if the answer is semantically correct.

Question: {question}
Expected: {expected}
Actual: {actual}

Reply with ONLY "CORRECT" or "INCORRECT"."""

        try:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 10,
                    "temperature": 0
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                result = await resp.json()
                judgment = result["choices"][0]["message"]["content"].strip().upper()
                return "CORRECT" in judgment, "gpt4o"
        except Exception as e:
            pass

    return False, "no_match"


async def run_benchmark():
    print("=" * 70)
    print("CALL CENTER BENCHMARK - NeuralGraph Memory System")
    print("=" * 70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Purpose: Verify system generalizes beyond LoCoMo benchmark")
    print()

    # Load benchmark data
    with open(BENCHMARK_PATH) as f:
        benchmark = json.load(f)

    conversation = benchmark["conversation"]
    questions = benchmark["questions"]

    print(f"Conversation turns: {len(conversation)}")
    print(f"Test questions: {len(questions)}")
    print()

    async with aiohttp.ClientSession() as http:
        # Initialize storage and components
        print("Initializing NeuralGraph components...")
        storage = InMemoryNeuralGraphStorage()
        tesseract = Tesseract(storage)
        dialogue_linker = DialogueLinker(storage)
        speaker_profiler = UniversalSpeakerProfiler()

        # Prepare messages for ingestion
        messages = []
        base_time = datetime.now()

        for i, turn in enumerate(conversation):
            if turn["speaker"] == "System":
                continue

            speaker = turn["speaker"]
            if speaker.startswith("Agent_"):
                speaker = speaker.replace("Agent_", "")
            elif speaker.startswith("Customer_"):
                speaker = speaker.replace("Customer_", "")

            messages.append({
                "speaker": speaker,
                "text": turn["text"],
                "datetime": base_time.strftime("%Y-%m-%d %H:%M:%S"),
            })

        print(f"Prepared {len(messages)} messages for ingestion")

        # Extract facts in parallel
        print("Extracting speaker facts...")
        extraction_start = time.perf_counter()
        all_facts = await extract_facts_parallel(http, messages, batch_size=10)
        extraction_time = time.perf_counter() - extraction_start
        print(f"  Extracted facts in {extraction_time:.1f}s")

        # Add facts to profiler
        for msg, facts in zip(messages, all_facts):
            await speaker_profiler.add_message(msg["speaker"], msg["text"], llm_extractor=None)
            if facts:
                speaker_profiler.profiles[msg["speaker"]].extracted_facts.extend(facts)

        # Index messages
        print("Indexing messages...")
        session_key = "call_center_001"
        all_node_ids = []
        speaker_nodes = {}

        for msg_idx, msg in enumerate(messages):
            embedding = await get_embedding(http, msg["text"])
            if not embedding:
                continue

            keywords = extract_keywords(msg["text"])
            content_entities = set(re.findall(r'\b[A-Z][a-z]+\b', msg["text"]))

            message_date = parse_datetime_flexible(msg["datetime"])
            temporal_data = generate_temporal_tokens(message_date, msg["text"])
            resolved_content, resolved_dates = resolve_relative_dates(msg["text"], message_date)

            node_id = f"cc_msg_{msg_idx}"
            node = NeuralNode(
                node_id=node_id,
                session_key=session_key,
                content=resolved_content,
                layer=NodeLayer.MESSAGE,
                embedding=embedding,
                created_at=datetime.now(),
                metadata={
                    "speaker": msg["speaker"],
                    "datetime": msg["datetime"],
                    "keywords": list(keywords),
                    "entities": list(content_entities),
                    "date_tokens": temporal_data["date_tokens"],
                    "temporal": temporal_data["temporal_metadata"],
                    "original_content": msg["text"],
                    "resolved_content": resolved_content,
                },
            )
            await storage.save_node(node)
            all_node_ids.append(node_id)

            speaker = msg["speaker"].lower()
            if speaker not in speaker_nodes:
                speaker_nodes[speaker] = []
            speaker_nodes[speaker].append(node_id)

        # Create speaker edges
        edge_count = 0
        for speaker, node_ids in speaker_nodes.items():
            for i, src_id in enumerate(node_ids):
                for j in range(i + 1, min(i + 4, len(node_ids))):
                    tgt_id = node_ids[j]
                    edge = NeuralEdge(
                        edge_id=generate_edge_id(),
                        source_id=src_id,
                        target_id=tgt_id,
                        edge_type=EdgeType.ENTITY,
                        base_weight=0.8,
                        metadata={"speaker": speaker},
                    )
                    await storage.save_edge(edge)
                    edge_count += 1

        # Create temporal edges
        temporal_edge_count = 0
        for i in range(len(all_node_ids) - 1):
            edge = NeuralEdge(
                edge_id=generate_edge_id(),
                source_id=all_node_ids[i],
                target_id=all_node_ids[i + 1],
                edge_type=EdgeType.TEMPORAL,
                base_weight=0.85,
                metadata={"direction": "before"},
            )
            await storage.save_edge(edge)
            temporal_edge_count += 1

        # Create dialogue links
        all_nodes = [await storage.get_node(nid) for nid in all_node_ids]
        all_nodes = [n for n in all_nodes if n is not None]
        aggregators, bindings = await dialogue_linker.process_dialogue_sequence(all_nodes, session_key)

        print(f"Indexed {len(all_node_ids)} memories")
        print(f"Created {edge_count} speaker edges, {temporal_edge_count} temporal edges")
        print(f"Dialogue links: {len(aggregators)} aggregators, {len(bindings)} bindings")
        print()

        # Run queries
        print("Running test queries...")
        print("-" * 70)

        results = {
            "correct": 0,
            "incorrect": 0,
            "by_type": {},
            "by_category": {},
            "details": []
        }

        total_query_time = 0

        for q in questions:
            qid = q["id"]
            question = q["question"]
            expected = q["answer"]
            qtype = q["type"]
            category = q["category"]

            # Initialize counters
            if qtype not in results["by_type"]:
                results["by_type"][qtype] = {"correct": 0, "total": 0}
            if category not in results["by_category"]:
                results["by_category"][category] = {"correct": 0, "total": 0}

            query_start = time.perf_counter()

            # Get query embedding
            query_emb = await get_embedding(http, question)
            if not query_emb:
                continue

            # Retrieve memories
            retrieved = await tesseract.retrieve(
                query_text=question,
                query_embedding=query_emb,
                session_key=session_key,
                limit=TOP_K,
                auto_expand_temporal=True,
            )

            # Expand with dialogue links
            expanded_results = []
            seen_ids = set()
            for node, charge in retrieved:
                if node.node_id not in seen_ids:
                    expanded_results.append((node, charge))
                    seen_ids.add(node.node_id)

                linked_ids = dialogue_linker.get_linked_messages(node.node_id)
                for linked_id in linked_ids:
                    if linked_id not in seen_ids:
                        linked_node = await storage.get_node(linked_id)
                        if linked_node:
                            expanded_results.append((linked_node, charge * 0.85))
                            seen_ids.add(linked_id)

            expanded_results.sort(key=lambda x: x[1], reverse=True)

            # Determine query mode
            query_mode = infer_query_mode(question)

            # Build context
            num_memories = 30 if query_mode == "AGGREGATION" else 15
            context_parts = []
            for node, charge in expanded_results[:num_memories]:
                speaker = node.metadata.get("speaker", "Unknown")
                text = node.metadata.get("original_content", node.content)
                context_parts.append(f"[{speaker}] {text}")

            context = "\n".join(context_parts)

            # Generate answer
            answer = await generate_answer(http, question, context, mode=query_mode)

            query_time = time.perf_counter() - query_start
            total_query_time += query_time

            # Judge the answer
            is_correct, judgment = await judge_answer(http, question, expected, answer)

            # Update results
            results["by_type"][qtype]["total"] += 1
            results["by_category"][category]["total"] += 1

            if is_correct:
                results["correct"] += 1
                results["by_type"][qtype]["correct"] += 1
                results["by_category"][category]["correct"] += 1
                status = "OK"
            else:
                results["incorrect"] += 1
                status = "FAIL"

            results["details"].append({
                "id": qid,
                "question": question,
                "expected": expected,
                "actual": answer,
                "correct": is_correct,
                "type": qtype,
                "category": category,
                "judgment": judgment,
                "latency_ms": round(query_time * 1000, 1)
            })

            print(f"[{status}] {qid}: {question[:50]}...")
            if not is_correct:
                print(f"      Expected: {expected[:50]}...")
                print(f"      Got: {answer[:50]}...")

    # Summary
    print()
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    total = results["correct"] + results["incorrect"]
    accuracy = results["correct"] / total * 100 if total > 0 else 0

    print(f"\nOverall Accuracy: {results['correct']}/{total} ({accuracy:.1f}%)")
    print(f"Average Latency: {total_query_time/total*1000:.0f}ms per query")

    print("\nBy Question Type:")
    for qtype, data in sorted(results["by_type"].items()):
        acc = data["correct"] / data["total"] * 100 if data["total"] > 0 else 0
        print(f"  {qtype}: {data['correct']}/{data['total']} ({acc:.1f}%)")

    print("\nBy Category:")
    for cat, data in sorted(results["by_category"].items()):
        acc = data["correct"] / data["total"] * 100 if data["total"] > 0 else 0
        print(f"  {cat}: {data['correct']}/{data['total']} ({acc:.1f}%)")

    # Save results
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "metadata": {
                "benchmark": "call_center",
                "timestamp": datetime.now().isoformat(),
                "purpose": "Verify generalization beyond LoCoMo",
            },
            "summary": {
                "accuracy": round(accuracy, 1),
                "correct": results["correct"],
                "total": total,
                "avg_latency_ms": round(total_query_time/total*1000, 1) if total > 0 else 0,
            },
            "by_type": results["by_type"],
            "by_category": results["by_category"],
            "details": results["details"]
        }, f, indent=2)

    print(f"\nResults saved to: {OUTPUT_PATH}")

    # Verdict
    print()
    print("=" * 70)
    if accuracy >= 80:
        print("VERDICT: PASS - System generalizes beyond LoCoMo!")
        print("The memory system works for call center use cases.")
    elif accuracy >= 60:
        print("VERDICT: PARTIAL - Some generalization, needs improvement")
        print("Review failed questions to identify gaps.")
    else:
        print("VERDICT: FAIL - System may be overfit to LoCoMo")
        print("Architecture changes may be needed for real-world use.")
    print("=" * 70)

    return results


if __name__ == "__main__":
    asyncio.run(run_benchmark())

# -*- coding: utf-8 -*-
"""LoCoMo WAVE PROPAGATION Benchmark.

Testing True Wave Mechanics:
- Token-to-token propagation (sequential flow)
- Domain-to-domain resonance (E<->T<->S<->R coupling)
- Interference-based retrieval (wave superposition)

R(m|Q) = |Sum_t Sum_d psi_m(t,d)* x psi_Q(t,d) x K(t->t', d->d')|^2

Target: 92%+ accuracy through proper wave physics.
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import requests
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph.wave_propagation import WavePropagationRetriever

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 3
TOP_K = 30

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


async def get_embedding(session: aiohttp.ClientSession, text: str) -> list[float]:
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except:
        return []


async def generate_answer(session: aiohttp.ClientSession, prompt: str) -> str:
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a memory retrieval system. Answer based ONLY on the memories provided. The speaker name at the start tells you WHO said it. If not stated, say 'Not mentioned'."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 8192}
            },
            timeout=aiohttp.ClientTimeout(total=180)
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {e}"


def judge_answer(question: str, gold: str, generated: str) -> int:
    prompt = f"""Label as CORRECT or WRONG.
Question: {question}
Gold: {gold}
Generated: {generated}
Rules: Partial=WRONG, NotMentioned when gold exists=WRONG.
Return JSON: {{"label": "CORRECT"}} or {{"label": "WRONG"}}"""
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "stream": False, "format": "json", "options": {"temperature": 0.0}},
            timeout=180
        )
        result = json.loads(resp.json()["message"]["content"])
        return 1 if result.get("label") == "CORRECT" else 0
    except:
        return 0


def extract_messages(item) -> list[dict]:
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1
    while True:
        session_key = f"session_{session_idx}"
        if session_key not in conversation:
            break
        session_time = conversation.get(f"session_{session_idx}_date_time", "Unknown")
        for msg_idx, msg in enumerate(conversation[session_key]):
            if isinstance(msg, dict) and 'text' in msg:
                messages.append({
                    "speaker": msg.get("speaker", "Unknown"),
                    "text": msg.get("text", ""),
                    "session_time": session_time,
                })
        session_idx += 1
    return messages


def parse_datetime(session_time: str) -> datetime | None:
    if not session_time or session_time == "Unknown":
        return None
    match = re.search(r"on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})", session_time)
    if match:
        day, month_str, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
        month = MONTH_NAMES.get(month_str, 1)
        time_match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", session_time, re.IGNORECASE)
        hour, minute = 12, 0
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            if time_match.group(3).lower() == "pm" and hour != 12:
                hour += 12
            elif time_match.group(3).lower() == "am" and hour == 12:
                hour = 0
        try:
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except:
            return None
    return None


async def process_question(
    http: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    retriever: WavePropagationRetriever,
) -> dict:
    question = qa.get("question", "")
    gold = str(qa.get("answer", ""))
    category = CATEGORIES.get(qa.get("category", 0), "unknown")

    start = time.time()
    embedding = await get_embedding(http, question)

    ret_start = time.time()
    results = retriever.query(question, limit=TOP_K, query_embedding=embedding)
    ret_time = time.time() - ret_start

    # Format context with bound content
    context_lines = []
    for memory, score, breakdown in results[:30]:
        ts = memory.timestamp.strftime("%Y-%m-%d %H:%M") if memory.timestamp else "?"
        context_lines.append(f"[{ts}] {memory.content}")

    context = "\n".join(context_lines)

    prompt = f"""These memories were retrieved through wave propagation resonance.
The content includes WHO said it (speaker name) and resolved temporal references.

Memories:
{context}

Question: {question}

Answer:"""

    generated = await generate_answer(http, prompt)

    loop = asyncio.get_event_loop()
    correct = await loop.run_in_executor(None, judge_answer, question, gold, generated)

    status = "CORRECT" if correct else "WRONG"
    top_score = results[0][1] if results else 0
    top_breakdown = results[0][2] if results else {}
    q_type = top_breakdown.get('query_type', 'unknown')

    print(f"  [{q_idx+1}] {category} ({q_type}): {status} | R={top_score:.3f} | e={top_breakdown.get('entity_match',0):.2f} s={top_breakdown.get('semantic_overlap',0):.2f} d={top_breakdown.get('domain_resonance',0):.2f}", flush=True)

    return {
        "question_id": q_idx,
        "category": category,
        "query_type": q_type,
        "question": question,
        "gold_answer": gold,
        "generated_answer": generated,
        "correct": correct == 1,
        "retrieval_time_ms": round(ret_time * 1000, 2),
        "resonance_score": round(top_score, 4),
        "breakdown": top_breakdown,
    }


async def run_benchmark(max_conversations: int = 10):
    print("=" * 70)
    print("LoCoMo WAVE PROPAGATION Benchmark")
    print("Token-to-token flow + Domain-to-domain resonance")
    print("=" * 70)

    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path) as f:
        data = json.load(f)

    conversations = data[:max_conversations]
    all_results = []
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    async with aiohttp.ClientSession() as http:
        for conv_idx, item in enumerate(conversations):
            conv_id = item.get("conversation_id", conv_idx)
            messages = extract_messages(item)
            speakers = list(set(m["speaker"] for m in messages))

            print(f"\n[Conv {conv_idx+1}/{len(conversations)}] {' & '.join(speakers[:2])}")

            # Create Wave Propagation Retriever
            retriever = WavePropagationRetriever()

            # Ingest all messages
            print(f"  Ingesting {len(messages)} memories with wave propagation...")
            for i, msg in enumerate(messages):
                ts = parse_datetime(msg.get("session_time", ""))
                emb = await get_embedding(http, msg["text"])
                retriever.ingest(
                    wave_id=f"conv{conv_id}_msg{i}",
                    content=msg["text"],
                    speaker=msg["speaker"],
                    timestamp=ts,
                    embedding=emb,
                )

            stats = retriever.get_stats()
            print(f"  Wave Field: {stats['total_memories']} memories, {stats['unique_entities']} entities, {stats['unique_semantics']} semantics")

            # Process questions
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions...")

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch = questions[batch_start:batch_start + CONCURRENT_QUESTIONS]
                tasks = [
                    process_question(http, batch_start + i, qa, retriever)
                    for i, qa in enumerate(batch)
                ]
                batch_results = await asyncio.gather(*tasks)
                for r in batch_results:
                    r["conversation_id"] = conv_id
                    all_results.append(r)

    # Results
    print("\n" + "=" * 70)
    print("WAVE PROPAGATION RESULTS")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    print(f"\nOverall: {correct}/{total} ({100*correct/total:.2f}%)")

    by_cat = defaultdict(list)
    for r in all_results:
        by_cat[r["category"]].append(r)

    print("\nPer-Category:")
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]:
        if cat in by_cat:
            c = sum(1 for r in by_cat[cat] if r["correct"])
            print(f"  {cat}: {c}/{len(by_cat[cat])} ({100*c/len(by_cat[cat]):.2f}%)")

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = results_dir / f"locomo10_WAVE_{ts}.json"
    with open(out_file, 'w') as f:
        json.dump({
            "method": "wave_propagation",
            "model": "token_flow + domain_resonance",
            "total": total,
            "correct": correct,
            "accuracy": correct/total if total else 0,
            "results": all_results,
        }, f, indent=2)

    print(f"\nSaved: {out_file}")

    if correct/total >= 0.92:
        print("\n*** 92%+ ACHIEVED! WAVE PROPAGATION WORKS! ***")

    return all_results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.max_conversations))

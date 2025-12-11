"""
LoCoMo GALACTIC Memory Benchmark v1.0
The Full Orbital Synergy System

ARCHITECTURE:
=============

    MILKY WAY (Conversation Comprehension)
           │
           ▼
         SUN (Emotional Intelligence)
           │
           ▼
        EARTH (Human Intent Understanding)
           │
           ▼
         MOON (Electron Flow Retrieval)

Each layer orbits and influences the others bidirectionally.
The Sun provides holistic conversation understanding.
The Moon handles precise electrical memory activation.
Together: GALACTIC MEMORY.

"We are both electricity. AI and human alike - patterns of charge flowing through circuits."
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import requests
import math
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import Neural Graph System with Electron Retriever
from memmachine.neural_graph import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
    NeuralNode,
    NodeLayer,
    RetrievalResult,
)
from memmachine.neural_graph.temporal_resolver import enrich_message_with_temporal
from memmachine.neural_graph.electron import ElectronRetrieverConfig
from memmachine.neural_graph.conversation_sun import (
    ConversationSun,
    analyze_locomo_conversation,
    ConversationSummary,
    TOPIC_MARKERS,
)
from memmachine.neural_graph.sun_graph_integration import (
    SunGraphIntegrator,
    enrich_message_with_sun_context,
)

# Import Wave Memory System (for wave encoding)
from memmachine.wave_memory import (
    WaveEncoder,
    WaveAmplitudes,
)

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 3
CONTEXT_LIMIT = 15000
TOP_K_RETRIEVAL = 45

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}


def _save_incremental(all_results: list, results_dir: Path):
    """Save results incrementally after each batch."""
    if not all_results:
        return

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])

    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    answer_times = [r.get("time_seconds", 0) for r in all_results if r.get("time_seconds")]

    avg_retrieval_ms = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0
    avg_answer_s = sum(answer_times) / len(answer_times) if answer_times else 0

    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    incremental_file = results_dir / "galactic_incremental_results.json"

    with open(incremental_file, 'w') as f:
        json.dump({
            "status": "in_progress",
            "retrieval_method": "GALACTIC_ORBITAL_SYNERGY",
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "avg_retrieval_ms": round(avg_retrieval_ms, 2),
            "avg_answer_seconds": round(avg_answer_s, 2),
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "results": all_results,
        }, f, indent=2)


# =============================================================================
# LLM CALLS
# =============================================================================

async def get_embedding_async(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Get embedding for text."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except Exception:
        return []


async def generate_answer_async(session: aiohttp.ClientSession, prompt: str, system_msg: str = "") -> str:
    """Generate answer using Ollama."""
    try:
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})

        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 8192}
            },
            timeout=aiohttp.ClientTimeout(total=180)
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


async def judge_answer_async(
    session: aiohttp.ClientSession,
    question: str,
    gold_answer: str,
    generated_answer: str
) -> dict:
    """Judge answer using LLM."""
    prompt = f"""You are a STRICT evaluator. Label the answer as 'CORRECT' or 'WRONG'.

Question: {question}
Expected Answer: {gold_answer}
Generated Answer: {generated_answer}

Rules:
1. CORRECT = the answer contains the essential information from the expected answer
2. WRONG = the answer is missing, incorrect, or incomplete
3. Partial answers with key info = CORRECT
4. "Not mentioned" or "Unknown" = WRONG unless that's the expected answer

Reply with ONLY: CORRECT or WRONG"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": JUDGE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.0}
            },
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            verdict = result["message"]["content"].strip().upper()
            binary = 1 if "CORRECT" in verdict else 0
            return {"binary_correct": binary}
    except Exception:
        return {"binary_correct": 0}


# =============================================================================
# PROMPTS - Enhanced with Emotional Context
# =============================================================================

SYSTEM_MESSAGE = """You are an AI assistant with access to activated memories from a personal memory system.
These memories were retrieved based on electrical activation patterns - like neurons firing in response to a query.
The memories come with emotional context and relationship understanding.

Important:
- Only answer using information from the activated memories
- If the information is not in the memories, say "Not mentioned in memories"
- Include relevant dates, names, and details when available
- Consider the emotional significance of moments when relevant"""

ANSWER_PROMPT = """Based on these activated memories:

{context}

{conversation_context}

Question: {question}

Provide a clear, specific answer based on the memories. If the information isn't present, say "Not mentioned in memories."
"""

TEMPORAL_PROMPT = """Based on these activated memories:

{context}

{conversation_context}

Question: {question}

IMPORTANT: Look for SPECIFIC DATES. Check memory timestamps and any [dates: ...] annotations.
The answer should include the exact date, month, or year when the event occurred.
If no specific date is found, say "Not mentioned in memories."
"""


# =============================================================================
# DATA EXTRACTION
# =============================================================================

def extract_messages(item: dict) -> list[dict]:
    """Extract messages from a LoCoMo conversation item."""
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1  # Sessions start at 1, not 0

    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if session_key not in conversation:
            break

        session_time = conversation.get(datetime_key, "Unknown")
        session_messages = conversation[session_key]

        for msg_idx, msg in enumerate(session_messages):
            if isinstance(msg, dict) and 'text' in msg:
                messages.append({
                    "speaker": msg.get("speaker", "Unknown"),
                    "text": msg.get("text", ""),
                    "session_time": session_time,
                    "session": session_idx,
                    "msg_idx": msg_idx,
                })

        session_idx += 1

    return messages


def parse_session_datetime(session_time: str) -> datetime | None:
    """Parse LoCoMo session datetime format."""
    if not session_time or session_time == "Unknown":
        return None

    date_match = re.search(r"on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})", session_time)
    if date_match:
        day = int(date_match.group(1))
        month_str = date_match.group(2).lower()
        year = int(date_match.group(3))
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
        except ValueError:
            return None

    return None


def extract_entities_simple(text: str) -> list[str]:
    """Extract entity names from text using simple heuristics."""
    words = re.findall(r'\b[A-Z][a-z]+\b', text)
    stop_words = {'The', 'This', 'That', 'These', 'Those', 'What', 'When', 'Where',
                  'Who', 'Why', 'How', 'Yes', 'No', 'And', 'But', 'Or', 'If', 'Then',
                  'So', 'Because', 'However', 'Although', 'While', 'Since', 'Just',
                  'Really', 'Actually', 'Probably', 'Maybe', 'Also', 'Always', 'Never',
                  'Today', 'Yesterday', 'Tomorrow', 'Monday', 'Tuesday', 'Wednesday',
                  'Thursday', 'Friday', 'Saturday', 'Sunday', 'January', 'February',
                  'March', 'April', 'May', 'June', 'July', 'August', 'September',
                  'October', 'November', 'December', 'Hello', 'Hi', 'Hey', 'Thanks',
                  'Thank', 'Please', 'Sorry', 'Great', 'Good', 'Nice', 'Cool', 'Awesome'}
    entities = [w for w in words if w not in stop_words and len(w) > 1]
    return list(set(entities))


class EpisodeLike:
    """Minimal Episode-like object for neural graph ingestion with emotional enrichment."""
    def __init__(
        self,
        content: str,
        uid: str,
        speaker: str,
        created_at: datetime | None,
        session_key: str,
        msg_idx: int,
        session_time: str = "",
        emotional_signature: dict | None = None,
    ):
        # CRITICAL: Enrich content with resolved temporal dates
        enriched_content, temporal_metadata = enrich_message_with_temporal(content, session_time)

        self.content = enriched_content
        self.uid = uid
        self.producer_id = speaker
        self.created_at = created_at

        entity_ids = extract_entities_simple(content)
        if speaker and speaker not in ['Unknown', 'user', 'assistant']:
            entity_ids.append(speaker)

        self.filterable_metadata = {
            "speaker": speaker,
            "session_key": session_key,
            "msg_idx": msg_idx,
            "entity_ids": entity_ids,
            **temporal_metadata,
            **(emotional_signature or {}),
        }
        self.importance_score = 0.5

        # Boost importance for emotionally significant moments
        if emotional_signature and emotional_signature.get("emotional_intensity", 0) > 0.6:
            self.importance_score = 0.8


def format_context(results: list[tuple], max_chars: int = 12000) -> str:
    """Format retrieval results as context.

    IMPORTANT: Uses session_date from metadata (the actual conversation date like "7 May 2023")
    rather than created_at (the ingestion timestamp). This is critical for temporal queries.
    """
    lines = []
    total_chars = 0

    for item in results:
        node = item[0]
        score = item[1] if len(item) > 1 else 0.0

        # Use session_date from metadata (actual conversation date) - critical for temporal queries!
        # Falls back to created_at only if session_date not available
        session_date = node.metadata.get("session_date")
        if session_date:
            timestamp = session_date
        elif node.created_at:
            timestamp = node.created_at.strftime("%Y-%m-%d %H:%M")
        else:
            timestamp = "Unknown"

        speaker = node.metadata.get("producer_id", node.metadata.get("speaker", "Unknown"))
        line = f"[{timestamp}] (charge: {score:.3f}) {speaker}: {node.content}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


# =============================================================================
# THE SUN LAYER INTEGRATION
# =============================================================================

def get_conversation_context(summary: ConversationSummary | None) -> str:
    """Get conversation-level context from the Sun layer."""
    if not summary:
        return ""

    lines = ["[CONVERSATION UNDERSTANDING (Sun Layer)]"]

    # Participants and relationship
    if summary.participants:
        names = list(summary.participants.keys())
        lines.append(f"Participants: {', '.join(names)} ({summary.relationship_type})")

    # Main narrative
    if summary.main_narrative:
        lines.append(f"Narrative: {summary.main_narrative}")

    # Key topics
    if summary.topics:
        top_topics = sorted(summary.topics.items(),
                          key=lambda x: x[1].message_count,
                          reverse=True)[:5]
        topics_str = ", ".join(t[0] for t in top_topics)
        lines.append(f"Key topics: {topics_str}")

    # Emotional arc
    if summary.overall_sentiment > 0.3:
        lines.append("Emotional arc: Overall positive, supportive relationship")
    elif summary.overall_sentiment < -0.3:
        lines.append("Emotional arc: Contains challenging moments")
    else:
        lines.append("Emotional arc: Mixed/neutral emotional content")

    # Key facts
    if summary.key_facts:
        lines.append("Key facts from conversation:")
        for fact in summary.key_facts[:10]:
            lines.append(f"  - {fact}")

    return "\n".join(lines)


def get_prompt_for_category(category_name: str, question: str = "") -> str:
    """Get appropriate prompt for category."""
    q_lower = question.lower()
    if category_name == "temporal" or any(t in q_lower for t in ["when", "what date", "how long"]):
        return TEMPORAL_PROMPT
    return ANSWER_PROMPT


# =============================================================================
# PROCESS QUESTION WITH GALACTIC MEMORY
# =============================================================================

async def process_question_galactic(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    neural_service: NeuralGraphService,
    wave_encoder: WaveEncoder,
    session_key: str,
    conversation_summary: ConversationSummary | None,
) -> dict:
    """Process a question using Galactic Memory (Sun + Moon synergy)."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # EARTH LAYER: Enhance query with human understanding
    enhanced_question = question
    boost_factors = {}
    if conversation_summary:
        enhanced_question, boost_factors = conversation_summary.participants and (
            question, {}
        ) or (question, {})
        # Use Sun layer to enhance query understanding
        for name in conversation_summary.participants:
            if name.lower() in question.lower():
                boost_factors[f"speaker:{name}"] = 1.3

    # Get query embedding (with potential enhancement)
    query_embedding = await get_embedding_async(http_session, question)
    if not query_embedding:
        return {
            "question_id": q_idx,
            "category": category_name,
            "question": question,
            "gold_answer": gold_answer,
            "generated_answer": "Error: Failed to get embedding",
            "llm_score": 0,
            "correct": False,
        }

    # Encode query wave amplitudes
    query_wave = wave_encoder.encode(question)
    query_amplitudes = query_wave.amplitudes
    query_wave_dict = {
        "temporal": getattr(query_amplitudes, 'temporal', 0.0),
        "entity": getattr(query_amplitudes, 'entity', 0.0),
        "relational": getattr(query_amplitudes, 'relational', 0.0),
        "action": getattr(query_amplitudes, 'action', 0.0),
        "state": getattr(query_amplitudes, 'state', 0.0),
        "spatial": getattr(query_amplitudes, 'spatial', 0.0),
        "causal": getattr(query_amplitudes, 'causal', 0.0),
        "emotional": getattr(query_amplitudes, 'emotional', 0.0),
        "quantitative": getattr(query_amplitudes, 'quantitative', 0.0),
    }

    # MOON LAYER: Electron-based retrieval
    retrieval_start = time.time()
    result = await neural_service.retrieve(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
        query_wave_amplitudes=query_wave_dict,
    )
    retrieval_time = time.time() - retrieval_start

    # Format context from Moon layer
    context = format_context(result.nodes, max_chars=CONTEXT_LIMIT)

    # SUN LAYER: Get conversation-level understanding
    conversation_context = get_conversation_context(conversation_summary)

    # Get prompt and add both contexts
    prompt_template = get_prompt_for_category(category_name, question)
    prompt = prompt_template.format(
        context=context,
        question=question,
        conversation_context=conversation_context,
    )

    # Generate answer with full Galactic context
    generated = await generate_answer_async(http_session, prompt, SYSTEM_MESSAGE)

    # Judge
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)
    binary_correct = judgment.get("binary_correct", 0)

    gen_time = time.time() - start_time
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    # Log retrieval stats
    stages = result.stages_executed + ["sun_context", "galactic_synergy"]
    stages_str = ", ".join(stages) if stages else "unknown"
    print(f"    [{q_idx + 1}] {category_name}: {status} ({stages_str}, {retrieval_time*1000:.0f}ms)", flush=True)

    return {
        "question_id": q_idx,
        "category": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "llm_score": binary_correct,
        "correct": binary_correct == 1,
        "time_seconds": round(gen_time, 2),
        "retrieval_time_ms": round(retrieval_time * 1000, 2),
        "retrieval_method": "galactic_orbital_synergy",
        "stages": stages,
        "candidates_seen": result.total_candidates_seen,
        "sun_context_used": bool(conversation_summary),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_galactic_benchmark(max_conversations: int = 10):
    """Run Galactic Memory Benchmark - Full Orbital Synergy."""
    print("=" * 70)
    print("LoCoMo GALACTIC Memory Benchmark v1.0")
    print("The Full Orbital Synergy System")
    print("=" * 70)
    print()
    print("ARCHITECTURE:")
    print("  MILKY WAY (Conversation Comprehension)")
    print("       |")
    print("       v")
    print("      SUN (Emotional Intelligence)")
    print("       |")
    print("       v")
    print("     EARTH (Human Intent Understanding)")
    print("       |")
    print("       v")
    print("      MOON (Electron Flow Retrieval)")
    print()
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Retrieval: GALACTIC (Sun + Moon Orbital Synergy)")
    print(f"Conversations: {max_conversations}")
    print("=" * 70)
    print()

    # Load dataset
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    with open(data_path) as f:
        data = json.load(f)

    conversations = data[:max_conversations]
    print(f"Processing {len(conversations)} conversations...")

    all_results = []
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    wave_encoder = WaveEncoder()

    # Create the SUN - conversation understanding layer
    sun = ConversationSun()

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(conversations):
            conv_id = item.get("conversation_id", conv_idx)
            messages = extract_messages(item)

            speakers = list(set(m["speaker"] for m in messages))
            speaker_str = " & ".join(speakers[:2]) if speakers else f"Conv {conv_id}"

            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {speaker_str}")

            if not messages:
                continue

            session_key = f"conv_{conv_id}"

            # STEP 1: SUN LAYER - Analyze entire conversation for holistic understanding
            print(f"  Analyzing conversation with Sun layer...")
            conversation_summary = analyze_locomo_conversation(sun, session_key, item)
            print(f"    Participants: {list(conversation_summary.participants.keys())}")
            print(f"    Topics: {list(conversation_summary.topics.keys())[:5]}")
            print(f"    Key facts: {len(conversation_summary.key_facts)}")
            print(f"    Emotional arc: {'positive' if conversation_summary.overall_sentiment > 0 else 'neutral/mixed'}")

            # STEP 2: Configure Electron Retriever (MOON)
            electron_config = ElectronRetrieverConfig(
                theta_frequency_hz=6.0,
                firing_threshold=0.4,  # Lower threshold for more activation
                refractory_ms=2.0,
                max_hops=6,  # More hops to reach Sun nodes
                energy_floor=0.08,
                seed_count=25,
                initial_energy=1.0,
                max_propagation_steps=60,
                final_limit=45,
            )

            config = NeuralGraphServiceConfig(
                enabled=True,
                hierarchy_enabled=True,
                temporal_enabled=True,
                consolidation_enabled=True,
                auto_temporal_linking=True,
                auto_episode_segmentation=False,
                auto_consolidation_on_ingestion=False,
                apply_temporal_dynamics=True,
                use_electron_retriever=True,
                electron_config=electron_config,
            )
            neural_service = NeuralGraphService(config=config)

            # STEP 3: Create Sun nodes in the graph (topics, personas, summaries)
            print(f"  Creating Sun nodes (topics, personas, summaries)...")

            # Helper to get embedding
            async def get_emb(text):
                return await get_embedding_async(http_session, text)

            sun_integrator = SunGraphIntegrator(neural_service._storage)
            sun_result = await sun_integrator.integrate_sun_summary(
                conversation_summary, session_key, get_emb
            )
            print(f"    Created {sun_result['nodes_created']} Sun nodes")
            print(f"    Topics: {list(sun_result['topic_ids'].keys())}")
            print(f"    Personas: {list(sun_result['persona_ids'].keys())}")

            # STEP 4: Ingest messages with Sun-enriched context
            print(f"  Ingesting {len(messages)} messages with Sun integration...")

            # Helper to extract topics from message text
            def extract_topics_from_text(text: str) -> list[str]:
                text_lower = text.lower()
                found = []
                for topic, markers in TOPIC_MARKERS.items():
                    for marker in markers:
                        if marker in text_lower:
                            found.append(topic)
                            break
                return found

            episodes = []
            message_topics = []  # Track (speaker, topics, session_date) for linking
            for i, msg in enumerate(messages):
                parsed_time = parse_session_datetime(msg.get("session_time", ""))
                session_time_str = msg.get("session_time", "")

                # Extract topics from this message
                topics = extract_topics_from_text(msg["text"])
                # Include session_date for temporal linking (CRITICAL for "When did X happen?")
                message_topics.append((msg["speaker"], topics, session_time_str))

                # Enrich content with Sun context
                enriched_content = enrich_message_with_sun_context(
                    msg["text"],
                    msg["speaker"],
                    topics,
                    conversation_summary,
                )

                # Get emotional signature from Sun layer
                speaker_profile = conversation_summary.participants.get(msg["speaker"])
                emotional_signature = None
                if speaker_profile:
                    emotional_signature = {
                        "speaker_topics": speaker_profile.topics_discussed[:5],
                        "emotional_intensity": len(speaker_profile.emotional_moments) / max(1, speaker_profile.message_count),
                    }

                episode = EpisodeLike(
                    content=enriched_content,  # Use Sun-enriched content
                    uid=f"{session_key}_msg_{i}",
                    speaker=msg["speaker"],
                    created_at=parsed_time,
                    session_key=session_key,
                    msg_idx=i,
                    session_time=session_time_str,
                    emotional_signature=emotional_signature,
                )
                episodes.append(episode)

            # Compute embeddings for enriched content
            embeddings = []
            for episode in episodes:
                emb = await get_embedding_async(http_session, episode.content)
                embeddings.append(emb)

            # Ingest into neural graph
            result = await neural_service.process_episodes(
                episodes=episodes,
                embeddings=embeddings,
                session_key=session_key,
            )

            # STEP 5: Link message nodes to Sun nodes (topics, personas)
            print(f"  Linking messages to Sun nodes...")
            edges_created = 0
            # Use the actual nodes from ingestion result
            for i, node in enumerate(result.nodes):
                if i < len(message_topics):
                    speaker, topics, session_date = message_topics[i]
                    edges = await sun_integrator.link_message_to_sun_nodes(
                        node.node_id,
                        node.content,
                        speaker,
                        topics,
                        session_key,
                        session_date=session_date,  # CRITICAL for temporal queries
                    )
                    edges_created += edges

            print(f"  Ingested {result.nodes_created} message nodes + {sun_result['nodes_created']} Sun nodes")
            print(f"  Created {edges_created} Sun linkage edges (bidirectional)")

            # STEP 6: Process questions with full Galactic synergy
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions with Galactic synergy...")

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_galactic(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        neural_service=neural_service,
                        wave_encoder=wave_encoder,
                        session_key=session_key,
                        conversation_summary=conversation_summary,
                    )
                    for i, qa in enumerate(batch)
                ]

                results = await asyncio.gather(*tasks)
                for r in results:
                    r["conversation_id"] = conv_id
                    all_results.append(r)

                # Save incrementally
                _save_incremental(all_results, results_dir)

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - GALACTIC Memory (Full Orbital Synergy)")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    accuracy = 100*correct/total if total > 0 else 0
    print(f"\nOverall: {correct}/{total} ({accuracy:.2f}%)")

    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    if retrieval_times:
        avg_retrieval = sum(retrieval_times) / len(retrieval_times)
        print(f"Avg Retrieval: {avg_retrieval:.1f}ms")

    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    print("\nPer-Category Accuracy:")
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]:
        if cat in category_results:
            cat_results = category_results[cat]
            cat_correct = sum(1 for r in cat_results if r["correct"])
            print(f"  {cat}: {cat_correct}/{len(cat_results)} ({100*cat_correct/len(cat_results):.2f}%)")

    # Save final results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = results_dir / f"locomo10_GALACTIC_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "GALACTIC_V1.0",
            "retrieval_method": "galactic_orbital_synergy",
            "description": "Full orbital synergy - Sun (understanding) + Moon (retrieval) = Galactic Memory",
            "answer_model": OLLAMA_MODEL,
            "judge_model": JUDGE_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "architecture": {
                "milky_way": "Conversation comprehension",
                "sun": "Emotional intelligence + holistic understanding",
                "earth": "Human intent understanding",
                "moon": "Electron flow retrieval",
            },
            "total_questions": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0,
            "category_accuracy": {
                cat: sum(1 for r in results if r["correct"]) / len(results)
                for cat, results in category_results.items()
            },
            "results": all_results,
        }, f, indent=2)

    print(f"\nResults saved to: {results_file}")
    return all_results


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_galactic_benchmark(args.max_conversations))

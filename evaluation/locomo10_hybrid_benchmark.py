"""
LoCoMo Hybrid Benchmark v1.0
Neural Graph + Theta-Gamma Coupling + Wave Resonance Boosting

This benchmark combines the best of both approaches:
1. Neural Graph embedding-based retrieval (PRIMARY) - proven 78.6% accuracy
2. Theta-Gamma phase coupling (BOOSTING) - brain-inspired temporal binding
3. Wave resonance (SCORING) - multi-dimensional semantic matching

Key Insight:
The brain-inspired benchmark failed (55.78%) because it used wave resonance
WITHOUT embeddings. This hybrid uses embeddings as foundation and adds
brain-inspired boosting factors.

Architecture:
- Foundation: Neural Graph embedding-based retrieval (like hippocampal pattern completion)
- Enhancement 1: Theta-gamma phase coherence boost for temporal binding
- Enhancement 2: Wave amplitude alignment boost for semantic dimensions
- Enhancement 3: Consolidation (LTP) boost for frequently co-activated memories

Target: >80% accuracy, especially on temporal questions (currently 57.6%)
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import requests
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import Neural Graph System (for embedding-based retrieval)
from memmachine.neural_graph import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
    NeuralNode,
    NodeLayer,
    RetrievalResult,
)

# Import Context Engineering (for semantic query understanding)
from memmachine.common.domain_classifier import (
    classify_query,
    QueryDecomposition,
    QueryDomain,
    QueryIntent,
)

# Import Wave Memory System (for wave encoding)
from memmachine.wave_memory import (
    WaveEncoder,
    MemoryWave,
    WaveAmplitudes,
    TemporalCalculator,
    ThetaPhase,
    GammaBurst,
    OscillationState,
    THETA_FREQUENCY_HZ,
    GAMMA_CYCLES_PER_THETA,
)

# Configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"

CONCURRENT_QUESTIONS = 3
CONTEXT_LIMIT = 15000
TOP_K_RETRIEVAL = 45  # Slightly higher for re-ranking

# Brain-inspired boosting weights
THETA_WEIGHT = 0.08  # Phase coherence boost
GAMMA_WEIGHT = 0.04  # Content slot match boost
WAVE_WEIGHT = 0.06   # Wave amplitude alignment boost
LTP_WEIGHT = 0.03    # Consolidation/LTP boost

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}


def _save_incremental(all_results: list, results_dir: Path):
    """Save results incrementally after each batch."""
    if not all_results:
        return

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])

    # Calculate latency stats
    retrieval_times = [r.get("retrieval_time_ms", 0) for r in all_results if r.get("retrieval_time_ms")]
    answer_times = [r.get("time_seconds", 0) for r in all_results if r.get("time_seconds")]

    avg_retrieval_ms = sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0
    avg_answer_s = sum(answer_times) / len(answer_times) if answer_times else 0

    # Category breakdown
    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    incremental_file = results_dir / "hybrid_incremental_results.json"

    with open(incremental_file, 'w') as f:
        json.dump({
            "status": "in_progress",
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
# HYBRID RETRIEVER - Neural Graph + Brain-Inspired Boosting
# =============================================================================

@dataclass
class HybridMemoryState:
    """Track brain state for each memory node."""
    node_id: str
    theta_phase: ThetaPhase
    gamma_bursts: list[GammaBurst]
    wave_amplitudes: dict[str, float]
    co_activation_count: int = 0
    ripple_count: int = 0


class HybridRetriever:
    """
    Hybrid retriever combining Neural Graph (embeddings) with Brain-Inspired boosting.

    How it works:
    1. PRIMARY: Neural Graph retrieves memories using embedding cosine similarity
    2. BOOST 1: Theta phase coherence (memories from same temporal context)
    3. BOOST 2: Gamma slot overlap (memories with matching content types)
    4. BOOST 3: Wave amplitude alignment (multi-dimensional semantic match)
    5. BOOST 4: LTP/consolidation (frequently co-activated memories)

    This mimics how the hippocampus retrieves memories:
    - Pattern completion (embeddings)
    - Temporal binding (theta)
    - Content binding (gamma)
    - Hebbian learning (LTP)
    """

    def __init__(
        self,
        neural_service: NeuralGraphService,
        theta_weight: float = THETA_WEIGHT,
        gamma_weight: float = GAMMA_WEIGHT,
        wave_weight: float = WAVE_WEIGHT,
        ltp_weight: float = LTP_WEIGHT,
    ):
        self.neural_service = neural_service
        self.theta_weight = theta_weight
        self.gamma_weight = gamma_weight
        self.wave_weight = wave_weight
        self.ltp_weight = ltp_weight

        # Brain state tracking
        self._memory_states: dict[str, HybridMemoryState] = {}
        self._session_reference: datetime | None = None
        self._wave_encoder = WaveEncoder()

    def set_reference_time(self, ref_time: datetime):
        """Set the reference time for theta phase calculations."""
        self._session_reference = ref_time

    def register_memory(
        self,
        node_id: str,
        content: str,
        timestamp: datetime | None,
    ):
        """Register a memory's brain state for boosting calculations."""
        if self._session_reference is None:
            self._session_reference = timestamp or datetime.now(timezone.utc)

        # Compute theta phase
        theta = ThetaPhase.from_timestamp(
            timestamp or datetime.now(timezone.utc),
            self._session_reference,
        )

        # Encode wave - returns MemoryWave, extract amplitudes
        memory_wave = self._wave_encoder.encode(content)
        wave_amplitudes = memory_wave.amplitudes  # WaveAmplitudes object

        # Compute gamma bursts from the amplitudes (not the MemoryWave!)
        gamma_bursts = self._compute_gamma_bursts(wave_amplitudes)

        # Store state - convert amplitudes to dict properly
        amp_dict = {
            'temporal': wave_amplitudes.temporal,
            'entity': wave_amplitudes.entity,
            'relational': wave_amplitudes.relational,
            'action': wave_amplitudes.action,
            'state': wave_amplitudes.state,
            'spatial': wave_amplitudes.spatial,
            'causal': wave_amplitudes.causal,
            'emotional': wave_amplitudes.emotional,
            'quantitative': wave_amplitudes.quantitative,
        }

        self._memory_states[node_id] = HybridMemoryState(
            node_id=node_id,
            theta_phase=theta,
            gamma_bursts=gamma_bursts,
            wave_amplitudes=amp_dict,
        )

    def _compute_gamma_bursts(self, wave_amp: WaveAmplitudes) -> list[GammaBurst]:
        """Compute gamma bursts from wave amplitudes."""
        bursts = []

        dimensions = [
            (0, getattr(wave_amp, 'temporal', 0.0), "temporal"),
            (1, getattr(wave_amp, 'entity', 0.0), "entity"),
            (2, getattr(wave_amp, 'action', 0.0), "action"),
            (3, getattr(wave_amp, 'relational', 0.0), "relational"),
            (4, getattr(wave_amp, 'state', 0.0), "state"),
            (5, getattr(wave_amp, 'spatial', 0.0), "spatial"),
            (6, getattr(wave_amp, 'emotional', 0.0), "emotional"),
        ]

        for slot, amplitude, _ in dimensions:
            if amplitude > 0.2:  # Only significant amplitudes
                freq = 40.0 + (amplitude * 25.0)  # 40-65 Hz
                bursts.append(GammaBurst(slot=slot, frequency=freq, power=amplitude))

        return bursts

    async def retrieve_with_boosting(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 40,
    ) -> list[tuple[NeuralNode, float, dict]]:
        """
        Retrieve memories with brain-inspired boosting.

        Returns:
            List of (node, boosted_score, boost_breakdown) tuples
        """
        # STEP 1: Compute query brain state FIRST (needed for Wave-Routed Retrieval)
        query_theta = ThetaPhase.from_timestamp(
            datetime.now(timezone.utc),
            self._session_reference or datetime.now(timezone.utc),
        )
        query_memory_wave = self._wave_encoder.encode(query_text)
        query_amplitudes = query_memory_wave.amplitudes  # Extract WaveAmplitudes
        query_gamma = self._compute_gamma_bursts(query_amplitudes)

        # Convert WaveAmplitudes to dict for retriever
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

        # STEP 2: Neural Graph retrieval with Wave-Routed query amplitudes
        result = await self.neural_service.retrieve(
            query_text=query_text,
            query_embedding=query_embedding,
            session_key=session_key,
            limit=limit * 2,  # Get more for re-ranking
            query_wave_amplitudes=query_wave_dict,  # CRITICAL: Enable Wave-Routed Retrieval
        )

        if not result.nodes:
            return []

        # STEP 3: Apply brain-inspired boosting
        boosted_results = []

        for node, base_score in result.nodes:
            # Get memory's brain state
            state = self._memory_states.get(node.node_id)

            boost_breakdown = {
                "base_score": base_score,
                "theta_boost": 0.0,
                "gamma_boost": 0.0,
                "wave_boost": 0.0,
                "ltp_boost": 0.0,
            }

            if state:
                # Theta phase coherence boost
                phase_coherence = query_theta.phase_coherence(state.theta_phase)
                cycle_proximity = query_theta.cycle_proximity(state.theta_phase, max_cycles=150)
                theta_boost = self.theta_weight * (0.6 * phase_coherence + 0.4 * cycle_proximity)
                boost_breakdown["theta_boost"] = theta_boost

                # Gamma slot overlap boost
                query_slots = {g.slot for g in query_gamma}
                memory_slots = {g.slot for g in state.gamma_bursts}
                if query_slots:
                    overlap = len(query_slots & memory_slots) / len(query_slots)
                    gamma_boost = self.gamma_weight * overlap
                    boost_breakdown["gamma_boost"] = gamma_boost

                # Wave amplitude alignment boost
                if state.wave_amplitudes:
                    # Build query amplitude dict directly from WaveAmplitudes
                    query_dict = {
                        'temporal': query_amplitudes.temporal,
                        'entity': query_amplitudes.entity,
                        'relational': query_amplitudes.relational,
                        'action': query_amplitudes.action,
                        'state': query_amplitudes.state,
                        'spatial': query_amplitudes.spatial,
                        'causal': query_amplitudes.causal,
                        'emotional': query_amplitudes.emotional,
                        'quantitative': query_amplitudes.quantitative,
                    }
                    alignment = sum(
                        state.wave_amplitudes.get(k, 0) * query_dict.get(k, 0)
                        for k in query_dict.keys()
                    )
                    # Normalize by vector magnitudes
                    norm_q = math.sqrt(sum(v*v for v in query_dict.values()))
                    norm_m = math.sqrt(sum(v*v for v in state.wave_amplitudes.values()))
                    if norm_q > 0 and norm_m > 0:
                        alignment = alignment / (norm_q * norm_m)
                    wave_boost = self.wave_weight * max(0, alignment)
                    boost_breakdown["wave_boost"] = wave_boost

                # LTP/consolidation boost
                ltp_boost = self.ltp_weight * min(state.co_activation_count * 0.1, 0.15)
                boost_breakdown["ltp_boost"] = ltp_boost

            # Compute final score
            total_boost = sum([
                boost_breakdown["theta_boost"],
                boost_breakdown["gamma_boost"],
                boost_breakdown["wave_boost"],
                boost_breakdown["ltp_boost"],
            ])

            final_score = base_score + total_boost
            boost_breakdown["final_score"] = final_score
            boost_breakdown["total_boost"] = total_boost

            boosted_results.append((node, final_score, boost_breakdown))

        # Sort by final score
        boosted_results.sort(key=lambda x: x[1], reverse=True)

        # Record co-activation for top results (Hebbian learning)
        self._record_co_activation([r[0].node_id for r in boosted_results[:7]])

        return boosted_results[:limit]

    def _record_co_activation(self, node_ids: list[str]):
        """Record co-activation for Hebbian learning (LTP simulation)."""
        for node_id in node_ids:
            if node_id in self._memory_states:
                self._memory_states[node_id].co_activation_count += 1


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


def evaluate_llm_judge_sync(question: str, gold_answer: str, generated_answer: str) -> int:
    """Evaluate using LLM judge."""
    JUDGE_PROMPT = """You are a STRICT evaluator. Label the answer as 'CORRECT' or 'WRONG'.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

STRICT RULES:
1. If gold answer has MULTIPLE items (e.g., "A, B, C"), the generated answer must mention ALL of them to be CORRECT. Missing ANY item = WRONG.
2. If gold answer is a specific date/time, the generated answer must match that date. "Not mentioned" when there IS a gold answer = WRONG.
3. If gold answer says "Yes" or "No", the generated answer must convey the same meaning. Saying "Not mentioned" when the gold answer exists = WRONG.
4. For dates: different formats are OK (e.g., "May 7" vs "7 May 2023" vs "2023-05-07"), but the date must be CORRECT.
5. Partial answers are WRONG. If gold says "Running, pottery" and answer only says "Running" = WRONG.
6. "Not mentioned in memories" when the gold answer exists = WRONG.

Return JSON: {{"label": "CORRECT"}} or {{"label": "WRONG"}}"""

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": JUDGE_MODEL,
                "messages": [{"role": "user", "content": JUDGE_PROMPT.format(
                    question=question,
                    gold_answer=gold_answer,
                    generated_answer=generated_answer,
                )}],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0}
            },
            timeout=180
        )
        result = json.loads(response.json()["message"]["content"])
        return 1 if result.get("label") == "CORRECT" else 0
    except Exception as e:
        print(f"    Judge error: {e}", flush=True)
        return 0


async def judge_answer_async(session: aiohttp.ClientSession, question: str, gold_answer: str, generated_answer: str) -> dict:
    """Async wrapper for LLM judge."""
    loop = asyncio.get_event_loop()
    score = await loop.run_in_executor(None, evaluate_llm_judge_sync, question, gold_answer, generated_answer)
    return {"binary_correct": score}


# =============================================================================
# PROMPTS - Schema-guided with brain-inspired reasoning
# =============================================================================

SYSTEM_MESSAGE = """You are a memory retrieval system implementing hippocampal pattern completion.
Your task: Find and synthesize information from timestamped memories.
Answer based ONLY on what's in the memories. If not stated, say "Not mentioned in memories"."""

# Main schema-guided prompt (from neural graph prompts.py)
ANSWER_PROMPT = """You are a memory retrieval system. Your task is to find and synthesize information from memories.

REASONING PROCESS (do this mentally):
1. WHO is the question about? Identify the person/entity.
2. WHAT is being asked? Identify the topic/activity/relationship.
3. SCAN all memories for this person + topic combination.
4. CONNECT related memories - they may describe the same thing differently.
5. SYNTHESIZE a complete answer from ALL relevant memories.

CRITICAL:
- For "how does X do Y" questions, find ALL instances of X doing Y
- For preferences/habits, combine multiple examples
- Different memories may describe the SAME activity - recognize patterns

Memories:
{context}

Question: {question}

Answer (be complete - include all relevant details):"""

# Enhanced temporal prompt with explicit date handling
TEMPORAL_PROMPT = """You are a memory retrieval system specialized in temporal (time) questions.

CRITICAL FOR TEMPORAL QUESTIONS:
1. Look at the [YYYY-MM-DD HH:MM] timestamps on each memory
2. The timestamps show WHEN the conversation happened
3. Relative time references (e.g., "last Saturday", "yesterday", "last year") are relative to the MEMORY'S timestamp
4. Example: If memory timestamp is [2023-05-08] and content says "last Saturday", calculate backwards from May 8, 2023

{temporal_hints}

Memories:
{context}

Question: {question}

Answer (provide the specific date/time, showing your calculation if using relative references):"""

# Multi-hop prompt
MULTI_HOP_PROMPT = """You are a memory retrieval system for multi-hop reasoning.

This question requires connecting information across MULTIPLE memories.

REASONING:
1. Identify ALL entities in the question
2. Find memories about each entity
3. Find CONNECTIONS between those entities
4. Synthesize information from multiple memories

KEY: The answer is NOT in any single memory - you must COMBINE.

Memories:
{context}

Question: {question}

Answer (synthesize from multiple memories):"""

# Open domain prompt
OPEN_DOMAIN_PROMPT = """You are a memory retrieval system for inference questions.

This question asks for INFERENCE based on available information.

APPROACH:
1. Gather all memories with relevant clues
2. Look for patterns, themes, stated preferences
3. Make reasonable inferences from evidence
4. State inference with appropriate confidence

Memories:
{context}

Question: {question}

Answer (infer from the memories):"""


# =============================================================================
# DATA EXTRACTION
# =============================================================================

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def extract_messages(item) -> list[dict]:
    """Extract messages from conversation item."""
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1

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

    # Primary format: "X:XX am/pm on DD Month, YYYY"
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


def calculate_temporal_hints(question: str, results: list[tuple], reference_date: datetime) -> str:
    """Calculate temporal hints for temporal questions."""
    q_lower = question.lower()

    is_temporal = any(t in q_lower for t in ["when", "what date", "what day", "how long", "which year"])
    if not is_temporal:
        return ""

    calculator = TemporalCalculator()
    hints = []

    for item in results[:15]:
        if len(item) >= 2:
            node = item[0]
            content = node.content if hasattr(node, 'content') else ""
            timestamp = node.created_at if hasattr(node, 'created_at') else None

            if not timestamp:
                continue

            content_lower = content.lower()

            # Pattern: "the [weekday] before [date]"
            match = re.search(
                r"the\s+(\w+day)\s+before\s+(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4})",
                content_lower
            )
            if match:
                expr = match.group(0)
                result = calculator.calculate(expr, timestamp)
                if result.calculated_date:
                    date_str = result.calculated_date.strftime("%B %d, %Y")
                    hints.append(f"'{expr}' = {date_str}")

            # Pattern: "last [weekday]"
            match = re.search(r"last\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", content_lower)
            if match and timestamp:
                weekday = match.group(1)
                expr = f"last {weekday}"
                result = calculator.calculate(expr, timestamp)
                if result.calculated_date:
                    date_str = result.calculated_date.strftime("%B %d, %Y")
                    hints.append(f"'{expr}' (from {timestamp.strftime('%Y-%m-%d')}) = {date_str}")

    if hints:
        unique = list(dict.fromkeys(hints))
        return "\n\nCALCULATED DATES:\n" + "\n".join(f"- {h}" for h in unique[:5])

    return ""


def build_context_aware_system_message(decomposition: QueryDecomposition) -> str:
    """Build a context-aware system message using query decomposition.

    This is TRUE CONTEXT ENGINEERING - telling the LLM exactly what the query
    is asking for so it can focus on the right information.
    """
    parts = [
        "You are a memory retrieval system. Answer based ONLY on the memories provided.",
        ""
    ]

    # Tell the LLM what TYPE of question this is
    domain_guidance = {
        QueryDomain.TEMPORAL: "This is a TEMPORAL question - you need to find WHEN something happened. Pay close attention to timestamps [YYYY-MM-DD HH:MM] and relative time references (yesterday, last week, etc.).",
        QueryDomain.FACTUAL: "This is a FACTUAL question - you need to find a specific fact or piece of information about someone or something.",
        QueryDomain.RELATIONAL: "This is a RELATIONAL question - you need to find how entities are connected or related.",
        QueryDomain.SPATIAL: "This is a SPATIAL question - you need to find WHERE something happened or is located.",
        QueryDomain.CAUSAL: "This is a CAUSAL question - you need to find WHY something happened or what caused it.",
        QueryDomain.QUANTITATIVE: "This is a QUANTITATIVE question - you need to count or measure something.",
        QueryDomain.OPEN_DOMAIN: "This question requires inference from available memories.",
    }
    parts.append(f"QUESTION TYPE: {domain_guidance.get(decomposition.primary_domain, 'General retrieval')}")
    parts.append("")

    # Tell the LLM what entities to focus on
    if decomposition.subjects:
        parts.append(f"FOCUS ON: {', '.join(decomposition.subjects)}")

    # Tell the LLM what kind of answer is expected
    seeking_guidance = {
        "WHEN": "Answer with a specific DATE or TIME (e.g., 'May 7, 2023' or 'last Saturday before May 25')",
        "WHERE": "Answer with a specific PLACE or LOCATION",
        "WHO": "Answer with a specific PERSON's name",
        "WHAT": "Answer with the specific THING, ACTION, or FACT being asked about",
        "WHY": "Answer with the REASON or CAUSE",
        "HOW": "Answer with the METHOD or PROCESS",
    }
    if decomposition.seeking in seeking_guidance:
        parts.append(f"EXPECTED ANSWER: {seeking_guidance[decomposition.seeking]}")

    # Add temporal calculation guidance
    if decomposition.primary_domain == QueryDomain.TEMPORAL or decomposition.seeking == "WHEN":
        parts.append("")
        parts.append("TEMPORAL CALCULATION:")
        parts.append("- Memory timestamps [YYYY-MM-DD HH:MM] show when the conversation happened")
        parts.append("- Relative times ('last Saturday', 'yesterday') are relative to the memory's timestamp")
        parts.append("- Calculate backwards: if timestamp is [2023-05-08] and content says 'yesterday', the event was 2023-05-07")

    # Add action context if relevant
    if decomposition.actions:
        parts.append(f"LOOK FOR: memories about {', '.join(decomposition.actions)}")

    parts.append("")
    parts.append("If the information is not in the memories, say 'Not mentioned in memories'.")

    return "\n".join(parts)


def get_prompt_for_category(category_name: str, question: str = "") -> str:
    """Get appropriate prompt for category."""
    if category_name == "temporal":
        return TEMPORAL_PROMPT
    elif category_name == "multi_hop":
        return MULTI_HOP_PROMPT
    elif category_name == "open_domain":
        return OPEN_DOMAIN_PROMPT

    # Default - use query analysis
    q_lower = question.lower()
    if any(t in q_lower for t in ["when", "what date", "how long"]):
        return TEMPORAL_PROMPT
    elif any(t in q_lower for t in ["both", "together", "and", "compared"]):
        return MULTI_HOP_PROMPT
    elif any(t in q_lower for t in ["likely", "would", "could", "predict"]):
        return OPEN_DOMAIN_PROMPT

    return ANSWER_PROMPT


def format_context(results: list[tuple], max_chars: int = 12000) -> str:
    """Format retrieval results as context."""
    lines = []
    total_chars = 0

    for item in results:
        node = item[0]
        timestamp = node.created_at.strftime("%Y-%m-%d %H:%M") if node.created_at else "Unknown"
        speaker = node.metadata.get("producer_id", node.metadata.get("speaker", "Unknown"))
        line = f"[{timestamp}] {speaker}: {node.content}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


# =============================================================================
# EPISODE-LIKE CLASS FOR NEURAL GRAPH
# =============================================================================

def extract_entities_simple(text: str) -> list[str]:
    """Extract entity names from text using simple heuristics.

    Finds capitalized words that are likely proper nouns (names, places).
    This enables ENTITY edge creation for horizontal linking.
    """
    # Find capitalized words (likely proper nouns)
    words = re.findall(r'\b[A-Z][a-z]+\b', text)
    # Remove common words that are capitalized at sentence start
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
    """Minimal Episode-like object for neural graph ingestion."""
    def __init__(self, content: str, uid: str, speaker: str, created_at: datetime | None, session_key: str, msg_idx: int):
        self.content = content
        self.uid = uid
        self.producer_id = speaker
        self.created_at = created_at

        # Extract entities for HORIZONTAL LINKING
        entity_ids = extract_entities_simple(content)
        # Also add speaker as an entity for person-based linking
        if speaker and speaker not in ['Unknown', 'user', 'assistant']:
            entity_ids.append(speaker)

        self.filterable_metadata = {
            "speaker": speaker,
            "session_key": session_key,
            "msg_idx": msg_idx,
            "entity_ids": entity_ids,  # CRITICAL: Enable ENTITY edge creation
        }
        self.importance_score = 0.5


# =============================================================================
# PROCESS QUESTION
# =============================================================================

async def process_question_hybrid(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    hybrid_retriever: HybridRetriever,
    session_key: str,
    reference_date: datetime | None,
) -> dict:
    """Process a question using hybrid retrieval."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # Get query embedding
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

    # =========================================================================
    # CONTEXT ENGINEERING: Decompose query to understand what's being asked
    # =========================================================================
    query_decomposition = classify_query(question)

    # Hybrid retrieval with brain-inspired boosting
    retrieval_start = time.time()
    results = await hybrid_retriever.retrieve_with_boosting(
        query_text=question,
        query_embedding=query_embedding,
        session_key=session_key,
        limit=TOP_K_RETRIEVAL,
    )
    retrieval_time = time.time() - retrieval_start

    # Format context
    context = format_context(results, max_chars=CONTEXT_LIMIT)

    # Calculate temporal hints for temporal questions
    temporal_hints = ""
    if category_name == "temporal" or "when" in question.lower():
        temporal_hints = calculate_temporal_hints(question, results, reference_date)

    # Get prompt
    prompt_template = get_prompt_for_category(category_name, question)

    if "{temporal_hints}" in prompt_template:
        prompt = prompt_template.format(
            context=context,
            question=question,
            temporal_hints=temporal_hints,
        )
    else:
        if temporal_hints:
            context = context + temporal_hints
        prompt = prompt_template.format(context=context, question=question)

    # =========================================================================
    # CONTEXT ENGINEERING: Build context-aware system message
    # =========================================================================
    context_aware_system_msg = build_context_aware_system_message(query_decomposition)

    # Generate answer with context-aware guidance
    generated = await generate_answer_async(http_session, prompt, context_aware_system_msg)

    # Judge
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)
    binary_correct = judgment.get("binary_correct", 0)

    gen_time = time.time() - start_time
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    # Log boost stats for first few
    avg_boost = 0
    if results and len(results[0]) >= 3:
        boosts = [r[2].get("total_boost", 0) for r in results[:5]]
        avg_boost = sum(boosts) / len(boosts) if boosts else 0

    print(f"    [{q_idx + 1}] {category_name}: {status} (boost: {avg_boost:.3f})", flush=True)

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
        "retrieval_method": "hybrid_neural_brain",
        "avg_boost": round(avg_boost, 4),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_hybrid_benchmark(max_conversations: int = 10):
    """Run Hybrid Benchmark with Neural Graph + Brain-Inspired boosting."""
    print("=" * 70)
    print("LoCoMo Hybrid Benchmark v1.0")
    print("Neural Graph + Theta-Gamma Coupling + Wave Resonance")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")
    print(f"Brain Weights: theta={THETA_WEIGHT}, gamma={GAMMA_WEIGHT}, wave={WAVE_WEIGHT}, LTP={LTP_WEIGHT}")
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

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(conversations):
            conv_id = item.get("conversation_id", conv_idx)
            messages = extract_messages(item)

            speakers = list(set(m["speaker"] for m in messages))
            speaker_str = " & ".join(speakers[:2]) if speakers else f"Conv {conv_id}"

            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {speaker_str}")

            if not messages:
                continue

            # Get reference date
            reference_date = datetime.now(timezone.utc)
            for msg in reversed(messages):
                parsed = parse_session_datetime(msg.get("session_time", ""))
                if parsed:
                    reference_date = parsed
                    break

            # Create Neural Graph Service
            session_key = f"conv_{conv_id}"
            config = NeuralGraphServiceConfig(
                enabled=True,
                hierarchy_enabled=True,
                temporal_enabled=True,
                consolidation_enabled=True,
                auto_temporal_linking=True,
                auto_episode_segmentation=False,
                auto_consolidation_on_ingestion=False,
                apply_temporal_dynamics=True,
            )
            neural_service = NeuralGraphService(config=config)

            # Create Hybrid Retriever
            hybrid_retriever = HybridRetriever(neural_service)
            hybrid_retriever.set_reference_time(reference_date)

            # Ingest messages
            print(f"  Ingesting {len(messages)} messages...")
            embeddings = []
            for msg in messages:
                emb = await get_embedding_async(http_session, msg["text"])
                embeddings.append(emb)

            episodes = []
            for i, msg in enumerate(messages):
                parsed_time = parse_session_datetime(msg.get("session_time", ""))
                episode = EpisodeLike(
                    content=msg["text"],
                    uid=f"{session_key}_msg_{i}",
                    speaker=msg["speaker"],
                    created_at=parsed_time,
                    session_key=session_key,
                    msg_idx=i,
                )
                episodes.append(episode)

            # Ingest into neural graph
            result = await neural_service.process_episodes(
                episodes=episodes,
                embeddings=embeddings,
                session_key=session_key,
            )

            # Get the actual created nodes from storage to get real node IDs
            actual_nodes = await neural_service._storage.get_nodes_by_session(session_key)

            # Register brain states using the ACTUAL node IDs (not generated ones)
            for node in actual_nodes:
                hybrid_retriever.register_memory(
                    node_id=node.node_id,  # Use actual UUID from neural graph
                    content=node.content,
                    timestamp=node.created_at,
                )

            print(f"  Ingested {result.nodes_created} nodes, registered {len(actual_nodes)} brain states")

            # Process questions
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions...")

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_hybrid(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        hybrid_retriever=hybrid_retriever,
                        session_key=session_key,
                        reference_date=reference_date,
                    )
                    for i, qa in enumerate(batch)
                ]

                results = await asyncio.gather(*tasks)
                for r in results:
                    r["conversation_id"] = conv_id
                    all_results.append(r)

                # Save incrementally after each batch
                _save_incremental(all_results, results_dir)

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - Hybrid Neural Graph + Brain-Inspired Retrieval")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    print(f"\nOverall: {correct}/{total} ({100*correct/total:.2f}%)")

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
    results_file = results_dir / f"locomo10_HYBRID_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "HYBRID_V1.0",
            "retrieval_method": "neural_graph_plus_brain_inspired_boosting",
            "answer_model": OLLAMA_MODEL,
            "judge_model": JUDGE_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "brain_weights": {
                "theta": THETA_WEIGHT,
                "gamma": GAMMA_WEIGHT,
                "wave": WAVE_WEIGHT,
                "ltp": LTP_WEIGHT,
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_hybrid_benchmark(args.max_conversations))

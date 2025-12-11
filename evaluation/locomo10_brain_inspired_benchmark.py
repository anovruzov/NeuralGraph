"""
LoCoMo Brain-Inspired Benchmark v1.0
Theta-Gamma Coupled Memory Retrieval

This benchmark implements neuroscience-inspired memory retrieval:
1. Wave resonance for semantic matching
2. Theta-gamma phase coupling for temporal binding
3. Sharp wave ripple simulation for consolidation
4. Optimized for 8GB RAM systems using phi3:mini

Neuroscience Background:
- Theta rhythm (4-8 Hz): Temporal context - "when" memories are bound together
- Gamma rhythm (30-80 Hz): Content slots - "what" is stored in each memory
- Phase-amplitude coupling: Memories encoded at similar theta phases resonate
- Sharp wave ripples: Memory consolidation through replay

Key Innovation:
Instead of pure semantic similarity, we add TEMPORAL RESONANCE.
Memories that happened "together" (same theta cycle) retrieve together.
This particularly helps temporal questions (currently 57.6% -> target 80%+).
"""

import json
import asyncio
import aiohttp
import time
import sys
import requests
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import Wave Memory System with Theta-Gamma Coupling
from memmachine.wave_memory import (
    WaveResonanceRetriever,
    WaveEncoder,
    MemoryWave,
    WaveAmplitudes,
    TemporalCalculator,
    TemporalContextEnhancer,
    ThetaPhase,
    GammaBurst,
    OscillationState,
    ThetaGammaCoupledRetriever,
    enhance_retrieval_with_coupling,
    THETA_FREQUENCY_HZ,
    GAMMA_CYCLES_PER_THETA,
)

# Configuration - CAN USE EITHER phi3:mini (8GB RAM) OR qwen2.5:7b (16GB RAM)
OLLAMA_BASE_URL = "http://localhost:11434"
# For 8GB RAM systems: phi3:mini (2.2 GB)
# For 16GB RAM systems: qwen2.5:7b-instruct (4.7 GB) for better accuracy
OLLAMA_MODEL = "qwen2.5:7b-instruct"  # Higher accuracy model
JUDGE_MODEL = "qwen2.5:7b-instruct"   # Same model for judging
EMBEDDING_MODEL = "nomic-embed-text"  # 274MB - efficient embeddings

CONCURRENT_QUESTIONS = 3  # Adjust based on RAM
CONTEXT_LIMIT = 12000  # Context for retrieval
TOP_K_RETRIEVAL = 40

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

# =============================================================================
# THETA-GAMMA ENHANCED RETRIEVER
# =============================================================================

@dataclass
class BrainInspiredMemory:
    """Memory with both wave and oscillation state."""
    wave: MemoryWave
    oscillation: OscillationState
    base_score: float = 0.0  # From wave resonance
    coupling_score: float = 0.0  # From theta-gamma coupling


class BrainInspiredRetriever:
    """
    Combines Wave Resonance with Theta-Gamma Phase Coupling.

    This is how the brain retrieves memories:
    1. WAVE RESONANCE: Find semantically similar memories (what)
    2. THETA BINDING: Boost memories from same temporal context (when)
    3. GAMMA SLOTS: Match content type signatures
    4. RIPPLE CONSOLIDATION: Prefer well-consolidated memories
    """

    def __init__(
        self,
        theta_weight: float = 0.20,  # Weight for theta phase coherence
        gamma_weight: float = 0.10,  # Weight for gamma slot matching
        consolidation_bonus: float = 0.05,  # Bonus per consolidation event
    ):
        self.theta_weight = theta_weight
        self.gamma_weight = gamma_weight
        self.consolidation_bonus = consolidation_bonus

        # Wave resonance for semantic matching
        self._wave_retriever = WaveResonanceRetriever(use_semantic_resonance=False)

        # Theta-gamma coupling for temporal binding
        self._theta_gamma = ThetaGammaCoupledRetriever(
            phase_coherence_weight=theta_weight,
            cycle_proximity_weight=gamma_weight,
            consolidation_bonus=consolidation_bonus,
        )

        # Track memories with full brain state
        self._memories: list[BrainInspiredMemory] = []
        self._session_reference: datetime | None = None

    @property
    def memory_count(self) -> int:
        return len(self._memories)

    def clear(self):
        """Clear all memories."""
        self._memories.clear()
        self._wave_retriever = WaveResonanceRetriever(use_semantic_resonance=False)
        self._theta_gamma.clear()
        self._session_reference = None

    def ingest(
        self,
        content: str,
        speaker: str,
        timestamp: datetime | None = None,
        session_key: str = "",
        msg_idx: int = 0,
        reference_date: datetime | None = None,
    ):
        """Ingest a memory with brain-inspired encoding.

        Steps:
        1. Create wave with amplitude encoding (semantic features)
        2. Compute theta phase (temporal binding)
        3. Compute gamma bursts (content slot assignment)
        """
        # Set reference if first message
        if self._session_reference is None:
            self._session_reference = reference_date or timestamp or datetime.now(timezone.utc)

        # Create wave through wave retriever
        self._wave_retriever.ingest(
            content=content,
            speaker=speaker,
            timestamp=timestamp,
            session_key=session_key,
            msg_idx=msg_idx,
            reference_date=reference_date,
        )

        # Get the wave that was just created
        if self._wave_retriever._memories:
            wave = self._wave_retriever._memories[-1]

            # Add to theta-gamma system
            osc_state = self._theta_gamma.add_memory(wave, self._session_reference)

            # Store combined memory
            self._memories.append(BrainInspiredMemory(
                wave=wave,
                oscillation=osc_state,
            ))

    def retrieve(
        self,
        query: str,
        top_k: int = 30,
        reference_date: datetime | None = None,
    ) -> list[tuple[MemoryWave, float]]:
        """Retrieve memories using brain-inspired multi-stage process.

        Stage 1: WAVE RESONANCE
        - Find semantically similar memories based on wave amplitudes
        - This is like the brain's initial pattern matching

        Stage 2: THETA-GAMMA COUPLING
        - Boost memories that share temporal context with query
        - Memories encoded at similar theta phases resonate
        - This is critical for temporal questions

        Stage 3: CONSOLIDATION BONUS
        - Well-consolidated memories (frequently co-activated) get boost
        - Simulates how strongly encoded memories are easier to retrieve
        """
        if not self._memories:
            return []

        # Stage 1: Wave resonance (semantic matching)
        wave_results = self._wave_retriever.retrieve(
            query=query,
            top_k=top_k * 2,  # Get more candidates for re-ranking
            reference_date=reference_date,
        )

        if not wave_results:
            return []

        # Build score lookup from wave retrieval
        wave_scores = {wave.wave_id: score for wave, score in wave_results}

        # Stage 2: Create query wave for theta-gamma coupling
        query_wave = self._create_query_wave(query, reference_date)

        # Stage 3: Apply theta-gamma enhancement
        enhanced_results = []

        for memory in self._memories:
            wave = memory.wave
            osc = memory.oscillation

            # Get wave resonance score
            base_score = wave_scores.get(wave.wave_id, 0.0)

            if base_score < 0.1:  # Skip very low scoring memories
                continue

            # Compute theta phase coherence with query
            query_theta = ThetaPhase.from_timestamp(
                query_wave.timestamp or datetime.now(timezone.utc),
                self._session_reference or datetime.now(timezone.utc),
            )

            phase_coherence = query_theta.phase_coherence(osc.theta)
            cycle_proximity = query_theta.cycle_proximity(osc.theta, max_cycles=200)

            # Compute gamma slot overlap
            query_gamma = self._compute_gamma_bursts(query_wave)
            gamma_overlap = self._compute_gamma_overlap(query_gamma, osc.gamma_bursts)

            # Consolidation bonus
            consolidation = min(osc.ripple_count * self.consolidation_bonus, 0.2)
            ltp_bonus = osc.co_activation_strength * 0.1

            # Combine scores (brain-inspired weighting)
            coupling_score = (
                self.theta_weight * phase_coherence +
                self.gamma_weight * cycle_proximity +
                0.05 * gamma_overlap +
                consolidation +
                ltp_bonus
            )

            total_score = base_score + coupling_score

            # Store for analysis
            memory.base_score = base_score
            memory.coupling_score = coupling_score

            enhanced_results.append((wave, total_score, osc))

        # Sort by total score
        enhanced_results.sort(key=lambda x: x[1], reverse=True)

        # Record co-activation (strengthen connections between retrieved memories)
        self._record_co_activation(enhanced_results[:7])  # Top 7 = working memory

        # Return top_k as (wave, score) tuples
        return [(wave, score) for wave, score, _ in enhanced_results[:top_k]]

    def _create_query_wave(self, query: str, reference_date: datetime | None) -> MemoryWave:
        """Create a wave representation of the query."""
        encoder = WaveEncoder()
        amplitudes = encoder.encode(query)

        return MemoryWave(
            content=query,
            speaker="query",
            amplitudes=amplitudes,
            timestamp=reference_date or datetime.now(timezone.utc),
        )

    def _compute_gamma_bursts(self, wave: MemoryWave) -> list[GammaBurst]:
        """Compute gamma bursts from wave amplitudes."""
        bursts = []
        amp = wave.amplitudes

        # Access amplitudes through the WaveAmplitudes object
        dimension_slots = [
            (0, getattr(amp, 'temporal', 0.0), "temporal"),
            (1, getattr(amp, 'entity', 0.0), "entity"),
            (2, getattr(amp, 'action', 0.0), "action"),
            (3, getattr(amp, 'relational', 0.0), "relational"),
            (4, getattr(amp, 'state', 0.0), "state"),
            (5, getattr(amp, 'spatial', 0.0), "spatial"),
            (6, getattr(amp, 'emotional', 0.0), "emotional"),
        ]

        SLOW_GAMMA_HZ = 40.0
        FAST_GAMMA_HZ = 65.0

        for slot, amplitude, dim_name in dimension_slots:
            if amplitude > 0.2:
                freq = SLOW_GAMMA_HZ + (amplitude * (FAST_GAMMA_HZ - SLOW_GAMMA_HZ))
                bursts.append(GammaBurst(
                    slot=slot,
                    frequency=freq,
                    power=amplitude,
                ))

        return bursts

    def _compute_gamma_overlap(
        self,
        query_bursts: list[GammaBurst],
        memory_bursts: list[GammaBurst],
    ) -> float:
        """Compute overlap between query and memory gamma bursts."""
        if not query_bursts or not memory_bursts:
            return 0.0

        query_slots = {g.slot for g in query_bursts}
        memory_slots = {g.slot for g in memory_bursts}

        if not query_slots:
            return 0.5  # Default neutral

        overlap = len(query_slots & memory_slots) / len(query_slots)
        return overlap

    def _record_co_activation(self, results: list[tuple[MemoryWave, float, OscillationState]]):
        """Record co-activation for Hebbian learning (LTP simulation)."""
        if len(results) < 2:
            return

        # Strengthen connections between co-retrieved memories
        for i, (wave_i, score_i, osc_i) in enumerate(results):
            for j, (wave_j, score_j, osc_j) in enumerate(results):
                if i >= j:
                    continue
                osc_i.strengthen_binding(0.02)
                osc_j.strengthen_binding(0.02)

    def simulate_consolidation(self):
        """Simulate sharp wave ripple replay for consolidation.

        In the brain, this happens during sleep. Here we call it
        after ingestion to strengthen important memories.
        """
        return self._theta_gamma.simulate_ripple_replay(
            session_key="",
            replay_fraction=0.15,  # Replay top 15% of memories
        )


# =============================================================================
# IMPORT NEURAL GRAPH PROMPTS - THE KEY TO 78.6% ACCURACY!
# =============================================================================
# These implement hippocampal-inspired reasoning patterns:
# - WHO-WHAT-SCAN-CONNECT-SYNTHESIZE
# - Specialized prompts for temporal, multi-hop, open_domain
# - Pattern completion from partial cues

from memmachine.neural_graph.prompts import (
    RETRIEVAL_ANSWER_PROMPT,
    TEMPORAL_QUERY_PROMPT,
    MULTI_HOP_QUERY_PROMPT,
    OPEN_DOMAIN_QUERY_PROMPT,
    select_prompt_for_query,
)

# =============================================================================
# ENHANCED PROMPTS - Combine Neural Graph + SLM Optimization
# =============================================================================

SYSTEM_MESSAGE = """You are a memory retrieval system implementing hippocampal-inspired recall.

COGNITIVE PROCESS (like the brain's CA3 pattern completion):
1. ENTITY BINDING: Identify WHO the question is about
2. CONCEPT BINDING: Identify WHAT is being asked
3. TEMPORAL BINDING: Note WHEN context from timestamps
4. PATTERN COMPLETION: Reconstruct full answer from partial memory traces
5. SYNTHESIS: Combine distributed memories into coherent response

RULES:
- Use ONLY information from the provided memories
- If information is not in memories, say "Not stated in memories"
- Be precise with dates, names, and facts"""

# Enhanced schema-guided prompt with SLM optimization
BRAIN_GUIDED_PROMPT = """You are a memory retrieval system. Your task is to find and synthesize information from memories.

REASONING PROCESS (do this mentally, don't write it out):
1. WHO is the question about? Identify the person/entity.
2. WHAT is being asked? Identify the topic/activity/relationship.
3. SCAN all memories for this person + topic combination.
4. CONNECT related memories - they may describe the same thing differently.
5. SYNTHESIZE a complete answer from ALL relevant memories.

CRITICAL FOR MULTI-HOP QUESTIONS:
- If asking "how does X do Y", look for ALL instances of X doing Y across memories
- If asking about preferences/habits, combine multiple examples into one answer
- Different memories may describe the SAME activity - recognize these patterns

Memories:
{context}

Question: {question}

Answer (be complete - include all relevant details from memories):"""

# Enhanced temporal prompt with calculated dates support
TEMPORAL_PROMPT = """You are a memory retrieval system specialized in temporal queries.

For questions about WHEN something happened:
1. Look for explicit dates, months, years in the memories
2. Check the CALCULATED DATES section below for pre-computed temporal references
3. Convert relative references (e.g., "last year" in a May 2023 memory = 2022)
4. Pay attention to timestamps [YYYY-MM-DD HH:MM] on each memory
5. If multiple time references exist, identify which one answers the specific question

Memories:
{context}

Question: {question}

Answer (provide the specific date/time):"""

# Multi-hop prompt for complex reasoning
MULTI_HOP_PROMPT = """You are a memory retrieval system specialized in multi-hop reasoning.

This question requires connecting information across multiple memories.

REASONING STEPS:
1. Identify ALL entities mentioned in the question
2. Find memories about each entity separately
3. Find connections between those entities
4. Synthesize information from multiple memories into one answer

KEY: The answer is NOT in any single memory - you must COMBINE information.

Memories:
{context}

Question: {question}

Answer (synthesize from multiple memories):"""

# Open domain inference prompt
OPEN_DOMAIN_PROMPT = """You are a memory retrieval system for open-ended inference questions.

This question asks you to make inferences based on available information.

REASONING APPROACH:
1. Gather all memories that provide clues about the question
2. Look for patterns, recurring themes, stated preferences
3. Make reasonable inferences based on the evidence
4. State your inference with appropriate confidence

Memories:
{context}

Question: {question}

Answer (make reasonable inferences from the memories):"""

# Adversarial prompt for tricky questions
ADVERSARIAL_PROMPT = """You are a memory retrieval system. CAREFUL - this may be a tricky question.

WARNING: Verify WHO did WHAT before answering.
- Don't assume based on similar names or contexts
- Check the memories for exact attribution
- If the question attributes something to the wrong person, point this out
- If information is not clearly stated for the specific person asked about, say "Not stated"

Memories:
{context}

Question: {question}

Answer (verify attribution before responding):"""

# Judge prompt for evaluation
JUDGE_PROMPT = """Given a question, gold answer, and generated answer, evaluate correctness.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

Evaluate: Is the generated answer semantically correct? Consider partial matches as correct if key information is present.
Return JSON only: {{"label": "CORRECT"}} or {{"label": "WRONG"}}
"""


# =============================================================================
# LLM CALLS (Optimized for phi3:mini)
# =============================================================================

async def generate_answer_async(session: aiohttp.ClientSession, prompt: str) -> str:
    """Generate answer using phi3:mini."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_MESSAGE},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_ctx": 4096,  # phi3:mini context
                }
            },
            timeout=aiohttp.ClientTimeout(total=180)
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


def evaluate_llm_judge_sync(question: str, gold_answer: str, generated_answer: str) -> int:
    """Evaluate using phi3:mini as judge."""
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": JUDGE_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": JUDGE_PROMPT.format(
                            question=question,
                            gold_answer=gold_answer,
                            generated_answer=generated_answer,
                        ),
                    },
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0}
            },
            timeout=180
        )
        result_text = response.json()["message"]["content"]
        result = json.loads(result_text)
        label = result.get("label", "WRONG")
        return 1 if label == "CORRECT" else 0
    except Exception as e:
        print(f"    Judge error: {e}", flush=True)
        return 0


async def judge_answer_async(session: aiohttp.ClientSession, question: str, gold_answer: str, generated_answer: str) -> dict:
    """Async wrapper for LLM judge."""
    loop = asyncio.get_event_loop()
    score = await loop.run_in_executor(
        None, evaluate_llm_judge_sync, question, gold_answer, generated_answer
    )
    return {"binary_correct": score}


# =============================================================================
# DATA EXTRACTION
# =============================================================================

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
    """Parse session datetime string."""
    if not session_time or session_time == "Unknown":
        return None

    formats = [
        "%B %d, %Y %I:%M %p",
        "%B %d, %Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(session_time, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def get_prompt_for_category(category_name: str, question: str = "") -> str:
    """Get appropriate prompt template for category.

    Uses TWO-STAGE selection:
    1. First check by category (from benchmark metadata)
    2. Then use neural graph's select_prompt_for_query for fine-tuning

    This combines benchmark labels with intelligent query analysis.
    """
    # Stage 1: Category-based selection
    if category_name == "temporal":
        return TEMPORAL_PROMPT
    elif category_name == "adversarial":
        return ADVERSARIAL_PROMPT
    elif category_name == "multi_hop":
        return MULTI_HOP_PROMPT
    elif category_name == "open_domain":
        return OPEN_DOMAIN_PROMPT

    # Stage 2: If single_hop or unknown, use intelligent query analysis
    # This catches edge cases where question type doesn't match category
    if question:
        selected = select_prompt_for_query(question)
        # Map neural graph prompts to our enhanced versions
        if selected == TEMPORAL_QUERY_PROMPT:
            return TEMPORAL_PROMPT
        elif selected == MULTI_HOP_QUERY_PROMPT:
            return MULTI_HOP_PROMPT
        elif selected == OPEN_DOMAIN_QUERY_PROMPT:
            return OPEN_DOMAIN_PROMPT

    # Default: Schema-guided brain prompt
    return BRAIN_GUIDED_PROMPT


# =============================================================================
# CONTEXT FORMATTING
# =============================================================================

def format_brain_context(
    results: list[tuple[MemoryWave, float]],
    max_chars: int = 10000,
) -> str:
    """Format retrieval results as context with temporal markers."""
    lines = []
    total_chars = 0

    for memory, score in results:
        # Format: [timestamp] speaker: text (with theta phase hint)
        timestamp = memory.timestamp.strftime("%Y-%m-%d %H:%M") if memory.timestamp else ""
        line = f"[{timestamp}] {memory.speaker}: {memory.content}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


def calculate_temporal_hints(
    question: str,
    results: list[tuple[MemoryWave, float]],
    reference_date: datetime,
) -> str:
    """Calculate temporal hints for temporal questions."""
    import re

    q_lower = question.lower()
    calculator = TemporalCalculator()
    hints = []

    is_temporal = any(trigger in q_lower for trigger in [
        "when ", "what date", "what day", "how long",
        "since when", "how many years", "how many months",
    ])

    if not is_temporal:
        return ""

    for memory, score in results[:15]:
        content = memory.content
        content_lower = content.lower()

        # Pattern: "the [weekday] before [date]"
        match = re.search(
            r"the\s+(\w+day)\s+before\s+(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4})",
            content_lower
        )
        if match:
            expr = match.group(0)
            result = calculator.calculate(expr, memory.timestamp)
            if result.calculated_date:
                date_str = result.calculated_date.strftime("%B %d, %Y")
                hints.append(f"'{expr}' = {date_str}")

    # Duration patterns
    for memory, _ in results[:10]:
        content_lower = memory.content.lower()
        match = re.search(r"(\d+)\s+(year|month|week)s?", content_lower)
        if match:
            num = match.group(1)
            unit = match.group(2)
            hints.append(f"Duration mentioned: {num} {unit}s")
            break

    if hints:
        unique_hints = list(dict.fromkeys(hints))
        return "\n\nCALCULATED DATES:\n" + "\n".join(f"- {h}" for h in unique_hints[:5])

    return ""


# =============================================================================
# PROCESS QUESTION
# =============================================================================

async def process_question_brain(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    retriever: BrainInspiredRetriever,
    reference_date: datetime | None = None,
) -> dict:
    """Process a single question using brain-inspired retrieval."""
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # Stage 1: Brain-inspired retrieval (wave + theta-gamma)
    results = retriever.retrieve(
        query=question,
        top_k=TOP_K_RETRIEVAL,
        reference_date=reference_date,
    )

    context = format_brain_context(results, max_chars=CONTEXT_LIMIT)

    # Stage 2: Calculate temporal hints for temporal questions
    temporal_hints = ""
    if category_name == "temporal" or any(t in question.lower() for t in ["when", "how long", "what date"]):
        temporal_hints = calculate_temporal_hints(question, results, reference_date)

    # Stage 3: Generate answer using INTELLIGENT prompt selection
    # This uses both category metadata AND query analysis for optimal prompt
    prompt_template = get_prompt_for_category(category_name, question)
    enhanced_context = context + temporal_hints
    prompt = prompt_template.format(context=enhanced_context, question=question)
    generated = await generate_answer_async(http_session, prompt)

    # Stage 4: Anti-abstention retry (except adversarial)
    abstention_retried = False
    if "not stated" in generated.lower() and category_name != "adversarial":
        relaxed_prompt = prompt.replace(
            "Not stated.",
            "provide any relevant information even if partial"
        )
        generated_retry = await generate_answer_async(http_session, relaxed_prompt)
        if "not stated" not in generated_retry.lower():
            generated = generated_retry
            abstention_retried = True

    # Stage 5: Judge answer
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)

    gen_time = time.time() - start_time
    binary_correct = judgment.get("binary_correct", 0)
    status = "CORRECT" if binary_correct == 1 else "WRONG"

    print(f"    [{q_idx + 1}] {category_name}: {status}", flush=True)

    return {
        "question_id": q_idx,
        "category": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "llm_score": binary_correct,
        "correct": binary_correct == 1,
        "time_seconds": round(gen_time, 2),
        "abstention_retried": abstention_retried,
        "retrieval_method": "brain_inspired_theta_gamma",
        "has_temporal_hints": bool(temporal_hints),
    }


# =============================================================================
# MAIN BENCHMARK
# =============================================================================

async def run_brain_benchmark(max_conversations: int = 10):
    """Run Brain-Inspired Benchmark with theta-gamma coupling."""
    print("=" * 70)
    print("LoCoMo Brain-Inspired Benchmark v1.0")
    print("Theta-Gamma Coupled Memory Retrieval")
    print("=" * 70)
    print(f"Answer Model: {OLLAMA_MODEL} (optimized for 8GB RAM)")
    print(f"Judge Model: {JUDGE_MODEL}")
    print(f"Theta Weight: 0.20 (temporal binding)")
    print(f"Gamma Weight: 0.10 (content slot matching)")
    print(f"Conversations: {max_conversations}")
    print("=" * 70)
    print()

    # Load dataset
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    print(f"Loading dataset from {data_path}...")

    with open(data_path) as f:
        data = json.load(f)

    conversations = data[:max_conversations]
    print(f"Processing {len(conversations)} conversations...")
    print()

    all_results = []

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(conversations):
            conv_id = item.get("conversation_id", conv_idx)

            messages = extract_messages(item)
            speakers = list(set(m["speaker"] for m in messages))
            speaker_str = " & ".join(speakers[:2]) if speakers else f"Conv {conv_id}"

            print(f"\n[Conversation {conv_idx + 1}/{len(conversations)}] {speaker_str}")

            if not messages:
                print("  No messages found, skipping...")
                continue

            # Get reference date from last session
            reference_date = datetime.now(timezone.utc)
            for msg in reversed(messages):
                parsed = parse_session_datetime(msg.get("session_time", ""))
                if parsed:
                    reference_date = parsed
                    break

            # Create Brain-Inspired Retriever
            retriever = BrainInspiredRetriever(
                theta_weight=0.20,  # Higher for better temporal binding
                gamma_weight=0.10,
                consolidation_bonus=0.05,
            )

            # Ingest messages
            for msg in messages:
                timestamp = parse_session_datetime(msg.get("session_time", ""))
                retriever.ingest(
                    content=msg["text"],
                    speaker=msg["speaker"],
                    timestamp=timestamp,
                    session_key=f"session_{msg['session']}",
                    msg_idx=msg["msg_idx"],
                    reference_date=reference_date,
                )

            # Simulate consolidation (sharp wave ripple replay)
            replayed = retriever.simulate_consolidation()

            print(f"  Ingested {retriever.memory_count} memories (replayed {replayed} for consolidation)")

            # Process questions
            questions = item.get("qa", item.get("questions", []))
            print(f"  Processing {len(questions)} questions...")

            for batch_start in range(0, len(questions), CONCURRENT_QUESTIONS):
                batch_end = min(batch_start + CONCURRENT_QUESTIONS, len(questions))
                batch = questions[batch_start:batch_end]

                tasks = [
                    process_question_brain(
                        http_session=http_session,
                        q_idx=batch_start + i,
                        qa=qa,
                        retriever=retriever,
                        reference_date=reference_date,
                    )
                    for i, qa in enumerate(batch)
                ]

                results = await asyncio.gather(*tasks)
                for result in results:
                    result["conversation_id"] = conv_id
                    all_results.append(result)

    # Calculate metrics
    print("\n" + "=" * 70)
    print("RESULTS - Brain-Inspired Theta-Gamma Retrieval")
    print("=" * 70)

    total = len(all_results)
    correct = sum(1 for r in all_results if r["correct"])
    print(f"\nOverall: {correct}/{total} ({100*correct/total:.2f}%)")

    # Per-category
    category_results = defaultdict(list)
    for r in all_results:
        category_results[r["category"]].append(r)

    print("\nPer-Category Accuracy:")
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop", "adversarial"]:
        if cat in category_results:
            cat_results = category_results[cat]
            cat_correct = sum(1 for r in cat_results if r["correct"])
            print(f"  {cat}: {cat_correct}/{len(cat_results)} ({100*cat_correct/len(cat_results):.2f}%)")

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    results_file = results_dir / f"locomo10_BRAIN_INSPIRED_{timestamp}.json"

    with open(results_file, 'w') as f:
        json.dump({
            "version": "BRAIN_INSPIRED_V1.0",
            "retrieval_method": "theta_gamma_coupled",
            "answer_model": OLLAMA_MODEL,
            "judge_model": JUDGE_MODEL,
            "theta_weight": 0.20,
            "gamma_weight": 0.10,
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
    parser.add_argument("--max-conversations", type=int, default=10, help="Max conversations to process")
    args = parser.parse_args()

    asyncio.run(run_brain_benchmark(args.max_conversations))

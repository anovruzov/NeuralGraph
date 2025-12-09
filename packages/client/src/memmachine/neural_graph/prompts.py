"""Schema-Guided Retrieval Prompts for Neural Memory Graph.

These prompts implement neuroscience-inspired reasoning patterns for memory retrieval:

Neuroscience Background:
- The hippocampus performs ASSOCIATIVE BINDING - connecting WHO-DOES-WHAT-WHY
- CA3 region performs PATTERN COMPLETION - reconstructing full memories from partial cues
- Prefrontal cortex provides TOP-DOWN control over memory search

The prompts guide the LLM to perform these same cognitive operations:
1. Identify the ENTITY (who) being queried
2. Identify the CONCEPT (what) being asked about
3. SCAN all memories for entity + concept combinations
4. CONNECT related memories (recognize patterns)
5. SYNTHESIZE a complete answer from distributed traces

This approach improved benchmark accuracy from 57% to 81% on LoCoMo-10.
"""

# =============================================================================
# RETRIEVAL ANSWER PROMPT - Schema-Guided Reasoning
# =============================================================================

RETRIEVAL_ANSWER_PROMPT = """You are a memory retrieval system. Your task is to find and synthesize information from memories.

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


# =============================================================================
# RETRIEVAL CONTEXT FORMATTER
# =============================================================================

def format_retrieval_context(
    nodes: list,
    max_chars: int = 15000,
    include_timestamps: bool = True,
    include_speaker: bool = True,
) -> str:
    """Format retrieved nodes into context for the LLM.

    Args:
        nodes: List of (NeuralNode, score) tuples from retrieval
        max_chars: Maximum characters in context
        include_timestamps: Include [timestamp] prefix
        include_speaker: Include speaker/producer_id

    Returns:
        Formatted context string
    """
    lines = []
    total_chars = 0

    for node, score in nodes:
        # Build line components
        parts = []

        if include_timestamps and node.created_at:
            timestamp = node.created_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"[{timestamp}]")

        if include_speaker:
            speaker = node.metadata.get("producer_id", node.metadata.get("speaker", ""))
            if speaker:
                parts.append(f"{speaker}:")

        parts.append(node.content)
        line = " ".join(parts)

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


# =============================================================================
# SPECIALIZED PROMPTS FOR DIFFERENT QUERY TYPES
# =============================================================================

TEMPORAL_QUERY_PROMPT = """You are a memory retrieval system specialized in temporal queries.

For questions about WHEN something happened:
1. Look for explicit dates, months, years in the memories
2. Convert relative references (e.g., "last year" in a May 2023 memory = 2022)
3. Pay attention to timestamps on each memory
4. If multiple time references exist, identify which one answers the specific question

Memories:
{context}

Question: {question}

Answer (provide the specific date/time):"""


MULTI_HOP_QUERY_PROMPT = """You are a memory retrieval system specialized in multi-hop reasoning.

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


OPEN_DOMAIN_QUERY_PROMPT = """You are a memory retrieval system for open-ended inference questions.

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


# =============================================================================
# PROMPT SELECTOR
# =============================================================================

def select_prompt_for_query(query: str) -> str:
    """Select the best prompt template based on query type.

    This performs simple keyword-based classification.
    For production, use a classifier model.

    Args:
        query: The question being asked

    Returns:
        Appropriate prompt template
    """
    query_lower = query.lower()

    # Temporal indicators
    temporal_words = ["when", "what time", "what date", "how long ago", "which year", "which month"]
    if any(word in query_lower for word in temporal_words):
        return TEMPORAL_QUERY_PROMPT

    # Multi-hop indicators (comparing, combining)
    multi_hop_words = ["both", "and", "together", "compared to", "in common", "share"]
    if any(word in query_lower for word in multi_hop_words):
        return MULTI_HOP_QUERY_PROMPT

    # Open domain / inference indicators
    inference_words = ["likely", "probably", "would", "might", "could", "think", "feel",
                       "predict", "expect", "pursue", "future"]
    if any(word in query_lower for word in inference_words):
        return OPEN_DOMAIN_QUERY_PROMPT

    # Default to general retrieval prompt
    return RETRIEVAL_ANSWER_PROMPT


# =============================================================================
# EXPORTED INTERFACE
# =============================================================================

__all__ = [
    "RETRIEVAL_ANSWER_PROMPT",
    "TEMPORAL_QUERY_PROMPT",
    "MULTI_HOP_QUERY_PROMPT",
    "OPEN_DOMAIN_QUERY_PROMPT",
    "format_retrieval_context",
    "select_prompt_for_query",
]

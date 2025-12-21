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

CRITICAL EVIDENCE REQUIREMENTS:
- Every factual claim in your answer MUST be supported by specific text in the memories
- If a fact is NOT explicitly stated in the memories, do NOT include it
- If the memories don't contain enough information, say "Based on available memories, I cannot determine..."
- DO NOT infer or guess information not directly stated

CRITICAL FOR MULTI-HOP QUESTIONS:
- If asking "how does X do Y", look for ALL instances of X doing Y across memories
- If asking about preferences/habits, combine multiple examples into one answer
- Different memories may describe the SAME activity - recognize these patterns

Memories:
{context}

Question: {question}

Answer (only include facts directly supported by the memories above):"""


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
            resolved_date = ""
            if node.metadata:
                resolved_date = node.metadata.get("resolved_date", "") or ""
            if resolved_date:
                parts.append(f"[date={resolved_date}]")
            timestamp = node.created_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"[timestamp={timestamp}]")

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

# SINGLE-HOP FACTUAL PROMPT (STRICT EVIDENCE MODE) - ANTI-HALLUCINATION VERSION
SINGLE_HOP_ANSWER_PROMPT = """Extract the specific fact from the memories below.

ANTI-HALLUCINATION RULES:
1. ONLY output facts that appear VERBATIM in the memories
2. DO NOT invent, guess, or assume anything
3. For lists: scan ALL memories, output ALL matching items
4. If not explicitly stated, output: Not found

EXTRACTION PROCESS:
1. Identify the person asked about (match [speaker] or name mentions)
2. Find memories where that person discusses the topic
3. Extract ONLY the exact words/phrases that answer the question
4. If multiple items exist across memories, list ALL of them

Memories:
{context}

Question: {question}

Answer (exact words from memories only):"""


# LIST QUESTION PROMPT (MULTIPLE ITEMS) - ANTI-HALLUCINATION VERSION
LIST_QUESTION_ANSWER_PROMPT = """Extract ALL items from the memories that answer this question.

ANTI-HALLUCINATION RULES:
1. ONLY output items that appear VERBATIM in the memories
2. DO NOT invent, infer, or guess any items
3. Scan EVERY memory - items may be scattered across multiple memories
4. Output comma-separated, no explanations
5. If no items found, output: Not found

VALIDATION PROCESS:
1. For each item you want to output, find the EXACT memory that mentions it
2. If you cannot point to a specific memory for an item, DO NOT include it
3. Check the speaker - only include items about the person asked about

Memories:
{context}

Question: {question}

Answer (only items found verbatim in memories):"""


# AGGREGATION/COMPARISON PROMPT (e.g., "What do X and Y have in common?")
AGGREGATION_ANSWER_PROMPT = """Find what is shared between the entities in this question.

RULES:
1. Output ONLY shared items - comma-separated, no explanations.
2. Only include facts stated about ALL entities asked about.
3. If nothing is shared, output: Not found

EXAMPLE:
Q: What do John and Mary both like? → hiking, coffee

Memories:
{context}

Question: {question}

Answer:"""


TEMPORAL_QUERY_PROMPT = """You are a memory retrieval system specialized in temporal queries.

For questions about WHEN something happened:
1. Use metadata fields when available (resolved_date, timestamp) to answer
2. Look for explicit dates, months, years in the memories
3. Convert relative references (e.g., "last year" in a May 2023 memory = 2022)
4. Prefer resolved_date over text if both are present
5. If multiple time references exist, identify which one answers the specific question

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


OPEN_DOMAIN_QUERY_PROMPT = """You are answering an open-ended inference question.

First decide the question type:
- If it is about the people or events in the conversation, infer ONLY from the memories.
- If it is general knowledge (definitions, how things work, public facts), ignore memories and answer from your own knowledge.

REASONING APPROACH:
1. For conversation-based questions, gather the best clues in the memories.
2. Look for patterns, recurring themes, stated preferences.
3. Make a reasonable inference; do not copy timestamps or metadata.
4. Match the question type:
   - Yes/No questions: answer "Yes" or "No" plus a short reason.
   - A-or-B questions: choose one option and give a short reason.
   - Otherwise: give a concise phrase or 1 sentence.
5. Do NOT answer with a date unless the question explicitly asks "when/what date".

Memories:
{context}

Question: {question}

Answer:"""


# =============================================================================
# QUERY REWRITING PROMPTS (THE PLAN Enhancement)
# =============================================================================

QUERY_REWRITE_PROMPT = """You are a query rewriting assistant. Rewrite the question in {num_rewrites} alternative ways to improve memory retrieval.

REWRITING RULES:
1. Keep the core meaning EXACTLY the same
2. Use different phrasings (synonyms, restructure)
3. For single-hop questions: focus on the entity and attribute
4. Make questions more specific when possible
5. Include the main entity name in each rewrite

Original question: {question}

Return ONLY a JSON array of rewrites:
["rewrite 1", "rewrite 2", ...]

JSON array:"""


SINGLE_HOP_REWRITE_PROMPT = """Rewrite this single-hop factual question in {num_rewrites} alternative ways.

SINGLE-HOP FOCUS:
- Keep the target entity explicit
- Vary the phrasing of the attribute/property being asked
- Include possessive forms and alternative constructions
- Example: "What is John's job?" → ["What does John do for work?", "What profession does John have?"]

Original: {question}

Return only a JSON array:"""


TEMPORAL_REWRITE_PROMPT = """Rewrite this temporal question in {num_rewrites} alternative ways.

TEMPORAL FOCUS:
- Keep date/time references intact
- Vary how the temporal aspect is asked
- Include explicit and relative time phrasings
- Example: "When did Mary visit Paris?" → ["What date did Mary go to Paris?", "In what month did Mary travel to Paris?"]

Original: {question}

Return only a JSON array:"""


# =============================================================================
# QUERY REWRITING FUNCTIONS
# =============================================================================

def get_rewrite_prompt(query: str, num_rewrites: int = 2) -> tuple[str, str]:
    """Get the appropriate rewrite prompt for a query.

    THE PLAN: "mix query rewriting (single-hop focus) with controlled beam (2-3 rewrites)"

    Args:
        query: Original query
        num_rewrites: Number of rewrites to request (default 2-3)

    Returns:
        Tuple of (prompt_template, prompt_type)
    """
    query_lower = query.lower()

    # Detect temporal queries
    temporal_words = ["when", "what time", "what date", "which year", "which month", "how long ago"]
    if any(word in query_lower for word in temporal_words):
        return TEMPORAL_REWRITE_PROMPT.format(
            question=query,
            num_rewrites=num_rewrites
        ), "temporal"

    # Detect single-hop entity queries (most common for improvement)
    single_hop_patterns = [
        "what is", "what's", "what does", "what did",
        "who is", "who's", "who does",
        "where is", "where does", "where did",
        "how does", "how did",
        "'s "  # Possessive pattern like "John's"
    ]
    if any(pattern in query_lower for pattern in single_hop_patterns):
        return SINGLE_HOP_REWRITE_PROMPT.format(
            question=query,
            num_rewrites=num_rewrites
        ), "single_hop"

    # Default to general rewrite
    return QUERY_REWRITE_PROMPT.format(
        question=query,
        num_rewrites=num_rewrites
    ), "general"


async def generate_query_rewrites(
    query: str,
    llm_client,  # aiohttp session or compatible
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b-instruct",
    num_rewrites: int = 2,
    timeout_seconds: float = 5.0
) -> list[str]:
    """Generate alternative query phrasings using LLM.

    THE PLAN: "controlled beam (2-3 rewrites) and penalize duplicates during fusion"

    Args:
        query: Original query
        llm_client: aiohttp ClientSession or compatible
        base_url: LLM API base URL
        model: Model to use for rewriting
        num_rewrites: Number of rewrites to generate
        timeout_seconds: Timeout for LLM call

    Returns:
        List of query rewrites (including original)
    """
    import json
    import aiohttp

    prompt, prompt_type = get_rewrite_prompt(query, num_rewrites)

    try:
        async with llm_client.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,  # Low temp for focused rewrites
                    "num_predict": 200
                }
            },
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as response:
            result = await response.json()
            resp = result.get("response", "").strip()

            # Parse JSON array
            try:
                # Find JSON array in response
                start = resp.find("[")
                end = resp.rfind("]") + 1
                if start >= 0 and end > start:
                    rewrites = json.loads(resp[start:end])
                    if isinstance(rewrites, list):
                        # Filter to valid strings and limit
                        valid = [r for r in rewrites if isinstance(r, str) and len(r) > 5]
                        return valid[:num_rewrites]
            except json.JSONDecodeError:
                pass

            # Fallback: try to extract line-by-line
            lines = [line.strip().strip('"').strip("'").strip(",")
                     for line in resp.split("\n")
                     if line.strip() and not line.strip().startswith("[")]
            return lines[:num_rewrites]

    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Query rewrite failed: {e}")
        return []


def deduplicate_rewrites(
    original: str,
    rewrites: list[str],
    similarity_threshold: float = 0.85
) -> list[str]:
    """Deduplicate rewrites that are too similar.

    THE PLAN: "penalize duplicates during fusion"

    Args:
        original: Original query
        rewrites: List of generated rewrites
        similarity_threshold: Max similarity to keep (higher = more duplicates removed)

    Returns:
        Deduplicated list of rewrites
    """
    if not rewrites:
        return []

    # Simple word overlap similarity
    def word_overlap(s1: str, s2: str) -> float:
        words1 = set(s1.lower().split())
        words2 = set(s2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    # Remove rewrites too similar to original
    filtered = []
    for rewrite in rewrites:
        sim_to_original = word_overlap(original, rewrite)
        if sim_to_original < similarity_threshold:
            # Check against already-accepted rewrites
            too_similar = False
            for accepted in filtered:
                if word_overlap(rewrite, accepted) >= similarity_threshold:
                    too_similar = True
                    break
            if not too_similar:
                filtered.append(rewrite)

    return filtered


def fuse_rewrite_results(
    results_by_query: dict[str, list[tuple]],
    original_query: str,
    limit: int = 10,
    duplicate_penalty: float = 0.7
) -> list[tuple]:
    """Fuse results from multiple query rewrites with duplicate penalty.

    THE PLAN: "penalize duplicates during fusion"

    Args:
        results_by_query: Dict of query -> list of (node, score) tuples
        original_query: The original query (gets priority)
        limit: Maximum results to return
        duplicate_penalty: Penalty multiplier for duplicate nodes (0.7 = 30% penalty)

    Returns:
        Fused list of (node, adjusted_score) tuples
    """
    # Track best score per node
    node_scores: dict[str, tuple[float, any]] = {}  # node_id -> (best_score, node)
    node_occurrence_count: dict[str, int] = {}  # How many queries returned this node

    # Process original query first (priority)
    if original_query in results_by_query:
        for node, score in results_by_query[original_query]:
            node_scores[node.node_id] = (score, node)
            node_occurrence_count[node.node_id] = 1

    # Process rewrite results
    for query, results in results_by_query.items():
        if query == original_query:
            continue

        for node, score in results:
            count = node_occurrence_count.get(node.node_id, 0)

            if node.node_id in node_scores:
                # Already seen - apply duplicate penalty or boost
                old_score, _ = node_scores[node.node_id]

                # If appearing in multiple rewrites, slight boost (confirms relevance)
                # but penalize diminishing returns
                if count < 2:
                    # First duplicate - small boost
                    new_score = max(old_score, score * (1 + 0.1))
                else:
                    # Multiple duplicates - diminishing boost
                    new_score = max(old_score, score * duplicate_penalty)

                node_scores[node.node_id] = (new_score, node)
            else:
                # New node from rewrite
                node_scores[node.node_id] = (score, node)

            node_occurrence_count[node.node_id] = count + 1

    # Sort by score and return top
    ranked = sorted(
        [(node, score) for score, node in node_scores.values()],
        key=lambda x: x[1],
        reverse=True
    )

    return ranked[:limit]


# =============================================================================
# PROMPT SELECTOR
# =============================================================================

def select_prompt_for_query(
    query: str,
    is_list_question: bool = False,
    list_type: str | None = None
) -> str:
    """Select the best prompt template based on query type.

    Enhanced for single-hop optimization with strict evidence requirements.

    Args:
        query: The question being asked
        is_list_question: Whether this is a list question (from QueryAnalysis)
        list_type: Type of list question ("plural", "aggregation")

    Returns:
        Appropriate prompt template
    """
    query_lower = query.lower()

    # AGGREGATION / COMPARISON queries (highest priority - these fail often)
    aggregation_words = ["in common", "both", "share", "do .* and .* have"]
    if any(word in query_lower for word in aggregation_words):
        return AGGREGATION_ANSWER_PROMPT
    if list_type == "aggregation":
        return AGGREGATION_ANSWER_PROMPT

    # LIST QUESTIONS (multiple items expected)
    if is_list_question and list_type == "plural":
        return LIST_QUESTION_ANSWER_PROMPT

    # Explicit list patterns
    list_patterns = [
        "what books", "what movies", "what types", "what kinds",
        "what things", "which items", "how many", "all of",
        "has .* read", "has .* done", "has .* made", "has .* painted",
        "have they"
    ]
    if any(pattern in query_lower for pattern in list_patterns):
        return LIST_QUESTION_ANSWER_PROMPT

    # Temporal indicators
    temporal_words = ["when", "what time", "what date", "how long ago", "which year", "which month"]
    if any(word in query_lower for word in temporal_words):
        return TEMPORAL_QUERY_PROMPT

    # Multi-hop indicators (comparing, combining - but not aggregation)
    multi_hop_words = ["together", "compared to"]
    if any(word in query_lower for word in multi_hop_words):
        return MULTI_HOP_QUERY_PROMPT

    # Open domain / inference indicators
    inference_words = ["likely", "probably", "would", "might", "could", "think", "feel",
                       "predict", "expect", "pursue", "future"]
    if any(word in query_lower for word in inference_words):
        return OPEN_DOMAIN_QUERY_PROMPT

    # SINGLE-HOP factual queries (simple entity-attribute questions)
    single_hop_patterns = [
        r"what is \w+'s",
        r"what does \w+ ",
        r"where is \w+",
        r"where does \w+",
        r"who is \w+'s",
        r"how does \w+",
        r"what did \w+ ",
    ]
    import re
    for pattern in single_hop_patterns:
        if re.search(pattern, query_lower):
            return SINGLE_HOP_ANSWER_PROMPT

    # Default to general retrieval prompt (also has evidence requirements now)
    return RETRIEVAL_ANSWER_PROMPT


# =============================================================================
# EXPORTED INTERFACE
# =============================================================================

__all__ = [
    # Answer prompts
    "RETRIEVAL_ANSWER_PROMPT",
    "SINGLE_HOP_ANSWER_PROMPT",
    "LIST_QUESTION_ANSWER_PROMPT",
    "AGGREGATION_ANSWER_PROMPT",
    "TEMPORAL_QUERY_PROMPT",
    "MULTI_HOP_QUERY_PROMPT",
    "OPEN_DOMAIN_QUERY_PROMPT",
    # Rewrite prompts
    "QUERY_REWRITE_PROMPT",
    "SINGLE_HOP_REWRITE_PROMPT",
    "TEMPORAL_REWRITE_PROMPT",
    # Functions
    "format_retrieval_context",
    "select_prompt_for_query",
    "get_rewrite_prompt",
    "generate_query_rewrites",
    "deduplicate_rewrites",
    "fuse_rewrite_results",
]

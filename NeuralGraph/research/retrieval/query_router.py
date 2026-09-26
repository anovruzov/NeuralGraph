"""Query Router - Intelligent Query Analysis and Pre-Filtering

THE FUNDAMENTAL INSIGHT:
Different question types need different retrieval strategies.

PROBLEM WITH CURRENT APPROACH:
    Query: "What is Caroline's job?"
    Message: "I work as a teacher" (speaker=Caroline)

    Embedding similarity: LOW (job ≠ teacher semantically)
    Result: MISS

SOLUTION:
    1. Detect query is asking about CAROLINE
    2. Filter to messages WHERE speaker=Caroline OR mentions Caroline
    3. Search within filtered set
    4. "I work as a teacher" now has a chance

QUERY TYPES AND STRATEGIES:

1. ENTITY-FOCUSED (single-hop)
   Pattern: "What is X's Y?" / "Where does X live?" / "What did X say about Y?"
   Strategy: Filter by entity X, then semantic search

2. TEMPORAL-FOCUSED
   Pattern: "When did X happen?" / "What happened on DATE?" / "After X, what?"
   Strategy: Filter by temporal markers, traverse temporal chain

3. MULTI-HOP
   Pattern: "What did the person who X say about Y?"
   Strategy: Resolve "the person who X" first, then filter

4. ADVERSARIAL
   Pattern: Negations, hypotheticals, "Did X NOT happen?"
   Strategy: Special negation handling, verify against all candidates

SOFT VS HARD FILTERING (Phase 1 Fix):
- HARD: Candidates not matching filter are REMOVED (causes over-filtering regression)
- SOFT: Candidates not matching filter get SCORE PENALTY (maintains recall, improves precision)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


class QueryType(Enum):
    """Classification of query intent."""
    ENTITY_ATTRIBUTE = "entity_attribute"      # "What is X's job?"
    ENTITY_ACTION = "entity_action"            # "What did X do?"
    ENTITY_RELATION = "entity_relation"        # "Who is X's friend?"
    TEMPORAL_WHEN = "temporal_when"            # "When did X happen?"
    TEMPORAL_SEQUENCE = "temporal_sequence"    # "What happened after X?"
    TEMPORAL_DURATION = "temporal_duration"    # "How long did X last?"
    MULTI_HOP = "multi_hop"                    # "What did the person who X..."
    ADVERSARIAL = "adversarial"                # Negations, hypotheticals
    OPEN_DOMAIN = "open_domain"                # General questions


@dataclass
class QueryAnalysis:
    """Result of analyzing a query."""
    query_text: str
    query_type: QueryType

    # Extracted entities (people, places, things)
    entities: list[str] = field(default_factory=list)

    # Primary subject (who/what the question is ABOUT)
    primary_subject: str | None = None

    # Speaker filter (if asking about what someone SAID)
    speaker_filter: str | None = None

    # Temporal markers
    temporal_markers: list[str] = field(default_factory=list)
    temporal_direction: str | None = None  # "before", "after", "during"

    # Negation detection
    has_negation: bool = False

    # Confidence in classification
    confidence: float = 0.5

    # Recommended retrieval strategy
    strategy: str = "semantic"  # "semantic", "entity_filter", "temporal_filter", "hybrid"

    # Filter parameters
    filter_params: dict[str, Any] = field(default_factory=dict)

    # THE PLAN Phase 2a: Rare/specific terms for keyword retrieval
    rare_terms: list[str] = field(default_factory=list)

    # Open-domain indicators
    is_open_domain: bool = False
    open_domain_confidence: float = 0.0

    # SINGLE-HOP OPTIMIZATION: List question detection
    # List questions like "What books has X read?" need multiple distinct answers
    is_list_question: bool = False
    list_question_type: str | None = None  # "plural", "aggregation", "all"

    # SINGLE-HOP OPTIMIZATION: Should suppress multi-hop expansion
    suppress_multi_hop: bool = False


@dataclass
class SoftFilterResult:
    """Result of soft filtering - candidates with score adjustments instead of removal.

    SOFT FILTERING PRINCIPLE (THE PLAN Enhancement):
    Two modes for score adjustment:
    1. PENALTY MODE: Non-matching candidates get score penalties (original)
       - Matches: 1.0, Non-matches: 0.2
    2. BOOST MODE: Matching candidates get score boosts (new)
       - Matches: 1.5+, Non-matches: 1.0 (maintains recall)

    Boost mode is preferred for single-hop queries because it:
    - Preserves ALL candidates (better recall)
    - Creates larger score separation for matches
    - Allows reranker to see full candidate pool

    Score modifiers (penalty mode):
    - 1.0 = full match (speaker/entity matches perfectly)
    - 0.7-0.9 = partial match (fuzzy match, alias match)
    - 0.3-0.5 = weak match (mentioned entity, not speaker)
    - 0.1-0.2 = no match but semantically adjacent

    Score modifiers (boost mode):
    - 1.8 = exact speaker match (strong boost)
    - 1.5-1.7 = alias/fuzzy speaker match
    - 1.3-1.5 = entity ID match
    - 1.1-1.2 = content mention
    - 1.0 = no match (no penalty, maintains recall)
    """
    node_id: str
    score_modifier: float  # Multiplier for final score (0.0 to 2.0)
    match_reason: str  # Why this score was assigned

    # Match quality indicators
    is_speaker_match: bool = False
    is_content_match: bool = False
    is_entity_id_match: bool = False
    is_fuzzy_match: bool = False

    # Boost mode tracking
    boost_applied: float = 0.0  # Additional boost applied in boost mode


# ============================================================================
# FUZZY MATCHING UTILITIES
# ============================================================================

def _compute_edit_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings.

    Used for fuzzy entity matching to catch typos and variations.
    """
    if len(s1) < len(s2):
        return _compute_edit_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Insertions, deletions, substitutions
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def _fuzzy_match(query_entity: str, candidate: str, threshold: float = 0.7) -> tuple[bool, float]:
    """Check if query_entity fuzzy-matches candidate.

    Args:
        query_entity: Entity from query (e.g., "Caroline")
        candidate: Candidate string to match against (e.g., "Carol")
        threshold: Minimum similarity ratio (0.0-1.0)

    Returns:
        Tuple of (is_match, similarity_ratio)
    """
    query_lower = query_entity.lower().strip()
    candidate_lower = candidate.lower().strip()

    # Exact substring match (highest priority)
    if query_lower in candidate_lower or candidate_lower in query_lower:
        return True, 1.0

    # Prefix match (common for nicknames: "Caroline" -> "Carol")
    min_prefix_len = min(3, len(query_lower), len(candidate_lower))
    if query_lower[:min_prefix_len] == candidate_lower[:min_prefix_len]:
        # Compute similarity based on shared prefix
        shared_len = 0
        for i in range(min(len(query_lower), len(candidate_lower))):
            if query_lower[i] == candidate_lower[i]:
                shared_len += 1
            else:
                break
        prefix_ratio = shared_len / max(len(query_lower), len(candidate_lower))
        if prefix_ratio >= threshold:
            return True, prefix_ratio

    # Edit distance similarity
    max_len = max(len(query_lower), len(candidate_lower))
    if max_len == 0:
        return False, 0.0

    distance = _compute_edit_distance(query_lower, candidate_lower)
    similarity = 1.0 - (distance / max_len)

    return similarity >= threshold, similarity


# Common nickname/alias mappings
NICKNAME_ALIASES: dict[str, list[str]] = {
    "william": ["will", "bill", "billy", "willy", "liam"],
    "james": ["jim", "jimmy", "jamie"],
    "robert": ["rob", "bob", "bobby", "robbie"],
    "richard": ["rick", "dick", "ricky", "rich"],
    "michael": ["mike", "mikey", "mick"],
    "elizabeth": ["liz", "beth", "lizzy", "betty", "eliza"],
    "jennifer": ["jen", "jenny"],
    "katherine": ["kate", "katie", "kathy", "kat", "kitty"],
    "patricia": ["pat", "patty", "tricia"],
    "margaret": ["maggie", "meg", "peggy", "marge"],
    "caroline": ["carol", "carrie", "lina"],
    "catherine": ["cathy", "kate", "katie", "cat"],
    "nicholas": ["nick", "nicky"],
    "christopher": ["chris", "topher"],
    "alexander": ["alex", "xander", "sandy"],
    "benjamin": ["ben", "benji", "benny"],
    "jonathan": ["jon", "johnny", "nathan"],
    "matthew": ["matt", "matty"],
    "anthony": ["tony", "ant"],
    "daniel": ["dan", "danny"],
    "david": ["dave", "davey"],
    "joseph": ["joe", "joey"],
    "thomas": ["tom", "tommy"],
    "edward": ["ed", "eddie", "ted", "teddy"],
    "samuel": ["sam", "sammy"],
    "steven": ["steve", "stevie"],
    "andrew": ["andy", "drew"],
    "joshua": ["josh"],
    "timothy": ["tim", "timmy"],
}


def _is_alias_match(entity1: str, entity2: str) -> tuple[bool, float]:
    """Check if two entities are nickname/alias matches.

    Args:
        entity1: First entity name
        entity2: Second entity name

    Returns:
        Tuple of (is_match, confidence)
    """
    e1_lower = entity1.lower().strip()
    e2_lower = entity2.lower().strip()

    # Check if either is an alias of the other
    for canonical, aliases in NICKNAME_ALIASES.items():
        all_names = [canonical] + aliases
        e1_in = e1_lower in all_names
        e2_in = e2_lower in all_names

        if e1_in and e2_in:
            # Both are in the same alias family
            return True, 0.85

    return False, 0.0


def _match_entity(
    query_entity: str,
    speaker: str | None,
    content: str,
    entity_ids: list[str] | None,
    fuzzy_threshold: float = 0.7
) -> SoftFilterResult:
    """Match a query entity against a node's speaker, content, and entity IDs.

    Uses multiple matching strategies in priority order:
    1. Exact speaker match → 1.0 modifier
    2. Alias speaker match → 0.9 modifier
    3. Fuzzy speaker match → 0.8 modifier
    4. Entity ID exact match → 0.85 modifier
    5. Content mention (exact) → 0.7 modifier
    6. Content mention (fuzzy) → 0.5 modifier
    7. No match → 0.2 modifier (maintains in pool but deprioritized)

    Args:
        query_entity: Entity from query
        speaker: Node's speaker metadata
        content: Node's content text
        entity_ids: Node's entity ID list
        fuzzy_threshold: Minimum similarity for fuzzy matching

    Returns:
        SoftFilterResult with score modifier and match details
    """
    query_lower = query_entity.lower().strip()
    result = SoftFilterResult(
        node_id="",  # Set by caller
        score_modifier=0.2,  # Default: no match penalty
        match_reason="no_match"
    )

    # 1. Exact speaker match
    if speaker:
        speaker_lower = speaker.lower().strip()
        if query_lower == speaker_lower:
            result.score_modifier = 1.0
            result.match_reason = "exact_speaker_match"
            result.is_speaker_match = True
            return result

        # Substring match (entity in speaker or vice versa)
        if query_lower in speaker_lower or speaker_lower in query_lower:
            result.score_modifier = 0.95
            result.match_reason = "substring_speaker_match"
            result.is_speaker_match = True
            return result

    # 2. Alias speaker match
    if speaker:
        is_alias, alias_conf = _is_alias_match(query_entity, speaker)
        if is_alias:
            result.score_modifier = 0.9
            result.match_reason = "alias_speaker_match"
            result.is_speaker_match = True
            result.is_fuzzy_match = True
            return result

    # 3. Fuzzy speaker match
    if speaker:
        is_fuzzy, fuzzy_sim = _fuzzy_match(query_entity, speaker, fuzzy_threshold)
        if is_fuzzy:
            result.score_modifier = 0.75 + (0.15 * fuzzy_sim)  # 0.75-0.9
            result.match_reason = f"fuzzy_speaker_match (sim={fuzzy_sim:.2f})"
            result.is_speaker_match = True
            result.is_fuzzy_match = True
            return result

    # 4. Entity ID exact match
    if entity_ids:
        for eid in entity_ids:
            eid_lower = eid.lower()
            if query_lower == eid_lower or query_lower in eid_lower:
                result.score_modifier = 0.85
                result.match_reason = f"entity_id_match ({eid})"
                result.is_entity_id_match = True
                return result

    # 5. Entity ID fuzzy/alias match
    if entity_ids:
        for eid in entity_ids:
            is_alias, _ = _is_alias_match(query_entity, eid)
            if is_alias:
                result.score_modifier = 0.8
                result.match_reason = f"entity_id_alias_match ({eid})"
                result.is_entity_id_match = True
                result.is_fuzzy_match = True
                return result

            is_fuzzy, fuzzy_sim = _fuzzy_match(query_entity, eid, fuzzy_threshold)
            if is_fuzzy:
                result.score_modifier = 0.7 + (0.1 * fuzzy_sim)
                result.match_reason = f"entity_id_fuzzy_match ({eid}, sim={fuzzy_sim:.2f})"
                result.is_entity_id_match = True
                result.is_fuzzy_match = True
                return result

    # 6. Content mention (exact)
    content_lower = content.lower() if content else ""
    if query_lower in content_lower:
        result.score_modifier = 0.7
        result.match_reason = "content_exact_mention"
        result.is_content_match = True
        return result

    # 7. Content mention (fuzzy) - check word by word
    if content_lower:
        words = re.findall(r'\b\w+\b', content_lower)
        for word in words:
            if len(word) < 3:
                continue
            is_fuzzy, fuzzy_sim = _fuzzy_match(query_entity, word, fuzzy_threshold)
            if is_fuzzy and fuzzy_sim >= 0.8:  # Higher threshold for content
                result.score_modifier = 0.5 + (0.2 * fuzzy_sim)
                result.match_reason = f"content_fuzzy_mention ({word}, sim={fuzzy_sim:.2f})"
                result.is_content_match = True
                result.is_fuzzy_match = True
                return result

    # 8. No match - keep in pool but deprioritize
    result.score_modifier = 0.2
    result.match_reason = "no_match"
    return result


class QueryRouter:
    """Analyzes queries and routes to appropriate retrieval strategy.

    THE KEY INSIGHT:
    Instead of treating all queries the same (embed → search all),
    we FIRST understand what the query is asking, THEN filter the
    search space to relevant messages.

    This is how humans think:
    - "What is Caroline's job?" → Think about what CAROLINE said
    - "When did they go to Paris?" → Think about PARIS + TIME
    - "What happened after the party?" → Think in SEQUENCE
    """

    # Patterns for entity-focused queries (COMPREHENSIVE - 40+ patterns)
    # These patterns are designed to be universal and work across any domain
    ENTITY_PATTERNS = [
        # === POSSESSIVE PATTERNS ===
        (r"(?:what|where|who|how)\s+(?:is|are|was|were)\s+(\w+)'s\s+(\w+)", "entity_attribute"),
        (r"(\w+)'s\s+(?:favorite|favourite|preferred|best|worst)\s+(\w+)", "entity_attribute"),
        (r"(\w+)'s\s+(?:first|last|new|old|current|previous|former)\s+(\w+)", "entity_attribute"),

        # === DIRECT ENTITY QUESTIONS ===
        (r"(?:what|where|who)\s+(?:did|does|do|is|was)\s+(\w+)\s+", "entity_action"),
        (r"(?:what|how)\s+(?:did|does|do)\s+(\w+)\s+(?:say|think|feel|believe)", "entity_action"),
        (r"(?:what|which)\s+(?:does|did)\s+(\w+)\s+(?:like|love|hate|prefer|enjoy)", "entity_action"),
        (r"(?:where)\s+(?:does|did|is|was)\s+(\w+)\s+(?:live|work|study|stay|go)", "entity_action"),
        (r"(?:how)\s+(?:does|did|is|was)\s+(\w+)\s+(?:feel|doing|feeling)", "entity_action"),

        # === RELATIONSHIP PATTERNS ===
        (r"(?:who)\s+(?:is|are|was|were)\s+(\w+)'s\s+(\w+)", "entity_relation"),
        (r"(?:who)\s+(?:does|did)\s+(\w+)\s+(?:know|meet|date|marry|love)", "entity_relation"),
        (r"(?:what|who)\s+(?:is|are)\s+(\w+)\s+(?:related|connected|linked)\s+to", "entity_relation"),
        (r"(?:is|are|was|were)\s+(\w+)\s+(?:friends?|colleagues?|partners?)\s+with", "entity_relation"),
        (r"(?:how)\s+(?:does|did)\s+(\w+)\s+(?:know|meet)\s+(\w+)", "entity_relation"),

        # === ATTRIBUTE/PROPERTY PATTERNS ===
        (r"(?:what)\s+(?:is|are|was|were)\s+(\w+)'s\s+(?:name|age|job|occupation|profession|hobby|hobbies)", "entity_attribute"),
        (r"(?:what)\s+(?:color|colour|size|type|kind|brand)\s+(?:is|was)\s+(\w+)'s", "entity_attribute"),
        (r"(?:how)\s+(?:old|tall|heavy|far|much)\s+(?:is|was)\s+(\w+)", "entity_attribute"),
        (r"(?:what)\s+(?:does|did)\s+(\w+)\s+(?:look like|sound like|seem like)", "entity_attribute"),

        # === ORIGIN/LOCATION PATTERNS ===
        (r"(?:where)\s+(?:is|was|does|did)\s+(\w+)\s+(?:from|born|raised|grow up)", "entity_attribute"),
        (r"(?:where)\s+(?:does|did)\s+(\w+)\s+(?:come from|originate|hail)", "entity_attribute"),
        (r"(?:what)\s+(?:country|city|town|state|region)\s+(?:is|was)\s+(\w+)\s+from", "entity_attribute"),

        # === WORK/CAREER PATTERNS ===
        (r"(?:what)\s+(?:does|did)\s+(\w+)\s+(?:do|work as|work at)", "entity_attribute"),
        (r"(?:where)\s+(?:does|did)\s+(\w+)\s+(?:work|study|teach|practice)", "entity_action"),
        (r"(?:what)\s+(?:is|was)\s+(\w+)'s\s+(?:job|occupation|profession|career|role|position)", "entity_attribute"),
        (r"(?:is|was)\s+(\w+)\s+(?:a|an)\s+(?:doctor|teacher|engineer|lawyer|nurse|student)", "entity_attribute"),

        # === PREFERENCE/OPINION PATTERNS ===
        (r"(?:what)\s+(?:does|did)\s+(\w+)\s+(?:like|love|hate|enjoy|prefer|dislike)", "entity_action"),
        (r"(?:does|did)\s+(\w+)\s+(?:like|love|hate|enjoy|prefer)\s+(\w+)", "entity_action"),
        (r"(?:what)\s+(?:is|are|was|were)\s+(\w+)'s\s+(?:opinion|view|thought|feeling)\s+(?:on|about)", "entity_action"),

        # === ACTION/ACTIVITY PATTERNS ===
        (r"(?:what)\s+(?:did|does|has|had)\s+(\w+)\s+(?:do|done|doing|make|made|buy|bought)", "entity_action"),
        (r"(?:has|had|did|does)\s+(\w+)\s+(?:ever|always|never|often|usually)\s+(\w+)", "entity_action"),
        (r"(?:why)\s+(?:did|does|is|was)\s+(\w+)\s+", "entity_action"),
        (r"(?:how)\s+(?:did|does)\s+(\w+)\s+(?:manage|succeed|fail|handle|deal)", "entity_action"),

        # === COMMUNICATION PATTERNS ===
        (r"(?:what)\s+(?:did|does)\s+(\w+)\s+(?:say|tell|mention|explain|describe|ask)", "entity_action"),
        (r"(?:did|does|has)\s+(\w+)\s+(?:say|tell|mention|talk|speak)\s+about", "entity_action"),
        (r"(?:according to|as per)\s+(\w+)", "entity_action"),

        # === COMPARISON PATTERNS ===
        (r"(?:is|was)\s+(\w+)\s+(?:taller|shorter|older|younger|better|worse)\s+than\s+(\w+)", "entity_relation"),
        (r"(?:who|what)\s+(?:is|was)\s+(?:more|less|most|least)\s+(\w+)\s+(?:than)?\s*(\w+)?", "entity_relation"),
        (r"(?:between|among)\s+(\w+)\s+and\s+(\w+)", "entity_relation"),

        # === EXPERIENCE/EVENT PATTERNS ===
        (r"(?:has|had|did)\s+(\w+)\s+(?:been to|visited|seen|experienced|tried)", "entity_action"),
        (r"(?:what)\s+(?:happened|occurs|occurred)\s+(?:to|with)\s+(\w+)", "entity_action"),
        (r"(?:did|has|had)\s+(\w+)\s+(?:participate|attend|join|enter|win|lose)", "entity_action"),
    ]

    # Patterns for temporal queries (COMPREHENSIVE - 50+ patterns)
    # These patterns are designed to be universal and work across any domain
    TEMPORAL_PATTERNS = [
        # === BASIC WHEN PATTERNS ===
        (r"when\s+did\s+(\w+)", "temporal_when"),
        (r"when\s+does\s+(\w+)", "temporal_when"),
        (r"when\s+is\s+(\w+)", "temporal_when"),
        (r"when\s+was\s+(\w+)", "temporal_when"),
        (r"when\s+will\s+(\w+)", "temporal_when"),
        (r"when\s+(?:was|is|will)\s+the\s+(?:last|next|first)\s+time", "temporal_when"),

        # === SEQUENCE PATTERNS ===
        (r"what\s+(?:happened|occurred)\s+(?:after|before|during)\s+", "temporal_sequence"),
        (r"what\s+(?:came|comes)\s+(?:before|after|next)", "temporal_sequence"),
        (r"(?:before|after|since|until)\s+(\w+)\s+(?:did|does|was|were)", "temporal_sequence"),
        (r"(?:following|preceding)\s+the\s+(\w+)", "temporal_sequence"),
        (r"(?:prior to|subsequent to|in the wake of)", "temporal_sequence"),

        # === DURATION PATTERNS ===
        (r"how\s+long\s+(?:did|does|was|were|has|had)", "temporal_duration"),
        (r"how\s+long\s+(?:ago|since|until|before|after)", "temporal_duration"),
        (r"for\s+how\s+(?:long|many|much)\s+(?:did|does|has|had)", "temporal_duration"),
        (r"(?:duration|length)\s+of\s+(?:the|this|that)", "temporal_duration"),

        # === RELATIVE TIME MARKERS ===
        (r"(?:yesterday|today|tomorrow)", "temporal_marker"),
        (r"(?:last|next|this|that)\s+(?:week|month|year|day|night|morning|evening|afternoon)", "temporal_marker"),
        (r"(?:last|next|this|past)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)", "temporal_marker"),
        (r"(?:last|next|this|past)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)", "temporal_marker"),

        # === AGO PATTERNS ===
        (r"(\d+)\s+(?:days?|weeks?|months?|years?)\s+ago", "temporal_marker"),
        (r"(?:a|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:days?|weeks?|months?|years?)\s+ago", "temporal_marker"),
        (r"(?:few|several|couple(?:\s+of)?|many)\s+(?:days?|weeks?|months?|years?)\s+ago", "temporal_marker"),
        (r"(?:recently|lately|just|just now|right now|currently)", "temporal_marker"),

        # === FUTURE PATTERNS ===
        (r"in\s+(\d+)\s+(?:days?|weeks?|months?|years?)", "temporal_marker"),
        (r"(?:soon|later|eventually|someday|one day|in the future)", "temporal_marker"),
        (r"(?:by|until|before)\s+(?:the\s+end\s+of|next)", "temporal_marker"),
        (r"(?:going to|about to|planning to|scheduled to)", "temporal_marker"),

        # === SPECIFIC TIME PATTERNS ===
        (r"(?:at|around|about)\s+(\d{1,2})\s*(?:am|pm|o'clock)", "temporal_marker"),
        (r"(?:in the|during the)\s+(?:morning|afternoon|evening|night)", "temporal_marker"),
        (r"(?:on|during)\s+the\s+(?:weekend|weekday|holiday)", "temporal_marker"),

        # === DATE PATTERNS ===
        (r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", "temporal_marker"),
        (r"\d{4}[/-]\d{2}[/-]\d{2}", "temporal_marker"),
        (r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?", "temporal_marker"),
        (r"\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:january|february|march|april|may|june|july|august|september|october|november|december)", "temporal_marker"),

        # === YEAR PATTERNS ===
        (r"(?:in|during|around|circa|by)\s+(?:19|20)\d{2}", "temporal_marker"),
        (r"(?:the\s+)?(?:year\s+)?(?:19|20)\d{2}", "temporal_marker"),
        (r"(?:early|mid|late)\s+(?:19|20)\d{2}s?", "temporal_marker"),

        # === SEASONAL PATTERNS ===
        (r"(?:in|during|last|next|this)\s+(?:spring|summer|fall|autumn|winter)", "temporal_marker"),
        (r"(?:spring|summer|fall|autumn|winter)\s+(?:of\s+)?(?:19|20)\d{2}", "temporal_marker"),

        # === FREQUENCY PATTERNS ===
        (r"how\s+(?:often|frequently|regularly|many times)", "temporal_duration"),
        (r"(?:every|each)\s+(?:day|week|month|year|time|other)", "temporal_marker"),
        (r"(?:daily|weekly|monthly|yearly|annually|hourly)", "temporal_marker"),
        (r"(?:once|twice|thrice)\s+(?:a|per|each)\s+(?:day|week|month|year)", "temporal_marker"),
        (r"(?:always|never|sometimes|often|rarely|seldom|usually|frequently)", "temporal_marker"),

        # === PERIOD PATTERNS ===
        (r"(?:throughout|during|over)\s+the\s+(?:course of|period|time)", "temporal_duration"),
        (r"(?:from|between)\s+(?:19|20)\d{2}\s+(?:to|and|until)\s+(?:19|20)\d{2}", "temporal_duration"),
        (r"(?:from|since)\s+(?:the\s+)?(?:beginning|start|end|middle)", "temporal_duration"),

        # === MILESTONE/EVENT TIME PATTERNS ===
        (r"(?:when|what time)\s+(?:did|does|will)\s+(?:the|this|that)\s+(\w+)\s+(?:start|begin|end|finish|happen|occur)", "temporal_when"),
        (r"(?:at the time of|during|while)\s+the\s+(\w+)", "temporal_sequence"),
        (r"(?:before|after|since|during)\s+(?:the\s+)?(\w+)\s+(?:event|meeting|party|trip|wedding|birthday)", "temporal_sequence"),

        # === ELAPSED TIME PATTERNS ===
        (r"how\s+(?:much|many)\s+time\s+(?:has|had|did)\s+(?:passed|elapsed|gone by)", "temporal_duration"),
        (r"(?:time|days?|weeks?|months?|years?)\s+(?:passed|elapsed|since)", "temporal_duration"),
        (r"(?:it's been|it has been|it was)\s+(?:\d+|a|an|one|two|three|few|several)\s+(?:days?|weeks?|months?|years?)", "temporal_duration"),
    ]

    # Negation words
    NEGATION_WORDS = {"not", "never", "no", "none", "neither", "nor", "didn't", "doesn't",
                     "don't", "wasn't", "weren't", "isn't", "aren't", "hasn't", "haven't",
                     "hadn't", "won't", "wouldn't", "couldn't", "shouldn't", "can't"}

    # Common person name patterns (will be augmented with actual names from context)
    PERSON_INDICATORS = {"who", "person", "someone", "somebody", "they", "he", "she"}

    def __init__(self, known_entities: set[str] | None = None):
        """Initialize router.

        Args:
            known_entities: Set of known entity names from the conversation
        """
        self.known_entities = known_entities or set()

    def update_known_entities(self, entities: set[str]) -> None:
        """Update known entities from conversation context."""
        self.known_entities.update(entities)

    def analyze(self, query: str) -> QueryAnalysis:
        """Analyze a query and determine retrieval strategy.

        Args:
            query: The question to analyze

        Returns:
            QueryAnalysis with extracted info and recommended strategy
        """
        query_lower = query.lower().strip()

        analysis = QueryAnalysis(
            query_text=query,
            query_type=QueryType.OPEN_DOMAIN,
            confidence=0.5,
            strategy="semantic"
        )

        # 1. Check for negations (adversarial indicator)
        analysis.has_negation = any(neg in query_lower.split() for neg in self.NEGATION_WORDS)
        if analysis.has_negation:
            analysis.query_type = QueryType.ADVERSARIAL
            analysis.confidence = 0.7

        # 2. Extract entities (capitalized words, known names)
        entities = self._extract_entities(query)
        analysis.entities = entities

        # 3. Detect temporal markers
        temporal_markers = self._extract_temporal_markers(query_lower)
        analysis.temporal_markers = temporal_markers

        # 4. THE PLAN Phase 2a: Extract rare terms for keyword retrieval
        rare_terms = self._extract_rare_terms(query)
        analysis.rare_terms = rare_terms

        # 5. Classify query type and extract primary subject
        query_type, subject, confidence = self._classify_query(query, query_lower, entities, temporal_markers)

        if confidence > analysis.confidence:
            analysis.query_type = query_type
            analysis.confidence = confidence

        analysis.primary_subject = subject

        # 6. Determine if we need speaker filter
        if subject and self._is_asking_about_speech(query_lower):
            analysis.speaker_filter = subject

        # 7. THE PLAN Phase 2a: Detect open-domain queries
        analysis.is_open_domain, analysis.open_domain_confidence = self._detect_open_domain(
            query, query_lower, entities, temporal_markers, analysis.query_type
        )

        # If strongly open-domain and no entities found, upgrade query type
        if analysis.is_open_domain and analysis.open_domain_confidence > 0.7 and not entities:
            analysis.query_type = QueryType.OPEN_DOMAIN
            analysis.confidence = analysis.open_domain_confidence

        # 8. Set retrieval strategy based on analysis
        analysis.strategy, analysis.filter_params = self._determine_strategy(analysis)

        # THE PLAN Phase 2a: If rare terms found, add them to filter params
        if rare_terms:
            analysis.filter_params["rare_terms"] = rare_terms
            # If strategy is semantic and rare terms exist, switch to hybrid
            if analysis.strategy == "semantic" and len(rare_terms) >= 2:
                analysis.strategy = "hybrid_keyword"
                logger.debug(f"Switching to hybrid_keyword strategy due to rare terms: {rare_terms}")

        # 9. SINGLE-HOP OPTIMIZATION: Detect list questions
        is_list, list_type = self._detect_list_question(query, query_lower)
        analysis.is_list_question = is_list
        analysis.list_question_type = list_type
        if is_list:
            analysis.filter_params["is_list_question"] = True
            analysis.filter_params["list_type"] = list_type
            logger.debug(f"List question detected: type={list_type}")

        # 10. SINGLE-HOP OPTIMIZATION: Determine if multi-hop expansion should be suppressed
        analysis.suppress_multi_hop = self._should_suppress_multi_hop(analysis)
        if analysis.suppress_multi_hop:
            analysis.filter_params["suppress_multi_hop"] = True
            logger.debug("Multi-hop expansion suppressed for single-hop query")

        return analysis

    def _detect_open_domain(
        self,
        query: str,
        query_lower: str,
        entities: list[str],
        temporal_markers: list[str],
        current_type: QueryType,
    ) -> tuple[bool, float]:
        """Detect if a query is open-domain (requires world knowledge).

        THE PLAN Phase 2a Enhancement:
        Better detection of open-domain queries to trigger external retrieval.

        Returns:
            Tuple of (is_open_domain, confidence)
        """
        confidence = 0.0

        # If query already has strong entity/temporal signals, less likely open-domain
        if entities and len(entities) <= 2:
            # Few entities might still be open-domain if asking definitional questions
            pass
        elif entities:
            # Many entities suggests session-specific query
            confidence -= 0.2

        if temporal_markers:
            # Temporal queries are usually session-specific
            confidence -= 0.3

        # Definitional patterns (highly likely open-domain)
        definitional_patterns = [
            (r"^what is (?:a |an |the )?", 0.7),
            (r"^what are ", 0.6),
            (r"^who is ", 0.4),  # Lower because often asking about people in conversation
            (r"^who was ", 0.5),
            (r"^define ", 0.8),
            (r"^explain (?:what|how|why)", 0.7),
            (r"^how does .* work", 0.7),
            (r"^why does ", 0.6),
            (r"^why do ", 0.6),
            (r"^what causes ", 0.8),
            (r"^what makes ", 0.6),
            (r"^what happens when ", 0.6),
            (r"^describe (?:what|how)", 0.6),
        ]

        for pattern, weight in definitional_patterns:
            if re.search(pattern, query_lower):
                confidence = max(confidence + weight, 0.0)
                break

        # Factual patterns (moderately likely open-domain)
        factual_patterns = [
            (r"how many .* (?:are there|exist)", 0.6),
            (r"how much ", 0.4),
            (r"when was .* (?:invented|discovered|founded|created)", 0.7),
            (r"where is .* located", 0.6),
            (r"what country ", 0.6),
            (r"what year ", 0.5),
            (r"capital of", 0.7),
            (r"president of", 0.6),
            (r"population of", 0.7),
            (r"(?:largest|smallest|oldest|youngest|tallest|shortest) .* in the world", 0.7),
        ]

        for pattern, weight in factual_patterns:
            if re.search(pattern, query_lower):
                confidence = max(confidence + weight, 0.0)
                break

        # Scientific/technical terms boost open-domain confidence
        scientific_terms = {
            "photosynthesis", "evolution", "gravity", "atom", "molecule",
            "electron", "proton", "neutron", "cell", "dna", "rna",
            "gene", "chromosome", "protein", "enzyme", "virus", "bacteria",
            "planet", "star", "galaxy", "solar system", "orbit", "mass",
            "energy", "force", "momentum", "velocity", "acceleration",
            "temperature", "pressure", "volume", "density", "climate",
            "ecosystem", "species", "habitat", "evolution", "genetics",
        }

        for term in scientific_terms:
            if term in query_lower:
                confidence += 0.3
                break

        # Absence of conversational markers increases open-domain likelihood
        conversational_markers = [
            r"\bshe\b", r"\bhe\b", r"\bthey\b", r"\bwe\b",
            r"\byesterday\b", r"\blast week\b", r"\btoday\b",
            r"\bour\b", r"\bmy\b", r"\byour\b",
            r"\bthe meeting\b", r"\bthe conversation\b",
            r"\btold me\b", r"\bsaid\b", r"\basked\b",
        ]

        has_conversational = any(re.search(p, query_lower) for p in conversational_markers)
        if not has_conversational:
            confidence += 0.2

        # Cap confidence
        confidence = max(0.0, min(1.0, confidence))

        is_open = confidence >= 0.5
        return is_open, confidence

    def _extract_entities(self, query: str) -> list[str]:
        """Extract entity names from query.

        THE PLAN Phase 2a Enhancement:
        Lower reliance on capitalization for entity extraction.

        We now use multiple signals:
        1. Capitalization (traditional approach)
        2. Known entities from context (case-insensitive)
        3. Named entity patterns (titles, proper noun indicators)
        4. Quotation-marked text (explicit entity reference)
        5. Possessive patterns ("X's" often indicates entity)
        """
        entities = []
        seen_lower = set()  # Track seen entities (lowercase) to avoid duplicates

        query_lower = query.lower()

        # 1. Extract known entities first (case-insensitive)
        for entity in self.known_entities:
            if entity.lower() in query_lower and entity.lower() not in seen_lower:
                entities.append(entity)
                seen_lower.add(entity.lower())

        # 2. Find capitalized words (potential names)
        # Enhanced: Also look for consecutive capitalized words (full names)
        words = query.split()
        i = 0
        while i < len(words):
            word = words[i]
            clean_word = re.sub(r'[^\w]', '', word)

            if clean_word and clean_word[0].isupper():
                # Check for multi-word names (e.g., "New York", "John Smith")
                name_parts = [clean_word]
                j = i + 1
                while j < len(words):
                    next_word = words[j]
                    next_clean = re.sub(r'[^\w]', '', next_word)
                    if next_clean and next_clean[0].isupper() and next_clean not in self._COMMON_TITLE_WORDS:
                        name_parts.append(next_clean)
                        j += 1
                    else:
                        break

                # Add full name if multi-word, or single capitalized word
                full_name = " ".join(name_parts)
                if full_name.lower() not in seen_lower:
                    # Skip if it's the first word and not a known entity
                    if i > 0 or full_name.lower() in {e.lower() for e in self.known_entities}:
                        entities.append(full_name)
                        seen_lower.add(full_name.lower())
                        # Also add individual parts for matching
                        for part in name_parts:
                            if part.lower() not in seen_lower:
                                entities.append(part)
                                seen_lower.add(part.lower())

                i = j
                continue

            i += 1

        # 3. Extract possessive patterns (e.g., "X's job" -> X is an entity)
        possessive_pattern = r"(\w+)'s\s+"
        for match in re.finditer(possessive_pattern, query):
            entity = match.group(1)
            if entity.lower() not in seen_lower and entity.lower() not in {"what", "who", "it", "that", "this"}:
                entities.append(entity)
                seen_lower.add(entity.lower())

        # 4. Extract quoted text as potential entities
        quoted_pattern = r'"([^"]+)"|\'([^\']+)\''
        for match in re.finditer(quoted_pattern, query):
            quoted = match.group(1) or match.group(2)
            if quoted and quoted.lower() not in seen_lower:
                entities.append(quoted)
                seen_lower.add(quoted.lower())

        # 5. Extract terms following entity indicators
        # Patterns like "named X", "called X", "person X", "user X"
        indicator_patterns = [
            r"\b(?:named|called|known as)\s+(\w+)",
            r"\b(?:person|user|member|friend|colleague|partner)\s+(\w+)",
            r"\b(?:mr\.|mrs\.|ms\.|dr\.)\s*(\w+)",
        ]
        for pattern in indicator_patterns:
            for match in re.finditer(pattern, query_lower):
                entity = match.group(1)
                if entity.lower() not in seen_lower:
                    # Capitalize the first letter
                    entities.append(entity.capitalize())
                    seen_lower.add(entity.lower())

        return entities

    # Common title words that shouldn't start multi-word entity names
    _COMMON_TITLE_WORDS = {"The", "A", "An", "And", "Or", "But", "In", "On", "At", "To", "For", "With", "By"}

    def _extract_rare_terms(self, query: str) -> list[str]:
        """Extract rare/specific terms that should trigger keyword retrieval.

        THE PLAN Phase 2a Enhancement:
        Add a rare-term detector that forces keyword/lexical retrieval
        when embeddings alone are weak.

        Rare terms include:
        1. Technical/domain-specific words
        2. Unusual nouns not in common vocabulary
        3. Specific product/brand names
        4. Acronyms and abbreviations
        5. Numbers with units (specific quantities)
        """
        rare_terms = []
        query_lower = query.lower()

        # Common words to exclude (high frequency)
        COMMON_WORDS = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "must", "shall", "can", "need", "dare",
            "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
            "into", "through", "during", "before", "after", "above", "below",
            "between", "under", "again", "further", "then", "once", "here",
            "there", "when", "where", "why", "how", "all", "each", "few",
            "more", "most", "other", "some", "such", "no", "nor", "not",
            "only", "own", "same", "so", "than", "too", "very", "just",
            "and", "but", "if", "or", "because", "as", "until", "while",
            "about", "against", "between", "into", "through", "during",
            "what", "which", "who", "whom", "this", "that", "these", "those",
            "am", "is", "are", "was", "were", "be", "been", "being",
            "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
            "you", "your", "yours", "yourself", "yourselves", "he", "him",
            "his", "himself", "she", "her", "hers", "herself", "it", "its",
            "itself", "they", "them", "their", "theirs", "themselves",
            "say", "said", "tell", "told", "ask", "asked", "go", "went",
            "going", "get", "got", "make", "made", "know", "knew", "think",
            "thought", "take", "took", "see", "saw", "come", "came", "want",
            "wanted", "use", "used", "find", "found", "give", "gave",
            "work", "worked", "call", "called", "try", "tried", "feel",
            "felt", "become", "became", "leave", "left", "put", "mean",
            "meant", "keep", "kept", "let", "begin", "began", "seem",
            "seemed", "help", "helped", "show", "showed", "hear", "heard",
            "play", "played", "run", "ran", "move", "moved", "live", "lived",
            "did", "does", "doing", "done",
        }

        # Extract words
        words = re.findall(r'\b[a-z]{3,}\b', query_lower)

        for word in words:
            if word not in COMMON_WORDS:
                # Check if it's rare based on various heuristics
                is_rare = False

                # Heuristic 1: Technical/domain-specific suffixes
                technical_suffixes = (
                    "tion", "sion", "ment", "ness", "ity", "ology",
                    "graphy", "metry", "scopy", "ism", "ist", "ize",
                    "ification", "ation"
                )
                if any(word.endswith(suffix) for suffix in technical_suffixes):
                    is_rare = True

                # Heuristic 2: Compound words (unusual character patterns)
                if "-" in word or "_" in word:
                    is_rare = True

                # Heuristic 3: Words not commonly used in conversation
                if len(word) > 8:  # Longer words tend to be more specific
                    is_rare = True

                if is_rare:
                    rare_terms.append(word)

        # Extract acronyms (all caps, 2-6 letters)
        acronyms = re.findall(r'\b[A-Z]{2,6}\b', query)
        rare_terms.extend(a.lower() for a in acronyms)

        # Extract numbers with units (specific quantities)
        quantity_pattern = r'\b(\d+(?:\.\d+)?)\s*(mg|kg|lb|oz|ml|L|cm|m|km|mph|kph|%|dollars?|euros?|pounds?)\b'
        for match in re.finditer(quantity_pattern, query, re.IGNORECASE):
            rare_terms.append(match.group(0).lower())

        return list(set(rare_terms))  # Deduplicate

    def _extract_temporal_markers(self, query_lower: str) -> list[str]:
        """Extract temporal markers from query."""
        markers = []

        temporal_words = [
            "yesterday", "today", "tomorrow", "morning", "afternoon", "evening", "night",
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "january", "february", "march", "april", "may", "june", "july", "august",
            "september", "october", "november", "december",
            "last week", "next week", "last month", "next month", "last year", "next year",
            "ago", "later", "before", "after", "during", "while", "when"
        ]

        for word in temporal_words:
            if word in query_lower:
                markers.append(word)

        # Also look for date patterns
        date_pattern = r'\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?'
        dates = re.findall(date_pattern, query_lower)
        markers.extend(dates)

        return markers

    def _classify_query(
        self,
        query: str,
        query_lower: str,
        entities: list[str],
        temporal_markers: list[str]
    ) -> tuple[QueryType, str | None, float]:
        """Classify query type and extract primary subject."""

        # Check entity patterns
        for pattern, q_type in self.ENTITY_PATTERNS:
            match = re.search(pattern, query_lower)
            if match:
                subject = match.group(1) if match.groups() else None
                # Capitalize subject if it matches a known entity
                if subject:
                    for entity in entities:
                        if entity.lower() == subject.lower():
                            subject = entity
                            break
                return QueryType[q_type.upper()], subject, 0.8

        # Check temporal patterns
        for pattern, q_type in self.TEMPORAL_PATTERNS:
            if re.search(pattern, query_lower):
                subject = entities[0] if entities else None
                return QueryType.TEMPORAL_WHEN if "when" in q_type else QueryType.TEMPORAL_SEQUENCE, subject, 0.75

        # If has temporal markers and entities, likely temporal query
        if temporal_markers and entities:
            return QueryType.TEMPORAL_WHEN, entities[0], 0.6

        # If has entity and asking direct question, entity-focused
        if entities and any(w in query_lower for w in ["what", "where", "who", "how"]):
            return QueryType.ENTITY_ACTION, entities[0], 0.6

        # Multi-hop detection: "the person who", "the one that", indirect references
        multi_hop_patterns = [
            r"the\s+(?:person|one|man|woman)\s+who",
            r"(?:whose|which)\s+\w+\s+(?:is|was|did)",
            r"the\s+\w+\s+that\s+\w+\s+mentioned"
        ]
        for pattern in multi_hop_patterns:
            if re.search(pattern, query_lower):
                return QueryType.MULTI_HOP, entities[0] if entities else None, 0.7

        return QueryType.OPEN_DOMAIN, entities[0] if entities else None, 0.5

    def _is_asking_about_speech(self, query_lower: str) -> bool:
        """Check if query is asking about what someone SAID."""
        speech_indicators = [
            "say", "said", "tell", "told", "mention", "mentioned",
            "talk", "talked", "speak", "spoke", "discuss", "discussed",
            "explain", "explained", "describe", "described"
        ]
        return any(ind in query_lower for ind in speech_indicators)

    def _detect_list_question(self, query: str, query_lower: str) -> tuple[bool, str | None]:
        """Detect if query is asking for a list/multiple items.

        SINGLE-HOP OPTIMIZATION:
        List questions like "What books has X read?" need multiple distinct answers.
        We need to:
        1. Retrieve multiple distinct nodes (not just top-1)
        2. Avoid deduplication that removes valid answers
        3. Bias toward multiple supporting nodes in ranking

        List question patterns:
        - Plural nouns: "what books", "which movies", "what types"
        - Aggregation words: "all", "every", "each", "any"
        - Enumeration: "list", "name all", "what are all"
        - "What X have/has Y done?" pattern

        Returns:
            Tuple of (is_list_question, list_type)
        """
        # Pattern 1: Plural noun questions
        plural_patterns = [
            r"what\s+(?:are\s+)?(?:the\s+)?(\w+s)\b(?!\s+(?:his|her|their|is|was))",
            r"which\s+(\w+s)\b(?!\s+(?:his|her|their|is|was))",
            r"what\s+types?\s+of",
            r"what\s+kinds?\s+of",
            r"how\s+many\s+(\w+)",
        ]

        for pattern in plural_patterns:
            match = re.search(pattern, query_lower)
            if match:
                # Check if matched word is actually plural (ends in s, not a name)
                word = match.group(1) if match.groups() else ""
                if word and word.endswith("s") and word not in {"is", "was", "has", "does", "his", "hers"}:
                    return True, "plural"

        # Pattern 2: Aggregation words
        aggregation_patterns = [
            r"\ball\s+(?:the\s+)?(?:of\s+)?",
            r"\bevery\s+",
            r"\beach\s+",
            r"\blist\s+(?:all\s+)?",
            r"\bname\s+(?:all\s+)?",
            r"\bwhat\s+are\s+all\s+",
        ]

        for pattern in aggregation_patterns:
            if re.search(pattern, query_lower):
                return True, "aggregation"

        # Pattern 3: "have/has done" with plural object
        have_done_patterns = [
            r"(?:has|have)\s+\w+\s+(?:read|watched|seen|done|made|created|written|visited|bought|painted)",
            r"(?:has|have)\s+\w+\s+(?:been\s+to|gone\s+to)",
            r"what\s+(?:has|have)\s+\w+\s+\w+ed\b",
        ]

        for pattern in have_done_patterns:
            if re.search(pattern, query_lower):
                return True, "plural"

        # Pattern 4: "What do X and Y have in common?"
        common_pattern = r"(?:have|do|did)\s+(?:\w+\s+)?(?:and\s+\w+\s+)?(?:have\s+)?in\s+common"
        if re.search(common_pattern, query_lower):
            return True, "aggregation"

        # Pattern 5: Explicit "both" questions
        if "both" in query_lower:
            return True, "aggregation"

        return False, None

    def _should_suppress_multi_hop(self, analysis: "QueryAnalysis") -> bool:
        """Determine if multi-hop expansion should be suppressed.

        SINGLE-HOP OPTIMIZATION:
        Multi-hop expansion can swamp rankings with loosely related nodes.
        Suppress it for clear single-hop queries.

        Suppress when:
        - Query type is ENTITY_ATTRIBUTE (simple fact lookup)
        - High confidence in single-hop classification
        - No multi-hop indicators in query

        Returns:
            True if multi-hop expansion should be suppressed
        """
        # Always suppress for pure entity attribute queries
        if analysis.query_type == QueryType.ENTITY_ATTRIBUTE:
            return True

        # Suppress for entity action with high confidence
        if analysis.query_type == QueryType.ENTITY_ACTION and analysis.confidence >= 0.7:
            return True

        # Suppress for temporal queries (they use temporal chain, not multi-hop)
        if analysis.query_type in {QueryType.TEMPORAL_WHEN, QueryType.TEMPORAL_SEQUENCE}:
            return True

        # Don't suppress for explicit multi-hop
        if analysis.query_type == QueryType.MULTI_HOP:
            return False

        # Don't suppress for low confidence
        if analysis.confidence < 0.6:
            return False

        # Check for multi-hop indicators in query
        query_lower = analysis.query_text.lower()
        multi_hop_indicators = [
            "the person who", "the one who", "whoever", "someone who",
            "after", "before", "because", "since", "then"
        ]
        if any(ind in query_lower for ind in multi_hop_indicators):
            return False

        return True

    def _determine_strategy(self, analysis: QueryAnalysis) -> tuple[str, dict]:
        """Determine retrieval strategy based on analysis."""
        params = {}

        # ENTITY-FOCUSED: Filter by entity first
        if analysis.query_type in {QueryType.ENTITY_ATTRIBUTE, QueryType.ENTITY_ACTION, QueryType.ENTITY_RELATION}:
            if analysis.primary_subject:
                params["entity_filter"] = analysis.primary_subject
                params["include_speaker_messages"] = True
                if analysis.speaker_filter:
                    params["speaker_filter"] = analysis.speaker_filter
                return "entity_filter", params

        # TEMPORAL: Use temporal chain traversal
        if analysis.query_type in {QueryType.TEMPORAL_WHEN, QueryType.TEMPORAL_SEQUENCE, QueryType.TEMPORAL_DURATION}:
            params["temporal_markers"] = analysis.temporal_markers
            if analysis.primary_subject:
                params["entity_filter"] = analysis.primary_subject
            return "temporal_filter", params

        # MULTI-HOP: Need two-stage retrieval
        if analysis.query_type == QueryType.MULTI_HOP:
            params["multi_hop"] = True
            params["entities"] = analysis.entities
            return "multi_hop", params

        # ADVERSARIAL: Need verification pass
        if analysis.query_type == QueryType.ADVERSARIAL:
            params["verify_negation"] = analysis.has_negation
            params["entities"] = analysis.entities
            return "adversarial", params

        # Default: Standard semantic search
        return "semantic", params


class FilteredRetriever:
    """Retrieves messages using query-aware filtering.

    THE CORE IDEA:
    Instead of searching ALL messages, we first FILTER to a relevant subset
    based on the query analysis, then search within that subset.

    This dramatically improves accuracy for entity-focused queries because:
    - "What is Caroline's job?" + filter(speaker=Caroline)
    - Now "I work as a teacher" is in the candidate set!

    FILTERING MODES (THE PLAN Enhancement):
    - "hard": Remove non-matching candidates entirely (original behavior, causes regression)
    - "soft": Apply score penalties to non-matching candidates (maintains recall)
    - "boost": Apply score BOOSTS to matching candidates (NEW - best for single-hop)
              Non-matches keep original score (1.0), matches get boosted (1.5+)

    Boost mode is recommended for single-hop factual questions because:
    1. Maintains full recall (no candidates removed)
    2. Creates larger score separation than penalty mode
    3. Works better with reranker (full candidate pool preserved)
    """

    # Filtering mode constants
    MODE_HARD = "hard"
    MODE_SOFT = "soft"
    MODE_BOOST = "boost"  # NEW: Boost matches instead of penalizing non-matches

    # Boost multipliers for different match types (THE PLAN: "boosting matches")
    BOOST_EXACT_SPEAKER = 1.8
    BOOST_ALIAS_SPEAKER = 1.6
    BOOST_FUZZY_SPEAKER = 1.5
    BOOST_ENTITY_ID = 1.4
    BOOST_CONTENT_EXACT = 1.25
    BOOST_CONTENT_FUZZY = 1.15
    BOOST_TEMPORAL_MATCH = 1.3
    BOOST_NO_MATCH = 1.0  # No penalty in boost mode - maintains recall

    def __init__(
        self,
        storage: "NeuralGraphStorage",
        filter_mode: str = "boost",  # Default to boost mode for single-hop optimization
        soft_fallback_threshold: float = 0.2,  # Lower threshold for soft filter fallback
    ):
        self.storage = storage
        self.router = QueryRouter()
        self.filter_mode = filter_mode
        self.soft_fallback_threshold = soft_fallback_threshold

        # Debug statistics
        self._filter_stats = {
            "total_queries": 0,
            "entity_queries": 0,
            "temporal_queries": 0,
            "candidates_filtered": 0,
            "candidates_retained": 0,
            "fallback_triggered": 0,
            "boost_mode_queries": 0,
            "avg_boost_factor": 0.0,
        }

    def update_context(self, nodes: list["NeuralNode"]) -> None:
        """Update router with known entities from conversation."""
        entities = set()
        for node in nodes:
            # Extract speaker
            if node.metadata:
                speaker = node.speaker_id
                if speaker:
                    entities.add(speaker)

            # Extract mentioned entities
            if node.entity_ids:
                entities.update(node.entity_ids)

        self.router.update_known_entities(entities)

    async def get_filtered_candidates(
        self,
        query: str,
        session_key: str,
        base_candidates: list["NeuralNode"] | None = None
    ) -> tuple[list["NeuralNode"], QueryAnalysis, dict[str, SoftFilterResult] | None]:
        """Get filtered candidate nodes based on query analysis.

        Args:
            query: The question
            session_key: Session identifier
            base_candidates: Optional pre-retrieved candidates to filter

        Returns:
            Tuple of (filtered_nodes, analysis, soft_filter_results)
            - filtered_nodes: List of candidate nodes (all for soft mode, filtered for hard mode)
            - analysis: Query analysis result
            - soft_filter_results: Dict of node_id -> SoftFilterResult (only in soft mode)
        """
        self._filter_stats["total_queries"] += 1
        analysis = self.router.analyze(query)

        logger.debug(
            f"Query analysis: type={analysis.query_type.value}, "
            f"strategy={analysis.strategy}, subject={analysis.primary_subject}, "
            f"entities={analysis.entities}"
        )

        # Get all nodes if no base candidates
        if base_candidates is None:
            base_candidates = await self._get_session_nodes(session_key)

        logger.debug(f"Base candidates: {len(base_candidates)}")

        soft_results: dict[str, SoftFilterResult] | None = None

        # Apply filters based on strategy and mode
        # THE PLAN: "promote entity and time filters in soft mode by boosting matches"
        if analysis.strategy == "entity_filter":
            self._filter_stats["entity_queries"] += 1
            if self.filter_mode == self.MODE_BOOST:
                # BOOST MODE: All candidates returned, matches get score boost
                self._filter_stats["boost_mode_queries"] += 1
                filtered, soft_results = self._apply_entity_filter_boost(
                    base_candidates, analysis.filter_params
                )
            elif self.filter_mode == self.MODE_SOFT:
                # SOFT MODE: Return all candidates with score modifiers (penalties)
                filtered, soft_results = self._apply_entity_filter_soft(
                    base_candidates, analysis.filter_params
                )
            else:
                # HARD MODE: Return only matching candidates (original behavior)
                filtered = self._apply_entity_filter_hard(base_candidates, analysis.filter_params)
        elif analysis.strategy == "temporal_filter":
            self._filter_stats["temporal_queries"] += 1
            if self.filter_mode == self.MODE_BOOST:
                self._filter_stats["boost_mode_queries"] += 1
                filtered, soft_results = self._apply_temporal_filter_boost(
                    base_candidates, analysis.filter_params
                )
            elif self.filter_mode == self.MODE_SOFT:
                filtered, soft_results = self._apply_temporal_filter_soft(
                    base_candidates, analysis.filter_params
                )
            else:
                filtered = self._apply_temporal_filter_hard(base_candidates, analysis.filter_params)
        elif analysis.strategy == "multi_hop":
            # For multi-hop, use boost mode if enabled, else soft
            if self.filter_mode == self.MODE_BOOST:
                self._filter_stats["boost_mode_queries"] += 1
                filtered, soft_results = self._apply_entity_filter_boost(
                    base_candidates, analysis.filter_params
                )
            elif self.filter_mode == self.MODE_SOFT:
                filtered, soft_results = self._apply_entity_filter_soft(
                    base_candidates, analysis.filter_params
                )
            else:
                filtered = self._apply_entity_filter_hard(base_candidates, analysis.filter_params)
        else:
            # Semantic or adversarial: return all candidates
            filtered = base_candidates

        # Log statistics
        self._filter_stats["candidates_retained"] += len(filtered)
        self._filter_stats["candidates_filtered"] += len(base_candidates) - len(filtered)

        logger.debug(
            f"Filtering result: {len(filtered)}/{len(base_candidates)} retained, "
            f"mode={self.filter_mode}"
        )

        return filtered, analysis, soft_results

    def get_filter_statistics(self) -> dict[str, int]:
        """Get debugging statistics about filtering behavior."""
        return self._filter_stats.copy()

    async def _get_session_nodes(self, session_key: str) -> list["NeuralNode"]:
        """Get all nodes for a session."""
        from .data_types import NodeLayer
        # Get message-layer nodes
        return await self.storage.get_nodes_by_layer(session_key, NodeLayer.MESSAGE)

    def _apply_entity_filter_soft(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> tuple[list["NeuralNode"], dict[str, SoftFilterResult]]:
        """Apply SOFT entity filtering - all candidates returned with score modifiers.

        SOFT FILTERING ADVANTAGE:
        Instead of removing candidates that don't match, we return ALL candidates
        with score modifiers. This maintains recall (we don't miss good candidates)
        while improving precision (matching candidates get higher scores).

        Args:
            candidates: List of candidate nodes
            params: Filter parameters with entity_filter, speaker_filter, etc.

        Returns:
            Tuple of (all_candidates, soft_filter_results_dict)
        """
        entity = params.get("entity_filter", "")
        speaker_filter_param = params.get("speaker_filter", "")

        if not entity:
            # No entity filter - all candidates get full score
            soft_results = {
                node.node_id: SoftFilterResult(
                    node_id=node.node_id,
                    score_modifier=1.0,
                    match_reason="no_entity_filter"
                )
                for node in candidates
            }
            return candidates, soft_results

        soft_results: dict[str, SoftFilterResult] = {}
        match_counts = {"speaker": 0, "entity_id": 0, "content": 0, "no_match": 0}

        for node in candidates:
            # Get node's speaker from metadata
            speaker = None
            if node.metadata:
                speaker = node.speaker_id

            # Use the comprehensive entity matching function
            result = _match_entity(
                query_entity=entity,
                speaker=speaker,
                content=node.content or "",
                entity_ids=node.entity_ids,
                fuzzy_threshold=0.7
            )
            result.node_id = node.node_id

            # Also check speaker_filter if provided (additional constraint)
            if speaker_filter_param and speaker:
                speaker_match = _match_entity(
                    query_entity=speaker_filter_param,
                    speaker=speaker,
                    content="",  # Only check speaker for speaker_filter
                    entity_ids=None,
                    fuzzy_threshold=0.7
                )
                if speaker_match.is_speaker_match:
                    # Boost score for speaker filter match
                    result.score_modifier = min(1.5, result.score_modifier * 1.2)
                    result.match_reason += " + speaker_filter_boost"

            soft_results[node.node_id] = result

            # Track statistics
            if result.is_speaker_match:
                match_counts["speaker"] += 1
            elif result.is_entity_id_match:
                match_counts["entity_id"] += 1
            elif result.is_content_match:
                match_counts["content"] += 1
            else:
                match_counts["no_match"] += 1

        logger.debug(
            f"Soft entity filter results for '{entity}': "
            f"speaker={match_counts['speaker']}, entity_id={match_counts['entity_id']}, "
            f"content={match_counts['content']}, no_match={match_counts['no_match']}"
        )

        return candidates, soft_results

    def _apply_entity_filter_hard(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> list["NeuralNode"]:
        """Apply HARD entity filtering - non-matching candidates removed.

        HARD FILTERING (Original behavior with improvements):
        Removes candidates that don't match the entity filter.
        Now uses fuzzy matching and improved fallback.

        WARNING: This mode can cause over-filtering regression.
        Use soft filtering mode instead for better accuracy.

        Args:
            candidates: List of candidate nodes
            params: Filter parameters

        Returns:
            Filtered list of candidates
        """
        entity = params.get("entity_filter", "")
        speaker_filter_param = params.get("speaker_filter", "")

        if not entity:
            return candidates

        # First pass: collect all matches with their quality scores
        matches: list[tuple["NeuralNode", SoftFilterResult]] = []

        for node in candidates:
            speaker = None
            if node.metadata:
                speaker = node.speaker_id

            result = _match_entity(
                query_entity=entity,
                speaker=speaker,
                content=node.content or "",
                entity_ids=node.entity_ids,
                fuzzy_threshold=0.7
            )
            result.node_id = node.node_id

            # Keep nodes with score_modifier >= 0.5 (at least a weak match)
            if result.score_modifier >= 0.5:
                matches.append((node, result))

        # Sort by match quality
        matches.sort(key=lambda x: x[1].score_modifier, reverse=True)
        filtered = [node for node, _ in matches]

        # IMPROVED FALLBACK: Trigger at 20% threshold instead of 10%
        # and add high-scoring semantic candidates instead of random ones
        fallback_threshold = max(self.soft_fallback_threshold, 0.2)
        min_candidates = 20

        if len(filtered) < len(candidates) * fallback_threshold and len(filtered) < min_candidates:
            self._filter_stats["fallback_triggered"] += 1
            logger.warning(
                f"Entity filter fallback triggered: only {len(filtered)}/{len(candidates)} "
                f"matched '{entity}', adding more candidates"
            )

            # Add unmatched candidates sorted by their soft filter score
            filtered_ids = {n.node_id for n in filtered}
            remaining = []

            for node in candidates:
                if node.node_id in filtered_ids:
                    continue

                speaker = None
                if node.metadata:
                    speaker = node.speaker_id

                result = _match_entity(
                    query_entity=entity,
                    speaker=speaker,
                    content=node.content or "",
                    entity_ids=node.entity_ids,
                    fuzzy_threshold=0.7
                )
                remaining.append((node, result.score_modifier))

            # Add remaining sorted by score (best non-matches first)
            remaining.sort(key=lambda x: x[1], reverse=True)

            for node, _ in remaining:
                if len(filtered) >= min_candidates:
                    break
                filtered.append(node)

        return filtered if filtered else candidates

    def _apply_temporal_filter_soft(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> tuple[list["NeuralNode"], dict[str, SoftFilterResult]]:
        """Apply SOFT temporal filtering - all candidates returned with score modifiers.

        Args:
            candidates: List of candidate nodes
            params: Filter parameters with temporal_markers and optional entity_filter

        Returns:
            Tuple of (all_candidates, soft_filter_results_dict)
        """
        temporal_markers = params.get("temporal_markers", [])
        entity = params.get("entity_filter", "")

        soft_results: dict[str, SoftFilterResult] = {}

        for node in candidates:
            content_lower = (node.content or "").lower()
            score_modifier = 0.3  # Base score for temporal queries
            match_reason = "no_temporal_match"

            # Check temporal markers in content
            matched_markers = [m for m in temporal_markers if m in content_lower]
            if matched_markers:
                # More markers = higher score
                marker_boost = min(0.5, 0.15 * len(matched_markers))
                score_modifier = 0.7 + marker_boost
                match_reason = f"temporal_marker_match ({', '.join(matched_markers)})"

            # Check timestamp metadata
            if node.metadata and node.metadata.get("timestamp"):
                # Node has explicit timestamp - boost slightly
                score_modifier = max(score_modifier, 0.6)
                if match_reason == "no_temporal_match":
                    match_reason = "has_timestamp"

            # Check entity if provided (combined temporal+entity query)
            if entity:
                speaker = node.speaker_id if node.metadata else None
                entity_result = _match_entity(
                    query_entity=entity,
                    speaker=speaker,
                    content=node.content or "",
                    entity_ids=node.entity_ids,
                    fuzzy_threshold=0.7
                )

                if entity_result.score_modifier >= 0.5:
                    # Combine temporal and entity scores
                    combined = (score_modifier + entity_result.score_modifier) / 2
                    if combined > score_modifier:
                        score_modifier = combined
                        match_reason += f" + entity_match ({entity_result.match_reason})"

            soft_results[node.node_id] = SoftFilterResult(
                node_id=node.node_id,
                score_modifier=score_modifier,
                match_reason=match_reason
            )

        return candidates, soft_results

    def _apply_temporal_filter_hard(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> list["NeuralNode"]:
        """Apply HARD temporal filtering - non-matching candidates removed.

        Args:
            candidates: List of candidate nodes
            params: Filter parameters

        Returns:
            Filtered list of candidates
        """
        temporal_markers = params.get("temporal_markers", [])
        entity = params.get("entity_filter", "")

        if not temporal_markers and not entity:
            return candidates

        filtered = []
        for node in candidates:
            content_lower = (node.content or "").lower()

            # Check temporal markers in content
            has_temporal = any(marker in content_lower for marker in temporal_markers)

            # Check entity if provided - use improved matching
            has_entity = True
            if entity:
                speaker = node.speaker_id if node.metadata else None
                entity_result = _match_entity(
                    query_entity=entity,
                    speaker=speaker,
                    content=node.content or "",
                    entity_ids=node.entity_ids,
                    fuzzy_threshold=0.7
                )
                has_entity = entity_result.score_modifier >= 0.5

            if has_temporal or has_entity:
                filtered.append(node)

        return filtered if filtered else candidates

    def _apply_entity_filter_boost(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> tuple[list["NeuralNode"], dict[str, SoftFilterResult]]:
        """Apply BOOST entity filtering - matches get score boost, non-matches unchanged.

        THE PLAN IMPLEMENTATION:
        "Promote entity and time filters in soft mode by boosting matches
        instead of only penalizing non-matches; keep a fallback to avoid hard drops."

        BOOST MODE ADVANTAGE over SOFT MODE:
        - Non-matches keep score_modifier = 1.0 (not 0.2)
        - Matches get score_modifier = 1.5-1.8 (strong boost)
        - Creates larger score separation without losing candidates
        - Better for reranker since full pool is preserved

        Args:
            candidates: List of candidate nodes
            params: Filter parameters with entity_filter, speaker_filter, etc.

        Returns:
            Tuple of (all_candidates, boost_results_dict)
        """
        entity = params.get("entity_filter", "")
        speaker_filter_param = params.get("speaker_filter", "")

        if not entity:
            # No entity filter - all candidates get neutral score
            boost_results = {
                node.node_id: SoftFilterResult(
                    node_id=node.node_id,
                    score_modifier=1.0,
                    match_reason="no_entity_filter"
                )
                for node in candidates
            }
            return candidates, boost_results

        boost_results: dict[str, SoftFilterResult] = {}
        match_counts = {"boosted": 0, "no_boost": 0}
        total_boost = 0.0

        for node in candidates:
            speaker = None
            if node.metadata:
                speaker = node.speaker_id

            # Get match result from the standard matcher
            result = _match_entity(
                query_entity=entity,
                speaker=speaker,
                content=node.content or "",
                entity_ids=node.entity_ids,
                fuzzy_threshold=0.7
            )
            result.node_id = node.node_id

            # Convert penalty-mode score to boost-mode score
            # Original: 1.0 for match, 0.2 for no match
            # Boost: 1.5-1.8 for match, 1.0 for no match
            if result.is_speaker_match:
                if result.is_fuzzy_match:
                    boost = self.BOOST_FUZZY_SPEAKER if result.score_modifier < 0.9 else self.BOOST_ALIAS_SPEAKER
                else:
                    boost = self.BOOST_EXACT_SPEAKER
                result.score_modifier = boost
                result.boost_applied = boost - 1.0
                result.match_reason = f"boost:{result.match_reason}"
                match_counts["boosted"] += 1
                total_boost += boost

            elif result.is_entity_id_match:
                boost = self.BOOST_ENTITY_ID
                result.score_modifier = boost
                result.boost_applied = boost - 1.0
                result.match_reason = f"boost:{result.match_reason}"
                match_counts["boosted"] += 1
                total_boost += boost

            elif result.is_content_match:
                if result.is_fuzzy_match:
                    boost = self.BOOST_CONTENT_FUZZY
                else:
                    boost = self.BOOST_CONTENT_EXACT
                result.score_modifier = boost
                result.boost_applied = boost - 1.0
                result.match_reason = f"boost:{result.match_reason}"
                match_counts["boosted"] += 1
                total_boost += boost

            else:
                # NO MATCH: Keep at 1.0 (no penalty in boost mode)
                result.score_modifier = self.BOOST_NO_MATCH
                result.boost_applied = 0.0
                result.match_reason = "no_boost"
                match_counts["no_boost"] += 1

            # Additional boost for speaker_filter match
            if speaker_filter_param and speaker:
                speaker_match = _match_entity(
                    query_entity=speaker_filter_param,
                    speaker=speaker,
                    content="",
                    entity_ids=None,
                    fuzzy_threshold=0.7
                )
                if speaker_match.is_speaker_match:
                    # Stack boost
                    additional_boost = 0.2
                    result.score_modifier += additional_boost
                    result.boost_applied += additional_boost
                    result.match_reason += " + speaker_filter_stack"

            boost_results[node.node_id] = result

        # Update statistics
        if match_counts["boosted"] > 0:
            self._filter_stats["avg_boost_factor"] = total_boost / match_counts["boosted"]

        logger.debug(
            f"Boost entity filter results for '{entity}': "
            f"boosted={match_counts['boosted']}, no_boost={match_counts['no_boost']}, "
            f"avg_boost={self._filter_stats['avg_boost_factor']:.2f}"
        )

        return candidates, boost_results

    def _apply_temporal_filter_boost(
        self,
        candidates: list["NeuralNode"],
        params: dict
    ) -> tuple[list["NeuralNode"], dict[str, SoftFilterResult]]:
        """Apply BOOST temporal filtering - matches get score boost, non-matches unchanged.

        THE PLAN IMPLEMENTATION:
        "Add temporal consistency boosts for answers whose timestamps overlap
        the query time window"

        Args:
            candidates: List of candidate nodes
            params: Filter parameters with temporal_markers and optional entity_filter

        Returns:
            Tuple of (all_candidates, boost_results_dict)
        """
        temporal_markers = params.get("temporal_markers", [])
        entity = params.get("entity_filter", "")

        boost_results: dict[str, SoftFilterResult] = {}
        match_counts = {"temporal_boost": 0, "entity_boost": 0, "no_boost": 0}

        for node in candidates:
            content_lower = (node.content or "").lower()
            score_modifier = 1.0  # Base: no boost
            match_reason = "no_temporal_boost"
            boost_applied = 0.0

            # Check temporal markers in content
            matched_markers = [m for m in temporal_markers if m in content_lower]
            if matched_markers:
                # Temporal boost based on marker count
                marker_boost = min(0.4, 0.12 * len(matched_markers))
                score_modifier = self.BOOST_TEMPORAL_MATCH + marker_boost
                boost_applied = score_modifier - 1.0
                match_reason = f"temporal_boost ({', '.join(matched_markers[:3])})"
                match_counts["temporal_boost"] += 1

            # Check timestamp metadata
            if node.metadata and node.metadata.get("timestamp"):
                if score_modifier < self.BOOST_TEMPORAL_MATCH:
                    score_modifier = self.BOOST_TEMPORAL_MATCH - 0.1  # Slightly lower boost
                    boost_applied = score_modifier - 1.0
                    if match_reason == "no_temporal_boost":
                        match_reason = "timestamp_boost"

            # Check entity if provided (combined temporal+entity query)
            if entity:
                speaker = node.speaker_id if node.metadata else None
                entity_result = _match_entity(
                    query_entity=entity,
                    speaker=speaker,
                    content=node.content or "",
                    entity_ids=node.entity_ids,
                    fuzzy_threshold=0.7
                )

                if entity_result.is_speaker_match or entity_result.is_entity_id_match:
                    # Stack entity boost with temporal boost
                    entity_boost = self.BOOST_ENTITY_ID if entity_result.is_entity_id_match else self.BOOST_EXACT_SPEAKER
                    # Combine: take geometric mean for stacking
                    combined = (score_modifier * entity_boost) ** 0.5
                    combined = max(combined, score_modifier, entity_boost)  # At least as good as best single
                    score_modifier = min(2.0, combined)  # Cap at 2.0
                    boost_applied = score_modifier - 1.0
                    match_reason += f" + entity_boost ({entity_result.match_reason})"
                    match_counts["entity_boost"] += 1

            if boost_applied == 0.0:
                match_counts["no_boost"] += 1

            boost_results[node.node_id] = SoftFilterResult(
                node_id=node.node_id,
                score_modifier=score_modifier,
                match_reason=match_reason,
                boost_applied=boost_applied
            )

        logger.debug(
            f"Boost temporal filter results: "
            f"temporal={match_counts['temporal_boost']}, entity={match_counts['entity_boost']}, "
            f"no_boost={match_counts['no_boost']}"
        )

        return candidates, boost_results


# Convenience function
def create_query_router(known_entities: set[str] | None = None) -> QueryRouter:
    """Create a query router with optional known entities."""
    return QueryRouter(known_entities)


def create_filtered_retriever(
    storage: "NeuralGraphStorage",
    filter_mode: str = "boost"
) -> FilteredRetriever:
    """Create a filtered retriever with specified mode.

    THE PLAN Enhancement: Default to "boost" mode for single-hop optimization.

    Args:
        storage: Neural graph storage backend
        filter_mode: Filtering mode:
            - "boost" (default, recommended for single-hop):
              Matches get score boost (1.5-1.8x), non-matches unchanged (1.0x)
            - "soft": Matches get 1.0x, non-matches penalized (0.2x)
            - "hard": Non-matching candidates removed entirely (causes regression)

    Returns:
        FilteredRetriever instance
    """
    return FilteredRetriever(storage, filter_mode=filter_mode)

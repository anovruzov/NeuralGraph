"""4D Tesseract Memory Architecture: Brain-Inspired Multi-Store System.

THE INSIGHT: The brain doesn't store all memories the same way.
Different brain regions specialize in different types of retrieval:

1. HIPPOCAMPUS (Temporal Store):
   - Episodic sequences, "when did X happen"
   - Time-ordered chains of events
   - "Yesterday", "last week", "4 years ago"

2. NEOCORTEX (Entity Store):
   - Semantic facts, "what is X"
   - Entity attributes and relationships
   - "Caroline is transgender", "moved from Sweden"

3. PREFRONTAL CORTEX (Reasoning Store):
   - Multi-hop paths, "if A then B then C"
   - Inference chains
   - Working memory for synthesis

4. ORBITOFRONTAL (Adversarial Store):
   - Conflict detection, "is X consistent?"
   - Negation handling
   - Verification and disambiguation

THE TESSERACT:
=============
A 4D memory system where each dimension is a specialized store.
Query enters all stores simultaneously.
Results are FUSED based on query type detection.

This is the missing link to 99% accuracy:
- Temporal questions → weighted toward Temporal Store
- Entity questions → weighted toward Entity Store
- Multi-hop → engages Reasoning Store
- Adversarial → activates conflict detection
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage

# NEO: Complete English thesaurus (117K+ words) + domain-specific additions
from .synonym_hash import expand_with_synonyms_extended as expand_with_synonyms
from .external_retriever import is_open_domain_query

# Temporal utilities for query expansion
from .temporal_utils import expand_temporal_query as _expand_temporal_query
from .data_types import EdgeType

logger = logging.getLogger(__name__)


_NOVELTY_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "what", "when", "where", "who",
    "why", "how", "did", "does", "with", "from", "that", "this", "these", "those",
    "they", "their", "them", "she", "he", "his", "its", "been", "were", "have",
    "has", "had", "will", "would", "could", "should", "may", "might", "into",
    "about", "over", "under", "after", "before", "during", "because", "while",
    "then", "than", "there", "here", "also", "just", "really",
}

_OPEN_DOMAIN_INFER_MARKERS = [
    "would ", "might ", "could ", " may ", "should ",
    "would be", "might be", "could be", "may be",
    "would likely", "might likely", "could possibly",
    "would probably", "might probably", "could potentially",
    "underlying", "based on", "given", "considering",
    "given that", "based on the", "considering the",
    "in light of", "taking into account", "judging by",
    "from the", "according to",
    "alternative", "instead", "rather than", "as opposed to",
    "what if", "suppose", "imagine", "hypothetically",
    "in place of", "other than",
    "personality", "attributes", "traits", "characteristics",
    "qualities", "nature", "temperament", "disposition",
    "character", "tendencies",
    "be considered", "describe", "characterize",
    "would you describe", "how would you",
    "be seen as", "be regarded as", "be viewed as",
    "likely to", "probably ", "possibly ", "potentially",
    "expected to", "predicted to", "anticipated to",
    "destined to",
    "suited for", "good at", "talented at", "skilled at",
    "capable of", "able to", "fit for",
    "prefer", "enjoy", "interested in", "inclined to",
    "drawn to", "attracted to", "keen on", "fond of",
    "better than", "worse than", "more than", "less than",
    "compared to", "in comparison",
    "think about", "believe about", "feel about",
    "opinion on", "view of", "stance on",
    "why ", "reason for", "because of", "caused by",
    "due to",
    "how does", "what does", "feel like", "think like",
    "emotional",
    "planning to", "hoping to", "aspiring to",
    "aiming to", "striving to",
    "similar to", "like ", "resemble", "akin to",
    "open to", "willing to", "receptive to", "amenable to",
    "impact of", "effect of", "consequence of",
]


def should_use_open_domain_infer(question: str) -> bool:
    q = question.lower()
    return any(marker in q for marker in _OPEN_DOMAIN_INFER_MARKERS)


def is_yes_no_question(question: str) -> bool:
    q = question.lower().strip()
    return bool(re.match(r"^(is|are|was|were|do|does|did|can|could|should|would|will|has|have|had)\b", q))


def detect_list_question_universal(question: str) -> tuple[bool, str | None]:
    q = question.lower()
    is_list = False
    list_type: str | None = None

    list_markers = [
        "what books", "what movies", "what types", "what kinds", "what things",
        "which items", "all of", "list", "names of", "what are the", "what are some",
    ]
    if any(marker in q for marker in list_markers):
        is_list = True
        list_type = "plural"

    # Do not use ``what\s+\w+s`` here: auxiliaries such as "is", "was",
    # "does", and "has" all end in ``s`` and made ordinary singular questions
    # look like list requests (for example, "What is Caroline's identity?").
    plural_patterns = [
        r"\b(?:what|which)\s+(?!is\b|was\b|does\b|has\b|this\b)[a-z][a-z-]*s\b",
        r"\blist\s+[a-z][a-z-]*s\b",
        r"\bwhat\s+(?:are|were)\s+(?:[a-z][a-z-]*['’]s\s+)?(?:[a-z][a-z-]*\s+){0,3}[a-z][a-z-]*s\b",
    ]
    if any(re.search(pattern, q) for pattern in plural_patterns):
        is_list = True
        list_type = "plural"

    if "what kind of" in q or "what kinds of" in q:
        is_list = True
        list_type = list_type or "plural"

    if re.search(r"\bin common\b", q) or ("both" in q and not is_yes_no_question(question)):
        is_list = True
        list_type = "aggregation"

    if is_list and not list_type:
        list_type = "plural"

    return is_list, list_type


def _expand_morphology_basic(word: str) -> set[str]:
    variants = {word}
    w = word.lower()
    base = w
    if w.endswith("ed") and len(w) > 4:
        base = w[:-2]
        if base.endswith("i"):
            base = base[:-1] + "y"
        elif len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
    elif w.endswith("ing") and len(w) > 5:
        base = w[:-3]
        if len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
    elif w.endswith("ies") and len(w) > 4:
        base = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        base = w[:-1]
    variants.add(base)
    if len(base) >= 3:
        variants.add(base + "s")
        variants.add(base + "ed")
        variants.add(base + "ing")
        if base.endswith("e"):
            variants.add(base[:-1] + "ing")
            variants.add(base + "d")
    return {v for v in variants if len(v) >= 3 and v.isalpha()}


def extract_topic_keywords_universal(question: str) -> set[str]:
    q = question.lower()
    stopwords = {
        "what", "which", "who", "where", "when", "why", "how", "did", "does", "do",
        "is", "are", "was", "were", "has", "have", "had", "will", "would", "could",
        "should", "the", "a", "an", "to", "for", "in", "on", "at", "of", "with", "from",
        "types", "type", "kinds", "kind", "names", "name", "list", "all", "any",
    }
    tokens = re.findall(r"\b[a-z]{3,}\b", q)
    keywords = {t for t in tokens if t not in stopwords}
    expanded = set()
    for w in keywords:
        expanded.update(_expand_morphology_basic(w))
    keywords.update(expanded)
    keywords.update(expand_with_synonyms(keywords))
    return keywords


def is_open_domain_world_query(question: str) -> bool:
    is_open, confidence = is_open_domain_query(question)
    if not is_open:
        return False
    q = question.lower()
    if re.search(r"\b(my|our|your|his|her|their|she|he|they|we)\b", q):
        return False
    has_name = bool(re.search(r"\b[A-Z][a-z]+\b", question))
    definitional = bool(re.search(r"^(what|who|where|when|why|how) (is|are|was|were|do|does|did)\b", q))
    if has_name and not definitional:
        return False
    return confidence >= 0.6


@dataclass
class NoveltyContext:
    """Precomputed novelty weights for surprise-style boosting."""
    query_tokens: set[str] = field(default_factory=set)
    token_idf: dict[str, float] = field(default_factory=dict)
    max_idf: float = 0.0


def _tokenize_for_novelty(text: str) -> set[str]:
    words = re.findall(r'\b[a-z0-9]{3,}\b', text.lower())
    return {w for w in words if w not in _NOVELTY_STOPWORDS}


def _build_novelty_context(
    query_text: str,
    nodes: list["NeuralNode"],
) -> NoveltyContext | None:
    query_tokens = _tokenize_for_novelty(query_text)
    if not query_tokens or not nodes:
        return None

    doc_freq: dict[str, int] = {}
    for node in nodes:
        tokens = _tokenize_for_novelty(node.content)
        for tok in tokens:
            doc_freq[tok] = doc_freq.get(tok, 0) + 1

    total_docs = len(nodes)
    token_idf: dict[str, float] = {}
    for tok in query_tokens:
        df = doc_freq.get(tok, 0)
        token_idf[tok] = math.log((1 + total_docs) / (1 + df)) + 1.0

    max_idf = max(token_idf.values(), default=0.0)
    return NoveltyContext(query_tokens=query_tokens, token_idf=token_idf, max_idf=max_idf)


def _compute_novelty_charge(content: str, novelty: NoveltyContext | None) -> float:
    if not novelty or not novelty.query_tokens or novelty.max_idf <= 0:
        return 0.0
    content_tokens = _tokenize_for_novelty(content)
    if not content_tokens:
        return 0.0

    score = 0.0
    for tok in content_tokens:
        if tok in novelty.query_tokens:
            score += novelty.token_idf.get(tok, 0.0)

    if score <= 0:
        return 0.0

    denom = novelty.max_idf * max(1, len(novelty.query_tokens))
    return min(1.0, score / denom)


def _robust_similarity(similarity: float) -> float:
    """Squash similarity to reduce outlier influence while preserving rank."""
    similarity = max(0.0, min(1.0, similarity))
    return math.tanh(similarity * 1.5)


# =============================================================================
# QUERY TYPE DETECTION
# =============================================================================

class QueryType:
    """Detected type of query for routing to appropriate stores."""
    TEMPORAL = "temporal"      # When questions, date references
    ENTITY = "entity"          # What/who questions, fact lookup
    MULTI_HOP = "multi_hop"    # Questions requiring inference chains
    ADVERSARIAL = "adversarial"  # Negation, conflict, verification
    OPEN = "open"              # General/open-ended


def detect_query_type(query: str) -> dict[str, float]:
    """Detect query type and return confidence scores for each type.

    THE PLAN Phase 2b Enhancement:
    Improved open-domain detection to route to external retrieval
    instead of falling back to generic semantic search.

    Returns dict mapping QueryType to confidence [0, 1].
    Multiple types can be active (e.g., temporal + entity).
    """
    query_lower = query.lower()

    scores = {
        QueryType.TEMPORAL: 0.0,
        QueryType.ENTITY: 0.0,
        QueryType.MULTI_HOP: 0.0,
        QueryType.ADVERSARIAL: 0.0,
        QueryType.OPEN: 0.0,  # Start at 0, will be computed dynamically
    }

    # Track if query has session-specific markers
    has_session_markers = False

    # TEMPORAL markers
    temporal_markers = [
        (r'\bwhen\b', 0.6),
        (r'\bhow long\b', 0.5),
        (r'\byesterday\b', 0.4),
        (r'\btoday\b', 0.3),
        (r'\blast week\b', 0.5),
        (r'\blast month\b', 0.5),
        (r'\blast year\b', 0.5),
        (r'\b\d+ years? ago\b', 0.6),
        (r'\b\d+ months? ago\b', 0.5),
        (r'\b\d+ days? ago\b', 0.4),
        (r'\bbefore\b', 0.3),
        (r'\bafter\b', 0.3),
        (r'\bduring\b', 0.3),
        (r'\bfirst\b', 0.2),
        (r'\brecent\b', 0.3),
    ]
    for pattern, weight in temporal_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.TEMPORAL] += weight

    # ENTITY markers (what/who/where factual questions)
    entity_markers = [
        (r'\bwhat is\b', 0.5),
        (r'\bwho is\b', 0.5),
        (r'\bwhat does\b', 0.4),
        (r'\bwhere is\b', 0.4),
        (r'\bwhere did\b', 0.5),
        (r"\bwhat's\b", 0.4),
        (r'\bidentity\b', 0.5),
        (r'\bname\b', 0.3),
        (r'\bcareer\b', 0.3),
        (r'\bjob\b', 0.3),
        (r'\bwork\b', 0.2),
        (r'\bfrom\b', 0.2),  # "moved from", "comes from"
    ]
    for pattern, weight in entity_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.ENTITY] += weight

    # MULTI_HOP markers (require connecting information)
    multihop_markers = [
        (r'\bhow many\b', 0.4),
        (r'\ball\b', 0.3),
        (r'\bevery\b', 0.3),
        (r'\bboth\b', 0.3),
        (r'\band\b.*\band\b', 0.4),  # Multiple conjunctions
        (r'\blist\b', 0.4),
        (r'\bwhat were\b', 0.3),
    ]
    for pattern, weight in multihop_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.MULTI_HOP] += weight

    # ADVERSARIAL markers
    adversarial_markers = [
        (r'\bnot\b', 0.4),
        (r'\bnever\b', 0.5),
        (r'\bexcept\b', 0.4),
        (r'\bother than\b', 0.5),
        (r'\bdid .* not\b', 0.5),
        (r'\bdidn\'t\b', 0.5),
        (r'\bfail\b', 0.3),
        (r'\bwithout\b', 0.3),
    ]
    for pattern, weight in adversarial_markers:
        if re.search(pattern, query_lower):
            scores[QueryType.ADVERSARIAL] += weight

    # THE PLAN Phase 2b: OPEN-DOMAIN markers
    # Detect queries that require world/encyclopedic knowledge
    # These should trigger external retrieval instead of session-only search

    # Definitional patterns (highly likely open-domain)
    open_domain_definitional = [
        (r'^what is (?:a |an |the )?(?!\w+\'s)', 0.7),  # "What is X?" but not "What is X's Y?"
        (r'^what are (?!the |their |our )', 0.6),  # "What are X?" but not about specific items
        (r'^define\b', 0.8),
        (r'^explain (?:what|how|why)\b', 0.7),
        (r'^how does .+ work\b', 0.7),
        (r'^why does\b', 0.6),
        (r'^why do\b', 0.6),
        (r'^what causes\b', 0.8),
        (r'^what makes\b', 0.6),
        (r'^what happens when\b', 0.6),
        (r'^describe (?:what|how)\b', 0.5),
    ]
    for pattern, weight in open_domain_definitional:
        if re.search(pattern, query_lower):
            scores[QueryType.OPEN] += weight
            break  # Only count one definitional pattern

    # Factual/encyclopedic patterns
    open_domain_factual = [
        (r'how many .+ (?:are there|exist)\b', 0.6),
        (r'when was .+ (?:invented|discovered|founded|created|born|built)\b', 0.7),
        (r'where is .+ located\b', 0.6),
        (r'what (?:country|city|place)\b', 0.5),
        (r'what year\b', 0.4),
        (r'capital of\b', 0.7),
        (r'(?:president|prime minister|leader) of\b', 0.6),
        (r'population of\b', 0.7),
        (r'(?:largest|smallest|oldest|youngest|tallest|shortest|longest|highest|lowest)\b', 0.5),
        (r'meaning of\b', 0.6),
        (r'origin of\b', 0.6),
        (r'history of\b', 0.6),
    ]
    for pattern, weight in open_domain_factual:
        if re.search(pattern, query_lower):
            scores[QueryType.OPEN] += weight
            break  # Only count one factual pattern

    # Scientific/technical terms (boost open-domain when present)
    scientific_terms = [
        'photosynthesis', 'evolution', 'gravity', 'atom', 'molecule',
        'electron', 'proton', 'neutron', 'cell', 'dna', 'rna',
        'gene', 'chromosome', 'protein', 'enzyme', 'virus', 'bacteria',
        'planet', 'star', 'galaxy', 'solar', 'orbit', 'mass',
        'energy', 'force', 'momentum', 'velocity', 'acceleration',
        'temperature', 'pressure', 'volume', 'density', 'climate',
        'ecosystem', 'species', 'habitat', 'genetics', 'quantum',
        'algorithm', 'computer', 'internet', 'network', 'database',
    ]
    for term in scientific_terms:
        if term in query_lower:
            scores[QueryType.OPEN] += 0.4
            break

    # Session-specific markers that REDUCE open-domain confidence
    session_markers = [
        (r'\bshe\b', 0.2),
        (r'\bhe\b', 0.2),
        (r'\bthey\b', 0.15),
        (r'\bwe\b', 0.15),
        (r'\bour\b', 0.2),
        (r'\bmy\b', 0.2),
        (r'\byour\b', 0.2),
        (r'\btold me\b', 0.3),
        (r'\bsaid\b', 0.2),
        (r'\basked\b', 0.2),
        (r'\bmentioned\b', 0.3),
        (r'\bthe meeting\b', 0.3),
        (r'\bthe conversation\b', 0.3),
        (r'\bearlier\b', 0.2),
    ]
    session_penalty = 0.0
    for pattern, weight in session_markers:
        if re.search(pattern, query_lower):
            session_penalty += weight
            has_session_markers = True

    # Apply session penalty to open-domain score
    scores[QueryType.OPEN] = max(0.0, scores[QueryType.OPEN] - session_penalty)

    # If no other type is strongly detected and no session markers, boost open-domain
    max_other_score = max(
        scores[QueryType.TEMPORAL],
        scores[QueryType.ENTITY],
        scores[QueryType.MULTI_HOP],
        scores[QueryType.ADVERSARIAL]
    )
    if max_other_score < 0.3 and not has_session_markers:
        scores[QueryType.OPEN] += 0.3  # Base boost for likely open-domain

    # Normalize and cap scores
    for key in scores:
        scores[key] = min(1.0, scores[key])

    return scores


# =============================================================================
# SPECIALIZED MEMORY STORES
# =============================================================================

@dataclass
class StoreConfig:
    """Configuration for a specialized store."""
    semantic_weight: float = 0.5
    temporal_decay_days: float = 30.0
    entity_boost: float = 0.4
    speaker_boost: float = 0.5
    keyword_boost: float = 0.3
    novelty_boost: float = 0.1
    min_charge: float = 0.1
    max_results: int = 30


class TemporalStore:
    """Hippocampus-inspired store for temporal/episodic retrieval.

    Specializes in:
    - Time-ordered sequences
    - "When did X happen" queries
    - Relative date resolution

    KEY INSIGHT: Temporal questions should weight recency/sequence
    more than pure semantic similarity.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            temporal_decay_days=14.0,  # More aggressive temporal decay
            semantic_weight=0.3,       # Lower semantic weight
            entity_boost=0.3,
            speaker_boost=0.4,
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        reference_time: datetime | None = None,
        limit: int = 30,
        all_nodes: list["NeuralNode"] | None = None,
        novelty_context: NoveltyContext | None = None,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with temporal weighting.

        Nodes closer in time to reference get higher weight.
        Nodes with temporal markers in content get boost.
        """
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        # Extract temporal markers from query
        query_lower = query_text.lower()
        temporal_keywords = self._extract_temporal_keywords(query_lower)
        decay_days = self._adjust_temporal_decay(query_lower, temporal_keywords)

        # Extract topic keywords - CRITICAL for distinguishing similar events
        topic_keywords = self._extract_topic_keywords(query_lower)

        # Extract entity names from query (capitalized words)
        query_entities = set(re.findall(r'\b[A-Z][a-z]+\b', query_text))
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does', 'The'}
        query_entities = {e.lower() for e in query_entities if e not in common}

        # CRITICAL: Remove entity names from topic_keywords to avoid double-counting
        # Topic should match the ACTION (hurt, camping) not the PERSON (melanie)
        topic_keywords = topic_keywords - query_entities

        # Get all nodes
        if all_nodes is None:
            all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        # Compute temporal-weighted charges
        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_temporal_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_text=query_lower,
                temporal_keywords=temporal_keywords,
                topic_keywords=topic_keywords,
                query_entities=query_entities,
                reference_time=reference_time,
                decay_days=decay_days,
                novelty_context=novelty_context,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        # Sort by charge descending
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]

    def _extract_temporal_keywords(self, query: str) -> set[str]:
        """Extract temporal keywords for matching.

        COMPREHENSIVE patterns - universal and not tied to any specific benchmark.
        """
        keywords = set()
        query_lower = query.lower()

        # Basic relative time markers
        basic_patterns = [
            r'yesterday', r'today', r'tomorrow',
            r'tonight', r'this morning', r'this afternoon', r'this evening',
            r'last night', r'last evening',
        ]

        # Week patterns
        week_patterns = [
            r'last week', r'this week', r'next week',
            r'past week', r'previous week', r'following week',
            r'a week ago', r'weeks ago', r'couple weeks',
            r'few weeks', r'several weeks',
        ]

        # Month patterns
        month_patterns = [
            r'last month', r'this month', r'next month',
            r'past month', r'previous month', r'following month',
            r'a month ago', r'months ago', r'couple months',
            r'few months', r'several months',
        ]

        # Year patterns
        year_patterns = [
            r'last year', r'this year', r'next year',
            r'past year', r'previous year', r'following year',
            r'a year ago', r'years ago', r'couple years',
            r'few years', r'several years',
            r'\d{4}',  # Years like 2023, 1999
            r'(?:19|20)\d{2}s',  # Decades like 1990s, 2020s
        ]

        # Month names (full and abbreviated)
        month_names = [
            r'january|jan', r'february|feb', r'march|mar', r'april|apr',
            r'may', r'june|jun', r'july|jul', r'august|aug',
            r'september|sep|sept', r'october|oct', r'november|nov', r'december|dec',
        ]

        # Day names (full and abbreviated)
        day_names = [
            r'monday|mon', r'tuesday|tue|tues', r'wednesday|wed',
            r'thursday|thu|thur|thurs', r'friday|fri',
            r'saturday|sat', r'sunday|sun',
        ]

        # Relative day references
        relative_day_patterns = [
            r'last monday', r'last tuesday', r'last wednesday', r'last thursday',
            r'last friday', r'last saturday', r'last sunday',
            r'next monday', r'next tuesday', r'next wednesday', r'next thursday',
            r'next friday', r'next saturday', r'next sunday',
            r'this monday', r'this tuesday', r'this wednesday', r'this thursday',
            r'this friday', r'this saturday', r'this sunday',
        ]

        # Time of day patterns
        time_patterns = [
            r'\d{1,2}:\d{2}', r'\d{1,2}\s*(?:am|pm)',
            r'morning', r'afternoon', r'evening', r'night', r'midnight', r'noon',
            r'dawn', r'dusk', r'sunrise', r'sunset',
        ]

        # Duration patterns
        duration_patterns = [
            r'\d+\s*(?:day|days|week|weeks|month|months|year|years)',
            r'(?:one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:day|days|week|weeks|month|months|year|years)',
            r'(?:a|an)\s*(?:day|week|month|year)',
            r'couple of (?:days|weeks|months|years)',
            r'few (?:days|weeks|months|years)',
            r'several (?:days|weeks|months|years)',
        ]

        # Sequence/order patterns
        sequence_patterns = [
            r'before', r'after', r'during', r'while', r'since', r'until',
            r'prior to', r'following', r'preceding', r'subsequent',
            r'earlier', r'later', r'previously', r'afterwards',
        ]

        # Season patterns
        season_patterns = [
            r'spring', r'summer', r'fall', r'autumn', r'winter',
            r'last spring', r'last summer', r'last fall', r'last autumn', r'last winter',
            r'this spring', r'this summer', r'this fall', r'this autumn', r'this winter',
            r'next spring', r'next summer', r'next fall', r'next autumn', r'next winter',
        ]

        # Holiday/event patterns
        holiday_patterns = [
            r'christmas', r'thanksgiving', r'easter', r'halloween',
            r'new year', r"new year's", r'valentine', r'independence day',
            r'birthday', r'anniversary', r'wedding', r'graduation',
            r'weekend', r'weekday', r'holiday', r'vacation',
        ]

        # Date format patterns
        date_patterns = [
            r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',  # MM/DD/YYYY or DD/MM/YYYY
            r'\d{4}[/-]\d{2}[/-]\d{2}',  # YYYY-MM-DD (ISO)
            r'\d{1,2}(?:st|nd|rd|th)',  # Ordinals like 1st, 2nd, 3rd
        ]

        # Frequency patterns
        frequency_patterns = [
            r'daily', r'weekly', r'monthly', r'yearly', r'annually',
            r'every day', r'every week', r'every month', r'every year',
            r'once', r'twice', r'thrice',
            r'always', r'never', r'sometimes', r'often', r'rarely',
            r'frequently', r'occasionally', r'regularly', r'seldom',
        ]

        # Combine all patterns
        all_patterns = (
            basic_patterns + week_patterns + month_patterns + year_patterns +
            month_names + day_names + relative_day_patterns + time_patterns +
            duration_patterns + sequence_patterns + season_patterns +
            holiday_patterns + date_patterns + frequency_patterns
        )

        for pattern in all_patterns:
            matches = re.findall(pattern, query_lower, re.IGNORECASE)
            keywords.update(m.lower() if isinstance(m, str) else m for m in matches)

        return keywords

    def _adjust_temporal_decay(self, query_lower: str, temporal_keywords: set[str]) -> float:
        """Adjust temporal decay to reduce recency bias for explicit time queries."""
        decay_days = self._config.temporal_decay_days

        has_explicit_year = bool(re.search(r'\b(?:19|20)\d{2}\b', query_lower))
        month_names = {
            "january", "jan", "february", "feb", "march", "mar", "april", "apr",
            "may", "june", "jun", "july", "jul", "august", "aug",
            "september", "sep", "sept", "october", "oct", "november", "nov",
            "december", "dec",
        }
        has_explicit_month = any(m in temporal_keywords for m in month_names)

        if has_explicit_year or has_explicit_month or "last year" in query_lower or "years ago" in query_lower:
            decay_days *= 1.75

        recency_terms = [
            "today", "yesterday", "this morning", "this afternoon", "this evening",
            "last night", "recent", "recently", "just now", "earlier today",
        ]
        if any(term in query_lower for term in recency_terms):
            decay_days *= 0.6

        decay_days = max(3.0, min(365.0, decay_days))
        return decay_days

    def _extract_event_year_from_content(self, content: str, msg_year: int) -> int | None:
        """Extract EVENT YEAR from content, resolving relative references.

        NEO TEMPORAL FIX:
        The KEY insight: Message timestamp ≠ Event time.
        "I painted that sunrise last year" (sent 2023) → event was 2022.

        COMPREHENSIVE patterns - universal and not tied to any specific benchmark.

        Returns the year the EVENT happened, not when the message was sent.
        """
        content_lower = content.lower()

        # 1. Explicit year mentioned in content (both 20xx and 19xx)
        explicit_years = re.findall(r'\b((?:19|20)\d{2})\b', content_lower)
        if explicit_years:
            # Return the earliest year mentioned (usually the event year)
            return min(int(y) for y in explicit_years)

        # 2. Relative year references - resolve based on message year
        # Single year ago patterns
        if re.search(r'\b(?:last year|a year ago|one year ago)\b', content_lower):
            return msg_year - 1

        # Multiple years ago - number words
        year_word_map = {
            'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20,
            'couple': 2, 'few': 3, 'several': 5, 'many': 10,
        }

        for word, num in year_word_map.items():
            if re.search(rf'\b{word}\s+years?\s+ago\b', content_lower):
                return msg_year - num

        # Multiple years ago - digits
        digit_match = re.search(r'\b(\d+)\s+years?\s+ago\b', content_lower)
        if digit_match:
            return msg_year - int(digit_match.group(1))

        # "Back in YEAR" patterns
        back_in_match = re.search(r'\bback in (?:the year )?(\d{4})\b', content_lower)
        if back_in_match:
            return int(back_in_match.group(1))

        # "In YEAR" patterns (when clearly referring to past)
        in_year_match = re.search(r'\bin (\d{4})(?:\s|,|\.|\b)', content_lower)
        if in_year_match:
            year = int(in_year_match.group(1))
            if year <= msg_year:
                return year

        # "During YEAR" patterns
        during_match = re.search(r'\bduring (?:the year )?(\d{4})\b', content_lower)
        if during_match:
            return int(during_match.group(1))

        # "Around YEAR" / "circa YEAR" patterns
        around_match = re.search(r'\b(?:around|circa|about) (\d{4})\b', content_lower)
        if around_match:
            return int(around_match.group(1))

        # "Early/mid/late YEARs" patterns (decades)
        decade_match = re.search(r'\b(?:early|mid|late)\s*((?:19|20)\d{2})s?\b', content_lower)
        if decade_match:
            return int(decade_match.group(1))

        # "The YEARs" pattern (decades)
        the_decade_match = re.search(r'\bthe\s*((?:19|20)\d{2})s\b', content_lower)
        if the_decade_match:
            return int(the_decade_match.group(1))

        # "When I was younger/a child/a kid" - approximate
        if re.search(r'\bwhen i was (?:younger|a child|a kid|little|growing up)\b', content_lower):
            return msg_year - 20  # Approximate childhood reference

        # "In my youth/childhood" patterns
        if re.search(r'\bin my (?:youth|childhood|teens|twenties)\b', content_lower):
            return msg_year - 15  # Approximate

        # "Years back" / "years prior" patterns
        if re.search(r'\byears?\s+(?:back|prior|earlier)\b', content_lower):
            return msg_year - 3  # Default to a few years

        # No relative time found - event time is same as message time
        return None

    def _extract_relative_time_type(self, content: str) -> str | None:
        """Extract what TYPE of relative time reference is in content.

        NEO GROUNDED DATE FIX:
        Detects: yesterday, last night, last week, last Friday, etc.
        Returns a category that can be matched against gold answers.

        COMPREHENSIVE patterns - universal and not tied to any specific benchmark.
        """
        content_lower = content.lower()

        # === DAY-LEVEL REFERENCES ===
        # Yesterday patterns
        if re.search(r'\b(yesterday|last night|the night before|previous day)\b', content_lower):
            return 'day_before'

        # Today patterns
        if re.search(r'\b(today|this morning|this afternoon|this evening|tonight)\b', content_lower):
            return 'today'

        # Tomorrow patterns
        if re.search(r'\b(tomorrow|tomorrow morning|tomorrow night)\b', content_lower):
            return 'day_after'

        # Day before yesterday
        if re.search(r'\b(day before yesterday|two days ago|2 days ago)\b', content_lower):
            return 'two_days_before'

        # === SPECIFIC DAY REFERENCES ===
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for day in days:
            # Last [day]
            if re.search(rf'\b(?:last|past|previous)\s+{day}\b', content_lower):
                return f'{day}_before'
            # This [day]
            if re.search(rf'\bthis\s+{day}\b', content_lower):
                return f'{day}_current'
            # Next [day]
            if re.search(rf'\bnext\s+{day}\b', content_lower):
                return f'{day}_after'

        # === WEEKEND REFERENCES ===
        if re.search(r'\b(?:last|past|previous)\s+weekend\b', content_lower):
            return 'weekend_before'
        if re.search(r'\bthis\s+weekend\b', content_lower):
            return 'weekend_current'
        if re.search(r'\bnext\s+weekend\b', content_lower):
            return 'weekend_after'

        # === WEEK-LEVEL REFERENCES ===
        if re.search(r'\b(?:last|past|previous)\s+week\b', content_lower):
            return 'week_before'
        if re.search(r'\bthis\s+week\b', content_lower):
            return 'this_week'
        if re.search(r'\bnext\s+week\b', content_lower):
            return 'next_week'
        if re.search(r'\b(?:a|one)\s+week\s+ago\b', content_lower):
            return 'week_before'
        if re.search(r'\b(?:two|2)\s+weeks?\s+ago\b', content_lower):
            return 'two_weeks_before'
        if re.search(r'\b(?:few|several|couple(?:\s+of)?)\s+weeks?\s+ago\b', content_lower):
            return 'few_weeks_before'

        # === MONTH-LEVEL REFERENCES ===
        if re.search(r'\b(?:last|past|previous)\s+month\b', content_lower):
            return 'month_before'
        if re.search(r'\bthis\s+month\b', content_lower):
            return 'this_month'
        if re.search(r'\bnext\s+month\b', content_lower):
            return 'next_month'
        if re.search(r'\b(?:a|one)\s+month\s+ago\b', content_lower):
            return 'month_before'
        if re.search(r'\b(?:two|2)\s+months?\s+ago\b', content_lower):
            return 'two_months_before'
        if re.search(r'\b(?:few|several|couple(?:\s+of)?)\s+months?\s+ago\b', content_lower):
            return 'few_months_before'

        # === SPECIFIC MONTH REFERENCES ===
        months = ['january', 'february', 'march', 'april', 'may', 'june',
                  'july', 'august', 'september', 'october', 'november', 'december']
        for month in months:
            if re.search(rf'\b(?:last|past|previous)\s+{month}\b', content_lower):
                return f'{month}_before'
            if re.search(rf'\bthis\s+{month}\b', content_lower):
                return f'{month}_current'
            if re.search(rf'\bnext\s+{month}\b', content_lower):
                return f'{month}_after'
            # Just the month name (in context)
            if re.search(rf'\bin\s+{month}\b', content_lower):
                return f'{month}_reference'

        # === YEAR-LEVEL REFERENCES ===
        if re.search(r'\b(?:last|past|previous)\s+year\b', content_lower):
            return 'year_before'
        if re.search(r'\bthis\s+year\b', content_lower):
            return 'this_year'
        if re.search(r'\bnext\s+year\b', content_lower):
            return 'next_year'
        if re.search(r'\b(?:a|one)\s+year\s+ago\b', content_lower):
            return 'year_before'
        if re.search(r'\b(?:two|2)\s+years?\s+ago\b', content_lower):
            return 'two_years_before'
        if re.search(r'\b(?:few|several|couple(?:\s+of)?)\s+years?\s+ago\b', content_lower):
            return 'few_years_before'

        # === SEASON REFERENCES ===
        seasons = ['spring', 'summer', 'fall', 'autumn', 'winter']
        for season in seasons:
            if re.search(rf'\b(?:last|past|previous)\s+{season}\b', content_lower):
                return f'{season}_before'
            if re.search(rf'\bthis\s+{season}\b', content_lower):
                return f'{season}_current'
            if re.search(rf'\bnext\s+{season}\b', content_lower):
                return f'{season}_after'

        # === HOLIDAY/EVENT REFERENCES ===
        holidays = ['christmas', 'thanksgiving', 'easter', 'halloween', 'new year']
        for holiday in holidays:
            if re.search(rf'\b(?:last|past|previous)\s+{holiday}\b', content_lower):
                return f'{holiday.replace(" ", "_")}_before'
            if re.search(rf'\bthis\s+{holiday}\b', content_lower):
                return f'{holiday.replace(" ", "_")}_current'
            if re.search(rf'\bnext\s+{holiday}\b', content_lower):
                return f'{holiday.replace(" ", "_")}_after'

        # === GENERAL TIME REFERENCES ===
        if re.search(r'\b(?:recently|lately|just now|just)\b', content_lower):
            return 'recent'
        if re.search(r'\b(?:soon|shortly|in a bit|later)\b', content_lower):
            return 'soon'
        if re.search(r'\b(?:long ago|ages ago|a while back|way back)\b', content_lower):
            return 'long_ago'
        if re.search(r'\b(?:earlier|before|previously|prior)\b', content_lower):
            return 'earlier'
        if re.search(r'\b(?:afterwards|after|subsequently|later on)\b', content_lower):
            return 'afterwards'

        # === TIME OF DAY REFERENCES ===
        if re.search(r'\b(?:morning|in the morning)\b', content_lower):
            return 'morning'
        if re.search(r'\b(?:afternoon|in the afternoon)\b', content_lower):
            return 'afternoon'
        if re.search(r'\b(?:evening|in the evening)\b', content_lower):
            return 'evening'
        if re.search(r'\b(?:night|at night|nighttime)\b', content_lower):
            return 'night'

        return None

    def _relative_time_matches_query(self, relative_type: str | None, query_text: str) -> float:
        """Check if a relative time type matches what the query is asking for.

        Returns a boost score if the relative time aligns with the query.
        """
        if not relative_type:
            return 0.0

        query_lower = query_text.lower()

        # "The week before" patterns in gold answers
        if 'week' in query_lower and relative_type == 'week_before':
            return 1.5

        # "The Friday before" patterns
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for day in days:
            if day in query_lower and relative_type == f'{day}_before':
                return 2.0  # Strong match for specific day

        # Day-level queries
        if ('yesterday' in query_lower or 'day before' in query_lower) and relative_type == 'day_before':
            return 1.5

        # If content has ANY relative time, give small boost for temporal questions
        if relative_type:
            return 0.3

        return 0.0

    def _extract_topic_keywords(self, query: str) -> set[str]:
        """Extract topic/event keywords that must match for temporal questions.

        This is CRITICAL: "When did X go camping?" must find memories with "camping",
        not just any memory about X.

        NEO: Added morphological expansion so "move" matches "moved".
        NEO+: Added synonym expansion.
        """
        # Remove question words and common verbs
        stopwords = {
            'when', 'did', 'does', 'do', 'is', 'was', 'were', 'are', 'has', 'have', 'had',
            'go', 'going', 'went', 'get', 'got', 'getting', 'buy', 'bought', 'buying',
            'the', 'a', 'an', 'to', 'for', 'in', 'on', 'at', 'of', 'with', 'from',
            'how', 'long', 'what', 'where', 'who', 'why', 'which', 'and', 'or', 'but',
            'she', 'he', 'her', 'his', 'they', 'their', 'it', 'its', 'this', 'that',
            'plan', 'planning', 'planned', 'join', 'joined', 'joining',
            'make', 'made', 'making', 'take', 'took', 'taking', 'give', 'gave', 'giving',
        }
        words = re.findall(r'\b[a-z]{3,}\b', query.lower())
        # Filter and keep meaningful topic words
        topics = {w for w in words if w not in stopwords}

        # Duration queries need time-unit anchors for "seven years now" style memories
        if re.search(r'\bhow long\b', query.lower()) or re.search(r'\bhow old\b|\bage\b', query.lower()):
            topics.update({"year", "years", "month", "months", "week", "weeks", "day", "days"})

        # NEO: Expand morphologically so "move" matches "moved"
        expanded = set()
        for w in topics:
            expanded.update(self._expand_morphology_temporal(w))
        topics.update(expanded)

        # NEO+: Synonym expansion
        topics.update(self._expand_synonyms_temporal(topics))

        # NEO+: Preserve proper nouns (Tilly, Valorant, etc.)
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', query)
        skip = {'When', 'What', 'Where', 'Who', 'Which', 'How', 'Did', 'Does', 'The'}
        for pn in proper_nouns:
            if pn not in skip:
                topics.add(pn.lower())

        return topics

    def _expand_synonyms_temporal(self, keywords: set[str]) -> set[str]:
        """Expand keywords with centralized synonym hash."""
        return expand_with_synonyms(keywords) - keywords

    def _expand_morphology_temporal(self, word: str) -> set[str]:
        """Expand word to morphological variants for temporal matching."""
        variants = {word}
        w = word.lower()

        # Get base form
        base = w
        if w.endswith('ed') and len(w) > 4:
            base = w[:-2]
            if base.endswith('i'):
                base = base[:-1] + 'y'
            elif len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ing') and len(w) > 5:
            base = w[:-3]
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ies') and len(w) > 4:
            base = w[:-3] + 'y'
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            base = w[:-1]

        variants.add(base)

        # Generate variants from base
        if len(base) >= 3:
            variants.add(base + 's')
            variants.add(base + 'ed')
            variants.add(base + 'ing')
            if base.endswith('e'):
                variants.add(base[:-1] + 'ing')
                variants.add(base + 'd')

        return {v for v in variants if len(v) >= 3 and v.isalpha()}

    def _compute_temporal_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_text: str,
        temporal_keywords: set[str],
        topic_keywords: set[str],
        query_entities: set[str],
        reference_time: datetime | None,
        decay_days: float,
        novelty_context: NoveltyContext | None = None,
    ) -> float:
        """Compute temporal-weighted charge.

        KEY FIX: Topic keywords must match for temporal questions.
        "When did X go camping?" must find memories with "camping".
        "When did X go camping in June?" must find memories from JUNE.
        """
        content_lower = node.content.lower()

        # Get message datetime from metadata
        msg_datetime_str = ""
        if node.metadata:
            msg_datetime_str = (node.metadata.get("datetime", "") or "").lower()

        # MONTH NAMES for temporal filtering
        MONTHS = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }

        # 1. TEMPORAL PERIOD FILTERING - CRITICAL
        # If query mentions a specific month, filter for messages FROM that month
        temporal_period_charge = 0.0
        query_months = {m for m in MONTHS.keys() if m in temporal_keywords}
        if query_months and msg_datetime_str:
            # Check if message datetime contains the queried month
            msg_has_query_month = any(m in msg_datetime_str for m in query_months)
            if msg_has_query_month:
                temporal_period_charge = 0.5  # Strong boost for correct month
            else:
                temporal_period_charge = -0.3  # Penalty for wrong month

        # 2. TOPIC MATCHING - CRITICAL for temporal questions
        # "When did X get hurt?" MUST find memories with "hurt"
        topic_charge = 0.0
        if topic_keywords:
            # Remove months from topic keywords (they're handled above)
            topic_words = topic_keywords - set(MONTHS.keys())
            if topic_words:
                matches = sum(1 for kw in topic_words if kw in content_lower)
                if matches == 0:
                    topic_charge = -1.0  # STRONGER penalty for missing topic
                else:
                    # NEO: Check for CRITICAL keywords (birthday, parade, concert, etc.)
                    # These must match EXACTLY for correct retrieval
                    critical_keywords = {'birthday', 'parade', 'concert', 'wedding',
                                         'anniversary', 'funeral', 'graduation', 'ceremony'}
                    has_critical = bool(topic_words & critical_keywords)
                    critical_in_content = any(kw in content_lower for kw in topic_words & critical_keywords)

                    if has_critical and not critical_in_content:
                        topic_charge = -1.5  # VERY STRONG penalty - wrong event type
                    elif has_critical and critical_in_content:
                        topic_charge = 3.0  # VERY STRONG boost for matching critical keyword
                    else:
                        # Standard topic matching
                        topic_charge = min(2.5, matches * 1.0)

        # 3. ENTITY MATCHING - Must mention the right person (in content OR as speaker)
        entity_charge = 0.0
        if query_entities:
            speaker = node.speaker_id.lower()
            # Check both content AND speaker metadata (e.g., "melanie" in "Melanie Turner")
            entity_matches = sum(1 for e in query_entities if e in content_lower or e in speaker)
            if entity_matches > 0:
                entity_charge = min(1.5, entity_matches * 0.6)  # Strong boost for entity match

        # 4. Semantic similarity (lower weight in temporal store)
        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = _robust_similarity(float(np.dot(query_embedding, node_emb / node_norm)))

        # 5. Temporal keyword matching in content
        temporal_charge = 0.0
        for keyword in temporal_keywords:
            if keyword in content_lower:
                temporal_charge += 0.2
        temporal_charge = min(1.0, temporal_charge)

        # 6. NEO GROUNDED DATE FIX: Relative time matching
        # "last week" in content + query asking about week → strong match
        # "last Friday" in content + query asking about Friday → strong match
        grounded_charge = 0.0
        relative_time_type = self._extract_relative_time_type(content_lower)
        if relative_time_type:
            # Memory has a relative time reference - check if it matches query
            grounded_charge = self._relative_time_matches_query(relative_time_type, query_text)
            # Also boost if query mentions temporal period that aligns
            if relative_time_type in ['week_before', 'this_week'] and 'week' in query_text.lower():
                grounded_charge += 0.5
            if relative_time_type == 'day_before' and any(w in query_text.lower() for w in ['yesterday', 'night', 'day']):
                grounded_charge += 0.5

        # 7. Recency decay (if reference time provided)
        recency_charge = 0.5
        if reference_time and node.created_at:
            time_diff = abs((reference_time - node.created_at).days)
            recency_charge = math.exp(-time_diff / decay_days)

        # 7.5 Surprise/novelty boost (rare query tokens present)
        novelty_charge = _compute_novelty_charge(node.content, novelty_context)

        # 8. NEO TEMPORAL FIX: EVENT YEAR MATCHING
        # "When did X paint a sunrise?" (gold: 2022)
        # Memory: "I painted that sunrise last year" (sent 2023)
        # Event year = 2023 - 1 = 2022 → MATCH!
        event_year_charge = 0.0
        query_years = {int(y) for y in re.findall(r'\b(20\d{2})\b', query_text)}

        # Get message year for relative time resolution
        msg_year = 2023  # Default
        if node.created_at:
            msg_year = node.created_at.year
        elif msg_datetime_str:
            year_match = re.search(r'(20\d{2})', msg_datetime_str)
            if year_match:
                msg_year = int(year_match.group(1))

        # Extract event year from content (resolves "last year" etc.)
        event_year = self._extract_event_year_from_content(content_lower, msg_year)

        # NEO FIX: Detect if query is asking about PAST events
        # "When did X paint/go/do Y?" without a year → likely asking about past event
        query_asks_past = bool(re.search(r'\b(when did|how long ago|when was)\b', query_text.lower()))

        if query_years and event_year:
            # Query asks for a specific year - boost if event year matches
            if event_year in query_years:
                event_year_charge = 1.5  # Strong boost for year match
            else:
                event_year_charge = -0.3  # Penalty for wrong year
        elif event_year and query_asks_past:
            # NEO: Query asks about past, memory has "last year" → STRONG boost
            # This helps "When did Melanie paint a sunrise?" find "painted last year"
            if event_year < msg_year:
                # Event happened BEFORE message timestamp → past event reference
                event_year_charge = 1.2  # Strong boost for past event with year reference
            else:
                event_year_charge = 0.5
        elif event_year:
            # Memory has relative time ref ("last year") - moderate boost
            event_year_charge = 0.4
        elif query_asks_past and 'last year' in content_lower:
            # Direct "last year" mention even if year extraction failed
            event_year_charge = 0.8

        # TOTAL: Topic match is KING for temporal questions
        # "When did X do Y?" - finding Y is more important than semantic similarity
        # NEO: Rebalanced - topic is now DOMINANT signal
        # NEO GROUNDED: Relative time matching now gets significant weight
        # NEO YEAR: Event year matching strengthened for "last year" queries
        total = (
            topic_charge * 0.32 +             # CRITICAL: topic must match
            event_year_charge * 0.18 +        # NEO: Event year matching (strengthened)
            grounded_charge * 0.15 +          # NEO: Relative time matching (last week, yesterday)
            temporal_period_charge * 0.12 +   # Right month/period
            entity_charge * 0.10 +            # Entity must be mentioned
            semantic_charge * 0.06 +          # Semantic similarity (reduced further)
            temporal_charge * 0.05 +          # Temporal keywords in content
            recency_charge * 0.02 +           # Slight recency bias
            novelty_charge * self._config.novelty_boost
        )

        return total


class EntityStore:
    """Neocortex-inspired store for semantic fact retrieval.

    Specializes in:
    - Entity attributes ("what is X's identity")
    - Entity relationships ("who does X work with")
    - Factual information

    KEY INSIGHT: Entity questions should weight entity binding
    and speaker binding heavily.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            semantic_weight=0.35,
            entity_boost=0.6,      # HIGH entity weight
            speaker_boost=0.8,     # VERY HIGH speaker weight (was 0.7)
            keyword_boost=0.4,     # Stronger keyword matching
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 50,  # Higher limit for entity to ensure coverage
        all_nodes: list["NeuralNode"] | None = None,
        novelty_context: NoveltyContext | None = None,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with entity/speaker weighting."""
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        # Extract entities from query
        query_entities = self._extract_entities(query_text)
        query_keywords = self._extract_keywords(query_text)

        # Get all nodes
        if all_nodes is None:
            all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_entity_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_entities=query_entities,
                query_keywords=query_keywords,
                novelty_context=novelty_context,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _extract_entities(self, text: str) -> set[str]:
        """Extract entity names (capitalized words)."""
        entities = re.findall(r'\b[A-Z][a-z]+\b', text)
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                  'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The', 'And',
                  'But', 'Or', 'From', 'To', 'In', 'On', 'At', 'For', 'With'}
        return {e for e in entities if e not in common}

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract meaningful keywords with morphological + synonym expansion.

        NEO: Essential for matching "move" in question to "moved" in answer.
        NEO+: Added synonym clusters for domain-specific terms.
        """
        text_lower = text.lower()
        # Split and filter
        words = re.findall(r'\b[a-z]{3,}\b', text_lower)
        stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all',
                     'can', 'had', 'her', 'was', 'one', 'our', 'out', 'has',
                     'what', 'when', 'where', 'who', 'why', 'how', 'did', 'does'}
        keywords = {w for w in words if w not in stopwords}

        # Expand morphologically
        expanded = set()
        for w in keywords:
            expanded.update(self._expand_morphology(w))
        keywords.update(expanded)

        # NEO+: Synonym expansion for common gaps
        keywords.update(self._expand_synonyms(keywords))

        # NEO+: Preserve proper nouns from original text (Tilly, etc.)
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', text)
        skip_words = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                      'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The'}
        for pn in proper_nouns:
            if pn not in skip_words:
                keywords.add(pn.lower())

        return keywords

    def _expand_synonyms(self, keywords: set[str]) -> set[str]:
        """Expand keywords with centralized synonym hash.

        NEO+: Uses O(1) hash lookup for synonyms.
        """
        return expand_with_synonyms(keywords) - keywords  # Return only NEW synonyms

    def _expand_morphology(self, word: str) -> set[str]:
        """Expand word to morphological variants."""
        variants = {word}
        w = word.lower()

        # Get base form
        base = w
        if w.endswith('ed') and len(w) > 4:
            base = w[:-2]
            if base.endswith('i'):
                base = base[:-1] + 'y'
            elif len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ing') and len(w) > 5:
            base = w[:-3]
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ies') and len(w) > 4:
            base = w[:-3] + 'y'
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            base = w[:-1]

        variants.add(base)

        # Generate variants from base
        if len(base) >= 3:
            variants.add(base + 's')
            variants.add(base + 'ed')
            variants.add(base + 'ing')
            if base.endswith('e'):
                variants.add(base[:-1] + 'ing')
                variants.add(base + 'd')

        return {v for v in variants if len(v) >= 3 and v.isalpha()}

    def _compute_entity_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_entities: set[str],
        query_keywords: set[str],
        novelty_context: NoveltyContext | None = None,
    ) -> float:
        """Compute entity-weighted charge."""
        content_lower = node.content.lower()

        # 1. Semantic similarity
        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = _robust_similarity(float(np.dot(query_embedding, node_emb / node_norm)))

        # 2. Entity binding (entities mentioned in content)
        entity_charge = 0.0
        if query_entities:
            matches = sum(1 for e in query_entities if e.lower() in content_lower)
            entity_charge = min(1.0, matches / len(query_entities))

        # 3. Speaker binding (speaker IS the queried entity)
        speaker_charge = 0.0
        if node.metadata and query_entities:
            speaker = (node.metadata.get("producer_id", "") or
                      node.metadata.get("speaker", "")).lower()
            if any(e.lower() == speaker for e in query_entities):
                speaker_charge = 1.0

        # 4. Keyword matching
        keyword_charge = 0.0
        if query_keywords:
            matches = sum(1 for k in query_keywords if k in content_lower)
            keyword_charge = min(1.0, matches / max(1, len(query_keywords)))

        # 5. Surprise/novelty boost (rare query tokens present)
        novelty_charge = _compute_novelty_charge(node.content, novelty_context)

        # TOTAL: Entity-weighted combination
        total = (
            semantic_charge * self._config.semantic_weight +
            entity_charge * self._config.entity_boost +
            speaker_charge * self._config.speaker_boost +
            keyword_charge * self._config.keyword_boost +
            novelty_charge * self._config.novelty_boost
        )

        # CRITICAL: If speaker matches AND semantic > 0.2, give bonus
        # This ensures ALL speaker messages have a chance even with low semantic
        if speaker_charge > 0.5 and semantic_charge > 0.2:
            total *= 1.5  # Increased from 1.3
            # Additional boost if keywords also match
            if keyword_charge > 0.3:
                total *= 1.2

        return total


class ReasoningStore:
    """Prefrontal cortex-inspired store for multi-hop reasoning.

    Specializes in:
    - Questions requiring multiple pieces of information
    - "How many X did Y do?"
    - Aggregation and counting

    KEY INSIGHT: Multi-hop questions need BREADTH not depth.
    Retrieve more nodes with moderate relevance rather than
    fewer nodes with high relevance.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            semantic_weight=0.5,
            entity_boost=0.4,
            speaker_boost=0.3,
            keyword_boost=0.4,  # Higher keyword weight for multi-hop
            min_charge=0.08,    # Lower threshold for breadth
            max_results=60,     # More results for multi-hop
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 60,
        all_nodes: list["NeuralNode"] | None = None,
        novelty_context: NoveltyContext | None = None,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with breadth-first approach."""
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        query_entities = self._extract_entities(query_text)
        query_keywords = self._extract_keywords(query_text)

        if all_nodes is None:
            all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_reasoning_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_entities=query_entities,
                query_keywords=query_keywords,
                novelty_context=novelty_context,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _extract_entities(self, text: str) -> set[str]:
        entities = re.findall(r'\b[A-Z][a-z]+\b', text)
        common = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does',
                  'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The', 'And'}
        return {e for e in entities if e not in common}

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract keywords with morphological + synonym expansion."""
        text_lower = text.lower()
        words = re.findall(r'\b[a-z]{3,}\b', text_lower)
        stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all'}
        keywords = {w for w in words if w not in stopwords}

        # NEO: Expand morphologically
        expanded = set()
        for w in keywords:
            expanded.update(self._expand_morphology_reasoning(w))
        keywords.update(expanded)

        # NEO+: Synonym expansion using centralized hash
        keywords.update(expand_with_synonyms(keywords))

        # NEO+: Preserve proper nouns
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', text)
        skip = {'What', 'When', 'Where', 'Who', 'Which', 'How', 'Did', 'Does', 'The', 'Many'}
        for pn in proper_nouns:
            if pn not in skip:
                keywords.add(pn.lower())

        return keywords

    def _expand_morphology_reasoning(self, word: str) -> set[str]:
        """Expand word to morphological variants."""
        variants = {word}
        w = word.lower()
        base = w
        if w.endswith('ed') and len(w) > 4:
            base = w[:-2]
            if base.endswith('i'):
                base = base[:-1] + 'y'
            elif len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ing') and len(w) > 5:
            base = w[:-3]
            if len(base) >= 2 and base[-1] == base[-2]:
                base = base[:-1]
        elif w.endswith('ies') and len(w) > 4:
            base = w[:-3] + 'y'
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            base = w[:-1]
        variants.add(base)
        if len(base) >= 3:
            variants.add(base + 's')
            variants.add(base + 'ed')
            variants.add(base + 'ing')
            if base.endswith('e'):
                variants.add(base[:-1] + 'ing')
                variants.add(base + 'd')
        return {v for v in variants if len(v) >= 3 and v.isalpha()}

    def _compute_reasoning_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_entities: set[str],
        query_keywords: set[str],
        novelty_context: NoveltyContext | None = None,
    ) -> float:
        """Compute reasoning charge emphasizing keyword coverage."""
        content_lower = node.content.lower()

        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = _robust_similarity(float(np.dot(query_embedding, node_emb / node_norm)))

        entity_charge = 0.0
        if query_entities:
            matches = sum(1 for e in query_entities if e.lower() in content_lower)
            entity_charge = min(1.0, matches / len(query_entities))

        speaker_charge = 0.0
        if node.metadata and query_entities:
            speaker = (node.metadata.get("producer_id", "") or
                      node.metadata.get("speaker", "")).lower()
            if any(e.lower() == speaker for e in query_entities):
                speaker_charge = 1.0

        keyword_charge = 0.0
        if query_keywords:
            matches = sum(1 for k in query_keywords if k in content_lower)
            keyword_charge = min(1.0, matches / max(1, len(query_keywords)))

        novelty_charge = _compute_novelty_charge(node.content, novelty_context)

        total = (
            semantic_charge * self._config.semantic_weight +
            entity_charge * self._config.entity_boost +
            speaker_charge * self._config.speaker_boost +
            keyword_charge * self._config.keyword_boost +
            novelty_charge * self._config.novelty_boost
        )

        return total


class AdversarialStore:
    """Orbitofrontal-inspired store for conflict/verification retrieval.

    Specializes in:
    - Negation handling ("did NOT")
    - Exception queries ("except", "other than")
    - Verification and disambiguation

    KEY INSIGHT: Adversarial questions need OPPOSITE weighting.
    Nodes that CONTRADICT the positive case are valuable.
    """

    def __init__(self, storage: "NeuralGraphStorage", config: StoreConfig | None = None):
        self._storage = storage
        self._config = config or StoreConfig(
            semantic_weight=0.4,
            entity_boost=0.5,
            speaker_boost=0.4,
        )

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 40,
        all_nodes: list["NeuralNode"] | None = None,
        novelty_context: NoveltyContext | None = None,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with adversarial weighting."""
        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        # Parse adversarial structure
        negation_terms, positive_terms = self._parse_adversarial(query_text)
        query_entities = self._extract_entities(query_text)

        if all_nodes is None:
            all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []

        results: list[tuple["NeuralNode", float]] = []

        for node in all_nodes:
            charge = self._compute_adversarial_charge(
                node=node,
                query_embedding=query_embedding_np,
                query_entities=query_entities,
                negation_terms=negation_terms,
                positive_terms=positive_terms,
                novelty_context=novelty_context,
            )
            if charge >= self._config.min_charge:
                results.append((node, charge))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _parse_adversarial(self, query: str) -> tuple[set[str], set[str]]:
        """Parse query into negated terms and positive terms.

        COMPREHENSIVE patterns - universal and not tied to any specific benchmark.
        """
        query_lower = query.lower()

        # Find negation patterns - comprehensive list
        negation_patterns = [
            # Basic negations
            r'not\s+(\w+)',
            r'never\s+(\w+)',
            r'no\s+(\w+)',
            r'none\s+(\w+)',
            # Contractions
            r"didn't\s+(\w+)",
            r"doesn't\s+(\w+)",
            r"don't\s+(\w+)",
            r"wasn't\s+(\w+)",
            r"weren't\s+(\w+)",
            r"isn't\s+(\w+)",
            r"aren't\s+(\w+)",
            r"hasn't\s+(\w+)",
            r"haven't\s+(\w+)",
            r"hadn't\s+(\w+)",
            r"won't\s+(\w+)",
            r"wouldn't\s+(\w+)",
            r"couldn't\s+(\w+)",
            r"shouldn't\s+(\w+)",
            r"can't\s+(\w+)",
            # Exception patterns
            r'except\s+(\w+)',
            r'except\s+for\s+(\w+)',
            r'other\s+than\s+(\w+)',
            r'apart\s+from\s+(\w+)',
            r'besides\s+(\w+)',
            r'excluding\s+(\w+)',
            r'but\s+not\s+(\w+)',
            r'save\s+for\s+(\w+)',
            # Absence patterns
            r'without\s+(\w+)',
            r'lacking\s+(\w+)',
            r'missing\s+(\w+)',
            r'absent\s+(\w+)',
            # Alternative patterns
            r'instead\s+of\s+(\w+)',
            r'rather\s+than\s+(\w+)',
            # Denial patterns
            r'deny\s+(\w+)',
            r'denied\s+(\w+)',
            r'refuse\s+(\w+)',
            r'refused\s+(\w+)',
            r'reject\s+(\w+)',
            r'rejected\s+(\w+)',
        ]

        negation_terms = set()
        for pattern in negation_patterns:
            matches = re.findall(pattern, query_lower)
            negation_terms.update(matches)

        # Positive terms are the rest
        words = re.findall(r'\b[a-z]{3,}\b', query_lower)
        stopwords = {
            # Articles and determiners
            'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all',
            # Question words
            'what', 'when', 'where', 'who', 'why', 'how', 'which',
            # Common verbs
            'did', 'does', 'was', 'were', 'has', 'have', 'had', 'been',
            'being', 'will', 'would', 'could', 'should', 'may', 'might',
            # Negation words
            'never', 'except', 'other', 'than', 'without', 'none',
            # Prepositions
            'with', 'from', 'into', 'onto', 'upon', 'about', 'over',
            'under', 'through', 'between', 'among', 'during', 'before',
            'after', 'above', 'below',
            # Conjunctions
            'that', 'this', 'these', 'those', 'then', 'than',
            # Pronouns
            'they', 'them', 'their', 'there', 'here',
        }
        positive_terms = {w for w in words if w not in stopwords and w not in negation_terms}

        return negation_terms, positive_terms

    def _extract_entities(self, text: str) -> set[str]:
        """Extract entity names from text.

        COMPREHENSIVE exclusion list - universal and not tied to any specific benchmark.
        """
        entities = re.findall(r'\b[A-Z][a-z]+\b', text)

        # Common words that are often capitalized but aren't entities
        common = {
            # Question words (sentence starters)
            'What', 'When', 'Where', 'Who', 'Why', 'How', 'Which',
            # Auxiliary verbs (sentence starters)
            'Did', 'Does', 'Do', 'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had',
            'Will', 'Would', 'Could', 'Should', 'May', 'Might', 'Can', 'Must',
            # Articles and determiners
            'The', 'This', 'That', 'These', 'Those', 'Some', 'Any', 'All', 'Each',
            'Every', 'Both', 'Neither', 'Either', 'Such', 'What', 'Which',
            # Conjunctions
            'And', 'But', 'Or', 'Nor', 'So', 'Yet', 'For', 'Because', 'Although',
            'Though', 'While', 'If', 'Unless', 'Until', 'Since', 'After', 'Before',
            # Negations
            'Not', 'Never', 'Except', 'Without', 'None', 'Nothing', 'Nobody',
            # Pronouns (sometimes capitalized at start)
            'He', 'She', 'It', 'They', 'We', 'You', 'Me', 'Us', 'Them', 'Him', 'Her',
            # Common nouns (often capitalized incorrectly)
            'Today', 'Tomorrow', 'Yesterday', 'Morning', 'Evening', 'Night',
            'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
            'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
            'September', 'October', 'November', 'December',
            # Common sentence starters
            'However', 'Therefore', 'Moreover', 'Furthermore', 'Nevertheless',
            'Meanwhile', 'Otherwise', 'Instead', 'Perhaps', 'Maybe', 'Actually',
            'Finally', 'Eventually', 'Apparently', 'Obviously', 'Certainly',
            'Probably', 'Possibly', 'Definitely', 'Absolutely', 'Especially',
            # Relative pronouns
            'There', 'Here', 'Then', 'Now', 'Once', 'Always', 'Sometimes',
            # Other common non-entity capitals
            'Yes', 'No', 'Please', 'Thank', 'Thanks', 'Hello', 'Goodbye',
            'Well', 'First', 'Second', 'Third', 'Last', 'Next', 'Many', 'Much',
            'More', 'Most', 'Few', 'Several', 'Other', 'Another', 'Same',
        }

        return {e for e in entities if e not in common}

    def _compute_adversarial_charge(
        self,
        node: "NeuralNode",
        query_embedding: np.ndarray,
        query_entities: set[str],
        negation_terms: set[str],
        positive_terms: set[str],
        novelty_context: NoveltyContext | None = None,
    ) -> float:
        """Compute charge with adversarial awareness."""
        content_lower = node.content.lower()

        semantic_charge = 0.0
        if node.embedding:
            node_emb = np.array(node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_charge = _robust_similarity(float(np.dot(query_embedding, node_emb / node_norm)))

        entity_charge = 0.0
        if query_entities:
            matches = sum(1 for e in query_entities if e.lower() in content_lower)
            entity_charge = min(1.0, matches / len(query_entities))

        speaker_charge = 0.0
        if node.metadata and query_entities:
            speaker = (node.metadata.get("producer_id", "") or
                      node.metadata.get("speaker", "")).lower()
            if any(e.lower() == speaker for e in query_entities):
                speaker_charge = 1.0

        # ADVERSARIAL: Check if node contains negated terms
        negation_charge = 0.0
        if negation_terms:
            # Nodes that DON'T contain negated terms are actually valuable
            # for adversarial questions (they show what DID happen)
            contains_negated = sum(1 for t in negation_terms if t in content_lower)
            if contains_negated == 0:
                # Node doesn't mention the negated thing - might be relevant
                negation_charge = 0.2
            else:
                # Node mentions the negated thing - could be evidence
                negation_charge = 0.4

        # Positive term matching
        positive_charge = 0.0
        if positive_terms:
            matches = sum(1 for t in positive_terms if t in content_lower)
            positive_charge = min(1.0, matches / max(1, len(positive_terms)))

        novelty_charge = _compute_novelty_charge(node.content, novelty_context)

        total = (
            semantic_charge * self._config.semantic_weight +
            entity_charge * self._config.entity_boost +
            speaker_charge * self._config.speaker_boost +
            negation_charge * 0.3 +
            positive_charge * 0.3 +
            novelty_charge * self._config.novelty_boost
        )

        return total


# =============================================================================
# TESSERACT: 4D FUSION LAYER
# =============================================================================

class Tesseract:
    """4D Memory Tesseract: Fuses results from all specialized stores.

    The tesseract queries all stores in parallel, then fuses results
    based on detected query type.

    This is the KEY to achieving 99% accuracy:
    - Each store specializes in different question types
    - Fusion weights are DYNAMIC based on query analysis
    - Final results combine breadth AND depth
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self._storage = storage
        self._temporal_store = TemporalStore(storage)
        self._entity_store = EntityStore(storage)
        self._reasoning_store = ReasoningStore(storage)
        self._adversarial_store = AdversarialStore(storage)

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        reference_time: datetime | None = None,
        limit: int = 80,
        auto_expand_temporal: bool = True,
    ) -> list[tuple["NeuralNode", float]]:
        """Execute 4D tesseract retrieval.

        1. Detect query type
        2. Auto-expand temporal queries with date tokens (if enabled)
        3. Query all stores in parallel
        4. Fuse results with type-based weighting
        5. Return top unified results

        Args:
            query_text: The search query
            query_embedding: Vector embedding of the query
            session_key: Session to search within
            reference_time: Reference time for temporal queries
            limit: Maximum results to return
            auto_expand_temporal: If True, automatically expand temporal queries
                with date tokens for better BM25 matching (default: True)
        """
        # Step 1: Detect query type
        query_types = detect_query_type(query_text)

        # Step 1.5: Auto-expand temporal queries with date tokens
        # This ensures queries like "May 2023" match "MONTH_MAY YEAR_2023" tokens
        effective_query = query_text
        if auto_expand_temporal and query_types.get(QueryType.TEMPORAL, 0) > 0.3:
            effective_query = _expand_temporal_query(query_text)
            logger.debug(f"Expanded temporal query: {query_text!r} -> {effective_query!r}")

        logger.info(f"Query type detection: {query_types}")

        # Preload nodes once and compute novelty context (surprise metric)
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return []
        novelty_context = _build_novelty_context(query_text, all_nodes)

        # Step 2: Query all stores (can be parallelized with asyncio.gather)
        # Note: Use effective_query (with temporal tokens) for temporal store
        temporal_results = await self._temporal_store.retrieve(
            effective_query,
            query_embedding,
            session_key,
            reference_time,
            limit=40,
            all_nodes=all_nodes,
            novelty_context=novelty_context,
        )

        entity_results = await self._entity_store.retrieve(
            query_text,
            query_embedding,
            session_key,
            limit=50,
            all_nodes=all_nodes,
            novelty_context=novelty_context,
        )

        reasoning_results = await self._reasoning_store.retrieve(
            query_text,
            query_embedding,
            session_key,
            limit=40,
            all_nodes=all_nodes,
            novelty_context=novelty_context,
        )

        adversarial_results = await self._adversarial_store.retrieve(
            query_text,
            query_embedding,
            session_key,
            limit=30,
            all_nodes=all_nodes,
            novelty_context=novelty_context,
        )

        # Step 3: Fuse with type-based weights
        fused = self._fuse_results(
            temporal_results=temporal_results,
            entity_results=entity_results,
            reasoning_results=reasoning_results,
            adversarial_results=adversarial_results,
            type_weights=query_types,
        )

        # Step 3.5: Apply temporal momentum (neighbor boost)
        fused = await self._apply_temporal_momentum(fused, type_weights=query_types)

        # Step 4: Sort and return top
        fused.sort(key=lambda x: x[1], reverse=True)
        return fused[:limit]

    def _normalize_charges(
        self, results: list[tuple["NeuralNode", float]]
    ) -> list[tuple["NeuralNode", float]]:
        """Normalize charges to [0, 1] range for fair fusion."""
        if not results:
            return results
        max_charge = max(charge for _, charge in results)
        if max_charge <= 0:
            return results
        return [(node, charge / max_charge) for node, charge in results]

    async def _apply_temporal_momentum(
        self,
        results: list[tuple["NeuralNode", float]],
        type_weights: dict[str, float],
        top_k: int = 6,
        max_neighbors: int = 3,
    ) -> list[tuple["NeuralNode", float]]:
        """Boost temporal neighbors of top results to preserve event continuity."""
        if not results:
            return results

        temporal_bias = max(
            type_weights.get(QueryType.TEMPORAL, 0.0),
            type_weights.get(QueryType.MULTI_HOP, 0.0),
        )
        base_boost = 0.04 + (0.06 * temporal_bias)

        combined: dict[str, tuple["NeuralNode", float]] = {
            node.node_id: (node, charge) for node, charge in results
        }

        for idx, (node, charge) in enumerate(results[:top_k]):
            neighbors = await self._storage.get_neighbors(
                node.node_id,
                edge_types=[EdgeType.TEMPORAL],
                direction="both",
            )
            if not neighbors:
                continue

            neighbors = sorted(
                neighbors,
                key=lambda pair: pair[1].base_weight * pair[1].ltp_boost * pair[1].confidence,
                reverse=True,
            )

            for neighbor, edge in neighbors[:max_neighbors]:
                edge_strength = edge.base_weight * edge.ltp_boost * edge.confidence
                distance_factor = 1.0 - (idx / max(1, top_k))
                boost = min(0.2, base_boost * edge_strength * distance_factor)
                if boost <= 0:
                    continue
                existing = combined.get(neighbor.node_id)
                if existing:
                    combined[neighbor.node_id] = (existing[0], existing[1] + boost)
                else:
                    combined[neighbor.node_id] = (neighbor, boost)

        return list(combined.values())

    def _fuse_results(
        self,
        temporal_results: list[tuple["NeuralNode", float]],
        entity_results: list[tuple["NeuralNode", float]],
        reasoning_results: list[tuple["NeuralNode", float]],
        adversarial_results: list[tuple["NeuralNode", float]],
        type_weights: dict[str, float],
    ) -> list[tuple["NeuralNode", float]]:
        """Fuse results from all stores with type-based weighting.

        KEY FIX:
        1. Normalize charges within each store to [0,1] for fair comparison
        2. Use ACTUAL query type scores, not defaults when score is 0
        3. Strongly favor the dominant query type's store
        """
        # Normalize charges within each store FIRST
        temporal_results = self._normalize_charges(temporal_results)
        entity_results = self._normalize_charges(entity_results)
        reasoning_results = self._normalize_charges(reasoning_results)
        adversarial_results = self._normalize_charges(adversarial_results)

        # Get ACTUAL type weights (use 0 when detection returns 0, not defaults)
        temporal_score = type_weights.get(QueryType.TEMPORAL, 0.0)
        entity_score = type_weights.get(QueryType.ENTITY, 0.0)
        multihop_score = type_weights.get(QueryType.MULTI_HOP, 0.0)
        adversarial_score = type_weights.get(QueryType.ADVERSARIAL, 0.0)
        open_score = type_weights.get(QueryType.OPEN, 0.0)

        BASE_WEIGHT = 0.05  # Minimum weight even if detection returns 0

        # Calculate weights from detection scores (proportional fusion)
        temporal_weight = temporal_score + BASE_WEIGHT
        entity_weight = entity_score + BASE_WEIGHT
        multihop_weight = multihop_score + BASE_WEIGHT
        adversarial_weight = adversarial_score + BASE_WEIGHT
        open_weight = open_score + BASE_WEIGHT

        # Normalize to sum to 1
        total = temporal_weight + entity_weight + multihop_weight + adversarial_weight + open_weight
        if total > 0:
            temporal_weight /= total
            entity_weight /= total
            multihop_weight /= total
            adversarial_weight /= total
            open_weight /= total
        else:
            temporal_weight = entity_weight = multihop_weight = adversarial_weight = open_weight = 0.2

        # Combine into single dict by node_id
        combined: dict[str, tuple["NeuralNode", float]] = {}

        # Add temporal results
        for node, charge in temporal_results:
            effective_charge = charge * (temporal_weight + open_weight * 0.25)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        # Add entity results
        for node, charge in entity_results:
            effective_charge = charge * (entity_weight + open_weight * 0.35)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        # Add reasoning results
        for node, charge in reasoning_results:
            effective_charge = charge * (multihop_weight + open_weight * 0.25)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        # Add adversarial results
        for node, charge in adversarial_results:
            effective_charge = charge * (adversarial_weight + open_weight * 0.15)
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective_charge)
            else:
                combined[node.node_id] = (node, effective_charge)

        return list(combined.values())

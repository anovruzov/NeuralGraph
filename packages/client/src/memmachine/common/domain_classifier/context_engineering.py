"""Context Engineering: TRUE semantic understanding for memory retrieval.

THE FUNDAMENTAL PROBLEM:
Memory systems fail because they don't UNDERSTAND the query.
They just do vector similarity on raw text - no semantic intelligence.

THE SOLUTION:
Before retrieval, we must UNDERSTAND:
1. What TYPE of question is this? (temporal, factual, relational, open)
2. What ENTITIES are involved? (people, places, things)
3. What ACTION or STATE is being asked about?
4. What CONSTRAINTS exist? (time, location, conditions)

This is TRUE CONTEXT ENGINEERING - the missing piece.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# QUERY DOMAIN TYPES
# =============================================================================

class QueryDomain(Enum):
    """The semantic domain of a query."""
    TEMPORAL = "temporal"
    FACTUAL = "factual"
    RELATIONAL = "relational"
    SPATIAL = "spatial"
    CAUSAL = "causal"
    QUANTITATIVE = "quantitative"
    OPEN_DOMAIN = "open_domain"


class QueryIntent(Enum):
    """What the user is trying to DO with the query."""
    RETRIEVE_FACT = "retrieve_fact"
    RETRIEVE_EVENT = "retrieve_event"
    RETRIEVE_RELATION = "retrieve_relation"
    RETRIEVE_SEQUENCE = "retrieve_sequence"
    RETRIEVE_SUMMARY = "retrieve_summary"
    RETRIEVE_COMPARISON = "retrieve_comparison"


# =============================================================================
# QUERY DECOMPOSITION RESULT
# =============================================================================

@dataclass
class QueryDecomposition:
    """The semantic decomposition of a query."""
    original_query: str
    primary_domain: QueryDomain
    domain_confidence: float
    secondary_domains: list[QueryDomain] = field(default_factory=list)
    intent: QueryIntent = QueryIntent.RETRIEVE_FACT
    subjects: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    temporal_markers: list[str] = field(default_factory=list)
    spatial_markers: list[str] = field(default_factory=list)
    seeking: str = ""
    boost_dimensions: dict[str, float] = field(default_factory=dict)
    filter_keywords: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"QueryDecomposition(\n"
            f"  query='{self.original_query[:50]}...'\n"
            f"  domain={self.primary_domain.value} ({self.domain_confidence:.2f})\n"
            f"  seeking={self.seeking}\n"
            f"  subjects={self.subjects}\n"
            f"  objects={self.objects}\n"
            f"  actions={self.actions}\n"
            f"  boost={self.boost_dimensions}\n"
            f")"
        )


# =============================================================================
# CONTEXT DOMAIN CLASSIFIER
# =============================================================================

class ContextDomainClassifier:
    """Classifies queries into semantic domains."""

    TEMPORAL_PATTERNS = [
        r'\bwhen\b', r'\bwhat time\b', r'\bwhat date\b', r'\bwhat day\b',
        r'\bwhat year\b', r'\bwhat month\b', r'\bhow long\b', r'\bhow often\b',
        r'\bbefore\b', r'\bafter\b', r'\bduring\b', r'\bwhile\b', r'\buntil\b',
        r'\bsince\b', r'\blast\s+(week|month|year|time)\b',
        r'\bnext\s+(week|month|year|time)\b',
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b',
        r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
        r'\b\d{4}\b', r'\b\d{1,2}/\d{1,2}\b',
    ]

    SPATIAL_PATTERNS = [
        r'\bwhere\b', r'\bwhat place\b', r'\bwhat location\b',
        r'\bin which\b.*\b(city|country|place|location)\b',
        r'\bat\b', r'\bnear\b', r'\bfrom\b.*\bto\b',
    ]

    CAUSAL_PATTERNS = [
        r'\bwhy\b', r'\bhow come\b', r'\bwhat caused\b', r'\bwhat led to\b',
        r'\bbecause\b', r'\breason\b', r'\bexplain\b',
    ]

    QUANTITATIVE_PATTERNS = [
        r'\bhow many\b', r'\bhow much\b', r'\bcount\b', r'\bnumber of\b',
        r'\btotal\b', r'\ball\b.*\b(the|of)\b',
    ]

    RELATIONAL_PATTERNS = [
        r'\bhow\b.*\brelated\b', r'\brelationship\b', r'\bconnection\b',
        r'\bbetween\b.*\band\b', r'\bwith\b',
    ]

    FACTUAL_PATTERNS = [
        r'^who\b', r'^what\b', r'^which\b', r'\bwho is\b', r'\bwho was\b',
        r'\bwhat is\b', r'\bwhat was\b', r'\bwhat did\b', r'\bwhat does\b',
    ]

    def __init__(self):
        self._temporal_re = [re.compile(p, re.IGNORECASE) for p in self.TEMPORAL_PATTERNS]
        self._spatial_re = [re.compile(p, re.IGNORECASE) for p in self.SPATIAL_PATTERNS]
        self._causal_re = [re.compile(p, re.IGNORECASE) for p in self.CAUSAL_PATTERNS]
        self._quantitative_re = [re.compile(p, re.IGNORECASE) for p in self.QUANTITATIVE_PATTERNS]
        self._relational_re = [re.compile(p, re.IGNORECASE) for p in self.RELATIONAL_PATTERNS]
        self._factual_re = [re.compile(p, re.IGNORECASE) for p in self.FACTUAL_PATTERNS]

    def classify(self, query: str) -> tuple[QueryDomain, float, list[QueryDomain]]:
        query_lower = query.lower().strip()
        scores: dict[QueryDomain, float] = {
            QueryDomain.TEMPORAL: self._score_temporal(query_lower),
            QueryDomain.SPATIAL: self._score_spatial(query_lower),
            QueryDomain.CAUSAL: self._score_causal(query_lower),
            QueryDomain.QUANTITATIVE: self._score_quantitative(query_lower),
            QueryDomain.RELATIONAL: self._score_relational(query_lower),
            QueryDomain.FACTUAL: self._score_factual(query_lower),
        }
        sorted_domains = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        primary_domain = sorted_domains[0][0]
        primary_score = sorted_domains[0][1]
        if primary_score < 0.3:
            primary_domain = QueryDomain.OPEN_DOMAIN
            confidence = 0.5
        else:
            confidence = min(1.0, primary_score)
        secondary = [d for d, s in sorted_domains[1:] if s > 0.2]
        return primary_domain, confidence, secondary

    def _score_temporal(self, query: str) -> float:
        matches = sum(1 for p in self._temporal_re if p.search(query))
        if query.startswith("when"):
            matches += 3
        return min(1.0, matches * 0.3)

    def _score_spatial(self, query: str) -> float:
        matches = sum(1 for p in self._spatial_re if p.search(query))
        if query.startswith("where"):
            matches += 3
        return min(1.0, matches * 0.3)

    def _score_causal(self, query: str) -> float:
        matches = sum(1 for p in self._causal_re if p.search(query))
        if query.startswith("why"):
            matches += 3
        return min(1.0, matches * 0.3)

    def _score_quantitative(self, query: str) -> float:
        matches = sum(1 for p in self._quantitative_re if p.search(query))
        if query.startswith("how many") or query.startswith("how much"):
            matches += 3
        return min(1.0, matches * 0.3)

    def _score_relational(self, query: str) -> float:
        matches = sum(1 for p in self._relational_re if p.search(query))
        return min(1.0, matches * 0.3)

    def _score_factual(self, query: str) -> float:
        matches = sum(1 for p in self._factual_re if p.search(query))
        if query.startswith("who") or query.startswith("what"):
            matches += 2
        return min(1.0, matches * 0.25)


# =============================================================================
# QUERY DECOMPOSER
# =============================================================================

class QueryDecomposer:
    """Decomposes queries into semantic components."""

    SEEKING_MAP = {
        "when": "WHEN", "where": "WHERE", "who": "WHO", "what": "WHAT",
        "why": "WHY", "how": "HOW", "which": "WHICH",
    }

    ACTION_PATTERNS = [
        r'\b(visit|visited|visiting)\b', r'\b(go|went|going|gone)\b',
        r'\b(meet|met|meeting)\b', r'\b(talk|talked|talking)\b',
        r'\b(say|said|saying)\b', r'\b(do|did|doing|done)\b',
        r'\b(make|made|making)\b', r'\b(get|got|getting)\b',
        r'\b(give|gave|giving|given)\b', r'\b(take|took|taking|taken)\b',
        r'\b(come|came|coming)\b', r'\b(see|saw|seeing|seen)\b',
        r'\b(know|knew|knowing|known)\b', r'\b(think|thought|thinking)\b',
        r'\b(want|wanted|wanting)\b', r'\b(use|used|using)\b',
        r'\b(find|found|finding)\b', r'\b(tell|told|telling)\b',
        r'\b(ask|asked|asking)\b', r'\b(work|worked|working)\b',
        r'\b(live|lived|living)\b', r'\b(move|moved|moving)\b',
        r'\b(buy|bought|buying)\b', r'\b(eat|ate|eating|eaten)\b',
        r'\b(drink|drank|drinking|drunk)\b', r'\b(play|played|playing)\b',
        r'\b(watch|watched|watching)\b', r'\b(read|reading)\b',
        r'\b(write|wrote|writing|written)\b', r'\b(start|started|starting)\b',
        r'\b(stop|stopped|stopping)\b', r'\b(begin|began|beginning|begun)\b',
        r'\b(end|ended|ending)\b', r'\b(plan|planned|planning)\b',
        r'\b(schedule|scheduled|scheduling)\b', r'\b(celebrate|celebrated|celebrating)\b',
        r'\b(attend|attended|attending)\b',
    ]

    TEMPORAL_MARKERS = [
        r'\b(yesterday|today|tomorrow)\b',
        r'\b(last|next|this)\s+(week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b',
        r'\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?(january|february|march|april|may|june|july|august|september|october|november|december)\b',
        r'\b(in|on|at|during|before|after)\s+\d{4}\b',
        r'\b\d{1,2}/\d{1,2}(/\d{2,4})?\b',
        r'\b(morning|afternoon|evening|night)\b',
        r'\b\d{1,2}:\d{2}\b',
        r'\b(early|late)\s+(morning|afternoon|evening)\b',
    ]

    SPATIAL_MARKERS = [
        r'\b(in|at|to|from)\s+[A-Z][a-z]+\b',
        r'\b(city|town|country|state|place|location|address|street|building)\b',
        r'\b(home|house|office|work|school|hospital|restaurant|store|shop)\b',
    ]

    def __init__(self):
        self._classifier = ContextDomainClassifier()
        self._action_re = [re.compile(p, re.IGNORECASE) for p in self.ACTION_PATTERNS]
        self._temporal_re = [re.compile(p, re.IGNORECASE) for p in self.TEMPORAL_MARKERS]
        self._spatial_re = [re.compile(p, re.IGNORECASE) for p in self.SPATIAL_MARKERS]

    def decompose(self, query: str) -> QueryDecomposition:
        query_lower = query.lower().strip()
        primary_domain, confidence, secondary_domains = self._classifier.classify(query)
        seeking = self._extract_seeking(query_lower)
        intent = self._determine_intent(primary_domain, seeking)
        subjects, objects = self._extract_entities(query)
        actions = self._extract_actions(query_lower)
        temporal_markers = self._extract_temporal_markers(query)
        spatial_markers = self._extract_spatial_markers(query)
        boost_dimensions = self._generate_boost_dimensions(primary_domain, secondary_domains, seeking)
        filter_keywords = self._generate_filter_keywords(subjects, objects, actions, temporal_markers, spatial_markers)

        return QueryDecomposition(
            original_query=query,
            primary_domain=primary_domain,
            domain_confidence=confidence,
            secondary_domains=secondary_domains,
            intent=intent,
            subjects=subjects,
            objects=objects,
            actions=actions,
            temporal_markers=temporal_markers,
            spatial_markers=spatial_markers,
            seeking=seeking,
            boost_dimensions=boost_dimensions,
            filter_keywords=filter_keywords,
        )

    def _extract_seeking(self, query: str) -> str:
        for word, seeking in self.SEEKING_MAP.items():
            if query.startswith(word) or f" {word} " in query:
                return seeking
        return "WHAT"

    def _determine_intent(self, domain: QueryDomain, seeking: str) -> QueryIntent:
        if domain == QueryDomain.TEMPORAL:
            return QueryIntent.RETRIEVE_EVENT if seeking == "WHEN" else QueryIntent.RETRIEVE_SEQUENCE
        elif domain == QueryDomain.RELATIONAL:
            return QueryIntent.RETRIEVE_RELATION
        elif domain == QueryDomain.QUANTITATIVE:
            return QueryIntent.RETRIEVE_SUMMARY
        elif domain == QueryDomain.CAUSAL:
            return QueryIntent.RETRIEVE_RELATION
        return QueryIntent.RETRIEVE_FACT

    def _extract_entities(self, query: str) -> tuple[list[str], list[str]]:
        subjects = []
        objects = []
        words = query.split()
        skip_first = words[0].lower() in self.SEEKING_MAP if words else False
        for i, word in enumerate(words):
            if i == 0 and skip_first:
                continue
            clean_word = re.sub(r'[^\w]', '', word)
            if clean_word and clean_word[0].isupper() and len(clean_word) > 1:
                if len(subjects) < 2:
                    subjects.append(clean_word)
                else:
                    objects.append(clean_word)
        return subjects, objects

    def _extract_actions(self, query: str) -> list[str]:
        actions = []
        for pattern in self._action_re:
            matches = pattern.findall(query)
            actions.extend(matches)
        return list(set(actions))

    def _extract_temporal_markers(self, query: str) -> list[str]:
        markers = []
        for pattern in self._temporal_re:
            matches = pattern.findall(query)
            if matches:
                for m in matches:
                    if isinstance(m, tuple):
                        markers.append(' '.join(m))
                    else:
                        markers.append(m)
        return list(set(markers))

    def _extract_spatial_markers(self, query: str) -> list[str]:
        markers = []
        for pattern in self._spatial_re:
            matches = pattern.findall(query)
            markers.extend(matches)
        return list(set(markers))

    def _generate_boost_dimensions(
        self, primary: QueryDomain, secondary: list[QueryDomain], seeking: str
    ) -> dict[str, float]:
        boosts: dict[str, float] = {}
        domain_boosts = {
            QueryDomain.TEMPORAL: {"temporal": 2.0},
            QueryDomain.SPATIAL: {"spatial": 2.0},
            QueryDomain.FACTUAL: {"entity": 1.5},
            QueryDomain.RELATIONAL: {"relational": 2.0},
            QueryDomain.CAUSAL: {"causal": 2.0},
            QueryDomain.QUANTITATIVE: {"quantitative": 2.0},
        }
        if primary in domain_boosts:
            boosts.update(domain_boosts[primary])
        for domain in secondary:
            if domain in domain_boosts:
                for dim, val in domain_boosts[domain].items():
                    if dim not in boosts:
                        boosts[dim] = val * 0.5
        seeking_boosts = {
            "WHEN": {"temporal": 2.5},
            "WHERE": {"spatial": 2.5},
            "WHO": {"entity": 2.0},
            "WHY": {"causal": 2.0},
        }
        if seeking in seeking_boosts:
            for dim, val in seeking_boosts[seeking].items():
                boosts[dim] = max(boosts.get(dim, 0), val)
        return boosts

    def _generate_filter_keywords(
        self, subjects: list[str], objects: list[str], actions: list[str],
        temporal: list[str], spatial: list[str]
    ) -> list[str]:
        keywords = []
        keywords.extend(subjects)
        keywords.extend(objects)
        keywords.extend(actions)
        return [k.lower() for k in keywords if k]


# =============================================================================
# SEMANTIC ROUTER
# =============================================================================

class SemanticRouter:
    """Routes queries to optimal retrieval strategies."""

    def __init__(self):
        self._decomposer = QueryDecomposer()

    def route(self, query: str) -> QueryDecomposition:
        return self._decomposer.decompose(query)

    def get_wave_amplitudes(self, decomposition: QueryDecomposition) -> dict[str, float]:
        amplitudes = {
            "temporal": 0.2, "entity": 0.2, "relational": 0.2, "action": 0.2,
            "state": 0.2, "spatial": 0.2, "causal": 0.2, "emotional": 0.1,
            "quantitative": 0.1,
        }
        for dim, boost in decomposition.boost_dimensions.items():
            if dim in amplitudes:
                amplitudes[dim] = min(1.0, amplitudes[dim] * boost)
        max_amp = max(amplitudes.values()) if amplitudes else 1.0
        if max_amp > 0:
            amplitudes = {k: v / max_amp for k, v in amplitudes.items()}
        return amplitudes

    def should_filter_by_entities(self, decomposition: QueryDecomposition) -> bool:
        return len(decomposition.subjects) > 0 or len(decomposition.objects) > 0

    def get_entity_filter(self, decomposition: QueryDecomposition) -> list[str]:
        return decomposition.subjects + decomposition.objects

    def should_boost_temporal(self, decomposition: QueryDecomposition) -> bool:
        return decomposition.primary_domain == QueryDomain.TEMPORAL or decomposition.seeking == "WHEN"

    def get_retrieval_strategy(self, decomposition: QueryDecomposition) -> str:
        domain = decomposition.primary_domain
        if domain == QueryDomain.TEMPORAL:
            return "electron"
        elif domain == QueryDomain.RELATIONAL:
            return "graph"
        elif domain == QueryDomain.FACTUAL:
            return "hybrid"
        return "electron"


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def classify_query(query: str) -> QueryDecomposition:
    """Classify and decompose a query."""
    router = SemanticRouter()
    return router.route(query)


def get_context_amplitudes(query: str) -> dict[str, float]:
    """Get context-aware wave amplitudes for a query."""
    router = SemanticRouter()
    decomposition = router.route(query)
    return router.get_wave_amplitudes(decomposition)

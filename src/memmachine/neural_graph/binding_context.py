"""Binding Context - The 4D Turn-Based Resolution System.

MATHEMATICAL FOUNDATION:
In dialogue, each turn t creates a binding context B_t that enables
resolution of pronouns, temporal expressions, and anaphora.

B_t = {
    speaker: S_t,           # Current speaker
    timestamp: τ_t,         # When this turn occurred
    referents: {r₁, r₂...}, # Entities mentioned (salience-ordered)
    anaphora: {a₁ → r_j},   # Resolved pronoun mappings
}

The Pronoun Resolution Function:
    Resolve(pronoun, B_t) → entity

The Temporal Resolution Function:
    Resolve(temporal_expr, τ_t) → datetime | (datetime, datetime)

These functions transform raw conversational input into 4D-enriched memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from collections import deque


@dataclass
class Referent:
    """An entity that can be referenced by pronouns."""
    name: str
    salience: float  # How recently/prominently mentioned
    is_plural: bool = False
    gender: str | None = None  # 'male', 'female', 'neutral', None

    def decay(self, factor: float = 0.9) -> None:
        """Decay salience over time."""
        self.salience *= factor


@dataclass
class BindingContext:
    """The 4D binding context for a conversation turn.

    Tracks:
    - Current speaker (for I/me/my resolution)
    - Previous speaker (for you/your resolution)
    - Timestamp (for temporal resolution)
    - Active referents (for it/this/that resolution)
    - Conversation history (for multi-turn anaphora)
    """
    speaker: str = ""
    previous_speaker: str = ""
    timestamp: datetime | None = None
    referents: list[Referent] = field(default_factory=list)
    turn_index: int = 0

    # History of recent turns for context
    _history: deque = field(default_factory=lambda: deque(maxlen=10))

    def update_turn(
        self,
        speaker: str,
        timestamp: datetime | None,
        content: str,
    ) -> None:
        """Update context for a new turn.

        This is called BEFORE processing each message to set up
        the binding context for pronoun/temporal resolution.
        """
        # Update speaker chain
        self.previous_speaker = self.speaker
        self.speaker = speaker
        self.timestamp = timestamp
        self.turn_index += 1

        # Decay existing referents
        for ref in self.referents:
            ref.decay()

        # Extract new referents from content
        new_refs = self._extract_referents(content)

        # Add new referents with high salience
        for ref in new_refs:
            # Check if already exists
            existing = next((r for r in self.referents if r.name.lower() == ref.name.lower()), None)
            if existing:
                existing.salience = ref.salience  # Refresh salience
            else:
                self.referents.append(ref)

        # Sort by salience (most salient first)
        self.referents.sort(key=lambda r: r.salience, reverse=True)

        # Keep only top referents
        self.referents = self.referents[:20]

        # Add to history
        self._history.append({
            'speaker': speaker,
            'timestamp': timestamp,
            'content': content,
            'turn': self.turn_index,
        })

    def _extract_referents(self, content: str) -> list[Referent]:
        """Extract entity referents from content."""
        referents = []

        # Find capitalized words (proper nouns)
        # Exclude common non-name words
        exclude = {
            'the', 'this', 'that', 'what', 'when', 'where', 'who', 'how',
            'yes', 'no', 'and', 'but', 'hey', 'hi', 'hello', 'thanks',
            'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
            'saturday', 'sunday', 'january', 'february', 'march', 'april',
            'may', 'june', 'july', 'august', 'september', 'october',
            'november', 'december', 'lgbtq',
        }

        words = re.findall(r'\b[A-Z][a-z]+\b', content)
        for word in words:
            if word.lower() not in exclude:
                referents.append(Referent(
                    name=word,
                    salience=1.0,
                    is_plural=False,
                ))

        # Check for plural referents (e.g., "my friends", "the team")
        plural_patterns = [
            r'\b(?:my|the|our)\s+(\w+s)\b',
            r'\b(\w+)\s+and\s+(\w+)\b',  # "X and Y" creates plural referent
        ]
        for pattern in plural_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    # Combined referent like "X and Y"
                    name = f"{match[0]} and {match[1]}"
                else:
                    name = match
                referents.append(Referent(
                    name=name,
                    salience=0.8,
                    is_plural=True,
                ))

        return referents

    def resolve_pronoun(self, pronoun: str) -> str | None:
        """Resolve a pronoun to its referent.

        The Mathematical Rule:
        Resolve(pronoun, B_t) =
            if pronoun ∈ {I, me, my, myself}: return S_t
            if pronoun ∈ {you, your}: return S_{t-1}
            if pronoun ∈ {it, this, that}: return argmax_{r} Salience(r)
            if pronoun ∈ {they, them, their}: return last_plural_referent
        """
        pronoun_lower = pronoun.lower()

        # First person → current speaker
        if pronoun_lower in {'i', 'me', 'my', 'myself', "i'm", "i've", "i'd", "i'll"}:
            return self.speaker if self.speaker else None

        # Second person → previous speaker (addressee)
        if pronoun_lower in {'you', 'your', 'yourself', "you're", "you've", "you'd"}:
            return self.previous_speaker if self.previous_speaker else None

        # Third person singular → most salient singular referent
        if pronoun_lower in {'he', 'him', 'his', 'himself'}:
            for ref in self.referents:
                if not ref.is_plural and ref.gender in {'male', None}:
                    return ref.name
            return None

        if pronoun_lower in {'she', 'her', 'hers', 'herself'}:
            for ref in self.referents:
                if not ref.is_plural and ref.gender in {'female', None}:
                    return ref.name
            return None

        # Neuter singular → most salient singular referent
        if pronoun_lower in {'it', 'its', 'itself', 'this', 'that'}:
            for ref in self.referents:
                if not ref.is_plural:
                    return ref.name
            return None

        # Plural → most salient plural referent
        if pronoun_lower in {'they', 'them', 'their', 'themselves'}:
            for ref in self.referents:
                if ref.is_plural:
                    return ref.name
            return None

        return None

    def resolve_temporal(self, temporal_expr: str) -> datetime | tuple[datetime, datetime] | int | None:
        """Resolve a temporal expression to absolute datetime(s).

        The Mathematical Rule:
        Resolve(temporal_expr, τ_t) =
            "yesterday" → τ_t - 1_day
            "last week" → τ_t - 7_days
            "last year" → τ_t.year - 1
            "N years ago" → τ_t.year - N
            "recently" → (τ_t - 30_days, τ_t)
        """
        if not self.timestamp:
            return None

        expr_lower = temporal_expr.lower().strip()
        τ = self.timestamp

        # Exact day offsets
        if expr_lower == 'yesterday':
            return τ - timedelta(days=1)

        if expr_lower == 'today':
            return τ

        if expr_lower == 'tomorrow':
            return τ + timedelta(days=1)

        # Week-based
        if expr_lower == 'last week':
            return τ - timedelta(days=7)

        if expr_lower == 'this week':
            return τ

        # Month-based
        if expr_lower == 'last month':
            return τ - timedelta(days=30)

        if expr_lower == 'this month':
            return τ

        # Year-based
        if expr_lower == 'last year':
            return τ.year - 1  # Return just the year as int

        if expr_lower == 'this year':
            return τ.year

        # N years ago
        years_match = re.match(r'(\d+)\s+years?\s+ago', expr_lower)
        if years_match:
            n = int(years_match.group(1))
            return τ.year - n

        # N months ago
        months_match = re.match(r'(\d+)\s+months?\s+ago', expr_lower)
        if months_match:
            n = int(months_match.group(1))
            return τ - timedelta(days=n * 30)

        # N weeks ago
        weeks_match = re.match(r'(\d+)\s+weeks?\s+ago', expr_lower)
        if weeks_match:
            n = int(weeks_match.group(1))
            return τ - timedelta(days=n * 7)

        # N days ago
        days_match = re.match(r'(\d+)\s+days?\s+ago', expr_lower)
        if days_match:
            n = int(days_match.group(1))
            return τ - timedelta(days=n)

        # Fuzzy ranges
        if expr_lower == 'recently':
            return (τ - timedelta(days=30), τ)

        if expr_lower == 'a while ago':
            return (τ - timedelta(days=90), τ - timedelta(days=30))

        if expr_lower == 'long ago':
            return (τ - timedelta(days=365), τ - timedelta(days=90))

        return None


class ContentBinder:
    """Binds raw content to 4D coordinates using binding context.

    This is the INGESTION transformation:
    Raw: "I went to LGBTQ support group yesterday"
    Bound: "Caroline went to LGBTQ support group on 2023-05-07"

    The transformation makes memories SELF-CONTAINED and SEARCHABLE.
    """

    # Temporal expression patterns
    TEMPORAL_PATTERNS = [
        (r'\byesterday\b', 'yesterday'),
        (r'\btoday\b', 'today'),
        (r'\btomorrow\b', 'tomorrow'),
        (r'\blast\s+week\b', 'last week'),
        (r'\bthis\s+week\b', 'this week'),
        (r'\blast\s+month\b', 'last month'),
        (r'\bthis\s+month\b', 'this month'),
        (r'\blast\s+year\b', 'last year'),
        (r'\bthis\s+year\b', 'this year'),
        (r'\b(\d+)\s+years?\s+ago\b', 'years_ago'),
        (r'\b(\d+)\s+months?\s+ago\b', 'months_ago'),
        (r'\b(\d+)\s+weeks?\s+ago\b', 'weeks_ago'),
        (r'\b(\d+)\s+days?\s+ago\b', 'days_ago'),
        (r'\brecently\b', 'recently'),
        (r'\ba\s+while\s+ago\b', 'a while ago'),
    ]

    # Pronoun patterns (word boundary important!)
    PRONOUN_PATTERNS = [
        # First person - replace with speaker
        (r"\bI'm\b", "{speaker} is"),
        (r"\bIm\b", "{speaker} is"),
        (r"\bI've\b", "{speaker} has"),
        (r"\bIve\b", "{speaker} has"),
        (r"\bI'd\b", "{speaker} would"),
        (r"\bId\b", "{speaker} would"),
        (r"\bI'll\b", "{speaker} will"),
        (r"\bIll\b", "{speaker} will"),
        (r"\bI\b", "{speaker}"),
        (r"\bme\b", "{speaker_lower}"),
        (r"\bmy\b", "{speaker}'s"),
        (r"\bmyself\b", "{speaker_lower}"),
    ]

    def __init__(self, context: BindingContext):
        self.context = context

    def bind(self, content: str) -> str:
        """Transform raw content into 4D-bound content.

        This performs:
        1. Pronoun resolution (I/me/my → speaker)
        2. Temporal resolution (yesterday → actual date)
        3. Context prepending (ensure speaker attribution)
        """
        bound = content

        # 1. PRONOUN BINDING
        if self.context.speaker:
            speaker = self.context.speaker.capitalize()
            speaker_lower = self.context.speaker.lower()

            for pattern, replacement in self.PRONOUN_PATTERNS:
                repl = replacement.format(speaker=speaker, speaker_lower=speaker_lower)
                bound = re.sub(pattern, repl, bound, flags=re.IGNORECASE)

        # 2. TEMPORAL BINDING
        if self.context.timestamp:
            for pattern, expr_type in self.TEMPORAL_PATTERNS:
                match = re.search(pattern, bound, re.IGNORECASE)
                if match:
                    resolved = self.context.resolve_temporal(match.group(0))
                    if resolved is not None:
                        if isinstance(resolved, datetime):
                            replacement = f"on {resolved.strftime('%Y-%m-%d')}"
                        elif isinstance(resolved, int):
                            replacement = f"in {resolved}"
                        elif isinstance(resolved, tuple):
                            replacement = f"around {resolved[0].strftime('%Y-%m-%d')}"
                        else:
                            continue
                        bound = re.sub(pattern, replacement, bound, flags=re.IGNORECASE)

        # 3. SPEAKER PREPENDING (if not already present)
        if self.context.speaker:
            speaker_lower = self.context.speaker.lower()
            if speaker_lower not in bound.lower():
                bound = f"{self.context.speaker.capitalize()}: {bound}"

        return bound


class QueryClassifier:
    """Classifies queries into 5 mathematical types.

    Query Types:
    1. SINGLE_HOP: Direct fact lookup (e=known, s=known, t=?, r=?)
    2. TEMPORAL: Time-based query (e=known, t=constraint, s=known)
    3. MULTI_HOP: Chain of lookups (Q₁ ∘ Q₂)
    4. OPEN_DOMAIN: Broad semantic query (e=?, s=fuzzy)
    5. ADVERSARIAL: Disambiguation required (ambiguous e or overlapping s)
    """

    # Patterns for each query type
    TEMPORAL_PATTERNS = [
        r'\bwhen\b',
        r'\bwhat\s+time\b',
        r'\bwhat\s+date\b',
        r'\bhow\s+long\s+ago\b',
        r'\bwhat\s+year\b',
        r'\bwhat\s+month\b',
        r'\bwhat\s+day\b',
    ]

    MULTI_HOP_PATTERNS = [
        r"\b\w+'s\s+\w+'s\b",  # X's Y's Z (possessive chain)
        r'\bfriend\s+of\b',
        r'\bwho\s+does\s+\w+\s+know\b',
        r"\bwhat\s+does\s+\w+'s\s+\w+\b",
    ]

    OPEN_DOMAIN_PATTERNS = [
        r'\bwhat\s+(?:kind|type|sort)\s+of\b',
        r'\bwhat\s+are\s+(?:some|all|the)\b',
        r'\bwhat\s+(?:hobbies|activities|interests)\b',
        r'\bwhat\s+does\s+\w+\s+(?:like|enjoy|prefer)\b',
        r'\btell\s+me\s+about\b',
        r'\bdescribe\b',
    ]

    @classmethod
    def classify(cls, query: str) -> tuple[str, dict[str, Any]]:
        """Classify a query and extract its coordinates.

        Returns:
            (query_type, coordinates)

        Where coordinates = {
            'entities': list of entity constraints,
            'temporals': list of temporal constraints,
            'semantics': list of semantic keywords,
            'unknowns': list of what's being asked for,
        }
        """
        query_lower = query.lower()

        # Extract coordinates first
        coords = cls._extract_coordinates(query)

        # Check for temporal query
        for pattern in cls.TEMPORAL_PATTERNS:
            if re.search(pattern, query_lower):
                coords['unknowns'].append('time')
                return ('temporal', coords)

        # Check for multi-hop
        for pattern in cls.MULTI_HOP_PATTERNS:
            if re.search(pattern, query_lower):
                return ('multi_hop', coords)

        # Check for open domain
        for pattern in cls.OPEN_DOMAIN_PATTERNS:
            if re.search(pattern, query_lower):
                return ('open_domain', coords)

        # Check for adversarial (multiple entities, or specific speaker mentioned)
        if len(coords['entities']) > 1:
            return ('adversarial', coords)

        # Default to single hop
        return ('single_hop', coords)

    @classmethod
    def _extract_coordinates(cls, query: str) -> dict[str, list]:
        """Extract 4D coordinates from query."""
        coords = {
            'entities': [],
            'temporals': [],
            'semantics': [],
            'relations': [],
            'unknowns': [],
        }

        # Extract entities (capitalized words)
        entities = re.findall(r'\b[A-Z][a-z]+\b', query)
        exclude = {'What', 'When', 'Where', 'Who', 'How', 'Why', 'Does', 'Did', 'Is', 'Are', 'Was', 'Were'}
        coords['entities'] = [e for e in entities if e not in exclude]

        # Extract temporal markers
        temporal_words = re.findall(
            r'\b(?:yesterday|today|tomorrow|last|this|next|ago|year|month|week|day|when|time|date)\b',
            query.lower()
        )
        coords['temporals'] = temporal_words

        # Extract semantic keywords (nouns and meaningful words)
        # Remove common question words and stopwords
        stopwords = {
            'what', 'when', 'where', 'who', 'how', 'why', 'does', 'did', 'is', 'are',
            'was', 'were', 'the', 'a', 'an', 'to', 'of', 'in', 'on', 'at', 'for',
            'with', 'and', 'or', 'but', 'not', 'has', 'have', 'had', 'do', 'be',
        }
        words = re.findall(r'\b[a-z]{4,}\b', query.lower())
        coords['semantics'] = [w for w in words if w not in stopwords]

        # Extract relations (verbs)
        relation_verbs = {
            'go', 'went', 'attend', 'visit', 'start', 'begin', 'join', 'leave',
            'work', 'live', 'move', 'meet', 'know', 'like', 'love', 'hate',
            'make', 'create', 'paint', 'run', 'walk', 'play', 'watch',
        }
        words_lower = [w.lower() for w in re.findall(r'\b\w+\b', query)]
        coords['relations'] = [w for w in words_lower if w in relation_verbs]

        return coords

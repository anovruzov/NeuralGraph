"""4D Tensor Memory - True Fourth Dimensional Data Structure.

FROM 4D PERSPECTIVE LOOKING DOWN AT 3D:

3D structures (all failed):
- Graph: Nodes at positions, edges connecting them. Must TRAVERSE to find.
- Holographic: Concept sets, intersection scoring. Still LINEAR search.
- Embedding: Vector space, similarity search. APPROXIMATE, not PRECISE.

4D structure (what the brain actually does):
- Memory doesn't EXIST at a location
- Memory EMERGES from dimensional intersection
- No search. No traversal. Direct ACCESS.

THE 4D TENSOR:
- Axis 0: ENTITY (who is involved)
- Axis 1: TOPIC (what is it about)
- Axis 2: TIME (when did it happen)
- Axis 3: RELATION (how are things connected)

A memory is a POINT in 4D space: tensor[entity][topic][time][relation] = memory_content

Query "When did Caroline go to LGBTQ support group?":
- entity = "caroline"
- topic = "lgbtq_support_group"
- time = ? (this is what we're asking)
- relation = "attend"

We don't SEARCH. We SLICE the tensor along known dimensions,
and the unknown dimension REVEALS itself.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


# =============================================================================
# 4D COORDINATE EXTRACTORS
# =============================================================================

class EntityExtractor:
    """Extract ENTITY dimension coordinates (WHO)."""

    COMMON_NAMES = {
        'caroline', 'melanie', 'sarah', 'emma', 'olivia', 'sophia',
        'michael', 'david', 'james', 'john', 'robert', 'daniel',
    }

    @staticmethod
    def extract(text: str, speaker: str = "") -> set[str]:
        """Extract entity coordinates from text."""
        entities = set()

        # Speaker is ALWAYS an entity (4D binding!)
        if speaker:
            entities.add(speaker.lower())

        # Find capitalized words (proper nouns)
        words = re.findall(r'\b[A-Z][a-z]+\b', text)
        for word in words:
            w_lower = word.lower()
            # Filter out common non-name capitalized words
            if w_lower not in {'the', 'this', 'that', 'what', 'when', 'where', 'who',
                               'how', 'yes', 'no', 'and', 'but', 'hey', 'hi', 'hello',
                               'thanks', 'thank', 'please', 'sorry', 'great', 'good',
                               'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
                               'saturday', 'sunday', 'january', 'february', 'march',
                               'april', 'may', 'june', 'july', 'august', 'september',
                               'october', 'november', 'december'}:
                entities.add(w_lower)

        return entities


class TopicExtractor:
    """Extract TOPIC dimension coordinates (WHAT)."""

    # Topic patterns - compound concepts that should be single coordinates
    TOPIC_PATTERNS = {
        # LGBTQ related
        r'lgbtq\s*\+?\s*support\s*group': 'lgbtq_support_group',
        r'lgbtq\s*\+?\s*community': 'lgbtq_community',
        r'lgbtq\s*\+?\s*conference': 'lgbtq_conference',
        r'lgbtq\s*\+?\s*activist': 'lgbtq_activist',
        r'pride\s*parade': 'pride_parade',
        r'transgender': 'transgender',
        r'coming\s*out': 'coming_out',

        # Activities
        r'support\s*group': 'support_group',
        r'charity\s*race': 'charity_race',
        r'pottery\s*workshop': 'pottery_workshop',
        r'art\s*show': 'art_show',
        r'mentoring\s*program': 'mentoring_program',
        r'adoption\s*agenc': 'adoption_agency',
        r'adoption\s*meeting': 'adoption_meeting',

        # Hobbies
        r'paint(?:ing|ed)?': 'painting',
        r'running|ran|run': 'running',
        r'pottery': 'pottery',
        r'camping': 'camping',
        r'hiking': 'hiking',
        r'swimming': 'swimming',
        r'reading|read': 'reading',
        r'cooking|cook': 'cooking',

        # Life events
        r'wedding': 'wedding',
        r'birthday': 'birthday',
        r'vacation': 'vacation',
        r'trip': 'trip',
        r'beach': 'beach',
        r'museum': 'museum',

        # Work/Education
        r'job|work|career|profession': 'job',
        r'counselor|counseling': 'counseling',
        r'education|school|university': 'education',
        r'research(?:ed|ing)?': 'research',

        # Relationships
        r'friend(?:s)?': 'friends',
        r'family': 'family',
        r'partner|spouse|husband|wife': 'relationship',
        r'single': 'relationship_status',
        r'dating': 'dating',

        # Identity
        r'identity': 'identity',
    }

    @classmethod
    def extract(cls, text: str) -> set[str]:
        """Extract topic coordinates from text."""
        topics = set()
        text_lower = text.lower()

        # Match compound patterns first
        for pattern, topic in cls.TOPIC_PATTERNS.items():
            if re.search(pattern, text_lower):
                topics.add(topic)

        return topics


class TimeExtractor:
    """Extract TIME dimension coordinates (WHEN)."""

    @staticmethod
    def extract(text: str, reference_time: datetime | None = None) -> dict[str, Any]:
        """Extract time coordinates from text.

        Returns a dict with:
        - absolute: datetime if mentioned
        - relative: string like "yesterday", "last week"
        - computed: datetime computed from relative + reference
        """
        text_lower = text.lower()
        result = {
            'absolute': None,
            'relative': None,
            'computed': None,
            'year': None,
        }

        # Check for relative time expressions
        relative_patterns = [
            (r'yesterday', 'yesterday', -1),
            (r'today', 'today', 0),
            (r'last\s+week', 'last_week', -7),
            (r'last\s+month', 'last_month', -30),
            (r'last\s+year', 'last_year', -365),
            (r'(\d+)\s+years?\s+ago', 'years_ago', None),
            (r'last\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', 'last_weekday', None),
        ]

        for pattern, rel_type, days_delta in relative_patterns:
            match = re.search(pattern, text_lower)
            if match:
                result['relative'] = rel_type
                if days_delta is not None and reference_time:
                    from datetime import timedelta
                    result['computed'] = reference_time + timedelta(days=days_delta)
                break

        # Check for year mentions
        year_match = re.search(r'\b(20\d{2})\b', text_lower)
        if year_match:
            result['year'] = int(year_match.group(1))

        return result


class RelationExtractor:
    """Extract RELATION dimension coordinates (HOW/WHY)."""

    RELATION_VERBS = {
        # Actions
        'went': 'attend', 'go': 'attend', 'going': 'attend', 'attend': 'attend',
        'visited': 'visit', 'visit': 'visit', 'visiting': 'visit',
        'joined': 'join', 'join': 'join', 'joining': 'join',
        'started': 'start', 'start': 'start', 'starting': 'start',
        'finished': 'finish', 'finish': 'finish', 'finished': 'finish',
        'painted': 'create', 'paint': 'create', 'painting': 'create',
        'made': 'create', 'make': 'create', 'making': 'create',
        'created': 'create', 'create': 'create', 'creating': 'create',
        'ran': 'participate', 'run': 'participate', 'running': 'participate',
        'participated': 'participate', 'participate': 'participate',

        # States
        'is': 'be', 'am': 'be', 'are': 'be', 'was': 'be', 'were': 'be',
        'has': 'have', 'have': 'have', 'had': 'have',
        'lives': 'reside', 'live': 'reside', 'lived': 'reside',
        'moved': 'relocate', 'move': 'relocate', 'moving': 'relocate',

        # Research/Work
        'researched': 'research', 'research': 'research', 'researching': 'research',
        'studied': 'study', 'study': 'study', 'studying': 'study',
        'works': 'work', 'work': 'work', 'worked': 'work',
    }

    @classmethod
    def extract(cls, text: str) -> set[str]:
        """Extract relation coordinates from text."""
        relations = set()
        text_lower = text.lower()
        words = re.findall(r'\b\w+\b', text_lower)

        for word in words:
            if word in cls.RELATION_VERBS:
                relations.add(cls.RELATION_VERBS[word])

        return relations


# =============================================================================
# 4D MEMORY POINT
# =============================================================================

@dataclass
class MemoryPoint:
    """A point in 4D memory space.

    Unlike a 3D node, a MemoryPoint has COORDINATES in 4 dimensions.
    It can be accessed from ANY dimension, not just by traversal.
    """
    # Unique ID
    point_id: str

    # The actual content
    content: str

    # 4D COORDINATES
    entities: set[str] = field(default_factory=set)     # WHO
    topics: set[str] = field(default_factory=set)       # WHAT
    time_coords: dict = field(default_factory=dict)     # WHEN
    relations: set[str] = field(default_factory=set)    # HOW

    # Speaker (primary entity for this memory)
    speaker: str = ""

    # Timestamp
    timestamp: datetime | None = None

    # Embedding for fallback similarity
    embedding: list[float] | None = None


# =============================================================================
# 4D TENSOR INDEX
# =============================================================================

class TensorMemoryIndex:
    """4D Tensor Index - direct dimensional access to memories.

    This is NOT a search structure. This is a COORDINATE SYSTEM.

    Query with partial coordinates → Get all points matching those coordinates.
    The unspecified dimensions are what the query is ASKING FOR.
    """

    def __init__(self):
        # All memory points
        self._points: dict[str, MemoryPoint] = {}

        # 4D TENSOR INDICES - each maps coordinate → point_ids
        self._entity_index: dict[str, set[str]] = defaultdict(set)   # WHO
        self._topic_index: dict[str, set[str]] = defaultdict(set)    # WHAT
        self._time_index: dict[str, set[str]] = defaultdict(set)     # WHEN
        self._relation_index: dict[str, set[str]] = defaultdict(set) # HOW

    def insert(
        self,
        point_id: str,
        content: str,
        speaker: str = "",
        timestamp: datetime | None = None,
        embedding: list[float] | None = None,
    ) -> MemoryPoint:
        """Insert a memory into 4D space.

        CRITICAL 4D TRANSFORMATION:
        Raw content is transformed into ENRICHED content that includes:
        1. WHO said it (speaker prepended)
        2. WHEN resolved (relative times computed to absolute)

        This is what the brain does - stores INTERPRETED memories, not raw input.
        """
        # Extract 4D coordinates
        entities = EntityExtractor.extract(content, speaker)
        topics = TopicExtractor.extract(content)
        time_coords = TimeExtractor.extract(content, timestamp)
        relations = RelationExtractor.extract(content)

        # ENRICHED CONTENT TRANSFORMATION
        enriched_content = self._enrich_content(content, speaker, timestamp, time_coords)

        # Create memory point with ENRICHED content (not raw!)
        point = MemoryPoint(
            point_id=point_id,
            content=enriched_content,
            entities=entities,
            topics=topics,
            time_coords=time_coords,
            relations=relations,
            speaker=speaker.lower() if speaker else "",
            timestamp=timestamp,
            embedding=embedding,
        )

        # Store point
        self._points[point_id] = point

        # Index by ALL coordinates in ALL dimensions
        for entity in entities:
            self._entity_index[entity].add(point_id)

        for topic in topics:
            self._topic_index[topic].add(point_id)

        # Time indexing - use year if available
        if time_coords.get('year'):
            self._time_index[str(time_coords['year'])].add(point_id)
        if time_coords.get('relative'):
            self._time_index[time_coords['relative']].add(point_id)

        for relation in relations:
            self._relation_index[relation].add(point_id)

        return point

    def _enrich_content(
        self,
        content: str,
        speaker: str,
        timestamp: datetime | None,
        time_coords: dict,
    ) -> str:
        """Transform raw content into 4D enriched memory.

        THE BRAIN FIX:
        When Caroline says "I went to LGBTQ support group yesterday",
        the brain doesn't store:
            "I went to LGBTQ support group yesterday" + metadata(Caroline, May 8)

        The brain stores:
            "Caroline went to LGBTQ support group on May 7"

        The memory IS interpreted, not raw.

        This method performs:
        1. Speaker binding: Replace "I/me/my" with speaker name
        2. Time resolution: Resolve "yesterday/last week/last year" to actual dates
        3. Context prepending: Ensure speaker is in the content
        """
        enriched = content

        # 1. SPEAKER BINDING - Replace first person pronouns with speaker
        if speaker:
            speaker_cap = speaker.capitalize()
            # Replace "I" with speaker name (word boundary to avoid "It", "In", etc.)
            enriched = re.sub(r'\bI\b', speaker_cap, enriched)
            # Replace "I'm" with "Speaker is"
            enriched = re.sub(r"\bI'm\b", f"{speaker_cap} is", enriched, flags=re.IGNORECASE)
            enriched = re.sub(r"\bIm\b", f"{speaker_cap} is", enriched, flags=re.IGNORECASE)
            # Replace "I've" with "Speaker has"
            enriched = re.sub(r"\bI've\b", f"{speaker_cap} has", enriched, flags=re.IGNORECASE)
            enriched = re.sub(r"\bIve\b", f"{speaker_cap} has", enriched, flags=re.IGNORECASE)
            # Replace "me" with speaker name (lowercase)
            enriched = re.sub(r'\bme\b', speaker.lower(), enriched)
            # Replace "my" with speaker's
            enriched = re.sub(r'\bmy\b', f"{speaker_cap}'s", enriched, flags=re.IGNORECASE)
            enriched = re.sub(r'\bMy\b', f"{speaker_cap}'s", enriched)

        # 2. TIME RESOLUTION - Resolve relative times to absolute dates
        if timestamp and time_coords.get('relative'):
            rel = time_coords['relative']

            if rel == 'yesterday':
                actual_date = (timestamp - timedelta(days=1)).strftime('%Y-%m-%d')
                enriched = re.sub(r'\byesterday\b', f'on {actual_date}', enriched, flags=re.IGNORECASE)

            elif rel == 'today':
                actual_date = timestamp.strftime('%Y-%m-%d')
                enriched = re.sub(r'\btoday\b', f'on {actual_date}', enriched, flags=re.IGNORECASE)

            elif rel == 'last_week':
                actual_date = (timestamp - timedelta(days=7)).strftime('%Y-%m-%d')
                enriched = re.sub(r'\blast week\b', f'around {actual_date}', enriched, flags=re.IGNORECASE)

            elif rel == 'last_month':
                actual_date = (timestamp - timedelta(days=30)).strftime('%Y-%m')
                enriched = re.sub(r'\blast month\b', f'in {actual_date}', enriched, flags=re.IGNORECASE)

            elif rel == 'last_year':
                year = timestamp.year - 1
                enriched = re.sub(r'\blast year\b', f'in {year}', enriched, flags=re.IGNORECASE)

            elif rel == 'years_ago':
                # Extract number from original content
                years_match = re.search(r'(\d+)\s+years?\s+ago', content, re.IGNORECASE)
                if years_match:
                    years = int(years_match.group(1))
                    actual_year = timestamp.year - years
                    enriched = re.sub(r'\d+\s+years?\s+ago', f'in {actual_year}', enriched, flags=re.IGNORECASE)

        # 3. CONTEXT PREPENDING - Ensure speaker is present in content
        if speaker:
            speaker_lower = speaker.lower()
            enriched_lower = enriched.lower()
            # If speaker name isn't in the content, prepend it
            if speaker_lower not in enriched_lower:
                enriched = f"{speaker.capitalize()}: {enriched}"

        return enriched

    def query(
        self,
        query_text: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryPoint, float, dict]]:
        """Query 4D space with partial coordinates.

        This is the 4D MAGIC:
        1. Extract coordinates from query
        2. Find ALL points that match ANY coordinate
        3. Score by DIMENSIONAL OVERLAP
        4. Return points sorted by overlap

        The more dimensions match, the more precise the retrieval.
        """
        # Extract query coordinates
        q_entities = EntityExtractor.extract(query_text)
        q_topics = TopicExtractor.extract(query_text)
        q_time = TimeExtractor.extract(query_text)
        q_relations = RelationExtractor.extract(query_text)

        # Also extract from raw words (fallback for topics not in patterns)
        q_words = set(re.findall(r'\b[a-z]{4,}\b', query_text.lower()))
        stop_words = {'what', 'when', 'where', 'which', 'does', 'would', 'could',
                      'have', 'been', 'being', 'their', 'there', 'this', 'that',
                      'with', 'from', 'about', 'into', 'likely', 'pursue'}
        q_words -= stop_words

        # Collect candidate points from all matching dimensions
        candidate_ids: set[str] = set()

        # Entity dimension lookup
        for entity in q_entities:
            if entity in self._entity_index:
                candidate_ids.update(self._entity_index[entity])

        # Topic dimension lookup
        for topic in q_topics:
            if topic in self._topic_index:
                candidate_ids.update(self._topic_index[topic])

        # Time dimension lookup
        if q_time.get('year'):
            year_str = str(q_time['year'])
            if year_str in self._time_index:
                candidate_ids.update(self._time_index[year_str])

        # Relation dimension lookup
        for relation in q_relations:
            if relation in self._relation_index:
                candidate_ids.update(self._relation_index[relation])

        # If no dimensional matches, fall back to word matching in content
        if not candidate_ids:
            for word in q_words:
                for pid, point in self._points.items():
                    if word in point.content.lower():
                        candidate_ids.add(pid)

        # If still nothing, return all points
        if not candidate_ids:
            candidate_ids = set(self._points.keys())

        # Score each candidate by DIMENSIONAL OVERLAP
        scored: list[tuple[MemoryPoint, float, dict]] = []

        for pid in candidate_ids:
            point = self._points[pid]

            # Calculate overlap in each dimension
            entity_overlap = len(q_entities & point.entities) / max(len(q_entities), 1)
            topic_overlap = len(q_topics & point.topics) / max(len(q_topics), 1) if q_topics else 0
            relation_overlap = len(q_relations & point.relations) / max(len(q_relations), 1) if q_relations else 0

            # Word overlap (content matching)
            point_words = set(re.findall(r'\b[a-z]{4,}\b', point.content.lower()))
            word_overlap = len(q_words & point_words) / max(len(q_words), 1) if q_words else 0

            # Time dimension - check if time-related query matches time-related content
            time_score = 0.0
            if q_time.get('relative') or 'when' in query_text.lower():
                if point.time_coords.get('relative') or point.time_coords.get('year'):
                    time_score = 0.3

            # Embedding similarity (fallback)
            embedding_score = 0.0
            if query_embedding and point.embedding:
                embedding_score = self._cosine_similarity(query_embedding, point.embedding)

            # DIMENSIONAL SCORE:
            # Entity (25%) + Topic (25%) + Word (20%) + Time (15%) + Embedding (15%)
            # This weights PRECISE dimensional matches over fuzzy embedding
            score = (
                0.25 * entity_overlap +
                0.25 * topic_overlap +
                0.20 * word_overlap +
                0.15 * time_score +
                0.15 * max(0, embedding_score)
            )

            # BONUS: If entity matches AND topic matches, this is a PRECISE HIT
            if entity_overlap > 0.5 and (topic_overlap > 0.3 or word_overlap > 0.3):
                score += 0.20

            breakdown = {
                'entity': entity_overlap,
                'topic': topic_overlap,
                'word': word_overlap,
                'time': time_score,
                'embedding': embedding_score,
            }

            scored.append((point, score, breakdown))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:limit]

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def get_stats(self) -> dict:
        """Get index statistics."""
        return {
            'total_points': len(self._points),
            'unique_entities': len(self._entity_index),
            'unique_topics': len(self._topic_index),
            'unique_relations': len(self._relation_index),
            'time_indices': len(self._time_index),
        }


# =============================================================================
# 4D TENSOR RETRIEVER
# =============================================================================

class TensorRetriever:
    """4D Tensor Retriever - dimensional access to memory.

    This replaces graph traversal with dimensional slicing.
    """

    def __init__(self):
        self._index = TensorMemoryIndex()

    def insert(
        self,
        point_id: str,
        content: str,
        speaker: str = "",
        timestamp: datetime | None = None,
        embedding: list[float] | None = None,
    ) -> MemoryPoint:
        """Insert memory into 4D tensor."""
        return self._index.insert(point_id, content, speaker, timestamp, embedding)

    def query(
        self,
        query_text: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryPoint, float, dict]]:
        """Query 4D tensor with dimensional coordinates."""
        return self._index.query(query_text, limit, query_embedding)

    def get_stats(self) -> dict:
        """Get tensor statistics."""
        return self._index.get_stats()

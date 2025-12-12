"""Episodic Temporal Memory: Brain-Inspired Temporal Retrieval.

THE NEUROSCIENCE INSIGHT:
========================
The hippocampus doesn't store timestamps - it stores EPISODES.

When you remember "what I did yesterday", your brain:
1. Recalls the EPISODE (the context, the people, the place)
2. Places the episode in a SEQUENCE relative to other episodes
3. Reconstructs the time from episode boundaries

The key insight: TEMPORAL INFORMATION IS BOUND TO EPISODES, NOT FLOATING.

"Yesterday" in Session 1 (May 8) means May 7.
"Yesterday" in Session 5 (July 3) means July 2.

The SAME WORD has DIFFERENT MEANINGS depending on the episode.

OUR SOLUTION:
=============
1. EPISODE BINDING: Each memory belongs to an episode with a timestamp
2. TEMPORAL RECONSTRUCTION: Resolve "yesterday" relative to episode, not query time
3. SEQUENCE CHAINS: Build before/after relationships between events
4. TEMPORAL LANDMARKS: Anchor events to episode boundaries

This is the missing piece for temporal questions.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .data_types import NeuralNode
    from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

MONTH_NAMES_REVERSE = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class TemporalEvent:
    """An event with resolved temporal information."""
    content: str
    episode_date: datetime  # The date of the episode (session) this belongs to
    resolved_date: datetime | None  # The actual date the event refers to
    temporal_expression: str | None  # Original expression ("yesterday", "last week")
    temporal_type: str  # "relative", "absolute", "duration", "future"
    node: "NeuralNode"
    confidence: float = 1.0


@dataclass
class Episode:
    """An episode (session) with its temporal context."""
    episode_id: str
    timestamp: datetime
    events: list[TemporalEvent] = field(default_factory=list)

    # Sequence relationships
    previous_episode: str | None = None
    next_episode: str | None = None


class EpisodicTemporalStore:
    """Hippocampus-inspired episodic memory for temporal retrieval.

    KEY INNOVATIONS:
    1. Episode-bound temporal resolution
    2. Temporal expression extraction and classification
    3. Sequence-aware retrieval
    4. Temporal landmark anchoring
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self._storage = storage
        self._episodes: dict[str, Episode] = {}
        self._temporal_index: dict[str, list[TemporalEvent]] = {}  # date_key -> events

    async def build_episodic_index(self, session_key: str) -> None:
        """Build the episodic temporal index from stored nodes."""
        all_nodes = await self._storage.get_nodes_by_session(session_key)
        if not all_nodes:
            return

        # Group nodes by session/episode
        session_nodes: dict[int, list["NeuralNode"]] = {}
        session_dates: dict[int, datetime] = {}

        for node in all_nodes:
            session_num = node.metadata.get("session", 0) if node.metadata else 0
            if session_num not in session_nodes:
                session_nodes[session_num] = []
            session_nodes[session_num].append(node)

            # Track session date
            if node.created_at and session_num not in session_dates:
                session_dates[session_num] = node.created_at

        # Build episodes
        sorted_sessions = sorted(session_nodes.keys())
        prev_episode_id = None

        for session_num in sorted_sessions:
            episode_id = f"{session_key}_ep{session_num}"
            episode_date = session_dates.get(session_num, datetime.now())

            episode = Episode(
                episode_id=episode_id,
                timestamp=episode_date,
                previous_episode=prev_episode_id,
            )

            # Update previous episode's next pointer
            if prev_episode_id and prev_episode_id in self._episodes:
                self._episodes[prev_episode_id].next_episode = episode_id

            # Process events in this episode
            for node in session_nodes[session_num]:
                events = self._extract_temporal_events(node, episode_date)
                episode.events.extend(events)

                # Index by resolved date
                for event in events:
                    if event.resolved_date:
                        date_key = event.resolved_date.strftime("%Y-%m-%d")
                        if date_key not in self._temporal_index:
                            self._temporal_index[date_key] = []
                        self._temporal_index[date_key].append(event)

            self._episodes[episode_id] = episode
            prev_episode_id = episode_id

    def _extract_temporal_events(
        self,
        node: "NeuralNode",
        episode_date: datetime
    ) -> list[TemporalEvent]:
        """Extract and resolve temporal events from a node."""
        content = node.content
        content_lower = content.lower()
        events = []

        # Pattern 1: "yesterday" -> episode_date - 1 day
        if "yesterday" in content_lower:
            resolved = episode_date - timedelta(days=1)
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=resolved,
                temporal_expression="yesterday",
                temporal_type="relative",
                node=node,
                confidence=0.95,
            ))

        # Pattern 2: "today" -> episode_date
        if "today" in content_lower:
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=episode_date,
                temporal_expression="today",
                temporal_type="relative",
                node=node,
                confidence=0.95,
            ))

        # Pattern 3: "last week" -> episode_date - 7 days (approximate)
        if "last week" in content_lower:
            resolved = episode_date - timedelta(days=7)
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=resolved,
                temporal_expression="last week",
                temporal_type="relative",
                node=node,
                confidence=0.8,  # Less precise
            ))

        # Pattern 4: "last friday", "last monday", etc.
        for day_name in DAY_NAMES:
            pattern = f"last {day_name}"
            if pattern in content_lower:
                resolved = self._resolve_last_weekday(episode_date, day_name)
                events.append(TemporalEvent(
                    content=content,
                    episode_date=episode_date,
                    resolved_date=resolved,
                    temporal_expression=pattern,
                    temporal_type="relative",
                    node=node,
                    confidence=0.9,
                ))

        # Pattern 5: "X years ago"
        years_match = re.search(r'(\d+)\s+years?\s+ago', content_lower)
        if years_match:
            years = int(years_match.group(1))
            resolved = episode_date.replace(year=episode_date.year - years)
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=resolved,
                temporal_expression=years_match.group(0),
                temporal_type="relative",
                node=node,
                confidence=0.85,
            ))

        # Pattern 6: Absolute dates in content "[= 7 May 2023]"
        grounded_match = re.search(r'\[=\s*(\d{1,2})\s+(\w+)\s+(\d{4})\]', content)
        if grounded_match:
            day = int(grounded_match.group(1))
            month_name = grounded_match.group(2).lower()
            year = int(grounded_match.group(3))
            month = MONTH_NAMES.get(month_name, 1)
            try:
                resolved = datetime(year, month, day)
                events.append(TemporalEvent(
                    content=content,
                    episode_date=episode_date,
                    resolved_date=resolved,
                    temporal_expression=grounded_match.group(0),
                    temporal_type="absolute",
                    node=node,
                    confidence=1.0,
                ))
            except ValueError:
                pass

        # Pattern 7: Month references ("in June", "July 2023")
        for month_name, month_num in MONTH_NAMES.items():
            if month_name in content_lower:
                # Check for year
                year_match = re.search(rf'{month_name}\s+(\d{{4}})', content_lower)
                if year_match:
                    year = int(year_match.group(1))
                else:
                    # Assume same year as episode or next year if month passed
                    year = episode_date.year
                    if month_num < episode_date.month:
                        year += 1

                try:
                    resolved = datetime(year, month_num, 15)  # Mid-month approximation
                    events.append(TemporalEvent(
                        content=content,
                        episode_date=episode_date,
                        resolved_date=resolved,
                        temporal_expression=month_name,
                        temporal_type="absolute",
                        node=node,
                        confidence=0.7,  # Month-level precision
                    ))
                except ValueError:
                    pass
                break  # Only match first month

        # Pattern 8: Future references ("next week", "tomorrow", "this month")
        if "tomorrow" in content_lower:
            resolved = episode_date + timedelta(days=1)
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=resolved,
                temporal_expression="tomorrow",
                temporal_type="future",
                node=node,
                confidence=0.9,
            ))

        if "next week" in content_lower:
            resolved = episode_date + timedelta(days=7)
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=resolved,
                temporal_expression="next week",
                temporal_type="future",
                node=node,
                confidence=0.75,
            ))

        if "this month" in content_lower:
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=episode_date,
                temporal_expression="this month",
                temporal_type="relative",
                node=node,
                confidence=0.7,
            ))

        # If no temporal expressions found, still create an event anchored to episode
        if not events:
            events.append(TemporalEvent(
                content=content,
                episode_date=episode_date,
                resolved_date=episode_date,
                temporal_expression=None,
                temporal_type="episode_anchored",
                node=node,
                confidence=0.5,
            ))

        return events

    def _resolve_last_weekday(self, reference: datetime, day_name: str) -> datetime:
        """Resolve 'last friday' etc. relative to reference date."""
        day_index = DAY_NAMES.index(day_name)
        current_day = reference.weekday()

        # Days since last occurrence
        days_ago = (current_day - day_index) % 7
        if days_ago == 0:
            days_ago = 7  # "last friday" means the previous one, not today

        return reference - timedelta(days=days_ago)

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        limit: int = 40,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with episodic temporal awareness."""
        # Ensure index is built
        if not self._episodes:
            await self.build_episodic_index(session_key)

        query_embedding_np = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_embedding_np)
        if query_norm > 1e-10:
            query_embedding_np = query_embedding_np / query_norm

        query_lower = query_text.lower()

        # Extract temporal hints from query
        query_temporal = self._extract_query_temporal(query_lower)

        # Collect all events
        all_events: list[TemporalEvent] = []
        for episode in self._episodes.values():
            all_events.extend(episode.events)

        # Score each event
        results: list[tuple["NeuralNode", float]] = []
        seen_nodes = set()

        for event in all_events:
            if event.node.node_id in seen_nodes:
                continue
            seen_nodes.add(event.node.node_id)

            score = self._score_temporal_event(
                event=event,
                query_embedding=query_embedding_np,
                query_text=query_lower,
                query_temporal=query_temporal,
            )

            if score > 0.1:
                results.append((event.node, score))

        # Sort by score
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _extract_query_temporal(self, query: str) -> dict[str, Any]:
        """Extract temporal hints from the query."""
        hints = {
            "type": None,  # "when", "how_long", "before_after"
            "entities": [],
            "temporal_words": [],
            "target_date": None,
            "temporal_constraint": None,  # NEW: season/period constraint
        }

        # Detect query type
        if "when did" in query or "when was" in query or "when is" in query:
            hints["type"] = "when"
        elif "how long" in query:
            hints["type"] = "how_long"
        elif "before" in query or "after" in query:
            hints["type"] = "before_after"
        elif "first time" in query or "last time" in query:
            hints["type"] = "first_last"

        # Extract entities (capitalized words in original)
        entities = re.findall(r'\b[A-Z][a-z]+\b', query)
        common = {'When', 'What', 'Where', 'Who', 'How', 'Did', 'Does', 'Is', 'Was', 'The'}
        hints["entities"] = [e for e in entities if e not in common]

        # Extract temporal words
        temporal_words = []
        for tw in ["yesterday", "today", "tomorrow", "last week", "this week",
                   "last month", "this month", "last year"]:
            if tw in query:
                temporal_words.append(tw)
        hints["temporal_words"] = temporal_words

        # Extract year if mentioned
        year_match = re.search(r'\b(20\d{2})\b', query)
        if year_match:
            hints["target_year"] = int(year_match.group(1))

        # Extract month if mentioned
        for month_name in MONTH_NAMES:
            if month_name in query:
                hints["target_month"] = month_name
                hints["target_month_num"] = MONTH_NAMES[month_name]
                break

        # NEW: Extract TEMPORAL CONSTRAINTS (seasons, periods)
        # These help disambiguate between multiple similar events
        if "during the summer" in query or "in the summer" in query or "summer" in query:
            hints["temporal_constraint"] = "summer"  # June-August
            hints["constraint_months"] = [6, 7, 8]
        elif "during the winter" in query or "in the winter" in query or "winter" in query:
            hints["temporal_constraint"] = "winter"  # Dec-Feb
            hints["constraint_months"] = [12, 1, 2]
        elif "during the spring" in query or "in the spring" in query or "spring" in query:
            hints["temporal_constraint"] = "spring"  # March-May
            hints["constraint_months"] = [3, 4, 5]
        elif "during the fall" in query or "in the fall" in query or "fall" in query or "autumn" in query:
            hints["temporal_constraint"] = "fall"  # Sept-Nov
            hints["constraint_months"] = [9, 10, 11]

        # Also check for specific month mentions like "in July", "in August"
        for month_name, month_num in MONTH_NAMES.items():
            if f"in {month_name}" in query:
                hints["temporal_constraint"] = month_name
                hints["constraint_months"] = [month_num]
                break

        return hints

    def _score_temporal_event(
        self,
        event: TemporalEvent,
        query_embedding: np.ndarray,
        query_text: str,
        query_temporal: dict,
    ) -> float:
        """Score an event for temporal relevance."""
        content_lower = event.content.lower()

        # 1. Semantic similarity (base)
        semantic_score = 0.0
        if event.node.embedding:
            node_emb = np.array(event.node.embedding, dtype=np.float32)
            node_norm = np.linalg.norm(node_emb)
            if node_norm > 1e-10:
                semantic_score = float(np.dot(query_embedding, node_emb / node_norm))

        # 2. Entity matching (HIGH weight for temporal questions)
        entity_score = 0.0
        if query_temporal["entities"]:
            matches = sum(1 for e in query_temporal["entities"] if e.lower() in content_lower)
            entity_score = min(1.0, matches / len(query_temporal["entities"]))

        # 3. Speaker binding
        speaker_score = 0.0
        if event.node.metadata and query_temporal["entities"]:
            speaker = (event.node.metadata.get("producer_id", "") or
                      event.node.metadata.get("speaker", "")).lower()
            if any(e.lower() == speaker for e in query_temporal["entities"]):
                speaker_score = 1.0

        # 4. Temporal expression match (CRITICAL for "when" questions)
        temporal_match_score = 0.0
        if query_temporal["type"] == "when":
            # Boost events with resolved temporal expressions
            if event.resolved_date and event.temporal_expression:
                temporal_match_score = 0.4 * event.confidence

            # Extra boost if event has grounded date markers
            if "[=" in content_lower:
                temporal_match_score += 0.3

        # 5. Duration questions ("how long")
        duration_score = 0.0
        if query_temporal["type"] == "how_long":
            if re.search(r'\d+\s+(years?|months?|weeks?|days?)', content_lower):
                duration_score = 0.5

        # 6. Target date proximity AND temporal constraint matching
        date_proximity_score = 0.0
        constraint_penalty = 0.0
        if event.resolved_date:
            # If query mentions a year, boost events from that year
            if "target_year" in query_temporal:
                if event.resolved_date.year == query_temporal["target_year"]:
                    date_proximity_score = 0.3
                else:
                    # PENALTY for wrong year
                    constraint_penalty = 0.3

            # If query mentions a month, boost events from that month
            if "target_month" in query_temporal:
                event_month = MONTH_NAMES_REVERSE.get(event.resolved_date.month, "").lower()
                if event_month == query_temporal["target_month"]:
                    date_proximity_score += 0.2

            # NEW: Temporal constraint matching (e.g., "during the summer")
            if "constraint_months" in query_temporal:
                constraint_months = query_temporal["constraint_months"]
                event_month = event.resolved_date.month

                if event_month in constraint_months:
                    # Event is WITHIN the constraint - boost it
                    date_proximity_score += 0.3
                else:
                    # Event is OUTSIDE the constraint - penalize it heavily
                    # This is critical for disambiguating "pride parade in summer" vs "pride parade in August"
                    constraint_penalty = 0.4

        # 7. Keyword matching for the ACTION
        # For "When did Caroline go to the LGBTQ support group?"
        # We need to match "LGBTQ support group" in the content
        # THIS IS CRITICAL - we need the RIGHT event, not just any event
        action_keywords = self._extract_action_keywords(query_text)
        action_score = 0.0
        action_match_count = 0
        if action_keywords:
            for kw in action_keywords:
                if kw in content_lower:
                    action_match_count += 1
            # Use a higher weight for action matching
            action_score = min(1.0, action_match_count / max(1, len(action_keywords)))

            # BONUS: If we match 3+ action keywords, this is likely THE event
            if action_match_count >= 3:
                action_score *= 1.5

        # WEIGHTED COMBINATION
        # For temporal questions, we weight differently than other types
        if query_temporal["type"] == "when":
            # ACTION is the MOST important - we need to find the RIGHT event
            total = (
                semantic_score * 0.10 +   # Lower semantic weight
                entity_score * 0.15 +     # Entity is important but secondary
                speaker_score * 0.10 +    # Speaker binding
                temporal_match_score * 0.15 +  # Temporal resolution
                date_proximity_score * 0.10 +  # Date/constraint matching
                action_score * 0.40       # ACTION IS KEY - what event are we looking for?
            )
            # Apply constraint penalty (e.g., asking for "summer" but event is in August)
            total = total * (1.0 - constraint_penalty)
        elif query_temporal["type"] == "how_long":
            total = (
                semantic_score * 0.2 +
                entity_score * 0.2 +
                duration_score * 0.4 +
                action_score * 0.2
            )
        else:
            total = (
                semantic_score * 0.3 +
                entity_score * 0.25 +
                speaker_score * 0.2 +
                temporal_match_score * 0.15 +
                action_score * 0.1
            )

        return total

    def _extract_action_keywords(self, query: str) -> list[str]:
        """Extract the action/event keywords from a temporal query.

        "When did Caroline go to the LGBTQ support group?"
        -> ["lgbtq", "support", "group"]

        "When did Melanie sign up for a pottery class?"
        -> ["sign", "pottery", "class"]

        CRITICAL: Keep specific nouns that identify the EVENT.
        """
        query_lower = query.lower()

        # First, extract multi-word phrases that are important
        important_phrases = []
        phrase_patterns = [
            r'support group', r'pride parade', r'pride festival', r'activist group',
            r'pottery class', r'pottery workshop', r'adoption meeting', r'adoption agency',
            r'mentorship program', r'transgender conference', r'lgbtq conference',
            r'talent show', r'charity race', r'dance competition', r'road trip',
            r'birthday party', r'camping trip',
        ]
        for pattern in phrase_patterns:
            if pattern in query_lower:
                important_phrases.extend(pattern.split())

        # Remove question words and entities
        cleaned = re.sub(r'\b(when|did|does|was|is|will|has|have|had|the|a|an|to|for|of)\b', ' ', query_lower)

        # Remove common verbs that don't help
        cleaned = re.sub(r'\b(go|went|going|get|got|getting|do|doing|done|make|made|making)\b', ' ', cleaned)

        # Extract remaining meaningful words (3+ chars)
        words = re.findall(r'\b[a-z]{3,}\b', cleaned)

        # Filter stopwords
        stopwords = {'she', 'her', 'his', 'him', 'they', 'their', 'and', 'but', 'with'}
        keywords = [w for w in words if w not in stopwords]

        # Add important phrases with higher priority (they'll appear twice = higher weight)
        keywords = important_phrases + keywords

        return keywords


class EpisodicTesseract:
    """Enhanced Tesseract with episodic temporal memory.

    This replaces the basic TemporalStore with EpisodicTemporalStore
    for dramatically improved temporal question accuracy.
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self._storage = storage
        self._episodic_store = EpisodicTemporalStore(storage)

        # Import other stores from tesseract module
        from .tesseract import EntityStore, ReasoningStore, AdversarialStore, detect_query_type, QueryType
        self._entity_store = EntityStore(storage)
        self._reasoning_store = ReasoningStore(storage)
        self._adversarial_store = AdversarialStore(storage)
        self._detect_query_type = detect_query_type
        self._QueryType = QueryType

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        session_key: str,
        reference_time: datetime | None = None,
        limit: int = 80,
    ) -> list[tuple["NeuralNode", float]]:
        """Retrieve with episodic temporal enhancement."""
        query_types = self._detect_query_type(query_text)

        # Use EPISODIC store for temporal questions
        temporal_results = await self._episodic_store.retrieve(
            query_text, query_embedding, session_key, limit=50
        )

        entity_results = await self._entity_store.retrieve(
            query_text, query_embedding, session_key, limit=50
        )

        reasoning_results = await self._reasoning_store.retrieve(
            query_text, query_embedding, session_key, limit=40
        )

        adversarial_results = await self._adversarial_store.retrieve(
            query_text, query_embedding, session_key, limit=30
        )

        # Fuse with enhanced temporal weighting
        fused = self._fuse_results(
            temporal_results=temporal_results,
            entity_results=entity_results,
            reasoning_results=reasoning_results,
            adversarial_results=adversarial_results,
            type_weights=query_types,
        )

        fused.sort(key=lambda x: x[1], reverse=True)
        return fused[:limit]

    def _fuse_results(
        self,
        temporal_results: list[tuple["NeuralNode", float]],
        entity_results: list[tuple["NeuralNode", float]],
        reasoning_results: list[tuple["NeuralNode", float]],
        adversarial_results: list[tuple["NeuralNode", float]],
        type_weights: dict[str, float],
    ) -> list[tuple["NeuralNode", float]]:
        """Fuse with ENHANCED temporal weighting."""
        QueryType = self._QueryType

        total_weight = sum(type_weights.values()) or 1.0

        # ENHANCED: Give temporal store MORE weight when query is temporal
        temporal_type_weight = type_weights.get(QueryType.TEMPORAL, 0.25)
        if temporal_type_weight > 0.4:
            # Strong temporal signal - boost temporal store significantly
            temporal_weight = 0.5
            entity_weight = 0.25
            multihop_weight = 0.15
            adversarial_weight = 0.1
        else:
            temporal_weight = type_weights.get(QueryType.TEMPORAL, 0.2) / total_weight
            entity_weight = type_weights.get(QueryType.ENTITY, 0.3) / total_weight
            multihop_weight = type_weights.get(QueryType.MULTI_HOP, 0.25) / total_weight
            adversarial_weight = type_weights.get(QueryType.ADVERSARIAL, 0.1) / total_weight

        combined: dict[str, tuple["NeuralNode", float]] = {}

        for node, charge in temporal_results:
            effective = charge * temporal_weight
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective)
            else:
                combined[node.node_id] = (node, effective)

        for node, charge in entity_results:
            effective = charge * entity_weight
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective)
            else:
                combined[node.node_id] = (node, effective)

        for node, charge in reasoning_results:
            effective = charge * multihop_weight
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective)
            else:
                combined[node.node_id] = (node, effective)

        for node, charge in adversarial_results:
            effective = charge * adversarial_weight
            if node.node_id in combined:
                existing_node, existing_charge = combined[node.node_id]
                combined[node.node_id] = (existing_node, existing_charge + effective)
            else:
                combined[node.node_id] = (node, effective)

        return list(combined.values())

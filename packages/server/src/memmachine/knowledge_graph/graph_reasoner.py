"""Think-on-Graph reasoning for knowledge graph queries."""

import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from .data_types import (
    Entity,
    MeKBScore,
    Relation,
    ReasoningPath,
    ReasoningResult,
)

logger = logging.getLogger(__name__)


class GraphStorage(Protocol):
    """Protocol for graph storage operations."""

    async def get_entity(self, uid: str) -> Entity | None:
        """Get entity by UID."""
        ...

    async def get_entities_by_name(
        self, name: str, session_key: str
    ) -> list[Entity]:
        """Get entities by name."""
        ...

    async def get_neighbors(
        self, entity_uid: str, max_hops: int = 1
    ) -> list[tuple[Entity, Relation]]:
        """Get neighboring entities and their relations."""
        ...

    async def search_entities(
        self, query: str, session_key: str, limit: int = 10
    ) -> list[Entity]:
        """Search entities by text similarity."""
        ...


@dataclass
class BeamState:
    """State for beam search in graph reasoning."""

    path: list[Entity]
    relations: list[Relation]
    score: float = 0.0
    visited: set[str] = field(default_factory=set)

    @property
    def current_entity(self) -> Entity | None:
        """Get the current (last) entity in the path."""
        return self.path[-1] if self.path else None

    def extend(
        self, entity: Entity, relation: Relation, score_delta: float
    ) -> "BeamState":
        """Create new state by extending with an entity."""
        new_visited = self.visited.copy()
        new_visited.add(entity.uid)
        return BeamState(
            path=self.path + [entity],
            relations=self.relations + [relation],
            score=self.score + score_delta,
            visited=new_visited,
        )


class MeKBScorer:
    """MeKB TF-IDF scoring for entities.

    MeKBScore(u, e) = TF(u, e) × IDF(e)
    """

    def __init__(self):
        """Initialize scorer."""
        self._user_mention_counts: dict[str, dict[str, int]] = {}  # user -> entity -> count
        self._user_total_mentions: dict[str, int] = {}  # user -> total
        self._entity_user_counts: dict[str, int] = {}  # entity -> num users
        self._total_users: int = 0

    def record_mention(
        self, user_id: str, entity_uid: str, count: int = 1
    ) -> None:
        """Record a mention of an entity by a user."""
        if user_id not in self._user_mention_counts:
            self._user_mention_counts[user_id] = {}
            self._total_users += 1

        if entity_uid not in self._user_mention_counts[user_id]:
            self._user_mention_counts[user_id][entity_uid] = 0
            # Track that this user mentions this entity
            self._entity_user_counts[entity_uid] = (
                self._entity_user_counts.get(entity_uid, 0) + 1
            )

        self._user_mention_counts[user_id][entity_uid] += count
        self._user_total_mentions[user_id] = (
            self._user_total_mentions.get(user_id, 0) + count
        )

    def compute_score(self, user_id: str, entity_uid: str) -> MeKBScore:
        """Compute MeKB score for an entity."""
        user_mentions = (
            self._user_mention_counts.get(user_id, {}).get(entity_uid, 0)
        )
        user_total = self._user_total_mentions.get(user_id, 0)
        users_mentioning = self._entity_user_counts.get(entity_uid, 1)

        return MeKBScore(
            entity_uid=entity_uid,
            user_mentions=user_mentions,
            user_total_mentions=user_total,
            total_users=max(1, self._total_users),
            users_mentioning=users_mentioning,
        )

    def get_top_entities(
        self, user_id: str, limit: int = 10
    ) -> list[tuple[str, float]]:
        """Get top entities by MeKB score for a user."""
        if user_id not in self._user_mention_counts:
            return []

        scores = []
        for entity_uid in self._user_mention_counts[user_id]:
            mekb = self.compute_score(user_id, entity_uid)
            scores.append((entity_uid, mekb.score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:limit]


class ThinkOnGraphReasoner:
    """Think-on-Graph beam search reasoning over knowledge graph.

    Implements multi-hop reasoning to answer complex queries.
    """

    def __init__(
        self,
        storage: GraphStorage | None = None,
        mekb_scorer: MeKBScorer | None = None,
        beam_width: int = 5,
        max_depth: int = 3,
        score_threshold: float = 0.1,
    ):
        """Initialize reasoner.

        Args:
            storage: Graph storage backend.
            mekb_scorer: MeKB scorer for entity importance.
            beam_width: Number of paths to keep at each step.
            max_depth: Maximum reasoning depth (hops).
            score_threshold: Minimum score to continue exploring.
        """
        self._storage = storage
        self._mekb_scorer = mekb_scorer or MeKBScorer()
        self._beam_width = beam_width
        self._max_depth = max_depth
        self._score_threshold = score_threshold

    async def reason(
        self,
        query: str,
        session_key: str,
        seed_entities: list[Entity] | None = None,
    ) -> ReasoningResult:
        """Perform reasoning over the knowledge graph.

        Args:
            query: The query to reason about.
            session_key: Session key for the user.
            seed_entities: Optional seed entities to start from.

        Returns:
            ReasoningResult with paths and relevant entities.
        """
        start_time = time.time()

        if self._storage is None:
            return ReasoningResult(
                query=query,
                paths=[],
                relevant_entities=[],
                confidence=0.0,
                processing_time_ms=(time.time() - start_time) * 1000,
                explanation="No graph storage configured",
            )

        # Find seed entities if not provided
        if seed_entities is None:
            seed_entities = await self._storage.search_entities(
                query, session_key, limit=self._beam_width
            )

        if not seed_entities:
            return ReasoningResult(
                query=query,
                paths=[],
                relevant_entities=[],
                confidence=0.0,
                processing_time_ms=(time.time() - start_time) * 1000,
                explanation="No seed entities found",
            )

        # Initialize beam with seed entities
        beam: list[BeamState] = []
        for entity in seed_entities:
            mekb = self._mekb_scorer.compute_score(session_key, entity.uid)
            state = BeamState(
                path=[entity],
                relations=[],
                score=mekb.score + entity.mekb_score,
                visited={entity.uid},
            )
            beam.append(state)

        # Beam search
        all_paths: list[BeamState] = list(beam)

        for depth in range(self._max_depth):
            next_beam: list[BeamState] = []

            for state in beam:
                current = state.current_entity
                if current is None:
                    continue

                # Get neighbors
                neighbors = await self._storage.get_neighbors(current.uid)

                for neighbor_entity, relation in neighbors:
                    if neighbor_entity.uid in state.visited:
                        continue

                    # Score the extension
                    mekb = self._mekb_scorer.compute_score(
                        session_key, neighbor_entity.uid
                    )
                    score_delta = (
                        mekb.score +
                        neighbor_entity.mekb_score +
                        relation.weight * 0.1
                    )

                    if score_delta < self._score_threshold:
                        continue

                    new_state = state.extend(
                        neighbor_entity, relation, score_delta
                    )
                    next_beam.append(new_state)
                    all_paths.append(new_state)

            # Keep top states
            next_beam.sort(key=lambda s: s.score, reverse=True)
            beam = next_beam[:self._beam_width]

            if not beam:
                break

        # Convert to reasoning paths
        all_paths.sort(key=lambda s: s.score, reverse=True)
        top_paths = all_paths[:self._beam_width]

        paths = []
        relevant_entities: list[Entity] = []
        seen_entity_uids: set[str] = set()

        for state in top_paths:
            path = ReasoningPath(
                path_id=str(uuid.uuid4()),
                entities=state.path,
                relations=state.relations,
                score=state.score,
                explanation=f"Path: {' -> '.join(e.name for e in state.path)}",
            )
            paths.append(path)

            for entity in state.path:
                if entity.uid not in seen_entity_uids:
                    seen_entity_uids.add(entity.uid)
                    relevant_entities.append(entity)

        processing_time = (time.time() - start_time) * 1000

        return ReasoningResult(
            query=query,
            paths=paths,
            relevant_entities=relevant_entities,
            confidence=paths[0].score if paths else 0.0,
            processing_time_ms=processing_time,
            explanation=f"Found {len(paths)} reasoning paths with {len(relevant_entities)} entities",
        )


class InMemoryGraphStorage:
    """In-memory implementation of graph storage."""

    def __init__(self):
        """Initialize storage."""
        self._entities: dict[str, Entity] = {}
        self._relations: dict[str, Relation] = {}
        self._entity_relations: dict[str, list[str]] = {}  # entity_uid -> relation_uids

    async def add_entity(self, entity: Entity) -> None:
        """Add an entity to storage."""
        self._entities[entity.uid] = entity
        if entity.uid not in self._entity_relations:
            self._entity_relations[entity.uid] = []

    async def add_relation(self, relation: Relation) -> None:
        """Add a relation to storage."""
        self._relations[relation.uid] = relation

        # Index by source
        if relation.source_entity_uid not in self._entity_relations:
            self._entity_relations[relation.source_entity_uid] = []
        self._entity_relations[relation.source_entity_uid].append(relation.uid)

        # Index by target (for bidirectional traversal)
        if relation.target_entity_uid not in self._entity_relations:
            self._entity_relations[relation.target_entity_uid] = []
        self._entity_relations[relation.target_entity_uid].append(relation.uid)

    async def get_entity(self, uid: str) -> Entity | None:
        """Get entity by UID."""
        return self._entities.get(uid)

    async def get_entities_by_name(
        self, name: str, session_key: str
    ) -> list[Entity]:
        """Get entities by name."""
        name_lower = name.lower()
        return [
            e for e in self._entities.values()
            if e.session_key == session_key and (
                e.name.lower() == name_lower or
                name_lower in [a.lower() for a in e.aliases]
            )
        ]

    async def get_neighbors(
        self, entity_uid: str, max_hops: int = 1
    ) -> list[tuple[Entity, Relation]]:
        """Get neighboring entities and their relations."""
        neighbors = []
        relation_uids = self._entity_relations.get(entity_uid, [])

        for rel_uid in relation_uids:
            relation = self._relations.get(rel_uid)
            if relation is None:
                continue

            # Get the other entity
            other_uid = (
                relation.target_entity_uid
                if relation.source_entity_uid == entity_uid
                else relation.source_entity_uid
            )
            other_entity = self._entities.get(other_uid)

            if other_entity:
                neighbors.append((other_entity, relation))

        return neighbors

    async def search_entities(
        self, query: str, session_key: str, limit: int = 10
    ) -> list[Entity]:
        """Search entities by text similarity (simple substring match)."""
        query_lower = query.lower()
        matches = []

        for entity in self._entities.values():
            if entity.session_key != session_key:
                continue

            # Check name and aliases
            if query_lower in entity.name.lower():
                matches.append(entity)
            elif any(query_lower in a.lower() for a in entity.aliases):
                matches.append(entity)
            elif entity.description and query_lower in entity.description.lower():
                matches.append(entity)

        # Sort by MeKB score
        matches.sort(key=lambda e: e.mekb_score, reverse=True)
        return matches[:limit]

    async def get_all_entities(self, session_key: str) -> list[Entity]:
        """Get all entities for a session."""
        return [
            e for e in self._entities.values()
            if e.session_key == session_key
        ]

    async def get_all_relations(self, session_key: str) -> list[Relation]:
        """Get all relations for a session."""
        return [
            r for r in self._relations.values()
            if r.session_key == session_key
        ]

    async def clear_session(self, session_key: str) -> int:
        """Clear all data for a session."""
        entity_uids = [
            e.uid for e in self._entities.values()
            if e.session_key == session_key
        ]
        relation_uids = [
            r.uid for r in self._relations.values()
            if r.session_key == session_key
        ]

        for uid in entity_uids:
            del self._entities[uid]
            if uid in self._entity_relations:
                del self._entity_relations[uid]

        for uid in relation_uids:
            del self._relations[uid]

        return len(entity_uids) + len(relation_uids)

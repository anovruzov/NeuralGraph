"""High-level knowledge graph service coordinating extraction and reasoning."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from memmachine.common.episode_store import Episode

from .data_types import (
    Entity,
    ExtractionResult,
    ReasoningResult,
    ReflectiveInsight,
    Relation,
)
from .entity_verifier import EntityVerifier, VerificationResult
from .entity_extractor import EntityExtractor, HybridEntityExtractor
from .graph_reasoner import (
    InMemoryGraphStorage,
    MeKBScorer,
    ThinkOnGraphReasoner,
)

if TYPE_CHECKING:
    from memmachine.neural_graph import NeuralGraphService

logger = logging.getLogger(__name__)


class KnowledgeGraphService:
    """High-level service for knowledge graph operations.

    Coordinates entity extraction, graph construction, MeKB scoring,
    and Think-on-Graph reasoning.
    """

    def __init__(
        self,
        extractor: EntityExtractor | None = None,
        storage: InMemoryGraphStorage | None = None,
        mekb_scorer: MeKBScorer | None = None,
        reasoner: ThinkOnGraphReasoner | None = None,
        neural_graph_service: "NeuralGraphService | None" = None,
        enabled: bool = True,
    ):
        """Initialize knowledge graph service.

        Args:
            extractor: Entity extractor. Creates hybrid if None.
            storage: Graph storage. Creates in-memory if None.
            mekb_scorer: MeKB scorer. Creates default if None.
            reasoner: Graph reasoner. Creates default if None.
            neural_graph_service: Neural graph service for creating ENTITY/SEMANTIC edges.
            enabled: Whether the service is enabled.
        """
        self._enabled = enabled
        self._extractor = extractor or HybridEntityExtractor()
        self._storage = storage or InMemoryGraphStorage()
        self._mekb_scorer = mekb_scorer or MeKBScorer()
        self._reasoner = reasoner or ThinkOnGraphReasoner(
            storage=self._storage,
            mekb_scorer=self._mekb_scorer,
        )

        # Neural graph integration (Phase 7: neural linking)
        self._neural_graph_service = neural_graph_service

        # Statistics
        self._entities_extracted = 0
        self._relations_extracted = 0
        self._reasoning_queries = 0
        self._neural_edges_created = 0

    @property
    def enabled(self) -> bool:
        """Whether the service is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable the service."""
        self._enabled = value

    @property
    def neural_graph_service(self) -> "NeuralGraphService | None":
        """Get the neural graph service."""
        return self._neural_graph_service

    @neural_graph_service.setter
    def neural_graph_service(self, value: "NeuralGraphService | None") -> None:
        """Set the neural graph service."""
        self._neural_graph_service = value

    @property
    def statistics(self) -> dict[str, int]:
        """Get service statistics."""
        return {
            "entities_extracted": self._entities_extracted,
            "relations_extracted": self._relations_extracted,
            "reasoning_queries": self._reasoning_queries,
            "neural_edges_created": self._neural_edges_created,
        }

    async def process_episode(
        self,
        episode: Episode,
        session_key: str,
    ) -> ExtractionResult:
        """Process an episode to extract entities and relations.

        Args:
            episode: Episode to process.
            session_key: Session key for the user.

        Returns:
            ExtractionResult with extracted entities and relations.
        """
        if not self._enabled:
            return ExtractionResult(
                text=episode.content,
                entities=[],
                relations=[],
                confidence=0.0,
                extractor_type="disabled",
            )

        # Extract entities and relations
        result = await self._extractor.extract(
            text=episode.content,
            session_key=session_key,
            episode_uid=episode.uid,
        )

        # Add to graph storage
        for entity in result.entities:
            # Check for existing entity
            existing = await self._storage.get_entities_by_name(
                entity.name, session_key
            )

            if existing:
                # Merge with existing entity
                existing[0].record_mention(episode.uid)
                await self._storage.add_entity(existing[0])
            else:
                # Add new entity
                await self._storage.add_entity(entity)
                self._entities_extracted += 1

            # Update MeKB scorer
            self._mekb_scorer.record_mention(session_key, entity.uid)

        # Add relations
        for relation in result.relations:
            await self._storage.add_relation(relation)
            self._relations_extracted += 1

        # Neural graph integration: Create ENTITY and SEMANTIC edges
        if self._neural_graph_service:
            try:
                edges_created = await self._neural_graph_service.process_extraction_result(
                    extraction=result,
                    session_key=session_key,
                    source_node_id=None,  # Will be linked when episode is processed
                )
                self._neural_edges_created += edges_created
                logger.debug(f"Created {edges_created} neural edges from extraction")
            except Exception as e:
                logger.warning(f"Neural graph edge creation failed: {e}")

        logger.debug(
            f"Processed episode {episode.uid}: "
            f"{len(result.entities)} entities, {len(result.relations)} relations"
        )

        return result

    async def process_episodes(
        self,
        episodes: list[Episode],
        session_key: str,
    ) -> list[ExtractionResult]:
        """Process multiple episodes.

        Args:
            episodes: Episodes to process.
            session_key: Session key.

        Returns:
            List of extraction results.
        """
        results = []
        for episode in episodes:
            result = await self.process_episode(episode, session_key)
            results.append(result)
        return results

    async def reason(
        self,
        query: str,
        session_key: str,
    ) -> ReasoningResult:
        """Perform reasoning over the knowledge graph.

        Args:
            query: Query to reason about.
            session_key: Session key.

        Returns:
            ReasoningResult with paths and entities.
        """
        if not self._enabled:
            return ReasoningResult(
                query=query,
                paths=[],
                relevant_entities=[],
                confidence=0.0,
                explanation="Knowledge graph disabled",
            )

        self._reasoning_queries += 1
        return await self._reasoner.reason(query, session_key)

    async def verify_query(
        self,
        query: str,
        session_key: str,
    ) -> VerificationResult:
        """Verify a query for adversarial entity swaps.

        Checks if entities in the query match stored knowledge graph facts.
        Detects questions like "What is X's necklace?" when Y owns the necklace.

        Args:
            query: Query to verify.
            session_key: Session key.

        Returns:
            VerificationResult with any detected entity swaps.
        """
        if not self._enabled:
            return VerificationResult(
                query=query,
                explanation="Knowledge graph disabled",
            )

        # Get all entities and relations for this session
        entities = await self.get_all_entities(session_key)
        relations = await self.get_all_relations(session_key)

        # Create verifier with current graph state
        verifier = EntityVerifier(entities=entities, relations=relations)

        # Verify the query
        return verifier.verify_query(query)

    async def get_entity_by_name(
        self,
        name: str,
        session_key: str,
    ) -> Entity | None:
        """Get an entity by name.

        Args:
            name: Entity name.
            session_key: Session key.

        Returns:
            Entity or None.
        """
        entities = await self._storage.get_entities_by_name(name, session_key)
        return entities[0] if entities else None

    async def get_all_entities(
        self,
        session_key: str,
    ) -> list[Entity]:
        """Get all entities for a session.

        Args:
            session_key: Session key.

        Returns:
            List of entities.
        """
        return await self._storage.get_all_entities(session_key)

    async def get_all_relations(
        self,
        session_key: str,
    ) -> list[Relation]:
        """Get all relations for a session.

        Args:
            session_key: Session key.

        Returns:
            List of relations.
        """
        return await self._storage.get_all_relations(session_key)

    async def get_entity_neighbors(
        self,
        entity_uid: str,
    ) -> list[tuple[Entity, Relation]]:
        """Get neighbors of an entity.

        Args:
            entity_uid: Entity UID.

        Returns:
            List of (entity, relation) tuples.
        """
        return await self._storage.get_neighbors(entity_uid)

    async def get_top_entities(
        self,
        session_key: str,
        limit: int = 10,
    ) -> list[tuple[Entity, float]]:
        """Get top entities by MeKB score.

        Args:
            session_key: Session key.
            limit: Maximum entities to return.

        Returns:
            List of (entity, score) tuples.
        """
        top_scores = self._mekb_scorer.get_top_entities(session_key, limit)
        result = []

        for entity_uid, score in top_scores:
            entity = await self._storage.get_entity(entity_uid)
            if entity:
                result.append((entity, score))

        return result

    async def search_entities(
        self,
        query: str,
        session_key: str,
        limit: int = 10,
    ) -> list[Entity]:
        """Search for entities matching a query.

        Args:
            query: Search query.
            session_key: Session key.
            limit: Maximum results.

        Returns:
            List of matching entities.
        """
        return await self._storage.search_entities(query, session_key, limit)

    async def generate_insights(
        self,
        session_key: str,
    ) -> list[ReflectiveInsight]:
        """Generate reflective insights from the knowledge graph.

        Args:
            session_key: Session key.

        Returns:
            List of insights.
        """
        # This would use LLM to generate high-level insights
        # For now, generate simple insights from patterns

        insights = []
        entities = await self.get_all_entities(session_key)

        if not entities:
            return insights

        # Find most mentioned entities
        top_entities = await self.get_top_entities(session_key, 5)
        if top_entities:
            names = [e.name for e, _ in top_entities[:3]]
            insight = ReflectiveInsight(
                uid=f"insight_{session_key}_frequent",
                session_key=session_key,
                insight_type="pattern",
                content=f"Frequently mentioned: {', '.join(names)}",
                confidence=0.8,
                supporting_entities=[e.uid for e, _ in top_entities[:3]],
            )
            insights.append(insight)

        # Find preferences (entities with likes/dislikes relations)
        # This would be expanded with actual preference detection

        return insights

    async def clear_session(self, session_key: str) -> int:
        """Clear all knowledge graph data for a session.

        Args:
            session_key: Session key.

        Returns:
            Number of items cleared.
        """
        return await self._storage.clear_session(session_key)

    def get_graph_summary(self, session_key: str) -> dict[str, Any]:
        """Get summary of knowledge graph state.

        Args:
            session_key: Session key.

        Returns:
            Summary dictionary.
        """
        return {
            "enabled": self._enabled,
            "statistics": self.statistics,
        }

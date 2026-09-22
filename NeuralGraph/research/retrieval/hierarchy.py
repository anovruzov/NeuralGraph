"""Hierarchical node management for Neural Memory Graph.

Implements the 4-layer hierarchy (Image 2: Deep Neural Network):
- Layer 0: Messages (raw content)
- Layer 1: Episodes (session segments)
- Layer 2: Topics (semantic clusters)
- Layer 3: Persona (stable user model)

Each layer transforms and compresses information from the layer below,
similar to hidden layers in deep networks extracting increasingly
abstract features.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .data_types import (
    ConsolidationState,
    EdgeType,
    NeuralEdge,
    NeuralNode,
    NodeLayer,
    cosine_similarity,
    generate_edge_id,
    generate_node_id,
)

if TYPE_CHECKING:
    from .storage import NeuralGraphStorage

logger = logging.getLogger(__name__)


@dataclass
class HierarchyConfig:
    """Configuration for hierarchy management.

    Episode consolidation tuned based on neuroscience research:
    - Biological memory consolidation needs ~6-8 hours (sleep cycles)
    - 2-hour gaps create too many micro-episodes
    - Larger episodes provide better semantic coherence
    """

    # Episode consolidation (TUNED based on consolidation research)
    min_messages_per_episode: int = 5   # Was 3 - too small, creates micro-episodes
    max_messages_per_episode: int = 50  # Was 20 - too small for long conversations
    episode_time_gap_hours: float = 6.0  # Was 2.0 - biologically ~6-8 hrs needed

    # Topic clustering
    min_episodes_per_topic: int = 2
    topic_similarity_threshold: float = 0.6

    # Persona modeling
    min_topics_per_persona: int = 3
    persona_stability_threshold: float = 0.8


class HierarchyManager:
    """Manages the hierarchical node structure (Image 2: Deep Network).

    Provides operations for:
    - Creating nodes at each layer
    - Consolidating lower layers into higher layers
    - Traversing the hierarchy
    - Maintaining parent-child relationships

    The hierarchy enables:
    - Progressive abstraction (messages → episodes → topics → persona)
    - Efficient retrieval at different granularity levels
    - Memory consolidation (detailed → compressed)
    """

    def __init__(
        self,
        storage: NeuralGraphStorage,
        config: HierarchyConfig | None = None
    ):
        """Initialize hierarchy manager.

        Args:
            storage: Neural graph storage backend
            config: Hierarchy configuration
        """
        self._storage = storage
        self._config = config or HierarchyConfig()

    # =========================================================================
    # LAYER 0: MESSAGE NODES
    # =========================================================================

    async def create_message_node(
        self,
        content: str,
        session_key: str,
        embedding: list[float] | None = None,
        wave_amplitudes: dict[str, float] | None = None,
        entity_ids: list[str] | None = None,
        importance_score: float = 0.5,
        resolved_date: str | None = None,
        temporal_tokens: list[str] | None = None,
        temporal_metadata: dict[str, Any] | None = None,
        resolved_dates: dict[str, Any] | None = None,
        explicit_date: str | None = None,
        duration_years: int | None = None,
        duration_months: int | None = None,
        since_year: int | None = None,
        since_date: str | None = None,
        **metadata
    ) -> NeuralNode:
        """Create a Layer 0 message node.

        Message nodes are the atomic units of memory.
        They contain raw content and are the highest granularity.

        Args:
            content: Message text content
            session_key: Session identifier
            embedding: Semantic embedding vector
            wave_amplitudes: 9-dimensional wave signals
            entity_ids: Referenced entity IDs
            importance_score: Base importance [0, 1]
            resolved_date: Canonical ISO date (YYYY-MM-DD) for the message
            temporal_tokens: Precomputed temporal tokens for indexing/filtering
            temporal_metadata: Structured temporal components (year, month, etc.)
            resolved_dates: Resolved relative date annotations
            explicit_date: Explicit date extracted from content (YYYY-MM-DD)
            duration_years: Duration in years extracted from content
            duration_months: Duration in months extracted from content
            since_year: Derived start year for duration
            since_date: Derived start date for duration (YYYY-MM-DD)
            **metadata: Additional metadata

        Returns:
            Created message node
        """
        # Extract created_at from metadata if provided
        original_created_at = metadata.pop('created_at', None)

        node = NeuralNode(
            node_id=generate_node_id(),
            layer=NodeLayer.MESSAGE,
            content=content,
            embedding=embedding,
            wave_amplitudes=wave_amplitudes or {},
            entity_ids=entity_ids or [],
            session_key=session_key,
            importance_score=importance_score,
            heat_score=importance_score,  # Initial heat = importance
            metadata={
                **metadata,
                "resolved_date": resolved_date or metadata.get("resolved_date"),
                "temporal_tokens": temporal_tokens or metadata.get("temporal_tokens"),
                "temporal_metadata": temporal_metadata or metadata.get("temporal_metadata"),
                "resolved_dates": resolved_dates or metadata.get("resolved_dates"),
                "explicit_date": explicit_date or metadata.get("explicit_date"),
                "duration_years": duration_years or metadata.get("duration_years"),
                "duration_months": duration_months or metadata.get("duration_months"),
                "since_year": since_year or metadata.get("since_year"),
                "since_date": since_date or metadata.get("since_date"),
            },
            # CRITICAL: Use original conversation timestamp if provided
            created_at=original_created_at if original_created_at else datetime.now(timezone.utc),
        )

        await self._storage.save_node(node)
        logger.debug(f"Created message node {node.node_id[:8]} for session {session_key}")

        return node

    async def create_message_nodes_batch(
        self,
        messages: list[dict[str, Any]],
        session_key: str
    ) -> list[NeuralNode]:
        """Create multiple message nodes.

        Args:
            messages: List of message dictionaries with keys:
                - content: str
                - embedding: list[float] | None
                - wave_amplitudes: dict[str, float] | None
                - entity_ids: list[str] | None
                - importance_score: float
            session_key: Session identifier

        Returns:
            List of created message nodes
        """
        nodes = []
        for msg in messages:
            node = await self.create_message_node(
                content=msg.get("content", ""),
                session_key=session_key,
                embedding=msg.get("embedding"),
                wave_amplitudes=msg.get("wave_amplitudes"),
                entity_ids=msg.get("entity_ids"),
                importance_score=msg.get("importance_score", 0.5),
                **msg.get("metadata", {})
            )
            nodes.append(node)

        return nodes

    # =========================================================================
    # LAYER 1: EPISODE NODES
    # =========================================================================

    async def consolidate_to_episode(
        self,
        message_ids: list[str],
        session_key: str,
        summary: str | None = None,
        **metadata
    ) -> NeuralNode:
        """Consolidate messages into an episode node (Layer 0 → Layer 1).

        Episodes group related messages from a session segment.
        This is the first level of hierarchical abstraction.

        Args:
            message_ids: IDs of messages to consolidate
            session_key: Session identifier
            summary: Optional episode summary (LLM-generated)
            **metadata: Additional metadata

        Returns:
            Created episode node

        Raises:
            ValueError: If no valid messages found
        """
        # Fetch messages
        messages = []
        for mid in message_ids:
            msg = await self._storage.get_node(mid)
            if msg and msg.layer == NodeLayer.MESSAGE:
                messages.append(msg)

        if not messages:
            raise ValueError("No valid message nodes found to consolidate")

        # Aggregate embeddings with importance-weighted pooling
        # OPTIMIZED: Weighted pooling preserves dominant signals better than mean
        messages_with_embeddings = [m for m in messages if m.embedding]
        if messages_with_embeddings:
            embedding_dim = len(messages_with_embeddings[0].embedding)

            # Compute weights based on importance and heat
            total_weight = sum(
                m.importance_score * m.heat_score
                for m in messages_with_embeddings
            )

            if total_weight > 0:
                agg_embedding = []
                for i in range(embedding_dim):
                    weighted_val = sum(
                        m.embedding[i] * (m.importance_score * m.heat_score) / total_weight
                        for m in messages_with_embeddings
                    )
                    agg_embedding.append(weighted_val)
            else:
                # Fallback to mean pooling if all weights are zero
                agg_embedding = [
                    sum(m.embedding[i] for m in messages_with_embeddings) / len(messages_with_embeddings)
                    for i in range(embedding_dim)
                ]
        else:
            agg_embedding = None

        # Aggregate wave amplitudes (max pooling - preserve strongest signals)
        all_amps = [m.wave_amplitudes for m in messages if m.wave_amplitudes]
        if all_amps:
            agg_waves: dict[str, float] = {}
            all_keys = set()
            for a in all_amps:
                all_keys.update(a.keys())
            for key in all_keys:
                agg_waves[key] = max(a.get(key, 0.0) for a in all_amps)
        else:
            agg_waves = {}

        # Collect all entity references
        all_entity_ids = set()
        for m in messages:
            all_entity_ids.update(m.entity_ids)

        # Generate summary if not provided
        if summary is None:
            summary = f"Episode: {len(messages)} messages"

        # Aggregate importance and heat
        avg_importance = sum(m.importance_score for m in messages) / len(messages)
        max_heat = max(m.heat_score for m in messages)

        # Create episode node
        episode = NeuralNode(
            node_id=generate_node_id(),
            layer=NodeLayer.EPISODE,
            content=summary,
            embedding=agg_embedding,
            wave_amplitudes=agg_waves,
            entity_ids=list(all_entity_ids),
            session_key=session_key,
            importance_score=avg_importance,
            heat_score=max_heat,
            child_ids=[m.node_id for m in messages],
            source_memory_ids=[m.node_id for m in messages],
            metadata=metadata,
        )

        await self._storage.save_node(episode)

        # Update messages to point to parent episode
        for msg in messages:
            msg.parent_id = episode.node_id
            msg.consolidation_state = ConsolidationState.ARCHIVED
            await self._storage.save_node(msg)

            # Create hierarchy edge
            edge = NeuralEdge(
                edge_id=generate_edge_id(),
                source_id=episode.node_id,
                target_id=msg.node_id,
                edge_type=EdgeType.HIERARCHY,
                base_weight=1.0,
                confidence=1.0,
            )
            await self._storage.save_edge(edge)

        logger.info(
            f"Consolidated {len(messages)} messages into episode {episode.node_id[:8]}"
        )

        return episode

    async def auto_segment_to_episodes(
        self,
        session_key: str
    ) -> list[NeuralNode]:
        """Automatically segment messages into episodes.

        Uses time gaps and topic shifts to determine episode boundaries.

        Args:
            session_key: Session identifier

        Returns:
            List of created episode nodes
        """
        # Get all unassigned message nodes
        messages = await self._storage.get_nodes_by_layer(session_key, NodeLayer.MESSAGE)
        unassigned = [
            m for m in messages
            if m.parent_id is None and m.consolidation_state == ConsolidationState.ACTIVE
        ]

        if len(unassigned) < self._config.min_messages_per_episode:
            return []

        # Sort by creation time
        unassigned.sort(key=lambda m: m.created_at)

        # Segment by time gaps
        episodes = []
        current_segment: list[NeuralNode] = []

        for msg in unassigned:
            if current_segment:
                last_msg = current_segment[-1]
                time_gap = (msg.created_at - last_msg.created_at).total_seconds() / 3600

                # Check for segment boundary
                if (time_gap > self._config.episode_time_gap_hours or
                    len(current_segment) >= self._config.max_messages_per_episode):
                    # Create episode for current segment
                    if len(current_segment) >= self._config.min_messages_per_episode:
                        episode = await self.consolidate_to_episode(
                            [m.node_id for m in current_segment],
                            session_key
                        )
                        episodes.append(episode)
                    current_segment = []

            current_segment.append(msg)

        # Handle remaining segment
        if len(current_segment) >= self._config.min_messages_per_episode:
            episode = await self.consolidate_to_episode(
                [m.node_id for m in current_segment],
                session_key
            )
            episodes.append(episode)

        return episodes

    # =========================================================================
    # LAYER 2: TOPIC NODES
    # =========================================================================

    async def cluster_to_topic(
        self,
        episode_ids: list[str],
        topic_label: str,
        session_key: str,
        description: str | None = None,
        **metadata
    ) -> NeuralNode:
        """Cluster episodes into a topic node (Layer 1 → Layer 2).

        Topics represent semantic groupings of related episodes.

        Args:
            episode_ids: IDs of episodes to cluster
            topic_label: Topic label/name
            session_key: Session identifier
            description: Optional topic description
            **metadata: Additional metadata

        Returns:
            Created topic node

        Raises:
            ValueError: If no valid episodes found
        """
        # Fetch episodes
        episodes = []
        for eid in episode_ids:
            ep = await self._storage.get_node(eid)
            if ep and ep.layer == NodeLayer.EPISODE:
                episodes.append(ep)

        if not episodes:
            raise ValueError("No valid episode nodes found to cluster")

        # Compute centroid embedding
        embeddings = [e.embedding for e in episodes if e.embedding]
        if embeddings:
            embedding_dim = len(embeddings[0])
            centroid = [
                sum(e[i] for e in embeddings) / len(embeddings)
                for i in range(embedding_dim)
            ]
        else:
            centroid = None

        # Aggregate wave amplitudes
        all_amps = [e.wave_amplitudes for e in episodes if e.wave_amplitudes]
        if all_amps:
            agg_waves: dict[str, float] = {}
            all_keys = set()
            for a in all_amps:
                all_keys.update(a.keys())
            for key in all_keys:
                agg_waves[key] = sum(a.get(key, 0.0) for a in all_amps) / len(all_amps)
        else:
            agg_waves = {}

        # Collect all entity references
        all_entity_ids = set()
        for e in episodes:
            all_entity_ids.update(e.entity_ids)

        # Aggregate scores
        avg_importance = sum(e.importance_score for e in episodes) / len(episodes)
        avg_heat = sum(e.heat_score for e in episodes) / len(episodes)

        content = description or f"Topic: {topic_label}"

        # Create topic node
        topic = NeuralNode(
            node_id=generate_node_id(),
            layer=NodeLayer.TOPIC,
            content=content,
            embedding=centroid,
            wave_amplitudes=agg_waves,
            entity_ids=list(all_entity_ids),
            session_key=session_key,
            importance_score=avg_importance,
            heat_score=avg_heat,
            child_ids=[e.node_id for e in episodes],
            metadata={"label": topic_label, **metadata},
        )

        await self._storage.save_node(topic)

        # Update episodes to point to parent topic
        for episode in episodes:
            episode.parent_id = topic.node_id
            await self._storage.save_node(episode)

            # Create hierarchy edge
            edge = NeuralEdge(
                edge_id=generate_edge_id(),
                source_id=topic.node_id,
                target_id=episode.node_id,
                edge_type=EdgeType.HIERARCHY,
                base_weight=1.0,
                confidence=1.0,
            )
            await self._storage.save_edge(edge)

        logger.info(
            f"Clustered {len(episodes)} episodes into topic '{topic_label}' "
            f"({topic.node_id[:8]})"
        )

        return topic

    async def auto_cluster_to_topics(
        self,
        session_key: str
    ) -> list[NeuralNode]:
        """Automatically cluster episodes into topics using embedding similarity.

        Uses simple agglomerative clustering based on embedding similarity.

        Args:
            session_key: Session identifier

        Returns:
            List of created topic nodes
        """
        # Get unassigned episodes
        episodes = await self._storage.get_nodes_by_layer(session_key, NodeLayer.EPISODE)
        unassigned = [
            e for e in episodes
            if e.parent_id is None and e.embedding is not None
        ]

        if len(unassigned) < self._config.min_episodes_per_topic:
            return []

        # Simple clustering: group by embedding similarity
        clusters: list[list[NeuralNode]] = []
        used = set()

        for episode in unassigned:
            if episode.node_id in used:
                continue

            # Start new cluster with this episode
            cluster = [episode]
            used.add(episode.node_id)

            # Find similar episodes
            for other in unassigned:
                if other.node_id in used:
                    continue

                if episode.embedding and other.embedding:
                    sim = cosine_similarity(episode.embedding, other.embedding)
                    if sim >= self._config.topic_similarity_threshold:
                        cluster.append(other)
                        used.add(other.node_id)

            if len(cluster) >= self._config.min_episodes_per_topic:
                clusters.append(cluster)

        # Create topic nodes
        topics = []
        for i, cluster in enumerate(clusters):
            # Generate topic label from dominant entities
            all_entities = set()
            for ep in cluster:
                all_entities.update(ep.entity_ids)

            label = f"Topic_{i+1}"
            if all_entities:
                label = f"Topic: {', '.join(list(all_entities)[:3])}"

            topic = await self.cluster_to_topic(
                [ep.node_id for ep in cluster],
                label,
                session_key
            )
            topics.append(topic)

        return topics

    # =========================================================================
    # LAYER 3: PERSONA NODES
    # =========================================================================

    async def synthesize_persona(
        self,
        topic_ids: list[str],
        persona_aspect: str,
        session_key: str,
        description: str | None = None,
        **metadata
    ) -> NeuralNode:
        """Synthesize a persona facet from topics (Layer 2 → Layer 3).

        Persona nodes represent stable aspects of the user's identity,
        preferences, or behavior patterns.

        Args:
            topic_ids: IDs of topics to synthesize from
            persona_aspect: Aspect name (e.g., "communication_style")
            session_key: Session identifier
            description: Optional description
            **metadata: Additional metadata

        Returns:
            Created persona node

        Raises:
            ValueError: If no valid topics found
        """
        # Fetch topics
        topics = []
        for tid in topic_ids:
            topic = await self._storage.get_node(tid)
            if topic and topic.layer == NodeLayer.TOPIC:
                topics.append(topic)

        if not topics:
            raise ValueError("No valid topic nodes found to synthesize")

        # Compute stable embedding (weighted by heat/importance)
        embeddings = [(t.embedding, t.heat_score * t.importance_score)
                      for t in topics if t.embedding]
        if embeddings:
            total_weight = sum(w for _, w in embeddings)
            embedding_dim = len(embeddings[0][0])
            weighted_embedding = [0.0] * embedding_dim

            for emb, weight in embeddings:
                norm_weight = weight / total_weight if total_weight > 0 else 1.0 / len(embeddings)
                for i in range(embedding_dim):
                    weighted_embedding[i] += emb[i] * norm_weight
        else:
            weighted_embedding = None

        # Aggregate stable wave amplitudes (mean, not max)
        all_amps = [t.wave_amplitudes for t in topics if t.wave_amplitudes]
        if all_amps:
            agg_waves: dict[str, float] = {}
            all_keys = set()
            for a in all_amps:
                all_keys.update(a.keys())
            for key in all_keys:
                agg_waves[key] = sum(a.get(key, 0.0) for a in all_amps) / len(all_amps)
        else:
            agg_waves = {}

        # Collect key entities
        entity_counts: dict[str, int] = {}
        for t in topics:
            for eid in t.entity_ids:
                entity_counts[eid] = entity_counts.get(eid, 0) + 1

        # Keep entities that appear in multiple topics (stable)
        stable_entities = [
            eid for eid, count in entity_counts.items()
            if count >= len(topics) * 0.5
        ]

        content = description or f"Persona aspect: {persona_aspect}"

        # Persona nodes have high stability (importance/heat)
        importance = min(1.0, sum(t.importance_score for t in topics) / len(topics) * 1.2)
        heat = sum(t.heat_score for t in topics) / len(topics)

        # Create persona node
        persona = NeuralNode(
            node_id=generate_node_id(),
            layer=NodeLayer.PERSONA,
            content=content,
            embedding=weighted_embedding,
            wave_amplitudes=agg_waves,
            entity_ids=stable_entities,
            session_key=session_key,
            importance_score=importance,
            heat_score=heat,
            child_ids=[t.node_id for t in topics],
            metadata={"aspect": persona_aspect, **metadata},
        )

        await self._storage.save_node(persona)

        # Update topics to point to parent persona
        for topic in topics:
            topic.parent_id = persona.node_id
            await self._storage.save_node(topic)

            # Create hierarchy edge
            edge = NeuralEdge(
                edge_id=generate_edge_id(),
                source_id=persona.node_id,
                target_id=topic.node_id,
                edge_type=EdgeType.HIERARCHY,
                base_weight=1.0,
                confidence=1.0,
            )
            await self._storage.save_edge(edge)

        logger.info(
            f"Synthesized persona '{persona_aspect}' from {len(topics)} topics "
            f"({persona.node_id[:8]})"
        )

        return persona

    # =========================================================================
    # HIERARCHY TRAVERSAL
    # =========================================================================

    async def get_ancestors(self, node_id: str) -> list[NeuralNode]:
        """Get all ancestor nodes in the hierarchy.

        Traverses upward from node to root (persona level).

        Args:
            node_id: Starting node ID

        Returns:
            List of ancestor nodes (parent first, then grandparent, etc.)
        """
        ancestors = []
        current = await self._storage.get_node(node_id)

        while current and current.parent_id:
            parent = await self._storage.get_node(current.parent_id)
            if parent:
                ancestors.append(parent)
                current = parent
            else:
                break

        return ancestors

    async def get_descendants(
        self,
        node_id: str,
        max_depth: int = 3
    ) -> list[NeuralNode]:
        """Get all descendant nodes in the hierarchy.

        Traverses downward from node.

        Args:
            node_id: Starting node ID
            max_depth: Maximum depth to traverse

        Returns:
            List of descendant nodes
        """
        descendants = []
        to_visit = [(node_id, 0)]
        visited = set()

        while to_visit:
            current_id, depth = to_visit.pop(0)

            if current_id in visited or depth >= max_depth:
                continue
            visited.add(current_id)

            node = await self._storage.get_node(current_id)
            if node is None:
                continue

            for child_id in node.child_ids:
                if child_id not in visited:
                    child = await self._storage.get_node(child_id)
                    if child:
                        descendants.append(child)
                        to_visit.append((child_id, depth + 1))

        return descendants

    async def get_siblings(self, node_id: str) -> list[NeuralNode]:
        """Get sibling nodes (same parent).

        Args:
            node_id: Node ID

        Returns:
            List of sibling nodes
        """
        node = await self._storage.get_node(node_id)
        if node is None or node.parent_id is None:
            return []

        parent = await self._storage.get_node(node.parent_id)
        if parent is None:
            return []

        siblings = []
        for child_id in parent.child_ids:
            if child_id != node_id:
                child = await self._storage.get_node(child_id)
                if child:
                    siblings.append(child)

        return siblings

    async def get_layer_summary(self, session_key: str) -> dict[str, Any]:
        """Get summary statistics for each layer.

        Args:
            session_key: Session identifier

        Returns:
            Dictionary with layer statistics
        """
        summary = {}

        for layer in NodeLayer:
            nodes = await self._storage.get_nodes_by_layer(session_key, layer)
            if nodes:
                summary[layer.name] = {
                    "count": len(nodes),
                    "avg_heat": sum(n.heat_score for n in nodes) / len(nodes),
                    "avg_importance": sum(n.importance_score for n in nodes) / len(nodes),
                    "with_parent": sum(1 for n in nodes if n.parent_id),
                    "with_children": sum(1 for n in nodes if n.child_ids),
                }
            else:
                summary[layer.name] = {"count": 0}

        return summary

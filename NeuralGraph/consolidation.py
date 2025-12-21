"""Memory consolidation for Neural Memory Graph.

Implements compression and pruning (Image 5: SVD/Sketching):
- Periodic edge pruning (remove weak, old edges)
- Node merging (deduplicate similar nodes)
- Summary hub creation (compress dense clusters)
- Heat-based promotion/eviction

The consolidation system enables:
- Memory efficiency through compression
- Noise removal through pruning
- Knowledge crystallization through hub creation
- Forgetting through eviction
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .data_types import (
    ConsolidationResult,
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
    from .hierarchy import HierarchyManager

logger = logging.getLogger(__name__)


@dataclass
class ConsolidationConfig:
    """Configuration for memory consolidation.

    Heat system tuning based on research:
    - Ebbinghaus forgetting curve (exponential decay)
    - Spaced repetition (SM-2, FSRS) - 3 reviews for durability
    - Biological LTP - 15% strengthening per activation
    - Memory consolidation - 7-day critical period

    THE PLAN Enhancement:
    - Recency-aware retention for factual nodes
    - Defer pruning of single-hop facts until corroborating edges exist
    """

    # Edge pruning
    prune_weight_threshold: float = 0.1  # Minimum effective weight
    prune_age_threshold_days: float = 14.0  # 2 weeks before edge pruning eligible
    prune_batch_size: int = 100

    # Node merging
    # Research shows 0.72-0.75 is sweet spot for semantic deduplication
    merge_similarity_threshold: float = 0.72  # Was 0.85 - too strict
    merge_max_per_cycle: int = 20

    # State recovery
    stuck_state_timeout_minutes: float = 5.0  # Recover CONSOLIDATING nodes stuck > 5 min

    # Summary hub creation
    hub_cluster_size_threshold: int = 5  # Minimum nodes for hub
    hub_density_threshold: float = 0.6  # Edge density for hub trigger
    hub_max_per_cycle: int = 5

    # Heat-based lifecycle (TUNED based on research)
    # Promotion: reachable in 6-7 activations (SM-2 shows 3 reviews sufficient)
    promotion_heat_threshold: float = 2.5  # Was 5.0 - unreachable

    # Eviction: must be BELOW initial heat (0.5) after decay
    eviction_heat_threshold: float = 0.25  # Was 0.5 - same as initial!
    eviction_age_threshold_days: float = 7.0  # Was 30.0 - 1 week minimum
    eviction_recency_threshold_days: float = 7.0  # NEW: must be inactive 7 days

    # Decay rates (Ebbinghaus curve: ~13% loss per week for active memories)
    heat_decay_rate: float = 0.015  # Was 0.03 - too aggressive
    inactive_decay_multiplier: float = 2.0  # 2x decay if not activated recently
    inactive_threshold_days: float = 3.0  # Considered inactive after 3 days

    # Layer-specific decay rates (higher layers = more stable)
    message_decay_rate: float = 0.02  # Short-term, faster decay
    episode_decay_rate: float = 0.015  # Medium-term
    topic_decay_rate: float = 0.01  # Long-term
    persona_decay_rate: float = 0.005  # Very stable

    # Heat boost per activation (LTP research: ~15% increase)
    heat_boost_base: float = 0.2  # Was 0.1 in NeuralNode - too small
    heat_boost_recency_bonus: float = 0.05  # Bonus for repeated access
    max_heat_score: float = 10.0

    # Promotion thresholds per layer
    message_to_episode_threshold: float = 2.5
    episode_to_topic_threshold: float = 3.5
    topic_to_persona_threshold: float = 4.5
    min_activations_for_promotion: int = 3  # SM-2 optimal

    # Scheduling
    min_cycle_interval_seconds: float = 60.0

    # THE PLAN: Recency-aware retention for single-hop facts
    # "recency-aware retention so factual nodes stay available"
    recency_aware_retention: bool = True
    factual_node_protection: bool = True  # Protect single-hop factual nodes
    min_corroborating_edges: int = 1  # Defer eviction until this many edges exist
    factual_heat_boost: float = 0.15  # Extra heat for factual nodes
    recency_window_days: float = 14.0  # Consider "recent" for retention
    entity_node_protection: bool = True  # Protect nodes with entity mentions
    speaker_fact_retention: bool = True  # Extra protection for speaker-attributed facts


class ConsolidationManager:
    """Memory consolidation system (Image 5: Compression/Pruning).

    Implements neural network compression principles:
    - Pruning: Remove weak edges (like pruning small weights)
    - Merging: Combine similar nodes (like weight sharing)
    - Hub creation: Create summary nodes (like low-rank approximation)
    - Eviction: Remove cold nodes (like dropout/forgetting)

    The system runs periodic consolidation cycles that:
    1. Prune weak edges to reduce noise
    2. Merge duplicate/similar nodes
    3. Create summary hubs for dense clusters
    4. Promote/evict nodes based on heat
    """

    def __init__(
        self,
        storage: NeuralGraphStorage,
        hierarchy_manager: HierarchyManager | None = None,
        temporal_manager: Any | None = None,
        config: ConsolidationConfig | None = None
    ):
        """Initialize consolidation manager.

        Args:
            storage: Neural graph storage backend
            hierarchy_manager: Optional hierarchy manager for promotions
            temporal_manager: Optional temporal manager for hub linking
            config: Consolidation configuration
        """
        self._storage = storage
        self._hierarchy = hierarchy_manager
        self._temporal = temporal_manager
        self._config = config or ConsolidationConfig()
        self._last_cycle_time: float = 0

    # =========================================================================
    # MAIN CONSOLIDATION CYCLE
    # =========================================================================

    async def run_consolidation_cycle(
        self,
        session_key: str
    ) -> ConsolidationResult:
        """Run full consolidation cycle.

        Executes all consolidation phases:
        0. Recover orphaned nodes (stuck in intermediate states)
        1. Prune weak edges
        2. Merge similar nodes
        3. Create summary hubs
        4. Process heat-based lifecycle

        Args:
            session_key: Session identifier

        Returns:
            ConsolidationResult with statistics
        """
        start_time = time.perf_counter()

        # Check minimum cycle interval
        current_time = time.time()
        if current_time - self._last_cycle_time < self._config.min_cycle_interval_seconds:
            return ConsolidationResult(
                session_key=session_key,
                edges_pruned=0,
                nodes_merged=0,
                hubs_created=0,
                nodes_promoted=0,
                nodes_evicted=0,
                total_time_ms=0,
            )

        self._last_cycle_time = current_time

        # Phase 0: Recover orphaned nodes stuck in intermediate states
        recovered = await self._recover_orphaned_nodes(session_key)
        if recovered > 0:
            logger.info(f"Recovered {recovered} orphaned nodes from intermediate states")

        # Phase 1: Prune weak edges
        edges_pruned = await self._prune_weak_edges(session_key)

        # Phase 2: Merge similar nodes
        nodes_merged = await self._merge_similar_nodes(session_key)

        # Phase 3: Create summary hubs
        hubs_created = await self._create_summary_hubs(session_key)

        # Phase 4: Heat-based promotion/eviction
        promoted, evicted = await self._process_heat_lifecycle(session_key)

        total_time_ms = (time.perf_counter() - start_time) * 1000

        result = ConsolidationResult(
            session_key=session_key,
            edges_pruned=edges_pruned,
            nodes_merged=nodes_merged,
            hubs_created=hubs_created,
            nodes_promoted=promoted,
            nodes_evicted=evicted,
            total_time_ms=total_time_ms,
        )

        logger.info(
            f"Consolidation cycle for {session_key}: "
            f"pruned={edges_pruned}, merged={nodes_merged}, "
            f"hubs={hubs_created}, promoted={promoted}, evicted={evicted}"
        )

        return result

    async def _recover_orphaned_nodes(self, session_key: str) -> int:
        """Recover nodes stuck in intermediate consolidation states.

        Nodes can get stuck in CONSOLIDATING state if the process crashes
        during a consolidation cycle. This method recovers them.

        Args:
            session_key: Session identifier

        Returns:
            Number of nodes recovered
        """
        nodes = await self._storage.get_all_nodes(session_key)
        recovered = 0
        timeout = timedelta(minutes=self._config.stuck_state_timeout_minutes)

        for node in nodes:
            if node.consolidation_state == ConsolidationState.CONSOLIDATING:
                # Check if stuck (updated_at is old)
                age = datetime.now(timezone.utc) - node.updated_at
                if age > timeout:
                    # Decide recovery action based on heat score
                    if node.heat_score < self._config.eviction_heat_threshold:
                        # Low heat: mark for eviction
                        node.consolidation_state = ConsolidationState.EVICTED
                    else:
                        # Has heat: restore to active
                        node.consolidation_state = ConsolidationState.ACTIVE

                    await self._storage.save_node(node)
                    recovered += 1

                    logger.debug(
                        f"Recovered stuck node {node.node_id[:8]} "
                        f"(was CONSOLIDATING for {age.total_seconds():.0f}s, "
                        f"now {node.consolidation_state.value})"
                    )

        return recovered

    # =========================================================================
    # PHASE 1: EDGE PRUNING
    # =========================================================================

    async def _prune_weak_edges(self, session_key: str) -> int:
        """Remove edges that are weak AND old (Image 5: pruning).

        Edges are pruned if:
        - Effective weight < threshold (weak)
        - Age > threshold (old/stale)

        This removes noise and reduces graph complexity.

        Args:
            session_key: Session identifier

        Returns:
            Number of edges pruned
        """
        all_edges = await self._storage.get_all_edges(session_key)
        pruned = 0

        for edge in all_edges:
            if pruned >= self._config.prune_batch_size:
                break

            age_days = edge.days_since_activation
            effective_weight = abs(edge.effective_weight)

            # Prune if weak AND old
            if (effective_weight < self._config.prune_weight_threshold and
                    age_days > self._config.prune_age_threshold_days):

                await self._storage.delete_edge(edge.edge_id)
                pruned += 1

                logger.debug(
                    f"Pruned edge {edge.edge_id[:8]} "
                    f"(weight={effective_weight:.3f}, age={age_days:.1f}d)"
                )

        return pruned

    async def prune_edges_by_type(
        self,
        session_key: str,
        edge_type: EdgeType,
        weight_threshold: float | None = None
    ) -> int:
        """Prune edges of a specific type.

        Args:
            session_key: Session identifier
            edge_type: Type of edges to prune
            weight_threshold: Override weight threshold

        Returns:
            Number of edges pruned
        """
        threshold = weight_threshold or self._config.prune_weight_threshold

        all_edges = await self._storage.get_all_edges(session_key)
        type_edges = [e for e in all_edges if e.edge_type == edge_type]

        pruned = 0
        for edge in type_edges:
            if abs(edge.effective_weight) < threshold:
                await self._storage.delete_edge(edge.edge_id)
                pruned += 1

        return pruned

    # =========================================================================
    # PHASE 2: NODE MERGING
    # =========================================================================

    async def _merge_similar_nodes(self, session_key: str) -> int:
        """Merge nodes that are effectively duplicates (Image 5: compression).

        Nodes are merged if their embeddings have high cosine similarity.
        The merged node retains combined heat/importance.

        Args:
            session_key: Session identifier

        Returns:
            Number of nodes merged
        """
        # Focus on message layer (most duplicates)
        nodes = await self._storage.get_nodes_by_layer(session_key, NodeLayer.MESSAGE)

        # Filter to active nodes with embeddings
        mergeable = [
            n for n in nodes
            if n.embedding is not None and n.consolidation_state == ConsolidationState.ACTIVE
        ]

        merged = 0
        merged_ids: set[str] = set()

        for i, node1 in enumerate(mergeable):
            if merged >= self._config.merge_max_per_cycle:
                break

            if node1.node_id in merged_ids:
                continue

            for node2 in mergeable[i + 1:]:
                if node2.node_id in merged_ids:
                    continue

                # Check similarity
                sim = cosine_similarity(node1.embedding, node2.embedding)

                if sim >= self._config.merge_similarity_threshold:
                    # Merge node2 into node1
                    await self._merge_nodes(node1, node2)
                    merged_ids.add(node2.node_id)
                    merged += 1

                    logger.debug(
                        f"Merged node {node2.node_id[:8]} into {node1.node_id[:8]} "
                        f"(similarity={sim:.3f})"
                    )

        return merged

    async def _merge_nodes(
        self,
        keep: NeuralNode,
        remove: NeuralNode
    ) -> None:
        """Merge remove node into keep node.

        Args:
            keep: Node to keep (receives merged data)
            remove: Node to remove
        """
        # Transfer edges
        edges_from = await self._storage.get_edges_from(remove.node_id)
        edges_to = await self._storage.get_edges_to(remove.node_id)

        for edge in edges_from:
            if edge.target_id != keep.node_id:  # Avoid self-loops
                edge.source_id = keep.node_id
                await self._storage.save_edge(edge)

        for edge in edges_to:
            if edge.source_id != keep.node_id:  # Avoid self-loops
                edge.target_id = keep.node_id
                await self._storage.save_edge(edge)

        # Update keep with merged properties
        keep.heat_score = max(keep.heat_score, remove.heat_score)
        keep.importance_score = max(keep.importance_score, remove.importance_score)

        # Merge entity references
        keep.entity_ids = list(set(keep.entity_ids + remove.entity_ids))

        # Merge source memory IDs
        keep.source_memory_ids = list(set(keep.source_memory_ids + remove.source_memory_ids))

        # Update content (keep longer content)
        if len(remove.content) > len(keep.content):
            keep.content = remove.content

        keep.updated_at = datetime.now(timezone.utc)
        await self._storage.save_node(keep)

        # Delete remove node
        await self._storage.delete_node(remove.node_id)

    # =========================================================================
    # PHASE 3: SUMMARY HUB CREATION
    # =========================================================================

    async def _create_summary_hubs(self, session_key: str) -> int:
        """Create summary hub nodes for dense clusters (Image 5: sketching).

        When many nodes are densely connected, create a hub node
        that summarizes them. This is like low-rank matrix approximation:
        replace N² dense connections with N→hub→N.

        Args:
            session_key: Session identifier

        Returns:
            Number of hubs created
        """
        # Find dense clusters
        clusters = await self._find_dense_clusters(session_key)

        hubs_created = 0

        for cluster in clusters:
            if hubs_created >= self._config.hub_max_per_cycle:
                break

            if len(cluster) < self._config.hub_cluster_size_threshold:
                continue

            # Create hub for cluster
            hub = await self._create_hub_for_cluster(cluster, session_key)
            if hub:
                hubs_created += 1

        return hubs_created

    async def _find_dense_clusters(
        self,
        session_key: str
    ) -> list[list[NeuralNode]]:
        """Find clusters of densely connected nodes.

        Uses entity overlap as clustering signal.

        Args:
            session_key: Session identifier

        Returns:
            List of node clusters
        """
        nodes = await self._storage.get_nodes_by_layer(session_key, NodeLayer.MESSAGE)

        # Filter to nodes with entities
        nodes_with_entities = [n for n in nodes if n.entity_ids]

        if len(nodes_with_entities) < self._config.hub_cluster_size_threshold:
            return []

        # Group by shared entities
        entity_to_nodes: dict[str, list[NeuralNode]] = {}
        for node in nodes_with_entities:
            for entity_id in node.entity_ids:
                if entity_id not in entity_to_nodes:
                    entity_to_nodes[entity_id] = []
                entity_to_nodes[entity_id].append(node)

        # Find clusters where multiple nodes share entities
        clusters: list[list[NeuralNode]] = []
        used_nodes: set[str] = set()

        for entity_id, sharing_nodes in entity_to_nodes.items():
            if len(sharing_nodes) < self._config.hub_cluster_size_threshold:
                continue

            # Check if nodes are already clustered
            unused = [n for n in sharing_nodes if n.node_id not in used_nodes]
            if len(unused) >= self._config.hub_cluster_size_threshold:
                clusters.append(unused)
                for n in unused:
                    used_nodes.add(n.node_id)

        return clusters

    async def _create_hub_for_cluster(
        self,
        cluster: list[NeuralNode],
        session_key: str
    ) -> NeuralNode | None:
        """Create a summary hub node for a cluster.

        Args:
            cluster: Nodes to summarize
            session_key: Session identifier

        Returns:
            Created hub node
        """
        if not cluster:
            return None

        # Compute centroid embedding
        embeddings = [n.embedding for n in cluster if n.embedding]
        if embeddings:
            dim = len(embeddings[0])
            centroid = [
                sum(e[i] for e in embeddings) / len(embeddings)
                for i in range(dim)
            ]
        else:
            centroid = None

        # Aggregate wave amplitudes
        all_amps = [n.wave_amplitudes for n in cluster if n.wave_amplitudes]
        if all_amps:
            agg_waves: dict[str, float] = {}
            all_keys = set()
            for a in all_amps:
                all_keys.update(a.keys())
            for key in all_keys:
                agg_waves[key] = sum(a.get(key, 0.0) for a in all_amps) / len(all_amps)
        else:
            agg_waves = {}

        # Collect entities
        all_entities = set()
        for n in cluster:
            all_entities.update(n.entity_ids)

        # Generate summary content
        entity_list = list(all_entities)[:5]
        summary = f"Summary hub: {len(cluster)} nodes about {', '.join(entity_list)}"

        # Create hub node
        hub = NeuralNode(
            node_id=generate_node_id(),
            layer=NodeLayer.TOPIC,  # Hubs are topic-layer
            content=summary,
            embedding=centroid,
            wave_amplitudes=agg_waves,
            entity_ids=list(all_entities),
            session_key=session_key,
            importance_score=max(n.importance_score for n in cluster),
            heat_score=sum(n.heat_score for n in cluster) / len(cluster),
            child_ids=[n.node_id for n in cluster],
            metadata={"hub_type": "summary", "cluster_size": len(cluster)},
        )

        # Determine hub's temporal position (median of cluster creation times)
        # This allows hub to be linked into temporal chains
        cluster_times = sorted(n.created_at for n in cluster if n.created_at)
        if cluster_times:
            hub.created_at = cluster_times[len(cluster_times) // 2]

        await self._storage.save_node(hub)

        # Create hierarchy edges from hub to cluster members
        for node in cluster:
            edge = NeuralEdge(
                edge_id=generate_edge_id(),
                source_id=hub.node_id,
                target_id=node.node_id,
                edge_type=EdgeType.HIERARCHY,
                base_weight=1.0,
            )
            await self._storage.save_edge(edge)

            # Update node to reference hub
            node.parent_id = hub.node_id
            await self._storage.save_node(node)

        # NEW: Insert hub into temporal chain so it's reachable via temporal traversal
        if self._temporal:
            try:
                await self._temporal.auto_link_temporal(session_key, NodeLayer.TOPIC)
            except Exception as e:
                logger.warning(f"Failed to link hub {hub.node_id[:8]} to temporal chain: {e}")

        logger.info(
            f"Created summary hub {hub.node_id[:8]} for {len(cluster)} nodes"
        )

        return hub

    # =========================================================================
    # PHASE 4: HEAT-BASED LIFECYCLE
    # =========================================================================

    async def _process_heat_lifecycle(
        self,
        session_key: str
    ) -> tuple[int, int]:
        """Process heat-based promotion and eviction.

        Implements research-backed memory lifecycle:
        - Ebbinghaus curve: decay based on days_since_ACTIVATION (not creation)
        - Layer-specific decay: higher layers are more stable
        - Inactive penalty: unused memories decay faster
        - SM-2 promotion: 3+ activations + heat threshold
        - Safe eviction: requires age + recency + low heat

        Args:
            session_key: Session identifier

        Returns:
            Tuple of (promoted_count, evicted_count)
        """
        nodes = await self._storage.get_all_nodes(session_key)

        promoted = 0
        evicted = 0

        for node in nodes:
            # Get layer-specific decay rate (higher layers = more stable)
            if node.layer == NodeLayer.MESSAGE:
                base_decay_rate = self._config.message_decay_rate
            elif node.layer == NodeLayer.EPISODE:
                base_decay_rate = self._config.episode_decay_rate
            elif node.layer == NodeLayer.TOPIC:
                base_decay_rate = self._config.topic_decay_rate
            else:  # PERSONA
                base_decay_rate = self._config.persona_decay_rate

            # Apply inactive penalty (2x decay if not accessed recently)
            if node.days_since_activation > self._config.inactive_threshold_days:
                decay_rate = base_decay_rate * self._config.inactive_decay_multiplier
            else:
                decay_rate = base_decay_rate

            # CRITICAL FIX: Use days_since_activation, NOT days_since_creation
            # Ebbinghaus forgetting curve applies to time since last retrieval
            decay_factor = math.exp(-decay_rate * node.days_since_activation)
            node.heat_score *= decay_factor

            # Get layer-specific promotion threshold
            if node.layer == NodeLayer.MESSAGE:
                promotion_threshold = self._config.message_to_episode_threshold
            elif node.layer == NodeLayer.EPISODE:
                promotion_threshold = self._config.episode_to_topic_threshold
            elif node.layer == NodeLayer.TOPIC:
                promotion_threshold = self._config.topic_to_persona_threshold
            else:
                promotion_threshold = float('inf')  # Persona can't promote further

            # Check for promotion (high heat + minimum activations)
            # SM-2 research: 3 reviews is optimal for durable memory
            activation_count = getattr(node, '_activation_count', 0)
            if (node.heat_score >= promotion_threshold and
                    activation_count >= self._config.min_activations_for_promotion and
                    node.consolidation_state == ConsolidationState.ACTIVE):

                node.consolidation_state = ConsolidationState.CONSOLIDATING
                promoted += 1

                logger.debug(
                    f"Marked node {node.node_id[:8]} for promotion "
                    f"(heat={node.heat_score:.2f}, activations={activation_count})"
                )

            # Check for eviction (low heat + old + inactive)
            # SAFE eviction requires ALL THREE conditions:
            # 1. Heat below threshold (memory is cold)
            # 2. Age above threshold (memory has existed long enough)
            # 3. Recency above threshold (memory hasn't been accessed recently)
            elif (node.heat_score <= self._config.eviction_heat_threshold and
                    node.days_since_creation >= self._config.eviction_age_threshold_days and
                    node.days_since_activation >= self._config.eviction_recency_threshold_days and
                    node.consolidation_state == ConsolidationState.ACTIVE):

                # THE PLAN: Recency-aware retention for factual nodes
                # "defer pruning of single-hop facts until they have corroborating edges"
                if self._config.recency_aware_retention:
                    # Check if this is a factual node that should be protected
                    should_protect = await self._should_protect_factual_node(node)
                    if should_protect:
                        logger.debug(
                            f"Protected factual node {node.node_id[:8]} from eviction "
                            f"(entities={node.entity_ids[:2]}, heat={node.heat_score:.2f})"
                        )
                        # Apply factual heat boost to keep it alive longer
                        node.heat_score += self._config.factual_heat_boost
                        await self._storage.save_node(node)
                        continue

                # Clean up parent's child_ids reference before eviction
                if node.parent_id:
                    parent = await self._storage.get_node(node.parent_id)
                    if parent and node.node_id in parent.child_ids:
                        parent.child_ids.remove(node.node_id)
                        await self._storage.save_node(parent)

                # CASCADE: Delete all edges from this node
                edges_from = await self._storage.get_edges_from(node.node_id)
                for edge in edges_from:
                    await self._storage.delete_edge(edge.edge_id)

                # CASCADE: Delete all edges to this node
                edges_to = await self._storage.get_edges_to(node.node_id)
                for edge in edges_to:
                    await self._storage.delete_edge(edge.edge_id)

                await self._storage.delete_node(node.node_id)
                evicted += 1

                logger.debug(
                    f"Evicted node {node.node_id[:8]} "
                    f"(heat={node.heat_score:.2f}, age={node.days_since_creation:.1f}d, "
                    f"inactive={node.days_since_activation:.1f}d, "
                    f"edges_deleted={len(edges_from) + len(edges_to)})"
                )
                continue

            await self._storage.save_node(node)

        return promoted, evicted

    # =========================================================================
    # HEAT MANAGEMENT
    # =========================================================================

    async def boost_heat(
        self,
        node_id: str,
        amount: float = 0.5
    ) -> float:
        """Boost a node's heat score.

        Args:
            node_id: Node identifier
            amount: Heat to add

        Returns:
            New heat score
        """
        node = await self._storage.get_node(node_id)
        if node is None:
            return 0.0

        node.heat_score = min(10.0, node.heat_score + amount)
        await self._storage.save_node(node)

        return node.heat_score

    async def decay_all_heat(
        self,
        session_key: str,
        decay_rate: float | None = None
    ) -> int:
        """Apply heat decay to all nodes.

        Uses days_since_activation (not creation) per Ebbinghaus research.
        Applies layer-specific decay rates for stability gradient.

        Args:
            session_key: Session identifier
            decay_rate: Override decay rate (ignores layer-specific if set)

        Returns:
            Number of nodes decayed
        """
        nodes = await self._storage.get_all_nodes(session_key)
        decayed = 0

        for node in nodes:
            if node.heat_score > 0:
                # Get layer-specific rate unless overridden
                if decay_rate is not None:
                    rate = decay_rate
                elif node.layer == NodeLayer.MESSAGE:
                    rate = self._config.message_decay_rate
                elif node.layer == NodeLayer.EPISODE:
                    rate = self._config.episode_decay_rate
                elif node.layer == NodeLayer.TOPIC:
                    rate = self._config.topic_decay_rate
                else:
                    rate = self._config.persona_decay_rate

                # Apply inactive penalty
                if node.days_since_activation > self._config.inactive_threshold_days:
                    rate *= self._config.inactive_decay_multiplier

                # CRITICAL: Use days_since_activation, not days_since_creation
                decay_factor = math.exp(-rate * node.days_since_activation)
                node.heat_score *= decay_factor
                await self._storage.save_node(node)
                decayed += 1

        return decayed

    # =========================================================================
    # STATISTICS & ANALYSIS
    # =========================================================================

    async def get_consolidation_statistics(
        self,
        session_key: str
    ) -> dict[str, Any]:
        """Get consolidation-related statistics.

        Args:
            session_key: Session identifier

        Returns:
            Statistics dictionary
        """
        nodes = await self._storage.get_all_nodes(session_key)
        edges = await self._storage.get_all_edges(session_key)

        if not nodes:
            return {"node_count": 0, "edge_count": 0}

        # Heat distribution
        heat_scores = [n.heat_score for n in nodes]
        avg_heat = sum(heat_scores) / len(heat_scores)
        min_heat = min(heat_scores)
        max_heat = max(heat_scores)

        # State distribution
        state_counts = {}
        for state in ConsolidationState:
            state_counts[state.value] = sum(
                1 for n in nodes if n.consolidation_state == state
            )

        # Edge weight distribution
        weights = [abs(e.effective_weight) for e in edges]
        avg_weight = sum(weights) / len(weights) if weights else 0
        weak_edges = sum(1 for w in weights if w < self._config.prune_weight_threshold)

        # Age distribution
        ages = [n.days_since_creation for n in nodes]
        avg_age = sum(ages) / len(ages)
        old_nodes = sum(
            1 for n in nodes
            if n.days_since_creation > self._config.eviction_age_threshold_days
        )

        return {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "heat": {
                "average": avg_heat,
                "min": min_heat,
                "max": max_heat,
                "ready_for_promotion": sum(
                    1 for h in heat_scores
                    if h >= self._config.promotion_heat_threshold
                ),
                "ready_for_eviction": sum(
                    1 for n in nodes
                    if n.heat_score <= self._config.eviction_heat_threshold
                    and n.days_since_creation >= self._config.eviction_age_threshold_days
                ),
            },
            "consolidation_states": state_counts,
            "edges": {
                "average_weight": avg_weight,
                "weak_edges": weak_edges,
                "weak_edge_ratio": weak_edges / len(edges) if edges else 0,
            },
            "age": {
                "average_days": avg_age,
                "old_nodes": old_nodes,
            },
        }

    async def estimate_memory_savings(
        self,
        session_key: str
    ) -> dict[str, Any]:
        """Estimate potential memory savings from consolidation.

        Args:
            session_key: Session identifier

        Returns:
            Savings estimates
        """
        stats = await self.get_consolidation_statistics(session_key)

        potential_edge_pruning = stats["edges"]["weak_edges"]
        potential_evictions = stats["heat"]["ready_for_eviction"]

        # Rough estimates
        bytes_per_edge = 200  # Approximate
        bytes_per_node = 1000  # Approximate

        return {
            "edges_to_prune": potential_edge_pruning,
            "nodes_to_evict": potential_evictions,
            "estimated_savings_bytes": (
                potential_edge_pruning * bytes_per_edge +
                potential_evictions * bytes_per_node
            ),
            "edge_reduction_ratio": (
                potential_edge_pruning / stats["edge_count"]
                if stats["edge_count"] > 0 else 0
            ),
            "node_reduction_ratio": (
                potential_evictions / stats["node_count"]
                if stats["node_count"] > 0 else 0
            ),
        }

    # =========================================================================
    # THE PLAN: RECENCY-AWARE RETENTION FOR SINGLE-HOP FACTS
    # =========================================================================

    async def _should_protect_factual_node(self, node: NeuralNode) -> bool:
        """Determine if a node should be protected from eviction.

        THE PLAN: "recency-aware retention so factual nodes stay available;
        defer pruning of single-hop facts until they have corroborating edges."

        Protection criteria (any one triggers protection):
        1. Node has entity mentions (likely contains facts about someone)
        2. Node has speaker attribution (first-person facts)
        3. Node lacks sufficient corroborating edges (single-hop orphan)
        4. Node is within recency window (recently created)

        Args:
            node: Node to check for protection

        Returns:
            True if node should be protected from eviction
        """
        # Check if within recency window
        if node.days_since_creation < self._config.recency_window_days:
            return True

        # Check for entity mentions (likely factual content)
        if self._config.entity_node_protection and node.entity_ids:
            # Nodes about specific entities are valuable for single-hop QA
            if len(node.entity_ids) >= 1:
                return True

        # Check for speaker attribution (first-person facts)
        if self._config.speaker_fact_retention:
            speaker = node.speaker_id
            if speaker and speaker.lower() != "unknown":
                # This is a speaker-attributed fact (e.g., "I am a teacher")
                return True

        # Check for factual content patterns
        if self._config.factual_node_protection:
            content = node.content or ""
            # Detect factual statement patterns
            factual_patterns = [
                " is ", " are ", " was ", " were ",
                " has ", " have ", " had ",
                " works ", " works as ", " work ",
                " lives ", " lived ",
                " born ", " moved ", " started ",
            ]
            if any(pattern in content.lower() for pattern in factual_patterns):
                return True

        # Check corroborating edges (defer eviction if not enough edges)
        edges_from = await self._storage.get_edges_from(node.node_id)
        edges_to = await self._storage.get_edges_to(node.node_id)
        total_edges = len(edges_from) + len(edges_to)

        if total_edges < self._config.min_corroborating_edges:
            # Single-hop orphan node - protect until it gains connections
            # This prevents evicting factual nodes that haven't been linked yet
            return True

        return False

    async def protect_session_facts(self, session_key: str) -> int:
        """Proactively boost heat for factual nodes in a session.

        THE PLAN: "factual nodes stay available"

        Call this after ingestion to mark factual nodes for retention.

        Args:
            session_key: Session identifier

        Returns:
            Number of nodes protected
        """
        nodes = await self._storage.get_all_nodes(session_key)
        protected = 0

        for node in nodes:
            if await self._should_protect_factual_node(node):
                # Apply a small heat boost to keep factual nodes above eviction
                if node.heat_score < self._config.eviction_heat_threshold + 0.2:
                    node.heat_score += self._config.factual_heat_boost
                    await self._storage.save_node(node)
                    protected += 1

        logger.info(f"Protected {protected} factual nodes in session {session_key}")
        return protected

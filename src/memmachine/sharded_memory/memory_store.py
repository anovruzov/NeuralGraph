"""
Sharded Memory Store.

Provides the storage layer for sharded memories with:
- Per-shard storage backends
- Index management for fast lookups
- Batch operations for efficiency
"""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .data_types import (
    Domain,
    DomainEdge,
    DomainType,
    HashPointer,
    Memory,
    MemoryDomainEdge,
    MemoryType,
    ShardInfo,
    Subdomain,
    SubdomainType,
)
from .domain_hierarchy import DomainHierarchyManager, SUBDOMAIN_TO_DOMAIN
from .shard_router import ShardRouter

logger = logging.getLogger(__name__)


# =============================================================================
# ABSTRACT SHARD STORAGE
# =============================================================================

class MemoryShardStorage(ABC):
    """
    Abstract base class for shard storage backends.

    Implementations can use various backends:
    - In-memory (for testing/development)
    - SQLite/PostgreSQL (for production)
    - Redis (for caching)
    - Vector DB (for similarity search)
    """

    @abstractmethod
    async def store_memory(self, memory: Memory) -> None:
        """Store a memory in this shard."""
        pass

    @abstractmethod
    async def get_memory(self, memory_id: str) -> Memory | None:
        """Get a memory by ID."""
        pass

    @abstractmethod
    async def get_memories(self, memory_ids: list[str]) -> list[Memory]:
        """Get multiple memories by IDs."""
        pass

    @abstractmethod
    async def query_by_domain(
        self,
        domain_id: str,
        limit: int = 100,
    ) -> list[Memory]:
        """Query memories by domain."""
        pass

    @abstractmethod
    async def query_by_subdomain(
        self,
        subdomain_id: str,
        limit: int = 100,
    ) -> list[Memory]:
        """Query memories by subdomain."""
        pass

    @abstractmethod
    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory."""
        pass

    @abstractmethod
    async def get_memory_count(self) -> int:
        """Get total memory count in this shard."""
        pass


# =============================================================================
# IN-MEMORY SHARD STORAGE (For development/testing)
# =============================================================================

class InMemoryShardStorage(MemoryShardStorage):
    """In-memory implementation for development and testing."""

    def __init__(self, shard_id: int):
        self.shard_id = shard_id
        self._memories: dict[str, Memory] = {}
        # Indexes for fast lookup
        self._domain_index: dict[str, set[str]] = defaultdict(set)
        self._subdomain_index: dict[str, set[str]] = defaultdict(set)
        self._user_index: dict[str, set[str]] = defaultdict(set)
        self._type_index: dict[MemoryType, set[str]] = defaultdict(set)

    async def store_memory(self, memory: Memory) -> None:
        """Store a memory and update indexes."""
        self._memories[memory.memory_id] = memory

        # Update indexes
        self._user_index[memory.user_id].add(memory.memory_id)
        self._type_index[memory.memory_type].add(memory.memory_id)

        if memory.primary_domain_id:
            self._domain_index[memory.primary_domain_id].add(memory.memory_id)

        if memory.primary_subdomain_id:
            self._subdomain_index[memory.primary_subdomain_id].add(memory.memory_id)

        # Index all linked domains/subdomains
        for pointer in memory.domain_pointers:
            if pointer.target_type == "domain":
                self._domain_index[pointer.target_id].add(memory.memory_id)
            elif pointer.target_type == "subdomain":
                self._subdomain_index[pointer.target_id].add(memory.memory_id)

    async def get_memory(self, memory_id: str) -> Memory | None:
        """Get a memory by ID."""
        memory = self._memories.get(memory_id)
        if memory:
            memory.touch()
        return memory

    async def get_memories(self, memory_ids: list[str]) -> list[Memory]:
        """Get multiple memories by IDs."""
        result = []
        for mid in memory_ids:
            memory = self._memories.get(mid)
            if memory:
                memory.touch()
                result.append(memory)
        return result

    async def query_by_domain(
        self,
        domain_id: str,
        limit: int = 100,
    ) -> list[Memory]:
        """Query memories by domain."""
        memory_ids = list(self._domain_index.get(domain_id, set()))[:limit]
        return await self.get_memories(memory_ids)

    async def query_by_subdomain(
        self,
        subdomain_id: str,
        limit: int = 100,
    ) -> list[Memory]:
        """Query memories by subdomain."""
        memory_ids = list(self._subdomain_index.get(subdomain_id, set()))[:limit]
        return await self.get_memories(memory_ids)

    async def query_by_user(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[Memory]:
        """Query memories by user."""
        memory_ids = list(self._user_index.get(user_id, set()))[:limit]
        return await self.get_memories(memory_ids)

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory and update indexes."""
        memory = self._memories.pop(memory_id, None)
        if not memory:
            return False

        # Update indexes
        self._user_index[memory.user_id].discard(memory_id)
        self._type_index[memory.memory_type].discard(memory_id)

        if memory.primary_domain_id:
            self._domain_index[memory.primary_domain_id].discard(memory_id)
        if memory.primary_subdomain_id:
            self._subdomain_index[memory.primary_subdomain_id].discard(memory_id)

        for pointer in memory.domain_pointers:
            if pointer.target_type == "domain":
                self._domain_index[pointer.target_id].discard(memory_id)
            elif pointer.target_type == "subdomain":
                self._subdomain_index[pointer.target_id].discard(memory_id)

        return True

    async def get_memory_count(self) -> int:
        """Get total memory count."""
        return len(self._memories)


# =============================================================================
# SHARDED MEMORY STORE
# =============================================================================

@dataclass
class ShardedMemoryStoreConfig:
    """Configuration for the sharded memory store."""
    num_shards: int = 16
    enable_caching: bool = True
    cache_ttl_seconds: int = 300
    batch_size: int = 100


class ShardedMemoryStore:
    """
    Main interface for the sharded memory system.

    Coordinates:
    - Shard routing
    - Per-shard storage
    - Domain/subdomain management
    - Graph edge management
    """

    def __init__(
        self,
        config: ShardedMemoryStoreConfig | None = None,
        shard_router: ShardRouter | None = None,
    ):
        self._config = config or ShardedMemoryStoreConfig()
        self._router = shard_router or ShardRouter()
        self._hierarchy_manager = DomainHierarchyManager()

        # Initialize shard storage (in-memory for now)
        self._shards: dict[int, MemoryShardStorage] = {}
        for shard_id in range(self._config.num_shards):
            self._shards[shard_id] = InMemoryShardStorage(shard_id)

        # Domain/subdomain storage (global, not sharded)
        self._domains: dict[str, Domain] = {}
        self._subdomains: dict[str, Subdomain] = {}

        # Edge storage (adjacency tables)
        self._domain_edges: dict[str, DomainEdge] = {}
        self._memory_domain_edges: dict[str, MemoryDomainEdge] = {}

        # Index: memory_id -> list of edge IDs
        self._memory_edge_index: dict[str, list[str]] = defaultdict(list)

    # =========================================================================
    # USER INITIALIZATION
    # =========================================================================

    async def initialize_user(self, user_id: str) -> dict[str, Any]:
        """
        Initialize the complete domain structure for a new user.

        Creates all domains, subdomains, and cross-domain edges.

        Args:
            user_id: The user identifier.

        Returns:
            Statistics about created structures.
        """
        # Create domains
        domains = self._hierarchy_manager.create_domains_for_user(user_id)
        for domain in domains:
            self._domains[domain.domain_id] = domain

        # Create subdomains
        subdomains = self._hierarchy_manager.create_subdomains_for_user(user_id)
        for subdomain in subdomains:
            self._subdomains[subdomain.subdomain_id] = subdomain

            # Add subdomain to parent domain
            domain = self._domains.get(subdomain.parent_domain_id)
            if domain:
                domain.add_subdomain(subdomain.subdomain_id)

        # Create domain edges
        edges = self._hierarchy_manager.create_domain_edges(user_id)
        for edge in edges:
            self._domain_edges[edge.edge_id] = edge

        return {
            "user_id": user_id,
            "domains_created": len(domains),
            "subdomains_created": len(subdomains),
            "domain_edges_created": len(edges),
        }

    # =========================================================================
    # MEMORY OPERATIONS
    # =========================================================================

    async def store_memory(
        self,
        user_id: str,
        content: str,
        memory_type: MemoryType,
        primary_subdomain_type: SubdomainType,
        additional_subdomains: list[SubdomainType] | None = None,
        importance_score: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        """
        Store a new memory with automatic domain linking.

        This is the primary API for ingesting memories. It:
        1. Creates the memory object
        2. Links to primary and additional subdomains via hash pointers
        3. Routes to the correct shard
        4. Updates all indexes

        Args:
            user_id: User identifier.
            content: Memory content text.
            memory_type: Type of memory.
            primary_subdomain_type: Primary subdomain for this memory.
            additional_subdomains: Additional subdomains to link.
            importance_score: Importance score (0-1).
            metadata: Additional metadata.

        Returns:
            The stored Memory object.
        """
        # Get primary domain type
        primary_domain_type = SUBDOMAIN_TO_DOMAIN.get(primary_subdomain_type)
        if not primary_domain_type:
            raise ValueError(f"Unknown subdomain type: {primary_subdomain_type}")

        # Construct IDs
        primary_domain_id = f"{user_id}:domain:{primary_domain_type.value}"
        primary_subdomain_id = f"{user_id}:subdomain:{primary_subdomain_type.value}"

        # Create memory
        memory = Memory(
            memory_id=f"{user_id}:mem:{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            primary_domain_id=primary_domain_id,
            primary_subdomain_id=primary_subdomain_id,
            importance_score=importance_score,
            metadata=metadata or {},
        )

        # Create hash pointers for primary subdomain
        primary_subdomain = self._subdomains.get(primary_subdomain_id)
        if primary_subdomain:
            pointer = HashPointer(
                pointer_id=f"{memory.memory_id}->sub:{primary_subdomain_id}",
                source_type="memory",
                source_id=memory.memory_id,
                target_type="subdomain",
                target_id=primary_subdomain_id,
                target_hash=primary_subdomain.compute_hash(),
                relation_type="belongs_to",
                weight=1.0,
            )
            memory.add_domain_pointer(pointer)

        # Create hash pointers for primary domain
        primary_domain = self._domains.get(primary_domain_id)
        if primary_domain:
            pointer = HashPointer(
                pointer_id=f"{memory.memory_id}->dom:{primary_domain_id}",
                source_type="memory",
                source_id=memory.memory_id,
                target_type="domain",
                target_id=primary_domain_id,
                target_hash=primary_domain.compute_hash(),
                relation_type="belongs_to",
                weight=1.0,
            )
            memory.add_domain_pointer(pointer)

        # Add additional subdomain pointers
        if additional_subdomains:
            for subdomain_type in additional_subdomains:
                additional_domain_type = SUBDOMAIN_TO_DOMAIN.get(subdomain_type)
                if additional_domain_type:
                    subdomain_id = f"{user_id}:subdomain:{subdomain_type.value}"
                    subdomain = self._subdomains.get(subdomain_id)
                    if subdomain:
                        pointer = HashPointer(
                            pointer_id=f"{memory.memory_id}->sub:{subdomain_id}",
                            source_type="memory",
                            source_id=memory.memory_id,
                            target_type="subdomain",
                            target_id=subdomain_id,
                            target_hash=subdomain.compute_hash(),
                            relation_type="references",
                            weight=0.7,
                        )
                        memory.add_domain_pointer(pointer)

        # Route to shard and store
        shard_id, shard_info = self._router.route_write(memory)
        storage = self._shards[shard_id]
        await storage.store_memory(memory)

        # Create memory-domain edges for fast lookup
        await self._create_memory_edges(memory)

        # Update subdomain statistics
        if primary_subdomain:
            primary_subdomain.memory_count += 1
            primary_subdomain.last_memory_at = datetime.now(timezone.utc)

        if primary_domain:
            primary_domain.total_memories += 1
            primary_domain.last_activity_at = datetime.now(timezone.utc)

        logger.debug(
            f"Stored memory {memory.memory_id} in shard {shard_id}, "
            f"linked to {len(memory.domain_pointers)} domains/subdomains"
        )

        return memory

    async def _create_memory_edges(self, memory: Memory) -> None:
        """Create adjacency table entries for a memory."""
        for pointer in memory.domain_pointers:
            edge_id = f"edge:{memory.memory_id}:{pointer.target_id}"

            if pointer.target_type == "domain":
                edge = MemoryDomainEdge(
                    edge_id=edge_id,
                    memory_id=memory.memory_id,
                    domain_id=pointer.target_id,
                    subdomain_id=None,
                    weight=pointer.weight,
                    is_primary=(pointer.target_id == memory.primary_domain_id),
                    shard_id=memory.shard_id,
                )
            else:  # subdomain
                # Extract domain from subdomain
                subdomain = self._subdomains.get(pointer.target_id)
                domain_id = subdomain.parent_domain_id if subdomain else None

                edge = MemoryDomainEdge(
                    edge_id=edge_id,
                    memory_id=memory.memory_id,
                    domain_id=domain_id,
                    subdomain_id=pointer.target_id,
                    weight=pointer.weight,
                    is_primary=(pointer.target_id == memory.primary_subdomain_id),
                    shard_id=memory.shard_id,
                )

            self._memory_domain_edges[edge_id] = edge
            self._memory_edge_index[memory.memory_id].append(edge_id)

    async def get_memory(self, memory_id: str) -> Memory | None:
        """Get a memory by ID, searching across shards if needed."""
        # Check memory edge index for shard hint
        edge_ids = self._memory_edge_index.get(memory_id, [])
        if edge_ids:
            edge = self._memory_domain_edges.get(edge_ids[0])
            if edge and edge.shard_id:
                storage = self._shards.get(int(edge.shard_id))
                if storage:
                    return await storage.get_memory(memory_id)

        # Fallback: search all shards (expensive)
        for storage in self._shards.values():
            memory = await storage.get_memory(memory_id)
            if memory:
                return memory

        return None

    async def get_memories_by_subdomain(
        self,
        user_id: str,
        subdomain_type: SubdomainType,
        limit: int = 50,
    ) -> list[Memory]:
        """Get memories for a specific subdomain."""
        subdomain_id = f"{user_id}:subdomain:{subdomain_type.value}"
        domain_type = SUBDOMAIN_TO_DOMAIN.get(subdomain_type)

        if not domain_type:
            return []

        # Get the shard for this subdomain
        shard_id = self._router._hash_ring.get_shard_from_components(
            user_id, domain_type, subdomain_type
        )
        storage = self._shards.get(shard_id)

        if not storage:
            return []

        return await storage.query_by_subdomain(subdomain_id, limit)

    async def get_memories_by_domain(
        self,
        user_id: str,
        domain_type: DomainType,
        limit: int = 50,
    ) -> list[Memory]:
        """Get memories for a specific domain."""
        domain_id = f"{user_id}:domain:{domain_type.value}"

        # Get the shard for this domain
        shard_id = self._router._hash_ring.get_shard_from_components(
            user_id, domain_type
        )
        storage = self._shards.get(shard_id)

        if not storage:
            return []

        return await storage.query_by_domain(domain_id, limit)

    # =========================================================================
    # GRAPH TRAVERSAL
    # =========================================================================

    async def get_related_subdomains(
        self,
        user_id: str,
        subdomain_type: SubdomainType,
    ) -> list[Subdomain]:
        """Get subdomains related to the given subdomain via domain edges."""
        subdomain_id = f"{user_id}:subdomain:{subdomain_type.value}"
        subdomain = self._subdomains.get(subdomain_id)

        if not subdomain:
            return []

        # Get parent domain
        domain = self._domains.get(subdomain.parent_domain_id)
        if not domain:
            return []

        # Get related domains
        related_subdomains = []
        for pointer in domain.related_domain_pointers:
            related_domain = self._domains.get(pointer.target_id)
            if related_domain:
                # Get subdomains of related domain
                for related_subdomain_id in related_domain.subdomain_ids:
                    related_subdomain = self._subdomains.get(related_subdomain_id)
                    if related_subdomain:
                        related_subdomains.append(related_subdomain)

        return related_subdomains

    async def get_domain_edges(
        self,
        user_id: str,
        domain_type: DomainType,
    ) -> list[DomainEdge]:
        """Get all edges for a domain."""
        domain_id = f"{user_id}:domain:{domain_type.value}"

        edges = []
        for edge in self._domain_edges.values():
            if edge.source_domain_id == domain_id or (
                edge.bidirectional and edge.target_domain_id == domain_id
            ):
                edges.append(edge)

        return edges

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_statistics(self, user_id: str | None = None) -> dict[str, Any]:
        """Get statistics for the memory store."""
        total_memories = 0
        for storage in self._shards.values():
            total_memories += await storage.get_memory_count()

        return {
            "total_shards": len(self._shards),
            "total_memories": total_memories,
            "total_domains": len(self._domains),
            "total_subdomains": len(self._subdomains),
            "total_domain_edges": len(self._domain_edges),
            "total_memory_edges": len(self._memory_domain_edges),
            "router_stats": self._router.get_statistics(),
        }

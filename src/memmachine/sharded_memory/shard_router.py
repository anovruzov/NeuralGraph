"""
Shard Router for Memory Distribution.

Implements consistent hashing for distributing memories across shards
based on the composite key: hash(user_id, domain, subdomain).

Sharding Strategy:
==================

1. HASH KEY FORMULA:
   shard_key = SHA256(user_id + ":" + domain_type + ":" + subdomain_type)
   shard_id = hash_ring.get_shard(shard_key)

2. WHY THIS KEY?
   - user_id: Ensures user's data is localized (data locality)
   - domain_type: Groups related memories together (query efficiency)
   - subdomain_type: Fine-grained distribution (load balancing)

3. CONSISTENT HASHING:
   - Uses a hash ring with virtual nodes for even distribution
   - Adding/removing shards only redistributes ~1/N of data
   - Virtual nodes prevent hotspots from non-uniform key distribution

4. SHARD NAMING:
   - Format: "shard_{shard_id}" (e.g., "shard_0", "shard_15")
   - Each shard can be a separate database/table/collection
   - Supports both horizontal partitioning (same schema) and
     vertical partitioning (domain-specific schemas)

ROUTING EXAMPLES:
=================

Query: "What are my coding preferences?"
  → domain=PREFERENCES, subdomain=CODE_PREFERENCES
  → shard_key = hash("user123:preferences:code_preferences")
  → Routes to shard_7

Query: "What goals did I set last week?"
  → domain=GOALS, subdomain=SHORT_TERM_GOALS
  → shard_key = hash("user123:goals:short_term_goals")
  → Routes to shard_3

Query: "Current mood?" (cross-domain, needs multiple shards)
  → Primary: CURRENT_DATA.CURRENT_MOOD → shard_12
  → Follow pointers to: PERSONALITY.EMOTIONAL_BASELINE → shard_5
"""

import bisect
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .data_types import (
    DomainType,
    Memory,
    ShardConfig,
    ShardInfo,
    ShardKey,
    SubdomainType,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CONSISTENT HASH RING
# =============================================================================

class ConsistentHashRing:
    """
    Consistent hash ring for shard distribution.

    Uses virtual nodes to ensure even distribution of keys across shards.
    When a shard is added/removed, only ~1/N keys need to be remapped.
    """

    def __init__(
        self,
        num_shards: int = 16,
        virtual_nodes_per_shard: int = 150,
    ):
        """
        Initialize the hash ring.

        Args:
            num_shards: Number of physical shards.
            virtual_nodes_per_shard: Virtual nodes per shard for distribution.
        """
        self._num_shards = num_shards
        self._virtual_nodes = virtual_nodes_per_shard
        self._ring: list[tuple[int, int]] = []  # (hash_position, shard_id)
        self._ring_positions: list[int] = []     # Sorted list for binary search

        self._build_ring()

    def _build_ring(self) -> None:
        """Build the hash ring with virtual nodes."""
        self._ring = []

        for shard_id in range(self._num_shards):
            for vnode in range(self._virtual_nodes):
                # Create unique key for this virtual node
                key = f"shard_{shard_id}_vnode_{vnode}"
                position = self._hash_to_int(key)
                self._ring.append((position, shard_id))

        # Sort by position for binary search
        self._ring.sort(key=lambda x: x[0])
        self._ring_positions = [pos for pos, _ in self._ring]

    def _hash_to_int(self, key: str) -> int:
        """Convert a key to an integer position on the ring."""
        hash_bytes = hashlib.sha256(key.encode()).digest()
        # Use first 8 bytes as 64-bit integer
        return int.from_bytes(hash_bytes[:8], byteorder='big')

    def get_shard(self, key: str) -> int:
        """
        Get the shard ID for a given key.

        Args:
            key: The key to route (typically the shard_key hash).

        Returns:
            Shard ID (0 to num_shards-1).
        """
        if not self._ring:
            return 0

        position = self._hash_to_int(key)

        # Binary search for the first position >= key position
        idx = bisect.bisect_left(self._ring_positions, position)

        # Wrap around if past end of ring
        if idx >= len(self._ring):
            idx = 0

        return self._ring[idx][1]

    def get_shard_from_components(
        self,
        user_id: str,
        domain_type: DomainType,
        subdomain_type: SubdomainType | None = None,
    ) -> int:
        """
        Get shard ID from key components.

        Args:
            user_id: User identifier.
            domain_type: Domain type.
            subdomain_type: Optional subdomain type.

        Returns:
            Shard ID.
        """
        key = ShardKey(
            user_id=user_id,
            domain_type=domain_type,
            subdomain_type=subdomain_type,
        )
        return self.get_shard(key.compute_hash())

    def get_all_shards_for_user(self, user_id: str) -> list[int]:
        """
        Get all possible shards for a user.

        Useful for user-wide queries that span all domains.

        Args:
            user_id: User identifier.

        Returns:
            List of shard IDs that may contain user's data.
        """
        shards = set()
        for domain_type in DomainType:
            shard_id = self.get_shard_from_components(user_id, domain_type)
            shards.add(shard_id)
        return sorted(shards)

    def get_shards_for_domains(
        self,
        user_id: str,
        domain_types: list[DomainType],
    ) -> list[int]:
        """
        Get shards for specific domains.

        Args:
            user_id: User identifier.
            domain_types: List of domain types to query.

        Returns:
            List of shard IDs.
        """
        shards = set()
        for domain_type in domain_types:
            shard_id = self.get_shard_from_components(user_id, domain_type)
            shards.add(shard_id)
        return sorted(shards)

    @property
    def num_shards(self) -> int:
        """Get number of shards."""
        return self._num_shards

    def get_ring_stats(self) -> dict[str, Any]:
        """Get statistics about the hash ring."""
        shard_counts = {}
        for _, shard_id in self._ring:
            shard_counts[shard_id] = shard_counts.get(shard_id, 0) + 1

        return {
            "num_shards": self._num_shards,
            "virtual_nodes_per_shard": self._virtual_nodes,
            "total_ring_positions": len(self._ring),
            "positions_per_shard": shard_counts,
        }


# =============================================================================
# SHARD ROUTER
# =============================================================================

class ShardRouter:
    """
    Routes memory operations to the correct shard.

    Provides the main interface for:
    - Determining which shard a memory belongs to
    - Routing read/write operations
    - Managing shard metadata
    """

    def __init__(self, config: ShardConfig | None = None):
        """
        Initialize the shard router.

        Args:
            config: Shard configuration. Uses defaults if None.
        """
        self._config = config or ShardConfig()
        self._hash_ring = ConsistentHashRing(
            num_shards=self._config.num_shards,
            virtual_nodes_per_shard=self._config.virtual_nodes_per_shard,
        )

        # Shard metadata
        self._shards: dict[int, ShardInfo] = {}
        self._initialize_shards()

    def _initialize_shards(self) -> None:
        """Initialize shard metadata."""
        for shard_id in range(self._config.num_shards):
            self._shards[shard_id] = ShardInfo(
                shard_id=shard_id,
                shard_name=f"shard_{shard_id}",
                max_memories=self._config.default_max_memories_per_shard,
                max_size_bytes=self._config.default_max_size_per_shard,
            )

    # =========================================================================
    # SHARD KEY COMPUTATION
    # =========================================================================

    def compute_shard_key(
        self,
        user_id: str,
        domain_type: DomainType,
        subdomain_type: SubdomainType | None = None,
    ) -> ShardKey:
        """
        Compute the shard key for given components.

        The shard key formula is:
            SHA256(user_id + ":" + domain_type + [":" + subdomain_type])

        Args:
            user_id: User identifier.
            domain_type: Domain type.
            subdomain_type: Optional subdomain type for finer granularity.

        Returns:
            ShardKey with computed hash.
        """
        return ShardKey(
            user_id=user_id,
            domain_type=domain_type,
            subdomain_type=subdomain_type,
        )

    def get_shard_for_memory(self, memory: Memory) -> int:
        """
        Get the shard ID for a memory.

        Uses the memory's primary domain for routing.

        Args:
            memory: The memory to route.

        Returns:
            Shard ID.
        """
        if memory.shard_id is not None:
            return int(memory.shard_id)

        # Use primary domain if set
        if memory.primary_domain_id:
            # Extract domain type from domain_id
            # Format: "user_id:domain:domain_type"
            parts = memory.primary_domain_id.split(":")
            if len(parts) >= 3:
                try:
                    domain_type = DomainType(parts[2])
                    subdomain_type = None
                    if memory.primary_subdomain_id:
                        subdomain_parts = memory.primary_subdomain_id.split(":")
                        if len(subdomain_parts) >= 3:
                            subdomain_type = SubdomainType(subdomain_parts[2])

                    return self._hash_ring.get_shard_from_components(
                        memory.user_id,
                        domain_type,
                        subdomain_type,
                    )
                except ValueError:
                    pass

        # Fallback: hash the memory_id directly
        return self._hash_ring.get_shard(memory.memory_id)

    def get_shard_for_key(self, shard_key: ShardKey) -> int:
        """
        Get the shard ID for a shard key.

        Args:
            shard_key: The shard key.

        Returns:
            Shard ID.
        """
        return self._hash_ring.get_shard(shard_key.compute_hash())

    # =========================================================================
    # ROUTING OPERATIONS
    # =========================================================================

    def route_write(
        self,
        memory: Memory,
    ) -> tuple[int, ShardInfo]:
        """
        Route a write operation to the appropriate shard.

        Args:
            memory: Memory to write.

        Returns:
            Tuple of (shard_id, shard_info).
        """
        shard_id = self.get_shard_for_memory(memory)
        shard_info = self._shards[shard_id]

        # Update memory with shard assignment
        memory.shard_id = str(shard_id)

        # Check shard capacity
        if shard_info.is_full:
            logger.warning(f"Shard {shard_id} is at capacity")
            # In production, would trigger rebalancing or use overflow shard

        # Update shard stats
        shard_info.memory_count += 1
        shard_info.last_write_at = datetime.now(timezone.utc)

        return shard_id, shard_info

    def route_read(
        self,
        user_id: str,
        domain_types: list[DomainType] | None = None,
        subdomain_types: list[SubdomainType] | None = None,
    ) -> list[tuple[int, ShardInfo]]:
        """
        Route a read operation to appropriate shards.

        Args:
            user_id: User identifier.
            domain_types: Optional list of domains to query.
            subdomain_types: Optional list of subdomains to query.

        Returns:
            List of (shard_id, shard_info) tuples to query.
        """
        shards_to_query = set()

        if subdomain_types:
            # Query specific subdomains
            from .domain_hierarchy import SUBDOMAIN_TO_DOMAIN
            for subdomain_type in subdomain_types:
                domain_type = SUBDOMAIN_TO_DOMAIN.get(subdomain_type)
                if domain_type:
                    shard_id = self._hash_ring.get_shard_from_components(
                        user_id, domain_type, subdomain_type
                    )
                    shards_to_query.add(shard_id)

        elif domain_types:
            # Query specific domains
            for domain_type in domain_types:
                shard_id = self._hash_ring.get_shard_from_components(
                    user_id, domain_type
                )
                shards_to_query.add(shard_id)

        else:
            # Query all shards for user
            shards_to_query = set(
                self._hash_ring.get_all_shards_for_user(user_id)
            )

        result = []
        for shard_id in sorted(shards_to_query):
            shard_info = self._shards[shard_id]
            shard_info.last_read_at = datetime.now(timezone.utc)
            result.append((shard_id, shard_info))

        return result

    # =========================================================================
    # SHARD MANAGEMENT
    # =========================================================================

    def get_shard_info(self, shard_id: int) -> ShardInfo | None:
        """Get information about a specific shard."""
        return self._shards.get(shard_id)

    def get_all_shards(self) -> list[ShardInfo]:
        """Get information about all shards."""
        return list(self._shards.values())

    def get_active_shards(self) -> list[ShardInfo]:
        """Get all active (writable) shards."""
        return [s for s in self._shards.values() if s.is_active and not s.is_read_only]

    def update_shard_stats(
        self,
        shard_id: int,
        memory_count: int | None = None,
        size_bytes: int | None = None,
    ) -> None:
        """Update statistics for a shard."""
        if shard_id not in self._shards:
            return

        shard = self._shards[shard_id]
        if memory_count is not None:
            shard.memory_count = memory_count
        if size_bytes is not None:
            shard.size_bytes = size_bytes

    # =========================================================================
    # REBALANCING (Placeholder for production)
    # =========================================================================

    def get_rebalance_plan(self) -> list[dict[str, Any]]:
        """
        Generate a rebalancing plan for overloaded shards.

        Returns:
            List of rebalance operations to perform.
        """
        # Find overloaded shards
        overloaded = [s for s in self._shards.values() if s.utilization > 0.8]

        if not overloaded:
            return []

        # Find underutilized shards
        underutilized = [s for s in self._shards.values() if s.utilization < 0.5]

        plan = []
        for source in overloaded:
            for target in underutilized:
                if target.shard_id != source.shard_id:
                    plan.append({
                        "action": "migrate",
                        "source_shard": source.shard_id,
                        "target_shard": target.shard_id,
                        "estimated_memories": int(source.memory_count * 0.2),
                    })
                    break

        return plan

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_statistics(self) -> dict[str, Any]:
        """Get overall sharding statistics."""
        total_memories = sum(s.memory_count for s in self._shards.values())
        total_size = sum(s.size_bytes for s in self._shards.values())
        active_shards = len([s for s in self._shards.values() if s.is_active])

        utilizations = [s.utilization for s in self._shards.values()]
        avg_utilization = sum(utilizations) / len(utilizations) if utilizations else 0

        return {
            "num_shards": self._config.num_shards,
            "active_shards": active_shards,
            "total_memories": total_memories,
            "total_size_bytes": total_size,
            "average_utilization": avg_utilization,
            "shard_distribution": {
                s.shard_id: s.memory_count for s in self._shards.values()
            },
            "hash_ring_stats": self._hash_ring.get_ring_stats(),
        }

"""
Sharded Memory Module with Hash Pointers and Knowledge Graph.

This module implements a sophisticated memory architecture with:
1. Hash-based sharding for horizontal scaling
2. Hash pointers for cross-domain references
3. Hierarchical domain model
4. Knowledge graph-style traversal for intelligent retrieval

Architecture Overview:
    ┌─────────────────────────────────────────────────────────────────┐
    │                      ShardRouter                                │
    │  hash(user_id, domain, subdomain) → shard_id                   │
    └─────────────────────────────────────────────────────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
    ┌─────────────┐         ┌─────────────┐         ┌─────────────┐
    │   Shard 0   │         │   Shard 1   │         │   Shard N   │
    │  Memories   │         │  Memories   │         │  Memories   │
    └─────────────┘         └─────────────┘         └─────────────┘
           │                       │                       │
           └───────────────────────┴───────────────────────┘
                                   │
    ┌─────────────────────────────────────────────────────────────────┐
    │                   Domain Graph Layer                            │
    │  Domains ↔ Subdomains ↔ Memories (via hash pointers)           │
    └─────────────────────────────────────────────────────────────────┘
"""

from .data_types import (
    # Core types
    Domain,
    Subdomain,
    DomainType,
    SubdomainType,
    Memory,
    MemoryType,
    HashPointer,
    # Edges
    DomainEdge,
    MemoryDomainEdge,
    # Shard types
    ShardKey,
    ShardInfo,
    ShardConfig,
    # Retrieval
    RetrievalQuery,
    RetrievalResult,
    TraversalPath,
)

from .domain_hierarchy import (
    DOMAIN_HIERARCHY,
    DomainHierarchyManager,
    get_domain_by_type,
    get_subdomains_for_domain,
)

from .shard_router import (
    ShardRouter,
    ConsistentHashRing,
)

from .memory_store import (
    ShardedMemoryStore,
    MemoryShardStorage,
)

from .graph_retriever import (
    GraphRetriever,
    RetrievalConfig,
)

__all__ = [
    # Data types
    "Domain",
    "Subdomain",
    "DomainType",
    "SubdomainType",
    "Memory",
    "MemoryType",
    "HashPointer",
    "DomainEdge",
    "MemoryDomainEdge",
    "ShardKey",
    "ShardInfo",
    "ShardConfig",
    "RetrievalQuery",
    "RetrievalResult",
    "TraversalPath",
    # Domain hierarchy
    "DOMAIN_HIERARCHY",
    "DomainHierarchyManager",
    "get_domain_by_type",
    "get_subdomains_for_domain",
    # Shard router
    "ShardRouter",
    "ConsistentHashRing",
    # Memory store
    "ShardedMemoryStore",
    "MemoryShardStorage",
    # Graph retriever
    "GraphRetriever",
    "RetrievalConfig",
]

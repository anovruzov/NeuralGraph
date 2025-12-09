"""
Data types for sharded memory with hash pointers and knowledge graph.

This module defines the complete data model for:
- Domains and Subdomains (hierarchical taxonomy)
- Memories (the actual stored content)
- Hash Pointers (edges linking memories to domains)
- Sharding infrastructure
- Retrieval structures
"""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# =============================================================================
# DOMAIN TYPES
# =============================================================================

class DomainType(str, Enum):
    """Top-level domain categories for memory organization."""

    # Core personality and mental state
    PERSONALITY = "personality"

    # User preferences
    PREFERENCES = "preferences"

    # Life advice / coaching
    LIFE_ADVICE = "life_advice"

    # Goals and objectives
    GOALS = "goals"

    # Current/recent data (last ~30 days)
    CURRENT_DATA = "current_data"

    # Relationships and social graph
    RELATIONSHIPS = "relationships"

    # Knowledge and expertise
    KNOWLEDGE = "knowledge"

    # Temporal/historical data
    HISTORICAL = "historical"


class SubdomainType(str, Enum):
    """Subdomain categories within each domain."""

    # Personality subdomains
    TYPING_STYLE = "typing_style"
    PERSONALITY_TRAITS = "personality_traits"
    MENTAL_HEALTH_SIGNALS = "mental_health_signals"
    COMMUNICATION_PATTERNS = "communication_patterns"
    EMOTIONAL_BASELINE = "emotional_baseline"

    # Preferences subdomains
    CODE_PREFERENCES = "code_preferences"
    MUSIC_PREFERENCES = "music_preferences"
    ENVIRONMENTAL_PREFERENCES = "environmental_preferences"
    CONTENT_PREFERENCES = "content_preferences"
    WORKFLOW_PREFERENCES = "workflow_preferences"
    FOOD_PREFERENCES = "food_preferences"
    AESTHETIC_PREFERENCES = "aesthetic_preferences"

    # Life advice subdomains
    ADVICE_GIVEN = "advice_given"
    STRENGTHS = "strengths"
    WEAKNESSES = "weaknesses"
    RESILIENCE_PATTERNS = "resilience_patterns"
    GROWTH_AREAS = "growth_areas"
    COPING_MECHANISMS = "coping_mechanisms"

    # Goals subdomains
    SHORT_TERM_GOALS = "short_term_goals"
    LONG_TERM_GOALS = "long_term_goals"
    DEADLINES = "deadlines"
    MILESTONES = "milestones"
    ASPIRATIONS = "aspirations"

    # Current data subdomains
    RECENT_TOPICS = "recent_topics"
    ACTIVE_PROJECTS = "active_projects"
    CURRENT_MOOD = "current_mood"
    PENDING_TASKS = "pending_tasks"
    RECENT_INTERACTIONS = "recent_interactions"

    # Relationships subdomains
    FAMILY = "family"
    FRIENDS = "friends"
    COLLEAGUES = "colleagues"
    ACQUAINTANCES = "acquaintances"
    PETS = "pets"

    # Knowledge subdomains
    EXPERTISE_AREAS = "expertise_areas"
    LEARNING_INTERESTS = "learning_interests"
    EDUCATION = "education"
    SKILLS = "skills"

    # Historical subdomains
    LIFE_EVENTS = "life_events"
    PAST_EXPERIENCES = "past_experiences"
    MEMORIES = "memories"


class MemoryType(str, Enum):
    """Types of memory content."""

    FACT = "fact"               # Declarative fact about user
    PREFERENCE = "preference"   # User preference/choice
    EVENT = "event"             # Something that happened
    INSIGHT = "insight"         # Derived insight about user
    QUOTE = "quote"             # Direct quote from user
    INTERACTION = "interaction" # Record of interaction
    GOAL = "goal"               # Goal or objective
    ADVICE = "advice"           # Advice given to user
    EMOTION = "emotion"         # Emotional state/event


# =============================================================================
# HASH POINTER
# =============================================================================

@dataclass
class HashPointer:
    """
    A hash pointer linking entities in the memory graph.

    Hash pointers are the fundamental edge type that connect:
    - Memories → Domains
    - Memories → Subdomains
    - Domains → Domains (cross-references)
    - Subdomains → Subdomains

    The pointer contains the target's ID plus a hash for verification.
    """

    pointer_id: str                  # Unique ID for this pointer
    source_type: str                 # "memory", "domain", "subdomain"
    source_id: str                   # ID of the source entity
    target_type: str                 # "memory", "domain", "subdomain"
    target_id: str                   # ID of the target entity
    target_hash: str                 # SHA-256 hash of target for verification

    # Pointer metadata
    relation_type: str = "belongs_to"  # Type of relationship
    weight: float = 1.0                # Strength of connection (0-1)
    confidence: float = 1.0            # Confidence in this link
    bidirectional: bool = False        # Whether edge goes both ways

    # Temporal
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0

    def verify(self, target_content: str) -> bool:
        """Verify the hash pointer against target content."""
        computed_hash = hashlib.sha256(target_content.encode()).hexdigest()
        return computed_hash == self.target_hash

    def touch(self) -> None:
        """Update access time and count."""
        self.last_accessed = datetime.now(timezone.utc)
        self.access_count += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pointer_id": self.pointer_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_hash": self.target_hash,
            "relation_type": self.relation_type,
            "weight": self.weight,
            "confidence": self.confidence,
            "bidirectional": self.bidirectional,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
        }


# =============================================================================
# DOMAIN AND SUBDOMAIN
# =============================================================================

@dataclass
class Subdomain:
    """
    A subdomain within a domain.

    Subdomains are the leaf nodes in the domain hierarchy that
    memories actually link to. Each subdomain belongs to exactly
    one parent domain.
    """

    subdomain_id: str                      # Unique identifier
    subdomain_type: SubdomainType          # Type enum
    parent_domain_id: str                  # Parent domain pointer
    user_id: str                           # Owner user

    # Subdomain metadata
    name: str = ""                         # Human-readable name
    description: str = ""                  # Description
    icon: str = ""                         # UI icon

    # Statistics
    memory_count: int = 0                  # Memories in this subdomain
    last_memory_at: datetime | None = None # Last memory added
    total_accesses: int = 0                # Query accesses

    # Cross-references (hash pointers to other subdomains)
    related_subdomain_pointers: list[HashPointer] = field(default_factory=list)

    # Temporal
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_related_subdomain(self, pointer: HashPointer) -> None:
        """Add a cross-reference to another subdomain."""
        self.related_subdomain_pointers.append(pointer)
        self.updated_at = datetime.now(timezone.utc)

    def get_content_for_hash(self) -> str:
        """Get content string for hash verification."""
        return f"{self.subdomain_id}:{self.subdomain_type.value}:{self.parent_domain_id}"

    def compute_hash(self) -> str:
        """Compute hash of this subdomain."""
        return hashlib.sha256(self.get_content_for_hash().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "subdomain_id": self.subdomain_id,
            "subdomain_type": self.subdomain_type.value,
            "parent_domain_id": self.parent_domain_id,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "memory_count": self.memory_count,
            "last_memory_at": self.last_memory_at.isoformat() if self.last_memory_at else None,
            "total_accesses": self.total_accesses,
            "related_subdomain_pointers": [p.to_dict() for p in self.related_subdomain_pointers],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class Domain:
    """
    A top-level domain in the memory hierarchy.

    Domains are the primary organizational units. Each domain contains
    multiple subdomains and can have cross-references to other domains.
    """

    domain_id: str                         # Unique identifier
    domain_type: DomainType                # Type enum
    user_id: str                           # Owner user

    # Domain metadata
    name: str = ""                         # Human-readable name
    description: str = ""                  # Description
    icon: str = ""                         # UI icon
    color: str = ""                        # UI color

    # Child subdomains
    subdomain_ids: list[str] = field(default_factory=list)

    # Statistics
    total_memories: int = 0                # Total memories across subdomains
    last_activity_at: datetime | None = None

    # Cross-references (hash pointers to other domains)
    related_domain_pointers: list[HashPointer] = field(default_factory=list)

    # Shard hint (primary shard for this domain)
    primary_shard_id: str | None = None

    # Temporal
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_subdomain(self, subdomain_id: str) -> None:
        """Add a subdomain to this domain."""
        if subdomain_id not in self.subdomain_ids:
            self.subdomain_ids.append(subdomain_id)
            self.updated_at = datetime.now(timezone.utc)

    def add_related_domain(self, pointer: HashPointer) -> None:
        """Add a cross-reference to another domain."""
        self.related_domain_pointers.append(pointer)
        self.updated_at = datetime.now(timezone.utc)

    def get_content_for_hash(self) -> str:
        """Get content string for hash verification."""
        return f"{self.domain_id}:{self.domain_type.value}:{self.user_id}"

    def compute_hash(self) -> str:
        """Compute hash of this domain."""
        return hashlib.sha256(self.get_content_for_hash().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "domain_id": self.domain_id,
            "domain_type": self.domain_type.value,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "subdomain_ids": self.subdomain_ids,
            "total_memories": self.total_memories,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "related_domain_pointers": [p.to_dict() for p in self.related_domain_pointers],
            "primary_shard_id": self.primary_shard_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# =============================================================================
# MEMORY
# =============================================================================

@dataclass
class Memory:
    """
    A memory unit stored in the sharded system.

    Memories are the actual content units. Each memory can link to
    multiple domains and subdomains via hash pointers, enabling
    rich cross-referencing and graph traversal.
    """

    memory_id: str                         # Unique identifier
    user_id: str                           # Owner user
    content: str                           # The actual memory content
    memory_type: MemoryType                # Type of memory

    # Hash for verification
    content_hash: str = ""                 # SHA-256 of content

    # Embedding for similarity search
    embedding: list[float] | None = None

    # Domain pointers (hash pointers to domains/subdomains)
    domain_pointers: list[HashPointer] = field(default_factory=list)

    # Primary domain (for sharding - the main domain this memory belongs to)
    primary_domain_id: str | None = None
    primary_subdomain_id: str | None = None

    # Shard location
    shard_id: str | None = None

    # Source tracking
    source_episode_uid: str | None = None  # Original episode this came from
    source_message_ids: list[str] = field(default_factory=list)

    # Importance scoring
    importance_score: float = 0.5          # 0-1, higher = more important
    access_count: int = 0                  # Times accessed in queries
    decay_factor: float = 1.0              # Temporal decay (1.0 = fresh)

    # Emotional context
    emotional_valence: float = 0.0         # -1 to 1
    emotional_arousal: float = 0.0         # 0 to 1

    # Entity references (links to knowledge graph entities)
    entity_ids: list[str] = field(default_factory=list)

    # Temporal
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None     # Optional expiration

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Compute content hash after initialization."""
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()

    def add_domain_pointer(self, pointer: HashPointer) -> None:
        """Add a domain/subdomain pointer."""
        self.domain_pointers.append(pointer)
        self.updated_at = datetime.now(timezone.utc)

    def touch(self) -> None:
        """Update access statistics."""
        self.access_count += 1
        self.updated_at = datetime.now(timezone.utc)

    def get_domain_ids(self) -> list[str]:
        """Get all domain IDs this memory links to."""
        return [
            p.target_id for p in self.domain_pointers
            if p.target_type == "domain"
        ]

    def get_subdomain_ids(self) -> list[str]:
        """Get all subdomain IDs this memory links to."""
        return [
            p.target_id for p in self.domain_pointers
            if p.target_type == "subdomain"
        ]

    def compute_relevance_score(
        self,
        query_embedding: list[float] | None = None,
        time_weight: float = 0.1,
    ) -> float:
        """
        Compute relevance score for this memory.

        Combines:
        - Embedding similarity (if available)
        - Importance score
        - Recency (temporal decay)
        - Access frequency
        """
        base_score = self.importance_score

        # Apply temporal decay
        age_hours = (datetime.now(timezone.utc) - self.created_at).total_seconds() / 3600
        recency_score = 1.0 / (1.0 + time_weight * age_hours)
        base_score *= (0.7 + 0.3 * recency_score)

        # Boost frequently accessed memories
        access_boost = min(1.0, 0.5 + 0.05 * self.access_count)
        base_score *= access_boost

        # TODO: Add embedding similarity if query_embedding provided

        return min(1.0, base_score)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "memory_id": self.memory_id,
            "user_id": self.user_id,
            "content": self.content,
            "content_hash": self.content_hash,
            "memory_type": self.memory_type.value,
            "domain_pointers": [p.to_dict() for p in self.domain_pointers],
            "primary_domain_id": self.primary_domain_id,
            "primary_subdomain_id": self.primary_subdomain_id,
            "shard_id": self.shard_id,
            "source_episode_uid": self.source_episode_uid,
            "importance_score": self.importance_score,
            "access_count": self.access_count,
            "decay_factor": self.decay_factor,
            "emotional_valence": self.emotional_valence,
            "emotional_arousal": self.emotional_arousal,
            "entity_ids": self.entity_ids,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.metadata,
        }


# =============================================================================
# EDGES (Adjacency Tables)
# =============================================================================

@dataclass
class DomainEdge:
    """
    Edge between two domains in the domain graph.

    This represents cross-domain relationships like:
    - Preferences ↔ Personality
    - CurrentData → Goals
    - Goals → LifeAdvice
    """

    edge_id: str
    source_domain_id: str
    target_domain_id: str
    relation_type: str                     # e.g., "influences", "references", "derived_from"

    # Edge metadata
    weight: float = 1.0                    # Strength of relationship
    bidirectional: bool = True             # Most domain edges are bidirectional

    # Statistics
    traversal_count: int = 0               # Times this edge was traversed

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "edge_id": self.edge_id,
            "source_domain_id": self.source_domain_id,
            "target_domain_id": self.target_domain_id,
            "relation_type": self.relation_type,
            "weight": self.weight,
            "bidirectional": self.bidirectional,
            "traversal_count": self.traversal_count,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class MemoryDomainEdge:
    """
    Edge connecting a memory to a domain/subdomain.

    This is the primary edge type stored in adjacency tables
    for fast lookup of "all memories in domain X".
    """

    edge_id: str
    memory_id: str
    domain_id: str
    subdomain_id: str | None = None

    # Edge metadata
    weight: float = 1.0                    # Strength of association
    is_primary: bool = False               # Is this the primary domain?

    # Shard hint for fast lookup
    shard_id: str | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "edge_id": self.edge_id,
            "memory_id": self.memory_id,
            "domain_id": self.domain_id,
            "subdomain_id": self.subdomain_id,
            "weight": self.weight,
            "is_primary": self.is_primary,
            "shard_id": self.shard_id,
            "created_at": self.created_at.isoformat(),
        }


# =============================================================================
# SHARDING TYPES
# =============================================================================

@dataclass
class ShardKey:
    """
    The composite key used for shard routing.

    shard_id = hash(user_id + domain_type + subdomain_type)
    """

    user_id: str
    domain_type: DomainType
    subdomain_type: SubdomainType | None = None

    def compute_hash(self) -> str:
        """Compute the shard routing hash."""
        key_parts = [self.user_id, self.domain_type.value]
        if self.subdomain_type:
            key_parts.append(self.subdomain_type.value)
        key_string = ":".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()

    def get_shard_id(self, num_shards: int) -> int:
        """Get the shard ID for a given number of shards."""
        hash_bytes = bytes.fromhex(self.compute_hash())
        # Use first 8 bytes as integer
        hash_int = int.from_bytes(hash_bytes[:8], byteorder='big')
        return hash_int % num_shards

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "user_id": self.user_id,
            "domain_type": self.domain_type.value,
            "subdomain_type": self.subdomain_type.value if self.subdomain_type else None,
            "hash": self.compute_hash(),
        }


@dataclass
class ShardInfo:
    """Information about a single shard."""

    shard_id: int
    shard_name: str                        # e.g., "shard_0", "shard_personality_0"

    # Capacity and usage
    memory_count: int = 0
    max_memories: int = 1_000_000          # Soft limit
    size_bytes: int = 0
    max_size_bytes: int = 10_737_418_240   # 10GB default

    # Connection info (for distributed storage)
    host: str = "localhost"
    port: int = 5432
    database: str = "memmachine"
    table_prefix: str = ""

    # Status
    is_active: bool = True
    is_read_only: bool = False
    last_write_at: datetime | None = None
    last_read_at: datetime | None = None

    # Replication
    replica_of: int | None = None          # If this is a replica
    replicas: list[int] = field(default_factory=list)

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_full(self) -> bool:
        """Check if shard is at capacity."""
        return (
            self.memory_count >= self.max_memories or
            self.size_bytes >= self.max_size_bytes
        )

    @property
    def utilization(self) -> float:
        """Get shard utilization as percentage."""
        count_util = self.memory_count / self.max_memories
        size_util = self.size_bytes / self.max_size_bytes
        return max(count_util, size_util)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "shard_id": self.shard_id,
            "shard_name": self.shard_name,
            "memory_count": self.memory_count,
            "max_memories": self.max_memories,
            "size_bytes": self.size_bytes,
            "utilization": self.utilization,
            "is_active": self.is_active,
            "is_read_only": self.is_read_only,
            "host": self.host,
            "port": self.port,
        }


@dataclass
class ShardConfig:
    """Configuration for the sharding system."""

    # Number of virtual shards (should be power of 2 for consistent hashing)
    num_shards: int = 16

    # Replication factor
    replication_factor: int = 1

    # Hash ring virtual nodes (for consistent hashing)
    virtual_nodes_per_shard: int = 150

    # Capacity limits
    default_max_memories_per_shard: int = 1_000_000
    default_max_size_per_shard: int = 10_737_418_240  # 10GB

    # Write behavior
    write_to_replicas: bool = True
    async_replication: bool = True

    # Read behavior
    read_from_replicas: bool = True
    read_preference: str = "primary"  # "primary", "secondary", "nearest"


# =============================================================================
# RETRIEVAL TYPES
# =============================================================================

@dataclass
class RetrievalQuery:
    """A query for memory retrieval."""

    user_id: str
    query_text: str

    # Optional filters
    domain_types: list[DomainType] | None = None
    subdomain_types: list[SubdomainType] | None = None
    memory_types: list[MemoryType] | None = None

    # Time filters
    created_after: datetime | None = None
    created_before: datetime | None = None

    # Graph traversal options
    follow_domain_pointers: bool = True
    max_hops: int = 2                      # How many edges to follow
    include_related_domains: bool = True

    # Result options
    limit: int = 20
    include_embeddings: bool = False

    # Query embedding (if available)
    query_embedding: list[float] | None = None


@dataclass
class TraversalPath:
    """A path through the memory graph during retrieval."""

    path_id: str
    start_node_type: str                   # "memory", "domain", "subdomain"
    start_node_id: str
    edges: list[HashPointer] = field(default_factory=list)
    end_node_type: str = ""
    end_node_id: str = ""

    # Scoring
    path_score: float = 0.0
    hop_count: int = 0

    def add_hop(self, edge: HashPointer) -> None:
        """Add a hop to the path."""
        self.edges.append(edge)
        self.end_node_type = edge.target_type
        self.end_node_id = edge.target_id
        self.hop_count += 1
        # Decay score with each hop
        self.path_score *= 0.8

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "path_id": self.path_id,
            "start_node_type": self.start_node_type,
            "start_node_id": self.start_node_id,
            "edges": [e.to_dict() for e in self.edges],
            "end_node_type": self.end_node_type,
            "end_node_id": self.end_node_id,
            "path_score": self.path_score,
            "hop_count": self.hop_count,
        }


@dataclass
class RetrievalResult:
    """Result of a memory retrieval query."""

    query: RetrievalQuery
    memories: list[Memory]
    traversal_paths: list[TraversalPath] = field(default_factory=list)

    # Domains/subdomains accessed
    accessed_domains: list[str] = field(default_factory=list)
    accessed_subdomains: list[str] = field(default_factory=list)
    accessed_shards: list[int] = field(default_factory=list)

    # Performance
    total_time_ms: float = 0.0
    shard_lookup_time_ms: float = 0.0
    graph_traversal_time_ms: float = 0.0
    ranking_time_ms: float = 0.0

    # Statistics
    total_candidates: int = 0
    memories_from_graph: int = 0
    memories_from_direct: int = 0

    # Neural graph integration (Phase 7)
    neural_graph_time_ms: float = 0.0
    neural_nodes_retrieved: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query_text": self.query.query_text,
            "memories": [m.to_dict() for m in self.memories],
            "memory_count": len(self.memories),
            "traversal_paths": [p.to_dict() for p in self.traversal_paths],
            "accessed_domains": self.accessed_domains,
            "accessed_subdomains": self.accessed_subdomains,
            "accessed_shards": self.accessed_shards,
            "total_time_ms": self.total_time_ms,
            "total_candidates": self.total_candidates,
            "neural_graph_time_ms": self.neural_graph_time_ms,
            "neural_nodes_retrieved": self.neural_nodes_retrieved,
        }

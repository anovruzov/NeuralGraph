"""Data types for knowledge graph (MeKB architecture).

Implements entity types, graph nodes, edges, and MeKB scoring.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    """Types of entities in the knowledge graph."""

    PERSON = "person"           # People, characters, users
    ORGANIZATION = "organization"  # Companies, institutions
    LOCATION = "location"       # Places, addresses
    EVENT = "event"             # Activities, meetings, occurrences
    CONCEPT = "concept"         # Abstract ideas, topics
    OBJECT = "object"           # Physical things, products
    TIME = "time"               # Dates, periods, deadlines
    PREFERENCE = "preference"   # Likes, dislikes, preferences
    ATTRIBUTE = "attribute"     # Properties, characteristics
    ACTION = "action"           # Actions taken, behaviors


class RelationType(str, Enum):
    """Types of relationships between entities."""

    # Person relationships
    KNOWS = "knows"
    WORKS_WITH = "works_with"
    RELATED_TO = "related_to"
    FRIEND_OF = "friend_of"
    FAMILY_OF = "family_of"

    # Organizational relationships
    WORKS_FOR = "works_for"
    MEMBER_OF = "member_of"
    OWNS = "owns"
    MANAGES = "manages"

    # Location relationships
    LOCATED_IN = "located_in"
    LIVES_IN = "lives_in"
    VISITED = "visited"

    # Event relationships
    PARTICIPATED_IN = "participated_in"
    ORGANIZED = "organized"
    ATTENDED = "attended"

    # Concept relationships
    IS_A = "is_a"
    PART_OF = "part_of"
    RELATED_TO_CONCEPT = "related_to_concept"
    INTERESTED_IN = "interested_in"

    # Preference relationships
    LIKES = "likes"
    DISLIKES = "dislikes"
    PREFERS = "prefers"

    # Temporal relationships
    HAPPENED_AT = "happened_at"
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"

    # Action relationships
    PERFORMED = "performed"
    RECEIVED = "received"

    # Generic
    MENTIONS = "mentions"
    ASSOCIATED_WITH = "associated_with"


@dataclass
class Entity:
    """An entity node in the knowledge graph."""

    uid: str
    name: str
    entity_type: EntityType
    session_key: str

    # Entity attributes
    aliases: list[str] = field(default_factory=list)
    description: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    # MeKB scoring
    mention_count: int = 0       # Times entity mentioned by user
    total_mentions: int = 0      # Global mention count
    mekb_score: float = 0.0      # Computed TF-IDF score

    # Source tracking
    source_episode_uids: list[str] = field(default_factory=list)
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Emotional association (from Phase 2)
    emotional_valence: float = 0.0  # -1 to 1 (negative to positive)

    # Wikidata linking (optional)
    wikidata_id: str | None = None
    wikipedia_url: str | None = None

    def record_mention(self, episode_uid: str) -> None:
        """Record a mention of this entity."""
        self.mention_count += 1
        self.total_mentions += 1
        if episode_uid not in self.source_episode_uids:
            self.source_episode_uids.append(episode_uid)
        self.last_seen = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "uid": self.uid,
            "name": self.name,
            "entity_type": self.entity_type.value,
            "session_key": self.session_key,
            "aliases": self.aliases,
            "description": self.description,
            "attributes": self.attributes,
            "mention_count": self.mention_count,
            "total_mentions": self.total_mentions,
            "mekb_score": self.mekb_score,
            "source_episode_uids": self.source_episode_uids,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "emotional_valence": self.emotional_valence,
            "wikidata_id": self.wikidata_id,
            "wikipedia_url": self.wikipedia_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        """Create from dictionary."""
        return cls(
            uid=data["uid"],
            name=data["name"],
            entity_type=EntityType(data["entity_type"]),
            session_key=data["session_key"],
            aliases=data.get("aliases", []),
            description=data.get("description"),
            attributes=data.get("attributes", {}),
            mention_count=data.get("mention_count", 0),
            total_mentions=data.get("total_mentions", 0),
            mekb_score=data.get("mekb_score", 0.0),
            source_episode_uids=data.get("source_episode_uids", []),
            first_seen=datetime.fromisoformat(data["first_seen"]),
            last_seen=datetime.fromisoformat(data["last_seen"]),
            emotional_valence=data.get("emotional_valence", 0.0),
            wikidata_id=data.get("wikidata_id"),
            wikipedia_url=data.get("wikipedia_url"),
        )


@dataclass
class Relation:
    """A relationship edge in the knowledge graph."""

    uid: str
    source_entity_uid: str
    target_entity_uid: str
    relation_type: RelationType
    session_key: str

    # Relation attributes
    weight: float = 1.0
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)

    # Source tracking
    source_episode_uids: list[str] = field(default_factory=list)
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Mention count for this specific relation
    mention_count: int = 1

    def record_mention(self, episode_uid: str) -> None:
        """Record a mention of this relation."""
        self.mention_count += 1
        self.weight = min(10.0, self.weight + 0.1)  # Strengthen with mentions
        if episode_uid not in self.source_episode_uids:
            self.source_episode_uids.append(episode_uid)
        self.last_seen = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "uid": self.uid,
            "source_entity_uid": self.source_entity_uid,
            "target_entity_uid": self.target_entity_uid,
            "relation_type": self.relation_type.value,
            "session_key": self.session_key,
            "weight": self.weight,
            "confidence": self.confidence,
            "attributes": self.attributes,
            "source_episode_uids": self.source_episode_uids,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "mention_count": self.mention_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Relation":
        """Create from dictionary."""
        return cls(
            uid=data["uid"],
            source_entity_uid=data["source_entity_uid"],
            target_entity_uid=data["target_entity_uid"],
            relation_type=RelationType(data["relation_type"]),
            session_key=data["session_key"],
            weight=data.get("weight", 1.0),
            confidence=data.get("confidence", 1.0),
            attributes=data.get("attributes", {}),
            source_episode_uids=data.get("source_episode_uids", []),
            first_seen=datetime.fromisoformat(data["first_seen"]),
            last_seen=datetime.fromisoformat(data["last_seen"]),
            mention_count=data.get("mention_count", 1),
        )


@dataclass
class ExtractionResult:
    """Result of entity/relation extraction from text."""

    text: str
    entities: list[Entity]
    relations: list[Relation]
    confidence: float = 0.0
    processing_time_ms: float = 0.0
    extractor_type: str = "unknown"
    raw_response: str | None = None


@dataclass
class MeKBScore:
    """MeKB TF-IDF scoring for entity importance.

    MeKBScore(u, e) = TF(u, e) × IDF(e)

    Where:
    - TF(u, e) = mentions(u, e) / total_mentions(u)
    - IDF(e) = log(N / n_e)
    - N = total users
    - n_e = users mentioning entity
    """

    entity_uid: str
    user_mentions: int = 0       # mentions(u, e)
    user_total_mentions: int = 0  # total_mentions(u)
    total_users: int = 1          # N
    users_mentioning: int = 1     # n_e

    @property
    def tf(self) -> float:
        """Term frequency for this user."""
        if self.user_total_mentions == 0:
            return 0.0
        return self.user_mentions / self.user_total_mentions

    @property
    def idf(self) -> float:
        """Inverse document frequency."""
        if self.users_mentioning == 0:
            return 0.0
        return math.log(self.total_users / self.users_mentioning)

    @property
    def score(self) -> float:
        """Compute TF-IDF score."""
        return self.tf * self.idf

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entity_uid": self.entity_uid,
            "user_mentions": self.user_mentions,
            "user_total_mentions": self.user_total_mentions,
            "total_users": self.total_users,
            "users_mentioning": self.users_mentioning,
            "tf": self.tf,
            "idf": self.idf,
            "score": self.score,
        }


@dataclass
class ReasoningPath:
    """A reasoning path through the knowledge graph."""

    path_id: str
    entities: list[Entity]
    relations: list[Relation]
    score: float = 0.0
    explanation: str = ""

    @property
    def length(self) -> int:
        """Number of hops in the path."""
        return len(self.relations)

    def to_string(self) -> str:
        """Convert path to readable string."""
        if not self.entities:
            return ""

        parts = []
        for i, entity in enumerate(self.entities):
            parts.append(f"[{entity.name}]")
            if i < len(self.relations):
                rel = self.relations[i]
                parts.append(f"-({rel.relation_type.value})->")

        return "".join(parts)


@dataclass
class ReasoningResult:
    """Result of graph reasoning."""

    query: str
    paths: list[ReasoningPath]
    relevant_entities: list[Entity]
    confidence: float = 0.0
    processing_time_ms: float = 0.0
    explanation: str = ""


@dataclass
class ReflectiveInsight:
    """High-level insight derived from knowledge graph patterns."""

    uid: str
    session_key: str
    insight_type: str  # "preference", "pattern", "relationship", "summary"
    content: str
    confidence: float = 0.0

    # Supporting evidence
    supporting_entities: list[str] = field(default_factory=list)
    supporting_relations: list[str] = field(default_factory=list)
    source_episode_uids: list[str] = field(default_factory=list)

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "uid": self.uid,
            "session_key": self.session_key,
            "insight_type": self.insight_type,
            "content": self.content,
            "confidence": self.confidence,
            "supporting_entities": self.supporting_entities,
            "supporting_relations": self.supporting_relations,
            "source_episode_uids": self.source_episode_uids,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

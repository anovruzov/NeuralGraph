"""Domain classification data types for memory sharding."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ShardType(str, Enum):
    """Types of memory shards for partitioned retrieval."""
    PERSONA_PROFILE = "persona_profile"     # Identity, beliefs, preferences
    CONVERSATION_LOG = "conversation_log"   # All messages with timestamps
    FACTUAL_KNOWLEDGE = "factual_knowledge" # Facts, events, entities
    GOAL_HISTORY = "goal_history"           # Plans, goals, outcomes


class Domain(str, Enum):
    """Semantic domains for memory classification and sharding."""

    EVENTS = "events"  # Activities, appointments, meetings, dates
    PERSONAL = "personal"  # Identity, feelings, preferences
    RELATIONSHIPS = "relationships"  # Friends, family, social connections
    WORK = "work"  # Job, career, professional activities
    HOBBIES = "hobbies"  # Interests, leisure activities
    HEALTH = "health"  # Medical, wellness, fitness
    FINANCE = "finance"  # Money, purchases, budgets
    LOCATION = "location"  # Places, travel, geography
    GENERAL = "general"  # Fallback for uncategorized content

    @classmethod
    def from_string(cls, s: str) -> "Domain":
        """Parse a domain from string, case-insensitive."""
        s = s.strip().lower()
        for domain in cls:
            if domain.value == s:
                return domain
        return cls.GENERAL

    @classmethod
    def all_values(cls) -> list[str]:
        """Return all domain values as strings."""
        return [d.value for d in cls]


@dataclass
class DomainClassification:
    """Result of domain classification for a piece of content."""

    domains: list[Domain]
    confidence: float = 1.0

    @property
    def primary_domain(self) -> Domain:
        """Return the primary (first) domain."""
        return self.domains[0] if self.domains else Domain.GENERAL

    def as_filter_value(self) -> str:
        """Return domains as comma-separated string for storage."""
        return ",".join(d.value for d in self.domains)

    @classmethod
    def from_filter_value(cls, value: str) -> "DomainClassification":
        """Parse domains from stored comma-separated string."""
        if not value:
            return cls(domains=[Domain.GENERAL])
        domains = [Domain.from_string(d) for d in value.split(",")]
        return cls(domains=domains)


# Domain to Shard Type mapping
DOMAIN_TO_SHARD: dict[Domain, ShardType] = {
    Domain.PERSONAL: ShardType.PERSONA_PROFILE,
    Domain.RELATIONSHIPS: ShardType.PERSONA_PROFILE,
    Domain.HEALTH: ShardType.PERSONA_PROFILE,
    Domain.EVENTS: ShardType.FACTUAL_KNOWLEDGE,
    Domain.LOCATION: ShardType.FACTUAL_KNOWLEDGE,
    Domain.FINANCE: ShardType.FACTUAL_KNOWLEDGE,
    Domain.WORK: ShardType.CONVERSATION_LOG,
    Domain.HOBBIES: ShardType.CONVERSATION_LOG,
    Domain.GENERAL: ShardType.CONVERSATION_LOG,
}


def get_shard_for_domain(domain: Domain) -> ShardType:
    """Get the appropriate shard type for a domain."""
    return DOMAIN_TO_SHARD.get(domain, ShardType.CONVERSATION_LOG)

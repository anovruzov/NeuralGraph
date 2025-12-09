"""
Factual Memory Store (Sino-Slavic-Iranian Architecture - Phase 4)

Graph-based storage for immutable facts (Chinese MemoryOS pattern).

Factual Memory stores objective data points:
- Entity-Attribute-Value triples
- High precision, low ambiguity
- Immutable (facts don't change, only our knowledge of them)

This contrasts with Reflective Memory which stores evolving insights.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FactualTriple:
    """Entity-Attribute-Value triple for immutable facts.

    Represents a verified fact about an entity.
    Example: (Caroline, occupation, researcher)
    """
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity: str = ""                    # Who/what the fact is about
    attribute: str = ""                 # What aspect/property
    value: str = ""                     # The fact itself
    source_episode_uids: list[str] = field(default_factory=list)  # Where we learned this
    first_established: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0             # 1.0 for explicitly stated facts
    verification_count: int = 1         # How many times verified
    is_verified: bool = False           # Has been cross-validated


@dataclass
class FactQuery:
    """Query for retrieving facts."""
    entity: Optional[str] = None
    attribute: Optional[str] = None
    value: Optional[str] = None
    min_confidence: float = 0.0


@dataclass
class FactConflict:
    """Represents a conflict between facts."""
    existing_fact: FactualTriple
    new_fact: FactualTriple
    conflict_type: str  # "value_mismatch", "attribute_conflict"


class FactualMemoryStore:
    """Graph-based storage for immutable facts (Chinese MemoryOS pattern).

    This store implements the MemBench Factual Memory requirements:
    - Entity-Attribute-Value triple storage
    - High precision retrieval
    - Conflict detection and resolution
    - Verification tracking

    Key difference from vector stores:
    - Structured, exact matching (not probabilistic)
    - Facts are immutable once established
    - Cross-references between related facts
    """

    def __init__(self):
        """Initialize the factual memory store."""
        # Primary index: entity -> list of triples
        self._entity_index: dict[str, list[FactualTriple]] = defaultdict(list)

        # Secondary index: attribute -> list of triples
        self._attribute_index: dict[str, list[FactualTriple]] = defaultdict(list)

        # Tertiary index: value -> list of triples (for reverse lookups)
        self._value_index: dict[str, list[FactualTriple]] = defaultdict(list)

        # UID lookup
        self._uid_lookup: dict[str, FactualTriple] = {}

        # Statistics
        self._total_facts = 0
        self._conflicts_detected = 0

    def store_fact(
        self,
        entity: str,
        attribute: str,
        value: str,
        source_uid: Optional[str] = None,
        confidence: float = 1.0,
    ) -> tuple[FactualTriple, Optional[FactConflict]]:
        """Store a verified factual triple.

        Args:
            entity: Who/what the fact is about
            attribute: What aspect/property
            value: The fact itself
            source_uid: Source episode UID
            confidence: Confidence score (1.0 for stated facts)

        Returns:
            Tuple of (stored_triple, conflict_if_any)
        """
        entity_key = entity.lower().strip()
        attr_key = attribute.lower().strip()
        value_key = value.lower().strip()

        # Check for existing facts
        existing = self._find_exact_fact(entity_key, attr_key)

        if existing:
            # Same entity-attribute exists
            if existing.value.lower() == value_key:
                # Same value - just increment verification
                existing.verification_count += 1
                existing.last_verified = datetime.now(timezone.utc)
                if source_uid and source_uid not in existing.source_episode_uids:
                    existing.source_episode_uids.append(source_uid)
                return existing, None
            else:
                # Different value - conflict!
                conflict = FactConflict(
                    existing_fact=existing,
                    new_fact=FactualTriple(
                        entity=entity,
                        attribute=attribute,
                        value=value,
                        source_episode_uids=[source_uid] if source_uid else [],
                        confidence=confidence,
                    ),
                    conflict_type="value_mismatch"
                )
                self._conflicts_detected += 1
                logger.warning(
                    f"Fact conflict: {entity}.{attribute} = '{existing.value}' vs '{value}'"
                )
                # Return existing fact + conflict
                # Caller must decide resolution strategy
                return existing, conflict

        # New fact - create and index
        triple = FactualTriple(
            entity=entity,
            attribute=attribute,
            value=value,
            source_episode_uids=[source_uid] if source_uid else [],
            confidence=confidence,
        )

        self._index_triple(triple)
        self._total_facts += 1

        return triple, None

    def _find_exact_fact(
        self,
        entity_key: str,
        attr_key: str
    ) -> Optional[FactualTriple]:
        """Find exact entity-attribute match."""
        for triple in self._entity_index.get(entity_key, []):
            if triple.attribute.lower() == attr_key:
                return triple
        return None

    def _index_triple(self, triple: FactualTriple):
        """Add triple to all indexes."""
        entity_key = triple.entity.lower()
        attr_key = triple.attribute.lower()
        value_key = triple.value.lower()

        self._entity_index[entity_key].append(triple)
        self._attribute_index[attr_key].append(triple)
        self._value_index[value_key].append(triple)
        self._uid_lookup[triple.uid] = triple

    def get_facts(
        self,
        entity: Optional[str] = None,
        attribute: Optional[str] = None,
        value: Optional[str] = None,
    ) -> list[FactualTriple]:
        """Retrieve facts matching the query.

        Args:
            entity: Filter by entity name
            attribute: Filter by attribute type
            value: Filter by value

        Returns:
            List of matching FactualTriple objects
        """
        if entity:
            candidates = self._entity_index.get(entity.lower(), [])
        elif attribute:
            candidates = self._attribute_index.get(attribute.lower(), [])
        elif value:
            candidates = self._value_index.get(value.lower(), [])
        else:
            # All facts
            candidates = list(self._uid_lookup.values())

        # Apply additional filters
        results = []
        for triple in candidates:
            if entity and triple.entity.lower() != entity.lower():
                continue
            if attribute and triple.attribute.lower() != attribute.lower():
                continue
            if value and triple.value.lower() != value.lower():
                continue
            results.append(triple)

        return results

    def get_fact_by_uid(self, uid: str) -> Optional[FactualTriple]:
        """Get a specific fact by UID."""
        return self._uid_lookup.get(uid)

    def verify_claim(
        self,
        entity: str,
        attribute: str,
        claimed_value: str,
    ) -> tuple[bool, Optional[str], Optional[FactualTriple]]:
        """Verify if a claim about an entity is correct.

        Args:
            entity: Who/what is being claimed about
            attribute: What aspect
            claimed_value: The claimed value

        Returns:
            Tuple of (is_correct, actual_value_if_wrong, matching_fact)
        """
        facts = self.get_facts(entity=entity, attribute=attribute)

        if not facts:
            # No evidence to contradict
            return True, None, None

        for fact in facts:
            if self._values_match(fact.value, claimed_value):
                # Claim matches a stored fact
                return True, None, fact

        # Claim doesn't match any stored fact
        actual_value = facts[0].value
        return False, actual_value, facts[0]

    def _values_match(self, stored: str, claimed: str) -> bool:
        """Check if two values match (fuzzy)."""
        stored_lower = stored.lower().strip()
        claimed_lower = claimed.lower().strip()

        # Exact match
        if stored_lower == claimed_lower:
            return True

        # Substring match
        if stored_lower in claimed_lower or claimed_lower in stored_lower:
            return True

        return False

    def find_entity_for_attribute(
        self,
        attribute: str,
        value: str,
    ) -> list[str]:
        """Find which entity(ies) have a specific attribute-value.

        Useful for adversarial detection: "Who has pottery as hobby?"

        Args:
            attribute: The attribute type
            value: The value to look for

        Returns:
            List of entity names
        """
        entities = []
        attr_facts = self._attribute_index.get(attribute.lower(), [])

        for fact in attr_facts:
            if self._values_match(fact.value, value):
                entities.append(fact.entity)

        return entities

    def get_entity_profile(self, entity: str) -> dict[str, str]:
        """Get all facts about an entity as a profile dict.

        Args:
            entity: The entity name

        Returns:
            Dict of attribute -> value
        """
        facts = self.get_facts(entity=entity)
        return {f.attribute: f.value for f in facts}

    def search_by_value(self, value: str) -> list[tuple[str, str, FactualTriple]]:
        """Search for facts containing a value.

        Returns list of (entity, attribute, triple) tuples.
        """
        results = []
        value_lower = value.lower()

        for val_key, triples in self._value_index.items():
            if value_lower in val_key or val_key in value_lower:
                for triple in triples:
                    results.append((triple.entity, triple.attribute, triple))

        return results

    def get_stats(self) -> dict:
        """Get storage statistics."""
        return {
            "total_facts": self._total_facts,
            "unique_entities": len(self._entity_index),
            "unique_attributes": len(self._attribute_index),
            "conflicts_detected": self._conflicts_detected,
        }

    def clear(self):
        """Clear all stored facts."""
        self._entity_index.clear()
        self._attribute_index.clear()
        self._value_index.clear()
        self._uid_lookup.clear()
        self._total_facts = 0
        self._conflicts_detected = 0

    def export_graph(self) -> list[dict]:
        """Export all facts as list of dicts (for serialization)."""
        return [
            {
                "uid": t.uid,
                "entity": t.entity,
                "attribute": t.attribute,
                "value": t.value,
                "confidence": t.confidence,
                "verification_count": t.verification_count,
            }
            for t in self._uid_lookup.values()
        ]

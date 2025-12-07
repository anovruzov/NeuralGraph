"""Entity verification for adversarial query detection.

Detects when queries attribute facts/events to the wrong entity
by cross-referencing claims against the knowledge graph.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .data_types import Entity, EntityType, Relation, RelationType

logger = logging.getLogger(__name__)


@dataclass
class EntityClaim:
    """A claim about an entity extracted from a query."""

    entity_name: str
    claimed_action: str | None = None  # What the entity supposedly did
    claimed_attribute: str | None = None  # What the entity supposedly has/is
    claimed_relation: str | None = None  # Who the entity is related to
    original_text: str = ""


@dataclass
class VerificationResult:
    """Result of entity verification against knowledge graph."""

    query: str
    claims: list[EntityClaim] = field(default_factory=list)
    verified_entities: list[Entity] = field(default_factory=list)

    # Adversarial detection
    is_adversarial: bool = False
    entity_swaps_detected: list[dict[str, Any]] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)

    # Confidence
    confidence: float = 1.0
    explanation: str = ""

    def add_swap_detection(
        self,
        claimed_entity: str,
        actual_entity: str,
        fact: str,
    ) -> None:
        """Record a detected entity swap."""
        self.is_adversarial = True
        self.entity_swaps_detected.append({
            "claimed_entity": claimed_entity,
            "actual_entity": actual_entity,
            "fact": fact,
        })
        self.corrections.append(
            f"The question claims '{claimed_entity}' {fact}, "
            f"but memories show this was actually '{actual_entity}'."
        )


class EntityVerifier:
    """Verifies entity claims against knowledge graph to detect adversarial queries.

    This detects questions like:
    - "What is Melanie's necklace symbolize?" (when it's Caroline's necklace)
    - "When did Jon paint a sunrise?" (when Gina painted the sunrise)
    """

    def __init__(
        self,
        entities: list[Entity] | None = None,
        relations: list[Relation] | None = None,
    ):
        """Initialize verifier with knowledge graph data.

        Args:
            entities: List of entities from knowledge graph.
            relations: List of relations from knowledge graph.
        """
        self._entities = entities or []
        self._relations = relations or []

        # Build lookup indices
        self._entity_by_name: dict[str, Entity] = {}
        self._entity_by_uid: dict[str, Entity] = {}
        self._relations_by_entity: dict[str, list[Relation]] = {}

        self._build_indices()

    def _build_indices(self) -> None:
        """Build lookup indices for fast verification."""
        for entity in self._entities:
            # Index by name (lowercase for case-insensitive matching)
            self._entity_by_name[entity.name.lower()] = entity
            for alias in entity.aliases:
                self._entity_by_name[alias.lower()] = entity
            self._entity_by_uid[entity.uid] = entity

        for relation in self._relations:
            # Index relations by source and target
            if relation.source_entity_uid not in self._relations_by_entity:
                self._relations_by_entity[relation.source_entity_uid] = []
            self._relations_by_entity[relation.source_entity_uid].append(relation)

            if relation.target_entity_uid not in self._relations_by_entity:
                self._relations_by_entity[relation.target_entity_uid] = []
            self._relations_by_entity[relation.target_entity_uid].append(relation)

    def update_graph(
        self,
        entities: list[Entity],
        relations: list[Relation],
    ) -> None:
        """Update the knowledge graph data."""
        self._entities = entities
        self._relations = relations
        self._build_indices()

    def extract_entity_names(self, query: str) -> list[str]:
        """Extract potential entity names from a query.

        Uses simple heuristics:
        1. Capitalized words
        2. Possessive forms (X's)
        3. Known entities from graph
        """
        names = set()

        # Find possessive forms (e.g., "Melanie's", "Caroline's")
        possessive_pattern = r"\b([A-Z][a-z]+)'s\b"
        for match in re.finditer(possessive_pattern, query):
            names.add(match.group(1))

        # Find capitalized names (excluding sentence starts)
        words = query.split()
        for i, word in enumerate(words):
            # Skip first word (might be capitalized due to sentence start)
            if i > 0 and word and word[0].isupper():
                # Clean punctuation
                clean_word = re.sub(r'[^\w]', '', word)
                if len(clean_word) > 1:
                    names.add(clean_word)

        # Match against known entities
        query_lower = query.lower()
        for entity_name in self._entity_by_name:
            if entity_name in query_lower:
                # Get the proper cased name
                entity = self._entity_by_name[entity_name]
                names.add(entity.name)

        return list(names)

    def find_entity(self, name: str) -> Entity | None:
        """Find entity by name (case-insensitive)."""
        return self._entity_by_name.get(name.lower())

    def get_entity_facts(self, entity: Entity) -> list[str]:
        """Get all facts about an entity from its relations."""
        facts = []

        relations = self._relations_by_entity.get(entity.uid, [])
        for rel in relations:
            if rel.source_entity_uid == entity.uid:
                # Entity is the source
                target = self._entity_by_uid.get(rel.target_entity_uid)
                if target:
                    facts.append(f"{rel.relation_type.value} {target.name}")
            else:
                # Entity is the target
                source = self._entity_by_uid.get(rel.source_entity_uid)
                if source:
                    facts.append(f"is {rel.relation_type.value} by {source.name}")

        return facts

    def find_owner_of_attribute(
        self,
        attribute_keywords: list[str],
    ) -> Entity | None:
        """Find which entity owns an attribute based on keywords.

        Used to detect questions like "What is X's necklace?" when
        the necklace actually belongs to Y.
        """
        for keyword in attribute_keywords:
            keyword_lower = keyword.lower()

            for relation in self._relations:
                # Check if any relation involves this attribute
                if relation.relation_type in [
                    RelationType.OWNS,
                    RelationType.RECEIVED,
                    RelationType.ASSOCIATED_WITH,
                ]:
                    target = self._entity_by_uid.get(relation.target_entity_uid)
                    if target and keyword_lower in target.name.lower():
                        owner = self._entity_by_uid.get(relation.source_entity_uid)
                        return owner

                # Check relation attributes
                for attr_key, attr_val in relation.attributes.items():
                    if keyword_lower in str(attr_val).lower():
                        owner = self._entity_by_uid.get(relation.source_entity_uid)
                        return owner

        return None

    def find_performer_of_action(
        self,
        action_keywords: list[str],
    ) -> Entity | None:
        """Find which entity performed an action.

        Used to detect questions like "When did X paint?" when
        Y actually painted.
        """
        for keyword in action_keywords:
            keyword_lower = keyword.lower()

            for relation in self._relations:
                if relation.relation_type == RelationType.PERFORMED:
                    target = self._entity_by_uid.get(relation.target_entity_uid)
                    if target and keyword_lower in target.name.lower():
                        performer = self._entity_by_uid.get(relation.source_entity_uid)
                        return performer

                # Check relation attributes for action keywords
                for attr_val in relation.attributes.values():
                    if keyword_lower in str(attr_val).lower():
                        performer = self._entity_by_uid.get(relation.source_entity_uid)
                        return performer

        return None

    def verify_query(self, query: str) -> VerificationResult:
        """Verify a query against the knowledge graph.

        Detects potential adversarial entity swaps.

        Args:
            query: The query to verify.

        Returns:
            VerificationResult with any detected entity swaps.
        """
        result = VerificationResult(query=query)

        if not self._entities:
            result.explanation = "No entities in knowledge graph"
            return result

        # Extract entity names from query
        entity_names = self.extract_entity_names(query)

        # Find verified entities
        for name in entity_names:
            entity = self.find_entity(name)
            if entity:
                result.verified_entities.append(entity)

        # Check for possessive claims (X's something)
        possessive_pattern = r"\b([A-Z][a-z]+)'s\s+(\w+)"
        for match in re.finditer(possessive_pattern, query):
            claimed_owner = match.group(1)
            attribute = match.group(2)

            # Find actual owner
            actual_owner = self.find_owner_of_attribute([attribute])

            if actual_owner and actual_owner.name.lower() != claimed_owner.lower():
                result.add_swap_detection(
                    claimed_entity=claimed_owner,
                    actual_entity=actual_owner.name,
                    fact=f"has {attribute}",
                )

        # Check for action claims (When did X verb?)
        action_pattern = r"(?:When|What|How|Why|Where)\s+did\s+([A-Z][a-z]+)\s+(\w+)"
        for match in re.finditer(action_pattern, query):
            claimed_performer = match.group(1)
            action = match.group(2)

            # Find actual performer
            actual_performer = self.find_performer_of_action([action])

            if actual_performer and actual_performer.name.lower() != claimed_performer.lower():
                result.add_swap_detection(
                    claimed_entity=claimed_performer,
                    actual_entity=actual_performer.name,
                    fact=action,
                )

        if result.is_adversarial:
            result.confidence = 0.9
            result.explanation = (
                f"Detected {len(result.entity_swaps_detected)} entity swap(s). "
                "The query attributes facts to the wrong person."
            )
        else:
            result.explanation = "No entity swaps detected"

        return result

    def get_correction_context(self, result: VerificationResult) -> str:
        """Generate correction context to prepend to retrieved memories.

        This helps the LLM recognize adversarial questions.
        """
        if not result.is_adversarial:
            return ""

        lines = ["=== IMPORTANT: ENTITY VERIFICATION WARNING ==="]
        for correction in result.corrections:
            lines.append(f"⚠️ {correction}")
        lines.append("Please verify entity attribution before answering.")
        lines.append("=" * 50)

        return "\n".join(lines)

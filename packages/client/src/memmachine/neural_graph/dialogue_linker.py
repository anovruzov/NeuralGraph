"""Universal Message Linking Layer - The Dialogue Fabric

THE FUNDAMENTAL INSIGHT:
Messages don't exist in isolation. Meaning flows BETWEEN speakers in a dialogue.
A response's meaning depends on what it's responding TO.

THE BINDING PROBLEM:
┌─────────────────────────────────────────────────────────────────────┐
│ USER: "I went to Paris yesterday"                                   │
│ ASSISTANT: "That sounds amazing! How was the Eiffel Tower?"         │
│ USER: "It was beautiful at sunset"                                  │
│                                                                     │
│ "It" → "Eiffel Tower" → "Paris" → "yesterday"                       │
│                                                                     │
│ The MEANING is distributed across the dialogue.                     │
│ No single message contains the full context.                        │
└─────────────────────────────────────────────────────────────────────┘

THE SOLUTION: DialogueAggregators
Hidden nodes that live ABOVE individual messages and capture:
1. EXCHANGE PAIRS - Question + Answer as atomic unit
2. TOPIC THREADS - Messages about same subject across speakers
3. COREFERENCE CHAINS - Pronouns linked to their referents
4. TEMPORAL ANCHORS - When things happened relative to conversation

Architecture:

    Layer 2: DIALOGUE AGGREGATORS (Hidden)
    ┌─────────────────────────────────────────────────────────┐
    │  [Exchange]     [Topic:Paris]     [Coref:it→Tower]      │
    │      │               │                   │              │
    └──────┼───────────────┼───────────────────┼──────────────┘
           │               │                   │
           ▼               ▼                   ▼
    Layer 1: MESSAGE NODES
    ┌─────────────────────────────────────────────────────────┐
    │  [User:went Paris] → [Asst:Eiffel Tower?] → [User:It]   │
    └─────────────────────────────────────────────────────────┘

When querying "What did User think of the Eiffel Tower?":
1. Query hits "Eiffel Tower" in Assistant message
2. Aggregator links to "It was beautiful" in User response
3. Coreference resolves "It" → "Eiffel Tower"
4. Answer found through CROSS-MESSAGE linking

This is how brains work - we don't remember individual sentences,
we remember CONVERSATIONS as coherent episodes.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data_types import NeuralNode, NeuralEdge
    from .storage import NeuralGraphStorage


# =============================================================================
# AGGREGATOR TYPES
# =============================================================================

class AggregatorType(Enum):
    """Types of dialogue aggregators."""
    EXCHANGE = "exchange"           # Question-Answer pair
    TOPIC_THREAD = "topic_thread"   # Messages about same topic
    COREF_CHAIN = "coref_chain"     # Coreference resolution chain
    TEMPORAL_ANCHOR = "temporal"    # Temporal context group
    SPEAKER_TURN = "speaker_turn"   # All messages in one speaker's turn
    REACTION = "reaction"           # Response to specific content


class BindingType(Enum):
    """Types of cross-message bindings."""
    RESPONDS_TO = "responds_to"     # This message responds to that one
    REFERS_TO = "refers_to"         # Pronoun/reference to prior mention
    CONTINUES = "continues"         # Same topic continuation
    ELABORATES = "elaborates"       # Adds detail to prior message
    CONTRASTS = "contrasts"         # Disagrees/contrasts with prior
    TEMPORAL_LINK = "temporal"      # Same time reference


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class DialogueAggregator:
    """Hidden aggregator node that binds related messages.

    Lives in Layer 1.5 (between messages and episodes).
    Not directly retrievable - activates when constituent messages activate.
    """
    aggregator_id: str
    aggregator_type: AggregatorType
    session_key: str

    # Constituent message node IDs
    message_ids: list[str] = field(default_factory=list)

    # What this aggregator represents
    topic: str = ""  # e.g., "Paris trip", "Eiffel Tower visit"

    # For coreference chains
    referent: str = ""  # The thing being referred to
    mentions: list[str] = field(default_factory=list)  # ["it", "that", "the tower"]

    # For temporal anchors
    temporal_expression: str = ""  # "yesterday", "last week"
    resolved_date: str = ""  # "2023-05-07"

    # Aggregated embedding (centroid of constituent messages)
    embedding: list[float] | None = None

    # Binding strength to each constituent
    binding_weights: dict[str, float] = field(default_factory=dict)

    # Importance based on how often accessed together
    cohesion_score: float = 0.5

    def __post_init__(self):
        if not self.aggregator_id:
            self.aggregator_id = f"agg-{uuid.uuid4().hex[:12]}"


@dataclass
class CrossMessageBinding:
    """Edge connecting messages through dialogue structure."""
    binding_id: str
    binding_type: BindingType
    source_id: str  # Message that references
    target_id: str  # Message being referenced

    # What's being linked
    source_span: str = ""  # e.g., "it"
    target_span: str = ""  # e.g., "Eiffel Tower"

    # Binding strength
    confidence: float = 0.8

    # For temporal bindings
    temporal_relation: str = ""  # "same_time", "before", "after"

    def __post_init__(self):
        if not self.binding_id:
            self.binding_id = f"bind-{uuid.uuid4().hex[:12]}"


# =============================================================================
# PRONOUN PATTERNS
# =============================================================================

PRONOUN_PATTERNS = {
    # Personal pronouns
    "he": "person_male",
    "him": "person_male",
    "his": "person_male",
    "she": "person_female",
    "her": "person_female",
    "hers": "person_female",
    "they": "person_plural",
    "them": "person_plural",
    "their": "person_plural",

    # Demonstratives
    "it": "thing",
    "this": "thing_near",
    "that": "thing_far",
    "these": "things_near",
    "those": "things_far",

    # Location
    "there": "place",
    "here": "place",

    # Time
    "then": "time",
}

# Temporal markers that need resolution
TEMPORAL_MARKERS = {
    "yesterday": -1,
    "today": 0,
    "tomorrow": 1,
    "last week": -7,
    "next week": 7,
    "last month": -30,
    "next month": 30,
    "last year": -365,
    "next year": 365,
}


# =============================================================================
# DIALOGUE LINKER
# =============================================================================

class DialogueLinker:
    """Creates and manages dialogue aggregators and cross-message bindings.

    This is the UNIVERSAL MESSAGE LINKING LAYER that captures how meaning
    flows between speakers in a conversation.
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self._storage = storage
        self._aggregators: dict[str, DialogueAggregator] = {}
        self._bindings: dict[str, CrossMessageBinding] = {}

        # Index: message_id -> aggregator_ids
        self._message_to_aggregators: dict[str, list[str]] = {}

        # Index: message_id -> binding_ids (as source)
        self._message_to_bindings: dict[str, list[str]] = {}

    async def process_dialogue_sequence(
        self,
        messages: list["NeuralNode"],
        session_key: str,
    ) -> tuple[list[DialogueAggregator], list[CrossMessageBinding]]:
        """Process a sequence of messages and create linking structures.

        This is the main entry point. Call after message ingestion.

        Args:
            messages: Ordered list of message nodes (must have content, speaker)
            session_key: Session identifier

        Returns:
            Tuple of (aggregators_created, bindings_created)
        """
        aggregators = []
        bindings = []

        # 1. Create EXCHANGE aggregators (adjacent Q-A pairs)
        exchange_aggs = await self._create_exchange_aggregators(messages, session_key)
        aggregators.extend(exchange_aggs)

        # 2. Create TOPIC THREAD aggregators (messages about same subject)
        topic_aggs = await self._create_topic_aggregators(messages, session_key)
        aggregators.extend(topic_aggs)

        # 3. Create COREFERENCE bindings (pronouns to referents)
        coref_bindings = await self._create_coreference_bindings(messages)
        bindings.extend(coref_bindings)

        # 4. Create RESPONSE bindings (message responds to previous)
        response_bindings = await self._create_response_bindings(messages)
        bindings.extend(response_bindings)

        # 5. Create TEMPORAL ANCHOR aggregators
        temporal_aggs = await self._create_temporal_anchors(messages, session_key)
        aggregators.extend(temporal_aggs)

        # Store everything
        for agg in aggregators:
            self._aggregators[agg.aggregator_id] = agg
            for msg_id in agg.message_ids:
                if msg_id not in self._message_to_aggregators:
                    self._message_to_aggregators[msg_id] = []
                self._message_to_aggregators[msg_id].append(agg.aggregator_id)

        for binding in bindings:
            self._bindings[binding.binding_id] = binding
            if binding.source_id not in self._message_to_bindings:
                self._message_to_bindings[binding.source_id] = []
            self._message_to_bindings[binding.source_id].append(binding.binding_id)

        return aggregators, bindings

    async def _create_exchange_aggregators(
        self,
        messages: list["NeuralNode"],
        session_key: str,
    ) -> list[DialogueAggregator]:
        """Create aggregators for question-answer exchanges.

        When Speaker A says something and Speaker B responds,
        those two messages form an EXCHANGE unit.
        """
        aggregators = []

        for i in range(len(messages) - 1):
            msg_a = messages[i]
            msg_b = messages[i + 1]

            # Get speakers (from metadata or content prefix)
            speaker_a = self._get_speaker(msg_a)
            speaker_b = self._get_speaker(msg_b)

            # Different speakers = exchange
            if speaker_a != speaker_b:
                # Check if it's Q-A pattern
                is_question = self._is_question(msg_a.content)
                is_reaction = self._is_reaction(msg_b.content)

                agg_type = AggregatorType.EXCHANGE
                topic = self._extract_topic(msg_a.content, msg_b.content)

                agg = DialogueAggregator(
                    aggregator_id=f"exch-{uuid.uuid4().hex[:8]}",
                    aggregator_type=agg_type,
                    session_key=session_key,
                    message_ids=[msg_a.node_id, msg_b.node_id],
                    topic=topic,
                    binding_weights={
                        msg_a.node_id: 1.0,
                        msg_b.node_id: 1.0,
                    },
                    cohesion_score=0.9 if is_question else 0.7,
                )

                # Compute centroid embedding
                if msg_a.embedding and msg_b.embedding:
                    agg.embedding = [
                        (a + b) / 2
                        for a, b in zip(msg_a.embedding, msg_b.embedding)
                    ]

                aggregators.append(agg)

        return aggregators

    async def _create_topic_aggregators(
        self,
        messages: list["NeuralNode"],
        session_key: str,
    ) -> list[DialogueAggregator]:
        """Create aggregators for messages about the same topic.

        Groups messages that mention the same entities/subjects,
        even if they're not adjacent.
        """
        aggregators = []

        # Extract entities from each message
        msg_entities: dict[str, set[str]] = {}
        for msg in messages:
            entities = self._extract_entities(msg.content)
            msg_entities[msg.node_id] = entities

        # Find entity clusters (messages sharing entities)
        entity_to_messages: dict[str, list[str]] = {}
        for msg_id, entities in msg_entities.items():
            for entity in entities:
                entity_lower = entity.lower()
                if entity_lower not in entity_to_messages:
                    entity_to_messages[entity_lower] = []
                entity_to_messages[entity_lower].append(msg_id)

        # Create aggregator for each entity with 2+ messages
        for entity, msg_ids in entity_to_messages.items():
            if len(msg_ids) >= 2:
                agg = DialogueAggregator(
                    aggregator_id=f"topic-{uuid.uuid4().hex[:8]}",
                    aggregator_type=AggregatorType.TOPIC_THREAD,
                    session_key=session_key,
                    message_ids=msg_ids,
                    topic=entity,
                    cohesion_score=min(1.0, len(msg_ids) * 0.2),
                )
                aggregators.append(agg)

        return aggregators

    async def _create_coreference_bindings(
        self,
        messages: list["NeuralNode"],
    ) -> list[CrossMessageBinding]:
        """Create bindings for pronouns to their referents.

        "It was beautiful" → "Eiffel Tower"
        "She said yes" → "Caroline"
        """
        bindings = []

        # Build entity history (what nouns have been mentioned)
        entity_history: list[tuple[str, str, str]] = []  # (entity, msg_id, category)

        for msg in messages:
            content = msg.content

            # Find pronouns that need resolution
            pronouns_found = self._find_pronouns(content)

            for pronoun, category in pronouns_found:
                # Look back for matching antecedent
                for entity, entity_msg_id, entity_category in reversed(entity_history):
                    if self._categories_match(category, entity_category):
                        binding = CrossMessageBinding(
                            binding_id=f"coref-{uuid.uuid4().hex[:8]}",
                            binding_type=BindingType.REFERS_TO,
                            source_id=msg.node_id,
                            target_id=entity_msg_id,
                            source_span=pronoun,
                            target_span=entity,
                            confidence=0.7,
                        )
                        bindings.append(binding)
                        break  # Take most recent match

            # Add entities from this message to history
            entities = self._extract_entities(content)
            for entity in entities:
                category = self._categorize_entity(entity)
                entity_history.append((entity, msg.node_id, category))

        return bindings

    async def _create_response_bindings(
        self,
        messages: list["NeuralNode"],
    ) -> list[CrossMessageBinding]:
        """Create bindings for responses to their triggers.

        Every message (except first) responds to something before it.
        """
        bindings = []

        for i in range(1, len(messages)):
            current = messages[i]
            previous = messages[i - 1]

            # Check for explicit response markers
            binding_type = BindingType.RESPONDS_TO
            confidence = 0.6

            content_lower = current.content.lower()

            # Boost confidence for explicit markers
            if any(marker in content_lower for marker in
                   ["yes", "no", "yeah", "sure", "thanks", "wow", "that's"]):
                confidence = 0.9
                binding_type = BindingType.RESPONDS_TO

            if any(marker in content_lower for marker in
                   ["also", "and", "plus", "too"]):
                binding_type = BindingType.CONTINUES
                confidence = 0.8

            if any(marker in content_lower for marker in
                   ["but", "however", "actually", "no,"]):
                binding_type = BindingType.CONTRASTS
                confidence = 0.85

            binding = CrossMessageBinding(
                binding_id=f"resp-{uuid.uuid4().hex[:8]}",
                binding_type=binding_type,
                source_id=current.node_id,
                target_id=previous.node_id,
                confidence=confidence,
            )
            bindings.append(binding)

        return bindings

    async def _create_temporal_anchors(
        self,
        messages: list["NeuralNode"],
        session_key: str,
    ) -> list[DialogueAggregator]:
        """Create aggregators for temporal expressions.

        Groups messages with the same temporal reference and
        records the resolved date if available.
        """
        aggregators = []

        # Find temporal expressions in messages
        temporal_messages: dict[str, list[tuple[str, str]]] = {}  # marker -> [(msg_id, full_expr)]

        for msg in messages:
            content_lower = msg.content.lower()

            for marker, offset in TEMPORAL_MARKERS.items():
                if marker in content_lower:
                    if marker not in temporal_messages:
                        temporal_messages[marker] = []
                    temporal_messages[marker].append((msg.node_id, marker))

        # Create aggregator for each temporal marker
        for marker, msg_info in temporal_messages.items():
            msg_ids = [m[0] for m in msg_info]

            agg = DialogueAggregator(
                aggregator_id=f"temp-{uuid.uuid4().hex[:8]}",
                aggregator_type=AggregatorType.TEMPORAL_ANCHOR,
                session_key=session_key,
                message_ids=msg_ids,
                temporal_expression=marker,
                # resolved_date would be set by the caller who knows session dates
                cohesion_score=0.95,  # Temporal anchors are strong
            )
            aggregators.append(agg)

        return aggregators

    # =========================================================================
    # RETRIEVAL METHODS
    # =========================================================================

    def get_linked_messages(self, message_id: str) -> list[tuple[str, float, str]]:
        """Get all messages linked to this one through aggregators/bindings.

        Returns:
            List of (linked_message_id, link_strength, link_type)
        """
        linked = []

        # Through aggregators
        agg_ids = self._message_to_aggregators.get(message_id, [])
        for agg_id in agg_ids:
            agg = self._aggregators.get(agg_id)
            if agg:
                for other_id in agg.message_ids:
                    if other_id != message_id:
                        weight = agg.binding_weights.get(other_id, 0.5)
                        linked.append((other_id, weight * agg.cohesion_score, agg.aggregator_type.value))

        # Through bindings (as source)
        binding_ids = self._message_to_bindings.get(message_id, [])
        for binding_id in binding_ids:
            binding = self._bindings.get(binding_id)
            if binding:
                linked.append((binding.target_id, binding.confidence, binding.binding_type.value))

        # Through bindings (as target) - reverse lookup
        for binding_id, binding in self._bindings.items():
            if binding.target_id == message_id:
                linked.append((binding.source_id, binding.confidence, f"reverse_{binding.binding_type.value}"))

        return linked

    def get_aggregators_for_message(self, message_id: str) -> list[DialogueAggregator]:
        """Get all aggregators containing this message."""
        agg_ids = self._message_to_aggregators.get(message_id, [])
        return [self._aggregators[aid] for aid in agg_ids if aid in self._aggregators]

    def get_topic_context(self, topic: str) -> list[str]:
        """Get all message IDs related to a topic."""
        message_ids = []
        for agg in self._aggregators.values():
            if agg.aggregator_type == AggregatorType.TOPIC_THREAD:
                if topic.lower() in agg.topic.lower():
                    message_ids.extend(agg.message_ids)
        return list(set(message_ids))

    def resolve_coreference(self, message_id: str, pronoun: str) -> str | None:
        """Resolve a pronoun in a message to its referent."""
        binding_ids = self._message_to_bindings.get(message_id, [])
        for binding_id in binding_ids:
            binding = self._bindings.get(binding_id)
            if binding and binding.binding_type == BindingType.REFERS_TO:
                if binding.source_span.lower() == pronoun.lower():
                    return binding.target_span
        return None

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _get_speaker(self, node: "NeuralNode") -> str:
        """Extract speaker from node."""
        # Try metadata
        if hasattr(node, 'metadata') and node.metadata:
            speaker = node.metadata.get('speaker')
            if speaker:
                return speaker

        # Try content prefix "Speaker: text"
        content = node.content or ""
        if ":" in content[:50]:
            return content.split(":")[0].strip()

        return "unknown"

    def _is_question(self, content: str) -> bool:
        """Check if content is a question."""
        content = content.strip()
        if content.endswith("?"):
            return True
        question_starters = ["what", "when", "where", "who", "why", "how", "is", "are", "do", "does", "can", "could", "would"]
        first_word = content.lower().split()[0] if content else ""
        return first_word in question_starters

    def _is_reaction(self, content: str) -> bool:
        """Check if content is a reaction to something."""
        reactions = ["wow", "oh", "nice", "great", "cool", "awesome", "thanks", "yeah", "yes", "no", "sure", "ok"]
        first_word = content.lower().split()[0] if content else ""
        return first_word in reactions

    def _extract_topic(self, content_a: str, content_b: str) -> str:
        """Extract main topic from exchange."""
        # Simple: find shared capitalized words
        words_a = set(w for w in content_a.split() if w[0].isupper() if len(w) > 2)
        words_b = set(w for w in content_b.split() if w[0].isupper() if len(w) > 2)
        shared = words_a & words_b
        if shared:
            return ", ".join(shared)

        # Fallback: first capitalized word in A
        for word in content_a.split():
            if len(word) > 2 and word[0].isupper():
                return word

        return ""

    def _extract_entities(self, content: str) -> set[str]:
        """Extract named entities (capitalized words)."""
        entities = set()
        words = content.split()

        # Skip first word (might be capitalized just because it starts sentence)
        for i, word in enumerate(words):
            clean = re.sub(r'[^\w]', '', word)
            if len(clean) > 2 and clean[0].isupper():
                # Skip common words that are often capitalized
                if clean.lower() not in ["the", "and", "but", "for", "with", "this", "that"]:
                    entities.add(clean)

        return entities

    def _find_pronouns(self, content: str) -> list[tuple[str, str]]:
        """Find pronouns and their categories in content."""
        found = []
        words = content.lower().split()

        for word in words:
            clean = re.sub(r'[^\w]', '', word)
            if clean in PRONOUN_PATTERNS:
                found.append((clean, PRONOUN_PATTERNS[clean]))

        return found

    def _categorize_entity(self, entity: str) -> str:
        """Categorize an entity for coreference matching."""
        # Simple heuristics
        entity_lower = entity.lower()

        # Check for place indicators
        if any(p in entity_lower for p in ["city", "street", "park", "tower", "building"]):
            return "place"

        # Check for common name patterns
        # This is simplistic - real NER would be better
        if entity[0].isupper() and len(entity) > 2:
            # Assume proper nouns are people by default
            return "person"

        return "thing"

    def _categories_match(self, pronoun_cat: str, entity_cat: str) -> bool:
        """Check if pronoun category matches entity category."""
        if pronoun_cat == "thing":
            return entity_cat in ["thing", "place"]
        if pronoun_cat == "place":
            return entity_cat == "place"
        if pronoun_cat.startswith("person"):
            return entity_cat == "person"
        return True  # Default match


# =============================================================================
# INTEGRATION FUNCTION
# =============================================================================

async def create_dialogue_links(
    messages: list["NeuralNode"],
    session_key: str,
    storage: "NeuralGraphStorage",
) -> DialogueLinker:
    """Convenience function to create dialogue links for a message sequence.

    Call this after ingesting messages to create the linking layer.

    Args:
        messages: Ordered list of message nodes
        session_key: Session identifier
        storage: Neural graph storage

    Returns:
        DialogueLinker with all aggregators and bindings created
    """
    linker = DialogueLinker(storage)
    await linker.process_dialogue_sequence(messages, session_key)
    return linker

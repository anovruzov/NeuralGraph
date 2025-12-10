"""Sun-Graph Integration - Making the Sun's understanding part of the neural graph.

THE KEY INSIGHT:
The Sun analyzes conversations holistically, but that understanding must be
EMBEDDED into the neural graph so the Moon (electron retrieval) can use it.

This module creates:
1. TOPIC nodes from the Sun's topic analysis
2. PERSONA nodes from participant profiles
3. SUMMARY nodes for key facts and emotional moments
4. Edges linking messages to their topics/personas/summaries

When electrons flow, they can:
- Start at a message, flow UP to its topic node
- From topic node, flow DOWN to related messages
- Use summary nodes as "gravitational centers" that pull related memories together
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .conversation_sun import ConversationSummary, TopicThread, ParticipantProfile, TemporalEvent
    from .storage import NeuralGraphStorage

from .data_types import NeuralNode, NeuralEdge, NodeLayer, EdgeType


# =============================================================================
# SUN NODE TYPES
# =============================================================================

@dataclass
class SunNode:
    """A node created by the Sun layer for holistic understanding."""
    node_id: str
    node_type: str  # "topic", "persona", "summary", "emotional_moment"
    content: str
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)
    linked_message_ids: list[str] = field(default_factory=list)
    importance: float = 0.5


# =============================================================================
# GRAPH INTEGRATION
# =============================================================================

class SunGraphIntegrator:
    """Integrates Sun's understanding into the neural graph.

    Creates higher-level nodes (TOPIC, PERSONA) that message nodes
    link to, enabling electrons to flow through the conceptual hierarchy.
    """

    def __init__(self, storage: "NeuralGraphStorage"):
        self._storage = storage
        self._topic_node_ids: dict[str, str] = {}  # topic_name -> node_id
        self._persona_node_ids: dict[str, str] = {}  # participant_name -> node_id
        self._summary_node_ids: list[str] = []
        self._temporal_node_ids: dict[str, str] = {}  # date_text -> node_id (CRITICAL for temporal queries)

    async def integrate_sun_summary(
        self,
        summary: "ConversationSummary",
        session_key: str,
        get_embedding_fn,
    ) -> dict[str, Any]:
        """Integrate Sun's conversation summary into the neural graph.

        Creates:
        1. Topic nodes for each identified topic
        2. Persona nodes for each participant
        3. Summary nodes for key facts
        4. Returns mapping of topic/persona names to node IDs for linking

        Args:
            summary: ConversationSummary from the Sun layer
            session_key: Session identifier
            get_embedding_fn: Async function to get embeddings

        Returns:
            Dict with topic_ids, persona_ids, and summary_ids for linking
        """
        result = {
            "topic_ids": {},
            "persona_ids": {},
            "summary_ids": [],
            "temporal_ids": {},  # CRITICAL: date_text -> node_id for temporal queries
            "nodes_created": 0,
        }

        # 1. Create TOPIC nodes
        for topic_name, topic_thread in summary.topics.items():
            topic_content = self._create_topic_content(topic_name, topic_thread)
            topic_embedding = await get_embedding_fn(topic_content)

            topic_node = NeuralNode(
                node_id=f"topic_{session_key}_{topic_name}_{uuid.uuid4().hex[:8]}",
                layer=NodeLayer.TOPIC,
                content=topic_content,
                embedding=topic_embedding,
                metadata={
                    "sun_type": "topic",
                    "topic_name": topic_name,
                    "mention_count": topic_thread.message_count,
                    "first_seen": topic_thread.first_seen.isoformat() if topic_thread.first_seen else None,
                    "last_seen": topic_thread.last_seen.isoformat() if topic_thread.last_seen else None,
                },
                importance_score=min(1.0, topic_thread.message_count / 10),
                session_key=session_key,
            )

            await self._storage.save_node(topic_node)
            result["topic_ids"][topic_name] = topic_node.node_id
            self._topic_node_ids[topic_name] = topic_node.node_id
            result["nodes_created"] += 1

        # 2. Create PERSONA nodes for each participant
        for name, profile in summary.participants.items():
            persona_content = self._create_persona_content(name, profile, summary)
            persona_embedding = await get_embedding_fn(persona_content)

            persona_node = NeuralNode(
                node_id=f"persona_{session_key}_{name}_{uuid.uuid4().hex[:8]}",
                layer=NodeLayer.PERSONA,
                content=persona_content,
                embedding=persona_embedding,
                metadata={
                    "sun_type": "persona",
                    "participant_name": name,
                    "message_count": profile.message_count,
                    "topics_discussed": profile.topics_discussed[:10],
                    "key_facts": profile.key_facts[:10],
                },
                importance_score=0.8,  # Personas are always important
                session_key=session_key,
            )

            await self._storage.save_node(persona_node)
            result["persona_ids"][name] = persona_node.node_id
            self._persona_node_ids[name] = persona_node.node_id
            result["nodes_created"] += 1

        # 3. Create SUMMARY nodes for key facts (grouped)
        if summary.key_facts:
            # Group facts into chunks of 5 for summary nodes
            fact_chunks = [summary.key_facts[i:i+5] for i in range(0, len(summary.key_facts), 5)]

            for i, chunk in enumerate(fact_chunks[:10]):  # Max 10 summary nodes
                summary_content = "Key facts from conversation:\n" + "\n".join(f"- {f}" for f in chunk)
                summary_embedding = await get_embedding_fn(summary_content)

                summary_node = NeuralNode(
                    node_id=f"summary_{session_key}_facts_{i}_{uuid.uuid4().hex[:8]}",
                    layer=NodeLayer.EPISODE,  # Summaries at episode level
                    content=summary_content,
                    embedding=summary_embedding,
                    metadata={
                        "sun_type": "summary",
                        "summary_type": "key_facts",
                        "fact_count": len(chunk),
                    },
                    importance_score=0.7,
                    session_key=session_key,
                )

                await self._storage.save_node(summary_node)
                result["summary_ids"].append(summary_node.node_id)
                self._summary_node_ids.append(summary_node.node_id)
                result["nodes_created"] += 1

        # 4. Create narrative summary node
        if summary.main_narrative:
            narrative_content = f"Conversation narrative: {summary.main_narrative}"
            if summary.relationship_type != "unknown":
                narrative_content += f"\nRelationship: {summary.relationship_type}"
            if summary.overall_sentiment != 0:
                sentiment_desc = "positive" if summary.overall_sentiment > 0.3 else "negative" if summary.overall_sentiment < -0.3 else "neutral"
                narrative_content += f"\nOverall tone: {sentiment_desc}"

            narrative_embedding = await get_embedding_fn(narrative_content)

            narrative_node = NeuralNode(
                node_id=f"summary_{session_key}_narrative_{uuid.uuid4().hex[:8]}",
                layer=NodeLayer.EPISODE,
                content=narrative_content,
                embedding=narrative_embedding,
                metadata={
                    "sun_type": "narrative",
                    "relationship_type": summary.relationship_type,
                    "overall_sentiment": summary.overall_sentiment,
                    "participant_count": len(summary.participants),
                },
                importance_score=0.9,  # Narrative is very important
                session_key=session_key,
            )

            await self._storage.save_node(narrative_node)
            result["summary_ids"].append(narrative_node.node_id)
            result["nodes_created"] += 1

        # 5. Create TEMPORAL nodes for date anchors - CRITICAL for "When did X happen?"
        # Group temporal events by date
        events_by_date: dict[str, list] = {}
        for event in summary.temporal_events:
            if event.date_text:
                date_key = event.date_text.strip()
                if date_key not in events_by_date:
                    events_by_date[date_key] = []
                events_by_date[date_key].append(event)

        # Also include session dates as temporal anchors
        for session_id, date_text in summary.session_dates.items():
            if date_text and date_text not in events_by_date:
                events_by_date[date_text] = []

        # Create a temporal node for each unique date
        for date_text, events in events_by_date.items():
            # Build rich content for the temporal node
            temporal_content = f"DATE: {date_text}\n"
            if events:
                temporal_content += "Events on this date:\n"
                for event in events[:10]:  # Limit events per date
                    temporal_content += f"- {event.speaker}: {event.event_description[:100]}\n"

            temporal_embedding = await get_embedding_fn(temporal_content)

            # Sanitize date_text for node_id (remove special chars)
            safe_date = "".join(c if c.isalnum() or c == '_' else '_' for c in date_text)

            temporal_node = NeuralNode(
                node_id=f"temporal_{session_key}_{safe_date}_{uuid.uuid4().hex[:8]}",
                layer=NodeLayer.TEMPORAL,
                content=temporal_content,
                embedding=temporal_embedding,
                metadata={
                    "sun_type": "temporal",
                    "date_text": date_text,
                    "event_count": len(events),
                    "speakers": list(set(e.speaker for e in events)) if events else [],
                },
                importance_score=0.95,  # Temporal nodes are VERY important for retrieval
                session_key=session_key,
            )

            await self._storage.save_node(temporal_node)
            result["temporal_ids"][date_text] = temporal_node.node_id
            self._temporal_node_ids[date_text] = temporal_node.node_id
            result["nodes_created"] += 1

        return result

    async def link_message_to_sun_nodes(
        self,
        message_node_id: str,
        message_content: str,
        speaker: str,
        topics: list[str],
        session_key: str,
        session_date: str | None = None,
    ) -> int:
        """Create edges linking a message node to its topic, persona, and temporal nodes.

        Args:
            message_node_id: ID of the message node
            message_content: Content for context
            speaker: Speaker name to link to persona
            topics: List of topic names this message relates to
            session_key: Session identifier
            session_date: Date string for this message's session (for temporal linking)

        Returns:
            Number of edges created
        """
        edges_created = 0

        # Link to persona node (bidirectional)
        if speaker in self._persona_node_ids:
            persona_id = self._persona_node_ids[speaker]

            # Message -> Persona (child to parent)
            edge_up = NeuralEdge(
                edge_id=f"edge_{message_node_id}_to_{persona_id}",
                source_id=message_node_id,
                target_id=persona_id,
                edge_type=EdgeType.HIERARCHY,
                base_weight=0.8,
                metadata={"direction": "up", "sun_link": True},
            )
            await self._storage.save_edge(edge_up)
            edges_created += 1

            # Persona -> Message (parent to child, for electron flow back)
            edge_down = NeuralEdge(
                edge_id=f"edge_{persona_id}_to_{message_node_id}",
                source_id=persona_id,
                target_id=message_node_id,
                edge_type=EdgeType.HIERARCHY,
                base_weight=0.5,  # Lower weight for downward flow
                metadata={"direction": "down", "sun_link": True},
            )
            await self._storage.save_edge(edge_down)
            edges_created += 1

        # Link to topic nodes (bidirectional)
        for topic in topics:
            if topic in self._topic_node_ids:
                topic_id = self._topic_node_ids[topic]

                # Message -> Topic
                edge_up = NeuralEdge(
                    edge_id=f"edge_{message_node_id}_to_{topic_id}",
                    source_id=message_node_id,
                    target_id=topic_id,
                    edge_type=EdgeType.SEMANTIC,
                    base_weight=0.7,
                    metadata={"direction": "up", "sun_link": True, "topic": topic},
                )
                await self._storage.save_edge(edge_up)
                edges_created += 1

                # Topic -> Message (for electron flow back)
                edge_down = NeuralEdge(
                    edge_id=f"edge_{topic_id}_to_{message_node_id}",
                    source_id=topic_id,
                    target_id=message_node_id,
                    edge_type=EdgeType.SEMANTIC,
                    base_weight=0.4,  # Lower weight for downward
                    metadata={"direction": "down", "sun_link": True, "topic": topic},
                )
                await self._storage.save_edge(edge_down)
                edges_created += 1

        # Link to temporal node (CRITICAL for "When did X happen?" queries)
        if session_date and session_date in self._temporal_node_ids:
            temporal_id = self._temporal_node_ids[session_date]

            # Message -> Temporal (when did this happen)
            edge_up = NeuralEdge(
                edge_id=f"edge_{message_node_id}_to_{temporal_id}",
                source_id=message_node_id,
                target_id=temporal_id,
                edge_type=EdgeType.TEMPORAL,
                base_weight=0.9,  # High weight for temporal links
                metadata={"direction": "up", "sun_link": True, "date": session_date},
            )
            await self._storage.save_edge(edge_up)
            edges_created += 1

            # Temporal -> Message (events on this date)
            edge_down = NeuralEdge(
                edge_id=f"edge_{temporal_id}_to_{message_node_id}",
                source_id=temporal_id,
                target_id=message_node_id,
                edge_type=EdgeType.TEMPORAL,
                base_weight=0.6,  # Good weight for finding events by date
                metadata={"direction": "down", "sun_link": True, "date": session_date},
            )
            await self._storage.save_edge(edge_down)
            edges_created += 1

        return edges_created

    def _create_topic_content(self, topic_name: str, thread: "TopicThread") -> str:
        """Create rich content for a topic node."""
        content_parts = [f"Topic: {topic_name.upper()}"]

        if thread.mentions:
            # Include some example mentions
            content_parts.append("Key mentions:")
            for mention in thread.mentions[:5]:
                content_parts.append(f"- {mention[:100]}")

        content_parts.append(f"Discussed {thread.message_count} times in this conversation.")

        return "\n".join(content_parts)

    def _create_persona_content(
        self,
        name: str,
        profile: "ParticipantProfile",
        summary: "ConversationSummary"
    ) -> str:
        """Create rich content for a persona node."""
        content_parts = [f"Participant: {name}"]

        if profile.topics_discussed:
            topics_str = ", ".join(profile.topics_discussed[:5])
            content_parts.append(f"Topics discussed: {topics_str}")

        if profile.key_facts:
            content_parts.append("Key facts:")
            for fact in profile.key_facts[:5]:
                content_parts.append(f"- {fact}")

        # Add emotional context
        if profile.emotional_moments:
            emotions = set(m.emotion for m in profile.emotional_moments[:10])
            if emotions:
                content_parts.append(f"Emotional themes: {', '.join(emotions)}")

        content_parts.append(f"Sent {profile.message_count} messages in this conversation.")

        return "\n".join(content_parts)

    def get_topic_node_id(self, topic_name: str) -> str | None:
        """Get the node ID for a topic."""
        return self._topic_node_ids.get(topic_name)

    def get_persona_node_id(self, participant_name: str) -> str | None:
        """Get the node ID for a participant."""
        return self._persona_node_ids.get(participant_name)

    def get_temporal_node_id(self, date_text: str) -> str | None:
        """Get the node ID for a temporal anchor (date)."""
        return self._temporal_node_ids.get(date_text)

    def get_all_sun_node_ids(self) -> list[str]:
        """Get all node IDs created by the Sun integration."""
        return (
            list(self._topic_node_ids.values()) +
            list(self._persona_node_ids.values()) +
            list(self._temporal_node_ids.values()) +
            self._summary_node_ids
        )


# =============================================================================
# HELPER: Enrich message content with Sun context
# =============================================================================

def enrich_message_with_sun_context(
    message_content: str,
    speaker: str,
    topics: list[str],
    summary: "ConversationSummary",
) -> str:
    """Enrich a message's content with Sun-derived context for better embedding.

    This ensures that when the message is embedded, it captures:
    - Who is speaking (persona context)
    - What topics are being discussed
    - The relationship context

    Args:
        message_content: Original message text
        speaker: Speaker name
        topics: Topics identified in this message
        summary: Full conversation summary

    Returns:
        Enriched content string
    """
    enrichments = []

    # Add speaker context
    if speaker in summary.participants:
        profile = summary.participants[speaker]
        if profile.topics_discussed:
            enrichments.append(f"[Speaker: {speaker}, discusses: {', '.join(profile.topics_discussed[:3])}]")

    # Add topic context
    if topics:
        enrichments.append(f"[Topics: {', '.join(topics)}]")

    # Add relationship context
    if summary.relationship_type != "unknown":
        enrichments.append(f"[Relationship: {summary.relationship_type}]")

    if enrichments:
        return message_content + " " + " ".join(enrichments)
    return message_content


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

async def create_sun_integrated_graph(
    storage: "NeuralGraphStorage",
    summary: "ConversationSummary",
    session_key: str,
    get_embedding_fn,
) -> SunGraphIntegrator:
    """Create and populate a Sun-integrated graph.

    Returns the integrator so messages can be linked to Sun nodes.
    """
    integrator = SunGraphIntegrator(storage)
    await integrator.integrate_sun_summary(summary, session_key, get_embedding_fn)
    return integrator

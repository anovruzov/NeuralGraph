"""Conversation Sun - The holistic understanding layer.

THE SUN METAPHOR:
Just as the Sun provides light and energy that all planets orbit around,
the Conversation Sun provides the holistic understanding that all
individual memories orbit around.

The Moon (Electron Retrieval) handles individual memory activation.
The Sun (this module) understands:
1. The ENTIRE conversation arc
2. WHO the participants are and their relationship
3. WHAT topics flow through the dialogue
4. The EMOTIONAL journey and key moments
5. HOW information connects across time

When a query comes in, the Sun provides context that helps the Moon
find the right memories. They work in orbital synergy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data_types import NeuralNode


# =============================================================================
# EMOTIONAL SIGNATURES
# =============================================================================

# Emotional markers and their valence
EMOTION_MARKERS = {
    # Positive emotions
    "happy": ("joy", 0.8),
    "excited": ("joy", 0.9),
    "thrilled": ("joy", 0.95),
    "love": ("love", 0.9),
    "loved": ("love", 0.85),
    "loving": ("love", 0.85),
    "proud": ("pride", 0.85),
    "grateful": ("gratitude", 0.8),
    "thankful": ("gratitude", 0.75),
    "thanks": ("gratitude", 0.6),
    "amazing": ("joy", 0.8),
    "wonderful": ("joy", 0.75),
    "great": ("joy", 0.6),
    "awesome": ("joy", 0.7),
    "beautiful": ("appreciation", 0.7),
    "celebrate": ("joy", 0.85),
    "celebrating": ("joy", 0.85),
    "support": ("support", 0.7),
    "supportive": ("support", 0.75),
    "helped": ("support", 0.65),
    "comfort": ("comfort", 0.7),
    "comforting": ("comfort", 0.75),
    "accepted": ("acceptance", 0.85),
    "acceptance": ("acceptance", 0.9),
    "belong": ("belonging", 0.8),
    "belonging": ("belonging", 0.85),
    "connected": ("connection", 0.75),
    "connection": ("connection", 0.8),

    # Negative emotions
    "sad": ("sadness", -0.7),
    "upset": ("sadness", -0.65),
    "disappointed": ("disappointment", -0.6),
    "frustrated": ("frustration", -0.65),
    "angry": ("anger", -0.75),
    "scared": ("fear", -0.7),
    "afraid": ("fear", -0.65),
    "worried": ("worry", -0.6),
    "anxious": ("anxiety", -0.65),
    "nervous": ("anxiety", -0.55),
    "stressed": ("stress", -0.6),
    "lonely": ("loneliness", -0.7),
    "hurt": ("pain", -0.75),
    "painful": ("pain", -0.7),
    "difficult": ("struggle", -0.5),
    "hard": ("struggle", -0.45),
    "tough": ("struggle", -0.5),
    "struggle": ("struggle", -0.55),
    "struggling": ("struggle", -0.6),
    "rejected": ("rejection", -0.8),
    "rejection": ("rejection", -0.85),

    # Milestone/transition emotions
    "finally": ("relief", 0.6),
    "milestone": ("achievement", 0.8),
    "achieved": ("achievement", 0.75),
    "accomplished": ("achievement", 0.8),
    "progress": ("growth", 0.65),
    "growing": ("growth", 0.6),
    "journey": ("transformation", 0.5),
    "transition": ("transformation", 0.6),
    "changed": ("transformation", 0.55),
    "becoming": ("transformation", 0.65),
}

# Topic categories for conversation understanding
TOPIC_MARKERS = {
    "identity": ["transgender", "trans", "identity", "authentic", "true self", "coming out"],
    "support": ["support", "help", "there for", "backing", "encouragement"],
    "family": ["family", "parents", "mother", "father", "kids", "children", "daughter", "son"],
    "friends": ["friend", "friendship", "buddy", "pal"],
    "work": ["job", "work", "career", "office", "colleague"],
    "hobbies": ["hobby", "painting", "pottery", "music", "art", "camping", "hiking"],
    "events": ["event", "parade", "conference", "meeting", "gathering", "party"],
    "emotions": ["feel", "feeling", "felt", "emotion", "emotional"],
    "growth": ["grow", "growing", "progress", "journey", "milestone"],
    "challenges": ["challenge", "difficult", "hard", "struggle", "problem"],
}


@dataclass
class TemporalEvent:
    """An event anchored to a specific time."""
    event_description: str
    date_text: str  # Original date string like "7 May 2023", "last week"
    speaker: str
    session_date: str | None = None  # The session's date for context
    message_content: str = ""


@dataclass
class EmotionalMoment:
    """A moment with emotional significance."""
    content: str
    emotion: str
    valence: float  # -1.0 to 1.0
    intensity: float  # 0.0 to 1.0
    timestamp: datetime | None
    speaker: str


@dataclass
class TopicThread:
    """A topic that runs through the conversation."""
    topic: str
    mentions: list[str] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    message_count: int = 0


@dataclass
class ParticipantProfile:
    """Understanding of a conversation participant."""
    name: str
    message_count: int = 0
    topics_discussed: list[str] = field(default_factory=list)
    emotional_moments: list[EmotionalMoment] = field(default_factory=list)
    key_facts: list[str] = field(default_factory=list)


@dataclass
class ConversationSummary:
    """The Sun - holistic understanding of a conversation."""

    # WHO
    participants: dict[str, ParticipantProfile] = field(default_factory=dict)
    relationship_type: str = "unknown"  # friends, family, professional, etc.

    # WHAT
    topics: dict[str, TopicThread] = field(default_factory=dict)
    main_narrative: str = ""

    # EMOTIONAL
    emotional_arc: list[tuple[datetime, str, float]] = field(default_factory=list)
    peak_moments: list[EmotionalMoment] = field(default_factory=list)
    overall_sentiment: float = 0.0

    # TEMPORAL
    first_message: datetime | None = None
    last_message: datetime | None = None
    session_count: int = 0
    message_count: int = 0

    # TEMPORAL EVENTS - Critical for answering "When did X happen?"
    temporal_events: list[TemporalEvent] = field(default_factory=list)
    session_dates: dict[int, str] = field(default_factory=dict)  # session_id -> date string

    # KEY FACTS (extracted important information)
    key_facts: list[str] = field(default_factory=list)

    def to_context_string(self) -> str:
        """Convert summary to context string for retrieval enhancement."""
        lines = []

        # Participants
        if self.participants:
            names = list(self.participants.keys())
            lines.append(f"Participants: {', '.join(names)}")
            lines.append(f"Relationship: {self.relationship_type}")

        # Main narrative
        if self.main_narrative:
            lines.append(f"Narrative: {self.main_narrative}")

        # Key topics
        if self.topics:
            topic_list = sorted(self.topics.items(),
                              key=lambda x: x[1].message_count,
                              reverse=True)[:5]
            topics_str = ", ".join(t[0] for t in topic_list)
            lines.append(f"Main topics: {topics_str}")

        # Key facts
        if self.key_facts:
            for fact in self.key_facts[:10]:
                lines.append(f"- {fact}")

        return "\n".join(lines)


# =============================================================================
# CONVERSATION ANALYZER
# =============================================================================

class ConversationSun:
    """The Sun - provides holistic conversation understanding.

    Like the Sun in our solar system, this layer:
    1. Illuminates the entire conversation landscape
    2. Provides the gravitational center that memories orbit around
    3. Gives energy (context) to the Moon (Electron Retrieval)
    """

    def __init__(self):
        self._summaries: dict[str, ConversationSummary] = {}

    def analyze_conversation(
        self,
        session_key: str,
        messages: list[dict[str, Any]],
    ) -> ConversationSummary:
        """Analyze a full conversation to build understanding.

        Args:
            session_key: Unique identifier for this conversation
            messages: List of message dicts with 'text', 'speaker', 'timestamp'

        Returns:
            ConversationSummary with holistic understanding
        """
        summary = ConversationSummary()
        summary.message_count = len(messages)

        # Track unique sessions
        sessions_seen = set()

        for msg in messages:
            text = msg.get("text", msg.get("content", ""))
            speaker = msg.get("speaker", msg.get("producer_id", "Unknown"))
            timestamp = msg.get("timestamp", msg.get("created_at"))
            session = msg.get("session", 0)

            sessions_seen.add(session)

            # Parse timestamp
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp)
                except:
                    timestamp = None

            # Update temporal bounds
            if timestamp:
                if summary.first_message is None or timestamp < summary.first_message:
                    summary.first_message = timestamp
                if summary.last_message is None or timestamp > summary.last_message:
                    summary.last_message = timestamp

            # Update participant profile
            if speaker not in summary.participants:
                summary.participants[speaker] = ParticipantProfile(name=speaker)
            profile = summary.participants[speaker]
            profile.message_count += 1

            # Analyze emotions
            emotions = self._extract_emotions(text)
            for emotion, valence, intensity in emotions:
                moment = EmotionalMoment(
                    content=text[:200],
                    emotion=emotion,
                    valence=valence,
                    intensity=intensity,
                    timestamp=timestamp,
                    speaker=speaker,
                )
                profile.emotional_moments.append(moment)
                summary.emotional_arc.append((timestamp, emotion, valence))

                # Track peak moments
                if abs(valence * intensity) > 0.6:
                    summary.peak_moments.append(moment)

            # Analyze topics
            topics = self._extract_topics(text)
            for topic in topics:
                if topic not in summary.topics:
                    summary.topics[topic] = TopicThread(topic=topic)
                thread = summary.topics[topic]
                thread.mentions.append(text[:100])
                thread.message_count += 1
                if timestamp:
                    if thread.first_seen is None or timestamp < thread.first_seen:
                        thread.first_seen = timestamp
                    if thread.last_seen is None or timestamp > thread.last_seen:
                        thread.last_seen = timestamp

                if topic not in profile.topics_discussed:
                    profile.topics_discussed.append(topic)

            # Extract key facts
            facts = self._extract_key_facts(text, speaker)
            summary.key_facts.extend(facts)
            profile.key_facts.extend(facts)

            # Extract temporal events - CRITICAL for "When did X happen?" questions
            session_time = msg.get("session_time", "")
            temporal_events = self._extract_temporal_events(text, speaker, session_time)
            summary.temporal_events.extend(temporal_events)

            # Track session dates
            if session_time and session not in summary.session_dates:
                summary.session_dates[session] = session_time

        summary.session_count = len(sessions_seen)

        # Determine relationship type
        summary.relationship_type = self._infer_relationship(summary)

        # Generate main narrative
        summary.main_narrative = self._generate_narrative(summary)

        # Calculate overall sentiment
        if summary.emotional_arc:
            total_valence = sum(v for _, _, v in summary.emotional_arc)
            summary.overall_sentiment = total_valence / len(summary.emotional_arc)

        # Store and return
        self._summaries[session_key] = summary
        return summary

    def get_summary(self, session_key: str) -> ConversationSummary | None:
        """Get cached summary for a conversation."""
        return self._summaries.get(session_key)

    def enhance_query(
        self,
        query: str,
        session_key: str,
    ) -> tuple[str, dict[str, float]]:
        """Enhance a query with conversation-level understanding.

        Returns:
            Tuple of (enhanced_query, boost_factors)
        """
        summary = self._summaries.get(session_key)
        if not summary:
            return query, {}

        boost_factors = {}

        # Check if query mentions specific participants
        for name in summary.participants:
            if name.lower() in query.lower():
                boost_factors[f"speaker:{name}"] = 1.5

        # Check if query relates to peak emotional moments
        query_emotions = self._extract_emotions(query)
        if query_emotions:
            boost_factors["emotional_relevance"] = 1.3

        # Check topic relevance
        query_topics = self._extract_topics(query)
        for topic in query_topics:
            if topic in summary.topics:
                boost_factors[f"topic:{topic}"] = 1.2 + (summary.topics[topic].message_count * 0.1)

        # Build enhanced query with context
        context_hint = summary.to_context_string()
        enhanced = f"{query}\n\n[Conversation context: {context_hint[:500]}]"

        return enhanced, boost_factors

    def _extract_emotions(self, text: str) -> list[tuple[str, float, float]]:
        """Extract emotions from text.

        Returns list of (emotion_name, valence, intensity)
        """
        text_lower = text.lower()
        emotions = []

        for marker, (emotion, valence) in EMOTION_MARKERS.items():
            if marker in text_lower:
                # Count occurrences for intensity
                count = text_lower.count(marker)
                intensity = min(1.0, 0.5 + count * 0.2)
                emotions.append((emotion, valence, intensity))

        return emotions

    def _extract_topics(self, text: str) -> list[str]:
        """Extract topic categories from text."""
        text_lower = text.lower()
        found_topics = []

        for topic, markers in TOPIC_MARKERS.items():
            for marker in markers:
                if marker in text_lower:
                    found_topics.append(topic)
                    break

        return found_topics

    def _extract_key_facts(self, text: str, speaker: str) -> list[str]:
        """Extract key facts from text."""
        facts = []

        # Look for identity statements
        identity_patterns = [
            r"I am (\w+)",
            r"I'm (\w+)",
            r"I work as (\w+)",
            r"I live in (\w+)",
            r"I have (\d+) (\w+)",
        ]

        for pattern in identity_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    fact = f"{speaker} {' '.join(match)}"
                else:
                    fact = f"{speaker} is {match}"
                facts.append(fact)

        # Look for event statements
        event_patterns = [
            r"went to (?:the |a )?([^.!?]+)",
            r"attended (?:the |a )?([^.!?]+)",
            r"joined (?:the |a )?([^.!?]+)",
            r"started ([^.!?]+)",
        ]

        for pattern in event_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match) < 50:  # Avoid overly long matches
                    facts.append(f"{speaker} {match}")

        return facts[:3]  # Limit per message

    def _extract_temporal_events(
        self,
        text: str,
        speaker: str,
        session_date: str | None = None,
    ) -> list[TemporalEvent]:
        """Extract temporal events - things that happened at specific times.

        CRITICAL for answering "When did X happen?" questions.
        """
        events = []

        # Date patterns to look for
        date_patterns = [
            # Explicit dates: "7 May 2023", "May 7, 2023", "07/05/2023"
            r'\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b',
            r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b',
            r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b',
            r'\b(\d{4}-\d{2}-\d{2})\b',
            # Month + year: "May 2023"
            r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b',
            # Relative dates that are important
            r'\b(last\s+(?:week|month|year|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b',
            r'\b(yesterday|today|tomorrow)\b',
            r'\b(this\s+(?:morning|afternoon|evening|week|month|year))\b',
        ]

        # Event action patterns that indicate something happened
        event_patterns = [
            r'(?:I\s+)?went\s+to\s+(?:the\s+|a\s+)?([^.!?,]+)',
            r'(?:I\s+)?attended\s+(?:the\s+|a\s+)?([^.!?,]+)',
            r'(?:I\s+)?visited\s+(?:the\s+|a\s+)?([^.!?,]+)',
            r'(?:I\s+)?joined\s+(?:the\s+|a\s+)?([^.!?,]+)',
            r'(?:I\s+)?started\s+([^.!?,]+)',
            r'(?:I\s+)?met\s+(?:with\s+)?([^.!?,]+)',
            r'(?:I\s+)?had\s+(?:a\s+|the\s+)?([^.!?,]+)',
            r'(?:I\s+)?celebrated\s+([^.!?,]+)',
            r'(?:I\s+)?bought\s+(?:a\s+|the\s+)?([^.!?,]+)',
            r'(?:I\s+)?moved\s+to\s+([^.!?,]+)',
            r'(?:I\s+)?came\s+out\s+(?:to\s+|as\s+)?([^.!?,]+)?',
        ]

        # Find dates in the text
        found_dates = []
        for pattern in date_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            found_dates.extend(matches)

        # Use session_date as fallback if no explicit date found
        date_to_use = found_dates[0] if found_dates else session_date

        # Find events and pair with dates
        for pattern in event_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if match and len(match) < 100:  # Sanity check
                    event_desc = match.strip()
                    if event_desc:
                        events.append(TemporalEvent(
                            event_description=event_desc,
                            date_text=date_to_use or "",
                            speaker=speaker,
                            session_date=session_date,
                            message_content=text[:200],
                        ))

        # Also create event from session date context even if no explicit event pattern
        # This captures "On 7 May 2023, they discussed X" type scenarios
        if session_date and not events:
            # Check for significant content in the message
            significant_markers = ["went", "visited", "attended", "joined", "started",
                                   "met", "had", "celebrated", "came out", "first time"]
            text_lower = text.lower()
            for marker in significant_markers:
                if marker in text_lower:
                    events.append(TemporalEvent(
                        event_description=f"{speaker}'s message about: {text[:100]}",
                        date_text=session_date,
                        speaker=speaker,
                        session_date=session_date,
                        message_content=text[:200],
                    ))
                    break

        return events[:5]  # Limit per message

    def _infer_relationship(self, summary: ConversationSummary) -> str:
        """Infer the relationship type between participants."""
        if len(summary.participants) != 2:
            return "group"

        # Check for family indicators
        family_words = ["mom", "dad", "parent", "child", "brother", "sister", "family"]
        all_text = " ".join(
            " ".join(m.content for m in p.emotional_moments)
            for p in summary.participants.values()
        ).lower()

        if any(word in all_text for word in family_words):
            return "family"

        # Check for professional indicators
        work_words = ["meeting", "project", "deadline", "client", "boss", "colleague"]
        if any(word in all_text for word in work_words):
            return "professional"

        # Default to friends
        return "friends"

    def _generate_narrative(self, summary: ConversationSummary) -> str:
        """Generate a brief narrative of the conversation."""
        parts = []

        # Participants
        names = list(summary.participants.keys())
        if len(names) == 2:
            parts.append(f"A conversation between {names[0]} and {names[1]}")
        elif names:
            parts.append(f"A conversation involving {', '.join(names)}")

        # Main topics
        if summary.topics:
            top_topics = sorted(summary.topics.items(),
                              key=lambda x: x[1].message_count,
                              reverse=True)[:3]
            topics_str = ", ".join(t[0] for t in top_topics)
            parts.append(f"discussing {topics_str}")

        # Emotional arc
        if summary.overall_sentiment > 0.3:
            parts.append("with overall positive sentiment")
        elif summary.overall_sentiment < -0.3:
            parts.append("with challenging emotional content")

        return " ".join(parts) + "."


# =============================================================================
# INTEGRATION HELPERS
# =============================================================================

def create_conversation_sun() -> ConversationSun:
    """Create a new ConversationSun instance."""
    return ConversationSun()


def analyze_locomo_conversation(
    sun: ConversationSun,
    session_key: str,
    conversation_data: dict,
) -> ConversationSummary:
    """Analyze a LoCoMo-format conversation.

    Converts LoCoMo format to messages and analyzes.
    """
    # Get the conversation sub-dict (LoCoMo structure)
    conversation = conversation_data.get("conversation", conversation_data)
    messages = []
    session_idx = 1  # Sessions start at 1, not 0

    while True:
        sess_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if sess_key not in conversation:
            break

        session_time = conversation.get(datetime_key, "")
        session_messages = conversation[sess_key]

        for msg in session_messages:
            if isinstance(msg, dict) and 'text' in msg:
                messages.append({
                    "text": msg.get("text", ""),
                    "speaker": msg.get("speaker", "Unknown"),
                    "session": session_idx,
                    "session_time": session_time,
                })

        session_idx += 1

    return sun.analyze_conversation(session_key, messages)

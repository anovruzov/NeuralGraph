"""
Memory Shards Module for Enhanced LoCoMo Benchmark v2.0

This module implements a partitioned memory architecture that organizes
conversation history into semantic shards for more targeted retrieval.

Shard Types:
- persona_profile: Static traits, identity, preferences
- conversation_log: Chronological conversation history
- factual_knowledge: Extracted facts, events, entities
- goal_history: Plans, goals, intentions, outcomes
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import requests
import numpy as np


# Domain to Shard mapping
class ShardType(str, Enum):
    """Types of memory shards for partitioned retrieval."""
    PERSONA_PROFILE = "persona_profile"     # Identity, beliefs, preferences
    CONVERSATION_LOG = "conversation_log"   # All messages with timestamps
    FACTUAL_KNOWLEDGE = "factual_knowledge" # Facts, events, entities
    GOAL_HISTORY = "goal_history"           # Plans, goals, outcomes


# Map existing domains to shard types
DOMAIN_TO_SHARD = {
    "personal": ShardType.PERSONA_PROFILE,
    "relationships": ShardType.PERSONA_PROFILE,
    "events": ShardType.FACTUAL_KNOWLEDGE,
    "location": ShardType.FACTUAL_KNOWLEDGE,
    "work": ShardType.CONVERSATION_LOG,
    "hobbies": ShardType.CONVERSATION_LOG,
    "finance": ShardType.FACTUAL_KNOWLEDGE,
    "health": ShardType.PERSONA_PROFILE,
    "general": ShardType.CONVERSATION_LOG,
}

# Question keywords to shard routing
QUESTION_TO_SHARD = {
    # Persona Profile indicators
    "identity": ShardType.PERSONA_PROFILE,
    "personality": ShardType.PERSONA_PROFILE,
    "preference": ShardType.PERSONA_PROFILE,
    "like": ShardType.PERSONA_PROFILE,
    "favorite": ShardType.PERSONA_PROFILE,
    "relationship": ShardType.PERSONA_PROFILE,
    "married": ShardType.PERSONA_PROFILE,
    "single": ShardType.PERSONA_PROFILE,
    "children": ShardType.PERSONA_PROFILE,
    "family": ShardType.PERSONA_PROFILE,

    # Factual Knowledge indicators
    "when": ShardType.FACTUAL_KNOWLEDGE,
    "where": ShardType.FACTUAL_KNOWLEDGE,
    "date": ShardType.FACTUAL_KNOWLEDGE,
    "time": ShardType.FACTUAL_KNOWLEDGE,
    "event": ShardType.FACTUAL_KNOWLEDGE,
    "visit": ShardType.FACTUAL_KNOWLEDGE,
    "buy": ShardType.FACTUAL_KNOWLEDGE,
    "purchase": ShardType.FACTUAL_KNOWLEDGE,

    # Goal History indicators
    "plan": ShardType.GOAL_HISTORY,
    "planning": ShardType.GOAL_HISTORY,
    "goal": ShardType.GOAL_HISTORY,
    "want": ShardType.GOAL_HISTORY,
    "intend": ShardType.GOAL_HISTORY,
    "future": ShardType.GOAL_HISTORY,
}


@dataclass
class MemoryMessage:
    """A single memory message with metadata."""
    text: str
    speaker: str
    session: int
    session_time: str
    domain: Optional[str] = None
    shard_type: Optional[ShardType] = None
    embedding: Optional[list[float]] = None
    message_index: int = 0  # Global index for surrounding context

    def to_context_line(self) -> str:
        """Format message for context."""
        return f"[Session {self.session} - {self.session_time}] {self.speaker}: {self.text}"


@dataclass
class MemoryShard:
    """Base class for memory shards."""
    shard_type: ShardType
    messages: list[MemoryMessage] = field(default_factory=list)

    def add_message(self, msg: MemoryMessage):
        """Add a message to this shard."""
        msg.shard_type = self.shard_type
        self.messages.append(msg)

    def get_context(self, max_chars: int = 8000) -> str:
        """Format shard messages as context string."""
        lines = []
        total_chars = 0
        for msg in self.messages:
            line = msg.to_context_line()
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line) + 1
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.messages)


@dataclass
class PersonaProfile(MemoryShard):
    """Shard for static traits, identity, and preferences."""
    shard_type: ShardType = ShardType.PERSONA_PROFILE


@dataclass
class ConversationLog(MemoryShard):
    """Shard for chronological conversation history."""
    shard_type: ShardType = ShardType.CONVERSATION_LOG


@dataclass
class FactualKnowledge(MemoryShard):
    """Shard for extracted facts, events, and entities."""
    shard_type: ShardType = ShardType.FACTUAL_KNOWLEDGE


@dataclass
class GoalHistory(MemoryShard):
    """Shard for plans, goals, intentions, and outcomes."""
    shard_type: ShardType = ShardType.GOAL_HISTORY


@dataclass
class MemoryShardSystem:
    """Complete memory shard system for a conversation."""
    persona_profile: PersonaProfile = field(default_factory=PersonaProfile)
    conversation_log: ConversationLog = field(default_factory=ConversationLog)
    factual_knowledge: FactualKnowledge = field(default_factory=FactualKnowledge)
    goal_history: GoalHistory = field(default_factory=GoalHistory)
    all_messages: list[MemoryMessage] = field(default_factory=list)

    def get_shard(self, shard_type: ShardType) -> MemoryShard:
        """Get shard by type."""
        shard_map = {
            ShardType.PERSONA_PROFILE: self.persona_profile,
            ShardType.CONVERSATION_LOG: self.conversation_log,
            ShardType.FACTUAL_KNOWLEDGE: self.factual_knowledge,
            ShardType.GOAL_HISTORY: self.goal_history,
        }
        return shard_map.get(shard_type, self.conversation_log)

    def add_message(self, msg: MemoryMessage, domain: Optional[str] = None):
        """Add message to appropriate shard based on domain."""
        # Always add to all_messages for full context
        msg.message_index = len(self.all_messages)
        self.all_messages.append(msg)

        # Route to appropriate shard
        if domain:
            shard_type = DOMAIN_TO_SHARD.get(domain, ShardType.CONVERSATION_LOG)
        else:
            shard_type = ShardType.CONVERSATION_LOG

        shard = self.get_shard(shard_type)
        shard.add_message(msg)

    def route_question(self, question: str) -> list[ShardType]:
        """Determine which shards are relevant for a question."""
        question_lower = question.lower()
        relevant_shards = set()

        # Check for keyword matches
        for keyword, shard_type in QUESTION_TO_SHARD.items():
            if keyword in question_lower:
                relevant_shards.add(shard_type)

        # Default to all shards if no match
        if not relevant_shards:
            return [ShardType.PERSONA_PROFILE, ShardType.FACTUAL_KNOWLEDGE,
                    ShardType.CONVERSATION_LOG]

        return list(relevant_shards)

    def get_relevant_messages(self, question: str,
                               top_k: int = 30) -> list[MemoryMessage]:
        """Get messages from relevant shards for a question."""
        relevant_shards = self.route_question(question)
        messages = []

        for shard_type in relevant_shards:
            shard = self.get_shard(shard_type)
            messages.extend(shard.messages)

        # Deduplicate while preserving order
        seen = set()
        unique_messages = []
        for msg in messages:
            key = (msg.session, msg.text[:50])
            if key not in seen:
                seen.add(key)
                unique_messages.append(msg)

        return unique_messages[:top_k]

    def get_stats(self) -> dict:
        """Get statistics about shard distribution."""
        return {
            "total_messages": len(self.all_messages),
            "persona_profile": len(self.persona_profile),
            "conversation_log": len(self.conversation_log),
            "factual_knowledge": len(self.factual_knowledge),
            "goal_history": len(self.goal_history),
        }


def extract_messages_from_conversation(item: dict) -> list[MemoryMessage]:
    """Extract all messages from a LoCoMo conversation item."""
    conversation = item.get("conversation", item)
    messages = []

    session_idx = 1
    msg_index = 0
    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if session_key not in conversation:
            break

        session_time = conversation.get(datetime_key, "Unknown")
        session_messages = conversation[session_key]

        for msg in session_messages:
            if isinstance(msg, dict) and 'text' in msg:
                memory_msg = MemoryMessage(
                    text=msg.get("text", ""),
                    speaker=msg.get("speaker", "Unknown"),
                    session=session_idx,
                    session_time=session_time,
                    domain=msg.get("domain"),  # From preprocessed data
                    message_index=msg_index
                )
                messages.append(memory_msg)
                msg_index += 1

        session_idx += 1

    return messages


def populate_shard_system(item: dict) -> MemoryShardSystem:
    """Create a populated MemoryShardSystem from a LoCoMo conversation."""
    system = MemoryShardSystem()
    messages = extract_messages_from_conversation(item)

    for msg in messages:
        system.add_message(msg, domain=msg.domain)

    return system


def classify_message_domain_heuristic(text: str) -> str:
    """Simple heuristic domain classification (fallback if no Ollama)."""
    text_lower = text.lower()

    # Personal/Identity
    if any(w in text_lower for w in ["i am", "i'm", "my name", "identity", "transgender", "lgbtq"]):
        return "personal"

    # Relationships
    if any(w in text_lower for w in ["husband", "wife", "kids", "children", "friend", "family", "married"]):
        return "relationships"

    # Events
    if any(w in text_lower for w in ["went to", "visited", "attended", "conference", "meeting", "party"]):
        return "events"

    # Location
    if any(w in text_lower for w in ["travel", "moved", "city", "country", "location", "place"]):
        return "location"

    # Work
    if any(w in text_lower for w in ["work", "job", "career", "office", "business", "project"]):
        return "work"

    # Hobbies
    if any(w in text_lower for w in ["hobby", "paint", "art", "music", "sport", "game", "read"]):
        return "hobbies"

    # Health
    if any(w in text_lower for w in ["health", "doctor", "sick", "exercise", "wellness"]):
        return "health"

    # Finance
    if any(w in text_lower for w in ["money", "buy", "purchase", "cost", "price", "budget"]):
        return "finance"

    return "general"


def get_embedding_ollama(text: str, base_url: str = "http://localhost:11434",
                         model: str = "nomic-embed-text") -> Optional[list[float]]:
    """Get embedding from Ollama."""
    try:
        response = requests.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=30
        )
        response.raise_for_status()
        return response.json()["embedding"]
    except Exception as e:
        print(f"  Embedding error: {e}")
        return None


def compute_embeddings_for_shard(shard: MemoryShard,
                                  base_url: str = "http://localhost:11434"):
    """Compute embeddings for all messages in a shard."""
    for msg in shard.messages:
        if msg.embedding is None:
            msg.embedding = get_embedding_ollama(msg.text, base_url)


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if vec1 is None or vec2 is None:
        return 0.0
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    dot = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def semantic_search_shard(question: str, shard: MemoryShard,
                          top_k: int = 10,
                          base_url: str = "http://localhost:11434") -> list[MemoryMessage]:
    """Search a shard using semantic similarity."""
    # Get question embedding
    question_embedding = get_embedding_ollama(question, base_url)
    if question_embedding is None:
        return shard.messages[:top_k]

    # Compute similarities
    scored_messages = []
    for msg in shard.messages:
        if msg.embedding is None:
            continue
        score = cosine_similarity(question_embedding, msg.embedding)
        scored_messages.append((score, msg))

    # Sort by score
    scored_messages.sort(key=lambda x: x[0], reverse=True)

    return [msg for _, msg in scored_messages[:top_k]]


# =============================================================================
# EVIDENCE TRACING
# =============================================================================

@dataclass
class EvidenceTrace:
    """Log of evidence used for an answer."""
    question: str
    category: str
    retrieved_messages: list[dict] = field(default_factory=list)
    shard_sources: list[str] = field(default_factory=list)
    bm25_scores: list[float] = field(default_factory=list)
    semantic_scores: list[float] = field(default_factory=list)
    final_context: str = ""
    answer: str = ""
    gold_answer: str = ""
    score: int = 0
    is_correct: bool = False
    is_hallucination: bool = False
    is_refusal: bool = False
    grounded: bool = True

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "question": self.question,
            "category": self.category,
            "retrieved_count": len(self.retrieved_messages),
            "shard_sources": self.shard_sources,
            "context_length": len(self.final_context),
            "answer": self.answer,
            "gold_answer": self.gold_answer,
            "score": self.score,
            "is_correct": self.is_correct,
            "is_hallucination": self.is_hallucination,
            "is_refusal": self.is_refusal,
            "grounded": self.grounded,
        }

"""Plain data records for the chat-memory store.

Every record is a frozen-ish dataclass with ``to_dict``; the store is the only writer.
Datetimes are ISO-8601 strings in UTC (``"2026-09-17T08:00:00+00:00"``) everywhere they are
persisted so that SQLite string comparison orders them correctly.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

MEMORY_KINDS = (
    "fact",          # stable attribute: "Ali works as a nurse"
    "preference",    # likes/dislikes/habits: "Ali prefers dark roast coffee"
    "event",         # something that happened at a time: "Ali ran the Berlin half marathon on 2026-04-12"
    "plan",          # intention / future: "Ali plans to visit Tokyo in October 2026"
    "relationship",  # between people/orgs: "Ali's sister is named Leyla"
    "opinion",       # belief/attitude: "Ali thinks remote work is more productive"
    "identity",      # who someone is: name, role, location, occupation
    "task",          # open item / commitment: "Ali needs to renew the passport before June"
    "other",
)

MEMORY_STATUS = ("active", "superseded", "retracted")
MESSAGE_STATUS = ("pending", "processing", "processed", "skipped", "failed")
JOB_STATUS = ("queued", "leased", "done", "failed", "dead")
LINK_TYPES = ("supersedes", "contradicts", "related", "elaborates", "same_event")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return iso(utcnow())  # type: ignore[return-value]


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


@dataclass
class ChatMessage:
    message_id: str
    chat_id: str
    seq: int
    speaker: str
    role: str
    text: str
    sent_at: str | None
    ingested_at: str
    content_hash: str
    status: str = "pending"
    processed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Memory:
    memory_id: str
    text: str
    kind: str
    subject: str            # canonical entity key the memory is about
    subject_name: str       # display name for the subject
    speaker: str            # who said it
    chat_id: str            # chat where it was (first) observed
    importance: float
    confidence: float
    event_time: str | None          # ISO date or datetime the memory is about, if any
    event_time_precision: str       # "day" | "month" | "year" | "none"
    observed_at: str                # sent_at of the source message (or ingest time)
    created_at: str
    updated_at: str
    status: str = "active"
    superseded_by: str | None = None
    version: int = 1
    text_hash: str = ""
    embedding: list[float] | None = None
    access_count: int = 0
    last_accessed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # populated on read when requested
    source_message_ids: list[str] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)

    def to_dict(self, include_embedding: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_embedding:
            d.pop("embedding", None)
        return d


@dataclass
class Entity:
    entity_id: str          # canonical key
    name: str               # display name
    type: str = ""
    mention_count: int = 0
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Relation:
    relation_id: str
    subject_id: str
    predicate: str
    object_id: str
    confidence: float
    chat_id: str
    memory_id: str | None
    message_id: str | None
    status: str = "active"
    observation_count: int = 1
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryLink:
    source_id: str
    target_id: str
    link_type: str
    weight: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Job:
    job_id: int
    kind: str               # "extract" | "maintain"
    chat_id: str | None
    ref_id: str | None      # message_id for extract jobs
    seq: int | None
    dedupe_key: str | None
    status: str
    attempts: int
    max_attempts: int
    available_at: str
    leased_until: str | None
    worker_id: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    finished_at: str | None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievedMemory:
    memory: Memory
    score: float
    channels: dict[str, float] = field(default_factory=dict)   # per-channel contribution
    sources: list[ChatMessage] = field(default_factory=list)   # provenance
    entities: list[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": self.memory.to_dict(),
            "score": self.score,
            "channels": self.channels,
            "sources": [s.to_dict() for s in self.sources],
            "entities": self.entities,
            "explanation": self.explanation,
        }

"""Plain records for Mycelic. The store is the only writer; everything is JSON-serialisable via ``to_dict``.

Timestamps are ISO-8601 UTC strings with second precision (``2026-09-24T10:00:00+00:00``) so SQLite string
comparison orders them, the same convention ``NeuralGraph.chat_memory.models`` uses.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .hierarchy import LAYERS

MEMORY_KINDS = ("fact", "observation", "event", "risk", "decision", "plan", "preference", "other")
MEMORY_STATUS = ("active", "superseded", "retracted")
VISIBILITY = ("team", "org")
OPERATORS = ("agent_observation", "topic_consolidation", "slot_composition")
EVENT_KINDS = ("memory.observed", "memory.derived", "memory.retracted", "agent.event",
               "agent.registered", "agent.revoked", "agent.key_rotated", "rule.upserted", "rule.deleted")
EVENT_STATUS = ("pending", "published", "applied", "failed")
AGENT_STATUS = ("active", "revoked")
DEFAULT_AGENT_SCOPES = ("memory:read", "memory:write", "events:write", "lineage:read")
ALL_SCOPES = DEFAULT_AGENT_SCOPES + ("admin",)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def derived_memory_id(*, operator: str, scope: str, key: str, parent_ids: list[str] | tuple[str, ...]) -> str:
    """Deterministic id for an aggregated memory.

    The id is a function of *what was aggregated* (operator, target unit, topic/rule key, sorted parents), never of
    time or process identity.  Replaying the event log therefore reproduces exactly the same derived ids, which is
    what makes ``apply`` idempotent and a rebuild from JetStream byte-comparable with the state it replaces.
    """
    digest = hashlib.sha256("|".join([operator, scope, key, *sorted(parent_ids)]).encode("utf-8")).hexdigest()
    return f"mem_d{digest[:22]}"


def content_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


@dataclass
class Agent:
    agent_id: str
    org_id: str
    display_name: str
    path: str                       # full six-segment path, see hierarchy.AgentPath
    scopes: list[str]
    key_prefix: str                 # first characters of the key, for display/rotation only
    status: str = "active"
    created_at: str = ""
    last_seen_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Memory:
    memory_id: str
    org_id: str
    layer: str                      # one of hierarchy.LAYERS
    scope: str                      # unit path the memory belongs to (agent path for layer 'agent')
    text: str
    topic: str | None
    slot: str | None
    entity: str | None
    kind: str
    confidence: float
    support: int                    # distinct agents whose observations are in the lineage
    independent_teams: int          # distinct teams in the lineage (independent failure domains)
    producer_id: str                # agent id, or 'mycelic' for derived memories
    operator: str                   # OPERATORS
    rule_id: str | None
    event_id: str | None            # event that created/published this memory
    visibility: str = "team"
    status: str = "active"
    superseded_by: str | None = None
    created_at: str = ""            # producer time (agent's observed_at for raw memories)
    applied_at: str | None = None   # when the consumer applied the event that carries it
    source_event_ids: list[str] = field(default_factory=list)
    local_ref: str | None = None    # the agent's own local memory id (opaque to Mycelic)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def layer_index(self) -> int:
        return LAYERS.index(self.layer)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LineageEdge:
    child_id: str
    parent_id: str
    contributed_by: str             # agent id (raw parent) or 'mycelic'
    parent_layer: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventRecord:
    event_id: str
    kind: str                       # EVENT_KINDS
    org_id: str
    agent_id: str | None
    subject: str                    # NATS subject
    payload: dict[str, Any]
    status: str = "pending"
    js_seq: int | None = None
    attempts: int = 0
    last_error: str | None = None
    created_at: str = ""
    published_at: str | None = None
    applied_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Rule:
    """A slot-composition rule: a conclusion that only exists once every required slot is covered."""

    rule_id: str
    target_layer: str
    required_slots: list[str]
    conclusion: str                 # template with {entity} and {slot:<name>} placeholders
    topic_prefix: str | None = None # only memories whose topic starts with this feed the rule
    min_agents: int = 2
    min_teams: int = 1
    kind: str = "risk"
    org_id: str | None = None       # None = every organization on this deployment
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

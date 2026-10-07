"""Plain records for Mycelic. The store is the only writer; everything is JSON-serialisable via ``to_dict``.

Timestamps are ISO-8601 UTC strings with second precision (``2026-09-24T10:00:00+00:00``) so SQLite string
comparison orders them, the same convention ``NeuralGraph.chat_memory.models`` uses.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
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
#: a ``{slot:<name>}`` placeholder in a rule's conclusion template (the slot-name charset of the API)
SLOT_PLACEHOLDER_RE = re.compile(r"\{slot:([A-Za-z0-9_.:-]{1,100})\}")


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


def canonical_label(s: str | None) -> str | None:
    """The one spelling of a topic, slot or entity: NFKC, case-folded, NFKC again, whitespace runs collapsed to one
    space and trimmed.  None for None and for a label that is only whitespace.

    ``'SD-9'``, ``' sd-9 '`` and the full-width ``'ＳＤ－９'`` are the same label; scripts without case (CJK) are
    unchanged and others are lower-cased, never stripped.  Folding can leave text that is not NFKC (U+01F0 folds
    to ``j`` + U+030C, which NFKC composes back), hence the second pass; the result is a fixed point:
    ``canonical_label(canonical_label(x)) == canonical_label(x)``.
    """
    if s is None:
        return None
    if not isinstance(s, str):
        raise TypeError(f"a label must be a string, not {type(s).__name__}")
    return " ".join(unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", s).casefold()).split()) or None


def _is_count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _max_per_layer(a: Any, b: Any) -> Any:
    """Merge two ``{layer: n}`` maps whose slots became one label: the larger count per layer.  A malformed side is
    kept as it is, for the validator to refuse."""
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return b if isinstance(a, dict) else a
    out = dict(a)
    for layer, n in b.items():
        m = out.get(layer, n)
        out[layer] = max(m, n) if _is_count(m) and _is_count(n) else (n if _is_count(m) else m)
    return out


def canonical_rule_body(body: dict[str, Any]) -> dict[str, Any]:
    """A shallow copy of a rule body whose labels are canonical (see :func:`canonical_label`): the required slots
    (de-duplicated, in order), ``emits_slot``, ``topic_prefix``, ``emits_topic``, the slots of ``min_units`` (two
    that become one keep the larger count per layer) and the ``{slot:...}`` placeholders of ``conclusion``.  Values
    of the wrong type are left as they are for the validator to refuse."""
    out = dict(body)
    slots = out.get("required_slots")
    if isinstance(slots, list) and all(isinstance(x, str) for x in slots):
        out["required_slots"] = list(dict.fromkeys(canonical_label(x) for x in slots))
    for key in ("emits_slot", "topic_prefix", "emits_topic"):
        if isinstance(out.get(key), str):
            out[key] = canonical_label(out[key])
    units = out.get("min_units")
    if isinstance(units, dict) and all(isinstance(k, str) for k in units):
        merged: dict[Any, Any] = {}
        for slot, per in units.items():
            key = canonical_label(slot)
            merged[key] = _max_per_layer(merged[key], per) if key in merged else per
        out["min_units"] = merged
    if isinstance(out.get("conclusion"), str):
        out["conclusion"] = SLOT_PLACEHOLDER_RE.sub(lambda m: "{slot:%s}" % canonical_label(m.group(1)), out["conclusion"])
    return out


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
    """A slot-composition rule: a conclusion that only exists once every required slot is covered.

    Rules compose.  A rule whose ``emits_slot`` is set produces conclusions that carry that slot, so a higher
    rule can list it among its ``required_slots`` and take conclusions as evidence (``sources`` names the
    operators whose memories may fill a slot).  ``min_units`` demands corroboration of a slot across
    organizational units (``{"supply_risk": {"region": 2}}``: supply risk reported by at least two regions);
    the units are counted over the memories that become parents, which without ``corroborate`` is the single
    strongest memory per slot, so a count above one needs ``corroborate`` unless that one memory itself spans
    the units (a consolidation, or an already corroborated conclusion).  With ``corroborate`` every memory that
    fills a required slot becomes evidence, not only the strongest one per slot.  That is what strategic synthesis is: a
    conclusion whose parents are other units' conclusions.
    """

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
    sources: list[str] = field(default_factory=lambda: ["agent_observation"])   # operators that may fill a slot
    emits_slot: str | None = None   # the slot the conclusion carries, so higher rules can consume it
    emits_topic: str | None = None  # the topic the conclusion carries (default: topic_prefix or rule_id)
    min_units: dict[str, dict[str, int]] = field(default_factory=dict)          # slot -> layer -> distinct units
    corroborate: bool = False       # every memory filling a required slot is evidence, not just the strongest

    def conclusion_topic(self) -> str | None:
        """The topic a conclusion carries: ``emits_topic``, else ``topic_prefix``, else the rule id as a label."""
        return self.emits_topic or self.topic_prefix or canonical_label(self.rule_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: version of the derivation semantics, part of every derived id: bump it when a released builder's output changes
#: 2: consolidations quote by visibility (metadata.statements) and derived text is bounded
DERIVATION_VERSION = 2
#: rule fields that do not change what a rule derives: switching a rule off and on, deleting and re-creating it,
#: narrowing it to one organization or editing its metadata keeps its digest, so its conclusions keep their ids
_RULE_NON_DERIVING = ("enabled", "metadata", "org_id")


def rule_snapshot(rule: Rule) -> dict[str, Any]:
    """The fields of a rule that decide its conclusions (``rule_id`` and the twelve deriving fields)."""
    return {k: v for k, v in rule.to_dict().items() if k not in _RULE_NON_DERIVING}


def rule_digest(rule: Rule) -> str:
    """A short hash of :func:`rule_snapshot`: conclusion ids embed it, so a rule that derives differently derives new ids."""
    return content_hash(json.dumps(rule_snapshot(rule), sort_keys=True, ensure_ascii=False))[:16]

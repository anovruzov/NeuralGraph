"""Artifact schemas: claims, lineage, support, questions, conflicts.

These are the only objects that cross organisational boundaries in the
hierarchical systems.  They carry no raw text unless `quotes` is populated
under a permissive `quote_policy` (measured as a privacy failure).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field
from typing import Any

LAYERS: tuple[str, ...] = ("worker", "squad", "team", "department", "division", "region", "executive")
LAYER_RANK = {l: i for i, l in enumerate(LAYERS)}


def stable_hash(*parts: Any, n: int = 12) -> str:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return h[:n]


@dataclass
class SupportRecord:
    replica_count: int = 0          # raw number of contributing observations
    independent_support: float = 0.0  # after dedup + correlation discount
    distinct_workers: int = 0
    distinct_teams: int = 0
    distinct_departments: int = 0
    distinct_regions: int = 0
    evidence_hashes: list[str] = field(default_factory=list)  # bounded


@dataclass
class LineageRecord:
    parent_claim_ids: list[str] = field(default_factory=list)
    contributing_units: list[str] = field(default_factory=list)  # child unit ids with n>0
    root_worker_hashes: list[str] = field(default_factory=list)   # bounded, hashed
    path: list[str] = field(default_factory=list)                 # unit ids from origin to holder
    derivation_operator: str = "observe"   # observe | pool | synthesize | revise | answer_question | copy
    evidence_rounds: tuple[int, int] = (0, 0)


@dataclass
class Claim:
    claim_id: str
    producer_id: str
    layer: str
    cell: int                      # flat cell id (vocab.CellIndex)
    label: int                     # label index
    sign: int                      # +1 elevated, -1 reduced
    n: int
    k: int
    rate: float
    baseline_rate: float
    effect: float
    p_value: float
    q_value: float
    confidence: float
    support: SupportRecord = field(default_factory=SupportRecord)
    lineage: LineageRecord = field(default_factory=LineageRecord)
    valid_from: int = 0
    valid_to: int | None = None
    revision_of: str | None = None
    contradicts: list[str] = field(default_factory=list)
    status: str = "proposed"       # proposed | accepted | quarantined | superseded | unresolved | rejected
    privacy_class: str = "aggregate"
    quotes: list[str] = field(default_factory=list)
    signature: str = ""
    round_created: int = 0
    round_updated: int = 0
    origin_attack: str | None = None   # set only by attack generator on poisoned claims (evaluation use)
    conditional_on: dict[str, str] = field(default_factory=dict)  # e.g. {"region": "R2"} for split conflicts
    quarantine_reason: str | None = None
    disputed: bool = False          # a scoped claim that sibling units' null evidence does not generalise

    # ---- signing ---------------------------------------------------------
    def content_digest(self) -> str:
        body = json.dumps({
            "producer": self.producer_id, "cell": self.cell, "label": self.label, "sign": self.sign,
            "n": self.n, "k": self.k, "round": self.round_created,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def sign_with(self, key: bytes) -> None:
        self.signature = hmac.new(key, self.content_digest().encode(), hashlib.sha256).hexdigest()[:24]

    def verify(self, key: bytes) -> bool:
        expected = hmac.new(key, self.content_digest().encode(), hashlib.sha256).hexdigest()[:24]
        return hmac.compare_digest(expected, self.signature)

    @property
    def sig_key(self) -> tuple[int, int, int]:
        return (self.cell, self.label, self.sign)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def wire_bytes(self) -> int:
        base = 96  # ids, counts, floats, status, signature
        base += 8 * len(self.lineage.parent_claim_ids) + 6 * len(self.lineage.contributing_units)
        base += 12 * len(self.lineage.root_worker_hashes) + 12 * len(self.support.evidence_hashes)
        base += sum(len(q) for q in self.quotes)
        return base


@dataclass
class QuestionArtifact:
    question_id: str
    asker_id: str
    cell: int
    label: int
    trigger: str                 # marginal_pair | confidence | eig | fixed
    budget_bytes: int
    target_unit_ids: list[str]
    round: int
    status: str = "open"        # open | answered | expired
    expected_gain: float = 0.0
    answer_bytes: int = 0

    def wire_bytes(self) -> int:
        return 48 + 6 * len(self.target_unit_ids)


@dataclass
class Conflict:
    conflict_id: str
    cell: int
    label: int
    claim_ids: list[str]
    signs: list[int]
    status: str = "open"        # open | resolved | unresolved_appropriate | split_conditional
    winner: str | None = None
    reason: str = ""
    round_opened: int = 0
    round_closed: int | None = None


@dataclass
class ModelProfile:
    """Simulated edge/central model behaviour.  Values are assumptions unless
    measured by scripts/validate_with_real_slm.py (then `measured=True`)."""
    name: str
    params_b: float
    attr_omission: float          # P(attribute not registered in a sketch)
    attr_misread: float           # P(attribute value substituted)
    label_drop: float             # P(observed label dropped by the model)
    label_spurious: float         # P(spurious label added)
    injection_susceptibility: float
    poison_tpr: float             # content-classifier operating point
    poison_fpr: float
    latency_ms_per_1k_tokens: float
    ram_gb: float
    energy_j_per_1k_tokens: float
    usd_per_1k_tokens: float
    context_window_tokens: int
    measured: bool = False

    def dominates(self, other: "ModelProfile") -> bool:
        """True if self is at least as capable as other on every fidelity parameter."""
        return (
            self.attr_omission <= other.attr_omission and self.attr_misread <= other.attr_misread
            and self.label_drop <= other.label_drop and self.label_spurious <= other.label_spurious
            and self.injection_susceptibility <= other.injection_susceptibility
            and self.poison_tpr >= other.poison_tpr and self.poison_fpr <= other.poison_fpr
        )

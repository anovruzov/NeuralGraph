"""The follow-up service at HQ (G7): a supported conclusion becomes approval-routed work for a named owner.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

:class:`FollowupService` reads HQ's store only through :class:`~..detect.store.HqReader` (read-only; one connection
per call) and writes only its own ledger (``ledger.py``). Every check of a call runs inside one ``BEGIN IMMEDIATE``
ledger transaction that re-reads the key's entries, so two service instances on one file still produce exactly one
terminal decision; the first failing check decides. A refusal is exactly one committed ``refused`` entry (its
payload holds the operation, the code and, when relevant, the conclusion id, the type, the conclusion's status, a
schema path and keyword or an arg name; never an arg value), after which :class:`FollowupRefused` is raised. A
malformed call (a bad ``as_of``, a bad principal object, duplicate targets, an ``as_of`` earlier than the key's last
entry or the conclusion's, a malformed stored conclusion) is :class:`FollowupError` and writes nothing.

**Propose** (:meth:`FollowupService.propose`), in order: ``not_system`` (only :data:`~.policy.SYSTEM` proposes, so
no record content and no person can), ``unknown_conclusion``, then an already proposed key returns that key with no
entry (whatever its state), ``conclusion_not_supported``, ``unknown_type``, ``tier_not_allowed`` (only T0 and T1;
checked before ``enabled``), ``type_disabled``, ``args_invalid`` (the pack's compiled args schema over the
NFC-normalised args), ``args_out_of_scope`` (a ``conclusion_id`` arg must be the conclusion's own id, a ``predicate``
arg its key's predicate, an ``entity_id`` arg's (type, value) in the conclusion's scope ids: its key's entity and its
lineage cells' entities), ``target_not_contributing`` (targets default to the conclusion's contributing sites; given
ones must be a non-empty subset), ``kill_switch``, ``daily_cap`` (``proposed`` entries of the type on the UTC date of
``as_of``) and ``approvers_unavailable``. Then ``proposed``, ``assigned`` (the owner-role holder at the assignment
unit, the common prefix of the conclusion's decision unit and the targets' decision unit, or its nearest ancestor;
``ack_due = as_of + ack_days``) and, without a holder, at once ``escalated`` (``unassigned``) or
``escalation_failed``. For T1 the draft is written outside the transaction and recorded in its own
(``drafted`` or ``draft_failed``; no automatic retry; :meth:`FollowupService.regenerate` adds an attempt).

The key (D4) is ``act:<conclusion id>:<type>:<first 16 hex of sha256(canonical {args, targets})>`` over the
NFC-normalised args and the sorted targets: key order and Unicode normalisation of args never change it; other
targets give another key.

**Decide** (:meth:`approve`, :meth:`edit`, :meth:`reject`), in order: ``system_cannot_decide``, ``unknown_key``,
``terminal``, the ``as_of`` check, ``conclusion_no_longer_supported``, ``approvers_unavailable``, the authority codes
(read from the CURRENT approvers file, never from the assignment: the principal needs an entry with the owner or
escalation role whose unit covers every target; a target no longer in the org is ``out_of_scope``), approve only
``kill_switch``, approve and edit ``no_draft`` (an edit of T0; a T1 without a draft) and ``stale_version``. Every tier
needs exactly one approval before it executes (D3; T0's version is always 1). An edit may not name an immutable field
(:data:`IMMUTABLE_FIELDS`) that is not a draft property; the merged draft must pass the type's ``draft_schema``
(``draft_invalid``) and the id-scope scan (``draft_out_of_scope``); an unchanged draft writes nothing.

**Execute** at most once (:meth:`execute`): a stored result is returned byte for byte with no executor call; an
``executing`` entry without a closing one is ``in_progress`` in the process that runs it and, anywhere else (a crash),
gets one ``outcome_unknown`` (``interrupted``) without an executor call; then ``rejected``, ``not_approved``,
``conclusion_no_longer_supported``, and a kill switch that is on appends ``blocked``. ``executing`` is committed before
the executor runs; an executor exception is ``outcome_unknown`` (``executor_error``; nothing of the exception is
kept); a crash (a ``BaseException``) propagates and leaves ``executing``. One executing service per ledger (D12).

:meth:`overdue` escalates each unacknowledged item past its ``ack_due`` once; :meth:`check_outcome` measures, after
execution, whether the failure mode recurred (``outcome.py``) and records each distinct result once.
:func:`replay` rebuilds every follow-up's state from the ledger alone and calls no executor.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from .. import schemacheck
from ..detect.store import HqReader
from ..edge.egress import FOLLOWUP_KEY_RE
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from . import outcome
from .drafts import DraftWriter, draft_payload, draft_scope_problem
from .executors import ExecContext
from .ledger import KINDS, PAYLOAD_KEYS, Entry, FollowupLedger, LedgerConflict, LedgerError, LedgerTx
from .policy import (BUILT_AHEAD_LABEL, ApproversError, Approvers, FollowupError, KillSwitch, Principal,
                     load_approvers, normalise_as_of, plus_days, utc_date)

if TYPE_CHECKING:
    from ..detect.org import OrgConfig
    from ..packs.loader import FollowupType, FrozenPack

PROPOSE_CODES = ("not_system", "unknown_conclusion", "conclusion_not_supported", "unknown_type", "tier_not_allowed",
                 "type_disabled", "args_invalid", "args_out_of_scope", "target_not_contributing", "kill_switch",
                 "daily_cap", "approvers_unavailable")
DECISION_CODES = ("system_cannot_decide", "unknown_key", "terminal", "conclusion_no_longer_supported",
                  "not_an_approver", "role_not_allowed", "out_of_scope", "stale_version", "no_draft",
                  "immutable_field", "draft_invalid", "draft_out_of_scope")
EXECUTION_CODES = ("not_approved", "rejected", "not_executed")
REFUSAL_CODES = PROPOSE_CODES + DECISION_CODES + EXECUTION_CODES
OPS = ("propose", "approve", "edit", "reject", "regenerate", "execute", "check_outcome")
STATUSES = ("awaiting_draft", "draft_failed", "awaiting_approval", "approved", "rejected", "executing", "executed",
            "outcome_unknown")
EXECUTE_STATUSES = ("executed", "blocked_kill_switch", "outcome_unknown", "in_progress")
IMMUTABLE_FIELDS = ("args", "conclusion_id", "key", "targets", "tier", "type")
EXECUTOR_KINDS = ("packet", "draft")
ALLOWED_TIERS = ("T0", "T1")
_TIER_EXECUTOR = dict(zip(ALLOWED_TIERS, EXECUTOR_KINDS))           # the loader's pairing: T0 packet, T1 draft
CONCLUSION_ID_RE = re.compile(r"c-[0-9a-f]{32}", re.ASCII)
TYPE_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,40}", re.ASCII)
_OPEN = ("awaiting_draft", "draft_failed", "awaiting_approval")


class FollowupRefused(Exception):
    """A refused call; always follows exactly one committed ``refused`` entry. ``str`` names only the code."""

    def __init__(self, code: str) -> None:
        if code not in REFUSAL_CODES:
            raise ValueError("unknown refusal code") from None
        super().__init__()
        self.code = code

    def __str__(self) -> str:
        return f"follow-up refused: {self.code}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


# --------------------------------------------------------------------------------------------------- views and state

@dataclass(frozen=True)
class ConclusionView:
    """The latest version of a conclusion as HQ's store holds it, with its question's key and window and its routes."""

    conclusion_id: str
    version: int
    status: str
    as_of: str
    question_id: str
    candidate_key: str
    entity_type: str
    entity_id: str
    predicate: str
    window: tuple[str, str]
    decision_unit: str | None
    confirming_sites: tuple[str, ...]
    contributing_sites: tuple[str, ...]
    support_lb: int
    roots_lb: int
    reporters_lb: int
    newest_week: str | None
    scope_ids: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class FollowupState:
    """One follow-up as its ledger entries make it; built only by :func:`replay` (and its per-key fold)."""

    key: str
    conclusion_id: str
    conclusion_version: int
    question_id: str
    candidate_key: str
    type_id: str
    tier: str
    executor: str
    args: Mapping[str, Any]
    targets: tuple[str, ...]
    proposed_as_of: str
    owner: str | None = None
    owner_role: str | None = None
    unit: str | None = None
    assignment_unit: str | None = None
    ack_due: str | None = None
    escalation: Mapping[str, Any] | None = None
    draft_attempts: int = 0
    drafts: tuple[tuple[int, Mapping[str, Any], str], ...] = ()
    latest_version: int = 0
    decision: str | None = None
    decided_version: int | None = None
    decided_by: str | None = None
    acknowledged: bool = False
    executing: bool = False
    result: Mapping[str, Any] | None = None
    outcome_unknown: bool = False
    blocked: int = 0
    outcomes: tuple[Mapping[str, Any], ...] = ()
    last_as_of: str = ""

    @property
    def status(self) -> str:
        if self.outcome_unknown:
            return "outcome_unknown"
        if self.result is not None:
            return "executed"
        if self.executing:
            return "executing"
        if self.decision is not None:
            return self.decision
        if self.tier == "T1" and not self.drafts:
            return "draft_failed" if self.draft_attempts > 0 else "awaiting_draft"
        return "awaiting_approval"

    def draft(self, version: int | None) -> Mapping[str, Any] | None:
        found = [d for v, d, _ in self.drafts if v == version]
        return found[0] if found else None

    def to_dict(self) -> dict[str, Any]:
        return strict_load(canonical_bytes({
            "key": self.key, "conclusion_id": self.conclusion_id, "conclusion_version": self.conclusion_version,
            "question_id": self.question_id, "candidate_key": self.candidate_key, "type": self.type_id,
            "tier": self.tier, "executor": self.executor, "args": self.args, "targets": list(self.targets),
            "proposed_as_of": self.proposed_as_of, "owner": self.owner, "owner_role": self.owner_role,
            "unit": self.unit, "assignment_unit": self.assignment_unit, "ack_due": self.ack_due,
            "escalation": self.escalation, "draft_attempts": self.draft_attempts,
            "drafts": [{"version": v, "draft": d, "source": s} for v, d, s in self.drafts],
            "latest_version": self.latest_version, "decision": self.decision, "decided_version": self.decided_version,
            "decided_by": self.decided_by, "acknowledged": self.acknowledged, "executing": self.executing,
            "result": self.result, "outcome_unknown": self.outcome_unknown, "blocked": self.blocked,
            "outcomes": list(self.outcomes), "last_as_of": self.last_as_of, "status": self.status}))


@dataclass(frozen=True)
class ExecuteResult:
    status: str
    result: Mapping[str, Any] | None


@dataclass(frozen=True)
class _Refusal:
    code: str


def _bad(seq: int) -> LedgerError:
    return LedgerError("chain:bad_entry", seq)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _apply(states: dict[str, FollowupState], e: Entry) -> None:
    """One entry onto the states; an impossible transition is ``LedgerError('chain:bad_entry', seq)``."""
    if e.kind not in KINDS or not isinstance(e.payload, dict) or tuple(sorted(e.payload)) != PAYLOAD_KEYS[e.kind]:
        raise _bad(e.seq) from None
    if e.kind == "refused" or e.key == "-":
        return
    p, st = e.payload, states.get(e.key)
    if e.kind == "proposed":
        targets = p["targets"]
        if (st is not None or p["tier"] not in ALLOWED_TIERS or p["executor"] != _TIER_EXECUTOR[p["tier"]]
                or not isinstance(p["as_of"], str) or not isinstance(targets, list)
                or not all(isinstance(t, str) for t in targets)):
            raise _bad(e.seq) from None
        states[e.key] = FollowupState(
            key=e.key, conclusion_id=p["conclusion_id"], conclusion_version=p["conclusion_version"],
            question_id=p["question_id"], candidate_key=p["candidate_key"], type_id=p["type"], tier=p["tier"],
            executor=p["executor"], args=p["args"], targets=tuple(targets), proposed_as_of=p["as_of"],
            latest_version=1 if p["tier"] == "T0" else 0, last_as_of=p["as_of"])
        return
    if st is None:
        raise _bad(e.seq) from None
    closed = st.result is not None or st.outcome_unknown
    kind = e.kind
    if kind in ("drafted", "draft_failed", "edited") and st.tier != "T1":
        raise _bad(e.seq) from None                   # T0's version is the proposal itself: it is never drafted
    if kind == "assigned":
        if st.assignment_unit is not None or not isinstance(p["assignment_unit"], str):
            raise _bad(e.seq) from None
        st = replace(st, owner=p["owner"], owner_role=p["role"], unit=p["unit"],
                     assignment_unit=p["assignment_unit"], ack_due=p["ack_due"])
    elif kind == "drafted":
        if st.decision is not None or p["attempt"] != st.draft_attempts + 1 \
                or p["version"] != st.latest_version + 1 or not isinstance(p["draft"], dict):
            raise _bad(e.seq) from None
        st = replace(st, draft_attempts=p["attempt"], drafts=st.drafts + ((p["version"], p["draft"], p["source"]),),
                     latest_version=p["version"])
    elif kind == "draft_failed":
        if st.decision is not None or p["attempt"] != st.draft_attempts + 1:
            raise _bad(e.seq) from None
        st = replace(st, draft_attempts=p["attempt"])
    elif kind == "edited":
        if st.decision is not None or p["base_version"] != st.latest_version \
                or p["version"] != st.latest_version + 1 or not isinstance(p["draft"], dict):
            raise _bad(e.seq) from None
        st = replace(st, drafts=st.drafts + ((p["version"], p["draft"], "edited"),), latest_version=p["version"],
                     acknowledged=True)
    elif kind in ("approved", "rejected"):
        if st.decision is not None or not _is_int(p["version"]):
            raise _bad(e.seq) from None
        st = replace(st, decision=kind, decided_version=p["version"], decided_by=e.actor, acknowledged=True)
    elif kind == "executing":
        if st.decision != "approved" or st.executing:
            raise _bad(e.seq) from None
        st = replace(st, executing=True)
    elif kind in ("executed", "outcome_unknown"):
        if not st.executing or closed:
            raise _bad(e.seq) from None
        if kind == "executed":
            if not isinstance(p["result"], dict):
                raise _bad(e.seq) from None
            st = replace(st, result=p["result"])
        else:
            st = replace(st, outcome_unknown=True)
    elif kind == "blocked":
        if st.decision != "approved" or st.executing:
            raise _bad(e.seq) from None
        st = replace(st, blocked=st.blocked + 1)
    elif kind in ("escalated", "escalation_failed"):
        if st.escalation is not None:
            raise _bad(e.seq) from None
        st = replace(st, escalation={"kind": kind, **p})
    elif kind == "outcome":
        if st.result is None or not isinstance(p["result"], dict):
            raise _bad(e.seq) from None
        st = replace(st, outcomes=st.outcomes + (p["result"],))
    as_of = p.get("as_of")
    if isinstance(as_of, str) and as_of > st.last_as_of:
        st = replace(st, last_as_of=as_of)
    states[e.key] = st


def replay(entries: Sequence[Entry]) -> dict[str, FollowupState]:
    """Every follow-up's state from a whole ledger, in seq order (a gap or an out-of-order seq is
    ``LedgerError('chain:seq_gap', seq)``; an impossible transition ``chain:bad_entry``). Pure: no executor, no I/O."""
    states: dict[str, FollowupState] = {}
    for expected, e in enumerate(entries, start=1):
        if e.seq != expected:
            raise LedgerError("chain:seq_gap", expected) from None
        _apply(states, e)
    return states


def _fold(entries: Sequence[Entry]) -> FollowupState | None:
    """One key's state from that key's entries, in increasing seq order."""
    states: dict[str, FollowupState] = {}
    last = 0
    for e in entries:
        if e.seq <= last:
            raise LedgerError("chain:seq_gap", e.seq) from None
        last = e.seq
        _apply(states, e)
    return next(iter(states.values()), None)


# --------------------------------------------------------------------------------------------------- helpers

class _NotCanonical(Exception):
    pass


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k in value:
            if not isinstance(k, str):
                raise _NotCanonical() from None
            nk = unicodedata.normalize("NFC", k)
            if nk in out:
                raise _NotCanonical() from None
            out[nk] = _nfc(value[k])
        return out
    if isinstance(value, (list, tuple)):
        return [_nfc(v) for v in value]
    return value


def canonical_args(args: Any) -> Any:
    """``args`` with every string (keys included) in NFC, as a fresh strict JSON value; None when it cannot be."""
    value = None
    try:
        value = strict_load(canonical_bytes(_nfc(args)))
    except (_NotCanonical, StrictJsonError, RecursionError):
        value = None
    return value


def followup_key(conclusion_id: str, type_id: str, args: Any, targets: Iterable[str]) -> str:
    """``act:<conclusion id>:<type>:<16 hex>`` over the canonical NFC args and the sorted targets (D4)."""
    digest = sha256_hex(canonical_bytes({"args": canonical_args(args), "targets": sorted(targets)}))
    return f"act:{conclusion_id}:{type_id}:{digest[:16]}"


def _common_prefix(a: str, b: str) -> str:
    left, right = a.split("/"), b.split("/")
    n = 0
    while n < min(len(left), len(right)) and left[n] == right[n]:
        n += 1
    return "/".join(left[:n])


def _field(obj: Any, key: str, kind: type) -> Any:
    value = obj.get(key) if isinstance(obj, dict) else None
    if not (_is_int(value) if kind is int else isinstance(value, kind)):
        raise FollowupError("a stored conclusion or question is malformed") from None
    return value


def _key_column(key: Any) -> str:
    """The ledger key of a refusal about ``key``: the key when it is key-shaped, else ``'-'``."""
    return key if isinstance(key, str) and FOLLOWUP_KEY_RE.fullmatch(key) is not None else "-"


def _refused_payload(op: str, code: str, *, conclusion_id: Any = None, type_id: Any = None, status: Any = None,
                     path: str | None = None, keyword: str | None = None, arg: str | None = None) -> dict[str, Any]:
    cid = conclusion_id if isinstance(conclusion_id, str) and CONCLUSION_ID_RE.fullmatch(conclusion_id) else None
    tid = type_id if isinstance(type_id, str) and TYPE_ID_RE.fullmatch(type_id) else None
    return {"op": op, "code": code, "conclusion_id": cid, "type": tid, "status": status, "path": path,
            "keyword": keyword, "arg": arg}


# --------------------------------------------------------------------------------------------------- the service

class FollowupService:
    """The follow-up service over one ledger. ``executors`` maps ``packet`` and ``draft`` to objects with
    ``run(ExecContext) -> dict``; T2 has no executor. The ledger stamps ``at`` with its own clock; every decision uses
    the caller's ``as_of``."""

    def __init__(self, ledger: FollowupLedger, *, pack: "FrozenPack", org: "OrgConfig", hq: HqReader,
                 approvers_path: Any, kill_switch: KillSwitch, drafter: DraftWriter,
                 executors: Mapping[str, Any]) -> None:
        info = ledger.info
        if (info["pack_id"], info["config_hash"], info["enterprise"]) != (pack.id, pack.config_hash, org.enterprise):
            raise FollowupError("the ledger belongs to another pack config or enterprise") from None
        if "write" in executors:
            raise FollowupError("T2 has no executor") from None
        if any(name not in EXECUTOR_KINDS for name in executors):
            raise FollowupError("executors are keyed packet or draft") from None
        needed = {ft.executor for ft in pack.followups.values() if ft.enabled and ft.tier in ALLOWED_TIERS}
        if not needed <= set(executors):
            raise FollowupError("an enabled T0 or T1 type has no executor") from None
        self.ledger = ledger
        self.pack = pack
        self.org = org
        self.hq = hq
        self.approvers_path = approvers_path
        self.kill_switch = kill_switch
        self.drafter = drafter
        self.executors = dict(executors)
        self._args_schemas = {t: schemacheck.compile(ft.args_json_schema()) for t, ft in pack.followups.items()}
        self._draft_schemas = {t: schemacheck.compile(ft.draft_json_schema()) for t, ft in pack.followups.items()
                               if ft.draft_schema is not None}
        self._lock = threading.RLock()
        self._inflight: set[str] = set()

    # ------------------------------------------------------------------ reads
    def view(self, conclusion_id: str) -> ConclusionView | None:
        """The latest version of a conclusion, or None when HQ holds none."""
        rows = self.hq.conclusions(conclusion_id)
        if not rows:
            return None
        latest = rows[-1]
        body = strict_load(latest.body)
        question = self.hq.question(latest.question_id)
        if question is None:
            raise FollowupError("a stored conclusion or question is malformed") from None
        qbody = strict_load(question.body)
        params, window = _field(qbody, "params", dict), _field(qbody, "window", dict)
        entity_type, entity_id = _field(params, "entity_type", str), _field(params, "entity_id", str)
        support = _field(_field(body, "gate", dict), "support", dict)
        confirming = _field(support, "confirming_sites", list)
        cells = _field(_field(body, "lineage", dict), "cells", list)
        decision_unit = body.get("decision_unit") if isinstance(body, dict) else None
        newest = support.get("newest_week")
        if not isinstance(decision_unit, (str, type(None))) or not isinstance(newest, (str, type(None))) \
                or not all(isinstance(s, str) for s in confirming):
            raise FollowupError("a stored conclusion or question is malformed") from None
        scope = {(entity_type, entity_id)}
        for cell in cells:
            scope.add((_field(cell, "entity_type", str), _field(cell, "entity_id", str)))
        routes = self.hq.routes(latest.question_id)
        return ConclusionView(
            conclusion_id=latest.conclusion_id, version=latest.version, status=latest.status, as_of=latest.as_of,
            question_id=latest.question_id, candidate_key=_field(qbody, "candidate_key", str),
            entity_type=entity_type, entity_id=entity_id, predicate=_field(params, "predicate", str),
            window=(_field(window, "start_week", str), _field(window, "end_week", str)), decision_unit=decision_unit,
            confirming_sites=tuple(confirming),
            contributing_sites=tuple(sorted(r.site for r in routes
                                            if r.role == "contributing" and r.site in self.org.sites)),
            support_lb=_field(support, "support_lb", int), roots_lb=_field(support, "roots_lb", int),
            reporters_lb=_field(support, "reporters_lb", int), newest_week=newest, scope_ids=frozenset(scope))

    def state(self, key: str) -> FollowupState | None:
        if not isinstance(key, str) or FOLLOWUP_KEY_RE.fullmatch(key) is None:
            return None
        return _fold(self.ledger.entries(key))

    def states(self) -> list[FollowupState]:
        states = replay(self.ledger.entries())
        return [states[k] for k in sorted(states)]

    def state_digest(self) -> str:
        return sha256_hex(canonical_bytes([s.to_dict() for s in self.states()]))

    def head_hash(self) -> str:
        return self.ledger.head_hash()

    def verify_chain(self, **anchor: Any) -> Any:
        return self.ledger.verify_chain(**anchor)

    def summary(self) -> dict[str, Any]:
        entries = self.ledger.entries()
        states = replay(entries)
        kinds = dict.fromkeys(KINDS, 0)
        for e in entries:
            kinds[e.kind] += 1
        statuses = dict.fromkeys(STATUSES, 0)
        for st in states.values():
            statuses[st.status] += 1
        return {"label": BUILT_AHEAD_LABEL, "ledger_head_hash": entries[-1].hash if entries else self.ledger.genesis,
                "ledger_entries": len(entries), "kinds": kinds, "statuses": statuses}

    def close(self) -> None:
        self.ledger.close()

    # ------------------------------------------------------------------ shared checks
    @staticmethod
    def _principal(principal: Any) -> Principal:
        if not isinstance(principal, Principal):
            raise FollowupError("the principal must be a Principal") from None
        return principal

    def _approvers(self) -> Approvers | None:
        found = None
        try:
            found = load_approvers(self.approvers_path, self.org, self.pack)
        except ApproversError:
            found = None
        return found

    def _state_tx(self, tx: LedgerTx, key: Any) -> FollowupState | None:
        if not isinstance(key, str) or FOLLOWUP_KEY_RE.fullmatch(key) is None:
            return None
        return _fold(tx.entries(key))

    def _packets(self, tx: LedgerTx, conclusion_id: str) -> list[dict[str, Any]]:
        """The crossing packet bodies of every executed T0 follow-up of the conclusion."""
        executors: dict[str, str] = {}
        packets: list[dict[str, Any]] = []
        for e in tx.entries_for_conclusion(conclusion_id):
            if e.kind == "proposed":
                executors[e.key] = e.payload["executor"]
            elif e.kind == "executed" and executors.get(e.key) == "packet":
                packets.extend(p for p in e.payload["result"].get("packets", []) if isinstance(p, dict))
        return packets

    def _scope(self, view: ConclusionView, packets: Sequence[Mapping[str, Any]]) -> frozenset[tuple[str, str]]:
        """The ids a draft may name: the conclusion's scope ids and the packets' co-mentions."""
        return view.scope_ids | {(m["entity_type"], m["entity_id"]) for p in packets for m in p.get("co_mentions", [])}

    def _escalate(self, tx: LedgerTx, key: str, ft: "FollowupType", unit: str, approvers: Approvers | None,
                  reason: str, as_of: str) -> None:
        role = ft.escalate_to_role
        if role is None:
            problem = "no_escalation_role"
        elif approvers is None:
            problem = "approvers_unavailable"
        else:
            to, at_unit = approvers.find(role, unit)
            if to is not None:
                tx.append("escalated", key, "system", {"reason": reason, "to_role": role, "to": to, "unit": at_unit,
                                                       "as_of": as_of})
                return
            problem = "no_holder"
        tx.append("escalation_failed", key, "system", {"reason": reason, "to_role": role, "problem": problem,
                                                       "as_of": as_of})

    # ------------------------------------------------------------------ propose
    def propose(self, conclusion_id: str, type_id: str, args: Any, *, principal: Principal, as_of: str,
                targets: Sequence[str] | None = None) -> str:
        """The follow-up key (an existing one when already proposed); :class:`FollowupRefused` after one ``refused``
        entry; :class:`FollowupError` with no entry for a malformed call."""
        as_of = normalise_as_of(as_of)
        principal = self._principal(principal)
        if targets is not None and (not isinstance(targets, (list, tuple))
                                    or not all(isinstance(t, str) for t in targets)):
            raise FollowupError("targets must be a list of site ids") from None
        with self._lock:
            with self.ledger.transaction() as tx:
                result = self._propose(tx, conclusion_id, type_id, args, principal, as_of, targets)
            if isinstance(result, _Refusal):
                raise FollowupRefused(result.code) from None
            key, new, tier = result
        if new and tier == "T1":
            self._draft(key, as_of, "system")
        return key

    def _propose(self, tx: LedgerTx, conclusion_id: Any, type_id: Any, args: Any, principal: Principal, as_of: str,
                 targets: Sequence[str] | None) -> _Refusal | tuple[str, bool, str]:
        cid_ok = isinstance(conclusion_id, str) and CONCLUSION_ID_RE.fullmatch(conclusion_id) is not None
        nfc_args = canonical_args(args)
        view: ConclusionView | None = None

        def key_now() -> str:
            if not cid_ok or not isinstance(type_id, str) or TYPE_ID_RE.fullmatch(type_id) is None \
                    or nfc_args is None:
                return "-"
            chosen = targets if targets is not None else (view.contributing_sites if view is not None else None)
            return followup_key(conclusion_id, type_id, nfc_args, chosen) if chosen is not None else "-"

        def refuse(code: str, **fields: Any) -> _Refusal:
            tx.append("refused", key_now(), principal.label,
                      _refused_payload("propose", code, conclusion_id=conclusion_id, type_id=type_id, **fields))
            return _Refusal(code)

        if principal.kind != "system":
            return refuse("not_system")
        view = self.view(conclusion_id) if cid_ok else None
        if view is None:
            return refuse("unknown_conclusion")
        if utc_date(as_of) < view.as_of:
            raise FollowupError("as_of is before the conclusion's as_of") from None
        existing = key_now()
        if existing != "-" and any(e.kind == "proposed" for e in tx.entries(existing)):
            return existing, False, ""
        if view.status != "supported":
            return refuse("conclusion_not_supported", status=view.status)
        ft = self.pack.followups.get(type_id) if isinstance(type_id, str) else None
        if ft is None:
            return refuse("unknown_type")
        if ft.tier not in ALLOWED_TIERS:
            return refuse("tier_not_allowed")
        if not ft.enabled:
            return refuse("type_disabled")
        if nfc_args is None:
            return refuse("args_invalid", path="$", keyword="json")
        problems = self._args_schemas[ft.id].validate(nfc_args)
        if problems:
            return refuse("args_invalid", path=problems[0][0], keyword=problems[0][1])
        for name in sorted(ft.args):
            spec, value = ft.args[name], nfc_args[name]
            in_scope = (value == view.conclusion_id if spec["kind"] == "conclusion_id"
                        else value == view.predicate if spec["kind"] == "predicate"
                        else (spec["entity_type"], value) in view.scope_ids if spec["kind"] == "entity_id"
                        else True)
            if not in_scope:
                return refuse("args_out_of_scope", arg=name)
        if targets is None:
            chosen = list(view.contributing_sites)
        else:
            if len(set(targets)) != len(targets):
                raise FollowupError("duplicate targets") from None
            chosen = sorted(targets)
        if not chosen or any(t not in view.contributing_sites for t in chosen):
            return refuse("target_not_contributing")
        if self.kill_switch.state(ft.id).on:
            return refuse("kill_switch")
        if tx.proposed_count(ft.id, utc_date(as_of)) >= ft.daily_cap:
            return refuse("daily_cap")
        approvers = self._approvers()
        if approvers is None:
            return refuse("approvers_unavailable")
        key = followup_key(view.conclusion_id, ft.id, nfc_args, chosen)
        tx.append("proposed", key, "system", {
            "conclusion_id": view.conclusion_id, "conclusion_version": view.version, "question_id": view.question_id,
            "candidate_key": view.candidate_key, "type": ft.id, "tier": ft.tier, "executor": ft.executor,
            "args": nfc_args, "targets": chosen, "as_of": as_of})
        targets_unit = self.org.decision_unit(chosen)
        unit = targets_unit if view.decision_unit is None else (_common_prefix(view.decision_unit, targets_unit)
                                                                or self.org.enterprise)
        owner, found_at = approvers.find(ft.owner_role, unit)
        tx.append("assigned", key, "system", {
            "owner": owner, "role": ft.owner_role, "unit": found_at, "assignment_unit": unit,
            "ack_due": plus_days(as_of, ft.ack_days), "approvers_hash": approvers.approvers_hash})
        if owner is None:
            self._escalate(tx, key, ft, unit, approvers, "unassigned", as_of)
        return key, True, ft.tier

    # ------------------------------------------------------------------ drafts
    def _draft(self, key: str, as_of: str, actor: str) -> tuple[int | None, str | None]:
        """Write a draft outside any transaction, then record ``drafted`` or ``draft_failed`` in one."""
        with self._lock:
            with self.ledger.transaction() as tx:
                state = self._state_tx(tx, key)
                packets = self._packets(tx, state.conclusion_id)
        view = self.view(state.conclusion_id)
        ft = self.pack.followups[state.type_id]
        payload = draft_payload(self.pack, ft, view, state.args, packets)
        draft, reason = self.drafter.write(ft, payload, ref=f"d:{key[-16:]}:{state.draft_attempts + 1}",
                                           scope=self._scope(view, packets))
        with self._lock:
            with self.ledger.transaction() as tx:
                state = self._state_tx(tx, key)
                if state.decision is not None:
                    return None, "terminal"
                attempt = state.draft_attempts + 1
                if draft is None:
                    tx.append("draft_failed", key, actor, {"attempt": attempt, "reason": reason})
                    return None, reason
                version = state.latest_version + 1
                tx.append("drafted", key, actor, {"attempt": attempt, "version": version, "draft": draft,
                                                  "source": "generated"})
        return version, None

    def regenerate(self, key: str, *, principal: Principal, as_of: str) -> tuple[int | None, str | None]:
        """A new draft attempt for a T1 follow-up not yet decided: ``(version, None)`` or ``(None, reason)``. The
        system may ask; a human needs authority over every target."""
        as_of = normalise_as_of(as_of)
        principal = self._principal(principal)
        with self._lock:
            with self.ledger.transaction() as tx:
                refusal = self._regenerate_tx(tx, key, principal, as_of)
            if refusal is not None:
                raise FollowupRefused(refusal.code) from None
        return self._draft(key, as_of, principal.label)

    def _regenerate_tx(self, tx: LedgerTx, key: Any, principal: Principal, as_of: str) -> _Refusal | None:
        state = self._state_tx(tx, key)

        def refuse(code: str, **fields: Any) -> _Refusal:
            tx.append("refused", _key_column(key), principal.label,
                      _refused_payload("regenerate", code, conclusion_id=state.conclusion_id if state else None,
                                       type_id=state.type_id if state else None, **fields))
            return _Refusal(code)

        if state is None:
            return refuse("unknown_key")
        if state.decision is not None:
            return refuse("terminal")
        if state.tier != "T1":
            return refuse("no_draft")
        if as_of < state.last_as_of:
            raise FollowupError("as_of is before the follow-up's last entry") from None
        view = self.view(state.conclusion_id)
        if view.status != "supported":
            return refuse("conclusion_no_longer_supported", status=view.status)
        if principal.kind == "human":
            code, _ = self._authority(state, principal)
            if code is not None:
                return refuse(code)
        return None

    def _roles(self, state: FollowupState) -> set[str]:
        ft = self.pack.followups[state.type_id]
        return {ft.owner_role} | ({ft.escalate_to_role} if ft.escalate_to_role is not None else set())

    def _authority(self, state: FollowupState, principal: Principal) -> tuple[str | None, Approvers | None]:
        """``(code, approvers)``: ``approvers_unavailable``, an authority code or None, from the CURRENT approvers
        file, read once (the approvers it was read from, for the grant)."""
        approvers = self._approvers()
        if approvers is None:
            return "approvers_unavailable", None
        roles = self._roles(state)
        if any(t not in self.org.sites for t in state.targets):
            return approvers.authority(principal.label, roles, ()) or "out_of_scope", approvers
        return approvers.authority(principal.label, roles, [self.org.unit_path(t) for t in state.targets]), approvers

    # ------------------------------------------------------------------ decisions
    def approve(self, key: str, version: int, *, principal: Principal, as_of: str) -> None:
        self._decide("approve", key, principal, as_of, version=version)

    def reject(self, key: str, *, principal: Principal, as_of: str) -> None:
        self._decide("reject", key, principal, as_of)

    def edit(self, key: str, base_version: int, fields: Mapping[str, Any], *, principal: Principal,
             as_of: str) -> int:
        """The new version (the latest one when the merged draft is unchanged, with no entry)."""
        return self._decide("edit", key, principal, as_of, version=base_version, fields=fields)

    def _decide(self, op: str, key: Any, principal: Any, as_of: Any, *, version: Any = None,
                fields: Any = None) -> Any:
        as_of = normalise_as_of(as_of)
        principal = self._principal(principal)
        if op != "reject" and not (_is_int(version) and version >= 0):
            raise FollowupError("the version must be an int >= 0") from None
        if op == "edit" and (not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields)):
            raise FollowupError("fields must be an object") from None
        with self._lock:
            with self.ledger.transaction() as tx:
                result = self._decide_tx(tx, op, key, principal, as_of, version, fields)
            if isinstance(result, _Refusal):
                raise FollowupRefused(result.code) from None
        return result

    def _decide_tx(self, tx: LedgerTx, op: str, key: Any, principal: Principal, as_of: str, version: Any,
                   fields: Any) -> Any:
        state = self._state_tx(tx, key) if principal.kind == "human" else None

        def refuse(code: str, **extra: Any) -> _Refusal:
            tx.append("refused", _key_column(key), principal.label,
                      _refused_payload(op, code, conclusion_id=state.conclusion_id if state else None,
                                       type_id=state.type_id if state else None, **extra))
            return _Refusal(code)

        if principal.kind != "human":
            return refuse("system_cannot_decide")
        if state is None:
            return refuse("unknown_key")
        if state.decision is not None:
            return refuse("terminal")
        if as_of < state.last_as_of:
            raise FollowupError("as_of is before the follow-up's last entry") from None
        view = self.view(state.conclusion_id)
        if view.status != "supported":
            return refuse("conclusion_no_longer_supported", status=view.status)
        code, approvers = self._authority(state, principal)
        if code is not None:
            return refuse(code)
        ft = self.pack.followups[state.type_id]
        if op == "approve" and self.kill_switch.state(ft.id).on:
            return refuse("kill_switch")
        if op in ("approve", "edit"):
            if op == "edit" and state.tier == "T0":
                return refuse("no_draft")
            if state.tier == "T1" and not state.drafts:
                return refuse("no_draft")
            if version != state.latest_version:
                return refuse("stale_version")
        grant = approvers.grant(principal.label, self._roles(state), [self.org.unit_path(t) for t in state.targets])
        if op == "edit":
            return self._edit_tx(tx, state, view, fields, principal, as_of, refuse)
        tx.append("approved" if op == "approve" else "rejected", state.key, principal.label, {
            "version": state.latest_version, "role": grant.role, "unit_path": grant.unit_path,
            "approvers_hash": approvers.approvers_hash, "conclusion_version": view.version, "as_of": as_of})
        return None

    def _edit_tx(self, tx: LedgerTx, state: FollowupState, view: ConclusionView, fields: Mapping[str, Any],
                 principal: Principal, as_of: str, refuse: Any) -> Any:
        ft = self.pack.followups[state.type_id]
        properties = ft.draft_schema["properties"]
        for name in sorted(fields):
            if name not in properties and name in IMMUTABLE_FIELDS:
                return refuse("immutable_field", arg=name)
        latest = state.draft(state.latest_version)
        merged = {**latest, **fields}
        problems = self._draft_schemas[ft.id].validate(merged)
        if problems:
            return refuse("draft_invalid", path=problems[0][0], keyword=problems[0][1])
        merged = strict_load(canonical_bytes(merged))
        path = draft_scope_problem(self.pack, self._scope(view, self._packets(tx, state.conclusion_id)), merged)
        if path is not None:
            return refuse("draft_out_of_scope", path=path)
        if canonical_bytes(merged) == canonical_bytes(latest):
            return state.latest_version
        changed = [{"field": name, "from": latest.get(name), "to": merged[name]} for name in sorted(merged)
                   if name not in latest or canonical_bytes(latest[name]) != canonical_bytes(merged[name])]
        version = state.latest_version + 1
        tx.append("edited", state.key, principal.label, {"base_version": state.latest_version, "version": version,
                                                         "draft": merged, "diff": {"changed": changed},
                                                         "as_of": as_of})
        return version

    # ------------------------------------------------------------------ execute
    def execute(self, key: str, *, as_of: str) -> ExecuteResult:
        as_of = normalise_as_of(as_of)
        with self._lock:
            with self.ledger.transaction() as tx:
                started = self._execute_tx(tx, key, as_of)
            if isinstance(started, _Refusal):
                raise FollowupRefused(started.code) from None
            if isinstance(started, ExecuteResult):
                return started
            state, view, draft = started
            self._inflight.add(key)
        try:
            result = None
            try:
                result = self.executors[state.executor].run(ExecContext(view=view, state=state, as_of=as_of,
                                                                        draft=draft))
                result = strict_load(canonical_bytes(result)) if isinstance(result, dict) else None
            except Exception:              # an executor failure is outcome_unknown; nothing of it is kept
                result = None
            kind, payload = (("executed", {"result": result, "as_of": as_of}) if result is not None
                             else ("outcome_unknown", {"reason": "executor_error"}))
            conflict = False
            with self._lock:
                try:
                    with self.ledger.transaction() as tx:
                        tx.append(kind, key, "system", payload)
                except LedgerConflict:
                    conflict = True
            if conflict or result is None:
                return ExecuteResult("outcome_unknown", None)
            return ExecuteResult("executed", result)
        finally:
            with self._lock:
                self._inflight.discard(key)

    def _execute_tx(self, tx: LedgerTx, key: Any, as_of: str) -> Any:
        state = self._state_tx(tx, key)

        def refuse(code: str, **extra: Any) -> _Refusal:
            tx.append("refused", _key_column(key), "system",
                      _refused_payload("execute", code, conclusion_id=state.conclusion_id if state else None,
                                       type_id=state.type_id if state else None, **extra))
            return _Refusal(code)

        if state is None:
            return refuse("unknown_key")
        if state.result is not None:
            return ExecuteResult("executed", state.result)
        if state.outcome_unknown:
            return ExecuteResult("outcome_unknown", None)
        if state.executing:
            if key in self._inflight:
                return ExecuteResult("in_progress", None)
            tx.append("outcome_unknown", key, "system", {"reason": "interrupted"})
            return ExecuteResult("outcome_unknown", None)
        if as_of < state.last_as_of:
            raise FollowupError("as_of is before the follow-up's last entry") from None
        if state.decision == "rejected":
            return refuse("rejected")
        if state.decision is None:
            return refuse("not_approved")
        view = self.view(state.conclusion_id)
        if view.status != "supported":
            return refuse("conclusion_no_longer_supported", status=view.status)
        kill = self.kill_switch.state(state.type_id)
        if kill.on:
            tx.append("blocked", key, "system", {"reason": "kill_switch", "source": kill.source, "as_of": as_of})
            return ExecuteResult("blocked_kill_switch", None)
        try:
            tx.append("executing", key, "system", {"as_of": as_of})
        except LedgerConflict:
            return ExecuteResult("in_progress", None)
        return state, view, state.draft(state.decided_version) if state.tier == "T1" else None

    # ------------------------------------------------------------------ escalation and outcome
    @staticmethod
    def _due(state: FollowupState, as_of: str) -> bool:
        return (state.status in _OPEN and not state.acknowledged and state.escalation is None
                and state.ack_due is not None and as_of > state.ack_due)

    def overdue(self, *, as_of: str) -> list[str]:
        """Escalate every unacknowledged open follow-up past its ``ack_due`` (strictly), once; the keys escalated."""
        as_of = normalise_as_of(as_of)
        done = []
        for candidate in self.states():
            if not self._due(candidate, as_of):
                continue
            with self._lock:
                with self.ledger.transaction() as tx:
                    state = self._state_tx(tx, candidate.key)
                    if not self._due(state, as_of):
                        continue
                    self._escalate(tx, state.key, self.pack.followups[state.type_id], state.assignment_unit,
                                   self._approvers(), "overdue", as_of)
            done.append(candidate.key)
        return sorted(done)

    def check_outcome(self, key: str, *, as_of: str, post_start: str | None = None) -> dict[str, Any]:
        """Measure recurrence after execution (``outcome.py``: measurement only, not causal); one ``outcome`` entry
        per distinct result."""
        as_of = normalise_as_of(as_of)
        with self._lock:
            with self.ledger.transaction() as tx:
                result = self._outcome_tx(tx, key, as_of, post_start)
            if isinstance(result, _Refusal):
                raise FollowupRefused(result.code) from None
        return result

    def _outcome_tx(self, tx: LedgerTx, key: Any, as_of: str, post_start: str | None) -> Any:
        state = self._state_tx(tx, key)

        def refuse(code: str) -> _Refusal:
            tx.append("refused", _key_column(key), "system",
                      _refused_payload("check_outcome", code, conclusion_id=state.conclusion_id if state else None,
                                       type_id=state.type_id if state else None))
            return _Refusal(code)

        if state is None:
            return refuse("unknown_key")
        if state.result is None:
            return refuse("not_executed")
        if as_of < state.last_as_of:
            raise FollowupError("as_of is before the follow-up's last entry") from None
        result = outcome.check(self.hq, self.pack, self.view(state.conclusion_id), as_of=as_of,
                               post_start=post_start)

        def bare(r: Mapping[str, Any]) -> bytes:
            return canonical_bytes({k: r[k] for k in r if k != "as_of"})

        if not state.outcomes or bare(state.outcomes[-1]) != bare(result):
            tx.append("outcome", state.key, "system", {"result": result, "as_of": as_of})
        return result

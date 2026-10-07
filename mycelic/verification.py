"""Downward verification: a deterministic, auditable answer to "was this memory derived correctly, and is it still
true?" for anyone who may read it, from a walk of its derivation DAG down to the raw notes at its leaves.

:func:`verify` loads the memory and everything it rests on through ``lineage_edges`` (breadth first, each frontier in
sorted order, at most ``max_nodes`` rows), checks every node, and returns a report and the codes it found.  It only
reads; the service runs it under the store lock, so it sees committed states only.

**What is checked.**  Every finding is a reason code with exactly one severity (table below), at most once per node.

* shape: every parent row exists, there is no cycle, a raw note has no parents, and each edge's ``parent_layer`` and
  ``contributed_by`` agree with the parent row (the two edge fields a digest does not cover, see ``integrity.py``);
* integrity: each row's keyed digest against the row as stored and its lineage edges (:func:`integrity.check_memory`);
* raw notes: the row agrees with its ``memory.observed`` event on every field the log carries that never changes after
  insert (labels through ``canonical_label``, so pre-v3 spellings pass), the events it cites are in the log, its
  producer is registered at its path in its organization, and its status agrees with the lifecycle events in the log
  (below);
* derived memories: their parents lie inside their unit and are eligible evidence, the support thresholds hold, and
  recomputing the memory from its stored parents with the stored derivation (``metadata.derivation``: the
  ``min_support`` or the rule snapshot it was derived under) reproduces its id, text, confidence and every covered
  field.  A memory whose parent fails its integrity check is not recomputed: the parent's reason stands, and the child
  is not blamed for it;
* currency, active derived memories only: the rule is still applied, enabled and unchanged, ``min_support`` is still
  the one configured, and the planner derives exactly this memory from the applied evidence now;
* status: every retracted or superseded node, the memory itself included;
* expiry: every active raw note whose ``expires_at`` is past (its retraction not applied yet), readable or not;
* freshness, with ``max_leaf_age``: how long ago the server ingested each raw note the caller can read (its event's
  ``created_at``, which a rebuild reproduces) or its producer last re-attested it (``attested_at``), whichever is later,
  never the producer's ``observed_at``.

**Verdict.**  ``failed`` on any E, else ``unverifiable`` on any U, else ``stale`` on any S, else ``verified``; warnings
(W) never change it.  ``derived_correctly`` is False on an E, None on a U, else True; ``still_true`` is None on an E or a
U, False on an S, else True.  All three come from the codes before redaction, so every viewer gets the same answer.

**Codes.**

* E (error, the derivation is wrong or was tampered with): ``missing_parent``, ``cycle_detected``,
  ``unexpected_parent`` (a raw note with parents), ``edge_mismatch``, ``integrity_mismatch``, ``integrity_downgraded``
  (an unkeyed digest where a key is set), ``integrity_unknown_key_forged`` (a key id this database never recorded),
  ``integrity_missing`` (no digest after the backfill completed), ``source_event_mismatch``, ``producer_unregistered``,
  ``status_inconsistent``, ``parent_outside_unit``, ``topic_mismatch``, ``parent_ineligible``, ``slot_uncovered``,
  ``below_min_support``, ``below_min_agents``, ``below_min_teams``, ``below_min_units``, ``rule_snapshot_mismatch``,
  ``id_mismatch``, ``text_mismatch``, ``confidence_mismatch``, ``content_mismatch``, ``not_derivable``, ``hidden_error``;
* U (unverifiable, something needed to check is not available): ``integrity_unknown_key`` (a recorded key that is no
  longer configured), ``integrity_not_backfilled``, ``source_event_missing``, ``cited_event_missing``,
  ``legacy_derivation`` (derived by an older release), ``walk_truncated``, ``replay_in_progress``,
  ``hidden_unverifiable``;
* S (stale, derived correctly but no longer current): ``retraction_pending``, ``update_pending``, ``node_retracted``,
  ``node_superseded``, ``leaf_expired``, ``leaf_stale``, ``min_support_changed``, ``rule_deleted``, ``rule_disabled``,
  ``rule_changed``, ``not_current``, ``hidden_stale``;
* W (warning): ``integrity_backfilled`` (signed by the start-up backfill), ``producer_revoked``, ``not_applied``,
  ``log_lag`` (events of the organization not applied yet; administrators only).

**Redaction.**  Every node shows its id, layer, scope (a raw note's team: its path would end in its producer's id),
operator, status and ``ok``.  A node the caller may not read (:meth:`Principal.can_read`) shows nothing more than that
and the codes lineage already shows (:data:`DISCLOSED`), without detail; every other code becomes ``hidden_error``,
``hidden_unverifiable`` or ``hidden_stale`` (:data:`HIDDEN`), and its warnings are only counted.  No detail on any node,
for any viewer, carries text, statements, metadata values, agent or producer ids or digests; key ids, ``as_of`` and
``log_lag`` are shown to administrators only.

**Determinism.**  Frontiers are sorted, so a truncated walk cuts at the same ids every time.  ``dag_digest`` hashes the
DAG's shape (ids, layers, edges, missing parents, truncation): the same for every viewer and on a rebuild from the log.
``report_digest`` hashes the verdict, both booleans, ``max_leaf_age``, the DAG digest and each node's status, ``ok`` and
codes as the caller sees them.  It leaves out details, warnings, times (``valid_until`` among them) and pointers, so
two calls agree, a rebuild agrees, and a row re-signed by the start-up backfill (warning ``integrity_backfilled``)
changes nothing.

**Validity.**  ``valid_until`` is the earliest ``expires_at`` of the active raw notes in the walk the caller can read
(None without one): until then no expiry the caller can see makes the memory stale.  ``valid_until_partial`` says the
walk was truncated or holds raw notes the caller may not read, whose expiries it does not show.

**Cost.**  About 0.1 ms per node plus one planner run per active derived memory, bounded by ``MYCELIC_VERIFY_MAX_NODES``
(``walk_truncated`` beyond it), plus one read of the organization's retractions logged since the oldest raw note's event
(all of them when a note's event row is gone), one of its agent removals and one of its updates not applied yet; the walk
holds the store lock and the event loop meanwhile.

**Unkeyed deployments** (``integrity_mode: unkeyed``).  Without ``MYCELIC_EVENT_SIGNING_KEY`` a digest detects
corruption, not edits: whoever can write the database can re-hash a row.  A re-hashed raw note is still caught by the
comparison with its event, and a derived memory is still recomputed from its parents.

**Lifecycle.**  A raw note's status is checked against the events that change it: the retractions that target it
(:data:`LIFECYCLE_KINDS`) and the removals of its producer (``agent.removed``, which retracts every note the agent has,
one logged after the removal included).  An active note with such an event applied, or a retracted one with none
applied, is ``status_inconsistent``; one still pending or published is ``retraction_pending``.  A superseded raw note
must be superseded by its producer's update: ``superseded_by`` names a raw note of the same producer and organization
whose ``metadata.version_of`` names it, in its row and in its ``memory.observed`` event, and the log applied that
event; anything else, or ``superseded_by`` on a note that is not superseded, is ``status_inconsistent``.  An active note
with an update still on its way through the log is ``update_pending``.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .aggregation import (
    CONSOLIDATABLE, MYCELIC_PRODUCER, Aggregator, build_conclusion, build_consolidation, contributing_agents,
    contributing_teams, contributing_units, rule_chain,
)
from .auth import Principal
from .hierarchy import LAYER_INDEX, child_unit_of, is_ancestor_or_self, parent_path, unit_at_layer
from .integrity import CONTENT_FIELDS, DERIVED_METADATA, LIFECYCLE_METADATA, Keyring, check_memory
from .models import (
    DERIVATION_VERSION, MEMORY_STATUS, LineageEdge, Memory, Rule, canonical_label, parse_iso, rule_digest, utc_seconds,
)
from .store import MycelicStore

logger = logging.getLogger(__name__)

VERDICTS = ("verified", "stale", "unverifiable", "failed")
#: E error, U unverifiable, S stale, W warning; codes and reasons sort in this order
SEVERITIES = ("E", "U", "S", "W")
#: every reason code and its severity
REASONS: dict[str, str] = {
    # shape
    "missing_parent": "E", "cycle_detected": "E", "unexpected_parent": "E", "edge_mismatch": "E",
    # integrity
    "integrity_mismatch": "E", "integrity_downgraded": "E", "integrity_unknown_key_forged": "E", "integrity_missing": "E",
    "integrity_unknown_key": "U", "integrity_not_backfilled": "U", "integrity_backfilled": "W",
    # raw notes
    "source_event_mismatch": "E", "producer_unregistered": "E", "status_inconsistent": "E", "source_event_missing": "U",
    "cited_event_missing": "U", "retraction_pending": "S", "update_pending": "S", "leaf_expired": "S", "producer_revoked": "W",
    "not_applied": "W",
    # derived memories
    "parent_outside_unit": "E", "topic_mismatch": "E", "parent_ineligible": "E", "slot_uncovered": "E",
    "below_min_support": "E", "below_min_agents": "E", "below_min_teams": "E", "below_min_units": "E",
    "rule_snapshot_mismatch": "E", "id_mismatch": "E", "text_mismatch": "E", "confidence_mismatch": "E",
    "content_mismatch": "E", "not_derivable": "E", "legacy_derivation": "U",
    # status, currency and freshness
    "node_retracted": "S", "node_superseded": "S", "leaf_stale": "S", "min_support_changed": "S", "rule_deleted": "S",
    "rule_disabled": "S", "rule_changed": "S", "not_current": "S",
    # the report as a whole
    "walk_truncated": "U", "replay_in_progress": "U", "log_lag": "W",
    # what a caller sees of a node it may not read
    "hidden_error": "E", "hidden_unverifiable": "U", "hidden_stale": "S",
}
REPORT_LEVEL = ("walk_truncated", "replay_in_progress")
#: codes a hidden node keeps: lineage already shows that a node is retracted or superseded and which parents are missing
DISCLOSED = ("node_retracted", "node_superseded", "missing_parent", "cycle_detected")
HIDDEN = {"E": "hidden_error", "U": "hidden_unverifiable", "S": "hidden_stale"}
#: events in the log that change a raw note's status
LIFECYCLE_KINDS = ("memory.retracted",)
MAX_LEAF_AGE_SECONDS = 315_360_000          # ten years
#: fields of a raw note that its ``memory.observed`` payload must carry unchanged (labels, confidence, expiry, cited
#: events and metadata are compared separately); what a rebuild would insert from the log
_PAYLOAD_FIELDS = ("memory_id", "org_id", "layer", "scope", "text", "kind", "support", "independent_teams", "producer_id",
                   "operator", "rule_id", "visibility", "created_at", "local_ref", "event_id")
_RULE_FIELDS = frozenset(Rule.__dataclass_fields__)
_ADMIN_DETAIL = ("key_id",)
_STRUCTURAL = ("parent_outside_unit", "topic_mismatch", "parent_ineligible", "slot_uncovered", "below_min_support",
               "below_min_agents", "below_min_teams", "below_min_units")


@dataclass(frozen=True)
class VerificationResult:
    """``report`` is what the caller sees; ``codes`` are the distinct codes before redaction, warnings included, for
    metrics and the administrators' audit log (never shown to the caller)."""

    report: dict[str, Any]
    codes: list[str]


class VerificationNotFound(KeyError):
    pass


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _rank(layer: Any) -> int:
    return LAYER_INDEX.get(layer, -1) if isinstance(layer, str) else -1


def _strs(values: Any) -> list[str]:
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


@functools.lru_cache(maxsize=4096)
def _team_of(scope: str) -> str | None:
    """What lineage shows of a hidden raw note's scope: its team (None for a path that is not one)."""
    try:
        return unit_at_layer(scope, "team")
    except (ValueError, TypeError):
        return None


def _payload_mismatch(m: Memory, kind: str, p: dict[str, Any]) -> list[str]:
    """The fields on which a raw note and its ``memory.observed`` event disagree (a malformed value is a mismatch)."""
    out = [] if kind == "memory.observed" else ["event_kind"]
    out += [k for k in _PAYLOAD_FIELDS if p.get(k) != getattr(m, k)]
    for k in ("topic", "slot", "entity"):
        try:
            if canonical_label(p.get(k)) != getattr(m, k):
                out.append(k)
        except TypeError:
            out.append(k)
    expires = p.get("expires_at")
    if expires is not None:             # the apply stores it in UTC to the second; a malformed value stays itself
        expires = utc_seconds(expires) or expires
    if expires != m.expires_at:
        out.append("expires_at")
    c = p.get("confidence")
    if isinstance(c, bool) or not isinstance(c, (int, float)) or round(float(c), 6) != round(float(m.confidence), 6):
        out.append("confidence")
    try:
        if sorted(p.get("source_event_ids") or []) != sorted(m.source_event_ids):
            out.append("source_event_ids")
    except TypeError:
        out.append("source_event_ids")
    meta = p.get("metadata") or {}
    if not isinstance(meta, dict) or ({k: v for k, v in meta.items() if k not in LIFECYCLE_METADATA}
                                      != {k: v for k, v in m.metadata.items() if k not in LIFECYCLE_METADATA}):
        out.append("metadata")
    return sorted(out)


class _Verification:
    """One verification: the walk, the findings per node, and the report built from them."""

    def __init__(self, store: MycelicStore, aggregator: Aggregator, keyring: Keyring, root: Memory, principal: Principal,
                 now: datetime, max_nodes: int, max_leaf_age: int | None) -> None:
        self.store, self.aggregator, self.keyring, self.root = store, aggregator, keyring, root
        self.principal, self.now, self.max_nodes, self.max_leaf_age = principal, now, max_nodes, max_leaf_age
        self.nodes: dict[str, Memory] = {root.memory_id: root}
        self.edges: dict[str, list[LineageEdge]] = {}
        self.missing: set[str] = set()
        self.truncated = False
        self.found: dict[str, dict[str, dict[str, Any]]] = {}      # memory id -> code -> detail
        self.top: dict[str, dict[str, Any]] = {}                  # report-level code -> detail
        self.integrity_ok: dict[str, bool] = {}
        self.events: dict[str, Any] = {}

    def add(self, memory_id: str, code: str, detail: dict[str, Any] | None = None) -> None:
        """Record a finding; a second one with the same code merges its detail (lists united and sorted)."""
        entry = self.found.setdefault(memory_id, {}).setdefault(code, {})
        for k, v in (detail or {}).items():
            if isinstance(v, list):
                entry[k] = sorted(set(entry.get(k, [])) | set(v))
            else:
                entry.setdefault(k, v)

    def codes(self, memory_id: str) -> dict[str, dict[str, Any]]:
        return self.found.get(memory_id, {})

    def has_error(self, memory_id: str) -> bool:
        return any(REASONS[c] == "E" for c in self.codes(memory_id))

    # ------------------------------------------------------------------ walk
    def walk(self) -> None:
        """Breadth first, one batched read of edges and one of rows per level, frontiers sorted (a truncation cuts at the
        same ids every time).  On truncation the edges of the last level loaded are read too, so every loaded node's
        integrity can be checked."""
        frontier = [self.root.memory_id]
        while frontier:
            got = self.store.parents_of_many(frontier)
            for child in frontier:
                self.edges[child] = got.get(child, [])
            if self.truncated:
                break
            want = sorted({e.parent_id for c in frontier for e in self.edges[c]} - set(self.nodes) - self.missing)
            room = self.max_nodes - len(self.nodes)
            if len(want) > room:
                self.truncated = True
                want = want[:max(0, room)]
            rows = self.store.get_memories(want)
            self.missing |= set(want) - set(rows)
            self.nodes.update(rows)
            frontier = sorted(rows)

    def parents(self, memory_id: str) -> list[str]:
        return [e.parent_id for e in self.edges.get(memory_id, [])]

    def explored(self, memory_id: str) -> bool:
        return memory_id in self.edges and all(p in self.nodes or p in self.missing for p in self.parents(memory_id))

    # ------------------------------------------------------------------ (a) shape
    def check_shape(self) -> None:
        for mid in sorted(self.nodes):
            gone = sorted(p for p in self.parents(mid) if p in self.missing)
            if gone:
                self.add(mid, "missing_parent", {"parent_ids": gone})
            if self.nodes[mid].operator == "agent_observation" and self.edges.get(mid):
                self.add(mid, "unexpected_parent", {"parents": len(self.edges[mid])})
        # white/grey/black depth-first colouring from the root: an edge to a grey node closes a cycle
        colour = {self.root.memory_id: 1}
        stack = [(self.root.memory_id, iter(sorted(set(self.parents(self.root.memory_id)))))]
        while stack:
            mid, it = stack[-1]
            parent = next(it, None)
            if parent is None:
                colour[mid] = 2
                stack.pop()
            elif parent in self.nodes:
                if colour.get(parent) == 1:
                    self.add(mid, "cycle_detected", {"parent_ids": [parent]})
                elif parent not in colour:
                    colour[parent] = 1
                    stack.append((parent, iter(sorted(set(self.parents(parent))))))

    def check_edges(self) -> None:
        """Edge fields the digest does not cover, against parents whose own integrity holds."""
        for mid in sorted(self.edges):
            bad = []
            for e in self.edges[mid]:
                p = self.nodes.get(e.parent_id)
                if p is None or not self.integrity_ok.get(p.memory_id):
                    continue
                if e.parent_layer != p.layer or e.contributed_by != (p.producer_id if p.layer == "agent" else MYCELIC_PRODUCER):
                    bad.append(p.memory_id)
            if bad:
                self.add(mid, "edge_mismatch", {"parent_ids": bad})

    # ------------------------------------------------------------------ (b) integrity
    def check_integrity(self) -> None:
        columns = self.store.integrity_of(list(self.nodes))
        try:
            known = json.loads(self.store.get_meta("known_key_ids") or "[]")
        except json.JSONDecodeError:
            known = []
        known = [k for k in known if isinstance(k, str)] if isinstance(known, list) else []
        complete = self.store.get_meta("integrity_backfill_complete") == "1"
        for mid in sorted(self.nodes):
            digest, kid, origin = columns.get(mid, (None, None, None))
            outcome = check_memory(self.keyring, self.nodes[mid], self.parents(mid), digest, kid, origin)
            self.integrity_ok[mid] = outcome == "ok"
            if outcome == "ok":
                if origin == "backfill":
                    self.add(mid, "integrity_backfilled")
            elif outcome == "mismatch":
                self.add(mid, "integrity_mismatch")
            elif outcome == "downgraded":
                self.add(mid, "integrity_downgraded")
            elif outcome == "unknown_key":
                self.add(mid, "integrity_unknown_key" if kid in known else "integrity_unknown_key_forged", {"key_id": kid})
            else:
                self.add(mid, "integrity_missing" if complete else "integrity_not_backfilled")

    # ------------------------------------------------------------------ (c), (d) raw notes
    def check_leaves(self) -> None:
        leaves = [self.nodes[mid] for mid in sorted(self.nodes) if self.nodes[mid].operator == "agent_observation"]
        if not leaves:
            return
        self.events = self.store.get_events([m.event_id for m in leaves if isinstance(m.event_id, str)]
                                            + [e for m in leaves for e in _strs(m.source_event_ids)])
        agents = self.store.get_agents(sorted({m.producer_id for m in leaves}))
        # a retraction is logged after the note's own event, so only the log from the first of those is read; when a
        # note's event row is gone there is no such bound, and the whole log is read
        own = [m for m in leaves if m.org_id == self.root.org_id]
        logged = [m.event_id for m in own if isinstance(m.event_id, str) and m.event_id in self.events]
        since = logged if len(logged) == len(own) else ()
        lifecycle = self.store.lifecycle_events(self.root.org_id, LIFECYCLE_KINDS, since=since) if own else {}
        removals = self.store.removal_events(self.root.org_id, {m.producer_id for m in own})
        updates = self.store.unapplied_updates(self.root.org_id, [m.memory_id for m in own if m.status == "active"])
        successors = self.store.get_memories([m.superseded_by for m in own if isinstance(m.superseded_by, str)])
        successor_events = self.store.get_events([s.event_id for s in successors.values() if isinstance(s.event_id, str)])
        for m in leaves:
            if m.org_id != self.root.org_id:
                continue                            # parent_outside_unit on its child explains it
            mid = m.memory_id
            ev = self.events.get(m.event_id) if isinstance(m.event_id, str) else None
            if ev is None:
                self.add(mid, "source_event_missing", {"event_id": m.event_id})
            else:
                fields = _payload_mismatch(m, ev.kind, ev.payload if isinstance(ev.payload, dict) else {})
                if fields:
                    self.add(mid, "source_event_mismatch", {"fields": fields})
            cited = sorted({e for e in _strs(m.source_event_ids) if e not in self.events})
            if cited:
                self.add(mid, "cited_event_missing", {"event_ids": cited})
            agent = agents.get(m.producer_id)
            if agent is None or agent.org_id != m.org_id or agent.path != m.scope:
                self.add(mid, "producer_unregistered")
            elif agent.status == "revoked":
                self.add(mid, "producer_revoked")
            if m.applied_at is None:
                self.add(mid, "not_applied")
            states = [status for _, status in lifecycle.get(mid, []) + removals.get(m.producer_id, [])]
            applied = "applied" in states
            if m.status == "superseded":
                # only the producer's own update supersedes a raw note, once it applied; a retraction applied later was
                # ignored, so the retractions are not read
                if not self.superseded_by_update(m, successors, successor_events):
                    self.add(mid, "status_inconsistent")
            elif ((m.status == "active" and applied) or (m.status == "retracted" and not applied and ev is not None)
                  or m.superseded_by is not None or m.status not in MEMORY_STATUS):
                self.add(mid, "status_inconsistent")
            if "pending" in states or "published" in states:
                self.add(mid, "retraction_pending")
            if m.status == "active" and updates.get(mid):
                self.add(mid, "update_pending")
            # judged whoever asks (a hidden node shows hidden_stale), so the verdict is the same for every viewer
            expires = parse_iso(m.expires_at) if isinstance(m.expires_at, str) else None
            if m.status == "active" and expires is not None and expires <= self.now:
                self.add(mid, "leaf_expired", {"expires_at": m.expires_at})

    @staticmethod
    def superseded_by_update(m: Memory, successors: dict[str, Memory], events: dict[str, Any]) -> bool:
        """Is ``m`` superseded by a raw note of its producer and organization that names it (``metadata.version_of``), row
        and log alike, and whose ``memory.observed`` event the log has applied?"""
        nxt = successors.get(m.superseded_by) if isinstance(m.superseded_by, str) else None
        ev = events.get(nxt.event_id) if nxt is not None and isinstance(nxt.event_id, str) else None
        if nxt is None or ev is None:
            return False
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        logged = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return (nxt.operator == "agent_observation" and (nxt.producer_id, nxt.org_id) == (m.producer_id, m.org_id)
                and isinstance(nxt.metadata, dict) and nxt.metadata.get("version_of") == m.memory_id
                and ev.kind == "memory.observed" and ev.status == "applied" and payload.get("memory_id") == nxt.memory_id
                and logged.get("version_of") == m.memory_id)

    # ------------------------------------------------------------------ (e) status
    def check_status(self) -> None:
        for mid, m in self.nodes.items():
            if m.status == "retracted":
                self.add(mid, "node_retracted")
            elif m.status == "superseded":
                self.add(mid, "node_superseded")

    # ------------------------------------------------------------------ (f) derived memories
    def check_derived(self) -> None:
        for mid in sorted(self.nodes):
            m = self.nodes[mid]
            if m.operator == "agent_observation" or not self.explored(mid):
                continue
            ids = self.parents(mid)
            if any(p in self.missing for p in ids) or not all(self.integrity_ok.get(p) for p in ids):
                continue                            # the parent's own reason stands; the child is not blamed
            derivation = m.metadata.get("derivation")
            if not isinstance(derivation, dict) or derivation.get("v") != DERIVATION_VERSION:
                self.add(mid, "legacy_derivation", {"version": derivation.get("v") if isinstance(derivation, dict) else None})
                continue
            parents = [self.nodes[p] for p in ids]
            try:
                if m.operator == "topic_consolidation":
                    self.rederive_consolidation(m, parents, derivation)
                elif m.operator == "slot_composition":
                    self.rederive_conclusion(m, parents, derivation)
                else:
                    self.add(mid, "not_derivable")
            except Exception as exc:                # never the text: the id and the exception's type
                logger.warning("re-derivation of %s failed: %s", mid, type(exc).__name__)
                self.add(mid, "not_derivable")

    def rederive_consolidation(self, m: Memory, parents: list[Memory], derivation: dict[str, Any]) -> None:
        mid = m.memory_id
        outside = sorted(p.memory_id for p in parents
                         if p.org_id != m.org_id or not p.scope.startswith(m.scope + "/") or _rank(p.layer) >= _rank(m.layer))
        if outside:
            self.add(mid, "parent_outside_unit", {"parent_ids": outside})
        off_topic = sorted(p.memory_id for p in parents if p.topic != m.topic)
        if off_topic:
            self.add(mid, "topic_mismatch", {"parent_ids": off_topic})
        ineligible = sorted(p.memory_id for p in parents if p.operator not in CONSOLIDATABLE)
        if ineligible:
            self.add(mid, "parent_ineligible", {"parent_ids": ineligible})
        min_support = derivation.get("min_support")
        promotion = m.metadata.get("promoted_from") is not None
        effective = 1 if promotion else min_support
        child_of = {scope: child_unit_of(scope, m.scope) for scope in {p.scope for p in parents if p.memory_id not in outside}}
        children = set(child_of.values())
        # plan_consolidation's rule: fewer than min_support child units contribute (and are registered), each exactly its
        # own consolidation; with min_support 3 a unit whose two children both have one promotes both
        promoted_ok = (0 < len(children) == len(parents) < min_support
                       and (m.metadata.get("registered_child_units") or 0) < min_support
                       and all(p.operator == "topic_consolidation" and parent_path(p.scope) == m.scope for p in parents))
        if len(children) < effective or (promotion and not promoted_ok):
            self.add(mid, "below_min_support", {"children": len(children), "required": effective})
        if outside:
            return                                  # evidence outside the unit has no child unit to be grouped under
        # grouped in child order as the aggregator builds them: a promotion's promoted_from is its first parent
        contributions: dict[str, list[Memory]] = {}
        for p in sorted(parents, key=lambda p: (child_of[p.scope], p.memory_id)):
            contributions.setdefault(child_of[p.scope], []).append(p)
        rebuilt = build_consolidation(m.org_id, m.scope, m.topic, contributions, promotion=promotion,
                                      effective_min_support=effective,
                                      registered_child_units=m.metadata.get("registered_child_units") or 0,
                                      version_of=None, min_support=min_support, now=self.verified_at)
        self.compare(m, rebuilt)

    def rederive_conclusion(self, m: Memory, parents: list[Memory], derivation: dict[str, Any]) -> None:
        mid = m.memory_id
        snapshot = derivation.get("rule")
        try:
            rule = Rule(**{k: v for k, v in snapshot.items() if k in _RULE_FIELDS})
            consistent = rule_digest(rule) == derivation.get("rule_digest")
        except Exception:
            consistent = False
        if not consistent:
            self.add(mid, "rule_snapshot_mismatch")
            return
        outside = sorted(p.memory_id for p in parents
                         if p.org_id != m.org_id or not is_ancestor_or_self(m.scope, p.scope) or _rank(p.layer) > _rank(m.layer))
        if outside:
            self.add(mid, "parent_outside_unit", {"parent_ids": outside})
        ineligible = sorted(p.memory_id for p in parents
                            if p.operator not in rule.sources or p.slot not in rule.required_slots
                            or (rule.topic_prefix and not (p.topic or "").startswith(rule.topic_prefix))
                            or (m.entity is not None and p.entity != m.entity) or rule.rule_id in rule_chain(p))
        if ineligible:
            self.add(mid, "parent_ineligible", {"parent_ids": ineligible})
        uncovered = [s for s in rule.required_slots if not any(p.slot == s for p in parents)]
        if uncovered:
            self.add(mid, "slot_uncovered", {"slots": uncovered})
        agents = {a for p in parents for a in contributing_agents(p)}
        if len(agents) < rule.min_agents:
            self.add(mid, "below_min_agents", {"count": len(agents), "required": rule.min_agents})
        teams = {t for p in parents for t in contributing_teams(p)}
        if len(teams) < rule.min_teams:
            self.add(mid, "below_min_teams", {"count": len(teams), "required": rule.min_teams})
        for slot, per in sorted(rule.min_units.items()):
            for layer, n in sorted(per.items()):
                count = len({u for p in parents if p.slot == slot for u in contributing_units(p, layer)})
                if count < n:
                    self.add(mid, "below_min_units", {"slot": slot, "layer": layer, "count": count, "required": n})
        # the stored evidence as the candidates reproduces the original selection: with corroborate the evidence is every
        # candidate, without it one selected memory per slot
        built = build_conclusion(rule, m.org_id, m.scope, m.entity, parents, version_of=None, now=self.verified_at)
        if built is None:
            if not any(c in _STRUCTURAL for c in self.codes(mid)):
                self.add(mid, "not_derivable")
            return
        self.compare(m, built[0])

    def compare(self, m: Memory, r: Memory) -> None:
        mid = m.memory_id
        if r.memory_id != mid:
            self.add(mid, "id_mismatch", {"recomputed_id": r.memory_id})
        if r.text != m.text:
            self.add(mid, "text_mismatch")
        if round(float(r.confidence), 6) != round(float(m.confidence), 6):
            self.add(mid, "confidence_mismatch", {"stored": round(float(m.confidence), 6), "recomputed": round(float(r.confidence), 6)})
        fields = [f for f in CONTENT_FIELDS if f not in ("memory_id", "text") and getattr(r, f) != getattr(m, f)]
        fields += [f"metadata.{k}" for k in DERIVED_METADATA if r.metadata.get(k) != m.metadata.get(k)]
        if fields:
            self.add(mid, "content_mismatch", {"fields": fields})

    # ------------------------------------------------------------------ (g) currency
    def check_currency(self) -> None:
        for mid in sorted(self.nodes):
            m = self.nodes[mid]
            derivation = m.metadata.get("derivation")
            if (m.status != "active" or m.operator == "agent_observation" or not isinstance(derivation, dict)
                    or derivation.get("v") != DERIVATION_VERSION):
                continue
            try:
                self.currency(m, derivation)
            except Exception as exc:
                logger.warning("planning %s for verification failed: %s", mid, type(exc).__name__)
                self.add(mid, "not_current", {"planned_id": None})

    def currency(self, m: Memory, derivation: dict[str, Any]) -> None:
        mid = m.memory_id
        if m.operator == "topic_consolidation" and derivation.get("min_support") != self.aggregator.min_support:
            self.add(mid, "min_support_changed", {"derived_under": derivation.get("min_support"),
                                                  "configured": self.aggregator.min_support})
        elif m.operator == "slot_composition":
            applied = self.store.get_applied_rule(m.rule_id) if m.rule_id else None
            if applied is None:
                self.add(mid, "rule_deleted")
            elif not applied.enabled:
                self.add(mid, "rule_disabled")
            elif (applied.org_id not in (None, m.org_id) or applied.target_layer != m.layer
                  or rule_digest(applied) != derivation.get("rule_digest")):
                self.add(mid, "rule_changed", {"rule_id": m.rule_id})
        if (not self.explored(mid) or self.has_error(mid)
                or any(c == "min_support_changed" or c.startswith("rule_") for c in self.codes(mid))):
            return                                  # explained already (rule_snapshot_mismatch is an E)
        plan = self.aggregator.plan_for(m)
        planned = plan.memory.memory_id if plan is not None and plan.memory is not None else None
        if planned != mid:
            self.add(mid, "not_current", {"planned_id": planned})

    # ------------------------------------------------------------------ (h) freshness, (i) the report as a whole
    def check_freshness(self) -> bool:
        """leaf_stale on every readable raw note ingested, and not re-attested since, more than ``max_leaf_age`` seconds
        ago; returns whether some raw note could not be judged (hidden, no usable event row, or not loaded)."""
        partial = self.truncated
        for mid in sorted(self.nodes):
            m = self.nodes[mid]
            if m.operator != "agent_observation":
                continue
            if not self.principal.can_read(m):
                partial = True
                continue
            ev = self.events.get(m.event_id) if isinstance(m.event_id, str) else None
            ingested = parse_iso(ev.created_at) if ev is not None and isinstance(ev.created_at, str) else None
            if ingested is None:
                partial = True
                continue
            attested = parse_iso(m.attested_at) if isinstance(m.attested_at, str) else None
            age = (self.now - max(ingested, attested or ingested)).total_seconds()
            if age > self.max_leaf_age:             # type: ignore[operator]
                self.add(mid, "leaf_stale", {"age_seconds": int(age), "max_leaf_age": self.max_leaf_age})
        return partial

    def check_report_level(self) -> None:
        if self.store.get_meta("replay_target_seq") is not None:
            self.top["replay_in_progress"] = {}
        if self.truncated:
            self.top["walk_truncated"] = {}
        lag = self.store.unapplied_events(self.root.org_id)
        if lag:
            self.top["log_lag"] = {"unapplied_events": lag}

    # ------------------------------------------------------------------ report
    @property
    def verified_at(self) -> str:
        return self.now.isoformat(timespec="seconds")

    def detail(self, detail: dict[str, Any]) -> dict[str, Any]:
        if self.principal.is_admin:
            return dict(detail)
        return {k: v for k, v in detail.items() if k not in _ADMIN_DETAIL}

    def run(self) -> VerificationResult:
        self.walk()
        self.check_integrity()
        for mid, m in list(self.nodes.items()):
            if not isinstance(m.metadata, dict):    # a row whose metadata is not an object failed its integrity check
                self.nodes[mid] = dataclasses.replace(m, metadata={})
        self.root = self.nodes[self.root.memory_id]
        self.check_shape()
        self.check_edges()
        self.check_leaves()
        self.check_status()
        self.check_derived()
        self.check_currency()
        partial = self.check_freshness() if self.max_leaf_age is not None else False
        self.check_report_level()
        return self.report(partial)

    def report(self, freshness_partial: bool) -> VerificationResult:
        severity_order = {s: i for i, s in enumerate(SEVERITIES)}

        def order(code: str) -> tuple[int, str]:
            return severity_order[REASONS[code]], code

        unredacted = {REASONS[c] for codes in self.found.values() for c in codes} | {REASONS[c] for c in self.top}
        verdict = ("failed" if "E" in unredacted else "unverifiable" if "U" in unredacted
                   else "stale" if "S" in unredacted else "verified")
        derived_correctly = False if "E" in unredacted else None if "U" in unredacted else True
        still_true = None if {"E", "U"} & unredacted else False if "S" in unredacted else True

        views: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        hidden_warnings = redacted = 0
        worst: dict[str, int] = {"E": 0, "U": 0, "S": 0}
        unexplored = 0
        for mid in sorted(self.nodes, key=lambda i: (-_rank(self.nodes[i].layer), i)):
            m = self.nodes[mid]
            found = self.codes(mid)
            bad = sorted((c for c in found if REASONS[c] != "W"), key=order)
            if bad:
                worst[REASONS[bad[0]]] += 1
            unexplored += not self.explored(mid)
            readable = self.principal.can_read(m)
            if readable:
                reasons = [{"code": c, "severity": REASONS[c], **({"detail": d} if (d := self.detail(found[c])) else {})}
                           for c in bad]
                warnings += [{"code": c, "memory_id": mid, **({"detail": d} if (d := self.detail(found[c])) else {})}
                             for c in sorted(found) if REASONS[c] == "W"]
            else:
                redacted += 1
                hidden_warnings += sum(REASONS[c] == "W" for c in found)
                shown = sorted({c if c in DISCLOSED else HIDDEN[REASONS[c]] for c in bad}, key=order)
                reasons = [{"code": c, "severity": REASONS[c]} for c in shown]
            # a raw note's path ends in its producer's id: the report shows its team, for every viewer
            scope = _team_of(m.scope) if m.layer == "agent" or m.operator == "agent_observation" else m.scope
            for r in reasons:
                counts[r["code"]] = counts.get(r["code"], 0) + 1
            views.append({"memory_id": mid, "layer": m.layer, "scope": scope, "operator": m.operator, "status": m.status,
                          "redacted": not readable, "ok": not bad, "reasons": reasons})
        for code in REPORT_LEVEL:
            if code in self.top:
                counts[code] = 1
        if "log_lag" in self.top and self.principal.is_admin:
            warnings.append({"code": "log_lag", "detail": dict(self.top["log_lag"])})
        warnings.sort(key=lambda w: (w["code"], w.get("memory_id") or ""))
        reasons = [{"code": c, "severity": REASONS[c], "count": counts[c]} for c in sorted(counts, key=order)]

        dag_digest = _digest({"nodes": sorted([mid, m.layer] for mid, m in self.nodes.items()),
                              "edges": sorted([e.child_id, e.parent_id] for edges in self.edges.values() for e in edges),
                              "missing": sorted(self.missing), "truncated": self.truncated})
        report_digest = _digest({"memory_id": self.root.memory_id, "verdict": verdict, "derived_correctly": derived_correctly,
                                 "still_true": still_true, "max_leaf_age": self.max_leaf_age, "dag_digest": dag_digest,
                                 "reasons": [[r["code"], r["severity"], r["count"]] for r in reasons],
                                 "nodes": [[v["memory_id"], v["status"], v["ok"], sorted(r["code"] for r in v["reasons"])]
                                           for v in views]})
        leaves = sum(m.operator == "agent_observation" for m in self.nodes.values())
        raw = [m for m in self.nodes.values() if m.operator == "agent_observation"]
        expiries = [m.expires_at for m in raw if m.status == "active" and isinstance(m.expires_at, str)
                    and self.principal.can_read(m)]
        report: dict[str, Any] = {
            "memory_id": self.root.memory_id, "verdict": verdict, "derived_correctly": derived_correctly,
            "still_true": still_true, "reasons": reasons, "warnings": warnings,
            "summary": {"nodes": len(self.nodes), "derived": len(self.nodes) - leaves, "leaves": leaves, "redacted": redacted,
                        "failed_nodes": worst["E"], "unverifiable_nodes": worst["U"], "stale_nodes": worst["S"],
                        "unexplored": unexplored, "hidden_warnings": hidden_warnings},
            "superseded_by": self.successor(), "current_version": self.current_version(),
            "freshness_partial": freshness_partial, "valid_until": min(expiries) if expiries else None,
            "valid_until_partial": self.truncated or not all(self.principal.can_read(m) for m in raw),
            "integrity_mode": "keyed" if self.keyring.keyed else "unkeyed",
            "verified_at": self.verified_at, "max_leaf_age": self.max_leaf_age,
            "nodes": views, "dag_digest": dag_digest, "report_digest": report_digest,
        }
        if self.principal.is_admin:
            try:
                last = int(self.store.get_meta("last_applied_seq") or 0)
            except ValueError:
                last = 0
            report["as_of"] = {"last_applied_seq": last}
        codes = sorted({c for found in self.found.values() for c in found} | set(self.top))
        return VerificationResult(report=report, codes=codes)

    def successor(self) -> str | None:
        root = self.root
        if root.status != "superseded" or not root.superseded_by:
            return None
        nxt = self.store.get_memory(root.superseded_by)
        return nxt.memory_id if nxt is not None and self.principal.can_read(nxt) else None

    def current_version(self) -> str | None:
        """The active memory of the root's (unit, operator, key) when that is not the root: after a retraction the only
        pointer from a retired derived memory to the one that replaced it."""
        root = self.root
        key = root.metadata.get("agg_key") if root.operator != "agent_observation" else None
        if not isinstance(key, str):
            return None
        current = self.store.current_derived(root.org_id, root.operator, root.scope, key)
        if current is None or current.memory_id == root.memory_id or not self.principal.can_read(current):
            return None
        return current.memory_id


def verify(store: MycelicStore, aggregator: Aggregator, keyring: Keyring, memory_id: str, *, principal: Principal,
           now: datetime, max_nodes: int, max_leaf_age: int | None = None) -> VerificationResult:
    """Verify ``memory_id`` for ``principal`` (who must be able to read it: the service checks) as of ``now``.

    Synchronous and read-only.  ``aggregator`` plans currency: pass :meth:`Aggregator.planner` so that verification
    counts nothing in the aggregation metrics; its ``min_support`` is the configured one.
    """
    root = store.get_memory(memory_id)
    if root is None:
        raise VerificationNotFound(memory_id)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return _Verification(store, aggregator, keyring, root, principal, now, max(1, int(max_nodes)), max_leaf_age).run()

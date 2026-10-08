"""Aggregation: how agent observations become team, department, subsidiary, region and enterprise memories.

Two deterministic operators run inside the consumer's apply transaction.  No model is involved, so every
derived memory can be explained from its lineage alone and a replay of the event log reproduces it exactly.
Each operator is a read-only planner (:meth:`Aggregator.plan_consolidation`, :meth:`Aggregator.plan_rule`) that
reads the evidence, rules and registry as the log has applied them, and a pure builder (:func:`build_consolidation`,
:func:`build_conclusion`) that turns that evidence into the memory; the apply path persists what the planner returns.

**Topic consolidation** (``operator='topic_consolidation'``).  A unit U at layer L gets a memory on topic T
when at least ``min_support`` of its *direct children* contribute something on T.  A child's contribution
is its own consolidation on T if it has one, otherwise the best material in its subtree (recursively down to
the raw agent observations), and in either case every rule conclusion on T that sits at the child itself: neither
replaces the other.  Parents of the derived memory are exactly those contributions, so the lineage
records the chain of units the knowledge passed through, and ``support``/``independent_teams`` count the
distinct agents and teams underneath.  When more evidence arrives the coalition grows, a new derived memory
(new deterministic id) supersedes the old one, and the old one stays readable as a previous version.

A unit with fewer registered child units than ``min_support`` (a subsidiary with one department) also holds once one
of its children contributes that child's own consolidation (which already stands for ``min_support`` agents): it
*promotes* it, together with whatever its other children contribute, so more evidence never takes a consolidation
away.  A raw note is never promoted on its own.

**Disputes.**  A note may carry a structured claim, ``metadata.value``, for its slot and entity ("closed", "open"),
compared in its canonical spelling (``models.canonical_value``: "Open" and "open" agree; a value that is not a string of
at most 200 characters, which an earlier client may have stored, claims nothing).
Notes on the same slot and entity whose values differ dispute each other: their consolidation carries
``metadata.conflict`` (true), takes the confidence of its strongest contribution instead of raising it, and claims no
slot, so no rule takes a disputed consolidation as evidence; the flag travels up with every consolidation built on it.
A rule conclusion whose evidence pool claims two values for one slot and entity is flagged too: a corroborating rule
does not raise that slot's confidence, and a rule without ``corroborate`` takes the strongest memory claiming each value
as evidence next to its selection, so both sides are in its lineage; a conclusion resting on a disputed memory is
flagged as well.  Every value beneath a
unit is compared, whatever its note's visibility: a consolidation that is not disputed keeps the values beneath it as
digests (``metadata.claims``, :func:`claims_of`, never shown to readers), so two teams whose team-visibility notes
disagree are flagged above them, and only that bit leaves either team.  A consolidation whose value-carrying parents
agree carries that ``value``, counted only from what may travel upward (org-visible notes and the values of child
consolidations): a team-visibility note's value never leaves its team.  Free text is never compared, so notes without
a value never dispute anything.

**Slot composition** (``operator='slot_composition'``).  A :class:`~mycelic.models.Rule` names the slots a
conclusion needs (for example ``transport_disruption``, ``supplier_buffer_low``, ``demand_commitment``).  The
conclusion exists only once every slot is covered for the same entity by observations from at least
``min_agents`` agents in at least ``min_teams`` teams inside the rule's target unit.  Slot selection follows
``RuleBasedSynthesizer``'s order (the strongest memory per slot); when that selection misses the thresholds of a rule
without ``corroborate`` (one agent is the strongest in several slots), the rule takes the best selection that meets
them (:func:`_coalition`, a bounded search that keeps the best selection it found), so agreeing evidence never takes a
conclusion away.  A '*' conclusion (no entity) selects within the evidence about one entity and the evidence that names
none, and exists only when its selection includes the latter (:func:`_wildcard_scopes`, :func:`wildcard_selection`):
evidence about two entities is never stitched together; with ``corroborate`` that pool is its evidence.  The pools share
the evidence that names no entity, which is read once per evaluation, and one search (one bound) covers all of them.
The support metrics reuse ``LineageAnalyzer`` from the coordination package, with each raw observation as its own
lineage root and its team as its failure domain.

**Composition and strategic synthesis.**  A rule may name ``sources`` other than raw observations, so the
conclusions of one rule (carrying ``emits_slot``) or the consolidations of a unit become evidence for a higher
rule; ``min_units`` demands the evidence span distinct units of a layer ("supply risk in two regions") and
``corroborate`` keeps every memory that fills a slot as a parent.  Derivations cascade: a new conclusion is
itself offered to the rules and consolidations above it, so an enterprise strategy can rest on regional
conclusions that rest on team consolidations that rest on agents' notes, and the lineage records every hop.
When evidence is retracted, every dependent conclusion is retired and then re-evaluated on what remains.

**Lifecycle and ids.**  A derived memory exists exactly when its operator holds on what the log has applied.  New
evidence is offered upward as it applies (:meth:`Aggregator.derive_for`); a rule event applies the rule to everything
applied before it (:meth:`Aggregator.evaluate_rule`, :meth:`Aggregator.withdraw_rule`); a registry event re-plans the
promotions whose unit gained or lost a child (:meth:`Aggregator.registry_changed`); and the re-aggregation job
(:meth:`Aggregator.reaggregate_step`) converges state derived by an older release or under another ``min_support``.
A derived id is ``sha256(operator, unit, versioned key, sorted parent ids)``: the versioned key adds the derivation
version and ``min_support`` to a topic (:func:`consolidation_id_key`) or the rule's digest to a rule and entity
(:func:`conclusion_id_key`), so an equal derivation keeps its id and a different one never reuses it.

**What a consolidation says.**  The readers of a derived memory are all members of its unit.  A team consolidation
(read only by that team) quotes its team's notes, team-visibility and org-visible alike; every consolidation above team
level quotes only org-visible notes and rule conclusions, and counts the team-visibility notes beneath it without
quoting them.  No consolidation adds an agent id: a team consolidation quotes without a prefix, one above team prefixes
each statement with the child unit it came from.  The quoted statements are kept as ``metadata.statements`` with their
origins (``statement_origins``: ``team``, ``org`` or ``rule``), and a consolidation parent contributes those
statements, never its text.  Every derived text is at most :data:`MAX_DERIVED_TEXT` characters, and a consolidation's
text and statements are a function of its parent set alone (:func:`consolidation_statements`), never of apply order
or the clock, so a rebuild reproduces them.  Raw text and labels are the producer's content and are quoted as written.
A rule template that quotes ``{slot:...}`` is an operator's decision to publish the quoted evidence, whatever its
visibility, at the rule's target layer and, through consolidations of the conclusion's topic, at every layer above it.

Confidence of a consolidation is the noisy-OR of the strongest contribution per child
(``1 - prod(1 - c_i)``): independent sources raise confidence, a single source cannot exceed its own, and a disputed
consolidation (above) stays at its strongest contribution.
Confidence of a rule conclusion without ``corroborate`` is the *minimum* over the selected slots, the same conservative
choice the research synthesizer makes: a conclusion is only as certain as its weakest required piece.
"""
from __future__ import annotations

import heapq
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from NeuralGraph.research.coordination.contracts import ClaimEnvelope, PolicyStatus
from NeuralGraph.research.coordination.core import LineageAnalyzer, RuleBasedSynthesizer, to_jsonable

from .hierarchy import LAYERS, ancestors, child_unit_of, layer_of_path, parent_path, unit_at_layer
from .integrity import DERIVED_METADATA, OPTIONAL_DERIVED_METADATA
from .models import (
    DERIVATION_VERSION, SLOT_PLACEHOLDER_RE, LineageEdge, Memory, Rule, canonical_value, content_hash, derived_memory_id,
    now_iso, rule_digest, rule_snapshot,
)
from .store import MycelicStore, Tx

logger = logging.getLogger(__name__)

MYCELIC_PRODUCER = "mycelic"
CONSOLIDATABLE = ("agent_observation", "topic_consolidation", "slot_composition")
MAX_CASCADE = 8
REAGGREGATE_PHASES = ("derived", "topics", "rules")
FRAGILITY_TOP_K = 6        # per-slot claims scored for fragility (the analyzer enumerates their product)
STATEMENT_CHARS = 220      # a quoted statement is clipped to this many characters
MAX_STATEMENTS = 12        # statements one consolidation quotes at most
PER_CHILD_STATEMENTS = 3   # statements per child above team when a unit has more than four children
MAX_DERIVED_TEXT = 2000    # characters of any derived text (consolidation or conclusion)
COALITION_SEARCH_NODES = 20_000   # selections one rule evaluation tries at most when the strongest per slot falls short
REDACTED = "[REDACTED]"   # a memory whose text is this literal never fills a slot (``RuleBasedSynthesizer``)
QUOTABLE_ABOVE_TEAM = ("org", "rule")   # statement origins a consolidation above team level may quote


@dataclass
class Derivation:
    """One derived memory produced by an apply step, with everything the caller must persist and publish."""

    memory: Memory
    edges: list[LineageEdge]
    supersedes: str | None
    parents: list[Memory] = field(default_factory=list)

    def event_payload(self) -> dict[str, Any]:
        payload = self.memory.to_dict()
        payload["parents"] = [e.to_dict() for e in self.edges]
        payload["supersedes"] = self.supersedes
        return payload


@dataclass
class Plan:
    """What one operator derives at one unit from the evidence applied so far, computed without writing anything.

    ``memory`` is None when the operator does not hold.  ``current`` is the active memory for the same
    (unit, operator, key).  ``contributions`` maps each direct child unit to what it contributes (consolidation),
    or each required slot to the candidates that fill it (rule).
    """

    memory: Memory | None
    parents: list[Memory]
    current: Memory | None
    contributions: dict[str, list[Memory]]


def consolidation_id_key(topic: str, min_support: int) -> str:
    """The key a consolidation's id is derived under: its topic, the derivation version and the configured
    ``min_support`` (which decides which coalitions exist and when a unit promotes)."""
    return f"{topic}|v{DERIVATION_VERSION}|ms{min_support}"


def conclusion_id_key(rule: Rule, entity: str | None) -> str:
    """The key a conclusion's id is derived under: rule and entity, the derivation version and the rule's digest."""
    return f"{rule.rule_id}:{entity or '*'}|v{DERIVATION_VERSION}|r{rule_digest(rule)}"


def _clip(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def noisy_or(confidences: list[float]) -> float:
    p = 1.0
    for c in confidences:
        p *= 1.0 - max(0.0, min(1.0, c))
    return round(min(0.99, 1.0 - p), 4)


def contributing_agents(m: Memory) -> list[str]:
    if m.layer == "agent":
        return [m.producer_id]
    return list(m.metadata.get("contributing_agents", []))


def contributing_teams(m: Memory) -> list[str]:
    if m.layer == "agent":
        return [unit_at_layer(m.scope, "team") or m.scope]
    return list(m.metadata.get("contributing_teams", []))


def contributing_units(m: Memory, layer: str) -> set[str]:
    """Units at ``layer`` that stand behind a memory: its own unit for a raw note, its evidence's units otherwise."""
    if m.layer == "agent":
        unit = unit_at_layer(m.scope, layer)
        return {unit} if unit else set()
    units = {unit_at_layer(t, layer) for t in contributing_teams(m)}
    own = unit_at_layer(m.scope, layer)
    if own:
        units.add(own)
    return {u for u in units if u}


def lineage_roots(m: Memory) -> list[str]:
    """The raw observations a memory ultimately rests on (itself, for a raw note)."""
    if m.layer == "agent":
        return [m.memory_id]
    return list(m.metadata.get("roots", [m.memory_id]))


def rule_chain(m: Memory) -> set[str]:
    """Rules whose conclusions a memory (transitively) rests on; empty for a raw observation."""
    if m.layer == "agent":
        return set()
    chain = set(m.metadata.get("rule_chain", []))
    if m.rule_id:
        chain.add(m.rule_id)
    return chain


def _candidate_key(m: Memory) -> tuple:
    """Everything ``Aggregator._compose_rules`` reads from a memory: two memories with the same key are offered to the
    same rule keys."""
    return m.org_id, m.operator, m.scope, m.slot, m.entity, m.topic, frozenset(rule_chain(m))


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _origin(m: Memory) -> str:
    """Where a quoted statement comes from: a rule conclusion, an org-visible note, or a team-visibility note."""
    if m.operator == "slot_composition":
        return "rule"
    return "org" if m.visibility == "org" else "team"


def _offered(m: Memory) -> list[tuple[str, str]]:
    """The (statement, origin) pairs a parent offers a consolidation, in its stored order."""
    if m.operator == "topic_consolidation":
        # a consolidation offers what it quotes, never its text; one derived before statements existed offers nothing,
        # since its head or legacy text may carry agent ids or team-visibility text
        return list(zip(m.metadata.get("statements") or [], m.metadata.get("statement_origins") or []))
    return [(_clip(m.text, STATEMENT_CHARS), _origin(m))]


def private_observations(m: Memory) -> int:
    """Team-visibility notes a memory stands for that a consolidation above team level counts but never quotes."""
    if m.operator == "topic_consolidation":
        return int(m.metadata.get("private_observations") or 0)
    return 1 if m.operator == "agent_observation" and m.visibility != "org" else 0


def _parent_order(m: Memory) -> tuple:
    """Strongest first; a raw note's ``created_at`` is its producer's ``observed_at`` (the same on a rebuild), a derived
    memory's is the apply-time clock and never orders anything; the id settles every tie."""
    return -m.confidence, m.created_at if m.layer == "agent" else "", m.memory_id


@dataclass(frozen=True)
class Statements:
    """What a consolidation says: its ``text``, the statements it quotes (unprefixed) with their ``origins``, how many
    distinct statements its parents ``offered`` at this layer (shown or not) and the team-``private`` observations
    beneath it."""

    text: str
    statements: list[str]
    origins: list[str]
    offered: int
    private: int


def consolidation_statements(unit: str, topic: str, contributions: dict[str, list[Memory]], support: int) -> Statements:
    """The text and statements of the consolidation of ``topic`` at ``unit`` (pure: a function of the mapping child →
    set of parents, never of dict or list order).

    At team level every parent is a candidate, org-visible and rule statements first (so team-private ones can never
    crowd out what may travel upward), unprefixed, at most :data:`MAX_STATEMENTS`.  Above team only org and rule
    statements are candidates: children in sorted order, each child's parents strongest first, at most
    ``max(PER_CHILD_STATEMENTS, MAX_STATEMENTS // children)`` per child, each prefixed with the child's leaf.  A
    statement that reads like an earlier one (``casefold``) is skipped; ``offered`` counts the distinct statements this
    layer may quote, and ``(+N more)`` says how many of them are not shown.  Items are dropped from the end until the
    text fits in :data:`MAX_DERIVED_TEXT` characters.
    """
    layer = layer_of_path(unit)
    leaf = unit.rsplit("/", 1)[-1]
    parents = [m for group in contributions.values() for m in group]
    private = sum(private_observations(m) for m in parents)
    seen: set[str] = set()
    shown: list[tuple[str, str, str]] = []            # (prefix, statement, origin)
    if layer == "team":
        candidates = sorted(((0 if origin in QUOTABLE_ABOVE_TEAM else 1, *_parent_order(m), i), statement, origin)
                            for m in parents for i, (statement, origin) in enumerate(_offered(m)))
        for _, statement, origin in candidates:
            if statement.casefold() not in seen:
                seen.add(statement.casefold())
                shown.append(("", statement, origin))
        head = f"{topic} — team '{leaf}': {_plural(support, 'agent')}."
    else:
        cap = max(PER_CHILD_STATEMENTS, MAX_STATEMENTS // max(1, len(contributions)))
        for child in sorted(contributions):
            prefix, n = child.rsplit("/", 1)[-1], 0
            for m in sorted(contributions[child], key=_parent_order):
                for statement, origin in _offered(m):
                    if origin not in QUOTABLE_ABOVE_TEAM or statement.casefold() in seen:
                        continue
                    seen.add(statement.casefold())
                    if n < cap:                       # beyond the cap a statement is offered but not shown
                        shown.append((prefix, statement, origin))
                        n += 1
        child_layer = LAYERS[LAYERS.index(layer) - 1]
        head = (f"{topic} — {layer} '{leaf}': {_plural(len(contributions), child_layer + ' source')}, "
                f"{_plural(support, 'agent')}"
                + (f", {_plural(private, 'team-private observation')} not quoted" if private else "") + ".")
    offered = len(seen)
    shown = shown[:MAX_STATEMENTS]

    def compose(items: list[tuple[str, str, str]]) -> str:
        more = offered - len(items)
        quoted = "; ".join(f"[{prefix}] {statement}" if prefix else statement for prefix, statement, _ in items)
        return head + (" " + quoted if items else "") + (f" (+{more} more)" if more else "")

    text = compose(shown)
    while shown and len(text) > MAX_DERIVED_TEXT:
        shown.pop()
        text = compose(shown)
    if len(text) > MAX_DERIVED_TEXT:                  # unreachable: a head is a few hundred characters at most
        text = _clip(text, MAX_DERIVED_TEXT)
    return Statements(text=text, statements=[s for _, s, _ in shown], origins=[o for _, _, o in shown], offered=offered,
                      private=private)


def render_consolidation(unit: str, topic: str, contributions: dict[str, list[Memory]], support: int) -> str:
    return consolidation_statements(unit, topic, contributions, support).text


def render_conclusion(template: str, entity: str | None, slot_texts: dict[str, str]) -> str:
    out = template.replace("{entity}", entity or "unknown entity")
    out = SLOT_PLACEHOLDER_RE.sub(lambda m: _clip(slot_texts.get(m.group(1), f"<{m.group(1)}: missing>"), 200), out)
    return out if len(out) <= MAX_DERIVED_TEXT else out[:MAX_DERIVED_TEXT - 1].rstrip() + "…"


def _claim(m: Memory, rule: Rule, now: str) -> ClaimEnvelope:
    return ClaimEnvelope(
        claim_id=m.memory_id, query_id=rule.rule_id, producer_node_id=m.producer_id,
        content={"slot": m.slot, "value": m.text}, confidence=max(0.0, min(1.0, m.confidence)),
        evidence_refs=(m.memory_id,), source_ids=tuple(m.source_event_ids), parent_memory_ids=(),
        lineage_root_ids=tuple(lineage_roots(m)) or (m.memory_id,),
        failure_domains=tuple(contributing_teams(m)) or (m.scope,),
        policy_status=PolicyStatus.ALLOWED, created_at=m.created_at or now,
        derivation_operator=m.operator,
    )


def _scored_claims(claims: tuple[ClaimEnvelope, ...], slots: tuple[str, ...]) -> tuple[ClaimEnvelope, ...]:
    """Fragility enumerates the product of per-slot candidates: score only the K strongest per slot, in the
    synthesizer's own order so the selection is always inside the scored set."""
    scored: list[ClaimEnvelope] = []
    for slot in slots:
        per = sorted((c for c in claims if c.content.get("slot") == slot),
                     key=lambda c: (-c.confidence, c.producer_node_id, c.claim_id))
        scored.extend(per[:FRAGILITY_TOP_K])
    return tuple(scored)


def _claimed_value(m: Memory) -> str | None:
    """The value a note or a consolidation claims for its slot and entity (``metadata.value`` in canonical form, so a
    value stored as an earlier release received it compares like today's), or None."""
    v = m.metadata.get("value") if m.operator in ("agent_observation", "topic_consolidation") else None
    return canonical_value(v) if m.slot is not None and m.entity is not None else None


def claim_digest(value: str) -> str:
    """How a claimed value travels above the note that claims it: a digest, so a team-visibility note's value is
    compared at every layer without being stored there."""
    return content_hash("claim", value)[:16]


def claims_of(m: Memory) -> set[tuple[str, str, str]]:
    """``(slot, entity, claim_digest(value))`` of every value a memory stands for, whatever its visibility: a note its
    own, a consolidation every value beneath it (``metadata.claims``; one derived before claims existed, the ``value``
    it published), a conclusion none."""
    if m.operator == "topic_consolidation" and isinstance(m.metadata.get("claims"), list):
        return {(c[0], c[1], c[2]) for c in m.metadata["claims"]
                if isinstance(c, list) and len(c) == 3 and all(isinstance(x, str) for x in c)}
    v = _claimed_value(m)
    return {(m.slot, m.entity, claim_digest(v))} if v is not None else set()     # type: ignore[arg-type]


def consolidation_claims(parents: list[Memory]) -> tuple[bool, str | None]:
    """``(conflict, value)`` of a consolidation of ``parents`` (pure).  ``conflict``: a derived parent is disputed already,
    or two values beneath the parents differ for the same slot and entity (:func:`claims_of`: team-visibility notes'
    values included, so a dispute is flagged at every layer above it and only that bit leaves a team; a raw note's own
    ``metadata.conflict`` is the agent's, and flags nothing).  ``value``: the one value every value-carrying parent that
    may travel upward (an org-visible note, a consolidation) claims, when there is no conflict and they all speak of one
    slot and entity."""
    claimed: dict[tuple[str, str], set[str]] = {}
    public: dict[tuple[str, str], set[str]] = {}
    conflict = False
    for m in parents:
        conflict = conflict or (m.operator != "agent_observation" and bool(m.metadata.get("conflict")))
        for slot, entity, digest in claims_of(m):
            claimed.setdefault((slot, entity), set()).add(digest)
        v = _claimed_value(m)
        if v is not None and (m.operator == "topic_consolidation" or m.visibility == "org"):
            public.setdefault((m.slot, m.entity), set()).add(v)     # type: ignore[arg-type]
    conflict = conflict or any(len(digests) > 1 for digests in claimed.values())
    if conflict or len(public) != 1:
        return conflict, None
    [values] = public.values()
    return False, next(iter(values)) if len(values) == 1 else None


def promoted_child(contributions: dict[str, list[Memory]]) -> Memory | None:
    """The consolidation a promotion is named after: the first child (in unit order) that contributes its own
    consolidation (the smallest id if it somehow had two), or None."""
    for child in sorted(contributions):
        own = sorted((m for m in contributions[child] if m.operator == "topic_consolidation" and m.scope == child),
                     key=lambda m: m.memory_id)
        if own:
            return own[0]
    return None


def build_consolidation(org_id: str, unit: str, topic: str, contributions: dict[str, list[Memory]], *, promotion: bool,
                        effective_min_support: int, registered_child_units: int, version_of: str | None,
                        min_support: int, now: str) -> Memory:
    """The consolidation of ``topic`` at ``unit`` from what each direct child contributes (no store, no clock).

    The text and statements are a function of the parent set; the order of a child's list does not matter
    (:func:`consolidation_statements`).  ``min_support`` is the configured threshold (not the effective one): it
    is part of the id, so a deployment that changes it derives new ids rather than reusing ones built under another.
    """
    layer = layer_of_path(unit)
    parents = [m for group in contributions.values() for m in group]
    parent_ids = sorted(m.memory_id for m in parents)
    agents = sorted({a for m in parents for a in contributing_agents(m)})
    teams = sorted({t for m in parents for t in contributing_teams(m)})
    strongest = [max(m.confidence for m in group) for group in contributions.values()]
    conflict, value = consolidation_claims(parents)
    claims = sorted({c for m in parents for c in claims_of(m)})
    # a dispute is not corroboration: its consolidation is as certain as its strongest contribution, no more
    confidence = round(min(0.99, max(strongest)), 4) if conflict else noisy_or(strongest)
    # a consolidation of same-slot evidence is itself evidence, unless that evidence disputes the slot's value
    slot = None if conflict else _common(parents, "slot")
    entity = _common(parents, "entity")
    st = consolidation_statements(unit, topic, contributions, len(agents))
    promoted = promoted_child(contributions) if promotion else None
    metadata: dict[str, Any] = {
        "agg_key": topic, "contributing_agents": agents, "contributing_teams": teams,
        "children": sorted(contributions), "child_layer": LAYERS[LAYERS.index(layer) - 1],
        "parent_count": len(parents), "version_of": version_of,
        "effective_min_support": effective_min_support, "registered_child_units": registered_child_units,
        "promoted_from": promoted.memory_id if promoted is not None else None,
        "roots": sorted({r for m in parents for r in lineage_roots(m)}),
        "rule_chain": sorted({r for m in parents for r in rule_chain(m)}),
        "derivation": {"v": DERIVATION_VERSION, "min_support": min_support},
        "statements": st.statements, "statement_origins": st.origins, "private_observations": st.private,
    }
    if conflict:
        metadata["conflict"] = True
    elif claims:
        metadata["claims"] = [list(c) for c in claims]
    if value is not None and slot is not None and entity is not None:
        metadata["value"] = value
    return Memory(
        memory_id=derived_memory_id(operator="topic_consolidation", scope=unit, key=consolidation_id_key(topic, min_support),
                                    parent_ids=parent_ids),
        org_id=org_id, layer=layer, scope=unit, text=render_consolidation(unit, topic, contributions, len(agents)),
        topic=topic, slot=slot, entity=entity, kind=_common(parents, "kind") or "fact", confidence=confidence,
        support=len(agents), independent_teams=len(teams), producer_id=MYCELIC_PRODUCER,
        operator="topic_consolidation", rule_id=None, event_id=None, visibility="org", created_at=now,
        applied_at=now, source_event_ids=[], metadata=metadata,
    )


def _rank(m: Memory) -> tuple:
    """``RuleBasedSynthesizer``'s order of the memories filling one slot: strongest first, then producer, then id."""
    return -max(0.0, min(1.0, m.confidence)), m.producer_id, m.memory_id


def _selection_key(selection: list[Memory]) -> tuple:
    """The order of selections (one memory per slot, in slot order): weakest memory strongest first, then the earliest
    in each slot's ranking (:func:`_rank`), slot by slot."""
    return max(_rank(m)[0] for m in selection), tuple(_rank(m) for m in selection)


def _strongest(rule: Rule, candidates: list[Memory]) -> list[Memory] | None:
    """The strongest memory of each required slot, as ``RuleBasedSynthesizer`` selects it (a memory whose text is the
    literal :data:`REDACTED` fills none), or None when a slot has none."""
    out = []
    for slot in rule.required_slots:
        filling = [m for m in candidates if m.slot == slot and m.text != REDACTED]
        if not filling:
            return None
        out.append(min(filling, key=_rank))
    return out


def _meets_thresholds(rule: Rule, evidence: list[Memory]) -> bool:
    """Does the evidence come from ``min_agents`` agents in ``min_teams`` teams, and span the units ``min_units`` demands
    in each of its slots?"""
    return (len({a for m in evidence for a in contributing_agents(m)}) >= rule.min_agents
            and len({t for m in evidence for t in contributing_teams(m)}) >= rule.min_teams
            and all(len({u for m in evidence if m.slot == slot for u in contributing_units(m, layer)}) >= n
                    for slot, per in rule.min_units.items() for layer, n in per.items()))


def _contributors(m: Memory, known: dict[tuple[str, str], tuple[frozenset, frozenset]]) -> tuple[frozenset, frozenset]:
    """The agents and teams behind ``m``; a raw note's are its author's, worked out once per author in ``known``."""
    if m.layer != "agent":
        return frozenset(contributing_agents(m)), frozenset(contributing_teams(m))
    key = (m.producer_id, m.scope)
    if key not in known:
        known[key] = frozenset(contributing_agents(m)), frozenset(contributing_teams(m))
    return known[key]


class _SearchExhausted(Exception):
    pass


_exhausted_warned: set[str] = set()


def _coalition(rule: Rule, candidates: list[Memory]) -> list[Memory] | None:
    """The selection of a rule without ``corroborate`` when the strongest one does not meet its thresholds (one agent is
    the strongest in several slots): of the selections about one entity at most that do, the one whose weakest memory is
    strongest, then the earliest in each slot's ranking (:func:`_rank`), slot by slot; None when no selection does.  The
    candidates of a rule evaluated for an entity are all about it; a '*' evaluation searches all its pools at once.

    Each slot's memories are ranked once, those that name no entity and, per entity, those about it, and only the
    strongest memory per contributor set (agents and teams) is kept: a weaker one with the same contributors is never
    needed, nor is one about an entity behind one that names none.  A depth-first search in ranking order takes in each
    slot only memories about the entity an earlier slot took (or about none), is pruned by what the remaining slots could
    still add (their contributors together, and the most one memory per slot adds), and finds the earliest selection
    with every memory at or above a floor; a lower floor only adds selections, so the strongest floor (a candidate's
    confidence) with one is found by bisection, starting from the weakest.  The searches try at most
    :data:`COALITION_SEARCH_NODES` selections in all, whatever the number of entities; when that stops them, the strongest
    selection found so far stands, and the rule does not hold only when none was found (logged once per rule).  The
    searches depend on the candidates alone, so the outcome is the same on every node; :func:`build_conclusion` makes the
    evidence reproduce it.
    """
    n = len(rule.required_slots)
    free: list[list[tuple]] = []                 # per slot: (rank, agents, teams, memory) of the memories naming no entity
    about: list[dict[str, list[tuple]]] = []     # per slot and entity: those of the memories about it
    every: list[list[tuple]] = []                # per slot: all of them, in ranking order
    known: dict[tuple[str, str], tuple[frozenset, frozenset]] = {}
    for slot in rule.required_slots:
        nulls: list[tuple] = []
        named: dict[str, list[tuple]] = {}
        kept: list[tuple] = []
        seen: dict[str | None, set[tuple[frozenset, frozenset]]] = {None: set()}
        need = rule.min_units.get(slot)
        for rank, m in sorted(((_rank(m), m) for m in candidates if m.slot == slot and m.text != REDACTED),
                              key=lambda r: r[0]):
            if need and any(len(contributing_units(m, layer)) < k for layer, k in need.items()):
                continue
            c = _contributors(m, known)
            if c in seen[None] or c in seen.setdefault(m.entity, set()):
                continue
            seen[m.entity].add(c)
            kept.append((rank, *c, m))
            (nulls if m.entity is None else named.setdefault(m.entity, [])).append(kept[-1])
        if not kept:
            return None
        free.append(nulls)
        about.append(named)
        every.append(kept)
    budget = [COALITION_SEARCH_NODES]

    def attempt(floor: float) -> list[Memory] | None:
        """The earliest selection, in ranking order slot by slot, whose memories are all at or above ``floor``."""
        # what slots i.. can still add: all their contributors together, and at most the largest set of each slot
        rest_agents: list[frozenset] = [frozenset()] * (n + 1)
        rest_teams: list[frozenset] = [frozenset()] * (n + 1)
        gain_agents, gain_teams = [0] * (n + 1), [0] * (n + 1)
        for i in reversed(range(n)):
            above = list(itertools.takewhile(lambda e: -e[0][0] >= floor, every[i]))
            if not above:
                return None
            rest_agents[i] = rest_agents[i + 1].union(*(a for _, a, _, _ in above))
            rest_teams[i] = rest_teams[i + 1].union(*(t for _, _, t, _ in above))
            gain_agents[i] = gain_agents[i + 1] + max(len(a) for _, a, _, _ in above)
            gain_teams[i] = gain_teams[i + 1] + max(len(t) for _, _, t, _ in above)

        def short(agents: frozenset, teams: frozenset, i: int) -> bool:
            """Can slots i.. no longer bring ``agents`` and ``teams`` to the thresholds?  (A union's size is counted
            from the few chosen so far, never by building it.)"""
            return (min(len(rest_agents[i]) + len(agents - rest_agents[i]), len(agents) + gain_agents[i]) < rule.min_agents
                    or min(len(rest_teams[i]) + len(teams - rest_teams[i]), len(teams) + gain_teams[i]) < rule.min_teams)

        if short(frozenset(), frozenset(), 0):
            return None

        def search(i: int, entity: str | None, agents: frozenset, teams: frozenset) -> list[Memory] | None:
            if i == n:
                return []
            # once a slot took a memory about an entity, the others take only memories about it or about none
            entries = (every[i] if entity is None
                       else heapq.merge(free[i], about[i].get(entity, ()), key=lambda e: e[0]))
            tried: set[tuple[frozenset, frozenset]] = set()
            for rank, a, t, m in entries:
                if -rank[0] < floor:
                    break
                budget[0] -= 1
                if budget[0] < 0:
                    raise _SearchExhausted
                if entity is not None:
                    if (a, t) in tried:
                        continue            # a memory naming no entity behind a stronger one about the entity
                    tried.add((a, t))
                if short(agents | a, teams | t, i + 1):
                    continue
                rest = search(i + 1, m.entity if entity is None else entity, agents | a, teams | t)
                if rest is not None:
                    return [m, *rest]
            return None

        return search(0, None, frozenset(), frozenset())

    # a lower floor only adds selections, so the strongest floor with one is found by bisection
    floors = sorted({-e[0][0] for entries in every for e in entries}, reverse=True)
    best: list[Memory] | None = None
    try:
        best = attempt(floors[-1])
        lo, hi = 0, len(floors) - 1                 # best is attempt(floors[hi]) throughout
        while best is not None and lo < hi:
            mid = (lo + hi) // 2
            at = attempt(floors[mid])
            if at is not None:
                best, hi = at, mid
            else:
                lo = mid + 1
    except _SearchExhausted:
        if rule.rule_id not in _exhausted_warned:            # once per rule and process: it recurs at every evaluation
            _exhausted_warned.add(rule.rule_id)
            logger.warning("rule %s: the coalition search stopped after %d selections over %d candidates; %s",
                           rule.rule_id, COALITION_SEARCH_NODES, len(candidates),
                           "it does not hold" if best is None else "the strongest selection found so far stands")
    return best


def _select(rule: Rule, pool: list[Memory]) -> list[Memory] | None:
    """The selection a rule rests on within a pool of candidates: the strongest memory of each slot, or, without
    ``corroborate``, the best selection that meets the thresholds when that one does not (:func:`_coalition`)."""
    strongest = _strongest(rule, pool)
    if strongest is None or rule.corroborate or _meets_thresholds(rule, strongest):
        return strongest
    return _coalition(rule, pool)


def wildcard_selection(selected: list[Memory]) -> bool:
    """Can a '*' conclusion rest on this selection?  Only on evidence that names no entity next to evidence about one
    entity at most: never on one entity's conclusion (that is the entity's own key), never on evidence about two entities
    stitched together."""
    return any(m.entity is None for m in selected) and len({m.entity for m in selected if m.entity is not None}) <= 1


def _wildcard_scopes(rule: Rule, candidates: list[Memory]) -> tuple[list[Memory], list[Memory]] | None:
    """The selection of a '*' conclusion and the pool it is chosen in, of the pools "evidence that names no entity, and
    evidence about one entity E", one per E (only the former when no candidate names an entity).  Without ``corroborate``
    it is the best selection about one entity at most, as :func:`_select` would choose it in each pool: the strongest
    selection of the pool whose strongest is best when that one meets the thresholds, otherwise one search of every pool
    at once (:func:`_coalition`); its pool is that of the entity it names.  With ``corroborate`` a pool is the evidence,
    whichever memories its strongest selection names, and must meet the thresholds; the pool whose strongest selection is
    best counts (weakest memory strongest, then earliest in each slot's ranking), then the first E.  The memories that
    name no entity are read once, not once per pool, so a pool costs only the evidence about its entity."""
    nulls = [m for m in candidates if m.entity is None]
    by_entity: dict[str, list[Memory]] = {}
    for m in candidates:
        if m.entity is not None:
            by_entity.setdefault(m.entity, []).append(m)

    def top(mems: list[Memory]) -> dict[str, tuple[tuple, Memory]]:
        """The strongest memory of each required slot among ``mems`` (:func:`_strongest`), with its rank."""
        out: dict[str, tuple[tuple, Memory]] = {}
        for m in mems:
            if m.slot in rule.required_slots and m.text != REDACTED:
                rank = _rank(m)
                if m.slot not in out or rank < out[m.slot][0]:
                    out[m.slot] = rank, m       # type: ignore[index]
        return out

    top_null = top(nulls)
    strongest: dict[str | None, tuple[tuple, list[Memory]]] = {}     # each pool's strongest selection and its key
    for e in sorted(by_entity) or [None]:
        tops = (top_null, top(by_entity[e])) if e is not None else (top_null,)
        picks = [min((t[s] for t in tops if s in t), key=lambda r: r[0], default=None) for s in rule.required_slots]
        if all(p is not None for p in picks):
            ranks = tuple(r for r, _ in picks)      # type: ignore[misc]
            strongest[e] = (max(r[0] for r in ranks), ranks), [m for _, m in picks]     # :func:`_selection_key`
    if not strongest:
        return None
    if not rule.corroborate:
        _, picks = min(((key, e or ""), picks) for e, (key, picks) in strongest.items())
        selection = picks if _meets_thresholds(rule, picks) else _coalition(rule, candidates)
        if selection is None:
            return None
        named = {m.entity for m in selection if m.entity is not None}
        return selection, [m for m in candidates if m.entity is None or m.entity in named]
    known: dict[tuple[str, str], tuple[frozenset, frozenset]] = {}

    def tally(mems: list[Memory]) -> tuple[set[str], set[str], dict[tuple[str, str], set[str]]]:
        """The agents, teams and (per ``min_units`` slot and layer) units behind ``mems``."""
        agents: set[str] = set()
        teams: set[str] = set()
        units: dict[tuple[str, str], set[str]] = {}
        for m in mems:
            a, t = _contributors(m, known)
            agents.update(a)
            teams.update(t)
            for layer in rule.min_units.get(m.slot or "", {}):
                units.setdefault((m.slot, layer), set()).update(contributing_units(m, layer))   # type: ignore[arg-type]
        return agents, teams, units

    def joint(common: set[str], extra: set[str]) -> int:
        """``len(common | extra)``, counted without copying ``common``."""
        return len(common) + len(extra - common)

    shared, nothing = tally(nulls), (set(), set(), {})
    best: tuple | None = None
    for e, (key, picks) in strongest.items():
        own = tally(by_entity[e]) if e is not None else nothing
        if (joint(shared[0], own[0]) >= rule.min_agents and joint(shared[1], own[1]) >= rule.min_teams
                and all(joint(shared[2].get((slot, layer), set()), own[2].get((slot, layer), set())) >= k
                        for slot, per in rule.min_units.items() for layer, k in per.items())):
            if best is None or (key, e or "") < best[0]:
                best = ((key, e or ""), picks, e)
    if best is None:
        return None
    _, picks, e = best
    return picks, (nulls + by_entity[e] if e is not None else nulls)


def build_conclusion(rule: Rule, org_id: str, unit: str, entity: str | None, candidates: list[Memory], *,
                     version_of: str | None, now: str) -> tuple[Memory, list[Memory]] | None:
    """The conclusion of ``rule`` at ``unit`` for ``entity`` and the evidence it rests on, or None when the rule does
    not hold on these candidates (no store, no clock).  The fragility metrics are left to the caller.

    The selection is one memory per required slot (:func:`_select`): the strongest of each slot, in
    ``RuleBasedSynthesizer``'s order, unless the rule does not corroborate and that one misses its thresholds; then the
    best selection that meets them.  A '*' conclusion (``entity`` None, candidates about any entity) selects within one
    entity's evidence and the evidence that names none (:func:`_wildcard_scopes`) and holds only when its selection
    includes a memory that names no entity (:func:`wildcard_selection`).  Evidence is the selection's pool (the candidates,
    or for '*' the pool it was chosen in) with ``corroborate``; without it, the selection and, for each slot whose pool
    (for '*', the evidence about the selection's entity or none) claims different values for one entity, the strongest
    memory claiming each of them, so a dispute is in the lineage.  A disputed slot, or a disputed memory among the
    evidence, flags the conclusion (``metadata.conflict``).
    """
    key = f"{rule.rule_id}:{entity or '*'}"
    slots = tuple(rule.required_slots)
    found = (_select(rule, candidates), candidates) if entity is not None else _wildcard_scopes(rule, candidates)
    if found is None or found[0] is None:
        return None
    selected, scope = found
    while True:
        if entity is None and not wildcard_selection(selected):
            return None
        named = {m.entity for m in selected if m.entity is not None}
        pool = (scope if entity is not None or rule.corroborate
                else [m for m in candidates if m.entity is None or m.entity in named])
        # the values claimed in each slot, the strongest memory claiming each one first: a slot is disputed when it holds
        # two values for one entity
        values: dict[str, dict[tuple[str, str], Memory]] = {}
        for m in sorted(pool, key=_rank):
            for slot, e, digest in claims_of(m):
                if slot == m.slot and slot in slots:
                    values.setdefault(slot, {}).setdefault((e, digest), m)
        disputed = {(slot, e) for slot, held in values.items() for e, _ in held if sum(e2 == e for e2, _ in held) > 1}
        if rule.corroborate:
            # with corroboration every memory that fills a required slot is evidence, not only the strongest per slot
            evidence = sorted(pool, key=lambda m: m.memory_id)
            break
        chosen = {m.memory_id for m in selected}
        sides = {m.memory_id: m for slot, e in disputed for (e2, _), m in values[slot].items() if e2 == e}
        evidence = [*selected, *sorted((m for mid, m in sides.items() if mid not in chosen), key=lambda m: m.memory_id)]
        # the evidence alone must reproduce the selection (verification re-derives it so): it does when the selection is
        # the best one, but one the search settled for at its bound (:func:`_coalition`) gives way to a better one among
        # the evidence until they agree, and without agreement the rule does not hold
        best = _select(rule, evidence) if len(evidence) > len(selected) else selected
        if best is not None and _selection_key(best) < _selection_key(selected):
            selected = best
            continue
        if best is None or [m.memory_id for m in best] != [m.memory_id for m in selected]:
            return None
        break
    if not _meets_thresholds(rule, evidence):
        return None
    agents = sorted({a for m in evidence for a in contributing_agents(m)})
    teams = sorted({t for m in evidence for t in contributing_teams(m)})
    # corroboration is per slot: "supply_risk reported by two regions" counts the units behind that slot only
    units = {slot: {layer: sorted({u for m in evidence if m.slot == slot for u in contributing_units(m, layer)})
                    for layer in per} for slot, per in rule.min_units.items()}
    parent_ids = sorted(m.memory_id for m in evidence)
    slot_texts = {m.slot: m.text for m in selected if m.slot}
    if rule.corroborate:
        # confidence per slot rises with independent corroboration (noisy-OR over the units filling it), unless the
        # memories filling it claim different values (a dispute is not corroboration: the strongest one counts);
        # the conclusion is as certain as its weakest slot
        per_slot = []
        for slot in slots:
            best_by_unit: dict[str, float] = {}
            for m in evidence:
                if m.slot == slot:
                    u = m.scope if m.layer != "agent" else (unit_at_layer(m.scope, "team") or m.scope)
                    best_by_unit[u] = max(best_by_unit.get(u, 0.0), m.confidence)
            if any(s == slot for s, _ in disputed):
                per_slot.append(round(min(0.99, max(best_by_unit.values())), 4))
            else:
                per_slot.append(noisy_or(list(best_by_unit.values())))
        confidence = round(min(per_slot), 4) if per_slot else 0.0
    else:
        # the selection's weakest slot (a dispute changes nothing here: one memory per slot counts already)
        confidence = round(round(min(max(0.0, min(1.0, m.confidence)) for m in selected), 6), 4)
    memory = Memory(
        memory_id=derived_memory_id(operator="slot_composition", scope=unit, key=conclusion_id_key(rule, entity),
                                    parent_ids=parent_ids),
        org_id=org_id, layer=rule.target_layer, scope=unit,
        text=render_conclusion(rule.conclusion, entity, slot_texts),
        topic=rule.conclusion_topic(), slot=rule.emits_slot, entity=entity,
        kind=rule.kind, confidence=confidence, support=len(agents), independent_teams=len(teams),
        producer_id=MYCELIC_PRODUCER, operator="slot_composition", rule_id=rule.rule_id, event_id=None,
        visibility="org", created_at=now, applied_at=now, source_event_ids=[],
        metadata={
            "agg_key": key, "contributing_agents": agents, "contributing_teams": teams,
            "slots": {m.slot: m.memory_id for m in selected if m.slot}, "candidates": len(candidates),
            "fragility_scored_candidates": sum(min(FRAGILITY_TOP_K, sum(m.slot == slot for m in candidates)) for slot in slots),
            "evidence": {m.memory_id: {"slot": m.slot, "layer": m.layer, "scope": m.scope, "operator": m.operator}
                         for m in evidence},
            "corroborated_units": units, "roots": sorted({r for m in evidence for r in lineage_roots(m)}),
            "rule_chain": sorted({rule.rule_id} | {r for m in evidence for r in rule_chain(m)}),
            "version_of": version_of,
            "derivation": {"v": DERIVATION_VERSION, "rule_digest": rule_digest(rule), "rule": rule_snapshot(rule)},
        },
    )
    if disputed or any(m.operator != "agent_observation" and m.metadata.get("conflict") for m in evidence):
        memory.metadata["conflict"] = True
    return memory, evidence


class Aggregator:
    def __init__(self, store: MycelicStore, *, min_support: int = 2, clock: Callable[[], str] = now_iso,
                 max_candidates: int = 5000, max_dependents: int = 100_000,
                 on_event: Callable[[str, dict[str, str]], None] | None = None) -> None:
        self.store = store
        self.min_support = max(1, int(min_support))
        self.clock = clock
        self.max_candidates = max_candidates
        self.max_dependents = max_dependents
        self.on_event = on_event          # ('inconsistency', {kind}) / ('truncated', {what}): the service counts them
        self._cap_warned: set[tuple[str, str]] = set()

    def planner(self) -> "Aggregator":
        """A twin for read-only planning (downward verification): the same min_support, clock, max_candidates and
        max_dependents, no on_event (nothing is counted), and the once-per-key cap warnings shared with this one."""
        twin = Aggregator(self.store, min_support=self.min_support, clock=self.clock, max_candidates=self.max_candidates,
                          max_dependents=self.max_dependents)
        twin._cap_warned = self._cap_warned
        return twin

    def _emit(self, name: str, labels: dict[str, str]) -> None:
        if self.on_event is not None:
            self.on_event(name, labels)

    def _latest(self, org_id: str, unit: str, key: str, **filters: Any) -> list[Memory]:
        """The newest ``max_candidates`` active applied memories under ``unit`` matching ``filters``, in apply order.
        A full result means older evidence was left out: counted every time, logged once per (unit, key)."""
        rows = self.store.list_memories(org_id, scope=unit, status="active", latest=True, limit=self.max_candidates, **filters)
        if len(rows) >= self.max_candidates:
            self._emit("truncated", {"what": "candidates"})
            if (unit, key) not in self._cap_warned:
                self._cap_warned.add((unit, key))
                logger.warning("candidates for %s at %s reached the cap of %d: only the newest are aggregated",
                               key, unit, self.max_candidates)
        return rows

    # ------------------------------------------------------------------ entry point
    def derive_for(self, tx: Tx, memory: Memory) -> list[Derivation]:
        """Run every operator that the given (already inserted) memory could have changed.

        Called inside the apply transaction, so reads see the just-inserted rows.  Returns the derivations in the
        order they were persisted (each later consolidation may already include an earlier one as a parent).
        """
        return self._derive(tx, memory, depth=0)

    def _derive(self, tx: Tx, memory: Memory, *, depth: int) -> list[Derivation]:
        out: list[Derivation] = []
        # rules first: a conclusion on the memory's own topic is then already in place when the units above consolidate
        # that topic, so each of them derives one version for this memory, not one without the conclusion and one with it
        if memory.slot:
            out.extend(self._compose_rules(tx, memory))
        if memory.topic and memory.operator in CONSOLIDATABLE:
            out.extend(self._consolidate_topic(tx, memory.org_id, memory.scope, memory.topic))
        if depth < MAX_CASCADE:
            # a derived memory is evidence for whatever sits above it: rules that take conclusions, consolidations
            # of conclusions across sibling units, and so on up to the enterprise
            for d in list(out):
                out.extend(self._derive(tx, d.memory, depth=depth + 1))
                if d.supersedes:
                    out.extend(self._superseded(tx, d.supersedes, d.memory, depth=depth + 1))
        elif out:
            logger.error("cascade truncated at depth %d after %s; not re-offered: %s (rules may form a cycle)",
                         depth, memory.memory_id, [d.memory.memory_id for d in out])
            self._emit("truncated", {"what": "cascade"})
        return out

    def retire_dependents(self, tx: Tx, memory_id: str, reason: str) -> list[str]:
        """Retract every active memory derived (transitively) from ``memory_id``; the caller re-derives afterwards.

        Only active memories are walked: an active memory never rests on an inactive one, so the walk reaches every
        active dependent, and its cost follows what is current rather than every version the history kept.
        """
        ids = self.store.dependents_of(memory_id, active_only=True, max_nodes=self.max_dependents)
        if len(ids) >= self.max_dependents:
            logger.error("dependents of %s cut at %d; the rest stay active until their own evidence changes",
                         memory_id, self.max_dependents)
            self._emit("truncated", {"what": "dependents"})
        for dep in ids:
            tx.set_memory_status(dep, "retracted", reason=reason)
        return ids

    def retract_notes(self, tx: Tx, notes: list[Memory], reason: str) -> list[Derivation]:
        """Retract active raw notes together (an agent's removal), so each key gets one new version instead of one per note.

        The path of a single retraction (set the status, retire the dependents, offer the note upward, re-evaluate what
        was retired), batched: every note is retracted and its dependents retired first, then each distinct (unit, topic)
        is consolidated and each distinct candidate key composed once, in sorted order, and finally the retired memories
        are re-evaluated on what remains.
        """
        retired: list[str] = []
        for m in notes:
            tx.set_memory_status(m.memory_id, "retracted", reason=reason)
            retired += self.retire_dependents(tx, m.memory_id, "evidence retracted")
        out: list[Derivation] = []
        for org_id, scope, topic in sorted({(m.org_id, m.scope, m.topic) for m in notes if m.topic}):
            out.extend(self._consolidate_topic(tx, org_id, scope, topic))
        reps: dict[tuple, Memory] = {}
        for m in notes:
            if m.slot:
                reps.setdefault(_candidate_key(m), m)
        for key in sorted(reps, key=lambda k: (k[3], k[4] is not None, k[4] or "", k[5] or "")):
            out.extend(self._compose_rules(tx, reps[key]))
        out = self._cascade(tx, out)
        return out + self.reevaluate(tx, retired)

    def _withdraw(self, tx: Tx, current: Memory, reason: str = "support below threshold") -> list[Derivation]:
        """The memory no longer holds (support fell below the threshold because a child unit appeared or evidence went
        away, a stronger note about one entity made a '*' conclusion's selection that entity's; or its rule was deleted,
        disabled or moved): retract it, retire everything built on it and re-evaluate those on what remains."""
        tx.set_memory_status(current.memory_id, "retracted", reason=reason)
        retired = self.retire_dependents(tx, current.memory_id, "evidence withdrawn")
        # as for a retracted note: evidence that went away can change which memory is strongest for a rule's slot, so a
        # '*' conclusion it blocked (its selection named one entity only) may hold now; it did not rest on it, so it is
        # not among the retired
        out = self._compose_rules(tx, current) if current.slot else []
        return out + (self.reevaluate(tx, retired) if retired else [])

    def reevaluate(self, tx: Tx, retired_ids: list[str], *, depth: int = 1) -> list[Derivation]:
        """Re-run the operator of each retired derived memory on the evidence that is still active.

        A conclusion that lost one piece of evidence may still hold on the rest (three regions minus one is still
        two); it then comes back as a new version, or as the reactivated earlier version whose coalition returned.
        """
        out: list[Derivation] = []
        for mid in retired_ids:
            m = self.store.get_memory(mid)
            if m is None:
                continue
            if m.operator == "topic_consolidation" and m.topic:
                # consolidations are recomputed for the unit itself and everything above it
                child_scope = m.scope + "/x"          # any path directly below the unit climbs through it
                out.extend(self._consolidate_topic(tx, m.org_id, child_scope, m.topic))
            elif m.operator == "slot_composition":
                rule = self.rule_for(m)          # never rebuilt at a unit whose layer its rule no longer targets
                if rule is not None:
                    out.extend(self._compose_rule(tx, rule, m.org_id, m.scope, m.entity))
            if m.slot:
                # if it does not come back, the strongest evidence for a rule's '*' conclusion, which did not rest on
                # it, may now name no entity
                out.extend(self._compose_rules(tx, m))
        for d in list(out):
            out.extend(self._derive(tx, d.memory, depth=depth))
        return out

    def _superseded(self, tx: Tx, memory_id: str, successor: Memory, *, depth: int) -> list[Derivation]:
        """Deal with what a superseded version leaves behind once its successor has been offered upward.

        Whatever still rests on it and was not itself replaced by the cascade (a conclusion keyed by an entity the new
        version no longer carries) is stale: it is retired and re-evaluated on active evidence.  And the old version
        stops being a candidate for the rules its slot fed, under its slot, entity and topic, which the new version may
        not carry (a rule changed what it emits, a note with another slot joined a consolidation): a '*' conclusion it
        blocked (as the strongest for its slot it made the selection one entity's) may hold now, and did not rest on it,
        so it is not among the retired.  When the successor is the same candidate (the common case: a note joins a
        consolidation, a conclusion gains evidence), offering it upward has just composed exactly those rule keys, and
        whatever the retirement above changed is re-offered by ``reevaluate``, so they are not composed a second time.
        """
        stale = self.retire_dependents(tx, memory_id, "evidence superseded")
        out = self.reevaluate(tx, stale, depth=depth)
        old = self.store.get_memory(memory_id)
        if old is not None and old.slot and _candidate_key(old) != _candidate_key(successor):
            composed = self._compose_rules(tx, old)
            for d in list(composed):
                composed.extend(self._derive(tx, d.memory, depth=depth))
            out.extend(composed)
        return out

    # ------------------------------------------------------------------ reconciliation
    # A derived memory exists exactly when its operator holds on the applied evidence, rules and registry.  New
    # evidence is offered upward by ``derive_for``; everything else that decides whether a memory holds is reconciled
    # here: a rule event (``evaluate_rule``, ``withdraw_rule``), a registry event (``registry_changed``) and the
    # re-aggregation job (``reaggregate_step``).
    def _cascade(self, tx: Tx, out: list[Derivation]) -> list[Derivation]:
        """Offer each derivation in ``out`` to everything above it and deal with what the version it superseded leaves
        behind (``_superseded``): what ``_derive`` does for the derivations it makes, for ones made outside it."""
        for d in list(out):
            out.extend(self._derive(tx, d.memory, depth=1))
            if d.supersedes:
                out.extend(self._superseded(tx, d.supersedes, d.memory, depth=1))
        return out

    def rule_for(self, m: Memory) -> Rule | None:
        """The applied rule an existing conclusion is evaluated under now, or None: its rule was deleted or disabled,
        or no longer covers its organization or targets its layer."""
        if m.operator != "slot_composition" or not m.rule_id:
            return None
        rule = self.store.get_applied_rule(m.rule_id)
        if rule is None or not rule.enabled or rule.org_id not in (None, m.org_id) or rule.target_layer != m.layer:
            return None
        return rule

    def plan_for(self, m: Memory) -> Plan | None:
        """What the operator of an existing derived memory derives at its unit now (read-only); None when no operator
        applies to it any more (see :meth:`rule_for`)."""
        if m.operator == "topic_consolidation" and m.topic:
            return self.plan_consolidation(m.org_id, m.scope, m.topic)
        rule = self.rule_for(m)
        return self.plan_rule(rule, m.org_id, m.scope, m.entity) if rule is not None else None

    def _reconcile_consolidation(self, tx: Tx, org_id: str, unit: str, topic: str) -> list[Derivation]:
        """Make the consolidation of ``topic`` at ``unit`` what its plan says: withdraw it, keep it, or persist a new version."""
        plan = self.plan_consolidation(org_id, unit, topic)
        if plan.memory is None:
            if plan.current is not None:  # support fell below the threshold (retraction): the memory no longer holds
                return self._withdraw(tx, plan.current)
            return []
        if plan.current is not None and plan.current.memory_id == plan.memory.memory_id:
            return []
        d = self._persist(tx, plan.memory, plan.parents, plan.current)
        return [d] if d is not None else []

    def reconcile(self, tx: Tx, m: Memory) -> list[Derivation]:
        """Re-run the operator of a derived memory that is still active and offer what changed to everything above it.

        A conclusion whose rule no longer applies is withdrawn with the reason (``rule deleted``, ``rule disabled``,
        ``rule no longer applies here``); one that its rule derives differently now gets a new version.
        """
        m = self.store.get_memory(m.memory_id)
        if m is None or m.status != "active":
            return []
        out: list[Derivation] = []
        if m.operator == "topic_consolidation" and m.topic:
            out = self._reconcile_consolidation(tx, m.org_id, m.scope, m.topic)
        elif m.operator == "slot_composition":
            rule = self.rule_for(m)
            if rule is not None:
                out = self._compose_rule(tx, rule, m.org_id, m.scope, m.entity)
            else:
                applied = self.store.get_applied_rule(m.rule_id) if m.rule_id else None
                reason = ("rule deleted" if applied is None else "rule disabled" if not applied.enabled
                          else "rule no longer applies here")
                out = self._withdraw(tx, m, reason=reason)
        key = m.topic if m.operator == "topic_consolidation" else f"{m.rule_id}:{m.entity or '*'}"
        current = self.store.current_derived(m.org_id, m.operator, m.scope, key) if key else None
        if (current is None or current.memory_id != m.memory_id) and self.store.get_memory(m.memory_id).status == "active":
            # a row outside the one-active-per-key index (written without its key): what the key derives replaces it
            out.extend(self._withdraw(tx, m, reason="not the current version of its key"))
        return self._cascade(tx, out)

    def rule_keys(self, rule: Rule, *, org_id: str | None = None) -> list[tuple[str, str, str | None]]:
        """Every (organization, unit, entity) where ``rule`` has evidence to evaluate: the unit at its target layer above
        each applied active memory that could fill one of its slots.  Evidence above the target layer gives no key, and
        a '*' key comes only from evidence that names no entity (a '*' conclusion needs one among its selection)."""
        if org_id is not None:
            orgs = [org_id] if rule.org_id in (None, org_id) else []
        else:
            orgs = [rule.org_id] if rule.org_id is not None else self.store.memory_org_ids()
        keys: set[tuple[str, str, str | None]] = set()
        for org in orgs:
            for scope, entity in self.store.distinct_scope_entity(org, operators=rule.sources, slots=rule.required_slots,
                                                                  topic_prefix=rule.topic_prefix):
                unit = unit_at_layer(scope, rule.target_layer)
                if unit is not None:
                    keys.add((org, unit, entity))
        return sorted(keys, key=lambda k: (k[0], k[1], k[2] is not None, k[2] or ""))

    def evaluate_rule(self, tx: Tx, rule: Rule, *, org_id: str | None = None) -> list[Derivation]:
        """Apply an upserted rule (already in ``applied_rules``) to everything applied so far.

        Every active conclusion of the rule is reconciled first: withdrawn when the rule is disabled or no longer covers
        its organization or layer, superseded when the rule derives differently.  Then, if the rule is enabled, it is
        composed wherever evidence could satisfy it, so a rule added after its evidence concludes at once.  An identical
        upsert reproduces every id and changes nothing.
        """
        out: list[Derivation] = []
        conclusions = self.store.active_conclusions(rule.rule_id, org_id=org_id)
        reconciled: set[tuple[str, str, str | None]] = set()
        for m in conclusions:
            if self.rule_for(m) is not None and self.store.get_memory(m.memory_id).status == "active":
                reconciled.add((m.org_id, m.scope, m.entity))     # reconcile composes the rule at this key now
            out.extend(self.reconcile(tx, m))
        # a key just reconciled is not composed again: whatever changed its evidence since was offered to the rule
        keys = [k for k in self.rule_keys(rule, org_id=org_id) if k not in reconciled] if rule.enabled else []
        composed: list[Derivation] = []
        for org, unit, entity in keys:
            composed.extend(self._compose_rule(tx, rule, org, unit, entity))
        out.extend(self._cascade(tx, composed))
        logger.info("rule %s evaluated (%s): %d conclusions reconciled, %d other keys composed, %d derivations", rule.rule_id,
                    "enabled" if rule.enabled else "disabled", len(conclusions), len(keys), len(out))
        return out

    def withdraw_rule(self, tx: Tx, rule_id: str) -> list[Derivation]:
        """Apply a rule deletion (already gone from ``applied_rules``): every active conclusion of the rule is withdrawn
        ('rule deleted') and whatever rested on it is retired and re-evaluated on what remains."""
        out: list[Derivation] = []
        conclusions = self.store.active_conclusions(rule_id)
        for m in conclusions:
            out.extend(self.reconcile(tx, m))
        logger.info("rule %s deleted: %d conclusions withdrawn, %d derivations", rule_id, len(conclusions), len(out))
        return out

    def registry_counts(self, org_id: str, agent_path: str) -> dict[str, int]:
        """How many registered child units each unit above an agent has, as the log has applied the registry.  Teams
        are left out: a team's children are agents, so a team never promotes."""
        return {u: len(self.store.child_units(org_id, u)) for u in ancestors(agent_path, include_self=False)
                if layer_of_path(u) != "team"}

    def registry_changed(self, tx: Tx, org_id: str, agent_path: str, before: dict[str, int]) -> list[Derivation]:
        """Apply a registration or revocation whose ``registry_counts`` before it were ``before``.

        Only promotion depends on how many child units a unit has: where a unit above the agent gained or lost one,
        the topics it promotes or could promote (those of its direct children's consolidations) are re-planned from the
        agent's team up to the enterprise.  A second child ends a promotion, losing it allows one again.
        """
        after = self.registry_counts(org_id, agent_path)
        changed = [u for u, n in after.items() if before.get(u) != n]
        if not changed:
            return []
        topics = sorted({t for u in changed for t in self.store.promotion_topics(org_id, u)})
        out: list[Derivation] = []
        for topic in topics:
            out.extend(self._consolidate_topic(tx, org_id, agent_path, topic))
        out = self._cascade(tx, out)
        logger.info("registry changed under %s: child units changed at %s, %d topics re-planned, %d derivations",
                    agent_path, changed, len(topics), len(out))
        return out

    # ------------------------------------------------------------------ re-aggregation (local maintenance)
    def _topic_keys(self, org_id: str) -> list[tuple[int, str, str]]:
        """(layer index, unit, topic) of every unit above applied active evidence on a topic, lower layers first.  The
        scopes are stored paths, so the units above one are its prefixes (no validation: this runs at every step)."""
        keys: set[tuple[int, str, str]] = set()
        for scope, topic in self.store.distinct_scope_topic(org_id, operators=CONSOLIDATABLE):
            parts = scope.split("/")
            for n in range(1, len(parts)):            # a unit of n segments is at layer index len(LAYERS) - n
                keys.add((len(LAYERS) - n, "/".join(parts[:n]), topic))
        return sorted(keys)

    def _rule_keys_of(self, org_id: str) -> tuple[dict[str, Rule], list[tuple[str, str, bool, str]]]:
        """The enabled rules of an organization and (rule_id, unit, has entity, entity or '') of every key they have
        evidence for, sorted."""
        rules = {r.rule_id: r for r in self.store.list_applied_rules(org_id)}
        keys = sorted((rule_id, unit, entity is not None, entity or "")
                      for rule_id, rule in rules.items() for _, unit, entity in self.rule_keys(rule, org_id=org_id))
        return rules, keys

    def reaggregate_step(self, tx: Tx, org_id: str, cursor: dict[str, Any] | None, *,
                         limit: int = 50) -> tuple[list[Derivation], int, dict[str, Any] | None]:
        """One bounded step of re-aggregating ``org_id`` on what is applied: ``(derivations, memories changed, next
        cursor)``, the next cursor None once the last phase is done.

        Three phases, each enumerated afresh at every step and resumed after the last key it processed (never by
        position), so applies between steps are safe:

        * ``derived``: every active consolidation and conclusion, lower layers first, is reconciled; an id built under
          another derivation version, ``min_support`` or rule digest becomes a new version (the old one superseded,
          ``version_of`` kept), and one whose rule was deleted, disabled or moved, or whose topic no evidence carries
          any more (a legacy spelling), is withdrawn;
        * ``topics``: every (unit, topic) above applied evidence is consolidated, which creates what should exist and
          does not (after ``min_support`` was lowered, say);
        * ``rules``: every enabled rule is composed wherever it has evidence.

        A cursor is JSON: ``{"phase": ..., "after": key or None}``.  A step processes at most ``limit`` items of one
        phase; one that finds fewer moves on to the start of the next phase.
        """
        cursor = cursor or {"phase": "derived", "after": None}
        phase, after = cursor["phase"], cursor.get("after")
        before = self.store.revision
        out: list[Derivation] = []
        if phase == "derived":
            page = self.store.active_derived_page(org_id, after, limit)
            for _, m in page:
                out.extend(self.reconcile(tx, m))
            last = page[-1][0] if page else None
            n = len(page)
        elif phase == "topics":
            todo = [k for k in self._topic_keys(org_id) if after is None or k > tuple(after)][:limit]
            for _, unit, topic in todo:
                out.extend(self._cascade(tx, self._reconcile_consolidation(tx, org_id, unit, topic)))
            last = list(todo[-1]) if todo else None
            n = len(todo)
        elif phase == "rules":
            rules, keys = self._rule_keys_of(org_id)
            todo = [k for k in keys if after is None or k > tuple(after)][:limit]
            for rule_id, unit, has_entity, entity in todo:
                out.extend(self._cascade(tx, self._compose_rule(tx, rules[rule_id], org_id, unit, entity if has_entity else None)))
            last = list(todo[-1]) if todo else None
            n = len(todo)
        else:
            raise ValueError(f"unknown re-aggregation phase {phase!r}")
        if n >= limit:
            nxt: dict[str, Any] | None = {"phase": phase, "after": last}
        else:
            i = REAGGREGATE_PHASES.index(phase)
            nxt = {"phase": REAGGREGATE_PHASES[i + 1], "after": None} if i + 1 < len(REAGGREGATE_PHASES) else None
        return out, self.store.revision - before, nxt

    # ------------------------------------------------------------------ topic consolidation
    def plan_consolidation(self, org_id: str, unit: str, topic: str) -> Plan:
        """The consolidation ``unit`` should carry on ``topic`` given the applied evidence (read-only)."""
        consolidations: list[Memory] = []
        if layer_of_path(unit) != "team":             # a team's children are agents: nothing below it consolidates
            consolidations = [m for m in self._latest(org_id, unit, topic, topic=topic, operators=("topic_consolidation",))
                              if m.scope != unit]
        # a child with its own consolidation contributes exactly that and the conclusions sitting at the child itself,
        # so its leaves below are not read at all and the newest leaves are those of the children that still need them
        consolidated = sorted(m.scope for m in consolidations if parent_path(m.scope) == unit)
        leaves = [m for m in self._latest(org_id, unit, topic, topic=topic, operators=("agent_observation", "slot_composition"),
                                          exclude_subtrees=consolidated) if m.scope != unit]
        if consolidated:
            at_child = set(consolidated)
            leaves += [m for m in self._latest(org_id, unit, topic, topic=topic, operators=("slot_composition",))
                       if m.scope in at_child]
        contributions = self._contributions(unit, consolidations + leaves)
        # A unit with fewer registered child units than min_support (a subsidiary with one department, an
        # enterprise with one region) would otherwise never get a memory.  Such a unit *promotes* a child's own
        # consolidation (which already stands for at least ``min_support`` agents), together with whatever its other
        # children contribute, so one more note from a sibling never takes it away; that keeps the chain of
        # transformations explicit in the lineage instead of leaving the top layers empty.  A raw observation is
        # never promoted on its own: a team memory always means at least ``min_support`` agents agreed, and a solo
        # agent's note stays discoverable through subtree search.
        registered_children = self.store.child_units(org_id, unit)
        promotion = (0 < len(contributions) < self.min_support and len(registered_children) < self.min_support
                     and promoted_child(contributions) is not None)
        current = self.store.current_derived(org_id, "topic_consolidation", unit, topic)
        if len(contributions) < self.min_support and not promotion:
            return Plan(memory=None, parents=[], current=current, contributions=contributions)
        memory = build_consolidation(org_id, unit, topic, contributions, promotion=promotion,
                                     effective_min_support=1 if promotion else self.min_support,
                                     registered_child_units=len(registered_children),
                                     version_of=current.memory_id if current else None, min_support=self.min_support,
                                     now=self.clock())
        return Plan(memory=memory, parents=[m for group in contributions.values() for m in group], current=current,
                    contributions=contributions)

    def _consolidate_topic(self, tx: Tx, org_id: str, scope: str, topic: str) -> list[Derivation]:
        results: list[Derivation] = []
        for unit in reversed(ancestors(scope, include_self=False)):          # team first, enterprise last
            results.extend(self._reconcile_consolidation(tx, org_id, unit, topic))
        return results

    def _contributions(self, unit: str, mems: list[Memory]) -> dict[str, list[Memory]]:
        """What each direct child of ``unit`` contributes on the topic: its own consolidation, or its subtree's best,
        and the rule conclusions sitting exactly at the child.

        Leaves are raw observations and rule conclusions (anything that is not itself a consolidation).  A conclusion
        at a unit is never part of that unit's own consolidation (whose parents lie strictly below it), so it travels
        upward next to it: neither one stands in for the other.
        """
        derived_by_scope = {m.scope: m for m in mems if m.operator == "topic_consolidation"}
        leaves = [m for m in mems if m.operator != "topic_consolidation"]
        by_child: dict[str, list[Memory]] = {}
        for m in leaves:
            by_child.setdefault(child_unit_of(m.scope, unit), [])
        for scope in derived_by_scope:
            if scope != unit and scope.startswith(unit + "/"):
                by_child.setdefault(child_unit_of(scope, unit), [])
        out: dict[str, list[Memory]] = {}
        for child in sorted(by_child):
            best = self._best_in_subtree(child, derived_by_scope, leaves)
            if best:
                out[child] = best
        return out

    def _best_in_subtree(self, unit: str, derived_by_scope: dict[str, Memory], leaves: list[Memory]) -> list[Memory]:
        here = [m for m in leaves if m.scope == unit]          # an agent's notes, or the conclusions at a unit
        if unit in derived_by_scope:
            return [derived_by_scope[unit], *here]
        if layer_of_path(unit) == "agent":
            return here
        # grandchildren come from consolidations too: a capped leaf set must not hide a consolidation below
        children = sorted({child_unit_of(s, unit) for s in [*(m.scope for m in leaves), *derived_by_scope]
                           if s.startswith(unit + "/")})
        out: list[Memory] = list(here)
        for child in children:
            out.extend(self._best_in_subtree(child, derived_by_scope, leaves))
        return out

    # ------------------------------------------------------------------ slot composition (rules)
    def _compose_rules(self, tx: Tx, memory: Memory) -> list[Derivation]:
        results: list[Derivation] = []
        for rule in self.store.list_applied_rules(memory.org_id):
            if memory.slot not in rule.required_slots or memory.operator not in rule.sources:
                continue
            if rule.rule_id in rule_chain(memory):
                continue                                   # a rule never feeds on its own conclusions, however indirectly
            if rule.topic_prefix and not (memory.topic or "").startswith(rule.topic_prefix):
                continue
            target_unit = unit_at_layer(memory.scope, rule.target_layer)
            if target_unit is None:
                continue
            results.extend(self._compose_rule(tx, rule, memory.org_id, target_unit, memory.entity))
            if memory.entity is not None and self._wildcard_due(rule, memory.org_id, target_unit):
                # evidence about one entity can also change which memory is strongest for the rule's '*' conclusion
                results.extend(self._compose_rule(tx, rule, memory.org_id, target_unit, None))
        return results

    def _wildcard_due(self, rule: Rule, org_id: str, unit: str) -> bool:
        """Is there a '*' conclusion of ``rule`` at ``unit`` to refresh, or an entity-less memory that could form one?"""
        if self.store.current_derived(org_id, "slot_composition", unit, f"{rule.rule_id}:*") is not None:
            return True
        return bool(self.store.list_memories(org_id, scope=unit, status="active", null_entity=True, operators=rule.sources,
                                             slots=rule.required_slots, topic_prefix=rule.topic_prefix, latest=True, limit=1))

    def plan_rule(self, rule: Rule, org_id: str, unit: str, entity: str | None) -> Plan:
        """The conclusion ``rule`` should have at ``unit`` for ``entity`` given the applied evidence (read-only)."""
        key = f"{rule.rule_id}:{entity or '*'}"
        candidates = self._latest(org_id, unit, key, entity=entity, operators=rule.sources, slots=rule.required_slots,
                                  topic_prefix=rule.topic_prefix)
        candidates = [m for m in candidates if rule.rule_id not in rule_chain(m)]
        # finer-grained evidence wins: a consolidation whose own parents are already candidates would only
        # restate them (and count them twice), so it is used only when its parents are not available here
        ids = {m.memory_id for m in candidates}
        candidates = [m for m in candidates if m.operator != "topic_consolidation"
                      or not all(e.parent_id in ids for e in self.store.parents_of(m.memory_id))]
        current = self.store.current_derived(org_id, "slot_composition", unit, key)
        contributions = {slot: [m for m in candidates if m.slot == slot] for slot in rule.required_slots}
        built = build_conclusion(rule, org_id, unit, entity, candidates,
                                 version_of=current.memory_id if current else None, now=self.clock())
        if built is None:
            return Plan(memory=None, parents=[], current=current, contributions=contributions)
        memory, evidence = built
        return Plan(memory=memory, parents=evidence, current=current, contributions=contributions)

    def _compose_rule(self, tx: Tx, rule: Rule, org_id: str, target_unit: str, entity: str | None) -> list[Derivation]:
        """Evaluate one rule at one unit: the new conclusion (possibly with what its withdrawal re-derived), or []."""
        plan = self.plan_rule(rule, org_id, target_unit, entity)
        current = plan.current
        if plan.memory is None:
            return self._withdraw(tx, current) if current is not None else []
        if current is not None and current.memory_id == plan.memory.memory_id:
            return []
        claims = tuple(_claim(m, rule, plan.memory.created_at) for group in plan.contributions.values() for m in group)
        slots = tuple(rule.required_slots)
        synthesis = RuleBasedSynthesizer(slots).synthesize(claims)
        plan.memory.metadata["fragility"] = to_jsonable(LineageAnalyzer().score(_scored_claims(claims, slots), slots, synthesis))
        d = self._persist(tx, plan.memory, plan.parents, current)
        return [d] if d is not None else []

    # ------------------------------------------------------------------ persistence
    def _inconsistency(self, tx: Tx, kind: str, memory: Memory, stored: Memory) -> None:
        """Report derived state that disagrees with its recomputation; applying goes on (one poisoned memory must not
        stall the log).  The audit row carries hashes of the texts, never the texts."""
        logger.error("aggregation inconsistency (%s) for %s at %s: stored %s/%s, recomputed %s/%s", kind, memory.memory_id,
                     memory.scope, stored.operator, stored.confidence, memory.operator, memory.confidence)
        self._emit("inconsistency", {"kind": kind})
        tx.audit(MYCELIC_PRODUCER, "aggregation.inconsistency", memory.memory_id, {
            "org_id": memory.org_id, "kind": kind, "operator": memory.operator, "scope": memory.scope,
            "stored_text_sha": content_hash(stored.text), "recomputed_text_sha": content_hash(memory.text),
            "stored_confidence": stored.confidence, "recomputed_confidence": memory.confidence})

    def _persist(self, tx: Tx, memory: Memory, parents: list[Memory], current: Memory | None) -> Derivation | None:
        """Make ``memory`` the active version for its (unit, operator, key): insert it, or reactivate the identical
        earlier version.  None when its id is taken by an unrelated row (reported; nothing changes)."""
        now = memory.created_at
        existing = self.store.get_memory(memory.memory_id)
        reactivation = (existing is not None and existing.status != "active" and existing.operator == memory.operator
                        and existing.scope == memory.scope)
        if existing is not None and not reactivation:
            # never attach lineage to (or supersede in favour of) a row this derivation did not produce
            self._inconsistency(tx, "id_collision", memory, existing)
            return None
        edges = [LineageEdge(child_id=memory.memory_id, parent_id=p.memory_id,
                             contributed_by=p.producer_id if p.layer == "agent" else MYCELIC_PRODUCER,
                             parent_layer=p.layer, created_at=now) for p in parents]
        supersedes = None
        if current is not None:              # before the insert: only one active memory per (unit, operator, key)
            tx.set_memory_status(current.memory_id, "superseded", superseded_by=memory.memory_id, reason="coalition changed")
            supersedes = current.memory_id
        if existing is None:
            tx.insert_memory(memory, parent_ids=[p.memory_id for p in parents])
        else:
            # the exact earlier coalition is back (evidence was retracted, or a retraction was undone by new
            # evidence): the earlier derived memory becomes current again, keeping its id, text and lineage edges
            if (existing.text != memory.text or round(existing.confidence, 4) != round(memory.confidence, 4)
                    or existing.support != memory.support):
                self._inconsistency(tx, "reactivation_mismatch", memory, existing)
            version_of = current.memory_id if current is not None else existing.metadata.get("version_of")
            meta = {**existing.metadata, **memory.metadata, "version_of": version_of, "reactivated_at": now}
            meta.pop("status_reason", None)          # the reason it was retired does not describe an active memory
            for k in (*DERIVED_METADATA, *OPTIONAL_DERIVED_METADATA):
                # keep what the digest signed: equal in every consistent case, and a recomputation that differs was
                # reported as reactivation_mismatch above, so verification sees a derivation mismatch, not tampering
                if k in existing.metadata:
                    meta[k] = existing.metadata[k]
                else:
                    meta.pop(k, None)
            tx.reactivate_memory(memory.memory_id, applied_at=now, metadata=meta)
            memory = self.store.get_memory(memory.memory_id) or memory
        tx.add_lineage_edges(edges)
        logger.info("derived %s at %s (%s) from %d parents%s", memory.memory_id, memory.scope, memory.operator,
                    len(parents), f", supersedes {supersedes}" if supersedes else "")
        return Derivation(memory=memory, edges=edges, supersedes=supersedes, parents=parents)


def _common(mems: list[Memory], attr: str) -> str | None:
    """The one value every parent carries, or None: a parent without it (None) blocks it, so a consolidation never
    claims a slot or entity that only some of its evidence supports."""
    values = {getattr(m, attr) for m in mems}
    return values.pop() if len(values) == 1 else None

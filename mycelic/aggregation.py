"""Aggregation: how agent observations become team, department, subsidiary, region and enterprise memories.

Two deterministic operators run inside the consumer's apply transaction.  No model is involved, so every
derived memory can be explained from its lineage alone and a replay of the event log reproduces it exactly.

**Topic consolidation** (``operator='topic_consolidation'``).  A unit U at layer L gets a memory on topic T
when at least ``min_support`` of its *direct children* contribute something on T.  A child's contribution
is its own consolidation on T if it has one, otherwise the best material in its subtree (recursively down to
the raw agent observations).  Parents of the derived memory are exactly those contributions, so the lineage
records the chain of units the knowledge passed through, and ``support``/``independent_teams`` count the
distinct agents and teams underneath.  When more evidence arrives the coalition grows, a new derived memory
(new deterministic id) supersedes the old one, and the old one stays readable as a previous version.

**Slot composition** (``operator='slot_composition'``).  A :class:`~mycelic.models.Rule` names the slots a
conclusion needs (for example ``transport_disruption``, ``supplier_buffer_low``, ``demand_commitment``).  The
conclusion exists only once every slot is covered for the same entity by observations from at least
``min_agents`` agents in at least ``min_teams`` teams inside the rule's target unit.  Slot selection reuses
``RuleBasedSynthesizer`` and the support metrics reuse ``LineageAnalyzer`` from the coordination package, with
each raw observation as its own lineage root and its team as its failure domain.

**Composition and strategic synthesis.**  A rule may name ``sources`` other than raw observations, so the
conclusions of one rule (carrying ``emits_slot``) or the consolidations of a unit become evidence for a higher
rule; ``min_units`` demands the evidence span distinct units of a layer ("supply risk in two regions") and
``corroborate`` keeps every memory that fills a slot as a parent.  Derivations cascade: a new conclusion is
itself offered to the rules and consolidations above it, so an enterprise strategy can rest on regional
conclusions that rest on team consolidations that rest on agents' notes, and the lineage records every hop.
When evidence is retracted, every dependent conclusion is retired and then re-evaluated on what remains.

Confidence of a consolidation is the noisy-OR of the strongest contribution per child
(``1 - prod(1 - c_i)``): independent sources agreeing raise confidence, a single source cannot exceed its own.
Confidence of a rule conclusion is the *minimum* over the selected slots, the same conservative choice the
research synthesizer makes: a conclusion is only as certain as its weakest required piece.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from NeuralGraph.research.coordination.contracts import ClaimEnvelope, PolicyStatus
from NeuralGraph.research.coordination.core import LineageAnalyzer, RuleBasedSynthesizer, to_jsonable

from .hierarchy import LAYERS, ancestors, child_unit_of, layer_of_path, unit_at_layer
from .models import LineageEdge, Memory, Rule, derived_memory_id, now_iso
from .store import MycelicStore, Tx

logger = logging.getLogger(__name__)

MYCELIC_PRODUCER = "mycelic"
_SLOT_RE = re.compile(r"\{slot:([a-zA-Z0-9_.-]+)\}")
CONSOLIDATABLE = ("agent_observation", "topic_consolidation", "slot_composition")
MAX_CASCADE = 8


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


def render_consolidation(unit: str, topic: str, contributions: dict[str, list[Memory]], support: int) -> str:
    layer = layer_of_path(unit)
    leaf = unit.rsplit("/", 1)[-1]
    child_layer = LAYERS[LAYERS.index(layer) - 1]
    statements: list[str] = []
    seen: set[str] = set()
    for child in sorted(contributions):
        for m in contributions[child]:
            key = " ".join(m.text.lower().split())
            if key in seen:
                continue
            seen.add(key)
            label = child.rsplit("/", 1)[-1]
            statements.append(f"[{label}] {_clip(m.text)}")
    head = (f"{topic} — consolidated at {layer} '{leaf}' from {len(contributions)} {child_layer} sources "
            f"({support} agent{'s' if support != 1 else ''}): ")
    return head + " ".join(statements)


def render_conclusion(template: str, entity: str | None, slot_texts: dict[str, str]) -> str:
    out = template.replace("{entity}", entity or "unknown entity")
    return _SLOT_RE.sub(lambda m: _clip(slot_texts.get(m.group(1), f"<{m.group(1)}: missing>"), 200), out)


class Aggregator:
    def __init__(self, store: MycelicStore, *, min_support: int = 2, clock: Callable[[], str] = now_iso,
                 max_candidates: int = 5000) -> None:
        self.store = store
        self.min_support = max(1, int(min_support))
        self.clock = clock
        self.max_candidates = max_candidates

    # ------------------------------------------------------------------ entry point
    def derive_for(self, tx: Tx, memory: Memory) -> list[Derivation]:
        """Run every operator that the given (already inserted) memory could have changed.

        Called inside the apply transaction, so reads see the just-inserted rows.  Returns the derivations in the
        order they were persisted (each later consolidation may already include an earlier one as a parent).
        """
        return self._derive(tx, memory, depth=0)

    def _derive(self, tx: Tx, memory: Memory, *, depth: int) -> list[Derivation]:
        out: list[Derivation] = []
        if memory.topic and memory.operator in CONSOLIDATABLE:
            out.extend(self._consolidate_topic(tx, memory.org_id, memory.scope, memory.topic))
        if memory.slot:
            out.extend(self._compose_rules(tx, memory))
        if depth < MAX_CASCADE:
            # a derived memory is evidence for whatever sits above it: rules that take conclusions, consolidations
            # of conclusions across sibling units, and so on up to the enterprise
            for d in list(out):
                out.extend(self._derive(tx, d.memory, depth=depth + 1))
        return out

    def retire_dependents(self, tx: Tx, memory_id: str, reason: str) -> list[str]:
        """Retract every memory derived (transitively) from ``memory_id``; the caller re-derives afterwards."""
        retired = []
        for dep in self.store.dependents_of(memory_id):
            m = self.store.get_memory(dep)
            if m is not None and m.status == "active":
                tx.set_memory_status(dep, "retracted", reason=reason)
                retired.append(dep)
        return retired

    def reevaluate(self, tx: Tx, retired_ids: list[str]) -> list[Derivation]:
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
            elif m.operator == "slot_composition" and m.rule_id:
                rule = self.store.get_rule(m.rule_id)
                if rule is not None and rule.enabled:
                    d = self._compose_rule(tx, rule, m.org_id, m.scope, m.entity)
                    if d is not None:
                        out.append(d)
        for d in list(out):
            out.extend(self._derive(tx, d.memory, depth=1))
        return out

    # ------------------------------------------------------------------ topic consolidation
    def _consolidate_topic(self, tx: Tx, org_id: str, scope: str, topic: str) -> list[Derivation]:
        results: list[Derivation] = []
        for unit in reversed(ancestors(scope, include_self=False)):          # team first, enterprise last
            mems = self.store.list_memories(org_id, scope=unit, topic=topic, status="active", limit=self.max_candidates,
                                            newest_first=False, applied_only=True)
            mems = [m for m in mems if m.operator in CONSOLIDATABLE and m.scope != unit]
            contributions = self._contributions(unit, mems)
            # A unit with fewer registered child units than min_support (a subsidiary with one department, an
            # enterprise with one region) would otherwise never get a memory.  Such a unit *promotes* its child's
            # own consolidation unchanged, which keeps the chain of transformations explicit in the lineage instead
            # of leaving the top layers empty.  A raw observation is never promoted: a team memory always means at
            # least ``min_support`` agents agreed, and a solo agent's note stays discoverable through subtree search.
            registered_children = self.store.child_units(org_id, unit)
            promotion = (0 < len(contributions) < self.min_support and len(registered_children) < self.min_support
                         and all(len(group) == 1 and group[0].operator == "topic_consolidation" and group[0].scope == child
                                 for child, group in contributions.items()))
            current = self.store.current_derived(org_id, "topic_consolidation", unit, topic)
            if len(contributions) < self.min_support and not promotion:
                if current is not None:      # support fell below the threshold (retraction): the memory no longer holds
                    tx.set_memory_status(current.memory_id, "retracted", reason="support below threshold")
                continue
            effective = 1 if promotion else self.min_support
            parents = [m for group in contributions.values() for m in group]
            parent_ids = sorted(m.memory_id for m in parents)
            new_id = derived_memory_id(operator="topic_consolidation", scope=unit, key=topic, parent_ids=parent_ids)
            if current is not None and current.memory_id == new_id:
                continue
            agents = sorted({a for m in parents for a in contributing_agents(m)})
            teams = sorted({t for m in parents for t in contributing_teams(m)})
            confidence = noisy_or([max(m.confidence for m in group) for group in contributions.values()])
            now = self.clock()
            memory = Memory(
                memory_id=new_id, org_id=org_id, layer=layer_of_path(unit), scope=unit,
                text=render_consolidation(unit, topic, contributions, len(agents)), topic=topic,
                slot=_common(parents, "slot"),      # a consolidation of same-slot evidence is itself evidence
                entity=_common(parents, "entity"), kind=_common(parents, "kind") or "fact", confidence=confidence,
                support=len(agents), independent_teams=len(teams), producer_id=MYCELIC_PRODUCER,
                operator="topic_consolidation", rule_id=None, event_id=None, visibility="org", created_at=now,
                applied_at=now, source_event_ids=[], metadata={
                    "agg_key": topic, "contributing_agents": agents, "contributing_teams": teams,
                    "children": sorted(contributions), "child_layer": LAYERS[LAYERS.index(layer_of_path(unit)) - 1],
                    "parent_count": len(parents), "version_of": current.memory_id if current else None,
                    "effective_min_support": effective, "registered_child_units": len(registered_children),
                    "promoted_from": parents[0].memory_id if promotion else None,
                    "roots": sorted({r for m in parents for r in lineage_roots(m)}),
                },
            )
            results.append(self._persist(tx, memory, parents, current))
        return results

    def _contributions(self, unit: str, mems: list[Memory]) -> dict[str, list[Memory]]:
        """What each direct child of ``unit`` contributes on the topic: its own consolidation, or its subtree's best.

        Leaves are raw observations and rule conclusions (anything that is not itself a consolidation); a
        conclusion sitting exactly at a child unit represents that unit.
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
        if unit in derived_by_scope:
            return [derived_by_scope[unit]]
        here = [m for m in leaves if m.scope == unit]
        if here or layer_of_path(unit) == "agent":
            return here
        children = sorted({child_unit_of(m.scope, unit) for m in leaves if m.scope.startswith(unit + "/")})
        out: list[Memory] = []
        for child in children:
            out.extend(self._best_in_subtree(child, derived_by_scope, leaves))
        return out

    # ------------------------------------------------------------------ slot composition (rules)
    def _compose_rules(self, tx: Tx, memory: Memory) -> list[Derivation]:
        results: list[Derivation] = []
        for rule in self.store.list_rules(memory.org_id):
            if memory.slot not in rule.required_slots or memory.operator not in rule.sources:
                continue
            if memory.rule_id == rule.rule_id:
                continue                                   # a rule never feeds on its own conclusions
            if rule.topic_prefix and not (memory.topic or "").startswith(rule.topic_prefix):
                continue
            target_unit = unit_at_layer(memory.scope, rule.target_layer)
            if target_unit is None:
                continue
            derivation = self._compose_rule(tx, rule, memory.org_id, target_unit, memory.entity)
            if derivation is not None:
                results.append(derivation)
        return results

    def _compose_rule(self, tx: Tx, rule: Rule, org_id: str, target_unit: str, entity: str | None) -> Derivation | None:
        candidates = self.store.list_memories(org_id, scope=target_unit, status="active", entity=entity,
                                              operators=rule.sources, limit=self.max_candidates, newest_first=False,
                                              applied_only=True)
        candidates = [m for m in candidates if m.slot in rule.required_slots
                      and m.rule_id != rule.rule_id
                      and (not rule.topic_prefix or (m.topic or "").startswith(rule.topic_prefix))
                      and (entity is None or m.entity == entity)]
        # finer-grained evidence wins: a consolidation whose own parents are already candidates would only
        # restate them (and count them twice), so it is used only when its parents are not available here
        ids = {m.memory_id for m in candidates}
        candidates = [m for m in candidates if m.operator != "topic_consolidation"
                      or not all(e.parent_id in ids for e in self.store.parents_of(m.memory_id))]
        key = f"{rule.rule_id}:{entity or '*'}"
        current = self.store.current_derived(org_id, "slot_composition", target_unit, key)
        claims = tuple(self._claim(m, rule) for m in candidates)
        slots = tuple(rule.required_slots)
        synthesis = RuleBasedSynthesizer(slots).synthesize(claims)
        by_id = {m.memory_id: m for m in candidates}
        selected = [by_id[cid] for cid in synthesis.selected_claim_ids] if synthesis.success else []
        # with corroboration every memory that fills a required slot is evidence, not only the strongest per slot
        evidence = sorted(candidates, key=lambda m: m.memory_id) if (rule.corroborate and synthesis.success) else selected
        agents = sorted({a for m in evidence for a in contributing_agents(m)})
        teams = sorted({t for m in evidence for t in contributing_teams(m)})
        # corroboration is per slot: "supply_risk reported by two regions" counts the units behind that slot only
        units = {slot: {layer: sorted({u for m in evidence if m.slot == slot for u in contributing_units(m, layer)})
                        for layer in per} for slot, per in rule.min_units.items()}
        enough_units = all(len(units[slot][layer]) >= n for slot, per in rule.min_units.items() for layer, n in per.items())
        if not synthesis.success or len(agents) < rule.min_agents or len(teams) < rule.min_teams or not enough_units:
            if current is not None:
                tx.set_memory_status(current.memory_id, "retracted", reason="support below threshold")
            return None
        parent_ids = sorted(m.memory_id for m in evidence)
        new_id = derived_memory_id(operator="slot_composition", scope=target_unit, key=key, parent_ids=parent_ids)
        if current is not None and current.memory_id == new_id:
            return None
        metrics = LineageAnalyzer().score(claims, slots, synthesis)
        slot_texts = {m.slot: m.text for m in selected if m.slot}
        if rule.corroborate:
            # confidence per slot rises with independent corroboration (noisy-OR over the units filling it);
            # the conclusion is as certain as its weakest slot
            per_slot = []
            for slot in slots:
                best_by_unit: dict[str, float] = {}
                for m in evidence:
                    if m.slot == slot:
                        unit = m.scope if m.layer != "agent" else (unit_at_layer(m.scope, "team") or m.scope)
                        best_by_unit[unit] = max(best_by_unit.get(unit, 0.0), m.confidence)
                per_slot.append(noisy_or(list(best_by_unit.values())))
            confidence = round(min(per_slot), 4) if per_slot else 0.0
        else:
            confidence = round(synthesis.confidence, 4)
        now = self.clock()
        memory = Memory(
            memory_id=new_id, org_id=org_id, layer=rule.target_layer, scope=target_unit,
            text=render_conclusion(rule.conclusion, entity, slot_texts),
            topic=rule.emits_topic or rule.topic_prefix or rule.rule_id, slot=rule.emits_slot, entity=entity,
            kind=rule.kind, confidence=confidence, support=len(agents), independent_teams=len(teams),
            producer_id=MYCELIC_PRODUCER, operator="slot_composition", rule_id=rule.rule_id, event_id=None,
            visibility="org", created_at=now, applied_at=now, source_event_ids=[],
            metadata={
                "agg_key": key, "contributing_agents": agents, "contributing_teams": teams,
                "slots": {m.slot: m.memory_id for m in selected if m.slot}, "candidates": len(candidates),
                "evidence": {m.memory_id: {"slot": m.slot, "layer": m.layer, "scope": m.scope, "operator": m.operator}
                             for m in evidence},
                "corroborated_units": units, "roots": sorted({r for m in evidence for r in lineage_roots(m)}),
                "fragility": to_jsonable(metrics), "version_of": current.memory_id if current else None,
            },
        )
        return self._persist(tx, memory, evidence, current)

    @staticmethod
    def _claim(m: Memory, rule: Rule) -> ClaimEnvelope:
        return ClaimEnvelope(
            claim_id=m.memory_id, query_id=rule.rule_id, producer_node_id=m.producer_id,
            content={"slot": m.slot, "value": m.text}, confidence=max(0.0, min(1.0, m.confidence)),
            evidence_refs=(m.memory_id,), source_ids=tuple(m.source_event_ids), parent_memory_ids=(),
            lineage_root_ids=tuple(lineage_roots(m)) or (m.memory_id,),
            failure_domains=tuple(contributing_teams(m)) or (m.scope,),
            policy_status=PolicyStatus.ALLOWED, created_at=m.created_at or now_iso(),
            derivation_operator=m.operator,
        )

    # ------------------------------------------------------------------ persistence
    def _persist(self, tx: Tx, memory: Memory, parents: list[Memory], current: Memory | None) -> Derivation:
        now = memory.created_at
        edges = [LineageEdge(child_id=memory.memory_id, parent_id=p.memory_id,
                             contributed_by=p.producer_id if p.layer == "agent" else MYCELIC_PRODUCER,
                             parent_layer=p.layer, created_at=now) for p in parents]
        supersedes = None
        if current is not None:              # before the insert: only one active memory per (unit, operator, key)
            tx.set_memory_status(current.memory_id, "superseded", superseded_by=memory.memory_id, reason="coalition changed")
            supersedes = current.memory_id
        existing = self.store.get_memory(memory.memory_id)
        if existing is None:
            tx.insert_memory(memory)
        elif existing.status != "active" and existing.operator == memory.operator and existing.scope == memory.scope:
            # the exact earlier coalition is back (evidence was retracted, or a retraction was undone by new
            # evidence): the earlier derived memory becomes current again, keeping its id and its lineage edges
            version_of = current.memory_id if current is not None else existing.metadata.get("version_of")
            tx.reactivate_memory(memory.memory_id, applied_at=now, metadata={**existing.metadata, **memory.metadata,
                                                                            "version_of": version_of, "reactivated_at": now})
            memory = self.store.get_memory(memory.memory_id) or memory
        else:
            raise RuntimeError(f"derived memory id collision for {memory.memory_id}; refusing to attach lineage")
        tx.add_lineage_edges(edges)
        logger.info("derived %s at %s (%s) from %d parents%s", memory.memory_id, memory.scope, memory.operator,
                    len(parents), f", supersedes {supersedes}" if supersedes else "")
        return Derivation(memory=memory, edges=edges, supersedes=supersedes, parents=parents)


def _common(mems: list[Memory], attr: str) -> str | None:
    values = {getattr(m, attr) for m in mems if getattr(m, attr)}
    return values.pop() if len(values) == 1 else None

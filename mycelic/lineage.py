"""Lineage reconstruction: the answer to "where did this come from?" for any memory.

For a memory id the graph is walked upward through ``lineage_edges`` to the raw observations at its roots and
returned as a plain JSON structure that answers, explicitly:

* where it came from (``roots`` and their agents, teams and source events),
* which memories contributed (``nodes``/``edges``),
* which agents and teams contributed (``contributing_agents``/``contributing_teams``),
* which organizational layers transformed it (``layers`` and ``transformations``),
* when it was produced (``timeline``),
* what confidence/support exists (``support``, including the coordination package's fragility metrics for rule
  conclusions),
* whether the underlying evidence can still be reconstructed (``evidence.reconstructable``: every root is still
  present and not retracted, and every source event it cites is still in the log).

Nodes the caller may not read are *redacted*, not dropped: the shape of the lineage stays truthful (the
count of contributions, their layer and unit) while text, agent ids and evidence references are withheld.
That mirrors the ``PolicyStatus.REDACTED`` behaviour of the coordination contracts.
"""
from __future__ import annotations

from typing import Any, Callable

from .hierarchy import LAYERS, unit_at_layer
from .models import Memory
from .store import MycelicStore


class LineageNotFound(KeyError):
    pass


def _node_view(m: Memory, redacted: bool, *, shared_entity: str | None = None) -> dict[str, Any]:
    if redacted:
        return {
            "memory_id": m.memory_id, "layer": m.layer,
            "scope": unit_at_layer(m.scope, "team") if m.layer == "agent" else m.scope,
            "text": None, "topic": m.topic, "slot": m.slot,
            "entity": m.entity if m.entity and m.entity == shared_entity else None, "kind": m.kind,
            "confidence": m.confidence, "support": m.support, "independent_teams": m.independent_teams,
            "producer_id": None, "operator": m.operator, "rule_id": m.rule_id, "status": m.status,
            "created_at": m.created_at, "applied_at": m.applied_at, "event_id": None, "source_event_ids": [],
            "local_ref": None, "redacted": True,
        }
    return {
        "memory_id": m.memory_id, "layer": m.layer, "scope": m.scope, "text": m.text, "topic": m.topic,
        "slot": m.slot, "entity": m.entity, "kind": m.kind, "confidence": m.confidence, "support": m.support,
        "independent_teams": m.independent_teams, "producer_id": m.producer_id, "operator": m.operator,
        "rule_id": m.rule_id, "status": m.status, "superseded_by": m.superseded_by, "created_at": m.created_at,
        "applied_at": m.applied_at, "event_id": m.event_id, "source_event_ids": list(m.source_event_ids),
        "local_ref": m.local_ref, "fragility": m.metadata.get("fragility"), "redacted": False,
    }


def reconstruct(store: MycelicStore, memory_id: str, *, visible: Callable[[Memory], bool],
                max_nodes: int = 2000) -> dict[str, Any]:
    root_memory = store.get_memory(memory_id)
    if root_memory is None:
        raise LineageNotFound(memory_id)

    nodes: dict[str, dict[str, Any]] = {}
    memories: dict[str, Memory] = {memory_id: root_memory}
    edges: list[dict[str, Any]] = []
    missing_parents: list[str] = []
    frontier = [memory_id]
    visited = {memory_id}
    complete = True
    while frontier:
        if len(visited) > max_nodes:
            complete = False
            break
        nxt: list[str] = []
        for child_id in frontier:
            child = memories.get(child_id)
            if child is None:
                continue
            for e in store.parents_of(child_id):
                parent = memories.get(e.parent_id) or store.get_memory(e.parent_id)
                parent_visible = parent is not None and visible(parent)
                edges.append({"child": child_id, "parent": e.parent_id, "parent_layer": e.parent_layer,
                              "contributed_by": e.contributed_by if parent_visible else None,
                              "redacted": not parent_visible})
                if parent is None:
                    missing_parents.append(e.parent_id)
                    continue
                memories[e.parent_id] = parent
                if e.parent_id not in visited:
                    visited.add(e.parent_id)
                    nxt.append(e.parent_id)
        frontier = nxt

    for mid, m in memories.items():
        nodes[mid] = _node_view(m, redacted=not visible(m), shared_entity=root_memory.entity)

    has_parent = {e["child"] for e in edges}
    roots = sorted(mid for mid in memories if mid not in has_parent)
    visible_mems = [m for m in memories.values() if visible(m)]
    redacted_count = len(memories) - len(visible_mems)
    agents = sorted({m.producer_id for m in visible_mems if m.layer == "agent"})
    teams = sorted({unit_at_layer(m.scope, "team") or m.scope for m in memories.values() if m.layer == "agent"})
    layers_present = [layer for layer in LAYERS if any(m.layer == layer for m in memories.values())]
    transformations = []
    for m in memories.values():
        if m.layer == "agent":
            continue
        parent_layers = sorted({e["parent_layer"] for e in edges if e["child"] == m.memory_id},
                               key=LAYERS.index)
        transformations.append({"memory_id": m.memory_id, "operator": m.operator, "rule_id": m.rule_id,
                                "from_layers": parent_layers, "to_layer": m.layer, "scope": m.scope,
                                "at": m.applied_at or m.created_at})
    transformations.sort(key=lambda t: (LAYERS.index(t["to_layer"]), t["at"]))

    # evidence: roots must be present and active, and their source events still in the log
    root_mems = [memories[r] for r in roots]
    cited = sorted({eid for m in root_mems for eid in m.source_event_ids} | {m.event_id for m in root_mems if m.event_id})
    present_events = store.get_events(cited) if cited else {}
    visible_cited = {eid for m in root_mems if visible(m) for eid in (set(m.source_event_ids) | ({m.event_id} if m.event_id else set()))}
    missing_all = [eid for eid in cited if eid not in present_events]
    missing_events = [eid for eid in missing_all if eid in visible_cited]     # never name another team's event ids
    retracted_roots = [m.memory_id for m in root_mems if m.status == "retracted"]
    reconstructable = not missing_parents and not missing_all and not retracted_roots and complete
    observed_times = [m.created_at for m in root_mems if m.created_at]

    # version history of the queried memory
    previous: list[str] = []
    cursor = root_memory.metadata.get("version_of")
    while cursor and len(previous) < 50:
        previous.append(cursor)
        prev = store.get_memory(cursor)
        cursor = prev.metadata.get("version_of") if prev else None

    return {
        "memory_id": memory_id,
        "memory": nodes[memory_id],
        "nodes": nodes,
        "edges": edges,
        "roots": roots,
        "contributing_agents": agents,
        "contributing_teams": teams,
        "redacted_contributions": redacted_count,
        "layers": layers_present,
        "transformations": transformations,
        "timeline": {
            "first_observed_at": min(observed_times) if observed_times else None,
            "last_observed_at": max(observed_times) if observed_times else None,
            "produced_at": root_memory.applied_at or root_memory.created_at,
        },
        "support": {
            "agents": root_memory.support, "teams": root_memory.independent_teams,
            "confidence": root_memory.confidence, "roots": len(roots),
            "fragility": root_memory.metadata.get("fragility"),
        },
        "evidence": {
            "source_event_ids": sorted({eid for m in root_mems if visible(m)
                                        for eid in (set(m.source_event_ids) | ({m.event_id} if m.event_id else set()))}),
            "events_present": len(present_events), "events_missing": missing_events,
            "events_missing_total": len(missing_all),
            "missing_parents": missing_parents, "retracted_roots": retracted_roots,
            "reconstructable": reconstructable,
        },
        "previous_versions": previous,
        "superseded_by": root_memory.superseded_by,
        "complete": complete,
    }

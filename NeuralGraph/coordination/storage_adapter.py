"""Lineage resolution over a real NeuralGraph store.

This module is the single import boundary between the coordination package and
NeuralGraph's storage layer.  ``contracts``, ``core`` and ``adapters`` stay free
of every NeuralGraph storage import so the coordination contracts remain
domain-neutral; only this file knows what a ``NeuralNode`` or an ``EdgeType``
is, and it exports nothing but identifier tuples.

Direction is load-bearing.  A ``HIERARCHY`` edge runs PARENT -> CHILD: the
summary node is the edge's ``source_id`` and the node the summary was built FROM
is its ``target_id`` (``hierarchy.py``).  ``NeuralNode.parent_id`` points the
other way, UP to the summary.  The *derivation* parents of X are therefore the
targets of X's own outgoing ``HIERARCHY`` edges, plus X's self-declared
``source_memory_ids``.  Reading ``parent_id`` or ``get_edges_to`` instead would
invert lineage and report a summary as an ancestor of its own inputs.

Only ``get_node`` and ``get_edges_from`` are used.  Both are on the storage ABC
and are read-only: nothing here writes, and the neural retriever is deliberately
not used because it activates edges (a write) while reading.
"""

from __future__ import annotations

from typing import Any

from ..data_types import EdgeType
from ..storage import NeuralGraphStorage


def _identifier(value: Any, field: str) -> str:
    """Return ``value`` as a plain identifier string.

    A blank or non-``str`` value is not an identifier, and a stored row can hold
    anything a previous build (or a hand-edited database) put there.  The
    message names the field and the offending type only: never the value, never
    surrounding node payload.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a str, got {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    return str.__str__(value)


def _identifiers(values: Any, field: str) -> tuple[str, ...]:
    """Return a stored id collection as plain identifier strings.

    ``str``/``bytes`` are rejected explicitly rather than iterated: a legacy row
    holding ``"memory-42"`` instead of ``["memory-42"]`` would otherwise explode
    into nine single-character "identifiers".
    """
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, (tuple, list)):
        raise ValueError(
            f"{field} must be a tuple or list of identifiers, "
            f"got {type(values).__name__}"
        )
    return tuple(_identifier(value, field) for value in values)


class StorageLineageResolver:
    """Report the derivation facts a real local graph actually records.

    The result is a plain mapping of identifier tuples, suitable as the
    ``lineage_resolver`` of ``NeuralGraphMemoryAdapter``.  Nothing is invented:
    a truncated or dangling walk under-reports rather than guessing, which keeps
    correlated copies distinguishable from independent roots.

    Bounds are explicit so a hostile or cyclic graph cannot make one query
    unbounded: the walk expands at most ``max_nodes`` nodes and follows at most
    ``max_depth`` derivation hops.  Storage errors propagate; they are never
    swallowed into empty lineage, which would silently claim "no known
    ancestors" for a memory whose ancestry merely failed to load.
    """

    def __init__(
        self,
        storage: NeuralGraphStorage,
        *,
        max_depth: int = 3,
        max_nodes: int = 64,
        domain_key: str | None = None,
    ) -> None:
        """``domain_key`` opts in to reading a recorded failure domain.

        NeuralGraph has no failure-domain concept of its own, so by default this
        resolver reports none and every domain-derived metric stays visibly
        zero.  That default is deliberate: a synthesised domain would make
        coalition counts and the minimum failure-domain cut look meaningful
        while measuring nothing.

        When ``domain_key`` is set, the domain of a memory is read from the
        metadata of the **lineage roots** the walk actually resolved -- not from
        the retrieved node itself.  That is what a failure domain means here:
        the upstream origin a memory ultimately derives from, which is the thing
        that fails as a unit.  Two nodes in different databases that ingested
        the same upstream feed share a domain; two nodes that merely hold equal
        values do not.

        A root recording no domain contributes none.  Absence is reported as
        absence, so provenance that was never recorded can never be mistaken for
        a genuine independent origin.
        """
        if max_depth < 0:
            raise ValueError("max_depth must not be negative")
        if max_nodes < 0:
            raise ValueError("max_nodes must not be negative")
        if domain_key is not None and not str(domain_key).strip():
            raise ValueError("domain_key must not be blank")
        self._storage = storage
        self._max_depth = max_depth
        self._max_nodes = max_nodes
        self._domain_key = domain_key

    def _declared_domain(self, node: Any) -> str | None:
        """Read a recorded failure domain off one resolved root node."""
        if self._domain_key is None:
            return None
        metadata = getattr(node, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        value = metadata.get(self._domain_key)
        if value is None:
            return None
        return _identifier(value, f"metadata[{self._domain_key}]")

    async def __call__(self, node: Any) -> dict[str, Any]:
        """Resolve the derivation facts recorded for one retrieved node.

        ``source_ids`` are what the node declares about itself.
        ``parent_memory_ids`` are its direct derivation parents as recorded,
        including ids whose node no longer resolves: a dangling id is still a
        derivation fact and dropping it would hide a lost dependency.
        ``lineage_root_ids`` are only nodes the walk actually reached and
        resolved that themselves record no derivation parent, so a dangling id
        is never a root and an unreachable ancestor is never invented.
        ``edge_path`` is every ``HIERARCHY`` edge the walk read while computing
        direct derivation parents, which at the depth or node bound can include
        an edge whose target was never expanded.  The five keys below are
        always present and are the complete surface: no claim payload and no
        retrieval-operator label is ever contributed from here.
        """
        origin_id = _identifier(getattr(node, "node_id", None), "node_id")
        declared = _identifiers(getattr(node, "source_memory_ids", ()), "source_memory_ids")
        edge_ids: set[str] = set()
        parents = await self._direct_parents(origin_id, declared, edge_ids)
        domains: set[str] = set()
        roots = await self._walk(origin_id, parents, edge_ids, domains)
        return {
            "source_ids": tuple(sorted(set(declared))),
            "parent_memory_ids": tuple(sorted(parents)),
            "lineage_root_ids": tuple(sorted(roots)),
            "edge_path": tuple(sorted(edge_ids)),
            # Empty unless ``domain_key`` was configured AND a resolved root
            # actually records a domain under it.  Absence stays visible rather
            # than being synthesised into a domain the graph does not record;
            # see the coordination lineage tests for the effect on
            # LineageAnalyzer.
            "failure_domains": tuple(sorted(domains)),
        }

    async def _direct_parents(
        self,
        node_id: str,
        declared: tuple[str, ...],
        edge_ids: set[str],
    ) -> set[str]:
        """Return the direct derivation parents of ``node_id``.

        The union of the node's own ``source_memory_ids`` and the targets of its
        outgoing ``HIERARCHY`` edges.  ``child_ids`` is deliberately unused: it
        records containment, not derivation, and under-reporting is the safe
        direction.  Traversed edge ids accumulate into ``edge_ids``.
        """
        edges = await self._storage.get_edges_from(node_id, [EdgeType.HIERARCHY])
        # Storage may return an unordered collection (the in-memory backend
        # iterates a set), so every result is sorted before it is used.
        observed = sorted(
            (
                _identifier(getattr(edge, "edge_id", None), "edge_id"),
                _identifier(getattr(edge, "target_id", None), "target_id"),
            )
            for edge in edges
        )
        parents = set(declared)
        for edge_id, target_id in observed:
            edge_ids.add(edge_id)
            parents.add(target_id)
        return parents

    async def _walk(
        self,
        origin_id: str,
        parents: set[str],
        edge_ids: set[str],
        domains: set[str],
    ) -> set[str]:
        """Breadth-first search upward for nodes that record no parent.

        ``origin_id`` is seeded as visited, so a cycle terminates and the origin
        never roots itself.  Each frontier is expanded in sorted id order, so a
        walk truncated by ``max_nodes`` truncates identically on every run and
        on every backend.
        """
        roots: set[str] = set()
        visited = {origin_id}
        frontier = sorted(parents - visited)
        visited.update(frontier)
        expansions = 0
        depth = 0
        while frontier and depth < self._max_depth and expansions < self._max_nodes:
            depth += 1
            next_frontier: list[str] = []
            for candidate in frontier:
                if expansions >= self._max_nodes:
                    break
                expansions += 1
                reached = await self._storage.get_node(candidate)
                if reached is None:
                    # A recorded id whose node is gone stays a parent, but it
                    # cannot be confirmed as a root: its own ancestry is unknown.
                    continue
                declared = _identifiers(
                    getattr(reached, "source_memory_ids", ()), "source_memory_ids"
                )
                candidate_parents = await self._direct_parents(candidate, declared, edge_ids)
                if not candidate_parents:
                    roots.add(candidate)
                    declared_domain = self._declared_domain(reached)
                    if declared_domain is not None:
                        domains.add(declared_domain)
                    continue
                for parent in sorted(candidate_parents - visited):
                    visited.add(parent)
                    next_frontier.append(parent)
            frontier = sorted(next_frontier)
        return roots

"""Organizational hierarchy: layers, unit paths and the subtree/ancestor relations everything else uses.

Mycelic propagates knowledge through six layers::

    agent -> team -> department -> subsidiary -> region -> enterprise

A *unit path* names one organizational unit from the enterprise root downwards, joined with ``/``::

    northwind                                   enterprise   (layer index 5)
    northwind/emea                              region       (4)
    northwind/emea/northwind-gmbh               subsidiary   (3)
    northwind/emea/northwind-gmbh/operations    department   (2)
    northwind/emea/northwind-gmbh/operations/logistics          team   (1)
    northwind/emea/northwind-gmbh/operations/logistics/agent-7  agent  (0)

The enterprise segment doubles as the organization id: two agents share an organization exactly
when their paths share a root.  A memory's ``scope`` is the path of the unit it belongs to, so the
layer of a memory is a function of its scope and the subtree of a unit is a prefix match.  Both
facts are relied upon by the store (SQL ``LIKE scope || '/%'``) and by authorization.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LAYERS: tuple[str, ...] = ("agent", "team", "department", "subsidiary", "region", "enterprise")
LAYER_INDEX: dict[str, int] = {name: i for i, name in enumerate(LAYERS)}
UNIT_LAYERS: tuple[str, ...] = LAYERS[1:]            # layers an org unit can have (agents are leaves)
ENTERPRISE_INDEX = LAYER_INDEX["enterprise"]
DEPTH = len(LAYERS)                                    # 6 segments in a full agent path

_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class HierarchyError(ValueError):
    """A path or layer name that does not fit the hierarchy."""


def validate_segment(segment: str, what: str = "segment") -> str:
    if not isinstance(segment, str) or not _SEGMENT_RE.match(segment):
        raise HierarchyError(f"{what} must match {_SEGMENT_RE.pattern!r} (lowercase, no '/'): {segment!r}")
    return segment


def split_path(path: str) -> list[str]:
    if not isinstance(path, str) or not path:
        raise HierarchyError("path must be a non-empty string")
    parts = path.split("/")
    if len(parts) > DEPTH:
        raise HierarchyError(f"path has {len(parts)} segments; at most {DEPTH} are allowed: {path!r}")
    return [validate_segment(p, "path segment") for p in parts]


def layer_of_path(path: str) -> str:
    """The layer a unit path denotes: 1 segment is the enterprise, 6 segments is an agent."""
    n = len(split_path(path))
    return LAYERS[DEPTH - n]


def layer_index(layer: str) -> int:
    if layer not in LAYER_INDEX:
        raise HierarchyError(f"unknown layer {layer!r}; expected one of {LAYERS}")
    return LAYER_INDEX[layer]


def parent_path(path: str) -> str | None:
    parts = split_path(path)
    return "/".join(parts[:-1]) if len(parts) > 1 else None


def ancestors(path: str, include_self: bool = True) -> list[str]:
    """Unit paths from the enterprise root down to ``path`` (inclusive by default)."""
    parts = split_path(path)
    out = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return out if include_self else out[:-1]


def org_of(path: str) -> str:
    return split_path(path)[0]


def is_ancestor_or_self(unit: str, path: str) -> bool:
    """True when ``unit`` is ``path`` itself or one of its ancestors (``path`` lies in ``unit``'s subtree)."""
    return path == unit or path.startswith(unit + "/")


def unit_at_layer(path: str, layer: str) -> str | None:
    """The ancestor of ``path`` at ``layer`` (None when ``path`` is above that layer)."""
    parts = split_path(path)
    want = DEPTH - layer_index(layer)
    if len(parts) < want:
        return None
    return "/".join(parts[:want])


def child_unit_of(descendant: str, unit: str) -> str:
    """The direct child of ``unit`` that contains ``descendant`` (used to count independent children)."""
    if not is_ancestor_or_self(unit, descendant) or descendant == unit:
        raise HierarchyError(f"{descendant!r} is not below {unit!r}")
    unit_parts = split_path(unit)
    return "/".join(split_path(descendant)[: len(unit_parts) + 1])


@dataclass(frozen=True)
class AgentPath:
    """The five organizational units an agent belongs to plus its own id, as one validated path."""

    enterprise: str
    region: str
    subsidiary: str
    department: str
    team: str
    agent_id: str

    @classmethod
    def from_parts(cls, *, enterprise: str, region: str | None = None, subsidiary: str | None = None,
                   department: str | None = None, team: str | None = None, agent_id: str) -> "AgentPath":
        """Missing intermediate units default to the enterprise name so small organizations need not invent them."""
        e = validate_segment(enterprise, "enterprise")
        return cls(
            enterprise=e,
            region=validate_segment(region or e, "region"),
            subsidiary=validate_segment(subsidiary or e, "subsidiary"),
            department=validate_segment(department or e, "department"),
            team=validate_segment(team or e, "team"),
            agent_id=validate_segment(agent_id, "agent_id"),
        )

    @classmethod
    def parse(cls, path: str) -> "AgentPath":
        parts = split_path(path)
        if len(parts) != DEPTH:
            raise HierarchyError(f"an agent path needs {DEPTH} segments, got {len(parts)}: {path!r}")
        return cls(*parts)

    @property
    def path(self) -> str:
        return "/".join((self.enterprise, self.region, self.subsidiary, self.department, self.team, self.agent_id))

    @property
    def team_path(self) -> str:
        return "/".join((self.enterprise, self.region, self.subsidiary, self.department, self.team))

    @property
    def org_id(self) -> str:
        return self.enterprise

    def unit(self, layer: str) -> str:
        return unit_at_layer(self.path, layer) or self.enterprise

    def units(self) -> dict[str, str]:
        return {layer: self.unit(layer) for layer in LAYERS}

    def to_dict(self) -> dict[str, str]:
        return {"enterprise": self.enterprise, "region": self.region, "subsidiary": self.subsidiary,
                "department": self.department, "team": self.team, "agent_id": self.agent_id, "path": self.path}

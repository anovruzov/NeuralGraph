"""The rules channel: hand-written rules from the pack, evaluated over the same cells as the detectors.

A rule names an entity type, a predicate (never a code: cells carry predicates), a window, a minimum count per site
and a minimum number of sites. It fires for an entity when at least ``min_sites`` sites each count at least
``min_count_per_site`` in the rule's own window.

Rules:

* The caller gives, per reported site, the lower-bound window count of each entity of the rule's type for the rule's
  predicate in the run's channels (a ``'<k'`` cell counts 1, so a rule never fires on a count the cells cannot
  prove). Late sites are left out by the caller; there is no history requirement and no stale filter.
* Rule hits are never ranked and never use the alert budget; detection merges them into candidates by key.
* Pure: no clock, no I/O, sorted iteration only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from ..packs.loader import Rule

KEY_SEPARATOR = ":"


def series_key(entity_type: str, entity_id: str, predicate: str) -> str:
    """``<entity_type>:<entity_id>:<predicate>``; none of the three can contain the separator."""
    return KEY_SEPARATOR.join((entity_type, entity_id, predicate))


@dataclass(frozen=True)
class RuleHit:
    """One rule firing for one key at one week; ``sites`` are the satisfying ``(site, count_lb)`` pairs, sorted."""

    rule_id: str
    key: str
    week: str
    sites: tuple[tuple[str, int], ...]


def evaluate_rule(rule: "Rule", counts: Mapping[str, Mapping[str, int]], *, week: str) -> list[RuleHit]:
    """The rule's hits at ``week`` from ``counts`` (site -> entity id -> lower-bound window count), sorted by key."""
    entity_ids = sorted({eid for site in sorted(counts) for eid in counts[site]})
    hits = []
    for eid in entity_ids:
        sites = tuple((site, counts[site][eid]) for site in sorted(counts)
                      if counts[site].get(eid, 0) >= rule.min_count_per_site)
        if len(sites) >= rule.min_sites:
            hits.append(RuleHit(rule_id=rule.id, key=series_key(rule.entity_type, eid, rule.predicate), week=week,
                                sites=sites))
    return sorted(hits, key=lambda h: h.key)

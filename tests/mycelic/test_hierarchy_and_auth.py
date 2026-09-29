from __future__ import annotations

import unittest

from mycelic.auth import RateLimiter, generate_api_key, memory_visible, parse_agent_id
from mycelic.hierarchy import (
    AgentPath, HierarchyError, ancestors, child_unit_of, is_ancestor_or_self, layer_of_path, unit_at_layer,
)
from mycelic.models import Memory


def mem(layer: str, scope: str, visibility: str = "team") -> Memory:
    return Memory(memory_id="m", org_id="acme", layer=layer, scope=scope, text="t", topic=None, slot=None, entity=None,
                  kind="fact", confidence=0.5, support=1, independent_teams=1, producer_id="a", operator="agent_observation",
                  rule_id=None, event_id=None, visibility=visibility)


class HierarchyTests(unittest.TestCase):
    def test_layers_follow_path_depth(self) -> None:
        p = AgentPath.from_parts(enterprise="acme", region="eu", subsidiary="acme-de", department="ops", team="log", agent_id="a1")
        self.assertEqual(layer_of_path(p.path), "agent")
        self.assertEqual(layer_of_path(p.team_path), "team")
        self.assertEqual(layer_of_path("acme"), "enterprise")
        self.assertEqual(unit_at_layer(p.path, "department"), "acme/eu/acme-de/ops")
        self.assertEqual(ancestors(p.team_path), ["acme", "acme/eu", "acme/eu/acme-de", "acme/eu/acme-de/ops", "acme/eu/acme-de/ops/log"])
        self.assertEqual(child_unit_of(p.path, "acme/eu"), "acme/eu/acme-de")
        self.assertTrue(is_ancestor_or_self("acme/eu", p.path))
        self.assertFalse(is_ancestor_or_self("acme/eu/other", p.path))

    def test_small_org_defaults_and_validation(self) -> None:
        p = AgentPath.from_parts(enterprise="acme", agent_id="a1")
        self.assertEqual(p.path, "acme/acme/acme/acme/acme/a1")
        with self.assertRaises(HierarchyError):
            AgentPath.from_parts(enterprise="Acme Inc", agent_id="a1")
        with self.assertRaises(HierarchyError):
            AgentPath.parse("acme/eu")
        with self.assertRaises(HierarchyError):
            layer_of_path("a/b/c/d/e/f/g")


class AuthTests(unittest.TestCase):
    def test_key_format(self) -> None:
        key, key_hash, prefix = generate_api_key("agent-7")
        self.assertTrue(key.startswith("mk_agent-7."))
        self.assertEqual(parse_agent_id(key), "agent-7")
        self.assertEqual(len(key_hash), 64)
        self.assertIsNone(parse_agent_id("nope"))

    def test_visibility_rules(self) -> None:
        me, team = "acme/eu/de/ops/log/a1", "acme/eu/de/ops/log"
        self.assertTrue(memory_visible(mem("agent", "acme/eu/de/ops/log/a2"), agent_path=me, team_path=team))
        self.assertFalse(memory_visible(mem("agent", "acme/eu/de/ops/proc/b1"), agent_path=me, team_path=team))
        self.assertTrue(memory_visible(mem("agent", "acme/eu/de/ops/proc/b1", "org"), agent_path=me, team_path=team))
        self.assertTrue(memory_visible(mem("department", "acme/eu/de/ops"), agent_path=me, team_path=team))
        self.assertTrue(memory_visible(mem("enterprise", "acme"), agent_path=me, team_path=team))
        self.assertFalse(memory_visible(mem("department", "acme/eu/de/sales"), agent_path=me, team_path=team))
        self.assertFalse(memory_visible(mem("team", "acme/eu/de/ops/proc"), agent_path=me, team_path=team))
        self.assertTrue(memory_visible(mem("team", "acme/eu/de/ops/proc"), agent_path=None, team_path=None))

    def test_rate_limiter(self) -> None:
        r = RateLimiter(rps=10, burst=3)
        self.assertEqual([r.allow("k", now=0.0) for _ in range(4)], [True, True, True, False])
        self.assertTrue(r.allow("k", now=0.2))
        self.assertTrue(RateLimiter(rps=0).allow("k"))


if __name__ == "__main__":
    unittest.main()

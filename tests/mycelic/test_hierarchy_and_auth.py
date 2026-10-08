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

    def test_rate_limiter_take_and_debt(self) -> None:
        r = RateLimiter(rps=1, burst=10)
        self.assertTrue(r.allow("k", now=0.0))
        r.take("k", 5, now=0.0)
        self.assertEqual(r._buckets["k"].tokens, 4.0)
        self.assertFalse(r.in_debt("k", now=0.0))
        r.take("k", 6, now=0.0)
        self.assertEqual(r._buckets["k"].tokens, -2.0)
        self.assertTrue(r.in_debt("k", now=0.0))
        self.assertFalse(r.allow("k", now=0.0))
        self.assertEqual(r._buckets["k"].tokens, -2.0, "a refused request takes nothing")
        self.assertTrue(r.in_debt("k", now=1.0))
        self.assertFalse(r.in_debt("k", now=2.0))
        self.assertFalse(r.allow("k", now=2.0), "out of debt is not yet solvent")
        self.assertTrue(r.allow("k", now=3.0))
        # a debt is as deep as the charge, past one burst too (a cap would price every long walk the same): a charge of 25
        # tokens at 1 token/s keeps the caller refused for 25 s
        r.take("k", 25, now=3.0)
        self.assertEqual(r._buckets["k"].tokens, -25.0)
        self.assertTrue(r.in_debt("k", now=27.5))
        self.assertFalse(r.in_debt("k", now=28.0))
        self.assertFalse(r.allow("k", now=28.5))
        self.assertTrue(r.allow("k", now=29.0))
        # nothing to charge, and an unknown key, create no bucket
        r.take("other", 0, now=0.0)
        r.take("other", -3, now=0.0)
        self.assertFalse(r.in_debt("unknown", now=0.0))
        self.assertEqual(set(r._buckets), {"k"})
        # limiting off: take is a no-op and nobody is ever in debt
        off = RateLimiter(rps=0, burst=10)
        off.take("k", 50)
        self.assertFalse(off.in_debt("k"))
        self.assertEqual(off._buckets, {})
        # the injected clock is used whenever no time is passed; frozen, it never refills
        t = [0.0]
        c = RateLimiter(rps=1, burst=10, clock=lambda: t[0])
        c.take("k", 12)
        self.assertEqual(c._buckets["k"].tokens, -2.0)
        for _ in range(3):
            self.assertTrue(c.in_debt("k"))
            self.assertFalse(c.allow("k"))
        t[0] = 2.0
        self.assertFalse(c.in_debt("k"))
        self.assertEqual(c._buckets["k"].updated, 2.0)
        frozen = RateLimiter(rps=1, burst=2, clock=lambda: 7.0)
        self.assertEqual([frozen.allow("k") for _ in range(3)], [True, True, False])


if __name__ == "__main__":
    unittest.main()

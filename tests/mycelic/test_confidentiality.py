"""Confidentiality of the upward flow: what each consolidation may quote (by visibility, without agent ids, bounded, as
a function of its parent set alone), who may read the text of a memory that is no longer active, and the upgrade from
rows derived by the previous derivation version."""
from __future__ import annotations

import json
import random
import time
import unittest
from typing import Any
from unittest import mock

from mycelic import aggregation
from mycelic import service as service_module
from mycelic.aggregation import (
    MAX_DERIVED_TEXT, MAX_STATEMENTS, STATEMENT_CHARS, _clip, build_conclusion, build_consolidation, consolidation_statements,
    render_conclusion,
)
from mycelic.auth import Principal
from mycelic.lineage import reconstruct
from mycelic.mcp import MycelicTools, _principal
from mycelic.metrics import Metrics
from mycelic.models import DERIVATION_VERSION, MEMORY_STATUS, Agent, Memory, rule_digest, rule_snapshot
from mycelic.service import MycelicService

from .helpers import (
    DEMO_RULE, ServiceHarness, invariant_violations, memory_history, pump, rebuild, rebuild_differences, settings,
)

ORG = "northwind"
REGION = "northwind/emea"
SUB = "northwind/emea/nw-gmbh"
D1, D2 = f"{SUB}/d1", f"{SUB}/d2"
TEAMS = {"t1": "d1", "t2": "d1", "t3": "d2", "t4": "d2"}
UPPER = ("department", "subsidiary", "region", "enterprise")
AT = "2026-10-01T09:00:00+00:00"
TOPIC = "ops:x"
TRANSPORT, SUPPLIER, DEMAND = "supply:sd-9/transport", "supply:sd-9/supplier", "supply:sd-9/demand"


def team_path(team: str) -> str:
    return f"{SUB}/{TEAMS[team]}/{team}"


class InlineHarness(ServiceHarness):
    """A service that is never started: ``settle`` publishes and applies the outbox inline (``pump``) and keeps the log,
    so the run can be rebuilt."""

    def __init__(self, **overrides: Any) -> None:
        super().__init__(**overrides)
        self.log: list[dict[str, Any]] = []

    async def start(self) -> "InlineHarness":
        await self.service.transport.connect()
        return self

    async def settle(self, timeout: float = 10.0) -> None:
        await pump(self.service, self.log)


async def org12(h: ServiceHarness) -> dict[str, list[str]]:
    """agent-zq7x-1 … agent-zq7x-12, three per team: t1 and t2 in department d1, t3 and t4 in d2 (subsidiary nw-gmbh,
    region emea, enterprise northwind)."""
    agents: dict[str, list[str]] = {}
    n = 0
    for team, department in TEAMS.items():
        agents[team] = []
        for _ in range(3):
            n += 1
            await h.register(f"agent-zq7x-{n}", team=team, department=department)
            agents[team].append(f"agent-zq7x-{n}")
    return agents


async def note(h: ServiceHarness, agent_id: str, text: str, key: str, *, topic: str | None = TOPIC, visibility: str = "team",
               at: str = AT, **fields: Any) -> str:
    return await h.observe(agent_id, text, topic=topic, visibility=visibility, idempotency_key=key, observed_at=at, **fields)


async def supply_evidence(h: ServiceHarness, agents: dict[str, list[str]]) -> dict[str, str]:
    """DEMO_RULE's three slots in three teams; the org-visible transport note A is the strongest, so the conclusion rests on it."""
    t1, t2, t3 = agents["t1"], agents["t2"], agents["t3"]
    return {
        "A": await note(h, t1[0], "Port of Rotterdam terminal 3 strike announced.", "A", topic=TRANSPORT,
                        slot="transport_disruption", entity="sd-9", visibility="org", confidence=0.9),
        "B": await note(h, t1[1], "Carrier ETA for SD-9 slipped by 12 days.", "B", topic=TRANSPORT,
                        slot="transport_disruption", entity="sd-9", confidence=0.7),
        "S": await note(h, t2[0], "Kessler has two weeks of SD-9 stock left.", "S", topic=SUPPLIER, slot="supplier_buffer_low",
                        entity="sd-9"),
        "D": await note(h, t3[0], "Helios committed to 40 RX-4 arms.", "D", topic=DEMAND, slot="demand_commitment", entity="sd-9"),
    }


def raw(memory_id: str, scope: str, text: str, *, visibility: str = "org", confidence: float = 0.8, at: str = AT,
        status: str = "active", topic: str = TOPIC) -> Memory:
    return Memory(memory_id=memory_id, org_id=ORG, layer="agent", scope=scope, text=text, topic=topic, slot=None, entity=None,
                  kind="observation", confidence=confidence, support=1, independent_teams=1,
                  producer_id=scope.rsplit("/", 1)[-1], operator="agent_observation", rule_id=None, event_id=None,
                  visibility=visibility, status=status, created_at=at)


def consolidated(memory_id: str, scope: str, layer: str, statements: list[str], origins: list[str], *, private: int = 0,
                 confidence: float = 0.8, status: str = "active") -> Memory:
    return Memory(memory_id=memory_id, org_id=ORG, layer=layer, scope=scope, text="never quoted", topic=TOPIC, slot=None,
                  entity=None, kind="fact", confidence=confidence, support=2, independent_teams=1, producer_id="mycelic",
                  operator="topic_consolidation", rule_id=None, event_id=None, visibility="org", status=status,
                  created_at="2026-10-07T00:00:00+00:00",
                  metadata={"statements": statements, "statement_origins": origins, "private_observations": private})


def clip220(text: str) -> str:
    """The documented clipping of a statement, written out again: whitespace runs collapsed, at most 220 code points."""
    t = " ".join(text.split())
    return t if len(t) <= 220 else t[:219].rstrip() + "…"


def offered_at(store: Any, m: Memory) -> int:
    """How many distinct statements the lineage parents of a consolidation offer at its layer, as documented: a raw note
    offers its clipped text (origin its visibility), a conclusion its clipped text (origin rule), a consolidation its
    statements; above team only org and rule statements count, and statements that differ only in case are one."""
    keys = set()
    for e in store.parents_of(m.memory_id):
        p = store.get_memory(e.parent_id)
        if p.operator == "topic_consolidation":
            pairs = list(zip(p.metadata.get("statements") or [], p.metadata.get("statement_origins") or []))
        else:
            pairs = [(clip220(p.text), "rule" if p.operator == "slot_composition" else p.visibility)]
        keys |= {s.casefold() for s, origin in pairs if m.layer == "team" or origin in ("org", "rule")}
    return len(keys)


def quoted(m: Memory) -> str:
    return " ".join([m.text, json.dumps(m.metadata.get("statements")), json.dumps(m.metadata.get("statement_origins"))])


class ConfidentialityTests(unittest.IsolatedAsyncioTestCase):
    async def live(self, **kw: Any) -> ServiceHarness:
        h = await ServiceHarness(**kw).start()
        self.addAsyncCleanup(h.close)
        return h

    async def inline(self, **kw: Any) -> InlineHarness:
        h = await InlineHarness(**kw).start()
        self.addAsyncCleanup(h.close)
        return h

    @staticmethod
    def derived(store: Any, status: str | None = "active") -> list[Memory]:
        return [m for m in store.list_memories(ORG, status=status, limit=1_000_000) if m.operator != "agent_observation"]

    @staticmethod
    def current(h: ServiceHarness, unit: str, topic: str = TOPIC) -> Memory | None:
        return h.service.store.current_derived(ORG, "topic_consolidation", unit, topic)

    # ------------------------------------------------------------------ what a consolidation quotes
    async def test_derived_text_never_contains_agent_ids(self) -> None:
        h = await self.live()
        s, st = h.service, h.service.store
        agents = await org12(h)
        ids = [a for team in agents.values() for a in team]
        await s.upsert_rule(DEMO_RULE)
        for i, a in enumerate(ids):
            await note(h, a, f"Dock {i % 3} report {i}: the forklift queue is long.", f"x{i}",
                       visibility="org" if i % 2 else "team", confidence=0.5 + i / 40)
        await supply_evidence(h, agents)
        await note(h, agents["t2"][1], "Kessler confirms the SD-9 buffer is thin.", "S2", topic=SUPPLIER,
                   slot="supplier_buffer_low", entity="sd-9", visibility="org")
        forged = await note(h, agents["t4"][0], "A note that tries to set what consolidations quote.", "forged",
                            visibility="org", metadata={"statements": ["FORGED STATEMENT"], "statement_origins": ["org"],
                                                        "private_observations": 9, "note": "kept"})
        await h.settle()
        self.assertEqual(st.get_memory(forged).metadata, {"note": "kept"}, "agents cannot set what a consolidation quotes")
        rows = self.derived(st, status=None)
        layers = {m.layer for m in rows if m.operator == "topic_consolidation" and m.status == "active"}
        self.assertEqual(layers, {"team", "department", "subsidiary", "region", "enterprise"})
        self.assertEqual(len([m for m in rows if m.operator == "slot_composition" and m.status == "active"]), 1)
        self.assertGreater(len(rows), 20)
        for m in rows:
            with self.subTest(memory=m.memory_id, layer=m.layer, status=m.status):
                self.assertNotIn("zq7x", quoted(m), "no derived text or statement names an agent")
                self.assertNotIn("FORGED", quoted(m))
                self.assertLessEqual(len(m.text), MAX_DERIVED_TEXT)
        reader = h.principal("agent-zq7x-12")
        upper = [m for m in rows if m.layer in UPPER and reader.can_read(m)]
        self.assertEqual({m.layer for m in upper if m.status == "active"}, set(UPPER))
        for m in upper:
            with self.subTest(memory=m.memory_id, layer=m.layer, status=m.status):
                self.assertNotIn("zq7x", json.dumps(s.public_view(m, reader)), "nothing in the agent's view names an agent")

    async def test_team_private_text_never_rises_above_team(self) -> None:
        h = await self.live()
        s, st = h.service, h.service.store
        agents = await org12(h)
        markers = {t: f"MARKER-{t.upper()}-Q7" for t in TEAMS}
        for t in TEAMS:
            await note(h, agents[t][0], f"{markers[t]} pallets stuck at gate 2", f"{t}-team")
            await note(h, agents[t][1], f"Gate 2 queue reported in {t}", f"{t}-org", visibility="org")
        await h.settle()
        for t in TEAMS:
            with self.subTest(team=t):
                c = self.current(h, team_path(t))
                i = c.metadata["statements"].index(f"{markers[t]} pallets stuck at gate 2")
                self.assertEqual(c.metadata["statement_origins"][i], "team")
                self.assertIn(markers[t], c.text, "a team reads its own team-visibility notes in its consolidation")
                self.assertEqual(c.metadata["private_observations"], 1)
                for other in TEAMS:
                    if other != t:
                        self.assertNotIn(markers[other], quoted(c))

        def upper_is_clean(expected_private: dict[str, int]) -> None:
            for m in self.derived(st, status=None):
                if m.layer in UPPER:
                    for marker in markers.values():
                        self.assertNotIn(marker, quoted(m), f"{m.layer} {m.scope} quotes a team-visibility note")
                    self.assertNotIn("team", m.metadata.get("statement_origins") or [])
            for unit, n in expected_private.items():
                with self.subTest(unit=unit):
                    c = self.current(h, unit)
                    self.assertEqual(c.metadata["private_observations"], n)
                    self.assertIn(f"{n} team-private observations not quoted", c.text)

        upper_is_clean({D1: 2, D2: 2, SUB: 4, REGION: 4, ORG: 4})
        # another team's agent cannot find the marker anywhere in what a query gives it, lineage included
        res = s.query(h.principal(agents["t3"][0]), {"query": f"{markers['t1']} pallets stuck", "scope": ORG})
        self.assertIsNotNone(res["answer"])
        self.assertNotIn(markers["t1"], json.dumps({k: v for k, v in res.items() if k != "query"}))
        self.assertEqual([m.memory_id for m in st.list_memories(ORG, status=None, limit=100_000) if "consolidated at" in m.text], [])
        # skip-level contributions: d3 has two teams but only t5 says anything (its team consolidation feeds the subsidiary
        # directly), and d4's lone agent has no team consolidation (its raw team-visibility note feeds the subsidiary)
        for agent_id, team, department in (("agent-zq7x-13", "t5", "d3"), ("agent-zq7x-14", "t5", "d3"),
                                           ("agent-zq7x-15", "t6", "d3"), ("agent-zq7x-16", "t7", "d4")):
            await h.register(agent_id, team=team, department=department)
        markers["t5"], markers["t7"] = "MARKER-T5-Q7", "MARKER-T7-Q7"
        await note(h, "agent-zq7x-13", f"{markers['t5']} cold store at 9C", "t5-team")
        await note(h, "agent-zq7x-14", "Cold store alarm raised in t5", "t5-org", visibility="org")
        await note(h, "agent-zq7x-16", f"{markers['t7']} a lone observation", "t7-team")
        await h.settle()
        self.assertIsNone(self.current(h, f"{SUB}/d3"))
        self.assertIsNone(self.current(h, f"{SUB}/d4"))
        sub = self.current(h, SUB)
        parents = {(p.layer, p.scope) for p in (st.get_memory(e.parent_id) for e in st.parents_of(sub.memory_id))}
        self.assertLessEqual({("team", f"{SUB}/d3/t5"), ("agent", f"{SUB}/d4/t7/agent-zq7x-16")}, parents)
        self.assertIn("[d3] Cold store alarm raised in t5", sub.text)
        self.assertIn(f"{SUB.rsplit('/', 1)[-1]}': 4 department sources", sub.text)
        upper_is_clean({D1: 2, D2: 2, SUB: 6, REGION: 6, ORG: 6})

    async def test_org_visible_observations_reach_the_enterprise(self) -> None:
        h = await self.live()
        agents = await org12(h)
        org_text = {t: f"Forklift {i} is out of service in {t}" for i, t in enumerate(TEAMS, start=1)}
        for t in TEAMS:
            a, b, c = agents[t]
            await note(h, a, f"MARKER-{t} spare parts ordered", f"{t}-team")
            await note(h, b, org_text[t], f"{t}-org", visibility="org")
            await note(h, c, org_text[t], f"{t}-org-again", visibility="org", at="2026-10-01T09:05:00+00:00")
        await h.settle()
        d1 = self.current(h, D1)
        self.assertEqual(d1.text, f"{TOPIC} — department 'd1': 2 team sources, 6 agents, 2 team-private observations not quoted. "
                                  f"[t1] {org_text['t1']}; [t2] {org_text['t2']}")
        sub, region, ent = (self.current(h, u) for u in (SUB, REGION, ORG))
        self.assertEqual(sub.metadata["statements"], [org_text[t] for t in TEAMS])
        self.assertEqual((region.metadata["promoted_from"], ent.metadata["promoted_from"]), (sub.memory_id, region.memory_id))
        for c in (region, ent):
            self.assertEqual(c.metadata["statements"], sub.metadata["statements"], "a promotion keeps its child's statements")
            self.assertEqual(c.metadata["statement_origins"], ["org"] * 4)
        for t in TEAMS:
            self.assertIn(f"[emea] {org_text[t]}", ent.text)
        self.assertEqual(ent.text, f"{TOPIC} — enterprise 'northwind': 1 region source, 12 agents, 4 team-private observations "
                                   "not quoted. " + "; ".join(f"[emea] {org_text[t]}" for t in TEAMS))
        self.assertTrue(ent.text.split(" — ")[0] == TOPIC, "the live console reads the topic before ' — '")

    async def test_promotion_keeps_only_quotable_statements(self) -> None:
        h = await self.live()
        for i in (1, 2, 3):
            await h.register(f"solo-{i}", team="solo", department="lonely")
        # thirteen strong team-private notes do not crowd out one weak org-visible note at team level
        for i in range(13):
            await note(h, f"solo-{1 + i % 3}", f"PRIVATE-{i:02d} shift plan detail", f"p{i}", topic="ops:p", confidence=0.99)
        await note(h, "solo-2", "The night shift starts at 22:00", "org", topic="ops:p", visibility="org", confidence=0.3)
        await h.settle()
        team = self.current(h, f"{SUB}/lonely/solo", "ops:p")
        self.assertEqual(team.metadata["statements"][0], "The night shift starts at 22:00")
        self.assertEqual(team.metadata["statement_origins"], ["org"] + ["team"] * (MAX_STATEMENTS - 1))
        self.assertEqual(team.metadata["private_observations"], 13)
        self.assertTrue(team.text.endswith(" (+2 more)"), team.text)
        department = self.current(h, f"{SUB}/lonely", "ops:p")
        self.assertEqual(department.metadata["promoted_from"], team.memory_id)
        self.assertEqual(department.text, "ops:p — department 'lonely': 1 team source, 3 agents, 13 team-private observations "
                                          "not quoted. [solo] The night shift starts at 22:00")
        for unit in (f"{SUB}/lonely", SUB, REGION, ORG):
            with self.subTest(unit=unit):
                c = self.current(h, unit, "ops:p")
                self.assertEqual((c.metadata["statements"], c.metadata["statement_origins"], c.metadata["private_observations"]),
                                 (["The night shift starts at 22:00"], ["org"], 13))
                self.assertNotIn("PRIVATE-", c.text)

    async def test_identical_statements_are_quoted_once(self) -> None:
        h = await self.live()
        agents = await org12(h)
        a1, a2, _ = agents["t1"]
        b1, b2, b3 = agents["t2"]
        await note(h, a1, "Dock 4 is CLOSED until Friday", "dup-1", topic="ops:dup", visibility="org", confidence=0.9)
        await note(h, a2, "  dock 4\tis closed\n until   friday ", "dup-2", topic="ops:dup", visibility="org", confidence=0.6)
        # a team-visibility note reading exactly like an org-visible one, and stronger: the org-visible one is kept
        await note(h, b1, "Bay 7 flooded overnight", "dup-3", topic="ops:dup", confidence=0.95)
        await note(h, b2, "Bay 7 flooded overnight", "dup-4", topic="ops:dup", visibility="org", confidence=0.5)
        await note(h, b3, "DOCK 4 IS CLOSED UNTIL FRIDAY", "dup-5", topic="ops:dup", visibility="org", confidence=0.4)
        await h.settle()
        t1 = self.current(h, team_path("t1"), "ops:dup")
        self.assertEqual((t1.metadata["statements"], t1.support), (["Dock 4 is CLOSED until Friday"], 2), "one statement, two agents")
        self.assertEqual(t1.text, "ops:dup — team 't1': 2 agents. Dock 4 is CLOSED until Friday")
        t2 = self.current(h, team_path("t2"), "ops:dup")
        self.assertEqual((t2.metadata["statements"], t2.metadata["statement_origins"]),
                         (["Bay 7 flooded overnight", "DOCK 4 IS CLOSED UNTIL FRIDAY"], ["org", "org"]))
        self.assertEqual((t2.metadata["private_observations"], t2.support), (1, 3))
        d1 = self.current(h, D1, "ops:dup")
        self.assertEqual(d1.metadata["statements"], ["Dock 4 is CLOSED until Friday", "Bay 7 flooded overnight"],
                         "a statement already quoted for t1 is not quoted again for t2")
        self.assertEqual(d1.text, "ops:dup — department 'd1': 2 team sources, 5 agents, 1 team-private observation not quoted. "
                                  "[t1] Dock 4 is CLOSED until Friday; [t2] Bay 7 flooded overnight")

    async def test_clipping_is_by_code_point_and_collapses_whitespace(self) -> None:
        cases = {
            "emoji": "a" * 218 + "😀" * 5,
            "zwj sequence": "a" * 217 + "👨‍👩‍👧" + "b" * 10,
            "combining mark": "a" * 218 + "é" + "x" * 5,
            "space at the boundary": "a" * 218 + " " + "b" * 10,
            "astral only": "😀" * 300,
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                out = _clip(text)
                self.assertLessEqual(len(out), STATEMENT_CHARS)
                self.assertTrue(out.endswith("…"))
                self.assertEqual(out.encode("utf-8").decode("utf-8"), out, "valid UTF-8")
                self.assertFalse(out[:-1].endswith(" "))
                self.assertTrue(text.startswith(out[:-1]), "a prefix, cut between code points")
        self.assertEqual(_clip("a\n\tb   c\r\n"), "a b c")
        self.assertEqual(_clip("x" * STATEMENT_CHARS), "x" * STATEMENT_CHARS)
        # end to end
        h = await self.live()
        agents = await org12(h)
        long = "Freezer " + "❄️ temperature drift " * 30
        await note(h, agents["t1"][0], "Line one\n\tline two\r\nline three", "ws", visibility="org")
        await note(h, agents["t1"][1], long, "long", visibility="org")
        await h.settle()
        team = self.current(h, team_path("t1"))
        self.assertIn("Line one line two line three", team.metadata["statements"])
        self.assertIn(_clip(long), team.metadata["statements"])
        self.assertTrue(all(len(x) <= STATEMENT_CHARS for x in team.metadata["statements"]))
        for ch in "\n\t\r":
            self.assertNotIn(ch, team.text)

    async def test_raw_text_naming_an_agent_is_quoted_as_written(self) -> None:
        h = await self.live()
        agents = await org12(h)
        await note(h, agents["t1"][0], "agent-zq7x-3 says the dock is closed", "named", visibility="org", confidence=0.9)
        await note(h, agents["t1"][1], "The dock is closed, says the shift lead", "plain", visibility="org")
        await note(h, agents["t2"][0], "Dock closure confirmed in t2", "t2-a", visibility="org")
        await note(h, agents["t2"][1], "Dock closure confirmed again in t2", "t2-b", visibility="org")
        await h.settle()
        self.assertIn("agent-zq7x-3 says the dock is closed", self.current(h, team_path("t1")).metadata["statements"])
        d1 = self.current(h, D1)
        self.assertIn("[t1] agent-zq7x-3 says the dock is closed", d1.text, "producer text is not scrubbed")
        for producer in ("agent-zq7x-1", "agent-zq7x-2", "agent-zq7x-4", "agent-zq7x-5"):
            self.assertNotIn(producer, d1.text, "but no producer's id is added")

    async def test_redacted_literal_is_never_selected_by_rules(self) -> None:
        h = await self.live()
        s, st = h.service, h.service.store
        agents = await org12(h)
        await s.upsert_rule(DEMO_RULE)
        red = await note(h, agents["t1"][0], "[REDACTED]", "red", topic=TRANSPORT, slot="transport_disruption", entity="sd-9",
                         confidence=0.99)
        sup = await note(h, agents["t2"][0], "Two weeks of SD-9 stock left.", "sup", topic=SUPPLIER, slot="supplier_buffer_low",
                         entity="sd-9")
        dem = await note(h, agents["t3"][0], "Helios committed to 40 RX-4 arms.", "dem", topic=DEMAND, slot="demand_commitment",
                         entity="sd-9")
        await h.settle()
        conclusions = lambda: [m for m in self.derived(st) if m.operator == "slot_composition"]  # noqa: E731
        self.assertEqual(conclusions(), [], "three agents in three teams, but the transport slot is not filled")
        rule = st.get_applied_rule(DEMO_RULE["rule_id"])
        self.assertIsNone(build_conclusion(rule, ORG, ORG, "sd-9", [st.get_memory(x) for x in (red, sup, dem)],
                                           version_of=None, now=AT), "the pure builder never selects it either")
        real = await note(h, agents["t1"][1], "Port strike at terminal 3.", "real", topic=TRANSPORT, slot="transport_disruption",
                          entity="sd-9", confidence=0.5)
        await h.settle()
        [c] = conclusions()
        parents = {e.parent_id for e in st.parents_of(c.memory_id)}
        self.assertEqual(parents, {real, sup, dem}, "the weaker real note is selected over the literal")
        self.assertNotIn("[REDACTED]", c.text)
        self.assertIn("Port strike at terminal 3.", c.text)
        team = self.current(h, team_path("t1"), TRANSPORT)
        self.assertIn("[REDACTED]", team.metadata["statements"], "the consolidation quotes it verbatim")

    async def test_rule_templates_publish_quoted_evidence_at_their_layer(self) -> None:
        h = await self.live()
        s, st = h.service, h.service.store
        agents = await org12(h)
        await s.upsert_rule({"rule_id": "dept_hazard", "target_layer": "department", "required_slots": ["hazard"],
                             "min_agents": 1, "conclusion": "Hazard at {entity}: {slot:hazard}"})
        await note(h, agents["t1"][0], "MARKER-P3 forklift battery leaking", "h1", topic="hazard:bay", slot="hazard", entity="bay-1")
        await note(h, agents["t3"][0], "MARKER-P3B pallet rack bent", "h3", topic="hazard:bay", slot="hazard", entity="bay-1")
        await h.settle()
        c1 = st.current_derived(ORG, "slot_composition", D1, "dept_hazard:bay-1")
        self.assertEqual(c1.text, "Hazard at bay-1: MARKER-P3 forklift battery leaking",
                         "a template quoting {slot:...} publishes a team-visibility note at the rule's layer")
        for unit in (SUB, REGION, ORG):
            with self.subTest(unit=unit):
                c = self.current(h, unit, "dept_hazard")
                self.assertIn("MARKER-P3 forklift battery leaking", c.text, "and above it, through consolidations of its topic")
                self.assertEqual(set(c.metadata["statement_origins"]), {"rule"})
        # consolidations of the note's own topic still never quote it above team
        own = self.current(h, SUB, "hazard:bay")
        self.assertEqual(own.text, "hazard:bay — subsidiary 'nw-gmbh': 2 department sources, 2 agents, "
                                   "2 team-private observations not quoted.")

    async def test_derived_text_is_bounded_for_wide_units(self) -> None:
        t0 = time.perf_counter()
        h = await self.inline()
        st = h.service.store
        n = 0
        filler = " rack inventory count differs from the system by several pallets on aisle seven" * 5
        for sub in ("sub-a", "sub-b"):
            for department in ("dep-1", "dep-2"):
                for team in range(5):
                    for _ in range(10):
                        n += 1
                        await h.register(f"w{n:03d}", team=f"t{team}", department=department, subsidiary=sub)
                        await note(h, f"w{n:03d}", (f"Note {n:03d}:" + filler)[:300], f"w{n}", topic="wide:t", visibility="org")
        await h.settle()
        rows = self.derived(st, status=None)
        layers = {m.layer for m in rows if m.status == "active"}
        self.assertEqual(layers, {"team", "department", "subsidiary", "region", "enterprise"})
        more = {layer: 0 for layer in layers}
        for m in rows:
            with self.subTest(memory=m.memory_id, layer=m.layer, status=m.status):
                self.assertLessEqual(len(m.text), MAX_DERIVED_TEXT)
                statements = m.metadata["statements"]
                self.assertLessEqual(len(statements), MAX_STATEMENTS)
                self.assertEqual(len(statements), len(m.metadata["statement_origins"]))
                self.assertTrue(statements, "at least one statement always fits")
                self.assertTrue(all(len(x) <= STATEMENT_CHARS for x in statements))
                hidden = offered_at(st, m) - len(statements)
                self.assertGreaterEqual(hidden, 0)
                if hidden:
                    self.assertTrue(m.text.endswith(f" (+{hidden} more)"), m.text[-40:])
                    more[m.layer] += 1
                else:
                    self.assertNotIn(" more)", m.text)
        self.assertTrue(more["team"] and more["department"] and more["subsidiary"] and more["region"], more)
        # pure: one team of 200 agents, a conclusion quoting 30 placeholders, and the worst-case head
        team = "northwind/emea/nw-gmbh/ops/wide"
        notes = [raw(f"mem_{i:04d}", f"{team}/a{i}", f"{i:03d} " + "y" * 300) for i in range(200)]
        m = build_consolidation(ORG, team, "wide:t", {x.scope: [x] for x in notes}, promotion=False, effective_min_support=2,
                                registered_child_units=200, version_of=None, min_support=2, now=AT)
        self.assertLessEqual(len(m.text), MAX_DERIVED_TEXT)
        self.assertEqual(m.metadata["private_observations"], 0)
        self.assertTrue(m.text.endswith(f" (+{200 - len(m.metadata['statements'])} more)"))
        out = render_conclusion("Risk for {entity}: " + " / ".join(["{slot:a}"] * 30), "sd-9", {"a": "z" * 300})
        self.assertLessEqual(len(out), MAX_DERIVED_TEXT)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(render_conclusion("Risk\n\n  for {entity}: {slot:a}", "sd-9", {"a": "x"}), "Risk\n\n  for sd-9: x",
                         "a template's own whitespace is kept")
        seg = lambda c: c * 64  # noqa: E731
        enterprise = seg("e")
        children = {f"{enterprise}/{seg(chr(97 + i))}": [
            consolidated(f"mem_c{i:02d}", f"{enterprise}/{seg(chr(97 + i))}", "region",
                         [clip220(f"{i:02d}-{j} " + "s" * 300) for j in range(12)], ["org"] * 12, private=99_999)]
            for i in range(20)}
        worst = consolidation_statements(enterprise, "t" * 200, children, 99_999)
        self.assertLessEqual(len(worst.text), MAX_DERIVED_TEXT)
        self.assertTrue(worst.statements, "the head leaves room for at least one statement")
        self.assertTrue(worst.text.endswith(f" (+{worst.offered - len(worst.statements)} more)"))
        team_path_64 = "/".join(seg(c) for c in "abcde")
        wide_team = {f"{team_path_64}/a{i}": [raw(f"mem_{i}", f"{team_path_64}/a{i}", f"{i:03d} " + "q" * 300)] for i in range(30)}
        worst_team = consolidation_statements(team_path_64, "t" * 200, wide_team, 30)
        self.assertLessEqual(len(worst_team.text), MAX_DERIVED_TEXT)
        self.assertTrue(worst_team.statements)
        print(f"\n[200-agent bounded-text test: {time.perf_counter() - t0:.1f} s, {len(rows)} derived rows]", flush=True)

    async def test_statements_are_deterministic_and_rebuild_identically(self) -> None:
        h = await self.inline()
        s, st = h.service, h.service.store
        agents = await org12(h)
        await s.upsert_rule(DEMO_RULE)
        evidence = await supply_evidence(h, agents)
        for t in TEAMS:
            await note(h, agents[t][0], f"Gate 2 jammed in {t}", f"{t}-a", visibility="org")
            await note(h, agents[t][1], f"GATE 2 JAMMED IN {t.upper()}", f"{t}-b", visibility="org")
            await note(h, agents[t][2], f"Team-only remark from {t}", f"{t}-c")
            await h.settle()
        await note(h, agents["t3"][0], "Early note from t3", "t3-early", topic="ops:late", visibility="org")
        await note(h, agents["t3"][1], "Second note from t3", "t3-second", topic="ops:late")
        await h.settle()
        third = await note(h, agents["t3"][2], "Late note from t3", "t3-late", topic="ops:late")
        await h.settle()
        await s.retract(h.principal(agents["t3"][2]), third, "withdrawn")       # the first coalition is reactivated
        await s.retract(h.principal(agents["t1"][0]), evidence["A"], "called off")  # the conclusion is re-derived
        await h.settle()
        history = self.derived(st, status=None)
        self.assertTrue(any(m.metadata.get("reactivated_at") for m in history), "the history has a reactivation")
        self.assertTrue(any(m.status == "retracted" for m in history), "and retractions")
        rebuilt = await rebuild(h.log, h.tmp.name)
        self.addAsyncCleanup(rebuilt.store.close)
        self.assertEqual(rebuild_differences(s, rebuilt), [])
        live_rows, rebuilt_rows = memory_history(st), memory_history(rebuilt.store)
        for mid, row in live_rows.items():
            self.assertEqual(row[4:], rebuilt_rows[mid][4:], f"text and statements of {mid}")

        # the same notes applied forward and reversed: the same active derived memories, texts and statements
        plan: list[tuple[str, str, str, dict[str, Any]]] = []
        for i, t in enumerate(TEAMS):
            a, b, c = agents[t]
            plan += [(a, f"Gate {i} jammed", f"{t}-1", {"visibility": "org", "confidence": 0.7}),
                     (b, f"GATE {i} JAMMED", f"{t}-2", {"visibility": "org", "confidence": 0.7}),
                     (c, f"Remark {i}", f"{t}-3", {"confidence": 0.9, "at": "2026-10-01T08:00:00+00:00"})]
        plan += [(agents["t1"][0], "Port strike.", "tr", {"topic": TRANSPORT, "slot": "transport_disruption", "entity": "sd-9"}),
                 (agents["t2"][0], "Thin buffer.", "su", {"topic": SUPPLIER, "slot": "supplier_buffer_low", "entity": "sd-9"}),
                 (agents["t3"][0], "Committed.", "de", {"topic": DEMAND, "slot": "demand_commitment", "entity": "sd-9"})]

        async def apply(order: list[tuple[str, str, str, dict[str, Any]]]) -> dict[str, tuple]:
            g = await self.inline()
            await org12(g)
            await g.service.upsert_rule(DEMO_RULE)
            for agent_id, text, key, fields in order:
                await note(g, agent_id, text, key, **fields)
                await g.settle()
            return {m.memory_id: (m.text, m.metadata.get("statements"), m.metadata.get("statement_origins"),
                                  m.metadata.get("private_observations")) for m in self.derived(g.service.store)}

        forward, backward = await apply(plan), await apply(list(reversed(plan)))
        self.assertEqual(forward, backward)
        self.assertEqual(len([v for v in forward.values() if v[1] is None]), 1, "one conclusion, the rest consolidations")

        # a pure call does not depend on dict or list order
        rnd = random.Random(7)
        team = team_path("t1")
        team_parents = [raw(f"mem_{i}", f"{team}/a{i % 4}", text, visibility=vis, confidence=conf)
                        for i, (text, vis, conf) in enumerate([("Gate jammed", "org", 0.7), ("GATE JAMMED", "org", 0.7),
                                                               ("gate  jammed", "team", 0.9), ("Remark", "team", 0.5),
                                                               ("Other", "org", 0.5), ("Third", "org", 0.5)])]
        dept_parents = {f"{D1}/t1": [consolidated("mem_c1", f"{D1}/t1", "team", ["One", "Two", "Three"], ["org", "team", "org"],
                                                  private=1)],
                        f"{D1}/t2": [raw("mem_r1", f"{D1}/t2/b1", "Skip-level org note", confidence=0.6),
                                     raw("mem_r2", f"{D1}/t2/b2", "Skip-level team note", visibility="team", confidence=0.6),
                                     raw("mem_r3", f"{D1}/t2/b3", "one", confidence=0.6)]}
        team_contributions: dict[str, list[Memory]] = {}
        for p in team_parents:
            team_contributions.setdefault(p.scope, []).append(p)
        for unit, contributions in ((team, team_contributions), (D1, dept_parents)):
            ref = consolidation_statements(unit, TOPIC, contributions, 6)
            for _ in range(25):
                items = list(contributions.items())
                rnd.shuffle(items)
                shuffled = {k: rnd.sample(v, len(v)) for k, v in items}
                self.assertEqual(consolidation_statements(unit, TOPIC, shuffled, 6), ref)
        self.assertEqual(consolidation_statements(D1, TOPIC, dept_parents, 6).statements, ["One", "Three", "Skip-level org note"])

    # ------------------------------------------------------------------ text of memories that are not active
    async def test_inactive_text_withheld_for_other_principals(self) -> None:
        h = await self.live()
        s, st = h.service, h.service.store
        agents = await org12(h)
        await s.upsert_rule(DEMO_RULE)
        ids = await supply_evidence(h, agents)
        await h.settle()
        [c] = [m for m in self.derived(st) if m.operator == "slot_composition"]
        team = self.current(h, team_path("t1"), TRANSPORT)
        a_text = st.get_memory(ids["A"]).text
        self.assertIn(ids["A"], {e.parent_id for e in st.parents_of(c.memory_id)})
        await s.retract(h.principal(agents["t1"][0]), ids["A"], "strike called off")
        await h.settle()
        note_a, c_old, team_old = (st.get_memory(x) for x in (ids["A"], c.memory_id, team.memory_id))
        self.assertEqual({note_a.status, c_old.status, team_old.status}, {"retracted"})
        teammate, far = h.principal(agents["t1"][1]), h.principal("agent-zq7x-12")
        for p, mems in ((teammate, (note_a, c_old, team_old)), (far, (note_a, c_old))):
            listed = {m.memory_id: s.public_view(m, p) for m in s.list_memories(p, scope=ORG, status="retracted", limit=500)}
            self.assertTrue(listed)
            for v in listed.values():
                self.assertEqual((v["text"], v["text_withheld"]), ("", "retracted"))
                self.assertNotIn("statements", v["metadata"])
            for m in mems:
                with self.subTest(reader=p.id, memory=m.memory_id):
                    self.assertTrue(p.can_read(m) and not p.can_read_text(m))
                    v = s.public_view(m, p)
                    self.assertEqual((v["text"], v["text_withheld"], v["status"]), ("", "retracted", "retracted"))
                    self.assertNotIn("statements", v["metadata"])
                    self.assertNotIn("statement_origins", v["metadata"])
                    self.assertEqual(set(v) - {"text_withheld"}, set(m.to_dict()), "the shape is kept")
                    self.assertEqual(listed[m.memory_id], v)
        self.assertNotIn(team_old.memory_id, {m.memory_id for m in s.list_memories(far, scope=ORG, status="retracted", limit=500)},
                         "who may read a memory does not change")
        [c_new] = [m for m in self.derived(st) if m.operator == "slot_composition"]
        self.assertNotIn("text_withheld", s.public_view(c_new, far), "the key is absent when the text is shown")
        self.assertEqual(s.public_view(c_new, far)["text"], c_new.text)

        # a superseded team consolidation read by a teammate
        t3 = agents["t3"]
        await note(h, t3[0], "Cold store 3 at 9C", "cs-1", topic="ops:cold")
        await note(h, t3[1], "Cold store 3 alarm", "cs-2", topic="ops:cold")
        await h.settle()
        first = self.current(h, team_path("t3"), "ops:cold")
        late = await note(h, t3[2], "Cold store 3 door left open", "cs-3", topic="ops:cold")
        await h.settle()
        first = st.get_memory(first.memory_id)
        self.assertEqual(first.status, "superseded")
        reader = h.principal(t3[1])
        v = s.public_view(first, reader)
        self.assertEqual((v["text"], v["text_withheld"], v["superseded_by"]), ("", "superseded", first.superseded_by),
                         "the reader can still follow to the current version")
        g, ga = s.lineage(reader, first.memory_id), s.lineage(h.admin, first.memory_id)
        self.assertEqual((g["memory"]["text"], g["memory"]["text_withheld"]), ("", "superseded"))
        self.assertEqual(set(g["nodes"]), set(ga["nodes"]))
        self.assertEqual({(e["child"], e["parent"]) for e in g["edges"]}, {(e["child"], e["parent"]) for e in ga["edges"]})
        self.assertEqual(g["redacted_contributions"], ga["redacted_contributions"])
        for mid, n in g["nodes"].items():
            if mid != first.memory_id:
                self.assertEqual(n["text"], st.get_memory(mid).text, "active notes of the reader's own team")
                self.assertNotIn("text_withheld", n)

        # the lineage of the retracted conclusion for another team's agent: withheld and redacted nodes keep their shape
        g, ga = s.lineage(far, c_old.memory_id), s.lineage(h.admin, c_old.memory_id)
        self.assertEqual(set(g["nodes"]), set(ga["nodes"]))
        self.assertEqual({(e["child"], e["parent"]) for e in g["edges"]}, {(e["child"], e["parent"]) for e in ga["edges"]})
        self.assertEqual((g["memory"]["text"], g["memory"]["text_withheld"]), ("", "retracted"))
        self.assertEqual((g["nodes"][ids["A"]]["text"], g["nodes"][ids["A"]]["text_withheld"]), ("", "retracted"))
        redacted = [n for n in g["nodes"].values() if n["redacted"]]
        self.assertEqual(len(redacted), 2)
        self.assertTrue(all(n["text"] is None and "text_withheld" not in n for n in redacted))
        self.assertEqual(g["redacted_contributions"], len(redacted), "withheld nodes are not counted as redacted")
        for mid, n in g["nodes"].items():
            if not n["redacted"]:
                self.assertEqual(set(n) - {"text_withheld"}, set(ga["nodes"][mid]))
        self.assertNotIn(a_text, json.dumps(g))
        self.assertFalse(s._lineage_summary(far, c_old)["reconstructable"])

        # MCP, with the principal the HTTP middleware would set
        tools = MycelicTools(s)
        token = _principal.set(far)
        try:
            got = await tools.call("mycelic_get_memory", {"memory_id": c_old.memory_id})
            lin = await tools.call("mycelic_lineage", {"memory_id": c_old.memory_id})
        finally:
            _principal.reset(token)
        self.assertEqual((got["text"], got["text_withheld"]), ("", "retracted"))
        self.assertEqual((lin["nodes"][ids["A"]]["text"], lin["nodes"][ids["A"]]["text_withheld"]), ("", "retracted"))

        # reactivation: retracting the third note brings the first coalition back, readable again
        await s.retract(h.principal(t3[2]), late, "the door was closed")
        await h.settle()
        back = st.get_memory(first.memory_id)
        self.assertEqual(back.status, "active")
        v = s.public_view(back, reader)
        self.assertEqual((v["text"], v["metadata"]["statements"]), (back.text, back.metadata["statements"]))
        self.assertNotIn("text_withheld", v)
        self.assertNotIn("text_withheld", s.lineage(reader, back.memory_id)["memory"])

    async def test_owner_and_admin_still_read_inactive_text(self) -> None:
        h = await self.live()
        s, st = h.service, h.service.store
        agents = await org12(h)
        await s.upsert_rule(DEMO_RULE)
        ids = await supply_evidence(h, agents)
        await h.settle()
        [c] = [m for m in self.derived(st) if m.operator == "slot_composition"]
        team = self.current(h, team_path("t1"), TRANSPORT)
        owner = h.principal(agents["t1"][0])
        await s.retract(owner, ids["A"], "strike called off")
        await h.settle()
        a = st.get_memory(ids["A"])
        self.assertEqual(a.status, "retracted")
        self.assertEqual(s.public_view(a, owner), a.to_dict(), "the producer's view is what it always was")
        [listed] = [m for m in s.list_memories(owner, scope=ORG, status="retracted", limit=500) if m.memory_id == a.memory_id]
        self.assertEqual(s.public_view(listed, owner)["text"], a.text)
        g = s.lineage(owner, c.memory_id)
        self.assertEqual(g["nodes"][a.memory_id]["text"], a.text)
        self.assertNotIn("text_withheld", g["nodes"][a.memory_id])
        self.assertEqual((g["memory"]["text"], g["memory"]["text_withheld"]), ("", "retracted"), "a derived memory has no owner")
        for m in (st.get_memory(c.memory_id), st.get_memory(team.memory_id)):
            self.assertEqual((s.public_view(m, owner)["text"], s.public_view(m, owner)["text_withheld"]), ("", "retracted"))
        admin = h.admin
        for m in st.list_memories(ORG, status=None, limit=100_000):
            self.assertEqual(s.public_view(m, admin), m.to_dict())
        ga = s.lineage(admin, c.memory_id)
        self.assertEqual(ga, reconstruct(st, c.memory_id, visible=admin.can_read, full=admin.owns),
                         "the administrator's lineage is what it was before text could be withheld")
        self.assertTrue(all(n["text"] == st.get_memory(mid).text for mid, n in ga["nodes"].items()))

    def test_can_read_text_rule(self) -> None:
        def agent(agent_id: str, team: str, org: str = ORG) -> Principal:
            return Principal.for_agent(Agent(agent_id=agent_id, org_id=org, display_name=agent_id, path=f"{team}/{agent_id}",
                                             scopes=["memory:read"], key_prefix=""))

        t1 = team_path("t1")
        readers = {"admin": Principal.admin(), "owner": agent("own", t1), "teammate": agent("mate", t1),
                   "other team": agent("far", team_path("t2")), "other org": agent("acme-1", "acme/acme/acme/acme/t", "acme")}
        for status in MEMORY_STATUS:
            memories = {
                "team note": raw("mem_t", f"{t1}/own", "team note", visibility="team", status=status),
                "org note": raw("mem_o", f"{t1}/own", "org note", status=status),
                "department memory": consolidated("mem_d", D1, "department", [], [], status=status),
            }
            for what, m in memories.items():
                active = status == "active"
                expected = {"admin": True,
                            "owner": True if m.layer == "agent" else active,
                            "teammate": active,
                            "other team": active and what != "team note",
                            "other org": False}
                for name, p in readers.items():
                    with self.subTest(reader=name, memory=what, status=status):
                        self.assertEqual(p.can_read_text(m), expected[name])
                        if expected[name]:
                            self.assertTrue(p.can_read(m), "text is never readable where the memory is not")

    async def test_rule_snapshot_not_exposed(self) -> None:
        h = await self.live()
        s, st = h.service, h.service.store
        agents = await org12(h)
        await s.upsert_rule(DEMO_RULE)
        await supply_evidence(h, agents)
        await h.settle()
        [c] = [m for m in self.derived(st) if m.operator == "slot_composition"]
        rule = st.get_applied_rule(DEMO_RULE["rule_id"])
        view = s.public_view(c, h.principal("agent-zq7x-12"))
        self.assertEqual(view["metadata"]["derivation"], {"v": DERIVATION_VERSION, "rule_digest": rule_digest(rule)})
        self.assertNotIn("contributing_agents", view["metadata"])
        self.assertNotIn("evidence", view["metadata"])
        self.assertEqual(s.public_view(c, h.admin)["metadata"]["derivation"]["rule"], rule_snapshot(rule))

    # ------------------------------------------------------------------ upgrade
    async def test_rows_from_the_previous_derivation_version_are_replaced(self) -> None:
        with mock.patch.object(aggregation, "DERIVATION_VERSION", 1), mock.patch.object(service_module, "DERIVATION_VERSION", 1):
            h = await self.live()
            s = h.service
            agents = await org12(h)
            await s.upsert_rule(DEMO_RULE)
            for t in TEAMS:
                await note(h, agents[t][0], f"MARKER-{t} team only", f"{t}-team")
                await note(h, agents[t][1], f"Gate 2 queue in {t}", f"{t}-org", visibility="org")
            await supply_evidence(h, agents)
            await h.settle()
        st = s.store
        self.assertEqual(st.get_meta("derivation_version"), "1")
        old = self.derived(st)
        self.assertTrue(old and all(m.metadata["derivation"]["v"] == 1 for m in old))
        # what the previous release stored: texts that name agents, and no statements
        async with st.transaction() as tx:
            tx.c.execute("UPDATE memories SET text = 'legacy [agent-zq7x-1] quoted MARKER-t1 team only in ' || memory_id, "
                         "metadata = json_remove(metadata, '$.statements', '$.statement_origins', '$.private_observations') "
                         "WHERE operator != 'agent_observation'")
        legacy = {m.memory_id: m for m in self.derived(st, status=None)}
        await s.close()
        s2 = MycelicService(settings(h.tmp.name), transport=s.transport, metrics=Metrics())
        self.addAsyncCleanup(s2.close)
        await s2.start()
        self.assertEqual(s2._reaggregation["reason"], "derivation_version")
        self.assertTrue(await s2.wait_idle(30))
        self.assertEqual(s2._reaggregation["state"], "done", s2._reaggregation)
        self.assertEqual(s2.store.get_meta("derivation_version"), str(DERIVATION_VERSION))
        active = self.derived(s2.store)
        self.assertEqual(len(active), len(old))
        for m in active:
            with self.subTest(memory=m.memory_id):
                self.assertNotIn(m.memory_id, legacy)
                self.assertEqual(m.metadata["derivation"]["v"], DERIVATION_VERSION)
                self.assertNotIn("zq7x", m.text)
                if m.operator == "topic_consolidation":
                    self.assertIn("statements", m.metadata)
                    if m.layer != "team":
                        self.assertNotIn("MARKER", quoted(m))
        admin = s2.authenticate(f"Bearer {h.settings.admin_token}")
        reader = s2.authenticate(f"Bearer {h.keys[agents['t1'][1]]}")
        readable = 0
        for mid, m in legacy.items():
            with self.subTest(legacy=mid):
                now = s2.store.get_memory(mid)
                self.assertIn(now.status, ("superseded", "retracted"))
                self.assertEqual(s2.public_view(now, admin)["text"], m.text, "kept as history for administrators")
                if reader.can_read(now):
                    readable += 1
                    view = s2.public_view(now, reader)
                    self.assertEqual((view["text"], view["text_withheld"]), ("", now.status))
        self.assertGreater(readable, 0)
        self.assertEqual(invariant_violations(s2, ORG), [])


if __name__ == "__main__":
    unittest.main()

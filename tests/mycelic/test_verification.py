"""Downward verification (``verification.py``, ``MycelicService.verify``): a fresh conclusion and a multi-hop strategy
verify; every kind of tampering fails with its code on its node and never blames a child for its parent; status flips,
retractions in flight, rule and MIN_SUPPORT changes and old leaves are stale; missing keys, events, digests, legacy rows,
truncated walks and replays are unverifiable; promotions, diamonds, '*' rules and foreign parents; a 5,000-parent fan-in
within budget; randomized runs and their rebuilds; redaction; authorisation; determinism; audit and metrics; and an
engine that only reads and waits for open transactions."""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sqlite3
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

from mycelic import verification
from mycelic.auth import Principal
from mycelic.integrity import Keyring, canonical
from mycelic.models import Rule, now_iso, parse_iso, rule_digest, rule_snapshot, utcnow
from mycelic.service import Forbidden, MycelicService, NotFound, ValidationError
from mycelic.store import MycelicStore, row_memory
from mycelic.verification import DISCLOSED, HIDDEN, REASONS

from .helpers import (
    DEMO_RULE, V3_SCHEMA, HeldTransport, ServiceHarness, drain_outbox, full_reaggregation_pass, pump, rebuild,
)
from .test_aggregation_invariants import ORGS, Sequence
from .test_integrity import boot, node, shut
from .test_store_migration import V3_ROWS
from .test_strategic import ENTITY, REGIONAL, STRATEGIC

K = "verification-signing-key-0123456789abcdef"
KEY_A = "verification-key-a-0123456789abcdef01234"
KEY_B = "verification-key-b-fedcba9876543210fedcb"
ORG = "northwind"
TRANSPORT = "supply:sd-9/transport"
T0 = "2026-01-01T00:00:00+00:00"
DEMO_TEXTS = {
    "log-1": "Port of Rotterdam terminal 3 strike announced for weeks 41-43; SD-9 servo drive shipments route through it.",
    "log-2": "Carrier ETA for the SD-9 servo drive container slipped by 12 days.",
    "proc-1": "Kessler Antriebe confirms roughly two weeks of SD-9 inventory left.",
    "sales-1": "Helios Automation committed to 40 RX-4 arms for November; every RX-4 uses an SD-9 drive.",
}
#: the codes (f) gives a derived memory: none of them may land on a child whose parent failed its integrity check
DERIVATION_CODES = ("parent_outside_unit", "topic_mismatch", "parent_ineligible", "slot_uncovered", "below_min_support",
                    "below_min_agents", "below_min_teams", "below_min_units", "rule_snapshot_mismatch", "id_mismatch",
                    "text_mismatch", "confidence_mismatch", "content_mismatch", "not_derivable", "legacy_derivation")


class World:
    """A node over ``<tmp>/mycelic.db`` applied inline (``helpers.pump``: no background loops, every state settled), with
    the same entry points as ``ServiceHarness`` so a scenario builds on either."""

    def __init__(self, tmp: str, *, key: str | None = K, previous: tuple[str, ...] = (), **overrides: Any) -> None:
        self.tmp = tmp
        self.service = node(tmp, key=key, previous=previous, **overrides)
        self.log: list[dict[str, Any]] = []
        self.keys: dict[str, str] = {}
        self._principals: dict[str, Principal] = {}

    @property
    def store(self) -> MycelicStore:
        return self.service.store

    @property
    def admin(self) -> Principal:
        return Principal.admin()

    async def register(self, agent_id: str, *, team: str, department: str = "ops", subsidiary: str = "nw-gmbh",
                       region: str = "emea", enterprise: str = ORG, scopes: list[str] | None = None) -> None:
        body: dict[str, Any] = {"enterprise": enterprise, "region": region, "subsidiary": subsidiary, "department": department,
                                "team": team, "agent_id": agent_id}
        if scopes is not None:
            body["scopes"] = scopes
        _, self.keys[agent_id] = await self.service.register_agent(body)

    def principal(self, agent_id: str) -> Principal:
        if agent_id not in self._principals:       # authenticate() schedules a last-seen touch: once per agent
            self._principals[agent_id] = self.service.authenticate(f"Bearer {self.keys[agent_id]}")
        return self._principals[agent_id]

    async def observe(self, agent_id: str, text: str, **fields: Any) -> str:
        m, _ = await self.service.ingest_memory(self.principal(agent_id), {"text": text, **fields})
        return m.memory_id

    async def settle(self) -> None:
        await pump(self.service, self.log)

    def engine(self, memory_id: str, principal: Principal | None = None, **kwargs: Any) -> dict[str, Any]:
        """The engine's report, without the service's audit row (so it can run inside a transaction the test rolls back)."""
        kwargs.setdefault("now", utcnow())
        return verification.verify(self.store, self.service.aggregator.planner(), self.service.keyring, memory_id,
                                   principal=principal or self.admin, max_nodes=self.service.settings.verify_max_nodes,
                                   **kwargs).report

    def tampered(self, memory_id: str, edits: list[Any], principal: Principal | None = None, **kwargs: Any) -> dict[str, Any]:
        """The engine's report on ``memory_id`` with ``edits`` (``(sql, args)`` or a callable) applied, all rolled back."""
        c = self.store._conn
        c.execute("BEGIN")
        try:
            for edit in edits:
                if callable(edit):
                    edit()
                else:
                    sql, args = edit
                    changed = c.execute(sql, args).rowcount
                    assert changed >= 1, f"{sql} {args} changed nothing"
            return self.engine(memory_id, principal, **kwargs)
        finally:
            c.execute("ROLLBACK")

    async def close(self) -> None:
        await shut(self.service)


# ---------------------------------------------------------------------------------------------------------- scenarios
async def demo(x: Any) -> dict[str, str]:
    """DataPathTests' scenario: DEMO_RULE over logistics (log-1, log-2), procurement (proc-1) and field sales (sales-1,
    sales-2).  The note ids, the enterprise conclusion and the logistics team consolidation."""
    s = x.service
    await s.upsert_rule(DEMO_RULE)
    for a in ("log-1", "log-2"):
        await x.register(a, team="logistics")
    await x.register("proc-1", team="procurement")
    for a in ("sales-1", "sales-2"):
        await x.register(a, team="field-sales", department="commercial")
    await x.settle()
    ids = {
        "log-1": await x.observe("log-1", DEMO_TEXTS["log-1"], topic=TRANSPORT, slot="transport_disruption", entity="sd-9",
                                 confidence=0.9),
        "log-2": await x.observe("log-2", DEMO_TEXTS["log-2"], topic=TRANSPORT, slot="transport_disruption", entity="sd-9",
                                 confidence=0.7),
        "proc-1": await x.observe("proc-1", DEMO_TEXTS["proc-1"], topic="supply:sd-9/supplier", slot="supplier_buffer_low",
                                  entity="sd-9", confidence=0.85),
        "sales-1": await x.observe("sales-1", DEMO_TEXTS["sales-1"], topic="supply:sd-9/demand", slot="demand_commitment",
                                   entity="sd-9", confidence=0.8),
    }
    await x.settle()
    [conclusion] = s.store.list_memories(ORG, layers=["enterprise"])
    [team] = s.store.list_memories(ORG, layers=["team"])
    ids["conclusion"], ids["team"] = conclusion.memory_id, team.memory_id
    return ids


async def strategic(x: Any) -> dict[str, str]:
    """StrategicSynthesisTests' scenario: regional_supply_risk in EMEA and APAC and strategic_second_source above them."""
    s = x.service
    await s.upsert_rule(REGIONAL)
    await s.upsert_rule(STRATEGIC)
    for region, sub in (("emea", "nw-gmbh"), ("apac", "nw-kk")):
        for team, dept in (("logistics", "ops"), ("procurement", "ops"), ("field-sales", "commercial")):
            for i in (1, 2):
                await x.register(f"{region}-{team}-{i}", team=team, department=dept, subsidiary=sub, region=region)
    await x.register("hq-sourcing-1", team="sourcing", department="strategy", subsidiary="northwind-hq", region="hq")
    await x.register("hq-analytics-1", team="analytics", department="strategy", subsidiary="northwind-hq", region="hq")
    await x.settle()
    for region in ("emea", "apac"):
        port = "Rotterdam" if region == "emea" else "Busan"
        await x.observe(f"{region}-logistics-1", f"Port of {port} strike announced; SD-9 shipments route through it.",
                        topic="supply:sd-9/transport", slot="transport_disruption", entity=ENTITY, confidence=0.9)
        await x.observe(f"{region}-logistics-2", f"Carrier ETA for SD-9 containers via {port} slipped by 12 days.",
                        topic="supply:sd-9/transport", slot="transport_disruption", entity=ENTITY, confidence=0.7)
        await x.observe(f"{region}-procurement-1", "Kessler Antriebe confirms two weeks of SD-9 inventory left.",
                        topic="supply:sd-9/supplier", slot="supplier_buffer_low", entity=ENTITY, confidence=0.85)
        await x.observe(f"{region}-field-sales-1", "A customer committed to 40 RX-4 arms for November; every RX-4 uses an SD-9.",
                        topic="supply:sd-9/demand", slot="demand_commitment", entity=ENTITY, confidence=0.8)
    await x.observe("hq-analytics-1", "RX-4 order intake is up 40% year over year across all regions.",
                    topic="strategy:demand", slot="demand_growth", entity=ENTITY, confidence=0.8)
    await x.observe("hq-sourcing-1", "Kessler Antriebe is the only qualified SD-9 source for every subsidiary.",
                    topic="strategy:supply-base", slot="supplier_concentration", entity=ENTITY, confidence=0.95)
    await x.settle()
    st = s.store
    [strat] = [m for m in st.list_memories(ORG, layers=["enterprise"]) if m.rule_id == "strategic_second_source"]
    regional = {m.scope: m.memory_id for m in st.list_memories(ORG, layers=["region"]) if m.rule_id == "regional_supply_risk"}
    return {"strategic": strat.memory_id, "emea": regional["northwind/emea"], "apac": regional["northwind/apac"]}


# ---------------------------------------------------------------------------------------------------------- helpers
def view(report: dict[str, Any], memory_id: str) -> dict[str, Any]:
    return next(n for n in report["nodes"] if n["memory_id"] == memory_id)


def codes(report: dict[str, Any], memory_id: str) -> list[str]:
    return [r["code"] for r in view(report, memory_id)["reasons"]]


def detail(report: dict[str, Any], memory_id: str, code: str) -> dict[str, Any]:
    return next(r for r in view(report, memory_id)["reasons"] if r["code"] == code).get("detail", {})


def top(report: dict[str, Any]) -> list[str]:
    return [r["code"] for r in report["reasons"]]


def warning_codes(report: dict[str, Any]) -> list[tuple[str, str | None]]:
    return [(w["code"], w.get("memory_id")) for w in report["warnings"]]


def edit(memory_id: str, assignment: str, *args: Any) -> tuple[str, tuple]:
    return f"UPDATE memories SET {assignment} WHERE memory_id=?", (*args, memory_id)


def resign(store: MycelicStore, memory_id: str) -> None:
    """Sign a row as it is stored now, with its lineage edges, under the store's keyring: what an aggregation bug or the
    key holder would write."""
    r = store._conn.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
    digest, kid = store.keyring.sign(canonical(row_memory(r), [e.parent_id for e in store.parents_of(memory_id)]))
    store._conn.execute("UPDATE memories SET digest=?, digest_key_id=?, digest_origin='write' WHERE memory_id=?",
                        (digest, kid, memory_id))


def forge_rule(store: MycelicStore, memory_id: str, **changes: Any) -> None:
    """Rewrite a conclusion's stored derivation as if its rule had ``changes``, with a matching rule digest, and re-sign it."""
    m = store.get_memory(memory_id)
    derivation = m.metadata["derivation"]
    rule = Rule(**{**derivation["rule"], **changes})
    meta = {**m.metadata, "derivation": {**derivation, "rule": rule_snapshot(rule), "rule_digest": rule_digest(rule)}}
    store._conn.execute("UPDATE memories SET metadata=? WHERE memory_id=?", (json.dumps(meta), memory_id))
    resign(store, memory_id)


def retraction_event(memory_id: str, status: str) -> tuple[str, tuple]:
    """A ``memory.retracted`` event targeting ``memory_id`` in the given state, as SQL."""
    return ("INSERT INTO events(event_id, kind, org_id, subject, payload, status, created_at) "
            "VALUES (?, 'memory.retracted', ?, 'mycelic.northwind.memory.retracted', json_object('memory_id', ?), ?, ?)",
            (f"evt_test_{status}", ORG, memory_id, status, T0))


def event_of(store: MycelicStore, memory_id: str) -> str:
    return store.get_memory(memory_id).event_id


def ingested(store: MycelicStore, memory_id: str):
    return parse_iso(store.get_event(event_of(store, memory_id)).created_at)


def audit_rows(store: MycelicStore) -> list[dict[str, Any]]:
    return [a for a in store.recent_audit(100_000) if a["action"] == "memory.verify"]


def counter(metric: Any, *labels: str) -> float:
    return metric.labels(*labels)._value.get()


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    def tmpdir(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    async def world(self, tmp: str | None = None, **kwargs: Any) -> World:
        w = World(tmp or self.tmpdir(), **kwargs)
        self.addAsyncCleanup(w.close)              # before the directory goes (cleanups run last first)
        await boot(w.service)
        return w

    def assertVerified(self, report: dict[str, Any]) -> None:
        self.assertEqual((report["verdict"], report["derived_correctly"], report["still_true"], report["reasons"]),
                         ("verified", True, True, []), report)

    # ------------------------------------------------------------------------------------------------ verified
    async def test_fresh_conclusion_is_verified_with_lineage_node_count(self) -> None:
        for key, mode in ((K, "keyed"), (None, "unkeyed")):
            with self.subTest(mode=mode):
                w = await self.world(key=key)
                ids = await demo(w)
                s = w.service
                r = await s.verify(w.admin, ids["conclusion"])
                self.assertVerified(r)
                self.assertEqual(r["warnings"], [])
                self.assertEqual(r["summary"]["nodes"], len(s.lineage(w.admin, ids["conclusion"])["nodes"]))
                self.assertEqual(r["summary"], {"nodes": 4, "derived": 1, "leaves": 3, "redacted": 0, "failed_nodes": 0,
                                                "unverifiable_nodes": 0, "stale_nodes": 0, "unexplored": 0, "hidden_warnings": 0})
                self.assertEqual(r["integrity_mode"], mode)
                self.assertEqual(r["as_of"], {"last_applied_seq": int(w.store.get_meta("last_applied_seq"))})
                self.assertEqual((r["superseded_by"], r["current_version"], r["freshness_partial"], r["max_leaf_age"]),
                                 (None, None, False, None))
                self.assertEqual({n["memory_id"] for n in r["nodes"]}, {ids["conclusion"], ids["log-1"], ids["proc-1"], ids["sales-1"]})
                order = [(-["agent", "team", "department", "subsidiary", "region", "enterprise"].index(n["layer"]), n["memory_id"])
                         for n in r["nodes"]]
                self.assertEqual(order, sorted(order))
                self.assertEqual(r["nodes"][0]["memory_id"], ids["conclusion"])
                self.assertTrue(all(n["ok"] and not n["redacted"] and n["reasons"] == [] for n in r["nodes"]))
                self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", r["dag_digest"]) and re.fullmatch(r"[0-9a-f]{64}", r["report_digest"]))
                team = await s.verify(w.principal("log-1"), ids["team"])
                self.assertVerified(team)
                self.assertEqual((team["summary"]["nodes"], team["summary"]["leaves"]), (3, 2))
                self.assertNotIn("as_of", team, "administrators only")
                leaf = await s.verify(w.admin, ids["proc-1"])
                self.assertVerified(leaf)
                self.assertEqual((leaf["summary"]["nodes"], leaf["summary"]["leaves"], leaf["summary"]["derived"]), (1, 1, 0))
                # a raw note edited and re-hashed (unkeyed, anyone who can write the database can): the log still tells
                r = w.tampered(ids["conclusion"], [edit(ids["log-1"], "text=text||'!'"), lambda: resign(w.store, ids["log-1"])])
                self.assertEqual(r["verdict"], "failed")
                self.assertEqual(codes(r, ids["log-1"]), ["source_event_mismatch"])
                self.assertIn("text_mismatch", codes(r, ids["conclusion"]), "and the conclusion no longer quotes it")

    async def test_strategic_multi_hop_conclusion_verifies(self) -> None:
        w = await self.world()
        ids = await strategic(w)
        s = w.service
        for name in ("strategic", "emea", "apac"):
            with self.subTest(memory=name):
                self.assertVerified(await s.verify(w.admin, ids[name]))
        r = await s.verify(w.admin, ids["strategic"])
        self.assertEqual(r["summary"]["nodes"], len(s.lineage(w.admin, ids["strategic"])["nodes"]))
        self.assertEqual((r["summary"]["derived"], r["summary"]["leaves"]), (3, 8))
        self.assertEqual({n["layer"] for n in r["nodes"]}, {"enterprise", "region", "agent"})
        emea = w.principal("emea-field-sales-2")
        r = await s.verify(emea, ids["strategic"])
        self.assertVerified(r)
        apac = [n for n in r["nodes"] if n["scope"].startswith("northwind/apac")]
        self.assertEqual(len(apac), 4, "the APAC regional conclusion and its three leaves")
        for n in apac:
            self.assertEqual((n["redacted"], n["ok"], n["reasons"]), (True, True, []), n)
        self.assertFalse(view(r, ids["emea"])["redacted"])
        self.assertTrue(view(r, ids["apac"])["redacted"])
        self.assertEqual(r["summary"]["redacted"], sum(n["redacted"] for n in r["nodes"]))
        self.assertGreater(r["summary"]["redacted"], 4)

    # ------------------------------------------------------------------------------------------------ failed
    async def test_tampering_matrix(self) -> None:
        w = await self.world()
        ids = await demo(w)
        await w.register("acme-1", team="acme", department="acme", subsidiary="acme", region="acme", enterprise="acme")
        await w.settle()
        foreign = await w.observe("acme-1", "Acme's own note.", topic="supply:sd-9/transport", slot="transport_disruption",
                                  entity="sd-9", confidence=0.9)
        await w.settle()
        sw = await self.world()
        sids = await strategic(sw)
        st = w.store
        C, T, L1, L2, P1, S1 = ids["conclusion"], ids["team"], ids["log-1"], ids["log-2"], ids["proc-1"], ids["sales-1"]
        unkeyed_l1 = Keyring().sign(canonical(st.get_memory(L1)))[0]
        resign_c = lambda: resign(st, C)          # noqa: E731
        cases: list[tuple[str, World, str, list[Any], str, str]] = [
            ("leaf text", w, C, [edit(L1, "text=text||'!'")], L1, "integrity_mismatch"),
            ("leaf text (event)", w, C, [edit(L1, "text=text||'!'")], L1, "source_event_mismatch"),
            ("conclusion confidence", w, C, [edit(C, "confidence=0.5")], C, "integrity_mismatch"),
            ("conclusion confidence (recomputed)", w, C, [edit(C, "confidence=0.5")], C, "confidence_mismatch"),
            ("leaf re-hashed unkeyed", w, C, [edit(L1, "digest=?, digest_key_id='none'", unkeyed_l1)], L1, "integrity_downgraded"),
            ("random key id", w, C, [edit(L1, "digest_key_id='0123456789ab'")], L1, "integrity_unknown_key_forged"),
            ("digest nulled", w, C, [edit(L1, "digest=NULL")], L1, "integrity_missing"),
            ("parent row deleted", w, C, [("DELETE FROM memories WHERE memory_id=?", (P1,))], C, "missing_parent"),
            ("edge re-pointed", w, C, [("UPDATE lineage_edges SET parent_id=? WHERE child_id=? AND parent_id=?", (L2, C, L1))],
             C, "integrity_mismatch"),
            ("edge contributed_by", w, C, [("UPDATE lineage_edges SET contributed_by='log-2' WHERE child_id=? AND parent_id=?",
                                            (C, L1))], C, "edge_mismatch"),
            ("cycle", w, C, [("INSERT INTO lineage_edges VALUES (?, ?, 'mycelic', 'enterprise', ?)", (L1, C, T0))], L1,
             "cycle_detected"),
            ("self-loop", w, C, [("INSERT INTO lineage_edges VALUES (?, ?, 'mycelic', 'enterprise', ?)", (C, C, T0))], C,
             "cycle_detected"),
            ("parent edge on a raw leaf", w, C, [("INSERT INTO lineage_edges VALUES (?, ?, 'log-2', 'agent', ?)", (P1, L2, T0))],
             P1, "unexpected_parent"),
            ("event payload text", w, C, [("UPDATE events SET payload=json_set(payload, '$.text', 'forged') WHERE event_id=?",
                                           (event_of(st, L1),))], L1, "source_event_mismatch"),
            ("leaf metadata not an object", w, C, [edit(L1, "metadata='[1]'")], L1, "integrity_mismatch"),
            ("conclusion metadata not an object", w, C, [edit(C, "metadata='[1]'")], C, "integrity_mismatch"),
            ("leaf cites events that are not a list", w, C, [edit(L1, "source_event_ids='\"evt_x\"'")], L1, "integrity_mismatch"),
            ("producer's agent row deleted", w, C, [("DELETE FROM agents WHERE agent_id='log-1'", ())], L1, "producer_unregistered"),
            ("producer moved", w, C, [("UPDATE agents SET path=path||'-moved' WHERE agent_id='log-1'", ())], L1,
             "producer_unregistered"),
            # re-signed with the keyring: an aggregation bug or the key holder
            ("team edge removed, re-signed", w, T, [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?", (T, L2)),
                                                    lambda: resign(st, T)], T, "below_min_support"),
            ("regional parent removed, re-signed", sw, sids["strategic"],
             [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?", (sids["strategic"], sids["emea"])),
              lambda: resign(sw.store, sids["strategic"])], sids["strategic"], "below_min_units"),
            ("foreign-org parent, re-signed", w, C, [("INSERT INTO lineage_edges VALUES (?, ?, 'acme-1', 'agent', ?)",
                                                      (C, foreign, T0)), resign_c], C, "parent_outside_unit"),
            ("rule digest altered, re-signed", w, C,
             [edit(C, "metadata=json_set(metadata, '$.derivation.rule_digest', 'ffffffffffffffff')"), resign_c], C,
             "rule_snapshot_mismatch"),
            ("team topic, re-signed", w, T, [edit(T, "topic='supply:other'"), lambda: resign(st, T)], T, "topic_mismatch"),
            ("consolidation as a conclusion parent, re-signed", w, C,
             [("INSERT INTO lineage_edges VALUES (?, ?, 'mycelic', 'team', ?)", (C, T, T0)), resign_c], C, "parent_ineligible"),
            ("demand parent removed, re-signed", w, C, [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?", (C, S1)),
                                                        resign_c], C, "slot_uncovered"),
            ("demand parent removed, re-signed (agents)", w, C,
             [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?", (C, S1)), resign_c], C, "below_min_agents"),
            ("derivation forged with min_teams 5", w, C, [lambda: forge_rule(st, C, min_teams=5)], C, "below_min_teams"),
            ("derivation forged with another template", w, C, [lambda: forge_rule(st, C, conclusion="Forged for {entity}.")], C,
             "id_mismatch"),
            ("derivation forged with another template (text)", w, C,
             [lambda: forge_rule(st, C, conclusion="Forged for {entity}.")], C, "text_mismatch"),
            ("conclusion text, re-signed", w, C, [edit(C, "text='No risk.'"), resign_c], C, "text_mismatch"),
            ("conclusion support, re-signed", w, C, [edit(C, "support=support+1"), resign_c], C, "content_mismatch"),
            ("team roots, re-signed", w, T, [edit(T, "metadata=json_set(metadata, '$.roots', json('[]'))"), lambda: resign(st, T)], T,
             "content_mismatch"),
        ]
        for name, world, root, edits, at, code in cases:
            with self.subTest(case=name):
                r = world.tampered(root, edits)
                self.assertEqual(r["verdict"], "failed", r)
                self.assertFalse(r["derived_correctly"])
                self.assertIsNone(r["still_true"])
                self.assertIn(code, codes(r, at), r["nodes"])
                self.assertEqual(REASONS[code], "E")
                self.assertIn(code, top(r))
                self.assertFalse(view(r, at)["ok"])
                self.assertGreaterEqual(r["summary"]["failed_nodes"], 1)
        # the parent is blamed, never its child: a leaf whose integrity fails leaves its conclusion without a derivation code
        r = w.tampered(C, [edit(L1, "text=text||'!'")])
        self.assertEqual(codes(r, C), [])
        self.assertTrue(view(r, C)["ok"])
        r = w.tampered(C, [edit(L1, "confidence=0.99")])
        self.assertIn("integrity_mismatch", codes(r, L1))
        self.assertFalse(set(codes(r, C)) & set(DERIVATION_CODES), codes(r, C))
        # nor is the edge to it checked: the rewritten producer would not match the edge's contributed_by
        r = w.tampered(C, [edit(L1, "producer_id='log-2'")])
        self.assertIn("integrity_mismatch", codes(r, L1))
        self.assertEqual(codes(r, C), [])
        # an error explains its node: the planner, which no longer derives this conclusion, is not asked as well
        r = w.tampered(C, [("DELETE FROM memories WHERE memory_id=?", (P1,))])
        self.assertEqual(codes(r, C), ["missing_parent"])
        # the details name what is wrong: ids and field names, never text
        r = w.tampered(C, [("DELETE FROM memories WHERE memory_id=?", (P1,))])
        self.assertEqual(detail(r, C, "missing_parent"), {"parent_ids": [P1]})
        self.assertEqual(r["summary"]["nodes"], 3)
        r = w.tampered(C, [("UPDATE events SET payload=json_set(payload, '$.text', 'forged') WHERE event_id=?", (event_of(st, L1),))])
        self.assertEqual(detail(r, L1, "source_event_mismatch"), {"fields": ["text"]})
        r = w.tampered(C, [edit(C, "confidence=0.5")])
        self.assertEqual(detail(r, C, "confidence_mismatch"), {"stored": 0.5, "recomputed": st.get_memory(C).confidence})
        r = w.tampered(C, [edit(L1, "digest_key_id='0123456789ab'")])
        self.assertEqual(detail(r, L1, "integrity_unknown_key_forged"), {"key_id": "0123456789ab"})
        r = w.tampered(C, [edit(L1, "digest_key_id='0123456789ab'")], w.principal("log-2"))
        self.assertEqual(detail(r, L1, "integrity_unknown_key_forged"), {}, "key ids are for administrators")
        r = w.tampered(C, [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?", (C, S1)), resign_c])
        self.assertEqual(detail(r, C, "slot_uncovered"), {"slots": ["demand_commitment"]})
        self.assertEqual(detail(r, C, "below_min_agents"), {"count": 2, "required": 3})
        r = w.tampered(C, [lambda: forge_rule(st, C, min_teams=5)])
        self.assertEqual(detail(r, C, "below_min_teams"), {"count": 3, "required": 5})
        r = w.tampered(C, [edit(C, "support=support+1"), resign_c])
        self.assertEqual(detail(r, C, "content_mismatch"), {"fields": ["support"]})
        r = w.tampered(T, [edit(T, "metadata=json_set(metadata, '$.roots', json('[]'))"), lambda: resign(st, T)])
        self.assertEqual(detail(r, T, "content_mismatch"), {"fields": ["metadata.roots"]})
        r = w.tampered(C, [lambda: forge_rule(st, C, conclusion="Forged for {entity}.")])
        self.assertNotEqual(detail(r, C, "id_mismatch")["recomputed_id"], C)
        self.assertEqual(detail(r, C, "text_mismatch"), {}, "never the text")
        r = sw.tampered(sids["strategic"], [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?",
                                             (sids["strategic"], sids["emea"])), lambda: resign(sw.store, sids["strategic"])])
        self.assertEqual(detail(r, sids["strategic"], "below_min_units"), {"slot": "supply_risk", "layer": "region", "count": 1,
                                                                          "required": 2})
        # what is not a mismatch: a pre-v3 payload's labels as the agent wrote them, an unapplied note (a warning only)
        r = w.tampered(C, [("UPDATE events SET payload=json_set(payload, '$.topic', ' Supply:SD-9/Transport ', '$.slot', "
                            "'Transport_Disruption', '$.entity', ' SD-9') WHERE event_id=?", (event_of(st, L1),))])
        self.assertVerified(r)
        r = w.tampered(C, [edit(L1, "applied_at=NULL")])
        self.assertVerified(r)
        self.assertEqual(warning_codes(r), [("not_applied", L1)])
        # a payload that is not JSON at all, and labels that are not strings: a mismatch, never an exception
        r = w.tampered(C, [("UPDATE events SET payload='{not json' WHERE event_id=?", (event_of(st, L1),))])
        self.assertIn("source_event_mismatch", codes(r, L1))
        r = w.tampered(C, [("UPDATE events SET payload=json_set(payload, '$.topic', 5, '$.confidence', 'high') WHERE event_id=?",
                            (event_of(st, L1),))])
        self.assertEqual(detail(r, L1, "source_event_mismatch"), {"fields": ["confidence", "topic"]})
        # every edit was rolled back
        self.assertVerified(w.engine(C))
        self.assertVerified(sw.engine(sids["strategic"]))

    async def test_lifecycle_cross_check_detects_status_flips(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s = w.service
        await s.retract(w.principal("log-2"), ids["log-2"], "withdrawn")
        await w.settle()
        self.assertEqual(w.store.get_memory(ids["log-2"]).status, "retracted")
        r = w.engine(ids["log-2"])
        self.assertEqual((r["verdict"], codes(r, ids["log-2"])), ("stale", ["node_retracted"]), "a retraction the log applied")
        # a status set in the database breaks the row's digest (schema 6 signs the lifecycle) ...
        r = w.tampered(ids["log-2"], [edit(ids["log-2"], "status='active'")])
        self.assertEqual(r["verdict"], "failed")
        self.assertEqual(codes(r, ids["log-2"]), ["integrity_mismatch", "status_inconsistent"])
        # ... and one signed again as it stands (the key holder, or an aggregation bug) still disagrees with the log
        r = w.tampered(ids["log-2"], [edit(ids["log-2"], "status='active'"), lambda: resign(w.store, ids["log-2"])])
        self.assertEqual(r["verdict"], "failed")
        self.assertEqual(codes(r, ids["log-2"]), ["status_inconsistent"])
        r = w.tampered(ids["conclusion"], [edit(ids["proc-1"], "status='retracted'"), lambda: resign(w.store, ids["proc-1"])])
        self.assertEqual(r["verdict"], "failed")
        self.assertEqual(codes(r, ids["proc-1"]), ["status_inconsistent", "node_retracted"])
        r = w.tampered(ids["conclusion"], [retraction_event(ids["proc-1"], "applied")])
        self.assertEqual((r["verdict"], codes(r, ids["proc-1"])), ("failed", ["status_inconsistent"]),
                         "a retraction the log applied, and an active note")
        for status in ("pending", "published"):
            r = w.tampered(ids["conclusion"], [retraction_event(ids["proc-1"], status)])
            self.assertEqual((r["verdict"], codes(r, ids["proc-1"])), ("stale", ["retraction_pending"]))
        self.assertVerified(w.tampered(ids["conclusion"], [retraction_event(ids["proc-1"], "failed")]))
        corrupted = [("INSERT INTO events(event_id, kind, org_id, subject, payload, status, created_at) VALUES "
                      "('evt_bad_1', 'memory.retracted', ?, 'x', '{not json', 'applied', ?), "
                      "('evt_bad_2', 'memory.retracted', ?, 'x', '{\"memory_id\": 5}', 'pending', ?), "
                      "('evt_bad_3', 'memory.retracted', ?, 'x', '{}', 'pending', ?)", (ORG, T0) * 3)]
        self.assertVerified(w.tampered(ids["conclusion"], corrupted))
        self.assertEqual(w.store.lifecycle_events(ORG, verification.LIFECYCLE_KINDS),
                         {ids["log-2"]: [(e.event_id, "applied") for e in w.store.list_events(ORG, kind="memory.retracted")]})
        for status in ("superseded", "archived"):
            with self.subTest(status=status):
                r = w.tampered(ids["conclusion"], [edit(ids["proc-1"], "status=?", status), lambda: resign(w.store, ids["proc-1"])])
                self.assertEqual(r["verdict"], "failed")
                self.assertIn("status_inconsistent", codes(r, ids["proc-1"]))
        sw = await self.world()
        sids = await strategic(sw)
        r = sw.tampered(sids["strategic"], [edit(sids["emea"], "status='retracted'"), lambda: resign(sw.store, sids["emea"])])
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        self.assertEqual(codes(r, sids["emea"]), ["node_retracted"])
        self.assertEqual(codes(r, sids["strategic"]), ["not_current"])
        # what the planner derives now rests on the enterprise consolidation of both regions' conclusions instead
        self.assertNotIn(detail(r, sids["strategic"], "not_current")["planned_id"], (None, sids["strategic"]))

    async def test_a_retraction_undone_in_the_database_never_verifies(self) -> None:
        # someone who can write the database but lacks the signing key brings a retracted conclusion back: the note's
        # retraction event re-pointed elsewhere, the note and the conclusion set active again
        w = await self.world()
        ids = await demo(w)
        s, st, c = w.service, w.store, ids["conclusion"]
        self.assertTrue(s.keyring.keyed)
        await s.retract(w.principal("sales-1"), ids["sales-1"], "order cancelled")
        await w.settle()
        self.assertEqual((st.get_memory(c).status, st.get_memory(ids["sales-1"]).status), ("retracted", "retracted"))
        self.assertEqual((await s.verify(w.admin, c))["verdict"], "stale")
        st._conn.execute("UPDATE events SET payload=json_set(payload, '$.memory_id', 'mem_nothing') "
                         "WHERE kind='memory.retracted' AND json_extract(payload, '$.memory_id')=?", (ids["sales-1"],))
        st._conn.execute(*edit(ids["sales-1"], "status='active'"))
        st._conn.execute(*edit(c, "status='active', superseded_by=NULL"))
        for who, principal in (("admin", w.admin), ("sales-2", w.principal("sales-2"))):
            with self.subTest(viewer=who):
                r = await s.verify(principal, c)
                self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("failed", False, None))
        r = await s.verify(w.admin, c)
        self.assertEqual(codes(r, c)[0], "integrity_mismatch")
        self.assertEqual(codes(r, ids["sales-1"])[0], "integrity_mismatch")

    async def test_a_rollback_to_signed_states_is_what_security_md_says_and_a_rebuild_undoes_it(self) -> None:
        """What SECURITY.md §3 and §6 state: rows set back, digest columns included, to states the key signed earlier,
        with the retraction's event row deleted or marked failed, verify, because nothing in the database can tell them
        from the state they restore; the stream still holds the retraction, so a rebuild from it brings it back."""
        for variant in ("deleted", "failed"):
            with self.subTest(variant=variant):
                w = await self.world()
                ids = await demo(w)
                s, st, c, note = w.service, w.store, ids["conclusion"], ids["sales-1"]
                copy = {mid: tuple(st._conn.execute("SELECT digest, digest_key_id, digest_origin FROM memories "
                                                    "WHERE memory_id=?", (mid,)).fetchone()) for mid in (c, note)}
                await s.retract(w.principal("sales-1"), note, "order cancelled")
                await w.settle()
                self.assertEqual((await s.verify(w.admin, c))["verdict"], "stale")
                retraction = ("FROM events WHERE kind='memory.retracted' AND json_extract(payload, '$.memory_id')=?", (note,))
                if variant == "deleted":
                    st._conn.execute("DELETE " + retraction[0], retraction[1])
                else:
                    st._conn.execute("UPDATE events SET status='failed' WHERE event_id IN (SELECT event_id " + retraction[0] + ")",
                                     retraction[1])
                for mid, (digest, key_id, origin) in copy.items():
                    st._conn.execute(*edit(mid, "status='active', superseded_by=NULL, digest=?, digest_key_id=?, digest_origin=?",
                                           digest, key_id, origin))
                r = await s.verify(w.principal("sales-2"), c)
                self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"], r["reasons"]),
                                 ("verified", True, True, []), "a rollback the database cannot tell (SECURITY.md §6)")
                rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
                try:
                    self.assertEqual((rebuilt.store.get_memory(note).status, rebuilt.store.get_memory(c).status),
                                     ("retracted", "retracted"), "the stream still holds the retraction")
                finally:
                    await rebuilt.store.close()
        security = " ".join((Path(__file__).resolve().parents[2] / "SECURITY.md").read_text(encoding="utf-8").split())
        self.assertNotIn("so an edit by anyone who does not hold the key is detected,", security)
        self.assertIn("A rollback is not: a row set back, digest columns included, to an earlier state the key did sign", security)
        self.assertIn("rebuilding the database from the stream", security)

    async def test_retraction_pending_then_settled(self) -> None:
        transport = HeldTransport(hold=False)
        h = await ServiceHarness(transport=transport, event_signing_key=K).start()
        self.addAsyncCleanup(h.close)
        ids = await demo(h)
        s = h.service
        old = ids["conclusion"]
        self.assertVerified(await s.verify(h.admin, old))
        transport.hold = True
        await s.retract(h.principal("log-1"), ids["log-1"], "strike called off")
        await drain_outbox(s)
        r = await s.verify(h.admin, old)
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False), r)
        self.assertEqual(codes(r, ids["log-1"]), ["retraction_pending"])
        self.assertEqual(top(r), ["retraction_pending"])
        self.assertIn(("log_lag", None), warning_codes(r))
        self.assertEqual(next(w for w in r["warnings"] if w["code"] == "log_lag")["detail"], {"unapplied_events": 1})
        self.assertNotIn("log_lag", json.dumps(await s.verify(h.principal("sales-1"), old)), "administrators only")
        transport.hold = False
        await h.settle()
        replacement = s.store.current_derived(ORG, "slot_composition", ORG, s.store.get_memory(old).metadata["agg_key"])
        self.assertIsNotNone(replacement)
        r = await s.verify(h.admin, old)
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        self.assertEqual(codes(r, old), ["node_retracted"])
        self.assertEqual(codes(r, ids["log-1"]), ["node_retracted"])
        self.assertIsNone(r["superseded_by"], "a retraction never sets superseded_by")
        self.assertEqual(r["current_version"], replacement.memory_id)
        self.assertEqual(r["warnings"], [])
        self.assertVerified(await s.verify(h.admin, replacement.memory_id))
        self.assertIsNone((await s.verify(h.admin, replacement.memory_id))["current_version"])
        await h.register("log-3", team="logistics")
        await h.observe("log-3", "Terminal 3 strike confirmed again by the port authority.", topic=TRANSPORT,
                        slot="transport_disruption", entity="sd-9", confidence=0.95)
        await h.settle()
        newest = s.store.current_derived(ORG, "slot_composition", ORG, replacement.metadata["agg_key"])
        self.assertNotEqual(newest.memory_id, replacement.memory_id)
        r = await s.verify(h.admin, replacement.memory_id)
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        self.assertEqual(codes(r, replacement.memory_id), ["node_superseded"])
        self.assertEqual((r["superseded_by"], r["current_version"]), (newest.memory_id, newest.memory_id))
        self.assertVerified(await s.verify(h.admin, newest.memory_id))

    async def test_rule_and_min_support_changes_are_stale(self) -> None:
        w = await self.world()
        ids = await demo(w)
        C = ids["conclusion"]
        rule_id = DEMO_RULE["rule_id"]
        cases = [
            ("rule_deleted", [("DELETE FROM applied_rules WHERE rule_id=?", (rule_id,))], {}),
            ("rule_disabled", [("UPDATE applied_rules SET snapshot=json_set(snapshot, '$.enabled', json('false')) WHERE rule_id=?",
                                (rule_id,))], {}),
            ("rule_changed", [("UPDATE applied_rules SET snapshot=json_set(snapshot, '$.conclusion', 'Edited for {entity}.') "
                               "WHERE rule_id=?", (rule_id,))], {"rule_id": rule_id}),
            ("rule_changed", [("UPDATE applied_rules SET snapshot=json_set(snapshot, '$.target_layer', 'region') WHERE rule_id=?",
                               (rule_id,))], {"rule_id": rule_id}),
            ("rule_changed", [("UPDATE applied_rules SET snapshot=json_set(snapshot, '$.org_id', 'acme') WHERE rule_id=?",
                               (rule_id,))], {"rule_id": rule_id}),
        ]
        for code, edits, want in cases:
            with self.subTest(code=code, edit=edits[0][0]):
                r = w.tampered(C, edits)
                self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
                self.assertEqual(codes(r, C), [code], "exactly the matching code, and no not_current alongside")
                self.assertEqual(top(r), [code])
                self.assertEqual(detail(r, C, code), want)
        # metadata, enabled-and-on-again and org_id None do not change what a rule derives
        r = w.tampered(C, [("UPDATE applied_rules SET snapshot=json_set(snapshot, '$.metadata', json('{\"owner\": \"x\"}')) "
                            "WHERE rule_id=?", (rule_id,))])
        self.assertVerified(r)
        await w.close()
        reopened = World(w.tmp, min_support=3)
        self.addAsyncCleanup(reopened.close)
        await boot(reopened.service)
        r = await reopened.service.verify(reopened.admin, ids["team"])
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        self.assertEqual(codes(r, ids["team"]), ["min_support_changed"])
        self.assertEqual(detail(r, ids["team"], "min_support_changed"), {"derived_under": 2, "configured": 3})
        self.assertNotIn("not_current", json.dumps(r))
        self.assertVerified(await reopened.service.verify(reopened.admin, C))

    async def test_leaf_freshness_uses_server_time_and_hides_hidden_leaves(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, st, C = w.service, w.store, ids["conclusion"]
        leaves = (ids["log-1"], ids["proc-1"], ids["sales-1"])
        latest = max(ingested(st, mid) for mid in leaves)
        r = await s.verify(w.admin, C, max_leaf_age=1, now=latest + timedelta(seconds=3))
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        for mid in leaves:
            self.assertEqual(codes(r, mid), ["leaf_stale"])
            self.assertEqual(detail(r, mid, "leaf_stale")["max_leaf_age"], 1)
            self.assertGreaterEqual(detail(r, mid, "leaf_stale")["age_seconds"], 3)
        self.assertEqual(r["reasons"], [{"code": "leaf_stale", "severity": "S", "count": 3}])
        self.assertEqual((r["freshness_partial"], r["max_leaf_age"]), (False, 1))
        self.assertVerified(await s.verify(w.admin, C, max_leaf_age=3600, now=latest + timedelta(seconds=3)))
        # the producer's observed_at is never used: only when the server ingested the note
        old = await w.observe("sales-1", "Recalled a 2020 shipment delay.", topic="ops:history", observed_at="2020-01-01T00:00:00+00:00")
        await w.settle()
        self.assertEqual(st.get_memory(old).created_at, "2020-01-01T00:00:00+00:00")
        r = await s.verify(w.admin, old, max_leaf_age=3600, now=ingested(st, old) + timedelta(seconds=5))
        self.assertVerified(r)
        # every leaf is judged whoever asks, so every viewer gets the same verdict: a leaf the caller may not read shows
        # hidden_stale, without its age, and the answer is not partial
        r = await s.verify(w.principal("sales-2"), C, max_leaf_age=1, now=latest + timedelta(seconds=3))
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("stale", True, False))
        self.assertEqual(codes(r, ids["sales-1"]), ["leaf_stale"])
        for mid in (ids["log-1"], ids["proc-1"]):
            self.assertEqual((view(r, mid)["redacted"], view(r, mid)["reasons"]), (True, [{"code": "hidden_stale", "severity": "S"}]))
        self.assertFalse(r["freshness_partial"])
        self.assertEqual(r["reasons"], [{"code": "hidden_stale", "severity": "S", "count": 2},
                                        {"code": "leaf_stale", "severity": "S", "count": 1}])
        # a reader above every contributing team, who may read none of the leaves, gets the administrator's verdict
        await w.register("hq-1", team="board", department="strategy")
        r = await s.verify(w.principal("hq-1"), C, max_leaf_age=1, now=latest + timedelta(seconds=3))
        self.assertEqual((r["verdict"], r["still_true"], r["freshness_partial"], r["summary"]["redacted"]),
                         ("stale", False, False, 3))
        self.assertEqual(r["reasons"], [{"code": "hidden_stale", "severity": "S", "count": 3}])
        self.assertVerified(await s.verify(w.principal("hq-1"), C, max_leaf_age=3600, now=latest + timedelta(seconds=3)))
        for bad in (0, -1, True, 1.5, "3", verification.MAX_LEAF_AGE_SECONDS + 1):
            with self.subTest(max_leaf_age=bad):
                with self.assertRaises(ValidationError):
                    await s.verify(w.admin, C, max_leaf_age=bad)
        self.assertVerified(await s.verify(w.admin, C, max_leaf_age=verification.MAX_LEAF_AGE_SECONDS))

    # ------------------------------------------------------------------------------------------------ unverifiable
    async def test_unverifiable_cases(self) -> None:
        # a previous key dropped, then listed again
        tmp = self.tmpdir()
        w = await self.world(tmp, key=KEY_A)
        ids = await demo(w)
        C = ids["conclusion"]
        await w.close()
        rotated = await self.world(tmp, key=KEY_B)
        r = await rotated.service.verify(rotated.admin, C)
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("unverifiable", None, None))
        self.assertEqual(top(r), ["integrity_unknown_key"])
        self.assertEqual({tuple(codes(r, n["memory_id"])) for n in r["nodes"]}, {("integrity_unknown_key",)})
        await rotated.close()
        listed = await self.world(tmp, key=KEY_B, previous=(KEY_A,))
        self.assertVerified(await listed.service.verify(listed.admin, C))
        await listed.close()

        # a schema-3 database with a legacy derived row, after the backfill
        legacy_tmp = self.tmpdir()
        c = sqlite3.connect(os.path.join(legacy_tmp, "mycelic.db"))
        c.executescript(V3_SCHEMA + V3_ROWS)
        c.close()
        legacy = await self.world(legacy_tmp)
        r = await legacy.service.verify(legacy.admin, "mem_d1")
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("unverifiable", None, None))
        self.assertEqual(codes(r, "mem_d1"), ["legacy_derivation"])
        self.assertEqual(detail(r, "mem_d1", "legacy_derivation"), {"version": None})
        self.assertEqual(codes(r, "mem_a"), ["cited_event_missing", "source_event_missing"])
        self.assertEqual(codes(r, "mem_b"), ["source_event_missing"])
        self.assertEqual(detail(r, "mem_a", "cited_event_missing"), {"event_ids": ["evt_x"]})
        self.assertEqual(sorted(warning_codes(r)), [("integrity_backfilled", m) for m in ("mem_a", "mem_b", "mem_d1")])
        self.assertNotIn("not_current", json.dumps(r))

        # the leaf's event row deleted; a digest nulled before the backfill completed; a replay in progress
        w = await self.world()
        ids = await demo(w)
        C, L1 = ids["conclusion"], ids["log-1"]
        cases = [
            ([("DELETE FROM events WHERE event_id=?", (event_of(w.store, L1),))], L1, "source_event_missing"),
            ([edit(L1, "digest=NULL"), ("DELETE FROM meta WHERE key='integrity_backfill_complete'", ())], L1,
             "integrity_not_backfilled"),
            ([("INSERT INTO meta(key, value) VALUES ('replay_target_seq', '999')", ())], None, "replay_in_progress"),
        ]
        for edits, at, code in cases:
            with self.subTest(code=code):
                r = w.tampered(C, edits)
                self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("unverifiable", None, None), r)
                self.assertEqual(top(r), [code])
                if at is not None:
                    self.assertEqual(codes(r, at), [code])
        # a retraction signed as such (the lifecycle is covered: schema 6) whose event row is gone
        r = w.tampered(C, [("DELETE FROM events WHERE event_id=?", (event_of(w.store, L1),)), edit(L1, "status='retracted'"),
                           lambda: resign(w.store, L1)])
        self.assertEqual(codes(r, L1), ["source_event_missing", "node_retracted"], "a pruned log: the status is not judged")
        self.assertEqual(detail(r, L1, "source_event_missing"), {"event_id": event_of(w.store, L1)})
        # retractions are read from the first leaf event on (a retraction follows its note); with a leaf's own row pruned
        # there is no such bound, so one logged before every other leaf's event still counts
        early = [retraction_event(L1, "pending"), ("UPDATE events SET rid=0 WHERE event_id='evt_test_pending'", ())]
        r = w.tampered(C, [("DELETE FROM events WHERE event_id=?", (event_of(w.store, L1),)), *early])
        self.assertEqual(codes(r, L1), ["source_event_missing", "retraction_pending"])

        # a walk cut at verify_max_nodes
        sw = await self.world(verify_max_nodes=10)
        sids = await strategic(sw)
        r = await sw.service.verify(sw.admin, sids["strategic"])
        self.assertEqual((r["verdict"], r["derived_correctly"], r["still_true"]), ("unverifiable", None, None))
        self.assertEqual(top(r), ["walk_truncated"])
        self.assertEqual(r["reasons"], [{"code": "walk_truncated", "severity": "U", "count": 1}])
        self.assertEqual(r["summary"]["nodes"], 10)
        self.assertGreater(r["summary"]["unexplored"], 0)
        again = await sw.service.verify(sw.admin, sids["strategic"])
        self.assertEqual((again["report_digest"], again["dag_digest"]), (r["report_digest"], r["dag_digest"]), "the same cut")
        self.assertEqual([n["memory_id"] for n in again["nodes"]], [n["memory_id"] for n in r["nodes"]])
        r = await sw.service.verify(sw.admin, sids["strategic"], max_leaf_age=3600)
        self.assertTrue(r["freshness_partial"])

    # ------------------------------------------------------------------------------------------------ shapes
    async def test_edge_shapes_promotion_diamond_wildcard_cross_org(self) -> None:
        # one team in an organization: every unit above it promotes the team's consolidation
        w = await self.world()
        for a in ("a1", "a2"):
            await w.register(a, team="t1", department="solo", subsidiary="solo", region="solo", enterprise="solo")
        await w.settle()
        n1 = await w.observe("a1", "Dock 4 crane is down.", topic="ops:cranes", confidence=0.8)
        n2 = await w.observe("a2", "Crane repair needs two weeks.", topic="ops:cranes", confidence=0.7)
        await w.settle()
        chain = {m.layer: m for m in w.store.list_memories("solo", operator="topic_consolidation")}
        self.assertEqual(set(chain), {"team", "department", "subsidiary", "region", "enterprise"})
        r = await w.service.verify(w.admin, chain["enterprise"].memory_id)
        self.assertVerified(r)
        self.assertEqual((r["summary"]["nodes"], r["summary"]["derived"], r["summary"]["leaves"]), (7, 5, 2))
        dept, team = chain["department"].memory_id, chain["team"].memory_id
        self.assertEqual(chain["department"].metadata["promoted_from"], team)
        over_raw = [("UPDATE lineage_edges SET parent_id=?, contributed_by='a1', parent_layer='agent' WHERE child_id=?", (n1, dept)),
                    lambda: resign(w.store, dept)]
        over_two = [("INSERT INTO lineage_edges VALUES (?, ?, 'a2', 'agent', ?)", (dept, n2, T0)), lambda: resign(w.store, dept)]
        for name, edits in (("over a raw parent", over_raw), ("over two parents", over_two)):
            with self.subTest(promotion=name):
                r = w.tampered(dept, edits)
                self.assertEqual(r["verdict"], "failed")
                self.assertIn("below_min_support", codes(r, dept))
                self.assertEqual(detail(r, dept, "below_min_support")["required"], 1)

        # a diamond: r_dept rests on r_team's conclusion and, directly, on one of the same notes
        d = await self.world()
        await d.service.upsert_rule({"rule_id": "r_team", "target_layer": "team", "required_slots": ["s1", "s2"], "min_agents": 2,
                                     "conclusion": "T {entity}: {slot:s1}", "emits_slot": "s3"})
        await d.service.upsert_rule({"rule_id": "r_dept", "target_layer": "department", "required_slots": ["s3", "s1"],
                                     "sources": ["slot_composition", "agent_observation"], "corroborate": True, "min_agents": 2,
                                     "conclusion": "D {entity}"})
        for a in ("b1", "b2"):
            await d.register(a, team="t1")
        await d.settle()
        x1 = await d.observe("b1", "First half of the picture.", slot="s1", entity="e1", confidence=0.9)
        x2 = await d.observe("b2", "Second half of the picture.", slot="s2", entity="e1", confidence=0.8)
        await d.settle()
        [ct] = [m for m in d.store.list_memories(ORG, operator="slot_composition") if m.rule_id == "r_team"]
        [cd] = [m for m in d.store.list_memories(ORG, operator="slot_composition") if m.rule_id == "r_dept"]
        self.assertEqual({e.parent_id for e in d.store.parents_of(cd.memory_id)}, {ct.memory_id, x1})
        self.assertEqual({e.parent_id for e in d.store.parents_of(ct.memory_id)}, {x1, x2})
        r = await d.service.verify(d.admin, cd.memory_id)
        self.assertVerified(r)
        self.assertEqual((r["summary"]["nodes"], r["summary"]["leaves"], r["summary"]["derived"]), (4, 2, 2))
        self.assertEqual(len(r["nodes"]), 4)
        latest = max(ingested(d.store, x1), ingested(d.store, x2))
        r = await d.service.verify(d.admin, cd.memory_id, max_leaf_age=1, now=latest + timedelta(seconds=3))
        self.assertEqual(r["reasons"], [{"code": "leaf_stale", "severity": "S", "count": 2}], "the shared leaf once")

        # '*': no entity check on mixed parents; a '*' whose selection all names entities is not derivable
        await d.service.upsert_rule({"rule_id": "r_star", "target_layer": "team", "required_slots": ["s4", "s5"], "min_agents": 2,
                                     "conclusion": "Star: {slot:s4} / {slot:s5}"})
        y1 = await d.observe("b1", "No entity named here.", slot="s4", confidence=0.9)
        await d.observe("b2", "About e1.", slot="s5", entity="e1", confidence=0.8)
        await d.settle()
        [star] = [m for m in d.store.list_memories(ORG, operator="slot_composition") if m.rule_id == "r_star"]
        self.assertIsNone(star.entity)
        self.assertEqual({p.entity for p in d.store.get_memories([e.parent_id for e in d.store.parents_of(star.memory_id)]).values()},
                         {None, "e1"})
        self.assertVerified(await d.service.verify(d.admin, star.memory_id))
        named = [edit(y1, "entity='e1'"), lambda: resign(d.store, y1),
                 ("UPDATE events SET payload=json_set(payload, '$.entity', 'e1') WHERE event_id=?", (event_of(d.store, y1),))]
        r = d.tampered(star.memory_id, named)
        self.assertEqual(r["verdict"], "failed")
        self.assertEqual(codes(r, star.memory_id), ["not_derivable"])
        self.assertEqual(codes(r, y1), [])

        # a parent in another organization: parent_outside_unit; to an agent the foreign node is hidden
        f = await self.world()
        ids = await demo(f)
        await f.register("acme-1", team="acme", department="acme", subsidiary="acme", region="acme", enterprise="acme")
        await f.settle()
        foreign = await f.observe("acme-1", "Acme's own note.", topic=TRANSPORT, slot="transport_disruption", entity="sd-9")
        await f.settle()
        C = ids["conclusion"]
        edits = [("INSERT INTO lineage_edges VALUES (?, ?, 'acme-1', 'agent', ?)", (C, foreign, T0)), lambda: resign(f.store, C)]
        r = f.tampered(C, edits)
        self.assertIn("parent_outside_unit", codes(r, C))
        self.assertIn(foreign, detail(r, C, "parent_outside_unit")["parent_ids"])
        self.assertEqual(codes(r, foreign), [], "a foreign leaf skips the leaf checks: its child's code explains it")
        r = f.tampered(C, edits, f.principal("sales-2"))
        self.assertEqual(r["verdict"], "failed")
        self.assertTrue(view(r, foreign)["redacted"])
        self.assertEqual(view(r, foreign)["scope"], "acme/acme/acme/acme/acme")
        self.assertIn("parent_outside_unit", codes(r, C))

    async def test_promotion_of_two_children_under_min_support_3(self) -> None:
        # MIN_SUPPORT 3 and one department of two teams with three agents each: the department promotes both teams'
        # consolidations (fewer than 3 children), and every unit above it promotes the department's
        w = await self.world(min_support=3)
        for team in ("t1", "t2"):
            for i in range(3):
                await w.register(f"{team}-a{i}", team=team, department="ops", subsidiary="solo", region="solo", enterprise="solo")
        await w.settle()
        # the idempotency keys fix the note ids, and with them the team consolidations' ids (which also embed
        # DERIVATION_VERSION): these make the second team's id sort first, which the assertion below checks
        for team in ("t1", "t2"):
            for i in range(3):
                await w.observe(f"{team}-a{i}", f"Crane {i} of {team} is down.", topic="ops:cranes", confidence=0.6 + i / 10,
                                idempotency_key=f"note-{i}")
        await w.settle()
        chain = {m.layer: m for m in w.store.list_memories("solo", operator="topic_consolidation") if m.layer != "team"}
        teams = {m.scope.rsplit("/", 1)[1]: m.memory_id for m in w.store.list_memories("solo", layers=["team"])}
        dept = chain["department"]
        self.assertEqual(sorted(e.parent_id for e in w.store.parents_of(dept.memory_id)), sorted(teams.values()))
        self.assertLess(teams["t2"], teams["t1"], "the edges (in parent id order) list the second team first")
        self.assertEqual((dept.metadata["promoted_from"], dept.metadata["effective_min_support"]), (teams["t1"], 1),
                         "promoted from the first child unit")
        for layer in ("department", "subsidiary", "region", "enterprise"):
            with self.subTest(layer=layer):
                self.assertVerified(await w.service.verify(w.admin, chain[layer].memory_id))
        r = await w.service.verify(w.admin, chain["enterprise"].memory_id)
        self.assertEqual((r["summary"]["nodes"], r["summary"]["derived"], r["summary"]["leaves"]), (12, 6, 6))
        # a promotion is only valid while the unit has fewer registered children than min_support
        forged = [edit(dept.memory_id, "metadata=json_set(metadata, '$.registered_child_units', 3)"),
                  lambda: resign(w.store, dept.memory_id)]
        r = w.tampered(dept.memory_id, forged)
        self.assertEqual((r["verdict"], codes(r, dept.memory_id)), ("failed", ["below_min_support"]))
        # one child dropped and re-signed: a valid promotion of the other, but not this memory
        dropped = [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?", (dept.memory_id, teams["t1"])),
                   lambda: resign(w.store, dept.memory_id)]
        r = w.tampered(dept.memory_id, dropped)
        self.assertEqual(r["verdict"], "failed")
        self.assertIn("id_mismatch", codes(r, dept.memory_id))
        self.assertNotIn("below_min_support", codes(r, dept.memory_id))

    async def test_randomized_hierarchies_promote_and_verify(self) -> None:
        # sparse, deep and wide random trees under min_support 2 to 4: teams below the threshold, units of one child or
        # of several (which promote all their children's consolidations), retractions; every memory verifies
        multi = 0
        for seed in range(8):
            rnd = random.Random(seed)
            ms = 2 + seed % 3
            w = await self.world(key=K if seed % 2 else None, min_support=ms)
            agents: list[str] = []
            for region in [f"r{i}" for i in range(rnd.randint(1, 2))]:
                for sub in [f"s{i}" for i in range(rnd.randint(1, 2))]:
                    for dept in [f"d{i}" for i in range(rnd.randint(1, 3))]:
                        for team in [f"t{i}" for i in range(rnd.randint(1, 3))]:
                            for i in range(rnd.choice([ms - 1, ms, ms, ms + 1])):
                                agent = f"{region}-{sub}-{dept}-{team}-{i}"
                                await w.register(agent, team=team, department=dept, subsidiary=sub, region=region)
                                agents.append(agent)
            await w.settle()
            notes = []
            for agent in agents:
                for topic in ("ops:cranes", "supply:x"):
                    if rnd.random() < 0.85:
                        notes.append(await w.observe(agent, f"{topic} seen by {agent}.", topic=topic,
                                                     confidence=round(rnd.uniform(0.4, 0.95), 2)))
            await w.settle()
            for mid in rnd.sample(notes, min(len(notes), rnd.randint(0, 3))):
                await w.service.retract(w.admin, mid)
            await w.settle()
            st = w.store
            for m in st.list_memories(ORG, status=None, limit=100_000):
                parents = st.parents_of(m.memory_id)
                multi += m.metadata.get("promoted_from") is not None and len(parents) > 1
                with self.subTest(seed=seed, min_support=ms, memory=m.memory_id, layer=m.layer, status=m.status):
                    r = await w.service.verify(w.admin, m.memory_id)
                    self.assertEqual(r["verdict"], "verified" if m.status == "active" else "stale", r["nodes"])
                    if m.operator != "agent_observation":
                        self.assertIs(r["derived_correctly"], True)
        self.assertGreater(multi, 0, "some unit promoted several children's consolidations")

    async def test_wide_fan_in_verifies_within_budget(self) -> None:
        w = await self.world()
        s = w.service
        for j in range(10):
            await w.register(f"a{j}", team="t1", department="acme", subsidiary="acme", region="acme", enterprise="acme")
        await w.settle()
        for j in range(10):
            p = w.principal(f"a{j}")
            for n in range(500):
                await s.ingest_memory(p, {"text": f"note {n} by a{j}", "topic": "supply:x", "visibility": "org", "confidence": 0.6})
        now = now_iso()
        async with s.store.transaction() as tx:     # applied in one transaction, then aggregated by one pass
            for ev in s.store.pending_events(1_000_000):
                w.log.append({})
                tx.mark_applied(ev.event_id, len(w.log), now)
                tx.set_applied(ev.payload["memory_id"], now)
        await full_reaggregation_pass(s)
        await w.settle()
        [team] = s.store.list_memories("acme", layers=["team"])
        self.assertEqual(len(s.store.parents_of(team.memory_id)), 5000)
        [top_memory] = s.store.list_memories("acme", layers=["enterprise"])
        twin = s.aggregator.planner()
        self.assertIsNone(twin.on_event)
        self.assertIs(twin._cap_warned, s.aggregator._cap_warned, "the once-per-key cap warning is shared")
        self.assertEqual((twin.min_support, twin.max_candidates, twin.max_dependents, twin.clock),
                         (s.aggregator.min_support, s.aggregator.max_candidates, s.aggregator.max_dependents, s.aggregator.clock))
        truncated = s.metrics.aggregation_truncated.labels("candidates")._value.get()
        for _ in range(2):
            t0 = time.perf_counter()
            r = await s.verify(w.admin, top_memory.memory_id)
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 5.0)
            self.assertVerified(r)
            self.assertEqual((r["summary"]["nodes"], r["summary"]["leaves"], r["summary"]["derived"]), (5005, 5000, 5))
            self.assertLessEqual(r["summary"]["nodes"], s.settings.verify_max_nodes)
        self.assertEqual(s.metrics.aggregation_truncated.labels("candidates")._value.get(), truncated,
                         "currency planning through the quiet twin counts nothing")
        self.assertVerified(await s.verify(w.admin, team.memory_id))

    # ------------------------------------------------------------------------------------------------ randomized
    async def test_randomized_runs_verify_every_active_derived_memory(self) -> None:
        # min_support 1, 2 and 3 (where a unit of two children with consolidations promotes both), keyed and not
        for seed in range(int(os.environ.get("MYCELIC_VERIFY_SEEDS") or 6)):
            seq = VerifiedSequence(seed, settle_each=False, min_support=1 + seed % 3)
            bad = await seq.run()
            self.assertEqual(bad, [], seq.report(bad))
            self.assertGreater(seq.verified, 0)
            self.assertEqual(bool(seq.signing_key), seed % 2 == 1)

    # ------------------------------------------------------------------------------------------------ confidentiality
    async def test_redaction_never_leaks_hidden_content(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, st, C = w.service, w.store, ids["conclusion"]
        await s.revoke_agent("log-1")
        await w.settle()
        digests = [r["digest"] for r in st._conn.execute("SELECT digest FROM memories")]
        self.assertTrue(all(digests) and len(digests) >= 6)
        conclusion_text = st.get_memory(C).text
        hidden_events = [event_of(st, ids["log-1"]), event_of(st, ids["proc-1"])]
        agent_ids = ["log-1", "log-2", "proc-1", "sales-1", "sales-2"]

        def leaks(report: dict[str, Any], *, texts: list[str]) -> list[str]:
            dumped = json.dumps(report, ensure_ascii=False)
            found = [t for t in texts if t in dumped] + [d for d in digests if d in dumped]
            found += [n for n in ("contributing_agents", "digest_key_id", "digest_origin", '"statements":', '"text":', '"metadata":',
                                  '"producer_id":', '"digest":') if n in dumped]
            return found + [a for a in agent_ids if a in dumped]

        r = await s.verify(w.principal("sales-2"), C)
        self.assertVerified(r)
        self.assertEqual(leaks(r, texts=[DEMO_TEXTS["log-1"], DEMO_TEXTS["proc-1"], conclusion_text, *hidden_events,
                                         "Rotterdam", "Kessler"]), [])
        self.assertEqual({n["memory_id"] for n in r["nodes"]}, {C, ids["log-1"], ids["proc-1"], ids["sales-1"]})
        self.assertEqual({n["memory_id"]: n["layer"] for n in r["nodes"]},
                         {C: "enterprise", ids["log-1"]: "agent", ids["proc-1"]: "agent", ids["sales-1"]: "agent"})
        self.assertEqual(r["summary"]["hidden_warnings"], 1, "log-1 is revoked: counted, never named")
        self.assertEqual(r["warnings"], [])
        self.assertEqual(r["summary"]["redacted"], 2)
        self.assertNotIn("as_of", r)
        admin = await s.verify(w.admin, C)
        self.assertEqual(warning_codes(admin), [("producer_revoked", ids["log-1"])])
        self.assertEqual(leaks(admin, texts=[*DEMO_TEXTS.values(), conclusion_text]), [])
        self.assertEqual(admin["dag_digest"], r["dag_digest"])

        # tampered: a hidden node shows only what lineage discloses and hidden_* codes, never a detail
        edits = [edit(ids["log-1"], "text=text||'!'"), edit(ids["proc-1"], "status='retracted'"),
                 edit(ids["proc-1"], "digest_key_id='0123456789ab'")]
        r = w.tampered(C, edits, w.principal("sales-2"))
        self.assertEqual(r["verdict"], "failed")
        allowed = set(DISCLOSED) | set(HIDDEN.values())
        for mid in (ids["log-1"], ids["proc-1"]):
            n = view(r, mid)
            self.assertTrue(n["redacted"])
            self.assertTrue({x["code"] for x in n["reasons"]} <= allowed, n)
            self.assertFalse(any("detail" in x for x in n["reasons"]), n)
            self.assertFalse(n["ok"])
        self.assertEqual(codes(r, ids["log-1"]), ["hidden_error"], "integrity and event mismatch: one hidden_error")
        self.assertEqual(codes(r, ids["proc-1"]), ["hidden_error", "node_retracted"])
        self.assertEqual(r["summary"]["failed_nodes"], 2)
        self.assertEqual(leaks(r, texts=[DEMO_TEXTS["log-1"], DEMO_TEXTS["proc-1"], conclusion_text, *hidden_events,
                                         "0123456789ab"]), [])
        # the readable conclusion is not what the planner derives without the supplier note: said without naming it
        self.assertEqual(codes(r, C), ["not_current"])
        self.assertEqual({x["code"] for x in r["reasons"]}, {"hidden_error", "node_retracted", "not_current"})
        admin = w.tampered(C, edits)
        self.assertEqual(codes(admin, ids["proc-1"]), ["integrity_unknown_key_forged", "status_inconsistent", "node_retracted"])
        sales2 = w.principal("sales-2")
        r = w.tampered(C, [("DELETE FROM events WHERE event_id=?", (hidden_events[0],))], sales2)
        self.assertEqual((r["verdict"], codes(r, ids["log-1"])), ("unverifiable", ["hidden_unverifiable"]))
        self.assertEqual(r["reasons"], [{"code": "hidden_unverifiable", "severity": "U", "count": 1}])
        r = w.tampered(C, [retraction_event(ids["proc-1"], "pending")], sales2)
        self.assertEqual((r["verdict"], codes(r, ids["proc-1"])), ("stale", ["hidden_stale"]))
        self.assertEqual(leaks(r, texts=[*hidden_events, "evt_test_pending"]), [])
        # a disclosed code keeps its name on a hidden node but loses its detail (the parent ids it names)
        shape = [("INSERT INTO lineage_edges VALUES (?, 'mem_gone', 'mycelic', 'team', ?)", (ids["log-1"], T0)),
                 ("INSERT INTO lineage_edges VALUES (?, ?, 'mycelic', 'enterprise', ?)", (ids["proc-1"], C, T0))]
        admin = w.tampered(C, shape)
        self.assertEqual(detail(admin, ids["log-1"], "missing_parent"), {"parent_ids": ["mem_gone"]})
        self.assertEqual(detail(admin, ids["proc-1"], "cycle_detected"), {"parent_ids": [C]})
        r = w.tampered(C, shape, sales2)
        self.assertEqual(codes(r, ids["log-1"]), ["hidden_error", "missing_parent"])
        self.assertEqual(codes(r, ids["proc-1"]), ["cycle_detected", "hidden_error"])
        for mid in (ids["log-1"], ids["proc-1"]):
            self.assertFalse(any("detail" in x for x in view(r, mid)["reasons"]), view(r, mid))
        self.assertEqual(leaks(r, texts=[*hidden_events, "mem_gone"]), [])

        # an org-visible note is readable by another team: not redacted, with its details
        shared = await w.observe("proc-1", "Org-wide: the second Kessler plant reopens in March.", topic="ops:plants",
                                 visibility="org")
        await w.settle()
        r = await s.verify(sales2, shared)
        self.assertVerified(r)
        self.assertFalse(view(r, shared)["redacted"])
        r = w.tampered(shared, [("UPDATE events SET payload=json_set(payload, '$.text', 'forged') WHERE event_id=?",
                                 (event_of(st, shared),))], sales2)
        self.assertEqual(detail(r, shared, "source_event_mismatch"), {"fields": ["text"]})
        self.assertEqual(leaks(r, texts=["Kessler plant"]), [])

        # the longest text an agent may write: compared in full, never in the report
        huge = ("Huge note on the terminal 3 strike. " * 200)[:w.service.settings.max_text_chars]
        await w.observe("log-2", huge, topic=TRANSPORT, confidence=0.6)
        await w.settle()
        [team] = st.list_memories(ORG, layers=["team"])
        r = await s.verify(w.admin, team.memory_id)
        self.assertVerified(r)
        self.assertEqual(leaks(r, texts=[huge[:40], team.text]), [])
        r = w.tampered(team.memory_id, [("UPDATE memories SET text=substr(text, 1, length(text) - 1)||'.' WHERE text=?", (huge,))])
        self.assertEqual(r["verdict"], "failed")

        # readable inactive nodes: the details are never derived from the text the reader may not see
        await s.retract(w.principal("sales-1"), ids["sales-1"], "order cancelled")
        await w.settle()
        self.assertEqual(st.get_memory(C).status, "retracted")
        r = await s.verify(w.principal("sales-2"), C)
        self.assertEqual(r["verdict"], "stale")
        self.assertEqual(codes(r, C), ["node_retracted"])
        self.assertFalse(view(r, ids["sales-1"])["redacted"])
        self.assertEqual(leaks(r, texts=[DEMO_TEXTS["sales-1"], DEMO_TEXTS["log-1"], DEMO_TEXTS["proc-1"], conclusion_text,
                                         *hidden_events]), [])

    async def test_authorisation_errors(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s = w.service
        await w.register("reader", team="field-sales", department="commercial", scopes=["memory:read"])
        await w.register("acme-1", team="acme", department="acme", subsidiary="acme", region="acme", enterprise="acme")
        await w.settle()
        acme = await w.observe("acme-1", "Acme note.", topic="ops:x")
        await w.settle()
        before = len(audit_rows(w.store))
        with self.assertRaises(Forbidden):
            await s.verify(w.principal("reader"), ids["conclusion"])
        for who, mid in (("sales-2", ids["log-1"]), ("sales-2", acme), ("sales-2", "mem_unknown"), ("log-1", acme)):
            with self.subTest(who=who, memory=mid):
                with self.assertRaises(NotFound):
                    await s.verify(w.principal(who), mid)
        with self.assertRaises(NotFound):
            await s.verify(w.admin, "mem_unknown")
        with self.assertRaises(verification.VerificationNotFound):          # the engine's own guard
            w.engine("mem_unknown")
        for bad in ("a/b", "", "x" * 201, None, 5, "mem_1\n"):
            with self.subTest(memory_id=bad):
                with self.assertRaises(ValidationError):
                    await s.verify(w.admin, bad)                 # type: ignore[arg-type]
        self.assertEqual(len(audit_rows(w.store)), before, "refused calls are not audited")
        self.assertVerified(await s.verify(w.admin, ids["conclusion"]))
        self.assertVerified(await s.verify(w.admin, acme))
        self.assertVerified(await s.verify(w.principal("acme-1"), acme))
        # log_lag is scoped to the memory's organization and the deployment-wide '_', and derived events are not lag
        derived_pending = ("INSERT INTO events(event_id, kind, org_id, subject, payload, status, created_at) "
                           "VALUES ('evt_d_test', 'memory.derived', ?, 'x', '{}', 'pending', ?)", (ORG, T0))
        self.assertEqual(w.tampered(ids["conclusion"], [derived_pending])["warnings"], [])
        await w.observe("acme-1", "A second Acme note, not applied yet.", topic="ops:x")
        r = await s.verify(w.admin, ids["conclusion"])
        self.assertEqual(r["warnings"], [])
        r = await s.verify(w.admin, acme)
        self.assertEqual(r["warnings"], [{"code": "log_lag", "detail": {"unapplied_events": 1}}])
        await s.upsert_rule({"rule_id": "noop", "target_layer": "team", "required_slots": ["zz"], "conclusion": "noop"})
        self.assertEqual((await s.verify(w.admin, ids["conclusion"]))["warnings"],
                         [{"code": "log_lag", "detail": {"unapplied_events": 1}}])
        self.assertEqual((await s.verify(w.admin, acme))["warnings"], [{"code": "log_lag", "detail": {"unapplied_events": 2}}])
        self.assertNotIn("log_lag", json.dumps(await s.verify(w.principal("acme-1"), acme)))

    # ------------------------------------------------------------------------------------------------ determinism
    async def test_report_is_deterministic_and_survives_rebuild(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s = w.service
        await w.register("log-3", team="logistics")
        await w.settle()
        n3 = await w.observe("log-3", "Customs backlog of two weeks at Rotterdam.", topic=TRANSPORT, slot="transport_disruption",
                             entity="sd-9", confidence=0.6)
        await w.settle()
        await s.retract(w.principal("log-3"), n3, "duplicate")
        await w.settle()
        await w.observe("log-3", "Customs backlog confirmed by the port authority.", topic=TRANSPORT, confidence=0.65)
        await w.settle()
        first, second = await s.verify(w.admin, ids["conclusion"]), await s.verify(w.admin, ids["conclusion"])
        self.assertEqual((first["report_digest"], first["dag_digest"]), (second["report_digest"], second["dag_digest"]))
        self.assertEqual({k: v for k, v in first.items() if k != "verified_at"}, {k: v for k, v in second.items() if k != "verified_at"})
        sales = await s.verify(w.principal("sales-2"), ids["conclusion"])
        self.assertEqual(sales["dag_digest"], first["dag_digest"], "the DAG's shape is the same for every viewer")
        derived = [m for m in w.store.list_memories(ORG, status=None, limit=1000) if m.operator != "agent_observation"]
        self.assertTrue({"active", "superseded", "retracted"} <= {m.status for m in derived}, [m.status for m in derived])
        live = {m.memory_id: await s.verify(w.admin, m.memory_id) for m in derived}
        # the live rows re-signed by the start-up backfill (an upgrade from schema 3): warnings only, the same digests
        w.store._conn.execute("UPDATE memories SET digest=NULL, digest_key_id=NULL, digest_origin=NULL")
        w.store._conn.execute("DELETE FROM meta WHERE key='integrity_backfill_complete'")
        self.assertGreater(await s._backfill_integrity(), 0)
        backfilled = {mid: await s.verify(w.admin, mid) for mid in live}
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            for m in derived:
                again = await rebuilt.verify(Principal.admin(), m.memory_id)
                with self.subTest(memory=m.memory_id, status=m.status):
                    self.assertEqual((again["report_digest"], again["dag_digest"]),
                                     (live[m.memory_id]["report_digest"], live[m.memory_id]["dag_digest"]))
                    self.assertEqual((backfilled[m.memory_id]["report_digest"], backfilled[m.memory_id]["dag_digest"]),
                                     (again["report_digest"], again["dag_digest"]))
                    self.assertEqual(again["verdict"], "verified" if m.status == "active" else "stale")
                    self.assertFalse([x for x in again["warnings"] if x["code"] == "integrity_backfilled"])
                    self.assertTrue(backfilled[m.memory_id]["warnings"])
                    self.assertEqual({x["code"] for x in backfilled[m.memory_id]["warnings"]}, {"integrity_backfilled"})
        finally:
            await rebuilt.store.close()

    async def test_audit_row_and_metrics_per_call(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, C = w.service, ids["conclusion"]
        m = s.metrics
        rows, verified = len(audit_rows(w.store)), counter(m.verifications, "verified")
        r = await s.verify(w.admin, C, remote="10.0.0.9")
        new = audit_rows(w.store)
        self.assertEqual(len(new), rows + 1)
        self.assertEqual((new[0]["principal"], new[0]["target"], new[0]["remote"]), ("admin", C, "10.0.0.9"))
        self.assertEqual(new[0]["detail"], {"org_id": ORG, "verdict": "verified", "nodes": 4, "reasons": []})
        self.assertEqual(counter(m.verifications, "verified"), verified + 1)
        self.assertEqual(r["verdict"], "verified")
        for verdict in verification.VERDICTS:
            self.assertIn(f'mycelic_verifications_total{{verdict="{verdict}"}}', m.render()[0].decode())
        # reasons once per distinct code, whatever the number of nodes carrying it
        latest = max(ingested(w.store, mid) for mid in (ids["log-1"], ids["proc-1"], ids["sales-1"]))
        stale_before, leaf_stale = counter(m.verifications, "stale"), counter(m.verification_reasons, "leaf_stale")
        r = await s.verify(w.principal("sales-2"), C, max_leaf_age=1, now=latest + timedelta(seconds=3))
        self.assertEqual(r["reasons"], [{"code": "hidden_stale", "severity": "S", "count": 2},
                                        {"code": "leaf_stale", "severity": "S", "count": 1}])
        r = await s.verify(w.admin, C, max_leaf_age=1, now=latest + timedelta(seconds=3))
        self.assertEqual(r["reasons"], [{"code": "leaf_stale", "severity": "S", "count": 3}])
        self.assertEqual(counter(m.verification_reasons, "leaf_stale"), leaf_stale + 2)
        self.assertEqual(counter(m.verification_reasons, "hidden_stale"), 1, "as sales-2 saw them")
        self.assertEqual(counter(m.verifications, "stale"), stale_before + 2)
        self.assertEqual(audit_rows(w.store)[0]["detail"]["reasons"], ["leaf_stale"])
        await s.revoke_agent("log-1")
        await w.settle()
        revoked = counter(m.verification_reasons, "producer_revoked")
        await s.verify(w.principal("sales-2"), C)
        # counted as the caller sees them: log-1's warning is only in sales-2's summary.hidden_warnings, while the audit
        # row keeps the codes before redaction; an administrator's verification counts it
        self.assertEqual(counter(m.verification_reasons, "producer_revoked"), revoked, "a hidden node's warning is not counted")
        self.assertEqual(audit_rows(w.store)[0]["detail"]["reasons"], ["producer_revoked"])
        await s.verify(w.admin, C)
        self.assertEqual(counter(m.verification_reasons, "producer_revoked"), revoked + 1, "an administrator's, warnings included")
        self.assertEqual(audit_rows(w.store)[0]["detail"]["reasons"], ["producer_revoked"])
        rendered = m.render()[0].decode()
        # the hidden codes counted are those of reports as their callers saw them (sales-2's hidden_stale), nothing else
        self.assertEqual(set(re.findall(r'reason="(hidden_\w+)"', rendered)), {"hidden_stale"})
        labels = re.findall(r"mycelic_verification\w*\{([^}]*)\}", rendered)
        self.assertTrue(labels)
        self.assertEqual({label.split("=")[0] for label in labels}, {"verdict", "reason", "le"}, "no org label anywhere")
        self.assertEqual(len(re.findall(r'mycelic_verification_reasons_total\{reason="', rendered)),
                         len([c for c in REASONS if not c.startswith("hidden_")]) + 1)
        # refused calls: no audit row, no verdict
        rows = len(audit_rows(w.store))
        totals = {v: counter(m.verifications, v) for v in verification.VERDICTS}
        for call in (lambda: s.verify(w.principal("sales-2"), ids["log-1"]), lambda: s.verify(w.admin, "a/b"),
                     lambda: s.verify(w.admin, C, max_leaf_age=0)):
            with self.assertRaises((NotFound, ValidationError)):
                await call()
        await w.register("reader", team="field-sales", department="commercial", scopes=["memory:read"])
        with self.assertRaises(Forbidden):
            await s.verify(w.principal("reader"), C)
        self.assertEqual(len(audit_rows(w.store)), rows)
        self.assertEqual({v: counter(m.verifications, v) for v in verification.VERDICTS}, totals)

    async def test_engine_is_read_only_and_waits_for_open_transactions(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, st, C = w.service, w.store, ids["conclusion"]
        latest = max(ingested(st, mid) for mid in (ids["log-1"], ids["proc-1"], ids["sales-1"]))
        await s.retract(w.principal("log-2"), ids["log-2"], "withdrawn")      # pending: retraction_pending, log_lag
        sales = w.principal("sales-2")
        for mid in (C, ids["team"], ids["log-1"], ids["log-2"], ids["sales-1"]):
            for principal in (w.admin, sales):
                if not principal.can_read(st.get_memory(mid)):
                    continue
                changes, revision = st._conn.total_changes, st.revision
                w.engine(mid, principal, max_leaf_age=1, now=latest + timedelta(seconds=3))
                self.assertEqual((st._conn.total_changes, st.revision), (changes, revision), (mid, principal.id))
        await w.settle()
        # a verification waits for an open transaction and then reports what it committed
        async with st.transaction() as tx:
            task = asyncio.create_task(s.verify(w.admin, C))
            await asyncio.sleep(0.05)
            self.assertFalse(task.done(), "verify holds the store lock: it waits")
            tx.set_memory_status(C, "retracted", reason="test")
        r = await task
        self.assertEqual(view(r, C)["status"], "retracted")
        self.assertEqual(codes(r, C), ["node_retracted"])

        # interleaved with live applies on a started service: never 'failed'
        h = await ServiceHarness(event_signing_key=K).start()
        self.addAsyncCleanup(h.close)
        hids = await demo(h)
        hs = h.service
        verdicts: list[str] = []
        done = asyncio.Event()

        async def writer() -> None:
            await h.register("log-3", team="logistics")
            await h.register("proc-2", team="procurement")
            for i in range(12):
                agent, topic, slot = (("log-3", TRANSPORT, "transport_disruption") if i % 2 else
                                      ("proc-2", "supply:sd-9/supplier", "supplier_buffer_low"))
                mid = await h.observe(agent, f"note {i} from {agent}", topic=topic, slot=slot, entity="sd-9",
                                      confidence=0.5 + i / 30)
                if i % 4 == 3:
                    await hs.retract(h.principal(agent), mid, "withdrawn")
                await asyncio.sleep(0.02)
            await h.settle()
            done.set()

        async def verifier() -> None:
            while not done.is_set():
                for m in hs.store.list_memories(ORG, status=None, limit=1000):
                    r = await hs.verify(h.admin, m.memory_id)
                    verdicts.append(r["verdict"])
                    self.assertNotEqual(r["verdict"], "failed", r)
                await asyncio.sleep(0.01)

        await asyncio.wait_for(asyncio.gather(writer(), verifier()), timeout=60)
        self.assertGreater(len(verdicts), 20)
        self.assertNotIn("failed", verdicts)
        for m in hs.store.list_memories(ORG, status="active", limit=1000):
            self.assertEqual((await hs.verify(h.admin, m.memory_id))["verdict"], "verified", m.memory_id)
        self.assertTrue(hids)


class VerifiedSequence(Sequence):
    """A randomized G3 sequence that, once settled, verifies every memory it holds and compares each active derived
    memory's report with its rebuild's."""

    verified = 0

    async def on_settled(self, rebuilt: MycelicService) -> list[str]:
        bad: list[str] = []
        admin = Principal.admin()
        for org in ORGS:
            for m in self.service.store.list_memories(org, status=None, limit=100_000):
                r = await self.service.verify(admin, m.memory_id)
                self.verified += 1
                want = "verified" if m.status == "active" else "stale"
                derived = m.operator != "agent_observation"
                if r["verdict"] != want or (derived and r["derived_correctly"] is not True):
                    bad.append(f"verify {m.memory_id} ({m.operator} {m.layer} {m.status}): {r['verdict']} "
                               f"{[(n['memory_id'], n['reasons']) for n in r['nodes'] if n['reasons']]}")
                if derived and m.status == "active":
                    again = await rebuilt.verify(admin, m.memory_id)
                    if (again["report_digest"], again["dag_digest"]) != (r["report_digest"], r["dag_digest"]):
                        bad.append(f"verify {m.memory_id}: the rebuild reports {again['verdict']} {again['reasons']}, "
                                   f"live {r['verdict']} {r['reasons']}")
        return bad


if __name__ == "__main__":
    unittest.main()

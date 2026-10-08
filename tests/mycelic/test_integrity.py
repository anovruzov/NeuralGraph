"""Tamper-evident memory rows (schema 4): the canonical forms and their golden vectors, the keyring (rotation, previous
keys, downgrade), a digest written by every insert and reproduced by a rebuild, tampering detected by SQL edits, the
start-up backfill (run by start() before the consumer; origin, resumption, lifecycle writes on backfilled rows, its
bound, the bound's residual and the alarm), and digests that never leave the store."""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
from typing import Any
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

from mycelic import aggregation
from mycelic.api import create_app
from mycelic.cli import build_parser, cmd_serve
from mycelic.config import ConfigError, Settings
from mycelic.integrity import (
    CONTENT_FIELDS, DERIVED_METADATA, Keyring, canonical, canonical_derived, canonical_raw, check_memory, key_id,
)
from mycelic.metrics import Metrics
from mycelic.models import Memory, content_hash, now_iso
from mycelic.service import MycelicService
from mycelic.store import MycelicStore, Tx, row_memory
from mycelic.transport import InProcessTransport

from .helpers import (
    ADMIN_TOKEN, DEMO_RULE, HeldTransport, ServiceHarness, digest_map, drain_outbox, integrity_outcomes, pump, rebuild, settings,
)

K = "k" * 32
KEY_A = "signing-key-a-0123456789abcdef0123456789"
KEY_B = "signing-key-b-fedcba9876543210fedcba9876"
ID_K, ID_A, ID_B = "c440476c713d", key_id(KEY_A), key_id(KEY_B)
ORG = "northwind"
TEAM = "northwind/emea/nw-gmbh/ops/logistics"
TRANSPORT = "supply:sd-9/transport"

# ---------------------------------------------------------------------------------------------------- golden vectors
RAW_CANONICAL = (
    '{"confidence":0.3,"created_at":"2026-09-01T00:00:00+00:00","entity":"sd-9","event_id":"evt_golden","form":"raw",'
    '"independent_teams":1,"kind":"observation","layer":"agent","local_ref":null,"memory_id":"mem_golden_raw",'
    '"metadata":{"n":2,"source":"erp"},"operator":"agent_observation","org_id":"northwind","producer_id":"log-1",'
    '"rule_id":null,"scope":"northwind/emea/nw-gmbh/ops/logistics/log-1","slot":"transport_disruption",'
    '"source_event_ids":["evt_a","evt_b"],"support":1,"text":"Hafen Rotterdam: Streik an Terminal 3 — KW 41–43 ✓",'
    '"topic":"supply:sd-9/transport","visibility":"team"}'
).encode("utf-8")
DER_CANONICAL = (
    '{"confidence":0.97,"entity":"sd-9","form":"derived","independent_teams":1,"kind":"observation","layer":"team",'
    '"memory_id":"mem_dgolden","metadata":{"agg_key":"supply:sd-9/transport","child_layer":"agent","children":'
    '["northwind/emea/nw-gmbh/ops/logistics/log-1","northwind/emea/nw-gmbh/ops/logistics/log-2"],"contributing_agents":'
    '["log-1","log-2"],"contributing_teams":["northwind/emea/nw-gmbh/ops/logistics"],"corroborated_units":null,'
    '"derivation":{"min_support":2,"v":2},"effective_min_support":2,"evidence":null,"parent_count":2,'
    '"private_observations":2,"promoted_from":null,"roots":["mem_a","mem_b"],"rule_chain":[],"slots":null,'
    '"statement_origins":["team","team"],"statements":["strike","delay"]},"operator":"topic_consolidation",'
    '"org_id":"northwind","parent_ids":["mem_a","mem_b"],"producer_id":"mycelic","rule_id":null,'
    '"scope":"northwind/emea/nw-gmbh/ops/logistics","slot":"transport_disruption","support":2,'
    '"text":"supply:sd-9/transport — team \'logistics\': 2 agents. strike; delay","topic":"supply:sd-9/transport",'
    '"visibility":"org"}'
).encode("utf-8")
#: (form, origin) -> (keyed with "k" * 32, unkeyed)
GOLDEN_DIGESTS = {
    ("raw", "write"): ("47f12d8c9676877087ed27a8489e4cf7159702401a877a1883df95a6fd433b02",
                       "c3ff4017b21ccdca51fb280598ab0ef4babec6500b3188c52dbf5e647bba28c0"),
    ("raw", "backfill"): ("3604563cdd62e5875d4d954d713413ae1f8e63fe0170c8f78be3072bee37ab13",
                          "352e2b15648eba20b108e18fddb85dc0f0f96ebd8c3d81cc7e04ec3f5ccc2b74"),
    ("derived", "write"): ("d3cb99177b9cf1f75fc158b2ce16422db811477d0195dbad726e25110ca53984",
                           "c47e7d5e43d213962b42dd1a512d2de3885afa4dc407e1312461116863bfd37d"),
    ("derived", "backfill"): ("f81680e57479ec59563ebad345c2bddfdbc467cf3212e42778789b5098cb012c",
                              "6fc7b202cb91e8e837d4f7d478506d67256e2fd89065b8dda392dcfce9969a48"),
}
EVENT_SIGNATURE = "v1=3ee8b232b00d38a65125697b8ca96902d432dd558eb14939ffda0723647e7f81"   # of b"{}" under "k" * 32
DER_PARENTS = ["mem_b", "mem_a"]


def golden_raw() -> Memory:
    return Memory(memory_id="mem_golden_raw", org_id="northwind", layer="agent", scope=f"{TEAM}/log-1",
                  text="Hafen Rotterdam: Streik an Terminal 3 — KW 41–43 ✓", topic=TRANSPORT,
                  slot="transport_disruption", entity="sd-9", kind="observation", confidence=0.1 + 0.2, support=1,
                  independent_teams=1, producer_id="log-1", operator="agent_observation", rule_id=None, event_id="evt_golden",
                  visibility="team", status="active", created_at="2026-09-01T00:00:00+00:00", applied_at=None,
                  source_event_ids=["evt_b", "evt_a"], local_ref=None, metadata={"source": "erp", "n": 2})


def golden_derived() -> Memory:
    return Memory(memory_id="mem_dgolden", org_id="northwind", layer="team", scope=TEAM,
                  text="supply:sd-9/transport — team 'logistics': 2 agents. strike; delay", topic=TRANSPORT,
                  slot="transport_disruption", entity="sd-9", kind="observation", confidence=0.97, support=2, independent_teams=1,
                  producer_id="mycelic", operator="topic_consolidation", rule_id=None, event_id="evt_dX", visibility="org",
                  status="superseded", superseded_by="mem_dnext", created_at="2026-09-02T00:00:00+00:00",
                  applied_at="2026-09-02T00:00:00+00:00", source_event_ids=[], local_ref=None,
                  metadata={"agg_key": TRANSPORT, "contributing_agents": ["log-1", "log-2"], "contributing_teams": [TEAM],
                            "children": [f"{TEAM}/log-1", f"{TEAM}/log-2"], "child_layer": "agent", "parent_count": 2,
                            "version_of": "mem_dprev", "effective_min_support": 2, "registered_child_units": 2,
                            "promoted_from": None, "roots": ["mem_a", "mem_b"], "rule_chain": [],
                            "derivation": {"v": 2, "min_support": 2}, "statements": ["strike", "delay"],
                            "statement_origins": ["team", "team"], "private_observations": 2,
                            "status_reason": "coalition changed", "reactivated_at": "2026-09-03T00:00:00+00:00",
                            "fragility": {"x": 1}})


def outcome(store: MycelicStore, memory_id: str) -> str:
    """``check_memory`` of one row as stored now, against its lineage edges, under the store's keyring."""
    r = store._conn.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
    parents = [e.parent_id for e in store.parents_of(memory_id)]
    return check_memory(store.keyring, row_memory(r), parents, r["digest"], r["digest_key_id"], r["digest_origin"])


def counter(metric: Any, *labels: str) -> float:
    return (metric.labels(*labels) if labels else metric)._value.get()


def audits(store: MycelicStore, action: str) -> list[dict[str, Any]]:
    return [a for a in store.recent_audit(10_000) if a["action"] == action]


def node(tmp: str, *, key: str | None = None, previous: tuple[str, ...] = (), transport: Any = None, **overrides: Any) -> MycelicService:
    """A service over ``<tmp>/mycelic.db`` signing with ``key`` (and verifying ``previous``)."""
    return MycelicService(settings(tmp, event_signing_key=key, event_signing_keys_previous=list(previous), **overrides),
                          transport=transport or InProcessTransport(), metrics=Metrics())


async def boot(s: MycelicService) -> None:
    """The start-up steps a node without background loops needs: key bookkeeping, the backfill, the transport."""
    await s._note_signing_keys()
    await s._backfill_integrity()
    await s.transport.connect()


async def shut(s: MycelicService) -> None:
    await asyncio.sleep(0)                       # the last-seen touches authenticate() scheduled run first
    await s.store.close()


async def register(s: MycelicService, *agents: tuple[str, str, str]) -> dict[str, str]:
    keys = {}
    for agent_id, department, team in agents:
        _, keys[agent_id] = await s.register_agent({"enterprise": ORG, "region": "emea", "subsidiary": "nw-gmbh",
                                                    "department": department, "team": team, "agent_id": agent_id})
    return keys


LOGISTICS = (("log-1", "ops", "logistics"), ("log-2", "ops", "logistics"), ("log-3", "ops", "logistics"))
#: a second team in the department, so nothing above the logistics team consolidation is promoted
PROCUREMENT = (("proc-1", "ops", "procurement"),)
N1 = {"text": "Hafen Rotterdam: Streik an Terminal 3 — KW 41–43", "topic": " Supply:SD-9/Transport ",
      "slot": "Transport_Disruption", "entity": " SD-9", "visibility": "org", "confidence": 0.9,
      "metadata": {"source": "erp", "n": 2}, "local_ref": "erp-17", "idempotency_key": "n1"}


async def scenario(s: MycelicService, log: list[dict[str, Any]]) -> dict[str, str]:
    """DEMO_RULE over three teams in two departments; notes with non-canonical labels, org-visible and team-visible; an
    embedded memory of POST /events; growth (supersession), a duplicate POST, a retraction (reactivation) and growth
    again.  Returns the note ids."""
    await s.upsert_rule(DEMO_RULE)
    keys = await register(s, *LOGISTICS, *PROCUREMENT, ("sales-1", "commercial", "field-sales"))
    await pump(s, log)

    def p(agent_id: str) -> Any:
        return s.authenticate(f"Bearer {keys[agent_id]}")

    notes = {"n1": (await s.ingest_memory(p("log-1"), dict(N1)))[0].memory_id}
    notes["n2"] = (await s.ingest_memory(p("log-2"), {"text": "Carrier ETA for the SD-9 container slipped by 12 days.",
                                                      "topic": TRANSPORT, "slot": "transport_disruption", "entity": "sd-9",
                                                      "confidence": 0.7}))[0].memory_id
    notes["p1"] = (await s.ingest_memory(p("proc-1"), {"text": "Kessler Antriebe has two weeks of SD-9 inventory left.",
                                                       "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low",
                                                       "entity": "sd-9", "confidence": 0.85, "visibility": "org"}))[0].memory_id
    [entry] = await s.ingest_events(p("sales-1"), [{
        "type": "crm.commitment", "payload": {"order": 40}, "idempotency_key": "e1",
        "memory": {"text": "Helios Automation committed to 40 RX-4 arms for November.", "topic": "Supply:SD-9/Demand",
                   "slot": "demand_commitment", "entity": "SD-9", "confidence": 0.8, "source_event_ids": []}}])
    notes["embedded"] = entry["memory_id"]
    # the same topic from another team and another department: consolidations at every layer above the agents
    for agent_id in ("proc-1", "sales-1"):
        await s.ingest_memory(p(agent_id), {"text": f"Inbound delays from the Rotterdam strike ({agent_id}).", "topic": TRANSPORT,
                                            "visibility": "org", "confidence": 0.5})
    await pump(s, log)
    notes["n3"] = (await s.ingest_memory(p("log-3"), {"text": "Customs backlog of two weeks reported at Rotterdam.",
                                                      "topic": TRANSPORT, "slot": "transport_disruption", "entity": "sd-9",
                                                      "confidence": 0.6}))[0].memory_id
    await pump(s, log)
    before = digest_map(s.store)
    again, created = await s.ingest_memory(p("log-1"), dict(N1))
    assert not created and again.memory_id == notes["n1"], "a duplicate POST returns the stored note"
    assert digest_map(s.store) == before, "and signs nothing again"
    await s.retract(p("log-3"), notes["n3"], "duplicate report")
    await pump(s, log)
    # and grows again: the reactivated consolidation is superseded once more
    notes["n4"] = (await s.ingest_memory(p("log-3"), {"text": "Customs backlog confirmed by the port authority.",
                                                      "topic": TRANSPORT, "confidence": 0.65}))[0].memory_id
    await pump(s, log)
    return notes


class CanonicalFormTests(unittest.TestCase):
    def test_canonical_form_golden_vectors(self) -> None:
        raw, der = golden_raw(), dataclasses.replace(golden_derived(), status="active", superseded_by=None)
        self.assertEqual(canonical_raw(raw), RAW_CANONICAL)
        self.assertEqual(canonical(raw), RAW_CANONICAL)
        self.assertEqual(canonical_derived(der, DER_PARENTS), DER_CANONICAL)
        self.assertEqual(canonical(der, DER_PARENTS), DER_CANONICAL)
        self.assertEqual(key_id(K), ID_K)
        keyed, unkeyed = Keyring(K), Keyring()
        for (form, origin), (want_keyed, want_unkeyed) in GOLDEN_DIGESTS.items():
            data = RAW_CANONICAL if form == "raw" else DER_CANONICAL
            with self.subTest(form=form, origin=origin):
                self.assertEqual(keyed.sign(data, origin=origin), (want_keyed, ID_K))
                self.assertEqual(unkeyed.sign(data, origin=origin), (want_unkeyed, "none"))
                self.assertEqual(keyed.check(data, want_keyed, ID_K, origin=origin), "ok")
                self.assertEqual(unkeyed.check(data, want_unkeyed, "none", origin=origin), "ok")
        self.assertEqual(keyed.event_signature(b"{}"), EVENT_SIGNATURE)
        # the pre-keyring formula, so streams written before schema 4 verify unchanged
        self.assertEqual(EVENT_SIGNATURE, "v1=" + hmac.new(K.encode(), b"{}", hashlib.sha256).hexdigest())
        # the lifecycle of a row that is not active enters the form (schema 6), right after "slot" in key order
        self.assertEqual(canonical(golden_derived(), DER_PARENTS),
                         DER_CANONICAL.replace(b',"support":2,', b',"status":"superseded","superseded_by":"mem_dnext","support":2,'))
        # fields a later release adds enter the form only when set
        for m, form in ((golden_raw(), RAW_CANONICAL), (der, DER_CANONICAL)):
            m.expires_at = None
            m.attested_at = None
            self.assertEqual(canonical(m, DER_PARENTS), form)
            m.expires_at = "2026-12-31T00:00:00+00:00"
            self.assertNotEqual(canonical(m, DER_PARENTS), form)
            self.assertIn(b'"expires_at":"2026-12-31T00:00:00+00:00"', canonical(m, DER_PARENTS))

    def test_canonical_form_edge_cases(self) -> None:
        raw, der = golden_raw(), golden_derived()
        R, D = canonical(raw), canonical(der, DER_PARENTS)
        same, differ = self.assertEqual, self.assertNotEqual
        same(canonical(dataclasses.replace(raw, confidence=0.3)), R, "0.1 + 0.2 is 0.3")
        same(canonical(dataclasses.replace(raw, confidence=1)), canonical(dataclasses.replace(raw, confidence=1.0)),
             "integer and float confidences from legacy payloads are the same")
        differ(canonical(dataclasses.replace(raw, confidence=0.31)), R)
        differ(canonical(dataclasses.replace(raw, local_ref="")), R, "None and '' are different values")
        same(canonical(dataclasses.replace(raw, source_event_ids=["evt_a", "evt_b"])), R, "source order is irrelevant")
        differ(canonical(dataclasses.replace(raw, source_event_ids=["evt_a", "evt_a", "evt_b"])), R, "sorted, not de-duplicated")
        same(canonical(der, ["mem_a", "mem_b", "mem_a"]), D, "parent order and duplicates are irrelevant")
        differ(canonical(der, ["mem_a"]), D)
        differ(canonical(der, ["mem_a", "mem_b", "mem_c"]), D)
        self.assertIn("— KW 41–43 ✓".encode("utf-8"), R, "non-ASCII text is raw UTF-8")
        self.assertNotIn(b"\\u", R)
        same(canonical(dataclasses.replace(raw, metadata={**raw.metadata, "status_reason": "withdrawn",
                                                          "reactivated_at": now_iso()})), R)
        differ(canonical(dataclasses.replace(raw, metadata={**raw.metadata, "n": 3})), R)
        differ(canonical(dataclasses.replace(raw, metadata={**raw.metadata, "extra": None})), R)
        # every content field is covered, in both forms
        def other(m: Memory, name: str) -> Any:
            value = getattr(m, name)
            return value + 1 if isinstance(value, int) else (value or "") + "x"

        for name in CONTENT_FIELDS:
            with self.subTest(field=name):
                differ(canonical(dataclasses.replace(raw, **{name: other(raw, name)})), R)
                if name != "operator":
                    differ(canonical(dataclasses.replace(der, **{name: other(der, name)}), DER_PARENTS), D)
        for name in ("created_at", "event_id", "local_ref"):
            with self.subTest(field=name):
                differ(canonical(dataclasses.replace(raw, **{name: (getattr(raw, name) or "") + "x"})), R)
        for name in DERIVED_METADATA:
            with self.subTest(metadata=name):
                differ(canonical(dataclasses.replace(der, metadata={**der.metadata, name: "changed"}), DER_PARENTS), D)
        # apply-time fields are not covered; the lifecycle is (schema 6), and an active row's form is its insert's
        with self.subTest(changes={"applied_at": now_iso()}):
            same(canonical(dataclasses.replace(raw, applied_at=now_iso())), R)
            same(canonical(dataclasses.replace(der, applied_at=now_iso()), DER_PARENTS), D)
        same(canonical(dataclasses.replace(raw, status="active", superseded_by=None)), R)
        for changes in ({"status": "retracted"}, {"status": "superseded"}, {"superseded_by": "mem_x"}):
            with self.subTest(changes=changes):
                differ(canonical(dataclasses.replace(raw, **changes)), R)
        for changes in ({"status": "active"}, {"status": "retracted"}, {"superseded_by": None}, {"superseded_by": "mem_y"}):
            with self.subTest(changes=changes):
                differ(canonical(dataclasses.replace(der, **changes), DER_PARENTS), D)
        same(canonical(dataclasses.replace(der, created_at=now_iso(), event_id=None), DER_PARENTS), D)
        lifecycle = ("version_of", "fragility", "candidates", "fragility_scored_candidates", "registered_child_units",
                     "reactivated_at", "status_reason")
        same(canonical(dataclasses.replace(der, metadata={k: v for k, v in der.metadata.items() if k not in lifecycle}), DER_PARENTS), D)
        same(canonical(dataclasses.replace(der, metadata={**der.metadata, "fragility": {"y": 2}, "candidates": 9,
                                                          "fragility_scored_candidates": 4, "version_of": None,
                                                          "registered_child_units": 7, "unknown_key": 1}), DER_PARENTS), D)
        # a missing allowlisted key is null (legacy derived rows)
        without = dataclasses.replace(der, metadata={k: v for k, v in der.metadata.items() if k != "statements"})
        same(canonical(without, DER_PARENTS), canonical(dataclasses.replace(der, metadata={**der.metadata, "statements": None}), DER_PARENTS))
        # a lone surrogate fails where the SQLite insert of the same text fails
        bad = dataclasses.replace(raw, text="Streik \ud800")
        with self.assertRaises(UnicodeEncodeError):
            canonical(bad)
        with self.assertRaises(UnicodeEncodeError):
            sqlite3.connect(":memory:").execute("SELECT ?", (bad.text,))
        store = MycelicStore(":memory:")
        try:
            async def insert() -> None:
                async with store.transaction() as tx:
                    tx.insert_memory(bad)
            with self.assertRaises(UnicodeEncodeError):
                asyncio.run(insert())
            self.assertIsNone(store.get_memory(bad.memory_id))
        finally:
            store._conn.close()

    def test_keyring_outcomes_and_event_signatures(self) -> None:
        kr, unkeyed = Keyring(K), Keyring()
        data = canonical(golden_raw())
        digest, kid = kr.sign(data)
        bare = unkeyed.sign(data)[0]
        self.assertEqual(kr.check(data, digest, kid), "ok")
        self.assertEqual(kr.check(data + b" ", digest, kid), "mismatch")
        self.assertEqual(Keyring("j" * 32).check(data, digest, kid), "unknown_key")
        self.assertEqual(Keyring("j" * 32, [K]).check(data, digest, kid), "ok", "a previous key verifies")
        self.assertEqual(unkeyed.check(data, digest, kid), "unknown_key", "an unkeyed node cannot verify a keyed digest")
        self.assertEqual(kr.check(data, bare, "none"), "downgraded")
        self.assertEqual(kr.check(data, digest, "none"), "downgraded")
        self.assertEqual(unkeyed.check(data, bare, "none"), "ok")
        self.assertEqual(unkeyed.check(data + b" ", bare, "none"), "mismatch")
        for weird in ("é" * 64, digest.upper(), digest[:-1], "", 5, None, b"x"):
            with self.subTest(digest=weird):
                self.assertEqual(kr.check(data, weird, kid), "mismatch")
        for weird_id in (None, 5, kid.encode(), ["c440476c713d"]):
            with self.subTest(key_id=weird_id):
                self.assertEqual(kr.check(data, digest, weird_id), "mismatch")
        for weird_origin in (None, "other", "WRITE", 1, ["write"]):
            with self.subTest(origin=weird_origin):
                self.assertEqual(kr.check(data, digest, kid, origin=weird_origin), "mismatch")
        backfilled = kr.sign(data, origin="backfill")[0]
        self.assertEqual(kr.check(data, backfilled, kid, origin="backfill"), "ok")
        self.assertEqual(kr.check(data, backfilled, kid, origin="write"), "mismatch")
        self.assertEqual(kr.check(data, digest, kid, origin="backfill"), "mismatch")
        self.assertEqual(unkeyed.check(data, unkeyed.sign(data, origin="backfill")[0], "none", origin="write"), "mismatch")
        # domain separation from the event signature (an HMAC under the key itself)
        self.assertNotEqual(digest, hmac.new(K.encode(), data, hashlib.sha256).hexdigest())
        self.assertNotEqual("v1=" + digest, kr.event_signature(data))
        # events: a previous key verifies what it signed; only the current key signs
        wire = b'{"event_id":"evt_1","kind":"memory.observed"}'
        by_a, by_b = Keyring(KEY_A).event_signature(wire), Keyring(KEY_B).event_signature(wire)
        rotated = Keyring(KEY_B, [KEY_A])
        self.assertEqual(rotated.event_signature(wire), by_b)
        self.assertTrue(rotated.verify_event(wire, by_a))
        self.assertTrue(rotated.verify_event(wire, by_b))
        self.assertFalse(Keyring(KEY_B).verify_event(wire, by_a))
        self.assertFalse(rotated.verify_event(wire + b" ", by_a))
        for given in ("", "v1=", "v1=é", by_a.upper(), None):
            with self.subTest(given=given):
                self.assertFalse(rotated.verify_event(wire, given))
        self.assertTrue(unkeyed.verify_event(wire, ""), "nothing to check without a key")
        self.assertIsNone(unkeyed.event_signature(wire))
        # a keyring never shows a key
        for text in (repr(rotated), str(rotated)):
            self.assertNotIn(KEY_A, text)
            self.assertNotIn(KEY_B, text)
            self.assertIn(ID_A, text)
            self.assertIn(ID_B, text)
        self.assertFalse(hasattr(rotated, "__dict__"))
        # keys are de-duplicated in order, empty ones dropped, previous keys ignored without a current one
        self.assertEqual(Keyring(KEY_B, [KEY_A, KEY_B, "", KEY_A]).key_ids, [ID_B, ID_A])
        self.assertEqual((rotated.keyed, rotated.key_id, rotated.key_ids), (True, ID_B, [ID_B, ID_A]))
        for current in (None, ""):
            ring = Keyring(current, [KEY_A])
            self.assertEqual((ring.keyed, ring.key_id, ring.key_ids), (False, "none", []))
        # check_memory: the digest columns as a database returns them, never an exception
        m = golden_raw()
        self.assertEqual(check_memory(kr, m, [], digest, kid, "write"), "ok")
        self.assertEqual(check_memory(kr, m, [], None, kid, "write"), "missing")
        self.assertEqual(check_memory(kr, m, [], None, None, None), "missing")
        for columns in ((5, kid, "write"), (digest, None, "write"), (digest, kid, None), (b"x", kid, "write")):
            with self.subTest(columns=columns):
                self.assertEqual(check_memory(kr, m, [], *columns), "mismatch")
        self.assertEqual(check_memory(kr, dataclasses.replace(m, metadata=[1]), [], digest, kid, "write"), "mismatch")
        self.assertEqual(check_memory(kr, dataclasses.replace(m, confidence="high"), [], digest, kid, "write"), "mismatch")


class IntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def other_dir(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    async def test_every_new_row_has_a_digest_and_rebuild_reproduces_it(self) -> None:
        s, log = node(self.tmp.name, key=K), []
        await boot(s)
        notes = await scenario(s, log)
        rows = s.store._conn.execute("SELECT * FROM memories").fetchall()
        layers = {r["layer"] for r in rows}
        self.assertEqual(layers, {"agent", "team", "department", "subsidiary", "region", "enterprise"})
        self.assertEqual({r["operator"] for r in rows}, {"agent_observation", "topic_consolidation", "slot_composition"})
        self.assertEqual({r["status"] for r in rows}, {"active", "superseded", "retracted"})
        self.assertTrue(any("reactivated_at" in json.loads(r["metadata"]) for r in rows), "a row was reactivated")
        self.assertTrue(any(json.loads(r["metadata"]).get("promoted_from") for r in rows), "a promotion was signed too")
        self.assertEqual({(r["digest_key_id"], r["digest_origin"]) for r in rows}, {(ID_K, "write")})
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", r["digest"]) for r in rows))
        self.assertEqual(set(integrity_outcomes(s.store).values()), {"ok"})
        self.assertEqual(s.store.get_memory(notes["n1"]).topic, TRANSPORT, "stored in canonical spelling")

        rebuilt = await rebuild(log, self.tmp.name, event_signing_key=K)
        try:
            self.assertEqual(digest_map(rebuilt.store), digest_map(s.store), "a rebuild reproduces every digest and key")
            live, again = digest_map(s.store), digest_map(rebuilt.store)
            for name in ("n1", "embedded", "n3"):
                self.assertEqual(again[notes[name]], live[notes[name]], f"{name}: the digest at ingest is the digest at apply")
            self.assertEqual(set(integrity_outcomes(rebuilt.store).values()), {"ok"})
        finally:
            await rebuilt.store.close()
        bare = await rebuild(log, self.tmp.name)
        try:
            self.assertEqual({kid for _, kid in digest_map(bare.store).values()}, {"none"})
            self.assertFalse(bare.store.keyring.keyed)
            self.assertEqual(set(integrity_outcomes(bare.store).values()), {"ok"})
            self.assertEqual(set(digest_map(bare.store)), set(digest_map(s.store)))
        finally:
            await bare.store.close()
        await shut(s)

    def tampered(self, store: MycelicStore, memory_id: str, *statements: tuple[str, tuple]) -> str:
        """The outcome for ``memory_id`` with ``statements`` applied, all rolled back afterwards."""
        c = store._conn
        c.execute("BEGIN")
        try:
            for sql, args in statements:
                self.assertEqual(c.execute(sql, args).rowcount, 1, sql)
            return outcome(store, memory_id)
        finally:
            c.execute("ROLLBACK")

    async def test_tampering_and_downgrade_are_detected(self) -> None:
        s, log = node(self.tmp.name, key=K, previous=("j" * 32,)), []
        await boot(s)
        notes = await scenario(s, log)
        st = s.store
        raw = notes["n1"]
        [team] = st.list_memories(ORG, layers=["team"], topic=TRANSPORT)
        self.assertEqual((team.metadata["statements"] != [], len(team.metadata["roots"])), (True, 3))
        [conclusion] = st.list_memories(ORG, operator="slot_composition")
        edges = st.parents_of(team.memory_id)
        self.assertEqual(set(integrity_outcomes(st).values()), {"ok"})
        mem = "UPDATE memories SET {} WHERE memory_id=?"

        def edit(memory_id: str, assignment: str, *args: Any) -> tuple[str, tuple]:
            return mem.format(assignment), (*args, memory_id)

        cases = [
            # raw content
            (raw, [edit(raw, "text=text||'!'")], "mismatch"),
            (raw, [edit(raw, "confidence=confidence+0.01")], "mismatch"),
            (raw, [edit(raw, "support=2")], "mismatch"),
            (raw, [edit(raw, "independent_teams=2")], "mismatch"),
            (raw, [edit(raw, "topic='supply:sd-9/other'")], "mismatch"),
            (raw, [edit(raw, "visibility='team'")], "mismatch"),
            (raw, [edit(raw, "metadata=json_set(metadata, '$.source', 'forged')")], "mismatch"),
            (raw, [edit(raw, "created_at='2020-01-01T00:00:00+00:00'")], "mismatch"),
            (raw, [edit(raw, "source_event_ids='[\"evt_x\"]'")], "mismatch"),
            # derived content
            (team.memory_id, [edit(team.memory_id, "text=text||'!'")], "mismatch"),
            (team.memory_id, [edit(team.memory_id, "confidence=0.5")], "mismatch"),
            (team.memory_id, [edit(team.memory_id, "support=support+1")], "mismatch"),
            (team.memory_id, [edit(team.memory_id, "metadata=json_set(metadata, '$.statements', json('[\"forged\"]'))")], "mismatch"),
            (team.memory_id, [edit(team.memory_id, "metadata=json_set(metadata, '$.roots', json('[\"mem_x\"]'))")], "mismatch"),
            (conclusion.memory_id, [edit(conclusion.memory_id, "text='No risk.'")], "mismatch"),
            (conclusion.memory_id, [edit(conclusion.memory_id, "metadata=json_set(metadata, '$.slots.supplier_buffer_low', 'mem_x')")],
             "mismatch"),
            # lineage: an edge re-pointed, deleted or added
            (team.memory_id, [("UPDATE lineage_edges SET parent_id=? WHERE child_id=? AND parent_id=?",
                               (notes["p1"], team.memory_id, edges[0].parent_id))], "mismatch"),
            (team.memory_id, [("DELETE FROM lineage_edges WHERE child_id=? AND parent_id=?", (team.memory_id, edges[0].parent_id))],
             "mismatch"),
            (team.memory_id, [("INSERT INTO lineage_edges VALUES (?, ?, 'proc-1', 'agent', '2026-01-01T00:00:00+00:00')",
                               (team.memory_id, notes["p1"]))], "mismatch"),
            # the key id and the origin
            (raw, [edit(raw, "digest_key_id=?", key_id("j" * 32))], "mismatch"),
            (raw, [edit(raw, "digest_key_id=NULL")], "mismatch"),
            (raw, [edit(raw, "digest_origin='backfill'")], "mismatch"),
            (raw, [edit(raw, "digest_origin=NULL")], "mismatch"),
            (raw, [edit(raw, "digest=upper(digest)")], "mismatch"),
            (raw, [edit(raw, "digest=?", "é" * 64)], "mismatch"),
            (raw, [edit(raw, "digest_key_id='none'")], "downgraded"),
            (raw, [edit(raw, "digest=?, digest_key_id='none'", Keyring().sign(canonical(st.get_memory(raw)))[0])], "downgraded"),
            (raw, [edit(raw, "digest_key_id='0123456789ab'")], "unknown_key"),
            (raw, [edit(raw, "digest=NULL")], "missing"),
            # the lifecycle columns are covered (schema 6): a status set in the database is an edit
            (raw, [edit(raw, "status='retracted'")], "mismatch"),
            (raw, [edit(raw, "superseded_by='mem_x'")], "mismatch"),
            (team.memory_id, [edit(team.memory_id, "status='superseded', superseded_by='mem_x'")], "mismatch"),
            # apply-time columns and the reason of a status are not
            (raw, [edit(raw, "applied_at=NULL")], "ok"),
            (raw, [edit(raw, "apply_seq=apply_seq+1000")], "ok"),
            (raw, [edit(raw, "metadata=json_set(metadata, '$.status_reason', 'withdrawn')")], "ok"),
            (team.memory_id, [edit(team.memory_id, "event_id='evt_other'")], "ok"),
            (team.memory_id, [edit(team.memory_id, "metadata=json_set(metadata, '$.status_reason', 'x', '$.version_of', 'mem_x')")], "ok"),
        ]
        for memory_id, statements, expected in cases:
            with self.subTest(memory=memory_id, edit=statements[0][0]):
                self.assertEqual(self.tampered(st, memory_id, *statements), expected)
        # a backfill digest relabelled as written, and the other way round
        for row_id, parents in ((raw, []), (team.memory_id, [e.parent_id for e in edges])):
            backfilled = s.keyring.sign(canonical(st.get_memory(row_id), parents), origin="backfill")[0]
            relabel = edit(row_id, "digest=?, digest_origin='backfill'", backfilled)
            self.assertEqual(self.tampered(st, row_id, relabel), "ok")
            self.assertEqual(self.tampered(st, row_id, relabel, edit(row_id, "digest_origin='write'")), "mismatch")
        self.assertEqual(set(integrity_outcomes(st).values()), {"ok"}, "every edit was rolled back")
        await shut(s)

    async def test_previous_key_verifies_old_digests(self) -> None:
        s1, log = node(self.tmp.name, key=KEY_A), []
        await boot(s1)
        await s1.upsert_rule(DEMO_RULE)
        keys = await register(s1, *LOGISTICS, *PROCUREMENT)
        await pump(s1, log)
        for agent_id in ("log-1", "log-2"):
            await s1.ingest_memory(s1.authenticate(f"Bearer {keys[agent_id]}"), {"text": f"strike seen by {agent_id}", "topic": TRANSPORT})
        await pump(s1, log)
        signed_by_a = digest_map(s1.store)
        self.assertEqual({kid for _, kid in signed_by_a.values()}, {ID_A})
        self.assertEqual(json.loads(s1.store.get_meta("known_key_ids")), [ID_A])
        await shut(s1)

        s2 = node(self.tmp.name, key=KEY_B, previous=(KEY_A,))
        await boot(s2)
        self.assertEqual(json.loads(s2.store.get_meta("known_key_ids")), [ID_A, ID_B])
        self.assertEqual(set(integrity_outcomes(s2.store).values()), {"ok"}, "rows signed by A verify with A listed")
        await s2.ingest_memory(s2.authenticate(f"Bearer {keys['log-3']}"), {"text": "strike seen by log-3", "topic": TRANSPORT})
        await pump(s2, log)
        now = digest_map(s2.store)
        # the one row whose lifecycle changed since (the team consolidation the new note superseded) is signed again,
        # by the current key; every other row keeps the digest A signed
        resigned = {m for m in signed_by_a if now[m] != signed_by_a[m]}
        self.assertEqual([(s2.store.get_memory(m).layer, s2.store.get_memory(m).status) for m in resigned],
                         [("team", "superseded")])
        self.assertEqual({now[m][1] for m in resigned}, {ID_B})
        self.assertEqual({m: now[m] for m in signed_by_a if m not in resigned}, {m: v for m, v in signed_by_a.items()
                                                                               if m not in resigned})
        new = set(now) - set(signed_by_a)
        self.assertEqual(len(new), 2, "the note and the grown team consolidation")
        self.assertEqual({now[m][1] for m in new}, {ID_B})
        self.assertEqual(set(integrity_outcomes(s2.store).values()), {"ok"})
        await shut(s2)

        s3 = node(self.tmp.name, key=KEY_B)
        with self.assertLogs("mycelic.service", "WARNING") as logs:
            await boot(s3)
        self.assertTrue(any(ID_A in line and "MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS" in line for line in logs.output), logs.output)
        self.assertFalse(any(KEY_A in line or KEY_B in line for line in logs.output))
        outcomes = integrity_outcomes(s3.store)
        self.assertEqual({outcomes[m] for m in signed_by_a if m not in resigned}, {"unknown_key"}, "never ok once A is not listed")
        self.assertEqual({outcomes[m] for m in new | resigned}, {"ok"})
        self.assertEqual(json.loads(s3.store.get_meta("known_key_ids")), [ID_A, ID_B])
        await shut(s3)

        s4 = node(self.tmp.name)                    # a keyed database started without a key
        with self.assertLogs("mycelic.service", "WARNING") as logs:
            await boot(s4)
        self.assertTrue(any(ID_A in line and ID_B in line for line in logs.output), logs.output)
        await s4.ingest_memory(s4.authenticate(f"Bearer {keys['log-1']}"), {"text": "unkeyed note", "topic": "ops:x"})
        await pump(s4, log)
        outcomes, kids = integrity_outcomes(s4.store), digest_map(s4.store)
        self.assertEqual({outcomes[m] for m in now}, {"unknown_key"})
        unkeyed = set(kids) - set(now)
        self.assertEqual(len(unkeyed), 1)
        self.assertEqual({(kids[m][1], outcomes[m]) for m in unkeyed}, {("none", "ok")})
        self.assertEqual(json.loads(s4.store.get_meta("known_key_ids")), [ID_A, ID_B], "an unkeyed start records nothing")
        await shut(s4)

    async def held_writes(self, tmp: str, transport: HeldTransport) -> dict[str, str]:
        """A node keyed with A accepts registrations, the rule and notes; the consumer is held, so all of them are
        still in the log, signed by A, when it shuts down."""
        s1 = node(tmp, key=KEY_A, transport=transport)
        await s1.start()
        try:
            await s1.upsert_rule(DEMO_RULE)
            keys = await register(s1, *LOGISTICS[:2], *PROCUREMENT, ("sales-1", "commercial", "field-sales"))
            for agent_id, topic, slot in (("log-1", TRANSPORT, "transport_disruption"), ("log-2", TRANSPORT, "transport_disruption"),
                                          ("proc-1", "supply:sd-9/supplier", "supplier_buffer_low"),
                                          ("sales-1", "supply:sd-9/demand", "demand_commitment")):
                await s1.ingest_memory(s1.authenticate(f"Bearer {keys[agent_id]}"),
                                       {"text": f"{slot} for sd-9 ({agent_id})", "topic": topic, "slot": slot, "entity": "sd-9"})
            await drain_outbox(s1)
            self.assertEqual(s1.store.max_apply_seq(), 0, "nothing applied while held")
        finally:
            await s1.close()
        return keys

    async def test_rotation_with_events_in_flight(self) -> None:
        held = HeldTransport()
        await self.held_writes(self.tmp.name, held)
        self.assertEqual(len(held._log), 9, "the rule, four registrations and four notes")
        held.hold = False
        s2 = node(self.tmp.name, key=KEY_B, previous=(KEY_A,), transport=held)
        await s2.start()
        try:
            self.assertTrue(await s2.wait_idle(20))
            self.assertEqual(counter(s2.metrics.events_failed, "signature"), 0)
            self.assertEqual(s2.store.event_counts().get("applied"), s2.store.stats()["events"]["applied"])
            raw = s2.store.list_memories(ORG, operator="agent_observation", status=None, limit=100)
            self.assertEqual(len(raw), 4)
            self.assertTrue(all(m.applied_at for m in raw), "events signed by A applied after the rotation")
            [conclusion] = s2.store.list_memories(ORG, operator="slot_composition")
            self.assertEqual(conclusion.layer, "enterprise")
            kids = digest_map(s2.store)
            self.assertEqual({kids[m.memory_id][1] for m in raw}, {ID_A}, "accepted (and signed) before the rotation")
            self.assertEqual({kid for mid, (_, kid) in kids.items() if mid not in {m.memory_id for m in raw}}, {ID_B})
            self.assertEqual(set(integrity_outcomes(s2.store).values()), {"ok"})
        finally:
            await s2.close()

        # the same, rotated without A: every event A signed is rejected and nothing it carried is applied
        tmp, held = self.other_dir(), HeldTransport()
        await self.held_writes(tmp, held)
        held.hold = False
        s3 = node(tmp, key=KEY_B, transport=held)
        with self.assertLogs("mycelic.service", "WARNING") as logs:
            await s3.start()
            try:
                self.assertTrue(await s3.wait_idle(20))
                self.assertEqual(counter(s3.metrics.events_failed, "signature"), len(held._log))
                self.assertEqual(len(audits(s3.store, "event.rejected")), len(held._log))
                self.assertEqual({a["detail"]["reason"] for a in audits(s3.store, "event.rejected")}, {"invalid signature"})
                raw = s3.store.list_memories(ORG, operator="agent_observation", status=None, limit=100)
                self.assertEqual(len(raw), 4, "the API stored them before the rotation")
                self.assertFalse(any(m.applied_at for m in raw), "never applied")
                self.assertEqual(s3.store.list_memories(ORG, layers=["team", "department", "subsidiary", "region", "enterprise"],
                                                        status=None), [])
            finally:
                await s3.close()
        self.assertTrue(any(ID_A in line for line in logs.output))

    async def lineage_team(self, s: MycelicService, log: list[dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
        """Three logistics notes, the third growing the team consolidation: (keys, [n1, n2, n3])."""
        keys = await register(s, *LOGISTICS, *PROCUREMENT)
        await pump(s, log)
        notes = []
        for agent_id in ("log-1", "log-2", "log-3"):
            m, _ = await s.ingest_memory(s.authenticate(f"Bearer {keys[agent_id]}"), {
                "text": f"Strike at terminal 3, reported by {agent_id}.", "topic": TRANSPORT, "visibility": "org"})
            notes.append(m.memory_id)
            await pump(s, log)
        return keys, notes

    async def test_reactivation_after_rotation_does_not_raise(self) -> None:
        s1, log = node(self.tmp.name, key=KEY_A), []
        await boot(s1)
        keys, (n1, n2, n3) = await self.lineage_team(s1, log)
        versions = s1.store.list_memories(ORG, layers=["team"], status=None, newest_first=False)
        self.assertEqual([m.status for m in versions], ["superseded", "active"])
        c1 = versions[0]
        self.assertEqual(sorted(c1.metadata["roots"]), sorted([n1, n2]))
        before = s1.store.integrity_of([c1.memory_id])[c1.memory_id]
        self.assertEqual(before[1:], (ID_A, "write"))
        await shut(s1)

        s2 = node(self.tmp.name, key=KEY_B, previous=(KEY_A,))
        await boot(s2)
        known = digest_map(s2.store)
        event = await s2.retract(s2.authenticate(f"Bearer {keys['log-3']}"), n3, "false alarm")
        await pump(s2, log)
        self.assertEqual(s2.store.get_event(event.event_id).status, "applied")
        back = s2.store.get_memory(c1.memory_id)
        self.assertEqual(back.status, "active", "the earlier coalition is current again")
        self.assertIn("reactivated_at", back.metadata)
        after = s2.store.integrity_of([c1.memory_id])[c1.memory_id]
        self.assertNotEqual(after[0], before[0], "its lifecycle changed, so it is signed again")
        self.assertEqual(after[1:], (ID_B, "write"), "by the current key, under its origin")
        self.assertEqual(outcome(s2.store, c1.memory_id), "ok")
        self.assertEqual(set(integrity_outcomes(s2.store).values()), {"ok"})
        now = digest_map(s2.store)
        self.assertLessEqual({now[m][1] for m in set(now) - set(known)}, {ID_B})
        self.assertEqual(counter(s2.metrics.aggregation_inconsistency, "reactivation_mismatch"), 0)
        await shut(s2)

    async def test_reactivation_mismatch_keeps_the_signed_metadata(self) -> None:
        s, log = node(self.tmp.name, key=K), []
        await boot(s)
        keys, (n1, n2, n3) = await self.lineage_team(s, log)
        c1 = s.store.list_memories(ORG, layers=["team"], status="superseded")[0]
        original = aggregation.consolidation_statements

        def rendered_differently(*args: Any, **kwargs: Any) -> Any:
            st = original(*args, **kwargs)
            return dataclasses.replace(st, text=st.text + " (rendered differently)",
                                       statements=[x + " (altered)" for x in st.statements])

        with mock.patch.object(aggregation, "consolidation_statements", rendered_differently):
            event = await s.retract(s.authenticate(f"Bearer {keys['log-3']}"), n3, "false alarm")
            await pump(s, log)
        self.assertEqual(s.store.get_event(event.event_id).status, "applied")
        self.assertEqual(counter(s.metrics.aggregation_inconsistency, "reactivation_mismatch"), 1)
        back = s.store.get_memory(c1.memory_id)
        self.assertEqual(back.status, "active")
        self.assertEqual((back.text, back.metadata["statements"]), (c1.text, c1.metadata["statements"]),
                         "the signed text and statements are kept, not the differing recomputation")
        self.assertFalse(any("(altered)" in x for x in back.metadata["statements"]))
        self.assertEqual(outcome(s.store, c1.memory_id), "ok")
        self.assertEqual(set(integrity_outcomes(s.store).values()), {"ok"})
        await shut(s)

    async def test_squatter_and_parentless_rows_are_never_re_signed(self) -> None:
        s, log = node(self.tmp.name, key=K), []
        await boot(s)
        keys = await register(s, *LOGISTICS, *PROCUREMENT)
        await pump(s, log)
        n1, n2 = [(await s.ingest_memory(s.authenticate(f"Bearer {keys[a]}"), {"text": f"strike ({a})", "topic": TRANSPORT}))[0].memory_id
                  for a in ("log-1", "log-2")]
        await pump(s, log)
        [c1] = s.store.list_memories(ORG, layers=["team"])
        # an unrelated row holds the id the coalition with log-3's next note would take (id_collision)
        n3 = f"mem_{content_hash('log-3', 'n3')[:22]}"
        taken = aggregation.derived_memory_id(operator="topic_consolidation", scope=TEAM,
                                              key=aggregation.consolidation_id_key(TRANSPORT, 2), parent_ids=[n1, n2, n3])
        squatter = dataclasses.replace(c1, memory_id=taken, operator="agent_observation", layer="agent", scope=f"{TEAM}/log-9",
                                       producer_id="log-9", text="unrelated row", topic=None, slot=None, entity=None,
                                       applied_at=None, metadata={})
        async with s.store.transaction() as tx:
            self.assertTrue(tx.insert_memory(squatter))
        before = s.store.integrity_of([taken, c1.memory_id])
        await s.ingest_memory(s.authenticate(f"Bearer {keys['log-3']}"), {"text": "strike (log-3)", "topic": TRANSPORT,
                                                                          "idempotency_key": "n3"})
        await pump(s, log)
        self.assertEqual(counter(s.metrics.aggregation_inconsistency, "id_collision"), 1)
        self.assertEqual(s.store.integrity_of([taken, c1.memory_id]), before, "the squatter and the current version keep their digests")
        self.assertEqual((outcome(s.store, taken), outcome(s.store, c1.memory_id)), ("ok", "ok"))
        # a derived row inserted without parents (as fixtures do) is signed with none: it does not raise, and once
        # lineage edges name parents it never verifies
        loose = dataclasses.replace(c1, memory_id="mem_dloose", metadata={**c1.metadata, "agg_key": "loose"})
        async with s.store.transaction() as tx:
            self.assertTrue(tx.insert_memory(loose))
        self.assertEqual(outcome(s.store, "mem_dloose"), "ok")
        async with s.store.transaction() as tx:
            tx.add_lineage_edges([dataclasses.replace(e, child_id="mem_dloose") for e in s.store.parents_of(c1.memory_id)])
        self.assertEqual(outcome(s.store, "mem_dloose"), "mismatch")
        await shut(s)

    async def test_digest_never_leaves_through_api_or_events(self) -> None:
        h = await ServiceHarness(event_signing_key=K).start()
        client = TestClient(TestServer(create_app(h.service)))
        await client.start_server()
        admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        try:
            keys = {}
            for agent_id, dept, team in (*LOGISTICS, *PROCUREMENT, ("sales-1", "commercial", "field-sales")):
                r = await client.post("/admin/agents", json={"enterprise": ORG, "region": "emea", "subsidiary": "nw-gmbh",
                                                             "department": dept, "team": team, "agent_id": agent_id}, headers=admin)
                keys[agent_id] = {"Authorization": f"Bearer {(await r.json())['api_key']}"}
            self.assertEqual((await client.post("/admin/rules", json=DEMO_RULE, headers=admin)).status, 201)
            for agent_id, topic, slot in (("log-1", TRANSPORT, "transport_disruption"), ("log-2", TRANSPORT, "transport_disruption"),
                                          ("proc-1", "supply:sd-9/supplier", "supplier_buffer_low")):
                r = await client.post("/memory", json={"text": f"{slot} for sd-9 ({agent_id})", "topic": topic, "slot": slot,
                                                       "entity": "sd-9", "visibility": "org"}, headers=keys[agent_id])
                self.assertEqual(r.status, 202)
            r = await client.post("/events", json={"events": [{"type": "crm", "memory": {
                "text": "demand committed for sd-9", "topic": "supply:sd-9/demand", "slot": "demand_commitment", "entity": "sd-9"}}]},
                headers=keys["sales-1"])
            self.assertEqual(r.status, 202)
            await h.settle()
            r = await client.post("/memory", json={"text": "strike (log-3)", "topic": TRANSPORT, "slot": "transport_disruption",
                                                   "entity": "sd-9"}, headers=keys["log-3"])
            await h.settle()
            r = await client.post(f"/memory/{(await r.json())['memory_id']}/retract", json={"reason": "dup"}, headers=keys["log-3"])
            await h.settle()
            st = h.service.store
            digests = {r["digest"] for r in st._conn.execute("SELECT digest FROM memories")}
            self.assertGreaterEqual(len(digests), 8)
            self.assertTrue(all(d and re.fullmatch(r"[0-9a-f]{64}", d) for d in digests))
            [conclusion] = st.list_memories(ORG, operator="slot_composition")

            def leaks(text: str) -> list[str]:
                return [d for d in digests if d in text] + [n for n in ("digest_key_id", "digest_origin", '"digest"') if n in text]

            planted = next(iter(digests))
            self.assertEqual(leaks(json.dumps({"memory": {"digest": planted}})), [planted, '"digest"'], "the scanner works")
            seen: list[tuple[str, str]] = []
            for m in st.list_memories(ORG, status=None, limit=1000):
                for who, headers in (("admin", admin), ("log-1", keys["log-1"])):
                    seen.append((f"GET /memory {m.memory_id} as {who}", await (await client.get(f"/memory/{m.memory_id}", headers=headers)).text()))
            # downward verification walks every row's digest and reports none of them (before the audit log is read)
            for who, headers in (("sales-1", keys["sales-1"]), ("admin", admin)):
                r = await client.get(f"/verify/{conclusion.memory_id}", headers=headers)
                self.assertEqual(r.status, 200)
                seen.append((f"GET /verify as {who}", await r.text()))
            r = await client.post("/query", json={"query": "supply risk sd-9", "verify": True}, headers=keys["sales-1"])
            text = await r.text()
            self.assertIn("verification", json.loads(text)["answer"], text)
            seen.append(("POST /query verify", text))
            for path, headers in ((f"/memories?scope={ORG}&limit=500", admin), ("/memories?status=superseded", keys["log-1"]),
                                  ("/memories?status=retracted", keys["log-3"]), ("/memories", keys["sales-1"]),
                                  (f"/lineage/{conclusion.memory_id}", keys["sales-1"]), (f"/lineage/{conclusion.memory_id}", admin),
                                  (f"/admin/events?org={ORG}&limit=1000", admin), ("/admin/audit?limit=1000", admin),
                                  ("/admin/status", admin), ("/metrics", admin), ("/health", admin)):
                seen.append((f"GET {path}", await (await client.get(path, headers=headers)).text()))
            for body, headers in (({"query": "supply risk sd-9", "include_lineage": True}, keys["sales-1"]),
                                  ({"query": "strike", "scope": ORG, "include_lineage": True}, admin)):
                seen.append((f"POST /query {body}", await (await client.post("/query", json=body, headers=headers)).text()))
            for name, arguments in (("mycelic_query", {"query": "supply risk sd-9"}), ("mycelic_lineage", {"memory_id": conclusion.memory_id}),
                                    ("mycelic_get_memory", {"memory_id": conclusion.memory_id}),
                                    ("mycelic_verify", {"memory_id": conclusion.memory_id})):
                r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                    "params": {"name": name, "arguments": arguments}},
                                      headers={**keys["sales-1"], "Accept": "application/json"})
                text = await r.text()
                self.assertFalse(json.loads(text)["result"]["isError"], text)
                seen.append((f"MCP {name}", text))
            seen += [(f"events.payload {r['event_id']}", r["payload"]) for r in st._conn.execute("SELECT event_id, payload FROM events")]
            wire = h.service.transport._log
            self.assertGreater(len(wire), 10)
            seen += [(f"wire {msg_id}", payload.decode("utf-8") + json.dumps(headers)) for _, payload, msg_id, headers in wire]
            for where, text in seen:
                with self.subTest(where=where):
                    self.assertEqual(leaks(text), [])
            names = {f.name for f in dataclasses.fields(Memory)}
            self.assertEqual(set(conclusion.to_dict()), names)
            self.assertFalse({"digest", "digest_key_id", "digest_origin"} & names)
        finally:
            await client.close()
            await h.close()


class BackfillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    async def live_state(self) -> tuple[MycelicService, list[dict[str, Any]], dict[str, str]]:
        s, log = node(self.tmp.name, key=K), []
        await boot(s)
        notes = await scenario(s, log)
        return s, log, notes

    @staticmethod
    def upgrade(store: MycelicStore) -> None:
        """What a schema-3 database looks like right after the migration: no digests, no completion flag."""
        store._conn.execute("UPDATE memories SET digest=NULL, digest_key_id=NULL, digest_origin=NULL")
        store._conn.execute("DELETE FROM meta WHERE key='integrity_backfill_complete'")

    async def test_backfill_marks_origin_completes_and_resumes(self) -> None:
        s, log, notes = await self.live_state()
        st = s.store
        self.assertEqual(st.get_meta("integrity_backfill_complete"), "1", "a new database is complete at its first start")
        self.assertEqual(audits(st, "integrity.backfill"), [], "and has nothing to audit")
        written = {r["memory_id"]: r["digest"] for r in st._conn.execute("SELECT memory_id, digest FROM memories")}
        total = len(written)
        self.upgrade(st)
        s.backfill_batch = 2
        calls: list[dict[str, Any]] = []
        original = Tx.backfill_digests

        def cancelled_on_second_batch(tx: Tx, **kwargs: Any) -> tuple[int, int]:
            calls.append(kwargs)
            if len(calls) == 2:
                raise asyncio.CancelledError()      # SIGTERM while the second batch runs
            return original(tx, **kwargs)

        with mock.patch.object(Tx, "backfill_digests", cancelled_on_second_batch):
            with self.assertRaises(asyncio.CancelledError):
                await s._backfill_integrity()
        self.assertEqual(calls[0], {"after_rid": 0, "below_rid": None, "limit": 2})
        self.assertEqual(st._conn.execute("SELECT COUNT(*) FROM memories WHERE digest IS NOT NULL").fetchone()[0], 2,
                         "only the committed batch")
        self.assertIsNone(st.get_meta("integrity_backfill_complete"))
        self.assertEqual(counter(s.metrics.integrity_backfilled), 2)

        with self.assertLogs("mycelic.service", "WARNING") as logs:
            self.assertEqual(await s._backfill_integrity(), total - 2)
        self.assertTrue(any(f"signed {total - 2} memories that had no digest" in line for line in logs.output), logs.output)
        rows = st._conn.execute("SELECT * FROM memories").fetchall()
        self.assertEqual({(r["digest_key_id"], r["digest_origin"]) for r in rows}, {(ID_K, "backfill")})
        self.assertEqual(set(integrity_outcomes(st).values()), {"ok"})
        for r in rows:
            parents = [e.parent_id for e in st.parents_of(r["memory_id"])]
            with self.subTest(memory=r["memory_id"]):
                self.assertNotEqual(r["digest"], written[r["memory_id"]], "another subkey")
                self.assertEqual(check_memory(s.keyring, row_memory(r), parents, written[r["memory_id"]], ID_K, "write"), "ok",
                                 "the row as stored is what was written")
        self.assertEqual(st.get_meta("integrity_backfill_complete"), "1")
        [row] = audits(st, "integrity.backfill")
        self.assertEqual(row["detail"], {"rows": total - 2, "batches": (total - 2) // 2 + 1, "refused": 0, "key_id": ID_K})
        self.assertEqual(counter(s.metrics.integrity_backfilled), total)

        # lifecycle writes on backfilled rows sign them again under their origin: retracting the last note supersedes
        # nothing new and reactivates the consolidations it had grown, which the backfill signed
        statuses = {m: st.get_memory(m).status for m in written}
        columns = st.integrity_of(list(written))
        event = await s.retract(s.authenticate(f"Bearer {ADMIN_TOKEN}"), notes["n4"], "withdrawn after the upgrade")
        await pump(s, log)
        self.assertEqual(st.get_event(event.event_id).status, "applied")
        reactivated = [m for m, status in statuses.items() if status == "superseded" and st.get_memory(m).status == "active"]
        self.assertIn("team", {st.get_memory(m).layer for m in reactivated}, reactivated)
        for m in reactivated:
            with self.subTest(reactivated=m):
                self.assertIn("reactivated_at", st.get_memory(m).metadata)
                self.assertEqual(columns[m][1:], (ID_K, "backfill"))
                self.assertEqual(outcome(st, m), "ok")
        changed = {m for m in written if st.get_memory(m).status != statuses[m]}
        self.assertTrue(set(reactivated) <= changed)
        self.assertEqual({m: v for m, v in st.integrity_of(list(written)).items() if m not in changed},
                         {m: v for m, v in columns.items() if m not in changed}, "a row whose lifecycle did not change is untouched")
        self.assertEqual({v[1:] for m, v in st.integrity_of(list(changed)).items()}, {(ID_K, "backfill")},
                         "one whose lifecycle changed keeps its key id and origin")
        self.assertEqual(set(integrity_outcomes(st).values()), {"ok"})
        self.assertLessEqual({v[1:] for m, v in st.integrity_of(list(digest_map(st))).items() if m not in written}, {(ID_K, "write")},
                             "whatever the retraction derived is signed at insert")

        keys = {a.agent_id: a for a in await st.list_agents(ORG)}
        self.assertIn("log-1", keys)
        _, key = await s.register_agent({"enterprise": ORG, "team": "late", "agent_id": "late-1"})
        m, _ = await s.ingest_memory(s.authenticate(f"Bearer {key}"), {"text": "after the backfill", "topic": "ops:x"})
        self.assertEqual(st.integrity_of([m.memory_id])[m.memory_id][1:], (ID_K, "write"))
        await shut(s)

        s2 = node(self.tmp.name, key=K)
        await s2.start()
        try:
            self.assertEqual(counter(s2.metrics.integrity_backfilled), 0)
            self.assertEqual(len(audits(s2.store, "integrity.backfill")), 1, "a second start does nothing")
            self.assertEqual(await s2._backfill_integrity(), 0)
        finally:
            await s2.close()

    async def test_upgrade_to_schema_6_signs_the_lifecycle_once(self) -> None:
        s, log, notes = await self.live_state()
        st = s.store
        inactive = [r["memory_id"] for r in st._conn.execute("SELECT memory_id FROM memories WHERE status != 'active' "
                                                              "OR superseded_by IS NOT NULL ORDER BY rid")]
        self.assertGreaterEqual(len(inactive), 3)
        # the database as schema 5 left it: every row signed without its lifecycle; and one inactive row edited before
        edited = inactive[0]
        for mid in inactive:
            r = st._conn.execute("SELECT * FROM memories WHERE memory_id=?", (mid,)).fetchone()
            before = dataclasses.replace(row_memory(r), status="active", superseded_by=None)
            if mid == edited:
                before.text += " (edited before the upgrade)"
            digest, _ = s.keyring.sign(canonical(before, [e.parent_id for e in st.parents_of(mid)]), origin=r["digest_origin"])
            st._conn.execute("UPDATE memories SET digest=? WHERE memory_id=?", (digest, mid))
        st._conn.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
        self.assertEqual({integrity_outcomes(st)[m] for m in inactive}, {"mismatch"}, "the new form does not check them yet")
        await shut(s)

        s2 = node(self.tmp.name, key=K)
        self.assertEqual(s2.store.get_meta("schema_version"), "6")
        self.assertIsNotNone(s2.store.get_meta("lifecycle_resign_below"))
        self.assertEqual(s2.store.get_meta("reaggregate_pending"), "1")
        s2.backfill_batch = 2
        with self.assertLogs("mycelic.service", "WARNING") as logs:
            await s2.start()
        try:
            outcomes = integrity_outcomes(s2.store)
            self.assertEqual({outcomes[m] for m in inactive if m != edited}, {"ok"})
            self.assertEqual(outcomes[edited], "mismatch", "an edited row is never signed again")
            self.assertEqual({o for m, o in outcomes.items() if m not in inactive}, {"ok"})
            self.assertIsNone(s2.store.get_meta("lifecycle_resign_below"))
            [row] = audits(s2.store, "integrity.lifecycle_resign")
            self.assertEqual((row["detail"]["rows"], row["detail"]["left"]), (len(inactive) - 1, 1))
            self.assertTrue(any(f"signed the lifecycle of {len(inactive) - 1} memories" in line for line in logs.output), logs.output)
            self.assertTrue(any("1 memories that were not active before the upgrade" in line for line in logs.output))
            # a status set in the database after the upgrade is an edit
            ok = next(m for m in inactive if m != edited)
            s2.store._conn.execute("UPDATE memories SET status=CASE status WHEN 'retracted' THEN 'superseded' ELSE 'retracted' "
                                   "END WHERE memory_id=?", (ok,))
            self.assertEqual(outcome(s2.store, ok), "mismatch")
        finally:
            await s2.close()
        s3 = node(self.tmp.name, key=K)
        await s3.start()
        try:
            self.assertEqual(len(audits(s3.store, "integrity.lifecycle_resign")), 1, "a second start does nothing")
            self.assertEqual(outcome(s3.store, ok), "mismatch", "and never launders an edit made after the upgrade")
        finally:
            await s3.close()

    async def test_start_runs_the_backfill_before_the_consumer(self) -> None:
        fresh = tempfile.TemporaryDirectory()
        self.addCleanup(fresh.cleanup)
        s0 = node(fresh.name, key=K)
        await s0.start()
        try:
            self.assertEqual(s0.store.get_meta("integrity_backfill_complete"), "1", "a new database is complete after start()")
            self.assertEqual(audits(s0.store, "integrity.backfill"), [])
            self.assertEqual(counter(s0.metrics.integrity_backfilled), 0)
        finally:
            await s0.close()

        s, log, notes = await self.live_state()
        # a note the API stored before the upgrade whose event the consumer has not applied yet: applied after the backfill
        _, key = await s.register_agent({"enterprise": ORG, "region": "emea", "subsidiary": "nw-gmbh", "department": "ops",
                                         "team": "logistics", "agent_id": "log-4"})
        pending, _ = await s.ingest_memory(s.authenticate(f"Bearer {key}"), {"text": "Terminal 3 reopens on Monday.",
                                                                              "topic": TRANSPORT, "visibility": "org"})
        self.assertIsNone(s.store.get_memory(pending.memory_id).applied_at)
        written = [r["memory_id"] for r in s.store._conn.execute("SELECT memory_id FROM memories")]
        total = len(written)
        self.upgrade(s.store)
        await shut(s)

        seen: dict[str, tuple[str | None, int]] = {}
        connect, consume = MycelicService._connect_with_retry, MycelicService._consumer_loop

        def state(svc: MycelicService) -> tuple[str | None, int]:
            unsigned = svc.store._conn.execute("SELECT COUNT(*) FROM memories WHERE digest IS NULL").fetchone()[0]
            return svc.store.get_meta("integrity_backfill_complete"), unsigned

        async def connecting(svc: MycelicService, *, first: bool) -> bool:
            seen.setdefault("connect", state(svc))
            return await connect(svc, first=first)

        async def consuming(svc: MycelicService) -> None:
            seen.setdefault("consumer", state(svc))
            await consume(svc)

        s2 = node(self.tmp.name, key=K)
        with mock.patch.object(MycelicService, "_connect_with_retry", connecting), \
                mock.patch.object(MycelicService, "_consumer_loop", consuming):
            with self.assertLogs("mycelic.service", "WARNING") as logs:
                await s2.start()
            try:
                self.assertTrue(await s2.wait_idle(20))
                self.assertEqual(seen, {"connect": ("1", 0), "consumer": ("1", 0)},
                                 "every row was signed and the backfill completed before the broker and the consumer")
                st = s2.store
                self.assertIsNotNone(st.get_memory(pending.memory_id).applied_at, "applied by the consumer after the backfill")
                self.assertEqual(st.integrity_of([pending.memory_id])[pending.memory_id][1:], (ID_K, "backfill"))
                self.assertEqual(outcome(st, pending.memory_id), "ok")
                self.assertTrue(any(f"signed {total} memories that had no digest" in line for line in logs.output), logs.output)
                derived = set(digest_map(st)) - set(written)
                self.assertEqual({v[1:] for v in st.integrity_of(written).values()}, {(ID_K, "backfill")})
                self.assertLessEqual({v[1:] for v in st.integrity_of(list(derived)).values()}, {(ID_K, "write")},
                                     "what applying the pending note derived is signed at insert")
                self.assertEqual(set(integrity_outcomes(st).values()), {"ok"})
                self.assertEqual(st.get_meta("integrity_backfill_complete"), "1")
                self.assertEqual(counter(s2.metrics.integrity_backfilled), total)
                [row] = audits(st, "integrity.backfill")
                self.assertEqual(row["detail"], {"rows": total, "batches": total // s2.backfill_batch + 1, "refused": 0,
                                                 "key_id": ID_K})
            finally:
                await s2.close()

    async def test_backfill_yields_between_batches(self) -> None:
        empty = node(self.tmp.name, key=K)
        calls: list[tuple[int, int]] = []
        ticks = 0
        original = Tx.backfill_digests

        def observed(tx: Tx, **kwargs: Any) -> tuple[int, int]:
            n, last = original(tx, **kwargs)
            calls.append((ticks, n))
            return n, last

        with mock.patch.object(Tx, "backfill_digests", observed):
            self.assertEqual(await empty._backfill_integrity(), 0)
        self.assertEqual(calls, [(0, 0)], "an empty database: one empty batch")
        self.assertEqual(empty.store.get_meta("integrity_backfill_complete"), "1")
        self.assertEqual(audits(empty.store, "integrity.backfill"), [])
        await shut(empty)

        store = MycelicStore(":memory:")
        async with store.transaction() as tx:
            for i in range(10):
                tx.insert_memory(Memory(memory_id=f"mem_{i}", org_id=ORG, layer="agent", scope=f"{TEAM}/log-1", text=f"note {i}",
                                        topic=None, slot=None, entity=None, kind="observation", confidence=0.5, support=1,
                                        independent_teams=1, producer_id="log-1", operator="agent_observation", rule_id=None,
                                        event_id=None, created_at=now_iso()))
        self.upgrade(store)
        s = MycelicService(settings(self.tmp.name, event_signing_key=K), store=store, transport=InProcessTransport(), metrics=Metrics())
        s.backfill_batch = 3
        calls.clear()

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(ticker())
        try:
            with mock.patch.object(Tx, "backfill_digests", observed):
                self.assertEqual(await s._backfill_integrity(), 10)
        finally:
            task.cancel()
        self.assertEqual([n for _, n in calls], [3, 3, 3, 1])
        seen = [t for t, _ in calls]
        self.assertTrue(all(a < b for a, b in zip(seen, seen[1:])), f"the event loop ran between batches: {seen}")
        self.assertEqual(counter(s.metrics.integrity_backfilled), 10)
        self.assertEqual(set(integrity_outcomes(store).values()), {"ok"})
        await store.close()

    async def test_removed_digests_are_not_re_signed(self) -> None:
        s, log, notes = await self.live_state()
        st = s.store
        rids = [r["rid"] for r in st._conn.execute("SELECT rid FROM memories ORDER BY rid")]
        victim = st._conn.execute("SELECT memory_id FROM memories WHERE rid=?", (rids[len(rids) // 2],)).fetchone()["memory_id"]
        # someone with database access edits a row, removes its digest and the completion flag, and waits for a restart
        st._conn.execute("UPDATE memories SET text='forged', digest=NULL, digest_key_id=NULL, digest_origin=NULL WHERE memory_id=?",
                         (victim,))
        st._conn.execute("DELETE FROM meta WHERE key='integrity_backfill_complete'")
        self.assertEqual(st.backfill_bound(), rids[0])
        with self.assertLogs("mycelic.service", "ERROR") as logs:
            self.assertEqual(await s._backfill_integrity(), 0)
        self.assertTrue(any("1 memories written after digests were introduced have no digest" in line for line in logs.output))
        self.assertEqual(outcome(st, victim), "missing", "never re-signed")
        [row] = audits(st, "integrity.backfill")
        self.assertEqual(row["detail"], {"rows": 0, "batches": 1, "refused": 1, "key_id": ID_K})
        self.assertEqual(st.get_meta("integrity_backfill_complete"), "1")
        self.assertEqual(counter(s.metrics.integrity_backfilled), 0)

        # the documented residual of the bound, which is read from the database: removing the digests of every row
        # signed at insert up to and including the victim moves it past the victim, which is then signed as 'backfill'
        # with its edit; the backfill is logged, audited and counted, the only trace a database created at schema 4 has
        victim_rid = rids[len(rids) // 2]
        st._conn.execute("UPDATE memories SET digest=NULL, digest_key_id=NULL, digest_origin=NULL WHERE rid<=?", (victim_rid,))
        st._conn.execute("DELETE FROM meta WHERE key='integrity_backfill_complete'")
        prefix = rids.index(victim_rid) + 1
        self.assertEqual(st.backfill_bound(), rids[prefix])
        with self.assertLogs("mycelic.service", "WARNING") as logs:
            self.assertEqual(await s._backfill_integrity(), prefix)
        self.assertTrue(any(f"signed {prefix} memories that had no digest" in line for line in logs.output), logs.output)
        self.assertEqual((outcome(st, victim), st.integrity_of([victim])[victim][2], st.get_memory(victim).text),
                         ("ok", "backfill", "forged"))
        self.assertEqual(audits(st, "integrity.backfill")[0]["detail"], {"rows": prefix, "batches": 1, "refused": 0, "key_id": ID_K})
        self.assertEqual(counter(s.metrics.integrity_backfilled), prefix)

        # and in a database whose rows all predate digests (just upgraded), any tampered row is signed as 'backfill'
        self.upgrade(st)
        self.assertIsNone(st.backfill_bound())
        with self.assertLogs("mycelic.service", "WARNING") as logs:
            n = await s._backfill_integrity()
        self.assertEqual(n, len(rids))
        self.assertTrue(any("at any other time digests were removed" in line for line in logs.output))
        self.assertEqual((outcome(st, victim), st.integrity_of([victim])[victim][2]), ("ok", "backfill"))
        self.assertEqual(audits(st, "integrity.backfill")[0]["detail"]["rows"], len(rids))
        self.assertEqual(len(audits(st, "integrity.backfill")), 3)
        self.assertEqual(counter(s.metrics.integrity_backfilled), prefix + len(rids))
        await shut(s)


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    P1, P2 = "previous-key-one-0123456789abcdef01234", "previous-key-two-0123456789abcdef01234"

    def env(self, **extra: str) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith("MYCELIC_")}
        base.update(MYCELIC_HOST="127.0.0.1", **extra)
        return base

    def from_env(self, **extra: str) -> Settings:
        with mock.patch.dict(os.environ, self.env(**extra), clear=True):
            return Settings.from_env()

    async def test_settings_validate_previous_keys(self) -> None:
        P1, P2 = self.P1, self.P2
        s = self.from_env(MYCELIC_EVENT_SIGNING_KEY=K, MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=f" {P1} , ,{P2},{K},{P1}")
        self.assertEqual(s.event_signing_keys_previous, [P1, P2])
        self.assertEqual(Keyring.from_settings(s).key_ids, [ID_K, key_id(P1), key_id(P2)])
        for value in ("", ",", " , ,"):
            self.assertEqual(self.from_env(MYCELIC_EVENT_SIGNING_KEY=K, MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=value)
                             .event_signing_keys_previous, [])
        self.assertEqual(Settings().event_signing_keys_previous, [])
        short, placeholder = "short-previous-key", "replace-me-with-the-old-key-0123456789"
        for env, needle in (({"MYCELIC_EVENT_SIGNING_KEY": K, "MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS": f"{P1},{short}"},
                             "entry 2 is shorter than 32 characters"),
                            ({"MYCELIC_EVENT_SIGNING_KEY": K, "MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS": placeholder},
                             "entry 1 looks like a placeholder"),
                            ({"MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS": P1}, "requires MYCELIC_EVENT_SIGNING_KEY")):
            with self.subTest(needle=needle):
                with self.assertRaises(ConfigError) as cm:
                    self.from_env(**env)
                message = str(cm.exception)
                self.assertIn("MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS", message)
                self.assertIn(needle, message)
                for secret in (K, P1, short, placeholder):
                    self.assertNotIn(secret, message)
                # the server refuses to start with exit code 2, without echoing a key
                err = io.StringIO()
                with mock.patch.dict(os.environ, self.env(**env), clear=True), contextlib.redirect_stderr(err):
                    self.assertEqual(await cmd_serve(build_parser().parse_args(["serve"])), 2)
                self.assertIn("configuration error", err.getvalue())
                for secret in (K, P1, short, placeholder):
                    self.assertNotIn(secret, err.getvalue())
        red = s.redacted()
        self.assertEqual((red["event_signing_key"], red["event_signing_keys_previous"]), ("set", 2))
        for secret in (K, P1, P2):
            self.assertNotIn(secret, json.dumps(red, default=str))
        self.assertIsNone(Settings().redacted()["event_signing_keys_previous"])

        h = await ServiceHarness(event_signing_key=K, event_signing_keys_previous=[P1, P2]).start()
        client = TestClient(TestServer(create_app(h.service)))
        await client.start_server()
        try:
            r = await client.get("/admin/status", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
            text = await r.text()
            for secret in (K, P1, P2):
                self.assertNotIn(secret, text)
            self.assertEqual(json.loads(text)["settings"]["event_signing_keys_previous"], 2)
            self.assertNotIn(K, repr(h.service.keyring) + repr(h.service.store.keyring))
        finally:
            await client.close()
            await h.close()


if __name__ == "__main__":
    unittest.main()

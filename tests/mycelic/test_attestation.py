"""Producer re-attestation (``POST /memory/{id}/attest``, MCP ``mycelic_attest``): a note confirmed by its producer is fresh
for verification's ``max_leaf_age`` from the attestation on, one withdrawn is retracted; only the producer of an active,
unexpired raw note may attest it; an attestation applies through the log (never backwards, never after a retraction,
never on a row whose digest does not check), re-signs the row and is reproduced by a rebuild; the due list holds the
caller's own notes that something rests on."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from mycelic import mcp
from mycelic.api import create_app
from mycelic.integrity import check_memory
from mycelic.mcp import INSTRUCTIONS, TOOLS
from mycelic.models import parse_iso, utcnow
from mycelic.sdk import MycelicClient, MycelicError
from mycelic.service import MAX_EXPIRY_SECONDS, Forbidden, NotFound, ValidationError
from mycelic.store import row_memory

from .helpers import ADMIN_TOKEN, HeldTransport, ServiceHarness, digest_map, invariant_violations, rebuild, rebuild_differences
from .test_lifecycle import K, ORG, audits, iso, mcp_call, wire
from .test_verification import TRANSPORT, World, codes, demo, detail


def ingested(store: Any, memory_id: str):
    return parse_iso(store.get_event(store.get_memory(memory_id).event_id).created_at)


class AttestationTests(unittest.IsolatedAsyncioTestCase):
    def tmpdir(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    async def world(self, **kwargs: Any) -> World:
        w = World(self.tmpdir(), **kwargs)
        self.addAsyncCleanup(w.close)
        await w.service.transport.connect()
        return w

    async def test_attest_refreshes_leaf_freshness_for_verification(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st, c = w.service, w.store, ids["conclusion"]
        leaves = {"log-1": ids["log-1"], "proc-1": ids["proc-1"], "sales-1": ids["sales-1"]}
        t0 = max(ingested(st, mid) for mid in leaves.values())
        r = await s.verify(w.admin, c, max_leaf_age=60, now=t0 + timedelta(seconds=120))
        self.assertEqual((r["verdict"], [codes(r, mid) for mid in leaves.values()]), ("stale", [["leaf_stale"]] * 3))
        digests = digest_map(st)
        for agent_id, mid in leaves.items():
            ev, still_true = await s.attest(w.principal(agent_id), mid, {"still_true": True}, now=t0 + timedelta(seconds=100))
            self.assertTrue(still_true)
            self.assertEqual((ev.kind, ev.agent_id, ev.payload), ("memory.attested", agent_id,
                                                                  {"memory_id": mid, "by": agent_id, "at": iso(t0 + timedelta(seconds=100))}))
            self.assertIsNone(st.get_memory(mid).attested_at, "until the event applies")
        await w.settle()
        for mid in leaves.values():
            m = st.get_memory(mid)
            self.assertEqual(m.attested_at, iso(t0 + timedelta(seconds=100)))
            self.assertNotEqual(digest_map(st)[mid], digests[mid], "the row is signed again")
            r = st._conn.execute("SELECT * FROM memories WHERE memory_id=?", (mid,)).fetchone()
            self.assertEqual((check_memory(st.keyring, row_memory(r), [], r["digest"], r["digest_key_id"], r["digest_origin"]),
                              r["digest_origin"]), ("ok", "write"))
        r = await s.verify(w.admin, c, max_leaf_age=60, now=t0 + timedelta(seconds=120))
        self.assertEqual((r["verdict"], r["reasons"]), ("verified", []), "fresh from the attestation on")
        r = await s.verify(w.admin, c, max_leaf_age=60, now=t0 + timedelta(seconds=161))
        self.assertEqual(detail(r, ids["log-1"], "leaf_stale"), {"age_seconds": 61, "max_leaf_age": 60})
        self.assertEqual([(a["principal"], a["target"]) for a in audits(st, "memory.attest")][::-1],
                         [(agent_id, mid) for agent_id, mid in leaves.items()])
        self.assertEqual(len(audits(st, "memory.attested")), 3)
        self.assertEqual(invariant_violations(s, ORG), [])

    async def test_attest_false_retracts_and_cascades(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st = w.service, w.store
        ev, still_true = await s.attest(w.principal("sales-1"), ids["sales-1"], {"still_true": False, "reason": "order cancelled"})
        self.assertFalse(still_true)
        self.assertEqual((ev.kind, ev.payload["reason"]), ("memory.retracted", "order cancelled"))
        self.assertEqual([a["detail"]["reason"] for a in audits(st, "memory.retract")], ["order cancelled"])
        ev, _ = await s.attest(w.principal("log-2"), ids["log-2"], {"still_true": False})
        self.assertEqual(ev.payload["reason"], "no longer true (attestation)")
        await w.settle()
        self.assertEqual((st.get_memory(ids["sales-1"]).status, st.get_memory(ids["sales-1"]).metadata["status_reason"]),
                         ("retracted", "order cancelled"))
        self.assertEqual(st.get_memory(ids["conclusion"]).status, "retracted", "as a retraction would: no demand evidence left")
        self.assertEqual(st.get_memory(ids["team"]).status, "retracted", "and logistics is down to one note")
        self.assertEqual(invariant_violations(s, ORG), [])
        r = await s.verify(w.admin, ids["conclusion"])
        self.assertEqual((r["verdict"], codes(r, ids["sales-1"])), ("stale", ["node_retracted"]))

    async def test_attest_authorisation_and_state_errors(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, st = w.service, w.store
        await w.register("reader", team="logistics", scopes=["memory:read"])
        await w.settle()
        log1 = w.principal("log-1")
        ok = {"still_true": True}
        refused: list[tuple[Any, str, Any, type, str]] = [
            (w.admin, ids["log-1"], ok, Forbidden, "only the producing agent can attest a memory"),
            (w.principal("log-2"), ids["log-1"], ok, Forbidden, "only the producing agent can attest a memory"),
            (w.principal("reader"), ids["log-1"], ok, Forbidden, "missing scope memory:write"),
            (log1, ids["log-1"], {}, ValidationError, "'still_true' must be true or false"),
            (log1, ids["log-1"], {"still_true": "yes"}, ValidationError, "'still_true' must be true or false"),
            (log1, ids["log-1"], {"still_true": 1}, ValidationError, "'still_true' must be true or false"),
            (log1, ids["log-1"], {"still_true": True, "reason": "x" * 201}, ValidationError, "'reason' is longer than 200"),
            (log1, ids["log-1"], [], ValidationError, "body must be a JSON object"),
            (log1, "mem_nope", ok, NotFound, "mem_nope"),
            (log1, ids["sales-1"], ok, NotFound, ids["sales-1"]),             # another team's note: the same 404
            (log1, ids["team"], ok, ValidationError, "only a raw observation can be attested"),
        ]
        for principal, mid, body, exc, message in refused:
            with self.subTest(principal=principal.id, memory=mid, body=body):
                with self.assertRaises(exc) as ctx:
                    await s.attest(principal, mid, body)
                self.assertIn(message, str(ctx.exception))
        gone = await w.observe("log-1", "to be withdrawn", topic="ops:x")
        await w.settle()
        await s.retract(log1, gone, "withdrawn")
        await w.settle()
        with self.assertRaises(ValidationError) as ctx:
            await s.attest(log1, gone, ok)
        self.assertEqual(str(ctx.exception), "the memory is not active (it was superseded or retracted)")
        t0 = utcnow()
        soon = await w.observe("log-1", "short-lived", topic="ops:x", expires_at=iso(t0 + timedelta(seconds=5)))
        await w.settle()
        with self.assertRaises(ValidationError) as ctx:
            await s.attest(log1, soon, ok, now=t0 + timedelta(seconds=6))
        self.assertEqual(str(ctx.exception), "the memory has expired")
        await s.attest(log1, soon, ok, now=t0 + timedelta(seconds=1))
        await w.settle()
        self.assertEqual(st.get_memory(soon).expires_at, iso(t0 + timedelta(seconds=5)), "an attestation never extends an expiry")
        # a principal authenticated before its revocation committed: refused inside the transaction
        stale = w.principal("log-2")
        await s.revoke_agent("log-2")
        events = len(st.list_events(ORG, limit=10_000))
        for body in (ok, {"still_true": False}):
            with self.assertRaises(Forbidden) as ctx:
                await s.attest(stale, ids["log-2"], body)
            self.assertEqual(str(ctx.exception), "agent revoked")
        self.assertEqual(len(st.list_events(ORG, limit=10_000)), events)

    async def test_attestation_never_moves_backwards_and_after_retraction_is_ignored(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st, mid = w.service, w.store, ids["log-1"]
        t0 = ingested(st, mid)
        log1 = w.principal("log-1")
        later, earlier = iso(t0 + timedelta(minutes=10)), iso(t0 + timedelta(minutes=5))
        await s.attest(log1, mid, {"still_true": True}, now=t0 + timedelta(minutes=10))
        await s.attest(log1, mid, {"still_true": True}, now=t0 + timedelta(minutes=5))       # applied after: older
        await w.settle()
        self.assertEqual(st.get_memory(mid).attested_at, later, "an older attestation never moves it back")
        self.assertEqual([a["detail"]["reason"] for a in audits(st, "memory.attest_ignored")], ["attestation_not_newer"])
        self.assertEqual(s.metrics.events_ignored.labels("attestation_not_newer")._value.get(), 1)
        signed = digest_map(st)[mid]
        # the log may carry what the API never sends: each is applied without effect, audited and counted
        cases = {
            "before its ingest": {"memory_id": mid, "by": "log-1", "at": iso(t0 - timedelta(seconds=1))},
            "by another agent": {"memory_id": mid, "by": "log-2", "at": iso(t0 + timedelta(hours=1))},
            "of a derived memory": {"memory_id": ids["team"], "by": "mycelic", "at": iso(t0 + timedelta(hours=1))},
            "of no memory": {"memory_id": "mem_nope", "by": "log-1", "at": iso(t0 + timedelta(hours=1))},
            "without a time": {"memory_id": mid, "by": "log-1", "at": "soon"},
            "the same time again": {"memory_id": mid, "by": "log-1", "at": later},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                event = wire("memory.attested", payload)
                w.log.append(event)
                self.assertEqual(await s.apply_event(event, seq=len(w.log)), "ignored")
        self.assertEqual(st.get_memory(mid).attested_at, later)
        self.assertEqual(digest_map(st)[mid], signed)
        self.assertEqual(s.metrics.events_ignored.labels("attestation_target")._value.get(), 5)
        # an attestation that reaches the log after a retraction is ignored too
        attested, _ = await s.attest(w.principal("proc-1"), ids["proc-1"], {"still_true": True}, now=t0 + timedelta(hours=2))
        retracted = await s.retract(w.principal("proc-1"), ids["proc-1"], "withdrawn")
        for ev in (retracted, attested):                # the retraction gets into the log first
            async with st.transaction() as tx:
                tx.mark_published(ev.event_id, len(w.log) + 1)
            w.log.append(json.loads(json.dumps(s._event_wire(st.get_event(ev.event_id)))))
            await s.apply_event(w.log[-1], seq=len(w.log))
        await w.settle()
        self.assertEqual((st.get_memory(ids["proc-1"]).status, st.get_memory(ids["proc-1"]).attested_at), ("retracted", None))
        self.assertEqual(audits(st, "memory.attest_ignored")[0]["detail"], {"org_id": ORG, "reason": "attestation_target"})
        self.assertEqual(s.metrics.events_ignored.labels("attestation_target")._value.get(), 6)
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [])
        finally:
            await rebuilt.store.close()

    async def test_due_attestations_lists_only_contributing_own_notes(self) -> None:
        w = await self.world()
        ids = await demo(w)
        s, st = w.service, w.store
        log1 = w.principal("log-1")
        t0 = utcnow()
        solo = await w.observe("log-1", "Only I say this.", topic="ops:solo")
        expiring = await w.observe("log-1", "Gate 2 is closed today.", topic=TRANSPORT, expires_at=iso(t0 + timedelta(hours=1)))
        gone = await w.observe("log-1", "Gate 3 is closed today.", topic=TRANSPORT)
        await w.settle()
        await s.retract(log1, gone, "withdrawn")
        await w.settle()
        due = s.due_attestations(log1, now=t0 + timedelta(minutes=1))
        self.assertEqual([d["memory_id"] for d in due], sorted([ids["log-1"], expiring], key=lambda m: (
            st.get_event(st.get_memory(m).event_id).created_at, m)), "contributing, own, active: stalest first")
        self.assertEqual(set(due[0]), {"memory_id", "topic", "slot", "entity", "ingested_at", "attested_at", "expires_at",
                                       "dependents"})
        entry = next(d for d in due if d["memory_id"] == ids["log-1"])
        self.assertEqual((entry["topic"], entry["slot"], entry["entity"], entry["attested_at"]),
                         (TRANSPORT, "transport_disruption", "sd-9", None))
        self.assertEqual(entry["dependents"], 2, "the team consolidation and the conclusion")
        self.assertNotIn(solo, [d["memory_id"] for d in due], "nothing rests on it")
        self.assertEqual([d["memory_id"] for d in s.due_attestations(log1, now=t0 + timedelta(hours=2))], [ids["log-1"]],
                         "an expired note is not due")
        self.assertEqual([d["memory_id"] for d in s.due_attestations(w.principal("log-2"), now=t0)], [ids["log-2"]])
        self.assertEqual(s.due_attestations(w.principal("sales-2"), now=t0), [], "nothing of its own")
        # attested: no longer due until older_than has passed again
        await s.attest(log1, ids["log-1"], {"still_true": True}, now=t0 + timedelta(minutes=30))
        await w.settle()
        later = t0 + timedelta(minutes=45)
        self.assertEqual([d["memory_id"] for d in s.due_attestations(log1, older_than=1200, now=later)], [expiring])
        due = s.due_attestations(log1, older_than=600, now=later)
        self.assertEqual([d["memory_id"] for d in due], [expiring, ids["log-1"]], "the attested one is fresher")
        self.assertEqual(due[1]["attested_at"], iso(t0 + timedelta(minutes=30)))
        self.assertEqual(len(s.due_attestations(log1, limit=1, now=later)), 1)
        with self.assertRaises(Forbidden):
            s.due_attestations(w.admin)
        await w.register("writer", team="logistics", scopes=["memory:write"])
        with self.assertRaises(Forbidden):
            s.due_attestations(w.principal("writer"))
        for kwargs in ({"older_than": -1}, {"older_than": MAX_EXPIRY_SECONDS + 1}, {"older_than": True}, {"older_than": 1.5},
                       {"limit": 0}, {"limit": 501}, {"limit": "5"}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValidationError):
                    s.due_attestations(log1, **kwargs)

    async def test_attestation_replays_and_is_integrity_protected(self) -> None:
        w = await self.world(key=K)
        ids = await demo(w)
        s, st, mid = w.service, w.store, ids["log-1"]
        t0 = ingested(st, mid)
        await s.attest(w.principal("log-1"), mid, {"still_true": True}, now=t0 + timedelta(minutes=1))
        await s.attest(w.principal("proc-1"), ids["proc-1"], {"still_true": True}, now=t0 + timedelta(minutes=2))
        await w.settle()
        rebuilt = await rebuild(w.log, self.tmpdir(), event_signing_key=K)
        try:
            self.assertEqual(rebuild_differences(s, rebuilt), [], "attested_at and the re-signed digests")
            self.assertEqual(rebuilt.store.get_memory(mid).attested_at, iso(t0 + timedelta(minutes=1)))
        finally:
            await rebuilt.store.close()
        # an attested_at edited in the database: the digest covers it, and the log is not consulted for it
        for value in (iso(t0 + timedelta(days=30)), None):
            with self.subTest(attested_at=value):
                r = w.tampered(ids["conclusion"], [("UPDATE memories SET attested_at=? WHERE memory_id=?", (value, mid))])
                self.assertEqual((r["verdict"], codes(r, mid)), ("failed", ["integrity_mismatch"]))
        # a row whose digest does not check is never re-signed by an attestation
        st._conn.execute("UPDATE memories SET text=text||' (edited)' WHERE memory_id=?", (ids["sales-1"],))
        before = digest_map(st)[ids["sales-1"]]
        await s.attest(w.principal("sales-1"), ids["sales-1"], {"still_true": True}, now=t0 + timedelta(minutes=3))
        await w.settle()
        self.assertEqual((st.get_memory(ids["sales-1"]).attested_at, digest_map(st)[ids["sales-1"]]), (None, before))
        self.assertEqual(audits(st, "memory.attest_ignored")[0]["detail"]["reason"], "attestation_integrity")
        self.assertEqual(s.metrics.events_ignored.labels("attestation_integrity")._value.get(), 1)
        r = await s.verify(w.admin, ids["sales-1"])
        self.assertIn("integrity_mismatch", codes(r, ids["sales-1"]))


class AttestationSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.transport = HeldTransport(hold=False)
        self.h = await ServiceHarness(transport=self.transport).start()
        self.client = TestClient(TestServer(create_app(self.h.service)))
        await self.client.start_server()
        self.url = str(self.client.make_url("")).rstrip("/")
        self.ids = await demo(self.h)

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.h.close()

    async def rpc(self, key: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        r = await self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                   headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        self.assertEqual(r.status, 200)
        return (await r.json())["result"]

    async def test_attest_surfaces_http_mcp_sdk_proxy(self) -> None:
        s, st, ids = self.h.service, self.h.service.store, self.ids
        key = self.h.keys["log-1"]
        headers = {"Authorization": f"Bearer {key}"}
        # REST
        r = await self.client.post(f"/memory/{ids['log-1']}/attest", json={"still_true": True}, headers=headers)
        self.assertEqual(r.status, 202)
        body = await r.json()
        self.assertEqual((body["memory_id"], body["still_true"], body["status"]), (ids["log-1"], True, "accepted"))
        self.assertEqual(st.get_event(body["event_id"]).kind, "memory.attested")
        for path, json_body, auth, status, message in (
                (f"/memory/{ids['log-1']}/attest", {"still_true": "yes"}, headers, 400, "'still_true' must be true or false"),
                (f"/memory/{ids['log-1']}/attest", {"still_true": True}, {"Authorization": f"Bearer {ADMIN_TOKEN}"}, 403,
                 "only the producing agent can attest a memory"),
                (f"/memory/{ids['sales-1']}/attest", {"still_true": True}, headers, 404, "no visible memory with id"),
                (f"/memory/{ids['team']}/attest", {"still_true": True}, headers, 400, "only a raw observation")):
            r = await self.client.post(path, json=json_body, headers=auth)
            self.assertEqual(r.status, status, path)
            self.assertTrue((await r.json())["error"].startswith(message), await r.text())
        await self.h.settle()
        r = await self.client.get("/attestations/due?older_than=0&limit=10", headers={"Authorization": f"Bearer {self.h.keys['log-2']}"})
        self.assertEqual(r.status, 200)
        due = await r.json()
        self.assertEqual(([d["memory_id"] for d in due["due"]], due["older_than"], due["limit"]), ([ids["log-2"]], 0, 10))
        r = await self.client.get("/attestations/due", headers=headers)
        self.assertEqual(((await r.json())["older_than"], (await r.json())["limit"]), (0, 50))
        for query, message in (("older_than=-1", "'older_than' must be an integer"), ("older_than=1e3", "'older_than' must be an integer"),
                               ("limit=0", "'limit' must be an integer between 1 and 500"), ("limit=x", "'limit' must be an integer"),
                               (f"older_than={MAX_EXPIRY_SECONDS + 1}", "'older_than' must be an integer between 0 and")):
            r = await self.client.get(f"/attestations/due?{query}", headers=headers)
            self.assertEqual(r.status, 400, query)
            self.assertTrue((await r.json())["error"].startswith(message), query)
        r = await self.client.get("/attestations/due", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        self.assertEqual(r.status, 403)
        r = await self.client.get("/", headers=headers)
        self.assertTrue({"/memory/{id}/attest", "/attestations/due"} <= set((await r.json())["endpoints"]))
        # MCP: the seventh tool, its description and annotations, and the instructions
        await self.rpc(key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        tools = (await self.rpc(key, "tools/list", {}))["tools"]
        self.assertEqual([t["name"] for t in tools][-1], "mycelic_attest")
        self.assertEqual(len(tools), 7)
        [tool] = [t for t in tools if t["name"] == "mycelic_attest"]
        self.assertEqual(tool["annotations"], {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True,
                                               "openWorldHint": False})
        self.assertEqual((tool["inputSchema"]["required"], tool["inputSchema"]["properties"]["still_true"]),
                         (["memory_id"], {"type": "boolean", "default": True}))
        for phrase in ("Only the agent that produced the memory may attest it", "still_true=false retracts it",
                       "self-attestation: it adds freshness, not independent assurance"):
            self.assertIn(phrase, tool["description"])
        self.assertIn("re-attest it with mycelic_attest", INSTRUCTIONS)
        self.assertEqual([t["name"] for t in TOOLS][-1], "mycelic_attest")
        out = await self.rpc(key, "tools/call", {"name": "mycelic_attest", "arguments": {"memory_id": self.ids["log-1"]}})
        self.assertFalse(out["isError"], out)
        self.assertEqual((out["structuredContent"]["still_true"], out["structuredContent"]["status"]), (True, "accepted"))
        out = await self.rpc(key, "tools/call", {"name": "mycelic_attest", "arguments": {"memory_id": self.ids["sales-1"]}})
        self.assertTrue(out["isError"])
        self.assertIn("no visible memory", out["content"][0]["text"])
        # in-process MCP call with still_true=false: a retraction
        res = await mcp_call(s, self.h.principal("sales-1"), "mycelic_attest", memory_id=ids["sales-1"], still_true=False,
                             reason="cancelled")
        self.assertEqual(st.get_event(res["event_id"]).kind, "memory.retracted")
        # the SDK and the stdio proxy
        client = MycelicClient(self.url, self.h.keys["proc-1"], retries=0)
        res = await asyncio.to_thread(client.attest, ids["proc-1"])
        self.assertEqual(res["still_true"], True)
        await self.h.settle()
        self.assertEqual([d["memory_id"] for d in await asyncio.to_thread(client.due_attestations, older_than=3600)], [])
        with self.assertRaises(MycelicError) as ctx:
            await asyncio.to_thread(MycelicClient(self.url, self.h.keys["log-2"], retries=0).attest, ids["log-1"])
        self.assertEqual((ctx.exception.status, ctx.exception.message), (403, "only the producing agent can attest a memory"))
        proxy = mcp.ProxyTools(MycelicClient(self.url, self.h.keys["log-2"], retries=0))
        out = await proxy.call("mycelic_attest", {"memory_id": ids["log-2"], "still_true": True})
        self.assertEqual(out["still_true"], True)
        for args in ({"memory_id": ""}, {}):
            with self.assertRaises(mcp.ToolError):
                await proxy.call("mycelic_attest", args)
        await self.h.settle()
        self.assertIsNotNone(st.get_memory(ids["log-2"]).attested_at)
        self.assertEqual(st.get_memory(ids["sales-1"]).status, "retracted")
        self.assertEqual(json.loads(json.dumps(st.get_memory(ids["log-1"]).to_dict()))["attested_at"],
                         st.get_memory(ids["log-1"]).attested_at)
        self.assertEqual(invariant_violations(s, ORG), [])


if __name__ == "__main__":
    unittest.main()

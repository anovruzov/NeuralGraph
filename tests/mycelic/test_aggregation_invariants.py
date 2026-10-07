"""Aggregation as a fixed point, under random sequences of everything that can change it: agents registered (full,
sparse default-filled, a new region, a second organization) and revoked, notes shared with random labels (case and
whitespace variants) and retracted, rules added, changed, disabled, deleted, re-created and moved to another layer or
organization.

After every sequence (and, in one mode, after every single operation) the state must satisfy:

* I1  at most one active memory per (organization, operator, unit, key);
* I2  every active derived memory has parents, all present and active;
* I3  a consolidation stands for at least ``effective_min_support`` direct children of its unit (1 only when it promotes),
      its parents lie strictly inside the unit on the same topic, and their child units are ``metadata.children``;
* I4  ``support``/``independent_teams`` are the distinct agents/teams of ``metadata.roots``, and the roots are the
      union of the parents' roots;
* I5  soundness: re-planning every active derived memory reproduces its id, under an applied, enabled rule that covers
      its organization and targets its layer; completeness: wherever an operator holds on the applied evidence there is
      a current memory with the planned id, and nowhere else;
* I6  with ``min_support`` 1, every active topical note reaches the consolidation of every unit above it;
* I7  a rebuild of the log into a fresh store reproduces every memory's id, layer, status, support and version, its
      digest and the key that signed it, the lineage edges and the applied rules;
* I8  every memory's digest checks ``ok`` against the row as stored and its lineage edges (odd seeds sign with a key,
      even seeds run unkeyed);

and a full re-aggregation pass over the final state changes nothing.

``MYCELIC_INVARIANT_SEEDS`` sets the number of seeds per mode (the default keeps each test well under 30 s).
"""
from __future__ import annotations

import asyncio
import os
import random
import tempfile
import unittest
from typing import Any

from mycelic.metrics import Metrics
from mycelic.service import MycelicService, ValidationError
from mycelic.transport import InProcessTransport

from .helpers import (
    ADMIN_TOKEN, digest_violations, full_reaggregation_pass, invariant_violations, pump, rebuild, rebuild_differences, settings,
)

DEFAULT_SEEDS = {"each": 12, "end": 24}
OPS_PER_SEQUENCE = 36
ORGS = ("acme", "globex")
#: unit paths an agent can be registered at: full ones, a sparse one (acme/acme/acme/acme/t5), a second region (which
#: withdraws the enterprise's promotion of the first) and a second organization
PLACES = [
    {"enterprise": "acme", "region": "emea", "subsidiary": "gmbh", "department": "ops", "team": "t1"},
    {"enterprise": "acme", "region": "emea", "subsidiary": "gmbh", "department": "ops", "team": "t2"},
    {"enterprise": "acme", "region": "emea", "subsidiary": "gmbh", "department": "sales", "team": "t3"},
    {"enterprise": "acme", "team": "t5"},
    {"enterprise": "acme", "region": "apac", "subsidiary": "kk", "department": "ops", "team": "t4"},
    {"enterprise": "globex", "region": "eu", "subsidiary": "gx", "department": "ops", "team": "g1"},
    {"enterprise": "globex", "team": "g2"},
]
TOPICS = ["supply:a", "Supply:A ", "  SUPPLY:a", "supply:b", "ops:x", "OPS:X"]
SLOTS = [None, "s1", "S1", " s2 ", "s2", "s3"]
ENTITIES = [None, "e1", "E1 ", "e2"]
RULES: dict[str, dict[str, Any]] = {
    "r_team": {"rule_id": "r_team", "target_layer": "team", "required_slots": ["s1", "s2"], "min_agents": 2,
               "conclusion": "T {entity}: {slot:s1}", "emits_slot": "s3"},
    "r_dept": {"rule_id": "r_dept", "target_layer": "department", "required_slots": ["s1"], "corroborate": True,
               "min_agents": 2, "conclusion": "D {entity}", "topic_prefix": "supply:", "emits_slot": "s3"},
    "r_ent": {"rule_id": "r_ent", "target_layer": "enterprise", "required_slots": ["s3", "s1"], "min_agents": 2,
              "sources": ["slot_composition", "agent_observation", "topic_consolidation"], "conclusion": "E {entity}"},
}
SIGNING_KEY = "invariant-signing-key-0123456789abcdef"
LAYER_MOVES = {"r_team": ["team", "department"], "r_dept": ["department", "subsidiary", "team"], "r_ent": ["enterprise", "region"]}


class Sequence:
    """One seeded sequence of operations through the service's own entry points, applied by an inline consumer."""

    def __init__(self, seed: int, *, settle_each: bool) -> None:
        self.rnd = random.Random(seed)
        self.seed = seed
        self.settle_each = settle_each
        self.min_support = 1 + seed % 2
        self.signing_key = SIGNING_KEY if seed % 2 else None
        self.tmp = tempfile.TemporaryDirectory()
        self.service = MycelicService(settings(self.tmp.name, min_support=self.min_support, event_signing_key=self.signing_key),
                                      transport=InProcessTransport(), metrics=Metrics())
        self.log: list[dict[str, Any]] = []
        self.keys: dict[str, str] = {}
        self.principals: dict[str, Any] = {}
        self.notes: list[str] = []
        self.ops: list[str] = []

    @property
    def admin(self) -> Any:
        if "admin" not in self.principals:
            self.principals["admin"] = self.service.authenticate(f"Bearer {ADMIN_TOKEN}")
        return self.principals["admin"]

    def principal(self, agent_id: str) -> Any:
        if agent_id not in self.principals:           # authenticate() schedules a last-seen touch: once per agent
            self.principals[agent_id] = self.service.authenticate(f"Bearer {self.keys[agent_id]}")
        return self.principals[agent_id]

    def active_agents(self) -> list[str]:
        return sorted(a for a in self.keys if (agent := self.service.store.get_agent(a)) and agent.status == "active")

    async def register(self, place: dict[str, str] | None = None) -> str:
        place = place or self.rnd.choice(PLACES)
        agent_id = f"a{len(self.keys)}"
        _, key = await self.service.register_agent({**place, "agent_id": agent_id})
        self.keys[agent_id] = key
        return f"register {agent_id} at {'/'.join(place.values())}"

    async def observe(self, i: int) -> str:
        agents = self.active_agents()
        if not agents:
            return await self.register()
        agent_id = self.rnd.choice(agents)
        body: dict[str, Any] = {"text": f"note {i} by {agent_id}", "topic": self.rnd.choice(TOPICS),
                                "confidence": round(self.rnd.uniform(0.3, 0.95), 2), "idempotency_key": f"{self.seed}-{i}"}
        slot, entity = self.rnd.choice(SLOTS), self.rnd.choice(ENTITIES)
        if slot:
            body["slot"] = slot
        if entity:
            body["entity"] = entity
        m, _ = await self.service.ingest_memory(self.principal(agent_id), body)
        self.notes.append(m.memory_id)
        return f"observe {agent_id} {body['topic']!r} {slot!r} {entity!r} {body['confidence']}"

    async def retract(self) -> str:
        live = [mid for mid in self.notes if (m := self.service.store.get_memory(mid)) and m.status == "active"]
        if not live:
            return "retract (nothing to retract)"
        mid = self.rnd.choice(live)
        self.notes.remove(mid)
        await self.service.retract(self.admin, mid, "withdrawn")
        return f"retract {mid}"

    async def upsert_rule(self) -> str:
        rule_id = self.rnd.choice(sorted(RULES))
        body = dict(RULES[rule_id])
        body["min_agents"] = self.rnd.choice([1, 1, 2, 3])
        body["target_layer"] = self.rnd.choice(LAYER_MOVES[rule_id])
        body["org_id"] = self.rnd.choice([None, None, "acme", "globex"])
        body["enabled"] = self.rnd.random() > 0.2
        if self.rnd.random() < 0.3:
            body["conclusion"] = body["conclusion"] + " (revised)"
        if self.rnd.random() < 0.3:
            body["metadata"] = {"edited": self.rnd.randrange(3)}
        if self.rnd.random() < 0.3:              # conclusions on another topic: consolidated elsewhere above
            body["emits_topic"] = self.rnd.choice(["risk:t", "risk:u"])
        try:
            await self.service.upsert_rule(body)
        except ValidationError as exc:
            return f"upsert {rule_id} refused: {exc}"
        return (f"upsert {rule_id} layer={body['target_layer']} org={body['org_id']} min_agents={body['min_agents']} "
                f"enabled={body['enabled']} topic={body.get('emits_topic')} conclusion={body['conclusion']!r}")

    async def delete_rule(self) -> str:
        rule_id = self.rnd.choice(sorted(RULES))
        ok = await self.service.delete_rule(rule_id)
        return f"delete {rule_id} ({'deleted' if ok else 'absent'})"

    async def revoke(self) -> str:
        agents = self.active_agents()
        if len(agents) < 3:
            return await self.register()
        agent_id = self.rnd.choice(agents)
        await self.service.revoke_agent(agent_id)
        return f"revoke {agent_id}"

    async def run(self) -> list[str]:
        """The violations found, each prefixed with where (empty when every invariant held)."""
        s = self.service
        await s.transport.connect()
        try:
            # one department to start with, so every unit above it promotes until a registration adds a sibling
            for place in (PLACES[0], PLACES[0], PLACES[1]):
                self.ops.append(await self.register(place))
            self.ops.append(await self.upsert_rule())
            await pump(s, self.log)
            for i in range(OPS_PER_SEQUENCE):
                r = self.rnd.random()
                if r < 0.45:
                    self.ops.append(await self.observe(i))
                elif r < 0.57:
                    self.ops.append(await self.retract())
                elif r < 0.69:
                    self.ops.append(await self.register())
                elif r < 0.75:
                    self.ops.append(await self.revoke())
                elif r < 0.93:
                    self.ops.append(await self.upsert_rule())
                else:
                    self.ops.append(await self.delete_rule())
                if self.settle_each:
                    await pump(s, self.log)
                    bad = [v for org in ORGS for v in invariant_violations(s, org)] + digest_violations(s.store)
                    if bad:
                        return [f"after op {i}: {v}" for v in bad]
            await pump(s, self.log)
            bad = [f"settled: {v}" for org in ORGS for v in invariant_violations(s, org)]
            bad += [f"settled: {v}" for v in digest_violations(s.store)]
            rebuilt = await rebuild(self.log, self.tmp.name, min_support=self.min_support, event_signing_key=self.signing_key)
            try:
                bad += rebuild_differences(s, rebuilt)
                bad += [f"rebuilt: {v}" for v in digest_violations(rebuilt.store)]
            finally:
                await rebuilt.store.close()
            changed = await full_reaggregation_pass(s)
            if changed:
                bad.append(f"a full re-aggregation pass over the settled state changed {changed} memories")
            return bad
        finally:
            await asyncio.sleep(0)                   # let the last-seen touches that authenticate() scheduled finish
            await s.store.close()
            self.tmp.cleanup()

    def report(self, bad: list[str]) -> str:
        mode = "settled after every op" if self.settle_each else "settled at the end"
        ops = "\n".join(f"  {i - 4:>3}: {op}" for i, op in enumerate(self.ops))
        return (f"seed {self.seed}, {mode}, min_support {self.min_support}, {'keyed' if self.signing_key else 'unkeyed'}: "
                f"{len(bad)} violations\n"
                + "\n".join(bad[:12]) + f"\noperations (index -4..-1 are the set-up):\n{ops}")


def seeds(mode: str) -> range:
    return range(int(os.environ.get("MYCELIC_INVARIANT_SEEDS") or DEFAULT_SEEDS[mode]))


class RandomizedInvariantTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, *, settle_each: bool) -> None:
        for seed in seeds("each" if settle_each else "end"):
            seq = Sequence(seed, settle_each=settle_each)
            bad = await seq.run()
            self.assertEqual(bad, [], seq.report(bad))

    async def test_randomized_sequences_settled_each_op(self) -> None:
        await self.check(settle_each=True)

    async def test_randomized_sequences_settled_at_end(self) -> None:
        await self.check(settle_each=False)


if __name__ == "__main__":
    unittest.main()

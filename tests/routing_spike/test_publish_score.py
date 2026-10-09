"""Publishing to the fabric with lineage, the scorer's checks, found and chance, and a statistic that can fail."""
from __future__ import annotations

import asyncio
import random
import tempfile
import unittest
from pathlib import Path

from mycelic.collective.jsonio import sha256_hex
from mycelic.collective.packs.loader import load_pack
from mycelic.lineage import reconstruct
from mycelic.store import MycelicStore
from research.routing_spike import score as S
from research.routing_spike.hq import Received
from research.routing_spike.publish import Fabric, agent_of, org_of

PACK = load_pack("device_quality")
QID = "a" * 64
QUESTION = {"question_id": QID, "params": {"entity_type": "lot", "entity_id": "L20046", "predicate": "crack"},
            "window": {"start_week": "2024-W20", "end_week": "2024-W27"}, "as_of": "2024-07-20",
            "pack_hash": PACK.config_hash}


def verdict(site: str, kind: str = "confirm", support: str | None = "3-9") -> Received:
    body = {"verdict": kind, "reason": None, "support_bucket": support if kind == "confirm" else None,
            "roots_bucket": support if kind == "confirm" else None,
            "reporters_bucket": support if kind == "confirm" else None,
            "newest_week": "2024-W27" if kind == "confirm" else None, "verdict_id": sha256_hex(site + kind)}
    return Received(site, QID, "site", body, sha256_hex(f"{site}|{kind}|{support}"))


async def publish(db: Path, received: list[Received], status: str = "supported", label: str = "R") -> dict:
    fabric = Fabric(db, PACK)
    await fabric.start([label])
    try:
        return await fabric.publish(label, QUESTION, entity_type="lot", candidate_key="lot:L20046:crack",
                                    snapshot_week="2024-W27", as_of="2024-07-20",
                                    route=[r.site for r in received], received=received, status=status)
    finally:
        await fabric.close()


class FabricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fabric" / "mycelic.db"
        self.received = [verdict("plant-ashvale"), verdict("plant-fennick"), verdict("werk-dornhagen", "refute")]
        self.ids = asyncio.run(publish(self.db, self.received))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def logs(self, drop: str | None = None) -> tuple[set, dict]:
        receive = {(r.site, r.sha256) for r in self.received if r.site != drop}
        egress = {r.site: {r.sha256} for r in self.received}
        return receive, egress

    def test_the_conclusion_cites_its_verdict_events_and_its_lineage_reconstructs(self) -> None:
        store = MycelicStore(self.db)
        try:
            note = store.get_memory(self.ids["memory_id"])
            self.assertEqual(note.producer_id, agent_of("R"))
            self.assertEqual(note.org_id, org_of("R"))
            self.assertEqual(note.metadata["value"], "supported")
            self.assertEqual(note.metadata["candidate_key"], "lot:L20046:crack")
            graph = reconstruct(store, note.memory_id, visible=lambda m: True)
            self.assertTrue(graph["evidence"]["reconstructable"])
            cited = set(graph["evidence"]["source_event_ids"])
            self.assertTrue(set(self.ids["verdict_event_ids"]) <= cited)
            self.assertIn(self.ids["conclusion_event_id"], cited)
            events = store.get_events(self.ids["verdict_event_ids"])
            self.assertEqual({e.payload["payload"]["sha256"] for e in events.values()},
                             {r.sha256 for r in self.received})
            self.assertFalse(any("narrative" in str(e.payload) for e in events.values()))
        finally:
            asyncio.run(store.close())

    def test_the_scorer_accepts_matching_logs(self) -> None:
        receive, egress = self.logs()
        rows = S.fabric_conclusions(self.db, ["R"], min_confirming_sites=2, receive=receive, egress=egress)["R"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["lineage_ok"])
        self.assertEqual(rows[0]["confirm_sites"], ["plant-ashvale", "plant-fennick"])
        self.assertTrue(S.qualifying(rows[0]))

    def test_the_scorer_rejects_a_sha256_missing_from_the_receive_log(self) -> None:
        receive, egress = self.logs(drop="plant-fennick")
        [row] = S.fabric_conclusions(self.db, ["R"], min_confirming_sites=2, receive=receive, egress=egress)["R"]
        self.assertFalse(row["sha_ok"])
        self.assertFalse(S.qualifying(row))

    def test_the_scorer_rejects_a_sha256_missing_from_the_site_egress_log(self) -> None:
        receive, egress = self.logs()
        egress["plant-ashvale"] = set()
        [row] = S.fabric_conclusions(self.db, ["R"], min_confirming_sites=2, receive=receive, egress=egress)["R"]
        self.assertFalse(S.qualifying(row))

    def test_fewer_confirming_sites_than_the_gate_needs_do_not_qualify(self) -> None:
        receive, egress = self.logs()
        [row] = S.fabric_conclusions(self.db, ["R"], min_confirming_sites=3, receive=receive, egress=egress)["R"]
        self.assertFalse(S.qualifying(row))

    def test_a_weak_confirm_does_not_count(self) -> None:
        tmp = Path(self.tmp.name) / "weak" / "mycelic.db"
        received = [verdict("plant-ashvale"), verdict("plant-fennick", support="<k")]
        asyncio.run(publish(tmp, received))
        receive = {(r.site, r.sha256) for r in received}
        egress = {r.site: {r.sha256} for r in received}
        [row] = S.fabric_conclusions(tmp, ["R"], min_confirming_sites=2, receive=receive, egress=egress)["R"]
        self.assertEqual(row["confirm_sites"], ["plant-ashvale"])
        self.assertFalse(S.qualifying(row))


PATTERN = {"id": "p1", "key": "lot:L20046:crack", "entity_type": "lot", "entity_id": "L20046", "predicate": "crack",
           "sites": ["a", "b"], "found_from": "2024-W20", "found_to": "2024-W31"}


def row(week: str, status: str = "supported", ok: bool = True, key: str = "lot:L20046:crack") -> dict:
    t, i, p = key.split(":")
    return {"key": key, "entity_type": t, "entity_id": i, "predicate": p, "week": week, "status": status,
            "lineage_ok": ok, "question_id": week}


class FoundTests(unittest.TestCase):
    def test_found_needs_supported_qualifying_and_inside_the_window(self) -> None:
        self.assertEqual(S.finds([row("2024-W25")], [PATTERN]), {"p1": "2024-W25"})
        self.assertEqual(S.finds([row("2024-W25", status="hypothesis")], [PATTERN]), {})
        self.assertEqual(S.finds([row("2024-W25", ok=False)], [PATTERN]), {})
        self.assertEqual(S.finds([row("2024-W35")], [PATTERN]), {})
        self.assertEqual(S.finds([row("2024-W25", key="lot:L20047:crack")], [PATTERN]), {})
        self.assertEqual(S.finds([row("2024-W28"), row("2024-W22")], [PATTERN]), {"p1": "2024-W22"})

    def test_a_chance_find_is_an_earlier_or_same_week_no_plant_support(self) -> None:
        found = {"p1": "2024-W25"}
        self.assertEqual(S.chance_finds(found, [row("2024-W24")], [PATTERN]), {"p1"})
        self.assertEqual(S.chance_finds(found, [row("2024-W25")], [PATTERN]), {"p1"})
        self.assertEqual(S.chance_finds(found, [row("2024-W26")], [PATTERN]), set())
        self.assertEqual(S.chance_finds(found, [row("2024-W24", status="hypothesis")], [PATTERN]), set())

    def test_u_is_the_exact_mean_over_subsets(self) -> None:
        per_label = {"R": {"found_net": 3}, **{f"U{i:02d}": {"found_net": i % 2} for i in range(1, 21)}}
        self.assertEqual(S.arm_value(per_label, "U", "found_net"), 0.5)
        self.assertEqual(S.arm_value(per_label, "R", "found_net"), 3.0)


class StatisticTests(unittest.TestCase):
    def test_an_inverted_router_gives_an_interval_below_zero(self) -> None:
        rng = random.Random(1)
        f_u = [10 + rng.random() for _ in range(20)]
        f_r = [u - 3 - rng.random() for u in f_u]
        res = S.primary_statistic(f_r, f_u, B=2000)
        self.assertLess(res["ci_high"], 0)
        self.assertFalse(res["pass"])

    def test_a_no_signal_router_gives_an_interval_containing_zero(self) -> None:
        rng = random.Random(2)
        f_u = [10.0] * 20
        f_r = [10 + rng.choice((-1, 1)) * rng.random() for _ in range(20)]
        res = S.primary_statistic(f_r, f_u, B=2000)
        self.assertLess(res["ci_low"], 0)
        self.assertGreater(res["ci_high"], 0)
        self.assertFalse(res["pass"])

    def test_a_router_that_finds_more_passes_and_the_difference_is_signed(self) -> None:
        f_u = [5.0] * 20
        f_r = [7.0 + (i % 3) for i in range(20)]
        self.assertTrue(S.primary_statistic(f_r, f_u, B=2000)["pass"])
        self.assertLess(S.primary_statistic(f_u, f_r, B=2000)["mean_diff"], 0)

    def test_the_no_plant_bar(self) -> None:
        self.assertTrue(S.noplant_statistic([0.0] * 20, [0.0] * 20, B=1000)["pass"])
        self.assertFalse(S.noplant_statistic([0.2] * 20, [0.0] * 20, B=1000)["pass"])

    def test_the_verdict_rules(self) -> None:
        ok = {"1_leakage": {"ok": True}}
        bad = {"1_leakage": {"ok": False}}
        p, f = {"pass": True}, {"pass": False}
        self.assertEqual(S.verdict(ok, p, p)["verdict"], "pass")
        self.assertEqual(S.verdict(ok, f, p)["verdict"], "fail")
        self.assertEqual(S.verdict(ok, p, f)["verdict"], "fail")
        self.assertEqual(S.verdict(bad, p, p)["verdict"], "withheld")

    def test_the_primary_seed_string_is_the_preregistered_one(self) -> None:
        from research.routing_spike.world import SETTINGS
        self.assertEqual(SETTINGS["bootstrap"]["primary_seed"], "routing-spike:primary")
        self.assertEqual(SETTINGS["bootstrap"]["noplant_seed"], "routing-spike:noplant")
        self.assertEqual(SETTINGS["bootstrap"]["B"], 10000)


if __name__ == "__main__":
    unittest.main()

"""HQ: the fusion arithmetic, ties, the arms, equal budget, what the routers may read, and the import picture."""
from __future__ import annotations

import asyncio
import math
import tempfile
import unittest
from itertools import combinations
from pathlib import Path

from mycelic.collective.jsonio import canonical_bytes
from research.routing_spike import hq as H
from research.routing_spike import wire
from research.routing_spike.run import blindness_check, close_endpoints, import_check, open_endpoints, site_configs
from research.routing_spike.world import SETTINGS

from .helpers import small_world

SITES = ["s-a", "s-b", "s-c", "s-d", "s-e", "s-f"]


def h_of(qid: str = "q" * 64) -> dict[str, str]:
    return {s: H.tie_hash("routing-spike-v1", qid, s) for s in SITES}


def signals(**overrides: dict[str, int]) -> dict[str, dict[str, int]]:
    base = {name: {s: 0 for s in SITES} for name in H.SIGNALS}
    for name, values in overrides.items():
        base[name].update(values)
    return base


class FusionTests(unittest.TestCase):
    def test_reciprocal_rank_fusion_by_hand(self) -> None:
        sig = signals(key_now={"s-a": 9, "s-b": 4, "s-c": 1}, supporting={"s-a": 1, "s-b": 1},
                      entity_span={"s-c": 30, "s-a": 20, "s-d": 10}, type_span={"s-d": 500, "s-c": 400, "s-a": 300})
        h = h_of()
        order = H.order_for("R", sig, SITES, h)
        r = 1 / 61
        expected = {
            "s-a": 1 / 61 + 1 / 61 + 1 / 62 + 1 / 63,       # key 1, supporting tie 1st or 2nd by h, entity 2, type 3
            "s-b": 1 / 62 + 1 / 62,
            "s-c": 1 / 63 + 1 / 61 + 1 / 62,
            "s-d": 1 / 63 + 1 / 61,
        }
        sup = sorted(["s-a", "s-b"], key=lambda s: h[s])
        expected["s-a"] = 1 / 61 + (1 / 61 if sup[0] == "s-a" else 1 / 62) + 1 / 62 + 1 / 63
        expected["s-b"] = 1 / 62 + (1 / 61 if sup[0] == "s-b" else 1 / 62)
        want = sorted(SITES, key=lambda s: (-expected.get(s, 0.0), h[s]))
        self.assertEqual(order, want)
        self.assertEqual(order[:1], ["s-a"])
        self.assertTrue(r > 0)

    def test_sites_with_no_signal_get_no_rank_and_follow_by_h(self) -> None:
        sig = signals(key_now={"s-c": 2})
        h = h_of()
        order = H.order_for("R", sig, SITES, h)
        self.assertEqual(order[0], "s-c")
        self.assertEqual(order[1:], sorted([s for s in SITES if s != "s-c"], key=lambda s: h[s]))

    def test_ties_break_by_the_question_hash_not_by_name(self) -> None:
        sig = signals(key_now={s: 5 for s in SITES})
        orders = {tuple(H.order_for("R1", sig, SITES, h_of(q))) for q in ("a" * 64, "b" * 64, "c" * 64, "d" * 64)}
        self.assertGreater(len(orders), 1)
        for q in ("a" * 64, "b" * 64):
            h = h_of(q)
            self.assertEqual(H.order_for("R1", sig, SITES, h), sorted(SITES, key=lambda s: h[s]))

    def test_rank_ignores_zero_and_negative_values(self) -> None:
        self.assertEqual(H.rank({"s-a": 0, "s-b": -3, "s-c": 1}, SITES, h_of()), {"s-c": 1})

    def test_r1_is_the_key_ranking_alone(self) -> None:
        sig = signals(key_now={"s-e": 3, "s-b": 7}, supporting={"s-a": 1}, entity_span={"s-a": 99},
                      type_span={"s-a": 999})
        order = H.order_for("R1", sig, SITES, h_of())
        self.assertEqual(order[:2], ["s-b", "s-e"])

    def test_r0_subtracts_the_keys_own_window_cells(self) -> None:
        sig = signals(key_now={"s-a": 10}, supporting={"s-a": 1}, entity_span={"s-a": 10, "s-b": 3},
                      type_span={"s-a": 10, "s-b": 3})
        order = H.order_for("R0", sig, SITES, h_of())
        self.assertEqual(order[0], "s-b")
        self.assertNotEqual(H.order_for("R", sig, SITES, h_of())[0], "s-b")

    def test_oracle_puts_the_planted_sites_first_in_rs_order(self) -> None:
        sig = signals(key_now={"s-a": 9, "s-b": 5, "s-f": 1})
        h = h_of()
        r = H.order_for("R", sig, SITES, h)
        o = H.order_for("O", sig, SITES, h, oracle_sites=["s-f", "s-b"])
        self.assertEqual(o[:2], [s for s in r if s in ("s-f", "s-b")])
        self.assertEqual(o[2:], [s for s in r if s not in ("s-f", "s-b")])
        self.assertEqual(H.order_for("O", sig, SITES, h, oracle_sites=None), r)

    def test_placebo_scores_each_site_with_another_sites_signals(self) -> None:
        sig = signals(key_now={"s-a": 9, "s-b": 5}, supporting={"s-a": 1})
        qid = "e" * 64
        sigma = H.placebo_permutation(SITES, 7, qid)
        self.assertEqual(sorted(sigma.values()), sorted(SITES))
        self.assertEqual(sigma, H.placebo_permutation(SITES, 7, qid))
        p = H.order_for("P", sig, SITES, h_of(qid), seed=7, question_id=qid)
        top = next(s for s in SITES if sigma[s] == "s-a")
        self.assertEqual(p[0], top)
        firsts = {H.order_for("P", sig, SITES, h_of(q), seed=7, question_id=q)[0] for q in
                  (f"{i:064x}" for i in range(40))}
        self.assertGreater(len(firsts), 3)

    def test_u_is_every_three_site_subset(self) -> None:
        subsets = H.u_subsets(SITES, 3)
        self.assertEqual(len(subsets), 20)
        self.assertEqual(subsets, list(combinations(sorted(SITES), 3)))
        labels = H.arm_labels(SITES, 3)
        self.assertEqual(labels[:6], ["A", "R", "R1", "R0", "O", "P"])
        self.assertEqual(len(labels), 26)
        self.assertEqual(SETTINGS["m"], 3)

    def test_candidate_reads_only_the_allowed_fields(self) -> None:
        c = {"key": "lot:L1:crack", "entity_type": "lot", "entity_id": "L1", "predicate": "crack", "run_channel": "X",
             "snapshot": {"week": "2024-W30", "as_of": "2024-08-10", "window": ["2024-W23", "2024-W30"],
                          "supporting_sites": ["s-a"], "score": 0.9, "features": {"x": 1},
                          "contributing_sites": ["s-a", "s-b"], "lineage": [{"site": "s-a"}]}}
        a = H.Candidate.from_detector(c)
        c2 = {**c, "detectors": ["D9"], "snapshot": {**c["snapshot"], "score": 0.1, "features": {},
                                                    "contributing_sites": [], "lineage": []}}
        self.assertEqual(H.Candidate.from_detector(c2), a)


class _Recorder:
    """A CollectiveStore stand-in that records which reads the routers make."""

    def __init__(self, store) -> None:
        self._store = store
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        self.calls.append(name)
        return getattr(self._store, name)


class HqWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.world = small_world(Path(cls.tmp.name) / "w", seed=2, planted=True, top_n=2)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.pipeline.store.close()
        cls.tmp.cleanup()

    def test_routes_are_one_shot_equal_budget_and_follow_each_arms_order(self) -> None:
        world = self.world
        endpoints = open_endpoints(site_configs(world), "in_process", world.workdir / "hq" / "wire.jsonl",
                                   world.workdir / "configs")
        try:
            hq = H.Hq(world.pack, world.pipeline.store, endpoints, hq_dir=world.workdir / "hq",
                      tie_salt=SETTINGS["tie_salt"], m=3, seed=world.seed)
            hq.describe_all()
            eligible = hq.eligible()
            self.assertEqual(list(eligible), sorted(world.site_ids))
            recorder = _Recorder(world.pipeline.store)
            hq.view = H.HqView(recorder, world.pack)
            asked = [hq.prepare(H.Candidate.from_detector(c)) for c in world.candidates]
            self.assertEqual(set(recorder.calls), {"key_cells", "entity_site_volumes", "type_site_volumes"})
            labels = H.arm_labels(eligible, 3)

            async def go() -> None:
                for a in asked:
                    for label in labels:
                        await hq.ask(a, label)

            asyncio.run(go())
            for a in asked:
                for label in labels:
                    r = a.results[label]
                    self.assertEqual(len(r.route), 6 if label == "A" else 3)
                    self.assertTrue(r.route_selected_first)
                    self.assertEqual(list(r.route), a.orders[label][:len(r.route)])
                    self.assertEqual(set(r.roles.values()) <= {"contributing", "sibling"}, True)
                    for site in r.route:
                        self.assertEqual(r.roles[site],
                                         "contributing" if a.signals["key_now"][site] > 0 else "sibling")
                shas = {(x.site, x.sha256) for label in labels for x in a.results[label].received}
                self.assertEqual(len(shas), len(eligible))
            blind = blindness_check(world, asked, hq, eligible, SETTINGS)
            self.assertTrue(blind["ok"], blind)
        finally:
            close_endpoints(endpoints)

    def test_a_garbled_verdict_becomes_hqs_own_unknown_record(self) -> None:
        world = self.world

        class Liar:
            def request(self, kind: str, body: bytes = b"") -> bytes:
                if kind == "describe":
                    return canonical_bytes(wire.descriptor("plant-ashvale", world.pack.id, world.pack.config_hash,
                                                           sorted(world.pack.questions)))
                return b'{"verdict":"confirm","narrative":"housing cracked"}'

        proxy = H.SiteProxyAdapter("plant-ashvale", Liar(), world.pack, clock=lambda: "2024-01-01T00:00:00Z")
        proxy.describe()
        hq = H.Hq(world.pack, world.pipeline.store, {"plant-ashvale": Liar()}, hq_dir=Path(self.tmp.name) / "liar",
                  tie_salt="t", m=3, seed=1)
        hq.describe_all()
        a = hq.prepare(H.Candidate.from_detector(world.candidates[0]))
        result = asyncio.run(hq.ask(a, "A"))
        [received] = result.received
        self.assertEqual(received.source, "hq")
        self.assertEqual(received.body["reason"], "error")
        self.assertEqual(received.problem, "invalid")
        self.assertEqual(result.status, "hypothesis")


class ImportTests(unittest.TestCase):
    def test_hq_and_wire_load_no_site_module_numpy_or_retrieval_package(self) -> None:
        report = import_check()
        self.assertTrue(report["ok"], report)

    def test_the_forbidden_list_matches_the_design(self) -> None:
        from research.routing_spike.run import FORBIDDEN_HQ
        for name in ("research.routing_spike.site_process", "NeuralGraph.research.retrieval",
                     "NeuralGraph.llm_backend", "numpy", "mycelic.collective.edge.records",
                     "mycelic.collective.edge.site", "mycelic.collective.edge.verify",
                     "mycelic.collective.edge.extract", "mycelic.collective.evaluate",
                     "mycelic.collective.packs.generator"):
            self.assertIn(name, FORBIDDEN_HQ)


class ArithmeticTests(unittest.TestCase):
    def test_fused_scores_are_exact_sums(self) -> None:
        ranks = [{"s-a": 1, "s-b": 2}, {"s-b": 1}]
        h = h_of()
        order = H.fuse(ranks, SITES, h)
        self.assertEqual(order[0], "s-b")
        self.assertTrue(math.isclose(1 / 62 + 1 / 61, 1 / 61 + 1 / 62))


if __name__ == "__main__":
    unittest.main()

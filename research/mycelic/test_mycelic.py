"""Tests for the Mycelic enterprise benchmark.

Run: python3 -m unittest research.mycelic.test_mycelic -v

These check the properties the study's conclusions depend on:
  * determinism (a seed reproduces a run exactly)
  * the leakage contract (no ground-truth field reaches a system)
  * the fairness contract (one operator implementation for everyone)
  * generator invariants (heterogeneity, locality, decoy balance)
  * metric sanity (controls behave the way controls must)
"""
from __future__ import annotations

import unittest

import numpy as np

from .corpus import (CAUSAL_CHAINS, KIND_FACET, PREDICATES, PRED_ID,
                     build_corpus, make_gold)
from .evalm import evaluate
from .models import ANCHORS, allocation, tier_from_q
from .ops import ExtractResult, _chain_span, extract, stem_rep_map
from .org import DEPT, REGION, SITE, TEAM, USER, build_org
from .runner import build_world, run_arch
from .systems import HierConfig, HierRunner, map_reduce, oracle_retrieval

SMALL = 1200


class TestOrg(unittest.TestCase):
    def test_deterministic(self):
        a, b = build_org(SMALL, seed=3), build_org(SMALL, seed=3)
        self.assertEqual(len(a.user_ids), len(b.user_ids))
        self.assertEqual([n.parent for n in a.nodes], [n.parent for n in b.nodes])

    def test_six_levels_populated(self):
        o = build_org(10_000, seed=0)
        for lvl in (USER, TEAM, DEPT, SITE, REGION):
            self.assertGreater(len(o.levels[lvl]), 0, f"level {lvl} empty")
        self.assertEqual(len(o.levels[5]), 1)

    def test_heterogeneous_fanin(self):
        """A balanced k-ary tree would make the whole study meaningless."""
        o = build_org(50_000, seed=0)
        for lvl in (TEAM, DEPT, SITE):
            fan = [len(o.nodes[n].children) for n in o.levels[lvl]]
            self.assertGreater(np.std(fan), 1.0, f"level {lvl} too uniform")
            self.assertGreater(max(fan), 2 * int(np.median(fan)),
                               f"level {lvl} has no large nodes")

    def test_every_user_reaches_the_root(self):
        o = build_org(5_000, seed=1)
        for u in o.user_ids[::97]:
            self.assertEqual(o.ancestor_at(u, 5), o.root)
            for lvl in (TEAM, DEPT, SITE, REGION):
                self.assertIsNotNone(o.ancestor_at(u, lvl))


class TestCorpus(unittest.TestCase):
    def setUp(self):
        self.org = build_org(SMALL, seed=0)
        self.c = build_corpus(self.org, seed=0)
        self.gold = make_gold(self.c)

    def test_deterministic(self):
        c2 = build_corpus(build_org(SMALL, seed=0), seed=0)
        self.assertTrue(np.array_equal(self.c.recs["pred"], c2.recs["pred"]))
        self.assertTrue(np.array_equal(self.c.recs["anchor"], c2.recs["anchor"]))
        self.assertEqual(self.c.text(17), c2.text(17))

    def test_patterns_span_multiple_regions(self):
        """If a pattern were visible inside one branch it would not be
        testing cross-organisational discovery at all."""
        for p in self.c.patterns:
            if p.real and p.pid in set(self.gold.discoverable):
                self.assertGreaterEqual(p.n_regions, 2, f"pattern {p.pid}")

    def test_no_single_user_holds_a_whole_pattern(self):
        for p in self.c.patterns:
            if not p.real:
                continue
            per_user = {}
            for j, fr in enumerate(p.facet_records):
                for r in fr:
                    per_user.setdefault(int(self.c.recs["uid"][r]), set()).add(j)
            for u, facets in per_user.items():
                self.assertLess(len(facets), max(2, len(p.preds)),
                                f"user {u} holds most of pattern {p.pid}")

    def test_all_five_decoy_classes_present(self):
        kinds = {p.decoy_type for p in self.c.patterns if not p.real}
        self.assertEqual(kinds, {1, 2, 3, 4, 5})

    def test_echoes_repeat_wording_independents_do_not(self):
        recs = self.c.recs
        ev = {}
        for i in range(len(recs)):
            ev.setdefault(int(recs["event"][i]), []).append(i)
        shared = [v for v in ev.values() if len(v) > 1][:40]
        self.assertTrue(shared, "no echoed events in the corpus")
        sims = []
        for grp in shared:
            a = set(self.c.tokens(grp[0]))
            b = set(self.c.tokens(grp[1]))
            sims.append(len(a & b) / max(1, len(a | b)))
        self.assertGreater(float(np.mean(sims)), 0.7,
                           "echoes should be near-duplicates")
        rng = np.random.default_rng(0)
        other = []
        for _ in range(60):
            i, j = rng.integers(0, len(recs), 2)
            if recs["event"][i] == recs["event"][j]:
                continue
            a, b = set(self.c.tokens(int(i))), set(self.c.tokens(int(j)))
            other.append(len(a & b) / max(1, len(a | b)))
        self.assertLess(float(np.mean(other)), 0.55,
                        "independent records should not look like duplicates")

    def test_entity_locality(self):
        """Most entities must belong somewhere, or index routing is a
        giveaway on one side and useless on the other."""
        reg = np.array([self.org.ancestor_at(int(u), REGION)
                        for u in self.c.recs["uid"]])
        from collections import defaultdict
        d = defaultdict(set)
        for a, r in zip(self.c.recs["anchor"].tolist(), reg.tolist()):
            d[a].add(r)
        multi = sum(1 for v in d.values() if len(v) >= 2)
        frac = multi / max(1, len(d))
        self.assertGreater(frac, 0.30, "entities too local; triage trivial")
        gold_anchors = {self.c.patterns[p].anchor for p in self.gold.discoverable}
        self.assertLess(len(gold_anchors) / max(1, multi), 0.5,
                        "multi-region entities are almost all gold: giveaway")


class TestOperators(unittest.TestCase):
    def setUp(self):
        self.w = build_world(SMALL, 0)

    def test_extraction_output_carries_no_ground_truth(self):
        """The leakage contract: an operator may READ truth to simulate noise,
        but nothing it returns may expose it."""
        rng = np.random.default_rng(0)
        ex = extract(self.w.corpus, np.arange(500), ANCHORS["small-7b"], rng,
                     near_miss=self.w.near_miss)
        for f in ("kind", "group", "facet", "veracity", "salience",
                  "superseded"):
            self.assertFalse(hasattr(ex, f), f"ExtractResult exposes {f}")
        self.assertEqual(set(ex.__dataclass_fields__),
                         {"rid", "uid", "pred", "anchor", "t", "polarity",
                          "sig", "spurious"})

    def test_stronger_tier_extracts_better(self):
        rng = np.random.default_rng(1)
        lo = extract(self.w.corpus, np.arange(3000), ANCHORS["edge-3b"], rng,
                     near_miss=self.w.near_miss)
        rng = np.random.default_rng(1)
        hi = extract(self.w.corpus, np.arange(3000), ANCHORS["frontier-plus"],
                     rng, near_miss=self.w.near_miss)
        self.assertGreater(len(hi), len(lo))

    def test_entity_errors_are_per_agent_not_per_mention(self):
        """The same agent must read the same entity the same way every time."""
        c = self.w.corpus
        rng = np.random.default_rng(0)
        ex = extract(c, np.arange(len(c.recs)), ANCHORS["small-7b"], rng,
                     near_miss=self.w.near_miss)
        from collections import defaultdict
        seen = defaultdict(set)
        for i in range(len(ex)):
            if ex.spurious[i]:
                continue          # an invented claim has no true anchor
            true_anchor = int(c.recs["anchor"][int(ex.rid[i])])
            seen[(int(ex.uid[i]), true_anchor)].add(int(ex.anchor[i]))
        inconsistent = sum(1 for v in seen.values() if len(v) > 1)
        self.assertEqual(inconsistent, 0,
                         "an agent resolved the same entity two different ways")

    def test_chain_span(self):
        ch = CAUSAL_CHAINS[0]
        mask = 0
        for p in ch[:4]:
            mask |= 1 << PRED_ID[p]
        span, ci = _chain_span(mask)
        self.assertEqual(span, 4)
        self.assertEqual(ci, 0)
        self.assertEqual(_chain_span(0)[0], 0)

    def test_capability_vector_is_monotone_in_q(self):
        prev = None
        for q in np.linspace(0.05, 1.0, 12):
            t = tier_from_q(float(q))
            if prev is not None:
                self.assertGreaterEqual(t.extract_recall, prev.extract_recall - 1e-9)
                self.assertGreaterEqual(t.causal_check, prev.causal_check - 1e-9)
                self.assertLessEqual(t.hallucination, prev.hallucination + 1e-9)
            prev = t


class TestSystems(unittest.TestCase):
    def setUp(self):
        self.w = build_world(SMALL, 0)
        self.alloc = allocation("back-loaded")

    def test_runs_are_deterministic(self):
        a = run_arch("H_mycelic_full", self.w, self.alloc, 0)
        b = run_arch("H_mycelic_full", self.w, self.alloc, 0)
        ma = evaluate(self.w.corpus, self.w.gold, a)
        mb = evaluate(self.w.corpus, self.w.gold, b)
        for k in ("average_precision", "found_anywhere_in_register",
                  "compute_units", "tokens_total"):
            self.assertAlmostEqual(ma[k], mb[k], places=9, msg=k)

    def test_hierarchy_never_moves_raw_text_off_the_user_node(self):
        r = run_arch("H_mycelic_full", self.w, self.alloc, 0)
        self.assertEqual(r.exposed_raw_records, 0)

    def test_flat_baselines_do_move_raw_text(self):
        r = run_arch("A_flat_rag", self.w, self.alloc, 0)
        self.assertGreater(r.exposed_raw_records, 0)

    def test_random_rank_has_the_same_candidates_as_its_parent(self):
        base = run_arch("B2_map_reduce", self.w, self.alloc, 0)
        rr = run_arch("Z_random_rank", self.w, self.alloc, 0)
        self.assertEqual(sorted(h.anchor for h in base.hypotheses),
                         sorted(h.anchor for h in rr.hypotheses))
        mb = evaluate(self.w.corpus, self.w.gold, base)
        mr = evaluate(self.w.corpus, self.w.gold, rr)
        self.assertAlmostEqual(mb["found_anywhere_in_register"],
                               mr["found_anywhere_in_register"], places=9)

    def test_oracle_has_near_total_evidence_coverage(self):
        r = run_arch("Y_oracle_retrieval", self.w, self.alloc, 0)
        m = evaluate(self.w.corpus, self.w.gold, r)
        self.assertGreater(m["evidence_coverage_2links"], 0.9)

    def test_every_call_is_metered(self):
        r = run_arch("H_mycelic_full", self.w, self.alloc, 0)
        d = r.meter.as_dict()
        self.assertGreater(d["calls"], 0)
        self.assertGreater(d["tok_in"], 0)
        self.assertGreater(d["cu"], 0)
        for stage, v in d["by_stage"].items():
            if v["calls"]:
                self.assertGreater(v["tok_in"], 0, f"{stage} unmetered")

    def test_kernel_context_is_comparable_across_architectures(self):
        ctx = {}
        for a in ("B2_map_reduce", "E_hier_lineage", "H_mycelic_full"):
            r = run_arch(a, self.w, self.alloc, 0)
            ctx[a] = evaluate(self.w.corpus, self.w.gold, r)["kernel_context_tokens"]
        vals = [v for v in ctx.values() if v > 0]
        self.assertLess(max(vals) / max(1.0, min(vals)), 6.0,
                        f"kernel contexts differ wildly: {ctx}")

    def test_ablating_lineage_destroys_lineage_accuracy(self):
        cfg = HierConfig(lineage=False, independence=False,
                         downward_retrieval=False, questions=False)
        r = HierRunner(self.w.corpus, self.alloc, cfg, seed=0,
                       ul=self.w.user_layer(self.alloc[USER], 0),
                       near_miss=self.w.near_miss).run()
        m = evaluate(self.w.corpus, self.w.gold, r)
        self.assertEqual(m["lineage_accuracy"], 0.0)

    def test_descent_does_not_broadcast(self):
        """A targeted descent must touch a bounded number of nodes, and that
        bound must NOT grow with the enterprise - otherwise the downward path
        is a broadcast with extra steps.  Checked at 10k, where the org is big
        enough for the claim to mean something."""
        w = build_world(10_000, 0)
        cfg = HierConfig()
        runner = HierRunner(w.corpus, self.alloc, cfg, seed=0,
                            ul=w.user_layer(self.alloc[USER], 0),
                            near_miss=w.near_miss)
        self.w = w
        ul = runner._ul
        h = runner.h
        h.build_user_layer(ul)
        for lvl in (TEAM, DEPT, SITE, REGION):
            h.aggregate_level(lvl, ul)
        before = h.meter.levels.get("descend-read")
        n_before = before.calls if before else 0
        anchor = w.corpus.patterns[w.gold.discoverable[0]].anchor
        h.descend(int(anchor), [], budget_nodes=3)
        after = h.meter.levels["descend-read"].calls - n_before
        self.assertLessEqual(after, cfg.descent_leaf_cap,
                             "descent exceeded its own leaf cap")
        self.assertLess(after, 0.02 * len(w.org.user_ids),
                        "descent touched too much of the organisation")


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.w = build_world(SMALL, 0)
        self.alloc = allocation("back-loaded")

    def test_empty_report_scores_zero_not_nan(self):
        from .systems import RunResult, Meter
        r = RunResult(name="empty", hypotheses=[], meter=Meter(),
                      retained=set(), kernel_kos=[])
        m = evaluate(self.w.corpus, self.w.gold, r)
        for k, v in m.items():
            self.assertFalse(np.isnan(v), f"{k} is NaN")
        self.assertEqual(m["discovery_recall"], 0.0)
        self.assertEqual(m["average_precision"], 0.0)

    def test_strict_match_never_exceeds_primary(self):
        for a in ("A_flat_rag", "H_mycelic_full", "B2_map_reduce"):
            r = run_arch(a, self.w, self.alloc, 0)
            m = evaluate(self.w.corpus, self.w.gold, r)
            self.assertLessEqual(m["discovery_recall_strict"],
                                 m["discovery_recall"] + 1e-9, a)

    def test_found_is_at_least_recall_at_100(self):
        for a in ("A_flat_rag", "H_mycelic_full"):
            r = run_arch(a, self.w, self.alloc, 0)
            m = evaluate(self.w.corpus, self.w.gold, r)
            self.assertGreaterEqual(m["found_anywhere_in_register"] + 1e-9,
                                    m["recall_at_100"], a)



class TestReportGenerators(unittest.TestCase):
    """The committed documents must regenerate from the committed artifacts."""

    def test_missing_architecture_is_never_reported_as_zero(self):
        from . import findings
        rows = findings._rows()
        ladder = ("C_recursive_sum", "D_hier_nolineage", "E_hier_lineage",
                  "F_hier_retrieval", "G_hier_questions")
        sc = findings._largest_complete_scale(rows, ladder)
        self.assertIsNotNone(sc)
        for a in ladder:
            self.assertTrue(any(r["arch"] == a and r["scale"] == sc for r in rows), a)
        text = findings.executive_summary()
        for s in sorted({r["scale"] for r in rows}):
            missing = [a for a in ladder
                       if not any(r["arch"] == a and r["scale"] == s for r in rows)]
            if missing:
                self.assertNotIn(f"At {s:,} users", text.split("**2.")[0])

    def test_loss_doc_ranker_section_survives_a_fresh_clone(self):
        import glob
        import os
        from . import loss_doc
        from .runner import ART
        if glob.glob(os.path.join(ART, "hyp_features_final*.jsonl")):
            self.skipTest("final feature dumps present; the fallback is not exercised")
        sec = loss_doc.calibrator_section()
        self.assertNotIn("not yet complete", sec)
        self.assertIn("_finalH", sec)
        self.assertIn("_finalC", sec)

    def test_calibrator_cli_passes_dump_tags(self):
        # drives the real command-line paths (a `store` that passed tag= once
        # crashed with a TypeError), with the fitting itself mocked out
        from unittest import mock
        from . import calibrator
        fitted = {"n_train": 0, "features": [], "weights": []}
        selected = {"selection_grid": [], "l2": 1.0, "interactions": True, "n_train": 0}
        with mock.patch.object(calibrator, "fit_and_store", return_value=fitted) as fit, \
                mock.patch.object(calibrator, "select_and_store", return_value=selected) as sel, \
                mock.patch("builtins.print"):
            self.assertEqual(calibrator.main(["calibrator", "store", "_a,_b"]), 0)
            fit.assert_called_once_with(tags=("_a", "_b"))
            self.assertEqual(calibrator.main(["calibrator", "store"]), 0)
            self.assertEqual(fit.call_args, mock.call(tags=("",)))
            self.assertEqual(calibrator.main(["calibrator", "select", "_hybH,_v3C,_J"]), 0)
            sel.assert_called_once_with(tags=("_hybH", "_v3C", "_J"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

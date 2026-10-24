"""PROPOSAL ONLY (F-H7, wave 2) -- the test that would accompany gate_veto.patch as tests/mycelic/test_pushdown_h7.py.
Not placed in any repository. Mirrors tests/mycelic/test_pushdown_n1.py.

EMERGENCE-Next H7-1 (``pushdown.echo_veto_bucket_gap``): a confirm whose support bucket sits above its roots bucket by
at least the gap is not counted. These pin that the veto is opt-in (a pack without the key keeps its bytes, its config
hash and the shipped gate), that a vetoed site is neither counted nor an N1 corroborator, that it never contests, that
the note names it, and that the gap compares bucket INDICES (so '3-9' records over '3-9' roots -- a 3:1 echo ratio hidden
by the bucket -- is not vetoed: the granularity limit Kill 4 measured)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mycelic.collective.edge.egress import SUPPRESSED
from mycelic.collective.packs.loader import PackError
from mycelic.collective.pushdown.gate import GateParams, VerdictRecord, evaluate

from tests.mycelic.test_collective_edge import pack_copy
from tests.mycelic.test_collective_pushdown import AS_OF, DQ, _q, verdict_body

ROUTES = {"s1": "contributing", "s2": "contributing", "s3": "contributing"}


def confirm(site, pack=DQ, support="3-9", roots="3-9", reporters="3-9"):
    q = _q(pack)
    return VerdictRecord(site, 1, "site", verdict_body(site, "confirm", q, pack_hash=pack.config_hash,
                                                       support_bucket=support, roots_bucket=roots,
                                                       reporters_bucket=reporters), AS_OF)


class H7EchoVetoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.veto = pack_copy(self.tmp, "device_quality", {("questions.json", "pushdown", "echo_veto_bucket_gap"): 1})
        self.veto_n1 = pack_copy(self.tmp, "device_quality", {("questions.json", "pushdown", "echo_veto_bucket_gap"): 1,
                                                              ("questions.json", "pushdown", "weak_corroborating_sites"): 1})

    def gate(self, pack, records):
        return evaluate(pack, _q(pack), ROUTES, records, as_of=AS_OF)

    def test_shipped_gate_is_unchanged_without_the_key(self) -> None:
        self.assertEqual(GateParams.from_pack(DQ).echo_veto_bucket_gap, 0)
        self.assertNotIn("echo_veto_bucket_gap", GateParams.from_pack(DQ).to_dict())
        self.assertEqual(pack_copy(self.tmp, "device_quality").config_hash, DQ.config_hash)
        result = self.gate(DQ, [confirm("s1", support="10-49", roots="3-9"), confirm("s2")])
        self.assertEqual(result.status, "supported")
        self.assertNotIn("echo_vetoed_sites", result.to_dict()["support"])
        self.assertTrue(all("echo_vetoed" not in u for u in result.to_dict()["used"]))

    def test_veto_is_a_different_pack(self) -> None:
        self.assertNotEqual(self.veto.config_hash, DQ.config_hash)
        self.assertEqual(GateParams.from_pack(self.veto).to_dict()["echo_veto_bucket_gap"], 1)

    def test_an_echo_site_is_not_counted_and_is_named(self) -> None:
        result = self.gate(self.veto, [confirm("s1", self.veto, support="10-49", roots="3-9"), confirm("s2", self.veto)])
        self.assertEqual(result.status, "hypothesis")
        self.assertEqual(result.to_dict()["support"]["confirming_sites"], ["s2"])
        self.assertEqual(result.to_dict()["support"]["echo_vetoed_sites"], ["s1"])
        self.assertNotIn("s1", result.to_dict()["support"]["weak_confirming_sites"])
        self.assertIn("s1 confirms with more records than roots (echo veto; not counted)", result.reasons)
        self.assertNotIn("s1 confirms with fewer than 3 records (not counted)", result.reasons)

    def test_equal_buckets_are_not_vetoed(self) -> None:
        # '3-9' over '3-9' may hide 9 records on 3 roots: the bucket granularity is the veto's limit, by design
        result = self.gate(self.veto, [confirm("s1", self.veto), confirm("s2", self.veto)])
        self.assertEqual(result.status, "supported")
        self.assertEqual(result.to_dict()["support"]["echo_vetoed_sites"], [])

    def test_a_vetoed_site_is_not_an_n1_corroborator(self) -> None:
        result = self.gate(self.veto_n1, [confirm("s1", self.veto_n1), confirm("s2", self.veto_n1, support="10-49", roots="3-9")])
        self.assertEqual(result.status, "hypothesis")
        self.assertEqual(result.to_dict()["support"].get("corroborating_sites"), [])

    def test_only_vetoed_confirms_is_not_no_evidence(self) -> None:
        result = self.gate(self.veto, [confirm("s1", self.veto, support="10-49", roots="3-9")])
        self.assertEqual(result.status, "hypothesis")
        self.assertNotIn("hypothesis: no evidence (no site confirms)", result.reasons)
        self.assertFalse(result.checks["provenance"])

    def test_gap_two_needs_two_buckets(self) -> None:
        two = pack_copy(self.tmp, "device_quality", {("questions.json", "pushdown", "echo_veto_bucket_gap"): 2})
        kept = self.gate(two, [confirm("s1", two, support="10-49", roots="3-9"), confirm("s2", two)])
        self.assertEqual(kept.status, "supported")
        cut = self.gate(two, [confirm("s1", two, support="50+", roots="3-9"), confirm("s2", two)])
        self.assertEqual(cut.to_dict()["support"]["echo_vetoed_sites"], ["s1"])

    def test_out_of_range_is_refused(self) -> None:
        with self.assertRaises(PackError):
            pack_copy(self.tmp, "device_quality", {("questions.json", "pushdown", "echo_veto_bucket_gap"): 4})

    def test_output_is_deterministic(self) -> None:
        recs = [confirm("s1", self.veto, support="10-49", roots="3-9"), confirm("s2", self.veto)]
        self.assertEqual(self.gate(self.veto, recs).to_dict(), self.gate(self.veto, list(reversed(recs))).to_dict())


if __name__ == "__main__":
    unittest.main()

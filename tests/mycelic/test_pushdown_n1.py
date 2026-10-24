"""EMERGENCE-Next protocol N1 (``pushdown.weak_corroborating_sites``): a site that confirms with fewer than k records
may stand in for a missing k-record site. These pin that N1 is opt-in (a pack without the key keeps its bytes, its
config hash and the shipped gate R0), that it never supports without a k-record site, and that a weak site adds
nothing to roots, reporters or freshness."""
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


def full(site: str, pack=DQ, **kw):
    q = _q(pack)
    return VerdictRecord(site, 1, "site", verdict_body(site, "confirm", q, pack_hash=pack.config_hash,
                                                       reporters_bucket="3-9", **kw), AS_OF)


def weak(site: str, pack=DQ, **kw):
    q = _q(pack)
    return VerdictRecord(site, 1, "site", verdict_body(site, "confirm", q, pack_hash=pack.config_hash,
                                                       support_bucket=SUPPRESSED, roots_bucket=SUPPRESSED,
                                                       reporters_bucket=SUPPRESSED, **kw), AS_OF)


def refute(site: str, pack=DQ):
    return VerdictRecord(site, 1, "site", verdict_body(site, "refute", _q(pack), pack_hash=pack.config_hash), AS_OF)


class N1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.n1 = pack_copy(self.tmp, "device_quality", {("questions.json", "pushdown", "weak_corroborating_sites"): 1})

    def gate(self, pack, records):
        return evaluate(pack, _q(pack), ROUTES, records, as_of=AS_OF)

    def test_r0_is_unchanged_without_the_key(self) -> None:
        self.assertEqual(GateParams.from_pack(DQ).weak_corroborating_sites, 0)
        self.assertNotIn("weak_corroborating_sites", GateParams.from_pack(DQ).to_dict())
        self.assertEqual(pack_copy(self.tmp, "device_quality").config_hash, DQ.config_hash)
        result = self.gate(DQ, [full("s1"), weak("s2")])
        self.assertEqual(result.status, "hypothesis")
        self.assertNotIn("corroborating_sites", result.to_dict()["support"])
        self.assertIn("s2 confirms with fewer than 3 records (not counted)", result.reasons)

    def test_n1_is_a_different_pack(self) -> None:
        self.assertNotEqual(self.n1.config_hash, DQ.config_hash)
        self.assertEqual(GateParams.from_pack(self.n1).to_dict()["weak_corroborating_sites"], 1)

    def test_n1_supports_one_k_site_with_one_weak_corroboration(self) -> None:
        result = self.gate(self.n1, [full("s1", self.n1), weak("s2", self.n1)])
        self.assertEqual(result.status, "supported", result.reasons)
        self.assertIn("supported under N1: s2 corroborate with fewer than 3 records", result.reasons)
        self.assertEqual(result.to_dict()["support"]["roots_lb"], 3)          # the k-record site's alone
        self.assertEqual(result.to_dict()["support"]["corroborating_sites"], ["s2"])
        self.assertEqual(result.to_dict()["support"]["confirming_sites"], ["s1"])

    def test_n1_never_supports_without_a_k_record_site(self) -> None:
        result = self.gate(self.n1, [weak("s1", self.n1), weak("s2", self.n1), weak("s3", self.n1)])
        self.assertEqual(result.status, "hypothesis")

    def test_a_weak_site_adds_no_roots_or_reporters(self) -> None:
        # the k-record site has fewer than k roots: a weak corroboration cannot make up the roots
        result = self.gate(self.n1, [full("s1", self.n1, roots_bucket=SUPPRESSED), weak("s2", self.n1)])
        self.assertEqual(result.status, "hypothesis")
        self.assertTrue(any("independent roots" in r for r in result.reasons), result.reasons)

    def test_a_contributing_refute_still_contests(self) -> None:
        result = self.gate(self.n1, [full("s1", self.n1), weak("s2", self.n1), refute("s3", self.n1)])
        self.assertEqual(result.status, "contested")

    def test_the_cap_is_respected(self) -> None:
        two = pack_copy(self.tmp, "device_quality", {("questions.json", "pushdown", "min_confirming_sites"): 3,
                                                     ("questions.json", "pushdown", "weak_corroborating_sites"): 1})
        result = self.gate(two, [full("s1", two), weak("s2", two), weak("s3", two)])
        self.assertEqual(result.status, "hypothesis")    # 1 k-site + at most 1 weak < 3

    def test_out_of_range_is_refused(self) -> None:
        with self.assertRaises(PackError):
            pack_copy(self.tmp, "device_quality", {("questions.json", "pushdown", "weak_corroborating_sites"): 11})


if __name__ == "__main__":
    unittest.main()

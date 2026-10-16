from types import SimpleNamespace
import unittest

from research.mycelic.corpus import PRED_ID, PRED_SURFACE
from rollup.edge import emulate
from rollup.protocol import validate_summary


class EdgeTests(unittest.TestCase):
    def world(self):
        recs = [{"uid": 10, "t": 0, "pred": PRED_ID["supplier_substitution"], "anchor": 0, "event": 40, "polarity": 1},
                {"uid": 10, "t": 1, "pred": PRED_ID["supplier_substitution"], "anchor": 0, "event": 40, "polarity": 1},
                {"uid": 20, "t": 2, "pred": PRED_ID["supplier_substitution"], "anchor": 0, "event": 40, "polarity": 1},
                {"uid": 10, "t": 3, "pred": PRED_ID["component_defect"], "anchor": 0, "event": 41, "polarity": -1}]
        org = SimpleNamespace(user_ids=[10, 20], ancestor_at=lambda u, level: u,
                              nodes={10: SimpleNamespace(name="r00-site-00"), 20: SimpleNamespace(name="r01-site-00")})
        return SimpleNamespace(seed=0, org=org, recs=recs, entities=["orion"],
                               tokens=lambda rid: [PRED_SURFACE["supplier_substitution" if rid < 3 else "component_defect"][0]])

    def test_perfect_marking_echoes_share_handle_across_sites(self):
        weeks, counts = emulate(self.world(), company_key=b"test", perfect=True)
        self.assertEqual(counts["records"], 4)
        self.assertEqual(counts["lexicon_events"], 4)
        self.assertEqual(counts["kept"], 4)
        self.assertEqual(counts["true_copies"], 1)
        self.assertEqual(counts["marked_copy"], 1)
        self.assertEqual(counts["marked_negated"], 1)
        local = weeks[1][0]["entries"]
        copies = next(r for r in local if r["kind"] == "supplier_substitution")
        peer = weeks[1][1]["entries"][0]
        self.assertEqual(copies["distinct"], 1)
        self.assertEqual(copies["handles"], peer["handles"])
        for ss in weeks.values():
            for s in ss:
                validate_summary(s)

    def test_replay_is_deterministic_and_company_key_changes_handles(self):
        a, _ = emulate(self.world(), company_key=b"test")
        b, _ = emulate(self.world(), company_key=b"test")
        c, _ = emulate(self.world(), company_key=b"other")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()

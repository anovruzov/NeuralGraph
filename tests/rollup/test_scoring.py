import copy
from types import SimpleNamespace
import unittest

from rollup.run import scores


class ScoringTests(unittest.TestCase):
    def fixture(self, week=3, true_overlap=False):
        key = "entity:E-ORION:chain_auth"
        truth = {"patterns": [{"id": "p000", "key": key if true_overlap else "other", "anchor": "E-ORION",
                               "found_from": "2024-W02", "found_to": "2024-W04", "rare": True,
                               "contradicted": False, "sites": ["r00-site-00"]}],
                 "decoys": [{"id": "d040", "class": 2, "pattern": 0, "keys": [key],
                             "found_from": "2024-W02", "found_to": "2024-W04"}]}
        corpus = SimpleNamespace(patterns=[SimpleNamespace(facet_records=[[0]], chain=1)],
                                 entities=["orion"], recs=[{"anchor": 0}])
        story = {"key": key, "entity": "E-ORION", "lineage_ok": True}
        records = [{"week": week, "stories": [story], "qualified": [story],
                    "replies": {"judge": {key: {"ruling": "supported", "reason_tag": "none", "chain": "chain_auth"}},
                                "skeptic": {key: {"text": "argument", "attack_tags": []}}}}]
        return records, truth, corpus

    def test_decoy_outside_window_not_counted(self):
        for week, expected in ((1, 0), (2, 1), (4, 1), (5, 0)):
            with self.subTest(week=week):
                result = scores(*self.fixture(week))
                self.assertEqual(result["decoys"]["every_facet_window"]["2"]["accepted"], expected)
                self.assertEqual(result["decoys"]["facet0_window"]["2"]["accepted"], expected)
                self.assertEqual(result["decoys"]["every_facet_unwindowed"]["2"]["accepted"], 1)

    def test_true_key_precedence_and_shipped_facet0_exception(self):
        result = scores(*self.fixture(true_overlap=True))
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["decoys"]["every_facet_window"]["2"]["accepted"], 0)
        # Frozen shipped loop has no true-key override. Reproduced literally.
        self.assertEqual(result["decoys"]["facet0_window"]["2"]["accepted"], 1)

    def test_lineage_failure_cannot_score_found(self):
        records, truth, corpus = self.fixture(true_overlap=True)
        records[0]["stories"][0]["lineage_ok"] = False
        self.assertEqual(scores(records, truth, corpus)["found"], 0)

    def test_duplicate_ruling_counts_pattern_once(self):
        records, truth, corpus = self.fixture(true_overlap=True)
        records.append(copy.deepcopy(records[0]))
        result = scores(records, truth, corpus)
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["supported"], 2)


if __name__ == "__main__":
    unittest.main()

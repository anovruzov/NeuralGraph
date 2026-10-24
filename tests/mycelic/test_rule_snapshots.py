"""``detectors.rule_snapshots`` (EMERGENCE-Next, opt-in): a rule-layer candidate no detector alerted on gets a snapshot
from its rule hit. Absent, detection is the shipped one."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mycelic.collective.detect.detectors import detect
from mycelic.collective.packs.loader import PackError, load_pack
from research.e2e_ingest import emergence_world as ew


def pack_with(**fields):
    d = Path(tempfile.mkdtemp()) / "emergence"
    shutil.copytree(ew.PACK_DIR, d)
    f = d / "detectors.json"
    j = json.loads(f.read_text())
    j.update(fields)
    f.write_text(json.dumps(j))
    return d


class RuleSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        original = ew.check_world_counts
        ew.check_world_counts = lambda *a: None
        try:
            cls.base = ew.build_emergence_world(200, 0, tempfile.mkdtemp(), top_n=None)
            cls.rule = ew.build_emergence_world(200, 0, tempfile.mkdtemp(), pack_dir=pack_with(rule_snapshots=True),
                                                top_n=None)
        finally:
            ew.check_world_counts = original

    @classmethod
    def tearDownClass(cls) -> None:
        cls.base.pipeline.close()
        cls.rule.pipeline.close()

    def test_absent_is_the_shipped_detector(self) -> None:
        self.assertNotIn("rule_snapshots", load_pack(str(ew.PACK_DIR)).detectors)
        self.assertTrue(all(c["snapshot"].get("source") != "rule" for c in self.base.candidates))

    def test_rule_snapshots_add_only_rule_candidates_with_a_full_window(self) -> None:
        base = {c["key"]: c["snapshot"] for c in self.base.candidates}
        added = [c for c in self.rule.candidates if c["key"] not in base]
        self.assertTrue(added)
        weeks = list(ew.world_weeks(ew.START, ew.N_WEEKS))
        for c in added:
            snap = c["snapshot"]
            self.assertEqual(snap["source"], "rule")
            self.assertIn(snap["week"], c["rule"]["weeks"])
            self.assertEqual(weeks.index(snap["week"]) - weeks.index(snap["window"][0]), 5)
        fields = ("week", "as_of", "window", "score", "supporting_sites")
        for c in self.rule.candidates:                 # detector snapshots are untouched (pack hashes differ, by design)
            if c["key"] in base:
                self.assertEqual({f: c["snapshot"][f] for f in fields}, {f: base[c["key"]][f] for f in fields})

    def test_must_be_boolean(self) -> None:
        with self.assertRaises(PackError):
            load_pack(str(pack_with(rule_snapshots="yes")))


if __name__ == "__main__":
    unittest.main()

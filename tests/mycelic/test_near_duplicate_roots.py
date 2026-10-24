"""``egress.near_duplicate_roots`` (opt-in): near-duplicate echoes at one site share a root. Absent, a pack keeps its
bytes, hash and the shipped exact-text root rule."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mycelic.collective.edge.records import RecordStore
from mycelic.collective.packs.loader import PackError

from tests.mycelic.test_collective_edge import DQ, pack_copy, record

ECHO = "The SD-9 pump cracked at the housing seam during the night shift, ticket attached"


class NearDuplicateRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def roots(self, near, recs):
        store = RecordStore(self.tmp / f"s{len(list(self.tmp.iterdir()))}.sqlite3", site_id="s1", pack_id=DQ.id,
                            config_hash=DQ.config_hash)
        try:
            store.ingest(recs, ingested_at="2026-04-26T08:00:00Z", near_duplicate_roots=near)
            return {r: store._conn.execute("SELECT root_ref FROM records WHERE record_ref=?", (r,)).fetchone()[0]
                    for r in (x["record_ref"] for x in recs)}
        finally:
            store.close()

    def echoes(self):
        return [record(DQ, "a", "s1", "2026-04-08", narrative=f"[author A] :: {ECHO}", reporter="R1"),
                record(DQ, "b", "s1", "2026-04-09", narrative=f"[author B] :: {ECHO}", reporter="R2"),
                record(DQ, "c", "s1", "2026-04-09", narrative=f"[author C] :: {ECHO[:-8]} pending", reporter="R3")]

    def test_shipped_rule_keeps_echoes_apart(self) -> None:
        roots = self.roots(None, self.echoes())
        self.assertEqual(len(set(roots.values())), 3)

    def test_echoes_share_one_root(self) -> None:
        roots = self.roots((0.65, 14), self.echoes())
        self.assertEqual(set(roots.values()), {"a"})

    def test_a_distinct_report_keeps_its_own_root(self) -> None:
        recs = self.echoes() + [record(DQ, "d", "s1", "2026-04-09",
                                       narrative="[author D] :: SD-9 display went blank after a firmware update",
                                       reporter="R4")]
        self.assertEqual(self.roots((0.65, 14), recs)["d"], "d")

    def test_outside_the_window_is_not_merged(self) -> None:
        recs = self.echoes()[:1] + [record(DQ, "late", "s1", "2026-04-28", narrative=f"[author E] :: {ECHO}",
                                           reporter="R5")]
        self.assertEqual(self.roots((0.65, 14), recs)["late"], "late")

    def test_another_entity_is_not_merged(self) -> None:
        a = self.echoes()[0]
        b = record(DQ, "x", "s1", "2026-04-08", narrative=f"[author X] :: {ECHO}", reporter="R6",
                   entities={"component": ["BATTERY-DOOR"]})
        self.assertEqual(self.roots((0.65, 14), [a, b])["x"], "x")

    def test_pack_setting_is_opt_in_and_validated(self) -> None:
        self.assertIsNone(DQ.egress.near_duplicate_roots)
        self.assertEqual(pack_copy(self.tmp, "device_quality").config_hash, DQ.config_hash)
        p = pack_copy(self.tmp, "device_quality", {("egress.json", "near_duplicate_roots"): {"jaccard": 0.65, "days": 14}})
        self.assertEqual(p.egress.near_duplicate_roots, (0.65, 14))
        with self.assertRaises(PackError):
            pack_copy(self.tmp, "device_quality", {("egress.json", "near_duplicate_roots"): {"jaccard": 0.2, "days": 14}})


if __name__ == "__main__":
    unittest.main()

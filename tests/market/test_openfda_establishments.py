"""The establishment counter behind the market sizing (``tools/market/openfda_establishments.py``), offline."""
from __future__ import annotations

import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("openfda_establishments",
                                               ROOT / "tools" / "market" / "openfda_establishments.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # type: ignore[union-attr]


def rec(number: str, owner: str, country: str, types, firm: str = "Acme Medical Inc.") -> dict:
    return {"registration": {"registration_number": number, "iso_country_code": country,
                             "owner_operator": {"owner_operator_number": owner, "firm_name": firm}},
            "establishment_type": types}


MFG = ["Manufacture Medical Device"]
DIST = ["Foreign Exporter"]


class CountTests(unittest.TestCase):
    def test_bands_by_owner_and_site_type(self) -> None:
        records = [rec("1", "A", "US", MFG), rec("2", "A", "DE", MFG), rec("3", "A", "IE", DIST),
                   rec("3", "A", "IE", ["Complaint File Establishment per 21 CFR 820.198"]),   # same site twice
                   rec("4", "B", "US", MFG), rec("5", "B", "US", MFG), rec("6", "B", "US", MFG),
                   rec("7", "C", "JP", DIST)]
        out = m.count(records)
        self.assertEqual((out["establishments"], out["qualifying_establishments"], out["countries"]), (7, 6, 4))
        bands = {(b["min_establishments"], b["min_countries"]): b["groups"]
                 for b in out["views"]["owner/all_types"]["bands"]}
        self.assertEqual(bands[(2, 1)], 2)        # A (3 sites) and B (3 sites)
        self.assertEqual(bands[(3, 2)], 1)        # only A spans two countries
        self.assertEqual(bands[(5, 2)], 0)
        q = {(b["min_establishments"], b["min_countries"]): b["groups"]
             for b in out["views"]["owner/manufacturing_or_complaint"]["bands"]}
        self.assertEqual(q[(3, 2)], 1)            # A's Irish site counts once its complaint-file type is unioned in
        self.assertEqual(out["views"]["owner/all_types"]["top"][0], {"name": "Acme Medical Inc.", "sites": 3,
                                                                     "countries": 3})

    def test_firm_name_grouping_merges_owner_numbers_of_one_name(self) -> None:
        records = [rec("1", "A", "US", MFG, "Acme Medical, Inc."), rec("2", "A2", "DE", MFG, "ACME MEDICAL GmbH"),
                   rec("3", "A3", "IE", MFG, "Acme Medical Limited")]
        out = m.count(records)
        owner = {(b["min_establishments"], b["min_countries"]): b["groups"] for b in out["views"]["owner/all_types"]
                 ["bands"]}
        firm = {(b["min_establishments"], b["min_countries"]): b["groups"] for b in out["views"]["firm_name/all_types"]
                ["bands"]}
        self.assertEqual((owner[(3, 2)], firm[(3, 2)]), (0, 1))
        self.assertEqual(m.normalise_name("ACME MEDICAL GmbH"), "acme medical")

    def test_records_missing_a_field_are_skipped_and_counted(self) -> None:
        bad = [{"registration": {"iso_country_code": "US"}}, {"registration": {"registration_number": "9"}},
               {"registration": {"registration_number": "9", "owner_operator": {"owner_operator_number": "Z"}}}]
        out = m.count(bad + [rec("1", "A", "US", "Manufacture Medical Device")])
        self.assertEqual(out["skipped_records"], {"registration_number": 1, "owner_operator_number": 1,
                                                  "iso_country_code": 1})
        self.assertEqual(out["establishments"], 1)

    def test_main_reads_local_partition_zips(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("part.json", json.dumps({"meta": {}, "results": [rec("1", "A", "US", MFG),
                                                                               rec("2", "A", "DE", MFG),
                                                                               rec("3", "A", "IE", MFG)]}))
            part = Path(tmp) / "p.zip"
            part.write_bytes(buf.getvalue())
            out = Path(tmp) / "out.json"
            self.assertEqual(m.main(["--out", str(out), "--files", str(part)]), 0)
            doc = json.loads(out.read_text())
            self.assertEqual(doc["kind"], "openfda_establishment_count")
            self.assertEqual(doc["views"]["owner/all_types"]["bands"][1]["groups"], 1)


if __name__ == "__main__":
    unittest.main()

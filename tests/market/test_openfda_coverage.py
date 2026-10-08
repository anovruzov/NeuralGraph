"""The field-coverage probe's counting (``tools/market/openfda_coverage.py``), offline."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("openfda_coverage", ROOT / "tools" / "market" / "openfda_coverage.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # type: ignore[union-attr]


def rec(lot=None, text=None, problems=None) -> dict:
    out: dict = {"device": [{"lot_number": lot}] if lot is not None else []}
    if text is not None:
        out["mdr_text"] = [{"text": text, "text_type_code": "Description of Event or Problem"}]
    if problems is not None:
        out["product_problems"] = problems
    return out


class CoverageTests(unittest.TestCase):
    def test_real_lots_and_placeholders(self) -> None:
        for value in ("L10002", "A-77 ", "2024-03"):
            self.assertTrue(m.real_lot(value), value)
        for value in ("", " ", "UNK", "unknown", "NI", "N/A", "ASKU", None, 7):
            self.assertFalse(m.real_lot(value), value)

    def test_generic_only(self) -> None:
        self.assertTrue(m.generic_only(["Adverse Event Without Identified Device or Use Problem"]))
        self.assertTrue(m.generic_only(["Insufficient Information", "Appropriate Term/Code Not Available"]))
        self.assertFalse(m.generic_only(["Insufficient Information", "Overheating of Device"]))
        self.assertTrue(m.generic_only([]))

    def test_summarise_sample(self) -> None:
        records = [rec("L1", "It overheated.", ["Overheating of Device"]),
                   rec("UNK", "Too hot.", ["Insufficient Information"]),
                   rec(None, None, ["Insufficient Information"]),
                   rec("L2", "  ", None)]
        out = m.summarise_sample(records)
        self.assertEqual(out["records"], 4)
        self.assertEqual(out["real_lot_share"], 0.5)
        self.assertEqual(out["narrative_share"], 0.5)
        self.assertEqual(out["coded_share"], 0.75)
        self.assertEqual(out["generic_only_share_of_coded"], 0.6667)
        self.assertEqual(out["top_problems"][0], {"problem": "Insufficient Information", "records": 2})

    def test_empty_sample(self) -> None:
        out = m.summarise_sample([])
        self.assertEqual((out["records"], out["real_lot_share"], out["generic_only_share_of_coded"]), (0, None, None))


if __name__ == "__main__":
    unittest.main()

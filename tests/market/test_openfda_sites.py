"""The site-split probe's summary (``tools/market/openfda_sites.py``), offline."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("openfda_sites", ROOT / "tools" / "market" / "openfda_sites.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # type: ignore[union-attr]


class SitesTests(unittest.TestCase):
    def test_summarise(self) -> None:
        names = [{"term": "ACME IRELAND", "count": 300}, {"term": "ACME INC", "count": 600},
                 {"term": "ACME MEXICO", "count": 50}]
        countries = [{"term": "US", "count": 600}, {"term": "IE", "count": 300}, {"term": "MX", "count": 50}]
        out = m.summarise(names, countries, 1000)
        self.assertEqual((out["names_with_min_reports"], out["countries_with_min_reports"]), (2, 2))
        self.assertEqual(out["largest_name_share"], 0.6)
        self.assertEqual(out["top_names"][0], {"name": "ACME INC", "reports": 600})
        self.assertEqual([c["country"] for c in out["top_countries"]], ["US", "IE", "MX"])

    def test_empty(self) -> None:
        out = m.summarise([], [], 0)
        self.assertEqual((out["reports"], out["largest_name_share"], out["top_names"]), (0, None, []))


if __name__ == "__main__":
    unittest.main()

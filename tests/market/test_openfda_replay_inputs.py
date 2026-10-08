"""The replay-inputs probe's rule steps (``tools/market/openfda_replay_inputs.py``), offline."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("openfda_replay_inputs",
                                               ROOT / "tools" / "market" / "openfda_replay_inputs.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # type: ignore[union-attr]

YEARS = range(2019, 2025)


def flat(per_year: int) -> dict[int, int]:
    return {y: per_year for y in YEARS}


class ChooseWindowTests(unittest.TestCase):
    def test_longest_window_with_three_fitting_codes(self) -> None:
        totals = {"AAA": flat(1000), "BBB": flat(3000), "CCC": flat(1500), "DDD": flat(500), "EEE": flat(20000)}
        out = m.choose_window(totals, ["AAA", "BBB", "CCC", "DDD", "EEE"], 2019, 2024, 10000)
        # six years: AAA 6000, CCC 9000, DDD 3000 fit (BBB 18000 does not), so the longest window is used
        self.assertEqual((out["run"], out["date_from"], out["date_to"], out["years"]), (True, "20190101", "20241231", 6))
        self.assertEqual(out["codes"], ["AAA", "CCC", "DDD"])
        self.assertEqual(out["not_fitting"], ["BBB", "EEE"])
        self.assertEqual(out["reports"], {"AAA": 6000, "CCC": 9000, "DDD": 3000})

    def test_shorter_window_when_too_few_codes_fit(self) -> None:
        totals = {"AAA": flat(4000), "BBB": flat(3000), "CCC": flat(2600), "DDD": flat(30000)}
        out = m.choose_window(totals, ["AAA", "BBB", "CCC", "DDD"], 2019, 2024, 10000)
        # 2022-2024: AAA 12000 fails; 2023-2024: AAA 8000, BBB 6000, CCC 5200 fit
        self.assertEqual((out["date_from"], out["years"], out["codes"]), ("20230101", 2, ["AAA", "BBB", "CCC"]))

    def test_at_most_max_codes_in_order(self) -> None:
        totals = {c: flat(100) for c in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")}
        out = m.choose_window(totals, ["FFF", "EEE", "DDD", "CCC", "BBB", "AAA"], 2019, 2024, 10000)
        self.assertEqual(out["codes"], ["FFF", "EEE", "DDD", "CCC", "BBB"])

    def test_not_run_when_no_window_holds_enough_codes(self) -> None:
        totals = {"AAA": flat(20000), "BBB": flat(500), "CCC": flat(500)}
        out = m.choose_window(totals, ["AAA", "BBB", "CCC"], 2019, 2024, 10000)
        self.assertFalse(out["run"])
        self.assertIn("no window", out["reason"])

    def test_a_missing_year_does_not_fit(self) -> None:
        totals = {"AAA": {2024: 10}, "BBB": flat(10), "CCC": flat(10), "DDD": flat(10)}
        out = m.choose_window(totals, ["AAA", "BBB", "CCC", "DDD"], 2019, 2024, 10000)
        self.assertEqual((out["years"], out["codes"]), (6, ["BBB", "CCC", "DDD"]))


class FirmNameTests(unittest.TestCase):
    def test_names_only_by_word_or_leading_bd(self) -> None:
        terms = ["Becton Dickinson & Co.", "BD Medical", "ABD Devices", "Bard Access Systems", "BECTON, DICKINSON AND CO",
                 "Becton Dickinson & Co.", "BD", "Something BD Inc"]
        self.assertEqual(m.firm_names(terms, "BECTON"),
                         ["Becton Dickinson & Co.", "BD Medical", "BECTON, DICKINSON AND CO", "BD"])

    def test_at_most_ten(self) -> None:
        self.assertEqual(len(m.firm_names([f"Becton {i}" for i in range(15)], "BECTON")), 10)


if __name__ == "__main__":
    unittest.main()

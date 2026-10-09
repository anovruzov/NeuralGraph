"""Public replay 002's pack rule (``tools/market/replay_pack.py``), offline."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("replay_pack", ROOT / "tools" / "market" / "replay_pack.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # type: ignore[union-attr]

BASE = ROOT / "mycelic" / "collective" / "packs" / "data" / "device_quality"


def shapes(lots, models, catalogs, problems) -> dict:
    return {"fields": {m.LOT_FIELD: {"AAA": lots}, m.MODEL_FIELD: {"AAA": models},
                       m.CATALOG_FIELD: {"AAA": catalogs}, m.PROBLEM_FIELD: {"AAA": problems}}}


class SignatureTests(unittest.TestCase):
    def test_runs_and_drops(self) -> None:
        self.assertEqual(m.signature("2101234"), (("digit", 7),))
        self.assertEqual(m.signature("AB-123"), (("alpha", 2), ("sep-", 1), ("digit", 3)))
        self.assertEqual(m.signature("UNKNOWN"), "no_digit")
        self.assertEqual(m.signature("REF 367856"), "other_characters")
        self.assertEqual(m.signature("-123"), "separator_at_edge")
        self.assertEqual(m.signature("12--34"), "adjacent_separators")
        self.assertEqual(m.signature("1-2/3"), "mixed_separators")
        self.assertEqual(m.signature("1" * 41), "too_long")

    def test_merge_sums_codes_after_trimming_and_upper_casing(self) -> None:
        self.assertEqual(m.merge({"A": [[" ab1 ", 2], ["AB1", 3]], "B": [["ab1", 1]], "C": {"error": "HTTP 400"}}),
                         {"AB1": 6})
        self.assertEqual(m.merge({"A": [["Leak/Splash ", 2]]}, upper=False), {"Leak/Splash": 2})


class ShapeFormatTests(unittest.TestCase):
    def test_largest_signature_and_narrowest_lengths_covering_ninety_percent(self) -> None:
        counts = {"2101234": 50, "2109999": 30, "0012345": 10, "123456789012": 5, "AB12": 20, "UNK": 400}
        out = m.shape_format(counts)
        # digit-only reports: 95 of them; 7 digits carry 90, so the 12-digit outlier is not needed
        self.assertEqual(out["format"], [{"digit": [7, 7]}])
        self.assertEqual((out["covered"], out["eligible"], out["total"]), (90, 115, 515))
        self.assertEqual(out["dropped"], {"no_digit": 400})
        self.assertIsNone(out["separator"])

    def test_separator_format(self) -> None:
        out = m.shape_format({"AB-12": 5, "C-123": 5, "1234": 3})
        self.assertEqual(out["format"], [{"alpha": [1, 2]}, {"sep": "required"}, {"digit": [2, 3]}])
        self.assertEqual(out["separator"], "-")

    def test_ties_take_fewer_runs(self) -> None:
        self.assertEqual(m.shape_format({"A1": 5, "12": 5})["format"], [{"digit": [2, 2]}])

    def test_no_value_with_a_digit(self) -> None:
        self.assertIsNone(m.shape_format({"UNK": 3, "NA": 2})["format"])

    def test_product_path_prefers_the_model_number_on_a_tie(self) -> None:
        self.assertEqual(m.product_path({"367856": 5, "UNK": 50}, {"367856": 5}), m.MODEL_PATH)
        self.assertEqual(m.product_path({"UNK": 50}, {"367856": 5}), m.CATALOG_PATH)

    def test_example_ids_fit_the_format(self) -> None:
        self.assertEqual(m.example_id([{"digit": [7, 7]}], None, 3, "9"), "9999993")
        self.assertEqual(m.example_id([{"alpha": [0, 2]}, {"sep": "required"}, {"digit": [1, 3]}], "-", 12, "8"),
                         "X-12")
        with self.assertRaises(m.BuildError):
            m.example_id([{"digit": [1, 1]}], None, 12, "9")


class TermMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vocab = json.loads((BASE / "vocabulary.json").read_text())["predicates"]
        self.codes = json.loads((BASE / "codes.json").read_text())

    def test_whole_words_of_an_id_or_lexicon_entry(self) -> None:
        out = m.term_map({"Leak/Splash": 9, "Fracture": 4, "Break": 7, "Crack": 3, "Leakage Test": 1,
                          "Cracked and Leaking": 2, "Device Contamination with Chemical or Other Material": 5},
                         self.vocab, self.codes, {"Crack": "ILL-0101"})
        self.assertEqual(out["added"], {"Leak/Splash": "ILL-0201", "Fracture": "ILL-0101", "Leakage Test": "ILL-0201",
                                        "Device Contamination with Chemical or Other Material": "ILL-1001"})
        self.assertEqual(out["ambiguous"], ["Cracked and Leaking"])
        self.assertEqual(out["unmapped"], ["Break"])
        self.assertEqual((out["reports"], out["mapped"]), (31, 22))

    def test_a_predicate_without_a_specific_code_takes_its_first_code(self) -> None:
        self.assertEqual(m.predicate_code("malfunction_unspecified", self.codes), "ILL-9001")
        self.assertEqual(m.predicate_code("crack", self.codes), "ILL-0101")


class RenameTextTests(unittest.TestCase):
    def test_any_case_never_inside_a_longer_run(self) -> None:
        names = {"SD-9": "8888881", "SD-90": "8888882", "L10001": "9999991"}
        self.assertEqual(m.rename_text("The sd-9 and SD-90 units; lot L10001, not L100012.", names),
                         "The 8888881 and 8888882 units; lot 9999991, not L100012.")
        self.assertEqual(m.rename_text("SD 9, SD_9, SD\u2013 9, SD\u200b-9 and XSD-9", names),
                         "8888881, 8888881, 8888881, 8888881 and XSD-9")


class BuildTests(unittest.TestCase):
    SHAPES = shapes([["2101234", 50], ["UNK", 40], ["1203456", 30]], [["UNKNOWN", 20], ["367856", 2]],
                    [["367856", 30], ["382523", 10], ["NI", 9]],
                    [["Leak/Splash", 12], ["Break", 3], ["Fluid/Blood Leak", 2]])

    def test_the_built_pack_loads_and_changes_only_what_the_rule_says(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "device_quality_bd"
            result = m.build(self.SHAPES, BASE, out, "device_quality_bd")
            self.assertEqual(result["pack"]["id"], "device_quality_bd")
            self.assertEqual(result["product_path"], m.CATALOG_PATH)
            vocab = json.loads((out / "vocabulary.json").read_text())
            self.assertEqual(vocab["entity_types"]["lot"]["id_format"], [{"digit": [7, 7]}])
            self.assertEqual(vocab["entity_types"]["product"]["id_format"], [{"digit": [6, 6]}])
            self.assertIsNone(vocab["entity_types"]["product"]["separator"])
            base_vocab = json.loads((BASE / "vocabulary.json").read_text())
            self.assertEqual(vocab["predicates"], base_vocab["predicates"])
            mapping = json.loads((out / "mapping_openfda.json").read_text())
            self.assertEqual(mapping["entities"]["product"], [m.CATALOG_PATH])
            self.assertEqual(mapping["codes"][0]["value_map"]["Leak/Splash"], "ILL-0201")
            self.assertNotIn("Break", mapping["codes"][0]["value_map"])
            for name in ("detectors.json", "codes.json", "rules.json", "egress.json", "questions.json",
                         "followups.json", "mapping.json"):
                self.assertEqual((out / name).read_bytes(), (BASE / name).read_bytes(), name)
            fixtures = (out / "fixtures" / "records.jsonl").read_text()
            base_fixtures = (BASE / "fixtures" / "records.jsonl").read_text()
            self.assertEqual(fixtures.count("\n"), base_fixtures.count("\n"))
            for spelling in ("SD-9", "sd-9", "SD 9", "SD_12", "IP\u201321", "IP\u00a021", "L10001"):
                self.assertNotIn(spelling, fixtures.replace("XSD-91", ""))
            self.assertIn("XSD-91", fixtures)          # a decoy that is no id keeps its text
            self.assertEqual(sorted(p.name for p in (out / "fixtures").iterdir()), ["records.jsonl"])
            with self.assertRaises(m.BuildError):
                m.build(self.SHAPES, BASE, out, "device_quality_bd")

    def test_the_build_is_byte_for_byte_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            m.build(self.SHAPES, BASE, a, "device_quality_bd")
            m.build(self.SHAPES, BASE, b, "device_quality_bd")
            files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
            self.assertEqual(files, sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file()))
            for f in files:
                self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)

    def test_no_format_no_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(m.BuildError):
                m.build(shapes([["UNK", 3]], [["1", 1]], [], []), BASE, Path(tmp) / "x", "x_pack")


if __name__ == "__main__":
    unittest.main()

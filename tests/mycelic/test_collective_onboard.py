"""D001: the pack drafter (``mycelic.collective.onboard``), offline, on small synthetic exports the tests write.

Every export here is invented by the test: no record of any public source. The sections follow
``docs/collective/onboard/BUILD-D001.md`` section 8: reading, dates, role inference, categories, the lexicon,
determinism, loading, the normalised export, the pipeline, the check, scoring, the report, the CLI, the settings and the
workflow.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import random
import re
import shutil
import tempfile
import unittest
from dataclasses import fields
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from mycelic.collective import stats
from mycelic.collective.edge.extract import LexicalExtractor, sense
from mycelic.collective.onboard import check as C
from mycelic.collective.onboard import draft as D
from mycelic.collective.onboard import report as REP
from mycelic.collective.onboard import score as SC
from mycelic.collective.onboard.__main__ import main as cli
from mycelic.collective.onboard.exports import Export, ExportError, parse_export
from mycelic.collective.onboard.roles import (DATE_FORMATS, choose_date_format, column_evidence, infer_roles,
                                              parse_date, roles_from_json, roles_right)
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.connector import map_rows
from mycelic.collective.packs.loader import RESERVED, load_pack_dir
from mycelic.collective.pilot import audit as AUDIT

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "docs" / "collective" / "onboard" / "D001-settings.json"
CHOICE = ROOT / "docs" / "collective" / "onboard" / "CHOICE-D001.md"
WORKFLOW = ROOT / ".github" / "workflows" / "onboard-run.yml"
PARAMS = D.load_params()
LANG = D.load_language("en")
TEMPLATE = D.load_template()
IDS = TEMPLATE["ids.json"]
TRAIN = D.parse_window("2015-01-01", "2021-12-31")
TEST = D.parse_window("2022-01-01", "2024-12-31")
HEADER = ("ID", "SITE", "DATE", "TEXT", "CAT", "PERSON")
SITES = tuple(f"s{i}" for i in range(1, 7))
FILLER = ("employee", "working", "shift", "crew", "morning", "reported", "nearby", "station", "worker", "tool")


def roles(**over: Any) -> Any:
    raw = {"record_id": "ID", "site": "SITE", "date": "DATE", "narrative": "TEXT", "category": "CAT",
           "entities": [], "reporter": None, "forbidden": ["PERSON"]}
    raw.update(over)
    return roles_from_json({"language": "en", "roles": raw})


def pipe(rows: list[dict[str, str]], header: tuple[str, ...] = HEADER, delim: str = "|") -> bytes:
    lines = [delim.join(header)] + [delim.join(r.get(h, "") for h in header) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


class Rows:
    """A builder of synthetic rows: record ids in order, sites cycled, dates in the training window by default."""

    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def add(self, n: int, text: str, cat: str = "", *, sites: tuple[str, ...] = SITES, day: date = date(2018, 3, 5),
            person: str = "") -> "Rows":
        for i in range(n):
            self.rows.append({"ID": f"R{len(self.rows) + 1:05d}", "SITE": sites[i % len(sites)],
                              "DATE": f"{day.month}/{day.day}/{day.year}", "TEXT": text, "CAT": cat,
                              "PERSON": person})
        return self

    def export(self) -> Export:
        return parse_export(pipe(self.rows))


def corpus_rows(seed: int = 1, per_cat: int = 100, years: tuple[int, int] = (2015, 2024)) -> list[dict[str, str]]:
    """A synthetic multi-site export: three categories with their own cue words, an other bucket, filler words."""
    rng = random.Random(seed)
    cues = {"ROOF FALL": ["roof", "rock", "bolter", "ceiling"], "HAULAGE": ["shuttle", "conveyor", "haul", "trolley"],
            "SLIP OR FALL": ["slipped", "icy", "walkway", "stairs"], "OTHER": ["misc", "assorted"]}
    rows = []
    for cat, words in cues.items():
        for _ in range(per_cat):
            year = rng.randint(*years)
            text = " ".join(rng.sample(words, 2) + rng.sample(FILLER, 5))
            rows.append({"SITE": rng.choice(SITES), "DATE": f"{rng.randint(1, 12)}/{rng.randint(1, 28)}/{year}",
                         "TEXT": text.capitalize() + ". " + " ".join(rng.sample(FILLER, 4)).capitalize() + ".",
                         "CAT": cat, "PERSON": f"Person {rng.randint(1, 3)}"})
    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r["ID"] = f"A{i + 1:05d}"
    return rows


def drafted(rows: list[dict[str, str]], pack_id: str = "test_pack", **kw: Any) -> D.Draft:
    return D.draft_export(parse_export(pipe(rows)), kw.pop("roles", roles()), kw.pop("window", TRAIN), pack_id,
                          params=kw.pop("params", PARAMS), lang=LANG, template=TEMPLATE)


class TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# =================================================================================================== 1 reading

class ReadingTests(unittest.TestCase):
    def test_pipe_tab_and_comma_with_quotes(self) -> None:
        for delim, fmt in (("|", "pipe"), ("\t", "tab")):
            e = parse_export(f"A{delim}B\n1{delim} x \n2{delim}\n".encode())
            self.assertEqual((e.format, e.columns), (fmt, ("A", "B")))
            self.assertEqual(e.rows, (("1", "x"), ("2", None)))
        e = parse_export(b'A,B\n1,"a, b"\n2,"say ""hi"""\n')
        self.assertEqual(e.format, "comma")
        self.assertEqual(e.rows, (("1", "a, b"), ("2", 'say "hi"')))

    def test_delimiter_ties_go_pipe_then_tab_then_comma(self) -> None:
        self.assertEqual(parse_export(b"A|B\tC,D\nx|y\tz,w\n").format, "pipe")
        self.assertEqual(parse_export(b"A\tB,C\nx\ty,z\n").format, "tab")
        self.assertEqual(parse_export(b"A,B|C|D\n1,2|3|4\n").format, "pipe")

    def test_json_lines(self) -> None:
        data = b'{"a": 1, "b[]": ["x", " y ", "x"]}\n\n{"b[]": "p;q", "c": true}\n[1]\nnot json\n{"a": {"n": 1}}\n'
        e = parse_export(data)
        self.assertEqual((e.format, e.columns), ("jsonl", ("a", "b[]", "c")))
        self.assertEqual(e.rows, (("1", ("x", "y"), None), (None, ("p", "q"), "true")))
        self.assertEqual(dict(e.rejected), {"invalid_json": 1, "nested_value": 1, "not_object": 1})
        self.assertEqual(e.blank_lines, 1)

    def test_byte_order_mark_and_latin1_fallback(self) -> None:
        e = parse_export(b"\xef\xbb\xbfA|B\n1|caf\xc3\xa9\n")
        self.assertEqual((e.encoding, e.columns, e.rows[0][1]), ("utf-8", ("A", "B"), "café"))
        e = parse_export(b"A|B\n1|caf\xe9 \x85 x\n")
        self.assertEqual(e.encoding, "latin-1")
        self.assertEqual(e.rows[0][1], "café \x85 x")      # \x85 is no line break
        self.assertEqual(len(e.rows), 1)

    def test_wrong_width_rejected_and_counted(self) -> None:
        e = parse_export(b"A|B|C\n1|2|3\n1|2\n1|2|3|4\n4|5|6\r\n")
        self.assertEqual(len(e.rows), 2)
        self.assertEqual(dict(e.rejected), {"wrong_width": 2})
        e = parse_export(b"A,B\n1,2\n1,2,3\n")
        self.assertEqual(dict(e.rejected), {"wrong_width": 1})

    def test_list_columns_and_empty_values(self) -> None:
        e = parse_export(b"ID|CAT[]\n1| a ; b;;a \n2| ; \n3|\n")
        self.assertEqual([r[1] for r in e.rows], [("a", "b"), None, None])
        self.assertEqual(e.value(0, "CAT[]"), ("a", "b"))

    def test_bad_headers(self) -> None:
        for data in (b"", b"\n\n", b"A|A\n1|2\n"):
            with self.subTest(data=data), self.assertRaises(ExportError):
                parse_export(data)


# =================================================================================================== 2 dates

class DateTests(unittest.TestCase):
    MONTHS = LANG.months

    def test_every_format(self) -> None:
        cases = {"YYYY-MM-DD": "2021-03-04", "YYYYMMDD": "20210304", "YYYY/MM/DD": "2021/03/04",
                 "M/D/YYYY": "3/4/2021", "M/D/YY": "3/4/21", "D.M.YYYY": "4.3.2021", "D-MON-YYYY": "4-mar-2021",
                 "D-MON-YY": "04-Mar-21"}
        self.assertEqual(set(cases), set(DATE_FORMATS))
        for fmt, value in cases.items():
            with self.subTest(fmt=fmt):
                self.assertEqual(parse_date(value, fmt, self.MONTHS), date(2021, 3, 4))
        self.assertEqual(parse_date("2021-03-04T10:00:00", "YYYY-MM-DD", self.MONTHS), date(2021, 3, 4))
        self.assertEqual(parse_date("3/4/2021 10:00", "M/D/YYYY", self.MONTHS), date(2021, 3, 4))
        self.assertIsNone(parse_date("2021-02-30", "YYYY-MM-DD", self.MONTHS))
        self.assertIsNone(parse_date("4-xyz-2021", "D-MON-YYYY", self.MONTHS))

    def test_the_part_before_the_first_space_or_a_t_before_a_digit_is_parsed(self) -> None:
        # rule 1.3 as amended (A1): the cut is at the first space, or at a "T" a digit follows; "OCT" is not cut
        self.assertEqual(parse_date("4-OCT-2021", "D-MON-YYYY", self.MONTHS), date(2021, 10, 4))
        self.assertEqual(parse_date("04-OCT-21", "D-MON-YY", self.MONTHS), date(2021, 10, 4))
        self.assertEqual(parse_date("4-Oct-2021", "D-MON-YYYY", self.MONTHS), date(2021, 10, 4))
        self.assertEqual(parse_date("04-OCT-2021 00:00:00", "D-MON-YYYY", self.MONTHS), date(2021, 10, 4))
        self.assertEqual(parse_date("2021-10-04T08:00", "YYYY-MM-DD", self.MONTHS), date(2021, 10, 4))
        self.assertIsNone(parse_date("2021-10-04Tx", "YYYY-MM-DD", self.MONTHS))     # a T before a letter: no cut
        # a whole year of upper-case D-MON-YYYY dates reaches the share (it did not under the old cut)
        year = [f"{d:02d}-{m.upper()}-2021" for m in self.MONTHS for d in (1, 15, 28)]
        self.assertEqual(choose_date_format(year, self.MONTHS, 0.95), "D-MON-YYYY")

    def test_two_digit_years_on_both_sides_of_70(self) -> None:
        self.assertEqual(parse_date("1/2/69", "M/D/YY", self.MONTHS), date(2069, 1, 2))
        self.assertEqual(parse_date("1/2/70", "M/D/YY", self.MONTHS), date(1970, 1, 2))
        self.assertEqual(parse_date("2-jan-05", "D-MON-YY", self.MONTHS), date(2005, 1, 2))
        self.assertEqual(parse_date("2-jan-99", "D-MON-YY", self.MONTHS), date(1999, 1, 2))

    def test_the_share_rule(self) -> None:
        good = ["2021-01-%02d" % (i % 28 + 1) for i in range(19)]
        self.assertEqual(choose_date_format(good + ["junk"], self.MONTHS, 0.95), "YYYY-MM-DD")      # 19 of 20
        self.assertIsNone(choose_date_format(good[:18] + ["junk", "junk"], self.MONTHS, 0.95))   # 18 of 20
        # the first format that reaches the share wins, in the rule's order
        self.assertEqual(choose_date_format(["1/2/2021"] * 20, self.MONTHS, 0.95), "M/D/YYYY")

    def test_no_format_fails_the_draft(self) -> None:
        rows = Rows().add(60, "roof fell", "A").rows
        for r in rows:
            r["DATE"] = "sometime"
        with self.assertRaises(D.DraftError):
            drafted(rows)

    def test_rows_outside_the_training_window_are_read_only_at_their_date(self) -> None:
        rows = corpus_rows(seed=3)
        export = parse_export(pipe(rows))
        seen: list[tuple[int, str]] = []

        class Recording(Export):
            def value(self, row: int, name: str) -> Any:
                seen.append((row, name))
                return super().value(row, name)

        rec = Recording(**{f.name: getattr(export, f.name) for f in fields(export)})
        d = D.draft_export(rec, roles(), TRAIN, "rec_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        outside = {r for r, day in enumerate(d.dated.dates) if not TRAIN.contains(day)}
        self.assertTrue(outside)
        # rule 1.3 as amended (A2): outside the window only the date and the identifying columns (record id, site,
        # forbidden) are read, the latter only to refuse; never a narrative or a category
        self.assertEqual({name for r, name in seen if r in outside}, {"DATE", "ID", "SITE", "PERSON"})
        seen.clear()
        D.export_refusal(rec, roles(), PARAMS)
        self.assertEqual({name for _, name in seen}, {"ID", "SITE", "PERSON"})


# =================================================================================================== 3 role inference

class RoleInferenceTests(unittest.TestCase):
    def infer(self, data: bytes, rows: list[int] | None = None) -> dict[str, Any]:
        e = parse_export(data)
        rows = list(range(len(e))) if rows is None else rows
        return infer_roles(column_evidence(e, rows, LANG.months), PARAMS, LANG.site_words, LANG.category_words,
                           len(rows))

    def test_a_synthetic_export_with_known_roles(self) -> None:
        rows = corpus_rows(seed=5)
        got = self.infer(pipe(rows, ("PERSON", "ID", "DATE", "SITE", "CAT", "TEXT")))
        self.assertEqual(got, {"record_id": "ID", "site": "SITE", "date": "DATE", "narrative": "TEXT",
                               "category": "CAT"})
        self.assertEqual(roles_right(got, roles()), 5)

    def test_each_rule_and_its_ties(self) -> None:
        long = "one two three four five six seven eight nine"
        lines = ["NOTE_A|NOTE_B|D1|D2|K1|K2|PLANT|LOC|TYPE|GROUP"]
        for i in range(40):
            lines.append(f"{long}|{long}|2020-01-{i % 28 + 1:02d}|2020-02-{i % 28 + 1:02d}|k{i}|j{i}|p{i % 4}|"
                         f"l{i % 9}|t{i % 3}|g{i % 5}")
        got = self.infer(("\n".join(lines) + "\n").encode())
        self.assertEqual(got["narrative"], "NOTE_A")          # equal mean tokens: the leftmost
        self.assertEqual(got["date"], "D1")                   # equal fill: the leftmost
        self.assertEqual(got["record_id"], "K1")              # the leftmost all-distinct column (D2 repeats)
        self.assertEqual(got["site"], "PLANT")                # a site word wins over more distinct values
        self.assertEqual(got["category"], "TYPE")             # the leftmost label-like column with a category word

    def test_fallbacks_without_header_words(self) -> None:
        lines = ["REF|WHEN|WHO|LBL|TXT"]
        for i in range(30):
            lines.append(f"r{i}|2021-01-01|x{i % 7}|lab {i % 3}|" + " ".join(["word"] * 9))
        got = self.infer(("\n".join(lines) + "\n").encode())
        self.assertEqual(got["site"], "WHO")                  # most distinct values below the row count, <= 2 tokens
        self.assertEqual(got["record_id"], "REF")
        self.assertEqual(got["category"], "LBL")              # the label-like column with the most distinct values

    def test_no_narrative_when_short(self) -> None:
        got = self.infer(b"A|B\n1|two words\n2|three more words\n")
        self.assertIsNone(got["narrative"])


# =================================================================================================== 4 categories

class CategoryTests(unittest.TestCase):
    def test_fold_merge_and_the_label_rule(self) -> None:
        r = Rows().add(30, "x", "Roof Fall").add(30, "x", "ROOF FALL").add(10, "x", "roof  fall")
        d = drafted(r.rows)
        (cat,) = [c for c in d.categories if c.key == "roof fall"]
        self.assertEqual((cat.count, cat.label), (70, "ROOF FALL"))       # 30 and 30 tie: the smaller string
        self.assertEqual(set(cat.spellings), {"Roof Fall", "ROOF FALL", "roof  fall"})
        self.assertEqual({d.plan.value_map[s] for s in cat.spellings}, {"D-001"})

    def test_the_floor_in_records_and_sites(self) -> None:
        r = Rows().add(60, "x", "BIG")
        r.add(9, "x", "NINE").add(10, "x", "TEN")
        r.add(10, "x", "TWO SITES", sites=SITES[:2]).add(10, "x", "THREE SITES", sites=SITES[:3])
        d = drafted(r.rows)
        vm = d.plan.value_map
        self.assertNotIn("NINE", vm)
        self.assertNotIn("TWO SITES", vm)
        self.assertEqual((vm["TEN"], vm["THREE SITES"]), ("D-999", "D-999"))
        self.assertTrue(D.category_floor(10, 3, PARAMS))
        self.assertFalse(D.category_floor(9, 3, PARAMS))
        self.assertFalse(D.category_floor(10, 2, PARAMS))

    def test_the_predicate_minimum_and_non_specific_labels(self) -> None:
        r = Rows().add(50, "x", "FIFTY").add(49, "x", "FORTY NINE").add(80, "x", "Unknown or Other").add(60, "x", "?")
        d = drafted(r.rows)
        self.assertEqual(d.plan.ids, ("fifty",))
        self.assertEqual({d.plan.value_map[k] for k in ("FORTY NINE", "Unknown or Other", "?")}, {"D-999"})

    def test_the_cap(self) -> None:
        params = dict(PARAMS, max_predicates=3)
        r = Rows()
        for i, n in enumerate((90, 80, 70, 60, 60)):
            r.add(n, "x", f"CAT {chr(65 + i)}")
        d = drafted(r.rows, params=params)
        self.assertEqual(d.plan.ids, ("cat_a", "cat_b", "cat_c"))
        self.assertEqual({d.plan.value_map["CAT D"], d.plan.value_map["CAT E"]}, {"D-999"})
        self.assertEqual(PARAMS["max_predicates"], 199)

    def test_ids(self) -> None:
        self.assertEqual(D.slug("Slip or Fall (Person)", PARAMS, "c_"), "slip_or_fall_person")
        self.assertEqual(D.slug("4 wheel drive", PARAMS, "c_"), "c_4_wheel_drive")
        self.assertEqual(D.slug("X", PARAMS, "c_"), "c_x")
        self.assertEqual(D.slug("Café — à la carte", PARAMS, "c_"), "cafe_a_la_carte")
        reserved = sorted(w for w in RESERVED if re.fullmatch(r"[a-z]{2,10}", w))[0]
        self.assertEqual(D.slug(reserved.upper(), PARAMS, "c_"), "c_" + reserved)
        self.assertEqual(len(D.slug("a" * 50, PARAMS, "c_")), 36)
        cats = [D.Category(key=k, label=k, spellings=(k,), count=100 - i, sites=6)
                for i, k in enumerate(("other category", "roof-fall", "roof fall", "4x4"))]
        self.assertEqual(D.predicate_ids(cats, PARAMS, IDS), ["other_category_2", "roof_fall", "roof_fall_2", "c_4x4"])

    def test_code_order(self) -> None:
        r = Rows().add(60, "x", "BRONZE").add(70, "x", "AMBER").add(60, "x", "AARDVARK")
        d = drafted(r.rows)
        self.assertEqual(d.plan.ids, ("amber", "aardvark", "bronze"))
        self.assertEqual([d.plan.codes[p] for p in d.plan.ids], ["D-001", "D-002", "D-003"])
        self.assertEqual(d.files["codes.json"]["D-999"]["predicate"], "other_category")


# =================================================================================================== 5 lexicon

def lexicon_rows() -> Rows:
    """A corpus whose df(t), df(t, c) and p(c | t) are known by construction."""
    r = Rows()
    r.add(60, "plain words only", "AMBER").add(60, "plain words only", "BRONZE").add(60, "plain words only", "CYAN")
    r.add(10, "zeta here", "AMBER")                                    # df 10, p 1: kept
    r.add(9, "eta here", "AMBER")                                      # df 9: under the floor
    r.add(10, "theta here", "AMBER", sites=SITES[:2])                  # 2 sites: under the floor
    r.add(10, "theta2 sitecheck", "AMBER", sites=SITES[:3])            # 3 sites: kept ('sitecheck')
    r.add(30, "iota here", "AMBER").add(20, "iota here", "BRONZE")       # p 30/50 = 0.6: kept
    r.add(29, "kappa here", "AMBER").add(21, "kappa here", "BRONZE")     # p 29/50 = 0.58: refused
    r.add(9, "numa here", "AMBER").add(3, "numa here", "OTHERWISE")    # df(t, c) 9: refused
    r.add(10, "xina here", "AMBER").add(3, "xina here", "OTHERWISE")   # df(t, c) 10, p 10/13: kept
    r.add(15, "abc1 ab the not evidence refusedword", "BRONZE")          # digits, short, stop, negation words
    r.add(12, "roof fell. fell", "BRONZE")                               # no bigram across a sentence end
    r.add(12, "roof fell", "BRONZE")                                     # the bigram 'roof fell'
    r.add(12, "bolt, nut", "BRONZE")                                     # no bigram across a comma
    r.add(15, "zorro rides", "BRONZE", person="Zorro")                   # a person value: refused, alone or in a bigram
    r.add(30, "omega here", "AMBER", day=date(2023, 5, 1))             # only in test rows: never a term
    r.add(10, "otherwise", "OTHERWISE")
    return r


class LexiconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.d = drafted(lexicon_rows().rows)
        cls.terms = {t: p for p, ts in cls.d.learned.items() for t in ts}

    def test_the_floors(self) -> None:
        t = self.terms
        self.assertEqual(t.get("zeta"), "amber")
        self.assertNotIn("eta", t)
        self.assertNotIn("theta", t)
        self.assertEqual(t.get("sitecheck"), "amber")

    def test_the_share_at_and_just_under(self) -> None:
        self.assertEqual(self.terms.get("iota"), "amber")
        self.assertNotIn("kappa", self.terms)
        self.assertEqual(D.term_score(30, 50), Fraction(3, 5))

    def test_df_t_c_on_both_sides(self) -> None:
        self.assertNotIn("numa", self.terms)
        self.assertEqual(self.terms.get("xina"), "amber")

    def test_refused_tokens(self) -> None:
        for word in ("abc1", "ab", "the", "not", "evidence"):
            self.assertNotIn(word, self.terms)
            self.assertFalse(any(word in t.split() for t in self.terms), word)
        self.assertEqual(self.terms.get("refusedword"), "bronze")

    def test_bigrams(self) -> None:
        self.assertEqual(self.terms.get("roof fell"), "bronze")
        self.assertNotIn("bolt nut", self.terms)
        self.assertEqual(D.candidate_terms("Roof fell. Fell", LANG, PARAMS), {"roof", "fell", "roof fell"})
        self.assertEqual(D.candidate_terms("bolt, nut", LANG, PARAMS), {"bolt", "nut"})
        self.assertEqual(D.candidate_terms("bolt  nut", LANG, PARAMS), {"bolt", "nut", "bolt nut"})   # folded space

    def test_a_forbidden_value_is_refused(self) -> None:
        self.assertNotIn("zorro", self.terms)
        self.assertNotIn("zorro rides", self.terms)
        self.assertEqual(self.terms.get("rides"), "bronze")

    def test_the_placeholder(self) -> None:
        self.assertEqual(self.d.learned["cyan"], [])
        self.assertEqual(self.d.lexicon["cyan"], ["unlearned-cyan"])
        self.assertEqual(D.placeholder("roof_fall_2", IDS), "unlearned-roof-fall-2")
        self.assertEqual(self.d.facts["placeholders"], 1)

    def test_a_term_only_in_test_rows_never_enters(self) -> None:
        self.assertNotIn("omega", self.terms)
        self.assertNotIn("omega", self.d.table.terms)

    def test_ties_and_the_cap(self) -> None:
        r = Rows().add(60, "plain words", "BB").add(60, "plain words", "AA")
        rows = r.rows
        for i in range(12):        # both labels on one row (a list column): p ties at 1, df ties: the smaller id
            rows.append({"ID": f"T{i:03d}", "SITE": SITES[i % 6], "DATE": "1/2/2019", "TEXT": "shared",
                         "CAT": "AA;BB", "PERSON": ""})
        for k in range(55):        # 55 terms of AA with df 10 (and four with more): only 50 kept, in order
            n = 10 + (4 if k < 4 else 0)
            for i in range(n):
                rows.append({"ID": f"K{k:02d}{i:03d}", "SITE": SITES[i % 6], "DATE": "1/2/2019",
                             "TEXT": f"term{chr(97 + k // 26)}{chr(97 + k % 26)}", "CAT": "AA", "PERSON": ""})
        header = ("ID", "SITE", "DATE", "TEXT", "CAT[]", "PERSON")
        for row in rows:
            row["CAT[]"] = row["CAT"]
        d = D.draft_export(parse_export(pipe(rows, header)), roles(category="CAT[]"), TRAIN, "tie_pack",
                           params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertIn("shared", d.learned["aa"])
        self.assertNotIn("shared", d.learned["bb"])
        self.assertEqual(len(d.learned["aa"]), 50)
        self.assertEqual(d.learned["aa"][:4], ["termaa", "termab", "termac", "termad"])   # df 14 first
        self.assertNotIn("termcc", d.learned["aa"])                                       # the 55th is cut


# =================================================================================================== 6 determinism

class DeterminismTests(TempDir):
    def test_same_export_same_bytes_and_shuffled_rows_same_pack(self) -> None:
        rows = corpus_rows(seed=11)
        a = D.pack_bytes(drafted(rows).files)
        b = D.pack_bytes(drafted(copy.deepcopy(rows)).files)
        shuffled = copy.deepcopy(rows)
        random.Random(4).shuffle(shuffled)
        c = D.pack_bytes(drafted(shuffled).files)
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        h1 = D.write_pack(drafted(rows).files, self.tmp / "p1")
        h2 = D.write_pack(drafted(shuffled).files, self.tmp / "p2")
        self.assertEqual(h1, h2)


# =================================================================================================== 7 loading

class LoadingTests(TempDir):
    def test_drafted_packs_load_single_and_multi_label(self) -> None:
        single = corpus_rows(seed=2)
        multi = copy.deepcopy(single)
        for i, r in enumerate(multi):
            r["CAT[]"] = r["CAT"] + (";HAULAGE" if i % 7 == 0 else "")
        header = ("ID", "SITE", "DATE", "TEXT", "CAT[]", "PERSON")
        for name, export, rl in (("single", parse_export(pipe(single)), roles()),
                                 ("multi", parse_export(pipe(multi, header)), roles(category="CAT[]")),
                                 ("jsonl", parse_export(("\n".join(json.dumps(r) for r in single) + "\n").encode()),
                                  roles())):
            with self.subTest(export=name):
                d = D.draft_export(export, rl, TRAIN, f"load_{name}", params=PARAMS, lang=LANG, template=TEMPLATE)
                D.write_pack(d.files, self.tmp / name)
                pack, error = D.load_written(self.tmp / name)
                self.assertIsNone(error)
                self.assertEqual(set(pack.predicates), {"other_category", *d.plan.ids})
                self.assertGreaterEqual(len(pack.fixtures), 40)
                self.assertEqual(pack.mapping()["primary_entity_type"], "export_scope")

    def test_a_declared_entity_column_becomes_an_alias_only_type(self) -> None:
        rows = corpus_rows(seed=7)
        for i, r in enumerate(rows):
            r["UNIT"] = "Rare unit" if i % 50 == 0 else ("Unit A" if i % 2 else "unit-b")
        export = parse_export(pipe(rows, HEADER + ("UNIT",)))
        rl = roles(entities=["UNIT"])
        d = D.draft_export(export, rl, TRAIN, "entity_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        (et,) = d.etypes
        self.assertEqual((et.id, et.ids), ("unit", ("V-UNIT-A", "V-UNIT-B")))        # the rare value is left out
        D.write_pack(d.files, self.tmp / "p")
        pack = load_pack_dir(self.tmp / "p")
        self.assertFalse(pack.entity_types["unit"].egress)
        norm, rejected = D.normalised_rows(export, rl, d.dated, TRAIN, TEMPLATE, d.etypes)
        self.assertEqual(rejected, {})
        mapped = map_rows(norm, pack)
        self.assertEqual(dict(mapped.rejected), {})
        canon = Canonicaliser(pack)
        resolved = {canon.resolve_exact("unit", v).entity_id for r in mapped.records for v in r["entities"]["unit"]
                    if canon.resolve_exact("unit", v) is not None}
        self.assertEqual(resolved, {"V-UNIT-A", "V-UNIT-B"})
        result = C.check_pack(self.tmp / "p", export, rl, TRAIN, params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["checks"]["value_floor"]["entity_ids"], 2)

    def test_fixtures_and_generator_hold_no_export_text(self) -> None:
        rows = corpus_rows(seed=6)
        d = drafted(rows)
        D.write_pack(d.files, self.tmp / "p")
        strings = [s for _, s in C.pack_strings(self.tmp / "p")]
        narratives = [r["TEXT"] for r in rows]
        self.assertEqual(C.ngram_hits(strings, narratives, PARAMS["ngram_tokens"]), set())
        gen = json.dumps(d.files["generator.json"]) + json.dumps(d.files[D.FIXTURES])
        for r in rows:
            self.assertNotIn(r["TEXT"], gen)
            self.assertNotIn(r["ID"], gen)
            self.assertNotIn(r["PERSON"], gen)
        allowed = {w for ws in d.lexicon.values() for t in ws for w in t.split()}
        allowed |= set(LANG.other_phrase.split()) | set(LANG.scope_alias.split())
        template_words = set(re.findall(r"\w+", json.dumps(LANG.templates).lower()))
        for line in d.files[D.FIXTURES]:
            words = set(re.findall(r"\w+", line["record"]["narrative"].lower()))
            self.assertLessEqual(words - template_words - {w for t in allowed for w in re.findall(r"\w+", t)}, set())


# ================================================================================================ 8 normalised export

class NormalisedExportTests(TempDir):
    def test_maps_through_the_drafted_mapping_with_no_rejection(self) -> None:
        rows = corpus_rows(seed=8)
        rows[0]["SITE"] = "Upper Site"         # a space: outside the site id pattern
        rows[1]["SITE"] = "MIXED.Case-1"
        export = parse_export(pipe(rows))
        d = D.draft_export(export, roles(), TRAIN, "norm_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        D.write_pack(d.files, self.tmp / "p")
        pack = load_pack_dir(self.tmp / "p")
        out, rejected = D.normalised_rows(export, roles(), d.dated, D.parse_window("2010-01-01", "2030-12-31"),
                                          TEMPLATE)
        self.assertEqual(rejected, {"bad_site": 1})
        mapped = map_rows(out, pack)
        self.assertEqual((len(mapped.records), dict(mapped.rejected), mapped.unmapped_code_values),
                         (len(out), {}, 0))
        by_ref = {r["record_ref"]: r for r in out}
        self.assertEqual(by_ref[rows[1]["ID"]]["site"], "mixed.case-1")
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["received_date"]) for r in out))
        self.assertTrue(all(r["scope"] == "ALL" for r in out))


# =================================================================================================== 9 pipeline

def weekly_rows(weeks: int = 40, seed: int = 9) -> list[dict[str, str]]:
    rng = random.Random(seed)
    rows = []
    start = date(2019, 1, 7)
    cues = {"ROOF FALL": ["roof", "rock"], "HAULAGE": ["shuttle", "conveyor"]}
    for w in range(weeks):
        for site in SITES:
            for _ in range(3):
                cat = rng.choice(sorted(cues))
                day = start + timedelta(days=7 * w + rng.randrange(5))
                rows.append({"SITE": site, "DATE": day.isoformat(), "CAT": cat, "PERSON": "",
                             "TEXT": " ".join(rng.sample(cues[cat], 1) + rng.sample(FILLER, 4)) + "."})
    for i, r in enumerate(rows):
        r["ID"] = f"W{i:05d}"
    return rows


class PipelineTests(TempDir):
    def test_predicates_attach_to_the_scope_and_the_audit_runs(self) -> None:
        rows = weekly_rows()
        export = parse_export(pipe(rows))
        window = D.parse_window("2019-01-01", "2019-12-31")
        d = D.draft_export(export, roles(), window, "pipe_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        D.write_pack(d.files, self.tmp / "pack")
        pack = load_pack_dir(self.tmp / "pack")
        norm, _ = D.normalised_rows(export, roles(), d.dated, window, TEMPLATE)
        record = map_rows([dict(norm[0], codes=[])], pack).records[0]
        canon = Canonicaliser(pack)
        _, text, claims = sense(dict(record, codes=[]), pack, canon, LexicalExtractor(pack, canon))
        self.assertTrue(claims)
        self.assertEqual({(c.entity_type, c.entity_id) for c in claims}, {("export_scope", "ALL")})
        D.write_jsonl(norm, self.tmp / "records.jsonl")
        (self.tmp / "outcomes.csv").write_text("outcome_id,opened,entity_type,entity_id,predicate\n")
        with contextlib.redirect_stdout(io.StringIO()):
            code = AUDIT.main(["run", "--pack", str(self.tmp / "pack"), "--records", str(self.tmp / "records.jsonl"),
                               "--outcomes", str(self.tmp / "outcomes.csv"), "--out", str(self.tmp / "audit")])
        self.assertEqual(code, 0)
        summary = REP.audit_summary(self.tmp / "audit" / "audit.json")
        self.assertGreater(summary["records"], 0)
        self.assertTrue(summary["channels_run"]["X"] and summary["channels_run"]["S"])


# =================================================================================================== 10 check

class CheckTests(TempDir):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = corpus_rows(seed=12)
        cls.export = parse_export(pipe(cls.rows))
        cls.d = D.draft_export(cls.export, roles(), TRAIN, "check_pack", params=PARAMS, lang=LANG, template=TEMPLATE)

    def fresh(self) -> Path:
        p = self.tmp / f"p{len(list(self.tmp.iterdir()))}"
        D.write_pack(self.d.files, p)
        return p

    def check(self, p: Path) -> dict[str, Any]:
        return C.check_pack(p, self.export, roles(), TRAIN, params=PARAMS, lang=LANG, template=TEMPLATE)

    def test_a_clean_pack_passes(self) -> None:
        result = self.check(self.fresh())
        self.assertTrue(result["passed"], result)
        self.assertGreater(result["checks"]["term_floor"]["terms"], 0)

    def edit(self, p: Path, name: str, fn: Any) -> None:
        if name.endswith(".jsonl"):
            lines = [json.loads(x) for x in (p / name).read_text().splitlines()]
            fn(lines)
            (p / name).write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))
            return
        obj = json.loads((p / name).read_text())
        fn(obj)
        (p / name).write_text(json.dumps(obj, indent=2, sort_keys=True))

    def test_a_term_under_the_floor_fails(self) -> None:
        p = self.fresh()
        pid = self.d.plan.ids[0]
        self.edit(p, "vocabulary.json", lambda v: v["predicates"][pid]["lexicon"]["en"].append("quasar"))
        result = self.check(p)
        self.assertFalse(result["checks"]["term_floor"]["passed"])
        self.assertEqual(result["checks"]["term_floor"]["failures"], 1)
        self.assertFalse(result["passed"])

    def test_a_person_value_written_as_a_label_fails(self) -> None:
        p = self.fresh()
        pid = self.d.plan.ids[0]

        def plant(v: dict) -> None:
            v["predicates"][pid]["label"] = "PERSON 2"
        self.edit(p, "vocabulary.json", plant)
        result = self.check(p)
        self.assertFalse(result["checks"]["refused_strings"]["passed"])
        self.assertEqual(result["checks"]["refused_strings"]["files"], ["vocabulary.json"])
        self.assertEqual(result["checks"]["refused_strings"]["kinds"]["equal"], 1)
        self.assertNotIn("Person 2", json.dumps(result))

    def test_a_person_value_inside_a_term_fails(self) -> None:
        p = self.fresh()
        pid = self.d.plan.ids[0]
        self.edit(p, "vocabulary.json", lambda v: v["predicates"][pid]["lexicon"]["en"].append("person 3 roof"))
        self.assertFalse(self.check(p)["checks"]["refused_terms"]["passed"])

    def test_an_eight_gram_of_a_narrative_in_a_fixture_fails(self) -> None:
        p = self.fresh()
        text = next(r["TEXT"] for r in self.rows if len(re.findall(r"\w+", r["TEXT"])) >= 8)

        def plant(lines: list) -> None:
            lines[0]["record"]["narrative"] = "Copied: " + text
        self.edit(p, "fixtures/records.jsonl", plant)
        result = self.check(p)
        self.assertFalse(result["checks"]["ngram"]["passed"])
        self.assertEqual(result["checks"]["ngram"]["files"], ["fixtures/records.jsonl"])

    def test_a_value_map_spelling_under_the_floor_fails(self) -> None:
        p = self.fresh()
        self.edit(p, "mapping.json", lambda m: m["codes"][0]["value_map"].update({"RARE CAT": "D-999"}))
        self.assertFalse(self.check(p)["checks"]["value_floor"]["passed"])

    def test_term_presence_counts_overlapping_terms_independently(self) -> None:
        found = C.term_presence(["The roof bolter stopped. Roof.", "bolter"], ["roof", "roof bolter", "bolter"])
        self.assertEqual(found, [{"roof", "roof bolter", "bolter"}, {"bolter"}])

    def test_the_label_scan(self) -> None:
        settings = json.loads(SETTINGS_PATH.read_text())
        names = C.column_names(settings)
        self.assertIn("summary", names)
        self.assertIn("components", names)
        self.assertNotIn("reporter", names)
        self.assertEqual(C.label_hits(["ROOF FALL", "summary"], names), 1)


# =================================================================================================== 11 score

def arm_settings(B: int = 200, hand: dict[str, str] | None = None, margin: float = 0.1,
                  criterion: dict | None = None) -> dict[str, Any]:
    s = json.loads(SETTINGS_PATH.read_text())
    s["bootstrap"]["B"] = B
    arm = s["arms"]["msha"]
    arm["roles"] = roles().to_json()["roles"]
    arm["per_company"] = 50
    arm["criterion"] = criterion or {"id": "C1", "margin": margin}
    if hand is not None:
        arm["hand_packs"] = hand
    return s


def write_arm(tmp: Path, companies: dict[str, list[dict[str, str]]], header: tuple[str, ...] = HEADER) -> Path:
    exports = tmp / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    listing = {}
    for label, rows in companies.items():
        (exports / f"{label}.txt").write_bytes(pipe(rows, header))
        listing[label] = {"file": f"{label}.txt", "rows": len(rows)}
    (exports / "companies.json").write_text(json.dumps(listing))
    return exports


class ScoreMathTests(unittest.TestCase):
    def test_micro_macro_and_coverage_by_hand(self) -> None:
        f = frozenset
        preds = [f({"a"}), f({"a", "b"}), f(), f({"b"})]
        golds = [f({"a"}), f({"b"}), f({"a"}), f({"a"})]
        comps = ["x", "x", "y", "y"]
        counts = [SC.record_counts(p, g) for p, g in zip(preds, golds)]
        self.assertEqual(counts, [(1, 0, 0), (1, 1, 0), (0, 0, 1), (0, 1, 1)])
        m = SC.micro(counts)
        self.assertEqual((m["tp"], m["fp"], m["fn"]), (2, 2, 2))
        self.assertAlmostEqual(m["f1"], 0.5)
        # pairs with gold: (x,a): tp1 fp1 -> 2/3; (x,b): tp1 -> 1; (y,a): fn2 -> 0; (y,b) has no gold
        self.assertAlmostEqual(SC.macro_f1(comps, preds, golds), (2 / 3 + 1 + 0) / 3)
        out = SC.reader_metrics(comps, preds, golds, B=50, seed="t")
        self.assertEqual(out["coverage"], 0.75)
        self.assertEqual(out["macro"]["pairs"], 3)

    def test_every_interval_uses_the_same_draws(self) -> None:
        f = frozenset
        rng = random.Random(3)
        golds = [f({rng.choice("ab")}) for _ in range(40)]
        a = [f({rng.choice("ab")}) for _ in range(40)]
        b = [f({"a"}) for _ in range(40)]
        ca = [SC.record_counts(p, g) for p, g in zip(a, golds)]
        cb = [SC.record_counts(p, g) for p, g in zip(b, golds)]
        boot_a = stats.bootstrap_f1(ca, B=30, seed="d001:boot:x")
        diff = SC.difference(a, b, golds, B=30, seed="d001:boot:x")
        rng_a, rng_b = random.Random("d001:boot:x"), random.Random("d001:boot:x")
        draws_a = [[rng_a.randrange(40) for _ in range(40)] for _ in range(30)]
        draws_b = [[rng_b.randrange(40) for _ in range(40)] for _ in range(30)]
        self.assertEqual(draws_a, draws_b)
        reps = sorted(stats.f1_from_counts(*[sum(ca[j][i] for j in d) for i in range(3)]) for d in draws_a)
        self.assertAlmostEqual(boot_a["ci_low"], stats.percentile(reps, 2.5))
        self.assertAlmostEqual(diff["exact_diff"], SC.exact_f1(ca) - SC.exact_f1(cb))
        macro = SC.bootstrap_macro(["c"] * 40, a, golds, B=30, seed="d001:boot:x")
        pairs, contrib = SC._pairs(["c"] * 40, a, golds)
        expect = sorted(v for v in (SC._macro_of(len(pairs), d, contrib) for d in draws_a) if v is not None)
        self.assertAlmostEqual(macro["ci_low"], stats.percentile(expect, 2.5))

    def test_the_criteria_at_their_margins(self) -> None:
        pooled = {"readers": {r: {"micro": {"f1": 0.5}} for r in SC.READERS}, "differences": {}}
        pooled["readers"]["drafted"]["micro"]["f1"] = 0.6
        pooled["readers"]["permuted_labels"]["micro"]["f1"] = 0.55
        pooled["differences"] = {r: {"diff": 0.1, "exact_diff": Fraction(1, 10), "ci_low": 0.001, "ci_high": 0.2}
                                 for r in SC.CONTROLS}
        c1 = SC.criterion({"id": "C1", "margin": 0.1}, pooled, None, [], 5)
        self.assertTrue(c1["passed"])
        self.assertEqual(c1["best_control"], "permuted_labels")
        pooled["differences"]["permuted_labels"]["exact_diff"] = Fraction(99, 1000)
        self.assertFalse(SC.criterion({"id": "C1", "margin": 0.1}, pooled, None, [], 5)["passed"])
        pooled["differences"]["permuted_labels"].update(exact_diff=Fraction(1, 5), ci_low=0.0)
        self.assertFalse(SC.criterion({"id": "C1", "margin": 0.1}, pooled, None, [], 5)["passed"])
        matched = {"hand": {"pack": {"difference": {"diff": -0.01, "ci_low": -0.0499, "ci_high": 0.02, "n": 9}}}}
        spec = {"id": "C2", "margin": 0.05, "against": "pack"}
        self.assertTrue(SC.criterion(spec, pooled, matched, [], 6)["passed"])
        matched["hand"]["pack"]["difference"]["ci_low"] = -0.05
        self.assertFalse(SC.criterion(spec, pooled, matched, [], 6)["passed"])

    def test_a_criterion_that_cannot_be_computed_fails(self) -> None:
        pooled = {"readers": {r: {"micro": {"f1": None}} for r in SC.READERS}, "differences": {}}
        self.assertFalse(SC.criterion({"id": "C1", "margin": 0.1}, pooled, None, [], 5)["passed"])
        self.assertFalse(SC.criterion({"id": "C1", "margin": 0.1}, pooled, None, ["c2"], 5)["passed"])
        self.assertFalse(SC.criterion({"id": "C1", "margin": 0.1}, pooled, None, [], 0)["passed"])
        self.assertFalse(SC.criterion({"id": "C2", "margin": 0.05, "against": "pack"}, pooled, None, [], 6)["passed"])


class ControlTests(unittest.TestCase):
    def test_the_majority_prior_and_label_names(self) -> None:
        r = Rows().add(80, "x", "ROOF FALL").add(60, "x", "ROOF/ ceiling (cave)").add(55, "x", "HAULAGE AND roof")
        d = drafted(r.rows)
        top = min(d.plan.ids, key=lambda p: (-d.plan.counts[p], p))
        self.assertEqual(top, "roof_fall")
        lex = D.label_name_lexicon(d.plan, LANG)
        self.assertEqual(D.label_parts("ROOF/ ceiling (cave)", LANG), ["roof", "ceiling", "cave"])
        self.assertEqual(D.label_parts("HAULAGE AND roof", LANG), ["haulage", "roof"])
        self.assertEqual(lex["roof_fall"], ["roof fall"])
        self.assertEqual(lex["roof_ceiling_cave"], ["roof", "ceiling", "cave"])     # 'roof': the larger of the two
        self.assertEqual(lex["haulage_and_roof"], ["haulage"])
        r2 = Rows().add(80, "x", "AB").add(60, "x", "CD")
        lex2 = D.with_placeholders(D.label_name_lexicon(drafted(r2.rows).plan, LANG), IDS)
        self.assertEqual(lex2["ab"], ["unlearned-ab"])                               # 2 characters: no term

    def test_the_permuted_control_keeps_counts_and_uses_its_seed(self) -> None:
        d = drafted(corpus_rows(seed=13))
        p1 = D.permuted_labels(d.corpus, "d001:permute:msha:c1")
        p2 = D.permuted_labels(d.corpus, "d001:permute:msha:c1")
        p3 = D.permuted_labels(d.corpus, "d001:permute:msha:c2")
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)
        self.assertEqual(sorted(p1), sorted(d.corpus.categories))
        order = sorted(range(len(d.corpus)), key=lambda i: d.corpus.refs[i])
        values = [d.corpus.categories[i] for i in order]
        random.Random("d001:permute:msha:c1").shuffle(values)
        self.assertEqual([p1[i] for i in order], values)

    def test_the_permuted_control_is_label_blind(self) -> None:
        # texts that name their label perfectly: the drafted lexicon learns them, the permuted one cannot
        r = Rows()
        for i in range(120):
            cat = "AMBER" if i % 2 else "BRONZE"
            r.add(1, f"{cat.lower()}word", cat, sites=(SITES[i % 6],))
        d = drafted(r.rows)
        self.assertEqual(d.learned["amber"], ["amberword"])
        perm = D.assign_terms(d.table, d.eligible, D.record_labels(D.permuted_labels(d.corpus, "d001:permute:t:c"),
                                                                   d.plan), d.plan.ids, PARAMS)
        self.assertEqual(perm, {"amber": [], "bronze": []})


class SampleAndReadTests(TempDir):
    def test_the_draw_and_the_de_duplication(self) -> None:
        r = Rows().add(70, "roof rock training", "ROOF FALL").add(70, "shuttle haul training", "HAULAGE")
        rows = r.rows
        test_day = date(2023, 2, 3)
        add = Rows().add(1, "Roof rock TRAINING", "ROOF FALL", day=test_day)       # equals a training narrative
        add.add(3, "same test text", "HAULAGE", day=test_day)                       # repeated in test
        add.add(1, "own words", "OTHER", day=test_day)                              # no specific predicate
        for i in range(30):
            add.add(1, f"fresh roof words number{i:02d}", "ROOF FALL", day=test_day)
        for i, row in enumerate(add.rows):
            row["ID"] = f"Z{i:03d}"
        export = parse_export(pipe(rows + add.rows))
        d = D.draft_export(export, roles(), TRAIN, "draw_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        sample, counts = SC.draw_sample(export, d, TEST, "d001:sample:msha:c1", 200, "c1")
        self.assertEqual((counts["in_training"], counts["repeated_in_test"], counts["no_specific"]), (1, 2, 1))
        self.assertEqual(counts["eligible"], 31)
        self.assertIn("Z001", {s.ref for s in sample})                              # the smallest id of the repeats
        few, _ = SC.draw_sample(export, d, TEST, "d001:sample:msha:c1", 5, "c1")
        eligible = sorted([s for s in sample], key=lambda s: s.ref)
        expect = random.Random("d001:sample:msha:c1").sample(eligible, 5)
        self.assertEqual([s.ref for s in few], [s.ref for s in expect])

    def test_the_reader_never_sees_the_scored_column(self) -> None:
        rows = corpus_rows(seed=14)
        export = parse_export(pipe(rows))
        d = D.draft_export(export, roles(), TRAIN, "blind_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        D.write_pack(d.files, self.tmp / "p")
        pack = load_pack_dir(self.tmp / "p")
        sample = [SC.Sampled("c1", i, rows[i]["ID"], rows[i]["TEXT"], frozenset({"roof_fall"}), (rows[i]["CAT"],))
                  for i in range(len(rows)) if rows[i]["CAT"] == "ROOF FALL"][:5]
        reader_rows = SC.drafted_rows(sample, export, d)
        for row in reader_rows:
            self.assertNotIn("codes", row)
            self.assertNotIn(rows[0]["CAT"], json.dumps(row))
        seen = []
        score_module = SC
        real = score_module.sense

        def spy(record: Any, *args: Any, **kw: Any) -> Any:
            seen.append(record)
            return real(record, *args, **kw)
        score_module.sense = spy
        try:
            preds, rejected = SC.read_records(pack, reader_rows, {"other_category"})
        finally:
            score_module.sense = real
        self.assertEqual(rejected, 0)
        self.assertTrue(all(r["codes"] == [] for r in seen))
        # a narrative without a cue reads as nothing, whatever its filed category
        empty = [dict(reader_rows[0], narrative="Nothing to see.")]
        self.assertEqual(SC.read_records(pack, empty, {"other_category"})[0], [frozenset()])
        self.assertTrue(any("roof_fall" in p for p in preds))

    def test_the_matched_space_with_a_hand_pack_that_merges_two_names(self) -> None:
        names = {"ENGINE": ["piston", "cylinder"], "ENGINE AND COOLING": ["radiator", "coolant"],
                 "BRAKES": ["pedal", "rotor"]}
        rng = random.Random(15)
        rows = []
        for year in (2019, 2023):
            for name, cues in names.items():
                for _ in range(70 if year == 2019 else 25):
                    rows.append({"record_ref": "", "site": rng.choice(SITES),
                                 "received_date": f"{year}-03-0{rng.randint(1, 9)}",
                                 "codes[]": name, "narrative": " ".join(rng.sample(cues, 1) + rng.sample(FILLER, 3)),
                                 "scope": "ALL"})
        for i, r in enumerate(rows):
            r["record_ref"] = f"H{i:04d}"
        header = ("record_ref", "site", "received_date", "codes[]", "narrative", "scope")
        rl = roles(record_id="record_ref", site="site", date="received_date", narrative="narrative",
                   category="codes[]", forbidden=[])
        export = parse_export(pipe(rows, header))
        d = D.draft_export(export, rl, TRAIN, "matched_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        D.write_pack(d.files, self.tmp / "hand")
        vm_path = self.tmp / "hand" / "mapping.json"
        mapping = json.loads(vm_path.read_text())
        vm = mapping["codes"][0]["value_map"]
        vm["ENGINE AND COOLING"] = vm["ENGINE"]                    # the hand pack merges two names
        vm_path.write_text(json.dumps(mapping))
        hand = load_pack_dir(self.tmp / "hand")
        space = SC.matched_space(d, hand)
        self.assertEqual(space, {"BRAKES": "brakes", "ENGINE": "engine", "ENGINE AND COOLING": "engine"})
        by = SC.names_of(d)
        self.assertEqual(by["engine_and_cooling"], ["ENGINE AND COOLING"])
        # through the arm: settings name the hand pack by an absolute path
        exports = write_arm(self.tmp / "arm", {"c1": rows}, header)
        s = arm_settings(hand={"hand": str(self.tmp / "hand")},
                          criterion={"id": "C2", "margin": 0.05, "against": "hand"})
        s["arms"]["msha"]["roles"] = rl.to_json()["roles"]
        doc = SC.run_arm(s, "0" * 64, "msha", exports, self.tmp / "out", lambda: "c" * 40)
        m = doc["matched"]["hand"]["hand"]
        self.assertGreater(m["records"], 0)
        self.assertIn("passed", doc["criterion"])
        self.assertEqual(doc["companies"]["c1"]["hand"]["hand"]["matched_names"], 3)
        self.assertEqual(doc["criterion"]["matched_names"], {"c1": 3})          # printed beside C2 (A8)


class ArmTests(TempDir):
    def test_an_arm_end_to_end_holds_aggregates_only(self) -> None:
        rows1, rows2 = corpus_rows(seed=21, per_cat=90), corpus_rows(seed=22, per_cat=90)
        for i, r in enumerate(rows2):
            r["ID"] = f"B{i:05d}"
        exports = write_arm(self.tmp, {"c1": rows1, "c2": rows2})
        doc = SC.run_arm(arm_settings(), "0" * 64, "msha", exports, self.tmp / "out", lambda: "c" * 40)
        self.assertEqual(doc["errors"], [])
        self.assertEqual(set(doc["pooled"]["readers"]), set(SC.READERS))
        self.assertEqual(doc["criterion"]["id"], "C1")
        text = (self.tmp / "out" / "arm.json").read_text()
        for r in rows1 + rows2:
            self.assertNotIn(r["TEXT"], text)
            self.assertNotIn(f'"{r["ID"]}"', text)
            self.assertNotIn(r["PERSON"], text)
        for label in ("c1", "c2"):
            self.assertTrue((self.tmp / "out" / label / "pack" / "pack.json").is_file())
            self.assertTrue(json.loads((self.tmp / "out" / label / "check.json").read_text())["passed"])
        self.assertTrue(all(isinstance(v, (int, float)) for v in
                            [doc["pooled"]["readers"]["drafted"]["micro"]["f1"]]))

    def test_a_company_that_cannot_be_drafted_fails_the_criterion(self) -> None:
        bad = Rows().add(30, "x", "TOO FEW").rows
        exports = write_arm(self.tmp, {"c1": corpus_rows(seed=23), "c2": bad})
        doc = SC.run_arm(arm_settings(), "0" * 64, "msha", exports, self.tmp / "out", lambda: "c" * 40)
        self.assertEqual(doc["errors"], ["c2"])
        self.assertFalse(doc["criterion"]["passed"])


# =================================================================================================== 12 report

def fake_audit(path: Path, records: int = 5) -> None:
    channel = {"reason": None, "alert_timeline": [{"available_date": "2023-01-01", "week": "2022-W52", "keys": []}],
               "by_outcome": []}
    doc = {"export": {"records": records, "sites": ["a", "b"]}, "weeks": {"evaluated_weeks": 10},
           "channels": {"X": channel, "S": channel, "R_mf": {"reason": "none", "by_outcome": []}}, "review": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


class ReportTests(TempDir):
    @classmethod
    def setUpClass(cls) -> None:
        cls._t = tempfile.TemporaryDirectory()
        cls.base = Path(cls._t.name)
        cls.rows = corpus_rows(seed=31, per_cat=90)
        cls.exports = write_arm(cls.base / "msha", {"c1": cls.rows})
        cls.settings = arm_settings()
        del cls.settings["arms"]["nhtsa"]
        SC.run_arm(cls.settings, "0" * 64, "msha", cls.exports, cls.base / "arms" / "msha", lambda: "c" * 40)
        fake_audit(cls.base / "audit" / "audit.json")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._t.cleanup()

    def run_report(self, **kw: Any) -> tuple[dict, list[str]]:
        printed: list[str] = []
        doc = REP.run_report(self.settings, "settings.json", "0" * 64, [self.base / "arms" / "msha"],
                             self.base / "audit" / "audit.json", self.tmp / "report", lambda: "c" * 40,
                             emit=printed.append, **kw)
        return doc, printed

    def test_sentinels_never_appear(self) -> None:
        doc, printed = self.run_report()
        self.assertNotEqual(doc["verdict"], "withheld")
        text = (self.tmp / "report" / "report.json").read_text() + (self.tmp / "report" / "report.md").read_text()
        for r in self.rows:
            self.assertNotIn(r["TEXT"], text)
            self.assertIsNone(re.search(rf"(?<![A-Za-z0-9]){re.escape(r['ID'])}(?![A-Za-z0-9])", text))
            self.assertNotIn(r["PERSON"], text)
        self.assertEqual(set(doc["criteria"]), {"C1", "M1", "M2", "M3", "M4"})
        self.assertTrue(doc["criteria"]["M4"]["passed"])
        self.assertTrue(doc["criteria"]["M2"]["passed"])
        # amendment A8 and A9: what the numbers do not say is said beside them
        md = (self.tmp / "report" / "report.md").read_text()
        for needle in ("It is not an interval for the field in general.",
                       "label_names is a mechanical split of each label",
                       "Roles right: the inference's header words and markers were chosen knowing both sources'",
                       "a pass means no worse than that list of names"):
            self.assertIn(needle, md)
        self.assertIn("exact", doc["criteria"]["C1"])

    def guard(self, strings: dict[str, list[str]]) -> dict[str, int]:
        """The guard over a one-company arm doc whose printed strings are ``strings`` (labels, terms, an error)."""
        doc = {"companies": {"c1": {"draft": {"categories": {"passing_floor": [{"label": s} for s in
                                                                                strings.get("label", [])]},
                                              "predicates": [{"label": "ROOF FALL", "id": "roof_fall",
                                                              "first_terms": strings.get("term", [])}]},
                                    "pack": {"loader": {"error": None}}, "controls": {"majority_prior": {}}},
                             **({"c2": {"error": strings["error"][0]}} if "error" in strings else {})}}
        sent = REP.Sentinels(D.Refusal([r["ID"] for r in self.rows] + ["mine-12345"], ["Person 1", "Person 2"],
                                       PARAMS), [r["TEXT"] for r in self.rows])
        return REP.guard_hits(doc, {"c1": sent, "c2": sent}, 8, IDS["placeholder_prefix"])

    def test_the_guard_counts_each_kind(self) -> None:
        clean = self.guard({"label": ["HAULAGE"], "term": ["roof", "unlearned-x"]})
        self.assertEqual({k: clean[k] for k in REP.GUARD_KINDS}, dict.fromkeys(REP.GUARD_KINDS, 0))
        self.assertEqual(clean["unread"], 0)
        self.assertEqual(clean["strings"], 5)          # two labels, one id, two terms (a placeholder read as an id)
        long_text = next(r["TEXT"] for r in self.rows if len(re.findall(r"\w+", r["TEXT"])) >= 8)
        hits = self.guard({"label": [f"x {long_text} y", self.rows[0]["ID"], "Person 2"], "term": ["person"],
                           "error": ["at mine-12345 here"]})
        self.assertGreater(hits["narrative_ngrams"], 0)
        self.assertEqual(hits["refused_equal"], 2)     # the record id and the forbidden value, as labels
        self.assertEqual(hits["refused_name_word"], 1)  # 'person': a word of a forbidden value of two words
        self.assertEqual(hits["refused_inside"], 1)     # a site-like reference of 5+ characters in an error text
        self.assertEqual(self.guard({"label": [f"{self.rows[0]['ID']}9"]})["refused_equal"], 0)

    def test_a_planted_hit_withholds_the_report(self) -> None:
        # each kind, read by the guard from the arm's own exports (record id, site and forbidden columns, narratives)
        eight = " ".join(re.findall(r"\w+", next(r["TEXT"] for r in self.rows
                                                  if len(re.findall(r"\w+", r["TEXT"])) >= 8))[:8])
        cases = {"record id": (self.rows[0]["ID"], "refused_equal"), "site": ("s3", "refused_equal"),
                 "forbidden": ("Person 2", "refused_equal"), "name word": ("person", "refused_name_word"),
                 "narrative": (eight, "narrative_ngrams")}
        for case, (planted, kind) in cases.items():
            with self.subTest(case=case):
                arm_dir = self.tmp / f"arm_planted_{case.replace(' ', '_')}"
                shutil.copytree(self.base / "arms" / "msha", arm_dir)
                doc = json.loads((arm_dir / "arm.json").read_text())
                doc["companies"]["c1"]["draft"]["predicates"][0]["first_terms"].append(planted)
                (arm_dir / "arm.json").write_text(json.dumps(doc))
                out = REP.run_report(self.settings, "settings.json", "0" * 64, [arm_dir],
                                     self.base / "audit" / "audit.json", self.tmp / "report", lambda: "c" * 40,
                                     emit=lambda s: None)
                self.assertEqual(out["verdict"], "withheld")
                self.assertFalse(out["criteria"]["M3"]["passed"])
                self.assertGreaterEqual(out["guard_hits"]["msha"][kind], 1)
                text = (self.tmp / "report" / "report.json").read_text() + \
                    (self.tmp / "report" / "report.md").read_text()
                self.assertNotIn(planted, text)
                self.assertIn(REP.GUARD_SAYS[kind], text)
                self.assertNotIn("record text", text)

    def test_the_block_markers_and_their_sha256(self) -> None:
        _, printed = self.run_report()
        self.assertEqual(len(printed), 2)
        for name, block in zip(("report.json", "report.md"), printed):
            data = (self.tmp / "report" / name).read_bytes()
            first, *body, last = block.split("\n")
            self.assertEqual(first, f"=== D001 {name} BEGIN lines={len(data.decode().splitlines())} "
                                    f"sha256={hashlib.sha256(data).hexdigest()} ===")
            self.assertEqual(last, f"=== D001 {name} END ===")
            self.assertEqual("\n".join(body) + "\n", data.decode())

    def test_an_unread_export_withholds_the_report(self) -> None:
        arm_dir = self.tmp / "arm_copy"
        shutil.copytree(self.base / "arms" / "msha", arm_dir)
        doc = json.loads((arm_dir / "arm.json").read_text())
        doc["exports_dir"] = str(self.tmp / "gone")
        (arm_dir / "arm.json").write_text(json.dumps(doc))
        printed: list[str] = []
        out = REP.run_report(self.settings, "settings.json", "0" * 64, [arm_dir], self.base / "audit" / "audit.json",
                             self.tmp / "report", lambda: "c" * 40, emit=printed.append)
        self.assertEqual(out["verdict"], "withheld")
        self.assertEqual(out["exports_unread"], 1)
        self.assertFalse(out["criteria"]["M3"]["passed"])
        self.assertNotIn("arms", out)
        self.assertEqual(set(out["criteria"]), {"M3"})
        self.assertNotIn('"arms"', (self.tmp / "report" / "report.json").read_text())

    def test_the_code_hash_is_printed(self) -> None:
        doc, printed = self.run_report()
        self.assertRegex(doc["code_hash"], r"^[0-9a-f]{64}$")
        self.assertIn(doc["code_hash"], printed[1])
        self.assertTrue(any(f.startswith("tools/onboard/") for f in doc["files"]))

    def test_m4_fails_without_an_audit(self) -> None:
        printed: list[str] = []
        doc = REP.run_report(self.settings, "settings.json", "0" * 64, [self.base / "arms" / "msha"],
                             self.tmp / "missing.json", self.tmp / "report", lambda: "c" * 40, emit=printed.append)
        self.assertFalse(doc["criteria"]["M4"]["passed"])
        self.assertEqual(doc["verdict"], "fail")


# =================================================================================================== 13 CLI

class CliTests(TempDir):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_touches_nothing(self) -> None:
        before = sorted(self.tmp.rglob("*"))
        for argv in (["draft", "--export", "x.txt", "--roles", "r.json", "--pack-id", "p", "--train-from",
                      "2015-01-01", "--train-to", "2021-12-31", "--out", str(self.tmp / "o")],
                     ["export", "--export", "x", "--roles", "r", "--pack", "p", "--from", "2022-01-01", "--to",
                      "2024-12-31", "--out", str(self.tmp / "n.jsonl")],
                     ["check", "--pack", "p", "--export", "x", "--roles", "r", "--train-from", "2015-01-01",
                      "--train-to", "2021-12-31", "--out", str(self.tmp / "c.json")],
                     ["score", "--settings", str(SETTINGS_PATH), "--arm", "msha", "--exports", "e", "--out",
                      str(self.tmp / "a")],
                     ["report", "--settings", str(SETTINGS_PATH), "--arms", "a", "b", "--audit", "x", "--out",
                      str(self.tmp / "r")]):
            with self.subTest(command=argv[0]):
                code, out, _ = self.run_cli(*argv, "--dry-run")
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith(f"dry-run: onboard {argv[0]}"))
                self.assertIn("would write:", out)
        self.assertEqual(sorted(self.tmp.rglob("*")), before)

    def test_exit_codes(self) -> None:
        rows = corpus_rows(seed=41)
        (self.tmp / "e.txt").write_bytes(pipe(rows))
        (self.tmp / "r.json").write_text(json.dumps(roles().to_json()))
        base = ["--export", str(self.tmp / "e.txt"), "--roles", str(self.tmp / "r.json"), "--train-from",
                "2015-01-01", "--train-to", "2021-12-31"]
        code, out, _ = self.run_cli("draft", *base, "--pack-id", "cli_pack", "--out", str(self.tmp / "d"))
        self.assertEqual(code, 0, out)
        for r in rows:
            self.assertNotIn(r["TEXT"], out)
        code, out, _ = self.run_cli("check", "--pack", str(self.tmp / "d" / "pack"), *base, "--out",
                                    str(self.tmp / "c.json"))
        self.assertEqual(code, 0, out)
        vocab = json.loads((self.tmp / "d" / "pack" / "vocabulary.json").read_text())
        pid = next(p for p in vocab["predicates"] if p != "other_category")
        vocab["predicates"][pid]["label"] = "Person 1"
        (self.tmp / "d" / "pack" / "vocabulary.json").write_text(json.dumps(vocab))
        code, out, _ = self.run_cli("check", "--pack", str(self.tmp / "d" / "pack"), *base, "--out",
                                    str(self.tmp / "c2.json"))
        self.assertEqual(code, 1)
        self.assertNotIn("Person 1", out)
        code, _, err = self.run_cli("draft", *base, "--pack-id", "Bad Id", "--out", str(self.tmp / "d2"))
        self.assertEqual(code, 2)
        code, _, err = self.run_cli("draft", "--export", str(self.tmp / "missing.txt"), "--roles",
                                    str(self.tmp / "r.json"), "--pack-id", "x_pack", "--train-from", "2015-01-01",
                                    "--train-to", "2021-12-31", "--out", str(self.tmp / "d3"))
        self.assertEqual(code, 2)
        (self.tmp / "few.txt").write_bytes(pipe(Rows().add(20, "x", "A").rows))
        code, _, err = self.run_cli("draft", "--export", str(self.tmp / "few.txt"), "--roles", str(self.tmp / "r.json"),
                                    "--pack-id", "few_pack", "--train-from", "2015-01-01", "--train-to",
                                    "2021-12-31", "--out", str(self.tmp / "d4"))
        self.assertEqual(code, 1)
        (self.tmp / "bad_roles.json").write_text(json.dumps({"language": "en", "roles": {}}))
        code, _, _ = self.run_cli("draft", "--export", str(self.tmp / "e.txt"), "--roles",
                                  str(self.tmp / "bad_roles.json"), "--pack-id", "y_pack", "--train-from",
                                  "2015-01-01", "--train-to", "2021-12-31", "--out", str(self.tmp / "d5"))
        self.assertEqual(code, 2)

    def test_export_command(self) -> None:
        rows = corpus_rows(seed=42)
        (self.tmp / "e.txt").write_bytes(pipe(rows))
        (self.tmp / "r.json").write_text(json.dumps(roles().to_json()))
        base = ["--export", str(self.tmp / "e.txt"), "--roles", str(self.tmp / "r.json")]
        self.assertEqual(self.run_cli("draft", *base, "--pack-id", "exp_pack", "--train-from", "2015-01-01",
                                      "--train-to", "2021-12-31", "--out", str(self.tmp / "d"))[0], 0)
        code, out, _ = self.run_cli("export", *base, "--pack", str(self.tmp / "d" / "pack"), "--from", "2022-01-01",
                                    "--to", "2024-12-31", "--out", str(self.tmp / "n.jsonl"))
        self.assertEqual(code, 0, out)
        lines = (self.tmp / "n.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), sum(1 for r in rows if int(r["DATE"].rsplit("/", 1)[1]) >= 2022))


# =================================================================================================== 14 settings

class SettingsTests(unittest.TestCase):
    def test_the_choice_files_values(self) -> None:
        s = json.loads(SETTINGS_PATH.read_text())
        self.assertEqual(s["params"], PARAMS)
        p = s["params"]
        self.assertEqual((p["floor_records"], p["floor_sites"], p["predicate_min_records"], p["max_predicates"]),
                         (10, 3, 50, 199))
        self.assertEqual((p["term_min_df"], p["term_min_p"], p["max_terms"], p["token_min_letters"]), (10, 0.6, 50, 3))
        self.assertEqual((p["date_min_share"], p["ngram_tokens"], p["id_max_chars"], p["label_max_chars"]),
                         (0.95, 8, 36, 80))
        self.assertEqual((p["entity_id_max_chars"], p["entity_max_ids"]), (40, 500))
        self.assertEqual(p["inference"], {
            "narrative_min_mean_tokens": 8, "narrative_min_fill": 0.5, "date_min_share": 0.95,
            "record_id_min_fill": 0.99, "site_min_fill": 0.99, "site_min_distinct": 2,
            "site_fallback_max_mean_tokens": 2, "label_min_fill": 0.9, "label_min_letter_share": 0.9,
            "label_min_distinct": 2, "label_max_distinct": 200, "label_max_mean_tokens": 8})
        self.assertEqual(s["bootstrap"], {"B": 10000, "seed_prefix": "d001:boot", "alpha": 0.05})
        self.assertEqual((s["sample"]["seed_prefix"], s["permute"]["seed_prefix"]), ("d001:sample", "d001:permute"))
        self.assertEqual(s["timeout_minutes"], 180)
        # amendment A2: the refusal's lengths are rule 8's (forbidden 4 with a letter, references 5), now in params
        self.assertEqual(s["report_guard"], {"ngram": 8})
        self.assertEqual(p["refusal"], {"forbidden_inside_min_chars": 4, "reference_inside_min_chars": 5,
                                        "name_word_min_letters": 4})
        self.assertEqual(p["term_max_chars"], 64)                                         # amendment A4
        m, n = s["arms"]["msha"], s["arms"]["nhtsa"]
        self.assertEqual((m["train"], m["test"]), (["2015-01-01", "2021-12-31"], ["2022-01-01", "2024-12-31"]))
        self.assertEqual(m["companies"], {"column": "CONTROLLER_ID", "count": 5, "min_test_rows": 100,
                                          "min_training_sites": 3, "labels": ["c1", "c2", "c3", "c4", "c5"]})
        self.assertEqual((m["per_company"], m["criterion"], m["audit_company"]), (200, {"id": "C1", "margin": 0.1},
                                                                                   "c1"))
        self.assertEqual(len(m["columns"]), 57)
        self.assertEqual(m["roles"], {                                     # A12: the equipment columns left
            "record_id": "DOCUMENT_NO", "site": "MINE_ID", "date": "ACCIDENT_DT", "narrative": "NARRATIVE",
            "category": "CLASSIFICATION", "entities": [], "reporter": None,
            "forbidden": ["CONTROLLER_ID", "CONTROLLER_NAME", "OPERATOR_ID", "OPERATOR_NAME", "CONTRACTOR_ID",
                          "CLOSED_DOC_NO", "FIPS_STATE_CD"]})
        self.assertEqual(m["definition_columns"], ["DOCUMENT_NO", "MINE_ID", "ACCIDENT_DT", "NARRATIVE",
                                                   "CLASSIFICATION", "ACCIDENT_TYPE", "CONTROLLER_ID",
                                                   "CONTROLLER_NAME", "OPERATOR_ID", "OPERATOR_NAME", "CONTRACTOR_ID",
                                                   "CLOSED_DOC_NO", "FIPS_STATE_CD"])
        self.assertEqual((n["train"], n["test"]), (["2020-01-01", "2022-12-31"], ["2023-01-01", "2024-12-31"]))
        self.assertEqual(n["companies"]["makes"], ["FORD", "CHEVROLET", "JEEP", "HONDA", "NISSAN", "DODGE"])
        self.assertEqual(n["columns"], ["odino", "state", "received", "components[]", "vehicle", "summary",
                                        "reporter"])
        self.assertEqual(n["roles"], {"record_id": "odino", "site": "state", "date": "received",
                                      "narrative": "summary", "category": "components[]", "entities": [],
                                      "reporter": None, "forbidden": ["reporter", "vehicle"]})       # A6
        self.assertEqual(m["download_code"], ["tools/onboard/fetch_msha.py"])                            # A8
        self.assertEqual(n["download_code"], ["tools/onboard/fetch_nhtsa.py", "tools/market/nhtsa_export.py",
                                              "tools/market/vehicle_pack.py"])
        self.assertEqual(n["hand_packs"], {"pack": "docs/collective/replay/vehicles/pack",
                                           "pack-v2": "docs/collective/replay/vehicles/pack-v2"})
        self.assertEqual((n["per_company"], n["criterion"]), (200, {"id": "C2", "margin": 0.05, "against": "pack"}))
        for path in n["hand_packs"].values():
            load_pack_dir(ROOT / path)

    def test_the_msha_columns_are_the_probe_records(self) -> None:
        s = json.loads(SETTINGS_PATH.read_text())
        sources = (ROOT / "docs" / "collective" / "onboard" / "SOURCES.md").read_text()
        block = sources.split("**The 57 fields of `Accidents.txt`, in order:**")[1].split("\n\n")[0]
        self.assertEqual(re.findall(r"`([A-Z_]+)`", block), s["arms"]["msha"]["columns"])

    def test_the_choice_file_names_the_values(self) -> None:
        text = CHOICE.read_text()
        for needle in ("N = 10", "S = 3", "R = 50", "199", "K = 50", "0.6", "B = 10,000", "d001:boot",
                       "d001:sample", "d001:permute", "180 minutes", "0.10", "-0.05", "2015-01-01 to 2021-12-31",
                       "2022-01-01 to 2024-12-31", "2020-01-01 to 2022-12-31", "2023-01-01 to 2024-12-31",
                       "200 records per company", "8 consecutive"):
            self.assertIn(needle, text)


# =================================================================================================== 15 workflow

def _yaml() -> Any:
    try:
        import yaml
    except ImportError:          # the run's own job has no PyYAML; the CI suites do
        return None
    return yaml


class WorkflowTests(unittest.TestCase):
    text = WORKFLOW.read_text(encoding="utf-8")

    def test_textual_contract(self) -> None:
        t = self.text
        self.assertIn('paths: ["docs/collective/onboard/run-*.json"]', t)
        self.assertIn("branches-ignore: [main]", t)
        self.assertIn("ls docs/collective/onboard/run-*.json | sort | tail -n 1", t)
        self.assertIn("timeout-minutes: 180", t)
        self.assertIn("settings_sha256", t)
        self.assertLess(t.index("fetch_msha.py download"), t.index("=== D001 RUN-START ==="))
        self.assertLess(t.index("fetch_nhtsa.py download"), t.index("=== D001 RUN-START ==="))
        self.assertLess(t.index("=== D001 RUN-START ==="), t.index("fetch_msha.py split"))
        self.assertIn("persist-credentials: false", t)
        self.assertIn("contents: read", t)

    def test_pinned_actions_equal_the_vehicle_replays(self) -> None:
        pins = re.compile(r"uses: (\S+@[0-9a-f]{40})")
        mine = set(pins.findall(self.text))
        theirs = set(pins.findall((ROOT / ".github" / "workflows" / "vehicle-replay.yml").read_text()))
        self.assertEqual(mine, theirs)
        self.assertEqual(len(re.findall(r"uses: ", self.text)), len(pins.findall(self.text)))

    @unittest.skipUnless(_yaml(), "PyYAML is not installed")
    def test_structure(self) -> None:
        doc = _yaml().safe_load(self.text)
        on = doc.get("on", doc.get(True))
        self.assertEqual(on["push"], {"branches-ignore": ["main"], "paths": ["docs/collective/onboard/run-*.json"]})
        self.assertIn("workflow_dispatch", on)
        self.assertEqual(doc["permissions"], {"contents": "read"})
        (job,) = doc["jobs"].values()
        self.assertEqual((job["runs-on"], job["timeout-minutes"]), ("ubuntu-latest", 180))
        steps = job["steps"]
        names = [s.get("name", s.get("uses")) for s in steps]
        start = next(i for i, s in enumerate(steps) if s.get("id") == "start")
        self.assertIn("=== D001 RUN-START ===", steps[start]["run"])
        download = next(i for i, n in enumerate(names) if n and n.startswith("download"))
        self.assertLess(download, start)
        for s in steps[start + 1:]:
            self.assertIn("steps.start.outcome == 'success'", s.get("if", ""))
        tests = steps[next(i for i, n in enumerate(names) if n == "offline tests")]["run"]
        for module in ("tests.mycelic.test_collective_onboard", "tests.onboard.test_onboard_fetch",
                       "tests.mycelic.test_collective_guards.OnboardGenericTests"):
            self.assertIn(module, tests)
        upload = steps[-1]
        self.assertTrue(upload["uses"].startswith("actions/upload-artifact@"))
        self.assertEqual(upload["with"]["path"], "d001/upload/")
        collect = next(s for s in steps if s.get("name") == "collect the files to upload")["run"]
        for never in ("raw", "split", "audit-in", "audit.json", "records.jsonl"):
            self.assertNotIn(never, collect)
        self.assertIn('json.loads(check.read_text())["passed"]', collect)


# ====================================================================== 16 the amendment before any run (A1 to A9)

NHTSA_ROLES = json.loads(SETTINGS_PATH.read_text())["arms"]["nhtsa"]["roles"]
NHTSA_COLUMNS = ("odino", "state", "received", "components[]", "vehicle", "summary", "reporter")
# real two-letter state codes, lower-cased as NHTSA's sites are: Idaho's "id" equals the template's key "id"
STATES = ("id", "in", "or", "me", "oh", "ok", "hi", "pa", "tx", "ca")


def nhtsa_rows(seed: int = 51, per_cat: int = 120, vehicle: str = "FORD-EXPLORER-2019") -> list[dict[str, str]]:
    """An NHTSA-shaped synthetic export: real state codes as sites, a vehicle on every row, and the model's word in
    the ENGINE narratives (so a reader without the vehicle refused would learn it)."""
    rng = random.Random(seed)
    cues = {"ENGINE": ["stalled", "idle", "explorer"], "SERVICE BRAKES": ["brake", "pedal", "stopping"],
            "STEERING": ["steering", "wheel", "pull"], "UNKNOWN OR OTHER": ["noise", "strange", "rattle"]}
    filler = ["vehicle", "driving", "highway", "contacted", "manufacturer", "repair", "warning", "light"]
    rows = []
    for cat, words in cues.items():
        for _ in range(per_cat):
            year = rng.randint(2020, 2024)
            rows.append({"state": rng.choice(STATES), "received": f"{year}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}",
                         "components[]": cat, "vehicle": vehicle,
                         "summary": " ".join(rng.sample(words, 2) + rng.sample(filler, 5)).capitalize() + "."})
    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r["odino"] = r["reporter"] = str(11400000 + i)
    return rows


def nhtsa_export(rows: list[dict[str, str]]) -> Export:
    return parse_export(pipe(rows, NHTSA_COLUMNS))


NHTSA_TRAIN = D.parse_window("2020-01-01", "2022-12-31")


class RefusalTests(unittest.TestCase):
    """Amendment A2: the one refusal, its three rules and their thresholds on both sides."""

    def test_equal_at_any_length_and_case_folded(self) -> None:
        r = D.Refusal(["id", "R00001"], ["OTHER", "?"], PARAMS)
        for s in ("ID", "id", "r00001", "Other", "?"):
            self.assertEqual(r.string(s), "equal", s)
        for s in ("idaho", "others", "r000011", ""):
            self.assertIsNone(r.string(s), s)

    def test_inside_thresholds(self) -> None:
        r = D.Refusal(["mine1", "mine12", "s1"], ["Roof", "Gas", "1234", "AB-9"], PARAMS)
        self.assertEqual(r.string("FALL OF ROOF OR BACK"), "inside")       # forbidden of 4 characters with a letter
        self.assertIsNone(r.string("gas leak"))                            # forbidden of 3 characters: not inside
        self.assertEqual(r.string("gas"), "equal")                         # ...but equal at any length
        self.assertIsNone(r.string("code 1234 here"))                      # forbidden of 4 with no letter
        self.assertEqual(r.string("type ab-9 here"), "inside")             # 4 characters, one a letter
        self.assertEqual(r.string("at mine1 today"), "inside")             # a reference of 5 characters
        self.assertIsNone(r.string("at s1 today"))                         # a reference of 2: equal only
        self.assertIsNone(r.string("roofing"))                             # whole words only

    def test_name_words(self) -> None:
        r = D.Refusal([], ["Halvorsen Quarry Holdings", "Joe Smith", "Kowalczyk", "FORD-EXPLORER-2019"], PARAMS)
        self.assertEqual(r.string("halvorsen"), "name_word")
        self.assertEqual(r.string("Explorer"), "name_word")                # a model inside a vehicle
        self.assertEqual(r.string("smith"), "name_word")
        self.assertIsNone(r.string("joe"))                                 # 3 letters: not a name word
        self.assertIsNone(r.string("2019"))                                # digits: not a name word
        self.assertEqual(r.string("kowalczyk"), "equal")                   # a one-word value: equal only
        self.assertIsNone(r.string("halvorsen haul"))                      # a string equals a word; a term below
        self.assertEqual(r.term("halvorsen haul"), "name_word")            # a term: any of its words
        self.assertEqual(r.term("told joe"), None)
        self.assertEqual(D.Refusal([], ["Joe"], PARAMS).term("told joe"), "equal")

    def test_the_drafter_and_the_check_build_the_same_refusal(self) -> None:
        rows = corpus_rows(seed=61)
        rows[-1]["PERSON"] = "Late Value"
        export = parse_export(pipe(rows))
        d = drafted(rows)
        r = D.export_refusal(export, roles(), PARAMS)
        self.assertEqual((d.refusal.equal, d.refusal.name_words, d.refusal.inside.by_run),
                         (r.equal, r.name_words, r.inside.by_run))
        self.assertIn("late value", r.equal)


class AmendedDraftTests(TempDir):
    """Amendments A2, A4 and A6 in the drafter, each with the check passing on what it drafts."""

    def check(self, export: Export, d: D.Draft, rl: Any, window: D.Window = TRAIN) -> dict[str, Any]:
        out = self.tmp / f"p{len(list(self.tmp.iterdir()))}"
        D.write_pack(d.files, out)
        return C.check_pack(out, export, rl, window, params=PARAMS, lang=LANG, template=TEMPLATE)

    def test_real_state_codes_as_sites_pass_the_floor(self) -> None:
        rows = nhtsa_rows()
        export = nhtsa_export(rows)
        rl = roles_from_json({"language": "en", "roles": NHTSA_ROLES})
        d = D.draft_export(export, rl, NHTSA_TRAIN, "nhtsa_ford", params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertIn("id", {r["state"] for r in rows if r["received"][:4] <= "2022"})
        result = self.check(export, d, rl, NHTSA_TRAIN)
        self.assertTrue(result["passed"], result)
        p = self.tmp / "p0"
        self.assertIn("id", {s for _, s in C.pack_strings(p)})                 # the template's key "id" ...
        self.assertEqual(d.refusal.string("id"), "equal")                       # ... equals Idaho's site value

    def test_the_vehicle_column_refuses_model_words(self) -> None:
        rows = nhtsa_rows()
        export = nhtsa_export(rows)
        declared = roles_from_json({"language": "en", "roles": NHTSA_ROLES})
        d = D.draft_export(export, declared, NHTSA_TRAIN, "nhtsa_v", params=PARAMS, lang=LANG, template=TEMPLATE)
        terms = {t for ts in d.learned.values() for t in ts}
        self.assertNotIn("explorer", terms)
        self.assertNotIn("ford", terms)
        self.assertGreater(d.refused_terms, 0)
        self.assertTrue(self.check(export, d, declared, NHTSA_TRAIN)["passed"])
        unrefused = roles_from_json({"language": "en", "roles": dict(NHTSA_ROLES, forbidden=["reporter"])})
        d2 = D.draft_export(export, unrefused, NHTSA_TRAIN, "nhtsa_v2", params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertIn("explorer", {t for ts in d2.learned.values() for t in ts})  # what A6 stops

    def test_a_forbidden_value_equal_to_a_template_word_or_key_passes(self) -> None:
        rows = corpus_rows(seed=62)
        for i, word in enumerate(("case", "id", "label", "Other", "never", "codes")):
            rows[i]["PERSON"] = word
        export = parse_export(pipe(rows))
        d = D.draft_export(export, roles(), TRAIN, "tpl_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        result = self.check(export, d, roles())
        self.assertTrue(result["passed"], result)

    def test_a_forbidden_value_only_in_test_rows_refuses_the_term_in_drafter_and_check_alike(self) -> None:
        rows = corpus_rows(seed=63)
        base = drafted(rows)
        self.assertIn("conveyor", base.learned["haulage"])
        late = next(i for i, r in enumerate(rows) if int(r["DATE"].rsplit("/", 1)[1]) >= 2022)
        rows[late]["PERSON"] = "Conveyor"
        export = parse_export(pipe(rows))
        d = D.draft_export(export, roles(), TRAIN, "late_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertFalse(any("conveyor" in t.split() for ts in d.learned.values() for t in ts))
        self.assertGreater(d.facts["terms"]["refused"], base.facts["terms"]["refused"])   # counted, not silent
        result = self.check(export, d, roles())
        self.assertTrue(result["passed"], result)

    def test_a_surname_inside_a_name_is_never_learned(self) -> None:
        rows = corpus_rows(seed=64, per_cat=100)
        n = 0
        for r in rows:
            if r["CAT"] == "HAULAGE" and int(r["DATE"].rsplit("/", 1)[1]) <= 2021 and n < 40:
                r["TEXT"] += " Supervisor Kowalczyk was told."
                n += 1
        learned = drafted(rows)
        self.assertIn("kowalczyk", {t for ts in learned.learned.values() for t in ts})   # without the name: learned
        rows[0]["PERSON"] = "Anna Kowalczyk"
        export = parse_export(pipe(rows))
        d = D.draft_export(export, roles(), TRAIN, "name_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        terms = {t for ts in d.learned.values() for t in ts}
        self.assertFalse(any("kowalczyk" in t.split() for t in terms))
        self.assertTrue(self.check(export, d, roles())["passed"])

    def test_a_category_equal_to_a_forbidden_marker_is_left_out_not_failed(self) -> None:
        rows = corpus_rows(seed=65)
        for r in rows:
            if r["CAT"] == "OTHER":
                r["CAT"] = "?"
        rows[3]["PERSON"] = "?"
        export = parse_export(pipe(rows))
        d = D.draft_export(export, roles(), TRAIN, "marker_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertNotIn("?", d.plan.value_map)
        self.assertEqual(d.facts["categories"]["refused"], 1)
        self.assertNotIn("?", [x["label"] for x in d.facts["categories"]["passing_floor"]])
        self.assertTrue(self.check(export, d, roles())["passed"])

    def test_a_predicate_whose_label_is_a_name_word_is_left_out_and_the_plan_redone(self) -> None:
        rows = corpus_rows(seed=66)
        for r in rows:
            if r["CAT"] == "SLIP OR FALL":
                r["CAT"] = "MACHINERY"
        rows[5]["PERSON"] = "Jeffrey Mining Machinery"
        export = parse_export(pipe(rows))
        d = D.draft_export(export, roles(), TRAIN, "word_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertNotIn("machinery", d.plan.ids)
        self.assertEqual(set(d.plan.ids), {"roof_fall", "haulage"})
        self.assertEqual([c.key for c in d.plan.refused], ["machinery"])
        self.assertEqual(d.facts["categories"]["refused"], 1)
        self.assertTrue(self.check(export, d, roles())["passed"])

    def test_a_term_longer_than_the_loader_allows_is_no_candidate(self) -> None:
        long_a, long_b = "a" * 33, "b" * 31            # the bigram is 65 characters, each word fits
        self.assertEqual(D.candidate_terms(f"{long_a} {long_b}", LANG, PARAMS), {long_a, long_b})
        self.assertEqual(D.candidate_terms(f"{long_a} {'b' * 30}", LANG, PARAMS),
                         {long_a, "b" * 30, f"{long_a} {'b' * 30}"})           # 64: still a candidate
        self.assertEqual(D.candidate_terms("x" * 65, LANG, PARAMS), set())
        rows = corpus_rows(seed=67)
        for r in rows:
            if r["CAT"] == "HAULAGE":
                r["TEXT"] += f" {long_a} {long_b}."
        d = drafted(rows)
        D.write_pack(d.files, self.tmp / "long")
        self.assertIsNone(D.load_written(self.tmp / "long")[1])
        self.assertIn(long_a, d.learned["haulage"])
        with self.assertRaises(Exception):          # the loader's own limit, which A4 keeps the drafter under
            files = copy.deepcopy(d.files)
            files["vocabulary.json"]["predicates"]["haulage"]["lexicon"]["en"].append(f"{long_a} {long_b}")
            D.write_pack(files, self.tmp / "too_long")
            load_pack_dir(self.tmp / "too_long")

    def test_a_refused_entity_value_is_left_out(self) -> None:
        rows = corpus_rows(seed=68)
        for i, r in enumerate(rows):
            r["UNIT"] = "Unit A" if i % 2 else "Person 2"
        export = parse_export(pipe(rows, HEADER + ("UNIT",)))
        rl = roles(entities=["UNIT"])
        d = D.draft_export(export, rl, TRAIN, "ent_pack", params=PARAMS, lang=LANG, template=TEMPLATE)
        (et,) = d.etypes
        self.assertEqual(et.ids, ("V-UNIT-A",))
        self.assertEqual(d.facts["refused_entity_values"], 1)
        self.assertTrue(self.check(export, d, rl)["passed"])


class AmendedCheckTests(TempDir):
    """Amendment A3: the planted violations each check must catch, and the template rebuild."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = corpus_rows(seed=12)
        cls.rows += Rows().add(12, "duoword here", "HAULAGE", sites=SITES[:2], day=date(2016, 1, 1)).rows
        for i, r in enumerate(cls.rows):
            r["ID"] = f"A{i + 1:05d}"
        cls.export = parse_export(pipe(cls.rows))
        cls.d = D.draft_export(cls.export, roles(), TRAIN, "check_pack", params=PARAMS, lang=LANG, template=TEMPLATE)

    def fresh(self) -> Path:
        p = self.tmp / f"p{len(list(self.tmp.iterdir()))}"
        D.write_pack(self.d.files, p)
        return p

    def check(self, p: Path) -> dict[str, Any]:
        return C.check_pack(p, self.export, roles(), TRAIN, params=PARAMS, lang=LANG, template=TEMPLATE)["checks"]

    def plant(self, name: str, fn: Any) -> dict[str, Any]:
        p = self.fresh()
        if name.endswith(".jsonl"):
            lines = [json.loads(x) for x in (p / name).read_text().splitlines()]
            fn(lines)
            (p / name).write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))
        else:
            obj = json.loads((p / name).read_text())
            fn(obj)
            (p / name).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
        return self.check(p)

    def label(self, value: str) -> Any:
        pid = self.d.plan.ids[0]
        return lambda v: v["predicates"][pid].update(label=value)

    def term(self, value: str) -> Any:
        pid = self.d.plan.ids[0]
        return lambda v: v["predicates"][pid]["lexicon"]["en"].append(value)

    def test_a_clean_pack_rebuilds_from_the_template(self) -> None:
        checks = self.check(self.fresh())
        self.assertTrue(all(c["passed"] for c in checks.values()), checks)
        self.assertEqual(checks["template"], {"passed": True, "files": [], "error": None})
        self.assertGreater(checks["refused_strings"]["strings"], 0)

    def test_a_record_id_or_a_site_value_written_as_a_label_fails(self) -> None:
        for value in (self.rows[0]["ID"], "s1"):
            with self.subTest(value=value):
                c = self.plant("vocabulary.json", self.label(value))["refused_strings"]
                self.assertFalse(c["passed"])
                self.assertEqual((c["kinds"]["equal"], c["files"]), (1, ["vocabulary.json"]))

    def test_a_value_map_key_equal_to_a_forbidden_value_fails(self) -> None:
        c = self.plant("mapping.json", lambda m: m["codes"][0]["value_map"].update({"Person 1": "D-999"}))
        self.assertFalse(c["refused_strings"]["passed"])
        self.assertEqual(c["refused_strings"]["files"], ["mapping.json"])

    def test_a_term_holding_a_site_value_fails(self) -> None:
        c = self.plant("vocabulary.json", self.term("roof s1"))["refused_terms"]
        self.assertFalse(c["passed"])
        self.assertEqual(c["kinds"]["equal"], 1)

    def test_a_term_at_two_sites_fails_the_term_floor(self) -> None:
        self.assertNotIn("duoword", {t for ts in self.d.learned.values() for t in ts})
        c = self.plant("vocabulary.json", self.term("duoword"))["term_floor"]
        self.assertEqual((c["passed"], c["failures"]), (False, 1))

    def test_a_placeholder_lookalike_is_not_exempt_from_the_term_floor(self) -> None:
        c = self.plant("vocabulary.json", self.term("unrelatedword"))["term_floor"]
        self.assertEqual((c["passed"], c["failures"]), (False, 1))

    def test_eight_tokens_of_a_narrative_fail_and_seven_do_not(self) -> None:
        tokens = re.findall(r"\w+", self.rows[0]["TEXT"])
        self.assertGreaterEqual(len(tokens), 8)
        for n, passed in ((8, False), (7, True)):
            with self.subTest(tokens=n):
                def plant(lines: list, n: int = n) -> None:
                    lines[0]["record"]["narrative"] = " ".join(tokens[:n])
                self.assertEqual(self.plant("fixtures/records.jsonl", plant)["ngram"]["passed"], passed)

    def test_a_value_at_a_template_position_fails_the_template_check(self) -> None:
        cases = {"generator.json": lambda g: g["filler"]["en"].append("Person 1 was there."),
                 "egress.json": lambda e: e.update({"Person 2": 1}),
                 "pack.json": lambda p: p.update(title="Person 2")}
        for name, fn in cases.items():
            with self.subTest(file=name):
                c = self.plant(name, fn)
                self.assertTrue(c["refused_strings"]["passed"])           # not a record-derived position ...
                self.assertFalse(c["template"]["passed"])                  # ... and still caught
                self.assertIn(name, c["template"]["files"])


# ---------------------------------------------------------------------------------------- the last guard (A5)

def two_arm_settings() -> dict[str, Any]:
    """Both arms on the synthetic layout, each with C1: the guard's per-arm reading without a hand pack."""
    s = arm_settings()
    s["arms"]["nhtsa"] = copy.deepcopy(s["arms"]["msha"])
    return s


class GuardTests(TempDir):
    def run_arms(self, arms: dict[str, dict[str, list[dict[str, str]]]],
                 settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
        settings = settings or two_arm_settings()
        for name in list(settings["arms"]):
            if name not in arms:
                del settings["arms"][name]
        dirs = []
        for name, companies in arms.items():
            exports = write_arm(self.tmp / "in" / name, companies)
            SC.run_arm(settings, "0" * 64, name, exports, self.tmp / "arms" / name, lambda: "c" * 40)
            dirs.append(self.tmp / "arms" / name)
        fake_audit(self.tmp / "audit.json")
        doc = REP.run_report(settings, "s.json", "0" * 64, dirs, self.tmp / "audit.json", self.tmp / "report",
                             lambda: "c" * 40, emit=lambda s: None)
        text = (self.tmp / "report" / "report.json").read_text() + (self.tmp / "report" / "report.md").read_text()
        return doc, text

    def test_a_forbidden_cell_equal_to_a_report_word_does_not_withhold(self) -> None:
        rows = corpus_rows(seed=71, per_cat=90)
        for i, word in enumerate(("Other", "Records", "label", "Share", "Code", "Verdict")):
            rows[i]["PERSON"] = word
        doc, text = self.run_arms({"msha": {"c1": rows}})
        self.assertNotEqual(doc["verdict"], "withheld")
        self.assertTrue(doc["criteria"]["M3"]["passed"], doc["criteria"]["M3"])
        self.assertIn("Other share", text)

    def test_the_other_arms_make_and_labels_do_not_withhold(self) -> None:
        msha = corpus_rows(seed=72, per_cat=90)
        for i, word in enumerate(("FORD", "UNKNOWN", "Unknown or other")):
            msha[i]["PERSON"] = word
        nhtsa = corpus_rows(seed=73, per_cat=90)
        for r in nhtsa:
            r["ID"] = "N" + r["ID"]
            if r["CAT"] == "OTHER":
                r["CAT"] = "UNKNOWN OR OTHER"
        doc, text = self.run_arms({"msha": {"c1": msha}, "nhtsa": {"FORD": nhtsa}})
        self.assertNotEqual(doc["verdict"], "withheld")
        self.assertTrue(doc["criteria"]["M3"]["passed"], doc["criteria"]["M3"])
        self.assertIn("FORD", text)
        self.assertIn("UNKNOWN OR OTHER", text)

    def test_a_failed_floor_company_prints_no_label_id_or_term(self) -> None:
        rows = corpus_rows(seed=74, per_cat=90)
        real = SC.check_pack

        def failing(*a: Any, **kw: Any) -> dict[str, Any]:
            out = real(*a, **kw)
            out["passed"] = False
            return out
        SC.check_pack = failing
        try:
            doc, text = self.run_arms({"msha": {"c1": rows}})
        finally:
            SC.check_pack = real
        arm = (self.tmp / "arms" / "msha" / "arm.json").read_text()
        c1 = json.loads(arm)["companies"]["c1"]
        self.assertTrue(c1["strings_withheld"])
        for s in ("ROOF FALL", "roof_fall", "HAULAGE", "haulage", "shuttle", "conveyor", "slipped"):
            self.assertNotIn(s, arm)
            self.assertNotIn(s, text)
        self.assertIn("Labels and terms withheld: this pack failed the privacy floor.", text)
        self.assertFalse(doc["criteria"]["M3"]["passed"])

    def test_the_guard_reads_parsed_strings_and_folds(self) -> None:
        sent8 = "the operator backed the loader into the berm"
        sent = REP.Sentinels(D.Refusal([], ["Anna Kowalczyk", "OTHER WORDS", "Joe"], PARAMS),
                             [f"x {sent8.capitalize()} y"])
        doc = {"companies": {"c1": {"draft": {"categories": {"passing_floor": [
            {"label": "the operator backed\tthe loader into the berm"}, {"label": "told joe"}]}, "predicates": [
            {"label": "OTHER WORDS", "id": "p", "first_terms": ["kowalczyk", "told joe"]}]},
            "pack": {"loader": {"error": None}}, "controls": {"majority_prior": {}}}}}
        hits = REP.guard_hits(doc, {"c1": sent}, 8, IDS["placeholder_prefix"])
        self.assertEqual(hits["narrative_ngrams"], 1)        # a tab inside a label: still the narrative's 8 words
        self.assertEqual(hits["refused_equal"], 2)           # 'OTHER WORDS' folded; the term 'told joe' by its word
        self.assertEqual(hits["refused_name_word"], 1)       # a surname inside a name
        self.assertEqual(hits["strings"], 6)                 # the label 'told joe' is read by the string rule


class ExactPrintingTests(unittest.TestCase):
    """Amendment A8: a criterion's deciding values are printed unrounded beside each comparison."""

    def test_c1_prints_the_exact_difference_and_each_comparison(self) -> None:
        pooled = {"readers": {r: {"micro": {"f1": 0.5}} for r in SC.READERS},
                  "differences": {r: {"diff": 0.09996, "exact_diff": Fraction(2499, 25000), "ci_low": -0.05049,
                                      "ci_high": 0.2} for r in SC.CONTROLS}}
        c1 = SC.criterion({"id": "C1", "margin": 0.1}, pooled, None, [], 5)
        self.assertFalse(c1["passed"])
        self.assertEqual(c1["exact"]["diff_fraction"], "2499/25000")
        self.assertEqual(c1["exact"]["comparisons"], {"diff >= 0.1": False, "ci_low > 0": False})
        kept = SC.rounded({"criterion": c1})["criterion"]
        self.assertEqual(kept["exact"]["ci_low"], -0.05049)
        self.assertEqual(kept["diff"], round(0.09996, 4))                 # the rounded copy beside it
        detail = REP._criterion_detail(kept)
        for needle in ("diff 0.09996", "diff_fraction 2499/25000", "ci_low -0.05049", "diff >= 0.1: no",
                       "ci_low > 0: no"):
            self.assertIn(needle, detail)
        self.assertNotIn("0.100", detail)

    def test_c2_prints_its_comparison_and_the_matched_names(self) -> None:
        matched = {"hand": {"pack": {"difference": {"diff": -0.01, "ci_low": -0.0499999, "ci_high": 0.02, "n": 9}}}}
        c2 = SC.criterion({"id": "C2", "margin": 0.05, "against": "pack"}, {}, matched, [], 6)
        c2["matched_names"] = {"FORD": 21}
        detail = REP._criterion_detail(SC.rounded(c2))
        self.assertIn("ci_low -0.0499999", detail)
        self.assertIn("ci_low > -0.05: yes", detail)
        self.assertIn("matched names per make FORD 21", detail)


class MatchedGoldTests(TempDir):
    """Amendment A7: the matched gold counts a filed name outside C that the hand pack maps to a reached predicate;
    a non-specific hand code never enters the matched space."""

    def test_matched_gold_and_specificity(self) -> None:
        rng = random.Random(81)
        names = {"ENGINE": ["piston", "cylinder"], "BRAKES": ["pedal", "rotor"], "ENGINE PARTS": ["gasket"],
                 "STEERING": ["wheel", "pull"]}
        rows = []
        for name, cues in names.items():
            for _ in range(70 if name != "ENGINE PARTS" else 20):
                rows.append({"record_ref": "", "site": rng.choice(SITES), "received_date": "2019-03-04",
                             "codes[]": name, "narrative": " ".join(rng.sample(cues, 1) + rng.sample(FILLER, 3)),
                             "scope": "ALL"})
        for i, r in enumerate(rows):
            r["record_ref"] = f"H{i:04d}"
        header = ("record_ref", "site", "received_date", "codes[]", "narrative", "scope")
        rl = roles(record_id="record_ref", site="site", date="received_date", narrative="narrative",
                   category="codes[]", forbidden=[])
        d = D.draft_export(parse_export(pipe(rows, header)), rl, TRAIN, "mg_pack", params=PARAMS, lang=LANG,
                           template=TEMPLATE)
        self.assertNotIn("engine_parts", d.plan.ids)                          # 20 rows: the other bucket
        D.write_pack(d.files, self.tmp / "hand")
        mapping = json.loads((self.tmp / "hand" / "mapping.json").read_text())
        vm = mapping["codes"][0]["value_map"]
        vm["ENGINE PARTS"] = vm["ENGINE"]                                     # the hand pack merges it in
        (self.tmp / "hand" / "mapping.json").write_text(json.dumps(mapping))
        codes = json.loads((self.tmp / "hand" / "codes.json").read_text())
        codes[vm["STEERING"]]["specific"] = False                             # a non-specific hand code
        (self.tmp / "hand" / "codes.json").write_text(json.dumps(codes))
        hand = load_pack_dir(self.tmp / "hand")
        space = SC.matched_space(d, hand)
        self.assertEqual(space, {"BRAKES": "brakes", "ENGINE": "engine"})
        sample = [SC.Sampled("c1", 0, "x1", "t", frozenset(), ("ENGINE PARTS",)),
                  SC.Sampled("c1", 1, "x2", "t", frozenset(), ("BRAKES", "STEERING")),
                  SC.Sampled("c1", 2, "x3", "t", frozenset(), ("STEERING",))]
        self.assertEqual(SC.matched_gold(sample, hand, set(space.values())),
                         [frozenset({"engine"}), frozenset({"brakes"}), frozenset()])

    def test_the_arm_scores_the_matched_space_with_the_amended_gold(self) -> None:
        rng = random.Random(82)
        names = {"ENGINE": ["piston", "cylinder"], "BRAKES": ["pedal", "rotor"], "ENGINE PARTS": ["gasket"]}
        rows = []
        for name, cues in names.items():
            for _ in range(70 if name != "ENGINE PARTS" else 20):
                rows.append({"site": rng.choice(SITES), "received_date": "2019-03-04", "codes[]": name,
                             "narrative": " ".join(rng.sample(cues, 1) + rng.sample(FILLER, 3))})
        for i in range(30):              # test window: filed under a name in C and a merged name outside C
            rows.append({"site": SITES[i % 6], "received_date": "2023-03-04", "codes[]": "BRAKES;ENGINE PARTS",
                         "narrative": f"pedal piston {FILLER[i % 10]} test{chr(97 + i % 26)}{chr(97 + i // 26)}"})
        for i, r in enumerate(rows):
            r["record_ref"], r["scope"] = f"M{i:04d}", "ALL"
        header = ("record_ref", "site", "received_date", "codes[]", "narrative", "scope")
        rl = roles(record_id="record_ref", site="site", date="received_date", narrative="narrative",
                   category="codes[]", forbidden=[])
        d = D.draft_export(parse_export(pipe(rows, header)), rl, TRAIN, "mga_pack", params=PARAMS, lang=LANG,
                           template=TEMPLATE)
        D.write_pack(d.files, self.tmp / "hand")
        mapping = json.loads((self.tmp / "hand" / "mapping.json").read_text())
        vm = mapping["codes"][0]["value_map"]
        vm["ENGINE PARTS"] = vm["ENGINE"]
        (self.tmp / "hand" / "mapping.json").write_text(json.dumps(mapping))
        exports = write_arm(self.tmp / "arm", {"c1": rows}, header)
        s = arm_settings(hand={"hand": str(self.tmp / "hand")}, criterion={"id": "C2", "margin": 0.05,
                                                                           "against": "hand"})
        s["arms"]["msha"]["roles"] = rl.to_json()["roles"]
        doc = SC.run_arm(s, "0" * 64, "msha", exports, self.tmp / "out", lambda: "c" * 40)
        m = doc["companies"]["c1"]["hand"]["hand"]["matched"]
        # both readers name engine for 'piston'; the record's merged name maps there, so it is no false positive
        self.assertEqual((m["drafted"]["micro"]["tp"], m["drafted"]["micro"]["fp"]), (60, 0))
        self.assertEqual((m["hand"]["micro"]["tp"], m["hand"]["micro"]["fp"]), (60, 0))

    def test_pack_maps_one_name_to_each_specific_code(self) -> None:
        # so the amended gold equals the names-in-C gold for pack/, and C2 is unchanged
        hand = load_pack_dir(ROOT / "docs" / "collective" / "replay" / "vehicles" / "pack")
        vm = hand.mapping()["codes"][0]["value_map"]
        specific = [code for code in vm.values() if hand.codes[code].specific]
        self.assertEqual(len(specific), len(set(specific)))


def wiring_rows(seed: int = 91) -> list[dict[str, str]]:
    """One large category and two small ones, so that even permuted labels leave the large one terms."""
    rng = random.Random(seed)
    sizes = {"BIG": 400, "SMALL ONE": 70, "SMALL TWO": 70, "OTHER": 30}
    cues = {"BIG": ["alpha", "beta"], "SMALL ONE": ["gamma"], "SMALL TWO": ["delta"], "OTHER": ["misc"]}
    rows = []
    for cat, n in sizes.items():
        for _ in range(n):
            year = rng.randint(2015, 2024)
            rows.append({"SITE": rng.choice(SITES), "DATE": f"{rng.randint(1, 12)}/{rng.randint(1, 28)}/{year}",
                         "TEXT": " ".join(rng.sample(cues[cat], 1) + rng.sample(FILLER, 5)).capitalize() + ".",
                         "CAT": cat, "PERSON": ""})
    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r["ID"] = f"W{i + 1:05d}"
    return rows


class ArmWiringTests(TempDir):
    """The controls, the gold and the draw as one company's scoring wires them (rules 3 and 4)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._t = tempfile.TemporaryDirectory()
        base = Path(cls._t.name)
        cls.rows = wiring_rows()
        exports = write_arm(base / "in", {"c1": cls.rows})
        cls.settings = arm_settings()
        cls.settings["arms"]["msha"]["per_company"] = 7
        with tempfile.TemporaryDirectory() as work:
            cls.agg, cls.mem = SC._company(cls.settings, "msha", "c1", {"file": "c1.txt"}, exports, base / "out",
                                           Path(work))
        cls.d = cls.mem["draft"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._t.cleanup()

    def test_the_majority_prior_names_the_largest_predicate_for_every_record(self) -> None:
        counts: dict[str, int] = {}
        for r in self.rows:
            if TRAIN.contains(parse_date(r["DATE"], "M/D/YYYY", LANG.months)) and r["CAT"] != "OTHER":
                counts[r["CAT"]] = counts.get(r["CAT"], 0) + 1
        largest = max(counts, key=counts.get)
        self.assertEqual(largest, "BIG")
        self.assertEqual(self.mem["majority"], "big")
        self.assertEqual(self.mem["preds"]["majority_prior"], [frozenset({"big"})] * 7)

    def test_the_permuted_control_is_learned_and_read(self) -> None:
        corpus = self.d.corpus
        order = sorted(range(len(corpus)), key=lambda i: corpus.refs[i])
        values = [corpus.categories[i] for i in order]
        random.Random("d001:permute:msha:c1").shuffle(values)
        permuted: list[tuple[str, ...]] = [()] * len(corpus)
        for pos, i in enumerate(order):
            permuted[i] = values[pos]
        lex = D.with_placeholders(D.assign_terms(self.d.table, self.d.eligible, D.record_labels(permuted,
                                                                                               self.d.plan),
                                                 self.d.plan.ids, PARAMS), IDS)
        self.assertEqual(self.mem["lexicons"]["permuted_labels"], lex)
        self.assertFalse(lex["big"][0].startswith(IDS["placeholder_prefix"]))      # the large category keeps terms
        with tempfile.TemporaryDirectory() as work:
            pack = SC.control_pack(self.d, lex, Path(work), "perm")
            rows = SC.drafted_rows(self.mem["sample"], parse_export(pipe(self.rows)), self.d)
            preds, _ = SC.read_records(pack, rows, {"other_category"})
        self.assertEqual(self.mem["preds"]["permuted_labels"], preds)
        self.assertTrue(any(preds))

    def test_the_label_names_control_is_its_labels_and_read(self) -> None:
        lex = D.with_placeholders(D.label_name_lexicon(self.d.plan, LANG), IDS)
        self.assertEqual(self.mem["lexicons"]["label_names"], lex)
        self.assertEqual(lex["big"], ["big"])
        with tempfile.TemporaryDirectory() as work:
            pack = SC.control_pack(self.d, lex, Path(work), "names")
            rows = SC.drafted_rows(self.mem["sample"], parse_export(pipe(self.rows)), self.d)
            preds, _ = SC.read_records(pack, rows, {"other_category"})
        self.assertEqual(self.mem["preds"]["label_names"], preds)

    def test_per_company_is_honoured_and_the_gold_is_specific_only(self) -> None:
        self.assertEqual((self.agg["sample"]["drawn"], len(self.mem["sample"])), (7, 7))
        self.assertGreater(self.agg["sample"]["eligible"], 7)
        for s in self.mem["sample"]:
            self.assertTrue(s.gold <= set(self.d.plan.ids))
            self.assertNotIn("other_category", s.gold)
            self.assertEqual(s.gold, frozenset({self.d.plan.of_key[D.folded(n)] for n in s.names}))

    def test_exactly_ten_first_terms_are_written(self) -> None:
        for p in self.agg["draft"]["predicates"]:
            self.assertEqual(p["first_terms"], self.d.lexicon[p["id"]][:10])
        self.assertGreater(len(self.d.lexicon["big"]), 10)
        self.assertEqual(len(next(p for p in self.agg["draft"]["predicates"] if p["id"] == "big")["first_terms"]),
                         10)


class GoldTests(unittest.TestCase):
    def test_other_bucket_and_below_floor_values_never_enter_the_gold(self) -> None:
        rows = corpus_rows(seed=95)
        header = ("ID", "SITE", "DATE", "TEXT", "CAT[]", "PERSON")
        test_day = date(2023, 4, 5)
        extra = Rows()
        for i in range(12):
            extra.add(1, f"fresh haul words number{i:02d}", "HAULAGE;OTHER", day=test_day)
            extra.add(1, f"fresh roof words number{i:02d}", "ROOF FALL;RARE THING", day=test_day)
        for i, r in enumerate(extra.rows):
            r["ID"] = f"G{i:03d}"
        for r in rows + extra.rows:
            r["CAT[]"] = r["CAT"]
        export = parse_export(pipe(rows + extra.rows, header))
        d = D.draft_export(export, roles(category="CAT[]"), TRAIN, "gold_pack", params=PARAMS, lang=LANG,
                           template=TEMPLATE)
        sample, _ = SC.draw_sample(export, d, TEST, "d001:sample:msha:c1", 10 ** 6, "c1")
        by_ref = {s.ref: s for s in sample}
        for r in extra.rows:
            s = by_ref[r["ID"]]
            self.assertEqual(len(s.names), 2)
            self.assertEqual(s.gold, frozenset({d.plan.of_key[D.folded(r["CAT"].split(";")[0])]}))
        self.assertTrue(all(s.gold <= set(d.plan.ids) for s in sample))


class DownloadCodeTests(unittest.TestCase):
    """Amendment A8: M1 counts and hashes every module the download scripts import from tools/."""

    @staticmethod
    def closure(script: Path) -> set[str]:
        import ast
        seen, todo = set(), [script]
        while todo:
            path = todo.pop()
            rel = path.relative_to(ROOT).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else []
                for name in names:
                    for base in sorted((ROOT / "tools").iterdir()):
                        candidate = base / f"{name.split('.')[0]}.py"
                        if base.is_dir() and candidate.is_file():
                            todo.append(candidate)
        return seen

    def test_the_settings_list_the_import_closure(self) -> None:
        s = json.loads(SETTINGS_PATH.read_text())
        for arm in s["arms"].values():
            with self.subTest(script=arm["download_script"]):
                self.assertEqual(set(arm["download_code"]), self.closure(ROOT / arm["download_script"]))
                self.assertEqual(arm["download_code"][0], arm["download_script"])

    def test_the_report_counts_and_hashes_them(self) -> None:
        s = json.loads(SETTINGS_PATH.read_text())
        lines = REP.script_lines(s)
        self.assertEqual(set(lines), {"tools/onboard/fetch_msha.py", "tools/onboard/fetch_nhtsa.py",
                                      "tools/market/nhtsa_export.py", "tools/market/vehicle_pack.py"})
        for path, n in lines.items():
            self.assertEqual(n, len((ROOT / path).read_text(encoding="utf-8").splitlines()))
        files = REP.code_files(s)
        self.assertIn("tools/market/nhtsa_export.py", files)
        self.assertIn("tools/market/vehicle_pack.py", files)
        self.assertIn("mycelic/collective/onboard/draft.py", files)


class AmendmentRecordTests(unittest.TestCase):
    def test_the_choice_file_records_the_amendment_before_any_run(self) -> None:
        text = CHOICE.read_text()
        self.assertIn("## Amended before any run, 2026-10-09", text)
        self.assertLess(text.index("## Amended before any run, 2026-10-09"), text.index("## Runs"))
        for needle in ("### A1. Dates", "### A2. One refusal", "### A3. The privacy floor check", "### A4.",
                       "### A5. The last guard", "### A6. NHTSA's vehicle column", "### A7.", "### A8.",
                       "### A9. The declaration, added"):
            self.assertIn(needle, text)
        self.assertEqual(text.split("## Runs", 1)[1].strip(), "None yet.")


class WorkflowAmendmentTests(TempDir):
    """Amendment A5 in the workflow (a withheld report uploads alone) and the honest download step."""

    text = WORKFLOW.read_text(encoding="utf-8")

    def step_run(self, name: str) -> str:
        """The ``run:`` block of the named step, dedented, read from the text (no YAML library)."""
        lines = self.text.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
        body, inside = [], False
        for line in lines[start + 1:]:
            if line.strip().startswith("- ") and line.startswith("      - "):
                break
            if line.strip() == "run: |":
                inside = True
                continue
            if inside:
                body.append(line)
        indent = min(len(x) - len(x.lstrip()) for x in body if x.strip())
        return "\n".join(x[indent:] for x in body).strip("\n")

    def collect(self, verdict: str | None) -> list[str]:
        script = self.step_run("collect the files to upload").split("<<'EOF'\n", 1)[1].rsplit("\nEOF", 1)[0]
        work = self.tmp / (verdict or "none")
        if verdict is not None:
            (work / "d001" / "report").mkdir(parents=True)
            (work / "d001" / "report" / "report.json").write_text(json.dumps({"verdict": verdict}))
            (work / "d001" / "report" / "report.md").write_text("# report\n")
        arm = work / "d001" / "arms" / "msha"
        for company, passed in (("c1", True), ("c2", False)):
            (arm / company / "pack").mkdir(parents=True)
            (arm / company / "pack" / "pack.json").write_text("{}")
            (arm / company / "check.json").write_text(json.dumps({"passed": passed}))
        (arm / "arm.json").write_text("{}")
        (work / "d001" / "upload").mkdir(parents=True)
        import subprocess
        import sys
        subprocess.run([sys.executable, "-c", script], cwd=work, check=True, capture_output=True, text=True)
        return sorted(p.relative_to(work / "d001" / "upload").as_posix()
                      for p in (work / "d001" / "upload").rglob("*") if p.is_file())

    def test_a_withheld_or_missing_report_uploads_alone(self) -> None:
        self.assertEqual(self.collect("withheld"), ["report.json", "report.md"])
        self.assertEqual(self.collect(None), [])

    def test_a_cleared_report_uploads_the_arm_files_and_the_packs_that_passed(self) -> None:
        for verdict in ("pass", "fail"):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.collect(verdict), ["msha/arm.json", "msha/c1/pack/pack.json", "report.json",
                                                         "report.md"])

    def test_the_download_step_runs_exactly_the_two_downloads(self) -> None:
        self.assertEqual(self.step_run("download (a failure here is not a run)").splitlines(),
                         ["python tools/onboard/fetch_msha.py download --out d001/raw/msha",
                          "python tools/onboard/fetch_nhtsa.py download --out d001/raw/nhtsa"])


# =================================================================================================== 17 the second amendment

def leaf_rows(make: str, vehicle: str, seed: int, first_id: int, leaf: bool) -> list[dict[str, str]]:
    """An NHTSA-shaped make: :func:`nhtsa_rows` with its own record ids, and, when ``leaf``, every STEERING narrative
    saying "leaf spring" (a word of another make's vehicle, NISSAN-LEAF, in the review's case)."""
    rows = nhtsa_rows(seed=seed, vehicle=vehicle)
    for i, r in enumerate(rows):
        r["odino"] = r["reporter"] = str(first_id + i)
        if leaf and r["components[]"] == "STEERING":
            r["summary"] = r["summary"][:-1] + " leaf spring."
    return rows


def nhtsa_layout_settings() -> dict[str, Any]:
    """The NHTSA roles and windows on the synthetic arm (C1, no hand pack): the guard's reading of real-shaped makes."""
    s = arm_settings()
    del s["arms"]["nhtsa"]
    arm = s["arms"]["msha"]
    arm["roles"] = copy.deepcopy(NHTSA_ROLES)
    arm["train"], arm["test"] = ["2020-01-01", "2022-12-31"], ["2023-01-01", "2024-12-31"]
    return s


class PerCompanyGuardTests(TempDir):
    """Amendment A10: the last guard reads each company against its own export only. A value of one company never
    withholds a string another company learned honestly, with two companies or with three, where one holds the cell."""

    def run_one(self, companies: dict[str, list[dict[str, str]]], header: tuple[str, ...] = HEADER,
                settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], str]:
        settings = settings or msha_only_settings()
        work = self.tmp / f"run{len(list(self.tmp.iterdir()))}"
        exports = write_arm(work / "in", companies, header)
        arm = SC.run_arm(settings, "0" * 64, "msha", exports, work / "arm", lambda: "c" * 40)
        fake_audit(work / "audit.json")
        doc = REP.run_report(settings, "s.json", "0" * 64, [work / "arm"], work / "audit.json", work / "report",
                             lambda: "c" * 40, emit=lambda s: None)
        text = (work / "report" / "report.json").read_text() + (work / "report" / "report.md").read_text()
        return doc, arm, text

    @staticmethod
    def companies(n: int, cell_in: str, cell: str, edit: Any = None) -> dict[str, list[dict[str, str]]]:
        out = {}
        for k in range(n):
            rows = corpus_rows(seed=101 + k, per_cat=90)
            for i, r in enumerate(rows):
                r["ID"] = f"{chr(75 + k)}{i + 1:05d}"
                if edit is not None:
                    edit(r)
            out[f"c{k + 1}"] = rows
        out[cell_in][4]["PERSON"] = cell
        return out

    def assert_cleared(self, doc: dict[str, Any]) -> None:
        self.assertNotEqual(doc["verdict"], "withheld", doc.get("guard_hits"))
        self.assertTrue(doc["criteria"]["M3"]["passed"], doc["criteria"]["M3"])

    @staticmethod
    def labels(arm: dict[str, Any], company: str) -> list[str]:
        return [x["label"] for x in arm["companies"][company]["draft"]["categories"]["passing_floor"]]

    @staticmethod
    def first_terms(arm: dict[str, Any], company: str, label: str) -> list[str]:
        return next(p["first_terms"] for p in arm["companies"][company]["draft"]["predicates"] if p["label"] == label)

    def test_a_cell_equal_to_another_companys_other_bucket_label(self) -> None:
        for n in (2, 3):
            with self.subTest(companies=n):
                doc, arm, text = self.run_one(self.companies(n, "c1", "Other"))
                self.assertNotIn("OTHER", self.labels(arm, "c1"))          # c1 refuses its own category
                for other in [f"c{k}" for k in range(2, n + 1)]:
                    self.assertIn("OTHER", self.labels(arm, other))      # the others print it
                self.assert_cleared(doc)
                self.assertIn('"label": "OTHER"', text)

    def test_a_name_word_of_one_company_equal_to_a_term_another_learned(self) -> None:
        for n in (2, 3):
            with self.subTest(companies=n):
                doc, arm, text = self.run_one(self.companies(n, "c2", "Kestrelvane Conveyor"))
                self.assertNotIn("conveyor", self.first_terms(arm, "c2", "HAULAGE"))
                for other in ["c1", *([f"c{k}" for k in range(3, n + 1)])]:
                    self.assertIn("conveyor", self.first_terms(arm, other, "HAULAGE"))
                self.assert_cleared(doc)
                self.assertIn("conveyor", text)

    def test_a_three_letter_identifier_equal_to_a_word_of_a_term(self) -> None:
        def car(r: dict[str, str]) -> None:
            r["TEXT"] = r["TEXT"].replace("shuttle", "shuttle car").replace("Shuttle", "Shuttle car")
        for n in (2, 3):
            with self.subTest(companies=n):
                doc, arm, text = self.run_one(self.companies(n, "c2", "CAR", car))
                self.assertFalse(any("car" in t.split() for t in self.first_terms(arm, "c2", "HAULAGE")))
                for other in ["c1", *([f"c{k}" for k in range(3, n + 1)])]:
                    self.assertTrue(any("car" in t.split() for t in self.first_terms(arm, other, "HAULAGE")))
                self.assert_cleared(doc)

    def test_a_vehicle_word_of_one_make_equal_to_a_term_another_learned(self) -> None:
        # the review's case: FORD's narratives say "leaf spring"; NISSAN's vehicle is NISSAN-LEAF-2019
        for n in (2, 3):
            with self.subTest(companies=n):
                makes = {"FORD": leaf_rows("FORD", "FORD-RANGER-2019", 111, 11400000, leaf=True),
                         "NISSAN": leaf_rows("NISSAN", "NISSAN-LEAF-2019", 112, 12400000, leaf=False)}
                if n == 3:
                    makes["HONDA"] = leaf_rows("HONDA", "HONDA-PILOT-2019", 113, 13400000, leaf=True)
                doc, arm, text = self.run_one(makes, NHTSA_COLUMNS, nhtsa_layout_settings())
                for make in [m for m in makes if m != "NISSAN"]:
                    self.assertIn("leaf", self.first_terms(arm, make, "STEERING"))
                self.assert_cleared(doc)
                self.assertIn("leaf", text)

    def test_a_companys_own_value_still_withholds_and_the_report_names_the_company(self) -> None:
        companies = self.companies(2, "c2", "Person 9")
        work = self.tmp / "own"
        settings = msha_only_settings()
        exports = write_arm(work / "in", companies)
        SC.run_arm(settings, "0" * 64, "msha", exports, work / "arm", lambda: "c" * 40)
        arm = json.loads((work / "arm" / "arm.json").read_text())
        arm["companies"]["c2"]["draft"]["predicates"][0]["label"] = "Person 9"     # c2's own forbidden value
        arm["companies"]["c1"]["draft"]["predicates"][0]["label"] = "Person 9 x"   # not a value of c1
        (work / "arm" / "arm.json").write_text(json.dumps(arm))
        fake_audit(work / "audit.json")
        doc = REP.run_report(settings, "s.json", "0" * 64, [work / "arm"], work / "audit.json", work / "report",
                             lambda: "c" * 40, emit=lambda s: None)
        self.assertEqual(doc["verdict"], "withheld")
        self.assertFalse(doc["criteria"]["M3"]["passed"])
        self.assertEqual(doc["guard_hits_by_company"]["msha"], {"c1": {}, "c2": {"refused_equal": 1}})
        md = (work / "report" / "report.md").read_text()
        self.assertIn("Hits by company: msha c2 refused_equal 1.", md)
        self.assertNotIn("Person 9", md + (work / "report" / "report.json").read_text())

    def test_an_unreadable_or_unknown_listed_export_withholds(self) -> None:
        companies = self.companies(2, "c1", "Person 2")
        settings = msha_only_settings()
        for case in ("unreadable", "not in the arm file"):
            with self.subTest(case=case):
                work = self.tmp / case.replace(" ", "_")
                exports = write_arm(work / "in", companies)
                SC.run_arm(settings, "0" * 64, "msha", exports, work / "arm", lambda: "c" * 40)
                listing = json.loads((exports / "companies.json").read_text())
                listing["c9"] = {"file": "c9.txt" if case == "unreadable" else "c2.txt"}   # c9.txt does not exist
                (exports / "companies.json").write_text(json.dumps(listing))
                fake_audit(work / "audit.json")
                doc = REP.run_report(settings, "s.json", "0" * 64, [work / "arm"], work / "audit.json",
                                     work / "report", lambda: "c" * 40, emit=lambda s: None)
                self.assertEqual(doc["verdict"], "withheld")
                self.assertEqual(doc["exports_unread"], 1)
                self.assertFalse(doc["criteria"]["M3"]["passed"])

    def test_a_company_missing_a_declared_column_is_read_with_the_rest(self) -> None:
        companies = self.companies(2, "c1", "Person 2")
        work = self.tmp / "missing"
        settings = msha_only_settings()
        exports = write_arm(work / "in", {"c1": companies["c1"]})
        (exports / "c2.txt").write_bytes(pipe(companies["c2"], HEADER[:-1]))          # no PERSON column
        listing = json.loads((exports / "companies.json").read_text())
        listing["c2"] = {"file": "c2.txt"}
        (exports / "companies.json").write_text(json.dumps(listing))
        arm = SC.run_arm(settings, "0" * 64, "msha", exports, work / "arm", lambda: "c" * 40)
        self.assertIn("RolesError", arm["companies"]["c2"]["error"])
        export = parse_export(pipe(companies["c2"], HEADER[:-1]))
        refusal = D.export_refusal(export, roles(), PARAMS)                               # no crash: the rest
        self.assertIn(D.folded(companies["c2"][0]["ID"]), refusal.equal)
        fake_audit(work / "audit.json")
        doc = REP.run_report(settings, "s.json", "0" * 64, [work / "arm"], work / "audit.json", work / "report",
                             lambda: "c" * 40, emit=lambda s: None)
        self.assertEqual(doc["verdict"], "fail")                                           # C1 and M2 fail; no hold
        self.assertTrue(doc["criteria"]["M3"]["last_guard_clean"])


def msha_only_settings() -> dict[str, Any]:
    s = arm_settings()
    del s["arms"]["nhtsa"]
    return s


def backstop_rows(seed: int, prefix: str) -> list[dict[str, str]]:
    """Record ids of 5 characters (``<prefix>0001`` ...), one of 4 (``<prefix>999``), and sites of 6 characters."""
    rows = corpus_rows(seed=seed, per_cat=90)
    for i, r in enumerate(rows):
        r["ID"] = f"{prefix}{i + 1:04d}"
        r["SITE"] = f"pit-0{SITES.index(r['SITE']) + 1}"
    rows[-1]["ID"] = f"{prefix}999"
    return rows


class BackstopTests(TempDir):
    """Amendment A11: the whole rendered report is scanned for record ids and site values of at least 5 characters of
    every company, exact after folding, as whole tokens. A planted one withholds the report."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._t = tempfile.TemporaryDirectory()
        cls.base = Path(cls._t.name)
        cls.rows = {"c1": backstop_rows(121, "Q"), "c2": backstop_rows(122, "V")}
        cls.settings = msha_only_settings()
        exports = write_arm(cls.base / "in", cls.rows)
        SC.run_arm(cls.settings, "0" * 64, "msha", exports, cls.base / "arm", lambda: "c" * 40)
        fake_audit(cls.base / "audit.json")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._t.cleanup()

    def report(self, md: str | None = None, js: str | None = None) -> tuple[dict[str, Any], str]:
        render, build = REP.render, REP.build_report
        if md is not None:
            REP.render = lambda doc: render(doc) + f"\nleak {md}\n"
        if js is not None:
            REP.build_report = lambda *a, **kw: dict(build(*a, **kw), leak=f"at {js} here")
        try:
            doc = REP.run_report(self.settings, "s.json", "0" * 64, [self.base / "arm"], self.base / "audit.json",
                                 self.tmp / "report", lambda: "c" * 40, emit=lambda s: None)
        finally:
            REP.render, REP.build_report = render, build
        text = (self.tmp / "report" / "report.json").read_text() + (self.tmp / "report" / "report.md").read_text()
        return doc, text

    def test_a_clean_report_clears_and_counts_the_values(self) -> None:
        doc, text = self.report()
        self.assertNotEqual(doc["verdict"], "withheld")
        values = doc["criteria"]["M3"]["backstop_values"]
        n = len(self.rows["c1"]) + len(self.rows["c2"]) - 2                    # each company's 4-character id aside
        self.assertEqual(values, {"report_record_id": n, "report_site": 6})

    def test_a_planted_record_id_or_site_value_withholds(self) -> None:
        cases = {"record id in report.md": ({"md": self.rows["c1"][0]["ID"]}, "report_record_id"),
                 "record id of the other company": ({"md": self.rows["c2"][7]["ID"]}, "report_record_id"),
                 "folded": ({"md": self.rows["c1"][0]["ID"].lower()}, "report_record_id"),
                 "five characters exactly": ({"md": "Q0002"}, "report_record_id"),
                 "site value in report.json": ({"js": "PIT-03"}, "report_site")}
        for case, (plant, kind) in cases.items():
            with self.subTest(case=case):
                doc, text = self.report(**plant)
                self.assertEqual(doc["verdict"], "withheld")
                self.assertFalse(doc["criteria"]["M3"]["passed"])
                self.assertEqual(doc["backstop_hits"][kind], 1)
                self.assertIn(REP.BACKSTOP_SAYS[kind], text)
                self.assertNotIn(next(iter(plant.values())), text)
                self.assertNotIn('"arms"', text)

    def test_only_whole_tokens_of_five_or_more_characters(self) -> None:
        for plant in ("Q999", "Q00019", "xQ0001", "Q0001x", "pit-0", "pit-07"):
            with self.subTest(plant=plant):
                doc, _ = self.report(md=plant)
                self.assertNotEqual(doc["verdict"], "withheld")

    def test_the_record_id_planted_into_the_render_of_a_one_company_report(self) -> None:
        # the test the first fix deleted, restored against the backstop: a record id planted into render()'s output
        rows = corpus_rows(seed=31, per_cat=90)
        exports = write_arm(self.tmp / "one", {"c1": rows})
        SC.run_arm(self.settings, "0" * 64, "msha", exports, self.tmp / "one_arm", lambda: "c" * 40)
        planted = rows[0]["ID"]
        original = REP.render
        REP.render = lambda doc: original(doc) + f"\nleak {planted}\n"
        try:
            doc = REP.run_report(self.settings, "s.json", "0" * 64, [self.tmp / "one_arm"], self.base / "audit.json",
                                 self.tmp / "report", lambda: "c" * 40, emit=lambda s: None)
        finally:
            REP.render = original
        self.assertEqual(doc["verdict"], "withheld")
        self.assertFalse(doc["criteria"]["M3"]["passed"])
        text = (self.tmp / "report" / "report.json").read_text() + (self.tmp / "report" / "report.md").read_text()
        self.assertNotIn(planted, text)


class EquipmentColumnTests(TempDir):
    """Amendment A12: MSHA's equipment columns are not declared: their values are neither read nor refused."""

    def test_the_settings_leave_the_equipment_columns_undeclared(self) -> None:
        s = json.loads(SETTINGS_PATH.read_text())
        m = s["arms"]["msha"]
        for column in ("EQUIP_MFR_NAME", "EQUIP_MODEL_NO"):
            self.assertIn(column, m["columns"])                              # still a column of the file (M1)
            self.assertNotIn(column, m["roles"]["forbidden"])
            self.assertNotIn(column, m["definition_columns"])
        self.assertEqual(s["arms"]["nhtsa"]["roles"]["forbidden"], ["reporter", "vehicle"])   # NHTSA unchanged

    def test_an_equipment_name_refuses_nothing_in_the_msha_layout(self) -> None:
        s = json.loads(SETTINGS_PATH.read_text())
        columns = tuple(s["arms"]["msha"]["columns"])
        rows = []
        for r in corpus_rows(seed=131):
            rows.append({"DOCUMENT_NO": r["ID"], "MINE_ID": r["SITE"], "ACCIDENT_DT": r["DATE"],
                         "NARRATIVE": r["TEXT"], "CLASSIFICATION": r["CAT"], "OPERATOR_NAME": r["PERSON"],
                         "EQUIP_MFR_NAME": "Kestrelvane Conveyor", "EQUIP_MODEL_NO": "Haul Shuttle 9"})
        export = parse_export(pipe(rows, columns))
        declared = SC.arm_roles(s, "msha")
        d = D.draft_export(export, declared, TRAIN, "equip", params=PARAMS, lang=LANG, template=TEMPLATE)
        for word in ("conveyor", "shuttle", "haul"):
            self.assertIn(word, d.learned["haulage"])
            self.assertIsNone(d.refusal.term(word))
        self.assertEqual(d.facts["refusal"]["terms"], 0)
        D.write_pack(d.files, self.tmp / "pack")
        self.assertTrue(C.check_pack(self.tmp / "pack", export, declared, TRAIN, params=PARAMS, lang=LANG,
                                     template=TEMPLATE)["passed"])
        old = roles_from_json({"language": "en", "roles": dict(declared.to_json()["roles"], forbidden=[
            *declared.forbidden, "EQUIP_MFR_NAME", "EQUIP_MODEL_NO"])})
        before = D.draft_export(export, old, TRAIN, "equip_old", params=PARAMS, lang=LANG, template=TEMPLATE)
        self.assertNotIn("conveyor", before.learned["haulage"])               # what A12 stops
        self.assertGreater(before.facts["refusal"]["terms"], 0)


class LabelNamesRefusalTests(TempDir):
    """Amendment A13: the label-names control passes the same refusal as the drafted and permuted lexicons."""

    def test_a_refused_label_part_is_no_term_of_the_control(self) -> None:
        rows = corpus_rows(seed=141)
        rows[2]["PERSON"] = "Roof Bolter"
        d = drafted(rows)
        self.assertEqual(d.refusal.term("roof fall"), "name_word")
        self.assertFalse(any("roof" in t.split() for t in d.learned["roof_fall"]))      # the drafted pack lost it
        self.assertEqual(D.label_name_lexicon(d.plan, LANG)["roof_fall"], ["roof fall"])  # unrefused, as before
        lex = D.label_name_lexicon(d.plan, LANG, d.refusal)
        self.assertEqual(lex["roof_fall"], [])
        self.assertEqual(lex["haulage"], ["haulage"])
        self.assertEqual(D.with_placeholders(lex, IDS)["roof_fall"], ["unlearned-roof-fall"])
        self.assertEqual(D.refused_label_parts(d.plan, LANG, d.refusal), 1)
        control = drafted(corpus_rows(seed=141))
        self.assertEqual(D.refused_label_parts(control.plan, LANG, control.refusal), 0)

    def test_the_scorer_reads_the_refused_control_and_counts_it(self) -> None:
        rows = corpus_rows(seed=142, per_cat=90)
        rows[2]["PERSON"] = "Roof Bolter"
        exports = write_arm(self.tmp / "in", {"c1": rows})
        with tempfile.TemporaryDirectory() as work:
            agg, mem = SC._company(msha_only_settings(), "msha", "c1", {"file": "c1.txt"}, exports, self.tmp / "out",
                                   Path(work))
        self.assertEqual(mem["lexicons"]["label_names"]["roof_fall"], ["unlearned-roof-fall"])
        self.assertEqual(agg["controls"]["label_names"]["refused"], 1)
        self.assertEqual(agg["controls"]["label_names"]["placeholders"], 1)


class RefusalCountTests(TempDir):
    """Amendment A14: what the refusal removed, counted per company and per arm, and printed without a refused
    string."""

    def test_refused_terms_that_would_have_been_assigned(self) -> None:
        rows = corpus_rows(seed=151)
        base = drafted(rows)                                            # no word of a person value in a narrative
        self.assertEqual(base.facts["refusal"]["terms"], 0)
        rows[2]["PERSON"] = "Roof Bolter"
        d = drafted(rows)
        self.assertEqual(d.plan.ids, base.plan.ids)
        removed = d.facts["refusal"]
        uncapped = D.assign_terms(base.table, base.eligible, base.labels, base.plan.ids,
                                  dict(PARAMS, max_terms=10 ** 6))
        would = {p: [t for t in ts if d.refusal.term(t) is not None] for p, ts in uncapped.items()}
        kept = [t for ts in base.learned.values() for t in ts if d.refusal.term(t) is not None]
        self.assertEqual(removed["terms"], d.refused_terms)
        self.assertGreater(removed["terms"], 0)
        self.assertEqual(removed["assignable"], sum(len(v) for v in would.values()))
        self.assertGreater(removed["assignable"], 0)
        self.assertEqual(removed["within_cap"], len(kept))
        self.assertEqual({p["id"]: p["refused_assignable"] for p in d.facts["predicates"]},
                         {p: len(v) for p, v in would.items()})
        self.assertEqual((removed["categories"], removed["rows"], removed["category_rows"]), (0, 0, []))

    def test_the_cap_and_the_thresholds_of_the_counterfactual(self) -> None:
        # one sentence per word, so no bigram: 'alpha' in 55 BIG and 5 SMALL records (p 55/60: assignable); 'omega'
        # in the 55 BIG (p 1: assignable); 'beta' in 20 and 20 (p 0.5: not); 'gamma' in 9 BIG and 1 SMALL (df(t, c) 9:
        # not); 'delta' in the 55 BIG (p 1, kept). The person value refuses alpha, beta, gamma and omega; each passed
        # the floor.
        r = Rows().add(9, "Alpha. Delta. Omega. Gamma.", "BIG").add(20, "Alpha. Delta. Omega. Beta.", "BIG")
        r.add(26, "Alpha. Delta. Omega.", "BIG").add(5, "Alpha. Theta.", "SMALL").add(20, "Beta. Theta.", "SMALL")
        r.add(1, "Gamma. Theta.", "SMALL").add(30, "Theta.", "SMALL")
        r.rows[0]["PERSON"] = "Alpha Beta Gamma Omega"
        d = drafted(r.rows)
        self.assertEqual(sorted(d.table.terms[t] for t in d.refused_tids), ["alpha", "beta", "gamma", "omega"])
        self.assertEqual(d.learned, {"big": ["delta"], "small": ["theta"]})
        removed = d.facts["refusal"]
        self.assertEqual((removed["terms"], removed["assignable"], removed["within_cap"]), (4, 2, 2))
        self.assertEqual({p["id"]: p["refused_assignable"] for p in d.facts["predicates"]}, {"big": 2, "small": 0})
        # K 1: the assignable count has no cap (2); within K, delta outranks both (df 55 and p 1 as omega, then the
        # term's order; alpha has the smaller p)
        one = drafted(r.rows, params=dict(PARAMS, max_terms=1))
        self.assertEqual((one.facts["refusal"]["assignable"], one.facts["refusal"]["within_cap"]), (2, 0))

    def test_refused_categories_with_their_rows(self) -> None:
        rows = corpus_rows(seed=152)
        for r in rows:
            if r["CAT"] == "SLIP OR FALL":
                r["CAT"] = "MACHINERY"
            elif r["CAT"] == "OTHER":
                r["CAT"] = "?"
        rows[5]["PERSON"] = "Harlowmere Mining Machinery"
        rows[6]["PERSON"] = "?"
        d = drafted(rows)
        removed = d.facts["refusal"]
        corpus = [r for r in rows if TRAIN.contains(parse_date(r["DATE"], "M/D/YYYY", LANG.months))]
        machinery = sum(1 for r in corpus if r["CAT"] == "MACHINERY")
        marker = sum(1 for r in corpus if r["CAT"] == "?")
        self.assertEqual(removed["categories"], 2)
        self.assertEqual(removed["category_rows"], sorted([machinery, marker], reverse=True))
        self.assertEqual(removed["rows"], machinery + marker)
        self.assertAlmostEqual(removed["share"], (machinery + marker) / len(corpus))
        self.assertEqual(removed["with_minimum"], 1)                     # '?' is a non-specific label

    def test_a_row_under_two_refused_categories_counts_once(self) -> None:
        rows = corpus_rows(seed=155)
        header = ("ID", "SITE", "DATE", "TEXT", "CAT[]", "PERSON")
        both = 0
        for r in rows:
            cat = {"SLIP OR FALL": "MACHINERY", "OTHER": "?"}.get(r["CAT"], r["CAT"])
            if cat == "MACHINERY" and both < 15:
                cat, both = "MACHINERY;?", both + 1
            r["CAT[]"] = cat
        rows[5]["PERSON"] = "Harlowmere Mining Machinery"
        rows[6]["PERSON"] = "?"
        d = D.draft_export(parse_export(pipe(rows, header)), roles(category="CAT[]"), TRAIN, "two_pack",
                           params=PARAMS, lang=LANG, template=TEMPLATE)
        corpus = [r["CAT[]"].split(";") for r in rows
                  if TRAIN.contains(parse_date(r["DATE"], "M/D/YYYY", LANG.months))]
        under = sum(1 for cats in corpus if {"MACHINERY", "?"} & set(cats))
        each = sorted([sum(1 for cats in corpus if "MACHINERY" in cats), sum(1 for cats in corpus if "?" in cats)],
                      reverse=True)
        removed = d.facts["refusal"]
        self.assertEqual(removed["category_rows"], each)
        self.assertEqual(removed["rows"], under)
        self.assertLess(removed["rows"], sum(each))                      # a row under both counts once

    def test_the_report_prints_the_counts_and_no_refused_string(self) -> None:
        c1 = corpus_rows(seed=153, per_cat=90)
        for r in c1:
            if r["CAT"] == "SLIP OR FALL":
                r["CAT"] = "MACHINERY"
        c1[5]["PERSON"] = "Harlowmere Mining Machinery"
        c1[6]["PERSON"] = "Roof Bolter"
        c2 = corpus_rows(seed=154, per_cat=90)
        for i, r in enumerate(c2):
            r["ID"] = f"B{i + 1:05d}"
        settings = msha_only_settings()
        exports = write_arm(self.tmp / "in", {"c1": c1, "c2": c2})
        arm = SC.run_arm(settings, "0" * 64, "msha", exports, self.tmp / "arm", lambda: "c" * 40)
        r1 = arm["companies"]["c1"]["draft"]["refusal"]
        total = arm["criterion"]["refusal"]
        self.assertEqual(total["categories"], 1)
        self.assertEqual(total["rows"], r1["rows"])
        self.assertEqual(total["terms"], r1["terms"])
        self.assertEqual(total["assignable"], r1["assignable"])
        self.assertEqual(total["label_parts"], 1)
        self.assertEqual(total["companies"], 2)
        self.assertEqual(total["corpus"], sum(c["draft"]["training"]["corpus"] for c in arm["companies"].values()))
        fake_audit(self.tmp / "audit.json")
        doc = REP.run_report(settings, "s.json", "0" * 64, [self.tmp / "arm"], self.tmp / "audit.json",
                             self.tmp / "report", lambda: "c" * 40, emit=lambda s: None)
        self.assertNotEqual(doc["verdict"], "withheld")
        md = (self.tmp / "report" / "report.md").read_text()
        text = md + (self.tmp / "report" / "report.json").read_text()
        rd = doc["arms"]["msha"]["companies"]["c1"]["draft"]["refusal"]
        detail = REP._refusal_detail(doc["criteria"]["C1"]["refusal"])
        self.assertIn(f"refused categories 1 (corpus rows {r1['rows']} of {total['corpus']}", detail)
        self.assertIn(f"refused floor-passing terms {r1['terms']} (would have been assigned {r1['assignable']}",
                      detail)
        self.assertIn("what the refusal removed: " + detail, md)                     # beside C1
        self.assertIn(f"| {rd['categories']}, {rd['rows']}, {REP._f(rd['share'])} | "
                      f"{rd['terms']}, {rd['assignable']}, {rd['within_cap']} | 1 |", md)   # per company
        self.assertIn(f"c1 {r1['category_rows'][0]}; c2 none.", md)
        for refused in ("MACHINERY", "Machinery", "machinery"):
            self.assertNotIn(refused, text)


class SecondAmendmentRecordTests(unittest.TestCase):
    def test_the_choice_file_records_the_second_amendment_before_any_run(self) -> None:
        text = CHOICE.read_text()
        first, second = "## Amended before any run, 2026-10-09", "## Amended again before any run, 2026-10-09"
        self.assertLess(text.index(first), text.index(second))
        self.assertLess(text.index(second), text.index("## Runs"))
        section = text.split(second, 1)[1].split("## Runs", 1)[0]
        for needle in ("### A10. The last guard reads each company against its own export",
                       "### A11. A backstop over the whole report", "### A12. MSHA's equipment columns",
                       "### A13. The label-names control goes through the refusal",
                       "### A14. What the refusal removed", "of at least 5 characters", "as a whole token",
                       "`EQUIP_MFR_NAME`", "`reporter` and `vehicle` stay forbidden"):
            self.assertIn(needle, section)
        self.assertEqual(text.split("## Runs", 1)[1].strip(), "None yet.")


if __name__ == "__main__":
    unittest.main()

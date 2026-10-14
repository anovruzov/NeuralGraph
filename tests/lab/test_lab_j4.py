"""Accuracy test A003, build 2, stage 1 (``lab.j4``, ``lab.j4labels`` and ``lab.j4terms``, the core of the lab's ``j4``
experiment; ``docs/collective/A003/CHOICE-A003.md`` as amended C1 to C32 and T1 to T11): task D's text under both
branches (C32), the payload with ``predicate_terms`` and nothing else, the schema and the cap, the arms, the lifts and
the readings as the pinned functions, the verdict lines and the line check, every cell of C19's verdict word with the
word under lever 5's text alone (C6), the confirmation case (C1), the drafted term-presence judge (C5), the three
layouts' checks pinned equal to the pinned functions on the earlier layouts, the eight sources and the stop rules (C20),
A002's questions prefix (C3), the term artifact's check with every outcome (C29), the split's removals and tripwires
(C4), the draw disjoint from the three synthetic draws with its chain file, ``lab/jt.py``'s frozen code hash (T7), and
the runner through the repo's fake model server (two passes, task D first; task B first in the confirmation case).

Nothing here measures a model: every server is the collective's fake, every archive and artifact is
``tests/lab/j4_data.py``'s (every value invented), no earlier run's or term run's artifact is downloaded and no NHTSA
host is reached. The synthetic terms are compared, never printed.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from lab import goldlabels
from lab import j1 as lab_j1
from lab import j2 as lab_j2
from lab import j2labels
from lab import j3 as lab_j3
from lab import j3labels
from lab import j4 as lab_j4
from lab import j4labels
from lab import j4terms
from lab import jt as lab_jt
from lab import notes
from lab.goldlabels import NHTSA_PACK
from lab.notes import J4_SPLIT_VIOLATED, J4_TERMS_UNVERIFIED
from lab.prereg import PLACEHOLDER_BASE_URL
from lab.responder import Responder
from mycelic.collective.edge.verify import JUDGE_MAX_TOKENS, JUDGE_TASK, decide, judge_schema, lexical_judge
from mycelic.collective.experiments.common import code_hash as common_code_hash
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import load_routing
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps, sha256_hex
from mycelic.collective.onboard.check import term_presence
from mycelic.collective.packs import loader
from mycelic.collective.packs.canonical import Canonicaliser, folded
from tests.lab import j2_data, j3_data, j4_data
from tests.lab.helpers import MANIFEST_TEST, ROOT, call_main, kill_mentioning, write_json

PACK = loader.load_pack(ROOT / NHTSA_PACK)
CHOICE = ROOT / "docs" / "collective" / "A003" / "CHOICE-A003.md"
ARCHIVE = j4_data.archive(j4_data.PER_MAKE, 1)     # 724 eligible synthetic records; every value invented
OTHER_ARCHIVE = j4_data.archive(j4_data.PER_MAKE, 2)
JT_CODE_HASH = "f5743df58d418643ef231db62485219024cbb761cb22f8889e268b589e723d76"     # T7: lab/jt.py, frozen
N, E = lab_j2.NEGATION_TASK, lab_j3.EVIDENCE_TASK
ND, ED = "judge_record_negation_terms", "judge_record_evidence_terms"
MODELS = json.loads(MANIFEST_TEST.read_text(encoding="utf-8"))["models"]
SPECIAL = {*j4_data.STRADDLING_ODINOS, *j4_data.REPEATED_ODINOS, *j4_data.BOUNDARY_ODINOS.values(),
           j4_data.TWO_MAKES_ODINO, j4_data.OTHER_MAKE_ODINO}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def jsonl(lines: list[dict[str, Any]]) -> bytes:
    return "".join(canonical_dumps(line) + "\n" for line in lines).encode("utf-8")


def refs_of(data: bytes) -> set[str]:
    return {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}


def assert_no_ref(test: unittest.TestCase, text: str, refs: set[str]) -> None:
    hits = sorted(r for r in refs if re.search(r"(?<![0-9A-Za-z])" + re.escape(r) + r"(?![0-9A-Za-z])", text))
    test.assertEqual(hits, [], f"{len(hits)} record refs in the text")


def assert_no_term(test: unittest.TestCase, text: str, terms: set[str]) -> None:
    low = text.lower()
    hits = sorted(t for t in terms if re.search(r"(?<![a-z])" + re.escape(t.lower()) + r"(?![a-z])", low))
    test.assertEqual(hits, [], f"{len(hits)} terms in the text")


def restamp(base: Path, rel: str, data: bytes) -> None:
    """Rewrite a file of an artifact and its manifest entry, as a consistent artifact would have it."""
    (base / rel).write_bytes(data)
    manifest = read_json(base / "prereg" / "prereg.json")
    manifest["files"][rel] = {"sha256": sha256_hex(data), "bytes": len(data)}
    (base / "prereg" / "prereg.json").write_bytes(canonical_bytes(manifest) + b"\n")


# --------------------------------------------------------------------------------------------------- fixtures

class _Data:
    """``j4_data.write_data`` once: the archive, the three synthetic earlier artifacts and the term artifact, the
    term check, the eight sources and the draw of 150 at seed 4, as the plan job will make them."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> type[_Data]:
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-data-"))
            cls.dir = j4_data.write_data(cls.tmp / "data")
            cls.entries = json.loads((cls.dir / "stand-in.json").read_text(encoding="utf-8"))
            cls.questions_prefix = cls.entries[2]["questions_sha256_prefix"]
            cls.used = [{k: v for k, v in e.items() if k != "questions_sha256_prefix"} for e in cls.entries]
            cls.term_entry = read_json(cls.dir / "terms-stand-in.json")
            cls.terms = j4terms.check_terms(cls.dir / "terms", cls.term_entry)
            assert cls.terms.outcome == "verified", cls.terms.summary
            cls.T = cls.terms.terms
            cls.all_terms = {t for terms in cls.T.values() for t in terms}
            cls.est = cls.establish(cls.used)
            cls.draw = cls.make_draw(cls.est)
            cls.j001 = j4_data.refs_in(cls.dir / "earlier", j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
            cls.a001 = j4_data.refs_in(cls.dir / "earlier", j4_data.A001_RUN_ID, "prereg/j2/labels.jsonl")
            cls.a002 = j4_data.refs_in(cls.dir / "earlier", j4_data.A002_RUN_ID, j4_data.J3_LABELS_FILE)
            cls.p_d = lab_j4.drafted_pack(cls.terms.document["terms"])
        return cls

    @classmethod
    def establish(cls, used: list[dict[str, Any]], directory: Path | None = None, *,
                  questions_prefix: str | None = None, blob: bytes = ARCHIVE) -> j4labels.Established:
        return j4labels.establish(directory if directory is not None else cls.dir / "earlier", used[0], used[1],
                                  used[2], lambda: blob, a002_questions_prefix=questions_prefix or cls.questions_prefix)

    @classmethod
    def make_draw(cls, est: j4labels.Established, *, n: int = 150, term: j4labels.Drafting | None = None,
                  term_archive: str | None = None, blob: bytes = ARCHIVE) -> j4labels.Draw:
        return j4labels.draw(pack_id=NHTSA_PACK, n=n, seed=4, fetch=lambda: blob, est=est,
                             term=term if term is not None else cls.terms.drafting,
                             term_archive_sha256=term_archive or cls.terms.summary["archive_sha256"],
                             used=cls.used, requested=cls.used, stand_in=True)


def tearDownModule() -> None:
    for holder in (_Data, _Artifacts, _Plan):
        if holder.tmp is not None:
            kill_mentioning(str(holder.tmp))
            shutil.rmtree(holder.tmp, True)


# --------------------------------------------------------------------------------------------------- the rule

def terms_blockquote() -> str:
    """"The terms", item 8's blockquote in CHOICE-A003.md: the sentence task D's instructions add, verbatim."""
    text = CHOICE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(text) if line.rstrip().endswith("in these words, fixed now:"))
    quote: list[str] = []
    for line in text[start + 1:]:
        stripped = line.strip()
        if stripped.startswith(">"):
            quote.append(stripped[1:].strip())
        elif quote:
            break
    return " ".join(quote)


def rule_titles() -> dict[int, str]:
    text = CHOICE.read_text(encoding="utf-8")
    section = text.split("\n## The rule\n", 1)[1].split("\n**The settings", 1)[0]
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^(\d+)\. \*\*(.+?)\*\*", section, re.MULTILINE)}


def sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.) ", text)


class RuleTests(unittest.TestCase):
    """The constants of ``lab.j4`` against the rule's text (rules 3 to 5, 10 to 13; C1, C5, C6, C19, C32)."""

    def test_task_d_s_text_is_the_rule_s_sentence_before_the_base_s_last_sentence(self) -> None:
        """Rule 3 and C32, under both branches: B's text with "The terms", item 8's sentence inserted before B's last
        sentence by one construction; a base that does not end with that sentence is refused."""
        sentence = terms_blockquote()
        self.assertGreater(len(sentence), 300)
        self.assertEqual(lab_j4.TERMS_SENTENCE, sentence)
        self.assertTrue(sentence.startswith("predicate_terms: words and phrases that earlier complaints filed under"))
        self.assertTrue(sentence.endswith("Judge the record's own statements."))
        for base, base_text, task in (("negation", lab_j2.NEGATION_INSTRUCTIONS, ND),
                                      ("evidence", lab_j3.EVIDENCE_INSTRUCTIONS, ED)):
            with self.subTest(base=base):
                built = base_text.rpartition(" Judge only this record.")[0] + " " + sentence + " Judge only this record."
                d = lab_j4.drafted_task(base)
                self.assertEqual(d.instructions, built)
                self.assertEqual(lab_j4.drafted_instructions(base_text), built)
                self.assertEqual(sentences(d.instructions)[-1], "Judge only this record.")
                self.assertEqual(d.instructions.count(sentence), 1)
                self.assertEqual(d.instructions.replace(" " + sentence, ""), base_text)          # nothing else differs
                self.assertEqual((d.name, d.data_class, d.max_tokens), (task, "raw", lab_j4.base_task(base).max_tokens))
                folded_text = d.instructions.lower()
                for code in PACK.codes:                                        # names no code or filed label
                    self.assertNotIn(code.lower(), folded_text)
                for predicate in PACK.predicates:
                    self.assertIsNone(re.search(rf"\b{re.escape(predicate.replace('_', ' '))}\b", folded_text))
        # task B is the earlier cycle's task, imported unchanged
        self.assertEqual(lab_j4.base_task("negation"), lab_j2.negation_task())
        self.assertEqual(lab_j4.base_task("evidence"), lab_j3.evidence_task())
        self.assertEqual(lab_j4.NEGATION_INSTRUCTIONS, lab_j2.NEGATION_INSTRUCTIONS)
        self.assertEqual(lab_j4.EVIDENCE_INSTRUCTIONS, lab_j3.EVIDENCE_INSTRUCTIONS)
        for bad in ("Judge one record.", lab_j2.NEGATION_INSTRUCTIONS + " More.", lab_j2.NEGATION_INSTRUCTIONS[:-1]):
            with self.assertRaises(lab_j4.J4Error):
                lab_j4.drafted_instructions(bad)
        with self.assertRaises(lab_j4.J4Error):
            lab_j4.drafted_task("rationale")

    def test_every_rule_reference_names_its_item(self) -> None:
        """Every ``rule N`` of this file (other files' rules aside) is one of its 17 rules, whose bold titles are the
        items the cross-references mean."""
        titles = rule_titles()
        self.assertEqual(sorted(titles), list(range(1, 18)))
        wanted = {1: "Records:", 2: "Questions:", 3: "The tasks:", 4: "The readings,", 5: "The arms:",
                  6: "The judging path:", 7: "Models:", 8: "The comparators,", 9: "Scoring,", 10: "Paired differences,",
                  11: "The primary pair,", 12: "The comparison count.", 13: "The verdict rule.",
                  14: "Reported beside the verdict", 15: "Units and limits", 16: "What is written,",
                  17: "The first run is the result."}
        for n, start in wanted.items():
            with self.subTest(rule=n):
                self.assertTrue(titles[n].startswith(start), titles[n])
        text = CHOICE.read_text(encoding="utf-8")
        others = ("A002's", "CHOICE-A002", "(CHOICE-A002", "J001's", "A001's", "CHOICE-A001", "standing", "(standing")
        own = [int(m.group(2)) for m in re.finditer(r"(\S+)\s+rule (\d+)", text) if m.group(1) not in others]
        self.assertEqual(sorted(set(own) - set(range(1, 18))), [])
        for n in (3, 8, 10, 11, 13, 14, 15, 16, 17):
            self.assertIn(n, own)

    def test_the_tasks_schemas_and_caps(self) -> None:
        """Rule 3: task D has task B's data class, schema and output cap (256 under N, 512 under E); the pins."""
        self.assertEqual(lab_j4.BASES, ("negation", "evidence"))
        self.assertEqual(lab_j4.tasks("negation"), (ND, N))
        self.assertEqual(lab_j4.tasks("evidence"), (ED, E))
        self.assertEqual(lab_j4.ALL_TASKS, (ND, N, ED, E))
        for d, b, schema, cap in ((ND, N, judge_schema(), JUDGE_MAX_TOKENS), (ED, E, lab_j3.evidence_schema(), 512)):
            with self.subTest(task=d):
                self.assertEqual(lab_j4.task_schema(d), schema)
                self.assertEqual(lab_j4.task_schema(b), schema)
                self.assertEqual((lab_j4.task_spec(d).max_tokens, lab_j4.task_spec(b).max_tokens), (cap, cap))
                self.assertEqual(lab_j4.task_spec(d).data_class, lab_j4.task_spec(b).data_class)
                self.assertEqual(lab_j4.schema_keys(d), tuple(schema["properties"]))
                self.assertEqual(lab_j4.TASK_MAX_TOKENS[d], cap)
        self.assertEqual((JUDGE_MAX_TOKENS, lab_j3.FIELD_MAX_TOKENS), (256, 512))
        self.assertEqual(lab_j4.TASK_FIELDS, {ND: None, N: None, ED: "citation", E: "citation"})
        for bad in (JUDGE_TASK, lab_j3.RATIONALE_TASK, "judge_record_other"):
            for fn in (lab_j4.task_spec, lab_j4.task_schema):
                with self.assertRaises(lab_j4.J4Error):
                    fn(bad)
        pins = lab_j4.task_pins("negation", "negation_terms")
        self.assertEqual(sorted(pins), ["B", "D", "base", "order"])
        self.assertEqual(pins["B"], lab_j2.task_pins()["negation"])
        self.assertEqual(lab_j4.task_pins("evidence", "evidence_terms")["B"], lab_j3.task_pins()["evidence"])
        self.assertEqual(pins["D"], {"name": ND, "data_class": "raw",
                                     "instructions_sha256": sha256_hex(lab_j4.drafted_task("negation").instructions),
                                     "schema_sha256": sha256_hex(canonical_bytes(judge_schema())), "max_tokens": 256})
        self.assertEqual((pins["base"], pins["order"]), ("negation", [ND, N]))
        self.assertEqual(lab_j4.task_pins("evidence", "evidence")["order"], [E, ED])

    def test_the_payload_is_the_shipped_payload_plus_predicate_terms(self) -> None:
        """"The terms", item 8: task D's payload is ``lab.j1.payload`` with ``question.predicate_terms`` (T[predicate]
        in the drafter's order, the empty list when T has none) and nothing else; task B's is the shipped payload;
        the narrative is never cut shorter for the terms."""
        d = _Data.get()
        records = {r["record_ref"]: r for r, _ in lab_j1.parse_labels(d.draw.data)}
        questions = lab_j1.read_questions(lab_j1.build_questions(PACK, d.draw.data, seed=4, parts=5)[0])
        seen = {"with": 0, "empty": 0}
        for q in questions:
            record = records[q["record_ref"]]
            shipped = lab_j1.payload(PACK, q, record)
            got = lab_j4.payload(PACK, q, record, d.T)
            with self.subTest(question=q["question_id"]):
                self.assertEqual(got["record"], shipped["record"])
                self.assertEqual(sorted(got["question"]), sorted([*shipped["question"], "predicate_terms"]))
                self.assertEqual({k: v for k, v in got["question"].items() if k != "predicate_terms"},
                                 shipped["question"])
                self.assertEqual(got["question"]["predicate_terms"], d.T.get(q["predicate"], []))
                self.assertEqual(got["record"]["codes"], [])
                self.assertEqual(lab_j4.task_payload(ND, PACK, q, record, d.T), got)
                self.assertEqual(lab_j4.task_payload(ED, PACK, q, record, d.T), got)
                self.assertEqual(lab_j4.task_payload(N, PACK, q, record, d.T), shipped)
                self.assertEqual(lab_j4.task_payload(E, PACK, q, record, d.T), shipped)
            seen["with" if got["question"]["predicate_terms"] else "empty"] += 1
        self.assertGreater(seen["with"], 20)
        self.assertGreater(seen["empty"], 20)
        # canonical JSON: the key follows predicate_label in sorted order, inside the data block
        rendered = canonical_dumps(got["question"])
        self.assertLess(rendered.index('"predicate_label"'), rendered.index('"predicate_terms"'))
        self.assertEqual(lab_j4.predicate_terms(d.T, "unknown_or_other"), [])
        self.assertEqual(lab_j4.predicate_terms({"engine": ["b", "a"]}, "engine"), ["b", "a"])      # the drafter's order
        # the narrative is cut at max_input_chars before the terms are added beside it, never in its place
        q = questions[0]
        long = dict(records[q["record_ref"]], narrative="filler words here " * 600)
        many = {q["predicate"]: ["x" * 64] * 50}
        self.assertEqual(lab_j4.payload(PACK, q, long, many)["record"]["text"],
                         lab_j1.payload(PACK, q, long)["record"]["text"])
        self.assertEqual(len(lab_j4.payload(PACK, q, long, many)["record"]["text"]), PACK.extraction.max_input_chars)
        with self.assertRaises(lab_j4.J4Error):
            lab_j4.task_payload(JUDGE_TASK, PACK, q, long, many)

    def test_the_arms_the_lifts_the_readings_and_the_pins(self) -> None:
        """Rules 4, 5, 10 and 12: the four arms per branch (the rule's table), each a task and a reading; the readings
        are the pinned functions; the lifts; 4 comparisons this cycle."""
        self.assertEqual(lab_j4.ARMS, {"negation": ("negation_terms", "negation_terms_mapped", "negation",
                                                    "negation_mapped"),
                                       "evidence": ("evidence_terms", "evidence_terms_grounded", "evidence",
                                                    "evidence_grounded")})
        self.assertEqual(lab_j4.LETTERS, ("D", "Dr", "B", "Br"))
        self.assertEqual(lab_j4.arms_doc("negation"), {
            "negation_terms": {"letter": "D", "task": ND, "reading": "shipped"},
            "negation_terms_mapped": {"letter": "Dr", "task": ND, "reading": "mapped"},
            "negation": {"letter": "B", "task": N, "reading": "shipped"},
            "negation_mapped": {"letter": "Br", "task": N, "reading": "mapped"}})
        self.assertEqual(lab_j4.arms_doc("evidence"), {
            "evidence_terms": {"letter": "D", "task": ED, "reading": "shipped"},
            "evidence_terms_grounded": {"letter": "Dr", "task": ED, "reading": "grounded"},
            "evidence": {"letter": "B", "task": E, "reading": "shipped"},
            "evidence_grounded": {"letter": "Br", "task": E, "reading": "grounded"}})
        # B's arms are the earlier cycles' arms of the same name, on the same task and reading
        self.assertEqual(lab_j2.ARM_TASK["negation"], N)
        self.assertEqual(lab_j3.ARM_READING["evidence_grounded"], "grounded")
        self.assertEqual(lab_j4.READING, {"negation": "mapped", "evidence": "grounded"})
        self.assertIs(lab_j4.MAPPING, lab_j2.MAPPING)
        self.assertIs(lab_j4.GROUNDING, lab_j3.GROUNDING)
        self.assertEqual(lab_j4.reading_pin("negation"), {"name": "mapped", "pin": lab_j2.mapping_pin()})
        self.assertEqual(lab_j4.reading_pin("evidence"), {"name": "grounded", "pin": lab_j3.grounding_pin()})
        self.assertIs(lab_j4.mapped_reply, lab_j2.mapped_reply)
        self.assertIs(lab_j4.grounded_reply, lab_j3.grounded_reply)
        self.assertIs(lab_j4.grounded, lab_j3.grounded)
        self.assertIs(lab_j4.lift_reading, lab_j2.lift_reading)
        self.assertEqual(lab_j4.LIFTS, (("D_minus_B", "D", "B"), ("Dr_minus_Br", "Dr", "Br"), ("Dr_minus_D", "Dr", "D"),
                                        ("Br_minus_B", "Br", "B")))
        self.assertEqual(lab_j4.lifts("negation"), (("D_minus_B", "negation_terms", "negation"),
                                                    ("Dr_minus_Br", "negation_terms_mapped", "negation_mapped"),
                                                    ("Dr_minus_D", "negation_terms_mapped", "negation_terms"),
                                                    ("Br_minus_B", "negation_mapped", "negation")))
        self.assertEqual(lab_j4.lifts_doc("evidence")["Dr_minus_D"],
                         {"arm": "evidence_terms_grounded", "minus": "evidence_terms", "lever": "grounding_check"})
        self.assertEqual(lab_j4.lifts_doc("negation")["Br_minus_B"],
                         {"arm": "negation_mapped", "minus": "negation", "lever": "unclear_mapping"})
        self.assertEqual({v["lever"] for k, v in lab_j4.lifts_doc("negation").items() if k.endswith("_minus_B")
                          or k == "Dr_minus_Br"}, {"drafted_terms", "unclear_mapping"})
        self.assertEqual(lab_j4.COMPARISONS_THIS_CYCLE, 4)
        self.assertEqual(lab_j4.COMPARISONS_THIS_CYCLE, len(lab_j4.ARMS["negation"]))
        self.assertEqual((lab_j4.TARGET_BA, lab_j4.VERDICT_WORDS, lab_j4.LIFT_READINGS),
                         (0.86, ("incomplete", "pass_provisional", "partial", "failure"),
                          ("helps", "hurts", "not_told_apart")))
        self.assertEqual((lab_j4.WITHHOLD_SHARE, lab_j4.BOUNDARY, lab_j4.DATA_LABEL),
                         (lab_j1.WITHHOLD_SHARE, "site:lab", "public"))
        self.assertEqual(lab_j4.LINE_KEYS, (*lab_j1.LINE_KEYS, "task", "citation", "grounded", "first_key",
                                            "mapped_verdict", "grounded_verdict", "terms_count", "terms_sha256"))
        self.assertEqual(lab_j4.CODE_FILES, (*lab_j3.CODE_FILES, "lab/jt.py", "lab/j4.py", "lab/j4labels.py",
                                             "lab/j4terms.py", "mycelic/collective/onboard/check.py"))
        for name in ("lab/jt.py", "lab/j4.py", "lab/j4labels.py", "lab/j4terms.py", "lab/j3.py", "lab/j2labels.py",
                     "mycelic/collective/edge/verify.py", "mycelic/collective/onboard/check.py"):
            self.assertIn(name, lab_j4.code_files_list())
        self.assertEqual(lab_j4.code_hash(), common_code_hash([ROOT / p for p in lab_j4.code_files_list()]))
        self.assertEqual(lab_j4.EXCLUSION_KEYS, (*lab_j3.EXCLUSION_KEYS, "split"))
        for key in ("base_task", "drafted_pack", "terms", "lexical", "lexical_drafted", "presence_drafted", "prior",
                    "reading", "confirmation", "headline_secondary", "other_reading_arm", "comparisons_this_cycle"):
            self.assertIn(key, lab_j4.PREREG_KEYS)
        # the comparators of rule 8, the three lexical judges first, and the pass test's clause each difference carries
        self.assertEqual(lab_j4.COMPARATORS, ("lexical_drafted", "lexical", "presence_drafted", "prior"))
        self.assertEqual(lab_j4.JUDGE_CLAUSES, {"lexical_drafted": "beats_lexical", "lexical": "beats_hand_lexical",
                                                "presence_drafted": "beats_term_presence"})
        self.assertEqual([name for name, _, _ in lab_j4.JUDGE_PAIRS],
                         ["drafted_minus_hand", "presence_minus_hand", "presence_minus_drafted"])

    def test_the_primary_pair_and_the_confirmation_case(self) -> None:
        """Rules 5, 11 and 15, C1: P = (a-4b, D) and the pass order D, B; in the confirmation case (base evidence,
        primary evidence) P = (a-4b, B), D the headline secondary and the pass order B, D."""
        self.assertEqual(lab_j4.primary_arms("negation"), ("negation_terms",))
        self.assertEqual(lab_j4.primary_arms("evidence"), ("evidence_terms", "evidence"))
        self.assertEqual(lab_j4.pass_order("negation", "negation_terms"), (ND, N))
        self.assertEqual(lab_j4.pass_order("evidence", "evidence_terms"), (ED, E))
        self.assertEqual(lab_j4.pass_order("evidence", "evidence"), (E, ED))
        self.assertFalse(lab_j4.is_confirmation("negation", "negation_terms"))
        self.assertFalse(lab_j4.is_confirmation("evidence", "evidence_terms"))
        self.assertTrue(lab_j4.is_confirmation("evidence", "evidence"))
        self.assertEqual(lab_j4.headline_secondary("negation", "negation_terms"), "negation_terms_mapped")
        self.assertEqual(lab_j4.headline_secondary("evidence", "evidence"), "evidence_terms")
        self.assertEqual(lab_j4.other_reading_arm("negation", "negation_terms"), "negation_terms_mapped")
        self.assertEqual(lab_j4.other_reading_arm("evidence", "evidence"), "evidence_grounded")
        for base, arm in (("negation", "negation"), ("negation", "evidence"), ("evidence", "negation_terms"),
                          ("evidence", "evidence_grounded"), ("rationale", "rationale_terms")):
            with self.subTest(base=base, arm=arm):
                with self.assertRaises(lab_j4.J4Error):
                    lab_j4.pass_order(base, arm)

    def test_the_routing_routes_the_branch_s_two_tasks_to_the_one_endpoint(self) -> None:
        doc = lab_j4.routing_doc(MODELS, ["fake-a"], "http://127.0.0.1:1/v1", "negation")
        self.assertEqual(doc["routes"], {ND: {"endpoint": "fake-a"}, N: {"endpoint": "fake-a"}})
        self.assertEqual(doc["endpoints"], {"fake-a": lab_j1.endpoint(MODELS["fake-a"], "http://127.0.0.1:1/v1")})
        self.assertEqual(lab_j4.routing_doc(MODELS, ["fake-a"], "http://x/v1", "evidence")["routes"],
                         {ED: {"endpoint": "fake-a"}, E: {"endpoint": "fake-a"}})
        both = lab_j4.routing_doc(MODELS, ["fake-a", "fake-b"], PLACEHOLDER_BASE_URL, "negation")
        self.assertEqual((sorted(both["endpoints"]), both["routes"]), (["fake-a", "fake-b"], {}))

    def test_lab_jt_s_code_hash_is_frozen(self) -> None:
        """T7 and C29: ``lab/jt.py`` hashes to the value the term set records, the amendment copies and the judging
        plan job compares; build 2 does not edit it."""
        self.assertEqual(lab_jt.code_hash(), JT_CODE_HASH)
        self.assertEqual(common_code_hash([ROOT / "lab" / "jt.py"], ROOT), JT_CODE_HASH)
        self.assertIn(JT_CODE_HASH, CHOICE.read_text(encoding="utf-8"))
        self.assertEqual(_Data.get().terms.summary["document_jt_code_hash"], JT_CODE_HASH)

    def test_the_term_set_and_p_d(self) -> None:
        """T as the term set holds it, P_D rebuilt from it (the hand pack's detectors and fixtures, another
        vocabulary), the longest list in T by characters for the warm-up's worst case (C12)."""
        d = _Data.get()
        doc = d.terms.document
        self.assertEqual(lab_j4.terms_map(doc["terms"]), d.T)
        self.assertEqual(list(d.T), list(lab_jt.hand_spellings(PACK)))
        self.assertEqual(len(d.T), 26)
        self.assertEqual(lab_j4.read_terms(d.terms.data), (doc, d.T))
        self.assertEqual(d.p_d.hashes(), d.terms.p_d)
        self.assertEqual(d.p_d.hashes(), doc["p_d"]["hashes"])
        self.assertEqual((d.p_d.detector_hash, d.p_d.fixtures_hash), (PACK.detector_hash, PACK.fixtures_hash))
        self.assertNotEqual(d.p_d.vocabulary_hash, PACK.vocabulary_hash)
        longest = lab_j4.longest_terms(d.T)
        sizes = {p: len(canonical_dumps(t)) for p, t in d.T.items() if t}
        self.assertEqual(sizes[longest], max(sizes.values()))
        self.assertEqual(lab_j4.longest_terms({"a": ["xx"], "b": ["yy"], "c": []}), "a")       # the first on a tie
        self.assertEqual(lab_j4.longest_terms({"a": [], "b": ["y"], "c": ["zzz"]}), "c")
        self.assertIsNone(lab_j4.longest_terms({"a": []}))
        self.assertEqual(lab_j4.terms_sha256(["b", "a"]), sha256_hex(canonical_bytes(["b", "a"])))
        for bad in ([{"predicate": "a", "terms": ["x"]}, {"predicate": "a", "terms": []}],
                    [{"predicate": "a", "terms": "x"}], [{"predicate": "a", "terms": [""]}],
                    [{"predicate": 1, "terms": []}]):
            with self.assertRaises(lab_j4.J4Error):
                lab_j4.terms_map(bad)
        with self.assertRaises(lab_j4.J4Error):
            lab_j4.read_terms(canonical_bytes({**doc, "kind": "lab_j2_labels"}))
        with self.assertRaises(lab_j4.J4Error):
            lab_j4.read_terms(canonical_bytes({**doc, "terms": doc["terms"][:-1]}))


# --------------------------------------------------------------------------------------------------- lines, views

def _q(ref: str, kind: str, predicate: str, part: int = 1) -> dict[str, Any]:
    return {"question_id": f"{ref}:{kind}", "record_ref": ref, "part": part, "kind": kind, "entity_type": "vehicle",
            "entity_id": "V-1", "predicate": predicate, "filed": []}


def _lex(q: dict[str, Any], verdict: str) -> dict[str, Any]:
    d = {"confirm": "yes", "refute": "no", "unknown": "unclear"}[verdict]
    return lab_j1.verdict_line(q, lab_j1.stand_in(q["record_ref"]), {"mentions_entity": "yes", "describes_predicate": d},
                               None)


TEXTS = {"A": "The engine stalled at speed and the tires were fine.", "B": "Seats rattled; the air bags did not deploy."}
T_SMALL = {"engine": ["stalled", "misfire"], "tires": ["tread"], "air_bags": [], "seats": ["rattle", "cushion"]}


def _line(q: dict[str, Any], task: str, m: str | None, d: str | None, error_kind: str | None = None, *,
          citation: str | None = None, first: bool = True) -> dict[str, Any]:
    reply: dict[str, Any] | None
    if m is None:
        reply = None
    else:
        two = {"mentions_entity": m, "describes_predicate": d}
        if lab_j4.TASK_FIELDS[task] == "citation":
            cited = citation if citation is not None else ""
            reply = {"citation": cited, **two} if first else {**two, "citation": cited}
        else:
            reply = two
    terms = lab_j4.predicate_terms(T_SMALL, q["predicate"]) if lab_j4.TASK_DRAFTED[task] else None
    return lab_j4.verdict_line(q, lab_j1.stand_in(q["record_ref"]), reply, error_kind, task, TEXTS[q["record_ref"]],
                               terms)


class LineTests(unittest.TestCase):
    """Rule 16: the j4 line, the j1-shaped views of the four arms of each branch through ``lab.j1.score`` and
    ``lab.j1.paired``, and the line check's every outcome."""

    def setUp(self) -> None:
        self.qs = [_q("A", "positive", "engine"), _q("A", "negative", "tires"), _q("B", "positive", "air_bags"),
                   _q("B", "negative", "seats")]
        a_pos, a_neg, b_pos, b_neg = self.qs
        self.n_lines = [
            _line(a_pos, ND, "yes", "yes"), _line(a_neg, ND, "yes", "unclear"), _line(b_pos, ND, "yes", "no"),
            _line(b_neg, ND, None, None, "timeout"),
            _line(a_pos, N, "yes", "unclear"), _line(a_neg, N, "yes", "no"), _line(b_pos, N, "yes", "yes"),
            _line(b_neg, N, "unclear", "unclear")]
        self.e_lines = [
            _line(a_pos, ED, "yes", "yes", citation="engine stalled"),          # grounded confirm
            _line(a_neg, ED, "yes", "yes", citation="tires exploded"),          # not grounded: G reads no
            _line(b_pos, ED, "yes", "unclear", citation=""),
            _line(b_neg, ED, None, None, "timeout"),
            _line(a_pos, E, "yes", "yes", citation="the tires were fine"), _line(a_neg, E, "yes", "no", citation=""),
            _line(b_pos, E, "yes", "yes", citation="air bags"), _line(b_neg, E, "no", "no", citation="")]

    def test_every_verdict_is_the_pinned_decide_over_the_one_record(self) -> None:
        q = self.qs[0]
        w = lab_j1.stand_in("A")
        good, bad = TEXTS["A"][4:20], "a passage of no record"
        answers = ("yes", "no", "unclear")
        for m, d in itertools.product(answers, answers):
            two = {"mentions_entity": m, "describes_predicate": d}
            shipped = decide([w], [(w, two)], 0).verdict
            mapped = decide([w], [(w, lab_j2.mapped_reply(two))], 0).verdict
            for task, extra in ((ND, {}), (N, {}), (ED, {"citation": good}), (ED, {"citation": bad}),
                                (E, {"citation": good}), (E, {"citation": ""})):
                reply = {**extra, **two}
                terms = T_SMALL["engine"] if lab_j4.TASK_DRAFTED[task] else None
                line = lab_j4.verdict_line(q, w, reply, None, task, TEXTS["A"], terms)
                with self.subTest(reply=(m, d), task=task, extra=extra):
                    self.assertEqual(sorted(line), sorted(lab_j4.LINE_KEYS))
                    self.assertEqual({k: line[k] for k in lab_j1.LINE_KEYS}, lab_j1.verdict_line(q, w, two, None))
                    self.assertEqual((line["task"], line["verdict"]), (task, shipped))
                    self.assertEqual(line["first_key"], next(iter(reply)))
                    if lab_j4.TASK_DRAFTED[task]:
                        self.assertEqual((line["terms_count"], line["terms_sha256"]),
                                         (2, sha256_hex(canonical_bytes(["stalled", "misfire"]))))
                    else:
                        self.assertEqual((line["terms_count"], line["terms_sha256"]), (None, None))
                    if lab_j4.TASK_BASE[task] == "negation":
                        self.assertEqual((line["mapped_verdict"], line["citation"], line["grounded"],
                                          line["grounded_verdict"]), (mapped, None, None, None))
                        continue
                    is_grounded = extra["citation"] == good
                    self.assertEqual((line["citation"], line["grounded"], line["mapped_verdict"]),
                                     (extra["citation"], is_grounded, None))
                    self.assertEqual(line["grounded_verdict"],
                                     decide([w], [(w, lab_j3.grounded_reply(two, is_grounded))], 0).verdict)
        # the line never holds a term: only the count and the sha256 of the list the payload carried
        line = lab_j4.verdict_line(q, w, {"mentions_entity": "yes", "describes_predicate": "yes"}, None, ND, TEXTS["A"],
                                   ["stalled", "misfire"])
        assert_no_term(self, canonical_dumps(line), {"stalled", "misfire"})
        failed = lab_j4.verdict_line(q, w, None, "timeout", ED, TEXTS["A"], [])
        self.assertEqual((failed["verdict"], failed["grounded_verdict"], failed["grounded"], failed["first_key"],
                          failed["citation"], failed["scored"], failed["terms_count"]),
                         ("unknown", "unknown", None, None, None, False, 0))
        for task, terms in ((JUDGE_TASK, None), (ND, None), (N, []), (lab_j3.RATIONALE_TASK, None)):
            with self.assertRaises(lab_j4.J4Error):
                lab_j4.verdict_line(q, w, None, "timeout", task, TEXTS["A"], terms)

    def test_the_views_of_the_four_arms_of_each_branch(self) -> None:
        lexical = [_lex(q, v) for q, v in zip(self.qs, ("refute", "confirm", "confirm", "refute"))]
        views = {arm: lab_j4.arm_lines(self.n_lines + self.e_lines, arm) for arm in lab_j4.ARM_TASK}
        self.assertEqual(views["negation_terms"], [{k: x[k] for k in lab_j1.LINE_KEYS} for x in self.n_lines[:4]])
        self.assertEqual([x["verdict"] for x in views["negation_terms"]], ["confirm", "unknown", "refute", "unknown"])
        self.assertEqual([x["verdict"] for x in views["negation_terms_mapped"]], ["confirm", "refute", "refute",
                                                                                  "unknown"])
        self.assertEqual([x["describes_predicate"] for x in views["negation_terms_mapped"]], ["yes", "no", "no", None])
        self.assertEqual([x["verdict"] for x in views["negation"]], ["unknown", "refute", "confirm", "unknown"])
        self.assertEqual([x["verdict"] for x in views["negation_mapped"]], ["refute", "refute", "confirm", "unknown"])
        self.assertEqual([x["verdict"] for x in views["evidence_terms"]], ["confirm", "confirm", "unknown", "unknown"])
        self.assertEqual([x["verdict"] for x in views["evidence_terms_grounded"]], ["confirm", "refute", "unknown",
                                                                                    "unknown"])
        self.assertEqual([x["describes_predicate"] for x in views["evidence_terms_grounded"]],
                         ["yes", "no", "unclear", None])
        self.assertEqual([x["verdict"] for x in views["evidence"]], ["confirm", "refute", "confirm", "unknown"])
        self.assertEqual([x["verdict"] for x in views["evidence_grounded"]], ["confirm", "refute", "confirm",
                                                                              "unknown"])
        self.assertEqual([x["grounded"] for x in self.e_lines[4:]], [True, False, True, False])
        for arm, view in views.items():
            with self.subTest(arm=arm):
                self.assertEqual(len(view), 4)
                for line in view:
                    self.assertEqual(sorted(line), sorted(lab_j1.LINE_KEYS))
                score = lab_j1.score(self.qs, view, bootstrap_b=1000, bootstrap_seed=4)
                self.assertEqual(score["records_scored"] + score["records_dropped"], 2)
                lab_j1.paired(self.qs, view, lexical, bootstrap_b=1000, bootstrap_seed=4)
        # a reading arm's view is the earlier cycles' view of the same reading (lab.j2's for M, lab.j3's for G)
        self.assertEqual(views["negation_mapped"], lab_j2.arm_lines(
            [dict({k: x[k] for k in lab_j1.LINE_KEYS}, task=x["task"], mapped_verdict=x["mapped_verdict"])
             for x in self.n_lines], "negation_mapped"))
        with self.assertRaises(lab_j4.J4Error):
            lab_j4.arm_lines(self.n_lines, "composite")

    def test_the_line_check(self) -> None:
        for q, line in zip([*self.qs] * 2, self.n_lines):
            self.assertIsNone(lab_j4.line_problem(q, line, TEXTS[q["record_ref"]], T_SMALL), line)
        for q, line in zip([*self.qs] * 2, self.e_lines):
            self.assertIsNone(lab_j4.line_problem(q, line, TEXTS[q["record_ref"]], T_SMALL), line)
        q, nd, n_line = self.qs[1], self.n_lines[1], self.n_lines[5]          # ND yes/unclear; N yes/no
        text = TEXTS["A"]

        def check(line: Any, q: dict[str, Any] = q, text: str = text, terms: Any = T_SMALL) -> str | None:
            return lab_j4.line_problem(q, line, text, terms)

        self.assertEqual(check({k: v for k, v in nd.items() if k != "task"}), "keys")
        self.assertEqual(check({**nd, "extra": 1}), "keys")
        self.assertEqual(check("not a line"), "keys")
        self.assertEqual(check({**nd, "task": JUDGE_TASK}), "task")
        self.assertEqual(check({**nd, "task": lab_j3.RATIONALE_TASK}), "task")
        self.assertEqual(check({**nd, "verdict": "refute"}), "verdict")
        self.assertEqual(check({**nd, "kind": "positive"}), "question")
        self.assertEqual(check({**nd, "mentions_entity": "maybe"}), "answers")
        self.assertEqual(check({**nd, "citation": ""}), "citation")
        self.assertEqual(check({**nd, "first_key": "citation"}), "first_key")
        self.assertEqual(check({**nd, "first_key": None}), "first_key")
        self.assertIsNone(check({**nd, "first_key": "describes_predicate"}))          # B19: shape only
        self.assertEqual(check({**nd, "mapped_verdict": "unknown"}), "mapped_verdict")
        self.assertEqual(check({**nd, "grounded": False}), "grounded")
        self.assertEqual(check({**nd, "grounded_verdict": "refute"}), "grounded_verdict")
        # the terms recomputed from T: the count and the sha256 of the list the payload carried
        self.assertEqual(check({**nd, "terms_count": 2}), "terms_count")
        self.assertEqual(check({**nd, "terms_count": True}), "terms_count")
        self.assertEqual(check({**nd, "terms_count": None}), "terms_count")
        self.assertEqual(check({**nd, "terms_sha256": sha256_hex(canonical_bytes(["tread", "x"]))}), "terms_sha256")
        self.assertEqual(check({**nd, "terms_sha256": None}), "terms_sha256")
        self.assertEqual(check(nd, terms={**T_SMALL, "tires": ["sidewall"]}), "terms_sha256")
        self.assertEqual(check(nd, terms={**T_SMALL, "tires": []}), "terms_count")
        self.assertEqual(check(nd, terms={}), "terms_count")
        self.assertEqual(check({**n_line, "terms_count": 1}), "terms_count")
        self.assertEqual(check({**n_line, "terms_sha256": nd["terms_sha256"]}), "terms_sha256")
        self.assertIsNone(check(n_line, terms={}))                              # task B reads no terms
        # a consistent change of the reply and its readings is a line the runner could have written
        self.assertIsNone(check({**nd, "describes_predicate": "no", "verdict": "refute", "mapped_verdict": "refute"}))
        # branch E: the citation, grounded recomputed from the text, G recomputed; no mapped verdict
        eq, ed = self.qs[1], self.e_lines[1]                                    # ED yes/yes, citation not grounded
        self.assertIsNone(check(ed, q=eq))
        self.assertEqual(check({**ed, "citation": None}, q=eq), "citation")
        self.assertEqual(check({**ed, "citation": 3}, q=eq), "citation")
        self.assertEqual(check({**ed, "first_key": "foo"}, q=eq), "first_key")
        self.assertEqual(check({**ed, "mapped_verdict": "confirm"}, q=eq), "mapped_verdict")
        self.assertEqual(check({**ed, "grounded": True}, q=eq), "grounded")
        self.assertEqual(check({**ed, "grounded": None}, q=eq), "grounded")
        self.assertEqual(check({**ed, "grounded_verdict": "confirm"}, q=eq), "grounded_verdict")
        self.assertEqual(check(ed, q=eq, text="the tires exploded on the road"), "grounded")
        self.assertIsNone(check({**ed, "grounded": True, "grounded_verdict": "confirm"}, q=eq,
                                text="the tires exploded on the road"))
        self.assertEqual(check({**ed, "terms_count": 0}, q=eq), "terms_count")
        failed = self.e_lines[3]
        self.assertIsNone(lab_j4.line_problem(self.qs[3], failed, TEXTS["B"], T_SMALL))
        self.assertEqual(lab_j4.line_problem(self.qs[3], {**failed, "citation": ""}, TEXTS["B"], T_SMALL), "citation")
        self.assertEqual(lab_j4.line_problem(self.qs[3], {**failed, "grounded": False}, TEXTS["B"], T_SMALL),
                         "grounded")
        # the shipped verdict's check is J001's, called on the projected line
        with mock.patch.object(lab_j1, "line_problem", return_value="verdict") as checked:
            self.assertEqual(check(nd), "verdict")
        self.assertEqual(sorted(checked.call_args[0][1]), sorted(lab_j1.LINE_KEYS))


# --------------------------------------------------------------------------------------------------- the verdict word

def _scores(ba: float, lo: float, hi: float, unknown: float | None = 0.1) -> dict[str, Any]:
    return {"balanced_accuracy": {"value": ba, "ci_low": lo, "ci_high": hi},
            "unknown_share": {"value": unknown, "ci_low": unknown, "ci_high": unknown}}


def _paired(diff: float, lo: float | None, hi: float) -> dict[str, Any]:
    return {"n": 150, "mean_diff": diff, "ci_low": lo, "ci_high": hi}


BEATS, LEVEL = _paired(0.1, 0.02, 0.2), _paired(0.0, -0.05, 0.05)


def _word(**changes: Any) -> tuple[str, dict[str, Any]]:
    args: dict[str, Any] = {"complete": True, "display_class": "model", "measurement": True, "dropped_share": 0.0,
                            "scores": _scores(0.9, 0.85, 0.95), "paired": _paired(0.2, 0.1, 0.3),
                            "bound": {"value": 0.58, "records": 150, "questions": 300},
                            "paired_hand": BEATS, "paired_presence": BEATS}
    args.update(changes)
    return lab_j4.verdict_word(**args)


class VerdictWordTests(unittest.TestCase):
    """Rule 13 as C6 and C19 state it: ``lab.j2.verdict_word`` under lever 5's text alone, then the hand clause and
    the term-presence clause, every cell; the words in A5's order; equality fails every strict clause."""

    def test_every_cell_of_c19(self) -> None:
        low = _scores(0.75, 0.70, 0.80)
        cells = [
            # (label, changes, the word, the word under lever 5's text alone)
            ("every clause", {}, "pass_provisional", "pass_provisional"),
            ("the target and the drafted judge, the hand judge not beaten", {"paired_hand": LEVEL}, "partial",
             "pass_provisional"),
            ("the target and the drafted judge, the term-presence judge not beaten", {"paired_presence": LEVEL},
             "partial", "pass_provisional"),
            ("the target, the drafted judge not beaten", {"paired": LEVEL}, "partial", "partial"),
            ("the target, no judge beaten", {"paired": LEVEL, "paired_hand": LEVEL, "paired_presence": LEVEL},
             "partial", "partial"),
            ("the target, the bound not beaten", {"bound": {"value": 0.9}}, "partial", "partial"),
            ("below the target, every judge beaten", {"scores": low}, "partial", "partial"),
            ("below the target, the drafted judge beaten, the hand judge not", {"scores": low, "paired_hand": LEVEL},
             "failure", "partial"),
            ("below the target, the drafted judge beaten, the term-presence judge not",
             {"scores": low, "paired_presence": LEVEL}, "failure", "partial"),
            ("below the target, the drafted judge not beaten, the others beaten", {"scores": low, "paired": LEVEL},
             "failure", "failure"),
            ("below the target, no judge beaten", {"scores": low, "paired": LEVEL, "paired_hand": LEVEL,
                                                   "paired_presence": LEVEL}, "failure", "failure"),
            ("the hand difference missing", {"paired_hand": None}, "incomplete", "pass_provisional"),
            ("the hand interval missing", {"paired_hand": _paired(0.1, None, 0.2)}, "incomplete", "pass_provisional"),
            ("the term-presence difference missing", {"paired_presence": None}, "incomplete", "pass_provisional"),
            ("the term-presence difference missing below the target", {"scores": low, "paired": LEVEL,
                                                                        "paired_presence": None},
             "incomplete", "failure"),
            ("the drafted difference missing", {"paired": None}, "incomplete", "incomplete"),
            ("the bound missing", {"bound": None}, "incomplete", "incomplete"),
            ("not complete", {"complete": False}, "incomplete", "incomplete"),
            ("not a model measurement", {"display_class": "plumbing"}, "incomplete", "incomplete"),
            ("measurement false", {"measurement": False}, "incomplete", "incomplete"),
            ("too many dropped", {"dropped_share": 2 / 150}, "incomplete", "incomplete"),
            ("no scores", {"scores": None}, "incomplete", "incomplete"),
        ]
        for label, changes, want, want5 in cells:
            word, test = _word(**changes)
            with self.subTest(cell=label):
                self.assertEqual((word, test["lever5_word"]), (want, want5))
                self.assertEqual(test["lever5_word"], lab_j2.verdict_word(**{
                    k: v for k, v in {"complete": True, "display_class": "model", "measurement": True,
                                      "dropped_share": 0.0, "scores": _scores(0.9, 0.85, 0.95),
                                      "paired": _paired(0.2, 0.1, 0.3),
                                      "bound": {"value": 0.58, "records": 150, "questions": 300}, **changes}.items()
                    if k not in ("paired_hand", "paired_presence")})[0])
                self.assertEqual(test["passed"], want == "pass_provisional")
                self.assertEqual(sorted(test["clauses"]), sorted(["balanced_accuracy_at_target", "beats_lexical",
                                                                  "beats_bound", "unknown_share_reported",
                                                                  "beats_hand_lexical", "beats_term_presence"]))
        word, test = _word()
        self.assertEqual((test["diff_hand_lexical"], test["diff_hand_lexical_ci_low"], test["diff_hand_lexical_ci_high"],
                          test["diff_term_presence"], test["diff_term_presence_ci_low"],
                          test["diff_term_presence_ci_high"]), (0.1, 0.02, 0.2, 0.1, 0.02, 0.2))
        self.assertEqual((test["diff_lexical"], test["diff_lexical_ci_low"], test["balanced_accuracy"], test["bound"]),
                         (0.2, 0.1, 0.9, 0.58))
        # equality fails every strict clause (A002's B17); the target itself is reached
        self.assertEqual(_word(paired_hand=_paired(0.05, 0.0, 0.1))[0], "partial")
        self.assertEqual(_word(paired_presence=_paired(0.05, 0.0, 0.1))[0], "partial")
        self.assertEqual(_word(paired_hand=_paired(0.05, 0.001, 0.1))[0], "pass_provisional")
        self.assertEqual(_word(scores=_scores(0.86, 0.80, 0.9))[0], "pass_provisional")
        self.assertEqual(_word(scores=_scores(0.86, 0.80, 0.9), paired_hand=LEVEL)[0], "partial")

    def test_the_word_is_lab_j2_s_where_the_added_clauses_hold(self) -> None:
        """C19: ``lab.j4.verdict_word`` equals ``lab.j2.verdict_word`` on every input whose two added clauses hold,
        and ``lab.j4.pass_test`` is ``lab.j2.pass_test`` with the two clauses added."""
        grid = itertools.product((0.9, 0.86, 0.859, 0.7), (0.1, 0.0, -0.1, None), (0.5, 0.88, None),
                                 (True, False), (0.0, 0.02))
        compared = 0
        for ba, drafted_low, bound, complete, dropped in grid:
            kwargs = {"complete": complete, "display_class": "model", "measurement": True, "dropped_share": dropped,
                      "scores": _scores(ba, ba - 0.05, ba + 0.05),
                      "paired": _paired(0.1, drafted_low, 0.2) if drafted_low is not None else None,
                      "bound": {"value": bound} if bound is not None else None}
            word2, test2 = lab_j2.verdict_word(**kwargs)
            word4, test4 = lab_j4.verdict_word(**kwargs, paired_hand=BEATS, paired_presence=BEATS)
            with self.subTest(ba=ba, drafted_low=drafted_low, bound=bound, complete=complete, dropped=dropped):
                self.assertEqual(word4, word2)
                self.assertEqual({k: v for k, v in test4["clauses"].items()
                                  if k not in ("beats_hand_lexical", "beats_term_presence")}, test2["clauses"])
                self.assertEqual(test4["passed"], test2["passed"])
                self.assertEqual(test4["lever5_word"], word2)
            compared += 1
        self.assertEqual(compared, 4 * 4 * 3 * 2 * 2)
        test = lab_j4.pass_test(_scores(0.9, 0.85, 0.95), _paired(0.2, 0.1, 0.3), {"value": 0.5}, LEVEL, BEATS)
        self.assertEqual({k: v for k, v in test.items() if k in lab_j2.pass_test(None, None, None)
                          and k not in ("clauses", "passed")},
                         {k: v for k, v in lab_j2.pass_test(_scores(0.9, 0.85, 0.95), _paired(0.2, 0.1, 0.3),
                                                            {"value": 0.5}).items() if k not in ("clauses", "passed")})
        self.assertEqual((test["clauses"]["beats_hand_lexical"], test["clauses"]["beats_term_presence"],
                          test["passed"]), (False, True, False))
        self.assertEqual(lab_j4.lever5_word(complete=True, display_class="model", measurement=True, dropped_share=0.0,
                                            scores=_scores(0.9, 0.85, 0.95), paired=_paired(0.2, 0.1, 0.3),
                                            bound={"value": 0.5}), "pass_provisional")


# --------------------------------------------------------------------------------------------------- comparators

def crafted(records: list[dict[str, Any]], texts: list[str], predicates: list[str]
            ) -> tuple[bytes, list[dict[str, Any]]]:
    """Label lines of ``records`` with their narratives replaced by ``texts``, and one positive question each about
    the matching predicate (a synthetic case: every value invented)."""
    lines, questions = [], []
    for i, (record, text, predicate) in enumerate(zip(records, texts, predicates)):
        rec = dict(record, narrative=text)
        vehicle = rec["entities"]["vehicle"][0]
        gold = [{"entity_type": "vehicle", "entity_id": vehicle, "negated": False, "predicate": predicate}]
        lines.append(canonical_dumps({"record": rec, "gold": gold}) + "\n")
        questions.append({"question_id": f"{rec['record_ref']}:positive", "record_ref": rec["record_ref"], "part": 1,
                          "kind": "positive", "entity_type": "vehicle", "entity_id": vehicle, "predicate": predicate,
                          "filed": [predicate]})
    return "".join(lines).encode("utf-8"), questions


class ComparatorTests(unittest.TestCase):
    """Rule 8 and C5: the drafted lexical judge over P_D, the hand-pack lexical judge, the drafted term-presence judge
    over ``onboard.check.term_presence`` and the record-blind control, on every question before any model runs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.d = _Data.get()
        cls.questions = lab_j1.read_questions(lab_j1.build_questions(PACK, cls.d.draw.data, seed=4, parts=5)[0])
        cls.lines = lab_j4.comparator_lines(PACK, cls.d.p_d, cls.d.draw.data, cls.questions, cls.d.T)

    def test_the_comparators_are_the_pinned_functions(self) -> None:
        d, qs = self.d, self.questions
        self.assertEqual(list(self.lines), list(lab_j4.COMPARATORS))
        self.assertEqual(self.lines["lexical"], lab_j1.lexical_verdicts(PACK, d.draw.data, qs))
        self.assertEqual(self.lines["lexical_drafted"], lab_j1.lexical_verdicts(d.p_d, d.draw.data, qs))
        self.assertEqual(self.lines["prior"], lab_j1.prior_verdicts(qs))
        for name, lines in self.lines.items():
            with self.subTest(comparator=name):
                self.assertEqual([line["question_id"] for line in lines], [q["question_id"] for q in qs])
                for line in lines:
                    self.assertEqual(sorted(line), sorted(lab_j1.LINE_KEYS))
                    self.assertIsNone(lab_j1.line_problem(next(q for q in qs if q["question_id"]
                                                               == line["question_id"]), line))
                score = lab_j1.score(qs, lines, bootstrap_b=1000, bootstrap_seed=4)
                self.assertEqual((score["records_scored"], score["records_dropped"]), (150, 0))
        pairs = lab_j4.judge_pairs(qs, self.lines, bootstrap_b=1000, bootstrap_seed=4)
        self.assertEqual(list(pairs), ["drafted_minus_hand", "presence_minus_hand", "presence_minus_drafted"])
        self.assertEqual(pairs["drafted_minus_hand"], lab_j1.paired(qs, self.lines["lexical_drafted"],
                                                                    self.lines["lexical"], bootstrap_b=1000,
                                                                    bootstrap_seed=4))
        self.assertEqual(pairs["presence_minus_drafted"], lab_j1.paired(qs, self.lines["presence_drafted"],
                                                                        self.lines["lexical_drafted"],
                                                                        bootstrap_b=1000, bootstrap_seed=4))
        # the drafted judge reads T: on this draw it answers otherwise than the hand judge somewhere
        self.assertNotEqual([x["verdict"] for x in self.lines["lexical_drafted"]],
                            [x["verdict"] for x in self.lines["lexical"]])
        # the same payload serves both judges and the model: the hand pack and P_D build it alike
        records = {r["record_ref"]: r for r, _ in lab_j1.parse_labels(d.draw.data)}
        for q in qs[:20]:
            self.assertEqual(lab_j1.payload(d.p_d, q, records[q["record_ref"]]),
                             lab_j1.payload(PACK, q, records[q["record_ref"]]))
            self.assertEqual(lexical_judge(d.p_d, Canonicaliser(d.p_d))(lab_j4.payload(PACK, q, records[q["record_ref"]],
                                                                                       d.T)),
                             lexical_judge(d.p_d, Canonicaliser(d.p_d))(lab_j1.payload(PACK, q,
                                                                                       records[q["record_ref"]])))

    def test_the_term_presence_judge_on_every_question(self) -> None:
        """C5: ``mentions_entity`` as the drafted lexical judge answers; ``describes_predicate`` yes exactly when
        ``check.term_presence`` finds a term of T[predicate] in the payload's text; the verdict by the pinned
        ``decide``."""
        records = {r["record_ref"]: r for r, _ in lab_j1.parse_labels(self.d.draw.data)}
        drafted = {line["question_id"]: line for line in self.lines["lexical_drafted"]}
        for q, line in zip(self.questions, self.lines["presence_drafted"]):
            record = records[q["record_ref"]]
            text = lab_j1.payload(PACK, q, record)["record"]["text"]
            found = term_presence([text], self.d.T.get(q["predicate"], []))[0]
            reply = {"mentions_entity": drafted[q["question_id"]]["mentions_entity"],
                     "describes_predicate": "yes" if found else "no"}
            with self.subTest(question=q["question_id"]):
                self.assertEqual(line, lab_j1.verdict_line(q, lab_j1.window_record(record), reply, None))
        with self.assertRaises(lab_j4.J4Error):
            lab_j4.presence_verdicts(PACK, self.d.draw.data, self.questions, self.d.T, self.lines["lexical_drafted"][1:])

    def test_the_term_presence_judge_confirms_a_negated_mention_the_lexical_judge_refutes(self) -> None:
        """C5: a narrative that mentions a drafted term only as negated ("no <term>"): the drafted lexical judge,
        with the pack's negation cues within three tokens, refutes; the term-presence judge confirms; a plain mention
        both confirm; a predicate whose list is empty matches nothing, whatever the text."""
        d = self.d
        records = [r for r, _ in lab_j1.parse_labels(d.draw.data)]
        with_terms = [p for p, terms in d.T.items() if any(" " not in t for t in terms)]
        empty = [p for p, terms in d.T.items() if not terms]
        self.assertGreaterEqual(len(with_terms), 5)
        texts, predicates = [], []
        for p in with_terms:
            term = next(t for t in d.T[p] if " " not in t)
            texts += [f"The owner said there was no {term} on the drive home.",
                      f"The owner reported {term} again on the highway."]
            predicates += [p, p]
        hand_word = json.loads((ROOT / NHTSA_PACK / "vocabulary.json").read_text(encoding="utf-8"))["predicates"]
        for p in empty[:3]:
            texts.append(f"The owner reported {hand_word[p]['lexicon']['en'][0]} again on the highway.")
            predicates.append(p)
        labels, questions = crafted(records[:len(texts)], texts, predicates)
        lexical = lab_j1.lexical_verdicts(d.p_d, labels, questions)
        presence = lab_j4.presence_verdicts(PACK, labels, questions, d.T, lexical)
        for i, p in enumerate(predicates):
            negated = i < 2 * len(with_terms) and i % 2 == 0
            plain = i < 2 * len(with_terms) and i % 2 == 1
            with self.subTest(case=i, predicate=p):
                self.assertEqual(lexical[i]["mentions_entity"], "yes")
                self.assertEqual(presence[i]["mentions_entity"], lexical[i]["mentions_entity"])
                if negated:
                    self.assertEqual((lexical[i]["verdict"], presence[i]["verdict"]), ("refute", "confirm"))
                elif plain:
                    self.assertEqual((lexical[i]["verdict"], presence[i]["verdict"]), ("confirm", "confirm"))
                else:                                           # an empty list matches nothing
                    self.assertEqual((presence[i]["describes_predicate"], presence[i]["verdict"]), ("no", "refute"))
        # mentions_entity follows the drafted lexical judge's answer, whatever it is
        changed = [dict(line, mentions_entity="unclear", describes_predicate="unclear", verdict="unknown")
                   for line in lexical]
        self.assertEqual({x["mentions_entity"] for x in lab_j4.presence_verdicts(PACK, labels, questions, d.T,
                                                                                 changed)}, {"unclear"})


# --------------------------------------------------------------------------------------------------- the artifacts

class _Artifacts:
    """The three synthetic earlier artifacts, built once; each test case works on a fresh copy."""

    tmp: Path | None = None

    @classmethod
    def template(cls) -> Path:
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-artifacts-"))
            cls.entries = j4_data.stand_in(cls.tmp / "template", ARCHIVE)
        return cls.tmp / "template"

    @classmethod
    def fresh(cls, test: unittest.TestCase) -> tuple[Path, list[dict[str, Any]], str]:
        """(a fresh copy of the artifacts' directory, the three entries without the tests-only questions prefix, the
        synthetic A002 questions prefix)."""
        template = cls.template()
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-earlier-"))
        test.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(template, tmp / "earlier")
        entries = [{k: v for k, v in e.items() if k != "questions_sha256_prefix"} for e in cls.entries]
        return tmp / "earlier", entries, cls.entries[2]["questions_sha256_prefix"]


def _noop() -> None:
    return None


def labels_cases(base: Path, entry: dict[str, Any], labels_rel: str, record_rel: str, questions_rel: str
                 ) -> dict[str, tuple[Callable[[], None], dict[str, Any]]]:
    """A001's and A002's test tables for a labels layout, on the artifact under ``base``."""
    labels = (base / labels_rel).read_bytes()
    changed = labels.replace(b"stalled", b"stopped", 1)
    assert changed != labels
    questions = read_lines(base / questions_rel)
    record = read_json(base / record_rel)
    broken = labels + b"{not a line\n"
    manifest = base / "prereg" / "prereg.json"
    return {
        "consistent": (_noop, {}),
        "no directory": (_noop, {"directory": None}),
        "another directory": (_noop, {"directory": base / "nowhere"}),
        "another run id": (_noop, {"entry": {**entry, "run_id": entry["run_id"] + 100}}),
        "a file outside the fixed list": (_noop, {"entry": {**entry, "file": questions_rel}}),
        "another prefix": (_noop, {"entry": {**entry, "sha256_prefix": "0" * 12}}),
        "a longer prefix": (_noop, {"entry": {**entry, "sha256_prefix": sha256_hex(labels)}}),
        "a changed byte": (lambda: (base / labels_rel).write_bytes(changed), {}),
        "a changed byte the request expects": (lambda: (base / labels_rel).write_bytes(changed),
                                               {"entry": {**entry, "sha256_prefix": sha256_hex(changed)[:12]}}),
        "a manifest that disagrees on the questions": (
            lambda: (base / questions_rel).write_bytes((base / questions_rel).read_bytes() + b"\n"), {}),
        "a manifest that disagrees on the record": (
            lambda: (base / record_rel).write_bytes((base / record_rel).read_bytes() + b"\n"), {}),
        "a questions file of the same refs restamped": (
            lambda: restamp(base, questions_rel, (base / questions_rel).read_bytes() + b"\n"), {}),
        "other refs in the questions": (
            lambda: restamp(base, questions_rel, jsonl([dict(questions[0], record_ref="999999999"), *questions[1:]])),
            {}),
        "a questions file of fewer records": (lambda: restamp(base, questions_rel, jsonl(questions[2:])), {}),
        "a record of another seed": (
            lambda: restamp(base, record_rel, canonical_bytes({**record, "seed": record["seed"] + 1}) + b"\n"), {}),
        "a record of another n": (lambda: restamp(base, record_rel, canonical_bytes({**record, "n": 149}) + b"\n"), {}),
        "a record of another source": (
            lambda: restamp(base, record_rel, canonical_bytes({**record, "source": "generator"}) + b"\n"), {}),
        "a record of another kind": (
            lambda: restamp(base, record_rel, canonical_bytes({**record, "kind": "lab_j1_labels"}) + b"\n"), {}),
        "a manifest that is not json": (lambda: manifest.write_bytes(b"{not json"), {}),
        "a manifest without files": (lambda: manifest.write_bytes(b'{"kind":"lab_prereg"}\n'), {}),
        "labels that are not label lines": (
            lambda: restamp(base, labels_rel, b'{"record": 1}\n'),
            {"entry": {**entry, "sha256_prefix": sha256_hex(b'{"record": 1}\n')[:12]}}),
        "a malformed line": (lambda: restamp(base, labels_rel, broken),
                             {"entry": {**entry, "sha256_prefix": sha256_hex(broken)[:12]}}),
        "a record that is not an object": (lambda: restamp(base, record_rel, b"[]\n"), {}),
        "a manifest file entry without a size": (
            lambda: manifest.write_bytes(canonical_bytes({"files": {labels_rel: {"sha256": sha256_hex(labels)}}})
                                         + b"\n"), {}),
    }


def chain_cases(base: Path, entry: dict[str, Any], chain_rel: str) -> dict[str, tuple[Callable[[], None],
                                                                                       dict[str, Any]]]:
    data = (base / chain_rel).read_bytes()
    short = b"".join(data.splitlines(keepends=True)[:-1])
    bad = b'{"ref": "1"}\n'
    return {
        "consistent": (_noop, {}),
        "no directory": (_noop, {"directory": None}),
        "another run id": (_noop, {"entry": {**entry, "run_id": entry["run_id"] + 100}}),
        "a file outside the fixed list": (_noop, {"entry": {**entry, "file": "prereg/j2/questions.jsonl"}}),
        "another prefix": (_noop, {"entry": {**entry, "sha256_prefix": "0" * 12}}),
        "a line removed": (lambda: (base / chain_rel).write_bytes(short), {}),
        "a line removed the request expects": (lambda: (base / chain_rel).write_bytes(short),
                                               {"entry": {**entry, "sha256_prefix": sha256_hex(short)[:12]}}),
        "a line that is not a ref": (lambda: restamp(base, chain_rel, bad),
                                     {"entry": {**entry, "sha256_prefix": sha256_hex(bad)[:12]}}),
        "a manifest that is not json": (lambda: (base / "prereg" / "prereg.json").write_bytes(b"{not json"), {}),
    }


class LayoutTests(unittest.TestCase):
    """C3 and item 3: ``lab.j4labels.earlier_labels_refs`` over the three labels layouts and ``earlier_chain_refs``
    over the two chain files, each pinned equal to the pinned function on the earlier layouts case for case, and A002's
    layout's every fixed outcome with its questions prefix."""

    def run_cases(self, run_id: int, which: Callable[[Path, dict[str, Any]], dict[str, Any]],
                  table: Callable[..., dict[str, tuple[Callable[[], None], dict[str, Any]]]], *rels: str,
                  compare: Callable[[Any, Any], Any], ours: Callable[..., Any]) -> int:
        directory, entries, _ = _Artifacts.fresh(self)
        labels = list(table(directory / str(run_id), which(directory, entries), *rels))
        for label in labels:
            directory, entries, _ = _Artifacts.fresh(self)
            entry = which(directory, entries)
            change, args = table(directory / str(run_id), entry, *rels)[label]
            change()
            where, asked = args.get("directory", directory), args.get("entry", entry)
            with self.subTest(case=label):
                got = ours(where, asked)
                self.assertEqual(got, compare(where, asked))
                self.assertIn(got[1], j4labels.OUTCOMES)
        return len(labels)

    def test_the_j1_layout_is_lab_j2labels_case_for_case(self) -> None:
        n = self.run_cases(j4_data.J001_RUN_ID, lambda d, e: {k: e[0][k] for k in ("run_id", "artifact", "file",
                                                                                     "sha256_prefix")},
                           labels_cases, "prereg/j1/labels.jsonl", "prereg/j1/labels.json", "prereg/j1/questions.jsonl",
                           compare=j2labels.earlier_refs, ours=j4labels.earlier_labels_refs)
        self.assertGreater(n, 20)
        directory, entries, _ = _Artifacts.fresh(self)
        refs = j4_data.refs_in(directory, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        self.assertEqual(j4labels.earlier_labels_refs(directory, entries[0]), (refs, "verified"))
        # a file of the fixed list absent from the artifact: absent here, outside A001's list there
        for file in ("prereg/j2/labels.jsonl", "prereg/j3/labels.jsonl"):
            asked = {**entries[0], "file": file}
            self.assertEqual((j4labels.earlier_labels_refs(directory, asked), j2labels.earlier_refs(directory, asked)),
                             ((None, "artifact absent"), (None, "malformed")))

    def test_the_j2_layout_is_lab_j3labels_case_for_case(self) -> None:
        n = self.run_cases(j4_data.A001_RUN_ID, lambda d, e: e[1], labels_cases, "prereg/j2/labels.jsonl",
                           "prereg/j2/labels.json", "prereg/j2/questions.jsonl",
                           compare=j3labels.earlier_labels_refs, ours=j4labels.earlier_labels_refs)
        self.assertGreater(n, 20)
        directory, entries, _ = _Artifacts.fresh(self)
        asked = {**entries[1], "file": "prereg/j3/labels.jsonl"}
        self.assertEqual((j4labels.earlier_labels_refs(directory, asked), j3labels.earlier_labels_refs(directory,
                                                                                                        asked)),
                         ((None, "artifact absent"), (None, "malformed")))

    def test_the_j2_chain_is_lab_j2labels_case_for_case(self) -> None:
        for ours in (j4labels.earlier_chain_refs, j4labels.earlier_labels_refs):
            with self.subTest(function=ours.__name__):
                self.run_cases(j4_data.A001_RUN_ID, lambda d, e: j4labels.chain_entry(e[1]), chain_cases,
                               "prereg/j2/excluded.jsonl", compare=j2labels.earlier_refs, ours=ours)
        directory, entries, _ = _Artifacts.fresh(self)
        chain = j4labels.chain_entry(entries[1])
        self.assertEqual(chain, j3labels.chain_entry(entries[1]))
        self.assertEqual(j4labels.earlier_chain_refs(directory, chain)[1], "verified")
        self.assertEqual(len(j4labels.earlier_chain_refs(directory, chain)[0]), 300)
        # a labels file is no chain file
        self.assertEqual(j4labels.earlier_chain_refs(directory, entries[1]), (None, "malformed"))

    def test_the_j3_layout_s_fixed_outcomes(self) -> None:
        """C3: A002's labels file hashing to the entry's prefix, the manifest, ``labels.json`` of kind
        ``lab_j2_labels`` saying nhtsa, 150 and seed 3, the questions file's refs equal to the labels' and its bytes
        hashing to A002's questions prefix (the stand-in's synthetic prefix here; A002's pinned one by default)."""
        want = {"consistent": "verified", "no directory": "artifact absent", "another directory": "artifact absent",
                "another run id": "artifact absent", "a file outside the fixed list": "malformed",
                "another prefix": "sha256 mismatch", "a longer prefix": "verified", "a changed byte": "sha256 mismatch",
                "a changed byte the request expects": "manifest mismatch",
                "a manifest that disagrees on the questions": "manifest mismatch",
                "a manifest that disagrees on the record": "manifest mismatch",
                "a questions file of the same refs restamped": "sha256 mismatch",
                "other refs in the questions": "sha256 mismatch", "a questions file of fewer records": "sha256 mismatch",
                "a record of another seed": "labels mismatch", "a record of another n": "labels mismatch",
                "a record of another source": "labels mismatch", "a record of another kind": "labels mismatch",
                "a manifest that is not json": "malformed", "a manifest without files": "manifest mismatch",
                "labels that are not label lines": "malformed", "a malformed line": "malformed",
                "a record that is not an object": "labels mismatch",
                "a manifest file entry without a size": "manifest mismatch"}
        rels = ("prereg/j3/labels.jsonl", "prereg/j3/labels.json", "prereg/j3/questions.jsonl")
        directory, entries, prefix = _Artifacts.fresh(self)
        self.assertEqual(sorted(labels_cases(directory / str(j4_data.A002_RUN_ID), entries[2], *rels)), sorted(want))
        a002 = j4_data.refs_in(directory, j4_data.A002_RUN_ID, rels[0])
        for label, outcome in want.items():
            directory, entries, prefix = _Artifacts.fresh(self)
            change, args = labels_cases(directory / str(j4_data.A002_RUN_ID), entries[2], *rels)[label]
            change()
            got = j4labels.earlier_labels_refs(args.get("directory", directory), args.get("entry", entries[2]),
                                               questions_prefix=prefix)
            with self.subTest(case=label):
                self.assertEqual(got, (a002 if outcome == "verified" else None, outcome))
        # the questions file's refs checked once its prefix is the one expected
        directory, entries, prefix = _Artifacts.fresh(self)
        base = directory / str(j4_data.A002_RUN_ID)
        questions = read_lines(base / rels[2])
        other = jsonl([dict(questions[0], record_ref="999999999"), *questions[1:]])
        restamp(base, rels[2], other)
        self.assertEqual(j4labels.earlier_labels_refs(directory, entries[2], questions_prefix=sha256_hex(other)[:12]),
                         (None, "refs mismatch"))
        # A002's pinned questions prefix by default: the synthetic artifact does not have it
        directory, entries, prefix = _Artifacts.fresh(self)
        self.assertEqual(j4labels.earlier_labels_refs(directory, entries[2]), (None, "sha256 mismatch"))
        self.assertEqual(j4labels.earlier_labels_refs(directory, entries[2], questions_prefix=prefix)[1], "verified")
        self.assertEqual(j4labels.earlier_labels_refs(directory, entries[2],
                                                      questions_prefix=sha256_hex((base / rels[2]).read_bytes()))[1],
                         "verified")

    def test_the_j3_chain_s_fixed_outcomes(self) -> None:
        want = {"consistent": "verified", "no directory": "artifact absent", "another run id": "artifact absent",
                "a file outside the fixed list": "malformed", "another prefix": "sha256 mismatch",
                "a line removed": "sha256 mismatch", "a line removed the request expects": "manifest mismatch",
                "a line that is not a ref": "malformed", "a manifest that is not json": "malformed"}
        for label, outcome in want.items():
            directory, entries, _ = _Artifacts.fresh(self)
            chain = j4labels.chain_entry(entries[2])
            change, args = chain_cases(directory / str(j4_data.A002_RUN_ID), chain, j4_data.J3_CHAIN_FILE)[label]
            change()
            for fn in (j4labels.earlier_chain_refs, j4labels.earlier_labels_refs):
                got = fn(args.get("directory", directory), args.get("entry", chain))
                with self.subTest(case=label, function=fn.__name__):
                    self.assertEqual(got[1], outcome)
                    self.assertEqual(got[0] is None, outcome != "verified")
        directory, entries, _ = _Artifacts.fresh(self)
        refs, outcome = j4labels.earlier_chain_refs(directory, j4labels.chain_entry(entries[2]))
        j001 = j4_data.refs_in(directory, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        a001 = j4_data.refs_in(directory, j4_data.A001_RUN_ID, "prereg/j2/labels.jsonl")
        a002 = j4_data.refs_in(directory, j4_data.A002_RUN_ID, j4_data.J3_LABELS_FILE)
        self.assertEqual((refs, outcome, len(refs)), (j001 | a001 | a002, "verified", 450))
        self.assertEqual(j2labels.earlier_refs(directory, j4labels.chain_entry(entries[2])), (None, "malformed"))

    def test_the_pins_and_the_constants(self) -> None:
        """C3: A002's prefixes from A002 run 1, the questions prefix a code constant, the layouts and the sources."""
        self.assertEqual((j4labels.A002_LABELS_SHA256_PREFIX, j4labels.A002_QUESTIONS_SHA256_PREFIX,
                          j4labels.A002_CHAIN_SHA256_PREFIX), ("0a64a62d94a7", "84486fa38568", "65d51f0fca51"))
        self.assertEqual(j4labels.A002_REBUILD, {"source": "nhtsa", "n": 150, "seed": 3})
        self.assertEqual(j4labels.COMPLAINT_FILE_SHA256_PREFIXES, {"a001": "a5a5de2d31f3", "a002": "216ecb36f9f5"})
        text = CHOICE.read_text(encoding="utf-8")
        for prefix in ("0a64a62d94a7", "84486fa38568", "65d51f0fca51", "216ecb36f9f5", "a5a5de2d31f3"):
            self.assertIn(prefix, text)
        self.assertEqual(j4labels.EXCLUDE_FILES, ("prereg/j1/labels.jsonl", "prereg/j2/excluded.jsonl",
                                                  "prereg/j2/labels.jsonl", "prereg/j3/labels.jsonl",
                                                  "prereg/j3/excluded.jsonl"))
        self.assertEqual({k: v for k, v in j4labels.LAYOUTS.items() if k in j3labels.LAYOUTS}, j3labels.LAYOUTS)
        self.assertEqual(j4labels.LAYOUTS["prereg/j3/labels.jsonl"],
                         ("prereg/j3/labels.json", "prereg/j3/questions.jsonl", {"source": "nhtsa", "n": 150, "seed": 3},
                          "lab_j2_labels"))
        self.assertEqual(j4labels.OUTCOMES, (*j2labels.OUTCOMES, "not attempted"))
        self.assertEqual((j4labels.SOURCES, j4labels.CYCLES, j4labels.CHAINS, j4labels.BY, j4labels.PROBLEMS),
                         (("J1", "R1", "A1", "R2", "K2", "B1", "R3", "K3"), ("j001", "a001", "a002"), ("K2", "K3"),
                          ("artifact", "rebuild", "chain"), ("unverified", "disagrees")))
        self.assertEqual(j4labels.CHAIN_COVERS, {"K2": ("j001", "a001"), "K3": ("j001", "a001", "a002")})
        self.assertEqual(j4labels.CONSISTENCY, ("j1_r1", "a1_r2", "b1_r3", "outside_k2", "outside_k3", "k2_union",
                                                "k3_union"))
        self.assertEqual((j4labels.EXPECTED_EXCLUDED, j4labels.EXPECTED_CHAIN_LINES), (450, 600))

    def test_the_a002_rebuild(self) -> None:
        """Item 3: R3 is ``lab.j2labels.draw`` of 150 at seed 3 over the eligible list less J001's and A001's sets,
        verified only when its labels' bytes hash to the prefix: A001's rebuild with A002's seed."""
        directory, entries, _ = _Artifacts.fresh(self)
        j001 = j4_data.refs_in(directory, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        a001 = j4_data.refs_in(directory, j4_data.A001_RUN_ID, "prereg/j2/labels.jsonl")
        a002 = j4_data.refs_in(directory, j4_data.A002_RUN_ID, j4_data.J3_LABELS_FILE)
        sha = sha256_hex((directory / str(j4_data.A002_RUN_ID) / j4_data.J3_LABELS_FILE).read_bytes())
        self.assertEqual(j4labels.rebuild_a002_refs(lambda: ARCHIVE, j001 | a001, sha[:12]), (a002, "verified", sha))
        self.assertEqual(j4labels.rebuild_a002_refs(lambda: ARCHIVE, j001 | a001), (None, "sha256 mismatch", sha))
        self.assertEqual(j4labels.rebuild_a002_refs(lambda: ARCHIVE, j001 | a001, sha[:12], seed=2)[1],
                         "sha256 mismatch")
        self.assertEqual(j4labels.rebuild_a002_refs(lambda: ARCHIVE, j001, sha[:12])[1], "sha256 mismatch")
        self.assertEqual(j4labels.rebuild_a002_refs(lambda: j2_data.archive(5, 3), j001 | a001, sha[:12]),
                         (None, "malformed", ""))
        with mock.patch.object(j3labels, "rebuild_a001_refs", return_value=(None, "x", "")) as pinned:
            j4labels.rebuild_a002_refs(lambda: ARCHIVE, j001, "abc")
        self.assertEqual(pinned.call_args.kwargs, {"n": 150, "seed": 3})


# --------------------------------------------------------------------------------------------------- the sources

def broken(entry: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The entry with each named prefix replaced by one no file has."""
    return {**entry, **{k: "0" * 12 for k in keys}}


class SourcesTests(unittest.TestCase):
    """Item 4 as amended C20: the eight sources and the stop rules on the synthetic artifacts: each cycle by its own
    sources, by a chain with the others established, chains alone stopping, R2 and R3 not attempted, every
    consistency count stopping, two chains each establishing a cycle."""

    def establish(self, directory: Path | None, entries: list[dict[str, Any]], prefix: str,
                  blob: bytes = ARCHIVE) -> j4labels.Established:
        return j4labels.establish(directory, entries[0], entries[1], entries[2], lambda: blob,
                                  a002_questions_prefix=prefix)

    def test_every_source_verifying(self) -> None:
        directory, entries, prefix = _Artifacts.fresh(self)
        est = self.establish(directory, entries, prefix)
        self.assertEqual(est.summary["sources"], {s: {"outcome": "verified", "refs": n} for s, n in (
            ("J1", 150), ("R1", 150), ("A1", 150), ("R2", 150), ("K2", 300), ("B1", 150), ("R3", 150), ("K3", 450))})
        self.assertEqual(est.summary["cycles"], {c: {"established": True, "by": "artifact", "chain": None, "refs": 150}
                                                 for c in j4labels.CYCLES})
        self.assertEqual(est.summary["consistency"], dict.fromkeys(j4labels.CONSISTENCY, 0))
        j001 = j4_data.refs_in(directory, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        a001 = j4_data.refs_in(directory, j4_data.A001_RUN_ID, "prereg/j2/labels.jsonl")
        a002 = j4_data.refs_in(directory, j4_data.A002_RUN_ID, j4_data.J3_LABELS_FILE)
        self.assertEqual((len(j001 | a001 | a002), j001 & a001, a001 & a002, j001 & a002), (450, set(), set(), set()))
        self.assertEqual((est.excluded, est.cycle_sets), (j001 | a001 | a002, {"j001": j001, "a001": a001,
                                                                               "a002": a002}))
        self.assertEqual((est.summary["excluded"], est.summary["expected_excluded"], est.problem), (450, 450, None))
        self.assertEqual(sorted(est.shas), ["R1", "R2", "R3"])
        self.assertTrue(all(len(v) == 64 for v in est.shas.values()))
        assert_no_ref(self, canonical_dumps(est.summary), est.excluded)
        self.assertEqual(j4labels.mechanisms(est), {"artifact": "verified", "rebuild": "verified"})

    def test_each_cycle_by_its_own_sources(self) -> None:
        directory, e, prefix = _Artifacts.fresh(self)
        cases = {
            "j001 by artifact alone": ([broken(e[0], "rebuild_sha256_prefix"), e[1], e[2]],
                                       {"J1": "verified", "R1": "sha256 mismatch"}, ("artifact", "artifact", "artifact"),
                                       {"j1_r1": None}),
            "j001 by rebuild alone": ([broken(e[0], "sha256_prefix"), e[1], e[2]],
                                      {"J1": "sha256 mismatch", "R1": "verified"}, ("rebuild", "artifact", "artifact"),
                                      {"j1_r1": None}),
            "a001 by artifact alone": ([e[0], broken(e[1], "rebuild_sha256_prefix"), e[2]],
                                       {"A1": "verified", "R2": "sha256 mismatch"}, ("artifact", "artifact", "artifact"),
                                       {"a1_r2": None}),
            "a001 by rebuild alone": ([e[0], broken(e[1], "sha256_prefix"), e[2]],
                                      {"A1": "sha256 mismatch", "R2": "verified"}, ("artifact", "rebuild", "artifact"),
                                      {"a1_r2": None}),
            "a002 by artifact alone": ([e[0], e[1], broken(e[2], "rebuild_sha256_prefix")],
                                       {"B1": "verified", "R3": "sha256 mismatch"}, ("artifact", "artifact", "artifact"),
                                       {"b1_r3": None}),
            "a002 by rebuild alone": ([e[0], e[1], broken(e[2], "sha256_prefix")],
                                      {"B1": "sha256 mismatch", "R3": "verified"}, ("artifact", "artifact", "rebuild"),
                                      {"b1_r3": None}),
            "every cycle by its rebuild": ([broken(x, "sha256_prefix") for x in e],
                                           {"J1": "sha256 mismatch", "A1": "sha256 mismatch", "B1": "sha256 mismatch"},
                                           ("rebuild", "rebuild", "rebuild"), {"j1_r1": None, "a1_r2": None,
                                                                              "b1_r3": None}),
        }
        for label, (used, outcomes, by, nulls) in cases.items():
            est = self.establish(directory, used, prefix)
            with self.subTest(case=label):
                self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in outcomes}, outcomes)
                self.assertEqual(tuple(est.summary["cycles"][c]["by"] for c in j4labels.CYCLES), by)
                self.assertEqual(est.summary["consistency"], {**dict.fromkeys(j4labels.CONSISTENCY, 0), **nulls})
                self.assertEqual((est.problem, len(est.excluded)), (None, 450))

    def test_a_cycle_by_a_chain_with_the_others_established(self) -> None:
        directory, e, prefix = _Artifacts.fresh(self)
        no_j001 = broken(e[0], "sha256_prefix", "rebuild_sha256_prefix")
        no_a001 = broken(e[1], "sha256_prefix", "rebuild_sha256_prefix")
        no_a002 = broken(e[2], "sha256_prefix", "rebuild_sha256_prefix")
        no_k2 = {**e[1], "chain_sha256_prefix": "0" * 12}
        no_k2_a001 = {**no_a001, "chain_sha256_prefix": "0" * 12}
        cases = {
            # (entries, by per cycle, the chain per cycle, R2, R3, the counts that are not computed)
            "j001 by K2 less A001's set": ([no_j001, e[1], e[2]], ("chain", "artifact", "artifact"),
                                           ("K2", None, None), "verified", "verified",
                                           ("j1_r1", "k2_union", "k3_union")),
            "j001 by K3 less A001's and A002's": ([no_j001, no_k2, e[2]], ("chain", "artifact", "artifact"),
                                                  ("K3", None, None), "verified", "verified",
                                                  ("j1_r1", "outside_k2", "k2_union", "k3_union")),
            "a001 by K2 less J001's set": ([e[0], no_a001, e[2]], ("artifact", "chain", "artifact"),
                                           (None, "K2", None), "sha256 mismatch", "verified",
                                           ("a1_r2", "k2_union", "k3_union")),
            "a001 by K3 less J001's and A002's": ([e[0], no_k2_a001, e[2]], ("artifact", "chain", "artifact"),
                                                  (None, "K3", None), "sha256 mismatch", "verified",
                                                  ("a1_r2", "outside_k2", "k2_union", "k3_union")),
            "a002 by K3 less J001's and A001's": ([e[0], e[1], no_a002], ("artifact", "artifact", "chain"),
                                                  (None, None, "K3"), "verified", "sha256 mismatch",
                                                  ("b1_r3", "k3_union")),
            "two chains: j001 by K2, a002 by K3 (C20)": ([no_j001, e[1], no_a002], ("chain", "artifact", "chain"),
                                                         ("K2", None, "K3"), "verified", "sha256 mismatch",
                                                         ("j1_r1", "b1_r3", "k2_union", "k3_union")),
        }
        sets = {"j001": j4_data.refs_in(directory, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl"),
                "a001": j4_data.refs_in(directory, j4_data.A001_RUN_ID, "prereg/j2/labels.jsonl"),
                "a002": j4_data.refs_in(directory, j4_data.A002_RUN_ID, j4_data.J3_LABELS_FILE)}
        for label, (used, by, chains, r2, r3, nulls) in cases.items():
            est = self.establish(directory, used, prefix)
            with self.subTest(case=label):
                cycles = est.summary["cycles"]
                self.assertEqual(tuple(cycles[c]["by"] for c in j4labels.CYCLES), by)
                self.assertEqual(tuple(cycles[c]["chain"] for c in j4labels.CYCLES), chains)
                self.assertEqual((est.summary["sources"]["R2"]["outcome"], est.summary["sources"]["R3"]["outcome"]),
                                 (r2, r3))
                self.assertEqual(est.cycle_sets, sets)
                self.assertEqual(est.summary["consistency"],
                                 {**dict.fromkeys(j4labels.CONSISTENCY, 0), **dict.fromkeys(nulls)})
                self.assertEqual((est.problem, est.excluded), (None, sets["j001"] | sets["a001"] | sets["a002"]))
                derived = [c for c in j4labels.CYCLES if cycles[c]["by"] == "chain"]
                runs = j4labels.excluded_runs(est, used, e, True)
                self.assertEqual([r["derived"] for r in runs],
                                 ["by_chain" if c in derived else None for c in j4labels.CYCLES])

    def test_chains_alone_establish_nothing_and_rebuilds_are_not_attempted(self) -> None:
        directory, e, prefix = _Artifacts.fresh(self)
        no_j001 = broken(e[0], "sha256_prefix", "rebuild_sha256_prefix")
        no_a001 = broken(e[1], "sha256_prefix", "rebuild_sha256_prefix")
        no_a002 = broken(e[2], "sha256_prefix", "rebuild_sha256_prefix")
        cases = {
            "every artifact and rebuild failing": ([no_j001, no_a001, no_a002], (None, None, None),
                                                   {"J1": "sha256 mismatch", "R1": "sha256 mismatch",
                                                    "A1": "sha256 mismatch", "R2": "not attempted",
                                                    "K2": "verified", "B1": "sha256 mismatch",
                                                    "R3": "not attempted", "K3": "verified"}, 450),
            "K2 cannot split J001 from A001": ([no_j001, no_a001, {**e[2], "chain_sha256_prefix": "0" * 12}],
                                               (None, None, "artifact"),
                                               {"R2": "not attempted", "R3": "not attempted", "K3": "sha256 mismatch"},
                                               450),
            "K3 establishes one cycle at most": ([e[0], {**no_a001, "chain_sha256_prefix": "0" * 12}, no_a002],
                                                 ("artifact", None, None),
                                                 {"R2": "sha256 mismatch", "R3": "not attempted",
                                                  "K2": "sha256 mismatch", "K3": "verified"}, 450),
            "K3 cannot split J001 from A002 without A002's set": (
                [no_j001, {**e[1], "chain_sha256_prefix": "0" * 12}, broken(e[2], "sha256_prefix")],
                (None, "artifact", None), {"R2": "not attempted", "R3": "not attempted", "B1": "sha256 mismatch"},
                450),
        }
        for label, (used, by, outcomes, excluded) in cases.items():
            est = self.establish(directory, used, prefix)
            with self.subTest(case=label):
                self.assertEqual(tuple(est.summary["cycles"][c]["by"] for c in j4labels.CYCLES), by)
                self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in outcomes}, outcomes)
                self.assertEqual((est.problem, est.summary["problem"], len(est.excluded)),
                                 ("unverified", "unverified", excluded))
                self.assertEqual(est.shas["R3"], "")
        est = self.establish(None, [no_j001, no_a001, no_a002], prefix)
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in j4labels.SOURCES},
                         {"J1": "artifact absent", "R1": "sha256 mismatch", "A1": "artifact absent",
                          "R2": "not attempted", "K2": "artifact absent", "B1": "artifact absent",
                          "R3": "not attempted", "K3": "artifact absent"})
        self.assertEqual((est.problem, est.excluded), ("unverified", set()))
        self.assertEqual(est.summary["consistency"], dict.fromkeys(j4labels.CONSISTENCY, None))

    def test_every_consistency_count_stops_the_plan_job(self) -> None:
        # J1 from another archive: its refs are not R1's
        directory, e, prefix = _Artifacts.fresh(self)
        other = Path(tempfile.mkdtemp(prefix="lab-j4-other-"))
        self.addCleanup(shutil.rmtree, other, True)
        other_entries = j4_data.stand_in(other, OTHER_ARCHIVE)
        shutil.rmtree(directory / str(j4_data.J001_RUN_ID))
        shutil.copytree(other / str(j4_data.J001_RUN_ID), directory / str(j4_data.J001_RUN_ID))
        est = self.establish(directory, [{**other_entries[0], "rebuild_sha256_prefix": e[0]["rebuild_sha256_prefix"]},
                                         e[1], e[2]], prefix)
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("J1", "R1")},
                         {"J1": "verified", "R1": "verified"})
        self.assertEqual(est.summary["consistency"]["j1_r1"], 300)
        self.assertEqual(est.summary["consistency"]["outside_k2"], 150)
        self.assertEqual(est.summary["consistency"]["outside_k3"], 150)
        self.assertEqual((est.problem, len(est.excluded)), ("disagrees", 600))
        # A1 drawn without J001's set excluded: its refs are not R2's
        directory, e, prefix = _Artifacts.fresh(self)
        shutil.rmtree(directory / str(j4_data.A001_RUN_ID))
        loose = j3_data.a001_artifact(directory, ARCHIVE, set())
        est = self.establish(directory, [e[0], {**loose, "rebuild_sha256_prefix": e[1]["rebuild_sha256_prefix"]}, e[2]],
                             prefix)
        self.assertGreater(est.summary["consistency"]["a1_r2"], 0)
        self.assertEqual(est.problem, "disagrees")
        # B1 drawn with J001's set alone excluded: its refs are not R3's
        directory, e, prefix = _Artifacts.fresh(self)
        shutil.rmtree(directory / str(j4_data.A002_RUN_ID))
        j001 = j4_data.refs_in(directory, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        loose3 = j4_data.a002_artifact(directory, ARCHIVE, j001)
        est = self.establish(directory, [e[0], e[1], {**{k: v for k, v in loose3.items()
                                                         if k != "questions_sha256_prefix"},
                                                      "rebuild_sha256_prefix": e[2]["rebuild_sha256_prefix"]}],
                             loose3["questions_sha256_prefix"])
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("B1", "R3")},
                         {"B1": "verified", "R3": "verified"})
        self.assertGreater(est.summary["consistency"]["b1_r3"], 0)
        self.assertEqual(est.problem, "disagrees")
        # a short or a wide chain: K2's and K3's counts
        for chain_rel, run_id, index, key in (("prereg/j2/excluded.jsonl", j4_data.A001_RUN_ID, 1, "k2"),
                                              ("prereg/j3/excluded.jsonl", j4_data.A002_RUN_ID, 2, "k3")):
            directory, e, prefix = _Artifacts.fresh(self)
            base = directory / str(run_id)
            refs = sorted(json.loads(line)["record_ref"] for line in (base / chain_rel).read_text().splitlines())
            short = j2labels.excluded_jsonl(refs[:-5])
            restamp(base, chain_rel, short)
            used = list(e)
            used[index] = {**e[index], "chain_sha256_prefix": sha256_hex(short)[:12]}
            est = self.establish(directory, used, prefix)
            with self.subTest(chain=key, case="short"):
                self.assertEqual(est.summary["consistency"][f"outside_{key}"], 5)
                self.assertEqual(est.summary["consistency"][f"{key}_union"], 5)
                self.assertEqual(est.problem, "disagrees")
            wide = j2labels.excluded_jsonl([*refs, "999999901", "999999902"])
            restamp(base, chain_rel, wide)
            used[index] = {**e[index], "chain_sha256_prefix": sha256_hex(wide)[:12]}
            est = self.establish(directory, used, prefix)
            with self.subTest(chain=key, case="wide"):
                self.assertEqual(est.summary["consistency"][f"{key}_union"], 2)
                self.assertEqual(est.summary["consistency"]["outside_k3"], 2 if key == "k2" else 0)
                self.assertEqual(est.summary["consistency"]["outside_k2"], 0)
                self.assertEqual((est.problem, len(est.excluded)), ("disagrees", 452))

    def test_the_labels_record_names_every_source(self) -> None:
        """B22's shape with a third entry, and an entry of kind split with the two counts (C4); counts and hashes
        only."""
        d = _Data.get()
        runs = d.draw.excluded_runs
        self.assertEqual(len(runs), 4)
        for row, entry, cycle, chain in zip(runs, d.used, j4labels.CYCLES, (None, "K2", "K3")):
            with self.subTest(cycle=cycle):
                self.assertEqual({k: row[k] for k in ("run_id", "artifact", "file", "sha256_prefix")},
                                 {k: entry[k] for k in ("run_id", "artifact", "file", "sha256_prefix")})
                self.assertEqual((row["refs"], row["outcome"], row["derived"], row["established_by"], row["stand_in"]),
                                 (150, "verified", None, "artifact", True))
                self.assertEqual(row["rebuild"], {"seed": entry["rebuild_seed"],
                                                  "sha256_prefix": entry["rebuild_sha256_prefix"],
                                                  "outcome": "verified", "refs": 150,
                                                  "sha256": d.est.shas[{"j001": "R1", "a001": "R2", "a002": "R3"}[cycle]]})
                self.assertEqual(row["requested"], {k: entry[k] for k in sorted(entry)})
                if chain is None:
                    self.assertIsNone(row["chain"])
                else:
                    self.assertEqual(row["chain"], {"file": entry["chain_file"],
                                                    "sha256_prefix": entry["chain_sha256_prefix"],
                                                    "outcome": "verified", "refs": 300 if chain == "K2" else 450})
        self.assertEqual(runs[3], {"kind": "split", "by_record_id": 0, "by_text": 1})
        self.assertEqual(j4labels.excluded_runs(d.est, d.used, d.used, False)[:3],
                         [dict(r, stand_in=False) for r in runs[:3]])
        exclusion = d.draw.record["exclusion"]
        self.assertEqual(sorted(exclusion), sorted(lab_j4.EXCLUSION_KEYS))
        self.assertEqual((exclusion["sources"], exclusion["cycles"], exclusion["consistency"],
                          exclusion["expected_excluded"], exclusion["split"]),
                         (d.est.summary["sources"], d.est.summary["cycles"], d.est.summary["consistency"], 450,
                          d.draw.split))
        self.assertEqual(exclusion["mechanisms"], {"artifact": "verified", "rebuild": "verified"})
        with self.assertRaises(j4labels.J4LabelsError):
            j4labels.extend_exclusion({"exclusion": {}}, d.est, {"by_text": 1})
        assert_no_ref(self, canonical_dumps(d.draw.record), d.draw.drawn | d.draw.excluded)


# --------------------------------------------------------------------------------------------------- the term check

class TermsCheckTests(unittest.TestCase):
    """"The terms", item 10, C4, C29, T1 and T7: the term artifact's check, every outcome; anything not verified is
    ``J4_TERMS_UNVERIFIED``; nothing of an unverified document is kept, and no term in any summary."""

    def fresh(self) -> tuple[Path, dict[str, Any], Path]:
        d = _Data.get()
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-terms-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(d.dir / "terms", tmp / "terms")
        entry = dict(d.term_entry)
        return tmp / "terms", entry, tmp / "terms" / str(entry["run_id"])

    def rewrite(self, base: Path, entry: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Change ``terms.json``'s document, restamp it in the manifest and return the entry that expects it."""
        doc = read_json(base / lab_jt.TERMS_FILE)
        change(doc)
        data = lab_jt.document_bytes(doc)
        restamp(base, lab_jt.TERMS_FILE, data)
        return {**entry, "sha256": sha256_hex(data)}

    def test_the_term_set_verifies(self) -> None:
        d = _Data.get()
        directory, entry, base = self.fresh()
        result = j4terms.check_terms(directory, entry)
        self.assertEqual((result.outcome, result.failed, result.problem), ("verified", None, None))
        self.assertEqual(result.data, (base / lab_jt.TERMS_FILE).read_bytes())
        self.assertEqual(result.terms, d.T)
        self.assertEqual(result.document, read_json(base / lab_jt.TERMS_FILE))
        self.assertEqual(result.p_d, lab_jt.p_d_hashes(result.document["terms"]))
        self.assertEqual(result.drafting, j4labels.drafting_from_files(
            (base / lab_jt.DRAFTING_REFS_FILE).read_bytes(), (base / lab_jt.DRAFTING_TEXT_FILE).read_bytes()))
        self.assertEqual(result.drafting.rows, result.document["drafting"]["rows"])
        summary = result.summary
        self.assertEqual(sorted(summary), sorted(j4terms.SUMMARY_KEYS))
        self.assertEqual((summary["outcome"], summary["sha256"], summary["expected_sha256"], summary["vocabulary_hash"],
                          summary["jt_code_hash"], summary["document_jt_code_hash"]),
                         ("verified", entry["sha256"], entry["sha256"], entry["vocabulary_hash"], JT_CODE_HASH,
                          JT_CODE_HASH))
        self.assertEqual(summary["archive_sha256"], sha256_hex(ARCHIVE))
        self.assertEqual(summary["term_counts"], result.document["term_counts"])
        assert_no_term(self, canonical_dumps(summary), d.all_terms)
        self.assertEqual(j4terms.OUTCOMES, (*j2labels.OUTCOMES, "terms absent"))
        self.assertEqual(j4terms.ENTRY_KEYS, ("run_id", "artifact", "file", "sha256", "vocabulary_hash", "cutoff"))
        self.assertEqual(sorted(d.term_entry), sorted(j4terms.ENTRY_KEYS))
        self.assertEqual(set(j4terms.CHECK_OUTCOMES.values()) | {"verified"}, set(j4terms.OUTCOMES))
        self.assertEqual(j4terms.CHECKS, tuple(j4terms.CHECK_OUTCOMES))

    def test_every_outcome(self) -> None:
        """A changed byte, a wrong cutoff, another window, other makes, other hand pack hashes, another code hash of
        ``lab/jt.py``, a drafting file that does not match, another P_D, an absent artifact or file, a manifest that
        disagrees, a document that is not a term set: each one fixed outcome and the check that failed."""

        def doc_change(change: Callable[[dict[str, Any]], None]) -> Callable[[Path, dict[str, Any]], dict[str, Any]]:
            return lambda base, entry: self.rewrite(base, entry, change)

        def file_change(rel: str, edit: Callable[[bytes], bytes], stamp: bool = True
                        ) -> Callable[[Path, dict[str, Any]], dict[str, Any]]:
            def apply(base: Path, entry: dict[str, Any]) -> dict[str, Any]:
                data = edit((base / rel).read_bytes())
                if stamp:
                    restamp(base, rel, data)
                else:
                    (base / rel).write_bytes(data)
                return entry
            return apply

        def remove(rel: str) -> Callable[[Path, dict[str, Any]], dict[str, Any]]:
            def apply(base: Path, entry: dict[str, Any]) -> dict[str, Any]:
                (base / rel).unlink()
                return entry
            return apply

        def entry_change(**changes: Any) -> Callable[[Path, dict[str, Any]], dict[str, Any]]:
            return lambda base, entry: {**entry, **changes}

        def set_key(path: tuple[str, ...], value: Any) -> Callable[[dict[str, Any]], None]:
            def change(doc: dict[str, Any]) -> None:
                obj = doc
                for key in path[:-1]:
                    obj = obj[key]
                obj[path[-1]] = value
            return change

        def first_term(doc: dict[str, Any]) -> None:
            entry = next(e for e in doc["terms"] if e["terms"])
            entry["terms"] = ["invented", *entry["terms"][1:]]

        reversed_refs = lambda data: b"".join(reversed(data.splitlines(keepends=True)))      # noqa: E731
        cases: dict[str, tuple[Callable[[Path, dict[str, Any]], dict[str, Any]], str, str]] = {
            "another run id": (entry_change(run_id=j4_data.TERMS_RUN_ID + 1), "artifact absent", "artifact"),
            "another file": (entry_change(file="prereg/jt/prereg.json"), "malformed", "document"),
            "no terms file": (remove(lab_jt.TERMS_FILE), "terms absent", "terms_file"),
            "a changed byte": (file_change(lab_jt.TERMS_FILE, lambda b: b.replace(b'"kind"', b'"kind" ', 1), False),
                               "sha256 mismatch", "terms_sha256"),
            "another sha256": (entry_change(sha256="0" * 64), "sha256 mismatch", "terms_sha256"),
            "a prefix of the sha256": (lambda base, entry: {**entry, "sha256": entry["sha256"][:12]},
                                       "sha256 mismatch", "terms_sha256"),
            "a changed byte the request expects": (
                lambda base, entry: {**file_change(lab_jt.TERMS_FILE, lambda b: b + b"\n", False)(base, entry),
                                     "sha256": sha256_hex((base / lab_jt.TERMS_FILE).read_bytes())},
                "manifest mismatch", "manifest"),
            "no drafting text file": (remove(lab_jt.DRAFTING_TEXT_FILE), "manifest mismatch", "manifest"),
            "a drafting refs file the manifest does not hold": (
                file_change(lab_jt.DRAFTING_REFS_FILE, lambda b: b + b"\n", False), "manifest mismatch", "manifest"),
            "a manifest that is not json": (file_change("prereg/prereg.json", lambda b: b"{not json", False),
                                            "malformed", "document"),
            "a drafting refs file that does not match the document": (
                file_change(lab_jt.DRAFTING_REFS_FILE, reversed_refs), "refs mismatch", "drafting_files"),
            "a drafting refs file with one ref fewer": (
                file_change(lab_jt.DRAFTING_REFS_FILE, lambda b: b"".join(b.splitlines(keepends=True)[1:])),
                "refs mismatch", "drafting_files"),
            "a drafting text file that does not match the document": (
                file_change(lab_jt.DRAFTING_TEXT_FILE, lambda b: b"".join(b.splitlines(keepends=True)[1:])),
                "refs mismatch", "drafting_files"),
            "a drafting text file that is not hashes": (
                file_change(lab_jt.DRAFTING_TEXT_FILE, lambda b: b'{"text": "x"}\n'), "refs mismatch",
                "drafting_files"),
            "another drafting row count": (doc_change(set_key(("drafting", "rows"), 1)), "refs mismatch",
                                           "drafting_files"),
            "another cutoff in the document": (doc_change(set_key(("cutoff",), "2023-02-01")), "labels mismatch",
                                               "cutoff"),
            "another cutoff in the request": (entry_change(cutoff="2024-01-01"), "labels mismatch", "cutoff"),
            "another window": (doc_change(set_key(("window",), ["2019-01-01", "2022-12-31"])), "labels mismatch",
                               "window"),
            "other makes": (doc_change(set_key(("makes",), ["FORD"])), "labels mismatch", "makes"),
            "other hand pack hashes": (doc_change(set_key(("hand", "hashes", "vocabulary_hash"), "0" * 64)),
                                       "labels mismatch", "hand_hashes"),
            "another hand pack": (doc_change(set_key(("hand", "pack"), "docs/collective/other")), "labels mismatch",
                                  "hand_hashes"),
            "another code hash of lab/jt.py in the document": (
                doc_change(set_key(("code", "jt_code_hash"), "0" * 64)), "labels mismatch", "jt_code_hash"),
            "another P_D vocabulary hash in the request": (entry_change(vocabulary_hash="0" * 64), "sha256 mismatch",
                                                          "p_d_hashes"),
            "other P_D hashes in the document": (doc_change(set_key(("p_d", "hashes", "config_hash"), "0" * 64)),
                                                 "sha256 mismatch", "p_d_hashes"),
            "a term changed": (doc_change(first_term), "sha256 mismatch", "p_d_hashes"),
            "not a term set": (doc_change(set_key(("kind",), "lab_jt_prereg")), "malformed", "document"),
            "another schema version": (doc_change(set_key(("schema_version",), 2)), "malformed", "document"),
            "an entry fewer": (doc_change(lambda doc: doc["terms"].pop()), "malformed", "document"),
            "entries out of order": (doc_change(lambda doc: doc["terms"].reverse()), "malformed", "document"),
            "a term that is not a string": (doc_change(lambda doc: doc["terms"][0].update(terms=[1])), "malformed",
                                            "document"),
        }
        d = _Data.get()
        for label, (change, outcome, failed) in cases.items():
            directory, entry, base = self.fresh()
            asked = change(base, entry)
            result = j4terms.check_terms(directory, asked)
            with self.subTest(case=label):
                self.assertEqual((result.outcome, result.failed), (outcome, failed))
                self.assertIn(result.outcome, j4terms.OUTCOMES)
                self.assertEqual(j4terms.CHECK_OUTCOMES[failed], outcome)
                self.assertEqual(result.problem, J4_TERMS_UNVERIFIED)
                self.assertEqual((result.data, result.document, result.terms, result.drafting, result.p_d),
                                 (None, None, None, None, None))
                self.assertEqual(sorted(result.summary), sorted(j4terms.SUMMARY_KEYS))
                for key in ("vocabulary_hash", "p_d", "archive_sha256", "drafting_rows", "term_counts",
                            "document_jt_code_hash"):
                    self.assertIsNone(result.summary[key])
                assert_no_term(self, canonical_dumps(result.summary), d.all_terms)
        # an absent directory, and lab/jt.py at this commit hashing to another value than the term set records
        directory, entry, base = self.fresh()
        self.assertEqual((j4terms.check_terms(None, entry).outcome, j4terms.check_terms(None, entry).failed),
                         ("artifact absent", "artifact"))
        self.assertEqual(j4terms.check_terms(directory / "nowhere", entry).outcome, "artifact absent")
        with mock.patch.object(lab_jt, "code_hash", lambda: "1" * 64):
            result = j4terms.check_terms(directory, entry)
        self.assertEqual((result.outcome, result.failed, result.problem),
                         ("labels mismatch", "jt_code_hash", J4_TERMS_UNVERIFIED))
        self.assertEqual(result.summary["jt_code_hash"], JT_CODE_HASH)
        self.assertEqual(j4terms.check_terms(directory, entry).outcome, "verified")
        self.assertIn("J4_TERMS_UNVERIFIED", dir(notes))
        self.assertIsNone(re.search(r"[0-9]", J4_TERMS_UNVERIFIED + J4_SPLIT_VIOLATED))


# --------------------------------------------------------------------------------------------------- the split

class SplitTests(unittest.TestCase):
    """Item 8 of "The data and the draw" (C4): the drafting set rebuilt and compared with the term run's, the removals
    by record id and by text before the draw, both tripwires after it, and the draw with its chain file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.d = _Data.get()
        rows, _ = j2labels.nhtsa_rows(ARCHIVE)
        cls.eligible, cls.counts = j2labels.eligible_records(PACK, rows)

    def test_the_rebuild_and_the_term_run_s_drafting_set(self) -> None:
        d = self.d
        rebuilt = j4labels.rebuild_drafting(ARCHIVE)
        self.assertEqual(rebuilt, d.terms.drafting)
        doc = d.terms.document["drafting"]
        self.assertEqual((rebuilt.rows, rebuilt.refs_sha256, rebuilt.text_sha256),
                         (doc["rows"], doc["refs_sha256"], doc["text_file_sha256"]))
        pooled = lab_jt.pooled_export(ARCHIVE)
        self.assertEqual(sorted(rebuilt.refs), sorted(json.loads(x)["record_ref"] for x in
                                                      lab_jt.drafting_refs_bytes(pooled.export).decode().splitlines()))
        self.assertEqual(rebuilt.text, frozenset(lab_jt.narrative_hashes(pooled.export)))
        self.assertTrue({*j4_data.STRADDLING_ODINOS, j4_data.REPEATED_ODINOS[0]} <= rebuilt.refs)
        self.assertFalse({j4_data.REPEATED_ODINOS[1], j4_data.BOUNDARY_ODINOS["2023-01-01"]} & rebuilt.refs)
        archive_sha = sha256_hex(ARCHIVE)
        self.assertIsNone(j4labels.compare_drafting(doc["rows"], doc["refs_sha256"], archive_sha, rebuilt, archive_sha))
        self.assertEqual(j4labels.compare_drafting(doc["rows"] + 1, doc["refs_sha256"], archive_sha, rebuilt,
                                                   archive_sha), J4_SPLIT_VIOLATED)
        self.assertEqual(j4labels.compare_drafting(doc["rows"], "0" * 64, archive_sha, rebuilt, archive_sha),
                         J4_SPLIT_VIOLATED)
        # archives that differ: both figures printed, the two sets used together, no stop
        self.assertIsNone(j4labels.compare_drafting(doc["rows"] + 1, "0" * 64, "1" * 64, rebuilt, archive_sha))
        self.assertEqual(j4labels.narrative_sha256("  The  Cabin filled "), hashlib.sha256(
            folded("  The  Cabin filled ").encode("utf-8")).hexdigest())
        self.assertIsNone(j4labels.narrative_sha256("   "))
        self.assertIsNone(j4labels.narrative_sha256(None))

    def test_a_straddling_complaint_and_a_repeated_narrative_are_removed_before_the_draw(self) -> None:
        """C4: an eligible record whose id is a drafting record id (the two straddling complaints) and every other one
        whose folded narrative is a drafting narrative (the repeated narrative's 2023 complaint, and the 2023-01-01
        boundary complaint, which repeats the window's boundary complaints); among the records the earlier cycles do
        not already exclude (the synthetic J001 and A001 draws hold three of them)."""
        d = self.d
        sets = (d.terms.drafting,)
        by_ref, by_text, already = j4labels.split_removals(self.eligible, set(), sets)
        self.assertEqual(by_ref, set(j4_data.STRADDLING_ODINOS))
        self.assertEqual(by_text, {j4_data.REPEATED_ODINOS[1], j4_data.BOUNDARY_ODINOS["2023-01-01"]})
        self.assertEqual(already, 0)
        self.assertTrue(by_ref | by_text <= {r["record_ref"] for r, _ in self.eligible})
        self.assertEqual(j4labels.split_removals(self.eligible, d.est.excluded, sets),
                         (set(), set(j4_data.SPLIT_BY_TEXT), 3))
        self.assertTrue({*j4_data.STRADDLING_ODINOS} <= d.j001 and j4_data.REPEATED_ODINOS[1] in d.a001)
        # every other eligible record stays
        self.assertEqual(j4labels.split_removals(self.eligible, set(), ()), (set(), set(), 0))
        # the two drafting sets are used together: a ref or a narrative of either removes the record
        ref = next(r["record_ref"] for r, _ in self.eligible if r["record_ref"] not in d.est.excluded | SPECIAL)
        other = j4labels.Drafting(rows=1, refs=frozenset({ref}), text=frozenset(), refs_sha256="x", text_sha256="y")
        self.assertEqual(j4labels.split_removals(self.eligible, d.est.excluded, (d.terms.drafting, other)),
                         ({ref}, set(j4_data.SPLIT_BY_TEXT), 3))

    def test_the_draw_over_the_eligible_list_less_the_450_and_the_removals(self) -> None:
        """Items 5 and 6: the fresh draw is the pinned ``draw`` over the eligible list less the 450 refs and the split's
        removals, disjoint from the three synthetic draws; the chain file holds 600 lines plus the removals."""
        d = self.d
        draw = d.draw
        self.assertIsNone(draw.problem)
        self.assertEqual((draw.by_record_id, draw.by_text), (set(), set(j4_data.SPLIT_BY_TEXT)))
        self.assertEqual(draw.excluded, d.j001 | d.a001 | d.a002 | set(j4_data.SPLIT_BY_TEXT))
        self.assertEqual((len(draw.excluded), len(draw.drawn)), (451, 150))
        self.assertEqual(draw.drawn & (d.j001 | d.a001 | d.a002), set())
        self.assertEqual(draw.drawn & (draw.by_record_id | draw.by_text), set())
        self.assertEqual(draw.drawn & d.terms.drafting.refs, set())
        self.assertEqual(draw.data, "".join(j2labels.label_lines(
            j2labels.draw(self.eligible, draw.excluded, 150, 4))).encode("utf-8"))
        self.assertEqual(draw.chain, j2labels.excluded_jsonl(draw.excluded | draw.drawn))
        self.assertEqual(draw.chain, j4labels.chain_bytes(draw.excluded, draw.drawn))
        self.assertEqual(len(draw.chain.splitlines()), j4labels.EXPECTED_CHAIN_LINES + 1)
        record = draw.record
        exclusion = record["exclusion"]
        self.assertEqual((record["kind"], record["n"], record["seed"], record["available"], record["records"]),
                         ("lab_j2_labels", 150, 4, 724, 150))
        self.assertEqual((exclusion["excluded"], exclusion["eligible_after_exclusion"], exclusion["overlap"],
                          exclusion["complaint_file_sha256"], exclusion["excluded_sha256"]),
                         (451, 273, 0, sha256_hex(ARCHIVE), sha256_hex(draw.chain)))
        self.assertEqual(draw.split, {
            "archive_sha256": sha256_hex(ARCHIVE), "term_archive_sha256": sha256_hex(ARCHIVE), "archives_equal": True,
            "term_rows": d.terms.drafting.rows, "term_refs_sha256": d.terms.drafting.refs_sha256,
            "rebuilt_rows": d.terms.drafting.rows, "rebuilt_refs_sha256": d.terms.drafting.refs_sha256,
            "rebuilt_text_sha256": d.terms.drafting.text_sha256, "by_record_id": 0, "by_text": 1,
            "already_excluded": 3, "tripwires": {"before_cutoff": 0, "in_drafting": 0}})
        self.assertEqual(sorted(draw.split), sorted(j4labels.SPLIT_KEYS))
        assert_no_ref(self, canonical_dumps(draw.split), draw.drawn | draw.excluded | set(d.terms.drafting.refs))
        # without the split's removals the same seed would have drawn otherwise only where a removal was drawable
        plain = j2labels.draw(self.eligible, d.est.excluded, 150, 4)
        self.assertEqual(len(plain), 150)

    def test_the_drafting_sets_of_two_archives_are_used_together(self) -> None:
        d = self.d
        other = j4labels.rebuild_drafting(OTHER_ARCHIVE)
        self.assertNotEqual(other.refs_sha256, d.terms.drafting.refs_sha256)
        draw = d.make_draw(d.est, term=other, term_archive=sha256_hex(OTHER_ARCHIVE))
        self.assertIsNone(draw.problem)
        self.assertEqual((draw.split["archives_equal"], draw.split["term_rows"], draw.split["rebuilt_rows"]),
                         (False, other.rows, d.terms.drafting.rows))
        self.assertEqual(draw.by_text, set(j4_data.SPLIT_BY_TEXT))
        # a term run drafting set holding an eligible ref the day's archive does not draft: removed by record id
        ref = next(r["record_ref"] for r, _ in self.eligible if r["record_ref"] not in d.est.excluded | SPECIAL)
        holding = j4labels.Drafting(rows=1, refs=frozenset({ref}), text=frozenset(), refs_sha256="0" * 64,
                                    text_sha256="0" * 64)
        draw = d.make_draw(d.est, term=holding, term_archive="1" * 64)
        self.assertEqual((draw.problem, draw.by_record_id, draw.split["by_record_id"]), (None, {ref}, 1))
        self.assertNotIn(ref, draw.drawn)
        self.assertEqual(len(draw.chain.splitlines()), j4labels.EXPECTED_CHAIN_LINES + 2)

    def test_a_drafting_set_that_differs_on_the_same_archive_stops_before_the_draw(self) -> None:
        d = self.d
        t = d.terms.drafting
        for label, term in (("another row count", j4labels.Drafting(rows=t.rows + 1, refs=t.refs, text=t.text,
                                                                     refs_sha256=t.refs_sha256,
                                                                     text_sha256=t.text_sha256)),
                            ("other record ids", j4labels.Drafting(rows=t.rows, refs=t.refs, text=t.text,
                                                                   refs_sha256="0" * 64, text_sha256=t.text_sha256))):
            draw = d.make_draw(d.est, term=term)
            with self.subTest(case=label):
                self.assertEqual((draw.problem, draw.data, draw.record, draw.chain), (J4_SPLIT_VIOLATED, None, None,
                                                                                        None))
                self.assertEqual(draw.split["by_record_id"], None)
        with mock.patch.object(lab_jt, "pooled_export", side_effect=lab_jt.JtError("x")):
            draw = d.make_draw(d.est)
        self.assertEqual((draw.problem, draw.data, draw.split["rebuilt_rows"]), (J4_SPLIT_VIOLATED, None, None))
        with self.assertRaises(j4labels.J4LabelsError):
            d.make_draw(j4labels.Established(sets={}, shas={}, summary={}, problem="unverified"))
        with self.assertRaises(j2labels.J2LabelsError):
            d.make_draw(d.est, n=274)

    def test_both_tripwires_stop_the_plan_job(self) -> None:
        """C4: a drawn record with a drafting id or narrative (here: the removal itself switched off, so the
        2023-01-01 boundary complaint can be drawn), and a drawn record received before the cutoff (here: an
        eligibility code that lets one through), each stop with ``J4_SPLIT_VIOLATED`` after the draw."""
        d = self.d
        with mock.patch.object(j4labels, "split_removals", return_value=(set(), set(), 0)):
            draw = d.make_draw(d.est, n=274)
        self.assertEqual(draw.split["tripwires"], {"before_cutoff": 0, "in_drafting": 1})
        self.assertIn(j4_data.BOUNDARY_ODINOS["2023-01-01"], draw.drawn)
        self.assertEqual(draw.problem, J4_SPLIT_VIOLATED)
        real = j2labels.build_labels

        def earlier(*args: Any, **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
            data, record = real(*args, **kwargs)
            first, rest = data.split(b"\n", 1)
            line = json.loads(first)
            line["record"]["received_date"] = "2022-12-31"
            return canonical_dumps(line).encode("utf-8") + b"\n" + rest, record

        with mock.patch.object(j2labels, "build_labels", earlier):
            draw = d.make_draw(d.est)
        self.assertEqual((draw.split["tripwires"], draw.problem),
                         ({"before_cutoff": 1, "in_drafting": 0}, J4_SPLIT_VIOLATED))
        # the tripwires themselves, line by line
        lines = [json.loads(x) for x in d.draw.data.decode().splitlines()]
        sets = (d.terms.drafting,)

        def labels_of(changed: list[dict[str, Any]]) -> bytes:
            return "".join(canonical_dumps(x) + "\n" for x in changed).encode("utf-8")

        self.assertEqual(j4labels.tripwires(d.draw.data, sets), {"before_cutoff": 0, "in_drafting": 0})
        cases = {
            "a record id of the drafting set": ({"record_ref": j4_data.REPEATED_ODINOS[0]}, (0, 1)),
            "a drafting narrative": ({"narrative": j4_data.REPEATED_NARRATIVE.upper()}, (0, 1)),
            "received on the day before the cutoff": ({"received_date": "2022-12-31"}, (1, 0)),
            "received on the cutoff day": ({"received_date": "2023-01-01"}, (0, 0)),
            "no received date": ({"received_date": None}, (1, 0)),
            "a received date of another form": ({"received_date": "20230510"}, (1, 0)),
        }
        for label, (change, (before, inside)) in cases.items():
            changed = [dict(lines[0], record={**lines[0]["record"], **change}), *lines[1:]]
            with self.subTest(case=label):
                self.assertEqual(j4labels.tripwires(labels_of(changed), sets),
                                 {"before_cutoff": before, "in_drafting": inside})


# --------------------------------------------------------------------------------------------------- the runner

class _Plan:
    """Hand-built J4 preregistrations (the plan job's work is stage 2's wiring): the synthetic draw of 40 records in 2
    parts, J001's questions, the comparators, the routing, ``prereg_doc`` and the term set's copy, per (base, arm)."""

    tmp: Path | None = None
    built: dict[tuple[str, str], Path] = {}

    @classmethod
    def get(cls, base: str = "negation", arm: str = "negation_terms") -> Path:
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-plan-"))
        if (base, arm) not in cls.built:
            cls.built[(base, arm)] = cls.build(cls.tmp / f"{base}-{arm}", base, arm)
        return cls.built[(base, arm)]

    @staticmethod
    def build(directory: Path, base: str, arm: str) -> Path:
        d = _Data.get()
        draw = d.make_draw(d.est, n=40)
        questions_bytes, questions = lab_j1.build_questions(PACK, draw.data, seed=4, parts=2)
        lines = lab_j4.comparator_lines(PACK, d.p_d, draw.data, lab_j1.read_questions(questions_bytes), d.T)
        routing = lab_j4.routing_doc(MODELS, ["fake-a", "fake-b"], PLACEHOLDER_BASE_URL, base)
        terms = lab_j4.terms_pin(sha256=d.terms.summary["sha256"], run_id=d.term_entry["run_id"],
                                 artifact=d.term_entry["artifact"], vocabulary_hash=d.term_entry["vocabulary_hash"],
                                 cutoff=d.term_entry["cutoff"], jt_code_hash=lab_jt.code_hash())
        doc = lab_j4.prereg_doc(pack=PACK, pack_ref=NHTSA_PACK, p_d=d.p_d, terms=terms, labels=draw.record,
                                questions=questions,
                                comparator_sha256={k: sha256_hex(lab_j4.jsonl_bytes(v)) for k, v in lines.items()},
                                routing=routing, base=base, seed=4, parts=2, bootstrap_b=1000, bootstrap_seed=4,
                                primary={"model": "fake-b", "arm": arm}, comparisons_before=21)
        out = directory / "prereg" / "j4"
        out.mkdir(parents=True)
        (out / "labels.jsonl").write_bytes(draw.data)
        (out / "questions.jsonl").write_bytes(questions_bytes)
        (out / "terms.json").write_bytes(d.terms.data)
        write_json(out / "prereg.json", doc)
        return out


class _Fake:
    """The lab's responder, and the shipped judge's answers for the two ``_terms`` tasks (behind an empty citation
    under branch E); ``by_task`` replaces a task's answers. Every request's task and payload is kept."""

    def __init__(self, by_task: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None) -> None:
        self.base = Responder(PACK)
        judge = lexical_judge(PACK, Canonicaliser(PACK))
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            ND: judge, ED: lambda payload: {"citation": "", **judge(payload)}}
        self.handlers.update(by_task or {})
        self.tasks: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        name = request["response_format"]["json_schema"]["name"]
        payload = request_payload(request)
        self.tasks.append(name)
        self.payloads.append(payload)
        if name in self.handlers:
            return self.handlers[name](payload)
        return self.base(request)


class PreregDocTests(unittest.TestCase):
    def test_the_preregistration_document(self) -> None:
        d = _Data.get()
        doc = read_json(_Plan.get() / "prereg.json")
        self.assertEqual(sorted(doc), sorted(lab_j4.PREREG_KEYS))
        self.assertEqual((doc["kind"], doc["experiment"], doc["base_task"], doc["boundary"], doc["data_label"],
                          doc["parts"], doc["seed"], doc["bootstrap_b"], doc["bootstrap_seed"], doc["withhold_share"]),
                         ("lab_j4_prereg", "J4", "negation", "site:lab", "public", 2, 4, 1000, 4, 0.01))
        self.assertEqual((doc["tasks"], doc["reading"], doc["arms"], doc["lifts"], doc["comparators"]),
                         (lab_j4.task_pins("negation", "negation_terms"), lab_j4.reading_pin("negation"),
                          lab_j4.arms_doc("negation"), lab_j4.lifts_doc("negation"), lab_j4.comparators_doc()))
        self.assertEqual((doc["primary"], doc["confirmation"], doc["headline_arm"], doc["headline_secondary"],
                          doc["other_reading_arm"]),
                         ({"model": "fake-b", "arm": "negation_terms"}, False, "negation_terms",
                          "negation_terms_mapped", "negation_terms_mapped"))
        self.assertEqual((doc["comparisons_before"], doc["comparisons_this_cycle"], doc["target_balanced_accuracy"]),
                         (21, 4, 0.86))
        self.assertEqual(doc["pack"], lab_j1.pack_pins(PACK, NHTSA_PACK))
        self.assertEqual(doc["drafted_pack"], {"hashes": d.p_d.hashes()})
        self.assertEqual(doc["terms"], {"file": "prereg/j4/terms.json", "sha256": d.terms.summary["sha256"],
                                        "run_id": j4_data.TERMS_RUN_ID, "artifact": j4_data.TERMS_ARTIFACT,
                                        "vocabulary_hash": d.p_d.vocabulary_hash, "cutoff": "2023-01-01",
                                        "jt_code_hash": JT_CODE_HASH})
        self.assertEqual(sorted(doc["exclusion"]), sorted(lab_j4.EXCLUSION_KEYS))
        self.assertEqual(doc["code_hash"], lab_j4.code_hash())
        self.assertEqual(doc["presence_drafted"]["rule"], lab_j4.PRESENCE_RULE)
        self.assertEqual([e["name"] for e in doc["endpoints"]], ["fake-a", "fake-b"])
        evidence = read_json(_Plan.get("evidence", "evidence") / "prereg.json")
        self.assertEqual((evidence["confirmation"], evidence["headline_secondary"], evidence["other_reading_arm"],
                          evidence["tasks"]["order"], evidence["reading"]["name"]),
                         (True, "evidence_terms", "evidence_grounded", [E, ED], "grounded"))
        kwargs = dict(pack=PACK, pack_ref=NHTSA_PACK, p_d=d.p_d, terms=doc["terms"], labels=d.draw.record,
                      questions={"seed": 4, "parts": 2, "records": 1, "questions": 2, "part_records": [1, 0],
                                 "negative_draw": "x", "sha256": "0" * 64},
                      comparator_sha256=dict.fromkeys(lab_j4.COMPARATORS, "0" * 64), routing={"endpoints": {}},
                      base="negation", seed=4, parts=2, bootstrap_b=1000, bootstrap_seed=4, comparisons_before=21)
        for primary, terms in ((("a-4b", "negation"), doc["terms"]), (("a-4b", "evidence"), doc["terms"]),
                               (("a-4b", "negation_terms"), {**doc["terms"], "extra": 1})):
            with self.assertRaises(lab_j4.J4Error):
                lab_j4.prereg_doc(**{**kwargs, "terms": terms}, primary={"model": primary[0], "arm": primary[1]})


class RunTests(unittest.TestCase):
    """``lab.j4 run`` in this process against the collective's fake server: two passes, task D first (task B first in
    the confirmation case), every pin refused before any call, a stop in each pass."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def server(self, fake: _Fake | None = None) -> tuple[FakeOpenAIServer, _Fake]:
        fake = fake or _Fake()
        server = FakeOpenAIServer("valid", responder=fake).start()
        self.addCleanup(server.stop)
        return server, fake

    def routing(self, base_url: str, base: str = "negation", change: Callable[[dict[str, Any]], None] | None = None,
                name: str = "routing") -> Path:
        doc = lab_j4.routing_doc(MODELS, ["fake-a"], base_url, base)
        if change is not None:
            change(doc)
        return write_json(self.tmp / f"{name}.json", doc)

    def argv(self, plan: Path, routing: Path, *, part: int = 1, run_id: str = "r1", budget: int = 600,
             endpoint: str = "fake-a", **files: Path) -> list[str]:
        return ["run", f"--prereg={files.get('prereg', plan / 'prereg.json')}",
                f"--labels={files.get('labels', plan / 'labels.jsonl')}",
                f"--questions={files.get('questions', plan / 'questions.jsonl')}",
                f"--terms={files.get('terms', plan / 'terms.json')}", f"--routing={routing}",
                f"--endpoint={endpoint}", f"--part={part}", f"--run-id={run_id}", f"--runs-dir={self.tmp / 'runs'}",
                f"--budget-seconds={budget}"]

    def run_j4(self, argv: list[str]) -> tuple[int, str, str, Path]:
        code, out, err = call_main(lab_j4, argv)
        return code, out, err, self.tmp / "runs" / "j4" / argv[8].split("=", 1)[1]

    def part(self, plan: Path, part: int = 1) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        qs = [q for q in lab_j1.read_questions((plan / "questions.jsonl").read_bytes()) if q["part"] == part]
        return qs, {r["record_ref"]: r for r, _ in lab_j1.parse_labels((plan / "labels.jsonl").read_bytes())}

    def test_two_passes_task_d_first(self) -> None:
        """Rules 3, 15 and 16: every question with task D, then every one with task B; D's payload the shipped one
        plus ``predicate_terms``; both under the base's schema and cap; every line passes the line check."""
        plan = _Plan.get()
        d = _Data.get()
        server, fake = self.server()
        code, out, err, run = self.run_j4(self.argv(plan, self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = read_json(run / "run.json"), read_lines(run / "verdicts.jsonl")
        qs, records = self.part(plan)
        n = len(qs)
        self.assertEqual(n, 40)
        self.assertEqual([x["task"] for x in lines], [ND] * n + [N] * n)
        self.assertEqual(fake.tasks, [ND] * n + [N] * n)
        self.assertEqual([x["question_id"] for x in lines], [q["question_id"] for q in qs] * 2)
        self.assertEqual(fake.payloads[:n], [lab_j4.payload(PACK, q, records[q["record_ref"]], d.T) for q in qs])
        self.assertEqual(fake.payloads[n:], [lab_j1.payload(PACK, q, records[q["record_ref"]]) for q in qs])
        self.assertTrue(any(p["question"]["predicate_terms"] for p in fake.payloads[:n]))
        for q, line in zip(qs * 2, lines):
            with self.subTest(question=q["question_id"], task=line["task"]):
                text = lab_j1.payload(PACK, q, records[q["record_ref"]])["record"]["text"]
                self.assertIsNone(lab_j4.line_problem(q, line, text, d.T))
                self.assertEqual(sorted(line), sorted(lab_j4.LINE_KEYS))
        self.assertEqual([x["terms_count"] for x in lines[:n]], [len(d.T.get(q["predicate"], [])) for q in qs])
        assert_no_term(self, (run / "verdicts.jsonl").read_text(encoding="utf-8"), d.all_terms)
        self.assertEqual((doc["kind"], doc["experiment"], doc["base_task"], doc["primary_arm"], doc["complete"],
                          doc["stopped"], doc["stopped_task"], doc["measurement"], doc["listed_fake"]),
                         ("lab_j4_run", "J4", "negation", "negation_terms", True, None, None, False, True))
        self.assertEqual((doc["pass_order"], doc["questions_planned"], doc["questions_done"],
                          doc["complete_by_task"], doc["questions_done_by_task"]),
                         ([ND, N], 80, 80, {ND: True, N: True}, {ND: 40, N: 40}))
        self.assertEqual((doc["pinned"]["terms_sha256"], doc["pinned"]["drafted_pack"], doc["pinned"]["reading"]),
                         (d.terms.summary["sha256"], d.p_d.hashes(), lab_j4.reading_pin("negation")))
        self.assertEqual(doc["verdicts_sha256"], hashlib.sha256((run / "verdicts.jsonl").read_bytes()).hexdigest())
        for key in ("failures_by_task", "latency_ms_by_task", "tokens_by_task"):
            self.assertEqual(sorted(doc[key]), sorted([ND, N]))
        rows = read_ledger(run / "ledger.jsonl")
        self.assertEqual([r["task"] for r in rows], [ND] * n + [N] * n)
        self.assertEqual((rows[0]["ref"], rows[n]["ref"]), ("j4-1-0-0000", "j4-1-1-0000"))
        self.assertEqual({(r["boundary"], r["data_label"]) for r in rows}, {("site:lab", "public")})
        chat = server.chat_requests
        self.assertEqual([(c["json"]["max_tokens"], c["json"]["temperature"]) for c in chat[::n]], [(256, 0), (256, 0)])
        first, second = chat[0]["json"], chat[n]["json"]
        self.assertIn(lab_j4.drafted_task("negation").instructions, first["messages"][0]["content"])
        self.assertIn(lab_j2.NEGATION_INSTRUCTIONS, second["messages"][0]["content"])
        self.assertNotIn(lab_j4.TERMS_SENTENCE, second["messages"][0]["content"])
        self.assertEqual(first["response_format"]["json_schema"]["schema"], judge_schema())
        self.assertEqual(second["response_format"]["json_schema"]["schema"], judge_schema())
        self.assertIn("negation_terms=40/40 negation=40/40", out)
        self.assertIn("complete=true", out)
        # the views score; the fake answers D as it answers B, so nothing here measures the terms
        views = {arm: lab_j4.arm_lines(lines, arm) for arm in lab_j4.ARMS["negation"]}
        self.assertEqual([x["verdict"] for x in views["negation_terms"]], [x["verdict"] for x in views["negation"]])

    def test_the_confirmation_case_runs_task_b_first(self) -> None:
        """C1: base ``evidence``, primary ``evidence``: task B, then task D; S_E and 512 tokens for both."""
        plan = _Plan.get("evidence", "evidence")
        server, fake = self.server()
        code, out, err, run = self.run_j4(self.argv(plan, self.routing(server.base_url, "evidence")))
        self.assertEqual(code, 0, err)
        doc, lines = read_json(run / "run.json"), read_lines(run / "verdicts.jsonl")
        self.assertEqual([x["task"] for x in lines], [E] * 40 + [ED] * 40)
        self.assertEqual((doc["pass_order"], doc["complete_by_task"], doc["primary_arm"]),
                         ([E, ED], {E: True, ED: True}, "evidence"))
        self.assertEqual({(x["citation"], x["first_key"], x["grounded"], x["mapped_verdict"]) for x in lines},
                         {("", "citation", False, None)})
        self.assertTrue(all("predicate_terms" in p["question"] for p in fake.payloads[40:]))
        self.assertFalse(any("predicate_terms" in p["question"] for p in fake.payloads[:40]))
        self.assertEqual({c["json"]["max_tokens"] for c in server.chat_requests}, {512})
        self.assertEqual({c["json"]["response_format"]["json_schema"]["name"] for c in server.chat_requests}, {E, ED})
        self.assertIn("evidence=40/40 evidence_terms=40/40", out)

    def stopped_run(self, ticks: list[float]) -> tuple[int, dict[str, Any], list[dict[str, Any]], FakeOpenAIServer]:
        server, _ = self.server()
        clock = iter(ticks)
        with mock.patch.object(lab_j4, "_clock", lambda: next(clock)), mock.patch.object(lab_j1.signal, "setitimer"):
            code, _, err, run = self.run_j4(self.argv(_Plan.get(), self.routing(server.base_url), budget=1))
        return code, read_json(run / "run.json"), read_lines(run / "verdicts.jsonl"), server

    def test_a_stop_in_the_second_pass_leaves_the_first_complete(self) -> None:
        """Rule 15: the budget ends during task B's pass: task D's set (P's) is complete for this part, B's is not."""
        code, doc, lines, server = self.stopped_run([0.0] * 41 + [2.0] * 3)
        self.assertEqual(code, 1)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "budget", N))
        self.assertEqual((doc["complete_by_task"], doc["questions_done_by_task"]), ({ND: True, N: False},
                                                                                    {ND: 40, N: 0}))
        self.assertEqual([x["task"] for x in lines], [ND] * 40)
        self.assertEqual(len(server.chat_requests), 40)

    def test_a_stop_in_the_first_pass_ends_both(self) -> None:
        code, doc, lines, server = self.stopped_run([0.0, 0.0, 0.0, 2.0, 2.0])
        self.assertEqual(code, 1)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "budget", ND))
        self.assertEqual((doc["complete_by_task"], doc["questions_done_by_task"], len(lines)),
                         ({ND: False, N: False}, {ND: 2, N: 0}, 2))

    def test_it_stops_when_the_server_stays_down(self) -> None:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code, _, err, run = self.run_j4(self.argv(_Plan.get(), self.routing(f"http://127.0.0.1:{port}/v1")))
        self.assertEqual(code, 1, err)
        doc, lines = read_json(run / "run.json"), read_lines(run / "verdicts.jsonl")
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"], doc["complete_by_task"]),
                         (False, "server_down", ND, {ND: False, N: False}))
        self.assertEqual({(x["task"], x["error_kind"], x["scored"]) for x in lines}, {(ND, "network", False)})

    def test_every_pin_is_checked_before_any_call(self) -> None:
        """The pins: the term set's sha256, P_D rebuilt from it, both tasks, the reading, the arms, the lifts, the
        routing of both tasks without escalation, the labels, the questions, the pack and the code hash, each refused
        with exit 2 and its own message before a call is made."""
        plan = _Plan.get()
        server, fake = self.server()
        good = self.routing(server.base_url)
        written: list[Path] = []

        def changed(name: str, edit: Callable[[bytes], bytes]) -> Path:
            target = self.tmp / name
            target.write_bytes(edit((plan / name).read_bytes()))
            written.append(target)
            return target

        def prereg_with(name: str, change: Callable[[dict[str, Any]], None]) -> Path:
            doc = read_json(plan / "prereg.json")
            change(doc)
            written.append(write_json(self.tmp / f"prereg-{name}.json", doc))
            return written[-1]

        def routing_with(name: str, change: Callable[[dict[str, Any]], None]) -> Path:
            written.append(self.routing(server.base_url, change=change, name=f"routing-{name}"))
            return written[-1]

        def escalating(task: str) -> Callable[[dict[str, Any]], None]:
            def change(doc: dict[str, Any]) -> None:
                doc["endpoints"]["fake-b"] = lab_j1.endpoint(MODELS["fake-b"], server.base_url)
                doc["routes"][task]["escalate_to"] = "fake-b"
            return change

        terms_doc = read_json(plan / "terms.json")
        other_terms = dict(terms_doc, terms=[dict(e, terms=["invented" + "abcdefghijklmnopqrstuvwxyz"[i]])
                                             if e["terms"] else e for i, e in enumerate(terms_doc["terms"])])
        other_bytes = lab_jt.document_bytes(other_terms)
        (self.tmp / "terms-other.json").write_bytes(other_bytes)
        (self.tmp / "terms-broken.json").write_bytes(b"{not json")
        differs = "{} differs from the preregistration"
        escalation = "the routing must route both judge tasks to the endpoint without escalation"
        cases = [
            ("labels", {"labels": changed("labels.jsonl", lambda b: b.replace(b"stalled", b"stopped", 1))},
             differs.format("the labels' sha256")),
            ("questions", {"questions": changed("questions.jsonl", lambda b: b + b"\n")},
             differs.format("the questions' sha256")),
            ("terms", {"terms": changed("terms.json", lambda b: b + b"\n")}, differs.format("the term set's sha256")),
            ("terms pinned again, P_D", {"terms": self.tmp / "terms-other.json",
                                         "prereg": prereg_with("terms-sha", lambda p: p["terms"].update(
                                             sha256=sha256_hex(other_bytes)))},
             differs.format("the drafted pack's hashes")),
            ("terms that do not rebuild P_D", {"terms": self.tmp / "terms-broken.json",
                                               "prereg": prereg_with("terms-broken", lambda p: p["terms"].update(
                                                   sha256=sha256_hex(b"{not json")))},
             "the term set does not rebuild the drafted pack"),
            ("drafted pack", {"prereg": prereg_with("pd", lambda p: p["drafted_pack"]["hashes"].update(
                vocabulary_hash="0" * 64))}, differs.format("the drafted pack's hashes")),
            ("pack", {"prereg": prereg_with("pack", lambda p: p["pack"].update(vocabulary_hash="0" * 64))},
             differs.format("the pack's vocabulary hash")),
            ("code", {"prereg": prereg_with("code", lambda p: p.update(code_hash="0" * 64))},
             differs.format("the code hash")),
            ("D's instructions", {"prereg": prereg_with("d-text", lambda p: p["tasks"]["D"].update(
                instructions_sha256="0" * 64))}, differs.format("the judge tasks")),
            ("D's max_tokens", {"prereg": prereg_with("d-tokens", lambda p: p["tasks"]["D"].update(max_tokens=512))},
             differs.format("the judge tasks")),
            ("B's schema", {"prereg": prereg_with("b-schema", lambda p: p["tasks"]["B"].update(
                schema_sha256="0" * 64))}, differs.format("the judge tasks")),
            ("pass order", {"prereg": prereg_with("order", lambda p: p["tasks"].update(order=[N, ND]))},
             differs.format("the judge tasks")),
            ("reading", {"prereg": prereg_with("reading", lambda p: p["reading"]["pin"]["rule"]["describes_predicate"]
                                               .update(unclear="yes"))}, differs.format("the reading")),
            ("arms", {"prereg": prereg_with("arms", lambda p: p["arms"]["negation_terms_mapped"].update(
                reading="shipped"))}, differs.format("the arms")),
            ("lifts", {"prereg": prereg_with("lifts", lambda p: p["lifts"]["D_minus_B"].update(minus="negation_mapped"))},
             differs.format("the lifts")),
            ("kind", {"prereg": prereg_with("kind", lambda p: p.update(kind="lab_j3_prereg"))},
             "the preregistration is not a J4 prereg.json"),
            ("base task", {"prereg": prereg_with("base", lambda p: p.update(base_task="rationale"))},
             "the preregistration is not a J4 prereg.json"),
            ("primary arm", {"prereg": prereg_with("primary", lambda p: p["primary"].update(arm="negation"))},
             "the preregistered primary arm is not one of the branch's"),
            ("transport", {"routing": routing_with("transport", lambda r: r["endpoints"]["fake-a"].update(
                transport_schema="reduced"))}, differs.format("the endpoint's pins")),
            ("D's route missing", {"routing": routing_with("no-d", lambda r: r["routes"].pop(ND))},
             f"routing: $.routes.{ND}: no route"),
            ("B's route missing", {"routing": routing_with("no-b", lambda r: r["routes"].pop(N))},
             f"routing: $.routes.{N}: no route"),
            ("D's escalation", {"routing": routing_with("esc-d", escalating(ND))}, escalation),
            ("B's escalation", {"routing": routing_with("esc-b", escalating(N))}, escalation),
            ("part", {"part": 3}, "--part is not one of the preregistered parts"),
            ("endpoint", {"endpoint": "fake-slow"}, "the endpoint is not in the preregistration or the routing"),
            ("budget", {"budget": 0}, "--budget-seconds must be >= 1"),
        ]
        self.assertEqual(len(written), len(set(written)), "two cases share a file")
        for label, change, _ in cases:
            if "escalation" in label:
                load_routing(change["routing"], tasks=lab_j4.tasks("negation"), check_env=False)
        for i, (label, change, message) in enumerate(cases):
            routing = change.pop("routing", good)
            argv = self.argv(plan, routing, run_id=f"pin-{i}", **change)
            with self.subTest(pin=label):
                code, _, err, run = self.run_j4(argv)
                self.assertEqual(code, 2, label)
                self.assertTrue(err.startswith("error: "), err)
                self.assertIn(message, err)
                self.assertFalse(run.exists())
        self.assertEqual(server.chat_requests, [])
        with mock.patch.object(lab_j4, "code_hash", lambda: "1" * 64):
            code, _, err, run = self.run_j4(self.argv(plan, good, run_id="pin-code-now"))
        self.assertEqual((code, run.exists()), (2, False))
        self.assertIn(differs.format("the code hash"), err)
        code, _, err, run = self.run_j4(self.argv(plan, good, run_id="pin-good"))
        self.assertEqual((code, run.exists()), (0, True), err)
        self.assertEqual(len(server.chat_requests), 80)

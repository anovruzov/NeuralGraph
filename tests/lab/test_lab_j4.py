"""Accuracy test A003, build 2, stage 1 (``lab.j4``, ``lab.j4labels`` and ``lab.j4terms``, the core of the lab's ``j4``
experiment; ``docs/collective/A003/CHOICE-A003.md`` as amended C1 to C32 and T1 to T11): task D's text under both
branches (C32), the payload with ``predicate_terms`` and nothing else, the schema and the cap, the arms, the lifts and
the readings as the pinned functions, the verdict lines and the line check, every cell of C19's verdict word with the
word under lever 5's text alone (C6), the confirmation case (C1), the drafted term-presence judge (C5), the three
layouts' checks pinned equal to the pinned functions on the earlier layouts, the eight sources and the stop rules (C20),
A002's questions prefix (C3), the term artifact's check with every outcome (C29), the split's removals and tripwires
(C4), the draw disjoint from the three synthetic draws with its chain file, ``lab/jt.py``'s frozen code hash (T7), and
the runner through the repo's fake model server (two passes, task D first; task B first in the confirmation case).

Build 2, stage 2 (the wiring, C27): the request block's errors and the template, ``plan_outputs`` on a j4, a j3, a j2
and another plan, the template's 5 units in 5 shards, the unit adapter (the status with ``J4_TERMS_STOPPED``, the
required task, ``--terms``, the routing of the branch's two tasks, a unit run on the fake provider), both fake handlers,
the warm-up's two tasks with the probe task B and the terms in task D's payloads (C12), the two new workflow steps,
``lab.prereg``'s J4 step and its fixed lines, the aggregate's verdict words and its manifest cross-check, the notes
free of digits, the data flags, and the small and the template dry runs with every number traced and no ref or term
anywhere a reader looks.

Nothing here measures a model: every server is the collective's fake, every archive and artifact is
``tests/lab/j4_data.py``'s (every value invented), no earlier run's or term run's artifact is downloaded and no NHTSA
host is reached. The synthetic terms are compared, never printed.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime
import hashlib
import io
import itertools
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from lab import aggregate as lab_aggregate
from lab import dryrun as lab_dryrun
from lab import goldlabels as lab_goldlabels
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
from lab import plan as lab_plan
from lab import prereg as lab_prereg
from lab import request as lab_request
from lab import summary as lab_summary
from lab import units
from lab import warmup as lab_warmup
from lab.goldlabels import NHTSA_PACK
from lab.manifest import load_manifest
from lab.notes import J4_SPLIT_VIOLATED, J4_TERMS_UNVERIFIED
from lab.prereg import PLACEHOLDER_BASE_URL
from lab.request import RequestError
from lab.responder import Responder, ResponderError
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
from tests.lab.helpers import (LAB_MANIFEST, MANIFEST_TEST, PLUMBING_MIN, ROOT, TEMPLATES, DryTree, call_main,
                               check_sources, kill_mentioning, run_plan, write_json)
from tests.lab.test_lab_jt import without_runner_values

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
    for holder in (_Data, _Artifacts, _Plan, _Prereg4, _DryRun4):
        if holder.tmp is not None:
            kill_mentioning(str(holder.tmp))
            shutil.rmtree(holder.tmp, True)


# --------------------------------------------------------------------------------------------------- the rule

def terms_blockquote() -> str:
    """"The terms", item 8's blockquote in CHOICE-A003.md: the sentence task D's instructions add, verbatim."""
    text = CHOICE.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(text) if line.rstrip().endswith("words, fixed now:")]
    assert len(starts) == 1 and text[starts[0] - 1].rstrip().endswith("in these")
    start = starts[0]
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
        # rule 12: the branch's arms times the models (4 with the rule's one model; 8 with a dry run's two)
        self.assertEqual([lab_j4.comparisons_this_cycle(base, k) for base in lab_j4.BASES for k in (1, 2)],
                         [4, 8, 4, 8])
        with self.assertRaises(lab_j4.J4Error):
            lab_j4.comparisons_this_cycle("rationale", 1)
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

    def test_the_earlier_cycle_s_own_figures(self) -> None:
        """Rule 14: B and Br are reported beside the earlier cycle's own figures for the same arms, as CHOICE-A003.md
        cites them ("What the evidence says" and the amendment before any build): A001 run 1's arms C and D (lab run
        38114522160) under branch N, A002 run 1's arms E and Eg (lab run 38439612256) under branch E."""
        self.assertEqual(lab_j4.EARLIER_ARMS, {
            "negation": {"cycle": "A001", "run_id": 38114522160,
                         "B": {"arm": "negation", "balanced_accuracy": 0.577, "ci_low": 0.530, "ci_high": 0.623},
                         "Br": {"arm": "negation_mapped", "balanced_accuracy": 0.653, "ci_low": 0.610,
                                "ci_high": 0.697}},
            "evidence": {"cycle": "A002", "run_id": 38439612256,
                         "B": {"arm": "evidence", "balanced_accuracy": 0.540, "ci_low": 0.503, "ci_high": 0.577},
                         "Br": {"arm": "evidence_grounded", "balanced_accuracy": 0.553, "ci_low": 0.517,
                                "ci_high": 0.590}}})
        text = " ".join(CHOICE.read_text(encoding="utf-8").split())         # the rule's lines, re-wrapped
        for figure in ("0.577 [0.530, 0.623]", "0.653 [0.610, 0.697]", "0.540 [0.503, 0.577]", "0.553 [0.517, 0.590]",
                       "38114522160", "38439612256",
                       "(arm C read as answered, 0.577; arm D through the mapping, 0.653)"):
            with self.subTest(figure=figure):
                self.assertIn(figure, text)
        for base in lab_j4.BASES:
            earlier = lab_j4.EARLIER_ARMS[base]
            self.assertEqual((earlier["B"]["arm"], earlier["Br"]["arm"]),
                             (lab_j4.arm_of(base, "B"), lab_j4.arm_of(base, "Br")))

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
                self.assertEqual(test["passed"], all(test["clauses"].values()))
                if want == "pass_provisional":
                    self.assertTrue(test["passed"])
                elif want in ("partial", "failure"):
                    self.assertFalse(test["passed"])
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
        base = directory / str(j4_data.A002_RUN_ID)
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
        # C3: B1's outcome is kept with the questions prefix it was checked against (the stand-in's here)
        self.assertEqual(est.summary["sources"], {s: {"outcome": "verified", "refs": n,
                                                      **({"questions_sha256_prefix": prefix} if s == "B1" else {})}
                                                  for s, n in (("J1", 150), ("R1", 150), ("A1", 150), ("R2", 150),
                                                               ("K2", 300), ("B1", 150), ("R3", 150), ("K3", 450))})
        self.assertNotEqual(prefix, j4labels.A002_QUESTIONS_SHA256_PREFIX)
        # without a stand-in's prefix B1 is checked against A002's pinned one, and the summary says so
        pinned = j4labels.establish(directory, entries[0], entries[1], entries[2], lambda: ARCHIVE)
        self.assertEqual(pinned.summary["sources"]["B1"], {"outcome": "sha256 mismatch", "refs": None,
                                                           "questions_sha256_prefix": "84486fa38568"})
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
        theirs = j4_data.refs_in(other, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        ours = j4_data.refs_in(_Artifacts.template(), j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        k3 = j4labels.earlier_chain_refs(directory, j4labels.chain_entry(e[2]))[0]
        self.assertGreater(len(theirs - k3), 100)                 # the fixed synthetic ids aside, other records
        self.assertEqual(est.summary["consistency"]["j1_r1"], len(theirs ^ ours))
        self.assertEqual(est.summary["consistency"]["outside_k2"], len(theirs - k3))
        self.assertEqual(est.summary["consistency"]["outside_k3"], len(theirs - k3))
        self.assertEqual((est.problem, len(est.excluded)), ("disagrees", 450 + len(theirs - k3)))
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
        # each count alone above 0 stops the plan job: every other count is 0 or not computed
        for key in j4labels.CONSISTENCY:
            used, prefix, want = self.alone(key)
            directory = used.pop()
            est = self.establish(directory, used, prefix)
            with self.subTest(alone=key):
                self.assertEqual({k: v for k, v in est.summary["consistency"].items() if v}, {key: want})
                self.assertEqual((est.problem, est.summary["problem"]), ("disagrees", "disagrees"))
                self.assertTrue(all(c["established"] for c in est.summary["cycles"].values()))

    def alone(self, key: str) -> tuple[list[Any], str, int]:
        """Artifacts and entries under which the consistency count ``key`` is the only one above 0: (the three
        entries followed by the artifacts' directory, the A002 questions prefix, the count expected)."""
        directory, e, prefix = _Artifacts.fresh(self)
        no_chain = {"chain_sha256_prefix": "0" * 12}
        j001 = j4_data.refs_in(directory, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
        a001 = j4_data.refs_in(directory, j4_data.A001_RUN_ID, "prereg/j2/labels.jsonl")
        a002 = j4_data.refs_in(directory, j4_data.A002_RUN_ID, j4_data.J3_LABELS_FILE)

        def chain(run_id: int, rel: str, refs: list[str], index: int) -> dict[str, Any]:
            data = j2labels.excluded_jsonl(refs)
            restamp(directory / str(run_id), rel, data)
            return {**e[index], "chain_sha256_prefix": sha256_hex(data)[:12]}

        if key == "j1_r1":                     # J001's artifact from another archive; neither chain verifies
            other = Path(tempfile.mkdtemp(prefix="lab-j4-other-"))
            self.addCleanup(shutil.rmtree, other, True)
            theirs = j4_data.stand_in(other, OTHER_ARCHIVE)[0]
            shutil.rmtree(directory / str(j4_data.J001_RUN_ID))
            shutil.copytree(other / str(j4_data.J001_RUN_ID), directory / str(j4_data.J001_RUN_ID))
            refs = j4_data.refs_in(other, j4_data.J001_RUN_ID, "prereg/j1/labels.jsonl")
            used = [{**theirs, "rebuild_sha256_prefix": e[0]["rebuild_sha256_prefix"]}, {**e[1], **no_chain},
                    {**e[2], **no_chain}]
            return [*used, directory], prefix, len(refs ^ j001)
        if key == "a1_r2":                     # A001's artifact drawn without J001's set; neither chain verifies
            shutil.rmtree(directory / str(j4_data.A001_RUN_ID))
            loose = j3_data.a001_artifact(directory, ARCHIVE, set())
            refs = j4_data.refs_in(directory, j4_data.A001_RUN_ID, "prereg/j2/labels.jsonl")
            used = [e[0], {**loose, "rebuild_sha256_prefix": e[1]["rebuild_sha256_prefix"], **no_chain},
                    {**e[2], **no_chain}]
            return [*used, directory], prefix, len(refs ^ a001)
        if key == "b1_r3":                     # A002's artifact drawn with J001's set alone excluded; K3 broken
            shutil.rmtree(directory / str(j4_data.A002_RUN_ID))
            loose3 = j4_data.a002_artifact(directory, ARCHIVE, j001)
            refs = j4_data.refs_in(directory, j4_data.A002_RUN_ID, j4_data.J3_LABELS_FILE)
            third = {k: v for k, v in loose3.items() if k != "questions_sha256_prefix"}
            used = [e[0], e[1], {**third, "rebuild_sha256_prefix": e[2]["rebuild_sha256_prefix"], **no_chain}]
            return [*used, directory], loose3["questions_sha256_prefix"], len(refs ^ a002)
        no_j001 = broken(e[0], "sha256_prefix", "rebuild_sha256_prefix")
        short_of_a001 = sorted(j001 | a001)
        five = sorted(a001)[:5]
        if key == "outside_k2":                # J001 by K2, which lacks five of A001's refs; K3 broken
            k2 = chain(j4_data.A001_RUN_ID, "prereg/j2/excluded.jsonl",
                       [r for r in short_of_a001 if r not in five], 1)
            return [no_j001, k2, {**e[2], **no_chain}, directory], prefix, 5
        if key == "outside_k3":                # J001 by K3, which lacks five of A001's refs; K2 broken
            k3 = chain(j4_data.A002_RUN_ID, j4_data.J3_CHAIN_FILE,
                       [r for r in sorted(j001 | a001 | a002) if r not in five], 2)
            return [no_j001, {**e[1], **no_chain}, k3, directory], prefix, 5
        if key == "k2_union":                  # K2 two refs wider than J001's and A001's sets; K3 broken
            k2 = chain(j4_data.A001_RUN_ID, "prereg/j2/excluded.jsonl", [*short_of_a001, "999999901", "999999902"], 1)
            return [e[0], k2, {**e[2], **no_chain}, directory], prefix, 2
        assert key == "k3_union", key          # K3 one ref wider than the three sets, K2 consistent with them
        k3 = chain(j4_data.A002_RUN_ID, j4_data.J3_CHAIN_FILE, [*sorted(j001 | a001 | a002), "999999901"], 2)
        return [e[0], e[1], k3, directory], prefix, 1

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
            # C4: the same number of lines and distinct hashes, one hash replaced, the manifest restamped: only the
            # file's sha256 against the document's text_file_sha256 tells it apart
            "a drafting text file with one hash replaced": (
                file_change(lab_jt.DRAFTING_TEXT_FILE,
                            lambda b: b.replace(json.loads(b.splitlines()[0])["sha256"].encode(),
                                                hashlib.sha256(b"x").hexdigest().encode(), 1)),
                "refs mismatch", "drafting_files"),
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
        # the replaced hash is no drafting narrative's, so the text file keeps its line count and distinct hashes
        self.assertNotIn(hashlib.sha256(b"x").hexdigest(), d.terms.drafting.text)
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
        self.assertEqual(result.summary["jt_code_hash"], "1" * 64)          # this commit's, printed beside
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
        # rule 12: the branch's four arms times the routing's two models (the template's one model gives 4)
        self.assertEqual((doc["comparisons_before"], doc["comparisons_this_cycle"], doc["target_balanced_accuracy"]),
                         (21, 8, 0.86))
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


# =================================================================================================== stage 2: wiring

LABELS = {"source": "nhtsa", "pack": NHTSA_PACK, "n": 40, "seed": 4}
TEMPLATE = TEMPLATES / "accuracy-003.json"


def template() -> dict[str, Any]:
    return read_json(TEMPLATE)


def j4_block(**changes: Any) -> dict[str, Any]:
    """A J4 block of 40 records in 2 parts with the template's three exclude entries and term entry (the real runs';
    the dry runs' stand-ins replace them)."""
    t = template()["experiments"]["j4"]
    block = {"minutes": 5, "labels": dict(LABELS), "parts": 2, "seed": 4, "bootstrap_b": 1000, "bootstrap_seed": 4,
             "base_task": "negation", "exclude": t["exclude"], "terms": t["terms"],
             "primary": {"model": "fake-b", "arm": "negation_terms"}, "comparisons_before": 21}
    block.update(changes)
    return block


def small_request(**changes: Any) -> dict[str, Any]:
    """A J4 request of two gguf models for the dry run on the fake provider (the template's shape, 40 records in 2
    parts); ``--j4-data`` stands in for its ``exclude`` and ``terms`` entries."""
    block = j4_block(primary={"model": "a-4b", "arm": "negation_terms"})
    block.update(changes)
    return {"schema_version": 1, "purpose": "Accuracy test A003's judging run, plumbing on two models and forty records.",
            "provider": "llama-server", "models": ["a-1p5b", "a-4b"], "job_minutes": 60, "max_parallel": 4,
            "retention_days": 30, "experiments": {"j4": block}}


def drafting_refs(data_dir: Path) -> set[str]:
    """The synthetic term run's drafting record ids (its ``drafting_refs.jsonl``)."""
    path = data_dir / "terms" / str(j4_data.TERMS_RUN_ID) / "prereg" / "jt" / "drafting_refs.jsonl"
    return {line["record_ref"] for line in read_lines(path)}


def payload_texts(directory: Path) -> dict[str, str]:
    """Each drawn record's payload text (``lab.j1.payload``) by record ref."""
    records = {r["record_ref"]: r for r, _ in lab_j1.parse_labels((directory / "labels.jsonl").read_bytes())}
    out: dict[str, str] = {}
    for q in lab_j1.read_questions((directory / "questions.jsonl").read_bytes()):
        if q["record_ref"] not in out:
            out[q["record_ref"]] = lab_j1.payload(PACK, q, records[q["record_ref"]])["record"]["text"]
    return out


class _Prereg4:
    """A J4 plan of two fake models (40 records in 2 parts) planned with the template's entries and preregistered in
    this process with the test data standing in for the three earlier runs and the term run, as ``lab.dryrun
    --j4-data`` hands them to ``lab.prereg``."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Prereg4]":
        if cls.tmp is None:
            d = _Data.get()
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-prereg-"))
            request = json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))
            request["experiments"] = {"j4": j4_block()}
            cls.request_path = write_json(cls.tmp / "j4-test.json", request)
            code, _, err = run_plan(["--request", str(cls.request_path), "--manifest", str(MANIFEST_TEST),
                                     "--out", str(cls.tmp / "plan")])
            assert code == 0, err
            cls.plan_path = cls.tmp / "plan" / "plan.json"
            cls.plan = read_json(cls.plan_path)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cls.manifest = lab_prereg.preregister(cls.plan_path, nhtsa_fetch=lambda: ARCHIVE,
                                                      earlier_plans=d.dir / "earlier", exclude_stand_in=d.entries,
                                                      terms_plans=d.dir / "terms", terms_stand_in=d.term_entry)
            cls.stdout = out.getvalue()
            cls.dir = cls.tmp / "plan" / "prereg" / "j4"
            cls.prereg = lab_prereg.load_prereg(cls.plan_path)
        return cls


class _DryRun4:
    """``lab.dryrun`` with ``--j4-data`` (every source and the term set verify on the stand-ins), the shards on the
    fake provider, made once per request."""

    made: dict[str, "_DryRun4"] = {}
    tmp: Path | None = None

    def __init__(self, request: Path, name: str) -> None:
        assert _DryRun4.tmp is not None
        root = _DryRun4.tmp / name
        # the negated variant (the judging kind's dry runs): the drafted lexical judge and the term-presence judge
        # answer apart on it, so a figure of one shown as the other's shows
        self.data = j4_data.write_data(root / "data", negated=True)
        self.out = root / "D"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.code = lab_dryrun.main(["--request", str(request), "--out", str(self.out), "--manifest",
                                         str(LAB_MANIFEST), "--j4-data", str(self.data)])
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        self.plan = read_json(self.out / "plan" / "plan.json")
        self.report = read_json(self.out / "report" / "report.json")
        self.manifest = read_json(self.out / "plan" / "prereg" / "prereg.json")
        prereg = self.out / "plan" / "prereg" / "j4"
        self.drawn = refs_of((prereg / "labels.jsonl").read_bytes())
        self.excluded = {line["record_ref"] for line in read_lines(prereg / "excluded.jsonl")}
        self.drafting = drafting_refs(self.data)
        # every complaint id of the archive, drawn or not: none may reach anything a reader sees
        export = lab_goldlabels.nhtsa_export()
        with zipfile.ZipFile(self.data / "complaints.zip") as z:
            rows = z.read("CMPL.txt").decode("latin-1").splitlines()
        self.archive_refs = {row.split("\t")[export.C_ODINO] for row in rows if row}
        self.entries = read_json(self.data / "stand-in.json")
        self.questions_prefix = self.entries[2]["questions_sha256_prefix"]
        entry = read_json(self.data / "terms-stand-in.json")
        self.term_check = j4terms.check_terms(self.data / "terms", entry)
        self.terms = {t for ts in self.term_check.terms.values() for t in ts}

    @classmethod
    def get(cls, name: str) -> "_DryRun4":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-dry-"))
        if name not in cls.made:
            if name == "small":
                request = write_json(cls.tmp / "accuracy-small.json", small_request())
            else:
                request = TEMPLATE
            cls.made[name] = cls(request, name)
        return cls.made[name]

    def public_texts(self) -> dict[str, str]:
        """Everything a reader sees: the console, the plan and report summaries and their sources, the report."""
        out = {"stdout": self.stdout, "stderr": self.stderr}
        for rel in ("plan/summary.md", "plan/summary.sources.json", "report/report.md", "report/report.json",
                    "report/report.sources.json"):
            out[rel] = (self.out / rel).read_text(encoding="utf-8")
        for path in sorted((self.out / "shards").glob("*/summary.md")):
            out[path.relative_to(self.out).as_posix()] = path.read_text(encoding="utf-8")
        return out


class RequestTests(unittest.TestCase):
    """``lab.request``'s j4 block (C23, C1, "The request") and the template."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(LAB_MANIFEST)

    def error(self, change: Callable[[dict[str, Any]], None]) -> tuple[str, str]:
        obj = template()
        change(obj["experiments"]["j4"])
        with self.assertRaises(RequestError) as caught:
            lab_request.validate(obj, self.manifest)
        return caught.exception.path, caught.exception.problem

    def test_the_template(self) -> None:
        obj = template()
        out = lab_request.validate(obj, self.manifest)
        block = obj["experiments"]["j4"]
        self.assertEqual(out["experiments"], {"j4": {**block, "models": ["a-4b"]}})
        self.assertEqual((obj["provider"], obj["models"], obj["job_minutes"], obj["max_parallel"],
                          obj["retention_days"]), ("llama-server", ["a-4b"], 330, 5, 30))
        self.assertEqual((block["minutes"], block["labels"], block["parts"], block["seed"], block["bootstrap_b"],
                          block["bootstrap_seed"], block["base_task"], block["primary"], block["comparisons_before"]),
                         (300, {"source": "nhtsa", "pack": NHTSA_PACK, "n": 150, "seed": 4}, 5, 4, 10000, 4,
                          "negation", {"model": "a-4b", "arm": "negation_terms"}, 21))
        self.assertEqual([(e["file"], e["rebuild_seed"], e.get("chain_file")) for e in block["exclude"]],
                         [("prereg/j1/labels.jsonl", 1, None),
                          ("prereg/j2/labels.jsonl", 2, "prereg/j2/excluded.jsonl"),
                          ("prereg/j3/labels.jsonl", 3, "prereg/j3/excluded.jsonl")])
        terms = block["terms"]
        self.assertEqual((terms["file"], terms["cutoff"], terms["artifact"]),
                         ("prereg/jt/terms.json", "2023-01-01", f"lab-plan-{terms['run_id']}-1"))
        for key in ("sha256", "vocabulary_hash"):
            self.assertRegex(terms[key], r"\A[0-9a-f]{64}\Z")
        # every recorded fact the run depends on, as "The request", C3, T1 and T2 of CHOICE-A003.md fix them: a typo
        # in a chain or labels prefix would only leave a source unverified, a typo in the term pin would stop the run
        j001 = {"run_id": 37996675559, "artifact": "lab-plan-37996675559-1", "file": "prereg/j1/labels.jsonl",
                "sha256_prefix": "bc6d092d8bca", "rebuild_seed": 1, "rebuild_sha256_prefix": "bc6d092d8bca"}
        a001 = {"run_id": 38114522160, "artifact": "lab-plan-38114522160-1", "file": "prereg/j2/labels.jsonl",
                "sha256_prefix": "623780b8a1de", "rebuild_seed": 2, "rebuild_sha256_prefix": "623780b8a1de",
                "chain_file": "prereg/j2/excluded.jsonl", "chain_sha256_prefix": "9a948cbf2cd9"}
        a002 = {"run_id": 38439612256, "artifact": "lab-plan-38439612256-1", "file": "prereg/j3/labels.jsonl",
                "sha256_prefix": "0a64a62d94a7", "rebuild_seed": 3, "rebuild_sha256_prefix": "0a64a62d94a7",
                "chain_file": "prereg/j3/excluded.jsonl", "chain_sha256_prefix": "65d51f0fca51"}
        term_run = {"run_id": 38612800578, "artifact": "lab-plan-38612800578-1", "file": "prereg/jt/terms.json",
                    "sha256": "42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc",
                    "vocabulary_hash": "c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579",
                    "cutoff": "2023-01-01"}
        self.assertEqual(block["exclude"], [j001, a001, a002])
        self.assertEqual(block["terms"], term_run)
        choice = CHOICE.read_text(encoding="utf-8")
        for entry in (j001, a001, a002, term_run):
            for key, value in entry.items():
                if key not in ("rebuild_seed", "file", "chain_file"):
                    with self.subTest(key=key, value=value):
                        self.assertIn(str(value), choice)
        self.assertEqual((j4labels.A002_LABELS_SHA256_PREFIX, j4labels.A002_CHAIN_SHA256_PREFIX),
                         (a002["sha256_prefix"], a002["chain_sha256_prefix"]))
        purpose = obj["purpose"]
        self.assertLessEqual(len(purpose), 500)
        for path in ("docs/collective/A003/CHOICE-A003.md", "lab/requests/accuracy-003.json"):
            self.assertIn(path, purpose)
            purpose = purpose.replace(path, "")
        self.assertIsNone(re.search(r"[0-9]", purpose))
        self.assertEqual(lab_request.EXPERIMENTS[-1], "j4")
        self.assertEqual(lab_request._J4_KEYS, ("models", "minutes", "labels", "parts", "seed", "bootstrap_b",
                                                "bootstrap_seed", "base_task", "exclude", "terms", "primary",
                                                "comparisons_before"))
        self.assertEqual(lab_request._J4_TERMS_KEYS, ("run_id", "artifact", "file", "sha256", "vocabulary_hash",
                                                      "cutoff"))
        # The build left the request out (pushing a file under lab/requests/ starts the run). Since the run's request
        # was pushed alone (c5dad9a, CHOICE-A003.md Runs, "Run 1"), the file exists; it must be the template, exactly.
        request = ROOT / "lab" / "requests" / "accuracy-003.json"
        self.assertTrue(request.exists())
        self.assertEqual(json.loads(request.read_text(encoding="utf-8")), template())

    def test_the_errors(self) -> None:
        p = "$.experiments.j4"

        def drop(key: str) -> Callable[[dict[str, Any]], None]:
            return lambda b: b.pop(key)

        def entry(i: int, **changes: Any) -> Callable[[dict[str, Any]], None]:
            def change(b: dict[str, Any]) -> None:
                for key, value in changes.items():
                    if value is None:
                        b["exclude"][i].pop(key)
                    else:
                        b["exclude"][i][key] = value
            return change

        def terms(**changes: Any) -> Callable[[dict[str, Any]], None]:
            def change(b: dict[str, Any]) -> None:
                for key, value in changes.items():
                    if value is None:
                        b["terms"].pop(key)
                    else:
                        b["terms"][key] = value
            return change

        chain = {"chain_file": "prereg/j2/excluded.jsonl", "chain_sha256_prefix": "0" * 12}
        cases = [
            (lambda b: b.update(base_task="rationale"), f"{p}.base_task", lab_request.J4_BASE_PROBLEM),
            (drop("base_task"), f"{p}.base_task", None),
            (drop("terms"), f"{p}.terms", None),
            (lambda b: b.update(exclude=b["exclude"][:2]), f"{p}.exclude", lab_request.J4_EXCLUDE_LIST_PROBLEM),
            (lambda b: b.update(exclude=b["exclude"][0]), f"{p}.exclude", lab_request.J4_EXCLUDE_LIST_PROBLEM),
            (entry(0, file="prereg/j2/labels.jsonl"), f"{p}.exclude[0].file", lab_request.J4_FILE_PROBLEMS[0]),
            (entry(1, file="prereg/j3/labels.jsonl"), f"{p}.exclude[1].file", lab_request.J4_FILE_PROBLEMS[1]),
            (entry(2, file="prereg/j2/labels.jsonl"), f"{p}.exclude[2].file", lab_request.J4_FILE_PROBLEMS[2]),
            (entry(2, file="prereg/j4/labels.jsonl"), f"{p}.exclude[2].file", lab_request.J4_FILE_PROBLEM),
            (entry(0, rebuild_seed=2), f"{p}.exclude[0].rebuild_seed", lab_request.J4_SEED_PROBLEM),
            (entry(2, rebuild_seed=4), f"{p}.exclude[2].rebuild_seed", lab_request.J4_SEED_PROBLEM),
            (entry(0, **chain), f"{p}.exclude[0].chain_file", lab_request.J4_CHAIN_FIRST_PROBLEM),
            (entry(2, chain_file=None), f"{p}.exclude[2].chain_file", lab_request.J4_CHAIN_REQUIRED),
            (entry(2, chain_sha256_prefix=None), f"{p}.exclude[2].chain_sha256_prefix", lab_request.J4_CHAIN_REQUIRED),
            (entry(2, chain_file="prereg/j2/excluded.jsonl"), f"{p}.exclude[2].chain_file",
             lab_request.J4_CHAIN_FILE_PROBLEMS[2]),
            (entry(1, chain_file="prereg/j3/excluded.jsonl"), f"{p}.exclude[1].chain_file",
             lab_request.J4_CHAIN_FILE_PROBLEMS[1]),
            (entry(2, sha256_prefix="0a64"), f"{p}.exclude[2].sha256_prefix", lab_request.J2_SHA_PROBLEM),
            (entry(2, artifact="lab-plan-1-1"), f"{p}.exclude[2].artifact", lab_request.J2_ARTIFACT_PROBLEM),
            (terms(file="prereg/jt/terms.jsonl"), f"{p}.terms.file", lab_request.J4_TERMS_FILE_PROBLEM),
            (terms(file="prereg/j4/terms.json"), f"{p}.terms.file", lab_request.J4_TERMS_FILE_PROBLEM),
            (terms(sha256="42c591ccbb66"), f"{p}.terms.sha256", lab_request.J4_HEX_PROBLEM),
            (terms(sha256="A" * 64), f"{p}.terms.sha256", lab_request.J4_HEX_PROBLEM),
            (terms(vocabulary_hash="c0260f0edab4"), f"{p}.terms.vocabulary_hash", lab_request.J4_HEX_PROBLEM),
            (terms(artifact="lab-plan-1-1"), f"{p}.terms.artifact", lab_request.J4_TERMS_ARTIFACT_PROBLEM),
            (terms(cutoff="2024-01-01"), f"{p}.terms.cutoff", lab_request.JT_CUTOFF_PROBLEM),
            (terms(cutoff=None), f"{p}.terms.cutoff", None),
            (terms(extra="x"), f"{p}.terms.extra", None),
            (lambda b: b["primary"].update(arm="negation"), f"{p}.primary.arm", lab_request.J4_PRIMARY_ARM_PROBLEM),
            (lambda b: b["primary"].update(arm="negation_terms_mapped"), f"{p}.primary.arm",
             lab_request.J4_PRIMARY_ARM_PROBLEM),
            (lambda b: b["primary"].update(arm="evidence_terms"), f"{p}.primary.arm", None),
            (lambda b: b["primary"].update(model="a-1p5b"), f"{p}.primary.model",
             lab_request.J4_PRIMARY_MODEL_PROBLEM),
            (lambda b: b.update(labels={**b["labels"], "source": "generator"}), f"{p}.labels", None),
            (lambda b: b.update(comparisons_before=-1), f"{p}.comparisons_before", None),
            (lambda b: b.update(parts=31), f"{p}.parts", None),
        ]
        for change, path, problem in cases:
            with self.subTest(path=path, problem=problem):
                got_path, got_problem = self.error(change)
                self.assertTrue(got_path.startswith(path), (got_path, got_problem))
                if problem is not None:
                    self.assertEqual(got_problem, problem)

    def test_a_j4_block_beside_a_j2_or_a_j3_block_is_refused(self) -> None:
        """The plan's exclusion outputs and the plan job's downloads name one excluding kind's earlier runs: a j4 block
        beside a j2 or a j3 block (whose plan would hand the j4 step the other kind's entries) is refused."""
        for name, template_name in (("j3", "accuracy-002.json"), ("j2", "accuracy.json")):
            with self.subTest(beside=name):
                obj = template()
                obj["experiments"][name] = read_json(TEMPLATES / template_name)["experiments"][name]
                with self.assertRaises(RequestError) as caught:
                    lab_request.validate(obj, self.manifest)
                self.assertEqual((caught.exception.path, caught.exception.problem),
                                 ("$.experiments", lab_request.J4_BESIDE_PROBLEM))
        self.assertEqual(sorted(lab_request.validate(template(), self.manifest)["experiments"]), ["j4"])

    def test_the_confirmation_case_and_branch_e(self) -> None:
        """C1: under base ``evidence`` the primary arm is ``evidence_terms`` or, in the confirmation case,
        ``evidence``; no other arm of the branch, and no arm of the other branch."""
        for arm in ("evidence_terms", "evidence"):
            with self.subTest(arm=arm):
                obj = template()
                obj["experiments"]["j4"].update(base_task="evidence", primary={"model": "a-4b", "arm": arm})
                out = lab_request.validate(obj, self.manifest)["experiments"]["j4"]
                self.assertEqual((out["base_task"], out["primary"]["arm"]), ("evidence", arm))
        for arm in ("evidence_grounded", "evidence_terms_grounded"):
            with self.subTest(arm=arm):
                path, problem = self.error(lambda b: b.update(base_task="evidence",
                                                              primary={"model": "a-4b", "arm": arm}))
                self.assertEqual((path, problem), ("$.experiments.j4.primary.arm", lab_request.J4_PRIMARY_ARM_PROBLEM))
        path, _ = self.error(lambda b: b.update(base_task="evidence", primary={"model": "a-4b", "arm": "negation"}))
        self.assertEqual(path, "$.experiments.j4.primary.arm")

    def test_a_fake_model_is_accepted_and_a_hosted_one_refused(self) -> None:
        manifest = load_manifest(MANIFEST_TEST)
        obj = json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))
        obj["experiments"] = {"j4": j4_block()}
        out = lab_request.validate(obj, manifest)["experiments"]["j4"]
        self.assertEqual((out["models"], out["primary"]), (["fake-a", "fake-b"], {"model": "fake-b",
                                                                                   "arm": "negation_terms"}))
        obj["models"] = ["fake-a", "fake-b", "h-a"]
        obj["hosted"] = {"h-a": {"max_calls": 10}}
        obj["experiments"]["j4"]["models"] = ["fake-b", "h-a"]
        with self.assertRaises(RequestError) as caught:
            lab_request.validate(obj, manifest)
        self.assertEqual(caught.exception.path, "$.experiments.j4.models[1]")


class PlanWiringTests(unittest.TestCase):
    """``lab.plan`` (C27): the template's units and shards, and the plan step's outputs on a j4, a j3, a j2 and
    another plan."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-plans-"))
        cls.plans: dict[str, dict[str, Any]] = {}
        for name in ("accuracy-003.json", "accuracy-002.json", "accuracy.json", "check.json"):
            out = cls.tmp / name
            code, _, err = run_plan(["--request", str(TEMPLATES / name), "--manifest", str(LAB_MANIFEST),
                                     "--out", str(out), "--github-output", str(cls.tmp / f"{name}.out")])
            assert code == 0, (name, err)
            cls.plans[name] = read_json(out / "plan.json")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, True)

    def test_the_template_gives_five_units_in_five_shards(self) -> None:
        plan = self.plans["accuracy-003.json"]
        block = template()["experiments"]["j4"]
        self.assertEqual([(u["unit"], u["experiment"], u["model"], u["minutes"]) for u in plan["units"]],
                         [(f"j4-a-4b-p{k}", "j4", "a-4b", 300) for k in range(1, 6)])
        self.assertEqual([(s["kind"], s["model"], s["units"], s["planned_minutes"]) for s in plan["shards"]],
                         [("gguf", "a-4b", [f"j4-a-4b-p{k}"], 300) for k in range(1, 6)])
        for k, unit in enumerate(plan["units"], 1):
            params = unit["params"]
            self.assertEqual((params["part"], params["parts"], params["base_task"], params["terms"],
                              params["exclude"], params["primary"], params["comparisons_before"]),
                             (k, 5, "negation", block["terms"], block["exclude"], block["primary"], 21))
        self.assertEqual(lab_plan.exclude_entries(plan), block["exclude"])
        self.assertEqual(lab_plan.terms_entry(plan), block["terms"])

    def test_the_plan_outputs(self) -> None:
        j4 = template()["experiments"]["j4"]
        j3 = read_json(TEMPLATES / "accuracy-002.json")["experiments"]["j3"]
        j2 = read_json(TEMPLATES / "accuracy.json")["experiments"]["j2"]
        keys = ("j2_exclude_artifact", "j2_exclude_run_id", "j3_exclude_artifact_2", "j3_exclude_run_id_2",
                "j4_exclude_artifact_3", "j4_exclude_run_id_3", "j4_terms_artifact", "j4_terms_run_id")
        self.assertEqual([k for k in lab_plan.OUTPUT_KEYS if k.startswith(("j2_", "j3_", "j4_"))], list(keys))

        def pairs(*entries: dict[str, Any] | None) -> tuple[str, ...]:
            out: list[str] = []
            for e in entries:
                out += [e["artifact"], str(e["run_id"])] if e is not None else ["", ""]
            return tuple(out)

        expected = {
            "accuracy-003.json": pairs(j4["exclude"][0], j4["exclude"][1], j4["exclude"][2], j4["terms"]),
            "accuracy-002.json": pairs(j3["exclude"][0], j3["exclude"][1], None, None),
            "accuracy.json": pairs(j2["exclude"], None, None, None),
            "check.json": pairs(None, None, None, None),
        }
        for name, want in expected.items():
            with self.subTest(plan=name):
                outputs = lab_plan.plan_outputs(self.plans[name], "0" * 64)
                self.assertEqual(sorted(outputs), sorted(lab_plan.OUTPUT_KEYS))
                self.assertEqual(tuple(outputs[k] for k in keys), want)
                written = (self.tmp / f"{name}.out").read_text(encoding="utf-8").splitlines()
                for key, value in zip(keys, want):
                    self.assertIn(f"{key}={value}", written)
        self.assertEqual(tuple(lab_plan.plan_outputs(None, "")[k] for k in keys), pairs(None, None, None, None))


class UnitWiringTests(unittest.TestCase):
    """``lab.units`` (C27): the adapter, the status reading with J4_TERMS_STOPPED, the required task, the argv with
    ``--terms``, the routing of the branch's two tasks, and a unit run through the fake provider."""

    def setUp(self) -> None:
        self.p = _Prereg4.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-unit-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_adapter_and_the_lists(self) -> None:
        adapter = units.ADAPTERS["j4"]
        self.assertEqual((adapter.module, adapter.routed_tasks, adapter.boundary, adapter.result_file,
                          adapter.subcommand, adapter.deadline_s),
                         ("lab.j4", lab_j4.ALL_TASKS, "site:lab", "run.json", "run", lab_j1.DEADLINE_S))
        self.assertEqual(sorted(adapter.collect), ["ledger.jsonl", "run.json", "verdicts.jsonl"])
        for name in ("PREREG_EXPERIMENTS", "REPLY_PARTICIPATION", "PASS_EXPERIMENTS"):
            self.assertIn("j4", getattr(units, name), name)
        unit = {"experiment": "j4", "params": {"base_task": "negation", "primary": {"arm": "negation_terms"}}}
        self.assertEqual(units.required_tasks(unit), [ND])
        confirmation = {"experiment": "j4", "params": {"base_task": "evidence", "primary": {"arm": "evidence"}}}
        self.assertEqual(units.required_tasks(confirmation), [E])
        branch_e = {"experiment": "j4", "params": {"base_task": "evidence", "primary": {"arm": "evidence_terms"}}}
        self.assertEqual(units.required_tasks(branch_e), [ED])
        self.assertTrue(units.public_text({"experiment": "j4", "params": {}}))
        self.assertEqual(units.unit_notes("j4", "model", public_text=True),
                         ["model_measurement_public", "public_narratives", "runner_hardware"])
        self.assertEqual(units.harness_measurement("j4", {"measurement": True}), True)
        self.assertIs(units.harness_verdict("j4", {"measurement": False}), False)

    def test_the_status_reading(self) -> None:
        def result(complete: bool, d: bool, b: bool, order: tuple[str, str] = (ND, N),
                   kind: str = "lab_j4_run") -> dict[str, Any]:
            return {"kind": kind, "complete": complete, "pass_order": list(order),
                    "complete_by_task": {order[0]: d, order[1]: b}}

        status = units.harness_status
        self.assertEqual(status("j4", 0, False, result(True, True, True)), ("ok", None))
        self.assertEqual(status("j4", 1, False, result(False, True, False)), ("ok", notes.J4_TERMS_STOPPED))
        self.assertEqual(status("j4", 1, False, result(False, True, False, (E, ED))), ("ok", notes.J4_TERMS_STOPPED))
        self.assertEqual(status("j4", 1, False, result(False, False, False)), ("failed", notes.J1_STOPPED))
        self.assertEqual(status("j4", 1, False, result(False, False, True)), ("failed", notes.J1_STOPPED))
        self.assertEqual(status("j4", 1, False, result(False, True, True)), ("failed", notes.J1_STOPPED))
        self.assertEqual(status("j4", 0, False, result(False, True, False)), ("failed", notes.RESULT_CONTRADICTS_EXIT))
        self.assertEqual(status("j4", 1, False, result(True, True, True)), ("failed", notes.RESULT_CONTRADICTS_EXIT))
        self.assertEqual(status("j4", 0, False, result(True, True, True, kind="lab_j3_run")),
                         ("failed", notes.RESULT_MISSING))
        self.assertEqual(status("j4", 0, False, result(True, True, True, order=(N, N))),
                         ("failed", notes.RESULT_MISSING))
        self.assertEqual(status("j4", 0, False, {"kind": "lab_j4_run", "complete": True,
                                                 "complete_by_task": {ND: True, N: True}}),
                         ("failed", notes.RESULT_MISSING))
        self.assertEqual(status("j4", 0, False, None), ("failed", notes.RESULT_MISSING))
        self.assertEqual(status("j4", 3, False, result(True, True, True)), ("failed", notes.UNEXPECTED_EXIT))
        self.assertEqual(status("j4", 0, True, result(True, True, True))[0], "timed_out")
        self.assertIsNone(re.search(r"[0-9]", notes.J4_TERMS_STOPPED))

    def test_participation_judges_the_second_pass_only_when_it_finished(self) -> None:
        def row(task: str, ref: str, ok: bool) -> dict[str, Any]:
            return {"task": task, "ref": ref, "attempt": 1, "ok": ok, "error_kind": None if ok else "network",
                    "boundary": "site:lab"}

        good_d = [row(ND, f"d{i}", True) for i in range(10)]
        bad_b = [row(N, f"b{i}", i == 0) for i in range(10)]
        stopped = {"kind": "lab_j4_run", "complete": False, "pass_order": [ND, N],
                   "complete_by_task": {ND: True, N: False}}
        finished = {"kind": "lab_j4_run", "complete": True, "pass_order": [ND, N],
                    "complete_by_task": {ND: True, N: True}}
        _, problem = units.participation("j4", good_d + bad_b, [ND], stopped)
        self.assertIsNone(problem)
        _, problem = units.participation("j4", good_d + bad_b, [ND], finished)
        self.assertTrue(problem is not None and problem.startswith(notes.LOW_PARTICIPATION) and N in problem)

    def test_the_unit_adapter_runs_it(self) -> None:
        unit = next(u for u in self.p.plan["units"] if u["unit"] == "j4-fake-b-p2")
        out = self.tmp / "shard"
        record = units.run_unit(dict(unit, shard="s002-fake-b"), self.p.plan, out, timeout_s=300,
                                provider_override="fake", prereg=self.p.prereg)
        self.assertEqual((record["status"], record["measurement_class"], record["exit_code"]),
                         ("ok", "plumbing", 0), record["status_reason"])
        self.assertEqual(record["notes"], ["plumbing", "public_narratives", "runner_hardware"])
        base = f"runs/j4/{unit['run_id']}/"
        self.assertEqual(sorted(record["files"]), [base + "ledger.jsonl", base + "run.json", base + "verdicts.jsonl"])
        argv = record["argv"]
        self.assertEqual(argv[2:4], ["lab.j4", "run"])
        for arg in ("--part=2", "--endpoint=fake-b", f"--budget-seconds={300 - units.SIM_BUDGET_MARGIN_S}"):
            self.assertIn(arg, argv)
        self.assertEqual([a for a in argv if a.startswith("--terms=")],
                         [f"--terms={self.p.prereg.dir / lab_j4.TERMS_FILE}"])
        self.assertTrue(any(a.endswith("prereg/j4/prereg.json") for a in argv))
        routing = read_json(out / "routing" / f"{unit['unit']}.json")
        self.assertEqual((sorted(routing["endpoints"]), routing["routes"]),
                         (["fake-b"], {ND: {"endpoint": "fake-b"}, N: {"endpoint": "fake-b"}}))
        self.assertEqual((record["participation"]["required"],
                          {t: record["participation"]["tasks"][t]["attempted"] for t in (ND, N)}),
                         ([ND], {ND: 40, N: 40}))
        self.assertEqual(record["harness_measurement"], False)
        doc = read_json(out / (base + "run.json"))
        self.assertEqual((doc["kind"], doc["complete"], doc["complete_by_task"], doc["pass_order"]),
                         ("lab_j4_run", True, {ND: True, N: True}, [ND, N]))
        lines = read_lines(out / (base + "verdicts.jsonl"))
        self.assertEqual([line["task"] for line in lines], [ND] * 40 + [N] * 40)
        self.assertTrue(all(line["terms_count"] is not None for line in lines[:40]))
        self.assertTrue(all(line["terms_count"] is None and line["terms_sha256"] is None for line in lines[40:]))


class WiringTests(unittest.TestCase):
    def test_both_fake_handlers(self) -> None:
        """The lab's fake answers each ``_terms`` task as it answers the base task (task D as task B, so nothing in a
        dry run measures the terms), behind an empty citation under branch E, in the pack branch and the pack-free
        one; an unknown name is still refused."""
        p = _Prereg4.get()
        d = _Data.get()
        qs = lab_j1.read_questions((p.dir / "questions.jsonl").read_bytes())
        recs = {r["record_ref"]: r for r, _ in lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())}

        def request(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            from mycelic.collective.inference.fakeserver import _DATA_CLOSE, _DATA_OPEN
            return {"messages": [{"role": "system", "content": "x"},
                                 {"role": "user", "content": f"{_DATA_OPEN}{canonical_dumps(payload)}{_DATA_CLOSE}"}],
                    "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": {}}}}

        for responder in (Responder(PACK), Responder(None, pack_free_judge=True)):
            for q in qs[:6]:
                record = recs[q["record_ref"]]
                base = lab_j1.payload(PACK, q, record)
                drafted = lab_j4.payload(PACK, q, record, d.T)
                shipped = responder(request(JUDGE_TASK, base))
                with self.subTest(responder=responder.pack is not None, question=q["question_id"]):
                    self.assertEqual(responder(request(ND, drafted)), responder(request(N, base)))
                    self.assertEqual(responder(request(N, base)), shipped)
                    self.assertEqual(responder(request(ED, drafted)), {"citation": "", **shipped})
                    self.assertEqual(responder(request(E, base)), {"citation": "", **shipped})
                    self.assertEqual(list(responder(request(ED, drafted)))[0], "citation")
                    with self.assertRaises(ResponderError):
                        responder(request("judge_record_other_terms", drafted))
        self.assertEqual(set(lab_j4.DRAFTED_TASK.values()), {ND, ED})

    def test_the_warm_up_sends_two_tasks_the_probe_b(self) -> None:
        """Rule 16, C12: a J4 unit warms task B with the judge's generated payloads and, with the preregistration's
        term set, task D with the same payloads plus ``predicate_terms`` (the typical one its asked predicate's list,
        the worst case asked about the predicate with the longest list, by characters, and carrying it); sorted by
        name task B comes first, so the probe is task B's. Without the preregistration task D is not warmed. The
        warm-up's record carries the lists' counts and predicates, never a term."""
        p = _Prereg4.get()
        d = _Data.get()
        units_ = [u for u in p.plan["units"] if u["model"] == "fake-a"]
        tasks = [t for t in lab_warmup.warm_tasks(units_, p.plan, p.prereg) if t.units]
        self.assertEqual([t.task.name for t in tasks], [N, ND])
        b, dt = tasks
        self.assertEqual((b.task, dt.task), (lab_j2.negation_task(), lab_j4.drafted_task("negation")))
        self.assertEqual((b.schema, dt.schema), (judge_schema(), judge_schema()))
        self.assertEqual((b.stream, dt.stream, b.pack, dt.pack, b.terms), (False, False, NHTSA_PACK, NHTSA_PACK, None))
        self.assertEqual(sorted(b.units), sorted(u["unit"] for u in units_))
        self.assertEqual(dt.units, b.units)
        typical, worst = dt.payload, dt.worst
        self.assertEqual({k: v for k, v in typical.items() if k != "question"},
                         {k: v for k, v in b.payload.items() if k != "question"})
        self.assertEqual({k: v for k, v in typical["question"].items() if k != lab_j4.TERMS_KEY}, b.payload["question"])
        self.assertEqual(typical["question"][lab_j4.TERMS_KEY],
                         lab_j4.predicate_terms(d.T, typical["question"]["predicate"]))
        longest = lab_j4.longest_terms(d.T)
        self.assertIsNotNone(longest)
        self.assertEqual(worst["question"]["predicate"], longest)
        self.assertEqual(worst["question"][lab_j4.TERMS_KEY], d.T[longest])
        self.assertEqual(len("".join(worst["question"][lab_j4.TERMS_KEY])),
                         max(len("".join(terms)) for terms in d.T.values()))
        self.assertEqual(dt.terms, {"typical_terms": len(typical["question"][lab_j4.TERMS_KEY]),
                                    "typical_predicate": typical["question"]["predicate"],
                                    "worst_terms": len(d.T[longest]), "worst_predicate": longest})
        self.assertEqual([t.task.name for t in lab_warmup.warm_tasks(units_, p.plan) if t.units], [N])
        self.assertNotIn(b.payload["record"]["text"], "".join(
            r["narrative"] for r, _ in lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())))

    def test_the_warm_up_prints_task_d_s_term_counts_on_a_server(self) -> None:
        """Rule 16, C12, on the stub server: task D's warm-up row holds both payloads' term counts and asked
        predicates, and its console line ``lab: warmup <task D> ...`` prints them with the worst case's counted prompt,
        ``max_tokens``, the slot and whether it fits; counts and predicate ids only, never a term. Task B prints no
        such line, and its probe is task B's."""
        from lab.server import ModelServer, ServerSpec
        from mycelic.collective.inference.tasks import render_messages
        from mycelic.collective.schemacheck import compile as compile_schema
        from tests.lab.stubs.fake_server_stub import write_launcher

        p = _Prereg4.get()
        d = _Data.get()
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-warm-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(kill_mentioning, str(tmp))
        launcher = write_launcher(tmp / "bin" / "server-stub", {"record_dir": str(tmp / "records"), "tag": "b7",
                                                                 "pack": str(ROOT / NHTSA_PACK)})
        model = tmp / "models" / "model.gguf"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"GGUF")
        ctx = 16384
        instance = ModelServer(ServerSpec(binary=launcher, model_path=model, alias="stub-alias", tag="b7", slots=1,
                                          ctx_per_slot=ctx, seed=7, threads=2, threads_batch=2, cache_ram_mib=256,
                                          extra_args=()), out=tmp / "OUT", key="k", index=0, health_deadline_s=30)
        self.addCleanup(instance.stop)
        instance.start()
        units_ = [u for u in p.plan["units"] if u["model"] == "fake-a"]
        tasks = [t for t in lab_warmup.warm_tasks(units_, p.plan, p.prereg) if t.units]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            record, skips, stop_all = lab_warmup.warm_up(
                instance, {"response_format": "json_schema", "transport_schema": "full"}, tasks, ctx_per_slot=ctx,
                out=tmp / "OUT", start_index=0, meminfo=lambda: 8 << 30)
        self.assertEqual((skips, stop_all), ({}, None))
        _, dt = tasks

        def counted(payload: dict[str, Any]) -> int:
            """The stub's count: its template's prompt, one token per four characters, plus one."""
            messages = render_messages(dt.task, payload, compile_schema(dt.schema))
            prompt = "".join(f"<|{m.get('role')}|>\n{m.get('content')}\n" for m in messages) + "<|assistant|>\n"
            return len(prompt) // 4 + 1

        longest = lab_j4.longest_terms(d.T)
        worst = counted(dt.worst)
        self.assertGreater(worst, counted(dt.payload))
        expected = (f"lab: warmup {ND} typical_terms {len(d.T.get(dt.terms['typical_predicate'], []))} "
                    f"typical_predicate {dt.payload['question']['predicate']} worst_terms {len(d.T[longest])} "
                    f"worst_predicate {longest} worst_prompt_tokens {worst} max_tokens {dt.task.max_tokens} slot "
                    f"{ctx} fits yes")
        self.assertEqual([line for line in out.getvalue().splitlines() if line.startswith("lab: warmup ")],
                         [expected])
        rows = {row["task"]: row for row in record["tasks"]}
        self.assertEqual(sorted(rows), [N, ND])
        self.assertNotIn("terms", rows[N])
        self.assertEqual(rows[ND]["terms"], dt.terms)
        self.assertEqual((rows[ND]["prompt_tokens_counted"], rows[ND]["worst_prompt_tokens"], rows[ND]["fits"]),
                         (counted(dt.payload), worst, True))
        self.assertEqual(record["probe"]["task"], N)
        assert_no_term(self, out.getvalue() + canonical_dumps(record), d.all_terms)
        # an uncounted worst case, and a slot one token too small
        self.assertTrue(lab_warmup.terms_line(dt, None, ctx).endswith(
            f"worst_prompt_tokens none max_tokens {dt.task.max_tokens} slot {ctx} fits none"))
        small = worst + dt.task.max_tokens - 1
        self.assertTrue(lab_warmup.terms_line(dt, worst, small).endswith(f"slot {small} fits no"))
        self.assertTrue(lab_warmup.terms_line(dt, worst, small + 1).endswith(f"slot {small + 1} fits yes"))

    def test_the_workflow_downloads_the_third_earlier_artifact_and_the_term_artifact(self) -> None:
        """C27: the plan job's ``earlier-3`` and ``terms`` steps, of the ``earlier`` steps' shape (the same pinned
        action, the workflow's own read-only token, ``continue-on-error``), after ``earlier-2`` and before ``prereg``;
        the third excluded run under ``lab-earlier`` by its own run id, the term run's artifact under ``lab-terms``,
        which ``lab.prereg`` reads by default beside the plan directory; the workflow's permissions stay
        ``actions: read``, ``contents: read`` and no job widens them."""
        import yaml
        text = (ROOT / ".github" / "workflows" / "mycelic-lab.yml").read_text(encoding="utf-8")
        wf = yaml.safe_load(text)
        self.assertEqual(wf["permissions"], {"actions": "read", "contents": "read"})
        self.assertFalse(any("permissions" in job for job in wf["jobs"].values()))
        steps = wf["jobs"]["plan"]["steps"]
        ids = [s.get("id") for s in steps]
        at = ids.index("prereg")
        self.assertEqual(ids[at - 5:at], ["plan", "earlier", "earlier-2", "earlier-3", "terms"])
        earlier, third, terms = steps[at - 4], steps[at - 2], steps[at - 1]
        for step, run_key, artifact_key, root in ((third, "j4_exclude_run_id_3", "j4_exclude_artifact_3",
                                                   "lab-earlier"),
                                                  (terms, "j4_terms_run_id", "j4_terms_artifact", "lab-terms")):
            with self.subTest(step=step["id"]):
                self.assertEqual(step["uses"], earlier["uses"])
                self.assertIs(step["continue-on-error"], True)
                self.assertEqual(step["if"], f"steps.plan.outputs.{run_key} != ''")
                self.assertEqual(step["with"], {
                    "name": f"${{{{ steps.plan.outputs.{artifact_key} }}}}",
                    "run-id": f"${{{{ steps.plan.outputs.{run_key} }}}}",
                    "github-token": "${{ github.token }}",
                    "path": f"${{{{ runner.temp }}}}/{root}/${{{{ steps.plan.outputs.{run_key} }}}}"})
                self.assertIn(run_key, lab_plan.OUTPUT_KEYS)
                self.assertIn(artifact_key, lab_plan.OUTPUT_KEYS)
        self.assertEqual(text.count("github.token"), 5)
        self.assertEqual(lab_prereg.terms_dir(Path("/x/lab-plan")), Path("/x/lab-terms"))
        self.assertEqual(lab_prereg.TERMS_DIRNAME, "lab-terms")

    def test_the_notes_hold_no_digit(self) -> None:
        """No ASCII digit in any J4 note, heading or column of ``lab.notes``, so no figure can ride in a fixed
        sentence and every number a summary prints is traced to a run file; the primary line's placeholders are the
        ones the summary fills."""
        names = sorted(n for n in dir(notes) if n.startswith("J4_"))
        self.assertEqual(names, ["J4_ARMS_NOTE", "J4_EXCLUSION_DISAGREES", "J4_EXCLUSION_NOTE",
                                 "J4_FRESHNESS_UNVERIFIED", "J4_LABEL", "J4_PRIMARY_LINE", "J4_SPLIT_VIOLATED",
                                 "J4_STAND_IN", "J4_TERMS_NOTE", "J4_TERMS_STOPPED", "J4_TERMS_UNVERIFIED",
                                 "J4_TOO_FEW_RECORDS", "J4_VERDICT_NOTE"])
        texts = {n: getattr(notes, n) for n in names}
        headings = {k: v for k, v in notes.HEADINGS.items() if k == "j4" or k.startswith("j4-")}
        columns = {k: v for k, v in notes.COLUMNS.items() if k.startswith("j4_")}
        self.assertEqual(sorted(headings), ["j4", "j4-agreement", "j4-arms", "j4-citations", "j4-earlier",
                                            "j4-judges", "j4-lifts", "j4-tasks", "j4-terms"])
        self.assertGreater(len(columns), 40)
        texts.update({f"HEADINGS.{k}": v for k, v in headings.items()})
        texts.update({f"COLUMNS.{k}": v for k, v in columns.items()})
        for name, value in texts.items():
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
                self.assertIsNone(re.search(r"[0-9]", value))
        self.assertEqual(sorted(set(re.findall(r"\{([a-z_]+)\}", notes.J4_PRIMARY_LINE))),
                         ["arm", "ba", "bound", "dhi", "diff", "dlo", "gap", "hdiff", "hhi", "hi", "hlo", "lex_d",
                          "lex_d_hi", "lex_d_lo", "lex_h", "lex_h_hi", "lex_h_lo", "lex_p", "lex_p_hi", "lex_p_lo",
                          "lo", "model", "other_arm", "other_word", "pdiff", "target", "thi", "tlo", "unknown",
                          "word", "word_five"])
        self.assertIn("J4", lab_summary.ids(notes.J4_LABEL))
        self.assertNotIn("{", lab_summary.ids(notes.J4_LABEL))
        self.assertEqual(lab_summary.J4_ARMS, {base: lab_j4.ARMS[base] for base in lab_j4.BASES})
        self.assertEqual(lab_summary.J4_LIFTS, tuple(name for name, _, _ in lab_j4.LIFTS))

    def test_the_dry_run_takes_one_test_data_flag(self) -> None:
        """``--j4-data`` does not go with ``--j2-data``, ``--j3-data`` or ``--jt-data``: a usage error, refused
        before the plan is written."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-dryrun-flags-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for other in ("--j2-data", "--j3-data", "--jt-data"):
            with self.subTest(other=other):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = lab_dryrun.main(["--request", str(TEMPLATE), "--out", str(tmp / "out"),
                                            "--j4-data", str(tmp / "a"), other, str(tmp / "b")])
                self.assertEqual(code, lab_dryrun.EXIT_USAGE)
                self.assertIn("error: --j4-data does not go with --j2-data, --j3-data or --jt-data", err.getvalue())
                self.assertFalse((tmp / "out").exists())


class PreregWiringTests(unittest.TestCase):
    """``lab.prereg``'s J4 step in this process (rule order: the term check, the eight sources, the split and the
    draw, the comparators), its files, its manifest block and its fixed lines."""

    def test_the_files_and_the_manifest(self) -> None:
        p = _Prereg4.get()
        d = _Data.get()
        self.assertEqual(sorted(x.name for x in p.dir.iterdir()),
                         sorted(["labels.jsonl", "labels.json", "questions.jsonl", "questions.json", "excluded.jsonl",
                                 "terms.json", "routing.json", "prereg.json",
                                 *(f"{name}.{ext}" for name in lab_j4.COMPARATORS for ext in ("jsonl", "json"))]))
        self.assertEqual((p.dir / "terms.json").read_bytes(), d.terms.data)
        j4 = p.manifest["j4"]
        doc = read_json(p.dir / "prereg.json")
        self.assertEqual((j4["base_task"], j4["primary"], j4["headline_arm"], j4["headline_secondary"],
                          j4["other_reading_arm"], j4["confirmation"]),
                         ("negation", {"model": "fake-b", "arm": "negation_terms"}, doc["headline_arm"],
                          "negation_terms_mapped", "negation_terms_mapped", False))
        self.assertEqual((j4["labels"]["records"], j4["questions"]["questions"], j4["parts"], j4["stand_in"],
                          j4["terms_stand_in"]), (40, 80, 2, True, True))
        self.assertEqual((j4["exclusion"]["excluded"], j4["exclusion"]["overlap"], j4["split"]["by_record_id"],
                          j4["split"]["by_text"], j4["split"]["tripwires"]),
                         (451, 0, 0, 1, {"before_cutoff": 0, "in_drafting": 0}))
        self.assertEqual(j4["excluded_lines"], 451 + 40)
        terms = j4["terms"]
        self.assertEqual((terms["sha256"], terms["check"]["outcome"], terms["requested"]),
                         (d.terms.summary["sha256"], "verified", template()["experiments"]["j4"]["terms"]))
        self.assertEqual(sum(terms["terms_by_predicate"].values()), len(d.all_terms))
        self.assertEqual(doc["terms"]["sha256"], d.terms.summary["sha256"])
        for name in lab_j4.COMPARATORS:
            self.assertEqual(j4[name], read_json(p.dir / f"{name}.json"), name)
        # the draw, the questions and every comparator's score recomputed from the request's own figures (C5)
        draw = d.make_draw(d.est, n=40)
        self.assertEqual((p.dir / "labels.jsonl").read_bytes(), draw.data)
        questions_bytes, _ = lab_j1.build_questions(PACK, draw.data, seed=4, parts=2)
        self.assertEqual((p.dir / "questions.jsonl").read_bytes(), questions_bytes)
        questions = lab_j1.read_questions(questions_bytes)
        for name in lab_j4.COMPARATORS:
            self.assertEqual(j4[name], lab_j1.score(questions, read_lines(p.dir / f"{name}.jsonl"), bootstrap_b=1000,
                                                    bootstrap_seed=4), name)
        # C11: two models, four arms each
        self.assertEqual((j4["comparisons_before"], j4["comparisons_this_cycle"], doc["comparisons_this_cycle"]),
                         (21, 8, 8))
        self.assertNotIn("j4", self.other_kinds_manifest())
        self.assertEqual(p.manifest["j3"], None)
        manifest_text = (p.tmp / "plan" / "prereg" / "prereg.json").read_text(encoding="utf-8")
        assert_no_term(self, manifest_text, d.all_terms)

    def other_kinds_manifest(self) -> dict[str, Any]:
        """A plan of another kind's preregistration manifest: it has no ``j4`` key (only a J4 plan writes one)."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-other-kind-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        request = json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))
        request["experiments"] = {"j1": {"minutes": 5, "labels": {**LABELS, "seed": 1}, "parts": 2, "seed": 1,
                                         "bootstrap_b": 1000}}
        path = write_json(tmp / "j1-test.json", request)
        code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "plan")])
        self.assertEqual(code, 0, err)
        with contextlib.redirect_stdout(io.StringIO()):
            manifest = lab_prereg.preregister(tmp / "plan" / "plan.json", nhtsa_fetch=lambda: ARCHIVE)
        self.assertEqual(list(manifest), ["schema_version", "kind", "plan_sha256", "files", "e1", "x1", "e2", "j1",
                                          "l1", "j2", "j3", "jt"])
        return manifest

    def test_each_seed_feeds_its_own_step(self) -> None:
        """C5: the labels' seed draws the records, the block's seed orders the questions and splits them into parts,
        and the bootstrap's seed and size score every comparator and pair the judges; three different seeds, so a
        step fed another's seed shows."""
        d = _Data.get()
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-seeds-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        request = json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))
        request["experiments"] = {"j4": j4_block(labels={**LABELS, "seed": 5}, seed=6, bootstrap_b=1001,
                                                 bootstrap_seed=7)}
        path = write_json(tmp / "j4-test.json", request)
        code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "plan")])
        self.assertEqual(code, 0, err)
        with contextlib.redirect_stdout(io.StringIO()):
            manifest = lab_prereg.preregister(tmp / "plan" / "plan.json", nhtsa_fetch=lambda: ARCHIVE,
                                              earlier_plans=d.dir / "earlier", exclude_stand_in=d.entries,
                                              terms_plans=d.dir / "terms", terms_stand_in=d.term_entry)
        j4 = manifest["j4"]
        directory = tmp / "plan" / "prereg" / "j4"
        draw = j4labels.draw(pack_id=NHTSA_PACK, n=40, seed=5, fetch=lambda: ARCHIVE, est=d.est,
                             term=d.terms.drafting, term_archive_sha256=d.terms.summary["archive_sha256"],
                             used=d.used, requested=d.used, stand_in=True)
        self.assertEqual((directory / "labels.jsonl").read_bytes(), draw.data)
        self.assertNotEqual(draw.data, d.make_draw(d.est, n=40).data)
        questions_bytes, _ = lab_j1.build_questions(PACK, draw.data, seed=6, parts=2)
        self.assertEqual((directory / "questions.jsonl").read_bytes(), questions_bytes)
        self.assertNotEqual(questions_bytes, lab_j1.build_questions(PACK, draw.data, seed=5, parts=2)[0])
        questions = lab_j1.read_questions(questions_bytes)
        files = {name: read_lines(directory / f"{name}.jsonl") for name in lab_j4.COMPARATORS}
        self.assertEqual(files, lab_j4.comparator_lines(PACK, d.p_d, draw.data, questions, d.T))
        for name in lab_j4.COMPARATORS:
            self.assertEqual(j4[name], lab_j1.score(questions, files[name], bootstrap_b=1001, bootstrap_seed=7), name)
        self.assertEqual(j4["judge_pairs"], lab_j4.judge_pairs(questions, files, bootstrap_b=1001, bootstrap_seed=7))
        self.assertNotEqual(j4["judge_pairs"], lab_j4.judge_pairs(questions, files, bootstrap_b=1000, bootstrap_seed=7))
        self.assertEqual((j4["seed"], j4["bootstrap_b"], j4["bootstrap_seed"], j4["labels"]["seed"]), (6, 1001, 7, 5))

    # ------------------------------------------------------------------ the stops (item 4, items 5 to 8, item 10)

    def stopped(self, *, n: int = 40, fetch: Callable[[], bytes] = lambda: ARCHIVE, earlier: Path | None = None,
                entries: list[dict[str, Any]] | None = None, stand_in: bool = True
                ) -> tuple[lab_prereg._StepFailed, list[str]]:
        """A J4 plan of two fake models (``n`` records in 2 parts) preregistered in this process with the test data's
        stand-ins (or the ones given; ``stand_in`` False reads the request's own entries): the stop and the lines
        printed. Nothing is written under ``prereg/j4``."""
        d = _Data.get()
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-stop-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        request = json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))
        request["experiments"] = {"j4": j4_block(labels={**LABELS, "n": n})}
        path = write_json(tmp / "j4-test.json", request)
        code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "plan")])
        self.assertEqual(code, 0, err)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(lab_prereg._StepFailed) as caught:
            lab_prereg.preregister(tmp / "plan" / "plan.json", nhtsa_fetch=fetch,
                                   earlier_plans=earlier if earlier is not None else d.dir / "earlier",
                                   exclude_stand_in=(entries if entries is not None else d.entries) if stand_in
                                   else None, terms_plans=d.dir / "terms", terms_stand_in=d.term_entry)
        self.assertFalse((tmp / "plan" / "prereg" / "j4").exists())
        return caught.exception, out.getvalue().splitlines()

    def assert_term_line_last(self, lines: list[str]) -> None:
        """Rule 17: a stop after the term check verified prints the term line last, with P_D's four hashes."""
        d = _Data.get()
        self.assertTrue(lines[-1].startswith("j4 terms: outcome verified failed none "), lines[-1])
        self.assertIn(" ".join(f"p_d_{key} {d.terms.p_d[key]}" for key in lab_prereg.P_D_HASH_KEYS), lines[-1])
        self.assertEqual(sum(1 for line in lines if line.startswith("j4 terms:")), 1)
        self.assertFalse(any(line.startswith("j4 comparators:") for line in lines))

    def test_a_cycle_not_established_stops_before_any_draw(self) -> None:
        """Item 4 (a): no earlier artifact and J001's rebuild broken: no cycle is established; the sources' lines,
        B1's with the questions prefix it was checked against, the exclusion line, then the term line."""
        d = _Data.get()
        empty = Path(tempfile.mkdtemp(prefix="lab-j4-empty-"))
        self.addCleanup(shutil.rmtree, empty, True)
        entries = [broken(d.entries[0], "rebuild_sha256_prefix"), d.entries[1], d.entries[2]]
        err, lines = self.stopped(earlier=empty, entries=entries)
        self.assertEqual((err.path, err.problem), ("$.experiments.j4.exclude", notes.J4_FRESHNESS_UNVERIFIED))
        self.assertIn("j4 exclusion: source J1 artifact absent", lines)
        self.assertIn(f"j4 exclusion: source B1 artifact absent B1_questions_sha256 {d.questions_prefix}", lines)
        self.assertTrue(any(line.startswith("j4 exclusion: j001=not_established a001=not_established "
                                            "a002=not_established consistency ") for line in lines), lines)
        self.assertFalse(any(line.startswith(("j4 draw:", "j4 split:")) for line in lines))
        self.assert_term_line_last(lines)

    def test_the_request_s_own_entries_check_b1_against_a002_s_pinned_questions_prefix(self) -> None:
        """C3: without a stand-in the request's own entries are read (no such artifact here) and B1 is checked
        against A002's pinned questions prefix, which its line prints."""
        err, lines = self.stopped(stand_in=False)
        self.assertEqual((err.path, err.problem), ("$.experiments.j4.exclude", notes.J4_FRESHNESS_UNVERIFIED))
        self.assertIn("j4 exclusion: source B1 artifact absent B1_questions_sha256 84486fa38568", lines)
        self.assert_term_line_last(lines)

    def test_a_consistency_count_above_zero_stops_before_any_draw(self) -> None:
        """Item 4 (b): J001's artifact drawn from another archive (``j1_r1`` above 0), and K3 one ref wider than the
        three cycles' sets (``k3_union`` 1), each the only count above 0 (``SourcesTests.alone``): the plan job stops
        with J4_EXCLUSION_DISAGREES before the draw, the count printed on the exclusion line."""
        for key in ("j1_r1", "k3_union"):
            with self.subTest(count=key):
                used, prefix, want = SourcesTests.alone(self, key)     # type: ignore[arg-type]
                directory = used.pop()
                entries = [used[0], used[1], {**used[2], "questions_sha256_prefix": prefix}]
                err, lines = self.stopped(earlier=directory, entries=entries)
                self.assertEqual((err.path, err.problem), ("$.experiments.j4.exclude", notes.J4_EXCLUSION_DISAGREES))
                exclusion = [line for line in lines if line.startswith("j4 exclusion: j001=")]
                self.assertEqual(len(exclusion), 1)
                self.assertGreater(want, 0)
                self.assertIn(f" {key}={want} ", exclusion[0])
                others = [part for part in exclusion[0].split(" ") if "=" in part and part.split("=")[0]
                          in j4labels.CONSISTENCY and part.split("=")[0] != key]
                self.assertTrue(all(part.split("=")[1] in ("0", "none") for part in others), others)
                self.assertFalse(any(line.startswith(("j4 draw:", "j4 split:")) for line in lines))
                self.assert_term_line_last(lines)

    def test_too_few_records_left_stops_after_the_split(self) -> None:
        """Items 5 and 6 (c): one record more than the 273 the exclusion and the split leave: J4_TOO_FEW_RECORDS at
        the labels' ``n``, the draw's own sentence printed, then the term line."""
        err, lines = self.stopped(n=274)
        self.assertEqual((err.path, err.problem), ("$.experiments.j4.labels.n", notes.J4_TOO_FEW_RECORDS))
        self.assertIn("j4 draw: only 273 eligible unexcluded public records remain: no further fresh draw of 274 "
                      "records is pre-registered", " ".join(lines))
        self.assert_term_line_last(lines)

    def test_a_tripwire_stops_after_the_draw(self) -> None:
        """Item 8 (d): the removals skipped (``split_removals`` finding nothing), a draw of every one of the 274
        records holds the repeated-narrative complaint: ``in_drafting`` 1, J4_SPLIT_VIOLATED, the split line printed
        before the term line and no draw line."""
        with mock.patch.object(j4labels, "split_removals", return_value=(set(), set(), 3)):
            err, lines = self.stopped(n=274)
        self.assertEqual((err.path, err.problem), ("$.experiments.j4.labels", J4_SPLIT_VIOLATED))
        split = [line for line in lines if line.startswith("j4 split:")]
        self.assertEqual(len(split), 1)
        self.assertTrue(split[0].endswith("by_record_id 0 by_text 0 already_excluded 3 before_cutoff 0 in_drafting 1"),
                        split[0])
        self.assertEqual(lines[-2], split[0])
        self.assertFalse(any(line.startswith("j4 draw:") for line in lines))
        self.assert_term_line_last(lines)

    def test_a_drafting_set_that_does_not_rebuild_stops_before_the_draw(self) -> None:
        """Item 8 (e): the same archive as the term run's, a rebuilt drafting set of one row fewer: J4_SPLIT_VIOLATED
        before the removals; the split line shows both figures and no count."""
        d = _Data.get()
        short = dataclasses.replace(d.terms.drafting, rows=d.terms.drafting.rows - 1)
        with mock.patch.object(j4labels, "rebuild_drafting", return_value=short):
            err, lines = self.stopped()
        self.assertEqual((err.path, err.problem), ("$.experiments.j4.labels", J4_SPLIT_VIOLATED))
        split = [line for line in lines if line.startswith("j4 split:")]
        self.assertEqual(len(split), 1)
        self.assertIn(f"archives_equal yes term_rows {d.terms.drafting.rows} ", split[0])
        self.assertIn(f"rebuilt_rows {d.terms.drafting.rows - 1} ", split[0])
        self.assertTrue(split[0].endswith("by_record_id none by_text none already_excluded none before_cutoff none "
                                          "in_drafting none"), split[0])
        self.assertFalse(any(line.startswith("j4 draw:") for line in lines))
        self.assert_term_line_last(lines)

    def test_a_draw_that_holds_an_excluded_record_stops_with_the_exclusion_note(self) -> None:
        """Item 5 (f): ``lab.j2labels.build_labels`` refusing a draw that holds an excluded record (an overlap above
        0) with enough records left is J4_EXCLUSION_DISAGREES at the labels, not J4_TOO_FEW_RECORDS."""
        refusal = j2labels.J2LabelsError("the draw holds 1 excluded records")
        with mock.patch.object(j2labels, "build_labels", side_effect=refusal):
            err, lines = self.stopped()
        self.assertEqual((err.path, err.problem), ("$.experiments.j4.labels", notes.J4_EXCLUSION_DISAGREES))
        self.assertIn("j4 draw: the draw holds 1 excluded records", lines)
        self.assert_term_line_last(lines)

    def test_a_drafted_pack_that_does_not_rebuild_stops_before_the_sources(self) -> None:
        """Item 10 (g): P_D rebuilt by this kind's builder must have the four hashes the term check verified; another
        pack's stops the plan job with J4_TERMS_UNVERIFIED, the term line its only line."""
        with mock.patch.object(lab_j4, "drafted_pack", return_value=PACK):
            err, lines = self.stopped()
        self.assertEqual((err.path, err.problem), ("$.experiments.j4.terms", J4_TERMS_UNVERIFIED))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("j4 terms: outcome verified failed none "), lines[0])

    def test_a_straddling_complaint_no_earlier_draw_holds_is_removed_by_record_id(self) -> None:
        """C4 on the console: an archive with two more complaints straddling the cutoff, whose later rows no earlier
        draw holds: the split's line prints two removals by record id beside the one by text, the draw line 453
        excluded. The archives differ from the term run's, so both drafting sets are used together and nothing
        stops."""
        export = lab_goldlabels.nhtsa_export()
        new = {"7900098": "The hood latch rattled loose twice on the gravel road near the farm.",
               "7900099": "The rear hatch strut gave way and the lid dropped onto the loading bay."}
        with zipfile.ZipFile(io.BytesIO(ARCHIVE)) as z:
            rows = z.read("CMPL.txt").decode("latin-1")
        extra = [j4_data._row(export, ref, "FORD", "ENGINE", "OH", day, text, "MODEL Y", "2021")
                 for ref, text in new.items() for day in (datetime.date(2022, 5, 5), datetime.date(2023, 5, 5))]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(zipfile.ZipInfo("CMPL.txt", date_time=j4_data.ZIP_DATE),
                       (rows + "\n".join(extra) + "\n").encode("latin-1"))
        variant = buf.getvalue()
        d = _Data.get()
        self.assertFalse(set(new) & (d.est.excluded | SPECIAL))
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-straddling-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        request = json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))
        request["experiments"] = {"j4": j4_block()}
        path = write_json(tmp / "j4-test.json", request)
        code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "plan")])
        self.assertEqual(code, 0, err)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            manifest = lab_prereg.preregister(tmp / "plan" / "plan.json", nhtsa_fetch=lambda: variant,
                                              earlier_plans=d.dir / "earlier", exclude_stand_in=d.entries,
                                              terms_plans=d.dir / "terms", terms_stand_in=d.term_entry)
        lines = out.getvalue().splitlines()
        split = next(line for line in lines if line.startswith("j4 split:"))
        self.assertIn("archives_equal no ", split)
        self.assertTrue(split.endswith("by_record_id 2 by_text 1 already_excluded 3 before_cutoff 0 in_drafting 0"),
                        split)
        draw = next(line for line in lines if line.startswith("j4 draw:"))
        self.assertIn("available 726 excluded 453 (earlier 450 split_by_record_id 2 split_by_text 1) "
                      "eligible_after_exclusion 273 overlap 0 ", draw)
        j4 = manifest["j4"]
        self.assertEqual((j4["split"]["by_record_id"], j4["split"]["by_text"], j4["exclusion"]["excluded"]),
                         (2, 1, 453))
        chain = {line["record_ref"] for line in read_lines(tmp / "plan" / "prereg" / "j4" / "excluded.jsonl")}
        self.assertLessEqual(set(new), chain)
        self.assertFalse(set(new) & refs_of((tmp / "plan" / "prereg" / "j4" / "labels.jsonl").read_bytes()))
        assert_no_ref(self, out.getvalue(), chain | set(new))

    def test_the_fixed_lines(self) -> None:
        p = _Prereg4.get()
        d = _Data.get()
        lines = p.stdout.splitlines()
        heads = [line.split(":")[0] for line in lines]
        self.assertEqual(heads, ["j4 exclusion", "j4 draw", "j4 split", "j4 terms", "j4 comparators",
                                 "j4 comparators"])
        self.assertEqual(lines[0], "j4 exclusion: j001=artifact a001=artifact a002=artifact consistency j1_r1=0 "
                                   "a1_r2=0 b1_r3=0 outside_k2=0 outside_k3=0 k2_union=0 k3_union=0 excluded 450")
        self.assertIn("available 724 excluded 451 (earlier 450 split_by_record_id 0 split_by_text 1) "
                      "eligible_after_exclusion 273 overlap 0 sources J1=verified R1=verified A1=verified R2=verified "
                      f"K2=verified B1=verified B1_questions_sha256 {d.questions_prefix} R3=verified K3=verified",
                      lines[1])
        self.assertTrue(lines[2].endswith("by_record_id 0 by_text 1 already_excluded 3 before_cutoff 0 in_drafting 0"),
                        lines[2])
        self.assertIn("archives_equal yes", lines[2])
        self.assertTrue(lines[3].startswith("j4 terms: outcome verified failed none run_id "
                                            f"{j4_data.TERMS_RUN_ID} terms_sha256 {d.terms.summary['sha256']}"),
                        lines[3])
        # item 10: the four hashes of P_D the term check verified, each by its name
        self.assertIn(f"expected_sha256 {d.term_entry['sha256']} " + " ".join(
            f"p_d_{key} {d.terms.p_d[key]}" for key in ("config_hash", "vocabulary_hash", "detector_hash",
                                                        "fixtures_hash")) + f" jt_code_hash {JT_CODE_HASH} ", lines[3])
        self.assertEqual(sorted(d.terms.p_d), sorted(lab_prereg.P_D_HASH_KEYS))
        self.assertTrue(lines[3].endswith("stand_in yes"))
        self.assertEqual([part.split(" ")[0] for part in lines[4][len("j4 comparators: "):].split("; ")],
                         ["lexical_drafted", "lexical", "presence_drafted"])
        self.assertTrue(lines[5].startswith("j4 comparators: record_blind balanced_accuracy "))
        for name, _, _ in lab_j4.JUDGE_PAIRS:
            self.assertIn(f" {name} ", lines[5] + " ")
        refs = refs_of((p.dir / "labels.jsonl").read_bytes()) | {
            line["record_ref"] for line in read_lines(p.dir / "excluded.jsonl")} | d.terms.drafting.refs
        assert_no_ref(self, p.stdout, refs)
        assert_no_term(self, p.stdout, d.all_terms)

    def test_a_term_artifact_that_does_not_verify_stops_before_any_draw(self) -> None:
        """Item 10: the term check is the first step; its failure prints the term line and refuses the plan with
        J4_TERMS_UNVERIFIED before the sources are read or anything is drawn."""
        d = _Data.get()
        tmp = Path(tempfile.mkdtemp(prefix="lab-j4-unverified-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        request = json.loads(PLUMBING_MIN.read_text(encoding="utf-8"))
        request["experiments"] = {"j4": j4_block()}
        path = write_json(tmp / "j4-test.json", request)
        code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "plan")])
        self.assertEqual(code, 0, err)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(lab_prereg._StepFailed) as caught:
            lab_prereg.preregister(tmp / "plan" / "plan.json", nhtsa_fetch=lambda: ARCHIVE,
                                   earlier_plans=d.dir / "earlier", exclude_stand_in=d.entries,
                                   terms_plans=d.dir / "terms", terms_stand_in={**d.term_entry, "sha256": "0" * 64})
        self.assertEqual((caught.exception.path, caught.exception.problem), ("$.experiments.j4.terms",
                                                                              notes.J4_TERMS_UNVERIFIED))
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertTrue(out.getvalue().startswith("j4 terms: outcome sha256 mismatch failed terms_sha256 "))
        self.assertFalse((tmp / "plan" / "prereg" / "j4").exists())


class AggregateWiringTests(unittest.TestCase):
    """DryTree copies of the small dry run, changed and re-sealed as a real run would have left them: the verdict
    words of C19 with the word under lever 5's text alone (C6), the lifts, the stop that spares the first pass, and
    the manifest cross-checked against ``prereg/j4/prereg.json`` and the term set's pin."""

    def setUp(self) -> None:
        self.dry = _DryRun4.get("small")
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j4-agg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = DryTree(self.dry.out, self.tmp / "T", manifest=LAB_MANIFEST)
        self.prereg_dir = self.dry.out / "plan" / "prereg" / "j4"
        self.questions = lab_j1.read_questions((self.prereg_dir / "questions.jsonl").read_bytes())
        self.texts = payload_texts(self.prereg_dir)
        self.T = lab_j4.read_terms((self.prereg_dir / "terms.json").read_bytes())[1]

    def shard_of(self, model: str, part: int) -> tuple[str, dict[str, Any]]:
        unit = next(u for u in self.tree.plan["units"] if u["unit"] == f"j4-{model}-p{part}")
        return next(s["shard"] for s in self.tree.plan["shards"] if unit["unit"] in s["units"]), unit

    def make_measured(self, *models: str) -> None:
        for model in models:
            shards: dict[str, list[str]] = {}
            for part in (1, 2):
                shard, unit = self.shard_of(model, part)
                rel = f"runs/j4/{unit['run_id']}/run.json"
                doc = self.tree.read(shard, rel)
                doc["measurement"] = True
                self.tree.replace_unit_file(shard, unit["unit"], rel, canonical_bytes(doc) + b"\n")
                shards.setdefault(shard, []).append(unit["unit"])
            for shard, unit_ids in shards.items():
                self.tree.make_real(shard, model_units=tuple(unit_ids))

    def restamp(self, model: str, part: int, *, lines: Callable[[list[dict[str, Any]]], None] | None = None,
                run: Callable[[dict[str, Any]], None] | None = None,
                unit_record: Callable[[dict[str, Any]], None] | None = None) -> None:
        shard, unit = self.shard_of(model, part)
        rel = f"runs/j4/{unit['run_id']}/verdicts.jsonl"
        got = read_lines(self.tree.path(shard, rel))
        if lines is not None:
            lines(got)
        data = jsonl(got)
        self.tree.replace_unit_file(shard, unit["unit"], rel, data)
        run_rel = f"runs/j4/{unit['run_id']}/run.json"
        doc = self.tree.read(shard, run_rel)
        doc["verdicts_sha256"] = hashlib.sha256(data).hexdigest()
        if run is not None:
            run(doc)
        self.tree.replace_unit_file(shard, unit["unit"], run_rel, canonical_bytes(doc) + b"\n")
        if unit_record is not None:
            self.tree.edit(shard, f"units/{unit['unit']}/unit.json", unit_record)
        self.tree.reseal(shard)

    def correct(self, line: dict[str, Any]) -> None:
        """Rewrite a line as the runner would have written a right answer (both answers yes on a positive, the
        entity yes and the predicate no on a negative), with the list its payload carried for task D."""
        q = next(q for q in self.questions if q["question_id"] == line["question_id"])
        reply = {"mentions_entity": "yes", "describes_predicate": "yes" if line["kind"] == "positive" else "no"}
        terms = lab_j4.predicate_terms(self.T, q["predicate"]) if line["task"] == ND else None
        fresh = lab_j4.verdict_line(q, lab_j1.stand_in(q["record_ref"]), reply, None, line["task"],
                                    self.texts[q["record_ref"]], terms)
        line.clear()
        line.update(fresh)

    def lines_of(self, model: str) -> list[dict[str, Any]]:
        """Every verdict line of the model's two parts, as the tree holds them now."""
        out: list[dict[str, Any]] = []
        for part in (1, 2):
            shard, unit = self.shard_of(model, part)
            out += read_lines(self.tree.path(shard, f"runs/j4/{unit['run_id']}/verdicts.jsonl"))
        return out

    def comparators(self) -> dict[str, list[dict[str, Any]]]:
        return {name: read_lines(self.prereg_dir / f"{name}.jsonl") for name in lab_j4.COMPARATORS}

    def assert_recomputed(self, arm: str, a: dict[str, Any], lines: list[dict[str, Any]]) -> None:
        """Rule 13, C6 and C19 recomputed from the arm's own lines: each paired difference to a lexical judge, the
        pass test's figures (the drafted judge in the second clause, the hand and term-presence judges in the added
        ones), the word and the word under lever 5's text alone."""
        view = lab_j4.arm_lines(lines, arm)
        comparators = self.comparators()
        for name in lab_j4.LEXICAL_JUDGES:
            self.assertEqual(a["paired"][name], lab_j1.paired(self.questions, view, comparators[name], bootstrap_b=1000,
                                                              bootstrap_seed=4), (arm, name))
        test = a["pass_test"]
        self.assertEqual((test["diff_lexical"], test["diff_hand_lexical"], test["diff_term_presence"]),
                         (a["paired"][lab_j4.LEXICAL_DRAFTED]["mean_diff"],
                          a["paired"][lab_j4.LEXICAL_HAND]["mean_diff"],
                          a["paired"][lab_j4.PRESENCE_DRAFTED]["mean_diff"]), arm)
        common = {"complete": a["complete"], "display_class": a["display_class"], "measurement": a["measurement"],
                  "dropped_share": a["dropped_share"], "scores": a["scores"], "bound": a["prior_bound"],
                  "paired": a["paired"][lab_j4.LEXICAL_DRAFTED]}
        self.assertEqual(a["verdict_word"], lab_j4.verdict_word(
            **common, paired_hand=a["paired"][lab_j4.LEXICAL_HAND],
            paired_presence=a["paired"][lab_j4.PRESENCE_DRAFTED])[0], arm)
        self.assertEqual(a["lever5_word"], lab_j2.verdict_word(**common)[0], arm)

    def arms_row(self, md: str, arm: str) -> str:
        letter = lab_j4.ARM_LETTERS[arm]
        return next(line for line in md.splitlines() if line.startswith(f"| `a-4b` | `{arm}` | `{letter}` |"))

    def aggregate(self) -> dict[str, Any]:
        shutil.rmtree(self.tmp / "R", True)
        code, _, err, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, err)
        return report["j4"]

    def test_the_verdict_words_the_added_clauses_and_the_lifts(self) -> None:
        """a-4b, measured, answers task D right on every question and task B as the hand lexical judge: D and Dr pass
        provisionally (above the target, the bound and all three lexical judges), B and Br equal the hand judge, so
        the hand clause fails for them and their word is not a pass whatever lever 5's text alone says, and the drafted
        terms' lift helps."""
        self.make_measured("a-4b")
        for part in (1, 2):
            self.restamp("a-4b", part, lines=lambda ls: [self.correct(x) for x in ls if x["task"] == ND])
        block = self.aggregate()
        arms = block["models"]["a-4b"]["arms"]
        lines = self.lines_of("a-4b")
        for arm, a in arms.items():
            self.assert_recomputed(arm, a, lines)
        for arm in ("negation_terms", "negation_terms_mapped"):
            a = arms[arm]
            self.assertEqual((a["display_class"], a["verdict_word"], a["lever5_word"], a["pass_test"]["passed"]),
                             ("model", "pass_provisional", "pass_provisional", True), arm)
            self.assertEqual(a["scores"]["balanced_accuracy"]["value"], 1.0)
            self.assertEqual(set(a["pass_test"]["clauses"].values()), {True})
        for arm in ("negation", "negation_mapped"):
            a = arms[arm]
            # B and Br answer as the hand judge: they beat the drafted judge below the target, not the hand judge
            self.assertEqual(a["paired"]["lexical"]["mean_diff"], 0.0)
            self.assertIs(a["pass_test"]["clauses"]["beats_hand_lexical"], False)
            self.assertIs(a["pass_test"]["clauses"]["beats_lexical"], True)
            self.assertNotEqual(a["paired"][lab_j4.PRESENCE_DRAFTED]["mean_diff"],
                                a["paired"][lab_j4.LEXICAL_DRAFTED]["mean_diff"])
            self.assertEqual((a["verdict_word"], a["lever5_word"]), ("failure", "partial"), arm)
        primary = block["primary_result"]
        self.assertEqual((primary["arm"], primary["verdict_word"], primary["lever5_word"],
                          primary["other_reading"]["arm"], primary["other_reading"]["verdict_word"]),
                         ("negation_terms", "pass_provisional", "pass_provisional", "negation_terms_mapped",
                          "pass_provisional"))
        lifts = block["models"]["a-4b"]["lifts"]
        self.assertEqual((lifts["D_minus_B"]["reading"], lifts["Dr_minus_Br"]["reading"],
                          lifts["D_minus_B"]["paired"]["n"]), ("helps", "helps", 40))
        small = block["models"]["a-1p5b"]["arms"]
        self.assertEqual({arm: a["verdict_word"] for arm, a in small.items()},
                         dict.fromkeys(lab_j4.ARMS["negation"], "incomplete"))
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("Primary pair `a-4b` `negation_terms`: `pass_provisional`; under lever five's text alone: "
                      "`pass_provisional`.", md)
        for arm in ("negation", "negation_mapped"):
            self.assertIn("| `failure` | `partial` |", self.arms_row(md, arm))

    def test_the_primary_pair_s_two_words_apart(self) -> None:
        """C6 and C19: a-4b measured and answering task D as the hand lexical judge: the primary arm beats the
        drafted judge below the target but not the hand judge, so its word is ``failure`` while the word under lever
        5's text alone is ``partial``; the primary line and the arms table print each word in its own place, and the
        primary line each judge's figures from that judge."""
        self.make_measured("a-4b")
        block = self.aggregate()
        a = block["models"]["a-4b"]["arms"]["negation_terms"]
        self.assert_recomputed("negation_terms", a, self.lines_of("a-4b"))
        self.assertEqual((a["verdict_word"], a["lever5_word"]), ("failure", "partial"))
        result = block["primary_result"]
        self.assertEqual((result["verdict_word"], result["lever5_word"]), ("failure", "partial"))
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("Primary pair `a-4b` `negation_terms`: `failure`; under lever five's text alone: `partial`.", md)
        row = self.arms_row(md, "negation_terms")
        self.assertIn("| `failure` | `partial` |", row)
        # rule 11: the headline against the drafted judge, with the same words against the hand and term-presence
        # judges beside it
        heads = [a["headlines"][name]["headline"] for name in (lab_j4.LEXICAL_DRAFTED, lab_j4.LEXICAL_HAND,
                                                                lab_j4.PRESENCE_DRAFTED)]
        self.assertTrue(all(isinstance(h, str) for h in heads), heads)
        self.assertNotEqual(heads[0], heads[1])
        self.assertIn(" | ".join(f"`{h}`" for h in heads) + " | n/a |", row)

        def interval(x: dict[str, Any], key: str = "value") -> str:
            return f"{x[key]:.3f} [{x['ci_low']:.3f}, {x['ci_high']:.3f}]"

        judges, paired = result["judges"], a["paired"]
        self.assertEqual(judges, {name: a["judges"][name]["balanced_accuracy"] for name in lab_j4.LEXICAL_JUDGES})
        self.assertEqual(len({judges[name]["value"] for name in lab_j4.LEXICAL_JUDGES}), 3)
        self.assertIn(f"drafted lexical judge {interval(judges['lexical_drafted'])} on the same records, paired "
                      f"difference {interval(paired['lexical_drafted'], 'mean_diff')}; hand lexical judge "
                      f"{interval(judges['lexical'])}, paired difference {interval(paired['lexical'], 'mean_diff')}; "
                      f"term presence {interval(judges['presence_drafted'])}, paired difference "
                      f"{interval(paired['presence_drafted'], 'mean_diff')}; ", md)

    def test_a_line_the_runner_could_not_have_written(self) -> None:
        """Rule 16: the aggregate refuses a part whose lines or run.json the runner could not have written: a task D
        line whose term count or term sha256 is not its asked predicate's list in terms.json, a task B line whose
        mapped verdict is not the reading recomputed, a run.json with the passes in the other order. That part is then
        ok for neither task, both sets hold part 2 alone and every word is incomplete; the files put back, the part
        reads ok again."""
        self.make_measured("a-4b")
        shard, unit = self.shard_of("a-4b", 1)
        original = read_lines(self.tree.path(shard, f"runs/j4/{unit['run_id']}/verdicts.jsonl"))
        pass_order = self.tree.read(shard, f"runs/j4/{unit['run_id']}/run.json")["pass_order"]
        self.assertEqual(pass_order, [ND, N])

        def first(lines: list[dict[str, Any]], task: str) -> dict[str, Any]:
            return next(line for line in lines if line["task"] == task)

        def another_list(line: dict[str, Any]) -> list[str]:
            carried = list(lab_j4.predicate_terms(self.T, line["predicate"]))
            return next(list(ts) for _, ts in sorted(self.T.items()) if ts and list(ts) != carried)

        def flip(line: dict[str, Any]) -> None:
            line["mapped_verdict"] = "refute" if line["mapped_verdict"] != "refute" else "confirm"

        cases: dict[str, tuple[Callable[[list[dict[str, Any]]], None] | None,
                               Callable[[dict[str, Any]], None] | None]] = {
            "terms_count": (lambda ls: first(ls, ND).update(terms_count=first(ls, ND)["terms_count"] + 1), None),
            "terms_sha256": (lambda ls: first(ls, ND).update(
                terms_sha256=lab_j4.terms_sha256(another_list(first(ls, ND)))), None),
            "mapped_verdict": (lambda ls: flip(first(ls, N)), None),
            "pass_order": (None, lambda doc: doc.update(pass_order=[N, ND])),
        }
        for name, (lines, run) in cases.items():
            with self.subTest(change=name):
                self.restamp("a-4b", 1, lines=lines, run=run)
                big = self.aggregate()["models"]["a-4b"]
                part = next(p for p in big["parts"] if p["part"] == 1)
                self.assertEqual(part["ok_by_task"], {ND: False, N: False})
                self.assertEqual((big["sets"][ND]["parts_ok"], big["sets"][N]["parts_ok"]), ([2], [2]))
                self.assertEqual({arm: (a["verdict_word"], a["parts_finished"]) for arm, a in big["arms"].items()},
                                 dict.fromkeys(lab_j4.ARMS["negation"], ("incomplete", 1)))
                self.restamp("a-4b", 1, lines=lambda ls: ls.__setitem__(slice(None), [dict(x) for x in original]),
                             run=lambda doc: doc.update(pass_order=list(pass_order)))
                big = self.aggregate()["models"]["a-4b"]
                part = next(p for p in big["parts"] if p["part"] == 1)
                self.assertEqual(part["ok_by_task"], {ND: True, N: True})
                self.assertEqual((big["arms"]["negation_terms"]["verdict_word"],
                                  big["arms"]["negation_terms"]["parts_finished"]), ("failure", 2))

    def test_the_readings_beside_the_verdict(self) -> None:
        """Rule 14 and traps 6 and 11: the agreement counts are the primary arm's verdicts against the drafted
        lexical judge's and the term-presence judge's, per kind; the empty-list count is the questions whose asked
        predicate has no drafted term; the headline is J001's against the drafted judge, the hand and term-presence
        judges' beside it. Rule 9: one record's two task D lines lost to transport failures leave that record out of
        every comparator and the bound (the same records), and withhold the word (one record in forty)."""
        self.make_measured("a-4b")
        big = self.aggregate()["models"]["a-4b"]
        comparators = self.comparators()
        view = lab_j4.arm_lines(self.lines_of("a-4b"), "negation_terms")
        self.assertEqual(big["agreement"]["arm"], "negation_terms")
        for name in (lab_j4.LEXICAL_DRAFTED, lab_j4.PRESENCE_DRAFTED):
            judge = {line["question_id"]: line["verdict"] for line in comparators[name]}
            for kind in lab_j1.KINDS:
                mine = [line for line in view if line["kind"] == kind and line["scored"] is True]
                got = big["agreement"]["judges"][name][kind]
                self.assertEqual((got["questions"], got["equal"]),
                                 (len(mine), sum(1 for line in mine if line["verdict"] == judge[line["question_id"]])),
                                 (name, kind))
        drafted = big["agreement"]["judges"][lab_j4.LEXICAL_DRAFTED]
        self.assertLess(sum(drafted[kind]["equal"] for kind in lab_j1.KINDS),
                        sum(drafted[kind]["questions"] for kind in lab_j1.KINDS))
        empty = sum(1 for q in self.questions if not self.T.get(q["predicate"]))
        self.assertEqual((big["empty_terms"]["questions"], big["empty_terms"]["empty"]), (len(self.questions), empty))
        self.assertTrue(0 < empty < len(self.questions))
        a = big["arms"]["negation_terms"]
        self.assertEqual(sorted(a["headlines"]), sorted(lab_j4.LEXICAL_JUDGES))
        for name in lab_j4.LEXICAL_JUDGES:
            self.assertEqual(a["headlines"][name]["headline"], lab_j1.headline_for(
                complete=True, display_class="model", measurement=True, dropped_share=a["dropped_share"],
                model_ba=a["scores"]["balanced_accuracy"],
                lexical_ba=a["judges"][name]["balanced_accuracy"]["value"])[0], name)
        self.assertEqual(a["headline"], a["headlines"][lab_j4.LEXICAL_DRAFTED]["headline"])
        self.assertNotEqual(a["headline"], a["headlines"][lab_j4.LEXICAL_HAND]["headline"])
        # transport failures: one record of part 1 whose loss moves the bound
        everyone = sorted({q["record_ref"] for q in self.questions})
        ref = next(r for r in sorted({q["record_ref"] for q in self.questions if q["part"] == 1})
                   if lab_j1.prior_bound(self.questions, [x for x in everyone if x != r])
                   != lab_j1.prior_bound(self.questions))
        kept = [x for x in everyone if x != ref]

        def fail(lines: list[dict[str, Any]]) -> None:
            for i, line in enumerate(lines):
                if line["record_ref"] == ref and line["task"] == ND:
                    q = next(q for q in self.questions if q["question_id"] == line["question_id"])
                    lines[i] = lab_j4.verdict_line(q, lab_j1.stand_in(ref), None, "network", ND, self.texts[ref],
                                                   lab_j4.predicate_terms(self.T, q["predicate"]))

        self.restamp("a-4b", 1, lines=fail)
        big = self.aggregate()["models"]["a-4b"]
        part = next(p for p in big["parts"] if p["part"] == 1)
        self.assertEqual(part["ok_by_task"], {ND: True, N: True})
        a = big["arms"]["negation_terms"]
        self.assertEqual((a["records_dropped"], a["withheld_reason"], a["verdict_word"], a["lever5_word"],
                          a["headline"]), (1, notes.J1_WITHHELD, "incomplete", "incomplete", None))
        self.assertGreater(a["dropped_share"], lab_j1.WITHHOLD_SHARE)
        self.assertEqual({name: a["judges"][name]["records_scored"] for name in lab_j4.COMPARATORS},
                         dict.fromkeys(lab_j4.COMPARATORS, 39))
        self.assertEqual(a["prior_bound"], lab_j1.prior_bound(self.questions, kept))
        self.assertEqual(big["arms"]["negation"]["records_dropped"], 0)
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")

    def test_a_stop_in_the_second_pass(self) -> None:
        """Rule 15 in the aggregate: a part whose task B pass stopped keeps task D's set complete (P gets its word)
        and leaves B's incomplete, with the controls and the lifts that need B."""
        self.make_measured("a-4b")

        def drop_b(lines: list[dict[str, Any]]) -> None:
            lines[:] = [line for line in lines if line["task"] == ND]

        self.restamp("a-4b", 2, lines=drop_b,
                     run=lambda doc: doc.update(complete=False, stopped="budget", stopped_task=N,
                                                complete_by_task={ND: True, N: False}, questions_done=40,
                                                questions_done_by_task={ND: 40, N: 0}),
                     unit_record=lambda r: r.update(status="ok", status_reason=notes.J4_TERMS_STOPPED, exit_code=1))
        block = self.aggregate()
        big = block["models"]["a-4b"]
        part = next(p for p in big["parts"] if p["part"] == 2)
        self.assertEqual((part["status"], part["status_reason"], part["ok_by_task"], part["stopped_task"]),
                         ("ok", notes.J4_TERMS_STOPPED, {ND: True, N: False}, N))
        self.assertEqual((big["sets"][ND]["complete"], big["sets"][N]["complete"], big["sets"][N]["parts_ok"]),
                         (True, False, [1]))
        self.assertEqual((big["arms"]["negation_terms"]["complete"], big["arms"]["negation"]["complete"],
                          big["arms"]["negation"]["verdict_word"]), (True, False, "incomplete"))
        self.assertEqual(big["arms"]["negation_terms"]["verdict_word"], "failure")
        self.assertEqual({name: entry["complete"] for name, entry in big["lifts"].items()},
                         {"D_minus_B": False, "Dr_minus_Br": False, "Dr_minus_D": True, "Br_minus_B": False})
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")

    def test_a_manifest_that_disagrees_with_the_preregistration(self) -> None:
        """The manifest ``prereg/prereg.json`` must agree with ``prereg/j4/prereg.json`` on the base task, the
        primary pair, the headline arm and secondary, the other reading's arm and the term set's pin, and the term
        set's bytes must hash to that pin: each disagreement refuses the block with PREREG_MISSING; the files put
        back, the block is read again."""
        plan_dir = self.tree.root / "plan"
        manifest_path = plan_dir / "prereg" / "prereg.json"
        pristine = manifest_path.read_bytes()
        block = self.aggregate()
        self.assertEqual((block["reason"], block["primary_result"]["arm"], sorted(block["models"])),
                         (None, "negation_terms", ["a-1p5b", "a-4b"]))
        edits: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            ("primary.arm", lambda j4: j4["primary"].update(arm="negation_terms_mapped")),
            ("primary.model", lambda j4: j4["primary"].update(model="a-1p5b")),
            ("base_task", lambda j4: j4.update(base_task="evidence")),
            ("headline_secondary", lambda j4: j4.update(headline_secondary="negation")),
            ("other_reading_arm", lambda j4: j4.update(other_reading_arm="negation")),
            ("terms.sha256", lambda j4: j4["terms"].update(sha256="0" * 64)),
            ("confirmation", lambda j4: j4.update(confirmation=True)),
            ("comparisons_this_cycle", lambda j4: j4.update(comparisons_this_cycle=4))]
        for name, change in edits:
            with self.subTest(edit=name):
                manifest = json.loads(pristine)
                change(manifest["j4"])
                manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
                block = self.aggregate()
                self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                                 (lab_aggregate.PREREG_MISSING, {}, None))
        manifest_path.write_bytes(pristine)
        # P_D rebuilt from terms.json must have the four hashes preregistered (rule 16, item 9)
        hand = loader.load_pack(ROOT / NHTSA_PACK)
        with mock.patch.object(lab_j4, "drafted_pack", lambda entries, hand_dir=None: hand):
            block = self.aggregate()
        self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                         (lab_aggregate.PREREG_MISSING, {}, None))
        doc_rel = "prereg/j4/prereg.json"
        doc_pristine = (plan_dir / doc_rel).read_bytes()
        terms_rel = lab_j4.TERMS_FILE
        terms_pristine = (plan_dir / terms_rel).read_bytes()

        def with_doc(change: Callable[[dict[str, Any]], None]) -> None:
            doc = json.loads(doc_pristine)
            change(doc)
            restamp(plan_dir, doc_rel, canonical_bytes(doc) + b"\n")

        def with_manifest(change: Callable[[dict[str, Any]], None]) -> None:
            manifest = json.loads(manifest_path.read_bytes())
            change(manifest)
            manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")

        other_terms = terms_pristine + b"\n"

        def terms_and_pin() -> None:
            # terms.json one byte longer, its file entry and the manifest's pin restamped; the document untouched
            restamp(plan_dir, terms_rel, other_terms)
            with_manifest(lambda m: m["j4"]["terms"].update(sha256=sha256_hex(other_terms)))

        def both_counts() -> None:
            # the document and the manifest agree on 4 comparisons, but the plan has two models of four arms
            with_doc(lambda doc: doc.update(comparisons_this_cycle=4))
            with_manifest(lambda m: m["j4"].update(comparisons_this_cycle=4))

        for name, change in (("the document's term pin", lambda: with_doc(lambda doc: doc["terms"].update(
                                 sha256="0" * 64))),
                             ("the document's comparisons", lambda: with_doc(lambda doc: doc.update(
                                 comparisons_this_cycle=4))),
                             ("terms.json and the manifest's pin", terms_and_pin),
                             ("both counts", both_counts)):
            with self.subTest(change=name):
                change()
                block = self.aggregate()
                self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                                 (lab_aggregate.PREREG_MISSING, {}, None))
                (plan_dir / doc_rel).write_bytes(doc_pristine)
                (plan_dir / terms_rel).write_bytes(terms_pristine)
                manifest_path.write_bytes(pristine)
        for rel, mutate in (("prereg/j4/lexical_drafted.jsonl", "line"), ("prereg/j4/terms.json", "bytes")):
            with self.subTest(file=rel):
                original = (plan_dir / rel).read_bytes()
                if mutate == "line":
                    lines = read_lines(plan_dir / rel)
                    lines[0]["verdict"] = "refute" if lines[0]["verdict"] != "refute" else "confirm"
                    data = jsonl(lines)
                else:
                    data = original + b"\n"
                restamp(plan_dir, rel, data)
                self.assertEqual(lab_prereg.load_prereg(self.tree.plan_path).manifest["files"][rel]["sha256"],
                                 sha256_hex(data))
                block = self.aggregate()
                self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                                 (lab_aggregate.PREREG_MISSING, {}, None))
                restamp(plan_dir, rel, original)
        block = self.aggregate()
        self.assertEqual((block["reason"], block["primary_result"]["arm"]), (None, "negation_terms"))
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")

    def test_without_the_preregistration(self) -> None:
        (self.tree.root / "plan" / "prereg" / "prereg.json").unlink()
        block = self.aggregate()
        self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                         (lab_aggregate.PREREG_MISSING, {}, None))
        md, sources = lab_summary.render_report(self.tmp / "R")
        self.assertIn(lab_aggregate.PREREG_MISSING, md)
        check_sources(self, md, sources, self.tmp / "R")


class _DryRunChecks:
    """The checks both dry runs share: every unit ran on the fake provider, the report's ``j4`` block, every number of
    both summaries traced to a run file, and no ref and no term in anything a reader sees."""

    name = ""
    units: list[str] = []
    n = 0
    parts = 0
    models: list[str] = []
    before = 21

    def dry(self) -> _DryRun4:
        return _DryRun4.get(self.name)

    def test_the_console(self: Any) -> None:
        dry = self.dry()
        self.assertEqual(dry.code, 0, dry.stdout + dry.stderr)
        lines = dry.stdout.splitlines()
        self.assertIn(f"plan: {len(self.units)} units in {len(dry.plan['shards'])} shards (real)", lines)
        self.assertIn("j4 exclusion: j001=artifact a001=artifact a002=artifact consistency j1_r1=0 a1_r2=0 b1_r3=0 "
                      "outside_k2=0 outside_k3=0 k2_union=0 k3_union=0 excluded 450", lines)
        self.assertIn("prereg: e1 no x1 no e2 no j1 no j4 yes", lines)
        draw = next(line for line in lines if line.startswith("j4 draw: "))
        self.assertIn("eligible_after_exclusion 273 overlap 0 sources J1=verified R1=verified A1=verified "
                      f"R2=verified K2=verified B1=verified B1_questions_sha256 {dry.questions_prefix} R3=verified "
                      "K3=verified", draw)
        split = next(line for line in lines if line.startswith("j4 split: "))
        self.assertTrue(split.endswith("by_record_id 0 by_text 1 already_excluded 3 before_cutoff 0 in_drafting 0"))
        terms = next(line for line in lines if line.startswith("j4 terms: "))
        self.assertTrue(terms.startswith("j4 terms: outcome verified failed none "), terms)
        self.assertIn(" ".join(f"p_d_{key} {dry.term_check.p_d[key]}" for key in lab_prereg.P_D_HASH_KEYS), terms)
        comparators = [line for line in lines if line.startswith("j4 comparators: ")]
        self.assertEqual(len(comparators), 2)
        self.assertEqual([part.split(" ")[0] for part in comparators[0][len("j4 comparators: "):].split("; ")],
                         ["lexical_drafted", "lexical", "presence_drafted"])
        # the shards' own lines ("lab: unit ... status ok class plumbing") come from their subprocesses; the report
        # holds the same statuses
        self.assertEqual({u["unit"]: (u["status"], u["display_class"]) for u in dry.report["units"]},
                         {unit: ("ok", "plumbing") for unit in self.units})
        self.assertEqual(sorted(u["unit"] for u in dry.plan["units"]), self.units)

    def test_the_report_block(self: Any) -> None:
        dry = self.dry()
        j4 = dry.report["j4"]
        manifest = dry.manifest["j4"]
        self.assertEqual((j4["label"], j4["reason"], j4["display_class"], j4["stand_in"], j4["terms_stand_in"],
                          sorted(j4["models"]), j4["base_task"], j4["confirmation"]),
                         ("public_nhtsa", None, "plumbing", True, True, self.models, "negation", False))
        self.assertEqual((j4["labels"]["records"], j4["labels"]["available"], j4["questions"]["questions"],
                          j4["questions"]["parts"]), (self.n, 724, 2 * self.n, self.parts))
        this_cycle = len(lab_j4.ARMS["negation"]) * len(self.models)
        self.assertEqual(j4["comparisons"], {"this_cycle": this_cycle, "before": self.before,
                                             "since_loop_began": self.before + this_cycle})
        prereg_doc = read_json(dry.out / "plan" / "prereg" / "j4" / "prereg.json")
        self.assertEqual((dry.manifest["j4"]["comparisons_this_cycle"], prereg_doc["comparisons_this_cycle"]),
                         (this_cycle, this_cycle))
        # rule 14: A001's own figures for the controls, as its record holds them
        self.assertEqual(j4["earlier"], {
            "cycle": "A001", "run_id": 38114522160,
            "B": {"arm": "negation", "balanced_accuracy": 0.577, "ci_low": 0.530, "ci_high": 0.623},
            "Br": {"arm": "negation_mapped", "balanced_accuracy": 0.653, "ci_low": 0.610, "ci_high": 0.697}})
        # the negated variant: the drafted lexical judge and the drafted term-presence judge answer apart
        self.assertNotEqual(j4["judge_pairs"]["presence_minus_drafted"]["mean_diff"], 0)
        self.assertEqual((j4["primary"], j4["headline_arm"], j4["headline_secondary"], j4["other_reading_arm"],
                          j4["target_balanced_accuracy"]),
                         ({"model": "a-4b", "arm": "negation_terms"}, "negation_terms", "negation_terms_mapped",
                          "negation_terms_mapped", 0.86))
        ex = j4["exclusion"]
        self.assertEqual((ex["excluded"], ex["eligible_after_exclusion"], ex["overlap"]), (451, 273, 0))
        self.assertEqual({s: ex["sources"][s]["outcome"] for s in j4labels.SOURCES},
                         dict.fromkeys(j4labels.SOURCES, "verified"))
        self.assertEqual(set(ex["consistency"].values()), {0})
        self.assertEqual((j4["split"]["by_record_id"], j4["split"]["by_text"], j4["split"]["tripwires"]),
                         (0, 1, {"before_cutoff": 0, "in_drafting": 0}))
        self.assertEqual(j4["chain_lines"], 451 + self.n)
        self.assertEqual(len(dry.excluded), 451 + self.n)
        self.assertEqual((j4["terms"]["verified"], j4["terms"]["sha256"], j4["terms"]["jt_code_hash"]),
                         (True, manifest["terms"]["sha256"], JT_CODE_HASH))
        self.assertEqual(sum(j4["terms"]["terms_by_predicate"].values()), len(dry.terms))
        for name in lab_j4.COMPARATORS:
            self.assertEqual(j4["judges"][name], manifest[name], name)
        self.assertEqual((j4["unknown"], j4["judge_pairs"], j4["prior_bound"]),
                         (manifest["unknown"], manifest["judge_pairs"], manifest["prior_bound"]))
        hand = manifest["lexical"]
        for name, entry in j4["models"].items():
            with self.subTest(model=name):
                self.assertEqual(sorted(entry["sets"]), sorted(lab_j4.tasks("negation")))
                for arm in lab_j4.ARMS["negation"]:
                    a = entry["arms"][arm]
                    self.assertEqual((a["letter"], a["task"], a["reading"], a["complete"], a["parts_finished"],
                                      a["parts_planned"], a["display_class"], a["verdict_word"], a["lever5_word"],
                                      a["records_dropped"]),
                                     (lab_j4.ARM_LETTERS[arm], lab_j4.ARM_TASK[arm], lab_j4.ARM_READING[arm], True,
                                      self.parts, self.parts, "plumbing", "incomplete", "incomplete", 0), arm)
                    # the fake answers both tasks as the hand lexical judge: every arm is that judge, line for line
                    for metric in lab_j1.METRICS:
                        self.assertEqual(a["scores"][metric], hand[metric], (arm, metric))
                    self.assertEqual((a["paired"]["lexical"]["mean_diff"], a["paired"]["lexical"]["n"]),
                                     (0.0, self.n))
                    self.assertEqual(a["primary"], name == "a-4b" and arm == "negation_terms")
                    self.assertFalse(a["pass_test"]["passed"])
                self.assertEqual({lift: (e["reading"], e["paired"]["n"], e["paired"]["mean_diff"])
                                  for lift, e in entry["lifts"].items()},
                                 {lift: ("not_told_apart", self.n, 0.0) for lift in lab_summary.J4_LIFTS})
                self.assertEqual((entry["empty_terms"]["questions"], entry["citations"]), (2 * self.n, None))
                earlier = entry["earlier"]
                self.assertEqual((earlier["cycle"], earlier["run_id"], earlier["label"]),
                                 ("A001", 38114522160, "between_draw"))
                for arm, letter, then in (("negation", "B", (0.577, 0.530, 0.623)),
                                          ("negation_mapped", "Br", (0.653, 0.610, 0.697))):
                    e = earlier["arms"][arm]
                    now = entry["arms"][arm]["scores"]["balanced_accuracy"]["value"]
                    self.assertEqual((e["letter"], e["earlier_arm"], e["earlier_balanced_accuracy"],
                                      e["earlier_ci_low"], e["earlier_ci_high"], e["balanced_accuracy"]),
                                     (letter, arm, *then, now))
                    self.assertEqual(e["difference"], now - then[0])      # this draw less the earlier one
                for task in lab_j4.tasks("negation"):
                    self.assertEqual((entry["tokens"][task]["attempts"], entry["tokens"][task]["calls_ok"]),
                                     (2 * self.n, 2 * self.n))
        primary = j4["primary_result"]
        self.assertEqual((primary["model"], primary["arm"], primary["verdict_word"], primary["lever5_word"],
                          primary["display_class"], primary["headline"], primary["other_reading"]["arm"],
                          primary["other_reading"]["verdict_word"]),
                         ("a-4b", "negation_terms", "incomplete", "incomplete", "plumbing", None,
                          "negation_terms_mapped", "incomplete"))

    def test_the_summaries(self: Any) -> None:
        dry = self.dry()
        root = dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        self.assertEqual(md.splitlines()[0], notes.NO_MEASUREMENT_LINE)
        for heading in ("j4", "j4-arms", "j4-lifts", "j4-judges", "j4-agreement", "j4-terms", "j4-tasks",
                        "j4-earlier"):
            self.assertIn(f"#### {notes.HEADINGS[heading]}", md)
        self.assertNotIn(notes.HEADINGS["j4-citations"], md)
        for note in (notes.J4_STAND_IN, notes.J4_ARMS_NOTE, notes.J4_VERDICT_NOTE, notes.J4_EXCLUSION_NOTE,
                     notes.J4_TERMS_NOTE):
            self.assertIn(note, md)
        self.assertIn("J4 here asks one model", md.replace("`", ""))
        self.assertIn("Primary pair `a-4b` `negation_terms`: `incomplete`; under lever five's text alone: "
                      "`incomplete`. It scored balanced accuracy", md)
        self.assertIn("Under the other reading of the standing default the primary would be `negation_terms_mapped`: "
                      "`incomplete`.", md)
        this_cycle = 4 * len(self.models)
        self.assertIn(f"- comparisons since the loop began: {self.before + this_cycle} ({this_cycle} this cycle, "
                      f"{self.before} before)", md)
        self.assertIn(f"| `a-4b` | `negation_terms` | `D` | `{ND}` | `shipped` | `plumbing` | {self.parts} of "
                      f"{self.parts} |", md)
        self.assertIn(f"| `a-4b` | `D_minus_B` | `drafted_terms` | {self.n} | 0.000 |", md)
        self.assertIn("- eligible records 724; excluded records 451; eligible after exclusion 273; overlap with the "
                      "excluded set 0", md)
        sources_line = ("- sources: `J1` `verified` (150 refs); `R1` `verified` (150 refs); `A1` `verified` (150 "
                        "refs); `R2` `verified` (150 refs); `K2` `verified` (300 refs); `B1` `verified` (150 refs; "
                        f"questions sha `{dry.questions_prefix}`); `R3` `verified` (150 refs); `K3` `verified` (450 "
                        "refs)")
        self.assertIn(sources_line, md)
        # the primary line's figures, each judge's from its own entry: the drafted lexical judge's and the
        # term-presence judge's differ on the negated variant
        result = dry.report["j4"]["primary_result"]
        paired = dry.report["j4"]["models"]["a-4b"]["arms"]["negation_terms"]["paired"]

        def interval(x: dict[str, Any], key: str = "value") -> str:
            return f"{x[key]:.3f} [{x['ci_low']:.3f}, {x['ci_high']:.3f}]"

        judges = result["judges"]
        self.assertNotEqual(judges["lexical_drafted"]["value"], judges["presence_drafted"]["value"])
        self.assertIn(f"drafted lexical judge {interval(judges['lexical_drafted'])} on the same records, paired "
                      f"difference {interval(paired['lexical_drafted'], 'mean_diff')}; hand lexical judge "
                      f"{interval(judges['lexical'])}, paired difference {interval(paired['lexical'], 'mean_diff')}; "
                      f"term presence {interval(judges['presence_drafted'])}, paired difference "
                      f"{interval(paired['presence_drafted'], 'mean_diff')}; ", md)
        self.assertIn("- removed by record id 0; removed by text 1; drafting records already excluded 3; drawn records "
                      "received before the cutoff 0; drawn drafting records 0", md)
        self.assertIn(f"chain file lines {451 + self.n}", md)
        self.assertIn("| `a-4b` | `negation` | `B` |", md)
        sources = read_json(root / "report.sources.json")["sources"]
        check_sources(self, md, sources, root)
        plan_md = (dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        plan_sources = read_json(dry.out / "plan" / "summary.sources.json")
        check_sources(self, plan_md, plan_sources["sources"], dry.out / "plan")
        self.assertIn(f"`J4` labels: source `nhtsa`, records {self.n}", plan_md)
        self.assertIn(sources_line, plan_md)
        self.assertIn("`J4` term set sha `" + dry.manifest["j4"]["terms"]["sha256"] + "`", plan_md)
        for note in (notes.J4_STAND_IN, notes.J4_EXCLUSION_NOTE, notes.J4_TERMS_NOTE):
            self.assertIn(note, plan_md)

    def test_no_ref_and_no_term_anywhere_a_reader_looks(self: Any) -> None:
        """No drawn, excluded or drafting record id and no drafted term in the console, the summaries, their
        sources or the report; and, over the whole output tree, refs and terms only in the plan artifact's data files
        that hold them by design and the shards' verdict lines (refs only)."""
        dry = self.dry()
        refs = dry.drawn | dry.excluded | dry.drafting | dry.archive_refs
        self.assertGreater(len(refs), 451)
        self.assertLessEqual(dry.drawn | dry.excluded | dry.drafting, dry.archive_refs)
        self.assertGreater(len(dry.terms), 20)
        for name, text in dry.public_texts().items():
            with self.subTest(file=name):
                assert_no_ref(self, text, refs)
                assert_no_term(self, text, dry.terms)
        holders = set()
        for path in sorted(p for p in dry.out.rglob("*") if p.is_file()):
            # the runner's own values (lab.hostinfo's ENV_NAMES, recorded verbatim in every shard's provenance.json)
            # are names the runner gives, such as the branch in GITHUB_REF, never record text: CI's run of
            # 2026-10-15 found a synthetic term inside the deployment branch's name, as test_lab_jt's scan did
            text = without_runner_values(path.read_text(encoding="utf-8", errors="replace"))
            low = text.lower()
            has_ref = any(re.search(r"(?<![0-9A-Za-z])" + re.escape(r) + r"(?![0-9A-Za-z])", text)
                          for r in refs if r in text)
            has_term = any(re.search(r"(?<![a-z])" + re.escape(t.lower()) + r"(?![a-z])", low)
                           for t in dry.terms if t.lower() in low)
            if has_ref or has_term:
                rel = path.relative_to(dry.out).as_posix()
                holders.add(re.sub(r"\Ashards/[^/]+/runs/j4/[^/]+/", "shards/*/runs/j4/*/", rel))
        allowed = {f"plan/prereg/j4/{name}" for name in (
            "labels.jsonl", "questions.jsonl", "excluded.jsonl", "terms.json",
            *(f"{c}.jsonl" for c in lab_j4.COMPARATORS))} | {"shards/*/runs/j4/*/verdicts.jsonl"}
        self.assertLessEqual(holders, allowed)


class SmallDryRunTests(_DryRunChecks, unittest.TestCase):
    """The small dry run: two gguf models on the fake provider, 40 records in 2 parts, the test data standing in for
    the three excluded runs and the term run."""

    name = "small"
    units = ["j4-a-1p5b-p1", "j4-a-1p5b-p2", "j4-a-4b-p1", "j4-a-4b-p2"]
    n = 40
    parts = 2
    models = ["a-1p5b", "a-4b"]


class TemplateDryRunTests(_DryRunChecks, unittest.TestCase):
    """``lab.dryrun`` of the template the run copies to ``lab/requests/accuracy-003.json``: 5 units in 5 shards, the
    term stand-in verified, every one of the eight sources verified on the stand-in, the split's line with its two
    removal counts and its tripwires at 0, 273 eligible after exclusion, overlap 0, the three lexical judges, every
    unit ok and plumbing, every word incomplete, 25 comparisons, and no ref and no term anywhere a reader looks."""

    name = "template"
    units = [f"j4-a-4b-p{k}" for k in range(1, 6)]
    n = 150
    parts = 5
    models = ["a-4b"]

    def test_the_plan(self) -> None:
        dry = self.dry()
        self.assertEqual([(s["model"], s["units"]) for s in dry.plan["shards"]],
                         [("a-4b", [f"j4-a-4b-p{k}"]) for k in range(1, 6)])
        self.assertEqual(dry.report["j4"]["comparisons"], {"this_cycle": 4, "before": 21, "since_loop_began": 25})
        # the template's own draw and questions (C5: labels seed 4, 150 records, block seed 4, 5 parts), recomputed
        # from the dry data
        blob = (dry.data / "complaints.zip").read_bytes()
        used = [{k: v for k, v in e.items() if k != "questions_sha256_prefix"} for e in dry.entries]
        est = j4labels.establish(dry.data / "earlier", used[0], used[1], used[2], lambda: blob,
                                 a002_questions_prefix=dry.questions_prefix)
        draw = j4labels.draw(pack_id=NHTSA_PACK, n=150, seed=4, fetch=lambda: blob, est=est,
                             term=dry.term_check.drafting,
                             term_archive_sha256=dry.term_check.summary["archive_sha256"], used=used,
                             requested=used, stand_in=True)
        prereg = dry.out / "plan" / "prereg" / "j4"
        self.assertEqual((prereg / "labels.jsonl").read_bytes(), draw.data)
        self.assertEqual((prereg / "questions.jsonl").read_bytes(),
                         lab_j1.build_questions(PACK, draw.data, seed=4, parts=5)[0])

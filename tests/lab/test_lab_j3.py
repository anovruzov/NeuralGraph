"""Accuracy test A002 (``lab.j3`` and ``lab.j3labels``, the lab's ``j3`` experiment; ``docs/collective/A002/CHOICE-A002.md``
as amended B1 to B25): the rule's constants pinned against its text, the three tasks and their schemas, g, G, M and
the composite with both ablations, the verdict lines and the nine arms' views, the line check (B19, B23), the verdict
words with the ablation clauses (B8), the five sources and the stop rules on synthetic artifacts (B3, B5), the labels
record (B22), the request block and the template, the preregistration with its stop rules, the runner through the
repo's fake model server (task E first, rule 14), the unit status reading, the aggregate's words and lifts, the fake
provider's answers to tasks E and R, the warm-up's three tasks (B4) and two dry runs. Nothing here measures a model:
every server is the collective's fake, every complaint archive is built in the test (``tests.lab.j3_data`` over
``tests.lab.j2_data``: every value invented), no earlier run's artifact is downloaded and no NHTSA host is reached.

Three fixtures are made once: a J3 plan of two fake models preregistered against two synthetic earlier artifacts
(:class:`_Prereg`: both artifacts verify, neither rebuild does, the request carrying the real runs' rebuild
prefixes), a small dry run of two gguf models on the fake provider with the test data (:class:`_Small`, the
aggregate's cases are :class:`~tests.lab.helpers.DryTree` copies of it) and the template's dry run (:class:`_Template`).
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

from lab import aggregate as lab_aggregate
from lab import dryrun as lab_dryrun
from lab import goldlabels
from lab import j1 as lab_j1
from lab import j2 as lab_j2
from lab import j2labels
from lab import j3 as lab_j3
from lab import j3labels
from lab import notes
from lab import plan as lab_plan
from lab import prereg as lab_prereg
from lab import request as lab_request
from lab import summary as lab_summary
from lab import units
from lab import warmup as lab_warmup
from lab.goldlabels import NHTSA_PACK
from lab.manifest import load_manifest
from lab.notes import (J1_NOT_MEASURED, J1_STOPPED, J1_WITHHELD, J3_ARMS_NOTE, J3_EVIDENCE_NOTE, J3_EVIDENCE_STOPPED,
                       J3_EXCLUSION_DISAGREES, J3_EXCLUSION_NOTE, J3_FRESHNESS_UNVERIFIED, J3_STAND_IN,
                       J3_TOO_FEW_RECORDS, J3_VERDICT_NOTE, LOW_PARTICIPATION, NO_MEASUREMENT_LINE,
                       RESULT_CONTRADICTS_EXIT, RESULT_MISSING, UNEXPECTED_EXIT)
from lab.plan import build_plan
from lab.request import (EXPERIMENTS, J2_ARTIFACT_PROBLEM, J2_PRIMARY_ARM_PROBLEM, J2_SHA_PROBLEM,
                         J3_CHAIN_FILE_PROBLEM, J3_CHAIN_FIRST_PROBLEM, J3_CHAIN_REQUIRED, J3_EXCLUDE_LIST_PROBLEM,
                         J3_FILE_PROBLEM, J3_FIRST_FILE_PROBLEM, J3_FIRST_SEED_PROBLEM, J3_LABELS_PROBLEM,
                         J3_PRIMARY_MODEL_PROBLEM, J3_SECOND_FILE_PROBLEM, J3_SEED_ORDER_PROBLEM, NHTSA_HOSTED,
                         RequestError, load_request, validate)
from lab.responder import Responder, ResponderError
from mycelic.collective.edge.verify import JUDGE_MAX_TOKENS, JUDGE_TASK, decide, judge_schema, judge_task
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import load_routing
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps, sha256_hex
from mycelic.collective.packs import loader
from mycelic.collective.schemacheck import STRICT_KEYWORDS
from tests.lab import j2_data, j3_data
from tests.lab.helpers import (LAB_MANIFEST, MANIFEST_TEST, ROOT, TEMPLATES, DryTree, call_main, check_sources,
                               kill_mentioning, plumbing_min, run_plan, write_json)

PACK = loader.load_pack(ROOT / NHTSA_PACK)
MANIFEST = load_manifest(MANIFEST_TEST)
CHOICE = ROOT / "docs" / "collective" / "A002" / "CHOICE-A002.md"
ARCHIVE = j2_data.archive(j3_data.PER_MAKE, 1)       # 540 eligible synthetic records; every value invented (B21)
LABELS = {"source": "nhtsa", "pack": NHTSA_PACK, "n": 40, "seed": 3}
# the real runs' entries, as the template carries them (the prefixes are the recorded runs'; no synthetic file has them)
J001_ENTRY = {"run_id": 37996675559, "artifact": "lab-plan-37996675559-1", "file": "prereg/j1/labels.jsonl",
              "sha256_prefix": "bc6d092d8bca", "rebuild_seed": 1, "rebuild_sha256_prefix": "bc6d092d8bca"}
A001_ENTRY = {"run_id": 38114522160, "artifact": "lab-plan-38114522160-1", "file": "prereg/j2/labels.jsonl",
              "sha256_prefix": "623780b8a1de", "rebuild_seed": 2, "rebuild_sha256_prefix": "623780b8a1de",
              "chain_file": "prereg/j2/excluded.jsonl", "chain_sha256_prefix": "9a948cbf2cd9"}
E, R, N = lab_j3.EVIDENCE_TASK, lab_j3.RATIONALE_TASK, lab_j2.NEGATION_TASK
ARTIFACT_ONLY = {"J1": "verified", "R1": "sha256 mismatch", "A1": "verified", "R2": "sha256 mismatch", "K": "verified"}


def artifact_only(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The synthetic stand-in's two entries with the real runs' rebuild prefixes: the artifacts verify, the
    rebuilds cannot (the archive is not NHTSA's), one source per cycle suffices."""
    first = {**entries[0], "rebuild_sha256_prefix": J001_ENTRY["rebuild_sha256_prefix"]}
    second = {**entries[1], "rebuild_sha256_prefix": A001_ENTRY["rebuild_sha256_prefix"]}
    return [first, second]


def j3_block(exclude: list[dict[str, Any]], **changes: Any) -> dict[str, Any]:
    block = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 3, "bootstrap_b": 1000, "bootstrap_seed": 3,
             "exclude": exclude, "primary": {"model": "fake-b", "arm": "evidence"}}
    block.update(changes)
    return block


def small_request(**changes: Any) -> dict[str, Any]:
    """A J3 request of two gguf models for the dry run on the fake provider (the template's shape, 40 records in 2
    parts); ``--j3-data`` stands in for its ``exclude`` entries."""
    block = j3_block([J001_ENTRY, A001_ENTRY], primary={"model": "a-4b", "arm": "evidence"})
    block.update(changes)
    return {"schema_version": 1, "purpose": "Accuracy test A002's plumbing on two models and forty records.",
            "provider": "llama-server", "models": ["a-1p5b", "a-4b"], "job_minutes": 60, "max_parallel": 4,
            "retention_days": 30, "experiments": {"j3": block}}


def refs_of_labels(data: bytes) -> set[str]:
    return {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def jsonl(lines: list[dict[str, Any]]) -> bytes:
    return "".join(canonical_dumps(line) + "\n" for line in lines).encode("utf-8")


def assert_no_ref(test: unittest.TestCase, text: str, refs: set[str]) -> None:
    """No record ref of ``refs`` appears in ``text`` (rule 15: counts, scores and hashes only)."""
    hits = sorted(r for r in refs if r in text)
    test.assertEqual(hits, [], f"{len(hits)} record refs in the text")


def texts_of(directory: Path) -> dict[str, str]:
    """Each drawn record's payload text (``lab.j1.payload``, the text g compares with, B23) by record ref."""
    records = {r["record_ref"]: r for r, _ in lab_j1.parse_labels((directory / "labels.jsonl").read_bytes())}
    out: dict[str, str] = {}
    for q in lab_j1.read_questions((directory / "questions.jsonl").read_bytes()):
        if q["record_ref"] not in out:
            out[q["record_ref"]] = lab_j1.payload(PACK, q, records[q["record_ref"]])["record"]["text"]
    return out


# --------------------------------------------------------------------------------------------------- fixtures

class _Prereg:
    """A J3 plan of two fake models (40 records in 2 parts) whose request excludes the two synthetic earlier runs,
    planned and preregistered once with the test archive: J1, A1 and K verify on the synthetic artifacts, R1 and R2
    cannot (the archive is not NHTSA's and the request carries the real runs' rebuild prefixes), and one verifying
    source per cycle suffices (B5)."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Prereg]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j3-prereg-"))
            cls.earlier = cls.tmp / "earlier"
            cls.stand_in = j3_data.stand_in(cls.earlier, ARCHIVE)
            cls.exclude = artifact_only(cls.stand_in)
            request = plumbing_min()
            request["experiments"] = {"j3": j3_block(cls.exclude)}
            cls.request = request
            path = write_json(cls.tmp / "j3-test.json", request)
            code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST),
                                     "--out", str(cls.tmp / "plan")])
            assert code == 0, err
            cls.plan_path = cls.tmp / "plan" / "plan.json"
            cls.plan = read_json(cls.plan_path)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cls.manifest = lab_prereg.preregister(cls.plan_path, nhtsa_fetch=lambda: ARCHIVE,
                                                      earlier_plans=cls.earlier)
            cls.stdout = out.getvalue()
            cls.dir = cls.tmp / "plan" / "prereg" / "j3"
            cls.j001 = refs_of_labels((cls.earlier / str(j3_data.J001_RUN_ID) / "prereg" / "j1" / "labels.jsonl")
                                      .read_bytes())
            cls.a001 = refs_of_labels((cls.earlier / str(j3_data.A001_RUN_ID) / "prereg" / "j2" / "labels.jsonl")
                                      .read_bytes())
            cls.excluded = cls.j001 | cls.a001
            cls.drawn = refs_of_labels((cls.dir / "labels.jsonl").read_bytes())
            cls.texts = texts_of(cls.dir)
        return cls


class _Small:
    """``lab.dryrun`` of :func:`small_request` with ``--j3-data`` (every source verifies on the stand-in), the
    shards on the fake provider, made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Small]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j3-small-"))
            cls.data = j3_data.write_data(cls.tmp / "data")
            request = write_json(cls.tmp / "accuracy-small.json", small_request())
            cls.out = cls.tmp / "D"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cls.code = lab_dryrun.main(["--request", str(request), "--out", str(cls.out),
                                            "--manifest", str(LAB_MANIFEST), "--j3-data", str(cls.data)])
            cls.stdout = stdout.getvalue()
            cls.plan = read_json(cls.out / "plan" / "plan.json")
            cls.report = read_json(cls.out / "report" / "report.json")
            cls.manifest = read_json(cls.out / "plan" / "prereg" / "prereg.json")
        return cls


class _Template:
    """``lab.dryrun`` of ``lab/templates/accuracy-002.json`` (the request the run copies to ``lab/requests/``) with
    ``--j3-data``, made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Template]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j3-template-"))
            cls.data = j3_data.write_data(cls.tmp / "data")
            cls.out = cls.tmp / "D"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cls.code = lab_dryrun.main(["--request", str(TEMPLATES / "accuracy-002.json"), "--out", str(cls.out),
                                            "--manifest", str(LAB_MANIFEST), "--j3-data", str(cls.data)])
            cls.stdout = stdout.getvalue()
            cls.plan = read_json(cls.out / "plan" / "plan.json")
            cls.report = read_json(cls.out / "report" / "report.json")
            cls.manifest = read_json(cls.out / "plan" / "prereg" / "prereg.json")
        return cls


def tearDownModule() -> None:
    for holder in (_Prereg, _Small, _Template):
        if holder.tmp is not None:
            kill_mentioning(str(holder.tmp))
            shutil.rmtree(holder.tmp, True)


# --------------------------------------------------------------------------------------------------- the rule

def rule_blockquotes() -> list[list[str]]:
    """The lines of rule 3's two blockquotes in CHOICE-A002.md: E's instructions, then R's, each verbatim."""
    text = CHOICE.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(text) if line.rstrip().endswith("inserted before the last sentence:")]
    quotes = []
    for start in starts:
        quote: list[str] = []
        for line in text[start + 1:]:
            stripped = line.strip()
            if stripped.startswith(">"):
                quote.append(stripped[1:].strip())
            elif quote:
                break
        quotes.append(quote)
    return quotes


def rule_titles() -> dict[int, str]:
    """The bold title of each numbered item of the section ``## The rule`` (B10)."""
    text = CHOICE.read_text(encoding="utf-8")
    section = text.split("\n## The rule\n", 1)[1].split("\n**The settings", 1)[0]
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^(\d+)\. \*\*(.+?)\*\*", section, re.MULTILINE)}


def sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.) ", text)


class RuleTests(unittest.TestCase):
    """The constants of ``lab.j3`` and ``lab.j3labels`` against the rule's text (rules 3 to 5, 10, 11; B1, B10, B18)."""

    def test_e_s_and_r_s_instructions_are_the_rule_s_blockquotes(self) -> None:
        quotes = rule_blockquotes()
        self.assertEqual(len(quotes), 2)
        self.assertGreaterEqual(min(len(q) for q in quotes), 10)
        self.assertEqual(" ".join(quotes[0]), lab_j3.EVIDENCE_INSTRUCTIONS)
        self.assertEqual(" ".join(quotes[1]), lab_j3.RATIONALE_INSTRUCTIONS)
        # N's text verbatim (A001's, unchanged), the new sentences inserted before the last sentence
        negation = sentences(lab_j2.NEGATION_INSTRUCTIONS)
        self.assertEqual(negation[-1], lab_j3.LAST_SENTENCE)
        self.assertEqual(sentences(judge_task().instructions)[-1], lab_j3.LAST_SENTENCE)
        for text, field in ((lab_j3.EVIDENCE_INSTRUCTIONS, lab_j3.CITATION_SENTENCES),
                            (lab_j3.RATIONALE_INSTRUCTIONS, lab_j3.ANALYSIS_SENTENCES)):
            with self.subTest(field=field[:9]):
                self.assertEqual(text, " ".join([*negation[:-1], field, lab_j3.LAST_SENTENCE]))
                self.assertTrue(text.startswith(lab_j2.NEGATION_INSTRUCTIONS[:-len(lab_j3.LAST_SENTENCE)]))
                self.assertTrue(text.endswith(" " + lab_j3.LAST_SENTENCE))
                self.assertEqual(text.count(field), 1)
                # names no code, category or filed label (rule 3, last bullet)
                folded = text.lower()
                for code in PACK.codes:
                    self.assertNotIn(code.lower(), folded)
                for predicate in PACK.predicates:
                    self.assertIsNone(re.search(rf"\b{re.escape(predicate.replace('_', ' '))}\b", folded), predicate)
        self.assertTrue(lab_j3.CITATION_SENTENCES.startswith("citation: copy, exactly as it is written"))
        self.assertTrue(lab_j3.ANALYSIS_SENTENCES.startswith("analysis: before answering, write two or three"))
        self.assertIn("A yes without a passage copied from the record counts as a no.", lab_j3.CITATION_SENTENCES)

    def test_every_rule_reference_names_its_item(self) -> None:
        """B10: the numbered items of the section The rule carry the titles the cross-references mean."""
        titles = rule_titles()
        self.assertEqual(sorted(titles), list(range(1, 17)))
        wanted = {1: "Records:", 2: "Questions:", 3: "The tasks:", 4: "The two reply mappings", 5: "The arms:",
                  6: "The judging path:", 7: "Models:", 8: "The comparators,", 9: "Scoring,", 10: "Paired differences,",
                  11: "The primary pair,", 12: "The verdict rule.", 13: "Reported beside the verdict",
                  14: "Units and limits", 15: "What is written,", 16: "The first run is the result."}
        for n, start in wanted.items():
            with self.subTest(rule=n):
                self.assertTrue(titles[n].startswith(start), titles[n])
        text = CHOICE.read_text(encoding="utf-8")
        own = [int(m.group(1)) for m in re.finditer(r"(?<!J001's )(?<!A001's )(?<!J001 )(?<!CHOICE-A001 )\brule (\d+)",
                                                      text)]
        self.assertTrue(own)
        self.assertEqual(sorted(set(own) - set(range(1, 17))), [])
        for n in (5, 7, 8, 10, 11, 12, 15, 16):
            self.assertIn(n, own)

    def test_the_three_tasks_and_their_schemas(self) -> None:
        answer = {"type": "string", "enum": ["yes", "no", "unclear"]}
        s_e, s_r = lab_j3.evidence_schema(), lab_j3.rationale_schema()
        self.assertEqual(s_e, {"type": "object", "additionalProperties": False,
                               "required": ["citation", "describes_predicate", "mentions_entity"],
                               "properties": {"citation": {"type": "string"}, "mentions_entity": answer,
                                              "describes_predicate": answer}})
        self.assertEqual(s_r, {"type": "object", "additionalProperties": False,
                               "required": ["analysis", "describes_predicate", "mentions_entity"],
                               "properties": {"analysis": {"type": "string"}, "mentions_entity": answer,
                                              "describes_predicate": answer}})
        # the new field first in insertion order and first in sorted order; no strict keyword anywhere
        for schema, field in ((s_e, "citation"), (s_r, "analysis")):
            self.assertEqual(list(schema["properties"])[0], field)
            self.assertEqual(sorted(schema["properties"])[0], field)
            self.assertEqual(schema["required"][0], field)
            self.assertEqual(sorted(schema["required"]), schema["required"])
            self.assertEqual(set(STRICT_KEYWORDS) & set(canonical_dumps(schema).replace('"', " ").split()), set())
            self.assertEqual({k for k in schema["properties"] if k != field}, set(judge_schema()["properties"]))
        self.assertEqual(lab_j3.schema_keys(E), ("citation", "mentions_entity", "describes_predicate"))
        self.assertEqual(lab_j3.schema_keys(R), ("analysis", "mentions_entity", "describes_predicate"))
        self.assertEqual(lab_j3.schema_keys(N), tuple(judge_schema()["properties"]))
        self.assertEqual(lab_j3.task_schema(N), judge_schema())
        e, r = lab_j3.evidence_task(), lab_j3.rationale_task()
        self.assertEqual((e.name, e.data_class, e.max_tokens, e.instructions), (E, "raw", 512, lab_j3.EVIDENCE_INSTRUCTIONS))
        self.assertEqual((r.name, r.data_class, r.max_tokens, r.instructions),
                         (R, "raw", 512, lab_j3.RATIONALE_INSTRUCTIONS))
        self.assertEqual((lab_j3.FIELD_MAX_TOKENS, JUDGE_MAX_TOKENS, lab_j3.TASK_MAX_TOKENS),
                         (512, 256, {E: 512, R: 512, N: 256}))
        self.assertEqual(lab_j3.task_spec(N), lab_j2.negation_task())
        self.assertEqual((lab_j3.task_spec(E), lab_j3.task_spec(R)), (e, r))
        for bad in (JUDGE_TASK, "judge_record_other"):
            with self.assertRaises(lab_j3.J3Error):
                lab_j3.task_spec(bad)
            with self.assertRaises(lab_j3.J3Error):
                lab_j3.task_schema(bad)
        self.assertEqual(lab_j3.TASKS, (E, R, N))                        # rule 14: E first, then R, then N
        self.assertEqual(lab_j3.TASK_LABELS, {E: "evidence", R: "rationale", N: "negation"})
        self.assertEqual(lab_j3.TASK_FIELDS, {E: "citation", R: "analysis", N: None})
        pins = lab_j3.task_pins()
        self.assertEqual(sorted(pins), ["evidence", "negation", "order", "rationale"])
        self.assertEqual(pins["negation"], lab_j2.task_pins()["negation"])
        self.assertEqual(pins["evidence"], {"name": E, "data_class": "raw",
                                            "instructions_sha256": sha256_hex(lab_j3.EVIDENCE_INSTRUCTIONS),
                                            "schema_sha256": sha256_hex(canonical_bytes(s_e)), "max_tokens": 512})
        self.assertEqual(pins["rationale"]["schema_sha256"], sha256_hex(canonical_bytes(s_r)))
        self.assertEqual(pins["order"], [E, R, N])

    def test_g_s_declared_cases(self) -> None:
        """Rule 4 and How it is read: g on each declared case, over the truncated payload text."""
        text = "The brakes  FAILED on the\nhighway; the dealer found nothing. Steering wheel locked up twice."
        cases = {
            "exact": ("brakes  FAILED on the\nhighway", True),
            "case only": ("THE BRAKES  failed", True),
            "whitespace runs only": ("brakes FAILED on the highway", True),
            "a word changed": ("brakes WORKED on the highway", False),
            "a paraphrase": ("the brakes did not work", False),
            "an added character": ("brakes  FAILED  on the highway!", False),
            "a dropped character": ("brakes  FAILD on the highway", False),
            "another record's passage": ("the airbag did not deploy", False),
            "empty": ("", False),
            "three characters after norm": ("  T h e ".replace(" ", "")[:3], False),
            "four characters": ("rake", True),
            "an accent": ("brakés FAILED", False),
        }
        for label, (citation, want) in cases.items():
            with self.subTest(case=label):
                self.assertIs(lab_j3.grounded(citation, text), want)
        self.assertEqual(lab_j3.norm("  A\tb  \n c "), "a b c")
        self.assertEqual(lab_j3.GROUNDING_MIN_CHARS, 4)
        self.assertIs(lab_j3.grounded(" brak ", text), True)
        self.assertIs(lab_j3.grounded(" bra ", text), False)               # three after norm
        self.assertIs(lab_j3.grounded(None, text), False)
        self.assertIs(lab_j3.grounded("brakes", None), False)
        self.assertIs(lab_j3.grounded(12, text), False)
        # the text compared is the truncated payload text: a passage beyond the cut is not grounded
        p = _Prereg.get()
        record = next(iter(lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())))[0]
        q = next(q for q in lab_j1.read_questions((p.dir / "questions.jsonl").read_bytes())
                 if q["record_ref"] == record["record_ref"])
        cap = PACK.extraction.max_input_chars
        self.assertEqual(cap, 6000)
        long = dict(record, narrative=record["narrative"] + " filler words here " * 600 + "the tail passage after the cut")
        payload = lab_j1.payload(PACK, q, long)
        self.assertLessEqual(len(payload["record"]["text"]), cap)
        self.assertIs(lab_j3.grounded("the tail passage after the cut", payload["record"]["text"]), False)
        self.assertIs(lab_j3.grounded(record["narrative"][:12], payload["record"]["text"]), True)
        self.assertIs(lab_j3.grounded("the tail passage after the cut", long["narrative"]), True)

    def test_G_and_M_on_every_answer_and_their_commutation(self) -> None:
        answers = ("yes", "no", "unclear")
        self.assertEqual(lab_j3.GROUNDING, {"describes_predicate": {"yes": "no"}})
        self.assertEqual(lab_j2.MAPPING, {"describes_predicate": {"unclear": "no"}})
        for m, d, g in itertools.product(answers, answers, (True, False, None)):
            reply = {"citation": "x", "mentions_entity": m, "describes_predicate": d}
            with self.subTest(reply=(m, d), grounded=g):
                grounded = lab_j3.grounded_reply(reply, g)
                self.assertEqual(grounded["mentions_entity"], m)
                self.assertEqual(grounded["describes_predicate"], "no" if d == "yes" and g is not True else d)
                self.assertEqual(sorted(grounded), ["describes_predicate", "mentions_entity"])
                both = lab_j3.grounded_mapped_reply(reply, g)
                self.assertEqual(both, lab_j2.mapped_reply(lab_j3.grounded_reply(reply, g)))
                self.assertEqual(both, lab_j3.grounded_reply(lab_j2.mapped_reply(reply), g))     # they commute
                self.assertEqual(both["describes_predicate"], "no" if d == "unclear" or (d == "yes" and g is not True)
                                 else d)
                self.assertEqual(lab_j3.answers(reply), {"mentions_entity": m, "describes_predicate": d})
        self.assertIsNone(lab_j3.grounded_reply(None, True))
        self.assertIsNone(lab_j3.grounded_mapped_reply(None, False))
        self.assertIsNone(lab_j3.answers(None))
        pin = lab_j3.grounding_pin()
        self.assertEqual(sorted(pin), ["mapping", "predicate", "sha256"])
        self.assertEqual(pin["mapping"], {"describes_predicate": {"yes": "no"}})
        self.assertEqual(pin["predicate"]["min_chars"], 4)
        self.assertEqual(pin["sha256"], sha256_hex(canonical_bytes({k: v for k, v in pin.items() if k != "sha256"})))

    def test_the_composite_rule_and_both_ablations(self) -> None:
        verdicts = ("confirm", "refute", "unknown")
        for lexical, arm in itertools.product(verdicts, verdicts):
            self.assertEqual(lab_j3.composite_verdict(lexical, arm), "confirm" if lexical == "confirm" else arm)
        qs = [_q("A", "positive", "engine"), _q("A", "negative", "tires"), _q("B", "positive", "air_bags"),
              _q("B", "negative", "seats")]
        lexical = [_lex(q, v) for q, v in zip(qs, ("confirm", "confirm", "refute", "unknown"))]
        prior = lab_j1.prior_verdicts(qs)
        self.assertEqual([line["verdict"] for line in prior], ["confirm", "refute", "confirm", "refute"])
        a = lab_j3.ablation_a_lines(qs, lexical)
        self.assertEqual([line["verdict"] for line in a], ["confirm", "confirm", "refute", "refute"])
        self.assertEqual({(line["mentions_entity"], line["describes_predicate"]) for line in a}, {("yes", "no")})
        self.assertEqual(lab_j1.verdict(lab_j1.stand_in("A"), lab_j3.ABLATION_A_REPLY), "refute")
        b = lab_j3.ablation_b_lines(qs, lexical, prior)
        self.assertEqual([line["verdict"] for line in b], ["confirm", "confirm", "confirm", "refute"])
        self.assertEqual([{k: line[k] for k in lab_j1.LINE_KEYS if k != "verdict"} for line in b],
                         [{k: line[k] for k in lab_j1.LINE_KEYS if k != "verdict"} for line in prior])
        for view in (a, b):
            for line in view:
                self.assertEqual(sorted(line), sorted(lab_j1.LINE_KEYS))
            lab_j1.score(qs, view, bootstrap_b=1000, bootstrap_seed=3)
        # on a draw where the lexical judge never answers unknown, ablation A is its verdicts line for line (B8)
        never_unknown = [_lex(q, v) for q, v in zip(qs, ("confirm", "refute", "refute", "confirm"))]
        self.assertEqual([line["verdict"] for line in lab_j3.ablation_a_lines(qs, never_unknown)],
                         [line["verdict"] for line in never_unknown])
        self.assertEqual(lab_j3.ABLATIONS, ("lexical_or_no", "lexical_or_record_blind"))
        pin = lab_j3.composite_pin()
        self.assertEqual(pin["rule"], lab_j3.COMPOSITE_RULE)
        self.assertEqual(pin["arms"], {"composite": "evidence_grounded", "composite_mapped": "evidence_grounded_mapped"})
        self.assertEqual(pin["ablations"], {"lexical_or_no": {"reply": {"mentions_entity": "yes", "describes_predicate": "no"}},
                                            "lexical_or_record_blind": {"reply": "record_blind"}})
        self.assertEqual(pin["sha256"], sha256_hex(canonical_bytes({k: v for k, v in pin.items() if k != "sha256"})))

    def test_the_arms_the_lifts_and_the_pins(self) -> None:
        self.assertEqual(lab_j3.ARMS, ("negation", "negation_mapped", "evidence", "evidence_grounded",
                                       "evidence_grounded_mapped", "rationale", "rationale_mapped", "composite",
                                       "composite_mapped"))
        self.assertEqual(lab_j3.ARM_LETTERS, {"negation": "N", "negation_mapped": "D", "evidence": "E",
                                              "evidence_grounded": "Eg", "evidence_grounded_mapped": "Egm",
                                              "rationale": "R", "rationale_mapped": "Rm", "composite": "C",
                                              "composite_mapped": "Cm"})
        self.assertEqual(lab_j3.ARM_TASK, {"negation": N, "negation_mapped": N, "evidence": E, "evidence_grounded": E,
                                           "evidence_grounded_mapped": E, "rationale": R, "rationale_mapped": R,
                                           "composite": E, "composite_mapped": E})
        self.assertEqual(lab_j3.ARM_READING, {"negation": "shipped", "negation_mapped": "mapped", "evidence": "shipped",
                                              "evidence_grounded": "grounded",
                                              "evidence_grounded_mapped": "grounded_mapped", "rationale": "shipped",
                                              "rationale_mapped": "mapped", "composite": "grounded",
                                              "composite_mapped": "grounded_mapped"})
        self.assertEqual({a for a, c in lab_j3.ARM_COMPOSITE.items() if c}, {"composite", "composite_mapped"})
        self.assertEqual(lab_j3.COMPOSITE_OF, {"composite": "evidence_grounded",
                                               "composite_mapped": "evidence_grounded_mapped"})
        self.assertEqual(lab_j3.HEADLINE_SECONDARY, "evidence_grounded")                               # B1
        self.assertEqual(lab_j3.arms_doc()["evidence"], {"letter": "E", "task": E, "reading": "shipped",
                                                         "composite": False})
        self.assertEqual(lab_j3.arms_doc()["composite_mapped"], {"letter": "Cm", "task": E,
                                                                 "reading": "grounded_mapped", "composite": True})
        self.assertEqual(lab_j3.LIFTS, (("E_minus_N", "evidence", "negation"),
                                        ("Eg_minus_E", "evidence_grounded", "evidence"),
                                        ("Eg_minus_N", "evidence_grounded", "negation"),
                                        ("Egm_minus_Eg", "evidence_grounded_mapped", "evidence_grounded"),
                                        ("Egm_minus_D", "evidence_grounded_mapped", "negation_mapped"),
                                        ("R_minus_N", "rationale", "negation"),
                                        ("Rm_minus_D", "rationale_mapped", "negation_mapped"),
                                        ("D_minus_N", "negation_mapped", "negation"),
                                        ("C_minus_Eg", "composite", "evidence_grounded"),
                                        ("Cm_minus_Egm", "composite_mapped", "evidence_grounded_mapped")))
        self.assertEqual(lab_j3.LIFT_LEVERS, {"E_minus_N": "evidence_field", "Eg_minus_E": "grounding_check",
                                              "Eg_minus_N": "evidence_field_and_check",
                                              "Egm_minus_Eg": "unclear_mapping",
                                              "Egm_minus_D": "evidence_field_and_check",
                                              "R_minus_N": "rationale_field", "Rm_minus_D": "rationale_field",
                                              "D_minus_N": "unclear_mapping", "C_minus_Eg": "lexical_disjunction",
                                              "Cm_minus_Egm": "lexical_disjunction"})
        self.assertEqual(lab_j3.ABLATION_DIFFERENCES, (("C_minus_ablation_A", "composite", "lexical_or_no"),
                                                       ("C_minus_ablation_B", "composite", "lexical_or_record_blind"),
                                                       ("Cm_minus_ablation_A", "composite_mapped", "lexical_or_no"),
                                                       ("Cm_minus_ablation_B", "composite_mapped",
                                                        "lexical_or_record_blind")))
        self.assertEqual(lab_j3.lifts_doc()["E_minus_N"], {"arm": "evidence", "minus": "negation",
                                                           "lever": "evidence_field"})
        self.assertEqual(lab_j3.ablation_differences_doc()["Cm_minus_ablation_B"],
                         {"arm": "composite_mapped", "ablation": "lexical_or_record_blind"})
        self.assertEqual((lab_j3.TARGET_BA, lab_j3.VERDICT_WORDS, lab_j3.LIFT_READINGS),
                         (0.86, ("incomplete", "pass_provisional", "partial", "failure"),
                          ("helps", "hurts", "not_told_apart")))
        self.assertEqual((lab_j3.WITHHOLD_SHARE, lab_j3.BOUNDARY, lab_j3.DATA_LABEL),
                         (lab_j1.WITHHOLD_SHARE, "site:lab", "public"))
        self.assertEqual(lab_j3.LINE_KEYS, (*lab_j1.LINE_KEYS, "task", "citation", "analysis", "grounded", "first_key",
                                            "mapped_verdict", "grounded_verdict", "grounded_mapped_verdict"))
        self.assertEqual(lab_j3.CODE_FILES, (*lab_j2.CODE_FILES, "lab/j3.py", "lab/j3labels.py"))
        self.assertTrue(set(lab_j2.PREREG_KEYS) <= set(lab_j3.PREREG_KEYS))
        for key in ("ablation_a", "ablation_b", "grounding", "composite", "lifts", "ablation_differences",
                    "headline_secondary"):
            self.assertIn(key, lab_j3.PREREG_KEYS)
        self.assertEqual(lab_j3.EXCLUSION_KEYS, (*lab_j2.EXCLUSION_KEYS, "sources", "cycles", "consistency",
                                                 "expected_excluded"))
        # the summary's own copies, which the module's constants must not drift from (B11)
        self.assertEqual(lab_summary.J3_ARMS, lab_j3.ARMS)
        self.assertEqual(lab_summary.J3_LIFTS, tuple(name for name, _, _ in lab_j3.LIFTS))
        self.assertEqual(lab_summary.J3_ABLATION_DIFFERENCES, tuple(name for name, _, _ in lab_j3.ABLATION_DIFFERENCES))
        self.assertEqual(lab_summary.J3_TASKS, lab_j3.TASKS)
        self.assertEqual((lab_summary.J3_SOURCES, lab_summary.J3_CYCLES, lab_summary.J3_CONSISTENCY),
                         (j3labels.SOURCES, j3labels.CYCLES, j3labels.CONSISTENCY))
        # j3labels' pins
        self.assertEqual((j3labels.A001_LABELS_SHA256_PREFIX, j3labels.A001_QUESTIONS_SHA256_PREFIX,
                          j3labels.A001_CHAIN_SHA256_PREFIX), ("623780b8a1de", "2ba18ea89bb7", "9a948cbf2cd9"))
        self.assertEqual(j3labels.A001_REBUILD, {"source": "nhtsa", "n": 150, "seed": 2})
        self.assertEqual(j3labels.EXCLUDE_FILES, ("prereg/j1/labels.jsonl", "prereg/j2/excluded.jsonl",
                                                  "prereg/j2/labels.jsonl"))
        self.assertEqual(j3labels.OUTCOMES, (*j2labels.OUTCOMES, "not attempted"))
        self.assertEqual((j3labels.SOURCES, j3labels.CYCLES, j3labels.BY, j3labels.CONSISTENCY, j3labels.PROBLEMS),
                         (("J1", "R1", "A1", "R2", "K"), ("j001", "a001"), ("artifact", "rebuild", "chain"),
                          ("j1_r1", "a1_r2", "outside_chain", "chain_union"), ("unverified", "disagrees")))
        self.assertEqual(j3labels.EXPECTED_EXCLUDED, 300)

    def test_the_routing_routes_the_three_tasks_to_the_one_endpoint(self) -> None:
        models = _Prereg.get().plan["models"]
        doc = lab_j3.routing_doc(models, ["fake-a"], "http://127.0.0.1:1/v1")
        self.assertEqual(doc["routes"], {E: {"endpoint": "fake-a"}, R: {"endpoint": "fake-a"}, N: {"endpoint": "fake-a"}})
        self.assertEqual(doc["endpoints"], {"fake-a": lab_j1.endpoint(models["fake-a"], "http://127.0.0.1:1/v1")})
        self.assertEqual(doc["endpoints"]["fake-a"]["boundary"], "site:lab")
        both = lab_j3.routing_doc(models, ["fake-a", "fake-b"], "http://127.0.0.1:1/v1")
        self.assertEqual((sorted(both["endpoints"]), both["routes"]), (["fake-a", "fake-b"], {}))

    def test_every_verdict_is_the_pinned_decide_over_the_one_record(self) -> None:
        p = _Prereg.get()
        record = next(iter(lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())))[0]
        q = next(q for q in lab_j1.read_questions((p.dir / "questions.jsonl").read_bytes())
                 if q["record_ref"] == record["record_ref"])
        w = lab_j1.window_record(record)
        text = p.texts[record["record_ref"]]
        good, bad = text[5:40], "a passage of no record"
        answers = ("yes", "no", "unclear")
        for m, d in itertools.product(answers, answers):
            two = {"mentions_entity": m, "describes_predicate": d}
            shipped = decide([w], [(w, two)], 0).verdict
            mapped = decide([w], [(w, lab_j2.mapped_reply(two))], 0).verdict
            for task, extra in ((E, {"citation": good}), (E, {"citation": bad}), (E, {"citation": ""}),
                                (R, {"analysis": "two sentences."}), (N, {})):
                reply = {**extra, **two}
                line = lab_j3.verdict_line(q, w, reply, None, task, text)
                with self.subTest(reply=(m, d), task=task, extra=extra):
                    self.assertEqual(sorted(line), sorted(lab_j3.LINE_KEYS))
                    self.assertEqual({k: line[k] for k in lab_j1.LINE_KEYS}, lab_j1.verdict_line(q, w, two, None))
                    self.assertEqual((line["task"], line["verdict"], line["mapped_verdict"]), (task, shipped, mapped))
                    self.assertEqual(line["first_key"], next(iter(reply)))
                    self.assertEqual((line["citation"], line["analysis"]),
                                     (extra.get("citation") if task == E else None,
                                      extra.get("analysis") if task == R else None))
                    if task != E:
                        self.assertEqual((line["grounded"], line["grounded_verdict"], line["grounded_mapped_verdict"]),
                                         (None, None, None))
                        continue
                    is_grounded = extra["citation"] == good
                    self.assertIs(line["grounded"], is_grounded)
                    self.assertEqual(line["grounded_verdict"],
                                     decide([w], [(w, lab_j3.grounded_reply(two, is_grounded))], 0).verdict)
                    self.assertEqual(line["grounded_mapped_verdict"],
                                     decide([w], [(w, lab_j3.grounded_mapped_reply(two, is_grounded))], 0).verdict)
                    if d == "yes" and not is_grounded:
                        self.assertEqual(line["grounded_verdict"], decide([w], [(w, {**two, "describes_predicate": "no"})],
                                                                          0).verdict)
        failed = lab_j3.verdict_line(q, w, None, "timeout", E, text)
        self.assertEqual((failed["verdict"], failed["mapped_verdict"], failed["grounded_verdict"],
                          failed["grounded_mapped_verdict"], failed["grounded"], failed["first_key"],
                          failed["citation"], failed["scored"]),
                         ("unknown", "unknown", "unknown", "unknown", None, None, None, False))
        with self.assertRaises(lab_j3.J3Error):
            lab_j3.verdict_line(q, w, None, "timeout", JUDGE_TASK, text)
        # a reply whose field came last keeps its first key as written (B19)
        late = lab_j3.verdict_line(q, w, {"mentions_entity": "yes", "describes_predicate": "yes", "citation": good},
                                   None, E, text)
        self.assertEqual((late["first_key"], late["grounded"]), ("mentions_entity", True))
        self.assertEqual(lab_j3.first_key({"b": 1, "a": 2}), "b")
        self.assertIsNone(lab_j3.first_key(None))
        self.assertIsNone(lab_j3.first_key({}))


# --------------------------------------------------------------------------------------------------- lines, views

def _q(ref: str, kind: str, predicate: str, part: int = 1) -> dict[str, Any]:
    return {"question_id": f"{ref}:{kind}", "record_ref": ref, "part": part, "kind": kind, "entity_type": "vehicle",
            "entity_id": "V-1", "predicate": predicate, "filed": []}


def _lex(q: dict[str, Any], verdict: str) -> dict[str, Any]:
    """A lexical judge's line with the given verdict (``confirm`` yes/yes, ``refute`` yes/no, ``unknown`` yes/unclear)."""
    d = {"confirm": "yes", "refute": "no", "unknown": "unclear"}[verdict]
    line = lab_j1.verdict_line(q, lab_j1.stand_in(q["record_ref"]), {"mentions_entity": "yes", "describes_predicate": d},
                               None)
    assert line["verdict"] == verdict
    return line


TEXTS = {"A": "The engine stalled at speed and the tires were fine.", "B": "Seats rattled; the air bags did not deploy."}


def _line(q: dict[str, Any], task: str, m: str | None, d: str | None, error_kind: str | None = None, *,
          citation: str | None = None, analysis: str | None = None, first: bool = True) -> dict[str, Any]:
    reply: dict[str, Any] | None
    if m is None:
        reply = None
    else:
        two = {"mentions_entity": m, "describes_predicate": d}
        field = {E: ("citation", citation if citation is not None else ""),
                 R: ("analysis", analysis if analysis is not None else ""), N: None}[task]
        reply = {field[0]: field[1], **two} if field is not None and first else \
            {**two, field[0]: field[1]} if field is not None else two
    return lab_j3.verdict_line(q, lab_j1.stand_in(q["record_ref"]), reply, error_kind, task, TEXTS[q["record_ref"]])


class LineTests(unittest.TestCase):
    """Rule 15: the j3 line, the j1-shaped views of the nine arms through ``lab.j1.score`` and ``lab.j1.paired``, and
    the line check's every outcome (B19, B23), without touching ``lab/j1.py``."""

    def setUp(self) -> None:
        self.qs = [_q("A", "positive", "engine"), _q("A", "negative", "tires"), _q("B", "positive", "air_bags"),
                   _q("B", "negative", "seats")]
        a_pos, a_neg, b_pos, b_neg = self.qs
        self.lines = [
            _line(a_pos, E, "yes", "yes", citation="engine stalled"),           # grounded confirm
            _line(a_neg, E, "yes", "yes", citation="tires exploded"),           # not grounded: G reads no
            _line(b_pos, E, "yes", "unclear", citation=""),                     # unknown; M reads refute
            _line(b_neg, E, None, None, "timeout"),
            _line(a_pos, R, "yes", "yes", analysis="x"), _line(a_neg, R, "yes", "unclear", analysis="y"),
            _line(b_pos, R, "yes", "yes", analysis="z"), _line(b_neg, R, "no", "no", analysis="w"),
            _line(a_pos, N, "yes", "no"), _line(a_neg, N, "yes", "no"), _line(b_pos, N, "unclear", "unclear"),
            _line(b_neg, N, "yes", "unclear")]
        self.lexical = {q["question_id"]: _lex(q, v) for q, v in zip(self.qs, ("refute", "confirm", "confirm",
                                                                             "refute"))}

    def test_the_views_of_the_nine_arms(self) -> None:
        e = lab_j3.arm_lines(self.lines, "evidence")
        self.assertEqual(e, [{k: line[k] for k in lab_j1.LINE_KEYS} for line in self.lines[:4]])
        self.assertEqual([line["verdict"] for line in e], ["confirm", "confirm", "unknown", "unknown"])
        eg = lab_j3.arm_lines(self.lines, "evidence_grounded")
        self.assertEqual([line["verdict"] for line in eg], ["confirm", "refute", "unknown", "unknown"])
        self.assertEqual([line["describes_predicate"] for line in eg], ["yes", "no", "unclear", None])
        egm = lab_j3.arm_lines(self.lines, "evidence_grounded_mapped")
        self.assertEqual([line["verdict"] for line in egm], ["confirm", "refute", "refute", "unknown"])
        self.assertEqual([line["describes_predicate"] for line in egm], ["yes", "no", "no", None])
        r = lab_j3.arm_lines(self.lines, "rationale")
        self.assertEqual(r, [{k: line[k] for k in lab_j1.LINE_KEYS} for line in self.lines[4:8]])
        rm = lab_j3.arm_lines(self.lines, "rationale_mapped")
        self.assertEqual([line["verdict"] for line in rm], ["confirm", "refute", "confirm", "unknown"])
        n = lab_j3.arm_lines(self.lines, "negation")
        self.assertEqual(n, [{k: line[k] for k in lab_j1.LINE_KEYS} for line in self.lines[8:]])
        d = lab_j3.arm_lines(self.lines, "negation_mapped")
        self.assertEqual([line["verdict"] for line in d], ["refute", "refute", "unknown", "refute"])
        c = lab_j3.arm_lines(self.lines, "composite", self.lexical)
        self.assertEqual([line["verdict"] for line in c], ["confirm", "confirm", "confirm", "unknown"])
        self.assertEqual([line["describes_predicate"] for line in c], [line["describes_predicate"] for line in eg])
        cm = lab_j3.arm_lines(self.lines, "composite_mapped", self.lexical)
        self.assertEqual([line["verdict"] for line in cm], ["confirm", "confirm", "confirm", "unknown"])
        self.assertEqual([line["describes_predicate"] for line in cm], [line["describes_predicate"] for line in egm])
        # the composite reads the lexical judge at the verdict only; the answer pairs stay the model's
        for view in (e, eg, egm, r, rm, n, d, c, cm):
            for line in view:
                self.assertEqual(sorted(line), sorted(lab_j1.LINE_KEYS))
            score = lab_j1.score(self.qs, view, bootstrap_b=1000, bootstrap_seed=3)
            self.assertEqual(score["records_scored"] + score["records_dropped"], 2)
            lab_j1.paired(self.qs, view, list(self.lexical.values()), bootstrap_b=1000, bootstrap_seed=3)
        score_e = lab_j1.score(self.qs, e, bootstrap_b=1000, bootstrap_seed=3)
        self.assertEqual((score_e["records_scored"], score_e["records_dropped"]), (1, 1))
        self.assertEqual(score_e["answers"], {"positive": {"yes_yes": 1}, "negative": {"yes_yes": 1}})
        self.assertEqual(lab_j1.score(self.qs, eg, bootstrap_b=1000, bootstrap_seed=3)["answers"],
                         {"positive": {"yes_yes": 1}, "negative": {"yes_no": 1}})
        self.assertEqual((score_e["specificity"]["value"],
                          lab_j1.score(self.qs, eg, bootstrap_b=1000, bootstrap_seed=3)["specificity"]["value"]),
                         (0.0, 1.0))
        paired = lab_j1.paired(self.qs, eg, e, bootstrap_b=1000, bootstrap_seed=3)
        self.assertEqual((paired["n"], paired["mean_diff"]), (1, 0.5))
        with self.assertRaises(lab_j3.J3Error):
            lab_j3.arm_lines(self.lines, "composite")
        with self.assertRaises(lab_j3.J3Error):
            lab_j3.arm_lines(self.lines, "composite", {})
        with self.assertRaises(lab_j3.J3Error):
            lab_j3.arm_lines(self.lines, "shipped")

    def test_the_line_check(self) -> None:
        for q, line in zip([*self.qs] * 3, self.lines):
            self.assertIsNone(lab_j3.line_problem(q, line, TEXTS[q["record_ref"]]), line)
        q, good = self.qs[1], self.lines[1]                      # E, yes/yes, citation not grounded
        text = TEXTS["A"]
        check = lambda line, q=q, text=text: lab_j3.line_problem(q, line, text)       # noqa: E731
        self.assertEqual(check({k: v for k, v in good.items() if k != "task"}), "keys")
        self.assertEqual(check({**good, "extra": 1}), "keys")
        self.assertEqual(check("not a line"), "keys")
        self.assertEqual(check({**good, "task": JUDGE_TASK}), "task")
        self.assertEqual(check({**good, "verdict": "refute"}), "verdict")
        self.assertEqual(check({**good, "kind": "positive"}), "question")
        self.assertEqual(check({**good, "mentions_entity": "maybe"}), "answers")
        self.assertEqual(check({**good, "citation": None}), "citation")
        self.assertEqual(check({**good, "citation": 3}), "citation")
        self.assertEqual(check({**good, "analysis": "x"}), "analysis")
        self.assertEqual(check({**good, "first_key": "foo"}), "first_key")
        self.assertEqual(check({**good, "first_key": None}), "first_key")
        self.assertIsNone(check({**good, "first_key": "describes_predicate"}))     # B19: shape only, never recomputed
        self.assertEqual(check({**good, "mapped_verdict": "unknown"}), "mapped_verdict")
        self.assertEqual(check({**good, "grounded": True}), "grounded")              # B23: g recomputed from the text
        self.assertEqual(check({**good, "grounded": None}), "grounded")
        self.assertEqual(check({**good, "grounded_verdict": "confirm"}), "grounded_verdict")
        self.assertEqual(check({**good, "grounded_mapped_verdict": "confirm"}), "grounded_mapped_verdict")
        # the same line against another text: the citation now grounded, so grounded False is refused
        self.assertEqual(lab_j3.line_problem(q, good, "the tires exploded on the road"), "grounded")
        grounded = {**good, "grounded": True, "grounded_verdict": "confirm", "grounded_mapped_verdict": "confirm"}
        self.assertIsNone(lab_j3.line_problem(q, grounded, "the tires exploded on the road"))
        # a consistent change of the reply and every reading is a line the runner could have written
        self.assertIsNone(check({**good, "describes_predicate": "no", "verdict": "refute", "mapped_verdict": "refute",
                                 "grounded_verdict": "refute", "grounded_mapped_verdict": "refute"}))
        failed = self.lines[3]
        self.assertEqual(lab_j3.line_problem(self.qs[3], {**failed, "first_key": "citation"}, TEXTS["B"]), "first_key")
        self.assertEqual(lab_j3.line_problem(self.qs[3], {**failed, "citation": ""}, TEXTS["B"]), "citation")
        self.assertEqual(lab_j3.line_problem(self.qs[3], {**failed, "grounded_verdict": "refute"}, TEXTS["B"]),
                         "grounded_verdict")
        # an R line or an N line carries no citation and no grounding
        r_line, n_line = self.lines[4], self.lines[8]
        self.assertEqual(lab_j3.line_problem(self.qs[0], {**r_line, "citation": ""}, TEXTS["A"]), "citation")
        self.assertEqual(lab_j3.line_problem(self.qs[0], {**r_line, "grounded": False}, TEXTS["A"]), "grounded")
        self.assertEqual(lab_j3.line_problem(self.qs[0], {**r_line, "grounded_verdict": "confirm"}, TEXTS["A"]),
                         "grounded_verdict")
        self.assertEqual(lab_j3.line_problem(self.qs[0], {**r_line, "analysis": None}, TEXTS["A"]), "analysis")
        self.assertEqual(lab_j3.line_problem(self.qs[0], {**n_line, "analysis": ""}, TEXTS["A"]), "analysis")
        self.assertEqual(lab_j3.line_problem(self.qs[0], {**n_line, "first_key": "citation"}, TEXTS["A"]), "first_key")
        self.assertEqual(lab_j3.line_problem(self.qs[0], {**n_line, "grounded_mapped_verdict": "refute"}, TEXTS["A"]),
                         "grounded_mapped_verdict")
        # the shipped verdict's check is J001's, called on the projected line
        with mock.patch.object(lab_j1, "line_problem", return_value="verdict") as checked:
            self.assertEqual(check(good), "verdict")
        self.assertEqual(sorted(checked.call_args[0][1]), sorted(lab_j1.LINE_KEYS))


# --------------------------------------------------------------------------------------------------- the verdict word

def _scores(ba: float, lo: float, hi: float, unknown: float | None = 0.1) -> dict[str, Any]:
    return {"balanced_accuracy": {"value": ba, "ci_low": lo, "ci_high": hi},
            "unknown_share": {"value": unknown, "ci_low": unknown, "ci_high": unknown}}


def _paired(diff: float, lo: float, hi: float) -> dict[str, Any]:
    return {"n": 150, "mean_diff": diff, "ci_low": lo, "ci_high": hi}


def _word(**changes: Any) -> tuple[str, dict[str, Any]]:
    args: dict[str, Any] = {"complete": True, "display_class": "model", "measurement": True, "dropped_share": 0.0,
                            "scores": _scores(0.9, 0.85, 0.95), "paired": _paired(0.2, 0.1, 0.3),
                            "bound": {"value": 0.58, "records": 150, "questions": 300},
                            "ablation_a": _paired(0.2, 0.1, 0.3), "ablation_b": _paired(0.1, 0.02, 0.2)}
    args.update(changes)
    return lab_j3.composite_verdict_word(**args)


class VerdictWordTests(unittest.TestCase):
    """Rule 12 as A5, A6 and B8 read it: every arm's word is ``lab.j2.verdict_word``'s; a composite's adds the two
    ablation clauses, in one order."""

    def test_the_words_in_one_order_with_the_ablation_clauses(self) -> None:
        word, test = _word()
        self.assertEqual(word, "pass_provisional")
        self.assertEqual(test["ablation_clauses"], {"beats_ablation_a": True, "beats_ablation_b": True})
        self.assertEqual((test["diff_ablation_a"], test["diff_ablation_a_ci_low"], test["diff_ablation_b"],
                          test["diff_ablation_b_ci_low"], test["passed"]), (0.2, 0.1, 0.1, 0.02, True))
        self.assertEqual(test["clauses"], {"balanced_accuracy_at_target": True, "beats_lexical": True,
                                           "beats_bound": True, "unknown_share_reported": True})
        # the j2 word first: incomplete stays incomplete, whatever the ablations say
        for change in ({"complete": False}, {"display_class": "plumbing"}, {"measurement": False},
                       {"dropped_share": 2 / 150}, {"scores": None}, {"paired": None}, {"bound": None}):
            self.assertEqual(_word(**change)[0], "incomplete", change)
        # B8: an ablation difference that is not a number is incomplete, even when the four clauses hold
        for change in ({"ablation_a": None}, {"ablation_b": None}, {"ablation_a": {"n": 0, "mean_diff": None,
                                                                                   "ci_low": None, "ci_high": None}},
                       {"ablation_b": _paired(0.1, 0.0, 0.2) | {"ci_low": None}}):
            word, test = _word(**change)
            self.assertEqual((word, test["passed"]), ("incomplete", False), change)
        # the four clauses met but an ablation clause failed: partial, with the figures
        for change in ({"ablation_a": _paired(0.0, 0.0, 0.0)}, {"ablation_b": _paired(0.05, -0.01, 0.1)},
                       {"ablation_a": _paired(0.1, 0.0, 0.2)}):
            word, test = _word(**change)
            self.assertEqual((word, test["passed"]), ("partial", False), change)
            self.assertFalse(all(test["ablation_clauses"].values()))
        # a j2 partial or failure stands, whatever the ablations say
        self.assertEqual(_word(paired=_paired(0.1, -0.02, 0.2))[0], "partial")
        self.assertEqual(_word(scores=_scores(0.75, 0.7, 0.8))[0], "partial")
        self.assertEqual(_word(scores=_scores(0.75, 0.7, 0.8), paired=_paired(0.05, -0.02, 0.12))[0], "failure")
        # B8: a missing ablation figure is incomplete whatever the other clauses say, as a missing bound is
        self.assertEqual(_word(scores=_scores(0.75, 0.7, 0.8), paired=_paired(0.05, -0.02, 0.12),
                               ablation_a=None)[0], "incomplete")
        # the non-composite word is A001's, unchanged
        self.assertEqual(lab_j3.verdict_word, lab_j2.verdict_word)
        self.assertEqual(lab_j3.pass_test, lab_j2.pass_test)
        self.assertEqual(lab_j3.lift_reading, lab_j2.lift_reading)
        self.assertEqual(lab_j3.lift_reading(_paired(0.1, 0.01, 0.2)), "helps")
        self.assertEqual(lab_j3.lift_reading(_paired(-0.1, -0.2, -0.01)), "hurts")
        self.assertEqual(lab_j3.lift_reading(_paired(0.0, -0.1, 0.1)), "not_told_apart")
        self.assertIsNone(lab_j3.lift_reading(None))
        # equality fails the strict clauses (B17)
        self.assertEqual(_word(ablation_b=_paired(0.1, 0.0, 0.2))[1]["ablation_clauses"]["beats_ablation_b"], False)
        self.assertEqual(_word(ablation_b=_paired(0.1, 0.001, 0.2))[1]["ablation_clauses"]["beats_ablation_b"], True)


# --------------------------------------------------------------------------------------------------- the draw

def restamp(base: Path, rel: str, data: bytes) -> None:
    """Rewrite a file of an artifact and its manifest entry, as a consistent artifact would have it."""
    (base / rel).write_bytes(data)
    manifest = read_json(base / "prereg" / "prereg.json")
    manifest["files"][rel] = {"sha256": sha256_hex(data), "bytes": len(data)}
    (base / "prereg" / "prereg.json").write_bytes(canonical_bytes(manifest) + b"\n")


class ExclusionTests(unittest.TestCase):
    """The data and the draw as amended B3, B5, B21 and B22: the A001 artifact check and its agreement with A001's
    check, the A001 rebuild, the five sources and the stop rules on synthetic artifacts, the labels record and the
    draw over the eligible list less the 300 refs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows, cls.kept = j2labels.nhtsa_rows(ARCHIVE)
        cls.eligible, cls.counts = j2labels.eligible_records(PACK, cls.rows)
        cls.seed1 = goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1, fetch=lambda: ARCHIVE)[0]
        cls.j001 = refs_of_labels(cls.seed1)
        cls.seed2 = j2labels.build_labels(NHTSA_PACK, 150, 2, fetch=lambda: ARCHIVE, excluded=cls.j001,
                                          mechanisms={"artifact": "verified", "rebuild": "verified"})[0]
        cls.a001 = refs_of_labels(cls.seed2)

    def artifacts(self, blob: bytes = ARCHIVE) -> tuple[Path, list[dict[str, Any]]]:
        """Both synthetic earlier artifacts, fresh: (their directory of run ids, the two stand-in entries)."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-j3-earlier-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp, j3_data.stand_in(tmp, blob)

    def test_the_synthetic_archive_fits_three_draws(self) -> None:
        """B21: 90 complaints per make, about 540 eligible, so the two stand-ins and the dry run's own draw fit."""
        self.assertEqual(j3_data.PER_MAKE, 90)
        self.assertEqual(self.counts["eligible"], 540)
        self.assertEqual((len(self.j001), len(self.a001), len(self.j001 & self.a001)), (150, 150, 0))
        third = j2labels.draw(self.eligible, self.j001 | self.a001, 150, 3)
        self.assertEqual(len(third), 150)
        self.assertEqual({r["record_ref"] for r, _ in third} & (self.j001 | self.a001), set())
        sixty = j2labels.eligible_records(PACK, j2labels.nhtsa_rows(j2_data.archive(60, 1))[0])[0]
        self.assertEqual(len(sixty), 360)
        with self.assertRaises(j2labels.J2LabelsError):
            j2labels.draw(sixty, [p[0]["record_ref"] for p in sixty[:300]], 150, 3)

    def test_the_a001_artifact_check_on_a_consistent_artifact(self) -> None:
        """B3: the labels file's bytes hash to the entry's prefix, the manifest agrees, ``labels.json`` is a
        ``lab_j2_labels`` record of nhtsa, 150, seed 2, and the questions' refs are the labels': the 150 refs come
        back; the chain file is read by A001's code unchanged."""
        directory, entries = self.artifacts()
        first, second = entries
        self.assertEqual(j3labels.earlier_labels_refs(directory, second), (self.a001, "verified"))
        self.assertEqual(j3labels.earlier_labels_refs(str(directory), second), (self.a001, "verified"))
        self.assertEqual(second["sha256_prefix"], sha256_hex(self.seed2)[:12])
        self.assertEqual(second["rebuild_sha256_prefix"], second["sha256_prefix"])
        self.assertEqual((second["run_id"], second["artifact"], second["file"], second["rebuild_seed"],
                          second["chain_file"]),
                         (j3_data.A001_RUN_ID, j3_data.A001_ARTIFACT, "prereg/j2/labels.jsonl", 2,
                          "prereg/j2/excluded.jsonl"))
        self.assertEqual((first["run_id"], first["file"], first["rebuild_seed"], first["sha256_prefix"]),
                         (j3_data.J001_RUN_ID, "prereg/j1/labels.jsonl", 1, sha256_hex(self.seed1)[:12]))
        chain = j3labels.chain_entry(second)
        self.assertEqual(chain, {"run_id": second["run_id"], "artifact": second["artifact"],
                                 "file": "prereg/j2/excluded.jsonl", "sha256_prefix": second["chain_sha256_prefix"]})
        self.assertEqual(j2labels.earlier_refs(directory, chain), (self.j001 | self.a001, "verified"))
        self.assertEqual(j3labels.earlier_labels_refs(directory, chain), j2labels.earlier_refs(directory, chain))
        # a longer prefix, up to the whole sha256, verifies too; the j1 layout reads through the same function
        self.assertEqual(j3labels.earlier_labels_refs(directory, {**second, "sha256_prefix": sha256_hex(self.seed2)}),
                         (self.a001, "verified"))
        self.assertEqual(j3labels.earlier_labels_refs(directory, first), (self.j001, "verified"))
        self.assertEqual(j3labels.earlier_labels_refs(None, second), (None, "artifact absent"))

    def a001_cases(self, directory: Path, entry: dict[str, Any]) -> dict[str, tuple[Callable[[], None],
                                                                                    dict[str, Any], str]]:
        base = directory / str(entry["run_id"])
        labels_rel, record_rel, questions_rel = ("prereg/j2/labels.jsonl", "prereg/j2/labels.json",
                                                 "prereg/j2/questions.jsonl")
        labels = (base / labels_rel).read_bytes()
        changed = labels.replace(b"stalled", b"stopped", 1)
        self.assertNotEqual(changed, labels)
        questions = read_lines(base / questions_rel)
        other_ref = dict(questions[0], record_ref="999999999")
        record = read_json(base / record_rel)
        broken = labels + b"{not a line\n"
        return {
            "no directory": (lambda: None, {"directory": None}, "artifact absent"),
            "another directory": (lambda: None, {"directory": base / "nowhere"}, "artifact absent"),
            "another run id": (lambda: None, {"entry": {**entry, "run_id": entry["run_id"] + 1}}, "artifact absent"),
            "a file the artifact lacks": (lambda: None, {"entry": {**entry, "file": "prereg/j1/labels.jsonl"}},
                                          "artifact absent"),
            "a file outside the fixed list": (lambda: None, {"entry": {**entry, "file": questions_rel}}, "malformed"),
            "another prefix": (lambda: None, {"entry": {**entry, "sha256_prefix": "0" * 12}}, "sha256 mismatch"),
            "a changed byte": (lambda: (base / labels_rel).write_bytes(changed), {}, "sha256 mismatch"),
            "a changed byte the request expects": (lambda: (base / labels_rel).write_bytes(changed),
                                                   {"entry": {**entry, "sha256_prefix": sha256_hex(changed)[:12]}},
                                                   "manifest mismatch"),
            "a manifest that disagrees on the questions": (
                lambda: (base / questions_rel).write_bytes((base / questions_rel).read_bytes() + b"\n"), {},
                "manifest mismatch"),
            "a manifest that disagrees on the record": (
                lambda: (base / record_rel).write_bytes((base / record_rel).read_bytes() + b"\n"), {},
                "manifest mismatch"),
            "other refs in the questions": (
                lambda: restamp(base, questions_rel, jsonl([other_ref, *questions[1:]])), {}, "refs mismatch"),
            "a questions file of fewer records": (
                lambda: restamp(base, questions_rel, jsonl(questions[2:])), {}, "refs mismatch"),
            "a record of another seed": (
                lambda: restamp(base, record_rel, canonical_bytes({**record, "seed": 1}) + b"\n"), {},
                "labels mismatch"),
            "a record of another n": (
                lambda: restamp(base, record_rel, canonical_bytes({**record, "n": 149}) + b"\n"), {},
                "labels mismatch"),
            "a record of another source": (
                lambda: restamp(base, record_rel, canonical_bytes({**record, "source": "generator"}) + b"\n"),
                {}, "labels mismatch"),
            "a record of another kind": (
                lambda: restamp(base, record_rel, canonical_bytes({**record, "kind": "lab_j1_labels"}) + b"\n"),
                {}, "labels mismatch"),
            "a manifest that is not json": (
                lambda: (base / "prereg" / "prereg.json").write_bytes(b"{not json"), {}, "malformed"),
            "a manifest without files": (
                lambda: (base / "prereg" / "prereg.json").write_bytes(b'{"kind":"lab_prereg"}\n'), {},
                "manifest mismatch"),
            "labels that are not label lines": (
                lambda: restamp(base, labels_rel, b'{"record": 1}\n'),
                {"entry": {**entry, "sha256_prefix": sha256_hex(b'{"record": 1}\n')[:12]}}, "malformed"),
            "a malformed line": (
                lambda: restamp(base, labels_rel, broken),
                {"entry": {**entry, "sha256_prefix": sha256_hex(broken)[:12]}}, "malformed"),
            "a record that is not an object": (
                lambda: restamp(base, record_rel, b"[]\n"), {}, "labels mismatch"),
            "a manifest file entry without a size": (
                lambda: (base / "prereg" / "prereg.json").write_bytes(canonical_bytes(
                    {"files": {labels_rel: {"sha256": sha256_hex(labels)}}}) + b"\n"), {}, "manifest mismatch"),
        }

    def test_the_a001_artifact_check_s_fixed_outcomes(self) -> None:
        """B3: a changed byte, a manifest that disagrees, a labels record of another seed, n, source or kind, a
        questions file with other refs, an absent directory, an absent file and a malformed line each fail with one
        fixed outcome and no refs."""
        directory, entries = self.artifacts()
        for label in self.a001_cases(directory, entries[1]):
            directory, entries = self.artifacts()
            change, args, want = self.a001_cases(directory, entries[1])[label]
            change()
            got = j3labels.earlier_labels_refs(args.get("directory", directory), args.get("entry", entries[1]))
            with self.subTest(case=label):
                self.assertEqual(got, (None, want))
                self.assertIn(want, j3labels.OUTCOMES)

    def test_the_check_agrees_with_a001_s_on_the_j001_shaped_artifact_case_for_case(self) -> None:
        """B3: on the j1 layout, ``earlier_labels_refs`` gives ``lab.j2labels.earlier_refs``' outcome, case for
        case, A001's test table replayed through both."""
        directory, entries = self.artifacts()
        entry = {k: entries[0][k] for k in ("run_id", "artifact", "file", "sha256_prefix")}
        base = directory / str(entry["run_id"])
        labels_rel, record_rel, questions_rel = ("prereg/j1/labels.jsonl", "prereg/j1/labels.json",
                                                 "prereg/j1/questions.jsonl")
        labels = (base / labels_rel).read_bytes()
        changed = labels.replace(b"stalled", b"stopped", 1)
        questions = read_lines(base / questions_rel)
        record = read_json(base / record_rel)
        cases: dict[str, tuple[Callable[[Path], None], dict[str, Any]]] = {
            "consistent": (lambda b: None, {}),
            "no directory": (lambda b: None, {"directory": None}),
            "another run id": (lambda b: None, {"entry": {**entry, "run_id": entry["run_id"] + 1}}),
            "a file outside the fixed list": (lambda b: None, {"entry": {**entry, "file": questions_rel}}),
            "another prefix": (lambda b: None, {"entry": {**entry, "sha256_prefix": "0" * 12}}),
            "a changed byte": (lambda b: (b / labels_rel).write_bytes(changed), {}),
            "a changed byte the request expects": (lambda b: (b / labels_rel).write_bytes(changed),
                                                   {"entry": {**entry, "sha256_prefix": sha256_hex(changed)[:12]}}),
            "a manifest that disagrees": (
                lambda b: (b / questions_rel).write_bytes((b / questions_rel).read_bytes() + b"\n"), {}),
            "other refs in the questions": (
                lambda b: restamp(b, questions_rel, jsonl([dict(questions[0], record_ref="999999999"),
                                                           *questions[1:]])), {}),
            "a record of another seed": (
                lambda b: restamp(b, record_rel, canonical_bytes({**record, "seed": 2}) + b"\n"), {}),
            "a record of another n": (
                lambda b: restamp(b, record_rel, canonical_bytes({**record, "n": 149}) + b"\n"), {}),
            "a manifest that is not json": (lambda b: (b / "prereg" / "prereg.json").write_bytes(b"{not json"), {}),
            "a manifest without files": (
                lambda b: (b / "prereg" / "prereg.json").write_bytes(b'{"kind":"lab_prereg"}\n'), {}),
            "labels that are not label lines": (
                lambda b: restamp(b, labels_rel, b'{"record": 1}\n'),
                {"entry": {**entry, "sha256_prefix": sha256_hex(b'{"record": 1}\n')[:12]}}),
            "a record that is not an object": (lambda b: restamp(b, record_rel, b"[]\n"), {}),
        }
        for label, (change, args) in cases.items():
            directory, entries = self.artifacts()
            change(directory / str(entry["run_id"]))
            where, which = args.get("directory", directory), args.get("entry", entry)
            with self.subTest(case=label):
                self.assertEqual(j3labels.earlier_labels_refs(where, which), j2labels.earlier_refs(where, which))
        directory, entries = self.artifacts()
        self.assertEqual(j3labels.earlier_labels_refs(directory, entry), (self.j001, "verified"))
        # the j2 labels file is outside A001's fixed list (malformed there) and a file of the fixed list here (absent
        # from the j1-shaped artifact): the one outcome the two checks do not share, by B3's generalisation
        asked = {**entry, "file": "prereg/j2/labels.jsonl"}
        self.assertEqual((j3labels.earlier_labels_refs(directory, asked), j2labels.earlier_refs(directory, asked)),
                         ((None, "artifact absent"), (None, "malformed")))

    def test_the_a001_rebuild(self) -> None:
        """Item 4: R2 is ``lab.j2labels.draw`` of 150 at seed 2 over the eligible list less J001's set; it verifies
        only when its labels' bytes hash to the pinned prefix; ``not attempted`` is the stop rules' word."""
        prefix = sha256_hex(self.seed2)[:12]
        self.assertEqual(j3labels.rebuild_a001_refs(lambda: ARCHIVE, self.j001, prefix),
                         (self.a001, "verified", sha256_hex(self.seed2)))
        self.assertEqual(j3labels.rebuild_a001_refs(lambda: ARCHIVE, self.j001),
                         (None, "sha256 mismatch", sha256_hex(self.seed2)))
        self.assertEqual(j3labels.rebuild_a001_refs(lambda: ARCHIVE, self.j001, "0" * 12)[1], "sha256 mismatch")
        # another J001 set gives another draw under the same seed: the sha differs and the prefix does not match
        refs, outcome, sha = j3labels.rebuild_a001_refs(lambda: ARCHIVE, set(), prefix)
        self.assertEqual((refs, outcome), (None, "sha256 mismatch"))
        self.assertNotEqual(sha, sha256_hex(self.seed2))
        self.assertEqual(j3labels.rebuild_a001_refs(lambda: ARCHIVE, set(), sha[:12], seed=2)[0] & self.j001 != set(),
                         True)
        self.assertEqual(j3labels.rebuild_a001_refs(lambda: j2_data.archive(5, 3), self.j001, prefix),
                         (None, "malformed", ""))
        self.assertEqual(j3labels.rebuild_a001_refs(lambda: ARCHIVE, self.j001, prefix, seed=1)[1], "sha256 mismatch")
        calls: list[int] = []

        def fetch() -> bytes:
            calls.append(1)
            return ARCHIVE
        j3labels.rebuild_a001_refs(fetch, self.j001, prefix)
        self.assertEqual(len(calls), 1)

    def establish(self, directory: Path | None, entries: list[dict[str, Any]]) -> j3labels.Established:
        return j3labels.establish(directory, entries[0], entries[1], lambda: ARCHIVE)

    def test_every_source_verifying_and_each_cycle_alone(self) -> None:
        """B5: with every source verified the counts are 0 and the union is 300; each cycle is established by its
        artifact alone, by its rebuild alone, and by both agreeing."""
        directory, entries = self.artifacts()
        est = self.establish(directory, entries)
        self.assertEqual(est.summary["sources"], {s: {"outcome": "verified", "refs": n} for s, n in
                                                  (("J1", 150), ("R1", 150), ("A1", 150), ("R2", 150), ("K", 300))})
        self.assertEqual(est.summary["cycles"], {"j001": {"established": True, "by": "artifact", "refs": 150},
                                                 "a001": {"established": True, "by": "artifact", "refs": 150}})
        self.assertEqual(est.summary["consistency"], {"j1_r1": 0, "a1_r2": 0, "outside_chain": 0, "chain_union": 0})
        self.assertEqual((est.summary["excluded"], est.summary["expected_excluded"], est.summary["problem"],
                          est.problem), (300, 300, None, None))
        self.assertEqual((est.excluded, est.cycle_sets), (self.j001 | self.a001, {"j001": self.j001,
                                                                                    "a001": self.a001}))
        self.assertEqual((est.shas["R1"], est.shas["R2"]), (sha256_hex(self.seed1), sha256_hex(self.seed2)))
        assert_no_ref(self, canonical_dumps(est.summary), est.excluded)
        # J001 by artifact alone (R1 fails), by rebuild alone (J1 absent); A001 the same
        first, second = entries
        cases = {
            "j001 by artifact alone": ([{**first, "rebuild_sha256_prefix": "0" * 12}, second],
                                       {"J1": "verified", "R1": "sha256 mismatch"}, ("artifact", "artifact"),
                                       {"j1_r1": None, "a1_r2": 0, "outside_chain": 0, "chain_union": 0}),
            "j001 by rebuild alone": ([{**first, "sha256_prefix": "0" * 12}, second],
                                      {"J1": "sha256 mismatch", "R1": "verified"}, ("rebuild", "artifact"),
                                      {"j1_r1": None, "a1_r2": 0, "outside_chain": 0, "chain_union": 0}),
            "a001 by artifact alone": ([first, {**second, "rebuild_sha256_prefix": "0" * 12}],
                                       {"A1": "verified", "R2": "sha256 mismatch"}, ("artifact", "artifact"),
                                       {"j1_r1": 0, "a1_r2": None, "outside_chain": 0, "chain_union": 0}),
            "a001 by rebuild alone": ([first, {**second, "sha256_prefix": "0" * 12}],
                                      {"A1": "sha256 mismatch", "R2": "verified"}, ("artifact", "rebuild"),
                                      {"j1_r1": 0, "a1_r2": None, "outside_chain": 0, "chain_union": 0}),
            "both cycles by rebuild alone": ([{**first, "sha256_prefix": "0" * 12}, {**second, "sha256_prefix": "0" * 12}],
                                             {"J1": "sha256 mismatch", "A1": "sha256 mismatch"}, ("rebuild", "rebuild"),
                                             {"j1_r1": None, "a1_r2": None, "outside_chain": 0, "chain_union": 0}),
        }
        for label, (used, outcomes, by, consistency) in cases.items():
            est = self.establish(directory, used)
            with self.subTest(case=label):
                self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in outcomes}, outcomes)
                self.assertEqual((est.summary["cycles"]["j001"]["by"], est.summary["cycles"]["a001"]["by"]), by)
                self.assertEqual(est.summary["consistency"], consistency)
                self.assertEqual((est.problem, est.excluded), (None, self.j001 | self.a001))
                self.assertEqual(j3labels.mechanisms(est), {"artifact": est.summary["sources"]["J1"]["outcome"],
                                                            "rebuild": est.summary["sources"]["R1"]["outcome"]})

    def test_the_chain_establishes_a_cycle_with_the_other_cycle_established(self) -> None:
        """B5: a cycle with no source of its own is K less the other cycle's set, by chain; the rebuild of A001 is
        attempted over the chain-given J001 set; K alone establishes neither."""
        directory, entries = self.artifacts()
        first, second = entries
        gone_j001 = {**first, "sha256_prefix": "0" * 12, "rebuild_sha256_prefix": "0" * 12}
        est = self.establish(directory, [gone_j001, second])
        self.assertEqual(est.summary["cycles"], {"j001": {"established": True, "by": "chain", "refs": 150},
                                                 "a001": {"established": True, "by": "artifact", "refs": 150}})
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("J1", "R1", "A1", "R2", "K")},
                         {"J1": "sha256 mismatch", "R1": "sha256 mismatch", "A1": "verified", "R2": "verified",
                          "K": "verified"})
        self.assertEqual(est.summary["consistency"], {"j1_r1": None, "a1_r2": 0, "outside_chain": 0, "chain_union": None})
        self.assertEqual((est.problem, est.excluded, est.cycle_sets["j001"]), (None, self.j001 | self.a001, self.j001))
        gone_a001 = {**second, "sha256_prefix": "0" * 12, "rebuild_sha256_prefix": "0" * 12}
        est = self.establish(directory, [first, gone_a001])
        self.assertEqual(est.summary["cycles"], {"j001": {"established": True, "by": "artifact", "refs": 150},
                                                 "a001": {"established": True, "by": "chain", "refs": 150}})
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("A1", "R2")},
                         {"A1": "sha256 mismatch", "R2": "sha256 mismatch"})
        self.assertEqual(est.summary["consistency"], {"j1_r1": 0, "a1_r2": None, "outside_chain": 0, "chain_union": None})
        self.assertEqual((est.problem, est.excluded, est.cycle_sets["a001"]), (None, self.j001 | self.a001, self.a001))
        # K alone: neither cycle established, R2 not attempted, the union is K's refs, and the plan job stops
        est = self.establish(directory, [gone_j001, gone_a001])
        self.assertEqual(est.summary["cycles"], {"j001": {"established": False, "by": None, "refs": None},
                                                 "a001": {"established": False, "by": None, "refs": None}})
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("J1", "R1", "A1", "R2", "K")},
                         {"J1": "sha256 mismatch", "R1": "sha256 mismatch", "A1": "sha256 mismatch",
                          "R2": "not attempted", "K": "verified"})
        self.assertEqual(est.summary["consistency"], {"j1_r1": None, "a1_r2": None, "outside_chain": 0,
                                                      "chain_union": None})
        self.assertEqual((est.problem, est.summary["problem"], est.excluded, est.shas["R2"]),
                         ("unverified", "unverified", self.j001 | self.a001, ""))
        # R2 not attempted whenever J001's set is not established, even with A1 verified
        no_chain = {**second, "chain_sha256_prefix": "0" * 12}
        est = self.establish(directory, [gone_j001, no_chain])
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("J1", "R1", "A1", "R2", "K")},
                         {"J1": "sha256 mismatch", "R1": "sha256 mismatch", "A1": "verified", "R2": "not attempted",
                          "K": "sha256 mismatch"})
        self.assertEqual((est.summary["cycles"]["j001"]["established"], est.summary["cycles"]["a001"]["by"],
                          est.problem, est.excluded), (False, "artifact", "unverified", self.a001))
        self.assertEqual(est.summary["consistency"], {"j1_r1": None, "a1_r2": None, "outside_chain": None,
                                                      "chain_union": None})
        # neither verifying: an absent directory and the wrong rebuild prefixes
        est = self.establish(None, [gone_j001, gone_a001])
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("J1", "R1", "A1", "R2", "K")},
                         {"J1": "artifact absent", "R1": "sha256 mismatch", "A1": "artifact absent",
                          "R2": "not attempted", "K": "artifact absent"})
        self.assertEqual((est.problem, est.excluded, est.summary["excluded"]), ("unverified", set(), 0))
        self.assertEqual(est.summary["consistency"], dict.fromkeys(j3labels.CONSISTENCY, None))

    def test_every_consistency_count_stops_the_plan_job(self) -> None:
        """B5: J1 and R1 disagreeing, A1 and R2 disagreeing, a verified source outside the chain, and a chain that
        is not the union of the two sets, each a count above 0 and the problem ``disagrees``."""
        other = j2_data.archive(j3_data.PER_MAKE, 2)                    # other record ids, the same shape
        # J1 from another archive (its refs are not R1's, rebuilt from ARCHIVE)
        directory, entries = self.artifacts()
        other_dir, other_entries = self.artifacts(other)
        j1_other = {**other_entries[0], "rebuild_sha256_prefix": entries[0]["rebuild_sha256_prefix"]}
        shutil.copytree(other_dir / str(j3_data.J001_RUN_ID), directory / "swap")
        shutil.rmtree(directory / str(j3_data.J001_RUN_ID))
        (directory / "swap").rename(directory / str(j3_data.J001_RUN_ID))
        est = self.establish(directory, [j1_other, entries[1]])
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("J1", "R1")},
                         {"J1": "verified", "R1": "verified"})
        self.assertEqual(est.summary["consistency"]["j1_r1"], 300)
        self.assertGreater(est.summary["consistency"]["outside_chain"], 0)
        self.assertEqual((est.problem, len(est.excluded)), ("disagrees", 450))
        # A1 drawn without J001's set excluded: its refs are not R2's
        directory, entries = self.artifacts()
        a001_dir = directory / str(j3_data.A001_RUN_ID)
        shutil.rmtree(a001_dir)
        loose = j3_data.a001_artifact(directory, ARCHIVE, set())
        loose = {**loose, "rebuild_sha256_prefix": entries[1]["rebuild_sha256_prefix"]}
        est = self.establish(directory, [entries[0], loose])
        self.assertEqual({s: est.summary["sources"][s]["outcome"] for s in ("A1", "R2")},
                         {"A1": "verified", "R2": "verified"})
        self.assertGreater(est.summary["consistency"]["a1_r2"], 0)
        self.assertEqual(est.problem, "disagrees")
        # a chain missing refs of a verified source: outside_chain above 0
        directory, entries = self.artifacts()
        base = directory / str(j3_data.A001_RUN_ID)
        short = j2labels.excluded_jsonl(sorted(self.j001 | self.a001)[:-5])
        restamp(base, "prereg/j2/excluded.jsonl", short)
        trimmed = {**entries[1], "chain_sha256_prefix": sha256_hex(short)[:12]}
        est = self.establish(directory, [entries[0], trimmed])
        self.assertEqual(est.summary["sources"]["K"], {"outcome": "verified", "refs": 295})
        self.assertEqual(est.summary["consistency"], {"j1_r1": 0, "a1_r2": 0, "outside_chain": 5, "chain_union": 5})
        self.assertEqual((est.problem, len(est.excluded)), ("disagrees", 300))
        # a chain with refs beyond the two sets: chain_union above 0 while outside_chain is 0
        wide = j2labels.excluded_jsonl(self.j001 | self.a001 | {"999999901", "999999902"})
        restamp(base, "prereg/j2/excluded.jsonl", wide)
        widened = {**entries[1], "chain_sha256_prefix": sha256_hex(wide)[:12]}
        est = self.establish(directory, [entries[0], widened])
        self.assertEqual(est.summary["consistency"], {"j1_r1": 0, "a1_r2": 0, "outside_chain": 0, "chain_union": 2})
        self.assertEqual((est.problem, len(est.excluded)), ("disagrees", 302))
        for cycle in j3labels.CYCLES:
            self.assertEqual(est.summary["cycles"][cycle]["by"], "artifact")

    def test_the_labels_record_names_every_source(self) -> None:
        """B22: ``exclusion.excluded_runs`` holds one entry per cycle with the artifact's, the rebuild's and (A001)
        the chain's outcomes and counts; ``mechanisms`` stays J001's pair; the sources, cycles and consistency
        counts sit beside them; counts and hashes only."""
        directory, entries = self.artifacts()
        used = artifact_only(entries)
        requested = [J001_ENTRY, A001_ENTRY]
        est = self.establish(directory, used)
        runs = j3labels.excluded_runs(est, (used[0], used[1]), (requested[0], requested[1]), True)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0], {
            "run_id": j3_data.J001_RUN_ID, "artifact": j2_data.ARTIFACT, "file": "prereg/j1/labels.jsonl",
            "sha256_prefix": used[0]["sha256_prefix"], "refs": 150, "outcome": "verified",
            "rebuild": {"seed": 1, "sha256_prefix": "bc6d092d8bca", "outcome": "sha256 mismatch", "refs": 0,
                        "sha256": sha256_hex(self.seed1)},
            "chain": None, "derived": None, "established_by": "artifact", "stand_in": True,
            "requested": {k: J001_ENTRY[k] for k in sorted(J001_ENTRY)}})
        self.assertEqual(runs[1], {
            "run_id": j3_data.A001_RUN_ID, "artifact": j3_data.A001_ARTIFACT, "file": "prereg/j2/labels.jsonl",
            "sha256_prefix": used[1]["sha256_prefix"], "refs": 150, "outcome": "verified",
            "rebuild": {"seed": 2, "sha256_prefix": "623780b8a1de", "outcome": "sha256 mismatch", "refs": 0,
                        "sha256": sha256_hex(self.seed2)},
            "chain": {"file": "prereg/j2/excluded.jsonl", "sha256_prefix": used[1]["chain_sha256_prefix"],
                      "outcome": "verified", "refs": 300},
            "derived": None, "established_by": "artifact", "stand_in": True,
            "requested": {k: A001_ENTRY[k] for k in sorted(A001_ENTRY)}})
        data, record = j2labels.build_labels(NHTSA_PACK, 40, 3, fetch=lambda: ARCHIVE, excluded=est.excluded,
                                             mechanisms=j3labels.mechanisms(est), excluded_runs=runs)
        drawn = refs_of_labels(data)
        self.assertEqual((len(drawn), drawn & est.excluded), (40, set()))
        exclusion = j3labels.extend_exclusion(record, est)
        self.assertIs(exclusion, record["exclusion"])
        self.assertEqual(sorted(exclusion), sorted(lab_j3.EXCLUSION_KEYS))
        self.assertEqual((exclusion["excluded"], exclusion["eligible_after_exclusion"], exclusion["overlap"],
                          exclusion["mechanisms"], exclusion["expected_excluded"]),
                         (300, 240, 0, {"artifact": "verified", "rebuild": "sha256 mismatch"}, 300))
        self.assertEqual(exclusion["sources"], est.summary["sources"])
        self.assertEqual(exclusion["cycles"], est.summary["cycles"])
        self.assertEqual(exclusion["consistency"], est.summary["consistency"])
        self.assertEqual(exclusion["excluded_sha256"], sha256_hex(j3labels.chain_bytes(est.excluded, drawn)))
        self.assertEqual(j3labels.chain_bytes(est.excluded, drawn), j2labels.excluded_jsonl(est.excluded | drawn))
        self.assertEqual(len(j3labels.chain_bytes(est.excluded, drawn).splitlines()), 340)
        self.assertEqual((record["kind"], record["n"], record["seed"], record["available"], record["records"]),
                         ("lab_j2_labels", 40, 3, 540, 40))
        assert_no_ref(self, canonical_dumps(record), drawn | est.excluded)
        # the derived reading is marked when a cycle came from the chain
        gone = {**used[1], "sha256_prefix": "0" * 12}
        by_chain = self.establish(directory, [used[0], gone])
        row = j3labels.excluded_runs(by_chain, (used[0], gone), (requested[0], requested[1]), False)[1]
        self.assertEqual((row["derived"], row["established_by"], row["outcome"], row["refs"], row["stand_in"]),
                         ("by_chain", "chain", "sha256 mismatch", 0, False))

    def test_the_draw_over_the_eligible_list_less_the_300(self) -> None:
        """Item 6: the fresh draw is the pinned ``draw`` over the eligible list less the union, disjoint from both
        synthetic earlier draws, and too few records left refuse with the fixed message."""
        excluded = self.j001 | self.a001
        drawn = j2labels.draw(self.eligible, excluded, 150, 3)
        refs = {r["record_ref"] for r, _ in drawn}
        self.assertEqual((len(refs), refs & self.j001, refs & self.a001), (150, set(), set()))
        self.assertNotEqual(refs, {r["record_ref"] for r, _ in j2labels.draw(self.eligible, self.j001, 150, 3)})
        left = [p for p in self.eligible if p[0]["record_ref"] not in excluded]
        self.assertEqual(len(left), 240)
        self.assertEqual(j2labels.draw(self.eligible, excluded, 240, 3), left)
        with self.assertRaises(j2labels.J2LabelsError) as refused:
            j2labels.draw(self.eligible, excluded, 241, 3)
        self.assertEqual(refused.exception.problem, "only 240 eligible unexcluded public records remain: no further "
                                                    "fresh draw of 241 records is pre-registered")


# --------------------------------------------------------------------------------------------------- request, plan

class RequestTests(unittest.TestCase):
    def blocks(self, block: dict[str, Any], models: list[str] | None = None) -> dict[str, Any]:
        obj = plumbing_min()
        if models is not None:
            obj["models"] = models
        obj["experiments"] = {"j3": block}
        return validate(obj, MANIFEST)["experiments"]["j3"]

    def error(self, block: dict[str, Any], models: list[str] | None = None) -> tuple[str, str]:
        with self.assertRaises(RequestError) as caught:
            self.blocks(block, models)
        return caught.exception.path, caught.exception.problem

    def test_defaults(self) -> None:
        ex = [J001_ENTRY, A001_ENTRY]
        base = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 3, "exclude": ex,
                "primary": {"model": "fake-a", "arm": "evidence_grounded"}}
        self.assertEqual(self.blocks(base), {"models": ["fake-a", "fake-b"], "minutes": 5, "labels": LABELS,
                                             "parts": 2, "seed": 3, "bootstrap_b": 10000, "bootstrap_seed": 1,
                                             "exclude": ex, "primary": {"model": "fake-a", "arm": "evidence_grounded"},
                                             "comparisons_before": 0})
        self.assertEqual(self.blocks({**base, "comparisons_before": 12})["comparisons_before"], 12)
        self.assertIn("j3", EXPERIMENTS)
        self.assertEqual(lab_request._J3_REQUIRED, ("minutes", "labels", "parts", "seed", "exclude", "primary"))
        self.assertEqual(lab_request._J3_KEYS, lab_request._J2_KEYS)
        self.assertEqual(lab_request._J3_EXCLUDE_KEYS, ("run_id", "artifact", "file", "sha256_prefix", "rebuild_seed",
                                                        "rebuild_sha256_prefix", "chain_file", "chain_sha256_prefix"))
        # the file is pinned per entry (the rule's "The request"): the three files of lab.j3labels.EXCLUDE_FILES are
        # what the j3 kind reads, the chain file through the second entry's chain keys alone
        self.assertEqual(j3labels.EXCLUDE_FILES, ("prereg/j1/labels.jsonl", "prereg/j2/excluded.jsonl",
                                                  "prereg/j2/labels.jsonl"))
        self.assertEqual([(e["file"], e.get("chain_file")) for e in self.blocks(base)["exclude"]],
                         [("prereg/j1/labels.jsonl", None), ("prereg/j2/labels.jsonl", "prereg/j2/excluded.jsonl")])

    def test_every_block_error(self) -> None:
        ex = [J001_ENTRY, A001_ENTRY]
        base = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 1, "exclude": ex,
                "primary": {"model": "fake-b", "arm": "evidence"}}
        path = "$.experiments.j3"
        first, second = ex

        def with_first(**changes: Any) -> list[dict[str, Any]]:
            return [{**first, **changes}, second]

        def with_second(**changes: Any) -> list[dict[str, Any]]:
            return [first, {**second, **changes}]

        seed_range = "must be an int in [0, 2147483647]"
        cases = [
            ({**base, "extra": 1}, None, (f"{path}.extra", "unknown key")),
            ({k: v for k, v in base.items() if k != "exclude"}, None, (f"{path}.exclude", "required")),
            ({k: v for k, v in base.items() if k != "primary"}, None, (f"{path}.primary", "required")),
            ({**base, "models": ["fake-a", "h-a"]}, ["fake-a", "h-a"], (f"{path}.models[1]", NHTSA_HOSTED)),
            ({**base, "models": ["nope"]}, None, (f"{path}.models[0]", "not one of $.models")),
            ({**base, "minutes": 21}, None,
             (f"{path}.minutes", "exceeds the shard capacity (job_minutes minus the shard overhead)")),
            ({**base, "labels": {"source": "generator", "pack": "device_quality", "n": 40, "seed": 1}}, None,
             (f"{path}.labels.source", J3_LABELS_PROBLEM)),
            ({**base, "labels": {**LABELS, "n": 39}}, None, (f"{path}.labels.n", "must be an int in [40, 2000]")),
            ({**base, "parts": 31}, None, (f"{path}.parts", "must be an int in [1, 30]")),
            ({**base, "bootstrap_b": 999}, None, (f"{path}.bootstrap_b", "must be an int in [1000, 20000]")),
            ({**base, "comparisons_before": -1}, None,
             (f"{path}.comparisons_before", "must be an int in [0, 1000000]")),
            ({**base, "exclude": first}, None, (f"{path}.exclude", J3_EXCLUDE_LIST_PROBLEM)),
            ({**base, "exclude": [first]}, None, (f"{path}.exclude", J3_EXCLUDE_LIST_PROBLEM)),
            ({**base, "exclude": [first, second, second]}, None, (f"{path}.exclude", J3_EXCLUDE_LIST_PROBLEM)),
            ({**base, "exclude": ["run", second]}, None, (f"{path}.exclude[0]", "must be an object")),
            ({**base, "exclude": with_first(extra=1)}, None, (f"{path}.exclude[0].extra", "unknown key")),
            ({**base, "exclude": [{k: v for k, v in first.items() if k != "rebuild_seed"}, second]}, None,
             (f"{path}.exclude[0].rebuild_seed", "required")),
            ({**base, "exclude": [{k: v for k, v in first.items() if k != "rebuild_sha256_prefix"}, second]}, None,
             (f"{path}.exclude[0].rebuild_sha256_prefix", "required")),
            ({**base, "exclude": with_first(run_id=0)}, None,
             (f"{path}.exclude[0].run_id", "must be an int in [1, 100000000000000000000]")),
            ({**base, "exclude": with_first(artifact="lab-plan-1-1")}, None,
             (f"{path}.exclude[0].artifact", J2_ARTIFACT_PROBLEM)),
            ({**base, "exclude": with_first(file="prereg/j1/questions.jsonl")}, None,
             (f"{path}.exclude[0].file", J3_FILE_PROBLEM)),
            ({**base, "exclude": with_second(file="../prereg/j2/labels.jsonl")}, None,
             (f"{path}.exclude[1].file", J3_FILE_PROBLEM)),
            # the file pinned per entry: a file of the list on the wrong entry is refused (the rule's "The request")
            ({**base, "exclude": with_first(file="prereg/j2/labels.jsonl")}, None,
             (f"{path}.exclude[0].file", J3_FIRST_FILE_PROBLEM)),
            ({**base, "exclude": with_first(file="prereg/j2/excluded.jsonl")}, None,
             (f"{path}.exclude[0].file", J3_FIRST_FILE_PROBLEM)),
            ({**base, "exclude": with_second(file="prereg/j1/labels.jsonl")}, None,
             (f"{path}.exclude[1].file", J3_SECOND_FILE_PROBLEM)),
            ({**base, "exclude": with_second(file="prereg/j2/excluded.jsonl")}, None,
             (f"{path}.exclude[1].file", J3_SECOND_FILE_PROBLEM)),
            ({**base, "exclude": with_first(sha256_prefix="ABCDEF123456")}, None,
             (f"{path}.exclude[0].sha256_prefix", J2_SHA_PROBLEM)),
            ({**base, "exclude": with_second(rebuild_sha256_prefix="abcdef12345")}, None,
             (f"{path}.exclude[1].rebuild_sha256_prefix", J2_SHA_PROBLEM)),
            ({**base, "exclude": with_first(rebuild_seed=-1)}, None, (f"{path}.exclude[0].rebuild_seed", seed_range)),
            ({**base, "exclude": with_first(rebuild_seed=2)}, None,
             (f"{path}.exclude[0].rebuild_seed", J3_FIRST_SEED_PROBLEM)),
            ({**base, "exclude": with_second(rebuild_seed=1)}, None,
             (f"{path}.exclude[1].rebuild_seed", J3_SEED_ORDER_PROBLEM)),
            ({**base, "exclude": with_second(rebuild_seed=0)}, None,
             (f"{path}.exclude[1].rebuild_seed", J3_SEED_ORDER_PROBLEM)),
            ({**base, "exclude": with_first(chain_file="prereg/j2/excluded.jsonl", chain_sha256_prefix="a" * 12)},
             None, (f"{path}.exclude[0].chain_file", J3_CHAIN_FIRST_PROBLEM)),
            ({**base, "exclude": with_first(chain_sha256_prefix="a" * 12)}, None,
             (f"{path}.exclude[0].chain_sha256_prefix", J3_CHAIN_FIRST_PROBLEM)),
            ({**base, "exclude": [first, {k: v for k, v in second.items() if k != "chain_file"}]}, None,
             (f"{path}.exclude[1].chain_file", J3_CHAIN_REQUIRED)),
            ({**base, "exclude": [first, {k: v for k, v in second.items() if k != "chain_sha256_prefix"}]}, None,
             (f"{path}.exclude[1].chain_sha256_prefix", J3_CHAIN_REQUIRED)),
            ({**base, "exclude": with_second(chain_file="prereg/j2/labels.jsonl")}, None,
             (f"{path}.exclude[1].chain_file", J3_CHAIN_FILE_PROBLEM)),
            ({**base, "exclude": with_second(chain_sha256_prefix="g" * 12)}, None,
             (f"{path}.exclude[1].chain_sha256_prefix", J2_SHA_PROBLEM)),
            ({**base, "primary": {"model": "fake-slow", "arm": "evidence"}}, None,
             (f"{path}.primary.model", J3_PRIMARY_MODEL_PROBLEM)),
            ({**base, "models": ["fake-a"], "primary": {"model": "fake-b", "arm": "evidence"}}, None,
             (f"{path}.primary.model", J3_PRIMARY_MODEL_PROBLEM)),
            ({**base, "primary": {"model": "fake-b", "arm": "shipped"}}, None,
             (f"{path}.primary.arm", J2_PRIMARY_ARM_PROBLEM)),
            ({**base, "primary": {"model": "fake-b", "arm": "Eg"}}, None,
             (f"{path}.primary.arm", J2_PRIMARY_ARM_PROBLEM)),
            ({**base, "primary": {"model": "fake-b"}}, None, (f"{path}.primary.arm", "required")),
        ]
        for block, models, want in cases:
            with self.subTest(case=want):
                self.assertEqual(self.error(block, models), want)
        # every declared arm is a primary the validator accepts
        for arm in lab_j3.ARMS:
            self.assertEqual(self.blocks({**base, "primary": {"model": "fake-b", "arm": arm}})["primary"]["arm"], arm)

    def test_the_template_gives_five_units_in_five_shards(self) -> None:
        """The settings table: 5 units of 300 minutes, one per shard, job_minutes 330, max_parallel 5, both excluded
        runs named, P = (a-4b, evidence), seeds 3, parts 5, B 10,000, bootstrap seed 3, comparisons_before 12."""
        manifest = load_manifest(LAB_MANIFEST)
        request = load_request(TEMPLATES / "accuracy-002.json", manifest)
        purpose = request.data["purpose"]
        self.assertIn("A002", purpose)
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9-])[0-9]+(?![A-Za-z0-9-])", purpose))   # no digit that could be a value
        self.assertEqual((request.data["job_minutes"], request.data["max_parallel"], request.data["retention_days"],
                          request.data["provider"], request.data["models"]), (330, 5, 30, "llama-server", ["a-4b"]))
        plan = build_plan(request, manifest, "unknown")
        self.assertEqual((len(plan["units"]), len(plan["shards"]), plan["max_parallel"]), (5, 5, 5))
        self.assertEqual({len(s["units"]) for s in plan["shards"]}, {1})
        self.assertEqual({(s["planned_minutes"], s["timeout_minutes"]) for s in plan["shards"]}, {(300, 325)})
        unit = next(u for u in plan["units"] if u["unit"] == "j3-a-4b-p5")
        self.assertEqual(unit["params"], {
            "pack": NHTSA_PACK, "labels": {**LABELS, "n": 150}, "endpoint": "a-4b", "part": 5, "parts": 5, "seed": 3,
            "bootstrap_b": 10000, "bootstrap_seed": 3, "exclude": [J001_ENTRY, A001_ENTRY],
            "primary": {"model": "a-4b", "arm": "evidence"}, "comparisons_before": 12})
        self.assertEqual((unit["minutes"], unit["seeds"], unit["kind"]), (300, [3], "gguf"))
        self.assertEqual({tuple(e["run_id"] for e in u["params"]["exclude"]) for u in plan["units"]},
                         {(37996675559, 38114522160)})
        outputs = lab_plan.plan_outputs(plan, "0" * 64)
        self.assertEqual((outputs["j2_exclude_artifact"], outputs["j2_exclude_run_id"], outputs["j3_exclude_artifact_2"],
                          outputs["j3_exclude_run_id_2"], outputs["has_units"]),
                         ("lab-plan-37996675559-1", "37996675559", "lab-plan-38114522160-1", "38114522160", "true"))
        self.assertEqual(list(outputs), list(lab_plan.OUTPUT_KEYS))
        self.assertEqual(lab_plan.exclude_entries(plan), [J001_ENTRY, A001_ENTRY])

    def test_the_plan_outputs_on_a_j3_a_j2_and_another_plan(self) -> None:
        """B2: a j2 plan fills the first pair only, a j3 plan both, any other plan neither."""
        manifest = load_manifest(LAB_MANIFEST)
        j2 = build_plan(load_request(TEMPLATES / "accuracy.json", manifest), manifest, "unknown")
        outputs = lab_plan.plan_outputs(j2, "0" * 64)
        self.assertEqual((outputs["j2_exclude_artifact"], outputs["j2_exclude_run_id"], outputs["j3_exclude_artifact_2"],
                          outputs["j3_exclude_run_id_2"]), ("lab-plan-37996675559-1", "37996675559", "", ""))
        self.assertEqual(lab_plan.exclude_entries(j2), [lab_plan.j2_exclude(j2)])
        other = build_plan(load_request(ROOT / "lab" / "requests" / "plumbing-001.json", manifest), manifest, "unknown")
        outputs = lab_plan.plan_outputs(other, "0" * 64)
        self.assertEqual([outputs[k] for k in ("j2_exclude_artifact", "j2_exclude_run_id", "j3_exclude_artifact_2",
                                               "j3_exclude_run_id_2")], ["", "", "", ""])
        self.assertIsNone(lab_plan.exclude_entries(other))
        self.assertIsNone(lab_plan.exclude_entries(None))
        self.assertIsNone(lab_plan.j2_exclude(build_plan(load_request(TEMPLATES / "accuracy-002.json", manifest),
                                                         manifest, "unknown")))
        none = lab_plan.plan_outputs(None, "")
        self.assertEqual([none[k] for k in ("j2_exclude_artifact", "j2_exclude_run_id", "j3_exclude_artifact_2",
                                            "j3_exclude_run_id_2")], ["", "", "", ""])
        self.assertEqual(lab_plan.OUTPUT_KEYS, ("has_provision", "has_units", "j2_exclude_artifact",
                                                "j2_exclude_run_id", "j3_exclude_artifact_2", "j3_exclude_run_id_2",
                                                "matrix", "max_parallel", "plan_sha256", "provision_matrix",
                                                "retention_days", "result_class"))


# --------------------------------------------------------------------------------------------------- preregistration

class PreregTests(unittest.TestCase):
    def test_the_preregistration(self) -> None:
        p = _Prereg.get()
        m = p.manifest["j3"]
        self.assertEqual(sorted(path.name for path in p.dir.iterdir()),
                         ["ablation_a.json", "ablation_a.jsonl", "ablation_b.json", "ablation_b.jsonl",
                          "excluded.jsonl", "labels.json", "labels.jsonl", "lexical.json", "lexical.jsonl",
                          "prereg.json", "prior.json", "prior.jsonl", "questions.json", "questions.jsonl",
                          "routing.json"])
        doc = read_json(p.dir / "prereg.json")
        self.assertEqual(sorted(doc), sorted(lab_j3.PREREG_KEYS))
        self.assertEqual((doc["kind"], doc["experiment"], doc["boundary"], doc["data_label"], doc["parts"],
                          doc["seed"], doc["bootstrap_b"], doc["bootstrap_seed"], doc["withhold_share"]),
                         ("lab_j3_prereg", "J3", "site:lab", "public", 2, 3, 1000, 3, 0.01))
        self.assertEqual(doc["labels"]["sha256"], hashlib.sha256((p.dir / "labels.jsonl").read_bytes()).hexdigest())
        self.assertEqual((doc["labels"]["available"], doc["labels"]["source"], doc["labels"]["n"], doc["labels"]["seed"],
                          doc["labels"]["records"]), (540, "nhtsa", 40, 3, 40))
        self.assertEqual((doc["questions"]["records"], doc["questions"]["questions"], doc["questions"]["parts"],
                          doc["questions"]["seed"], doc["questions"]["negative_draw"]),
                         (40, 80, 2, 3, "other_records_positive_frequency"))
        for name in ("lexical", "prior", "ablation_a", "ablation_b"):
            self.assertEqual(doc[name]["sha256"], hashlib.sha256((p.dir / f"{name}.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["ablation_a"], {"sha256": doc["ablation_a"]["sha256"], "name": "lexical_or_no",
                                             "reply": {"mentions_entity": "yes", "describes_predicate": "no"}})
        self.assertEqual(doc["ablation_b"], {"sha256": doc["ablation_b"]["sha256"], "name": "lexical_or_record_blind",
                                             "reply": "record_blind"})
        self.assertEqual((doc["tasks"], doc["mapping"], doc["grounding"], doc["composite"], doc["arms"], doc["lifts"],
                          doc["ablation_differences"]),
                         (lab_j3.task_pins(), lab_j3.mapping_pin(), lab_j3.grounding_pin(), lab_j3.composite_pin(),
                          lab_j3.arms_doc(), lab_j3.lifts_doc(), lab_j3.ablation_differences_doc()))
        self.assertEqual(doc["tasks"]["order"], [E, R, N])
        self.assertEqual((doc["primary"], doc["headline_arm"], doc["headline_secondary"]),
                         ({"model": "fake-b", "arm": "evidence"}, "evidence", "evidence_grounded"))
        self.assertEqual((doc["comparisons_before"], doc["target_balanced_accuracy"]), (0, 0.86))
        self.assertEqual(sorted(doc["exclusion"]), sorted(lab_j3.EXCLUSION_KEYS))
        ex = doc["exclusion"]
        self.assertEqual((ex["excluded"], ex["eligible_after_exclusion"], ex["overlap"], ex["mechanisms"],
                          ex["expected_excluded"]), (300, 240, 0, {"artifact": "verified", "rebuild": "sha256 mismatch"},
                                                     300))
        self.assertEqual({s: ex["sources"][s]["outcome"] for s in j3labels.SOURCES}, ARTIFACT_ONLY)
        self.assertEqual({c: ex["cycles"][c]["by"] for c in j3labels.CYCLES}, {"j001": "artifact", "a001": "artifact"})
        self.assertEqual(ex["consistency"], {"j1_r1": None, "a1_r2": None, "outside_chain": 0, "chain_union": 0})
        self.assertEqual(ex["complaint_file_sha256"], sha256_hex(ARCHIVE))
        self.assertEqual(doc["code_hash"], lab_j3.code_hash())
        for name in ("mycelic/collective/edge/verify.py", "lab/j1.py", "lab/j2.py", "lab/j2labels.py", "lab/j3.py",
                     "lab/j3labels.py"):
            self.assertIn(name, doc["code_files"])
        self.assertEqual([e["name"] for e in doc["endpoints"]], ["fake-a", "fake-b"])
        routing = read_json(p.dir / "routing.json")
        self.assertEqual((sorted(routing["endpoints"]), routing["routes"]), (["fake-a", "fake-b"], {}))
        # the comparators and the ablations are J001's and this cycle's code on the fresh draw (rules 5 and 8)
        qs = lab_j1.read_questions((p.dir / "questions.jsonl").read_bytes())
        lexical = read_lines(p.dir / "lexical.jsonl")
        prior = read_lines(p.dir / "prior.jsonl")
        self.assertEqual(lexical, lab_j1.lexical_verdicts(PACK, (p.dir / "labels.jsonl").read_bytes(), qs))
        self.assertEqual(prior, lab_j1.prior_verdicts(qs))
        self.assertEqual(read_lines(p.dir / "ablation_a.jsonl"), lab_j3.ablation_a_lines(qs, lexical))
        self.assertEqual(read_lines(p.dir / "ablation_b.jsonl"), lab_j3.ablation_b_lines(qs, lexical, prior))
        for name in ("lexical", "prior", "ablation_a", "ablation_b"):
            self.assertEqual(m[name], lab_j1.score(qs, read_lines(p.dir / f"{name}.jsonl"), bootstrap_b=1000,
                                                   bootstrap_seed=3))
            self.assertEqual(m[name], read_json(p.dir / f"{name}.json"))
        self.assertEqual(m["prior_bound"], lab_j1.prior_bound(qs))
        self.assertEqual(m["lexical_unknown"], sum(1 for line in lexical if line["verdict"] == "unknown"))
        self.assertEqual((m["endpoints"], m["prereg"], m["parts"], m["seed"], m["stand_in"], m["bootstrap_seed"]),
                         (["fake-a", "fake-b"], "prereg/j3/prereg.json", 2, 3, False, 3))
        self.assertEqual((m["primary"], m["headline_arm"], m["headline_secondary"], m["comparisons_before"],
                          m["target_balanced_accuracy"]),
                         ({"model": "fake-b", "arm": "evidence"}, "evidence", "evidence_grounded", 0, 0.86))
        for key in ("tasks", "mapping", "grounding", "composite", "arms", "lifts", "ablation_differences"):
            self.assertEqual(m[key], doc[key])
        self.assertEqual(sorted(m["exclusion"]), sorted([*lab_j3.EXCLUSION_KEYS, "rebuilt_sha256",
                                                         "rebuild_expected_prefix", "stand_in"]))
        self.assertEqual(m["exclusion"]["rebuild_expected_prefix"], {"j001": "bc6d092d8bca", "a001": "623780b8a1de"})
        self.assertEqual(m["exclusion"]["rebuilt_sha256"], {"j001": sha256_hex(seed_one_labels()),
                                                            "a001": sha256_hex(seed_two_labels())})
        lab_prereg.load_prereg(p.plan_path)

    def test_the_draw_excludes_both_earlier_cycles_records(self) -> None:
        """Items 1 to 8 end to end: the 300 refs are out of the draw; the chain file holds every excluded ref and
        the draw's; the record names both excluded runs with counts and hashes only; the fixed console lines."""
        p = _Prereg.get()
        self.assertEqual((len(p.j001), len(p.a001), len(p.excluded), len(p.drawn)), (150, 150, 300, 40))
        self.assertEqual(p.drawn & p.excluded, set())
        excluded_lines = read_lines(p.dir / "excluded.jsonl")
        self.assertEqual(len(excluded_lines), 340)
        self.assertEqual({line["record_ref"] for line in excluded_lines}, p.excluded | p.drawn)
        self.assertEqual((p.dir / "excluded.jsonl").read_bytes(), j3labels.chain_bytes(p.excluded, p.drawn))
        labels = read_json(p.dir / "labels.json")
        ex = labels["exclusion"]
        self.assertEqual((labels["kind"], labels["n"], labels["seed"], labels["available"]),
                         ("lab_j2_labels", 40, 3, 540))
        runs = ex["excluded_runs"]
        self.assertEqual([(r["run_id"], r["outcome"], r["refs"], r["rebuild"]["outcome"], r["rebuild"]["seed"],
                           r["established_by"], r["derived"], r["stand_in"], r["requested"]) for r in runs],
                         [(j3_data.J001_RUN_ID, "verified", 150, "sha256 mismatch", 1, "artifact", None, False,
                           {k: p.exclude[0][k] for k in sorted(p.exclude[0])}),
                          (j3_data.A001_RUN_ID, "verified", 150, "sha256 mismatch", 2, "artifact", None, False,
                           {k: p.exclude[1][k] for k in sorted(p.exclude[1])})])
        self.assertEqual((runs[0]["chain"], runs[1]["chain"]["outcome"], runs[1]["chain"]["refs"]),
                         (None, "verified", 300))
        self.assertEqual((ex["excluded"], ex["eligible_after_exclusion"], ex["overlap"], ex["stand_in"]),
                         (300, 240, 0, False))
        self.assertEqual(ex["excluded_sha256"], sha256_hex((p.dir / "excluded.jsonl").read_bytes()))
        self.assertEqual((p.manifest["j3"]["excluded_sha256"], p.manifest["j3"]["excluded_lines"]),
                         (ex["excluded_sha256"], 340))
        self.assertEqual(p.manifest["j3"]["excluded_runs"], runs)
        for rel in ("labels.json", "prereg.json", "lexical.json", "prior.json", "ablation_a.json", "ablation_b.json",
                    "questions.json"):
            assert_no_ref(self, (p.dir / rel).read_text(encoding="utf-8"), p.drawn | p.excluded)
        assert_no_ref(self, canonical_dumps(p.manifest), p.drawn | p.excluded)
        lines = p.stdout.splitlines()
        self.assertIn(f"j3 exclusion: source R1 sha256 mismatch: rebuilt seed-one labels sha256 "
                      f"{ex['rebuilt_sha256']['j001'][:12]} against the pinned prefix bc6d092d8bca", lines)
        self.assertIn(f"j3 exclusion: source R2 sha256 mismatch: rebuilt seed-two labels sha256 "
                      f"{ex['rebuilt_sha256']['a001'][:12]} against the pinned prefix 623780b8a1de", lines)
        self.assertIn("j3 exclusion: j001=artifact a001=artifact consistency j1_r1=none a1_r2=none outside_chain=0 "
                      "chain_union=0 excluded 300", lines)
        self.assertIn(f"j3 draw: available 540 excluded 300 eligible_after_exclusion 240 overlap 0 sources J1=verified "
                      f"R1=sha256 mismatch A1=verified R2=sha256 mismatch K=verified j001=artifact a001=artifact "
                      f"consistency j1_r1=none a1_r2=none outside_chain=0 chain_union=0 labels_sha256 "
                      f"{labels['sha256'][:12]} questions_sha256 {p.manifest['j3']['questions']['sha256'][:12]} "
                      f"excluded_sha256 {ex['excluded_sha256'][:12]} excluded_lines 340 complaint_file_sha256 "
                      f"{sha256_hex(ARCHIVE)[:12]}", lines)
        lexical = p.manifest["j3"]["lexical"]["balanced_accuracy"]
        comparators = next(line for line in lines if line.startswith("j3 comparators:"))
        self.assertTrue(comparators.startswith(f"j3 comparators: lexical balanced_accuracy {lexical['value']:.3f} "
                                               f"[{lexical['ci_low']:.3f}, {lexical['ci_high']:.3f}] record_blind "))
        self.assertIn(f"predicate_only_bound {p.manifest['j3']['prior_bound']['value']:.3f} ablation_a ", comparators)
        self.assertTrue(comparators.endswith(f"lexical_unknown {p.manifest['j3']['lexical_unknown']}"))
        self.assertFalse(any(line.startswith("j3 exclusion: source J1") or line.startswith("j3 exclusion: source A1")
                             or line.startswith("j3 exclusion: source K") for line in lines))
        assert_no_ref(self, p.stdout, p.drawn | p.excluded)
        self.assertNotIn("Operator", p.stdout)

    def test_the_plan_summary_shows_the_preregistration(self) -> None:
        p = _Prereg.get()
        md, sources = lab_summary.render_plan(p.tmp / "plan")
        self.assertIn("`J3` labels: source `nhtsa`, records 40", md)
        self.assertIn("eligible records 540; excluded records 300; eligible after exclusion 240; overlap with the "
                      "excluded set 0", md)
        self.assertIn("- sources: `J1` `verified` (150 refs); `R1` `sha256 mismatch` (n/a refs); `A1` `verified` "
                      "(150 refs); `R2` `sha256 mismatch` (n/a refs); `K` `verified` (300 refs)", md)
        self.assertIn("- cycles established by: `j001` `artifact` (150 refs); `a001` `artifact` (150 refs)", md)
        self.assertIn("- consistency counts: `j1_r1` n/a; `a1_r2` n/a; `outside_chain` 0; `chain_union` 0", md)
        self.assertIn(f"excluded refs file sha `{p.manifest['j3']['excluded_sha256'][:12]}`; chain file lines 340; "
                      f"complaint file sha `{sha256_hex(ARCHIVE)[:12]}`", md)
        self.assertIn("`J3` primary pair: `fake-b` `evidence`; headline secondary `evidence_grounded`; target balanced "
                      "accuracy 0.860", md)
        for column in ("lexical judge, every record", "record-blind control, every record", "ablation A, every record",
                       "ablation B, every record"):
            self.assertIn(f"`J3` {column}: balanced accuracy", md)
        self.assertIn(f"`J3` predicate-only bound, every record: {p.manifest['j3']['prior_bound']['value']:.3f}; "
                      f"lexical judge unknown verdicts {p.manifest['j3']['lexical_unknown']}", md)
        self.assertIn(J3_EXCLUSION_NOTE, md)
        self.assertNotIn(J3_STAND_IN, md)
        assert_no_ref(self, md, p.drawn | p.excluded)
        check_sources(self, md, sources, p.tmp / "plan")

    def plan_dir(self, name: str, **block_changes: Any) -> Path:
        """A fresh plan of the fixture's request (with ``block_changes`` to its j3 block) under a new directory."""
        p = _Prereg.get()
        tmp = Path(tempfile.mkdtemp(prefix=f"lab-j3-{name}-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        request = json.loads(json.dumps(p.request))
        request["experiments"]["j3"].update(block_changes)
        path = write_json(tmp / "j3-test.json", request)
        code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "plan")])
        self.assertEqual(code, 0, err)
        (tmp / "complaints.zip").write_bytes(ARCHIVE)
        return tmp

    def prereg(self, tmp: Path, *extra: str) -> tuple[int, str, str]:
        return call_main(lab_prereg, ["--plan", str(tmp / "plan" / "plan.json"),
                                      "--nhtsa-archive", str(tmp / "complaints.zip"), *extra])

    def test_no_source_verifying_stops_the_plan_job(self) -> None:
        """B5: the artifacts absent and the rebuilt draws not the pinned ones: no draw, a plan error, the ask."""
        tmp = self.plan_dir("unverified")
        (tmp / "nothing").mkdir()
        code, out, err = self.prereg(tmp, "--earlier-plans", str(tmp / "nothing"))
        self.assertEqual(code, 2)
        lines = out.splitlines()
        for line in ("j3 exclusion: source J1 artifact absent", "j3 exclusion: source A1 artifact absent",
                     "j3 exclusion: source K artifact absent", "j3 exclusion: source R2 not attempted",
                     "j3 exclusion: j001=not established a001=not established consistency j1_r1=none a1_r2=none "
                     "outside_chain=none chain_union=none excluded 0"):
            self.assertIn(line, lines)
        self.assertTrue(any(line.startswith("j3 exclusion: source R1 sha256 mismatch: rebuilt seed-one labels sha256 ")
                            for line in lines))
        self.assertFalse(any(line.startswith("j3 draw:") for line in lines))
        self.assertTrue(err.startswith(f"error: $.experiments.j3.exclude: {J3_FRESHNESS_UNVERIFIED}"), err)
        error = read_json(tmp / "plan" / "plan-error.json")
        self.assertEqual((error["path"], error["problem"]), ("$.experiments.j3.exclude", J3_FRESHNESS_UNVERIFIED))
        self.assertFalse((tmp / "plan" / "prereg" / "j3").exists())
        self.assertEqual(lab_prereg.earlier_dir(tmp / "plan"), tmp / "lab-earlier")

    def test_verified_sources_that_disagree_stop_the_plan_job(self) -> None:
        """B5: a J001 artifact of other records whose bytes match the request and a seed-one rebuild that matches
        its own pin: both verify, their sets differ, the count is printed and the plan job stops."""
        tmp = self.plan_dir("disagree")
        other = j2_data.archive(j3_data.PER_MAKE, 2)
        entries = j3_data.stand_in(tmp / "earlier", other)
        seed1 = goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1, fetch=lambda: ARCHIVE)[0]
        write_json(tmp / "stand-in.json", [{**entries[0], "rebuild_sha256_prefix": sha256_hex(seed1)[:12]},
                                           {**entries[1], "rebuild_sha256_prefix": "0" * 12}])
        code, out, err = self.prereg(tmp, "--earlier-plans", str(tmp / "earlier"),
                                     "--exclude-stand-in", str(tmp / "stand-in.json"))
        self.assertEqual(code, 2)
        lines = out.splitlines()
        self.assertTrue(any(line.startswith("j3 exclusion: j001=artifact a001=artifact consistency j1_r1=300 ")
                            for line in lines), out)
        self.assertTrue(err.startswith(f"error: $.experiments.j3.exclude: {J3_EXCLUSION_DISAGREES}"), err)
        self.assertEqual(read_json(tmp / "plan" / "plan-error.json")["problem"], J3_EXCLUSION_DISAGREES)
        assert_no_ref(self, out, refs_of_labels(seed1) | refs_of_labels(
            (tmp / "earlier" / str(j3_data.J001_RUN_ID) / "prereg" / "j1" / "labels.jsonl").read_bytes()))

    def test_too_few_records_left_stop_the_plan_job(self) -> None:
        tmp = self.plan_dir("toofew", labels={**LABELS, "n": 300})
        code, out, err = self.prereg(tmp, "--earlier-plans", str(_Prereg.get().earlier))
        self.assertEqual(code, 2)
        self.assertIn("j3 draw: only 240 eligible unexcluded public records remain: no further fresh draw of 300 "
                      "records is pre-registered", out.splitlines())
        self.assertTrue(err.startswith(f"error: $.experiments.j3.labels.n: {J3_TOO_FEW_RECORDS}"), err)
        self.assertEqual(read_json(tmp / "plan" / "plan-error.json")["problem"], J3_TOO_FEW_RECORDS)

    def test_the_stand_in_is_recorded(self) -> None:
        """Tests only: ``--exclude-stand-in`` replaces the request's two entries for the checks and marks the
        preregistration; every source verifies on the test data and the counts are 0."""
        tmp = self.plan_dir("standin")
        data = j3_data.write_data(tmp / "data")
        code, out, err = self.prereg(tmp, "--earlier-plans", str(data / "earlier"),
                                     "--exclude-stand-in", str(data / "stand-in.json"))
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertIn("j3 exclusion: j001=artifact a001=artifact consistency j1_r1=0 a1_r2=0 outside_chain=0 "
                      "chain_union=0 excluded 300", lines)
        self.assertTrue(any(line.startswith("j3 draw: available 540 excluded 300 eligible_after_exclusion 240 overlap "
                                            "0 sources J1=verified R1=verified A1=verified R2=verified K=verified ")
                            for line in lines))
        self.assertFalse(any(line.startswith("j3 exclusion: source") for line in lines))
        self.assertEqual(lines[-1], "prereg: e1 no x1 no e2 no j1 no j3 yes")
        j3 = read_json(tmp / "plan" / "prereg" / "prereg.json")["j3"]
        self.assertTrue(j3["stand_in"])
        self.assertEqual((j3["exclusion"]["stand_in"], j3["exclusion"]["mechanisms"], j3["exclusion"]["consistency"]),
                         (True, {"artifact": "verified", "rebuild": "verified"},
                          {"j1_r1": 0, "a1_r2": 0, "outside_chain": 0, "chain_union": 0}))
        self.assertEqual({s: j3["exclusion"]["sources"][s]["outcome"] for s in j3labels.SOURCES},
                         dict.fromkeys(j3labels.SOURCES, "verified"))
        runs = j3["excluded_runs"]
        self.assertEqual([(r["stand_in"], r["requested"], r["refs"], r["outcome"], r["rebuild"]["outcome"]) for r in runs],
                         [(True, {k: _Prereg.get().exclude[0][k] for k in sorted(_Prereg.get().exclude[0])}, 150,
                           "verified", "verified"),
                          (True, {k: _Prereg.get().exclude[1][k] for k in sorted(_Prereg.get().exclude[1])}, 150,
                           "verified", "verified")])
        self.assertEqual(runs[0]["sha256_prefix"], j3["exclusion"]["rebuilt_sha256"]["j001"][:12])
        self.assertEqual(runs[1]["sha256_prefix"], j3["exclusion"]["rebuilt_sha256"]["a001"][:12])
        # the same draw as without the stand-in: the excluded set is the same 300 refs
        self.assertEqual((tmp / "plan" / "prereg" / "j3" / "labels.jsonl").read_bytes(),
                         (_Prereg.get().dir / "labels.jsonl").read_bytes())
        md, sources = lab_summary.render_plan(tmp / "plan")
        self.assertIn(J3_STAND_IN, md)
        self.assertIn("`R1` `verified` (150 refs)", md)
        check_sources(self, md, sources, tmp / "plan")

    def test_a_stand_in_that_cannot_be_read_is_refused(self) -> None:
        tmp = self.plan_dir("badstandin")
        (tmp / "stand-in.json").write_text("{not json", encoding="utf-8")
        code, _, err = self.prereg(tmp, "--exclude-stand-in", str(tmp / "stand-in.json"))
        self.assertEqual((code, err.strip()), (2, "error: the exclusion stand-in cannot be read"))
        for bad in ([J001_ENTRY], [J001_ENTRY, {**A001_ENTRY, "chain_file": "x"}], [1, 2], [{}, {}],
                    [{**J001_ENTRY, "rebuild_seed": 2}, A001_ENTRY]):
            write_json(tmp / "stand-in.json", bad)
            with self.subTest(bad=bad):
                self.assertEqual(self.prereg(tmp, "--exclude-stand-in", str(tmp / "stand-in.json"))[0], 2)
                with self.assertRaises(RequestError):
                    lab_prereg.load_stand_in(tmp / "stand-in.json")
        write_json(tmp / "stand-in.json", [J001_ENTRY, A001_ENTRY])
        self.assertEqual(lab_prereg.load_stand_in(tmp / "stand-in.json"), [J001_ENTRY, A001_ENTRY])
        self.assertFalse((tmp / "plan" / "prereg").exists())
        # a J2-shaped stand-in (an object) is not a J3 one: the request's entries are used instead, and the checks
        # run against them
        write_json(tmp / "stand-in.json", {**{k: J001_ENTRY[k] for k in ("run_id", "artifact", "file", "sha256_prefix")},
                                           "rebuild_sha256_prefix": "a" * 12})
        (tmp / "nothing").mkdir()
        code, out, _ = self.prereg(tmp, "--earlier-plans", str(tmp / "nothing"),
                                   "--exclude-stand-in", str(tmp / "stand-in.json"))
        self.assertEqual(code, 2)
        self.assertIn("j3 exclusion: source J1 artifact absent", out.splitlines())


def seed_one_labels() -> bytes:
    return goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1, fetch=lambda: ARCHIVE)[0]


def seed_two_labels() -> bytes:
    return j2labels.build_labels(NHTSA_PACK, 150, 2, fetch=lambda: ARCHIVE, excluded=refs_of_labels(seed_one_labels()),
                                 mechanisms={"artifact": "verified", "rebuild": "verified"})[0]


# --------------------------------------------------------------------------------------------------- the runner

class _Scripted:
    """The lab's responder, except for the payloads of chosen questions: ``invalid`` ones get a reply the schema
    refuses (so the repair fails too) and ``drop`` ones a dropped connection; ``by_task`` maps a task name to a
    function of the payload giving the reply every question of that task gets instead."""

    def __init__(self, invalid: list[dict[str, Any]] = (), drop: list[dict[str, Any]] = (),
                 by_task: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None) -> None:
        self.base = Responder(PACK)
        self.invalid = {canonical_bytes(p) for p in invalid}
        self.drop = {canonical_bytes(p) for p in drop}
        self.by_task = dict(by_task or {})
        self.tasks: list[str] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        name = request["response_format"]["json_schema"]["name"]
        self.tasks.append(name)
        payload = request_payload(request)
        key = canonical_bytes(payload)
        if key in self.drop:
            raise RuntimeError("dropped")
        if key in self.invalid:
            return {"mentions_entity": "perhaps", "describes_predicate": "yes"}
        if name in self.by_task:
            return self.by_task[name](payload)
        return self.base(request)


def quoting(answers: dict[str, str], *, first: bool = True, chars: int = 40,
            fixed: str | None = None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A task E reply: the payload text's first ``chars`` characters (grounded) or ``fixed`` (not grounded) as the
    citation, first or last in the object, with the given answers."""
    def reply(payload: dict[str, Any]) -> dict[str, Any]:
        citation = payload["record"]["text"][:chars] if fixed is None else fixed
        return {"citation": citation, **answers} if first else {**answers, "citation": citation}
    return reply


class RunTests(unittest.TestCase):
    """``lab.j3 run`` in this process against the collective's fake server answered by the lab's responder."""

    def setUp(self) -> None:
        self.p = _Prereg.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j3-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qs = [q for q in lab_j1.read_questions((self.p.dir / "questions.jsonl").read_bytes()) if q["part"] == 1]
        self.recs = {r["record_ref"]: r for r, _ in lab_j1.parse_labels((self.p.dir / "labels.jsonl").read_bytes())}
        self.lexical = {line["question_id"]: line for line in read_lines(self.p.dir / "lexical.jsonl")}

    def server(self, responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> FakeOpenAIServer:
        server = FakeOpenAIServer("valid", responder=responder or Responder(PACK)).start()
        self.addCleanup(server.stop)
        return server

    def routing(self, base_url: str, model: str = "fake-a", change: Callable[[dict[str, Any]], None] | None = None,
                name: str | None = None) -> Path:
        doc = lab_j3.routing_doc(self.p.plan["models"], [model], base_url)
        if change is not None:
            change(doc)
        return write_json(self.tmp / f"routing-{name or model}.json", doc)

    def argv(self, routing: Path, *, endpoint: str = "fake-a", part: int = 1, run_id: str = "r1",
             budget: int = 600, **files: Path) -> list[str]:
        d = self.p.dir
        return ["run", f"--prereg={files.get('prereg', d / 'prereg.json')}",
                f"--labels={files.get('labels', d / 'labels.jsonl')}",
                f"--questions={files.get('questions', d / 'questions.jsonl')}", f"--routing={routing}",
                f"--endpoint={endpoint}", f"--part={part}", f"--run-id={run_id}", f"--runs-dir={self.tmp / 'runs'}",
                f"--budget-seconds={budget}"]

    def run_j3(self, argv: list[str]) -> tuple[int, str, str, Path]:
        code, out, err = call_main(lab_j3, argv)
        return code, out, err, self.tmp / "runs" / "j3" / argv[7].split("=", 1)[1]

    def read(self, run: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return read_json(run / "run.json"), read_lines(run / "verdicts.jsonl")

    def stopped_run(self, ticks: list[float], budget: int = 1) -> tuple[int, dict[str, Any], list[dict[str, Any]],
                                                                       FakeOpenAIServer]:
        """A run whose budget check is driven by the test's clock (no real timer is armed)."""
        server = self.server()
        clock = iter(ticks)
        with mock.patch.object(lab_j3, "_clock", lambda: next(clock)), \
                mock.patch.object(lab_j1.signal, "setitimer"):
            code, _, err, run = self.run_j3(self.argv(self.routing(server.base_url), budget=budget))
        doc, lines = self.read(run)
        return code, doc, lines, server

    def test_three_passes_task_e_first(self) -> None:
        """Rule 14 and rule 3: every question with task E, then every one with R, then with N; the same payload;
        each task's schema and max_tokens; every verdict by the pinned rule; the fake answers as the lexical judge
        behind an empty field, so the grounded readings refute what it confirmed."""
        server = self.server()
        code, out, err, run = self.run_j3(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        n = len(self.qs)
        self.assertEqual(n, 40)
        self.assertEqual(len(lines), 3 * n)
        self.assertEqual([line["task"] for line in lines], [E] * n + [R] * n + [N] * n)
        self.assertEqual([line["question_id"] for line in lines], [q["question_id"] for q in self.qs] * 3)
        for line in lines:
            with self.subTest(question=line["question_id"], task=line["task"]):
                self.assertEqual({k: line[k] for k in lab_j1.LINE_KEYS}, self.lexical[line["question_id"]])
                self.assertEqual(line["mapped_verdict"], line["verdict"])      # the lexical judge never says unclear
                self.assertEqual(sorted(line), sorted(lab_j3.LINE_KEYS))
                if line["task"] == E:
                    self.assertEqual((line["citation"], line["analysis"], line["grounded"], line["first_key"]),
                                     ("", None, False, "citation"))
                    self.assertEqual(line["grounded_verdict"], "refute" if line["verdict"] == "confirm" else line["verdict"])
                    self.assertEqual(line["grounded_mapped_verdict"], line["grounded_verdict"])
                elif line["task"] == R:
                    self.assertEqual((line["citation"], line["analysis"], line["grounded"], line["first_key"],
                                      line["grounded_verdict"]), (None, "", None, "analysis", None))
                else:
                    self.assertEqual((line["citation"], line["analysis"], line["grounded"], line["grounded_verdict"]),
                                     (None, None, None, None))
                    self.assertIn(line["first_key"], lab_j3.schema_keys(N))
        self.assertEqual((doc["kind"], doc["experiment"], doc["complete"], doc["stopped"], doc["stopped_task"],
                          doc["measurement"], doc["listed_fake"]),
                         ("lab_j3_run", "J3", True, None, None, False, True))
        self.assertEqual(doc["complete_by_task"], {E: True, R: True, N: True})
        self.assertEqual((doc["pass_order"], doc["questions_planned"], doc["questions_done"], doc["part"],
                          doc["parts"]), ([E, R, N], 120, 120, 1, 2))
        self.assertEqual((doc["questions_planned_by_task"], doc["questions_done_by_task"]),
                         ({E: 40, R: 40, N: 40}, {E: 40, R: 40, N: 40}))
        self.assertEqual(doc["verdicts_sha256"], hashlib.sha256((run / "verdicts.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["ledger_sha256"], hashlib.sha256((run / "ledger.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["prereg_sha256"], hashlib.sha256((self.p.dir / "prereg.json").read_bytes()).hexdigest())
        self.assertEqual((doc["pinned"]["tasks"], doc["pinned"]["mapping"], doc["pinned"]["grounding"],
                          doc["pinned"]["composite"]),
                         (lab_j3.task_pins(), lab_j3.mapping_pin(), lab_j3.grounding_pin(), lab_j3.composite_pin()))
        for key in ("failures_by_task", "latency_ms_by_task", "tokens_by_task"):
            self.assertEqual(sorted(doc[key]), sorted(lab_j3.TASKS))
        self.assertEqual(doc["failures"], {"model": {"questions": 0, "by_kind": {}},
                                           "transport": {"questions": 0, "by_kind": {}}})
        self.assertEqual({task: (t["attempts"], t["cut_at_cap"]) for task, t in doc["tokens_by_task"].items()},
                         {E: (40, 0), R: (40, 0), N: (40, 0)})
        rows = read_ledger(run / "ledger.jsonl")
        self.assertEqual(len(rows), 120)
        self.assertEqual([r["task"] for r in rows], [E] * n + [R] * n + [N] * n)
        self.assertEqual({(r["boundary"], r["endpoint_boundary"], r["boundary_mode"], r["data_label"]) for r in rows},
                         {("site:lab", "site:lab", "own", "public")})
        self.assertEqual((rows[0]["ref"], rows[n]["ref"], rows[2 * n]["ref"]), ("j3-1-0-0000", "j3-1-1-0000", "j3-1-2-0000"))
        self.assertEqual(doc["tokens_by_task"][E], lab_j3.token_figures(rows, E))
        chat = server.chat_requests
        self.assertEqual(len(chat), 120)
        self.assertEqual([c["json"]["response_format"]["json_schema"]["name"] for c in chat], [E] * n + [R] * n + [N] * n)
        self.assertEqual([(c["json"]["max_tokens"], c["json"]["temperature"]) for c in chat[::n]],
                         [(512, 0), (512, 0), (256, 0)])
        first, second, third = chat[0]["json"], chat[n]["json"], chat[2 * n]["json"]
        self.assertIn(lab_j3.EVIDENCE_INSTRUCTIONS, first["messages"][0]["content"])
        self.assertIn(lab_j3.RATIONALE_INSTRUCTIONS, second["messages"][0]["content"])
        self.assertIn(lab_j2.NEGATION_INSTRUCTIONS, third["messages"][0]["content"])
        self.assertNotIn(lab_j3.CITATION_SENTENCES, third["messages"][0]["content"])
        payload = lab_j1.payload(PACK, self.qs[0], self.recs[self.qs[0]["record_ref"]])
        self.assertEqual((request_payload(first), request_payload(second), request_payload(third)),
                         (payload, payload, payload))
        self.assertEqual(payload["record"]["codes"], [])
        self.assertEqual(first["response_format"]["json_schema"]["schema"], lab_j3.evidence_schema())
        self.assertEqual(second["response_format"]["json_schema"]["schema"], lab_j3.rationale_schema())
        self.assertEqual(third["response_format"]["json_schema"]["schema"], judge_schema())
        self.assertNotIn("narrative", (run / "verdicts.jsonl").read_text(encoding="utf-8"))
        self.assertIn("complete=true", out)
        self.assertIn("evidence=40/40 rationale=40/40 negation=40/40", out)
        self.assertEqual(units.harness_status("j3", code, False, doc), ("ok", None))
        record, problem = units.participation("j3", rows, [E], doc)
        self.assertEqual(({t: record["tasks"][t]["attempted"] for t in lab_j3.TASKS}, problem),
                         ({E: 40, R: 40, N: 40}, None))

    def test_the_citation_the_grounding_and_the_first_key_as_written(self) -> None:
        """Rule 4 and B19 on the runner: a verbatim citation is grounded and keeps a yes; one from no record turns a
        yes into a no under G; the first key is the reply's own, wherever the field came."""
        yes = {"mentions_entity": "yes", "describes_predicate": "yes"}
        runs = {"quoted": quoting(yes), "invented": quoting(yes, fixed="a passage of no record at all"),
                "late": quoting(yes, first=False), "short": quoting(yes, chars=3)}
        for name, reply in runs.items():
            scripted = _Scripted(by_task={E: reply})
            server = self.server(scripted)
            code, _, err, run = self.run_j3(self.argv(self.routing(server.base_url, name=name), run_id=name))
            self.assertEqual(code, 0, err)
            _, lines = self.read(run)
            e = [line for line in lines if line["task"] == E]
            with self.subTest(run=name):
                self.assertEqual(len(e), 40)
                grounded = name in ("quoted", "late")
                self.assertEqual({line["grounded"] for line in e}, {grounded})
                self.assertEqual({line["verdict"] for line in e}, {"confirm"})
                self.assertEqual({line["grounded_verdict"] for line in e}, {"confirm" if grounded else "refute"})
                self.assertEqual({line["grounded_mapped_verdict"] for line in e}, {"confirm" if grounded else "refute"})
                self.assertEqual({line["first_key"] for line in e}, {"mentions_entity" if name == "late" else "citation"})
                for line in e:
                    text = self.p.texts[line["record_ref"]]
                    self.assertIs(lab_j3.grounded(line["citation"], text), grounded)
                    self.assertIsNone(lab_j3.line_problem(next(q for q in self.qs if q["question_id"] == line["question_id"]),
                                                          line, text))
                    self.assertEqual(sorted(line), sorted(lab_j3.LINE_KEYS))
            self.assertEqual(scripted.tasks, [E] * 40 + [R] * 40 + [N] * 40)
        # the views score as the readings say: Eg of the invented run refutes every question, E confirms all
        _, lines = self.read(self.tmp / "runs" / "j3" / "invented")
        score_e = lab_j1.score(self.qs, lab_j3.arm_lines(lines, "evidence"), bootstrap_b=1000, bootstrap_seed=3)
        score_eg = lab_j1.score(self.qs, lab_j3.arm_lines(lines, "evidence_grounded"), bootstrap_b=1000, bootstrap_seed=3)
        self.assertEqual((score_e["sensitivity"]["value"], score_e["specificity"]["value"]), (1.0, 0.0))
        self.assertEqual((score_eg["sensitivity"]["value"], score_eg["specificity"]["value"]), (0.0, 1.0))
        c = lab_j3.arm_lines(lines, "composite", self.lexical)
        self.assertEqual([line["verdict"] for line in c], [self.lexical[q["question_id"]]["verdict"] for q in self.qs])

    def test_a_model_failure_is_unknown_and_a_transport_failure_is_not_scored_in_each_pass(self) -> None:
        bad, gone = self.qs[3], self.qs[6]
        scripted = _Scripted(invalid=[lab_j1.payload(PACK, bad, self.recs[bad["record_ref"]])],
                             drop=[lab_j1.payload(PACK, gone, self.recs[gone["record_ref"]])])
        server = self.server(scripted)
        code, _, err, run = self.run_j3(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        for task in lab_j3.TASKS:
            by_id = {line["question_id"]: line for line in lines if line["task"] == task}
            with self.subTest(task=task):
                b, g = by_id[bad["question_id"]], by_id[gone["question_id"]]
                self.assertEqual((b["verdict"], b["mapped_verdict"], b["grounded_verdict"], b["error_kind"],
                                  b["scored"], b["first_key"], b["citation"]),
                                 ("unknown", "unknown", "unknown" if task == E else None, "schema_invalid", True,
                                  None, None))
                self.assertEqual((g["error_kind"], g["scored"], g["verdict"], g["grounded"], g["first_key"]),
                                 ("network", False, "unknown", None, None))
                self.assertEqual(doc["failures_by_task"][task],
                                 {"model": {"questions": 1, "by_kind": {"schema_invalid": 1}},
                                  "transport": {"questions": 1, "by_kind": {"network": 1}}})
        self.assertEqual(doc["failures"], {"model": {"questions": 3, "by_kind": {"schema_invalid": 3}},
                                           "transport": {"questions": 3, "by_kind": {"network": 3}}})
        self.assertTrue(doc["complete"])
        rows = read_ledger(run / "ledger.jsonl")
        ref_bad = f"j3-1-0-{self.qs.index(bad):04d}"
        self.assertEqual([(r["attempt"], r["error_kind"]) for r in rows if r["ref"] == ref_bad],
                         [(1, "schema_invalid"), (2, "schema_invalid")])                 # rule 6: one repair
        record, problem = units.participation("j3", rows, [E], doc)
        self.assertEqual(({t: record["tasks"][t]["ok"] for t in lab_j3.TASKS}, problem), ({E: 39, R: 39, N: 39}, None))

    def test_a_stop_in_the_second_pass_leaves_the_first_complete(self) -> None:
        """Rule 14: the budget ends during the rationale pass: task E's set is complete for this part, R's and N's
        are not, exit 1, and the unit reads ok with J3_EVIDENCE_STOPPED; the required task is E alone."""
        code, doc, lines, server = self.stopped_run([0.0] * 41 + [2.0] * 3)
        self.assertEqual(code, 1)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "budget", R))
        self.assertEqual(doc["complete_by_task"], {E: True, R: False, N: False})
        self.assertEqual((doc["questions_done"], doc["questions_done_by_task"]), (40, {E: 40, R: 0, N: 0}))
        self.assertEqual([line["task"] for line in lines], [E] * 40)
        self.assertEqual(len(server.chat_requests), 40)
        self.assertEqual(units.harness_status("j3", 1, False, doc), ("ok", J3_EVIDENCE_STOPPED))
        rows = read_ledger(self.tmp / "runs" / "j3" / "r1" / "ledger.jsonl")
        unit = {"experiment": "j3", "params": {}}
        self.assertEqual(units.required_tasks(unit), [E])
        record, problem = units.participation("j3", rows, units.required_tasks(unit), doc)
        self.assertEqual((record["required"], record["tasks"][E]["attempted"], problem), ([E], 40, None))
        self.assertNotIn(R, record["tasks"])
        self.assertNotIn(N, record["tasks"])

    def test_a_stop_in_the_third_pass_keeps_two_sets(self) -> None:
        code, doc, lines, server = self.stopped_run([0.0] * 81 + [2.0] * 3)
        self.assertEqual(code, 1)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "budget", N))
        self.assertEqual(doc["complete_by_task"], {E: True, R: True, N: False})
        self.assertEqual((doc["questions_done_by_task"], len(lines)), ({E: 40, R: 40, N: 0}, 80))
        self.assertEqual(units.harness_status("j3", 1, False, doc), ("ok", J3_EVIDENCE_STOPPED))

    def test_a_stop_in_the_first_pass_fails_the_unit(self) -> None:
        code, doc, lines, server = self.stopped_run([0.0, 0.0, 0.0, 2.0, 2.0])
        self.assertEqual(code, 1)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "budget", E))
        self.assertEqual(doc["complete_by_task"], {E: False, R: False, N: False})
        self.assertEqual((doc["questions_done"], doc["questions_done_by_task"], len(lines)),
                         (2, {E: 2, R: 0, N: 0}, 2))
        self.assertEqual(units.harness_status("j3", 1, False, doc), ("failed", J1_STOPPED))

    def test_it_stops_when_the_server_stays_down(self) -> None:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code, _, err, run = self.run_j3(self.argv(self.routing(f"http://127.0.0.1:{port}/v1")))
        self.assertEqual(code, 1, err)
        doc, lines = self.read(run)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "server_down", E))
        self.assertEqual(doc["complete_by_task"], {E: False, R: False, N: False})
        self.assertEqual({(line["task"], line["error_kind"], line["scored"]) for line in lines},
                         {(E, "network", False)})
        self.assertEqual(units.harness_status("j3", 1, False, doc), ("failed", J1_STOPPED))

    def test_every_pin_is_checked_before_any_call(self) -> None:
        """Rules 3 to 6: the three tasks' pins, the mapping, the grounding, the composite, the arms, the lifts, the
        routing of the three tasks without escalation, the labels, the questions, the pack and the code hash, each
        refused with exit 2 and its own message before a call is made."""
        server = self.server()
        good = self.routing(server.base_url)
        d = self.p.dir
        written: list[Path] = []

        def changed(name: str, edit: Callable[[bytes], bytes]) -> Path:
            target = self.tmp / name
            target.write_bytes(edit((d / name).read_bytes()))
            written.append(target)
            return target

        def prereg_with(name: str, change: Callable[[dict[str, Any]], None]) -> Path:
            doc = read_json(d / "prereg.json")
            change(doc)
            written.append(write_json(self.tmp / f"prereg-{name}.json", doc))
            return written[-1]

        def routing_with(name: str, change: Callable[[dict[str, Any]], None]) -> Path:
            written.append(self.routing(server.base_url, change=change, name=name))
            return written[-1]

        def escalating(task: str) -> Callable[[dict[str, Any]], None]:
            def change(doc: dict[str, Any]) -> None:
                doc["endpoints"]["fake-b"] = lab_j1.endpoint(self.p.plan["models"]["fake-b"], server.base_url)
                doc["routes"][task]["escalate_to"] = "fake-b"
            return change

        differs = "{} differs from the preregistration"
        escalation = "the routing must route the three judge tasks to the endpoint without escalation"
        cases = [
            ("labels", {"labels": changed("labels.jsonl", lambda b: b.replace(b"stalled", b"stopped", 1))},
             differs.format("the labels' sha256")),
            ("questions", {"questions": changed("questions.jsonl", lambda b: b + b"\n")},
             differs.format("the questions' sha256")),
            ("pack", {"prereg": prereg_with("pack", lambda p: p["pack"].update(vocabulary_hash="0" * 64))},
             differs.format("the pack's vocabulary hash")),
            ("code", {"prereg": prereg_with("code", lambda p: p.update(code_hash="0" * 64))},
             differs.format("the code hash")),
            ("evidence max_tokens", {"prereg": prereg_with("e-tokens", lambda p: p["tasks"]["evidence"].update(
                max_tokens=256))}, differs.format("the judge tasks")),
            ("evidence instructions", {"prereg": prereg_with("e-text", lambda p: p["tasks"]["evidence"].update(
                instructions_sha256="0" * 64))}, differs.format("the judge tasks")),
            ("rationale schema", {"prereg": prereg_with("r-schema", lambda p: p["tasks"]["rationale"].update(
                schema_sha256="0" * 64))}, differs.format("the judge tasks")),
            ("negation max_tokens", {"prereg": prereg_with("n-tokens", lambda p: p["tasks"]["negation"].update(
                max_tokens=512))}, differs.format("the judge tasks")),
            ("task order", {"prereg": prereg_with("order", lambda p: p["tasks"].update(order=[N, R, E]))},
             differs.format("the judge tasks")),
            ("mapping rule", {"prereg": prereg_with("m-rule", lambda p: p["mapping"]["rule"]["describes_predicate"]
                                                    .update(unclear="yes"))}, differs.format("the mapping")),
            ("grounding floor", {"prereg": prereg_with("g-floor", lambda p: p["grounding"]["predicate"].update(
                min_chars=3))}, differs.format("the grounding")),
            ("grounding sha", {"prereg": prereg_with("g-sha", lambda p: p["grounding"].update(sha256="0" * 64))},
             differs.format("the grounding")),
            ("composite rule", {"prereg": prereg_with("c-rule", lambda p: p["composite"].update(rule="other"))},
             differs.format("the composite")),
            ("composite ablation", {"prereg": prereg_with("c-abl", lambda p: p["composite"]["ablations"]
                                                          ["lexical_or_no"]["reply"].update(describes_predicate="yes"))},
             differs.format("the composite")),
            ("arms", {"prereg": prereg_with("arms", lambda p: p["arms"]["evidence_grounded"].update(reading="shipped"))},
             differs.format("the arms")),
            ("lifts", {"prereg": prereg_with("lifts", lambda p: p["lifts"]["E_minus_N"].update(minus="rationale"))},
             differs.format("the lifts")),
            ("kind", {"prereg": prereg_with("kind", lambda p: p.update(kind="lab_j2_prereg"))},
             "the preregistration is not a J3 prereg.json"),
            ("transport", {"routing": routing_with("transport", lambda r: r["endpoints"]["fake-a"].update(
                transport_schema="reduced"))}, differs.format("the endpoint's pins")),
            ("boundary", {"routing": routing_with("boundary", lambda r: r["endpoints"]["fake-a"].update(
                boundary="central"))}, differs.format("the endpoint's pins")),
            ("evidence route missing", {"routing": routing_with("no-e", lambda r: r["routes"].pop(E))},
             f"routing: $.routes.{E}: no route"),
            ("rationale route missing", {"routing": routing_with("no-r", lambda r: r["routes"].pop(R))},
             f"routing: $.routes.{R}: no route"),
            ("negation route missing", {"routing": routing_with("no-n", lambda r: r["routes"].pop(N))},
             f"routing: $.routes.{N}: no route"),
            ("evidence escalation", {"routing": routing_with("esc-e", escalating(E))}, escalation),
            ("rationale escalation", {"routing": routing_with("esc-r", escalating(R))}, escalation),
            ("negation escalation", {"routing": routing_with("esc-n", escalating(N))}, escalation),
            ("part", {"part": 3}, "--part is not one of the preregistered parts"),
            ("endpoint", {"endpoint": "fake-slow"}, "the endpoint is not in the preregistration or the routing"),
            ("budget", {"budget": 0}, "--budget-seconds must be >= 1"),
        ]
        self.assertEqual(len(written), len(set(written)), "two cases share a file")
        for label, change, _ in cases:
            if "escalation" in label:
                load_routing(change["routing"], tasks=lab_j3.TASKS, check_env=False)     # the routing itself loads
        for i, (label, change, message) in enumerate(cases):
            routing = change.pop("routing", good)
            argv = self.argv(routing, run_id=f"pin-{i}", **change)
            with self.subTest(pin=label):
                code, _, err, run = self.run_j3(argv)
                self.assertEqual(code, 2, label)
                self.assertTrue(err.startswith("error: "), err)
                self.assertIn(message, err)
                self.assertFalse(run.exists())
        self.assertEqual(server.chat_requests, [])
        with mock.patch.object(lab_j3, "code_hash", lambda: "1" * 64):
            code, _, err, run = self.run_j3(self.argv(good, run_id="pin-code-now"))
        self.assertEqual((code, run.exists()), (2, False))
        self.assertIn(differs.format("the code hash"), err)
        code, _, err, run = self.run_j3(self.argv(good, run_id="pin-j2", prereg=ROOT / "lab" / "models.json"))
        self.assertEqual(code, 2)
        self.assertIn("not a J3 prereg.json", err)
        code, _, err, run = self.run_j3(self.argv(good, run_id="pin-good"))
        self.assertEqual((code, run.exists()), (0, True), err)
        self.assertEqual(len(server.chat_requests), 3 * len(self.qs))

    def test_the_unit_adapter_runs_it(self) -> None:
        unit = next(u for u in self.p.plan["units"] if u["unit"] == "j3-fake-b-p2")
        prereg = lab_prereg.load_prereg(self.p.plan_path)
        out = self.tmp / "shard"
        record = units.run_unit(dict(unit, shard="s002-fake-b"), self.p.plan, out, timeout_s=300,
                                provider_override="fake", prereg=prereg)
        self.assertEqual((record["status"], record["measurement_class"], record["exit_code"]),
                         ("ok", "plumbing", 0), record["status_reason"])
        self.assertEqual(record["notes"], ["plumbing", "public_narratives", "runner_hardware"])
        base = f"runs/j3/{unit['run_id']}/"
        self.assertEqual(sorted(record["files"]), [base + "ledger.jsonl", base + "run.json", base + "verdicts.jsonl"])
        argv = record["argv"]
        self.assertEqual(argv[2:4], ["lab.j3", "run"])
        self.assertIn("--part=2", argv)
        self.assertIn("--endpoint=fake-b", argv)
        self.assertIn(f"--budget-seconds={300 - units.SIM_BUDGET_MARGIN_S}", argv)
        self.assertTrue(any(a.endswith("prereg/j3/prereg.json") for a in argv))
        routing = read_json(out / "routing" / f"{unit['unit']}.json")
        self.assertEqual((sorted(routing["endpoints"]), routing["routes"]),
                         (["fake-b"], {E: {"endpoint": "fake-b"}, R: {"endpoint": "fake-b"}, N: {"endpoint": "fake-b"}}))
        self.assertEqual((record["participation"]["required"],
                          {t: record["participation"]["tasks"][t]["attempted"] for t in lab_j3.TASKS}),
                         ([E], {E: 40, R: 40, N: 40}))
        self.assertEqual(record["harness_measurement"], False)
        doc = read_json(out / (base + "run.json"))
        self.assertEqual((doc["complete"], doc["complete_by_task"]), (True, {E: True, R: True, N: True}))

    def test_the_status_reading(self) -> None:
        def result(complete: bool, e: bool, r: bool, n: bool, kind: str = "lab_j3_run") -> dict[str, Any]:
            return {"kind": kind, "complete": complete, "complete_by_task": {E: e, R: r, N: n}}

        self.assertEqual(units.harness_status("j3", 0, False, result(True, True, True, True)), ("ok", None))
        self.assertEqual(units.harness_status("j3", 1, False, result(False, True, False, False)),
                         ("ok", J3_EVIDENCE_STOPPED))
        self.assertEqual(units.harness_status("j3", 1, False, result(False, True, True, False)),
                         ("ok", J3_EVIDENCE_STOPPED))
        self.assertEqual(units.harness_status("j3", 1, False, result(False, False, False, False)), ("failed", J1_STOPPED))
        self.assertEqual(units.harness_status("j3", 1, False, result(False, False, True, True)), ("failed", J1_STOPPED))
        self.assertEqual(units.harness_status("j3", 1, False, result(False, True, True, True)), ("failed", J1_STOPPED))
        self.assertEqual(units.harness_status("j3", 0, False, result(False, True, False, False)),
                         ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j3", 0, False, result(True, True, True, False)),
                         ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j3", 1, False, result(True, True, True, True)),
                         ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j3", 0, False, result(True, True, True, True, kind="lab_j2_run")),
                         ("failed", RESULT_MISSING))
        self.assertEqual(units.harness_status("j3", 1, False, {"kind": "lab_j3_run", "complete": False,
                                                               "complete_by_task": {E: True, R: False}}),
                         ("failed", RESULT_MISSING))
        self.assertEqual(units.harness_status("j3", 0, False, None), ("failed", RESULT_MISSING))
        self.assertEqual(units.harness_status("j3", 3, False, result(True, True, True, True)), ("failed", UNEXPECTED_EXIT))
        self.assertEqual(units.harness_status("j3", 0, True, result(True, True, True, True))[0], "timed_out")
        self.assertEqual(units.harness_measurement("j3", {"measurement": True}), True)
        self.assertIs(units.harness_verdict("j3", {"measurement": False}), False)
        self.assertEqual(units.required_tasks({"experiment": "j3", "params": {}}), [E])
        self.assertTrue(units.public_text({"experiment": "j3", "params": {}}))
        self.assertEqual(units.unit_notes("j3", "model", public_text=True),
                         ["model_measurement_public", "public_narratives", "runner_hardware"])
        self.assertIn("j3", units.PREREG_EXPERIMENTS)
        self.assertIn("j3", units.REPLY_PARTICIPATION)
        self.assertIn("j3", units.PASS_EXPERIMENTS)
        adapter = units.ADAPTERS["j3"]
        self.assertEqual((adapter.module, adapter.routed_tasks, adapter.boundary, adapter.result_file,
                          adapter.subcommand, adapter.deadline_s),
                         ("lab.j3", lab_j3.TASKS, "site:lab", "run.json", "run", lab_j1.DEADLINE_S))
        self.assertEqual(sorted(adapter.collect), ["ledger.jsonl", "run.json", "verdicts.jsonl"])

    def test_participation_judges_a_later_pass_only_when_it_finished(self) -> None:
        def row(task: str, ref: str, ok: bool) -> dict[str, Any]:
            return {"task": task, "ref": ref, "attempt": 1, "ok": ok, "error_kind": None if ok else "network",
                    "boundary": "site:lab"}

        good_e = [row(E, f"e{i}", True) for i in range(10)]
        bad_r = [row(R, f"r{i}", i == 0) for i in range(10)]
        stopped = {"kind": "lab_j3_run", "complete": False, "complete_by_task": {E: True, R: False, N: False}}
        finished = {"kind": "lab_j3_run", "complete": True, "complete_by_task": {E: True, R: True, N: True}}
        record, problem = units.participation("j3", good_e + bad_r, [E], stopped)
        self.assertEqual((record["tasks"][R]["ok"], problem), (1, None))
        record, problem = units.participation("j3", good_e + bad_r, [E], finished)
        self.assertTrue(problem is not None and problem.startswith(LOW_PARTICIPATION) and R in problem)
        bad_e = [row(E, f"e{i}", i == 0) for i in range(10)]
        record, problem = units.participation("j3", bad_e, [E], stopped)
        self.assertTrue(problem is not None and problem.startswith(LOW_PARTICIPATION) and E in problem)
        record, problem = units.participation("j3", bad_r, [E], stopped)
        self.assertTrue(problem is not None and problem.startswith(notes.NO_MODEL_CALLS))


# --------------------------------------------------------------------------------------------------- wiring

class WiringTests(unittest.TestCase):
    def test_the_fake_provider_answers_tasks_e_and_r_behind_an_empty_field(self) -> None:
        """Both variant tasks are answered as the shipped judge is, the field first and empty, in the pack branch
        and the pack-free one; task N as before; an unknown name is still refused."""
        p = _Prereg.get()
        qs = lab_j1.read_questions((p.dir / "questions.jsonl").read_bytes())
        recs = {r["record_ref"]: r for r, _ in lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())}

        def request(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            from mycelic.collective.inference.fakeserver import _DATA_CLOSE, _DATA_OPEN
            return {"messages": [{"role": "system", "content": "x"},
                                 {"role": "user", "content": f"{_DATA_OPEN}{canonical_dumps(payload)}{_DATA_CLOSE}"}],
                    "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": {}}}}

        for responder in (Responder(PACK), Responder(None, pack_free_judge=True)):
            for q in qs[:6]:
                payload = lab_j1.payload(PACK, q, recs[q["record_ref"]])
                shipped = responder(request(JUDGE_TASK, payload))
                with self.subTest(responder=responder.pack is not None, question=q["question_id"]):
                    e, r = responder(request(E, payload)), responder(request(R, payload))
                    self.assertEqual(e, {"citation": "", **shipped})
                    self.assertEqual(r, {"analysis": "", **shipped})
                    self.assertEqual((list(e)[0], list(r)[0]), ("citation", "analysis"))
                    self.assertEqual(responder(request(N, payload)), shipped)
                    self.assertIn(shipped["describes_predicate"], ("yes", "no"))
                    with self.assertRaises(ResponderError):
                        responder(request("judge_record_other", payload))
        with self.assertRaises(ResponderError):
            Responder(None)(request(E, lab_j1.payload(PACK, qs[0], recs[qs[0]["record_ref"]])))

    def test_the_warm_up_sends_the_three_tasks_the_evidence_one_first(self) -> None:
        """B4: a J3 unit warms its three tasks with the judge's generated payloads, sorted by name, so the probe is
        task E's; the shipped judge and the extraction task are not registered for it."""
        p = _Prereg.get()
        units_ = [u for u in p.plan["units"] if u["model"] == "fake-a"]
        tasks = [t for t in lab_warmup.warm_tasks(units_, p.plan) if t.units]
        self.assertEqual([t.task.name for t in tasks], [E, N, R])
        evidence, negation, rationale = tasks
        self.assertEqual((evidence.task, rationale.task, negation.task),
                         (lab_j3.evidence_task(), lab_j3.rationale_task(), lab_j2.negation_task()))
        self.assertEqual((evidence.schema, rationale.schema, negation.schema),
                         (lab_j3.evidence_schema(), lab_j3.rationale_schema(), judge_schema()))
        for t in (negation, rationale):
            self.assertEqual((t.payload, t.worst, t.stream, t.pack), (evidence.payload, evidence.worst, False, NHTSA_PACK))
            self.assertEqual(t.units, evidence.units)
        self.assertEqual(sorted(evidence.units), sorted(u["unit"] for u in units_))
        self.assertNotIn(evidence.payload["record"]["text"], "".join(
            r["narrative"] for r, _ in lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())))
        j2_units = [dict(u, experiment="j2", unit=u["unit"].replace("j3", "j2")) for u in units_]
        self.assertEqual([t.task.name for t in lab_warmup.warm_tasks(j2_units, p.plan) if t.units], [JUDGE_TASK, N])

    def test_the_plan_outputs_name_both_excluded_runs(self) -> None:
        p = _Prereg.get()
        outputs = lab_plan.plan_outputs(p.plan, "0" * 64)
        self.assertEqual((outputs["j2_exclude_artifact"], outputs["j2_exclude_run_id"], outputs["j3_exclude_artifact_2"],
                          outputs["j3_exclude_run_id_2"]),
                         (j2_data.ARTIFACT, str(j3_data.J001_RUN_ID), j3_data.A001_ARTIFACT, str(j3_data.A001_RUN_ID)))
        self.assertEqual(lab_plan.exclude_entries(p.plan), p.exclude)
        tmp = Path(tempfile.mkdtemp(prefix="lab-j3-outputs-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        code, _, err = run_plan(["--request", str(p.tmp / "j3-test.json"), "--manifest", str(MANIFEST_TEST),
                                 "--out", str(tmp / "plan"), "--github-output", str(tmp / "out.txt")])
        self.assertEqual(code, 0, err)
        written = (tmp / "out.txt").read_text(encoding="utf-8")
        self.assertIn(f"j2_exclude_artifact={j2_data.ARTIFACT}\n", written)
        self.assertIn(f"j2_exclude_run_id={j3_data.J001_RUN_ID}\n", written)
        self.assertIn(f"j3_exclude_artifact_2={j3_data.A001_ARTIFACT}\n", written)
        self.assertIn(f"j3_exclude_run_id_2={j3_data.A001_RUN_ID}\n", written)
        self.assertEqual(p.plan["units"][0]["params"]["exclude"], p.exclude)
        self.assertEqual({u["params"]["primary"]["arm"] for u in p.plan["units"]}, {"evidence"})
        self.assertEqual({u["params"]["comparisons_before"] for u in p.plan["units"]}, {0})


    def test_the_workflow_downloads_the_second_earlier_artifact(self) -> None:
        """B2: the plan job's ``earlier-2`` step, of the ``earlier`` step's shape (the same pinned action, the
        workflow's own read-only token, ``continue-on-error``), downloads the second excluded run's plan artifact
        under the same ``lab-earlier`` directory by its own run id, keyed on the plan's second pair of outputs,
        after ``earlier`` and before ``prereg``; the workflow's permissions stay ``actions: read``, ``contents: read``
        and no job widens them."""
        import yaml
        text = (ROOT / ".github" / "workflows" / "mycelic-lab.yml").read_text(encoding="utf-8")
        wf = yaml.safe_load(text)
        self.assertEqual(wf["permissions"], {"actions": "read", "contents": "read"})
        self.assertFalse(any("permissions" in job for job in wf["jobs"].values()))
        steps = wf["jobs"]["plan"]["steps"]
        ids = [s.get("id") for s in steps]
        at = ids.index("prereg")
        self.assertEqual(ids[at - 3:at], ["plan", "earlier", "earlier-2"])
        earlier, second = steps[at - 2], steps[at - 1]
        self.assertTrue(earlier["uses"].startswith("actions/download-artifact@"))
        self.assertEqual(second["uses"], earlier["uses"])
        self.assertIs(second["continue-on-error"], True)
        self.assertEqual(second["if"], "steps.plan.outputs.j3_exclude_run_id_2 != ''")
        self.assertEqual(second["with"], {
            "name": "${{ steps.plan.outputs.j3_exclude_artifact_2 }}",
            "run-id": "${{ steps.plan.outputs.j3_exclude_run_id_2 }}",
            "github-token": "${{ github.token }}",
            "path": "${{ runner.temp }}/lab-earlier/${{ steps.plan.outputs.j3_exclude_run_id_2 }}"})
        self.assertEqual(earlier["if"], "steps.plan.outputs.j2_exclude_run_id != ''")
        self.assertEqual(earlier["with"]["path"],
                         "${{ runner.temp }}/lab-earlier/${{ steps.plan.outputs.j2_exclude_run_id }}")
        self.assertEqual(text.count("github.token"), 3)
        for key in ("j2_exclude_artifact", "j2_exclude_run_id", "j3_exclude_artifact_2", "j3_exclude_run_id_2"):
            self.assertIn(key, lab_plan.OUTPUT_KEYS)
        self.assertNotIn("lab-plan", second["with"]["path"])

    def test_the_notes_hold_no_digit(self) -> None:
        """The lab's guard over every fixed sentence, read for J3's: no ASCII digit in any J3 note, heading or
        column of ``lab.notes``, so no figure can ride in a fixed sentence and every number a summary prints is
        traced to a run file; the primary line's placeholders are the ones the summary fills."""
        names = sorted(n for n in dir(notes) if n.startswith("J3_"))
        self.assertEqual(names, ["J3_ARMS_NOTE", "J3_EVIDENCE_NOTE", "J3_EVIDENCE_STOPPED", "J3_EXCLUSION_DISAGREES",
                                 "J3_EXCLUSION_NOTE", "J3_FRESHNESS_UNVERIFIED", "J3_LABEL", "J3_PRIMARY_LINE",
                                 "J3_STAND_IN", "J3_TOO_FEW_RECORDS", "J3_VERDICT_NOTE"])
        texts = {n: getattr(notes, n) for n in names}
        headings = {k: v for k, v in notes.HEADINGS.items() if k == "j3" or k.startswith("j3-")}
        columns = {k: v for k, v in notes.COLUMNS.items() if k.startswith("j3_")}
        self.assertEqual(sorted(headings), ["j3", "j3-arms", "j3-evidence", "j3-lifts", "j3-tasks"])
        self.assertGreater(len(columns), 20)
        texts.update({f"HEADINGS.{k}": v for k, v in headings.items()})
        texts.update({f"COLUMNS.{k}": v for k, v in columns.items()})
        for name, value in texts.items():
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
                self.assertIsNone(re.search(r"[0-9]", value))
        self.assertEqual(sorted(set(re.findall(r"\{([a-z_]+)\}", notes.J3_PRIMARY_LINE))),
                         ["arm", "ba", "bound", "dhi", "diff", "dlo", "gap", "hi", "lex", "lo", "model", "other_arm",
                          "other_word", "target", "unknown", "word"])
        self.assertIn("J3", lab_summary.ids(notes.J3_LABEL))
        self.assertNotIn("{", lab_summary.ids(notes.J3_LABEL))


    def test_the_dry_run_takes_one_test_data_flag(self) -> None:
        """``lab.dryrun --j3-data`` hands the test data to the same three ``lab.prereg`` flags as ``--j2-data`` does;
        the two flags together are a usage error, refused before the plan is written."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-j3-dryrun-flags-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = lab_dryrun.main(["--request", str(TEMPLATES / "accuracy-002.json"), "--out", str(tmp / "out"),
                                    "--j2-data", str(tmp / "a"), "--j3-data", str(tmp / "b")])
        self.assertEqual(code, lab_dryrun.EXIT_USAGE)
        self.assertIn("--j2-data and --j3-data do not go together", err.getvalue())
        self.assertFalse((tmp / "out").exists())


# --------------------------------------------------------------------------------------------------- dry runs

class SmallDryRunTests(unittest.TestCase):
    """The small dry run: two gguf models on the fake provider, the test data standing in for both excluded runs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Small.get()

    def test_every_unit_ran_against_the_fake_provider(self) -> None:
        self.assertEqual(self.dry.code, 0, self.dry.stdout)
        lines = self.dry.stdout.splitlines()
        self.assertIn(f"plan: 4 units in {len(self.dry.plan['shards'])} shards (real)", lines)
        self.assertIn("j3 exclusion: j001=artifact a001=artifact consistency j1_r1=0 a1_r2=0 outside_chain=0 "
                      "chain_union=0 excluded 300", lines)
        self.assertIn("prereg: e1 no x1 no e2 no j1 no j3 yes", lines)
        self.assertEqual({(u["status"], u["display_class"]) for u in self.dry.report["units"]}, {("ok", "plumbing")})
        self.assertEqual(sorted(u["unit"] for u in self.dry.plan["units"]),
                         ["j3-a-1p5b-p1", "j3-a-1p5b-p2", "j3-a-4b-p1", "j3-a-4b-p2"])

    def test_the_report_block(self) -> None:
        j3 = self.dry.report["j3"]
        self.assertEqual((j3["label"], j3["reason"], j3["display_class"], j3["stand_in"], sorted(j3["models"])),
                         ("public_nhtsa", None, "plumbing", True, ["a-1p5b", "a-4b"]))
        self.assertEqual((j3["labels"]["records"], j3["labels"]["available"], j3["questions"]["questions"],
                          j3["questions"]["part_records"]), (40, 540, 80, [20, 20]))
        self.assertEqual(j3["comparisons"], {"this_cycle": 18, "before": 0, "since_loop_began": 18})
        self.assertEqual((j3["primary"], j3["headline_arm"], j3["headline_secondary"], j3["target_balanced_accuracy"]),
                         ({"model": "a-4b", "arm": "evidence"}, "evidence", "evidence_grounded", 0.86))
        self.assertEqual((j3["tasks"], j3["mapping"], j3["grounding"], j3["composite"], j3["arms"], j3["lifts"],
                          j3["ablation_differences"]),
                         (lab_j3.task_pins(), lab_j3.mapping_pin(), lab_j3.grounding_pin(), lab_j3.composite_pin(),
                          lab_j3.arms_doc(), lab_j3.lifts_doc(), lab_j3.ablation_differences_doc()))
        ex = j3["exclusion"]
        self.assertEqual((ex["excluded"], ex["eligible_after_exclusion"], ex["overlap"], ex["mechanisms"],
                          ex["stand_in"], ex["consistency"]),
                         (300, 240, 0, {"artifact": "verified", "rebuild": "verified"}, True,
                          {"j1_r1": 0, "a1_r2": 0, "outside_chain": 0, "chain_union": 0}))
        self.assertEqual({s: ex["sources"][s]["outcome"] for s in j3labels.SOURCES},
                         dict.fromkeys(j3labels.SOURCES, "verified"))
        self.assertEqual(j3["settings"], {"parts": 2, "seed": 3, "bootstrap_b": 1000, "bootstrap_seed": 3,
                                          "comparisons_before": 0, "target_balanced_accuracy": 0.86,
                                          "withhold_share": 0.01})
        self.assertEqual(j3["chain_lines"], 340)
        manifest = self.dry.manifest["j3"]
        for key in ("lexical", "prior", "prior_bound", "ablation_a", "ablation_b", "lexical_unknown"):
            self.assertEqual(j3[key], manifest[key], key)
        self.assertEqual(j3["lexical_unknown"], 0)
        lexical = j3["lexical"]
        for name, entry in j3["models"].items():
            with self.subTest(model=name):
                self.assertEqual(sorted(entry), ["ablation_differences", "arms", "evidence", "first_keys", "lifts",
                                                 "parts", "sets", "tokens"])
                self.assertEqual(sorted(entry["sets"]), ["evidence", "negation", "rationale"])
                self.assertEqual({(s["task"], s["complete"], s["parts_finished"], s["display_class"], s["measurement"])
                                  for s in entry["sets"].values()},
                                 {(E, True, 2, "plumbing", False), (R, True, 2, "plumbing", False),
                                  (N, True, 2, "plumbing", False)})
                for arm in lab_j3.ARMS:
                    a = entry["arms"][arm]
                    self.assertEqual((a["letter"], a["task"], a["reading"], a["composite"], a["set"], a["complete"],
                                      a["parts_finished"], a["parts_planned"], a["display_class"], a["verdict_word"],
                                      a["records_dropped"]),
                                     (lab_j3.ARM_LETTERS[arm], lab_j3.ARM_TASK[arm], lab_j3.ARM_READING[arm],
                                      lab_j3.ARM_COMPOSITE[arm], lab_j3.TASK_LABELS[lab_j3.ARM_TASK[arm]], True, 2, 2,
                                      "plumbing", "incomplete", 0), arm)
                    self.assertEqual((a["paired_lexical"]["n"], a["prior_bound"], a["primary"], a["headline_arm"],
                                      a["headline_secondary"], a["headline"]),
                                     (40, j3["prior_bound"], name == "a-4b" and arm == "evidence", arm == "evidence",
                                      arm == "evidence_grounded", None))
                    self.assertEqual(a["headline_reason"], J1_NOT_MEASURED if arm in ("evidence", "evidence_grounded")
                                     else None)
                    self.assertFalse(a["pass_test"]["passed"])
                    if arm in ("evidence_grounded", "evidence_grounded_mapped"):
                        # the fake quotes nothing: every lexical confirm is refuted under G, so 0.5 at best
                        self.assertEqual((a["scores"]["sensitivity"]["value"], a["scores"]["specificity"]["value"]),
                                         (0.0, 1.0))
                        self.assertLess(a["paired_lexical"]["mean_diff"], 0)
                        self.assertEqual((a["ablation_a"], a["paired_ablation_a"]), (None, None))
                    else:
                        # every other arm reads the lexical judge's answers, never unclear, so it is the lexical judge
                        for metric in lab_j1.METRICS:
                            self.assertEqual(a["scores"][metric], lexical[metric], (arm, metric))
                        self.assertEqual(a["paired_lexical"]["mean_diff"], 0.0)
                    if lab_j3.ARM_COMPOSITE[arm]:
                        clauses = a["pass_test"]["ablation_clauses"]
                        self.assertEqual(clauses["beats_ablation_a"], False)
                        self.assertEqual(clauses["beats_ablation_b"], a["paired_ablation_b"]["ci_low"] > 0)
                        self.assertEqual(a["paired_ablation_a"], a["paired_lexical"])       # B8: no lexical unknown
                        for metric in lab_j1.METRICS:
                            self.assertEqual(a["ablation_a"][metric], a["lexical"][metric], (arm, metric))
                        self.assertEqual((a["paired_ablation_b"]["n"], a["ablation_b"]["records_scored"]), (40, 40))
                    else:
                        self.assertNotIn("ablation_clauses", a["pass_test"])
                        self.assertEqual((a["ablation_b"], a["paired_ablation_b"]), (None, None))
                for lift, arm, minus in lab_j3.LIFTS:
                    entry_l = entry["lifts"][lift]
                    self.assertEqual((entry_l["arm"], entry_l["minus"], entry_l["lever"], entry_l["complete"],
                                      entry_l["paired"]["n"]), (arm, minus, lab_j3.LIFT_LEVERS[lift], True, 40))
                readings = {lift: entry["lifts"][lift]["reading"] for lift in entry["lifts"]}
                self.assertEqual(readings, {"E_minus_N": "not_told_apart", "Eg_minus_E": "hurts", "Eg_minus_N": "hurts",
                                            "Egm_minus_Eg": "not_told_apart", "Egm_minus_D": "hurts",
                                            "R_minus_N": "not_told_apart", "Rm_minus_D": "not_told_apart",
                                            "D_minus_N": "not_told_apart", "C_minus_Eg": "helps",
                                            "Cm_minus_Egm": "helps"})
                for name_d, arm, ablation in lab_j3.ABLATION_DIFFERENCES:
                    d = entry["ablation_differences"][name_d]
                    self.assertEqual((d["arm"], d["ablation"], d["complete"], d["paired"]["n"]), (arm, ablation, True, 40))
                    self.assertEqual(d["reading"], "not_told_apart" if ablation == "lexical_or_no"
                                     else lab_j3.lift_reading(d["paired"]))
                    self.assertEqual(d["paired"], entry["arms"][arm]["paired_ablation_a" if ablation == "lexical_or_no"
                                                                      else "paired_ablation_b"])
                evidence = entry["evidence"]
                confirmed = sum(1 for line in lab_aggregate._j1_lines((self.dry.out / "plan" / "prereg" / "j3"
                                                                        / "lexical.jsonl").read_bytes())
                                if line["kind"] == "positive" and line["verdict"] == "confirm")
                self.assertEqual(evidence["replies"], 80)
                self.assertEqual(evidence["grounded_yes"]["positive"], {"yes": confirmed, "grounded": 0, "share": 0.0})
                self.assertEqual(evidence["grounded_yes"]["negative"], {"yes": 0, "grounded": 0, "share": None})
                self.assertEqual((evidence["empty_citations"], evidence["empty_citation_share"],
                                  evidence["citation_chars_p50"], evidence["citation_chars_p95"]), (80, 1.0, 0.0, 0.0))
                self.assertEqual(entry["first_keys"][E], {"field": "citation", "replies": 80, "field_first": 80,
                                                          "share": 1.0, "first_keys": {"citation": 80}})
                self.assertEqual(entry["first_keys"][R]["share"], 1.0)
                self.assertEqual((entry["first_keys"][N]["field"], entry["first_keys"][N]["share"],
                                  entry["first_keys"][N]["field_first"], sum(entry["first_keys"][N]["first_keys"].values())),
                                 (None, None, None, 80))
                for task in lab_j3.TASKS:
                    tokens = entry["tokens"][task]
                    self.assertEqual((tokens["attempts"], tokens["calls_ok"], tokens["cut_at_cap"]), (80, 80, 0))
                    self.assertIsNotNone(tokens["latency_ms_p50"])
                for part in entry["parts"]:
                    self.assertEqual((part["ok_by_task"], part["complete_by_task"], part["stopped"]),
                                     ({E: True, R: True, N: True}, {E: True, R: True, N: True}, None))
                    self.assertEqual(sorted(part["tokens_by_task"]), sorted(lab_j3.TASKS))
        primary = j3["primary_result"]
        self.assertEqual((primary["model"], primary["arm"], primary["verdict_word"], primary["complete"],
                          primary["display_class"], primary["measurement"], primary["headline"],
                          primary["headline_reason"]),
                         ("a-4b", "evidence", "incomplete", True, "plumbing", False, None, J1_NOT_MEASURED))
        self.assertEqual(primary["lexical_balanced_accuracy"], lexical["balanced_accuracy"]["value"])
        self.assertEqual((primary["headline_secondary"]["arm"], primary["headline_secondary"]["verdict_word"],
                          primary["headline_secondary"]["headline"], primary["headline_secondary"]["headline_reason"]),
                         ("evidence_grounded", "incomplete", None, J1_NOT_MEASURED))

    def test_the_summaries(self) -> None:
        root = self.dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        self.assertEqual(md.splitlines()[0], NO_MEASUREMENT_LINE)
        for heading in ("j3", "j3-arms", "j3-lifts", "j3-evidence", "j3-tasks"):
            self.assertIn(f"#### {notes.HEADINGS[heading]}", md)
        for note in (J3_STAND_IN, J3_ARMS_NOTE, J3_VERDICT_NOTE, J3_EXCLUSION_NOTE, J3_EVIDENCE_NOTE):
            self.assertIn(note, md)
        self.assertIn("J3 here asks one model", md.replace("`", ""))
        self.assertIn("Primary pair `a-4b` `evidence`: `incomplete`. It scored balanced accuracy", md)
        self.assertIn("gap to the target 0.860 is", md)
        self.assertIn("Under the other reading of the standing default the primary would be `evidence_grounded`: "
                      "`incomplete`.", md)
        self.assertIn("- comparisons since the loop began: 18 (18 this cycle, 0 before)", md)
        self.assertIn("| `a-4b` | `evidence` | `E` | `judge_record_evidence` | `shipped` | `plumbing` | 2 of 2 |", md)
        self.assertIn("| `a-4b` | `composite_mapped` | `Cm` | `judge_record_evidence` | `grounded_mapped` | `plumbing` "
                      "| 2 of 2 |", md)
        self.assertIn("| `a-4b` | `E_minus_N` | `evidence_field` | 40 | 0.000 |", md)
        self.assertIn("| `a-4b` | `C_minus_ablation_A` | `lexical_or_no` | 40 | 0.000 |", md)
        self.assertIn("| `a-4b` | 80 | 0.000 (0 of ", md)
        self.assertIn("| `a-4b` | `judge_record_evidence` | 1.000 | 80 | 80 |", md)
        self.assertIn("| `a-4b` | `judge_record_negation` | n/a | 80 | 80 |", md)
        self.assertIn("eligible records 540; excluded records 300; eligible after exclusion 240", md)
        self.assertIn("- sources: `J1` `verified` (150 refs); `R1` `verified` (150 refs); `A1` `verified` (150 refs); "
                      "`R2` `verified` (150 refs); `K` `verified` (300 refs)", md)
        self.assertIn("chain file lines 340", md)
        sources = read_json(root / "report.sources.json")["sources"]
        check_sources(self, md, sources, root)
        plan_md = (self.dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        plan_sources = read_json(self.dry.out / "plan" / "summary.sources.json")
        check_sources(self, plan_md, plan_sources["sources"], self.dry.out / "plan")
        self.assertIn("`J3` labels: source `nhtsa`, records 40", plan_md)
        self.assertIn(J3_STAND_IN, plan_md)
        drawn = refs_of_labels((self.dry.out / "plan" / "prereg" / "j3" / "labels.jsonl").read_bytes())
        excluded = {line["record_ref"] for line in read_lines(self.dry.out / "plan" / "prereg" / "j3"
                                                              / "excluded.jsonl")}
        self.assertEqual(len(excluded), 340)
        for text in (md, plan_md, (root / "report.json").read_text(encoding="utf-8"), self.dry.stdout):
            assert_no_ref(self, text, drawn | excluded)
            self.assertNotIn("Operator", text)
        # no narrative, citation or analysis text reaches the report (B23)
        texts = texts_of(self.dry.out / "plan" / "prereg" / "j3")
        report_text = (root / "report.json").read_text(encoding="utf-8")
        for text in texts.values():
            self.assertNotIn(text[:40], report_text)


class AggregateTests(unittest.TestCase):
    """DryTree copies of the small dry run, changed and re-sealed as a real run would have left them: the verdict
    words with the ablation clauses (rule 12, B8), the lifts (rule 10), the stop that spares task E (rule 14), the
    line check with ``grounded`` recomputed from the drawn narratives (B23) and ``first_key`` checked for shape (B19)."""

    def setUp(self) -> None:
        self.dry = _Small.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j3-agg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = DryTree(self.dry.out, self.tmp / "T", manifest=LAB_MANIFEST)
        self.prereg_dir = self.dry.out / "plan" / "prereg" / "j3"
        self.questions = lab_j1.read_questions((self.prereg_dir / "questions.jsonl").read_bytes())
        self.lexical = {line["question_id"]: line for line in read_lines(self.prereg_dir / "lexical.jsonl")}
        self.texts = texts_of(self.prereg_dir)

    def shard_of(self, model: str, part: int) -> tuple[str, dict[str, Any]]:
        unit = next(u for u in self.tree.plan["units"] if u["unit"] == f"j3-{model}-p{part}")
        return next(s["shard"] for s in self.tree.plan["shards"] if unit["unit"] in s["units"]), unit

    def make_measured(self, *models: str) -> None:
        for model in models:
            shards: dict[str, list[str]] = {}
            for part in (1, 2):
                shard, unit = self.shard_of(model, part)
                rel = f"runs/j3/{unit['run_id']}/run.json"
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
        rel = f"runs/j3/{unit['run_id']}/verdicts.jsonl"
        got = read_lines(self.tree.path(shard, rel))
        if lines is not None:
            lines(got)
        data = jsonl(got)
        self.tree.replace_unit_file(shard, unit["unit"], rel, data)
        run_rel = f"runs/j3/{unit['run_id']}/run.json"
        doc = self.tree.read(shard, run_rel)
        doc["verdicts_sha256"] = hashlib.sha256(data).hexdigest()
        if run is not None:
            run(doc)
        self.tree.replace_unit_file(shard, unit["unit"], run_rel, canonical_bytes(doc) + b"\n")
        if unit_record is not None:
            self.tree.edit(shard, f"units/{unit['unit']}/unit.json", unit_record)
        self.tree.reseal(shard)

    def answer(self, line: dict[str, Any], m: str | None, d: str | None, error_kind: str | None = None, *,
               citation: str | None = None) -> None:
        """Rewrite a line's reply, its field, ``grounded``, ``first_key`` and every reading's verdict consistently,
        as the runner would have written them (an E line's citation given, or the record's own first forty
        characters)."""
        task = line["task"]
        two = None if m is None else {"mentions_entity": m, "describes_predicate": d}
        field = lab_j3.TASK_FIELDS[task]
        if two is None:
            reply = None
        elif field == "citation":
            reply = {"citation": citation if citation is not None else self.texts[line["record_ref"]][:40], **two}
        elif field == "analysis":
            reply = {"analysis": "two sentences of analysis.", **two}
        else:
            reply = two
        q = next(q for q in self.questions if q["question_id"] == line["question_id"])
        fresh = lab_j3.verdict_line(q, lab_j1.stand_in(q["record_ref"]), reply, error_kind, task,
                                    self.texts[line["record_ref"]])
        line.clear()
        line.update(fresh)

    def correct(self, line: dict[str, Any], *, citation: str | None = None) -> None:
        self.answer(line, "yes", "yes" if line["kind"] == "positive" else "no", citation=citation)

    def aggregate(self) -> dict[str, Any]:
        shutil.rmtree(self.tmp / "R", True)
        code, _, err, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, err)
        return report["j3"]

    def test_the_verdict_words_and_the_lifts(self) -> None:
        """a-4b, measured, answers every task right with grounded citations: every arm passes provisionally, the
        composite arms through both ablation clauses, the lifts are not told apart; a-1p5b, measured, answers as the
        lexical judge quoting nothing: failure everywhere, the grounding check hurts."""
        self.make_measured("a-4b", "a-1p5b")
        for part in (1, 2):
            self.restamp("a-4b", part, lines=lambda ls: [self.correct(x) for x in ls])
        block = self.aggregate()
        lexical_ba = block["lexical"]["balanced_accuracy"]["value"]
        bound = block["prior_bound"]["value"]
        arms = block["models"]["a-4b"]["arms"]
        for arm in lab_j3.ARMS:
            a = arms[arm]
            with self.subTest(arm=arm):
                self.assertEqual((a["display_class"], a["measurement"], a["complete"], a["verdict_word"]),
                                 ("model", True, True, "pass_provisional"))
                self.assertEqual((a["scores"]["balanced_accuracy"]["value"], a["scores"]["unknown_share"]["value"],
                                  a["records_dropped"], a["dropped_share"]), (1.0, 0.0, 0, 0.0))
                self.assertTrue(a["pass_test"]["passed"])
                self.assertEqual(a["pass_test"]["clauses"], {"balanced_accuracy_at_target": True, "beats_lexical": True,
                                                             "beats_bound": True, "unknown_share_reported": True})
                self.assertEqual(a["lexical"]["balanced_accuracy"]["value"], lexical_ba)
                self.assertAlmostEqual(a["paired_lexical"]["mean_diff"], 1.0 - lexical_ba, places=12)
                self.assertGreater(a["paired_lexical"]["ci_low"], 0)
                self.assertEqual((a["prior_bound"]["value"], a["pass_test"]["bound"]), (bound, bound))
                self.assertAlmostEqual(a["pass_test"]["gap_to_target"], -0.14, places=12)
                if lab_j3.ARM_COMPOSITE[arm]:
                    self.assertEqual(a["pass_test"]["ablation_clauses"], {"beats_ablation_a": True,
                                                                          "beats_ablation_b": True})
                    self.assertGreater(a["pass_test"]["diff_ablation_b_ci_low"], 0)
                    self.assertEqual(a["paired_ablation_a"], a["paired_lexical"])
        self.assertEqual((arms["evidence"]["headline"], arms["evidence"]["headline_reason"], arms["evidence"]["primary"],
                          arms["evidence"]["headline_arm"]), ("better", None, True, True))
        self.assertEqual((arms["evidence_grounded"]["headline"], arms["evidence_grounded"]["primary"],
                          arms["evidence_grounded"]["headline_secondary"]), ("better", False, True))
        self.assertEqual({arm: arms[arm]["headline"] for arm in lab_j3.ARMS if arm not in ("evidence", "evidence_grounded")},
                         dict.fromkeys([a for a in lab_j3.ARMS if a not in ("evidence", "evidence_grounded")], None))
        lifts = block["models"]["a-4b"]["lifts"]
        self.assertEqual({name: entry["reading"] for name, entry in lifts.items()},
                         dict.fromkeys(lab_j3.LIFT_LEVERS, "not_told_apart"))
        self.assertEqual({name: entry["complete"] for name, entry in lifts.items()}, dict.fromkeys(lifts, True))
        self.assertEqual({name: entry["reading"] for name, entry in block["models"]["a-4b"]["ablation_differences"].items()},
                         dict.fromkeys(("C_minus_ablation_A", "C_minus_ablation_B", "Cm_minus_ablation_A",
                                        "Cm_minus_ablation_B"), "helps"))
        evidence = block["models"]["a-4b"]["evidence"]
        self.assertEqual((evidence["grounded_yes"]["positive"]["share"], evidence["grounded_yes"]["negative"]["yes"],
                          evidence["empty_citation_share"]), (1.0, 0, 0.0))
        self.assertTrue(30 <= evidence["citation_chars_p50"] <= evidence["citation_chars_p95"] <= 40.0)
        other = block["models"]["a-1p5b"]
        self.assertEqual({arm: a["verdict_word"] for arm, a in other["arms"].items()}, dict.fromkeys(lab_j3.ARMS, "failure"))
        self.assertEqual((other["arms"]["evidence"]["headline"], other["arms"]["evidence_grounded"]["headline"],
                          other["arms"]["evidence"]["display_class"]), ("not_told_apart", "worse", "model"))
        self.assertEqual({name: entry["reading"] for name, entry in other["lifts"].items()},
                         {"E_minus_N": "not_told_apart", "Eg_minus_E": "hurts", "Eg_minus_N": "hurts",
                          "Egm_minus_Eg": "not_told_apart", "Egm_minus_D": "hurts", "R_minus_N": "not_told_apart",
                          "Rm_minus_D": "not_told_apart", "D_minus_N": "not_told_apart", "C_minus_Eg": "helps",
                          "Cm_minus_Egm": "helps"})
        c_other = other["arms"]["composite"]
        self.assertEqual(c_other["pass_test"]["ablation_clauses"],
                         {"beats_ablation_a": False, "beats_ablation_b": c_other["paired_ablation_b"]["ci_low"] > 0})
        primary = block["primary_result"]
        self.assertEqual((primary["model"], primary["arm"], primary["verdict_word"], primary["pass_test"]["passed"],
                          primary["headline"], primary["headline_secondary"]["verdict_word"],
                          primary["headline_secondary"]["headline"]),
                         ("a-4b", "evidence", "pass_provisional", True, "better", "pass_provisional", "better"))
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("Primary pair `a-4b` `evidence`: `pass_provisional`. It scored balanced accuracy 1.000 "
                      "[1.000, 1.000]; gap to the target 0.860 is -0.140;", md)
        self.assertIn("Under the other reading of the standing default the primary would be `evidence_grounded`: "
                      "`pass_provisional`.", md)
        self.assertIn("| `pass_provisional` | yes | yes | no | `better` |", md)
        self.assertIn("| `pass_provisional` | yes | no | yes | `better` |", md)
        self.assertIn("| `failure` | no | no | no | n/a |", md)
        self.assertIn("| `a-1p5b` | `Eg_minus_E` | `grounding_check` | 40 |", md)
        self.assertIn("| `hurts` |", md)
        self.assertIn("| `a-4b` | 80 | 1.000 (", md)
        self.assertNotIn(J1_NOT_MEASURED, md)

    def test_the_grounding_check_and_the_composite_s_words(self) -> None:
        """Rule 4, B8 and B17 in the aggregate: a-4b confirms everything, quoting the record on positives and
        inventing on negatives: E fails (specificity 0), Eg passes provisionally (the check removed the false
        confirms only), the grounded shares say so, and the composite is Eg where the lexical judge does not confirm,
        its ablation-A difference equal to its lexical difference to the figure."""
        self.make_measured("a-4b")

        def confirm_all(lines: list[dict[str, Any]]) -> None:
            for x in lines:
                if x["task"] == E:
                    self.answer(x, "yes", "yes", citation=None if x["kind"] == "positive" else "no such passage here")
        for part in (1, 2):
            self.restamp("a-4b", part, lines=confirm_all)
        block = self.aggregate()
        arms = block["models"]["a-4b"]["arms"]
        e, eg, c = arms["evidence"], arms["evidence_grounded"], arms["composite"]
        self.assertEqual((e["scores"]["sensitivity"]["value"], e["scores"]["specificity"]["value"],
                          e["scores"]["balanced_accuracy"]["value"], e["verdict_word"]), (1.0, 0.0, 0.5, "failure"))
        self.assertEqual((eg["scores"]["sensitivity"]["value"], eg["scores"]["specificity"]["value"],
                          eg["verdict_word"], eg["pass_test"]["passed"]), (1.0, 1.0, "pass_provisional", True))
        evidence = block["models"]["a-4b"]["evidence"]
        self.assertEqual((evidence["grounded_yes"]["positive"]["share"], evidence["grounded_yes"]["negative"]["share"],
                          evidence["grounded_yes"]["positive"]["yes"], evidence["grounded_yes"]["negative"]["yes"]),
                         (1.0, 0.0, 40, 40))
        lifts = block["models"]["a-4b"]["lifts"]
        self.assertEqual((lifts["Eg_minus_E"]["reading"], lifts["Eg_minus_N"]["reading"], lifts["E_minus_N"]["reading"]),
                         ("helps", "helps", "hurts"))
        self.assertAlmostEqual(lifts["Eg_minus_E"]["paired"]["mean_diff"], 0.5, places=12)
        # the composite: confirm where the lexical judge confirms, else Eg's (correct) verdict
        view = lab_j3.arm_lines([line for part in (1, 2) for line in self.lines_of("a-4b", part)], "composite",
                                self.lexical)
        self.assertEqual(c["scores"], lab_j1.score(self.questions, view, bootstrap_b=1000, bootstrap_seed=3))
        false_confirms = sum(1 for q in self.questions if q["kind"] == "negative"
                             and self.lexical[q["question_id"]]["verdict"] == "confirm")
        self.assertAlmostEqual(c["scores"]["specificity"]["value"], 1 - false_confirms / 40, places=12)
        self.assertEqual(c["scores"]["sensitivity"]["value"], 1.0)
        self.assertEqual(c["paired_ablation_a"], c["paired_lexical"])                  # B8, no lexical unknown
        self.assertEqual(block["lexical_unknown"], 0)
        word, test = lab_j3.composite_verdict_word(
            complete=True, display_class="model", measurement=True, dropped_share=0.0, scores=c["scores"],
            paired=c["paired_lexical"], bound=c["prior_bound"], ablation_a=c["paired_ablation_a"],
            ablation_b=c["paired_ablation_b"])
        self.assertEqual((c["verdict_word"], c["pass_test"]), (word, test))
        # and pinned to a value, not to itself: Eg is right on every question, so the composite errs only where the
        # lexical judge confirms a negative; its balanced accuracy 1 - false_confirms / 80 is at or above the target
        # on this draw, and its paired lower ends are above the lexical judge, the bound and both ablations
        self.assertLessEqual(false_confirms, 11)
        self.assertEqual((c["verdict_word"], c["pass_test"]["passed"],
                          c["pass_test"]["ablation_clauses"]["beats_ablation_a"],
                          c["pass_test"]["ablation_clauses"]["beats_ablation_b"]), ("pass_provisional", True, True, True))
        self.assertEqual(c["pass_test"]["ablation_clauses"]["beats_ablation_a"], c["pass_test"]["clauses"]["beats_lexical"])
        self.assertEqual(block["models"]["a-4b"]["ablation_differences"]["C_minus_ablation_A"]["paired"],
                         c["paired_lexical"])
        self.assertEqual(block["primary_result"]["verdict_word"], "failure")
        self.assertEqual(block["primary_result"]["headline_secondary"]["verdict_word"], "pass_provisional")
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("Primary pair `a-4b` `evidence`: `failure`.", md)
        self.assertIn("Under the other reading of the standing default the primary would be `evidence_grounded`: "
                      "`pass_provisional`.", md)
        self.assertIn("| `a-4b` | 80 | 1.000 (40 of 40) | 0.000 (0 of 40) |", md)

    def lines_of(self, model: str, part: int) -> list[dict[str, Any]]:
        shard, unit = self.shard_of(model, part)
        return read_lines(self.tree.path(shard, f"runs/j3/{unit['run_id']}/verdicts.jsonl"))

    def test_partial_and_the_mapping_s_lift(self) -> None:
        """a-4b's task E confirms twelve positives the lexical judge missed, quoting the record: below the target but
        the lexical judge beaten, so partial for E and Eg alike. a-1p5b's task N answers unclear on ten negatives:
        unknown under the shipped rule (failure, hurts), refute under the mapping (D - N helps)."""
        self.make_measured("a-4b", "a-1p5b")
        missed = [q["question_id"] for q in self.questions if q["kind"] == "positive"
                  and self.lexical[q["question_id"]]["verdict"] != "confirm"]
        self.assertGreaterEqual(len(missed), 12)
        fix = set(missed[:12])

        def quote_all(lines: list[dict[str, Any]]) -> None:
            for x in lines:
                if x["task"] == E and x["question_id"] in fix:
                    self.answer(x, "yes", "yes")
                elif x["task"] == E and x["mentions_entity"] is not None:
                    self.answer(x, x["mentions_entity"], x["describes_predicate"])
        for part in (1, 2):
            self.restamp("a-4b", part, lines=quote_all)
        negatives = [q["question_id"] for q in self.questions if q["kind"] == "negative"][:10]
        for part in (1, 2):
            self.restamp("a-1p5b", part, lines=lambda ls: [self.answer(x, "yes", "unclear") for x in ls
                                                           if x["task"] == N and x["question_id"] in negatives])
        block = self.aggregate()
        lexical = block["lexical"]
        arms = block["models"]["a-4b"]["arms"]
        for arm in ("evidence", "evidence_grounded", "composite"):
            a = arms[arm]
            with self.subTest(arm=arm):
                self.assertLess(a["scores"]["balanced_accuracy"]["value"], 0.86)
                self.assertGreater(a["paired_lexical"]["ci_low"], 0)
                self.assertAlmostEqual(a["paired_lexical"]["mean_diff"], 12 / 80, places=12)
                self.assertEqual((a["verdict_word"], a["pass_test"]["passed"]), ("partial", False))
                self.assertEqual(a["pass_test"]["clauses"]["balanced_accuracy_at_target"], False)
        self.assertAlmostEqual(arms["evidence"]["scores"]["sensitivity"]["value"],
                               lexical["sensitivity"]["value"] + 12 / 40, places=12)
        self.assertEqual(arms["evidence_grounded"]["scores"], arms["evidence"]["scores"])   # the quotes are grounded
        self.assertEqual(block["primary_result"]["verdict_word"], "partial")
        self.assertEqual(block["models"]["a-4b"]["lifts"]["E_minus_N"]["reading"], "helps")
        self.assertEqual(block["models"]["a-4b"]["lifts"]["Eg_minus_E"]["reading"], "not_told_apart")
        model = block["models"]["a-1p5b"]
        n, d = model["arms"]["negation"], model["arms"]["negation_mapped"]
        self.assertEqual((n["scores"]["specificity"]["value"], d["scores"]["specificity"]["value"]),
                         (lexical["specificity"]["value"] - 10 / 40, lexical["specificity"]["value"]))
        self.assertEqual((n["scores"]["unknown_share"]["value"], d["scores"]["unknown_share"]["value"]), (10 / 80, 0.0))
        self.assertEqual((n["verdict_word"], d["verdict_word"], n["pass_test"]["clauses"]["beats_lexical"]),
                         ("failure", "failure", False))
        self.assertLess(n["paired_lexical"]["ci_high"], 0)
        readings = {name: entry["reading"] for name, entry in model["lifts"].items()}
        self.assertEqual((readings["D_minus_N"], readings["E_minus_N"], readings["R_minus_N"], readings["Egm_minus_D"]),
                         ("helps", "helps", "helps", "hurts"))
        self.assertAlmostEqual(model["lifts"]["D_minus_N"]["paired"]["mean_diff"], 10 / 80, places=12)
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("Primary pair `a-4b` `evidence`: `partial`.", md)
        self.assertIn("| `a-1p5b` | `D_minus_N` | `unclear_mapping` | 40 | 0.125 |", md)

    def test_what_is_not_a_finished_part(self) -> None:
        """Rule 15, B19 and B23: a ``grounded`` that disagrees with the citation and the drawn narrative, a grounded
        verdict that disagrees with the pinned decide, a first key that is not a schema key, a citation on an N
        line, a line of a fourth task, a run of another preregistration and a (question, task) twice each leave the
        part unfinished for every task; a first key that is another schema key is a shape the runner could have
        written."""

        def first(match: Callable[[dict[str, Any]], bool], change: dict[str, Any]) -> Callable[[list], None]:
            def edit(lines: list[dict[str, Any]]) -> None:
                next(line for line in lines if match(line)).update(change)
            return edit

        self.restamp("a-1p5b", 1, lines=first(lambda x: x["task"] == E and x["verdict"] == "confirm", {"grounded": True}))
        self.restamp("a-1p5b", 2, lines=first(lambda x: x["task"] == E, {"first_key": "describes_predicate"}))
        self.restamp("a-4b", 1, lines=first(lambda x: x["task"] == N, {"citation": ""}))
        self.restamp("a-4b", 2, lines=first(lambda x: x["task"] == E and x["verdict"] == "confirm",
                                             {"grounded_verdict": "confirm"}))
        block = self.aggregate()
        small, big = block["models"]["a-1p5b"], block["models"]["a-4b"]
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in small["parts"]],
                         [(1, {E: False, R: False, N: False}), (2, {E: True, R: True, N: True})])
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in big["parts"]],
                         [(1, {E: False, R: False, N: False}), (2, {E: False, R: False, N: False})])
        self.assertEqual({s["parts_ok"] == [2] and not s["complete"] and s["partial"] for s in small["sets"].values()},
                         {True})
        self.assertEqual({arm: a["verdict_word"] for arm, a in small["arms"].items()}, dict.fromkeys(lab_j3.ARMS, "incomplete"))
        self.assertEqual({(a["parts_finished"], a["partial"], a["scores"]["records_scored"])
                          for a in small["arms"].values()}, {(1, True, 20)})
        self.assertEqual({arm: a["scores"] for arm, a in big["arms"].items()}, dict.fromkeys(lab_j3.ARMS, None))
        self.assertEqual({(entry["complete"], entry["paired"], entry["reading"]) for entry in big["lifts"].values()},
                         {(False, None, None)})
        self.assertEqual({(entry["complete"], entry["paired"]) for entry in big["ablation_differences"].values()},
                         {(False, None)})
        self.assertEqual(block["primary_result"]["verdict_word"], "incomplete")
        self.assertEqual(big["evidence"]["replies"], 0)
        self.assertEqual(big["first_keys"][E]["share"], None)
        # a grounded citation marked false is refused the same way (the text decides, B23)
        self.restamp("a-1p5b", 2, lines=first(lambda x: x["task"] == E and x["verdict"] == "confirm",
                                               {"grounded": False, "first_key": "citation"}))
        self.restamp("a-1p5b", 1, lines=lambda ls: [x.update(grounded=False) for x in ls if x["task"] == E])
        small = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in small["parts"]],
                         [(1, {E: True, R: True, N: True}), (2, {E: True, R: True, N: True})])
        quoted = next(x for x in self.lines_of("a-1p5b", 2) if x["task"] == E and x["verdict"] == "confirm")
        self.restamp("a-1p5b", 2, lines=lambda ls: [self.answer(x, "yes", "yes") for x in ls
                                                    if x["question_id"] == quoted["question_id"] and x["task"] == E])
        self.restamp("a-1p5b", 2, lines=first(lambda x: x["question_id"] == quoted["question_id"] and x["task"] == E,
                                               {"grounded": False}))
        small = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual(next(p["ok_by_task"] for p in small["parts"] if p["part"] == 2), {E: False, R: False, N: False})
        # a fourth task, another preregistration, a (question, task) twice, a run.json that says a task stopped
        self.restamp("a-1p5b", 2, lines=first(lambda x: x["task"] == R, {"task": JUDGE_TASK}))
        self.restamp("a-4b", 1, run=lambda doc: doc.update(prereg_sha256="0" * 64))
        self.restamp("a-4b", 2, lines=lambda ls: ls.append(dict(ls[0])))
        self.restamp("a-1p5b", 1, run=lambda doc: doc.update(complete=False, stopped="interrupted",
                                                             complete_by_task={E: True, R: False, N: False}))
        block = self.aggregate()
        small, big = block["models"]["a-1p5b"], block["models"]["a-4b"]
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in small["parts"]],
                         [(1, {E: True, R: False, N: False}), (2, {E: False, R: False, N: False})])
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in big["parts"]],
                         [(1, {E: False, R: False, N: False}), (2, {E: False, R: False, N: False})])
        self.assertEqual((small["arms"]["evidence"]["scores"]["records_scored"], small["arms"]["rationale"]["scores"]),
                         (20, None))
        # a run.json of another endpoint is not this unit's run, however good its lines: the fourth-task line put
        # back and the quoted line answered consistently again, part 2 of a-1p5b is refused with endpoint a-4b and
        # finished with its own endpoint restored

        def mend(ls: list[dict[str, Any]]) -> None:
            next(x for x in ls if x["task"] == JUDGE_TASK)["task"] = R
            for x in ls:
                if x["question_id"] == quoted["question_id"] and x["task"] == E:
                    self.answer(x, "yes", "yes")
        self.restamp("a-1p5b", 2, lines=mend, run=lambda doc: doc.update(endpoint="a-4b"))
        small = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual(next(p["ok_by_task"] for p in small["parts"] if p["part"] == 2), {E: False, R: False, N: False})
        self.restamp("a-1p5b", 2, run=lambda doc: doc.update(endpoint="a-1p5b"))
        small = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual(next(p["ok_by_task"] for p in small["parts"] if p["part"] == 2), {E: True, R: True, N: True})
        # a verdicts file replaced under a run.json whose verdicts_sha256 is the old file's, the shard re-sealed so
        # the seal holds: not a finished part for any task, although every line passes the check; the same lines
        # under a restamped run.json are
        shard, unit = self.shard_of("a-1p5b", 2)
        rel = f"runs/j3/{unit['run_id']}/verdicts.jsonl"
        lines = read_lines(self.tree.path(shard, rel))
        for x in lines:
            if x["task"] == N:
                self.correct(x)
        data = jsonl(lines)
        self.assertNotEqual(data, self.tree.path(shard, rel).read_bytes())
        self.tree.replace_unit_file(shard, unit["unit"], rel, data)
        self.tree.reseal(shard)
        run = self.tree.read(shard, f"runs/j3/{unit['run_id']}/run.json")
        self.assertNotEqual(run["verdicts_sha256"], hashlib.sha256(data).hexdigest())
        small = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual(next(p["ok_by_task"] for p in small["parts"] if p["part"] == 2), {E: False, R: False, N: False})
        self.restamp("a-1p5b", 2)
        run = self.tree.read(shard, f"runs/j3/{unit['run_id']}/run.json")
        self.assertEqual(run["verdicts_sha256"], hashlib.sha256(data).hexdigest())
        small = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual(next(p["ok_by_task"] for p in small["parts"] if p["part"] == 2), {E: True, R: True, N: True})

    def test_a_stop_in_the_second_pass_and_a_dropped_record(self) -> None:
        """Rule 14 in the aggregate: a part whose rationale pass stopped keeps task E's set complete (P and the
        composite arms get their words) and leaves R's and N's incomplete; a record left out by a transport failure
        in task E withholds that set and every arm on it when its share is above one in a hundred, and the other
        sets are untouched."""
        self.make_measured("a-4b", "a-1p5b")

        def drop_later(lines: list[dict[str, Any]]) -> None:
            lines[:] = [line for line in lines if line["task"] == E]

        self.restamp("a-4b", 2, lines=drop_later,
                     run=lambda doc: doc.update(complete=False, stopped="budget", stopped_task=R,
                                                complete_by_task={E: True, R: False, N: False}, questions_done=40,
                                                questions_done_by_task={E: 40, R: 0, N: 0}),
                     unit_record=lambda r: r.update(status="ok", status_reason=J3_EVIDENCE_STOPPED, exit_code=1))
        every = lab_j1.prior_bound(self.questions)
        refs = sorted({q["record_ref"] for q in self.questions})
        dropped = next(q for q in self.questions if q["part"] == 1 and q["kind"] == "positive"
                       and lab_j1.prior_bound(self.questions, [r for r in refs if r != q["record_ref"]])["value"]
                       != every["value"])
        self.restamp("a-1p5b", 1, lines=lambda ls: [self.answer(x, None, None, "timeout") for x in ls
                                                    if x["task"] == E and x["question_id"] == dropped["question_id"]])
        block = self.aggregate()
        big = block["models"]["a-4b"]
        part = next(p for p in big["parts"] if p["part"] == 2)
        self.assertEqual((part["status"], part["status_reason"], part["ok_by_task"], part["stopped"],
                          part["stopped_task"]), ("ok", J3_EVIDENCE_STOPPED, {E: True, R: False, N: False}, "budget", R))
        self.assertEqual((big["sets"]["evidence"]["complete"], big["sets"]["evidence"]["parts_ok"]), (True, [1, 2]))
        for label in ("rationale", "negation"):
            self.assertEqual((big["sets"][label]["complete"], big["sets"][label]["partial"], big["sets"][label]["parts_ok"]),
                             (False, True, [1]))
        self.assertEqual({arm: a["verdict_word"] for arm, a in big["arms"].items()},
                         {"negation": "incomplete", "negation_mapped": "incomplete", "evidence": "failure",
                          "evidence_grounded": "failure", "evidence_grounded_mapped": "failure", "rationale": "incomplete",
                          "rationale_mapped": "incomplete", "composite": "failure", "composite_mapped": "failure"})
        self.assertEqual((big["arms"]["evidence"]["headline"], big["arms"]["rationale"]["scores"]["records_scored"],
                          big["arms"]["evidence"]["scores"]["records_scored"]), ("not_told_apart", 20, 40))
        self.assertEqual(block["primary_result"]["verdict_word"], "failure")
        self.assertEqual({name: entry["complete"] for name, entry in big["lifts"].items()},
                         {"E_minus_N": False, "Eg_minus_E": True, "Eg_minus_N": False, "Egm_minus_Eg": True,
                          "Egm_minus_D": False, "R_minus_N": False, "Rm_minus_D": False, "D_minus_N": False,
                          "C_minus_Eg": True, "Cm_minus_Egm": True})
        self.assertEqual((big["lifts"]["E_minus_N"]["paired"]["n"], big["lifts"]["Eg_minus_E"]["paired"]["n"]), (20, 40))
        self.assertEqual({name: entry["complete"] for name, entry in big["ablation_differences"].items()},
                         dict.fromkeys(("C_minus_ablation_A", "C_minus_ablation_B", "Cm_minus_ablation_A",
                                        "Cm_minus_ablation_B"), True))
        self.assertEqual((big["first_keys"][E]["replies"], big["first_keys"][R]["replies"], big["tokens"][E]["attempts"],
                          big["tokens"][R]["attempts"]), (80, 40, 80, 40))
        small = block["models"]["a-1p5b"]
        e, c, n = small["arms"]["evidence"], small["arms"]["composite"], small["arms"]["negation"]
        for arm in (e, c, small["arms"]["evidence_grounded"]):
            self.assertEqual((arm["records_dropped"], arm["dropped_share"], arm["verdict_word"], arm["withheld_reason"]),
                             (1, 1 / 40, "incomplete", J1_WITHHELD))
        self.assertEqual((e["headline"], e["headline_reason"], e["complete"], e["scores"]["records_scored"],
                          e["lexical"]["records_scored"]), (None, J1_WITHHELD, True, 39, 39))
        self.assertEqual((n["records_dropped"], n["verdict_word"], n["scores"]["records_scored"]), (0, "failure", 40))
        self.assertEqual(small["lifts"]["E_minus_N"]["paired"]["n"], 39)
        kept = [r for r in refs if r != dropped["record_ref"]]
        self.assertEqual(e["prior_bound"], lab_j1.prior_bound(self.questions, kept))
        self.assertEqual((e["prior_bound"]["records"], e["pass_test"]["bound"]), (39, e["prior_bound"]["value"]))
        self.assertEqual((c["ablation_a"]["records_scored"], c["ablation_b"]["records_scored"],
                          c["paired_ablation_b"]["n"]), (39, 39, 39))
        self.assertEqual((n["prior_bound"], block["prior_bound"]), (every, every))
        self.assertEqual(small["evidence"]["replies"], 79)
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("| `a-4b` | `rationale` | `R` | `judge_record_rationale` | `shipped` | `model` | 1 of 2 |", md)
        self.assertIn("| `a-4b` | `evidence` | `E` | `judge_record_evidence` | `shipped` | `model` | 2 of 2 |", md)
        self.assertIn(J1_WITHHELD, md)

    def test_a_manifest_that_disagrees_with_the_preregistration(self) -> None:
        """The manifest ``prereg/prereg.json`` (tied to the plan's sha) must agree with ``prereg/j3/prereg.json``
        (tied to every run.json) on the primary pair, the headline arm and secondary and the four comparator
        files: an edited manifest can name neither another primary pair nor other comparator lines than the units
        verified before judging. Each disagreement refuses the block with PREREG_MISSING; the files put back, the
        block is read again."""
        plan_dir = self.tree.root / "plan"
        manifest_path = plan_dir / "prereg" / "prereg.json"
        pristine = manifest_path.read_bytes()
        block = self.aggregate()
        self.assertEqual((block["reason"], block["primary_result"]["arm"], sorted(block["models"])),
                         (None, "evidence", ["a-1p5b", "a-4b"]))
        edits: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            ("primary.arm", lambda j3: j3["primary"].update(arm="evidence_grounded")),
            ("primary.model", lambda j3: j3["primary"].update(model="a-1p5b")),
            ("headline_arm", lambda j3: j3.update(headline_arm="evidence_grounded")),
            ("headline_secondary", lambda j3: j3.update(headline_secondary="evidence"))]
        for name, change in edits:
            with self.subTest(edit=name):
                manifest = json.loads(pristine)
                change(manifest["j3"])
                manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
                block = self.aggregate()
                self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                                 (lab_aggregate.PREREG_MISSING, {}, None))
        manifest_path.write_bytes(pristine)
        # a comparator file rewritten and restamped in the manifest (its sha256 and size), but not in the document
        # the units pinned, whose sha256 of that file is the plan job's
        for name in ("lexical", "prior", "ablation_a", "ablation_b"):
            with self.subTest(file=name):
                rel = f"prereg/j3/{name}.jsonl"
                original = (plan_dir / rel).read_bytes()
                lines = read_lines(plan_dir / rel)
                lines[0]["verdict"] = "refute" if lines[0]["verdict"] != "refute" else "confirm"
                restamp(plan_dir, rel, jsonl(lines))
                self.assertEqual(lab_prereg.load_prereg(self.tree.plan_path).manifest["files"][rel]["sha256"],
                                 sha256_hex(jsonl(lines)))
                block = self.aggregate()
                self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                                 (lab_aggregate.PREREG_MISSING, {}, None))
                restamp(plan_dir, rel, original)
        self.assertEqual(json.loads(manifest_path.read_bytes()), json.loads(pristine))
        block = self.aggregate()
        self.assertEqual((block["reason"], block["primary_result"]["arm"]), (None, "evidence"))
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


class TemplateDryRunTests(unittest.TestCase):
    """``lab.dryrun`` of the template the run copies to ``lab/requests/accuracy-002.json``: 5 units in 5 shards, every
    source verified on the stand-in, 240 eligible after exclusion, overlap 0, every word incomplete, 21 comparisons."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Template.get()

    def test_the_plan(self) -> None:
        plan = self.dry.plan
        self.assertEqual((len(plan["units"]), len(plan["shards"]), plan["result_class"], plan["max_parallel"]),
                         (5, 5, "real", 5))
        self.assertEqual([s["units"] for s in plan["shards"]], [[f"j3-a-4b-p{k}"] for k in range(1, 6)])
        self.assertEqual({(s["planned_minutes"], s["timeout_minutes"]) for s in plan["shards"]}, {(300, 325)})
        self.assertEqual(sorted(e["key"] for e in plan["provision"] if e["target"] == "gguf"), ["a-4b"])

    def test_every_unit_ran_against_the_fake_provider(self) -> None:
        self.assertEqual(self.dry.code, 0, self.dry.stdout)
        lines = self.dry.stdout.splitlines()
        self.assertIn("plan: 5 units in 5 shards (real)", lines)
        self.assertIn("j3 exclusion: j001=artifact a001=artifact consistency j1_r1=0 a1_r2=0 outside_chain=0 "
                      "chain_union=0 excluded 300", lines)
        self.assertTrue(any(line.startswith("j3 draw: available 540 excluded 300 eligible_after_exclusion 240 overlap 0 "
                                            "sources J1=verified R1=verified A1=verified R2=verified K=verified "
                                            "j001=artifact a001=artifact consistency j1_r1=0 a1_r2=0 outside_chain=0 "
                                            "chain_union=0 labels_sha256 ") for line in lines), self.dry.stdout)
        self.assertTrue(any("excluded_lines 450 complaint_file_sha256" in line for line in lines))
        self.assertIn("prereg: e1 no x1 no e2 no j1 no j3 yes", lines)
        units_ = self.dry.report["units"]
        self.assertEqual(len(units_), 5)
        self.assertEqual({(u["status"], u["display_class"]) for u in units_}, {("ok", "plumbing")})

    def test_the_report_block(self) -> None:
        j3 = self.dry.report["j3"]
        self.assertEqual((j3["reason"], j3["display_class"], j3["stand_in"], sorted(j3["models"])),
                         (None, "plumbing", True, ["a-4b"]))
        self.assertEqual((j3["labels"]["records"], j3["labels"]["n"], j3["labels"]["seed"], j3["questions"]["questions"],
                          j3["questions"]["parts"], j3["questions"]["part_records"], j3["questions"]["seed"]),
                         (150, 150, 3, 300, 5, [30] * 5, 3))
        self.assertEqual(j3["comparisons"], {"this_cycle": 9, "before": 12, "since_loop_began": 21})
        self.assertEqual((j3["primary"], j3["headline_arm"], j3["headline_secondary"]),
                         ({"model": "a-4b", "arm": "evidence"}, "evidence", "evidence_grounded"))
        self.assertEqual((j3["settings"]["bootstrap_b"], j3["settings"]["bootstrap_seed"], j3["settings"]["seed"]),
                         (10000, 3, 3))
        ex = j3["exclusion"]
        self.assertEqual((ex["excluded"], ex["eligible_after_exclusion"], ex["overlap"], ex["stand_in"],
                          ex["expected_excluded"]), (300, 240, 0, True, 300))
        self.assertEqual({s: ex["sources"][s]["outcome"] for s in j3labels.SOURCES},
                         dict.fromkeys(j3labels.SOURCES, "verified"))
        runs = ex["excluded_runs"]
        self.assertEqual([(r["stand_in"], r["requested"], r["refs"], r["established_by"]) for r in runs],
                         [(True, {k: J001_ENTRY[k] for k in sorted(J001_ENTRY)}, 150, "artifact"),
                          (True, {k: A001_ENTRY[k] for k in sorted(A001_ENTRY)}, 150, "artifact")])
        self.assertEqual((j3["prior_bound"]["records"], j3["prior_bound"]["questions"], j3["chain_lines"]),
                         (150, 300, 450))
        entry = j3["models"]["a-4b"]
        self.assertEqual({(s["complete"], s["parts_finished"]) for s in entry["sets"].values()}, {(True, 5)})
        for arm in lab_j3.ARMS:
            a = entry["arms"][arm]
            self.assertEqual((a["verdict_word"], a["parts_finished"], a["display_class"], a["paired_lexical"]["n"]),
                             ("incomplete", 5, "plumbing", 150))
            if arm not in ("evidence_grounded", "evidence_grounded_mapped"):
                self.assertEqual(a["scores"]["balanced_accuracy"], j3["lexical"]["balanced_accuracy"])
        self.assertEqual({lift: entry["lifts"][lift]["paired"]["n"] for lift in entry["lifts"]},
                         dict.fromkeys(lab_j3.LIFT_LEVERS, 150))
        self.assertEqual(j3["primary_result"]["verdict_word"], "incomplete")
        self.assertEqual(j3["primary_result"]["headline_secondary"]["verdict_word"], "incomplete")

    def test_the_summaries(self) -> None:
        root = self.dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        self.assertEqual(md.splitlines()[0], NO_MEASUREMENT_LINE)
        self.assertIn(f"#### {notes.HEADINGS['j3']}", md)
        self.assertIn(J3_STAND_IN, md)
        self.assertIn("Primary pair `a-4b` `evidence`: `incomplete`.", md)
        self.assertIn("- comparisons since the loop began: 21 (9 this cycle, 12 before)", md)
        self.assertIn("| `a-4b` | `evidence` | `E` | `judge_record_evidence` | `shipped` | `plumbing` | 5 of 5 |", md)
        self.assertIn("chain file lines 450", md)
        check_sources(self, md, read_json(root / "report.sources.json")["sources"], root)
        plan_md = (self.dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        check_sources(self, plan_md, read_json(self.dry.out / "plan" / "summary.sources.json")["sources"],
                      self.dry.out / "plan")
        self.assertIn("`J3` labels: source `nhtsa`, records 150", plan_md)
        self.assertIn("`J3` primary pair: `a-4b` `evidence`; headline secondary `evidence_grounded`; target balanced "
                      "accuracy 0.860", plan_md)
        drawn = refs_of_labels((self.dry.out / "plan" / "prereg" / "j3" / "labels.jsonl").read_bytes())
        excluded = {line["record_ref"] for line in read_lines(self.dry.out / "plan" / "prereg" / "j3"
                                                              / "excluded.jsonl")}
        self.assertEqual((len(drawn), len(excluded)), (150, 450))
        for text in (md, plan_md, (root / "report.json").read_text(encoding="utf-8"), self.dry.stdout):
            assert_no_ref(self, text, drawn | excluded)


if __name__ == "__main__":
    unittest.main()

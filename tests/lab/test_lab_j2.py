"""Accuracy test A001 (``lab.j2`` and ``lab.j2labels``, the lab's ``j2`` experiment; ``docs/collective/A001/CHOICE-A001.md``
as amended A1 to A12): the rule's constants pinned against its text, the two tasks and the mapping, the verdict lines
and the arms' views (A8), the verdict word (A5, A6) and the lift readings, the fresh draw and both freshness
mechanisms on synthetic artifacts (A1 to A4), the request block and the template, the preregistration with its stop
rules, the runner through the repo's fake model server (task N first, A9), the unit status reading, the aggregate's
words and lifts, the fake provider's answer to task N (A10), the warm-up's two tasks (A11) and two dry runs. Nothing
here measures a model: every server is the collective's fake, every complaint archive is built in the test
(``tests.lab.j2_data``: every value invented), no earlier run's artifact is downloaded and no NHTSA host is reached.

Three fixtures are made once: a J2 plan of two fake models preregistered against a synthetic earlier artifact
(:class:`_Prereg`), a small dry run of two gguf models on the fake provider with the test data (:class:`_Small`, the
aggregate's cases are :class:`~tests.lab.helpers.DryTree` copies of it) and the template's dry run (:class:`_Template`).
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import random
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
from lab import notes
from lab import plan as lab_plan
from lab import prereg as lab_prereg
from lab import request as lab_request
from lab import summary as lab_summary
from lab import units
from lab import warmup as lab_warmup
from lab.goldlabels import NHTSA_PACK
from lab.manifest import load_manifest
from lab.notes import (J1_NOT_MEASURED, J1_STOPPED, J1_WITHHELD, J2_ARMS_NOTE, J2_EXCLUSION_DISAGREES,
                       J2_EXCLUSION_NOTE, J2_FRESHNESS_UNVERIFIED, J2_SHIPPED_STOPPED, J2_STAND_IN,
                       J2_TOO_FEW_RECORDS, J2_VERDICT_NOTE, LOW_PARTICIPATION, NO_MEASUREMENT_LINE, RESULT_CONTRADICTS_EXIT,
                       RESULT_MISSING, UNEXPECTED_EXIT)
from lab.plan import build_plan
from lab.request import (EXPERIMENTS, J2_ARTIFACT_PROBLEM, J2_FILE_PROBLEM, J2_LABELS_PROBLEM, J2_PRIMARY_ARM_PROBLEM,
                         J2_PRIMARY_MODEL_PROBLEM, J2_SHA_PROBLEM, NHTSA_HOSTED, RequestError, load_request, validate)
from lab.responder import Responder, ResponderError
from mycelic.collective.edge.verify import JUDGE_MAX_TOKENS, JUDGE_TASK, decide, judge_schema, judge_task
from mycelic.collective.experiments import e1_extract
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps, sha256_hex
from mycelic.collective.packs import loader
from mycelic.collective.packs.canonical import Canonicaliser
from tests.lab import j2_data
from tests.lab.helpers import (LAB_MANIFEST, MANIFEST_TEST, ROOT, TEMPLATES, DryTree, call_main, check_sources,
                               kill_mentioning, plumbing_min, run_plan, write_json)

PACK = loader.load_pack(ROOT / NHTSA_PACK)
MANIFEST = load_manifest(MANIFEST_TEST)
CHOICE = ROOT / "docs" / "collective" / "A001" / "CHOICE-A001.md"
ARCHIVE = j2_data.archive(60, 1)          # 360 eligible synthetic records; every value invented
LABELS = {"source": "nhtsa", "pack": NHTSA_PACK, "n": 40, "seed": 2}
J001_EXCLUDE = {"run_id": 37996675559, "artifact": "lab-plan-37996675559-1", "file": "prereg/j1/labels.jsonl",
                "sha256_prefix": "bc6d092d8bca"}
N, SHIPPED = lab_j2.NEGATION_TASK, JUDGE_TASK


def exclude_of(stand_in: dict[str, Any]) -> dict[str, Any]:
    return {k: stand_in[k] for k in ("run_id", "artifact", "file", "sha256_prefix")}


def j2_block(exclude: dict[str, Any], **changes: Any) -> dict[str, Any]:
    block = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 2, "bootstrap_b": 1000, "bootstrap_seed": 2,
             "exclude": exclude, "primary": {"model": "fake-b", "arm": "negation"}}
    block.update(changes)
    return block


def small_request(**changes: Any) -> dict[str, Any]:
    """A J2 request of two gguf models for the dry run on the fake provider (the template's shape, 40 records in 2
    parts); ``--j2-data`` stands in for its ``exclude`` entry."""
    block = j2_block(J001_EXCLUDE, primary={"model": "a-4b", "arm": "negation"})
    block.update(changes)
    return {"schema_version": 1, "purpose": "Accuracy test A001's plumbing on two models and forty records.",
            "provider": "llama-server", "models": ["a-1p5b", "a-4b"], "job_minutes": 60, "max_parallel": 4,
            "retention_days": 30, "experiments": {"j2": block}}


def refs_of_labels(data: bytes) -> set[str]:
    return {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def jsonl(lines: list[dict[str, Any]]) -> bytes:
    return "".join(canonical_dumps(line) + "\n" for line in lines).encode("utf-8")


def assert_no_ref(test: unittest.TestCase, text: str, refs: set[str]) -> None:
    """No record ref of ``refs`` appears in ``text`` (rule 13: counts, scores and hashes only)."""
    hits = sorted(r for r in refs if r in text)
    test.assertEqual(hits, [], f"{len(hits)} record refs in the text")


# --------------------------------------------------------------------------------------------------- fixtures

class _Prereg:
    """A J2 plan of two fake models (40 records in 2 parts) whose request excludes the synthetic earlier run, planned
    and preregistered once with the test archive: mechanism 1 verifies on the synthetic artifact, mechanism 2 cannot
    (the archive is not NHTSA's), and one verifying suffices (A2)."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Prereg]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j2-prereg-"))
            cls.earlier = cls.tmp / "earlier"
            cls.stand_in = j2_data.earlier_artifact(cls.earlier, ARCHIVE)
            cls.exclude = exclude_of(cls.stand_in)
            request = plumbing_min()
            request["experiments"] = {"j2": j2_block(cls.exclude)}
            cls.request = request
            path = write_json(cls.tmp / "j2-test.json", request)
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
            cls.dir = cls.tmp / "plan" / "prereg" / "j2"
            cls.excluded = refs_of_labels((cls.earlier / str(j2_data.RUN_ID) / "prereg" / "j1" / "labels.jsonl")
                                          .read_bytes())
            cls.drawn = refs_of_labels((cls.dir / "labels.jsonl").read_bytes())
        return cls


class _Small:
    """``lab.dryrun`` of :func:`small_request` with ``--j2-data`` (both mechanisms verify on the stand-in), the
    shards on the fake provider, made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Small]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j2-small-"))
            cls.data = j2_data.write_data(cls.tmp / "data")
            request = write_json(cls.tmp / "accuracy-small.json", small_request())
            cls.out = cls.tmp / "D"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cls.code = lab_dryrun.main(["--request", str(request), "--out", str(cls.out),
                                            "--manifest", str(LAB_MANIFEST), "--j2-data", str(cls.data)])
            cls.stdout = stdout.getvalue()
            cls.plan = read_json(cls.out / "plan" / "plan.json")
            cls.report = read_json(cls.out / "report" / "report.json")
            cls.manifest = read_json(cls.out / "plan" / "prereg" / "prereg.json")
        return cls


class _Template:
    """``lab.dryrun`` of ``lab/templates/accuracy.json`` (the request the run copies to ``lab/requests/``) with
    ``--j2-data``, made once."""

    tmp: Path | None = None

    @classmethod
    def get(cls) -> "type[_Template]":
        if cls.tmp is None:
            cls.tmp = Path(tempfile.mkdtemp(prefix="lab-j2-template-"))
            cls.data = j2_data.write_data(cls.tmp / "data")
            cls.out = cls.tmp / "D"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                cls.code = lab_dryrun.main(["--request", str(TEMPLATES / "accuracy.json"), "--out", str(cls.out),
                                            "--manifest", str(LAB_MANIFEST), "--j2-data", str(cls.data)])
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

def rule_blockquote() -> list[str]:
    """The lines of rule 3's blockquote (arm C's instructions, verbatim) in CHOICE-A001.md."""
    text = CHOICE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(text) if line.rstrip().endswith("N's instructions, fixed now, verbatim:"))
    quote = []
    for line in text[start + 1:]:
        stripped = line.strip()
        if stripped.startswith(">"):
            quote.append(stripped[1:].strip())
        elif quote:
            break
    return quote


def sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.) ", text)


class RuleTests(unittest.TestCase):
    """The constants of ``lab.j2`` against the rule's text (rule 3, rules 8 to 10, A5, A8, A9)."""

    def test_the_negation_instructions_are_rule_3_s_text_verbatim(self) -> None:
        quote = rule_blockquote()
        self.assertGreaterEqual(len(quote), 8)
        self.assertEqual(" ".join(quote), lab_j2.NEGATION_INSTRUCTIONS)
        shipped, variant = sentences(judge_task().instructions), sentences(lab_j2.NEGATION_INSTRUCTIONS)
        # the first three sentences and the last are the shipped ones; only the describes_predicate text changes
        self.assertEqual(variant[:3], shipped[:3])
        self.assertEqual(variant[-1], shipped[-1])
        self.assertTrue(variant[3].startswith("describes_predicate:"))
        self.assertTrue(shipped[3].startswith("describes_predicate:"))
        self.assertNotEqual(variant[3:-1], shipped[3:-1])
        self.assertIn("a component the record never mentions is a no, not an unclear", lab_j2.NEGATION_INSTRUCTIONS)
        # N names no code, category or filed label (rule 3, last bullet)
        folded = lab_j2.NEGATION_INSTRUCTIONS.lower()
        for code in PACK.codes:
            self.assertNotIn(code.lower(), folded)
        for predicate in PACK.predicates:
            self.assertIsNone(re.search(rf"\b{re.escape(predicate.replace('_', ' '))}\b", folded), predicate)

    def test_the_two_tasks(self) -> None:
        task = lab_j2.negation_task()
        self.assertEqual((task.name, task.data_class, task.max_tokens), ("judge_record_negation", "raw", 256))
        self.assertEqual(task.max_tokens, JUDGE_MAX_TOKENS)
        self.assertEqual(task.instructions, lab_j2.NEGATION_INSTRUCTIONS)
        self.assertEqual(lab_j2.task_spec(SHIPPED), judge_task())
        self.assertEqual(lab_j2.task_spec(N), task)
        with self.assertRaises(lab_j2.J2Error):
            lab_j2.task_spec("judge_record_other")
        pins = lab_j2.task_pins()
        self.assertEqual(sorted(pins), ["negation", "order", "shipped"])
        self.assertEqual(pins["shipped"], lab_j1.task_pins())
        self.assertEqual(pins["negation"], {"name": N, "data_class": "raw",
                                            "instructions_sha256": sha256_hex(lab_j2.NEGATION_INSTRUCTIONS),
                                            "schema_sha256": sha256_hex(canonical_bytes(judge_schema())),
                                            "max_tokens": 256})
        self.assertEqual(pins["order"], [N, SHIPPED])
        self.assertEqual(lab_j2.TASKS, (N, SHIPPED))              # A9: task N first
        self.assertEqual(lab_j2.TASK_LABELS, {N: "negation", SHIPPED: "shipped"})

    def test_the_mapping_reads_the_reply_alone(self) -> None:
        self.assertEqual(lab_j2.MAPPING, {"describes_predicate": {"unclear": "no"}})
        cases = {("yes", "unclear"): ("yes", "no"), ("unclear", "unclear"): ("unclear", "no"),
                 ("no", "unclear"): ("no", "no"), ("yes", "yes"): ("yes", "yes"), ("yes", "no"): ("yes", "no"),
                 ("unclear", "yes"): ("unclear", "yes"), ("no", "no"): ("no", "no")}
        for (m, d), (wm, wd) in cases.items():
            self.assertEqual(lab_j2.mapped_reply({"mentions_entity": m, "describes_predicate": d}),
                             {"mentions_entity": wm, "describes_predicate": wd}, (m, d))
        self.assertIsNone(lab_j2.mapped_reply(None))
        self.assertEqual(list(inspect.signature(lab_j2.mapped_reply).parameters), ["reply"])
        pin = lab_j2.mapping_pin()
        self.assertEqual(pin, {"rule": {"describes_predicate": {"unclear": "no"}},
                               "sha256": sha256_hex(canonical_bytes(lab_j2.MAPPING))})

    def test_the_arms_the_lifts_and_the_target(self) -> None:
        self.assertEqual(lab_j2.ARMS, ("shipped", "shipped_mapped", "negation", "negation_mapped"))
        self.assertEqual(lab_j2.ARM_TASK, {"shipped": SHIPPED, "shipped_mapped": SHIPPED, "negation": N,
                                           "negation_mapped": N})
        self.assertEqual(lab_j2.ARM_MAPPED, {"shipped": False, "shipped_mapped": True, "negation": False,
                                             "negation_mapped": True})
        self.assertEqual(lab_j2.arms_doc(), {"shipped": {"task": SHIPPED, "mapped": False},
                                             "shipped_mapped": {"task": SHIPPED, "mapped": True},
                                             "negation": {"task": N, "mapped": False},
                                             "negation_mapped": {"task": N, "mapped": True}})
        self.assertEqual(lab_j2.LIFTS, (("B_minus_A", "shipped_mapped", "shipped"),
                                        ("C_minus_A", "negation", "shipped"),
                                        ("D_minus_A", "negation_mapped", "shipped"),
                                        ("D_minus_B", "negation_mapped", "shipped_mapped"),
                                        ("D_minus_C", "negation_mapped", "negation")))
        self.assertEqual(lab_j2.LIFT_LEVERS, {"B_minus_A": "unclear_mapping", "D_minus_C": "unclear_mapping",
                                              "C_minus_A": "negation_instruction",
                                              "D_minus_B": "negation_instruction", "D_minus_A": "both"})
        self.assertEqual(lab_j2.TARGET_BA, 0.86)
        self.assertEqual(lab_j2.VERDICT_WORDS, ("incomplete", "pass_provisional", "partial", "failure"))
        self.assertEqual(lab_j2.READINGS, ("helps", "hurts", "not_told_apart"))
        self.assertEqual((lab_j2.WITHHOLD_SHARE, lab_j2.BOUNDARY, lab_j2.DATA_LABEL),
                         (lab_j1.WITHHOLD_SHARE, "site:lab", "public"))
        self.assertEqual(lab_j2.LINE_KEYS, (*lab_j1.LINE_KEYS, "task", "mapped_verdict"))
        self.assertEqual(lab_j2.CODE_FILES, (*lab_j1.CODE_FILES, "lab/j2.py", "lab/j2labels.py"))
        self.assertTrue(set(lab_j1.PREREG_KEYS) - {"task"} <= set(lab_j2.PREREG_KEYS))
        for key in ("tasks", "mapping", "arms", "primary", "headline_arm", "exclusion", "comparisons_before",
                    "target_balanced_accuracy"):
            self.assertIn(key, lab_j2.PREREG_KEYS)
        self.assertNotIn("task", lab_j2.PREREG_KEYS)
        self.assertEqual(lab_j2.EXCLUSION_KEYS, ("excluded", "eligible_after_exclusion", "overlap", "mechanisms",
                                                 "complaint_file_sha256", "excluded_runs", "excluded_sha256"))
        self.assertEqual(j2labels.J001_LABELS_SHA256_PREFIX, "bc6d092d8bca")
        self.assertEqual(j2labels.REBUILD, {"source": "nhtsa", "n": 150, "seed": 1})
        self.assertEqual(j2labels.EXCLUDE_FILES, ("prereg/j1/labels.jsonl", "prereg/j2/excluded.jsonl"))

    def test_the_routing_routes_both_tasks_to_the_one_endpoint(self) -> None:
        models = _Prereg.get().plan["models"]
        doc = lab_j2.routing_doc(models, ["fake-a"], "http://127.0.0.1:1/v1")
        self.assertEqual(doc["routes"], {N: {"endpoint": "fake-a"}, SHIPPED: {"endpoint": "fake-a"}})
        self.assertEqual(doc["endpoints"], {"fake-a": lab_j1.endpoint(models["fake-a"], "http://127.0.0.1:1/v1")})
        self.assertEqual(doc["endpoints"]["fake-a"]["boundary"], "site:lab")
        both = lab_j2.routing_doc(models, ["fake-a", "fake-b"], "http://127.0.0.1:1/v1")
        self.assertEqual((sorted(both["endpoints"]), both["routes"]), (["fake-a", "fake-b"], {}))

    def test_every_verdict_is_the_pinned_decide_over_the_one_record(self) -> None:
        p = _Prereg.get()
        record = next(iter(lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())))[0]
        q = next(q for q in lab_j1.read_questions((p.dir / "questions.jsonl").read_bytes())
                 if q["record_ref"] == record["record_ref"])
        w = lab_j1.window_record(record)
        cases = {("yes", "yes"): ("confirm", "confirm"), ("yes", "no"): ("refute", "refute"),
                 ("yes", "unclear"): ("unknown", "refute"), ("no", "unclear"): ("unknown", "unknown"),
                 ("unclear", "unclear"): ("unknown", "unknown"), ("no", "yes"): ("unknown", "unknown")}
        for (m, d), (verdict, mapped) in cases.items():
            reply = {"mentions_entity": m, "describes_predicate": d}
            for task in lab_j2.TASKS:
                line = lab_j2.verdict_line(q, w, reply, None, task)
                with self.subTest(reply=(m, d), task=task):
                    self.assertEqual(sorted(line), sorted(lab_j2.LINE_KEYS))
                    self.assertEqual((line["verdict"], line["mapped_verdict"], line["task"]), (verdict, mapped, task))
                    self.assertEqual(line["verdict"], decide([w], [(w, reply)], 0).verdict)
                    self.assertEqual(line["mapped_verdict"], decide([w], [(w, lab_j2.mapped_reply(reply))], 0).verdict)
                    self.assertEqual({k: line[k] for k in lab_j1.LINE_KEYS}, lab_j1.verdict_line(q, w, reply, None))
        failed = lab_j2.verdict_line(q, w, None, "timeout", N)
        self.assertEqual((failed["verdict"], failed["mapped_verdict"], failed["scored"]), ("unknown", "unknown", False))
        with self.assertRaises(lab_j2.J2Error):
            lab_j2.verdict_line(q, w, None, "timeout", "judge_record_other")


# --------------------------------------------------------------------------------------------------- lines, views

def _q(ref: str, kind: str, predicate: str, part: int = 1) -> dict[str, Any]:
    return {"question_id": f"{ref}:{kind}", "record_ref": ref, "part": part, "kind": kind, "entity_type": "vehicle",
            "entity_id": "V-1", "predicate": predicate, "filed": []}


def _line(q: dict[str, Any], task: str, m: str | None, d: str | None, error_kind: str | None = None
          ) -> dict[str, Any]:
    reply = None if m is None else {"mentions_entity": m, "describes_predicate": d}
    return lab_j2.verdict_line(q, lab_j1.stand_in(q["record_ref"]), reply, error_kind, task)


class LineTests(unittest.TestCase):
    """A8: the j2 line, the j1-shaped views of the four arms and the line check without touching ``lab/j1.py``."""

    def setUp(self) -> None:
        self.qs = [_q("A", "positive", "engine"), _q("A", "negative", "tires"), _q("B", "positive", "seats"),
                   _q("B", "negative", "air_bags")]
        self.lines = [_line(self.qs[0], N, "yes", "yes"), _line(self.qs[1], N, "yes", "unclear"),
                      _line(self.qs[2], N, None, None, "timeout"), _line(self.qs[3], N, "no", "no"),
                      _line(self.qs[0], SHIPPED, "yes", "unclear"), _line(self.qs[1], SHIPPED, "yes", "no"),
                      _line(self.qs[2], SHIPPED, "yes", "yes"), _line(self.qs[3], SHIPPED, "unclear", "unclear")]

    def test_the_views_of_the_four_arms(self) -> None:
        shipped = lab_j2.arm_lines(self.lines, "shipped")
        self.assertEqual(shipped, [{k: line[k] for k in lab_j1.LINE_KEYS} for line in self.lines[4:]])
        negation = lab_j2.arm_lines(self.lines, "negation")
        self.assertEqual(negation, [{k: line[k] for k in lab_j1.LINE_KEYS} for line in self.lines[:4]])
        mapped = lab_j2.arm_lines(self.lines, "negation_mapped")
        self.assertEqual([line["verdict"] for line in mapped], ["confirm", "refute", "unknown", "unknown"])
        self.assertEqual([line["describes_predicate"] for line in mapped], ["yes", "no", None, "no"])
        self.assertEqual([line["mentions_entity"] for line in mapped], ["yes", "yes", None, "no"])
        self.assertEqual([line["verdict"] for line in self.lines[:4]], ["confirm", "unknown", "unknown", "unknown"])
        shipped_mapped = lab_j2.arm_lines(self.lines, "shipped_mapped")
        self.assertEqual([line["verdict"] for line in shipped_mapped], ["refute", "refute", "confirm", "unknown"])
        self.assertEqual([line["describes_predicate"] for line in shipped_mapped], ["no", "no", "yes", "no"])
        for view in (shipped, negation, mapped, shipped_mapped):
            for line in view:
                self.assertEqual(sorted(line), sorted(lab_j1.LINE_KEYS))
        with self.assertRaises(lab_j2.J2Error):
            lab_j2.arm_lines(self.lines, "mapped")
        # the views pass through J001's scoring and pairing unchanged
        score = lab_j1.score(self.qs, negation, bootstrap_b=1000, bootstrap_seed=2)
        self.assertEqual((score["records_scored"], score["records_dropped"]), (1, 1))
        self.assertEqual(score["answers"], {"positive": {"yes_yes": 1}, "negative": {"yes_unclear": 1}})
        mapped_score = lab_j1.score(self.qs, mapped, bootstrap_b=1000, bootstrap_seed=2)
        self.assertEqual(mapped_score["answers"], {"positive": {"yes_yes": 1}, "negative": {"yes_no": 1}})
        self.assertEqual((score["specificity"]["value"], mapped_score["specificity"]["value"]), (0.0, 1.0))
        paired = lab_j1.paired(self.qs, mapped, negation, bootstrap_b=1000, bootstrap_seed=2)
        self.assertEqual((paired["n"], paired["mean_diff"]), (1, 0.5))

    def test_the_line_check(self) -> None:
        for q, line in zip([*self.qs, *self.qs], self.lines):
            self.assertIsNone(lab_j2.line_problem(q, line), line)
        good = self.lines[1]                                   # N, yes/unclear: unknown, mapped refute
        q = self.qs[1]
        self.assertEqual(lab_j2.line_problem(q, {k: v for k, v in good.items() if k != "task"}), "keys")
        self.assertEqual(lab_j2.line_problem(q, {**good, "extra": 1}), "keys")
        self.assertEqual(lab_j2.line_problem(q, "not a line"), "keys")
        self.assertEqual(lab_j2.line_problem(q, {**good, "task": "judge_record_other"}), "task")
        self.assertEqual(lab_j2.line_problem(q, {**good, "verdict": "refute"}), "verdict")
        self.assertEqual(lab_j2.line_problem(q, {**good, "kind": "positive"}), "question")
        self.assertEqual(lab_j2.line_problem(q, {**good, "mentions_entity": "maybe"}), "answers")
        self.assertEqual(lab_j2.line_problem(q, {**good, "mapped_verdict": "unknown"}), "mapped_verdict")
        self.assertEqual(lab_j2.line_problem(q, {**good, "mapped_verdict": "confirm"}), "mapped_verdict")
        failed = self.lines[2]
        self.assertEqual(lab_j2.line_problem(self.qs[2], {**failed, "mapped_verdict": "refute"}), "mapped_verdict")
        # a consistent change of the reply and both verdicts is a line the runner could have written
        self.assertIsNone(lab_j2.line_problem(q, {**good, "describes_predicate": "no", "verdict": "refute",
                                                  "mapped_verdict": "refute"}))
        # the shipped verdict's check is J001's, called on the projected line
        with mock.patch.object(lab_j1, "line_problem", return_value="verdict") as checked:
            self.assertEqual(lab_j2.line_problem(q, good), "verdict")
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
                            "bound": {"value": 0.58, "records": 150, "questions": 300}}
    args.update(changes)
    return lab_j2.verdict_word(**args)


class VerdictWordTests(unittest.TestCase):
    """Rule 10 as A5 and A6 read it: the pass test's four clauses, the words in one order, the readings of rule 9."""

    def test_the_pass_test_s_four_clauses(self) -> None:
        test = lab_j2.pass_test(_scores(0.9, 0.85, 0.95), _paired(0.2, 0.1, 0.3), {"value": 0.58})
        self.assertEqual(test["clauses"], {"balanced_accuracy_at_target": True, "beats_lexical": True,
                                           "beats_bound": True, "unknown_share_reported": True})
        self.assertEqual((test["passed"], test["target"], test["balanced_accuracy"], test["ci_low"], test["ci_high"]),
                         (True, 0.86, 0.9, 0.85, 0.95))
        self.assertAlmostEqual(test["gap_to_target"], -0.04)
        self.assertEqual((test["diff_lexical"], test["diff_lexical_ci_low"], test["diff_lexical_ci_high"],
                          test["bound"], test["unknown_share"]), (0.2, 0.1, 0.3, 0.58, 0.1))
        # the target is reached at 0.860 exactly (BA >= 0.860)
        self.assertTrue(lab_j2.pass_test(_scores(0.86, 0.8, 0.92), _paired(0.2, 0.1, 0.3), {"value": 0.58})["passed"])
        self.assertFalse(lab_j2.pass_test(_scores(0.859, 0.8, 0.92), _paired(0.2, 0.1, 0.3), {"value": 0.58})
                         ["clauses"]["balanced_accuracy_at_target"])
        # the paired lower end must be above 0, not at it
        self.assertFalse(lab_j2.pass_test(_scores(0.9, 0.85, 0.95), _paired(0.1, 0.0, 0.2), {"value": 0.58})
                         ["clauses"]["beats_lexical"])
        # the BA lower end must be above the bound's point value, not at it
        self.assertFalse(lab_j2.pass_test(_scores(0.9, 0.58, 0.95), _paired(0.2, 0.1, 0.3), {"value": 0.58})
                         ["clauses"]["beats_bound"])
        self.assertTrue(lab_j2.pass_test(_scores(0.9, 0.581, 0.95), _paired(0.2, 0.1, 0.3), {"value": 0.58})
                        ["clauses"]["beats_bound"])
        # the unknown share must be a number
        no_unknown = lab_j2.pass_test(_scores(0.9, 0.85, 0.95, unknown=None), _paired(0.2, 0.1, 0.3), {"value": 0.58})
        self.assertEqual((no_unknown["clauses"]["unknown_share_reported"], no_unknown["passed"]), (False, False))
        # missing figures fail their clauses and give null figures, never an error
        empty = lab_j2.pass_test(None, None, None)
        self.assertEqual((empty["passed"], empty["balanced_accuracy"], empty["gap_to_target"], empty["bound"]),
                         (False, None, None, None))
        self.assertEqual(set(empty["clauses"].values()), {False})

    def test_the_words_in_one_order(self) -> None:
        self.assertEqual(_word()[0], "pass_provisional")
        # incomplete first: a part did not finish, not a model measurement, or the dropped share is unknown or high
        self.assertEqual(_word(complete=False)[0], "incomplete")
        self.assertEqual(_word(display_class="plumbing")[0], "incomplete")
        self.assertEqual(_word(display_class="unverified")[0], "incomplete")
        self.assertEqual(_word(measurement=False)[0], "incomplete")
        self.assertEqual(_word(measurement=None)[0], "incomplete")
        self.assertEqual(_word(dropped_share=None)[0], "incomplete")
        self.assertEqual(_word(scores=None)[0], "incomplete")
        self.assertEqual(_word(paired=None)[0], "incomplete")
        self.assertEqual(_word(bound=None)[0], "incomplete")
        # A6: one record of 150 left out keeps the set complete, two withhold it; exactly 0.01 is at most 0.01
        self.assertEqual(_word(dropped_share=1 / 150)[0], "pass_provisional")
        self.assertEqual(_word(dropped_share=0.01)[0], "pass_provisional")
        self.assertEqual(_word(dropped_share=2 / 150)[0], "incomplete")
        # partial: the target reached but an interval clause failed
        self.assertEqual(_word(paired=_paired(0.1, -0.02, 0.2))[0], "partial")
        self.assertEqual(_word(scores=_scores(0.9, 0.57, 0.95))[0], "partial")
        self.assertEqual(_word(scores=_scores(0.9, 0.85, 0.95, unknown=None))[0], "partial")
        # partial: below the target but the lexical judge beaten with the lower end above 0
        self.assertEqual(_word(scores=_scores(0.75, 0.7, 0.8))[0], "partial")
        # failure: below the target and the interval to the lexical judge includes 0 or lies below it
        self.assertEqual(_word(scores=_scores(0.75, 0.7, 0.8), paired=_paired(0.05, -0.02, 0.12))[0], "failure")
        self.assertEqual(_word(scores=_scores(0.5, 0.45, 0.55), paired=_paired(-0.2, -0.3, -0.1))[0], "failure")
        self.assertEqual(_word(scores=_scores(0.75, 0.7, 0.8), paired=_paired(0.0, 0.0, 0.0))[0], "failure")
        # the figures come with the word
        word, test = _word(scores=_scores(0.75, 0.7, 0.8), paired=_paired(0.05, -0.02, 0.12))
        self.assertEqual((word, test["passed"], test["balanced_accuracy"]), ("failure", False, 0.75))
        self.assertAlmostEqual(test["gap_to_target"], 0.11)

    def test_the_lift_readings(self) -> None:
        self.assertEqual(lab_j2.lift_reading(_paired(0.1, 0.01, 0.2)), "helps")
        self.assertEqual(lab_j2.lift_reading(_paired(0.1, 0.0, 0.2)), "not_told_apart")
        self.assertEqual(lab_j2.lift_reading(_paired(-0.1, -0.2, -0.01)), "hurts")
        self.assertEqual(lab_j2.lift_reading(_paired(-0.1, -0.2, 0.0)), "not_told_apart")
        self.assertEqual(lab_j2.lift_reading(_paired(0.0, -0.1, 0.1)), "not_told_apart")
        self.assertIsNone(lab_j2.lift_reading(None))
        self.assertIsNone(lab_j2.lift_reading({"n": 0, "mean_diff": None, "ci_low": None, "ci_high": None}))


# --------------------------------------------------------------------------------------------------- the draw

class ExclusionTests(unittest.TestCase):
    """The data and the draw as amended A1 to A4: the eligible list equal to the pinned build, the draw less the
    excluded refs, both freshness mechanisms on synthetic artifacts, the labels record and the carried-forward file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows, cls.kept = j2labels.nhtsa_rows(ARCHIVE)
        cls.eligible, cls.counts = j2labels.eligible_records(PACK, cls.rows)
        cls.seed1, cls.seed1_record = goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1, fetch=lambda: ARCHIVE)
        cls.seed1_refs = refs_of_labels(cls.seed1)
        cls.seed1_prefix = sha256_hex(cls.seed1)[:12]

    def test_the_eligible_list_equals_the_pinned_build_byte_for_byte(self) -> None:
        """A4: with nothing excluded, ``j2labels``' lines are ``goldlabels.nhtsa_lines``' for the same n and seed."""
        self.assertEqual(self.counts["eligible"], 360)
        self.assertEqual(sum(self.kept.values()), 360)
        self.assertEqual([p[0]["record_ref"] for p in self.eligible],
                         sorted(p[0]["record_ref"] for p in self.eligible))
        for n, seed in ((40, 1), (40, 2), (150, 1), (150, 2)):
            lines, counts = goldlabels.nhtsa_lines(PACK, self.rows, n, seed)
            with self.subTest(n=n, seed=seed):
                self.assertEqual(j2labels.label_lines(j2labels.draw(self.eligible, [], n, seed)), lines)
                self.assertEqual(self.counts, counts)
        data, record = j2labels.build_labels(NHTSA_PACK, 150, 1, fetch=lambda: ARCHIVE, excluded=(),
                                             mechanisms={"artifact": "artifact absent", "rebuild": "verified"})
        self.assertEqual(data, self.seed1)
        for key in ("source", "pack", "pack_version", "vocabulary_hash", "n", "seed", "available", "public"):
            self.assertEqual(record[key], self.seed1_record[key], key)
        self.assertEqual((record["kind"], record["records"], record["claims"], record["sha256"]),
                         ("lab_j2_labels", 150, record["claims"], sha256_hex(data)))
        self.assertEqual(record["exclusion"]["excluded"], 0)
        self.assertEqual(record["exclusion"]["eligible_after_exclusion"], 360)
        self.assertEqual(record["exclusion"]["excluded_sha256"], sha256_hex(j2labels.excluded_jsonl(self.seed1_refs)))

    def test_the_draw_less_the_excluded_refs(self) -> None:
        """A4: the draw is ``random.Random("nhtsa:2").sample`` over the eligible list less the excluded refs, sorted
        by record ref, and holds no excluded ref; too few records left refuse with a fixed message."""
        excluded = self.seed1_refs
        left = [pair for pair in self.eligible if pair[0]["record_ref"] not in excluded]
        self.assertEqual(len(left), 210)
        for n in (40, 150):
            drawn = j2labels.draw(self.eligible, excluded, n, 2)
            expected = sorted(random.Random("nhtsa:2").sample(left, n), key=lambda pair: pair[0]["record_ref"])
            with self.subTest(n=n):
                self.assertEqual(drawn, expected)
                self.assertEqual({r["record_ref"] for r, _ in drawn} & excluded, set())
                self.assertNotEqual(drawn, j2labels.draw(self.eligible, [], n, 2))
                self.assertNotEqual(drawn, j2labels.draw(self.eligible, excluded, n, 1))
        self.assertEqual(j2labels.draw(self.eligible, excluded, 210, 2), left)
        keep = [pair[0]["record_ref"] for pair in self.eligible[-39:]]
        with self.assertRaises(j2labels.J2LabelsError) as refused:
            j2labels.draw(self.eligible, [pair[0]["record_ref"] for pair in self.eligible if pair[0]["record_ref"]
                                          not in keep], 40, 2)
        self.assertEqual(refused.exception.problem, "only 39 eligible unexcluded public records remain: no further "
                                                    "fresh draw of 40 records is pre-registered")
        assert_no_ref(self, refused.exception.problem, {pair[0]["record_ref"] for pair in self.eligible})
        # the draw itself refuses an overlap it could never produce
        data, record = j2labels.build_labels(NHTSA_PACK, 40, 2, fetch=lambda: ARCHIVE, excluded=excluded,
                                             mechanisms={"artifact": "verified", "rebuild": "verified"})
        self.assertEqual(refs_of_labels(data) & excluded, set())
        self.assertEqual(record["exclusion"]["overlap"], 0)
        with self.assertRaises(j2labels.J2LabelsError):
            j2labels.build_labels("device_quality", 40, 2, fetch=lambda: ARCHIVE, excluded=(), mechanisms={})

    def artifact(self, blob: bytes = ARCHIVE, **kwargs: Any) -> tuple[Path, dict[str, Any], Path]:
        """A fresh synthetic earlier plan artifact: (its directory of run ids, the exclude entry, the run's base)."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-j2-earlier-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        stand_in = j2_data.earlier_artifact(tmp, blob, **kwargs)
        return tmp, exclude_of(stand_in), tmp / str(j2_data.RUN_ID)

    @staticmethod
    def restamp(base: Path, rel: str, data: bytes) -> None:
        """Rewrite a file of the artifact and its manifest entry, as a consistent artifact would have it."""
        (base / rel).write_bytes(data)
        manifest = read_json(base / "prereg" / "prereg.json")
        manifest["files"][rel] = {"sha256": sha256_hex(data), "bytes": len(data)}
        (base / "prereg" / "prereg.json").write_bytes(canonical_bytes(manifest) + b"\n")

    def test_mechanism_one_on_a_consistent_artifact(self) -> None:
        """A1: the labels file's bytes hash to the request's prefix, the manifest agrees, ``labels.json`` says nhtsa,
        150, seed 1, and the questions' refs are the labels'; the 150 refs come back and nothing else."""
        directory, entry, base = self.artifact()
        refs, outcome = j2labels.earlier_refs(directory, entry)
        self.assertEqual(outcome, j2labels.VERIFIED)
        self.assertEqual(refs, self.seed1_refs)
        self.assertEqual(len(refs), 150)
        self.assertEqual(refs, {q["record_ref"] for q in lab_j1.read_questions(
            (base / "prereg" / "j1" / "questions.jsonl").read_bytes())})
        self.assertEqual(entry["sha256_prefix"], self.seed1_prefix)
        self.assertEqual(entry, {"run_id": j2_data.RUN_ID, "artifact": f"lab-plan-{j2_data.RUN_ID}-1",
                                 "file": "prereg/j1/labels.jsonl", "sha256_prefix": self.seed1_prefix})
        # a longer prefix, up to the whole sha256, verifies too
        self.assertEqual(j2labels.earlier_refs(directory, {**entry, "sha256_prefix": sha256_hex(self.seed1)})[1],
                         j2labels.VERIFIED)
        self.assertEqual(j2labels.earlier_refs(str(directory), entry), (self.seed1_refs, j2labels.VERIFIED))

    def test_mechanism_one_s_fixed_outcomes(self) -> None:
        """A1: a changed byte, a manifest that disagrees, a questions file with other refs, a labels record of
        another draw, an absent directory and malformed files each fail with one fixed outcome and no refs."""
        directory, entry, base = self.artifact()
        labels_rel, record_rel, questions_rel = ("prereg/j1/labels.jsonl", "prereg/j1/labels.json",
                                                 "prereg/j1/questions.jsonl")
        labels = (base / labels_rel).read_bytes()
        changed = labels.replace(b"stalled", b"stopped", 1)
        self.assertNotEqual(changed, labels)
        questions = read_lines(base / questions_rel)
        other_ref = dict(questions[0], record_ref="999999999")
        record = read_json(base / record_rel)
        cases: dict[str, tuple[Callable[[], None], dict[str, Any], str]] = {
            "no directory": (lambda: None, {"directory": None}, "artifact absent"),
            "another directory": (lambda: None, {"directory": base / "nowhere"}, "artifact absent"),
            "another run id": (lambda: None, {"entry": {**entry, "run_id": entry["run_id"] + 1}}, "artifact absent"),
            "a file the artifact lacks": (lambda: None, {"entry": {**entry, "file": "prereg/j2/excluded.jsonl"}},
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
                lambda: self.restamp(base, questions_rel, jsonl([other_ref, *questions[1:]])), {}, "refs mismatch"),
            "a questions file of fewer records": (
                lambda: self.restamp(base, questions_rel, jsonl(questions[2:])), {}, "refs mismatch"),
            "a record of another seed": (
                lambda: self.restamp(base, record_rel, canonical_bytes({**record, "seed": 2}) + b"\n"), {},
                "labels mismatch"),
            "a record of another n": (
                lambda: self.restamp(base, record_rel, canonical_bytes({**record, "n": 149}) + b"\n"), {},
                "labels mismatch"),
            "a record of another source": (
                lambda: self.restamp(base, record_rel, canonical_bytes({**record, "source": "generator"}) + b"\n"),
                {}, "labels mismatch"),
            "a manifest that is not json": (
                lambda: (base / "prereg" / "prereg.json").write_bytes(b"{not json"), {}, "malformed"),
            "a manifest without files": (
                lambda: (base / "prereg" / "prereg.json").write_bytes(b'{"kind":"lab_prereg"}\n'), {},
                "manifest mismatch"),
            "labels that are not label lines": (
                lambda: self.restamp(base, labels_rel, b'{"record": 1}\n'),
                {"entry": {**entry, "sha256_prefix": sha256_hex(b'{"record": 1}\n')[:12]}}, "malformed"),
            "a record that is not an object": (
                lambda: self.restamp(base, record_rel, b"[]\n"), {}, "labels mismatch"),
            "a manifest file entry without a size": (
                lambda: (base / "prereg" / "prereg.json").write_bytes(canonical_bytes(
                    {"files": {labels_rel: {"sha256": sha256_hex(labels)}}}) + b"\n"), {}, "manifest mismatch"),
        }
        for label, (change, args, want) in cases.items():
            directory, entry, base = self.artifact()
            labels = (base / labels_rel).read_bytes()
            change()
            got = j2labels.earlier_refs(args.get("directory", directory), args.get("entry", entry))
            with self.subTest(case=label):
                self.assertEqual(got, (None, want))
                self.assertIn(want, j2labels.OUTCOMES)
        self.assertEqual(j2labels.OUTCOMES, ("verified", "artifact absent", "sha256 mismatch", "manifest mismatch",
                                             "refs mismatch", "labels mismatch", "malformed"))

    def test_mechanism_one_reads_a_later_cycle_s_excluded_file(self) -> None:
        """A3: the chain's next link is ``prereg/j2/excluded.jsonl``, one ``{"record_ref"}`` line per ref."""
        tmp = Path(tempfile.mkdtemp(prefix="lab-j2-chain-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        run_id = j2_data.RUN_ID + 7
        base = tmp / str(run_id)
        refs = {f"R{i:05d}" for i in range(300)}
        data = j2labels.excluded_jsonl(refs)
        (base / "prereg" / "j2").mkdir(parents=True)
        (base / "prereg" / "j2" / "excluded.jsonl").write_bytes(data)
        manifest = {"schema_version": 1, "kind": "lab_prereg", "files": {
            "prereg/j2/excluded.jsonl": {"sha256": sha256_hex(data), "bytes": len(data)}}}
        (base / "prereg" / "prereg.json").write_bytes(canonical_bytes(manifest) + b"\n")
        entry = {"run_id": run_id, "artifact": f"lab-plan-{run_id}-1", "file": "prereg/j2/excluded.jsonl",
                 "sha256_prefix": sha256_hex(data)[:12]}
        self.assertEqual(j2labels.earlier_refs(tmp, entry), (refs, j2labels.VERIFIED))
        self.assertEqual(data.decode("utf-8").splitlines(), [canonical_dumps({"record_ref": r}) for r in sorted(refs)])
        self.assertEqual(j2labels.excluded_jsonl([*refs, "R00001", "R00002"]), data)
        for bad in (b'{"record_ref": 1}\n', b'{"record_ref": "R1", "narrative": "x"}\n', b'["R1"]\n', b"R1\n"):
            self.restamp(base, "prereg/j2/excluded.jsonl", bad)
            with self.subTest(bad=bad):
                self.assertEqual(j2labels.earlier_refs(tmp, {**entry, "sha256_prefix": sha256_hex(bad)[:12]}),
                                 (None, "malformed"))
        self.restamp(base, "prereg/j2/excluded.jsonl", b"")
        self.assertEqual(j2labels.earlier_refs(tmp, {**entry, "sha256_prefix": sha256_hex(b"")[:12]}),
                         (set(), j2labels.VERIFIED))

    def test_mechanism_two_rebuilds_the_seed_one_draw(self) -> None:
        """A2: the rebuild verifies only when its labels' sha256 starts with the pinned prefix; the sha256 comes
        back either way so that the plan job can print it beside the pin."""
        refs, outcome, sha = j2labels.rebuild_refs(lambda: ARCHIVE, self.seed1_prefix)
        self.assertEqual((refs, outcome, sha), (self.seed1_refs, j2labels.VERIFIED, sha256_hex(self.seed1)))
        self.assertEqual(j2labels.rebuild_refs(lambda: ARCHIVE), (None, "sha256 mismatch", sha256_hex(self.seed1)))
        self.assertEqual(j2labels.rebuild_refs(lambda: ARCHIVE, "0" * 12), (None, "sha256 mismatch", sha))
        self.assertEqual(j2labels.rebuild_refs(lambda: j2_data.archive(5, 3), self.seed1_prefix),
                         (None, "malformed", ""))
        calls: list[int] = []

        def fetch() -> bytes:
            calls.append(1)
            return ARCHIVE
        j2labels.rebuild_refs(fetch, self.seed1_prefix)
        self.assertEqual(len(calls), 1)

    def test_the_labels_record_and_the_file_the_pinned_readers_read(self) -> None:
        """Rule 1 and A4: the record carries ``available`` and the exclusion block (counts and hashes only); the
        file is the shape ``e1_extract.read_labels`` and ``lab.j1`` read, codes removed, data label public."""
        runs = [{"run_id": j2_data.RUN_ID, "artifact": j2_data.ARTIFACT, "file": j2_data.LABELS_FILE,
                 "sha256_prefix": self.seed1_prefix, "refs": 150, "outcome": "verified", "stand_in": False}]
        data, record = j2labels.build_labels(NHTSA_PACK, 40, 2, fetch=lambda: ARCHIVE, excluded=self.seed1_refs,
                                             mechanisms={"artifact": "verified", "rebuild": "sha256 mismatch"},
                                             excluded_runs=runs)
        drawn = refs_of_labels(data)
        self.assertEqual(len(drawn), 40)
        self.assertEqual(record["exclusion"], {
            "excluded": 150, "eligible_after_exclusion": 210, "overlap": 0,
            "mechanisms": {"artifact": "verified", "rebuild": "sha256 mismatch"},
            "complaint_file_sha256": sha256_hex(ARCHIVE), "excluded_runs": runs,
            "excluded_sha256": sha256_hex(j2labels.excluded_jsonl(self.seed1_refs | drawn))})
        self.assertEqual((record["kind"], record["source"], record["pack"], record["n"], record["seed"],
                          record["available"], record["records"], record["claims"] >= 40, record["sha256"]),
                         ("lab_j2_labels", "nhtsa", PACK.id, 40, 2, 360, 40, True, sha256_hex(data)))
        self.assertEqual(sorted(record["public"]), ["complaints", "eligible", "lexical", "makes", "mapped",
                                                    "rejected", "window"])
        assert_no_ref(self, canonical_dumps(record), drawn | self.seed1_refs)
        tmp = Path(tempfile.mkdtemp(prefix="lab-j2-labels-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "labels.jsonl").write_bytes(data)
        labels, sha = e1_extract.read_labels(tmp / "labels.jsonl", PACK, Canonicaliser(PACK))
        self.assertEqual((len(labels), sha), (40, sha256_hex(data)))
        e1_extract.check_data_label(labels, "public")
        for r, gold in lab_j1.parse_labels(data):
            self.assertEqual((r["codes"], r["site"], len(r["entities"]["vehicle"])), ([], goldlabels.PUBLIC_SITE, 1))
            self.assertTrue(gold)
            lab_j1.window_record(r)
        self.assertNotIn("Operator", data.decode("utf-8"))


# --------------------------------------------------------------------------------------------------- request, plan

class RequestTests(unittest.TestCase):
    def blocks(self, block: dict[str, Any], models: list[str] | None = None) -> dict[str, Any]:
        obj = plumbing_min()
        if models is not None:
            obj["models"] = models
        obj["experiments"] = {"j2": block}
        return validate(obj, MANIFEST)["experiments"]["j2"]

    def error(self, block: dict[str, Any], models: list[str] | None = None) -> tuple[str, str]:
        with self.assertRaises(RequestError) as caught:
            self.blocks(block, models)
        return caught.exception.path, caught.exception.problem

    def test_defaults(self) -> None:
        p = _Prereg.get()
        base = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 3, "exclude": p.exclude,
                "primary": {"model": "fake-a", "arm": "shipped_mapped"}}
        self.assertEqual(self.blocks(base), {"models": ["fake-a", "fake-b"], "minutes": 5, "labels": LABELS,
                                             "parts": 2, "seed": 3, "bootstrap_b": 10000, "bootstrap_seed": 1,
                                             "exclude": p.exclude, "primary": {"model": "fake-a",
                                                                               "arm": "shipped_mapped"},
                                             "comparisons_before": 0})
        self.assertEqual(self.blocks({**base, "comparisons_before": 12})["comparisons_before"], 12)
        self.assertIn("j2", EXPERIMENTS)
        self.assertEqual(lab_request._J2_REQUIRED, ("minutes", "labels", "parts", "seed", "exclude", "primary"))

    def test_every_block_error(self) -> None:
        p = _Prereg.get()
        ex = p.exclude
        base = {"minutes": 5, "labels": LABELS, "parts": 2, "seed": 1, "exclude": ex,
                "primary": {"model": "fake-b", "arm": "negation"}}
        path = "$.experiments.j2"
        cases = [
            ({**base, "extra": 1}, None, (f"{path}.extra", "unknown key")),
            ({k: v for k, v in base.items() if k != "exclude"}, None, (f"{path}.exclude", "required")),
            ({k: v for k, v in base.items() if k != "primary"}, None, (f"{path}.primary", "required")),
            ({k: v for k, v in base.items() if k != "parts"}, None, (f"{path}.parts", "required")),
            ({**base, "models": ["fake-a", "h-a"]}, ["fake-a", "h-a"], (f"{path}.models[1]", NHTSA_HOSTED)),
            ({**base, "models": ["nope"]}, None, (f"{path}.models[0]", "not one of $.models")),
            ({**base, "minutes": 21}, None,
             (f"{path}.minutes", "exceeds the shard capacity (job_minutes minus the shard overhead)")),
            ({**base, "labels": {"source": "generator", "pack": "device_quality", "n": 40, "seed": 1}}, None,
             (f"{path}.labels.source", J2_LABELS_PROBLEM)),
            ({**base, "labels": {**LABELS, "pack": "device_quality"}}, None,
             (f"{path}.labels.pack", f"nhtsa labels read the vehicle pack {NHTSA_PACK}")),
            ({**base, "labels": {**LABELS, "n": 39}}, None, (f"{path}.labels.n", "must be an int in [40, 2000]")),
            ({**base, "parts": 31}, None, (f"{path}.parts", "must be an int in [1, 30]")),
            ({**base, "seed": -1}, None, (f"{path}.seed", "must be an int in [0, 2147483647]")),
            ({**base, "bootstrap_b": 999}, None, (f"{path}.bootstrap_b", "must be an int in [1000, 20000]")),
            ({**base, "comparisons_before": -1}, None,
             (f"{path}.comparisons_before", "must be an int in [0, 1000000]")),
            ({**base, "comparisons_before": 1000001}, None,
             (f"{path}.comparisons_before", "must be an int in [0, 1000000]")),
            ({**base, "exclude": "run"}, None, (f"{path}.exclude", "must be an object")),
            ({**base, "exclude": {**ex, "extra": 1}}, None, (f"{path}.exclude.extra", "unknown key")),
            ({**base, "exclude": {k: v for k, v in ex.items() if k != "sha256_prefix"}}, None,
             (f"{path}.exclude.sha256_prefix", "required")),
            ({**base, "exclude": {**ex, "run_id": 0}}, None,
             (f"{path}.exclude.run_id", "must be an int in [1, 100000000000000000000]")),
            ({**base, "exclude": {**ex, "run_id": "1"}}, None,
             (f"{path}.exclude.run_id", "must be an int in [1, 100000000000000000000]")),
            ({**base, "exclude": {**ex, "artifact": f"lab-plan-{ex['run_id'] + 1}-1"}}, None,
             (f"{path}.exclude.artifact", J2_ARTIFACT_PROBLEM)),
            ({**base, "exclude": {**ex, "artifact": f"plan-{ex['run_id']}-1"}}, None,
             (f"{path}.exclude.artifact", J2_ARTIFACT_PROBLEM)),
            ({**base, "exclude": {**ex, "artifact": f"lab-plan-{ex['run_id']}"}}, None,
             (f"{path}.exclude.artifact", J2_ARTIFACT_PROBLEM)),
            ({**base, "exclude": {**ex, "file": "prereg/j1/questions.jsonl"}}, None,
             (f"{path}.exclude.file", J2_FILE_PROBLEM)),
            ({**base, "exclude": {**ex, "file": "../prereg/j1/labels.jsonl"}}, None,
             (f"{path}.exclude.file", J2_FILE_PROBLEM)),
            ({**base, "exclude": {**ex, "sha256_prefix": "ABCDEF123456"}}, None,
             (f"{path}.exclude.sha256_prefix", J2_SHA_PROBLEM)),
            ({**base, "exclude": {**ex, "sha256_prefix": "abcdef12345"}}, None,
             (f"{path}.exclude.sha256_prefix", J2_SHA_PROBLEM)),
            ({**base, "exclude": {**ex, "sha256_prefix": "g" * 12}}, None,
             (f"{path}.exclude.sha256_prefix", J2_SHA_PROBLEM)),
            ({**base, "primary": {"model": "fake-slow", "arm": "negation"}}, None,
             (f"{path}.primary.model", J2_PRIMARY_MODEL_PROBLEM)),
            ({**base, "models": ["fake-a"], "primary": {"model": "fake-b", "arm": "negation"}}, None,
             (f"{path}.primary.model", J2_PRIMARY_MODEL_PROBLEM)),
            ({**base, "primary": {"model": "fake-b", "arm": "mapped"}}, None,
             (f"{path}.primary.arm", J2_PRIMARY_ARM_PROBLEM)),
            ({**base, "primary": {"model": "fake-b"}}, None, (f"{path}.primary.arm", "required")),
            ({**base, "primary": {"model": "fake-b", "arm": "negation", "extra": 1}}, None,
             (f"{path}.primary.extra", "unknown key")),
        ]
        for block, models, want in cases:
            with self.subTest(case=want):
                self.assertEqual(self.error(block, models), want)

    def test_the_template_gives_fifteen_units_in_fifteen_shards(self) -> None:
        """The settings table: 15 units of 300 minutes, one per shard, job_minutes 330, max_parallel 15, the
        excluded run named, P = (a-4b, negation), seeds 2, parts 5, B 10,000."""
        manifest = load_manifest(LAB_MANIFEST)
        request = load_request(TEMPLATES / "accuracy.json", manifest)
        self.assertIn("lab/requests/accuracy-001.json", request.data["purpose"])
        self.assertEqual((request.data["job_minutes"], request.data["max_parallel"], request.data["retention_days"],
                          request.data["provider"], request.data["models"]),
                         (330, 15, 30, "llama-server", ["a-0p5b", "a-1p5b", "a-4b"]))
        plan = build_plan(request, manifest, "unknown")
        self.assertEqual((len(plan["units"]), len(plan["shards"]), plan["max_parallel"]), (15, 15, 15))
        self.assertEqual({len(s["units"]) for s in plan["shards"]}, {1})
        self.assertEqual({(s["planned_minutes"], s["timeout_minutes"]) for s in plan["shards"]}, {(300, 325)})
        unit = next(u for u in plan["units"] if u["unit"] == "j2-a-4b-p5")
        self.assertEqual(unit["params"], {
            "pack": NHTSA_PACK, "labels": {**LABELS, "n": 150}, "endpoint": "a-4b", "part": 5, "parts": 5, "seed": 2,
            "bootstrap_b": 10000, "bootstrap_seed": 2, "exclude": J001_EXCLUDE,
            "primary": {"model": "a-4b", "arm": "negation"}, "comparisons_before": 0})
        self.assertEqual((unit["minutes"], unit["seeds"], unit["kind"]), (300, [2], "gguf"))
        self.assertEqual({u["params"]["exclude"]["run_id"] for u in plan["units"]}, {37996675559})
        outputs = lab_plan.plan_outputs(plan, "0" * 64)
        self.assertEqual((outputs["j2_exclude_artifact"], outputs["j2_exclude_run_id"], outputs["has_units"]),
                         ("lab-plan-37996675559-1", "37996675559", "true"))
        self.assertEqual(list(outputs), list(lab_plan.OUTPUT_KEYS))
        self.assertEqual(lab_plan.j2_exclude(plan), J001_EXCLUDE)
        self.assertIsNone(lab_plan.j2_exclude(build_plan(load_request(ROOT / "lab" / "requests" / "plumbing-001.json",
                                                                      manifest), manifest, "unknown")))
        self.assertIsNone(lab_plan.j2_exclude(None))
        self.assertEqual((lab_plan.plan_outputs(None, "")["j2_exclude_artifact"],
                          lab_plan.plan_outputs(None, "")["j2_exclude_run_id"]), ("", ""))


# --------------------------------------------------------------------------------------------------- preregistration

class PreregTests(unittest.TestCase):
    def test_the_preregistration(self) -> None:
        p = _Prereg.get()
        m = p.manifest["j2"]
        self.assertEqual(sorted(path.name for path in p.dir.iterdir()),
                         ["excluded.jsonl", "labels.json", "labels.jsonl", "lexical.json", "lexical.jsonl",
                          "prereg.json", "prior.json", "prior.jsonl", "questions.json", "questions.jsonl",
                          "routing.json"])
        doc = read_json(p.dir / "prereg.json")
        self.assertEqual(sorted(doc), sorted(lab_j2.PREREG_KEYS))
        self.assertEqual((doc["kind"], doc["experiment"], doc["boundary"], doc["data_label"], doc["parts"],
                          doc["seed"], doc["bootstrap_b"], doc["bootstrap_seed"], doc["withhold_share"]),
                         ("lab_j2_prereg", "J2", "site:lab", "public", 2, 2, 1000, 2, 0.01))
        self.assertEqual(doc["labels"]["sha256"], hashlib.sha256((p.dir / "labels.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["labels"]["available"], 360)
        self.assertEqual((doc["labels"]["source"], doc["labels"]["n"], doc["labels"]["seed"], doc["labels"]["records"]),
                         ("nhtsa", 40, 2, 40))
        self.assertEqual(doc["questions"]["sha256"],
                         hashlib.sha256((p.dir / "questions.jsonl").read_bytes()).hexdigest())
        self.assertEqual((doc["questions"]["records"], doc["questions"]["questions"], doc["questions"]["parts"],
                          doc["questions"]["seed"], doc["questions"]["negative_draw"]),
                         (40, 80, 2, 2, "other_records_positive_frequency"))
        self.assertEqual(doc["lexical"]["sha256"], hashlib.sha256((p.dir / "lexical.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["prior"], {"sha256": hashlib.sha256((p.dir / "prior.jsonl").read_bytes()).hexdigest(),
                                        "rule": lab_j1.PRIOR_RULE, "top": list(lab_j1.PRIOR_TOP),
                                        "source": lab_j1.PRIOR_SOURCE})
        self.assertEqual(doc["tasks"], lab_j2.task_pins())
        self.assertEqual(doc["tasks"]["order"], [N, SHIPPED])
        self.assertEqual(doc["mapping"], lab_j2.mapping_pin())
        self.assertEqual(doc["arms"], lab_j2.arms_doc())
        self.assertEqual((doc["primary"], doc["headline_arm"]), ({"model": "fake-b", "arm": "negation"}, "negation"))
        self.assertEqual((doc["comparisons_before"], doc["target_balanced_accuracy"]), (0, 0.86))
        self.assertEqual(sorted(doc["exclusion"]), sorted(lab_j2.EXCLUSION_KEYS))
        self.assertEqual((doc["exclusion"]["excluded"], doc["exclusion"]["eligible_after_exclusion"],
                          doc["exclusion"]["overlap"], doc["exclusion"]["mechanisms"]),
                         (150, 210, 0, {"artifact": "verified", "rebuild": "sha256 mismatch"}))
        self.assertEqual(doc["exclusion"]["complaint_file_sha256"], sha256_hex(ARCHIVE))
        self.assertEqual(doc["code_hash"], lab_j2.code_hash())
        for name in ("mycelic/collective/edge/verify.py", "mycelic/collective/inference/runtime.py", "lab/j1.py",
                     "lab/j2.py", "lab/j2labels.py"):
            self.assertIn(name, doc["code_files"])
        self.assertEqual([e["name"] for e in doc["endpoints"]], ["fake-a", "fake-b"])
        self.assertEqual({e["boundary"] for e in doc["endpoints"]}, {"site:lab"})
        self.assertEqual((doc["twins"], doc["excluded"]), ([list(t) for t in lab_j1.TWINS], list(lab_j1.EXCLUDED)))
        routing = read_json(p.dir / "routing.json")
        self.assertEqual((sorted(routing["endpoints"]), routing["routes"]), (["fake-a", "fake-b"], {}))
        # the comparators are J001's code on the fresh draw (rule 6)
        qs = lab_j1.read_questions((p.dir / "questions.jsonl").read_bytes())
        lexical = read_lines(p.dir / "lexical.jsonl")
        self.assertEqual(lexical, lab_j1.lexical_verdicts(PACK, (p.dir / "labels.jsonl").read_bytes(), qs))
        self.assertEqual(m["lexical"], lab_j1.score(qs, lexical, bootstrap_b=1000, bootstrap_seed=2))
        self.assertEqual(m["lexical"], read_json(p.dir / "lexical.json"))
        self.assertEqual(m["prior"], lab_j1.score(qs, read_lines(p.dir / "prior.jsonl"), bootstrap_b=1000,
                                                  bootstrap_seed=2))
        self.assertEqual(m["prior_bound"], lab_j1.prior_bound(qs))
        self.assertEqual((m["endpoints"], m["prereg"], m["parts"], m["seed"], m["stand_in"]),
                         (["fake-a", "fake-b"], "prereg/j2/prereg.json", 2, 2, False))
        self.assertEqual((m["primary"], m["headline_arm"], m["comparisons_before"], m["target_balanced_accuracy"]),
                         ({"model": "fake-b", "arm": "negation"}, "negation", 0, 0.86))
        self.assertEqual((m["tasks"], m["mapping"], m["arms"]), (doc["tasks"], doc["mapping"], doc["arms"]))
        self.assertEqual(sorted(m["exclusion"]), sorted([*lab_j2.EXCLUSION_KEYS, "rebuilt_sha256",
                                                         "rebuild_expected_prefix", "symmetric_difference",
                                                         "stand_in"]))
        lab_prereg.load_prereg(p.plan_path)

    def test_the_draw_excludes_the_earlier_run_s_records(self) -> None:
        """A1 to A4 end to end: the earlier artifact's 150 refs are out of the draw; the chain file holds every
        excluded ref and the draw's; the record names the excluded run with counts and hashes only."""
        p = _Prereg.get()
        self.assertEqual(len(p.excluded), 150)
        self.assertEqual(len(p.drawn), 40)
        self.assertEqual(p.drawn & p.excluded, set())
        excluded_lines = read_lines(p.dir / "excluded.jsonl")
        self.assertEqual(len(excluded_lines), 190)
        self.assertEqual({line["record_ref"] for line in excluded_lines}, p.excluded | p.drawn)
        self.assertEqual((p.dir / "excluded.jsonl").read_bytes(), j2labels.excluded_jsonl(p.excluded | p.drawn))
        labels = read_json(p.dir / "labels.json")
        ex = labels["exclusion"]
        self.assertEqual((labels["kind"], labels["n"], labels["seed"], labels["available"]),
                         ("lab_j2_labels", 40, 2, 360))
        self.assertEqual(ex["excluded_runs"], [{
            "run_id": j2_data.RUN_ID, "artifact": j2_data.ARTIFACT, "file": "prereg/j1/labels.jsonl",
            "sha256_prefix": p.stand_in["sha256_prefix"], "refs": 150, "outcome": "verified", "stand_in": False,
            "requested": p.exclude}])
        self.assertEqual((ex["excluded"], ex["eligible_after_exclusion"], ex["overlap"], ex["stand_in"],
                          ex["symmetric_difference"], ex["rebuild_expected_prefix"]),
                         (150, 210, 0, False, None, "bc6d092d8bca"))
        self.assertEqual(ex["mechanisms"], {"artifact": "verified", "rebuild": "sha256 mismatch"})
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", ex["rebuilt_sha256"]))
        self.assertFalse(ex["rebuilt_sha256"].startswith("bc6d092d8bca"))
        self.assertEqual(ex["excluded_sha256"], sha256_hex((p.dir / "excluded.jsonl").read_bytes()))
        self.assertEqual(p.manifest["j2"]["excluded_sha256"], ex["excluded_sha256"])
        self.assertEqual(p.manifest["j2"]["excluded_runs"], ex["excluded_runs"])
        for rel in ("labels.json", "prereg.json", "lexical.json", "prior.json", "questions.json"):
            assert_no_ref(self, (p.dir / rel).read_text(encoding="utf-8"), p.drawn | p.excluded)
        assert_no_ref(self, canonical_dumps(p.manifest), p.drawn | p.excluded)
        # the rule's fixed console lines (The data and the draw, item 5; rule 13), and never a ref
        lines = p.stdout.splitlines()
        self.assertIn(f"j2 exclusion: mechanism rebuild sha256 mismatch: rebuilt seed-one labels sha256 "
                      f"{ex['rebuilt_sha256'][:12]} against the pinned prefix bc6d092d8bca", lines)
        self.assertIn(f"j2 draw: available 360 excluded 150 eligible_after_exclusion 210 overlap 0 mechanisms "
                      f"artifact=verified rebuild=sha256 mismatch labels_sha256 {labels['sha256'][:12]} "
                      f"excluded_sha256 {ex['excluded_sha256'][:12]} complaint_file_sha256 "
                      f"{sha256_hex(ARCHIVE)[:12]}", lines)
        lexical = p.manifest["j2"]["lexical"]["balanced_accuracy"]
        self.assertIn(f"j2 comparators: lexical balanced_accuracy {lexical['value']:.3f} [{lexical['ci_low']:.3f}, "
                      f"{lexical['ci_high']:.3f}] record_blind", "\n".join(lines))
        self.assertTrue(any(line.startswith("j2 comparators:") and line.rstrip().endswith(
            f"predicate_only_bound {p.manifest['j2']['prior_bound']['value']:.3f}") for line in lines))
        assert_no_ref(self, p.stdout, p.drawn | p.excluded)
        self.assertNotIn("Operator", p.stdout)

    def test_the_plan_summary_shows_the_preregistration(self) -> None:
        p = _Prereg.get()
        md, sources = lab_summary.render_plan(p.tmp / "plan")
        self.assertIn("`J2` labels: source `nhtsa`, records 40", md)
        self.assertIn("eligible records 360; excluded records 150; eligible after exclusion 210; overlap with the "
                      "excluded set 0; artifact mechanism `verified`; rebuild mechanism `sha256 mismatch`; "
                      "excluded refs file sha `", md)
        self.assertIn("`J2` primary pair: `fake-b` `negation`; target balanced accuracy 0.860", md)
        self.assertIn("`J2` lexical judge, every record: balanced accuracy", md)
        self.assertIn("`J2` record-blind control, every record: balanced accuracy", md)
        self.assertIn(f"`J2` predicate-only bound, every record: {p.manifest['j2']['prior_bound']['value']:.3f}", md)
        self.assertIn(J2_EXCLUSION_NOTE, md)
        self.assertNotIn(J2_STAND_IN, md)
        assert_no_ref(self, md, p.drawn | p.excluded)
        check_sources(self, md, sources, p.tmp / "plan")

    def plan_dir(self, name: str, **block_changes: Any) -> Path:
        """A fresh plan of the fixture's request (with ``block_changes`` to its j2 block) under a new directory."""
        p = _Prereg.get()
        tmp = Path(tempfile.mkdtemp(prefix=f"lab-j2-{name}-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        request = json.loads(json.dumps(p.request))
        request["experiments"]["j2"].update(block_changes)
        path = write_json(tmp / "j2-test.json", request)
        code, _, err = run_plan(["--request", str(path), "--manifest", str(MANIFEST_TEST), "--out", str(tmp / "plan")])
        self.assertEqual(code, 0, err)
        (tmp / "complaints.zip").write_bytes(ARCHIVE)
        return tmp

    def prereg(self, tmp: Path, *extra: str) -> tuple[int, str, str]:
        return call_main(lab_prereg, ["--plan", str(tmp / "plan" / "plan.json"),
                                      "--nhtsa-archive", str(tmp / "complaints.zip"), *extra])

    def test_neither_mechanism_verifying_stops_the_plan_job(self) -> None:
        """A2: the earlier artifact absent and the rebuilt sha256 not the pinned one: no draw, a plan error."""
        tmp = self.plan_dir("unverified")
        (tmp / "nothing").mkdir()
        code, out, err = self.prereg(tmp, "--earlier-plans", str(tmp / "nothing"))
        self.assertEqual(code, 2)
        self.assertIn("j2 exclusion: mechanism artifact artifact absent", out.splitlines())
        self.assertTrue(any(line.startswith("j2 exclusion: mechanism rebuild sha256 mismatch: rebuilt seed-one "
                                            "labels sha256 ") for line in out.splitlines()))
        self.assertFalse(any(line.startswith("j2 draw:") for line in out.splitlines()))
        self.assertTrue(err.startswith(f"error: $.experiments.j2.exclude: {J2_FRESHNESS_UNVERIFIED}"), err)
        error = read_json(tmp / "plan" / "plan-error.json")
        self.assertEqual((error["path"], error["problem"]), ("$.experiments.j2.exclude", J2_FRESHNESS_UNVERIFIED))
        self.assertFalse((tmp / "plan" / "prereg" / "j2").exists())
        # the default directory is lab-earlier beside the plan's directory, never under it
        self.assertEqual(lab_prereg.earlier_dir(tmp / "plan"), tmp / "lab-earlier")

    def test_two_verifying_mechanisms_that_disagree_stop_the_plan_job(self) -> None:
        """A2: an artifact of other records whose bytes match the request, and a rebuild that matches its own pin:
        both verify, their sets differ, the symmetric difference is printed and the plan job stops."""
        tmp = self.plan_dir("disagree")
        other = j2_data.archive(60, 2)                            # other record ids, same shape
        stand_in = j2_data.earlier_artifact(tmp / "earlier", other)
        seed1 = goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1, fetch=lambda: ARCHIVE)[0]
        write_json(tmp / "stand-in.json", {**stand_in, "rebuild_sha256_prefix": sha256_hex(seed1)[:12]})
        code, out, err = self.prereg(tmp, "--earlier-plans", str(tmp / "earlier"),
                                     "--exclude-stand-in", str(tmp / "stand-in.json"))
        self.assertEqual(code, 2)
        self.assertIn("j2 exclusion: both mechanisms verified; symmetric difference 300", out.splitlines())
        self.assertTrue(err.startswith(f"error: $.experiments.j2.exclude: {J2_EXCLUSION_DISAGREES}"), err)
        self.assertEqual(read_json(tmp / "plan" / "plan-error.json")["problem"], J2_EXCLUSION_DISAGREES)
        assert_no_ref(self, out, refs_of_labels(seed1) | refs_of_labels(
            (tmp / "earlier" / str(j2_data.RUN_ID) / "prereg" / "j1" / "labels.jsonl").read_bytes()))

    def test_too_few_records_left_stop_the_plan_job(self) -> None:
        """The data and the draw, item 5: fewer eligible unexcluded records than the draw needs."""
        tmp = self.plan_dir("toofew", labels={**LABELS, "n": 300})
        code, out, err = self.prereg(tmp, "--earlier-plans", str(_Prereg.get().earlier))
        self.assertEqual(code, 2)
        self.assertIn("j2 draw: only 210 eligible unexcluded public records remain: no further fresh draw of 300 "
                      "records is pre-registered", out.splitlines())
        self.assertTrue(err.startswith(f"error: $.experiments.j2.labels.n: {J2_TOO_FEW_RECORDS}"), err)
        self.assertEqual(read_json(tmp / "plan" / "plan-error.json")["problem"], J2_TOO_FEW_RECORDS)

    def test_the_stand_in_is_recorded(self) -> None:
        """Tests only: ``--exclude-stand-in`` replaces the request's exclude entry for the checks and marks the
        preregistration; both mechanisms verify on the test data and agree."""
        tmp = self.plan_dir("standin")
        data = j2_data.write_data(tmp / "data")
        code, out, err = self.prereg(tmp, "--earlier-plans", str(data / "earlier"),
                                     "--exclude-stand-in", str(data / "stand-in.json"))
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertIn("j2 exclusion: both mechanisms verified; symmetric difference 0", lines)
        self.assertTrue(any(line.startswith("j2 draw: available 360 excluded 150 eligible_after_exclusion 210 overlap "
                                            "0 mechanisms artifact=verified rebuild=verified ") for line in lines))
        self.assertEqual(lines[-1], "prereg: e1 no x1 no e2 no j1 no j2 yes")
        manifest = read_json(tmp / "plan" / "prereg" / "prereg.json")
        j2 = manifest["j2"]
        self.assertTrue(j2["stand_in"])
        self.assertEqual((j2["exclusion"]["stand_in"], j2["exclusion"]["symmetric_difference"],
                          j2["exclusion"]["mechanisms"]), (True, 0, {"artifact": "verified", "rebuild": "verified"}))
        run = j2["excluded_runs"][0]
        self.assertEqual((run["stand_in"], run["requested"], run["refs"], run["outcome"]),
                         (True, _Prereg.get().exclude, 150, "verified"))
        self.assertEqual(run["sha256_prefix"], j2["exclusion"]["rebuilt_sha256"][:12])
        # the same draw as without the stand-in: the excluded set is the same 150 refs
        self.assertEqual((tmp / "plan" / "prereg" / "j2" / "labels.jsonl").read_bytes(),
                         (_Prereg.get().dir / "labels.jsonl").read_bytes())
        md, sources = lab_summary.render_plan(tmp / "plan")
        self.assertIn(J2_STAND_IN, md)
        self.assertIn("artifact mechanism `verified`; rebuild mechanism `verified`", md)
        check_sources(self, md, sources, tmp / "plan")

    def test_a_stand_in_that_cannot_be_read_is_refused(self) -> None:
        tmp = self.plan_dir("badstandin")
        (tmp / "stand-in.json").write_text("{not json", encoding="utf-8")
        code, _, err = self.prereg(tmp, "--exclude-stand-in", str(tmp / "stand-in.json"))
        self.assertEqual((code, err.strip()), (2, "error: the exclusion stand-in cannot be read"))
        for bad in ({**J001_EXCLUDE, "rebuild_sha256_prefix": "XYZ"}, {**J001_EXCLUDE, "file": "x"}, [1], {}):
            write_json(tmp / "stand-in.json", bad)
            with self.subTest(bad=bad):
                self.assertEqual(self.prereg(tmp, "--exclude-stand-in", str(tmp / "stand-in.json"))[0], 2)
        with self.assertRaises(RequestError):
            lab_prereg.load_stand_in(tmp / "stand-in.json")
        write_json(tmp / "stand-in.json", {**J001_EXCLUDE, "rebuild_sha256_prefix": "a" * 12})
        self.assertEqual(lab_prereg.load_stand_in(tmp / "stand-in.json"),
                         {**J001_EXCLUDE, "rebuild_sha256_prefix": "a" * 12})
        self.assertFalse((tmp / "plan" / "prereg").exists())


# --------------------------------------------------------------------------------------------------- the runner

class _Scripted:
    """The lab's responder, except for the payloads of chosen questions: ``invalid`` ones get a reply the schema
    refuses (so the repair fails too) and ``drop`` ones a dropped connection; ``by_task`` maps a task name to the
    reply every question of that task gets instead."""

    def __init__(self, invalid: list[dict[str, Any]] = (), drop: list[dict[str, Any]] = (),
                 by_task: dict[str, dict[str, str]] | None = None) -> None:
        self.base = Responder(PACK)
        self.invalid = {canonical_bytes(p) for p in invalid}
        self.drop = {canonical_bytes(p) for p in drop}
        self.by_task = dict(by_task or {})
        self.tasks: list[str] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        name = request["response_format"]["json_schema"]["name"]
        self.tasks.append(name)
        key = canonical_bytes(request_payload(request))
        if key in self.drop:
            raise RuntimeError("dropped")
        if key in self.invalid:
            return {"mentions_entity": "perhaps", "describes_predicate": "yes"}
        if name in self.by_task:
            return dict(self.by_task[name])
        return self.base(request)


class RunTests(unittest.TestCase):
    """``lab.j2 run`` in this process against the collective's fake server answered by the lab's responder."""

    def setUp(self) -> None:
        self.p = _Prereg.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j2-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.qs = [q for q in lab_j1.read_questions((self.p.dir / "questions.jsonl").read_bytes()) if q["part"] == 1]
        self.recs = {r["record_ref"]: r for r, _ in lab_j1.parse_labels((self.p.dir / "labels.jsonl").read_bytes())}
        self.lexical = {line["question_id"]: line for line in read_lines(self.p.dir / "lexical.jsonl")}

    def server(self, responder: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> FakeOpenAIServer:
        server = FakeOpenAIServer("valid", responder=responder or Responder(PACK)).start()
        self.addCleanup(server.stop)
        return server

    def routing(self, base_url: str, model: str = "fake-a", change: Callable[[dict[str, Any]], None] | None = None
                ) -> Path:
        doc = lab_j2.routing_doc(self.p.plan["models"], [model], base_url)
        if change is not None:
            change(doc)
        return write_json(self.tmp / f"routing-{model}.json", doc)

    def argv(self, routing: Path, *, endpoint: str = "fake-a", part: int = 1, run_id: str = "r1",
             budget: int = 600, **files: Path) -> list[str]:
        d = self.p.dir
        return ["run", f"--prereg={files.get('prereg', d / 'prereg.json')}",
                f"--labels={files.get('labels', d / 'labels.jsonl')}",
                f"--questions={files.get('questions', d / 'questions.jsonl')}", f"--routing={routing}",
                f"--endpoint={endpoint}", f"--part={part}", f"--run-id={run_id}", f"--runs-dir={self.tmp / 'runs'}",
                f"--budget-seconds={budget}"]

    def run_j2(self, argv: list[str]) -> tuple[int, str, str, Path]:
        code, out, err = call_main(lab_j2, argv)
        return code, out, err, self.tmp / "runs" / "j2" / argv[7].split("=", 1)[1]

    def read(self, run: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return read_json(run / "run.json"), read_lines(run / "verdicts.jsonl")

    def stopped_run(self, ticks: list[float], budget: int = 1) -> tuple[int, dict[str, Any], list[dict[str, Any]],
                                                                       FakeOpenAIServer]:
        """A run whose budget check is driven by the test's clock (no real timer is armed)."""
        server = self.server()
        clock = iter(ticks)
        with mock.patch.object(lab_j2, "_clock", lambda: next(clock)), \
                mock.patch.object(lab_j1.signal, "setitimer"):
            code, _, err, run = self.run_j2(self.argv(self.routing(server.base_url), budget=budget))
        doc, lines = self.read(run)
        return code, doc, lines, server

    def test_two_passes_task_n_first(self) -> None:
        """A9 and rule 3: every question with task N, then every question with the shipped task; the same payload,
        the same schema and max_tokens; both verdicts by the pinned rule; the fake answers as the lexical judge."""
        server = self.server()
        code, out, err, run = self.run_j2(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        n = len(self.qs)
        self.assertEqual(n, 40)
        self.assertEqual(len(lines), 2 * n)
        self.assertEqual([line["task"] for line in lines], [N] * n + [SHIPPED] * n)
        self.assertEqual([line["question_id"] for line in lines], [q["question_id"] for q in self.qs] * 2)
        for line in lines:
            with self.subTest(question=line["question_id"], task=line["task"]):
                self.assertEqual({k: line[k] for k in lab_j1.LINE_KEYS}, self.lexical[line["question_id"]])
                self.assertEqual(line["mapped_verdict"], line["verdict"])      # the lexical judge never says unclear
                self.assertEqual(sorted(line), sorted(lab_j2.LINE_KEYS))
        self.assertEqual((doc["kind"], doc["experiment"], doc["complete"], doc["stopped"], doc["stopped_task"],
                          doc["measurement"], doc["listed_fake"]),
                         ("lab_j2_run", "J2", True, None, None, False, True))
        self.assertEqual(doc["complete_by_task"], {N: True, SHIPPED: True})
        self.assertEqual((doc["pass_order"], doc["questions_planned"], doc["questions_done"], doc["part"],
                          doc["parts"]), ([N, SHIPPED], 80, 80, 1, 2))
        self.assertEqual((doc["questions_planned_by_task"], doc["questions_done_by_task"]),
                         ({N: 40, SHIPPED: 40}, {N: 40, SHIPPED: 40}))
        self.assertEqual(doc["verdicts_sha256"], hashlib.sha256((run / "verdicts.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["ledger_sha256"], hashlib.sha256((run / "ledger.jsonl").read_bytes()).hexdigest())
        self.assertEqual(doc["prereg_sha256"], hashlib.sha256((self.p.dir / "prereg.json").read_bytes()).hexdigest())
        self.assertEqual((doc["pinned"]["tasks"], doc["pinned"]["mapping"]), (lab_j2.task_pins(), lab_j2.mapping_pin()))
        self.assertEqual(sorted(doc["failures_by_task"]), sorted(lab_j2.TASKS))
        self.assertEqual(sorted(doc["latency_ms_by_task"]), sorted(lab_j2.TASKS))
        self.assertEqual(doc["failures"], {"model": {"questions": 0, "by_kind": {}},
                                           "transport": {"questions": 0, "by_kind": {}}})
        rows = read_ledger(run / "ledger.jsonl")
        self.assertEqual(len(rows), 80)
        self.assertEqual([r["task"] for r in rows], [N] * n + [SHIPPED] * n)
        self.assertEqual({(r["boundary"], r["endpoint_boundary"], r["boundary_mode"], r["data_label"]) for r in rows},
                         {("site:lab", "site:lab", "own", "public")})
        self.assertEqual((rows[0]["ref"], rows[n]["ref"]), ("j2-1-0-0000", "j2-1-1-0000"))
        self.assertTrue(all(r["fake_marker"] for r in rows))
        chat = server.chat_requests
        self.assertEqual(len(chat), 80)
        self.assertEqual([c["json"]["response_format"]["json_schema"]["name"] for c in chat], [N] * n + [SHIPPED] * n)
        self.assertEqual({(c["json"]["max_tokens"], c["json"]["temperature"]) for c in chat}, {(256, 0)})
        first, later = chat[0]["json"], chat[n]["json"]
        self.assertIn(lab_j2.NEGATION_INSTRUCTIONS, first["messages"][0]["content"])
        self.assertNotIn(judge_task().instructions, first["messages"][0]["content"])
        self.assertIn(judge_task().instructions, later["messages"][0]["content"])
        payload = lab_j1.payload(PACK, self.qs[0], self.recs[self.qs[0]["record_ref"]])
        self.assertEqual((request_payload(first), request_payload(later)), (payload, payload))
        self.assertEqual(payload["record"]["codes"], [])
        self.assertEqual(first["response_format"]["json_schema"]["schema"],
                         later["response_format"]["json_schema"]["schema"])
        self.assertNotIn("narrative", (run / "verdicts.jsonl").read_text(encoding="utf-8"))
        self.assertIn("complete=true", out)
        self.assertIn("negation=40/40 shipped=40/40", out)
        self.assertEqual(units.harness_status("j2", code, False, doc), ("ok", None))
        record, problem = units.participation("j2", rows, [N], doc)
        self.assertEqual((record["tasks"][N]["attempted"], record["tasks"][SHIPPED]["attempted"], problem),
                         (40, 40, None))

    def test_the_two_arms_of_a_reply_that_differs_by_task(self) -> None:
        """Arms C and D read task N's reply, A and B the shipped one: an unclear component answer is unknown under
        the shipped rule and refute under the mapping (rule 3, arm B)."""
        scripted = _Scripted(by_task={N: {"mentions_entity": "yes", "describes_predicate": "unclear"}})
        server = self.server(scripted)
        code, _, err, run = self.run_j2(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        negation = [line for line in lines if line["task"] == N]
        shipped = [line for line in lines if line["task"] == SHIPPED]
        self.assertEqual({(line["verdict"], line["mapped_verdict"]) for line in negation}, {("unknown", "refute")})
        self.assertEqual([{k: line[k] for k in lab_j1.LINE_KEYS} for line in shipped],
                         [self.lexical[q["question_id"]] for q in self.qs])
        self.assertEqual(scripted.tasks, [N] * 40 + [SHIPPED] * 40)
        c, d = lab_j2.arm_lines(lines, "negation"), lab_j2.arm_lines(lines, "negation_mapped")
        score_c = lab_j1.score(self.qs, c, bootstrap_b=1000, bootstrap_seed=2)
        score_d = lab_j1.score(self.qs, d, bootstrap_b=1000, bootstrap_seed=2)
        self.assertEqual((score_c["balanced_accuracy"]["value"], score_c["unknown_share"]["value"]), (0.0, 1.0))
        self.assertEqual((score_d["sensitivity"]["value"], score_d["specificity"]["value"],
                          score_d["balanced_accuracy"]["value"], score_d["unknown_share"]["value"]),
                         (0.0, 1.0, 0.5, 0.0))
        self.assertEqual((score_c["answers"]["negative"], score_d["answers"]["negative"]),
                         ({"yes_unclear": 20}, {"yes_no": 20}))
        self.assertTrue(doc["complete"])

    def test_a_model_failure_is_unknown_and_a_transport_failure_is_not_scored_in_each_pass(self) -> None:
        bad, gone = self.qs[3], self.qs[6]
        scripted = _Scripted(invalid=[lab_j1.payload(PACK, bad, self.recs[bad["record_ref"]])],
                             drop=[lab_j1.payload(PACK, gone, self.recs[gone["record_ref"]])])
        server = self.server(scripted)
        code, _, err, run = self.run_j2(self.argv(self.routing(server.base_url)))
        self.assertEqual(code, 0, err)
        doc, lines = self.read(run)
        for task in lab_j2.TASKS:
            by_id = {line["question_id"]: line for line in lines if line["task"] == task}
            with self.subTest(task=task):
                self.assertEqual((by_id[bad["question_id"]]["verdict"], by_id[bad["question_id"]]["mapped_verdict"],
                                  by_id[bad["question_id"]]["error_kind"], by_id[bad["question_id"]]["scored"]),
                                 ("unknown", "unknown", "schema_invalid", True))
                self.assertEqual((by_id[gone["question_id"]]["error_kind"], by_id[gone["question_id"]]["scored"],
                                  by_id[gone["question_id"]]["verdict"], by_id[gone["question_id"]]["mapped_verdict"]),
                                 ("network", False, "unknown", "unknown"))
                self.assertEqual(doc["failures_by_task"][task],
                                 {"model": {"questions": 1, "by_kind": {"schema_invalid": 1}},
                                  "transport": {"questions": 1, "by_kind": {"network": 1}}})
        self.assertEqual(doc["failures"], {"model": {"questions": 2, "by_kind": {"schema_invalid": 2}},
                                           "transport": {"questions": 2, "by_kind": {"network": 2}}})
        self.assertTrue(doc["complete"])
        rows = read_ledger(run / "ledger.jsonl")
        ref_bad = f"j2-1-0-{self.qs.index(bad):04d}"
        self.assertEqual([(r["attempt"], r["error_kind"]) for r in rows if r["ref"] == ref_bad],
                         [(1, "schema_invalid"), (2, "schema_invalid")])                 # rule 4: one repair
        record, problem = units.participation("j2", rows, [N], doc)
        self.assertEqual((record["tasks"][N]["ok"], record["tasks"][SHIPPED]["ok"], problem), (39, 39, None))

    def test_a_stop_in_the_second_pass_leaves_the_first_complete(self) -> None:
        """A9: the budget ends during the shipped pass: task N's set is complete for this part, the shipped set is
        not, exit 1, and the unit reads ok with J2_SHIPPED_STOPPED; the required task is N alone."""
        code, doc, lines, server = self.stopped_run([0.0] * 41 + [2.0] * 3)
        self.assertEqual(code, 1)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "budget", SHIPPED))
        self.assertEqual(doc["complete_by_task"], {N: True, SHIPPED: False})
        self.assertEqual((doc["questions_done"], doc["questions_done_by_task"]), (40, {N: 40, SHIPPED: 0}))
        self.assertEqual([line["task"] for line in lines], [N] * 40)
        self.assertEqual(len(server.chat_requests), 40)
        self.assertEqual(units.harness_status("j2", 1, False, doc), ("ok", J2_SHIPPED_STOPPED))
        rows = read_ledger(self.tmp / "runs" / "j2" / "r1" / "ledger.jsonl")
        unit = {"experiment": "j2", "params": {}}
        self.assertEqual(units.required_tasks(unit), [N])
        record, problem = units.participation("j2", rows, units.required_tasks(unit), doc)
        self.assertEqual((record["required"], record["tasks"][N]["attempted"], problem), ([N], 40, None))
        self.assertNotIn(SHIPPED, record["tasks"])

    def test_a_stop_in_the_first_pass_fails_the_unit(self) -> None:
        code, doc, lines, server = self.stopped_run([0.0, 0.0, 0.0, 2.0, 2.0])
        self.assertEqual(code, 1)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "budget", N))
        self.assertEqual(doc["complete_by_task"], {N: False, SHIPPED: False})
        self.assertEqual((doc["questions_done"], doc["questions_done_by_task"], len(lines)), (2, {N: 2, SHIPPED: 0}, 2))
        self.assertEqual(units.harness_status("j2", 1, False, doc), ("failed", J1_STOPPED))

    def test_it_stops_when_the_server_stays_down(self) -> None:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code, _, err, run = self.run_j2(self.argv(self.routing(f"http://127.0.0.1:{port}/v1")))
        self.assertEqual(code, 1, err)
        doc, lines = self.read(run)
        self.assertEqual((doc["complete"], doc["stopped"], doc["stopped_task"]), (False, "server_down", N))
        self.assertEqual(doc["complete_by_task"], {N: False, SHIPPED: False})
        self.assertEqual({(line["task"], line["error_kind"], line["scored"]) for line in lines},
                         {(N, "network", False)})
        self.assertEqual(units.harness_status("j2", 1, False, doc), ("failed", J1_STOPPED))

    def test_every_pin_is_checked_before_any_call(self) -> None:
        """Rule 3 and 4: both tasks' pins, the mapping, the arms, the routing of both tasks without escalation, the
        labels, the questions, the pack and the code hash, each refused with exit 2 before a call is made."""
        server = self.server()
        good = self.routing(server.base_url)
        d = self.p.dir

        def changed(name: str, edit: Callable[[bytes], bytes]) -> Path:
            target = self.tmp / name
            target.write_bytes(edit((d / name).read_bytes()))
            return target

        def prereg_with(change: Callable[[dict[str, Any]], None]) -> Path:
            doc = read_json(d / "prereg.json")
            change(doc)
            return write_json(self.tmp / "prereg.json", doc)

        def routing_with(change: Callable[[dict[str, Any]], None]) -> Path:
            return self.routing(server.base_url, change=change)

        cases = [
            ("labels", {"labels": changed("labels.jsonl", lambda b: b.replace(b"stalled", b"stopped", 1))}),
            ("questions", {"questions": changed("questions.jsonl", lambda b: b + b"\n")}),
            ("pack", {"prereg": prereg_with(lambda p: p["pack"].update(vocabulary_hash="0" * 64))}),
            ("code", {"prereg": prereg_with(lambda p: p.update(code_hash="0" * 64))}),
            ("negation max_tokens", {"prereg": prereg_with(lambda p: p["tasks"]["negation"].update(max_tokens=512))}),
            ("negation instructions", {"prereg": prereg_with(lambda p: p["tasks"]["negation"].update(
                instructions_sha256="0" * 64))}),
            ("shipped max_tokens", {"prereg": prereg_with(lambda p: p["tasks"]["shipped"].update(max_tokens=512))}),
            ("task order", {"prereg": prereg_with(lambda p: p["tasks"].update(order=[SHIPPED, N]))}),
            ("mapping rule", {"prereg": prereg_with(lambda p: p["mapping"]["rule"]["describes_predicate"].update(
                unclear="yes"))}),
            ("mapping sha", {"prereg": prereg_with(lambda p: p["mapping"].update(sha256="0" * 64))}),
            ("arms", {"prereg": prereg_with(lambda p: p["arms"]["shipped_mapped"].update(mapped=False))}),
            ("kind", {"prereg": prereg_with(lambda p: p.update(kind="lab_j1_prereg"))}),
            ("transport", {"routing": routing_with(lambda r: r["endpoints"]["fake-a"].update(
                transport_schema="reduced"))}),
            ("model", {"routing": routing_with(lambda r: r["endpoints"]["fake-a"].update(model="another-alias"))}),
            ("boundary", {"routing": routing_with(lambda r: r["endpoints"]["fake-a"].update(boundary="central"))}),
            ("negation route missing", {"routing": routing_with(lambda r: r["routes"].pop(N))}),
            ("shipped route missing", {"routing": routing_with(lambda r: r["routes"].pop(SHIPPED))}),
            ("negation escalation", {"routing": routing_with(lambda r: r["routes"][N].update(escalate_to="fake-a"))}),
            ("shipped escalation", {"routing": routing_with(lambda r: r["routes"][SHIPPED].update(
                escalate_to="fake-a"))}),
            ("part", {"part": 3}),
            ("endpoint", {"endpoint": "fake-slow"}),
            ("budget", {"budget": 0}),
        ]
        for i, (label, change) in enumerate(cases):
            routing = change.pop("routing", good)
            argv = self.argv(routing, run_id=f"pin-{i}", **change)
            with self.subTest(pin=label):
                code, _, err, run = self.run_j2(argv)
                self.assertEqual(code, 2, label)
                self.assertTrue(err.startswith("error: "), err)
                self.assertFalse(run.exists())
        self.assertEqual(server.chat_requests, [])
        with mock.patch.object(lab_j2, "code_hash", lambda: "1" * 64):
            code, _, _, run = self.run_j2(self.argv(good, run_id="pin-code-now"))
        self.assertEqual((code, run.exists()), (2, False))
        # a j1 prereg.json is not a j2 one
        code, _, err, run = self.run_j2(self.argv(good, run_id="pin-j1", prereg=ROOT / "lab" / "models.json"))
        self.assertEqual(code, 2)
        self.assertIn("not a J2 prereg.json", err)

    def test_the_unit_adapter_runs_it(self) -> None:
        """``lab.units.run_unit`` with the fake provider: the argv, the routing of both tasks, the files, the notes,
        the participation of both tasks and the status."""
        unit = next(u for u in self.p.plan["units"] if u["unit"] == "j2-fake-b-p2")
        prereg = lab_prereg.load_prereg(self.p.plan_path)
        out = self.tmp / "shard"
        record = units.run_unit(dict(unit, shard="s002-fake-b"), self.p.plan, out, timeout_s=300,
                                provider_override="fake", prereg=prereg)
        self.assertEqual((record["status"], record["measurement_class"], record["exit_code"]),
                         ("ok", "plumbing", 0), record["status_reason"])
        self.assertEqual(record["notes"], ["plumbing", "public_narratives", "runner_hardware"])
        base = f"runs/j2/{unit['run_id']}/"
        self.assertEqual(sorted(record["files"]), [base + "ledger.jsonl", base + "run.json", base + "verdicts.jsonl"])
        argv = record["argv"]
        self.assertEqual(argv[2:4], ["lab.j2", "run"])
        self.assertIn("--part=2", argv)
        self.assertIn("--endpoint=fake-b", argv)
        self.assertIn(f"--budget-seconds={300 - units.SIM_BUDGET_MARGIN_S}", argv)
        self.assertTrue(any(a.endswith("prereg/j2/prereg.json") for a in argv))
        routing = read_json(out / "routing" / f"{unit['unit']}.json")
        self.assertEqual((sorted(routing["endpoints"]), routing["routes"]),
                         (["fake-b"], {N: {"endpoint": "fake-b"}, SHIPPED: {"endpoint": "fake-b"}}))
        self.assertEqual((record["participation"]["required"], record["participation"]["tasks"][N]["attempted"],
                          record["participation"]["tasks"][SHIPPED]["attempted"]), ([N], 40, 40))
        self.assertEqual(record["harness_measurement"], False)
        doc = read_json(out / (base + "run.json"))
        self.assertEqual((doc["complete"], doc["complete_by_task"]), (True, {N: True, SHIPPED: True}))

    def test_the_status_reading(self) -> None:
        def result(complete: bool, negation: bool, shipped: bool, kind: str = "lab_j2_run") -> dict[str, Any]:
            return {"kind": kind, "complete": complete, "complete_by_task": {N: negation, SHIPPED: shipped}}

        self.assertEqual(units.harness_status("j2", 0, False, result(True, True, True)), ("ok", None))
        self.assertEqual(units.harness_status("j2", 1, False, result(False, True, False)), ("ok", J2_SHIPPED_STOPPED))
        self.assertEqual(units.harness_status("j2", 1, False, result(False, False, False)), ("failed", J1_STOPPED))
        self.assertEqual(units.harness_status("j2", 1, False, result(False, False, True)), ("failed", J1_STOPPED))
        self.assertEqual(units.harness_status("j2", 1, False, result(False, True, True)), ("failed", J1_STOPPED))
        self.assertEqual(units.harness_status("j2", 0, False, result(False, True, False)),
                         ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j2", 0, False, result(True, True, False)),
                         ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j2", 1, False, result(True, True, True)),
                         ("failed", RESULT_CONTRADICTS_EXIT))
        self.assertEqual(units.harness_status("j2", 0, False, result(True, True, True, kind="lab_j1_run")),
                         ("failed", RESULT_MISSING))
        self.assertEqual(units.harness_status("j2", 1, False, {"kind": "lab_j2_run", "complete": False}),
                         ("failed", RESULT_MISSING))
        self.assertEqual(units.harness_status("j2", 1, False, {"kind": "lab_j2_run", "complete": False,
                                                               "complete_by_task": {N: True}}),
                         ("failed", RESULT_MISSING))
        self.assertEqual(units.harness_status("j2", 0, False, None), ("failed", RESULT_MISSING))
        self.assertEqual(units.harness_status("j2", 3, False, result(True, True, True)), ("failed", UNEXPECTED_EXIT))
        self.assertEqual(units.harness_status("j2", 2, False, result(True, True, True))[0], "failed")
        self.assertEqual(units.harness_status("j2", 0, True, result(True, True, True))[0], "timed_out")
        self.assertEqual(units.harness_measurement("j2", {"measurement": True}), True)
        self.assertIs(units.harness_verdict("j2", {"measurement": False}), False)
        self.assertEqual(units.required_tasks({"experiment": "j2", "params": {}}), [N])
        self.assertTrue(units.public_text({"experiment": "j2", "params": {}}))
        self.assertEqual(units.unit_notes("j2", "model", public_text=True),
                         ["model_measurement_public", "public_narratives", "runner_hardware"])
        adapter = units.ADAPTERS["j2"]
        self.assertEqual((adapter.module, adapter.routed_tasks, adapter.boundary, adapter.result_file,
                          adapter.subcommand, adapter.deadline_s),
                         ("lab.j2", lab_j2.TASKS, "site:lab", "run.json", "run", lab_j1.DEADLINE_S))
        self.assertEqual(sorted(adapter.collect), ["ledger.jsonl", "run.json", "verdicts.jsonl"])

    def test_participation_judges_the_shipped_pass_only_when_it_finished(self) -> None:
        """A9: the floor is judged on task N always and on the shipped task only when its pass finished."""

        def row(task: str, ref: str, ok: bool) -> dict[str, Any]:
            return {"task": task, "ref": ref, "attempt": 1, "ok": ok, "error_kind": None if ok else "network",
                    "boundary": "site:lab"}

        good_n = [row(N, f"n{i}", True) for i in range(10)]
        bad_shipped = [row(SHIPPED, f"s{i}", i == 0) for i in range(10)]
        stopped = {"kind": "lab_j2_run", "complete": False, "complete_by_task": {N: True, SHIPPED: False}}
        finished = {"kind": "lab_j2_run", "complete": True, "complete_by_task": {N: True, SHIPPED: True}}
        record, problem = units.participation("j2", good_n + bad_shipped, [N], stopped)
        self.assertEqual((record["tasks"][SHIPPED]["ok"], problem), (1, None))
        record, problem = units.participation("j2", good_n + bad_shipped, [N], finished)
        self.assertTrue(problem is not None and problem.startswith(LOW_PARTICIPATION) and SHIPPED in problem)
        bad_n = [row(N, f"n{i}", i == 0) for i in range(10)]
        record, problem = units.participation("j2", bad_n, [N], stopped)
        self.assertTrue(problem is not None and problem.startswith(LOW_PARTICIPATION) and N in problem)
        record, problem = units.participation("j2", bad_shipped, [N], stopped)
        self.assertTrue(problem is not None and problem.startswith(notes.NO_MODEL_CALLS))


# --------------------------------------------------------------------------------------------------- wiring

class WiringTests(unittest.TestCase):
    def test_the_fake_provider_answers_task_n_as_the_shipped_judge(self) -> None:
        """A10: both task names are answered alike, in the pack branch and the pack-free one; an unknown name is
        still refused."""
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
                with self.subTest(responder=responder.pack is not None, question=q["question_id"]):
                    self.assertEqual(responder(request(N, payload)), responder(request(SHIPPED, payload)))
                    self.assertIn(responder(request(N, payload))["describes_predicate"], ("yes", "no"))
                    with self.assertRaises(ResponderError):
                        responder(request("judge_record_other", payload))
        with self.assertRaises(ResponderError):
            Responder(None)(request(N, lab_j1.payload(PACK, qs[0], recs[qs[0]["record_ref"]])))

    def test_the_warm_up_sends_both_tasks_the_shipped_one_first(self) -> None:
        """A11: a J2 unit warms both tasks with the judge's generated payloads, sorted by name, so the probe is the
        shipped task's; J1 units warm the shipped task alone."""
        p = _Prereg.get()
        units_ = [u for u in p.plan["units"] if u["model"] == "fake-a"]
        tasks = [t for t in lab_warmup.warm_tasks(units_, p.plan) if t.units]
        self.assertEqual([t.task.name for t in tasks], [SHIPPED, N])
        shipped, negation = tasks
        self.assertEqual(negation.task, lab_j2.negation_task())
        self.assertEqual(shipped.task, judge_task())
        self.assertEqual((negation.payload, negation.worst, negation.schema, negation.stream, negation.pack),
                         (shipped.payload, shipped.worst, shipped.schema, False, NHTSA_PACK))
        self.assertEqual(sorted(negation.units), sorted(u["unit"] for u in units_))
        self.assertEqual(negation.units, shipped.units)
        self.assertNotIn(negation.payload["record"]["text"], "".join(
            r["narrative"] for r, _ in lab_j1.parse_labels((p.dir / "labels.jsonl").read_bytes())))
        j1_units = [dict(u, experiment="j1", unit=u["unit"].replace("j2", "j1")) for u in units_]
        self.assertEqual([t.task.name for t in lab_warmup.warm_tasks(j1_units, p.plan) if t.units], [SHIPPED])

    def test_the_plan_outputs_name_the_excluded_run(self) -> None:
        p = _Prereg.get()
        outputs = lab_plan.plan_outputs(p.plan, "0" * 64)
        self.assertEqual((outputs["j2_exclude_artifact"], outputs["j2_exclude_run_id"]),
                         (j2_data.ARTIFACT, str(j2_data.RUN_ID)))
        self.assertEqual(lab_plan.j2_exclude(p.plan), p.exclude)
        tmp = Path(tempfile.mkdtemp(prefix="lab-j2-outputs-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        code, _, err = run_plan(["--request", str(p.tmp / "j2-test.json"), "--manifest", str(MANIFEST_TEST),
                                 "--out", str(tmp / "plan"), "--github-output", str(tmp / "out.txt")])
        self.assertEqual(code, 0, err)
        written = (tmp / "out.txt").read_text(encoding="utf-8")
        self.assertIn(f"j2_exclude_artifact={j2_data.ARTIFACT}\n", written)
        self.assertIn(f"j2_exclude_run_id={j2_data.RUN_ID}\n", written)
        self.assertEqual(p.plan["units"][0]["params"]["exclude"], p.exclude)
        self.assertEqual({u["params"]["primary"]["arm"] for u in p.plan["units"]}, {"negation"})
        self.assertEqual({u["params"]["comparisons_before"] for u in p.plan["units"]}, {0})


# --------------------------------------------------------------------------------------------------- dry runs

class SmallDryRunTests(unittest.TestCase):
    """The small dry run: two gguf models on the fake provider, the test data standing in for the excluded run."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Small.get()

    def test_every_unit_ran_against_the_fake_provider(self) -> None:
        self.assertEqual(self.dry.code, 0, self.dry.stdout)
        lines = self.dry.stdout.splitlines()
        self.assertIn(f"plan: 4 units in {len(self.dry.plan['shards'])} shards (real)", lines)
        self.assertIn("j2 exclusion: both mechanisms verified; symmetric difference 0", lines)
        self.assertIn("prereg: e1 no x1 no e2 no j1 no j2 yes", lines)
        self.assertEqual({(u["status"], u["display_class"]) for u in self.dry.report["units"]}, {("ok", "plumbing")})
        self.assertEqual(sorted(u["unit"] for u in self.dry.plan["units"]),
                         ["j2-a-1p5b-p1", "j2-a-1p5b-p2", "j2-a-4b-p1", "j2-a-4b-p2"])

    def test_the_report_block(self) -> None:
        j2 = self.dry.report["j2"]
        self.assertEqual((j2["label"], j2["reason"], j2["display_class"], j2["stand_in"], sorted(j2["models"])),
                         ("public_nhtsa", None, "plumbing", True, ["a-1p5b", "a-4b"]))
        self.assertEqual((j2["labels"]["records"], j2["labels"]["available"], j2["questions"]["questions"],
                          j2["questions"]["part_records"]), (40, 360, 80, [20, 20]))
        self.assertEqual(j2["comparisons"], {"this_cycle": 8, "before": 0, "since_loop_began": 8})
        self.assertEqual((j2["primary"], j2["headline_arm"], j2["target_balanced_accuracy"]),
                         ({"model": "a-4b", "arm": "negation"}, "negation", 0.86))
        self.assertEqual((j2["tasks"], j2["mapping"], j2["arms"]),
                         (lab_j2.task_pins(), lab_j2.mapping_pin(), lab_j2.arms_doc()))
        self.assertEqual((j2["exclusion"]["excluded"], j2["exclusion"]["overlap"], j2["exclusion"]["mechanisms"],
                          j2["exclusion"]["stand_in"]),
                         (150, 0, {"artifact": "verified", "rebuild": "verified"}, True))
        self.assertEqual(j2["settings"], {"parts": 2, "seed": 2, "bootstrap_b": 1000, "bootstrap_seed": 2,
                                          "comparisons_before": 0, "target_balanced_accuracy": 0.86,
                                          "withhold_share": 0.01})
        manifest = self.dry.manifest
        self.assertEqual((j2["lexical"], j2["prior"], j2["prior_bound"]),
                         (manifest["j2"]["lexical"], manifest["j2"]["prior"], manifest["j2"]["prior_bound"]))
        for name, entry in j2["models"].items():
            with self.subTest(model=name):
                self.assertEqual(sorted(entry), ["arms", "lifts", "parts", "sets"])
                self.assertEqual({(s["complete"], s["parts_finished"], s["display_class"], s["measurement"])
                                  for s in entry["sets"].values()}, {(True, 2, "plumbing", False)})
                self.assertEqual(sorted(entry["sets"]), ["negation", "shipped"])
                for arm in lab_j2.ARMS:
                    a = entry["arms"][arm]
                    self.assertEqual((a["task"], a["mapped"], a["complete"], a["parts_finished"], a["parts_planned"],
                                      a["display_class"], a["verdict_word"], a["records_dropped"]),
                                     (lab_j2.ARM_TASK[arm], lab_j2.ARM_MAPPED[arm], True, 2, 2, "plumbing",
                                      "incomplete", 0), arm)
                    # the fake answers as the lexical judge, in both passes, and never unclear: every arm is lexical
                    for metric in lab_j1.METRICS:
                        self.assertEqual(a["scores"][metric], j2["lexical"][metric], (arm, metric))
                    self.assertEqual((a["paired_lexical"]["n"], a["paired_lexical"]["mean_diff"]), (40, 0.0))
                    self.assertEqual(a["prior_bound"], j2["prior_bound"])
                    self.assertEqual((a["primary"], a["headline_arm"]),
                                     (name == "a-4b" and arm == "negation", arm == "negation"))
                    self.assertEqual((a["headline"], a["headline_reason"]),
                                     (None, J1_NOT_MEASURED if arm == "negation" else None))
                    self.assertFalse(a["pass_test"]["passed"])
                for lift, arm, minus in lab_j2.LIFTS:
                    entry_l = entry["lifts"][lift]
                    self.assertEqual((entry_l["arm"], entry_l["minus"], entry_l["lever"], entry_l["reading"],
                                      entry_l["complete"], entry_l["paired"]["mean_diff"], entry_l["paired"]["n"]),
                                     (arm, minus, lab_j2.LIFT_LEVERS[lift], "not_told_apart", True, 0.0, 40))
                for part in entry["parts"]:
                    self.assertEqual((part["ok_by_task"], part["complete_by_task"], part["stopped"]),
                                     ({N: True, SHIPPED: True}, {N: True, SHIPPED: True}, None))
                    self.assertEqual(sorted(part["latency_ms_by_task"]), sorted(lab_j2.TASKS))
        primary = j2["primary_result"]
        self.assertEqual((primary["model"], primary["arm"], primary["verdict_word"], primary["complete"],
                          primary["display_class"], primary["measurement"]),
                         ("a-4b", "negation", "incomplete", True, "plumbing", False))
        self.assertEqual(primary["lexical_balanced_accuracy"], j2["lexical"]["balanced_accuracy"]["value"])

    def test_the_summaries(self) -> None:
        root = self.dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        self.assertEqual(md.splitlines()[0], NO_MEASUREMENT_LINE)
        for heading in ("j2", "j2-arms", "j2-lifts"):
            self.assertIn(f"#### {notes.HEADINGS[heading]}", md)
        for note in (J2_STAND_IN, J2_ARMS_NOTE, J2_VERDICT_NOTE, J2_EXCLUSION_NOTE):
            self.assertIn(note, md)
        self.assertIn("J2 here asks each model", md.replace("`", ""))
        self.assertIn("Primary pair `a-4b` `negation`: `incomplete`. It scored balanced accuracy", md)
        self.assertIn("gap to the target 0.860 is", md)
        self.assertIn("- comparisons since the loop began: 8 (8 this cycle, 0 before)", md)
        self.assertIn("| `a-4b` | `negation` | `judge_record_negation` | no | `plumbing` | 2 of 2 |", md)
        self.assertIn("| `a-4b` | `negation_mapped` | `judge_record_negation` | yes | `plumbing` | 2 of 2 |", md)
        self.assertIn("| `a-4b` | `C_minus_A` | `negation_instruction` | 40 | 0.000 |", md)
        self.assertIn("eligible records 360; excluded records 150; eligible after exclusion 210", md)
        sources = read_json(root / "report.sources.json")["sources"]
        check_sources(self, md, sources, root)
        plan_md = (self.dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        plan_sources = read_json(self.dry.out / "plan" / "summary.sources.json")
        check_sources(self, plan_md, plan_sources["sources"], self.dry.out / "plan")
        self.assertIn("`J2` labels: source `nhtsa`, records 40", plan_md)
        self.assertIn(J2_STAND_IN, plan_md)
        drawn = refs_of_labels((self.dry.out / "plan" / "prereg" / "j2" / "labels.jsonl").read_bytes())
        excluded = {line["record_ref"] for line in read_lines(self.dry.out / "plan" / "prereg" / "j2"
                                                              / "excluded.jsonl")}
        for text in (md, plan_md, (root / "report.json").read_text(encoding="utf-8"), self.dry.stdout):
            assert_no_ref(self, text, drawn | excluded)
            self.assertNotIn("Operator", text)


class AggregateTests(unittest.TestCase):
    """DryTree copies of the small dry run, changed and re-sealed as a real run would have left them: the verdict
    words (rule 10, A5, A6), the lifts (rules 8 and 9), the stop that spares task N (A9), the line check (A8)."""

    def setUp(self) -> None:
        self.dry = _Small.get()
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-j2-agg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = DryTree(self.dry.out, self.tmp / "T", manifest=LAB_MANIFEST)
        self.questions = lab_j1.read_questions((self.dry.out / "plan" / "prereg" / "j2" / "questions.jsonl")
                                               .read_bytes())
        self.lexical = {line["question_id"]: line for line in read_lines(self.dry.out / "plan" / "prereg" / "j2"
                                                                         / "lexical.jsonl")}

    def shard_of(self, model: str, part: int) -> tuple[str, dict[str, Any]]:
        unit = next(u for u in self.tree.plan["units"] if u["unit"] == f"j2-{model}-p{part}")
        return next(s["shard"] for s in self.tree.plan["shards"] if unit["unit"] in s["units"]), unit

    def make_measured(self, *models: str) -> None:
        """Every unit of each model: a verified model measurement whose run.json says ``measurement: true``."""
        for model in models:
            shards: dict[str, list[str]] = {}
            for part in (1, 2):
                shard, unit = self.shard_of(model, part)
                rel = f"runs/j2/{unit['run_id']}/run.json"
                doc = self.tree.read(shard, rel)
                doc["measurement"] = True
                self.tree.replace_unit_file(shard, unit["unit"], rel, canonical_bytes(doc) + b"\n")
                shards.setdefault(shard, []).append(unit["unit"])
            for shard, unit_ids in shards.items():
                self.tree.make_real(shard, model_units=tuple(unit_ids))

    def restamp(self, model: str, part: int, *, lines: Callable[[list[dict[str, Any]]], None] | None = None,
                run: Callable[[dict[str, Any]], None] | None = None,
                unit_record: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Change one part's verdict lines, run.json or unit record and stamp every hash again, as a hand edit that
        knew the hashes would."""
        shard, unit = self.shard_of(model, part)
        rel = f"runs/j2/{unit['run_id']}/verdicts.jsonl"
        got = read_lines(self.tree.path(shard, rel))
        if lines is not None:
            lines(got)
        data = jsonl(got)
        self.tree.replace_unit_file(shard, unit["unit"], rel, data)
        run_rel = f"runs/j2/{unit['run_id']}/run.json"
        doc = self.tree.read(shard, run_rel)
        doc["verdicts_sha256"] = hashlib.sha256(data).hexdigest()
        if run is not None:
            run(doc)
        self.tree.replace_unit_file(shard, unit["unit"], run_rel, canonical_bytes(doc) + b"\n")
        if unit_record is not None:
            self.tree.edit(shard, f"units/{unit['unit']}/unit.json", unit_record)
        self.tree.reseal(shard)

    @staticmethod
    def answer(line: dict[str, Any], m: str | None, d: str | None, error_kind: str | None = None) -> None:
        """Rewrite a line's reply and both verdicts consistently, as the runner would have written them."""
        reply = None if m is None else {"mentions_entity": m, "describes_predicate": d}
        w = lab_j1.stand_in(line["record_ref"])
        line.update(mentions_entity=m, describes_predicate=d, error_kind=error_kind, scored=error_kind is None
                    or error_kind in ("schema_invalid", "json_invalid"),
                    verdict=lab_j1.verdict(w, reply), mapped_verdict=lab_j1.verdict(w, lab_j2.mapped_reply(reply)))

    def correct(self, line: dict[str, Any]) -> None:
        self.answer(line, "yes", "yes" if line["kind"] == "positive" else "no")

    def aggregate(self) -> dict[str, Any]:
        shutil.rmtree(self.tmp / "R", True)
        code, _, err, report = self.tree.aggregate(self.tmp / "R")
        self.assertEqual(code, 0, err)
        return report["j2"]

    def test_the_verdict_words_and_the_lifts(self) -> None:
        """a-4b, measured, answers task N right on every question: P passes provisionally, the instruction's lifts
        help and the mapping's are not told apart; a-1p5b, measured, answers as the lexical judge: failure."""
        self.make_measured("a-4b", "a-1p5b")
        for part in (1, 2):
            self.restamp("a-4b", part, lines=lambda ls: [self.correct(x) for x in ls if x["task"] == N])
        block = self.aggregate()
        lexical_ba = block["lexical"]["balanced_accuracy"]["value"]
        bound = block["prior_bound"]["value"]
        arms = block["models"]["a-4b"]["arms"]
        for arm in ("negation", "negation_mapped"):
            a = arms[arm]
            with self.subTest(arm=arm):
                self.assertEqual((a["display_class"], a["measurement"], a["complete"], a["verdict_word"]),
                                 ("model", True, True, "pass_provisional"))
                self.assertEqual((a["scores"]["balanced_accuracy"]["value"], a["scores"]["unknown_share"]["value"],
                                  a["records_dropped"], a["dropped_share"]), (1.0, 0.0, 0, 0.0))
                self.assertEqual(a["pass_test"]["passed"], True)
                self.assertEqual(a["pass_test"]["clauses"], {"balanced_accuracy_at_target": True, "beats_lexical": True,
                                                             "beats_bound": True, "unknown_share_reported": True})
                self.assertEqual(a["lexical"]["balanced_accuracy"]["value"], lexical_ba)
                self.assertAlmostEqual(a["paired_lexical"]["mean_diff"], 1.0 - lexical_ba, places=12)
                self.assertGreater(a["paired_lexical"]["ci_low"], 0)
                self.assertEqual((a["prior_bound"]["value"], a["pass_test"]["bound"]), (bound, bound))
                self.assertAlmostEqual(a["pass_test"]["gap_to_target"], -0.14, places=12)
        self.assertEqual((arms["negation"]["headline"], arms["negation"]["headline_reason"],
                          arms["negation"]["primary"]), ("better", None, True))
        self.assertEqual((arms["negation_mapped"]["headline"], arms["negation_mapped"]["primary"]), (None, False))
        for arm in ("shipped", "shipped_mapped"):
            a = arms[arm]
            with self.subTest(arm=arm):
                self.assertEqual((a["verdict_word"], a["scores"]["balanced_accuracy"]["value"],
                                  a["paired_lexical"]["mean_diff"], a["headline"]), ("failure", lexical_ba, 0.0, None))
                self.assertEqual(a["pass_test"]["clauses"]["beats_lexical"], False)
        lifts = block["models"]["a-4b"]["lifts"]
        self.assertEqual({name: entry["reading"] for name, entry in lifts.items()},
                         {"B_minus_A": "not_told_apart", "C_minus_A": "helps", "D_minus_A": "helps",
                          "D_minus_B": "helps", "D_minus_C": "not_told_apart"})
        self.assertEqual({name: entry["complete"] for name, entry in lifts.items()}, dict.fromkeys(lifts, True))
        self.assertAlmostEqual(lifts["C_minus_A"]["paired"]["mean_diff"], 1.0 - lexical_ba, places=12)
        self.assertEqual((lifts["B_minus_A"]["paired"]["mean_diff"], lifts["D_minus_C"]["paired"]["mean_diff"]),
                         (0.0, 0.0))
        other = block["models"]["a-1p5b"]
        self.assertEqual({arm: a["verdict_word"] for arm, a in other["arms"].items()}, dict.fromkeys(lab_j2.ARMS, "failure"))
        self.assertEqual((other["arms"]["negation"]["headline"], other["arms"]["negation"]["display_class"]),
                         ("not_told_apart", "model"))
        self.assertEqual({name: entry["reading"] for name, entry in other["lifts"].items()},
                         dict.fromkeys(lab_j2.LIFT_LEVERS, "not_told_apart"))
        primary = block["primary_result"]
        self.assertEqual((primary["model"], primary["arm"], primary["verdict_word"], primary["pass_test"]["passed"]),
                         ("a-4b", "negation", "pass_provisional", True))
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("Primary pair `a-4b` `negation`: `pass_provisional`. It scored balanced accuracy 1.000 "
                      "[1.000, 1.000]; gap to the target 0.860 is -0.140;", md)
        self.assertIn("| `pass_provisional` | yes | yes | `better` |", md)
        self.assertIn("| `failure` | no | no | n/a |", md)
        self.assertIn("| `a-4b` | `C_minus_A` | `negation_instruction` | 40 |", md)
        self.assertIn("| `helps` |", md)
        self.assertNotIn(J1_NOT_MEASURED, md)

    def test_partial_and_the_mapping_s_lift(self) -> None:
        """a-4b's task N confirms twelve positives the lexical judge missed and nothing else changes: below the
        target but the lexical judge beaten, so partial. a-1p5b's task N answers unclear on ten negatives: under the
        shipped rule they are unknown (arm C below the lexical judge: failure, C - A hurts), under the mapping they
        are refutes (D - C helps; the answer counts show the pairs before and after M)."""
        self.make_measured("a-4b", "a-1p5b")
        missed = [q["question_id"] for q in self.questions if q["kind"] == "positive"
                  and self.lexical[q["question_id"]]["verdict"] != "confirm"]
        self.assertGreaterEqual(len(missed), 12)
        fix = set(missed[:12])
        for part in (1, 2):
            self.restamp("a-4b", part, lines=lambda ls: [self.answer(x, "yes", "yes") for x in ls
                                                         if x["task"] == N and x["question_id"] in fix])
        negatives = [q["question_id"] for q in self.questions if q["kind"] == "negative"][:10]
        for part in (1, 2):
            self.restamp("a-1p5b", part, lines=lambda ls: [self.answer(x, "yes", "unclear") for x in ls
                                                           if x["task"] == N and x["question_id"] in negatives])
        block = self.aggregate()
        lexical = block["lexical"]
        a = block["models"]["a-4b"]["arms"]["negation"]
        self.assertAlmostEqual(a["scores"]["sensitivity"]["value"], lexical["sensitivity"]["value"] + 12 / 40, places=12)
        self.assertEqual(a["scores"]["specificity"]["value"], lexical["specificity"]["value"])
        self.assertLess(a["scores"]["balanced_accuracy"]["value"], 0.86)
        self.assertGreater(a["paired_lexical"]["ci_low"], 0)
        self.assertAlmostEqual(a["paired_lexical"]["mean_diff"], 12 / 80, places=12)
        self.assertEqual((a["verdict_word"], a["pass_test"]["passed"]), ("partial", False))
        self.assertEqual(a["pass_test"]["clauses"], {"balanced_accuracy_at_target": False, "beats_lexical": True,
                                                     "beats_bound": True, "unknown_share_reported": True})
        self.assertEqual(block["primary_result"]["verdict_word"], "partial")
        self.assertEqual(block["models"]["a-4b"]["lifts"]["C_minus_A"]["reading"], "helps")
        self.assertEqual(block["models"]["a-4b"]["lifts"]["D_minus_C"]["reading"], "not_told_apart")
        model = block["models"]["a-1p5b"]
        c, d = model["arms"]["negation"], model["arms"]["negation_mapped"]
        self.assertEqual((c["scores"]["specificity"]["value"], d["scores"]["specificity"]["value"]),
                         (lexical["specificity"]["value"] - 10 / 40, lexical["specificity"]["value"]))
        self.assertEqual((c["scores"]["unknown_share"]["value"], d["scores"]["unknown_share"]["value"]),
                         (10 / 80, 0.0))
        self.assertEqual(c["scores"]["answers"]["negative"]["yes_unclear"], 10)
        self.assertNotIn("yes_unclear", d["scores"]["answers"]["negative"])
        self.assertEqual(d["scores"]["answers"]["negative"]["yes_no"], c["scores"]["answers"]["negative"].get("yes_no", 0) + 10)
        self.assertEqual((c["verdict_word"], d["verdict_word"]), ("failure", "failure"))
        self.assertEqual((c["headline"], c["pass_test"]["clauses"]["beats_lexical"]), ("worse", False))
        self.assertLess(c["paired_lexical"]["ci_high"], 0)
        lifts = {name: entry["reading"] for name, entry in model["lifts"].items()}
        self.assertEqual(lifts, {"B_minus_A": "not_told_apart", "C_minus_A": "hurts", "D_minus_A": "not_told_apart",
                                 "D_minus_B": "not_told_apart", "D_minus_C": "helps"})
        self.assertAlmostEqual(model["lifts"]["D_minus_C"]["paired"]["mean_diff"], 10 / 80, places=12)
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("Primary pair `a-4b` `negation`: `partial`.", md)
        self.assertIn("| `a-1p5b` | `D_minus_C` | `unclear_mapping` | 40 | 0.125 |", md)
        self.assertIn("| `helps` |", md)
        self.assertIn("| `hurts` |", md)

    def test_what_is_not_a_finished_part(self) -> None:
        """A8 and rule 12: a mapped verdict that disagrees with the pinned decide over the mapped reply, a line of a
        third task, a run of another preregistration and a (question, task) twice each leave the part unfinished
        for both tasks (the verdict file is one); hashes stamped again do not save a line that differs. A model with
        one part left is a partial reading that decides nothing."""

        def first(match: Callable[[dict[str, Any]], bool], change: dict[str, Any]) -> Callable[[list], None]:
            def edit(lines: list[dict[str, Any]]) -> None:
                next(line for line in lines if match(line)).update(change)
            return edit

        self.restamp("a-1p5b", 2, lines=first(lambda x: x["task"] == SHIPPED, {"task": "judge_record_other"}))
        self.restamp("a-4b", 1, lines=first(lambda x: x["task"] == N and x["verdict"] == "confirm",
                                             {"mapped_verdict": "refute"}))
        self.restamp("a-4b", 2, run=lambda doc: doc.update(prereg_sha256="0" * 64))
        block = self.aggregate()
        small, big = block["models"]["a-1p5b"], block["models"]["a-4b"]
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in small["parts"]],
                         [(1, {N: True, SHIPPED: True}), (2, {N: False, SHIPPED: False})])
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in big["parts"]],
                         [(1, {N: False, SHIPPED: False}), (2, {N: False, SHIPPED: False})])
        self.assertEqual({s["parts_ok"] == [1] and not s["complete"] and s["partial"] for s in small["sets"].values()},
                         {True})
        self.assertEqual({arm: a["verdict_word"] for arm, a in small["arms"].items()},
                         dict.fromkeys(lab_j2.ARMS, "incomplete"))
        self.assertEqual({(a["parts_finished"], a["partial"], a["scores"]["records_scored"])
                          for a in small["arms"].values()}, {(1, True, 20)})
        self.assertEqual({(entry["complete"], entry["paired"]["n"], entry["reading"])
                          for entry in small["lifts"].values()}, {(False, 20, "not_told_apart")})
        self.assertEqual({arm: a["scores"] for arm, a in big["arms"].items()}, dict.fromkeys(lab_j2.ARMS, None))
        self.assertEqual({(entry["complete"], entry["paired"], entry["reading"]) for entry in big["lifts"].values()},
                         {(False, None, None)})
        self.assertEqual(block["primary_result"]["verdict_word"], "incomplete")
        self.assertEqual({s["parts_finished"] for s in big["sets"].values()}, {0})
        self.restamp("a-1p5b", 1, lines=lambda lines: lines.append(dict(lines[0])))
        small = self.aggregate()["models"]["a-1p5b"]
        self.assertEqual([(p["part"], p["ok_by_task"]) for p in small["parts"]],
                         [(1, {N: False, SHIPPED: False}), (2, {N: False, SHIPPED: False})])
        self.assertEqual({arm: a["scores"] for arm, a in small["arms"].items()}, dict.fromkeys(lab_j2.ARMS, None))

    def test_a_stop_in_the_second_pass_and_a_dropped_record(self) -> None:
        """A9 and A6 in the aggregate: a part whose shipped pass stopped keeps task N's set complete (P gets its
        word) and leaves the control's incomplete; a record left out by a transport failure in task N withholds
        that set when its share is above one in a hundred (here one of forty), and the shipped set is untouched."""
        self.make_measured("a-4b", "a-1p5b")

        def drop_shipped(lines: list[dict[str, Any]]) -> None:
            lines[:] = [line for line in lines if line["task"] != SHIPPED]

        self.restamp("a-4b", 2, lines=drop_shipped,
                     run=lambda doc: doc.update(complete=False, stopped="budget", stopped_task=SHIPPED,
                                                complete_by_task={N: True, SHIPPED: False}, questions_done=40,
                                                questions_done_by_task={N: 40, SHIPPED: 0}),
                     unit_record=lambda r: r.update(status="ok", status_reason=J2_SHIPPED_STOPPED, exit_code=1))
        first_positive = next(q["question_id"] for q in self.questions if q["part"] == 1 and q["kind"] == "positive")
        self.restamp("a-1p5b", 1, lines=lambda ls: [self.answer(x, None, None, "timeout") for x in ls
                                                    if x["task"] == N and x["question_id"] == first_positive])
        block = self.aggregate()
        big = block["models"]["a-4b"]
        part = next(p for p in big["parts"] if p["part"] == 2)
        self.assertEqual((part["status"], part["status_reason"], part["ok_by_task"], part["stopped"],
                          part["stopped_task"]), ("ok", J2_SHIPPED_STOPPED, {N: True, SHIPPED: False}, "budget", SHIPPED))
        self.assertEqual((big["sets"]["negation"]["complete"], big["sets"]["negation"]["parts_ok"]), (True, [1, 2]))
        self.assertEqual((big["sets"]["shipped"]["complete"], big["sets"]["shipped"]["partial"],
                          big["sets"]["shipped"]["parts_ok"]), (False, True, [1]))
        self.assertEqual({arm: a["verdict_word"] for arm, a in big["arms"].items()},
                         {"shipped": "incomplete", "shipped_mapped": "incomplete", "negation": "failure",
                          "negation_mapped": "failure"})
        self.assertEqual((big["arms"]["negation"]["headline"], big["arms"]["shipped"]["scores"]["records_scored"],
                          big["arms"]["negation"]["scores"]["records_scored"]), ("not_told_apart", 20, 40))
        self.assertEqual(block["primary_result"]["verdict_word"], "failure")
        lifts = big["lifts"]
        self.assertEqual({name: entry["complete"] for name, entry in lifts.items()},
                         {"B_minus_A": False, "C_minus_A": False, "D_minus_A": False, "D_minus_B": False,
                          "D_minus_C": True})
        self.assertEqual((lifts["C_minus_A"]["paired"]["n"], lifts["D_minus_C"]["paired"]["n"]), (20, 40))
        small = block["models"]["a-1p5b"]
        c, a = small["arms"]["negation"], small["arms"]["shipped"]
        self.assertEqual((c["records_dropped"], c["dropped_share"], c["verdict_word"], c["withheld_reason"],
                          c["headline"], c["headline_reason"]), (1, 1 / 40, "incomplete", J1_WITHHELD, None, J1_WITHHELD))
        self.assertEqual((c["complete"], c["scores"]["records_scored"], c["lexical"]["records_scored"]), (True, 39, 39))
        self.assertEqual((a["records_dropped"], a["verdict_word"], a["scores"]["records_scored"]), (0, "failure", 40))
        self.assertEqual(small["lifts"]["C_minus_A"]["paired"]["n"], 39)
        md, sources = lab_summary.render_report(self.tmp / "R")
        check_sources(self, md, sources, self.tmp / "R")
        self.assertIn("| `a-4b` | `shipped` | `judge_record` | no | `model` | 1 of 2 |", md)
        self.assertIn("| `a-4b` | `negation` | `judge_record_negation` | no | `model` | 2 of 2 |", md)

    def test_without_the_preregistration(self) -> None:
        (self.tree.root / "plan" / "prereg" / "prereg.json").unlink()
        block = self.aggregate()
        self.assertEqual((block["reason"], block["models"], block["primary_result"]),
                         (lab_aggregate.PREREG_MISSING, {}, None))
        md, sources = lab_summary.render_report(self.tmp / "R")
        self.assertIn(lab_aggregate.PREREG_MISSING, md)
        check_sources(self, md, sources, self.tmp / "R")


class TemplateDryRunTests(unittest.TestCase):
    """``lab.dryrun`` of the template the run copies to ``lab/requests/accuracy-001.json``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry = _Template.get()

    def test_the_plan(self) -> None:
        plan = self.dry.plan
        self.assertEqual((len(plan["units"]), len(plan["shards"]), plan["result_class"], plan["max_parallel"]),
                         (15, 15, "real", 15))
        self.assertEqual([s["units"] for s in plan["shards"]],
                         [[f"j2-{m}-p{k}"] for m in ("a-0p5b", "a-1p5b", "a-4b") for k in range(1, 6)])
        self.assertEqual({(s["planned_minutes"], s["timeout_minutes"]) for s in plan["shards"]}, {(300, 325)})
        self.assertEqual(sorted(e["key"] for e in plan["provision"] if e["target"] == "gguf"),
                         ["a-0p5b", "a-1p5b", "a-4b"])

    def test_every_unit_ran_against_the_fake_provider(self) -> None:
        self.assertEqual(self.dry.code, 0, self.dry.stdout)
        lines = self.dry.stdout.splitlines()
        self.assertIn("plan: 15 units in 15 shards (real)", lines)
        self.assertIn("j2 exclusion: both mechanisms verified; symmetric difference 0", lines)
        self.assertTrue(any(line.startswith("j2 draw: available 360 excluded 150 eligible_after_exclusion 210 overlap "
                                            "0 mechanisms artifact=verified rebuild=verified labels_sha256 ")
                            for line in lines))
        self.assertIn("prereg: e1 no x1 no e2 no j1 no j2 yes", lines)
        units_ = self.dry.report["units"]
        self.assertEqual(len(units_), 15)
        self.assertEqual({(u["status"], u["display_class"]) for u in units_}, {("ok", "plumbing")})

    def test_the_report_block(self) -> None:
        j2 = self.dry.report["j2"]
        self.assertEqual((j2["reason"], j2["display_class"], j2["stand_in"], sorted(j2["models"])),
                         (None, "plumbing", True, ["a-0p5b", "a-1p5b", "a-4b"]))
        self.assertEqual((j2["labels"]["records"], j2["labels"]["n"], j2["labels"]["seed"], j2["questions"]["questions"],
                          j2["questions"]["parts"], j2["questions"]["part_records"], j2["questions"]["seed"]),
                         (150, 150, 2, 300, 5, [30] * 5, 2))
        self.assertEqual(j2["comparisons"], {"this_cycle": 12, "before": 0, "since_loop_began": 12})
        self.assertEqual((j2["primary"], j2["headline_arm"]), ({"model": "a-4b", "arm": "negation"}, "negation"))
        self.assertEqual(j2["settings"]["bootstrap_b"], 10000)
        self.assertEqual((j2["exclusion"]["excluded"], j2["exclusion"]["eligible_after_exclusion"],
                          j2["exclusion"]["overlap"], j2["exclusion"]["stand_in"]), (150, 210, 0, True))
        run = j2["exclusion"]["excluded_runs"][0]
        self.assertEqual((run["stand_in"], run["requested"], run["refs"]), (True, J001_EXCLUDE, 150))
        self.assertEqual((j2["prior_bound"]["records"], j2["prior_bound"]["questions"]), (150, 300))
        for name, entry in j2["models"].items():
            with self.subTest(model=name):
                self.assertEqual({(s["complete"], s["parts_finished"]) for s in entry["sets"].values()}, {(True, 5)})
                for arm in lab_j2.ARMS:
                    a = entry["arms"][arm]
                    self.assertEqual((a["verdict_word"], a["parts_finished"], a["display_class"]),
                                     ("incomplete", 5, "plumbing"))
                    self.assertEqual(a["scores"]["balanced_accuracy"], j2["lexical"]["balanced_accuracy"])
                    self.assertEqual(a["paired_lexical"]["n"], 150)
                self.assertEqual({entry_l["reading"] for entry_l in entry["lifts"].values()}, {"not_told_apart"})
        self.assertEqual(self.dry.report["j2"]["primary_result"]["verdict_word"], "incomplete")

    def test_the_summaries(self) -> None:
        root = self.dry.out / "report"
        md = (root / "report.md").read_text(encoding="utf-8")
        self.assertEqual(md.splitlines()[0], NO_MEASUREMENT_LINE)
        self.assertIn(f"#### {notes.HEADINGS['j2']}", md)
        self.assertIn(J2_STAND_IN, md)
        self.assertIn("Primary pair `a-4b` `negation`: `incomplete`.", md)
        self.assertIn("- comparisons since the loop began: 12 (12 this cycle, 0 before)", md)
        self.assertIn("| `a-4b` | `negation` | `judge_record_negation` | no | `plumbing` | 5 of 5 |", md)
        check_sources(self, md, read_json(root / "report.sources.json")["sources"], root)
        plan_md = (self.dry.out / "plan" / "summary.md").read_text(encoding="utf-8")
        check_sources(self, plan_md, read_json(self.dry.out / "plan" / "summary.sources.json")["sources"],
                      self.dry.out / "plan")
        self.assertIn("`J2` labels: source `nhtsa`, records 150", plan_md)
        self.assertIn("`J2` primary pair: `a-4b` `negation`; target balanced accuracy 0.860", plan_md)
        drawn = refs_of_labels((self.dry.out / "plan" / "prereg" / "j2" / "labels.jsonl").read_bytes())
        excluded = {line["record_ref"] for line in read_lines(self.dry.out / "plan" / "prereg" / "j2"
                                                              / "excluded.jsonl")}
        self.assertEqual(len(excluded), 300)
        for text in (md, plan_md, (root / "report.json").read_text(encoding="utf-8"), self.dry.stdout):
            assert_no_ref(self, text, drawn | excluded)


if __name__ == "__main__":
    unittest.main()

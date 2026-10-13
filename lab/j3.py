"""Accuracy test A002 (lab experiment ``j3``): the site verifier's narrow question asked of one model about a fresh
draw of real public complaints, one record at a time, under A001's negation instruction and two schema variants of
it, with a verbatim-substring grounding check, the unclear-to-no mapping and a lexical composite read beside the
shipped verdict rule.

    python -m lab.j3 run --prereg F --labels F --questions F --routing F --endpoint KEY --part K --run-id ID
        --runs-dir D --budget-seconds N

``docs/collective/A002/CHOICE-A002.md`` (amended B1 to B25 before any build) is the rule and ``BUILD-A002.md`` the
build note. This module imports the pinned modules read-only and changes none of them: the questions, the payload,
the comparators, the scoring and the paired differences are ``lab.j1``'s (J001's code, called unchanged); task N, the
mapping M, the pass test, the verdict word and the lift readings are ``lab.j2``'s (A001's code, called unchanged); the
call is ``Runtime.run`` with one of the three tasks through the same pinned server and client, with the shipped
payload; every verdict is the pinned ``verify.decide`` over the one record (``lab.j1.verdict``).

**The tasks** (rule 3), :data:`TASKS` in pass order: E (:data:`EVIDENCE_TASK`, schema :func:`evidence_schema`, the
shipped schema plus a string ``citation`` declared first, :data:`EVIDENCE_INSTRUCTIONS`, 512 tokens), R
(:data:`RATIONALE_TASK`, :func:`rationale_schema` with ``analysis`` first, :data:`RATIONALE_INSTRUCTIONS`, 512
tokens) and N (``lab.j2.NEGATION_TASK``, A001's arm C, the shipped schema, 256 tokens). E's and R's instructions are
N's text with the new field's sentences inserted before the last sentence.

**The readings** (rule 4): M (``lab.j2.MAPPING``: ``describes_predicate`` ``unclear`` read as ``no``); g, the
grounding predicate (:func:`grounded`: ``norm`` collapses whitespace runs to one space, strips and case-folds; the
citation is grounded when its ``norm`` has at least :data:`GROUNDING_MIN_CHARS` characters and is a substring of the
payload text's ``norm``); G (:func:`grounded_reply`: ``describes_predicate`` ``yes`` read as ``no`` when the citation
is not grounded); G then M. **The arms** (rule 5), :data:`ARMS`: N ``negation``, D ``negation_mapped``, E
``evidence`` (the primary arm, B1), Eg ``evidence_grounded`` (the headline secondary), Egm
``evidence_grounded_mapped``, R ``rationale``, Rm ``rationale_mapped``, C ``composite`` and Cm ``composite_mapped``;
a composite's verdict is ``confirm`` where the plan job's lexical judge confirms, else the grounded arm's
(:func:`composite_verdict`). **The ablations** (:data:`ABLATIONS`, computed in the plan job, :func:`ablation_a_lines`
and :func:`ablation_b_lines`): the same composite over the constant reply yes/no (``refute``) and over the
record-blind control. Model calls are made for the three tasks only; every arm is a view of one task's lines
(:func:`arm_lines`).

**A verdict line** (rule 15, :data:`LINE_KEYS`): J001's keys plus ``task``, the whole reply (``citation`` for E,
``analysis`` for R, null otherwise), ``grounded`` (E only), ``first_key`` (the parsed reply's first key in document
order, read at write time and never recomputed, B19) and the four readings' verdicts: ``verdict`` (shipped),
``mapped_verdict``, ``grounded_verdict`` and ``grounded_mapped_verdict`` (null for tasks without a citation), each by
the one pinned ``decide``. :func:`line_problem` recomputes every reading and ``grounded`` from the stored reply and the
payload text, and checks ``first_key`` for shape.

**The verdict word** is ``lab.j2.verdict_word`` for every arm; for C and Cm :func:`composite_verdict_word` adds the
ablation clauses (rule 12 as amended B8): ``pass_provisional`` needs the paired lower ends to both ablations above
zero, a composite that meets the four clauses but not an ablation clause is ``partial``, and one whose ablation
difference is not a number is ``incomplete``. The lifts of rule 10 (:data:`LIFTS`) and the ablation differences
(:data:`ABLATION_DIFFERENCES`) are read by ``lab.j2.lift_reading``.

**The run** judges one part's questions three times, task E first, then R, then N (rule 14). Every pin is checked
before any call (J001's, the three tasks' instructions, schema and ``max_tokens``, the mapping, the grounding and the
composite texts, the arms and the routing of the three tasks to the endpoint without escalation). A stop (budget,
server down, a signal) ends the pass it falls in and the passes after it; ``run.json`` (``kind: lab_j3_run``) records
``complete``, ``complete_by_task``, ``questions_done_by_task``, ``stopped`` and ``stopped_task``, the latency, the
output tokens and the cut replies per task, and the J001 fields. Exit codes: 0 complete; 1 stopped; 2 usage, pins or
the boundary; 130 interrupted. The budget timer and the signals are J001's (``lab.j1._Signals``).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective import stats
from mycelic.collective.edge.extract import BREAKER_AFTER, server_down
from mycelic.collective.edge.records import WindowRecord
from mycelic.collective.edge.verify import ANSWERS, JUDGE_MAX_TOKENS, judge_schema
from mycelic.collective.experiments import e1_extract
from mycelic.collective.experiments.common import (ROOT, UsageError, check_run_id, code_commit, code_dirty, code_files,
                                                   code_hash as common_code_hash, fail, measurement_flag, run_dir,
                                                   utc_clock, write_json_atomic)
from mycelic.collective.inference.client import list_models
from mycelic.collective.inference.errors import InferenceBoundaryError, InferenceError
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import ConfigError, key_problem, load_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.inference.tasks import TaskSpec
from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.loader import FrozenPack, PackError, load_pack
from mycelic.collective.schemacheck import compile as compile_schema

from . import j1 as lab_j1
from . import j2 as lab_j2
from .j2 import (MAPPING, NEGATION_INSTRUCTIONS, NEGATION_TASK, READINGS as LIFT_READINGS, TARGET_BA, VERDICT_WORDS,
                 lift_reading, mapped_reply, mapping_pin, negation_task, pass_test, verdict_word)

CLI = "lab.j3"
KIND = "j3"
EXPERIMENT = "J3"
SCHEMA_VERSION = 1
EVIDENCE_TASK = "judge_record_evidence"
RATIONALE_TASK = "judge_record_rationale"
FIELD_MAX_TOKENS = 512                                  # rule 3: E and R; N keeps the shipped 256
LAST_SENTENCE = "Judge only this record."
# rule 3, task E: the citation sentences, inserted before N's last sentence
CITATION_SENTENCES = (
    "citation: copy, exactly as it is written in the record's text, the shortest passage that reports the problem "
    "with that component or system; when the record reports no such problem, or you cannot find such a passage, "
    "write an empty string. Give the citation first, then the two answers. A yes without a passage copied from the "
    "record counts as a no."
)
# rule 3, task R: the analysis sentence, inserted before N's last sentence
ANALYSIS_SENTENCES = (
    "analysis: before answering, write two or three sentences that say which component or system the record "
    "reports a problem with, whether that is the component or system the predicate names, and what in the record's "
    "text shows it; then give the two answers."
)


def _with_field(sentences: str) -> str:
    head, found, last = NEGATION_INSTRUCTIONS.rpartition(" " + LAST_SENTENCE)
    if not found or last:
        raise AssertionError("the negation instructions do not end with the shipped last sentence")
    return f"{head} {sentences} {LAST_SENTENCE}"


EVIDENCE_INSTRUCTIONS = _with_field(CITATION_SENTENCES)
RATIONALE_INSTRUCTIONS = _with_field(ANALYSIS_SENTENCES)
TASKS = (EVIDENCE_TASK, RATIONALE_TASK, NEGATION_TASK)        # the pass order (rule 14): E first, then R, then N
TASK_LABELS = {EVIDENCE_TASK: "evidence", RATIONALE_TASK: "rationale", NEGATION_TASK: "negation"}
TASK_FIELDS: dict[str, str | None] = {EVIDENCE_TASK: "citation", RATIONALE_TASK: "analysis", NEGATION_TASK: None}
TASK_MAX_TOKENS = {EVIDENCE_TASK: FIELD_MAX_TOKENS, RATIONALE_TASK: FIELD_MAX_TOKENS, NEGATION_TASK: JUDGE_MAX_TOKENS}
ANSWER_KEYS = ("mentions_entity", "describes_predicate")
# rule 4: g's floor, a floor against a vacuous check, and G's rule
GROUNDING_MIN_CHARS = 4
GROUNDING_NORM = "collapse every run of whitespace to one space, strip, casefold; nothing else"
GROUNDING_RULE = ("describes_predicate yes is read as no when the citation is not grounded: norm(citation) has at "
                  "least the floor's characters and occurs as a substring of norm(text), the payload's record text")
GROUNDING = {"describes_predicate": {"yes": "no"}}
# rule 5: the composite and its ablations
COMPOSITE_RULE = "confirm where the lexical judge's verdict on the question is confirm, else the grounded arm's verdict"
ABLATION_A_REPLY = {"mentions_entity": "yes", "describes_predicate": "no"}
ABLATIONS = ("lexical_or_no", "lexical_or_record_blind")
ABLATION_A, ABLATION_B = ABLATIONS
LINE_READINGS = ("shipped", "mapped", "grounded", "grounded_mapped")
READING_VERDICT = {"shipped": "verdict", "mapped": "mapped_verdict", "grounded": "grounded_verdict",
                   "grounded_mapped": "grounded_mapped_verdict"}
ARMS = ("negation", "negation_mapped", "evidence", "evidence_grounded", "evidence_grounded_mapped", "rationale",
        "rationale_mapped", "composite", "composite_mapped")
ARM_LETTERS = {"negation": "N", "negation_mapped": "D", "evidence": "E", "evidence_grounded": "Eg",
               "evidence_grounded_mapped": "Egm", "rationale": "R", "rationale_mapped": "Rm", "composite": "C",
               "composite_mapped": "Cm"}
ARM_TASK = {"negation": NEGATION_TASK, "negation_mapped": NEGATION_TASK, "evidence": EVIDENCE_TASK,
            "evidence_grounded": EVIDENCE_TASK, "evidence_grounded_mapped": EVIDENCE_TASK, "rationale": RATIONALE_TASK,
            "rationale_mapped": RATIONALE_TASK, "composite": EVIDENCE_TASK, "composite_mapped": EVIDENCE_TASK}
ARM_READING = {"negation": "shipped", "negation_mapped": "mapped", "evidence": "shipped",
               "evidence_grounded": "grounded", "evidence_grounded_mapped": "grounded_mapped", "rationale": "shipped",
               "rationale_mapped": "mapped", "composite": "grounded", "composite_mapped": "grounded_mapped"}
ARM_COMPOSITE = {arm: arm in ("composite", "composite_mapped") for arm in ARMS}
COMPOSITE_OF = {"composite": "evidence_grounded", "composite_mapped": "evidence_grounded_mapped"}
HEADLINE_SECONDARY = "evidence_grounded"                 # B1: Eg's word is printed beside P's, labelled
# rule 10's lifts: (name, arm, minus arm), and the lever each reads (B18: the field carries its schema and cap)
LIFTS = (("E_minus_N", "evidence", "negation"), ("Eg_minus_E", "evidence_grounded", "evidence"),
         ("Eg_minus_N", "evidence_grounded", "negation"),
         ("Egm_minus_Eg", "evidence_grounded_mapped", "evidence_grounded"),
         ("Egm_minus_D", "evidence_grounded_mapped", "negation_mapped"), ("R_minus_N", "rationale", "negation"),
         ("Rm_minus_D", "rationale_mapped", "negation_mapped"), ("D_minus_N", "negation_mapped", "negation"),
         ("C_minus_Eg", "composite", "evidence_grounded"),
         ("Cm_minus_Egm", "composite_mapped", "evidence_grounded_mapped"))
LIFT_LEVERS = {"E_minus_N": "evidence_field", "Eg_minus_E": "grounding_check", "Eg_minus_N": "evidence_field_and_check",
               "Egm_minus_Eg": "unclear_mapping", "Egm_minus_D": "evidence_field_and_check",
               "R_minus_N": "rationale_field", "Rm_minus_D": "rationale_field", "D_minus_N": "unclear_mapping",
               "C_minus_Eg": "lexical_disjunction", "Cm_minus_Egm": "lexical_disjunction"}
# rule 10's ablation differences: (name, composite arm, ablation)
ABLATION_DIFFERENCES = (("C_minus_ablation_A", "composite", ABLATION_A), ("C_minus_ablation_B", "composite", ABLATION_B),
                        ("Cm_minus_ablation_A", "composite_mapped", ABLATION_A),
                        ("Cm_minus_ablation_B", "composite_mapped", ABLATION_B))
WITHHOLD_SHARE = lab_j1.WITHHOLD_SHARE
BOUNDARY = lab_j1.BOUNDARY
DATA_LABEL = lab_j1.DATA_LABEL
LINE_KEYS = (*lab_j1.LINE_KEYS, "task", "citation", "analysis", "grounded", "first_key", "mapped_verdict",
             "grounded_verdict", "grounded_mapped_verdict")
CODE_FILES = (*lab_j2.CODE_FILES, "lab/j3.py", "lab/j3labels.py")
PREREG_KEYS = ("schema_version", "kind", "experiment", "pack", "labels", "questions", "lexical", "prior", "ablation_a",
               "ablation_b", "tasks", "mapping", "grounding", "composite", "arms", "lifts", "ablation_differences",
               "primary", "headline_arm", "headline_secondary", "exclusion", "comparisons_before",
               "target_balanced_accuracy", "code_hash", "code_files", "code_commit", "code_dirty", "endpoints",
               "boundary", "data_label", "parts", "seed", "twins", "excluded", "bootstrap_b", "bootstrap_seed",
               "withhold_share")
EXCLUSION_KEYS = (*lab_j2.EXCLUSION_KEYS, "sources", "cycles", "consistency", "expected_excluded")
_clock = time.monotonic                 # the budget's clock (tests replace it)


class J3Error(ValueError):
    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


# --------------------------------------------------------------------------------------------------- tasks, schemas

def _answer() -> dict[str, Any]:
    return {"type": "string", "enum": list(ANSWERS)}


def evidence_schema() -> dict[str, Any]:
    """S_E: the shipped schema plus the string ``citation``, declared first (rule 3)."""
    return {"type": "object", "additionalProperties": False,
            "required": ["citation", "describes_predicate", "mentions_entity"],
            "properties": {"citation": {"type": "string"}, "mentions_entity": _answer(),
                           "describes_predicate": _answer()}}


def rationale_schema() -> dict[str, Any]:
    """S_R: the shipped schema plus the string ``analysis``, declared first (rule 3)."""
    return {"type": "object", "additionalProperties": False,
            "required": ["analysis", "describes_predicate", "mentions_entity"],
            "properties": {"analysis": {"type": "string"}, "mentions_entity": _answer(),
                           "describes_predicate": _answer()}}


def evidence_task() -> TaskSpec:
    return TaskSpec(EVIDENCE_TASK, "raw", EVIDENCE_INSTRUCTIONS, FIELD_MAX_TOKENS)


def rationale_task() -> TaskSpec:
    return TaskSpec(RATIONALE_TASK, "raw", RATIONALE_INSTRUCTIONS, FIELD_MAX_TOKENS)


def task_spec(name: str) -> TaskSpec:
    if name == EVIDENCE_TASK:
        return evidence_task()
    if name == RATIONALE_TASK:
        return rationale_task()
    if name == NEGATION_TASK:
        return negation_task()
    raise J3Error("not one of the three judge tasks")


def task_schema(name: str) -> dict[str, Any]:
    if name == EVIDENCE_TASK:
        return evidence_schema()
    if name == RATIONALE_TASK:
        return rationale_schema()
    if name == NEGATION_TASK:
        return judge_schema()
    raise J3Error("not one of the three judge tasks")


def schema_keys(name: str) -> tuple[str, ...]:
    """The property names a reply to ``name`` may begin with (the shape check of ``first_key``, B19)."""
    return tuple(task_schema(name)["properties"])


def _task_pins(task: TaskSpec) -> dict[str, Any]:
    return {"name": task.name, "data_class": task.data_class, "instructions_sha256": sha256_hex(task.instructions),
            "schema_sha256": sha256_hex(canonical_bytes(task_schema(task.name))), "max_tokens": task.max_tokens}


def task_pins() -> dict[str, Any]:
    """The three tasks' pins and the pass order; N's are ``lab.j2.task_pins()['negation']``."""
    return {TASK_LABELS[name]: _task_pins(task_spec(name)) for name in TASKS} | {"order": list(TASKS)}


# --------------------------------------------------------------------------------------------------- the readings

def norm(text: str) -> str:
    """Rule 4's ``norm``: every run of whitespace collapsed to one space, stripped, case-folded; nothing else."""
    return " ".join(text.split()).casefold()


def grounded(citation: Any, text: Any) -> bool:
    """g: the citation is grounded when its ``norm`` has at least :data:`GROUNDING_MIN_CHARS` characters and occurs
    as a substring of the payload text's ``norm``; anything that is not a string is not grounded."""
    if not isinstance(citation, str) or not isinstance(text, str):
        return False
    needle = norm(citation)
    return len(needle) >= GROUNDING_MIN_CHARS and needle in norm(text)


def answers(reply: Mapping[str, Any] | None) -> dict[str, str] | None:
    """The two answers of a reply, whatever else it holds (None stays None)."""
    if reply is None:
        return None
    return {k: reply[k] for k in ANSWER_KEYS}


def grounded_reply(reply: Mapping[str, Any] | None, is_grounded: bool | None) -> dict[str, str] | None:
    """G over the two answers: ``describes_predicate`` ``yes`` read as ``no`` when the citation is not grounded;
    ``yes`` with a grounded citation, ``no``, ``unclear`` and ``mentions_entity`` left as answered."""
    out = answers(reply)
    if out is None:
        return None
    if out["describes_predicate"] == "yes" and is_grounded is not True:
        out["describes_predicate"] = GROUNDING["describes_predicate"]["yes"]
    return out


def grounded_mapped_reply(reply: Mapping[str, Any] | None, is_grounded: bool | None) -> dict[str, str] | None:
    """G then M (the two commute: G acts on ``yes``, M on ``unclear``)."""
    return mapped_reply(grounded_reply(reply, is_grounded))


def grounding_pin() -> dict[str, Any]:
    doc = {"predicate": {"norm": GROUNDING_NORM, "min_chars": GROUNDING_MIN_CHARS, "rule": GROUNDING_RULE},
           "mapping": {k: dict(v) for k, v in GROUNDING.items()}}
    return {**doc, "sha256": sha256_hex(canonical_bytes(doc))}


def composite_verdict(lexical_verdict: str, arm_verdict: str) -> str:
    """Rule 5's composite rule, exactly: ``confirm`` when the lexical judge's verdict is ``confirm``, else the arm's."""
    return "confirm" if lexical_verdict == "confirm" else arm_verdict


def composite_pin() -> dict[str, Any]:
    doc = {"rule": COMPOSITE_RULE, "arms": dict(COMPOSITE_OF),
           "ablations": {ABLATION_A: {"reply": dict(ABLATION_A_REPLY)}, ABLATION_B: {"reply": "record_blind"}}}
    return {**doc, "sha256": sha256_hex(canonical_bytes(doc))}


def arms_doc() -> dict[str, dict[str, Any]]:
    return {arm: {"letter": ARM_LETTERS[arm], "task": ARM_TASK[arm], "reading": ARM_READING[arm],
                  "composite": ARM_COMPOSITE[arm]} for arm in ARMS}


def lifts_doc() -> dict[str, dict[str, str]]:
    return {name: {"arm": arm, "minus": minus, "lever": LIFT_LEVERS[name]} for name, arm, minus in LIFTS}


def ablation_differences_doc() -> dict[str, dict[str, str]]:
    return {name: {"arm": arm, "ablation": ablation} for name, arm, ablation in ABLATION_DIFFERENCES}


# --------------------------------------------------------------------------------------------------- lines, views

def first_key(reply: Mapping[str, Any] | None) -> str | None:
    """The parsed reply's first key in document order (``Runtime.run`` keeps it), None without a reply."""
    if reply is None:
        return None
    return next(iter(reply), None)


def verdict_line(question: Mapping[str, Any], window: WindowRecord, reply: Mapping[str, Any] | None,
                 error_kind: str | None, task: str, text: str) -> dict[str, Any]:
    """J001's verdict line plus the task, the whole reply, ``grounded``, ``first_key`` and the four readings'
    verdicts (see the module docstring). ``text`` is the payload's record text g compares the citation with."""
    if task not in TASKS:
        raise J3Error("not one of the three judge tasks")
    two = answers(reply)
    line = lab_j1.verdict_line(question, window, two, error_kind)
    field = TASK_FIELDS[task]
    value = reply.get(field) if reply is not None and field is not None else None
    line["task"] = task
    line["citation"] = value if field == "citation" else None
    line["analysis"] = value if field == "analysis" else None
    line["first_key"] = first_key(reply)
    line["mapped_verdict"] = lab_j1.verdict(window, mapped_reply(two))
    if task == EVIDENCE_TASK:
        is_grounded = grounded(line["citation"], text) if reply is not None else None
        line["grounded"] = is_grounded
        line["grounded_verdict"] = lab_j1.verdict(window, grounded_reply(two, is_grounded))
        line["grounded_mapped_verdict"] = lab_j1.verdict(window, grounded_mapped_reply(two, is_grounded))
    else:
        line["grounded"] = line["grounded_verdict"] = line["grounded_mapped_verdict"] = None
    return line


def line_problem(question: Mapping[str, Any], line: Any, text: str) -> str | None:
    """Why a stored line is not the line the runner writes for ``question`` (rule 15): its keys, its task, J001's
    check on the projected line (``lab.j1.line_problem``, unchanged), the field the task carries, ``first_key``
    checked for shape (B19), and every reading recomputed with the pinned ``decide``: the mapped verdict, ``grounded``
    from the stored citation and ``text`` (the payload text rebuilt from the plan artifact's labels), the grounded
    verdict and the grounded-then-mapped verdict."""
    if not isinstance(line, Mapping) or sorted(line) != sorted(LINE_KEYS):
        return "keys"
    task = line["task"]
    if task not in TASKS:
        return "task"
    problem = lab_j1.line_problem(question, {k: line[k] for k in lab_j1.LINE_KEYS})
    if problem is not None:
        return problem
    m, d = line["mentions_entity"], line["describes_predicate"]
    field = TASK_FIELDS[task]
    for name in ("citation", "analysis"):
        if name == field and m is not None:
            if not isinstance(line[name], str):
                return name
        elif line[name] is not None:
            return name
    if m is None:
        if line["first_key"] is not None:
            return "first_key"
        reply = None
    else:
        if line["first_key"] not in schema_keys(task):
            return "first_key"
        reply = {"mentions_entity": m, "describes_predicate": d}
    window = lab_j1.stand_in(question["record_ref"])
    if line["mapped_verdict"] != lab_j1.verdict(window, mapped_reply(reply)):
        return "mapped_verdict"
    if task != EVIDENCE_TASK:
        for key in ("grounded", "grounded_verdict", "grounded_mapped_verdict"):
            if line[key] is not None:
                return key
        return None
    is_grounded = grounded(line["citation"], text) if reply is not None else None
    if line["grounded"] is not is_grounded:
        return "grounded"
    if line["grounded_verdict"] != lab_j1.verdict(window, grounded_reply(reply, is_grounded)):
        return "grounded_verdict"
    if line["grounded_mapped_verdict"] != lab_j1.verdict(window, grounded_mapped_reply(reply, is_grounded)):
        return "grounded_mapped_verdict"
    return None


def arm_lines(lines: Iterable[Mapping[str, Any]], arm: str,
              lexical: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The j1-shaped view of an arm: the lines of its task, ``verdict`` replaced by the reading's verdict and
    ``describes_predicate`` by the reading's answer; for a composite arm the verdict is then
    :func:`composite_verdict` with the lexical judge's line of the question (``lexical``, by question id), and the
    answers stay the model's."""
    if arm not in ARMS:
        raise J3Error("not one of the nine arms")
    task, reading = ARM_TASK[arm], ARM_READING[arm]
    if ARM_COMPOSITE[arm] and lexical is None:
        raise J3Error("a composite arm needs the lexical judge's lines")
    out = []
    for line in lines:
        if line["task"] != task:
            continue
        view = {k: line[k] for k in lab_j1.LINE_KEYS}
        reply = answers(line) if line["mentions_entity"] is not None else None
        if reading != "shipped":
            view["verdict"] = line[READING_VERDICT[reading]]
            read = {"mapped": lambda: mapped_reply(reply), "grounded": lambda: grounded_reply(reply, line["grounded"]),
                    "grounded_mapped": lambda: grounded_mapped_reply(reply, line["grounded"])}[reading]()
            if read is not None:
                view["describes_predicate"] = read["describes_predicate"]
        if ARM_COMPOSITE[arm]:
            lexical_line = lexical.get(line["question_id"]) if lexical is not None else None
            if lexical_line is None:
                raise J3Error("the lexical judge has no line for a question of the composite arm")
            view["verdict"] = composite_verdict(lexical_line["verdict"], view["verdict"])
        out.append(view)
    return out


def ablation_a_lines(questions: Sequence[Mapping[str, Any]],
                     lexical: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Ablation A, ``lexical_or_no`` (rule 5): the composite over the constant reply yes/no, whose verdict under the
    pinned ``decide`` is ``refute``: ``confirm`` where the lexical judge confirms, else ``refute``."""
    by_id = {line["question_id"]: line for line in lexical}
    out = []
    for q in questions:
        line = lab_j1.verdict_line(q, lab_j1.stand_in(q["record_ref"]), ABLATION_A_REPLY, None)
        line["verdict"] = composite_verdict(by_id[q["question_id"]]["verdict"], line["verdict"])
        out.append(line)
    return out


def ablation_b_lines(questions: Sequence[Mapping[str, Any]], lexical: Iterable[Mapping[str, Any]],
                     prior: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Ablation B, ``lexical_or_record_blind`` (rule 5): the composite over the record-blind control's verdict:
    ``confirm`` where the lexical judge confirms, else the control's verdict."""
    by_id = {line["question_id"]: line for line in lexical}
    control = {line["question_id"]: line for line in prior}
    out = []
    for q in questions:
        line = dict(control[q["question_id"]])
        line["verdict"] = composite_verdict(by_id[q["question_id"]]["verdict"], line["verdict"])
        out.append(line)
    return out


def jsonl_bytes(lines: Iterable[Mapping[str, Any]]) -> bytes:
    return lab_j1.jsonl_bytes(lines)


# --------------------------------------------------------------------------------------------------- the verdict word

def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def composite_verdict_word(*, complete: bool, display_class: str | None, measurement: bool | None,
                           dropped_share: float | None, scores: Mapping[str, Any] | None,
                           paired: Mapping[str, Any] | None, bound: Mapping[str, Any] | None,
                           ablation_a: Mapping[str, Any] | None,
                           ablation_b: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Rule 12 for a composite arm: ``lab.j2.verdict_word`` with the two ablation clauses (B8): the paired lower
    ends to ablation A and to ablation B above zero. ``incomplete`` when either ablation difference is not a number;
    ``pass_provisional`` only with both clauses; a composite that meets the four clauses but not an ablation clause is
    ``partial``; else the word stands."""
    word, test = verdict_word(complete=complete, display_class=display_class, measurement=measurement,
                              dropped_share=dropped_share, scores=scores, paired=paired, bound=bound)
    low_a = ablation_a.get("ci_low") if isinstance(ablation_a, Mapping) else None
    low_b = ablation_b.get("ci_low") if isinstance(ablation_b, Mapping) else None
    clauses = {"beats_ablation_a": _num(low_a) and low_a > 0, "beats_ablation_b": _num(low_b) and low_b > 0}
    test = {**test, "ablation_clauses": clauses,
            "diff_ablation_a": ablation_a.get("mean_diff") if isinstance(ablation_a, Mapping) else None,
            "diff_ablation_a_ci_low": low_a,
            "diff_ablation_b": ablation_b.get("mean_diff") if isinstance(ablation_b, Mapping) else None,
            "diff_ablation_b_ci_low": low_b, "passed": test["passed"] and all(clauses.values())}
    if word == "incomplete":
        return word, test
    if not _num(low_a) or not _num(low_b):
        return "incomplete", test
    if word == "pass_provisional" and not all(clauses.values()):
        return "partial", test
    return word, test


# --------------------------------------------------------------------------------------------------- pins

def routing_doc(models: Mapping[str, Mapping[str, Any]], keys: Sequence[str], base_url: str) -> dict[str, Any]:
    """The only builder of J3 routing: J001's endpoints keyed by model key; with one key (a unit's) the three tasks
    are routed to it without escalation. The preregistration's routing pins every model at
    ``lab.prereg.PLACEHOLDER_BASE_URL`` and routes nothing."""
    routes = {task: {"endpoint": keys[0]} for task in TASKS} if len(keys) == 1 else {}
    return {"schema_version": 1, "endpoints": {key: lab_j1.endpoint(models[key], base_url) for key in keys},
            "routes": routes}


def code_files_list() -> list[str]:
    return code_files(CODE_FILES, ROOT)


def code_hash() -> str:
    """sha256 over :data:`CODE_FILES` (A001's files plus ``lab/j3.py`` and ``lab/j3labels.py``)."""
    return common_code_hash([ROOT / p for p in code_files_list()])


def prereg_doc(*, pack: FrozenPack, pack_ref: str, labels: Mapping[str, Any], questions: Mapping[str, Any],
               lexical_sha256: str, prior_sha256: str, ablation_a_sha256: str, ablation_b_sha256: str,
               routing: Mapping[str, Any], seed: int, parts: int, bootstrap_b: int, bootstrap_seed: int,
               primary: Mapping[str, str], comparisons_before: int) -> dict[str, Any]:
    """``prereg/j3/prereg.json``: everything a unit checks before its first call, and the settings of the rule."""
    exclusion = labels["exclusion"]
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j3_prereg", "experiment": EXPERIMENT,
            "pack": lab_j1.pack_pins(pack, pack_ref),
            "labels": {k: labels[k] for k in ("source", "n", "seed", "records", "claims", "sha256", "available")},
            "questions": {k: questions[k] for k in ("seed", "parts", "records", "questions", "part_records",
                                                     "negative_draw", "sha256")},
            "lexical": {"sha256": lexical_sha256},
            "prior": {"sha256": prior_sha256, "rule": lab_j1.PRIOR_RULE, "top": list(lab_j1.PRIOR_TOP),
                      "source": lab_j1.PRIOR_SOURCE},
            "ablation_a": {"sha256": ablation_a_sha256, "name": ABLATION_A, "reply": dict(ABLATION_A_REPLY)},
            "ablation_b": {"sha256": ablation_b_sha256, "name": ABLATION_B, "reply": "record_blind"},
            "tasks": task_pins(), "mapping": mapping_pin(), "grounding": grounding_pin(), "composite": composite_pin(),
            "arms": arms_doc(), "lifts": lifts_doc(), "ablation_differences": ablation_differences_doc(),
            "primary": {"model": primary["model"], "arm": primary["arm"]}, "headline_arm": primary["arm"],
            "headline_secondary": HEADLINE_SECONDARY,
            "exclusion": {k: exclusion[k] for k in EXCLUSION_KEYS},
            "comparisons_before": comparisons_before, "target_balanced_accuracy": TARGET_BA,
            "code_hash": code_hash(), "code_files": code_files_list(), "code_commit": code_commit(),
            "code_dirty": code_dirty(["mycelic/collective", "lab"]), "endpoints": lab_j1.endpoint_pins(routing),
            "boundary": BOUNDARY, "data_label": DATA_LABEL, "parts": parts, "seed": seed,
            "twins": [list(t) for t in lab_j1.TWINS], "excluded": list(lab_j1.EXCLUDED), "bootstrap_b": bootstrap_b,
            "bootstrap_seed": bootstrap_seed, "withhold_share": WITHHOLD_SHARE}


# --------------------------------------------------------------------------------------------------- the run

def _read(path: str | Path, what: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {what} ({exc.__class__.__name__})") from None


def _pin(name: str, pinned: Any, now: Any) -> None:
    if pinned != now:
        raise UsageError(f"{name} differs from the preregistration") from None


def read_prereg(path: str | Path) -> tuple[dict[str, Any], str]:
    data = _read(path, "the preregistration")
    failed = False
    try:
        doc = strict_load(data)
    except StrictJsonError:
        failed = True
    if failed or not isinstance(doc, dict) or sorted(doc) != sorted(PREREG_KEYS) or doc["kind"] != "lab_j3_prereg" \
            or doc["schema_version"] != SCHEMA_VERSION:
        raise UsageError("the preregistration is not a J3 prereg.json") from None
    return doc, sha256_hex(data)


class _Checked:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


def _check(args: argparse.Namespace) -> _Checked:
    check_run_id(args.run_id)
    if args.budget_seconds < 1:
        raise UsageError("--budget-seconds must be >= 1") from None
    prereg, prereg_sha = read_prereg(args.prereg)
    if not 1 <= args.part <= prereg["parts"]:
        raise UsageError("--part is not one of the preregistered parts") from None
    try:
        pack = load_pack(ROOT / prereg["pack"]["ref"])
    except (PackError, OSError):
        raise UsageError("the preregistered pack does not load") from None
    _pin("the pack's vocabulary hash", prereg["pack"]["vocabulary_hash"], pack.vocabulary_hash)
    _pin("the pack's config hash", prereg["pack"]["config_hash"], pack.config_hash)
    _pin("the code hash", prereg["code_hash"], code_hash())
    _pin("the judge tasks", prereg["tasks"], task_pins())
    _pin("the mapping", prereg["mapping"], mapping_pin())
    _pin("the grounding", prereg["grounding"], grounding_pin())
    _pin("the composite", prereg["composite"], composite_pin())
    _pin("the arms", prereg["arms"], arms_doc())
    _pin("the lifts", prereg["lifts"], lifts_doc())
    labels, labels_sha = e1_extract.read_labels(args.labels, pack, Canonicaliser(pack))
    _pin("the labels' sha256", prereg["labels"]["sha256"], labels_sha)
    e1_extract.check_data_label(labels, prereg["data_label"])
    questions_bytes = _read(args.questions, "the questions")
    _pin("the questions' sha256", prereg["questions"]["sha256"], sha256_hex(questions_bytes))
    try:
        questions = lab_j1.read_questions(questions_bytes)
    except (lab_j1.J1Error, StrictJsonError):
        raise UsageError("the questions file is malformed") from None
    _pin("the boundary", prereg["boundary"], BOUNDARY)
    _pin("the data label", prereg["data_label"], DATA_LABEL)
    try:
        config = load_routing(args.routing, tasks=TASKS, check_env=False)
    except ConfigError as exc:
        raise UsageError(f"routing: {exc}") from None
    pinned = {e["name"]: e for e in prereg["endpoints"]}
    if args.endpoint not in pinned or args.endpoint not in config.endpoints:
        raise UsageError("the endpoint is not in the preregistration or the routing") from None
    ep = config.endpoints[args.endpoint]
    _pin("the endpoint's pins", {k: v for k, v in pinned[args.endpoint].items() if k != "name"},
         e1_extract.endpoint_pins(ep))
    for task in TASKS:
        route = config.routes.get(task)
        if route is None or route.endpoint != args.endpoint or route.escalate_to is not None:
            raise UsageError("the routing must route the three judge tasks to the endpoint without escalation") \
                from None
    if ep.provider != "openai_compat":
        raise UsageError("the endpoint is not openai_compat: J3 judges with real servers only") from None
    problem = key_problem(ep, os.environ)
    if problem is not None:
        raise UsageError(f"routing: {problem}") from None
    mine = [q for q in questions if q["part"] == args.part]
    records = {r["record_ref"]: r for r, _ in labels}
    if any(q["record_ref"] not in records for q in mine):
        raise UsageError("a question names a record the labels do not hold") from None
    out_dir = run_dir(args.runs_dir, KIND, args.run_id)
    return _Checked(prereg=prereg, prereg_sha=prereg_sha, pack=pack, labels_sha=labels_sha, records=records,
                    questions=mine, questions_sha=prereg["questions"]["sha256"], config=config, endpoint=ep,
                    out_dir=out_dir)


def _file_sha(path: Path) -> str | None:
    return sha256_hex(path.read_bytes()) if path.exists() else None


def _base(args: argparse.Namespace, c: _Checked) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j3_run", "experiment": EXPERIMENT, "run_id": args.run_id,
            "endpoint": args.endpoint, "part": args.part, "parts": c.prereg["parts"], "pass_order": list(TASKS),
            "questions_planned": len(TASKS) * len(c.questions),
            "questions_planned_by_task": {task: len(c.questions) for task in TASKS},
            "prereg_sha256": c.prereg_sha,
            "pinned": {"labels_sha256": c.labels_sha, "questions_sha256": c.questions_sha,
                       "vocabulary_hash": c.pack.vocabulary_hash, "config_hash": c.pack.config_hash,
                       "code_hash": c.prereg["code_hash"], "tasks": c.prereg["tasks"],
                       "mapping": c.prereg["mapping"], "grounding": c.prereg["grounding"],
                       "composite": c.prereg["composite"], **e1_extract.endpoint_pins(c.endpoint)},
            "boundary": BOUNDARY, "data_label": DATA_LABEL, "model_requested": c.endpoint.model,
            "budget_seconds": args.budget_seconds, "code_commit": code_commit(), "started_at": utc_clock()}


def _latency(rows: Sequence[Mapping[str, Any]], task: str | None = None) -> dict[str, float | None]:
    values = [r["latency_ms"] for r in rows if r["ok"] and r["attempt"] >= 1 and r["latency_ms"] is not None
              and (task is None or r["task"] == task)]
    return {"p50": stats.percentile(values, 50), "p95": stats.percentile(values, 95)}


def token_figures(rows: Sequence[Mapping[str, Any]], task: str) -> dict[str, Any]:
    """Rule 13's per-task token figures from the ledger: the attempts made, the output tokens' median and 95th
    percentile over the attempts that reported them, and the replies cut at the cap (``finish_reason`` ``length``)."""
    mine = [r for r in rows if r["task"] == task and r["attempt"] >= 1]
    tokens = [r["tokens_out"] for r in mine if r.get("tokens_out") is not None]
    return {"attempts": len(mine), "with_tokens": len(tokens), "tokens_out_p50": stats.percentile(tokens, 50),
            "tokens_out_p95": stats.percentile(tokens, 95),
            "cut_at_cap": sum(1 for r in mine if r.get("finish_reason") == "length")}


def _execute(args: argparse.Namespace, c: _Checked) -> int:
    out = c.out_dir
    base = _base(args, c)
    out.mkdir(parents=True)
    empty = {"complete": False, "complete_by_task": {task: False for task in TASKS}, "stopped": None,
             "stopped_task": None, "questions_done": 0, "questions_done_by_task": {task: 0 for task in TASKS},
             "measurement": False, "models_listed": None, "listed_fake": None, "models_served": [],
             "verdicts_sha256": None, "ledger_sha256": None, "failures": None, "failures_by_task": None,
             "latency_ms_p50": None, "latency_ms_p95": None, "latency_ms_by_task": None, "tokens_by_task": None,
             "finished_at": None}
    write_json_atomic(out / "run.json", {**base, **empty})
    try:
        rt = Runtime(c.config, boundary=BOUNDARY, ledger_path=out / "ledger.jsonl", run_id=args.run_id,
                     clock=utc_clock, data_label=DATA_LABEL)
    except ConfigError as exc:
        return fail(str(exc))
    schemas = {name: compile_schema(task_schema(name)) for name in TASKS}
    specs = {name: task_spec(name) for name in TASKS}
    for spec in specs.values():
        if rt.exemption(spec) is not None:      # the endpoint is the runtime's own boundary: never an exemption
            rt.close()
            return fail("a judge route would leave the boundary under an exemption")
    lines: list[dict[str, Any]] = []
    done: dict[str, int] = {task: 0 for task in TASKS}
    stopped: str | None = None
    stopped_task: str | None = None
    interrupted = boundary = False
    listed: dict[str, Any] = {"ok": False, "ids": [], "fake": None}
    t0 = _clock()
    signals = lab_j1._Signals(args.budget_seconds)
    try:
        listed = list_models(c.endpoint)
        down = 0
        with open(out / "verdicts.jsonl", "w", encoding="utf-8", newline="\n") as fh:
            for t, task_name in enumerate(TASKS):
                if stopped is not None or boundary:
                    break
                task = specs[task_name]
                for i, q in enumerate(c.questions):
                    if signals.pending is not None:
                        raise lab_j1._Stop(signals.pending)
                    if _clock() - t0 >= args.budget_seconds:
                        stopped, stopped_task = "budget", task_name
                        break
                    if down >= BREAKER_AFTER:
                        stopped, stopped_task = "server_down", task_name
                        break
                    record = c.records[q["record_ref"]]
                    window = lab_j1.window_record(record)
                    payload = lab_j1.payload(c.pack, q, record)
                    reply, error_kind, down_now = None, None, False
                    try:
                        signals.enter_call()
                        reply = rt.run(task, payload, schemas[task_name], ref=f"j3-{args.part}-{t}-{i:04d}")
                    except InferenceBoundaryError:
                        boundary = True
                        break
                    except InferenceError as err:
                        error_kind = err.kind
                        down_now = server_down(err, rt, task_name)
                    except lab_j1._Stop as stop:
                        stopped_task = task_name
                        raise stop
                    finally:
                        signals.in_call = False
                    down = down + 1 if down_now else 0
                    line = verdict_line(q, window, reply, error_kind, task_name, payload["record"]["text"])
                    fh.write(canonical_dumps(line) + "\n")
                    fh.flush()
                    lines.append(line)
                    done[task_name] += 1
    except lab_j1._Stop as stop:
        stopped = stop.reason
        interrupted = stop.reason == "interrupted"
    except KeyboardInterrupt:
        interrupted = True
        stopped = "interrupted"
    finally:
        signals.close()
        rt.close()
    if stopped is not None and stopped_task is None:
        stopped_task = next((task for task in TASKS if done[task] < len(c.questions)), None)
    rows = read_ledger(out / "ledger.jsonl") if (out / "ledger.jsonl").exists() else []
    stop_index = TASKS.index(stopped_task) if stopped_task in TASKS else len(TASKS)
    complete_by_task = {task: (not interrupted and not boundary and done[task] == len(c.questions)
                               and (stopped is None or TASKS.index(task) < stop_index))
                        for task in TASKS}
    complete = all(complete_by_task.values()) and stopped is None and not interrupted and not boundary
    overall = _latency(rows)
    doc = {**base, "complete": complete, "complete_by_task": complete_by_task, "stopped": stopped,
           "stopped_task": stopped_task, "questions_done": len(lines), "questions_done_by_task": dict(done),
           "measurement": measurement_flag([c.endpoint], rows, bool(listed.get("fake"))),
           "models_listed": listed["ids"] if listed.get("ok") else None, "listed_fake": listed.get("fake"),
           "models_served": sorted({r["model_served"] for r in rows if r["model_served"]}),
           "verdicts_sha256": _file_sha(out / "verdicts.jsonl"), "ledger_sha256": _file_sha(out / "ledger.jsonl"),
           "failures": lab_j1._failures(lines),
           "failures_by_task": {task: lab_j1._failures([x for x in lines if x["task"] == task]) for task in TASKS},
           "latency_ms_p50": overall["p50"], "latency_ms_p95": overall["p95"],
           "latency_ms_by_task": {task: _latency(rows, task) for task in TASKS},
           "tokens_by_task": {task: token_figures(rows, task) for task in TASKS}, "finished_at": utc_clock()}
    write_json_atomic(out / "run.json", doc)
    if boundary:
        return fail("the runtime refused a judge route at its boundary")
    if interrupted:
        print(f"j3 run: interrupted after {len(lines)} questions; run.json says complete=false", file=sys.stderr)
        return 130
    by_task = " ".join(f"{TASK_LABELS[task]}={done[task]}/{len(c.questions)}" for task in TASKS)
    print(f"j3 run: {args.endpoint} part {args.part}: {len(lines)} of {base['questions_planned']} questions "
          f"({by_task}); complete={str(complete).lower()} stopped={stopped} "
          f"measurement={str(doc['measurement']).lower()}")
    return 0 if complete else 1


def cmd_run(args: argparse.Namespace) -> int:
    try:
        checked = _check(args)
    except UsageError as exc:
        return fail(str(exc))
    return _execute(args, checked)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.j3",
                                description="Accuracy test A002: one part of one model, the three judge tasks.")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="judge one part's questions with one endpoint, task E first, then R, then N")
    run.add_argument("--prereg", required=True)
    run.add_argument("--labels", required=True)
    run.add_argument("--questions", required=True)
    run.add_argument("--routing", required=True)
    run.add_argument("--endpoint", required=True)
    run.add_argument("--part", type=int, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--runs-dir", default="runs")
    run.add_argument("--budget-seconds", type=int, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())

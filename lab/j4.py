"""Accuracy test A003 (lab experiment ``j4``): the site verifier's narrow question asked of one model about a fresh
draw of real public complaints, one record at a time, under the earlier cycle's base task with and without the
drafted terms in the payload, read beside the drafted lexical judge, the hand-pack lexical judge and the drafted
term-presence judge.

    python -m lab.j4 run --prereg F --labels F --questions F --terms F --routing F --endpoint KEY --part K
        --run-id ID --runs-dir D --budget-seconds N

``docs/collective/A003/CHOICE-A003.md`` (amended C1 to C32 before any build and T1 to T11 after the term run) is the
rule and ``BUILD-A003.md`` the build note. This module imports the pinned modules read-only and changes none of them:
the questions, the shipped payload, the comparators, the scoring and the paired differences are ``lab.j1``'s (J001's
code, called unchanged); task N, the mapping M, the pass test, the verdict word under lever 5's text alone and the lift
readings are ``lab.j2``'s (A001's code, called unchanged); task E, its schema, the grounding check G and the first
key are ``lab.j3``'s (A002's code, called unchanged); the term set's P_D is ``lab.jt``'s (the term run's code, frozen
since the term run, C29); the term-presence judge reads ``mycelic.collective.onboard.check.term_presence`` unchanged.
Every verdict is the pinned ``verify.decide`` over the one record (``lab.j1.verdict``).

**The base task and the two tasks** (rule 3, C32). The request's ``base_task`` names the branch (:data:`BASES`):
``negation`` (branch N, in force: A001's task N, ``lab.j2.negation_task``) or ``evidence`` (branch E: A002's task E,
``lab.j3.evidence_task``). Task B is the base task, imported unchanged. Task D (:func:`drafted_task`) has the base's
name with ``_terms``, the base's data class, schema and output cap, and the base's instructions with the terms
sentence (:data:`TERMS_SENTENCE`, "The terms", item 8, verbatim) inserted before the base's last sentence by one
construction, ``base.rpartition(" Judge only this record.")[0] + " " + TERMS_SENTENCE + " Judge only this record."``,
refused unless the base ends with that sentence (:func:`drafted_instructions`). Task D's payload (:func:`payload`) is
the shipped payload (``lab.j1.payload``) with one key added to its question block, ``predicate_terms``: the list
T[predicate] of the asked predicate in the drafter's order, the empty list when T has none.

**The readings and the arms** (rules 4 and 5). Under branch N the reading is M (``lab.j2.mapped_reply``), under branch
E it is G (``lab.j3.grounded_reply``). Four arms per branch (:data:`ARMS`): D ``<base>_terms`` (task D as answered,
P's arm), Dr ``<base>_terms_<reading>`` (task D under the reading, the headline secondary), B ``<base>`` (task B as
answered, the control) and Br ``<base>_<reading>``. In the confirmation case of C1 (base ``evidence``, primary arm
``evidence``) P is (a-4b, B), D is the headline secondary and the pass order puts task B first
(:func:`pass_order`). The lifts of rule 10 are :data:`LIFTS`.

**A verdict line** (rule 16, :data:`LINE_KEYS`): J001's keys plus ``task``, the citation (branch E; null otherwise),
``grounded`` (branch E), ``first_key`` (the parsed reply's first key as written, A002's B19), the branch reading's
verdict (``mapped_verdict`` under N, ``grounded_verdict`` under E; the other null), and, for task D, ``terms_count``
and ``terms_sha256`` (the count and the sha256 of the canonical JSON of the list the payload carried; never a term;
null for task B). :func:`line_problem` recomputes the reading, ``grounded`` and the terms' count and sha256.

**The comparators** (rule 8, C5). The drafted lexical judge is ``lab.j1.lexical_verdicts`` over P_D
(:func:`drafted_pack`), the hand-pack lexical judge the same over the hand pack, and the drafted term-presence judge
(:func:`presence_verdicts`): ``mentions_entity`` as the drafted lexical judge answers the question, and
``describes_predicate`` yes when any term of T[predicate] occurs word-bounded in a folded sentence of the payload's
record text (``check.term_presence``), no otherwise; its verdict by the pinned ``decide``.

**The verdict word** (rule 13, C6, C19): :func:`verdict_word` is ``lab.j2.verdict_word`` over the scores, the
drafted-judge difference and the bound (the word under lever 5's text alone), then the hand clause and the
term-presence clause; :func:`pass_test` is ``lab.j2.pass_test`` with the two clauses added, ``passed`` only when all
six hold.

**The run** judges one part's questions twice, task D first, then task B (rule 15; task B first in the confirmation
case, C1). Every pin is checked before any call: J001's, the hand pack's hashes, the term set's sha256 (``--terms``
hashing to the preregistration's pin), P_D rebuilt from it with the pinned hashes, both tasks' instructions, schema and
``max_tokens``, the reading, the arms, the lifts and the routing of both tasks to the endpoint without escalation. A
stop (budget, server down, a signal) ends the pass it falls in and the pass after it; ``run.json`` (``kind:
lab_j4_run``) records ``complete``, ``complete_by_task``, ``questions_done_by_task``, ``stopped`` and
``stopped_task``, the latency, the output tokens and the cut replies per task, and the J001 fields. Exit codes: 0
complete; 1 stopped; 2 usage, pins or the boundary; 130 interrupted. The budget timer and the signals are J001's
(``lab.j1._Signals``), as A001's and A002's.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective.edge.extract import BREAKER_AFTER, server_down
from mycelic.collective.edge.records import WindowRecord
from mycelic.collective.edge.verify import JUDGE_MAX_TOKENS, judge_schema
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
from mycelic.collective.onboard.check import term_presence
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.loader import FrozenPack, PackError, load_pack
from mycelic.collective.schemacheck import compile as compile_schema

from . import j1 as lab_j1
from . import j2 as lab_j2
from . import j3 as lab_j3
from . import jt as lab_jt
from .j2 import NEGATION_TASK, TARGET_BA, mapped_reply, mapping_pin, negation_task
from .j3 import (EVIDENCE_TASK, FIELD_MAX_TOKENS, LAST_SENTENCE, answers, evidence_schema, evidence_task, first_key,
                 grounded, grounded_reply, grounding_pin)

# imported unchanged ("Two new kinds"): the readings' rules, the base instructions, the words and the lift reading
MAPPING = lab_j2.MAPPING
GROUNDING = lab_j3.GROUNDING
NEGATION_INSTRUCTIONS = lab_j2.NEGATION_INSTRUCTIONS
EVIDENCE_INSTRUCTIONS = lab_j3.EVIDENCE_INSTRUCTIONS
VERDICT_WORDS = lab_j2.VERDICT_WORDS
LIFT_READINGS = lab_j2.READINGS
lift_reading = lab_j2.lift_reading
norm = lab_j3.norm
CLI = "lab.j4"
KIND = "j4"
EXPERIMENT = "J4"
SCHEMA_VERSION = 1
# "The terms", item 8: the sentence task D's instructions add, verbatim
TERMS_SENTENCE = (
    "predicate_terms: words and phrases that earlier complaints filed under that component or system used more often "
    "than complaints filed under other components or systems. They show how such a problem is usually written, not "
    "whether this record reports one: a record can report the problem in other words, and a term can appear in a "
    "record that reports no problem with that component or system. Judge the record's own statements."
)
TERMS_KEY = "predicate_terms"                       # the one key task D's payload adds to its question block
TERMS_SUFFIX = "_terms"
# the base tasks of the adaptation rule: the request's base_task -> (task B's name, the reading's name)
BASES = ("negation", "evidence")
BASE_TASK = {"negation": NEGATION_TASK, "evidence": EVIDENCE_TASK}
DRAFTED_TASK = {base: BASE_TASK[base] + TERMS_SUFFIX for base in BASES}
READING = {"negation": "mapped", "evidence": "grounded"}
READING_VERDICT = {"shipped": "verdict", "mapped": "mapped_verdict", "grounded": "grounded_verdict"}
ALL_TASKS = (DRAFTED_TASK["negation"], NEGATION_TASK, DRAFTED_TASK["evidence"], EVIDENCE_TASK)
TASK_BASE = {**{BASE_TASK[b]: b for b in BASES}, **{DRAFTED_TASK[b]: b for b in BASES}}
TASK_DRAFTED = {task: task in DRAFTED_TASK.values() for task in ALL_TASKS}
TASK_FIELDS: dict[str, str | None] = {task: ("citation" if TASK_BASE[task] == "evidence" else None)
                                      for task in ALL_TASKS}
TASK_MAX_TOKENS = {task: (FIELD_MAX_TOKENS if TASK_BASE[task] == "evidence" else JUDGE_MAX_TOKENS)
                   for task in ALL_TASKS}
# rule 5: the four arms per branch, in the rule's table order D, Dr, B, Br
LETTERS = ("D", "Dr", "B", "Br")
ARMS = {"negation": ("negation_terms", "negation_terms_mapped", "negation", "negation_mapped"),
        "evidence": ("evidence_terms", "evidence_terms_grounded", "evidence", "evidence_grounded")}
ARM_LETTERS = {arm: letter for base in BASES for letter, arm in zip(LETTERS, ARMS[base])}
ARM_BASE = {arm: base for base in BASES for arm in ARMS[base]}
ARM_TASK = {arm: (DRAFTED_TASK[ARM_BASE[arm]] if ARM_LETTERS[arm] in ("D", "Dr") else BASE_TASK[ARM_BASE[arm]])
            for arm in ARM_LETTERS}
ARM_READING = {arm: (READING[ARM_BASE[arm]] if ARM_LETTERS[arm] in ("Dr", "Br") else "shipped") for arm in ARM_LETTERS}
CONFIRMATION_ARM = "evidence"                      # C1: (a-4b, evidence), arm B under branch E
# rule 10's lifts: (name, letter, minus letter); the lever each reads
LIFTS = (("D_minus_B", "D", "B"), ("Dr_minus_Br", "Dr", "Br"), ("Dr_minus_D", "Dr", "D"), ("Br_minus_B", "Br", "B"))
LIFT_LEVERS = {"negation": {"D_minus_B": "drafted_terms", "Dr_minus_Br": "drafted_terms",
                            "Dr_minus_D": "unclear_mapping", "Br_minus_B": "unclear_mapping"},
               "evidence": {"D_minus_B": "drafted_terms", "Dr_minus_Br": "drafted_terms",
                            "Dr_minus_D": "grounding_check", "Br_minus_B": "grounding_check"}}
# rule 8: the comparators computed in the plan job (prereg/j4/<name>.jsonl); the three lexical judges first
LEXICAL_DRAFTED, LEXICAL_HAND, PRESENCE_DRAFTED, PRIOR = "lexical_drafted", "lexical", "presence_drafted", "prior"
COMPARATORS = (LEXICAL_DRAFTED, LEXICAL_HAND, PRESENCE_DRAFTED, PRIOR)
LEXICAL_JUDGES = (LEXICAL_DRAFTED, LEXICAL_HAND, PRESENCE_DRAFTED)
# rule 10: each arm minus each lexical judge, and the pass test's clause each difference carries (rule 13, C5, C6)
JUDGE_CLAUSES = {LEXICAL_DRAFTED: "beats_lexical", LEXICAL_HAND: "beats_hand_lexical",
                 PRESENCE_DRAFTED: "beats_term_presence"}
# rules 8 and 14: the lexical judges paired with each other, record by record (name, judge, minus judge)
JUDGE_PAIRS = (("drafted_minus_hand", LEXICAL_DRAFTED, LEXICAL_HAND),
               ("presence_minus_hand", PRESENCE_DRAFTED, LEXICAL_HAND),
               ("presence_minus_drafted", PRESENCE_DRAFTED, LEXICAL_DRAFTED))
PRESENCE_RULE = ("mentions_entity as the drafted lexical judge answers the question; describes_predicate yes when any "
                 "term of T[predicate] occurs word-bounded in a folded sentence of the payload's record text "
                 "(onboard.check.term_presence), no otherwise; the verdict by the pinned decide")
COMPARISONS_THIS_CYCLE = 4                          # rule 12: 4 arms x 1 model x 1 cycle
# rule 14: the earlier cycle's own figures for the controls B and Br, beside which this draw's are reported as a
# between-draw difference that decides nothing: A001 run 1's arms C and D under branch N (lab run 38114522160), A002
# run 1's arms E and Eg under branch E (lab run 38439612256), balanced accuracy with its 95% interval, as
# CHOICE-A003.md cites them ("What the evidence says"; the amendment before any build)
EARLIER_ARMS = {
    "negation": {"cycle": "A001", "run_id": 38114522160,
                 "B": {"arm": "negation", "balanced_accuracy": 0.577, "ci_low": 0.530, "ci_high": 0.623},
                 "Br": {"arm": "negation_mapped", "balanced_accuracy": 0.653, "ci_low": 0.610, "ci_high": 0.697}},
    "evidence": {"cycle": "A002", "run_id": 38439612256,
                 "B": {"arm": "evidence", "balanced_accuracy": 0.540, "ci_low": 0.503, "ci_high": 0.577},
                 "Br": {"arm": "evidence_grounded", "balanced_accuracy": 0.553, "ci_low": 0.517, "ci_high": 0.590}},
}
WITHHOLD_SHARE = lab_j1.WITHHOLD_SHARE
BOUNDARY = lab_j1.BOUNDARY
DATA_LABEL = lab_j1.DATA_LABEL
TERMS_FILE = "prereg/j4/terms.json"                 # the plan artifact's copy of the term run's terms.json
LINE_KEYS = (*lab_j1.LINE_KEYS, "task", "citation", "grounded", "first_key", "mapped_verdict", "grounded_verdict",
             "terms_count", "terms_sha256")
CODE_FILES = (*lab_j3.CODE_FILES, "lab/jt.py", "lab/j4.py", "lab/j4labels.py", "lab/j4terms.py",
              "mycelic/collective/onboard/check.py")
PREREG_KEYS = ("schema_version", "kind", "experiment", "base_task", "pack", "drafted_pack", "terms", "labels",
               "questions", "lexical", "lexical_drafted", "presence_drafted", "prior", "tasks", "reading", "arms",
               "lifts", "comparators", "primary", "confirmation", "headline_arm", "headline_secondary",
               "other_reading_arm", "exclusion", "comparisons_before", "comparisons_this_cycle",
               "target_balanced_accuracy", "code_hash", "code_files", "code_commit", "code_dirty", "endpoints",
               "boundary", "data_label", "parts", "seed", "twins", "excluded", "bootstrap_b", "bootstrap_seed",
               "withhold_share")
EXCLUSION_KEYS = (*lab_j3.EXCLUSION_KEYS, "split")
TERMS_PIN_KEYS = ("file", "sha256", "run_id", "artifact", "vocabulary_hash", "cutoff", "jt_code_hash")
_clock = time.monotonic                 # the budget's clock (tests replace it)


class J4Error(ValueError):
    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


def _base(base: str) -> str:
    if base not in BASES:
        raise J4Error("the base task is negation or evidence")
    return base


# --------------------------------------------------------------------------------------------------- tasks, schemas

def drafted_instructions(base_instructions: str) -> str:
    """C32's construction: the base's text with :data:`TERMS_SENTENCE` before its last sentence, refused unless the
    base ends with that sentence."""
    head, found, last = base_instructions.rpartition(" " + LAST_SENTENCE)
    if not found or last:
        raise J4Error("the base instructions do not end with the shipped last sentence")
    return head + " " + TERMS_SENTENCE + " " + LAST_SENTENCE


def base_task(base: str) -> TaskSpec:
    """Task B: A001's task N under branch N, A002's task E under branch E, imported unchanged."""
    return negation_task() if _base(base) == "negation" else evidence_task()


def drafted_task(base: str) -> TaskSpec:
    """Task D (rule 3): the base's name with ``_terms``, its data class and ``max_tokens``, the instructions of
    :func:`drafted_instructions`."""
    b = base_task(base)
    return TaskSpec(DRAFTED_TASK[base], b.data_class, drafted_instructions(b.instructions), b.max_tokens)


def task_spec(name: str) -> TaskSpec:
    if name not in TASK_BASE:
        raise J4Error("not one of the four judge tasks")
    base = TASK_BASE[name]
    return drafted_task(base) if TASK_DRAFTED[name] else base_task(base)


def task_schema(name: str) -> dict[str, Any]:
    """The base's schema, unchanged, for both of its tasks: the shipped schema under N, S_E under E."""
    if name not in TASK_BASE:
        raise J4Error("not one of the four judge tasks")
    return evidence_schema() if TASK_BASE[name] == "evidence" else judge_schema()


def schema_keys(name: str) -> tuple[str, ...]:
    """The property names a reply to ``name`` may begin with (the shape check of ``first_key``, B19)."""
    return tuple(task_schema(name)["properties"])


def tasks(base: str) -> tuple[str, str]:
    """(task D, task B) of the branch."""
    return DRAFTED_TASK[_base(base)], BASE_TASK[base]


def is_confirmation(base: str, primary_arm: str) -> bool:
    """C1's confirmation case: base ``evidence`` and primary arm ``evidence`` (arm B under branch E)."""
    return _base(base) == "evidence" and primary_arm == CONFIRMATION_ARM


def primary_arms(base: str) -> tuple[str, ...]:
    """The arms a request may name as primary: ``<base>_terms``, and ``evidence`` with base ``evidence`` (C1)."""
    d = ARMS[_base(base)][0]
    return (d, CONFIRMATION_ARM) if base == "evidence" else (d,)


def _primary(base: str, primary_arm: str) -> str:
    if primary_arm not in primary_arms(base):
        raise J4Error("the primary arm is <base>_terms, or evidence with base evidence")
    return primary_arm


def pass_order(base: str, primary_arm: str) -> tuple[str, str]:
    """Rule 15: task D first, then task B; task B first in the confirmation case (C1): P's set first."""
    d, b = tasks(base)
    return (b, d) if is_confirmation(base, _primary(base, primary_arm)) else (d, b)


def arm_of(base: str, letter: str) -> str:
    return ARMS[_base(base)][LETTERS.index(letter)]


def headline_secondary(base: str, primary_arm: str) -> str:
    """Dr when P is (a-4b, D); D in the confirmation case (C1)."""
    return arm_of(base, "D" if is_confirmation(base, _primary(base, primary_arm)) else "Dr")


def other_reading_arm(base: str, primary_arm: str) -> str:
    """P's arm under the branch reading: the primary under the other reading of the section 7 default (Dr for D, Br
    for B in the confirmation case)."""
    return arm_of(base, "Br" if is_confirmation(base, _primary(base, primary_arm)) else "Dr")


def _task_pins(task: TaskSpec) -> dict[str, Any]:
    return {"name": task.name, "data_class": task.data_class, "instructions_sha256": sha256_hex(task.instructions),
            "schema_sha256": sha256_hex(canonical_bytes(task_schema(task.name))), "max_tokens": task.max_tokens}


def task_pins(base: str, primary_arm: str) -> dict[str, Any]:
    """Both tasks' pins (B's are the earlier cycle's own: ``lab.j2.task_pins()['negation']`` under N,
    ``lab.j3.task_pins()['evidence']`` under E), the base and the pass order."""
    return {"D": _task_pins(drafted_task(base)), "B": _task_pins(base_task(base)), "base": base,
            "order": list(pass_order(base, primary_arm))}


def reading_pin(base: str) -> dict[str, Any]:
    """The branch reading's pin: M (``lab.j2.mapping_pin``) under N, G (``lab.j3.grounding_pin``) under E."""
    name = READING[_base(base)]
    return {"name": name, "pin": mapping_pin() if name == "mapped" else grounding_pin()}


def arms_doc(base: str) -> dict[str, dict[str, Any]]:
    return {arm: {"letter": ARM_LETTERS[arm], "task": ARM_TASK[arm], "reading": ARM_READING[arm]}
            for arm in ARMS[_base(base)]}


def lifts_doc(base: str) -> dict[str, dict[str, str]]:
    return {name: {"arm": arm_of(base, a), "minus": arm_of(base, b), "lever": LIFT_LEVERS[base][name]}
            for name, a, b in LIFTS}


def lifts(base: str) -> tuple[tuple[str, str, str], ...]:
    """Rule 10's lifts of the branch as (name, arm, minus arm)."""
    return tuple((name, arm_of(base, a), arm_of(base, b)) for name, a, b in LIFTS)


def comparators_doc() -> dict[str, Any]:
    return {"order": list(COMPARATORS), "clauses": dict(JUDGE_CLAUSES),
            "pairs": {name: {"judge": a, "minus": b} for name, a, b in JUDGE_PAIRS}, "presence_rule": PRESENCE_RULE}


# --------------------------------------------------------------------------------------------------- the terms

def terms_map(entries: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """T as ``{hand predicate: [terms in the drafter's order]}``, every entry kept (an empty list included)."""
    out: dict[str, list[str]] = {}
    for entry in entries:
        predicate, terms = entry["predicate"], entry["terms"]
        if not isinstance(predicate, str) or predicate in out or not isinstance(terms, list) \
                or not all(isinstance(t, str) and t for t in terms):
            raise J4Error("a term set entry is malformed")
        out[predicate] = list(terms)
    return out


def predicate_terms(terms: Mapping[str, Sequence[str]], predicate: str) -> list[str]:
    """T[predicate] in the drafter's order; the empty list when T has none ("The terms", item 8)."""
    return list(terms.get(predicate, ()))


def terms_sha256(terms: Sequence[str]) -> str:
    """The sha256 of the canonical JSON of one predicate's list (a line's ``terms_sha256``; never the terms)."""
    return sha256_hex(canonical_bytes(list(terms)))


def longest_terms(terms: Mapping[str, Sequence[str]]) -> str | None:
    """C12: the predicate whose list is the longest in T by characters (the characters its canonical JSON adds to the
    data block), the first in predicate order on a tie; None when every list is empty."""
    best: tuple[int, str] | None = None
    for predicate in sorted(terms):
        size = len(canonical_dumps(list(terms[predicate]))) if terms[predicate] else 0
        if size and (best is None or size > best[0]):
            best = (size, predicate)
    return best[1] if best is not None else None


def read_terms(data: bytes) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """``terms.json``'s document and T from its bytes (``kind`` ``lab_jt_terms``, one entry per hand predicate)."""
    doc = strict_load(data)
    if not isinstance(doc, dict) or doc.get("kind") != lab_jt.TERMS_KIND \
            or doc.get("schema_version") != lab_jt.SCHEMA_VERSION or not isinstance(doc.get("terms"), list):
        raise J4Error("not a term set")
    terms = terms_map(doc["terms"])
    if len(terms) != lab_jt.HAND_SPECIFIC:
        raise J4Error("the term set does not hold one entry per specific hand predicate")
    return doc, terms


def drafted_pack(entries: Sequence[Mapping[str, Any]], hand_dir: str | Path | None = None) -> FrozenPack:
    """P_D ("The terms", item 9, C21): ``lab.jt.drafted_pack`` in a scratch directory, loaded, the directory removed
    (the loaded pack holds everything it needs)."""
    with tempfile.TemporaryDirectory(prefix="lab-j4-pd-") as tmp:
        return lab_jt.drafted_pack(hand_dir if hand_dir is not None else ROOT / lab_jt.PACK, entries,
                                   Path(tmp) / "pack")


# --------------------------------------------------------------------------------------------------- payloads

def payload(pack: FrozenPack, question: Mapping[str, Any], record: Mapping[str, Any],
            terms: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Task D's payload ("The terms", item 8): the shipped payload (``lab.j1.payload``) with ``predicate_terms`` added
    to its question block, and nothing else."""
    out = lab_j1.payload(pack, question, record)
    out["question"][TERMS_KEY] = predicate_terms(terms, question["predicate"])
    return out


def task_payload(task: str, pack: FrozenPack, question: Mapping[str, Any], record: Mapping[str, Any],
                 terms: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """The payload a task's call sends: :func:`payload` for task D, the shipped payload for task B."""
    if task not in TASK_BASE:
        raise J4Error("not one of the four judge tasks")
    return payload(pack, question, record, terms) if TASK_DRAFTED[task] else lab_j1.payload(pack, question, record)


# --------------------------------------------------------------------------------------------------- comparators

def presence_verdicts(pack: FrozenPack, labels_bytes: bytes, questions: Sequence[Mapping[str, Any]],
                      terms: Mapping[str, Sequence[str]],
                      drafted_lexical: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The drafted term-presence judge (rule 8, C5), as verdict lines: ``mentions_entity`` as the drafted lexical
    judge's line of the question answers it; ``describes_predicate`` ``yes`` when ``check.term_presence`` finds any
    term of T[predicate] in a folded sentence of the payload's record text (the narrative as ``judge_payload`` cuts
    it), ``no`` otherwise (an empty list matches nothing); the verdict by the pinned ``decide``."""
    records = {r["record_ref"]: r for r, _ in lab_j1.parse_labels(labels_bytes)}
    lexical = {line["question_id"]: line for line in drafted_lexical}
    out = []
    for q in questions:
        record = records[q["record_ref"]]
        line = lexical.get(q["question_id"])
        if line is None or line["mentions_entity"] is None:
            raise J4Error("the drafted lexical judge has no answer for a question")
        text = lab_j1.payload(pack, q, record)["record"]["text"]
        found = term_presence([text], predicate_terms(terms, q["predicate"]))[0]
        reply = {"mentions_entity": line["mentions_entity"], "describes_predicate": "yes" if found else "no"}
        out.append(lab_j1.verdict_line(q, lab_j1.window_record(record), reply, None))
    return out


def comparator_lines(hand: FrozenPack, p_d: FrozenPack, labels_bytes: bytes, questions: Sequence[Mapping[str, Any]],
                     terms: Mapping[str, Sequence[str]]) -> dict[str, list[dict[str, Any]]]:
    """Rule 8's comparators on every question, before any model runs, by :data:`COMPARATORS` name: the drafted lexical
    judge (``lab.j1.lexical_verdicts`` over P_D), the hand-pack lexical judge (over the hand pack), the drafted
    term-presence judge and the record-blind control (``lab.j1.prior_verdicts``)."""
    drafted = lab_j1.lexical_verdicts(p_d, labels_bytes, questions)
    return {LEXICAL_DRAFTED: drafted, LEXICAL_HAND: lab_j1.lexical_verdicts(hand, labels_bytes, questions),
            PRESENCE_DRAFTED: presence_verdicts(hand, labels_bytes, questions, terms, drafted),
            PRIOR: lab_j1.prior_verdicts(questions)}


def judge_pairs(questions: Sequence[Mapping[str, Any]], lines: Mapping[str, Sequence[Mapping[str, Any]]], *,
                bootstrap_b: int, bootstrap_seed: int) -> dict[str, dict[str, Any] | None]:
    """Rules 8 and 14: the three lexical judges paired with each other record by record (``lab.j1.paired``)."""
    return {name: lab_j1.paired(questions, lines[a], lines[b], bootstrap_b=bootstrap_b, bootstrap_seed=bootstrap_seed)
            for name, a, b in JUDGE_PAIRS}


# --------------------------------------------------------------------------------------------------- lines, views

def verdict_line(question: Mapping[str, Any], window: WindowRecord, reply: Mapping[str, Any] | None,
                 error_kind: str | None, task: str, text: str,
                 terms: Sequence[str] | None = None) -> dict[str, Any]:
    """J001's verdict line plus the task, the citation, ``grounded``, ``first_key``, the branch reading's verdict and,
    for task D, the count and sha256 of the list its payload carried (``terms``). ``text`` is the payload's record
    text G compares the citation with (branch E)."""
    if task not in TASK_BASE:
        raise J4Error("not one of the four judge tasks")
    if TASK_DRAFTED[task] == (terms is None):
        raise J4Error("task D's line needs the list its payload carried, and task B's none")
    two = answers(reply)
    line = lab_j1.verdict_line(question, window, two, error_kind)
    line["task"] = task
    line["citation"] = reply.get("citation") if reply is not None and TASK_FIELDS[task] == "citation" else None
    line["first_key"] = first_key(reply)
    if TASK_BASE[task] == "negation":
        line["mapped_verdict"] = lab_j1.verdict(window, mapped_reply(two))
        line["grounded"] = line["grounded_verdict"] = None
    else:
        is_grounded = grounded(line["citation"], text) if reply is not None else None
        line["grounded"] = is_grounded
        line["grounded_verdict"] = lab_j1.verdict(window, grounded_reply(two, is_grounded))
        line["mapped_verdict"] = None
    line["terms_count"] = len(terms) if terms is not None else None
    line["terms_sha256"] = terms_sha256(terms) if terms is not None else None
    return line


def line_problem(question: Mapping[str, Any], line: Any, text: str,
                 terms: Mapping[str, Sequence[str]]) -> str | None:
    """Why a stored line is not the line the runner writes for ``question`` (rule 16): its keys, its task, J001's
    check on the projected line (``lab.j1.line_problem``, unchanged), the citation the task carries, ``first_key``
    checked for shape (B19), the branch reading recomputed with the pinned ``decide`` (M under N; ``grounded`` from the
    stored citation and ``text``, the payload text rebuilt from the plan artifact's labels, and G under E), and the
    count and sha256 of task D's list recomputed from T (``terms``, rebuilt from the plan artifact's ``terms.json``)."""
    if not isinstance(line, Mapping) or sorted(line) != sorted(LINE_KEYS):
        return "keys"
    task = line["task"]
    if task not in TASK_BASE:
        return "task"
    problem = lab_j1.line_problem(question, {k: line[k] for k in lab_j1.LINE_KEYS})
    if problem is not None:
        return problem
    m, d = line["mentions_entity"], line["describes_predicate"]
    if TASK_FIELDS[task] == "citation" and m is not None:
        if not isinstance(line["citation"], str):
            return "citation"
    elif line["citation"] is not None:
        return "citation"
    if m is None:
        if line["first_key"] is not None:
            return "first_key"
        reply = None
    else:
        if line["first_key"] not in schema_keys(task):
            return "first_key"
        reply = {"mentions_entity": m, "describes_predicate": d}
    window = lab_j1.stand_in(question["record_ref"])
    if TASK_BASE[task] == "negation":
        if line["mapped_verdict"] != lab_j1.verdict(window, mapped_reply(reply)):
            return "mapped_verdict"
        for key in ("grounded", "grounded_verdict"):
            if line[key] is not None:
                return key
    else:
        if line["mapped_verdict"] is not None:
            return "mapped_verdict"
        is_grounded = grounded(line["citation"], text) if reply is not None else None
        if line["grounded"] is not is_grounded:
            return "grounded"
        if line["grounded_verdict"] != lab_j1.verdict(window, grounded_reply(reply, is_grounded)):
            return "grounded_verdict"
    if TASK_DRAFTED[task]:
        carried = predicate_terms(terms, question["predicate"])
        if line["terms_count"] != len(carried) or isinstance(line["terms_count"], bool):
            return "terms_count"
        if line["terms_sha256"] != terms_sha256(carried):
            return "terms_sha256"
    else:
        for key in ("terms_count", "terms_sha256"):
            if line[key] is not None:
                return key
    return None


def arm_lines(lines: Iterable[Mapping[str, Any]], arm: str) -> list[dict[str, Any]]:
    """The j1-shaped view of an arm: the lines of its task, and for a reading arm ``verdict`` replaced by the
    reading's verdict and ``describes_predicate`` by the reading's answer."""
    if arm not in ARM_TASK:
        raise J4Error("not one of the arms")
    task, reading = ARM_TASK[arm], ARM_READING[arm]
    out = []
    for line in lines:
        if line["task"] != task:
            continue
        view = {k: line[k] for k in lab_j1.LINE_KEYS}
        if reading != "shipped":
            view["verdict"] = line[READING_VERDICT[reading]]
            if line["mentions_entity"] is not None:
                reply = answers(line)
                read = mapped_reply(reply) if reading == "mapped" else grounded_reply(reply, line["grounded"])
                view["describes_predicate"] = read["describes_predicate"]
        out.append(view)
    return out


def jsonl_bytes(lines: Iterable[Mapping[str, Any]]) -> bytes:
    return lab_j1.jsonl_bytes(lines)


# --------------------------------------------------------------------------------------------------- the verdict word

def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _low(paired: Mapping[str, Any] | None) -> Any:
    return paired.get("ci_low") if isinstance(paired, Mapping) else None


def pass_test(scores: Mapping[str, Any] | None, paired: Mapping[str, Any] | None, bound: Mapping[str, Any] | None,
              paired_hand: Mapping[str, Any] | None, paired_presence: Mapping[str, Any] | None) -> dict[str, Any]:
    """C19: ``lab.j2.pass_test`` over the drafted-judge difference (``paired``) with the clauses ``beats_hand_lexical``
    and ``beats_term_presence`` added (the paired lower ends to the hand-pack lexical judge and to the drafted
    term-presence judge above zero); ``passed`` only when all six hold."""
    test = lab_j2.pass_test(scores, paired, bound)
    low_h, low_p = _low(paired_hand), _low(paired_presence)
    clauses = {**test["clauses"], "beats_hand_lexical": _num(low_h) and low_h > 0,
               "beats_term_presence": _num(low_p) and low_p > 0}
    return {**test, "clauses": clauses, "passed": all(clauses.values()),
            "diff_hand_lexical": paired_hand.get("mean_diff") if isinstance(paired_hand, Mapping) else None,
            "diff_hand_lexical_ci_low": low_h,
            "diff_hand_lexical_ci_high": paired_hand.get("ci_high") if isinstance(paired_hand, Mapping) else None,
            "diff_term_presence": paired_presence.get("mean_diff") if isinstance(paired_presence, Mapping) else None,
            "diff_term_presence_ci_low": low_p,
            "diff_term_presence_ci_high": (paired_presence.get("ci_high") if isinstance(paired_presence, Mapping)
                                           else None)}


def lever5_word(*, complete: bool, display_class: str | None, measurement: bool | None, dropped_share: float | None,
                scores: Mapping[str, Any] | None, paired: Mapping[str, Any] | None,
                bound: Mapping[str, Any] | None) -> str:
    """C6: the word under lever 5's text alone, ``lab.j2.verdict_word`` unchanged over the scores, the drafted-judge
    difference and the bound."""
    return lab_j2.verdict_word(complete=complete, display_class=display_class, measurement=measurement,
                               dropped_share=dropped_share, scores=scores, paired=paired, bound=bound)[0]


def verdict_word(*, complete: bool, display_class: str | None, measurement: bool | None,
                 dropped_share: float | None, scores: Mapping[str, Any] | None, paired: Mapping[str, Any] | None,
                 bound: Mapping[str, Any] | None, paired_hand: Mapping[str, Any] | None,
                 paired_presence: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Rule 13 as C6 and C19 state it, shaped as ``lab.j3.composite_verdict_word``: (the word, the pass test with its
    figures and ``lever5_word``). The word under lever 5's text alone is ``lab.j2.verdict_word``; then
    ``incomplete`` when that word is ``incomplete`` or when the hand-judge or the term-presence difference is not a
    number; ``pass_provisional`` only when that word is ``pass_provisional`` and both added clauses hold;
    ``partial`` when that word is ``pass_provisional`` and an added clause fails, when it is ``partial`` with the
    balanced accuracy at or above the target, or when it is ``partial`` below the target with both added clauses
    holding; else ``failure``. Equality fails every strict clause."""
    word5 = lever5_word(complete=complete, display_class=display_class, measurement=measurement,
                        dropped_share=dropped_share, scores=scores, paired=paired, bound=bound)
    test = pass_test(scores, paired, bound, paired_hand, paired_presence)
    test["lever5_word"] = word5
    added = test["clauses"]["beats_hand_lexical"] and test["clauses"]["beats_term_presence"]
    if word5 == "incomplete" or not _num(test["diff_hand_lexical_ci_low"]) \
            or not _num(test["diff_term_presence_ci_low"]):
        return "incomplete", test
    if word5 == "pass_provisional":
        return ("pass_provisional" if added else "partial"), test
    if word5 == "partial":
        if test["clauses"]["balanced_accuracy_at_target"] or added:
            return "partial", test
        return "failure", test
    return "failure", test


# --------------------------------------------------------------------------------------------------- pins

def routing_doc(models: Mapping[str, Mapping[str, Any]], keys: Sequence[str], base_url: str,
                base: str) -> dict[str, Any]:
    """The only builder of J4 routing: J001's endpoints keyed by model key; with one key (a unit's) the branch's two
    tasks are routed to it without escalation. The preregistration's routing pins every model at
    ``lab.prereg.PLACEHOLDER_BASE_URL`` and routes nothing."""
    routes = {task: {"endpoint": keys[0]} for task in tasks(base)} if len(keys) == 1 else {}
    return {"schema_version": 1, "endpoints": {key: lab_j1.endpoint(models[key], base_url) for key in keys},
            "routes": routes}


def code_files_list() -> list[str]:
    return code_files(CODE_FILES, ROOT)


def code_hash() -> str:
    """sha256 over :data:`CODE_FILES` (A002's files, ``lab/jt.py``, this kind's three modules and the onboard check
    the term-presence judge reads)."""
    return common_code_hash([ROOT / p for p in code_files_list()])


def terms_pin(*, sha256: str, run_id: int, artifact: str, vocabulary_hash: str, cutoff: str,
              jt_code_hash: str) -> dict[str, Any]:
    """The preregistration's ``terms`` block: the copy's path in the plan artifact and the term run's pins."""
    return {"file": TERMS_FILE, "sha256": sha256, "run_id": run_id, "artifact": artifact,
            "vocabulary_hash": vocabulary_hash, "cutoff": cutoff, "jt_code_hash": jt_code_hash}


def prereg_doc(*, pack: FrozenPack, pack_ref: str, p_d: FrozenPack, terms: Mapping[str, Any],
               labels: Mapping[str, Any], questions: Mapping[str, Any], comparator_sha256: Mapping[str, str],
               routing: Mapping[str, Any], base: str, seed: int, parts: int, bootstrap_b: int, bootstrap_seed: int,
               primary: Mapping[str, str], comparisons_before: int) -> dict[str, Any]:
    """``prereg/j4/prereg.json``: everything a unit checks before its first call, and the settings of the rule.
    ``terms`` is :func:`terms_pin`'s block; ``comparator_sha256`` the sha256 of each comparator's lines by
    :data:`COMPARATORS` name."""
    exclusion = labels["exclusion"]
    arm = _primary(base, primary["arm"])
    if sorted(terms) != sorted(TERMS_PIN_KEYS):
        raise J4Error("the terms block is not terms_pin's")
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j4_prereg", "experiment": EXPERIMENT, "base_task": base,
            "pack": lab_j1.pack_pins(pack, pack_ref), "drafted_pack": {"hashes": p_d.hashes()},
            "terms": dict(terms),
            "labels": {k: labels[k] for k in ("source", "n", "seed", "records", "claims", "sha256", "available")},
            "questions": {k: questions[k] for k in ("seed", "parts", "records", "questions", "part_records",
                                                     "negative_draw", "sha256")},
            "lexical": {"sha256": comparator_sha256[LEXICAL_HAND]},
            "lexical_drafted": {"sha256": comparator_sha256[LEXICAL_DRAFTED]},
            "presence_drafted": {"sha256": comparator_sha256[PRESENCE_DRAFTED], "rule": PRESENCE_RULE},
            "prior": {"sha256": comparator_sha256[PRIOR], "rule": lab_j1.PRIOR_RULE, "top": list(lab_j1.PRIOR_TOP),
                      "source": lab_j1.PRIOR_SOURCE},
            "tasks": task_pins(base, arm), "reading": reading_pin(base), "arms": arms_doc(base),
            "lifts": lifts_doc(base), "comparators": comparators_doc(),
            "primary": {"model": primary["model"], "arm": arm}, "confirmation": is_confirmation(base, arm),
            "headline_arm": arm, "headline_secondary": headline_secondary(base, arm),
            "other_reading_arm": other_reading_arm(base, arm),
            "exclusion": {k: exclusion[k] for k in EXCLUSION_KEYS},
            "comparisons_before": comparisons_before, "comparisons_this_cycle": COMPARISONS_THIS_CYCLE,
            "target_balanced_accuracy": TARGET_BA,
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
    if failed or not isinstance(doc, dict) or sorted(doc) != sorted(PREREG_KEYS) or doc["kind"] != "lab_j4_prereg" \
            or doc["schema_version"] != SCHEMA_VERSION or doc["base_task"] not in BASES:
        raise UsageError("the preregistration is not a J4 prereg.json") from None
    return doc, sha256_hex(data)


class _Checked:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


def _check(args: argparse.Namespace) -> _Checked:
    check_run_id(args.run_id)
    if args.budget_seconds < 1:
        raise UsageError("--budget-seconds must be >= 1") from None
    prereg, prereg_sha = read_prereg(args.prereg)
    base = prereg["base_task"]
    if not isinstance(prereg["primary"], dict) or prereg["primary"].get("arm") not in primary_arms(base):
        raise UsageError("the preregistered primary arm is not one of the branch's") from None
    arm = prereg["primary"]["arm"]
    if not 1 <= args.part <= prereg["parts"]:
        raise UsageError("--part is not one of the preregistered parts") from None
    try:
        pack = load_pack(ROOT / prereg["pack"]["ref"])
    except (PackError, OSError):
        raise UsageError("the preregistered pack does not load") from None
    _pin("the pack's vocabulary hash", prereg["pack"]["vocabulary_hash"], pack.vocabulary_hash)
    _pin("the pack's config hash", prereg["pack"]["config_hash"], pack.config_hash)
    _pin("the code hash", prereg["code_hash"], code_hash())
    _pin("the judge tasks", prereg["tasks"], task_pins(base, arm))
    _pin("the reading", prereg["reading"], reading_pin(base))
    _pin("the arms", prereg["arms"], arms_doc(base))
    _pin("the lifts", prereg["lifts"], lifts_doc(base))
    terms_bytes = _read(args.terms, "the term set")
    _pin("the term set's sha256", prereg["terms"]["sha256"], sha256_hex(terms_bytes))
    try:
        doc, terms = read_terms(terms_bytes)
        rebuilt = drafted_pack(doc["terms"]).hashes()
    except (J4Error, StrictJsonError, PackError, lab_jt.JtError, OSError, KeyError, TypeError):
        raise UsageError("the term set does not rebuild the drafted pack") from None
    _pin("the drafted pack's hashes", prereg["drafted_pack"]["hashes"], rebuilt)
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
    order = pass_order(base, arm)
    try:
        config = load_routing(args.routing, tasks=order, check_env=False)
    except ConfigError as exc:
        raise UsageError(f"routing: {exc}") from None
    pinned = {e["name"]: e for e in prereg["endpoints"]}
    if args.endpoint not in pinned or args.endpoint not in config.endpoints:
        raise UsageError("the endpoint is not in the preregistration or the routing") from None
    ep = config.endpoints[args.endpoint]
    _pin("the endpoint's pins", {k: v for k, v in pinned[args.endpoint].items() if k != "name"},
         e1_extract.endpoint_pins(ep))
    for task in order:
        route = config.routes.get(task)
        if route is None or route.endpoint != args.endpoint or route.escalate_to is not None:
            raise UsageError("the routing must route both judge tasks to the endpoint without escalation") from None
    if ep.provider != "openai_compat":
        raise UsageError("the endpoint is not openai_compat: J4 judges with real servers only") from None
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
                    out_dir=out_dir, base=base, arm=arm, order=order, terms=terms,
                    terms_sha=sha256_hex(terms_bytes), p_d=rebuilt)


def _file_sha(path: Path) -> str | None:
    return sha256_hex(path.read_bytes()) if path.exists() else None


def _run_base(args: argparse.Namespace, c: _Checked) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j4_run", "experiment": EXPERIMENT, "run_id": args.run_id,
            "endpoint": args.endpoint, "part": args.part, "parts": c.prereg["parts"], "base_task": c.base,
            "primary_arm": c.arm, "pass_order": list(c.order), "questions_planned": len(c.order) * len(c.questions),
            "questions_planned_by_task": {task: len(c.questions) for task in c.order},
            "prereg_sha256": c.prereg_sha,
            "pinned": {"labels_sha256": c.labels_sha, "questions_sha256": c.questions_sha,
                       "vocabulary_hash": c.pack.vocabulary_hash, "config_hash": c.pack.config_hash,
                       "code_hash": c.prereg["code_hash"], "tasks": c.prereg["tasks"],
                       "reading": c.prereg["reading"], "terms_sha256": c.terms_sha, "drafted_pack": dict(c.p_d),
                       **e1_extract.endpoint_pins(c.endpoint)},
            "boundary": BOUNDARY, "data_label": DATA_LABEL, "model_requested": c.endpoint.model,
            "budget_seconds": args.budget_seconds, "code_commit": code_commit(), "started_at": utc_clock()}


def _execute(args: argparse.Namespace, c: _Checked) -> int:
    out = c.out_dir
    base = _run_base(args, c)
    order = c.order
    out.mkdir(parents=True)
    empty = {"complete": False, "complete_by_task": {task: False for task in order}, "stopped": None,
             "stopped_task": None, "questions_done": 0, "questions_done_by_task": {task: 0 for task in order},
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
    schemas = {name: compile_schema(task_schema(name)) for name in order}
    specs = {name: task_spec(name) for name in order}
    for spec in specs.values():
        if rt.exemption(spec) is not None:      # the endpoint is the runtime's own boundary: never an exemption
            rt.close()
            return fail("a judge route would leave the boundary under an exemption")
    lines: list[dict[str, Any]] = []
    done: dict[str, int] = {task: 0 for task in order}
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
            for t, task_name in enumerate(order):
                if stopped is not None or boundary:
                    break
                task = specs[task_name]
                drafted = TASK_DRAFTED[task_name]
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
                    sent = task_payload(task_name, c.pack, q, record, c.terms)
                    reply, error_kind, down_now = None, None, False
                    try:
                        signals.enter_call()
                        reply = rt.run(task, sent, schemas[task_name], ref=f"j4-{args.part}-{t}-{i:04d}")
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
                    line = verdict_line(q, window, reply, error_kind, task_name, sent["record"]["text"],
                                        sent["question"][TERMS_KEY] if drafted else None)
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
        stopped_task = next((task for task in order if done[task] < len(c.questions)), None)
    rows = read_ledger(out / "ledger.jsonl") if (out / "ledger.jsonl").exists() else []
    stop_index = order.index(stopped_task) if stopped_task in order else len(order)
    complete_by_task = {task: (not interrupted and not boundary and done[task] == len(c.questions)
                               and (stopped is None or order.index(task) < stop_index))
                        for task in order}
    complete = all(complete_by_task.values()) and stopped is None and not interrupted and not boundary
    overall = lab_j3._latency(rows)
    doc = {**base, "complete": complete, "complete_by_task": complete_by_task, "stopped": stopped,
           "stopped_task": stopped_task, "questions_done": len(lines), "questions_done_by_task": dict(done),
           "measurement": measurement_flag([c.endpoint], rows, bool(listed.get("fake"))),
           "models_listed": listed["ids"] if listed.get("ok") else None, "listed_fake": listed.get("fake"),
           "models_served": sorted({r["model_served"] for r in rows if r["model_served"]}),
           "verdicts_sha256": _file_sha(out / "verdicts.jsonl"), "ledger_sha256": _file_sha(out / "ledger.jsonl"),
           "failures": lab_j1._failures(lines),
           "failures_by_task": {task: lab_j1._failures([x for x in lines if x["task"] == task]) for task in order},
           "latency_ms_p50": overall["p50"], "latency_ms_p95": overall["p95"],
           "latency_ms_by_task": {task: lab_j3._latency(rows, task) for task in order},
           "tokens_by_task": {task: lab_j3.token_figures(rows, task) for task in order}, "finished_at": utc_clock()}
    write_json_atomic(out / "run.json", doc)
    if boundary:
        return fail("the runtime refused a judge route at its boundary")
    if interrupted:
        print(f"j4 run: interrupted after {len(lines)} questions; run.json says complete=false", file=sys.stderr)
        return 130
    by_task = " ".join(f"{task[len('judge_record_'):]}={done[task]}/{len(c.questions)}" for task in order)
    print(f"j4 run: {args.endpoint} part {args.part}: {len(lines)} of {base['questions_planned']} questions "
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
    p = argparse.ArgumentParser(prog="python -m lab.j4",
                                description="Accuracy test A003: one part of one model, the two judge tasks.")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="judge one part's questions with one endpoint, both tasks, in the pass order "
                                     "the preregistration pins")
    run.add_argument("--prereg", required=True)
    run.add_argument("--labels", required=True)
    run.add_argument("--questions", required=True)
    run.add_argument("--terms", required=True)
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

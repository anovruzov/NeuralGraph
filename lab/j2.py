"""Accuracy test A001 (lab experiment ``j2``): the site verifier's narrow question asked of one model about a fresh
draw of real public complaints, one record at a time, under the shipped judge task and a lab-side variant task, with
a declared reply mapping read beside the shipped verdict rule.

    python -m lab.j2 run --prereg F --labels F --questions F --routing F --endpoint KEY --part K --run-id ID
        --runs-dir D --budget-seconds N

``docs/collective/A001/CHOICE-A001.md`` (amended A1 to A12 before any run) is the rule and ``BUILD-A001.md`` the build
note. This module imports the pinned modules read-only and changes none of them: the questions, the payload, the
comparators, the scoring and the paired differences are ``lab.j1``'s (J001's code, called unchanged); the call is
``Runtime.run`` with the shipped task (``verify.judge_task``) or the lab's variant :func:`negation_task`, through the
same pinned server and client, with the shipped schema and payload; every verdict is the pinned ``verify.decide`` over
the one record (``lab.j1.verdict``).

**The arms** (rule 3), :data:`ARMS`: ``shipped`` (A: the product's judge unchanged), ``shipped_mapped`` (B: A's replies
read through the mapping :data:`MAPPING` before ``decide``), ``negation`` (C: the variant task :data:`NEGATION_TASK`,
whose instructions :data:`NEGATION_INSTRUCTIONS` are rule 3's text verbatim; the shipped schema and reply, ``decide``)
and ``negation_mapped`` (D: C's replies through the mapping). Model calls are made for the two tasks only; B and D are
views of A's and C's lines (:func:`arm_lines`): ``verdict`` replaced by ``mapped_verdict`` and ``describes_predicate``
by the mapping's reading. The mapping M reads only the reply: ``describes_predicate`` ``unclear`` is read as ``no``;
``mentions_entity`` is left as answered (:func:`mapped_reply`).

**A verdict line** is J001's with two more keys (:data:`LINE_KEYS`): ``task`` (which pass) and ``mapped_verdict``
(``decide`` over the mapped reply, ``unknown`` without a reply). :func:`line_problem` projects a line to J001's keys and
calls ``lab.j1.line_problem`` unchanged, then recomputes the mapped verdict (A8).

**The verdict word** (rule 10 as read by A5 and A6; :func:`verdict_word`) is computed from the figures alone for every
(model, arm): ``incomplete`` unless every part of the (model, task) set finished with ``measurement`` true in the
``model`` class and at most :data:`~lab.j1.WITHHOLD_SHARE` of its records dropped; else ``pass_provisional`` when the
pass test holds (:func:`pass_test`: balanced accuracy at or above :data:`TARGET_BA`, the paired difference to the
lexical judge with its lower end above zero, the balanced accuracy's lower end above the predicate-only bound on the
same records, and the unknown share reported); else ``partial`` when the balanced accuracy is at or above the target or
the lower end of the difference to the lexical judge is above zero; else ``failure``. The first word whose condition
holds wins. The lifts of rule 8 (:data:`LIFTS`) are read by :func:`lift_reading`: ``helps`` when the lower end is above
zero, ``hurts`` when the upper end is below zero, else ``not_told_apart``.

**The run** judges one part's questions twice, in two passes, task N first (A9): first every question with
:data:`NEGATION_TASK`, then every question with the shipped task. Every pin is checked before any call (J001's, plus the
two tasks' instructions, schema and ``max_tokens``, the mapping and the routing of both tasks to the endpoint without
escalation). A stop (budget, server down, a signal) ends the pass it falls in and the passes after it; ``run.json``
(``kind: lab_j2_run``) records ``complete`` (both passes), ``complete_by_task`` and ``questions_done_by_task`` (a part
is finished for a task when every one of its calls for that task was made), ``stopped`` and ``stopped_task``, the
latency per task, and the J001 fields. Exit codes: 0 complete; 1 stopped; 2 usage, pins or the boundary; 130
interrupted. The budget timer and the signals are J001's (``lab.j1._Signals``).
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
from mycelic.collective.edge.verify import JUDGE_MAX_TOKENS, JUDGE_TASK, judge_schema, judge_task
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

CLI = "lab.j2"
KIND = "j2"
EXPERIMENT = "J2"
SCHEMA_VERSION = 1
NEGATION_TASK = "judge_record_negation"
# rule 3, arm C: the variant instruction, verbatim (the first three sentences and the last are the shipped ones)
NEGATION_INSTRUCTIONS = (
    "Judge one record of this site against one question. The question names an entity (its type, its id and the "
    "aliases it may be written as) and a predicate. mentions_entity: yes when the record names that entity in its "
    "text or among its structured entities, no when it does not, unclear when you cannot tell. describes_predicate: "
    "the predicate names a component or system of the entity. Answer yes when the record reports a problem, failure, "
    "defect or safety concern with that component or system, including a report that it did not work, did not "
    "deploy, did not engage, stopped working or failed (the record's codes are statements too). Answer no when the "
    "record does not mention that component or system, or mentions it only to say that it worked or was not "
    "involved; a component the record never mentions is a no, not an unclear. Answer unclear only when the record "
    "mentions that component or system and you cannot tell whether it reports a problem with it. Judge only this "
    "record."
)
TASKS = (NEGATION_TASK, JUDGE_TASK)                       # the pass order (A9): task N first, then the shipped task
TASK_LABELS = {NEGATION_TASK: "negation", JUDGE_TASK: "shipped"}
ARMS = ("shipped", "shipped_mapped", "negation", "negation_mapped")
ARM_TASK = {"shipped": JUDGE_TASK, "shipped_mapped": JUDGE_TASK, "negation": NEGATION_TASK,
            "negation_mapped": NEGATION_TASK}
ARM_MAPPED = {"shipped": False, "shipped_mapped": True, "negation": False, "negation_mapped": True}
# the mapping M (rule 3, arm B): a reply's describes_predicate unclear is read as no; nothing else changes
MAPPING = {"describes_predicate": {"unclear": "no"}}
# rule 8's lifts: (name, arm, minus arm), and the lever each reads
LIFTS = (("B_minus_A", "shipped_mapped", "shipped"), ("C_minus_A", "negation", "shipped"),
         ("D_minus_A", "negation_mapped", "shipped"), ("D_minus_B", "negation_mapped", "shipped_mapped"),
         ("D_minus_C", "negation_mapped", "negation"))
LIFT_LEVERS = {"B_minus_A": "unclear_mapping", "D_minus_C": "unclear_mapping", "C_minus_A": "negation_instruction",
               "D_minus_B": "negation_instruction", "D_minus_A": "both"}
TARGET_BA = 0.86
VERDICT_WORDS = ("incomplete", "pass_provisional", "partial", "failure")
READINGS = ("helps", "hurts", "not_told_apart")
WITHHOLD_SHARE = lab_j1.WITHHOLD_SHARE
BOUNDARY = lab_j1.BOUNDARY
DATA_LABEL = lab_j1.DATA_LABEL
LINE_KEYS = (*lab_j1.LINE_KEYS, "task", "mapped_verdict")
CODE_FILES = (*lab_j1.CODE_FILES, "lab/j2.py", "lab/j2labels.py")
PREREG_KEYS = ("schema_version", "kind", "experiment", "pack", "labels", "questions", "lexical", "prior", "tasks",
               "mapping", "arms", "primary", "headline_arm", "exclusion", "comparisons_before",
               "target_balanced_accuracy", "code_hash", "code_files", "code_commit", "code_dirty", "endpoints",
               "boundary", "data_label", "parts", "seed", "twins", "excluded", "bootstrap_b", "bootstrap_seed",
               "withhold_share")
EXCLUSION_KEYS = ("excluded", "eligible_after_exclusion", "overlap", "mechanisms", "complaint_file_sha256",
                  "excluded_runs", "excluded_sha256")
_clock = time.monotonic                 # the budget's clock (tests replace it)


class J2Error(ValueError):
    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


# --------------------------------------------------------------------------------------------------- tasks, mapping

def negation_task() -> TaskSpec:
    """Arm C's TaskSpec: task :data:`NEGATION_TASK`, data class ``raw``, rule 3's instructions, the shipped
    ``max_tokens``."""
    return TaskSpec(NEGATION_TASK, "raw", NEGATION_INSTRUCTIONS, JUDGE_MAX_TOKENS)


def task_spec(name: str) -> TaskSpec:
    if name == JUDGE_TASK:
        return judge_task()
    if name == NEGATION_TASK:
        return negation_task()
    raise J2Error("not one of the two judge tasks")


def _task_pins(task: TaskSpec) -> dict[str, Any]:
    return {"name": task.name, "data_class": task.data_class, "instructions_sha256": sha256_hex(task.instructions),
            "schema_sha256": sha256_hex(canonical_bytes(judge_schema())), "max_tokens": task.max_tokens}


def task_pins() -> dict[str, Any]:
    """Both tasks' pins and the pass order; the shipped task's are ``lab.j1.task_pins()``."""
    return {"shipped": _task_pins(judge_task()), "negation": _task_pins(negation_task()), "order": list(TASKS)}


def mapping_pin() -> dict[str, Any]:
    return {"rule": {k: dict(v) for k, v in MAPPING.items()}, "sha256": sha256_hex(canonical_bytes(MAPPING))}


def mapped_reply(reply: Mapping[str, str] | None) -> dict[str, str] | None:
    """The mapping M over a reply (None stays None): it reads the reply alone."""
    if reply is None:
        return None
    out = dict(reply)
    for field, table in MAPPING.items():
        out[field] = table.get(out[field], out[field])
    return out


def arms_doc() -> dict[str, dict[str, Any]]:
    return {arm: {"task": ARM_TASK[arm], "mapped": ARM_MAPPED[arm]} for arm in ARMS}


# --------------------------------------------------------------------------------------------------- lines, views

def verdict_line(question: Mapping[str, Any], window: WindowRecord, reply: Mapping[str, str] | None,
                 error_kind: str | None, task: str) -> dict[str, Any]:
    """J001's verdict line plus ``task`` and ``mapped_verdict`` (``decide`` over the mapped reply)."""
    if task not in TASKS:
        raise J2Error("not one of the two judge tasks")
    line = lab_j1.verdict_line(question, window, reply, error_kind)
    line["task"] = task
    line["mapped_verdict"] = lab_j1.verdict(window, mapped_reply(reply))
    return line


def line_problem(question: Mapping[str, Any], line: Any) -> str | None:
    """Why a stored line is not the line the runner writes for ``question`` (A8): its keys, its task, J001's check on
    the projected line (``lab.j1.line_problem``, unchanged: the shipped verdict by the one pinned ``decide``), and the
    mapped verdict recomputed with ``decide`` over the mapped reply."""
    if not isinstance(line, Mapping) or sorted(line) != sorted(LINE_KEYS):
        return "keys"
    if line["task"] not in TASKS:
        return "task"
    problem = lab_j1.line_problem(question, {k: line[k] for k in lab_j1.LINE_KEYS})
    if problem is not None:
        return problem
    m, d = line["mentions_entity"], line["describes_predicate"]
    reply = None if m is None else {"mentions_entity": m, "describes_predicate": d}
    if line["mapped_verdict"] != lab_j1.verdict(lab_j1.stand_in(question["record_ref"]), mapped_reply(reply)):
        return "mapped_verdict"
    return None


def arm_lines(lines: Iterable[Mapping[str, Any]], arm: str) -> list[dict[str, Any]]:
    """The j1-shaped view of an arm (A8): the lines of its task, and for a mapped arm ``verdict`` replaced by
    ``mapped_verdict`` and ``describes_predicate`` by the mapping's reading."""
    if arm not in ARMS:
        raise J2Error("not one of the four arms")
    task, mapped = ARM_TASK[arm], ARM_MAPPED[arm]
    out = []
    for line in lines:
        if line["task"] != task:
            continue
        view = {k: line[k] for k in lab_j1.LINE_KEYS}
        if mapped:
            view["verdict"] = line["mapped_verdict"]
            if view["mentions_entity"] is not None:
                view["describes_predicate"] = mapped_reply({"mentions_entity": view["mentions_entity"],
                                                            "describes_predicate": view["describes_predicate"]}
                                                           )["describes_predicate"]
        out.append(view)
    return out


def jsonl_bytes(lines: Iterable[Mapping[str, Any]]) -> bytes:
    return lab_j1.jsonl_bytes(lines)


# --------------------------------------------------------------------------------------------------- the verdict word

def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def pass_test(scores: Mapping[str, Any] | None, paired: Mapping[str, Any] | None,
              bound: Mapping[str, Any] | None) -> dict[str, Any]:
    """Rule 10's pass test on one (model, arm): the four clauses and their figures; ``passed`` only when every clause
    holds and every figure is a number."""
    ba = scores.get("balanced_accuracy") if isinstance(scores, Mapping) else None
    value = ba.get("value") if isinstance(ba, Mapping) else None
    low = ba.get("ci_low") if isinstance(ba, Mapping) else None
    high = ba.get("ci_high") if isinstance(ba, Mapping) else None
    unknown = scores.get("unknown_share", {}).get("value") if isinstance(scores, Mapping) else None
    diff_low = paired.get("ci_low") if isinstance(paired, Mapping) else None
    diff_high = paired.get("ci_high") if isinstance(paired, Mapping) else None
    diff = paired.get("mean_diff") if isinstance(paired, Mapping) else None
    bound_value = bound.get("value") if isinstance(bound, Mapping) else None
    clauses = {
        "balanced_accuracy_at_target": _num(value) and value >= TARGET_BA,
        "beats_lexical": _num(diff_low) and diff_low > 0,
        "beats_bound": _num(low) and _num(bound_value) and low > bound_value,
        "unknown_share_reported": _num(unknown),
    }
    return {"passed": all(clauses.values()), "target": TARGET_BA, "clauses": clauses,
            "balanced_accuracy": value, "ci_low": low, "ci_high": high,
            "gap_to_target": TARGET_BA - value if _num(value) else None,
            "diff_lexical": diff, "diff_lexical_ci_low": diff_low, "diff_lexical_ci_high": diff_high,
            "bound": bound_value, "unknown_share": unknown}


def verdict_word(*, complete: bool, display_class: str | None, measurement: bool | None,
                 dropped_share: float | None, scores: Mapping[str, Any] | None, paired: Mapping[str, Any] | None,
                 bound: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """(the verdict word, the pass test with its figures), by A5's order: ``incomplete``, ``pass_provisional``,
    ``partial``, ``failure``, the first whose condition holds (see the module docstring)."""
    test = pass_test(scores, paired, bound)
    if (not complete or display_class != "model" or measurement is not True or dropped_share is None
            or dropped_share > WITHHOLD_SHARE or not _num(test["balanced_accuracy"])
            or not _num(test["diff_lexical_ci_low"]) or not _num(test["bound"])):
        return "incomplete", test
    if test["passed"]:
        return "pass_provisional", test
    if test["clauses"]["balanced_accuracy_at_target"] or test["clauses"]["beats_lexical"]:
        return "partial", test
    return "failure", test


def lift_reading(paired: Mapping[str, Any] | None) -> str | None:
    """``helps`` when the lower end is above zero, ``hurts`` when the upper end is below zero, else
    ``not_told_apart``; None without an interval (rule 9)."""
    if not isinstance(paired, Mapping) or not _num(paired.get("ci_low")) or not _num(paired.get("ci_high")):
        return None
    if paired["ci_low"] > 0:
        return "helps"
    if paired["ci_high"] < 0:
        return "hurts"
    return "not_told_apart"


# --------------------------------------------------------------------------------------------------- pins

def routing_doc(models: Mapping[str, Mapping[str, Any]], keys: Sequence[str], base_url: str) -> dict[str, Any]:
    """The only builder of J2 routing: J001's endpoints keyed by model key; with one key (a unit's) both tasks are
    routed to it without escalation. The preregistration's routing pins every model at
    ``lab.prereg.PLACEHOLDER_BASE_URL`` and routes nothing."""
    routes = {task: {"endpoint": keys[0]} for task in TASKS} if len(keys) == 1 else {}
    return {"schema_version": 1, "endpoints": {key: lab_j1.endpoint(models[key], base_url) for key in keys},
            "routes": routes}


def code_files_list() -> list[str]:
    return code_files(CODE_FILES, ROOT)


def code_hash() -> str:
    """sha256 over :data:`CODE_FILES` (J001's files plus ``lab/j2.py`` and ``lab/j2labels.py``)."""
    return common_code_hash([ROOT / p for p in code_files_list()])


def prereg_doc(*, pack: FrozenPack, pack_ref: str, labels: Mapping[str, Any], questions: Mapping[str, Any],
               lexical_sha256: str, prior_sha256: str, routing: Mapping[str, Any], seed: int, parts: int,
               bootstrap_b: int, bootstrap_seed: int, primary: Mapping[str, str],
               comparisons_before: int) -> dict[str, Any]:
    """``prereg/j2/prereg.json``: everything a unit checks before its first call, and the settings of the rule."""
    exclusion = labels["exclusion"]
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j2_prereg", "experiment": EXPERIMENT,
            "pack": lab_j1.pack_pins(pack, pack_ref),
            "labels": {k: labels[k] for k in ("source", "n", "seed", "records", "claims", "sha256", "available")},
            "questions": {k: questions[k] for k in ("seed", "parts", "records", "questions", "part_records",
                                                     "negative_draw", "sha256")},
            "lexical": {"sha256": lexical_sha256},
            "prior": {"sha256": prior_sha256, "rule": lab_j1.PRIOR_RULE, "top": list(lab_j1.PRIOR_TOP),
                      "source": lab_j1.PRIOR_SOURCE},
            "tasks": task_pins(), "mapping": mapping_pin(), "arms": arms_doc(),
            "primary": {"model": primary["model"], "arm": primary["arm"]}, "headline_arm": primary["arm"],
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
    if failed or not isinstance(doc, dict) or sorted(doc) != sorted(PREREG_KEYS) or doc["kind"] != "lab_j2_prereg" \
            or doc["schema_version"] != SCHEMA_VERSION:
        raise UsageError("the preregistration is not a J2 prereg.json") from None
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
    _pin("the arms", prereg["arms"], arms_doc())
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
            raise UsageError("the routing must route both judge tasks to the endpoint without escalation") from None
    if ep.provider != "openai_compat":
        raise UsageError("the endpoint is not openai_compat: J2 judges with real servers only") from None
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
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j2_run", "experiment": EXPERIMENT, "run_id": args.run_id,
            "endpoint": args.endpoint, "part": args.part, "parts": c.prereg["parts"], "pass_order": list(TASKS),
            "questions_planned": len(TASKS) * len(c.questions),
            "questions_planned_by_task": {task: len(c.questions) for task in TASKS},
            "prereg_sha256": c.prereg_sha,
            "pinned": {"labels_sha256": c.labels_sha, "questions_sha256": c.questions_sha,
                       "vocabulary_hash": c.pack.vocabulary_hash, "config_hash": c.pack.config_hash,
                       "code_hash": c.prereg["code_hash"], "tasks": c.prereg["tasks"],
                       "mapping": c.prereg["mapping"], **e1_extract.endpoint_pins(c.endpoint)},
            "boundary": BOUNDARY, "data_label": DATA_LABEL, "model_requested": c.endpoint.model,
            "budget_seconds": args.budget_seconds, "code_commit": code_commit(), "started_at": utc_clock()}


def _latency(rows: Sequence[Mapping[str, Any]], task: str | None = None) -> dict[str, float | None]:
    values = [r["latency_ms"] for r in rows if r["ok"] and r["attempt"] >= 1 and r["latency_ms"] is not None
              and (task is None or r["task"] == task)]
    return {"p50": stats.percentile(values, 50), "p95": stats.percentile(values, 95)}


def _execute(args: argparse.Namespace, c: _Checked) -> int:
    out = c.out_dir
    base = _base(args, c)
    out.mkdir(parents=True)
    empty = {"complete": False, "complete_by_task": {task: False for task in TASKS}, "stopped": None,
             "stopped_task": None, "questions_done": 0, "questions_done_by_task": {task: 0 for task in TASKS},
             "measurement": False, "models_listed": None, "listed_fake": None, "models_served": [],
             "verdicts_sha256": None, "ledger_sha256": None, "failures": None, "failures_by_task": None,
             "latency_ms_p50": None, "latency_ms_p95": None, "latency_ms_by_task": None, "finished_at": None}
    write_json_atomic(out / "run.json", {**base, **empty})
    try:
        rt = Runtime(c.config, boundary=BOUNDARY, ledger_path=out / "ledger.jsonl", run_id=args.run_id,
                     clock=utc_clock, data_label=DATA_LABEL)
    except ConfigError as exc:
        return fail(str(exc))
    schema = compile_schema(judge_schema())
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
                    reply, error_kind, down_now = None, None, False
                    try:
                        signals.enter_call()
                        reply = rt.run(task, lab_j1.payload(c.pack, q, record), schema,
                                       ref=f"j2-{args.part}-{t}-{i:04d}")
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
                    line = verdict_line(q, window, reply, error_kind, task_name)
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
           "latency_ms_by_task": {task: _latency(rows, task) for task in TASKS}, "finished_at": utc_clock()}
    write_json_atomic(out / "run.json", doc)
    if boundary:
        return fail("the runtime refused a judge route at its boundary")
    if interrupted:
        print(f"j2 run: interrupted after {len(lines)} questions; run.json says complete=false", file=sys.stderr)
        return 130
    by_task = " ".join(f"{TASK_LABELS[task]}={done[task]}/{len(c.questions)}" for task in TASKS)
    print(f"j2 run: {args.endpoint} part {args.part}: {len(lines)} of {base['questions_planned']} questions "
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
    p = argparse.ArgumentParser(prog="python -m lab.j2",
                                description="Accuracy test A001: one part of one model, both judge tasks.")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="judge one part's questions with one endpoint, task N first")
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

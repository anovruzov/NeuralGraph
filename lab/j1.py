"""Judge test J001 (lab experiment ``j1``): the site verifier's narrow question, asked of one model about real public
complaints, one record at a time.

    python -m lab.j1 run --prereg F --labels F --questions F --routing F --endpoint KEY --part K --run-id ID
        --runs-dir D --budget-seconds N

``docs/collective/replay/vehicles/CHOICE-J001.md`` is the rule and ``BUILD-J001.md`` the build note. This module
imports the pinned collective modules read-only and changes none of them: the payload is ``verify.judge_payload``,
the task and schema are ``verify.judge_task`` and ``verify.judge_schema``, the call is ``Runtime.run`` (one repair, no
escalation) and the verdict is the verifier's own ``verify.decide`` over the one record. What ``SiteVerifier.answer``
adds around them (boundary checks, budgets, master data, secrets, storage, pooling) is not used.

**Questions** (:func:`build_questions`, in the plan job, before any model runs). For each labelled record, in record
order: the entity is its structured vehicle (a pack id); the positive is one filed predicate,
``random.Random("j1:<seed>:<record_ref>:positive").choice`` over the sorted filed predicates; the negative is
``random.Random("j1:<seed>:<record_ref>:negative").choice`` over the sorted pack predicates less the filed ones, less
:data:`EXCLUDED` and less the other name of every filed predicate in :data:`TWINS`. Record ``i`` of ``n`` is in part
``i * parts // n + 1``. One canonical line per question, the positive first: ``{"question_id"
("<record_ref>:<kind>"), "record_ref", "part", "kind", "entity_type", "entity_id", "predicate", "filed"}``.

**The payload** (:func:`payload`) is ``judge_payload`` of the record as a ``WindowRecord`` with its codes empty (the
labels hide them; a label record with codes is refused), its structured entities, language and narrative.

**A verdict line** (``verdicts.jsonl`` and the plan job's ``lexical.jsonl``): ``{"question_id", "record_ref", "kind",
"predicate", "mentions_entity", "describes_predicate", "verdict", "error_kind", "scored"}``. The two answers are the
whole reply (null without a valid reply), stored because the data is public; the verdict is ``decide([w], [(w,
reply)], 0)``, or ``decide([w], [], 1)`` (``unknown``) when no reply passed; ``scored`` is false only for a transport
failure (``e1_extract.failure_class``: timeout, network, HTTP error, size limit). No narrative is written.

**Scoring** (:func:`score`), against the filed codes, not checked labels: a verdict is correct when it is ``confirm``
on a positive or ``refute`` on a negative. A record is scored when both its questions have a scored line; a record
with a transport failure is left out (``records_dropped``), and one without both lines (a part that stopped) is
``records_not_judged``. Sensitivity (positives confirmed), specificity (negatives refuted), accuracy and the unknown
share each get ``{"value", "ci_low", "ci_high"}`` from ``stats.cluster_bootstrap_mean`` with one cluster per scored
record, seed ``j1:<bootstrap_seed>``: the same draws for every metric and judge scored on the same records. Every
scored record holds one positive and one negative, so balanced accuracy, the mean of sensitivity and specificity, is
the accuracy on every draw: its block is the accuracy's. :func:`headline` reads a model's balanced accuracy interval
against the lexical judge's balanced accuracy on the same records; :func:`paired` bootstraps the per-record
difference (``stats.paired_bootstrap``).

**The run** checks every pin before any call, each a usage error (exit 2): the prereg's kind; the labels' and the
questions' sha256; the pack's vocabulary and config hashes; :func:`code_hash`; the judge task's instructions, schema and
``max_tokens``; the endpoint's pins (``e1_extract.endpoint_pins``) and its route (``judge_record`` to the endpoint,
no escalation); boundary :data:`BOUNDARY` with data label :data:`DATA_LABEL` (``e1_extract.check_data_label``); the
part. It then judges the part's questions in order with ``Runtime.run(..., ref="j1-<part>-<i>")``: an
``InferenceError`` of a ``runtime.VALIDATION_KINDS`` kind is a model failure (``unknown``, scored), any other one a
transport failure (not scored), and an ``InferenceBoundaryError`` exits 2. It stops before a question once
``--budget-seconds`` have passed (``stopped: budget``) or after ``extract.BREAKER_AFTER`` consecutive failures that
``extract.server_down`` calls the server's (``stopped: server_down``); the rest are not sent.

**Files** under ``<runs dir>/j1/<run id>/``: ``verdicts.jsonl`` (flushed per question), ``ledger.jsonl`` and
``run.json`` (``kind: lab_j1_run``; written with ``complete: false`` before the first call and again at the end): the
run id, endpoint, part and parts, the questions planned and done, ``complete``, ``stopped``, ``measurement``
(``experiments.common.measurement_flag``: false when a fake answered), the models listed and served, the pins, the
prereg's sha256, the sha256 of ``verdicts.jsonl`` and ``ledger.jsonl``, the failures by class and kind, the latency
median and 95th percentile of the successful calls, the budget, and the start and finish times.

Exit codes: 0 complete; 1 stopped (budget or server down); 2 usage, pins or the boundary; 130 interrupted.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mycelic.collective import stats
from mycelic.collective.edge.extract import BREAKER_AFTER, server_down
from mycelic.collective.edge.records import WindowRecord
from mycelic.collective.edge.verify import JUDGE_TASK, decide, judge_payload, judge_schema, judge_task, lexical_judge
from mycelic.collective.experiments import e1_extract
from mycelic.collective.experiments.common import (ROOT, UsageError, check_run_id, code_commit, code_dirty, code_files,
                                                   code_hash as common_code_hash, fail, measurement_flag, run_dir,
                                                   utc_clock, write_json_atomic)
from mycelic.collective.inference.client import list_models
from mycelic.collective.inference.errors import InferenceBoundaryError, InferenceError
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import ConfigError, key_problem, load_routing, parse_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.packs.loader import FrozenPack, PackError, load_pack
from mycelic.collective.schemacheck import compile as compile_schema

CLI = "lab.j1"
KIND = "j1"
SCHEMA_VERSION = 1
KINDS = ("positive", "negative")
VERDICTS = ("confirm", "refute", "unknown")
ANSWERS = ("yes", "no", "unclear")
EXCLUDED = ("unknown_or_other",)
# the old and the new name NHTSA's files use for one component (CHOICE-J001.md, trap 2; PACK-V2.md)
TWINS = (("engine_and_engine_cooling", "engine"), ("fuel_system_gasoline", "fuel_propulsion_system"),
         ("service_brakes_hydraulic", "service_brakes"))
ENTITY_TYPE = "vehicle"
BOUNDARY = "site:lab"
DATA_LABEL = "public"
DEADLINE_S = 600
MAX_RETRIES = 1
MAX_PARTS = 30
WITHHOLD_SHARE = 0.01        # more than this share of a model's records left out by transport failures withholds it
CODE_FILES = ("mycelic/collective/edge/verify.py", "mycelic/collective/edge/extract.py",
              "mycelic/collective/edge/records.py", "mycelic/collective/packs/canonical.py",
              "mycelic/collective/packs/loader.py", "mycelic/collective/inference/*.py",
              "mycelic/collective/schemacheck.py", "mycelic/collective/jsonio.py", "mycelic/collective/stats.py",
              "lab/j1.py", "lab/goldlabels.py")
QUESTION_KEYS = ("question_id", "record_ref", "part", "kind", "entity_type", "entity_id", "predicate", "filed")
LINE_KEYS = ("question_id", "record_ref", "kind", "predicate", "mentions_entity", "describes_predicate", "verdict",
             "error_kind", "scored")
PREREG_KEYS = ("schema_version", "kind", "experiment", "pack", "labels", "questions", "lexical", "task", "code_hash",
               "code_files", "code_commit", "code_dirty", "endpoints", "boundary", "data_label", "parts", "seed",
               "twins", "excluded", "bootstrap_b", "bootstrap_seed", "withhold_share")
METRICS = ("sensitivity", "specificity", "balanced_accuracy", "accuracy", "unknown_share")
HEADLINES = ("better", "worse", "not_told_apart")
_clock = time.monotonic                 # the budget's clock (tests replace it)


class J1Error(ValueError):
    """A problem with the labels or the questions; ``problem`` never holds a narrative."""

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


# --------------------------------------------------------------------------------------------------- questions

def twin_of(predicate: str) -> str | None:
    """The other name of ``predicate`` in :data:`TWINS`, or None."""
    for old, new in TWINS:
        if predicate == old:
            return new
        if predicate == new:
            return old
    return None


def parse_labels(data: bytes) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """``(record, gold)`` per label line, in file order."""
    out = []
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        doc = strict_load(line)
        if not isinstance(doc, dict) or sorted(doc) != ["gold", "record"]:
            raise J1Error("a label line is not {record, gold}")
        out.append((doc["record"], doc["gold"]))
    return out


def _entity(record: Mapping[str, Any], gold: Sequence[Mapping[str, Any]]) -> str:
    vehicles = record.get("entities", {}).get(ENTITY_TYPE, [])
    if len(vehicles) != 1 or any(g["entity_type"] != ENTITY_TYPE or g["entity_id"] != vehicles[0] for g in gold):
        raise J1Error("a labelled record does not name exactly one structured vehicle")
    return vehicles[0]


def build_questions(pack: FrozenPack, labels_bytes: bytes, *, seed: int, parts: int) -> tuple[bytes, dict[str, Any]]:
    """The questions file's bytes and its record (see the module docstring)."""
    labels = parse_labels(labels_bytes)
    n = len(labels)
    if not 1 <= parts <= MAX_PARTS or n < parts:
        raise J1Error("parts must be 1 to 30 and at most the number of records")
    lines: list[str] = []
    sizes = [0] * parts
    for i, (record, gold) in enumerate(labels):
        ref = record["record_ref"]
        entity = _entity(record, gold)
        filed = sorted({g["predicate"] for g in gold})
        if not filed or any(p not in pack.predicates for p in filed):
            raise J1Error("a labelled record has no filed predicate of the pack")
        left = set(pack.predicates) - set(filed) - set(EXCLUDED) - {twin_of(p) for p in filed}
        positive = random.Random(f"j1:{seed}:{ref}:positive").choice(filed)
        negative = random.Random(f"j1:{seed}:{ref}:negative").choice(sorted(left))
        part = i * parts // n + 1
        sizes[part - 1] += 1
        for kind, predicate in zip(KINDS, (positive, negative)):
            lines.append(canonical_dumps({"question_id": f"{ref}:{kind}", "record_ref": ref, "part": part, "kind": kind,
                                          "entity_type": ENTITY_TYPE, "entity_id": entity, "predicate": predicate,
                                          "filed": filed}) + "\n")
    data = "".join(lines).encode("utf-8")
    record = {"schema_version": SCHEMA_VERSION, "kind": "lab_j1_questions", "seed": seed, "parts": parts,
              "records": n, "questions": len(lines), "part_records": sizes,
              "excluded": list(EXCLUDED), "twins": [list(t) for t in TWINS], "sha256": sha256_hex(data)}
    return data, record


def read_questions(data: bytes) -> list[dict[str, Any]]:
    """The question lines, each checked for its keys and kind."""
    out = []
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        q = strict_load(line)
        if not isinstance(q, dict) or sorted(q) != sorted(QUESTION_KEYS) or q["kind"] not in KINDS:
            raise J1Error("a question line is malformed")
        out.append(q)
    return out


def jsonl_bytes(lines: Iterable[Mapping[str, Any]]) -> bytes:
    return "".join(canonical_dumps(line) + "\n" for line in lines).encode("utf-8")


# --------------------------------------------------------------------------------------------------- judging

def window_record(record: Mapping[str, Any]) -> WindowRecord:
    """A label record as the verifier holds one of its own: codes empty (a record with codes is refused), its
    structured entities, language and narrative. The payload never carries persons or the reporter."""
    if record.get("codes"):
        raise J1Error("a label record still holds its codes")
    ref = record["record_ref"]
    return WindowRecord(record_ref=ref, iso_week="", root_ref=ref, reporter_id=None, language=record["language"],
                        codes=[], structured={k: list(v) for k, v in record["entities"].items()},
                        narrative=record["narrative"], synthetic=bool(record.get("synthetic")),
                        persons=record.get("persons"))


def question_params(question: Mapping[str, Any]) -> dict[str, str]:
    return {"entity_type": question["entity_type"], "entity_id": question["entity_id"],
            "predicate": question["predicate"]}


def payload(pack: FrozenPack, question: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """The shipped judge payload for one question about one label record."""
    return judge_payload(pack, {"params": question_params(question)}, window_record(record))


def verdict(window: WindowRecord, reply: Mapping[str, str] | None) -> str:
    """The verifier's rule over the one record: a reply judged, or one failure when no reply passed."""
    if reply is None:
        return decide([window], [], 1).verdict
    return decide([window], [(window, reply)], 0).verdict


def verdict_line(question: Mapping[str, Any], window: WindowRecord, reply: Mapping[str, str] | None,
                 error_kind: str | None) -> dict[str, Any]:
    return {"question_id": question["question_id"], "record_ref": question["record_ref"], "kind": question["kind"],
            "predicate": question["predicate"],
            "mentions_entity": reply["mentions_entity"] if reply is not None else None,
            "describes_predicate": reply["describes_predicate"] if reply is not None else None,
            "verdict": verdict(window, reply), "error_kind": error_kind,
            "scored": e1_extract.failure_class(error_kind) != "transport"}


def lexical_verdicts(pack: FrozenPack, labels_bytes: bytes,
                     questions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The verifier's lexical judge (``verify.lexical_judge``, its judge when no model runs) on every question's
    payload, as verdict lines."""
    records = {r["record_ref"]: r for r, _ in parse_labels(labels_bytes)}
    handler = lexical_judge(pack, Canonicaliser(pack))
    out = []
    for q in questions:
        record = records[q["record_ref"]]
        out.append(verdict_line(q, window_record(record), handler(payload(pack, q, record)), None))
    return out


# --------------------------------------------------------------------------------------------------- scoring

def _interval(clusters: list[list[float]], b: int, seed: str) -> dict[str, Any]:
    if not clusters:
        return {"value": None, "ci_low": None, "ci_high": None}
    result = stats.cluster_bootstrap_mean(clusters, B=b, seed=seed)
    return {"value": result["mean"], "ci_low": result["ci_low"], "ci_high": result["ci_high"]}


def _pairs(questions: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Mapping[str, Any]]]:
    out: dict[str, dict[str, Mapping[str, Any]]] = {}
    for q in questions:
        out.setdefault(q["record_ref"], {})[q["kind"]] = q
    return out


def correct(line: Mapping[str, Any]) -> bool:
    return line["verdict"] == ("confirm" if line["kind"] == "positive" else "refute")


def split_records(questions: Sequence[Mapping[str, Any]], lines: Mapping[str, Mapping[str, Any]],
                  records: Iterable[str] | None = None) -> tuple[list[str], list[str], list[str]]:
    """(scored, dropped, not judged) record refs, in question order, among ``records`` when given."""
    keep = set(records) if records is not None else None
    scored, dropped, not_judged = [], [], []
    for ref, pair in _pairs(questions).items():
        if keep is not None and ref not in keep:
            continue
        got = [lines.get(pair[k]["question_id"]) if k in pair else None for k in KINDS]
        if any(g is not None and g["scored"] is not True for g in got):
            dropped.append(ref)
        elif any(g is None for g in got):
            not_judged.append(ref)
        else:
            scored.append(ref)
    return scored, dropped, not_judged


def _failures(lines: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {c: {"questions": 0, "by_kind": {}} for c in ("model", "transport")}
    for line in lines:
        cls = e1_extract.failure_class(line["error_kind"])
        if cls is not None:
            out[cls]["questions"] += 1
            out[cls]["by_kind"][line["error_kind"]] = out[cls]["by_kind"].get(line["error_kind"], 0) + 1
    for entry in out.values():
        entry["by_kind"] = dict(sorted(entry["by_kind"].items()))
    return out


def answer_key(line: Mapping[str, Any]) -> str:
    """``<mentions_entity>_<describes_predicate>``, or ``failed`` for a call that got no valid reply."""
    if line["mentions_entity"] is None:
        return "failed"
    return f"{line['mentions_entity']}_{line['describes_predicate']}"


def score(questions: Sequence[Mapping[str, Any]], verdicts: Iterable[Mapping[str, Any]], *, bootstrap_b: int,
          bootstrap_seed: int, records: Iterable[str] | None = None) -> dict[str, Any]:
    """A judge's scores on the questions (see the module docstring); ``records`` limits them to those records."""
    lines = {line["question_id"]: line for line in verdicts}
    scored, dropped, not_judged = split_records(questions, lines, records)
    pairs = _pairs(questions)
    seed = f"j1:{bootstrap_seed}"
    pos = [[float(correct(lines[pairs[ref]["positive"]["question_id"]]))] for ref in scored]
    neg = [[float(correct(lines[pairs[ref]["negative"]["question_id"]]))] for ref in scored]
    both = [p + n for p, n in zip(pos, neg)]
    unknown = [[float(lines[pairs[ref][k]["question_id"]]["verdict"] == "unknown") for k in KINDS] for ref in scored]
    accuracy = _interval(both, bootstrap_b, seed)
    considered = [lines[pairs[ref][k]["question_id"]] for ref in (*scored, *dropped, *not_judged) for k in KINDS
                  if k in pairs[ref] and pairs[ref][k]["question_id"] in lines]
    answers = {k: {} for k in KINDS}
    verdict_counts = {k: dict.fromkeys(VERDICTS, 0) for k in KINDS}
    for ref in scored:
        for k in KINDS:
            line = lines[pairs[ref][k]["question_id"]]
            key = answer_key(line)
            answers[k][key] = answers[k].get(key, 0) + 1
            verdict_counts[k][line["verdict"]] += 1
    left = len(scored) + len(dropped)
    return {"records_scored": len(scored), "records_dropped": len(dropped), "records_not_judged": len(not_judged),
            "dropped_share": len(dropped) / left if left else None, "questions_scored": 2 * len(scored),
            "sensitivity": _interval(pos, bootstrap_b, seed), "specificity": _interval(neg, bootstrap_b, seed),
            "balanced_accuracy": dict(accuracy), "accuracy": accuracy,
            "unknown_share": _interval(unknown, bootstrap_b, seed),
            "answers": {k: dict(sorted(answers[k].items())) for k in KINDS}, "verdicts": verdict_counts,
            "failures": _failures(considered), "bootstrap": {"b": bootstrap_b, "seed": seed}}


def scored_records(questions: Sequence[Mapping[str, Any]], verdicts: Iterable[Mapping[str, Any]]) -> list[str]:
    lines = {line["question_id"]: line for line in verdicts}
    return split_records(questions, lines)[0]


def headline(model_ba: Mapping[str, Any] | None, lexical_ba: float | None) -> str | None:
    """``better`` when the low end of the model's interval is above the lexical judge's balanced accuracy, ``worse``
    when its high end is below it, else ``not_told_apart`` (rule 7); None without the numbers."""
    low = model_ba.get("ci_low") if isinstance(model_ba, Mapping) else None
    high = model_ba.get("ci_high") if isinstance(model_ba, Mapping) else None
    if low is None or high is None or lexical_ba is None:
        return None
    if low > lexical_ba:
        return "better"
    if high < lexical_ba:
        return "worse"
    return "not_told_apart"


def headline_for(*, complete: bool, display_class: str | None, measurement: bool | None,
                 dropped_share: float | None, model_ba: Mapping[str, Any] | None,
                 lexical_ba: float | None) -> tuple[str | None, str | None]:
    """(headline, why there is none): ``incomplete`` unless every part finished, ``not_measured`` unless the model's
    display class is ``model`` and its runs say ``measurement: true``, ``withheld`` when transport failures left out
    more than :data:`WITHHOLD_SHARE` of its records; else :func:`headline` and None."""
    if not complete:
        return None, "incomplete"
    if display_class != "model" or measurement is not True:
        return None, "not_measured"
    if dropped_share is None or dropped_share > WITHHOLD_SHARE:
        return None, "withheld"
    return headline(model_ba, lexical_ba), None


def paired(questions: Sequence[Mapping[str, Any]], model: Iterable[Mapping[str, Any]],
           lexical: Iterable[Mapping[str, Any]], *, bootstrap_b: int, bootstrap_seed: int) -> dict[str, Any] | None:
    """Model minus lexical balanced accuracy record by record, over the records both score, with a paired
    percentile bootstrap (``stats.paired_bootstrap``); None without a common record."""
    m = {line["question_id"]: line for line in model}
    x = {line["question_id"]: line for line in lexical}
    common = sorted(set(split_records(questions, m)[0]) & set(split_records(questions, x)[0]))
    if not common:
        return None
    pairs = _pairs(questions)

    def per_record(lines: Mapping[str, Mapping[str, Any]], ref: str) -> float:
        return sum(float(correct(lines[pairs[ref][k]["question_id"]])) for k in KINDS) / 2

    result = stats.paired_bootstrap([per_record(m, r) for r in common], [per_record(x, r) for r in common],
                                    B=bootstrap_b, seed=f"j1:{bootstrap_seed}")
    return {k: result[k] for k in ("n", "mean_diff", "ci_low", "ci_high", "B", "seed", "method")}


# --------------------------------------------------------------------------------------------------- pins

def endpoint(entry: Mapping[str, Any], base_url: str) -> dict[str, Any]:
    """One J1 endpoint: the model's alias and transport at :data:`BOUNDARY`."""
    return {"provider": "openai_compat", "boundary": BOUNDARY, "base_url": base_url, "model": entry["alias"],
            "response_format": entry["response_format"], "transport_schema": entry["transport_schema"],
            "connect_timeout_s": 5, "deadline_s": DEADLINE_S, "max_retries": MAX_RETRIES}


def routing_doc(models: Mapping[str, Mapping[str, Any]], keys: Sequence[str], base_url: str) -> dict[str, Any]:
    """The only builder of J1 routing: endpoints keyed by model key; with one key (a unit's), ``judge_record`` is
    routed to it without escalation, as ``SiteVerifier`` takes the route. The preregistration's routing pins every
    model at ``lab.prereg.PLACEHOLDER_BASE_URL`` and routes nothing."""
    routes = {JUDGE_TASK: {"endpoint": keys[0]}} if len(keys) == 1 else {}
    return {"schema_version": 1, "endpoints": {key: endpoint(models[key], base_url) for key in keys}, "routes": routes}


def endpoint_pins(routing: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``[{"name", <e1_extract.PINNED_ENDPOINT_FIELDS>}]`` of a routing document, in key order."""
    config = parse_routing(dict(routing), check_env=False)
    return [{"name": name, **e1_extract.endpoint_pins(config.endpoints[name])} for name in sorted(config.endpoints)]


def code_files_list() -> list[str]:
    return code_files(CODE_FILES, ROOT)


def code_hash() -> str:
    """sha256 over :data:`CODE_FILES`, built as ``e1_extract.e1_code_hash`` builds E1's."""
    return common_code_hash([ROOT / p for p in code_files_list()])


def task_pins() -> dict[str, Any]:
    task = judge_task()
    return {"name": task.name, "data_class": task.data_class, "instructions_sha256": sha256_hex(task.instructions),
            "schema_sha256": sha256_hex(canonical_bytes(judge_schema())), "max_tokens": task.max_tokens}


def pack_pins(pack: FrozenPack, ref: str) -> dict[str, Any]:
    return {"ref": ref, "id": pack.id, "version": pack.version, "vocabulary_hash": pack.vocabulary_hash,
            "config_hash": pack.config_hash}


def prereg_doc(*, pack: FrozenPack, pack_ref: str, labels: Mapping[str, Any], questions: Mapping[str, Any],
               lexical_sha256: str, routing: Mapping[str, Any], seed: int, parts: int, bootstrap_b: int,
               bootstrap_seed: int) -> dict[str, Any]:
    """``prereg/j1/prereg.json``: everything a unit checks before its first call, and the settings of rule 6."""
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j1_prereg", "experiment": "J1",
            "pack": pack_pins(pack, pack_ref),
            "labels": {k: labels[k] for k in ("source", "n", "seed", "records", "claims", "sha256")},
            "questions": {k: questions[k] for k in ("seed", "parts", "records", "questions", "part_records",
                                                     "sha256")},
            "lexical": {"sha256": lexical_sha256}, "task": task_pins(), "code_hash": code_hash(),
            "code_files": code_files_list(), "code_commit": code_commit(),
            "code_dirty": code_dirty(["mycelic/collective", "lab"]), "endpoints": endpoint_pins(routing),
            "boundary": BOUNDARY, "data_label": DATA_LABEL, "parts": parts, "seed": seed,
            "twins": [list(t) for t in TWINS], "excluded": list(EXCLUDED), "bootstrap_b": bootstrap_b,
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
    if failed or not isinstance(doc, dict) or sorted(doc) != sorted(PREREG_KEYS) or doc["kind"] != "lab_j1_prereg" \
            or doc["schema_version"] != SCHEMA_VERSION:
        raise UsageError("the preregistration is not a J1 prereg.json") from None
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
    _pin("the judge task", prereg["task"], task_pins())
    labels, labels_sha = e1_extract.read_labels(args.labels, pack, Canonicaliser(pack))
    _pin("the labels' sha256", prereg["labels"]["sha256"], labels_sha)
    e1_extract.check_data_label(labels, prereg["data_label"])
    questions_bytes = _read(args.questions, "the questions")
    _pin("the questions' sha256", prereg["questions"]["sha256"], sha256_hex(questions_bytes))
    try:
        questions = read_questions(questions_bytes)
    except (J1Error, StrictJsonError):
        raise UsageError("the questions file is malformed") from None
    _pin("the boundary", prereg["boundary"], BOUNDARY)
    _pin("the data label", prereg["data_label"], DATA_LABEL)
    try:
        config = load_routing(args.routing, tasks=(JUDGE_TASK,), check_env=False)
    except ConfigError as exc:
        raise UsageError(f"routing: {exc}") from None
    pinned = {e["name"]: e for e in prereg["endpoints"]}
    if args.endpoint not in pinned or args.endpoint not in config.endpoints:
        raise UsageError("the endpoint is not in the preregistration or the routing") from None
    ep = config.endpoints[args.endpoint]
    _pin("the endpoint's pins", {k: v for k, v in pinned[args.endpoint].items() if k != "name"},
         e1_extract.endpoint_pins(ep))
    route = config.routes.get(JUDGE_TASK)
    if route is None or route.endpoint != args.endpoint or route.escalate_to is not None:
        raise UsageError("the routing must route judge_record to the endpoint without escalation") from None
    if ep.provider != "openai_compat":
        raise UsageError("the endpoint is not openai_compat: J1 judges with real servers only") from None
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
    return {"schema_version": SCHEMA_VERSION, "kind": "lab_j1_run", "experiment": "J1", "run_id": args.run_id,
            "endpoint": args.endpoint, "part": args.part, "parts": c.prereg["parts"],
            "questions_planned": len(c.questions), "prereg_sha256": c.prereg_sha,
            "pinned": {"labels_sha256": c.labels_sha, "questions_sha256": c.questions_sha,
                       "vocabulary_hash": c.pack.vocabulary_hash, "config_hash": c.pack.config_hash,
                       "code_hash": c.prereg["code_hash"], "task": c.prereg["task"],
                       **e1_extract.endpoint_pins(c.endpoint)},
            "boundary": BOUNDARY, "data_label": DATA_LABEL, "model_requested": c.endpoint.model,
            "budget_seconds": args.budget_seconds, "code_commit": code_commit(), "started_at": utc_clock()}


def _latency(rows: Sequence[Mapping[str, Any]]) -> tuple[float | None, float | None]:
    values = [r["latency_ms"] for r in rows if r["ok"] and r["attempt"] >= 1 and r["latency_ms"] is not None]
    return stats.percentile(values, 50), stats.percentile(values, 95)


def _execute(args: argparse.Namespace, c: _Checked) -> int:
    out = c.out_dir
    base = _base(args, c)
    out.mkdir(parents=True)
    write_json_atomic(out / "run.json", {**base, "complete": False, "stopped": None, "questions_done": 0,
                                         "measurement": False, "models_listed": None, "listed_fake": None,
                                         "models_served": [], "verdicts_sha256": None, "ledger_sha256": None,
                                         "failures": None, "latency_ms_p50": None, "latency_ms_p95": None,
                                         "finished_at": None})
    try:
        rt = Runtime(c.config, boundary=BOUNDARY, ledger_path=out / "ledger.jsonl", run_id=args.run_id,
                     clock=utc_clock, data_label=DATA_LABEL)
    except ConfigError as exc:
        return fail(str(exc))
    task, schema = judge_task(), compile_schema(judge_schema())
    if rt.exemption(task) is not None:          # the endpoint is the runtime's own boundary: never an exemption
        rt.close()
        return fail("the judge route would leave the boundary under an exemption")
    lines: list[dict[str, Any]] = []
    stopped: str | None = None
    interrupted = boundary = False
    listed: dict[str, Any] = {"ok": False, "ids": [], "fake": None}
    t0 = _clock()
    try:
        listed = list_models(c.endpoint)
        down = 0
        with open(out / "verdicts.jsonl", "w", encoding="utf-8", newline="\n") as fh:
            for i, q in enumerate(c.questions):
                if _clock() - t0 >= args.budget_seconds:
                    stopped = "budget"
                    break
                if down >= BREAKER_AFTER:
                    stopped = "server_down"
                    break
                record = c.records[q["record_ref"]]
                window = window_record(record)
                reply, error_kind, down_now = None, None, False
                try:
                    reply = rt.run(task, payload(c.pack, q, record), schema, ref=f"j1-{args.part}-{i:04d}")
                except InferenceBoundaryError:
                    boundary = True
                    break
                except InferenceError as err:
                    error_kind = err.kind
                    down_now = server_down(err, rt, JUDGE_TASK)
                down = down + 1 if down_now else 0
                line = verdict_line(q, window, reply, error_kind)
                fh.write(canonical_dumps(line) + "\n")
                fh.flush()
                lines.append(line)
    except KeyboardInterrupt:
        interrupted = True
        stopped = "interrupted"
    finally:
        rt.close()
    rows = read_ledger(out / "ledger.jsonl") if (out / "ledger.jsonl").exists() else []
    p50, p95 = _latency(rows)
    complete = not interrupted and not boundary and stopped is None and len(lines) == len(c.questions)
    doc = {**base, "complete": complete, "stopped": stopped, "questions_done": len(lines),
           "measurement": measurement_flag([c.endpoint], rows, bool(listed.get("fake"))),
           "models_listed": listed["ids"] if listed.get("ok") else None, "listed_fake": listed.get("fake"),
           "models_served": sorted({r["model_served"] for r in rows if r["model_served"]}),
           "verdicts_sha256": _file_sha(out / "verdicts.jsonl"), "ledger_sha256": _file_sha(out / "ledger.jsonl"),
           "failures": _failures(lines), "latency_ms_p50": p50, "latency_ms_p95": p95, "finished_at": utc_clock()}
    write_json_atomic(out / "run.json", doc)
    if boundary:
        return fail("the runtime refused the judge route at its boundary")
    if interrupted:
        print(f"j1 run: interrupted after {len(lines)} questions; run.json says complete=false", file=sys.stderr)
        return 130
    print(f"j1 run: {args.endpoint} part {args.part}: {len(lines)} of {len(c.questions)} questions; "
          f"complete={str(complete).lower()} stopped={stopped} measurement={str(doc['measurement']).lower()}")
    return 0 if complete else 1


def cmd_run(args: argparse.Namespace) -> int:
    try:
        checked = _check(args)
    except UsageError as exc:
        return fail(str(exc))
    return _execute(args, checked)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.j1", description="Judge test J001: one part of one model.")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="judge one part's questions with one endpoint")
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

"""Warm a started model server up with the real tasks and schemas before any unit is measured on it.

:func:`warm_tasks` lists what a serving class will ask the server: for G0 and sim units the pack's ``extract_claims``
and ``judge_record`` tasks (not streamed), for E1 units only ``extract_claims``, for E2 units ``judge_record`` and,
when the central comparator is the model itself (``central: self``), its ``judge_candidate_raw`` and
``judge_candidate_allowed`` (a hosted central comparator is never warmed or fitted on this server; ``lab.hosted``'s
preflight sends it the same payloads), for E3 units each workload's task
(streamed), one :class:`WarmTask` per task and pack (shared by the units of that pack; the first such unit's seed
picks the record), sorted by (task name, pack). Each carries a typical payload, a worst case and its deadline:

* extraction: from ``generate(pack, <unit seed>, 1, baseline_weeks + window_weeks)``, the record with the longest
  narrative among the first 50 (ties by ``record_ref``) as ``model_payload`` builds it; the worst case is that
  record's narrative repeated past ``max_input_chars`` and cut as ``extract.truncate`` cuts it;
* judge: :func:`judge_example` for that record (``judge_payload`` asked about its first entity and the pack's first
  sorted predicate), with the typical or the worst narrative;
* the central tasks: :func:`e2_worst_payloads` for both payloads (the warm-up's call then times the worst case, which
  the E2 projection scales), ``judge_candidate_raw`` with a 1800 s deadline;
* E3: the workload's seeded filler (``warmup:<seed>:<workload>``), for both.

:func:`e2_worst_payloads` builds the largest payload an E2 run can send its central comparator from the model-free
rehearsal (``lab.prereg``), which retrieved exactly the records the run will: the rehearsal's largest item has
``central_raw_records.max`` records, and the payload holds that many of the longest narratives of every preregistered
seed's planted world, in the shape ``e2_pushdown`` sends (an upper bound on any real payload), asked about the
rehearsal's ``worst`` key over the world's weeks. A worst case beyond the slot skips the E2 units before any harness
starts (``context too small: judge_candidate_raw ...``, remedy ``raise e2_ctx_per_slot``).

:func:`warm_up` then, in order:

1. **fit**: ``POST /apply-template`` and ``POST /tokenize`` count each task's typical and worst-case prompt; a worst
   case plus ``max_tokens`` beyond the slot skips the task's units (``context too small``, remedy ``raise
   ctx_per_slot`` or ``e3_ctx_per_slot``), and an endpoint that does not answer 200 skips them too;
2. **calls**: one ``Runtime.single`` per fitting task with the typical payload, through a one-endpoint routing doc
   built exactly as the units' (the endpoint's ``response_format`` and transport), ledgered to
   ``OUT/server/warmup.ledger.jsonl`` (never under ``runs/``). An HTTP error or a network failure skips the task's
   units (remedy: more context or the reduced transport), ``finish_reason: length`` skips them (remedy: reasoning off
   or more tokens), a reply naming another model raises :data:`~lab.notes.ALIAS_MISMATCH`, and a schema-invalid
   reply is only recorded;
3. **probe**: the first fitting task's request body, sent twice as raw HTTP; any ``reasoning_content`` stops the
   whole start (remedy: reasoning off), and ``repeat_identical`` records whether the two replies were the same;
4. **memory**: ``MemAvailable`` below :data:`~lab.server.MIN_MEM_AFTER_LOAD` stops the whole start.

``budget_s`` (the shard's time left for it) bounds the whole warm-up: every request's timeout is cut to what is left
of it, and once less than :data:`MIN_REQUEST_S` is left, or a request went unanswered because the budget ran out, the
whole start stops with :data:`~lab.notes.BUDGET_EXHAUSTED` rather than blaming the context or the schema. The record's
``unanswered`` says whether any request got no HTTP answer at all, so the caller can look for a server that died under
the warm-up before it believes a finding.

A unit depends on a task when the task is one of its required tasks or one its adapter routes; :class:`WarmTask`
lists those units.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from mycelic.collective.edge.extract import (TASK_NAME, extraction_schema, extraction_task, model_payload,
                                             truncate)
from mycelic.collective.edge.records import WindowRecord
from mycelic.collective.edge.verify import judge_payload, judge_question, judge_schema, judge_task
from mycelic.collective.edge.weeks import iso_week, local_date
from mycelic.collective.evaluate.baselines import world_weeks
from mycelic.collective.evaluate.plant import load_plant, plant
from mycelic.collective.experiments.common import utc_clock
from mycelic.collective.experiments.e2_pushdown import CENTRAL_SCHEMA, CENTRAL_TASKS, allowed_view, central_task
from mycelic.collective.experiments.e3_latency import WORKLOADS, filler
from mycelic.collective.inference.routing import parse_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.inference.tasks import TaskSpec, render_messages
from mycelic.collective.jsonio import sha256_hex
from mycelic.collective.packs.generator import generate
from mycelic.collective.packs.loader import FrozenPack, load_pack
from mycelic.collective.schemacheck import Schema
from mycelic.collective.schemacheck import compile as compile_schema

from . import ROOT

from .notes import (ALIAS_MISMATCH, BUDGET_EXHAUSTED, CONTEXT_TOO_SMALL, MEMORY_AFTER_LOAD, REMEDY_CONTEXT,
                    REMEDY_HTTP, REMEDY_REASONING, WARMUP_HTTP, WARMUP_LENGTH, WARMUP_REASONING)
from .server import MIN_MEM_AFTER_LOAD, ServerError, loopback_json

LEDGER = "server/warmup.ledger.jsonl"
FIRST_RECORDS = 50
DEADLINE_S = 600
CENTRAL_RAW_DEADLINE_S = 1800
COUNT_TIMEOUT_S = 60
MIN_REQUEST_S = 5
MIB = 1 << 20


@dataclass(frozen=True)
class WarmTask:
    task: TaskSpec
    schema: dict[str, Any]
    payload: dict[str, Any]
    worst: dict[str, Any]
    stream: bool
    units: tuple[str, ...]
    pack: str = ""
    deadline_s: float = DEADLINE_S


# --------------------------------------------------------------------------------------------------- payloads

def _longest_record(pack: FrozenPack, seed: int) -> dict[str, Any]:
    weeks = pack.detectors["baseline_weeks"] + pack.detectors["window_weeks"]
    records = generate(pack, seed, 1, weeks).records[:FIRST_RECORDS]
    return sorted(records, key=lambda r: (-len(r["narrative"]), r["record_ref"]))[0]


def _worst_text(narrative: str, cap: int) -> str:
    repeated = " ".join([narrative] * (math.ceil((cap + 1) / (len(narrative) + 1)) + 1))
    return truncate(repeated, cap)[0]


def judge_example(pack: FrozenPack, record: Mapping[str, Any], narrative: str) -> dict[str, Any]:
    """A judge payload for a generated record: asked about its first entity (the first sorted entity type with a
    value, that type's first value; else the first sorted type and ``unknown``) and the pack's first sorted predicate,
    with ``narrative`` as the record's text. ``lab.sim`` warms its judge up with it."""
    entities = record["entities"]
    entity_type = next((t for t in sorted(entities) if entities[t] and t in pack.entity_types), None)
    if entity_type is None:
        entity_type, entity_id = sorted(pack.entity_types)[0], "unknown"
    else:
        entity_id = entities[entity_type][0]
    params = {"entity_type": entity_type, "entity_id": entity_id, "predicate": sorted(pack.predicates)[0]}
    window = WindowRecord(record_ref=record["record_ref"], iso_week="", root_ref=record["record_ref"],
                          reporter_id=None, language=record["language"], codes=list(record["codes"]),
                          structured={k: list(v) for k, v in entities.items()}, narrative=narrative)
    return judge_payload(pack, {"params": params}, window)


def e2_worst_payloads(unit: Mapping[str, Any], rehearsal: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the worst ``judge_candidate_raw`` payload, the matching ``judge_candidate_allowed`` payload) of an E2 unit;
    see the module docstring. The record shape mirrors ``e2_pushdown``'s central_raw payload (listed in
    ``docs/lab/INTEGRATION.md``; a test pins it against the harness's own requests)."""
    p = unit["params"]
    pack = load_pack(p["pack"])
    spec = load_plant(ROOT / p["plant_path"], pack)
    records: list[dict[str, Any]] = []
    for seed in p["seeds"]:
        world = generate(pack, seed, p["sites"], p["weeks"])
        records += [*world.records, *plant(world, spec, pack).records]
    longest = sorted(records, key=lambda r: (-len(r["narrative"]), r["record_ref"]))
    longest = longest[:rehearsal["central_raw_records"]["max"]]
    entity_type, rest = rehearsal["worst"]["key"].split(":", 1)
    entity_id, predicate = rest.rsplit(":", 1)
    question = judge_question(pack, {"entity_type": entity_type, "entity_id": entity_id, "predicate": predicate})
    weeks = world_weeks(pack.generator["start"], p["weeks"])
    window = {"start_week": weeks[0], "end_week": weeks[-1]}
    types = sorted(pack.mapping()["entities"])
    raw = {"question": question, "window": dict(window),
           "records": [{"site": r["site"], "week": iso_week(local_date(r["received_date"])),
                        "codes": sorted(r["codes"]), "entities": {t: list(r["entities"][t]) for t in types},
                        "language": r["language"], "text": truncate(r["narrative"], pack.extraction.max_input_chars)[0]}
                       for r in longest]}
    allowed = {"question": question, "window": dict(window), "records": [allowed_view(pack, r) for r in longest]}
    return raw, allowed


def warm_tasks(units: list[Mapping[str, Any]], plan: Mapping[str, Any], prereg: Any = None) -> list[WarmTask]:
    """The tasks a serving class's units will send, each with the units that depend on it. ``prereg`` (a
    :class:`~lab.prereg.Prereg`) gives E2's rehearsal; without it E2 units warm only their site judge."""
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def pack_tasks(unit: Mapping[str, Any], pack_id: str) -> None:
        if (TASK_NAME, pack_id) in found:
            return
        pack = load_pack(pack_id)
        record = _longest_record(pack, unit["params"]["seed"])
        typical = model_payload(record, pack)[0]
        worst_text = _worst_text(record["narrative"], pack.extraction.max_input_chars)
        judge = judge_task()
        found[(TASK_NAME, pack_id)] = {
            "task": extraction_task(pack), "schema": extraction_schema(pack), "payload": typical,
            "worst": {"language": record["language"], "text": worst_text}, "stream": False, "units": []}
        found[(judge.name, pack_id)] = {
            "task": judge, "schema": judge_schema(), "payload": judge_example(pack, record, record["narrative"]),
            "worst": judge_example(pack, record, worst_text), "stream": False, "units": []}

    for unit in units:
        if unit["experiment"] in ("g0", "sim", "e1", "e2"):
            pack_id = unit["params"]["pack"]
            pack_tasks(unit, pack_id)
            keys = {"e1": [(TASK_NAME, pack_id)], "e2": [(judge_task().name, pack_id)]}.get(
                unit["experiment"], [(TASK_NAME, pack_id), (judge_task().name, pack_id)])
            if (unit["experiment"] == "e2" and unit["params"]["central"] == "self" and prereg is not None
                    and prereg.manifest.get("e2") is not None):
                raw, allowed = e2_worst_payloads(unit, prereg.manifest["e2"]["rehearsal"])
                for name, payload in zip(CENTRAL_TASKS, (raw, allowed)):
                    if (name, pack_id) not in found:
                        found[(name, pack_id)] = {
                            "task": central_task(name), "schema": CENTRAL_SCHEMA, "payload": payload,
                            "worst": payload, "stream": False, "units": [],
                            "deadline_s": CENTRAL_RAW_DEADLINE_S if name == CENTRAL_TASKS[0] else DEADLINE_S}
                    keys.append((name, pack_id))
        else:
            keys = []
            for workload in unit["params"]["workloads"]:
                task, schema, words = WORKLOADS[workload]
                if (task.name, "") not in found:
                    text = {"text": filler(words, f"warmup:{unit['params']['seed']}:{workload}")}
                    found[(task.name, "")] = {"task": task, "schema": schema, "payload": text, "worst": text,
                                              "stream": True, "units": []}
                keys.append((task.name, ""))
        for key in keys:
            if unit["unit"] not in found[key]["units"]:
                found[key]["units"].append(unit["unit"])
    return [WarmTask(task=v["task"], schema=v["schema"], payload=v["payload"], worst=v["worst"], stream=v["stream"],
                     units=tuple(v["units"]), pack=key[1], deadline_s=v.get("deadline_s", DEADLINE_S))
            for key, v in sorted(found.items())]


def response_format(entry: Mapping[str, Any], compiled: Schema, task: TaskSpec) -> dict[str, Any] | None:
    """What ``Runtime._response_format`` sends for this manifest entry's format and transport."""
    if entry["response_format"] == "json_schema":
        schema = compiled.full if entry["transport_schema"] == "full" else compiled.reduced
        return {"type": "json_schema", "json_schema": {"name": task.name, "schema": schema, "strict": True}}
    if entry["response_format"] == "json_object":
        return {"type": "json_object"}
    return None


def routing_doc(base_url: str, alias: str, entry: Mapping[str, Any], tasks: list[WarmTask],
                deadline_s: float = DEADLINE_S) -> dict[str, Any]:
    endpoint = {"provider": "openai_compat", "boundary": "central", "base_url": base_url, "model": alias,
                "response_format": entry["response_format"], "transport_schema": entry["transport_schema"],
                "connect_timeout_s": 5, "deadline_s": deadline_s, "max_retries": 0}
    return {"schema_version": 1, "endpoints": {"lab": endpoint},
            "routes": {t.task.name: {"endpoint": "lab"} for t in tasks}}


# --------------------------------------------------------------------------------------------------- warm-up

def _count(port: int, messages: list[dict[str, str]], timeout: Callable[[float], float]
           ) -> tuple[int | None, int | None]:
    """(status, prompt tokens): ``/apply-template`` then ``/tokenize``; the first non-200 status ends it."""
    status, applied = loopback_json(port, "POST", "/apply-template", {"messages": messages},
                                    timeout_s=timeout(COUNT_TIMEOUT_S))
    prompt = applied.get("prompt") if isinstance(applied, dict) else None
    if status != 200 or not isinstance(prompt, str):
        return status if status != 200 else None, None
    status, tokens = loopback_json(port, "POST", "/tokenize",
                                   {"content": prompt, "add_special": True, "parse_special": True},
                                   timeout_s=timeout(COUNT_TIMEOUT_S))
    listed = tokens.get("tokens") if isinstance(tokens, dict) else None
    if status != 200 or not isinstance(listed, list):
        return status if status != 200 else None, None
    return 200, len(listed)


def _http_detail(status: int | None, error_kind: str | None = None) -> str:
    if status is not None:
        return f"HTTP {status}"
    return error_kind or "no answer"


def _reply(obj: Any) -> tuple[str | None, str | None]:
    """(content, reasoning_content) of a raw chat reply."""
    choices = obj.get("choices") if isinstance(obj, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) \
        else None
    if not isinstance(message, dict):
        return None, None
    content, reasoning = message.get("content"), message.get("reasoning_content")
    return (content if isinstance(content, str) else None, reasoning if isinstance(reasoning, str) else None)


def warm_up(server: Any, entry: Mapping[str, Any], tasks: list[WarmTask], *, ctx_per_slot: int, out: Path,
            start_index: int, meminfo: Callable[[], int | None], ctx_field: str = "ctx_per_slot",
            budget_s: float | None = None) -> tuple[dict[str, Any], dict[str, str], str | None]:
    """(warm-up record, {unit: skip reason}, a reason that stops the whole start or None); see the module docstring.
    ``server`` is a started :class:`~lab.server.ModelServer` (``port``, ``base_url``, ``alias``); ``budget_s`` None
    leaves every request its own timeout."""
    skips: dict[str, str] = {}
    stop_all: str | None = None
    end = None if budget_s is None else time.monotonic() + budget_s

    def timeout(cap: float) -> float:
        return cap if end is None else max(0.001, min(cap, end - time.monotonic()))

    def spent() -> bool:
        return end is not None and end - time.monotonic() < MIN_REQUEST_S

    def skip(task: WarmTask, reason: str) -> None:
        for uid in task.units:
            skips.setdefault(uid, reason)

    unanswered = False                       # a request got no HTTP answer at all (the server may have died)

    rows: list[dict[str, Any]] = []
    fitting: list[tuple[WarmTask, dict[str, Any]]] = []
    for t in tasks:
        if spent():
            stop_all = BUDGET_EXHAUSTED
            break
        compiled = compile_schema(t.schema)
        row: dict[str, Any] = {"task": t.task.name, "pack": t.pack or None, "units": list(t.units),
                               "http_status": None, "ok": None, "error_kind": None, "finish_reason": None,
                               "tokens_in": None, "prompt_tokens_counted": None, "worst_prompt_tokens": None,
                               "max_tokens": t.task.max_tokens, "fits": None, "stream_usage": None}
        rows.append(row)
        status, typical = _count(server.port, render_messages(t.task, t.payload, compiled), timeout)
        worst_status, worst = _count(server.port, render_messages(t.task, t.worst, compiled), timeout)
        if status != 200 or worst_status != 200:
            bad = status if status != 200 else worst_status
            unanswered = unanswered or bad is None
            if bad is None and spent():
                stop_all = BUDGET_EXHAUSTED
                break
            skip(t, f"{WARMUP_HTTP.format(task=t.task.name, detail=_http_detail(bad))}; {REMEDY_HTTP}")
            continue
        row.update(prompt_tokens_counted=typical, worst_prompt_tokens=worst, fits=worst + t.task.max_tokens
                   <= ctx_per_slot)
        if not row["fits"]:
            reason = CONTEXT_TOO_SMALL.format(task=t.task.name, needed=worst + t.task.max_tokens, prompt=worst,
                                              max_tokens=t.task.max_tokens, slot=ctx_per_slot)
            skip(t, f"{reason}; {REMEDY_CONTEXT.format(field=ctx_field)}")
            continue
        fitting.append((t, row))

    routed = [t for t, _ in fitting]
    for t, row in fitting:
        if stop_all is None and spent():
            stop_all = BUDGET_EXHAUSTED
        if stop_all is not None:
            break
        # one Runtime per call, so that its deadline is what is left of the budget (the same ledger and run id)
        config = parse_routing(routing_doc(server.base_url, server.alias, entry, routed,
                                           deadline_s=timeout(t.deadline_s)), check_env=False)
        runtime = Runtime(config, boundary="central", ledger_path=Path(out) / LEDGER, run_id=f"warmup-{start_index}",
                          clock=utc_clock, data_label="synthetic", allow_external_raw="synthetic")
        try:
            attempt = runtime.single(t.task, t.payload, compile_schema(t.schema), ref=f"warmup:{t.task.name}",
                                     stream=t.stream)
        finally:
            runtime.close()
        r = attempt.row
        row.update(http_status=r["http_status"], ok=r["ok"], error_kind=r["error_kind"],
                   finish_reason=r["finish_reason"], tokens_in=r["tokens_in"],
                   stream_usage=(r["tokens_out"] is not None) if t.stream else None)
        unanswered = unanswered or r["http_status"] is None
        if r["http_status"] is None and spent():
            stop_all = BUDGET_EXHAUSTED
            break
        if (r["http_status"] or 0) >= 400 or r["error_kind"] in ("network", "timeout"):
            detail = _http_detail(r["http_status"], r["error_kind"])
            skip(t, f"{WARMUP_HTTP.format(task=t.task.name, detail=detail)}; {REMEDY_HTTP}")
        elif r["finish_reason"] == "length":
            skip(t, f"{WARMUP_LENGTH.format(task=t.task.name)}; {REMEDY_REASONING}")
        elif r["http_status"] == 200 and r["model_served"] != server.alias:
            raise ServerError(ALIAS_MISMATCH)

    probe = None
    repeat_identical = None
    if fitting and stop_all is None and spent():
        stop_all = BUDGET_EXHAUSTED
    if fitting and stop_all is None:
        t = fitting[0][0]
        compiled = compile_schema(t.schema)
        body: dict[str, Any] = {"model": server.alias, "messages": render_messages(t.task, t.payload, compiled),
                                "temperature": 0, "max_tokens": t.task.max_tokens}
        fmt = response_format(entry, compiled, t.task)
        if fmt is not None:
            body["response_format"] = fmt
        replies = [loopback_json(server.port, "POST", "/v1/chat/completions", body, timeout_s=timeout(DEADLINE_S))
                   for _ in range(2)]
        parsed = [_reply(obj) for _, obj in replies]
        reasoning = any(r is not None and r.strip() for _, r in parsed)
        contents = [c for c, _ in parsed]
        repeat_identical = contents[0] is not None and contents[0] == contents[1]
        probe = {"task": t.task.name, "http_status": [s for s, _ in replies], "reasoning_content": reasoning,
                 "content_sha256": [sha256_hex(c) if c is not None else None for c in contents]}
        unanswered = unanswered or any(status is None for status, _ in replies)
        if reasoning:
            stop_all = f"{WARMUP_REASONING}; {REMEDY_REASONING}"
        elif any(status is None for status, _ in replies) and spent():
            stop_all = BUDGET_EXHAUSTED

    available = meminfo()
    if stop_all is None and available is not None and available < MIN_MEM_AFTER_LOAD:
        stop_all = MEMORY_AFTER_LOAD.format(available=available // MIB, needed=MIN_MEM_AFTER_LOAD // MIB)
    record = {"tasks": rows, "probe": probe, "repeat_identical": repeat_identical, "mem_available_after_load":
              available, "ledger": LEDGER, "skips": dict(sorted(skips.items())), "stop_all": stop_all,
              "unanswered": unanswered}
    return record, skips, stop_all

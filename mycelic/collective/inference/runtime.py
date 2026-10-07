"""The boundary-bound model runtime: every model call in the collective layer goes through :class:`Runtime`.

Ported (adapted, not merged) from origin/claude/mycelic-implementation-vr034p@388aa30, ``mycelic/models/router.py``:
the repair-then-escalate flow (one repair on the same endpoint, then one try on a stronger one) and one ledger
record per attempt. Dropped from the port: tiers and tenant tier policy, default models and ``provider:model``
specs, the ``LLMClientAdapter`` and embeddings, the second hosted provider, the NeuralGraph ``jsonutil``/``llm``
imports (which load numpy and the NeuralGraph LLM client), recording ``str(exc)`` and problem text in the ledger, and
forwarding the repair excerpt to the escalation tier.

A runtime is constructed for one boundary (``site:<id>`` or ``central``) and never changes it; ``run`` and
``single`` take no boundary argument. Before any I/O each call checks, in order: the ``ref`` handle, the payload,
the schema, the route, the api-key variables, and the boundary guard for the primary and then the escalation
endpoint. :func:`boundary_mode` is the single source of truth for the guard:

=====================================================  ==========================================================
endpoint boundary                                      mode
=====================================================  ==========================================================
``any-simulated``                                      ``simulated`` if the provider is fake or the runtime was built
                                                       with ``simulation=True``; otherwise refused (raw and
                                                       structured tasks alike)
equal to the runtime's boundary                        ``own``
anything else, task ``data_class == 'structured'``     ``structured_egress``
anything else, ``allow_external_raw`` set              ``external_raw_exempt`` (only for synthetic or public data)
anything else                                          refused
=====================================================  ==========================================================

A refusal writes one ledger row (attempt 0, ``boundary_mode`` refused) and raises
:class:`~.errors.InferenceBoundaryError` without contacting any endpoint, including when only the escalation
endpoint is out of bounds.

Flow of :meth:`Runtime.run`: attempt 1 on the primary; on ``json_invalid`` or ``schema_invalid`` attempt 2 on the
same endpoint with a repair note appended to the user turn (:func:`~.tasks.with_repair`; the roles stay ``[system,
user]``); if that also fails validation and the route has ``escalate_to``, attempt 3 on
the escalation endpoint with the original conversation only. Nothing else (HTTP errors, timeouts, size limits,
boundary, configuration, a missing fake handler) is repaired or escalated. :meth:`Runtime.single` makes exactly one
attempt and returns the row instead of raising; the latency and extraction harnesses use it.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from ..jsonio import StrictJsonError, canonical_dumps
from ..schemacheck import Schema, SchemaError
from ..schemacheck import compile as compile_schema
from . import client
from .errors import InferenceBoundaryError, InferenceError
from .jsonparse import ReplyParseError, extract_object
from .ledger import UsageLedger, cost
from .routing import NAME_RE, ConfigError, Endpoint, RoutingConfig, key_problem, runtime_boundary_ok
from .tasks import TaskSpec, render_messages, repair_message, with_repair

logger = logging.getLogger(__name__)

DATA_LABELS = ("synthetic", "public", "partner")
EXTERNAL_RAW_LABELS = ("synthetic", "public")
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", re.ASCII)
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)
VALIDATION_KINDS = ("json_invalid", "schema_invalid")


def boundary_mode(runtime_boundary: str, endpoint: Endpoint, data_class: str, *, simulation: bool,
                  allow_external_raw: str | None) -> str:
    if endpoint.boundary == "any-simulated":
        return "simulated" if endpoint.provider == "fake" or simulation else "refused"
    if endpoint.boundary == runtime_boundary:
        return "own"
    if data_class == "structured":
        return "structured_egress"
    if allow_external_raw is not None:
        return "external_raw_exempt"
    return "refused"


@dataclass(frozen=True)
class Attempt:
    """One logical attempt: its ledger row, the validated output (None on failure) and how many content chunks a
    streamed reply arrived in (0 when not streamed; not part of the ledger)."""

    row: dict[str, Any]
    output: dict[str, Any] | None
    content_chunks: int = 0


@dataclass(frozen=True)
class _Plan:
    task: TaskSpec
    payload: dict[str, Any]
    schema: Schema
    ref: str
    primary: Endpoint
    escalation: Endpoint | None
    primary_mode: str
    escalation_mode: str | None


@dataclass(frozen=True)
class _Result:
    row: dict[str, Any]
    output: dict[str, Any] | None
    problems: list[tuple[str, str]]
    content: str | None
    finish_reason: str | None
    content_chunks: int


class Runtime:
    def __init__(self, config: RoutingConfig, *, boundary: str, ledger_path: str | Path, run_id: str,
                 clock: Callable[[], str], data_label: str, allow_fake: bool = False, simulation: bool = False,
                 allow_external_raw: str | None = None, fake: Any = None, sleep: Callable[[float], Any] = time.sleep,
                 environ: Mapping[str, str] | None = None) -> None:
        if not runtime_boundary_ok(boundary):
            raise ConfigError("boundary", "must be site:<id> or central") from None
        if data_label not in DATA_LABELS:
            raise ConfigError("data_label", f"must be one of {', '.join(DATA_LABELS)}") from None
        if allow_external_raw is not None and (allow_external_raw not in EXTERNAL_RAW_LABELS
                                               or allow_external_raw != data_label):
            raise ConfigError("allow_external_raw",
                              "must be None, or 'public'/'synthetic' equal to data_label; partner data is never "
                              "exempt") from None
        if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
            raise ConfigError("run_id", "must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}") from None
        if any(e.provider == "fake" for e in config.endpoints.values()) and (not allow_fake or fake is None):
            raise ConfigError("allow_fake", "fake endpoints need allow_fake=True and a FakeProvider") from None
        failure = None
        try:
            ledger = UsageLedger(ledger_path)
        except OSError as exc:
            failure = exc.__class__.__name__
        if failure is not None:
            raise ConfigError("ledger_path", f"not writable ({failure})") from None
        self.config = config
        self.boundary = boundary
        self.run_id = run_id
        self.data_label = data_label
        self.simulation = simulation
        self.allow_external_raw = allow_external_raw
        self.ledger = ledger
        self._clock = clock
        self._fake = fake
        self._sleep = sleep
        self._environ = environ

    def close(self) -> None:
        self.ledger.close()

    # ------------------------------------------------------------------ public calls
    def run(self, task: TaskSpec, payload: dict[str, Any], schema: dict[str, Any] | Schema, *, ref: str,
            endpoint: str | None = None) -> dict[str, Any]:
        plan = self._preflight(task, payload, schema, ref, endpoint, escalate=True)
        base = render_messages(task, payload, plan.schema)

        first = self._attempt(plan, plan.primary, plan.primary_mode, 1, base, stream=False)
        if first.output is not None:
            return first.output
        last = first
        if first.row["error_kind"] in VALIDATION_KINDS:
            repair = repair_message(first.problems, truncated=first.finish_reason == "length",
                                    previous_reply=first.content)
            second = self._attempt(plan, plan.primary, plan.primary_mode, 2, with_repair(base, repair), stream=False)
            if second.output is not None:
                return second.output
            last = second
            if second.row["error_kind"] in VALIDATION_KINDS and plan.escalation is not None:
                third = self._attempt(plan, plan.escalation, plan.escalation_mode, 3, base, stream=False,
                                      escalated_from=plan.primary.name)
                if third.output is not None:
                    return third.output
                last = third
        raise InferenceError(task=task.name, endpoint=last.row["endpoint"], kind=last.row["error_kind"],
                             http_status=last.row["http_status"]) from None

    def single(self, task: TaskSpec, payload: dict[str, Any], schema: dict[str, Any] | Schema, *, ref: str,
               endpoint: str | None = None, stream: bool = False) -> Attempt:
        """Exactly one attempt (no repair, no escalation). Failures come back in ``row['error_kind']``."""
        plan = self._preflight(task, payload, schema, ref, endpoint, escalate=False)
        result = self._attempt(plan, plan.primary, plan.primary_mode, 1,
                               render_messages(task, payload, plan.schema), stream=stream)
        return Attempt(row=result.row, output=result.output, content_chunks=result.content_chunks)

    # ------------------------------------------------------------------ pre-flight and guard
    def _preflight(self, task: TaskSpec, payload: Any, schema: Any, ref: Any, endpoint: str | None, *,
                   escalate: bool) -> _Plan:
        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a TaskSpec") from None
        if not isinstance(ref, str) or REF_RE.fullmatch(ref) is None:
            raise ValueError("ref must be an opaque handle matching [A-Za-z0-9][A-Za-z0-9_.:-]{0,95}") from None
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict") from None
        reason = None
        try:
            canonical_dumps(payload)
        except StrictJsonError as err:
            reason = err.reason
        if reason is not None:
            raise ValueError(f"payload is not canonical JSON ({reason})") from None
        compiled = schema if isinstance(schema, Schema) else compile_schema(schema)
        if compiled.top_type != "object":
            raise SchemaError("the top-level schema must be a (non-nullable) object") from None

        route = self.config.routes.get(task.name)
        if endpoint is not None:
            if not isinstance(endpoint, str) or NAME_RE.fullmatch(endpoint) is None:
                raise ConfigError("$.endpoints", "invalid endpoint name") from None
            if endpoint not in self.config.endpoints:
                raise ConfigError(f"$.endpoints.{endpoint}", "unknown endpoint") from None
            primary, escalation = self.config.endpoints[endpoint], None
        elif route is not None:
            primary = self.config.endpoints[route.endpoint]
            escalation = self.config.endpoints[route.escalate_to] if escalate and route.escalate_to else None
        else:
            raise ConfigError(f"$.routes.{task.name}", "no route") from None

        env = os.environ if self._environ is None else self._environ
        for ep in (primary, escalation):
            if ep is not None:
                problem = key_problem(ep, env)
                if problem is not None:
                    raise problem from None

        plan = _Plan(task=task, payload=payload, schema=compiled, ref=ref, primary=primary, escalation=escalation,
                     primary_mode=self._mode(primary, task), escalation_mode=None)
        if plan.primary_mode == "refused":
            self._refuse(plan, primary, escalated_from=None)
        if escalation is not None:
            mode = self._mode(escalation, task)
            if mode == "refused":
                self._refuse(plan, escalation, escalated_from=primary.name)
            plan = replace(plan, escalation_mode=mode)
        return plan

    def _mode(self, endpoint: Endpoint, task: TaskSpec) -> str:
        return boundary_mode(self.boundary, endpoint, task.data_class, simulation=self.simulation,
                             allow_external_raw=self.allow_external_raw)

    def _refuse(self, plan: _Plan, endpoint: Endpoint, *, escalated_from: str | None) -> None:
        row = self._row(plan, endpoint, "refused", 0, escalated_from=escalated_from, ok=False, error_kind="boundary")
        self.ledger.append(row)
        logger.warning("inference refused: task=%s endpoint=%s attempt=0 kind=boundary http_status=None",
                       plan.task.name, endpoint.name)
        raise InferenceBoundaryError(task=plan.task.name, endpoint=endpoint.name) from None

    # ------------------------------------------------------------------ one attempt
    def _row(self, plan: _Plan, endpoint: Endpoint, mode: str, attempt: int, *, escalated_from: str | None,
             ok: bool, error_kind: str | None, result: client.ChatResult | None = None,
             failure: client.TransportFailure | None = None) -> dict[str, Any]:
        tokens_in = result.tokens_in if result is not None else None
        tokens_out = result.tokens_out if result is not None else None
        if result is not None:
            latency, retries, status, fake = result.latency_ms, result.transport_retries, result.http_status, \
                result.fake_marker
        elif failure is not None:
            latency, retries, status, fake = failure.latency_ms, failure.transport_retries, failure.http_status, \
                failure.fake_marker
        else:
            latency, retries, status, fake = None, 0, None, False
        cost_usd, basis = cost(endpoint, tokens_in, tokens_out, latency)
        return {
            "ts": self._clock(), "run_id": self.run_id, "task": plan.task.name, "ref": plan.ref,
            "endpoint": endpoint.name, "provider": endpoint.provider, "model_requested": endpoint.model,
            "model_served": result.model_served if result is not None else None, "host": endpoint.host_label,
            "boundary": self.boundary, "endpoint_boundary": endpoint.boundary, "boundary_mode": mode,
            "data_label": self.data_label, "attempt": attempt, "escalated_from": escalated_from, "ok": ok,
            "error_kind": error_kind, "http_status": status,
            "finish_reason": result.finish_reason if result is not None else None, "transport_retries": retries,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "latency_ms": latency,
            "ttft_ms": result.ttft_ms if result is not None else None, "cost_usd": cost_usd, "cost_basis": basis,
            "fake_marker": bool(fake or endpoint.provider == "fake"),
        }

    def _response_format(self, plan: _Plan, endpoint: Endpoint) -> dict[str, Any] | None:
        if endpoint.response_format == "json_schema":
            schema = plan.schema.full if endpoint.transport_schema == "full" else plan.schema.reduced
            return {"type": "json_schema", "json_schema": {"name": plan.task.name, "schema": schema, "strict": True}}
        if endpoint.response_format == "json_object":
            return {"type": "json_object"}
        return None

    def _attempt(self, plan: _Plan, endpoint: Endpoint, mode: str, attempt: int, messages: list[dict[str, str]], *,
                 stream: bool, escalated_from: str | None = None) -> _Result:
        result = failure = None
        try:
            if endpoint.provider == "fake":
                result = self._fake.complete(plan.task, plan.payload, endpoint)
            else:
                result = client.chat(endpoint, messages, max_tokens=plan.task.max_tokens,
                                     response_format=self._response_format(plan, endpoint), stream=stream,
                                     environ=self._environ, sleep=self._sleep)
        except client.TransportFailure as exc:
            failure = exc

        output = None
        problems: list[tuple[str, str]] = []
        if failure is not None:
            kind = failure.kind
        else:
            kind = None
            try:
                obj = extract_object(result.content)
            except ReplyParseError:
                obj = None
            if obj is None:
                kind, problems = "json_invalid", [("$", "json")]
            else:
                problems = plan.schema.validate(obj)
                if problems:
                    kind = "schema_invalid"
                else:
                    output = obj
        row = self._row(plan, endpoint, mode, attempt, escalated_from=escalated_from, ok=output is not None,
                        error_kind=kind, result=result, failure=failure)
        self.ledger.append(row)
        if kind is not None:
            logger.warning("inference attempt failed: task=%s endpoint=%s attempt=%d kind=%s http_status=%s",
                           plan.task.name, endpoint.name, attempt, kind, row["http_status"])
        return _Result(row=row, output=output, problems=problems,
                       content=result.content if result is not None else None,
                       finish_reason=result.finish_reason if result is not None else None,
                       content_chunks=result.content_chunks if result is not None else 0)

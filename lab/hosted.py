"""The optional hosted provider: one OpenAI-compatible host, named by two repository secrets, for bigger models.

The founder adds the repository secrets ``MYCELIC_LAB_HOSTED_BASE_URL`` (the host's base URL, ``https://<host>/v1``
or whatever path the host serves its OpenAI-compatible API under) and ``MYCELIC_LAB_HOSTED_API_KEY``, and a ``hosted``
entry in ``lab/models.json`` naming the host's model id (``manifest.py``). A hosted model then runs in three roles:
an E1 endpoint, the E1 reference, or E2's central comparator (``e2.central``). It never runs in the canary scan, the
simulation, E3 or as ``e2.models``: those run each site's model inside the runner, and E3 measures the runner.

**Secrets.** The plan job sees only whether both secrets are set (``LAB_HAS_HOSTED``); without them, the plan skips
the hosted units and the units that depend on them (``plan.filter_hosted``). A shard job of a hosted shard gets both
values in its environment (the workflow passes them to that shard only), and :func:`load_config` reads them: each is
stripped of surrounding whitespace and given a state (``not_set``, ``blank``, ``not_printable`` for a key outside
printable ASCII, ``invalid`` for a base URL the routing parser refuses, that is not https, http other than to a
loopback address, or https to a private or link-local address, and ``present``). The first problem, key first, fails
every hosted unit of the shard with a fixed sentence and no network request; no message ever holds a value.

**Where the values go.** Only :data:`KEY_VAR` reaches a subprocess, only the harness of a unit that calls the host,
and only through :func:`unit_environ` (then ``units.subprocess_env``, which keeps the allowlist and the unit's own
``env``). The base URL lives in exactly one place outside this process: the scratch routing file
``OUT/work/<unit>/hosted-routing/<name>.json`` the harness reads, deleted when the unit ends and by the seal, never
uploaded. The routing file under ``OUT/routing/`` is :func:`redact`'s copy, with :data:`REDACTED_BASE_URL`. The
provenance (:attr:`Session.block`) records the scheme and the ``host:port``, never the path. The local model server
keeps its four-variable environment (``server.server_env``).

**Preflight.** Before the first unit of a key in a shard, :class:`Session` lists the host's models (``GET /models``:
its status, whether it answered a list, the list's length and whether the requested id is in it; never the ids;
not a model call) and sends the canary: E1's extraction task with the warm-up's typical payload, or E2's two central
tasks with the rehearsal's worst-case payloads (``warmup.e2_worst_payloads``). Each canary task is one
``Runtime.single`` call, ledgered to :data:`PREFLIGHT_LEDGER`, retried after :data:`PREFLIGHT_WAIT_S` seconds while it
meets a 429, a 5xx, a network failure or a timeout, at most :data:`PREFLIGHT_MAX_CALLS` calls per task and within
:data:`PREFLIGHT_WINDOW_S` seconds (less when the shard has less time). An answer with status 200 passes, unless it
stopped at the token limit (a reasoning model would exhaust E2's 64-token central budget): ``HOSTED_LENGTH``. Another
status is ``HOSTED_PREFLIGHT_FAILED`` (``HTTP <status>`` and the remedy), the retries running out is
``HOSTED_UNAVAILABLE``; a redirect is never followed, so it fails as ``HTTP 3xx``.

**Calls.** The plan gives each shard a share of each key's calls (``plan.hosted.<key>.shares``): the preflight's
:data:`PREFLIGHT_MAX_CALLS` per canary task plus :func:`unit_bound` for each of the shard's units of that key. Before
each hosted unit, :meth:`Session.calls_used` counts the ledger rows of the calls made so far (attempt 1 or more: the
preflight ledger's rows of the key and the collected hosted ledgers of the units it started; an unreadable ledger
counts as the whole share), and a unit whose shard has used its share is skipped (``HOSTED_MAX_CALLS``). A running
unit can exceed the share only by its own repairs and the client's transport retries. Neither ``GET /models`` nor a
transport retry (another HTTP try of the same call, after a 429 or a 5xx, within the entry's ``max_retries``) is a
ledger row, so neither is counted; the host bills them all, and prepaid credits are the hard limit.
"""
from __future__ import annotations

import copy
import ipaddress
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from mycelic.collective.edge.extract import TASK_NAME
from mycelic.collective.experiments.common import utc_clock
from mycelic.collective.experiments.e2_pushdown import CENTRAL_SCHEMA, CENTRAL_TASKS, central_task
from mycelic.collective.inference.client import is_private_host, list_models
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.inference.routing import ConfigError, parse_routing
from mycelic.collective.inference.runtime import Runtime
from mycelic.collective.inference.tasks import TaskSpec
from mycelic.collective.schemacheck import compile as compile_schema

from . import warmup
from .notes import (BUDGET_EXHAUSTED, HOSTED_BASE_URL_INVALID, HOSTED_KEY_NOT_TOKEN, HOSTED_LENGTH, HOSTED_MAX_CALLS,
                    HOSTED_PREFLIGHT_FAILED, HOSTED_SECRET_BLANK, HOSTED_SECRET_NOT_SET, HOSTED_UNAVAILABLE)

KEY_VAR = "MYCELIC_LAB_HOSTED_API_KEY"
BASE_URL_VAR = "MYCELIC_LAB_HOSTED_BASE_URL"
HAS_VAR = "LAB_HAS_HOSTED"
REDACTED_BASE_URL = "https://hosted.invalid/v1"
BOUNDARY = "external"
PREFLIGHT_MAX_CALLS = 6
PREFLIGHT_WINDOW_S = 300
PREFLIGHT_WAIT_S = 30
CONNECT_TIMEOUT_S = 10
PREFLIGHT_LEDGER = "hosted/preflight.ledger.jsonl"
HOSTED_LEDGERS = {"e1": "ledger.jsonl", "e2": "central.ledger.jsonl"}
RETRYABLE_KINDS = ("http_5xx", "network", "timeout")
STATES = ("not_set", "blank", "not_printable", "invalid", "present")


# --------------------------------------------------------------------------------------------------- pure parts

def role(unit: Mapping[str, Any], models: Mapping[str, Mapping[str, Any]]) -> tuple[str, str] | None:
    """``("endpoint", <key>)`` for an E1 unit of a hosted model, ``("central", <key>)`` for an E2 unit whose central
    comparator is a hosted model; None for every other unit."""
    if unit.get("experiment") == "e1":
        entry = models.get(unit.get("model")) if isinstance(unit.get("model"), str) else None
        if isinstance(entry, Mapping) and entry.get("kind") == "hosted":
            return "endpoint", unit["model"]
    if unit.get("experiment") == "e2":
        central = unit.get("params", {}).get("central", "self")
        if central != "self":
            return "central", central
    return None


def preflight_tasks(experiment: str) -> int:
    """The canary tasks a preflight sends: E1's extraction task, or E2's two central tasks."""
    return {"e1": 1, "e2": 2}[experiment]


def unit_bound(experiment: str, *, records: int | None = None, top_n: int | None = None,
               seeds: int | None = None) -> int:
    """The most model calls one unit makes on the host: one call and one repair per E1 record; for E2, both central
    tasks (``central_raw`` and ``central_allowed``) for at most ``top_n`` candidates of each seed, with one repair
    each."""
    if experiment == "e1":
        return records * 2
    return 2 * top_n * seeds * 2


def endpoint(entry: Mapping[str, Any], base_url: str, *, deadline_s: float | None = None) -> dict[str, Any]:
    """The routing endpoint of a hosted manifest entry; the only builder of one (preregistration, units, preflight)."""
    return {"provider": "openai_compat", "boundary": BOUNDARY, "base_url": base_url, "model": entry["model"],
            "response_format": entry["response_format"], "transport_schema": entry["transport_schema"],
            "api_key_env": KEY_VAR, "connect_timeout_s": CONNECT_TIMEOUT_S,
            "deadline_s": deadline_s or entry["deadline_s"], "max_retries": entry["max_retries"],
            **({"price": dict(entry["price"])} if entry["price"] else {})}


def redact(doc: Mapping[str, Any]) -> dict[str, Any]:
    """A deep copy of a routing document in which every hosted endpoint's base URL is :data:`REDACTED_BASE_URL`."""
    out = copy.deepcopy(dict(doc))
    endpoints = out.get("endpoints")
    for body in (endpoints.values() if isinstance(endpoints, dict) else []):
        if isinstance(body, dict) and body.get("api_key_env") == KEY_VAR:
            body["base_url"] = REDACTED_BASE_URL
    return out


def is_hosted_doc(doc: Mapping[str, Any]) -> bool:
    """Whether a routing document names a hosted endpoint (one whose key is :data:`KEY_VAR`)."""
    endpoints = doc.get("endpoints")
    return isinstance(endpoints, dict) and any(isinstance(b, dict) and b.get("api_key_env") == KEY_VAR
                                               for b in endpoints.values())


# --------------------------------------------------------------------------------------------------- the secrets

@dataclass(frozen=True)
class Config:
    """The two secrets as a shard job sees them. ``key`` and ``base_url`` are None whenever ``problem`` is set;
    ``scheme`` and ``host`` (``host:port``) whenever the base URL is acceptable."""

    key: str | None = field(repr=False)
    base_url: str | None = field(repr=False)
    scheme: str | None
    host: str | None
    secrets: dict[str, str]
    problem: str | None


def _state(raw: str | None) -> tuple[str, str | None]:
    if raw is None or raw == "":
        return "not_set", None
    value = raw.strip()
    return ("blank", None) if value == "" else ("present", value)


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base_url(value: str) -> tuple[str, str] | None:
    """(scheme, ``host:port``) of an acceptable base URL, else None (the parser's message is dropped)."""
    doc = {"schema_version": 1, "routes": {},
           "endpoints": {"hosted": {"provider": "openai_compat", "boundary": BOUNDARY, "base_url": value,
                                    "model": "hosted"}}}
    parsed = None
    try:
        parsed = parse_routing(doc, check_env=False).endpoints["hosted"]
    except ConfigError:
        pass
    if parsed is None:
        return None
    parts = urlsplit(parsed.base_url or "")
    host = parts.hostname or ""
    loopback = _loopback(host)
    if parts.scheme not in ("http", "https") or (parts.scheme == "http" and not loopback):
        return None
    if not loopback and is_private_host(host):
        return None
    return parts.scheme, parsed.host_label


def _problem(name: str, state: str) -> str | None:
    return {"not_set": HOSTED_SECRET_NOT_SET.format(name=name), "blank": HOSTED_SECRET_BLANK.format(name=name),
            "not_printable": HOSTED_KEY_NOT_TOKEN, "invalid": HOSTED_BASE_URL_INVALID}.get(state)


def load_config(environ: Mapping[str, str]) -> Config:
    """The two secrets of ``environ``, stripped and checked; see the module docstring."""
    key_state, key = _state(environ.get(KEY_VAR))
    if key is not None and not all(0x21 <= ord(c) <= 0x7e for c in key):
        key_state = "not_printable"
    url_state, url = _state(environ.get(BASE_URL_VAR))
    scheme = host = None
    if url is not None:
        parsed = _base_url(url)
        if parsed is None:
            url_state = "invalid"
        else:
            scheme, host = parsed
    problem = _problem(KEY_VAR, key_state) or _problem(BASE_URL_VAR, url_state)
    return Config(key=key if problem is None else None, base_url=url if problem is None else None, scheme=scheme,
                  host=host, secrets={KEY_VAR: key_state, BASE_URL_VAR: url_state}, problem=problem)


def unit_environ(environ: Mapping[str, str], config: Config) -> dict[str, str]:
    """``environ`` with :data:`KEY_VAR` set to the stripped key; ``units.subprocess_env`` then keeps only the
    allowlist and the unit's ``env``."""
    return {**environ, KEY_VAR: config.key or ""}


# --------------------------------------------------------------------------------------------------- the session

@dataclass(frozen=True)
class Handle:
    """What a unit that calls the host needs: its key and role, the manifest entry, where to send the calls and the
    environment its harness gets, and the calls its shard had made before it."""

    key: str
    role: str
    entry: Mapping[str, Any]
    base_url: str = field(repr=False)
    host: str
    scheme: str
    environ: Mapping[str, str] = field(repr=False)
    share: int
    calls_before: int


def _count_calls(path: Path, endpoint_name: str | None = None) -> int:
    if not path.exists():
        return 0
    return sum(1 for row in read_ledger(path)
               if row["attempt"] >= 1 and (endpoint_name is None or row["endpoint"] == endpoint_name))


def preflight_rows(out: Path, key: str) -> list[dict[str, Any]]:
    """The successful rows of ``key``'s preflight (run id ``preflight-<key>``): the E2 projection's central
    latencies."""
    path = Path(out) / PREFLIGHT_LEDGER
    failed = False
    try:
        rows = read_ledger(path) if path.exists() else []
    except (OSError, ValueError):
        failed = True
    if failed:
        return []
    return [r for r in rows if r["run_id"] == f"preflight-{key}" and r["ok"] is True]


def _retryable(row: Mapping[str, Any]) -> bool:
    return row["error_kind"] in RETRYABLE_KINDS or row["http_status"] == 429


def _outcome(row: Mapping[str, Any], task: str, calls: int) -> str | None:
    """None when the host answered the canary; otherwise the reason the preflight failed."""
    status = row["http_status"]
    if status == 200:
        return HOSTED_LENGTH.format(task=task) if row["finish_reason"] == "length" else None
    if status is None or _retryable(row):
        detail = f"HTTP {status}" if status is not None else str(row["error_kind"])
        return HOSTED_UNAVAILABLE.format(detail=detail, calls=calls)
    return HOSTED_PREFLIGHT_FAILED.format(status=status)


class Session:
    """The hosted keys of one shard: the secrets, the preflight per key, the call count and the provenance block.
    The shard creates it only when one of its units has a :func:`role`."""

    def __init__(self, plan: Mapping[str, Any], shard: Mapping[str, Any], out: Path, environ: Mapping[str, str],
                 prereg: Any, sleep: Callable[[float], Any] = time.sleep) -> None:
        self.plan, self.shard, self.out, self.prereg, self.sleep = plan, shard, Path(out), prereg, sleep
        self.config = load_config(environ)
        self._environ = environ
        units = {u["unit"]: u for u in plan["units"]}
        keys = sorted({r[1] for uid in shard["units"] for r in [role(units[uid], plan["models"])] if r is not None})
        self.block: dict[str, Any] = {"secrets": dict(self.config.secrets), "scheme": self.config.scheme,
                                      "host": self.config.host, "problem": self.config.problem, "keys": {}}
        for key in keys:
            entry = plan["models"][key]
            shares = plan.get("hosted", {}).get(key, {}).get("shares", {})
            share = shares.get(shard["shard"]) if isinstance(shares, dict) else None
            self.block["keys"][key] = {
                **{k: entry[k] for k in ("model", "response_format", "transport_schema", "deadline_s", "max_retries")},
                "priced": entry["price"] is not None,
                "share": share if isinstance(share, int) and not isinstance(share, bool) else 0,
                "calls_used": 0, "models_list": None, "preflight": None}
        self._outcomes: dict[str, tuple[str, str | None]] = {}
        self._handed: dict[str, list[Mapping[str, Any]]] = {}

    # ------------------------------------------------------------------ the gate
    def gate(self, unit: Mapping[str, Any], budget_s: float) -> Handle | tuple[str, str]:
        """A :class:`Handle` for the unit, or ``(status, reason)`` for a unit that must not run: the secrets' problem
        (failed, no network), the shard's share used up (skipped), too little time for a preflight (skipped) or a
        preflight that failed (failed)."""
        found = role(unit, self.plan["models"])
        if found is None:
            raise ValueError("the unit calls no hosted model") from None
        role_name, key = found
        if self.config.problem is not None:
            return "failed", self.config.problem
        record = self.block["keys"][key]
        if self._used(key) >= record["share"]:
            return "skipped", HOSTED_MAX_CALLS
        if key not in self._outcomes:
            if budget_s < 1:
                record["preflight"] = {"status": "skipped", "reason": BUDGET_EXHAUSTED, "calls": 0, "tasks": []}
                return "skipped", BUDGET_EXHAUSTED
            self._outcomes[key] = self._preflight(key, unit, budget_s)
        status, reason = self._outcomes[key]
        if status != "passed":
            return "failed", reason or ""
        before = self._used(key)
        if before >= record["share"]:
            return "skipped", HOSTED_MAX_CALLS
        self._handed.setdefault(key, []).append(unit)
        return Handle(key=key, role=role_name, entry=self.plan["models"][key], base_url=self.config.base_url or "",
                      host=self.config.host or "", scheme=self.config.scheme or "",
                      environ=unit_environ(self._environ, self.config), share=record["share"], calls_before=before)

    def after_unit(self, unit: Mapping[str, Any], record: dict[str, Any]) -> None:
        """Count the unit's calls into the provenance block and the record's ``hosted.calls_after``."""
        found = role(unit, self.plan["models"])
        if found is None:
            return
        used = self._used(found[1])
        if isinstance(record.get("hosted"), dict):
            record["hosted"]["calls_after"] = used
        self.block["keys"][found[1]]["calls_used"] = used

    def calls_used(self, key: str) -> int:
        """The calls made on the host for ``key`` in this shard so far: the preflight ledger's rows of the key and the
        collected hosted ledgers of the units handed a handle (attempt 1 or more); the share when a ledger is
        unreadable."""
        share = self.block["keys"][key]["share"]
        try:
            total = _count_calls(self.out / PREFLIGHT_LEDGER, key)
            for unit in self._handed.get(key, []):
                total += _count_calls(self.out / "runs" / unit["experiment"] / unit["run_id"]
                                      / HOSTED_LEDGERS[unit["experiment"]])
        except (OSError, ValueError):
            return share
        return total

    def preflight_rows(self, key: str) -> list[dict[str, Any]]:
        return preflight_rows(self.out, key)

    def _used(self, key: str) -> int:
        used = self.calls_used(key)
        self.block["keys"][key]["calls_used"] = used
        return used

    # ------------------------------------------------------------------ the preflight
    def _canary(self, unit: Mapping[str, Any]) -> list[tuple[TaskSpec, dict[str, Any], dict[str, Any]]]:
        if unit["experiment"] == "e1":
            task = next(t for t in warmup.warm_tasks([unit], self.plan) if t.task.name == TASK_NAME)
            return [(task.task, task.schema, task.payload)]
        raw, allowed = warmup.e2_worst_payloads(unit, self.prereg.manifest["e2"]["rehearsal"])
        return [(central_task(name), CENTRAL_SCHEMA, payload) for name, payload in zip(CENTRAL_TASKS, (raw, allowed))]

    def _preflight(self, key: str, unit: Mapping[str, Any], budget_s: float) -> tuple[str, str | None]:
        entry = self.plan["models"][key]
        environ = unit_environ(self._environ, self.config)
        end = time.monotonic() + min(PREFLIGHT_WINDOW_S, budget_s)
        record = self.block["keys"][key]
        listing = parse_routing({"schema_version": 1, "routes": {},
                                 "endpoints": {key: endpoint(entry, self.config.base_url or "")}}, check_env=False)
        listed = list_models(listing.endpoints[key], environ=environ)
        record["models_list"] = {"http_status": listed["http_status"], "ok": listed["ok"], "listed": len(listed["ids"]),
                                 "model_listed": entry["model"] in listed["ids"] if listed["ok"] else None}
        result: dict[str, Any] = {"status": "passed", "reason": None, "calls": 0, "tasks": []}
        record["preflight"] = result
        for task, schema, payload in self._canary(unit):
            row, calls = self._call(key, entry, environ, task, schema, payload, end)
            result["calls"] += calls
            result["tasks"].append({"task": task.name, "calls": calls,
                                    **{k: row[k] for k in ("http_status", "error_kind", "finish_reason", "latency_ms",
                                                           "tokens_in", "tokens_out")}})
            reason = _outcome(row, task.name, calls)
            if reason is not None:
                result.update(status="failed", reason=reason)
                return "failed", reason
        return "passed", None

    def _call(self, key: str, entry: Mapping[str, Any], environ: Mapping[str, str], task: TaskSpec,
              schema: dict[str, Any], payload: dict[str, Any], end: float) -> tuple[dict[str, Any], int]:
        """One canary task: ``Runtime.single`` until the host answers or the retries run out; (last row, calls)."""
        compiled = compile_schema(schema)
        calls = 0
        while True:
            deadline = min(entry["deadline_s"], max(1.0, end - time.monotonic()))
            doc = {"schema_version": 1, "endpoints": {key: endpoint(entry, self.config.base_url or "",
                                                                     deadline_s=deadline)},
                   "routes": {task.name: {"endpoint": key}}}
            runtime = Runtime(parse_routing(doc, check_env=False), boundary="central",
                              ledger_path=self.out / PREFLIGHT_LEDGER, run_id=f"preflight-{key}", clock=utc_clock,
                              data_label="synthetic", allow_external_raw="synthetic", environ=environ,
                              sleep=self.sleep)
            try:
                row = runtime.single(task, payload, compiled, ref=f"preflight:{task.name}").row
            finally:
                runtime.close()
            calls += 1
            if not _retryable(row) or calls >= PREFLIGHT_MAX_CALLS:
                return row, calls
            left = end - time.monotonic()
            if left < 1:
                return row, calls
            self.sleep(min(PREFLIGHT_WAIT_S, left))
            if end - time.monotonic() < 1:
                return row, calls

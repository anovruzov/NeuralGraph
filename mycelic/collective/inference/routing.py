"""Routing configuration: which endpoint serves which task, and in which data boundary each endpoint sits.

No default routing ships. There is no default endpoint, URL, model tag or price anywhere in code: a site writes
its own routing file (``docs/collective/examples/routing.example.json`` is a template, not a default), and every
value in it is checked here before any request is made.

File format (unknown keys are rejected at every level)::

    {"schema_version": 1,
     "endpoints": {"<name>": {"provider": "openai_compat" | "fake", "boundary": "site:<id>" | "central" | "external"
                                 | "any-simulated", "base_url": "http(s)://host:port/v1", "model": "<tag>", ...}},
     "routes": {"<task>": {"endpoint": "<name>", "escalate_to": "<name>"}}}

``base_url`` is stored verbatim (minus one trailing ``/``); ``/v1`` is never added or removed, because servers
disagree about it. Two optional ``openai_compat`` keys control a model's thinking, which servers turn on by default
for hybrid-thinking models and which spends the small token budgets of the site tasks before any answer:
``reasoning_effort`` (one of :data:`REASONING_EFFORTS`, sent as is: ``"none"`` turns thinking off on Ollama and
recent vLLM) and ``chat_template_kwargs`` (a flat object of at most 8 names to booleans, ints or short strings, sent
as is: ``{"enable_thinking": false}`` for vLLM and llama-server with a thinking chat template). Neither is sent
unless the file names it; the server's default then applies, and the routing file's sha256 records which.
Escalation is single-hop: the escalation endpoint's own route is never followed. Every error is a
:class:`ConfigError` naming the JSON path of the offending value.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load

SITE_BOUNDARY_RE = re.compile(r"site:[a-z0-9][a-z0-9_.-]{0,63}", re.ASCII)
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}", re.ASCII)
TASK_RE = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
ENV_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,63}", re.ASCII)
MODEL_RE = re.compile(r"[\x21-\x7e]{1,256}", re.ASCII)

PROVIDERS = ("openai_compat", "fake")
SHARED_BOUNDARIES = ("central", "external", "any-simulated")
RESPONSE_FORMATS = ("json_schema", "json_object", "none")
TRANSPORT_SCHEMAS = ("full", "reduced")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")
TEMPLATE_KWARG_RE = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
MAX_TEMPLATE_KWARGS = 8

_TOP_KEYS = {"schema_version", "endpoints", "routes"}
_ENDPOINT_KEYS = {"provider", "boundary", "base_url", "model", "response_format", "transport_schema", "api_key_env",
                  "connect_timeout_s", "deadline_s", "max_retries", "max_response_bytes", "ca_file", "price", "seed",
                  "reasoning_effort", "chat_template_kwargs"}
_ROUTE_KEYS = {"endpoint", "escalate_to"}
_PRICE_KEYS = {"per_mtok_in", "per_mtok_out", "usd_per_hour"}


class ConfigError(ValueError):
    def __init__(self, path: str, problem: str) -> None:
        super().__init__(f"{path}: {problem}")
        self.path = path
        self.problem = problem


def is_site_boundary(value: Any) -> bool:
    return isinstance(value, str) and SITE_BOUNDARY_RE.fullmatch(value) is not None


def endpoint_boundary_ok(value: Any) -> bool:
    return value in SHARED_BOUNDARIES or is_site_boundary(value)


def runtime_boundary_ok(value: Any) -> bool:
    return value == "central" or is_site_boundary(value)


@dataclass(frozen=True)
class Price:
    per_mtok_in: float | None = None
    per_mtok_out: float | None = None
    usd_per_hour: float | None = None


@dataclass(frozen=True)
class Endpoint:
    name: str
    provider: str
    boundary: str
    base_url: str | None
    model: str
    response_format: str = "json_schema"
    transport_schema: str = "full"
    api_key_env: str | None = None
    connect_timeout_s: float = 5.0
    deadline_s: float = 120.0
    max_retries: int = 2
    max_response_bytes: int = 1048576
    ca_file: str | None = None
    price: Price | None = None
    seed: int | None = None
    reasoning_effort: str | None = None
    chat_template_kwargs: tuple[tuple[str, Any], ...] | None = None

    @property
    def host_label(self) -> str:
        """``host:port`` with the port made explicit, or ``in-process`` for the fake provider."""
        if self.provider == "fake" or not self.base_url:
            return "in-process"
        parts = urlsplit(self.base_url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return f"{host}:{port}"


@dataclass(frozen=True)
class Route:
    task: str
    endpoint: str
    escalate_to: str | None = None


@dataclass(frozen=True)
class RoutingConfig:
    endpoints: Mapping[str, Endpoint]
    routes: Mapping[str, Route]
    sha256: str

    def routed_endpoint_names(self) -> set[str]:
        names = set()
        for route in self.routes.values():
            names.add(route.endpoint)
            if route.escalate_to:
                names.add(route.escalate_to)
        return names


def _is_number(v: Any) -> bool:
    return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and math.isfinite(v))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _unknown_keys(obj: dict[str, Any], allowed: set[str], path: str) -> None:
    for key in sorted(obj, key=str):
        if key not in allowed:
            raise ConfigError(f"{path}.{key}", "unknown key") from None


def _check_base_url(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(path, "must be a non-empty string") from None
    if any(ord(c) <= 0x20 or ord(c) >= 0x7f for c in value):
        raise ConfigError(path, "must be ASCII without spaces or control characters") from None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ConfigError(path, "scheme must be http or https") from None
    if "@" in parts.netloc:
        raise ConfigError(path, "must not carry credentials (put the key in an environment variable)") from None
    if "?" in value or "#" in value:
        raise ConfigError(path, "must not have a query or fragment") from None
    port_ok = True
    try:
        parts.port
    except ValueError:
        port_ok = False
    if not parts.hostname or not port_ok:
        raise ConfigError(path, "needs a host and a valid port") from None
    return value[:-1] if value.endswith("/") else value


def _check_price(value: Any, path: str) -> Price:
    if not isinstance(value, dict):
        raise ConfigError(path, "must be an object") from None
    _unknown_keys(value, _PRICE_KEYS, path)
    for key in sorted(value):
        v = value[key]
        if not _is_number(v) or v < 0:
            raise ConfigError(f"{path}.{key}", "must be a finite number >= 0") from None
    per_token = "per_mtok_in" in value and "per_mtok_out" in value
    per_hour = set(value) == {"usd_per_hour"}
    if not (per_token and set(value) == {"per_mtok_in", "per_mtok_out"}) and not per_hour:
        raise ConfigError(path, "give both per_mtok_in and per_mtok_out, or usd_per_hour alone") from None
    return Price(per_mtok_in=value.get("per_mtok_in"), per_mtok_out=value.get("per_mtok_out"),
                 usd_per_hour=value.get("usd_per_hour"))


def _check_template_kwargs(value: Any, path: str) -> tuple[tuple[str, Any], ...]:
    if not isinstance(value, dict) or not 1 <= len(value) <= MAX_TEMPLATE_KWARGS:
        raise ConfigError(path, f"must be an object of 1 to {MAX_TEMPLATE_KWARGS} keys") from None
    for key in sorted(value, key=str):
        v = value[key]
        if not isinstance(key, str) or TEMPLATE_KWARG_RE.fullmatch(key) is None:
            raise ConfigError(path, "keys must match [a-z][a-z0-9_]{0,63}") from None
        if not (isinstance(v, bool) or _is_int(v) or (isinstance(v, str) and MODEL_RE.fullmatch(v) is not None
                                                       and len(v) <= 64)):
            raise ConfigError(f"{path}.{key}", "must be a boolean, an int or a printable ASCII string of at most 64 "
                                               "characters") from None
    return tuple(sorted(value.items()))


def _parse_endpoint(name: str, raw: Any, *, allow_fake: bool) -> Endpoint:
    path = f"$.endpoints.{name}"
    if not isinstance(raw, dict):
        raise ConfigError(path, "must be an object") from None
    _unknown_keys(raw, _ENDPOINT_KEYS, path)
    provider = raw.get("provider")
    if provider not in PROVIDERS:
        raise ConfigError(f"{path}.provider", f"must be one of {', '.join(PROVIDERS)}") from None
    if provider == "fake" and not allow_fake:
        raise ConfigError(f"{path}.provider", "the fake provider is only allowed in tests and rehearsals") from None
    boundary = raw.get("boundary")
    if not endpoint_boundary_ok(boundary):
        raise ConfigError(f"{path}.boundary", "must be site:<id>, central, external or any-simulated") from None

    base_url = None
    if provider == "openai_compat":
        if "base_url" not in raw:
            raise ConfigError(f"{path}.base_url", "required for openai_compat") from None
        base_url = _check_base_url(raw["base_url"], f"{path}.base_url")
        model = raw.get("model")
        if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
            raise ConfigError(f"{path}.model", "required: a non-empty printable ASCII tag") from None
    else:
        if "base_url" in raw:
            raise ConfigError(f"{path}.base_url", "not allowed for the fake provider") from None
        model = raw.get("model", "fake")
        if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
            raise ConfigError(f"{path}.model", "must be a non-empty printable ASCII tag") from None

    kwargs: dict[str, Any] = {}
    if "response_format" in raw:
        if raw["response_format"] not in RESPONSE_FORMATS:
            raise ConfigError(f"{path}.response_format", f"must be one of {', '.join(RESPONSE_FORMATS)}") from None
        kwargs["response_format"] = raw["response_format"]
    if "transport_schema" in raw:
        if raw["transport_schema"] not in TRANSPORT_SCHEMAS:
            raise ConfigError(f"{path}.transport_schema", "must be full or reduced") from None
        kwargs["transport_schema"] = raw["transport_schema"]
    if "api_key_env" in raw:
        if not isinstance(raw["api_key_env"], str) or ENV_RE.fullmatch(raw["api_key_env"]) is None:
            raise ConfigError(f"{path}.api_key_env", "must be an environment variable name") from None
        kwargs["api_key_env"] = raw["api_key_env"]
    for key, low, high in (("connect_timeout_s", 0, 60), ("deadline_s", 0, 3600)):
        if key in raw:
            v = raw[key]
            if not _is_number(v) or not low < v <= high:
                raise ConfigError(f"{path}.{key}", f"must be a number in ({low}, {high}]") from None
            kwargs[key] = float(v)
    for key, low, high in (("max_retries", 0, 5), ("max_response_bytes", 1024, 67108864)):
        if key in raw:
            v = raw[key]
            if not _is_int(v) or not low <= v <= high:
                raise ConfigError(f"{path}.{key}", f"must be an int in [{low}, {high}]") from None
            kwargs[key] = v
    if "ca_file" in raw:
        ca = raw["ca_file"]
        if base_url is None or not base_url.startswith("https://"):
            raise ConfigError(f"{path}.ca_file", "requires an https base_url") from None
        if not isinstance(ca, str) or not ca or not Path(ca).is_file():
            raise ConfigError(f"{path}.ca_file", "file not found") from None
        kwargs["ca_file"] = ca
    if "price" in raw:
        kwargs["price"] = _check_price(raw["price"], f"{path}.price")
    if "seed" in raw:
        if not _is_int(raw["seed"]) or raw["seed"] < 0:
            raise ConfigError(f"{path}.seed", "must be an int >= 0") from None
        kwargs["seed"] = raw["seed"]
    for key in ("reasoning_effort", "chat_template_kwargs"):
        if key in raw and provider != "openai_compat":
            raise ConfigError(f"{path}.{key}", "only for openai_compat") from None
    if "reasoning_effort" in raw:
        if raw["reasoning_effort"] not in REASONING_EFFORTS:
            raise ConfigError(f"{path}.reasoning_effort", f"must be one of {', '.join(REASONING_EFFORTS)}") from None
        kwargs["reasoning_effort"] = raw["reasoning_effort"]
    if "chat_template_kwargs" in raw:
        kwargs["chat_template_kwargs"] = _check_template_kwargs(raw["chat_template_kwargs"],
                                                                f"{path}.chat_template_kwargs")
    return Endpoint(name=name, provider=provider, boundary=boundary, base_url=base_url, model=model, **kwargs)


def key_problem(endpoint: Endpoint, environ: Mapping[str, str]) -> ConfigError | None:
    """The error for an endpoint whose api key variable is unset, empty or not a printable token; else None."""
    if not endpoint.api_key_env:
        return None
    value = environ.get(endpoint.api_key_env)
    path = f"$.endpoints.{endpoint.name}.api_key_env"
    if not value:
        return ConfigError(path, f"environment variable {endpoint.api_key_env} is unset or empty")
    if not all(0x21 <= ord(c) <= 0x7e for c in value):
        return ConfigError(path, f"environment variable {endpoint.api_key_env} is not a printable ASCII token")
    return None


def parse_routing(obj: Any, *, tasks: Iterable[str] = (), allow_fake: bool = False, check_env: bool = True,
                  environ: Mapping[str, str] | None = None, sha256: str | None = None) -> RoutingConfig:
    if not isinstance(obj, dict):
        raise ConfigError("$", "must be an object") from None
    _unknown_keys(obj, _TOP_KEYS, "$")
    version = obj.get("schema_version")
    if not _is_int(version) or version != 1:
        raise ConfigError("$.schema_version", "must be 1") from None
    raw_endpoints = obj.get("endpoints")
    if not isinstance(raw_endpoints, dict):
        raise ConfigError("$.endpoints", "must be an object") from None
    raw_routes = obj.get("routes")
    if not isinstance(raw_routes, dict):
        raise ConfigError("$.routes", "must be an object") from None

    endpoints: dict[str, Endpoint] = {}
    for name in sorted(raw_endpoints, key=str):
        if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
            raise ConfigError("$.endpoints", "endpoint names must match [a-z0-9][a-z0-9_.-]{0,63}") from None
        endpoints[name] = _parse_endpoint(name, raw_endpoints[name], allow_fake=allow_fake)

    routes: dict[str, Route] = {}
    for task in sorted(raw_routes, key=str):
        if not isinstance(task, str) or TASK_RE.fullmatch(task) is None:
            raise ConfigError("$.routes", "task names must match [a-z][a-z0-9_]{0,63}") from None
        path = f"$.routes.{task}"
        raw = raw_routes[task]
        if not isinstance(raw, dict):
            raise ConfigError(path, "must be an object") from None
        _unknown_keys(raw, _ROUTE_KEYS, path)
        target = raw.get("endpoint")
        if not isinstance(target, str) or target not in endpoints:
            raise ConfigError(f"{path}.endpoint", "must name a configured endpoint") from None
        escalate = raw.get("escalate_to")
        if escalate is not None:
            if not isinstance(escalate, str) or escalate not in endpoints:
                raise ConfigError(f"{path}.escalate_to", "must name a configured endpoint") from None
            if escalate == target:
                raise ConfigError(f"{path}.escalate_to", "must differ from the route's endpoint") from None
        routes[task] = Route(task=task, endpoint=target, escalate_to=escalate)

    for task in tasks:
        if task not in routes:
            raise ConfigError(f"$.routes.{task}", "no route") from None

    config = RoutingConfig(endpoints=MappingProxyType(endpoints), routes=MappingProxyType(routes),
                           sha256=sha256 if sha256 is not None else sha256_hex(canonical_bytes(obj)))
    if check_env:
        env = os.environ if environ is None else environ
        for name in sorted(config.routed_endpoint_names()):
            problem = key_problem(endpoints[name], env)
            if problem is not None:
                raise problem from None
    return config


def load_routing(path: str | Path, *, tasks: Iterable[str] = (), allow_fake: bool = False, check_env: bool = True,
                 environ: Mapping[str, str] | None = None) -> RoutingConfig:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        problem = f"cannot read ({exc.__class__.__name__})"
        raise ConfigError(str(path), problem) from None
    try:
        obj = strict_load(data)
    except StrictJsonError as err:
        raise ConfigError(err.path, err.reason) from None
    return parse_routing(obj, tasks=tasks, allow_fake=allow_fake, check_env=check_env, environ=environ,
                         sha256=sha256_hex(data))


def missing_env(config: RoutingConfig, names: Iterable[str], environ: Mapping[str, str] | None = None) -> list[str]:
    """Unset or empty api-key variables of the named endpoints, sorted; for ``--dry-run``."""
    env = os.environ if environ is None else environ
    out = set()
    for name in names:
        endpoint = config.endpoints.get(name)
        if endpoint is not None and endpoint.api_key_env and not env.get(endpoint.api_key_env):
            out.add(endpoint.api_key_env)
    return sorted(out)

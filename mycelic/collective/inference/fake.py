"""A deterministic in-process model for CI: per-task handlers, scripted failures, no sockets.

A handler takes the task payload (a deep copy) and returns the reply object; the provider serialises it as
canonical JSON, so the same (task, payload) gives byte-identical output on every machine and across runtimes.
``fail_next`` scripts the next replies for a task: ``json_invalid`` returns ``"not json"``, ``schema_invalid``
returns ``{"__fake_invalid__": true}``, and a transport kind raises :class:`~.client.TransportFailure`, so tests can
drive the runtime's repair and failure paths without a server.

Token counts are a fixed proxy (``ceil(bytes / 4)``); latency is 0. Every result is marked fake, and the ledger
prices it as ``fake``. There is no global registry: each gate builds its own instance.
"""
from __future__ import annotations

import copy
import math
import threading
from collections import deque
from typing import Any, Callable

from ..jsonio import canonical_bytes, canonical_dumps
from .client import ChatResult, TransportFailure
from .errors import InferenceError

SCRIPTABLE = ("json_invalid", "schema_invalid", "http_4xx", "http_5xx", "timeout", "network", "too_large")
_STATUS = {"http_4xx": 400, "http_5xx": 500}


class FakeProvider:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._scripted: dict[str, deque[str]] = {}
        self._lock = threading.Lock()

    def register(self, task_name: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        with self._lock:
            self._handlers[task_name] = handler

    def fail_next(self, task_name: str, kinds: list[str] | tuple[str, ...]) -> None:
        for kind in kinds:
            if kind not in SCRIPTABLE:
                raise ValueError(f"cannot script failure kind {kind!r}") from None
        with self._lock:
            self._scripted.setdefault(task_name, deque()).extend(kinds)

    def complete(self, task: Any, payload: dict[str, Any], endpoint: Any) -> ChatResult:
        with self._lock:
            handler = self._handlers.get(task.name)
            scripted = self._scripted.get(task.name)
            kind = scripted.popleft() if handler is not None and scripted else None
        if handler is None:
            raise InferenceError(task=task.name, endpoint=endpoint.name, kind="no_handler") from None
        if kind is not None and kind not in ("json_invalid", "schema_invalid"):
            raise TransportFailure(kind=kind, http_status=_STATUS.get(kind), transport_retries=0, latency_ms=0.0,
                                   fake_marker=True) from None
        if kind == "json_invalid":
            content = "not json"
        elif kind == "schema_invalid":
            content = canonical_dumps({"__fake_invalid__": True})
        else:
            content = canonical_dumps(handler(copy.deepcopy(payload)))
        return ChatResult(content=content, finish_reason="stop", model_served=endpoint.model,
                          tokens_in=math.ceil(len(canonical_bytes(payload)) / 4),
                          tokens_out=math.ceil(len(content.encode("utf-8")) / 4), http_status=None,
                          transport_retries=0, latency_ms=0.0, ttft_ms=None, fake_marker=True, content_chunks=0)

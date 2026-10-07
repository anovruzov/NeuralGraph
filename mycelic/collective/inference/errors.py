"""The inference error type, shared by client, fake and runtime without an import cycle.

An :class:`InferenceError` holds exactly four values, all of them configuration or classification, never text:
the task name, the endpoint name, a fixed ``kind`` and the HTTP status. ``args`` is empty, so nothing else can ride
along into ``str``, ``repr``, a traceback or a log line. Raise sites use ``from None`` so the exception that
caused the failure (whose message may hold a reason phrase, a JSON document or socket text) is not printed as
context either.
"""
from __future__ import annotations

KINDS = ("json_invalid", "schema_invalid", "http_4xx", "http_5xx", "timeout", "network", "boundary", "too_large",
         "no_handler")


class InferenceError(Exception):
    def __init__(self, *, task: str, endpoint: str | None, kind: str, http_status: int | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown inference error kind {kind!r}") from None
        super().__init__()
        self.task = task
        self.endpoint = endpoint
        self.kind = kind
        self.http_status = http_status

    def __str__(self) -> str:
        return (f"inference failed: task={self.task} endpoint={self.endpoint} kind={self.kind} "
                f"http_status={self.http_status}")

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(task={self.task!r}, endpoint={self.endpoint!r}, kind={self.kind!r}, "
                f"http_status={self.http_status!r})")


class InferenceBoundaryError(InferenceError):
    """The runtime refused an endpoint outside its data boundary; no request was sent."""

    def __init__(self, *, task: str, endpoint: str | None) -> None:
        super().__init__(task=task, endpoint=endpoint, kind="boundary", http_status=None)

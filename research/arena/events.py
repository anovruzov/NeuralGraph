"""Typed events and an append-only store with replay.

An :class:`Event` is one observation: a tag from :data:`TAGS`, the source that produced it, the agent it is
about, a message, a ``ref`` naming the artifact it was read from (path, line, sha) so the UI can link back, and
``data`` with the typed payload.  ``ts`` is when the console observed it; ``at`` is the artifact's own timestamp
when it carries one, else ``None`` -- the two are never conflated.

:class:`EventStore` assigns monotonic ids, appends every event to a jsonl file before anyone sees it, keeps a
bounded ring in memory, and answers ``since(after)`` from the ring or, for ids older than the ring, from disk.
That is what makes ``Last-Event-ID`` reconnection and historical replay the same operation.
"""
from __future__ import annotations

import collections
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TAGS = frozenset((
    "SPAWN", "ROUTE", "ASK", "REPLY", "VERIFY", "EDGE", "PATCH", "TEST", "FAIL", "COMMIT", "RECOVER",   # the spec's
    "COMPLETE", "ARTIFACT", "SYNC", "PUBLISH", "PHASE", "LOG", "CONTROL", "SOURCE",                   # the runtime's
))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    id: int
    ts: str
    at: str | None
    tag: str
    source: str
    agent: str | None
    msg: str
    ref: dict[str, Any]
    data: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "Event":
        d = json.loads(line)
        return cls(id=int(d["id"]), ts=d["ts"], at=d.get("at"), tag=d["tag"], source=d["source"],
                   agent=d.get("agent"), msg=d.get("msg", ""), ref=dict(d.get("ref") or {}),
                   data=dict(d.get("data") or {}))


class EventStore:
    """Append-only, monotonic, replayable.  Thread-safe."""

    def __init__(self, path: str | Path, *, keep: int = 20_000) -> None:
        self.path = Path(path)
        self.keep = keep
        self._ring: collections.deque[Event] = collections.deque(maxlen=keep)
        self._next = 1
        self._cv = threading.Condition()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        ev = Event.from_json(line)
                        self._ring.append(ev)
                        self._next = ev.id + 1
        self._fh = open(self.path, "a", encoding="utf-8")

    def append(self, *, tag: str, source: str, msg: str, agent: str | None = None, at: str | None = None,
               ref: dict[str, Any] | None = None, data: dict[str, Any] | None = None) -> Event:
        if tag not in TAGS:
            raise ValueError(f"unknown tag {tag!r}")
        with self._cv:
            ev = Event(id=self._next, ts=now_iso(), at=at, tag=tag, source=source, agent=agent, msg=msg,
                       ref=dict(ref or {}), data=dict(data or {}))
            self._next += 1
            self._fh.write(ev.to_json() + "\n")
            self._fh.flush()
            self._ring.append(ev)
            self._cv.notify_all()
        return ev

    @property
    def latest_id(self) -> int:
        with self._cv:
            return self._next - 1

    def since(self, after: int) -> list[Event]:
        """Every event with ``id > after``, in order; from the ring when it covers, else from disk."""
        with self._cv:
            if not self._ring:
                return []
            if self._ring[0].id <= after + 1:
                return [e for e in self._ring if e.id > after]
        out: list[Event] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ev = Event.from_json(line)
                    if ev.id > after:
                        out.append(ev)
        return out

    def wait(self, after: int, timeout: float) -> list[Event]:
        """Block until an event newer than ``after`` exists or ``timeout`` passes; return what is new."""
        with self._cv:
            self._cv.wait_for(lambda: self._next - 1 > after, timeout)
        return self.since(after)

    def tail(self, n: int) -> list[Event]:
        with self._cv:
            return list(self._ring)[-n:]

    def counts(self) -> dict[str, int]:
        with self._cv:
            c: dict[str, int] = {}
            for e in self._ring:
                c[e.tag] = c.get(e.tag, 0) + 1
            return c

    def close(self) -> None:
        with self._cv:
            self._fh.close()

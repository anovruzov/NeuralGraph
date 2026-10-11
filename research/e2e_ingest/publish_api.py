"""Publishing to the keyed fabric through the API (``docs/collective/E2E-INGEST.md`` sections 3.6 and 7; S3).

The routing spike publishes into an in-process fabric (``research/routing_spike/publish.py``, ``nats_url=None``).
This module sends the same two event types, with the same payloads and the same embedded memory, through
``mycelic.sdk.MycelicClient.publish_events`` to a live service: one ``POST /events`` carrying one event, so the
number of publishes is a count of API calls and every event has its own ``created_at`` and ``applied_at``.

* ``collective.verdict``: payload as ``publish.py:80-87`` (site, question id, source, verdict id, verdict, reason,
  the three buckets, newest week, window, the sha256 of the verdict bytes as HQ received them; an HQ ``unknown``
  record has no sha256); idempotency key ``{question_id}:{site}:{seq}``, the ``pd_verdicts`` primary key.
* ``collective.conclusion``: payload as ``publish.py:96-98`` and the embedded memory as ``publish.py:91-102``
  (``entity``, ``slot`` the predicate, ``value`` the gate status, ``topic`` ``collective/<pack id>``, ``kind``
  ``risk``, ``visibility`` ``team``, ``source_event_ids`` the verdict event ids the API returned, the metadata);
  idempotency key ``{conclusion_id}:{version}``, the ``pd_conclusions`` primary key.

The keys make every publish idempotent at the service: ``event_id = evt_<hash(agent, "event", key)>``
(``service.py:1220``), the embedded memory's id ``mem_<hash(agent, "event-memory", key)>`` (``service.py:1232``), and
a second POST of the same key returns ``created: false`` and creates no memory (``service.py:1243``, ``:1259-1261``).
A bridge run can therefore be repeated, resumed after a crash or re-sent after an ambiguous failure without a
duplicate; the watermark here is bookkeeping, not what correctness rests on.

Retries: the SDK retries connection errors and 429, 502, 503 and 504 with exponential backoff (``sdk/__init__.py``);
:class:`Publisher` adds a bounded outer loop over the same statuses (a service that is restarting answers nothing for
a few seconds) and raises :class:`PublishError` on any other status at once. A result whose first attempt failed and
whose later attempt returned ``created: false`` is marked ``ambiguous``: the event may be ours and new, or the
re-send of one the service had already taken.

This module imports ``mycelic.sdk`` (standard library only) and nothing of the service, the retrieval package or
numpy; ``tests/e2e_ingest`` checks that in a fresh interpreter. It declares the event types and ``conclusion_text``
itself; a test asserts equality with the spike's (``publish.py:30-31``, ``:43-47``). No record, narrative or graph
node is ever an argument here: a verdict body is the site's egress artifact, a conclusion text is HQ's own sentence.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.sdk import MycelicClient, MycelicError

VERDICT_EVENT = "collective.verdict"
CONCLUSION_EVENT = "collective.conclusion"
ORG_ID = "emergence"
AGENT_ID = "hq"
ROUTE_LABEL = "R"
MEMORY_KIND = "risk"
MEMORY_VISIBILITY = "team"
WATERMARK_SCHEMA = 1
#: the SDK's own code for a connection failure is 0; the rest are the statuses the SDK itself retries
RETRYABLE_STATUSES = (0, 429, 502, 503, 504)


class PublishError(RuntimeError):
    """A publish the service refused (any status outside :data:`RETRYABLE_STATUSES`) or that failed every retry."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json_atomic(path: str | Path, obj: Any) -> None:
    """Canonical JSON plus a newline through a temporary file and ``os.replace`` (the shape of
    ``experiments/common.py:57``, re-implemented so this module stays on the standard library)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------------------- the schema

def conclusion_text(pack: Any, question: Mapping[str, Any], status: str) -> str:
    """HQ's own sentence from the question's parameters (``publish.py:43-47``, verbatim)."""
    params, window = question["params"], question["window"]
    return (f"Pushdown conclusion for {pack.entity_types[params['entity_type']].label} {params['entity_id']}, "
            f"{pack.predicates[params['predicate']].label}, weeks {window['start_week']} to {window['end_week']}: "
            f"{status}.")


def verdict_key(question_id: str, site: str, seq: int) -> str:
    """``{question_id}:{site}:{seq}``: the ``pd_verdicts`` primary key (``store.py:127-130``)."""
    return f"{question_id}:{site}:{int(seq)}"


def conclusion_key(conclusion_id: str, version: int) -> str:
    """``{conclusion_id}:{version}``: the ``pd_conclusions`` primary key (``store.py:134-136``)."""
    return f"{conclusion_id}:{int(version)}"


def verdict_payload(question: Mapping[str, Any], *, site: str, source: str, body: Mapping[str, Any],
                    sha256: str | None) -> dict[str, Any]:
    """The verdict event's payload, key for key as ``publish.py:80-87``."""
    return {"site": site, "question_id": question["question_id"], "source": source,
            "verdict_id": body.get("verdict_id"), "verdict": body["verdict"], "reason": body.get("reason"),
            "support_bucket": body.get("support_bucket"), "roots_bucket": body.get("roots_bucket"),
            "reporters_bucket": body.get("reporters_bucket"), "newest_week": body.get("newest_week"),
            "window": dict(question["window"]), "sha256": sha256}


def verdict_event(question: Mapping[str, Any], *, site: str, seq: int, source: str, body: Mapping[str, Any],
                  sha256: str | None) -> dict[str, Any]:
    """One ``collective.verdict`` event for one ``pd_verdicts`` row."""
    return {"type": VERDICT_EVENT, "idempotency_key": verdict_key(question["question_id"], site, seq),
            "payload": verdict_payload(question, site=site, source=source, body=body, sha256=sha256)}


def conclusion_memory(pack: Any, question: Mapping[str, Any], *, status: str, entity_type: str, candidate_key: str,
                      conclusion_id: str, snapshot_week: str, as_of: str, route: Sequence[str],
                      verdict_event_ids: Sequence[str], route_label: str = ROUTE_LABEL) -> dict[str, Any]:
    """The embedded memory of a conclusion event, key for key as ``publish.py:91-102``."""
    params = question["params"]
    return {"text": conclusion_text(pack, question, status), "entity": params["entity_id"],
            "slot": params["predicate"], "value": status, "topic": f"collective/{pack.id}",
            "kind": MEMORY_KIND, "visibility": MEMORY_VISIBILITY, "source_event_ids": list(verdict_event_ids),
            "metadata": {"entity_type": entity_type, "entity_id": params["entity_id"],
                         "predicate": params["predicate"], "candidate_key": candidate_key,
                         "question_id": question["question_id"], "conclusion_id": conclusion_id, "as_of": as_of,
                         "snapshot_week": snapshot_week, "route_label": route_label, "routed_sites": list(route)}}


def conclusion_event(question: Mapping[str, Any], *, conclusion_id: str, version: int, status: str,
                     route: Sequence[str], verdict_event_ids: Sequence[str], memory: Mapping[str, Any],
                     route_label: str = ROUTE_LABEL) -> dict[str, Any]:
    """One ``collective.conclusion`` event with its embedded memory (payload as ``publish.py:96-98``)."""
    return {"type": CONCLUSION_EVENT, "idempotency_key": conclusion_key(conclusion_id, version),
            "payload": {"question_id": question["question_id"], "conclusion_id": conclusion_id, "status": status,
                        "route_label": route_label, "routed_sites": list(route),
                        "verdict_event_ids": list(verdict_event_ids)},
            "memory": dict(memory)}


# --------------------------------------------------------------------------------------------------- the publisher

@dataclass(frozen=True)
class PublishResult:
    """What the API answered for one event (``service.py:1244-1261``), plus how many attempts it took."""

    idempotency_key: str
    type: str
    event_id: str
    created: bool
    memory_id: str | None
    memory_event_id: str | None
    memory_created: bool | None
    attempts: int
    ambiguous: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _result(event: Mapping[str, Any], entry: Mapping[str, Any], attempts: int) -> PublishResult:
    if not isinstance(entry, Mapping) or not isinstance(entry.get("event_id"), str) or "created" not in entry:
        raise PublishError("the API's result entry lacks event_id or created") from None
    created = bool(entry["created"])
    has_memory = "memory_id" in entry
    memory_created: bool | None
    if not has_memory:
        memory_created = None
    elif "memory_created" in entry:
        memory_created = bool(entry["memory_created"])
    else:
        memory_created = "memory_event_id" in entry
    return PublishResult(idempotency_key=str(event["idempotency_key"]), type=str(event["type"]),
                         event_id=entry["event_id"], created=created, memory_id=entry.get("memory_id"),
                         memory_event_id=entry.get("memory_event_id"), memory_created=memory_created,
                         attempts=attempts, ambiguous=(attempts > 1 and not created))


class Publisher:
    """One ``POST /events`` per event through the SDK, with bounded retries, a per-publish ledger and a watermark.

    ``watermark`` (JSON, rewritten atomically after every publish) carries ``{schema, publishes, created, duplicate,
    ambiguous, last_key, last_event_id, updated_at}``, cumulative over every :class:`Publisher` that used the file;
    ``ledger`` (JSON lines, appended after every publish) carries one :class:`PublishResult` per line. Both hold ids,
    keys and counts only. ``on_publish(count, result)`` is called after each publish with the running count of this
    instance (the drills use it to kill the broker after publish k)."""

    def __init__(self, client: MycelicClient, *, watermark: str | Path | None = None, ledger: str | Path | None = None,
                 retries: int = 8, backoff: float = 0.25, max_backoff: float = 5.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], str] = utc_now,
                 on_publish: Callable[[int, PublishResult], None] | None = None) -> None:
        self.client = client
        self.watermark_path = Path(watermark) if watermark is not None else None
        self.ledger_path = Path(ledger) if ledger is not None else None
        self.retries = max(0, int(retries))
        self.backoff = float(backoff)
        self.max_backoff = float(max_backoff)
        self._sleep = sleep
        self._clock = clock
        self._on_publish = on_publish
        self.results: list[PublishResult] = []
        self._state = self._load_watermark()

    # ---- the watermark file
    def _load_watermark(self) -> dict[str, Any]:
        empty = {"schema": WATERMARK_SCHEMA, "publishes": 0, "created": 0, "duplicate": 0, "ambiguous": 0,
                 "last_key": None, "last_event_id": None, "updated_at": None}
        if self.watermark_path is None or not self.watermark_path.exists():
            return empty
        loaded = json.loads(self.watermark_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("schema") != WATERMARK_SCHEMA:
            raise PublishError(f"unreadable publish watermark at {self.watermark_path}") from None
        return {**empty, **{k: loaded[k] for k in empty if k in loaded}}

    @property
    def watermark(self) -> dict[str, Any]:
        return dict(self._state)

    @property
    def publishes(self) -> int:
        return len(self.results)

    @property
    def created(self) -> int:
        return sum(1 for r in self.results if r.created)

    @property
    def duplicate(self) -> int:
        return sum(1 for r in self.results if not r.created)

    def _record(self, result: PublishResult) -> None:
        self.results.append(result)
        st = self._state
        st["publishes"] += 1
        st["created" if result.created else "duplicate"] += 1
        st["ambiguous"] += int(result.ambiguous)
        st["last_key"], st["last_event_id"], st["updated_at"] = result.idempotency_key, result.event_id, self._clock()
        if self.ledger_path is not None:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({**result.to_dict(), "at": st["updated_at"]}, sort_keys=True,
                                    separators=(",", ":"), ensure_ascii=False) + "\n")
        if self.watermark_path is not None:
            write_json_atomic(self.watermark_path, st)

    # ---- one publish
    def publish(self, event: Mapping[str, Any]) -> PublishResult:
        """POST one event; the same event re-sent is the same event id and ``created: false``."""
        if event.get("type") not in (VERDICT_EVENT, CONCLUSION_EVENT):
            raise PublishError("only collective.verdict and collective.conclusion events are published") from None
        if not isinstance(event.get("idempotency_key"), str) or not event["idempotency_key"]:
            raise PublishError("every event needs an idempotency key") from None
        attempts = 0
        while True:
            attempts += 1
            try:
                answer = self.client.publish_events([dict(event)])
                break
            except MycelicError as exc:
                if exc.status not in RETRYABLE_STATUSES or attempts > self.retries:
                    raise PublishError(f"publish of {event['idempotency_key']} failed after {attempts} attempt(s): "
                                       f"HTTP {exc.status}: {exc.message}") from exc
                self._sleep(min(self.max_backoff, self.backoff * (2 ** (attempts - 1))))
        results = answer.get("results") if isinstance(answer, Mapping) else None
        if not isinstance(results, list) or len(results) != 1:
            raise PublishError("the API answered with other than one result for one event") from None
        result = _result(event, results[0], attempts)
        self._record(result)
        if self._on_publish is not None:
            self._on_publish(len(self.results), result)
        return result

    def publish_all(self, events: Sequence[Mapping[str, Any]]) -> list[PublishResult]:
        return [self.publish(e) for e in events]


def event_ids(results: Sequence[PublishResult]) -> dict[str, list[str]]:
    """Every id the API returned, by kind: ``agent.event`` ids (one per POST) and ``memory.observed`` ids (one per
    newly created conclusion memory, ``service.py:1246-1258``); the sets the signature and exactly-once checks cover."""
    return {"agent.event": [r.event_id for r in results],
            "memory.observed": [r.memory_event_id for r in results if r.memory_event_id is not None]}

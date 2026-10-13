"""Harness-side pieces that need the broker itself (``docs/collective/E2E-INGEST.md`` M3, M5, M8, M25; section 3.6).

* :class:`E2EProcessDriver`: ``mycelic.harness.ProcessDriver`` with the two settings the brief adds to the service's
  environment, ``MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG=5000`` (section 3.6) and ``MYCELIC_REPLAY_MAX_REJECT_RATIO=0.05``
  (M5); everything else is what ``harness.py:132-142`` sets. The driver refuses to start without nats-server and
  generates the signing key the service runs under (``harness.py:97-99``, ``:111``, ``:138``).
* :func:`read_stream`: every message of stream ``MYCELIC`` from ``first_seq`` to ``last_seq`` by sequence through
  nats-py (``JetStreamContext.get_msg``) with the driver's credentials, each checked with
  ``Keyring(driver.signing_key).verify_event(data, headers["Mycelic-Signature"])`` (the service signs the exact wire
  bytes, ``service.py:1041-1044``), and each verified payload's ``event_id`` and ``kind`` read. The report keeps ids,
  kinds, sizes and digests; never a payload.
* the three forgeries of M8, each on ``subject_for(org, "memory.observed")`` with its own ``Nats-Msg-Id`` (the stream
  de-duplicates on that header, so a re-used id would be dropped silently and prove nothing): no signature; a
  signature under a key the service never had; one of our own messages re-published with its ``memory_id`` replaced
  and its original header (altered bytes fail ``verify_event``, ``integrity.py:182-190``).
* :func:`signature_failures`: ``mycelic_events_failed_total{stage="signature"}`` from ``/metrics`` (``metrics.py:35``;
  the consumer counts it at ``service.py:929-931``), read with the exact label.
* :func:`broker_version`: ``Client.connected_server_version`` of a harness connection to the driver's broker.
* :func:`wait_idle`: the smoke test's idle condition (``tests/smoke/mycelic_smoke.py:59-80``) over ``/admin/status``.

Nothing here touches the service's own transport object (M25) and nothing here reads a record.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import nats

from mycelic.config import Settings
from mycelic.harness import ProcessDriver
from mycelic.integrity import Keyring
from mycelic.sdk import MycelicClient, MycelicError
from mycelic.transport import subject_for

STREAM: str = Settings.__dataclass_fields__["nats_stream"].default          # "MYCELIC" (config.py:82)
SIGNATURE_HEADER = "Mycelic-Signature"
MSG_ID_HEADER = "Nats-Msg-Id"
SIGNATURE_METRIC = "mycelic_events_failed_total"
SIGNATURE_LABELS = {"stage": "signature"}
MAX_ACTIVE_MEMORIES_PER_ORG = 5000
REPLAY_MAX_REJECT_RATIO = "0.05"
_METRIC_LINE = re.compile(r'^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)')
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


class E2EProcessDriver(ProcessDriver):
    """``ProcessDriver`` with the brief's two additions to the service environment; nothing else changes."""

    def env(self) -> dict[str, str]:
        env = super().env()
        env["MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG"] = str(MAX_ACTIVE_MEMORIES_PER_ORG)
        env["MYCELIC_REPLAY_MAX_REJECT_RATIO"] = REPLAY_MAX_REJECT_RATIO
        return env


def nats_url(driver: ProcessDriver) -> str:
    return f"nats://127.0.0.1:{driver.nats_port}"


async def _connect(driver: ProcessDriver) -> Any:
    return await nats.connect(nats_url(driver), user=driver.nats_user, password=driver.nats_password,
                              connect_timeout=5, max_reconnect_attempts=2, allow_reconnect=False)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------------------------------- the stream

@dataclass(frozen=True)
class StreamMessage:
    """One stream message as the harness saw it: where it sits, whether our key signed it, and what it says it is."""

    seq: int
    subject: str
    msg_id: str | None
    bytes: int
    sha256: str
    signed: bool
    verified: bool
    event_id: str | None
    kind: str | None
    org_id: str | None
    memory_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StreamReport:
    stream: str
    first_seq: int
    last_seq: int
    broker_version: str
    messages: list[StreamMessage] = field(default_factory=list)

    @property
    def fetched(self) -> int:
        return len(self.messages)

    @property
    def verified(self) -> int:
        return sum(1 for m in self.messages if m.verified)

    @property
    def foreign(self) -> int:
        return sum(1 for m in self.messages if not m.verified)

    @property
    def kinds(self) -> dict[str, int]:
        """Verified messages by kind."""
        out: dict[str, int] = {}
        for m in self.messages:
            if m.verified and m.kind is not None:
                out[m.kind] = out.get(m.kind, 0) + 1
        return dict(sorted(out.items()))

    @property
    def event_ids(self) -> list[str]:
        """The ``event_id`` of every verified payload, in stream order (duplicates kept)."""
        return [m.event_id for m in self.messages if m.verified and m.event_id is not None]

    def ids_in_stream(self, api_ids: Sequence[str]) -> tuple[int, int]:
        """``(present exactly once, duplicated)`` over the API-returned ids (M3's ``api_event_ids_in_stream`` and
        ``api_event_ids_duplicated``)."""
        counts: dict[str, int] = {}
        for eid in self.event_ids:
            counts[eid] = counts.get(eid, 0) + 1
        once = sum(1 for i in api_ids if counts.get(i, 0) == 1)
        duplicated = sum(1 for i in api_ids if counts.get(i, 0) > 1)
        return once, duplicated

    def to_dict(self) -> dict[str, Any]:
        return {"stream": self.stream, "first_seq": self.first_seq, "last_seq": self.last_seq,
                "broker_version": self.broker_version, "fetched": self.fetched, "verified": self.verified,
                "foreign": self.foreign, "kinds": self.kinds, "messages": [m.to_dict() for m in self.messages]}


def _classify(seq: int, subject: str, data: bytes, headers: Mapping[str, str], keyring: Keyring) -> StreamMessage:
    signature = headers.get(SIGNATURE_HEADER)
    verified = signature is not None and keyring.verify_event(data, signature)
    event_id = kind = org_id = memory_id = None
    if verified:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            event_id = payload.get("event_id") if isinstance(payload.get("event_id"), str) else None
            kind = payload.get("kind") if isinstance(payload.get("kind"), str) else None
            org_id = payload.get("org_id") if isinstance(payload.get("org_id"), str) else None
            inner = payload.get("payload")
            if isinstance(inner, dict) and isinstance(inner.get("memory_id"), str):
                memory_id = inner["memory_id"]
    return StreamMessage(seq=seq, subject=subject, msg_id=headers.get(MSG_ID_HEADER), bytes=len(data),
                         sha256=hashlib.sha256(data).hexdigest(), signed=signature is not None, verified=verified,
                         event_id=event_id, kind=kind, org_id=org_id, memory_id=memory_id)


def _version(nc: Any) -> str:
    v = nc.connected_server_version
    try:
        return f"{v.major}.{v.minor}.{v.patch}"
    except ValueError:
        return str(getattr(v, "_server_version", "unknown"))


async def _read_stream(driver: ProcessDriver, first_seq: int | None, last_seq: int | None,
                       keyring: Keyring) -> StreamReport:
    nc = await _connect(driver)
    try:
        js = nc.jetstream()
        info = await js.stream_info(STREAM)
        first = int(info.state.first_seq) if first_seq is None else int(first_seq)
        last = int(info.state.last_seq) if last_seq is None else int(last_seq)
        report = StreamReport(stream=STREAM, first_seq=first, last_seq=last, broker_version=_version(nc))
        for seq in range(first, last + 1):
            try:
                raw = await js.get_msg(STREAM, seq)
            except Exception as exc:          # a sequence a purge removed: reported as a gap, never invented
                if type(exc).__name__ == "NotFoundError":
                    continue
                raise
            report.messages.append(_classify(seq, raw.subject or "", raw.data or b"", dict(raw.headers or {}),
                                             keyring))
        return report
    finally:
        await nc.close()


def read_stream(driver: ProcessDriver, *, first_seq: int | None = None, last_seq: int | None = None,
                keyring: Keyring | None = None) -> StreamReport:
    """Every message of the stream by sequence, each checked under ``Keyring(driver.signing_key)`` (M3)."""
    return _run(_read_stream(driver, first_seq, last_seq, keyring or Keyring(driver.signing_key)))


async def _fetch_raw(driver: ProcessDriver, seq: int) -> tuple[bytes, dict[str, str], str]:
    nc = await _connect(driver)
    try:
        raw = await nc.jetstream().get_msg(STREAM, int(seq))
        return raw.data or b"", dict(raw.headers or {}), raw.subject or ""
    finally:
        await nc.close()


def fetch_raw(driver: ProcessDriver, seq: int) -> tuple[bytes, dict[str, str], str]:
    """``(data, headers, subject)`` of one stream message, for the re-publish forgery."""
    return _run(_fetch_raw(driver, seq))


async def _stream_payloads(driver: ProcessDriver) -> tuple[str, int]:
    nc = await _connect(driver)
    try:
        js = nc.jetstream()
        info = await js.stream_info(STREAM)
        first, last = int(info.state.first_seq), int(info.state.last_seq)
        parts: list[str] = []
        for seq in range(first, last + 1):
            try:
                raw = await js.get_msg(STREAM, seq)
            except Exception as exc:
                if type(exc).__name__ == "NotFoundError":
                    continue
                raise
            parts.append((raw.data or b"").decode("utf-8", "replace"))
        return "\n".join(parts), len(parts)
    finally:
        await nc.close()


def stream_payloads(driver: ProcessDriver) -> tuple[str, int]:
    """Every stream message's bytes as text, and how many there were: drill 5's scan reads the stream itself, not
    only the fabric's database. Decoded with replacement, so no byte could hide behind an encoding. The caller
    scans it for record refs and narratives and keeps counts only."""
    return _run(_stream_payloads(driver))


def broker_version(driver: ProcessDriver) -> str:
    async def go() -> str:
        nc = await _connect(driver)
        try:
            return _version(nc)
        finally:
            await nc.close()
    return _run(go())


# --------------------------------------------------------------------------------------------------- forgeries

async def _publish_raw(driver: ProcessDriver, subject: str, data: bytes, headers: Mapping[str, str]) -> int:
    nc = await _connect(driver)
    try:
        ack = await nc.jetstream().publish(subject, data, headers=dict(headers), timeout=5.0)
        return int(ack.seq)
    finally:
        await nc.close()


def publish_raw(driver: ProcessDriver, subject: str, data: bytes, headers: Mapping[str, str]) -> int:
    """Publish straight to the broker with the driver's credentials; returns the stream sequence."""
    return _run(_publish_raw(driver, subject, data, headers))


def forged_event(*, org_id: str, memory_id: str, producer_id: str, scope: str, event_id: str | None = None) -> bytes:
    """A well-formed ``memory.observed`` event in the shape of ``tests/mycelic/test_jetstream.py:544-548``: an
    unkeyed service would apply it, so its absence after the publish is evidence (M8)."""
    event = {"event_id": event_id or f"evt_forged_{memory_id}", "kind": "memory.observed", "org_id": org_id,
             "agent_id": producer_id, "created_at": "2026-01-01T00:00:00+00:00",
             "payload": {"memory_id": memory_id, "org_id": org_id, "layer": "agent", "scope": scope, "text": "forged",
                         "producer_id": producer_id, "operator": "agent_observation"}, "schema": 1}
    return json.dumps(event, ensure_ascii=False).encode("utf-8")


def forge_unsigned(driver: ProcessDriver, *, org_id: str, memory_id: str, producer_id: str, scope: str) -> int:
    """Forgery 1: no ``Mycelic-Signature`` header."""
    data = forged_event(org_id=org_id, memory_id=memory_id, producer_id=producer_id, scope=scope)
    return publish_raw(driver, subject_for(org_id, "memory.observed"), data,
                       {MSG_ID_HEADER: f"evt_forged_{memory_id}"})


def forge_wrong_key(driver: ProcessDriver, *, org_id: str, memory_id: str, producer_id: str, scope: str,
                    key: str | None = None) -> int:
    """Forgery 2: signed under a key the service never had (``Keyring(secrets.token_hex(32))``)."""
    data = forged_event(org_id=org_id, memory_id=memory_id, producer_id=producer_id, scope=scope)
    signature = Keyring(key or secrets.token_hex(32)).event_signature(data)
    assert signature is not None
    return publish_raw(driver, subject_for(org_id, "memory.observed"), data,
                       {MSG_ID_HEADER: f"evt_forged_{memory_id}", SIGNATURE_HEADER: signature})


def forge_replay(driver: ProcessDriver, *, seq: int, memory_id: str) -> int:
    """Forgery 3: one of our own stream messages re-published with its ``memory_id`` replaced and its original
    signature header, under a new message id (altered bytes fail ``verify_event``)."""
    data, headers, subject = fetch_raw(driver, seq)
    signature = headers.get(SIGNATURE_HEADER)
    if signature is None:
        raise ValueError(f"stream message {seq} carries no signature header; pick one of ours") from None
    event = json.loads(data.decode("utf-8"))
    if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
        raise ValueError(f"stream message {seq} is not an event with a payload") from None
    event["payload"]["memory_id"] = memory_id
    event["event_id"] = f"evt_forged_{memory_id}"
    altered = json.dumps(event, ensure_ascii=False).encode("utf-8")
    if altered == data:
        raise ValueError("the altered message equals the original") from None
    return publish_raw(driver, subject, altered,
                       {MSG_ID_HEADER: f"evt_forged_{memory_id}", SIGNATURE_HEADER: signature})


# --------------------------------------------------------------------------------------------------- the service

def metrics_text(base_url: str, token: str, timeout: float = 10.0) -> str:
    req = urllib.request.Request(f"{base_url.rstrip('/')}/metrics", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def metric_value(text: str, name: str, labels: Mapping[str, str] | None = None) -> float | None:
    """The sample of ``name`` whose labels equal ``labels`` exactly, or None when the text has no such sample."""
    wanted = dict(labels or {})
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE.match(line)
        if m is None or m.group("name") != name:
            continue
        found = {k: v.encode("utf-8").decode("unicode_escape") for k, v in _LABEL.findall(m.group("labels") or "")}
        if found == wanted:
            return float(m.group("value"))
    return None


def signature_failures(base_url: str, token: str) -> int:
    """``mycelic_events_failed_total{stage="signature"}``; 0 when the counter has never been incremented (the
    client library emits a labelled counter only once a label set has been touched)."""
    value = metric_value(metrics_text(base_url, token), SIGNATURE_METRIC, SIGNATURE_LABELS)
    return int(value) if value is not None else 0


def wait_for(predicate: Callable[[], bool], timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def wait_idle(admin: MycelicClient, timeout: float = 90.0) -> dict[str, Any]:
    """``/admin/status`` once the outbox and the consumer are empty, no replay is running and the transport is
    connected (``mycelic_smoke.py:59-80``); raises ``TimeoutError`` with the last checks otherwise."""
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            st = admin.status()
        except MycelicError:
            time.sleep(0.5)
            continue
        last = st
        c = st["checks"]
        pending = (c["publisher"].get("outbox_pending") or 0) + (c["consumer"].get("pending") or 0)
        if pending == 0 and c["consumer"]["replaying_to_seq"] is None and c["transport"].get("connected"):
            time.sleep(0.5)                          # one more round: a just-published event may still be in flight
            st = admin.status()
            c = st["checks"]
            if (c["publisher"].get("outbox_pending") or 0) + (c["consumer"].get("pending") or 0) == 0:
                return st
        time.sleep(0.5)
    raise TimeoutError(f"the fabric did not become idle: {json.dumps(last.get('checks'), default=str)[:800]}")


def fabric_counts(status: Mapping[str, Any]) -> dict[str, Any]:
    """The transport and publisher figures of one ``/admin/status`` answer (``service.py:2496-2506``)."""
    checks = status.get("checks") or {}
    transport, publisher = checks.get("transport") or {}, checks.get("publisher") or {}
    consumer = checks.get("consumer") or {}
    return {"status": status.get("status"), "transport": transport.get("transport"),
            "connected": transport.get("connected"), "reconnects": transport.get("reconnects"),
            "stream_messages": transport.get("stream_messages"), "first_seq": transport.get("first_seq"),
            "last_seq": transport.get("last_seq"), "consumer_pending": transport.get("consumer_pending"),
            "outbox_pending": publisher.get("outbox_pending"), "replaying_to_seq": consumer.get("replaying_to_seq"),
            "ready_block": consumer.get("ready_block")}


# --------------------------------------------------------------------------------------------------- counters

def metric_sum(text: str, name: str, labels: Mapping[str, str] | None = None) -> float:
    """The sum of every sample of ``name`` whose labels include ``labels``: ``mycelic_events_applied_total`` carries
    ``kind`` and ``result`` (``metrics.py:34``), so a per-kind figure sums over the results."""
    wanted = dict(labels or {})
    total = 0.0
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE.match(line)
        if m is None or m.group("name") != name:
            continue
        found = {k: v.encode("utf-8").decode("unicode_escape") for k, v in _LABEL.findall(m.group("labels") or "")}
        if all(found.get(k) == v for k, v in wanted.items()):
            total += float(m.group("value"))
    return total


def event_counters(base_url: str, token: str) -> dict[str, float]:
    """From one ``/metrics`` read: ``mycelic_events_applied_total`` per kind, the deliveries of each kind whatever
    their result, the ignored total (``metrics.py:39``) and the signature failures (M6, M8).

    The counter carries ``kind`` and ``result`` (``applied``, ``duplicate``, ``ignored``;
    ``service.py:1407-1484``), and a broker restart re-delivers what was in flight, so only the samples with
    ``result="applied"`` count one event once — which is what M6's ``applied_delta_*`` means."""
    text = metrics_text(base_url, token)
    kinds = ("agent.event", "memory.observed", "memory.derived", "memory.attested", "memory.retracted")
    out = {f"applied_{kind.replace('.', '_')}": metric_sum(text, "mycelic_events_applied_total",
                                                           {"kind": kind, "result": "applied"}) for kind in kinds}
    out.update({f"delivered_{kind.replace('.', '_')}": metric_sum(text, "mycelic_events_applied_total",
                                                                  {"kind": kind}) for kind in kinds})
    out["applied_total"] = metric_sum(text, "mycelic_events_applied_total", {"result": "applied"})
    out["delivered_total"] = metric_sum(text, "mycelic_events_applied_total")
    out["ignored_total"] = metric_sum(text, "mycelic_events_ignored_total")
    out["signature_failures"] = metric_sum(text, SIGNATURE_METRIC, SIGNATURE_LABELS)
    return out


def audit_rows(base_url: str, token: str, limit: int = 500) -> list[dict[str, Any]]:
    """``GET /admin/audit`` (``api.py:459``), newest first; drill 6 reads ``recovery.signature_rejections``."""
    req = urllib.request.Request(f"{base_url.rstrip('/')}/admin/audit?limit={int(limit)}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return list(json.loads(resp.read().decode("utf-8")).get("audit") or [])


def ready(base_url: str, timeout: float = 5.0) -> tuple[int, dict[str, Any]]:
    """``GET /ready``: the status code and the body (503 while a readiness block stands, ``service.py:596-612``)."""
    req = urllib.request.Request(f"{base_url.rstrip('/')}/ready")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, (json.loads(body) if body else {})
        except json.JSONDecodeError:
            return exc.code, {}


# --------------------------------------------------------------------------------------------------- service DB

def service_db_path(driver: ProcessDriver) -> Path:
    """Where ``ProcessDriver`` puts the service's database (its ``env``: ``MYCELIC_DB_PATH``)."""
    return Path(driver.workdir) / "mycelic.db"


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)


def service_meta(driver: ProcessDriver, key: str) -> str | None:
    """One ``meta`` row of the service database, read ``mode=ro`` (``store.py:49-52``)."""
    conn = _read_only(service_db_path(driver))
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ? ORDER BY key", (key,)).fetchone()
        return row[0] if row is not None else None
    finally:
        conn.close()


def known_key_ids(driver: ProcessDriver) -> list[str]:
    """``meta.known_key_ids``: every signing key id the service has started under (``service.py:467-478``), which
    is how M3 binds the stream to the key the harness holds."""
    raw = service_meta(driver, "known_key_ids")
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [k for k in loaded if isinstance(k, str)] if isinstance(loaded, list) else []


def fabric_db_text(driver: ProcessDriver) -> tuple[str, int, dict[str, int]]:
    """Every row of every table of the service database as JSON text, ``mode=ro`` (drill 5's scan input):
    ``(text, bytes, rows per table)``. A blob is decoded as UTF-8 with replacement, so no byte of a record could
    hide from the scan behind an encoding."""
    conn = _read_only(service_db_path(driver))
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "ORDER BY name").fetchall()]
        parts: list[str] = []
        counts: dict[str, int] = {}
        for table in tables:
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            counts[table] = len(rows)
            for row in rows:
                parts.append(json.dumps([v.decode("utf-8", "replace") if isinstance(v, bytes) else v for v in row],
                                        ensure_ascii=False, default=str))
        text = "\n".join(parts)
        return text, len(text.encode("utf-8")), counts
    finally:
        conn.close()

"""Logging, metrics, error reports and backups for every Mycelic process.

Four small pieces that the API, the worker and the holder share, kept in one module so operators have one
place to read when a log line or a metric name looks unfamiliar (docs/mycelic/RUNBOOK.md is the operator
view of this file):

* **Logging** — ``configure_logging(settings)`` installs one handler on the root logger writing to stderr.
  With ``settings.log_json`` every record is one JSON object per line (``ts``, ``level``, ``logger``, ``msg``,
  ``service``, ``request_id`` when bound, any ``extra=`` keys) so a log shipper needs no parser; otherwise the
  usual one-line text format. ``request_id_var`` is a ``ContextVar`` the HTTP middleware binds per request
  with ``bind_request_id`` so every line written while handling that request carries the same id as the
  ``X-Request-Id`` response header.

* **Metrics** — a dependency-free Prometheus registry. The metric names are the contract in
  docs/mycelic/API.md (``METRIC_NAMES``), pre-registered so ``/metrics`` always lists every family even before
  the first sample. Gauges that come from the database (queue backlog, dead jobs, claim counts, stale
  evidence, heartbeat age) are refreshed by *collectors* registered by the API process and run at scrape time,
  so the registry never holds a database handle itself.

* **Error reports** — ``ErrorReporter.report`` writes a row to ``error_reports`` and, when
  ``settings.error_webhook`` is set, POSTs the same JSON to it. Reporting an error must never make the
  original failure worse, so the webhook call is fire-and-forget with a short timeout and every branch swallows
  and logs its own exceptions.

* **Backups** — ``backup_bundle`` / ``restore_bundle`` implement ``python -m mycelic backup|restore``. Copies
  are taken with ``sqlite3.Connection.backup`` (the online backup API), which yields a consistent snapshot of a
  WAL database while the API and worker keep writing; plain file copies of a live WAL database do not. The
  bundle is a ``tar.gz`` with a ``manifest.json`` (versions, timestamps, sha256 per file) and restore verifies
  every checksum before it touches the data directory.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from . import __version__
from .util import j, new_id, now_iso

logger = logging.getLogger(__name__)

# ====================================================================== logging

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("mycelic_request_id", default=None)

# Attributes every LogRecord carries; anything else on the record came from ``extra=`` and is emitted as a field.
_STANDARD_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module", "exc_info", "exc_text",
    "stack_info", "lineno", "funcName", "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


def bind_request_id(request_id: str | None = None) -> str:
    """Bind a request id to the current context (task) and return it; generates ``req_...`` when none is given."""
    rid = request_id or new_id("req")
    request_id_var.set(rid)
    return rid


def current_request_id() -> str | None:
    return request_id_var.get()


def clear_request_id() -> None:
    request_id_var.set(None)


def _jsonable(value: Any) -> Any:
    """Log extras may be anything; the line must still be valid JSON."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


class JsonFormatter(logging.Formatter):
    """One JSON object per line. ``service`` names the process (api | worker | holder) so mixed streams sort."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z"
        line: dict[str, Any] = {"ts": ts, "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
                                "service": self.service}
        rid = request_id_var.get()
        if rid:
            line["request_id"] = rid
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_") or key in line:
                continue
            line[key] = _jsonable(value)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack"] = record.stack_info
        return json.dumps(line, ensure_ascii=False, default=str)


class _RequestIdFilter(logging.Filter):
    """Makes ``%(request_id)s`` usable in the plain format without every caller passing it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get() or "-"
        return True


_HANDLER_MARK = "_mycelic_handler"


def configure_logging(settings: Any, *, stream: Any = None) -> logging.Handler:
    """Install the process-wide log handler. Safe to call more than once (the previous handler is replaced).

    ``stream`` exists for tests; production always writes to stderr because stdout may be a protocol channel
    (the holder's local CLI) and container runtimes collect stderr anyway.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)
            with contextlib.suppress(Exception):
                h.close()
    service = getattr(settings, "service_name", "api") or "api"
    handler = logging.StreamHandler(stream or sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    if getattr(settings, "log_json", True):
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.addFilter(_RequestIdFilter())
        handler.setFormatter(logging.Formatter(f"%(asctime)s %(levelname)s {service} %(name)s [%(request_id)s] %(message)s"))
    root.addHandler(handler)
    level_name = str(getattr(settings, "log_level", "INFO") or "INFO").upper()
    root.setLevel(logging.getLevelName(level_name) if isinstance(logging.getLevelName(level_name), int) else logging.INFO)
    # aiohttp's access log duplicates what the request middleware logs with a request_id; keep it quiet.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    return handler


# ====================================================================== metrics

COUNTER, GAUGE, HISTOGRAM = "counter", "gauge", "histogram"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# The catalogue from docs/mycelic/API.md. Names, kinds, and the label names each family is documented with.
METRIC_SPECS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("mycelic_model_calls_total", COUNTER, ("provider", "model", "tier", "ok"), "Model provider calls."),
    ("mycelic_model_cost_usd_total", COUNTER, ("provider", "model", "tier"), "Estimated model spend in USD."),
    ("mycelic_jobs_backlog", GAUGE, (), "Jobs queued or leased in the coordination DB."),
    ("mycelic_jobs_dead", GAUGE, (), "Jobs that exhausted their attempts (the failed-job queue)."),
    ("mycelic_questions_total", COUNTER, ("outcome",), "Questions resolved, by outcome."),
    ("mycelic_routes_failed_total", COUNTER, (), "Question routes that ended in failed or timeout."),
    ("mycelic_claims", GAUGE, ("status",), "Claims by commit-gate status."),
    ("mycelic_evidence_stale", GAUGE, (), "Evidence references older than the freshness policy."),
    ("mycelic_worker_heartbeat_age_seconds", GAUGE, (), "Seconds since the newest worker heartbeat."),
    ("mycelic_http_requests_total", COUNTER, ("method", "route", "status"), "HTTP requests served."),
    ("mycelic_http_request_seconds", HISTOGRAM, ("method", "route"), "HTTP request latency."),
)
METRIC_NAMES: tuple[str, ...] = tuple(spec[0] for spec in METRIC_SPECS)

_LabelKey = tuple[tuple[str, str], ...]


def _label_key(labels: dict[str, Any]) -> _LabelKey:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _fmt_value(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(float(v))


def _escape_label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_labels(key: _LabelKey, extra: tuple[tuple[str, str], ...] = ()) -> str:
    items = list(key) + list(extra)
    if not items:
        return ""
    return "{" + ",".join(f'{k}="{_escape_label(v)}"' for k, v in items) + "}"


class _Family:
    def __init__(self, name: str, kind: str, help_text: str, labelnames: tuple[str, ...], buckets: tuple[float, ...]) -> None:
        self.name, self.kind, self.help, self.labelnames = name, kind, help_text, labelnames
        self.buckets = tuple(sorted(buckets))
        self.values: dict[_LabelKey, float] = {}
        self.hist: dict[_LabelKey, tuple[list[int], float, int]] = {}   # key -> (bucket counts, sum, count)


class Metrics:
    """A small Prometheus-compatible registry: counters, gauges, histograms, each with labels.

    Deliberately lenient: label sets are per sample and unknown metric names are auto-registered as the kind the
    call implies, because a metrics path must never raise inside a request handler. ``render_prometheus`` emits
    the text exposition format (``# HELP`` / ``# TYPE`` then samples). Collectors (``add_collector``) are called
    on every render so database-derived gauges are fresh at scrape time without a background task.
    """

    def __init__(self, *, register_defaults: bool = True) -> None:
        self._lock = threading.Lock()
        self._families: dict[str, _Family] = {}
        self._collectors: list[Callable[["Metrics"], None]] = []
        if register_defaults:
            for name, kind, labels, help_text in METRIC_SPECS:
                self.register(name, kind, help_text, labelnames=labels)

    # ------------------------------------------------------------------ registration
    def register(self, name: str, kind: str, help_text: str = "", *, labelnames: Iterable[str] = (),
                 buckets: Iterable[float] = DEFAULT_BUCKETS) -> None:
        with self._lock:
            if name not in self._families:
                self._families[name] = _Family(name, kind, help_text, tuple(labelnames), tuple(buckets))

    def counter(self, name: str, help_text: str = "", *, labelnames: Iterable[str] = ()) -> None:
        self.register(name, COUNTER, help_text, labelnames=labelnames)

    def gauge(self, name: str, help_text: str = "", *, labelnames: Iterable[str] = ()) -> None:
        self.register(name, GAUGE, help_text, labelnames=labelnames)

    def histogram(self, name: str, help_text: str = "", *, labelnames: Iterable[str] = (), buckets: Iterable[float] = DEFAULT_BUCKETS) -> None:
        self.register(name, HISTOGRAM, help_text, labelnames=labelnames, buckets=buckets)

    def add_collector(self, fn: Callable[["Metrics"], None]) -> None:
        """``fn(metrics)`` runs at every render; it should ``set`` gauges from live state and return quickly."""
        self._collectors.append(fn)

    def remove_collector(self, fn: Callable[["Metrics"], None]) -> None:
        with contextlib.suppress(ValueError):
            self._collectors.remove(fn)

    def _family(self, name: str, kind: str) -> _Family:
        fam = self._families.get(name)
        if fam is None:
            fam = _Family(name, kind, "", (), DEFAULT_BUCKETS)
            self._families[name] = fam
        return fam

    # ------------------------------------------------------------------ writes
    def inc(self, name: str, value: float = 1.0, **labels: Any) -> None:
        with self._lock:
            fam = self._family(name, COUNTER)
            key = _label_key(labels)
            fam.values[key] = fam.values.get(key, 0.0) + float(value)

    def set(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            fam = self._family(name, GAUGE)
            fam.values[_label_key(labels)] = float(value)

    def observe(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            fam = self._family(name, HISTOGRAM)
            key = _label_key(labels)
            counts, total, n = fam.hist.get(key, ([0] * len(fam.buckets), 0.0, 0))
            for i, edge in enumerate(fam.buckets):
                if value <= edge:
                    counts[i] += 1
            fam.hist[key] = (counts, total + float(value), n + 1)

    @contextlib.contextmanager
    def timer(self, name: str, **labels: Any) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - start, **labels)

    # ------------------------------------------------------------------ reads
    def get(self, name: str, **labels: Any) -> float | None:
        with self._lock:
            fam = self._families.get(name)
            if fam is None:
                return None
            if fam.kind == HISTOGRAM:
                h = fam.hist.get(_label_key(labels))
                return None if h is None else float(h[2])
            return fam.values.get(_label_key(labels))

    def reset(self) -> None:
        with self._lock:
            for fam in self._families.values():
                fam.values.clear()
                fam.hist.clear()

    def run_collectors(self) -> None:
        for fn in list(self._collectors):
            try:
                fn(self)
            except Exception:                       # a broken collector must not take /metrics down
                logger.exception("metrics collector %r failed", getattr(fn, "__name__", fn))

    def render_prometheus(self) -> str:
        self.run_collectors()
        out: list[str] = []
        with self._lock:
            for name in sorted(self._families):
                fam = self._families[name]
                out.append(f"# HELP {name} {fam.help}".rstrip())
                out.append(f"# TYPE {name} {fam.kind}")
                if fam.kind == HISTOGRAM:
                    for key in sorted(fam.hist):
                        counts, total, n = fam.hist[key]
                        for edge, c in zip(fam.buckets, counts):
                            out.append(f"{name}_bucket{_fmt_labels(key, (('le', _fmt_value(edge)),))} {c}")
                        out.append(f"{name}_bucket{_fmt_labels(key, (('le', '+Inf'),))} {n}")
                        out.append(f"{name}_sum{_fmt_labels(key)} {_fmt_value(total)}")
                        out.append(f"{name}_count{_fmt_labels(key)} {n}")
                    continue
                if not fam.values and not fam.labelnames:
                    out.append(f"{name} 0")         # an unlabelled family always has one sample, so dashboards see it
                for key in sorted(fam.values):
                    out.append(f"{name}{_fmt_labels(key)} {_fmt_value(fam.values[key])}")
        return "\n".join(out) + "\n"


metrics = Metrics()
"""Process-wide registry. Every module records into this one; the API serves it at ``/metrics``."""


# ====================================================================== error reports

class ErrorReporter:
    """Persist and forward errors. ``db`` is a ``CoordDB`` (or ``None`` in a process without one).

    ``report`` returns the new row id (or ``None``) and never raises: an error report that raised would hide
    the original error, which is the one the operator needs to see.
    """

    WEBHOOK_TIMEOUT_SECONDS = 5.0

    def __init__(self, db: Any | None, settings: Any) -> None:
        self.db = db
        self.settings = settings
        self._tasks: set[asyncio.Task] = set()

    async def report(self, service: str, message: str, *, level: str = "error", detail: dict[str, Any] | None = None,
                     request_id: str | None = None, tenant_id: str | None = None) -> int | None:
        at = now_iso()
        rid = request_id or request_id_var.get()
        row_id: int | None = None
        if self.db is not None:
            try:
                async with self.db.tx() as c:
                    cur = c.execute("INSERT INTO error_reports(at, service, level, message, detail, request_id, tenant_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (at, service, level, str(message)[:4000], j(detail), rid, tenant_id))
                    row_id = int(cur.lastrowid)
            except Exception:
                logger.exception("could not write error report")
        webhook = getattr(self.settings, "error_webhook", "") or ""
        if webhook:
            payload = {"at": at, "service": service, "level": level, "message": str(message)[:4000], "detail": detail or {},
                       "request_id": rid, "tenant_id": tenant_id, "version": __version__, "hostname": socket.gethostname(), "id": row_id}
            try:
                task = asyncio.get_running_loop().create_task(self._post(webhook, payload))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            except RuntimeError:                    # no running loop (sync caller): skip the webhook, the row is written
                logger.warning("error webhook skipped: no running event loop")
        return row_id

    async def _post(self, url: str, payload: dict[str, Any]) -> None:
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self.WEBHOOK_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status >= 400:
                        logger.warning("error webhook returned %s", resp.status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("error webhook failed: %s", exc)

    async def close(self) -> None:
        """Wait (briefly) for in-flight webhook posts; used on shutdown and by tests."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=self.WEBHOOK_TIMEOUT_SECONDS + 1)
        for t in list(self._tasks):
            t.cancel()

    def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        rows = self.db.all("SELECT * FROM error_reports ORDER BY id DESC LIMIT ?", (int(limit),))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d.get("detail") or "{}")
            except ValueError:
                pass
            out.append(d)
        return out


# ====================================================================== backups

BUNDLE_FORMAT = "mycelic-backup"
BUNDLE_FORMAT_VERSION = 1
COORD_MEMBER = "coord.db"
HOLDER_DB_NAME = "evidence.db"
_HOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class BackupError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sqlite_backup(src_path: str | Path, dst_path: str | Path) -> dict[str, Any]:
    """Consistent online copy of one SQLite database using the backup API.

    Works while other processes write (WAL mode); the copy is a single self-contained file (no ``-wal`` needed)
    because the destination connection is in the default rollback-journal mode and is checkpointed on close.
    Returns ``{"sha256", "bytes", "pages"}`` of the destination.
    """
    src_path, dst_path = Path(src_path), Path(dst_path)
    if not src_path.is_file():
        raise BackupError(f"database not found: {src_path}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst, pages=512, sleep=0.05)
            dst.execute("PRAGMA journal_mode=DELETE")
            pages = int(dst.execute("PRAGMA page_count").fetchone()[0])
        finally:
            dst.close()
    finally:
        src.close()
    return {"sha256": sha256_file(dst_path), "bytes": dst_path.stat().st_size, "pages": pages}


def _schema_info(db_path: Path) -> dict[str, Any]:
    """Read-only look at ``schema_migrations`` so the manifest says which schema the bundle needs."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        finally:
            conn.close()
        return {"current": max((int(v) for v, _ in rows), default=0), "applied": [{"version": int(v), "name": n} for v, n in rows]}
    except sqlite3.Error:
        return {"current": 0, "applied": []}


def find_holder_dbs(holders_dir: str | Path) -> list[tuple[str, Path]]:
    """``(holder_id, path)`` for every ``<holders_dir>/<holder_id>/evidence.db``."""
    root = Path(holders_dir)
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        db = child / HOLDER_DB_NAME
        if child.is_dir() and db.is_file() and _HOLDER_ID_RE.match(child.name):
            out.append((child.name, db))
    return out


def backup_bundle(settings: Any, out_path: str | Path, *, include_holders: bool = True, include_coord: bool = True) -> dict[str, Any]:
    """Write ``coord.db`` and every holder ``evidence.db`` into one ``tar.gz`` with a manifest. Returns the manifest.

    Layout inside the archive: ``manifest.json``, ``coord.db``, ``holders/<holder_id>/evidence.db``. The
    manifest records the schema version so restore can warn when the bundle needs a migration the running code
    does not have.
    """
    out_path = Path(out_path)
    coord_path = Path(settings.coord_db)
    holders_dir = Path(settings.holders_dir)
    if include_coord and not coord_path.is_file():
        raise BackupError(f"coordination database not found: {coord_path} (set MYCELIC_DATA_DIR or MYCELIC_COORD_DB)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = now_iso()
    files: list[dict[str, Any]] = []
    # Stage the copies next to the archive (same filesystem) so a large holder store is not copied twice.
    with tempfile.TemporaryDirectory(prefix=".mycelic-backup-", dir=str(out_path.parent)) as tmp:
        stage = Path(tmp)
        if include_coord:
            info = sqlite_backup(coord_path, stage / COORD_MEMBER)
            files.append({"path": COORD_MEMBER, "kind": "coord", "holder_id": None, "source": str(coord_path), **info,
                          "schema": _schema_info(stage / COORD_MEMBER)})
        if include_holders:
            for holder_id, db in find_holder_dbs(holders_dir):
                member = f"holders/{holder_id}/{HOLDER_DB_NAME}"
                info = sqlite_backup(db, stage / member)
                files.append({"path": member, "kind": "holder", "holder_id": holder_id, "source": str(db), **info})
        manifest = {
            "format": BUNDLE_FORMAT, "format_version": BUNDLE_FORMAT_VERSION,
            "created_at": started, "finished_at": now_iso(),
            "mycelic_version": __version__, "sqlite_version": sqlite3.sqlite_version, "python_version": platform.python_version(),
            "hostname": socket.gethostname(), "service": getattr(settings, "service_name", None),
            "data_dir": str(getattr(settings, "data_dir", "")),
            "schema": next((f["schema"] for f in files if f["kind"] == "coord"), {"current": 0, "applied": []}),
            "files": files,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        tmp_archive = out_path.with_name(out_path.name + ".partial")
        with tarfile.open(tmp_archive, "w:gz") as tar:
            tar.add(stage / "manifest.json", arcname="manifest.json")
            for f in files:
                tar.add(stage / f["path"], arcname=f["path"])
        os.replace(tmp_archive, out_path)        # readers never see a half-written bundle
    manifest["bundle"] = {"path": str(out_path), "bytes": out_path.stat().st_size, "sha256": sha256_file(out_path)}
    return manifest


def read_manifest(in_path: str | Path) -> dict[str, Any]:
    with tarfile.open(in_path, "r:gz") as tar:
        try:
            member = tar.extractfile("manifest.json")
        except KeyError:
            member = None
        if member is None:
            raise BackupError("not a Mycelic backup: manifest.json missing")
        manifest = json.loads(member.read().decode("utf-8"))
    if manifest.get("format") != BUNDLE_FORMAT:
        raise BackupError(f"not a Mycelic backup: format={manifest.get('format')!r}")
    if int(manifest.get("format_version", 0)) > BUNDLE_FORMAT_VERSION:
        raise BackupError(f"bundle format {manifest.get('format_version')} is newer than this code ({BUNDLE_FORMAT_VERSION})")
    return manifest


def _target_for(settings: Any, entry: dict[str, Any]) -> Path:
    """Where a manifest entry lands; the archive path is never trusted for the destination."""
    if entry.get("kind") == "coord":
        return Path(settings.coord_db)
    if entry.get("kind") == "holder":
        hid = str(entry.get("holder_id") or "")
        if not _HOLDER_ID_RE.match(hid):
            raise BackupError(f"bad holder id in manifest: {hid!r}")
        return Path(settings.holders_dir) / hid / HOLDER_DB_NAME
    raise BackupError(f"unknown entry kind in manifest: {entry.get('kind')!r}")


def _remove_sidecars(db_path: Path) -> None:
    """A stale ``-wal``/``-shm`` from the previous database would be replayed into the restored file: corrupt it."""
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()


def restore_bundle(settings: Any, in_path: str | Path, *, force: bool = False, holders: bool = True) -> dict[str, Any]:
    """Restore a bundle into ``settings.data_dir``. Refuses to overwrite existing databases unless ``force``.

    Every file is extracted to a staging directory and its sha256 checked against the manifest *before* any
    target is touched; with ``force`` the existing file is renamed to ``<name>.pre-restore.<timestamp>`` rather
    than deleted. Stop the API, worker and holders first: a process holding the old file would keep writing
    to an unlinked inode and its WAL would be lost.
    """
    in_path = Path(in_path)
    if not in_path.is_file():
        raise BackupError(f"bundle not found: {in_path}")
    manifest = read_manifest(in_path)
    entries = [e for e in manifest.get("files", []) if holders or e.get("kind") != "holder"]
    if not entries:
        raise BackupError("bundle contains no databases")
    targets = {e["path"]: _target_for(settings, e) for e in entries}
    existing = [str(t) for t in targets.values() if t.exists()]
    if existing and not force:
        raise BackupError("refusing to overwrite existing databases (pass --force): " + ", ".join(existing))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    restored: list[dict[str, Any]] = []
    moved_aside: list[str] = []
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mycelic-restore-", dir=str(Path(settings.data_dir))) as tmp:
        stage = Path(tmp)
        with tarfile.open(in_path, "r:gz") as tar:
            for e in entries:
                member = e["path"]
                if member.startswith(("/", "..")) or ".." in Path(member).parts:
                    raise BackupError(f"unsafe path in bundle: {member}")
                try:
                    src = tar.extractfile(member)
                except KeyError:
                    src = None
                if src is None:
                    raise BackupError(f"bundle is missing {member}")
                dst = stage / member
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(dst, "wb") as f:
                    shutil.copyfileobj(src, f)
                digest = sha256_file(dst)
                if digest != e.get("sha256"):
                    raise BackupError(f"checksum mismatch for {member}: bundle says {e.get('sha256')}, file is {digest}")
                conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
                try:
                    verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
                finally:
                    conn.close()
                if verdict != "ok":
                    raise BackupError(f"{member} failed quick_check: {verdict}")
        # Everything verified: now swap the files in.
        for e in entries:
            target = targets[e["path"]]
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                aside = target.with_name(f"{target.name}.pre-restore.{stamp}")
                os.replace(target, aside)
                moved_aside.append(str(aside))
            _remove_sidecars(target)
            shutil.move(str(stage / e["path"]), str(target))
            restored.append({"path": e["path"], "target": str(target), "kind": e["kind"], "holder_id": e.get("holder_id"), "bytes": e.get("bytes")})
    return {"restored": restored, "moved_aside": moved_aside, "manifest": manifest,
            "schema": manifest.get("schema", {}), "created_at": manifest.get("created_at"), "mycelic_version": manifest.get("mycelic_version")}


__all__ = [
    "request_id_var", "bind_request_id", "current_request_id", "clear_request_id", "configure_logging", "JsonFormatter",
    "Metrics", "metrics", "METRIC_NAMES", "METRIC_SPECS", "PROMETHEUS_CONTENT_TYPE",
    "ErrorReporter",
    "BackupError", "sqlite_backup", "backup_bundle", "restore_bundle", "read_manifest", "find_holder_dbs", "sha256_file",
]

"""Tests for mycelic.observability: JSON logs, Prometheus rendering, error reports, backup/restore.

Offline and deterministic. The webhook test runs a throwaway aiohttp server on a loopback port.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3
import tarfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from mycelic import __version__
from mycelic.config import Settings
from mycelic.db import CoordDB
from mycelic.observability import (
    METRIC_NAMES, BackupError, ErrorReporter, Metrics, backup_bundle, bind_request_id, clear_request_id,
    configure_logging, read_manifest, restore_bundle, sqlite_backup,
)


def _settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings(data_dir=tmp_path / "data")
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------- logging

def test_json_log_line_has_documented_fields(tmp_path: Path) -> None:
    buf = io.StringIO()
    handler = configure_logging(_settings(tmp_path, log_json=True, log_level="INFO", service_name="worker"), stream=buf)
    try:
        rid = bind_request_id()
        logging.getLogger("mycelic.test").info("hello %s", "world", extra={"goal_id": "goal_1", "obj": object()})
        clear_request_id()
        logging.getLogger("mycelic.test").warning("no request")
    finally:
        logging.getLogger().removeHandler(handler)
    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    first, second = lines
    assert first["msg"] == "hello world"
    assert first["level"] == "INFO"
    assert first["logger"] == "mycelic.test"
    assert first["service"] == "worker"
    assert first["request_id"] == rid and rid.startswith("req_")
    assert first["goal_id"] == "goal_1"
    assert isinstance(first["obj"], str)                     # non-JSON extras are repr()'d, never break the line
    assert first["ts"].endswith("Z") and "T" in first["ts"]
    assert "request_id" not in second and second["level"] == "WARNING"


def test_plain_format_when_json_disabled(tmp_path: Path) -> None:
    buf = io.StringIO()
    handler = configure_logging(_settings(tmp_path, log_json=False, service_name="api"), stream=buf)
    try:
        logging.getLogger("mycelic.plain").error("boom")
    finally:
        logging.getLogger().removeHandler(handler)
    text = buf.getvalue()
    assert "ERROR api mycelic.plain [-] boom" in text
    with pytest.raises(ValueError):
        json.loads(text)


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    root = logging.getLogger()
    before = len(root.handlers)
    h1 = configure_logging(_settings(tmp_path), stream=io.StringIO())
    h2 = configure_logging(_settings(tmp_path), stream=io.StringIO())
    try:
        assert h1 not in root.handlers and h2 in root.handlers
        assert len(root.handlers) == before + 1
    finally:
        root.removeHandler(h2)


# ---------------------------------------------------------------------- metrics

def test_prometheus_render_lists_every_documented_metric() -> None:
    m = Metrics()
    text = m.render_prometheus()
    for name in METRIC_NAMES:
        assert f"# TYPE {name} " in text, name
    assert "mycelic_jobs_backlog 0" in text                  # unlabelled families always have a sample


def test_prometheus_render_counters_gauges_histograms() -> None:
    m = Metrics()
    m.inc("mycelic_http_requests_total", method="GET", route="/api/goals", status=200)
    m.inc("mycelic_http_requests_total", method="GET", route="/api/goals", status=200)
    m.inc("mycelic_model_cost_usd_total", 0.0125, provider="anthropic", model="m", tier="light")
    m.set("mycelic_claims", 3, status="supported")
    m.set("mycelic_worker_heartbeat_age_seconds", 4.5)
    m.observe("mycelic_http_request_seconds", 0.02, method="GET", route="/api/goals")
    m.observe("mycelic_http_request_seconds", 3.0, method="GET", route="/api/goals")
    with m.timer("mycelic_http_request_seconds", method="POST", route="/api/questions"):
        pass
    text = m.render_prometheus()
    assert 'mycelic_http_requests_total{method="GET",route="/api/goals",status="200"} 2' in text
    assert 'mycelic_model_cost_usd_total{model="m",provider="anthropic",tier="light"} 0.0125' in text
    assert 'mycelic_claims{status="supported"} 3' in text
    assert "mycelic_worker_heartbeat_age_seconds 4.5" in text
    assert 'mycelic_http_request_seconds_bucket{method="GET",route="/api/goals",le="0.025"} 1' in text
    assert 'mycelic_http_request_seconds_bucket{method="GET",route="/api/goals",le="+Inf"} 2' in text
    assert 'mycelic_http_request_seconds_sum{method="GET",route="/api/goals"} 3.02' in text
    assert 'mycelic_http_request_seconds_count{method="GET",route="/api/goals"} 2' in text
    assert m.get("mycelic_http_request_seconds", method="POST", route="/api/questions") == 1
    assert m.get("mycelic_http_requests_total", method="GET", route="/api/goals", status=200) == 2


def test_collectors_run_at_render_and_failures_are_contained() -> None:
    m = Metrics()

    def fill(reg: Metrics) -> None:
        reg.set("mycelic_jobs_backlog", 7)

    def broken(reg: Metrics) -> None:
        raise RuntimeError("db gone")

    m.add_collector(fill)
    m.add_collector(broken)
    text = m.render_prometheus()
    assert "mycelic_jobs_backlog 7" in text


# ---------------------------------------------------------------------- error reports

async def test_error_report_row_written(db: CoordDB, tmp_path: Path) -> None:
    rep = ErrorReporter(db, _settings(tmp_path, error_webhook=""))
    rid = bind_request_id("req_test")
    try:
        row_id = await rep.report("api", "something failed", detail={"path": "/api/goals", "n": 1}, tenant_id="ten_1")
    finally:
        clear_request_id()
    assert row_id is not None
    row = db.one("SELECT * FROM error_reports WHERE id=?", (row_id,))
    assert row["service"] == "api" and row["level"] == "error" and row["message"] == "something failed"
    assert json.loads(row["detail"]) == {"path": "/api/goals", "n": 1}
    assert row["request_id"] == rid and row["tenant_id"] == "ten_1"
    assert rep.recent(limit=5)[0]["detail"] == {"path": "/api/goals", "n": 1}


async def test_error_report_posts_webhook_and_never_raises(db: CoordDB, tmp_path: Path) -> None:
    received: list[dict] = []

    async def hook(request: web.Request) -> web.Response:
        received.append(await request.json())
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_post("/hook", hook)
    async with TestServer(app) as server:
        rep = ErrorReporter(db, _settings(tmp_path, error_webhook=str(server.make_url("/hook"))))
        await rep.report("worker", "job died", level="critical", detail={"job_id": 9})
        await rep.close()
    assert len(received) == 1
    assert received[0]["service"] == "worker" and received[0]["level"] == "critical"
    assert received[0]["detail"] == {"job_id": 9} and received[0]["version"] == __version__

    # unreachable webhook: the row is still written and nothing propagates
    rep2 = ErrorReporter(db, _settings(tmp_path, error_webhook="http://127.0.0.1:9/hook"))
    row_id = await rep2.report("api", "still recorded")
    await rep2.close()
    assert row_id is not None and db.one("SELECT message FROM error_reports WHERE id=?", (row_id,))["message"] == "still recorded"


async def test_error_report_without_db(tmp_path: Path) -> None:
    rep = ErrorReporter(None, _settings(tmp_path))
    assert await rep.report("holder", "no coord db here") is None
    assert rep.recent() == []


# ---------------------------------------------------------------------- backup / restore

def _make_holder_db(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
    conn.executemany("INSERT INTO memories(text) VALUES (?)", [(r,) for r in rows])
    conn.commit()
    # leave the connection open: the WAL is not checkpointed, which is exactly the live-database case
    _open_conns.append(conn)


_open_conns: list[sqlite3.Connection] = []


def _rows(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT text FROM memories ORDER BY id")]
    finally:
        conn.close()


def test_sqlite_backup_copies_live_wal_database(tmp_path: Path) -> None:
    src = tmp_path / "live.db"
    _make_holder_db(src, ["a", "b"])
    info = sqlite_backup(src, tmp_path / "copy" / "live.db")
    assert info["bytes"] > 0 and len(info["sha256"]) == 64 and info["pages"] >= 1
    assert _rows(tmp_path / "copy" / "live.db") == ["a", "b"]
    assert not (tmp_path / "copy" / "live.db-wal").exists()


async def test_backup_restore_round_trip(tmp_path: Path) -> None:
    src_settings = _settings(tmp_path / "src")
    db = CoordDB(src_settings.coord_db)
    async with db.tx() as c:
        c.execute("INSERT INTO tenants(tenant_id, slug, name, created_at) VALUES ('ten_1', 'acme', 'Acme', '2026-01-01T00:00:00+00:00')")
    _make_holder_db(Path(src_settings.holders_dir) / "hold_a" / "evidence.db", ["alpha", "beta"])
    _make_holder_db(Path(src_settings.holders_dir) / "hold_b" / "evidence.db", ["gamma"])
    (Path(src_settings.holders_dir) / "not-a-holder").mkdir()          # ignored: no evidence.db inside

    bundle = tmp_path / "backups" / "mycelic.tar.gz"
    manifest = backup_bundle(src_settings, bundle)
    assert bundle.is_file() and not bundle.with_name(bundle.name + ".partial").exists()
    assert manifest["format"] == "mycelic-backup" and manifest["mycelic_version"] == __version__
    assert manifest["schema"]["current"] >= 1
    assert [f["path"] for f in manifest["files"]] == ["coord.db", "holders/hold_a/evidence.db", "holders/hold_b/evidence.db"]
    with tarfile.open(bundle, "r:gz") as tar:
        assert tar.getnames()[0] == "manifest.json"
        assert set(tar.getnames()) == {"manifest.json", "coord.db", "holders/hold_a/evidence.db", "holders/hold_b/evidence.db"}
    assert read_manifest(bundle)["files"][1]["holder_id"] == "hold_a"

    dst_settings = _settings(tmp_path / "dst")
    result = restore_bundle(dst_settings, bundle)
    assert [r["kind"] for r in result["restored"]] == ["coord", "holder", "holder"] and result["moved_aside"] == []
    restored_db = CoordDB(dst_settings.coord_db)
    assert restored_db.scalar("SELECT name FROM tenants WHERE tenant_id='ten_1'") == "Acme"
    assert _rows(Path(dst_settings.holders_dir) / "hold_a" / "evidence.db") == ["alpha", "beta"]
    assert _rows(Path(dst_settings.holders_dir) / "hold_b" / "evidence.db") == ["gamma"]

    # second restore refuses to overwrite ...
    with pytest.raises(BackupError, match="--force"):
        restore_bundle(dst_settings, bundle)
    # ... unless forced, and then the previous files are kept beside the restored ones
    await restored_db.close()
    forced = restore_bundle(dst_settings, bundle, force=True)
    assert len(forced["moved_aside"]) == 3 and all(".pre-restore." in p for p in forced["moved_aside"])
    assert _rows(Path(dst_settings.holders_dir) / "hold_b" / "evidence.db") == ["gamma"]
    await db.close()


def test_restore_rejects_tampered_bundle(tmp_path: Path) -> None:
    src_settings = _settings(tmp_path / "src")
    CoordDB(src_settings.coord_db)
    _make_holder_db(Path(src_settings.holders_dir) / "hold_a" / "evidence.db", ["x"])
    bundle = tmp_path / "good.tar.gz"
    manifest = backup_bundle(src_settings, bundle)

    # rebuild the archive with a different holder file but the original manifest
    _make_holder_db(tmp_path / "other.db", ["tampered"])
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bundle, "r:gz") as src, tarfile.open(bad, "w:gz") as out:
        for member in src.getmembers():
            if member.name == "holders/hold_a/evidence.db":
                out.add(tmp_path / "other.db", arcname=member.name)
            else:
                out.addfile(member, src.extractfile(member))
    dst_settings = _settings(tmp_path / "dst")
    with pytest.raises(BackupError, match="checksum mismatch"):
        restore_bundle(dst_settings, bad)
    assert not Path(dst_settings.coord_db).exists()          # nothing was written before verification failed
    assert manifest["files"][1]["holder_id"] == "hold_a"


def test_read_manifest_rejects_foreign_archive(tmp_path: Path) -> None:
    other = tmp_path / "other.tar.gz"
    (tmp_path / "x.txt").write_text("hi")
    with tarfile.open(other, "w:gz") as tar:
        tar.add(tmp_path / "x.txt", arcname="x.txt")
    with pytest.raises(BackupError, match="manifest.json missing"):
        read_manifest(other)


def test_backup_without_coord_db_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="coordination database not found"):
        backup_bundle(_settings(tmp_path / "empty"), tmp_path / "b.tar.gz")

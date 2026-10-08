"""Shared helpers for the provider connector suites (``test_connector_*``): a holder store and pipeline wired to an offline
GitHub, Slack, Gmail or Google Drive mock through a loopback-only HTTP factory and a fake clock. Not a test module itself.

Everything is offline and deterministic: the mocks listen on 127.0.0.1, credentials and secrets are dummy fixture values,
time is a :class:`FakeClock` (HTTP back-off and rate-limit waits advance it instead of sleeping).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mycelic.ingest.contract import Credentials, Secret
from mycelic.ingest.crypto import TokenVault
from mycelic.ingest.mocks import (FakeClock, load_fixture, loopback_http_factory, start_drive_mock, start_github_mock, start_gmail_mock,
                                  start_slack_mock)
from mycelic.ingest.service import IngestService

from .ingest_support import OWNER, make_pipeline, make_store

VAULT_SECRET = "connector-tests-master-key-0001"
GH_TOKEN = "gh-mock-ana-fine-grained"
SL_TOKEN = "xoxp-mock-ana-user-token"
BEN, CY, DEE = "usr_ben", "usr_cy", "usr_dee"
SLACK_PRINCIPALS = {"U0ANA": OWNER, "U0BEN": BEN, "U0CY": CY, "U0DEE": DEE}
GITHUB_PRINCIPALS = {"1001": OWNER, "1002": BEN, "1003": CY, "1004": DEE}
EMAIL_PRINCIPALS = {"ana@acme.example": OWNER, "ben@acme.example": BEN, "cy@acme.example": CY, "dee@acme.example": DEE}
GM_TOKEN = "ya29.mock-ana-gmail-readonly"
GD_TOKEN = "ya29.mock-ana-drive-readonly"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
CUSTOMER_NOTES = "1CustomerNotesFolderAna01"
FIXTURES = {"github": ("github_acme", start_github_mock), "slack": ("slack_acme", start_slack_mock), "gmail": ("gmail_acme", start_gmail_mock),
            "drive": ("drive_acme", start_drive_mock)}


def creds(token: str, *, kind: str = "pat", refresh: str | None = None, scope: str | None = None) -> Credentials:
    return Credentials(kind=kind, access_token=Secret(token), refresh_token=Secret(refresh) if refresh else None,
                       extra={"scope": scope} if scope else {})


def google_creds(token: str, scope: str | None, *, refresh: str | None = None) -> Credentials:
    """An OAuth access token as Google issues it (with the granted ``scope``); ``scope=None`` is a token entered without it."""
    return creds(token, kind="oauth2", refresh=refresh, scope=scope)


@dataclass
class Env:
    clock: FakeClock
    mock: Any
    url: str
    store: Any
    pipe: Any
    svc: IngestService
    fixture: dict[str, Any]
    con: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def cid(self) -> str:
        assert self.con is not None
        return self.con["connector_id"]

    def records(self, *where: str) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ingest_records" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY source_object_type, source_object_id"
        return [dict(r) for r in self.store.store._conn.execute(sql).fetchall()]

    def live_ids(self, object_type: str | None = None) -> list[str]:
        rows = self.records("deletion_status='live'")
        return sorted(r["source_object_id"] for r in rows if object_type is None or r["source_object_type"] == object_type)

    def source(self, external_id: str):
        return next(s for s in self.svc.sources(self.cid) if s.external_id == external_id)

    def connector_row(self) -> dict[str, Any]:
        return self.pipe.db.get_connector(self.cid)

    async def close(self) -> None:
        await self.pipe.aclose()
        await self.store.close()
        await self.mock.close()


async def _env(tmp_path: Path, kind: str, *, fixture: dict[str, Any] | None, latency: float, http_kw: dict[str, Any] | None,
               pipe_kw: dict[str, Any]) -> Env:
    name, start = FIXTURES[kind]
    fx = fixture or load_fixture(name)
    clock = FakeClock(fx["now"])
    url, mock = await start(fx, clock=clock, latency=latency)
    store = make_store(tmp_path, f"{kind}_holder")
    pipe = make_pipeline(store, vault=TokenVault.from_secret(VAULT_SECRET), http_factory=loopback_http_factory(clock, **(http_kw or {})),
                         clock=clock.datetime, **pipe_kw)
    return Env(clock=clock, mock=mock, url=url, store=store, pipe=pipe, svc=IngestService(pipe), fixture=fx)


async def github_env(tmp_path: Path, *, token: str | None = GH_TOKEN, config: dict[str, Any] | None = None, include: bool | list[str] = True,
                     fixture: dict[str, Any] | None = None, latency: float = 0.0, http_kw: dict[str, Any] | None = None, **pipe_kw: Any) -> Env:
    env = await _env(tmp_path, "github", fixture=fixture, latency=latency, http_kw=http_kw, pipe_kw=pipe_kw)
    if token is not None:
        cfg = {"api_base": env.url, "principal_map": GITHUB_PRINCIPALS, **(config or {})}
        env.con = await env.svc.add_connector("github", created_by=OWNER, config=cfg, credentials=creds(token))
        await env.svc.discover_sources(env.cid)
        await _include(env, include)
    return env


async def slack_env(tmp_path: Path, *, token: str | None = SL_TOKEN, config: dict[str, Any] | None = None, include: bool | list[str] = True,
                    fixture: dict[str, Any] | None = None, latency: float = 0.0, http_kw: dict[str, Any] | None = None, **pipe_kw: Any) -> Env:
    env = await _env(tmp_path, "slack", fixture=fixture, latency=latency, http_kw=http_kw, pipe_kw=pipe_kw)
    if token is not None:
        cfg = {"api_base": env.url + "/api", "slack_app_class": "internal", "principal_map": SLACK_PRINCIPALS, **(config or {})}
        env.con = await env.svc.add_connector("slack", created_by=OWNER, config=cfg, credentials=creds(token))
        await env.svc.discover_sources(env.cid)
        await _include(env, include)
    return env


async def gmail_env(tmp_path: Path, *, token: str | None = GM_TOKEN, scope: str | None = GMAIL_SCOPE, config: dict[str, Any] | None = None,
                    include: bool | list[str] = ("Customers", "GitHub"), fixture: dict[str, Any] | None = None, latency: float = 0.0,  # type: ignore[assignment]
                    http_kw: dict[str, Any] | None = None, **pipe_kw: Any) -> Env:
    env = await _env(tmp_path, "gmail", fixture=fixture, latency=latency, http_kw=http_kw, pipe_kw=pipe_kw)
    if token is not None:
        cfg = {"api_base": env.url, "principal_map": EMAIL_PRINCIPALS, **(config or {})}
        env.con = await env.svc.add_connector("gmail", created_by=OWNER, config=cfg, credentials=google_creds(token, scope))
        pin_anchor(env)
        await env.svc.discover_sources(env.cid)
        await _include(env, list(include) if isinstance(include, tuple) else include)
    return env


async def drive_env(tmp_path: Path, *, token: str | None = GD_TOKEN, scope: str | None = DRIVE_SCOPE, config: dict[str, Any] | None = None,
                    include: bool | list[str] = ("Engineering", "Customer notes"), fixture: dict[str, Any] | None = None,  # type: ignore[assignment]
                    latency: float = 0.0, http_kw: dict[str, Any] | None = None, **pipe_kw: Any) -> Env:
    env = await _env(tmp_path, "drive", fixture=fixture, latency=latency, http_kw=http_kw, pipe_kw=pipe_kw)
    if token is not None:
        cfg = {"api_base": env.url, "principal_map": EMAIL_PRINCIPALS, "folder_ids": [CUSTOMER_NOTES], **(config or {})}
        env.con = await env.svc.add_connector("google_drive", created_by=OWNER, config=cfg, credentials=google_creds(token, scope))
        pin_anchor(env)
        await env.svc.discover_sources(env.cid)
        await _include(env, list(include) if isinstance(include, tuple) else include)
    return env


def pin_anchor(env: Env) -> None:
    """Backfill windows are anchored at the connection's ``created_at`` (wall-clock time); pin it to the fake clock so the
    fixture's history falls in the same windows whenever the suite runs."""
    env.store.store._conn.execute("UPDATE connectors SET created_at=? WHERE connector_id=?",
                                  (env.clock.datetime().isoformat(timespec="seconds"), env.cid))


async def _include(env: Env, include: bool | list[str]) -> None:
    if include is False:
        return
    for s in env.svc.sources(env.cid):
        wanted = (include is True and s.selection != "excluded") or (include is not True and (s.external_id in include or s.name in include))
        if wanted and s.selection != "included" and s.source_type not in ("dm", "mpim"):
            await env.svc.set_source(s.source_id, actor=OWNER, selection="included")


async def full_sync(env: Env, *, backfill: bool = True) -> None:
    if backfill:
        await env.pipe.sync(env.cid, mode="backfill")
    await env.pipe.sync(env.cid)
    await env.pipe.process_available()


class Collect(logging.Handler):
    """Captures every record of the process (any logger) at DEBUG, formatted, for no-content-in-logs checks."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage() + " " + " ".join(f"{k}={v}" for k, v in record.__dict__.items() if k not in _STD))
        except Exception:
            self.lines.append(str(record.msg))


_STD = set(vars(logging.LogRecord("x", 0, "", 0, "", (), None))) | {"message", "asctime"}

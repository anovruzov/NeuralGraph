"""Offline provider mocks for tests and demos (INGESTION.md §11.4): faithful GitHub, Slack, Gmail and Google Drive servers on
loopback, with fictional fixtures (``mocks/fixtures``), fault injection, signed or tokened push deliveries and mock OAuth
endpoints.

Typical use (also what the cross-app demo needs)::

    from mycelic.ingest.mocks import (FakeClock, load_fixture, loopback_http_factory, start_drive_mock, start_github_mock,
                                      start_gmail_mock, start_slack_mock)

    gh_fx, sl_fx = load_fixture("github_acme"), load_fixture("slack_acme")
    clock = FakeClock(gh_fx["now"])                         # or real time: load_fixture(..., now=datetime.now(timezone.utc))
    gh_url, gh = await start_github_mock(gh_fx, clock=clock)
    sl_url, sl = await start_slack_mock(sl_fx, clock=clock)
    gm_url, gm = await start_gmail_mock(load_fixture("gmail_acme"), clock=clock)   # Gmail API + Pub/Sub push deliveries
    gd_url, gd = await start_drive_mock(load_fixture("drive_acme"), clock=clock)   # Drive API + changes.watch notifications
    pipe = IngestPipeline(store, http_factory=loopback_http_factory(clock), clock=clock.datetime, vault=vault, ...)

Never imported by production code.
"""
from __future__ import annotations

from .common import FakeClock, MockProvider, RequestRecord, load_fixture, loopback_http_factory, rebase_fixture, token_key
from .drive_mock import DriveMock, start_drive_mock
from .github_mock import GitHubMock, start_github_mock
from .gmail_mock import GmailMock, PushDelivery, start_gmail_mock
from .slack_mock import SlackMock, start_slack_mock

__all__ = ["DriveMock", "FakeClock", "GitHubMock", "GmailMock", "MockProvider", "PushDelivery", "RequestRecord", "SlackMock", "load_fixture",
           "loopback_http_factory", "rebase_fixture", "start_drive_mock", "start_github_mock", "start_gmail_mock", "start_slack_mock", "token_key"]

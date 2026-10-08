"""Offline provider mocks for tests and demos (INGESTION.md §11.4): faithful GitHub and Slack servers on loopback, with
fictional fixtures (``mocks/fixtures``), fault injection, signed webhook/event deliveries and mock OAuth endpoints.

Typical use (also what the cross-app demo needs)::

    from mycelic.ingest.mocks import FakeClock, load_fixture, loopback_http_factory, start_github_mock, start_slack_mock

    gh_fx, sl_fx = load_fixture("github_acme"), load_fixture("slack_acme")
    clock = FakeClock(gh_fx["now"])                         # or real time: load_fixture(..., now=datetime.now(timezone.utc))
    gh_url, gh = await start_github_mock(gh_fx, clock=clock)
    sl_url, sl = await start_slack_mock(sl_fx, clock=clock)
    pipe = IngestPipeline(store, http_factory=loopback_http_factory(clock), clock=clock.datetime, vault=vault, ...)

Never imported by production code.
"""
from __future__ import annotations

from .common import FakeClock, MockProvider, RequestRecord, load_fixture, loopback_http_factory, rebase_fixture, token_key
from .github_mock import GitHubMock, start_github_mock
from .slack_mock import SlackMock, start_slack_mock

__all__ = ["FakeClock", "GitHubMock", "MockProvider", "RequestRecord", "SlackMock", "load_fixture", "loopback_http_factory", "rebase_fixture",
           "start_github_mock", "start_slack_mock", "token_key"]

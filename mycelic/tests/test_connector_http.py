"""The connectors' HTTP client (``mycelic.ingest.http``): egress allow-list, Link parsing, ETag/304, status mapping,
rate-limit waits and parking, retries, serial requests per token, size caps and redaction of logs. Offline: a tiny local
aiohttp app and the GitHub mock on 127.0.0.1; time is a fake clock."""
from __future__ import annotations

import asyncio
import logging

import pytest
from aiohttp import web

from mycelic.ingest.contract import (AuthExpired, Capabilities, ConnectorManifest, Credentials, InsufficientScope, ObjectGone, PermanentError,
                                     RateLimited, RateLimitSpec, Secret, TransientError)
from mycelic.ingest.http import ConnectorHttpClient, check_egress, parse_link_header, path_template
from mycelic.ingest.mocks import FakeClock, load_fixture, start_github_mock

from .connector_support import Collect

TOKEN = "gh-mock-ana-fine-grained"
MANIFEST = ConnectorManifest(connector_type="probe", display_name="Probe", version="0.1.0", status="tested-offline", auth_kinds=("pat",),
                             scopes=(), modes=frozenset({"pull"}), source_types=("x",),
                             capabilities=Capabilities(edits=False, deletes="none", threads=False, attachments=False, acl="none", exports=False),
                             rate_limit=RateLimitSpec(kind="headers", serial_per_token=True), allowed_hosts=("api.example.com",))


class StaticSecrets:
    def __init__(self, token: str | None = TOKEN) -> None:
        self.token = token
        self.gets = 0

    async def get(self) -> Credentials:
        self.gets += 1
        return Credentials(kind="pat", access_token=Secret(self.token) if self.token else None)

    async def replace(self, creds: Credentials) -> None:
        self.token = creds.access_token.reveal() if creds.access_token else None

    def refresh_lock(self) -> asyncio.Lock:
        return asyncio.Lock()


def client(clock: FakeClock | None = None, *, token: str | None = TOKEN, **kw) -> ConnectorHttpClient:
    clock = clock or FakeClock(1_800_000_000)
    return ConnectorHttpClient(MANIFEST, StaticSecrets(token), allow_loopback_http=True, sleep=clock.sleep, clock=clock, **kw)


async def serve(routes: dict[str, object]) -> tuple[str, web.AppRunner, list[web.Request]]:
    seen: list[web.Request] = []
    app = web.Application()
    for path, handler in routes.items():
        async def h(request: web.Request, handler=handler):
            seen.append(request)
            return await handler(request) if asyncio.iscoroutinefunction(handler) else handler(request)
        app.router.add_route("*", path, h)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", runner, seen


# ---------------------------------------------------------------------------------------------- pure helpers
def test_link_header_parsing() -> None:
    header = ('<https://api.github.com/repositories/5001/issues?state=all&page=2>; rel="next", '
              '<https://api.github.com/repositories/5001/issues?state=all&page=5>; rel="last", '
              '<https://api.github.com/repositories/5001/issues?a=1,2&page=1>; rel="first"')
    links = parse_link_header(header)
    assert links["next"].endswith("page=2") and links["last"].endswith("page=5") and links["first"].endswith("a=1,2&page=1")
    assert parse_link_header(None) == {} and parse_link_header("garbage") == {}


def test_path_templates_hide_names_and_ids() -> None:
    assert path_template("/repos/acme/checkout/issues/482") == "/repos/{name}/{name}/issues/{id}"
    assert path_template("/repositories/5001/issues/comments/880101") == "/repositories/{name}/issues/comments/{id}"
    assert path_template("/api/conversations.history") == "/api/conversations.history"
    assert "acme" not in path_template("/orgs/acme/repos") and "Secret" not in path_template("/x/SecretName")


def test_egress_allow_list() -> None:
    hosts = ("api.github.com", "github.com")
    assert check_egress("https://api.github.com/user", hosts) == "api.github.com"
    for bad in ("http://api.github.com/user", "https://evil.example/user", "https://api.github.com.evil.example/",
                "https://user:pw@api.github.com/", "ftp://api.github.com/", "https://127.0.0.1:8443/"):
        with pytest.raises(PermanentError) as e:
            check_egress(bad, hosts)
        assert e.value.code == "egress_denied"
    # plain http only to loopback, and only with the explicit test flag
    with pytest.raises(PermanentError):
        check_egress("http://127.0.0.1:9999/user", hosts)
    assert check_egress("http://127.0.0.1:9999/user", hosts, allow_loopback_http=True) == "127.0.0.1"
    with pytest.raises(PermanentError):
        check_egress("http://10.0.0.5:9999/user", hosts, allow_loopback_http=True)


async def test_refused_egress_never_reads_credentials() -> None:
    secrets = StaticSecrets()
    c = ConnectorHttpClient(MANIFEST, secrets)
    for url in ("https://evil.example/x", "http://127.0.0.1:1/x"):
        with pytest.raises(PermanentError):
            await c.request("GET", url)
    assert secrets.gets == 0
    await c.close()


# ---------------------------------------------------------------------------------------------- status mapping
async def test_status_mapping_retries_and_limits() -> None:
    calls = {"flaky": 0}

    def flaky(_r):
        calls["flaky"] += 1
        return web.json_response({"ok": True}) if calls["flaky"] > 2 else web.Response(status=503)
    routes = {
        "/ok": lambda r: web.json_response({"auth": r.headers.get("Authorization", ""), "ua": r.headers.get("User-Agent", "")}),
        "/401": lambda r: web.json_response({"message": "Bad credentials"}, status=401),
        "/403": lambda r: web.json_response({"message": "Resource not accessible by personal access token"}, status=403),
        "/404": lambda r: web.json_response({"message": "Not Found"}, status=404),
        "/410": lambda r: web.json_response({"message": "Gone"}, status=410),
        "/301": lambda r: web.Response(status=301, headers={"Location": "https://elsewhere.example/moved"}),
        "/422": lambda r: web.json_response({"message": "Validation Failed"}, status=422),
        "/500": lambda r: web.Response(status=500),
        "/flaky": flaky,
        "/big": lambda r: web.Response(body=b"x" * 5000, content_type="application/json"),
        "/badjson": lambda r: web.Response(body=b"{not json", content_type="application/json"),
    }
    url, runner, seen = await serve(routes)
    clock = FakeClock(1_800_000_000)
    c = client(clock)
    try:
        ok = await c.request("GET", url + "/ok")
        assert ok.status == 200 and ok.json["auth"] == f"Bearer {TOKEN}" and ok.json["ua"].startswith("mycelic-ingest/")
        with pytest.raises(AuthExpired):
            await c.request("GET", url + "/401")
        with pytest.raises(InsufficientScope):
            await c.request("GET", url + "/403")
        for path, status in (("/404", 404), ("/410", 410)):
            with pytest.raises(ObjectGone) as e:
                await c.request("GET", url + path)
            assert e.value.status == status
        with pytest.raises(ObjectGone) as e:
            await c.request("GET", url + "/301")
        assert e.value.status == 301 and e.value.moved_to == "https://elsewhere.example/moved"   # never followed
        assert (await c.request("GET", url + "/301", expected=(301,))).status == 301
        with pytest.raises(PermanentError) as e:
            await c.request("GET", url + "/422")
        assert e.value.code == "http_error"
        n = len(seen)
        with pytest.raises(TransientError):
            await c.request("GET", url + "/500")
        assert len(seen) - n == 4                                         # one try + three retries
        assert (await c.request("GET", url + "/flaky")).json == {"ok": True} and calls["flaky"] == 3
        assert clock.slept and all(s < 5 for s in clock.slept)            # backoff waited on the fake clock
        with pytest.raises(PermanentError) as e:
            await c.request("GET", url + "/big", max_bytes=1000)
        assert e.value.code == "response_too_large"
        with pytest.raises(PermanentError) as e:
            await c.request("GET", url + "/badjson")
        assert e.value.code == "bad_json"
        with pytest.raises(AuthExpired):
            await client(clock, token=None).request("GET", url + "/ok")
    finally:
        await c.close()
        await runner.cleanup()


async def test_rate_limit_waits_inline_or_parks() -> None:
    state = {"n": 0}

    def limited(r):
        state["n"] += 1
        if state["n"] == 1:
            return web.json_response({"message": "slow down"}, status=429, headers={"Retry-After": "2"})
        return web.json_response({"ok": True})

    def long_limit(_r):
        return web.json_response({"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": "120"})

    def primary_zero(_r):
        return web.json_response({"message": "API rate limit exceeded for user ID 1001."}, status=403,
                                 headers={"x-ratelimit-limit": "5000", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000600"})
    url, runner, _ = await serve({"/limited": limited, "/long": long_limit, "/primary": primary_zero})
    clock = FakeClock(1_800_000_000)
    c = client(clock)
    try:
        assert (await c.request("GET", url + "/limited")).json == {"ok": True}
        assert clock.slept == [2.0]                                      # Retry-After honoured inline
        with pytest.raises(RateLimited) as e:
            await c.request("GET", url + "/long")
        assert e.value.retry_after == 120 and e.value.scope == "endpoint"
        with pytest.raises(RateLimited) as e:
            await c.request("GET", url + "/primary")
        assert e.value.retry_after == pytest.approx(1800000600 - clock() + 1) and e.value.scope == "token" and e.value.reset_at == 1800000600
        # the client remembers the exhausted budget: the next request parks without calling the provider
        with pytest.raises(RateLimited):
            await c.request("GET", url + "/limited")
        assert state["n"] == 2
    finally:
        await c.close()
        await runner.cleanup()


# ---------------------------------------------------------------------------------------------- against the GitHub mock
async def test_etag_304_and_rate_headers_against_the_mock() -> None:
    fx = load_fixture("github_acme")
    clock = FakeClock(fx["now"])
    url, gh = await start_github_mock(fx, clock=clock)
    c = client(clock)
    try:
        first = await c.request("GET", url + "/repos/acme/checkout/issues", params={"state": "all"}, etag_key="k")
        assert first.status == 200 and first.headers["etag"].startswith('W/"') and isinstance(first.json, list)
        remaining = c.rate["remaining"]
        again = await c.request("GET", url + "/repos/acme/checkout/issues", params={"state": "all"}, etag_key="k")
        assert again.not_modified and again.status == 304 and again.body == b"" and again.json is None
        assert gh.requests[-1].if_none_match and c.rate["remaining"] == remaining      # a 304 costs no primary budget
        other = await c.request("GET", url + "/repos/acme/checkout/issues", params={"state": "open"}, etag_key="k")
        assert other.status == 200                                                     # another URL never reuses the ETag
        plain = await c.request("GET", url + "/repos/acme/checkout/issues", params={"state": "all"})
        assert plain.status == 200 and not gh.requests[-1].if_none_match
        assert c.rate["limit"] == 5000 and c.rate["resource"] == "core"
        # paged walk through absolute Link URLs (GitHub points them at /repositories/{id})
        page = await c.request("GET", url + "/repos/acme/checkout/issues", params={"state": "all", "per_page": 2})
        assert "/repositories/5001/issues" in page.links["next"] and "page=2" in page.links["next"]
    finally:
        await c.close()
        await gh.close()


async def test_transient_failures_are_retried_then_surface() -> None:
    fx = load_fixture("github_acme")
    clock = FakeClock(fx["now"])
    url, gh = await start_github_mock(fx, clock=clock)
    c = client(clock)
    try:
        gh.fail_next(2, status=502)
        assert (await c.request("GET", url + "/user")).json["login"] == "ana"
        gh.fail_next(10, status=503)
        with pytest.raises(TransientError):
            await c.request("GET", url + "/user")
        gh.clear_faults()
        gh.crash_after_pages(0)                           # from now on, connections are dropped without an HTTP response
        with pytest.raises(TransientError) as e:
            await c.request("GET", url + "/user")
        assert e.value.detail["error"]
        gh.recover()
        assert (await c.request("GET", url + "/user")).status == 200
    finally:
        await c.close()
        await gh.close()


async def test_requests_are_serial_per_token() -> None:
    fx = load_fixture("github_acme")
    url, gh = await start_github_mock(fx, latency=0.01)
    serial = ConnectorHttpClient(MANIFEST, StaticSecrets(TOKEN), allow_loopback_http=True)
    other_client = ConnectorHttpClient(MANIFEST, StaticSecrets(TOKEN), allow_loopback_http=True)    # same credential, other connection
    parallel = ConnectorHttpClient(MANIFEST, StaticSecrets("gh-mock-ben-classic-repo"), allow_loopback_http=True, serial_per_token=False)
    try:
        await asyncio.gather(*[serial.request("GET", url + "/user") for _ in range(4)], *[other_client.request("GET", url + "/user") for _ in range(4)])
        assert gh.max_concurrency(TOKEN) == 1                      # one in-flight request per credential, across clients
        await asyncio.gather(*[parallel.request("GET", url + "/user") for _ in range(6)])
        assert gh.max_concurrency("gh-mock-ben-classic-repo") > 1  # the control: without the option requests overlap
    finally:
        for c in (serial, other_client, parallel):
            await c.close()
        await gh.close()


async def test_logs_carry_no_tokens_bodies_or_names() -> None:
    fx = load_fixture("github_acme")
    clock = FakeClock(fx["now"])
    url, gh = await start_github_mock(fx, clock=clock)
    c = client(clock)
    handler = Collect()
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        await c.request("GET", url + "/repos/acme/checkout/issues", params={"state": "all", "since": "2026-01-01T00:00:00Z"})
        with pytest.raises(ObjectGone):
            await c.request("GET", url + "/repos/acme/checkout/issues/475")
        gh.fail_next(5)
        with pytest.raises(TransientError):
            await c.request("GET", url + "/repos/acme/checkout/issues/482")
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
        await c.close()
        await gh.close()
    blob = "\n".join(handler.lines)
    assert "/repos/{name}/{name}/issues" in blob                      # requests are logged ...
    for needle in (TOKEN, "httpclient", "timeout regression", "acme/checkout", "checkout", "since=", "2026-01-01"):
        assert needle not in blob, needle                              # ... without tokens, bodies, names or query strings

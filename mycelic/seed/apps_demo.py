"""Demonstration apps: simulated GitHub, Slack, Google Drive and Gmail for the running demo (opt-in, demonstration mode only).

With ``MYCELIC_DEMO_APPS=1`` and demonstration mode on, the API process hosts the offline GitHub, Slack, Gmail and Drive
mocks on fixed loopback ports (``MYCELIC_DEMO_APPS_PORTS``, default ``18781,18782,18783,18784``) and lets its embedded
holders reach them. Once the demo organization exists it gives the Platform team a unit memory with GitHub, Slack and
the Engineering shared drive connected organization-wide by the team's lead, and connects Ana's mailbox (two labels) to
her personal memory, all with fictional data (the story of scenario check m and the connector tests). The connections
are named "simulated demo data" everywhere: nothing here is, or is presented as, a live integration.

Why in-process: the connectors run inside the holder, and the holder must reach the provider. Hosting the mocks next to
the embedded holders keeps the demonstration self-contained (no network, no credentials). The loopback permission is
set from code here and nowhere else; configuration cannot open it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

PLATFORM_MEMORY = "Platform team memory"
GITHUB_TOKEN = "gh-mock-ana-fine-grained"          # dummy tokens of the fictional fixtures; meaningful to the mocks only
SLACK_TOKEN = "xoxp-mock-ana-user-token"
GMAIL_TOKEN = "ya29.mock-ana-gmail-readonly"
DRIVE_TOKEN = "ya29.mock-ana-drive-readonly"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DEFAULT_PORTS = (18781, 18782, 18783, 18784)
# the fixtures' fictional colleagues as accounts of the demo organization (an administrator's principal mapping)
FIXTURE_PEOPLE = {"ana@acme.example": "ana@meridian.example", "ben@acme.example": "noor@meridian.example",
                  "cy@acme.example": "marcus@meridian.example", "dee@acme.example": "elin@meridian.example"}


def demo_apps_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "demo_mode", False)) and os.environ.get("MYCELIC_DEMO_APPS", "") == "1"


def _ports() -> tuple[int, int, int, int]:
    raw = [p.strip() for p in os.environ.get("MYCELIC_DEMO_APPS_PORTS", "").split(",") if p.strip()]
    try:
        given = [int(x) for x in raw]
    except ValueError:
        given = []
    ports = given + list(DEFAULT_PORTS[len(given):])
    return ports[0], ports[1], ports[2], ports[3]


class DemoApps:
    """Started by the runtime in the process that hosts the embedded holders."""

    def __init__(self, rt: Any, *, interval: float = 10.0) -> None:
        self.rt = rt
        self.interval = interval
        self.github_url = self.slack_url = self.gmail_url = self.drive_url = ""
        self._mocks: list[Any] = []
        self._task: asyncio.Task | None = None
        self._previous_factory: Any = None
        self.connected_holder: str | None = None
        self.mailbox_holder: str | None = None
        self._lock = asyncio.Lock()            # the background loop and an explicit call never connect twice

    async def start(self) -> None:
        from ..ingest.mocks import load_fixture, loopback_http_factory, start_drive_mock, start_github_mock, start_gmail_mock, start_slack_mock
        now = datetime.now(timezone.utc)
        gh_port, sl_port, gm_port, gd_port = _ports()
        self.github_url, gh = await start_github_mock(load_fixture("github_acme", now=now), port=gh_port)
        self.slack_url, sl = await start_slack_mock(load_fixture("slack_acme", now=now), port=sl_port)
        self.gmail_url, gm = await start_gmail_mock(load_fixture("gmail_acme", now=now), port=gm_port)
        self.drive_url, gd = await start_drive_mock(load_fixture("drive_acme", now=now), port=gd_port)
        self._mocks = [gh, sl, gm, gd]
        if self.rt.holders is not None:
            self._previous_factory = self.rt.holders.http_factory
            self.rt.holders.http_factory = loopback_http_factory()
        self._task = asyncio.create_task(self._loop(), name="demo-apps")
        logger.warning("demonstration apps: simulated GitHub at %s, Slack at %s, Gmail at %s and Google Drive at %s "
                       "(fictional data, not live integrations)", self.github_url, self.slack_url, self.gmail_url, self.drive_url)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        for m in self._mocks:
            with contextlib.suppress(Exception):
                await m.close()
        if self.rt.holders is not None:
            self.rt.holders.http_factory = self._previous_factory

    async def _loop(self) -> None:
        while True:
            try:
                if await self.ensure_connected():
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("demonstration apps: connecting the Platform memory failed; retrying")
            await asyncio.sleep(self.interval)

    async def ensure_connected(self) -> bool:
        """Idempotent: the Platform memory has the simulated GitHub, Slack and Drive connected and synced, and Ana's memory
        her simulated mailbox. False until the demo organization has been seeded."""
        async with self._lock:
            return await self._ensure_connected()

    async def _ensure_connected(self) -> bool:
        from .demo import MERIDIAN_SLUG
        rt = self.rt
        tenant = rt.org.get_tenant_by_slug(MERIDIAN_SLUG)
        if tenant is None or rt.holders is None:
            return False
        tid = tenant["tenant_id"]
        platform = next((u for u in rt.org.list_units(tid) if u["name"] == "Platform"), None)
        lead = rt.db.one("SELECT user_id FROM users WHERE tenant_id=? AND email='priya@meridian.example'", (tid,))
        ana = rt.db.scalar("SELECT user_id FROM users WHERE tenant_id=? AND email='ana@meridian.example'", (tid,))
        if platform is None or lead is None:
            return False
        holder = next((h for h in rt.org.list_holders(tid) if h.get("owner_type") == "unit" and h.get("owner_id") == platform["unit_id"]
                       and h.get("name") == PLATFORM_MEMORY and h.get("status") != "revoked"), None)
        if holder is None:
            holder, _key = await rt.org.register_holder(tid, owner_type="unit", owner_id=platform["unit_id"], name=PLATFORM_MEMORY, mode="embedded",
                                                        domains=["engineering", "infrastructure"])
        hid = holder["holder_id"]
        await rt.holders.ensure(hid)
        runtime = rt.holders.ingest(hid)
        if runtime is None:
            return True                      # ingestion disabled: nothing to connect
        users = [u["user_id"] for u in rt.db.all("SELECT user_id FROM users WHERE tenant_id=? ORDER BY email", (tid,))]
        # an administrator's mapping of the acme GitHub organization's members: every account of the demo organization
        gh_map = {"1001": ana, **{str(2000 + i): u for i, u in enumerate(users) if u != ana}}
        people = {fixture: uid for fixture, email in FIXTURE_PEOPLE.items()
                  if (uid := rt.db.scalar("SELECT user_id FROM users WHERE tenant_id=? AND email=?", (tid, email)))}
        # each included source is mapped to a tenant domain, as the lead would do in Connected apps: the mapping is the
        # classifier's strongest signal, and rules or the model add subdomains within it
        plan = [("github", "GitHub (simulated demo data)", {"kind": "pat", "access_token": GITHUB_TOKEN},
                 {"api_base": self.github_url, "auto_include": ["acme/checkout"], "principal_map": gh_map, "acl_members": {"acme/checkout": sorted(gh_map)}},
                 {"*": ["engineering"]}),
                ("slack", "Slack (simulated demo data)", {"kind": "pat", "access_token": SLACK_TOKEN},
                 {"api_base": self.slack_url + "/api", "slack_app_class": "internal", "auto_include": ["#deployments"], "principal_map": {"U0ANA": ana}},
                 {"*": ["infrastructure.ci-cd"]}),
                ("google_drive", "Google Drive (simulated demo data)", {"kind": "oauth2", "access_token": DRIVE_TOKEN, "extra": {"scope": DRIVE_SCOPE}},
                 {"api_base": self.drive_url, "auto_include": ["Engineering"], "principal_map": people},
                 {"*": ["engineering"]})]
        await self._connect(runtime, rt.org.get_holder(hid), lead["user_id"], plan, scope="organization")
        self.connected_holder = hid
        # Ana's mailbox feeds her personal memory only (Gmail connects personal holders, never a team's)
        mine = next((h for h in rt.org.list_holders(tid) if h.get("owner_type") == "user" and h.get("owner_id") == ana
                     and h.get("mode") == "embedded" and h.get("status") != "revoked"), None) if ana else None
        if mine is not None:
            await rt.holders.ensure(mine["holder_id"])
            mailbox = rt.holders.ingest(mine["holder_id"])
            if mailbox is not None:
                await self._connect(mailbox, rt.org.get_holder(mine["holder_id"]), ana, [
                    ("gmail", "Gmail (simulated demo data)", {"kind": "oauth2", "access_token": GMAIL_TOKEN, "extra": {"scope": GMAIL_SCOPE}},
                     {"api_base": self.gmail_url, "auto_include": ["Customers", "GitHub"], "principal_map": people},
                     {"Customers": ["sales.renewals"], "GitHub": ["engineering"]})], scope="personal")
                self.mailbox_holder = mine["holder_id"]
        return True

    async def _connect(self, runtime: Any, h: dict[str, Any], actor: str, plan: list[tuple[Any, ...]], *, scope: str) -> None:
        """Connect each planned app the holder does not have yet: add, map included sources to domains, backfill, then
        incremental, and mirror the connection for the coordinator's views."""
        from ..api.routes_integrations import mirror_connector
        rt = self.rt
        existing = {c["connector_type"] for c in runtime.pipeline.db.list_connectors() if c["status"] != "disconnected"}
        for ctype, name, creds, cfg, source_domains in plan:
            if ctype in existing:
                continue
            out = await runtime.control("connector.add", {"connector_type": ctype, "display_name": name, "config": cfg, "discover": True},
                                        actor=actor, credentials=creds)
            con = out["connector"]
            included = (await runtime.control("sources.list", {"connector_id": con["connector_id"], "selection": "included"}, actor=actor))["items"]
            changes = [{"source_id": src["source_id"], "default_domain_ids": source_domains.get(src.get("name") or "", source_domains.get("*"))}
                       for src in included]
            changes = [c for c in changes if c["default_domain_ids"]]
            if changes:
                await runtime.control("sources.update", {"connector_id": con["connector_id"], "changes": changes}, actor=actor)
            for mode in ("backfill", "incremental"):
                await runtime.control("connector.sync", {"connector_id": con["connector_id"], "mode": mode}, actor=actor)
            got = await runtime.control("connector.get", {"connector_id": con["connector_id"]}, actor=actor)
            await mirror_connector(rt, h, got["connector"], scope=scope, actor=rt.authz.principal_for_user(actor))
            await rt.db.audit(h["tenant_id"], "system", "demo-apps", "connector.install", resource_type="holder", resource_id=h["holder_id"],
                              detail={"connector_id": con["connector_id"], "connector_type": ctype, "scope": scope, "simulated": True})

"""Demonstration apps: simulated GitHub and Slack for the running demo (opt-in, demonstration mode only).

With ``MYCELIC_DEMO_APPS=1`` and demonstration mode on, the API process hosts the offline GitHub and Slack mocks on
fixed loopback ports (``MYCELIC_DEMO_APPS_PORTS``, default ``18781,18782``) and lets its embedded holders reach them.
Once the demo organization exists it gives the Platform team a unit memory with both apps connected
organization-wide by the team's lead, with fictional data (the same story as scenario check m). The connections are
named "simulated demo data" everywhere: nothing here is, or is presented as, a live integration.

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


def demo_apps_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "demo_mode", False)) and os.environ.get("MYCELIC_DEMO_APPS", "") == "1"


def _ports() -> tuple[int, int]:
    raw = [p.strip() for p in os.environ.get("MYCELIC_DEMO_APPS_PORTS", "18781,18782").split(",") if p.strip()]
    try:
        return int(raw[0]), int(raw[1])
    except (IndexError, ValueError):
        return 18781, 18782


class DemoApps:
    """Started by the runtime in the process that hosts the embedded holders."""

    def __init__(self, rt: Any, *, interval: float = 10.0) -> None:
        self.rt = rt
        self.interval = interval
        self.github_url = self.slack_url = ""
        self._mocks: list[Any] = []
        self._task: asyncio.Task | None = None
        self._previous_factory: Any = None
        self.connected_holder: str | None = None
        self._lock = asyncio.Lock()            # the background loop and an explicit call never connect twice

    async def start(self) -> None:
        from ..ingest.mocks import load_fixture, loopback_http_factory, start_github_mock, start_slack_mock
        now = datetime.now(timezone.utc)
        gh_port, sl_port = _ports()
        self.github_url, gh = await start_github_mock(load_fixture("github_acme", now=now), port=gh_port)
        self.slack_url, sl = await start_slack_mock(load_fixture("slack_acme", now=now), port=sl_port)
        self._mocks = [gh, sl]
        if self.rt.holders is not None:
            self._previous_factory = self.rt.holders.http_factory
            self.rt.holders.http_factory = loopback_http_factory()
        self._task = asyncio.create_task(self._loop(), name="demo-apps")
        logger.warning("demonstration apps: simulated GitHub at %s and Slack at %s (fictional data, not live integrations)",
                       self.github_url, self.slack_url)

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
        """Idempotent: the Platform memory exists and has both simulated apps connected and synced. False until the
        demo organization has been seeded."""
        async with self._lock:
            return await self._ensure_connected()

    async def _ensure_connected(self) -> bool:
        from ..api.routes_integrations import mirror_connector
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
        existing = {c["connector_type"] for c in runtime.pipeline.db.list_connectors() if c["status"] != "disconnected"}
        users = [u["user_id"] for u in rt.db.all("SELECT user_id FROM users WHERE tenant_id=? ORDER BY email", (tid,))]
        # an administrator's mapping of the acme GitHub organization's members: every account of the demo organization
        gh_map = {"1001": ana, **{str(2000 + i): u for i, u in enumerate(users) if u != ana}}
        # each included source is mapped to a tenant domain, as the lead would do in Connected apps: the mapping is the
        # classifier's strongest signal, and rules or the model add subdomains within it
        plan = [("github", "GitHub (simulated demo data)", GITHUB_TOKEN,
                 {"api_base": self.github_url, "auto_include": ["acme/checkout"], "principal_map": gh_map, "acl_members": {"acme/checkout": sorted(gh_map)}},
                 ["engineering"]),
                ("slack", "Slack (simulated demo data)", SLACK_TOKEN,
                 {"api_base": self.slack_url + "/api", "slack_app_class": "internal", "auto_include": ["#deployments"], "principal_map": {"U0ANA": ana}},
                 ["infrastructure.ci-cd"])]
        h = rt.org.get_holder(hid)
        for ctype, name, token, cfg, source_domains in plan:
            if ctype in existing:
                continue
            out = await runtime.control("connector.add", {"connector_type": ctype, "display_name": name, "config": cfg, "discover": True},
                                        actor=lead["user_id"], credentials={"kind": "pat", "access_token": token})
            con = out["connector"]
            included = (await runtime.control("sources.list", {"connector_id": con["connector_id"], "selection": "included"}, actor=lead["user_id"]))["items"]
            if included:
                await runtime.control("sources.update", {"connector_id": con["connector_id"], "changes": [
                    {"source_id": src["source_id"], "default_domain_ids": source_domains} for src in included]}, actor=lead["user_id"])
            for mode in ("backfill", "incremental"):
                await runtime.control("connector.sync", {"connector_id": con["connector_id"], "mode": mode}, actor=lead["user_id"])
            got = await runtime.control("connector.get", {"connector_id": con["connector_id"]}, actor=lead["user_id"])
            await mirror_connector(rt, h, got["connector"], scope="organization", actor=rt.authz.principal_for_user(lead["user_id"]))
            await rt.db.audit(tid, "system", "demo-apps", "connector.install", resource_type="holder", resource_id=hid,
                              detail={"connector_id": con["connector_id"], "connector_type": ctype, "scope": "organization", "simulated": True})
        self.connected_holder = hid
        return True

"""Shared fixtures for the Mycelic tests: an in-process service with a temporary database."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from mycelic.auth import Principal
from mycelic.config import Settings
from mycelic.metrics import Metrics
from mycelic.service import MycelicService
from mycelic.transport import InProcessTransport

ADMIN_TOKEN = "test-admin-token-0123456789abcdef0123456789"

DEMO_RULE = {
    "rule_id": "component_supply_risk",
    "target_layer": "enterprise",
    "required_slots": ["transport_disruption", "supplier_buffer_low", "demand_commitment"],
    "conclusion": ("Supply risk for {entity}: inbound transport is disrupted ({slot:transport_disruption}); "
                   "the supplier buffer is thin ({slot:supplier_buffer_low}); demand is committed ({slot:demand_commitment})."),
    "topic_prefix": "supply:",
    "min_agents": 3,
    "min_teams": 2,
    "kind": "risk",
}


def settings(tmp: str | Path, **overrides: Any) -> Settings:
    base = dict(host="127.0.0.1", port=0, db_path=str(Path(tmp) / "mycelic.db"), nats_url=None,
                admin_token=ADMIN_TOKEN, rate_limit_rps=0.0, min_support=2, publish_interval_seconds=0.05)
    base.update(overrides)
    s = Settings(**base)
    s.validate()
    return s


class ServiceHarness:
    """One service over the in-process transport, plus helpers to register agents and act as them."""

    def __init__(self, **overrides: Any) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings(self.tmp.name, **overrides)
        self.service = MycelicService(self.settings, transport=InProcessTransport(), metrics=Metrics())
        self.keys: dict[str, str] = {}

    async def start(self) -> "ServiceHarness":
        await self.service.start()
        return self

    async def close(self) -> None:
        await self.service.close()
        self.tmp.cleanup()

    async def register(self, agent_id: str, *, team: str, department: str = "ops", subsidiary: str = "nw-gmbh",
                       region: str = "emea", enterprise: str = "northwind", scopes: list[str] | None = None) -> Principal:
        body = {"enterprise": enterprise, "region": region, "subsidiary": subsidiary, "department": department,
                "team": team, "agent_id": agent_id}
        if scopes is not None:
            body["scopes"] = scopes
        agent, key = await self.service.register_agent(body)
        self.keys[agent_id] = key
        return self.principal(agent_id)

    def principal(self, agent_id: str) -> Principal:
        return self.service.authenticate(f"Bearer {self.keys[agent_id]}")

    @property
    def admin(self) -> Principal:
        return self.service.authenticate(f"Bearer {ADMIN_TOKEN}")

    async def observe(self, agent_id: str, text: str, **fields: Any) -> str:
        m, _ = await self.service.ingest_memory(self.principal(agent_id), {"text": text, **fields})
        return m.memory_id

    async def settle(self, timeout: float = 10.0) -> None:
        assert await self.service.wait_idle(timeout), "service did not become idle"

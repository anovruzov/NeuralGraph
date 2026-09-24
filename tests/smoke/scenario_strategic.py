"""The Northwind strategic scenario: two regions discover the same supply problem independently; HQ knows the
supply base is concentrated and demand is growing.  No region, department or team can see the strategic
conclusion; Mycelic composes it from the regional conclusions (which are themselves composed from agents'
notes) and keeps every hop in the lineage.

    northwind (enterprise)
    ├─ emea / nw-gmbh        ops: logistics, procurement   commercial: field-sales
    ├─ apac / nw-kk          ops: logistics, procurement   commercial: field-sales
    └─ hq   / northwind-hq   strategy: sourcing, analytics

Rules (deploy/mycelic/rules.json): ``regional_supply_risk`` fires at the region layer and emits the slot
``supply_risk``; ``strategic_second_source`` fires at the enterprise once ``supply_risk`` is corroborated in at
least two regions and HQ has observed ``demand_growth`` and ``supplier_concentration``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mycelic.sdk import MycelicClient

from .scenario import AgentSpec, ENTERPRISE, ENTITY

REGIONS = {"emea": "nw-gmbh", "apac": "nw-kk"}
TEAMS = {"logistics": "ops", "procurement": "ops", "field-sales": "commercial"}
HQ = {"region": "hq", "subsidiary": "northwind-hq", "department": "strategy"}
STRATEGIC_QUERY = "Should we qualify a second source for the SD-9 servo drive?"

PORTS = {"emea": "Rotterdam", "apac": "Busan"}
CUSTOMERS = {"emea": "Helios Automation", "apac": "Daichi Robotics"}


def regional_observations(region: str) -> dict[str, list[dict[str, Any]]]:
    port, customer = PORTS[region], CUSTOMERS[region]
    return {
        "logistics": [
            {"text": f"Port of {port} strike announced for weeks 41-43; SD-9 servo drive shipments route through it.",
             "topic": "supply:sd-9/transport", "slot": "transport_disruption", "entity": ENTITY, "confidence": 0.9},
            {"text": f"Carrier ETA for the SD-9 servo drive container via {port} slipped by 12 days.",
             "topic": "supply:sd-9/transport", "slot": "transport_disruption", "entity": ENTITY, "confidence": 0.7},
        ],
        "procurement": [
            {"text": "Kessler Antriebe confirms roughly two weeks of SD-9 inventory for this region and no second source qualified.",
             "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": ENTITY, "confidence": 0.85},
            {"text": "Kessler Antriebe quoted 9 weeks lead time for additional SD-9 units.",
             "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": ENTITY, "confidence": 0.75},
        ],
        "field-sales": [
            {"text": f"{customer} committed to 40 RX-4 arms for November; every RX-4 uses an SD-9 drive.",
             "topic": "supply:sd-9/demand", "slot": "demand_commitment", "entity": ENTITY, "confidence": 0.8},
            {"text": f"{customer} asked for delivery dates on 12 more RX-4 arms in Q4.",
             "topic": "supply:sd-9/demand", "slot": "demand_commitment", "entity": ENTITY, "confidence": 0.65},
        ],
    }


HQ_OBSERVATIONS = {
    "sourcing": [
        {"text": "Kessler Antriebe is the only qualified SD-9 source for every subsidiary; no second source is qualified.",
         "topic": "strategy:supply-base", "slot": "supplier_concentration", "entity": ENTITY, "confidence": 0.95},
    ],
    "analytics": [
        {"text": "RX-4 order intake is up 40% year over year across all regions.",
         "topic": "strategy:demand", "slot": "demand_growth", "entity": ENTITY, "confidence": 0.8},
    ],
}
PRIVATE = {
    "logistics": "Reminder: renew the forklift certification before October.",
    "procurement": "Personal note: Kessler's account manager prefers calls on Tuesdays.",
    "field-sales": "Internal: discount ceiling for this customer is 8%.",
    "sourcing": "Draft: shortlist of alternative drive suppliers, not yet validated.",
    "analytics": "Working note: the Q3 forecast model still double-counts returns.",
}


@dataclass
class StrategicScenario:
    workdir: Path
    agents_per_team: int = 2
    agents: list[AgentSpec] = field(default_factory=list)

    def build(self) -> "StrategicScenario":
        self.workdir.mkdir(parents=True, exist_ok=True)
        for region, sub in REGIONS.items():
            for team, dept in TEAMS.items():
                for i in range(1, self.agents_per_team + 1):
                    a = AgentSpec(agent_id=f"{region}-{team}-{i}", team=team, department=dept)
                    a.path = f"{ENTERPRISE}/{region}/{sub}/{dept}/{team}/{a.agent_id}"
                    self.agents.append(a)
        for team in ("sourcing", "analytics"):
            a = AgentSpec(agent_id=f"hq-{team}-1", team=team, department=HQ["department"])
            a.path = f"{ENTERPRISE}/{HQ['region']}/{HQ['subsidiary']}/{HQ['department']}/{team}/{a.agent_id}"
            self.agents.append(a)
        return self

    @staticmethod
    def region_of(a: AgentSpec) -> str:
        return a.path.split("/")[1]

    def register_all(self, admin: MycelicClient) -> None:
        for a in self.agents:
            parts = a.path.split("/")
            res = admin.register_agent(agent_id=a.agent_id, enterprise=parts[0], region=parts[1], subsidiary=parts[2],
                                       department=parts[3], team=parts[4], display_name=a.agent_id.replace("-", " ").title())
            a.api_key = res["api_key"]

    def write_observations(self) -> None:
        for a in self.agents:
            region = self.region_of(a)
            idx = int(a.agent_id.rsplit("-", 1)[1]) - 1
            shared = HQ_OBSERVATIONS[a.team] if region == "hq" else regional_observations(region)[a.team]
            mine = [shared[idx % len(shared)]]
            lines = [json.dumps({**obs, "share": True, "local_id": f"{a.agent_id}-obs-{n}"}) for n, obs in enumerate(mine)]
            lines.append(json.dumps({"text": PRIVATE[a.team], "share": False, "local_id": f"{a.agent_id}-private"}))
            a.observations = self.workdir / f"{a.agent_id}.observations.jsonl"
            a.observations.write_text("\n".join(lines) + "\n", encoding="utf-8")
            a.local_db = self.workdir / f"{a.agent_id}.local.db"
            a.state_file = self.workdir / f"{a.agent_id}.state.json"

    def run_agents(self, base_url: str, *, agents: list[AgentSpec] | None = None, timeout: float = 120.0) -> dict[str, dict[str, Any]]:
        from .scenario import Scenario

        proxy = Scenario(self.workdir)          # reuse the process runner
        proxy.agents = agents or self.agents
        return proxy.run_agents(base_url, agents=proxy.agents, timeout=timeout)

    def by_region(self, region: str) -> list[AgentSpec]:
        return [a for a in self.agents if self.region_of(a) == region]

    def agent(self, agent_id: str) -> AgentSpec:
        return next(a for a in self.agents if a.agent_id == agent_id)

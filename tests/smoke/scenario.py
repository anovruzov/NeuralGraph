"""The Northwind scenario: a small organization whose agents each see a piece of a supply problem.

Used by the production smoke test and the demo.  Nothing here is a mock: agents are real processes
(``python -m mycelic.sdk.agent``) with their own local memory files, talking to a real deployment over HTTP.

Organization (enterprise ``northwind`` -> region ``emea`` -> subsidiary ``nw-gmbh``)::

    ops / logistics      port, carrier and customs observations   -> slot transport_disruption
    ops / procurement    supplier stock levels                     -> slot supplier_buffer_low
    commercial / sales   customer commitments                      -> slot demand_commitment

No single agent, team or department can see the conclusion.  The rule ``component_supply_risk``
(deploy/mycelic/rules.json) needs all three slots for the same entity from at least three agents in at least two
teams; Mycelic derives it at the enterprise layer with the full lineage.  Every agent also keeps notes that are
never shared (``share: false``), so the demo can show what stayed local.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mycelic.harness import REPO_ROOT
from mycelic.sdk import MycelicClient

ENTITY = "sd-9"
ENTERPRISE, REGION, SUBSIDIARY = "northwind", "emea", "nw-gmbh"
TEAMS = {  # team -> department
    "logistics": "ops",
    "procurement": "ops",
    "field-sales": "commercial",
}
QUERY = "Is there a delivery risk for the RX-4 arm and its SD-9 servo drive this quarter?"

# Each team's shared observations (cycled over the team's agents) and private notes.
SHARED: dict[str, list[dict[str, Any]]] = {
    "logistics": [
        {"text": "Port of Rotterdam terminal 3 strike announced for weeks 41-43; SD-9 servo drive shipments route through it.",
         "topic": "supply:sd-9/transport", "slot": "transport_disruption", "entity": ENTITY, "confidence": 0.9},
        {"text": "Carrier ETA for the SD-9 servo drive container slipped by 12 days.",
         "topic": "supply:sd-9/transport", "slot": "transport_disruption", "entity": ENTITY, "confidence": 0.7},
        {"text": "Customs broker warns of a two-week backlog for SD-9 containers arriving via Rotterdam.",
         "topic": "supply:sd-9/transport", "slot": "transport_disruption", "entity": ENTITY, "confidence": 0.6},
    ],
    "procurement": [
        {"text": "Kessler Antriebe confirms roughly two weeks of SD-9 inventory left and no second source qualified.",
         "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": ENTITY, "confidence": 0.85},
        {"text": "Kessler Antriebe quoted 9 weeks lead time for additional SD-9 units.",
         "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": ENTITY, "confidence": 0.75},
    ],
    "field-sales": [
        {"text": "Helios Automation committed to 40 RX-4 arms for November; every RX-4 uses an SD-9 drive.",
         "topic": "supply:sd-9/demand", "slot": "demand_commitment", "entity": ENTITY, "confidence": 0.8},
        {"text": "Borealis Packaging asked for delivery dates on 12 RX-4 arms in Q4.",
         "topic": "supply:sd-9/demand", "slot": "demand_commitment", "entity": ENTITY, "confidence": 0.65},
    ],
}
PRIVATE: dict[str, list[str]] = {
    "logistics": ["Reminder: renew the forklift certification before October.",
                  "Draft note: the Antwerp fallback route was slower last year, verify before proposing it."],
    "procurement": ["Personal note: Kessler's account manager prefers calls on Tuesdays.",
                    "Unverified rumour that Kessler is being acquired; do not share until confirmed."],
    "field-sales": ["Helios wants a demo of the new gripper next month.",
                    "Internal: discount ceiling for Borealis is 8%."],
}


@dataclass
class AgentSpec:
    agent_id: str
    team: str
    department: str
    api_key: str = ""
    local_db: Path | None = None
    observations: Path | None = None
    state_file: Path | None = None
    path: str = ""

    @property
    def team_path(self) -> str:
        return f"{ENTERPRISE}/{REGION}/{SUBSIDIARY}/{self.department}/{self.team}"


@dataclass
class Scenario:
    workdir: Path
    agents_per_team: int = 2
    agents: list[AgentSpec] = field(default_factory=list)

    def build(self) -> "Scenario":
        self.workdir.mkdir(parents=True, exist_ok=True)
        for team, department in TEAMS.items():
            for i in range(1, self.agents_per_team + 1):
                self.agents.append(AgentSpec(agent_id=f"{team}-{i}", team=team, department=department))
        return self

    def register_all(self, admin: MycelicClient) -> None:
        for a in self.agents:
            res = admin.register_agent(agent_id=a.agent_id, enterprise=ENTERPRISE, region=REGION, subsidiary=SUBSIDIARY,
                                       department=a.department, team=a.team, display_name=a.agent_id.replace("-", " ").title())
            a.api_key = res["api_key"]
            a.path = res["agent"]["path"]

    def write_observations(self) -> None:
        for a in self.agents:
            idx = int(a.agent_id.rsplit("-", 1)[1]) - 1
            shared = SHARED[a.team]
            private = PRIVATE[a.team]
            lines = []
            # each agent shares one observation (cycling through the team's list) so pieces are spread across agents,
            # and the first two agents of a team overlap on the strongest one to show partial overlap
            mine = [shared[idx % len(shared)]]
            if idx == 1 and len(shared) > 1:
                mine.append(shared[0])
            for n, obs in enumerate(mine):
                lines.append(json.dumps({**obs, "share": True, "local_id": f"{a.agent_id}-obs-{n}"}))
            lines.append(json.dumps({"text": private[idx % len(private)], "share": False, "local_id": f"{a.agent_id}-private"}))
            a.observations = self.workdir / f"{a.agent_id}.observations.jsonl"
            a.observations.write_text("\n".join(lines) + "\n", encoding="utf-8")
            a.local_db = self.workdir / f"{a.agent_id}.local.db"
            a.state_file = self.workdir / f"{a.agent_id}.state.json"

    def run_agents(self, base_url: str, *, agents: list[AgentSpec] | None = None, timeout: float = 120.0) -> dict[str, dict[str, Any]]:
        """Run every agent as its own process, concurrently; return each agent's state summary."""
        procs = []
        for a in agents or self.agents:
            cmd = [sys.executable, "-m", "mycelic.sdk.agent", "--url", base_url, "--api-key", a.api_key, "--local-db", str(a.local_db),
                   "--observations", str(a.observations), "--state-file", str(a.state_file)]
            log = open(self.workdir / f"{a.agent_id}.log", "ab")
            procs.append((a, subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=log, stderr=subprocess.STDOUT)))
        states = {}
        for a, p in procs:
            p.wait(timeout=timeout)
            if p.returncode != 0:
                raise RuntimeError(f"agent {a.agent_id} exited with {p.returncode}: {(self.workdir / f'{a.agent_id}.log').read_text()[-2000:]}")
            states[a.agent_id] = json.loads(a.state_file.read_text(encoding="utf-8"))
        return states

    def agent(self, agent_id: str) -> AgentSpec:
        return next(a for a in self.agents if a.agent_id == agent_id)

    def by_team(self, team: str) -> list[AgentSpec]:
        return [a for a in self.agents if a.team == team]

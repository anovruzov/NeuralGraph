"""The live demo's own scenario: Orrery Robotics, a fictional company with 40 agents in three regions.

Fictional company, synthetic data.  Every name below (the company, its subsidiaries, the supplier "Mirelund
Drives", the customers "Tolvenka Packaging" and "Sorano Assembly") is invented; the port cities are real places
used only as scenery.

The core of the story is the SD-9 servo-drive scenario of ``tests/smoke/scenario_strategic.py`` (same topics,
slots, confidences, the same entity and the same rules in ``deploy/mycelic/rules.json``), retold with invented
names and staged in three waves so a presenter can watch each layer conclude::

    orrery (enterprise)
    ├─ emea / orrery-gmbh   ops: logistics, procurement, quality, maintenance   commercial: field-sales, pricing,
    │                       customer-success   people: hiring
    ├─ apac / orrery-kk     (same teams as EMEA)
    └─ hq   / orrery-hq     strategy: sourcing, analytics   finance: fp-and-a   it: security

* 14 **core** agents (two per core team in EMEA and APAC, one each in HQ sourcing and analytics) hold the
  pieces of the SD-9 story.  Stage ``observe``: logistics and field-sales share; stage ``conclude``: procurement
  shares, which completes ``regional_supply_risk`` in each region; stage ``synthesise``: HQ shares demand growth
  and supplier concentration, which completes ``strategic_second_source`` at the enterprise.
* 26 **background** agents in ten more teams share ordinary notes (weld quality, CNC maintenance, pricing,
  support tickets, hiring, FX, patching).  Their topics consolidate up the org chart but use no slot any rule
  needs, so they can never complete a rule: the signal has to come out of real, unrelated traffic.

Every agent also keeps at least one private note (``share: false``) that stays in its own local SQLite file.
Agents are real processes (``python -m mycelic.sdk.agent``) with their own keys, run by :func:`run_agents`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mycelic.harness import REPO_ROOT
from mycelic.sdk import MycelicClient
from tests.smoke.scenario import ENTITY
from tests.smoke.scenario_strategic import STRATEGIC_QUERY

__all__ = ["COMPANY", "ENTERPRISE", "ENTITY", "STRATEGIC_QUERY", "SUBJECT", "REGIONS", "STAGES", "LiveAgent", "LiveScenario",
           "run_agents"]

COMPANY = "Orrery Robotics"
SUBJECT = "SD-9 servo drive"                         # what the story is about (the console's labels)
ENTERPRISE = "orrery"
SUPPLIER = "Mirelund Drives"
CUSTOMERS = {"emea": "Tolvenka Packaging", "apac": "Sorano Assembly"}
PORTS = {"emea": "Rotterdam", "apac": "Busan"}
PLANTS = {"emea": "Eindhoven", "apac": "Nagoya", "hq": "head office"}

# region -> (label, subsidiary id, subsidiary label)
REGIONS: dict[str, tuple[str, str, str]] = {
    "emea": ("EMEA", "orrery-gmbh", "Orrery GmbH"),
    "apac": ("APAC", "orrery-kk", "Orrery K.K."),
    "hq": ("Head office", "orrery-hq", "Orrery HQ"),
}
DEPARTMENT_LABELS = {"ops": "Operations", "commercial": "Commercial", "people": "People", "strategy": "Strategy",
                     "finance": "Finance", "it": "IT"}
TEAM_LABELS = {"logistics": "Logistics", "procurement": "Procurement", "field-sales": "Field sales", "quality": "Quality",
               "maintenance": "Maintenance", "pricing": "Pricing", "customer-success": "Customer success",
               "hiring": "Hiring", "sourcing": "Sourcing", "analytics": "Analytics", "fp-and-a": "FP&A",
               "security": "Security"}
STAGES = ("observe", "conclude", "synthesise")      # the act in which an agent shares (acts 2, 3, 4)


def core_observations(region: str) -> dict[str, list[dict[str, Any]]]:
    """The SD-9 pieces one region's core teams hold; slots, topics and confidences as in scenario_strategic."""
    port, customer = PORTS[region], CUSTOMERS[region]
    return {
        "logistics": [
            {"text": f"Port of {port} strike announced for weeks 41-43; SD-9 servo drive shipments route through it.",
             "topic": "supply:sd-9/transport", "slot": "transport_disruption", "entity": ENTITY, "confidence": 0.9},
            {"text": f"Carrier ETA for the SD-9 servo drive container via {port} slipped by 12 days.",
             "topic": "supply:sd-9/transport", "slot": "transport_disruption", "entity": ENTITY, "confidence": 0.7},
        ],
        "procurement": [
            {"text": f"{SUPPLIER} confirms roughly two weeks of SD-9 inventory for this region and no second source qualified.",
             "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": ENTITY, "confidence": 0.85},
            {"text": f"{SUPPLIER} quoted 9 weeks lead time for additional SD-9 units.",
             "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": ENTITY, "confidence": 0.75},
        ],
        "field-sales": [
            {"text": f"{customer} committed to 40 RX-4 arms for November; every RX-4 uses an SD-9 drive.",
             "topic": "supply:sd-9/demand", "slot": "demand_commitment", "entity": ENTITY, "confidence": 0.8},
            {"text": f"{customer} asked for delivery dates on 12 more RX-4 arms in Q4.",
             "topic": "supply:sd-9/demand", "slot": "demand_commitment", "entity": ENTITY, "confidence": 0.65},
        ],
    }


HQ_OBSERVATIONS: dict[str, list[dict[str, Any]]] = {
    "sourcing": [
        {"text": f"{SUPPLIER} is the only qualified SD-9 source for every subsidiary; no second source is qualified.",
         "topic": "strategy:supply-base", "slot": "supplier_concentration", "entity": ENTITY, "confidence": 0.95},
    ],
    "analytics": [
        {"text": "RX-4 order intake is up 40% year over year across all regions.",
         "topic": "strategy:demand", "slot": "demand_growth", "entity": ENTITY, "confidence": 0.8},
    ],
}
RECOUNT = {"text": "Physical count confirms roughly two weeks of SD-9 inventory in the APAC warehouse.",
           "topic": "supply:sd-9/supplier", "slot": "supplier_buffer_low", "entity": ENTITY, "confidence": 0.9}


def background_observations(region: str) -> dict[str, list[dict[str, Any]]]:
    """Ordinary notes on other topics.  Their slots feed no rule, so they consolidate but never conclude."""
    plant = PLANTS[region]
    return {
        "quality": [
            {"text": f"Weld seam rejects on RX-4 base frames in {plant} at 1.8% this week, inside the 2% limit.",
             "topic": "quality:rx-4/welds", "slot": "defect_rate", "entity": "rx-4", "confidence": 0.8},
            {"text": f"Second-shift inspection in {plant} found porosity on 3 of 160 RX-4 frames; rework scheduled.",
             "topic": "quality:rx-4/welds", "slot": "defect_rate", "entity": "rx-4", "confidence": 0.7},
        ],
        "maintenance": [
            {"text": f"CNC line 2 in {plant}: spindle bearing replacement planned for Saturday, 6 hours of downtime.",
             "topic": "maintenance:cnc-line-2", "slot": "planned_downtime", "entity": "cnc-line-2", "confidence": 0.85},
            {"text": f"Vibration on CNC line 2 in {plant} is back within tolerance after the coolant pump swap.",
             "topic": "maintenance:cnc-line-2", "slot": "planned_downtime", "entity": "cnc-line-2", "confidence": 0.7},
        ],
        "pricing": [
            {"text": "Competitor list prices for comparable six-axis arms rose about 4% this quarter.",
             "topic": "pricing:rx-4/list", "slot": "price_signal", "entity": "rx-4", "confidence": 0.7},
            {"text": "Two distributors asked for volume tiers above 50 RX-4 units.",
             "topic": "pricing:rx-4/list", "slot": "price_signal", "entity": "rx-4", "confidence": 0.65},
        ],
        "customer-success": [
            {"text": "RX-4 gripper calibration tickets are down 20% since the firmware update.",
             "topic": "service:rx-4/tickets", "slot": "ticket_trend", "entity": "rx-4", "confidence": 0.8},
            {"text": "Most open RX-4 tickets are first-week setup questions, not faults.",
             "topic": "service:rx-4/tickets", "slot": "ticket_trend", "entity": "rx-4", "confidence": 0.7},
        ],
        "hiring": [
            {"text": "Four field-service engineer roles are open; median time to hire is 51 days.",
             "topic": "hiring:field-engineers", "slot": "open_roles", "entity": "field-engineers", "confidence": 0.8},
            {"text": "Referral candidates accept field-engineer offers faster than agency candidates.",
             "topic": "hiring:field-engineers", "slot": "open_roles", "entity": "field-engineers", "confidence": 0.6},
        ],
        "fp-and-a": [
            {"text": "EUR/JPY swings moved the Q3 APAC margin by about half a point.",
             "topic": "finance:fx-exposure", "slot": "fx_exposure", "entity": "fx", "confidence": 0.75},
            {"text": "Hedges cover 60% of next quarter's yen receivables.",
             "topic": "finance:fx-exposure", "slot": "fx_exposure", "entity": "fx", "confidence": 0.8},
        ],
        "security": [
            {"text": "Plant-floor HMI patch backlog is down to 11 devices.",
             "topic": "it:patching", "slot": "patch_backlog", "entity": "hmi", "confidence": 0.8},
            {"text": "Two plant gateways still run an end-of-life TLS library; replacements are ordered.",
             "topic": "it:patching", "slot": "patch_backlog", "entity": "hmi", "confidence": 0.75},
        ],
    }


PRIVATE: dict[str, list[str]] = {
    "logistics": ["Reminder: renew the forklift certification before October.",
                  "Draft: the Antwerp fallback route was slower last year, verify before proposing it."],
    "procurement": [f"Personal note: the {SUPPLIER} account manager prefers calls on Tuesdays.",
                    f"Unverified rumour that {SUPPLIER} is up for sale; do not share until confirmed."],
    "field-sales": ["Internal: discount ceiling for this customer is 8%.",
                    "The buyer hinted at a competing bid; keep this between us for now."],
    "sourcing": ["Draft: shortlist of alternative drive suppliers, not yet validated."],
    "analytics": ["Working note: the Q3 forecast model still double-counts returns."],
    "quality": ["Suspect the new weld wire batch; checking before I raise it.",
                "Audit prep checklist, personal copy."],
    "maintenance": ["Spare bearings are in locker 14, the key is with the night shift.",
                    "Idea: move the lubrication round to Mondays; not discussed yet."],
    "pricing": ["Scratch model: a 3% list increase would cost us two distributors.",
                "Do not quote the old tier table again."],
    "customer-success": ["Customer contact prefers WhatsApp; personal number on file.",
                         "Escalation draft for the Q4 renewals, not sent."],
    "hiring": ["Candidate salary expectations, confidential.",
               "Interview feedback notes, not for circulation."],
    "fp-and-a": ["Board pack draft numbers, unreviewed."],
    "security": ["Pen-test finding under embargo until the patch ships."],
}

CORE_TEAMS = {"logistics": ("ops", "observe"), "procurement": ("ops", "conclude"), "field-sales": ("commercial", "observe")}
HQ_CORE_TEAMS = {"sourcing": "strategy", "analytics": "strategy"}
BACKGROUND_TEAMS = {"quality": "ops", "maintenance": "ops", "pricing": "commercial", "customer-success": "commercial",
                    "hiring": "people"}
HQ_BACKGROUND_TEAMS = {"fp-and-a": "finance", "security": "it", "hiring": "people"}


@dataclass
class LiveAgent:
    """One agent: where it sits in the org chart, what it will share and what it keeps to itself.

    ``api_key``/``local_db``/``observations``/``state_file`` have the same names as ``tests.smoke.scenario.AgentSpec``
    so the agent runner treats both alike.  The key lives only in memory and is never written to a trace.
    """

    agent_id: str
    region: str
    department: str
    team: str
    role: str                       # "core" | "background"
    stage: str                      # STAGES: the act in which it shares
    shared: list[dict[str, Any]] = field(default_factory=list)
    private: list[str] = field(default_factory=list)
    delay: float = 0.0              # seconds before its first note, so a busy network trickles in
    api_key: str = ""
    local_db: Path | None = None
    observations: Path | None = None
    state_file: Path | None = None

    @property
    def subsidiary(self) -> str:
        return REGIONS[self.region][1]

    @property
    def team_path(self) -> str:
        return f"{ENTERPRISE}/{self.region}/{self.subsidiary}/{self.department}/{self.team}"

    @property
    def path(self) -> str:
        return f"{self.team_path}/{self.agent_id}"

    @property
    def label(self) -> str:
        return f"{TEAM_LABELS[self.team]} {self.agent_id.rsplit('-', 1)[1]}"


@dataclass
class LiveScenario:
    workdir: Path
    spread: float = 6.0             # seconds over which the stage-"observe" agents start sharing
    agents: list[LiveAgent] = field(default_factory=list)

    def build(self) -> "LiveScenario":
        self.workdir.mkdir(parents=True, exist_ok=True)
        for region in ("emea", "apac"):
            core = core_observations(region)
            for team, (dept, stage) in CORE_TEAMS.items():
                for i in (1, 2):
                    self.agents.append(LiveAgent(f"{region}-{team}-{i}", region, dept, team, "core", stage,
                                                 shared=[core[team][i - 1]], private=[PRIVATE[team][i - 1]]))
        for team, dept in HQ_CORE_TEAMS.items():
            self.agents.append(LiveAgent(f"hq-{team}-1", "hq", dept, team, "core", "synthesise",
                                         shared=list(HQ_OBSERVATIONS[team]), private=list(PRIVATE[team])))
        for region in ("emea", "apac", "hq"):
            notes = background_observations(region)
            teams = HQ_BACKGROUND_TEAMS if region == "hq" else BACKGROUND_TEAMS
            for team, dept in teams.items():
                for i in (1, 2):
                    private = PRIVATE[team]
                    self.agents.append(LiveAgent(f"{region}-{team}-{i}", region, dept, team, "background", "observe",
                                                 shared=[notes[team][i - 1]], private=[private[(i - 1) % len(private)]]))
        # a deterministic trickle: background and core notes interleave over ``spread`` seconds
        observe = self.stage("observe")
        for n, a in enumerate(sorted(observe, key=lambda a: (a.agent_id.rsplit("-", 1)[1], a.team, a.region))):
            a.delay = round(self.spread * n / max(1, len(observe) - 1), 2)
        return self

    # ------------------------------------------------------------------ queries over the scenario
    def stage(self, stage: str) -> list[LiveAgent]:
        return [a for a in self.agents if a.stage == stage]

    def by_role(self, role: str) -> list[LiveAgent]:
        return [a for a in self.agents if a.role == role]

    def by_region(self, region: str) -> list[LiveAgent]:
        return [a for a in self.agents if a.region == region]

    def agent(self, agent_id: str) -> LiveAgent:
        return next(a for a in self.agents if a.agent_id == agent_id)

    def units(self) -> list[dict[str, Any]]:
        """Every organizational unit above the agents, parents before children (the trace's ``org.units``)."""
        units: dict[str, dict[str, Any]] = {ENTERPRISE: {"path": ENTERPRISE, "layer": "enterprise", "label": COMPANY, "parent": None}}
        for a in self.agents:
            region = f"{ENTERPRISE}/{a.region}"
            sub = f"{region}/{a.subsidiary}"
            dept = f"{sub}/{a.department}"
            units.setdefault(region, {"path": region, "layer": "region", "label": REGIONS[a.region][0], "parent": ENTERPRISE})
            units.setdefault(sub, {"path": sub, "layer": "subsidiary", "label": REGIONS[a.region][2], "parent": region})
            units.setdefault(dept, {"path": dept, "layer": "department", "label": DEPARTMENT_LABELS[a.department], "parent": sub})
            units.setdefault(a.team_path, {"path": a.team_path, "layer": "team", "label": TEAM_LABELS[a.team], "parent": dept})
        return list(units.values())

    # ------------------------------------------------------------------ actions
    def register(self, admin: MycelicClient, a: LiveAgent) -> dict[str, Any]:
        res = admin.register_agent(agent_id=a.agent_id, enterprise=ENTERPRISE, region=a.region, subsidiary=a.subsidiary,
                                   department=a.department, team=a.team, display_name=a.label)
        a.api_key = res["api_key"]
        return res["agent"]

    def write_observations(self, a: LiveAgent, shared: list[dict[str, Any]] | None = None, *, tag: str = "obs",
                           include_private: bool = True) -> None:
        """Write the JSONL file the agent process consumes: its shared notes (``a.shared`` unless ``shared`` is
        given), then its private ones.  Only the first file of an agent carries its start delay."""
        notes = a.shared if shared is None else shared
        lines = []
        for n, obs in enumerate(notes):
            delay = {"delay": a.delay} if n == 0 and shared is None and a.delay else {}
            lines.append(json.dumps({**obs, "share": True, "local_id": f"{a.agent_id}-{tag}-{n}", **delay}))
        if include_private:
            for n, text in enumerate(a.private):
                lines.append(json.dumps({"text": text, "share": False, "local_id": f"{a.agent_id}-private-{n}"}))
        a.observations = self.workdir / f"{a.agent_id}.{tag}.jsonl"
        a.observations.write_text("\n".join(lines) + "\n", encoding="utf-8")
        a.local_db = self.workdir / f"{a.agent_id}.local.db"
        a.state_file = self.workdir / f"{a.agent_id}.state.json"


def run_agents(workdir: Path, base_url: str, agents: list[LiveAgent], *, timeout: float = 120.0) -> dict[str, dict[str, Any]]:
    """Run every agent as its own process, concurrently; return each agent's local-state summary.

    The same runner as ``tests.smoke.scenario.Scenario.run_agents``, except that a failure or an interrupt kills
    the agents still running instead of leaving them behind, and that each key reaches its agent through the
    environment (``MYCELIC_API_KEY``), not the command line, so ``ps`` never shows it.  A failing agent raises a
    one-line error; its log tail goes to stderr.
    """
    procs: list[tuple[LiveAgent, subprocess.Popen]] = []
    try:
        for a in agents:
            cmd = [sys.executable, "-m", "mycelic.sdk.agent", "--url", base_url,
                   "--local-db", str(a.local_db), "--observations", str(a.observations), "--state-file", str(a.state_file)]
            env = {**os.environ, "MYCELIC_API_KEY": a.api_key}
            with open(workdir / f"{a.agent_id}.log", "ab") as log:
                procs.append((a, subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)))
        states = {}
        for a, p in procs:
            p.wait(timeout=timeout)
            if p.returncode != 0:
                tail = (workdir / f"{a.agent_id}.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
                print(f"--- agent {a.agent_id} log tail ---\n" + "\n".join(tail[-20:]), file=sys.stderr)
                raise RuntimeError(f"agent {a.agent_id} exited with {p.returncode}: {(tail or ['(no output)'])[-1][:160]}")
            states[a.agent_id] = json.loads(a.state_file.read_text(encoding="utf-8"))
        return states
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)

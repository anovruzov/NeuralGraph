"""The demonstration seed: two fictional tenants exactly as docs/mycelic/DEMO_SCENARIO.md describes them.

Why a seed and not fixtures: the same organization backs the UI demonstration, the verification scenario and the
tests, so every reader of the scenario document can recognize what they see. Everything created here carries
``is_demo = 1`` and the goal title starts with ``[Demo]``; nothing here is ever mistaken for real records.

``run_seed`` is idempotent: a second call finds the tenant by slug and returns the same shape without writing.
``reset=True`` removes the demo tenants' rows and holder directories first, so ``python -m mycelic seed --reset``
starts over without touching anything else in the coordination DB.

Evidence texts are designed for the deterministic fake model (docs/mycelic/DECISIONS.md D7): each document
contains the words ``recurring`` and ``blocker`` plus its domain word, so a routed gap question (``What recurring
operational blockers related to <domain> ...``) shares at least two content tokens with it and the holder answers.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from ..auth import hash_password
from ..util import plus_seconds
from . import simulate

logger = logging.getLogger(__name__)

DAY = 86400
DEMO_PASSWORD = "demo-password"
MERIDIAN_SLUG = "meridian"
ORBITAL_SLUG = "orbital"
HOLDER_A = "hold_demo_a"          # Elin Dahl's holder: a separate process in the verification scenario
HOLDER_B = "hold_demo_b"          # Marcus Lund's holder: the second separate process
DEMO_GOAL_TITLE = ("[Demo] Identify recurring operational blockers across teams, verify their causes, and propose measurable "
                   "actions that reduce resolution time.")
# The order in which the loop asks: what the scenario document lists first is what the verification checks first.
DEMO_DOMAIN_ORDER = ["deployments", "approvals", "on-call", "escalation", "customs", "dispatch", "support", "metrics"]

# key, unit type, name, parent key. Iberia deliberately has no department level.
UNITS: list[tuple[str, str, str, str | None]] = [
    ("root", "executive", "Meridian Cold Chain", None),
    ("europe", "region", "Europe", "root"),
    ("nordics", "subsidiary", "Meridian Nordics", "europe"),
    ("operations", "department", "Operations", "nordics"),
    ("engineering", "department", "Engineering", "nordics"),
    ("support", "team", "Customer Support", "operations"),
    ("dispatch", "team", "Fleet Dispatch", "operations"),
    ("platform", "team", "Platform", "engineering"),
    ("sre", "team", "Site Reliability", "engineering"),
    ("iberia", "subsidiary", "Meridian Iberia", "europe"),
    ("iberia_ops", "team", "Iberia Operations", "iberia"),
    ("project", "project", "Dispatch Modernization", "nordics"),
]
PROJECT_SCOPE = {"project": ["dispatch", "platform"]}

# key, full name, [(unit key, role)], holder domains (empty: no holder), holder id
PERSONAS: list[dict[str, Any]] = [
    {"key": "ingrid", "name": "Ingrid Solheim", "memberships": [("root", "executive")], "domains": []},
    {"key": "tomas", "name": "Tomas Reyes", "memberships": [("root", "org_admin")], "domains": []},
    {"key": "petra", "name": "Petra Lindqvist", "memberships": [("europe", "regional_lead")], "domains": []},
    {"key": "anders", "name": "Anders Vik", "memberships": [("nordics", "subsidiary_lead")], "domains": []},
    {"key": "diego", "name": "Diego Ruiz", "memberships": [("iberia", "subsidiary_lead"), ("iberia_ops", "team_lead")], "domains": ["dispatch", "customs"]},
    {"key": "maya", "name": "Maya Okafor", "memberships": [("operations", "department_lead")], "domains": []},
    {"key": "lucas", "name": "Lucas Brandt", "memberships": [("engineering", "department_lead")], "domains": []},
    {"key": "sofia", "name": "Sofia Berg", "memberships": [("support", "team_lead")], "domains": ["support", "metrics"]},
    {"key": "jonas", "name": "Jonas Holm", "memberships": [("dispatch", "team_lead")], "domains": ["dispatch", "approvals", "on-call"]},
    {"key": "priya", "name": "Priya Nair", "memberships": [("platform", "team_lead")], "domains": ["deployments", "approvals"]},
    {"key": "elin", "name": "Elin Dahl", "memberships": [("support", "employee")], "domains": ["support", "deployments"], "holder_id": HOLDER_A},
    {"key": "marcus", "name": "Marcus Lund", "memberships": [("dispatch", "employee"), ("project", "employee")], "domains": ["dispatch", "approvals"], "holder_id": HOLDER_B},
    {"key": "ana", "name": "Ana Costa", "memberships": [("platform", "employee"), ("project", "employee")], "domains": ["deployments"]},
    {"key": "noor", "name": "Noor Haddad", "memberships": [("sre", "employee")], "domains": ["on-call", "escalation"]},
    {"key": "rafael", "name": "Rafael Ortega", "memberships": [("iberia_ops", "employee")], "domains": ["customs", "dispatch"]},
]

ORBITAL_USERS: list[dict[str, Any]] = [
    {"key": "mira", "name": "Mira Vance", "memberships": [("root", "org_admin"), ("root", "executive")], "domains": []},
    {"key": "leo", "name": "Leo Park", "memberships": [("bakery", "employee")], "domains": ["maintenance"], "holder_id": "hold_demo_orbital"},
]

_ELIN_TEXT = ("Recurring support blocker for deployments: deploy approvals for hotfixes take two days because the change board "
              "meets twice a week. Tickets stay open while we wait for the approval.")

# The evidence table of DEMO_SCENARIO.md. ``persona`` names the holder owner; the text carries "recurring", "blocker"
# and the domain word so the fake answer rule keeps it for the matching gap question.
_DOCUMENTS: list[dict[str, Any]] = [
    {"doc_id": "doc_demo_elin_1", "persona": "elin", "title": "Support retrospective, week 36", "text": _ELIN_TEXT,
     "observed_days_ago": 3, "domains": ["support", "deployments"], "origin": "root R1 (original retrospective)",
     "purpose": "root R1"},
    {"doc_id": "doc_demo_ana_1", "persona": "ana", "title": "Forwarded: Support retrospective, week 36", "text": _ELIN_TEXT,
     "observed_days_ago": 1, "domains": ["deployments"], "origin": "exact copy of R1 forwarded by e-mail",
     "purpose": "copy of R1 -> copied support, not independent"},
    {"doc_id": "doc_demo_marcus_1", "persona": "marcus", "title": "Dispatch incident log",
     "text": ("Recurring dispatch blocker on approvals: route changes need deploy approvals; approvals take two days and delay "
              "dispatch fixes. Drivers wait for the approval before the route update goes live."),
     "observed_days_ago": 2, "domains": ["dispatch", "approvals"], "origin": "root R2 (dispatch team log)",
     "purpose": "root R2, complementary to R1"},
    {"doc_id": "doc_demo_priya_1", "persona": "priya", "title": "Change board notes",
     "text": ("Recurring operational blocker for hotfix deployments and approvals: the change board approves deploys on Tuesdays "
              "and Thursdays; deploy approvals take two days on average."),
     "observed_days_ago": 5, "domains": ["deployments", "approvals"], "origin": "root R3 (change board minutes)",
     "purpose": "root R3, third independent source"},
    {"doc_id": "doc_demo_sofia_1", "persona": "sofia", "title": "Ticket resolution metrics",
     "text": ("Recurring support blocker in the metrics: median ticket resolution time is 52 hours; 30 percent of tickets wait on "
              "deploy approvals."),
     "observed_days_ago": 2, "domains": ["support", "metrics"], "origin": "measurement export", "purpose": "measurement baseline"},
    {"doc_id": "doc_demo_noor_1", "persona": "noor", "title": "On-call roster (January)",
     "text": "Recurring on-call blocker: the on-call rotation has 4 engineers and pages take 45 minutes to acknowledge.",
     "observed_days_ago": 200, "domains": ["on-call"], "origin": "root R4 (old roster)", "purpose": "stale fact (R4)"},
    {"doc_id": "doc_demo_jonas_1", "persona": "jonas", "title": "On-call roster (current)",
     "text": "Recurring on-call blocker: the on-call rotation has 6 engineers and pages take 20 minutes to acknowledge.",
     "observed_days_ago": 1, "domains": ["on-call"], "origin": "root R5 (current roster)",
     "purpose": "genuine contradiction with R4 (R5)"},
    {"doc_id": "doc_demo_noor_2", "persona": "noor", "title": "Pager escalation policy",
     "text": "Recurring escalation blocker: escalations go to the platform lead after 30 minutes without acknowledgement.",
     "observed_days_ago": 200, "domains": ["escalation"], "origin": "root R6 (old policy page)",
     "purpose": "single stale source -> stale hypothesis"},
    {"doc_id": "doc_demo_rafael_1", "persona": "rafael", "title": "Customs clearance delays",
     "text": ("Recurring customs blocker: customs paperwork delays cross-border shipments by a day; the approval from the customs "
              "broker is the blocker."),
     "observed_days_ago": 4, "domains": ["customs"], "origin": "root R7 (Iberia operations note)",
     "purpose": "single source outside Nordics -> hypothesis; blind verification finds no other holder"},
]

_ORBITAL_DOCUMENTS: list[dict[str, Any]] = [
    {"doc_id": "doc_demo_orbital_1", "persona": "leo", "title": "Oven maintenance schedule",
     "text": ("Recurring maintenance blocker: the deck ovens are descaled every Monday morning and the proofing cabinet is "
              "serviced monthly, so the first bake waits for the technician."),
     "observed_days_ago": 2, "domains": ["maintenance"], "origin": "Orbital Bakery record", "purpose": "isolation only"},
]


def demo_documents(*, tenant: str = MERIDIAN_SLUG) -> list[dict[str, Any]]:
    """The document table as data: title, text, observed_at (computed from the day offset now), domains, origin note.

    Returned dicts are copies; ``holder_id`` is filled in so the scenario can address the holder directly."""
    rows = _DOCUMENTS if tenant == MERIDIAN_SLUG else _ORBITAL_DOCUMENTS
    personas = PERSONAS if tenant == MERIDIAN_SLUG else ORBITAL_USERS
    ids = {p["key"]: _holder_id(p) for p in personas if p.get("domains")}
    out = []
    for d in rows:
        doc = dict(d)
        doc["holder_id"] = ids[d["persona"]]
        doc["kind"] = "note"
        doc["observed_at"] = plus_seconds(-int(d["observed_days_ago"]) * DAY)
        out.append(doc)
    return out


def _holder_id(persona: dict[str, Any]) -> str:
    return persona.get("holder_id") or f"hold_demo_{persona['key']}"


def _email(persona: dict[str, Any], domain: str) -> str:
    return f"{persona['key']}@{domain}"


def external_holder_keys_from_env() -> dict[str, str]:
    """``MYCELIC_HOLDER_KEY_A`` / ``_B`` make Elin's and Marcus's holders external (what the compose file does)."""
    out = {}
    if os.environ.get("MYCELIC_HOLDER_KEY_A"):
        out[HOLDER_A] = os.environ["MYCELIC_HOLDER_KEY_A"]
    if os.environ.get("MYCELIC_HOLDER_KEY_B"):
        out[HOLDER_B] = os.environ["MYCELIC_HOLDER_KEY_B"]
    return out


def _normalize_keys(external_holder_keys: dict[str, str] | None) -> dict[str, str]:
    keys = dict(external_holder_keys) if external_holder_keys is not None else external_holder_keys_from_env()
    aliases = {"a": HOLDER_A, "b": HOLDER_B, "elin": HOLDER_A, "marcus": HOLDER_B}
    return {aliases.get(k, k): v for k, v in keys.items() if v}


# ------------------------------------------------------------------------------------------------ seed
async def run_seed(rt: Any, *, reset: bool = False, external_holder_keys: dict[str, str] | None = None) -> dict[str, Any]:
    """Create (or return) the demonstration tenants. See the module docstring for the contract."""
    keys = _normalize_keys(external_holder_keys)
    existing = rt.org.get_tenant_by_slug(MERIDIAN_SLUG)
    if existing is not None and reset:
        await reset_demo(rt)
        existing = None
    if existing is not None:
        out = await _describe_existing(rt, existing)
        out["created"] = False
    else:
        out = await _create_meridian(rt, keys)
        out["orbital_tenant_id"] = (await _create_orbital(rt))["tenant_id"]
        out["created"] = True
    simulate.install(rt)
    await simulate.enqueue_first(rt, out["tenant_id"])
    return out


async def reset_demo(rt: Any) -> dict[str, int]:
    """Delete the demo tenants' rows (every table that carries their tenant id) and their holder directories."""
    removed: dict[str, int] = {}
    holders_dir = Path(getattr(rt.settings, "holders_dir", "") or Path(rt.settings.data_dir) / "holders")
    for slug in (MERIDIAN_SLUG, ORBITAL_SLUG):
        tenant = rt.org.get_tenant_by_slug(slug)
        if tenant is None:
            continue
        tid = tenant["tenant_id"]
        holders = rt.org.list_holders(tid)
        for h in holders:
            if rt.holders is not None and hasattr(rt.holders, "remove"):
                try:
                    await rt.holders.remove(h["holder_id"])
                except Exception:  # a holder that never started
                    logger.debug("holder %s was not running", h["holder_id"])
            shutil.rmtree(holders_dir / h["holder_id"], ignore_errors=True)
        holder_ids = [h["holder_id"] for h in holders]
        async with rt.db.tx() as c:
            n = 0
            job_ids = [r["job_id"] for r in c.execute("SELECT job_id FROM jobs WHERE tenant_id=?", (tid,)).fetchall()]
            if job_ids:
                c.execute(f"DELETE FROM job_attempts WHERE job_id IN ({','.join('?' * len(job_ids))})", job_ids)
            disc_ids = [r["discovery_id"] for r in c.execute("SELECT discovery_id FROM discoveries WHERE tenant_id=?", (tid,)).fetchall()]
            if disc_ids:
                c.execute(f"DELETE FROM discovery_reviews WHERE discovery_id IN ({','.join('?' * len(disc_ids))})", disc_ids)
            chat_ids = [r["chat_id"] for r in c.execute("SELECT chat_id FROM agent_chats WHERE tenant_id=?", (tid,)).fetchall()]
            if chat_ids:
                c.execute(f"DELETE FROM agent_messages WHERE chat_id IN ({','.join('?' * len(chat_ids))})", chat_ids)
            claim_ids = [r["claim_id"] for r in c.execute("SELECT claim_id FROM claims WHERE tenant_id=?", (tid,)).fetchall()]
            if claim_ids:
                c.execute(f"DELETE FROM claim_evidence WHERE claim_id IN ({','.join('?' * len(claim_ids))})", claim_ids)
            # children before parents; self-references inside one statement are fine (immediate FK checks run at statement end)
            for table in ("discoveries", "conflicts", "derivations", "claims", "evidence_refs", "revisions", "responses", "question_routes",
                          "question_runs", "questions", "goal_outcomes", "goal_loops", "goals", "notifications", "agent_chats", "jobs",
                          "model_usage", "events", "error_reports", "sessions", "invitations", "api_keys", "memberships", "grants",
                          "holders", "org_units", "tenant_policies", "users", "audit_log"):
                if table == "org_units":
                    c.execute("DELETE FROM project_scopes WHERE project_id IN (SELECT unit_id FROM org_units WHERE tenant_id=?)", (tid,))
                n += c.execute(f"DELETE FROM {table} WHERE tenant_id=?", (tid,)).rowcount
            c.execute("DELETE FROM transport_messages WHERE subject LIKE ?", (f"mycelic.{tid}.%",))
            for hid in holder_ids:
                c.execute("DELETE FROM transport_cursors WHERE consumer=?", (f"holder-{hid}",))
                c.execute("DELETE FROM transport_processed WHERE consumer=?", (f"holder-{hid}",))
            n += c.execute("DELETE FROM tenants WHERE tenant_id=?", (tid,)).rowcount
        removed[slug] = n
        logger.info("reset demo tenant %s: %d rows removed", slug, n)
    return removed


async def _create_meridian(rt: Any, keys: dict[str, str]) -> dict[str, Any]:
    tenant = await rt.org.create_tenant("Meridian Cold Chain", MERIDIAN_SLUG, is_demo=True)
    tid = tenant["tenant_id"]
    units = await _create_units(rt, tid, UNITS, PROJECT_SCOPE)
    users = await _create_users(rt, tid, PERSONAS, units, "meridian.example")
    tomas = users["tomas"]
    await rt.org.set_policy(tid, "min_independent_roots", 2, actor_id=tomas)
    await rt.org.set_policy(tid, "freshness_days", 90, actor_id=tomas)
    holders, external = await _register_holders(rt, tid, PERSONAS, users, keys)
    await rt.org.add_grant(tid, grantor_id=users["elin"], grantee_type="user", grantee_id=users["sofia"], resource_type="holder",
                           resource_id=HOLDER_A, level="artifact", reason="sharing selected knowledge with my team lead")
    pending = await _ingest_documents(rt, demo_documents(), users, external)
    goal, subgoals = await _create_goal(rt, users, units)
    return {
        "tenant_id": tid, "users": users, "units": units, "holders": holders, "external_holder_ids": sorted(external),
        "goal_id": goal["goal_id"], "subgoal_ids": [s["goal_id"] for s in subgoals], "pending_external_documents": pending,
    }


async def _create_orbital(rt: Any) -> dict[str, Any]:
    tenant = await rt.org.create_tenant("Orbital Bakery Co.", ORBITAL_SLUG, is_demo=True)
    tid = tenant["tenant_id"]
    units = await _create_units(rt, tid, [("root", "executive", "Orbital Bakery Co.", None), ("bakery", "team", "Bakery Operations", "root")], {})
    users = await _create_users(rt, tid, ORBITAL_USERS, units, "orbital.example")
    holders, external = await _register_holders(rt, tid, ORBITAL_USERS, users, {})
    await _ingest_documents(rt, demo_documents(tenant=ORBITAL_SLUG), users, external)
    return {"tenant_id": tid, "users": users, "units": units, "holders": holders}


async def _create_units(rt: Any, tid: str, spec: list[tuple[str, str, str, str | None]], scopes: dict[str, list[str]]) -> dict[str, str]:
    units: dict[str, str] = {}
    for key, typ, name, parent in spec:
        u = await rt.org.create_unit(tid, typ, name, parent_id=units[parent] if parent else None, is_demo=True)
        units[key] = u["unit_id"]
    for project, members in scopes.items():
        await rt.org.set_project_scope(units[project], [units[m] for m in members])
    return units


async def _create_users(rt: Any, tid: str, personas: list[dict[str, Any]], units: dict[str, str], domain: str) -> dict[str, str]:
    users: dict[str, str] = {}
    pw = hash_password(DEMO_PASSWORD)
    for p in personas:
        u = await rt.org.create_user(tid, _email(p, domain), p["name"], password_hash=pw, is_demo=True)
        users[p["key"]] = u["user_id"]
        for unit_key, role in p["memberships"]:
            await rt.org.add_membership(tid, u["user_id"], units[unit_key], role)
    return users


async def _register_holders(rt: Any, tid: str, personas: list[dict[str, Any]], users: dict[str, str], keys: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    holders: dict[str, str] = {}
    external: set[str] = set()
    for p in personas:
        if not p.get("domains"):
            continue
        hid = _holder_id(p)
        mode = "external" if hid in keys else "embedded"
        await rt.org.register_holder(tid, owner_type="user", owner_id=users[p["key"]], name=f"{p['name'].split()[0]}'s evidence", mode=mode,
                                     domains=p["domains"], holder_id=hid, key=keys.get(hid))
        holders[p["key"]] = hid
        if mode == "external":
            external.add(hid)
        elif rt.holders is not None:
            await rt.holders.ensure(hid)
    return holders, external


async def _ingest_documents(rt: Any, docs: list[dict[str, Any]], users: dict[str, str], external: set[str]) -> list[dict[str, Any]]:
    """Embedded holders receive their documents here; external holders' documents are returned for the caller to push
    through the holder process (the coordinator never writes into a store it does not own)."""
    pending: list[dict[str, Any]] = []
    for d in docs:
        row = {k: d[k] for k in ("doc_id", "holder_id", "title", "text", "kind", "observed_at", "domains")}
        row["uploaded_by"] = users.get(d["persona"])
        if d["holder_id"] in external:
            pending.append(row)
            continue
        store = rt.holders.get(d["holder_id"]) if rt.holders is not None else None
        if store is None:
            raise RuntimeError(f"embedded holder {d['holder_id']} is not available; the seed needs rt.holders (MYCELIC_EMBEDDED_HOLDERS=1)")
        await store.ingest_document(row["title"], row["text"], kind=row["kind"], observed_at=row["observed_at"], domains=row["domains"],
                                    uploaded_by=row["uploaded_by"], doc_id=row["doc_id"])
    return pending


async def _create_goal(rt: Any, users: dict[str, str], units: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    petra = rt.authz.principal_for_user(users["petra"])
    goal = await rt.goals.create_goal(petra, {
        "title": DEMO_GOAL_TITLE,
        "objective": ("Identify recurring operational blockers across teams, verify their causes with independent evidence, and propose "
                      "measurable actions that reduce ticket resolution time."),
        "owner_type": "unit", "owner_id": units["europe"], "scope_unit_id": units["europe"],
        "success_criteria": [{"metric": "resolution_time_hours", "target": 24, "direction": "decrease"}],
        "baseline": {"resolution_time_hours": 52},
        "measurement_source": {"metric": "resolution_time_hours", "source": "Ticket resolution metrics (Sofia Berg's holder)",
                               "domains": list(DEMO_DOMAIN_ORDER)},
        "budget": {"tokens": 400000, "usd": 5, "questions": 60, "followup_depth": 3, "period": "day"},   # renews daily (UTC); fake model costs 0 USD
        "priority": 2,
    }, activate=True)
    # team subgoals run their own loops (team-scoped questions and discoveries), so every level of the demo has content:
    # teams see their own findings, departments and above see them aggregated, the region sees the cross-team picture
    team_budget = {"tokens": 150000, "usd": 2, "questions": 30, "followup_depth": 2, "period": "day"}
    out = await rt.goals.action(petra, goal["goal_id"], "decompose", {"subgoals": [
        {"title": "[Demo] Cut ticket time waiting on approvals", "objective": "Reduce the time support tickets spend waiting on deploy approvals.",
         "owner_type": "unit", "owner_id": units["support"], "scope_unit_id": units["support"], "assignees": [{"type": "user", "id": users["sofia"]}],
         "success_criteria": [{"metric": "resolution_time_hours", "target": 24, "direction": "decrease"}], "baseline": {"resolution_time_hours": 52},
         "measurement_source": {"metric": "resolution_time_hours", "source": "Ticket resolution metrics", "domains": ["support", "deployments"]},
         "budget": team_budget, "activate": True},
        {"title": "[Demo] Reduce route-update delay", "objective": "Reduce the delay between a route change and its live update for drivers.",
         "owner_type": "unit", "owner_id": units["dispatch"], "scope_unit_id": units["dispatch"], "assignees": [{"type": "user", "id": users["jonas"]}],
         "measurement_source": {"domains": ["dispatch", "approvals", "on-call"]}, "budget": team_budget, "activate": True},
    ]})
    return goal, list(out.get("subgoals") or [])


async def _describe_existing(rt: Any, tenant: dict[str, Any]) -> dict[str, Any]:
    """The same shape as a fresh seed, rebuilt from the database (no writes except starting embedded holders)."""
    tid = tenant["tenant_id"]
    users = {p["key"]: (rt.org.find_user(tid, _email(p, "meridian.example")) or {"user_id": None})["user_id"] for p in PERSONAS}
    by_name = {(u["type"], u["name"]): u["unit_id"] for u in rt.org.list_units(tid)}
    units = {key: by_name.get((typ, name)) for key, typ, name, _ in UNITS}
    holders: dict[str, str] = {}
    external: set[str] = set()
    for h in rt.org.list_holders(tid):
        for p in PERSONAS:
            if users.get(p["key"]) == h["owner_id"] and h["holder_id"] == _holder_id(p):
                holders[p["key"]] = h["holder_id"]
        if h["mode"] == "external":
            external.add(h["holder_id"])
        elif rt.holders is not None and h["status"] != "revoked":
            await rt.holders.ensure(h["holder_id"])
    goal = rt.db.one("SELECT goal_id FROM goals WHERE tenant_id=? AND parent_goal_id IS NULL AND title=? ORDER BY created_at LIMIT 1", (tid, DEMO_GOAL_TITLE))
    goal_id = goal["goal_id"] if goal else None
    subgoals = [r["goal_id"] for r in rt.db.all("SELECT goal_id FROM goals WHERE parent_goal_id=? ORDER BY created_at", (goal_id,))] if goal_id else []
    orbital = rt.org.get_tenant_by_slug(ORBITAL_SLUG)
    pending = [{k: d[k] for k in ("doc_id", "holder_id", "title", "text", "kind", "observed_at", "domains")} | {"uploaded_by": users.get(d["persona"])}
               for d in demo_documents() if d["holder_id"] in external]
    return {"tenant_id": tid, "users": users, "units": units, "holders": holders, "external_holder_ids": sorted(external), "goal_id": goal_id,
            "subgoal_ids": subgoals, "orbital_tenant_id": orbital["tenant_id"] if orbital else None, "pending_external_documents": pending}

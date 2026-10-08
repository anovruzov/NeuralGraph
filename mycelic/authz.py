"""The one authorization engine (docs/mycelic/DECISIONS.md D9).

Every layer asks the same object the same questions, with the same ``Principal``:

* API handlers            -> ``require(...)`` / ``can_*`` before reading or writing
* retrieval / agents      -> ``knowledge_filter`` restricts which claims, discoveries and evidence refs enter a
                             model's context or a search result
* artifact routing        -> ``can_route(question, holder)`` decides which holders may receive a question
* workers                 -> re-check at create, route, retrieve and use with ``principal_for_loop``

Model
-----
A principal is a user with active memberships (unit, role), or a holder, worker or system actor.
Administrative rights (``org_admin``) are separate from knowledge access: an administrator manages people,
hierarchy, policies, models and workers but sees no claims, discoveries or evidence unless a membership or
grant says so.

Knowledge visibility of an artifact (claim, discovery, goal, question) with ``visibility`` and
``scope_unit_id``:

* ``org``      every active member of the tenant
* ``unit``     members of the scope unit; leads of the scope unit or of any of its ancestors ("a senior role
               receives authorized organizational knowledge"); members of a project that spans the unit;
               explicit grants
* ``private``  the owner user; explicit grants

Raw evidence in a holder's store is visible to the holder's owner only, or through an explicit ``raw``
grant on that holder. Policy-approved disclosures attached to a visible claim are visible with the claim.
Tenant isolation is absolute: a principal never sees a row from another tenant, whatever the grants say.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .db.coord import CoordDB
from .org import LEAD_ROLES, LEVEL_FOR_UNIT_TYPE, OrgService
from .util import now_iso

LEVEL_ORDER = ("employee", "team", "department", "subsidiary", "region", "executive")
ROLE_LEVEL = {"employee": "employee", "team_lead": "team", "department_lead": "department", "subsidiary_lead": "subsidiary",
              "regional_lead": "region", "executive": "executive", "org_admin": "admin"}


class Forbidden(PermissionError):
    """Raised by ``require``; the API maps it to 403 and the audit log records the denial."""

    def __init__(self, action: str, resource: str = "", reason: str = "not authorized") -> None:
        super().__init__(f"{action} {resource}: {reason}".strip())
        self.action, self.resource, self.reason = action, resource, reason


@dataclass
class Principal:
    tenant_id: str
    kind: str                                  # user | holder | worker | system
    id: str                                    # user_id / holder_id / worker_id
    name: str = ""
    memberships: list[dict[str, Any]] = field(default_factory=list)   # active rows joined with unit type/path
    grants: list[dict[str, Any]] = field(default_factory=list)
    session_kind: str = "web"
    is_demo: bool = False
    holder_ids: list[str] = field(default_factory=list)                # holders this user owns

    # ------------------------------------------------------------------ derived
    @property
    def is_user(self) -> bool:
        return self.kind == "user"

    @property
    def is_admin(self) -> bool:
        return self.kind == "system" or any(m["role"] == "org_admin" for m in self.memberships)

    @property
    def is_system(self) -> bool:
        return self.kind in ("system", "worker")

    @property
    def roles(self) -> set[str]:
        return {m["role"] for m in self.memberships}

    @property
    def highest_level(self) -> str:
        """The most senior organizational level this principal holds (for the default workspace)."""
        best = "employee"
        for m in self.memberships:
            lvl = ROLE_LEVEL.get(m["role"], "employee")
            if lvl == "admin":
                continue
            if LEVEL_ORDER.index(lvl) > LEVEL_ORDER.index(best):
                best = lvl
        return best

    @property
    def member_unit_ids(self) -> set[str]:
        return {m["unit_id"] for m in self.memberships if m["role"] != "org_admin"}

    @property
    def led_unit_ids(self) -> set[str]:
        return {m["unit_id"] for m in self.memberships if m["role"] in LEAD_ROLES}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id, "kind": self.kind, "id": self.id, "name": self.name, "is_admin": self.is_admin,
            "is_demo": self.is_demo, "session_kind": self.session_kind, "highest_level": self.highest_level,
            "roles": sorted(self.roles), "holder_ids": list(self.holder_ids),
            "memberships": [{"unit_id": m["unit_id"], "role": m["role"], "unit_type": m.get("unit_type"), "unit_name": m.get("unit_name"),
                             "level": LEVEL_FOR_UNIT_TYPE.get(m.get("unit_type", ""), m.get("unit_type"))} for m in self.memberships],
        }


class Authorizer:
    def __init__(self, db: CoordDB, org: OrgService) -> None:
        self.db = db
        self.org = org
        self._scope_cache: dict[tuple[str, int], tuple[set[str], set[str]]] = {}
        self._taxonomy_cache: dict[str, tuple[tuple[Any, ...], Any]] = {}

    # ------------------------------------------------------------------ principals
    def principal_for_user(self, user_id: str, *, session_kind: str = "web") -> Principal | None:
        u = self.org.get_user_row(user_id)
        if u is None or u["status"] != "active":
            return None
        mems = self.org.memberships_for_user(user_id)
        unit_ids = [m["unit_id"] for m in mems]
        grants = self.org.grants_for(user_id=user_id, unit_ids=unit_ids)
        holders = [h["holder_id"] for h in self.org.holders_for_user(user_id)]
        return Principal(tenant_id=u["tenant_id"], kind="user", id=user_id, name=u["name"], memberships=mems, grants=grants,
                         session_kind=session_kind, is_demo=bool(u["is_demo"]), holder_ids=holders)

    def principal_for_holder(self, holder: Mapping[str, Any]) -> Principal:
        return Principal(tenant_id=holder["tenant_id"], kind="holder", id=holder["holder_id"], name=holder["name"], session_kind="api")

    def principal_for_system(self, tenant_id: str, *, worker_id: str = "system") -> Principal:
        return Principal(tenant_id=tenant_id, kind="worker" if worker_id != "system" else "system", id=worker_id, name=worker_id, session_kind="internal")

    def principal_for_loop(self, tenant_id: str, goal: Mapping[str, Any]) -> Principal:
        """The loop acts *on behalf of the goal's owner*: it may see and route only what the owner may."""
        if goal.get("owner_type") == "user":
            p = self.principal_for_user(goal["owner_id"], session_kind="loop")
            if p is not None and p.tenant_id == tenant_id:
                p.kind = "loop"
                return p
            return Principal(tenant_id=tenant_id, kind="loop", id=f"goal:{goal['goal_id']}", name="loop", session_kind="loop")
        # unit-owned goal: acts as a lead of that unit
        unit = self.org.get_unit(goal["owner_id"])
        role = "executive" if unit and unit["type"] == "executive" else "team_lead"
        mem = [{"unit_id": goal["owner_id"], "role": role, "unit_type": unit["type"] if unit else "team", "unit_name": unit["name"] if unit else "",
                "unit_path": unit["path"] if unit else ""}]
        return Principal(tenant_id=tenant_id, kind="loop", id=f"goal:{goal['goal_id']}", name="loop", memberships=mem, session_kind="loop")

    # ------------------------------------------------------------------ scope sets
    def _scopes(self, p: Principal) -> tuple[set[str], set[str]]:
        """(visible_unit_ids, led_closure): units whose 'unit'-visibility knowledge the principal may see, and
        the closure of units it leads (self + descendants)."""
        # data_version changes when another connection (worker, second API process) commits, so a membership or
        # hierarchy change made elsewhere invalidates this cache too
        key = (f"{p.kind}:{p.id}:{sorted((m['unit_id'], m['role']) for m in p.memberships)!r}", self.db.revision, self.db.data_version())
        hit = self._scope_cache.get(key)
        if hit is not None:
            return hit
        if len(self._scope_cache) > 2000:
            self._scope_cache.clear()
        visible: set[str] = set()
        led: set[str] = set()
        for m in p.memberships:
            if m["role"] == "org_admin":
                continue
            visible.add(m["unit_id"])
            if m["role"] in LEAD_ROLES:
                desc = set(self.org.descendants(m["unit_id"]))
                led |= desc
                visible |= desc
            if m.get("unit_type") == "project":
                visible |= set(self.org.project_scope(m["unit_id"]))
        # projects spanning any visible unit are visible as scopes too
        visible |= set(self.org.projects_spanning(list(visible)))
        out = (visible, led)
        self._scope_cache[key] = out
        return out

    def visible_unit_ids(self, p: Principal) -> set[str]:
        if p.is_system:
            return set(u["unit_id"] for u in self.org.list_units(p.tenant_id))
        return self._scopes(p)[0]

    def led_unit_ids(self, p: Principal) -> set[str]:
        if p.is_system:
            return set(u["unit_id"] for u in self.org.list_units(p.tenant_id))
        return self._scopes(p)[1]

    def levels_for(self, p: Principal) -> list[str]:
        """Workspace levels this principal may open: employee always; each level it leads; admin."""
        out = ["employee"]
        for m in p.memberships:
            lvl = ROLE_LEVEL.get(m["role"])
            if lvl and lvl not in out:
                out.append(lvl)
        return out

    def _granted_ids(self, p: Principal, resource_type: str, *, min_level: str = "read") -> set[str]:
        order = {"read": 0, "artifact": 1, "raw": 2}
        need = order.get(min_level, 0)
        return {g["resource_id"] for g in p.grants if g["resource_type"] == resource_type and order.get(g["level"], 0) >= need}

    # ------------------------------------------------------------------ generic visibility
    def tenant_taxonomy(self, tenant_id: str | None) -> Any:
        """The tenant's domain taxonomy from the coordination DB (``domain_taxonomy`` / ``domain_aliases``), or the
        default taxonomy when the tenant has not configured one. Cached until the database changes."""
        from .ingest.domains import Domain, Taxonomy, default_taxonomy
        key = (tenant_id or "", self.db.revision, self.db.data_version())
        hit = self._taxonomy_cache.get(tenant_id or "")
        if hit is not None and hit[0] == key:
            return hit[1]
        rows = self.db.all("SELECT domain_id, parent_id, name, description, status FROM domain_taxonomy WHERE tenant_id=?", (tenant_id,)) if tenant_id else []
        if rows:
            aliases = {r["alias"]: r["domain_id"] for r in self.db.all("SELECT alias, domain_id FROM domain_aliases WHERE tenant_id=?", (tenant_id,))}
            tax = Taxonomy([Domain(r["domain_id"], r["name"], r["parent_id"], r["description"] or "", status=r["status"]) for r in rows], aliases)
        else:
            tax = default_taxonomy()
        self._taxonomy_cache[tenant_id or ""] = (key, tax)
        return tax

    def _domains_overlap(self, tenant_id: str | None, a: set[str], b: set[str]) -> bool:
        try:
            from .ingest.domains import domains_overlap
        except ImportError:  # pragma: no cover - a build without the ingestion package keeps exact matching
            return False
        return domains_overlap(a, b, self.tenant_taxonomy(tenant_id))

    def can_view_scoped(self, p: Principal, row: Mapping[str, Any], *, resource_type: str) -> bool:
        """Visibility rule for claims / discoveries / goals / questions (rows carry tenant_id, visibility,
        scope_unit_id and optionally owner_user_id / owner_type+owner_id / created_by_id)."""
        if row.get("tenant_id") != p.tenant_id:
            return False
        if p.is_system:
            return True
        if p.kind == "holder":
            return False
        rid = row.get(f"{resource_type}_id") or row.get("id")
        if rid and rid in self._granted_ids(p, resource_type):
            return True
        vis = row.get("visibility") or "unit"
        if vis == "org":
            return True
        owner_uid = row.get("owner_user_id") or (row.get("owner_id") if row.get("owner_type") == "user" else None)
        if owner_uid == p.id or row.get("created_by_id") == p.id or row.get("asker_id") == p.id:
            return True
        if vis == "private":
            return False
        scope = row.get("scope_unit_id")
        if scope is None:
            # scope-less artifacts belong to the owner unit when the owner is a unit
            scope = row.get("owner_id") if row.get("owner_type") == "unit" else None
        if scope is None:
            return False
        if scope in self.visible_unit_ids(p):
            return True
        # leads of an ancestor see everything below them
        led = self.led_unit_ids(p)
        return bool(led) and scope in led

    def filter_visible(self, p: Principal, rows: Iterable[Mapping[str, Any]], *, resource_type: str) -> list[dict[str, Any]]:
        return [dict(r) for r in rows if self.can_view_scoped(p, r, resource_type=resource_type)]

    def visibility_sql(self, p: Principal, *, alias: str = "", resource_type: str = "claim", owner_col: str | None = None) -> tuple[str, list[Any]]:
        """SQL predicate equivalent to :meth:`can_view_scoped` for list queries. Always includes the tenant check."""
        a = f"{alias}." if alias else ""
        args: list[Any] = [p.tenant_id]
        if p.is_system:
            return f"({a}tenant_id = ?)", args
        if p.kind == "holder":
            return "(0)", []
        parts = [f"{a}visibility = 'org'"]
        units = sorted(self.visible_unit_ids(p) | self.led_unit_ids(p))
        if units:
            parts.append(f"({a}visibility = 'unit' AND {a}scope_unit_id IN ({','.join('?' * len(units))}))")
            args.extend(units)
        oc = owner_col or ("owner_user_id" if resource_type in ("claim",) else "created_by_id")
        parts.append(f"{a}{oc} = ?"); args.append(p.id)
        granted = sorted(self._granted_ids(p, resource_type))
        if granted:
            parts.append(f"{a}{resource_type}_id IN ({','.join('?' * len(granted))})"); args.extend(granted)
        return f"({a}tenant_id = ? AND ({' OR '.join(parts)}))", args

    # ------------------------------------------------------------------ specific questions
    def can_manage_unit_knowledge(self, p: Principal, unit_id: str) -> bool:
        """Create/assign goals or accept discoveries at a unit: leads of the unit or an ancestor, or a member for team-level units."""
        if p.is_system:
            return True
        if unit_id in self.led_unit_ids(p):
            return True
        unit = self.org.get_unit(unit_id)
        return bool(unit) and unit["type"] in ("team", "project") and unit_id in p.member_unit_ids

    def can_manage_goal(self, p: Principal, goal: Mapping[str, Any]) -> bool:
        if goal.get("tenant_id") != p.tenant_id:
            return False
        if p.is_system:
            return True
        if goal.get("owner_type") == "user" and goal.get("owner_id") == p.id:
            return True
        if goal.get("created_by") == p.id:
            return True
        if goal.get("goal_id") in self._granted_ids(p, "goal", min_level="artifact"):
            return True
        scope = goal.get("scope_unit_id") or (goal.get("owner_id") if goal.get("owner_type") == "unit" else None)
        return bool(scope) and self.can_manage_unit_knowledge(p, scope)

    def can_create_goal(self, p: Principal, *, owner_type: str, owner_id: str, scope_unit_id: str | None) -> bool:
        if p.is_system:
            return True
        if owner_type == "user":
            if owner_id != p.id:
                return False
            return scope_unit_id is None or scope_unit_id in self.visible_unit_ids(p)
        return self.can_manage_unit_knowledge(p, owner_id) and (scope_unit_id is None or scope_unit_id == owner_id or scope_unit_id in self.led_unit_ids(p))

    def can_respond_question(self, p: Principal, question: Mapping[str, Any]) -> bool:
        """A person may answer a question routed to a holder they own, or one asked in a unit they belong to."""
        if question.get("tenant_id") != p.tenant_id:
            return False
        if p.is_system:
            return True
        if not self.can_view_scoped(p, question, resource_type="question"):
            return False
        return True

    def can_view_raw_evidence(self, p: Principal, holder: Mapping[str, Any]) -> bool:
        if holder.get("tenant_id") != p.tenant_id:
            return False
        if p.is_system:
            return True
        if holder.get("owner_type") == "user" and holder.get("owner_id") == p.id:
            return True
        if holder.get("owner_type") == "unit" and holder.get("owner_id") in self.led_unit_ids(p):
            return True
        return holder.get("holder_id") in self._granted_ids(p, "holder", min_level="raw")

    def can_search_holder(self, p: Principal, holder: Mapping[str, Any]) -> bool:
        """Search a holder's memory directly (private memory workspace): owner, raw/artifact grant, or unit lead of a unit-owned holder."""
        if holder.get("tenant_id") != p.tenant_id:
            return False
        if p.is_system:
            return True
        if holder.get("owner_type") == "user" and holder.get("owner_id") == p.id:
            return True
        if holder.get("owner_type") == "unit" and (holder.get("owner_id") in self.led_unit_ids(p) or holder.get("owner_id") in p.member_unit_ids):
            return True
        return holder.get("holder_id") in self._granted_ids(p, "holder", min_level="artifact")

    def can_view_evidence_ref(self, p: Principal, ref: Mapping[str, Any], *, via_claim_visible: bool = False) -> bool:
        """The policy-approved disclosure of a reference is visible with any visible claim that cites it, to the holder's
        owner, or through a grant on the holder or the reference."""
        if ref.get("tenant_id") != p.tenant_id:
            return False
        if p.is_system or via_claim_visible:
            return True
        holder = self.org.get_holder(ref["holder_id"])
        if holder and self.can_search_holder(p, holder):
            return True
        return ref.get("ref_id") in self._granted_ids(p, "evidence_ref")

    def can_route(self, question: Mapping[str, Any], holder: Mapping[str, Any], *, asker: Principal | None = None) -> tuple[bool, str]:
        """May this question be delivered to this holder? Re-checked at route time *and* by the holder at use time."""
        if holder.get("tenant_id") != question.get("tenant_id"):
            return False, "tenant mismatch"
        if holder.get("status") == "revoked":
            return False, "holder revoked"
        scope = question.get("scope_unit_id")
        policy = holder.get("export_policy") or {}
        answer_scopes = set(policy.get("answer_scopes", ["unit", "org"]))
        vis = (question.get("policy") or {}).get("visibility", "unit")
        if vis not in answer_scopes:
            return False, f"holder policy does not answer {vis}-scoped questions"
        if holder.get("owner_type") == "user":
            owner = self.org.get_user_row(holder["owner_id"])
            if owner is None or owner["status"] != "active" or owner["tenant_id"] != holder.get("tenant_id"):
                return False, "holder owner is not an active member of the tenant"
            owner_units = {m["unit_id"] for m in self.org.memberships_for_user(holder["owner_id"])}
            if not owner_units:
                return False, "holder owner has no active membership"
            if scope:
                closure = set(self.org.descendants(scope))
                closure |= set(self.org.project_scope(scope)) if scope else set()
                for pr in self.org.projects_spanning([scope]):
                    closure.add(pr)
                if not (owner_units & closure) and vis != "org":
                    return False, "holder owner is outside the question's scope"
        elif holder.get("owner_type") == "unit":
            if scope and holder["owner_id"] not in set(self.org.descendants(scope)) and vis != "org":
                return False, "unit holder is outside the question's scope"
        domains = set(holder.get("domains") or [])
        wanted = set(question.get("candidate_domains") or [])
        # nested domains match through the tenant taxonomy (a question about `engineering` reaches a holder whose
        # records are in `engineering.dependencies`; legacy flat names resolve through aliases)
        if domains and wanted and "*" not in domains and not (domains & wanted) and not self._domains_overlap(question.get("tenant_id") or holder.get("tenant_id"), domains, wanted):
            return False, "no matching evidence domain"
        if asker is not None and asker.kind in ("user", "loop") and not asker.is_system:
            # the asker must themselves be allowed to see the question's scope
            if scope and scope not in (self.visible_unit_ids(asker) | self.led_unit_ids(asker)):
                return False, "asker cannot see the scope"
        return True, "ok"

    def unit_level(self, unit_id: str | None) -> str:
        if not unit_id:
            return "employee"
        u = self.org.get_unit(unit_id)
        return LEVEL_FOR_UNIT_TYPE.get(u["type"], "team") if u else "team"

    # ------------------------------------------------------------------ admin
    def require_admin(self, p: Principal) -> None:
        if not p.is_admin:
            raise Forbidden("admin", reason="organization administrator role required")

    def require(self, ok: bool, action: str, resource: str = "", reason: str = "not authorized") -> None:
        if not ok:
            raise Forbidden(action, resource, reason)

    async def audit_denial(self, p: Principal, exc: Forbidden, *, request_id: str | None = None) -> None:
        await self.db.audit(p.tenant_id, p.kind, p.id, exc.action, resource_type=None, resource_id=exc.resource or None,
                            outcome="deny", detail={"reason": exc.reason, "at": now_iso()}, request_id=request_id)

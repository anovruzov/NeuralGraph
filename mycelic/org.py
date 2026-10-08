"""Tenants, users, organizational units, memberships, grants, holders and tenant policies.

Data access only; authorization decisions live in :mod:`mycelic.authz` and are made by the callers
(API handlers, workers) before these methods are invoked. Every mutation writes an audit row and, where
the UI cares, an outbox event, inside the same transaction.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .db.coord import CoordDB, row_to_dict, rows_to_dicts
from .util import hmac_sign, j, jl, new_id, now_iso, plus_seconds, token, token_hash

UNIT_TYPES = ("executive", "region", "subsidiary", "department", "team", "project")
HIERARCHY = ("executive", "region", "subsidiary", "department", "team")   # top -> bottom; any level may be omitted
ROLES = ("employee", "team_lead", "department_lead", "subsidiary_lead", "regional_lead", "executive", "org_admin")
MAX_HOLDER_DOMAINS = 64                   # routable domains per holder (published from ingestion or set by hand)
LEAD_ROLES = frozenset({"team_lead", "department_lead", "subsidiary_lead", "regional_lead", "executive"})
ROLE_FOR_UNIT_TYPE = {"team": "team_lead", "department": "department_lead", "subsidiary": "subsidiary_lead",
                      "region": "regional_lead", "executive": "executive", "project": "team_lead"}
LEVEL_FOR_UNIT_TYPE = {"team": "team", "project": "team", "department": "department", "subsidiary": "subsidiary",
                       "region": "region", "executive": "executive"}

DEFAULT_POLICIES: dict[str, Any] = {
    "min_independent_roots": 2,          # roots needed for 'supported'
    "freshness_days": 90,                # evidence older than this makes a claim 'stale'
    "default_goal_budget": {"tokens": 200000, "usd": 5.0, "questions": 40, "followup_depth": 3},
    "priority_weights": {"goal_value": 1.0, "uncertainty": 0.8, "impact": 0.8, "missing_evidence": 0.6,
                         "information_gain": 0.7, "cost": 0.5},
    "model_tiers": {"question_draft": "light", "classify": "light", "evaluate": "standard",
                    "verify": "standard", "synthesize": "heavy", "chat": "standard"},
    "holder_default_export": {"disclosure": "excerpt", "max_excerpt_chars": 480, "answer_scopes": ["unit", "org"]},
}

_USER_FIELDS = ("user_id", "tenant_id", "email", "name", "status", "is_demo", "created_at", "updated_at")


def _public_user(r: sqlite3.Row | dict | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    return {k: d.get(k) for k in _USER_FIELDS}


class OrgService:
    def __init__(self, db: CoordDB, *, secret_key: str = "") -> None:
        self.db = db
        self.secret_key = secret_key

    def route_key(self, holder_id: str) -> str:
        """The HMAC key that signs core -> holder envelopes (and holder -> core replies).

        Derived from the server secret (``MYCELIC_SECRET_KEY``) so a copy of the coordination database alone
        cannot forge envelopes; a holder receives it once, over its authenticated bootstrap call. Without a server
        secret (tests, throwaway dev runs) the random per-holder value stored at registration is used instead.
        """
        r = self.db.one("SELECT route_key FROM holders WHERE holder_id=?", (holder_id,))
        salt = r["route_key"] if r else ""
        if self.secret_key:
            # the per-holder random salt rotates with the holder's key, so a leaked signing key dies with the rotation
            return hmac_sign(self.secret_key, f"mycelic:route:{holder_id}:{salt}")
        return salt

    # ------------------------------------------------------------------ tenants
    async def create_tenant(self, name: str, slug: str, *, is_demo: bool = False, settings: dict | None = None,
                            tenant_id: str | None = None) -> dict[str, Any]:
        tid = tenant_id or new_id("ten")
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("INSERT INTO tenants(tenant_id, slug, name, is_demo, settings, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (tid, slug, name, int(is_demo), j(settings), now))
            for k, v in DEFAULT_POLICIES.items():
                c.execute("INSERT INTO tenant_policies(tenant_id, key, value, updated_at) VALUES (?, ?, ?, ?)", (tid, k, j(v), now))
            self.db.audit_sync(c, tid, "system", None, "tenant.create", resource_type="tenant", resource_id=tid)
        return self.get_tenant(tid)  # type: ignore[return-value]

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)), json_fields=("settings",))

    def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM tenants WHERE slug=?", (slug,)), json_fields=("settings",))

    def list_tenants(self) -> list[dict[str, Any]]:
        return rows_to_dicts(self.db.all("SELECT * FROM tenants ORDER BY created_at"), json_fields=("settings",))

    def policies(self, tenant_id: str) -> dict[str, Any]:
        out = dict(DEFAULT_POLICIES)
        for r in self.db.all("SELECT key, value FROM tenant_policies WHERE tenant_id=?", (tenant_id,)):
            out[r["key"]] = jl(r["value"], None)
        return out

    def policy(self, tenant_id: str, key: str, default: Any = None) -> Any:
        r = self.db.one("SELECT value FROM tenant_policies WHERE tenant_id=? AND key=?", (tenant_id, key))
        if r is None:
            return DEFAULT_POLICIES.get(key, default)
        return jl(r["value"], default)

    async def set_policy(self, tenant_id: str, key: str, value: Any, *, actor_id: str | None) -> None:
        async with self.db.tx() as c:
            c.execute("INSERT INTO tenant_policies(tenant_id, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(tenant_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                      (tenant_id, key, j(value), now_iso(), actor_id))
            self.db.audit_sync(c, tenant_id, "user", actor_id, "policy.set", resource_type="policy", resource_id=key, detail={"value": value})
            self.db.emit_sync(c, tenant_id, "policy.updated", ref_type="policy", ref_id=key, payload={"key": key}, audience={"roles": ["org_admin"]})

    # ------------------------------------------------------------------ users
    async def create_user(self, tenant_id: str, email: str, name: str, *, password_hash: str | None = None,
                          is_demo: bool = False, user_id: str | None = None) -> dict[str, Any]:
        uid = user_id or new_id("usr")
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("INSERT INTO users(user_id, tenant_id, email, name, password_hash, status, is_demo, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)", (uid, tenant_id, email.strip().lower(), name.strip(), password_hash, int(is_demo), now, now))
            self.db.audit_sync(c, tenant_id, "system", None, "user.create", resource_type="user", resource_id=uid, detail={"email": email})
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return _public_user(self.db.one("SELECT * FROM users WHERE user_id=?", (user_id,)))

    def get_user_row(self, user_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM users WHERE user_id=?", (user_id,))

    def find_user(self, tenant_id: str, email: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM users WHERE tenant_id=? AND email=?", (tenant_id, email.strip().lower()))

    def find_user_any_tenant(self, email: str) -> list[sqlite3.Row]:
        return self.db.all("SELECT * FROM users WHERE email=? ORDER BY created_at", (email.strip().lower(),))

    def list_users(self, tenant_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM users WHERE tenant_id=?", [tenant_id]
        if status:
            sql += " AND status=?"; args.append(status)
        sql += " ORDER BY name"
        return [_public_user(r) for r in self.db.all(sql, args)]  # type: ignore[misc]

    async def set_user_status(self, user_id: str, status: str, *, actor_id: str | None) -> None:
        async with self.db.tx() as c:
            r = c.execute("SELECT tenant_id FROM users WHERE user_id=?", (user_id,)).fetchone()
            if r is None:
                raise KeyError(user_id)
            c.execute("UPDATE users SET status=?, updated_at=? WHERE user_id=?", (status, now_iso(), user_id))
            if status != "active":
                c.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now_iso(), user_id))
                c.execute("UPDATE api_keys SET revoked_at=? WHERE principal_type='user' AND principal_id=? AND revoked_at IS NULL", (now_iso(), user_id))
                c.execute("UPDATE question_routes SET status='revoked', error='owner disabled' WHERE status IN ('pending','delivered') "
                          "AND holder_id IN (SELECT holder_id FROM holders WHERE owner_type='user' AND owner_id=?)", (user_id,))
            self.db.audit_sync(c, r["tenant_id"], "user", actor_id, "user.status", resource_type="user", resource_id=user_id, detail={"status": status})

    async def set_password_hash(self, user_id: str, password_hash: str) -> None:
        async with self.db.tx() as c:
            c.execute("UPDATE users SET password_hash=?, updated_at=? WHERE user_id=?", (password_hash, now_iso(), user_id))

    # ------------------------------------------------------------------ units
    async def create_unit(self, tenant_id: str, type: str, name: str, *, parent_id: str | None = None,
                          is_demo: bool = False, settings: dict | None = None, unit_id: str | None = None,
                          actor_id: str | None = None) -> dict[str, Any]:
        if type not in UNIT_TYPES:
            raise ValueError(f"unknown unit type {type!r}")
        uid = unit_id or new_id("unit")
        now = now_iso()
        async with self.db.tx() as c:
            if parent_id:
                p = c.execute("SELECT path, depth, tenant_id FROM org_units WHERE unit_id=?", (parent_id,)).fetchone()
                if p is None or p["tenant_id"] != tenant_id:
                    raise ValueError("parent unit not found in tenant")
                path, depth = f"{p['path']}{uid}/", int(p["depth"]) + 1
            else:
                path, depth = f"/{uid}/", 0
            c.execute("INSERT INTO org_units(unit_id, tenant_id, type, name, parent_id, path, depth, is_demo, settings, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (uid, tenant_id, type, name.strip(), parent_id, path, depth, int(is_demo), j(settings), now))
            self.db.audit_sync(c, tenant_id, "user" if actor_id else "system", actor_id, "unit.create", resource_type="unit", resource_id=uid,
                               detail={"type": type, "name": name, "parent_id": parent_id})
            self.db.emit_sync(c, tenant_id, "org.changed", ref_type="unit", ref_id=uid, payload={"action": "create"}, audience={"visibility": "org"})
        return self.get_unit(uid)  # type: ignore[return-value]

    def get_unit(self, unit_id: str) -> dict[str, Any] | None:
        d = row_to_dict(self.db.one("SELECT * FROM org_units WHERE unit_id=?", (unit_id,)), json_fields=("settings",))
        if d:
            d["level"] = LEVEL_FOR_UNIT_TYPE.get(d["type"], d["type"])
        return d

    def list_units(self, tenant_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM org_units WHERE tenant_id=?" + ("" if include_archived else " AND archived_at IS NULL") + " ORDER BY depth, name"
        out = rows_to_dicts(self.db.all(sql, (tenant_id,)), json_fields=("settings",))
        for d in out:
            d["level"] = LEVEL_FOR_UNIT_TYPE.get(d["type"], d["type"])
        return out

    def descendants(self, unit_id: str, *, include_self: bool = True) -> list[str]:
        r = self.db.one("SELECT path FROM org_units WHERE unit_id=?", (unit_id,))
        if r is None:
            return []
        rows = self.db.all("SELECT unit_id FROM org_units WHERE path LIKE ? AND archived_at IS NULL", (r["path"] + "%",))
        ids = [x["unit_id"] for x in rows]
        return ids if include_self else [i for i in ids if i != unit_id]

    def ancestors(self, unit_id: str, *, include_self: bool = False) -> list[str]:
        r = self.db.one("SELECT path FROM org_units WHERE unit_id=?", (unit_id,))
        if r is None:
            return []
        parts = [p for p in r["path"].split("/") if p]
        return parts if include_self else parts[:-1]

    def project_scope(self, project_id: str) -> list[str]:
        return [r["unit_id"] for r in self.db.all("SELECT unit_id FROM project_scopes WHERE project_id=?", (project_id,))]

    def projects_spanning(self, unit_ids: Iterable[str]) -> list[str]:
        ids = list(dict.fromkeys(unit_ids))
        if not ids:
            return []
        rows = self.db.all(f"SELECT DISTINCT project_id FROM project_scopes WHERE unit_id IN ({','.join('?' * len(ids))})", ids)
        return [r["project_id"] for r in rows]

    async def set_project_scope(self, project_id: str, unit_ids: Iterable[str], *, actor_id: str | None = None) -> None:
        async with self.db.tx() as c:
            p = c.execute("SELECT tenant_id, type FROM org_units WHERE unit_id=?", (project_id,)).fetchone()
            if p is None or p["type"] != "project":
                raise ValueError("not a project unit")
            c.execute("DELETE FROM project_scopes WHERE project_id=?", (project_id,))
            c.executemany("INSERT OR IGNORE INTO project_scopes(project_id, unit_id) VALUES (?, ?)", [(project_id, u) for u in dict.fromkeys(unit_ids)])
            self.db.audit_sync(c, p["tenant_id"], "user" if actor_id else "system", actor_id, "project.scope", resource_type="unit", resource_id=project_id,
                               detail={"unit_ids": list(unit_ids)})

    async def update_unit(self, unit_id: str, *, name: str | None = None, parent_id: str | None = ..., archived: bool | None = None,  # type: ignore[assignment]
                          actor_id: str | None = None) -> dict[str, Any]:
        async with self.db.tx() as c:
            r = c.execute("SELECT * FROM org_units WHERE unit_id=?", (unit_id,)).fetchone()
            if r is None:
                raise KeyError(unit_id)
            if name:
                c.execute("UPDATE org_units SET name=? WHERE unit_id=?", (name.strip(), unit_id))
            if parent_id is not ...:
                if parent_id == unit_id or (parent_id and parent_id in self.descendants(unit_id)):
                    raise ValueError("cannot move a unit under itself")
                if parent_id:
                    p = c.execute("SELECT path, depth FROM org_units WHERE unit_id=? AND tenant_id=?", (parent_id, r["tenant_id"])).fetchone()
                    if p is None:
                        raise ValueError("parent not found")
                    new_path, new_depth = f"{p['path']}{unit_id}/", int(p["depth"]) + 1
                else:
                    new_path, new_depth = f"/{unit_id}/", 0
                old_path = r["path"]
                rows = c.execute("SELECT unit_id, path, depth FROM org_units WHERE path LIKE ?", (old_path + "%",)).fetchall()
                for d in rows:
                    c.execute("UPDATE org_units SET path=?, depth=?, parent_id=CASE WHEN unit_id=? THEN ? ELSE parent_id END WHERE unit_id=?",
                              (new_path + d["path"][len(old_path):], new_depth + (int(d["depth"]) - int(r["depth"])), unit_id, parent_id, d["unit_id"]))
            if archived is not None:
                c.execute("UPDATE org_units SET archived_at=? WHERE unit_id=?", (now_iso() if archived else None, unit_id))
            self.db.audit_sync(c, r["tenant_id"], "user" if actor_id else "system", actor_id, "unit.update", resource_type="unit", resource_id=unit_id,
                               detail={"name": name, "parent_id": None if parent_id is ... else parent_id, "archived": archived})
            self.db.emit_sync(c, r["tenant_id"], "org.changed", ref_type="unit", ref_id=unit_id, payload={"action": "update"}, audience={"visibility": "org"})
        return self.get_unit(unit_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------ memberships
    async def add_membership(self, tenant_id: str, user_id: str, unit_id: str, role: str, *, granted_by: str | None = None) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        mid = new_id("mem")
        now = now_iso()
        async with self.db.tx() as c:
            u = c.execute("SELECT tenant_id FROM org_units WHERE unit_id=?", (unit_id,)).fetchone()
            usr = c.execute("SELECT tenant_id FROM users WHERE user_id=?", (user_id,)).fetchone()
            if u is None or usr is None or u["tenant_id"] != tenant_id or usr["tenant_id"] != tenant_id:
                raise ValueError("user and unit must belong to the tenant")
            c.execute("INSERT INTO memberships(membership_id, tenant_id, user_id, unit_id, role, status, granted_by, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?) "
                      "ON CONFLICT(user_id, unit_id, role) DO UPDATE SET status='active', revoked_at=NULL, granted_by=excluded.granted_by",
                      (mid, tenant_id, user_id, unit_id, role, granted_by, now))
            self.db.audit_sync(c, tenant_id, "user" if granted_by else "system", granted_by, "membership.add", resource_type="membership",
                               resource_id=f"{user_id}:{unit_id}:{role}", detail={"user_id": user_id, "unit_id": unit_id, "role": role})
            self.db.emit_sync(c, tenant_id, "membership.changed", ref_type="user", ref_id=user_id, payload={"unit_id": unit_id, "role": role, "action": "add"},
                              audience={"user_ids": [user_id], "roles": ["org_admin"], "unit_ids": [unit_id]})
        r = self.db.one("SELECT * FROM memberships WHERE user_id=? AND unit_id=? AND role=?", (user_id, unit_id, role))
        return row_to_dict(r)  # type: ignore[return-value]

    async def revoke_membership(self, user_id: str, unit_id: str, role: str | None = None, *, actor_id: str | None = None) -> int:
        now = now_iso()
        async with self.db.tx() as c:
            r = c.execute("SELECT tenant_id FROM users WHERE user_id=?", (user_id,)).fetchone()
            if r is None:
                return 0
            if role:
                cur = c.execute("UPDATE memberships SET status='revoked', revoked_at=? WHERE user_id=? AND unit_id=? AND role=? AND status='active'",
                                (now, user_id, unit_id, role))
            else:
                cur = c.execute("UPDATE memberships SET status='revoked', revoked_at=? WHERE user_id=? AND unit_id=? AND status='active'", (now, user_id, unit_id))
            n = cur.rowcount
            if n:
                self.db.audit_sync(c, r["tenant_id"], "user" if actor_id else "system", actor_id, "membership.revoke", resource_type="membership",
                                   resource_id=f"{user_id}:{unit_id}:{role or '*'}")
                self.db.emit_sync(c, r["tenant_id"], "membership.changed", ref_type="user", ref_id=user_id,
                                  payload={"unit_id": unit_id, "role": role, "action": "revoke"}, audience={"user_ids": [user_id], "roles": ["org_admin"], "unit_ids": [unit_id]})
        return n

    def memberships_for_user(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT m.*, u.type AS unit_type, u.name AS unit_name, u.path AS unit_path FROM memberships m "
                           "JOIN org_units u ON u.unit_id = m.unit_id WHERE m.user_id=? AND m.status='active' AND u.archived_at IS NULL", (user_id,))
        return rows_to_dicts(rows)

    def members_of(self, unit_id: str, *, include_descendants: bool = False) -> list[dict[str, Any]]:
        ids = self.descendants(unit_id) if include_descendants else [unit_id]
        if not ids:
            return []
        rows = self.db.all(f"SELECT m.*, us.name AS user_name, us.email FROM memberships m JOIN users us ON us.user_id = m.user_id "
                           f"WHERE m.unit_id IN ({','.join('?' * len(ids))}) AND m.status='active' AND us.status='active' ORDER BY us.name", ids)
        return rows_to_dicts(rows)

    def list_memberships(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT m.*, us.name AS user_name, us.email, u.name AS unit_name, u.type AS unit_type FROM memberships m "
                           "JOIN users us ON us.user_id=m.user_id JOIN org_units u ON u.unit_id=m.unit_id WHERE m.tenant_id=? ORDER BY us.name", (tenant_id,))
        return rows_to_dicts(rows)

    # ------------------------------------------------------------------ grants
    async def add_grant(self, tenant_id: str, *, grantor_id: str, grantee_type: str, grantee_id: str, resource_type: str,
                        resource_id: str, level: str = "read", reason: str = "", expires_in_seconds: float | None = None) -> dict[str, Any]:
        if grantee_type not in ("user", "unit"):
            raise ValueError("grantee_type must be user or unit")
        if level not in ("read", "artifact", "raw"):
            raise ValueError("level must be read, artifact or raw")
        table, col = ("users", "user_id") if grantee_type == "user" else ("org_units", "unit_id")
        if self.db.one(f"SELECT 1 FROM {table} WHERE {col}=? AND tenant_id=?", (grantee_id, tenant_id)) is None:
            raise ValueError("grantee is not part of this organization")
        res_table = {"holder": ("holders", "holder_id"), "claim": ("claims", "claim_id"), "discovery": ("discoveries", "discovery_id"),
                     "goal": ("goals", "goal_id"), "evidence_ref": ("evidence_refs", "ref_id")}.get(resource_type)
        if res_table is None:
            raise ValueError("unknown resource_type")
        if self.db.one(f"SELECT 1 FROM {res_table[0]} WHERE {res_table[1]}=? AND tenant_id=?", (resource_id, tenant_id)) is None:
            raise ValueError("resource is not part of this organization")
        gid = new_id("grant")
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("INSERT INTO grants(grant_id, tenant_id, grantor_id, grantee_type, grantee_id, resource_type, resource_id, level, reason, status, created_at, expires_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                      (gid, tenant_id, grantor_id, grantee_type, grantee_id, resource_type, resource_id, level, reason, now,
                       plus_seconds(expires_in_seconds) if expires_in_seconds else None))
            self.db.audit_sync(c, tenant_id, "user", grantor_id, "grant.add", resource_type=resource_type, resource_id=resource_id,
                               detail={"grantee_type": grantee_type, "grantee_id": grantee_id, "level": level, "reason": reason})
            aud = {"user_ids": [grantor_id, grantee_id]} if grantee_type == "user" else {"user_ids": [grantor_id], "unit_ids": [grantee_id]}
            self.db.emit_sync(c, tenant_id, "grant.changed", ref_type="grant", ref_id=gid, payload={"action": "add", "resource_type": resource_type, "resource_id": resource_id}, audience=aud)
        return row_to_dict(self.db.one("SELECT * FROM grants WHERE grant_id=?", (gid,)))  # type: ignore[return-value]

    async def revoke_grant(self, grant_id: str, *, actor_id: str | None) -> bool:
        async with self.db.tx() as c:
            r = c.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
            if r is None:
                return False
            c.execute("UPDATE grants SET status='revoked', revoked_at=? WHERE grant_id=?", (now_iso(), grant_id))
            self.db.audit_sync(c, r["tenant_id"], "user", actor_id, "grant.revoke", resource_type=r["resource_type"], resource_id=r["resource_id"])
            aud = {"user_ids": [r["grantor_id"], r["grantee_id"]]} if r["grantee_type"] == "user" else {"user_ids": [r["grantor_id"]], "unit_ids": [r["grantee_id"]]}
            self.db.emit_sync(c, r["tenant_id"], "grant.changed", ref_type="grant", ref_id=grant_id, payload={"action": "revoke"}, audience=aud)
        return True

    def grants_for(self, *, user_id: str, unit_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Active, unexpired grants to this user or to any of the units they belong to, inside the user's own tenant only."""
        ids = list(dict.fromkeys(unit_ids))
        now = now_iso()
        sql = ("SELECT * FROM grants WHERE status='active' AND (expires_at IS NULL OR expires_at > ?) "
               "AND tenant_id = (SELECT tenant_id FROM users WHERE user_id=?) AND ((grantee_type='user' AND grantee_id=?)")
        args: list[Any] = [now, user_id, user_id]
        if ids:
            sql += f" OR (grantee_type='unit' AND grantee_id IN ({','.join('?' * len(ids))}))"; args.extend(ids)
        sql += ")"
        return rows_to_dicts(self.db.all(sql, args))

    def list_grants(self, tenant_id: str, *, resource_type: str | None = None, resource_id: str | None = None,
                    grantor_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM grants WHERE tenant_id=?", [tenant_id]
        if resource_type:
            sql += " AND resource_type=?"; args.append(resource_type)
        if resource_id:
            sql += " AND resource_id=?"; args.append(resource_id)
        if grantor_id:
            sql += " AND grantor_id=?"; args.append(grantor_id)
        sql += " ORDER BY created_at DESC"
        return rows_to_dicts(self.db.all(sql, args))

    # ------------------------------------------------------------------ holders registry
    async def register_holder(self, tenant_id: str, *, owner_type: str, owner_id: str, name: str, mode: str = "external",
                              domains: Iterable[str] = (), export_policy: dict | None = None, holder_id: str | None = None,
                              key: str | None = None) -> tuple[dict[str, Any], str]:
        """Create a holder record. Returns (holder, plaintext_key) — the key is shown once and stored hashed."""
        hid = holder_id or new_id("hold")
        plain = key or token(32)
        now = now_iso()
        policy = export_policy if export_policy is not None else self.policy(tenant_id, "holder_default_export", {})
        async with self.db.tx() as c:
            c.execute("INSERT INTO holders(holder_id, tenant_id, owner_type, owner_id, name, key_hash, route_key, status, mode, domains, export_policy, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, 'offline', ?, ?, ?, ?, ?)",
                      (hid, tenant_id, owner_type, owner_id, name, token_hash(plain), token(24), mode, j(list(domains)), j(policy), now, now))
            self.db.audit_sync(c, tenant_id, "system", None, "holder.register", resource_type="holder", resource_id=hid,
                               detail={"owner_type": owner_type, "owner_id": owner_id, "mode": mode})
        return self.get_holder(hid), plain  # type: ignore[return-value]

    def get_holder(self, holder_id: str) -> dict[str, Any] | None:
        d = row_to_dict(self.db.one("SELECT * FROM holders WHERE holder_id=?", (holder_id,)), json_fields=("domains", "export_policy", "stats"))
        if d:
            d.pop("key_hash", None)
        return d

    def holder_secret_row(self, holder_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM holders WHERE holder_id=?", (holder_id,))

    def holder_by_key_hash(self, key_hash: str) -> dict[str, Any] | None:
        d = row_to_dict(self.db.one("SELECT h.* FROM holders h WHERE h.key_hash=? AND h.status<>'revoked' AND (h.owner_type<>'user' OR EXISTS "
                                    "(SELECT 1 FROM users u WHERE u.user_id=h.owner_id AND u.status='active' AND u.tenant_id=h.tenant_id))", (key_hash,)),
                        json_fields=("domains", "export_policy", "stats"))
        if d:
            d.pop("key_hash", None)
        return d

    def list_holders(self, tenant_id: str, *, owner_type: str | None = None, owner_id: str | None = None,
                     status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM holders WHERE tenant_id=?", [tenant_id]
        if owner_type:
            sql += " AND owner_type=?"; args.append(owner_type)
        if owner_id:
            sql += " AND owner_id=?"; args.append(owner_id)
        if status:
            sql += " AND status=?"; args.append(status)
        sql += " ORDER BY name"
        out = rows_to_dicts(self.db.all(sql, args), json_fields=("domains", "export_policy", "stats"))
        for d in out:
            d.pop("key_hash", None)
        return out

    def holders_for_user(self, user_id: str) -> list[dict[str, Any]]:
        out = rows_to_dicts(self.db.all("SELECT * FROM holders WHERE owner_type='user' AND owner_id=? AND status<>'revoked'", (user_id,)),
                            json_fields=("domains", "export_policy", "stats"))
        for d in out:
            d.pop("key_hash", None)
        return out

    @staticmethod
    def _taxonomy_ids_sync(c: sqlite3.Connection, tenant_id: str) -> set[str]:
        """The tenant's domain ids (its configured taxonomy, else the default one). Personal domains are never in it."""
        ids = {r["domain_id"] for r in c.execute("SELECT domain_id FROM domain_taxonomy WHERE tenant_id=? AND status='active' AND domain_id NOT LIKE 'personal.%'", (tenant_id,))}
        if ids or c.execute("SELECT 1 FROM domain_taxonomy WHERE tenant_id=? LIMIT 1", (tenant_id,)).fetchone():
            return ids
        from .ingest.domains import UNCLASSIFIED, default_taxonomy
        return {d for d, dom in default_taxonomy().domains.items() if dom.status == "active" and dom.scope == "tenant" and d != UNCLASSIFIED}

    async def holder_heartbeat(self, holder_id: str, *, stats: dict | None = None, status: str = "online") -> None:
        async with self.db.tx() as c:
            r = c.execute("SELECT tenant_id, status, domains, export_policy FROM holders WHERE holder_id=?", (holder_id,)).fetchone()
            if r is None or r["status"] == "revoked":
                return
            c.execute("UPDATE holders SET status=?, last_heartbeat_at=?, stats=CASE WHEN ? THEN ? ELSE stats END, updated_at=? WHERE holder_id=?",
                      (status, now_iso(), int(stats is not None), j(stats), now_iso(), holder_id))
            # domains the holder's ingested records belong to (counts only, tenant taxonomy only, at least the holder's
            # publication threshold) become routable for questions, unless the owner curates the list by hand
            reported = ((stats or {}).get("ingest") or {}).get("domains") or {}
            if reported and (jl(r["export_policy"], {}) or {}).get("auto_domains", True) is not False:
                current = list(jl(r["domains"], []) or [])
                known = self._taxonomy_ids_sync(c, r["tenant_id"])
                added = sorted(d for d in reported if isinstance(d, str) and d in known and d not in current)[:max(0, MAX_HOLDER_DOMAINS - len(current))]
                if added:
                    c.execute("UPDATE holders SET domains=? WHERE holder_id=?", (j(current + added), holder_id))
                    self.db.audit_sync(c, r["tenant_id"], "holder", holder_id, "holder.domains_published", resource_type="holder", resource_id=holder_id,
                                       detail={"added": added})
            if r["status"] != status:
                self.db.emit_sync(c, r["tenant_id"], "holder.status", ref_type="holder", ref_id=holder_id, payload={"status": status}, audience={"roles": ["org_admin"], "visibility": "org"})

    async def update_holder(self, holder_id: str, *, export_policy: dict | None = None, domains: Iterable[str] | None = None,
                            status: str | None = None, actor_id: str | None = None) -> dict[str, Any]:
        async with self.db.tx() as c:
            r = c.execute("SELECT tenant_id FROM holders WHERE holder_id=?", (holder_id,)).fetchone()
            if r is None:
                raise KeyError(holder_id)
            if export_policy is not None:
                c.execute("UPDATE holders SET export_policy=?, updated_at=? WHERE holder_id=?", (j(export_policy), now_iso(), holder_id))
            if domains is not None:
                c.execute("UPDATE holders SET domains=?, updated_at=? WHERE holder_id=?", (j(list(domains)), now_iso(), holder_id))
            if status is not None:
                c.execute("UPDATE holders SET status=?, updated_at=? WHERE holder_id=?", (status, now_iso(), holder_id))
                if status == "revoked":
                    c.execute("UPDATE question_routes SET status='revoked' WHERE holder_id=? AND status IN ('pending','delivered')", (holder_id,))
            self.db.audit_sync(c, r["tenant_id"], "user" if actor_id else "system", actor_id, "holder.update", resource_type="holder", resource_id=holder_id,
                               detail={"export_policy": export_policy, "domains": list(domains) if domains is not None else None, "status": status})
        return self.get_holder(holder_id)  # type: ignore[return-value]

    async def rotate_holder_key(self, holder_id: str, *, actor_id: str | None = None) -> str:
        plain = token(32)
        async with self.db.tx() as c:
            r = c.execute("SELECT tenant_id FROM holders WHERE holder_id=?", (holder_id,)).fetchone()
            if r is None:
                raise KeyError(holder_id)
            c.execute("UPDATE holders SET key_hash=?, route_key=?, updated_at=? WHERE holder_id=?", (token_hash(plain), token(24), now_iso(), holder_id))
            self.db.audit_sync(c, r["tenant_id"], "user" if actor_id else "system", actor_id, "holder.rotate_key", resource_type="holder", resource_id=holder_id)
        return plain

    # ------------------------------------------------------------------ audit / notifications
    def audit_events(self, tenant_id: str, *, limit: int = 100, action_prefix: str | None = None, resource_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM audit_log WHERE tenant_id=?", [tenant_id]
        if action_prefix:
            sql += " AND action LIKE ?"; args.append(action_prefix + "%")
        if resource_id:
            sql += " AND resource_id=?"; args.append(resource_id)
        sql += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
        return rows_to_dicts(self.db.all(sql, args), json_fields=("detail",))

    @staticmethod
    def notify_sync(c: sqlite3.Connection, tenant_id: str, user_id: str, kind: str, title: str, *, body: str = "",
                    ref_type: str | None = None, ref_id: str | None = None) -> None:
        c.execute("INSERT INTO notifications(tenant_id, user_id, kind, title, body, ref_type, ref_id, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (tenant_id, user_id, kind, title, body, ref_type, ref_id, now_iso()))

    def notifications_for(self, user_id: str, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM notifications WHERE user_id=?" + (" AND read_at IS NULL" if unread_only else "") + " ORDER BY id DESC LIMIT ?"
        return rows_to_dicts(self.db.all(sql, (user_id, int(limit))))

    async def mark_notifications_read(self, user_id: str, ids: Iterable[int] | None = None) -> int:
        async with self.db.tx() as c:
            if ids is None:
                return c.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (now_iso(), user_id)).rowcount
            lst = [int(i) for i in ids]
            if not lst:
                return 0
            return c.execute(f"UPDATE notifications SET read_at=? WHERE user_id=? AND id IN ({','.join('?' * len(lst))})", (now_iso(), user_id, *lst)).rowcount

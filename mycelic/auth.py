"""Authentication: passwords (scrypt, stdlib), sessions, invitations, API keys and holder keys.

Tokens are random and stored hashed; a leaked database does not yield usable credentials. Sessions carry the
tenant so a request can never switch tenants by changing a parameter.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

from .db.coord import CoordDB, row_to_dict, rows_to_dicts
from .org import OrgService, ROLES
from .util import new_id, now_iso, plus_seconds, token, token_hash

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, n, r, p, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


class AuthError(Exception):
    pass


class AuthService:
    def __init__(self, db: CoordDB, org: OrgService, *, session_ttl_seconds: int = 14 * 24 * 3600) -> None:
        self.db = db
        self.org = org
        self.session_ttl = session_ttl_seconds

    # ------------------------------------------------------------------ registration & login
    async def register_tenant(self, *, org_name: str, slug: str, admin_email: str, admin_name: str, password: str,
                              is_demo: bool = False) -> dict[str, Any]:
        """Onboarding step 1: a tenant, its executive root unit, and the first user as administrator + executive."""
        if self.org.get_tenant_by_slug(slug):
            raise AuthError("organization slug already taken")
        tenant = await self.org.create_tenant(org_name, slug, is_demo=is_demo)
        root = await self.org.create_unit(tenant["tenant_id"], "executive", org_name, is_demo=is_demo)
        user = await self.org.create_user(tenant["tenant_id"], admin_email, admin_name, password_hash=hash_password(password), is_demo=is_demo)
        await self.org.add_membership(tenant["tenant_id"], user["user_id"], root["unit_id"], "org_admin")
        await self.org.add_membership(tenant["tenant_id"], user["user_id"], root["unit_id"], "executive")
        return {"tenant": tenant, "root_unit": root, "user": user}

    async def login(self, *, email: str, password: str, tenant_slug: str | None = None, user_agent: str = "",
                    ip: str = "") -> tuple[str, dict[str, Any]]:
        rows = self.org.find_user_any_tenant(email)
        if tenant_slug:
            t = self.org.get_tenant_by_slug(tenant_slug)
            rows = [r for r in rows if t and r["tenant_id"] == t["tenant_id"]]
        cand = [r for r in rows if verify_password(password, r["password_hash"]) and r["status"] == "active"]
        if not cand:
            await self.db.audit(None, "user", None, "auth.login", outcome="deny", detail={"email": email})
            raise AuthError("invalid credentials")
        user = cand[0]
        tok = await self.create_session(user["user_id"], user["tenant_id"], kind="web", user_agent=user_agent, ip=ip)
        await self.db.audit(user["tenant_id"], "user", user["user_id"], "auth.login", outcome="ok")
        return tok, row_to_dict(user)  # type: ignore[return-value]

    async def create_session(self, user_id: str, tenant_id: str, *, kind: str = "web", user_agent: str = "", ip: str = "",
                             ttl_seconds: int | None = None) -> str:
        tok = token(32)
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("INSERT INTO sessions(session_id, token_hash, tenant_id, user_id, kind, created_at, expires_at, last_seen_at, user_agent, ip) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (new_id("ses"), token_hash(tok), tenant_id, user_id, kind, now, plus_seconds(ttl_seconds or self.session_ttl), now, user_agent[:300], ip[:64]))
        return tok

    def resolve_session(self, tok: str | None) -> dict[str, Any] | None:
        if not tok:
            return None
        r = self.db.one("SELECT * FROM sessions WHERE token_hash=?", (token_hash(tok),))
        if r is None or r["revoked_at"] or r["expires_at"] <= now_iso():
            return None
        return row_to_dict(r)

    async def touch_session(self, session_id: str) -> None:
        async with self.db.tx() as c:
            c.execute("UPDATE sessions SET last_seen_at=? WHERE session_id=?", (now_iso(), session_id))

    async def logout(self, tok: str | None) -> bool:
        if not tok:
            return False
        async with self.db.tx() as c:
            return c.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (now_iso(), token_hash(tok))).rowcount == 1

    async def revoke_user_sessions(self, user_id: str) -> int:
        async with self.db.tx() as c:
            return c.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now_iso(), user_id)).rowcount

    # ------------------------------------------------------------------ invitations
    async def invite(self, tenant_id: str, *, email: str, role: str, unit_id: str | None, invited_by: str,
                     ttl_seconds: int = 7 * 24 * 3600) -> tuple[str, dict[str, Any]]:
        if role not in ROLES:
            raise ValueError("unknown role")
        tok = token(24)
        iid = new_id("inv")
        async with self.db.tx() as c:
            c.execute("INSERT INTO invitations(invitation_id, tenant_id, email, token_hash, role, unit_id, invited_by, status, created_at, expires_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                      (iid, tenant_id, email.strip().lower(), token_hash(tok), role, unit_id, invited_by, now_iso(), plus_seconds(ttl_seconds)))
            self.db.audit_sync(c, tenant_id, "user", invited_by, "invitation.create", resource_type="invitation", resource_id=iid,
                               detail={"email": email, "role": role, "unit_id": unit_id})
        return tok, row_to_dict(self.db.one("SELECT * FROM invitations WHERE invitation_id=?", (iid,)))  # type: ignore[return-value]

    def get_invitation(self, tok: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM invitations WHERE token_hash=?", (token_hash(tok),))
        if r is None:
            return None
        d = row_to_dict(r)
        if d["status"] == "pending" and d["expires_at"] <= now_iso():
            d["status"] = "expired"
        return d

    async def accept_invitation(self, tok: str, *, name: str, password: str) -> tuple[str, dict[str, Any]]:
        inv = self.get_invitation(tok)
        if inv is None or inv["status"] != "pending":
            raise AuthError("invitation is not valid")
        existing = self.org.find_user(inv["tenant_id"], inv["email"])
        if existing is not None:
            user = row_to_dict(existing)
            if not user["password_hash"]:
                await self.org.set_password_hash(user["user_id"], hash_password(password))
        else:
            user = await self.org.create_user(inv["tenant_id"], inv["email"], name, password_hash=hash_password(password))
        if inv["unit_id"]:
            await self.org.add_membership(inv["tenant_id"], user["user_id"], inv["unit_id"], inv["role"], granted_by=inv["invited_by"])
        async with self.db.tx() as c:
            c.execute("UPDATE invitations SET status='accepted', accepted_at=?, accepted_user_id=? WHERE invitation_id=?",
                      (now_iso(), user["user_id"], inv["invitation_id"]))
            self.db.audit_sync(c, inv["tenant_id"], "user", user["user_id"], "invitation.accept", resource_type="invitation", resource_id=inv["invitation_id"])
        sess = await self.create_session(user["user_id"], inv["tenant_id"])
        return sess, self.org.get_user(user["user_id"])  # type: ignore[return-value]

    def list_invitations(self, tenant_id: str) -> list[dict[str, Any]]:
        return rows_to_dicts(self.db.all("SELECT * FROM invitations WHERE tenant_id=? ORDER BY created_at DESC", (tenant_id,)))

    async def revoke_invitation(self, invitation_id: str, *, actor_id: str) -> bool:
        async with self.db.tx() as c:
            r = c.execute("SELECT tenant_id FROM invitations WHERE invitation_id=?", (invitation_id,)).fetchone()
            if r is None:
                return False
            c.execute("UPDATE invitations SET status='revoked' WHERE invitation_id=? AND status='pending'", (invitation_id,))
            self.db.audit_sync(c, r["tenant_id"], "user", actor_id, "invitation.revoke", resource_type="invitation", resource_id=invitation_id)
        return True

    # ------------------------------------------------------------------ api keys
    async def create_api_key(self, tenant_id: str, *, principal_type: str, principal_id: str, label: str = "",
                             scopes: list[str] | None = None, ttl_seconds: int | None = None) -> tuple[str, dict[str, Any]]:
        tok = "mk_" + token(32)
        kid = new_id("key")
        async with self.db.tx() as c:
            c.execute("INSERT INTO api_keys(key_id, tenant_id, principal_type, principal_id, key_hash, label, scopes, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (kid, tenant_id, principal_type, principal_id, token_hash(tok), label, __import__("json").dumps(scopes or []), now_iso(),
                       plus_seconds(ttl_seconds) if ttl_seconds else None))
            self.db.audit_sync(c, tenant_id, principal_type, principal_id, "api_key.create", resource_type="api_key", resource_id=kid, detail={"label": label})
        return tok, row_to_dict(self.db.one("SELECT key_id, tenant_id, principal_type, principal_id, label, scopes, created_at, expires_at FROM api_keys WHERE key_id=?", (kid,)), json_fields=("scopes",))  # type: ignore[return-value]

    def resolve_api_key(self, tok: str | None) -> dict[str, Any] | None:
        if not tok or not tok.startswith("mk_"):
            return None
        r = self.db.one("SELECT * FROM api_keys WHERE key_hash=?", (token_hash(tok),))
        if r is None or r["revoked_at"] or (r["expires_at"] and r["expires_at"] <= now_iso()):
            return None
        return row_to_dict(r, json_fields=("scopes",))

    def resolve_holder_key(self, tok: str | None) -> dict[str, Any] | None:
        if not tok:
            return None
        return self.org.holder_by_key_hash(token_hash(tok))

    async def revoke_api_key(self, key_id: str, *, actor_id: str | None) -> bool:
        async with self.db.tx() as c:
            r = c.execute("SELECT tenant_id FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
            if r is None:
                return False
            c.execute("UPDATE api_keys SET revoked_at=? WHERE key_id=? AND revoked_at IS NULL", (now_iso(), key_id))
            self.db.audit_sync(c, r["tenant_id"], "user", actor_id, "api_key.revoke", resource_type="api_key", resource_id=key_id)
        return True

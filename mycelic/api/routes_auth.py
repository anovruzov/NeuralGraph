"""Auth and onboarding routes (docs/mycelic/API.md "Auth and onboarding"; DECISIONS D8, D10).

Sessions are issued by register, login, invitation accept and the demo switch, always as the same HttpOnly cookie;
``/auth/me`` is what the browser calls first and carries the onboarding checklist so the UI can send a new
organization through the setup steps. The demo endpoints exist only with ``settings.demo_mode`` and only shortcut
authentication: the session they create is an ordinary one and every later check is the production path.
"""
from __future__ import annotations

import re
from typing import Any

from aiohttp import web

from ..auth import AuthError
from ..authz import Principal
from ..util import now_iso
from .middleware import (ApiError, clear_session_cookie, client_ip, json_response, listing, need_str, opt_str, principal_of, read_json,
                         require_user, session_token, set_session_cookie)
from .routes_org import holder_view

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def tenant_view(t: dict[str, Any]) -> dict[str, Any]:
    return {"tenant_id": t["tenant_id"], "name": t["name"], "slug": t["slug"], "is_demo": bool(t.get("is_demo")), "created_at": t.get("created_at")}


def user_view(rt: Any, u: dict[str, Any], *, with_memberships: bool = True) -> dict[str, Any]:
    d = {k: u.get(k) for k in ("user_id", "email", "name", "status", "created_at", "updated_at")}
    d["is_demo"] = bool(u.get("is_demo"))
    if with_memberships:
        d["memberships"] = [{"unit_id": m["unit_id"], "unit_name": m.get("unit_name"), "unit_type": m.get("unit_type"), "role": m["role"]}
                            for m in rt.org.memberships_for_user(u["user_id"])]
    return d


def onboarding(rt: Any, settings: Any, tenant: dict[str, Any]) -> dict[str, Any]:
    tid = tenant["tenant_id"]
    units = rt.org.list_units(tid)
    steps = {
        "organization": any(u["depth"] > 0 or u["type"] != "executive" for u in units),
        "members": len(rt.org.list_users(tid)) > 1,
        "holder": bool(rt.org.list_holders(tid)),
        "model": _model_configured(rt, settings),
        "goal": int(rt.db.scalar("SELECT COUNT(*) FROM goals WHERE tenant_id=?", (tid,), 0)) > 0,
        "loop_active": int(rt.db.scalar("SELECT COUNT(*) FROM goal_loops WHERE tenant_id=? AND desired='active'", (tid,), 0)) > 0,
    }
    out: dict[str, Any] = {"complete": all(steps.values()), "steps": steps}
    if tenant.get("is_demo"):
        out["demo_goal_id"] = rt.db.scalar("SELECT goal_id FROM goals WHERE tenant_id=? AND status='active' ORDER BY priority, created_at LIMIT 1", (tid,))
    return out


def _model_configured(rt: Any, settings: Any) -> bool:
    if rt.router is None:
        return False
    if getattr(settings, "demo_mode", False):
        return True
    try:
        tiers = (rt.router.describe() or {}).get("tiers") or {}
    except Exception:
        return False
    return any((t or {}).get("provider") not in (None, "fake") for t in tiers.values())


async def _session_response(rt: Any, settings: Any, request: web.Request, user: dict[str, Any], token: str, *, status: int = 200,
                            extra: dict[str, Any] | None = None) -> web.Response:
    p = rt.authz.principal_for_user(user["user_id"])
    if p is None:
        raise ApiError(403, "user is not active")
    body = {"user": user_view(rt, user), "principal": p.to_dict(), "tenant": tenant_view(rt.org.get_tenant(p.tenant_id) or {"tenant_id": p.tenant_id, "name": "", "slug": ""})}
    if extra:
        body.update(extra)
    resp = json_response(body, status)
    set_session_cookie(resp, token, settings)
    return resp


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]
    settings = app["settings"]

    async def register(request: web.Request) -> web.Response:
        body = await read_json(request)
        org_name = need_str(body, "org_name", max_len=200)
        slug = need_str(body, "slug", max_len=63).lower()
        email = need_str(body, "email", max_len=254).lower()
        name = need_str(body, "name", max_len=200)
        password = body.get("password")
        if not _SLUG_RE.match(slug):
            raise ApiError(400, "slug must be 2-63 lowercase letters, digits or hyphens")
        if "@" not in email:
            raise ApiError(400, "email is not valid")
        if not isinstance(password, str) or len(password) < 8:
            raise ApiError(400, "password must be at least 8 characters")
        try:
            out = await rt.auth.register_tenant(org_name=org_name, slug=slug, admin_email=email, admin_name=name, password=password)
        except AuthError as exc:
            raise ApiError(409, str(exc), "slug_taken") from None
        user = out["user"]
        token = await rt.auth.create_session(user["user_id"], out["tenant"]["tenant_id"], kind="web", user_agent=request.headers.get("User-Agent", ""), ip=client_ip(request))
        await rt.db.audit(out["tenant"]["tenant_id"], "user", user["user_id"], "auth.register", resource_type="tenant", resource_id=out["tenant"]["tenant_id"],
                          request_id=request.get("request_id"))
        return await _session_response(rt, settings, request, user, token, status=201, extra={"tenant": tenant_view(out["tenant"]), "root_unit": out["root_unit"]})

    async def login(request: web.Request) -> web.Response:
        body = await read_json(request)
        email = need_str(body, "email", max_len=254).lower()
        password = body.get("password")
        if not isinstance(password, str) or not password:
            raise ApiError(400, "'password' is required")
        try:
            token, user = await rt.auth.login(email=email, password=password, tenant_slug=opt_str(body, "tenant_slug"),
                                              user_agent=request.headers.get("User-Agent", ""), ip=client_ip(request))
        except AuthError:
            raise ApiError(401, "invalid credentials") from None
        return await _session_response(rt, settings, request, rt.org.get_user(user["user_id"]) or user, token)

    async def logout(request: web.Request) -> web.Response:
        tok = session_token(request)
        if tok:
            await rt.auth.logout(tok)
        resp = json_response({"ok": True})
        clear_session_cookie(resp, settings)
        return resp

    async def me(request: web.Request) -> web.Response:
        p: Principal = require_user(request)
        user = rt.org.get_user(p.id)
        tenant = rt.org.get_tenant(p.tenant_id)
        if user is None or tenant is None:
            raise ApiError(401, "session user no longer exists")
        unread = int(rt.db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (p.id,), 0))
        return json_response({
            "user": user_view(rt, user), "tenant": tenant_view(tenant), "principal": p.to_dict(), "levels": rt.authz.levels_for(p),
            "holders": [holder_view(rt, h) for h in rt.org.holders_for_user(p.id)], "unread_notifications": unread,
            "demo_mode": bool(settings.demo_mode), "onboarding": onboarding(rt, settings, tenant),
            "session_kind": (request.get("auth") or {}).get("kind"),
        })

    async def invitation(request: web.Request) -> web.Response:
        inv = rt.auth.get_invitation(request.match_info["token"])
        if inv is None:
            raise ApiError(404, "invitation not found", "not_found")
        unit = rt.org.get_unit(inv["unit_id"]) if inv.get("unit_id") else None
        tenant = rt.org.get_tenant(inv["tenant_id"])
        return json_response({"invitation": {"email": inv["email"], "role": inv["role"], "unit_id": inv.get("unit_id"), "unit_name": unit["name"] if unit else None,
                                             "org_name": tenant["name"] if tenant else "", "org_slug": tenant["slug"] if tenant else "", "status": inv["status"],
                                             "expires_at": inv.get("expires_at")}})

    async def accept_invitation(request: web.Request) -> web.Response:
        body = await read_json(request)
        name = need_str(body, "name", max_len=200)
        password = body.get("password")
        if not isinstance(password, str) or len(password) < 8:
            raise ApiError(400, "password must be at least 8 characters")
        try:
            token, user = await rt.auth.accept_invitation(request.match_info["token"], name=name, password=password)
        except AuthError as exc:
            raise ApiError(400, str(exc), "invalid_invitation") from None
        return await _session_response(rt, settings, request, user, token)

    async def demo_personas(request: web.Request) -> web.Response:
        if not settings.demo_mode:
            raise ApiError(404, "demo mode is off", "not_found")
        items = []
        for t in rt.org.list_tenants():
            if not t.get("is_demo"):
                continue
            for u in rt.org.list_users(t["tenant_id"], status="active"):
                if not u.get("is_demo"):
                    continue
                p = rt.authz.principal_for_user(u["user_id"])
                if p is None:
                    continue
                items.append({"user_id": u["user_id"], "name": u["name"], "email": u["email"], "roles": sorted(p.roles),
                              "unit_names": sorted({m.get("unit_name") or "" for m in p.memberships} - {""}), "level": p.highest_level,
                              "is_admin": p.is_admin, "tenant_slug": t["slug"], "tenant_name": t["name"]})
        return json_response(listing(items))

    async def demo_switch(request: web.Request) -> web.Response:
        if not settings.demo_mode:
            raise ApiError(404, "demo mode is off", "not_found")
        body = await read_json(request)
        user_id = need_str(body, "user_id", max_len=80)
        row = rt.org.get_user_row(user_id)
        tenant = rt.org.get_tenant(row["tenant_id"]) if row is not None else None
        if row is None or tenant is None or not row["is_demo"] or not tenant.get("is_demo") or row["status"] != "active":
            raise ApiError(404, "no such demo persona", "not_found")
        # the previous demo session (if any) is closed so switching never leaves extra live sessions around
        prev = session_token(request)
        if prev:
            await rt.auth.logout(prev)
        token = await rt.auth.create_session(user_id, row["tenant_id"], kind="demo", user_agent=request.headers.get("User-Agent", ""), ip=client_ip(request))
        actor = principal_of(request)
        await rt.db.audit(row["tenant_id"], "user", actor.id if actor is not None else None, "auth.demo_switch", resource_type="user", resource_id=user_id,
                          detail={"at": now_iso()}, request_id=request.get("request_id"))
        return await _session_response(rt, settings, request, rt.org.get_user(user_id) or {"user_id": user_id}, token)

    app.router.add_post(f"{prefix}/auth/register", register)
    app.router.add_post(f"{prefix}/auth/login", login)
    app.router.add_post(f"{prefix}/auth/logout", logout)
    app.router.add_get(f"{prefix}/auth/me", me)
    app.router.add_get(f"{prefix}/auth/invitation/{{token}}", invitation)
    app.router.add_post(f"{prefix}/auth/invitation/{{token}}/accept", accept_invitation)
    app.router.add_get(f"{prefix}/demo/personas", demo_personas)
    app.router.add_post(f"{prefix}/demo/switch", demo_switch)


__all__ = ["setup", "tenant_view", "user_view", "onboarding"]

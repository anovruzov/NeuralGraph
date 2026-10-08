"""Organization, grants, holders, documents and private memory (docs/mycelic/API.md "Organization", "Grants and holders").

Administrative rights and knowledge access are distinct (DECISIONS D9): unit, membership, invitation and policy
management is ``org_admin`` work; holders and documents are the owner's (a user, or the leads of an owning unit),
with explicit grants for everyone else. Embedded holders are reached through their in-process store; external
holders receive signed envelopes over the transport and the API answers 202 because the effect happens on the
holder's machine. Evidence changes (revise, retract) are pushed into the knowledge layer at once so claims that
cite the changed references become stale and are scheduled for re-verification, the same path a holder process
takes through ``LoopEngine.on_transport``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from aiohttp import web

from ..authz import Forbidden, Principal
from ..org import DEFAULT_POLICIES, LEAD_ROLES, ROLES, UNIT_TYPES, routable_domains
from ..evidence.service import policy_problems
from ..transport import Envelope, Subjects, TransportError
from ..util import new_id, now_iso, sha256
from .middleware import (ApiError, json_response, limit_of, listing, need_str, opt_dict, opt_list, opt_str, query_int, read_json, require_admin,
                         require_holder, require_user)

logger = logging.getLogger(__name__)

GRANT_LEVELS = ("read", "artifact", "raw")
GRANT_RESOURCES = ("holder", "claim", "discovery", "goal", "evidence_ref")
UPLOAD_EXTENSIONS = (".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".log", ".rst", ".docx", ".pdf")


# ---------------------------------------------------------------------------------------------
# Views and helpers shared with the other route modules
# ---------------------------------------------------------------------------------------------


def unit_view(rt: Any, u: dict[str, Any]) -> dict[str, Any]:
    d = {k: u.get(k) for k in ("unit_id", "type", "level", "name", "parent_id", "path", "depth", "settings", "archived_at", "created_at")}
    d["is_demo"] = bool(u.get("is_demo"))
    d["member_count"] = int(rt.db.scalar("SELECT COUNT(DISTINCT user_id) FROM memberships WHERE unit_id=? AND status='active'", (u["unit_id"],), 0))
    return d


def holder_view(rt: Any, h: dict[str, Any]) -> dict[str, Any]:
    """The registry row without any secret: ``key_hash`` and ``route_key`` never leave the server."""
    d = {k: h.get(k) for k in ("holder_id", "owner_type", "owner_id", "name", "status", "mode", "domains", "export_policy", "last_heartbeat_at",
                               "created_at", "updated_at")}
    d["stats"] = h.get("stats") or {}
    d["domains"] = list(d.get("domains") or [])
    d["published_domains"] = list(h.get("published_domains") or [])          # from ingested records (heartbeats)
    if h.get("owner_type") == "user":
        u = rt.org.get_user(h["owner_id"])
        d["owner_name"] = u["name"] if u else ""
    else:
        un = rt.org.get_unit(h["owner_id"])
        d["owner_name"] = un["name"] if un else ""
    return d


def scopes_of(rt: Any, p: Principal) -> set[str]:
    return set(rt.authz.visible_unit_ids(p)) | set(rt.authz.led_unit_ids(p))


def unit_or_404(rt: Any, p: Principal, unit_id: str) -> dict[str, Any]:
    u = rt.org.get_unit(unit_id)
    if u is None or u["tenant_id"] != p.tenant_id:
        raise KeyError(unit_id)
    return u


def holder_or_404(rt: Any, p: Principal, holder_id: str) -> dict[str, Any]:
    h = rt.org.get_holder(holder_id)
    if h is None or h["tenant_id"] != p.tenant_id:
        raise KeyError(holder_id)
    return h


def is_holder_owner(rt: Any, p: Principal, h: dict[str, Any]) -> bool:
    """Real ownership only. Administration is not ownership: an administrator never lists, adds, revises or shares
    someone's evidence (DECISIONS D9). See ``can_manage_holder`` for the narrow administrative powers."""
    if h.get("tenant_id") != p.tenant_id:
        return False
    if h.get("owner_type") == "user":
        return h.get("owner_id") == p.id
    return h.get("owner_id") in rt.authz.led_unit_ids(p)


def reader_audience(p: Principal, h: dict[str, Any]) -> dict[str, Any]:
    """Who is reading a holder's content, for the holder's source-ACL check. Only a personal holder's own user is the
    owner (sees every live record); a unit's lead, a member or a grantee sees a connector record only when its source
    is public or they are among its members, so leading a unit never opens its private channels."""
    owner = h.get("owner_type") == "user" and h.get("owner_id") == p.id and h.get("tenant_id") == p.tenant_id
    return {"principal_ids": [p.id], "complete": True, "owner": owner}


def can_manage_holder(rt: Any, p: Principal, h: dict[str, Any]) -> bool:
    """Owner, or an administrator for incident response only: revoke the holder, rotate its key."""
    return h.get("tenant_id") == p.tenant_id and (p.is_admin or is_holder_owner(rt, p, h))


def can_see_holder(rt: Any, p: Principal, h: dict[str, Any]) -> bool:
    """Own, unit-owned in a visible unit, granted, or administrator (who manages integrations)."""
    if h.get("tenant_id") != p.tenant_id:
        return False
    if p.is_admin or is_holder_owner(rt, p, h):
        return True
    if h.get("owner_type") == "unit" and h.get("owner_id") in scopes_of(rt, p):
        return True
    return any(g["resource_type"] == "holder" and g["resource_id"] == h["holder_id"] for g in p.grants)


def visible_holders(rt: Any, p: Principal) -> list[dict[str, Any]]:
    return [h for h in rt.org.list_holders(p.tenant_id) if can_see_holder(rt, p, h)]


async def embedded_store(rt: Any, h: dict[str, Any]) -> Any | None:
    """The in-process ``EvidenceStore`` of an embedded holder (opened on demand), or ``None`` for external ones."""
    if h.get("mode") != "embedded" or rt.holders is None or h.get("status") == "revoked":
        return None
    store = rt.holders.get(h["holder_id"])
    if store is None:
        try:
            await rt.holders.ensure(h["holder_id"])
        except Exception as exc:
            logger.warning("cannot open embedded holder %s: %s", h["holder_id"], exc)
            return None
        store = rt.holders.get(h["holder_id"])
    return store


def holder_audience(rt: Any, h: dict[str, Any]) -> dict[str, Any]:
    """Who may receive events about a holder's documents: the owner only for a personal holder (titles and document ids
    are private), the owning unit for a unit holder (whose members may search it)."""
    if h.get("owner_type") == "user":
        return {"user_ids": [h["owner_id"]], "unit_ids": []}
    return {"user_ids": [], "unit_ids": [h["owner_id"]]}


async def emit_document_event(rt: Any, h: dict[str, Any], kind: str, doc: dict[str, Any] | None, **extra: Any) -> None:
    doc = doc or {}
    await rt.db.emit(h["tenant_id"], kind, ref_type="holder", ref_id=h["holder_id"],
                     # ids, domains and counts only: a document title is content and stays with the holder
                     payload={"holder_id": h["holder_id"], "domains": doc.get("domains") or [], "doc_id": doc.get("doc_id"),
                              "version": doc.get("version"), "records": 1, **extra}, audience=holder_audience(rt, h))


async def publish_to_holder(rt: Any, h: dict[str, Any], kind: str, payload: dict[str, Any], *, subject: str, msg_id: str | None = None,
                            reply_to: str | None = None) -> Envelope:
    """Sign an envelope with the holder's route key (derived from the server secret) and publish it."""
    if rt.transport is None:
        raise ApiError(503, "no transport configured", "transport")
    env = Envelope.new(subject, kind, h["tenant_id"], payload, msg_id=msg_id, reply_to=reply_to)
    env.sign(rt.org.route_key(h["holder_id"]))
    await rt.transport.publish(env)
    return env


async def apply_evidence_change(rt: Any, h: dict[str, Any], event: str, affected_ref_ids: Iterable[str], *, new_root: str | None = None,
                                doc_id: str | None = None, reason: str = "") -> dict[str, Any]:
    """Propagate a revision/retraction from an embedded holder exactly as ``LoopEngine.on_transport`` does for a
    holder process: mark references, stale the claims that cite them, queue their re-verification, wake the goals."""
    helper = getattr(rt.engine, "apply_evidence_change", None)
    if helper is not None:
        return await helper(h, event, list(affected_ref_ids), new_root, doc_id=doc_id, reason=reason)
    p = rt.authz.principal_for_system(h["tenant_id"], worker_id="api")
    out = await rt.knowledge.on_evidence_event(p, h["tenant_id"], h["holder_id"], event, list(affected_ref_ids), new_source_root_id=new_root, reason=reason)
    goals_touched: set[str] = set()
    for cid in out.get("affected_claim_ids") or []:
        cl = rt.knowledge.get_claim(cid)
        if cl and cl.get("goal_id"):
            goals_touched.add(cl["goal_id"])
        await rt.jobs.enqueue("claim.reverify", idempotency_key=f"claim.reverify:{cid}:{(cl or {}).get('version')}", tenant_id=h["tenant_id"],
                              ref_type="claim", ref_id=cid, payload={"claim_id": cid}, priority=4)
    for gid in goals_touched:
        await rt.goals.enqueue_tick(gid, reason="evidence changed", priority=3)
    await rt.db.emit(h["tenant_id"], "document.revised" if event == "revised" else "document.retracted", ref_type="holder", ref_id=h["holder_id"],
                     payload={"holder_id": h["holder_id"], "affected_claims": out.get("affected_claim_ids") or [], "doc_id": doc_id},
                     audience=holder_audience(rt, h))
    return out


def _tree(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for u in units:
        by_parent.setdefault(u.get("parent_id"), []).append(u)
    ids = {u["unit_id"] for u in units}

    def build(parent: str | None) -> list[dict[str, Any]]:
        return [{"unit": u, "children": build(u["unit_id"])} for u in by_parent.get(parent, [])]

    roots = [u for u in units if u.get("parent_id") is None or u.get("parent_id") not in ids]
    return [{"unit": u, "children": build(u["unit_id"])} for u in roots]


def root_unit(rt: Any, tenant_id: str) -> dict[str, Any] | None:
    for u in rt.org.list_units(tenant_id):
        if u["depth"] == 0 and u["type"] == "executive":
            return u
    units = rt.org.list_units(tenant_id)
    return units[0] if units else None


# ---------------------------------------------------------------------------------------------
# Network graph
# ---------------------------------------------------------------------------------------------


def network_graph(rt: Any, p: Principal, view: str, *, goal_id: str | None = None, unit_id: str | None = None, max_nodes: int = 400) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def add(nid: str, ntype: str, label: str, **meta: Any) -> bool:
        if nid in nodes:
            return True
        if len(nodes) >= max_nodes:
            return False
        nodes[nid] = {"id": nid, "type": ntype, "label": (label or nid)[:120], "level": meta.pop("level", None), "unit_id": meta.pop("unit_id", None),
                      "status": meta.pop("status", None), "meta": meta}
        return True

    def edge(a: str, b: str, kind: str, **meta: Any) -> None:
        if a in nodes and b in nodes:
            edges.append({"source": a, "target": b, "kind": kind, **({"meta": meta} if meta else {})})

    units = rt.org.list_units(p.tenant_id)
    mine = scopes_of(rt, p)
    visible_units = units if p.is_admin else [u for u in units if u["unit_id"] in mine]
    if view == "hierarchy":
        for u in visible_units:
            add(u["unit_id"], "unit", u["name"], level=u["level"], unit_id=u["unit_id"], unit_type=u["type"], depth=u["depth"])
        for u in visible_units:
            if u.get("parent_id"):
                edge(u["parent_id"], u["unit_id"], "parent")
            if u["type"] == "project":
                for scoped in rt.org.project_scope(u["unit_id"]):
                    edge(u["unit_id"], scoped, "scoped")
        for u in visible_units:
            for m in rt.org.members_of(u["unit_id"]):
                if m["role"] == "org_admin":
                    continue
                add(m["user_id"], "user", m.get("user_name") or m["user_id"], unit_id=u["unit_id"], email=m.get("email"))
                edge(m["user_id"], u["unit_id"], "leads" if m["role"] in LEAD_ROLES else "member", role=m["role"])
        for h in rt.org.list_holders(p.tenant_id):
            if not can_see_holder(rt, p, h):
                continue
            if h["owner_type"] == "unit" and h["owner_id"] not in nodes:
                continue
            if h["owner_type"] == "user" and h["owner_id"] not in nodes:
                continue
            add(h["holder_id"], "holder", h["name"], status=h.get("status"), unit_id=h["owner_id"] if h["owner_type"] == "unit" else None,
                mode=h.get("mode"), domains=h.get("domains") or [])
            edge(h["owner_id"], h["holder_id"], "owns")
    elif view == "collaboration":
        unit_by_id = {u["unit_id"]: u for u in units}
        for u in visible_units:
            add(u["unit_id"], "unit", u["name"], level=u["level"], unit_id=u["unit_id"], unit_type=u["type"])
        for u in visible_units:
            if u["type"] == "project":
                for scoped in rt.org.project_scope(u["unit_id"]):
                    edge(u["unit_id"], scoped, "scoped")
        counts: dict[tuple[str, str, str], int] = {}
        owner_units_cache: dict[str, list[str]] = {}
        for q in rt.questions.list(p, limit=500):
            scope = q.get("scope_unit_id")
            if not scope or scope not in nodes:
                continue
            for r in q.get("routes") or []:
                h = rt.org.get_holder(r["holder_id"])
                if h is None:
                    continue
                if h["owner_type"] == "unit":
                    targets = [h["owner_id"]]
                else:
                    if h["owner_id"] not in owner_units_cache:
                        owner_units_cache[h["owner_id"]] = [m["unit_id"] for m in rt.org.memberships_for_user(h["owner_id"]) if m["role"] != "org_admin"]
                    targets = owner_units_cache[h["owner_id"]]
                for t in targets:
                    if t == scope or t not in nodes or t not in unit_by_id:
                        continue
                    kind = "responded" if r.get("status") == "answered" else "routed"
                    counts[(scope, t, kind)] = counts.get((scope, t, kind), 0) + 1
        for (a, b, kind), n in counts.items():
            edge(a, b, kind, count=n)
    elif view == "lineage":
        if goal_id:
            g = rt.goals.get_goal(goal_id)
            if g is None or g["tenant_id"] != p.tenant_id:
                raise KeyError(goal_id)
            if not rt.goals.can_view(p, g):
                raise Forbidden("goal.view", goal_id)
            graph = rt.knowledge.lineage_graph(p, goal_id=goal_id, max_nodes=max_nodes)
            for n in graph["nodes"]:
                nodes[n["id"]] = _lineage_node(n)
            edges.extend(graph["edges"])
            add(goal_id, "goal", g["title"], status=g.get("status"), unit_id=g.get("scope_unit_id"))
            for n in graph["nodes"]:
                if n["type"] == "claim":
                    edge(goal_id, n["id"], "derived")
            for d in rt.knowledge.list_discoveries(p, goal_id=goal_id, limit=100):
                if add(d["discovery_id"], "discovery", d["title"], status=d.get("status"), level=d.get("level"), unit_id=d.get("scope_unit_id")):
                    edge(goal_id, d["discovery_id"], "derived")
                    for cid in d.get("claim_ids") or []:
                        edge(d["discovery_id"], cid, "supports")
        else:
            claims = rt.knowledge.list_claims(p, scope_unit_id=unit_id, limit=200) if unit_id else rt.knowledge.list_claims(p, limit=200)
            graph = rt.knowledge.lineage_graph(p, claim_ids=[c["claim_id"] for c in claims], max_nodes=max_nodes)
            for n in graph["nodes"]:
                nodes[n["id"]] = _lineage_node(n)
            edges.extend(graph["edges"])
            discs = rt.knowledge.list_discoveries(p, scope_unit_id=unit_id, limit=100) if unit_id else rt.knowledge.list_discoveries(p, limit=100)
            for d in discs:
                if add(d["discovery_id"], "discovery", d["title"], status=d.get("status"), level=d.get("level"), unit_id=d.get("scope_unit_id")):
                    for cid in d.get("claim_ids") or []:
                        edge(d["discovery_id"], cid, "supports")
    else:
        raise ApiError(400, "view must be hierarchy, collaboration or lineage")
    seen: set[tuple[str, str, str]] = set()
    uniq = []
    for e in edges:
        key = (e["source"], e["target"], e["kind"])
        if key in seen or e["source"] not in nodes or e["target"] not in nodes:
            continue
        seen.add(key)
        uniq.append(e)
    return {"view": view, "nodes": list(nodes.values()), "edges": uniq}


def _lineage_node(n: dict[str, Any]) -> dict[str, Any]:
    d = dict(n)
    return {"id": d.pop("id"), "type": d.pop("type"), "label": d.pop("label", ""), "level": d.pop("level", None), "unit_id": d.pop("unit_id", None),
            "status": d.pop("status", None), "meta": d}


# ---------------------------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------------------------


async def read_document_body(request: web.Request) -> dict[str, Any]:
    """JSON ``{title, text, ...}`` or a multipart upload with a ``file`` (text, Markdown, JSON/JSONL, CSV, DOCX or PDF; see
    mycelic.evidence.extract for the bounds)."""
    ctype = request.content_type or ""
    if ctype.startswith("multipart/"):
        fields: dict[str, Any] = {}
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                from ..evidence.extract import ExtractError, extract_text
                filename = (part.filename or "upload.txt").strip()
                if not any(filename.lower().endswith(ext) for ext in UPLOAD_EXTENSIONS):
                    raise ApiError(400, f"unsupported file type; use one of {', '.join(UPLOAD_EXTENSIONS)}")
                raw = await part.read(decode=False)
                try:
                    # off the event loop: a large PDF takes a while to parse
                    text, info = await asyncio.get_running_loop().run_in_executor(None, extract_text, filename, bytes(raw))
                except ExtractError as exc:
                    raise ApiError(400, str(exc), "unreadable_file") from None
                if not text.strip():
                    raise ApiError(400, "the file contains no text", "empty_file")
                fields["text"] = text
                fields.setdefault("title", filename.rsplit(".", 1)[0])
                fields["filename"] = filename
                fields["extract"] = info
            else:
                value = (await part.text()).strip()
                if part.name == "domains":
                    fields["domains"] = [d.strip() for d in value.split(",") if d.strip()]
                elif part.name in ("title", "kind", "observed_at", "origin_id"):
                    fields[part.name] = value
        if "text" not in fields:
            raise ApiError(400, "multipart upload needs a 'file' part")
        return fields
    body = await read_json(request)
    if not isinstance(body.get("text"), str):
        raise ApiError(400, "'text' is required")
    return body


def document_view(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: doc.get(k) for k in ("doc_id", "holder_id", "title", "kind", "source_root_id", "origin_id", "observed_at", "status", "version", "chars",
                                    "chunks", "domains", "summary", "created_at", "updated_at")}


# ---------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]
    settings = app["settings"]

    # ------------------------------------------------------------------ organization
    async def get_org(request: web.Request) -> web.Response:
        p = require_user(request)
        tenant = rt.org.get_tenant(p.tenant_id)
        units = [unit_view(rt, u) for u in rt.org.list_units(p.tenant_id, include_archived=True)]
        active = [u for u in units if not u.get("archived_at")]
        return json_response({
            "tenant": {"tenant_id": tenant["tenant_id"], "name": tenant["name"], "slug": tenant["slug"], "is_demo": bool(tenant.get("is_demo"))} if tenant else None,
            "units": units, "tree": _tree(active),
            "counts": {"users": len(rt.org.list_users(p.tenant_id)), "memberships": sum(1 for m in rt.org.list_memberships(p.tenant_id) if m["status"] == "active"),
                       "holders": len(rt.org.list_holders(p.tenant_id))},
        })

    async def create_unit(request: web.Request) -> web.Response:
        p = require_admin(request)
        body = await read_json(request)
        utype = need_str(body, "type", max_len=40)
        name = need_str(body, "name", max_len=200)
        if utype not in UNIT_TYPES:
            raise ApiError(400, f"type must be one of {', '.join(UNIT_TYPES)}")
        parent_id = opt_str(body, "parent_id", max_len=80)
        if parent_id:
            unit_or_404(rt, p, parent_id)
        elif utype != "executive":
            root = root_unit(rt, p.tenant_id)
            parent_id = root["unit_id"] if root else None
        unit = await rt.org.create_unit(p.tenant_id, utype, name, parent_id=parent_id, settings=opt_dict(body, "settings"), actor_id=p.id)
        return json_response({"unit": unit_view(rt, unit)}, 201)

    async def update_unit(request: web.Request) -> web.Response:
        p = require_admin(request)
        unit = unit_or_404(rt, p, request.match_info["unit_id"])
        body = await read_json(request)
        kw: dict[str, Any] = {"actor_id": p.id}
        if "name" in body:
            kw["name"] = need_str(body, "name", max_len=200)
        if "parent_id" in body:
            parent_id = opt_str(body, "parent_id", max_len=80)
            if parent_id:
                unit_or_404(rt, p, parent_id)
            kw["parent_id"] = parent_id
        if "archived" in body:
            if not isinstance(body["archived"], bool):
                raise ApiError(400, "'archived' must be a boolean")
            kw["archived"] = body["archived"]
        out = await rt.org.update_unit(unit["unit_id"], **kw)
        return json_response({"unit": unit_view(rt, out)})

    async def set_scope(request: web.Request) -> web.Response:
        p = require_admin(request)
        unit = unit_or_404(rt, p, request.match_info["unit_id"])
        if unit["type"] != "project":
            raise ApiError(400, "only project units have a scope")
        body = await read_json(request)
        ids = opt_list(body, "unit_ids")
        if ids is None:
            raise ApiError(400, "'unit_ids' is required")
        for uid in ids:
            if not isinstance(uid, str):
                raise ApiError(400, "'unit_ids' must be strings")
            unit_or_404(rt, p, uid)
        await rt.org.set_project_scope(unit["unit_id"], ids, actor_id=p.id)
        await rt.db.emit(p.tenant_id, "org.changed", ref_type="unit", ref_id=unit["unit_id"], payload={"action": "scope"}, audience={"visibility": "org"})
        return json_response({"unit_ids": rt.org.project_scope(unit["unit_id"])})

    async def get_unit(request: web.Request) -> web.Response:
        p = require_user(request)
        unit = unit_or_404(rt, p, request.match_info["unit_id"])
        allowed = p.is_admin or unit["unit_id"] in scopes_of(rt, p) or unit["unit_id"] in p.member_unit_ids
        rt.authz.require(allowed, "unit.view", unit["unit_id"], "not a member or lead of that unit")
        children = [unit_view(rt, u) for u in rt.org.list_units(p.tenant_id) if u.get("parent_id") == unit["unit_id"]]
        projects = [unit_view(rt, rt.org.get_unit(pid)) for pid in rt.org.projects_spanning([unit["unit_id"]]) if rt.org.get_unit(pid)]
        members = [{"user_id": m["user_id"], "user_name": m.get("user_name"), "email": m.get("email"), "role": m["role"], "created_at": m.get("created_at")}
                   for m in rt.org.members_of(unit["unit_id"])]
        holders = [holder_view(rt, h) for h in rt.org.list_holders(p.tenant_id, owner_type="unit", owner_id=unit["unit_id"]) if h.get("status") != "revoked"]
        return json_response({"unit": unit_view(rt, unit), "members": members, "children": children, "projects": projects, "holders": holders,
                              "scope": rt.org.project_scope(unit["unit_id"]) if unit["type"] == "project" else None})

    async def list_users(request: web.Request) -> web.Response:
        p = require_admin(request)
        from .routes_auth import user_view
        return json_response(listing([user_view(rt, u) for u in rt.org.list_users(p.tenant_id)]))

    async def update_user(request: web.Request) -> web.Response:
        p = require_admin(request)
        uid = request.match_info["user_id"]
        row = rt.org.get_user_row(uid)
        if row is None or row["tenant_id"] != p.tenant_id:
            raise KeyError(uid)
        body = await read_json(request)
        status = opt_str(body, "status", max_len=20)
        name = opt_str(body, "name", max_len=200)
        if status is not None:
            if status not in ("active", "disabled"):
                raise ApiError(400, "status must be active or disabled")
            if status == "disabled" and uid == p.id:
                raise ApiError(400, "you cannot disable your own account")
            await rt.org.set_user_status(uid, status, actor_id=p.id)
        if name is not None and name.strip():
            async with rt.db.tx() as c:
                c.execute("UPDATE users SET name=?, updated_at=? WHERE user_id=?", (name.strip(), now_iso(), uid))
                rt.db.audit_sync(c, p.tenant_id, "user", p.id, "user.rename", resource_type="user", resource_id=uid, detail={"name": name.strip()})
        from .routes_auth import user_view
        return json_response({"user": user_view(rt, rt.org.get_user(uid))})

    async def list_memberships(request: web.Request) -> web.Response:
        p = require_admin(request)
        rows = [{k: m.get(k) for k in ("membership_id", "user_id", "user_name", "email", "unit_id", "unit_name", "unit_type", "role", "status", "created_at", "revoked_at")}
                for m in rt.org.list_memberships(p.tenant_id)]
        return json_response(listing(rows))

    async def add_membership(request: web.Request) -> web.Response:
        p = require_admin(request)
        body = await read_json(request)
        uid, unit_id, role = need_str(body, "user_id", max_len=80), need_str(body, "unit_id", max_len=80), need_str(body, "role", max_len=40)
        if role not in ROLES:
            raise ApiError(400, f"role must be one of {', '.join(ROLES)}")
        row = rt.org.get_user_row(uid)
        if row is None or row["tenant_id"] != p.tenant_id:
            raise KeyError(uid)
        unit_or_404(rt, p, unit_id)
        m = await rt.org.add_membership(p.tenant_id, uid, unit_id, role, granted_by=p.id)
        return json_response({"membership": m}, 201)

    async def revoke_membership(request: web.Request) -> web.Response:
        p = require_admin(request)
        body = await read_json(request)
        uid, unit_id = need_str(body, "user_id", max_len=80), need_str(body, "unit_id", max_len=80)
        role = opt_str(body, "role", max_len=40)
        row = rt.org.get_user_row(uid)
        if row is None or row["tenant_id"] != p.tenant_id:
            raise KeyError(uid)
        n = await rt.org.revoke_membership(uid, unit_id, role, actor_id=p.id)
        routes_revoked = 0
        if n:
            routes_revoked = await _revoke_lost_routes(rt, uid)
        return json_response({"revoked": n, "routes_revoked": routes_revoked})

    async def _revoke_lost_routes(rt: Any, user_id: str) -> int:
        """Pending routes to the user's holders are re-checked against the reduced membership; the ones the
        authorizer would no longer allow are revoked."""
        revoked = 0
        for h in rt.org.holders_for_user(user_id):
            rows = rt.db.all("SELECT r.route_id, r.question_id FROM question_routes r WHERE r.holder_id=? AND r.status IN ('pending','delivered')", (h["holder_id"],))
            for r in rows:
                q = rt.questions.get(r["question_id"])
                if q is None:
                    continue
                ok, why = rt.authz.can_route(q, h)
                if not ok:
                    async with rt.db.tx() as c:
                        c.execute("UPDATE question_routes SET status='revoked', error=? WHERE route_id=? AND status IN ('pending','delivered')", (f"membership revoked: {why}", r["route_id"]))
                    revoked += 1
        return revoked

    async def list_invitations(request: web.Request) -> web.Response:
        p = require_admin(request)
        items = []
        for inv in rt.auth.list_invitations(p.tenant_id):
            d = {k: inv.get(k) for k in ("invitation_id", "email", "role", "unit_id", "invited_by", "status", "created_at", "expires_at", "accepted_at", "accepted_user_id")}
            if d["status"] == "pending" and (d.get("expires_at") or "") <= now_iso():
                d["status"] = "expired"
            unit = rt.org.get_unit(inv["unit_id"]) if inv.get("unit_id") else None
            d["unit_name"] = unit["name"] if unit else None
            items.append(d)
        return json_response(listing(items))

    async def create_invitation(request: web.Request) -> web.Response:
        p = require_admin(request)
        body = await read_json(request)
        email = need_str(body, "email", max_len=254).lower()
        role = need_str(body, "role", max_len=40)
        if "@" not in email:
            raise ApiError(400, "email is not valid")
        if role not in ROLES:
            raise ApiError(400, f"role must be one of {', '.join(ROLES)}")
        unit_id = opt_str(body, "unit_id", max_len=80)
        if unit_id:
            unit_or_404(rt, p, unit_id)
        elif role != "org_admin":
            root = root_unit(rt, p.tenant_id)
            unit_id = root["unit_id"] if root else None
        else:
            root = root_unit(rt, p.tenant_id)
            unit_id = root["unit_id"] if root else None
        token, inv = await rt.auth.invite(p.tenant_id, email=email, role=role, unit_id=unit_id, invited_by=p.id)
        base = (settings.public_url or "").rstrip("/") or f"{request.scheme}://{request.host}"
        d = {k: inv.get(k) for k in ("invitation_id", "email", "role", "unit_id", "status", "created_at", "expires_at")}
        unit = rt.org.get_unit(unit_id) if unit_id else None
        d["unit_name"] = unit["name"] if unit else None
        return json_response({"invitation": d, "accept_url": f"{base}/invite/{token}"}, 201)

    async def delete_invitation(request: web.Request) -> web.Response:
        p = require_admin(request)
        iid = request.match_info["invitation_id"]
        row = rt.db.one("SELECT tenant_id FROM invitations WHERE invitation_id=?", (iid,))
        if row is None or row["tenant_id"] != p.tenant_id:
            raise KeyError(iid)
        await rt.auth.revoke_invitation(iid, actor_id=p.id)
        return json_response({"ok": True})

    async def get_policies(request: web.Request) -> web.Response:
        p = require_admin(request)
        return json_response({"policies": rt.org.policies(p.tenant_id)})

    async def put_policy(request: web.Request) -> web.Response:
        p = require_admin(request)
        body = await read_json(request)
        key = need_str(body, "key", max_len=80)
        if key not in DEFAULT_POLICIES:
            raise ApiError(400, f"unknown policy key; known keys: {', '.join(sorted(DEFAULT_POLICIES))}")
        if "value" not in body:
            raise ApiError(400, "'value' is required")
        value = body["value"]
        default = DEFAULT_POLICIES[key]
        if isinstance(default, bool) or not isinstance(default, type(value)) and not (isinstance(default, (int, float)) and isinstance(value, (int, float))):
            raise ApiError(400, f"'{key}' expects a value of type {type(default).__name__}")
        if isinstance(default, dict):
            value = {**default, **value}
        await rt.org.set_policy(p.tenant_id, key, value, actor_id=p.id)
        return json_response({"policies": rt.org.policies(p.tenant_id)})

    async def network(request: web.Request) -> web.Response:
        p = require_user(request)
        view = (request.query.get("view") or "hierarchy").strip().lower()
        return json_response(network_graph(rt, p, view, goal_id=request.query.get("goal_id") or None, unit_id=request.query.get("unit_id") or None))

    # ------------------------------------------------------------------ grants
    def grant_view(g: dict[str, Any]) -> dict[str, Any]:
        d = {k: g.get(k) for k in ("grant_id", "grantor_id", "grantee_type", "grantee_id", "resource_type", "resource_id", "level", "reason", "status",
                                   "created_at", "expires_at", "revoked_at")}
        u = rt.org.get_user(g["grantor_id"])
        d["grantor_name"] = u["name"] if u else ""
        if g["grantee_type"] == "user":
            gu = rt.org.get_user(g["grantee_id"]); d["grantee_name"] = gu["name"] if gu else ""
        else:
            gn = rt.org.get_unit(g["grantee_id"]); d["grantee_name"] = gn["name"] if gn else ""
        d["resource_label"] = _resource_label(g["resource_type"], g["resource_id"])
        return d

    def _resource_label(rtype: str, rid: str) -> str:
        if rtype == "holder":
            h = rt.org.get_holder(rid); return h["name"] if h else rid
        if rtype == "claim":
            c = rt.knowledge.get_claim(rid); return (c["text"][:80] if c else rid)
        if rtype == "discovery":
            d = rt.knowledge.get_discovery(rid); return (d["title"] if d else rid)
        if rtype == "goal":
            g = rt.goals.get_goal(rid); return (g["title"] if g else rid)
        if rtype == "evidence_ref":
            r = rt.knowledge.get_ref(rid); return ((r or {}).get("title") or rid)
        return rid

    async def list_grants(request: web.Request) -> web.Response:
        p = require_user(request)
        given = [grant_view(g) for g in rt.org.list_grants(p.tenant_id, grantor_id=p.id)]
        received = [grant_view(g) for g in p.grants if g.get("tenant_id") == p.tenant_id]
        return json_response({"given": given, "received": received})

    async def create_grant(request: web.Request) -> web.Response:
        p = require_user(request)
        body = await read_json(request)
        gtype, gid = need_str(body, "grantee_type", max_len=10), need_str(body, "grantee_id", max_len=80)
        rtype, rid = need_str(body, "resource_type", max_len=20), need_str(body, "resource_id", max_len=80)
        level = opt_str(body, "level", max_len=10) or "read"
        if gtype not in ("user", "unit"):
            raise ApiError(400, "grantee_type must be user or unit")
        if rtype not in GRANT_RESOURCES:
            raise ApiError(400, f"resource_type must be one of {', '.join(GRANT_RESOURCES)}")
        if level not in GRANT_LEVELS:
            raise ApiError(400, f"level must be one of {', '.join(GRANT_LEVELS)}")
        expires = body.get("expires_in_seconds")
        if expires is not None and (not isinstance(expires, (int, float)) or expires <= 0):
            raise ApiError(400, "'expires_in_seconds' must be a positive number")
        if gtype == "user":
            row = rt.org.get_user_row(gid)
            if row is None or row["tenant_id"] != p.tenant_id:
                raise KeyError(gid)
        else:
            unit_or_404(rt, p, gid)
        _require_resource_owner(p, rtype, rid)
        g = await rt.org.add_grant(p.tenant_id, grantor_id=p.id, grantee_type=gtype, grantee_id=gid, resource_type=rtype, resource_id=rid, level=level,
                                   reason=opt_str(body, "reason", max_len=500) or "", expires_in_seconds=expires)
        return json_response({"grant": grant_view(g)}, 201)

    def _require_resource_owner(p: Principal, rtype: str, rid: str) -> None:
        """The caller must own what they share: holder owner, claim creator (or scope lead), goal manager, ..."""
        if rtype == "holder":
            h = rt.org.get_holder(rid)
            if h is None or h["tenant_id"] != p.tenant_id:
                raise KeyError(rid)
            rt.authz.require(is_holder_owner(rt, p, h), "grant.holder", rid, "only the holder's owner can share it")
        elif rtype == "claim":
            c = rt.knowledge.get_claim(rid)
            if c is None or c["tenant_id"] != p.tenant_id:
                raise KeyError(rid)
            rt.authz.require(c.get("created_by_id") == p.id or c.get("owner_user_id") == p.id or rt.authz.can_manage_unit_knowledge(p, c.get("scope_unit_id") or ""),
                             "grant.claim", rid, "only the claim's creator or a lead of its scope can share it")
        elif rtype == "discovery":
            d = rt.knowledge.get_discovery(rid)
            if d is None or d["tenant_id"] != p.tenant_id:
                raise KeyError(rid)
            rt.authz.require(rt.authz.can_manage_unit_knowledge(p, d.get("scope_unit_id") or ""), "grant.discovery", rid, "only a lead of the discovery's scope can share it")
        elif rtype == "goal":
            g = rt.goals.get_goal(rid)
            if g is None or g["tenant_id"] != p.tenant_id:
                raise KeyError(rid)
            rt.authz.require(rt.authz.can_manage_goal(p, g), "grant.goal", rid, "only a manager of the goal can share it")
        elif rtype == "evidence_ref":
            r = rt.knowledge.get_ref(rid)
            if r is None or r["tenant_id"] != p.tenant_id:
                raise KeyError(rid)
            h = rt.org.get_holder(r["holder_id"])
            rt.authz.require(bool(h) and is_holder_owner(rt, p, h), "grant.evidence_ref", rid, "only the holder's owner can share its evidence")

    async def delete_grant(request: web.Request) -> web.Response:
        p = require_user(request)
        gid = request.match_info["grant_id"]
        row = rt.db.one("SELECT * FROM grants WHERE grant_id=?", (gid,))
        if row is None or row["tenant_id"] != p.tenant_id:
            raise KeyError(gid)
        rt.authz.require(row["grantor_id"] == p.id or p.is_admin, "grant.revoke", gid, "only the grantor or an administrator can revoke a grant")
        await rt.org.revoke_grant(gid, actor_id=p.id)
        return json_response({"ok": True})

    # ------------------------------------------------------------------ holders
    async def list_holders(request: web.Request) -> web.Response:
        p = require_user(request)
        return json_response(listing([holder_view(rt, h) for h in visible_holders(rt, p)]))

    async def create_holder(request: web.Request) -> web.Response:
        p = require_user(request)
        body = await read_json(request)
        name = need_str(body, "name", max_len=200)
        owner_type = opt_str(body, "owner_type", max_len=10) or "user"
        owner_id = opt_str(body, "owner_id", max_len=80) or (p.id if owner_type == "user" else None)
        if owner_type not in ("user", "unit") or not owner_id:
            raise ApiError(400, "owner_type must be user or unit (with owner_id)")
        if owner_type == "user":
            row = rt.org.get_user_row(owner_id)
            if row is None or row["tenant_id"] != p.tenant_id:
                raise KeyError(owner_id)
            rt.authz.require(owner_id == p.id, "holder.create", owner_id, "you can only register holders for yourself")
        else:
            unit_or_404(rt, p, owner_id)
            rt.authz.require(p.is_admin or rt.authz.can_manage_unit_knowledge(p, owner_id), "holder.create", owner_id, "only leads of the unit can register a unit holder")
        mode = opt_str(body, "mode", max_len=10) or ("embedded" if settings.embedded_holders and rt.holders is not None else "external")
        if mode not in ("embedded", "external"):
            raise ApiError(400, "mode must be embedded or external")
        if mode == "embedded" and rt.holders is None:
            raise ApiError(400, "embedded holders are disabled on this server; register the holder as external")
        domains = opt_list(body, "domains") or []
        if any(not isinstance(d, str) for d in domains):
            raise ApiError(400, "'domains' must be strings")
        problems = policy_problems(opt_dict(body, "export_policy"))
        if problems:
            raise ApiError(400, "invalid export_policy: " + "; ".join(problems))
        holder, key = await rt.org.register_holder(p.tenant_id, owner_type=owner_type, owner_id=owner_id, name=name, mode=mode, domains=domains,
                                                   export_policy=opt_dict(body, "export_policy"))
        await rt.db.audit(p.tenant_id, "user", p.id, "holder.create", resource_type="holder", resource_id=holder["holder_id"], detail={"mode": mode, "owner_type": owner_type},
                          request_id=request.get("request_id"))
        if mode == "embedded":
            try:
                await rt.holders.ensure(holder["holder_id"])
                await rt.org.holder_heartbeat(holder["holder_id"], stats={"documents": 0, "memories": 0}, status="online")
                holder = rt.org.get_holder(holder["holder_id"]) or holder
            except Exception as exc:
                logger.warning("embedded holder %s could not be started: %s", holder["holder_id"], exc)
        await rt.db.emit(p.tenant_id, "holder.status", ref_type="holder", ref_id=holder["holder_id"], payload={"status": holder.get("status"), "action": "register"},
                         audience={**holder_audience(rt, holder), "roles": ["org_admin"]})
        return json_response({"holder": holder_view(rt, holder), "key": key}, 201)

    async def update_holder(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        body = await read_json(request)
        export_policy = opt_dict(body, "export_policy")
        domains = opt_list(body, "domains")
        status = opt_str(body, "status", max_len=20)
        name = opt_str(body, "name", max_len=200)
        if export_policy is not None or domains is not None or name is not None:
            # what a holder discloses is the owner's decision alone
            rt.authz.require(is_holder_owner(rt, p, h), "holder.update", h["holder_id"], "only the holder's owner can change its policy, domains or name")
        else:
            rt.authz.require(can_manage_holder(rt, p, h), "holder.update", h["holder_id"], "only the holder's owner or an administrator can revoke it")
        if export_policy is not None:
            problems = policy_problems({**(h.get("export_policy") or {}), **export_policy})
            if problems:
                raise ApiError(400, "invalid export_policy: " + "; ".join(problems))
        if status is not None and status != "revoked":
            raise ApiError(400, "status can only be set to revoked")
        if domains is not None and any(not isinstance(d, str) for d in domains):
            raise ApiError(400, "'domains' must be strings")
        if export_policy is not None:
            export_policy = {**(h.get("export_policy") or {}), **export_policy}
        out = await rt.org.update_holder(h["holder_id"], export_policy=export_policy, domains=domains, status=status, actor_id=p.id)
        if name is not None and name.strip():
            async with rt.db.tx() as c:
                c.execute("UPDATE holders SET name=?, updated_at=? WHERE holder_id=?", (name.strip(), now_iso(), h["holder_id"]))
            out = rt.org.get_holder(h["holder_id"]) or out
        if status == "revoked":
            await rt.questions.revoke_routes_for_holder(h["holder_id"], "holder revoked")
            if rt.holders is not None and h.get("mode") == "embedded":
                try:
                    await rt.holders.remove(h["holder_id"])
                except Exception:
                    logger.exception("could not stop embedded holder %s", h["holder_id"])
            await rt.db.emit(p.tenant_id, "holder.status", ref_type="holder", ref_id=h["holder_id"], payload={"status": "revoked"},
                             audience={**holder_audience(rt, h), "roles": ["org_admin"]})
        elif (export_policy is not None or domains is not None):
            if rt.holders is not None and h.get("mode") == "embedded":
                try:
                    await rt.holders.reload_policy(h["holder_id"])
                except Exception:
                    logger.exception("policy reload failed for %s", h["holder_id"])
            elif h.get("mode") == "external" and rt.transport is not None:
                try:
                    await publish_to_holder(rt, h, "control", {"action": "reload_policy", "export_policy": out.get("export_policy"), "domains": out.get("domains")},
                                            subject=Subjects.holder_control(p.tenant_id, h["holder_id"]), msg_id=f"control:policy:{h['holder_id']}:{new_id('c')}")
                except TransportError as exc:
                    logger.warning("policy push to %s failed: %s", h["holder_id"], exc)
        return json_response({"holder": holder_view(rt, out)})

    async def rotate_key(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(can_manage_holder(rt, p, h), "holder.rotate_key", h["holder_id"], "only the holder's owner or an administrator can rotate its key")
        key = await rt.org.rotate_holder_key(h["holder_id"], actor_id=p.id)
        if h.get("mode") == "embedded" and rt.holders is not None:
            # the signing (route) key rotated with the bearer key: restart the in-process holder so it signs with the new one
            try:
                await rt.holders.remove(h["holder_id"])
                await rt.holders.ensure(h["holder_id"])
            except Exception:
                logger.exception("could not restart embedded holder %s after key rotation", h["holder_id"])
        return json_response({"key": key, "note": "the bearer key and the envelope signing key both rotated; restart an external holder with the new key"})

    async def add_document(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(rt.authz.can_search_holder(p, h) or is_holder_owner(rt, p, h), "document.add", h["holder_id"], "only the holder's owner (or an artifact grant) can add documents")
        if h.get("status") == "revoked":
            raise ApiError(409, "holder is revoked")
        body = await read_document_body(request)
        title = (body.get("title") or "").strip() or "Untitled"
        text = body.get("text") or ""
        if not text.strip():
            raise ApiError(400, "document text is empty")
        kind = (body.get("kind") or "note").strip()
        domains = body.get("domains")
        if domains is not None and (not isinstance(domains, list) or any(not isinstance(d, str) for d in domains)):
            raise ApiError(400, "'domains' must be a list of strings")
        observed_at = body.get("observed_at") or None
        origin_id = body.get("origin_id") or None
        store = await embedded_store(rt, h)
        if store is not None:
            doc = await store.ingest_document(title, text, kind=kind, observed_at=observed_at, domains=domains, origin_id=origin_id, uploaded_by=p.id)
            await emit_document_event(rt, h, "document.ingested", doc, uploaded_by=p.id)
            await rt.db.audit(p.tenant_id, "user", p.id, "document.ingest", resource_type="holder", resource_id=h["holder_id"], detail={"doc_id": doc.get("doc_id")},
                              request_id=request.get("request_id"))
            try:
                await rt.engine.wake_goals_for_holder(h)
            except Exception:
                logger.exception("waking goals after ingest failed")
            return json_response({"document": document_view(doc)}, 201)
        if h.get("mode") == "embedded":
            raise ApiError(503, "the embedded holder is not running on this server", "holder_unavailable")
        doc_id = new_id("doc")
        await publish_to_holder(rt, h, "ingest", {"doc_id": doc_id, "title": title, "text": text, "kind": kind, "observed_at": observed_at, "domains": domains,
                                                  "origin_id": origin_id, "uploaded_by": p.id}, subject=Subjects.holder_ingest(p.tenant_id, h["holder_id"]),
                                msg_id=f"ingest:{doc_id}")
        await rt.db.audit(p.tenant_id, "user", p.id, "document.ingest", resource_type="holder", resource_id=h["holder_id"], detail={"doc_id": doc_id, "async": True},
                          request_id=request.get("request_id"))
        return json_response({"document": {"doc_id": doc_id, "holder_id": h["holder_id"], "title": title, "kind": kind, "source_root_id": None, "observed_at": observed_at,
                                           "status": "indexing", "version": 1, "chars": len(text), "chunks": 0, "domains": domains or []}}, 202)

    async def list_documents(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(rt.authz.can_search_holder(p, h) or is_holder_owner(rt, p, h), "document.list", h["holder_id"])
        store = await embedded_store(rt, h)
        if store is None:
            return json_response(listing([], available=False, note="documents of an external holder are listed on the holder's machine"))
        docs = await store.list_documents(status=request.query.get("status") or None, limit=limit_of(request, 100), audience=reader_audience(p, h))
        return json_response(listing([document_view(d) for d in docs]))

    async def get_document(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(rt.authz.can_view_raw_evidence(p, h), "document.raw", h["holder_id"], "raw documents need ownership or a raw grant")
        store = await embedded_store(rt, h)
        if store is None:
            raise ApiError(503, "raw documents of an external holder are read on the holder's machine", "external_holder")
        audience = reader_audience(p, h)
        doc = await store.document(request.match_info["doc_id"], audience=audience)
        if doc is None:
            raise KeyError(request.match_info["doc_id"])
        await rt.db.audit(p.tenant_id, "user", p.id, "document.read", resource_type="holder", resource_id=h["holder_id"], detail={"doc_id": doc["doc_id"]},
                          request_id=request.get("request_id"))
        return json_response({"document": document_view(doc), "text": await store.document_text(doc["doc_id"], audience=audience)})

    async def revise_document(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(is_holder_owner(rt, p, h), "document.revise", h["holder_id"], "only the holder's owner can revise its documents")
        body = await read_json(request)
        text = need_str(body, "text", max_len=5_000_000, allow_empty=False)
        doc_id = request.match_info["doc_id"]
        title, observed_at, reason = opt_str(body, "title", max_len=300), opt_str(body, "observed_at", max_len=40), opt_str(body, "reason", max_len=500) or ""
        store = await embedded_store(rt, h)
        if store is not None:
            result = await store.revise_document(doc_id, text, title=title, observed_at=observed_at, reason=reason, domains=body.get("domains"))
            doc = result.get("document") or {}
            out = await apply_evidence_change(rt, h, "revised", result.get("affected_ref_ids") or [], new_root=result.get("new_source_root_id"), doc_id=doc_id, reason=reason)
            await rt.db.audit(p.tenant_id, "user", p.id, "document.revise", resource_type="holder", resource_id=h["holder_id"], detail={"doc_id": doc_id, "reason": reason},
                              request_id=request.get("request_id"))
            try:
                await rt.engine.wake_goals_for_holder(h)
            except Exception:
                logger.exception("waking goals after revision failed")
            return json_response({"document": document_view(doc), "affected_ref_ids": result.get("affected_ref_ids") or [], "affected_claim_ids": out.get("affected_claim_ids") or []})
        if h.get("mode") == "embedded":
            raise ApiError(503, "the embedded holder is not running on this server", "holder_unavailable")
        await publish_to_holder(rt, h, "revise", {"doc_id": doc_id, "text": text, "title": title, "observed_at": observed_at, "reason": reason, "domains": body.get("domains")},
                                subject=Subjects.holder_ingest(p.tenant_id, h["holder_id"]), msg_id=f"revise:{doc_id}:{new_id('r')}")
        return json_response({"document": {"doc_id": doc_id, "holder_id": h["holder_id"], "title": title, "status": "indexing"}}, 202)

    async def retract_document(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(is_holder_owner(rt, p, h), "document.retract", h["holder_id"], "only the holder's owner can retract its documents")
        body = await read_json(request, required=False)
        reason = opt_str(body, "reason", max_len=500) or "retracted by the owner"
        doc_id = request.match_info["doc_id"]
        store = await embedded_store(rt, h)
        if store is not None:
            result = await store.retract_document(doc_id, reason)
            doc = result.get("document") or {}
            out = await apply_evidence_change(rt, h, "retracted", result.get("affected_ref_ids") or [], doc_id=doc_id, reason=reason)
            await rt.db.audit(p.tenant_id, "user", p.id, "document.retract", resource_type="holder", resource_id=h["holder_id"], detail={"doc_id": doc_id, "reason": reason},
                              request_id=request.get("request_id"))
            return json_response({"document": document_view(doc), "affected_ref_ids": result.get("affected_ref_ids") or [], "affected_claim_ids": out.get("affected_claim_ids") or []})
        if h.get("mode") == "embedded":
            raise ApiError(503, "the embedded holder is not running on this server", "holder_unavailable")
        await publish_to_holder(rt, h, "retract", {"doc_id": doc_id, "reason": reason}, subject=Subjects.holder_ingest(p.tenant_id, h["holder_id"]),
                                msg_id=f"retract:{doc_id}:{new_id('r')}")
        return json_response({"document": {"doc_id": doc_id, "holder_id": h["holder_id"], "status": "retracting"}}, 202)

    async def search_holder(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(rt.authz.can_search_holder(p, h), "holder.search", h["holder_id"], "searching a holder needs ownership or an artifact grant")
        q = (request.query.get("q") or "").strip()
        k = query_int(request, "k", 10, lo=1, hi=50)
        store = await embedded_store(rt, h)
        if store is None:
            raise ApiError(503, "an external holder is searched on the holder's machine", "external_holder")
        results = await store.search(q, k=k, audience=reader_audience(p, h)) if q else []
        for r in results:
            r["holder_id"], r["holder_name"] = h["holder_id"], h["name"]
        await rt.db.audit(p.tenant_id, "user", p.id, "holder.search", resource_type="holder", resource_id=h["holder_id"], detail={"q": q[:200], "hits": len(results)},
                          request_id=request.get("request_id"))
        return json_response({"query": q, "results": results})

    async def holder_stats(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        rt.authz.require(can_see_holder(rt, p, h), "holder.stats", h["holder_id"])
        store = await embedded_store(rt, h)
        stats: dict[str, Any] = dict(h.get("stats") or {})
        if store is not None:
            try:
                stats.update(await store.stats())
            except Exception:
                logger.exception("stats failed for holder %s", h["holder_id"])
        stats.setdefault("documents", 0); stats.setdefault("memories", 0); stats.setdefault("entities", 0); stats.setdefault("queue", 0)
        stats.setdefault("last_ingest_at", None)
        stats["status"] = h.get("status")
        stats["last_heartbeat_at"] = h.get("last_heartbeat_at")
        stats["mode"] = h.get("mode")
        return json_response(stats)

    # ------------------------------------------------------------------ my memory (across own holders)
    def _my_stores_holders(p: Principal) -> list[dict[str, Any]]:
        own = list(rt.org.holders_for_user(p.id))
        granted_ids = {g["resource_id"] for g in p.grants if g["resource_type"] == "holder" and g.get("level") in ("artifact", "raw")}
        seen = {h["holder_id"] for h in own}
        for gid in granted_ids:
            if gid in seen:
                continue
            h = rt.org.get_holder(gid)
            if h and h["tenant_id"] == p.tenant_id and h.get("status") != "revoked":
                own.append(h)
        return own

    async def memory_search(request: web.Request) -> web.Response:
        p = require_user(request)
        q = (request.query.get("q") or "").strip()
        k = query_int(request, "k", 10, lo=1, hi=50)
        results: list[dict[str, Any]] = []
        if q:
            for h in _my_stores_holders(p):
                store = await embedded_store(rt, h)
                if store is None:
                    continue
                try:
                    hits = await store.search(q, k=k, audience=reader_audience(p, h))
                except Exception:
                    logger.exception("memory search failed for %s", h["holder_id"])
                    continue
                for r in hits:
                    r["holder_id"], r["holder_name"] = h["holder_id"], h["name"]
                results.extend(hits)
            results.sort(key=lambda r: -(r.get("score") or 0))
        return json_response({"query": q, "results": results[:k]})

    async def memory_recent(request: web.Request) -> web.Response:
        p = require_user(request)
        limit = limit_of(request, 20, 200)
        items: list[dict[str, Any]] = []
        for h in _my_stores_holders(p):
            store = await embedded_store(rt, h)
            if store is None:
                continue
            items.extend(await recent_memories(store, h, limit))
        items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return json_response(listing(items[:limit]))

    # ------------------------------------------------------------------ holder process endpoints (bearer = holder key)
    async def bootstrap(request: web.Request) -> web.Response:
        hid = request.match_info["holder_id"]
        require_holder(request, hid)
        h = rt.org.get_holder(hid)
        if h is None or h.get("status") == "revoked":
            raise KeyError(hid)
        transport: dict[str, Any] = {"kind": settings.transport}
        if settings.transport == "nats":
            transport.update({"nats_url": settings.nats_url, "nats_stream": settings.nats_stream})
        else:
            transport["coord_db"] = settings.coord_db
        # owner_type/owner_id let the holder apply its own owner checks to connector actions (a user holder accepts them
        # only for its owner) and pick the connector ownership it may hold (personal for users, organization for units)
        return json_response({"holder_id": hid, "tenant_id": h["tenant_id"], "name": h["name"], "mode": h.get("mode"), "route_key": rt.org.route_key(hid),
                              "owner_type": h.get("owner_type"), "owner_id": h.get("owner_id"),
                              "export_policy": h.get("export_policy") or {}, "domains": routable_domains(h), "heartbeat_seconds": 20, "transport": transport})

    async def heartbeat(request: web.Request) -> web.Response:
        hid = request.match_info["holder_id"]
        require_holder(request, hid)
        body = await read_json(request, required=False)
        stats = opt_dict(body, "stats")
        status = opt_str(body, "status", max_len=20) or "online"
        if status not in ("online", "offline"):
            raise ApiError(400, "status must be online or offline")
        await rt.org.holder_heartbeat(hid, stats=stats, status=status)
        h = rt.org.get_holder(hid) or {}
        # tenant and signing-key fingerprint let a running holder notice that the registry moved under it (a demo reset,
        # a key rotation) and restart with a fresh bootstrap instead of listening on stale subjects
        return json_response({"ok": True, "export_policy": h.get("export_policy") or {}, "domains": routable_domains(h),
                              "tenant_id": h.get("tenant_id"), "route_key_id": sha256(rt.org.route_key(hid))[:16]})

    app.router.add_get(f"{prefix}/org", get_org)
    app.router.add_post(f"{prefix}/org/units", create_unit)
    app.router.add_patch(f"{prefix}/org/units/{{unit_id}}", update_unit)
    app.router.add_put(f"{prefix}/org/units/{{unit_id}}/scope", set_scope)
    app.router.add_get(f"{prefix}/org/units/{{unit_id}}", get_unit)
    app.router.add_get(f"{prefix}/org/users", list_users)
    app.router.add_patch(f"{prefix}/org/users/{{user_id}}", update_user)
    app.router.add_get(f"{prefix}/org/memberships", list_memberships)
    app.router.add_post(f"{prefix}/org/memberships", add_membership)
    app.router.add_delete(f"{prefix}/org/memberships", revoke_membership)
    app.router.add_get(f"{prefix}/org/invitations", list_invitations)
    app.router.add_post(f"{prefix}/org/invitations", create_invitation)
    app.router.add_delete(f"{prefix}/org/invitations/{{invitation_id}}", delete_invitation)
    app.router.add_get(f"{prefix}/org/policies", get_policies)
    app.router.add_put(f"{prefix}/org/policies", put_policy)
    app.router.add_get(f"{prefix}/org/network", network)
    app.router.add_get(f"{prefix}/grants", list_grants)
    app.router.add_post(f"{prefix}/grants", create_grant)
    app.router.add_delete(f"{prefix}/grants/{{grant_id}}", delete_grant)
    app.router.add_get(f"{prefix}/holders", list_holders)
    app.router.add_post(f"{prefix}/holders", create_holder)
    app.router.add_patch(f"{prefix}/holders/{{holder_id}}", update_holder)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/rotate-key", rotate_key)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/documents", add_document)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/documents", list_documents)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/documents/{{doc_id}}", get_document)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/documents/{{doc_id}}/revise", revise_document)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/documents/{{doc_id}}/retract", retract_document)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/search", search_holder)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/stats", holder_stats)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/bootstrap", bootstrap)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/heartbeat", heartbeat)
    app.router.add_get(f"{prefix}/me/memory/search", memory_search)
    app.router.add_get(f"{prefix}/me/memory/recent", memory_recent)


async def recent_memories(store: Any, h: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Newest chunk memories of one embedded store in the ``memory`` shape of the API."""
    try:
        mems = await store.store.list_memories(status="active", limit=limit, order="created_at DESC")
    except Exception:
        logger.exception("recent memories failed for %s", h["holder_id"])
        return []
    out = []
    for m in mems:
        d = m.to_dict() if hasattr(m, "to_dict") else dict(m)
        meta = d.get("metadata") or {}
        out.append({"memory_id": d.get("memory_id"), "text": d.get("text"), "kind": d.get("kind"), "observed_at": d.get("observed_at"), "created_at": d.get("created_at"),
                    "doc_id": meta.get("doc_id") or d.get("chat_id"), "title": meta.get("title") or d.get("subject_name"), "domains": meta.get("domains") or [],
                    "holder_id": h["holder_id"], "holder_name": h["name"]})
    return out


__all__ = ["setup", "unit_view", "holder_view", "scopes_of", "unit_or_404", "holder_or_404", "is_holder_owner", "can_see_holder", "visible_holders",
           "embedded_store", "holder_audience", "emit_document_event", "publish_to_holder", "apply_evidence_change", "network_graph", "root_unit",
           "recent_memories", "document_view"]

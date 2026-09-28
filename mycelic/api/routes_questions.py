"""QuestionArtifact routes (docs/mycelic/API.md "Questions").

A person's answer (``/respond``) is built by their own embedded holder store (``manual_response``) when the named
documents live there, so the references it discloses obey the holder's export policy exactly like a machine
answer; the coordinator only ever receives the opaque references.
"""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from ..authz import Forbidden
from ..inquiry.service import QUESTION_KINDS
from .middleware import ApiError, json_response, limit_of, listing, need_str, opt_list, opt_str, query_flag, read_json, require_user
from .routes_org import embedded_store

logger = logging.getLogger(__name__)


def question_or_404(rt: Any, p: Any, question_id: str) -> dict[str, Any]:
    q = rt.questions.get(question_id)
    if q is None or q["tenant_id"] != p.tenant_id:
        raise KeyError(question_id)
    return q


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]

    async def list_questions(request: web.Request) -> web.Response:
        p = require_user(request)
        items = rt.questions.list(p, goal_id=request.query.get("goal_id") or None, status=request.query.get("status") or None,
                                  scope_unit_id=request.query.get("scope_unit_id") or None, needs_input=query_flag(request, "needs_input"),
                                  live_only=query_flag(request, "live"), limit=limit_of(request, 100))
        return json_response(listing(items))

    async def create_question(request: web.Request) -> web.Response:
        p = require_user(request)
        body = await read_json(request)
        text = need_str(body, "text", max_len=2000)
        kind = opt_str(body, "kind", max_len=20) or "gap"
        if kind not in QUESTION_KINDS:
            raise ApiError(400, f"kind must be one of {', '.join(QUESTION_KINDS)}")
        domains = opt_list(body, "candidate_domains") or []
        if any(not isinstance(d, str) for d in domains):
            raise ApiError(400, "'candidate_domains' must be strings")
        data = {"text": text, "goal_id": opt_str(body, "goal_id", max_len=80), "scope_unit_id": opt_str(body, "scope_unit_id", max_len=80), "kind": kind,
                "candidate_domains": domains, "valid_from": opt_str(body, "valid_from", max_len=40), "valid_to": opt_str(body, "valid_to", max_len=40),
                "policy": body.get("policy") if isinstance(body.get("policy"), dict) else None, "budget": body.get("budget") if isinstance(body.get("budget"), dict) else None,
                "trigger": {"kind": "user_request", "note": opt_str(body, "note", max_len=500) or ""}}
        if data["goal_id"]:
            g = rt.goals.get_goal(data["goal_id"])
            if g is None or g["tenant_id"] != p.tenant_id:
                raise KeyError(data["goal_id"])
        if data["scope_unit_id"]:
            u = rt.org.get_unit(data["scope_unit_id"])
            if u is None or u["tenant_id"] != p.tenant_id:
                raise KeyError(data["scope_unit_id"])
        q = await rt.questions.create(p, {k: v for k, v in data.items() if v is not None}, asker_type="user", asker_id=p.id)
        if rt.worker is not None:
            try:
                rt.worker.wake()
            except Exception:
                pass
        return json_response({"question": rt.questions.view(q, principal=p)}, 201)

    async def get_question(request: web.Request) -> web.Response:
        p = require_user(request)
        question_or_404(rt, p, request.match_info["question_id"])
        return json_response(rt.questions.detail(p, request.match_info["question_id"]))

    async def respond(request: web.Request) -> web.Response:
        p = require_user(request)
        q = question_or_404(rt, p, request.match_info["question_id"])
        body = await read_json(request)
        content = opt_str(body, "content", max_len=8000) or ""
        no_evidence = bool(body.get("no_evidence"))
        if not content.strip() and not no_evidence:
            raise ApiError(400, "'content' is required unless no_evidence is set")
        doc_ids = opt_list(body, "doc_ids") or []
        if any(not isinstance(d, str) for d in doc_ids):
            raise ApiError(400, "'doc_ids' must be strings")
        holder_id = opt_str(body, "holder_id", max_len=80)
        if not holder_id:
            if not p.holder_ids:
                raise ApiError(400, "you have no evidence holder; register one under My memory first", "no_holder")
            holder_id = p.holder_ids[0]
        if holder_id not in set(p.holder_ids):
            raise Forbidden("question.respond", q["question_id"], "you do not own that holder")
        holder = rt.org.get_holder(holder_id)
        if holder is None or holder["tenant_id"] != p.tenant_id:
            raise KeyError(holder_id)
        rt.authz.require(rt.authz.can_respond_question(p, {**q, "visibility": (q.get("policy") or {}).get("visibility", "unit")}) or rt.questions.can_view(p, q),
                         "question.respond", q["question_id"], "you cannot see that question")
        refs: list[dict[str, Any]] = []
        store = await embedded_store(rt, holder)
        if store is not None and doc_ids and not no_evidence:
            route = rt.db.one("SELECT route_id FROM question_routes WHERE question_id=? AND holder_id=?", (q["question_id"], holder_id))
            qdict = {"question_id": q["question_id"], "text": q["text"], "policy": q.get("policy") or {}, "candidate_domains": q.get("candidate_domains") or [],
                     "tenant_id": q["tenant_id"], "route_id": route["route_id"] if route else None}
            try:
                art = await store.manual_response(qdict, content, doc_ids)
            except KeyError as exc:
                raise ApiError(400, f"unknown document {exc.args[0] if exc.args else ''} in that holder", "unknown_document") from None
            refs = list(art.get("evidence_refs") or [])
            if art.get("content"):
                content = art["content"]
        out = await rt.questions.human_response(p, q["question_id"], content=content, holder_id=holder_id, evidence_refs=refs, no_evidence=no_evidence)
        if not out.get("accepted"):
            raise ApiError(409, out.get("reason") or "response not accepted", "not_accepted")
        if rt.worker is not None:
            try:
                rt.worker.wake()
            except Exception:
                pass
        return json_response({"response": out.get("response"), "status": out.get("status"), "all_in": bool(out.get("all_in")), "duplicate": bool(out.get("duplicate"))}, 201)

    async def cancel(request: web.Request) -> web.Response:
        p = require_user(request)
        q = question_or_404(rt, p, request.match_info["question_id"])
        body = await read_json(request, required=False)
        out = await rt.questions.cancel(p, q["question_id"], reason=opt_str(body, "reason", max_len=500) or "cancelled")
        return json_response({"question": out})

    app.router.add_get(f"{prefix}/questions", list_questions)
    app.router.add_post(f"{prefix}/questions", create_question)
    app.router.add_get(f"{prefix}/questions/{{question_id}}", get_question)
    app.router.add_post(f"{prefix}/questions/{{question_id}}/respond", respond)
    app.router.add_post(f"{prefix}/questions/{{question_id}}/cancel", cancel)


__all__ = ["setup", "question_or_404"]

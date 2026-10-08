"""Discoveries, claims, evidence references and conflicts (docs/mycelic/API.md "Discoveries, claims, evidence, conflicts").

Reads go through the knowledge service's authorization-aware lists and details; the two things added here are the
raw-evidence fetch (owner or a ``raw`` grant, served by the embedded store or by the holder process over the
transport) and ``/conflicts/{id}/investigate``, which turns a disagreement into a bounded contradiction question.
"""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from ..authz import Forbidden
from ..transport import Envelope, Subjects, TransportError
from .middleware import ApiError, json_response, limit_of, listing, need_str, opt_str, read_json, require_user
from .routes_org import embedded_store

logger = logging.getLogger(__name__)

REVIEW_ACTIONS = ("reviewed", "accepted", "dismissed", "escalated", "comment")
CONFLICT_OUTCOMES = ("a_wins", "b_wins", "both_valid_scoped", "both_retracted", "unresolved")
RAW_TIMEOUT_SECONDS = 10.0


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]

    # ------------------------------------------------------------------ discoveries
    async def list_discoveries(request: web.Request) -> web.Response:
        p = require_user(request)
        items = rt.knowledge.list_discoveries(p, level=request.query.get("level") or None, scope_unit_id=request.query.get("scope_unit_id") or None,
                                              status=request.query.get("status") or None, goal_id=request.query.get("goal_id") or None,
                                              kind=request.query.get("kind") or None, limit=limit_of(request, 100))
        return json_response(listing(items))

    async def get_discovery(request: web.Request) -> web.Response:
        p = require_user(request)
        did = request.match_info["id"]
        d = rt.knowledge.get_discovery(did)
        if d is None or d["tenant_id"] != p.tenant_id:
            raise KeyError(did)
        return json_response(rt.knowledge.discovery_detail(p, did))

    async def review_discovery(request: web.Request) -> web.Response:
        p = require_user(request)
        did = request.match_info["id"]
        d = rt.knowledge.get_discovery(did)
        if d is None or d["tenant_id"] != p.tenant_id:
            raise KeyError(did)
        body = await read_json(request)
        action = body.get("action")
        if action not in REVIEW_ACTIONS:
            raise ApiError(400, f"action must be one of {', '.join(REVIEW_ACTIONS)}")
        out = await rt.knowledge.review_discovery(p, did, action, note=opt_str(body, "note", max_len=2000) or "")
        return json_response({"discovery": rt.knowledge.discovery_summary(out)})

    # ------------------------------------------------------------------ claims
    async def list_claims(request: web.Request) -> web.Response:
        p = require_user(request)
        items = rt.knowledge.list_claims(p, scope_unit_id=request.query.get("scope_unit_id") or None, status=request.query.get("status") or None,
                                         goal_id=request.query.get("goal_id") or None, q=request.query.get("q") or None, limit=limit_of(request, 100))
        return json_response(listing(items))

    async def get_claim(request: web.Request) -> web.Response:
        p = require_user(request)
        cid = request.match_info["id"]
        c = rt.knowledge.get_claim(cid)
        if c is None or c["tenant_id"] != p.tenant_id:
            raise KeyError(cid)
        return json_response(rt.knowledge.claim_detail(p, cid))

    async def retract_claim(request: web.Request) -> web.Response:
        p = require_user(request)
        cid = request.match_info["id"]
        c = rt.knowledge.get_claim(cid)
        if c is None or c["tenant_id"] != p.tenant_id:
            raise KeyError(cid)
        body = await read_json(request, required=False)
        out = await rt.knowledge.retract_claim(p, cid, reason=opt_str(body, "reason", max_len=1000) or "retracted by a person")
        return json_response({"claim": rt.knowledge.claim_summary(out)})

    # ------------------------------------------------------------------ evidence
    def _ref_and_claims(p: Any, ref_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        ref = rt.knowledge.get_ref(ref_id)
        if ref is None or ref["tenant_id"] != p.tenant_id:
            raise KeyError(ref_id)
        ids = [r["claim_id"] for r in rt.db.all("SELECT claim_id FROM claim_evidence WHERE ref_id=?", (ref_id,))]
        visible = [c for c in rt.knowledge.claims_by_ids(ids) if rt.authz.can_view_scoped(p, c, resource_type="claim")]
        return ref, visible

    async def get_evidence(request: web.Request) -> web.Response:
        p = require_user(request)
        ref, claims = _ref_and_claims(p, request.match_info["ref_id"])
        rt.authz.require(rt.authz.can_view_evidence_ref(p, ref, via_claim_visible=bool(claims)), "evidence.view", ref["ref_id"])
        return json_response({"evidence_ref": rt.knowledge.ref_view(ref), "claims": [rt.knowledge.claim_summary(c) for c in claims]})

    async def get_raw(request: web.Request) -> web.Response:
        p = require_user(request)
        ref, _ = _ref_and_claims(p, request.match_info["ref_id"])
        holder = rt.org.get_holder(ref["holder_id"])
        if holder is None:
            raise KeyError(ref["holder_id"])
        rt.authz.require(rt.authz.can_view_raw_evidence(p, holder), "evidence.raw", ref["ref_id"], "raw evidence needs ownership of the holder or a raw grant")
        store = await embedded_store(rt, holder)
        if store is not None:
            raw = await store.raw_for_ref(ref["ref_id"])
        else:
            if holder.get("mode") == "embedded":
                raise ApiError(503, "the embedded holder is not running on this server", "holder_unavailable")
            if rt.transport is None:
                raise ApiError(503, "no transport configured", "transport")
            env = Envelope.new(Subjects.holder_raw(p.tenant_id, holder["holder_id"]), "raw_request", p.tenant_id,
                               {"ref_id": ref["ref_id"], "holder_id": holder["holder_id"], "grant_token": p.id, "requested_by": p.id})
            env.sign(rt.org.route_key(holder["holder_id"]))
            try:
                reply = await rt.transport.request(env, timeout=RAW_TIMEOUT_SECONDS)
            except TransportError as exc:
                raise ApiError(504, f"the holder did not answer: {exc}", "holder_timeout") from None
            raw = reply.payload if isinstance(reply.payload, dict) and not reply.payload.get("error") else None
        if not raw:
            raise KeyError(ref["ref_id"])
        await rt.db.audit(p.tenant_id, "user", p.id, "evidence.raw", resource_type="evidence_ref", resource_id=ref["ref_id"], detail={"holder_id": holder["holder_id"]},
                          request_id=request.get("request_id"))
        return json_response({"ref_id": ref["ref_id"], "holder_id": holder["holder_id"], "doc_id": raw.get("doc_id"), "title": raw.get("title"), "text": raw.get("text"),
                              "observed_at": raw.get("observed_at"), "version": raw.get("version"), "status": raw.get("status"),
                              "source_root_id": raw.get("source_root_id"), "chunk_text": raw.get("chunk_text")})

    # ------------------------------------------------------------------ conflicts
    def _conflict_or_404(p: Any, conflict_id: str) -> dict[str, Any]:
        k = rt.knowledge.get_conflict(conflict_id)
        if k is None or k["tenant_id"] != p.tenant_id:
            raise KeyError(conflict_id)
        return k

    def _require_conflict_visible(p: Any, k: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        a, b = rt.knowledge.get_claim(k["claim_a_id"]), rt.knowledge.get_claim(k["claim_b_id"])
        ok = any(c is not None and rt.authz.can_view_scoped(p, c, resource_type="claim") for c in (a, b))
        rt.authz.require(ok, "conflict.view", k["conflict_id"])
        return a, b

    async def list_conflicts(request: web.Request) -> web.Response:
        p = require_user(request)
        items = rt.knowledge.list_conflicts(p, status=request.query.get("status") or None, scope_unit_id=request.query.get("scope_unit_id") or None,
                                            goal_id=request.query.get("goal_id") or None, limit=limit_of(request, 100))
        return json_response(listing(items))

    async def resolve_conflict(request: web.Request) -> web.Response:
        p = require_user(request)
        k = _conflict_or_404(p, request.match_info["id"])
        _require_conflict_visible(p, k)
        body = await read_json(request)
        outcome = body.get("outcome")
        if outcome not in CONFLICT_OUTCOMES:
            raise ApiError(400, f"outcome must be one of {', '.join(CONFLICT_OUTCOMES)}")
        out = await rt.knowledge.resolve_conflict(p, k["conflict_id"], outcome, note=opt_str(body, "note", max_len=2000) or "")
        return json_response({"conflict": rt.knowledge.conflict_view(out, p)})

    async def investigate(request: web.Request) -> web.Response:
        p = require_user(request)
        k = _conflict_or_404(p, request.match_info["id"])
        a, b = _require_conflict_visible(p, k)
        if k["status"] == "resolved":
            raise ApiError(409, "conflict is already resolved")
        body = await read_json(request, required=False)
        lead = a or b or {}
        text = (opt_str(body, "text", max_len=2000) or
                f"Records disagree about: {(a or {}).get('text', '')[:160]} versus {(b or {}).get('text', '')[:160]}. What do your own records show, with dates?")
        data = {"text": text, "goal_id": lead.get("goal_id"), "scope_unit_id": lead.get("scope_unit_id"), "kind": "contradiction",
                "trigger": {"kind": "contradiction", "conflict_id": k["conflict_id"], "claim_id": (a or {}).get("claim_id"), "ref_type": "conflict", "ref_id": k["conflict_id"]},
                "motivating_lineage": [{"type": "conflict", "id": k["conflict_id"]}] + [{"type": "claim", "id": c["claim_id"]} for c in (a, b) if c],
                "candidate_domains": list(dict.fromkeys(_domains_of(a) + _domains_of(b)))}
        q = await rt.questions.create(p, {kk: v for kk, v in data.items() if v is not None}, asker_type="user", asker_id=p.id,
                                      estimates={"uncertainty": 0.9, "impact": 0.7, "missing_evidence": 0.6, "information_gain": 0.8})
        out = await rt.knowledge.add_investigation(p, k["conflict_id"], "question", f"contradiction question {q['question_id']} asked", question_id=q["question_id"])
        if rt.worker is not None:
            try:
                rt.worker.wake()
            except Exception:
                pass
        return json_response({"conflict": rt.knowledge.conflict_view(out, p), "question": rt.questions.view(q, principal=p)}, 201)

    def _domains_of(c: dict[str, Any] | None) -> list[str]:
        if not c or not c.get("question_id"):
            return []
        q = rt.questions.get(c["question_id"])
        return list((q or {}).get("candidate_domains") or [])

    app.router.add_get(f"{prefix}/discoveries", list_discoveries)
    app.router.add_get(f"{prefix}/discoveries/{{id}}", get_discovery)
    app.router.add_post(f"{prefix}/discoveries/{{id}}/review", review_discovery)
    app.router.add_get(f"{prefix}/claims", list_claims)
    app.router.add_get(f"{prefix}/claims/{{id}}", get_claim)
    app.router.add_post(f"{prefix}/claims/{{id}}/retract", retract_claim)
    app.router.add_get(f"{prefix}/evidence/{{ref_id}}", get_evidence)
    app.router.add_get(f"{prefix}/evidence/{{ref_id}}/raw", get_raw)
    app.router.add_get(f"{prefix}/conflicts", list_conflicts)
    app.router.add_post(f"{prefix}/conflicts/{{id}}/resolve", resolve_conflict)
    app.router.add_post(f"{prefix}/conflicts/{{id}}/investigate", investigate)


__all__ = ["setup"]

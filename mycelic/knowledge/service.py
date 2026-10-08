"""Organizational knowledge: claims, evidence references, derivations, conflicts, revisions, discoveries.

All writes are transactional and idempotent where a background step may replay them (``idempotency_key`` on
claims and discoveries). Every status change writes a ``revisions`` row and an outbox event, and the audit
log records who did it. Raw evidence never enters this module: evidence references carry only what the
holder's export policy disclosed.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Iterable, Mapping

from ..authz import Authorizer, Forbidden, Principal
from ..db.coord import CoordDB, row_to_dict, rows_to_dicts
from ..org import LEVEL_FOR_UNIT_TYPE, OrgService
from ..util import j, jl, new_id, now_iso, sha256
from .gate import CommitGate, GateResult
from .support import compute_support, freshness, is_active

logger = logging.getLogger(__name__)

CLAIM_JSON = ("support",)
DISCOVERY_JSON = ("claim_ids", "followup_question_ids")
REF_JSON = ("meta",)
CONFLICT_JSON = ("investigation", "resolution")
DERIVATION_JSON = ("input_claim_ids", "input_ref_ids", "response_ids")
REVISION_JSON = ("before", "after")


class KnowledgeService:
    def __init__(self, db: CoordDB, org: OrgService, authz: Authorizer) -> None:
        self.db = db
        self.org = org
        self.authz = authz
        self.gate = CommitGate(authz)

    # ================================================================== evidence references
    def _ref_row(self, c: sqlite3.Connection, ref_id: str) -> dict[str, Any] | None:
        return row_to_dict(c.execute("SELECT * FROM evidence_refs WHERE ref_id=?", (ref_id,)).fetchone(), json_fields=REF_JSON)

    def upsert_refs_sync(self, c: sqlite3.Connection, tenant_id: str, holder_id: str, refs: Iterable[Mapping[str, Any]]) -> list[str]:
        """Store the references a holder disclosed in a response (idempotent on ref_id)."""
        now = now_iso()
        out: list[str] = []
        for r in refs:
            rid = r.get("ref_id")
            if not rid:
                continue
            c.execute(
                """INSERT INTO evidence_refs(ref_id, tenant_id, holder_id, source_root_id, root_known, kind, title, disclosed_excerpt,
                                             disclosure_level, observed_at, freshness_at, status, version, meta, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?)
                   ON CONFLICT(ref_id) DO UPDATE SET disclosed_excerpt=excluded.disclosed_excerpt, freshness_at=COALESCE(excluded.freshness_at, evidence_refs.freshness_at),
                     observed_at=COALESCE(excluded.observed_at, evidence_refs.observed_at), updated_at=excluded.updated_at
                   WHERE evidence_refs.holder_id = excluded.holder_id AND evidence_refs.tenant_id = excluded.tenant_id""",
                (rid, tenant_id, holder_id, r.get("source_root_id"), int(bool(r.get("root_known", bool(r.get("source_root_id"))))),
                 r.get("kind") or "document", (r.get("title") or "")[:300], r.get("disclosed_excerpt") or "", r.get("disclosure_level") or "excerpt",
                 r.get("observed_at"), r.get("freshness_at") or r.get("observed_at"), j(r.get("meta") or {}), now, now),
            )
            owner = c.execute("SELECT holder_id, tenant_id FROM evidence_refs WHERE ref_id=?", (rid,)).fetchone()
            if owner is None or owner["holder_id"] != holder_id or owner["tenant_id"] != tenant_id:
                # a reference id already owned by another holder: never let one holder overwrite or borrow another's evidence
                self.db.audit_sync(c, tenant_id, "holder", holder_id, "evidence.ref_collision", resource_type="evidence_ref", resource_id=rid, outcome="deny")
                continue
            out.append(rid)
        return out

    async def upsert_refs(self, tenant_id: str, holder_id: str, refs: Iterable[Mapping[str, Any]]) -> list[str]:
        async with self.db.tx() as c:
            return self.upsert_refs_sync(c, tenant_id, holder_id, refs)

    def get_ref(self, ref_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM evidence_refs WHERE ref_id=?", (ref_id,)), json_fields=REF_JSON)

    def refs_by_ids(self, ref_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(ref_ids))
        if not ids:
            return []
        rows = self.db.all(f"SELECT * FROM evidence_refs WHERE ref_id IN ({','.join('?' * len(ids))})", ids)
        order = {r: i for i, r in enumerate(ids)}
        out = rows_to_dicts(rows, json_fields=REF_JSON)
        out.sort(key=lambda d: order.get(d["ref_id"], 0))
        return out

    def refs_for_claim(self, claim_id: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT e.*, ce.role, ce.weight FROM claim_evidence ce JOIN evidence_refs e ON e.ref_id = ce.ref_id WHERE ce.claim_id=? ORDER BY ce.role, e.observed_at DESC", (claim_id,))
        return rows_to_dicts(rows, json_fields=REF_JSON)

    def ref_view(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        """The disclosed view of a reference (API.md ``evidence_ref``)."""
        holder = self.org.get_holder(ref["holder_id"])
        copies = 0
        if ref.get("source_root_id"):
            copies = int(self.db.scalar("SELECT COUNT(*) FROM evidence_refs WHERE tenant_id=? AND source_root_id=?", (ref["tenant_id"], ref["source_root_id"]), 0)) - 1
        return {"ref_id": ref["ref_id"], "holder_id": ref["holder_id"], "holder_name": holder["name"] if holder else "", "holder_owner": {"type": holder["owner_type"], "id": holder["owner_id"]} if holder else None,
                "source_root_id": ref.get("source_root_id"), "root_known": bool(ref.get("root_known")), "kind": ref.get("kind"), "title": ref.get("title"),
                "disclosed_excerpt": ref.get("disclosed_excerpt") or "", "disclosure_level": ref.get("disclosure_level"), "observed_at": ref.get("observed_at"),
                "freshness_at": ref.get("freshness_at"), "status": ref.get("status"), "version": ref.get("version"), "copies_of_same_root": max(0, copies),
                "role": ref.get("role"), "weight": ref.get("weight"), "demoted": ref.get("demoted"),
                "revised_root_id": (ref.get("meta") or {}).get("revised_root_id") if isinstance(ref.get("meta"), dict) else None}

    # ================================================================== claims
    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM claims WHERE claim_id=?", (claim_id,)), json_fields=CLAIM_JSON)

    def claims_by_ids(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        lst = list(dict.fromkeys(ids))
        if not lst:
            return []
        rows = self.db.all(f"SELECT * FROM claims WHERE claim_id IN ({','.join('?' * len(lst))})", lst)
        order = {r: i for i, r in enumerate(lst)}
        out = rows_to_dicts(rows, json_fields=CLAIM_JSON)
        out.sort(key=lambda d: order.get(d["claim_id"], 0))
        return out

    def claim_summary(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        d = dict(claim)
        creator = None
        if d.get("created_by_type") == "user":
            u = self.org.get_user(d["created_by_id"])
            creator = {"type": "user", "id": d["created_by_id"], "name": u["name"] if u else ""}
        else:
            creator = {"type": d.get("created_by_type"), "id": d.get("created_by_id"), "name": d.get("created_by_type")}
        d["created_by"] = creator
        d.pop("created_by_type", None); d.pop("created_by_id", None)
        return d

    def _claim_idempotency(self, tenant_id: str, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        r = self.db.one("SELECT c.* FROM claims c JOIN revisions r ON r.object_id = c.claim_id AND r.object_type='claim' AND r.version=1 "
                        "WHERE c.tenant_id=? AND json_extract(r.after, '$.idempotency_key') = ? LIMIT 1", (tenant_id, key))
        return row_to_dict(r, json_fields=CLAIM_JSON)

    async def commit_claim(self, principal: Principal, candidate: Mapping[str, Any], *, evidence: Iterable[Mapping[str, Any]] = (),
                           derivation: Mapping[str, Any] | None = None, input_claim_ids: Iterable[str] = (), question: Mapping[str, Any] | None = None,
                           idempotency_key: str | None = None, conflict_with: Iterable[str] = (), conflict_summary: str = "",
                           policy: Mapping[str, Any] | None = None) -> tuple[dict[str, Any] | None, GateResult]:
        """Run the commit gate and, when it passes, write claim + evidence links + derivation + revision + event in one
        transaction. Returns (claim, gate_result); claim is None when rejected. Replaying with the same idempotency key
        returns the existing claim without writing."""
        tenant_id = candidate["tenant_id"]
        existing = self._claim_idempotency(tenant_id, idempotency_key)
        if existing is not None:
            return existing, GateResult(True, existing["status"], ["idempotent replay"], existing.get("support") or {}, {}, {})
        pol = dict(policy or self.org.policies(tenant_id))
        ev_rows: list[dict[str, Any]] = []
        for e in evidence:
            ref = self.get_ref(e["ref_id"]) if isinstance(e, Mapping) and "holder_id" not in e else dict(e)
            if ref is None:
                continue
            ref = dict(ref)
            ref["role"] = e.get("role", "supports")
            ref["weight"] = float(e.get("weight", 1.0))
            ev_rows.append(ref)
        inputs = self.claims_by_ids(input_claim_ids)
        conflict_ids = list(conflict_with)
        result = self.gate.check(principal, candidate, evidence=ev_rows, policy=pol, has_open_conflict=bool(conflict_ids), question=question, input_claims=inputs)
        await self.db.audit(tenant_id, principal.kind, principal.id, "claim.gate", resource_type="claim", resource_id=idempotency_key,
                            outcome="allow" if result.ok else "deny", detail={"status": result.status, "reasons": result.reasons})
        if not result.ok:
            return None, result
        cid = new_id("claim")
        now = now_iso()
        scope = candidate.get("scope_unit_id")
        support = dict(result.support)
        support["freshness"] = result.freshness
        async with self.db.tx() as c:
            c.execute(
                """INSERT INTO claims(claim_id, tenant_id, scope_unit_id, visibility, owner_user_id, text, kind, status, confidence, valid_from, valid_to,
                                      version, supersedes_claim_id, goal_id, question_id, created_by_type, created_by_id, support, freshness_at, is_demo, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cid, tenant_id, scope, candidate.get("visibility", "unit"), candidate.get("owner_user_id"), candidate["text"].strip(), candidate.get("kind", "finding"),
                 result.status, float(candidate.get("confidence", 0.5)), candidate.get("valid_from"), candidate.get("valid_to"), candidate.get("supersedes_claim_id"),
                 candidate.get("goal_id"), candidate.get("question_id"), candidate.get("created_by_type", principal.kind), candidate.get("created_by_id", principal.id),
                 j(support), result.freshness.get("freshness_at"), int(bool(candidate.get("is_demo", principal.is_demo))), now, now),
            )
            # the role the gate decided (evidence it demoted is stored as context, so no later recomputation counts it again)
            for r in result.refs:
                c.execute("INSERT OR REPLACE INTO claim_evidence(claim_id, ref_id, role, weight) VALUES (?, ?, ?, ?)", (cid, r["ref_id"], r.get("role") or "supports", float(r.get("weight", 1.0))))
            d = dict(derivation or {})
            c.execute("INSERT INTO derivations(derivation_id, tenant_id, claim_id, operator, input_claim_ids, input_ref_ids, response_ids, model, contributor_type, contributor_id, rationale, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (new_id("der"), tenant_id, cid, d.get("operator", "synthesize"), j([x["claim_id"] for x in inputs]), j([r["ref_id"] for r in ev_rows]),
                       j(list(d.get("response_ids") or [])), d.get("model"), d.get("contributor_type", principal.kind), d.get("contributor_id", principal.id),
                       d.get("rationale", ""), now))
            if candidate.get("supersedes_claim_id"):
                c.execute("UPDATE claims SET superseded_by=?, updated_at=? WHERE claim_id=?", (cid, now, candidate["supersedes_claim_id"]))
            self._revision_sync(c, tenant_id, "claim", cid, 1, principal, "commit", {}, {"status": result.status, "reasons": result.reasons, "idempotency_key": idempotency_key,
                                                                                            "text": candidate["text"].strip()})
            for other in conflict_ids:
                self._open_conflict_sync(c, tenant_id, cid, other, conflict_summary or "responses disagree", candidate.get("question_id"), principal)
            self.db.emit_sync(c, tenant_id, "claim.committed", ref_type="claim", ref_id=cid, payload={"status": result.status, "goal_id": candidate.get("goal_id"), "scope_unit_id": scope},
                              audience={"unit_ids": [scope] if scope else [], "visibility": candidate.get("visibility", "unit"), "user_ids": [candidate.get("owner_user_id")] if candidate.get("owner_user_id") else []})
        claim = self.get_claim(cid)
        return claim, result

    async def revise_claim(self, principal: Principal, claim_id: str, *, text: str | None = None, status: str | None = None,
                           confidence: float | None = None, reason: str = "", support: Mapping[str, Any] | None = None,
                           freshness_at: str | None = None, event_kind: str = "claim.revised") -> dict[str, Any]:
        before = self.get_claim(claim_id)
        if before is None:
            raise KeyError(claim_id)
        now = now_iso()
        async with self.db.tx() as c:
            fields, args = ["version=version+1", "updated_at=?"], [now]
            if text is not None:
                fields.append("text=?"); args.append(text.strip())
            if status is not None:
                fields.append("status=?"); args.append(status)
            if confidence is not None:
                fields.append("confidence=?"); args.append(float(confidence))
            if support is not None:
                fields.append("support=?"); args.append(j(support))
            if freshness_at is not None:
                fields.append("freshness_at=?"); args.append(freshness_at)
            args.append(claim_id)
            c.execute(f"UPDATE claims SET {', '.join(fields)} WHERE claim_id=?", args)
            after = {"text": text, "status": status, "confidence": confidence, "reason": reason}
            self._revision_sync(c, before["tenant_id"], "claim", claim_id, int(before["version"]) + 1, principal, reason,
                                {"text": before["text"], "status": before["status"], "confidence": before["confidence"]}, {k: v for k, v in after.items() if v is not None})
            self.db.emit_sync(c, before["tenant_id"], event_kind, ref_type="claim", ref_id=claim_id, payload={"status": status or before["status"], "reason": reason, "goal_id": before.get("goal_id")},
                              audience={"unit_ids": [before["scope_unit_id"]] if before.get("scope_unit_id") else [], "visibility": before["visibility"],
                                        "user_ids": [before["owner_user_id"]] if before.get("owner_user_id") else []})
        return self.get_claim(claim_id)  # type: ignore[return-value]

    async def retract_claim(self, principal: Principal, claim_id: str, reason: str = "") -> dict[str, Any]:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        if not principal.is_system and not (claim.get("created_by_id") == principal.id or self.authz.can_manage_unit_knowledge(principal, claim.get("scope_unit_id") or "")):
            raise Forbidden("claim.retract", claim_id)
        out = await self.revise_claim(principal, claim_id, status="retracted", reason=reason or "retracted", event_kind="claim.retracted")
        await self.db.audit(claim["tenant_id"], principal.kind, principal.id, "claim.retract", resource_type="claim", resource_id=claim_id, detail={"reason": reason})
        await self._propagate_status(principal, claim_id, "retracted", reason=f"input claim {claim_id} retracted")
        return out

    async def _propagate_status(self, principal: Principal, claim_id: str, status: str, *, reason: str) -> list[str]:
        """Derived claims (derivations whose input_claim_ids include ``claim_id``) become stale."""
        rows = self.db.all("SELECT claim_id FROM derivations WHERE tenant_id=(SELECT tenant_id FROM claims WHERE claim_id=?) AND input_claim_ids LIKE ?", (claim_id, f'%"{claim_id}"%'))
        affected = []
        for r in rows:
            cl = self.get_claim(r["claim_id"])
            if cl and cl["status"] not in ("retracted", "stale"):
                await self.revise_claim(principal, r["claim_id"], status="stale", reason=reason, event_kind="claim.stale")
                affected.append(r["claim_id"])
        return affected

    def list_claims(self, principal: Principal, *, scope_unit_id: str | None = None, status: str | None = None, goal_id: str | None = None,
                    question_id: str | None = None, q: str | None = None, limit: int = 100, include_retracted: bool = False) -> list[dict[str, Any]]:
        vis, args = self.authz.visibility_sql(principal, alias="c", resource_type="claim")
        sql = f"SELECT c.* FROM claims c WHERE {vis}"
        if scope_unit_id:
            ids = self.org.descendants(scope_unit_id)
            sql += f" AND c.scope_unit_id IN ({','.join('?' * len(ids))})"; args.extend(ids)
        if status:
            sql += " AND c.status=?"; args.append(status)
        elif not include_retracted:
            sql += " AND c.status <> 'retracted'"
        if goal_id:
            sql += " AND c.goal_id=?"; args.append(goal_id)
        if question_id:
            sql += " AND c.question_id=?"; args.append(question_id)
        if q:
            sql += " AND lower(c.text) LIKE ?"; args.append(f"%{q.lower()}%")
        sql += " ORDER BY c.updated_at DESC LIMIT ?"; args.append(int(limit))
        return [self.claim_summary(d) for d in rows_to_dicts(self.db.all(sql, args), json_fields=CLAIM_JSON)]

    def claim_detail(self, principal: Principal, claim_id: str) -> dict[str, Any]:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        self.authz.require(self.authz.can_view_scoped(principal, claim, resource_type="claim"), "claim.view", claim_id)
        refs, demotions = self.effective_refs_for_claim(claim)
        pol = self.org.policies(claim["tenant_id"])
        supporting = [r for r in refs if r.get("role") == "supports" and is_active(r)]
        sup = compute_support(refs)
        history = []
        cur = claim.get("supersedes_claim_id")
        while cur and len(history) < 20:
            prev = self.get_claim(cur)
            if not prev:
                break
            history.append(self.claim_summary(prev))
            cur = prev.get("supersedes_claim_id")
        return {
            "claim": self.claim_summary(claim),
            "evidence": [self.ref_view(r) for r in refs],
            "derivations": rows_to_dicts(self.db.all("SELECT * FROM derivations WHERE claim_id=? ORDER BY created_at", (claim_id,)), json_fields=DERIVATION_JSON),
            "conflicts": [self.conflict_view(x, principal) for x in self.conflicts_for_claim(claim_id)],
            "revisions": self.revisions_for("claim", claim_id),
            "history": history,
            "dependencies": {"roots": sup["roots"], "shared": sup["shared_dependencies"], "unknown": sup["unknown_ref_ids"], "inactive": sup["inactive_ref_ids"]},
            "support": claim.get("support") or sup,
            "support_notes": demotions,
            "freshness": freshness(supporting, freshness_days=float(pol.get("freshness_days", 90))),
        }

    # ================================================================== conflicts
    def _open_conflict_sync(self, c: sqlite3.Connection, tenant_id: str, a: str, b: str, summary: str, question_id: str | None, principal: Principal) -> str:
        a, b = sorted([a, b])
        r = c.execute("SELECT conflict_id, status FROM conflicts WHERE claim_a_id=? AND claim_b_id=?", (a, b)).fetchone()
        if r is not None:
            return r["conflict_id"]
        cid = new_id("conf")
        now = now_iso()
        c.execute("INSERT INTO conflicts(conflict_id, tenant_id, claim_a_id, claim_b_id, status, summary, investigation, question_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
                  (cid, tenant_id, a, b, summary, j([{"at": now, "actor": principal.id, "step": "opened", "note": summary, "question_id": question_id}]), question_id, now, now))
        for x in (a, b):
            row = c.execute("SELECT status, version, scope_unit_id, visibility FROM claims WHERE claim_id=?", (x,)).fetchone()
            if row and row["status"] not in ("retracted",):
                c.execute("UPDATE claims SET status='contested', version=version+1, updated_at=? WHERE claim_id=?", (now, x))
                self._revision_sync(c, tenant_id, "claim", x, int(row["version"]) + 1, principal, f"conflict {cid} opened", {"status": row["status"]}, {"status": "contested"})
        scope = c.execute("SELECT scope_unit_id, visibility FROM claims WHERE claim_id=?", (a,)).fetchone()
        self.db.emit_sync(c, tenant_id, "conflict.opened", ref_type="conflict", ref_id=cid, payload={"claim_a_id": a, "claim_b_id": b, "summary": summary},
                          audience={"unit_ids": [scope["scope_unit_id"]] if scope and scope["scope_unit_id"] else [], "visibility": scope["visibility"] if scope else "unit"})
        self.db.audit_sync(c, tenant_id, principal.kind, principal.id, "conflict.open", resource_type="conflict", resource_id=cid, detail={"a": a, "b": b})
        return cid

    async def open_conflict(self, principal: Principal, tenant_id: str, claim_a: str, claim_b: str, summary: str, *, question_id: str | None = None) -> dict[str, Any]:
        async with self.db.tx() as c:
            cid = self._open_conflict_sync(c, tenant_id, claim_a, claim_b, summary, question_id, principal)
        return self.get_conflict(cid)  # type: ignore[return-value]

    def get_conflict(self, conflict_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM conflicts WHERE conflict_id=?", (conflict_id,)), json_fields=CONFLICT_JSON)

    def conflicts_for_claim(self, claim_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM conflicts WHERE (claim_a_id=? OR claim_b_id=?)", [claim_id, claim_id]
        if status:
            sql += " AND status=?"; args.append(status)
        return rows_to_dicts(self.db.all(sql + " ORDER BY created_at DESC", args), json_fields=CONFLICT_JSON)

    def conflict_view(self, conflict: Mapping[str, Any], principal: Principal | None = None) -> dict[str, Any]:
        """A conflict with both sides. With a principal, a side the viewer may not see is reduced to its id and status, and
        the summary (which may quote it) is replaced, so a visible claim never exposes a private one it disagrees with."""
        d = dict(conflict)
        a, b = self.get_claim(d["claim_a_id"]), self.get_claim(d["claim_b_id"])
        hidden = False
        for key, cl in (("claim_a", a), ("claim_b", b)):
            if cl is None:
                d[key] = None
            elif principal is not None and not self.authz.can_view_scoped(principal, cl, resource_type="claim"):
                d[key] = {"claim_id": cl["claim_id"], "status": cl["status"], "visible": False, "text": "[not visible to you]"}
                hidden = True
            else:
                d[key] = {**self.claim_summary(cl), "visible": True}
        if hidden:
            d["summary"] = "Disagreement with a claim outside your access."
            d["investigation"] = [{k: v for k, v in step.items() if k in ("at", "step", "outcome")} for step in (d.get("investigation") or [])]
        return d

    def list_conflicts(self, principal: Principal, *, status: str | None = None, scope_unit_id: str | None = None, goal_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        vis, args = self.authz.visibility_sql(principal, alias="c", resource_type="claim")
        sql = f"SELECT k.* FROM conflicts k JOIN claims c ON c.claim_id = k.claim_a_id WHERE {vis}"
        if status:
            sql += " AND k.status=?"; args.append(status)
        if scope_unit_id:
            ids = self.org.descendants(scope_unit_id)
            sql += f" AND c.scope_unit_id IN ({','.join('?' * len(ids))})"; args.extend(ids)
        if goal_id:
            sql += " AND c.goal_id=?"; args.append(goal_id)
        sql += " ORDER BY k.updated_at DESC LIMIT ?"; args.append(int(limit))
        return [self.conflict_view(d, principal) for d in rows_to_dicts(self.db.all(sql, args), json_fields=CONFLICT_JSON)]

    async def add_investigation(self, principal: Principal, conflict_id: str, step: str, note: str, *, question_id: str | None = None) -> dict[str, Any]:
        k = self.get_conflict(conflict_id)
        if k is None:
            raise KeyError(conflict_id)
        inv = list(k.get("investigation") or [])
        inv.append({"at": now_iso(), "actor": principal.id, "step": step, "note": note, "question_id": question_id})
        async with self.db.tx() as c:
            c.execute("UPDATE conflicts SET investigation=?, status=CASE WHEN status='open' THEN 'investigating' ELSE status END, question_id=COALESCE(?, question_id), updated_at=? WHERE conflict_id=?",
                      (j(inv), question_id, now_iso(), conflict_id))
        return self.get_conflict(conflict_id)  # type: ignore[return-value]

    async def resolve_conflict(self, principal: Principal, conflict_id: str, outcome: str, note: str = "") -> dict[str, Any]:
        k = self.get_conflict(conflict_id)
        if k is None:
            raise KeyError(conflict_id)
        a, b = self.get_claim(k["claim_a_id"]), self.get_claim(k["claim_b_id"])
        if not principal.is_system:
            scope = (a or {}).get("scope_unit_id") or (b or {}).get("scope_unit_id") or ""
            self.authz.require(self.authz.can_manage_unit_knowledge(principal, scope) or principal.kind == "loop", "conflict.resolve", conflict_id)
        if outcome not in ("a_wins", "b_wins", "both_valid_scoped", "both_retracted", "unresolved"):
            raise ValueError("unknown outcome")
        now = now_iso()
        inv = list(k.get("investigation") or [])
        inv.append({"at": now, "actor": principal.id, "step": "resolved", "note": note, "outcome": outcome})
        async with self.db.tx() as c:
            c.execute("UPDATE conflicts SET status='resolved', resolution=?, investigation=?, resolved_at=?, updated_at=? WHERE conflict_id=?",
                      (j({"outcome": outcome, "note": note}), j(inv), now, now, conflict_id))
            self.db.audit_sync(c, k["tenant_id"], principal.kind, principal.id, "conflict.resolve", resource_type="conflict", resource_id=conflict_id, detail={"outcome": outcome, "note": note})
            self.db.emit_sync(c, k["tenant_id"], "conflict.resolved", ref_type="conflict", ref_id=conflict_id, payload={"outcome": outcome},
                              audience={"unit_ids": [x for x in [(a or {}).get("scope_unit_id"), (b or {}).get("scope_unit_id")] if x], "visibility": "unit"})
        # claim statuses after the resolution
        pol = self.org.policies(k["tenant_id"])
        for cl, wins in ((a, outcome in ("a_wins", "both_valid_scoped")), (b, outcome in ("b_wins", "both_valid_scoped"))):
            if not cl or cl["status"] == "retracted":
                continue
            if outcome == "both_retracted" or (outcome in ("a_wins", "b_wins") and not wins):
                await self.revise_claim(principal, cl["claim_id"], status="retracted", reason=f"conflict {conflict_id}: {outcome}", event_kind="claim.retracted")
            elif outcome == "unresolved":
                # closed without a decision: neither side may count as supported, but neither stays blocked as contested
                await self.recompute_status(principal, cl["claim_id"], reason=f"conflict {conflict_id} closed unresolved; uncertainty retained", policy=pol, cap="hypothesis")
            else:
                await self.recompute_status(principal, cl["claim_id"], reason=f"conflict {conflict_id} resolved: {outcome}", policy=pol)
        return self.get_conflict(conflict_id)  # type: ignore[return-value]

    def _question_for_claim(self, claim: Mapping[str, Any]) -> dict[str, Any] | None:
        if not claim.get("question_id"):
            return None
        return row_to_dict(self.db.one("SELECT question_id, tenant_id, scope_unit_id, policy FROM questions WHERE question_id=?", (claim["question_id"],)), json_fields=("policy",))

    def effective_refs_for_claim(self, claim: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        """The claim's references with the role the commit gate's rules give them *now* (authorization, holder status,
        validity window), so a recomputation can never count evidence the gate would not."""
        refs, reasons, _known = self.gate.effective_refs(claim["tenant_id"], self.refs_for_claim(claim["claim_id"]), question=self._question_for_claim(claim),
                                                         valid_from=claim.get("valid_from"), valid_to=claim.get("valid_to"))
        return refs, reasons

    async def recompute_status(self, principal: Principal, claim_id: str, *, reason: str, policy: Mapping[str, Any] | None = None,
                               cap: str | None = None) -> dict[str, Any]:
        """Re-run the gate's support and freshness rules for an existing claim (after evidence or conflict changes).

        A claim with supporting evidence that changed at its source stays ``stale`` until re-verification replaces that
        support (:meth:`supersede_changed_support`); ``cap='hypothesis'`` keeps the result from being ``supported``."""
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        if claim["status"] == "retracted":
            return claim
        pol = policy or self.org.policies(claim["tenant_id"])
        refs, _reasons = self.effective_refs_for_claim(claim)
        supporting = [r for r in refs if r.get("role") == "supports" and is_active(r)]
        changed = [r for r in refs if r.get("role") == "supports" and not is_active(r)]
        support = compute_support(refs)
        fresh = freshness(supporting, freshness_days=float(pol.get("freshness_days", 90)))
        support["freshness"] = fresh
        open_conf = self.conflicts_for_claim(claim_id, status="open") + self.conflicts_for_claim(claim_id, status="investigating")
        if open_conf:
            status = "contested"
        elif changed:
            status = "stale"
        elif fresh["stale"]:
            status = "stale"
        elif support["independent_roots"] >= int(pol.get("min_independent_roots", 2)):
            status = "supported"
        else:
            status = "hypothesis"
        if cap == "hypothesis" and status == "supported":
            status = "hypothesis"
        if status == claim["status"] and (claim.get("support") or {}).get("independent_roots") == support["independent_roots"]:
            return claim
        return await self.revise_claim(principal, claim_id, status=status, reason=reason, support=support, freshness_at=fresh.get("freshness_at"),
                                       event_kind="claim.stale" if status == "stale" else "claim.revised")

    async def supersede_changed_support(self, principal: Principal, claim_id: str, *, reason: str) -> list[str]:
        """Re-verification found current evidence: supporting references whose source changed stop being support (role
        ``superseded``, kept for lineage), so the claim's status is computed from current evidence only."""
        rows = self.db.all("SELECT ce.ref_id FROM claim_evidence ce JOIN evidence_refs e ON e.ref_id=ce.ref_id WHERE ce.claim_id=? AND ce.role='supports' AND e.status<>'active'",
                           (claim_id,))
        ids = [r["ref_id"] for r in rows]
        if not ids:
            return []
        claim = self.get_claim(claim_id)
        async with self.db.tx() as c:
            c.execute(f"UPDATE claim_evidence SET role='superseded' WHERE claim_id=? AND role='supports' AND ref_id IN ({','.join('?' * len(ids))})", (claim_id, *ids))
            self.db.audit_sync(c, (claim or {}).get("tenant_id"), principal.kind, principal.id, "claim.support_superseded", resource_type="claim", resource_id=claim_id,
                               detail={"ref_ids": ids, "reason": reason})
        return ids

    # ================================================================== evidence change propagation
    async def on_evidence_event(self, principal: Principal, tenant_id: str, holder_id: str, event: str, affected_ref_ids: Iterable[str],
                                *, new_source_root_id: str | None = None, reason: str = "") -> dict[str, Any]:
        """A holder revised or retracted evidence. Idempotent: only references whose state actually changes propagate, so a
        redelivered event changes nothing. Claims citing a changed reference become ``stale`` (re-verification follows), or
        ``retracted`` when a retraction leaves them with no active supporting evidence; derived claims are marked stale."""
        ids = [r for r in dict.fromkeys(affected_ref_ids)]
        if not ids:
            return {"affected_claim_ids": [], "ref_ids": [], "changed_ref_ids": []}
        status = "retracted" if event == "retracted" else "revised"
        now = now_iso()
        changed: list[str] = []
        async with self.db.tx() as c:
            for rid in ids:
                r = c.execute("SELECT status, version, source_root_id, meta FROM evidence_refs WHERE ref_id=? AND tenant_id=? AND holder_id=?", (rid, tenant_id, holder_id)).fetchone()
                if r is None:
                    continue
                meta = jl(r["meta"], {}) or {}
                if r["status"] == "retracted" or (r["status"] == status and (status == "retracted" or not new_source_root_id or new_source_root_id == meta.get("revised_root_id"))):
                    continue        # already in this state: a replayed or redundant event
                # the reference keeps the root of the content it disclosed (the excerpt is the old content); the new
                # version's root is recorded for lineage and arrives as a new reference when a holder answers again
                if status == "revised":
                    meta.update({"revised_at": now, **({"revised_root_id": new_source_root_id} if new_source_root_id else {})})
                else:
                    meta.update({"retracted_at": now})
                c.execute("UPDATE evidence_refs SET status=?, version=version+1, updated_at=?, meta=? WHERE ref_id=?", (status, now, j(meta), rid))
                self._revision_sync(c, tenant_id, "evidence_ref", rid, int(r["version"]) + 1, principal, reason or event, {"status": r["status"]}, {"status": status})
                changed.append(rid)
            if changed:
                self.db.audit_sync(c, tenant_id, "holder", holder_id, f"evidence.{event}", resource_type="evidence_ref", resource_id=",".join(changed)[:200], detail={"count": len(changed)})
        if not changed:
            return {"affected_claim_ids": [], "ref_ids": ids, "changed_ref_ids": []}
        # only claims the reference still supports (context and superseded links do not make a claim stale)
        rows = self.db.all(f"SELECT DISTINCT claim_id FROM claim_evidence WHERE role='supports' AND ref_id IN ({','.join('?' * len(changed))})", changed)
        affected: list[str] = []
        for r in rows:
            claim = self.get_claim(r["claim_id"])
            if not claim or claim["status"] == "retracted":
                continue
            remaining = [x for x in self.effective_refs_for_claim(claim)[0] if x.get("role") == "supports" and is_active(x)]
            if status == "retracted" and not remaining:
                await self.revise_claim(principal, claim["claim_id"], status="retracted", reason=f"all supporting evidence retracted: {reason or 'source deleted'}",
                                        event_kind="claim.retracted")
                affected.append(claim["claim_id"])
                affected.extend(await self._propagate_status(principal, claim["claim_id"], "stale", reason=f"input claim {claim['claim_id']} retracted"))
                continue
            if claim["status"] != "stale":
                await self.revise_claim(principal, claim["claim_id"], status="stale", reason=f"evidence {event}: {reason or 'source changed'}", event_kind="claim.stale")
            affected.append(claim["claim_id"])
            affected.extend(await self._propagate_status(principal, claim["claim_id"], "stale", reason=f"input claim {claim['claim_id']} became stale"))
        return {"affected_claim_ids": list(dict.fromkeys(affected)), "ref_ids": ids, "changed_ref_ids": changed}

    async def sweep_stale(self, principal: Principal, tenant_id: str) -> list[str]:
        """Scheduled check: claims whose newest supporting evidence is older than the freshness policy become stale."""
        pol = self.org.policies(tenant_id)
        out = []
        for r in self.db.all("SELECT claim_id FROM claims WHERE tenant_id=? AND status IN ('supported','hypothesis')", (tenant_id,)):
            before = self.get_claim(r["claim_id"])
            after = await self.recompute_status(principal, r["claim_id"], reason="freshness sweep", policy=pol)
            if before and after and before["status"] != after["status"] and after["status"] == "stale":
                out.append(r["claim_id"])
        return out

    # ================================================================== discoveries
    def get_discovery(self, discovery_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM discoveries WHERE discovery_id=?", (discovery_id,)), json_fields=DISCOVERY_JSON)

    def discovery_summary(self, d: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(d)
        unit = self.org.get_unit(out["scope_unit_id"]) if out.get("scope_unit_id") else None
        out["scope_unit_name"] = unit["name"] if unit else ""
        goal = self.db.one("SELECT title FROM goals WHERE goal_id=?", (out["goal_id"],)) if out.get("goal_id") else None
        out["goal_title"] = goal["title"] if goal else ""
        claims = self.claims_by_ids(out.get("claim_ids") or [])
        out["claims"] = [self.claim_summary(x) for x in claims]
        refs = []
        for cl in claims:
            refs.extend(self.refs_for_claim(cl["claim_id"]))
        sup = compute_support(refs)
        out["support"] = {"independent_roots": sup["independent_roots"], "copied_refs": sup["copied_refs"], "unknown_independence": sup["unknown_independence"], "holders": sup["holders"]}
        out["freshness_at"] = max((cl.get("freshness_at") or "" for cl in claims), default=None) or None
        out["reviews"] = [dict(r, user_name=(self.org.get_user(r["user_id"]) or {}).get("name", "")) for r in rows_to_dicts(self.db.all("SELECT * FROM discovery_reviews WHERE discovery_id=? ORDER BY id", (out["discovery_id"],)))]
        out["status_counts"] = {s: sum(1 for x in claims if x["status"] == s) for s in ("supported", "hypothesis", "contested", "stale", "retracted")}
        return out

    async def create_discovery(self, principal: Principal, tenant_id: str, *, title: str, summary: str, kind: str, claim_ids: Iterable[str],
                               scope_unit_id: str | None, level: str | None = None, visibility: str = "unit", goal_id: str | None = None,
                               question_id: str | None = None, idempotency_key: str | None = None, followup_question_ids: Iterable[str] = (),
                               is_demo: bool | None = None) -> dict[str, Any]:
        if idempotency_key:
            r = self.db.one("SELECT d.* FROM discoveries d JOIN revisions r ON r.object_id=d.discovery_id AND r.object_type='discovery' AND r.version=1 "
                            "WHERE d.tenant_id=? AND json_extract(r.after, '$.idempotency_key')=?", (tenant_id, idempotency_key))
            if r is not None:
                return row_to_dict(r, json_fields=DISCOVERY_JSON)  # type: ignore[return-value]
        did = new_id("disc")
        now = now_iso()
        lvl = level or self.authz.unit_level(scope_unit_id)
        async with self.db.tx() as c:
            c.execute("INSERT INTO discoveries(discovery_id, tenant_id, scope_unit_id, visibility, level, kind, title, summary, claim_ids, goal_id, question_id, status, followup_question_ids, is_demo, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)",
                      (did, tenant_id, scope_unit_id, visibility, lvl, kind, title[:300], summary, j(list(claim_ids)), goal_id, question_id, j(list(followup_question_ids)),
                       int(principal.is_demo if is_demo is None else is_demo), now, now))
            self._revision_sync(c, tenant_id, "discovery", did, 1, principal, "created", {}, {"idempotency_key": idempotency_key, "title": title, "kind": kind})
            self.db.emit_sync(c, tenant_id, "discovery.created", ref_type="discovery", ref_id=did, payload={"title": title, "kind": kind, "level": lvl, "goal_id": goal_id, "scope_unit_id": scope_unit_id},
                              audience={"unit_ids": [scope_unit_id] if scope_unit_id else [], "visibility": visibility})
            self.db.audit_sync(c, tenant_id, principal.kind, principal.id, "discovery.create", resource_type="discovery", resource_id=did, detail={"title": title})
            # notify goal owner
            g = c.execute("SELECT owner_type, owner_id, title FROM goals WHERE goal_id=?", (goal_id,)).fetchone() if goal_id else None
            if g and g["owner_type"] == "user":
                self.org.notify_sync(c, tenant_id, g["owner_id"], "discovery", f"New discovery for goal “{g['title']}”", body=title, ref_type="discovery", ref_id=did)
        return self.get_discovery(did)  # type: ignore[return-value]

    async def review_discovery(self, principal: Principal, discovery_id: str, action: str, note: str = "") -> dict[str, Any]:
        d = self.get_discovery(discovery_id)
        if d is None:
            raise KeyError(discovery_id)
        self.authz.require(self.authz.can_view_scoped(principal, d, resource_type="discovery"), "discovery.review", discovery_id)
        if action not in ("reviewed", "accepted", "dismissed", "escalated", "comment"):
            raise ValueError("unknown action")
        if action in ("accepted", "dismissed", "escalated") and not principal.is_system:
            self.authz.require(self.authz.can_manage_unit_knowledge(principal, d.get("scope_unit_id") or ""), "discovery.review", discovery_id, "only unit leads or members can decide")
        now = now_iso()
        new_status = d["status"] if action == "comment" else action
        escalated_to = None
        async with self.db.tx() as c:
            if action == "escalated" and d.get("scope_unit_id"):
                unit = self.org.get_unit(d["scope_unit_id"])
                parent = unit.get("parent_id") if unit else None
                if parent:
                    punit = self.org.get_unit(parent)
                    escalated_to = parent
                    eid = new_id("disc")
                    c.execute("INSERT INTO discoveries(discovery_id, tenant_id, scope_unit_id, visibility, level, kind, title, summary, claim_ids, goal_id, question_id, status, followup_question_ids, is_demo, created_at, updated_at) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)",
                              (eid, d["tenant_id"], parent, d["visibility"], LEVEL_FOR_UNIT_TYPE.get(punit["type"], "department") if punit else "department", d["kind"],
                               f"Escalated: {d['title']}"[:300], f"Escalated from {unit['name'] if unit else 'a unit'}: {d['summary']}", j(d.get("claim_ids") or []), d.get("goal_id"),
                               d.get("question_id"), j(d.get("followup_question_ids") or []), int(d.get("is_demo") or 0), now, now))
                    self._revision_sync(c, d["tenant_id"], "discovery", eid, 1, principal, f"escalated from {discovery_id}", {}, {"source": discovery_id})
                    self.db.emit_sync(c, d["tenant_id"], "discovery.created", ref_type="discovery", ref_id=eid, payload={"title": d["title"], "kind": d["kind"], "escalated_from": discovery_id, "scope_unit_id": parent},
                                      audience={"unit_ids": [parent], "visibility": d["visibility"]})
            c.execute("UPDATE discoveries SET status=?, escalated_to=COALESCE(?, escalated_to), updated_at=? WHERE discovery_id=?", (new_status, escalated_to, now, discovery_id))
            c.execute("INSERT INTO discovery_reviews(discovery_id, user_id, action, note, at) VALUES (?, ?, ?, ?, ?)", (discovery_id, principal.id, action, note, now))
            self._revision_sync(c, d["tenant_id"], "discovery", discovery_id, int(self.db.scalar("SELECT COALESCE(MAX(version),1) FROM revisions WHERE object_type='discovery' AND object_id=?", (discovery_id,), 1)) + 1,
                                principal, action, {"status": d["status"]}, {"status": new_status, "note": note})
            self.db.emit_sync(c, d["tenant_id"], "discovery.updated", ref_type="discovery", ref_id=discovery_id, payload={"status": new_status, "action": action},
                              audience={"unit_ids": [d["scope_unit_id"]] if d.get("scope_unit_id") else [], "visibility": d["visibility"]})
            self.db.audit_sync(c, d["tenant_id"], principal.kind, principal.id, f"discovery.{action}", resource_type="discovery", resource_id=discovery_id, detail={"note": note})
        return self.get_discovery(discovery_id)  # type: ignore[return-value]

    async def add_claims_to_discovery(self, principal: Principal, discovery_id: str, claim_ids: Iterable[str], *, reason: str) -> dict[str, Any] | None:
        """Attach claims committed after the discovery was written (a late holder response), with a revision."""
        d = self.get_discovery(discovery_id)
        if d is None:
            return None
        before = list(d.get("claim_ids") or [])
        ids = list(dict.fromkeys(before + list(claim_ids)))
        if ids == before:
            return d
        version = int(self.db.scalar("SELECT COALESCE(MAX(version), 1) FROM revisions WHERE object_type='discovery' AND object_id=?", (discovery_id,), 1)) + 1
        async with self.db.tx() as c:
            c.execute("UPDATE discoveries SET claim_ids=?, updated_at=? WHERE discovery_id=?", (j(ids), now_iso(), discovery_id))
            self._revision_sync(c, d["tenant_id"], "discovery", discovery_id, version, principal, reason, {"claim_ids": before}, {"claim_ids": ids})
            self.db.emit_sync(c, d["tenant_id"], "discovery.updated", ref_type="discovery", ref_id=discovery_id, payload={"action": "claims_added", "claim_ids": ids},
                              audience={"unit_ids": [d["scope_unit_id"]] if d.get("scope_unit_id") else [], "visibility": d["visibility"]})
        return self.get_discovery(discovery_id)

    async def attach_followups(self, discovery_id: str, question_ids: Iterable[str]) -> None:
        d = self.get_discovery(discovery_id)
        if d is None:
            return
        ids = list(dict.fromkeys(list(d.get("followup_question_ids") or []) + list(question_ids)))
        async with self.db.tx() as c:
            c.execute("UPDATE discoveries SET followup_question_ids=?, updated_at=? WHERE discovery_id=?", (j(ids), now_iso(), discovery_id))

    def list_discoveries(self, principal: Principal, *, level: str | None = None, scope_unit_id: str | None = None, status: str | None = None,
                         goal_id: str | None = None, kind: str | None = None, limit: int = 100, include_descendants: bool = True) -> list[dict[str, Any]]:
        vis, args = self.authz.visibility_sql(principal, alias="d", resource_type="discovery", owner_col="discovery_id")
        # discoveries have no owner column; the owner_col trick compares discovery_id to the principal id (never equal) — harmless
        sql = f"SELECT d.* FROM discoveries d WHERE {vis}"
        if level:
            sql += " AND d.level=?"; args.append(level)
        if scope_unit_id:
            ids = self.org.descendants(scope_unit_id) if include_descendants else [scope_unit_id]
            sql += f" AND d.scope_unit_id IN ({','.join('?' * len(ids))})"; args.extend(ids)
        if status:
            sql += " AND d.status=?"; args.append(status)
        if goal_id:
            sql += " AND d.goal_id=?"; args.append(goal_id)
        if kind:
            sql += " AND d.kind=?"; args.append(kind)
        sql += " ORDER BY d.created_at DESC LIMIT ?"; args.append(int(limit))
        return [self.discovery_summary(d) for d in rows_to_dicts(self.db.all(sql, args), json_fields=DISCOVERY_JSON)]

    def discovery_detail(self, principal: Principal, discovery_id: str) -> dict[str, Any]:
        d = self.get_discovery(discovery_id)
        if d is None:
            raise KeyError(discovery_id)
        self.authz.require(self.authz.can_view_scoped(principal, d, resource_type="discovery"), "discovery.view", discovery_id)
        summary = self.discovery_summary(d)
        claims = self.claims_by_ids(d.get("claim_ids") or [])
        refs: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        seen_ref, seen_conf = set(), set()
        for cl in claims:
            for r in self.refs_for_claim(cl["claim_id"]):
                if r["ref_id"] not in seen_ref:
                    seen_ref.add(r["ref_id"]); refs.append(r)
            for k in self.conflicts_for_claim(cl["claim_id"]):
                if k["conflict_id"] not in seen_conf:
                    seen_conf.add(k["conflict_id"]); conflicts.append(k)
        followups = rows_to_dicts(self.db.all(f"SELECT question_id, text, status, kind, priority, created_at FROM questions WHERE question_id IN ({','.join('?' * len(d.get('followup_question_ids') or ['']))})",
                                              d.get("followup_question_ids") or [""]))
        return {"discovery": summary, "claims": [self.claim_summary(x) for x in claims], "evidence": [self.ref_view(r) for r in refs],
                "conflicts": [self.conflict_view(k, principal) for k in conflicts], "lineage": self.lineage_graph(principal, claim_ids=[x["claim_id"] for x in claims], discovery=d),
                "followups": followups, "revisions": self.revisions_for("discovery", discovery_id)}

    # ================================================================== lineage graph
    def lineage_graph(self, principal: Principal, *, claim_ids: Iterable[str] = (), discovery: Mapping[str, Any] | None = None,
                      goal_id: str | None = None, question_id: str | None = None, max_nodes: int = 400) -> dict[str, Any]:
        """Nodes and edges connecting discoveries, claims, questions, responses' holders and evidence references."""
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        def add(nid: str, ntype: str, label: str, **meta: Any) -> None:
            if nid not in nodes and len(nodes) < max_nodes:
                nodes[nid] = {"id": nid, "type": ntype, "label": label[:120], **meta}

        ids = list(dict.fromkeys(claim_ids))
        if goal_id:
            ids += [r["claim_id"] for r in self.db.all("SELECT claim_id FROM claims WHERE goal_id=? AND status<>'retracted'", (goal_id,))]
        if question_id:
            ids += [r["claim_id"] for r in self.db.all("SELECT claim_id FROM claims WHERE question_id=?", (question_id,))]
        claims = [c for c in self.claims_by_ids(ids) if self.authz.can_view_scoped(principal, c, resource_type="claim")]
        if discovery:
            add(discovery["discovery_id"], "discovery", discovery["title"], level=discovery.get("level"), status=discovery.get("status"))
        for cl in claims:
            add(cl["claim_id"], "claim", cl["text"], status=cl["status"], confidence=cl["confidence"], unit_id=cl.get("scope_unit_id"))
            if discovery:
                edges.append({"source": discovery["discovery_id"], "target": cl["claim_id"], "kind": "supports"})
            if cl.get("question_id"):
                q = self.db.one("SELECT question_id, text, status FROM questions WHERE question_id=?", (cl["question_id"],))
                if q:
                    add(q["question_id"], "question", q["text"], status=q["status"])
                    edges.append({"source": q["question_id"], "target": cl["claim_id"], "kind": "derived"})
            for r in self.refs_for_claim(cl["claim_id"]):
                add(r["ref_id"], "evidence", r.get("title") or r["ref_id"], holder_id=r["holder_id"], root=r.get("source_root_id"), status=r.get("status"), root_known=bool(r.get("root_known")))
                edges.append({"source": r["ref_id"], "target": cl["claim_id"], "kind": "supports" if r.get("role") == "supports" else ("conflicts" if r.get("role") == "contradicts" else "context")})
                holder = self.org.get_holder(r["holder_id"])
                if holder:
                    add(holder["holder_id"], "holder", holder["name"], owner_type=holder["owner_type"], owner_id=holder["owner_id"])
                    edges.append({"source": holder["holder_id"], "target": r["ref_id"], "kind": "owns"})
            for der in self.db.all("SELECT input_claim_ids FROM derivations WHERE claim_id=?", (cl["claim_id"],)):
                for inp in jl(der["input_claim_ids"], []):
                    if inp in nodes:
                        edges.append({"source": inp, "target": cl["claim_id"], "kind": "derived"})
            for k in self.conflicts_for_claim(cl["claim_id"]):
                other = k["claim_b_id"] if k["claim_a_id"] == cl["claim_id"] else k["claim_a_id"]
                if other in nodes:
                    edges.append({"source": cl["claim_id"], "target": other, "kind": "conflicts", "status": k["status"]})
        seen = set()
        uniq = []
        for e in edges:
            key = (e["source"], e["target"], e["kind"])
            if key not in seen and e["source"] in nodes and e["target"] in nodes:
                seen.add(key); uniq.append(e)
        return {"nodes": list(nodes.values()), "edges": uniq}

    # ================================================================== revisions
    def _revision_sync(self, c: sqlite3.Connection, tenant_id: str, object_type: str, object_id: str, version: int, principal: Principal,
                       reason: str, before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
        c.execute("INSERT INTO revisions(revision_id, tenant_id, object_type, object_id, version, actor_type, actor_id, reason, before, after, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (new_id("rev"), tenant_id, object_type, object_id, int(version), principal.kind, principal.id, reason[:500], j(before), j(after), now_iso()))

    def revisions_for(self, object_type: str, object_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return rows_to_dicts(self.db.all("SELECT * FROM revisions WHERE object_type=? AND object_id=? ORDER BY version, at LIMIT ?", (object_type, object_id, int(limit))), json_fields=REVISION_JSON)

    # ================================================================== metrics helpers
    def counts(self, tenant_id: str) -> dict[str, Any]:
        by_status = {r["status"]: r["n"] for r in self.db.all("SELECT status, COUNT(*) AS n FROM claims WHERE tenant_id=? GROUP BY status", (tenant_id,))}
        refs = {r["status"]: r["n"] for r in self.db.all("SELECT status, COUNT(*) AS n FROM evidence_refs WHERE tenant_id=? GROUP BY status", (tenant_id,))}
        conflicts = {r["status"]: r["n"] for r in self.db.all("SELECT status, COUNT(*) AS n FROM conflicts WHERE tenant_id=? GROUP BY status", (tenant_id,))}
        disc = {r["status"]: r["n"] for r in self.db.all("SELECT status, COUNT(*) AS n FROM discoveries WHERE tenant_id=? GROUP BY status", (tenant_id,))}
        return {"claims": by_status, "evidence_refs": refs, "conflicts": conflicts, "discoveries": disc}

    def evidence_freshness(self, tenant_id: str) -> dict[str, Any]:
        pol = self.org.policies(tenant_id)
        rows = rows_to_dicts(self.db.all("SELECT freshness_at, observed_at FROM evidence_refs WHERE tenant_id=? AND status='active'", (tenant_id,)))
        f = [freshness([r], freshness_days=float(pol.get("freshness_days", 90))) for r in rows]
        ages = sorted(x["age_days"] for x in f if x["age_days"] is not None)
        return {"total": len(rows), "stale": sum(1 for x in f if x["stale"]), "fresh": sum(1 for x in f if not x["stale"] and not x["unknown"]),
                "unknown": sum(1 for x in f if x["unknown"]), "median_age_days": ages[len(ages) // 2] if ages else None}

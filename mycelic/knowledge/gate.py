"""The commit gate (docs/mycelic/DECISIONS.md D12).

Nothing becomes organizational knowledge without passing five checks, each of which produces a readable
reason that is stored with the claim's first revision and in the audit log:

1. **authorization** — the committing principal may write into the claim's scope, and every supporting
   holder was a legitimate route for the question (re-checked here, not trusted from the response).
2. **schema** — required fields, enumerations, bounds.
3. **provenance** — at least one supporting evidence reference, or a derivation from existing claims, or an
   explicit human assertion (which can only ever be a hypothesis).
4. **temporal validity** — validity window well-formed and current; evidence observed inside the window.
5. **support** — independent roots vs. tenant policy; freshness vs. policy; open disagreement.

The result carries the status the claim gets: ``supported``, ``hypothesis``, ``contested`` or ``stale``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..authz import Authorizer, Principal
from ..util import now_iso, parse_iso
from .support import compute_support, freshness

CLAIM_KINDS = ("finding", "hypothesis", "prediction", "relationship", "measurement")
VISIBILITIES = ("private", "unit", "org")


@dataclass
class GateResult:
    ok: bool
    status: str                       # supported | hypothesis | contested | stale | rejected
    reasons: list[str] = field(default_factory=list)
    support: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "status": self.status, "reasons": list(self.reasons), "support": self.support,
                "freshness": self.freshness, "checks": self.checks}


class CommitGate:
    def __init__(self, authz: Authorizer) -> None:
        self.authz = authz

    def check(self, principal: Principal, candidate: Mapping[str, Any], *, evidence: Iterable[Mapping[str, Any]],
              policy: Mapping[str, Any], has_open_conflict: bool = False, question: Mapping[str, Any] | None = None,
              input_claims: Iterable[Mapping[str, Any]] = ()) -> GateResult:
        reasons: list[str] = []
        checks: dict[str, bool] = {}
        refs = [dict(r) for r in evidence]
        tenant_id = candidate.get("tenant_id")

        # 1. authorization
        auth_ok = principal.tenant_id == tenant_id
        if not auth_ok:
            reasons.append("tenant mismatch")
        scope = candidate.get("scope_unit_id")
        if auth_ok and not principal.is_system:
            if candidate.get("visibility") == "private":
                auth_ok = candidate.get("owner_user_id") == principal.id
                if not auth_ok:
                    reasons.append("private claims can only be created by their owner")
            elif scope:
                allowed = self.authz.visible_unit_ids(principal) | self.authz.led_unit_ids(principal)
                if scope not in allowed:
                    auth_ok = False
                    reasons.append("principal cannot write into the claim's scope")
            elif candidate.get("visibility") == "org" and not (principal.is_admin or "executive" in principal.roles):
                auth_ok = False
                reasons.append("org-wide claims need an executive or organization role")
        if auth_ok and question is not None:
            q = {"tenant_id": tenant_id, "scope_unit_id": question.get("scope_unit_id") or scope, "policy": question.get("policy") or {},
                 "candidate_domains": []}
            for r in refs:
                holder = self.authz.org.get_holder(r["holder_id"])
                if holder is None or holder.get("tenant_id") != tenant_id:
                    auth_ok = False
                    reasons.append(f"evidence {r['ref_id']} comes from an unknown holder")
                    continue
                ok, why = self.authz.can_route(q, holder)
                if not ok and holder.get("status") != "revoked":
                    # a route that was valid when routed may have become invalid (membership change): the evidence
                    # is still real, but it no longer counts as support inside this scope
                    r["role"] = "context"
                    reasons.append(f"evidence {r['ref_id']} no longer authorized for this scope ({why}); treated as context")
                elif holder.get("status") == "revoked":
                    r["status"] = "unavailable"
                    reasons.append(f"evidence {r['ref_id']} comes from a revoked holder; not counted")
        checks["authorization"] = auth_ok

        # 2. schema
        schema_ok = True
        text = (candidate.get("text") or "").strip()
        if not text or len(text) > 4000:
            schema_ok = False
            reasons.append("claim text must be 1..4000 characters")
        if candidate.get("kind", "finding") not in CLAIM_KINDS:
            schema_ok = False
            reasons.append(f"unknown claim kind {candidate.get('kind')!r}")
        if candidate.get("visibility", "unit") not in VISIBILITIES:
            schema_ok = False
            reasons.append(f"unknown visibility {candidate.get('visibility')!r}")
        conf = candidate.get("confidence", 0.5)
        if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
            schema_ok = False
            reasons.append("confidence must be within 0..1")
        if candidate.get("visibility") == "private" and not candidate.get("owner_user_id"):
            schema_ok = False
            reasons.append("private claims need an owner")
        checks["schema"] = schema_ok

        # 3. provenance
        supporting = [r for r in refs if (r.get("role") or "supports") == "supports" and r.get("status") not in ("retracted", "unavailable")]
        derived = list(input_claims)
        human = candidate.get("created_by_type") == "user"
        prov_ok = bool(supporting) or bool(derived) or human
        if not prov_ok:
            reasons.append("no supporting evidence, derivation or human assertion")
        checks["provenance"] = prov_ok

        # 4. temporal validity
        temporal_ok = True
        vf, vt = parse_iso(candidate.get("valid_from")), parse_iso(candidate.get("valid_to"))
        now = parse_iso(now_iso())
        if vf and vt and vf > vt:
            temporal_ok = False
            reasons.append("valid_from is after valid_to")
        if vt and now and vt < now:
            temporal_ok = False
            reasons.append("validity window already ended")
        if vf and now and vf > now:
            temporal_ok = False
            reasons.append("validity window has not started")
        if vf or vt:
            for r in supporting:
                obs = parse_iso(r.get("observed_at"))
                if obs and ((vf and obs < vf) or (vt and obs > vt)):
                    reasons.append(f"evidence {r['ref_id']} observed outside the validity window; treated as context")
                    r["role"] = "context"
            supporting = [r for r in refs if (r.get("role") or "supports") == "supports" and r.get("status") not in ("retracted", "unavailable")]
        checks["temporal"] = temporal_ok

        # 5. support
        support = compute_support(refs)
        fresh = freshness(supporting, freshness_days=float(policy.get("freshness_days", 90)))
        min_roots = int(policy.get("min_independent_roots", 2))
        if not (auth_ok and schema_ok and prov_ok and temporal_ok):
            return GateResult(False, "rejected", reasons, support, fresh, checks)
        if has_open_conflict or support["contradicting_refs"]:
            status = "contested"
            reasons.append("open disagreement references this claim")
        elif human and not supporting and not derived:
            status = "hypothesis"
            reasons.append("human assertion without evidence")
        elif fresh["stale"]:
            status = "stale"
            reasons.append(f"newest evidence is {fresh['age_days']} days old (policy: {policy.get('freshness_days', 90)})")
        elif support["independent_roots"] >= min_roots:
            status = "supported"
            reasons.append(f"{support['independent_roots']} independent source roots (policy needs {min_roots})")
            if support["copied_refs"]:
                reasons.append(f"{support['copied_refs']} copied reference(s) not counted as independent")
            if support["unknown_independence"]:
                reasons.append(f"{support['unknown_independence']} reference(s) of unknown independence not counted")
        else:
            status = "hypothesis"
            reasons.append(f"{support['independent_roots']} independent source root(s); policy needs {min_roots}")
            if support["copied_refs"]:
                reasons.append(f"{support['copied_refs']} copied reference(s) do not add independent support")
            if support["unknown_independence"]:
                reasons.append(f"{support['unknown_independence']} reference(s) of unknown independence")
        checks["support"] = status == "supported"
        return GateResult(True, status, reasons, support, fresh, checks)

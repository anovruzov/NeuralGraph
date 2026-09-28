"""QuestionArtifacts: bounded inquiry (creation, prioritization, deduplication, routing, response intake).

A question is the only thing the coordinator ever sends to an evidence holder. It carries its identifier,
asker, text, trigger, goal, scope, temporal validity, uncertainty, motivating lineage, candidate evidence
domains, policy, budget and status. Routing re-checks authorization per holder; a response is accepted only
from a holder that was routed, and its authorization is checked again at use time.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from ..authz import Authorizer, Forbidden, Principal
from ..db.coord import CoordDB, row_to_dict, rows_to_dicts
from ..goals import GoalService
from ..jobs import JobQueue
from ..knowledge import KnowledgeService
from ..org import OrgService
from ..transport import Envelope, Subjects, Transport
from ..util import j, jl, new_id, now_iso, plus_seconds, sha256

logger = logging.getLogger(__name__)

Q_JSON = ("trigger", "uncertainty", "motivating_lineage", "candidate_domains", "policy", "budget", "budget_spent", "priority_breakdown", "result")
R_JSON = ("evidence_ref_ids", "provenance")
LIVE_STATUSES = ("draft", "routed", "collecting", "evaluating", "verifying")
QUESTION_KINDS = ("gap", "verification", "contradiction", "relationship", "hypothesis", "prediction")


class DuplicateQuestion(ValueError):
    def __init__(self, existing_id: str) -> None:
        super().__init__(f"a live question with the same text already exists: {existing_id}")
        self.existing_id = existing_id


class CooldownActive(ValueError):
    def __init__(self, until: str, existing_id: str) -> None:
        super().__init__(f"the same question was resolved recently; cooldown until {until}")
        self.until = until
        self.existing_id = existing_id


def _norm(text: str) -> str:
    return " ".join(text.lower().split()).rstrip("?.! ")


def prioritize(goal: Mapping[str, Any] | None, estimates: Mapping[str, Any], weights: Mapping[str, float], *, holders: int = 1) -> tuple[float, dict[str, Any]]:
    """Heuristic priority. Every input is an estimate (0..1) and the output says so."""
    goal_value = (6 - int((goal or {}).get("priority") or 3)) / 5.0
    uncertainty = float(estimates.get("uncertainty", 0.5))
    impact = float(estimates.get("impact", 0.5))
    missing = float(estimates.get("missing_evidence", 0.5))
    gain = float(estimates.get("information_gain", 0.5))
    cost = min(1.0, 0.1 + 0.1 * max(0, holders))            # more holders -> more model calls
    w = {k: float(weights.get(k, 1.0)) for k in ("goal_value", "uncertainty", "impact", "missing_evidence", "information_gain", "cost")}
    score = w["goal_value"] * goal_value + w["uncertainty"] * uncertainty + w["impact"] * impact + w["missing_evidence"] * missing + w["information_gain"] * gain - w["cost"] * cost
    breakdown = {"method": "heuristic", "goal_value": round(goal_value, 3), "uncertainty": uncertainty, "impact": impact, "missing_evidence": missing,
                 "information_gain": gain, "cost": round(cost, 3), "weights": w, "score": round(score, 4),
                 "note": "estimates, not measurements: configured weights times heuristic inputs"}
    return round(score, 4), breakdown


class QuestionService:
    def __init__(self, db: CoordDB, org: OrgService, authz: Authorizer, jobs: JobQueue, goals: GoalService, knowledge: KnowledgeService,
                 transport: Transport | None = None, *, question_timeout_seconds: float = 120.0, cooldown_seconds: float = 900.0,
                 max_followup_depth: int = 3) -> None:
        self.db = db
        self.org = org
        self.authz = authz
        self.jobs = jobs
        self.goals = goals
        self.knowledge = knowledge
        self.transport = transport
        self.question_timeout_seconds = question_timeout_seconds
        self.cooldown_seconds = cooldown_seconds
        self.max_followup_depth = max_followup_depth

    # ------------------------------------------------------------------ reads
    def get(self, question_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.one("SELECT * FROM questions WHERE question_id=?", (question_id,)), json_fields=Q_JSON)

    def routes(self, question_id: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT r.*, h.name AS holder_name, h.owner_type AS holder_owner_type, h.owner_id AS holder_owner_id FROM question_routes r JOIN holders h ON h.holder_id=r.holder_id WHERE r.question_id=? ORDER BY r.sent_at", (question_id,))
        return rows_to_dicts(rows)

    def responses(self, question_id: str) -> list[dict[str, Any]]:
        return rows_to_dicts(self.db.all("SELECT * FROM responses WHERE question_id=? ORDER BY received_at", (question_id,)), json_fields=R_JSON)

    def view(self, q: Mapping[str, Any], *, principal: Principal | None = None) -> dict[str, Any]:
        d = dict(q)
        asker_name = ""
        if d["asker_type"] == "user":
            u = self.org.get_user(d["asker_id"]); asker_name = u["name"] if u else ""
        d["asker"] = {"type": d.pop("asker_type"), "id": d.pop("asker_id"), "name": asker_name or d["asker_type"] if "asker_type" in d else asker_name}
        goal = self.db.one("SELECT title FROM goals WHERE goal_id=?", (d["goal_id"],)) if d.get("goal_id") else None
        d["goal_title"] = goal["title"] if goal else ""
        unit = self.org.get_unit(d["scope_unit_id"]) if d.get("scope_unit_id") else None
        d["scope_unit_name"] = unit["name"] if unit else ""
        d["routes"] = [{"route_id": r["route_id"], "holder_id": r["holder_id"], "holder_name": r["holder_name"], "status": r["status"], "sent_at": r["sent_at"],
                        "responded_at": r["responded_at"], "deadline_at": r["deadline_at"]} for r in self.routes(d["question_id"])]
        d["needs_my_input"] = False
        if principal is not None and principal.is_user:
            mine = set(principal.holder_ids)
            d["needs_my_input"] = any(r["holder_id"] in mine and r["status"] in ("pending", "delivered") for r in d["routes"])
        lineage = []
        for item in d.get("motivating_lineage") or []:
            label = ""
            if item.get("type") == "claim":
                c = self.knowledge.get_claim(item["id"]); label = c["text"][:120] if c else ""
            elif item.get("type") == "question":
                qq = self.get(item["id"]); label = qq["text"][:120] if qq else ""
            elif item.get("type") == "conflict":
                k = self.knowledge.get_conflict(item["id"]); label = k["summary"][:120] if k else ""
            elif item.get("type") == "evidence_ref":
                r = self.knowledge.get_ref(item["id"]); label = (r or {}).get("title", "")
            lineage.append({**item, "label": label})
        d["motivating_lineage"] = lineage
        return d

    def response_view(self, r: Mapping[str, Any]) -> dict[str, Any]:
        d = dict(r)
        holder = self.org.get_holder(d["holder_id"])
        d["holder_name"] = holder["name"] if holder else ""
        d["evidence"] = [self.knowledge.ref_view(x) for x in self.knowledge.refs_by_ids(d.get("evidence_ref_ids") or [])]
        return d

    def list(self, principal: Principal, *, goal_id: str | None = None, status: str | None = None, scope_unit_id: str | None = None,
             needs_input: bool = False, live_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        vis, args = self.authz.visibility_sql(principal, alias="q", resource_type="question", owner_col="asker_id")
        sql = f"SELECT q.* FROM questions q WHERE {vis}"
        # a question 'policy.visibility' lives in JSON; questions are unit-visible by scope, org-visible when policy says so
        sql = sql.replace("q.visibility = 'org'", "json_extract(q.policy, '$.visibility') = 'org'").replace("q.visibility = 'unit'", "COALESCE(json_extract(q.policy, '$.visibility'), 'unit') = 'unit'")
        if needs_input and principal.is_user:
            mine = principal.holder_ids or [""]
            sql = f"SELECT q.* FROM questions q WHERE q.tenant_id=? AND q.question_id IN (SELECT question_id FROM question_routes WHERE holder_id IN ({','.join('?' * len(mine))}) AND status IN ('pending','delivered'))"
            args = [principal.tenant_id, *mine]
        if goal_id:
            sql += " AND q.goal_id=?"; args.append(goal_id)
        if status:
            sql += " AND q.status=?"; args.append(status)
        elif live_only:
            sql += f" AND q.status IN ({','.join('?' * len(LIVE_STATUSES))})"; args.extend(LIVE_STATUSES)
        if scope_unit_id:
            ids = self.org.descendants(scope_unit_id)
            sql += f" AND q.scope_unit_id IN ({','.join('?' * len(ids))})"; args.extend(ids)
        sql += " ORDER BY q.priority DESC, q.created_at DESC LIMIT ?"; args.append(int(limit))
        return [self.view(q, principal=principal) for q in rows_to_dicts(self.db.all(sql, args), json_fields=Q_JSON)]

    def detail(self, principal: Principal, question_id: str) -> dict[str, Any]:
        q = self.get(question_id)
        if q is None:
            raise KeyError(question_id)
        self.authz.require(self.can_view(principal, q), "question.view", question_id)
        resp = [self.response_view(r) for r in self.responses(question_id)]
        claims = [self.knowledge.claim_summary(c) for c in self.knowledge.claims_by_ids([r["claim_id"] for r in self.db.all("SELECT claim_id FROM claims WHERE question_id=?", (question_id,))])]
        discs = [self.knowledge.discovery_summary(d) for d in rows_to_dicts(self.db.all("SELECT * FROM discoveries WHERE question_id=?", (question_id,)), json_fields=("claim_ids", "followup_question_ids"))]
        children = rows_to_dicts(self.db.all("SELECT question_id, text, kind, status, depth, created_at FROM questions WHERE parent_question_id=? ORDER BY created_at", (question_id,)))
        return {"question": self.view(q, principal=principal), "responses": resp, "claims": claims, "discoveries": discs, "followups": children,
                "lineage": self.knowledge.lineage_graph(principal, question_id=question_id), "run": row_to_dict(self.db.one("SELECT * FROM question_runs WHERE question_id=?", (question_id,)), json_fields=("state",))}

    def can_view(self, principal: Principal, q: Mapping[str, Any]) -> bool:
        row = dict(q)
        row["visibility"] = (q.get("policy") or {}).get("visibility", "unit")
        if self.authz.can_view_scoped(principal, row, resource_type="question"):
            return True
        if principal.is_user and any(r["holder_id"] in set(principal.holder_ids) for r in self.routes(q["question_id"])):
            return True
        if q.get("goal_id"):
            g = self.goals.get_goal(q["goal_id"])
            return bool(g) and self.goals.can_view(principal, g)
        return False

    # ------------------------------------------------------------------ create
    async def create(self, principal: Principal, data: Mapping[str, Any], *, asker_type: str | None = None, asker_id: str | None = None,
                     estimates: Mapping[str, Any] | None = None, holders_estimate: int = 1, enqueue_route: bool = True, force: bool = False,
                     is_demo: bool | None = None) -> dict[str, Any]:
        tenant_id = principal.tenant_id
        text = " ".join((data.get("text") or "").split())
        if len(text) < 8:
            raise ValueError("question text is too short")
        goal = self.goals.get_goal(data["goal_id"]) if data.get("goal_id") else None
        if data.get("goal_id") and goal is None:
            raise ValueError("goal not found")
        if goal and goal["tenant_id"] != tenant_id:
            raise Forbidden("question.create", data["goal_id"], "goal belongs to another tenant")
        scope = data.get("scope_unit_id") or (goal.get("scope_unit_id") if goal else None) or (goal.get("owner_id") if goal and goal.get("owner_type") == "unit" else None)
        if scope is None and principal.is_user:
            mems = principal.memberships
            scope = mems[0]["unit_id"] if mems else None
        if not principal.is_system and principal.kind != "loop":
            if goal is not None:
                self.authz.require(self.goals.can_view(principal, goal), "question.create", data.get("goal_id", ""), "cannot see that goal")
            if scope and scope not in (self.authz.visible_unit_ids(principal) | self.authz.led_unit_ids(principal)):
                raise Forbidden("question.create", scope, "cannot ask inside that scope")
        kind = data.get("kind") or "gap"
        if kind not in QUESTION_KINDS:
            raise ValueError("unknown question kind")
        parent_id = data.get("parent_question_id")
        depth = 0
        if parent_id:
            parent = self.get(parent_id)
            if parent is None:
                raise ValueError("parent question not found")
            depth = int(parent["depth"]) + 1
            max_depth = int(((goal or {}).get("budget") or {}).get("followup_depth") or self.max_followup_depth)
            if depth > max_depth:
                raise ValueError(f"follow-up depth {depth} exceeds the limit {max_depth}")
        dedupe_key = sha256(tenant_id, data.get("goal_id") or "", scope or "", kind, _norm(text))
        live = self.db.one(f"SELECT question_id, status, cooldown_until, resolved_at FROM questions WHERE tenant_id=? AND dedupe_key=?", (tenant_id, dedupe_key))
        if live is not None and not force:
            if live["status"] in LIVE_STATUSES:
                raise DuplicateQuestion(live["question_id"])
            if live["cooldown_until"] and live["cooldown_until"] > now_iso():
                raise CooldownActive(live["cooldown_until"], live["question_id"])
        if goal is not None:
            bs = self.goals.budget_status(goal["goal_id"])
            if bs["exhausted"] and not force:
                raise ValueError(f"goal budget exhausted on {', '.join(bs['exhausted_on'])}")
        weights = self.org.policy(tenant_id, "priority_weights", {}) or {}
        score, breakdown = prioritize(goal, estimates or data.get("estimates") or {}, weights, holders=holders_estimate)
        default_pol = {"visibility": "unit", "disclosure": "excerpt", "blind_verification": kind == "verification",
                       "min_independent_roots": self.org.policy(tenant_id, "min_independent_roots", 2)}
        policy = {**default_pol, **(data.get("policy") or {})}
        budget = {"timeout_seconds": self.question_timeout_seconds, "holders": 10, **(data.get("budget") or {})}
        qid = new_id("q")
        now = now_iso()
        # a duplicate-key row from a resolved question must not block the unique index: release it
        async with self.db.tx() as c:
            if live is not None:
                c.execute("UPDATE questions SET dedupe_key=NULL WHERE question_id=?", (live["question_id"],))
            c.execute(
                """INSERT INTO questions(question_id, tenant_id, asker_type, asker_id, goal_id, text, kind, trigger, scope_unit_id, valid_from, valid_to, uncertainty,
                                         motivating_lineage, candidate_domains, policy, budget, budget_spent, status, priority, priority_breakdown, parent_question_id, depth,
                                         dedupe_key, is_demo, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (qid, tenant_id, asker_type or principal.kind, asker_id or principal.id, data.get("goal_id"), text, kind, j(data.get("trigger") or {"kind": "user_request" if principal.is_user else "loop"}),
                 scope, data.get("valid_from"), data.get("valid_to"), j(data.get("uncertainty") or {"prior": (estimates or {}).get("uncertainty", 0.5)}),
                 j(list(data.get("motivating_lineage") or [])), j(list(data.get("candidate_domains") or [])), j(policy), j(budget), j({"tokens": 0, "usd": 0.0}),
                 score, j(breakdown), parent_id, depth, dedupe_key, int(principal.is_demo if is_demo is None else is_demo), now, now),
            )
            self.db.audit_sync(c, tenant_id, principal.kind, principal.id, "question.create", resource_type="question", resource_id=qid, detail={"text": text[:200], "goal_id": data.get("goal_id"), "kind": kind})
            self.db.emit_sync(c, tenant_id, "question.created", ref_type="question", ref_id=qid, payload={"text": text, "goal_id": data.get("goal_id"), "kind": kind, "scope_unit_id": scope},
                              audience={"unit_ids": [scope] if scope else [], "visibility": policy["visibility"], "user_ids": [principal.id] if principal.is_user else []})
            if enqueue_route:
                self.jobs.enqueue_sync(c, "question.route", idempotency_key=f"question.route:{qid}", tenant_id=tenant_id, ref_type="question", ref_id=qid, priority=3, max_attempts=5)
        return self.get(qid)  # type: ignore[return-value]

    # ------------------------------------------------------------------ routing
    def candidate_holders(self, q: Mapping[str, Any], *, exclude: Iterable[str] = (), asker: Principal | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(routable holders, rejected [{holder_id, reason}]) after the authorization re-check."""
        ex = set(exclude)
        ok, rejected = [], []
        for h in self.org.list_holders(q["tenant_id"]):
            if h["holder_id"] in ex:
                rejected.append({"holder_id": h["holder_id"], "reason": "excluded (already supports the claim)"}); continue
            allowed, why = self.authz.can_route(q, h, asker=asker)
            (ok if allowed else rejected).append(h if allowed else {"holder_id": h["holder_id"], "reason": why})
        return ok, rejected

    async def route(self, question_id: str, *, principal: Principal | None = None, exclude_holder_ids: Iterable[str] = ()) -> dict[str, Any]:
        q = self.get(question_id)
        if q is None:
            raise KeyError(question_id)
        if q["status"] not in ("draft", "routed"):
            return {"routed": 0, "skipped": "question is not routable", "status": q["status"]}
        asker = principal
        if asker is None and q["asker_type"] == "user":
            asker = self.authz.principal_for_user(q["asker_id"])
        holders, rejected = self.candidate_holders(q, exclude=exclude_holder_ids, asker=asker if asker and asker.is_user else None)
        max_holders = int((q.get("budget") or {}).get("holders") or 10)
        holders = holders[:max_holders]
        now = now_iso()
        timeout = float((q.get("budget") or {}).get("timeout_seconds") or self.question_timeout_seconds)
        deadline = plus_seconds(timeout)
        if not holders:
            async with self.db.tx() as c:
                c.execute("UPDATE questions SET status='failed', result=?, updated_at=?, resolved_at=? WHERE question_id=?",
                          (j({"outcome": "no_authorized_holders", "rejected": rejected}), now, now, question_id))
                self.db.audit_sync(c, q["tenant_id"], "worker", "router", "question.route", resource_type="question", resource_id=question_id, outcome="deny", detail={"rejected": rejected})
                self.db.emit_sync(c, q["tenant_id"], "question.resolved", ref_type="question", ref_id=question_id, payload={"status": "failed", "reason": "no authorized holders"},
                                  audience={"unit_ids": [q["scope_unit_id"]] if q.get("scope_unit_id") else []})
            return {"routed": 0, "rejected": rejected, "status": "failed"}
        payload_base = {"question_id": question_id, "text": q["text"], "kind": q["kind"], "goal_id": q.get("goal_id"), "scope_unit_id": q.get("scope_unit_id"),
                        "candidate_domains": q.get("candidate_domains") or [], "valid_from": q.get("valid_from"), "valid_to": q.get("valid_to"),
                        "policy": q.get("policy") or {}, "budget": {"max_refs": 8}, "asked_at": now, "deadline_at": deadline}
        routed = 0
        async with self.db.tx() as c:
            for h in holders:
                rid = new_id("route")
                cur = c.execute("INSERT OR IGNORE INTO question_routes(route_id, tenant_id, question_id, holder_id, status, msg_id, attempts, sent_at, deadline_at) VALUES (?, ?, ?, ?, 'pending', ?, 1, ?, ?)",
                                (rid, q["tenant_id"], question_id, h["holder_id"], f"q:{question_id}:{h['holder_id']}", now, deadline))
                if cur.rowcount:
                    routed += 1
            c.execute("UPDATE questions SET status='collecting', updated_at=? WHERE question_id=?", (now, question_id))
            self.db.audit_sync(c, q["tenant_id"], "worker", "router", "question.route", resource_type="question", resource_id=question_id, detail={"holders": [h["holder_id"] for h in holders], "rejected": rejected})
            self.db.emit_sync(c, q["tenant_id"], "question.routed", ref_type="question", ref_id=question_id, payload={"holders": len(holders)}, audience={"unit_ids": [q["scope_unit_id"]] if q.get("scope_unit_id") else []})
            self.jobs.enqueue_sync(c, "question.collect", idempotency_key=f"question.collect:{question_id}", tenant_id=q["tenant_id"], ref_type="question", ref_id=question_id,
                                   priority=4, delay_seconds=timeout, max_attempts=5)
            for h in holders:
                self.org.notify_sync(c, q["tenant_id"], h["owner_id"], "question", "A question was routed to your evidence", body=q["text"][:200], ref_type="question", ref_id=question_id) if h["owner_type"] == "user" else None
        # publish after the DB commit; delivery failures are recorded per route and retried by the job's next attempt
        failed = 0
        for h in holders:
            row = self.org.holder_secret_row(h["holder_id"])
            env = Envelope.new(Subjects.holder_inbox(q["tenant_id"], h["holder_id"]), "question", q["tenant_id"],
                               {**payload_base, "holder_id": h["holder_id"], "route_id": self.db.scalar("SELECT route_id FROM question_routes WHERE question_id=? AND holder_id=?", (question_id, h["holder_id"]))},
                               msg_id=f"q:{question_id}:{h['holder_id']}")
            if row is not None:
                env.sign(row["route_key"])
            try:
                if self.transport is not None:
                    await self.transport.publish(env)
                async with self.db.tx() as c:
                    c.execute("UPDATE question_routes SET status='delivered', delivered_at=? WHERE question_id=? AND holder_id=? AND status='pending'", (now_iso(), question_id, h["holder_id"]))
            except Exception as exc:  # transport down: keep the route pending; the route job will be retried
                failed += 1
                logger.warning("route publish failed for %s -> %s: %s", question_id, h["holder_id"], exc)
                async with self.db.tx() as c:
                    c.execute("UPDATE question_routes SET error=?, attempts=attempts+1 WHERE question_id=? AND holder_id=?", (str(exc)[:300], question_id, h["holder_id"]))
        if q.get("goal_id"):
            await self.goals.charge_budget(q["goal_id"], questions=1)
        return {"routed": routed, "failed": failed, "rejected": rejected, "status": "collecting", "deadline_at": deadline}

    # ------------------------------------------------------------------ responses
    async def handle_response(self, payload: Mapping[str, Any], *, msg_id: str, holder_id: str | None = None) -> dict[str, Any]:
        """Intake of a ResponseArtifact (from the transport or from a person). Idempotent on ``msg_id``."""
        qid = payload.get("question_id")
        hid = holder_id or payload.get("holder_id")
        q = self.get(qid) if qid else None
        if q is None or not hid:
            await self.db.audit(None, "holder", hid, "response.reject", outcome="deny", detail={"reason": "unknown question", "msg_id": msg_id})
            return {"accepted": False, "reason": "unknown question"}
        existing = self.db.one("SELECT response_id FROM responses WHERE msg_id=?", (msg_id,))
        if existing is not None:
            return {"accepted": True, "duplicate": True, "response_id": existing["response_id"]}
        route = self.db.one("SELECT * FROM question_routes WHERE question_id=? AND holder_id=?", (qid, hid))
        holder = self.org.get_holder(hid)
        if route is None or holder is None or holder.get("tenant_id") != q["tenant_id"]:
            await self.db.audit(q["tenant_id"], "holder", hid, "response.reject", resource_type="question", resource_id=qid, outcome="deny", detail={"reason": "holder was not routed"})
            return {"accepted": False, "reason": "holder was not routed this question"}
        if route["status"] == "revoked":
            await self.db.audit(q["tenant_id"], "holder", hid, "response.reject", resource_type="question", resource_id=qid, outcome="deny", detail={"reason": "route revoked"})
            return {"accepted": False, "reason": "route revoked"}
        status = payload.get("status") or ("answered" if payload.get("content") else "no_evidence")
        allowed, why = self.authz.can_route(q, holder)          # re-check at use time (membership may have changed)
        if not allowed:
            status = "declined"
        refs = list(payload.get("evidence_refs") or []) if status == "answered" else []
        rid = new_id("resp")
        now = now_iso()
        async with self.db.tx() as c:
            ref_ids = self.knowledge.upsert_refs_sync(c, q["tenant_id"], hid, refs)
            c.execute("INSERT INTO responses(response_id, tenant_id, question_id, holder_id, route_id, status, content, evidence_ref_ids, provenance, confidence, freshness_at, msg_id, received_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (rid, q["tenant_id"], qid, hid, route["route_id"], status, (payload.get("content") or "")[:8000] if status == "answered" else "", j(ref_ids),
                       j({**(payload.get("provenance") or {}), **({"use_time_check": why} if not allowed else {})}), payload.get("confidence"), payload.get("freshness_at"), msg_id, now))
            c.execute("UPDATE question_routes SET status=?, responded_at=? WHERE route_id=?", (status if status != "answered" else "answered", now, route["route_id"]))
            self.db.audit_sync(c, q["tenant_id"], "holder", hid, "response.accept", resource_type="question", resource_id=qid, outcome="allow" if allowed else "deny",
                               detail={"status": status, "refs": len(ref_ids), "reason": why})
            self.db.emit_sync(c, q["tenant_id"], "question.responded", ref_type="question", ref_id=qid, payload={"holder_id": hid, "status": status},
                              audience={"unit_ids": [q["scope_unit_id"]] if q.get("scope_unit_id") else [], "user_ids": [holder["owner_id"]] if holder["owner_type"] == "user" else []})
            pending = c.execute("SELECT COUNT(*) AS n FROM question_routes WHERE question_id=? AND status IN ('pending','delivered')", (qid,)).fetchone()["n"]
            if pending == 0:
                # everyone answered: pull the collect job forward
                c.execute("UPDATE jobs SET available_at=? WHERE idempotency_key=? AND status='queued'", (now, f"question.collect:{qid}"))
        return {"accepted": True, "response_id": rid, "status": status, "all_in": pending == 0}

    async def human_response(self, principal: Principal, question_id: str, *, content: str, holder_id: str, evidence_refs: Iterable[Mapping[str, Any]] = (),
                             no_evidence: bool = False) -> dict[str, Any]:
        """A person answers from a holder they own (the evidence refs were produced by that holder)."""
        q = self.get(question_id)
        if q is None:
            raise KeyError(question_id)
        if holder_id not in set(principal.holder_ids) and not principal.is_system:
            raise Forbidden("question.respond", question_id, "you do not own that holder")
        route = self.db.one("SELECT * FROM question_routes WHERE question_id=? AND holder_id=?", (question_id, holder_id))
        if route is None:
            # a person may volunteer an answer for a question in their scope: create the route on the fly (authorization re-checked)
            holder = self.org.get_holder(holder_id)
            ok, why = self.authz.can_route(q, holder or {})
            if not ok:
                raise Forbidden("question.respond", question_id, why)
            async with self.db.tx() as c:
                c.execute("INSERT OR IGNORE INTO question_routes(route_id, tenant_id, question_id, holder_id, status, msg_id, attempts, sent_at, deadline_at) VALUES (?, ?, ?, ?, 'delivered', ?, 1, ?, ?)",
                          (new_id("route"), q["tenant_id"], question_id, holder_id, f"q:{question_id}:{holder_id}", now_iso(), now_iso()))
        payload = {"question_id": question_id, "holder_id": holder_id, "status": "no_evidence" if no_evidence else "answered", "content": content,
                   "evidence_refs": list(evidence_refs), "provenance": {"retrieval_operator": "human", "responder_user_id": principal.id}, "confidence": 0.8 if not no_evidence else None,
                   "freshness_at": now_iso()}
        out = await self.handle_response(payload, msg_id=f"resp:{question_id}:{holder_id}:human:{new_id('h')}", holder_id=holder_id)
        return {**out, "response": self.response_view(rows_to_dicts([self.db.one("SELECT * FROM responses WHERE response_id=?", (out["response_id"],))], json_fields=R_JSON)[0]) if out.get("response_id") else None}

    # ------------------------------------------------------------------ collection / status changes
    async def collect(self, question_id: str) -> dict[str, Any]:
        """Deadline reached (or everyone answered): close open routes and move on."""
        q = self.get(question_id)
        if q is None:
            raise KeyError(question_id)
        if q["status"] not in ("collecting", "routed"):
            return {"status": q["status"], "skipped": True}
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("UPDATE question_routes SET status='timeout' WHERE question_id=? AND status IN ('pending','delivered')", (question_id,))
            answered = c.execute("SELECT COUNT(*) AS n FROM responses WHERE question_id=? AND status='answered'", (question_id,)).fetchone()["n"]
            timed_out = c.execute("SELECT COUNT(*) AS n FROM question_routes WHERE question_id=? AND status='timeout'", (question_id,)).fetchone()["n"]
            if answered:
                c.execute("UPDATE questions SET status='evaluating', updated_at=? WHERE question_id=?", (now, question_id))
                self.jobs.enqueue_sync(c, "question.evaluate", idempotency_key=f"question.evaluate:{question_id}", tenant_id=q["tenant_id"], ref_type="question", ref_id=question_id, priority=3, max_attempts=4)
                status = "evaluating"
            else:
                status = "retained_uncertain"
                c.execute("UPDATE questions SET status='retained_uncertain', result=?, updated_at=?, resolved_at=?, cooldown_until=? WHERE question_id=?",
                          (j({"outcome": "no_evidence", "timed_out_routes": timed_out, "note": "no holder returned evidence; uncertainty retained"}), now, now, plus_seconds(self.cooldown_seconds), question_id))
                self.db.emit_sync(c, q["tenant_id"], "question.resolved", ref_type="question", ref_id=question_id, payload={"status": status}, audience={"unit_ids": [q["scope_unit_id"]] if q.get("scope_unit_id") else []})
            self.db.audit_sync(c, q["tenant_id"], "worker", "collector", "question.collect", resource_type="question", resource_id=question_id, detail={"answered": answered, "timed_out": timed_out, "status": status})
        if status == "retained_uncertain" and q.get("goal_id"):
            await self.goals.enqueue_tick(q["goal_id"], reason="question retained uncertain", priority=5)
        return {"status": status, "answered": answered, "timed_out": timed_out}

    async def set_status(self, question_id: str, status: str, *, result: Mapping[str, Any] | None = None, resolved: bool = False, cooldown: bool = False) -> None:
        q = self.get(question_id)
        if q is None:
            return
        now = now_iso()
        async with self.db.tx() as c:
            c.execute("UPDATE questions SET status=?, result=CASE WHEN ? THEN ? ELSE result END, updated_at=?, resolved_at=CASE WHEN ? THEN ? ELSE resolved_at END, cooldown_until=CASE WHEN ? THEN ? ELSE cooldown_until END WHERE question_id=?",
                      (status, int(result is not None), j(result or {}), now, int(resolved), now, int(cooldown), plus_seconds(self.cooldown_seconds), question_id))
            kind = "question.resolved" if resolved else "question.updated"
            self.db.emit_sync(c, q["tenant_id"], kind, ref_type="question", ref_id=question_id, payload={"status": status, "result": result or {}},
                              audience={"unit_ids": [q["scope_unit_id"]] if q.get("scope_unit_id") else [], "user_ids": [q["asker_id"]] if q["asker_type"] == "user" else []})

    async def cancel(self, principal: Principal, question_id: str, reason: str = "cancelled") -> dict[str, Any]:
        q = self.get(question_id)
        if q is None:
            raise KeyError(question_id)
        goal = self.goals.get_goal(q["goal_id"]) if q.get("goal_id") else None
        allowed = principal.is_system or q["asker_id"] == principal.id or (goal is not None and self.authz.can_manage_goal(principal, goal)) \
            or (q.get("scope_unit_id") and self.authz.can_manage_unit_knowledge(principal, q["scope_unit_id"]))
        self.authz.require(bool(allowed), "question.cancel", question_id)
        async with self.db.tx() as c:
            c.execute("UPDATE question_routes SET status='revoked' WHERE question_id=? AND status IN ('pending','delivered')", (question_id,))
        await self.set_status(question_id, "cancelled", result={"outcome": "cancelled", "reason": reason}, resolved=True)
        await self.jobs.cancel(ref_type="question", ref_id=question_id)
        await self.db.audit(q["tenant_id"], principal.kind, principal.id, "question.cancel", resource_type="question", resource_id=question_id, detail={"reason": reason})
        return self.view(self.get(question_id), principal=principal)  # type: ignore[arg-type]

    async def expire_stale(self, *, older_than_seconds: float) -> int:
        """Maintenance: questions stuck in a live status far past their deadline are expired."""
        cutoff = plus_seconds(-older_than_seconds)
        rows = self.db.all(f"SELECT question_id FROM questions WHERE status IN ({','.join('?' * len(LIVE_STATUSES))}) AND updated_at < ?", (*LIVE_STATUSES, cutoff))
        for r in rows:
            await self.set_status(r["question_id"], "expired", result={"outcome": "expired"}, resolved=True, cooldown=True)
            await self.jobs.cancel(ref_type="question", ref_id=r["question_id"])
        return len(rows)

    async def revoke_routes_for_holder(self, holder_id: str, reason: str) -> int:
        async with self.db.tx() as c:
            return c.execute("UPDATE question_routes SET status='revoked', error=? WHERE holder_id=? AND status IN ('pending','delivered')", (reason, holder_id)).rowcount

    def outcome_counts(self, tenant_id: str) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.all("SELECT status, COUNT(*) AS n FROM questions WHERE tenant_id=? GROUP BY status", (tenant_id,))}

    def failed_routes(self, tenant_id: str) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM question_routes WHERE tenant_id=? AND status IN ('failed','timeout','revoked')", (tenant_id,), 0))

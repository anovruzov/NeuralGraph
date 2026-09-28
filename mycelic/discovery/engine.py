"""The continual-discovery loop, step by step (docs/mycelic/ARCHITECTURE.md "Discovery loop").

Each public method handles one durable job kind. Every write is idempotent (claims, discoveries and follow-up
questions are keyed by question id + step), and each question run keeps a checkpoint in ``question_runs`` so a
worker that dies mid-step resumes without repeating a model call whose result was already stored.

Nothing here talks to a holder directly: questions leave through the transport, responses come back through
``on_transport``. Nothing here bypasses authorization: the loop acts as the goal owner's principal and every
claim goes through the commit gate.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Iterable, Mapping

from ..authz import Authorizer, Principal
from ..db.coord import CoordDB, row_to_dict, rows_to_dicts
from ..goals import GoalService
from ..inquiry import CooldownActive, DuplicateQuestion, LIVE_STATUSES, QuestionService
from ..jobs import Job, JobQueue
from ..knowledge import KnowledgeService
from ..models.base import ModelError, ModelRouter
from ..org import OrgService
from ..transport import Envelope, Subjects, Transport
from ..util import j, jl, new_id, now_iso, parse_iso, plus_seconds, utcnow

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "was", "were", "be", "by", "at", "as", "it", "this", "that",
         "from", "what", "which", "have", "has", "had", "do", "does", "did", "you", "your", "we", "our", "they", "their", "about", "any", "all"}


def _tokens(text: str) -> set[str]:
    try:
        from NeuralGraph.chat_memory.textutil import stem
    except Exception:  # pragma: no cover
        stem = lambda w: w  # noqa: E731
    return {stem(w) for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def _agrees(a: str, b: str) -> bool | None:
    """True = same statement, False = contradicts, None = unrelated (mirrors the fake evaluate rules)."""
    ta, tb = _tokens(a), _tokens(b)
    if len(ta & tb) < 3:
        return None
    na, nb = _numbers(a), _numbers(b)
    if na and nb and na != nb:
        return False
    neg = {"not", "no", "never", "longer"}
    wa, wb = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    if bool(wa & neg) != bool(wb & neg):
        return False
    return True


class LoopEngine:
    def __init__(self, db: CoordDB, org: OrgService, authz: Authorizer, jobs: JobQueue, goals: GoalService, knowledge: KnowledgeService,
                 questions: QuestionService, router: ModelRouter | None, transport: Transport | None, *, loop_defaults: Mapping[str, Any] | None = None,
                 worker_id: str = "engine") -> None:
        self.db, self.org, self.authz, self.jobs, self.goals, self.knowledge, self.questions = db, org, authz, jobs, goals, knowledge, questions
        self.router = router
        self.transport = transport
        self.loop_defaults = dict(loop_defaults or goals.loop_defaults)
        self.worker_id = worker_id
        self.hooks: dict[str, Any] = {}          # e.g. {"demo.simulate": async fn(job)} installed by the seed module

    # ================================================================== helpers
    def _principal_for_question(self, q: Mapping[str, Any], goal: Mapping[str, Any] | None) -> Principal:
        if goal is not None:
            return self.authz.principal_for_loop(goal["tenant_id"], goal)
        if q["asker_type"] == "user":
            p = self.authz.principal_for_user(q["asker_id"], session_kind="loop")
            if p is not None:
                return p
        return self.authz.principal_for_system(q["tenant_id"], worker_id=self.worker_id)

    def _checkpoint(self, question_id: str) -> dict[str, Any]:
        r = row_to_dict(self.db.one("SELECT * FROM question_runs WHERE question_id=?", (question_id,)), json_fields=("state",))
        return r or {"question_id": question_id, "step": "", "state": {}, "attempts": 0}

    async def _save_checkpoint(self, question_id: str, tenant_id: str, step: str, state: Mapping[str, Any]) -> None:
        async with self.db.tx() as c:
            c.execute("INSERT INTO question_runs(question_id, tenant_id, step, state, attempts, updated_at) VALUES (?, ?, ?, ?, 1, ?) "
                      "ON CONFLICT(question_id) DO UPDATE SET step=excluded.step, state=excluded.state, attempts=question_runs.attempts+1, updated_at=excluded.updated_at",
                      (question_id, tenant_id, step, j(state), now_iso()))

    async def _charge(self, goal_id: str | None, since: str, *, question_id: str | None = None) -> dict[str, Any]:
        """Charge the goal (and the question) with the model usage recorded since the last charge. Cursors on the
        ledger's row id make this exact and idempotent; ``since`` is kept for logging only."""
        if question_id:
            r = self.db.one("SELECT COALESCE(MAX(id), 0) AS m, COALESCE(SUM(input_tokens + output_tokens), 0) AS t, COALESCE(SUM(cost_usd), 0) AS c, "
                            "(SELECT COALESCE(json_extract(budget_spent, '$.usage_cursor'), 0) FROM questions WHERE question_id=?) AS cur "
                            "FROM model_usage WHERE question_id=? AND id > (SELECT COALESCE(json_extract(budget_spent, '$.usage_cursor'), 0) FROM questions WHERE question_id=?)",
                            (question_id, question_id, question_id))
            if r is not None and int(r["m"]) > int(r["cur"] or 0):
                async with self.db.tx() as c:
                    spent = jl(c.execute("SELECT budget_spent FROM questions WHERE question_id=?", (question_id,)).fetchone()["budget_spent"], {})
                    spent["tokens"] = int(spent.get("tokens", 0)) + int(r["t"])
                    spent["usd"] = round(float(spent.get("usd", 0.0)) + float(r["c"]), 6)
                    spent["usage_cursor"] = int(r["m"])
                    c.execute("UPDATE questions SET budget_spent=? WHERE question_id=?", (j(spent), question_id))
        if not goal_id:
            return {}
        return await self.goals.charge_usage(goal_id)

    async def _run_task(self, task: str, payload: Mapping[str, Any], *, tenant_id: str, goal: Mapping[str, Any] | None, question_id: str | None = None) -> dict[str, Any]:
        if self.router is None:
            raise ModelError("no model provider configured")
        max_tier = ((goal or {}).get("budget") or {}).get("model_tier_max")
        return await self.router.run_task(task, dict(payload), tenant_id=tenant_id, goal_id=(goal or {}).get("goal_id"), question_id=question_id,
                                          max_tier=max_tier, policy_tiers=self.org.policy(tenant_id, "model_tiers", {}) or {})

    def domains_for_goal(self, goal: Mapping[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
        """Candidate evidence domains: the goal's declared domains plus those of holders routable for its scope."""
        pseudo = {"tenant_id": goal["tenant_id"], "scope_unit_id": goal.get("scope_unit_id") or (goal["owner_id"] if goal["owner_type"] == "unit" else None),
                  "policy": {"visibility": "unit"}, "candidate_domains": []}
        holders = [h for h in self.org.list_holders(goal["tenant_id"]) if self.authz.can_route(pseudo, h)[0]]
        domains: list[str] = list((goal.get("measurement_source") or {}).get("domains") or [])
        for h in holders:
            for d in h.get("domains") or []:
                if d != "*" and d not in domains:
                    domains.append(d)
        return domains, holders

    # ================================================================== observe
    def observe(self, goal: Mapping[str, Any], since: str | None) -> dict[str, Any]:
        gid, tid = goal["goal_id"], goal["tenant_id"]
        since = since or "1970-01-01T00:00:00+00:00"
        new_refs = rows_to_dicts(self.db.all(
            "SELECT e.ref_id, e.title, e.holder_id, e.source_root_id FROM evidence_refs e JOIN responses r ON r.evidence_ref_ids LIKE '%' || e.ref_id || '%' "
            "JOIN questions q ON q.question_id = r.question_id WHERE q.goal_id=? AND e.created_at > ? LIMIT 50", (gid, since)))
        revised = rows_to_dicts(self.db.all(
            "SELECT DISTINCT e.ref_id, e.status, e.title FROM evidence_refs e JOIN claim_evidence ce ON ce.ref_id=e.ref_id JOIN claims c ON c.claim_id=ce.claim_id "
            "WHERE c.goal_id=? AND e.status IN ('revised','retracted') AND e.updated_at > ? LIMIT 50", (gid, since)))
        conflicts = rows_to_dicts(self.db.all(
            "SELECT k.conflict_id, k.summary, k.status FROM conflicts k JOIN claims c ON c.claim_id=k.claim_a_id WHERE c.goal_id=? AND k.status IN ('open','investigating') LIMIT 50", (gid,)))
        stale = rows_to_dicts(self.db.all("SELECT claim_id, text FROM claims WHERE goal_id=? AND status='stale' LIMIT 50", (gid,)))
        hypotheses = rows_to_dicts(self.db.all("SELECT claim_id, text FROM claims WHERE goal_id=? AND status='hypothesis' LIMIT 50", (gid,)))
        unanswered = rows_to_dicts(self.db.all(
            "SELECT r.route_id, r.holder_id, r.status, q.question_id FROM question_routes r JOIN questions q ON q.question_id=r.question_id WHERE q.goal_id=? AND r.status IN ('pending','delivered') LIMIT 50", (gid,)))
        goal_changed = int(self.db.scalar("SELECT COUNT(*) FROM revisions WHERE object_type='goal' AND object_id=? AND at > ? AND reason NOT IN ('created')", (gid, since), 0))
        ingested = rows_to_dicts(self.db.all("SELECT id, payload FROM events WHERE tenant_id=? AND kind='document.ingested' AND at > ? ORDER BY id DESC LIMIT 50", (tid, since)), json_fields=("payload",))
        scope_units = set(self.org.descendants(goal.get("scope_unit_id") or (goal["owner_id"] if goal["owner_type"] == "unit" else ""))) if (goal.get("scope_unit_id") or goal["owner_type"] == "unit") else set()
        docs = []
        for ev in ingested:
            hid = (ev.get("payload") or {}).get("holder_id")
            holder = self.org.get_holder(hid) if hid else None
            if holder and self.authz.can_route({"tenant_id": tid, "scope_unit_id": goal.get("scope_unit_id"), "policy": {"visibility": "unit"}, "candidate_domains": []}, holder)[0]:
                docs.append({"holder_id": hid, "title": (ev.get("payload") or {}).get("title", ""), "domains": (ev.get("payload") or {}).get("domains", [])})
        return {"since": since, "new_evidence": new_refs, "revised_evidence": revised, "open_conflicts": conflicts, "stale_claims": stale, "hypotheses": hypotheses,
                "unanswered_routes": unanswered, "goal_changed": goal_changed, "new_documents": docs, "scope_units": sorted(scope_units)}

    # ================================================================== loop tick
    async def tick(self, job: Job) -> dict[str, Any]:
        goal = self.goals.get_goal(job.ref_id or "")
        loop = self.goals.get_loop(job.ref_id or "") if goal else None
        if goal is None or loop is None:
            return {"skipped": "goal or loop missing"}
        gid, tid = goal["goal_id"], goal["tenant_id"]
        cfg = {**self.loop_defaults, **(loop.get("config") or {})}
        interval = float(cfg.get("check_interval_seconds", 300))
        next_check = plus_seconds(interval)
        started = now_iso()
        if goal["status"] != "active" or loop["desired"] != "active":
            state = "paused" if (loop["desired"] == "paused" or goal["status"] == "paused") else ("stopped" if loop["desired"] == "stopped" else "blocked")
            await self.goals.set_loop_state(gid, state, f"goal is {goal['status']}, loop desired {loop['desired']}", worker_id=self.worker_id)
            return {"state": state}
        if goal["status"] == "completed":
            await self.goals.set_loop_state(gid, "completed", "goal completed", worker_id=self.worker_id)
            return {"state": "completed"}
        principal = self.authz.principal_for_loop(tid, goal)
        bs = self.goals.budget_status(gid)
        if bs["exhausted"]:
            await self.goals.set_loop_state(gid, "budget_exhausted", f"budget exhausted on {', '.join(bs['exhausted_on'])}; raise the budget or archive the goal", worker_id=self.worker_id)
            return {"state": "budget_exhausted"}
        if self.router is None:
            await self.goals.set_loop_state(gid, "blocked", "no model provider configured (set MYCELIC_MODEL_* and provider keys)", worker_id=self.worker_id, next_check_at=next_check)
            return {"state": "blocked"}
        domains, holders = self.domains_for_goal(goal)
        if not holders:
            await self.goals.set_loop_state(gid, "blocked", "no authorized evidence holders in the goal's scope; register a holder or widen the scope", worker_id=self.worker_id, next_check_at=next_check)
            return {"state": "blocked"}
        live = self.db.all(f"SELECT question_id, candidate_domains, text FROM questions WHERE goal_id=? AND status IN ({','.join('?' * len(LIVE_STATUSES))})", (gid, *LIVE_STATUSES))
        slots = int(cfg.get("max_concurrent_questions", 2)) - len(live)
        obs = self.observe(goal, loop.get("last_run_at"))
        claims = rows_to_dicts(self.db.all("SELECT claim_id, text, status, question_id, support FROM claims WHERE goal_id=? AND status<>'retracted' ORDER BY updated_at DESC LIMIT 100", (gid,)), json_fields=("support",))
        q_domains = {r["question_id"]: jl(r["candidate_domains"], []) for r in self.db.all("SELECT question_id, candidate_domains FROM questions WHERE goal_id=?", (gid,))}
        covered = {d for c in claims if c["status"] == "supported" for d in q_domains.get(c["question_id"], [])}
        attempted = {d for r in self.db.all("SELECT candidate_domains FROM questions WHERE goal_id=? AND depth=0 AND kind='gap'", (gid,)) for d in jl(r["candidate_domains"], [])}
        uncovered = [d for d in domains if d not in covered and d not in attempted]
        # claims and conflicts that already have a question about them are not gaps any more (bounded inquiry)
        handled_claims = {r["cid"] for r in self.db.all("SELECT json_extract(trigger, '$.claim_id') AS cid FROM questions WHERE goal_id=? AND json_extract(trigger, '$.claim_id') IS NOT NULL", (gid,))}
        handled_conflicts = {r["kid"] for r in self.db.all("SELECT json_extract(trigger, '$.conflict_id') AS kid FROM questions WHERE goal_id=? AND json_extract(trigger, '$.conflict_id') IS NOT NULL", (gid,))}
        needs_verification = [c for c in claims if c["status"] in ("hypothesis", "stale") and c["claim_id"] not in handled_claims]
        open_conflicts = [k for k in obs["open_conflicts"] if k["conflict_id"] not in handled_conflicts]
        useful = bool(uncovered or obs["revised_evidence"] or open_conflicts or needs_verification or obs["goal_changed"] or obs["new_documents"])
        if slots <= 0:
            await self.goals.set_loop_state(gid, "active", f"{len(live)} question(s) in flight; waiting for responses", worker_id=self.worker_id, next_check_at=next_check, ran=True)
            return {"state": "active", "in_flight": len(live)}
        if not useful:
            await self.goals.set_loop_state(gid, "waiting", "nothing useful to ask right now; waiting for new evidence, a revision, or the next scheduled check (no model tokens spent)",
                                            worker_id=self.worker_id, next_check_at=next_check, ran=True)
            return {"state": "waiting"}
        # identify the most useful gaps (one model call, light tier)
        gap_obs = {k: v for k, v in obs.items() if k not in ("scope_units", "since", "open_conflicts", "hypotheses", "stale_claims", "new_evidence", "unanswered_routes")}
        gap_obs["open_conflicts"] = open_conflicts
        gap_obs["stale_claims"] = [c for c in needs_verification if c["status"] == "stale"]
        gap_input = {
            "goal": {"goal_id": gid, "title": goal["title"], "objective": goal["objective"], "success_criteria": goal.get("success_criteria") or []},
            "observations": gap_obs,
            "existing_claims": [{"claim_id": c["claim_id"], "text": c["text"], "status": c["status"], "domains": q_domains.get(c["question_id"], [])}
                                for c in claims[:40] if c["status"] != "hypothesis" or c in needs_verification],
            "open_questions": [{"question_id": r["question_id"], "text": r["text"]} for r in live],
            "candidate_domains": uncovered, "max_gaps": max(1, slots * 2),
        }
        try:
            gaps = (await self._run_task("identify_gap", gap_input, tenant_id=tid, goal=goal)).get("gaps") or []
        except ModelError as exc:
            await self.goals.set_loop_state(gid, "failed", f"model call failed: {exc}"[:400], worker_id=self.worker_id, next_check_at=next_check, ran=True)
            await self._charge(gid, started)
            return {"state": "failed", "error": str(exc)}
        weights = self.org.policy(tid, "priority_weights", {}) or {}
        from ..inquiry import prioritize
        scored = sorted(((prioritize(goal, g, weights, holders=len(holders))[0], g) for g in gaps if isinstance(g, dict)), key=lambda x: -x[0])
        created: list[str] = []
        existing_texts = [r["text"] for r in live]      # only in-flight questions: resolved ones are handled by dedupe + cooldown
        for score, gap in scored:
            if len(created) >= slots:
                break
            if self.goals.budget_status(gid)["exhausted"]:
                break
            lineage = []
            for k in ("claim_id", "conflict_id", "ref_id"):
                if gap.get(k):
                    lineage.append({"type": {"claim_id": "claim", "conflict_id": "conflict", "ref_id": "evidence_ref"}[k], "id": gap[k]})
            trig_extra = {k: gap[k] for k in ("claim_id", "conflict_id") if gap.get(k)}
            try:
                draft = await self._run_task("draft_question", {"goal": gap_input["goal"], "gap": gap, "scope": {"unit_id": goal.get("scope_unit_id"), "domains": domains},
                                                                "existing_question_texts": existing_texts[:30], "valid_window_days": 365}, tenant_id=tid, goal=goal)
            except ModelError as exc:
                logger.warning("draft_question failed for goal %s: %s", gid, exc)
                continue
            kind = draft.get("kind") if draft.get("kind") in ("gap", "verification", "contradiction", "relationship", "hypothesis", "prediction") else (gap.get("kind") or "gap")
            trig = {"kind": "observation", "gap": gap.get("description"), "rationale": gap.get("rationale"), **trig_extra,
                    "observed": {k: len(v) if isinstance(v, list) else v for k, v in obs.items() if k not in ("scope_units", "since")}}
            try:
                q = await self.questions.create(principal, {"text": draft["question"], "goal_id": gid, "scope_unit_id": goal.get("scope_unit_id") or (goal["owner_id"] if goal["owner_type"] == "unit" else None),
                                                            "kind": kind, "candidate_domains": list(draft.get("candidate_domains") or gap.get("domains") or []), "trigger": trig,
                                                            "motivating_lineage": lineage, "uncertainty": {"prior": gap.get("uncertainty", 0.5), "note": draft.get("uncertainty_note", "")},
                                                            "valid_from": plus_seconds(-365 * 86400), "valid_to": plus_seconds(365 * 86400)},
                                                asker_type="loop", asker_id=f"goal:{gid}", estimates=gap, holders_estimate=len(holders), is_demo=bool(goal.get("is_demo")))
                created.append(q["question_id"])
                existing_texts.append(q["text"])
            except (DuplicateQuestion, CooldownActive) as exc:
                logger.info("tick %s: skipped question (%s)", gid, exc)
            except ValueError as exc:
                logger.info("tick %s: cannot create question: %s", gid, exc)
        charge = await self._charge(gid, started)
        if charge.get("exhausted"):
            await self.goals.set_loop_state(gid, "budget_exhausted", f"budget exhausted on {', '.join(charge['exhausted_on'])}", worker_id=self.worker_id, ran=True, stats_delta={"questions_asked": len(created)})
            return {"state": "budget_exhausted", "created": created}
        if created or live:
            expl = f"asked {len(created)} question(s); {len(live) + len(created)} in flight" if created else f"{len(live)} question(s) in flight"
            await self.goals.set_loop_state(gid, "active", expl, worker_id=self.worker_id, next_check_at=next_check, ran=True, stats_delta={"questions_asked": len(created), "ticks": 1})
            return {"state": "active", "created": created}
        await self.goals.set_loop_state(gid, "waiting", "no new question could be formed (duplicates or cooldown); waiting for the next event or scheduled check",
                                        worker_id=self.worker_id, next_check_at=next_check, ran=True, stats_delta={"ticks": 1})
        return {"state": "waiting", "created": []}

    # ================================================================== question jobs
    async def route_question(self, job: Job) -> dict[str, Any]:
        q = self.questions.get(job.ref_id or "")
        if q is None:
            return {"skipped": "missing"}
        exclude = list((q.get("policy") or {}).get("exclude_holder_ids") or [])
        out = await self.questions.route(q["question_id"], exclude_holder_ids=exclude)
        if out.get("status") == "failed" and q.get("goal_id"):
            if exclude or int(q.get("depth") or 0) > 0:
                # a verification with every supporter excluded may legitimately find no independent holder: uncertainty is retained
                await self.questions.set_status(q["question_id"], "failed", result={**(q.get("result") or {}), "outcome": "no_independent_holders",
                                                "note": "no authorized holder outside the current support set; uncertainty retained"}, resolved=True, cooldown=True)
                await self.goals.enqueue_tick(q["goal_id"], reason="verification could not be routed", priority=5)
            else:
                await self.goals.set_loop_state(q["goal_id"], "blocked", "a question could not be routed: no authorized holders in scope", worker_id=self.worker_id)
        return out

    async def collect(self, job: Job) -> dict[str, Any]:
        return await self.questions.collect(job.ref_id or "")

    async def evaluate(self, job: Job) -> dict[str, Any]:
        q = self.questions.get(job.ref_id or "")
        if q is None or q["status"] != "evaluating":
            return {"skipped": q["status"] if q else "missing"}
        qid, tid = q["question_id"], q["tenant_id"]
        goal = self.goals.get_goal(q["goal_id"]) if q.get("goal_id") else None
        principal = self._principal_for_question(q, goal)
        started = now_iso()
        run = self._checkpoint(qid)
        state = dict(run.get("state") or {})
        responses = [r for r in self.questions.responses(qid) if r["status"] == "answered"]
        if "evaluation" not in state:
            resp_in = []
            for r in responses:
                refs = self.knowledge.refs_by_ids(r.get("evidence_ref_ids") or [])
                resp_in.append({"response_id": r["response_id"], "holder_id": r["holder_id"], "content": r["content"], "confidence": r.get("confidence"),
                                "refs": [{"ref_id": x["ref_id"], "root_id": x.get("source_root_id"), "root_known": bool(x.get("root_known")), "observed_at": x.get("observed_at")} for x in refs]})
            existing = rows_to_dicts(self.db.all("SELECT claim_id, text, status FROM claims WHERE tenant_id=? AND goal_id IS ? AND status<>'retracted' ORDER BY updated_at DESC LIMIT 30", (tid, q.get("goal_id"))))
            try:
                ev = await self._run_task("evaluate_responses", {"question": {"question_id": qid, "text": q["text"], "kind": q["kind"]}, "responses": resp_in,
                                                                 "existing_claims": existing, "today": now_iso()[:10]}, tenant_id=tid, goal=goal, question_id=qid)
            except ModelError as exc:
                await self._charge(q.get("goal_id"), started, question_id=qid)
                raise
            state["evaluation"] = ev
            await self._save_checkpoint(qid, tid, "evaluated", state)
        ev = state["evaluation"]
        resp_by_id = {r["response_id"]: r for r in responses}
        model_name = (self.router.describe().get("tiers", {}).get("standard", {}).get("model") if self.router else None)
        claim_ids: list[str] = list(state.get("claim_ids") or [])
        conflict_ids: list[str] = list(state.get("conflict_ids") or [])
        gate_notes: list[dict[str, Any]] = list(state.get("gate_notes") or [])
        conflict_id = (q.get("trigger") or {}).get("conflict_id") if q["kind"] == "contradiction" else None
        if conflict_id and "investigated" not in state:
            k = self.knowledge.get_conflict(conflict_id)
            if k is not None and k["status"] != "resolved":
                a, b = self.knowledge.get_claim(k["claim_a_id"]), self.knowledge.get_claim(k["claim_b_id"])
                findings = [f for f in (ev.get("findings") or []) if isinstance(f, dict) and f.get("text")]
                sa = [f for f in findings if a and _agrees(a["text"], f["text"]) is True]
                sb = [f for f in findings if b and _agrees(b["text"], f["text"]) is True]
                still = bool(ev.get("disagreements"))
                note = f"{len(responses)} holder response(s): {len(sa)} support A, {len(sb)} support B, records still disagree: {still}"
                await self.knowledge.add_investigation(principal, conflict_id, "responses", note, question_id=qid)
                outcome = "open"
                if not still and sa and not sb:
                    await self.knowledge.resolve_conflict(principal, conflict_id, "a_wins", note=f"investigation {qid}: only A is supported by current records"); outcome = "a_wins"
                elif not still and sb and not sa:
                    await self.knowledge.resolve_conflict(principal, conflict_id, "b_wins", note=f"investigation {qid}: only B is supported by current records"); outcome = "b_wins"
                elif not still and not sa and not sb and not responses:
                    outcome = "no_evidence"
                state["investigated"] = {"conflict_id": conflict_id, "outcome": outcome, "note": note}
            else:
                state["investigated"] = {"conflict_id": conflict_id, "outcome": "already_resolved"}
            await self._save_checkpoint(qid, tid, "claims_committed", state)
        if run.get("step") not in ("claims_committed", "verified", "verification_spawned") and not state.get("investigated"):
            base = {"tenant_id": tid, "scope_unit_id": q.get("scope_unit_id"), "visibility": (q.get("policy") or {}).get("visibility", "unit"), "goal_id": q.get("goal_id"),
                    "question_id": qid, "created_by_type": "loop" if q["asker_type"] != "user" else "agent", "created_by_id": principal.id, "valid_from": q.get("valid_from"), "valid_to": q.get("valid_to"),
                    "is_demo": bool(q.get("is_demo"))}
            for i, f in enumerate(ev.get("findings") or []):
                if not isinstance(f, dict) or not (f.get("text") or "").strip():
                    continue
                refs = [{"ref_id": r, "role": "supports"} for r in dict.fromkeys(f.get("supporting_ref_ids") or [])]
                kind = f.get("kind") if f.get("kind") in ("finding", "hypothesis", "prediction", "relationship", "measurement") else "finding"
                claim, gate = await self.knowledge.commit_claim(principal, {**base, "text": f["text"], "kind": kind, "confidence": float(f.get("confidence", 0.5))}, evidence=refs,
                                                                derivation={"operator": "synthesize", "response_ids": list(f.get("supporting_response_ids") or []), "model": model_name,
                                                                            "contributor_type": "loop", "contributor_id": principal.id, "rationale": "evaluate_responses"},
                                                                question=q, idempotency_key=f"claim:{qid}:{i}")
                gate_notes.append({"finding": i, "status": gate.status, "reasons": gate.reasons})
                if claim:
                    claim_ids.append(claim["claim_id"])
            for i, d in enumerate(ev.get("disagreements") or []):
                if not isinstance(d, dict):
                    continue
                def refs_of(ids: Iterable[str]) -> list[dict[str, Any]]:
                    out: list[dict[str, Any]] = []
                    for rid in ids:
                        for ref in (resp_by_id.get(rid) or {}).get("evidence_ref_ids") or []:
                            out.append({"ref_id": ref, "role": "supports"})
                    return out
                ca, ga = await self.knowledge.commit_claim(principal, {**base, "text": d.get("a_text") or "", "kind": "finding", "confidence": 0.5}, evidence=refs_of(d.get("a_response_ids") or []),
                                                           derivation={"operator": "synthesize", "response_ids": list(d.get("a_response_ids") or []), "model": model_name, "rationale": "disagreement side A"},
                                                           question=q, idempotency_key=f"claim:{qid}:dis{i}a")
                cb, gb = await self.knowledge.commit_claim(principal, {**base, "text": d.get("b_text") or "", "kind": "finding", "confidence": 0.5}, evidence=refs_of(d.get("b_response_ids") or []),
                                                           derivation={"operator": "synthesize", "response_ids": list(d.get("b_response_ids") or []), "model": model_name, "rationale": "disagreement side B"},
                                                           question=q, idempotency_key=f"claim:{qid}:dis{i}b", conflict_with=[ca["claim_id"]] if ca else [], conflict_summary=d.get("summary") or "responses disagree")
                for cl in (ca, cb):
                    if cl:
                        claim_ids.append(cl["claim_id"])
                if ca and cb:
                    k = self.knowledge.conflicts_for_claim(ca["claim_id"])
                    conflict_ids.extend(x["conflict_id"] for x in k if x["conflict_id"] not in conflict_ids)
            claim_ids = list(dict.fromkeys(claim_ids))
            state.update({"claim_ids": claim_ids, "conflict_ids": conflict_ids, "gate_notes": gate_notes})
            await self._save_checkpoint(qid, tid, "claims_committed", state)
        # verification questions resolve against their target claim
        verified: dict[str, Any] = dict(state.get("verified") or {})
        target_id = (q.get("trigger") or {}).get("claim_id") if q["kind"] in ("verification", "contradiction") else None
        if target_id and "outcome" not in verified:
            target = self.knowledge.get_claim(target_id)
            outcome = "no_evidence"
            if target and target["status"] != "retracted":
                findings = [f for f in (ev.get("findings") or []) if isinstance(f, dict) and f.get("text")]
                agree = [f for f in findings if _agrees(target["text"], f["text"]) is True]
                disagree = [f for f in findings if _agrees(target["text"], f["text"]) is False]
                if agree:
                    async with self.db.tx() as c:
                        for f in agree:
                            for rid in f.get("supporting_ref_ids") or []:
                                c.execute("INSERT OR IGNORE INTO claim_evidence(claim_id, ref_id, role, weight) VALUES (?, ?, 'supports', 1.0)", (target_id, rid))
                        c.execute("INSERT INTO derivations(derivation_id, tenant_id, claim_id, operator, input_claim_ids, input_ref_ids, response_ids, model, contributor_type, contributor_id, rationale, created_at) VALUES (?, ?, ?, 'verify', '[]', ?, ?, ?, 'loop', ?, ?, ?)",
                                  (new_id("der"), tid, target_id, j([r for f in agree for r in (f.get("supporting_ref_ids") or [])]), j([r for f in agree for r in (f.get("supporting_response_ids") or [])]),
                                   model_name, principal.id, f"independent verification via question {qid}", now_iso()))
                    await self.knowledge.recompute_status(principal, target_id, reason=f"independent verification ({qid}) added support")
                    outcome = "confirmed"
                if disagree:
                    for f in disagree:
                        other = next((cid for cid in claim_ids if (self.knowledge.get_claim(cid) or {}).get("text") == f["text"]), None)
                        if other and other != target_id:
                            await self.knowledge.open_conflict(principal, tid, target_id, other, f"verification disagrees: {f['text'][:160]}", question_id=qid)
                    outcome = "contradicted" if not agree else "mixed"
                if not agree and not disagree:
                    await self.knowledge.revise_claim(principal, target_id, reason=f"verification ({qid}) returned no independent evidence; uncertainty retained")
            verified = {"outcome": outcome, "target_claim_id": target_id}
            state["verified"] = verified
            await self._save_checkpoint(qid, tid, "verified", state)
        # spawn blind verification for hypotheses (bounded by depth/budget; not for verification questions themselves)
        spawned: list[str] = list(state.get("verification_questions") or [])
        if q["kind"] not in ("verification",) and not spawned and self.router is not None:
            pol = self.org.policies(tid)
            for cid in claim_ids:
                cl = self.knowledge.get_claim(cid)
                if not cl or cl["status"] != "hypothesis":
                    continue
                exclude = list((cl.get("support") or {}).get("holders") or [])
                try:
                    vq = await self._run_task("compose_verification_question", {"finding_text": cl["text"], "original_question": q["text"], "domains": q.get("candidate_domains") or []},
                                              tenant_id=tid, goal=goal, question_id=qid)
                    child = await self.questions.create(principal, {"text": vq["question"], "goal_id": q.get("goal_id"), "scope_unit_id": q.get("scope_unit_id"), "kind": "verification",
                                                                    "candidate_domains": q.get("candidate_domains") or [], "parent_question_id": qid,
                                                                    "trigger": {"kind": "verification", "claim_id": cid, "ref_type": "claim", "ref_id": cid},
                                                                    "motivating_lineage": [{"type": "claim", "id": cid}, {"type": "question", "id": qid}],
                                                                    "policy": {**(q.get("policy") or {}), "blind_verification": True, "exclude_holder_ids": exclude},
                                                                    "valid_from": q.get("valid_from"), "valid_to": q.get("valid_to")},
                                                        asker_type="loop", asker_id=principal.id, estimates={"uncertainty": 0.7, "impact": 0.6, "missing_evidence": 0.8, "information_gain": 0.7},
                                                        holders_estimate=2, is_demo=bool(q.get("is_demo")))
                    spawned.append(child["question_id"])
                except (DuplicateQuestion, CooldownActive, ValueError, ModelError) as exc:
                    logger.info("no verification question for %s: %s", cid, exc)
            state["verification_questions"] = spawned
            await self._save_checkpoint(qid, tid, "verification_spawned", state)
        await self._charge(q.get("goal_id"), started, question_id=qid)
        async with self.db.tx() as c:
            self.jobs.enqueue_sync(c, "question.commit", idempotency_key=f"question.commit:{qid}", tenant_id=tid, ref_type="question", ref_id=qid, priority=3, max_attempts=4)
        return {"claims": claim_ids, "conflicts": conflict_ids, "verification_questions": spawned, "verified": verified}

    async def commit(self, job: Job) -> dict[str, Any]:
        q = self.questions.get(job.ref_id or "")
        if q is None or q["status"] not in ("evaluating", "verifying"):
            return {"skipped": q["status"] if q else "missing"}
        qid, tid = q["question_id"], q["tenant_id"]
        goal = self.goals.get_goal(q["goal_id"]) if q.get("goal_id") else None
        principal = self._principal_for_question(q, goal)
        started = now_iso()
        run = self._checkpoint(qid)
        state = dict(run.get("state") or {})
        claims = [c for c in self.knowledge.claims_by_ids(state.get("claim_ids") or []) if c["status"] != "retracted"]
        conflicts = []
        for cl in claims:
            for k in self.knowledge.conflicts_for_claim(cl["claim_id"]):
                if k["status"] != "resolved" and k not in conflicts:
                    conflicts.append(k)
        level = self.authz.unit_level(q.get("scope_unit_id"))
        unit = self.org.get_unit(q["scope_unit_id"]) if q.get("scope_unit_id") else None
        discovery_id: str | None = state.get("discovery_id")
        followups: list[str] = list(state.get("followup_ids") or [])
        if claims and not discovery_id:
            if "synthesis" not in state:
                max_fu = int(((goal or {}).get("budget") or {}).get("followups_per_question") or 3)
                syn = await self._run_task("synthesize_discovery", {
                    "goal": {"title": goal["title"], "objective": goal["objective"]} if goal else None,
                    "question": {"text": q["text"], "kind": q["kind"]},
                    "findings": [{"claim_id": c["claim_id"], "text": c["text"], "status": c["status"], "confidence": c["confidence"], "support": c.get("support")} for c in claims],
                    "conflicts": [{"conflict_id": k["conflict_id"], "summary": k["summary"], "a_text": (self.knowledge.get_claim(k["claim_a_id"]) or {}).get("text", ""),
                                   "b_text": (self.knowledge.get_claim(k["claim_b_id"]) or {}).get("text", "")} for k in conflicts],
                    "level": level, "unit_name": unit["name"] if unit else "", "max_followups": max_fu}, tenant_id=tid, goal=goal, question_id=qid)
                state["synthesis"] = syn
                await self._save_checkpoint(qid, tid, "synthesized", state)
            syn = state["synthesis"]
            kind = syn.get("kind") if syn.get("kind") in ("finding", "contradiction", "relationship", "hypothesis", "prediction") else "finding"
            disc = await self.knowledge.create_discovery(principal, tid, title=syn.get("title") or q["text"][:120], summary=syn.get("summary") or "", kind=kind,
                                                         claim_ids=[c["claim_id"] for c in claims], scope_unit_id=q.get("scope_unit_id"), level=level,
                                                         visibility=(q.get("policy") or {}).get("visibility", "unit"), goal_id=q.get("goal_id"), question_id=qid,
                                                         idempotency_key=f"disc:{qid}", is_demo=bool(q.get("is_demo")))
            discovery_id = disc["discovery_id"]
            state["discovery_id"] = discovery_id
            await self._save_checkpoint(qid, tid, "discovery_created", state)
            if syn.get("escalate") and disc["status"] == "new":
                try:
                    await self.knowledge.review_discovery(self.authz.principal_for_system(tid, worker_id=self.worker_id), discovery_id, "escalated", note=str(syn.get("escalation_reason") or "escalated by the loop"))
                except Exception as exc:  # escalation is best effort
                    logger.info("escalation skipped for %s: %s", discovery_id, exc)
            # follow-up questions (bounded by depth and dedupe; routed by their own jobs)
            for fq in (syn.get("followup_questions") or [])[:int(((goal or {}).get("budget") or {}).get("followups_per_question") or 3)]:
                if not isinstance(fq, dict) or not fq.get("question"):
                    continue
                if self.goals.budget_status(goal["goal_id"])["exhausted"] if goal else False:
                    break
                kind_f = fq.get("kind") if fq.get("kind") in ("gap", "verification", "contradiction", "relationship", "hypothesis", "prediction") else "gap"
                trig_f: dict[str, Any] = {"kind": "followup", "discovery_id": discovery_id, "rationale": fq.get("rationale", "")}
                lineage_f = [{"type": "claim", "id": c["claim_id"]} for c in claims[:5]] + [{"type": "question", "id": qid}]
                if kind_f == "contradiction" and conflicts:
                    # link the follow-up to the conflict whose records it names (most shared content tokens), else the first open one
                    qt = _tokens(fq["question"])
                    best = max(conflicts, key=lambda k: len(qt & _tokens((self.knowledge.get_claim(k["claim_a_id"]) or {}).get("text", "") + " " + (self.knowledge.get_claim(k["claim_b_id"]) or {}).get("text", ""))))
                    trig_f.update({"kind": "contradiction", "conflict_id": best["conflict_id"]})
                    lineage_f.insert(0, {"type": "conflict", "id": best["conflict_id"]})
                    if best["conflict_id"] in {(self.questions.get(x) or {}).get("trigger", {}).get("conflict_id") for x in followups}:
                        continue
                try:
                    child = await self.questions.create(principal, {"text": fq["question"], "goal_id": q.get("goal_id"), "scope_unit_id": q.get("scope_unit_id"), "kind": kind_f,
                                                                    "candidate_domains": q.get("candidate_domains") or [], "parent_question_id": qid,
                                                                    "trigger": trig_f,
                                                                    "motivating_lineage": lineage_f,
                                                                    "policy": {k: v for k, v in (q.get("policy") or {}).items() if k != "exclude_holder_ids"},
                                                                    "valid_from": q.get("valid_from"), "valid_to": q.get("valid_to")},
                                                        asker_type="loop", asker_id=principal.id, estimates={"uncertainty": 0.6, "impact": 0.6, "missing_evidence": 0.6, "information_gain": 0.6},
                                                        holders_estimate=2, is_demo=bool(q.get("is_demo")))
                    followups.append(child["question_id"])
                except (DuplicateQuestion, CooldownActive, ValueError) as exc:
                    logger.info("follow-up skipped for %s: %s", qid, exc)
            state["followup_ids"] = followups
            await self._save_checkpoint(qid, tid, "followups_created", state)
            await self.knowledge.attach_followups(discovery_id, followups)
            # proposed measurable actions become goal outcomes of kind 'action' (labelled proposed)
            if goal and any(c["status"] == "supported" for c in claims) and not state.get("actions_recorded"):
                try:
                    acts = await self._run_task("record_outcome", {"goal": {"title": goal["title"], "objective": goal["objective"]}, "discovery": {"title": disc["title"], "summary": disc["summary"],
                                                                    "claims": [{"claim_id": c["claim_id"], "text": c["text"], "status": c["status"]} for c in claims]},
                                                                    "success_criteria": goal.get("success_criteria") or []}, tenant_id=tid, goal=goal, question_id=qid)
                    for a in (acts.get("actions") or [])[:3]:
                        if isinstance(a, dict) and a.get("text"):
                            await self.goals.add_outcome(principal, goal["goal_id"], kind="action", value={"text": a["text"], "metric": a.get("metric"), "target": a.get("target"), "proposed": True, "discovery_id": discovery_id},
                                                         claim_ids=list(a.get("evidence_claim_ids") or []))
                except (ModelError, ValueError) as exc:
                    logger.info("record_outcome skipped: %s", exc)
                state["actions_recorded"] = True
                await self._save_checkpoint(qid, tid, "actions_recorded", state)
        investigated = state.get("investigated") or {}
        summary = {"claim_ids": [c["claim_id"] for c in claims], "discovery_id": discovery_id, "followup_ids": followups, "conflict_ids": [k["conflict_id"] for k in conflicts],
                   "gate_notes": state.get("gate_notes") or [], "verified": state.get("verified") or {}, "verification_questions": state.get("verification_questions") or [],
                   "investigated": investigated,
                   "outcome": "investigated" if investigated else ("committed" if claims else "no_findings"),
                   "summary": (state.get("synthesis") or {}).get("summary") or (investigated.get("note") or ("no claims passed the commit gate" if not claims else ""))}
        final = "committed" if (claims or investigated) else "retained_uncertain"
        await self.questions.set_status(qid, final, result=summary, resolved=True, cooldown=True)
        await self._save_checkpoint(qid, tid, "done", state)
        await self._charge(q.get("goal_id"), started, question_id=qid)
        if goal:
            await self.goals.recompute_progress(goal["goal_id"])
            await self.goals.set_loop_state(goal["goal_id"], self.goals.get_loop(goal["goal_id"])["state"] if self.goals.get_loop(goal["goal_id"]) else "active",
                                            "committed a knowledge update; scheduling the next tick", worker_id=self.worker_id, stats_delta={"discoveries": 1 if discovery_id else 0, "claims": len(claims)})
            await self.goals.enqueue_tick(goal["goal_id"], reason="knowledge committed", priority=3)
        return summary

    async def reverify(self, job: Job) -> dict[str, Any]:
        cid = (job.payload or {}).get("claim_id") or job.ref_id
        claim = self.knowledge.get_claim(cid or "")
        if claim is None or claim["status"] not in ("stale", "contested"):
            return {"skipped": claim["status"] if claim else "missing"}
        tid = claim["tenant_id"]
        goal = self.goals.get_goal(claim["goal_id"]) if claim.get("goal_id") else None
        if goal and (goal["status"] != "active" or self.goals.budget_status(goal["goal_id"])["exhausted"]):
            return {"skipped": "goal inactive or budget exhausted"}
        principal = self.authz.principal_for_loop(tid, goal) if goal else self.authz.principal_for_system(tid, worker_id=self.worker_id)
        orig = self.questions.get(claim["question_id"]) if claim.get("question_id") else None
        try:
            vq = await self._run_task("compose_verification_question", {"finding_text": claim["text"], "original_question": (orig or {}).get("text", ""), "domains": (orig or {}).get("candidate_domains") or []},
                                      tenant_id=tid, goal=goal, question_id=None)
            q = await self.questions.create(principal, {"text": vq["question"], "goal_id": claim.get("goal_id"), "scope_unit_id": claim.get("scope_unit_id"), "kind": "verification",
                                                        "candidate_domains": (orig or {}).get("candidate_domains") or [], "parent_question_id": None,
                                                        "trigger": {"kind": "evidence_revised" if claim["status"] == "stale" else "contradiction", "claim_id": cid, "ref_type": "claim", "ref_id": cid},
                                                        "motivating_lineage": [{"type": "claim", "id": cid}], "policy": {**((orig or {}).get("policy") or {}), "blind_verification": True, "exclude_holder_ids": []}},
                                            asker_type="loop", asker_id=principal.id, estimates={"uncertainty": 0.8, "impact": 0.7, "missing_evidence": 0.7, "information_gain": 0.7},
                                            holders_estimate=2, is_demo=bool(claim.get("is_demo")), force=True)
            await self.knowledge.revise_claim(principal, cid, reason=f"re-verification scheduled via question {q['question_id']}")
            return {"question_id": q["question_id"]}
        except (DuplicateQuestion, CooldownActive, ValueError, ModelError) as exc:
            return {"skipped": str(exc)}

    async def goal_progress(self, job: Job) -> dict[str, Any]:
        return await self.goals.recompute_progress(job.ref_id or "")

    async def maintenance(self, job: Job) -> dict[str, Any]:
        report: dict[str, Any] = {"requeued": await self.jobs.requeue_expired(), "expired_questions": await self.questions.expire_stale(older_than_seconds=24 * 3600), "stale": {}}
        for t in self.org.list_tenants():
            p = self.authz.principal_for_system(t["tenant_id"], worker_id=self.worker_id)
            stale = await self.knowledge.sweep_stale(p, t["tenant_id"])
            report["stale"][t["tenant_id"]] = stale
            for cid in stale:
                await self.jobs.enqueue("claim.reverify", idempotency_key=f"claim.reverify:{cid}:{(self.knowledge.get_claim(cid) or {}).get('version')}", tenant_id=t["tenant_id"],
                                        ref_type="claim", ref_id=cid, payload={"claim_id": cid}, priority=6)
        if self.transport is not None:
            try:
                report["transport_pruned"] = await self.transport.prune()
            except Exception as exc:  # pragma: no cover
                report["transport_error"] = str(exc)
        report["jobs_pruned"] = await self.jobs.prune()
        return report

    # ================================================================== transport intake
    async def on_transport(self, env: Envelope) -> None:
        """Everything holders send back: responses, ingest results, evidence events. Idempotent per msg_id."""
        kind = env.kind
        payload = env.payload or {}
        hid = payload.get("holder_id")
        holder = self.org.get_holder(hid) if hid else None
        if holder is None or holder.get("tenant_id") != env.tenant_id:
            await self.db.audit(env.tenant_id, "holder", hid, f"transport.{kind}", outcome="deny", detail={"reason": "unknown holder", "msg_id": env.msg_id})
            return
        secret = self.org.holder_secret_row(hid)
        if secret is not None and env.signature and not env.verify(secret["route_key"]):
            await self.db.audit(env.tenant_id, "holder", hid, f"transport.{kind}", outcome="deny", detail={"reason": "bad signature", "msg_id": env.msg_id})
            return
        if kind == "response":
            await self.questions.handle_response(payload, msg_id=env.msg_id, holder_id=hid)
        elif kind == "evidence_event":
            p = self.authz.principal_for_system(env.tenant_id, worker_id=self.worker_id)
            out = await self.knowledge.on_evidence_event(p, env.tenant_id, hid, payload.get("event", "revised"), payload.get("affected_ref_ids") or [],
                                                         new_source_root_id=payload.get("new_source_root_id"), reason=payload.get("reason", ""))
            goals_touched = set()
            for cid in out["affected_claim_ids"]:
                cl = self.knowledge.get_claim(cid)
                if cl and cl.get("goal_id"):
                    goals_touched.add(cl["goal_id"])
                await self.jobs.enqueue("claim.reverify", idempotency_key=f"claim.reverify:{cid}:{(cl or {}).get('version')}", tenant_id=env.tenant_id, ref_type="claim", ref_id=cid, payload={"claim_id": cid}, priority=4)
            for gid in goals_touched:
                await self.goals.enqueue_tick(gid, reason="evidence changed", priority=3)
            await self.db.emit(env.tenant_id, "document.revised" if payload.get("event") == "revised" else "document.retracted", ref_type="holder", ref_id=hid,
                               payload={"holder_id": hid, "affected_claims": out["affected_claim_ids"], "doc_id": payload.get("doc_id")}, audience={"user_ids": [holder["owner_id"]] if holder["owner_type"] == "user" else [], "unit_ids": [holder["owner_id"]] if holder["owner_type"] == "unit" else []})
        elif kind == "ingest_result":
            await self.db.emit(env.tenant_id, "document.ingested", ref_type="holder", ref_id=hid, payload={"holder_id": hid, "title": payload.get("title") or (payload.get("document") or {}).get("title"),
                               "domains": payload.get("domains") or (payload.get("document") or {}).get("domains") or [], "doc_id": payload.get("doc_id") or (payload.get("document") or {}).get("doc_id")},
                               audience={"user_ids": [holder["owner_id"]] if holder["owner_type"] == "user" else [], "unit_ids": [holder["owner_id"]] if holder["owner_type"] == "unit" else []})
            await self.wake_goals_for_holder(holder)
        elif kind == "heartbeat":
            await self.org.holder_heartbeat(hid, stats=payload.get("stats"))
        else:
            logger.debug("ignoring transport kind %s from %s", kind, hid)

    async def wake_goals_for_holder(self, holder: Mapping[str, Any]) -> list[str]:
        """New evidence at a holder: schedule a tick for every active loop whose scope can route to it."""
        woken = []
        for g in rows_to_dicts(self.db.all("SELECT g.* FROM goals g JOIN goal_loops l ON l.goal_id=g.goal_id WHERE g.tenant_id=? AND g.status='active' AND l.desired='active'", (holder["tenant_id"],)),
                               json_fields=("budget", "budget_spent", "measurement_source")):
            pseudo = {"tenant_id": g["tenant_id"], "scope_unit_id": g.get("scope_unit_id") or (g["owner_id"] if g["owner_type"] == "unit" else None), "policy": {"visibility": "unit"}, "candidate_domains": []}
            if self.authz.can_route(pseudo, holder)[0]:
                await self.goals.enqueue_tick(g["goal_id"], reason="new evidence ingested", priority=4, delay_seconds=2.0)
                woken.append(g["goal_id"])
        return woken

    # ================================================================== dispatch
    async def handle(self, job: Job) -> dict[str, Any]:
        handlers = {"loop.tick": self.tick, "question.route": self.route_question, "question.collect": self.collect, "question.evaluate": self.evaluate,
                    "question.commit": self.commit, "claim.reverify": self.reverify, "goal.progress": self.goal_progress, "maintenance": self.maintenance}
        h = handlers.get(job.kind) or self.hooks.get(job.kind)
        if h is None:
            raise ValueError(f"no handler for job kind {job.kind}")
        return await h(job)

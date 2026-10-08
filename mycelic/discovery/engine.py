"""The continual-discovery loop, step by step (docs/mycelic/ARCHITECTURE.md "Discovery loop").

Each public method handles one durable job kind. Every write is idempotent (claims, discoveries and follow-up
questions are keyed by question id + step), and each question run keeps a checkpoint in ``question_runs`` so a
worker that dies mid-step resumes without repeating a model call whose result was already stored.

Nothing here talks to a holder directly: questions leave through the transport, responses come back through
``on_transport``. Nothing here bypasses authorization: the loop acts as the goal owner's principal and every
claim goes through the commit gate.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
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
from ..util import iso, j, jl, new_id, now_iso, parse_iso, plus_seconds, utcnow

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


def _norm_text(text: str) -> str:
    """Question text for duplicate checks: case, spacing and a trailing "(follow-up)" marker do not make a new question."""
    t = " ".join((text or "").lower().split())
    return re.sub(r"\s*\(follow-up\)\s*$", "", t).rstrip("?. ")


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


def _child_policy(parent: Mapping[str, Any] | None, **overrides: Any) -> dict[str, Any]:
    """A follow-up inherits its parent's visibility and disclosure, never its blind / holder-exclusion settings."""
    out = {k: v for k, v in (parent or {}).items() if k not in ("exclude_holder_ids", "blind_verification")}
    out.update(overrides)
    return out


class BudgetExhausted(ModelError):
    """The goal's budget is spent: no further model call is made for it until the budget is raised or its period renews."""

    def __init__(self, message: str, *, renews_at: str | None = None) -> None:
        super().__init__(message)
        self.renews_at = renews_at


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
        """Every model call made for a goal is checked against its budget first (after charging what was spent so far), so
        spend can exceed the budget by at most the one call that crossed it."""
        if self.router is None:
            raise ModelError("no model provider configured")
        if goal is not None and goal.get("goal_id"):
            bs = await self.goals.charge_usage(goal["goal_id"])
            if bs["exhausted"]:
                raise BudgetExhausted(f"goal budget exhausted on {', '.join(bs['exhausted_on'])}", renews_at=bs.get("renews_at"))
        max_tier = ((goal or {}).get("budget") or {}).get("model_tier_max")
        return await self.router.run_task(task, dict(payload), tenant_id=tenant_id, goal_id=(goal or {}).get("goal_id"), question_id=question_id,
                                          max_tier=max_tier, policy_tiers=self.org.policy(tenant_id, "model_tiers", {}) or {})

    # ================================================================== pipeline gate and shared question builders
    def _pipeline_state(self, goal_id: str | None, *, asker_type: str = "loop") -> str:
        """``run`` while the goal's pipeline may spend tokens and reach holders, ``hold`` while it is paused (resumed by
        GoalService.resume_questions), ``stop`` once the goal is completed or archived or its loop is stopped. A question a
        person asked under a goal that is not yet active (draft) still runs; the loop's own questions need an active goal."""
        if not goal_id:
            return "run"
        g, loop = self.goals.get_goal(goal_id), self.goals.get_loop(goal_id)
        if g is None or g["status"] in ("completed", "archived") or (loop is not None and loop["desired"] == "stopped"):
            return "stop"
        if g["status"] == "paused" or (loop is not None and loop["desired"] == "paused"):
            return "hold"
        if g["status"] != "active" and asker_type != "user":
            return "hold"
        return "run"

    async def _gate_question_step(self, q: Mapping[str, Any]) -> dict[str, Any] | None:
        """Checked at the start of every question step: ``None`` to proceed, else the job's result (held or cancelled)."""
        state = self._pipeline_state(q.get("goal_id"), asker_type=q.get("asker_type") or "loop")
        if state == "run":
            return None
        if state == "stop" and q["status"] in LIVE_STATUSES:
            await self.questions.cancel(self.authz.principal_for_system(q["tenant_id"], worker_id=self.worker_id), q["question_id"], reason="goal or loop stopped")
            return {"cancelled": "goal or loop stopped"}
        return {"held": "goal or loop paused; the step is re-queued on resume"}

    def _live_questions(self, goal_id: str | None) -> int:
        if not goal_id:
            return 0
        return int(self.db.scalar(f"SELECT COUNT(*) FROM questions WHERE goal_id=? AND status IN ({','.join('?' * len(LIVE_STATUSES))})", (goal_id, *LIVE_STATUSES), 0))

    def _free_slots(self, goal_id: str | None) -> int:
        """Questions the goal may still start under ``max_concurrent_questions`` (the question being processed counts until
        it resolves, so its children never push the goal over the limit; what does not fit waits for the next tick)."""
        if not goal_id:
            return 10**6
        loop = self.goals.get_loop(goal_id)
        cfg = {**self.loop_defaults, **((loop or {}).get("config") or {})}
        return max(0, int(cfg.get("max_concurrent_questions", 2)) - self._live_questions(goal_id))

    def _supporting_holders(self, claim: Mapping[str, Any]) -> list[str]:
        refs, _ = self.knowledge.effective_refs_for_claim(claim)
        return sorted({r["holder_id"] for r in refs if r.get("role") == "supports"})

    async def _ask_verification(self, principal: Principal, goal: Mapping[str, Any] | None, claim: Mapping[str, Any], *, trigger_kind: str = "verification",
                                parent: Mapping[str, Any] | None = None, exclude_supporters: bool = True, force: bool = False,
                                extra_trigger: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Every verification question is blind: written by ``compose_verification_question`` (topic only, no number or
        conclusion; ``questions.create`` rejects a text that leaks the claim), tied to its target (``trigger.claim_id``), and
        routed away from the holders that already support the claim unless the source itself changed (re-verification)."""
        cid = claim["claim_id"]
        orig = self.questions.get(claim["question_id"]) if claim.get("question_id") else None
        domains = list((orig or parent or {}).get("candidate_domains") or [])
        vq = await self._run_task("compose_verification_question", {"finding_text": claim["text"], "original_question": (orig or {}).get("text", ""), "domains": domains},
                                  tenant_id=claim["tenant_id"], goal=goal, question_id=(parent or {}).get("question_id"))
        exclude = self._supporting_holders(claim) if exclude_supporters else []
        lineage = [{"type": "claim", "id": cid}] + ([{"type": "question", "id": parent["question_id"]}] if parent else [])
        q = await self.questions.create(principal, {
            "text": vq["question"], "goal_id": claim.get("goal_id"), "scope_unit_id": claim.get("scope_unit_id"), "kind": "verification",
            "candidate_domains": domains, "parent_question_id": (parent or {}).get("question_id"),
            "trigger": {"kind": trigger_kind, "claim_id": cid, "ref_type": "claim", "ref_id": cid, **dict(extra_trigger or {})},
            "motivating_lineage": lineage,
            "policy": _child_policy((orig or parent or {}).get("policy"), blind_verification=True, exclude_holder_ids=exclude),
            "valid_from": (orig or parent or {}).get("valid_from"), "valid_to": (orig or parent or {}).get("valid_to")},
            asker_type="loop", asker_id=principal.id, estimates={"uncertainty": 0.7, "impact": 0.6, "missing_evidence": 0.8, "information_gain": 0.7},
            holders_estimate=2, is_demo=bool(claim.get("is_demo")), force=force)
        await self._attach_to_discoveries(claim["tenant_id"], q["question_id"], [cid])
        return q

    async def _ask_contradiction(self, principal: Principal, goal: Mapping[str, Any] | None, conflict: Mapping[str, Any], *, trigger_kind: str = "contradiction",
                                 parent: Mapping[str, Any] | None = None, extra_trigger: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Investigate a disagreement without telling holders either side's figures: a neutral question on the topic,
        routed to every authorized holder in scope (including those behind each side, so each side can be re-checked)."""
        a = self.knowledge.get_claim(conflict["claim_a_id"]) or {}
        b = self.knowledge.get_claim(conflict["claim_b_id"]) or {}
        anchor = a or b
        orig = self.questions.get(anchor["question_id"]) if anchor.get("question_id") else None
        domains = list((orig or parent or {}).get("candidate_domains") or [])
        vq = await self._run_task("compose_verification_question", {"finding_text": anchor.get("text", ""), "original_question": (orig or {}).get("text", ""), "domains": domains},
                                  tenant_id=conflict["tenant_id"], goal=goal, question_id=(parent or {}).get("question_id"))
        kid = conflict["conflict_id"]
        q = await self.questions.create(principal, {
            "text": vq["question"], "goal_id": anchor.get("goal_id") or (goal or {}).get("goal_id"), "scope_unit_id": anchor.get("scope_unit_id"), "kind": "contradiction",
            "candidate_domains": domains, "parent_question_id": (parent or {}).get("question_id"),
            "trigger": {"kind": trigger_kind, "conflict_id": kid, "ref_type": "conflict", "ref_id": kid, **dict(extra_trigger or {})},
            "motivating_lineage": [{"type": "conflict", "id": kid}, {"type": "claim", "id": conflict["claim_a_id"]}, {"type": "claim", "id": conflict["claim_b_id"]}],
            "policy": _child_policy((orig or parent or {}).get("policy"), blind_verification=True),
            "valid_from": (orig or parent or {}).get("valid_from"), "valid_to": (orig or parent or {}).get("valid_to")},
            asker_type="loop", asker_id=principal.id, estimates={"uncertainty": 0.9, "impact": 0.7, "missing_evidence": 0.5, "information_gain": 0.8},
            holders_estimate=2, is_demo=bool(anchor.get("is_demo")))
        await self._attach_to_discoveries(conflict["tenant_id"], q["question_id"], [conflict["claim_a_id"], conflict["claim_b_id"]])
        return q

    async def _attach_to_discoveries(self, tenant_id: str, question_id: str, claim_ids: list[str]) -> None:
        """A question about a claim (or a disagreement between claims) is a follow-up of every discovery that contains
        that claim, whichever step asked it: the discovery shows what it led to."""
        if not claim_ids:
            return
        rows = self.db.all(f"SELECT DISTINCT d.discovery_id FROM discoveries d, json_each(d.claim_ids) AS c WHERE d.tenant_id=? AND c.value IN ({','.join('?' * len(claim_ids))})",
                           (tenant_id, *claim_ids))
        for r in rows:
            await self.knowledge.attach_followups(r["discovery_id"], [question_id])

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
        # ``since`` is the start of the previous tick minus a second, compared inclusively: anything committed while that
        # tick ran (or in the same second) is observed again rather than missed; re-observing is harmless (dedupe, cooldown)
        since = since or "1970-01-01T00:00:00+00:00"
        new_refs = rows_to_dicts(self.db.all(
            "SELECT e.ref_id, e.title, e.holder_id, e.source_root_id FROM evidence_refs e JOIN responses r ON r.evidence_ref_ids LIKE '%' || e.ref_id || '%' "
            "JOIN questions q ON q.question_id = r.question_id WHERE q.goal_id=? AND e.created_at >= ? LIMIT 50", (gid, since)))
        revised = rows_to_dicts(self.db.all(
            "SELECT DISTINCT e.ref_id, e.status FROM evidence_refs e JOIN claim_evidence ce ON ce.ref_id=e.ref_id JOIN claims c ON c.claim_id=ce.claim_id "
            "WHERE c.goal_id=? AND e.status IN ('revised','retracted','unavailable') AND e.updated_at >= ? LIMIT 50", (gid, since)))
        conflicts = rows_to_dicts(self.db.all(
            "SELECT k.conflict_id, k.summary, k.status FROM conflicts k JOIN claims c ON c.claim_id=k.claim_a_id WHERE c.goal_id=? AND k.status IN ('open','investigating') LIMIT 50", (gid,)))
        stale = rows_to_dicts(self.db.all("SELECT claim_id, text FROM claims WHERE goal_id=? AND status='stale' LIMIT 50", (gid,)))
        hypotheses = rows_to_dicts(self.db.all("SELECT claim_id, text FROM claims WHERE goal_id=? AND status='hypothesis' LIMIT 50", (gid,)))
        unanswered = rows_to_dicts(self.db.all(
            "SELECT r.route_id, r.holder_id, r.status, q.question_id FROM question_routes r JOIN questions q ON q.question_id=r.question_id WHERE q.goal_id=? AND r.status IN ('pending','delivered') LIMIT 50", (gid,)))
        goal_revs = self.db.all("SELECT revision_id FROM revisions WHERE object_type='goal' AND object_id=? AND at >= ? AND reason NOT IN ('created') ORDER BY at", (gid, since))
        goal_changed = len(goal_revs)
        ingested = rows_to_dicts(self.db.all("SELECT id, payload FROM events WHERE tenant_id=? AND kind='document.ingested' AND at >= ? ORDER BY id DESC LIMIT 50", (tid, since)), json_fields=("payload",))
        scope_units = set(self.org.descendants(goal.get("scope_unit_id") or (goal["owner_id"] if goal["owner_type"] == "unit" else ""))) if (goal.get("scope_unit_id") or goal["owner_type"] == "unit") else set()
        docs = []
        for ev in ingested:
            hid = (ev.get("payload") or {}).get("holder_id")
            holder = self.org.get_holder(hid) if hid else None
            if holder and self.authz.can_route({"tenant_id": tid, "scope_unit_id": goal.get("scope_unit_id"), "policy": {"visibility": "unit"}, "candidate_domains": []}, holder)[0]:
                # E12: what arrived, never its titles (ingested content may be private mail or messages)
                docs.append({"holder_id": hid, "domains": (ev.get("payload") or {}).get("domains", []),
                             "records": int((ev.get("payload") or {}).get("records") or 1), "event_id": ev["id"]})
        return {"since": since, "new_evidence": new_refs, "revised_evidence": revised, "open_conflicts": conflicts, "stale_claims": stale, "hypotheses": hypotheses,
                "unanswered_routes": unanswered, "goal_changed": goal_changed, "goal_revision_ids": [r["revision_id"] for r in goal_revs], "new_documents": docs,
                "scope_units": sorted(scope_units)}

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
        observed_from = iso(utcnow() - timedelta(seconds=1))      # the next tick observes from here (see observe())
        if goal["status"] != "active" or loop["desired"] != "active":
            state = "paused" if (loop["desired"] == "paused" or goal["status"] == "paused") else ("stopped" if loop["desired"] == "stopped" else "blocked")
            await self.goals.set_loop_state(gid, state, f"goal is {goal['status']}, loop desired {loop['desired']}", worker_id=self.worker_id)
            return {"state": state}
        if goal["status"] == "completed":
            await self.goals.set_loop_state(gid, "completed", "goal completed", worker_id=self.worker_id)
            return {"state": "completed"}
        principal = self.authz.principal_for_loop(tid, goal)
        bs = await self.goals.charge_usage(gid)
        if bs["exhausted"]:
            renew = f"; renews {bs['renews_at']}" if bs.get("renews_at") else "; raise the budget to continue"
            await self.goals.set_loop_state(gid, "budget_exhausted", f"budget exhausted on {', '.join(bs['exhausted_on'])}{renew} (no model tokens spent while exhausted)",
                                            worker_id=self.worker_id, next_check_at=bs.get("renews_at") or next_check)
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
        obs = self.observe(goal, (loop.get("stats") or {}).get("observed_from") or loop.get("last_run_at"))
        mark: dict[str, Any] = {"observed_from": observed_from}
        claims = rows_to_dicts(self.db.all("SELECT claim_id, tenant_id, text, status, question_id, goal_id, scope_unit_id, is_demo, support FROM claims WHERE goal_id=? AND status<>'retracted' "
                                           "ORDER BY updated_at DESC LIMIT 100", (gid,)), json_fields=("support",))
        q_domains = {r["question_id"]: jl(r["candidate_domains"], []) for r in self.db.all("SELECT question_id, candidate_domains FROM questions WHERE goal_id=?", (gid,))}
        covered = {d for c in claims if c["status"] == "supported" for d in q_domains.get(c["question_id"], [])}
        attempted = {d for r in self.db.all("SELECT candidate_domains FROM questions WHERE goal_id=? AND depth=0 AND kind='gap'", (gid,)) for d in jl(r["candidate_domains"], [])}
        uncovered = [d for d in domains if d not in covered and d not in attempted]
        # verification, contradiction and deferred follow-up gaps are built here, deterministically, with the id of what they
        # target; a target counts as handled once a question about it exists (bounded inquiry: one question per target, and
        # a stale claim once more after each time it became stale). No model call is needed to find them.
        attempts: dict[str, Any] = dict((loop.get("stats") or {}).get("target_attempts") or {})
        cooldown = float(cfg.get("cooldown_seconds", 900))
        targets = [t for t in self._deterministic_gaps(goal, claims, obs, q_domains) if self._may_attempt(attempts, t["target"], cooldown)]
        # what the model would be asked about, by identity: the observation window overlaps the previous tick on purpose
        # (nothing is missed), so the same observations must not buy a second model call
        model_obs = {"uncovered": sorted(uncovered), "revised": sorted(f"{r['ref_id']}:{r['status']}" for r in obs["revised_evidence"]),
                     "goal_revisions": obs["goal_revision_ids"], "documents": sorted(d["event_id"] for d in obs["new_documents"])}
        model_fp = hashlib.sha256(j(model_obs).encode()).hexdigest()[:24]
        needs_model = bool(uncovered or obs["revised_evidence"] or obs["goal_changed"] or obs["new_documents"]) and model_fp != (loop.get("stats") or {}).get("model_obs_fp")
        useful = bool(needs_model or targets)
        if slots <= 0:
            # observations are not consumed while no question can be asked: the watermark stays where it was
            await self.goals.set_loop_state(gid, "active", f"{len(live)} question(s) in flight; waiting for responses", worker_id=self.worker_id, next_check_at=next_check, ran=True)
            return {"state": "active", "in_flight": len(live)}
        if not useful:
            await self.goals.set_loop_state(gid, "waiting", "nothing useful to ask right now; waiting for new evidence, a revision, or the next scheduled check (no model tokens spent)",
                                            worker_id=self.worker_id, next_check_at=next_check, ran=True, stats_set=mark)
            return {"state": "waiting"}
        gaps: list[dict[str, Any]] = list(targets)
        goal_brief = {"goal_id": gid, "title": goal["title"], "objective": goal["objective"], "success_criteria": goal.get("success_criteria") or []}
        if needs_model:
            # open coverage questions need judgement: one model call (light tier) ranks them; verification / contradiction
            # gaps it proposes without a target id are dropped, because the deterministic list above already covers them
            gap_input = {
                "goal": goal_brief,
                "observations": {"revised_evidence": obs["revised_evidence"], "goal_changed": obs["goal_changed"],
                                 "new_documents": [{k: v for k, v in d.items() if k != "event_id"} for d in obs["new_documents"]]},
                "existing_claims": [{"claim_id": c["claim_id"], "text": c["text"], "status": c["status"], "domains": q_domains.get(c["question_id"], [])}
                                    for c in claims[:40] if c["status"] == "supported"],
                "open_questions": [{"question_id": r["question_id"], "text": r["text"]} for r in live],
                "candidate_domains": uncovered, "max_gaps": max(1, slots * 2),
            }
            try:
                proposed = (await self._run_task("identify_gap", gap_input, tenant_id=tid, goal=goal)).get("gaps") or []
            except BudgetExhausted as exc:
                await self.goals.set_loop_state(gid, "budget_exhausted", f"{exc}; no further model calls until the budget is raised or renews", worker_id=self.worker_id,
                                                next_check_at=exc.renews_at or next_check, ran=True)
                return {"state": "budget_exhausted"}
            except ModelError as exc:
                await self.goals.set_loop_state(gid, "failed", f"model call failed: {exc}"[:400], worker_id=self.worker_id, next_check_at=next_check, ran=True)
                await self._charge(gid, started)
                return {"state": "failed", "error": str(exc)}
            mark["model_obs_fp"] = model_fp
            for g in proposed:
                if isinstance(g, dict) and not (g.get("kind") in ("verification", "contradiction") and not (g.get("claim_id") or g.get("conflict_id"))):
                    gaps.append({**g, "target": f"model:{g.get('kind') or 'gap'}:{','.join(map(str, g.get('domains') or []))}:{g.get('description', '')[:80]}"})
        weights = self.org.policy(tid, "priority_weights", {}) or {}
        from ..inquiry import prioritize
        scored = sorted(((prioritize(goal, g, weights, holders=len(holders))[0], i, g) for i, g in enumerate(gaps)), key=lambda x: (-x[0], x[1]))
        created: list[str] = []
        live_norm = {_norm_text(r["text"]) for r in live}
        tried: dict[str, Any] = {}
        for _score, _i, gap in scored:
            if len(created) >= slots:
                break
            if self.goals.budget_status(gid)["exhausted"]:
                break
            if not self._may_attempt(attempts, gap["target"], cooldown):
                continue
            tried[gap["target"]] = gap
            try:
                q = await self._ask_for_gap(principal, goal, gap, domains=domains, goal_brief=goal_brief, obs=obs, live_texts=[r["text"] for r in live], live_norm=live_norm)
            except BudgetExhausted:
                break
            except (DuplicateQuestion, CooldownActive) as exc:
                logger.info("tick %s: skipped question (%s)", gid, exc)
                continue
            except PermissionError as exc:
                await self.goals.set_loop_state(gid, "blocked", f"the goal's owner can no longer ask in its scope ({exc}); reassign the goal or restore the membership",
                                                worker_id=self.worker_id, next_check_at=next_check, ran=True, stats_set=mark)
                return {"state": "blocked", "created": created}
            except (ValueError, ModelError) as exc:
                logger.info("tick %s: cannot create question: %s", gid, exc)
                continue
            if q is not None:
                created.append(q["question_id"])
                live_norm.add(_norm_text(q["text"]))
        # remember what was tried (created or not), so a target that cannot become a question costs no tokens again until
        # its cooldown passes, and at most three times in all
        now_s = now_iso()
        for key in tried:
            prev = attempts.get(key) or {}
            attempts[key] = {"at": now_s, "n": int(prev.get("n", 0)) + 1}
        mark["target_attempts"] = dict(sorted(attempts.items(), key=lambda kv: kv[1].get("at", ""))[-300:])
        charge = await self._charge(gid, started)
        if charge.get("exhausted"):
            await self.goals.set_loop_state(gid, "budget_exhausted", f"budget exhausted on {', '.join(charge['exhausted_on'])}", worker_id=self.worker_id, ran=True,
                                            next_check_at=charge.get("renews_at") or next_check, stats_delta={"questions_asked": len(created)}, stats_set=mark)
            return {"state": "budget_exhausted", "created": created}
        if created or live:
            expl = f"asked {len(created)} question(s); {len(live) + len(created)} in flight" if created else f"{len(live)} question(s) in flight"
            await self.goals.set_loop_state(gid, "active", expl, worker_id=self.worker_id, next_check_at=next_check, ran=True,
                                            stats_delta={"questions_asked": len(created), "ticks": 1}, stats_set=mark)
            return {"state": "active", "created": created}
        await self.goals.set_loop_state(gid, "waiting", "no new question could be formed (duplicates or cooldown); waiting for the next event or scheduled check",
                                        worker_id=self.worker_id, next_check_at=next_check, ran=True, stats_delta={"ticks": 1}, stats_set=mark)
        return {"state": "waiting", "created": []}

    @staticmethod
    def _may_attempt(attempts: Mapping[str, Any], key: str, cooldown: float) -> bool:
        a = attempts.get(key)
        if not a:
            return True
        if int(a.get("n", 0)) >= 3:
            return False
        at = parse_iso(a.get("at"))
        return at is None or (utcnow() - at).total_seconds() >= cooldown

    def _deterministic_gaps(self, goal: Mapping[str, Any], claims: list[dict[str, Any]], obs: Mapping[str, Any], q_domains: Mapping[str, list[str]]) -> list[dict[str, Any]]:
        """Gaps the loop can name without a model: unhandled open conflicts, hypotheses and stale claims without a question
        about them, and follow-ups an earlier commit deferred because the goal had no free question slot."""
        gid = goal["goal_id"]
        asked_claim = {r["cid"]: r["at"] for r in self.db.all("SELECT json_extract(trigger, '$.claim_id') AS cid, MAX(created_at) AS at FROM questions WHERE goal_id=? "
                                                               "AND json_extract(trigger, '$.claim_id') IS NOT NULL GROUP BY cid", (gid,))}
        asked_conflict = {r["kid"] for r in self.db.all("SELECT json_extract(trigger, '$.conflict_id') AS kid FROM questions WHERE goal_id=? AND json_extract(trigger, '$.conflict_id') IS NOT NULL", (gid,))}
        stale_ids = [c["claim_id"] for c in claims if c["status"] == "stale"]
        stale_since: dict[str, str] = {}
        if stale_ids:
            stale_since = {r["object_id"]: r["at"] for r in self.db.all(
                f"SELECT object_id, MAX(at) AS at FROM revisions WHERE object_type='claim' AND json_extract(after, '$.status')='stale' AND object_id IN ({','.join('?' * len(stale_ids))}) GROUP BY object_id",
                stale_ids)}
        gaps: list[dict[str, Any]] = []
        for k in obs.get("open_conflicts") or []:
            if k["conflict_id"] in asked_conflict:
                continue
            gaps.append({"kind": "contradiction", "conflict_id": k["conflict_id"], "target": f"conflict:{k['conflict_id']}", "description": k.get("summary") or "records disagree",
                         "uncertainty": 0.9, "impact": 0.7, "missing_evidence": 0.5, "information_gain": 0.8, "rationale": "Records disagree; an open conflict blocks commitment."})
        for c in claims:
            last = asked_claim.get(c["claim_id"])
            if c["status"] == "hypothesis" and last is None:
                why = "The claim is a hypothesis: independent support is missing."
            elif c["status"] == "stale" and (last is None or last < stale_since.get(c["claim_id"], "")):
                why = "The claim's evidence is stale or changed at its source; re-verification is due."
            else:
                continue
            gaps.append({"kind": "verification", "claim_id": c["claim_id"], "target": f"claim:{c['claim_id']}:{stale_since.get(c['claim_id'], '')}",
                         "description": "independent verification of an existing claim", "domains": q_domains.get(c["question_id"], []),
                         "uncertainty": 0.7, "impact": 0.6, "missing_evidence": 0.6, "information_gain": 0.7, "rationale": why})
        done_fu = {r["f"] for r in self.db.all("SELECT json_extract(trigger, '$.followup_of') AS f FROM questions WHERE goal_id=? AND json_extract(trigger, '$.followup_of') IS NOT NULL", (gid,))}
        for r in self.db.all("SELECT question_id, result FROM questions WHERE goal_id=? AND status IN ('committed','retained_uncertain') "
                             "AND json_array_length(json_extract(result, '$.deferred_followups')) > 0 ORDER BY resolved_at DESC LIMIT 20", (gid,)):
            for fu in jl(r["result"], {}).get("deferred_followups") or []:
                key = f"{r['question_id']}:{fu.get('index')}"
                if key in done_fu or not isinstance(fu, dict) or not fu.get("question"):
                    continue
                gaps.append({"kind": fu.get("kind") or "gap", "followup": fu, "parent_question_id": r["question_id"], "target": f"followup:{key}", "followup_of": key,
                             "description": fu.get("rationale") or "follow-up", "uncertainty": 0.6, "impact": 0.6, "missing_evidence": 0.6, "information_gain": 0.6})
        return gaps

    async def _ask_for_gap(self, principal: Principal, goal: Mapping[str, Any], gap: Mapping[str, Any], *, domains: list[str], goal_brief: Mapping[str, Any],
                           obs: Mapping[str, Any], live_texts: list[str], live_norm: set[str]) -> dict[str, Any] | None:
        gid = goal["goal_id"]
        max_depth = int((goal.get("budget") or {}).get("followup_depth") or self.questions.max_followup_depth)
        if gap.get("claim_id") and gap.get("kind") == "verification":
            claim = self.knowledge.get_claim(gap["claim_id"])
            if claim is None or claim["status"] not in ("hypothesis", "stale"):
                return None
            if claim["status"] == "stale":
                # a stale claim's changed source must be re-read: its own holders are asked too
                return await self._ask_verification(principal, goal, claim, trigger_kind="observation", exclude_supporters=False)
            # a hypothesis is verified as a follow-up of the question that produced it (the same lineage and depth bound as a
            # verification spawned at evaluation), or at the top level when that question is already at the depth limit
            parent = self.questions.get(claim["question_id"]) if claim.get("question_id") else None
            if parent is not None and int(parent.get("depth") or 0) + 1 > max_depth:
                parent = None
            return await self._ask_verification(principal, goal, claim, trigger_kind="observation", parent=parent)
        if gap.get("conflict_id"):
            k = self.knowledge.get_conflict(gap["conflict_id"])
            if k is None or k["status"] == "resolved":
                return None
            # investigated as a follow-up of the question where the disagreement surfaced (depth permitting)
            parent = self.questions.get(k["question_id"]) if k.get("question_id") else None
            if parent is not None and int(parent.get("depth") or 0) + 1 > max_depth:
                parent = None
            return await self._ask_contradiction(principal, goal, k, trigger_kind="observation", parent=parent)
        if gap.get("followup"):
            return await self._create_followup(principal, goal, self.questions.get(gap["parent_question_id"]) or {}, gap["followup"], followup_of=gap["followup_of"])
        draft = await self._run_task("draft_question", {"goal": goal_brief, "gap": {k: v for k, v in gap.items() if k != "target"}, "scope": {"unit_id": goal.get("scope_unit_id"), "domains": domains},
                                                        "existing_question_texts": live_texts[:30], "valid_window_days": 365}, tenant_id=goal["tenant_id"], goal=goal)
        if _norm_text(draft.get("question") or "") in live_norm:
            raise DuplicateQuestion("")
        kind = draft.get("kind") if draft.get("kind") in ("gap", "relationship", "hypothesis", "prediction") else "gap"
        trig = {"kind": "observation", "gap": gap.get("description"), "rationale": gap.get("rationale"),
                "observed": {k: len(v) if isinstance(v, list) else v for k, v in obs.items() if k not in ("scope_units", "since", "goal_revision_ids")}}
        return await self.questions.create(principal, {"text": draft["question"], "goal_id": gid, "scope_unit_id": goal.get("scope_unit_id") or (goal["owner_id"] if goal["owner_type"] == "unit" else None),
                                                       "kind": kind, "candidate_domains": list(draft.get("candidate_domains") or gap.get("domains") or []), "trigger": trig,
                                                       "motivating_lineage": [], "uncertainty": {"prior": gap.get("uncertainty", 0.5), "note": draft.get("uncertainty_note", "")},
                                                       "policy": {"blind_verification": False},
                                                       "valid_from": plus_seconds(-365 * 86400), "valid_to": plus_seconds(365 * 86400)},
                                           asker_type="loop", asker_id=f"goal:{gid}", estimates=gap, holders_estimate=2, is_demo=bool(goal.get("is_demo")))

    # ================================================================== question jobs
    async def route_question(self, job: Job) -> dict[str, Any]:
        q = self.questions.get(job.ref_id or "")
        if q is None:
            return {"skipped": "missing"}
        if q["status"] in ("draft", "routed"):
            held = await self._gate_question_step(q)
            if held is not None:
                return held
        exclude = list((q.get("policy") or {}).get("exclude_holder_ids") or [])
        out = await self.questions.route(q["question_id"], exclude_holder_ids=exclude)
        if out.get("status") == "failed":
            # the question records why it could not be routed, with a cooldown, and the loop carries on with other work
            outcome = "no_independent_holders" if exclude else "no_authorized_holders"
            note = ("no authorized holder outside the current support set; uncertainty retained" if exclude
                    else "no authorized holder in scope serves this question's domains; uncertainty retained")
            await self.questions.set_status(q["question_id"], "failed", result={**(q.get("result") or {}), "outcome": outcome, "note": note, "rejected": out.get("rejected") or []},
                                            resolved=True, cooldown=True)
            goal = self.goals.get_goal(q["goal_id"]) if q.get("goal_id") else None
            if goal is not None:
                _domains, holders = self.domains_for_goal(goal)
                if holders:
                    await self.goals.enqueue_tick(goal["goal_id"], reason="a question could not be routed", priority=5, delay_seconds=5.0)
                else:
                    loop = self.goals.get_loop(goal["goal_id"]) or {}
                    interval = float({**self.loop_defaults, **(loop.get("config") or {})}.get("check_interval_seconds", 300))
                    await self.goals.set_loop_state(goal["goal_id"], "blocked", "no authorized evidence holders in the goal's scope; register a holder or widen the scope",
                                                    worker_id=self.worker_id, next_check_at=plus_seconds(interval))
        return out

    async def collect(self, job: Job) -> dict[str, Any]:
        return await self.questions.collect(job.ref_id or "")

    async def evaluate(self, job: Job) -> dict[str, Any]:
        q = self.questions.get(job.ref_id or "")
        if q is None or q["status"] != "evaluating":
            return {"skipped": q["status"] if q else "missing"}
        held = await self._gate_question_step(q)
        if held is not None:
            return held
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
            except BudgetExhausted as exc:
                return await self._defer_for_budget(job, q, goal, exc)
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
                winner = ("a_wins", a, b, sa) if (not still and sa and not sb) else (("b_wins", b, a, sb) if (not still and sb and not sa) else None)
                if winner is not None:
                    verdict, win, lose, win_findings = winner
                    decisive, why = self._decisive(qid, responses, win, lose, win_findings)
                    if decisive:
                        await self.knowledge.resolve_conflict(principal, conflict_id, verdict, note=f"investigation {qid}: only one record is supported by current records ({why})")
                        outcome = verdict
                    else:
                        # not enough to retract anyone: the conflict stays under investigation, uncertainty retained, a person decides
                        outcome = "needs_review"
                        note = f"{note}; not resolved automatically: {why}"
                        await self.knowledge.add_investigation(principal, conflict_id, "needs_review", note, question_id=qid)
                        await self._notify_conflict_review(goal, k, note)
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
            finding_claims: list[tuple[set[str], str, str]] = []      # (supporting response ids, finding text, claim id)
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
                    finding_claims.append((set(f.get("supporting_response_ids") or []), f["text"].strip(), claim["claim_id"]))

            def refs_of(ids: Iterable[str]) -> list[dict[str, Any]]:
                out: list[dict[str, Any]] = []
                for rid in ids:
                    for ref in (resp_by_id.get(rid) or {}).get("evidence_ref_ids") or []:
                        out.append({"ref_id": ref, "role": "supports"})
                return out

            def existing_side(resp_ids: list[str], text: str) -> str | None:
                """A disagreement side that is a finding already committed above (its responses are part of that finding's
                support) is that finding: the majority statement becomes contested instead of supported next to a copy."""
                ids = set(resp_ids)
                for rids, ftext, cid in finding_claims:
                    if (ids and ids <= rids) or (not ids and text.strip() == ftext):
                        return cid
                return None

            async def side_claim(resp_ids: list[str], text: str, conflict_with: list[str], summary: str) -> str | None:
                cid = existing_side(resp_ids, text)
                if cid is not None:
                    return cid
                # one claim per distinct response set, however many disagreement pairs name it
                key = hashlib.sha256(("|".join(sorted(resp_ids)) or text.strip()).encode()).hexdigest()[:16]
                cl, g = await self.knowledge.commit_claim(principal, {**base, "text": text, "kind": "finding", "confidence": 0.5}, evidence=refs_of(resp_ids),
                                                          derivation={"operator": "synthesize", "response_ids": list(resp_ids), "model": model_name, "rationale": "disagreement side"},
                                                          question=q, idempotency_key=f"claim:{qid}:side:{key}", conflict_with=conflict_with, conflict_summary=summary)
                gate_notes.append({"disagreement_side": key, "status": g.status, "reasons": g.reasons})
                if cl is None:
                    return None
                finding_claims.append((set(resp_ids), text.strip(), cl["claim_id"]))
                return cl["claim_id"]

            for d in ev.get("disagreements") or []:
                if not isinstance(d, dict) or not (d.get("a_text") or "").strip() or not (d.get("b_text") or "").strip():
                    continue
                summary = d.get("summary") or "responses disagree"
                ra, rb = list(d.get("a_response_ids") or []), list(d.get("b_response_ids") or [])
                ca = await side_claim(ra, d["a_text"], [], summary)
                if ca is None:
                    continue
                pre_b = existing_side(rb, d["b_text"])
                cb = pre_b if pre_b is not None else await side_claim(rb, d["b_text"], [ca], summary)
                if cb is None or cb == ca:
                    continue
                kc = await self.knowledge.open_conflict(principal, tid, ca, cb, summary, question_id=qid)
                claim_ids.extend([ca, cb])
                if kc["conflict_id"] not in conflict_ids:
                    conflict_ids.append(kc["conflict_id"])
            claim_ids = list(dict.fromkeys(claim_ids))
            state.update({"claim_ids": claim_ids, "conflict_ids": conflict_ids, "gate_notes": gate_notes})
            await self._save_checkpoint(qid, tid, "claims_committed", state)
        # verification questions resolve against their target claim
        verified: dict[str, Any] = dict(state.get("verified") or {})
        target_id = (q.get("trigger") or {}).get("claim_id") if q["kind"] in ("verification", "contradiction") else None
        verify_der = f"der_verify_{qid}"
        if target_id and "outcome" not in verified:
            target = self.knowledge.get_claim(target_id)
            outcome = "no_evidence"
            # a replay after a crash inside this block finds its own marker and does not repeat derivations or revisions
            already = self.db.one("SELECT 1 FROM derivations WHERE derivation_id=?", (verify_der,)) is not None or \
                self.db.one("SELECT 1 FROM revisions WHERE object_type='claim' AND object_id=? AND reason LIKE ?", (target_id, f"%({qid})%")) is not None
            if already:
                target = None
                outcome = "replayed"
            if target and target["status"] != "retracted":
                findings = [f for f in (ev.get("findings") or []) if isinstance(f, dict) and f.get("text")]
                agree = [f for f in findings if _agrees(target["text"], f["text"]) is True]
                disagree = [f for f in findings if _agrees(target["text"], f["text"]) is False]
                if agree:
                    async with self.db.tx() as c:
                        for f in agree:
                            for rid in f.get("supporting_ref_ids") or []:
                                c.execute("INSERT OR IGNORE INTO claim_evidence(claim_id, ref_id, role, weight) VALUES (?, ?, 'supports', 1.0)", (target_id, rid))
                        c.execute("INSERT OR IGNORE INTO derivations(derivation_id, tenant_id, claim_id, operator, input_claim_ids, input_ref_ids, response_ids, model, contributor_type, contributor_id, rationale, created_at) VALUES (?, ?, ?, 'verify', '[]', ?, ?, ?, 'loop', ?, ?, ?)",
                                  (verify_der, tid, target_id, j([r for f in agree for r in (f.get("supporting_ref_ids") or [])]), j([r for f in agree for r in (f.get("supporting_response_ids") or [])]),
                                   model_name, principal.id, f"independent verification via question {qid}", now_iso()))
                    fresh_refs = [r for r in self.knowledge.refs_by_ids([x for f in agree for x in (f.get("supporting_ref_ids") or [])]) if (r.get("status") or "active") == "active"]
                    if fresh_refs:
                        # current evidence confirms the claim: support whose source changed is superseded (kept for lineage)
                        await self.knowledge.supersede_changed_support(principal, target_id, reason=f"re-verified by question {qid}")
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
            elif target and target["status"] == "retracted":
                outcome = "target_retracted"
            verified = {"outcome": outcome, "target_claim_id": target_id}
            state["verified"] = verified
            await self._save_checkpoint(qid, tid, "verified", state)
        # spawn blind verification for hypotheses (bounded by depth, budget and the goal's free question slots; what does not
        # fit is picked up by a later tick as a verification gap; never for verification questions themselves)
        spawned: list[str] = list(state.get("verification_questions") or [])
        if q["kind"] not in ("verification",) and "verification_questions" not in state and self.router is not None:
            room = self._free_slots(q.get("goal_id"))
            deferred: list[str] = []
            for cid in claim_ids:
                cl = self.knowledge.get_claim(cid)
                if not cl or cl["status"] != "hypothesis":
                    continue
                if len(spawned) >= room:
                    deferred.append(cid)
                    continue
                try:
                    child = await self._ask_verification(principal, goal, cl, trigger_kind="verification", parent=q)
                    spawned.append(child["question_id"])
                except BudgetExhausted:
                    deferred.append(cid)
                except (DuplicateQuestion, CooldownActive, ValueError, ModelError) as exc:
                    logger.info("no verification question for %s: %s", cid, exc)
            state["verification_questions"] = spawned
            state["verification_deferred"] = deferred
            await self._save_checkpoint(qid, tid, "verification_spawned", state)
        await self._charge(q.get("goal_id"), started, question_id=qid)
        async with self.db.tx() as c:
            self.jobs.enqueue_sync(c, "question.commit", idempotency_key=f"question.commit:{qid}", tenant_id=tid, ref_type="question", ref_id=qid, priority=3, max_attempts=4)
        return {"claims": claim_ids, "conflicts": conflict_ids, "verification_questions": spawned, "verified": verified}

    def _decisive(self, qid: str, responses: list[dict[str, Any]], winner: Mapping[str, Any], loser: Mapping[str, Any],
                  win_findings: list[dict[str, Any]]) -> tuple[bool, str]:
        """May the investigation retract the losing record? Only when every holder behind it was asked and answered (a
        timeout or a decline is not evidence against it) and the winning side brings evidence from a source it did not
        already rest on (the holder that produced it re-asserting it is not an independent check)."""
        answered = {r["holder_id"] for r in responses}
        routes = {r["holder_id"]: r["status"] for r in self.questions.routes(qid)}
        behind_loser = {r["holder_id"] for r in self.knowledge.refs_for_claim(loser["claim_id"]) if r.get("role") == "supports"}
        silent = sorted(h for h in behind_loser if h not in answered)
        if silent:
            states = ", ".join(f"{routes.get(h, 'not routed')}" for h in silent)
            return False, f"{len(silent)} holder(s) behind the other record did not answer ({states})"
        had = {r["source_root_id"] for r in self.knowledge.refs_for_claim(winner["claim_id"]) if r.get("role") == "supports" and r.get("root_known") and r.get("source_root_id")}
        refs = self.knowledge.refs_by_ids([x for f in win_findings for x in (f.get("supporting_ref_ids") or [])])
        new_roots = {r["source_root_id"] for r in refs if r.get("root_known") and r.get("source_root_id") and (r.get("status") or "active") == "active"} - had
        if not new_roots:
            return False, "no evidence independent of the sources the winning record already rested on"
        return True, f"{len(new_roots)} new independent source root(s); every holder behind the other record answered"

    async def _notify_conflict_review(self, goal: Mapping[str, Any] | None, conflict: Mapping[str, Any], note: str) -> None:
        if goal is None or goal.get("owner_type") != "user":
            return
        async with self.db.tx() as c:
            self.org.notify_sync(c, conflict["tenant_id"], goal["owner_id"], "conflict", "A disagreement needs a decision", body=note[:300],
                                 ref_type="conflict", ref_id=conflict["conflict_id"])

    async def commit(self, job: Job) -> dict[str, Any]:
        """Commit the evaluated question into knowledge.

        Every sub-step is guarded by a checkpoint flag so a replay after a crash at any point resumes exactly where it
        stopped: discovery (idempotency key), escalation, follow-up questions (dedupe keys), proposed actions (deterministic
        outcome ids), then progress and loop bookkeeping. The question's resolved status is written last, so a replay is
        never a no-op while an effect is still missing.
        """
        q = self.questions.get(job.ref_id or "")
        if q is None or q["status"] not in ("evaluating", "verifying"):
            return {"skipped": q["status"] if q else "missing"}
        held = await self._gate_question_step(q)
        if held is not None:
            return held
        qid, tid = q["question_id"], q["tenant_id"]
        goal = self.goals.get_goal(q["goal_id"]) if q.get("goal_id") else None
        principal = self._principal_for_question(q, goal)
        started = now_iso()
        run = self._checkpoint(qid)
        state = dict(run.get("state") or {})
        claims = [c for c in self.knowledge.claims_by_ids(state.get("claim_ids") or []) if c["status"] != "retracted"]
        conflicts: list[dict[str, Any]] = []
        for cl in claims:
            for k in self.knowledge.conflicts_for_claim(cl["claim_id"]):
                if k["status"] != "resolved" and k["conflict_id"] not in {x["conflict_id"] for x in conflicts}:
                    conflicts.append(k)
        level = self.authz.unit_level(q.get("scope_unit_id"))
        unit = self.org.get_unit(q["scope_unit_id"]) if q.get("scope_unit_id") else None
        discovery_id: str | None = state.get("discovery_id")
        followups: list[str] = list(state.get("followup_ids") or [])
        max_fu = int(((goal or {}).get("budget") or {}).get("followups_per_question") or 3)
        # 1. synthesis + discovery
        if claims and not discovery_id:
            if "synthesis" not in state:
                try:
                    syn = await self._run_task("synthesize_discovery", {
                        "goal": {"title": goal["title"], "objective": goal["objective"]} if goal else None,
                        "question": {"text": q["text"], "kind": q["kind"]},
                        "findings": [{"claim_id": c["claim_id"], "text": c["text"], "status": c["status"], "confidence": c["confidence"], "support": c.get("support")} for c in claims],
                        "conflicts": [{"conflict_id": k["conflict_id"], "summary": k["summary"], "a_text": (self.knowledge.get_claim(k["claim_a_id"]) or {}).get("text", ""),
                                       "b_text": (self.knowledge.get_claim(k["claim_b_id"]) or {}).get("text", "")} for k in conflicts],
                        "level": level, "unit_name": unit["name"] if unit else "", "max_followups": max_fu}, tenant_id=tid, goal=goal, question_id=qid)
                except BudgetExhausted:
                    syn = self._fallback_synthesis(claims, conflicts)
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
        syn = state.get("synthesis") or {}
        # 2. escalation (best effort, once)
        if discovery_id and not state.get("escalation_done"):
            disc = self.knowledge.get_discovery(discovery_id)
            if syn.get("escalate") and disc and disc["status"] == "new":
                try:
                    await self.knowledge.review_discovery(self.authz.principal_for_system(tid, worker_id=self.worker_id), discovery_id, "escalated",
                                                          note=str(syn.get("escalation_reason") or "escalated by the loop"))
                except Exception as exc:  # escalation is best effort
                    logger.info("escalation skipped for %s: %s", discovery_id, exc)
            state["escalation_done"] = True
            await self._save_checkpoint(qid, tid, "escalated", state)
        # 3. follow-up questions (bounded by depth, dedupe, budget and the goal's free question slots; each routed by its own
        # job). Those that do not fit now are recorded on the question and asked by a later tick when a slot frees up.
        if discovery_id and not state.get("followups_done"):
            deferred_fu: list[dict[str, Any]] = []
            room = self._free_slots(q.get("goal_id"))
            for i, fq in enumerate((syn.get("followup_questions") or [])[:max_fu]):
                if not isinstance(fq, dict) or not fq.get("question"):
                    continue
                if goal and self.goals.budget_status(goal["goal_id"])["exhausted"]:
                    break
                fu = {"index": i, "question": fq["question"], "kind": fq.get("kind") or "gap", "rationale": fq.get("rationale", ""), "discovery_id": discovery_id,
                      "claim_ids": [c["claim_id"] for c in claims[:5]], "conflict_ids": [k["conflict_id"] for k in conflicts]}
                if len(followups) >= room:
                    deferred_fu.append(fu)
                    continue
                try:
                    child = await self._create_followup(principal, goal, q, fu, followup_of=f"{qid}:{i}")
                    if child is not None:
                        followups.append(child["question_id"])
                except DuplicateQuestion as exc:
                    # created by an earlier attempt of this very step (or an identical live question): link it, do not lose it
                    if exc.existing_id and exc.existing_id not in followups:
                        followups.append(exc.existing_id)
                except BudgetExhausted:
                    deferred_fu.append(fu)
                except (CooldownActive, ValueError, PermissionError, ModelError) as exc:
                    logger.info("follow-up skipped for %s: %s", qid, exc)
            state["followup_ids"] = followups
            state["deferred_followups"] = deferred_fu
            state["followups_done"] = True
            await self._save_checkpoint(qid, tid, "followups_created", state)
            await self.knowledge.attach_followups(discovery_id, followups)
        # 4. proposed measurable actions become goal outcomes of kind 'action', clearly labelled proposed (never progress)
        if discovery_id and goal and any(c["status"] == "supported" for c in claims) and not state.get("actions_recorded"):
            disc = self.knowledge.get_discovery(discovery_id) or {}
            try:
                acts = await self._run_task("record_outcome", {"goal": {"title": goal["title"], "objective": goal["objective"]},
                                                               "discovery": {"title": disc.get("title"), "summary": disc.get("summary"),
                                                                             "claims": [{"claim_id": c["claim_id"], "text": c["text"], "status": c["status"]} for c in claims]},
                                                               "success_criteria": goal.get("success_criteria") or []}, tenant_id=tid, goal=goal, question_id=qid)
                for i, a in enumerate((acts.get("actions") or [])[:3]):
                    if isinstance(a, dict) and a.get("text"):
                        await self.goals.add_outcome(principal, goal["goal_id"], kind="action", outcome_id=f"out_{qid}_{i}",
                                                     value={"text": a["text"], "metric": a.get("metric"), "target": a.get("target"), "proposed": True, "discovery_id": discovery_id},
                                                     claim_ids=list(a.get("evidence_claim_ids") or []))
            except (ModelError, ValueError) as exc:
                logger.info("record_outcome skipped: %s", exc)
            state["actions_recorded"] = True
            await self._save_checkpoint(qid, tid, "actions_recorded", state)
        investigated = state.get("investigated") or {}
        summary = {"claim_ids": [c["claim_id"] for c in claims], "discovery_id": discovery_id, "followup_ids": followups, "conflict_ids": [k["conflict_id"] for k in conflicts],
                   "deferred_followups": state.get("deferred_followups") or [], "verification_deferred": state.get("verification_deferred") or [],
                   "gate_notes": state.get("gate_notes") or [], "verified": state.get("verified") or {}, "verification_questions": state.get("verification_questions") or [],
                   "investigated": investigated, "synthesis_method": syn.get("method", "model") if syn else None,
                   "outcome": "investigated" if investigated else ("committed" if claims else "no_findings"),
                   "summary": syn.get("summary") or (investigated.get("note") or ("no claims passed the commit gate" if not claims else ""))}
        # 5. bookkeeping (idempotent), then the resolved status last
        if not state.get("finalized"):
            await self._charge(q.get("goal_id"), started, question_id=qid)
            if goal:
                await self.goals.recompute_progress(goal["goal_id"])
                loop = self.goals.get_loop(goal["goal_id"])
                await self.goals.set_loop_state(goal["goal_id"], loop["state"] if loop else "active", "committed a knowledge update; scheduling the next tick",
                                                worker_id=self.worker_id, stats_delta={"discoveries": 1 if discovery_id else 0, "claims": len(claims)})
                await self.goals.enqueue_tick(goal["goal_id"], reason="knowledge committed", priority=3)
            state["finalized"] = True
            await self._save_checkpoint(qid, tid, "finalized", state)
        final = "committed" if (claims or investigated) else "retained_uncertain"
        await self.questions.set_status(qid, final, result=summary, resolved=True, cooldown=True)
        await self._save_checkpoint(qid, tid, "done", state)
        return summary

    async def _create_followup(self, principal: Principal, goal: Mapping[str, Any] | None, parent: Mapping[str, Any], fu: Mapping[str, Any], *,
                               followup_of: str) -> dict[str, Any] | None:
        """One follow-up from a synthesis. Verification and contradiction follow-ups go through the blind builders (target
        id, neutral text, supporters excluded) and are skipped when their target already has a question; other kinds are
        asked as written, without inheriting the parent's blind or exclusion settings."""
        if not parent:
            return None
        kind = fu.get("kind") if fu.get("kind") in ("gap", "verification", "contradiction", "relationship", "hypothesis", "prediction") else "gap"
        extra = {"followup_of": followup_of, "discovery_id": fu.get("discovery_id")}
        gid = parent.get("goal_id")
        asked = lambda key, val: self.db.one(f"SELECT 1 FROM questions WHERE goal_id IS ? AND json_extract(trigger, '$.{key}')=? LIMIT 1", (gid, val)) is not None  # noqa: E731
        child: dict[str, Any] | None = None
        qt = _tokens(fu["question"])
        if kind == "contradiction":
            conflicts = [k for k in (self.knowledge.get_conflict(x) for x in fu.get("conflict_ids") or []) if k and k["status"] != "resolved"]
            if not conflicts:
                return None         # nothing left to investigate
            best = max(conflicts, key=lambda k: len(qt & _tokens((self.knowledge.get_claim(k["claim_a_id"]) or {}).get("text", "") + " "
                                                                  + (self.knowledge.get_claim(k["claim_b_id"]) or {}).get("text", ""))))
            if asked("conflict_id", best["conflict_id"]):
                return None
            child = await self._ask_contradiction(principal, goal, best, trigger_kind="followup", parent=parent, extra_trigger=extra)
        elif kind == "verification":
            targets = [c for c in self.knowledge.claims_by_ids(fu.get("claim_ids") or []) if c["status"] in ("hypothesis", "stale")]
            if not targets:
                return None         # every claim it could check is settled
            best = max(targets, key=lambda c: len(qt & _tokens(c["text"])))
            if asked("claim_id", best["claim_id"]):
                return None
            child = await self._ask_verification(principal, goal, best, trigger_kind="followup", parent=parent, extra_trigger=extra)
        else:
            lineage = [{"type": "claim", "id": c} for c in (fu.get("claim_ids") or [])[:5]] + [{"type": "question", "id": parent["question_id"]}]
            child = await self.questions.create(principal, {"text": fu["question"], "goal_id": gid, "scope_unit_id": parent.get("scope_unit_id"), "kind": kind,
                                                            "candidate_domains": parent.get("candidate_domains") or [], "parent_question_id": parent["question_id"],
                                                            "trigger": {"kind": "followup", "rationale": fu.get("rationale", ""), **extra}, "motivating_lineage": lineage,
                                                            "policy": _child_policy(parent.get("policy"), blind_verification=False),
                                                            "valid_from": parent.get("valid_from"), "valid_to": parent.get("valid_to")},
                                                asker_type="loop", asker_id=principal.id, estimates={"uncertainty": 0.6, "impact": 0.6, "missing_evidence": 0.6, "information_gain": 0.6},
                                                holders_estimate=2, is_demo=bool(parent.get("is_demo")))
        if child is not None and fu.get("discovery_id"):
            await self.knowledge.attach_followups(fu["discovery_id"], [child["question_id"]])
        return child

    @staticmethod
    def _fallback_synthesis(claims: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> dict[str, Any]:
        """A discovery written without a model call (the budget ran out after the claims were committed): no follow-ups,
        no escalation, and labelled as such."""
        supported = [c for c in claims if c["status"] == "supported"]
        first = (supported or claims)[0]
        parts = []
        if supported:
            parts.append("Supported: " + " ".join(c["text"] for c in supported))
        hyps = [c for c in claims if c["status"] != "supported"]
        if hyps:
            parts.append("Not yet supported: " + " ".join(c["text"] for c in hyps))
        if conflicts:
            parts.append("Disagreement: " + " ".join(k["summary"] for k in conflicts))
        return {"title": first["text"][:80], "summary": " ".join(parts), "kind": "contradiction" if conflicts else ("finding" if supported else "hypothesis"),
                "followup_questions": [], "escalate": False, "method": "deterministic_fallback",
                "note": "written without a model call because the goal's budget was exhausted"}

    async def _defer_for_budget(self, job: Job, q: Mapping[str, Any], goal: Mapping[str, Any] | None, exc: BudgetExhausted) -> dict[str, Any]:
        """The goal's budget is spent while a question is mid-flight: keep its responses, retry this step when the budget
        renews (or is raised), and show the real state on the loop."""
        delay = 900.0
        if exc.renews_at:
            renews = parse_iso(exc.renews_at)
            if renews is not None:
                delay = max(60.0, min(86400.0, (renews - utcnow()).total_seconds() + 5))
        bucket = int(utcnow().timestamp() // 600)
        await self.jobs.enqueue(job.kind, idempotency_key=f"{job.kind}:{q['question_id']}:budget:{bucket}", tenant_id=q["tenant_id"], ref_type="question",
                                ref_id=q["question_id"], payload=job.payload, priority=6, delay_seconds=delay, max_attempts=job.max_attempts)
        if goal:
            await self.goals.set_loop_state(goal["goal_id"], "budget_exhausted", f"{exc}; question {q['question_id']} waits for budget (responses kept)",
                                            worker_id=self.worker_id, next_check_at=exc.renews_at or plus_seconds(delay))
        return {"deferred": "budget_exhausted", "retry_in_seconds": round(delay)}

    # ================================================================== late responses
    async def late_response(self, job: Job) -> dict[str, Any]:
        """Integrate an answer that arrived after the question's collection closed (slow or reconnecting holder).

        Its evidence is evaluated against the question's committed claims: agreeing findings add support to the existing
        claim (and the support is recomputed from source roots, so a copy adds nothing), contradicting findings become a
        claim plus a conflict, unrelated findings become their own claim through the commit gate.
        """
        rid = (job.payload or {}).get("response_id")
        r = self.db.one("SELECT * FROM responses WHERE response_id=?", (rid,)) if rid else None
        q = self.questions.get(job.ref_id or "")
        if r is None or q is None:
            return {"skipped": "missing"}
        if q["status"] in ("routed", "collecting", "evaluating", "verifying"):
            # the regular evaluation has not finished: come back shortly (bounded by the job's attempts via a counter)
            n = int((job.payload or {}).get("n") or 0)
            if n >= 60:
                return {"skipped": "question never resolved"}
            await self.jobs.enqueue("question.late_response", idempotency_key=f"question.late_response:{rid}:{n + 1}", tenant_id=q["tenant_id"], ref_type="question",
                                    ref_id=q["question_id"], payload={"response_id": rid, "n": n + 1}, priority=5, delay_seconds=5.0)
            return {"deferred": "question still being evaluated"}
        if q["status"] in ("cancelled", "expired"):
            return {"skipped": q["status"]}
        state = self._pipeline_state(q.get("goal_id"), asker_type=q.get("asker_type") or "loop")
        if state == "stop":
            return {"skipped": "goal or loop stopped"}
        if state == "hold":
            n = int((job.payload or {}).get("n") or 0)
            if n >= 60:
                return {"skipped": "goal paused for too long; the response stays stored with the question"}
            await self.jobs.enqueue("question.late_response", idempotency_key=f"question.late_response:{rid}:{n + 1}", tenant_id=q["tenant_id"], ref_type="question",
                                    ref_id=q["question_id"], payload={"response_id": rid, "n": n + 1}, priority=6, delay_seconds=600.0)
            return {"held": "goal or loop paused"}
        qid, tid = q["question_id"], q["tenant_id"]
        goal = self.goals.get_goal(q["goal_id"]) if q.get("goal_id") else None
        principal = self._principal_for_question(q, goal)
        holder = self.org.get_holder(r["holder_id"])
        if holder is None or not self.authz.can_route(q, holder)[0]:
            return {"skipped": "holder no longer authorized for this question"}
        refs = self.knowledge.refs_by_ids(jl(r["evidence_ref_ids"], []))
        existing = [c for c in self.knowledge.list_claims(self.authz.principal_for_system(tid, worker_id=self.worker_id), question_id=qid)]
        try:
            ev = await self._run_task("evaluate_responses", {"question": {"question_id": qid, "text": q["text"], "kind": q["kind"]},
                                                             "responses": [{"response_id": r["response_id"], "holder_id": r["holder_id"], "content": r["content"], "confidence": r["confidence"],
                                                                            "refs": [{"ref_id": x["ref_id"], "root_id": x.get("source_root_id"), "root_known": bool(x.get("root_known")),
                                                                                      "observed_at": x.get("observed_at")} for x in refs]}],
                                                             "existing_claims": [{"claim_id": c["claim_id"], "text": c["text"], "status": c["status"]} for c in existing],
                                                             "today": now_iso()[:10]}, tenant_id=tid, goal=goal, question_id=qid)
        except BudgetExhausted as exc:
            return await self._defer_for_budget(job, q, goal, exc)
        base = {"tenant_id": tid, "scope_unit_id": q.get("scope_unit_id"), "visibility": (q.get("policy") or {}).get("visibility", "unit"), "goal_id": q.get("goal_id"),
                "question_id": qid, "created_by_type": "loop", "created_by_id": principal.id, "valid_from": q.get("valid_from"), "valid_to": q.get("valid_to"),
                "is_demo": bool(q.get("is_demo"))}
        attached, new_claims = [], []
        for i, f in enumerate(ev.get("findings") or []):
            if not isinstance(f, dict) or not f.get("text"):
                continue
            ref_ids = list(dict.fromkeys(f.get("supporting_ref_ids") or [x["ref_id"] for x in refs]))
            agree = next((c for c in existing if _agrees(c["text"], f["text"]) is True), None)
            disagree = next((c for c in existing if _agrees(c["text"], f["text"]) is False), None)
            if agree is not None:
                async with self.db.tx() as c:
                    for ref in ref_ids:
                        c.execute("INSERT OR IGNORE INTO claim_evidence(claim_id, ref_id, role, weight) VALUES (?, ?, 'supports', 1.0)", (agree["claim_id"], ref))
                    c.execute("INSERT OR IGNORE INTO derivations(derivation_id, tenant_id, claim_id, operator, input_claim_ids, input_ref_ids, response_ids, model, contributor_type, contributor_id, rationale, created_at) "
                              "VALUES (?, ?, ?, 'late_response', '[]', ?, ?, NULL, 'loop', ?, ?, ?)",
                              (f"der_late_{rid}_{agree['claim_id']}", tid, agree["claim_id"], j(ref_ids), j([rid]), principal.id, f"late response {rid} agrees", now_iso()))
                await self.knowledge.recompute_status(principal, agree["claim_id"], reason=f"late response {rid} added support")
                attached.append(agree["claim_id"])
            else:
                claim, _gate = await self.knowledge.commit_claim(principal, {**base, "text": f["text"], "kind": "finding", "confidence": float(f.get("confidence", 0.5))},
                                                                 evidence=[{"ref_id": x, "role": "supports"} for x in ref_ids],
                                                                 derivation={"operator": "late_response", "response_ids": [rid], "contributor_type": "loop", "contributor_id": principal.id,
                                                                             "rationale": "answer received after collection closed"},
                                                                 question=q, idempotency_key=f"claim:late:{rid}:{i}",
                                                                 conflict_with=[disagree["claim_id"]] if disagree is not None else [],
                                                                 conflict_summary=f"late response disagrees: {f['text'][:120]}")
                if claim:
                    new_claims.append(claim["claim_id"])
        disc_id = (q.get("result") or {}).get("discovery_id")
        if disc_id and new_claims:
            await self.knowledge.add_claims_to_discovery(principal, disc_id, new_claims, reason=f"late response {rid}")
        await self.db.audit(tid, "worker", self.worker_id, "question.late_response", resource_type="question", resource_id=qid,
                            detail={"response_id": rid, "attached_to": attached, "new_claims": new_claims})
        if goal:
            await self.goals.enqueue_tick(goal["goal_id"], reason="late response integrated", priority=4)
        return {"attached_to": attached, "new_claims": new_claims}

    # ================================================================== evidence changes
    async def apply_evidence_change(self, holder: Mapping[str, Any], event: str, affected_ref_ids: Iterable[str], new_root: str | None = None, *,
                                    doc_id: str | None = None, reason: str = "") -> dict[str, Any]:
        """One path for revisions and retractions, whether they arrive from a holder process over the transport or from an
        embedded holder through the API: mark references, stale or retract dependent claims, queue re-verification (keyed
        by the references that changed, so a redelivered event schedules nothing new), wake the goals."""
        tid = holder["tenant_id"]
        p = self.authz.principal_for_system(tid, worker_id=self.worker_id)
        out = await self.knowledge.on_evidence_event(p, tid, holder["holder_id"], event, list(affected_ref_ids), new_source_root_id=new_root, reason=reason)
        changed = out.get("changed_ref_ids") or []
        goals_touched: set[str] = set()
        if changed:
            key_refs = hashlib.sha256(",".join(sorted(changed)).encode()).hexdigest()[:16]
            for cid in out["affected_claim_ids"]:
                cl = self.knowledge.get_claim(cid)
                if cl and cl.get("goal_id"):
                    goals_touched.add(cl["goal_id"])
                if cl and cl["status"] == "stale":
                    await self.jobs.enqueue("claim.reverify", idempotency_key=f"claim.reverify:{cid}:{key_refs}", tenant_id=tid, ref_type="claim", ref_id=cid,
                                            payload={"claim_id": cid, "ref_ids": changed}, priority=4)
            for gid in goals_touched:
                await self.goals.enqueue_tick(gid, reason="evidence changed", priority=3)
            aud = {"user_ids": [holder["owner_id"]] if holder["owner_type"] == "user" else [], "unit_ids": [holder["owner_id"]] if holder["owner_type"] == "unit" else []}
            await self.db.emit(tid, {"revised": "document.revised", "retracted": "document.retracted", "deleted": "document.retracted"}.get(event, "holder.status"),
                               ref_type="holder", ref_id=holder["holder_id"],
                               payload={"holder_id": holder["holder_id"], "affected_claims": out["affected_claim_ids"], "doc_id": doc_id}, audience=aud)
        return out

    async def reverify(self, job: Job) -> dict[str, Any]:
        cid = (job.payload or {}).get("claim_id") or job.ref_id
        claim = self.knowledge.get_claim(cid or "")
        if claim is None or claim["status"] not in ("stale", "contested"):
            return {"skipped": claim["status"] if claim else "missing"}
        tid = claim["tenant_id"]
        goal = self.goals.get_goal(claim["goal_id"]) if claim.get("goal_id") else None
        if goal and (self._pipeline_state(goal["goal_id"]) != "run" or self.goals.budget_status(goal["goal_id"])["exhausted"]):
            # the next tick finds the claim as a verification gap once the loop runs again
            return {"skipped": "goal not running or budget exhausted"}
        principal = self.authz.principal_for_loop(tid, goal) if goal else self.authz.principal_for_system(tid, worker_id=self.worker_id)
        try:
            # the source changed: its own holders are asked again (they hold the new version), still without the claim's text
            q = await self._ask_verification(principal, goal, claim, trigger_kind="evidence_revised" if claim["status"] == "stale" else "contradiction",
                                             exclude_supporters=False, force=True)
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
        """Everything holders send back: responses, ingest results, evidence events. Idempotent per msg_id.

        A malformed envelope (bad types, unknown ids) is dropped with an audit row so it cannot block the durable
        consumer forever; transient failures (database busy, model outage) propagate so the transport redelivers.
        """
        try:
            await self._on_transport(env)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("dropping malformed %s envelope %s: %s", env.kind, env.msg_id, exc)
            await self.db.audit(env.tenant_id, "holder", (env.payload or {}).get("holder_id"), f"transport.{env.kind}", outcome="error",
                                detail={"msg_id": env.msg_id, "error": f"{type(exc).__name__}: {exc}"[:300], "dropped": True})

    async def _on_transport(self, env: Envelope) -> None:
        kind = env.kind
        payload = env.payload or {}
        hid = payload.get("holder_id")
        holder = self.org.get_holder(hid) if hid else None
        if holder is None or holder.get("tenant_id") != env.tenant_id:
            await self.db.audit(env.tenant_id, "holder", hid, f"transport.{kind}", outcome="deny", detail={"reason": "unknown holder", "msg_id": env.msg_id})
            return
        if not env.signature or not env.verify(self.org.route_key(hid)):
            await self.db.audit(env.tenant_id, "holder", hid, f"transport.{kind}", outcome="deny", detail={"reason": "bad signature", "msg_id": env.msg_id})
            return
        if kind == "response":
            await self.questions.handle_response(payload, msg_id=env.msg_id, holder_id=hid)
        elif kind == "evidence_event":
            if payload.get("event") in ("revised", "retracted", "deleted", "unavailable", "restored"):
                await self.apply_evidence_change(holder, payload["event"], payload.get("affected_ref_ids") or [], payload.get("new_source_root_id"),
                                                 doc_id=payload.get("doc_id"), reason=payload.get("reason", ""))
        elif kind == "ingest_result":
            batch = payload.get("batch") if isinstance(payload.get("batch"), dict) else {}
            await self.db.emit(env.tenant_id, "document.ingested", ref_type="holder", ref_id=hid, payload={"holder_id": hid,
                               "domains": payload.get("domains") or (payload.get("document") or {}).get("domains") or [],
                               "doc_id": payload.get("doc_id") or (payload.get("document") or {}).get("doc_id"), "records": int(batch.get("records") or 1)},
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
                    "question.commit": self.commit, "question.late_response": self.late_response, "claim.reverify": self.reverify, "goal.progress": self.goal_progress,
                    "maintenance": self.maintenance}
        h = handlers.get(job.kind) or self.hooks.get(job.kind)
        if h is None:
            raise ValueError(f"no handler for job kind {job.kind}")
        return await h(job)

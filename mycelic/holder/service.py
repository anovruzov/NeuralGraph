"""``HolderService``: the transport consumer that lets one evidence holder take part in the organization.

It subscribes to the holder's four subjects (inbox, ingest, raw, control) and turns signed envelopes from the
coordinator into calls on the :class:`~mycelic.evidence.EvidenceStore`, then publishes the outcome back on the
tenant-wide subjects. Three properties matter more than the dispatch itself:

* **Authenticity** — an inbound envelope is acted on only when its HMAC verifies with the holder's ``route_key``
  and its tenant matches. Anything else is dropped (acknowledged, logged, audited in the holder's own store): a bad
  signature never becomes valid on redelivery, so retrying would only poison the queue. Every envelope the holder
  publishes (``response``, ``ingest_result``, ``evidence_event``, ``heartbeat``) is signed with the same key and
  carries ``holder_id`` in its payload; the core verifies and drops the rest.
* **Idempotency** — every effect is committed together with a ``processed_messages`` row for the envelope's
  ``msg_id`` (see ``MycelicMemoryStore.run_in_tx``). A redelivered envelope finds the row and re-publishes the stored
  outcome without repeating the effect; outbound ``msg_id``s are deterministic (``resp:<question>:<holder>``,
  ``ingest:<doc>`` ...) so the core's transport de-duplicates the re-publish as well.
* **Liveness** — a ``heartbeat`` envelope goes out on ``responses(tenant)`` every ``heartbeat_interval`` seconds
  (``msg_id = hb:<holder>:<minute bucket>``), so a holder reachable only through NATS still shows online; the
  optional callback (HTTP heartbeat, or ``OrgService.holder_heartbeat`` in embedded mode) runs alongside.

Envelope kinds handled and what they produce:

============  ============================================  =========================================================
kind          payload                                       outcome
============  ============================================  =========================================================
question      QuestionArtifact (+ ``route_id``)             ``response`` on ``responses(tenant)``: ResponseArtifact
ingest        {title, text, kind?, observed_at?, ...}       ``ingest_result`` on ``ingest_results(tenant)``
revise        {doc_id, text, title?, observed_at?, reason}  ``evidence_event`` {event: revised, affected_ref_ids, ...}
retract       {doc_id, reason}                              ``evidence_event`` {event: retracted, affected_ref_ids, ...}
raw_request   {ref_id, grant_token}                         ``raw_reply`` on the envelope's reply subject
control       {action: stats | reload_policy | manual_response, ...}
                                                            ``control_reply`` on the reply subject; ``manual_response``
                                                            also publishes a ``response`` (``resp:<q>:<holder>:human:<hash>``)
============  ============================================  =========================================================
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Iterable

from ..evidence.service import EvidenceStore
from ..transport.base import Envelope, Subjects, Subscription, Transport
from ..util import canonical_json, now_iso, sha256

logger = logging.getLogger(__name__)

HeartbeatCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
PolicyLoader = Callable[[], dict[str, Any] | None | Awaitable[dict[str, Any] | None]]

_MALFORMED = (KeyError, ValueError, TypeError)


def manual_response_msg_id(question_id: str, holder_id: str, doc_ids: Iterable[str], content: str) -> str:
    """``resp:<question>:<holder>:human:<hash>``: deterministic per submission (documents *and* text), so a redelivered
    control envelope de-duplicates at the core while a corrected answer for the same documents is not lost."""
    digest = sha256(canonical_json({"doc_ids": sorted(str(d) for d in doc_ids), "content": content}))[:16]
    return f"resp:{question_id}:{holder_id}:human:{digest}"


def heartbeat_msg_id(holder_id: str, at: float | None = None) -> str:
    return f"hb:{holder_id}:{int((at if at is not None else time.time()) // 60)}"


class HolderService:
    """See module docstring.

    ``heartbeat`` is called every ``heartbeat_interval`` seconds with the store's stats (the standalone process posts
    them to the core API; embedded mode calls ``OrgService.holder_heartbeat``); the transport heartbeat is published
    in the same beat unless ``transport_heartbeat=False``. ``policy_loader`` is consulted before every question so
    the export policy the organization holds is the one applied.
    """

    def __init__(self, store: EvidenceStore, transport: Transport, *, holder_id: str, tenant_id: str, route_key: str,
                 heartbeat: HeartbeatCallback | None = None, heartbeat_interval: float = 20.0,
                 policy_loader: PolicyLoader | None = None, transport_heartbeat: bool = True) -> None:
        self.store = store
        self.transport = transport
        self.holder_id = holder_id
        self.tenant_id = tenant_id
        self.route_key = route_key
        self.heartbeat = heartbeat
        self.heartbeat_interval = float(heartbeat_interval)
        self.policy_loader = policy_loader
        self.transport_heartbeat = bool(transport_heartbeat)
        self.consumer = f"holder-{holder_id}"
        self._subs: list[Subscription] = []
        self._hb_task: asyncio.Task | None = None
        self.running = False
        self.started_at: str | None = None
        self.counters = {"handled": 0, "rejected": 0, "replayed": 0, "published": 0, "deduplicated": 0, "errors": 0, "heartbeats": 0}

    # ------------------------------------------------------------------ lifecycle
    @property
    def subjects(self) -> list[str]:
        t, h = self.tenant_id, self.holder_id
        return [Subjects.holder_inbox(t, h), Subjects.holder_ingest(t, h), Subjects.holder_raw(t, h), Subjects.holder_control(t, h)]

    async def start(self) -> None:
        if self.running:
            return
        for subject in self.subjects:
            self._subs.append(await self.transport.subscribe(subject, consumer=self.consumer, handler=self.handle))
        self.running = True
        self.started_at = now_iso()
        await self._safe_heartbeat()          # announce presence now; the loop continues after the first interval
        self._hb_task = asyncio.create_task(self._heartbeat_loop(), name=f"heartbeat-{self.holder_id}")
        logger.info("holder %s listening on %d subjects (consumer %s)", self.holder_id, len(self._subs), self.consumer)

    async def stop(self) -> None:
        self.running = False
        if self._hb_task is not None:
            self._hb_task.cancel()
            try:
                await self._hb_task
            except (asyncio.CancelledError, Exception):
                pass
            self._hb_task = None
        for sub in self._subs:
            await sub.close()
        self._subs = []

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            await self._safe_heartbeat()

    async def _safe_heartbeat(self) -> None:
        try:
            await self.heartbeat_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("heartbeat failed for holder %s", self.holder_id)

    async def heartbeat_once(self) -> dict[str, Any]:
        """One beat: the signed ``heartbeat`` envelope (de-duplicated per minute by its msg_id) and the callback."""
        stats = await self.stats()
        if self.transport_heartbeat:
            try:
                if await self._publish(Subjects.responses(self.tenant_id), "heartbeat", {"holder_id": self.holder_id, "stats": stats},
                                       msg_id=heartbeat_msg_id(self.holder_id), counter="heartbeats"):
                    logger.debug("holder %s heartbeat published", self.holder_id)
            except Exception:
                logger.exception("transport heartbeat failed for holder %s", self.holder_id)
        if self.heartbeat is not None:
            r = self.heartbeat(stats)
            if inspect.isawaitable(r):
                await r
        return stats

    async def stats(self) -> dict[str, Any]:
        stats = await self.store.stats()
        stats.update({"service": dict(self.counters), "started_at": self.started_at, "running": self.running, "at": now_iso()})
        return stats

    # ------------------------------------------------------------------ inbound
    async def handle(self, env: Envelope) -> dict[str, Any] | None:
        """Transport handler. Returns the outcome (``{"op", "result"}``) or ``None`` when the envelope was dropped.
        Raises only for transient failures, so the transport redelivers; malformed requests are answered with an
        error outcome and never retried."""
        if env.tenant_id != self.tenant_id:
            await self._drop(env, "tenant mismatch")
            return None
        if not env.verify(self.route_key):
            await self._drop(env, "bad signature")
            return None
        prior = await self.store.store.processed_outcome(env.msg_id)
        if prior is not None and prior.get("op"):
            self.counters["replayed"] += 1
            logger.info("holder %s replaying stored outcome for %s (%s)", self.holder_id, env.msg_id, env.kind)
            await self._emit(env, prior)
            return prior
        try:
            outcome = await self._dispatch(env)
        except _MALFORMED as exc:
            self.counters["errors"] += 1
            logger.warning("holder %s: malformed %s envelope %s: %s", self.holder_id, env.kind, env.msg_id, exc)
            outcome = {"op": env.kind, "result": None, "error": str(exc) or exc.__class__.__name__}
            await self.store.store.mark_processed(env.msg_id, outcome)
        self.counters["handled"] += 1
        await self._emit(env, outcome)
        return outcome

    async def _drop(self, env: Envelope, reason: str) -> None:
        self.counters["rejected"] += 1
        logger.warning("holder %s dropped %s envelope %s on %s: %s", self.holder_id, env.kind, env.msg_id, env.subject, reason)
        try:
            await self.store.store.log_event("envelope.rejected", env.msg_id, {"kind": env.kind, "subject": env.subject, "reason": reason})
        except Exception:
            logger.exception("could not audit the rejected envelope")

    async def _refresh_policy(self) -> None:
        if self.policy_loader is None:
            return
        try:
            pol = self.policy_loader()
            if inspect.isawaitable(pol):
                pol = await pol
        except Exception:
            logger.exception("policy loader failed for holder %s; keeping the current policy", self.holder_id)
            return
        if pol:
            self.store.update_policy(pol.get("export_policy"), pol.get("domains"))

    async def _dispatch(self, env: Envelope) -> dict[str, Any]:
        p = env.payload or {}
        kind = env.kind
        if kind == "question":
            await self._refresh_policy()
            question = dict(p)
            question.setdefault("tenant_id", env.tenant_id)
            result = await self.store.answer_question(question, idempotency_key=env.msg_id)
            return {"op": "question", "result": result}
        if kind == "ingest":
            result = await self.store.ingest_document(
                str(p.get("title") or ""), str(p.get("text") or ""), kind=str(p.get("kind") or "note"), observed_at=p.get("observed_at"),
                domains=p.get("domains"), origin_id=p.get("origin_id"), uploaded_by=p.get("uploaded_by"), doc_id=p.get("doc_id"),
                idempotency_key=env.msg_id)
            return {"op": "ingest", "result": result}
        if kind == "revise":
            result = await self.store.revise_document(str(p["doc_id"]), str(p.get("text") or ""), title=p.get("title"),
                                                      observed_at=p.get("observed_at"), reason=str(p.get("reason") or ""),
                                                      domains=p.get("domains"), idempotency_key=env.msg_id)
            return {"op": "revise", "result": result}
        if kind == "retract":
            result = await self.store.retract_document(str(p["doc_id"]), str(p.get("reason") or ""), idempotency_key=env.msg_id)
            return {"op": "retract", "result": result}
        if kind == "raw_request":
            ref_id = str(p.get("ref_id") or "")
            raw = await self.store.raw_for_ref(ref_id) if ref_id else None
            outcome = {"op": "raw_request", "result": raw} if raw else {"op": "raw_request", "result": None, "error": "unknown ref_id"}
            await self.store.store.log_event("raw.request", ref_id, {"granted": raw is not None, "grant_token": bool(p.get("grant_token")),
                                                                     "msg_id": env.msg_id})
            await self.store.store.mark_processed(env.msg_id, outcome)
            return outcome
        if kind == "control":
            action = str(p.get("action") or "")
            if action == "manual_response":
                await self._refresh_policy()
                question = dict(p.get("question") or {})
                if not question.get("question_id"):
                    raise ValueError("manual_response needs question.question_id")
                question.setdefault("tenant_id", env.tenant_id)
                result = await self.store.manual_response(question, str(p.get("content") or ""), list(p.get("doc_ids") or []),
                                                          idempotency_key=env.msg_id)
                return {"op": "manual_response", "result": result, "doc_ids": [str(d) for d in (p.get("doc_ids") or [])],
                        "content": str(p.get("content") or "")}
            outcome = {"op": "control", "result": await self._control(action, p)}
            await self.store.store.mark_processed(env.msg_id, outcome)
            return outcome
        outcome = {"op": kind, "result": None, "error": f"unsupported envelope kind {kind!r}"}
        await self.store.store.mark_processed(env.msg_id, outcome)
        return outcome

    async def _control(self, action: str, p: dict[str, Any]) -> dict[str, Any]:
        if action == "stats":
            return {"action": action, "stats": await self.stats()}
        if action == "reload_policy":
            self.store.update_policy(p.get("export_policy"), p.get("domains"))
            return {"action": action, "export_policy": dict(self.store.export_policy), "domains": list(self.store.domains)}
        raise ValueError(f"unknown control action {action!r}")

    # ------------------------------------------------------------------ outbound
    async def _emit(self, env: Envelope, outcome: dict[str, Any]) -> None:
        op, result, error = outcome.get("op"), outcome.get("result"), outcome.get("error")
        t, h = self.tenant_id, self.holder_id
        if op == "question":
            qid = (result or {}).get("question_id") or (env.payload or {}).get("question_id") or env.msg_id
            payload = result or {"question_id": qid, "holder_id": h, "status": "error", "content": "", "confidence": 0.0, "evidence_refs": [],
                                 "provenance": {}, "freshness_at": None, "reason": error, "route_id": (env.payload or {}).get("route_id")}
            await self._publish(Subjects.responses(t), "response", payload, msg_id=f"resp:{qid}:{h}")
        elif op == "manual_response":
            if result:
                # the stored outcome (replay) carries doc_ids/content only when the effect ran; the stored result is enough
                # for the id because manual_response mirrors them into the response
                doc_ids = outcome.get("doc_ids")
                content = outcome.get("content")
                if doc_ids is None or content is None:
                    doc_ids = [str(d) for d in ((env.payload or {}).get("doc_ids") or [])]
                    content = str((env.payload or {}).get("content") or "")
                msg_id = manual_response_msg_id(result["question_id"], h, doc_ids, content)
                await self._publish(Subjects.responses(t), "response", result, msg_id=msg_id)
            if env.reply_to:
                await self.transport.reply(env, result or {"error": error}, kind="control_reply")
        elif op == "ingest":
            payload = {"holder_id": h, "document": result, "error": error, "request_msg_id": env.msg_id}
            msg_id = f"ingest:{result['doc_id']}" if result else f"ingest:error:{env.msg_id}"
            await self._publish(Subjects.ingest_results(t), "ingest_result", payload, msg_id=msg_id)
        elif op in ("revise", "retract"):
            r = result or {}
            payload = {"event": ("revised" if op == "revise" else "retracted") if result else "error", "holder_id": h,
                       "doc_id": (r.get("document") or {}).get("doc_id") or (env.payload or {}).get("doc_id"),
                       "affected_ref_ids": list(r.get("affected_ref_ids") or []), "reason": r.get("reason") or error,
                       "version": (r.get("document") or {}).get("version"), "document": r.get("document"),
                       "request_msg_id": env.msg_id}
            if op == "revise":
                payload["new_source_root_id"] = r.get("new_source_root_id")
                payload["previous_source_root_id"] = r.get("previous_source_root_id")
            await self._publish(Subjects.evidence_events(t), "evidence_event", payload, msg_id=f"evidence:{env.msg_id}")
        elif op == "raw_request":
            await self.transport.reply(env, result or {"error": error or "unknown ref_id", "ref_id": (env.payload or {}).get("ref_id")}, kind="raw_reply")
        elif op == "control":
            if env.reply_to:
                await self.transport.reply(env, result or {"error": error}, kind="control_reply")
        elif error and env.reply_to:
            await self.transport.reply(env, {"error": error}, kind="error")

    async def _publish(self, subject: str, kind: str, payload: dict[str, Any], *, msg_id: str, counter: str = "published") -> bool:
        """Every outbound envelope is signed with the route key and names the holder in its payload."""
        payload = {**payload, "holder_id": self.holder_id}
        env = Envelope.new(subject, kind, self.tenant_id, payload, msg_id=msg_id, headers={"holder_id": self.holder_id}).sign(self.route_key)
        ok = await self.transport.publish(env)
        if ok:
            self.counters[counter] += 1
        elif counter == "published":
            self.counters["deduplicated"] += 1
        return ok

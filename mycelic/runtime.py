"""``Runtime``: every service wired together from :class:`mycelic.config.Settings`.

The API, the worker process, the holder process, the seed command and the tests all build the same object so
there is exactly one composition root. Attribute names are the contract the API handlers use
(``request.app["rt"].questions`` ...).
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents import AgentService
from .auth import AuthService
from .authz import Authorizer
from .config import Settings, load_settings
from .db import CoordDB
from .discovery import DiscoveryWorker, LoopEngine
from .goals import GoalService
from .inquiry import QuestionService
from .jobs import JobQueue
from .knowledge import KnowledgeService
from .org import OrgService

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    db: CoordDB
    org: OrgService
    auth: AuthService
    authz: Authorizer
    jobs: JobQueue
    knowledge: KnowledgeService
    goals: GoalService
    questions: QuestionService
    engine: LoopEngine
    agents: AgentService
    transport: Any = None            # mycelic.transport.Transport
    router: Any = None               # mycelic.models.base.ModelRouter
    embedder: Any = None             # mycelic.models.base.EmbeddingProvider
    holders: Any = None              # mycelic.holder.embedded.EmbeddedHolders (when embedded holders are enabled)
    worker: DiscoveryWorker | None = None
    ledger: Any = None               # mycelic.models.ledger.SqliteUsageLedger
    started_at: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    async def start(self, *, run_worker: bool | None = None, run_holders: bool | None = None) -> None:
        from .util import now_iso
        self.started_at = now_iso()
        if self.transport is not None:
            await self.transport.start()
        if (run_holders if run_holders is not None else self.settings.embedded_holders) and self.holders is not None:
            await self.holders.start()
        if run_worker if run_worker is not None else self.settings.run_worker_in_api:
            if self.worker is None:
                self.worker = build_worker(self)
            await self.worker.start()

    async def stop(self) -> None:
        if self.worker is not None:
            await self.worker.stop()
        if self.holders is not None:
            try:
                await self.holders.stop()
            except Exception as exc:  # pragma: no cover
                logger.warning("holders stop: %s", exc)
        if self.transport is not None:
            await self.transport.close()
        if self.router is not None:
            try:
                await self.router.close()
            except Exception:  # pragma: no cover
                pass
        await self.db.close()

    # ------------------------------------------------------------------ holder access helpers used by the API and the agents
    async def memory_search(self, principal, holder_id: str, query: str, k: int = 8) -> list[dict[str, Any]]:
        """Search a holder's memory on behalf of a principal (authorization is the caller's job; embedded holders only)."""
        if self.holders is None:
            return []
        store = self.holders.get(holder_id) if hasattr(self.holders, "get") else None
        if store is None:
            return []
        return await store.search(query, k=k)


def ensure_secret_key(settings: Settings) -> str:
    """Persist a generated secret in the data dir when none is configured (development convenience)."""
    if settings.secret_key:
        return settings.secret_key
    p = Path(settings.data_dir) / "secret_key"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        settings.secret_key = p.read_text().strip()
    else:
        settings.secret_key = secrets.token_urlsafe(32)
        p.write_text(settings.secret_key)
        try:
            p.chmod(0o600)
        except OSError:  # pragma: no cover
            pass
    return settings.secret_key


def build_worker(rt: Runtime, *, worker_id: str | None = None, role: str = "discovery") -> DiscoveryWorker:
    s = rt.settings
    return DiscoveryWorker(rt.db, rt.engine, rt.jobs, rt.goals, rt.transport, concurrency=s.worker_concurrency, lease_seconds=s.worker_lease_seconds,
                           heartbeat_seconds=s.worker_heartbeat_seconds, poll_seconds=s.worker_poll_seconds, worker_id=worker_id, role=role)


def build_runtime(settings: Settings | None = None, *, with_transport: bool = True, with_models: bool = True, with_holders: bool | None = None) -> Runtime:
    """Compose everything. Optional pieces (transport, models, embedded holders) are imported lazily so a partial
    checkout or a missing optional dependency degrades to a clear error at the point of use rather than at import."""
    s = settings or load_settings()
    ensure_secret_key(s)
    db = CoordDB(s.coord_db)
    org = OrgService(db)
    auth = AuthService(db, org, session_ttl_seconds=s.session_ttl_seconds)
    authz = Authorizer(db, org)
    jobs = JobQueue(db)
    knowledge = KnowledgeService(db, org, authz)
    loop_defaults = {"check_interval_seconds": s.loop_check_interval_seconds, "max_concurrent_questions": s.loop_max_concurrent_questions,
                     "cooldown_seconds": s.loop_cooldown_seconds, "max_followup_depth": s.loop_max_followup_depth, "question_timeout_seconds": s.loop_question_timeout_seconds}
    goals = GoalService(db, org, authz, jobs, loop_defaults=loop_defaults, heartbeat_seconds=s.worker_heartbeat_seconds)
    transport = router = embedder = ledger = holders = None
    if with_transport:
        from .transport import build_transport
        transport = build_transport(s, db)
    if with_models:
        from .models.base import build_embedder, build_router
        router = build_router(s, db)
        embedder = build_embedder(s)
        ledger = getattr(router, "ledger", None)
    questions = QuestionService(db, org, authz, jobs, goals, knowledge, transport, question_timeout_seconds=s.loop_question_timeout_seconds,
                                cooldown_seconds=s.loop_cooldown_seconds, max_followup_depth=s.loop_max_followup_depth)
    engine = LoopEngine(db, org, authz, jobs, goals, knowledge, questions, router, transport, loop_defaults=loop_defaults)
    rt = Runtime(settings=s, db=db, org=org, auth=auth, authz=authz, jobs=jobs, knowledge=knowledge, goals=goals, questions=questions, engine=engine,
                 agents=None, transport=transport, router=router, embedder=embedder, ledger=ledger)  # type: ignore[arg-type]
    rt.agents = AgentService(db, org, authz, knowledge, router, memory_search=rt.memory_search)
    use_holders = s.embedded_holders if with_holders is None else with_holders
    if use_holders and transport is not None:
        try:
            from .holder.embedded import EmbeddedHolders
            rt.holders = EmbeddedHolders(s, db, org, transport, router=router, embedder=embedder)
        except Exception as exc:  # the holder package may not be present yet in a partial build
            logger.warning("embedded holders unavailable: %s", exc)
    return rt

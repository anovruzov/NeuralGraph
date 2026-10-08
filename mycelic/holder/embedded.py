"""``EmbeddedHolders``: run every ``mode='embedded'`` holder of the coordination DB inside the API process.

Single-node deployments (and the demonstration) have no separate holder processes. Each embedded holder still owns
its own SQLite file under ``<holders_dir>/<holder_id>/evidence.db`` and is driven by the same transport consumer as a
standalone holder, so the code path the coordinator exercises is identical; only the heartbeat goes straight to
``OrgService.holder_heartbeat`` instead of over HTTP, and the export policy is re-read from the org registry before
each question (which is how a ``PATCH /holders/{id}`` takes effect without a restart).

The API uses :meth:`get` to reach a holder's store directly for the owner's memory search and uploads.
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any, Callable

from ..evidence.service import EmbeddingAdapter, EvidenceStore
from ..util import jl
from .service import HolderService

logger = logging.getLogger(__name__)


class _SharedEmbedding(EmbeddingAdapter):
    """An adapter over a provider several holders share: closing one holder's store must not close the provider."""

    async def close(self) -> None:
        return None


class EmbeddedHolders:
    """``settings`` only needs ``holders_dir``; ``db`` is the ``CoordDB`` (kept for callers that share one handle);
    ``org`` an ``OrgService``; ``transport`` a started ``Transport``; ``llm_factory`` a zero-argument callable returning
    an embedding client per holder, or ``embedder`` one shared Mycelic ``EmbeddingProvider`` (``embed(list[str])``,
    what ``build_embedder(settings)`` returns) that is adapted to the NeuralGraph shape; neither -> the deterministic
    hash embedder; ``router`` a ``ModelRouter`` or ``None``."""

    def __init__(self, settings: Any, db: Any, org: Any, transport: Any, llm_factory: Callable[[], Any] | None = None,
                 router: Any | None = None, *, embedder: Any | None = None, heartbeat_interval: float = 20.0,
                 extract: bool = False) -> None:
        self.settings = settings
        self.db = db
        self.org = org
        self.transport = transport
        self.llm_factory = llm_factory
        self.embedder = embedder
        self._tenant_llms: dict[str, Any] = {}
        self.router = router
        self.heartbeat_interval = float(heartbeat_interval)
        self.extract = bool(extract)
        self.holders_dir = Path(getattr(settings, "holders_dir", "") or Path(getattr(settings, "data_dir", ".")) / "holders").expanduser()
        self._services: dict[str, HolderService] = {}
        self._stores: dict[str, EvidenceStore] = {}
        self.running = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.running = True
        for tenant in self.org.list_tenants():
            for holder in self.org.list_holders(tenant["tenant_id"]):
                if holder.get("mode") == "embedded" and holder.get("status") != "revoked":
                    try:
                        await self.ensure(holder["holder_id"])
                    except Exception:
                        logger.exception("could not start embedded holder %s", holder["holder_id"])
        logger.info("embedded holders running: %d", len(self._services))

    async def stop(self) -> None:
        self.running = False
        for holder_id, svc in list(self._services.items()):
            try:
                await svc.stop()
                await self.org.holder_heartbeat(holder_id, stats=await svc.store.stats(), status="offline")
            except Exception:
                logger.exception("error stopping embedded holder %s", holder_id)
        for store in self._stores.values():
            try:
                await store.close()
            except Exception:
                logger.exception("error closing an embedded holder store")
        self._services.clear()
        self._stores.clear()

    # ------------------------------------------------------------------ holders
    def store_path(self, holder_id: str) -> Path:
        return self.holders_dir / holder_id / "evidence.db"

    async def ensure(self, holder_id: str) -> HolderService:
        """Open (or create) the holder's store and start its service; idempotent."""
        if holder_id in self._services:
            return self._services[holder_id]
        row = self.org.holder_secret_row(holder_id)
        if row is None:
            raise KeyError(holder_id)
        if row["mode"] != "embedded":
            raise ValueError(f"holder {holder_id} is not embedded (mode={row['mode']!r})")
        if row["status"] == "revoked":
            raise ValueError(f"holder {holder_id} is revoked")
        tenant_id = row["tenant_id"]
        llm = None
        if self.llm_factory is not None:
            llm = self.llm_factory()
            if inspect.isawaitable(llm):
                llm = await llm
        elif self.embedder is not None:
            if hasattr(self.embedder, "embed_many"):
                llm = self.embedder
            else:
                # one shared provider, metered per tenant so every paid embedding call reaches the usage ledger
                if tenant_id not in self._tenant_llms:
                    provider = self.embedder
                    ledger = getattr(self.router, "ledger", None)
                    if ledger is not None:
                        from ..models.router import MeteredEmbeddings
                        provider = MeteredEmbeddings(self.embedder, ledger, tenant_id=tenant_id, prices=getattr(self.router, "prices", None))
                    self._tenant_llms[tenant_id] = _SharedEmbedding(provider)
                llm = self._tenant_llms[tenant_id]
        store = EvidenceStore(self.store_path(holder_id), holder_id=holder_id, tenant_id=tenant_id, llm=llm, router=self.router,
                              export_policy=jl(row["export_policy"], {}), domains=jl(row["domains"], []), extract=self.extract)

        async def heartbeat(stats: dict[str, Any]) -> None:
            await self.org.holder_heartbeat(holder_id, stats=_heartbeat_stats(stats))

        svc = HolderService(store, self.transport, holder_id=holder_id, tenant_id=tenant_id, route_key=self.org.route_key(holder_id),
                            heartbeat=heartbeat, heartbeat_interval=self.heartbeat_interval,
                            policy_loader=lambda: self.current_policy(holder_id))
        self._stores[holder_id] = store
        self._services[holder_id] = svc
        await svc.start()
        return svc

    def get(self, holder_id: str) -> EvidenceStore | None:
        return self._stores.get(holder_id)

    def service(self, holder_id: str) -> HolderService | None:
        return self._services.get(holder_id)

    def holder_ids(self) -> list[str]:
        return list(self._services)

    def current_policy(self, holder_id: str) -> dict[str, Any] | None:
        h = self.org.get_holder(holder_id)
        if h is None:
            return None
        return {"export_policy": h.get("export_policy") or {}, "domains": h.get("domains") or []}

    async def reload_policy(self, holder_id: str) -> None:
        """Push the registry's current policy into the running store (the question path also re-reads it)."""
        store = self._stores.get(holder_id)
        pol = self.current_policy(holder_id)
        if store is not None and pol is not None:
            store.update_policy(pol["export_policy"], pol["domains"])

    async def remove(self, holder_id: str) -> None:
        """Stop and close one holder (after a revocation); its file stays on disk."""
        svc = self._services.pop(holder_id, None)
        store = self._stores.pop(holder_id, None)
        if svc is not None:
            await svc.stop()
        if store is not None:
            await store.close()


def _heartbeat_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """What the registry keeps per holder (the ``holder.stats`` shape of docs/mycelic/API.md)."""
    keys = ("documents", "memories", "entities", "queue", "exports", "questions_answered", "last_ingest_at", "holder_version", "at")
    out = {k: stats.get(k) for k in keys if k in stats}
    svc = stats.get("service") or {}
    out["handled"] = svc.get("handled", 0)
    out["rejected"] = svc.get("rejected", 0)
    return out

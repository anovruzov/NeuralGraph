"""``EmbeddedHolders``: run every ``mode='embedded'`` holder of the coordination DB inside the API process.

Single-node deployments (and the demonstration) have no separate holder processes. Each embedded holder still owns
its own SQLite file under ``<holders_dir>/<holder_id>/evidence.db`` and is driven by the same transport consumer as a
standalone holder, so the code path the coordinator exercises is identical; only the heartbeat goes straight to
``OrgService.holder_heartbeat`` instead of over HTTP, and the export policy is re-read from the org registry before
each question (which is how a ``PATCH /holders/{id}`` takes effect without a restart).

The API uses :meth:`get` to reach a holder's store directly for the owner's memory search and uploads.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from pathlib import Path
from typing import Any, Callable

from ..evidence.service import EmbeddingAdapter, EvidenceStore
from ..util import jl, now_iso
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
        self._ingest: dict[str, Any] = {}
        # connectors' HTTP clients (None = the default: https to each manifest's hosts only). Only code sets this, never
        # configuration: the in-process demonstration points it at loopback mocks
        self.http_factory: Callable[..., Any] | None = None
        self._vault: Any = None
        self._vault_checked = False
        self.running = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.running = True
        await self.reconcile()
        logger.info("embedded holders running: %d", len(self._services))
        # holders registered later by another process (a seed command, another API instance) or revoked elsewhere are
        # picked up on the next pass, so a running API never leaves an embedded holder's routed questions unanswered
        self._reconcile_task = asyncio.create_task(self._reconcile_loop(), name="embedded-holders-reconcile")

    async def reconcile(self) -> dict[str, list[str]]:
        """Start every embedded, non-revoked holder of the registry that is not running here; stop revoked ones."""
        started, stopped = [], []
        wanted: set[str] = set()
        for tenant in self.org.list_tenants():
            for holder in self.org.list_holders(tenant["tenant_id"]):
                if holder.get("mode") != "embedded":
                    continue
                if holder.get("status") == "revoked":
                    if holder["holder_id"] in self._services:
                        await self.remove(holder["holder_id"])
                        stopped.append(holder["holder_id"])
                    continue
                wanted.add(holder["holder_id"])
                running = self._services.get(holder["holder_id"])
                if running is not None and (running.tenant_id != holder["tenant_id"] or running.route_key != self.org.route_key(holder["holder_id"])):
                    # the registry moved under it (tenant recreated, signing key rotated): restart with the current values
                    await self.remove(holder["holder_id"])
                    stopped.append(holder["holder_id"])
                if holder["holder_id"] not in self._services:
                    try:
                        await self.ensure(holder["holder_id"])
                        started.append(holder["holder_id"])
                    except Exception:
                        logger.exception("could not start embedded holder %s", holder["holder_id"])
        for holder_id in [h for h in self._services if h not in wanted]:
            await self.remove(holder_id)          # deleted from the registry
            stopped.append(holder_id)
        if started or stopped:
            logger.info("embedded holders reconciled: started %d, stopped %d", len(started), len(stopped))
        return {"started": started, "stopped": stopped}

    async def _reconcile_loop(self) -> None:
        interval = max(2.0, min(self.heartbeat_interval, 30.0))
        while self.running:
            await asyncio.sleep(interval)
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("embedded holder reconciliation failed")

    async def stop(self) -> None:
        self.running = False
        task = getattr(self, "_reconcile_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for runtime in list(self._ingest.values()):
            try:
                await runtime.stop()
            except Exception:
                logger.exception("error stopping an ingest runtime")
        self._ingest.clear()
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
        # a user-owned holder's owner is always inside its records' ACLs (connector records, mycelic.ingest.acl)
        owner_ids = [row["owner_id"]] if row["owner_type"] == "user" and row["owner_id"] else []
        store = EvidenceStore(self.store_path(holder_id), holder_id=holder_id, tenant_id=tenant_id, llm=llm, router=self.router,
                              export_policy=jl(row["export_policy"], {}), domains=jl(row["domains"], []) + [d for d in jl(row["published_domains"], []) if d not in jl(row["domains"], [])],
                              extract=self.extract, owner_ids=owner_ids)

        async def heartbeat(stats: dict[str, Any]) -> None:
            await self.org.holder_heartbeat(holder_id, stats=_heartbeat_stats(stats))

        svc = HolderService(store, self.transport, holder_id=holder_id, tenant_id=tenant_id, route_key=self.org.route_key(holder_id),
                            heartbeat=heartbeat, heartbeat_interval=self.heartbeat_interval,
                            policy_loader=lambda: self.current_policy(holder_id))
        if getattr(self.settings, "ingest_enabled", True):
            from ..ingest.runtime import IngestRuntime
            svc.ingest = IngestRuntime(store, holder_kind="unit" if row["owner_type"] == "unit" else "user", publisher=svc, vault=self.vault(),
                                       router=self.router, tick_seconds=float(getattr(self.settings, "ingest_tick_seconds", 5.0) or 5.0),
                                       import_root=self.store_path(holder_id).parent / "imports", http_factory=self.http_factory)
            self._ingest[holder_id] = svc.ingest
            svc.ingest.pipeline.classifier.budget_check = lambda tid=tenant_id: self.classify_budget_left(tid)
        self._stores[holder_id] = store
        self._services[holder_id] = svc
        await svc.start()
        if svc.ingest is not None:
            await svc.ingest.start()
        return svc

    def vault(self) -> Any:
        """The server's credential vault (``MYCELIC_SECRET_KEY``), or ``None`` when no acceptable master key is configured:
        connectors that need credentials are then refused with a clear error, and credential-free ones still work."""
        if not self._vault_checked:
            self._vault_checked = True
            try:
                from ..ingest.crypto import TokenVault, VaultUnavailable
                try:
                    self._vault = TokenVault.from_settings(self.settings)
                except VaultUnavailable as exc:
                    logger.warning("connector credentials are disabled for embedded holders: %s", exc)
            except ImportError:
                logger.warning("connector credentials are disabled: the cryptography package is not installed")
        return self._vault

    def classify_budget_left(self, tenant_id: str) -> bool:
        """Tenant policy ``ingest_model_budget.classify_calls_per_day`` (default 500) against today's ``classify_domains``
        calls in the usage ledger, across all of the tenant's embedded holders."""
        cap = int(((self.org.policy(tenant_id, "ingest_model_budget", {}) or {}).get("classify_calls_per_day")) or 500)
        used = int(self.db.scalar("SELECT COUNT(*) FROM model_usage WHERE tenant_id=? AND purpose='classify_domains' AND at >= ?",
                                  (tenant_id, now_iso()[:10]), 0) or 0)
        return used < cap

    def ingest(self, holder_id: str) -> Any:
        return self._ingest.get(holder_id)

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
        from ..org import routable_domains
        return {"export_policy": h.get("export_policy") or {}, "domains": routable_domains(h)}

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
        runtime = self._ingest.pop(holder_id, None)
        if runtime is not None:
            await runtime.stop()
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
    ingest = stats.get("ingest")
    if isinstance(ingest, dict):
        # counts only; EvidenceStore.stats already drops personal domains and domains below the publication threshold
        out["ingest"] = {k: ingest.get(k) for k in ("connectors", "records", "by_app", "queue", "domains") if k in ingest}
    shards = stats.get("shards")
    if isinstance(shards, dict):
        # the shard map, stats, migrations and split recommendations (counts and tenant domain ids only; coord.shards)
        out["shards"] = {k: shards.get(k) for k in ("items", "migrations", "recommendations") if k in shards}
    return out

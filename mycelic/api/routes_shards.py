"""Shard administration (docs/mycelic/INGESTION.md §5.3, §7.2-§7.9; product decision 1).

One store per holder; a domain subtree moves into its own file only when measured thresholds trip and an administrator
approves the split. Administrators see the tenant's shard registry (partition, status, health, counts, last backup) and
the split recommendations, never content, and never a holder's personal domains.

* ``GET  /api/admin/shards`` -> ``{items: [{shard_id, holder_id, ordinal, partition, status, health, stats, last_backup_at}],
  recommendations: [...]}``
* ``POST /api/admin/shards/{holder_id}/split`` ``{domain_ids, wait?}`` -> ``{migration}``. An embedded holder runs the
  migration in this process (in the background unless ``wait``); an external holder receives a signed ``control``
  envelope with action ``shard_split`` and runs it itself.
* ``GET  /api/admin/shards/migrations/{migration_id}`` -> ``{migration}``

Every endpoint requires the organization administrator role, and a shard is only ever reached through its holder, after
the tenant check (a holder or migration of another tenant is 404, like an unknown one). Splits are audited.
"""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from ..shard_registry import list_shards, migration_view, mirror_shards_sync, record_migration_sync
from ..transport import Envelope, Subjects, TransportError
from ..util import new_id
from .middleware import ApiError, json_response, read_json, require_admin
from .routes_org import holder_or_404

logger = logging.getLogger(__name__)

SPLIT_TIMEOUT_SECONDS = 30.0
MAX_SPLIT_DOMAINS = 8


async def mirror_embedded(rt: Any, h: dict[str, Any]) -> None:
    """Refresh the registry rows of an embedded holder running in this process (the heartbeat does it within 20 s)."""
    if rt.holders is None or h.get("mode") != "embedded":
        return
    store = rt.holders.get(h["holder_id"])
    if store is None:
        return
    report = await store.shards.report(force=True)          # an administrator looking: sample now rather than up to 10 min ago
    async with rt.db.tx() as c:
        mirror_shards_sync(c, tenant_id=h["tenant_id"], holder_id=h["holder_id"], mode="embedded", report=report,
                           known_domains=rt.org._taxonomy_ids_sync(c, h["tenant_id"]))


def setup(app: web.Application, prefix: str = "/api") -> None:
    rt = app["rt"]

    async def list_all(request: web.Request) -> web.Response:
        p = require_admin(request)
        for h in rt.org.list_holders(p.tenant_id):
            try:
                await mirror_embedded(rt, h)
            except Exception:
                logger.exception("could not refresh the shard rows of holder %s", h["holder_id"])
        rows = rt.db.all("SELECT * FROM shards WHERE tenant_id=? ORDER BY holder_id, ordinal", (p.tenant_id,))
        items, recs = list_shards(rows)
        return json_response({"items": items, "recommendations": recs})

    async def split(request: web.Request) -> web.Response:
        from ..ingest.domains import UNCLASSIFIED, is_personal
        from ..ingest.reshard import SplitRefused
        p = require_admin(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        if h.get("status") == "revoked":
            raise ApiError(409, "the holder is revoked", "holder_revoked")
        body = await read_json(request)
        raw = body.get("domain_ids")
        if not isinstance(raw, list) or not raw or not all(isinstance(d, str) and d.strip() for d in raw) or len(raw) > MAX_SPLIT_DOMAINS:
            raise ApiError(400, f"domain_ids must be a list of 1 to {MAX_SPLIT_DOMAINS} domain ids")
        tax = rt.authz.tenant_taxonomy(p.tenant_id)
        ids: list[str] = []
        for d in raw:
            rid = tax.resolve(d.strip().lower())
            if is_personal(rid) or rid == UNCLASSIFIED or rid not in tax.domains:
                raise ApiError(400, f"unknown domain {d!r} (tenant taxonomy ids only)", "unknown_domain")
            if rid not in ids:
                ids.append(rid)
        wait = bool(body.get("wait"))
        mode = h.get("mode") or "embedded"
        if mode == "embedded":
            if rt.holders is None:
                raise ApiError(503, "embedded holders are not running on this server", "holder_unavailable")
            await rt.holders.ensure(h["holder_id"])
            store = rt.holders.get(h["holder_id"])
            try:
                mig = await store.shards.start_split(ids, requested_by=p.id, wait=wait)
            except SplitRefused as exc:
                await rt.db.audit(p.tenant_id, "user", p.id, "shard.split", resource_type="holder", resource_id=h["holder_id"], outcome="deny",
                                  detail={"domain_ids": ids, "code": exc.code}, request_id=request.get("request_id"))
                raise ApiError(409, str(exc), exc.code) from None
            await mirror_embedded(rt, h)
            status = 200 if wait else 202
        else:
            if rt.transport is None:
                raise ApiError(503, "no transport configured", "transport")
            env = Envelope.new(Subjects.holder_control(h["tenant_id"], h["holder_id"]), "control", h["tenant_id"],
                               {"action": "shard_split", "domain_ids": ids, "actor": p.id, "holder_id": h["holder_id"]}, msg_id=new_id("ctl"))
            try:
                reply = await rt.transport.request(env, timeout=SPLIT_TIMEOUT_SECONDS, sign_key=rt.org.route_key(h["holder_id"]))
            except TransportError as exc:
                raise ApiError(504, f"the holder did not answer: {exc}", "holder_timeout") from None
            out = reply.payload if isinstance(reply.payload, dict) else {}
            if out.get("error"):
                await rt.db.audit(p.tenant_id, "user", p.id, "shard.split", resource_type="holder", resource_id=h["holder_id"], outcome="deny",
                                  detail={"domain_ids": ids, "code": out.get("code")}, request_id=request.get("request_id"))
                raise ApiError(409, str(out["error"]), str(out.get("code") or "refused"))
            mig = out.get("migration") or {}
            async with rt.db.tx() as c:
                record_migration_sync(c, tenant_id=h["tenant_id"], holder_id=h["holder_id"], migration=mig,
                                      known_domains=rt.org._taxonomy_ids_sync(c, h["tenant_id"]))
            status = 202
        await rt.db.audit(p.tenant_id, "user", p.id, "shard.split", resource_type="holder", resource_id=h["holder_id"],
                          detail={"migration_id": mig.get("migration_id"), "domain_ids": ids, "mode": mode, "state": mig.get("state")},
                          request_id=request.get("request_id"))
        row = rt.db.one("SELECT * FROM shard_migrations WHERE migration_id=? AND tenant_id=?", (mig.get("migration_id"), p.tenant_id))
        return json_response({"migration": migration_view(row) if row is not None else mig}, status)

    async def get_migration(request: web.Request) -> web.Response:
        p = require_admin(request)
        mid = request.match_info["migration_id"]
        row = rt.db.one("SELECT * FROM shard_migrations WHERE migration_id=? AND tenant_id=?", (mid, p.tenant_id))
        if row is None:
            raise KeyError(mid)
        h = rt.org.get_holder(row["holder_id"])
        if h is not None and h["tenant_id"] == p.tenant_id and h.get("mode") == "embedded" and rt.holders is not None:
            store = rt.holders.get(h["holder_id"])
            live = store.shards.migration(mid) if store is not None else None
            if live is not None:
                async with rt.db.tx() as c:
                    record_migration_sync(c, tenant_id=h["tenant_id"], holder_id=h["holder_id"], migration=live,
                                          known_domains=rt.org._taxonomy_ids_sync(c, h["tenant_id"]))
                row = rt.db.one("SELECT * FROM shard_migrations WHERE migration_id=? AND tenant_id=?", (mid, p.tenant_id))
        return json_response({"migration": migration_view(row)})

    app.router.add_get(f"{prefix}/admin/shards", list_all)
    app.router.add_post(f"{prefix}/admin/shards/{{holder_id}}/split", split)
    app.router.add_get(f"{prefix}/admin/shards/migrations/{{migration_id}}", get_migration)

"""Integrations: connectors, sources, sync, webhooks and the tenant taxonomy (docs/mycelic/INGESTION.md §3.7, §10.2, §13.2).

The coordinator never holds an app's content or its tokens. Connectors run inside the holder they feed:

* an **embedded** holder's :class:`~mycelic.ingest.runtime.IngestRuntime` is called in-process;
* an **external** holder receives a signed ``connector_control`` envelope and answers with ``connector_reply``. A token
  in that envelope is sealed for the holder (:func:`mycelic.ingest.crypto.seal_transfer`), and both kinds are scrubbed
  from the transport once handled.

Who may do what (§10.2):

* a **personal** holder's connectors belong to its owner alone (an administrator never connects on someone's behalf);
* a **unit** holder's organization-wide connectors are installed and curated by the unit's leads, or by an org admin
  (an organization install, audited), and each source is mapped to the holder explicitly;
* administrators see connector metadata and counts across the tenant (``/admin/integrations``), never source names or
  content, and manage the tenant taxonomy.

Webhooks only notify: ``POST /webhooks/{type}/{endpoint}`` verifies the provider's signature on the raw body,
de-duplicates the delivery, reduces it to content-free notices and forwards one ``connector_notice`` per routed holder.
The holder then fetches what the notice names with its own grant, and the next poll repairs any notice that was lost.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from aiohttp import web

from ..authz import Principal
from ..ingest.contract import ConnectorError
from ..transport import Envelope, Subjects, TransportError
from ..util import j, jl, new_id, now_iso, plus_seconds, sha256
from .middleware import ApiError, json_response, listing, need_str, opt_dict, opt_list, opt_str, read_json, require_admin, require_user
from .routes_org import holder_or_404, is_holder_owner

logger = logging.getLogger(__name__)

CONTROL_TIMEOUT_SECONDS = 30.0
SYNC_TIMEOUT_SECONDS = 120.0
WEBHOOK_MAX_BYTES = 5 * 1024 * 1024
IMPORT_MAX_BYTES = 200 * 1024 * 1024
SERVER_TENANT = "__server__"          # vault namespace of server-held secrets (webhook signing secrets, PKCE verifiers)
SOURCE_CHANGE_FIELDS = ("source_id", "selection", "exportable", "default_domain_ids", "disclosure", "sensitivity")


def _registry() -> Any:
    from ..ingest.connectors import register_builtin
    return register_builtin()


def _server_vault(rt: Any) -> Any:
    from ..ingest.crypto import TokenVault, VaultUnavailable
    cached = rt.extras.get("server_vault")
    if cached is None:
        try:
            cached = TokenVault.from_settings(rt.settings)
        except VaultUnavailable as exc:
            raise ApiError(503, f"server secrets are not configured: {exc}", "vault_unavailable") from None
        rt.extras["server_vault"] = cached
    return cached


def _seal_server_secret(rt: Any, scope_id: str, value: str) -> bytes:
    """A server-held secret (webhook signing secret), sealed with the server vault and bound to ``scope_id``."""
    from ..ingest.contract import Credentials, Secret
    sealed = _server_vault(rt).seal(tenant_id=SERVER_TENANT, holder_id="-", connector_id=scope_id, credentials=Credentials(kind="secret", access_token=Secret(value)))
    return j({"kid": sealed.kid, "w": sealed.wrapped_dek.hex(), "c": sealed.ciphertext.hex()}).encode("utf-8")


def _open_server_secret(rt: Any, scope_id: str, blob: bytes | str) -> str:
    from ..ingest.crypto import SealedCredentials
    d = jl(blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob, {})
    row = SealedCredentials(kid=d["kid"], wrapped_dek=bytes.fromhex(d["w"]), ciphertext=bytes.fromhex(d["c"]), aad="")
    creds = _server_vault(rt).open(row, tenant_id=SERVER_TENANT, holder_id="-", connector_id=scope_id)
    return creds.access_token.reveal() if creds.access_token else ""


# ---------------------------------------------------------------------------------------------- authority
def require_connector_manager(rt: Any, p: Principal, h: dict[str, Any]) -> str:
    """The scope this caller may manage the holder's connectors in: ``personal`` (the user holder's owner) or
    ``organization`` (a lead of the owning unit, or an org admin for a unit holder). Anyone else gets 403."""
    if h.get("status") == "revoked":
        raise ApiError(409, "the holder is revoked", "holder_revoked")
    if h.get("owner_type") == "user":
        if h.get("owner_id") == p.id:
            return "personal"
        raise ApiError(403, "only the owner connects apps to a personal memory", "forbidden")
    if is_holder_owner(rt, p, h) or p.is_admin:
        return "organization"
    raise ApiError(403, "organization-wide connectors are managed by the unit's leads or an org admin", "forbidden")


def enabled_types(rt: Any, tenant_id: str) -> list[str] | None:
    v = rt.org.policy(tenant_id, "ingest_connectors", None)
    return list(v) if isinstance(v, list) else None


# ---------------------------------------------------------------------------------------------- the holder gateway
async def holder_call(rt: Any, h: dict[str, Any], action: str, params: dict[str, Any], *, actor: str, credentials: dict[str, Any] | None = None,
                      timeout: float = CONTROL_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run one connector action in the holder. Errors become API errors with the holder's code; nothing content-bearing
    is logged. ``credentials`` go to an embedded runtime in memory, to an external holder sealed for it."""
    from ..ingest.crypto import CredentialTampered, VaultUnavailable, seal_transfer
    from ..ingest.runtime import ControlError
    hid = h["holder_id"]
    if h.get("mode") == "embedded":
        if rt.holders is None:
            raise ApiError(503, "embedded holders are not running on this server", "holder_unavailable")
        try:
            await rt.holders.ensure(hid)
        except Exception:
            raise ApiError(503, "the embedded holder could not be opened", "holder_unavailable") from None
        runtime = rt.holders.ingest(hid)
        if runtime is None:
            raise ApiError(503, "ingestion is disabled on this server (MYCELIC_INGEST=0)", "ingest_disabled")
        try:
            return await runtime.control(action, params, actor=actor, credentials=credentials)
        except ControlError as exc:
            raise ApiError(404 if str(exc).startswith("unknown") else 400, str(exc), "refused") from None
        except VaultUnavailable as exc:
            raise ApiError(503, str(exc), "vault_unavailable") from None
        except CredentialTampered:
            raise ApiError(500, "a stored credential could not be opened", "credential_tampered") from None
        except ConnectorError as exc:
            raise ApiError(502 if exc.retryable else 400, f"the app refused: {exc.code}", exc.code) from None
        except ValueError as exc:
            raise ApiError(400, str(exc), "refused") from None
    if rt.transport is None:
        raise ApiError(503, "no transport configured", "transport")
    route_key = rt.org.route_key(hid)
    env = Envelope.new(Subjects.holder_control(h["tenant_id"], hid), "connector_control", h["tenant_id"],
                       {"action": action, "actor": actor, "params": params, "holder_id": hid}, msg_id=new_id("cc"))
    if credentials:
        env.payload["credentials_sealed"] = seal_transfer(route_key, hid, env.msg_id, credentials)
    try:
        reply = await rt.transport.request(env, timeout=timeout, sign_key=route_key)
    except TransportError as exc:
        raise ApiError(504, f"the holder did not answer: {exc}", "holder_timeout") from None
    out = reply.payload if isinstance(reply.payload, dict) else {}
    if out.get("error"):
        code = str(out.get("code") or "refused")
        status = {"refused": 400, "vault_unavailable": 503, "ingest_disabled": 503, "credential_tampered": 400}.get(code, 502)
        if code == "refused" and str(out["error"]).startswith("unknown"):
            status = 404
        raise ApiError(status, str(out["error"]), code)
    return out


# ---------------------------------------------------------------------------------------------- coordinator mirror
async def mirror_connector(rt: Any, h: dict[str, Any], con: dict[str, Any], *, scope: str, actor: Principal | None = None,
                           counts: dict[str, Any] | None = None) -> None:
    """Keep ``connector_registry`` (metadata visible to the owner and admins) in step with the holder: ids, type, status,
    scopes and counts, never source names or content."""
    now = now_iso()
    c = counts or con.get("counts") or {}
    async with rt.db.tx() as cx:
        row = cx.execute("SELECT connector_id FROM connector_registry WHERE connector_id=?", (con["connector_id"],)).fetchone()
        if row is None:
            install_id = new_id("ins")
            cx.execute("INSERT INTO connector_installs(install_id, tenant_id, connector_type, scope, installed_by, unit_id, external_account_id, status, granted_scopes, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                       (install_id, h["tenant_id"], con["connector_type"], scope, actor.id if actor else h.get("owner_id"), h["owner_id"] if h.get("owner_type") == "unit" else None,
                        con.get("source_account_id"), j(con.get("granted_scopes") or []), now, now))
            cx.execute("INSERT INTO connector_registry(connector_id, tenant_id, holder_id, install_id, scope, owner_user_id, connector_type, mode, status, status_code, granted_scopes, "
                       "sources_included, sources_pending, records, last_sync_at, last_success_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (con["connector_id"], h["tenant_id"], h["holder_id"], install_id, scope, h["owner_id"] if h.get("owner_type") == "user" else None,
                        con["connector_type"], con.get("mode") or "", con.get("status") or "", con.get("status_code") or "", j(con.get("granted_scopes") or []),
                        int(c.get("sources_included") or 0), int(c.get("sources_pending") or 0), int(c.get("records") or 0), con.get("last_sync_at"), con.get("last_success_at"), now, now))
        else:
            cx.execute("UPDATE connector_registry SET status=?, status_code=?, granted_scopes=?, sources_included=COALESCE(?, sources_included), "
                       "sources_pending=COALESCE(?, sources_pending), records=COALESCE(?, records), last_sync_at=COALESCE(?, last_sync_at), "
                       "last_success_at=COALESCE(?, last_success_at), updated_at=? WHERE connector_id=?",
                       (con.get("status") or "", con.get("status_code") or "", j(con.get("granted_scopes") or []),
                        c.get("sources_included"), c.get("sources_pending"), c.get("records"), con.get("last_sync_at"), con.get("last_success_at"), now, con["connector_id"]))
            if con.get("status") == "disconnected":
                cx.execute("UPDATE connector_installs SET status='revoked', revoked_at=?, updated_at=? WHERE install_id=(SELECT install_id FROM connector_registry WHERE connector_id=?)",
                           (now, now, con["connector_id"]))


def _connector_in_holder(rt: Any, h: dict[str, Any], connector_id: str) -> None:
    """A connector id from the URL must belong to this holder (the holder also refuses ids it does not know)."""
    r = rt.db.one("SELECT holder_id FROM connector_registry WHERE connector_id=?", (connector_id,))
    if r is not None and r["holder_id"] != h["holder_id"]:
        raise KeyError(connector_id)


# ---------------------------------------------------------------------------------------------- routes
def setup(app: web.Application, prefix: str) -> None:
    rt = app["rt"]
    settings = app["settings"]

    def public_base(request: web.Request) -> str:
        return (settings.public_url or f"{request.scheme}://{request.host}").rstrip("/")

    # ------------------------------------------------------------------ catalog
    async def catalog(request: web.Request) -> web.Response:
        p = require_user(request)
        reg = _registry()
        items = []
        for d in reg.catalog(enabled=enabled_types(rt, p.tenant_id)):
            # honest status: scaffolds are listed as planned and cannot be connected
            items.append({**d, "connectable": d["status"] != "scaffold" and d["enabled_for_tenant"]})
        return json_response(listing(items))

    # ------------------------------------------------------------------ connectors of a holder
    async def list_connectors(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        out = await holder_call(rt, h, "connectors.list", {}, actor=p.id)
        return json_response(listing(out.get("items") or []))

    async def add_connector(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        scope = require_connector_manager(rt, p, h)
        body = await read_json(request)
        ctype = need_str(body, "connector_type", max_len=64)
        reg = _registry()
        try:
            cls = reg.get(ctype)
        except KeyError:
            raise ApiError(400, f"unknown connector type {ctype!r}", "unknown_connector_type") from None
        if cls.manifest.status == "scaffold":
            raise ApiError(400, f"{cls.manifest.display_name} is planned, not implemented", "connector_scaffold")
        allowed = enabled_types(rt, p.tenant_id)
        if allowed is not None and ctype not in allowed:
            raise ApiError(403, "this connector type is disabled for the organization", "connector_disabled")
        wanted = "org" if scope == "organization" else "personal"
        if wanted not in cls.manifest.ownership:
            raise ApiError(400, f"{cls.manifest.display_name} cannot feed a {'unit' if wanted == 'org' else 'personal'} holder", "ownership")
        auth = opt_dict(body, "auth") or {}
        config = opt_dict(body, "config") or {}
        credentials = None
        if auth:
            kind = str(auth.get("kind") or "pat")
            if kind not in cls.manifest.auth_kinds:
                raise ApiError(400, f"auth kind {kind!r} is not supported by {ctype}", "auth_kind")
            if kind == "oauth2":
                return json_response(await start_oauth(request, p, h, ctype, scope, config))
            token = auth.get("token")
            if not isinstance(token, str) or not token.strip() or len(token) > 4096:
                raise ApiError(400, "auth.token is required", "bad_request")
            credentials = {"kind": kind, "access_token": token.strip()}
        elif "none" not in cls.manifest.auth_kinds:
            raise ApiError(400, "this connector needs auth: a token, or the OAuth flow", "auth_required")
        params = {"connector_type": ctype, "config": config, "display_name": opt_str(body, "display_name", max_len=120), "discover": True}
        out = await holder_call(rt, h, "connector.add", params, actor=p.id, credentials=credentials, timeout=SYNC_TIMEOUT_SECONDS)
        con = out.get("connector") or {}
        await mirror_connector(rt, h, con, scope=scope, actor=p)
        # audited without the token or any source name
        await rt.db.audit(p.tenant_id, "user", p.id, "connector.install", resource_type="holder", resource_id=h["holder_id"],
                          detail={"connector_id": con.get("connector_id"), "connector_type": ctype, "scope": scope, "auth": credentials["kind"] if credentials else "none",
                                  "discovered": (out.get("discovered") or {}).get("discovered")}, request_id=request.get("request_id"))
        return json_response({"connector": con, "discovered": out.get("discovered") or {}, "next": {"action": "select_sources"}}, status=201)

    async def get_connector(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        out = await holder_call(rt, h, "connector.get", {"connector_id": cid}, actor=p.id)
        return json_response(out)

    async def patch_connector(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        scope = require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        body = await read_json(request)
        status = opt_str(body, "status", max_len=20)
        if status not in (None, "active", "paused"):
            raise ApiError(400, "status must be active or paused")
        out = await holder_call(rt, h, "connector.update", {"connector_id": cid, "status": status, "config": opt_dict(body, "config") or {}}, actor=p.id)
        await mirror_connector(rt, h, out.get("connector") or {}, scope=scope, actor=p)
        await rt.db.audit(p.tenant_id, "user", p.id, "connector.update", resource_type="holder", resource_id=h["holder_id"],
                          detail={"connector_id": cid, "status": status}, request_id=request.get("request_id"))
        return json_response(out)

    async def delete_connector(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        scope = require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        data = request.query.get("data", "keep")
        if data not in ("keep", "delete"):
            raise ApiError(400, "data must be keep or delete")
        out = await holder_call(rt, h, "connector.disconnect", {"connector_id": cid, "data": data, "revoke": request.query.get("revoke") == "1"},
                                actor=p.id, timeout=SYNC_TIMEOUT_SECONDS)
        if rt.db.one("SELECT 1 FROM connector_registry WHERE connector_id=?", (cid,)) is not None:
            await mirror_connector(rt, h, {"connector_id": cid, "connector_type": "", "status": "disconnected"}, scope=scope, actor=p)
        async with rt.db.tx() as cx:
            cx.execute("DELETE FROM webhook_routes WHERE connector_id=?", (cid,))
        await rt.db.audit(p.tenant_id, "user", p.id, "connector.disconnect", resource_type="holder", resource_id=h["holder_id"],
                          detail={"connector_id": cid, "data": data, "records_deleted": out.get("records_deleted", 0)}, request_id=request.get("request_id"))
        return json_response({"connector": {"connector_id": cid, "status": "disconnected"}, "deletion": {"records_deleted": out.get("records_deleted", 0)}})

    # ------------------------------------------------------------------ sources
    async def list_sources(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        sel = request.query.get("selection")
        out = await holder_call(rt, h, "sources.list", {"connector_id": cid, "selection": sel}, actor=p.id)
        return json_response(listing(out.get("items") or []))

    async def discover_sources(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        out = await holder_call(rt, h, "sources.discover", {"connector_id": cid}, actor=p.id, timeout=SYNC_TIMEOUT_SECONDS)
        return json_response(out.get("discovered") or {})

    async def patch_sources(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        scope = require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        body = await read_json(request)
        changes = opt_list(body, "changes") or []
        if not changes or len(changes) > 500 or not all(isinstance(ch, dict) and isinstance(ch.get("source_id"), str) for ch in changes):
            raise ApiError(400, "changes must be a non-empty list of {source_id, selection?, ...}")
        clean = [{k: ch[k] for k in SOURCE_CHANGE_FIELDS if k in ch} for ch in changes]
        for ch in clean:
            if ch.get("selection") not in (None, "included", "excluded", "pending_review"):
                raise ApiError(400, "selection must be included, excluded or pending_review")
        existing = opt_str(body, "existing_records", max_len=10) or "keep"
        if existing not in ("keep", "delete"):
            raise ApiError(400, "existing_records must be keep or delete")
        out = await holder_call(rt, h, "sources.update", {"connector_id": cid, "changes": clean, "existing_records": existing}, actor=p.id,
                                timeout=SYNC_TIMEOUT_SECONDS)
        await rt.db.audit(p.tenant_id, "user", p.id, "connector.sources", resource_type="holder", resource_id=h["holder_id"],
                          detail={"connector_id": cid, "scope": scope, "changes": len(clean),
                                  "included": sum(1 for ch in clean if ch.get("selection") == "included"),
                                  "excluded": sum(1 for ch in clean if ch.get("selection") == "excluded")}, request_id=request.get("request_id"))
        return json_response(listing(out.get("items") or []))

    async def delete_source(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        out = await holder_call(rt, h, "source.delete", {"connector_id": cid, "source_id": request.match_info["source_id"]}, actor=p.id,
                                timeout=SYNC_TIMEOUT_SECONDS)
        await rt.db.audit(p.tenant_id, "user", p.id, "connector.source_delete", resource_type="holder", resource_id=h["holder_id"],
                          detail={"connector_id": cid, "records": out.get("records")}, request_id=request.get("request_id"))
        return json_response({"records_deleted": out.get("records", 0)})

    # ------------------------------------------------------------------ sync and queue
    async def sync_connector(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        scope = require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        body = await read_json(request, required=False)
        mode = opt_str(body, "mode", max_len=20) or "incremental"
        if mode not in ("incremental", "backfill"):
            raise ApiError(400, "mode must be incremental or backfill")
        out = await holder_call(rt, h, "connector.sync", {"connector_id": cid, "mode": mode}, actor=p.id, timeout=SYNC_TIMEOUT_SECONDS)
        try:
            got = await holder_call(rt, h, "connector.get", {"connector_id": cid}, actor=p.id)
            await mirror_connector(rt, h, got.get("connector") or {}, scope=scope, actor=p)
        except ApiError:
            pass
        return json_response(out, status=202)

    async def queue_status(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        return json_response(await holder_call(rt, h, "queue.status", {}, actor=p.id))

    # ------------------------------------------------------------------ records and domains ("why this domain")
    async def list_records(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        q = {k: request.query.get(k) for k in ("domain_id", "source_app", "limit") if request.query.get(k)}
        out = await holder_call(rt, h, "records.list", q, actor=p.id)
        return json_response(listing(out.get("items") or []))

    async def get_record(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        return json_response(await holder_call(rt, h, "record.get", {"record_id": request.match_info["record_id"]}, actor=p.id))

    async def correct_record_domains(request: web.Request) -> web.Response:
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        body = await read_json(request)
        add, remove = opt_list(body, "add") or [], opt_list(body, "remove") or []
        if not all(isinstance(x, str) for x in add + remove) or len(add) + len(remove) > 10:
            raise ApiError(400, "add and remove are lists of domain ids")
        out = await holder_call(rt, h, "record.domains", {"record_id": request.match_info["record_id"], "add": add, "remove": remove,
                                                           "primary": opt_str(body, "primary", max_len=120), "reason": opt_str(body, "reason", max_len=300) or ""},
                                actor=p.id)
        await rt.db.audit(p.tenant_id, "user", p.id, "record.domains", resource_type="holder", resource_id=h["holder_id"],
                          detail={"added": len(add), "removed": len(remove)}, request_id=request.get("request_id"))
        return json_response(out)

    # ------------------------------------------------------------------ webhook endpoints
    async def create_webhook(request: web.Request) -> web.Response:
        """A signed delivery URL for this connector. GitHub: Mycelic generates the secret, shown once, for the owner to
        paste into the repository's webhook settings. Slack: the owner supplies the app's signing secret."""
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        cid = request.match_info["connector_id"]
        _connector_in_holder(rt, h, cid)
        body = await read_json(request, required=False)
        got = (await holder_call(rt, h, "connector.get", {"connector_id": cid}, actor=p.id)).get("connector") or {}
        ctype = got.get("connector_type") or ""
        try:
            cls = _registry().get(ctype)
        except KeyError:
            raise ApiError(400, "unknown connector type", "unknown_connector_type") from None
        if "webhook" not in cls.manifest.modes:
            raise ApiError(400, f"{cls.manifest.display_name} does not support webhooks", "no_webhooks")
        supplied = opt_str(body, "signing_secret", max_len=256)
        secret = supplied or secrets.token_urlsafe(32)
        endpoint_id = "whk_" + secrets.token_hex(12)
        account = str(got.get("source_account_id") or "")
        now = now_iso()
        async with rt.db.tx() as cx:
            cx.execute("INSERT INTO webhook_endpoints(endpoint_id, tenant_id, connector_type, scope, external_account_id, secret_kid, secret_ct, status, created_at) "
                       "VALUES (?, ?, ?, 'connector', ?, ?, ?, 'active', ?)",
                       (endpoint_id, p.tenant_id, ctype, account, "server", _seal_server_secret(rt, endpoint_id, secret), now))
            cx.execute("INSERT OR IGNORE INTO webhook_routes(endpoint_id, external_account_id, source_external_id, holder_id, connector_id, created_at) VALUES (?, ?, '*', ?, ?, ?)",
                       (endpoint_id, account, h["holder_id"], cid, now))
        await rt.db.audit(p.tenant_id, "user", p.id, "webhook.create", resource_type="holder", resource_id=h["holder_id"],
                          detail={"connector_id": cid, "endpoint_id": endpoint_id, "connector_type": ctype}, request_id=request.get("request_id"))
        out = {"endpoint_id": endpoint_id, "url": f"{public_base(request)}/api/webhooks/{ctype}/{endpoint_id}", "connector_type": ctype}
        if not supplied:
            out["secret"] = secret            # shown once; only its sealed form is stored
        return json_response(out, status=201)

    async def receive_webhook(request: web.Request) -> web.Response:
        """Public. Verify, de-duplicate, reduce to notices, route; answer fast (GitHub expects 2xx within 10 s, Slack 3 s)."""
        ctype, endpoint_id = request.match_info["connector_type"], request.match_info["endpoint_id"]
        if request.content_length is not None and request.content_length > WEBHOOK_MAX_BYTES:
            raise ApiError(413, "payload too large")
        body = await request.content.read(WEBHOOK_MAX_BYTES + 1)
        if len(body) > WEBHOOK_MAX_BYTES:
            raise ApiError(413, "payload too large")
        ep = rt.db.one("SELECT * FROM webhook_endpoints WHERE endpoint_id=?", (endpoint_id,))
        if ep is None or ep["status"] != "active" or ep["connector_type"] != ctype:
            raise ApiError(404, "unknown webhook endpoint", "not_found")
        try:
            cls = _registry().get(ctype)
        except KeyError:
            raise ApiError(404, "unknown webhook endpoint", "not_found") from None
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            secret = _open_server_secret(rt, endpoint_id, ep["secret_ct"])
            ok = bool(cls.verify_webhook(headers, body, secret.encode("utf-8"), now=time.time()))
        except ApiError:
            raise
        except Exception:
            ok = False
        if not ok:
            await rt.db.audit(ep["tenant_id"], "system", "webhook", "webhook.rejected", resource_type="webhook", resource_id=endpoint_id,
                              detail={"connector_type": ctype, "reason": "signature"})
            raise ApiError(401, "signature verification failed", "bad_signature")
        challenge = getattr(cls, "challenge_response", None)
        if challenge is not None:
            ch = challenge(headers, body)
            if ch is not None:
                return json_response({"challenge": ch})
        try:
            notices = cls.parse_webhook(headers, body)
        except Exception:
            notices = []
        routed = 0
        for n in notices:
            if n.external_account_id and ep["external_account_id"] and n.external_account_id != ep["external_account_id"]:
                continue                       # a connector endpoint only speaks for its own account
            seen = rt.db.one("SELECT status FROM webhook_deliveries WHERE endpoint_id=? AND delivery_id=?", (endpoint_id, n.delivery_id))
            if seen is not None and seen["status"] == "routed":
                continue                       # a provider retry or a replay
            routes = rt.db.all("SELECT holder_id, connector_id FROM webhook_routes WHERE endpoint_id=? AND (source_external_id=? OR source_external_id='*')",
                               (endpoint_id, n.source_external_id))
            count = 0
            for r in routes:
                h = rt.org.get_holder(r["holder_id"])
                if h is None or h.get("status") == "revoked" or h["tenant_id"] != ep["tenant_id"]:
                    continue
                from ..ingest.runtime import notice_to_payload
                env = Envelope.new(Subjects.holder_ingest(h["tenant_id"], h["holder_id"]), "connector_notice", h["tenant_id"],
                                   {"connector_id": r["connector_id"], "notice": notice_to_payload(n), "holder_id": h["holder_id"]},
                                   msg_id=f"notice:{n.delivery_id}:{h['holder_id']}").sign(rt.org.route_key(h["holder_id"]))
                try:
                    await rt.transport.publish(env)
                    count += 1
                except Exception as exc:
                    logger.warning("could not forward a webhook notice to holder %s: %s", h["holder_id"], type(exc).__name__)
            async with rt.db.tx() as cx:
                cx.execute("INSERT OR REPLACE INTO webhook_deliveries(endpoint_id, delivery_id, event_type, received_at, status, routed_holders) VALUES (?, ?, ?, ?, ?, ?)",
                           (endpoint_id, n.delivery_id, n.action[:64], now_iso(), "routed" if count or not routes else "ignored", count))
            routed += count
        return json_response({"ok": True, "routed": routed})

    # ------------------------------------------------------------------ OAuth (terminates at the core; tokens go to the holder)
    async def start_oauth(request: web.Request, p: Principal, h: dict[str, Any], ctype: str, scope: str, config: dict[str, Any]) -> dict[str, Any]:
        from ..ingest.contract import Credentials, Secret
        cls = _registry().get(ctype)
        client_id = getattr(settings, f"{ctype}_client_id", "") or ""
        if not client_id:
            raise ApiError(503, f"OAuth for {ctype} is not configured on this server (MYCELIC_{ctype.upper()}_CLIENT_ID)", "oauth_unconfigured")
        state = secrets.token_urlsafe(32)
        redirect_uri = f"{public_base(request)}/api/integrations/oauth/{ctype}/callback"
        instance = cls()
        if hasattr(instance, "configure_oauth"):
            instance.configure_oauth(client_id=client_id, client_secret=getattr(settings, f"{ctype}_client_secret", ""), config=config)
        start = await instance.authorize(tenant_id=p.tenant_id, holder_id=h["holder_id"], redirect_uri=redirect_uri, state=state)
        verifier = start.pkce_verifier.reveal() if start.pkce_verifier else ""
        sealed = _server_vault(rt).seal(tenant_id=SERVER_TENANT, holder_id=h["holder_id"], connector_id="oauth:" + sha256(state),
                                        credentials=Credentials(kind="pkce", access_token=Secret(verifier) if verifier else None, extra={"config": j(config)}))
        async with rt.db.tx() as cx:
            cx.execute("INSERT INTO oauth_states(state_hash, tenant_id, holder_id, user_id, connector_type, install_scope, pkce_verifier_ct, redirect_uri, created_at, expires_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (sha256(state), p.tenant_id, h["holder_id"], p.id, ctype, scope,
                        j({"kid": sealed.kid, "w": sealed.wrapped_dek.hex(), "c": sealed.ciphertext.hex()}).encode("utf-8"), redirect_uri, now_iso(), plus_seconds(600)))
        return {"next": {"action": "redirect", "url": start.url}, "connector": None}

    async def oauth_callback(request: web.Request) -> web.Response:
        from ..ingest.crypto import SealedCredentials
        p = require_user(request)
        ctype = request.match_info["connector_type"]
        state, code = request.query.get("state", ""), request.query.get("code", "")
        if not state or not code:
            raise ApiError(400, "state and code are required")
        row = None
        async with rt.db.tx() as cx:
            row = cx.execute("SELECT * FROM oauth_states WHERE state_hash=?", (sha256(state),)).fetchone()
            if row is not None and row["used_at"] is None:
                cx.execute("UPDATE oauth_states SET used_at=? WHERE state_hash=?", (now_iso(), row["state_hash"]))     # single use
        if row is None or row["used_at"] is not None or row["connector_type"] != ctype or row["user_id"] != p.id or row["tenant_id"] != p.tenant_id \
                or row["expires_at"] < now_iso():
            raise ApiError(400, "this authorization link is invalid or expired; start again", "oauth_state")
        h = holder_or_404(rt, p, row["holder_id"])
        scope = require_connector_manager(rt, p, h)
        d = jl(row["pkce_verifier_ct"].decode("utf-8") if isinstance(row["pkce_verifier_ct"], (bytes, bytearray)) else row["pkce_verifier_ct"], {})
        pk = _server_vault(rt).open(SealedCredentials(kid=d["kid"], wrapped_dek=bytes.fromhex(d["w"]), ciphertext=bytes.fromhex(d["c"]), aad=""),
                                    tenant_id=SERVER_TENANT, holder_id=h["holder_id"], connector_id="oauth:" + sha256(state))
        config = jl(pk.extra.get("config"), {}) if pk.extra else {}
        cls = _registry().get(ctype)
        instance = cls()
        if hasattr(instance, "configure_oauth"):
            instance.configure_oauth(client_id=getattr(settings, f"{ctype}_client_id", ""), client_secret=getattr(settings, f"{ctype}_client_secret", ""), config=config)
        try:
            creds = await instance.complete_authorization(dict(request.query), redirect_uri=row["redirect_uri"], pkce_verifier=pk.access_token)
        except ConnectorError as exc:
            raise ApiError(400, f"the app refused the authorization: {exc.code}", exc.code) from None
        payload = creds.to_storable()
        out = await holder_call(rt, h, "connector.add", {"connector_type": ctype, "config": config, "discover": True}, actor=p.id,
                                credentials=payload, timeout=SYNC_TIMEOUT_SECONDS)
        con = out.get("connector") or {}
        await mirror_connector(rt, h, con, scope=scope, actor=p)
        await rt.db.audit(p.tenant_id, "user", p.id, "connector.install", resource_type="holder", resource_id=h["holder_id"],
                          detail={"connector_id": con.get("connector_id"), "connector_type": ctype, "scope": scope, "auth": "oauth2"}, request_id=request.get("request_id"))
        raise web.HTTPFound(f"/app/memory/integrations?connected={con.get('connector_id', '')}")

    # ------------------------------------------------------------------ export files for file-based connectors
    async def upload_import(request: web.Request) -> web.Response:
        """A JSON or JSONL export into the holder's ``imports/`` directory, for the ``local_export`` connector (embedded
        holders; an external holder's owner places files on their own machine). Names are sanitized, nothing outside the
        directory can be written, and an existing file is replaced only on request."""
        import re
        p = require_user(request)
        h = holder_or_404(rt, p, request.match_info["holder_id"])
        require_connector_manager(rt, p, h)
        if h.get("mode") != "embedded" or rt.holders is None:
            raise ApiError(400, "an external holder's export files are placed on the holder's own machine", "external_holder")
        if not (request.content_type or "").startswith("multipart/"):
            raise ApiError(400, "upload the export as multipart 'file'")
        reader = await request.multipart()
        replace = False
        saved = None
        root = rt.holders.store_path(h["holder_id"]).parent / "imports"
        root.mkdir(parents=True, exist_ok=True)
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "replace":
                replace = (await part.text()).strip() in ("1", "true", "yes")
            elif part.name == "file":
                from urllib.parse import unquote
                base = re.split(r"[/\\]", unquote(part.filename or "export.jsonl"))[-1]
                name = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-") or "export.jsonl"
                if not name.lower().endswith((".json", ".jsonl")):
                    raise ApiError(400, "export files are .json or .jsonl")
                size = 0
                tmp = root / f".{name}.part"
                with tmp.open("wb") as f:
                    while True:
                        chunk = await part.read_chunk(1 << 16)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > IMPORT_MAX_BYTES:
                            f.close()
                            tmp.unlink(missing_ok=True)
                            raise ApiError(413, "export file too large", "too_large")
                        f.write(chunk)
                saved = {"name": name, "bytes": size, "tmp": tmp}
        if saved is None:
            raise ApiError(400, "multipart upload needs a 'file' part")
        tmp = saved.pop("tmp")
        target = root / saved["name"]
        if target.exists() and not replace:
            tmp.unlink(missing_ok=True)
            raise ApiError(409, f"{saved['name']} already exists; send replace=1 to overwrite", "exists")
        tmp.replace(target)
        await rt.db.audit(p.tenant_id, "user", p.id, "import.upload", resource_type="holder", resource_id=h["holder_id"],
                          detail={"bytes": saved["bytes"]}, request_id=request.get("request_id"))
        return json_response({"import": saved, "config_hint": {"paths": [saved["name"]]}}, status=201)

    # ------------------------------------------------------------------ admin: metadata and taxonomy
    async def admin_integrations(request: web.Request) -> web.Response:
        p = require_admin(request)
        rows = rt.db.all("SELECT r.*, h.name AS holder_name, h.owner_type, h.owner_id FROM connector_registry r JOIN holders h ON h.holder_id=r.holder_id "
                         "WHERE r.tenant_id=? ORDER BY r.created_at", (p.tenant_id,))
        items = []
        totals: dict[str, dict[str, int]] = {"by_type": {}, "by_status": {}}
        for r in rows:
            d = {k: r[k] for k in ("connector_id", "holder_id", "holder_name", "scope", "connector_type", "status", "status_code", "mode", "sources_included",
                                   "sources_pending", "records", "last_sync_at", "last_success_at", "created_at")}
            d["granted_scopes"] = jl(r["granted_scopes"], [])
            items.append(d)
            totals["by_type"][r["connector_type"]] = totals["by_type"].get(r["connector_type"], 0) + 1
            totals["by_status"][r["status"]] = totals["by_status"].get(r["status"], 0) + 1
        return json_response(listing(items, totals=totals))

    def taxonomy_payload(tenant_id: str) -> dict[str, Any]:
        tax = rt.authz.tenant_taxonomy(tenant_id)
        configured = rt.db.one("SELECT 1 FROM domain_taxonomy WHERE tenant_id=? LIMIT 1", (tenant_id,)) is not None
        items = [{"domain_id": d.domain_id, "parent_id": d.parent_id, "name": d.name, "path": tax.path(d.domain_id), "description": d.description,
                  "status": d.status} for d in sorted(tax.domains.values(), key=lambda x: x.domain_id) if d.scope == "tenant"]
        return {"taxonomy_version": int(rt.org.policy(tenant_id, "taxonomy_version", 1) or 1), "configured": configured, "items": items,
                "aliases": [{"alias": a, "domain_id": d} for a, d in sorted(tax.aliases.items())]}

    async def get_domains(request: web.Request) -> web.Response:
        p = require_user(request)
        return json_response(taxonomy_payload(p.tenant_id))

    async def put_domains(request: web.Request) -> web.Response:
        """``{upsert:[{domain_id, name, parent_id?, description?}], deprecate:[domain_id], aliases:[{alias, domain_id}]}``. The first
        change copies the default taxonomy into the tenant's rows; ids are stable dot paths and never reused."""
        import re
        p = require_admin(request)
        body = await read_json(request)
        upsert = opt_list(body, "upsert") or []
        deprecate = opt_list(body, "deprecate") or []
        aliases = opt_list(body, "aliases") or []
        idre = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}(\.[a-z0-9][a-z0-9-]{0,40}){0,3}$")
        tax = rt.authz.tenant_taxonomy(p.tenant_id)
        now = now_iso()
        async with rt.db.tx() as cx:
            if cx.execute("SELECT 1 FROM domain_taxonomy WHERE tenant_id=? LIMIT 1", (p.tenant_id,)).fetchone() is None:
                for d in tax.domains.values():
                    if d.scope == "tenant":
                        cx.execute("INSERT INTO domain_taxonomy(tenant_id, domain_id, parent_id, name, description, status, created_at, updated_at, updated_by) "
                                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (p.tenant_id, d.domain_id, d.parent_id, d.name, d.description, d.status, now, now, p.id))
                for a, d in tax.aliases.items():
                    cx.execute("INSERT OR IGNORE INTO domain_aliases(tenant_id, alias, domain_id) VALUES (?, ?, ?)", (p.tenant_id, a, d))
            known = {r["domain_id"] for r in cx.execute("SELECT domain_id FROM domain_taxonomy WHERE tenant_id=?", (p.tenant_id,))}
            for u in upsert:
                if not isinstance(u, dict) or not isinstance(u.get("domain_id"), str) or not idre.match(u["domain_id"]) or u["domain_id"].startswith("personal"):
                    raise ApiError(400, "each upsert needs a dot-path domain_id (lowercase, at most 4 levels, not personal.*)")
                did = u["domain_id"]
                parent = did.rsplit(".", 1)[0] if "." in did else None
                if parent and parent not in known:
                    raise ApiError(400, f"parent domain {parent!r} does not exist")
                name = str(u.get("name") or did.rsplit(".", 1)[-1])[:120]
                cx.execute("INSERT INTO domain_taxonomy(tenant_id, domain_id, parent_id, name, description, status, created_at, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?) "
                           "ON CONFLICT(tenant_id, domain_id) DO UPDATE SET name=excluded.name, description=excluded.description, status='active', updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                           (p.tenant_id, did, parent, name, str(u.get("description") or "")[:500], now, now, p.id))
                known.add(did)
            for did in deprecate:
                if did not in known:
                    raise ApiError(400, f"unknown domain {did!r}")
                cx.execute("UPDATE domain_taxonomy SET status='deprecated', updated_at=?, updated_by=? WHERE tenant_id=? AND domain_id=?", (now, p.id, p.tenant_id, did))
            for a in aliases:
                if not isinstance(a, dict) or not isinstance(a.get("alias"), str) or a.get("domain_id") not in known:
                    raise ApiError(400, "each alias needs {alias, domain_id} with a known domain")
                cx.execute("INSERT OR REPLACE INTO domain_aliases(tenant_id, alias, domain_id) VALUES (?, ?, ?)", (p.tenant_id, a["alias"].lower()[:64], a["domain_id"]))
        await rt.org.set_policy(p.tenant_id, "taxonomy_version", int(rt.org.policy(p.tenant_id, "taxonomy_version", 1) or 1) + 1, actor_id=p.id)
        await rt.db.audit(p.tenant_id, "user", p.id, "taxonomy.update", resource_type="tenant", resource_id=p.tenant_id,
                          detail={"upsert": len(upsert), "deprecate": len(deprecate), "aliases": len(aliases)}, request_id=request.get("request_id"))
        return json_response(taxonomy_payload(p.tenant_id))

    app.router.add_get(f"{prefix}/integrations/catalog", catalog)
    app.router.add_get(f"{prefix}/integrations/oauth/{{connector_type}}/callback", oauth_callback)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/connectors", list_connectors)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/connectors", add_connector)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}", get_connector)
    app.router.add_patch(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}", patch_connector)
    app.router.add_delete(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}", delete_connector)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}/sources", list_sources)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}/sources/discover", discover_sources)
    app.router.add_patch(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}/sources", patch_sources)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}/sources/{{source_id}}/delete", delete_source)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}/sync", sync_connector)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/connectors/{{connector_id}}/webhook", create_webhook)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/ingest/queue", queue_status)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/imports", upload_import)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/records", list_records)
    app.router.add_get(f"{prefix}/holders/{{holder_id}}/records/{{record_id}}", get_record)
    app.router.add_post(f"{prefix}/holders/{{holder_id}}/records/{{record_id}}/domains", correct_record_domains)
    app.router.add_post(f"{prefix}/webhooks/{{connector_type}}/{{endpoint_id}}", receive_webhook)
    app.router.add_get(f"{prefix}/admin/integrations", admin_integrations)
    app.router.add_get(f"{prefix}/domains", get_domains)
    app.router.add_put(f"{prefix}/admin/domains", put_domains)

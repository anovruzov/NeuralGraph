"""Integrations API: who may connect what, sources, sync, the admin view, webhooks, the taxonomy, and the holder side of
the control channel (sealed credentials, owner checks, confined file paths).

Offline: the ``local_export`` connector reads files from the holder's own import directory; webhook signatures are
computed here; no third-party service is contacted.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from mycelic.ingest.contract import WebhookNotice
from mycelic.ingest.crypto import seal_transfer
from mycelic.tests.test_api import api  # noqa: F401  (fixture)
from mycelic.transport import Envelope, Subjects
from mycelic.transport.base import CONTENT_KINDS, SHORT_LIVED_KINDS


async def _setup(api) -> dict[str, Any]:
    admin_tok, reg = await api.register("acme")
    root = reg["root_unit"]["unit_id"] if "root_unit" in reg else None
    status, team, _ = await api.call("POST", "/api/org/units", token=admin_tok, body={"type": "team", "name": "Platform", "parent_id": root})
    assert status == 201, team
    team = team.get("unit", team)
    ana_tok, _ = await api.invite_and_accept(admin_tok, email="ana@acme.example", role="employee", unit_id=team["unit_id"], name="Ana")
    bo_tok, _ = await api.invite_and_accept(admin_tok, email="bo@acme.example", role="employee", unit_id=team["unit_id"], name="Bo")
    lead_tok, _ = await api.invite_and_accept(admin_tok, email="lee@acme.example", role="team_lead", unit_id=team["unit_id"], name="Lee")
    status, h, _ = await api.call("POST", "/api/holders", token=ana_tok, body={"name": "Ana's notes", "domains": ["engineering"]})
    assert status == 201, h
    holder = h.get("holder", h)
    return {"admin": admin_tok, "ana": ana_tok, "bo": bo_tok, "lead": lead_tok, "team": team, "holder": holder}


def _export(api, holder_id: str, name: str = "eng.jsonl") -> Path:
    d = Path(api.rt.settings.holders_dir) / holder_id / "imports"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    lines = [{"type": "source", "external_id": "eng-notes", "name": "Engineering notes", "visibility": "public"}]
    for i in range(3):
        lines.append({"id": f"n{i}", "text": f"Deployment of checkout failed after the dependency upgrade, note {i}; requests time out.",
                      "created_at": f"2026-09-0{i + 1}T10:00:00+00:00", "author": "ana"})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


async def test_catalog_states_status_honestly(api):
    s = await _setup(api)
    status, body, _ = await api.call("GET", "/api/integrations/catalog", token=s["ana"])
    assert status == 200
    by_type = {i["connector_type"]: i for i in body["items"]}
    assert by_type["local_export"]["status"] == "tested-offline" and by_type["local_export"]["connectable"] is True
    assert all(i["connectable"] is False for i in body["items"] if i["status"] == "scaffold")


async def test_owner_connects_syncs_and_others_cannot(api):
    s = await _setup(api)
    hid = s["holder"]["holder_id"]
    _export(api, hid)
    base = f"/api/holders/{hid}/connectors"
    # nobody but the owner manages a personal memory's connectors: not a teammate, not the lead, not the admin
    for tok in (s["bo"], s["lead"], s["admin"]):
        status, _, _ = await api.call("POST", base, token=tok, body={"connector_type": "local_export", "config": {"paths": ["eng.jsonl"]}})
        assert status in (403, 404)
    status, body, _ = await api.call("POST", base, token=s["ana"], body={"connector_type": "local_export",
                                                                         "config": {"source_app": "notes", "account_id": "acme", "paths": ["eng.jsonl"]}})
    assert status == 201, body
    cid = body["connector"]["connector_id"]
    assert body["discovered"]["discovered"] == 1 and body["next"]["action"] == "select_sources"
    status, srcs, _ = await api.call("GET", f"{base}/{cid}/sources", token=s["ana"])
    assert status == 200 and srcs["items"][0]["selection"] == "pending_review" and srcs["items"][0]["name"]
    status, out, _ = await api.call("PATCH", f"{base}/{cid}/sources", token=s["ana"],
                                    body={"changes": [{"source_id": srcs["items"][0]["source_id"], "selection": "included"}]})
    assert status == 200, out
    status, out, _ = await api.call("POST", f"{base}/{cid}/sync", token=s["ana"], body={"mode": "incremental"})
    assert status == 202 and out["processed"] >= 3, out
    status, lst, _ = await api.call("GET", base, token=s["ana"])
    assert lst["items"][0]["counts"]["records"] == 3
    # the admin sees metadata and counts across the tenant, never source names
    status, adm, _ = await api.call("GET", "/api/admin/integrations", token=s["admin"])
    assert status == 200 and adm["items"][0]["connector_id"] == cid and adm["items"][0]["records"] == 3
    assert "Engineering notes" not in json.dumps(adm)
    # the audit trail records the install without content
    rows = api.rt.db.all("SELECT detail FROM audit_log WHERE action='connector.install'")
    assert rows and "Engineering notes" not in rows[0]["detail"]
    # why this domain: records with their domains, each membership explained, and a sticky correction
    status, recs, _ = await api.call("GET", f"/api/holders/{hid}/records", token=s["ana"])
    assert status == 200 and len(recs["items"]) == 3 and all(r["domains"] for r in recs["items"])
    rid = recs["items"][0]["record_id"]
    status, detail, _ = await api.call("GET", f"/api/holders/{hid}/records/{rid}", token=s["ana"])
    assert status == 200 and detail["domains"][0]["method"] and "evidence" in detail["domains"][0] and detail["history"]
    assert "version:httpclient@4.2" not in detail["entities"] or detail["entities"]            # linked entities, when the text names any
    status, fixed, _ = await api.call("POST", f"/api/holders/{hid}/records/{rid}/domains", token=s["ana"],
                                      body={"add": ["engineering.dependencies"], "reason": "it is about a library upgrade"})
    assert status == 200 and any(d["domain_id"] == "engineering.dependencies" and d["method"] == "human" for d in fixed["domains"])
    assert any(h["actor_type"] == "user" for h in fixed["history"])
    status, _, _ = await api.call("GET", f"/api/holders/{hid}/records/{rid}", token=s["bo"])
    assert status in (403, 404)
    # disconnect with data deletion purges the records
    status, out, _ = await api.call("DELETE", f"{base}/{cid}?data=delete", token=s["ana"])
    assert status == 200 and out["deletion"]["records_deleted"] == 3
    status, adm, _ = await api.call("GET", "/api/admin/integrations", token=s["admin"])
    assert adm["items"][0]["status"] == "disconnected"


async def test_file_paths_cannot_leave_the_holder_import_directory(api):
    s = await _setup(api)
    hid = s["holder"]["holder_id"]
    for bad in (["../../coord.db"], ["/etc/passwd"], [str(Path(api.rt.settings.coord_db))]):
        status, body, _ = await api.call("POST", f"/api/holders/{hid}/connectors", token=s["ana"],
                                         body={"connector_type": "local_export", "config": {"paths": bad}})
        assert status == 400, (bad, body)
        assert "import directory" in body["error"]


async def test_unit_holder_connectors_belong_to_its_leads(api):
    s = await _setup(api)
    status, h, _ = await api.call("POST", "/api/holders", token=s["lead"], body={"name": "Platform team memory", "owner_type": "unit",
                                                                                "owner_id": s["team"]["unit_id"], "domains": ["engineering"]})
    assert status == 201, h
    hid = h.get("holder", h)["holder_id"]
    _export(api, hid)
    body = {"connector_type": "local_export", "config": {"source_app": "notes", "account_id": "acme", "paths": ["eng.jsonl"]}}
    status, _, _ = await api.call("POST", f"/api/holders/{hid}/connectors", token=s["ana"], body=body)
    assert status == 403                                                       # a member is not a lead
    status, out, _ = await api.call("POST", f"/api/holders/{hid}/connectors", token=s["lead"], body=body)
    assert status == 201, out
    assert api.rt.db.one("SELECT scope FROM connector_installs")["scope"] == "organization"


class _HookConnector:
    """Just the webhook half of a connector: HMAC-SHA256 over the raw body, ids-only notices."""
    from mycelic.ingest.contract import ConnectorManifest  # noqa: F401

    @classmethod
    def verify_webhook(cls, headers: Mapping[str, str], body: bytes, secret: bytes, *, now: float) -> bool:
        sig = headers.get("x-test-signature", "")
        return hmac.compare_digest(sig, hmac.new(secret, body, hashlib.sha256).hexdigest())

    @classmethod
    def parse_webhook(cls, headers: Mapping[str, str], body: bytes) -> list[WebhookNotice]:
        d = json.loads(body)
        return [WebhookNotice(connector_type="hooktest", delivery_id=headers["x-test-delivery"], external_account_id=d["account"],
                              source_external_id=d["source"], action=d["action"], object_refs=({"id": d["id"]},))]


async def test_webhook_is_verified_deduplicated_and_forwarded_without_content(api, monkeypatch):
    from mycelic.api import routes_integrations as ri
    s = await _setup(api)
    hid = s["holder"]["holder_id"]
    tid = api.rt.org.get_holder(hid)["tenant_id"]

    class Reg:
        def get(self, t):
            if t != "hooktest":
                raise KeyError(t)
            return _HookConnector
    monkeypatch.setattr(ri, "_registry", lambda: Reg())
    secret = "whsec-test"
    async with api.rt.db.tx() as c:
        c.execute("INSERT INTO webhook_endpoints(endpoint_id, tenant_id, connector_type, scope, external_account_id, secret_kid, secret_ct, status, created_at) "
                  "VALUES ('whk_t', ?, 'hooktest', 'connector', 'acct', 'server', ?, 'active', '2026-10-08T00:00:00+00:00')",
                  (tid, ri._seal_server_secret(api.rt, "whk_t", secret)))
        c.execute("INSERT INTO webhook_routes(endpoint_id, external_account_id, source_external_id, holder_id, connector_id, created_at) "
                  "VALUES ('whk_t', 'acct', '*', ?, 'con_x', '2026-10-08T00:00:00+00:00')", (hid,))
    body = json.dumps({"account": "acct", "source": "repo-1", "action": "edited", "id": "42", "text": "SECRET BODY"}).encode()
    good = {"x-test-signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), "x-test-delivery": "d-1", "Content-Type": "application/json"}

    async def post(headers):
        async with api.client.post("/api/webhooks/hooktest/whk_t", data=body, headers=headers) as r:
            return r.status, await r.json()
    status, out = await post({**good, "x-test-signature": "0" * 64})
    assert status == 401
    assert api.rt.db.one("SELECT 1 FROM audit_log WHERE action='webhook.rejected'") is not None
    status, out = await post(good)
    assert status == 200 and out["routed"] == 1
    status, out = await post(good)                                            # the provider retries the same delivery
    assert status == 200 and out["routed"] == 0
    rows = api.rt.db.all("SELECT payload FROM transport_messages WHERE msg_id LIKE 'notice:%'")
    assert len(rows) == 1 and "SECRET BODY" not in rows[0]["payload"] and '"connector_notice"' in rows[0]["payload"]
    async with api.client.post("/api/webhooks/hooktest/whk_unknown", data=body, headers=good) as r:
        assert r.status == 404


async def test_taxonomy_is_admin_managed_and_drives_routing(api):
    s = await _setup(api)
    status, tax, _ = await api.call("GET", "/api/domains", token=s["ana"])
    assert status == 200 and tax["configured"] is False and any(d["domain_id"] == "engineering" for d in tax["items"])
    status, _, _ = await api.call("PUT", "/api/admin/domains", token=s["ana"], body={"upsert": [{"domain_id": "legal"}]})
    assert status == 403
    status, body, _ = await api.call("PUT", "/api/admin/domains", token=s["admin"], body={"upsert": [{"domain_id": "personal.x"}]})
    assert status == 400
    status, tax2, _ = await api.call("PUT", "/api/admin/domains", token=s["admin"],
                                     body={"upsert": [{"domain_id": "engineering.payments", "name": "Payments"}], "aliases": [{"alias": "billing", "domain_id": "engineering.payments"}]})
    assert status == 200 and tax2["configured"] is True and tax2["taxonomy_version"] == tax["taxonomy_version"] + 1
    holder = {**api.rt.org.get_holder(s["holder"]["holder_id"]), "domains": ["billing"], "export_policy": {}}
    q = {"tenant_id": holder["tenant_id"], "scope_unit_id": None, "policy": {"visibility": "org"}, "candidate_domains": ["engineering"]}
    assert api.rt.authz.can_route(q, holder)[0]                                # the new alias resolves under engineering


# ------------------------------------------------------------------------------------------------ the holder side
class _StubRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, Any]] = []

    async def control(self, action, params, *, actor, credentials=None):
        self.calls.append((action, dict(params), actor, credentials))
        return {"ok": True}

    async def on_notice(self, payload):
        return {"seen": payload.get("connector_id")}


async def test_holder_opens_sealed_credentials_once_and_never_stores_them(tmp_path):
    from mycelic.evidence import EvidenceStore
    from mycelic.holder.service import HolderService
    from mycelic.tests.test_holder import MemoryTransport
    assert {"connector_control", "connector_reply"} <= CONTENT_KINDS and {"connector_control", "connector_reply"} <= SHORT_LIVED_KINDS
    store = EvidenceStore(tmp_path / "h.db", holder_id="hold_x", tenant_id="ten_x")
    svc = HolderService(store, MemoryTransport(), holder_id="hold_x", tenant_id="ten_x", route_key="rk-x", ingest=_StubRuntime())
    env = Envelope.new(Subjects.holder_control("ten_x", "hold_x"), "connector_control", "ten_x",
                       {"action": "connector.add", "actor": "usr_ana", "params": {"connector_type": "github"}}, msg_id="cc_1")
    env.payload["credentials_sealed"] = seal_transfer("rk-x", "hold_x", "cc_1", {"kind": "pat", "access_token": "ghp_" + "a" * 36})
    out = await svc.handle(env.sign("rk-x"))
    assert out["result"] == {"ok": True}
    action, _, actor, creds = svc.ingest.calls[0]
    assert action == "connector.add" and actor == "usr_ana" and creds["access_token"].startswith("ghp_")
    stored = await store.store.processed_outcome("cc_1")
    assert "ghp_" not in json.dumps(stored)
    # the same sealed blob in another envelope does not open (bound to the msg_id)
    env2 = Envelope.new(Subjects.holder_control("ten_x", "hold_x"), "connector_control", "ten_x",
                        {"action": "connector.add", "actor": "usr_ana", "params": {}, "credentials_sealed": env.payload["credentials_sealed"]}, msg_id="cc_2")
    out2 = await svc.handle(env2.sign("rk-x"))
    assert out2["code"] == "credential_tampered" and len(svc.ingest.calls) == 1
    await store.close()


async def test_runtime_refuses_actions_from_a_non_owner(tmp_path):
    from mycelic.evidence import EvidenceStore
    from mycelic.ingest.runtime import ControlError, IngestRuntime
    store = EvidenceStore(tmp_path / "h.db", holder_id="hold_y", tenant_id="ten_y", owner_ids=["usr_owner"])
    runtime = IngestRuntime(store, holder_kind="user", import_root=tmp_path / "imports")
    (tmp_path / "imports").mkdir()
    (tmp_path / "imports" / "a.jsonl").write_text(json.dumps({"id": "1", "text": "hello there", "created_at": "2026-09-01T00:00:00+00:00"}) + "\n")
    with pytest.raises(ControlError):
        await runtime.control("connector.add", {"connector_type": "local_export", "config": {"paths": ["a.jsonl"]}}, actor="usr_someone_else")
    out = await runtime.control("connector.add", {"connector_type": "local_export", "config": {"paths": ["a.jsonl"], "account_id": "a"}}, actor="usr_owner")
    assert out["connector"]["status"] == "active"
    await store.close()


async def test_export_upload_lands_in_the_import_directory_only(api):
    import aiohttp
    s = await _setup(api)
    hid = s["holder"]["holder_id"]

    async def upload(name, body, tok, replace=False):
        form = aiohttp.FormData()
        form.add_field("file", body, filename=name, content_type="application/json")
        if replace:
            form.add_field("replace", "1")
        async with api.client.post(f"/api/holders/{hid}/imports", data=form, headers={"Cookie": f"mycelic_session={tok}"}) as r:
            return r.status, await r.json()
    status, out = await upload("../../../coord.jsonl", b'{"id": "1", "text": "hello"}\n', s["ana"])
    assert status == 201 and out["import"]["name"] == "coord.jsonl"
    assert (Path(api.rt.settings.holders_dir) / hid / "imports" / "coord.jsonl").exists()
    assert (await upload("coord.jsonl", b"{}", s["ana"]))[0] == 409
    assert (await upload("coord.jsonl", b"{}", s["ana"], replace=True))[0] == 201
    assert (await upload("x.exe", b"MZ", s["ana"]))[0] == 400
    assert (await upload("y.jsonl", b"{}", s["bo"]))[0] in (403, 404)


async def test_github_oauth_connect_and_signed_webhook_through_the_api(api):
    """OAuth against the offline GitHub mock (PKCE, single-use state), then a signed webhook delivery edits a comment:
    the API verifies the signature, routes a content-free notice, and the holder re-fetches the comment itself."""
    from urllib.parse import parse_qs, urlparse

    import aiohttp

    from mycelic.ingest.contract import Secret
    from mycelic.ingest.mocks import load_fixture, loopback_http_factory, start_github_mock
    from mycelic.ingest.oauth import OAuthAppConfig, register_oauth_app

    fx = load_fixture("github_acme")
    gh_url, gh = await start_github_mock(fx)
    # private repositories need the 'repo' scope (it also grants write access, so it is the server's explicit choice)
    register_oauth_app("github", OAuthAppConfig(client_id=fx["app"]["client_id"], client_secret=Secret(fx["app"]["client_secret"]),
                                                oauth_base=gh_url, allow_loopback_http=True, scopes=("repo",)))
    api.rt.holders.http_factory = loopback_http_factory()       # the holder may reach the loopback mock (tests and the demo only)
    try:
        s = await _setup(api)
        hid = s["holder"]["holder_id"]
        status, out, _ = await api.call("POST", f"/api/holders/{hid}/connectors", token=s["ana"],
                                        body={"connector_type": "github", "auth": {"kind": "oauth2"},
                                              "config": {"api_base": gh_url, "auto_include": ["acme/checkout"]}})
        assert status == 200 and out["next"]["action"] == "redirect", out
        # the browser goes to the provider, which approves and redirects back with code and state
        async with aiohttp.ClientSession() as sess:
            async with sess.get(out["next"]["url"], allow_redirects=False) as r:
                back = urlparse(r.headers["Location"])
        q = {k: v[0] for k, v in parse_qs(back.query).items()}
        status, body, resp = await api.call("GET", f"/api/integrations/oauth/github/callback?code={q['code']}&state={q['state']}", token=s["ana"])
        assert status == 302 or resp.status == 302 or "connected=" in str(resp.url), (status, body)
        status, again, _ = await api.call("GET", f"/api/integrations/oauth/github/callback?code={q['code']}&state={q['state']}", token=s["ana"])
        assert status == 400                                                              # the state is single use
        status, lst, _ = await api.call("GET", f"/api/holders/{hid}/connectors", token=s["ana"])
        con = next(c for c in lst["items"] if c["connector_type"] == "github")
        assert con["status"] == "active" and con["auth_kind"] == "oauth2"
        assert "gh-" not in json.dumps([dict(r) for r in api.rt.db.all("SELECT * FROM audit_log")]) and "gh-" not in json.dumps([dict(r) for r in api.rt.db.all("SELECT * FROM connector_registry")])
        cid = con["connector_id"]
        status, srcs, _ = await api.call("GET", f"/api/holders/{hid}/connectors/{cid}/sources", token=s["ana"])
        status, out, _ = await api.call("POST", f"/api/holders/{hid}/connectors/{cid}/sync", token=s["ana"], body={"mode": "backfill"})
        assert status == 202 and out["processed"] > 0 and any(x["selection"] == "included" for x in srcs["items"]), out
        # live updates: a webhook endpoint with a generated secret, then a signed edit
        status, hook, _ = await api.call("POST", f"/api/holders/{hid}/connectors/{cid}/webhook", token=s["ana"])
        assert status == 201 and hook["secret"]
        path = urlparse(hook["url"]).path
        payload = gh.edit_comment(880102, "Rolled back httpclient to 4.1; the zebracorn timeouts stopped.")
        headers, raw = gh.signed_delivery("issue_comment", payload, secret=hook["secret"])
        bad_headers, bad_raw = gh.signed_delivery("issue_comment", payload, secret=hook["secret"], tamper=True)
        async with api.client.post(path, data=bad_raw, headers=bad_headers) as r:
            assert r.status == 401
        async with api.client.post(path, data=raw, headers=headers) as r:
            assert r.status == 200 and (await r.json())["routed"] == 1
        runtime = api.rt.holders.ingest(hid)
        for _ in range(50):                                                       # the embedded holder consumes the notice
            hits = await runtime.pipeline.evidence.search("zebracorn timeouts stopped", k=3, audience={"owner": True, "complete": True, "principal_ids": []})
            if hits:
                break
            await asyncio.sleep(0.1)
        assert hits, "the edited comment was re-fetched and re-indexed"
        rows = api.rt.db.all("SELECT payload FROM transport_messages WHERE msg_id LIKE 'notice:%'")
        assert rows and all("zebracorn" not in r["payload"] for r in rows)       # the notice itself carried no content
    finally:
        register_oauth_app("github", None)
        await gh.close()


async def test_gmail_oauth_connect_and_push_with_the_url_token_through_the_api(api):
    """Gmail through the API against the offline Google mock: OAuth with PKCE, a backfill, then a Pub/Sub push whose only
    credential is the endpoint secret in the push URL (``?token=``). A wrong token is refused; the right one routes an
    ids-only notice and the holder fetches the new message itself."""
    from datetime import datetime, timezone
    from urllib.parse import parse_qs, urlparse

    import aiohttp

    from mycelic.ingest.contract import Secret
    from mycelic.ingest.mocks import load_fixture, loopback_http_factory, start_gmail_mock
    from mycelic.ingest.oauth import OAuthAppConfig, register_oauth_app

    fx = load_fixture("gmail_acme", now=datetime.now(timezone.utc))
    gm_url, gm = await start_gmail_mock(fx)
    register_oauth_app("gmail", OAuthAppConfig(client_id=fx["app"]["client_id"], client_secret=Secret(fx["app"]["client_secret"]),
                                               oauth_base=gm_url, api_base=gm_url, allow_loopback_http=True))
    api.rt.holders.http_factory = loopback_http_factory()
    try:
        s = await _setup(api)
        hid = s["holder"]["holder_id"]
        status, out, _ = await api.call("POST", f"/api/holders/{hid}/connectors", token=s["ana"],
                                        body={"connector_type": "gmail", "auth": {"kind": "oauth2"},
                                              "config": {"api_base": gm_url, "auto_include": ["Customers"]}})
        assert status == 200 and out["next"]["action"] == "redirect", out
        async with aiohttp.ClientSession() as sess:
            async with sess.get(out["next"]["url"], allow_redirects=False) as r:
                back = urlparse(r.headers["Location"])
        q = {k: v[0] for k, v in parse_qs(back.query).items()}
        await api.call("GET", f"/api/integrations/oauth/gmail/callback?code={q['code']}&state={q['state']}", token=s["ana"])
        status, lst, _ = await api.call("GET", f"/api/holders/{hid}/connectors", token=s["ana"])
        con = next(c for c in lst["items"] if c["connector_type"] == "gmail")
        assert con["status"] == "active" and con["auth_kind"] == "oauth2", con
        cid = con["connector_id"]
        status, out, _ = await api.call("POST", f"/api/holders/{hid}/connectors/{cid}/sync", token=s["ana"], body={"mode": "backfill"})
        assert status == 202 and out["processed"] > 0, out
        status, hook, _ = await api.call("POST", f"/api/holders/{hid}/connectors/{cid}/webhook", token=s["ana"])
        assert status == 201 and hook["secret"]
        url = f"{str(api.client.make_url(urlparse(hook['url']).path))}"
        label = next(lab["id"] for lab in fx["labels"] if lab.get("name") == "Customers")
        d = gm.deliver(sender="Globex Operations <ops@globex.example>", to=["Ana Lima <ana@acme.example>"], subject="Remediation plan",
                       text="We accept the quokka remediation plan; the renewal can go ahead.", labels=("INBOX", label))
        assert await gm.send_push(url, gm.push_delivery(token="not-the-secret")) == 401
        assert await gm.send_push(url, gm.push_delivery(token=hook["secret"])) == 200
        runtime = api.rt.holders.ingest(hid)
        for _ in range(50):
            hits = await runtime.pipeline.evidence.search("quokka remediation plan", k=3, audience={"owner": True, "complete": True, "principal_ids": []})
            if hits:
                break
            await asyncio.sleep(0.1)
        assert hits, "the pushed message was fetched through the history feed and indexed"
        rows = api.rt.db.all("SELECT payload FROM transport_messages WHERE msg_id LIKE 'notice:%'")
        assert rows and all("quokka" not in r["payload"] for r in rows)
        assert d is not None
    finally:
        register_oauth_app("gmail", None)
        await gm.close()


def test_one_google_oauth_client_serves_gmail_and_drive():
    from types import SimpleNamespace

    from mycelic.api.routes_integrations import register_oauth_apps
    from mycelic.ingest.oauth import oauth_app, register_oauth_app

    settings = SimpleNamespace(github_client_id="", slack_client_id="", google_client_id="google-client-id", google_client_secret="google-secret")
    try:
        register_oauth_apps(settings)
        for ctype in ("gmail", "google_drive"):
            app = oauth_app(ctype)
            assert app is not None and app.client_id == "google-client-id" and app.client_secret.reveal() == "google-secret"
        assert oauth_app("github") is None and oauth_app("slack") is None          # not configured, not registered
    finally:
        register_oauth_app("gmail", None)
        register_oauth_app("google_drive", None)

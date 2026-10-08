"""The connector contract: manifest validation, a new app plugged in through the registry alone, static import boundaries,
and the reference export connector's contract surface (authorize, health, windows, notify-then-fetch, confirmed deletions)."""
from __future__ import annotations

import ast
import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mycelic
from mycelic.ingest.connectors import LocalExportConnector, register_builtin
from mycelic.ingest.contract import (AuthStart, Capabilities, ConnectResult, Connector, ConnectorManifest, Cursor, Page, RawItem, ScopeSpec,
                                     SourceDescriptor, WebhookNotice)
from mycelic.ingest.events import CanonicalEvent, Permissions
from mycelic.ingest.registry import ConnectorRegistry, ManifestError, validate_manifest
from mycelic.ingest.service import IngestService

from .ingest_support import OWNER, connect_export, make_pipeline, make_store, table_contains, write_jsonl

MYCELIC_ROOT = Path(mycelic.__file__).parent


class ToyConnector(Connector):
    """A whole new app in a few lines: nothing outside this class and one register() call changes."""
    manifest = ConnectorManifest(connector_type="toy_notes", display_name="Toy notes", version="0.1.0", status="tested-offline", auth_kinds=("none",),
                                 scopes=(), modes=frozenset({"pull"}), source_types=("notebook",),
                                 capabilities=Capabilities(edits=False, deletes="none", threads=False, attachments=False, acl="visibility_only",
                                                           exports=False))
    NOTES = [("n1", "Toy note: the reindeer deployment finished on time."), ("n2", "Toy note: the sleigh pipeline needs a rollback plan.")]

    async def authorize(self, **kw):
        return AuthStart(kind="none")

    async def connect(self, ctx):
        return ConnectResult(source_account_id="toy", auth_account_id="", account_label="toy")

    async def discover_sources(self, ctx):
        yield SourceDescriptor("notebook", "nb1", name="Notebook", visibility="public")

    async def initial_backfill(self, ctx, source, window, cursor):
        return
        yield

    async def incremental_sync(self, ctx, source, cursor):
        if cursor is None:
            items = [RawItem("note", {"id": i, "text": t}, source, "2026-01-01T00:00:00+00:00") for i, t in self.NOTES]
            yield Page("incr:nb1", items, Cursor(1, {"done": True}), has_more=False)

    def normalize(self, raw, ctx):
        return [CanonicalEvent.create(kind="message", tenant_id=ctx.tenant_id, holder_id=ctx.holder_id, connector_id=ctx.connector_id,
                                      source_app=ctx.source_app, source_account_id=ctx.source_account_id, source_object_type="note",
                                      source_object_id=raw.payload["id"], observed_at=raw.fetched_at, created_at=raw.fetched_at,
                                      body=raw.payload["text"], permissions=Permissions(raw.source.visibility))]


def test_manifest_validation() -> None:
    good = ToyConnector.manifest
    assert validate_manifest(good) == []
    assert validate_manifest(LocalExportConnector.manifest) == [] and LocalExportConnector.manifest.status == "tested-offline"
    bad = {
        "version": "one", "status": "beta", "allowed_hosts": ("http://api.example.com",),
        "scopes": (ScopeSpec("read", True, ""),), "capabilities": dataclasses.replace(good.capabilities, deletes="webhook"),
        "connector_type": "Bad-Type", "ownership": ("team",),
    }
    for field, value in bad.items():
        assert validate_manifest(dataclasses.replace(good, **{field: value})), field
    reg = ConnectorRegistry()

    class Scaffold(ToyConnector):
        manifest = dataclasses.replace(ToyConnector.manifest, connector_type="future_app", status="scaffold")
    reg.register(Scaffold)
    with pytest.raises(ManifestError):
        reg.create("future_app")                                       # a scaffold never runs
    with pytest.raises(ManifestError):
        reg.register(type("Broken", (ToyConnector,), {"manifest": dataclasses.replace(ToyConnector.manifest, version="x")}))
    with pytest.raises(ManifestError):
        reg.register(type("Dup", (ToyConnector,), {"manifest": dataclasses.replace(Scaffold.manifest, status="implemented")}))
    assert [c["status"] for c in reg.catalog()] == ["scaffold"]


async def test_a_new_app_plugs_in_through_the_registry_alone(tmp_path: Path) -> None:
    reg = register_builtin(ConnectorRegistry())
    reg.register(ToyConnector)
    store = make_store(tmp_path)
    pipe = make_pipeline(store, registry=reg)
    svc = IngestService(pipe)
    con = await svc.add_connector("toy_notes", created_by=OWNER, config={"auto_include": True})
    await svc.discover_sources(con["connector_id"])
    await pipe.sync(con["connector_id"])
    report = await pipe.process_available()
    assert report.outcomes["new"] == 2
    hits = await store.search("reindeer deployment")
    assert hits and store.store._conn.execute("SELECT DISTINCT source_app FROM ingest_records").fetchone()[0] == "toy_notes"
    await store.close()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = ".".join(path.relative_to(MYCELIC_ROOT.parent).with_suffix("").parts[:-1])
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = pkg.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1] + ([node.module] if node.module else []))
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)
    return out


def test_import_boundaries() -> None:
    """The discovery loop, inquiry, knowledge and goals never import the ingestion layer, and the pipeline never imports a
    concrete connector: adding an app needs no core change."""
    for pkg in ("discovery", "inquiry", "knowledge", "goals"):
        for f in (MYCELIC_ROOT / pkg).rglob("*.py"):
            assert not any(m == "mycelic.ingest" or m.startswith("mycelic.ingest.") for m in _imports(f)), f
    for name in ("pipeline", "queue", "service", "shards", "store", "domains", "events", "acl", "crypto", "registry", "contract", "normalize"):
        mods = _imports(MYCELIC_ROOT / "ingest" / f"{name}.py")
        assert not any("mycelic.ingest.connectors" in m for m in mods), name


# ---------------------------------------------------------------------------------------------- the export connector's contract
async def test_export_connector_contract_surface(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    with pytest.raises(Exception):
        await svc.add_connector("local_export", created_by=OWNER, config={"paths": [str(tmp_path / "missing.jsonl")]})
    with pytest.raises(Exception):
        await svc.add_connector("local_export", created_by=OWNER, config={"source_app": "Bad App!", "paths": []})
    assert store.store._conn.execute("SELECT COUNT(*) FROM connectors").fetchone()[0] == 0     # failed connects leave nothing
    # dates well before any plausible connect time: backfill windows run from the connect time back to the oldest record
    recs = [{"type": "message", "id": f"w{i}", "author": "a", "created_at": f"2024-0{1 + i}-15T00:00:00Z", "text": f"Window note {i} on deployments."}
            for i in range(4)]
    f = write_jsonl(tmp_path / "w.jsonl", recs, header={"id": "w", "source_type": "channel", "visibility": "public"})
    con = await connect_export(svc, [f], source_app="teamchat", limits={"backfill_window_days": 30, "page_size": 1})
    inst = pipe.connector(con["connector_id"])
    ctx = pipe.context(pipe.db.get_connector(con["connector_id"]))
    assert (await inst.authorize(tenant_id="t", holder_id="h", redirect_uri="", state="s")).kind == "file_upload"
    assert (await inst.health(ctx)).status == "ok"
    src = svc.sources(con["connector_id"])[0].descriptor()
    anchor = datetime(2024, 6, 1, tzinfo=timezone.utc)
    w1 = inst.plan_backfill(ctx, src, anchor=anchor)
    assert w1 == inst.plan_backfill(ctx, src, anchor=anchor) and w1[0].end > w1[-1].start          # stable and newest first
    rep = await pipe.sync(con["connector_id"], mode="backfill")
    await pipe.process_available()
    assert rep.error_code is None and rep.enqueued == 4 and len(rep.streams) >= 4
    again = await pipe.sync(con["connector_id"], mode="backfill")
    assert again.enqueued == 0 and again.pages == 0                                             # finished windows are skipped
    f.unlink()
    assert (await inst.health(ctx)).status == "error"
    await store.close()


async def test_notices_fetch_authoritative_state_and_confirm_deletions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    f = write_jsonl(tmp_path / "n.jsonl", [{"type": "message", "id": "k1", "author": "a", "created_at": "2026-02-01T00:00:00Z",
                                            "text": "Keep this capybara note."},
                                           {"type": "message", "id": "k2", "author": "a", "created_at": "2026-02-02T00:00:00Z",
                                            "text": "This narwhal note will disappear from the export."}],
                    header={"id": "notes", "source_type": "channel", "visibility": "public"})
    con = await connect_export(svc, [f], source_app="teamchat")
    await pipe.sync(con["connector_id"])
    await pipe.process_available()
    # the next export no longer contains k2: a notice naming it confirms the deletion against the readable file
    write_jsonl(f, [{"type": "message", "id": "k1", "author": "a", "created_at": "2026-02-01T00:00:00Z", "text": "Keep this capybara note."}],
                header={"id": "notes", "source_type": "channel", "visibility": "public"})
    rep = await svc.handle_notice(con["connector_id"], WebhookNotice("local_export", "n-1", "acme", "notes", "deleted", ({"type": "message", "id": "k2"},)))
    await pipe.process_available()
    assert rep.enqueued == 1 and table_contains(store, "narwhal") == [] and table_contains(store, "capybara")
    # an unreadable file is access loss, never a deletion
    f.unlink()
    lost = await svc.handle_notice(con["connector_id"], WebhookNotice("local_export", "n-2", "acme", "notes", "changed", ({"type": "message", "id": "k1"},)))
    assert lost.error_code == "source_unavailable" and table_contains(store, "capybara")
    assert svc.sources(con["connector_id"])[0].access_state == "lost"
    await store.close()

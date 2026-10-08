"""The Google Drive connector against the offline Drive mock, end to end through ``IngestPipeline`` into a real holder store:
connect and scopes, discovery of shared drives and named folders (no silent whole-drive ingestion), the resumable crawl
(Docs export, text/CSV/JSON/Markdown downloads, PDF and DOCX extraction, size and type limits), the changes feed,
expired page tokens, crash recovery, Google's rate limits, permissions as record ACLs and their narrowing (file sharing,
shared-drive membership, groups, hidden permissions), deletions, trash and moves (in, out, between included sources),
``changes.watch`` channels (token check, ids-only parsing, notify-then-fetch), OAuth, the mock's shapes, and no content in
logs."""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from mycelic.ingest.connectors import register_builtin
from mycelic.ingest.connectors.google_drive import GoogleDriveConnector
from mycelic.ingest.connectors.scaffolds import SCAFFOLD_CONNECTORS
from mycelic.ingest.contract import InsufficientScope, PermanentError, Secret
from mycelic.ingest.crypto import TokenVault
from mycelic.ingest.mocks import load_fixture, loopback_http_factory
from mycelic.ingest.oauth import OAuthAppConfig
from mycelic.ingest.registry import ConnectorRegistry, validate_manifest
from mycelic.ingest.service import IngestService
from mycelic.util import fingerprint

from .connector_support import (BEN, CUSTOMER_NOTES, CY, DEE, DRIVE_SCOPE, GD_TOKEN, VAULT_SECRET, Collect, Env, drive_env, full_sync,
                                google_creds)
from .ingest_support import OWNER, TENANT, make_pipeline, make_store, question, table_contains

OWNER_READ = {"principal_ids": [], "complete": True, "owner": True}
CHANNEL_SECRET = b"drive-channel-secret-0001"
ENG, SALES, NOTES, INCIDENTS, ANA_ROOT = "0AEngDriveAcmeUk9PVA", "0ASalesDriveAcmeUk9PVA", CUSTOMER_NOTES, "1IncidentsFolderAcmeEng01", "0AAnaMyDriveRootUk9PVA"
F_REVIEW, F_CSV, F_RUNBOOK, F_ROADMAP = "1CheckoutIncidentReviewDoc", "1GatewayLatencySeptCsv01", "1CheckoutDeployRunbookDoc", "1CheckoutQ4RoadmapDoc0001"
F_TEMPLATE, F_PNG, F_OFFSITE, F_TRASHED = "1PostmortemTemplateMd0001", "1ArchitectureDiagramPng01", "1Offsite2024AgendaDoc0001", "1OldDraftTrashedDoc000001"
F_PDF, F_DOCX, F_PLAYBOOK, F_VENDOR = "1GlobexContractSummaryPdf", "1GlobexCallNotesDocx00001", "1SalesPlaybookDoc00000001", "1VendorSlaNotesDoc0000001"
F_BUDGET, F_FORECAST = "1PersonalBudgetDoc0000001", "1RenewalsForecastDoc00001"
EXPECTED = sorted([F_REVIEW, F_CSV, F_RUNBOOK, F_ROADMAP, F_TEMPLATE, F_PDF, F_DOCX, F_PLAYBOOK, F_VENDOR])
REVIEW_Q = "What does the checkout incident review say about httpclient 4.2, the connection pool and the payment gateway timeouts?"


async def sync(env: Env, mode: str = "incremental"):
    report = await env.pipe.sync(env.cid, mode=mode)
    await env.pipe.process_available()
    return report


def rec(env: Env, file_id: str) -> dict:
    return next(r for r in env.records() if r["source_object_id"] == file_id)


def tombstone_reason(env: Env, file_id: str) -> str | None:
    row = env.store.store._conn.execute("SELECT t.reason FROM deletion_tombstones t JOIN ingest_records r ON r.record_key=t.record_key "
                                        "WHERE r.source_object_id=?", (file_id,)).fetchone()
    return row["reason"] if row else None


def any_tombstone(env: Env, file_id: str) -> bool:
    from mycelic.ingest.events import record_key
    key = record_key(TENANT, env.store.holder_id, "google_drive", "127.0.0.1", "drive_file", file_id)
    return env.store.store._conn.execute("SELECT 1 FROM deletion_tombstones WHERE record_key=?", (key,)).fetchone() is not None


def root_of(env: Env, file_id: str) -> str:
    return rec(env, file_id)["source_root_id"]


def cited(resp: dict, root: str) -> list[dict]:
    return [r for r in resp["evidence_refs"] if r["source_root_id"] == root]


async def ask(env: Env, q: str, audience):
    return await env.store.answer_question(question(q, audience=audience))


def members(*ids: str) -> dict:
    return {"principal_ids": list(ids), "complete": True}


# ---------------------------------------------------------------------------------------------- manifest
def test_manifest_is_registered_and_honest() -> None:
    m = register_builtin(ConnectorRegistry()).get("google_drive").manifest
    assert validate_manifest(m) == [] and m.status == "tested-offline" and set(m.ownership) == {"personal", "org"}
    assert [(s.scope, s.required) for s in m.scopes] == [(DRIVE_SCOPE, True)] and m.source_types == ("shared_drive", "folder")
    assert m.allowed_hosts == ("www.googleapis.com", "oauth2.googleapis.com", "accounts.google.com") and m.capabilities.acl == "full"
    assert "not live-verified" in m.terms_notes and "not renewed" in m.terms_notes and "administrator maps the group" in m.terms_notes
    assert "google_drive" not in {c.manifest.connector_type for c in SCAFFOLD_CONNECTORS}


# ---------------------------------------------------------------------------------------------- connect and discover
async def test_connect_scopes_and_ownership(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, token=None)
    try:
        cfg = {"api_base": env.url}
        con = await env.svc.add_connector("google_drive", created_by=OWNER, config=cfg, credentials=google_creds(GD_TOKEN, DRIVE_SCOPE))
        assert (con["source_account_id"], con["auth_account_id"], con["granted_scopes"]) == ("127.0.0.1", "ana@acme.example", [DRIVE_SCOPE])
        assert any("only shared drives" in w for w in con["warnings"])
        assert [(r.path, r.query.get("fields")) for r in env.mock.requests] == [("/drive/v3/about", "user(displayName,emailAddress,permissionId)")]
        await env.svc.disconnect(con["connector_id"], actor=OWNER)
        with pytest.raises(InsufficientScope):             # metadata cannot read contents
            await env.svc.add_connector("google_drive", created_by=OWNER, config=cfg,
                                        credentials=google_creds("ya29.mock-ana-drive-metadata", "https://www.googleapis.com/auth/drive.metadata.readonly"))
        full = await env.svc.add_connector("google_drive", created_by=OWNER, config={**cfg, "folder_ids": [NOTES]},
                                           credentials=google_creds("ya29.mock-ana-drive-full", "https://www.googleapis.com/auth/drive"))
        assert any("allow changes" in w for w in full["warnings"]) and not any("only shared drives" in w for w in full["warnings"])
        await env.svc.disconnect(full["connector_id"], actor=OWNER)
        with pytest.raises(PermanentError):
            await env.svc.add_connector("google_drive", created_by=OWNER, config={**cfg, "folder_ids": ["../etc"]},
                                        credentials=google_creds(GD_TOKEN, DRIVE_SCOPE))
        # shared drives feed unit holders too (organization-wide connections)
        unit_store = make_store(tmp_path, "unit_holder")
        unit_pipe = make_pipeline(unit_store, holder_kind="unit", vault=TokenVault.from_secret(VAULT_SECRET),
                                  http_factory=loopback_http_factory(env.clock), clock=env.clock.datetime)
        try:
            unit = await IngestService(unit_pipe).add_connector("google_drive", created_by=OWNER, config=cfg,
                                                                credentials=google_creds("ya29.mock-ben-drive-readonly", DRIVE_SCOPE))
            assert unit["ownership"] == "org" and unit["auth_account_id"] == "ben@acme.example"
        finally:
            await unit_pipe.aclose()
            await unit_store.close()
    finally:
        await env.close()


async def test_discovery_offers_shared_drives_and_named_folders_only(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, include=False, config={"folder_ids": [NOTES, F_REVIEW, "1DoesNotExist000000000"]})
    try:
        srcs = {s.external_id: s for s in env.svc.sources(env.cid)}
        assert set(srcs) == {ENG, SALES, NOTES}                           # a named file is no folder; an unknown id is skipped; no My Drive
        eng, notes = srcs[ENG], srcs[NOTES]
        assert (eng.source_type, eng.name, eng.visibility, eng.member_ids, eng.exportable) == ("shared_drive", "Engineering", "members",
                                                                                               sorted([OWNER, BEN, CY, DEE]), True)
        assert (notes.source_type, notes.name, notes.visibility, notes.member_ids) == ("folder", "Customer notes", "members", sorted([OWNER, BEN]))
        assert all(s.selection == "pending_review" for s in srcs.values())
        mine = await drive_env(tmp_path / "b", include=False, config={"include_my_drive": True})
        try:
            root = next(s for s in mine.svc.sources(mine.cid) if s.name == "My Drive")
            assert (root.external_id, root.visibility, root.member_ids, root.exportable) == (ANA_ROOT, "private", [OWNER], False)
        finally:
            await mine.close()
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- pull
async def test_crawl_contents_types_horizon_then_changes(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, config={"limits": {"page_size": 2}})
    try:
        await full_sync(env)
        assert env.live_ids("drive_file") == EXPECTED
        for absent in (F_PNG, F_OFFSITE, F_TRASHED, F_BUDGET, F_FORECAST):   # image; older than the horizon; trash; outside; not included
            assert absent not in env.live_ids()
        rows = env.records()
        assert len(rows) == len({(r["source_object_type"], r["source_object_id"]) for r in rows})
        text = {f: await env.store.document_text(rec(env, f)["record_id"], audience=OWNER_READ) for f in EXPECTED}
        assert text[F_REVIEW].startswith("Checkout incident review") and "﻿" not in text[F_REVIEW]   # Docs export, byte-order mark dropped
        assert "date: 2026-09-29; p99_ms: 30000" in text[F_CSV]                                           # CSV rows keep their column names
        assert "Renewal date: 1 November 2026" in text[F_PDF] and "Action: Ana sends the review by Friday." in text[F_DOCX]
        assert text[F_TEMPLATE].startswith("# Postmortem template")
        review = rec(env, F_REVIEW)
        assert (review["kind"], review["current_version"], review["author_entity_id"]) == ("document", "31", "person:ben@acme.example")
        assert review["source_root_id"] == fingerprint(text[F_REVIEW])
        files = [r for r in env.mock.requests if r.path == "/drive/v3/files"]
        eng = [r for r in files if r.query.get("driveId") == ENG]
        notes = [r for r in files if r.query.get("q", "").startswith(f"'{NOTES}'")]
        assert eng and all(r.query["corpora"] == "drive" and r.query["includeItemsFromAllDrives"] == "true" for r in eng)
        assert notes and all(r.query["corpora"] == "user" and r.query["q"] == f"'{NOTES}' in parents and trashed = false" for r in notes)
        assert all("files(id,name,mimeType,parents" in r.query["fields"] and r.query["supportsAllDrives"] == "true" for r in files)
        assert any(r.query.get("pageToken") for r in files) and any(r.query["q"].startswith(f"'{INCIDENTS}'") for r in files)
        exports = {r.path.split("/")[4] for r in env.mock.requests if r.path.endswith("/export")}
        media = {r.path.split("/")[4] for r in env.mock.requests if r.query.get("alt") == "media"}
        assert F_REVIEW in exports and F_PDF in media and F_DOCX in media and F_CSV in media and not exports & media and F_PNG not in media
        # the changes feed: an edit is fetched and revises the record
        env.clock.advance(600)
        env.mock.edit_file(F_RUNBOOK, "Checkout deploy runbook\n\nDeploy checkout to the canary first; roll back when the zebracorn alarm fires.",
                           by="ben@acme.example")
        n = len(env.mock.requests)
        r = await sync(env)
        assert r.error_code is None and r.enqueued >= 1
        changes = [x for x in env.mock.requests[n:] if x.path == "/drive/v3/changes"]
        assert changes and {x.query.get("driveId") for x in changes} == {ENG, None}
        assert all(x.query["includeRemoved"] == "true" and x.query["pageToken"].isdigit() for x in changes)
        assert [h["doc_id"] for h in await env.store.search("zebracorn alarm", audience=OWNER_READ)] == [rec(env, F_RUNBOOK)["record_id"]]
        assert "person:ben@acme.example" in json.loads(rec(env, F_RUNBOOK)["participant_entity_ids"])       # the last editor
        assert (await sync(env)).enqueued == 0
    finally:
        await env.close()


async def test_expired_page_token_is_cursor_invalid_then_a_catch_up_crawl(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, include=["Engineering"])
    try:
        await full_sync(env)
        env.clock.advance(3600)
        env.mock.edit_file(F_ROADMAP, "Q4 checkout roadmap: retry budget, connection pool tuning and a quokka-themed launch.")
        env.mock.expire_changes()
        broken = await env.pipe.sync(env.cid)
        assert broken.error_code == "cursor_invalid" and env.connector_row()["status"] == "active"
        n = len(env.mock.requests)
        healed = await sync(env)
        assert healed.error_code is None
        paths = [r.path for r in env.mock.requests[n:]]
        assert paths[0] == "/drive/v3/changes/startPageToken" and "/drive/v3/changes" not in paths   # a new token first, then the crawl
        assert [h["doc_id"] for h in await env.store.search("quokka-themed launch", audience=OWNER_READ)] == [rec(env, F_ROADMAP)["record_id"]]
        rows = env.records()
        assert len(rows) == len({r["source_object_id"] for r in rows})
        env.clock.advance(60)
        env.mock.edit_file(F_TEMPLATE, "# Postmortem template v2\n\n## Customer impact\n")
        n = len(env.mock.requests)
        assert (await sync(env)).error_code is None
        assert any(r.path == "/drive/v3/changes" and r.status == 200 for r in env.mock.requests[n:])   # back on the changes feed
    finally:
        await env.close()


class CrashingDrive(GoogleDriveConnector):
    crash_after: int | None = None

    async def initial_backfill(self, ctx, source, window, cursor):
        n = 0
        async for page in super().initial_backfill(ctx, source, window, cursor):
            if type(self).crash_after is not None and n >= type(self).crash_after:
                raise RuntimeError("simulated crash in the middle of a crawl")
            n += 1
            yield page


async def test_crash_mid_crawl_resumes_without_duplicates_or_loss(tmp_path: Path) -> None:
    reg = register_builtin(ConnectorRegistry())
    reg.register(CrashingDrive, replace=True)
    env = await drive_env(tmp_path, include=["Engineering"], registry=reg, config={"limits": {"page_size": 1}})
    try:
        CrashingDrive.crash_after = 3
        first = await env.pipe.sync(env.cid, mode="backfill")
        assert first.error_code == "connector_crash" and first.pages == 3
        [stream] = first.streams
        cursor, version = await env.pipe.queue.load_checkpoint(env.cid, stream)
        assert version == 3 and cursor.data["crawl"]["queue"]                     # the crawl state is the cursor
        await env.pipe.process_available()
        partial = env.live_ids("drive_file")
        CrashingDrive.crash_after = None
        second = await sync(env, mode="backfill")
        assert second.error_code is None and set(partial) < set(env.live_ids("drive_file"))
        assert env.live_ids("drive_file") == sorted([F_REVIEW, F_CSV, F_RUNBOOK, F_ROADMAP, F_TEMPLATE])
        downloads = Counter(r.path for r in env.mock.requests if r.path.endswith("/export") or r.query.get("alt") == "media")
        assert max(downloads.values()) <= 2
        rows = env.records()
        assert len(rows) == len({r["source_object_id"] for r in rows})
        # a provider crash (connections dropped) mid-crawl: the stream errors, the cursor holds, the resume completes
        env.store.store._conn.execute("DELETE FROM connector_checkpoints WHERE stream=?", (stream,))
        env.mock.crash_after_pages(4)
        broken = await env.pipe.sync(env.cid, mode="backfill")
        assert broken.error_code == "transient"
        env.mock.recover()
        healed = await sync(env, mode="backfill")
        assert healed.error_code is None and len(env.live_ids("drive_file")) == 5
    finally:
        CrashingDrive.crash_after = None
        await env.close()


async def test_google_rate_limits_back_off_inline_or_park(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, include=["Engineering"])
    try:
        env.mock.rate_limit_next(status=403, reason="rateLimitExceeded", path_contains="/export")
        ok = await sync(env)
        assert ok.error_code is None and any(1.0 <= s < 2.0 for s in env.clock.slept) and F_REVIEW in env.live_ids()
        env.clock.advance(600)
        env.mock.edit_file(F_RUNBOOK, "Checkout deploy runbook: canary first, then a kingfisher cohort, then everyone.")
        env.mock.rate_limit_next(status=429, reason="rateLimitExceeded", count=4)
        parked = await env.pipe.sync(env.cid)
        assert parked.error_code == "rate_limited" and parked.enqueued == 0 and env.connector_row()["status"] == "active"
        resumed = await sync(env)
        assert resumed.error_code is None
        assert rec(env, F_RUNBOOK)["record_id"] in [h["doc_id"] for h in await env.store.search("kingfisher cohort", audience=OWNER_READ)]
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- permissions
async def test_file_sharing_becomes_record_acls_that_only_narrow(tmp_path: Path) -> None:
    env = await drive_env(tmp_path)
    try:
        await full_sync(env)
        perms = {f: json.loads(rec(env, f)["permissions"]) for f in EXPECTED}
        assert perms[F_REVIEW]["member_ids"] == [OWNER, BEN] and perms[F_REVIEW]["visibility"] == "members"      # restricted to Ana and Ben
        assert perms[F_RUNBOOK]["member_ids"] == sorted([OWNER, BEN, CY, DEE])                                   # the drive's members
        assert perms[F_ROADMAP]["visibility"] == "members" and perms[F_ROADMAP]["member_ids"] == sorted([OWNER, BEN, CY, DEE])  # anyone, inside the drive
        assert perms[F_PLAYBOOK] == {"visibility": "members", "member_ids": [OWNER], "membership_ref": "google_drive:group:sales@acme.example",
                                     "acl_version": None}
        assert perms[F_VENDOR]["visibility"] == "private" and perms[F_VENDOR]["member_ids"] == [OWNER]           # Drive hid them: fail closed
        review = root_of(env, F_REVIEW)
        assert cited(await ask(env, REVIEW_Q, members(BEN)), review)
        assert not cited(await ask(env, REVIEW_Q, members(DEE)), review) and not cited(await ask(env, REVIEW_Q, members(BEN, CY)), review)
        assert cited(await ask(env, REVIEW_Q, {"owner": True}), review)
        playbook_q = "Who handles an account whose renewal is at risk, according to the sales playbook?"
        for who in (BEN, DEE):                             # the group is not mapped yet: only the owner reads it
            assert not cited(await ask(env, playbook_q, members(who)), root_of(env, F_PLAYBOOK))
        # a file whose sharing narrows: the change is a metadata update, effective at the next answer
        roadmap_q = "What is on the Q4 checkout roadmap for the payment gateway?"
        roadmap = root_of(env, F_ROADMAP)
        assert cited(await ask(env, roadmap_q, members(DEE)), roadmap)
        env.mock.set_permissions(F_ROADMAP, [{"type": "user", "role": "organizer", "emailAddress": "ana@acme.example"}], inherited_disabled=True)
        report = await env.pipe.sync(env.cid)
        out = await env.pipe.process_available()
        assert report.error_code is None and out.outcomes["metadata_only"] >= 1
        assert json.loads(rec(env, F_ROADMAP)["permissions"])["member_ids"] == [OWNER]
        assert not cited(await ask(env, roadmap_q, members(DEE)), roadmap) and cited(await ask(env, roadmap_q, {"owner": True}), roadmap)
        # a member leaves the shared drive: the source's ACL narrows every record of it at once, no file re-read
        runbook_q = "How should checkout be deployed according to the deploy runbook and the canary?"
        assert cited(await ask(env, runbook_q, members(DEE)), root_of(env, F_RUNBOOK))
        env.mock.remove_drive_member(ENG, "dee@acme.example")
        n = len(env.mock.requests)
        await env.svc.discover_sources(env.cid)
        assert not any("/export" in r.path or r.query.get("alt") == "media" for r in env.mock.requests[n:])
        assert not cited(await ask(env, runbook_q, members(DEE)), root_of(env, F_RUNBOOK))
        assert cited(await ask(env, runbook_q, members(CY)), root_of(env, F_RUNBOOK))
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- deletions and moves
async def test_trash_deletion_and_moves_in_out_and_between_sources(tmp_path: Path) -> None:
    env = await drive_env(tmp_path)
    try:
        await full_sync(env)
        env.mock.trash_file(F_RUNBOOK)                     # trashed: a deletion
        env.mock.delete_file(F_CSV)                        # deleted for good: 404 while the drive answers, a deletion
        n = len(env.mock.requests)
        r = await sync(env)
        assert r.error_code is None
        assert F_RUNBOOK not in env.live_ids() and tombstone_reason(env, F_RUNBOOK) == "deleted_at_source"
        assert F_CSV not in env.live_ids() and tombstone_reason(env, F_CSV) == "deleted_at_source"
        later = [(x.path, x.status) for x in env.mock.requests[n:]]
        assert (f"/drive/v3/files/{F_CSV}", 404) in later and (f"/drive/v3/drives/{ENG}", 200) in later
        assert table_contains(env.store, "p99_ms: 30000") == [] and table_contains(env.store, "Pin dependency versions") == []
        # moved out of every included source: it left the memory
        env.mock.move_file(F_DOCX, ANA_ROOT)
        await sync(env)
        assert F_DOCX not in env.live_ids() and tombstone_reason(env, F_DOCX) == "excluded_source"
        # moved from one included source into another: kept, now under the shared drive
        env.mock.move_file(F_PDF, INCIDENTS)
        await sync(env)
        pdf = rec(env, F_PDF)
        assert pdf["deletion_status"] == "live" and tombstone_reason(env, F_PDF) is None
        assert env.pipe.db.get_source(pdf["source_id"]).external_id == ENG
        # a change outside every source plants nothing (no tombstone that could block the file later)
        env.mock.edit_file(F_BUDGET, "Rent, groceries, the holiday fund and a new bike.")
        quiet = await sync(env)
        assert quiet.error_code is None and F_BUDGET not in env.live_ids() and not any_tombstone(env, F_BUDGET)
        # a folder moved out takes its files with it; a folder moved in brings its files
        env.mock.add_file(file_id="1GlobexFolderInNotes00001", name="Globex", parent=NOTES, mime_type="application/vnd.google-apps.folder",
                          owner="ana@acme.example")
        env.mock.add_file(file_id="1GlobexEscalationDoc00001", name="Globex escalation log", parent="1GlobexFolderInNotes00001",
                          text="Escalation log: Globex renewal call booked for Monday.", owner="ana@acme.example")
        await sync(env)
        assert "1GlobexEscalationDoc00001" in env.live_ids()
        env.mock.move_file("1GlobexFolderInNotes00001", ANA_ROOT)
        await sync(env)
        assert "1GlobexEscalationDoc00001" not in env.live_ids() and tombstone_reason(env, "1GlobexEscalationDoc00001") == "excluded_source"
        env.mock.add_file(file_id="1ArchiveFolderInRoot00001", name="Archive", parent=ANA_ROOT, mime_type="application/vnd.google-apps.folder",
                          owner="ana@acme.example")
        env.mock.add_file(file_id="1ArchivedQbrNotesDoc00001", name="QBR notes", parent="1ArchiveFolderInRoot00001",
                          text="QBR notes: Globex wants uptime reports every month.", owner="ana@acme.example")
        await sync(env)
        assert "1ArchivedQbrNotesDoc00001" not in env.live_ids()          # outside the sources
        env.mock.move_file("1ArchiveFolderInRoot00001", NOTES)
        await sync(env)
        assert "1ArchivedQbrNotesDoc00001" in env.live_ids()
        rows = env.records()
        assert len(rows) == len({r["source_object_id"] for r in rows})
    finally:
        await env.close()


async def test_a_deletion_is_never_inferred_from_an_unreadable_shared_drive(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, include=["Engineering"])
    try:
        await full_sync(env)
        env.mock.delete_file(F_CSV)
        env.mock.remove_drive_member(ENG, "ana@acme.example")          # the owner lost the drive at the same time
        report = await sync(env)
        assert report.error_code == "source_unavailable" and env.source(ENG).access_state == "lost"
        assert F_CSV in env.live_ids() and tombstone_reason(env, F_CSV) is None
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- push (changes.watch)
def test_channel_token_verification_and_ids_only_parsing() -> None:
    headers = {"X-Goog-Channel-ID": "mycelic-ch-01", "X-Goog-Channel-Token": CHANNEL_SECRET.decode(), "X-Goog-Resource-State": "change",
               "X-Goog-Resource-ID": "mockResource0011", "X-Goog-Message-Number": "7", "X-Goog-Resource-URI": "https://www.googleapis.com/drive/v3/changes"}
    assert GoogleDriveConnector.verify_webhook(headers, b"", CHANNEL_SECRET, now=0)
    assert GoogleDriveConnector.verify_webhook({k.lower(): v for k, v in headers.items()}, b"", CHANNEL_SECRET, now=0)
    assert not GoogleDriveConnector.verify_webhook({**headers, "X-Goog-Channel-Token": "guess"}, b"", CHANNEL_SECRET, now=0)
    assert not GoogleDriveConnector.verify_webhook({k: v for k, v in headers.items() if k != "X-Goog-Channel-Token"}, b"", CHANNEL_SECRET, now=0)
    assert not GoogleDriveConnector.verify_webhook({k: v for k, v in headers.items() if k != "X-Goog-Resource-State"}, b"", CHANNEL_SECRET, now=0)
    assert not GoogleDriveConnector.verify_webhook(headers, b"", b"", now=0)
    [n] = GoogleDriveConnector.parse_webhook(headers, b"")
    assert (n.connector_type, n.delivery_id, n.source_external_id, n.action) == ("google_drive", "mycelic-ch-01:7", "*", "changes.change")
    assert n.object_refs == ({"type": "channel", "id": "mycelic-ch-01", "resource_id": "mockResource0011"},)
    assert GoogleDriveConnector.parse_webhook({**headers, "X-Goog-Resource-State": "sync"}, b"") == []     # the handshake
    assert GoogleDriveConnector.parse_webhook({**headers, "X-Goog-Message-Number": "x"}, b"") == []
    assert GoogleDriveConnector.parse_webhook({}, b"") == []


async def test_watch_channel_notices_trigger_a_changes_fetch_once(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, include=["Engineering"])
    try:
        await full_sync(env)
        drive = env.pipe.connector(env.cid)
        ctx = env.pipe.context(env.connector_row())
        ch = await drive.watch_changes(ctx, env.source(ENG).descriptor(), address="https://mycelic.example/api/webhooks/google_drive/whk_1",
                                       token=CHANNEL_SECRET.decode(), channel_id="mycelic-ch-01")
        assert ch["id"] == "mycelic-ch-01" and ch["resourceId"] and env.mock.channels["mycelic-ch-01"]["drive"] == ENG
        sync_headers = env.mock.notification("mycelic-ch-01", state="sync")
        assert GoogleDriveConnector.verify_webhook(sync_headers, b"", CHANNEL_SECRET, now=env.clock()) and GoogleDriveConnector.parse_webhook(sync_headers, b"") == []
        sentinel = "Sentinelword5150"
        [headers] = env.mock.edit_file(F_ROADMAP, f"Q4 checkout roadmap: {sentinel} and a retry budget for the payment gateway.")
        assert GoogleDriveConnector.verify_webhook(headers, b"", CHANNEL_SECRET, now=env.clock())
        [notice] = GoogleDriveConnector.parse_webhook(headers, b"")
        assert sentinel not in repr(notice) and notice.source_external_id == "*"
        report = await env.svc.handle_notice(env.cid, notice)
        await env.pipe.process_available()
        assert report.error_code is None and report.enqueued >= 1
        assert [h["doc_id"] for h in await env.store.search(sentinel, audience=OWNER_READ)] == [rec(env, F_ROADMAP)["record_id"]]
        again = await env.svc.handle_notice(env.cid, notice)                 # a redelivered notification: the same delivery id
        assert again.duplicates == 1 and again.pages == 0
        await drive.stop_channel(ctx, channel_id=ch["id"], resource_id=ch["resourceId"])
        assert env.mock.channels == {}
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- OAuth
async def test_oauth_asks_for_drive_readonly_with_pkce(tmp_path: Path) -> None:
    fx = load_fixture("drive_acme")
    env = await drive_env(tmp_path, token=None, fixture=fx)
    try:
        app = OAuthAppConfig(client_id=fx["app"]["client_id"], client_secret=Secret(fx["app"]["client_secret"]), oauth_base=env.url,
                             api_base=env.url, allow_loopback_http=True)
        gd = GoogleDriveConnector(oauth=app)
        start = await gd.authorize(tenant_id=TENANT, holder_id="h", redirect_uri="https://mycelic.example/cb", state="s1")
        q = parse_qs(urlsplit(start.url).query)
        assert q["scope"] == [DRIVE_SCOPE] and q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
        async with aiohttp.ClientSession() as s:
            async with s.get(start.url, allow_redirects=False) as resp:
                params = {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["Location"]).query).items()}
        got = await gd.complete_authorization(params, redirect_uri="https://mycelic.example/cb", pkce_verifier=start.pkce_verifier)
        assert got.extra["scope"] == DRIVE_SCOPE and got.refresh_token is not None
        con = await env.svc.add_connector("google_drive", created_by=OWNER, credentials=got, config={"api_base": env.url})
        assert con["granted_scopes"] == [DRIVE_SCOPE] and con["auth_account_id"] == "ana@acme.example"
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- conformance of the mock
async def test_mock_speaks_drives_documented_shapes(tmp_path: Path) -> None:
    env = await drive_env(tmp_path, token=None)
    try:
        async with aiohttp.ClientSession() as s:
            async def get(path: str, **params):
                async with s.get(f"{env.url}/drive/v3{path}", params=params, headers={"Authorization": f"Bearer {GD_TOKEN}"}) as resp:
                    body = await resp.read()
                    return resp.status, resp.headers.get("Content-Type", ""), (json.loads(body) if b"{" in body[:1] else body)
            st, _, err = await get("/about")
            assert st == 400 and err["error"]["errors"][0]["reason"] == "required"                       # about needs fields
            _, _, about = await get("/about", fields="user(emailAddress)")
            assert about == {"user": {"emailAddress": "ana@acme.example"}}
            _, _, f = await get(f"/files/{NOTES}")
            assert set(f) == {"kind", "id", "name", "mimeType"}                                         # Drive's small default
            st, _, _ = await get(f"/files/{F_REVIEW}", fields="id")
            assert st == 404                                                                            # shared-drive items need supportsAllDrives
            _, _, full = await get(f"/files/{F_PDF}", fields="id,size,md5Checksum,owners(emailAddress)", supportsAllDrives="true")
            assert full["size"].isdigit() and len(full["md5Checksum"]) == 32 and full["owners"] == [{"emailAddress": "ana@acme.example"}]
            st, _, err = await get(f"/files/{F_REVIEW}", alt="media", supportsAllDrives="true")
            assert st == 403 and err["error"]["errors"][0]["reason"] == "fileNotDownloadable"
            st, ctype, body = await get(f"/files/{F_REVIEW}/export", mimeType="text/plain")
            assert st == 200 and ctype.startswith("text/plain") and body.startswith("﻿".encode())
            st, _, err = await get(f"/files/{F_PDF}/export", mimeType="text/plain")
            assert st == 403 and err["error"]["errors"][0]["reason"] == "fileNotExportable"
            st, _, err = await get("/files", q=f"'{ENG}' in parents", driveId=ENG, corpora="drive")
            assert st == 400                                                                            # driveId needs includeItemsFromAllDrives
            st, _, err = await get("/files", q=f"'{ENG}' in parents or name = 'x'")
            assert st == 400 and err["error"]["errors"][0]["reason"] == "invalid"
            _, _, listing = await get("/files", q=f"'{ENG}' in parents and trashed = false", driveId=ENG, corpora="drive", supportsAllDrives="true",
                                      includeItemsFromAllDrives="true", pageSize="2", fields="nextPageToken,files(id,mimeType)")
            assert set(listing) == {"nextPageToken", "files"} and all(set(x) == {"id", "mimeType"} for x in listing["files"])
            _, _, perms = await get(f"/files/{F_RUNBOOK}/permissions", supportsAllDrives="true", fields="permissions(type,role,emailAddress,permissionDetails)")
            assert {p["emailAddress"] for p in perms["permissions"]} == {"ana@acme.example", "ben@acme.example", "cy@acme.example", "dee@acme.example"}
            assert all(p["permissionDetails"][0]["inherited"] for p in perms["permissions"])
            st, _, err = await get(f"/files/{F_VENDOR}/permissions")
            assert st == 403 and err["error"]["errors"][0]["reason"] == "insufficientFilePermissions"
            _, _, tok = await get("/changes/startPageToken", supportsAllDrives="true", driveId=ENG)
            env.mock.edit_file(F_RUNBOOK, "Runbook edited.")
            _, _, ch = await get("/changes", pageToken=tok["startPageToken"], driveId=ENG, supportsAllDrives="true", includeItemsFromAllDrives="true",
                                 fields="newStartPageToken,changes(fileId,removed,file(id,version))")
            assert ch["changes"] == [{"fileId": F_RUNBOOK, "removed": False, "file": {"id": F_RUNBOOK, "version": "11"}}] and ch["newStartPageToken"]
            env.mock.expire_changes()
            st, _, err = await get("/changes", pageToken=tok["startPageToken"])
            assert st == 404 and err["error"]["code"] == 404
    finally:
        await env.close()


# ---------------------------------------------------------------------------------------------- logs
async def test_nothing_content_bearing_is_logged(tmp_path: Path) -> None:
    sentinel = "Sentinelword7841"
    fx = load_fixture("drive_acme")
    for f in fx["files"]:
        if f["id"] == F_REVIEW:
            f["text"] += f"\n{sentinel} in the review."
    handler = Collect()
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        env = await drive_env(tmp_path, fixture=fx)
        try:
            await full_sync(env)
            env.mock.fail_next(1, status=503)
            env.mock.edit_file(F_RUNBOOK, f"{sentinel} edited runbook")
            env.mock.trash_file(F_REVIEW)
            await sync(env)
            env.mock.revoke_token(GD_TOKEN)
            await env.pipe.sync(env.cid)
            metrics, outbox = json.dumps(env.pipe.db.metrics()), json.dumps(env.pipe.publisher.sent)
        finally:
            await env.close()
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    blob = "\n".join(handler.lines)
    assert handler.lines and "/drive/{id}/files/{id}" in blob
    for needle in (sentinel, GD_TOKEN, "httpclient 4.2", "Checkout incident review", "Customer notes", "Engineering", "Bearer"):
        assert needle not in blob, needle
    assert sentinel not in metrics and sentinel not in outbox


# ---------------------------------------------------------------------------------------------- live (opt-in, never required)
@pytest.mark.live
@pytest.mark.skipif(not (os.environ.get("MYCELIC_LIVE_DRIVE_TOKEN") and os.environ.get("MYCELIC_LIVE_DRIVE_FOLDER")),
                    reason="live Drive test: set MYCELIC_LIVE_DRIVE_TOKEN (an access token with drive.readonly) and MYCELIC_LIVE_DRIVE_FOLDER")
async def test_live_drive_read_only(tmp_path: Path) -> None:      # pragma: no cover - needs real credentials
    store = make_store(tmp_path, "live")
    pipe = make_pipeline(store, vault=TokenVault.from_secret("live-test-master-key-0001"))
    svc = IngestService(pipe)
    try:
        folder = os.environ["MYCELIC_LIVE_DRIVE_FOLDER"]
        con = await svc.add_connector("google_drive", created_by=OWNER, credentials=google_creds(os.environ["MYCELIC_LIVE_DRIVE_TOKEN"], DRIVE_SCOPE),
                                      config={"folder_ids": [folder], "auto_include": [folder], "limits": {"page_size": 5}, "max_files": 20})
        await svc.discover_sources(con["connector_id"])
        first = await pipe.sync(con["connector_id"])
        assert first.error_code is None
        assert (await pipe.sync(con["connector_id"])).error_code is None
    finally:
        await pipe.aclose()
        await store.close()

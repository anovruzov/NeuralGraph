#!/usr/bin/env python
"""Trace ONE raw source event through the real Mycelic system, in-process, in temp dirs only.

Run:  python research/mycelic_e2e/audit/trace_one_event.py        (writes trace_output.json next to this file)

What it builds (all production code, composed the way ``python -m mycelic serve`` composes it):
  * ``build_runtime(settings)``  -> CoordDB (coord.db), org/auth/authz, jobs, knowledge, goals, inquiry, LoopEngine,
                                    SQLite-outbox transport, DefaultModelRouter over the deterministic FakeProvider
  * ``create_app(rt, ..., run_worker=True, run_holders=True)`` -> the aiohttp API the UI talks to; the discovery worker
                                    and the embedded holders (one NeuralGraph store per holder, IngestRuntime) run inside it
  * every action is an HTTP call (register, units, invitations, holders, connectors, goals) except the file drop into the
    holder's import directory, which is what the UI upload endpoint does for a ``local_export`` source.

Nothing outside the temp dirs is touched; /home/user/NeuralGraph is never read or written.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
logging.disable(logging.CRITICAL)

from aiohttp import DummyCookieJar  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from mycelic.api.app import create_app  # noqa: E402
from mycelic.observability import Metrics  # noqa: E402
from mycelic.runtime import build_runtime  # noqa: E402
from mycelic.tests.test_api import Api, make_settings  # noqa: E402

EVENT_TEXT = "Acme Freight shipment into Rotterdam slipped another 9 days"
OUT: dict[str, Any] = {"meta": {"script": "research/mycelic_e2e/audit/trace_one_event.py", "event_text": EVENT_TEXT}, "stages": {}, "probes": {}}


# ====================================================================================================== helpers
def sql(path: str | Path, query: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Read-only SQL against a database file (never the live connection of the code under test)."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(query, params).fetchall()]
    except sqlite3.Error as exc:
        return [{"__sql_error__": str(exc)}]
    finally:
        c.close()


def tables(path: str | Path) -> dict[str, int]:
    out = {}
    for r in sql(path, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%fts%' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        n = sql(path, f'SELECT COUNT(*) AS n FROM "{r["name"]}"')
        out[r["name"]] = n[0].get("n", -1) if n else -1
    return out


def trim(v: Any, n: int = 160) -> Any:
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + "…"
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    if isinstance(v, dict):
        return {k: trim(x, n) for k, x in v.items() if k not in ("vector", "embedding")}
    if isinstance(v, list):
        return [trim(x, n) for x in v[:40]]
    return v


JSON_COLS = {"provenance", "support", "stats", "payload", "candidate_domains", "result", "domains", "published_domains", "permissions", "flags",
             "claim_ids", "evidence_ref_ids", "metadata", "derived_from", "budget_spent", "followup_question_ids", "detail", "input_ref_ids",
             "response_ids", "input_claim_ids", "meta", "trigger"}


def jsonify(r: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in r.items():
        if k in JSON_COLS and isinstance(v, str) and v[:1] in "{[":
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


def rows(path, query, params=()):
    return [trim(jsonify(r)) for r in sql(path, query, params)]


class World:
    """One running Mycelic: coordinator + API + discovery worker + embedded holders, on a temp data dir."""

    def __init__(self, root: Path, **settings: Any) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        base = dict(demo_mode=False, loop_question_timeout_seconds=4, worker_poll_seconds=0.05, worker_heartbeat_seconds=1.0,
                    ingest_tick_seconds=3600.0)       # no background connector polling: every sync is an explicit call
        base.update(settings)
        self.settings = make_settings(root, **base)
        self.api: Api | None = None
        self.client: TestClient | None = None
        self.rt = None
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, Any] = {}

    async def start(self) -> "World":
        self.rt = build_runtime(self.settings)
        app = create_app(self.rt, self.settings, run_worker=True, run_holders=True, metrics=Metrics(), allowed_hosts=None, dist_dir=self.root / "no-dist")
        server = TestServer(app, shutdown_timeout=5.0)
        self.client = TestClient(server, cookie_jar=DummyCookieJar())
        await self.client.start_server()
        self.api = Api(self.client, app)
        return self

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.close()          # on_cleanup -> rt.stop(): worker, holders (heartbeat offline), transport, db
        self.client = self.api = self.rt = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def call(self, who: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        st, data, _ = await self.api.call(method, path, token=self.tokens[who], body=body)
        return st, data

    # ---- quiet = nothing queued/leased in the durable job queue for a while, and no route still pending
    async def quiet(self, *, timeout: float = 60.0, settle: float = 1.2) -> bool:
        t0 = time.monotonic()
        calm_since = None
        while time.monotonic() - t0 < timeout:
            c = self.rt.jobs.counts()
            pending = self.rt.db.scalar("SELECT COUNT(*) FROM question_routes WHERE status IN ('pending','delivered')", (), 0)
            busy = c.get("queued", 0) + c.get("leased", 0) + int(pending or 0)
            if busy == 0:
                calm_since = calm_since or time.monotonic()
                if time.monotonic() - calm_since >= settle:
                    return True
            else:
                calm_since = None
            await asyncio.sleep(0.1)
        return False

    def holder_db(self, hid: str) -> Path:
        return Path(self.settings.holders_dir) / hid / "evidence.db"

    def imports(self, hid: str) -> Path:
        d = Path(self.settings.holders_dir) / hid / "imports"
        d.mkdir(parents=True, exist_ok=True)
        return d


# ====================================================================================================== world builder
DEPTS = {  # department -> teams
    "Logistics": ["Ocean Freight", "Port Operations"],
    "Sales": ["Key Accounts"],
    "Finance": ["Treasury"],
}
PEOPLE = [  # name, team, role, holder domains declared by the owner
    ("ana", "Ocean Freight", "employee", ["operations.logistics"]),
    ("bo", "Port Operations", "employee", ["operations.logistics"]),
    ("cy", "Key Accounts", "employee", ["operations.logistics"]),
    ("dee", "Treasury", "employee", ["finance"]),
]


async def build_tenant(w: World, slug: str = "acme", org_name: str = "Acme Freight Group", people=PEOPLE) -> dict[str, Any]:
    api = w.api
    tok, reg = await api.register(slug, org_name=org_name)
    w.tokens[f"admin@{slug}"] = tok
    admin = f"admin@{slug}"
    root = reg["root_unit"]["unit_id"]
    ids: dict[str, Any] = {"tenant_id": reg["tenant"]["tenant_id"], "root": root, "units": {}, "users": {}, "holders": {}, "slug": slug}
    st, region = await w.call(admin, "POST", "/api/org/units", {"type": "region", "name": "Europe", "parent_id": root})
    assert st == 201, region
    region = region.get("unit", region)
    ids["units"]["Europe"] = region["unit_id"]
    for dept, teams in DEPTS.items():
        st, d = await w.call(admin, "POST", "/api/org/units", {"type": "department", "name": dept, "parent_id": region["unit_id"]})
        assert st == 201, d
        d = d.get("unit", d)
        ids["units"][dept] = d["unit_id"]
        for t in teams:
            st, tt = await w.call(admin, "POST", "/api/org/units", {"type": "team", "name": t, "parent_id": d["unit_id"]})
            assert st == 201, tt
            ids["units"][t] = tt.get("unit", tt)["unit_id"]
    # the regional lead who will own the goal
    lena_tok, lena = await api.invite_and_accept(tok, email=f"lena@{slug}.example", role="regional_lead", unit_id=region["unit_id"], name="Lena")
    w.tokens[f"lena@{slug}"] = lena_tok
    ids["users"]["lena"] = lena["user"]["user_id"] if "user" in lena else lena
    for name, team, role, domains in people:
        t, body = await api.invite_and_accept(tok, email=f"{name}@{slug}.example", role=role, unit_id=ids["units"][team], name=name.title())
        w.tokens[f"{name}@{slug}"] = t
        ids["users"][name] = body["user"]["user_id"] if "user" in body else body
        st, h = await w.call(f"{name}@{slug}", "POST", "/api/holders", {"name": f"{name.title()}'s notes", "domains": domains})
        assert st == 201, h
        ids["holders"][name] = h["holder"]["holder_id"]
    return ids


# ====================================================================================================== source event
def source_files(app: str, account: str, channel_id: str, channel_name: str, author: str, event_id: str, msg_id: str, text: str,
                 ts: str = "2026-10-08T09:12:00+00:00", *, visibility: str = "public", member_ids=(), extra: dict | None = None):
    header = {"type": "source", "id": channel_id, "name": channel_name, "source_type": "channel", "visibility": visibility}
    if member_ids:
        header["member_ids"] = list(member_ids)
    rec = {"type": "message", "id": msg_id, "event_id": event_id, "author": author, "created_at": ts, "updated_at": ts, "text": text,
           "container_name": channel_name}
    rec.update(extra or {})
    return header, rec


def write_export(path: Path, header: dict, records: list[Any]) -> None:
    path.write_text("\n".join(json.dumps(x) if not isinstance(x, str) else x for x in [header, *records]) + "\n", encoding="utf-8")


async def connect_export(w: World, who: str, hid: str, fname: str, *, app="slack-export", account="T0ACME", principal_map=None) -> dict[str, Any]:
    base = f"/api/holders/{hid}/connectors"
    st, con = await w.call(who, "POST", base, {"connector_type": "local_export",
                                               "config": {"source_app": app, "account_id": account, "paths": [fname], "principal_map": principal_map or {}}})
    assert st == 201, con
    cid = con["connector"]["connector_id"]
    st, srcs = await w.call(who, "GET", f"{base}/{cid}/sources")
    for s in srcs["items"]:
        st, _ = await w.call(who, "PATCH", f"{base}/{cid}/sources", {"changes": [{"source_id": s["source_id"], "selection": "included"}]})
        assert st == 200
    return {"connector_id": cid, "base": base, "create": con, "sources": srcs["items"]}


async def sync(w: World, who: str, c: dict[str, Any]) -> dict[str, Any]:
    st, out = await w.call(who, "POST", f"{c['base']}/{c['connector_id']}/sync", {"mode": "incremental"})
    return {"http": st, **(out if isinstance(out, dict) else {"body": out})}


# ====================================================================================================== state dumps
def holder_state(w: World, hid: str, needle: str | None = None) -> dict[str, Any]:
    db = w.holder_db(hid)
    q = lambda s, p=(): rows(db, s, p)  # noqa: E731
    out = {
        "tables_nonempty": {k: v for k, v in tables(db).items() if v},
        "ingest_records": q("SELECT record_id, record_key, object_key, kind, connector_id, source_id, source_app, source_account_id, source_object_type, source_object_id, "
                            "author_entity_id, created_at_src, updated_at_src, current_version, version_count, content_hash, source_root_id, root_known, root_method, "
                            "primary_domain_id, visibility, sensitivity, flags, deletion_status, parent_record_id, thread_record_id, conversation_record_id, content_changed_at FROM ingest_records"),
        "ingest_versions": q("SELECT * FROM ingest_versions"),
        "applied_events": q("SELECT * FROM applied_events"),
        "ingest_queue": q("SELECT item_id, event_key, record_key, stream, kind, priority_class, status, attempts, outcome, last_error_code, payload_bytes FROM ingest_queue"),
        "ingest_stage_metrics": q("SELECT * FROM ingest_stage_metrics"),
        "domain_memberships": q("SELECT record_id, domain_id, confidence, method, is_primary, status, taxonomy_version FROM domain_memberships"),
        "shard_map": q("SELECT * FROM shard_map"),
        "record_locator": q("SELECT * FROM record_locator"),
        "domain_shard_counts": q("SELECT * FROM domain_shard_counts"),
        "documents": q("SELECT doc_id, title, kind, text, source_root_id, root_known, origin_id, observed_at, domains, status, version, chars FROM documents"),
        "chats": q("SELECT * FROM chats"),
        "messages": q("SELECT * FROM messages"),
        "memories": q("SELECT memory_id, text, kind, subject, chat_id, observed_at, event_time, status, version, embedding_dim, metadata FROM memories"),
        "record_memories": q("SELECT * FROM record_memories"),
        "entities": q("SELECT entity_id, name, type, mention_count FROM entities"),
        "memory_entities": q("SELECT * FROM memory_entities"),
        "record_entities": q("SELECT * FROM record_entities"),
        "relations": q("SELECT * FROM relations"),
        "ingest_outbox": q("SELECT msg_id, kind, created_at, sent_at, substr(payload,1,500) AS payload FROM ingest_outbox") if "ingest_outbox" in tables(db) else [],
        "exports": q("SELECT * FROM exports"),
    }
    if needle:
        out["needle_hits"] = needle_hits(db, needle)
    return out


def needle_hits(path: str | Path, needle: str) -> dict[str, int]:
    """table.column -> number of rows whose text contains ``needle`` (whole database file, FTS shadow tables included)."""
    hits: dict[str, int] = {}
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{name}")').fetchall()]
            except sqlite3.Error:
                continue
            for col in cols:
                try:
                    n = c.execute(f'SELECT COUNT(*) FROM "{name}" WHERE CAST("{col}" AS TEXT) LIKE ?', (f"%{needle}%",)).fetchone()[0]
                except sqlite3.Error:
                    continue
                if n:
                    hits[f"{name}.{col}"] = n
    finally:
        c.close()
    return hits


def needle_snips(path: str | Path, needle: str, width: int = 70) -> dict[str, list[str]]:
    """table.column -> up to 2 snippets around ``needle`` (case-insensitive) so a hit can be classified as content vs identifier."""
    out: dict[str, list[str]] = {}
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            try:
                cols = [r[1] for r in c.execute(f'PRAGMA table_info("{name}")').fetchall()]
            except sqlite3.Error:
                continue
            for col in cols:
                try:
                    vals = [r[0] for r in c.execute(f'SELECT CAST("{col}" AS TEXT) FROM "{name}" WHERE CAST("{col}" AS TEXT) LIKE ? LIMIT 2', (f"%{needle}%",)).fetchall()]
                except sqlite3.Error:
                    continue
                for v in vals:
                    i = v.lower().find(needle.lower())
                    out.setdefault(f"{name}.{col}", []).append(v[max(0, i - width): i + len(needle) + width].replace("\n", " "))
    finally:
        c.close()
    return out


def coord_state(w: World, goal_id: str | None = None, needle: str | None = None) -> dict[str, Any]:
    db = w.settings.coord_db
    q = lambda s, p=(): rows(db, s, p)  # noqa: E731
    g = (goal_id,) if goal_id else ()
    wh = " WHERE goal_id=?" if goal_id else ""
    out: dict[str, Any] = {
        "holders": q("SELECT holder_id, owner_type, owner_id, mode, status, domains, published_domains, last_heartbeat_at, stats FROM holders"),
        "transport_messages": q("SELECT id, subject, msg_id FROM transport_messages ORDER BY id"),
        "events_document_ingested": q("SELECT id, kind, ref_id, payload FROM events WHERE kind='document.ingested'"),
        "jobs": w.rt.jobs.counts(),
    }
    if goal_id:
        out.update({
            "goal": q("SELECT goal_id, title, status, owner_type, owner_id, scope_unit_id, budget_spent FROM goals WHERE goal_id=?", g),
            "goal_loop": q("SELECT goal_id, desired, state, explanation, stats FROM goal_loops WHERE goal_id=?", g),
            "questions": q("SELECT question_id, kind, depth, status, text, candidate_domains, scope_unit_id, parent_question_id, result FROM questions" + wh, g),
            "question_routes": q("SELECT r.route_id, r.question_id, r.holder_id, r.status, r.msg_id, r.attempts, r.error FROM question_routes r JOIN questions q ON q.question_id=r.question_id WHERE q.goal_id=?", g),
            "responses": q("SELECT r.response_id, r.question_id, r.holder_id, r.status, substr(r.content,1,300) AS content, r.confidence, r.evidence_ref_ids, r.provenance, r.msg_id FROM responses r "
                           "JOIN questions q ON q.question_id=r.question_id WHERE q.goal_id=?", g),
            "evidence_refs": q("SELECT ref_id, holder_id, source_root_id, root_known, kind, title, substr(disclosed_excerpt,1,200) AS excerpt, disclosure_level, status, observed_at, meta, version FROM evidence_refs"),
            "claims": q("SELECT claim_id, status, kind, substr(text,1,300) AS text, question_id, support, confidence, scope_unit_id, visibility FROM claims" + wh, g),
            "claim_evidence": q("SELECT ce.* FROM claim_evidence ce JOIN claims c ON c.claim_id=ce.claim_id" + (" WHERE c.goal_id=?" if goal_id else ""), g),
            "derivations": q("SELECT d.* FROM derivations d JOIN claims c ON c.claim_id=d.claim_id" + (" WHERE c.goal_id=?" if goal_id else ""), g),
            "conflicts": q("SELECT conflict_id, status, summary, claim_a_id, claim_b_id FROM conflicts"),
            "discoveries": q("SELECT discovery_id, kind, level, status, title, substr(summary,1,300) AS summary, claim_ids, question_id, followup_question_ids FROM discoveries" + wh, g),
            "commit_gate_audit": q("SELECT action, outcome, resource_id, detail FROM audit_log WHERE action LIKE 'claim%' OR action LIKE 'gate%' OR action LIKE '%commit%' ORDER BY id"),
            "model_calls_by_task": q("SELECT purpose, COUNT(*) AS n FROM model_usage GROUP BY purpose"),
        })
    if needle:
        out["needle_hits"] = needle_hits(db, needle)
    return out


# ====================================================================================================== goal + loop
async def create_goal(w: World, who: str, ids: dict[str, Any], title="Why are Rotterdam inbound shipments slipping?",
                      objective="Find out why Acme Freight shipments into Rotterdam keep slipping and by how long", scope="Europe") -> dict[str, Any]:
    st, g = await w.call(who, "POST", "/api/goals", {
        "title": title, "objective": objective, "owner_type": "unit", "owner_id": ids["units"][scope],
        "success_criteria": [{"metric": "delay_days", "target": 2, "direction": "decrease"}], "baseline": {"delay_days": 9},
        "budget": {"tokens": 500000, "usd": 5, "questions": 40, "followup_depth": 2}})
    assert st == 201, g
    return g["goal"]


def loop_summary(w: World, gid: str) -> dict[str, Any]:
    """Compact view of what the loop produced for one goal (coord.db)."""
    db = w.settings.coord_db
    qs = rows(db, "SELECT question_id, kind, depth, status, text, candidate_domains, result FROM questions WHERE goal_id=? ORDER BY created_at", (gid,))
    out = {"questions": qs}
    out["routes"] = rows(db, "SELECT r.holder_id, r.status, r.question_id FROM question_routes r JOIN questions q ON q.question_id=r.question_id WHERE q.goal_id=?", (gid,))
    out["responses"] = rows(db, "SELECT r.holder_id, r.question_id, r.status, substr(r.content,1,200) AS content, r.evidence_ref_ids, r.provenance FROM responses r "
                                "JOIN questions q ON q.question_id=r.question_id WHERE q.goal_id=?", (gid,))
    out["claims"] = rows(db, "SELECT claim_id, status, kind, substr(text,1,240) AS text, support, confidence FROM claims WHERE goal_id=?", (gid,))
    out["discoveries"] = rows(db, "SELECT discovery_id, kind, level, status, title, claim_ids FROM discoveries WHERE goal_id=?", (gid,))
    return out


def lexical_overlap(question: str, text: str) -> dict[str, Any]:
    from mycelic.models.fake import content_tokens
    a, b = set(content_tokens(question)), set(content_tokens(text))
    return {"question_tokens": sorted(a), "memory_tokens": sorted(b), "shared": sorted(a & b), "n_shared": len(a & b), "rule_needs": 2}


async def retrieval_diagnosis(w: World, hid: str, question: str) -> dict[str, Any]:
    """Production holder API, owner audience: is the memory retrievable with the loop's question, and with a content query?"""
    from mycelic.ingest.acl import Audience
    store = w.rt.holders.get(hid)
    aud = Audience.owner_only()
    out: dict[str, Any] = {}
    for label, q in (("loop_question", question), ("content_query", "Rotterdam shipment slipped days")):
        res = await store.search(q, k=5, audience=aud)
        out[label] = {"query": q, "hits": len(res), "top": [trim({k: v for k, v in r.items() if k in ("text", "score", "memory_id", "doc_id", "channels")}) for r in res[:2]]}
    return out


# ====================================================================================================== main trace
async def trace(variant: str, text: str, tmp: Path) -> dict[str, Any]:
    """variant 'literal' = the event exactly as given; 'lexical' = same event plus words the fake rules key on."""
    w = await World(tmp).start()
    rec: dict[str, Any] = {"variant": variant, "event_text": text, "stages": {}}
    try:
        ids = await build_tenant(w)
        rec["tenant"] = {k: ids[k] for k in ("tenant_id", "root", "units", "users", "holders")}
        hid, ana = ids["holders"]["ana"], "ana@acme"
        # ---- S0 raw event: a Slack-style channel message, as an export line
        header, event = source_files("slack-export", "T0ACME", "C0OCEAN", "#ocean-freight", "U0ANA", "Ev0ROTTERDAM9D", "1790674800.000100", text)
        write_export(w.imports(hid) / "ocean.jsonl", header, [event])
        rec["stages"]["0_raw_event"] = {"entry_point": "provider export file -> holder imports/ (UI: POST /api/holders/{id}/imports)",
                                         "source_app": "slack-export (local_export label)", "account": "T0ACME", "channel": header, "event": event,
                                         "file_sha256": hashlib.sha256((w.imports(hid) / "ocean.jsonl").read_bytes()).hexdigest()}
        # ---- S1..S4 connector, normalize, queue, domains/shard, holder store
        c = await connect_export(w, ana, hid, "ocean.jsonl", principal_map={"U0ANA": ids["users"]["ana"]})
        report = await sync(w, ana, c)
        hs = holder_state(w, hid, needle="Rotterdam")
        rec["stages"]["1_connector_normalize"] = {"entry_point": "POST /api/holders/{id}/connectors, .../sync -> IngestService.sync -> LocalExportConnector.fetch/normalize -> pipeline",
                                                   "connector": trim(c["create"]["connector"]), "sources": trim(c["sources"]), "sync_response": report,
                                                   "ingest_records": hs["ingest_records"], "ingest_versions": hs["ingest_versions"], "applied_events": hs["applied_events"]}
        rec["stages"]["2_queue"] = {"entry_point": "mycelic/ingest/queue.py (durable ingest_queue) drained by IngestPipeline.process_available",
                                    "ingest_queue": hs["ingest_queue"], "ingest_stage_metrics": hs["ingest_stage_metrics"]}
        rec["stages"]["3_domain_shard"] = {"entry_point": "mycelic/ingest/domains.py classify -> domain_memberships; mycelic/ingest/shards.py shard_map/record_locator",
                                           "domain_memberships": hs["domain_memberships"], "shard_map": hs["shard_map"], "record_locator": hs["record_locator"],
                                           "domain_shard_counts": hs["domain_shard_counts"]}
        rec["stages"]["4_holder_store"] = {"entry_point": "EvidenceStore (NeuralGraph MycelicMemoryStore) written by the pipeline's index stage",
                                           **{k: hs[k] for k in ("tables_nonempty", "documents", "chats", "messages", "memories", "record_memories", "entities",
                                                                 "memory_entities", "record_entities", "relations")}}
        # ---- S5 outbox -> transport -> coordinator, heartbeat, published domains
        await w.rt.holders.service(hid).heartbeat_once()          # the same call the holder's 20 s heartbeat loop makes
        await asyncio.sleep(0.5)
        cs0 = coord_state(w)
        rec["stages"]["5_outbox_transport_heartbeat"] = {
            "entry_point": "pipeline outbox -> HolderService.publish_ingest_output -> SqliteTransport -> LoopEngine._on_transport (ingest_result) ; heartbeat -> OrgService.holder_heartbeat",
            "ingest_outbox": hs["ingest_outbox"], "coordinator_transport_messages": cs0["transport_messages"], "coordinator_event_document_ingested": cs0["events_document_ingested"],
            "holder_registry_rows": [h for h in cs0["holders"] if h["holder_id"] == hid],
            "note": "published_domains stays [] until a domain has >= 5 records (EvidenceStore._ingest_stats_sync min_records_to_publish=5)"}
        rec["stages"]["5_needle_hits_after_ingest"] = {"holder_db": hs["needle_hits"], "coord_db": cs0["needle_hits"] if "needle_hits" in cs0 else needle_hits(w.settings.coord_db, "Rotterdam")}
        # ---- S6 goal + loop
        goal = await create_goal(w, "lena@acme", ids)
        gid = goal["goal_id"]
        t0 = time.monotonic()
        idle = await w.quiet(timeout=90)
        cs = coord_state(w, gid, needle="Rotterdam")
        rec["stages"]["6_goal_and_loop"] = {"entry_point": "POST /api/goals (routes_goals.create_goal -> GoalService.create_goal + action activate) ; LoopEngine.tick/route/collect/evaluate/commit via DiscoveryWorker",
                                            "goal_id": gid, "idle_reached": idle, "seconds_to_idle": round(time.monotonic() - t0, 2),
                                            **{k: cs[k] for k in ("goal_loop", "questions", "question_routes", "responses", "evidence_refs", "claims", "claim_evidence",
                                                                 "derivations", "conflicts", "discoveries", "commit_gate_audit", "model_calls_by_task", "jobs")}}
        rec["stages"]["6_needle_hits_coord"] = cs["needle_hits"]
        # ---- S7 diagnosis (production holder API, owner audience): where did the information stop?
        first_q = next((q for q in cs["questions"] if "operations.logistics" in json.dumps(q.get("candidate_domains"))), None)
        if first_q:
            rec["stages"]["7_retrieval_diagnosis"] = {
                "question_text": first_q["text"],
                "lexical_overlap_question_vs_memory": lexical_overlap(first_q["text"], text),
                **await retrieval_diagnosis(w, hid, first_q["text"])}
        rec["summary"] = loop_summary(w, gid)
        # ---- S8 control: the same goal, but a person asks a question that names the content (POST /api/questions, the UI's "ask")
        st, qb = await w.call("lena@acme", "POST", "/api/questions", {
            "text": "Why did the Acme Freight shipment into Rotterdam slip, and by how many days?", "goal_id": gid,
            "scope_unit_id": ids["units"]["Europe"], "candidate_domains": ["operations.logistics"]})
        await w.quiet(timeout=60)
        qid = (qb.get("question") or {}).get("question_id") if isinstance(qb, dict) else None
        cs8 = coord_state(w, gid)
        rec["stages"]["8_user_question_control"] = {
            "entry_point": "POST /api/questions (routes_questions.create_question -> QuestionService.create) then the same route/collect/evaluate/commit jobs",
            "http": st, "question_id": qid,
            "question": [q for q in cs8["questions"] if q["question_id"] == qid],
            "routes": [r for r in cs8["question_routes"] if r["question_id"] == qid],
            "responses": [r for r in cs8["responses"] if r["question_id"] == qid],
            "claims": [c for c in cs8["claims"] if c["question_id"] == qid],
            "discoveries": [d for d in cs8["discoveries"] if d["question_id"] == qid],
            "evidence_refs": cs8["evidence_refs"], "commit_gate_audit": cs8["commit_gate_audit"]}
        # ---- S9 provenance chain: coordinator evidence_ref -> holder exports row -> record -> provider object id
        chain = []
        for r in rows(w.settings.coord_db, "SELECT ref_id, holder_id, source_root_id, title, meta, status FROM evidence_refs"):
            ex = rows(w.holder_db(r["holder_id"]), "SELECT ref_id, memory_id, doc_id, question_id, disclosure_level, substr(disclosed_excerpt,1,80) AS excerpt FROM exports WHERE ref_id=?", (r["ref_id"],))
            ir = rows(w.holder_db(r["holder_id"]), "SELECT record_id, record_key, object_key, source_app, source_account_id, source_object_id, author_entity_id, created_at_src, source_root_id "
                                                   "FROM ingest_records WHERE record_id=?", (ex[0]["doc_id"],)) if ex else []
            chain.append({"coordinator_ref": r, "holder_export": ex, "holder_record": ir,
                          "root_matches_holder_record": bool(ir) and ir[0]["source_root_id"] == r["source_root_id"],
                          "object_key_matches_ref_meta": bool(ir) and ir[0]["object_key"] == (r["meta"] or {}).get("object_key"),
                          "fields_present_at_coordinator": sorted(set(r) | set(r["meta"] or {})),
                          "fields_only_in_holder": ["source_object_id", "source_account_id", "author_entity_id", "created_at_src", "record_key", "channel (connector_sources.name)", "event_id (ingest_versions.source_event_id)"]})
        rec["stages"]["9_provenance_chain"] = chain
        rec["ids"] = {"goal_id": gid, **{k: ids[k] for k in ("tenant_id",)}}
    finally:
        await w.stop()
    return rec



# ====================================================================================================== probes
LEX = "Recurring blocker: Acme Freight shipment into Rotterdam slipped another 9 days; customs paperwork caused the delay."


def verdict(v: str, observed: Any, note: str = "") -> dict[str, Any]:
    return {"verdict": v, "observed": observed, "note": note}


def hcounts(w: World, hid: str) -> dict[str, Any]:
    db = w.holder_db(hid)
    n = lambda s: (sql(db, s) or [{"n": -1}])[0].get("n", -1)  # noqa: E731
    return {"ingest_records": n("SELECT COUNT(*) n FROM ingest_records"), "live_records": n("SELECT COUNT(*) n FROM ingest_records WHERE deletion_status='live'"),
            "ingest_versions": n("SELECT COUNT(*) n FROM ingest_versions"), "documents_active": n("SELECT COUNT(*) n FROM documents WHERE status='active'"),
            "documents_total": n("SELECT COUNT(*) n FROM documents"), "memories_active": n("SELECT COUNT(*) n FROM memories WHERE status='active'"),
            "memories_total": n("SELECT COUNT(*) n FROM memories"), "applied_events": n("SELECT COUNT(*) n FROM applied_events"),
            "queue_outcomes": {f"{r['status']}/{r['outcome']}": r["n"] for r in sql(db, "SELECT status, outcome, COUNT(*) n FROM ingest_queue GROUP BY status, outcome")},
            "outbox": n("SELECT COUNT(*) n FROM ingest_outbox"), "entities": n("SELECT COUNT(*) n FROM entities"),
            "stage_metrics_fetch_dedupe_write": {f"{r['stage']}/{r['outcome']}": r["n"] for r in sql(db, "SELECT stage, outcome, SUM(count) n FROM ingest_stage_metrics WHERE stage IN ('fetch','dedupe','write','normalize') GROUP BY stage, outcome")}}


def ccounts(w: World, gid: str | None = None) -> dict[str, Any]:
    db = w.settings.coord_db
    n = lambda s, p=(): (sql(db, s, p) or [{"n": -1}])[0].get("n", -1)  # noqa: E731
    out = {"transport_messages": n("SELECT COUNT(*) n FROM transport_messages"), "distinct_msg_ids": n("SELECT COUNT(DISTINCT msg_id) n FROM transport_messages"),
           "events_document_ingested": n("SELECT COUNT(*) n FROM events WHERE kind='document.ingested'"),
           "evidence_refs": n("SELECT COUNT(*) n FROM evidence_refs"), "evidence_refs_active": n("SELECT COUNT(*) n FROM evidence_refs WHERE status='active'"),
           "claims": n("SELECT COUNT(*) n FROM claims"), "discoveries": n("SELECT COUNT(*) n FROM discoveries"), "questions": n("SELECT COUNT(*) n FROM questions"),
           "responses": n("SELECT COUNT(*) n FROM responses")}
    return out


async def new_world(tmp: Path, name: str, people=PEOPLE, **settings) -> tuple[World, dict[str, Any]]:
    w = await World(tmp / name, **settings).start()
    ids = await build_tenant(w, people=people)
    return w, ids


async def put_event(w: World, ids: dict, who: str, fname: str, text: str, *, msg_id="1790674800.000100", event_id="Ev0ROTTERDAM9D", channel="C0OCEAN",
                    cname="#ocean-freight", author=None, ts="2026-10-08T09:12:00+00:00", visibility="public", member_ids=(), extra=None, app="slack-export",
                    pmap=None) -> dict[str, Any]:
    hid = ids["holders"][who]
    author = author or f"U0{who.upper()}"
    header, rec = source_files(app, "T0ACME", channel, cname, author, event_id, msg_id, text, ts, visibility=visibility, member_ids=member_ids, extra=extra)
    write_export(w.imports(hid) / fname, header, [rec])
    c = await connect_export(w, f"{who}@acme", hid, fname, app=app, principal_map=pmap if pmap is not None else {author: ids["users"][who]})
    c["hid"], c["who"], c["fname"] = hid, who, fname
    return c


def append(w: World, c: dict, *lines: Any) -> None:
    p = w.imports(c["hid"]) / c["fname"]
    with p.open("a", encoding="utf-8") as f:
        for ln in lines:
            f.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")


def msg(msg_id: str, text: str, ts: str, *, typ="message", author="U0ANA", **kw) -> dict[str, Any]:
    return {"type": typ, "id": msg_id, "author": author, "created_at": "2026-10-08T09:12:00+00:00", "updated_at": ts, "text": text, **kw}


async def probe_a_duplicate(tmp: Path) -> dict[str, Any]:
    w, ids = await new_world(tmp, "a_dup")
    try:
        c = await put_event(w, ids, "ana", "ocean.jsonl", EVENT_TEXT)
        hid = c["hid"]
        steps = {}
        steps["1_first_sync"] = {"report": (await sync(w, "ana@acme", c))["report"], **hcounts(w, hid)}
        steps["2_resync_unchanged_file"] = {"report": (await sync(w, "ana@acme", c))["report"], **hcounts(w, hid)}
        append(w, c, json.dumps({"type": "message", "id": "1790674800.000100", "event_id": "Ev0ROTTERDAM9D", "author": "U0ANA", "created_at": "2026-10-08T09:12:00+00:00",
                                 "updated_at": "2026-10-08T09:12:00+00:00", "text": EVENT_TEXT, "container_name": "#ocean-freight"}))
        steps["3_identical_line_appended"] = {"report": (await sync(w, "ana@acme", c))["report"], **hcounts(w, hid)}
        # provider push path: signed webhook notice naming the message, delivered twice with the same delivery id, then re-sent with a new one
        st, hook = await w.call("ana@acme", "POST", f"{c['base']}/{c['connector_id']}/webhook", {})
        from mycelic.ingest.connectors.local_export import sign_notice
        path = hook["url"].split("://", 1)[-1]
        path = "/" + path.split("/", 1)[1]
        wh = {}
        for label, delivery in (("4a_push_delivery_d1", "d1"), ("4b_push_delivery_d1_redelivered", "d1"), ("4c_push_new_delivery_id_d2", "d2")):
            body = json.dumps({"delivery_id": delivery, "account_id": "T0ACME", "source_id": "C0OCEAN", "action": "changed", "occurred_at": "2026-10-08T09:13:00+00:00",
                               "objects": [{"type": "message", "id": "1790674800.000100"}]}).encode()
            async with w.client.post(path, data=body, headers={"X-Mycelic-Signature": sign_notice(hook["secret"].encode(), body), "Content-Type": "application/json"}) as r:
                code, txt = r.status, (await r.text())[:200]
            await asyncio.sleep(0.4)
            await w.rt.holders.ingest(hid).drain()
            wh[label] = {"http": code, "body": txt, **hcounts(w, hid)}
        steps["4_webhook_push"] = wh
        await w.quiet(timeout=10)
        cc = ccounts(w)
        final = hcounts(w, hid)
        ok = (final["ingest_records"], final["documents_total"], final["memories_total"], final["ingest_versions"]) == (1, 1, 1, 1)
        return verdict("PASS" if ok else "FAIL", {"steps": steps, "coordinator": cc, "final_holder_counts": final},
                       "one provider message delivered by sync x2, an appended identical line, and 3 signed push notices (one redelivered) -> still 1 record / 1 document / 1 memory")
    finally:
        await w.stop()


async def probe_b_restart(tmp: Path) -> dict[str, Any]:
    w, ids = await new_world(tmp, "b_restart")
    try:
        c = await put_event(w, ids, "ana", "ocean.jsonl", LEX)
        hid = c["hid"]
        await sync(w, "ana@acme", c)
        goal = await create_goal(w, "lena@acme", ids)
        await w.quiet(timeout=60)
        before = {"holder": hcounts(w, hid), "coord": ccounts(w), "goal_loop": rows(w.settings.coord_db, "SELECT state, explanation FROM goal_loops WHERE goal_id=?", (goal["goal_id"],))}
        claim_before = rows(w.settings.coord_db, "SELECT claim_id, status, support FROM claims")
        await w.restart()                       # close + reopen coordinator, API, worker, transport and every embedded holder on the same dirs
        await w.quiet(timeout=60)
        mid = {"holder": hcounts(w, hid), "coord": ccounts(w)}
        rep = await sync(w, "ana@acme", c)       # the connector's checkpoint must survive: nothing new to read
        # a replayed goal activation / run_now after restart
        st, _ = await w.call("lena@acme", "POST", f"/api/goals/{goal['goal_id']}/loop", {"action": "run_now"})
        await w.quiet(timeout=60)
        after = {"sync_report": rep.get("report"), "holder": hcounts(w, hid), "coord": ccounts(w),
                 "goal_loop": rows(w.settings.coord_db, "SELECT state, explanation FROM goal_loops WHERE goal_id=?", (goal["goal_id"],)),
                 "run_now_http": st, "jobs": w.rt.jobs.counts()}
        claim_after = rows(w.settings.coord_db, "SELECT claim_id, status, support FROM claims")
        strip = lambda h: {k: v for k, v in h.items() if k != "stage_metrics_fetch_dedupe_write"}  # noqa: E731  (counters legitimately move)
        same_holder = strip(before["holder"]) == strip(mid["holder"]) == strip(after["holder"])
        keys = ("evidence_refs", "claims", "discoveries", "responses")
        same_coord = all(before["coord"][k] == after["coord"][k] for k in keys)
        searchable = len(await w.rt.holders.get(hid).search("Rotterdam shipment slipped days", k=3, audience=__import__("mycelic.ingest.acl", fromlist=["Audience"]).Audience.owner_only())) 
        v = "PASS" if same_holder and same_coord and claim_before == claim_after and searchable == 1 else "FAIL"
        return verdict(v, {"before_restart": before, "after_reopen": mid, "after_resync_and_run_now": after, "claims_identical": claim_before == claim_after,
                           "holder_search_hits_after_reopen": searchable, "holder_state_identical_ignoring_metric_counters": same_holder,
                           "coord_counts_identical": same_coord, "questions_before_after": [before["coord"]["questions"], after["coord"]["questions"]]},
                       "restart = Runtime.stop() (worker, holders offline, transport, db closed) then build_runtime on the same data dir")
    finally:
        await w.stop()


async def probe_cd_edits_delete(tmp: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(c) out-of-order edits and (d) edit-then-delete, observed on the holder AND on the coordinator copy of the evidence."""
    w, ids = await new_world(tmp, "cd")
    try:
        c = await put_event(w, ids, "ana", "ocean.jsonl", LEX)
        hid, db = c["hid"], w.holder_db(c["hid"])
        mid = "1790674800.000100"
        await sync(w, "ana@acme", c)
        goal = await create_goal(w, "lena@acme", ids)
        await w.quiet(timeout=60)

        def snap(label: str) -> dict[str, Any]:
            return {"step": label, "holder": hcounts(w, hid),
                    "document": rows(db, "SELECT status, version, substr(text,1,120) AS text, source_root_id FROM documents"),
                    "record": rows(db, "SELECT current_version, version_count, deletion_status, content_hash, source_root_id FROM ingest_records"),
                    "versions": rows(db, "SELECT version_key, source_version, kind, source_event_id, text_retained, doc_version FROM ingest_versions ORDER BY ingested_at"),
                    "queue": rows(db, "SELECT item_id, status, outcome, last_error_code FROM ingest_queue ORDER BY item_id"),
                    "memories": rows(db, "SELECT status, version, substr(text,1,100) AS text FROM memories"),
                    "coord_evidence_refs": rows(w.settings.coord_db, "SELECT ref_id, status, version, source_root_id, substr(disclosed_excerpt,1,100) AS excerpt FROM evidence_refs"),
                    "coord_claims": rows(w.settings.coord_db, "SELECT claim_id, status, version, substr(text,1,100) AS text FROM claims"),
                    "coord_questions": rows(w.settings.coord_db, "SELECT question_id, kind, depth, status, substr(text,1,110) AS text, parent_question_id, trigger FROM questions ORDER BY created_at"),
                    "coord_routes": rows(w.settings.coord_db, "SELECT question_id, holder_id, status FROM question_routes ORDER BY sent_at"),
                    "coord_conflicts": rows(w.settings.coord_db, "SELECT conflict_id, status, summary, claim_a_id, claim_b_id FROM conflicts"),
                    "needle_9_days_holder": needle_hits(db, "9 days"), "needle_12_days_holder": needle_hits(db, "12 days"), "needle_3_days_holder": needle_hits(db, "slipped 3 days"),
                    "needle_rotterdam_coord": needle_hits(w.settings.coord_db, "Rotterdam")}
        s0 = snap("0_ingested_and_committed_hypothesis")
        # ---- (c) newer edit first, then an OLDER edit delivered later
        t_new, t_old = "2026-10-08T11:00:00+00:00", "2026-10-08T10:00:00+00:00"
        append(w, c, msg(mid, LEX.replace("9 days", "12 days"), t_new, typ="edit"))
        r1 = await sync(w, "ana@acme", c)
        await w.quiet(timeout=30)
        s1 = snap("1_newer_edit_T2_applied")
        append(w, c, msg(mid, LEX.replace("9 days", "slipped 3 days").replace("slipped another slipped 3 days", "slipped 3 days"), t_old, typ="edit"))
        r2 = await sync(w, "ana@acme", c)
        await w.quiet(timeout=30)
        s2 = snap("2_older_edit_T1_delivered_after_T2")
        cur = s2["document"][0]["text"] if s2["document"] else ""
        c_ok = "12 days" in cur and "slipped 3 days" not in cur and s2["holder"]["memories_active"] == 1
        stale_outcome = [q["outcome"] for q in s2["queue"]]
        pc = verdict("PASS" if c_ok else "FAIL", {"sync_newer": r1.get("report"), "sync_older": r2.get("report"), "snapshots": [s0, s1, s2], "queue_outcomes_in_order": stale_outcome},
                     "edit T2 (12 days) applied, then edit T1<T2 (3 days) arrives: the current document must stay at T2")
        # ---- (d) delete after the edits, then an old edit trying to resurrect it
        t_del = "2026-10-08T12:00:00+00:00"
        append(w, c, {"type": "delete", "id": mid, "object_type": "message", "deleted_at": t_del})
        r3 = await sync(w, "ana@acme", c)
        await w.quiet(timeout=30)
        s3 = snap("3_deleted_at_source_T3")
        append(w, c, msg(mid, LEX.replace("9 days", "12 days"), "2026-10-08T11:30:00+00:00", typ="edit"))   # older than the delete
        r4 = await sync(w, "ana@acme", c)
        await w.quiet(timeout=30)
        s4 = snap("4_edit_older_than_delete_arrives_late")
        gone = (s3["holder"]["live_records"] == 0 and s3["holder"]["memories_active"] == 0
                and not s3["needle_12_days_holder"] and not s3["needle_9_days_holder"] and all(r["status"] != "active" for r in s3["coord_evidence_refs"]))
        resurrect = s4["holder"]["live_records"] > 0 or s4["holder"]["memories_active"] > 0 or any(r["status"] == "active" for r in s4["coord_evidence_refs"])
        leftovers = {"holder_needle_hits_after_delete": {"customs paperwork": needle_hits(db, "customs paperwork"), "12 days": s3["needle_12_days_holder"], "9 days": s3["needle_9_days_holder"],
                                                         "Ev0ROTTERDAM9D (the event id, an identifier not content)": needle_hits(db, "Ev0ROTTERDAM9D")},
                     "coord_content_only_needle_'customs paperwork'_snippets_after_delete": needle_snips(w.settings.coord_db, "customs paperwork"),
                     "coord_needle_'12 days'_snippets_after_delete": needle_snips(w.settings.coord_db, "12 days"),
                     "coord_needle_'9 days'_snippets_after_delete": needle_snips(w.settings.coord_db, "9 days")}
        coord_left = {k: v for k, v in leftovers["coord_content_only_needle_'customs paperwork'_snippets_after_delete"].items()}
        pd = verdict("PASS" if gone and not resurrect and not coord_left else ("DEFECT" if gone and not resurrect else "FAIL"), {"sync_delete": r3.get("report"), "sync_late_edit": r4.get("report"), "snapshots": [s3, s4], "leftovers": leftovers,
                                                                   "resurrected_by_older_edit": resurrect},
                     "edit then delete: holder content/derived text purged? coordinator copy withdrawn? an edit older than the delete must not resurrect")
        c2 = verdict("DEFECT" if (s1["coord_conflicts"] and all(c["status"] == "contested" for c in s1["coord_claims"])) else "PASS", {
            "claims_after_edit": s1["coord_claims"], "conflicts_after_edit": s1["coord_conflicts"], "questions_after_edit": [(q["kind"], q["status"], q["trigger"].get("kind") if isinstance(q.get("trigger"), dict) else None) for q in s1["coord_questions"]],
            "evidence_refs_after_edit": s1["coord_evidence_refs"]},
            "an author's own edit (9 -> 12 days, one record, one holder) is re-verified by the same holder and then recorded as a conflict between the old claim and the new one; the old claim should be superseded, not contested")
        return pc, pd, c2
    finally:
        await w.stop()


async def probe_e_malformed(tmp: Path) -> dict[str, Any]:
    w, ids = await new_world(tmp, "e_bad")
    try:
        hid = ids["holders"]["ana"]
        good = msg("m-good", "Recurring blocker: good control record about Rotterdam customs paperwork.", "2026-10-08T09:12:00+00:00")
        lines = [
            json.dumps({"type": "source", "id": "C0BAD", "name": "#bad", "source_type": "channel", "visibility": "public"}),
            json.dumps(good),                                                                    # 1 control
            "{this is not json",                                                                 # 2 unparsable
            json.dumps([1, 2, 3]),                                                               # 3 not an object
            json.dumps({"type": "message", "text": "Rotterdam record without any id", "updated_at": "2026-10-08T09:12:00+00:00"}),                     # 4 missing id
            json.dumps({"type": "message", "id": "m-nometa", "text": "Recurring blocker: record with no author and no timestamps at all, Rotterdam."}),  # 5 missing author/ts
            json.dumps({"type": "message", "id": "m-notext", "author": "U0ANA", "updated_at": "2026-10-08T09:12:00+00:00"}),                              # 6 no text
            json.dumps({"type": "telepathy", "id": "m-weird", "text": "Rotterdam unknown type", "updated_at": "2026-10-08T09:12:00+00:00"}),             # 7 unknown type
            json.dumps({"type": "message", "id": "m-badts", "author": "U0ANA", "updated_at": "yesterday-ish", "created_at": "soon", "text": "Rotterdam bad timestamp record."}),  # 8
            json.dumps({"type": "message", "id": "m-nulltext", "author": "U0ANA", "updated_at": "2026-10-08T09:13:00+00:00", "text": None}),                # 9 null text
        ]
        (w.imports(hid) / "bad.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        c = await connect_export(w, "ana@acme", hid, "bad.jsonl", principal_map={"U0ANA": ids["users"]["ana"]})
        rep = await sync(w, "ana@acme", c)
        db = w.holder_db(hid)
        recs = rows(db, "SELECT source_object_id, kind, deletion_status, root_known, root_method, created_at_src, updated_at_src, author_entity_id FROM ingest_records ORDER BY source_object_id")
        st, con = await w.call("ana@acme", "GET", f"{c['base']}/{c['connector_id']}")
        st2, q = await w.call("ana@acme", "GET", f"/api/holders/{hid}/ingest/queue")
        aud = rows(db, "SELECT * FROM audit_log")
        coord_audit = rows(w.settings.coord_db, "SELECT action, outcome, detail FROM audit_log WHERE action LIKE '%ingest%' OR action LIKE '%connector%' OR action LIKE '%normalize%'")
        persisted_reasons = [a for a in aud if "normalize" in json.dumps(a).lower() or "invalid" in json.dumps(a).lower()]
        lines_in, recs_out = 9, len(recs)
        report = rep.get("report", {})
        rep2 = await sync(w, "ana@acme", c)
        metrics = rows(db, "SELECT stage, outcome, SUM(count) AS n FROM ingest_stage_metrics WHERE stage='normalize' GROUP BY outcome")
        docs = rows(db, "SELECT doc_id, origin_id, observed_at, substr(text,1,60) AS text FROM documents ORDER BY doc_id")
        obs_at = {r["source_object_id"]: (rows(db, "SELECT observed_at FROM documents WHERE doc_id=(SELECT record_id FROM ingest_records WHERE source_object_id=?)", (r["source_object_id"],)) or [{}])[0].get("observed_at") for r in recs}
        explicit = bool(report.get("normalize_errors")) and bool(persisted_reasons)
        v = "DEFECT"
        why = ""
        if recs_out + int(report.get("normalize_errors") or 0) == lines_in:
            why = ("every input line is accounted for (records + counted errors) but ONLY as a number in the sync response/stage metrics: which lines were rejected and why is not persisted "
                   "anywhere (holder audit_log, coordinator audit_log, connector health, dead-letter list all empty) and a second sync reports nothing, so the rejected lines cannot be found later"
                   if not explicit else "every line accounted for with a persisted reason")
            v = "DEFECT" if not explicit else "PASS"
        return verdict(v, {"input_data_lines": lines_in, "records_created": recs_out, "sync_report": report, "records": recs,
                           "connector_view_counts_health": trim({k: con.get("connector", con).get(k) for k in ("counts", "health", "status", "status_code", "last_sync_at")}) if isinstance(con, dict) else con,
                           "ingest_queue_endpoint": trim(q) if st2 == 200 else {"http": st2}, "holder_audit_log": aud, "coordinator_audit_rows_ingest": coord_audit,
                           "persisted_per_line_reasons": persisted_reasons, "second_sync_report": rep2.get("report"), "normalize_stage_metrics": metrics,
                           "observed_at_of_records_with_missing_or_bad_timestamps": obs_at, "documents": docs}, why)
    finally:
        await w.stop()



NEEDLES = ("Rotterdam", "Acme Freight", "9 days", "customs")


async def pc(w: World, who: str, method: str, path: str, body: Any = None) -> dict[str, Any]:
    """One API call as ``who``; records the status and which needles of the secret event appear anywhere in the response body."""
    try:
        st, data = await w.call(who, method, path, body)
    except Exception as exc:  # pragma: no cover
        return {"call": f"{method} {path}", "http": "client-error", "error": repr(exc)}
    txt = json.dumps(data, default=str) if not isinstance(data, str) else data
    # the caller's own words echoed back (query string, question text) are not a disclosure: strip them before looking for needles
    from urllib.parse import parse_qs, urlsplit
    echo = [x for v in parse_qs(urlsplit(path).query).get("q", []) for x in (v,)] + ([body["text"]] if isinstance(body, dict) and isinstance(body.get("text"), str) else [])
    scrub = txt
    for e in echo:
        scrub = scrub.replace(e, "").replace(json.dumps(e)[1:-1], "")
    out = {"call": f"{method} {path.split('?')[0]}", "http": st, "leaked_needles": [n for n in NEEDLES if n.lower() in scrub.lower()], "bytes": len(txt),
           "echoed_request_text_stripped": bool(echo)}
    if isinstance(data, dict):
        out["keys"] = sorted(data.keys())[:8]
        for k in ("items", "claims", "discoveries", "results", "records"):
            if isinstance(data.get(k), list):
                out[f"n_{k}"] = len(data[k])
        if data.get("error"):
            out["error"] = str(data["error"])[:120]
    return out


async def probe_f_common_origin(tmp: Path) -> dict[str, Any]:
    variants = {
        "f1_forwarded_from_metadata": lambda who: dict(text=LEX, extra={"forwarded_from": {"app": "slack-export", "account": "T0ACME", "object_type": "message",
                                                                                           "object_id": "1790674800.000100"}}),
        "f2_text_forward_block": lambda who: dict(text=f"FYI from {who}, see below\n---------- Forwarded message ---------\nFrom: Ana <ana@acme.example>\nSubject: Rotterdam\n\n{LEX}"),
        "f3_verbatim_copy_no_marker": lambda who: dict(text=LEX),
        "f1b_forwarded_from_with_own_words": lambda who: dict(text={
            "ana": "Heads-up from Ocean Freight: recurring blocker, the Acme Freight Rotterdam shipment slipped another 9 days, customs paperwork caused the delay.",
            "bo": "Port Ops FYI, recurring blocker again: Acme Freight into Rotterdam slipped another 9 days because customs paperwork caused the delay.",
            "cy": "Key Accounts note: Acme Freight Rotterdam shipment slipped another 9 days, a recurring blocker, customs paperwork caused the delay."}[who],
            extra={"forwarded_from": {"app": "slack-export", "account": "T0ACME", "object_type": "message", "object_id": "1790674800.000100"}}),
        "f4_retold_in_own_words_no_marker": lambda who: dict(text={
            "ana": "Heads-up from Ocean Freight: recurring blocker, the Acme Freight Rotterdam shipment slipped another 9 days, customs paperwork caused the delay.",
            "bo": "Port Ops FYI, recurring blocker again: Acme Freight into Rotterdam slipped another 9 days because customs paperwork caused the delay.",
            "cy": "Key Accounts note: Acme Freight Rotterdam shipment slipped another 9 days, a recurring blocker, customs paperwork caused the delay."}[who]),
    }
    out: dict[str, Any] = {}
    for name, mk in variants.items():
        w, ids = await new_world(tmp, name)
        try:
            cons = {}
            for who, ch, cn in (("ana", "C0OCEAN", "#ocean-freight"), ("bo", "C0PORT", "#port-ops"), ("cy", "C0KEYACCT", "#key-accounts")):
                spec = mk(who)
                cons[who] = await put_event(w, ids, who, f"{who}.jsonl", spec["text"], msg_id=f"fwd-{who}-1", event_id=f"Ev0FWD{who.upper()}", channel=ch, cname=cn,
                                            ts="2026-10-08T10:00:00+00:00", extra=spec.get("extra"))
                await sync(w, f"{who}@acme", cons[who])
            roots = {who: rows(w.holder_db(ids["holders"][who]), "SELECT source_object_id, source_root_id, root_known, root_method, object_key FROM ingest_records") for who in cons}
            goal = await create_goal(w, "lena@acme", ids)
            await w.quiet(timeout=60)
            sm = loop_summary(w, goal["goal_id"])
            claims = [{"status": c["status"], "support": {k: c["support"].get(k) for k in ("independent_roots", "copied_refs", "unknown_independence", "apparent_refs")},
                       "roots": c["support"].get("roots")} for c in sm["claims"]]
            refs = rows(w.settings.coord_db, "SELECT holder_id, source_root_id, root_known FROM evidence_refs")
            distinct_holder_roots = sorted({r[0]["source_root_id"] for r in roots.values() if r})
            top = max((c["support"]["independent_roots"] or 0 for c in claims), default=None)
            lineage_persisted = {who: needle_hits(w.holder_db(ids["holders"][who]), "1790674800.000100") for who in cons}   # the origin object id named by forwarded_from
            out[name] = {"holder_roots": roots, "distinct_roots_across_holders": len(distinct_holder_roots), "origin_object_id_found_in_holder_db": lineage_persisted, "coordinator_evidence_refs": refs, "claims": claims,
                         "discoveries": sm["discoveries"], "max_independent_roots_in_claim": top,
                         "verdict": "PASS" if top == 1 else "FAIL"}
        finally:
            await w.stop()
    # f4 (three people retelling one announcement in their own words, no lineage marker) is not detectable by construction: it is reported, and judged separately
    core = {k: v for k, v in out.items() if not k.startswith("f4")}
    verbatim_ok = all(v["verdict"] == "PASS" for k, v in core.items() if not k.startswith("f1b"))
    overall = "PASS" if all(v["verdict"] == "PASS" for v in core.values()) else ("DEFECT" if verbatim_ok else "FAIL")
    if out.get("f4_retold_in_own_words_no_marker", {}).get("max_independent_roots_in_claim") not in (None, 1):
        out["f4_retold_in_own_words_no_marker"]["verdict"] = "LIMITATION"     # undetectable by content hash; documented direction of error is over-counting here
    if out.get("f1b_forwarded_from_with_own_words", {}).get("verdict") == "FAIL":
        out["f1b_forwarded_from_with_own_words"]["verdict"] = "DEFECT"
    return verdict(overall, out, "the same original statement reaches 3 holders in 3 teams; the claim's independent_roots must be 1 (copied_refs 2). f4 = retold in own words with no marker: counted as independent (undetectable by content hash)")


GTEXT = {
    "ana": "Recurring blocker: Acme Freight shipment into Rotterdam slipped another 9 days; customs paperwork caused the delay.",
    "bo": "Recurring blocker at the Rotterdam terminal: an Acme Freight container slipped 9 days, customs paperwork caused the delay.",
    "cy": "Customer Acme Freight escalated a recurring blocker: the Rotterdam delivery slipped 9 days because customs paperwork caused the delay.",
}


async def probe_g_h_i(tmp: Path) -> tuple[dict, dict, dict]:
    w, ids = await new_world(tmp, "ghi")
    try:
        # ------------------------------------------------------------------ (g) three independent observations, two departments
        for who, ch in (("ana", "C0OCEAN"), ("bo", "C0PORT"), ("cy", "C0KEYACCT")):
            c = await put_event(w, ids, who, f"{who}.jsonl", GTEXT[who], msg_id=f"obs-{who}-1", event_id=f"Ev0OBS{who.upper()}", channel=ch, cname=f"#{ch.lower()}",
                                ts={"ana": "2026-10-08T09:00:00+00:00", "bo": "2026-10-08T10:30:00+00:00", "cy": "2026-10-08T11:45:00+00:00"}[who])
            await sync(w, f"{who}@acme", c)
            await w.rt.holders.service(ids["holders"][who]).heartbeat_once()
        goal = await create_goal(w, "lena@acme", ids)
        gid = goal["goal_id"]
        await w.quiet(timeout=90)
        sm = loop_summary(w, gid)
        cs = coord_state(w, gid)
        h2u = {v: k for k, v in ids["holders"].items()}
        gap_q = [q for q in sm["questions"] if q["kind"] == "gap" and "operations.logistics" in json.dumps(q["candidate_domains"])]
        routed = sorted({h2u.get(r["holder_id"], r["holder_id"]) for r in sm["routes"] if gap_q and r["question_id"] == gap_q[0]["question_id"]})
        responded = sorted({h2u.get(r["holder_id"]) for r in sm["responses"] if gap_q and r["question_id"] == gap_q[0]["question_id"] and r["status"] == "answered"})
        claim_support = [{"status": c["status"], "text": c["text"][:90], "independent_roots": c["support"].get("independent_roots"), "copied_refs": c["support"].get("copied_refs"),
                          "holders": [h2u.get(h) for h in c["support"].get("holders", [])], "roots": [r["source_root_id"] for r in c["support"].get("roots", [])]} for c in sm["claims"]]
        gate = cs["commit_gate_audit"]
        entity_tables = {who: [e["name"] for e in rows(w.holder_db(ids["holders"][who]), "SELECT name, type FROM entities")] for who in ("ana", "bo", "cy")}
        top = max((c["independent_roots"] or 0 for c in claim_support), default=0)
        g_ok = routed == ["ana", "bo", "cy"] and top == 3 and any(c["status"] == "supported" for c in claim_support)
        g_res = verdict("PASS" if g_ok else "FAIL", {
            "goal_id": gid, "gap_question": gap_q[0]["text"] if gap_q else None, "holders_in_departments": {"ana": "Logistics/Ocean Freight", "bo": "Logistics/Port Operations", "cy": "Sales/Key Accounts"},
            "question_routed_to": routed, "answered_by": responded, "claims": claim_support, "commit_gate_audit": gate,
            "discoveries": sm["discoveries"], "holder_entity_names": entity_tables,
            "holder_registry_published_domains": {h2u[h["holder_id"]]: h["published_domains"] for h in cs["holders"] if h["holder_id"] in h2u}},
            "3 holders / 2 departments, each with its own message about Acme Freight + Rotterdam (different authors, channels, wording, timestamps)")

        g_res["observed"]["_g2_placeholder"] = True
        # ------------------------------------------------------------------ (h) denied access
        disc = sm["discoveries"][0]["discovery_id"] if sm["discoveries"] else None
        claim = sm["claims"][0]["claim_id"] if sm["claims"] else None
        ref = rows(w.settings.coord_db, "SELECT ref_id FROM evidence_refs LIMIT 1")
        ref = ref[0]["ref_id"] if ref else None
        ana_h = ids["holders"]["ana"]
        logi = ids["units"]["Logistics"]

        async def battery(who: str) -> list[dict[str, Any]]:
            u = f"{who}@acme"
            calls = [pc(w, u, "GET", f"/api/goals/{gid}"), pc(w, u, "GET", f"/api/discoveries?goal_id={gid}"),
                     pc(w, u, "GET", f"/api/claims?goal_id={gid}"), pc(w, u, "GET", f"/api/holders/{ana_h}/search?q=Rotterdam"),
                     pc(w, u, "GET", f"/api/holders/{ana_h}/records"), pc(w, u, "GET", f"/api/holders/{ana_h}/documents"),
                     pc(w, u, "GET", f"/api/me/memory/search?q=Rotterdam+Acme+Freight")]
            if disc:
                calls.append(pc(w, u, "GET", f"/api/discoveries/{disc}"))
            if claim:
                calls.append(pc(w, u, "GET", f"/api/claims/{claim}"))
            if ref:
                calls.append(pc(w, u, "GET", f"/api/evidence/{ref}"))
            calls.append(pc(w, u, "POST", "/api/questions", {"text": "Why did the Acme Freight shipment into Rotterdam slip, and by how many days?",
                                                           "scope_unit_id": logi, "candidate_domains": ["operations.logistics"]}))
            return list(await asyncio.gather(*calls))
        res: dict[str, Any] = {}
        for who in ("dee", "bo", "lena"):
            res[who] = await battery(who)
        # an outsider's question aimed at the Logistics holders from the outsider's own unit; and the agent chat
        dee_unit = ids["units"]["Treasury"]
        res["dee_question_from_own_unit"] = await pc(w, "dee@acme", "POST", "/api/questions", {"text": "Why did the Acme Freight shipment into Rotterdam slip, and by how many days?",
                                                                                              "scope_unit_id": dee_unit, "candidate_domains": ["operations.logistics"]})
        await w.quiet(timeout=60)
        dee_q = rows(w.settings.coord_db, "SELECT q.question_id, q.status, q.scope_unit_id, q.result FROM questions q WHERE q.asker_id=?", (ids["users"]["dee"],))
        dee_routes = rows(w.settings.coord_db, "SELECT r.holder_id, r.status FROM question_routes r JOIN questions q ON q.question_id=r.question_id WHERE q.asker_id=?", (ids["users"]["dee"],))
        # what the refused asker can still read about their own failed question (opaque holder ids / reasons of holders in a unit they cannot see)
        dq_id = dee_q[0]["question_id"] if dee_q else None
        dq_detail = await w.call("dee@acme", "GET", f"/api/questions/{dq_id}") if dq_id else (None, None)
        dq_txt = json.dumps(dq_detail[1], default=str) if dq_detail[1] is not None else ""
        holder_ids_seen_by_dee = sorted(h for h in (ids["holders"]["ana"], ids["holders"]["bo"], ids["holders"]["cy"]) if h in dq_txt)
        chat = {}
        for kind, aid in (("unit_logistics", ("unit", logi)), ("personal", ("user", ids["users"]["dee"]))):
            st, cb = await w.call("dee@acme", "POST", "/api/chats", {"agent_type": aid[0], "agent_id": aid[1]})
            if st == 201:
                st2, ans = await w.call("dee@acme", "POST", f"/api/chats/{cb['chat']['chat_id']}/messages", {"text": "What do we know about the Acme Freight shipment into Rotterdam slipping 9 days?"})
                chat[kind] = {"create_http": st, "send_http": st2, "answer": str(ans.get("reply", ans))[:400] if isinstance(ans, dict) else str(ans)[:400],
                              "leaked_needles": [n for n in NEEDLES if n.lower() in json.dumps(ans, default=str).lower()]}
            else:
                chat[kind] = {"create_http": st, "body": str(cb)[:160]}
        dee_leaks = [(c["call"], c["http"], c["leaked_needles"]) for c in res["dee"] if c.get("leaked_needles")] + \
                    ([("dee_question_from_own_unit", res["dee_question_from_own_unit"]["http"], res["dee_question_from_own_unit"]["leaked_needles"])] if res["dee_question_from_own_unit"].get("leaked_needles") else []) + \
                    [(f"chat:{k}", v.get("send_http"), v["leaked_needles"]) for k, v in chat.items() if v.get("leaked_needles")]
        dee_question_accepted = [c for c in res["dee"] if c["call"] == "POST /api/questions" and c["http"] in (200, 201)]
        # a question the outsider got accepted must still not have reached holders in Logistics
        leak_via_routes = [r for r in dee_routes if r["holder_id"] in (ids["holders"]["ana"], ids["holders"]["bo"], ids["holders"]["cy"])]
        h_res = verdict("PASS" if not dee_leaks and not leak_via_routes else "FAIL", {
            "outsider": "dee (Treasury team, Finance dept; no membership under Logistics or Sales)", "calls_as_dee": res["dee"], "dee_question_from_own_unit": res["dee_question_from_own_unit"],
            "dee_questions_in_db": dee_q, "dee_question_routes_to_logistics_holders": leak_via_routes,
            "own_failed_question_detail": {"http": dq_detail[0], "holder_ids_of_logistics_holders_visible_to_dee": holder_ids_seen_by_dee, "mentions_rejected_reasons": "rejected" in dq_txt,
                                            "result": (dq_detail[1] or {}).get("question", {}).get("result") if isinstance(dq_detail[1], dict) else None}, "chat_as_dee": chat, "positive_control_bo_logistics_member": res["bo"],
            "positive_control_lena_regional_lead": res["lena"], "leaks_to_dee": dee_leaks},
            "needles = Rotterdam | Acme Freight | 9 days | customs, searched in every response body")

        # members-only channel: restricted content must not be disclosed to a wider question audience
        h2 = {}
        for label, members in (("h2_members_only_ana_bo", ["U0ANA", "U0BO"]),
                               ("h3_members_include_lena_and_admin", ["U0ANA", "U0LENA", "U0ADMIN"])):
            w2, ids2 = await new_world(tmp, label)
            try:
                pmap = {"U0ANA": ids2["users"]["ana"], "U0BO": ids2["users"]["bo"], "U0LENA": ids2["users"]["lena"],
                        "U0ADMIN": (w2.rt.db.one("SELECT user_id FROM users WHERE email=?", ("admin@acme.example",)) or {"user_id": ""})["user_id"]}
                c = await put_event(w2, ids2, "ana", "ocean.jsonl", LEX, visibility="members", member_ids=members, pmap=pmap)
                await sync(w2, "ana@acme", c)
                g2 = await create_goal(w2, "lena@acme", ids2)
                await w2.quiet(timeout=60)
                s2 = loop_summary(w2, g2["goal_id"])
                h2[label] = {"members": members, "responses": [{"status": r["status"], "memory_count": (r["provenance"] or {}).get("memory_count")} for r in s2["responses"]],
                             "claims": len(s2["claims"]), "coord_needle_hits": needle_hits(w2.settings.coord_db, "Rotterdam")}
            finally:
                await w2.stop()
        h_res["observed"]["restricted_source_acl"] = h2
        withheld = h2["h2_members_only_ana_bo"]["claims"] == 0 and not any(k.startswith(("responses", "evidence_refs", "claims")) for k in h2["h2_members_only_ana_bo"]["coord_needle_hits"])
        disclosed_when_inside = h2["h3_members_include_lena_and_admin"]["claims"] >= 1
        h_res["observed"]["restricted_source_withheld_from_wider_audience"] = withheld
        h_res["observed"]["same_source_disclosed_when_audience_inside_members"] = disclosed_when_inside
        if not withheld:
            h_res["verdict"] = "FAIL"

        # ------------------------------------------------------------------ (i) cross-tenant
        gl = [("gus", "Ocean Freight", "employee", ["operations.logistics"]), ("gail", "Port Operations", "employee", ["operations.logistics"])]
        ids_g = await build_tenant(w, slug="globex", org_name="Globex Logistics", people=gl)
        gu, gadmin = "lena@globex", "admin@globex"
        acme_ids = {"goal": gid, "disc": disc, "claim": claim, "ref": ref, "unit": ids["units"]["Logistics"], "holder": ana_h, "user": ids["users"]["ana"]}
        calls = [pc(w, gu, "GET", f"/api/goals/{gid}"), pc(w, gadmin, "GET", f"/api/goals/{gid}"), pc(w, gu, "GET", f"/api/discoveries/{disc}"),
                 pc(w, gu, "GET", f"/api/claims/{claim}"), pc(w, gu, "GET", f"/api/evidence/{ref}"), pc(w, gadmin, "GET", f"/api/evidence/{ref}/raw"),
                 pc(w, gu, "GET", f"/api/holders/{ana_h}/search?q=Rotterdam"), pc(w, gadmin, "GET", f"/api/holders/{ana_h}/records"),
                 pc(w, gu, "GET", f"/api/org/units/{acme_ids['unit']}"), pc(w, gu, "GET", f"/api/discoveries?goal_id={gid}"),
                 pc(w, gu, "POST", "/api/questions", {"text": "Why did the Acme Freight shipment into Rotterdam slip?", "scope_unit_id": acme_ids["unit"], "candidate_domains": ["operations.logistics"]}),
                 pc(w, gu, "POST", "/api/questions", {"text": "Why did the Acme Freight shipment into Rotterdam slip?", "goal_id": gid}),
                 pc(w, gu, "POST", "/api/goals", {"title": "x", "objective": "y", "owner_type": "unit", "owner_id": acme_ids["unit"]}),
                 pc(w, gadmin, "POST", "/api/org/memberships", {"user_id": ids_g["users"]["gus"], "unit_id": acme_ids["unit"], "role": "employee"})]
        xt = list(await asyncio.gather(*calls))
        # a Globex goal with the same domain: must route only to Globex holders and learn nothing from Acme
        g_goal = await create_goal(w, gu, ids_g, scope="Europe", title="Find recurring blockers at Globex", objective="Identify recurring operational blockers across Globex teams")
        await w.quiet(timeout=60)
        gsm = loop_summary(w, g_goal["goal_id"])
        g_holders = set(ids_g["holders"].values())
        cross_routes = [r for r in gsm["routes"] if r["holder_id"] not in g_holders]
        acme_routes_to_globex = rows(w.settings.coord_db, "SELECT r.holder_id FROM question_routes r JOIN questions q ON q.question_id=r.question_id WHERE q.tenant_id=?", (ids["tenant_id"],))
        acme_routes_to_globex = [r for r in acme_routes_to_globex if r["holder_id"] in g_holders]
        glob_detail = await pc(w, gu, "GET", f"/api/goals/{g_goal['goal_id']}")
        leaks = [(c["call"], c["http"], c["leaked_needles"]) for c in xt if c.get("leaked_needles")]
        bad_status = [(c["call"], c["http"]) for c in xt if c["http"] in (200, 201) and (c.get("n_items") or c.get("n_results") or c.get("leaked_needles"))]
        i_res = verdict("PASS" if not leaks and not cross_routes and not acme_routes_to_globex and not bad_status else "FAIL", {
            "globex_calls_with_acme_ids": xt, "globex_goal_questions": [q["text"] for q in gsm["questions"]], "globex_goal_routes_to_non_globex_holders": cross_routes,
            "acme_routes_to_globex_holders": acme_routes_to_globex, "globex_goal_responses": [r["status"] for r in gsm["responses"]], "globex_goal_claims": len(gsm["claims"]),
            "globex_goal_detail_leaks": glob_detail["leaked_needles"], "calls_that_succeeded": bad_status, "leaks": leaks,
            "same_domain_in_both_tenants": "operations.logistics"}, "second tenant 'globex' in the same coordinator + holder host, attacking with Acme ids")
        g_res["observed"].pop("_g2_placeholder", None)
        # (g2) the same three observations, but the owners declared NO domains: only what ingestion publishes can route (needs >= 5 records per domain)
        w3, ids3 = await new_world(tmp, "g2_nodomains", people=[("ana", "Ocean Freight", "employee", []), ("bo", "Port Operations", "employee", []), ("cy", "Key Accounts", "employee", [])])
        try:
            for who, ch in (("ana", "C0OCEAN"), ("bo", "C0PORT"), ("cy", "C0KEYACCT")):
                c = await put_event(w3, ids3, who, f"{who}.jsonl", GTEXT[who], msg_id=f"obs-{who}-1", event_id=f"Ev0OBS{who.upper()}", channel=ch, cname=f"#{ch.lower()}")
                await sync(w3, f"{who}@acme", c)
                await w3.rt.holders.service(ids3["holders"][who]).heartbeat_once()
            g3 = await create_goal(w3, "lena@acme", ids3)
            await w3.quiet(timeout=45)
            s3 = loop_summary(w3, g3["goal_id"])
            reg = rows(w3.settings.coord_db, "SELECT holder_id, domains, published_domains FROM holders")
            rec3 = {who: rows(w3.holder_db(ids3["holders"][who]), "SELECT primary_domain_id FROM ingest_records") for who in ("ana", "bo", "cy")}
            g_res["observed"]["g2_no_declared_domains"] = {
                "holder_registry": reg, "primary_domain_of_each_ingested_record": rec3, "goal_loop": rows(w3.settings.coord_db, "SELECT state, explanation FROM goal_loops WHERE goal_id=?", (g3["goal_id"],)),
                "questions": s3["questions"], "routes": s3["routes"], "claims": len(s3["claims"]),
                "outcome": "no question was routed to any holder" if not s3["routes"] else "routed"}
        finally:
            await w3.stop()
        return g_res, h_res, i_res
    finally:
        await w.stop()


async def guarded(name: str, coro) -> Any:
    try:
        return await coro
    except Exception:  # a harness/system exception is recorded, never swallowed silently
        return {"verdict": "HARNESS_ERROR", "observed": {"traceback": traceback.format_exc()[-1800:]}, "note": name}


async def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="mycelic_trace_"))
    OUT["meta"].update({"tmp_root": str(tmp_root), "python": sys.version.split()[0], "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "model_provider": "mycelic.models.fake.FakeProvider (deterministic)", "embedder": "hash", "transport": "sqlite outbox",
                        "connector_path": "local_export (source_app label 'slack-export'), via the HTTP API the UI uses"})
    try:
        OUT["stages"]["A_literal_event"] = await guarded("A", trace("literal", EVENT_TEXT, tmp_root / "A_literal"))
        OUT["stages"]["B_lexical_event"] = await guarded("B", trace(
            "lexical", "Recurring blocker: Acme Freight shipment into Rotterdam slipped another 9 days; customs paperwork caused the delay.", tmp_root / "B_lexical"))
        P = OUT["probes"]
        P["a_duplicate_delivery"] = await guarded("a", probe_a_duplicate(tmp_root))
        P["b_replay_after_restart"] = await guarded("b", probe_b_restart(tmp_root))
        r = await guarded("cd", probe_cd_edits_delete(tmp_root))
        if isinstance(r, tuple):
            P["c_out_of_order_edits"], P["d_edit_then_delete"], P["c2_edit_makes_old_claim_contested"] = r
        else:
            P["c_out_of_order_edits"] = P["d_edit_then_delete"] = r
        P["e_malformed_and_missing_metadata"] = await guarded("e", probe_e_malformed(tmp_root))
        P["f_common_origin"] = await guarded("f", probe_f_common_origin(tmp_root))
        r = await guarded("ghi", probe_g_h_i(tmp_root))
        if isinstance(r, tuple):
            P["g_three_observations_two_departments"], P["h_denied_access"], P["i_cross_tenant"] = r
        else:
            P["g_three_observations_two_departments"] = P["h_denied_access"] = P["i_cross_tenant"] = r
        h = P.get("h_denied_access", {}).get("observed", {}).get("own_failed_question_detail")
        if isinstance(h, dict):
            seen = h.get("holder_ids_of_logistics_holders_visible_to_dee") or []
            P["h2_metadata_visible_to_refused_asker"] = verdict("DEFECT" if seen else "PASS", h,
                                                                  "no content, but a refused asker reads the opaque ids and per-holder rejection reasons of holders in units they cannot see (existence + domain match)")
        OUT["verdicts"] = {k: v.get("verdict") for k, v in P.items()}
    finally:
        (HERE / "trace_output.json").write_text(json.dumps(OUT, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
        shutil.rmtree(tmp_root, ignore_errors=True)
    print(json.dumps(OUT.get("verdicts"), indent=1))


if __name__ == "__main__":
    asyncio.run(main())

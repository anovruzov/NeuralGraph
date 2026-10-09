"""Architecture gate tests on a synthetic run: a real ingested holder pair (connector path, production pipeline), a hand-built
coordinator database with a consistent claim/lineage/hypergraph story, then one targeted corruption per failure the gate must
catch (SQL-inserted claim -> G7, document without ingest_records -> G1, ...)."""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
REPO = BENCH.parents[2]
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(0, str(REPO))

from bench import arch_gate                                    # noqa: E402
from mycelic.db import CoordDB                                 # noqa: E402
from mycelic.evidence import EvidenceStore                     # noqa: E402
from mycelic.ingest.service import IngestService               # noqa: E402
from mycelic.knowledge.support import compute_support          # noqa: E402
from mycelic.tests.ingest_support import make_pipeline, write_jsonl   # noqa: E402


async def connect_export(svc, paths, *, source_app, account_id, created_by="usr_ana"):
    con = await svc.add_connector("local_export", created_by=created_by,
                                  config={"source_app": source_app, "account_id": account_id, "paths": [str(p) for p in paths]})
    await svc.discover_sources(con["connector_id"])
    for src in svc.sources(con["connector_id"]):
        if src.selection != "included":
            await svc.set_source(src.source_id, actor=created_by, selection="included")
    return con

T = "ten_a"
NOW = "2026-10-01T10:00:00+00:00"
ENT = "issue:tracker:lgx-412"

PUBLIC_HDR = {"id": "ops-chat", "name": "#ops", "source_type": "channel", "visibility": "public"}
PRIVATE_HDR = {"id": "notes", "name": "notes", "source_type": "folder", "visibility": "private", "member_ids": ["usr_ana"]}


def msgs(prefix: str, n: int = 2) -> list[dict]:
    return [{"type": "message", "id": f"{prefix}-{i}", "conversation_id": prefix, "author": "usr_ana", "created_at": f"2026-09-0{i + 1}T10:00:00Z",
             "text": f"Parcel delays traced to LGX-412 batch {prefix} number {i} in the routing service."} for i in range(n)]


async def _ingest(path: Path, tmp: Path, holder_id: str, files: list[tuple[str, dict, list[dict]]], owner: str | None) -> None:
    creator = owner or "usr_ana"
    store = EvidenceStore(path, holder_id=holder_id, tenant_id=T, owner_ids=[owner] if owner else [])
    try:
        pipe = make_pipeline(store)
        svc = IngestService(pipe)
        for i, (name, hdr, recs) in enumerate(files):
            f = write_jsonl(tmp / f"{holder_id}-{name}.jsonl", recs, header=hdr)
            con = await connect_export(svc, [f], source_app=f"app{i}", account_id=f"{holder_id}-{name}", created_by=creator)
            await pipe.sync(con["connector_id"])
        await pipe.process_available()
    finally:
        await store.close()


def build_run(root: Path) -> Path:
    holders = root / "holders"
    for hid in ("hold_a", "hold_b", "hold_d"):
        (holders / hid).mkdir(parents=True)
    src = root / "src"
    src.mkdir()
    asyncio.run(_ingest(holders / "hold_a" / "evidence.db", src, "hold_a", [("ops", PUBLIC_HDR, msgs("a")), ("notes", PRIVATE_HDR, msgs("pa", 1))], "usr_ana"))
    asyncio.run(_ingest(holders / "hold_b" / "evidence.db", src, "hold_b", [("ops", {**PUBLIC_HDR, "id": "ops-chat-b"}, msgs("b"))], "usr_ben"))
    asyncio.run(_ingest(holders / "hold_d" / "evidence.db", src, "hold_d", [("ops", {**PUBLIC_HDR, "id": "ops-chat-d"}, msgs("d"))], None))
    run = root / "run"
    run.mkdir()
    coord = run / "coord.db"
    db = CoordDB(coord)
    asyncio.run(db.close())
    c = sqlite3.connect(coord)
    c.executescript(f"""
    PRAGMA foreign_keys=OFF;
    INSERT INTO tenants(tenant_id, slug, name, created_at) VALUES ('{T}', 'a', 'A', '{NOW}');
    INSERT INTO org_units(unit_id, tenant_id, type, name, parent_id, path, depth, created_at) VALUES
      ('u_root', '{T}', 'executive', 'Root', NULL, '/u_root', 0, '{NOW}'),
      ('u_d1', '{T}', 'department', 'Logistics', 'u_root', '/u_root/u_d1', 1, '{NOW}'),
      ('u_d2', '{T}', 'department', 'Sales', 'u_root', '/u_root/u_d2', 1, '{NOW}');
    INSERT INTO users(user_id, tenant_id, email, name, created_at, updated_at) VALUES
      ('usr_ana', '{T}', 'ana@x', 'Ana', '{NOW}', '{NOW}'), ('usr_ben', '{T}', 'ben@x', 'Ben', '{NOW}', '{NOW}'), ('usr_lead', '{T}', 'lead@x', 'Lead', '{NOW}', '{NOW}');
    INSERT INTO memberships(membership_id, tenant_id, user_id, unit_id, role, created_at) VALUES
      ('m1', '{T}', 'usr_ana', 'u_d1', 'employee', '{NOW}'), ('m2', '{T}', 'usr_ben', 'u_d2', 'employee', '{NOW}'), ('m3', '{T}', 'usr_lead', 'u_root', 'executive', '{NOW}');
    INSERT INTO holders(holder_id, tenant_id, owner_type, owner_id, name, key_hash, route_key, mode, created_at, updated_at) VALUES
      ('hold_a', '{T}', 'user', 'usr_ana', 'ana', 'k1', 'r1', 'embedded', '{NOW}', '{NOW}'),
      ('hold_b', '{T}', 'user', 'usr_ben', 'ben', 'k2', 'r2', 'embedded', '{NOW}', '{NOW}'),
      ('hold_d', '{T}', 'unit', 'u_d1', 'logistics', 'k3', 'r3', 'embedded', '{NOW}', '{NOW}');
    INSERT INTO tenant_policies(tenant_id, key, value, updated_at) VALUES ('{T}', 'min_independent_roots', '2', '{NOW}');
    INSERT INTO goals(goal_id, tenant_id, owner_type, owner_id, title, objective, created_at, updated_at) VALUES ('g1', '{T}', 'user', 'usr_lead', 'g', 'o', '{NOW}', '{NOW}');
    INSERT INTO questions(question_id, tenant_id, asker_type, asker_id, goal_id, text, scope_unit_id, status, policy, budget, created_at, updated_at)
      VALUES ('q1', '{T}', 'user', 'usr_lead', 'g1', 'What delays parcels?', 'u_root', 'committed', '{{"visibility":"unit"}}', '{{"holders":10}}', '{NOW}', '{NOW}');
    INSERT INTO question_routes(route_id, tenant_id, question_id, holder_id, status) VALUES ('rt1', '{T}', 'q1', 'hold_a', 'answered'), ('rt2', '{T}', 'q1', 'hold_b', 'answered');
    INSERT INTO audit_log(tenant_id, at, actor_type, actor_id, action, resource_type, resource_id, outcome, detail) VALUES
      ('{T}', '{NOW}', 'worker', 'router', 'question.route', 'question', 'q1', 'ok', '{{"holders":["hold_a","hold_b"],"rank_method":"hypergraph"}}'),
      ('{T}', '{NOW}', 'loop', 'goal:g1', 'claim.gate', 'claim', 'claim:q1:0', 'allow', '{{}}'),
      ('{T}', '{NOW}', 'loop', 'goal:g1', 'discovery.create', 'discovery', 'disc1', 'ok', '{{}}');
    INSERT INTO evidence_refs(ref_id, tenant_id, holder_id, source_root_id, root_known, kind, created_at, updated_at) VALUES
      ('ev1', '{T}', 'hold_a', 'root1', 1, 'message', '{NOW}', '{NOW}'), ('ev2', '{T}', 'hold_b', 'root2', 1, 'message', '{NOW}', '{NOW}');
    INSERT INTO responses(response_id, tenant_id, question_id, holder_id, status, content, evidence_ref_ids, provenance, msg_id, received_at) VALUES
      ('rs1', '{T}', 'q1', 'hold_a', 'answered', 'x', '["ev1"]', '{{"retrieval_operator":"neuralgraph.hybrid"}}', 'm-rs1', '{NOW}'),
      ('rs2', '{T}', 'q1', 'hold_b', 'answered', 'y', '["ev2"]', '{{"retrieval_operator":"neuralgraph.hybrid"}}', 'm-rs2', '{NOW}');
    INSERT INTO model_usage(tenant_id, at, provider, model, tier, purpose, input_tokens, output_tokens) VALUES ('{T}', '{NOW}', 'fake', 'fake-1', 'small', 'answer_from_evidence', 100, 20);
    """)
    support = compute_support([{"ref_id": "ev1", "holder_id": "hold_a", "source_root_id": "root1", "root_known": 1, "role": "supports", "status": "active"},
                               {"ref_id": "ev2", "holder_id": "hold_b", "source_root_id": "root2", "root_known": 1, "role": "supports", "status": "active"}])
    c.execute("INSERT INTO claims(claim_id, tenant_id, scope_unit_id, visibility, text, kind, status, question_id, goal_id, created_by_type, created_by_id, support, created_at, updated_at) "
              "VALUES ('c1', ?, 'u_root', 'unit', 'LGX-412 delays parcels', 'finding', 'supported', 'q1', 'g1', 'agent', 'goal:g1', ?, ?, ?)", (T, json.dumps(support), NOW, NOW))
    c.executescript(f"""
    INSERT INTO claim_evidence(claim_id, ref_id, role, weight) VALUES ('c1', 'ev1', 'supports', 1), ('c1', 'ev2', 'supports', 1);
    INSERT INTO derivations(derivation_id, tenant_id, claim_id, operator, input_claim_ids, input_ref_ids, response_ids, contributor_type, contributor_id, created_at)
      VALUES ('d1', '{T}', 'c1', 'synthesize', '[]', '["ev1","ev2"]', '["rs1","rs2"]', 'loop', 'goal:g1', '{NOW}');
    INSERT INTO revisions(revision_id, tenant_id, object_type, object_id, version, actor_type, actor_id, after, at) VALUES
      ('rv1', '{T}', 'claim', 'c1', 1, 'loop', 'goal:g1', '{{"idempotency_key":"claim:q1:0"}}', '{NOW}'),
      ('rv2', '{T}', 'discovery', 'disc1', 1, 'loop', 'goal:g1', '{{"idempotency_key":"disc:q1"}}', '{NOW}');
    INSERT INTO discoveries(discovery_id, tenant_id, scope_unit_id, title, summary, claim_ids, question_id, created_at, updated_at)
      VALUES ('disc1', '{T}', 'u_root', 't', 's', '["c1"]', 'q1', '{NOW}', '{NOW}');
    INSERT INTO hyperedges(edge_id, tenant_id, kind, anchor_type, anchor_id, version, status, independent_roots, created_by_type, created_by_id, created_at, updated_at) VALUES
      ('e_sup', '{T}', 'support', 'claim', 'c1', 1, 'active', 2, 'loop', 'goal:g1', '{NOW}', '{NOW}'),
      ('e_lin', '{T}', 'lineage', 'claim', 'c1', 1, 'active', 0, 'loop', 'goal:g1', '{NOW}', '{NOW}'),
      ('e_ent', '{T}', 'entity_index', 'entity', '{ENT}', 1, 'active', 0, 'holder', 'hold_a', '{NOW}', '{NOW}');
    INSERT INTO hyperedge_members(edge_id, tenant_id, member_type, member_id, role) VALUES
      ('e_sup', '{T}', 'evidence_ref', 'ev1', 'supports'), ('e_sup', '{T}', 'evidence_ref', 'ev2', 'supports'),
      ('e_sup', '{T}', 'source_root', 'root1', 'origin'), ('e_sup', '{T}', 'source_root', 'root2', 'origin'),
      ('e_sup', '{T}', 'holder', 'hold_a', 'held_by'), ('e_sup', '{T}', 'holder', 'hold_b', 'held_by'),
      ('e_lin', '{T}', 'question', 'q1', 'asked_in'), ('e_lin', '{T}', 'claim', 'c1', 'subject'),
      ('e_ent', '{T}', 'holder', 'hold_a', 'holds'), ('e_ent', '{T}', 'holder', 'hold_b', 'holds');
    """)
    c.commit()
    c.close()
    (run / "run_manifest.json").write_text(json.dumps({"split": "dev", "mode": "system", "provider_label": "deterministic-provider"}))
    return root


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
    return build_run(tmp_path_factory.mktemp("gate_base"))


@pytest.fixture
def run(base, tmp_path) -> Path:
    dst = tmp_path / "copy"
    shutil.copytree(base, dst)
    return dst


def gate(root: Path, **kw):
    kw.setdefault("required_entities", [ENT])
    return arch_gate.check(root / "run", coord_db=root / "run" / "coord.db", holders_dir=root / "holders", **kw)


def sql(root: Path, statement: str, args=()):
    c = sqlite3.connect(root / "run" / "coord.db")
    c.execute(statement, args)
    c.commit()
    c.close()


def tree_state(root: Path):
    """Size, mtime and sha256 of every database file. (A read-only open of a WAL-mode file may create empty -wal/-shm sidecars;
    the database files themselves must be untouched.)"""
    import hashlib
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*.db")) if p.is_file()}


# ------------------------------------------------------------------------------------------------ the good run
def test_consistent_run_passes_every_gate(run):
    rep = gate(run)
    assert rep.valid, rep.summary()
    assert set(rep.results) == {f"G{i}" for i in range(1, 11)}
    assert all(r.status in ("pass", "na") for r in rep.results.values())
    cnt = rep.counts
    assert cnt["holders_created"] == 3 and cnt["holders_with_records"] == 3 and cnt["holders_activated"] == 2
    assert cnt["routes"] == 2 and cnt["records_ingested"] >= 7
    assert cnt["claims_by_status"] == {"supported": 1} and cnt["applied_events_outcomes"].get("new", 0) >= 7
    assert cnt["hyperedges_by_kind"] == {"support": 1, "lineage": 1, "entity_index": 1}
    assert rep.results["G4"].data["replay"]["denied"] == 0 and rep.results["G4"].data["rank_methods"] == {"hypergraph": 1}


def test_gate_does_not_write_anything(run):
    before = tree_state(run)
    gate(run)
    assert tree_state(run) == before
    conn = arch_gate.ro_connect(run / "run" / "coord.db")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO tenants(tenant_id, slug, name, created_at) VALUES ('x','x','x','x')")
    conn.close()
    src = (BENCH / "arch_gate.py").read_text()
    assert src.count("sqlite3.connect(") == 2            # ro_connect + the scratch copy a replay writes into
    assert "mode=ro" in src


# ------------------------------------------------------------------------------------------------ failures the gate must catch
def test_sql_inserted_claim_fails_g7(run):
    sql(run, "INSERT INTO claims(claim_id, tenant_id, text, kind, status, created_by_type, created_by_id, support, created_at, updated_at) "
             "VALUES ('forged', ?, 'LGX-412 is the cause', 'finding', 'supported', 'user', 'harness', '{\"independent_roots\": 2}', ?, ?)", (T, NOW, NOW))
    rep = gate(run)
    assert not rep.valid and "G7" in rep.failed_ids
    assert any("forged" in p for p in rep.results["G7"].problems)
    assert "G5" in rep.failed_ids                           # no revision / gate audit row either


def test_document_without_ingest_records_fails_g1(run):
    async def bypass():
        st = EvidenceStore(run / "holders" / "hold_a" / "evidence.db", holder_id="hold_a", tenant_id=T)
        try:
            await st.ingest_document("direct upload", "Parcel delays traced to LGX-412 uploaded directly into the store.")
        finally:
            await st.close()
    asyncio.run(bypass())
    rep = gate(run)
    assert not rep.valid and "G1" in rep.failed_ids
    assert any("no_ingest_record" in p for p in rep.results["G1"].problems)


def test_pre_hypergraph_run_reports_missing(run):
    c = sqlite3.connect(run / "run" / "coord.db")
    c.execute("DROP TABLE hyperedge_members")
    c.execute("DROP TABLE hyperedges")
    c.commit()
    c.close()
    rep = gate(run)
    assert not rep.valid
    assert rep.results["G3"].status == arch_gate.MISSING and rep.results["G8"].status == arch_gate.MISSING
    assert rep.results["G4"].status == arch_gate.MISSING
    assert "pre-hypergraph" in " ".join(rep.notes)
    off = gate(run, expect_hypergraph=False)
    assert off.results["G8"].status == arch_gate.NA
    assert off.results["G3"].status == arch_gate.PASS and off.valid, off.summary()


def test_support_edge_must_match_claim_evidence(run):
    sql(run, "DELETE FROM hyperedge_members WHERE edge_id='e_sup' AND member_id='ev2'")
    rep = gate(run)
    assert "G3" in rep.failed_ids and any("differ" in p for p in rep.results["G3"].problems)


def test_supported_claim_below_policy_or_with_unknown_roots_fails_g3(run):
    sql(run, "UPDATE evidence_refs SET source_root_id='root1' WHERE ref_id='ev2'")       # two copies of one root
    rep = gate(run, expect_hypergraph=False)
    assert "G3" in rep.failed_ids
    assert any("recomputed independent roots 1" in p for p in rep.results["G3"].problems)


def test_routing_outside_scope_missing_audit_or_over_budget_fail_g4(run):
    sql(run, "UPDATE questions SET scope_unit_id='u_d2' WHERE question_id='q1'")        # hold_a (Logistics) is outside Sales
    rep = gate(run)
    assert "G4" in rep.failed_ids and any("can_route denies" in p for p in rep.results["G4"].problems)
    sql(run, "UPDATE questions SET scope_unit_id='u_root' WHERE question_id='q1'")
    sql(run, "UPDATE questions SET budget='{\"holders\":1}' WHERE question_id='q1'")
    assert any("budget" in p for p in gate(run).results["G4"].problems)
    sql(run, "UPDATE questions SET budget='{\"holders\":10}' WHERE question_id='q1'")
    sql(run, "DELETE FROM audit_log WHERE action='question.route'")
    assert any("no audit_log row" in p for p in gate(run).results["G4"].problems)


def test_ranker_expected_but_absent_fails_g4(run):
    sql(run, "UPDATE audit_log SET detail='{\"holders\":[\"hold_a\",\"hold_b\"]}' WHERE action='question.route'")
    assert "G4" in gate(run).failed_ids
    assert gate(run, expect_ranker=False).results["G4"].status == arch_gate.PASS      # ablation A1: ranker off


def test_non_declared_provider_fails_g6(run):
    sql(run, "INSERT INTO model_usage(tenant_id, at, provider, model, tier, purpose) VALUES (?, ?, 'openai', 'gpt', 'big', 'x')", (T, NOW))
    rep = gate(run)
    assert "G6" in rep.failed_ids and any("openai" in p for p in rep.results["G6"].problems)


def test_discovery_without_resolvable_lineage_fails_g9(run):
    sql(run, "UPDATE discoveries SET claim_ids='[]' WHERE discovery_id='disc1'")
    assert "G9" in gate(run).failed_ids
    sql(run, "UPDATE discoveries SET claim_ids='[\"nope\"]' WHERE discovery_id='disc1'")
    assert any("does not exist" in p for p in gate(run).results["G9"].problems)


def test_discovery_idempotency_g5(run):
    sql(run, "INSERT INTO discoveries(discovery_id, tenant_id, scope_unit_id, title, summary, claim_ids, question_id, created_at, updated_at) "
             "VALUES ('disc2', ?, 'u_root', 't', 's', '[\"c1\"]', 'q1', ?, ?)", (T, NOW, NOW))
    sql(run, "INSERT INTO revisions(revision_id, tenant_id, object_type, object_id, version, actor_type, actor_id, after, at) "
             "VALUES ('rv9', ?, 'discovery', 'disc2', 1, 'loop', 'x', '{\"idempotency_key\":\"disc:q1\"}', ?)", (T, NOW))
    rep = gate(run)
    assert "G5" in rep.failed_ids and any("used by 2" in p for p in rep.results["G5"].problems)


def test_private_record_in_unit_holder_or_shared_holder_file_fails_g2(run):
    async def plant():
        st = EvidenceStore(run / "holders" / "hold_d" / "evidence.db", holder_id="hold_d", tenant_id=T)
        try:
            pipe = make_pipeline(st)
            svc = IngestService(pipe)
            f = write_jsonl(run / "p.jsonl", msgs("pd", 1), header={**PRIVATE_HDR, "id": "pd-notes"})
            con = await connect_export(svc, [f], source_app="appx", account_id="pd")
            await pipe.sync(con["connector_id"])
            await pipe.process_available()
        finally:
            await st.close()
    asyncio.run(plant())
    rep = gate(run)
    assert "G2" in rep.failed_ids and any("private_in_unit_holder" in p for p in rep.results["G2"].problems)


def test_g10_surviving_text_of_a_deleted_record_fails(run):
    c = sqlite3.connect(run / "holders" / "hold_a" / "evidence.db")
    rid = c.execute("SELECT record_id FROM ingest_records WHERE deletion_status='live' LIMIT 1").fetchone()[0]
    c.execute("UPDATE ingest_records SET deletion_status='deleted_at_source' WHERE record_id=?", (rid,))
    c.commit()
    c.close()
    rep = gate(run, deleted_markers=["LGX-412 batch a number 0"])
    assert "G10" in rep.failed_ids
    kinds = " ".join(rep.results["G10"].problems)
    assert "text_survives_in_documents" in kinds and "deleted_marker_survives" in kinds


def test_g10_declared_duplicate_and_restart_need_evidence(run):
    (run / "run" / "fault_plan.json").write_text(json.dumps({"faults": {"duplicate": 3, "restart": 1}, "restart": True}))
    rep = gate(run)
    assert "G10" in rep.failed_ids
    assert any("duplicate" in p for p in rep.results["G10"].problems) and any("restart" in p for p in rep.results["G10"].problems)


def test_user_principal_writing_system_records_fails_g7(run):
    sql(run, "INSERT INTO audit_log(tenant_id, at, actor_type, actor_id, action, resource_type, resource_id, outcome, detail) "
             "VALUES (?, ?, 'user', 'usr_lead', 'claim.gate', 'claim', 'claim:q1:9', 'allow', '{}')", (T, NOW))
    assert "G7" in gate(run).failed_ids
    (run / "run" / "harness_db_opens.json").write_text(json.dumps([{"path": "coord.db", "uri": "file:coord.db?mode=rw"}]))
    sql(run, "DELETE FROM audit_log WHERE actor_type='user'")
    assert any("without mode=ro" in p for p in gate(run).results["G7"].problems)


def test_evidence_ref_not_in_any_holder_response_fails_g7(run):
    sql(run, "UPDATE responses SET evidence_ref_ids='[]' WHERE response_id='rs2'")
    rep = gate(run)
    assert "G7" in rep.failed_ids and any("ev2" in p for p in rep.results["G7"].problems)


# ------------------------------------------------------------------------------------------------ finalize: score + gate + ledger row
def test_finalize_scores_gates_and_writes_the_ledger_row(run, tmp_path):
    from bench import ledger, report
    from bench.gold import GoldSink, gold_path
    rd = run / "run"
    opts = [{"label": "A", "id": ENT, "display": "LGX-412", "aliases": []}, {"label": "B", "id": "service:other", "display": "other-service", "aliases": []}]
    (rd / "tasks_dev.public.json").write_text(json.dumps({"split": "dev", "tasks": [{"task_id": "t1", "tenant": "a", "question_text": "What delays parcels?", "options": opts}]}))
    GoldSink(gold_path(rd, "dev")).write("t1", {"cls": "cross_domain", "answer": "A", "expected_abstain": False})
    (rd / "views").mkdir()
    (rd / "views" / "t1.json").write_text(json.dumps({"task_id": "t1", "status": "ok", "tenant_id": T, "latency_s": 2.0, "question": {"question_id": "q1", "status": "committed", "result": {}},
                                                       "claims": [{"claim": {"claim_id": "c1", "status": "supported", "text": "LGX-412 delays parcels", "question_id": "q1"},
                                                                   "evidence": [], "support": {"independent_roots": 2}}], "raw_checks": {}}))
    m = json.loads((rd / "run_manifest.json").read_text())
    m.update(seed=1, size="S", split="dev", counts={"created": 3, "activated": 2, "routed": 2, "with_records": 3}, model_usage={"calls": 1, "input_tokens": 100, "output_tokens": 20})
    (rd / "run_manifest.json").write_text(json.dumps(m))
    led = tmp_path / "ledger.jsonl"
    rid = ledger.start_run({"split": "dev", "mode": "system", "seed": 1, "size": "S", "ledger_path": str(led), "run_dir": str(rd)})
    out = report.finalize(rd, coord_db=rd / "coord.db", holders_dir=run / "holders", run_id=rid, ledger=led, expect_hypergraph=True)
    assert out["score"].accuracy == 1.0 and out["gate"] is not None                                    # scored, and the gate ran
    assert (rd / "score.json").exists() and (rd / "arch_gate.json").exists()
    row = out["ledger_row"]
    assert row["status"] == "finished" and row["accuracy"] == 1.0 and row["n"] == 1 and row["holders_created"] == 3 and row["gate_status"] in ("valid", "invalid")
    assert row["model_calls"] == 1 and row["provider_label"] == "deterministic-provider" and row["claims_by_status"] == {"supported": 1}
    text = report.render(rd).read_text()
    assert "deterministic-provider" in text and "100.0%" in text


def test_unreadable_coordinator_database_invalidates_every_gate(tmp_path):
    rep = arch_gate.check(None, coord_db=tmp_path / "missing.db", holders_dir=tmp_path)
    assert not rep.valid and rep.failed_ids == [f"G{i}" for i in range(1, 11)]
    assert all(r.status == arch_gate.ERROR for r in rep.results.values())


def test_restart_flag_in_sources_manifest_needs_a_resumed_cursor(run):
    (run / "run" / "sources_manifest.json").write_text(json.dumps([{"holder_id": "hold_a", "flags": {"restart": True}}]))
    rep = gate(run)
    assert "G10" in rep.failed_ids and any("hold_a" in p and "cursor never advanced" in p for p in rep.results["G10"].problems)
    c = sqlite3.connect(run / "holders" / "hold_a" / "evidence.db")
    c.execute("UPDATE connector_checkpoints SET version = 3 WHERE phase='incremental'")
    c.commit()
    c.close()
    assert gate(run).results["G10"].status == arch_gate.PASS


# ------------------------------------------------------------------------------------------------ G7: views are cross-checked against coord.db
def write_view(rd, *, text="LGX-412 delays parcels", status="supported", roots=2, version=1, claims=True, holder="hold_a", name="t1", with_disc=False):
    claim = {"claim": {"claim_id": "c1", "status": status, "text": text, "version": version, "question_id": "q1"},
             "evidence": [{"ref_id": "ev1", "holder_id": holder, "source_root_id": "root1"}, {"ref_id": "ev2", "holder_id": "hold_b", "source_root_id": "root2"}],
             "support": {"independent_roots": roots}}
    view = {"task_id": name, "status": "ok", "tenant_id": T, "question": {"question_id": "q1", "status": "committed", "result": {}, "resolved_at": "2026-12-31T00:00:00+00:00"},
            "claims": [claim] if claims else [], "discoveries": [], "raw_checks": {}}
    if with_disc:
        view["discoveries"] = [{"discovery": {"discovery_id": "disc1", "title": "t", "summary": "s", "claim_ids": ["c1"]}, "claims": [claim], "evidence": []}]
    (rd / "views").mkdir(exist_ok=True)
    (rd / "views" / f"{name}.json").write_text(json.dumps(view))


def test_views_that_match_coord_db_pass_g7(run):
    write_view(run / "run", with_disc=True)
    rep = gate(run)
    assert rep.results["G7"].status == arch_gate.PASS and rep.results["G7"].data["views_cross_checked"] == 1, rep.results["G7"].problems


@pytest.mark.parametrize("kw, needle", [({"text": "TAMPERED by harness: zzzfake-service is the cause."}, "differs from coord.db"),
                                        ({"status": "contested"}, "differs from coord.db"), ({"roots": 5}, "differs from coord.db"),
                                        ({"holder": "hold_b"}, "reference ev1 differs")])
def test_a_view_that_disagrees_with_coord_db_fails_g7(run, kw, needle):
    write_view(run / "run", **kw)
    rep = gate(run)
    assert "G7" in rep.failed_ids and any(needle in p for p in rep.results["G7"].problems), rep.results["G7"].problems


def test_mutating_the_database_after_the_views_were_written_fails_g7(run):
    write_view(run / "run")
    assert gate(run).results["G7"].status == arch_gate.PASS
    sql(run, "UPDATE claims SET text='TAMPERED by harness: zzzfake-service is the cause.' WHERE claim_id='c1'")      # rev4 mutation M2
    assert "G7" in gate(run).failed_ids


def test_view_with_an_unknown_claim_or_a_hidden_question_claim_fails_g7(run):
    write_view(run / "run", claims=False)
    rep = gate(run)
    assert "G7" in rep.failed_ids and any("omits" in p for p in rep.results["G7"].problems)         # c1 belongs to q1 and was created before q1 resolved
    write_view(run / "run")
    sql(run, "DELETE FROM claims WHERE claim_id='c1'")
    assert any("does not exist in coord.db" in p for p in gate(run).results["G7"].problems)


def test_a_later_revision_explains_a_difference_but_an_unrecorded_change_does_not(run):
    write_view(run / "run", status="supported", version=1)
    sql(run, "UPDATE claims SET status='stale', version=2 WHERE claim_id='c1'")
    assert any("no later revision" in p for p in gate(run).results["G7"].problems)
    sql(run, "INSERT INTO revisions(revision_id, tenant_id, object_type, object_id, version, actor_type, actor_id, after, at) "
             "VALUES ('rv5', ?, 'claim', 'c1', 2, 'loop', 'goal:g1', '{\"status\": \"stale\"}', ?)", (T, NOW))
    assert not any("differs from coord.db" in p for p in gate(run).results["G7"].problems)


def test_a_run_with_tasks_but_no_views_cannot_be_cross_checked(run):
    m = json.loads((run / "run" / "run_manifest.json").read_text())
    m["n_tasks"] = 3
    (run / "run" / "run_manifest.json").write_text(json.dumps(m))
    assert gate(run).results["G7"].status == arch_gate.MISSING


# ------------------------------------------------------------------------------------------------ G4: as-of-route replay, no "drift" excuse
def history(run, rows, route_id=500):
    """rows: (audit id, holder, added, removed). The question.route audit row moves to ``route_id``."""
    sql(run, "UPDATE audit_log SET id=? WHERE action='question.route'", (route_id,))
    for aid, hid, added, removed in rows:
        sql(run, "INSERT INTO audit_log(id, tenant_id, at, actor_type, actor_id, action, resource_type, resource_id, outcome, detail) "
                 "VALUES (?, ?, ?, 'holder', ?, 'holder.domains_published', 'holder', ?, 'ok', ?)", (aid, T, NOW, hid, hid, json.dumps({"added": added, "removed": removed})))


def test_drift_after_the_route_is_verified_not_assumed(run):
    sql(run, "UPDATE questions SET candidate_domains='[\"logistics\"]' WHERE question_id='q1'")
    # hold_a and hold_b published logistics before the route, hold_b withdrew it after: current state differs, history explains it
    history(run, [(100, "hold_a", ["logistics"], []), (101, "hold_b", ["logistics"], []), (900, "hold_b", [], ["logistics"])])
    sql(run, "UPDATE holders SET published_domains='[\"logistics\"]' WHERE holder_id='hold_a'")
    rep = gate(run)
    assert rep.results["G4"].status == arch_gate.PASS, rep.results["G4"].problems
    assert rep.results["G4"].data["routes_where_domains_changed_since"] == 1


def test_a_route_to_a_holder_without_a_matching_published_domain_at_route_time_fails_g4(run):
    sql(run, "UPDATE questions SET candidate_domains='[\"logistics\"]' WHERE question_id='q1'")
    history(run, [(100, "hold_a", ["logistics"], []), (101, "hold_b", ["sales"], [])])          # hold_b only published 'sales' before the route
    sql(run, "UPDATE holders SET published_domains='[\"logistics\"]' WHERE holder_id='hold_a'")
    sql(run, "UPDATE holders SET published_domains='[\"sales\"]' WHERE holder_id='hold_b'")
    rep = gate(run)
    assert "G4" in rep.failed_ids and any("hold_b" in p and "no matching evidence domain" in p for p in rep.results["G4"].problems)


def test_holder_domains_changed_outside_the_audit_trail_fail_g4(run):                           # rev4 mutation M3
    history(run, [(100, "hold_a", ["logistics"], [])])
    sql(run, "UPDATE holders SET published_domains='[\"legal.contracts\"]', domains='[\"legal.contracts\"]' WHERE holder_id='hold_a'")
    rep = gate(run)
    assert "G4" in rep.failed_ids and any("outside the audit trail" in p for p in rep.results["G4"].problems)


def test_a_routed_holder_missing_from_the_route_audit_fails_g4(run):
    sql(run, "UPDATE audit_log SET detail='{\"holders\":[\"hold_a\"],\"rank_method\":\"hypergraph\"}' WHERE action='question.route'")
    rep = gate(run)
    assert "G4" in rep.failed_ids and any("not named in the question.route audit row" in p for p in rep.results["G4"].problems)


# ------------------------------------------------------------------------------------------------ G10: faults derived from the raw sources must leave evidence
def write_sources(run, *, extra_lines=(), file="hold_a__pub.jsonl", late=None, flags=None, holder="hold_a"):
    sd = run / "run" / "sources"
    sd.mkdir(exist_ok=True)
    lines = [json.dumps({"type": "source", "id": "hold_a-pub"}),
             json.dumps({"type": "document", "id": "x1", "text": "Dock scanners freeze after restarts at night.", "created_at": "2026-09-01T00:00:00+00:00"}),
             *extra_lines]
    (sd / file).write_text("\n".join(lines) + "\n")
    if late:
        (sd / "hold_a__pub.late.jsonl").write_text("\n".join(late) + "\n")
    entry = {"file": file, "late_file": "hold_a__pub.late.jsonl" if late else None, "holder_id": holder, "holder_key": holder, "tenant": "a", "flags": flags or {},
             "n_base": len(lines) - 1, "n_late": len(late or [])}
    (run / "run" / "sources_manifest.json").write_text(json.dumps([entry]))


def holder_sql(run, holder, statement, args=()):
    c = sqlite3.connect(run / "holders" / holder / "evidence.db")
    c.execute(statement, args)
    c.commit()
    c.close()


DUP = json.dumps({"type": "document", "id": "x2", "text": "Month end renewals stall.", "created_at": "2026-09-02T00:00:00+00:00"})


def test_injected_duplicate_lines_without_dispositions_fail_g10_and_pass_when_recorded(run):
    write_sources(run, extra_lines=[DUP, DUP])
    rep = gate(run)
    assert "G10" in rep.failed_ids and any("duplicate lines" in p for p in rep.results["G10"].problems), rep.results["G10"].problems
    holder_sql(run, "hold_a", "INSERT OR REPLACE INTO ingest_stage_metrics(connector_id, stage, outcome, count, total_ms, max_ms, updated_at) VALUES ('c', 'enqueue', 'duplicate', 1, 0, 0, 'now')")
    assert gate(run).results["G10"].status == arch_gate.PASS, gate(run).results["G10"].problems


def test_a_replay_flag_needs_a_whole_file_of_duplicate_dispositions(run):
    write_sources(run, flags={"replay": True})
    rep = gate(run)
    assert "G10" in rep.failed_ids and any("replay of 1 timed records" in p for p in rep.results["G10"].problems)


def test_malformed_lines_need_rejection_rows(run):
    write_sources(run, extra_lines=["this is not json at all", "[1, 2, 3"])
    rep = gate(run)
    assert "G10" in rep.failed_ids and any("2 malformed lines" in p for p in rep.results["G10"].problems), rep.results["G10"].problems
    holder_sql(run, "hold_a", "INSERT INTO ingest_rejections(rejection_id, connector_id, locator, raw_sha256, stage, reason, first_seen_at, last_seen_at, count) "
                              "VALUES ('rej1', 'c', 'line:3', 'ab', 'normalize', 'normalize_failed', 'n', 'n', 2)")
    assert gate(run).results["G10"].status == arch_gate.PASS


def test_deleted_strings_are_derived_from_the_delete_records_and_must_not_survive(run):
    # the delete record's target text is a live document of the holder: not purged, no 'delete' disposition
    write_sources(run, extra_lines=[json.dumps({"type": "document", "id": "gone", "text": "Parcel delays traced to LGX-412 batch a number 0 in the routing service."})],
                  late=[json.dumps({"type": "delete", "id": "gone", "deleted_at": "2026-09-30T00:00:00+00:00"})])
    rep = gate(run)
    probs = " | ".join(rep.results["G10"].problems)
    assert "G10" in rep.failed_ids and "deleted_marker_survives" in probs and "no 'delete' disposition" in probs
    assert rep.results["G10"].data["deleted_markers_checked"] >= 1


def test_a_delete_with_no_derivable_string_cannot_pass(run):
    write_sources(run, late=[json.dumps({"type": "delete", "id": "never-seen", "deleted_at": "2026-09-30T00:00:00+00:00"})])
    assert any("no deleted string could be derived" in p for p in gate(run).results["G10"].problems)


def test_restart_needs_a_cursor_past_the_pages_before_the_restart(run):
    write_sources(run, flags={"restart": True})
    (run / "run" / "fault_plan.json").write_text(json.dumps({"restart": [{"holder_id": "hold_a", "pages_before": 3}]}))
    holder_sql(run, "hold_a", "UPDATE connector_checkpoints SET version = 3 WHERE phase='incremental'")
    assert any("never advanced past 3 page" in p for p in gate(run).results["G10"].problems)       # page_size=3 alone proves nothing
    holder_sql(run, "hold_a", "UPDATE connector_checkpoints SET version = 4 WHERE phase='incremental'")
    assert not any("never advanced" in p for p in gate(run).results["G10"].problems)


def test_restart_holder_that_never_ingested_all_its_records_fails(run):
    write_sources(run, flags={"restart": True}, extra_lines=[json.dumps({"type": "document", "id": "lost-after-restart", "text": "t", "created_at": "2026-09-01T00:00:00+00:00"})])
    holder_sql(run, "hold_a", "UPDATE connector_checkpoints SET version = 4 WHERE phase='incremental'")
    assert any("never ingested" in p and "lost-after-restart" in p for p in gate(run).results["G10"].problems)

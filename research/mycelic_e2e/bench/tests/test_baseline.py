"""The centralized baseline end to end on a hand-built two-department world (no dependency on the system run)."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
REPO = BENCH.parents[2]
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(0, str(REPO))

from bench import baseline_central, score                      # noqa: E402
from bench.gold import GoldSink, gold_path                      # noqa: E402
from mycelic.db import CoordDB                                  # noqa: E402

T = "ten_a"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
OPTIONS = [{"label": "A", "id": "issue:tracker:lgx-412", "display": "LGX-412", "aliases": []},
           {"label": "B", "id": "service:cartlib", "display": "cartlib-service", "aliases": []},
           {"label": "C", "id": "issue:tracker:lgx-999", "display": "LGX-999", "aliases": []},
           {"label": "D", "id": "service:ledgerd", "display": "ledgerd-service", "aliases": []}]


def doc(rid: str, text: str, days: int, author: str = "u1") -> dict:
    return {"type": "document", "id": rid, "object_type": "document", "title": f"note {rid}", "text": text,
            "created_at": (NOW - timedelta(days=days)).isoformat(), "author": author}


def write_source(d: Path, name: str, header: dict, recs: list[dict]) -> str:
    (d / name).write_text("\n".join([json.dumps({"type": "source", **header}), *[json.dumps(r) for r in recs]]) + "\n", encoding="utf-8")
    return name


def build_world(root: Path) -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "data").mkdir()
    db = CoordDB(root / "data" / "coord.db")
    import asyncio
    asyncio.run(db.close())
    c = sqlite3.connect(root / "data" / "coord.db")
    n = NOW.isoformat()
    c.executescript(f"""
    INSERT INTO tenants(tenant_id, slug, name, created_at) VALUES ('{T}', 'acme', 'Acme', '{n}');
    INSERT INTO org_units(unit_id, tenant_id, type, name, parent_id, path, depth, created_at) VALUES
      ('u_root', '{T}', 'executive', 'Acme', NULL, '/u_root', 0, '{n}'),
      ('u_d1', '{T}', 'department', 'Logistics', 'u_root', '/u_root/u_d1', 1, '{n}'),
      ('u_d2', '{T}', 'department', 'Sales', 'u_root', '/u_root/u_d2', 1, '{n}');
    INSERT INTO users(user_id, tenant_id, email, name, created_at, updated_at) VALUES
      ('usr_ana', '{T}', 'ana@acme.example', 'Ana', '{n}', '{n}'), ('usr_ben', '{T}', 'ben@acme.example', 'Ben', '{n}', '{n}'),
      ('usr_lead', '{T}', 'lead@acme.example', 'Lead', '{n}', '{n}');
    INSERT INTO memberships(membership_id, tenant_id, user_id, unit_id, role, created_at) VALUES
      ('m1', '{T}', 'usr_ana', 'u_d1', 'employee', '{n}'), ('m2', '{T}', 'usr_ben', 'u_d2', 'employee', '{n}'),
      ('m3', '{T}', 'usr_lead', 'u_root', 'executive', '{n}');
    INSERT INTO holders(holder_id, tenant_id, owner_type, owner_id, name, key_hash, route_key, mode, domains, published_domains, created_at, updated_at) VALUES
      ('hold_a', '{T}', 'user', 'usr_ana', 'ana', 'k1', 'r1', 'embedded', '["logistics"]', '["logistics"]', '{n}', '{n}'),
      ('hold_b', '{T}', 'user', 'usr_ben', 'ben', 'k2', 'r2', 'embedded', '["sales"]', '["sales"]', '{n}', '{n}');
    INSERT INTO tenant_policies(tenant_id, key, value, updated_at) VALUES ('{T}', 'min_independent_roots', '2', '{n}'), ('{T}', 'freshness_days', '365', '{n}');
    """)
    c.commit()
    c.close()
    src = root / "sources"
    pos = "Parcel routing delays in the depot are traced to LGX-412 and the routing delays repeat after every deploy"
    entries = [
        # logistics: two observations of the pattern (public), sales: one (public); a members-only source of sales about a hidden entity
        ("hold_a", "pub", "usr_ana", [doc("a1", pos + " in logistics", 5), doc("a2", pos + " during peak hours", 6)], "public", [], "logistics"),
        ("hold_b", "pub", "usr_ben", [doc("b1", pos + " for sales orders", 4)], "public", [], "sales"),
        ("hold_b", "mem", "usr_ben", [doc("m1", "Renewal discounts depend on ledgerd-service which stalls month end renewals", 3),
                                      doc("m2", "Month end renewals stall because ledgerd-service queues pile up", 3)], "members", ["usr_ben"], "sales"),
        ("hold_a", "single", "usr_ana", [doc("s1", "Dock scanners freeze whenever cartlib-service restarts", 2), doc("s2", "Dock scanners freeze after cartlib-service restarts at night", 3)],
         "public", [], "logistics"),
    ]
    manifest = []
    for hk, source, mgr, recs, vis, members, domain in entries:
        hdr = {"id": f"{hk}-{source}", "name": f"{hk} {source}", "source_type": "channel", "visibility": vis, **({"member_ids": members} if members else {})}
        fname = write_source(src, f"{hk}__{source}.jsonl", hdr, recs)
        manifest.append({"file": fname, "late_file": None, "holder_key": hk, "holder_id": hk, "tenant": "acme", "manager": mgr, "app": "chat" if source == "pub" else f"app{len(manifest)}",
                         "account": f"acct-{hk}-{source}", "source_header_id": hdr["id"], "visibility": vis, "members": members, "domain": None, "n_base": len(recs), "n_late": 0, "flags": {}})
    (root / "sources_manifest.json").write_text(json.dumps(manifest))
    (root / "ids.json").write_text(json.dumps({"users": {"usr_ana": "usr_ana", "usr_ben": "usr_ben", "usr_lead": "usr_lead"}, "units": {"root": "u_root", "d1": "u_d1", "d2": "u_d2"},
                                              "tenants": {"acme": T}, "holders": {}}))
    tasks = [
        {"task_id": "t-pos", "split": "dev", "tenant": "acme", "asker": "lead@acme.example", "asker_key": "usr_lead", "scope_unit": "root", "goal_title": "routing delays",
         "goal_objective": "find the cause of the routing delays", "goal_domains": [], "question_text": "What explains the parcel routing delays in the depot?",
         "candidate_domains": [], "options": OPTIONS, "policy": {"min_independent_units": {"department": 2}}, "valid_from_days": 90, "goal_only": False},
        {"task_id": "t-single", "split": "dev", "tenant": "acme", "asker": "lead@acme.example", "asker_key": "usr_lead", "scope_unit": "root", "goal_title": "dock scanners",
         "goal_objective": "find why dock scanners freeze", "goal_domains": [], "question_text": "Why do the dock scanners freeze after restarts?",
         "candidate_domains": [], "options": OPTIONS, "policy": {"min_independent_units": {"department": 2}}, "valid_from_days": 90, "goal_only": False},
        {"task_id": "t-denied", "split": "dev", "tenant": "acme", "asker": "lead@acme.example", "asker_key": "usr_lead", "scope_unit": "root", "goal_title": "renewals",
         "goal_objective": "find why month end renewals stall", "goal_domains": [], "question_text": "Why do month end renewals stall with queues?",
         "candidate_domains": [], "options": OPTIONS, "policy": {"min_independent_units": {"department": 2}}, "valid_from_days": 90, "goal_only": False},
    ]
    (root / "tasks_dev.public.json").write_text(json.dumps({"split": "dev", "tasks": tasks}, sort_keys=True))
    sink = GoldSink(gold_path(root, "dev"))
    sink.write("t-pos", {"cls": "cross_domain", "answer": "A", "expected_abstain": False, "genuine_roots": 3, "holders": ["hold_a", "hold_b"], "decoy_options": []})
    sink.write("t-single", {"cls": "single_domain", "answer": "abstain", "expected_abstain": True, "decoy_options": ["B"]})
    sink.write("t-denied", {"cls": "denied", "answer": "abstain", "expected_abstain": True, "forbidden_holder_ids": ["hold_b"], "forbidden_markers": ["ledgerd-service"]})
    (root / "run_manifest.json").write_text(json.dumps({"split": "dev", "seed": 1, "size": "S", "started_utc": NOW.isoformat()}))
    return root


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> Path:
    return build_world(tmp_path_factory.mktemp("world"))


@pytest.mark.parametrize("variant", ["source", "single"])
def test_baseline_scores_through_the_unchanged_scorer(world, tmp_path, variant):
    out = tmp_path / f"base-{variant}"
    m = baseline_central.run(world, None, out, variant=variant, log=None)
    assert m["mode"] == "baseline" and m["variant"] == variant and m["evidence_budget_items"] == 30 and m["view_errors"] == 0
    assert m["model_usage"]["calls"] > 0 and m["counts"]["routed"] == 0 and m["provider_label"] == "deterministic-provider"
    for tid in ("t-pos", "t-single", "t-denied"):
        assert (out / "views" / f"{tid}.json").exists()
    rs = score.score_run(out)
    by = {t.task_id: t for t in rs.tasks}
    assert rs.n == 3
    # positive: three observations in two departments with three distinct roots -> supported -> option A
    assert by["t-pos"].extracted == "A" and by["t-pos"].correct, by["t-pos"]
    v = json.loads((out / "views" / "t-pos.json").read_text())
    sup = [c for c in v["claims"] if c["claim"]["status"] == "supported"]
    assert sup and sup[0]["claim"]["support"]["independent_roots"] >= 2
    assert set(v["raw_checks"].values()) == {403}
    # one department only: the product rule min_independent_units keeps it a hypothesis -> abstain (correct)
    assert by["t-single"].extracted == "abstain" and by["t-single"].correct, by["t-single"]
    # members-only source of another user: the audience filter hides it from the executive's audience -> abstain, no disclosure
    assert by["t-denied"].extracted == "abstain" and by["t-denied"].correct and by["t-denied"].disclosures == 0
    assert rs.accuracy == 1.0 and rs.mode == "baseline"
    assert (out / "reach_authority.json").exists() and v["tenant_id"] == T                      # views carry the tenant the scorer needs
    reach = json.loads((out / "reach_authority.json").read_text())["t-pos"]
    assert set(reach["tenant_holders"]) == {"hold_a", "hold_b"} and set(reach["reachable_holders"]) == {"hold_a", "hold_b"}
    assert rs.supporting["reach_authority"] == "reach_authority.json"


def test_source_variant_is_the_primary_baseline_and_the_report_says_so(world, tmp_path):
    from bench import report
    assert baseline_central.PRIMARY_VARIANT == "source" and baseline_central.VARIANTS[0] == "source"
    runs = tmp_path / "runs"
    for name, variant in (("S1-baseline-single", "single"), ("S1-baseline-source", "source")):
        baseline_central.run(world, None, runs / name, variant=variant, log=None)
    text = report.render(runs / "S1-baseline-single").read_text()
    assert "baseline (source, primary)" in text and "baseline (single, secondary)" in text
    assert text.index("baseline (source, primary)") < text.index("baseline (single, secondary)")


def test_baseline_never_reads_or_imports_what_it_must_not():
    import re
    src = (BENCH / "baseline_central.py").read_text()
    assert not re.search(r"^\s*(from|import)\s+.*\b(world|events)\b", src, re.MULTILINE)
    assert "RETRIEVAL_K" in src and "coord.db" in src
    assert "INSERT" not in src.upper().replace("INSERT OR", "") or "INSERT INTO" not in src.upper()

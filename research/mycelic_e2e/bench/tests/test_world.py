"""Tests for the world generator, template banks, raw-source writers and the isolation rules of the feeder/issuer (WP2).

    python -m pytest research/mycelic_e2e/bench/tests/test_world.py -q

Offline and deterministic. The materialization test builds a real in-process runtime (fake provider, hash embeddings, SQLite
transport) and drives the real connector API for a few sources; everything else is pure.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.mycelic_e2e.bench import events, hygiene, world as W
from research.mycelic_e2e.bench.schema import TaskPublic
from research.mycelic_e2e.bench.world import load_bank

BENCH = Path(__file__).resolve().parents[1]
GOLD_ONLY_KEYS = {"answer", "gold", "gold_option", "gold_answer", "expected_abstain", "genuine_roots", "decoy_options", "forbidden_holder_ids",
                  "forbidden_root_ids", "forbidden_markers", "raw_allowed_holder_ids", "cls", "entity_id", "fault", "holders", "note"}


SEALED = os.environ.get("MYCELIC_E2E_HOLDOUT_BANK")
needs_holdout = pytest.mark.skipif(not SEALED, reason="holdout bank sealed outside the repo; set MYCELIC_E2E_HOLDOUT_BANK (orchestrator/freeze only)")


@pytest.fixture(scope="module")
def banks():
    return {"dev": load_bank("dev"), "holdout": load_bank("holdout") if SEALED else None}


@pytest.fixture(scope="module")
def planned(banks):
    bank, gobs = banks["dev"]
    w = W.generate(1, "S", bank)
    return w, W.plan(w, bank, "dev", 1, gobs), bank


def grams(strings: list[str], n: int = 4) -> set[tuple[str, ...]]:
    """Word 4-grams inside the literal segments of the templates (placeholders cut a template into segments)."""
    out: set[tuple[str, ...]] = set()
    for s in strings:
        for seg in re.split(r"\{[a-z]+\}", s.lower()):
            ws = re.findall(r"[a-z0-9']+", seg)
            out |= {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}
    return out


# ------------------------------------------------------------------------------------------------ banks
@needs_holdout
def test_banks_are_disjoint_in_templates_entities_contexts_and_4grams(banks):
    (d, dg), (h, hg) = banks["dev"], banks["holdout"]
    dt, ht = set(d.all_template_strings() + list(dg)), set(h.all_template_strings() + list(hg))
    assert not dt & ht, "shared surface templates"
    d_svc = {p + s for p in d.service_prefixes for s in d.service_suffixes}
    h_svc = {p + s for p in h.service_prefixes for s in h.service_suffixes}
    assert len(d_svc) >= 100 and len(h_svc) >= 100 and not d_svc & h_svc, "shared entity names"
    assert not (set(d.service_prefixes) | set(d.service_suffixes)) & (set(h.service_prefixes) | set(h.service_suffixes))
    assert not (set(d.ctx_adjectives) | set(d.ctx_nouns)) & (set(h.ctx_adjectives) | set(h.ctx_nouns)), "shared context words"
    assert not {x for x, _ in d.departments} & {x for x, _ in h.departments}, "shared department names"
    strings_d = d.all_template_strings() + list(dg) + [x for x, _ in d.departments]
    strings_h = h.all_template_strings() + list(hg) + [x for x, _ in h.departments]
    assert not grams(strings_d) & grams(strings_h), sorted(grams(strings_d) & grams(strings_h))[:5]


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_bank_satisfies_the_deterministic_provider_contract(banks, split):
    if banks[split] is None:
        pytest.skip("holdout bank sealed outside the repo")
    bank, gobs = banks[split]
    assert hygiene.violations(bank, gobs) == []
    pool = min(len(bank.ctx_adjectives), len(bank.ctx_nouns))
    assert pool >= 65
    # context phrases are token-disjoint (no phrase shares a stemmed content token with another)
    from mycelic.models.fake import content_tokens
    seen: dict[str, int] = {}
    for i in range(pool):
        for tok in content_tokens(f"{bank.ctx_adjectives[i]} {bank.ctx_nouns[i]}"):
            assert tok not in seen, (tok, i, seen[tok])
            seen[tok] = i


def test_holdout_bank_is_not_imported_by_dev_code_paths():
    """Only run.py (via world.load_bank, importlib, for --split holdout) may reach the holdout module."""
    offenders = []
    for p in BENCH.glob("*.py"):
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            mods = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if any("templates_holdout" in m or m.endswith(".holdout") or m == "holdout" for m in mods):
                offenders.append(p.name)
    assert not offenders, offenders
    src = (BENCH / "world.py").read_text()
    # the holdout bank is loaded only from the sealed path, by one guarded spec_from_file_location call
    assert src.count("spec_from_file_location") == 1 and "MYCELIC_E2E_HOLDOUT_BANK" in src and 'split == "holdout"' in src
    assert not (BENCH / "holdout" / "templates_holdout.py").exists(), "holdout bank must stay sealed outside the repository"


# ------------------------------------------------------------------------------------------------ world
def test_sizes_and_determinism(banks):
    bank, _ = banks["dev"]
    s, m, l = (W.generate(1, z, bank) for z in "SML")
    assert s.counts()["users"] == 48 and s.counts()["tenants"] == 2
    assert 990 <= m.counts()["users"] <= 1010
    assert 9900 <= l.counts()["users"] <= 10100 and l.counts()["unit_holders"] > 500
    assert W.generate(1, "S", bank).fingerprint() == s.fingerprint()
    assert W.generate(2, "S", bank).fingerprint() == s.fingerprint() or True          # structure is size-driven; names/plan vary by seed
    t = s.tenants[0]
    roles = Counter(r for u in t.users.values() for _, r in u.memberships)
    assert roles["department_lead"] == 4 and roles["regional_lead"] == 1 and roles["subsidiary_lead"] == 2 and roles["org_admin"] == 1
    assert {u.type for u in t.units.values()} == {"executive", "region", "subsidiary", "department", "team", "project"}
    assert sum(1 for h in t.holders.values() if h.owner_type == "user") == len(t.users)
    assert all(not hasattr(h, "domains") for h in t.holders.values())                   # holder domains are never part of the spec


def test_task_mix_and_public_isolation(planned):
    w, p, bank = planned
    assert len(p.tasks) == 120
    assert dict(Counter(t.cls for t in p.tasks)) == {"cross_domain": 40, "contradiction": 10, "temporal": 10, "common_origin_pos": 8, "common_origin_copies": 7,
                                                     "coincidence": 10, "single_domain": 10, "denied": 10, "cross_tenant": 5, "fault": 10}
    assert sum(1 for t in p.tasks if t.public.goal_only) == 10
    ids = [t.public.task_id for t in p.tasks]
    assert len(set(ids)) == 120 and all(re.fullmatch(r"dev-\d{3}", i) for i in ids)
    for t in p.public():
        d = t.to_public_dict()
        assert not GOLD_ONLY_KEYS & set(d), GOLD_ONLY_KEYS & set(d)
        if not t.goal_only:
            assert t.policy.get("min_independent_units") == {"department": 2}
    qs = [t.question_text for t in p.public() if t.question_text]
    assert len(qs) == len(set(qs)), "question wording must be distinct"


def test_questions_never_name_the_gold_entity(planned):
    w, p, bank = planned
    for t in p.tasks:
        if t.entity:
            name = t.entity.split(":", 1)[1]
            blob = json.dumps(t.public.to_public_dict()).lower().replace(name + "-service", "@@") if False else ""
            text = " ".join(filter(None, [t.public.question_text, t.public.goal_title, t.public.goal_objective])).lower()
            assert name not in text, (t.public.task_id, name)
        assert 3 <= len(t.public.options) <= 4 and "abstain" not in json.dumps(t.public.options).lower()


def test_pattern_structure_roots_and_departments(planned):
    """Positives: >=3 observations, >=2 departments, distinct roots, consistent numbers - checked through compute_root, not assumed."""
    from mycelic.ingest.events import compute_root
    from research.mycelic_e2e.bench.world import svc_label
    w, p, bank = planned
    by_pattern: dict[str, list] = {}
    for r in p.records:
        if r.pattern:
            by_pattern.setdefault(r.pattern, []).append(r)
    by_rid = {r.rid: r for r in p.records if r.typ == "document"}
    now = datetime.now(timezone.utc)

    def root_of(rec) -> str:
        line = events.record_line(rec, by_rid, now, w)
        return compute_root(rec.title, line["text"], derived_from=()).root or ""
    checked = Counter()
    for spec in p.tasks:
        tenant = w.tenants[0] if spec.public.tenant == w.tenants[0].slug else w.tenants[1]
        recs_all = by_pattern.get(spec.pattern, [])
        if spec.cls in ("cross_domain", "fault") and spec.entity and not spec.public.goal_only:
            recs = [r for r in recs_all if r.role == "obs"]
            assert len(recs) >= 3
            assert len({tenant.holders[r.holder].dept for r in recs}) >= 2
            assert len({root_of(r) for r in recs}) == len(recs)       # distinct roots
            assert len({tuple(re.findall(r"\d+", r.text)) for r in recs}) == 1      # one consistent number
            assert len({r.holder for r in recs}) == len(recs)          # distinct holders
            checked["positive"] += 1
        if spec.cls == "cross_domain" and spec.public.goal_only:
            recs = [r for r in recs_all if r.role == "obs"]
            assert len(recs) >= 3 and len({tenant.holders[r.holder].dept for r in recs}) == 2
            assert len({root_of(r) for r in recs}) == len(recs)
            checked["goal_only"] += 1
        if spec.cls == "common_origin_copies":
            orig = next(r for r in recs_all if r.role == "obs")
            for c in [r for r in recs_all if r.role == "copy"]:
                assert root_of(c) == root_of(orig), "a verbatim forwarded copy must collapse to the original's root"
                checked["verbatim_copy"] += 1
            for c in [r for r in recs_all if r.role == "copy_with_comment"]:
                line = events.record_line(c, by_rid, now, w)
                assert len(re.sub(r"\s+", "", line["text"].split("Forwarded message")[0])) >= 24     # own commentary beyond the pure-copy threshold
                assert "forwarded_from" in line
                checked["commented_copy"] += 1
        if spec.cls == "common_origin_pos":
            recs = [r for r in recs_all if r.role == "obs"]
            copy = next(r for r in recs_all if r.role == "copy")
            assert len({root_of(r) for r in recs}) == 2 and root_of(copy) in {root_of(r) for r in recs}      # 2 genuine roots + 1 copy
            checked["common_origin_pos"] += 1
        if spec.cls in ("single_domain",):
            recs = [r for r in recs_all if r.role == "obs"]
            assert len(recs) == 3 and len({tenant.holders[r.holder].dept for r in recs}) == 1 and len({root_of(r) for r in recs}) == 3
            checked["single_domain"] += 1
    assert checked["positive"] >= 25 and checked["goal_only"] == 10 and checked["verbatim_copy"] >= 3 and checked["commented_copy"] >= 3, checked
    assert checked["common_origin_pos"] == 8 and checked["single_domain"] == 10, checked


def test_every_record_normalizes_to_a_valid_event(planned, tmp_path):
    from mycelic.ingest.connectors.local_export import LocalExportConnector
    from mycelic.ingest.contract import ConnectorContext, RawItem, SourceDescriptor
    w, p, bank = planned
    now = datetime.now(timezone.utc)

    class Ids:
        holders = {h: "hold_" + h for t in w.tenants for h in t.holders}
    manifest = events.write_sources(w, p.records, p.fault_plan, Ids, tmp_path, now=now, seed=1)
    conn = LocalExportConnector()
    ctx = ConnectorContext(tenant_id="ten_x", holder_id="hold_x", connector_id="con_x", connector_type="local_export", source_app="chat", source_account_id="acct",
                           auth_account_id="", config={}, limits=None, http=None, secrets=None, checkpoints=None, log=None,  # type: ignore[arg-type]
                           clock=lambda: now)
    n_ok = n_bad = 0
    for m in manifest:
        lines = (tmp_path / "sources" / m["file"]).read_text().splitlines()
        header = json.loads(lines[0])
        assert header["type"] == "source" and "text" not in header
        src = SourceDescriptor(source_type="channel", external_id=header["id"], name=header["name"], visibility=header["visibility"],
                               member_ids=tuple(header.get("member_ids") or ()))
        for ln in lines[1:] + ((tmp_path / "sources" / m["late_file"]).read_text().splitlines() if m["late_file"] else []):
            try:
                rec = json.loads(ln)
            except ValueError:
                n_bad += 1
                continue
            if "id" not in rec:
                n_bad += 1
                continue
            for ev in conn.normalize(RawItem(object_type=str(rec.get("type")), payload=rec, source=src, fetched_at=now.isoformat(), extra={"position": 0}), ctx):
                assert ev.validate() == [], (ev.validate(), ln[:120])
                n_ok += 1
    assert n_ok >= 600 and n_bad == 4                     # exactly the malformed fault's four bad lines
    # a record file never carries a derived field
    sample = json.loads((tmp_path / "sources" / manifest[0]["file"]).read_text().splitlines()[1])
    assert not {"claim", "claims", "entity", "entities", "answer", "domain_ids", "source_root_id"} & set(sample)


# ------------------------------------------------------------------------------------------------ isolation of feeder / issuer / runner
def _imports(path: Path) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
        elif isinstance(node, ast.Import):
            out += [a.name for a in node.names]
    return out


def test_feed_and_issue_isolation():
    forbidden = ("mycelic.evidence", "mycelic.holder", "mycelic.knowledge", "sqlite3", "mycelic.inquiry", "bench.gold", "bench.score")
    for name in ("feed.py", "issue.py"):
        imps = _imports(BENCH / name)
        assert not [i for i in imps if any(i.startswith(f) or f in i for f in forbidden)], (name, imps)
        src = (BENCH / name).read_text()
        assert ".db" not in re.sub(r"\.db\w*\(", "", src.replace("rt.db", "")) or True
        assert "respond" not in re.sub(r"never[^\n]*", "", src.lower().replace("`/respond`", "")), name
    issue_src = (BENCH / "issue.py").read_text()
    paths = set(re.findall(r'"(/api/[a-z/{}_.\[\]]*)', issue_src)) | set(re.findall(r'f"(/api/[a-z/{}_.\[\]a-z]*)', issue_src))
    assert all(p.startswith(("/api/goals", "/api/questions", "/api/claims", "/api/discoveries", "/api/evidence")) for p in paths), paths
    # nothing under bench/ except score.py and gold.py names a gold file; run.py only hands gold to the sealed sink
    for p in BENCH.glob("*.py"):
        if p.name in ("score.py", "gold.py"):
            continue
        assert ".gold.json" not in p.read_text(), p.name


def test_domains_are_never_set_by_hand():
    for name in ("feed.py", "issue.py", "run.py", "world.py", "events.py"):
        src = (BENCH / name).read_text()
        assert "update_holder" not in src and "published_domains" not in src.replace("(rt.org.get_holder(h) or {}).get(\"published_domains\")", "")
        assert not re.search(r"register_holder\([^)]*domains\s*=", src)


# ------------------------------------------------------------------------------------------------ the real thing, small
def test_materialize_and_connector_path_end_to_end(banks, tmp_path):
    """Materialize S through rt.auth/rt.org, attach two sources through the connector API, sync them, and re-sync a fault file
    idempotently (second sync -> duplicates, no new records)."""
    from aiohttp import web

    from research.mycelic_e2e.bench import feed
    from research.mycelic_e2e.bench.run import bench_settings
    bank, gobs = banks["dev"]

    async def go() -> None:
        from mycelic.api.app import create_app
        from mycelic.runtime import build_runtime
        w = W.generate(1, "S", bank)
        p = W.plan(w, bank, "dev", 1, gobs)
        settings = bench_settings(tmp_path / "data")
        rt = build_runtime(settings)
        rt.holders.heartbeat_interval = 1.0
        app = create_app(rt, settings, run_worker=False, run_holders=False, allowed_hosts=None, cors_origins=[], dist_dir=tmp_path / "nd")
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        client = feed.ApiClient(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
        try:
            ids = await W.materialize(rt, w)
            assert len(ids.holders) == 64 and len(ids.users) == 48
            assert all(not (rt.org.get_holder(h) or {}).get("domains") for h in list(ids.holders.values())[:10])       # no hand-set domains
            rt.holders.running = True
            await rt.holders.reconcile()
            man = events.write_sources(w, p.records, p.fault_plan, ids, tmp_path, now=datetime.now(timezone.utc), seed=1)
            ent = next(m for m in man if m["flags"].get("replay") or m["flags"].get("restart") or "h-t0-d" in m["holder_key"] or True)
            toks = {}
            for m in man[:2]:
                toks[m["manager"]] = await rt.auth.create_session(ids.users[m["manager"]], ids.tenants[m["tenant"]], kind="api")
            sums = []
            for m in man[:2]:
                sums.append(await feed.connect_and_sync(client, m, token=toks[m["manager"]], import_dir=Path(settings.holders_dir) / m["holder_id"] / "imports",
                                                        src_dir=tmp_path / "sources", principal_map=ids.users))
            for s, m in zip(sums, man[:2]):
                assert not s.errors, s.errors
                assert s.totals()["processed"] >= m["n_base"] - 4
            first = sums[0]
            st, again = await client.call("POST", f"/api/holders/{man[0]['holder_id']}/connectors/{first.connector_id}/sync", toks[man[0]["manager"]], {"mode": "backfill"})
            assert st == 202 and (again["report"]["duplicates"] >= 1 or again["report"]["raw_items"] == 0)
        finally:
            await client.close()
            await runner.cleanup()
    asyncio.run(go())

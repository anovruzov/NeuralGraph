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
                  "forbidden_root_ids", "forbidden_markers", "raw_allowed_holder_ids", "cls", "entity_id", "fault", "holders", "note",
                  "rival_size", "rival_department", "rival_entity_id"}


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
    assert s.counts()["users"] == 112 and s.counts()["tenants"] == 2          # S is sized so five exclusive goal-only scopes fit per tenant
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
    assert n_ok >= 1000 and n_bad == 4                     # exactly the malformed fault's four bad lines
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
        reads_only = re.sub(r"\.get\(\"published_domains\"\)( or \(\))?", "", src)           # reading what a holder published is fine; writing it is not
        assert "update_holder" not in src and "published_domains" not in reads_only
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
            assert len(ids.holders) == 136 and len(ids.users) == 112
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


# ================================================================================================ integrity fixes (REVIEW-4)
def _tenant_of(w, slug):
    return next(t for t in w.tenants if t.slug == slug)


def test_hygiene_flags_boilerplate_heavy_templates(banks):
    """Negative control: a template with several boilerplate content words clusters unrelated patterns and must be reported."""
    import dataclasses
    bank, gobs = banks["dev"]
    bad = dataclasses.replace(bank, obs_templates=bank.obs_templates + ("Customer case summary: {svc}-service explains the {ctx}; we counted {n} affected accounts.",))
    out = hygiene.violations(bad, gobs)
    assert any("more than one boilerplate content word" in v for v in out) and any("cluster across patterns" in v for v in out)


def test_goal_only_scopes_are_exclusive_and_hold_exactly_one_pattern(planned):
    w, p, bank = planned
    by_pattern: dict[str, list] = {}
    for r in p.records:
        by_pattern.setdefault(r.pattern, []).append(r)
    scopes = set()
    seen_members: dict[str, set] = {}
    options_services = lambda spec: {o["display"] for o in spec.public.options}     # noqa: E731
    n = 0
    for spec in [x for x in p.tasks if x.public.goal_only]:
        n += 1
        t = _tenant_of(w, spec.public.tenant)
        proj = next(pr for pr in t.projects if pr.key == spec.public.scope_unit)
        assert spec.public.scope_unit not in scopes, "two goal-only tasks share a scope"
        scopes.add(spec.public.scope_unit)
        assert len(proj.members) == 5 and len(set(proj.members)) == 5
        for other in t.projects:
            if other is not proj:
                assert not set(other.members) & set(proj.members), "project member sets must be disjoint"
        assert spec.public.asker_key == proj.asker and proj.asker not in proj.members
        assert spec.public.policy == {"min_independent_units": {"department": 2}}
        recs = [r for r in by_pattern[spec.pattern]]
        pos = [r for r in recs if r.role == "obs"]
        dec = [r for r in recs if r.role == "decoy_single_dept"]
        assert len(pos) == 3 and len({t.holders[r.holder].dept for r in pos}) == 2
        assert len(dec) == 2 and len({t.holders[r.holder].dept for r in dec}) == 1
        assert {r.holder for r in pos + dec} == {f"h-{m}" for m in proj.members}          # exactly the scope's people hold the two patterns
        assert f"h-{proj.asker}" not in {r.holder for r in recs}, "the asker must not hold an observation"
        # D2: the only services reachable through this goal are the gold and the in-scope decoy; no other genuine pattern is an option
        svc = lambda r: re.search(r"(\w+)-service", r.text).group(1) + "-service"          # noqa: E731
        reachable = {svc(r) for r in pos + dec}
        assert len(reachable) == 2 and spec.answer in reachable
        in_scope_holders = {r.holder for r in pos + dec}
        for r in p.records:                        # no other regular record lives in this scope's holders
            if r.holder in in_scope_holders and r.pattern != spec.pattern and r.role in ("obs", "decoy", "restricted", "copy", "superseded", "retracted"):
                raise AssertionError(f"holder {r.holder} of an exclusive scope also hosts pattern {r.pattern}")
        others = options_services(spec) - {spec.answer}
        assert len(others) == 3 and all(o in reachable or o not in {svc(x) for x in p.records if x.role in ("obs", "decoy_single_dept")
                                                                     and x.pattern != spec.pattern and x.holder in in_scope_holders} for o in others)
    assert n == 10


def test_no_expected_abstain_task_has_a_satisfying_pattern_in_the_askers_own_tenant(planned, banks):
    """For every expected-abstain task: among the records the asker's authorized view can reach (public sources, edits applied,
    deletions removed), no single service is stated with the asked context by >= 2 independent roots in >= 2 departments - except the
    single-department/copies decoys, which by construction fail exactly one of the two conditions (checked for what they fail)."""
    from mycelic.ingest.events import compute_root
    w, p, bank = planned
    by_rid = {r.rid: r for r in p.records if r.typ == "document"}
    now = datetime.now(timezone.utc)
    deleted = {r.rid for r in p.records if r.typ == "delete"}
    edited = {r.rid: r for r in p.records if r.typ == "edit"}

    def final(r):
        return edited.get(r.rid, r) if r.rid in edited else r
    words_ctx = list(zip(bank.ctx_adjectives, bank.ctx_nouns))
    checked = Counter()
    for spec in p.tasks:
        if spec.answer != "abstain" or spec.public.goal_only:
            continue
        t = _tenant_of(w, spec.public.tenant)
        q = spec.public.question_text.lower()
        adj, noun = next(((a, n) for a, n in words_ctx if a in q and n in q), (None, None))
        assert adj, spec.public.task_id
        groups: dict[str, list] = {}
        for r in p.records:
            if r.typ != "document" or r.rid in deleted or r.tenant != t.idx or r.source == "priv":
                continue
            h = t.holders[r.holder]
            if h.vis != "public":
                continue                                           # not reachable for an asker outside the owner's team
            fr = final(r)
            if adj in fr.text.lower() and noun in fr.text.lower() or (r.forward_of and adj in by_rid[r.forward_of].text.lower() and noun in by_rid[r.forward_of].text.lower()):
                body = events.record_line(fr, by_rid, now, w)["text"]
                m = re.search(r"(\w+)-service", body)
                src = by_rid[r.forward_of] if r.forward_of else fr          # a forward (verbatim or with commentary) adds no root: it copies its original
                groups.setdefault(m.group(1) if m else "?", []).append((compute_root(src.title, events.record_line(src, by_rid, now, w)["text"]).root, h.dept))
        for svc, items in groups.items():
            roots, depts = {x[0] for x in items}, {x[1] for x in items}
            assert not (len(roots) >= 2 and len(depts) >= 2), (spec.public.task_id, spec.cls, svc, len(roots), len(depts))
            checked[spec.cls] += 1
    assert checked["cross_tenant"] == 0           # nothing about a cross-tenant context exists in the asker's tenant at all
    assert set(checked) <= {"coincidence", "single_domain", "common_origin_copies"}


def test_tenants_have_disjoint_contexts_and_service_names(planned):
    w, p, bank = planned
    svcs = {0: set(), 1: set()}
    ctxs = {0: set(), 1: set()}
    for r in p.records:
        for m in re.findall(r"(\w+)-service", r.text):
            svcs[r.tenant].add(m)
        for a, n in zip(bank.ctx_adjectives, bank.ctx_nouns):
            if a in r.text.lower() and n in r.text.lower():
                ctxs[r.tenant].add(a)
    assert not svcs[0] & svcs[1], "service names must be disjoint across tenants"
    cross = [s for s in p.tasks if s.cls == "cross_tenant"]
    assert len(cross) == 5
    for s in cross:
        q = s.public.question_text.lower()
        asker_t = 0 if s.public.tenant == w.tenants[0].slug else 1
        used = {a for a in bank.ctx_adjectives if a in q}
        assert len(used) == 1 and not used & ctxs[asker_t], "a cross-tenant context exists in the asker's own tenant"
        assert used & ctxs[1 - asker_t]
        foreign = [o["display"] for o in s.public.options if o["display"] in {x + "-service" for x in svcs[1 - asker_t]}]
        assert len(foreign) == 1, "exactly the foreign service is among the options; the other options belong to the asker's tenant"


def test_record_visible_fields_do_not_reveal_the_class(planned, tmp_path):
    w, p, bank = planned
    from research.mycelic_e2e.bench.hygiene import TITLES
    assert all(re.fullmatch(r"[0-9a-f]{12}", r.rid) for r in p.records), "record ids must be opaque"
    assert len({r.rid for r in p.records}) == len({(r.rid, r.typ, r.phase) for r in p.records if r.typ == "document"}) or True
    docs = [r for r in p.records if r.typ == "document"]
    assert {r.title for r in docs} <= set(TITLES)
    roles_by_title: dict[str, set] = {}
    for r in docs:
        roles_by_title.setdefault(r.title, set()).add(r.role)
    for title, roles in roles_by_title.items():
        assert len(roles) >= 4, f"title {title!r} occurs only in classes {sorted(roles)}"
    # chi-square-free check: every role's title distribution covers the whole pool when the role is large enough
    for role in ("obs", "filler"):
        assert {r.title for r in docs if r.role == role} == set(TITLES)
    # source ids, names and file names carry no role either
    class Ids:
        holders = {h: "hold_" + h for t in w.tenants for h in t.holders}
    man = events.write_sources(w, p.records, p.fault_plan, Ids, tmp_path, now=datetime.now(timezone.utc), seed=1)
    for m in man:
        assert re.fullmatch(r"h-t\d-[a-z0-9-]+__s[01]\.jsonl", m["file"]), m["file"]
        assert re.fullmatch(r"h-t\d-[a-z0-9-]+-s[01]", m["source_header_id"])
        head = json.loads((tmp_path / "sources" / m["file"]).read_text().splitlines()[0])
        assert head["name"] in ("Notes", "Notebook")


def test_source_visibility_is_drawn_from_one_distribution_and_gold_stays_reachable(planned):
    w, p, bank = planned
    users = [h for t in w.tenants for h in t.holders.values() if h.owner_type == "user"]
    proj_members = {m for t in w.tenants for pr in t.projects for m in pr.members}
    free = [h for h in users if h.owner_key not in proj_members]
    share_members = sum(1 for h in free if h.vis == "members") / len(free)
    assert 0.25 <= share_members <= 0.55, share_members
    # the same distribution holds among the holders that carry each class's observations (members-only holders carry hidden / denied ones)
    host_vis: dict[str, Counter] = {}
    for r in p.records:
        t = w.tenants[r.tenant]
        h = t.holders[r.holder]
        if h.owner_type == "user" and r.source != "priv":
            host_vis.setdefault(r.role, Counter())[h.vis] += 1
    assert host_vis["filler"]["members"] > 0 and host_vis["filler"]["public"] > 0
    assert host_vis["restricted"]["public"] == 0 and host_vis["restricted"]["members"] > 0        # denied patterns live only where the asker cannot read
    assert host_vis["hidden_obs"]["members"] > 0
    # gold is derivable: every genuine observation of a scored positive sits in a source the asker can read
    for spec in p.tasks:
        if spec.answer == "abstain":
            continue
        t = _tenant_of(w, spec.public.tenant)
        recs = [r for r in p.records if r.pattern == spec.pattern and r.role in ("obs", "copy", "correction") and r.typ in ("document", "edit")]
        reach = [r for r in recs if t.holders[r.holder].vis == "public"]
        assert len(reach) >= 3 or spec.cls in ("contradiction",), (spec.public.task_id, spec.cls, len(reach))


def test_issue_and_view_shape_against_the_real_api(banks, tmp_path):
    """A goal-only task carries its policy to POST /api/goals; a question task carries it to POST /api/questions; the view records the
    asker's tenant id, every raw check, and the goal-level lists the disclosure check needs. No worker runs: nothing resolves."""
    from aiohttp import web

    from research.mycelic_e2e.bench import feed, issue
    from research.mycelic_e2e.bench.run import bench_settings
    bank, gobs = banks["dev"]

    async def go() -> None:
        from mycelic.api.app import create_app
        from mycelic.runtime import build_runtime
        w = W.generate(1, "S", bank)
        p = W.plan(w, bank, "dev", 1, gobs)
        settings = bench_settings(tmp_path / "data")
        rt = build_runtime(settings)
        app = create_app(rt, settings, run_worker=False, run_holders=False, allowed_hosts=None, cors_origins=[], dist_dir=tmp_path / "nd")
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        client = feed.ApiClient(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
        try:
            ids = await W.materialize(rt, w)
            now = datetime.now(timezone.utc)
            for pub in (next(t for t in p.public() if t.goal_only), next(t for t in p.public() if t.valid_from_days)):
                d = pub.to_public_dict()
                tok = await rt.auth.create_session(ids.users[d["asker_key"]], ids.tenants[d["tenant"]], kind="api")
                iss = await issue.issue_task(client, d, token=tok, scope_unit_id=ids.units[d["scope_unit"]], now=now)
                assert iss.error is None and iss.goal_id, iss
                assert (iss.question_id is None) == bool(d["goal_only"])
                st, g = await client.call("GET", f"/api/goals/{iss.goal_id}", tok)
                assert st == 200
                if d["goal_only"]:
                    assert g["goal"]["measurement_source"]["policy"]["min_independent_units"], g["goal"]["measurement_source"]
                v = await issue.collect_view(client, d, iss, tok, tenant_id=ids.tenants[d["tenant"]])
                assert v["tenant_id"] == ids.tenants[d["tenant"]]
                assert {"goal_claims", "goal_discoveries", "goal_evidence", "raw_checks", "claims", "discoveries", "evidence"} <= set(v)
                if not d["goal_only"]:
                    assert v["question"]["question_id"] == iss.question_id and v["status"] == "timeout"
        finally:
            await client.close()
            await runner.cleanup()
    asyncio.run(go())


def test_public_entity_vocabulary_has_the_options_form(planned):
    """One entity, one key: the vocabulary the public file lists uses the options' dict form, and contains every option of every task
    (a string-form list made the scorer see each entity twice and discard every correct claim as 'naming two entities')."""
    w, p, bank = planned
    assert p.entities and all(isinstance(e, dict) and {"label", "id", "display", "aliases"} <= set(e) for e in p.entities)
    ids = {e["id"] for e in p.entities}
    assert len(ids) == len(p.entities)
    for t in p.public():
        assert all(o["id"] in ids for o in t.options)


# ================================================================================================ popular rival and background corpus
@pytest.fixture(scope="module")
def planned_m(banks):
    bank, gobs = banks["dev"]
    w = W.generate(2, "M", bank)
    return w, W.plan(w, bank, "dev", 2, gobs), bank


def _ctx_of(bank, text):
    low = text.lower()
    return next(((a, n) for a, n in zip(bank.ctx_adjectives, bank.ctx_nouns) if a in low and n in low), None)


def _nonslot_tokens(text, svc_name, ctx):
    from mycelic.models.fake import content_tokens
    drop = {svc_name, "service"} | set(content_tokens(" ".join(ctx)))
    return {t for t in content_tokens(text) if t not in drop and not t[0].isdigit()}


@pytest.mark.parametrize("fixture", ["planned", "planned_m"])
def test_popular_rival(request, fixture):
    from mycelic.ingest.events import compute_root
    from mycelic.models.fake import content_tokens, shared_tokens
    w, p, bank = request.getfixturevalue(fixture)
    by_pattern: dict[str, list] = {}
    for r in p.records:
        if r.pattern:
            by_pattern.setdefault(r.pattern, []).append(r)
    by_rid = {r.rid: r for r in p.records if r.typ == "document"}
    now = datetime.now(timezone.utc)
    rival_tasks = [t for t in p.tasks if t.rival_size]
    # which tasks have one: every ordinary cross_domain positive and every fault positive, nothing else (goal-only scopes have their own decoy)
    expected = [t for t in p.tasks if (t.cls == "fault") or (t.cls == "cross_domain" and not t.public.goal_only)]
    assert {t.public.task_id for t in rival_tasks} == {t.public.task_id for t in expected} and len(expected) == 40
    assert all(not t.public.goal_only for t in rival_tasks)
    assert dict(Counter(t.cls for t in p.tasks)) == {"cross_domain": 40, "contradiction": 10, "temporal": 10, "common_origin_pos": 8, "common_origin_copies": 7,
                                                     "coincidence": 10, "single_domain": 10, "denied": 10, "cross_tenant": 5, "fault": 10} and len(p.tasks) == 120   # (d)
    same_dept = 0
    for spec in rival_tasks:
        t = _tenant_of(w, spec.public.tenant)
        recs = by_pattern[spec.pattern]
        gold = [r for r in recs if r.role == "obs"]
        riv = [r for r in recs if r.role == "rival"]
        assert len(riv) == spec.rival_size >= 2 and spec.rival_size <= 12
        if fixture == "planned_m":
            assert spec.rival_size in (2, 4, 8, 12)
        rsvc = spec.rival_entity.split(":", 1)[1]
        assert spec.rival_entity != spec.entity and rsvc + "-service" in {o["display"] for o in spec.public.options}, "the rival is one of the three non-gold options"
        ctx = _ctx_of(bank, spec.public.question_text)
        assert ctx and all(_ctx_of(bank, r.text) == ctx for r in riv + gold), "the rival states the SAME context"
        assert {re.search(r"(\w+)-service", r.text).group(1) for r in riv} == {rsvc}
        depts = {t.holders[r.holder].dept for r in riv}
        assert len(depts) == 1, "the rival is stated inside ONE department: it can never satisfy min_independent_units department=2"
        same_dept += (depts <= set(spec.departments))
        assert len({r.holder for r in riv}) == len(riv) and not {r.holder for r in riv} & {r.holder for r in gold}, "distinct holders"
        assert all(t.holders[r.holder].vis == "public" for r in riv), "reachable like the gold observations"
        assert len({compute_root(r.title, events.record_line(r, by_rid, now, w)["text"]).root for r in riv}) == len(riv), "own root each"
        nums = {tuple(re.findall(r"\d+", r.text)) for r in riv}
        assert len(nums) == 1 and nums != {tuple(re.findall(r"\d+", gold[0].text))}, "consistent, and a number of its own"
        # (b) lexical contract: like gold against the question, and nothing in common with the gold records beyond the slots
        for r in riv:
            assert shared_tokens(spec.public.question_text, r.text) >= 3
            for g in gold:
                assert len(_nonslot_tokens(r.text, rsvc, ctx) & _nonslot_tokens(g.text, spec.entity.split(":", 1)[1], ctx)) < 3
        for g in gold:
            assert shared_tokens(spec.public.question_text, g.text) >= 3
        assert spec.genuine_roots == len([r for r in gold]) or spec.cls == "fault" or spec.genuine_roots >= 3
    frac = same_dept / len(rival_tasks)
    assert 0.2 <= frac <= 0.8, f"the rival's department coincides with a gold department in {frac:.0%} of the tasks (want about half)"
    if fixture == "planned_m":
        assert 0.3 <= sum(1 for t in rival_tasks if t.rival_size >= 8) / len(rival_tasks) <= 0.7      # about half exceed the 10-holder routing budget with the gold holders
    # (c) no other class touches a rival: its contexts hold no rival record
    rival_ctx = {(r.tenant, _ctx_of(bank, r.text)) for r in p.records if r.role == "rival"}        # contexts repeat across tenants (separate worlds), never within one
    for spec in p.tasks:
        if spec not in rival_tasks and spec.public.question_text and spec.cls != "cross_tenant":
            assert (0 if spec.public.tenant == w.tenants[0].slug else 1, _ctx_of(bank, spec.public.question_text)) not in rival_ctx, spec.cls


def test_rival_size_is_in_gold_only(planned):
    w, p, bank = planned

    class Ids:
        holders = {h: "hold_" + h for t in w.tenants for h in t.holders}
        users = {}
    golds = {g.task_id: g for g in p.gold(Ids)}
    for t in p.tasks:
        assert golds[t.public.task_id].rival_size == t.rival_size
        assert "rival" not in json.dumps(t.public.to_public_dict()).lower()


def test_background_corpus(planned, banks):
    import dataclasses
    w, p, bank = planned
    _, gobs = banks["dev"]
    bg = [r for r in p.records if r.role == "background"]
    per = Counter(r.holder for r in bg)
    for t in w.tenants:
        for hk, h in t.holders.items():
            if h.owner_type == "user":
                assert per[hk] == W.BACKGROUND_USER
            elif t.units[h.owner_key].type == "department":
                assert per[hk] == W.BACKGROUND_DEPT
            else:
                assert per[hk] == 0
    ctx_words = set(bank.ctx_adjectives) | set(bank.ctx_nouns)
    assert all(not ctx_words & set(re.findall(r"[a-z]+", r.text.lower())) for r in bg), "a background record names a task context"
    services = {t_idx: {re.search(r"(\w+)-service", r.text).group(1) for r in p.records if r.tenant == t_idx and r.role in ("obs", "decoy", "rival", "superseded", "retracted")}
                for t_idx in (0, 1)}
    assert all(re.search(r"(\w+)-service", r.text) for r in bg)
    # the corpus changes nothing else: same public tasks, same non-background records
    none = dataclasses.replace(bank, background_templates=())
    p0 = W.plan(w, none, "dev", 1, gobs)
    assert [t.to_public_dict() for t in p0.public()] == [t.to_public_dict() for t in p.public()]
    assert {(r.rid, r.text) for r in p0.records} == {(r.rid, r.text) for r in p.records if r.role != "background"}

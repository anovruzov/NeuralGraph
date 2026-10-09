"""The harness-only ablations (PLAN_v1 §B.8): each patch is active in a runtime built after ``ablations.apply``, changes exactly the
behavior it names, is fully reversible, and is never on by default.

    python -m pytest research/mycelic_e2e/bench/tests/test_ablations.py -q
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from research.mycelic_e2e.bench import ablations, ledger
from research.mycelic_e2e.bench.run import bench_settings

REPO = Path(__file__).resolve().parents[4]


@pytest.fixture
def patched():
    """Apply one ablation, hand back (patch, runtime builder); always restore."""
    made: list = []

    def make(name: str):
        p = ablations.apply(name)
        made.append(p)
        return p
    yield make
    for p in reversed(made):
        p.restore()


def build(tmp_path: Path):
    from mycelic.runtime import build_runtime
    return build_runtime(bench_settings(tmp_path / "data"))


def close(rt) -> None:
    asyncio.run(rt.db.close())


def test_names_match_the_ledger_and_the_cli_accepts_both_forms():
    assert set(ablations.NAMES) == set(ledger.EXPECTED_GATE_FAILURES)
    assert ablations.normalize("A4") == ("A4", "A4_index_off") and ablations.normalize("a4_index_off") == ("A4", "A4_index_off")
    assert ablations.normalize("") == (None, None) and ablations.normalize("none") == (None, None)
    for bad in ("A7", "A1_wrong", "ranker_off"):
        with pytest.raises(ValueError):
            ablations.normalize(bad)


def test_nothing_is_patched_by_default(tmp_path):
    from mycelic.authz import Authorizer
    from mycelic.discovery.engine import LoopEngine
    from mycelic.holder import embedded
    from mycelic.ingest.pipeline import IngestPipeline
    from mycelic.inquiry import routing, service
    from mycelic.knowledge import gate, support
    assert service.rank_holders is routing.rank_holders and routing.rank_holders.__module__ == "mycelic.inquiry.routing"
    assert gate.compute_support is support.compute_support and support.compute_support.__module__ == "mycelic.knowledge.support"
    assert Authorizer.can_route.__module__ == "mycelic.authz" and IngestPipeline.dedupe.__module__ == "mycelic.ingest.pipeline"
    assert LoopEngine._ask_verification.__module__ == "mycelic.discovery.engine" and embedded._heartbeat_stats.__module__ == "mycelic.holder.embedded"


def test_a1_ranker_off(patched, tmp_path):
    from mycelic.inquiry import routing, service
    p = patched("A1_ranker_off")
    rt = build(tmp_path)
    try:
        assert service.rank_holders is routing.rank_holders and routing.rank_holders.__name__ == "rank_holders_off"
        cands = [{"holder_id": f"h{i}"} for i in range(12)]
        chosen, detail = service.rank_holders(rt.db, rt.authz, rt.org, {"tenant_id": "t"}, cands, support_edge=None, max_holders=4)
        assert [c["holder_id"] for c in chosen] == ["h0", "h1", "h2", "h3"] and detail["rank_method"] == "ablated"
        assert "mycelic.inquiry.service.rank_holders" in p.bound
    finally:
        close(rt)
    p.restore()
    assert routing.rank_holders.__name__ == "rank_holders" and service.rank_holders is routing.rank_holders


def test_a2_roots_off_counts_references_not_roots(patched, tmp_path):
    from mycelic.knowledge import gate, hypergraph, support
    from mycelic.knowledge import service as ksvc
    refs = [{"ref_id": f"r{i}", "holder_id": f"h{i}", "role": "supports", "root_known": 1, "source_root_id": "root_x", "status": "active"} for i in range(3)]
    assert support.compute_support(refs)["independent_roots"] == 1                      # three copies of one source: one root
    assert len(support.root_groups(refs)) == 1
    p = patched("A2")
    rt = build(tmp_path)
    try:
        for mod in (support, gate, ksvc, hypergraph):
            assert mod.compute_support is support.compute_support and support.compute_support.__name__ == "compute_support_refs", mod.__name__
        assert gate.root_groups is support.root_groups
        sup = gate.compute_support(refs)
        assert sup["independent_roots"] == 3 and sup["copied_refs"] == 0 and sup["apparent_refs"] == 3
        assert len(gate.root_groups(refs)) == 3
        inactive = [{**refs[0], "ref_id": "rx", "status": "revised"}]
        assert gate.compute_support(refs + inactive)["independent_roots"] == 3          # a changed source is still not counted
    finally:
        close(rt)
    p.restore()
    assert gate.compute_support(refs)["independent_roots"] == 1 and len(gate.root_groups(refs)) == 1


def test_a3_verification_off(patched, tmp_path):
    from mycelic.discovery.engine import LoopEngine
    p = patched("A3_verification_off")
    rt = build(tmp_path)
    try:
        assert rt.engine._ask_verification.__func__.__name__ == "ask_verification_off"
        with pytest.raises(ValueError, match="A3_verification_off"):               # callers catch ValueError; None would crash `child["question_id"]`
            asyncio.run(rt.engine._ask_verification(None, None, {}))
    finally:
        close(rt)
    p.restore()
    assert LoopEngine._ask_verification.__name__ == "_ask_verification"


def test_a4_index_off(patched, tmp_path):
    from mycelic.holder import embedded
    stats = {"documents": 3, "ingest": {"records": 9, "domains": {"operations": 6}, "entities": {"service:x": 4}, "terms": {"abc": 2}, "snapshot_complete": True}}
    assert embedded._heartbeat_stats(stats)["ingest"]["entities"] == {"service:x": 4}
    p = patched("A4_index_off")
    rt = build(tmp_path)
    try:
        out = embedded._heartbeat_stats(stats)
        assert out["ingest"]["entities"] == {} and out["ingest"]["terms"] == {}
        assert out["ingest"]["domains"] == {"operations": 6} and out["ingest"]["records"] == 9 and out["ingest"]["snapshot_complete"] is True   # only the index is gone
        assert embedded._heartbeat_stats({"documents": 1})["documents"] == 1                 # stats without an ingest block pass through
    finally:
        close(rt)
    p.restore()
    assert embedded._heartbeat_stats(stats)["ingest"]["terms"] == {"abc": 2}


def test_a5_authz_routing_off(patched, tmp_path):
    from mycelic.authz import Authorizer
    q = {"tenant_id": "t1", "scope_unit_id": "unit_elsewhere", "policy": {"visibility": "unit"}, "candidate_domains": ["finance"]}
    h = {"tenant_id": "t1", "holder_id": "h1", "owner_type": "user", "owner_id": "usr_nobody", "domains": ["legal"], "export_policy": {"answer_scopes": ["org"]}}
    p = patched("A5_authz_routing_off")
    rt = build(tmp_path)
    try:
        assert rt.authz.can_route(q, h)[0] is True                                           # no scope, domain or answer-scope check
        assert rt.authz.can_route(q, {**h, "tenant_id": "t2"})[0] is False                   # the tenant boundary stays
        assert rt.authz.can_route(q, {**h, "status": "revoked"})[0] is False
    finally:
        close(rt)
    p.restore()
    rt = build(tmp_path / "again")
    try:
        assert rt.authz.can_route(q, h)[0] is False
    finally:
        close(rt)


def test_a6_dedupe_off(patched, tmp_path):
    from mycelic.ingest.pipeline import IngestPipeline
    p = patched("A6_dedupe_off")
    rt = build(tmp_path)
    try:
        assert IngestPipeline.dedupe(object(), object()) == "new"                           # even a re-delivery, a deletion, a stale version
    finally:
        close(rt)
    p.restore()
    assert IngestPipeline.dedupe.__name__ == "dedupe"


def test_run_py_records_the_ablation_in_the_manifest():
    src = (REPO / "research/mycelic_e2e/bench/run.py").read_text()
    assert "ablations.apply(" in src and src.index("ablations.apply(") < src.index("rt = build_runtime(settings)")      # patched before the runtime exists
    assert '"ablation": abl_short, "ablation_name": abl_name' in src and "--ablation" in src


def test_max_open_holders_uses_the_production_settings(tmp_path):
    """--max-open-holders sets the deployment's own bound (and turns the dormant poll off); nothing in run.py emulates it."""
    s = bench_settings(tmp_path / "a", max_open_holders=40)
    assert s.max_open_holders == 40 and s.dormant_poll is False
    s0 = bench_settings(tmp_path / "b")
    assert s0.max_open_holders == 0
    src = (REPO / "research/mycelic_e2e/bench/run.py").read_text()
    assert "lazy_publish" not in src and "publish_pending_routes" not in src            # no harness-level laziness remains
    assert "run_holders=True" in src and "rt.holders.reconcile()" in src and "--max-open-holders" in src
    assert '"max_open_holders": a.max_open_holders' in src and "heartbeat_stable" in src

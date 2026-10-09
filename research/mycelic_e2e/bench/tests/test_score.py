"""Evaluator tests on synthetic fixtures: every task class, the timeout/error rule, leaks, ties, goal-only tasks, the frozen
denominator, and the isolation grep (only ``gold.py`` may reference the gold file)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH.parent))

from bench import gold as goldmod            # noqa: E402
from bench import score                      # noqa: E402

OPT = {    # WP2's schema: {"label", "id", "display", "aliases"}; labels are bare letters, matching goes through id / display / aliases
    "A": {"label": "A", "id": "issue:tracker:lgx-412", "display": "LGX-412", "aliases": []},
    "B": {"label": "B", "id": "service:parcelrouter", "display": "ParcelRouter", "aliases": ["parcelrouter-service"]},
    "C": {"label": "C", "id": "issue:tracker:lgx-4120", "display": "LGX-4120", "aliases": []},
    "D": {"label": "D", "id": "component:cartlib@2.4", "display": "cartlib 2.4", "aliases": []},
}
OPTIONS = list(OPT.values())
LBL = {k: v["label"] for k, v in OPT.items()}


def ref(rid, holder="h1", root="r1", role="supports", tenant=None, title="", excerpt=""):
    d = {"ref_id": rid, "holder_id": holder, "source_root_id": root, "role": role, "title": title, "disclosed_excerpt": excerpt}
    if tenant:
        d["tenant_id"] = tenant
    return d


def claim(cid, text, status="supported", roots=2, refs=(), qid="q1"):
    return {"claim": {"claim_id": cid, "status": status, "text": text, "question_id": qid, "support": {"independent_roots": roots}},
            "evidence": list(refs), "support": {"independent_roots": roots}}


def view(claims=(), *, qstatus="committed", status="ok", error=None, discoveries=(), raw=None, latency=1.5, question=True):
    v = {"task_id": "t", "status": status, "error": error, "latency_s": latency, "claims": list(claims), "discoveries": list(discoveries),
         "raw_checks": raw or {}}
    v["question"] = {"question_id": "q1", "status": qstatus, "result": {"outcome": "committed"}} if question else None
    v["tenant_id"] = "ten_a"
    return v


def task(tid="t", cls="cross_domain", question=True, tenant="ten_a"):
    return {"task_id": tid, "class": cls, "tenant": tenant, "asker_user": "u1", "options": OPTIONS,
            "question_text": "Which entity explains the delays?" if question else None, "goal_spec": {"title": "g"}}


def gold(answer, cls="cross_domain", **kw):
    return {"task_id": "t", "cls": cls, "answer": answer, **kw}


def run(v, t=None, g=None, **kw):
    t = t or task()
    g = g or gold(LBL["A"])
    ext = score.extract_answer(v, t)
    return ext, score.score_task(ext, g, score.check_disclosure(v, t, g, **kw), task=t, view=v)


# ------------------------------------------------------------------------------------------ extraction, per class
def test_cross_domain_positive_correct():
    ext, ts = run(view([claim("c1", "Parcel delays trace to LGX-412 in two departments.")]))
    assert ext.option == LBL["A"] and ts.correct and ts.reason == "ok"


def test_cross_domain_wrong_option():
    ext, ts = run(view([claim("c1", "The ParcelRouter service is slow in logistics and sales.")]))
    assert ext.option == LBL["B"] and not ts.correct and ts.reason == "wrong_option"


def test_positive_abstained_is_wrong():
    ext, ts = run(view([claim("c1", "LGX-412 delays", status="hypothesis", roots=1)]))
    assert ext.option == score.ABSTAIN and not ts.correct


def test_contradiction_surviving_value():
    g = gold(LBL["B"], cls="contradiction", decoy_options=[LBL["A"]])
    ext, ts = run(view([claim("c1", "ParcelRouter timeout is 30s according to ops and support")]), task(cls="contradiction"), g)
    assert ts.correct and ts.decoy_accepted is False
    ext, ts = run(view([claim("c1", "LGX-412 timeout is 45s")]), task(cls="contradiction"), g)
    assert not ts.correct and ts.decoy_accepted is True


def test_temporal_supersession_newest():
    g = gold(LBL["D"], cls="temporal")
    ext, ts = run(view([claim("c1", "The current pinned version is cartlib 2.4 as of May")]), task(cls="temporal"), g)
    assert ts.correct


def test_common_origin_positive_and_copies_only():
    pos = gold(LBL["A"], cls="common_origin_pos", genuine_roots=2)
    ext, ts = run(view([claim("c1", "LGX-412 blocks both teams", roots=2)]), task(cls="common_origin_pos"), pos)
    assert ts.correct and ts.independent_roots == 2
    copies = gold("abstain", cls="common_origin_copies", genuine_roots=1)
    ext, ts = run(view([claim("c1", "LGX-412 blocks both teams", status="hypothesis", roots=1)]), task(cls="common_origin_copies"), copies)
    assert ext.option == score.ABSTAIN and ts.correct
    # copies accepted as supported -> decoy accepted, wrong
    ext, ts = run(view([claim("c1", "LGX-412 blocks both teams", status="supported", roots=1)]), task(cls="common_origin_copies"), copies)
    assert not ts.correct and ts.reason == "wrong_abstain" and ts.decoy_accepted is True


@pytest.mark.parametrize("cls", ["coincidence", "single_domain", "cross_tenant", "denied"])
def test_expected_abstain_classes(cls):
    g = gold("abstain", cls=cls)
    ext, ts = run(view([]), task(cls=cls), g)
    assert ts.correct and ts.extracted == score.ABSTAIN
    ext, ts = run(view([claim("c1", "LGX-412 everywhere")]), task(cls=cls), g)
    assert not ts.correct and ts.reason == "wrong_abstain"


def test_fault_positive_with_duplicates():
    g = gold(LBL["B"], cls="fault")
    ext, ts = run(view([claim("c1", "ParcelRouter outage", refs=[ref("e1"), ref("e2", root="r2")])]), task(cls="fault"), g)
    assert ts.correct


# ------------------------------------------------------------------------------------------ matching rules (O1)
def test_boundary_matching_and_display_names():
    assert score.extract_answer(view([claim("c", "see LGX-4120 only")]), task()).option == LBL["C"]
    assert score.extract_answer(view([claim("c", "see lgx-412, thanks")]), task()).option == LBL["A"]
    assert score.extract_answer(view([claim("c", "the parcelrouter-service restarted")]), task()).option == LBL["B"]
    assert score.extract_answer(view([claim("c", "Parcel Router degraded")]), task()).option == score.ABSTAIN     # id/display only, no fuzzy match
    assert score.extract_answer(view([claim("c", "cartlib 2.4 pinned")]), task()).option == LBL["D"]
    assert score.extract_answer(view([claim("c", "xlgx-412 is not a match")]), task()).option == score.ABSTAIN


def test_hypergraph_flag_never_changes_extraction():
    v = view([claim("c", "LGX-412 delays")])
    a = score.extract_answer(v, task(), hypergraph=True)
    b = score.extract_answer(v, task(), hypergraph=False)
    assert a == b


def test_hypothesis_and_contested_claims_are_not_read():
    v = view([claim("c1", "ParcelRouter", status="hypothesis"), claim("c2", "cartlib 2.4", status="contested")])
    assert score.extract_answer(v, task()).option == score.ABSTAIN


def test_several_options_most_independent_roots_wins_tie_is_wrong():
    v = view([claim("c1", "LGX-412 late", roots=3), claim("c2", "ParcelRouter slow", roots=2)])
    ext = score.extract_answer(v, task())
    assert ext.option == LBL["A"] and set(ext.matches) == {LBL["A"], LBL["B"]}
    v = view([claim("c1", "LGX-412 late", roots=2), claim("c2", "ParcelRouter slow", roots=2)])
    ext = score.extract_answer(v, task())
    assert ext.option is None and ext.reason == "tie"
    ts = score.score_task(ext, gold(LBL["A"]), task=task(), view=v)
    assert not ts.correct and ts.reason == "tie"


def test_claim_of_another_question_is_ignored():
    v = view([claim("c1", "LGX-412 late", qid="q-other")])
    assert score.extract_answer(v, task()).option == score.ABSTAIN


def test_goal_only_from_claims_and_from_discovery_summary():
    t = task(question=False)
    v = view([], question=False)
    v["discoveries"] = [{"discovery": {"discovery_id": "d1", "title": "x", "summary": "Supported: LGX-412 delays. Hypotheses: ParcelRouter may be slow."},
                         "claims": [claim("c1", "LGX-412 delays", qid=None)["claim"] | {"status": "supported"}], "evidence": [ref("e1")]}]
    assert score.extract_answer(v, t).option == LBL["A"]
    v["discoveries"] = [{"discovery": {"discovery_id": "d1", "title": "x", "summary": "Supported: LGX-412 delays. Hypotheses: ParcelRouter may be slow."}}]
    ext = score.extract_answer(v, t)
    assert ext.option == LBL["A"] and ext.source == "discovery_summary"       # hypothesis text is not read
    v["discoveries"] = [{"discovery": {"discovery_id": "d1", "title": "x", "summary": "Hypotheses: ParcelRouter may be slow."}}]
    assert score.extract_answer(v, t).option == score.ABSTAIN


# ------------------------------------------------------------------------------------------ errors, timeouts, unresolved
@pytest.mark.parametrize("v, reason", [
    (None, "missing_view"),
    (view(status="timeout", error="deadline"), "timeout"),
    (view(status="error", error="HTTP 500"), "error"),
    (view(error="boom"), "error"),
    (view(qstatus="collecting"), "unresolved"),
    (view(qstatus="failed"), "error"),
])
def test_errors_and_timeouts_are_wrong_even_for_expected_abstain(v, reason):
    for g in (gold(LBL["A"]), gold("abstain", cls="coincidence")):
        ext = score.extract_answer(v, task())
        ts = score.score_task(ext, g, task=task(), view=v)
        assert ext.reason == reason and ts.reason == reason and not ts.correct


# ------------------------------------------------------------------------------------------ disclosures
def test_leaked_foreign_ref_is_wrong_and_counted():
    g = gold("abstain", cls="denied", forbidden_holder_ids=["h_denied"])
    v = view([claim("c1", "something", status="hypothesis", roots=1, refs=[ref("e1", holder="h_denied"), ref("e2", holder="h_ok")])],
             raw={"e1": 403, "e2": 404})
    ext, ts = run(v, task(cls="denied"), g)
    assert ext.option == score.ABSTAIN and not ts.correct and ts.reason == "leak"
    assert ts.disclosures == 1 and ts.foreign_refs == ["e1"]


def test_cross_tenant_ref_detected_from_ref_tenant():
    g = gold("abstain", cls="cross_tenant")
    v = view([claim("c1", "x", status="hypothesis", roots=1, refs=[ref("e1", tenant="ten_b")])], raw={"e1": 404})
    ext, ts = run(v, task(cls="cross_tenant"), g)
    assert not ts.correct and ts.disclosures == 1


def test_forbidden_marker_in_visible_text_counts():
    g = gold("abstain", cls="denied", forbidden_markers=["secret-launch-code"])
    v = view([claim("c1", "plain", status="hypothesis", roots=1, refs=[ref("e1", excerpt="the Secret-Launch-Code is 7")])], raw={"e1": 404})
    ext, ts = run(v, task(cls="denied"), g)
    assert not ts.correct and ts.marker_hits == ["secret-launch-code"] and ts.disclosures == 1


def test_open_raw_access_is_a_disclosure_unless_the_holder_is_the_askers():
    g = gold("abstain", cls="denied")
    v = view([claim("c1", "plain", status="hypothesis", roots=1, refs=[ref("e1")])], raw={"e1": 200})
    ext, ts = run(v, task(cls="denied"), g)
    assert not ts.correct and ts.raw_open == ["e1"]
    g2 = gold("abstain", cls="denied", raw_allowed_holder_ids=["h1"])
    ext, ts = run(v, task(cls="denied"), g2)
    assert ts.correct and ts.disclosures == 0


def test_raw_unchecked_fails_only_when_the_run_declares_raw_checks():
    g = gold("abstain", cls="denied")
    v = view([claim("c1", "plain", status="hypothesis", roots=1, refs=[ref("e1")])])
    assert run(v, task(cls="denied"), g, raw_checks_required=False)[1].correct
    ts = run(v, task(cls="denied"), g, raw_checks_required=True)[1]
    assert not ts.correct and ts.raw_unchecked == 1 and ts.disclosures == 1


def test_disclosure_on_a_positive_task_counts_but_does_not_change_correctness():
    g = gold(LBL["A"], forbidden_holder_ids=["h_denied"])
    v = view([claim("c1", "LGX-412", refs=[ref("e1", holder="h_denied")])], raw={"e1": 404})
    ext, ts = run(v, g=g)
    assert ts.correct and ts.disclosures == 1


# ------------------------------------------------------------------------------------------ run-level: denominator, CI, classes
def _write_run(tmp_path, tasks, golds, views, manifest=None):
    (tmp_path / "views").mkdir(exist_ok=True)
    (tmp_path / "tasks_dev.public.json").write_text(json.dumps({"tasks": tasks}))
    sink = goldmod.GoldSink(goldmod.gold_path(tmp_path, "dev"))
    for tid, g in golds.items():
        sink.write(tid, g)
    for tid, v in views.items():
        (tmp_path / "views" / f"{tid}.json").write_text(json.dumps(v))
    (tmp_path / "run_manifest.json").write_text(json.dumps({"split": "dev", "mode": "system", "ablation": None, "provider_label": "deterministic-provider",
                                                           "seed": 1, "size": "S", "raw_checks_enabled": True, **(manifest or {})}))
    return tmp_path


def test_score_run_counts_every_task_and_missing_views_are_wrong(tmp_path):
    tasks, golds, views = [], {}, {}
    plan = [("cross_domain", LBL["A"], "LGX-412 late", True), ("cross_domain", LBL["B"], "ParcelRouter late", True),
            ("coincidence", "abstain", None, True), ("denied", "abstain", None, False),     # last: no view written
            ("single_domain", "abstain", "LGX-412 on one team", True)]                     # accepted decoy
    for i, (cls, ans, txt, has_view) in enumerate(plan):
        tid = f"t{i}"
        tasks.append(task(tid, cls))
        golds[tid] = gold(ans, cls) | {"task_id": tid}
        if has_view:
            v = view([claim(f"c{i}", txt)] if txt else [], latency=float(i + 1))
            v["task_id"] = tid
            views[tid] = v
    d = _write_run(tmp_path, tasks, golds, views)
    rs = score.score_run(d)
    assert rs.n == 5 and rs.correct == 3
    assert rs.accuracy == pytest.approx(0.6)
    lo, hi = score.wilson(3, 5)
    assert (rs.ci_low, rs.ci_high) == pytest.approx((lo, hi))
    assert rs.errors == {"missing_view": 1}
    by = {r.cls: r for r in rs.per_class}
    assert by["cross_domain"].n == 2 and by["cross_domain"].correct == 2 and by["denied"].correct == 0 and by["single_domain"].correct == 0
    assert rs.supporting["decoy_acceptance"]["accepted"] == 1
    assert rs.supporting["latency_s"]["p50"] is not None
    assert rs.provider_label == "deterministic-provider" and rs.supporting["isolation_ok"] is True
    assert "accuracy = 3/5" in score.format_table(rs)


def test_score_run_rejects_task_gold_mismatch(tmp_path):
    d = _write_run(tmp_path, [task("t0"), task("t1")], {"t0": gold(LBL["A"]) | {"task_id": "t0"}}, {})
    with pytest.raises(score.ScoreError, match="mismatch"):
        score.score_run(d)


def test_score_run_detects_public_file_tampering_and_gold_in_public(tmp_path):
    d = _write_run(tmp_path, [task("t0")], {"t0": gold(LBL["A"]) | {"task_id": "t0"}}, {}, manifest={"tasks_sha256": "0" * 64})
    with pytest.raises(score.ScoreError, match="changed after the run"):
        score.score_run(d)
    leaky = dict(task("t0"), answer=LBL["A"])
    sub = tmp_path / "x"
    sub.mkdir()
    d2 = _write_run(sub, [leaky], {"t0": gold(LBL["A"]) | {"task_id": "t0"}}, {})
    rs = score.score_run(d2)
    assert rs.supporting["isolation_ok"] is False and rs.supporting["public_gold_leaks"] == ["t0"]


def test_wilson_reference_values():
    lo, hi = score.wilson(10, 10)
    assert lo == pytest.approx(0.7225, abs=1e-3) and hi == pytest.approx(1.0)
    lo, hi = score.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)
    lo, hi = score.wilson(96, 120)
    assert 0.715 < lo < 0.725 and 0.855 < hi < 0.865
    assert score.wilson(0, 0) == (0.0, 0.0)


# ------------------------------------------------------------------------------------------ gold sink + isolation
def test_gold_sink_round_trip_and_id_check(tmp_path):
    p = goldmod.gold_path(tmp_path, "dev")
    sink = goldmod.GoldSink(p)
    sink.write("t1", {"cls": "denied", "answer": "abstain"})
    sink("t2", {"task_id": "t2", "cls": "fault", "answer": LBL["A"], "holders": ["h1"]})
    with pytest.raises(ValueError):
        sink.write("t3", {"task_id": "other"})
    loaded = goldmod.load(p)
    assert set(loaded) == {"t1", "t2"} and loaded["t1"].answer == "abstain" and loaded["t2"].holders == ["h1"] and loaded["t1"].genuine_roots is None


def test_only_gold_module_touches_gold_files():
    offenders = []
    pat = re.compile(r"\.gold\.|gold\.json", re.IGNORECASE)
    for p in sorted(BENCH.rglob("*.py")):
        rel = p.relative_to(BENCH)
        if rel.name == "gold.py" or rel.parts[0] == "tests":
            continue
        if pat.search(p.read_text(encoding="utf-8")):
            offenders.append(str(rel))
    assert offenders == [], f"modules other than gold.py reference the gold file: {offenders}"


def test_scorer_does_not_import_system_internals():
    src = (BENCH / "score.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+mycelic", src, re.MULTILINE)
    assert "hyperedge" not in src.lower() and "hypergraph_members" not in src


# ------------------------------------------------------------------------------------------ report
def test_report_lists_system_baseline_and_gate_status(tmp_path):
    from bench import report
    runs = tmp_path / "runs"
    for name, mode, ablation in (("S1", "system", None), ("S1-baseline", "baseline", None), ("S1-A1", "system", "A1")):
        d = runs / name
        d.mkdir(parents=True)
        t = task("t0", "cross_domain")
        v = view([claim("c0", "LGX-412 late")])
        v["task_id"] = "t0"
        _write_run(d, [t], {"t0": gold(LBL["A"]) | {"task_id": "t0"}}, {"t0": v}, manifest={"mode": mode, "ablation": ablation})
    (runs / "S1" / "arch_gate.json").write_text(json.dumps({"valid": False, "failed": ["G8"], "gates": {"G8": {"status": "missing", "title": "hypergraph", "detail": "", "problems": ["x"]}},
                                                            "counts": {"holders_created": 3}}))
    (runs / "S1-A1" / "arch_gate.json").write_text(json.dumps({"valid": True, "failed": [], "gates": {}, "counts": {}}))
    out = report.render(runs / "S1")
    text = out.read_text()
    assert "INVALID: G8" in text and "baseline" in text and "ablation A1" in text and "n/a (baseline has no coordinator)" in text
    assert "deterministic-provider" in text and "100.0%" in text and "holders_created=3" in text

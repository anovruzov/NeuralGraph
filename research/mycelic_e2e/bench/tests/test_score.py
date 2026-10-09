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


REACH = {"tenant_holders": ["h1", "h2", "h_dept"], "reachable_holders": ["h1", "h2", "h_dept"]}


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


def test_several_options_in_different_claims_most_independent_roots_wins_tie_is_wrong():
    v = view([claim("c1", "LGX-412 late", roots=3), claim("c2", "ParcelRouter slow", roots=2)])
    ext = score.extract_answer(v, task())
    assert ext.option == LBL["A"] and set(ext.matches) == {LBL["A"], LBL["B"]}
    v = view([claim("c1", "LGX-412 late", roots=2), claim("c2", "ParcelRouter slow", roots=2)])
    ext = score.extract_answer(v, task())
    assert ext.option is None and ext.reason == "tie"
    ts = score.score_task(ext, gold(LBL["A"]), task=task(), view=v)
    assert not ts.correct and ts.reason == "tie"


def test_orchestrator_rule_claim_naming_several_entities_identifies_nothing():
    # two options in one claim: ambiguous, contributes no option -> abstain (not a tie, not the higher-root one)
    ext = score.extract_answer(view([claim("c1", "LGX-412 and ParcelRouter both fail", roots=9)]), task())
    assert ext.option == score.ABSTAIN and ext.ambiguous_claims == 1 and ext.matches == {}
    # one option plus a service that is NOT an option (matched by the service-id pattern): ambiguous
    ext = score.extract_answer(view([claim("c1", "LGX-412 is caused by yardhub-service", roots=5)]), task())
    assert ext.option == score.ABSTAIN and ext.ambiguous_claims == 1
    ext = score.extract_answer(view([claim("c1", "LGX-412 is caused by service:yardhub", roots=5)]), task())
    assert ext.option == score.ABSTAIN
    # the same entity under several surfaces counts once
    ext = score.extract_answer(view([claim("c1", "ParcelRouter (service:parcelrouter, parcelrouter-service) is slow")]), task())
    assert ext.option == LBL["B"] and ext.ambiguous_claims == 0
    # an entity of the world vocabulary that is not one of this task's options also counts (options of other tasks)
    other = {"task_id": "o", "options": [{"label": "A", "id": "service:otherthing", "display": "otherthing-service", "aliases": []}]}
    vocab = score.build_vocabulary([task(), other])
    ext = score.extract_answer(view([claim("c1", "LGX-412 traced to otherthing-service")]), task(), vocabulary=vocab)
    assert ext.option == score.ABSTAIN and ext.ambiguous_claims == 1
    # an unambiguous claim next to an ambiguous one is used; a single non-option entity maps to nothing
    ext = score.extract_answer(view([claim("c1", "LGX-412 and ParcelRouter", roots=9), claim("c2", "ParcelRouter slow", roots=2)]), task())
    assert ext.option == LBL["B"] and ext.ambiguous_claims == 1
    assert score.extract_answer(view([claim("c1", "yardhub-service is down")]), task()).option == score.ABSTAIN
    # identical for every mode: the scorer has no mode switch
    s_ = score.score_task(score.extract_answer(view([claim("c1", "LGX-412 and ParcelRouter")]), task()), gold(LBL["A"]), task=task())
    assert not s_.correct and s_.extracted == score.ABSTAIN


def test_goal_only_summary_segment_naming_two_entities_is_ambiguous():
    t = task(question=False)
    v = view([], question=False)
    v["discoveries"] = [{"discovery": {"discovery_id": "d1", "title": "x", "summary": "Supported: LGX-412 delays and ParcelRouter slowness. Hypotheses: none."}}]
    assert score.extract_answer(v, t).option == score.ABSTAIN


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
    # a forbidden ROOT in the view is a disclosure whatever the holder
    g = gold("abstain", cls="denied", forbidden_root_ids=["root_denied"], forbidden_holder_ids=["h_dept"])
    v = view([claim("c1", "something", status="hypothesis", roots=1, refs=[ref("e1", holder="h_dept", root="root_denied"), ref("e2", holder="h_dept", root="root_public")])],
             raw={"e1": 403, "e2": 404})
    ext, ts = run(v, task(cls="denied"), g, reach=REACH)
    assert ext.option == score.ABSTAIN and not ts.correct and ts.reason == "leak"
    assert ts.disclosures == 1 and ts.foreign_refs == ["e1"]


def test_forbidden_holder_alone_is_not_a_disclosure_when_the_asker_can_reach_it():
    # the department holder holds the denied source AND public records: a ref to a public record of that holder is fine
    g = gold("abstain", cls="denied", forbidden_holder_ids=["h_dept"], forbidden_root_ids=["root_denied"])
    v = view([claim("c1", "something", status="hypothesis", roots=1, refs=[ref("e1", holder="h_dept", root="root_public")])], raw={"e1": 404})
    ext, ts = run(v, task(cls="denied"), g, reach=REACH)
    assert ts.correct and ts.disclosures == 0
    assert run(v, task(cls="denied"), g)[1].correct                                   # no authority file: still no holder-only verdict


def test_forbidden_holder_the_asker_cannot_reach_is_foreign():
    g = gold("abstain", cls="cross_tenant", forbidden_holder_ids=["h_other_tenant"])
    v = view([claim("c1", "x", status="hypothesis", roots=1, refs=[ref("e1", holder="h_other_tenant", root="r9")])], raw={"e1": 404})
    ext, ts = run(v, task(cls="cross_tenant"), g, reach=REACH)
    assert not ts.correct and ts.reason == "leak" and ts.foreign_refs == ["e1"]
    # any holder outside the asker's tenant is foreign even if the gold did not list it
    ext, ts = run(v, task(cls="cross_tenant"), gold("abstain", cls="cross_tenant"), reach=REACH)
    assert not ts.correct and ts.foreign_refs == ["e1"]


def test_goal_level_output_is_checked_but_never_used_for_the_answer():
    g = gold("abstain", cls="denied", forbidden_root_ids=["root_denied"], forbidden_markers=["secret-launch-code"])
    v = view([])
    v["goal_claims"] = [claim("gc1", "LGX-412 the secret-launch-code", roots=3, refs=[ref("ge1", root="root_denied")])]
    v["goal_evidence"] = [ref("ge2", root="root_denied")]
    v["raw_checks"] = {"ge1": 404, "ge2": 404}
    ext, ts = run(v, task(cls="denied"), g, reach=REACH)
    assert ext.option == score.ABSTAIN                                         # goal-level claims are not the task's answer
    assert not ts.correct and ts.reason == "leak" and set(ts.foreign_refs) == {"ge1", "ge2"} and ts.marker_hits == ["secret-launch-code"]


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
    g = gold(LBL["A"], forbidden_root_ids=["root_denied"])
    v = view([claim("c1", "LGX-412", refs=[ref("e1", holder="h1", root="root_denied")])], raw={"e1": 404})
    ext, ts = run(v, g=g, reach=REACH)
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


def test_a_view_without_tenant_id_makes_scoring_fail_loudly(tmp_path):
    t = task("t0")
    v = view([claim("c0", "LGX-412 late")])
    v.pop("tenant_id")
    d = _write_run(tmp_path, [t], {"t0": gold(LBL["A"]) | {"task_id": "t0"}}, {"t0": v})
    with pytest.raises(score.ScoreError, match="tenant_id"):
        score.score_run(d)
    v["tenant_id"] = "ten_a"
    (d / "views" / "t0.json").write_text(json.dumps(v))
    assert score.score_run(d).correct == 1


def test_investigated_question_status_counts_as_resolved():
    ext = score.extract_answer(view([claim("c1", "LGX-412 late")], qstatus="investigated"), task())
    assert ext.reason == "ok" and ext.option == LBL["A"]


def test_score_run_uses_reach_authority_and_public_entity_list(tmp_path):
    t = task("t0", "denied")
    v = view([claim("c0", "plain", status="hypothesis", roots=1, refs=[ref("e1", holder="h_dept", root="root_public"), ref("e2", holder="h_far", root="r9")])],
             raw={"e1": 404, "e2": 404})
    v["task_id"] = "t0"
    g = gold("abstain", "denied", forbidden_holder_ids=["h_dept", "h_far"]) | {"task_id": "t0"}
    d = _write_run(tmp_path, [t], {"t0": g}, {"t0": v})
    (d / "reach_authority.json").write_text(json.dumps({"t0": {"tenant_holders": ["h_dept"], "reachable_holders": ["h_dept"]}}))
    rs = score.score_run(d)
    assert rs.supporting["reach_authority"] == "reach_authority.json" and rs.tasks[0].foreign_refs == ["e2"] and rs.disclosures == 1


# ------------------------------------------------------------------------------------------ regression: one entity under several surfaces counts once
WP2_OPTS = [{"label": f"{n}-service", "id": f"service:{n}", "display": f"{n}-service", "aliases": [n]} for n in ("tallysync", "manifestdesk", "quotedesk", "yardloom")]
WP2_ENTITIES = ["tallysync-service", "manifestdesk-service", "quotedesk-service", "yardloom-service", "parcelhub-service", "parcelhub-service-two"]


def wp2_task(tid="dev-000"):
    return {"task_id": tid, "tenant": "acme", "options": WP2_OPTS, "question_text": "Which service sits behind the sable spindle?"}


def test_regression_exact_text_one_service_maps_to_the_option_with_a_public_entity_list():
    vocab = score.build_vocabulary([wp2_task()], WP2_ENTITIES)             # plain strings, as WP2's top-level ``entities`` list
    v = view([claim("c1", "tallysync-service: origin of the sable spindle, 42.", roots=3)])
    ext = score.extract_answer(v, wp2_task(), vocabulary=vocab)
    assert ext.option == "tallysync-service" and ext.ambiguous_claims == 0 and ext.matches == {"tallysync-service": 3}


def test_regression_every_surface_of_one_entity_is_one_canonical_key():
    assert {score.canon_key(x) for x in ("tallysync-service", "Service:TallySync", "service:tallysync", "TALLYSYNC-SERVICE")} == {"service:tallysync"}
    assert score.canon_key("issue:tracker:LGX-412") == "issue:tracker:lgx-412" and score.canon_key("parcelhub-service-two") == "parcelhub-service-two"
    vocab = score.build_vocabulary([wp2_task()], WP2_ENTITIES)
    for text in ("tallysync-service", "service:tallysync", "tallysync", "Tallysync (service:tallysync, tallysync-service) is flaky"):
        assert vocab.entities_in(text) == {"service:tallysync"}, text


def test_regression_two_different_services_are_ambiguous():
    vocab = score.build_vocabulary([wp2_task()], WP2_ENTITIES)
    ext = score.extract_answer(view([claim("c1", "tallysync-service and manifestdesk-service both stall", roots=9)]), wp2_task(), vocabulary=vocab)
    assert ext.option == score.ABSTAIN and ext.ambiguous_claims == 1
    ext = score.extract_answer(view([claim("c1", "tallysync-service blames somethingelse-service", roots=9)]), wp2_task(), vocabulary=vocab)   # a service not in any list
    assert ext.option == score.ABSTAIN and ext.ambiguous_claims == 1


def test_regression_names_that_contain_other_names_do_not_collide():
    vocab = score.build_vocabulary([wp2_task()], WP2_ENTITIES)
    assert vocab.entities_in("parcelhub-service is slow") == {"service:parcelhub"}
    assert vocab.entities_in("parcelhub-service-two is slow") == {"parcelhub-service-two"}            # not also parcelhub
    assert vocab.entities_in("parcelhub-service and parcelhub-service-two") == {"service:parcelhub", "parcelhub-service-two"}
    assert vocab.entities_in("tallysyncing, tallysync2 and xtallysync") == set()                       # substrings of other words


def test_regression_score_run_with_a_public_entities_list(tmp_path):
    t = wp2_task("dev-000")
    v = view([claim("c0", "tallysync-service: origin of the sable spindle, 42.", roots=3)])
    v["task_id"] = "dev-000"
    d = _write_run(tmp_path, [t], {"dev-000": gold("tallysync-service") | {"task_id": "dev-000"}}, {"dev-000": v})
    pub = json.loads((d / "tasks_dev.public.json").read_text())
    pub["entities"] = WP2_ENTITIES
    (d / "tasks_dev.public.json").write_text(json.dumps(pub))
    rs = score.score_run(d)
    assert rs.correct == 1 and rs.tasks[0].extracted == "tallysync-service" and rs.supporting["ambiguous_supported_claims"] == 0

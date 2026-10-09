"""H2: every evidence reference the asker can see gets its raw check, including the ones that only a discovery detail lists.

``score.all_visible_refs`` counts the references of claims, ``view["evidence"]`` and ``discoveries[*].evidence`` (question level and goal level
alike) as visible. ``issue.collect_view`` used to raw-check only claim evidence, question-discovery evidence and goal-claim evidence, so a
reference listed only by a GOAL discovery was reported as "raw unchecked" and counted as a disclosure with raw checks required (dev run
C6abl-A1-S1: 344 of them, none an observed disclosure). The measurement must be complete, not merely quiet.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import issue, score                    # noqa: E402

import test_score as TS                           # noqa: E402

TENANT = "ten_a"
REFS = {name: TS.ref(f"ev_{name}", holder="h_other", root=f"root_{name}") for name in ("claim", "goal_claim", "q_disc", "goal_disc")}


class DetailClient:
    """A question with one claim and one discovery; its goal also lists a second claim and a second discovery, each carrying its own reference."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def call(self, method, path, token, body=None, params=None):
        self.calls.append((method, path))
        if path == "/api/questions/q1":
            return 200, {"question": {"question_id": "q1", "status": "committed", "result": {"outcome": "committed"}, "routes": []},
                         "claims": [{"claim_id": "c1"}], "discoveries": [{"discovery_id": "d1"}], "followups": []}
        if path == "/api/goals/g1":
            return 200, {"goal": {"goal_id": "g1", "status": "active", "title": "g"}, "loop": {"state": "idle"}, "questions": [],
                         "claims": [{"claim_id": "c1"}, {"claim_id": "c2"}], "discoveries": [{"discovery_id": "d1"}, {"discovery_id": "d2"}]}
        if path == "/api/claims/c1":
            return 200, TS.claim("c1", "LGX-412 late", refs=[REFS["claim"]])
        if path == "/api/claims/c2":
            return 200, TS.claim("c2", "ParcelRouter slow", refs=[REFS["goal_claim"]])
        if path == "/api/discoveries/d1":
            return 200, {"discovery": {"discovery_id": "d1", "summary": "q"}, "evidence": [REFS["q_disc"]]}
        if path == "/api/discoveries/d2":
            return 200, {"discovery": {"discovery_id": "d2", "summary": "g"}, "evidence": [REFS["goal_disc"]]}
        if path.startswith("/api/evidence/") and path.endswith("/raw"):
            return 403, {"error": "forbidden"}
        return 404, {}


def collect():
    client = DetailClient()
    task = TS.task("t1") | {"goal_title": "g"}
    iss = issue.Issued("t1", goal_id="g1", question_id="q1")
    return client, task, asyncio.run(issue.collect_view(client, task, iss, "tok", tenant_id=TENANT))


def test_goal_discovery_evidence_gets_its_raw_check_and_is_not_reported_unchecked():
    client, task, view = collect()
    assert view["status"] == "ok" and [d["discovery"]["discovery_id"] for d in view["goal_discoveries"]] == ["d2"]
    assert set(view["raw_checks"]) == {r["ref_id"] for r in REFS.values()}                  # all four, among them the one only a goal discovery lists
    assert view["raw_checks"]["ev_goal_disc"] == 403
    g = TS.gold("abstain", "coincidence")
    chk = score.check_disclosure(view, task, g, raw_checks_required=True)
    assert chk.raw_unchecked == [] and chk.raw_open == [] and chk.count == 0
    # control: the same view without that raw check IS reported unchecked (this is what the run C6abl-A1-S1 showed for 344 references)
    stripped = {**view, "raw_checks": {k: v for k, v in view["raw_checks"].items() if k != "ev_goal_disc"}}
    again = score.check_disclosure(stripped, task, g, raw_checks_required=True)
    assert again.raw_unchecked == ["ev_goal_disc"] and again.count == 1


def test_everything_else_in_the_view_is_unchanged():
    client, _task, view = collect()
    assert [c["claim"]["claim_id"] for c in view["claims"]] == ["c1"] and [c["claim"]["claim_id"] for c in view["goal_claims"]] == ["c2"]
    assert [r["ref_id"] for r in view["evidence"]] == ["ev_claim", "ev_q_disc"]                      # the question-level list does not gain goal-level refs
    assert [r["ref_id"] for r in view["goal_evidence"]] == ["ev_goal_claim"]                         # nor does the goal-claim list gain discovery refs
    claim_calls = [p for _m, p in client.calls if p.startswith("/api/claims/")]
    assert claim_calls == ["/api/claims/c1", "/api/claims/c2"]                                        # claim call order as before
    raw_calls = [p for _m, p in client.calls if p.endswith("/raw")]
    assert raw_calls == [f"/api/evidence/ev_{n}/raw" for n in ("claim", "q_disc", "goal_claim", "goal_disc")] and len(raw_calls) == len(set(raw_calls))

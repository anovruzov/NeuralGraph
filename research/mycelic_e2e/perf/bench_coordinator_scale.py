"""Coordinator-side routing cost at N users WITHOUT opening holder stores (so N = 10,000 fits in memory): measured, not extrapolated.

Creates the tenant (N users, memberships, N embedded holders registered in coord.db) and times the O(N) / O(N^2) pieces a discovery
tick and a question route run: OrgService.list_holders, Authorizer.can_route over all holders, QuestionService.candidate_holders,
LoopEngine.domains_for_goal (per tick), QuestionService.question_audience (per route, uncached), plus tenant creation.

    python research/mycelic_e2e/perf/bench_coordinator_scale.py --n 10000 --out research/mycelic_e2e/perf/results/coord_n10000.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_bench as pb  # noqa: E402


COLD: dict[str, float] = {}


def timed(fn, reps: int = 1, name: str = ""):
    """min over ``reps`` runs; ``COLD[name]`` keeps the first run (caches empty), which is the honest number for cached code."""
    best = []
    for _ in range(reps):
        a = time.perf_counter()
        r = fn()
        best.append(time.perf_counter() - a)
    if name:
        COLD[name] = round(1000 * best[0], 2)
    return r, round(1000 * min(best), 2)


async def run(n: int) -> dict:
    base = Path(tempfile.mkdtemp(prefix=f"mycelic_coord_n{n}_", dir=os.environ.get("PERF_TMP", "/tmp")))
    out: dict = {"n": n, "rss_start_mb": pb.rss_mb()}
    rt = pb.build_runtime(pb.bench_settings(base), with_holders=False)
    t0 = time.perf_counter()
    ctx = await pb.phase_tenant(rt, n)
    out["tenant_create_s"] = round(time.perf_counter() - t0, 2)
    out["tenant_per_user_ms"] = ctx["timing"]["per_user_total_ms"]
    out["coord_db_bytes"] = ctx["timing"]["coord_db_bytes"]
    tid = ctx["tenant_id"]
    admin = rt.authz.principal_for_user(ctx["admin_id"])
    goal = await rt.goals.create_goal(admin, {"title": "g", "objective": "o", "owner_type": "unit", "owner_id": ctx["root"],
                                              "budget": {"tokens": 10**7, "usd": 100, "questions": 500}}, activate=True)
    loop_p = rt.authz.principal_for_loop(tid, goal)
    q = {"question_id": "q_probe", "tenant_id": tid, "scope_unit_id": ctx["root"], "policy": {"visibility": "unit"}, "asker_type": "loop",
         "asker_id": f"goal:{goal['goal_id']}", "goal_id": goal["goal_id"], "candidate_domains": ["engineering"]}
    hs, out["list_holders_ms"] = timed(lambda: rt.org.list_holders(tid), 2, "list_holders")
    out["holders"] = len(hs)
    _, out["can_route_all_holders_ms"] = timed(lambda: [rt.authz.can_route(q, h) for h in hs], 2, "can_route_all_holders")
    (ok, rej), out["candidate_holders_ms"] = timed(lambda: rt.questions.candidate_holders(q, asker=loop_p), 2, "candidate_holders")
    out["routable_holders"], out["rejected_holders"] = len(ok), len(rej)
    (dom, hh), out["domains_for_goal_ms"] = timed(lambda: rt.engine.domains_for_goal(goal), 2, "domains_for_goal")
    out["domains_for_goal_holders"], out["domains_found"] = len(hh), len(dom)

    def aud():
        rt.questions._audience_cache.clear()
        return rt.questions.question_audience(q)
    a, out["question_audience_uncached_ms"] = timed(aud, 1, "question_audience")
    out["audience_size"] = len(a["principal_ids"])
    _, out["question_audience_cached_ms"] = timed(lambda: rt.questions.question_audience(q), 1)
    # a second, unrelated write invalidates the cache (db.revision is in its key): the steady state of a busy coordinator
    out["first_run_ms"] = dict(COLD)        # the first of the repetitions: caches empty (the *_ms fields above are the best of two)
    out["peak_rss_mb"] = pb.peak_rss_mb()
    await rt.stop()
    shutil.rmtree(base, ignore_errors=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = asyncio.run(run(a.n))
    txt = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(txt)
    print(txt)

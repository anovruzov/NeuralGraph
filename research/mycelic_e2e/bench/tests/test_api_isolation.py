"""H1: a per-task API error or timeout makes THAT task wrong (BENCHMARK_CONTRACT section 5); it never aborts the run.

The dev run C5-S1 died in wave 5 on one ``aiohttp`` TimeoutError (the API was starved by a slow worker) after 60 of 120 views; the same crash
in the single holdout run would consume the shot with no result. The wave loop in ``run.py`` now calls the ``safe_*`` wrappers of ``issue.py``
for every per-task interaction; these tests drive the wrappers with a client that fails for one task, and pin the scorer's reading of the
resulting error view for every task class.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import issue, score                    # noqa: E402

import test_score as TS                           # noqa: E402

TENANT = "ten_a"


class FlakyClient:
    """A healthy API: every call succeeds with the minimal payload the issuer and the view collector read."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def call(self, method, path, token, body=None, params=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/api/goals":
            return 201, {"goal": {"goal_id": "g_" + str(body["title"])}}
        if method == "POST" and path == "/api/questions":
            return 201, {"question": {"question_id": "q_" + str(body["goal_id"])}}
        if method == "POST" and path.endswith("/loop"):
            return 200, {"ok": True}
        if method == "GET" and path.startswith("/api/questions/"):
            return 200, {"question": {"question_id": path.rsplit("/", 1)[1], "status": "committed", "result": {"outcome": "committed"}, "routes": []}, "claims": [], "discoveries": [],
                         "followups": []}
        if method == "GET" and path.startswith("/api/goals/"):
            return 200, {"goal": {"goal_id": path.rsplit("/", 1)[1], "status": "active", "title": "g"}, "loop": {"state": "idle"}, "questions": [], "claims": [], "discoveries": []}
        return 200, {}


def tasks3():
    return [{"task_id": f"t{i}", "goal_title": f"T{i}", "goal_objective": "o", "question_text": f"Which entity explains delay {i}?", "candidate_domains": [], "goal_domains": []}
            for i in (1, 2, 3)]


class TitledClient(FlakyClient):
    """Raises ``TimeoutError`` like a starved server, only for the named tasks: the goal POST (by goal title), the question POST (by goal), the
    question GET (by question id; ``status`` and ``read`` are the same request) and the loop run_now (by goal id)."""

    def __init__(self, *, fail_goal=(), fail_question=(), fail_status=(), fail_loop=(), fail_read=()) -> None:
        super().__init__()
        self.f = dict(goal=set(fail_goal), question=set(fail_question), status=set(fail_status), loop=set(fail_loop), read=set(fail_read))

    async def call(self, method, path, token, body=None, params=None):
        if method == "POST" and path == "/api/goals" and body["title"] in self.f["goal"]:
            raise TimeoutError()
        if method == "POST" and path == "/api/questions" and body["goal_id"].removeprefix("g_") in self.f["question"]:
            raise TimeoutError()
        if method == "GET" and path.startswith("/api/questions/"):
            qid = path.rsplit("/", 1)[1]
            if qid in self.f["status"]:
                raise TimeoutError()
            if qid in self.f["read"]:
                raise TimeoutError()
        if method == "POST" and path.endswith("/loop") and path.split("/")[3] in self.f["loop"]:
            raise TimeoutError()
        return await super().call(method, path, token, body, params)


def logger():
    lines: list[str] = []
    return lines, lines.append


def test_issue_failure_marks_only_that_task_as_error():
    lines, log = logger()
    errors = issue.ApiErrors(log)
    client = TitledClient(fail_goal={"T2"}, fail_question={"T3"})

    async def go():
        return await asyncio.gather(*[issue.safe_issue_task(client, t, errors=errors, token="tok", scope_unit_id="u1") for t in tasks3()])
    r1, r2, r3 = asyncio.run(go())
    assert r1.error is None and r1.goal_id == "g_T1" and r1.question_id == "q_g_T1"                   # unaffected
    assert r2.goal_id is None and r2.question_id is None and r2.error.startswith("issue: TimeoutError")  # the POST of the goal timed out
    assert r3.question_id is None and r3.error.startswith("issue: TimeoutError")                         # the goal exists, its question POST timed out
    assert errors.counts == {"issue": 2, "status": 0, "loop": 0, "view": 0} and set(errors.tasks) == {"t2", "t3"}
    assert len(lines) == 2 and "t2" in lines[0] and "t3" in lines[1] and "issue" in lines[0]


def test_failed_status_poll_counts_as_unresolved_and_polling_goes_on():
    lines, log = logger()
    errors = issue.ApiErrors(log)

    class Once(TitledClient):
        n = 0

        async def call(self, method, path, token, body=None, params=None):
            if method == "GET" and path == "/api/questions/q_a" and Once.n < 2:
                Once.n += 1
                raise TimeoutError("starved")
            return await super().call(method, path, token, body, params)
    client = Once()

    async def poll():
        out = []
        for _ in range(3):
            out.append(await issue.safe_question_status(client, "q_a", "tok", errors=errors, task_id="t1"))
        return out
    states = asyncio.run(poll())
    assert states[:2] == ["error", "error"] and states[2] == "committed"
    assert all(s not in issue.RESOLVED for s in states[:2])                  # the caller's `all(s in RESOLVED)` stays false: keep polling to the deadline
    assert errors.counts["status"] == 2 and len(lines) == 1                  # counted every time, logged once per task


def test_failed_loop_run_is_logged_and_the_run_goes_on():
    lines, log = logger()
    errors = issue.ApiErrors(log)
    client = TitledClient(fail_loop={"g_bad"})

    async def go():
        a = await issue.safe_run_loop_now(client, "g_bad", "tok", errors=errors, task_id="t2")
        b = await issue.safe_run_loop_now(client, "g_ok", "tok", errors=errors, task_id="t1")
        return a, b
    a, b = asyncio.run(go())
    assert a is None and b == 200 and errors.counts["loop"] == 1 and "t2" in lines[0] and "loop" in lines[0]


def test_collect_view_failure_is_an_error_view_in_the_normal_shape_and_other_tasks_are_unaffected():
    errors = issue.ApiErrors()
    client = TitledClient(fail_read={"q_g_T2"})
    tasks = tasks3()

    async def go():
        out = {}
        for t in tasks:
            iss = await issue.safe_issue_task(client, t, errors=errors, token="tok", scope_unit_id="u1")
            out[t["task_id"]] = await issue.safe_collect_view(client, t, iss, "tok", tenant_id=TENANT, errors=errors)
        return out
    views = asyncio.run(go())
    good, bad = views["t1"], views["t2"]
    assert good["status"] == "ok" and views["t3"]["status"] == "ok" and good["question"]["status"] == "committed"
    assert bad["status"] == "error" and bad["error"].startswith("TimeoutError") and len(bad["error"]) <= 220
    assert bad["tenant_id"] == TENANT and bad["task_id"] == "t2" and bad["claims"] == [] and bad["question"] is None
    assert set(bad) <= set(good)                                                # nothing a healthy view lacks (a healthy view adds the goal_* lists at the end)
    assert errors.counts == {"issue": 0, "status": 0, "loop": 0, "view": 1} and list(errors.tasks) == ["t2"]
    # the pre-existing error shape (an issue failure) is the same shape
    iss = issue.Issued("t9", error="goal: HTTP 500")
    ev = asyncio.run(issue.collect_view(client, tasks[0] | {"task_id": "t9"}, iss, "tok", tenant_id=TENANT))
    assert set(ev) == set(bad) and ev["status"] == "error"


def test_issue_error_reaches_the_view_as_an_error_view():
    errors = issue.ApiErrors()
    client = TitledClient(fail_goal={"T1"})
    t = tasks3()[0]

    async def go():
        iss = await issue.safe_issue_task(client, t, errors=errors, token="tok", scope_unit_id="u1")
        return await issue.safe_collect_view(client, t, iss, "tok", tenant_id=TENANT, errors=errors)
    v = asyncio.run(go())
    assert v["status"] == "error" and v["error"].startswith("issue: TimeoutError") and v["tenant_id"] == TENANT and v["claims"] == []
    assert errors.counts["view"] == 0                                           # collect_view itself did not fail: the error came from the issue step


def test_cancellation_is_not_swallowed():
    errors = issue.ApiErrors()

    class Cancelling(FlakyClient):
        async def call(self, *a, **k):
            raise asyncio.CancelledError()

    async def go():
        await issue.safe_question_status(Cancelling(), "q", "tok", errors=errors, task_id="t1")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(go())


def test_run_driver_uses_only_the_isolated_calls_in_its_wave_loop():
    src = (BENCH / "run.py").read_text()
    for bare in ("issue.issue_task(", "issue.question_status(", "issue.run_loop_now(", "issue.collect_view("):
        assert bare not in src, f"run.py calls {bare} outside the per-task isolation"
    for safe in ("issue.safe_issue_task(", "issue.safe_question_status(", "issue.safe_run_loop_now(", "issue.safe_collect_view("):
        assert src.count(safe) == 1, safe
    assert '"api_errors": dict(api_errors.counts)' in src                        # counted in run_manifest.json


# ------------------------------------------------------------------------------------------ what an error view scores
ALL_CLASSES = [("cross_domain", "A"), ("single_domain", "A"), ("contradiction", "A"), ("temporal", "A"), ("common_origin", "A"), ("fault", "A"),
               ("coincidence", "abstain"), ("denied", "abstain"), ("cross_tenant", "abstain"), ("copies", "abstain")]


def _error_view(tid="t"):
    t = TS.task(tid)
    iss = issue.Issued(tid, goal_id="g", question_id="q")
    return issue.error_view(t, iss, tenant_id=TENANT, exc=TimeoutError("total=300")), t


@pytest.mark.parametrize("cls, answer", ALL_CLASSES)
def test_error_view_is_wrong_for_every_class_including_expected_abstain(cls, answer):
    v, t = _error_view()
    t = t | {"class": cls}
    g = TS.gold(TS.LBL["A"] if answer == "A" else "abstain", cls)
    ext = score.extract_answer(v, t)
    ts = score.score_task(ext, g, score.check_disclosure(v, t, g), task=t, view=v)
    assert ext.option is None and ext.reason == "error" and ts.reason == "error" and not ts.correct
    # the same for a timeout view, a missing view and a view the harness could not read
    for other, reason in ((TS.view(status="timeout", error="deadline"), "timeout"), (None, "missing_view")):
        e2 = score.extract_answer(other, t)
        assert score.score_task(e2, g, task=t, view=other).correct is False and e2.reason == reason


def test_run_with_one_error_view_scores_that_task_wrong_and_the_rest_normally(tmp_path):
    tasks, golds, views = [], {}, {}
    for i, (cls, ans) in enumerate([("cross_domain", "A"), ("coincidence", "abstain"), ("cross_domain", "A"), ("denied", "abstain")]):
        tid = f"t{i}"
        tasks.append(TS.task(tid, cls))
        golds[tid] = TS.gold(TS.LBL["A"] if ans == "A" else "abstain", cls) | {"task_id": tid}
        if i in (0, 1):
            v = TS.view([TS.claim(f"c{i}", "LGX-412 late")] if ans == "A" else [])
        else:
            v, _ = _error_view(tid)                                             # the API timed out for these two: positive and expected-abstain
        v["task_id"] = tid
        views[tid] = v
    d = TS._write_run(tmp_path, tasks, golds, views)
    rs = score.score_run(d)                                                    # tenant_id present on every view: the scorer's own check passes
    assert rs.n == 4 and rs.correct == 2 and rs.errors == {"error": 2}
    by = {r.cls: r for r in rs.per_class}
    assert by["cross_domain"].correct == 1 and by["denied"].correct == 0 and by["denied"].errors == 1

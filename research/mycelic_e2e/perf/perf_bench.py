"""Performance probe of the in-process Mycelic stack: tenant+holders, ingestion, one discovery goal (see PERF_PROBE.md).

One process per N (so RSS is clean). The system under test is the real one: coordinator CoordDB (coord.db), SqliteTransport,
one EmbeddedHolders holder per user (EvidenceStore on a NeuralGraph store, HolderService transport consumer, IngestRuntime),
local_export connector -> IngestPipeline -> EvidenceStore, hash-256 embeddings, the deterministic fake model provider and the
real LoopEngine / DiscoveryWorker. Nothing is mocked except the model provider (FakeProvider) which the product ships.

    python research/mycelic_e2e/perf/perf_bench.py --n 200 --out perf/results/n200.json
    python research/mycelic_e2e/perf/perf_bench.py --n 200 --profile ingest --out perf/results/prof_ingest_n200.json
"""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json
import logging
import os
import platform
import pstats
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

from mycelic.config import Settings  # noqa: E402
from mycelic.runtime import build_runtime, build_worker  # noqa: E402
from mycelic.util import token  # noqa: E402

TOP_DOMAINS = ["engineering", "infrastructure", "product", "sales", "finance", "legal", "operations", "research",
               "customer-support", "security", "hr", "executive-strategy"]
TEAM_SIZE = 10
TEAMS_PER_DEPT = 10


# ------------------------------------------------------------------------------------------------ small helpers
def rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 1)
    return -1.0


def peak_rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)
    return -1.0


def n_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def dir_bytes(p: Path, pattern: str = "*") -> int:
    return sum(f.stat().st_size for f in p.rglob(pattern) if f.is_file())


def disk_by_suffix(holders_dir: Path, n: int) -> dict[str, int]:
    tot: dict[str, int] = defaultdict(int)
    for f in holders_dir.rglob("evidence.db*"):
        tot[f.suffix or ".db"] += f.stat().st_size
    return {k: round(v / max(1, n)) for k, v in sorted(tot.items())}


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 3)


def dist(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean_ms": round(1000 * statistics.fmean(xs), 3), "p50_ms": round(1000 * pct(xs, 0.5), 3),
            "p95_ms": round(1000 * pct(xs, 0.95), 3), "max_ms": round(1000 * max(xs), 3), "sum_s": round(sum(xs), 3)}


def machine() -> dict[str, Any]:
    cpu = next((l.split(":", 1)[1].strip() for l in Path("/proc/cpuinfo").read_text().splitlines() if l.startswith("model name")), "")
    mem = next((l.split(":", 1)[1].strip() for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal")), "")
    return {"platform": platform.platform(), "cpu": cpu, "cpus": os.cpu_count(), "mem": mem, "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version}


def bench_settings(base: Path) -> Settings:
    s = Settings()
    s.data_dir = base
    s.coord_db = str(base / "coord.db")
    s.holders_dir = str(base / "holders")
    s.host, s.port, s.public_url = "127.0.0.1", 0, ""
    s.secret_key = token(32)
    s.demo_mode, s.embedded_holders, s.run_worker_in_api = False, True, False
    s.transport = "sqlite"
    s.embed_provider = "hash"
    s.loop_question_timeout_seconds = 60          # collect is pulled forward as soon as every routed holder has answered
    s.loop_max_concurrent_questions = 3
    s.loop_cooldown_seconds = 900
    s.loop_max_followup_depth = 2
    s.worker_concurrency = 2
    s.worker_lease_seconds = 120.0
    s.log_json = False
    return s


class Prof:
    """cProfile around one phase; ``top(n)`` returns the n costliest functions by cumulative and by own time."""

    def __init__(self, enabled: bool) -> None:
        self.p = cProfile.Profile() if enabled else None

    def __enter__(self):
        if self.p:
            self.p.enable()
        return self

    def __exit__(self, *a):
        if self.p:
            self.p.disable()

    def top(self, n: int = 15) -> dict[str, Any] | None:
        if not self.p:
            return None
        out: dict[str, Any] = {}
        for key in ("tottime", "cumulative"):
            buf = io.StringIO()
            st = pstats.Stats(self.p, stream=buf)
            st.sort_stats(key)
            rows = []
            for (fn, line, name), (cc, nc, tt, ct, _callers) in sorted(st.stats.items(), key=lambda kv: -(kv[1][2] if key == "tottime" else kv[1][3]))[:n]:
                try:
                    rel = str(Path(fn).relative_to(ROOT))
                except ValueError:
                    rel = fn.split("site-packages/")[-1] if "site-packages" in fn else fn
                rows.append({"where": f"{rel}:{line}", "func": name, "ncalls": nc, "tottime_s": round(tt, 3), "cumtime_s": round(ct, 3)})
            out[key] = rows
        out["total_s"] = round(sum(v[2] for v in pstats.Stats(self.p).stats.values()), 3)
        return out


# ------------------------------------------------------------------------------------------------ phase 1: tenant
async def phase_tenant(rt, n: int) -> dict[str, Any]:
    org, auth = rt.org, rt.auth
    t: dict[str, Any] = {"n_users": n}
    t0 = time.perf_counter()
    reg = await auth.register_tenant(org_name="Perf Org", slug="perf", admin_email="admin@perf.test", admin_name="Admin", password="password123")
    t["register_tenant_s"] = round(time.perf_counter() - t0, 3)
    tid, root = reg["tenant"]["tenant_id"], reg["root_unit"]["unit_id"]
    n_teams = max(1, -(-n // TEAM_SIZE))
    n_depts = max(1, -(-n_teams // TEAMS_PER_DEPT))
    t0 = time.perf_counter()
    depts = [await org.create_unit(tid, "department", f"Dept {i}", parent_id=root) for i in range(n_depts)]
    teams = [await org.create_unit(tid, "team", f"Team {i}", parent_id=depts[i // TEAMS_PER_DEPT]["unit_id"]) for i in range(n_teams)]
    t["units_s"] = round(time.perf_counter() - t0, 3)
    t["depts"], t["teams"] = n_depts, n_teams
    users, d_user, d_mem, d_hold = [], [], [], []
    t_all = time.perf_counter()
    for i in range(n):
        team_i = i // TEAM_SIZE
        doms = [TOP_DOMAINS[team_i % len(TOP_DOMAINS)], TOP_DOMAINS[(team_i + 1) % len(TOP_DOMAINS)]]
        a = time.perf_counter()
        u = await org.create_user(tid, f"user{i}@perf.test", f"User {i}")
        b = time.perf_counter()
        await org.add_membership(tid, u["user_id"], teams[team_i]["unit_id"], "team_lead" if i % TEAM_SIZE == 0 else "employee")
        c = time.perf_counter()
        h, _key = await org.register_holder(tid, owner_type="user", owner_id=u["user_id"], name=f"User {i} notes", mode="embedded", domains=doms)
        d = time.perf_counter()
        d_user.append(b - a); d_mem.append(c - b); d_hold.append(d - c)
        users.append({"i": i, "user_id": u["user_id"], "holder_id": h["holder_id"], "team": teams[team_i]["unit_id"], "domains": doms, "email": u["email"]})
    t["users_memberships_holders_total_s"] = round(time.perf_counter() - t_all, 3)
    t["per_user_total_ms"] = round(1000 * (time.perf_counter() - t_all) / max(1, n), 3)
    t["create_user"], t["add_membership"], t["register_holder"] = dist(d_user), dist(d_mem), dist(d_hold)
    t["tenant_total_s"] = round(t["register_tenant_s"] + t["units_s"] + t["users_memberships_holders_total_s"], 3)
    t["coord_db_bytes"] = Path(rt.settings.coord_db).stat().st_size
    t["tables_rows"] = {name: rt.db.scalar(f"SELECT COUNT(*) FROM {name}") for name in ("users", "memberships", "holders", "org_units", "audit_log", "events")}
    return {"timing": t, "tenant_id": tid, "root": root, "admin_id": reg["user"]["user_id"], "users": users, "teams": [x["unit_id"] for x in teams]}


# ------------------------------------------------------------------------------------------------ phase 2: open holders
async def phase_open(rt, users: list[dict], base: Path) -> dict[str, Any]:
    h = rt.holders
    o: dict[str, Any] = {"n_holders": len(users)}
    await rt.transport.start()
    rss0, fd0, cpu0, t0 = rss_mb(), n_fds(), time.process_time(), time.perf_counter()
    per, series = [], []
    step = max(1, len(users) // 10)
    for k, u in enumerate(users):
        a = time.perf_counter()
        await h.ensure(u["holder_id"])
        per.append(time.perf_counter() - a)
        if (k + 1) % step == 0 or k + 1 == len(users):
            series.append({"open": k + 1, "rss_mb": rss_mb(), "fds": n_fds()})
    h.running = True
    o["open_all_s"] = round(time.perf_counter() - t0, 3)
    o["open_all_cpu_s"] = round(time.process_time() - cpu0, 3)
    o["open_each"] = dist(per)
    o["first10_mean_ms"] = round(1000 * statistics.fmean(per[:10]), 3)
    o["last10_mean_ms"] = round(1000 * statistics.fmean(per[-10:]), 3)
    o["rss_before_mb"], o["rss_after_mb"] = rss0, rss_mb()
    o["rss_growth_mb_per_open_holder"] = round((o["rss_after_mb"] - rss0) / max(1, len(users)), 3)
    o["rss_series"] = series
    o["fds_before"], o["fds_after"] = fd0, n_fds()
    o["fds_per_holder"] = round((o["fds_after"] - fd0) / max(1, len(users)), 2)
    o["open_stores_in_memory"] = len(h._stores)
    o["disk_per_holder_empty_bytes"] = round(dir_bytes(Path(rt.settings.holders_dir)) / max(1, len(users)))
    o["disk_per_holder_by_file_bytes"] = disk_by_suffix(Path(rt.settings.holders_dir), len(users))
    o["files_per_holder"] = sorted({f.name for f in Path(rt.settings.holders_dir).rglob("*") if f.is_file()})[:8]
    # idle cost of N open holders: transport consumers poll, heartbeat and ingest loops run
    c0, w0 = time.process_time(), time.perf_counter()
    await asyncio.sleep(8.0)
    o["idle_cpu_fraction_of_one_core"] = round((time.process_time() - c0) / (time.perf_counter() - w0), 3)
    # one reconcile pass with every holder already running (the API runs this every ~20 s)
    a = time.perf_counter()
    await h.reconcile()
    o["reconcile_noop_s"] = round(time.perf_counter() - a, 4)
    # close / reopen individual holders
    sample = [u["holder_id"] for u in users[:: max(1, len(users) // 20)]][:20]
    close_t, reopen_t = [], []
    for hid in sample:
        a = time.perf_counter(); await h.remove(hid); close_t.append(time.perf_counter() - a)
    for hid in sample:
        a = time.perf_counter(); await h.ensure(hid); reopen_t.append(time.perf_counter() - a)
    o["close_one"], o["reopen_one"] = dist(close_t), dist(reopen_t)
    return o


# ------------------------------------------------------------------------------------------------ phase 3: ingestion
CREATED_AT = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")      # fresh enough not to be stale
AGREE = True        # holders of one domain report the same figure (supported claims); False: figures differ per user (contested claims)
WORDS = ["alpha", "bravo", "delta", "gamma", "kappa", "omega", "sigma", "theta", "zeta", "lambda", "tango", "victor", "yankee", "zulu"]


def record_text(i: int, k: int, domain: str) -> tuple[str, str]:
    """Text of record k of user i. No per-user digits unless --disagree (the fake evaluator treats differing figures as contradictions)."""
    title = f"{domain} notes {i}-{k}"
    days = "two" if AGREE else str(2 + (i + k) % 5)
    w1, w2 = WORDS[(i + k) % len(WORDS)], WORDS[(i // 3 + 2 * k) % len(WORDS)]
    text = (f"Recurring operational blocker related to {domain}: approvals for {domain} changes take {days} days because the review board meets twice a week. "
            f"Tickets stay open while we wait; caused by the {domain} review backlog in the {w1} queue, noted by the {w2} group.")
    return title, text


async def phase_ingest(rt, users: list[dict], base: Path, records_per_user: int, prof: Prof) -> dict[str, Any]:
    h = rt.holders
    exp = base / "exports"
    exp.mkdir(exist_ok=True)
    per_holder, connect_t, drain_t = [], [], []
    rss0, cpu0 = rss_mb(), time.process_time()
    total_records = 0
    t_wall0 = time.perf_counter()
    with prof:
        for u in users:
            i = u["i"]
            ing = h.ingest(u["holder_id"])
            lines = [json.dumps({"type": "source", "id": f"u{i}-notes", "name": f"User {i} notes", "source_type": "channel", "visibility": "public",
                                 "domains": u["domains"][:1]})]
            for k in range(records_per_user):
                dom = u["domains"][k % 2]
                title, text = record_text(i, k, dom)
                lines.append(json.dumps({"type": "document", "id": f"u{i}-r{k}", "author": u["email"], "created_at": CREATED_AT, "title": title, "text": text}))
            p = exp / f"u{i}.jsonl"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            a = time.perf_counter()
            con = await ing.service.add_connector("local_export", created_by=u["user_id"],
                                                  config={"source_app": "notes", "account_id": f"u{i}", "paths": [str(p)], "auto_include": True})
            await ing.service.discover_sources(con["connector_id"])
            for s in ing.service.sources(con["connector_id"]):
                if s.selection != "included":
                    await ing.service.set_source(s.source_id, actor=u["user_id"], selection="included")
            b = time.perf_counter()
            r = await ing.drain()
            c = time.perf_counter()
            connect_t.append(b - a); drain_t.append(c - b); per_holder.append(c - a)
            total_records += r["processed"]
    wall = time.perf_counter() - t_wall0
    out: dict[str, Any] = {"records_per_user": records_per_user, "records_processed": total_records, "wall_s": round(wall, 3),
                           "cpu_s": round(time.process_time() - cpu0, 3), "aggregate_records_per_s": round(total_records / wall, 2),
                           "per_holder_total": dist(per_holder), "connect_discover": dist(connect_t), "drain_process": dist(drain_t),
                           "per_holder_records_per_s_mean": round(records_per_user / statistics.fmean(per_holder), 2),
                           "rss_before_mb": rss0, "rss_after_mb": rss_mb(), "peak_rss_mb": peak_rss_mb()}
    # what landed
    docs = mems = vec = 0
    for u in users[:: max(1, len(users) // 20)][:20]:
        st = h.get(u["holder_id"])
        c = st.store._conn
        docs += c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        mems += c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    sample_n = len(users[:: max(1, len(users) // 20)][:20])
    out["sample_holders"] = sample_n
    out["documents_per_holder_sampled"] = round(docs / sample_n, 2)
    out["memories_per_holder_sampled"] = round(mems / sample_n, 2)
    out["disk_per_holder_after_bytes"] = round(dir_bytes(Path(rt.settings.holders_dir), "*.db*") / len(users))
    out["disk_per_holder_after_by_file_bytes"] = disk_by_suffix(Path(rt.settings.holders_dir), len(users))
    for u in users[:: max(1, len(users) // 20)][:20]:                      # WAL folded into the main file for the sampled holders
        h.get(u["holder_id"]).store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    out["disk_sampled_holders_after_checkpoint_db_bytes"] = round(statistics.fmean(
        Path(h.store_path(u["holder_id"])).stat().st_size for u in users[:: max(1, len(users) // 20)][:20]))
    out["transport_messages"] = rt.db.scalar("SELECT COUNT(*) FROM transport_messages")
    out["transport_by_kind"] = {r[0]: r[1] for r in rt.db.all("SELECT json_extract(payload,'$.kind') k, COUNT(*) FROM transport_messages GROUP BY k")}
    out["model_usage_rows_during_ingest"] = rt.db.scalar("SELECT COUNT(*) FROM model_usage")
    out["model_usage_by_purpose"] = {r[0]: r[1] for r in rt.db.all("SELECT purpose, COUNT(*) FROM model_usage GROUP BY purpose")}
    out["profile"] = prof.top(15)
    return out


# ------------------------------------------------------------------------------------------------ phase 4: discovery
class Instr:
    """Wraps engine.handle (per job kind) and the fake provider (per task) to count calls and time."""

    def __init__(self, rt) -> None:
        self.by_kind: dict[str, list[float]] = defaultdict(list)
        self.model_calls: Counter = Counter()
        eng = rt.engine
        orig = eng.handle

        async def handle(job, _o=orig):
            t0 = time.perf_counter()
            try:
                return await _o(job)
            finally:
                self.by_kind[job.kind].append(time.perf_counter() - t0)
        eng.handle = handle
        prov = rt.router.providers["fake"]
        oc = prov.complete

        async def complete(messages, *, model, **kw):
            from mycelic.models.fake import parse_task_prompt
            user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
            task, _ = parse_task_prompt(user)
            self.model_calls[task or "?"] += 1
            return await oc(messages, model=model, **kw)
        prov.complete = complete
        oq = rt.questions.route
        self.route_t: list[float] = []

        async def route(qid, **kw):
            t0 = time.perf_counter()
            try:
                return await oq(qid, **kw)
            finally:
                self.route_t.append(time.perf_counter() - t0)
        rt.questions.route = route
        ocand = rt.questions.candidate_holders
        self.cand_t: list[float] = []

        def cand(q, **kw):
            t0 = time.perf_counter()
            try:
                return ocand(q, **kw)
            finally:
                self.cand_t.append(time.perf_counter() - t0)
        rt.questions.candidate_holders = cand
        od = eng.domains_for_goal
        self.dom_t: list[float] = []

        def dom(goal, _o=od):
            t0 = time.perf_counter()
            try:
                return _o(goal)
            finally:
                self.dom_t.append(time.perf_counter() - t0)
        eng.domains_for_goal = dom


async def phase_discovery(rt, ctx: dict, prof: Prof, max_wall: float, max_rounds: int = 12) -> dict[str, Any]:
    from mycelic.util import parse_iso, utcnow
    ins = Instr(rt)
    d: dict[str, Any] = {}
    tid = ctx["tenant_id"]
    admin = rt.authz.principal_for_user(ctx["admin_id"])
    worker = build_worker(rt)
    # coordinator side of the transport: the core-inbound consumer sees every heartbeat / ingest_result the holders published so far
    msgs_before = rt.db.scalar("SELECT COUNT(*) FROM transport_messages")
    t0, c0 = time.perf_counter(), time.process_time()
    await worker.start()                       # also starts the scheduler + 2 worker loops
    # let the core-inbound consumer catch up on the holders' heartbeats / ingest_result batches (event-driven, in-process)
    for _ in range(600):
        lag = rt.db.scalar("SELECT COALESCE(MAX(id),0) FROM transport_messages") - rt.db.scalar("SELECT last_id FROM transport_cursors WHERE consumer='core-inbound'", default=0)
        if lag <= 0:
            break
        await asyncio.sleep(0.1)
    d["core_inbound_catchup_s"] = round(time.perf_counter() - t0, 3)
    d["core_inbound_catchup_cpu_s"] = round(time.process_time() - c0, 3)
    d["transport_messages_at_start"] = msgs_before
    goal = await rt.goals.create_goal(admin, {"title": "Find recurring operational blockers", "objective": "Identify recurring operational blockers across all teams, verify causes",
                                              "owner_type": "unit", "owner_id": ctx["root"], "success_criteria": [{"metric": "resolution_time_hours", "target": 24, "direction": "decrease"}],
                                              "baseline": {"resolution_time_hours": 52}, "budget": {"tokens": 5_000_000, "usd": 100, "questions": 500, "followup_depth": 1}}, activate=True)
    d["goal_id"] = goal["goal_id"]
    t_start, c_start = time.perf_counter(), time.process_time()
    rounds = []

    async def wait_quiet(limit: float) -> bool:
        quiet, t_w = 0, time.perf_counter()
        while time.perf_counter() - t_w < limit:
            await asyncio.sleep(0.2)
            counts = rt.jobs.counts()
            if not counts.get("queued") and not counts.get("leased") and rt.jobs.next_available_at() is None:
                quiet += 1
                if quiet >= 5:
                    return True
            else:
                quiet = 0
        return False

    with prof:
        # the production shape: the DiscoveryWorker (2 loops + scheduler + core-inbound consumer) runs next to the N holders' consumers.
        # Round 0 is the loop as activated; every later round is the scheduled check coming due (enqueue_tick), until a tick asks nothing new.
        for r in range(max_rounds):
            nq0 = rt.db.scalar("SELECT COUNT(*) FROM questions WHERE goal_id=?", (goal["goal_id"],), default=0)
            tr, cr = time.perf_counter(), time.process_time()
            if r > 0:
                await rt.goals.enqueue_tick(goal["goal_id"], reason="bench: scheduled check", priority=5)
            ok = await wait_quiet(max(1.0, max_wall - (time.perf_counter() - t_start)))
            nq1 = rt.db.scalar("SELECT COUNT(*) FROM questions WHERE goal_id=?", (goal["goal_id"],), default=0)
            rounds.append({"round": r, "wall_s": round(time.perf_counter() - tr, 3), "cpu_s": round(time.process_time() - cr, 3), "new_questions": nq1 - nq0, "quiet": ok})
            if not ok or (r > 0 and nq1 == nq0):
                break
    d["rounds"] = rounds
    await worker.stop()
    d["wall_s"] = round(time.perf_counter() - t_start, 3)
    d["cpu_s"] = round(time.process_time() - c_start, 3)
    d["finished"] = not rt.jobs.counts().get("queued") and not rt.jobs.counts().get("leased")
    d["jobs_counts"] = dict(rt.jobs.counts())
    d["job_errors"] = [r["last_error"][:200] for r in rt.db.all("SELECT last_error FROM jobs WHERE last_error IS NOT NULL AND last_error NOT LIKE 'lease expired%' LIMIT 5")]
    d["jobs_by_kind"] = {k: dist(v) for k, v in sorted(ins.by_kind.items())}
    tick = ins.by_kind.get("loop.tick", [])
    d["ticks"] = len(tick)
    d["tick_time"] = dist(tick)
    d["model_calls_total"] = sum(ins.model_calls.values())
    d["model_calls_by_task"] = dict(ins.model_calls)
    d["model_usage_rows_by_purpose"] = {r[0]: r[1] for r in rt.db.all("SELECT purpose, COUNT(*) FROM model_usage GROUP BY purpose")}
    qs = rt.db.all("SELECT question_id, status, candidate_domains, depth, kind FROM questions WHERE goal_id=?", (goal["goal_id"],))
    d["questions"] = len(qs)
    d["questions_by_status"] = dict(Counter(q["status"] for q in qs))
    d["questions_by_kind_depth"] = dict(Counter(f"{q['kind']}/d{q['depth']}" for q in qs))
    routes = rt.db.all("SELECT question_id, status FROM question_routes WHERE tenant_id=?", (tid,))
    per_q = Counter(r["question_id"] for r in routes)
    d["routes_total"] = len(routes)
    d["routes_by_status"] = dict(Counter(r["status"] for r in routes))
    d["routes_per_question"] = {"min": min(per_q.values(), default=0), "max": max(per_q.values(), default=0), "mean": round(statistics.fmean(per_q.values()), 2) if per_q else 0}
    d["responses_by_status"] = {r[0]: r[1] for r in rt.db.all("SELECT status, COUNT(*) FROM responses WHERE tenant_id=? GROUP BY status", (tid,))}
    d["claims_by_status"] = {r[0]: r[1] for r in rt.db.all("SELECT status, COUNT(*) FROM claims WHERE goal_id=? GROUP BY status", (goal["goal_id"],))}
    d["discoveries"] = rt.db.scalar("SELECT COUNT(*) FROM discoveries WHERE goal_id=?", (goal["goal_id"],), default=0)
    d["route_call"] = dist(ins.route_t)
    d["candidate_holders_call"] = dist(ins.cand_t)
    d["domains_for_goal_call"] = dist(ins.dom_t)
    d["profile"] = prof.top(15)
    # micro-measurements of the O(N) pieces, taken after the pass on the same data
    q0 = rt.questions.get(qs[0]["question_id"]) if qs else None
    if q0:
        n_h = len(rt.org.list_holders(tid))
        a = time.perf_counter(); hs = rt.org.list_holders(tid); d["micro_list_holders_ms"] = round(1000 * (time.perf_counter() - a), 3)
        a = time.perf_counter()
        for hh in hs:
            rt.authz.can_route(q0, hh)
        d["micro_can_route_all_holders_ms"] = round(1000 * (time.perf_counter() - a), 3)
        d["micro_can_route_per_holder_us"] = round(1e6 * (time.perf_counter() - a) / max(1, n_h), 1)
        rt.questions._audience_cache.clear()
        a = time.perf_counter(); aud = rt.questions.question_audience(q0); d["micro_question_audience_uncached_ms"] = round(1000 * (time.perf_counter() - a), 3)
        d["audience_size"] = len(aud["principal_ids"])
        a = time.perf_counter(); rt.questions.question_audience(q0); d["micro_question_audience_cached_ms"] = round(1000 * (time.perf_counter() - a), 3)
    return d


# ------------------------------------------------------------------------------------------------ main
async def run(args) -> dict[str, Any]:
    base = Path(args.workdir or tempfile.mkdtemp(prefix=f"mycelic_perf_n{args.n}_", dir=os.environ.get("PERF_TMP", "/tmp")))
    base.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"n": args.n, "agree": AGREE, "machine": machine(), "workdir": str(base), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "profile_mode": args.profile}
    out["rss_start_mb"] = rss_mb()
    settings = bench_settings(base)
    t0 = time.perf_counter()
    rt = build_runtime(settings)
    out["build_runtime_s"] = round(time.perf_counter() - t0, 3)
    out["embedder"] = {"runtime_embedder": type(rt.embedder).__name__, "dim": getattr(rt.embedder, "dim", None), "model": getattr(rt.embedder, "model", None),
                       "router_providers": list(rt.router.providers), "tiers": rt.router.tiers}
    ctx = await phase_tenant(rt, args.n)
    out["tenant"] = ctx["timing"]
    out["rss_after_tenant_mb"] = rss_mb()
    out["open"] = await phase_open(rt, ctx["users"], base)
    if "ingest" in args.phases:
        out["ingest"] = await phase_ingest(rt, ctx["users"], base, args.records_per_user, Prof(args.profile == "ingest"))
    if "discovery" in args.phases:
        out["discovery"] = await phase_discovery(rt, ctx, Prof(args.profile == "discovery"), args.max_wall, args.max_rounds)
    out["peak_rss_mb"] = peak_rss_mb()
    out["rss_end_mb"] = rss_mb()
    out["fds_end"] = n_fds()
    out["total_disk_bytes"] = {"coord_db_files": dir_bytes(base, "coord.db*"), "holders": dir_bytes(Path(settings.holders_dir))}
    t0 = time.perf_counter()
    await rt.stop()
    out["shutdown_s"] = round(time.perf_counter() - t0, 3)
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--records-per-user", type=int, default=5)
    ap.add_argument("--phases", default="ingest,discovery")
    ap.add_argument("--profile", choices=["none", "ingest", "discovery"], default="none")
    ap.add_argument("--max-wall", type=float, default=900.0)
    ap.add_argument("--workdir", default="")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--disagree", action="store_true", help="users report different figures (contested claims, contradiction follow-ups)")
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    global AGREE
    AGREE = not args.disagree
    res = asyncio.run(run(args))
    txt = json.dumps(res, indent=1, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()

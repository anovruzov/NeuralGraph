"""Run driver: build a world, write raw sources, feed them through the connector API, issue the 120 tasks, drive the worker, and
write the asker-view dump of every task (PLAN_v1 §D WP2).

    python research/mycelic_e2e/bench/run.py --size S --seed 1 --split dev --mode system --out DIR [--wave 15] [--task-timeout 180]

``--mode system`` is the only mode WP2 implements (the central baseline is WP3's ``baseline_central.py``). The system path here
never reads gold: gold is created by the planner, handed to the sealed sink right after materialization and dropped; feed/issue
only see ``tasks_<split>.public.json`` and the sources manifest. ``--split holdout`` loads the sealed bank (importlib, here only).
The harness talks to the system through the real HTTP API on a loopback port (aiohttp), with Bearer sessions created by
``AuthService.create_session`` for the people who act (the same call a login makes). It never calls ``EvidenceStore`` /
``HolderService`` / ``KnowledgeService`` and never ``/respond``. Lifecycle-only calls on ``EmbeddedHolders`` (open all holders after
materialization; stop+open one holder for the restart fault) are the in-process equivalent of starting/restarting a holder process.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

from aiohttp import web  # noqa: E402

from research.mycelic_e2e.bench import ablations, events, feed, issue, ledger, world as W  # noqa: E402
from research.mycelic_e2e.bench.feed import ApiClient  # noqa: E402
from research.mycelic_e2e.bench.gold import GoldSink, gold_path  # noqa: E402
from research.mycelic_e2e.bench.schema import SIZES, SPLITS  # noqa: E402

PROVIDER_LABEL = "deterministic-provider"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def bench_settings(base: Path, *, question_timeout: float = 60.0, max_open_holders: int = 0):
    from mycelic.config import Settings
    s = Settings()
    s.data_dir = base
    s.coord_db = str(base / "coord.db")
    s.holders_dir = str(base / "holders")
    s.host, s.port, s.public_url = "127.0.0.1", 0, ""
    s.cors_origins, s.allowed_hosts, s.secure_cookies = [], [], False
    s.secret_key = secrets.token_urlsafe(32)
    s.demo_mode, s.embedded_holders, s.run_worker_in_api = False, True, False
    s.transport = "sqlite"
    s.model_light, s.model_standard, s.model_heavy = "fake:mycelic-fake-light", "fake:mycelic-fake-standard", "fake:mycelic-fake-heavy"
    s.embed_provider = "hash"
    s.loop_question_timeout_seconds = question_timeout
    s.loop_max_concurrent_questions = 3
    s.loop_cooldown_seconds = 900
    s.loop_max_followup_depth = 2
    s.worker_concurrency = 3
    s.worker_lease_seconds = 120.0
    s.worker_poll_seconds = 0.2
    s.log_json = False
    s.service_name = "bench"
    # production bound on open embedded holders (MYCELIC_MAX_OPEN_HOLDERS): the rest stay dormant and wake on demand. The harness does not
    # emulate it: it sets the settings the deployment would, with MYCELIC_DORMANT_POLL=0 (no scheduled-poll reopen), before the runtime exists
    if max_open_holders > 0:
        s.max_open_holders = int(max_open_holders)
        s.dormant_poll = False
    return s


def rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)
    return -1.0


async def wait_quiet(rt, limit: float, settle: int = 5, collect_horizon: float = 75.0) -> bool:
    """No job leased, no job due within 2 s, and no question.collect-style job due within ``collect_horizon`` (a collect job waits
    for the question timeout unless every holder answered). Future ticks (the loop's own 5-minute schedule) and 15-minute budget
    deferrals are ignored."""
    t0, quiet = time.time(), 0
    while time.time() - t0 < limit:
        await asyncio.sleep(0.25)
        busy = rt.jobs.counts().get("leased", 0) > 0
        if not busy:
            now = datetime.now(timezone.utc)
            near, far = (now + timedelta(seconds=2)).isoformat(), (now + timedelta(seconds=collect_horizon)).isoformat()
            for j in rt.jobs.list(status="queued", limit=500):
                due = str(j["available_at"])
                if due <= near or (j["kind"] == "question.collect" and due <= far):
                    busy = True
                    break
        quiet = 0 if busy else quiet + 1
        if quiet >= settle:
            return True
    return False


async def amain(a: argparse.Namespace) -> int:
    from mycelic.runtime import build_runtime, build_worker
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    run_id = "run_" + uuid.uuid4().hex[:12]
    t_start = time.perf_counter()
    bank, goal_obs = W.load_bank(a.split)                    # the holdout bank is imported only for --split holdout
    world = W.generate(a.seed, a.size, bank)
    plan = W.plan(world, bank, a.split, a.seed, goal_obs)
    (out / "world.json").write_text(json.dumps({"seed": a.seed, "size": a.size, "bank": bank.name, "counts": world.counts(), "sha256": world.fingerprint(),
                                                "tenants": [{"slug": t.slug, "users": len(t.users), "units": len(t.units), "holders": len(t.holders)} for t in world.tenants]},
                                               indent=1))
    log(f"world {a.size} seed {a.seed} split {a.split}: {world.counts()}  tasks={len(plan.tasks)} records={len(plan.records)}")

    # a harness-only ablation replaces one mechanism in the imported modules BEFORE the runtime is built (never a production setting)
    if a.lazy_holders and not a.max_open_holders:
        a.max_open_holders = 200                         # --lazy-holders is an alias for the production bound (no harness-level laziness remains)
        log("--lazy-holders is an alias for --max-open-holders 200")
    abl_short, abl_name = ablations.normalize(a.ablation)
    abl_patch = ablations.apply(abl_short) if abl_short and not a.generate_only else None
    if abl_patch is not None:
        log(f"ablation {abl_name}: {len(abl_patch.bound)} binding(s) replaced")
    settings = bench_settings(out / "data", question_timeout=a.question_timeout, max_open_holders=a.max_open_holders)
    rt = build_runtime(settings)
    hb = a.heartbeat
    if rt.holders is not None:
        rt.holders.heartbeat_interval = hb
    from mycelic.api.app import create_app
    app = create_app(rt, settings, run_worker=False, run_holders=True, allowed_hosts=None, cors_origins=[], dist_dir=out / "no-dist")     # holders start the way the API starts them
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    client = ApiClient(f"http://127.0.0.1:{port}")
    timings: dict[str, float] = {}
    try:
        # ---- materialize (real services only)
        t0 = time.perf_counter()
        ids = await W.materialize(rt, world, log=log)
        timings["materialize_s"] = round(time.perf_counter() - t0, 2)
        log(f"materialized in {timings['materialize_s']}s (rss peak {rss_mb()} MB)")

        (out / "ids.json").write_text(json.dumps({"tenants": ids.tenants, "units": ids.units, "users": ids.users, "holders": ids.holders}))
        # ---- tasks: public file, gold through the sealed sink, then the gold objects are dropped
        public = [t.to_public_dict() for t in plan.public()]
        pub_path = out / f"tasks_{a.split}.public.json"
        pub_path.write_text(json.dumps({"split": a.split, "entities": plan.entities, "tasks": public}, indent=1, sort_keys=True))
        tasks_sha = hashlib.sha256(pub_path.read_bytes()).hexdigest()
        sink = GoldSink(gold_path(out, a.split))
        gold = plan.gold(ids)
        for g in gold:
            sink.write(g.task_id, g)
        n_gold = len(gold)
        del gold, sink
        records, fault_plan = plan.records, plan.fault_plan
        asker_scope = {t["task_id"]: t["scope_unit"] for t in public}
        del plan

        # ---- raw sources
        now = datetime.fromisoformat(a.anchor) if a.anchor else datetime.now(timezone.utc).replace(microsecond=0)
        manifest = events.write_sources(world, records, fault_plan, ids, out, now=now, seed=a.seed)
        (out / "sources_manifest.json").write_text(json.dumps(manifest, indent=1))
        log(f"wrote {len(manifest)} source files, {sum(m['n_base'] + m['n_late'] for m in manifest)} raw lines")
        n_records, n_late = len(records), sum(1 for r in records if r.phase == "late")
        (out / "fault_plan.json").write_text(json.dumps(events.fault_evidence(records, fault_plan, ids), indent=1))
        del records
        h = hashlib.sha256()
        for f in sorted((out / "sources").glob("*.jsonl")):
            h.update(f.name.encode() + b"\0" + hashlib.sha256(f.read_bytes()).digest())
        sources_sha = h.hexdigest()
        if a.generate_only:
            gen = {"split": a.split, "size": a.size, "seed": a.seed, "tasks_sha256": tasks_sha, "sources_sha256": sources_sha, "world_sha256": world.fingerprint(),
                   "world_counts": world.counts(), "source_files": len(manifest), "records_planned": n_records, "n_tasks": len(public)}
            (out / "generate_manifest.json").write_text(json.dumps(gen, indent=1))
            log(f"generate-only: {gen}")
            return 0

        # ---- sessions for the people who act (the same call a login makes)
        tokens: dict[str, str] = {}
        key_tenant = {u.key: (t.slug, ids.tenants[t.slug]) for t in world.tenants for u in t.users.values()}

        async def token_for(key: str) -> str:
            if key not in tokens:
                tokens[key] = await rt.auth.create_session(ids.users[key], key_tenant[key][1], kind="api")
            return tokens[key]
        needed = {m["manager"] for m in manifest} | {t["asker_key"] for t in public}
        for k in sorted(needed):
            await token_for(k)
        principal_map = {k: v for k, v in ids.users.items()}

        # ---- holders: started the way the API starts them (EmbeddedHolders.start ran at app startup; reconcile() is its periodic pass, run
        # once now that the registry is populated). Unbounded: every holder opens. With a bound: they stay dormant and wake on demand.
        t0 = time.perf_counter()
        if rt.holders is not None:
            await rt.holders.reconcile()
        timings["open_holders_s"] = round(time.perf_counter() - t0, 2)
        hs = rt.holders.stats() if rt.holders else {}
        log(f"holders: {hs.get('open')} open of {hs.get('registered')} registered, max_open={hs.get('max_open')} bounded={hs.get('bounded')} "
            f"({timings['open_holders_s']}s, rss peak {rss_mb()} MB)")
        mgr_id = {m["holder_id"]: ids.users[m["manager"]] for m in manifest}

        async def restart_hook(holder_id: str, connector_id: str) -> None:
            from mycelic.api.routes_integrations import holder_call
            h = rt.org.get_holder(holder_id)
            await holder_call(rt, h, "connector.sync", {"connector_id": connector_id, "mode": "incremental", "max_pages": 1}, actor=mgr_id[holder_id])
            await rt.holders.remove(holder_id)                  # the holder process dies after its first page ...
            await rt.holders.ensure(holder_id)                  # ... and comes back; the next sync resumes from the durable cursor

        tok_cache = tokens
        t0 = time.perf_counter()
        sums = await feed.feed_all(client, manifest, token_for=lambda k: tok_cache[k], import_dir_for=lambda hid: Path(settings.holders_dir) / hid / "imports",
                                   src_dir=out / "sources", principal_map=principal_map, restart_hook=restart_hook, concurrency=a.feed_concurrency, log=log)
        timings["feed_s"] = round(time.perf_counter() - t0, 2)
        feed_errors = [e for s in sums for e in s.errors]
        tot = {k: sum(s.totals()[k] for s in sums) for k in ("raw_items", "enqueued", "duplicates", "normalize_errors", "processed")}
        log(f"fed {len(sums)} sources in {timings['feed_s']}s: {tot}; errors={len(feed_errors)} {feed_errors[:2]}")
        (out / "feed_summary.json").write_text(json.dumps({"totals": tot, "errors": feed_errors, "sources": [
            {"file": s.file, "holder_key": s.holder_key, "connector_id": s.connector_id, "totals": s.totals(), "wall_s": s.wall_s} for s in sums]}, indent=1))

        # ---- wait for the holders' own heartbeats to publish their routable domains (never set by hand)
        t0 = time.perf_counter()
        want = [m["holder_id"] for m in manifest if m["domain"] and m["visibility"] == "public" and m["n_base"] >= 5]
        deadline = time.time() + max(30.0, 6 * hb)
        pending = set(want)
        while pending and time.time() < deadline:
            pending = {h for h in pending if not (rt.org.get_holder(h) or {}).get("published_domains")}
            if pending:
                await asyncio.sleep(0.5)
        timings["domain_publication_s"] = round(time.perf_counter() - t0, 2)
        log(f"routable domains published by {len(want) - len(pending)}/{len(want)} holders ({timings['domain_publication_s']}s)")
        # two consecutive stable heartbeats: what the holders publish (domains, record counts, entity and term maps, queue, snapshot flag)
        # is unchanged across two successive intervals, so routing sees the final index
        t0 = time.perf_counter()
        stable, prev, deadline = 0, None, time.time() + max(60.0, 20 * hb)
        while time.time() < deadline:
            snap = {}
            for hid in sorted({m["holder_id"] for m in manifest}):
                row = rt.org.get_holder(hid) or {}
                ing = (row.get("stats") or {}).get("ingest") or {}
                snap[hid] = (tuple(row.get("published_domains") or ()), ing.get("records"), json.dumps(ing.get("queue"), sort_keys=True),
                             hashlib.sha256(json.dumps([ing.get("entities"), ing.get("terms")], sort_keys=True, default=str).encode()).hexdigest()[:12], ing.get("snapshot_complete"))
            stable = stable + 1 if snap == prev else 0
            prev = snap
            if stable >= 2:
                break
            await asyncio.sleep(max(1.0, hb))
        timings["heartbeat_stable_s"] = round(time.perf_counter() - t0, 2)
        log(f"heartbeats stable: {stable >= 2} after {timings['heartbeat_stable_s']}s")

        # ---- worker + tasks
        rt.worker = build_worker(rt)
        await rt.worker.start()
        views_dir = out / "views"
        views_dir.mkdir(exist_ok=True)
        t_issue0 = time.perf_counter()
        issued: dict[str, issue.Issued] = {}
        view_summary: dict[str, dict] = {}
        ticks_extra = max(0, a.n_ticks - 1)
        for w0 in range(0, len(public), a.wave):
            wave = public[w0:w0 + a.wave]
            sem = asyncio.Semaphore(8)

            async def one(t):
                async with sem:
                    return await issue.issue_task(client, t, token=tokens[t["asker_key"]], scope_unit_id=ids.units[t["scope_unit"]], now=now)
            res = await asyncio.gather(*[one(t) for t in wave])
            for r in res:
                issued[r.task_id] = r
            rt.worker.wake()
            deadline = time.time() + a.task_timeout
            await wait_quiet(rt, a.task_timeout)
            # question tasks that are still unresolved get the rest of their time budget
            while time.time() < deadline:
                states = [await issue.question_status(client, issued[t["task_id"]].question_id, tokens[t["asker_key"]])
                          for t in wave if t.get("question_text") and issued[t["task_id"]].question_id]
                if all(s in issue.RESOLVED for s in states):
                    break
                await asyncio.sleep(1.0)
            for _ in range(ticks_extra):                                   # goal-only tasks: N_ticks worker drains
                for t in wave:
                    if t.get("goal_only") and issued[t["task_id"]].goal_id:
                        await issue.run_loop_now(client, issued[t["task_id"]].goal_id, tokens[t["asker_key"]])
                rt.worker.wake()
                await wait_quiet(rt, min(60.0, a.task_timeout))
            for t in wave:
                iss = issued[t["task_id"]]
                v = await issue.collect_view(client, t, iss, tokens[t["asker_key"]], tenant_id=ids.tenants[t["tenant"]], timed_out=False)
                (views_dir / f"{t['task_id']}.json").write_text(json.dumps(v, indent=1, default=str))
                view_summary[t["task_id"]] = {"status": v["status"], "q": (v.get("question") or {}).get("status"),
                                              "supported": sum(1 for c in v["claims"] if ((c.get("claim") or c).get("status") == "supported"))}
            log(f"wave {w0 // a.wave + 1}/{-(-len(public) // a.wave)} done  ({time.perf_counter() - t_issue0:.0f}s)")
        timings["tasks_s"] = round(time.perf_counter() - t_issue0, 2)

        # ---- manifest
        cnt = lambda sql: rt.db.scalar(sql, default=0)  # noqa: E731
        routed = rt.db.all("SELECT DISTINCT holder_id FROM question_routes")
        answered = rt.db.all("SELECT DISTINCT holder_id FROM responses WHERE status='answered'")
        usage = rt.db.all("SELECT COUNT(*) AS n, COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o, GROUP_CONCAT(DISTINCT provider) AS p FROM model_usage")[0]
        fake_py = ROOT / "mycelic" / "models" / "fake.py"
        statuses: dict[str, int] = {}
        for v in view_summary.values():
            statuses[str(v["q"])] = statuses.get(str(v["q"]), 0) + 1
        run_manifest = {
            "run_id": run_id, "ledger_run_id": getattr(a, "ledger_run_id", None), "split": a.split, "mode": a.mode, "ablation": abl_short, "ablation_name": abl_name,
            "ablation_patches": abl_patch.describe()["patched"] if abl_patch else [], "seed": a.seed, "size": a.size, "provider_label": PROVIDER_LABEL,
            "provider": "fake (deterministic)", "fake_py_sha256": hashlib.sha256(fake_py.read_bytes()).hexdigest()[:16], "tasks_sha256": tasks_sha, "sources_sha256": sources_sha,
            "n_tasks": len(public), "n_gold_written": n_gold, "world_sha256": world.fingerprint(), "world_counts": world.counts(), "transport": "sqlite",
            "in_process_server": True, "max_open_holders": a.max_open_holders, "holders_stats": rt.holders.stats() if rt.holders else None, "heartbeat_stable": stable >= 2,
            "raw_checks_enabled": True,
            "counts": {"created": len(ids.holders), "with_records": len({m["holder_id"] for m in manifest}), "activated": len(answered), "routed": len(routed),
                       "opened": len(rt.holders.holder_ids()) if rt.holders else 0},
            "model_usage": {"calls": usage["n"], "input_tokens": usage["i"], "output_tokens": usage["o"], "providers": usage["p"]},
            "records": {"planned": n_records, "late_phase": n_late, "source_files": len(manifest), **tot},
            "question_statuses": statuses, "timings_s": {**timings, "total_s": round(time.perf_counter() - t_start, 2)},
            "peak_rss_mb": rss_mb(), "heartbeat_s": hb, "wave": a.wave, "task_timeout_s": a.task_timeout, "n_ticks": a.n_ticks,
            "events_range": [cnt("SELECT MIN(id) FROM events"), cnt("SELECT MAX(id) FROM events")],
            "container": {"platform": platform.platform(), "cpus": os.cpu_count(), "python": platform.python_version()},
            "correlation": {"tenants": ids.tenants, "tasks": {t: {"goal_id": i.goal_id, "question_id": i.question_id, "issued_at": i.issued_at, "error": i.error,
                                                                "activation_error": i.activation_error} for t, i in issued.items()},
                            "connectors": {s.holder_key + "/" + s.file: s.connector_id for s in sums}},
            "started_utc": now.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
        }
        (out / "run_manifest.json").write_text(json.dumps(run_manifest, indent=1, default=str))
        sup = sum(v["supported"] for v in view_summary.values())
        log(f"done in {run_manifest['timings_s']['total_s']}s: views={len(view_summary)} questions={statuses} supported_claims={sup} "
            f"tasks_with_supported={sum(1 for v in view_summary.values() if v['supported'])} model_calls={usage['n']} peak_rss={rss_mb()}MB")
        return 0
    finally:
        try:
            if rt.worker is not None:
                await rt.worker.stop()
        except Exception:
            pass
        await client.close()
        await runner.cleanup()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mycelic-E2E v1 run driver (WP2)")
    ap.add_argument("--size", choices=SIZES, required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--split", choices=SPLITS, default="dev")
    ap.add_argument("--mode", choices=["system"], default="system")
    ap.add_argument("--out", required=True)
    ap.add_argument("--wave", type=int, default=15, help="tasks issued concurrently")
    ap.add_argument("--task-timeout", type=float, default=180.0, help="seconds a wave may take (T_task)")
    ap.add_argument("--question-timeout", type=float, default=60.0)
    ap.add_argument("--heartbeat", type=float, default=2.0, help="embedded holder heartbeat seconds (publishes routable domains)")
    ap.add_argument("--n-ticks", type=int, default=2, help="loop drains for goal-only tasks")
    ap.add_argument("--feed-concurrency", type=int, default=6)
    ap.add_argument("--max-open-holders", type=int, default=0, help="production bound on open embedded holders (MYCELIC_MAX_OPEN_HOLDERS, dormant poll off); 0 = all open")
    ap.add_argument("--lazy-holders", action="store_true", help="alias for --max-open-holders 200")
    ap.add_argument("--ablation", default="", help="harness-only ablation: A1_ranker_off, A2_roots_off, A3_verification_off, A4_index_off, "
                                                   "A5_authz_routing_off, A6_dedupe_off (or A1..A6); recorded in run_manifest.json")
    ap.add_argument("--anchor", default="", help="ISO timestamp the record ages are measured from (default: now); fix it to get byte-identical sources")
    ap.add_argument("--generate-only", action="store_true", help="materialize, write tasks (public + gold) and sources, hash them, and stop (no feed, no tasks)")
    ap.add_argument("--ledger", action="store_true", help="also write ledger rows for a dev run (a holdout run always does, and is refused by the holdout guard)")
    a = ap.parse_args(argv)
    # the holdout guard runs BEFORE anything is generated or written: frozen bank hash, clean repository, snapshot rev == HEAD, one run per candidate
    a.ledger_run_id = None
    if a.split == "holdout" or a.ledger:
        cfg = {"split": a.split, "mode": a.mode, "ablation": ablations.normalize(a.ablation)[0], "seed": a.seed, "size": a.size, "provider_label": PROVIDER_LABEL,
               "transport": "sqlite", "inprocess": True, "run_dir": str(Path(a.out).resolve()), "generate_only": a.generate_only}
        try:
            a.ledger_run_id = ledger.start_run(cfg)
        except ledger.HoldoutRefused as exc:
            print(f"HOLDOUT REFUSED: {exc}", file=sys.stderr)
            return 3
    try:
        rc = asyncio.run(amain(a))
    except BaseException as exc:
        if a.ledger_run_id:
            ledger.abort_run(a.ledger_run_id, f"{type(exc).__name__}: {exc}")
        raise
    if a.ledger_run_id:
        if rc == 0:
            ledger.mark_completed(a.ledger_run_id, {"out": str(a.out)})
        else:
            ledger.abort_run(a.ledger_run_id, f"run.py returned {rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

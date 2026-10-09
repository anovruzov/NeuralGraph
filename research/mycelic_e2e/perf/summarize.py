"""Print markdown tables from results/n*.json (and top-15 profile tables from results/prof_*.json) for PERF_PROBE.md."""
from __future__ import annotations

import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parent / "results"


def load(n):
    p = R / f"n{n}.json"
    return json.loads(p.read_text()) if p.exists() else None


def main() -> None:
    ns = [n for n in (50, 200, 1000, 2000) if load(n)]
    ds = {n: load(n) for n in ns}
    print("| measure | " + " | ".join(f"N={n}" for n in ns) + " |")
    print("|---|" + "---|" * len(ns))

    def row(name, fn):
        vals = []
        for n in ns:
            try:
                vals.append(str(fn(ds[n])))
            except Exception:
                vals.append("-")
        print(f"| {name} | " + " | ".join(vals) + " |")
    row("tenant+units+users+memberships+holders, total s", lambda d: d["tenant"]["tenant_total_s"])
    row("per user (user+membership+holder rows), ms", lambda d: d["tenant"]["per_user_total_ms"])
    row("coord.db bytes after tenant", lambda d: d["tenant"]["coord_db_bytes"])
    row("open all N holders, s", lambda d: d["open"]["open_all_s"])
    row("open one holder mean / p95 ms", lambda d: f'{d["open"]["open_each"]["mean_ms"]} / {d["open"]["open_each"]["p95_ms"]}')
    row("first10 / last10 open mean ms", lambda d: f'{d["open"]["first10_mean_ms"]} / {d["open"]["last10_mean_ms"]}')
    row("close one holder mean ms (n=20)", lambda d: d["open"]["close_one"]["mean_ms"])
    row("reopen one holder mean ms (n=20)", lambda d: d["open"]["reopen_one"]["mean_ms"])
    row("RSS before -> after open, MB", lambda d: f'{d["open"]["rss_before_mb"]} -> {d["open"]["rss_after_mb"]}')
    row("RSS per open holder, MB", lambda d: d["open"]["rss_growth_mb_per_open_holder"])
    row("fds per open holder", lambda d: d["open"]["fds_per_holder"])
    row("disk per empty holder, bytes (db / -wal / -shm)", lambda d: "{} ({} / {} / {})".format(d["open"]["disk_per_holder_empty_bytes"], *[d["open"]["disk_per_holder_by_file_bytes"].get(k) for k in (".db", ".db-wal", ".db-shm")]) if ".db-wal" in d["open"]["disk_per_holder_by_file_bytes"] else d["open"]["disk_per_holder_empty_bytes"])
    row("idle CPU of N open holders (fraction of 1 core)", lambda d: d["open"]["idle_cpu_fraction_of_one_core"])
    row("no-op reconcile() pass, s", lambda d: d["open"]["reconcile_noop_s"])
    row("ingest: records", lambda d: d["ingest"]["records_processed"])
    row("ingest: wall s", lambda d: d["ingest"]["wall_s"])
    row("ingest: aggregate records/s", lambda d: d["ingest"]["aggregate_records_per_s"])
    row("ingest: per holder (5 rec) mean ms", lambda d: d["ingest"]["per_holder_total"]["mean_ms"])
    row("ingest: per holder p95 ms", lambda d: d["ingest"]["per_holder_total"]["p95_ms"])
    row("ingest: per-holder records/s (mean)", lambda d: d["ingest"]["per_holder_records_per_s_mean"])
    row("ingest: model calls", lambda d: sum(d["ingest"]["model_usage_by_purpose"].values()))
    row("ingest: disk/holder incl WAL, bytes", lambda d: d["ingest"]["disk_per_holder_after_bytes"])
    row("ingest: db-only/holder after checkpoint (sample), bytes", lambda d: d["ingest"]["disk_sampled_holders_after_checkpoint_db_bytes"])
    row("transport msgs after ingest (heartbeat+ingest_result)", lambda d: d["ingest"]["transport_messages"])
    row("core-inbound catch-up s (cpu s)", lambda d: f'{d["discovery"]["core_inbound_catchup_s"]} ({d["discovery"]["core_inbound_catchup_cpu_s"]})')
    row("discovery: rounds (wall s each)", lambda d: ", ".join(f'{r["wall_s"]}' for r in d["discovery"]["rounds"]))
    row("discovery: total wall s / cpu s", lambda d: f'{d["discovery"]["wall_s"]} / {d["discovery"]["cpu_s"]}')
    row("discovery: finished", lambda d: d["discovery"]["finished"])
    row("discovery: questions (by kind/depth)", lambda d: f'{d["discovery"]["questions"]} {d["discovery"]["questions_by_kind_depth"]}')
    row("discovery: ticks, mean tick ms", lambda d: f'{d["discovery"]["ticks"]}, {d["discovery"]["tick_time"]["mean_ms"]}')
    row("discovery: routes total (per question)", lambda d: f'{d["discovery"]["routes_total"]} ({d["discovery"]["routes_per_question"]["mean"]})')
    row("discovery: model calls total", lambda d: d["discovery"]["model_calls_total"])
    row("discovery: model calls by task", lambda d: d["discovery"]["model_calls_by_task"])
    row("question.route job mean ms", lambda d: d["discovery"]["jobs_by_kind"]["question.route"]["mean_ms"])
    row("  of which candidate_holders mean ms", lambda d: d["discovery"]["candidate_holders_call"]["mean_ms"])
    row("domains_for_goal (per tick) mean ms", lambda d: d["discovery"]["domains_for_goal_call"]["mean_ms"])
    row("loop.tick mean ms", lambda d: d["discovery"]["jobs_by_kind"]["loop.tick"]["mean_ms"])
    row("question.evaluate / commit mean ms", lambda d: f'{d["discovery"]["jobs_by_kind"]["question.evaluate"]["mean_ms"]} / {d["discovery"]["jobs_by_kind"]["question.commit"]["mean_ms"]}')
    row("micro: list_holders ms", lambda d: d["discovery"]["micro_list_holders_ms"])
    row("micro: can_route over all holders ms (us/holder)", lambda d: f'{d["discovery"]["micro_can_route_all_holders_ms"]} ({d["discovery"]["micro_can_route_per_holder_us"]})')
    row("micro: question_audience uncached ms", lambda d: d["discovery"]["micro_question_audience_uncached_ms"])
    row("peak RSS MB (whole run)", lambda d: d["peak_rss_mb"])
    row("shutdown s", lambda d: d["shutdown_s"])
    row("job errors", lambda d: d["discovery"]["job_errors"])
    for f in sorted(R.glob("prof_*.json")):
        d = json.loads(f.read_text())
        ph = d["profile_mode"]
        prof = (d.get("ingest") if ph == "ingest" else d.get("discovery") or {}).get("profile")
        if not prof:
            continue
        print(f"\n### cProfile top 15, phase `{ph}`, N={d['n']} (file {f.name}; profiled total {prof['total_s']} s)\n")
        for key, title in (("tottime", "by own time"), ("cumulative", "by cumulative time")):
            print(f"\n{title}\n\n| where | function | calls | own s | cum s |\n|---|---|---|---|---|")
            for r in prof[key]:
                print(f"| {r['where']} | {r['func']} | {r['ncalls']} | {r['tottime_s']} | {r['cumtime_s']} |")


if __name__ == "__main__":
    main()

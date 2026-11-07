"""Summarise verify-overlay mixed runs from raw rows (not from the harness summary's latency fields).

usage: summarise_mixed.py MIXED_DIR  -> prints a table, writes MIXED_DIR/summary.json
"""
import json, math, os, statistics, sys

D = sys.argv[1]


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(1, math.ceil(p / 100 * len(v))) - 1)] if v else float("nan")


runs = {}
for name in sorted(os.listdir(D)):
    p = os.path.join(D, name, "raw_mixed.jsonl")
    if not os.path.exists(p):
        continue
    s = json.load(open(os.path.join(D, name, "mixed_summary.json")))
    reads = [json.loads(l) for l in open(p)]
    reads = [r for r in reads if r.get("phase") == "mixed_read"]
    lat = [r["latency_ms"] for r in reads if r["ok"]]
    w = s["writes"]["by_kind"]
    runs[name] = {
        "reads": len(reads), "read_errors": sum(1 for r in reads if not r["ok"]),
        "violations": sum(len(r["violations"]) for r in reads),
        "read_qps": len(lat) / s["wall_s"], "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
        "p999": pct(lat, 99.9), "max": max(lat) if lat else float("nan"),
        "write_p50": {k: w[k]["latency"]["p50_ms"] for k in w}, "write_p99": {k: w[k]["latency"]["p99_ms"] for k in w},
        "write_errors": sum(w[k]["errors"] for k in w), "writes": s["ops"],
        "new_visible": f'{s["verify"]["new_rows_visible"]}/{s["verify"]["new_rows_total"]}',
        "retired_leaked": f'{s["verify"]["retired_rows_leaked"]}/{s["verify"]["retired_rows_total"]}',
        "peak_rss_mib": s["rss_bytes"]["peak"] / 2**20, "cpu_s": s["cpu_s"], "wall_s": s["wall_s"],
        "disk_before": s["disk_bytes_before"]["total"], "disk_after": s["disk_bytes_after"]["total"],
        "load_before": open(os.path.join(D, name + ".load_before.txt")).read().split("load averages:")[-1].strip(),
    }
json.dump(runs, open(os.path.join(D, "summary.json"), "w"), indent=1)
for n, r in runs.items():
    print(f"{n:34s} qps {r['read_qps']:7.1f} p50 {r['p50']:7.1f} p95 {r['p95']:7.1f} p99 {r['p99']:7.1f} p99.9 {r['p999']:7.1f} max {r['max']:7.1f} | "
          f"err {r['read_errors']}+{r['write_errors']} viol {r['violations']} new {r['new_visible']} leak {r['retired_leaked']} | "
          f"wp99 {max(r['write_p99'].values()):6.1f} | rss {r['peak_rss_mib']:5.0f}MiB cpu {r['cpu_s']:5.0f}s | load {r['load_before']}")
groups = {}
for n, r in runs.items():
    if n.rsplit("_", 1)[-1].startswith("r"):
        groups.setdefault(n.rsplit("_", 1)[0], []).append(r)
print("\nrepeat spread (mean ± sd [min, max]):")
for g, rs in groups.items():
    for k in ("read_qps", "p50", "p99", "max"):
        v = [r[k] for r in rs]
        sd = statistics.stdev(v) if len(v) > 1 else 0
        print(f"  {g:28s} {k:8s} n={len(v)} {statistics.mean(v):8.1f} ± {sd:6.1f} [{min(v):.1f}, {max(v):.1f}]")

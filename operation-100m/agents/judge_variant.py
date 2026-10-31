"""Critic verdict for one ablation run, against the task's round-1 (shipped engine) quality as baseline.

usage: judge_variant.py RUN_DIR DB TASK_RESULTS_DIR N
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from critic import Critic  # noqa: E402

run_dir, db, task_dir, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
task = os.path.basename(task_dir)
st = json.load(open(os.path.join(HERE, "..", "state", f"{task}.json")))
base = next(r for r in st["rounds"] if r["plan"]["experiment"]["variant"] == "engine_default")["critique"]["metrics"]["quality"]
gates = json.load(open(os.path.join(HERE, "..", "bench", "configs", "gates_stage.json")))
tasks = json.load(open(os.path.join(HERE, "..", "bench", "configs", "tasks.json")))
gates.update(tasks[task].get("gates_override", {}))
exp = {"seed": 20261031, "bank": 0, "n": n, "db": db, "manifest": os.path.join(task_dir, "manifest.json"),
       "queries": os.path.join(task_dir, "queries.jsonl"), "run_dir": run_dir, "baseline_quality": base}
c = Critic(exp, gates).run()
json.dump(c, open(os.path.join(run_dir, "critique.json"), "w"), indent=1, default=str)
m, q = c["metrics"], c["metrics"]["quality"]
L = m["latency"]
lv = sorted(L)
lo, hi = L[lv[0]], L[lv[-1]]
print(f"{os.path.basename(run_dir):24s} rec@10 {q['recall@10']:.4f} | c{lv[0]} p50/p99 {lo['p50_ms']:.1f}/{lo['p99_ms']:.1f} ms "
      f"{lo['successful_qps']:.0f} qps | c{lv[-1]} p99 {hi['p99_ms']:.1f} ms {hi['successful_qps']:.0f} qps "
      f"x{hi['successful_qps'] / lo['successful_qps']:.2f} | {m['memory']['resident_bytes_per_memory']:.0f} B/mem | "
      f"{c['verdict']} {[f['code'] for f in c['failures']]}")

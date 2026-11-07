"""Independent check of overlay-index runs against the committed 43937131 reference runs.

For each candidate run: Critic verdict (written under verify-overlay/, never into the candidate's directory), and
top-10 identity for every warm query, matched by (qid, concurrency), against the reference run of the same
configuration. Reference artifacts store memory ids only, so scores and channels cannot be compared from them.

usage: verify_overlay.py OUT_DIR NAME=CANDIDATE_DIR:REFERENCE_DIR[,REFERENCE_DIR...] ...
"""
import gzip, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from critic import Critic  # noqa: E402

ROOT = os.path.dirname(HERE)
RES = os.path.join(ROOT, "bench", "results")


def rows(d):
    p = os.path.join(d, "raw_queries.jsonl")
    fh = open(p) if os.path.exists(p) else gzip.open(p + ".gz", "rt")
    return [json.loads(l) for l in fh]


def tops(d):
    return {(r["qid"], r["concurrency"]): r["top"] for r in rows(d) if r["phase"] == "warm"}


out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)
st = json.load(open(os.path.join(ROOT, "state", "100k-real.json")))
base_q = next(r for r in st["rounds"] if r["plan"]["experiment"]["variant"] == "engine_default")["critique"]["metrics"]["quality"]
gates = json.load(open(os.path.join(ROOT, "bench", "configs", "gates_stage.json")))
report = {}
for spec in sys.argv[2:]:
    name, rest = spec.split("=", 1)
    cand, refs = rest.split(":", 1)
    exp = {"seed": 20261031, "bank": 0, "n": 100000, "db": os.path.join(ROOT, "work", "100k", "bank.db"),
           "manifest": os.path.join(RES, "100k-real", "manifest.json"), "queries": os.path.join(RES, "100k-real", "queries.jsonl"),
           "run_dir": cand, "baseline_quality": base_q}
    c = Critic(exp, gates).run()
    json.dump(c, open(os.path.join(out_dir, f"critic_{name}.json"), "w"), indent=1, default=str)
    ct = tops(cand)
    ident = {}
    for ref in refs.split(","):
        rt = tops(ref)
        common = [k for k in ct if k in rt]
        diff = [k for k in common if ct[k] != rt[k]]
        ident[os.path.relpath(ref, RES)] = {"compared": len(common), "differ": len(diff), "first_differences": diff[:5],
                                            "levels": sorted({k[1] for k in common})}
    m = c["metrics"]
    report[name] = {"candidate": os.path.relpath(cand, RES), "verdict": c["verdict"], "failures": [f["code"] for f in c["failures"]],
                    "recall@10": m["quality"]["recall@10"], "mrr@10": m["quality"]["mrr@10"], "ndcg@10": m["quality"]["ndcg@10"],
                    "latency": {str(k): {x: round(v[x], 3) for x in ("p50_ms", "p95_ms", "p99_ms", "successful_qps")} for k, v in m["latency"].items()},
                    "resident_bytes_per_memory": round(m["memory"]["resident_bytes_per_memory"]), "disk_bytes": m["disk"]["bytes"],
                    "top10_identity": ident}
json.dump(report, open(os.path.join(out_dir, "identity_report.json"), "w"), indent=1)
for k, v in report.items():
    print(k, v["verdict"], v["failures"], f"recall@10={v['recall@10']:.4f}", {r: (i["compared"], i["differ"], i["levels"]) for r, i in v["top10_identity"].items()})

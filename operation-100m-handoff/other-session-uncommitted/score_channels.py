"""Score run directories from raw_queries.jsonl against the generator's ground truth and the bank itself.

recall@10 / hit@10 / MRR@10: ranked ids vs `relevant`.
evidence@1: the top-1 memory is about the queried subject (same subject_name as the relevant row) AND its stored text
states an expected value - i.e. a reader given only the first line would have the right evidence.
Usage: python3 -I score_channels.py QUERIES.jsonl BANK.db RUN_DIR [RUN_DIR ...]
"""
import json, sqlite3, sys


def pct(v, p):
    v = sorted(v)
    import math
    return v[min(len(v) - 1, max(1, math.ceil(p / 100 * len(v))) - 1)]


def main():
    qpath, db, runs = sys.argv[1], sys.argv[2], sys.argv[3:]
    truth = [json.loads(l) for l in open(qpath)]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = {}

    def get(mid):
        if mid not in row:
            r = con.execute("SELECT subject_name, text, status FROM memories WHERE memory_id=?", (mid,)).fetchone()
            row[mid] = r
        return row[mid]

    out = {}
    for run in runs:
        raw = [json.loads(l) for l in open(f"{run}/raw_queries.jsonl")]
        serial = {r["qid"]: r for r in raw if r["phase"] == "warm" and r["concurrency"] == 1}
        assert len(serial) == len(truth), (run, len(serial))
        agg = {"recall": [], "hit": [], "mrr": [], "evidence": []}
        by_type = {}
        for q in truth:
            if not q["relevant"]:
                continue
            top = serial[q["qid"]]["top"][:10]
            rel = set(q["relevant"])
            hits = [m in rel for m in top]
            rr = next((1 / (i + 1) for i, h in enumerate(hits) if h), 0.0)
            subj = get(q["relevant"][0])[0]
            t1 = get(top[0]) if top else None
            ev = 1.0 if t1 and t1[0] == subj and any(v in t1[1] for v in q["expected_values"]) else 0.0
            m = {"recall": sum(hits) / len(rel), "hit": float(any(hits)), "mrr": rr, "evidence": ev}
            for k, v in m.items():
                agg[k].append(v)
                by_type.setdefault(q["type"], {}).setdefault(k, []).append(v)
        lat = [r["latency_ms"] for r in serial.values() if r["ok"]]
        mean = lambda xs: round(sum(xs) / len(xs), 4)  # noqa: E731
        out[run.rstrip("/").split("/")[-1]] = {
            "answerable": len(agg["hit"]),
            "recall@10": mean(agg["recall"]), "hit@10": mean(agg["hit"]), "mrr@10": mean(agg["mrr"]),
            "evidence@1": mean(agg["evidence"]),
            "p50_ms": round(pct(lat, 50), 3), "p95_ms": round(pct(lat, 95), 3), "p99_ms": round(pct(lat, 99), 3),
            "errors": sum(1 for r in serial.values() if not r["ok"]),
            "by_type": {t: {k: mean(v) for k, v in d.items()} | {"n": len(d["hit"])} for t, d in by_type.items()},
        }
    print(json.dumps(out, indent=1))


main()

"""AGENT 03 — CRITIC.

Independently checks a Retriever experiment from its raw artifacts. It never accepts a number from the Retriever's
summaries: it re-derives the corpus and ground truth from the spec (spec.py, a separate implementation from the
Rust generator), reads the bank's SQLite file directly, and recomputes every metric from raw per-query rows.
The Retriever's summary values are only compared against the recomputed ones.

Output: critique.json {verdict: PASS|FAIL, checks: [...], failures: [{code, detail, action_hint}], metrics: {...}}
"""
import gzip
import hashlib
import json
import math
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))
from spec import Corpus  # noqa: E402


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    rank = max(1, math.ceil(p / 100.0 * len(sorted_vals))) - 1
    return sorted_vals[min(rank, len(sorted_vals) - 1)]


def ranking_metrics(top, relevant, k=10):
    rel = set(relevant)
    top = top[:k]
    hits = [1 if m in rel else 0 for m in top]
    recall = sum(hits) / len(rel)
    rr = next((1.0 / (i + 1) for i, x in enumerate(hits) if x), 0.0)
    dcg = sum(x / math.log2(i + 2) for i, x in enumerate(hits))
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return {"recall": recall, "hit": 1.0 if any(hits) else 0.0, "mrr": rr, "ndcg": dcg / idcg}


class Critic:
    def __init__(self, exp, gates):
        self.exp, self.gates = exp, gates
        self.checks, self.failures, self.advisories, self.metrics = [], [], [], {}

    def check(self, name, ok, detail, code=None, hint=None):
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            f = {"code": code or name.upper(), "detail": detail, "action_hint": hint}
            # advisory gates inform the next stage's feasibility; they do not fail this stage
            (self.advisories if f["code"] in self.gates.get("advisory_codes", []) else self.failures).append(f)
        return ok

    # ---------------------------------------------------------------- corpus and ground truth
    def verify_dataset(self, corpus, db):
        man = json.load(open(self.exp["manifest"]))
        rows = db.execute("SELECT memory_id, text, status, superseded_by, observed_at FROM memories ORDER BY rid")
        th = hashlib.blake2b(digest_size=32)
        n = 0
        mismatched = []
        full = corpus.n <= self.gates.get("full_regeneration_max_n", 2_000_000)
        sample = set(random.Random(7).sample(range(corpus.n), min(2000, corpus.n)))
        for i, r in enumerate(rows):
            th.update(("%s\t%s\t%s\t%s\t%s\n" % (r[0], r[2], r[3] or "", r[4], r[1])).encode())
            if full or i in sample:
                if tuple(r) != corpus.record(i)[:5] and len(mismatched) < 5:
                    mismatched.append(r[0])
            n += 1
        self.check("row_count", n == corpus.n, f"{n} rows in DB, spec says {corpus.n}", "ROW_COUNT")
        self.check("dataset_hash", th.hexdigest() == man["dataset_text_blake2b256"],
                   f"DB text hash {th.hexdigest()[:16]}… vs manifest {man['dataset_text_blake2b256'][:16]}…",
                   "DATASET_HASH_MISMATCH", "regenerate the bank from the seed")
        self.check("records_match_independent_spec", not mismatched,
                   ("all" if full else "2000 sampled") + f" records re-derived in Python; mismatches: {mismatched}",
                   "GENERATOR_SPEC_DIVERGENCE", "fix generator/spec divergence before measuring")
        vh = hashlib.blake2b(digest_size=32)
        dims = set()
        for (blob, dim) in db.execute("SELECT embedding, embedding_dim FROM memories ORDER BY rid"):
            vh.update(blob)
            dims.add(dim)
        self.check("vector_hash", vh.hexdigest() == man["dataset_vec_blake2b256"],
                   f"embedding blobs hash {'matches' if vh.hexdigest() == man['dataset_vec_blake2b256'] else 'differs from'} manifest; dims {sorted(dims)}",
                   "VECTOR_HASH_MISMATCH", "rebuild the bank; embeddings changed")
        return man

    def verify_ground_truth(self, corpus, db, queries):
        mine = corpus.queries(len(queries))
        diff = [q["qid"] for q, p in zip(queries, mine)
                if (q["text"], q["type"], q["relevant"], q["expected_values"]) != (p["text"], p["type"], p["relevant"], p["expected_values"])]
        self.check("ground_truth_independent", not diff,
                   f"{len(queries)} queries re-derived; {len(diff)} differ {diff[:5]}",
                   "GROUND_TRUTH_DIVERGENCE", "regenerate queries")
        # the relevant rows must exist and actually state the expected value about the named entity
        bad = []
        for q, p in zip(queries, mine):
            for mid in p["relevant"]:
                row = db.execute("SELECT text, status FROM memories WHERE memory_id=?", (mid,)).fetchone()
                want_status = "superseded" if p["type"] == "historical" else "active"
                name = corpus.name(p["entity"])
                if (row is None or row[1] != want_status or name not in row[0]
                        or not any(v in row[0] for v in p["expected_values"])):
                    bad.append(mid)
        self.check("ground_truth_grounded_in_db", not bad,
                   f"every relevant row exists, has the expected status, names the entity and states an expected value; bad: {bad[:5]}",
                   "GROUND_TRUTH_UNGROUNDED", "fix ground-truth generation")
        return mine

    # ---------------------------------------------------------------- metrics from raw rows
    def recompute(self, truth, raw_rows, summary):
        by_q = {q["qid"]: q for q in truth}
        levels = sorted({r["concurrency"] for r in raw_rows if r["phase"] == "warm"})
        serial = {r["qid"]: r for r in raw_rows if r["phase"] == "warm" and r["concurrency"] == levels[0]}
        self.check("complete_serial_pass", len(serial) == len(truth),
                   f"{len(serial)}/{len(truth)} queries have a serial-pass row", "INCOMPLETE_RUN")
        agg, per_type = {}, {}
        for qid, r in serial.items():
            q = by_q[qid]
            if not q["relevant"]:
                continue
            m = ranking_metrics(r["top"], q["relevant"])
            # the engine appends superseded predecessors after the top k when include_superseded is set
            m["hit_any_returned"] = 1.0 if set(r["top"]) & set(q["relevant"]) else 0.0
            for kname, v in m.items():
                agg.setdefault(kname, []).append(v)
                per_type.setdefault(q["type"], {}).setdefault(kname, []).append(v)
        mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")  # noqa: E731
        quality = {f"{kname}@10" if kname != "mrr" else "mrr@10": mean(v) for kname, v in agg.items()}
        quality["answerable_queries"] = len(agg.get("hit", []))
        quality["by_type"] = {t: {f"{k}@10": round(mean(v), 4) for k, v in d.items()} | {"n": len(d["hit"])}
                              for t, d in per_type.items()}
        # unanswerable: no returned memory may be about the queried entity AND attribute (it does not exist)
        self.metrics["quality"] = quality

        lat = {}
        for cl in levels:
            rows = [r for r in raw_rows if r["phase"] == "warm" and r["concurrency"] == cl]
            ok = sorted(r["latency_ms"] for r in rows if r["ok"])
            errs = sum(1 for r in rows if not r["ok"])
            lv = next(x for x in summary["levels"] if x["concurrency"] == cl)
            lat[cl] = {"n": len(rows), "errors": errs, "p50_ms": pct(ok, 50), "p95_ms": pct(ok, 95), "p99_ms": pct(ok, 99),
                       "successful_qps": len(ok) / lv["wall_s"]}
            for p in ("p50_ms", "p95_ms", "p99_ms"):
                self.check(f"summary_{p}_c{cl}", abs(lat[cl][p] - lv["latency"][p]) < 1e-6,
                           f"recomputed {lat[cl][p]:.3f} vs Retriever summary {lv['latency'][p]:.3f}", "SUMMARY_DISAGREES")
            # same query, same answer, whatever the concurrency
            if cl != levels[0]:
                drift = [r["qid"] for r in rows if r["ok"] and r["top"] != serial[r["qid"]]["top"]]
                self.check(f"deterministic_under_concurrency_c{cl}", not drift,
                           f"{len(drift)} queries returned a different top-10 at concurrency {cl}: {drift[:5]}",
                           "NONDETERMINISTIC_UNDER_LOAD", "investigate shared state in the search path")
        self.metrics["latency"] = lat
        return quality, lat

    # ---------------------------------------------------------------- gates
    def gates_check(self, quality, lat, man, summary):
        g = self.gates
        levels = sorted(lat)
        c1 = lat[levels[0]]
        errors = sum(v["errors"] for v in lat.values())
        self.check("gate_no_errors", errors == 0, f"{errors} failed searches", "SEARCH_ERRORS")
        self.check("gate_recall", quality["recall@10"] >= g["recall_at_10_min"],
                   f"recall@10 {quality['recall@10']:.4f} (floor {g['recall_at_10_min']})",
                   "RECALL_BELOW_FLOOR", "inspect misses by query type before changing latency paths")
        self.check("gate_p99_serial", c1["p99_ms"] <= g["p99_ms_max"],
                   f"serial warm p99 {c1['p99_ms']:.1f} ms (budget {g['p99_ms_max']} ms)",
                   "P99_OVER_BUDGET", "profile the search path stage by stage")
        base = self.exp.get("baseline_quality")
        if base is not None:
            drop = base["recall@10"] - quality["recall@10"]
            self.check("gate_recall_non_regression", drop <= g["max_recall_drop_vs_baseline"],
                       f"recall@10 {quality['recall@10']:.4f} vs shipped engine {base['recall@10']:.4f} on the same bank "
                       f"(drop {drop:+.4f}, allowed {g['max_recall_drop_vs_baseline']})",
                       "RECALL_REGRESSION", "find the component that loses recall before keeping the speed-up")
        cmax = levels[-1]
        speedup = lat[cmax]["successful_qps"] / c1["successful_qps"]
        self.check("gate_concurrency_scaling", speedup >= g["min_qps_speedup_at_max_concurrency"],
                   f"QPS at concurrency {cmax} is {speedup:.2f}x serial (floor {g['min_qps_speedup_at_max_concurrency']}x)",
                   "CONCURRENCY_DOES_NOT_SCALE", "find the serialising lock in the search path")
        self.check("gate_p99_concurrent", lat[cmax]["p99_ms"] <= g["p99_ms_max_concurrent"],
                   f"p99 at concurrency {cmax}: {lat[cmax]['p99_ms']:.1f} ms (budget {g['p99_ms_max_concurrent']} ms)",
                   "P99_CONCURRENT_OVER_BUDGET", "reduce per-query CPU or contention")
        ram = int(summary["env"]["mem_bytes"])
        peak = summary["rss_bytes"]["peak"]
        self.check("gate_peak_rss", peak <= g["peak_rss_fraction_of_ram"] * ram,
                   f"peak RSS {peak / 2**30:.2f} GiB ({peak / ram:.1%} of RAM; budget {g['peak_rss_fraction_of_ram']:.0%})",
                   "RSS_OVER_BUDGET", "cut resident bytes per memory")
        n = man["n"]
        resident = max(summary["rss_bytes"]["after_index_load"] - summary["rss_bytes"]["process_start"], 0)
        per_mem = resident / n
        nxt = n * g["next_stage_multiplier"]
        proj = per_mem * nxt
        self.metrics["memory"] = {"resident_bytes_after_load": resident, "resident_bytes_per_memory": per_mem,
                                  "projected_resident_bytes_next_stage": proj, "next_stage_n": nxt}
        self.check("gate_projected_rss_next_stage", proj <= g["projected_rss_fraction_of_ram"] * ram,
                   f"{per_mem:,.0f} resident B/memory -> {proj / 2**30:.1f} GiB at n={nxt:,} "
                   f"(budget {g['projected_rss_fraction_of_ram']:.0%} of RAM = {g['projected_rss_fraction_of_ram'] * ram / 2**30:.1f} GiB)",
                   "PROJECTED_RSS_OVER_BUDGET", "bound resident memory: compact/quantised vectors or disk-backed segments")
        disk = summary["disk_bytes"]["total"]
        dproj = disk / n * nxt
        st = os.statvfs(os.path.dirname(os.path.abspath(self.exp["db"])))
        free = st.f_bavail * st.f_frsize
        self.metrics["disk"] = {"bytes": disk, "bytes_per_memory": disk / n, "projected_bytes_next_stage": dproj, "free_bytes": free}
        self.check("gate_projected_disk_next_stage", dproj <= g["projected_disk_fraction_of_free"] * free,
                   f"{disk / n:,.0f} disk B/memory -> {dproj / 2**30:.1f} GiB at n={nxt:,} (free {free / 2**30:.1f} GiB)",
                   "PROJECTED_DISK_OVER_BUDGET", "shrink on-disk bytes per memory")
        self.metrics["cold"] = summary["cold"]
        self.metrics["cpu_s"] = summary["cpu_s"]

    def claims_check(self, evidence):
        """Every claim in evidence.json must point at an artifact whose value matches."""
        for c in evidence.get("claims", []):
            try:
                v = json.load(open(c["artifact"]))
                for k in c["pointer"]:
                    v = v[k]
                ok = (abs(float(v) - float(c["value"])) <= 1e-6 * max(1.0, abs(float(v))))
            except Exception as e:  # noqa: BLE001
                ok, v = False, f"unreadable ({e})"
            self.check(f"claim_{c['id']}", ok, f"claimed {c['value']} vs artifact {v}", "UNSUPPORTED_CLAIM",
                       "drop or correct the claim")

    def run(self):
        e = self.exp
        corpus = Corpus(e["seed"], e["bank"], e["n"])
        db = sqlite3.connect(e["db"])  # SELECT only
        man = self.verify_dataset(corpus, db)
        queries = [json.loads(l) for l in open(e["queries"])]
        truth = self.verify_ground_truth(corpus, db, queries)
        raw = os.path.join(e["run_dir"], "raw_queries.jsonl")
        # committed artifacts are gzipped; a fresh run writes plain JSONL
        fh = gzip.open(raw + ".gz", "rt") if not os.path.exists(raw) and os.path.exists(raw + ".gz") else open(raw)
        raw_rows = [json.loads(l) for l in fh]
        summary = json.load(open(os.path.join(e["run_dir"], "run_summary.json")))
        quality, lat = self.recompute(truth, raw_rows, summary)
        self.gates_check(quality, lat, man, summary)
        if e.get("evidence") and os.path.exists(e["evidence"]):
            self.claims_check(json.load(open(e["evidence"])))
        self.metrics["ingest"] = {"memories_per_s_engine_insert": man["ingest_memories_per_s_engine_insert"],
                                  "memories_per_s_wall": man["ingest_memories_per_s_wall"],
                                  "build_peak_rss_bytes": man["peak_rss_bytes"], "build_cpu_s": man["cpu_s"]}
        return {"verdict": "PASS" if not self.failures else "FAIL", "experiment": e, "gates": self.gates,
                "checks": self.checks, "failures": self.failures, "advisories": self.advisories, "metrics": self.metrics}


if __name__ == "__main__":
    exp = json.load(open(sys.argv[1]))
    gates = json.load(open(sys.argv[2]))
    out = Critic(exp, gates).run()
    json.dump(out, open(sys.argv[3], "w"), indent=1, default=str)
    print(out["verdict"], [f["code"] for f in out["failures"]])

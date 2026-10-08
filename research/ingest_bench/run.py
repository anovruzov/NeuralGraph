"""Single store vs domain-sharded store under the same load (docs/mycelic/INGESTION.md §15, items 1, 3, 4, 5).

Deterministic: seeded synthetic corpus (corpus.py), hash embeddings (256-d), the real ingestion pipeline (local_export
connector -> admit -> classify -> route -> write) and the real retrieval path (EvidenceStore.search, owner audience).

Configurations, same corpus and seed:

* ``single``        one holder store (s0 only), the whole corpus ingested through the pipeline.
* ``sharded``       a holder whose ``--split`` largest domains were split into their own shards *before* ingest, so records
                    are routed to their shard at write time (s0 keeps the rest and the control tables).
* ``split_after``   the ``single`` holder after an online split of the same domains (copy, catch-up, cutover, cleanup):
                    the very same memory ids, partitioned differently, which makes the agreement measure exact.

Measured: ingest throughput (records/s, sync + process), write latency p50/p95 (the pipeline's write stage per record),
hybrid query latency p50/p95 (k=10), recall@10 agreement of the sharded search with the single-store search (both the
designed shard-level RRF and the channel-level fusion), known-item hit@10, the NeuralGraph vector-matrix bytes per store,
file sizes, and the split migration's duration.

Usage::

    .venv/bin/python research/ingest_bench/run.py                 # scale 1: 3,000 records, 200 queries (< 2 minutes)
    .venv/bin/python research/ingest_bench/run.py --scale 0.2     # quick
    .venv/bin/python research/ingest_bench/run.py --scale 4 --out research/ingest_bench/results/scale4.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import logging  # noqa: E402

logging.disable(logging.WARNING)

from corpus import DOMAINS, build_corpus, write_exports  # noqa: E402

from mycelic.evidence import EvidenceStore  # noqa: E402
from mycelic.ingest.connectors import register_builtin  # noqa: E402
from mycelic.ingest.pipeline import IngestPipeline  # noqa: E402
from mycelic.ingest.registry import ConnectorRegistry  # noqa: E402
from mycelic.ingest.service import IngestService  # noqa: E402

OWNER = "usr_bench"
TENANT = "ten_bench"
OWNER_AUDIENCE = {"principal_ids": [OWNER], "complete": True, "owner": True}


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 3)


def machine() -> dict[str, Any]:
    cpu = ""
    try:
        cpu = next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name"))
    except Exception:
        pass
    mem = ""
    try:
        mem = next(line.split(":", 1)[1].strip() for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal"))
    except Exception:
        pass
    import numpy
    return {"platform": platform.platform(), "cpu": cpu, "cpus": os.cpu_count(), "memory": mem, "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "numpy": numpy.__version__}


async def make_holder(path: Path) -> tuple[EvidenceStore, IngestPipeline, IngestService]:
    store = EvidenceStore(path, holder_id="hold_bench", tenant_id=TENANT, owner_ids=[OWNER])
    pipe = IngestPipeline(store, registry=register_builtin(ConnectorRegistry()), batch_debounce_seconds=3600.0)
    # keep every latency sample of the run (the default window is the last 512)
    store.shards._latency = defaultdict(lambda: deque(maxlen=10 ** 7))
    return store, pipe, IngestService(pipe)


async def ingest(pipe: IngestPipeline, svc: IngestService, files: list[dict[str, Any]]) -> dict[str, Any]:
    for f in files:
        con = await svc.add_connector("local_export", created_by=OWNER, config={"source_app": f"{f['app']}", "account_id": f"{f['domain']}-{f['app']}",
                                                                                  "paths": [str(f["path"])], "auto_include": True,
                                                                                  "limits": {"page_size": 500}})
        await svc.discover_sources(con["connector_id"])
    t0 = time.perf_counter()
    for con in pipe.db.list_connectors():
        await pipe.sync(con["connector_id"])
    t_sync = time.perf_counter() - t0
    rep = await pipe.process_available()
    total = time.perf_counter() - t0
    return {"records": rep.processed, "outcomes": dict(rep.outcomes), "seconds": round(total, 3), "sync_seconds": round(t_sync, 3),
            "process_seconds": round(total - t_sync, 3), "records_per_second": round(rep.processed / total, 1) if total else None}


def write_latency(store: EvidenceStore) -> dict[str, Any]:
    per: dict[str, list[float]] = {}
    for (sid, kind), xs in store.shards._latency.items():
        if kind == "write":
            per[sid] = list(xs)
    allx = [x for xs in per.values() for x in xs]
    return {"p50_ms": pct(allx, 0.5), "p95_ms": pct(allx, 0.95), "n": len(allx),
            "per_shard": {sid: {"p50_ms": pct(xs, 0.5), "p95_ms": pct(xs, 0.95), "n": len(xs)} for sid, xs in sorted(per.items())}}


async def run_queries(store: EvidenceStore, queries: list[dict[str, Any]], *, k: int, merge: str = "rrf", warmup: bool = True) -> dict[str, Any]:
    store.fanout_merge = merge
    if warmup:                                           # build the in-RAM indexes outside the measurement
        await store.search(queries[0]["text"], k=k, audience=OWNER_AUDIENCE)
    lat, results = [], {}
    for q in queries:
        t0 = time.perf_counter()
        res = await store.search(q["text"], k=k, audience=OWNER_AUDIENCE)
        lat.append((time.perf_counter() - t0) * 1000)
        results[q["text"]] = [(r["doc_id"], r["chunk_index"], r["memory_id"]) for r in res]
        assert not getattr(res, "partial", False), "a shard failed during the benchmark"
    store.fanout_merge = "rrf"
    return {"p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95), "mean_ms": round(statistics.mean(lat), 3), "results": results}


def object_of(store: EvidenceStore, doc_id: str) -> str | None:
    c = store._doc_store(doc_id).store._conn
    r = c.execute("SELECT source_object_id FROM ingest_records WHERE record_id=?", (doc_id,)).fetchone()
    return r[0] if r else None


def hit_at_k(store: EvidenceStore, queries: list[dict[str, Any]], results: dict[str, list[tuple]]) -> float:
    hits = 0
    for q in queries:
        objs = {object_of(store, d) for d, _c, _m in results[q["text"]]}
        hits += int(q["expected_object_id"] in objs)
    return round(hits / len(queries), 4)


def agreement(a: dict[str, list[tuple]], b: dict[str, list[tuple]], *, key: str, k: int) -> dict[str, Any]:
    """Mean |top-k(a) ∩ top-k(b)| / k over the queries (recall@k of b against a as the reference)."""
    idx = {"memory": lambda t: t[2], "chunk": lambda t: (t[0], t[1])}[key]
    vals = []
    for q, ra in a.items():
        sa = {idx(t) for t in ra[:k]}
        sb = {idx(t) for t in b.get(q, [])[:k]}
        if sa:
            vals.append(len(sa & sb) / len(sa))
    return {"mean": round(statistics.mean(vals), 4), "min": round(min(vals), 4), "share_perfect": round(sum(v == 1.0 for v in vals) / len(vals), 4)}


def matrix(store: EvidenceStore) -> dict[str, Any]:
    """Bytes of NeuralGraph's in-RAM vector matrices (built by the queries above), per shard."""
    out: dict[str, int] = {}
    if store._multi():
        for spec in store.shards.queryable():
            out[spec.shard_id] = store.shards.reader(spec.shard_id).matrix_bytes()
    else:
        idx = store.retriever._index
        out["s0"] = int(sum(M.nbytes for _i, M in idx.mats.values())) if idx is not None and hasattr(idx, "mats") else 0
    return {"total_bytes": sum(out.values()), "max_shard_bytes": max(out.values()) if out else 0, "per_shard": out}


async def files_of(store: EvidenceStore) -> dict[str, int]:
    """Database file bytes per shard after a WAL checkpoint (the WAL is transient and capped by autocheckpoint)."""
    out = {}
    for spec, st in store.shards.open_stores(statuses=("active",)):
        await st.store.checkpoint_wal()
        p = Path(st.path)
        out[spec.shard_id] = p.stat().st_size
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scale", type=float, default=1.0, help="corpus size multiplier (1.0 = 3,000 records)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--split", type=int, default=3, help="how many of the largest domains get their own shard")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--fetch-k", type=int, default=None, help="per-shard depth of the fan-out (default 2 * k)")
    ap.add_argument("--out", type=str, default=str(Path(__file__).resolve().parent / "results" / "latest.json"))
    args = ap.parse_args()
    started = time.perf_counter()
    n = int(3000 * args.scale)
    corpus = build_corpus(n, seed=args.seed, n_queries=args.queries)
    split = DOMAINS[:args.split]
    work = Path(tempfile.mkdtemp(prefix="ingest-bench-"))
    try:
        files = write_exports(corpus, work / "exports")
        out: dict[str, Any] = {"machine": machine(), "params": {"records": corpus.total, "seed": args.seed, "queries": len(corpus.queries), "k": args.k,
                                                                 "split_domains": split, "domain_sizes": corpus.sizes, "embedder": "hash-256",
                                                                 "fetch_k": args.fetch_k or 2 * args.k}}
        # ---------------------------------------------------------------- single store
        s1, p1, svc1 = await make_holder(work / "single" / "evidence.db")
        out["single"] = {"ingest": await ingest(p1, svc1, files), "write": write_latency(s1)}
        q1 = await run_queries(s1, corpus.queries, k=args.k)
        out["single"]["query"] = {k: q1[k] for k in ("p50_ms", "p95_ms", "mean_ms")}
        out["single"]["hit_at_k"] = hit_at_k(s1, corpus.queries, q1["results"])
        out["single"]["matrix"] = matrix(s1)
        out["single"]["files"] = await files_of(s1)
        # ---------------------------------------------------------------- sharded at write time
        s2, p2, svc2 = await make_holder(work / "sharded" / "evidence.db")
        s2.fanout_fetch_k = s1.fanout_fetch_k = args.fetch_k
        for dom in split:
            await s2.shards.start_split([dom], requested_by=OWNER)
        out["sharded"] = {"ingest": await ingest(p2, svc2, files), "write": write_latency(s2)}
        q2 = await run_queries(s2, corpus.queries, k=args.k)
        q2c = await run_queries(s2, corpus.queries, k=args.k, merge="channel", warmup=False)
        out["sharded"]["query_rrf"] = {k: q2[k] for k in ("p50_ms", "p95_ms", "mean_ms")}
        out["sharded"]["query_channel"] = {k: q2c[k] for k in ("p50_ms", "p95_ms", "mean_ms")}
        out["sharded"]["hit_at_k_rrf"] = hit_at_k(s2, corpus.queries, q2["results"])
        out["sharded"]["hit_at_k_channel"] = hit_at_k(s2, corpus.queries, q2c["results"])
        out["sharded"]["agreement_rrf_vs_single"] = agreement(q1["results"], q2["results"], key="chunk", k=args.k)
        out["sharded"]["agreement_channel_vs_single"] = agreement(q1["results"], q2c["results"], key="chunk", k=args.k)
        out["sharded"]["agreement_at3_rrf_vs_single"] = agreement(q1["results"], q2["results"], key="chunk", k=3)
        out["sharded"]["agreement_at3_channel_vs_single"] = agreement(q1["results"], q2c["results"], key="chunk", k=3)
        out["sharded"]["matrix"] = matrix(s2)
        out["sharded"]["files"] = await files_of(s2)
        out["sharded"]["records_per_shard"] = {sid: int(c.execute("SELECT COUNT(*) FROM ingest_records").fetchone()[0])
                                               for sid, c in ((sp.shard_id, st.store._conn) for sp, st in s2.shards.open_stores(statuses=("active",)))}
        # ---------------------------------------------------------------- the single store, split online
        t0 = time.perf_counter()
        migs = [await s1.shards.start_split([dom], requested_by=OWNER) for dom in split]
        dt = time.perf_counter() - t0
        moved = sum(int(m["checkpoint"].get("moved") or 0) for m in migs)
        out["split_after"] = {"migration": {"seconds": round(dt, 3), "records_moved": moved, "records_per_second": round(moved / dt, 1) if dt else None,
                                            "states": [m["state"] for m in migs]}}
        q3 = await run_queries(s1, corpus.queries, k=args.k)
        q3c = await run_queries(s1, corpus.queries, k=args.k, merge="channel", warmup=False)
        out["split_after"]["query_rrf"] = {k: q3[k] for k in ("p50_ms", "p95_ms", "mean_ms")}
        out["split_after"]["query_channel"] = {k: q3c[k] for k in ("p50_ms", "p95_ms", "mean_ms")}
        out["split_after"]["agreement_rrf_vs_single"] = agreement(q1["results"], q3["results"], key="memory", k=args.k)
        out["split_after"]["agreement_channel_vs_single"] = agreement(q1["results"], q3c["results"], key="memory", k=args.k)
        out["split_after"]["agreement_at3_rrf_vs_single"] = agreement(q1["results"], q3["results"], key="memory", k=3)
        out["split_after"]["agreement_at3_channel_vs_single"] = agreement(q1["results"], q3c["results"], key="memory", k=3)
        out["split_after"]["hit_at_k_rrf"] = hit_at_k(s1, corpus.queries, q3["results"])
        out["split_after"]["hit_at_k_channel"] = hit_at_k(s1, corpus.queries, q3c["results"])
        out["split_after"]["matrix"] = matrix(s1)
        out["split_after"]["files"] = await files_of(s1)
        await s1.close()
        await s2.close()
        out["seconds"] = round(time.perf_counter() - started, 1)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(report(out))
    print(f"\nwritten to {args.out}")


def report(o: dict[str, Any]) -> str:
    s, h, a = o["single"], o["sharded"], o["split_after"]
    mib = lambda b: f"{b / (1 << 20):.2f} MiB"  # noqa: E731
    rows = [
        ("records", o["params"]["records"], o["params"]["records"], o["params"]["records"]),
        ("shards", 1, len(h["files"]), len(a["files"])),
        ("ingest records/s", s["ingest"]["records_per_second"], h["ingest"]["records_per_second"], "-"),
        ("write p50 / p95 ms", f"{s['write']['p50_ms']} / {s['write']['p95_ms']}", f"{h['write']['p50_ms']} / {h['write']['p95_ms']}", "-"),
        ("query p50 / p95 ms (rrf)", f"{s['query']['p50_ms']} / {s['query']['p95_ms']}", f"{h['query_rrf']['p50_ms']} / {h['query_rrf']['p95_ms']}",
         f"{a['query_rrf']['p50_ms']} / {a['query_rrf']['p95_ms']}"),
        ("query p50 / p95 ms (channel)", "-", f"{h['query_channel']['p50_ms']} / {h['query_channel']['p95_ms']}",
         f"{a['query_channel']['p50_ms']} / {a['query_channel']['p95_ms']}"),
        ("recall@10 agreement (rrf)", "1.0 (reference)", h["agreement_rrf_vs_single"]["mean"], a["agreement_rrf_vs_single"]["mean"]),
        ("recall@10 agreement (channel)", "1.0 (reference)", h["agreement_channel_vs_single"]["mean"], a["agreement_channel_vs_single"]["mean"]),
        ("top-3 agreement (rrf / channel)", "1.0 (reference)", f"{h['agreement_at3_rrf_vs_single']['mean']} / {h['agreement_at3_channel_vs_single']['mean']}",
         f"{a['agreement_at3_rrf_vs_single']['mean']} / {a['agreement_at3_channel_vs_single']['mean']}"),
        ("known-item hit@10 (rrf / channel)", s["hit_at_k"], f"{h['hit_at_k_rrf']} / {h['hit_at_k_channel']}", f"{a['hit_at_k_rrf']} / {a['hit_at_k_channel']}"),
        ("vector matrix, total", mib(s["matrix"]["total_bytes"]), mib(h["matrix"]["total_bytes"]), mib(a["matrix"]["total_bytes"])),
        ("vector matrix, largest shard", mib(s["matrix"]["max_shard_bytes"]), mib(h["matrix"]["max_shard_bytes"]), mib(a["matrix"]["max_shard_bytes"])),
        ("database files, total", mib(sum(s["files"].values())), mib(sum(h["files"].values())), mib(sum(a["files"].values()))),
    ]
    lines = ["| measure | single store | sharded at write time | single, split online |", "|---|---|---|---|"]
    lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |" for r in rows]
    mig = a["migration"]
    lines.append(f"\nonline split of {o['params']['split_domains']}: {mig['records_moved']} records in {mig['seconds']} s ({mig['records_per_second']} records/s)")
    lines.append(f"run took {o['seconds']} s on {o['machine']['cpu']} ({o['machine']['cpus']} cpus), Python {o['machine']['python']}, SQLite {o['machine']['sqlite']}")
    return "\n".join(lines)


if __name__ == "__main__":
    asyncio.run(main())

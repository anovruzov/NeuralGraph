# Hindsight Memlake: primary-source extraction (Retriever output, 2026-10-31)

Collected by the Retriever research pass from primary sources only. Quotes are short excerpts; docs pages were
read through a summarising fetcher, so their quotes are close but not character-checked. The blog quotes come from
the raw markdown in the public repo.

Sources:
- BLOG: https://raw.githubusercontent.com/vectorize-io/hindsight/main/hindsight-docs/blog/2026-10-13-memlake.md
  (renders at https://hindsight.vectorize.io/blog/2026/10/13/memlake)
- SVG: https://raw.githubusercontent.com/vectorize-io/hindsight/main/hindsight-docs/static/img/blog/memlake/scale-light.svg
- PERF: https://docs.hindsight.vectorize.io/enterprise/memlake/performance/
- ARCH: https://docs.hindsight.vectorize.io/enterprise/memlake/architecture
- TRADE: https://docs.hindsight.vectorize.io/enterprise/memlake/tradeoffs
- ENT: https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/docs/cloud-enterprise/enterprise.md
- WP: https://vectorize.io/memlake/whitepaper (behind a form; not read)

| Claim | Value | Source | Quote |
|---|---|---|---|
| Total memories / banks | 100M across 20 banks | BLOG | "100 million memories across 20 banks ran on 4 servers" |
| Memories per bank | 5M | SVG | "100M memories (20 banks × 5M) on 4 servers" |
| Query servers | 4 × 6 vCPU | BLOG, PERF | "4 servers of 6 vCPU each" |
| Object storage (100M run) | unnamed, other cloud | BLOG footnote | "used object storage in another cloud" |
| Cache | RAM + local NVMe, sizes not stated | BLOG | "its cache, in memory and on local NVMe, stays warm" |
| Latency vs load (100M) | p50 40–43 ms, p99 73–80 ms, 1→200 recalls/s | BLOG alt text | "p50 40–43 ms and p99 73–80 ms from 1 to 200 recalls" |
| Per-load points | 1 rps 40/75; 10 rps 40/73; 200 rps 43/80 (p50/p99 ms) | SVG labels | "100M · 200 recall/s" |
| Max load tested | 200 recalls/s, not a saturation point | BLOG | "Latency stayed flat from 1 to 200 recalls per second" |
| Capacity | ~8–14 warm recalls/s per vCPU | BLOG | "roughly 8–14 warm recalls per second per vCPU" |
| Single bank 3.08M | p50 71 / p99 90 ms, serial, one 6-vCPU node, GCS same region | BLOG, ARCH | "one request at a time on one 6-vCPU query node" |
| Bank size sweep | 100k→3M (30×) ≈ 6× slower; 16 clients; not fully warm | BLOG footnote | "made a recall about 6× slower under heavy load" |
| Largest single bank | 10M (no warm latency given) | BLOG | "The largest single bank we've run holds 10 million memories" |
| Ingestion | 1,200–1,500 memories/s per bank | BLOG | "a single Memlake bank sustains 1,200–1,500 memories per second" |
| Commit latency | ~0.25 s | BLOG, ARCH | "A write commits in about a quarter of a second" |
| Cold open | 10M bank metadata load 8.6 s p95 | BLOG | "8.6 s at p95 for a 10M-memory bank, once per server" |
| Cold query | ~1 s p99 until cached; 1M bank 1.7 s → 74 ms | BLOG | "a 1M-memory bank went from 1.7 s p99 to 74 ms" |
| Dataset (100M) | synthetic | BLOG footnote | "the fleet ran on synthetic data" |
| Timing scope | engine-level gRPC, no Hindsight API | BLOG footnote | "Engine-level benchmarks (gRPC to Memlake, no Hindsight API in front)" |
| Reranker | excluded | BLOG | "All recall numbers are without the cross-encoder reranker" |
| Quality | BEIR SciFact/NFCorpus nDCG@10, bge-small, July engine; not at 100M | BLOG | "SciFact and NFCorpus, nDCG@10, bge-small-en-v1.5 embeddings" |
| Vector compression | RaBitQ ~60 B (384-d example) | BLOG | "for 384-dimension embeddings, 25× smaller than the original" |
| Storage | ~4–5 KB per memory in bucket | BLOG | "about 4–5 KB per memory in the bucket" |
| Licence | Memlake enterprise-only; OSS Hindsight is MIT on Postgres | BLOG, GitHub API | "It's part of Hindsight Enterprise" |

Not stated: RAM/instance type/region for the 100M run; cache sizes; any p95 recall latency; offered-load
distribution across banks and client concurrency; whether the 100M run was warm; warm latency at 10M; index build
time; embedding model/dimension for engine runs; synthetic record size; query generation; whether timing includes
query embedding; client- vs server-side timing; search modes and top-k; recall@k/nDCG at 100M or 3M; benchmark code
and data (not published; repo code search for "memlake" returns docs/figures only). No 30M single-bank result exists.

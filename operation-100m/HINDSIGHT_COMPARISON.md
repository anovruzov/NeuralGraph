# Hindsight Memlake vs NeuralGraph: what can and cannot be compared

Primary-source extraction with quotes and URLs: [`sources/hindsight_primary_sources.md`](sources/hindsight_primary_sources.md).
Labels: **published** (Hindsight's own statement, not reproduced), **verified** (measured here by the Critic),
**unknown**.

## Hindsight's published protocol — published

| Item | Published value |
|---|---|
| Fleet | 100M memories = 20 banks × 5M, synthetic data |
| Query servers | 4 × 6 vCPU (24 vCPU); RAM, instance type, cloud, region not stated |
| Storage | object storage "in another cloud"; RAM + local NVMe cache, sizes not stated |
| Warm recall latency (100M) | p50 40–43 ms, p99 73–80 ms at 1, 10 and 200 recalls/s; no p95 published |
| Max load | 200 recalls/s across the cluster — tested, not a saturation point (~8–14 warm recalls/s per vCPU) |
| Timing scope | engine-level gRPC, no Hindsight API, no cross-encoder reranker; whether query embedding is included is not stated |
| Single bank | 3.08M realistic bank: p50 71 / p99 90 ms, serial, one 6-vCPU node; largest bank 10M (no warm latency) |
| Cold | 10M bank metadata load 8.6 s p95; ~1 s p99 queries until cached; 1M bank 1.7 s → 74 ms p99 |
| Ingestion | 1,200–1,500 memories/s per bank (store only, no LLM extraction) |
| Quality at scale | **not published** (only BEIR SciFact/NFCorpus nDCG@10 on a small corpus, older engine) |
| Code / data | closed (Hindsight Enterprise); generator and driver not published |

## Comparison rules this project follows

1. **Same timing scope only.** NeuralGraph numbers here time `MemoryRetriever::search` in-process with the query
   embedding precomputed, no reranker, no LLM — the closest available analogue of "engine-level, no API". They
   are never compared with NeuralGraph's end-to-end LLM answer latency, and Hindsight's numbers are never compared
   with an API-level or answer-level number.
2. **Per-bank, not per-fleet, unless the fleet is run.** Hindsight's 100M is 20 independent 5M banks; each query
   searches one 5M partition. A single NeuralGraph bank is compared with a single Memlake bank at the same size only.
3. **Hardware is not matched.** This Mac (Apple M4, 4P+6E cores, 16 GB, local NVMe, no object store) is not four
   6-vCPU cloud servers reading object storage across clouds. Latencies are reported side by side, never as a
   win/loss.
4. **Quality is reported with every latency.** Hindsight publishes no recall at scale; NeuralGraph results here
   always carry Critic-recomputed recall@10 / MRR / nDCG@10 against independent ground truth. Latency without the
   recall it was bought at is not reported.
5. **Data is not matched.** Hindsight's synthetic generator is unpublished; ours is deterministic, published in
   `bench/spec/` and re-derived independently by the Critic.

## Status of each comparison point

| Hindsight claim | NeuralGraph counterpart | Status |
|---|---|---|
| 100M aggregate, 20 × 5M banks | not run: exceeds this machine (see `RESULTS.md` capacity plan) | **unknown** for NeuralGraph |
| Warm p99 73–80 ms per recall at 5M/bank | 100K and (if gated) 1M single bank measured here | partial: different bank size and hardware |
| 200 recalls/s across 24 vCPU | successful QPS at concurrency 1/2/4/8 on 10 cores measured here | partial: different hardware |
| Cold start (8.6 s metadata load at 10M) | process-cold index load time measured here (OS page cache not purged) | partial: not the same cold definition |
| Ingestion 1,200–1,500/s per bank | storage-path bulk insert measured here | partial: both exclude LLM extraction; batch sizes differ |
| Quality at 100M | — | **not published by Hindsight**; NeuralGraph reports it at every stage |

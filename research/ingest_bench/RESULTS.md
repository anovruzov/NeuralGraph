# Ingest benchmarks: one store vs domain-sharded store

Harness: `research/ingest_bench/run.py` (corpus in `corpus.py`). It covers docs/mycelic/INGESTION.md §15 items 1, 3, 4 and
5 for one holder, comparing a single store with a store split by domain. Raw numbers are in `results/scale1.json` and
`results/scale4.json`. Every number on this page comes from those runs, or from the repeat runs listed under "Run-to-run
variance". Nothing here is extrapolated.

## Machine

| | |
|---|---|
| CPU | Intel(R) Xeon(R) Processor @ 2.10GHz, 4 vCPUs (cloud container) |
| RAM | 16 GB (MemTotal 16,480,952 kB) |
| Disk | ext4 on a virtio block device (`/dev/vda`); databases written under `/tmp` |
| OS | Linux 6.18.44, x86_64, glibc 2.39 |
| Software | Python 3.11.15, SQLite 3.45.1, numpy 2.4.6 |
| Embedder | hash-256 (deterministic, no network) |

The machine was otherwise idle during the runs. Each run is one Python process: the benchmark, the pipeline and every
shard share one interpreter.

## How to run

```
.venv/bin/python research/ingest_bench/run.py                 # scale 1: 2,995 records, 200 queries, about 1 minute
.venv/bin/python research/ingest_bench/run.py --scale 0.2     # quick smoke run
.venv/bin/python research/ingest_bench/run.py --scale 4 --out research/ingest_bench/results/scale4.json   # about 4 minutes
```

Options: `--seed` (default 7), `--queries` (200), `--split` (the N largest domains that get their own shard, default 3),
`--k` (10), and `--fetch-k` (per-shard depth of the fan-out, default 2k).

## Method

**Corpus.** The corpus is seeded and synthetic. It has 12 top-level domains (the default taxonomy) with Zipf-like sizes.
Each domain has its own vocabulary, made of its taxonomy keywords plus seeded lexicon words. A record is about 70% words
of its own domain and about 30% shared words, so queries also match records outside the expected domain. Records are
written as `local_export` files, one per (domain, app) pair. A source mapping sets each record's primary domain, so the
label is known by construction. There are 200 known-item queries: each is four content words of a sampled record, and
that record is the expected hit.

**Configurations.** All three use the same corpus and seed.

* `single`: one holder store (`evidence.db`, s0 only). The whole corpus goes through the real pipeline
  (sync → admit → classify → route → write).
* `sharded`: a fresh holder in which the 3 largest domains (engineering, infrastructure, product) are split into their
  own `shd_<id>.db` shards *before* ingest, so records are routed at write time. s0 keeps the other 9 domains and the
  control tables. That makes 4 shards.
* `split_after`: the `single` holder after an online split of the same 3 domains through the real migration (copy,
  catch-up, cutover, cleanup). It holds the same memory ids, partitioned differently, so its agreement with `single`
  is exact.

**Measures.**

* *Ingest records/s*: records divided by wall time of sync plus processing.
* *Write p50/p95*: latency of the pipeline's write stage per record. Samples are taken where the stage meter
  records them (`ShardSet.observe`). In the sharded holder this includes taking the shard's writer gate and applying
  the control intent to s0.
* *Query p50/p95*: `EvidenceStore.search(k=10)` with the owner audience: hybrid (vector + BM25 + graph), measured
  end to end. One warm-up query runs before timing so the in-RAM indexes are built first. Sharded search embeds the
  query once and searches the shards one after another on the holder's read worker, with a 1.5 s per-shard timeout.
  No shard timed out in any run.
* *Recall@10 agreement*: mean |top10(sharded) ∩ top10(single)| / 10 over the 200 queries. The single store is the
  reference. `sharded` is compared by (doc_id, chunk), since its memory ids differ from the single holder's.
  `split_after` is compared by memory id.
* *Top-3 agreement*: the same measure with k = 3.
* *Known-item hit@10*: the share of queries whose expected record appears in the top 10.
* *Vector matrix*: bytes of NeuralGraph's in-RAM float32 matrices after the queries, as total and largest shard. This
  is the §7.3 "vector matrix > 1 GiB" signal.
* *Database files*: the size of every shard file after a WAL checkpoint.
* *Two merges are measured.* `rrf` is the first design's shard-level RRF: each shard's ranking is fused by rank.
  `channel` (`EvidenceStore.fanout_merge = "channel"`, the default since DECISIONS D17) fuses each retrieval channel
  (vector, keyword, graph) by rank across all shards' candidates, then applies the retriever's priors, which is how one
  store fuses them. The runs below predate that change and name each merge explicitly.

## Results, scale 1 (2,995 records, 4 shards; run took 52.8 s)

| measure | single store | sharded at write time | single, split online |
|---|---|---|---|
| records | 2995 | 2995 | 2995 |
| shards | 1 | 4 | 4 |
| ingest records/s | 169.3 | 177.5 | - |
| write p50 / p95 ms | 1.81 / 18.014 | 1.824 / 14.745 | - |
| query p50 / p95 ms (rrf) | 5.551 / 9.859 | 15.719 / 21.421 | 16.092 / 20.947 |
| query p50 / p95 ms (channel) | - | 15.771 / 22.598 | 15.671 / 19.178 |
| recall@10 agreement (rrf) | 1.0 (reference) | 0.3845 | 0.3875 |
| recall@10 agreement (channel) | 1.0 (reference) | 0.674 | 0.675 |
| top-3 agreement (rrf / channel) | 1.0 (reference) | 0.36 / 0.715 | 0.3617 / 0.715 |
| known-item hit@10 (rrf / channel) | 0.98 | 0.9 / 0.985 | 0.9 / 0.985 |
| vector matrix, total | 2.92 MiB | 2.92 MiB | 2.92 MiB |
| vector matrix, largest shard | 2.92 MiB | 1.30 MiB | 1.30 MiB |
| database files, total | 29.45 MiB | 32.08 MiB | 44.36 MiB |

Online split of engineering, infrastructure and product: 1,661 records in 3.002 s (553.4 records/s), all three
migrations `done`.

Records per shard (sharded): s0 1,334; engineering 871; infrastructure 466; product 324. Write p50/p95 per shard (ms):
s0 1.662/15.09; engineering 2.081/14.996; infrastructure 1.894/14.088; product 1.792/13.542.

## Results, scale 4 (11,993 records, 4 shards; run took 221.2 s)

| measure | single store | sharded at write time | single, split online |
|---|---|---|---|
| records | 11993 | 11993 | 11993 |
| shards | 1 | 4 | 4 |
| ingest records/s | 136.4 | 146.7 | - |
| write p50 / p95 ms | 2.567 / 21.313 | 2.359 / 15.918 | - |
| query p50 / p95 ms (rrf) | 12.24 / 44.702 | 26.332 / 35.497 | 26.678 / 52.886 |
| query p50 / p95 ms (channel) | - | 25.652 / 34.282 | 28.235 / 82.888 |
| recall@10 agreement (rrf) | 1.0 (reference) | 0.368 | 0.366 |
| recall@10 agreement (channel) | 1.0 (reference) | 0.643 | 0.6445 |
| top-3 agreement (rrf / channel) | 1.0 (reference) | 0.3617 / 0.6517 | 0.36 / 0.6533 |
| known-item hit@10 (rrf / channel) | 0.92 | 0.845 / 0.91 | 0.84 / 0.91 |
| vector matrix, total | 11.71 MiB | 11.71 MiB | 11.71 MiB |
| vector matrix, largest shard | 11.71 MiB | 5.22 MiB | 5.22 MiB |
| database files, total | 115.19 MiB | 118.72 MiB | 168.02 MiB |

Online split of the same three domains: 6,647 records in 20.052 s (331.5 records/s), all three migrations `done`.

Records per shard (sharded): s0 5,346; engineering 3,484; infrastructure 1,867; product 1,296. Write p50/p95 per shard
(ms): s0 1.947/15.871; engineering 3.718/16.881; infrastructure 2.727/15.999; product 2.274/14.771.

## Run-to-run variance

Each scale was run a second time on the same code and machine. The second runs' JSON is not committed; the figures below
are copied from their output. A is the committed run and B is the repeat.

| measure | scale 1, A | scale 1, B | scale 4, A | scale 4, B |
|---|---|---|---|---|
| ingest records/s, single / sharded | 169.3 / 177.5 | 181.4 / 173.1 | 136.4 / 146.7 | 136.4 / 139.0 |
| write p50 ms, single / sharded | 1.81 / 1.824 | 1.691 / 1.866 | 2.567 / 2.359 | 2.556 / 2.417 |
| write p95 ms, single / sharded | 18.014 / 14.745 | 16.905 / 15.121 | 21.313 / 15.918 | 21.042 / 18.506 |
| query p50 ms (rrf), single / sharded / split online | 5.551 / 15.719 / 16.092 | 5.921 / 16.663 / 16.416 | 12.24 / 26.332 / 26.678 | 12.624 / 28.268 / 23.849 |
| query p95 ms (rrf), single / sharded / split online | 9.859 / 21.421 / 20.947 | 11.345 / 23.74 / 23.019 | 44.702 / 35.497 / 52.886 | 45.597 / 64.02 / 31.089 |
| recall@10 agreement, rrf / channel (sharded) | 0.3845 / 0.674 | 0.388 / 0.677 | 0.368 / 0.643 | 0.368 / 0.642 |
| known-item hit@10, single / rrf / channel (sharded) | 0.98 / 0.9 / 0.985 | 0.985 / 0.9 / 0.985 | 0.92 / 0.845 / 0.91 | 0.925 / 0.845 / 0.91 |
| online split, records/s | 553.4 | 570.8 | 331.5 | 332.3 |

The retrieval measures are not bit-identical between runs: single-store hit@10 is 0.98 in one run and 0.985 in the
other. The corpus, the seed and the embeddings are fixed. This was not investigated. A plausible source is the
retriever's recency prior, which is computed against the wall clock and can flip near-ties. Between runs,
throughput, write p50 and query p50 differ by up to 12%. Query p95 over 200 queries is not stable: at scale 4 the
write-time-sharded p95 was 35.5 ms in one run and 64.0 ms in the other.

## Findings

1. **Shard-level RRF, as designed, does not reproduce single-store retrieval.** Agreement at recall@10 is 0.37 to 0.39,
   against the §15 target of ≥ 0.95. Known-item hit@10 drops from 0.98 to 0.90 at 3k records and from 0.92 to 0.85 at
   12k. The cause is structural: rank fusion gives every shard's rank 1 the same score, so each of the 4 shards
   contributes roughly equally to the top 10. Without sharding, the top 10 would cluster in the shard that holds the
   best matches. The figure is the same for routing at write time and for an online split, so it comes from the merge,
   not from the migration.
2. **Channel-level fusion is much closer, but still short of the target.** It keeps hit@10 within 0.01 of the single
   store (0.985 vs 0.98; 0.91 vs 0.92), with recall@10 agreement of 0.64 to 0.67. The remaining gap comes from BM25 and
   graph scores, which use per-shard statistics. Raising the per-shard depth from 20 to 50 (one run at scale 0.5)
   moved agreement only from 0.71 to 0.72 and cost about 45% latency (p50 13 to 18.7 ms), so it is not worth it. On
   these results the default became `channel` (DECISIONS D17); `rrf` stays available for comparison.
3. **Sharded queries are slower at p50**: about 2.8x at 3k records (5.6 vs 15.7 ms) and about 2.1x at 12k (12.2 vs
   26.3 ms). Shards are searched one after another on one read worker per holder. Searching them on parallel threads
   in one process was measured about 6x slower (around 60 ms vs 10 ms) because the threads queue on the interpreter
   lock. The p95 is too noisy at 200 queries to compare. At 12k records, the sharded p95 was below the single store's
   in one run and above it in the other (see variance).
4. **Ingest throughput and write latency do not get worse.** Ingest throughput is the same within run-to-run noise. The
   sharded holder was 2 to 8% faster in three runs and 5% slower in one (181.4 vs 173.1 records/s). Write p50 is also
   equal within noise. Write p95 was lower when sharded in all four runs (by 1.8 to 5.4 ms). Smaller shard files are a
   plausible cause, but this was not isolated. The two-step control intent to s0 does not show up as a cost at this
   scale.
5. **Vector memory**: the total is unchanged, because all shards live in one process. The largest single matrix drops
   to about 45% (1.30 of 2.92 MiB; 5.22 of 11.71 MiB). That is the quantity the §7.3 "> 1 GiB" signal limits, and the
   one that matters once shards could be loaded or evicted independently. That is not built: today every queryable
   shard is kept open.
6. **Disk after an online split grows until a VACUUM.** At 3k records the file total is 44.4 MiB vs 29.5 MiB, and at
   12k it is 168 vs 115 MiB. Cleanup blanks and deletes the moved rows in s0 (`secure_delete`), but an existing s0 was
   created without `auto_vacuum`, so it keeps its free pages. New shard files use incremental auto-vacuum and are
   trimmed. Routing at write time costs only about 3% more disk than one store (32.1 vs 29.5 MiB; 118.7 vs 115.2 MiB).
   A VACUUM of s0 after a split is not implemented.
7. **Online split speed**: 553 to 571 records/s at 3k and 332 records/s at 12k, end to end through all seven states,
   including cleanup. The benchmark ran no concurrent writes during the split. Writes to the source shard wait during
   cutover, which holds the source shard's gate, and during each cleanup batch (up to 200 records).
   Copy and catch-up run without the gate.

## Not measured here

* §15 item 2, live latency under a 1M-record backfill. Also: the 100k and 1M grid, more than one holder, 60 domains,
  8 shards, and a real embedder. Everything here uses hash-256, so absolute recall and hit rates say nothing about a
  semantic embedder. The *agreement* numbers are the meaningful ones.
* §15 item 6, deletion purge time and the page-scan check. Deletion across shards is covered functionally by
  `mycelic/tests/test_ingest_shards.py` (deletions win during a split, and deletion survives a backup restore), but it
  is not timed.
* §15 items 7 and 8 (routing accuracy, independence). Queue lag. The `allowed_ids` filter vs over-fetch-and-filter.
* Query latency with a domain filter. The harness queries without one, which is the worst case: every shard is
  searched.
* p95 figures come from in-memory latency samples, not from `ingest_stage_metrics`, which stores no percentiles.

# Operation 100M — results (stage 1: 100K memories)

Status 2026-10-31: **the 100K stage passes every gate with real embeddings, verified by the Critic in 4 of 4 runs.
1M, 10M and 100M have not been run.** Labels: **verified** (Critic recomputed from raw artifacts), **published**
(someone else's claim), **proposed** (a plan), **unknown**.

## What was measured

- **Bank:** 100,000 memories, generated deterministically from seed `20261031`. The generator spec is
  [`bench/spec/corpus_spec.json`](bench/spec/corpus_spec.json).
  - The mix: 70K first-version facts; 10K updates that supersede them through the engine's own `supersede` path;
    5K paraphrased duplicates; 2K contradictions from a second source; 13K distractors that name an entity and
    attribute without a value (hard negatives). Twin entities differ by one name syllable.
  - Text: 14.4 MB. Text hash `29216d17…aaeb`.
- **Embeddings:** real `nomic-embed-text` (768-d) from local Ollama, no prefix, the same call the engine makes.
  Vector hash `c29ed0ae…ce95`.
- **Queries:** 2,000 queries, of which 1,590 have an answer: current fact, historical (`include_superseded`) and
  contradiction. 410 are unanswerable. Query embeddings are precomputed.
- **Engine path:** `ChatMemoryStore` (`insert_memories_bulk`, `supersede_bulk`, `upsert_entities_bulk`) and
  `MemoryRetriever::search`, with k=10, channels `VK` (the shipped default), `touch=false` and
  `with_sources=false`. No LLM, no reranker.
- **Timing scope:** wall time of `search()` in-process. Query embedding is excluded. This is comparable in scope
  to Hindsight's "engine-level, no API" numbers, but **not** to any LLM answer latency.
- **Machine:** Apple M4 (4P+6E cores), 16 GB RAM, macOS 15.7.4, rustc 1.99.1, local NVMe.
- **Source:** branch `operation-100m` = `f0ddff96` plus the changes listed in [`ARCHITECTURE.md`](ARCHITECTURE.md).
  The runs used the working tree that the stage-1 commit contains, unchanged (see "Reproduce").
- **Critic:** [`agents/critic.py`](agents/critic.py) checks each run independently of the harness.
  - It re-derives all 100K records and all 2,000 ground-truth sets in Python from the spec. That is a separate
    implementation from the Rust generator.
  - It checks the text and vector hashes against the SQLite file.
  - It checks that every relevant row exists, has the expected status, names the entity and states an expected
    value.
  - It recomputes every metric from raw per-query rows.
  - It checks that results are identical across concurrency levels.

## Verified: shipped engine vs bounded engine (same bank, same queries)

| | Shipped engine (round 1) | Bounded, nprobe 32 (round 2) | **Bounded-recall (round 3)** |
|---|---:|---:|---:|
| Vector candidates | exact scan, all rows in RAM | disk IVF, 1,264 lists, probe 32 | **disk IVF, 316 lists, probe 48** |
| SQLite reads / FTS terms | one connection / all terms | read pool / drop terms in >1% of rows | **read pool / drop terms in >1% of rows** |
| recall@10 (1,590 answerable) | 0.9247 | 0.8040 ✗ | **0.9237** |
| MRR@10 / nDCG@10 | 0.586 / 0.661 | 0.640 / 0.660 | **0.679 / 0.734** |
| historical (returned anywhere) | 0.963 | 0.953 | 0.963 |
| p50 / p95 / p99, serial | 10.7 / 31.9 / 43.8 ms | 3.5 / 4.5 / 7.8 ms | **10.0 / 12.6 / 17.2 ms** |
| successful QPS c1 / c2 / c4 / c8 | 72 / 68 / 65 / 54 | 274 / 374 / 487 / 532 | **97 / 188 / 287 / 313** |
| p99 at c8 | 375 ms ✗ | 37.9 ms | **86.1 ms** |
| errors | 0 | 0 | **0** |
| resident after index load | 1,038 MiB (10.5 KB/memory) | 159 MiB (1.33 KB/memory) | **161 MiB (1.4 KB/memory)** |
| peak RSS, query process | 1,081 MiB | 176 MiB | **315 MiB** |
| process CPU for the run | 495 s | 62 s | **118 s** |
| cold: first query incl. index load | 589 ms | 211 ms | **372 ms** |
| cold: full index load (profile) | 285 ms | 235 ms | **206 ms** |
| disk: SQLite bank + IVF file | 449 MB + 0 | 449 MB + 311 MB | **449 MB + 308 MB** |
| IVF build | — | 39.3 s | **4.3 s** (peak RSS 54 MB) |
| Critic | FAIL: concurrency, c8 p99, projected RSS | FAIL: recall regression (−12 pts), concurrency | **PASS** |

**Ingestion (verified, storage path only).** 11,667 memories/s through `insert_memories_bulk`: 10K rows per
transaction, FTS triggers on, 768-d vectors. Wall rate 10,596/s, 8.7 CPU-s, peak RSS 618 MB. The shipped write
path runs LLM extraction first, which dominates and is not measured here. Hindsight's published 1,200–1,500/s per
bank also excludes the LLM.

**"Cold" here means process-cold, not OS-cold.** A fresh process starts with empty engine caches, but the OS page
cache is not purged (`purge` needs sudo). A true cold-disk number is **unknown**.

**Repeatability (verified).** The round-3 configuration was re-run three times with the same command:

- recall@10 was 0.9237 every time (deterministic).
- Serial p50 was 9.7–9.8 ms, serial p99 14.0–17.2 ms, about 100 QPS.
- **c8 was noisy:** 225–323 QPS (2.25–3.27× serial), c8 p99 38.9–86.1 ms.
- An earlier ablation run of the same configuration scaled only 1.95×. Its timing is **void**: it overlapped
  another session's benchmarks (load average 13–16 on 10 cores, 02:10–02:13), so it is not evidence either way.

So concurrency headroom at 100K is real but thin. Treat it as the first thing to re-check at 1M.

## Diagnosis: why the shipped engine does not scale (verified by profile)

There are three bottlenecks, and they are coupled. Removing any single one left the others binding (ablation
below).

1. **Every embedding is resident, twice, and is scanned on every query.**
   - `store.index_rows` decodes each vector to `Vec<f64>` (6 KB at 768-d) and keeps it in `IndexState.rows`.
     `IndexData` keeps a second normalised f32 copy (3 KB).
   - The result is 10.5 KB resident per memory: about 10 GB at 1M, 100 GB at 10M.
   - `vector_rank` scans all rows on all cores for each query. Serial cost is linear in n, and concurrent queries
     fight for the same cores. QPS *fell* from 72 to 54 going from 1 to 8 workers.
2. **The keyword channel scores huge posting lists.**
   - `fts_match_string` ORs every content token, so common words ("live", "work", "use") match much of the bank.
     `bm25()` then scores all of those rows before `LIMIT 200`.
   - At 100K: FTS p50 4.4 ms, but p99 34 ms; keyword-only search p99 50 ms. This cost grows with the bank.
3. **All SQL shares one `Mutex<Connection>` per store.** The FTS query, entity lookups and hydration from
   concurrent searches queue on it.

Smaller, linear-but-cheap items:

- `query_entities` clones and substring-scans the whole alias map per query: 1.05 ms at 6,250 entities.
- Superseded predecessors never rank inside the top 10 natively. They are found only through the history
  attachment after the top k, as designed (historical recall@10 0.72; returned anywhere 0.96).

## Verified ablation (100K, real embeddings)

Every row was judged by the Critic against the shipped engine's recall
([`bench/results/ablation-100k-real/`](bench/results/ablation-100k-real/)).
These runs used concurrency 1 and 8 only, so their c8 numbers lack warm-up.

| Variant | recall@10 | serial p50/p99 | c8 QPS | B/memory resident |
|---|---:|---:|---:|---:|
| IVF 1,264 lists, probe 32 | 0.792 | 7.0 / 43.8 ms | 125 | 1,400 |
| IVF 1,264 lists, probe 128 | 0.911 | 8.9 / 50.5 ms | 88 | 1,387 |
| read pool only | 0.925 | 14.7 / 72.9 ms | 48 | 10,745 |
| FTS prune, 1% df | **0.936** | 14.3 / 49.9 ms | 89 | 9,550 |
| FTS prune, 5% df | 0.928 | 13.8 / 25.8 ms | 75 | 10,652 |
| IVF 316/probe 16 + pool + prune † | 0.873 | 9.8 / 26.6 ms | 280 | 1,404 |
| IVF 316/probe 32 + pool + prune † | 0.904 | 12.9 / 36.8 ms | 133 | 1,435 |
| IVF 316/probe 48 + pool + prune † | 0.924 | 13.1 / 52.7 ms | 123 | 1,378 |
| mean-centred IVF, 4 settings | 0.778–0.897 | — | — | — |

† Load-contaminated: these runs (finished 02:11–02:13) overlapped another session's six serial benchmark runs.
Their recall is deterministic and valid; their latency and QPS are void. The round-3 run and its three repeats
(02:20–02:23) were outside that window.

Findings:

- **IVF is the only component that bounds memory, and it is the only one that costs recall.** On real
  nomic vectors, holding recall within 1 point needs about 15% of the bank scanned per query.
- **On synthetic vectors IVF looked free.** It even raised recall there (0.95 vs 0.82), which is why the 100K
  gate uses real embeddings. **Synthetic-vector recall is not evidence about real retrieval quality.**
- **FTS pruning improves recall (+1.2 pts) and latency.** The dropped common terms were noise.
- **The read pool helps only after the other two bottlenecks are removed.**
- **Mean-centring the IVF lists was tried and rejected.** It lowered recall at every matched setting, and the code
  was reverted.

## Process note: a gate was too weak, and the loop exposed it

The Critic's first gates had only an absolute floor (recall@10 ≥ 0.80). Round 2 passed that floor while losing 12
points against the shipped engine.

- A non-regression gate was added: at most 1 point below the shipped engine on the same bank.
- Earlier rounds were re-judged from raw artifacts under the new gate, with the original verdicts kept in
  [`state/100k-real.json`](state/100k-real.json).
- The gate change is versioned in [`bench/configs/gates_stage.json`](bench/configs/gates_stage.json).

## Remaining failures and open risks

- **Concurrency margin is thin:** 2.25–3.27× at c8 across four clean runs. The suspected cause is not yet proven.
  - The bundled SQLite is compiled without `SQLITE_DEFAULT_MEMSTATUS=0`, so every SQLite allocation takes a
    process-wide mutex.
  - Candidate fix: build with `LIBSQLITE3_FLAGS=-DSQLITE_DEFAULT_MEMSTATUS=0`, or call
    `sqlite3_config(SQLITE_CONFIG_MEMSTATUS, 0)`. Then sample with
    [`bench/tools/sample_hotspots.sh`](bench/tools/sample_hotspots.sh).
- **Recall vs cost at 1M is unknown.** Probe 48 of 316 lists scans about 15% of the bank, so query cost is still
  linear in n, at about 1/7 of the exact scan.
  - At 1M the probe count needed for ≤1-point recall loss on real vectors is **unknown**.
  - Real 1M embeddings would take about 5.5 h of local embedding. A 1M run with synthetic vectors measures cost
    and memory, not real recall.
- **Resident metadata is still O(n):** 1.4 KB/memory of `String` columns and maps. That is ~14 GB at 10M, over
  this machine's budget.
- **Index refresh:** rows written after an IVF build are scanned exactly from RAM (tested). Nothing rebuilds the
  index automatically yet, and every write still rebuilds the whole `IndexData` snapshot.
- **The IVF build reads vectors back from SQLite through a spool file.** At 1M that needs about 3 GB of temporary
  disk.

## Capacity projection (proposed, from verified per-memory costs)

| Bank size | Disk (SQLite + IVF) | Resident (bounded engine) | On this Mac? |
|---|---:|---:|---|
| 1M | ~7.6 GB + 3 GB build spool | ~1.4 GB | **yes** — next stage, synthetic vectors |
| 5M (one Hindsight-sized bank) | ~38 GB | ~7 GB | no: over 25% RAM budget and most free disk |
| 10M | ~76 GB | ~14 GB | **no**: exceeds 53 GB free disk and RAM budget |
| 100M (20 × 5M) | ~760 GB | ~140 GB | no: distributed only |

A distributed plan therefore needs two engine changes before it is worth paying for hardware:

1. Shrink resident metadata: intern subject/speaker/chat/kind, store times as integers, avoid the per-row `String`
   copies.
2. Store IVF vectors as int8 codes with an f32 rerank, which quarters disk and scan bandwidth.

Then validate on this Mac with several 1M banks served by one process (bank-routing and cold/warm behaviour) before
any cloud run. **No 10M or 100M result exists for NeuralGraph.**

## Reproduce

```bash
cargo build --release -p operation-100m-bench
cargo test --release -p neuralgraph-memory --test vindex
# operation-100m/work/ holds the regenerable banks and embeddings and is gitignored
target/release/o100m texts --n 100000 --nq 2000 --out-dir operation-100m/work/100k
python3 -I operation-100m/bench/tools/embed_ollama.py operation-100m/work/100k/queries_text.jsonl operation-100m/work/100k/queries.f32
python3 -I operation-100m/bench/tools/embed_ollama.py operation-100m/work/100k/records.jsonl operation-100m/work/100k/records.f32
python3 -I operation-100m/agents/loop.py --task 100k-real --max-rounds 4
```

The loop records every command it ran, with arguments, environment, exit code and duration, in each round's
`evidence.json`. Round 3's run, for example:

```bash
O100M_VECTOR_INDEX=ivf O100M_IVF_PATH=operation-100m/work/100k/bank.nl316.ivf O100M_NPROBE=48 O100M_READ_POOL=1 O100M_KEYWORD_MAX_DF=0.01 \
  target/release/o100m run --bank 0 --n 100000 --seed 20261031 --db operation-100m/work/100k/bank.db \
  --queries operation-100m/bench/results/100k-real/queries.jsonl --qvec file:operation-100m/work/100k/queries.f32 \
  --concurrency 1,2,4,8 --out-dir operation-100m/bench/results/100k-real/round3-bounded_recall
```

Real embeddings depend on the local Ollama build of `nomic-embed-text`. A different build may change vectors, and
the vector hash will show it.

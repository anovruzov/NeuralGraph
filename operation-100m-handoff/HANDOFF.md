# Operation 100M — handoff (2026-10-31)

## Where things stand

- **Repository:** `~/NeuralGraph-current`. This is the clean clone; the other NeuralGraph checkouts were not
  touched.
- **Branch:** `operation-100m`. Commit `43937131` is on top of `f0ddff96` (`origin/main`). Nothing has been pushed
  or merged.
- **Stage 1 (100K memories, real `nomic-embed-text` vectors): passed and verified.** The Critic passed it in
  4 of 4 runs.
- **1M, 10M and 100M: not run.**

| Same 100K bank | Shipped engine | Bounded engine |
|---|---:|---:|
| recall@10 | 0.9247 | 0.9237 |
| serial p50 / p99 | 10.7 / 43.8 ms | 10.0 / 17.2 ms |
| QPS at 1 → 8 workers | 72 → 54 | 97 → 225–323 |
| resident bytes per memory | 10.5 KB | 1.4 KB |

- **Bounded configuration:** `vector_index=ivf`, `nlist=316`, `ivf_nprobe=48`, read pool on,
  `keyword_max_df=0.01`. It is opt-in; the shipped defaults are unchanged.
- **Tests:** 551 passed, 0 failed, 3 ignored on the tested snapshot (546 original plus 5 in `tests/vindex.rs`).

Full numbers, diagnosis and commands are in [`RESULTS.md`](RESULTS.md). The design is in
[`ARCHITECTURE.md`](ARCHITECTURE.md), the code audit in [`REPOSITORY_AUDIT.md`](REPOSITORY_AUDIT.md), and what can
be compared with Hindsight in [`HINDSIGHT_COMPARISON.md`](HINDSIGHT_COMPARISON.md).

## Read this first

- **A second Claude session was writing in this same checkout.** Its work is uncommitted and deliberately left
  out of `43937131`:
  - `bench/tools/run_channels.sh`
  - `bench/tools/score_channels.py`
  - `bench/results/channels-100k-real/`
  - a 4-line `O100M_CHANNELS` override in `bench/src/main.rs` (the only working-tree diff)

  Decide whether to keep it before anyone else edits these files.
- **Commit `635b399b` was amended to `43937131`** to remove the other session's `mixed` subcommand, which had been
  committed by mistake. That code is still in the working-tree `bench/src/main.rs`, uncommitted, along with its
  `O100M_CHANNELS` override.
- **One change has not been re-verified.** The committed raw per-query rows are gzipped, and `critic.py` was
  taught to read `.gz` after the verified runs. Re-running the Critic on the committed artifacts was interrupted,
  so it has not been confirmed. Run that check first (see "Reproduce").
- **Regenerable data lives in `operation-100m/work/` (gitignored, local only):**
  - the 100K bank (`bank.db`, 449 MB) and its IVF files
  - `records.f32` and `queries.f32` (embeddings, about 34 minutes to recreate with local Ollama)
  - a 100K synthetic-vector bank

  Deleting that folder means redoing the embedding step.

## Open problems (in priority order)

0. **Searches collapse while writes are happening** (measured by the other session, not re-run here;
   `bench/results/mixed-100k-real/`, uncommitted). Setup: 4 readers, plus 20 writes/s on a copy of the 100K bank.
   - Searches fell to 5 qps on the shipped engine and 9 qps on the bounded one, versus about 486 qps for the
     bounded engine with no writes.
   - p99 under writes: 1.3 s shipped, 2.2 s bounded.
   - Correctness held: 0 stale or retracted rows returned, and all new rows were findable.
   - Likely cause (not profiled yet): every write moves the store cache key, so the next search rebuilds the whole
     `IndexData` snapshot.
   - The other session is profiling it now. Fix this before the 1M run.
1. **The concurrency margin is thin.** At 8 workers the engine reached 2.25–3.27× its serial QPS across runs.
   (An earlier 1.95× run is void: it overlapped another session's benchmarks.)
   - Suspect: the bundled SQLite is built with memory statistics on, which puts a global allocator mutex on the
     path. Not yet proven.
   - Test: build with `LIBSQLITE3_FLAGS=-DSQLITE_DEFAULT_MEMSTATUS=0`, or call
     `sqlite3_config(SQLITE_CONFIG_MEMSTATUS, 0)`.
   - Profile with `bench/tools/sample_hotspots.sh`.
2. **Recall vs query cost at 1M is unknown.** The verified setting reads about 15% of the bank per query, so
   query cost is still linear in bank size.
   - At 1M, the synthetic-vector track measures cost and memory only. It does not measure real recall: synthetic
     vectors made the vector index look free, while real vectors lost 12 points.
   - Real 1M embeddings take about 5.5 h locally.
3. **Resident metadata is still O(n):** 1.4 KB per memory of `String` columns, about 14 GB at 10M.
   - Fix: intern subject, speaker, chat and kind; store times as integers.
4. **Vector index storage is f32.** Moving to int8 codes with an f32 rerank would quarter both disk use and scan
   bandwidth.
5. **The index is never rebuilt automatically.** Rows written after a build are scanned exactly, as a delta.
6. **Only process-cold starts were measured.** A true cold-disk number needs `sudo purge`.

## Next steps

1. Re-verify the committed gzipped artifacts with the Critic (no new runs needed).
2. Run the 1M stage on synthetic vectors: `python3 -I operation-100m/agents/loop.py --task 1m-synth`.
   - The task is already defined in `bench/configs/tasks.json`.
   - The shipped engine is skipped because its projected 9.7 GB resident memory exceeds the budget.
   - The 10M projections are advisory gates.
   - Needs about 8 GB of disk plus a 3 GB build spool, and about 1.4 GB of RAM.
3. Run the concurrency investigation (problem 1) before trusting any 8-worker numbers at 1M.
4. Do not attempt 10M or 100M on this Mac: about 76 GB of disk at 10M, with 53 GB free.
   - First make the metadata and int8 changes.
   - Then validate several 1M banks served by one process.
   - Only then write a priced distributed plan; paid infrastructure needs approval.
5. Still not started from the original mission:
   - NATS cross-owner retrieval and authorization (`integrations/nats/`)
   - the research-loop diagram (`assets/research-loop.svg` and `.png`)
   - `DEREK_TECHNICAL_BRIEF.md`

## Reproduce

```bash
cargo build --release -p operation-100m-bench
cargo test --release -p neuralgraph-memory --test vindex
python3 -I operation-100m/agents/loop.py --task 100k-real   # resumes from state/; status PASS, so it stops
```

To re-check one committed run with the Critic (reads `raw_queries.jsonl.gz`; needs `work/100k/bank.db`):

```bash
python3 -I operation-100m/agents/judge_variant.py operation-100m/bench/results/ablation-100k-real/repeat_bounded_recall_1 operation-100m/work/100k/bank.db operation-100m/bench/results/100k-real 100000
```

`judge_variant.py` overwrites that run's `critique.json`. Compare the result with the committed version
(`git diff`) before keeping it.

To rebuild the bank and embeddings from scratch, see "Reproduce" in [`RESULTS.md`](RESULTS.md). Every command the
loop ran, with its arguments, environment, exit code and duration, is recorded in each round's `evidence.json`.

## Rules carried forward

- Report latency only together with the Critic-recomputed recall it was bought at.
- Never compare engine-level timing with LLM answer latency, or a single bank with Hindsight's 20-bank fleet.
- Gates are versioned in `bench/configs/gates_stage.json`. When they tighten, earlier rounds are re-judged from raw
  artifacts, and the original verdicts are kept.
- Stage changes by explicit path; never use `git add -A`, and never force-push.

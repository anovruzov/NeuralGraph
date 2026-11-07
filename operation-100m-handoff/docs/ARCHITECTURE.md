# Operation 100M — architecture of the change and of the loop

## Engine change: bounded retrieval, opt-in (`crates/neuralgraph-memory`)

The shipped defaults are unchanged: `vector_index = "exact"`, no read pool, no term pruning. The 546 existing tests
and the golden fixtures exercise exactly the old paths. Every new path is opt-in through configuration.

| Change | Where | What it bounds |
|---|---|---|
| Disk-backed IVF vector index | `src/vindex.rs` (new) | resident memory and vector-scan cost |
| `RetrievalConfig.vector_index` / `ivf_path` / `ivf_nprobe`, `MemoryRetriever::build_ivf` | `src/retrieval.rs` | — |
| Covered rows load without embeddings (`index_rows_embeddings_after`) | `src/store.rs`, `src/retrieval.rs` | resident memory |
| WAL read-connection pool for search-path reads (`set_read_pool`) | `src/store.rs` | lock serialisation |
| FTS term pruning by document frequency (`set_keyword_max_df`) | `src/store.rs` | keyword-channel scored set |
| Bulk write helpers (`insert_memories_bulk`, `upsert_entities_bulk`, `supersede_bulk`) | `src/store.rs` | ingestion transactions |

Details of each change:

- **IVF build and file.** Spherical k-means runs on an evenly strided sample (deterministic, no RNG). Vectors are
  normalised and written once, grouped by list, into `<db>.ivf`, with memory ids in `<db>.ivf.ids`.
- **IVF query.** The query reads only the `ivf_nprobe` nearest lists, one positional read per list. Candidates are
  scored by the same cosine and the same `dot` kernel as the exact path. Probing every list reproduces the exact
  ranking (test `ivf_probing_every_list_matches_exact_ranking`).
- **Where the vectors live.** Vectors stay in the OS page cache, not in process RSS. Rows written after a build
  are scanned exactly from RAM as a delta (test `ivf_keeps_covered_embeddings_out_of_memory_and_scans_newer_rows_exactly`).
- **Fallback.** A missing or unreadable index falls back to the exact scan with a warning.
- **Read pool.** Read-only connections (`PRAGMA query_only=1`) are opened lazily and reused. They serve the FTS
  query, entity lookups, hydration and the index load. `cache_key` stays on the main connection, because
  `PRAGMA data_version` is per-connection.
- **Term pruning.** Query terms whose FTS document frequency exceeds the fraction are dropped. Document
  frequencies are cached per store revision. The rarest term is always kept, so a query never goes empty (test
  `keyword_pruning_drops_common_terms_and_keeps_the_rarest`).

Verified configuration at 100K: `vector_index=ivf`, `nlist=316` (√n), `ivf_nprobe=48`, read pool on,
`keyword_max_df=0.01` (see [`RESULTS.md`](RESULTS.md)). It is **not** made the default. The probe depth needed at
larger banks is unknown, and changing defaults would change the shipped retrieval behaviour that the
LoCoMo/LongMemEval numbers were measured on.

## The three-agent loop (`operation-100m/agents/`)

```
Query/task ─▶ PLANNER ─plan.json─▶ RETRIEVER ─evidence.json + raw artifacts─▶ CRITIC ─critique.json─┐
                ▲                                                                                  │
                └──────────────── FAIL: failures {code, detail, action_hint} ◀─────────────────────┤
                                                                     PASS ─▶ state/verified/<task>.json
```

- **Planner** (`loop.py: Planner`). Reads the task (`bench/configs/tasks.json`), the prior rounds and the last
  critique.
  - Data failures (hash, ground truth) → plan a rebuild.
  - Otherwise → choose the untried variant in `bench/configs/variants.json` whose `addresses` covers the most open
    failure codes.
  - When nothing applies → stop with `NEEDS_IMPLEMENTATION` or `UNRESOLVED`. It never retries a variant.
- **Retriever** (`loop.py: Retriever`). Runs the plan with the real engine binary: build bank, build IVF, queries,
  run, profile. It records every command with environment, exit code and duration, and states candidate claims,
  each pointing at an artifact.
  - Research tools: cached arXiv API queries and GitHub API lookups, deduplicated in `state/search_cache.json`.
  - The Hindsight primary-source extraction is in `sources/`.
- **Critic** (`critic.py`, `spec.py`). Shares no code with the Retriever's measurement path.
  - It re-derives the corpus and ground truth in Python.
  - It reads SQLite directly and recomputes every metric from raw per-query rows.
  - It checks determinism under concurrency, then applies the gates in `bench/configs/gates_stage.json`.
  - Every claim in `evidence.json` must match its artifact.
- **Limits.**
  - At most 4 rounds per task. State persists in `state/<task>.json`, and the latest round is mirrored to
    `operation-100m/{plan,evidence,critique}.json`.
  - Gates are versioned. When they tighten, earlier rounds are re-judged from raw artifacts, and the original
    verdict is kept.
  - The agents are deterministic programs, not LLM calls, so every decision is a rule that can be replayed.
    Implementing a new variant is an engineer's step between rounds.

## Harness (`operation-100m/bench/`)

- `o100m` (Rust, uses `neuralgraph-memory` directly) has these subcommands: `texts`, `queries`, `build`,
  `build-ivf`, `run`, `profile`.
- `spec/corpus_spec.json` is the deterministic counter-based corpus: every record and query is a pure function of
  `(seed, bank, index)`.
- `tools/embed_ollama.py` is a resumable embedder with retries and a matrix hash.
- `tools/ablate.sh` plus `agents/judge_variant.py` run one variant and have the Critic judge it against the
  shipped-engine baseline.
- `tools/sample_hotspots.sh` takes CPU samples of a concurrent run (for the open concurrency investigation).

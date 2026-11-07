# NeuralGraph repository audit (Operation 100M)

Audited commit: `f0ddff96242cb98faa421d2a2df8e38046ca7a1b` (`origin/main`, 2026-10-31, merge of PR #18).
Work branch: `operation-100m`, cut from that commit in the clean clone `~/NeuralGraph-current`. The other local
checkouts (`~/NeuralGraph`, `~/NeuralGraph-main`, `~/NeuralGraph-tesseract-operator-plugin-v1`) have uncommitted
work and were not touched. Untracked `.venv/` and `NeuralGraph/` in this clone are preserved.

Labels: **verified** = measured or run here, artifact in `bench/results/`; **published** = stated by the repo's own
docs, not re-run; **unknown** = not established.

## Baseline test suite — verified

`cargo test --workspace --release` on a pristine `git archive` export of `f0ddff96` (so no branch edit could leak in):
**59 test binaries, 546 passed, 0 failed, 3 ignored**, 702 s wall, 3,720 s user CPU, peak RSS 0.80 GB.
Log: `bench/results/baseline/cargo_test_workspace_f0ddff96.log`.

After the Operation 100M engine changes (branch working tree): **61 binaries, 551 passed (546 + 5 new in
`tests/vindex.rs`), 0 failed, 3 ignored**, 594 s wall. Log: `bench/results/baseline/cargo_test_workspace_branch.log`. (A first run in the working tree was aborted and
discarded because it compiled after an additive `store.rs` edit.)

## What the memory engine is

`crates/neuralgraph-memory` (14.2k lines of Rust): a per-user chat-memory service. One SQLite file per store
(WAL, FTS5), a background LLM extraction worker that turns messages into memories/entities/relations, a hybrid
retriever, and MCP/REST/dashboard front ends. It is a port of a Python package and is pinned by golden fixtures.

| Component | File | Status | Notes |
|---|---|---|---|
| Storage (memories, messages, entities, relations, links, jobs, audit) | `store.rs` | implemented, tested | one `Mutex<Option<Connection>>` per store: every SQL statement serialises on it |
| Vector channel | `retrieval.rs` `IndexData::vector_rank` | implemented, tested | exact brute-force cosine over an in-RAM f32 matrix; scans all rows per query on all cores |
| Keyword channel | `store.rs` `keyword_candidates` | implemented, tested | FTS5 `bm25()` over an OR of every content token, `ORDER BY s LIMIT 200` |
| Graph channel | `retrieval.rs` `graph_rank` | implemented, **off by default** | own comment: never helped on LoCoMo, cost up to 9 pts evidence recall |
| Fusion + priors | `retrieval.rs` `search` | implemented, tested | RRF; subject/entity boost; importance and recency multipliers; optional temporal prior (off) |
| Temporal logic / supersession | `store.rs` `supersede`, `history` | implemented, tested | superseded rows hidden unless `include_superseded`/window; predecessors found via `memory_links` |
| Evidence tracking | `memory_sources`, quotes, raw-message recall | implemented, tested | provenance from memory to source messages |
| Index refresh | `retrieval.rs` `get_index` | implemented | full load once, then incremental by `updated_at`; every change rebuilds the whole `IndexData` snapshot |
| Distribution / NATS | — | **absent** | no networking between stores in this crate |
| Authorization between owners | — | **absent** in this crate | `SECURITY.md` documents server-level controls only |

Research crate (`crates/neuralgraph-research`): Rust ports of the LoCoMo/LongMemEval/call-center harnesses and a
deterministic coordination simulator (`coordination/`), whose artifacts regenerate byte-identically (published in
`docs/BENCHMARKS.md`; not re-run here beyond the test suite).

## Methodological lessons from `docs/BENCHMARKS.md` — published, preserved

- Answer accuracy depends heavily on the judge: strict vs lenient judges differ by 20-30 points on the same answers,
  and about a quarter of strict misses were judge false negatives. Retrieval metrics are the number that describes
  the memory system; answer accuracy describes the reader.
- More retrieval machinery did not automatically help: the graph channel, k=5, image captions and hard subject
  routing all failed to improve evidence recall on LoCoMo.
- Several results are single-run / single-conversation / single-judge and are labelled as such.

This audit follows the same rule: everything below is retrieval-level (ranked evidence against known ground truth),
with no LLM in the loop and no judge.

## Scaling bottlenecks — verified by profile (`bench/results/profile-100k-synth/profile.json`)

100K memories, 768-d vectors, engine defaults, per-query medians over 300 queries:

| Stage | p50 | p99 | Scaling |
|---|---:|---:|---|
| search, vector channel only (exact scan) | 10.8 ms | 17.8 ms | O(n·d) per query, all cores per query |
| search, keyword channel only (FTS5) | 7.3 ms | 49.3 ms | O(postings of every query term) — common words match a large share of the bank |
| `query_entities` (alias map clone + substring scan) | 1.04 ms | 1.23 ms | O(entities) per query, clone under a mutex |
| `cache_key` (`PRAGMA data_version`) | 0.001 ms | | constant |
| hydrate 10 memories / entity ids for 100 | 0.05–0.07 ms | | constant |

Resident memory: **10.3 KB per memory** after the index loads (768-d). `index_rows` decodes each embedding to
`Vec<f64>` (6 KB) and keeps it in `IndexState.rows`, and `IndexData` keeps a second normalised f32 copy (3 KB), plus
eight `String` columns per row. Projection: ~10 GB at 1M (64% of this Mac's 16 GB), ~100 GB at 10M.

Cold start: full index load 1.34 s at 100K (`index_rows` 0.34 s, the rest decoding/normalising).

Ingestion (storage path only; the shipped path runs LLM extraction first, which dominates and is not measured
here): 9,650 memories/s through `insert_memories_bulk` at 100K (one transaction per 10K rows, FTS triggers on).

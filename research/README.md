# NeuralGraph research

This directory is the **public research track**: data, results and reports. Nothing here is
needed to run the assistant — if you came for local memory in Claude, you want
[`crates/neuralgraph-memory`](../crates/neuralgraph-memory/README.md) instead.

| | |
|---|---|
| [`datasets/`](datasets/) | Inputs: LoCoMo (10 conversations, 1,540 questions), LongMemEval Oracle and the call-centre set. |
| [`reports/`](reports/) | One write-up per experiment, plus the campaign report and the repository audit. |
| [`results/`](results/) | Per-question output for every run cited in [`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md). |
| [`coordination/artifacts/`](coordination/artifacts/) | The coordination track's pinned artifacts and their `SHA256SUMS`. |

The code is the Rust crate
[`crates/neuralgraph-research`](../crates/neuralgraph-research/): the Tesseract retrieval
engine (`src/retrieval/`), the multi-agent survival simulator (`src/coordination/`) and the
harnesses (one binary each in `src/bin/`, shared code in `src/benchmarks/`). It was ported
from the Python implementation the campaign ran on (last Python commit `eba2f370`) and is
pinned to it by golden fixtures; see [the crate README](../crates/neuralgraph-research/README.md).

---

## The one-paragraph summary

On LoCoMo, replacing flat retrieval with **per-agent routing plus question/reply pair
back-fill** moves end-to-end accuracy from **64.9% to 73.8%** on the same 282 questions
under the same judge. The +8.9 gain survives all four graders we tried (+11.3 Qwen
lenient, +9.6 Qwen strict, +4.6 substring; *p* < .001). Across conversations 1–5
(744 questions) the shipped configuration scores **72.2%**.

The result we think matters most to the field is not the gain. It is that the **same
answers score 13.5% to 64.9% depending only on who grades them** — judge model alone
moves a score ~12 points, prompt leniency ~30. Any memory benchmark number without a
named judge is unfalsifiable.

Full numbers and evidence classes: [`../docs/BENCHMARKS.md`](../docs/BENCHMARKS.md).
Full campaign: [`reports/REPORT.md`](reports/REPORT.md) and
[`reports/RESULTS_ALL.md`](reports/RESULTS_ALL.md).

## What did not work

Published alongside what did, because negative results are the expensive half of a
campaign: graph-neighbour expansion, listwise reranking, wider *and* narrower context,
an LLM router, scoring-formula fixes, reply-only pairs, pair-aware reranking, a larger
answer model, mega search (vector + BM25 + graph hop), and an LLM-built entity graph.
Mega search and the entity graph both **improved retrieval recall and hurt end-to-end
accuracy** — they flood the context with related non-answers. One report per experiment
in [`reports/`](reports/).

---

## Reproducing

Everything runs from the repository root with a Rust toolchain (1.89+). Build once:

```bash
cargo build --release -p neuralgraph-research
```

Point the harnesses at a local model server — LM Studio (default `http://127.0.0.1:1234`)
or Ollama — with the same variables the Python harnesses read:

```bash
export LLM_BASE_URL=http://localhost:11434
export LLM_MODEL=qwen2.5:7b-instruct
export EMBED_MODEL=nomic-embed-text
```

**Retrieval only — no LLM calls (cached embeddings: `research/results/emb_cache/`):**

```bash
MODE=combined cargo run --release -p neuralgraph-research --bin two-agent-eval
MODE=split    cargo run --release -p neuralgraph-research --bin two-agent-eval
cargo run --release -p neuralgraph-research --bin retrieval-eval
```

The runner's retrieval metrics (`metadata.retrieval_metrics` in every result file) need no
model either: `RETRIEVAL_ONLY=1` reads embeddings from the cache, skips reranking,
answering and judging, and writes the same JSON with only the retrieval metrics real. It is
a Rust addition; with it every committed file's metrics re-derive exactly, e.g.

```bash
RETRIEVAL_ONLY=1 RUN_NAME=check ONLY_CAT=single_hop RETRIEVAL_MODE=flat \
  cargo run --release -p neuralgraph-research --bin runner
```

**End to end, shipped configuration, single_hop (~40 min):**

```bash
RUN_NAME=my_run ONLY_CAT=single_hop RETRIEVAL_MODE=local_pairs \
  cargo run --release -p neuralgraph-research --bin runner
```

**Full benchmark, shipped configuration (~3.5 h):**

```bash
RUN_NAME=capstone ONLY_CONV=4,5,6,7,8,9 RETRIEVAL_MODE=local_pairs \
  OPEN_DOMAIN_KEEP_CONTEXT=1 OPEN_DOMAIN_FORCE_INFER=2 OPEN_DOMAIN_INFER_WORLD=1 \
  cargo run --release -p neuralgraph-research --bin runner
```

**Re-score result files under a different judge (~10 min):**

```bash
cargo run --release -p neuralgraph-research --bin rescore -- \
  --file flat_single_hop=research/results/flat_single_hop.json \
  --file local_pairs_single_hop=research/results/local_pairs_single_hop.json
```

Append `count` to print how many judge calls a re-score needs, without a model.

Without `--file` the rescorer uses the Python script's default list (two committed files and
four scratch files under `N5_SCRATCH_DIR`).

**The assistant pipeline on LoCoMo and LongMemEval (Track D in `docs/BENCHMARKS.md`):**

```bash
cargo run --release -p neuralgraph-research --bin chat-memory-locomo -- --help
cargo run --release -p neuralgraph-research --bin chat-memory-longmemeval -- --help
```

**Entity graph for the KG experiment, and the coordination track:**

```bash
cargo run --release -p neuralgraph-research --bin kg-extractor -- \
  --dataset research/datasets/locomo10.json --cache-dir research/results/kg_cache
cargo run --release -p neuralgraph-research --bin coordination -- benchmark --sweep 30 --format markdown
```

Runs write to `research/results/<RUN_NAME>.json`. Embedding and entity-graph caches
(`results/emb_cache/`, `results/kg_cache/`) regenerate on first use and are not
committed — the embedding cache alone is ~321 MB.

### Run controls

| Variable | Effect |
|---|---|
| `RUN_NAME` | Output file name under `research/results/` |
| `ONLY_CAT` / `ONLY_CONV` | Restrict to a question category or conversations |
| `MAX_QUESTIONS` | Cap the number of questions |
| `RETRIEVAL_MODE` | `flat` \| `hybrid` \| `graph` \| `local_pairs` \| `mega` |
| `OPEN_DOMAIN_*` | Open-domain prompt treatments (`KEEP_CONTEXT`, `FORCE_INFER`, `INFER_WORLD`) |
| `RETRIEVAL_ONLY` | `1`: no model calls, retrieval metrics only (Rust addition) |
| `EMB_CACHE_DIR` | Embedding cache directory (default `research/results/emb_cache`) |
| `LLM_REASONING_EFFORT` | Default `none`; disables Gemma/Qwen thinking |

### What the port reproduces

Measured on the Rust port against the committed results (see the crate README for how each
is pinned): every result file's `retrieval_metrics`, the H9 tables of `two-agent-eval`, the
`retrieval-eval` table, the judge-sensitivity substring and Gemma columns, the LongMemEval
50-question sample, and all nine coordination artifacts byte for byte. Model-dependent
numbers (accuracy under an LLM judge) cannot be re-derived without re-running the model.

---

## Reading the reports

The reports were written during the campaign (September 2026) and are kept **verbatim**
as the record of what was run. They therefore reference the old repository layout. The
mapping:

| Referenced as | Now lives at |
|---|---|
| `demo/runner.py`, `research/benchmarks/runner.py` | `--bin runner` (`crates/neuralgraph-research/src/bin/runner.rs`) |
| `demo/retrieval_eval.py`, `demo/two_agent_eval.py` | `--bin retrieval-eval`, `--bin two-agent-eval` |
| `docs/research/n5_rescore.py`, `research/benchmarks/rescore.py` | `--bin rescore` |
| `research/benchmarks/chat_memory_*.py` | `--bin chat-memory-locomo`, `--bin chat-memory-longmemeval` |
| `demo/results/*.json` | `research/results/` |
| `docs/research/*.md` | `research/reports/` |
| `NeuralGraph/tesseract.py` and the other engine modules (`NeuralGraph/research/retrieval/*.py`) | `crates/neuralgraph-research/src/retrieval/*.rs`, one module per Python module |
| `NeuralGraph/coordination/`, `NeuralGraph/research/coordination/` | `crates/neuralgraph-research/src/coordination/`, `--bin coordination` |
| `NeuralGraph/coordination/artifacts/` | `research/coordination/artifacts/` |
| `NeuralGraph/temporal_utils.py`, `NeuralGraph/llm_backend.py` | `crates/neuralgraph-research/src/{temporal_utils,llm_backend}.rs` |

The Python itself is in git history: the last commit containing it is `eba2f370`.

## Excluded from this repository

The December 2025 baseline (`maximal.json`, 66.7%) **is not published here**. That run
used the gold answer as an acceptance gate and the gold category label for routing; its
numbers are leaked and not comparable to anything below. Every result in `results/` is
from the leakage-free harness. The leaky run is described in
[`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md) only so the record is complete.

## A note on credentials

An earlier commit on `main` contained a live OpenAI API key in the benchmark runner. It
was removed from the working tree, but **it remains in git history and must be revoked**
at platform.openai.com. The runner reads `OPENAI_API_KEY` from the environment and falls
back to a local judge.

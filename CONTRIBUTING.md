# Contributing to NeuralGraph

Issues and pull requests are both welcome. This file is short on purpose — there are only
a few things you need to know that you could not guess.

## Setup

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph
cargo build
```

Everything is Rust: the memory server (`crates/neuralgraph-memory`) and the research track
(`crates/neuralgraph-research`). There is no Python in the repository.

No model server is needed to develop or to run the tests: `--fake-llm` substitutes a
deterministic stand-in everywhere, and every test uses it.

## Tests

```bash
cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings
cargo test -p neuralgraph-memory
cargo test -p neuralgraph-research --release
```

CI runs all of the above (the research job on macOS, see below). Tests must stay offline and deterministic. If a change needs a
model, it needs a fake in
[`crates/neuralgraph-memory/src/testing.rs`](crates/neuralgraph-memory/src/testing.rs) too.

The memory server is a port of a Python implementation, and
[`tests/golden.rs`](crates/neuralgraph-memory/tests/golden.rs) holds it to that
implementation's recorded behaviour: prompts byte for byte, database contents after an
ingest, retrieval scores, MCP and REST responses. A deliberate behaviour change therefore
fails those tests; update the affected fixture in
`crates/neuralgraph-memory/tests/golden/` in the same commit and say why in the message.
Those fixtures were recorded from the Python implementation at `f4311102` by
`crates/neuralgraph-memory/tests/golden/generate.py`, which was removed with the rest of
the Python; it is recoverable from git history (`git show eba2f370:crates/neuralgraph-memory/tests/golden/generate.py`).
Change a fixture only deliberately, by hand or by porting that script, never to make a test
pass.

The research crate is held to the Python research code the same way:
`crates/neuralgraph-research/tests/golden/*.json` were recorded from the Python at
`eba2f370` (the last commit with Python in it) by the `gen_*.py` scripts that sat next to
them, recoverable from git history at the commit before the Python was removed (see
[the crate README](crates/neuralgraph-research/README.md#golden-fixtures)). The same rule
applies: fixtures change only deliberately, and the commit says why. Fixtures that pin
numpy's float32 arithmetic are exact on macOS (where numpy used Accelerate and the crate
calls the same routines) and checked with tolerances elsewhere, which is why CI runs the
research job on macOS.

## Where code goes

The assistant and the research are kept apart, and the crate graph enforces it:

- **`crates/neuralgraph-memory/`** — the assistant. Self-contained: it must never depend on
  the research crate, in any dependency kind.
- **`crates/neuralgraph-research/`** — the retrieval engine (`src/retrieval/`), the
  coordination simulator (`src/coordination/`) and the benchmark harnesses (`src/bin/`,
  `src/benchmarks/`). It depends on the memory crate (shared Python-compatibility helpers,
  the date parser, the model backend, and the memory library its `chat-memory-*` harnesses
  drive), never the other way round.
- **`research/`** — data, results, reports and the coordination track's pinned artifacts.

The organizational-memory service, Mycelic, lives in its own repository:
[PNurmanM/MycelicLabs](https://github.com/PNurmanM/MycelicLabs). It keeps its own copies of the
coordination contracts, the MCP protocol core and the tokenizer, so changes here never need to be
mirrored there.

[`crates/neuralgraph-memory/tests/crate_boundary.rs`](crates/neuralgraph-memory/tests/crate_boundary.rs)
checks the rule from `cargo metadata` on every test run:

```bash
cargo test -p neuralgraph-memory --test crate_boundary
```

## The one rule for benchmark changes

**If you change retrieval, bring a benchmark run — and name the judge.**

The campaign's most reusable finding is that the same answers score 13.5% to 64.9%
depending only on who grades them (see
[`research/reports/N5_judge_sensitivity.md`](research/reports/N5_judge_sensitivity.md)).
An accuracy number without its judge model and judge prompt cannot be compared to
anything, including itself a month later.

So a retrieval PR should say: which harness, which questions, which retrieval mode, which
judge model, which judge prompt, and the before/after on the *same* questions.
[`research/README.md`](research/README.md) has the commands.

Negative results are publishable here — roughly half the reports in
[`research/reports/`](research/reports/) are experiments that did not work, and they were
worth the compute.

## Results and artifacts

- Per-question output for cited runs belongs in `research/results/`.
- Caches (`emb_cache/`, `kg_cache/`) and scratch output are gitignored — they regenerate.
- Leaked runs are not published. If a harness change can see the gold answer or the gold
  category label, the numbers from it do not go in the repository.

## Style

Match the file you are editing. The codebase favours explicit names, docstrings that say
*why* rather than *what*, and comments only where the reason is not obvious from the code.

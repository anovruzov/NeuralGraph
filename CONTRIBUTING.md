# Contributing to NeuralGraph

Issues and pull requests are both welcome. This file is short on purpose — there are only
a few things you need to know that you could not guess.

## Setup

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph
cargo build                                   # the memory server (Rust 1.89+, a C compiler)

# only for the research track, which is still Python:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

No model server is needed to develop or to run the tests: `--fake-llm` substitutes a
deterministic stand-in everywhere, and every test uses it.

## Tests

```bash
cargo fmt --all --check && cargo clippy --all-targets -- -D warnings
cargo test                                    # memory server: 139 tests, a few seconds
python -m pytest NeuralGraph/tests -q         # research track: 116 tests, no GPU, no network
```

CI runs all of the above. Tests must stay offline and deterministic. If a change needs a
model, it needs a fake in
[`crates/neuralgraph-memory/src/testing.rs`](crates/neuralgraph-memory/src/testing.rs) too.

The memory server is a port of a Python implementation, and
[`tests/golden.rs`](crates/neuralgraph-memory/tests/golden.rs) holds it to that
implementation's recorded behaviour: prompts byte for byte, database contents after an
ingest, retrieval scores, MCP and REST responses. A deliberate behaviour change therefore
fails those tests; update the affected fixture in
`crates/neuralgraph-memory/tests/golden/` in the same commit and say why in the message.
`generate.py` in that directory regenerates every fixture from the Python reference (it
extracts the last Python release of the package from git, so it works on any checkout).

## Where code goes

The assistant and the research are kept apart, and the import graph enforces it:

- **`crates/neuralgraph-memory/`** — the assistant (Rust). Self-contained: it does not call
  into the Python tree at all.
- **`NeuralGraph/research/`** — the retrieval engine and the coordination simulator.
  Free to depend on each other.
- **`NeuralGraph/chat_memory/`** — the frozen Python reference of the assistant. It exists
  only because `research/benchmarks/chat_memory_*.py` import it; do not develop features
  there. It may import `llm_backend` and `temporal_utils`, and nothing else.
- **Shared Python** — `llm_backend.py` and `temporal_utils.py`, used by the research track
  and the reference package.

The organizational-memory service, Mycelic, lives in its own repository:
[PNurmanM/MycelicLabs](https://github.com/PNurmanM/MycelicLabs). It keeps its own copies of the
coordination contracts, the MCP protocol core and the tokenizer, so changes here never need to be
mirrored there.

A quick check that the Python reference still keeps out of the research code:

```bash
python -c "
import NeuralGraph.chat_memory, sys
leaked = [m for m in sys.modules if m.startswith('NeuralGraph.research')]
assert not leaked, leaked
print('clean')"
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

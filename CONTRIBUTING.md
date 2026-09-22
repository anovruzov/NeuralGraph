# Contributing to NeuralGraph

Issues and pull requests are both welcome. This file is short on purpose — there are only
a few things you need to know that you could not guess.

## Setup

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

No model server is needed to develop or to run the tests: `--fake-llm` substitutes a
deterministic stand-in everywhere, and every test uses it.

## Tests

```bash
python -m pytest NeuralGraph/tests -q        # 222 tests, ~4 s, no GPU, no network
```

Tests must stay offline and deterministic. If a change needs a model, it needs a fake
in [`NeuralGraph/chat_memory/testing.py`](NeuralGraph/chat_memory/testing.py) too.

## Where code goes

The assistant and the research are kept apart, and the import graph enforces it:

- **`NeuralGraph/chat_memory/`** — the assistant. It may import `llm_backend` and
  `temporal_utils`, and nothing else from the wider package.
- **`NeuralGraph/research/`** — the retrieval engine and the coordination simulator.
  Free to depend on each other; must never be imported by the assistant.
- **Shared** — `llm_backend.py` and `temporal_utils.py` only. Think before adding a third.

A quick check that the separation still holds:

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

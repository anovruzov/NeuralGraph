# NeuralGraph research

This directory is the **public research track**. Nothing here is needed to run the
assistant — if you came for local memory in Claude, you want
[`NeuralGraph/chat_memory/`](../NeuralGraph/chat_memory/README.md) instead.

| | |
|---|---|
| [`benchmarks/`](benchmarks/) | The harnesses. End-to-end LoCoMo runner, retrieval-only evaluators, judge rescorer. |
| [`datasets/`](datasets/) | Inputs: LoCoMo (10 conversations, 1,540 questions) and the call-centre set. |
| [`reports/`](reports/) | One write-up per experiment, plus the campaign report and the repository audit. |
| [`results/`](results/) | Per-question output for every run cited in [`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md). |

The code these harnesses drive lives in
[`NeuralGraph/research/retrieval/`](../NeuralGraph/research/retrieval/) (the Tesseract
retrieval engine) and
[`NeuralGraph/research/coordination/`](../NeuralGraph/research/coordination/) (the
multi-agent survival simulator).

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

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Point the harness at a local model server — LM Studio (default `http://127.0.0.1:1234`)
or Ollama:

```bash
export LLM_BASE_URL=http://localhost:11434
export LLM_MODEL=qwen2.5:7b-instruct
export EMBED_MODEL=nomic-embed-text
```

**Retrieval only — no LLM calls, ~4 minutes for all 1,540 questions:**

```bash
MODE=combined python research/benchmarks/two_agent_eval.py
python research/benchmarks/retrieval_eval.py
```

**End to end, shipped configuration, single_hop (~40 min):**

```bash
RUN_NAME=my_run ONLY_CAT=single_hop RETRIEVAL_MODE=local_pairs \
  python research/benchmarks/runner.py
```

**Full benchmark, shipped configuration (~3.5 h):**

```bash
RUN_NAME=capstone ONLY_CONV=4,5,6,7,8,9 RETRIEVAL_MODE=local_pairs \
  OPEN_DOMAIN_KEEP_CONTEXT=1 OPEN_DOMAIN_FORCE_INFER=2 OPEN_DOMAIN_INFER_WORLD=1 \
  python research/benchmarks/runner.py
```

**Re-score any result file under a different judge (~10 min):**

```bash
python research/benchmarks/rescore.py        # edit FILES at the top
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
| `LLM_REASONING_EFFORT` | Default `none`; disables Gemma/Qwen thinking |

---

## Reading the reports

The reports were written during the campaign (September 2026) and are kept **verbatim**
as the record of what was run. They therefore reference the old repository layout. The
mapping:

| Referenced as | Now lives at |
|---|---|
| `demo/runner.py` | `research/benchmarks/runner.py` |
| `demo/retrieval_eval.py`, `demo/two_agent_eval.py` | `research/benchmarks/` |
| `demo/results/*.json` | `research/results/` |
| `docs/research/*.md` | `research/reports/` |
| `NeuralGraph/tesseract.py` and the other engine modules | `NeuralGraph/research/retrieval/` |
| `NeuralGraph/coordination/` | `NeuralGraph/research/coordination/` |

## Excluded from this repository

The December 2025 baseline (`maximal.json`, 66.7%) **is not published here**. That run
used the gold answer as an acceptance gate and the gold category label for routing; its
numbers are leaked and not comparable to anything below. Every result in `results/` is
from the leakage-free harness. The leaky run is described in
[`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md) only so the record is complete.

## A note on credentials

An earlier commit on `main` contained a live OpenAI API key in the benchmark runner. It
was removed from the working tree, but **it remains in git history and must be revoked**
at platform.openai.com. The harness now reads `OPENAI_API_KEY` from the environment and
falls back to a local judge.

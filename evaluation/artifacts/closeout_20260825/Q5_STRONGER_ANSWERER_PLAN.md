# Q5 — Frozen stronger-answerer experiment (PREPARED, NOT EXECUTED)

**What this tests, and what it does not.** Replacing `qwen2.5:7b-instruct` with a
stronger answerer measures **model capability**. It is **not** a new NeuralGraph
retrieval mechanism, and no result from it may be described as a NeuralGraph
improvement. Retrieval, evidence, ordering, question membership and answer schema
are all held frozen precisely so the only free variable is the model.

The unpaid half of this question — `qwen3:8b` against `qwen2.5:7b-instruct` on
deterministic metrics — is executed and reported in
`Q5_QWEN3_8B_COMPARISON.md`. This document specifies the **paid** half, which
requires a judge and is therefore blocked.

## Frozen configuration

| | |
|---|---|
| answerer | `qwen3:8b`, digest `500a1f067a9f`, Q4_K_M, 8.2B params |
| ollama | 0.32.13 · context 40,960 · embedding length 4,096 |
| thinking mode | `/no_think` (frozen on deterministic smoke: identical quality, p50 24.5s vs 30.6s, 435 vs 557 output tokens) |
| temperature | 0 |
| seed | not exposed by the Ollama chat endpoint; determinism rests on temperature 0 |
| prompt hash | `5e923dc613887b80` (v1, unchanged) |
| retrieval | frozen additive — recorded excerpts ∪ entity/BM25 top-5, relevance-first |
| index digest | `993cf7e3749b8f00` |
| eval set | validation, digest `8077c40ba2e7cff2`, 60 question ids |
| corpus digest | `916ca308d5e41cba…` |
| judge | `gpt-4o`, same judge as the frozen baseline |

## Request accounting

| | count |
|---|---|
| generation requests | **0 new** — all 60 answers already cached locally, unpaid |
| judge requests | **60** (1 per question; identical answers reuse the cached verdict) |
| estimated judge input tokens | ~12,000 |
| estimated judge output tokens | ~600 |
| **maximum estimated cost** | **~$0.036** |

The cost is trivial. The blocker is the ledger, not the money: 48 paid requests
are already consumed against a 40-request ceiling whose 75% stop point is 30, and
`BUDGET_LEDGER.json` records `paid_calls_permitted: false`. The governor refuses
before issuing a request. **A human must raise the ceiling explicitly.**

## Acceptance criteria

Judge only the answers that differ from the cached `qwen2.5` answer; reuse the
existing verdict for byte-identical answers, which is both cheaper and removes
judge nondeterminism from the unchanged rows.

Accept the stronger answerer only if:

1. judged accuracy increases by **at least 12 questions** — the minimum
   detectable effect established in `Q8_POWER_ANALYSIS.md`. Anything smaller is
   indistinguishable from judge noise at n=60 and must not be called an
   improvement;
2. operational error rate stays 0%;
3. unsupported-item rate does not rise materially;
4. question membership, retrieval artifacts and denominators are unchanged.

Criterion 1 is deliberately severe. It is the honest consequence of the power
analysis, and it is the reason a cheap paid run is still not worth issuing
casually.

## Resume command

```bash
# 1. a human raises the ceiling in BUDGET_LEDGER.json (paid_calls_permitted: true)
# 2. then, from the repo root:
export OPENAI_API_KEY=...        # never logged, never committed
/opt/homebrew/bin/python3.11 -m evaluation.improvement_loop.batch validation \
    --size 5 --concurrency 2 --tag qwen3_judged
```

Generation is already cached and resumable; only the judge calls are new. The
batch runner checkpoints after every question via temp-file-and-rename, so an
interruption loses nothing.

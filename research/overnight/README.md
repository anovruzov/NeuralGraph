# How to test this branch (memory system, LongMemEval and LoCoMo)

This is the reproduction guide for the 2026-10-28/29 accuracy campaign. The narrative is in `REPORT.md`, every run is a row in
`LEDGER.md`, the scripts are in `scripts/`. Everything below was run on one Apple M5 Max with 48 GB, LM Studio on `:1234`.
Per-question result rows are written to `.context/overnight/results/` and are not committed.

## 1. What shipped

- Crate defaults (`neuralgraph-memory`): `context_quotes = 1` (c3899d07), `context_max_chars = 6000` and
  `context_raw_messages = 3` (three episodic user turns; c03f3efc).
- `memory_answer` (3fd21804): the routed reader as product code. CLI `neuralgraph-memory answer "<q>" [--today YYYY-MM-DD] [--json]`,
  MCP tool `memory_answer`, REST `POST /api/answer`. Concise prompt; count, duration and date-difference questions (`COMPUTE_RE`)
  go to the compute prompt with thinking (2500-token cap); a concise "I don't know" is re-asked once with the compute prompt.
- Headline, LongMemEval Oracle, three stratified 50-question draws (150 questions), official LongMemEval judge prompts run by
  Gemma-4-e4b, one question = 2 points, original pipeline = plain concise reader on the old 3600-character block:

| draw | role | ingest | original | routed (shipped) |
|---|---|---|---:|---:|
| seed 1 | tuning set (`lme_base2`) | concurrency 2 | 50 | 74 |
| seed 2 | held-out | deterministic | 44 | 62 |
| seed 3 | held-out | deterministic | 62 | 72 |
| mean | 150 questions | | 52 | **69** |

Pooled over the 100 held-out questions the routed protocol beats the original 22:8 on the official judge and 20:7 on strict
(REPORT sec. 8). Seed 1 chose the prompts and the routing, so its 74 is optimistic; the held-out mean is 67. The product path
(`memory_answer`) matched the harness on seed 1: official 74 vs 74, strict 74 vs 68 (3:0), 3.45 s mean per answer incl. retrieval
(LEDGER `PRODUCT-answer`). LoCoMo, sessions 1-8 of conversations 0-3 (168 questions): routed vs previous protocol strict 18 wins : 12
losses, i.e. neutral to mildly positive. Read sec. 4 before comparing any of these with vendor numbers.

## 2. Prerequisites (as used)

- LM Studio models, all pinned (a JIT-loaded model is unloaded after 60 min idle and fails with HTTP 400 under load):
  `qwen3.8-9b-mlx` (reader and extractor, loaded at 16384 context; 8192 overflows on assistant-heavy batches),
  `google/gemma-4-e4b` (judge), `text-embedding-nomic-embed-text-v1.5`, optional `qwen/qwen3.6-35b-a3b` (second judge, and the
  "accuracy mode" thinker).
- 48 GB limits: unload Gemma before loading the 35B (`lms unload google/gemma-4-e4b`); `qwen3.8-27b` blocks the 35B and is not needed.
- No builds or other GPU jobs during an ingest: it slowed the first baseline from ~1.8 h to 4.06 h.

```bash
lms load qwen3.8-9b-mlx --context-length 16384 -y      # check `lms load --help` for your lms version's flag names
lms load google/gemma-4-e4b -y
lms load text-embedding-nomic-embed-text-v1.5 -y
export LLM_NO_THINK_PREFILL=1 LLM_MODEL=qwen3.8-9b-mlx   # mandatory: without it Qwen thinks on every call (30 s/msg vs 6)
cargo build --release                                     # binaries land in target/release/
mkdir -p .context/overnight/db .context/overnight/results
```

Datasets are in the repo: `research/datasets/longmemeval/longmemeval_oracle.json`, `research/datasets/locomo10.json`.

## 3. Reproduce the headline (run from the repo root; repeat for `SEED` = 1, 2, 3)

```bash
SEED=2; R=.context/overnight/results; D=.context/overnight/db/lme_seed$SEED
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py

# 1. Deterministic ingest, one in-flight request (~2.5 h per seed). Do not raise --concurrency/--parallel.
./target/release/chat-memory-longmemeval --n 50 --seed $SEED --concurrency 1 --parallel 1 --db-dir $D --out $R/lme_seed${SEED}_retrieval.json

# 2. Context dumps: crate defaults (quotes 1, 6000 chars, 3 raw user turns), and the "original" block
./target/release/chat-memory-context lme --db-dir $D --n 50 --seed $SEED --out $R/lme_seed${SEED}_default_ctx.json
NEURALGRAPH_EXP=context_quotes=0,context_raw_messages=0,context_max_chars=3600 \
  ./target/release/chat-memory-context lme --db-dir $D --n 50 --seed $SEED --out $R/lme_seed${SEED}_plain_ctx.json

# 3. Answer and judge (Gemma strict, strict_v2, lenient_v2, official LongMemEval prompts)
python3 $A --contexts $R/lme_seed${SEED}_plain_ctx.json --out $R/lme_seed${SEED}_plain_concise_acc.json \
  --prompt concise --extra-judges --official-judge --threads 4 --label "seed$SEED original"
python3 $A --contexts $R/lme_seed${SEED}_default_ctx.json --out $R/lme_seed${SEED}_routed_acc.json \
  --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think \
  --extra-judges --official-judge --threads 2 --label "seed$SEED routed"

# 4. Paired comparison: wins:losses, exact McNemar p, bootstrap CI
python3 $C $R/lme_seed${SEED}_plain_concise_acc.json $R/lme_seed${SEED}_routed_acc.json --md \
  --metric strict,strict_v2,lenient_nonidk,official
```

Expected: seed 2 official 44 -> 62 (13:4, p 0.049), strict_v2 50 -> 72 (12:1, p 0.003); seed 3 official 62 -> 72 (9:4), strict 54 -> 70
(10:2, p 0.039). The full chain is `scripts/after_seed2.sh` and `scripts/after_seed3.sh`. A result within about 4 points of these is a
reproduction (ingest floor +-2 in deterministic mode, plus 1-2 questions of reader re-run variation from LM Studio batching).

Product-path check (one question, then all 50 via `scripts/parity_answer.sh`, which copies the databases first):

```bash
./target/release/neuralgraph-memory answer --db $D/<question_id>.db --today 2024-05-30 --json "How many days passed between ...?"
# JSON: text, routed, cascaded, calls, seconds
```

## 4. Measurement protocol vs. published vendor numbers

| | this branch | published vendor numbers (Zep, Mem0, Mastra, others) |
|---|---|---|
| LongMemEval variant | Oracle (evidence sessions only); easier: GPT-4o 0.87-0.92 Oracle vs 0.61-0.64 S (LongMemEval paper) | mostly S (~115k tokens per question) |
| Sample | 50 per draw; 150 total, 100 held-out; 1 question = 2 points | all 500 |
| Reader | local Qwen3.8-9B | GPT-4o, GPT-4o-mini, GPT-5-mini, GPT-5, Gemini-3-Pro |
| Judge | official LongMemEval prompts run by local Gemma-4-e4b; rejects a bare "I don't know" on abstention (3 of 4 rows), costing up to 8 points | official prompts run by GPT-4-class models (GPT-4o; GPT-5 in Mem0's OSS runs; GPT-OSS-120B in Hindsight) |
| LoCoMo | conversations 0-3, sessions 1-8 (168 questions), local strict judges | 10 conversations, LLM judge, lenient (GPT-4o-mini in the Mem0 paper) |
| Retrieval depth | 15 memories + source quotes + 3 raw user turns, about 1,100 tokens | Mem0: top-200 memories, ~6.8k tokens; Zep: 20 facts/entities, ~1.6k tokens (Zep paper); Mastra: whole observation log, ~30k tokens |

Sources: `hypotheses/R3_competitors.md` secs. 1-3, 6; vendor figures are vendor-reported and were not re-run.

Consequence. Our official-judge 69 (mean of 74 / 62 / 72) is in the band of GPT-4o-mini-class systems on S: Zep 63.8 (gpt-4o-mini)
and 71.2 (gpt-4o). The 85-95 figures (Mem0 94.4 with GPT-5, Mastra 94.9 with gpt-5-mini and 84.2 with gpt-4o) are reader-driven:
Mastra's own gpt-4o to gpt-5-mini swap is +10.6 on identical memories, and the Mem0 extractor swap costs only 1.2-2.4.

**ESTIMATE, not a measurement**: under their setup (frontier reader, GPT-4-class judge, abstention credited) this pipeline would
score about **88 +-5** on Oracle. Derivation: 69 + 4 to 8 (abstention credit: 4 abstention rows = 8 points, Gemma rejected 3 of 4) +
10 to 17 (frontier reader: +10.6 gpt-4o to gpt-5-mini, Mastra; +17 GPT-4o vs Llama-8B on Oracle, LongMemEval paper) = 83 to 94, midpoint
about 88; +-3 for the judge model is inside the +-5. It assumes our memories hold up under a better reader (evidence reaches the
context on 83-96% of questions per draw; a 20-question Claude Sonnet probe on identical contexts scored strict_v2 75 vs 55 for the 9B,
a probe, not a result). It is for Oracle; S is harder for every system, which would push the figure down. Testing it is sec. 5.

## 5. Apples-to-apples version

- All 500 questions: `--n 0` on both `chat-memory-longmemeval` and `chat-memory-context lme` (~25 h ingest, same
  `--concurrency 1 --parallel 1`; use `--resume` after an interruption). For the S variant pass `--file <longmemeval_s.json>`
  (not in this repo; ingest time for ~115k-token haystacks has not been measured).
- Frontier judge: `answer_eval.py --judge-model <id> --base-url <url>`. Limits of the harness as written: it sends no API key and
  uses one `--base-url` for the reader and the judge. So answer locally first, then regrade without re-answering:
  `--regrade-from <acc.json> --judge-model <id> --base-url <OpenAI-compatible URL>`; a hosted endpoint needs a keyless local gateway
  that injects the key. The harness also sends `reasoning_effort: none`, which some hosted endpoints reject. Untested here.
  Keep the Gemma and the frontier-judge columns side by side.
- All 10 LoCoMo conversations (~3 h): for N in 0..9 run `chat-memory-locomo ingest --db .context/overnight/db/locomo_cN.db --conv N
  --sessions 8 --batch-size 2 --concurrency 1 --parallel 1`, then `chat-memory-context locomo --db ... --conv N --sessions 8 --out ...`, then
  `answer_eval.py` with the routed flags from sec. 3 (without `--official-judge`; it is LongMemEval-only). Raise `--sessions` to
  cover whole conversations; sessions 1-8 is what the campaign used (`scripts/locomo_c3.sh`).
- Report per row: dataset variant, n, reader, judge model and prompt set, ingest mode, and the IDK count next to any lenient number.

## 6. Gotchas

- Deterministic ingest only (`--concurrency 1 --parallel 1`, ~1.5x wall time). Noise floor: +-2 points at concurrency 1; at the
  default concurrency 2 two same-config ingests of the same 50 histories scored strict 42 vs 54 (+-12) and differ on 41 of 50 questions.
- `LLM_NO_THINK_PREFILL=1` for every harness and `neuralgraph-memory` run; the thinking call omits the prefill by itself.
- Pin the reader, judge and embedder with `lms load ... -y`; reload the reader at 16384, not 8192.
- Lenient columns credit "I don't know"; read `lenient_nonidk` or `strict_v2` and the official judge instead.
- Reader-side re-runs on identical contexts differ by 1-2 questions; treat deltas under 2 questions as noise.
- Held-out before claiming: seeds 2 and 3 were never used to choose prompts or routing. The morning protocol (quotes + cascade at
  3600 characters) looked like +8 strict on seed 1 and was -4 strict on both held-out draws. A fourth draw (seed 4) was stopped
  at ~15/50 questions ingested (`lme_seed4_ingest ... --resume`, then `scripts/after_seed4.sh`).
- Do not read the 35B accuracy mode as a gain: +12 strict on seed 1, +3 strict and +1 official pooled over the held-out draws, ~3x latency.
- Do not compare the strict, lenient or `strict_v2` columns with anyone's number; only the official-prompt column is on the public prompts.

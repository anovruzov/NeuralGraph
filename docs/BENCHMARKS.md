# NeuralGraph Benchmarks

One page for every benchmark result across the project's three research
tracks, with the source commit and evidence class for each number.
Compiled 2026-09-06 during the repository audit.

> **Read the labels carefully.** The LoCoMo category constants inherited
> from the December code swap single-hop and multi-hop (dataset category
> 1, n=282, averages 3.13 evidence messages per question; category 4,
> n=841, averages 1.07). Retrieval tables below use the labels **as
> reported** in the campaign docs; PR #1 carries the corrected mapping.
> Aggregates are unaffected — only which row is called what.

---

## Track A — Coordination: capability survival (Anar)

Source: `NeuralGraph/coordination/artifacts/` at `13a1729`
(branch `claude/session-analysis-continuation-sm1a1t`); the artifacts are now committed at
[`research/coordination/artifacts/`](../research/coordination/artifacts/) and regenerate
byte-identically from the Rust port (`cargo run --release -p neuralgraph-research --bin
coordination -- experiment|benchmark [--sweep 30]|scale|figures`; CI checks `SHA256SUMS`).
Deterministic simulator, seed 20260813, 30-seed sweep, 5-node fixture.
**Evidence class: bitwise reproducible** — all SHA256SUMS verify and all
four JSON artifacts regenerate byte-identically (re-verified 2026-09-06,
macOS arm64, Python 3.11.14; PYTHONHASHSEED-independent). 277 tests pass.
Re-verified 2026-10-22 with the Rust port: all nine artifacts (JSON and SVG)
regenerate byte-identically; CI checks them on every push.

Mean capability survival per (strategy, intervention), 30 seeds:

| strategy | node fail | mem del | lineage root | auth revoke | route rm | edge corrupt | stale | partition | worst domain | **mean** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| isolated_local | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | **0.000** |
| centralized | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 | **0.111** |
| fixed_distributed_replication | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.556** |
| source_count_repair | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.556** |
| full_replication | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | **0.667** |
| random_path_diversification | 1.00 | 1.00 | 0.67 | 0.67 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.704** |
| **lineage_aware_repair** | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.778** |
| oracle_min_cut | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | **0.889** |

Survival, unrecovered silent forgetting, transfer volume:

| strategy | survival | unrecovered forgetting | mean bytes |
|---|---:|---:|---:|
| isolated_local | 0.000 | 0.000 | 1,371 |
| centralized | 0.111 | 0.667 | 3,020 |
| fixed_distributed_replication | 0.556 | 0.444 | 4,943 |
| source_count_repair | 0.556 | 0.444 | 4,941 |
| full_replication | 0.667 | 0.333 | 9,877 |
| random_path_diversification | 0.704 | 0.296 | 5,327 |
| **lineage_aware_repair** | **0.778** | **0.222** | 5,448 |
| oracle_min_cut | 0.889 | 0.111 | 6,461 |

Notes that must survive into any citation: "bytes" is serialized
transfer volume, not resident-memory savings; forgetting shown is
*unrecovered* after repair; full replication survives partition while
the repair strategies do not; scope is a small-N deterministic simulator
— it validates the mechanism, not 1K–10K-agent populations.

Invariants (from CLAUDE.md — if one moves, a claim about the world
changed): replica counting survives lineage-root failure at 0.00;
lineage_aware 0.778 / oracle 0.889; only min-cut-2 survives
worst_single_domain_failure; every scale verdict invariant across
K ∈ {2,3,5,8}, H ∈ {2,3}; lineage_aware loses to source_count in exactly
one of six cells (node_failure, I=1).

---

## Track B — Retrieval: the LoCoMo campaign (Nurman)

Source: `research/reports/RESULTS_ALL.md` + `research/results/*.json` at
`e054178` (merged as PR #3, 2026-09-06). LoCoMo, 10 conversations,
1,540 questions. Models: gemma-4-e4b (answer/rerank/campaign judge),
nomic-embed-text v1.5, qwen3.6-35b (independent judge). Leakage-free
harness (gold-answer gate and gold-category routing removed).

The retrieval-only numbers below (recall@k, every result file's `retrieval_metrics`, the H9
tables) re-derive exactly from the Rust port with the cached embeddings
(`research/README.md`, "Reproducing"), except two cells that depended on Python's per-process
set order and were never reproducible in Python either (see `crates/neuralgraph-research/README.md`).

### Headline — shipped system, conversations 1–5 (744 of 1,540 questions)

| category (as reported) | n | new (Gemma lenient) | old leaky, same questions (GPT-4o lenient) |
|---|---:|---:|---:|
| single_hop | 142 | 73.9% | |
| multi_hop | 400 | 72.8% | |
| temporal | 156 | 73.7% | |
| open_domain | 46 | 56.5% | |
| **overall** | **744** | **72.2%** | **66.9%** |

Different judges per column — the +5.3 is indicative. Conversations
6–10 and the adversarial category are not yet run. Shipped config:
`RETRIEVAL_MODE=local_pairs` + open-domain flags.

### The clean A/B — same 282 questions, same judge, only retrieval changed

| retrieval | accuracy | recall@10 | recall@50 | s/q |
|---|---:|---:|---:|---:|
| flat (original Tesseract) | 64.9% | 39.4 | 61.7 | 6.4 |
| hybrid (speaker boost + embed back-fill) | 67.0% | 44.7 | 61.7 | 8.2 |
| graph (time chain + speaker + dialogue links) | 66.7% | 39.4 | 61.7 | 7.9 |
| **local_pairs (per-agent routing + pair back-fill)** | **73.8%** | 46.8 | 62.8 | 7.9 |

The +8.9 flat→shipped gain survives all four judges (+11.3 Qwen lenient,
+9.6 Qwen strict, +4.6 substring; p < .001) — the campaign's most
defensible single result.

### All 18 experiments

| # | experiment | level | result | verdict |
|---|---|---|---|---|
| H9 | per-agent memory routing | retrieval + e2e | recall@10 +7.4; e2e +8.9 with pairs | **works** |
| H3 | pair nodes as back-fill | retrieval | +3.2 recall@all | **works** |
| H5 | open-domain prompt flags | e2e | +14.6 lenient, −1.0 strict | lenient-judge only |
| — | speaker boost + embed back-fill | e2e | +2.1 | small |
| — | graph-neighbour expansion | both | +1.4 recall, +1.8 e2e | ≤ embedding back-fill |
| H1 | listwise reranker | e2e | −4 accuracy, −56% latency | speed only |
| H2/N1 | wider / narrower context | e2e | −2…−9 either direction | hurts |
| H7 | LLM router | e2e | +1 / −2 | null |
| H4/H6 | scoring-formula fixes | retrieval | within noise | null |
| N3 | reply-only pairs | retrieval | −1 to −2 | hurts |
| N4 | pair-aware reranker/prompt | e2e | −7 / −3 / −12 | hurts |
| H8 | 35B answer model | e2e | −7 lenient, +4 strict | judge artifact |
| M1 | mega search (vector+BM25+graph hop) | both | recall@10 +6.9; e2e −10 single_hop | hurts e2e |
| M2 | LLM-built entity graph | both | recall@10 +5.0; e2e −8 single_hop | hurts e2e |
| N5 | judge sensitivity | evaluation | 51-point spread by grader | **paper-grade** |

### Judge sensitivity — same answers, four graders

| answer file | n | Gemma lenient | Qwen lenient | Qwen strict | substring |
|---|---:|---:|---:|---:|---:|
| flat, single_hop | 282 | 64.9 | 51.4 | 21.6 | 13.5 |
| shipped, single_hop | 282 | 73.8 | 62.8 | 31.2 | 18.1 |
| H5 open-domain treatment | 96 | 57.3 | 39.6 | 10.4 | 9.4 |

Judge model alone moves scores ~12 points at identical prompt; prompt
leniency ~30 points. Kappa vs campaign judge: Qwen lenient 0.72, strict
0.30, substring 0.17. **Any headline number must name its judge.**

### Mechanics worth keeping

- Per-agent routing equals oracle routing (gold spoken by the other
  agent only 2.9% of the time).
- Speaker names are embedding-level pointers to the *wrong* speaker
  (0.1% own-name vs 33.4% other-name incidence).
- The entity-graph PageRank hop is the only graph mechanism that beat
  plain embeddings at retrieval (+6.9 recall@10) — and still loses
  end-to-end by flooding the context with related non-answers.
- Retrieval decides; the reranker only reorders. 15/30 context memories
  is the measured optimum.

---

## Track C — The December baseline (superseded, leaky)

**The artifact for this track is not published in this repository.**
`maximal.json` (December 2025, GPT-4o lenient judge) used the gold answer as
an acceptance gate and the gold category label for routing. Those numbers are
leaked, so shipping them invites exactly the comparison they cannot support.
The table is kept only so the record of what was superseded is complete; it is
not a baseline anyone should cite or reproduce against. Every result in
`research/results/` comes from the leakage-free harness.

| measure | value | note |
|---|---:|---|
| overall accuracy | 66.7% | 66.9% on the 744-question comparison cohort |
| single_hop (as labeled) | 52.1% | worse than multi_hop (74.1%) |
| failures with evidence retrieved | ~76% | generation problem, not retrieval |
| unanswerable questions | 47 | gold appears nowhere in the conversation |
| hard ceiling | 96.9% | not 100% |

Diagnosis: `docs/ACCURACY.md` at the lineage snapshot. These numbers
exist to be compared against, not cited.

---

## Track D — The shipped assistant pipeline (`NeuralGraph.chat_memory`, now `crates/neuralgraph-memory`)

Measured 2026-10-18/19 with `research/benchmarks/chat_memory_locomo.py` (now
`cargo run --release -p neuralgraph-research --bin chat-memory-locomo`): LoCoMo conversation 0,
sessions 1–8 (174 messages), 66 non-adversarial questions whose evidence lies in those sessions.
Extraction by Qwen3.8-9B (LM Studio, `LLM_NO_THINK_PREFILL=1`), embeddings nomic-embed-text v1.5.
Retrieval-only: **evidence@k** = a retrieved memory cites a gold evidence message (provenance, no
judge). One question is 1.5 points; treat deltas under 3 as noise.
**Evidence class: single conversation, single model, committed harness; per-run artifacts not committed.**

| configuration | memories | questions with no memory on any evidence | evidence@15 |
|---|---:|---:|---:|
| previous defaults: 6-message batches, graph channel on, k=10 | 57 | 30% | 62.7 |
| 2-message batches, graph off, k=15 | 76 | 17% | 78.8 |
| + recall-first prompt (reactions/questions/lists count, cap 12) — **shipped** | 147 | 6% | **87.9** |

What did not help: 300-word batching (44.8; the extractor emits ~2 memories per call whatever the
window), raising the cap alone, hard subject routing, k=5, LoCoMo image captions (0 to −3), and
turning off relation extraction to save time (−37% time, −6 pts: that call registers the entities the
subject boost needs). Reconcile threshold 0.75 saves ~19% time within noise.

### Track D, LongMemEval Oracle (50-question stratified sample, seed 1)

`research/benchmarks/chat_memory_longmemeval.py` (now `--bin chat-memory-longmemeval`), same model and metric. One fresh database per question,
one chat per session. **turn@15** = a memory in the context block cites a `has_answer` turn.

| configuration | turn@15 | memories / question |
|---|---:|---:|
| `extract_assistant=False` (the default before 2026-10-20) | 87.0 | 12.0 |
| `extract_assistant=True` (**shipped default** since 2026-10-20) | **93.5** | 22.2 |

Five of six question types score 100 under both. Every miss with assistant turns excluded is an assistant-said fact.
Abstention (4 questions with no answer in the history): the context block is never empty, 0/4 in both runs.
Query latency on these databases: p50 10 ms / p95 12 ms end to end (embedding included), 403-token context.

### Track D, end-to-end accuracy of the shipped pipeline (same 50 LongMemEval Oracle questions)

Measured 2026-10-22 with the Python harness `research/benchmarks/chat_memory_answer_eval.py` at commit `d14c4a81`
on branch `PNurmanM/memory-architecture-stats` (no Rust port yet; it would be `--bin chat-memory-answer-eval`).
A local model answers from the shipped context block (mean ~540 tokens); Gemma-4-e4b, a different family, grades
under a lenient and a strict prompt; plus a substring check. Same databases as the retrieval table above.

| answerer | lenient | strict | substring | "I don't know" on answerable |
|---|---:|---:|---:|---:|
| Qwen3.8-9B, concise prompt | 80.0 | 54.0 | 44.0 | 15 / 46 |
| Qwen3.6-35B-A3B, concise prompt | 74.0 | 48.0 | 42.0 | 10 / 46 |
| Qwen3.8-9B, compute prompt (reason, then answer) | 72.0 | 56.0 | 34.0 | 7 / 46 |

Reading the gap between 93.5 retrieval and ~55 strict: about 5 of every 20 strict misses are judge false negatives
("approximately 1300" vs gold "1300", "Fridays" vs "Friday", "around 6:30 pm" vs "6:30 pm"), so hand-corrected
strict is roughly 65. The genuine misses are arithmetic over several memories (30-minute jog -> 0.5 hours, two
school spans -> 10 years, day counts between dates) and assistant facts that were never extracted. A bigger local
answerer did not help; letting the model reason traded "I don't know" for wrong sums. The lenient judge passed 7
"I don't know" answers on questions with a gold value. The number that describes the memory system is retrieval;
accuracy here describes the local reader. A human-labelled sample is the next step before quoting any accuracy
figure. **Evidence class: single run, one judge family, 50 questions; per-question rows not committed.**

### Track D, 2026-10-28 overnight campaign: source quotes + IDK cascade

Fresh ingests of the 50 LongMemEval Oracle questions and of LoCoMo conversation 0 (sessions 1-8, 66 questions) with the
shipped extraction config: Qwen3.8-9B at a 16k context, `LLM_NO_THINK_PREFILL=1`, nomic-embed-text v1.5, k=15, 3600-character
context budget. Answerer: Qwen3.8-9B, concise prompt. Four judges, all on the same answers: Gemma-4-e4b with the Oct-22
lenient and strict prompts; Gemma-4-e4b with the `strict_v2` / `lenient_v2` pair (rationale first, then a VERDICT line);
Qwen3.6-35B-A3B with the Oct-22 prompts. Harness: `scripts/answer_eval.py`, ledger and per-question rows in
`research/overnight/`. The **IDK cascade** is a client-side reader protocol, not a crate feature: answer concisely, and if
the answer is "I don't know", re-ask once with a compute-style prompt (`research/overnight/prompts/auto_v1n.txt`; its examples are neutral, because the earlier `auto_v1` quoted five LME-50 golds).
**context_quotes** renders the human message a memory was extracted from under its context line (one quote of at most 300
characters, cut at a sentence end, deduplicated across memories).

Shipped behaviour, crate-rendered context. "q=1" is `context_quotes = 1` + cascade, "q=2" is `context_quotes = 2` + cascade.
Gemma columns are strict / lenient / strict_v2 / lenient_v2; "35B" columns use the Oct-22 prompts. IDK counts all 50 (LME) or
66 (LoCoMo) answers.

| set | config | strict | lenient | strict_v2 | lenient_v2 | 35B strict | 35B lenient | IDK |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| LME-50 | base | 42.0 | 72.0 | 44.0 | 78.0 | 44.0 | 52.0 | 24 |
| LME-50 | q=1 | 50.0 | 74.0 | 54.0 | 76.0 | 54.0 | 56.0 | 14 |
| LME-50 | q=2 | 48.0 | 72.0 | 52.0 | 74.0 | 54.0 | 58.0 | 15 |
| LoCoMo c0 | base | 50.0 | 77.3 | 62.1 | 87.9 | 56.1 | 69.7 | 15 |
| LoCoMo c0 | q=1 | 50.0 | 80.3 | 56.1 | 86.4 | 62.1 | 72.7 | 9 |
| LoCoMo c0 | q=2 | 51.5 | 80.3 | 56.1 | 87.9 | 59.1 | 75.8 | 10 |

Paired against base (wins:losses = questions that flipped to correct : to wrong; McNemar exact p; LME-50 one question is 2
points, LoCoMo 1.5). "Lenient non-IDK" is lenient with every "I don't know" counted wrong.

| set | config | strict (Gemma) | strict_v2 | lenient non-IDK | 35B strict | 35B lenient |
|---|---|---|---|---|---|---|
| LME-50 | q=1 | +8.0, 5:1, p .219 | +10.0, 6:1, p .125 | **+14.0, 8:1, p .039** | +10.0, 5:0, p .062 | +4.0, 4:2, p .688 |
| LME-50 | q=2 | +6.0, 4:1, p .375 | +8.0, 6:2, p .289 | +10.0, 6:1, p .125 | +10.0, 5:0, p .062 | +6.0, 5:2, p .453 |
| LoCoMo c0 | q=1 | 0.0, 3:3, p 1.0 | -6.1, 4:8, p .388 | +3.0, 4:2, p .688 | +6.1, 6:2, p .289 | +3.0, 4:2, p .688 |
| LoCoMo c0 | q=2 | +1.5, 5:4, p 1.0 | -6.1, 4:8, p .388 | +4.5, 6:3, p .508 | +3.0, 4:2, p .688 | +6.1, 6:2, p .289 |

Reading: on LongMemEval the change removes ten "I don't know" answers (24 to 14) and raises strict under both judge
families; only the non-IDK lenient delta reaches p < .05, and the other deltas are consistent in sign, not individually
significant. On LoCoMo strict is flat under Gemma and up under Qwen-35B. The `strict_v2` LoCoMo loss (4:8) appears under
Gemma only; the 35B judge, which regraded the same answers, shows a gain on both quote settings. The audit of the earlier
R3/R6 runs found about half of the unlocked IDKs became confident wrong answers (5 of 10), so the cascade trades refusals for
errors as well as for correct answers.

Extraction fixes, 18-question failure-heavy LongMemEval subset, paired against a re-ingest of the same config. `E56` =
`NEURALGRAPH_EXP=hyphen_ground,num_dedupe` (name grounding accepts "Red-headed"; a candidate with different numbers is
never a cosine duplicate, which fixes a "120 stars" memory dropped as a duplicate of "300 stars"). One question is 5.6 points.

| ingest | evidence@15 | memories | strict | lenient | strict_v2 | lenient non-IDK |
|---|---:|---:|---:|---:|---:|---:|
| baseline re-ingest | 83.3 | 307 | 22.2 | 61.1 | 16.7 | 27.8 |
| E56 | 94.4 | 359 | 27.8 | 66.7 | 33.3 | 50.0 |

The judge audit counted 4 real fixes and 1 answer-only regression. On a full 50-question E56 re-ingest the fixes looked large
against the first base ingest (strict 42 -> 60), but that gain is within noise; switches available, default off (see the next
table). Against a second, unchanged-config re-ingest the fixes are strict +6 (7:4, p .55) and strict_v2 -8 (3:7).

Run-to-run variance of extraction (noise floor). `lme_base` and `lme_base2` are two ingests of the same 50 histories with the
same config, answered and judged identically (Gemma judge):

| ingest | memories | evidence@15 | strict | lenient | strict_v2 | lenient non-IDK |
|---|---:|---:|---:|---:|---:|---:|
| lme_base | 1138 | 95.7 | 42.0 | 72.0 | 44.0 | 50.0 |
| lme_base2 | 1104 | 93.5 | 54.0 | 82.0 | 58.0 | 66.0 |

41 of the 50 questions have a different memory count between the two ingests (max |delta| 21); evidence@15 differs by one
question. Strict moved +12.0 (7:1, p .07) and strict_v2 +14.0 (7:0, p .016) with no change at all. Consequence: a delta of under
about 12 strict points between two single ingests at n=50 is not evidence. The context_quotes + cascade result above is
unaffected, because it was paired on identical databases (only retrieval and the reader changed, not extraction).

Deterministic mode removes most of it: with the worker at `--concurrency 1 --parallel 1` (one in-flight request), two same-config ingests of the same 50 histories differ on 7 of 50 questions (max 13 memories), have no evidence@15 flips, and score strict 54.0 vs 56.0 / strict_v2 64.0 vs 66.0 (Gemma). Benchmark ingests should use that mode; it costs roughly 1.5x wall time. Re-tested in that mode, the two extraction fixes (`hyphen_ground`, `num_dedupe`) show no aggregate gain against two same-mode baselines (strict +6/+4, strict_v2 −2/−2, official judge 58 → 54 both, evidence@15 95.7 → 93.5), so they stay off by default. The month resolver, zero-memory retry and relative-time prompt rule (`months_ago,retry_zero,prompt_extra_file`, E48) was re-tested the same way and also rejected: evidence@15 +1 question, strict +2/0, strict_v2 0/−2, lenient non-IDK −6/−8, official judge 58 → 56 against both baselines.

Caveats:
- The lenient judges credit "I don't know" on answerable questions (16 of the 20 answerable IDKs in the LME-50 base under
  `lenient_v2`; 5 of 15 on LoCoMo under lenient). `lenient_v2` falls 2-4 points on LME-50 for that reason, not because
  answers got worse; read lenient next to the IDK count or use the non-IDK column.
- Extraction is not run-to-run stable. The same config re-ingested gave 7 vs 18 memories for one question and 7 vs 1 for
  another, and evidence@15 moved two questions of 18 between identical runs. An ingest delta of under about three
  questions is not evidence.
- n=50, so one question is 2 points. Only one comparison in the paired table clears p < .05, out of 20 read.
- The `strict_v2` LoCoMo loss (4:8) is not corroborated by the Qwen-35B judge; treat LoCoMo as neutral to mildly positive.
- Three of the 50 LME-50 baseline databases were re-ingested after dead extraction jobs (the 8k context overflowed on
  assistant-heavy batches, the model was reloaded at 16k); `gpt4_372c3eed` still has one dead batch.
- The Oracle haystack holds only evidence sessions, so quotes find their answer more easily than in a full haystack.

**Evidence class: single run per row, one ingest per set, 9B answerer, four judges over the same answers (two families),
50 and 66 questions; per-question rows in `.context/overnight/results/`, not committed.**

#### Round 2-3: episodic recall, context budget, reasoning reader

Three changes were tested on top of the morning's shipped behaviour (quotes + concise prompt + IDK cascade, 3600-character budget),
all on identical databases so only retrieval rendering and the reader differ:

- **Context budget** `context_max_chars` 3600 -> 6000, so more quotes fit.
- **Episodic recall** `context_raw_messages` 0 -> 3: up to three raw chat messages matching the question (SQLite FTS5 BM25 over the
  message store) are appended after the memory lines, human turns only, skipping messages already quoted.
- **Reasoning reader**: the compute prompt `research/overnight/prompts/auto_v1n.txt` on every question, with the 9B's thinking enabled
  (2500-token cap), instead of concise-then-cascade.

The configuration first tested is "episodic + 6000 + thinking compute reader on every question". The first two are crate defaults; the reader is client-side. The final reader routes by question type (see "Routed reader (final)" below); the tables up to that point are for thinking on every question.
Judges are unchanged (Gemma-4-e4b `strict`, `strict_v2`, lenient-non-IDK), plus the ported official LongMemEval judge (next
paragraph; LongMemEval only). Paired on identical databases; w:l = questions that flipped to correct : to wrong, McNemar
exact p. One LongMemEval question is 2 points, one LoCoMo question 1.5.

Shipped (morning) vs new, each cell `shipped -> new`:

| set / ingest | strict (Gemma) | strict_v2 | lenient non-IDK | official LME judge | IDK | s/answer |
|---|---|---|---|---|---:|---:|
| LME-50, clean ingest (`lme_base2`) | 62.0 -> 66.0, +4, 5:3, p .73 | 56.0 -> **76.0**, +20, 11:1, p .006 | 72.0 -> 80.0, +8, 5:1, p .22 | 58.0 -> **70.0**, +12, 7:1, p .07 | 15 -> 11 | 3.8 -> 7.7 |
| LME-50, first ingest (`lme_base`), replication | 50.0 -> 62.0, +12, 9:3, p .15 | 52.0 -> 70.0, +18, 11:2, p .022 (after re-judging 5 unparsable v2 verdicts) | 64.0 -> 80.0, +16, 10:2, p .039 | 48.0 -> **68.0**, +20, 11:1, p .006 | 14 -> 6 | 5.0 -> 8.2 |
| LoCoMo conv 0, 66 questions, replication | 50.0 -> 56.1, +6.1, 12:8, p .50 | 56.1 -> 66.7, +10.6, 12:5, p .14 (after re-judging 12 unparsable v2 verdicts) | 72.7 -> 77.3, +4.5, 8:5, p .58 | n/a | 9 -> 10 | 3.8 -> 8.5 |

The clean ingest was the selection set: the configuration was chosen on it from a dozen variants, so its numbers are optimistic.
The first LongMemEval ingest and LoCoMo were run once after the choice. The first ingest agrees in sign on every column and is
larger on the official judge; LoCoMo under thinking on every question is neutral on conversation 0 (strict +6.1 is 12:8, not
significant, and the two other columns are within 5 points) and a regression on conversation 1 (see the routed reader below). Mean
answer time doubles (about 4 s to 8 s on the 9B), and the context block grows from about 770 to about 1020-1290 tokens.

On a third LoCoMo conversation (conv 2, 29 questions, same recipe) the routed protocol scores strict 62.1 → 69.0 (3:1), strict_v2 69.0 → 79.3 (3:0), lenient-non-IDK 75.9 → 82.8 (2:0); pooled over four conversations (a fourth, conv 3 with 35 questions, scored 2:3) it is strict 18 wins : 12 losses, i.e. neutral to mildly positive on LoCoMo. Held-out sample (seed 2): a second stratified 50-question draw, never used while choosing prompts or routing (deterministic ingest; evidence@15 82.6). Final routed protocol vs the original plain-concise pipeline: strict 50 -> 60 (10:5), strict_v2 50 -> 72 (12:1, p .003), lenient-non-IDK 48 -> 66 (12:3, p .035), official LongMemEval judge 44 -> 62 (13:4, p .049). A second held-out draw (seed 3; evidence@15 93.5): strict 54 -> 70 (10:2, p .039), strict_v2 66 -> 76, lenient-non-IDK 74 -> 82, official 62 -> 72 (9:4). Pooled over the 100 held-out questions the routed protocol beats the original 20:7 on strict and 22:8 on the official judge. On the same two draws the morning's quotes + IDK-cascade protocol alone is neutral to negative (strict -4 on both, official -8 on seed 3): the context budget, the episodic turns and the routed thinking reader must ship together. Under the second judge model (Qwen3.6-35B-A3B, the Oct-22 prompts) the routed protocol vs the previous one is: LongMemEval clean ingest strict 66 → 74 (4:0), lenient 68 → 80 (6:0, p .031); first ingest strict 54 → 70 (8:0, p .008), lenient 56 → 76 (10:0, p .002); LoCoMo conv 0 strict 62.1 → 65.2 (6:4), lenient-non-IDK 71.2 → 84.8 (12:3, p .035). On the held-out sample the morning's quotes + cascade alone was weak (strict 46, official 50 vs 50 and 44 original); routed beat it on strict_v2 by 20 points (10:0, p .002). Accuracy mode (Qwen3.6-35B-A3B as the thinker for routed questions only, 9B for the rest): held-out strict 66, strict_v2 70, lenient-non-IDK 72, official 66 (+16/+20/+24/+22 over the original, p .004 to .077; +6/-2/+6/+4 over the routed 9B, within noise); on the seed-1 ingests strict 68 → 80 (6:0, p .031, clean) and 68 → 72 (2:0, first). Mean answer time 4.3 → 12.9 s (a routed answer about 22 s): a batch/offline mode, not the interactive default.

What carries the gain, LME-50 clean ingest (`lme_base2`, all rows share the same 50 databases; official judge where it was run):

| context | reader | strict | strict_v2 | lenient non-IDK | official | IDK | s/answer |
|---|---|---:|---:|---:|---:|---:|---:|
| 3600, quotes (shipped) | concise + IDK cascade | 62 | 56 | 72 | 58 | 15 | 3.8 |
| 6000, quotes | concise + IDK cascade | 62 | 64 | 74 | n/a | 14 | 4.8 |
| 3600, quotes | compute prompt, thinking | 64 | 68 | 74 | 64 | 12 | 7.1 |
| 6000, quotes | compute prompt, thinking | 66 | 74 | 76 | 66 | 14 | 6.0 |
| 6000, quotes + 3 raw user turns | concise + IDK cascade | 62 | 68 | 78 | 62 | 12 | 4.8 |
| 6000, quotes + 3 raw user turns | concise + cascade, cascade call thinks | 64 | 70 | 80 | 64 | 10 | 5.4 |
| **6000, quotes + 3 raw user turns** | **compute prompt, thinking** | **66** | **76** | **80** | **70** | 11 | 7.7 |

Paired against the shipped row on strict_v2: 6000 alone 4:0 (p .125); thinking alone 8:2; 6000 + thinking 10:1 (p .012); raw turns
+ cascade 8:2 (p .11); raw turns + cascade-think 8:1 (p .039); full configuration 11:1 (p .006). Also tried and dropped: including
assistant turns in the raw block (strict_v2 +2, 4:3; lenient non-IDK -2; assistant turns echo the question), and three-sample
self-consistency voting before the cascade (no change, strict_v2 +2 on 1:0; concise answers rarely agree verbatim).
The 3600/thinking row's strict (64) is a regrade of the same answers as the reader-ceiling table below (62): Gemma's own
run-to-run variation on a regrade is about two points.

Reader ceiling. Same 50 shipped (3600-character, quotes) contexts of `lme_base2`, no cascade, so each row isolates the reader. Paired
against the 9B concise row. The Claude Sonnet probe is a separate 20-question subset of the same contexts (sub20), so it has its own
9B baseline.

| reader | strict | strict_v2 | lenient non-IDK | IDK | s/answer | vs 9B concise |
|---|---:|---:|---:|---:|---:|---|
| Qwen3.8-9B, concise | 60 | 58 | 68 | 18 | 3.2 | |
| Qwen3.8-27B (dense), concise | 58 | 58 | 70 | 15 | 32 | strict_v2 0.0, 4:4 |
| Qwen3.8-9B, compute prompt, thinking | 62 | 68 | 74 | 12 | 7.1 | strict_v2 +10, 8:3, p .23 |
| Qwen3.8-27B, compute prompt, thinking | 56 | 70 | 86 | 9 | 57 | strict_v2 +12, 10:4, p .18; non-IDK +18, 10:1, p .012 |
| *20-question subset:* 9B, concise | 55 | 55 | 75 | 8 | 4.8 | |
| *20-question subset:* Claude Sonnet, concise prompt | 65 | **75** | 95 | 4 | 1.5* | strict_v2 +20, 4:0, p .125; non-IDK +20, 4:0 |

\* Sonnet answered through an agent call; its time is wall-clock per answer and is not comparable to local inference. Sonnet read
exactly the 9B's context block, graded by the same local judges. Its fixes were arithmetic and knowledge-update reasoning the 9B
does not do. A bigger dense local model does not help without thinking and costs ten times the latency; the thinking 27B is
slightly better than the thinking 9B on non-IDK accuracy at eight times the latency. With identical memories, the reader is the
remaining bottleneck: evidence reaches the context block on 94-98% of these questions.

The official LongMemEval judge. We ported the judge prompts of the LongMemEval repository (Wu et al., `evaluate_qa.py`), one prompt
per question type, and ran them with Gemma-4-e4b in place of GPT-4o. It accepts an answer that contains the correct answer or the
intermediate steps, tolerates an off-by-one error in day counts on temporal questions, accepts a knowledge-update answer that also
mentions the earlier value if it gives the updated one, and grades preference questions on whether the answer uses the user's
personal information, not on rubric coverage. For abstention it wants the model to say the question is unanswerable; Gemma rejected a
bare "I don't know" on 3 of the 4 abstention questions (and on all 4 for the shipped answers), so the four rows, worth 8
points, are largely uncredited in every official figure here. Under it, the shipped morning configuration scored 58 on the clean
ingest and 48 on the first; the new configuration scores 70 and 68. Published numbers on the same prompts: Zep 63.8 with gpt-4o-mini
and 71.2 with gpt-4o as reader (LongMemEval-S, GPT-4o judge); Mem0 (managed, 94.4 with a GPT-5 reader) and Mastra Observational
Memory (94.9 with gpt-5-mini, 84.2 with gpt-4o) report 85-95 with GPT-5-class readers. These are vendor-reported, are on the
~115k-token S variant, and the cited Mastra reader swap alone (gpt-4o to gpt-5-mini) is worth 10.6 points on identical memories.
We read ours as "comparable regime to the Zep 4o-mini to 4o range with a 9B local reader" and nothing more: the Oracle variant
is easier than S (GPT-4o scores 0.87-0.92 on Oracle against 0.61-0.64 on S in the LongMemEval paper), the judge is a 4B-class
local model, n = 50, and the gap to 85-95 is mostly a reader difference (see the reader-ceiling table).

Routed reader (final). Thinking on every question regressed LoCoMo conversation 1 (38 questions, sessions 1-8): strict 57.9 -> 50.0
(2:5), `strict_v2` 63.2 -> 57.9, lenient non-IDK 76.3 -> 73.7. A hand audit of the 13 flipped questions
(`research/overnight/judgments/locomo_c1.md`) found 4 real fixes against 3 real regressions, the rest format or judge noise: thinking
costs on short-phrase golds, and the richer context (6000 characters, raw turns) is neutral to positive. The final reader therefore
routes: questions matching `COMPUTE_RE` in `research/overnight/scripts/answer_eval.py` (how long / how many / how much / how old /
total / altogether / combined / sum of / number of / difference / between / days / weeks / months / years) go to the compute prompt
with thinking (2500-token cap, non-thinking fallback); all others get the concise prompt; the IDK cascade follows in both cases.
Context is the crate default. Each cell is `previous shipped protocol -> routed` (concise + IDK cascade on the 3600-character quotes
block), paired on identical databases, same judges as above:

| set | strict (Gemma) | strict_v2 | lenient non-IDK | official LME judge | IDK | questions routed | mean s/answer (p50) |
|---|---|---|---|---|---:|---:|---|
| LME-50, clean ingest | 62 -> 68 (6:3) | 56 -> **78** (11:0, p .001) | 72 -> 80 (5:1) | 58 -> **74** (8:0, p .008) | 15 -> 10 | 28/50 | 3.8 -> 4.3 (3.8) |
| LME-50, first ingest | 50 -> 68 (9:0, p .004) | 52 -> 72 (10:0, p .002) | 64 -> 74 (6:1) | 48 -> **64** (8:0, p .008) | 14 -> 9 | 28/50 | 5.0 -> 5.0 (3.4) |
| LoCoMo conv 0, 66 questions | 50.0 -> 57.6 (9:4) | 56.1 -> 68.2 (13:5, p .10) | 72.7 -> 81.8 (8:2) | n/a | 9 -> 7 | 4/66 | 3.8 -> 2.1 (1.7) |
| LoCoMo conv 1, 38 questions | 57.9 -> 57.9 (4:4) | 63.2 -> 68.4 (4:2) | 76.3 -> 78.9 (3:2) | n/a | 5 -> 3 | 0/38 | 2.9 -> 1.9 (1.8) |

The LoCoMo neutral (conversation 0) and regression (conversation 1) under all-questions thinking is resolved by routing: only 4 of 66
and 0 of 38 LoCoMo questions match the compute pattern, so LoCoMo is answered almost entirely on the concise path, and routed is
level or ahead on every LoCoMo column (strict +7.6 and +0.0, `strict_v2` +12.1 and +5.3 on conversations 0 and 1; none individually
significant). Mean latency stays close to the concise reader's (the routed questions think; the p50 is the concise path).

Judge audit (`research/overnight/judgments/round3_winner.md`, clean ingest, every flip between shipped and new read by hand):
- 17 questions flipped on at least one metric. 7 are real fixes, 1 is a real regression, 1 is a lucky guess the judge credited (an
  invented rota table), and 8 are format or judge noise (longer sentence rejected by `strict_v2`, "About 4 months ago" rejected by
  `strict`, and similar).
- The judge-corrected gain is therefore about +12 points on `strict_v2` (nominal +20) and about +10 on the official judge (nominal
  +12): +6 net questions and +5 net questions. Strict and lenient non-IDK are not individually significant on this ingest.
- Thinking carries most of it: thinking alone moves `strict_v2` 56 -> 68 and official 58 -> 64 on the unchanged context, and five of
  the seven real fixes need only that. The 6000-character budget adds +8 nominal `strict_v2` alone and +6 on top of
  thinking (68 -> 74); the raw turns add about +2 `strict_v2` and +4 official on top of 6000 + thinking (one clean fix, a taxi price
  that appears only in a raw turn). The episodic block is the least established part.
- The one real regression was a thinking overrun: the reader exceeded the 2500-token cap on one question and returned an empty
  answer (50 s), after raw-turn distractors gave it extra dollar amounts. One empty answer also occurs in the first-ingest
  replication. Both are counted wrong in every table above. The harness now re-asks once without thinking when the reply is empty
  (`research/overnight/scripts/answer_eval.py`, commit `a35c745b`); that change was not re-measured.
- Abstention held: the thinking reader said "I don't know" on 4 of 4 abstention questions, as the shipped reader did. Four is too
  few to say more, and the reader did invent an answer on one non-abstention assistant-side question (the lucky guess above).
- Confident-wrong answers (not "I don't know", judged wrong by the official judge) went 6 -> 5; `strict_v2` 11 -> 5.
- `strict_v2` returned no parsable VERDICT line on 5 of 50 (first ingest), 7 of 50 (shipped answers regraded) and 12 of 66 (LoCoMo)
  answers in the replication regrade; those count as wrong, so `strict_v2` is the weakest column on LoCoMo.
- Leak check: no gold phrase appears in the prompt, and the episodic retrieval reads only the question text (gold appears
  verbatim in 24/50 new contexts against 23/50 shipped).
- Two lenient columns fell on the clean ingest (lenient 88 -> 86, `lenient_v2` 88 -> 80) because they credit "I don't know", which
  the new reader says less often (15 -> 11); the audit found only three of the six `lenient_v2` losses involved a changed answer.

**Evidence class: one temp-0 run per row, 50 and 66 questions, a 9B answerer, a 4B-class local judge (Gemma-4-e4b) for all columns
including the official prompts, one set (clean ingest) used for selection, two for replication, and a fresh 50-question sample (seed 2) as a held-out check, a 20-question probe for the
Sonnet row; per-question rows in `.context/overnight/results/`, not committed.**

### Comparability with published numbers

Official-judge scores of the routed protocol (Qwen3.8-9B reader, official LongMemEval prompts run by Gemma-4-e4b, Oracle, 50 questions
per draw, one question = 2 points), original pipeline -> routed: seed 1 (tuning set, concurrency-2 ingest) 50 -> 74; seed 2 (held-out,
deterministic ingest) 44 -> 62 (13:4, p .049); seed 3 (held-out, deterministic) 62 -> 72 (9:4). Mean over the 150 questions 52 -> **69**.
Pooled over the 100 held-out questions the routed protocol beats the original 22:8 on the official judge and 20:7 on strict.

| | this repository | published vendor numbers |
|---|---|---|
| LongMemEval variant | Oracle (easier: GPT-4o 0.87-0.92 vs 0.61-0.64 on S, LongMemEval paper) | mostly S, ~115k tokens per question |
| Sample | 50 per draw, 150 total, 100 held-out | 500 |
| Reader | local Qwen3.8-9B | GPT-4o to GPT-5 |
| Judge | official prompts run by Gemma-4-e4b; bare "I don't know" rejected on abstention, up to 8 points | official prompts run by GPT-4-class models |
| LoCoMo | conversations 0-3, sessions 1-8, local strict judges | 10 conversations, lenient LLM judge |
| Retrieval depth | 15 memories + quotes + 3 raw turns, ~1,100 tokens | Mem0 top-200 memories (~6.8k tokens); Zep ~1.6k tokens; Mastra ~30k tokens |

Consequence: 69 is in the band of GPT-4o-mini-class systems (Zep 63.8 with gpt-4o-mini, 71.2 with gpt-4o); the 85-95 figures are
mostly reader differences (Mastra's gpt-4o to gpt-5-mini swap alone is +10.6 on identical memories). **Estimate, not a measurement:**
under their full setup (frontier reader and judge, abstention credited) this pipeline would score about 88 +-5 on Oracle: 69, plus 4 to 8
for abstention credit, plus 10 to 17 for a frontier reader, with +-3 for the judge model. S is harder for every system, so this does not
transfer to S. How to run the apples-to-apples version (all 500 questions, a frontier judge, all 10 LoCoMo conversations) is in
[`research/overnight/README.md`](../research/overnight/README.md).

## Evidence classes at a glance

| track | headline | evidence class |
|---|---|---|
| Coordination survival | lineage_aware 0.778 vs oracle 0.889 | bitwise reproducible (pinned + SHA256SUMS + tests) |
| Retrieval flat→shipped | +8.9, survives all four judges | judge-robust, committed per-question artifacts |
| Retrieval overall | 72.2% vs 66.9% | partial cohort (conv 1–5), cross-judge, lenient |
| December baseline | 66.7% | leaky, superseded |
| Quotes + IDK cascade (Track D) | LME-50 IDK 24 to 14, strict 42 to 50 (Gemma) / 44 to 54 (Qwen-35B) | single run, 50/66 questions, two judge families, not significant on strict |
| Episodic recall + 6000-char budget + thinking reader (Track D, round 2-3) | LME-50 official judge 58 to 70 and 48 to 68 on two ingests, strict_v2 56 to 76 and 52 to 70 with thinking on every question (routed reader: official 58 to 74 and 48 to 64, strict_v2 56 to 78 and 52 to 72; LoCoMo pooled strict 16:9 over three conversations; held-out seed-2 sample: official 44 to 62, strict_v2 50 to 72; accuracy mode with the 35B thinker: official 66 on that sample) | single run per row, 50/66 questions, small local judge, hand-audited (judge-corrected gain about +10 to +12), selected on one ingest and replicated on a second; thinking carries most of it |
| Agentic Web N=100/1K/10K | — | proposed only; no experiment exists |

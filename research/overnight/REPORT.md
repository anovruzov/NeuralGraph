# Overnight accuracy campaign — report (2026-10-28 01:47 – 2026-10-29 07:10 CDT, two nights; experiment set complete)

Started 2026-10-28 01:47 CDT. Orchestrator: Claude Fable 5.1 with Sonnet/Haiku agents. Machine: one M5 Max, LM Studio.
Everything below is reproducible from `research/overnight/` (scripts, prompts, ledger) and `.context/overnight/results/` (per-question rows).

## 1. Bottom line

| what | evidence | status |
|---|---|---|
| **FINAL (evening): 6000-char budget + 3 episodic user turns (crate defaults, c03f3efc) + ROUTED reader** (concise + IDK cascade; thinking compute prompt only for count/duration/date questions) | vs the morning's shipped config, paired on identical databases: LME-50 clean strict 62 → 68, strict_v2 56 → 78 (11:0, p 0.001), official judge 58 → 74 (8:0, p 0.008); LME-50 first ingest strict 50 → 68 (9:0), official 48 → 64 (8:0); Qwen-35B judge: strict 66 → 74 (4:0) / 54 → 70 (8:0); LoCoMo three conversations pooled strict 16:9 (conv 0 strict_v2 56 → 68, conv 2 69 → 79). Latency 4.3 s mean on LME (p50 3.8), 2 s on LoCoMo. Held-out fresh sample (seed 2, 50 unseen questions) vs the original pipeline: strict_v2 50 → 72 (12:1, p 0.003), official 44 → 62 (13:4, p 0.049), §8. Details §7a | **confirmed (S1 + S2 on 5 sets, plus held-out seed 2), shipped** |
| **Accuracy mode: Qwen3.6-35B-A3B as the routed thinker** (9B stays the concise reader) | vs the routed 9B: clean ingest strict 68 → 80 (6:0, p 0.031), first ingest 68 → 72 (2:0), held-out seed 2 66 vs 60 (5:2; strict_v2 −2); mean 4.3 → 12.9 s (routed answers ~22 s). §7b, §8 | **confirmed as a batch/offline mode, not the interactive default** |
| **Source quotes under each memory line + IDK cascade** — crate feature `RetrievalConfig.context_quotes` (human-only, deduped, sentence-cut; commits 6f7fd375, a9647fd0; default flipped to 1 in a separate revertable commit) + a client-side reader protocol (answer concisely; on "I don't know" re-ask with the compute prompt `prompts/auto_v1n.txt`) | **Shipped behaviour measured end to end** (crate-rendered contexts, 3600-char budget (the default when measured; now 6000, c03f3efc)): LME-50 strict 42 → 50 (Gemma, 5:1) / 44 → 54 (Qwen-35B judge, 5:0, p 0.06), strict_v2 44 → 54 (6:1), lenient-non-IDK 50 → 64 (8:1, p 0.039), IDK answers 24 → 14; LoCoMo-66 strict 50 → 50 (Gemma) / 56 → 62 (Qwen-35B, 6:2), lenient 77 → 80, IDK 15 → 9. Judge audit of the harness-rendered variant: 6 real fixes / 0 regressions (R6), 4 quote-driven, 2 cascade-driven. Only negative cell: Gemma strict_v2 on LoCoMo −6 (4:8, p 0.39), not corroborated by the 35B judge | **confirmed (S1 + S2), shipped** |
| **Extraction fixes: hyphenated-name grounding + numeric dedupe guard** (switches f0862153, guard bug fix a471b492, `=0` tokens 810ee07e; the default flip c13d9eaa was REVERTED after the noise-floor run) | Full LME-50 re-ingest vs the FIRST baseline ingest looked large (strict 42 → 60, 10:1, p 0.012). But an **unchanged-config re-ingest** of the same 50 histories scored strict 54 (vs 42; 7:1, p 0.07), strict_v2 58 (vs 44; 7:0, p 0.016): the first baseline was a low outlier (run under the HTTP-400 churn and heavy GPU contention). Against the second baseline the fixes are strict +6 (7:4, p 0.55) and strict_v2 −8 (3:7) — **within run-to-run noise**. Mechanism-level evidence is real on 2–3 questions (a corrected number dropped as a cosine duplicate; audit logs in `judgments/lme50_E56.md`), hyphen grounding fixed nothing measurable. | **rejected as default** — deterministic-mode A/B (±2 floor) vs two same-mode baselines: strict +6/+4 but strict_v2 −2/−2, lenient-non-IDK −2/−4, official judge 58 → 54 (5:7) both, evidence@15 95.7 → 93.5. Bug fixes stay available as switches. |
| Months-ago resolver + zero-memory retry + relative-time prompt rule (`months_ago,retry_zero,prompt_extra_file`) | subset: evidence@15 +11.1, lenient +11.1; **deterministic full-50 A/B vs two baselines: evidence@15 +1 question, strict +2/0, strict_v2 0/−2, lenient-non-IDK −6/−8, official −2/−2** | rejected as default (switches kept) |
| Compute-style prompts as the primary answerer (auto_v1/v2/v3, strict_format, abstain) | LoCoMo strict −8 to −12 (IDK refusals 15 → 23); LME-20 strict +5 / lenient −20 | rejected as primary; useful only behind the cascade |
| Weekday/"said on" rendering, calendar scaffold, preference routing, compute routing | all within noise on both sets (judge: 0 real flips for saidon) | rejected / noise |

Baselines reproduced tonight (fresh ingests, same shipped config): LoCoMo conv0 strict 50.0 / lenient 77.3 (published Oct-22 run: 36.4 / 77.3 on a different ingest); LME-50 strict 42.0 / lenient 72.0 / turn@15 95.7 (published Oct-22: 54.0 / 80.0 / 93.5 — a different ingest; 24 of 50 answers were "I don't know"); LME-18 subset 22.2 / 61.1.

## 2. What was built (all committed on this branch)

- `research/overnight/PLAN.md` — protocol: stages S0 → S4, promotion rules, agent roles, session-limit handling.
- `LEDGER.md` — every run (one row each, 105 rows), `RUNLOG.md` — GPU job start/end, `STATE.md` — live state for the cron loop.
- `crates/neuralgraph-research` `chat-memory-context` binary: builds the shipped context block from existing databases with retrieval overrides and source quotes (no fixture changes).
- `crates/neuralgraph-memory`: switches behind `NEURALGRAPH_EXP` (hyphen_ground, num_dedupe, months_ago, retry_zero, verbatim_fallback, prompt_extra_file, batch_size, context_messages, context_quotes, context_quote_chars, context_k, context_max_chars, context_raw_messages, context_raw_message_chars, context_raw_include_assistant, context_multi_query, temporal_prior=W); 18 new tests for these switches, more for episodic recall, multi-query and the temporal prior (tests/retrieval.rs); golden fixtures regenerated only for the two default flips (c3899d07, c03f3efc).
- `scripts/answer_eval.py` — stdlib answer+judge harness reproducing the Oct-22 numbers, plus renders (quotes, quotes_user, saidon), calendar scaffold, IDK cascade, question routing, second judge pair (strict_v2/lenient_v2), non-IDK metrics, re-grading, judge cache; round-4 flags `--route-model --cascade-model --agentic-search --verify --verify-apply-unsupported --think-early-stop --route-regex tight`, scripts `round4*.sh`, `after_seed2.sh`, prompt `auto_v1n_brief.txt`; `chat-memory-context` passes `temporal_prior` through.
- `scripts/compare.py` (paired McNemar + bootstrap), `s0.py` (one experiment = one command + ledger row), `sweep.sh`, `gpu.sh` (2 GPU slots), `after_lme_base*.sh` (repair-then-sweep chain).
- `judgments/` — eight independent judge/accountability reports.

## 3. Findings that change how to read any number here

1. **The lenient judge credits "I don't know"** on answerable questions (5 of 15 IDKs in the LoCoMo base, 8 of 10 on LME-20). Report `lenient_nonidk` or `strict_v2` alongside. Honest LoCoMo lenient is ~70, not 77.
2. **Extraction is not run-to-run stable — and the floor is large.** Two ingests of the same 50 histories with the same config: memories 1138 vs 1104 (41 of 50 questions differ, max 21), evidence@15 95.7 vs 93.5, and end-to-end strict **42 vs 54** (7:1), strict_v2 44 vs 58 (7:0, p 0.016). A single-ingest delta of ~12 strict points at n=50 is noise. Any ingest-side claim needs several re-ingests per arm (≈ 2 h each on this machine). Reader-side comparisons paired on identical databases are unaffected. Cause confirmed: worker concurrency 2 + LM Studio request batching. **Deterministic mode** (`--concurrency 1 --parallel 1`): two same-config ingests differ on 7 of 50 questions (max 13 memories), 0 evidence flips, end-to-end strict 54 vs 56, strict_v2 64 vs 66 — a ±2-point floor instead of ±12. Benchmark ingests should use it (cost: ~1.5x wall time); the extraction-fix A/B re-run in this mode (`lme_detE56`, ledger E56-det) found no aggregate gain.
3. **Two LM Studio failure modes cost the night ~1.5 h**: (a) a JIT-loaded judge model is unloaded after 60 min idle and cannot reload under load (HTTP 400 "Model unloaded" / "Operation canceled") — pin models with `lms load` and guard before each run; (b) Qwen at an 8192 context overflows on assistant-heavy extraction prompts → dead jobs → 3 of 50 baseline databases needed re-ingest. Run the extractor at 16k.
4. **`LLM_NO_THINK_PREFILL=1` is mandatory** for any product-crate harness run; without it Qwen thinks on every call (30 s/msg vs 6).
5. **On LoCoMo the lever is raw evidence text** (extraction is lossy there); **on LongMemEval the lever is the reader's arithmetic** (the value is already in the memory text on 19 of 20 rows). One config has to serve both: quotes_user + cascade (R3) was the candidate; decided by the LME-50 rows, then superseded by the routed reader (7a).
6. **Strict-judge false negatives**: "approximately 1300" vs "1300", "The Friday before 15 July" golds. `strict_v2` (rationale + VERDICT line) is less brittle and should lead in the next published table.

## 4. Numbers (per-question rows in `.context/overnight/results/`)

### LoCoMo conv0, 66 questions, Qwen3.8-9B answerer, Gemma judge (second judge pair in the last columns)

| config | strict | lenient | strict_v2 | lenient_v2 | lenient-nonIDK | idk |
|---|---:|---:|---:|---:|---:|---:|
| baseline concise, plain k15 | 50.0 | 77.3 | 62.1 | 87.9 | 69.7 | 15 |
| quotes (3/mem), k-limit 10 | **54.5** | **84.8** | — | — | — | — |
| quotes k20 / 7000 chars | **56.1** | 83.3 | — | — | — | — |
| quotes_user (1/mem, deduped) | 50.0 | 80.3 | **65.2** | **89.4** | 74.2 | 10 |
| quotes_user + cascade | 51.5 | 78.8 | 63.6 | 89.4 | 72.7 | 9 |
| quotes_user 2/mem + cascade | 50.0 | 81.8 | 60.6 | 87.9 | **75.8** | 8 |
| cascade only | 47.0 | 78.8 | 60.6 | 86.4 | 69.7 | 15 |
| auto_v1 (compute prompt) | 39.4 | 72.7 | — | — | — | 23 |

### LongMemEval Oracle, 20-question snapshot (1 q = 5 pts)

| config | strict | lenient | strict_v2 | lenient_v2 | idk |
|---|---:|---:|---:|---:|---:|
| baseline | 35 | 80 | 40 | 70 | 10 |
| cascade | **40** | **85** | **45** | 70 | 9 |
| quotes k20/7000 | 30 | 75 | 35 | 65 | 9 |
| auto_v1 | 40 | 60 | 40 | 50 | 10 |

### LongMemEval Oracle, full 50 (46 answerable + 4 abstention; 1 q = 2 pts), harness-rendered contexts

| config | strict | lenient | strict_v2 | lenient_v2 | lenient-nonIDK | idk |
|---|---:|---:|---:|---:|---:|---:|
| baseline concise, plain k15 | 42.0 | 72.0 | 44.0 | 78.0 | 50.0 | 24 |
| quotes_user (1/mem) | 50.0 | 70.0 | 52.0 | 72.0 | 56.0 | 16 |
| quotes_user + cascade (R3n, neutral prompt) | 52.0 | 74.0 | **60.0** | 72.0 | **62.0** | 13 |
| quotes_user 2/mem + cascade (R6n) | **56.0** | 74.0 | 54.0 | 70.0 | 60.0 | 14 |
| cascade + route-compute | 50.0 | 70.0 | 54.0 | 68.0 | 54.0 | 23 |
| cascade + saidon | 48.0 | 74.0 | 44.0 | 74.0 | 52.0 | 21 |
| quotes (3/mem, assistant too) kl10 | 46.0 | 68.0 | 46.0 | 78.0 | 56.0 | 18 |

### Shipped behaviour (crate-rendered quotes under the 3600-char budget (the default when measured; now 6000), concise + cascade), both sets, three judges

| set | config | strict (Gemma) | strict_v2 | strict (Qwen-35B) | lenient (Gemma) | lenient-nonIDK | idk |
|---|---|---:|---:|---:|---:|---:|---:|
| LME-50 | baseline | 42.0 | 44.0 | 44.0 | 72.0 | 50.0 | 24 |
| LME-50 | context_quotes=1 + cascade | **50.0** (5:1) | **54.0** (6:1) | **54.0** (5:0, p .06) | 74.0 | **64.0** (8:1, p .039) | 14 |
| LME-50 | context_quotes=2 + cascade | 48.0 | 52.0 | 54.0 (5:0) | 72.0 | 60.0 | 15 |
| LoCoMo | baseline | 50.0 | 62.1 | 56.1 | 77.3 | 69.7 | 15 |
| LoCoMo | context_quotes=1 + cascade | 50.0 (3:3) | 56.1 (4:8) | **62.1** (6:2) | 80.3 | 72.7 | 9 |
| LoCoMo | context_quotes=2 + cascade | 51.5 (5:4) | 56.1 (4:8) | 59.1 (4:2) | 80.3 | 74.2 | 10 |

### Extraction screens, 18-question failure-heavy LME subset (paired vs a same-config re-ingest)

| ingest | evidence@15 | memories | strict | lenient | strict_v2 | lenient-nonIDK |
|---|---:|---:|---:|---:|---:|---:|
| baseline re-ingest | 83.3 | 307 | 22.2 | 61.1 | 16.7 | 27.8 |
| hyphen_ground + num_dedupe (E56) | **94.4** | 359 | **27.8** | 66.7 | **33.3** | **50.0** |
| months_ago + retry_zero + relative-time rule (E48) | 94.4 | 346 | 22.2 | **72.2** | 22.2 | 38.9 |
| verbatim_fallback (E3) | killed at 6/18 (GPU starved); untested | | | | | |

## 5. Recommended shipping decisions (morning decisions; the reader is superseded by 7a, budget and episodic defaults by c03f3efc)

1. `context_quotes = 1` — feature committed (default off) and the default flip committed SEPARATELY so one `git revert` restores the old block (see the commit list in 5b). Evidence above.
2. IDK cascade — documented as the cheaper alternative reader protocol (the recommended one is routed, 7a) (docs/CHAT_MEMORY.md); it is a client prompt, not crate code.
3. `hyphen_ground` + `numeric_dedupe_guard` — bug fixes kept as switches (`NEURALGRAPH_EXP=hyphen_ground,num_dedupe`), default OFF. Decided in deterministic mode (±2 floor): no aggregate gain (official judge −4 vs both baselines, evidence@15 −1 question). The numeric-guard mechanism fixes 2–3 specific questions but the extra memories it keeps dilute the top-15 elsewhere.
4. `docs/BENCHMARKS.md` Track D and `docs/CHAT_MEMORY.md` updated with the tables and judge caveats.

## 5b. What actually ships, and its honest effect (LME-50)

The morning change was `context_quotes = 1` + the IDK cascade (reader side); the afternoon added the 6000-char budget and 3 episodic turns as crate defaults (c03f3efc) and the routed reader (7a). Measured paired on identical databases, so immune to the
extraction noise floor:

| ingest | judge | plain strict → shipped strict | plain lenient → shipped lenient | IDK |
|---|---|---:|---:|---:|
| lme_base (first ingest) | Gemma | 42.0 → 50.0 (5:1) | 72.0 → 74.0 | 24 → 14 |
| lme_base (first ingest) | Qwen-35B | 44.0 → 54.0 (5:0, p 0.06) | 52.0 → 56.0 | |
| lme_base2 (second ingest) | Gemma | 54.0 → 62.0 (4:0) | 82.0 → 88.0 (3:0) | 19 → 15 |
| LoCoMo conv0 | Gemma / Qwen-35B | 50.0 → 50.0 / 56.1 → 62.1 (6:2) | 77.3 → 80.3 | 15 → 9 |

The earlier "42 → 66" stacked figure in LEDGER row E56-50-shipped combined this with the extraction re-ingest and is superseded: the extraction
part is within noise and its default flip was reverted.

Commits on this branch (in order): b2689cc3 framework · f0862153 extraction switches · 6f7fd375 context quotes · a9647fd0 quotes parity ·
c3899d07 **context_quotes default 1** (one of two shipped default changes, the other is c03f3efc; `git revert c3899d07` restores the plain block) · 84fe9045 docs ·
c13d9eaa extraction defaults on → **reverted by 221bce5d** after the noise-floor run · 810ee07e `=0` env tokens · a471b492 numeric guard
both-digits fix · bde1e574 docs (noise floor, defaults off) · 80fa6fae episodic recall (opt-in) · c03f3efc context_max_chars 6000 + 3 raw turns by default · 2801aa31 multi-query (opt-in) · 89e09335 temporal prior (opt-in, default 0). Workspace tests: last confirmed green at 0afc3db4 and 49959fef, before 2801aa31 and 89e09335; re-run `cargo test` on the final head.


## 7a. Final reader protocol (evening): ROUTED thinking (placed before 7 on purpose: final protocol first; 7 is the afternoon history)

The all-questions thinking reader regressed LoCoMo conversation 1 (strict 57.9 → 50.0; judge audit: thinking costs on short-phrase golds,
the richer context is neutral-positive). Routing the thinking compute prompt only to count/duration/date-difference questions (regex on the
question) and answering everything else with the concise prompt + IDK cascade keeps the LongMemEval gain and makes LoCoMo positive:

| set | strict | strict_v2 | lenient non-IDK | official judge | routed | s/answer |
|---|---:|---:|---:|---:|---:|---:|
| LME-50 clean ingest | 62 → 68 (6:3) | 56 → **78** (11:0, p 0.001) | 72 → 80 | 58 → **74** (8:0, p 0.008) | 28/50 | 3.8 → 4.3 |
| LME-50 first ingest | 50 → 68 (9:0, p 0.004) | 52 → 72 (10:0, p 0.002) | 64 → 74 | 48 → **64** (8:0, p 0.008) | 28/50 | 5.0 → 5.0 |
| LoCoMo conv 0 | 50 → 58 (9:4) | 56 → 68 (13:5) | 73 → 82 (8:2) | — | 4/66 | 3.8 → 2.1 |
| LoCoMo conv 1 | 58 → 58 (4:4) | 63 → 68 (4:2) | 76 → 79 | — | 0/38 | 2.9 → 1.9 |
| LoCoMo conv 2 (29 q) | 62 → 69 (3:1) | 69 → 79 (3:0) | 76 → 83 (2:0) | — | — | — |

Pooled over four LoCoMo conversations (168 questions; conv 3 added later at 2:3) the routed protocol is strict 18 wins : 12 losses — neutral to mildly positive on two-human chat data.

Second judge model (Qwen3.6-35B, same prompts): LME clean strict 66 → 74 (4:0) / lenient 68 → 80 (6:0, p 0.031); LME first strict 54 → 70
(8:0, p 0.008) / lenient 56 → 76 (10:0); LoCoMo conv 0 strict 62 → 65 (6:4) / lenient-non-IDK 71 → 85 (12:3, p 0.035). Zero losses on strict_v2 and the official judge (Gemma) and zero strict losses (Qwen-35B) on
LongMemEval (Gemma strict on the clean ingest is 6:3). This is the documented recommendation (`docs/CHAT_MEMORY.md`). Comparable number: official
LongMemEval judge, Oracle, local 9B: **64–74 %** on the seed-1 sample (62 % on the held-out seed-2 draw, §8).

## 7b. Round 4 (night 2): latency, and the one wild idea that worked

Asked to cut latency without losing accuracy and to try wild ideas. Everything reader-side that shortens the thinking call loses accuracy
or saves nothing (ledger `R4-*`): tight routing −13 % latency for −6 official points; two-stage thinking budget no saving (retries); shorter
concise output no change; ≤120-word reasoning −3 % for −6 official; Gemma-4-e4b as reader slower and worse; a verifier pass +1 s for +1
question, and converting its "unsupported" verdicts to "I don't know" destroys accuracy (strict −18 to −32); speculative decoding (MTP) does
not draft on this MLX build; agentic follow-up retrieval almost never triggers (1–2 of 50); the rule-based temporal prior touches 3 of 50
questions. Reader-side identical re-runs differ by 1–2 questions (LM Studio batching), which bounds what any of these can show.

The recommendation that follows is architectural: serve the concise answer immediately (~2 s) and upgrade it with the routed/thinking
answer when it lands (~6–8 s). Documented in `docs/CHAT_MEMORY.md`.

The wild idea that worked: **Qwen3.6-35B-A3B (a mixture model with 3B active parameters) as the thinking model for routed questions only**,
the 9B staying the concise reader. LME-50 clean ingest vs the routed protocol: strict 68 → **80** (6:0, p 0.031), strict_v2 78 → 82,
lenient-non-IDK 80 → 84, official judge 74 → 76. It decodes 1.8× faster per token (106 vs 59 tok/s) but reasons 2× longer, so a routed
answer takes ~22 s (mean 12.9 s over the set). On the tuning sample that looked like an **accuracy mode for batch use**; on the two held-out draws the gain over the routed protocol shrank to
within noise (see §8), so it is an option, not a recommendation.
Replication on the first ingest: strict 68 → 72, strict_v2 72 → 76, official 64 → 68 (each 2:0); pooled over the two ingests strict is 8 wins : 0 losses. On the held-out seed-2 sample (§8) the gain over the routed 9B shrinks to strict +6 (5:2), strict_v2 −2 (3:4), lenient-non-IDK +6 (5:2), official +4 (4:2), within noise, so read the clean-ingest +12 as an upper bound.
A 1200-token thinking cap truncates 22 of 28 calls and gives mixed results (strict +8, strict_v2 −2, official −2), so it is not a way to buy the
latency back. **Accuracy mode** (35B-A3B as the routed thinker) is documented as the batch/offline option in `docs/CHAT_MEMORY.md`.

## 7. Round 2–3 (afternoon, after the user asked for competitor methods): episodic recall + a reasoning reader

Competitor analysis (`hypotheses/R3_competitors.md`): published 85–95 % LongMemEval numbers come from GPT-4o/GPT-5-class readers under
the official (lenient-ish) judge; the memory-design technique repeatedly credited is keeping raw turns next to extracted facts (Zep loses
9–18 points without them). We ported the official judge, implemented episodic recall in the crate (`context_raw_messages`, commit 80fa6fae),
and tested reasoning readers.

**Configuration tested in this round (thinking on every question; superseded by the routed reader, 7a)** (crate defaults `context_max_chars` 6000, `context_raw_messages` 3 user turns, `context_quotes` 1; reader =
compute prompt with the 9B's thinking enabled, 2500-token cap, non-thinking fallback), paired on identical databases against the morning's
shipped configuration (quotes + concise + cascade):

| set / ingest | strict (Gemma) | strict_v2 | lenient non-IDK | official LME judge | IDK | s/answer |
|---|---:|---:|---:|---:|---:|---:|
| LME-50, clean ingest | 62 → 66 (5:3) | 56 → **76** (11:1, p 0.006) | 72 → 80 (5:1) | 58 → **70** (7:1, p 0.07) | 15 → 11 | 3.8 → 7.7 |
| LME-50, first ingest | 50 → 62 (9:3) | 52 → 70 (11:2, p 0.022) | 64 → 80 (10:2, p 0.039) | 48 → **68** (11:1, p 0.006) | 14 → 6 | 5.0 → 8.2 |
| LoCoMo conv0 (66 q) | 50 → 56 (12:8) | 56 → 67 (12:5, p 0.14) | 73 → 77 (8:5) | — | 9 → 10 | 3.8 → 8.5 |
| LoCoMo conv1 (38 q) | 58 → 50 (2:5) | 63 → 58 (3:5) | 76 → 74 (2:3) | — | 5 → 4 | 2.9 → 9.1 |

**LoCoMo is neutral overall under all-questions thinking** (pooled strict 14 wins : 13 losses over 104 questions; the routed reader in 7a is 16:9 over 133): the gain is a LongMemEval gain (assistant-heavy, reasoning-heavy questions); on two-human chat with short-phrase golds the thinking reader does not help and the richer context can distract. Ablation and judge audit in `judgments/locomo_c1.md`.

Judge audit (`judgments/round3_winner.md`): 7 real fixes / 1 regression (a thinking overrun → empty answer; the harness now falls back to a
non-thinking call) / 1 lucky guess / 8 judge-noise; judge-corrected gain ≈ +12 strict_v2, +10 official. The thinking reader carries most of
it (alone: strict_v2 56 → 68 on the unchanged context); the 6000-char budget adds ~+6 and the raw turns ~+2–4. Abstention stayed 4/4.

Reader ceiling (same 50 shipped contexts): 27B concise = no gain at 10x latency; 27B with thinking = strict_v2 70 / non-IDK 86 at 57 s per
answer (impractical); 9B with thinking = strict_v2 68 at 7 s; Claude Sonnet on a 20-question probe = strict_v2 75 vs 55 for the 9B with
identical memories. The memory delivers the evidence on 94–98 % of questions; the reader's reasoning is the remaining bottleneck.

Rejected or parked in rounds 2–3: self-consistency voting (0), assistant turns in the episodic block (−2), chronological ordering (untested),
cascade-only thinking (+6 strict_v2 but dominated by routed thinking), rule-based multi-query retrieval (opt-in, commit 2801aa31: +1 question on
each LME ingest, 0/0/−1 on three LoCoMo conversations).

Comparable number for the README: official LongMemEval judge, Oracle variant, local 9B reader: **64–74 %** on the seed-1 sample, 62 % on the held-out seed-2 draw (routed reader, 7a, 8; all-questions thinking gave 68–70 %; LoCoMo neutral to positive, pooled strict 16:9) (Zep: 63.8 with gpt-4o-mini,
71.2 with gpt-4o; Mem0/Mastra 85–95 with GPT-5-class readers).

## 10. The 27B as extractor (afternoon, deterministic mode): rejected

The last untested lever. One LongMemEval-50 ingest with `qwen/qwen3.8-27b` as the extractor (concurrency 1, 6.8 h) vs the two 9B
deterministic baselines: evidence@15 93.5 vs 95.7, 1124 memories vs ~1143, plain-reader official judge **58 → 40** (2:11, p 0.022 against
both), strict −10/−12; with the routed reader official 74 → 66 (3:7), strict flat. A larger extractor does not help this pipeline: its
memories are fewer and read worse for the 9B. Extraction quality is bounded by the prompt and the reader, not the extractor's size.

## 11. The embedding model (evening): null

bge-m3 (1024-d, fetched as a GGUF into LM Studio) replacing nomic-embed-text v1.5 for one deterministic LongMemEval-50 ingest:
evidence@15 93.5 vs 95.7, plain reader official judge 58 → 54 (4:6 against both 9B baselines), routed reader strict +4 / official −2.
Within noise; nomic stays. With this, every lever on the list has been measured: prompt, reader, routing, context rendering, budget,
retrieval channels and priors, multi-query, extraction prompt and fixes, extractor size, and the embedder.

## 9. Product feature: `memory_answer` (commit 3fd21804)

The routed protocol is now product code, not only a harness: `neuralgraph-memory answer "<question>" [--today YYYY-MM-DD] [--json]`,
the MCP tool `memory_answer`, and `POST /api/answer`. It builds the context with the shipped defaults, classifies the question with the
same regex, answers with the concise prompt or the compute prompt with thinking (2500-token cap, non-thinking fallback), and applies the
IDK cascade. Parity on the 50 clean-ingest questions, same judges: strict 74 vs the harness's 68 (3:0), strict_v2 80 vs 78, official 74 vs 74,
lenient-non-IDK 80 vs 80; 3.5 s mean per answer including retrieval. Existing tools and schemas are unchanged (the MCP tool list gained one entry).

## 8. Held-out validity check (passed)

Every number above was measured on the same 50 LongMemEval questions (seed 1) plus three LoCoMo conversations; prompts and routing were
chosen on them. A fresh stratified sample (seed 2, 50 never-seen questions, deterministic ingest; a harder draw: evidence@15 82.6, ceiling 84.8)
gives, final routed protocol vs the original pipeline:

| metric | original | morning shipped (quotes + cascade) | **final routed** | | original | morning shipped | **final routed** |
|---|---:|---:|---:|---|---:|---:|---:|
| | seed 2 | | | | seed 3 | | |
| strict (Gemma) | 50 | 46 | **60** (10:5) | | 54 | 50 | **70** (10:2, p 0.039) |
| strict_v2 | 50 | 52 | **72** (12:1, p 0.003) | | 66 | 62 | **76** (8:3) |
| lenient non-IDK | 48 | 50 | **66** (12:3, p 0.035) | | 74 | 68 | **82** (5:1) |
| official LongMemEval judge | 44 | 50 | **62** (13:4, p 0.049) | | 62 | 54 | **72** (9:4) |

The gain transfers to unseen questions: pooled over the 100 held-out questions the routed protocol beats the original 20:7 on strict and
22:8 on the official judge. **The morning's quotes-plus-cascade-only protocol does not generalize** (strict −4 on both draws, official −8 on
seed 3); routing, the thinking reader and the 6000-char episodic context carry the result, and the three must ship together. Accuracy mode (35B thinker) on seed 2: strict 66, strict_v2 70, lenient-non-IDK 72, official 66 — vs the original +16 / +20 / +24 / +22
(all p ≤ 0.08), vs the routed protocol +6 / −2 / +6 / +4 (within noise, at ~3× latency). Seed 3: vs the routed protocol strict 0, strict_v2 +2, lenient-non-IDK +4, official −2. **Across the two held-out draws accuracy mode adds ~+3 strict
and ~+1 official over the routed protocol at ~3× latency — within noise; the +12 on the tuning sample did not carry.** Keep it as an option, not
a recommendation.

## 6. Incidents and time budget

| time | event |
|---|---|
| 01:50–02:05 | baseline ingest started without `LLM_NO_THINK_PREFILL=1`; restarted |
| 01:55–02:00 | full test-suite compile pushed load to 140 and slowed the ingest; rule: no builds during ingests |
| 03:45–04:25 | HTTP 400s from LM Studio (judge TTL unload); two sweep launches lost; models pinned |
| 03:21, 03:48, 04:33 | dead extraction jobs (8k context overflow); Qwen reloaded at 16k 04:52; 3 databases re-ingested 06:10–07:00 |
| 06:08 | LME-50 baseline ingest complete (4.06 h instead of ~1.8 h, because the GPU was shared with sweeps and a second ingest) |
| 07:45–07:48 (night 1) | `lme_sub18_E3` (verbatim_fallback) ingest killed at 6/18, GPU starved; E3 untested |
| night 2 | reader-side identical re-runs differ by 1–2 questions (LM Studio batching; R4-locomo): round-4 deltas under 2 questions are not effects |
| night 2 | 35B thinker: 4 truncations at 2500 tokens (R4f-rm35-rep); with a 1200-token cap 22 of 28 thinking calls truncate (R4f-rm35-cap) |
| 10-29 01:25–06:34 | deterministic ingests E48 (2.6 h) and seed 2 (2.5 h) held the GPU |

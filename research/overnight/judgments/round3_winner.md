# Judge audit: round-3 winner (episodic 6k + thinking reader) vs shipped, LME Oracle 50 (`lme_base2`)

Read-only audit, no model calls. S = strict, S2 = strict_v2 (Gemma-4-e4b), Lni = lenient_nonidk, Off = official LongMemEval judge.
1 question = 2 points. Mechanism evidence comes from existing ablation result files (listed in section 2); I ran nothing new.

Inputs (`.context/overnight/results/`):
- BASE (shipped): `lme_base2_crateq1_concise_cascauto_v1n_acc.json`; its Off column `..._official_acc.json` (same 50 answers, 0 differ).
- VARIANT: `lme_base2_q1raw3_6k_auto_v1n_think_acc.json` (ctx `lme_base2_q1raw3_6k_ctx.json`, reader 9B thinking, `auto_v1n`, max_tokens 2500, one run, temp 0).
- Contexts: base `lme_base2_crateq1_ctx.json` (3600 chars), variant 6000 chars + up to 3 BM25 raw user turns (36 of 50 contexts got a raw block).

## 1. Recompute (n = 50 paired, same 50 ids, no unpaired rows)

`compare.py BASE VARIANT --metric strict,strict_v2,lenient_nonidk`

| metric | base | variant | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict | 62.0 | 66.0 | +4.0 | 5 | 3 | 0.727 | [-8.0, +16.0] |
| strict_v2 | 56.0 | 76.0 | +20.0 | 11 | 1 | 0.006 | [+8.0, +32.0] |
| lenient_nonidk | 72.0 | 80.0 | +8.0 | 5 | 1 | 0.219 | [+0.0, +18.0] |

`compare.py BASE_official VARIANT --metric official`

| metric | base | variant | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| official | 58.0 | 70.0 | +12.0 | 7 | 1 | 0.070 | [+2.0, +24.0] |

Extra (same script, not requested): lenient 88 -> 86 (3:4), lenient_v2 88 -> 80 (2:6, p .29), substring 50 -> 50 (3:3), strict_nonidk 60 -> 66 (5:2).
The two lenient columns credit "I don't know" answers on answerable rows; the 6 lenient_v2 losses are IDK-vs-IDK explanation flips
(`gpt4_d6585ce9`, `08f4fc43`, `1c0ddc50`), `7024f17c` (IDK -> "0 hours"), `c9f37c46`, `9aaed6a3`. Only the last 3 involve a changed answer. Not treated as a regression on accuracy.
Cost: mean answer time 3.8 s -> 7.7 s, mean context 770 -> 1022 tokens, one 50 s tail (`9aaed6a3`).

## 2. Mechanism ablation (existing files, all n = 50, same 50 ids)

| arm | context | reader | S | S2 | Lni | Off | file stem (`lme_base2_`) |
|---|---|---|---:|---:|---:|---:|---|
| BASE | 3.6k | concise + IDK cascade | 62 | 56 | 72 | 58 | `crateq1_concise_cascauto_v1n` |
| T | 3.6k | auto_v1n + thinking | 62 | 68 | 74 | 64 | `crateq1_auto_v1n_think` |
| D | 6k quotes | concise + cascade | 62 | 64 | 74 | n/a | `q1_6k_concise_cascauto_v1n` |
| C | 6k quotes | auto_v1n + thinking | 66 | 74 | 76 | 66 | `q1_6k_auto_v1n_think` |
| E | 6k + raw3 | concise + cascade | 62 | 68 | 78 | 62 | `q1raw3_6k_concise_cascauto_v1n` |
| **F (variant)** | 6k + raw3 | auto_v1n + thinking | 66 | 76 | 80 | 70 | `q1raw3_6k_auto_v1n_think` |

Marginals on S2 / Off: thinking alone (BASE -> T) +12 / +6; budget alone (BASE -> D) +8 / n/a; raw+budget without thinking (BASE -> E) +12 / +4;
raw added on top of 6k+thinking (C -> F) +2 / +4; thinking added on top of raw+6k (E -> F) +8 / +8.
Confound: "thinking reader" = the `auto_v1n` compute prompt on every question PLUS thinking. No existing arm has `auto_v1n` without thinking on all questions (the baseline cascade uses it only on IDK rows), so prompt and thinking cannot be separated.

## 3. Flip audit (every strict, strict_v2, official, lenient_nonidk flip; 17 ids)

Class: RF real-fix, RR real-regression, FO format-only, JN judge-noise, LG lucky/unsupported-correct. Mech: R raw-turn, B budget, T thinking(+prompt).
Ablation letters (T, D, C, E, F) refer to section 2; "=1" means that arm scored S2-correct on the id.

| id | type | gold | base answer | variant answer | flips (b>v) | class | mechanism and evidence |
|---|---|---|---|---|---|---|---|
| 09ba9854 | multi | $50 | IDK (no taxi price) | Around $50 | S2 Lni Off 0>1 | RF | **R**. Taxi "$60" and train "$10" appear only in the 2 added raw lines (base ctx has $40-50 car service, $10 bus). T=0, C=0, D=0; E=1, F=1 |
| 0f05491a | KU | 120 | 125 stars | 120 stars | S S2 Off 0>1 | RF | **T**. Base ctx already holds the quote "I need 120 stars, not 300" (dated 07-30, later than the 125 memory of 07-11). The 3 added raw lines hold no number. T=1, D=0, E=0 |
| aae3761f | multi | 15 hours | IDK (two trips found) | 15 hours (4+6+5) | S S2 Lni Off 0>1 | RF | **T** (B also sufficient). All three trips (4h, 6h, Tennessee 5h) are in the base ctx. T=1, D=1, C=1, E=0 |
| cc6d1ec1 | temporal | Two months | about three months | About 2 months | S2 Off 0>1 | RF | **T**. Base ctx has "three months as of 05-25" and "workshop one month ago"; variant subtracts. T=1, C=1, D=0, E=0 |
| gpt4_372c3eed | multi | 10 years | 8 years | 10 years | S S2 Lni Off 0>1 | RF | **B + T**. Quote "graduating from UCLA in 2020" is in no 3.6k context; with 2010 start gives 10. Raw lines irrelevant. Fragile: D, E answer 6, C answers 11, only F lands on 10 |
| 1192316e | multi | 1.5 h | "hour to get ready, commute 30 min" (no total) | 90 minutes | S 0>1 (S2 credited base) | RF | **T**. Both facts in base ctx; variant sums. Base was wrong (never totals); S2 over-credited it. T=1 strict |
| a2f3aa27 | KU | 1300 | IDK ("no updated count") | approximately 1300 followers | S2 Lni Off 0>1, S 1>0 | RF | **T** (R also sufficient). Base ctx has "approximately 1300 followers"; base refused on "now". T=1, C=1, E=1. S 1>0 is the strict judge crediting the IDK base and rejecting "approximately" (JN) |
| 7161e7e2 | SS-asst | Admon: 8am-4pm Day Shift | IDK | 8 am to 4 pm (Shift 1) | S2 Lni 0>1 | LG | **R** (E=1 too). Raw lines give the 4 shift times and "2 days off" but NOT Admon's Sunday slot (gold lives in the assistant reply, not ingested). Variant invented a full rota table and guessed. Off marks it wrong. Not a fix |
| 9aaed6a3 | multi | $0.75 | $0.75 (correct) | **empty** (50 s, token cap) | S S2 Off 1>0 | RR | **R + T**. Same needed facts ($75, 1%) in both ctx. Variant adds distractor $ amounts (Sephora $50, Gap $100, raw Office Depot $50). E answers IDK; F thinks past 2500 tokens, no `Answer:` line. T=1, C=1, D=1 |
| a3332713 | multi | $200 | $100 + $100 = $200 | $200 | S2 0>1 | FO | Base is right; Off agrees (1). S2 rejected the longer sentence. Both ctx hold both $100 facts |
| e56a43b9 | multi | $5 | $5 discount (+ 500 points reason) | $5 | S2 0>1 | FO | Base right, Off=1 |
| gpt4_1a1dc16d | temporal | The meeting with Rachel | Rachel first, April 10 vs May 1 | Rachel April 10 first | S2 0>1 | FO | Base right, Off=1; D, E, T also 1 |
| gpt4_a2d1d1f6 | temporal | 3 days ago | 3 days ago (on April 15) | 3 days ago | S2 0>1 | JN | Contexts byte-identical (2854 chars), answers equivalent, Off=1 for base. Pure S2 noise |
| 29f2956b | SS-user | 30 minutes | You practice 30 minutes daily | 30 minutes | S 0>1 | FO | S2 and Off credit both |
| gpt4_6dc9b45b | temporal | 4 months ago | 4 months ago (on June 1, 2021) | About 4 months ago | S 1>0 | JN | Strict penalised "About" on an exact match; S2, Off credit both |
| c9f37c46 | temporal | 2 months | about 3 months | About 1 month | Lni 1>0 | JN | Both wrong under S, S2, Off; lenient credited the base. (C answers 2 months correctly, so the variant added a wrong computation on a case the 6k+think arm solved.) |
| gpt4_fe651585_abs | abstention | not enough info | I don't know. | I don't know. There is no info about Tom... | Off 0>1 | FO | Both abstain. Off credits the explanation, not a behaviour change. Unstable: C and E ("I don't know") score 0 |

Totals over the 17 flipped ids: **real-fix 7** (6 visible in S2, plus `1192316e` visible only in strict), **real-regression 1** (`9aaed6a3`),
lucky/unsupported 1, format-only/judge-noise 8 (`a3332713`, `e56a43b9`, `gpt4_1a1dc16d`, `gpt4_a2d1d1f6`, `29f2956b`, `gpt4_6dc9b45b`, `c9f37c46`, `gpt4_fe651585_abs`).

Per-metric reading of the flips:

| metric | nominal wins:losses | real-fix : real-regression | noise / format / lucky among them |
|---|---|---|---|
| strict | 5:3 | 4 (0f05491a, 1192316e, aae3761f, 372c) : 1 | fixes: 29f2956b FO. losses: a2f3aa27, 6dc9b45b JN |
| strict_v2 | 11:1 | 6 : 1 | 4 FO/JN (a3332713, e56a43b9, 1a1dc16d, a2d1d1f6), 1 LG (7161e7e2) |
| lenient_nonidk | 5:1 | 4 : 0 | 1 LG; loss c9f37c46 JN |
| official | 7:1 | 6 : 1 | 1 FO (fe651585_abs) |

Fix attribution (7 real fixes):

| mechanism | count | ids |
|---|---:|---|
| raw-turn only | 1 | 09ba9854 |
| thinking reader only (detail already in base ctx) | 4 | 0f05491a, cc6d1ec1, aae3761f (B also sufficient), 1192316e |
| thinking, raw also sufficient | 1 | a2f3aa27 |
| budget + thinking (combination) | 1 | gpt4_372c3eed |
| raw-turn caused the one regression (plus thinking truncation) | 1 | 9aaed6a3 |

Judge-corrected delta: S2 +20 nominal is +6 net questions after removing 4 FO/JN and 1 LG and adding `1192316e` and the regression, i.e. about +12 points (+7 -1 = +6). Off corrected: 6 real - 1 = +5 = +10 points.
The judge-independent signal (substring) is flat, 50 -> 50, because many variant answers are bare values and many gold strings are sentences.

## 4. Abstention rows (4)

| id | base | variant | S2 (b/v) | Off (b/v) |
|---|---|---|---|---|
| 982b5123_abs | "I don't know." | "I don't know" | 1 / 1 | 0 / 0 |
| 2698e78f_abs | "I don't know." | "I don't know" | 1 / 1 | 0 / 0 |
| gpt4_fe651585_abs | "I don't know." | "I don't know. There is no information about Tom becoming a parent..." | 1 / 1 | 0 / 1 |
| eeda8a6d_abs | "I don't know. (explanation)" | "I don't know" | 1 / 1 | 0 / 0 |

**The thinking reader answered none of the 4 abstention questions** (4/4 IDK, same as base). Off is 0/4 vs 1/4 only because it wants an explanation (format).
Caveat: the reader did fabricate on a non-abstention question without support (`7161e7e2`, invented rota), so abstention safety on unanswerable assistant-side questions is not proven by n = 4.

## 5. Confident-wrong (answer is not IDK, judged wrong)

IDK = answer starts "I don't know" (harness `is_idk`). IDK count 15 -> 11 overall; on answerable rows 11 -> 7.

| basis | base | variant | ids (base -> variant) |
|---|---:|---:|---|
| strict_v2 | 11 | 5 | base: cc6d1ec1, c9f37c46, 0f05491a, 1a1dc16d*, 372c, 0bc8ad93, a3332713*, 7f6b06db, e56a43b9*, 0a995998, a2d1d1f6*. variant: 9aaed6a3 (empty), c9f37c46, 7024f17c, 7f6b06db, 0a995998 |
| official | 6 | 5 | base: cc6d1ec1, c9f37c46, 0f05491a, 372c, 0bc8ad93, 0a995998. variant: 9aaed6a3 (empty), c9f37c46, 7024f17c, 7161e7e2 (LG), 0a995998 |

`*` = S2 false negative (Off and my reading say correct). Fair comparison is the Off row: 6 -> 5, i.e. no increase in confident errors, and one of the 5 is an empty output rather than a wrong claim.
IDK-to-confident-wrong conversions in the variant: `7024f17c` ("0 hours", gold 0.5) and `7161e7e2` (lucky). Confident-wrong-to-IDK: `0bc8ad93`.

## 6. Leak check

| item | what I checked | result |
|---|---|---|
| `research/overnight/prompts/auto_v1n.txt` | whole-string and token match of the 50 gold answers from `research/datasets/longmemeval/longmemeval_oracle.json` against the prompt (lowercased); also the 50 question strings | No gold phrase. The only string hits are the digits "3" (`0a995998`) and "5" (`4adc0475`), which occur only as rule numbers / example text ("3 weeks ago", "5."), not as answers. Prompt examples ("45 minutes", "$275", "3 weeks ago", "Blue Harbor Bakery", 12-foot / 20-foot ladder) match no gold of the 50 |
| prompt rules | read | generic rules only (later DATE wins, show arithmetic, `Answer:` line); no question-specific content. Router code says it reads question text only, never type or gold |
| `crates/neuralgraph-memory/src/retrieval.rs` `append_raw_messages` | read whole fn, grep gold/has_answer/evidence | Inputs are only `query`, chat_id/speaker filter, char budget, dedupe set. Ranks by `message_keyword_candidates`; skips assistant turns; no gold/has_answer/evidence reference (the only grep hits in the file are comments at lines 41, 55, 818 about evidence recall) |
| `crates/neuralgraph-memory/src/store.rs` `message_keyword_candidates` | read whole fn | FTS5 bm25 (or LIKE) over `messages` using the question text; no gold access |
| context builder | `chat_memory_context.rs` | `has_answer` is read only to compute the diagnostic `evidence_retrieved` flag (lines 288-296); it is not written into `context`. Gold is stored in the row for the judge only; `answer_eval.py` passes only context, question, date to the reader |
| gold string inside context | substring test | gold appears verbatim in 24/50 variant contexts vs 23/50 base: no leak signature (it is the natural overlap) |
| oracle caveat | n/a | LongMemEval Oracle ingests only evidence sessions for both arms, so retrieval is easier than the full haystack. Applies equally to base and variant; absolute numbers are not comparable to the S-set |

## 7. Verdict

**confirm** (on `lme_base2`, n = 50), under the checklist: real fixes 7 vs real regression 1 (>= 2x), delta >= 4 on every primary metric (S +4, S2 +20, Lni +8, Off +12), nothing down on a primary metric (the lenient_v2 -8 is IDK-explanation noise, section 1).
Strength is lower than the headline: judge-corrected gain is about +10 to +12 points, strict and Lni are not individually significant (p .73, .22), Off p .07, and it is one temp-0 run on one ingest. Replication on the first ingest (`lme_base`) and LoCoMo (`round3_replicate.sh`) had not produced result files when I looked; treat shipping as gated on those.

- **Thinking reader carries the gain**: it alone moves S2 56 -> 68 and Off 58 -> 64 on the unchanged 3.6k context, and 5 of 7 real fixes need only it.
- **Episodic turns are a small, unproven add-on**: one clean fix (`09ba9854`), +2 S2 / +4 Off over 6k+thinking (1-2 questions), and the single real regression (`9aaed6a3`) traces to raw-line distractors plus a thinking overrun, so keep them only if replication shows a similar lift.
- Risks to fix regardless: thinking overrun returns an empty answer (1/50, 50 s) with no fallback; reader confabulates on assistant-side questions (`7161e7e2`); answer latency doubles.

Ledger row to paste (replace the `promising -> replicating` line for R3-WINNER):

`| R3-WINNER | S0 | LME-50 (base2) | quotes + 3 raw user turns at 6000 chars + 9B thinking compute reader (auto_v1n) | lme_base2 / crate q1 raw3 6k / auto_v1n think | 66.0 | — | — | 93.5 | +4.0 | — | 0.73 | confirmed-S1 (judge), replication pending | judge round3_winner.md: 7 real fixes / 1 real regression (9aaed6a3 raw distractors + think overrun, empty answer); S2 56->76 (11:1) is inflated by 4 format/judge-noise flips + 1 lucky guess (7161e7e2): corrected ~+12 pts; Off 58->70 (7:1, p .07), Lni 72->80. 0/4 abstention answered; confident-wrong (Off) 6->5. Gain is the thinking reader (T alone S2 68, Off 64); raw turns add 1 clean fix (09ba9854), +2 S2 over 6k+think. Leak check clean. lme_base2_q1raw3_6k_auto_v1n_think_acc.json |`

# Judge audit: R3 and R6 vs R0 baseline, LongMemEval Oracle full 50

Read-only audit, one output file, no model calls. Notation: S = strict, S2 = strict_v2, L = lenient, L2 = lenient_v2
(all Gemma-4-e4b). 1 question = 2 points. Second judge (Qwen3.6-35B `_j35_acc.json`, `scripts/s2_regrade.sh`) has
not produced files yet, so PLAN stage "S2 judge-robust" is NOT done; this audit is a single-judge audit plus the
judge-free `substring` metric.

Inputs (`.context/overnight/results/`): base `lme_base_vk15_concise_acc.json`; R3 `..._quotes_user_cascauto_v1_acc.json`;
R6 `..._quotes_user_cascauto_v1_qpm2_acc.json`; R2 `..._quotes_user_acc.json`; contexts `lme_base_vk15_ctx.json`.
Configs differ only in `render`, `cascade`, `quotes_per_memory` (diffed all config keys: judge model, prompt, temperature,
prefill, extra_judges identical). One shared ctx file, so retrieval is identical across all four runs.

## 1. Recompute

Ids: all four files and the ctx file have the same 50 ids (asserted), n = 50 paired, no unpaired rows.

`compare.py base R3 --metric strict,strict_v2,lenient,lenient_v2,lenient_nonidk`

| metric | base | R3 | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict | 42.0 | 52.0 | +10.0 | 7 | 2 | 0.180 | [-2.0, +22.0] |
| strict_v2 | 44.0 | 60.0 | +16.0 | 9 | 1 | 0.021 | [+6.0, +28.0] |
| lenient | 72.0 | 74.0 | +2.0 | 4 | 3 | 1.000 | [-8.0, +12.0] |
| lenient_v2 | 78.0 | 72.0 | -6.0 | 2 | 5 | 0.453 | [-16.0, +4.0] |
| lenient_nonidk | 50.0 | 62.0 | +12.0 | 7 | 1 | 0.070 | [+2.0, +22.0] |

`compare.py base R6 ...`

| metric | base | R6 | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict | 42.0 | 58.0 | +16.0 | 8 | 0 | 0.008 | [+6.0, +28.0] |
| strict_v2 | 44.0 | 54.0 | +10.0 | 6 | 1 | 0.125 | [+0.0, +20.0] |
| lenient | 72.0 | 74.0 | +2.0 | 4 | 3 | 1.000 | [-8.0, +12.0] |
| lenient_v2 | 78.0 | 74.0 | -4.0 | 3 | 5 | 0.727 | [-16.0, +6.0] |
| lenient_nonidk | 50.0 | 60.0 | +10.0 | 6 | 1 | 0.125 | [+0.0, +20.0] |

`compare.py base R2 ...` (quotes only, no cascade): strict 50.0 (+8.0, 5/1, p 0.219), strict_v2 52.0 (+8.0, 5/1),
lenient 70.0 (-2.0, 2/3), lenient_v2 72.0 (-6.0, 2/5), lenient_nonidk 56.0 (+6.0, 4/1, p 0.375).

Extra columns I computed from the rows (not in compare.py output):

| metric | base | R2 | R3 | R6 |
|---|---:|---:|---:|---:|
| substring (judge-free) | 38.0 | 40.0 | 40.0 | 42.0 |
| strict_nonidk (IDK never credited) | 38.0 | 46.0 | 50.0 | 54.0 |
| strict_v2 minus IDK credits | 44.0 | 48.0 | 54.0 | 52.0 |
| answers that are "I don't know" (of 50) | 24 | 16 | 13 | 14 |
| IDK on answerable rows | 20 | 12 | 9 | 10 |
| mean prompt tokens (rendered, est. chars/4) | 561 | 942 | 942 | 997 |

Read: the strict_v2 headline for R3 (60, p 0.021) is inflated by 3 IDK answers that S2 credited (section 2). After removing
them R3 and R6 are +10 and +8 on S2, and +12 and +16 on strict_nonidk. The prompt grows by 68% (R3) / 78% (R6).

## 2. Flip audit (every strict and strict_v2 flip)

Union of strict and strict_v2 flips: R3 13 ids, R6 10 ids. Verdict arrows are base -> variant. "gold" shortened.
Mechanism key: quote = no cascade involved, the variant answer came from the quote-rendered primary prompt;
cascade = the primary answer under the quote render was still IDK and the `auto_v1` re-ask produced the answer
(`cascaded: true`, `answer_primary` is the IDK).

### R3 (13 flips)

| id | type | gold | base answer | R3 answer | flipped on | class | mechanism |
|---|---|---|---|---|---|---|---|
| 37d43f65 | single-user | 16GB | IDK, amount not specified | "upgraded ... RAM to 16GB" | S, S2 up | real-fix | quote: "16GB" only in the user quote ("Before the RAM upgrade to 16GB"); no memory states it |
| 9aaed6a3 | multi | $0.75 | IDK, "no transactions last Thursday" (called 05-18 a Saturday) | "$0.75 ... last Thursday (2023-05-18), 1% of $75" | S, S2 up | real-fix | quote: user quote "I spent $75 ... last Thursday" bridges Thursday to the 05-18 memory; R2 also fixes it |
| a3332713 | multi | $200 | $20 mug (best friend) + $100 card = $120 | $100 card + $100 baby clothes = $200 | S, S2 up | real-fix | quote (disambiguation): both $100 memories were already in plain ctx; quotes tie baby clothes to the coworker and mug to the best friend; R2 also fixes it |
| 1192316e | multi | an hour and a half | IDK (no commute duration) | "90 minutes" | S up | real-fix | cascade: 1h routine + "30-minute daily commute" were both in plain ctx; primary stayed IDK; compute prompt added them. R2 does not fix it |
| aae3761f | multi | 15 hours (3 trips) | IDK (third trip unknown) | "15 hours" | S, S2 up | real-fix | cascade: 4h + 5h + "six-hour trip to Washington D.C." all in plain ctx; primary IDK; cascade summed. R2 does not fix it |
| a2f3aa27 | knowledge-update | 1300 | IDK, "approximately 1300 ... not current" | "Approximately 1300 followers" | S down, S2 up | real-fix (S2); strict flip is judge-noise | cascade: same memory both runs; primary IDK; cascade copied the value. Base S=True was a strict-judge false credit of an IDK; R3 S=False rejects "Approximately" which S2 accepts |
| 4adc0475 | multi | 5 | "3 goals and 2 assists, total of 5" | "3 goals and 2 assists" | S down | real-regression (minor) | quote render: question asks for a total, answer omits the sum (S2, L, L2 still credit it). R2 has the same regression |
| a82c026e | single-user | Dark Souls 3 DLC | "Dark Souls 3 DLC after weeks of trying" | "Dark Souls 3 DLC last weekend" | S up | format-only | none (both contain the gold; strict judge rejected the base for no content reason; S2 had it right) |
| db467c8c | knowledge-update | nine months | "nine months on a tourist visa (B-2)" | "nine months" | S up | format-only | none (extra clause only; S2 had it right in base) |
| 7024f17c | multi | 0.5 hours | IDK ("30-minute jog, no yoga") | same IDK, reworded | S2 up | judge-noise | S2 credited an IDK; content identical |
| 09ba9854 | multi | $50 | IDK (no train fare) | IDK (no train fare, taxi ~$60) | S2 up | judge-noise | S2 credited an IDK |
| gpt4_d6585ce9 | temporal | my parents | IDK | IDK ("friends in Brooklyn") | S2 up | judge-noise | S2 credited an IDK; answer names "friends", gold is "my parents" |
| gpt4_a2d1d1f6 | temporal | 3 days ago | "3 days ago (on April 15, 2023)" | byte-identical string | S2 down | judge-noise | identical answer, different verdict; same flip appears in R2, R3, R6 |

### R6 (10 flips)

| id | gold | R6 answer | flipped on | class | mechanism |
|---|---|---|---|---|---|
| 37d43f65 | 16GB | "upgraded ... to 16GB" | S, S2 up | real-fix | quote |
| 9aaed6a3 | $0.75 | "$0.75 ... (2023-05-18)" | S, S2 up | real-fix | quote |
| a3332713 | $200 | "$100 gift card + $100 baby clothes = $200" | S, S2 up | real-fix | quote (disambiguation) |
| 4dfccbf7 | 24 days | "Feb 1 ... Feb 25 ... 24 days" | S up (S2 stays False: judge reads "**24 days**" wrong) | real-fix | quote placement: with 2 quotes per memory the "just got back from Joe's shop" quote (2023-02-25) moves under the "fixed during servicing" memory; R2/R3 gave "approximately 28 days" on the other layout (40 of 50 renders differ between R3 and R6) |
| 1192316e | an hour and a half | "90 minutes" | S up | real-fix | cascade |
| aae3761f | 15 hours | "15 hours" | S, S2 up | real-fix | cascade |
| a82c026e | Dark Souls 3 DLC | "... last weekend" | S up | format-only | none |
| db467c8c | nine months | "nine months" | S up | format-only | none |
| 09ba9854 | $50 | IDK | S2 up | judge-noise | S2 credited an IDK |
| gpt4_a2d1d1f6 | 3 days ago | identical string | S2 down | judge-noise | identical answer flipped |

### Counts

| class | R3 | R6 |
|---|---:|---:|
| real-fix | 6 | 6 |
| real-regression | 1 (4adc0475, minor) | 0 |
| format-only | 2 | 2 |
| judge-noise | 4 | 2 |
| total flipped ids | 13 | 10 |

| mechanism of the real fixes | R3 | R6 |
|---|---:|---:|
| quote | 3 (37d43f65, 9aaed6a3, a3332713) | 4 (+4dfccbf7) |
| cascade | 3 (1192316e, aae3761f, a2f3aa27) | 2 (1192316e, aae3761f) |
| both | 0 | 0 |

R6 loses the a2f3aa27 rescue: its cascade re-ask (same prompt, different quote layout) still answered IDK.
R2 (no cascade) is a clean ablation: it reproduces the quote fixes (37d43f65, 9aaed6a3, a3332713) and none of the cascade fixes.

Context for how much of this is noise: R2 and R3 share the same primary prompt; their primary answers are byte-identical on
49 of 50 rows (only aae3761f differs, both IDK). The answerer is effectively deterministic, so every answer change above
is caused by the prompt change, not sampling. The judge is not deterministic: one identical string (gpt4_a2d1d1f6) flips
in every variant, and the S judge credited base IDKs (a2f3aa27, gpt4_2ba83207).

Net audited effect, in questions: R3 +6 -1 = +5 (+10 pts); R6 +6 -0 = +6 (+12 pts). Exact two-sided sign test on the
audited fixes vs regressions: R3 6:1 p = 0.125, R6 6:0 p = 0.031. LoCoMo c0 (66 q) does not replicate a strict gain
(R3 +1.5, 4/3; R6 +0.0, 5/5) but is not worse; pooled raw strict wins/losses R3 11:5 (p 0.21), R6 13:5 (p 0.096).

## 3. Abstention rows (4)

Question: did any variant answer something that should have been declined? No. All four stay "I don't know" in base, R2, R3
and R6, and all four are credited by every metric. R3/R6 ran the cascade on all four (primary was IDK) and the cascade
did not override (`cascaded: false`).

| id | question | base / R2 / R3 / R6 |
|---|---|---|
| 982b5123_abs | When did I book the Airbnb in Sacramento? | IDK in all |
| 2698e78f_abs | How often do I see Dr. Johnson? | IDK in all (R6 adds "memories only mention Dr. Smith") |
| gpt4_fe651585_abs | Who became a parent first, Tom or Alex? | IDK in all |
| eeda8a6d_abs | How many fish are there in my 30-gallon tank? | IDK in all (see leak finding 5a: this exact question is the example in `auto_v1.txt`) |

Caveat: n = 4, and the cascade prompt's rule 4 already coaches this exact case. The abstention risk of the cascade is not
tested by this set. Also relevant: of the 11 base IDKs that became answers under R3 (10 under R6), 5 are correct and
6 (R3) / 5 (R6) are confident wrong answers (table in section 4). That is the abstention risk showing up on answerable rows.

## 4. Why lenient_v2 dropped 78 -> 72 (R3) / 74 (R6)

Confirmed: the drop is IDKs that L2 credited becoming wrong answers. L2's own text says a refusal is WRONG outside
abstention, yet Gemma credited 16 of the 20 base IDKs on answerable rows (lenient v1 credited 11).

| L2 credit source | base | R3 | R6 |
|---|---:|---:|---:|
| non-IDK answers credited | 19 | 26 | 25 |
| answerable IDKs credited | 16 | 6 | 8 |
| abstention rows credited | 4 | 4 | 4 |
| total (x2 = score) | 39 (78) | 36 (72) | 37 (74) |
| answerable IDKs remaining (of 20 in base) | 20 | 9 | 10 |

Net: +7 (R3) / +6 (R6) real non-IDK credits, minus 10 (R3) / 8 (R6) lost IDK credits.

L2 losses (identical in R3 and R6):

| id | gold | base | variant | reading |
|---|---|---|---|---|
| 08f4fc43 | 30 days | IDK (L2 credited) | "19 days" | IDK -> confident wrong |
| e56a43b9 | $5 | IDK (L2 credited) | "$1 discount per 100 points" (never multiplies out) | IDK -> confident wrong |
| f9e8c073 | five | IDK (L2 credited) | "three sessions" | IDK -> confident wrong |
| gpt4_372c3eed | 10 years | IDK (L2 credited) | "6 years" | IDK -> confident wrong |
| 1c0ddc50 | podcast preference rubric | history podcasts | history podcasts, reworded | judge-noise (preference rubric, both generic) |

L2 gains in R3: a3332713 (real) and 1a1907b4 (IDK both runs, L2 flipped to credit: noise); R6 also 2ebe6c90 (IDK both, noise).

Transition of the 20 base answerable IDKs (strict_v2 as arbiter):

| outcome | R3 | R6 |
|---|---:|---:|
| still IDK | 9 | 10 |
| became correct | 5 (a2f3aa27, 9aaed6a3, 1192316e, 37d43f65, aae3761f) | 5 (9aaed6a3, 1192316e, 37d43f65, 4dfccbf7*, aae3761f) |
| became confident wrong | 6 (08f4fc43, e56a43b9, f9e8c073, gpt4_372c3eed, gpt4_7f6b06db, 4dfccbf7 "approx 28 days") | 5 (same minus 4dfccbf7) |
| non-IDK base -> IDK | 0 | 0 |

(*4dfccbf7 correct by S, not S2.) So the L2 drop is mostly a measurement effect, but it is also a real trade: roughly half
of the IDKs the changes unlock are wrong answers, and L2 and L cannot tell the difference. Confident-wrong answers on
answerable rows rose from 8 (base) to 14 (R3, R6) by S2. Non-IDK precision by S2: base 18/26 = 69%, R3 23/37 = 62%,
R6 22/36 = 61%.

## 5. Leak check

Greps run: `gold|has_answer|evidence|abstention|qtype|["type"]` over `scripts/answer_eval.py`; `gold|answer|has_answer|evidence|source_quotes`
over `crates/neuralgraph-research/src/benchmarks/chat_memory_context.rs` and `chat_memory_longmemeval.rs`; the same words over
`prompts/*.txt`; plus a check of prompt example strings against the LME-50 questions and golds.

| item | result |
|---|---|
| `answer_eval.py` answering path (`run_prompt`, `answer_one`, `render_memories`) | reads only `question`, `question_date`, `context`, `source_quotes`, `memory_ids`. `gold`, `type`, `evidence_retrieved` are not read before the answer. Cascade/route comments say the label is never used; the code matches (routing uses question regex only). Clean |
| `answer_eval.py` grading path | `gold`/`type` used only in `grade()` and `substring_ok`; abstention rows get the fixed `ABSTENTION_GOLD`. `evidence_retrieved` is copied into the output row, never into a prompt. Clean |
| `chat_memory_context.rs` | `has_answer` turns build `gold_turns`, used only to compute the `evidence_retrieved` flag (`cites_gold`). Quotes come from `search(... with_sources: true)` restricted to the memories in the block, no filter on gold. `gold` is written to the row for the judge. Clean for answering |
| `chat_memory_longmemeval.rs` ingest | `has_answer` only fills `gold_turns` for metrics; ingest sends `role/content/sent_at` only. Clean |
| prompt files `concise` (inline), `auto_v1.txt` | no field reads. Examples are literal strings (see 5a) |
| Oracle haystack | only evidence sessions are ingested (dataset property), so quotes naturally contain the answer for single-session questions. Not a harness leak; it inflates both baseline and variants, and likely overstates how much quotes help on a full haystack |
| judge parity | same judge model/prompts/prefill for base and variants (config diff), separate cache files per run |

### 5a. Finding: `prompts/auto_v1.txt` examples are LME-50 golds

| example in auto_v1.txt | matches LME-50 row | where in the row |
|---|---|---|
| rule 4: "a 10-gallon tank when asked about a 30-gallon tank" | eeda8a6d_abs "How many fish are there in my 30-gallon tank?" | question text |
| rule 5: "30 minutes" | 29f2956b | gold |
| rule 5: "$140" | 1c549ce4 | gold |
| rule 5: "4 months ago" | gpt4_6dc9b45b | gold |
| rule 5: "Thrive Market" | gpt4_2ba83207 | gold |

Introduced in b3919d9b (R1/R2 harness commit), before the LME-50 runs. The same file is the R3/R6 cascade prompt.
(`COMPUTE_PROMPT` in `answer_eval.py` also says "30 minutes = 0.5 hours", the gold of 7024f17c; not used by R3/R6.)

Impact on R3/R6 numbers: none observable. The cascade fires only on a primary IDK. Of the five rows, three were never IDK
(29f2956b, 1c549ce4, gpt4_6dc9b45b: non-IDK and the same verdicts in base, R2, R3, R6), and the two that were IDK (eeda8a6d_abs,
gpt4_2ba83207) stayed IDK after the cascade. None of the 3 cascade rescues (1192316e, aae3761f, a2f3aa27) is among them.
So this is prompt-tuning contamination, not an answer leak, and the reported deltas stand. But the prompt must be scrubbed
before it is trusted or shipped: swap the five examples for neutral ones and rerun the cascade on the 16 rows where the primary was IDK
(16 calls) to confirm the 3 rescues survive. I could not run it (no model calls).

## 6. Verdicts

| variant | verdict | one sentence |
|---|---|---|
| R3 | confirm (lower margin) | 6 real fixes (3 quote, 3 cascade) vs 1 minor real regression, strict +10; the S2 +16 is inflated by 3 IDK credits (+10 after removing them) and the lenient_v2 -6 is the IDK-credit artifact plus 4 IDK -> confident-wrong cases, so the strict "nothing down > 2" clause is met only after that explanation. |
| R6 | confirm | 6 real fixes (4 quote, 2 cascade) vs 0 real regressions, strict +16 (8:0, p 0.008), substring +4 and strict_nonidk +16 agree, lenient_v2 -4 is the same artifact. |

Ship: R6. It has no audited regression, the best judge-free numbers (substring 42, strict_nonidk 54), the better LoCoMo
lenient/lenient_nonidk (+4.5/+6.1 vs +1.5/+3.0), and costs only ~6% more prompt than R3. R3's extra strict_v2 comes from IDK
credits, not answers. Caveats that travel with the ship decision: (1) LoCoMo strict is flat (R6 5:5), so the LME gain is
IDK recovery, not general accuracy; (2) about half of unlocked IDKs become confident wrong answers (5 of 10 in R6);
(3) prompt grows from ~561 to ~997 tokens (+78%); (4) scrub `auto_v1.txt` and re-run the 16 cascade rows; (5) the
second-judge regrade (`_j35`) is still pending, so the sign under a different judge model is unverified; (6) R6's cascade
is more brittle (a2f3aa27 lost) and 4dfccbf7 depends on quote placement, so expect +-2 questions of run-to-run layout noise.

## 7. Ledger notes (exact)

Replace the `verdict` and `notes` cells of the existing R3 and R6 rows (they currently read `promising` and carry only the file name):

R3 row, verdict cell: `promising (judge: confirm)`
R3 row, notes cell:
`judge lme50_winners.md: 6 real fixes (3 quote: 37d43f65/9aaed6a3/a3332713; 3 cascade: 1192316e/aae3761f/a2f3aa27) / 1 minor real regression (4adc0475, total omitted) / 2 format-only / 4 judge-noise. strict_v2 +16 inflated by 3 IDK credits (+10 without). lenient_v2 -6 = L2 credited 16/20 base IDKs; 4 of 5 losses are IDK->confident wrong. Abstention 4/4 held. Leak: auto_v1.txt examples are LME-50 golds (no effect on R3, scrub + rerun 16 cascade rows). 2nd judge pending.`

R6 row, verdict cell: `promising (judge: confirm, ship)`
R6 row, notes cell:
`judge lme50_winners.md: 6 real fixes (4 quote: 37d43f65/9aaed6a3/a3332713/4dfccbf7; 2 cascade: 1192316e/aae3761f) / 0 real regressions / 2 format-only / 2 judge-noise. strict 8:0 p=0.008, substring +4, strict_nonidk +16, strict_v2 +10 (+8 without IDK credits). lenient_v2 -4 = IDK-credit artifact (5 of 10 unlocked IDKs became confident wrong). LoCoMo c0 strict flat (5:5), lenient +4.5. Abstention 4/4 held. Prompt +78% tokens. Scrub auto_v1.txt examples (LME-50 golds) and rerun 16 cascade rows; 2nd judge pending. Preferred over R3.`

New audit row:

`| R3R6-audit | S0 | LME-50 | judge audit of R3 and R6 vs R0 | lme_base / vk15 / concise+quotes_user +casc:auto_v1 (R3: 1 quote, R6: 2 quotes) | 58.0 | 74.0 | 42.0 | 95.7 | +16.0 | +2.0 | 0.008/1.000 | pending | confirm R6 (ship), confirm R3 (lower margin). Figures are R6. Blockers before S4: scrub auto_v1.txt examples and rerun the 16 cascade rows; run s2_regrade.sh (_j35) and check the sign holds; LoCoMo strict flat. |`

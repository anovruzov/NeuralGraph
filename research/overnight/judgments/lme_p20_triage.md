# LME-20 triage: quotes (P7g, P7b), IDK cascade (P6), auto_v1 (P1)

Independent judge, read-only, no model calls. Base `lme_p20_vk15_concise_acc.json` (B0-p20). n = 5 points per question: everything
below is mechanism, not significance (best McNemar p is 0.22). Answerer qwen3.8-9b-mlx; judges gemma-4-e4b (v1 and v2 prompt pairs).
Manual truth ("true") = my own reading of gold vs answer; an "I don't know" counts as correct only on the 2 abstention rows.

## 1. Recompute (compare.py, n = 20 paired for all four, no unpaired ids)

| variant | strict | lenient | substr | strict_v2 | lenient_v2 | true (of 20) | IDK answers |
|---|---:|---:|---:|---:|---:|---:|---:|
| B0-p20 | 35 | 80 | 35 | 40 | 70 | 8 | 10 |
| P6-p20 cascade | 40 (+5, 1W/0L) | 85 (+5) | 35 | 45 (+5, 1W/0L) | 70 (0) | 9 | 9 |
| P7g-p20 quotes k20/7000 | 30 (-5, 1W/2L) | 75 (-5) | 30 | 35 (-5, 0W/1L) | 65 (-5) | 6 | 9 |
| P7b-p20 quotes kl10 | 35 (0, 2W/2L) | 75 (-5) | 35 | 40 (0, 1W/1L) | 70 (0) | 7 | 8 |
| P1-p20 auto_v1 | 40 (+5, 2W/1L) | 60 (-20, p 0.22) | 30 | 40 (0, 2W/2L) | 50 (-20) | 9 | 10 |
| (P7h quotes k20 + cascade, ledger only) | 40 | 75 | 30 | 45 | 60 | 8 | 6 |

Leak check: gold is read only in `grade()`/judge prompts; `run_prompt`/`answer_one` (answer_eval.py) use `question`, `question_date`,
`context`, `source_quotes`, `memory_ids` only, and the cascade docstring says row type and label are never consulted. Pass.

## 2. Judge reliability on this set (read before trusting any delta)

| finding | evidence | consequence |
|---|---|---|
| Lenient judge pays for "I don't know" | baseline: 8 of 10 IDK answers are lenient-CORRECT (only 2 are abstention rows); strict credits 3 of 10 | baseline lenient 80 is 8 real + 8 IDK credits; every lenient flip that touches an IDK is noise |
| Strict v1 keyword false-positive | a2f3aa27 gold 1300: baseline "I don't know ... approximately 1300 followers" is strict-CORRECT; bare "I don't know" is not | P1 "regression" on a2f3aa27 is noise |
| Strict v1 flips on identical content | a82c026e: "...Dark Souls 3 DLC after weeks of trying" strict-WRONG, "...Dark Souls 3 DLC last weekend" strict-RIGHT; v2 accepts both. 29f2956b: "30 minutes daily" wrong vs "30 minutes" right | strict v1 +5s from these are noise; strict_v2 is the better column here |
| Substring cannot see numerals-in-prose | 4adc0475 "3 goals and 2 assists" fails substring "5" | format-only class |
| `mean_ctx_tokens` under-reports quotes | counts only the plain memory block (583 vs 711) | real prompt size is about 3.2x (section 3b) |

## 3. Quotes (P7g, P7b): every flip

Classes: real-fix, real-regression, judge-noise, format-only, context-changed (quote pushed out / misled).
P7g and P7b give identical answers on 4adc0475, 778164c6, a82c026e, f9e8c073; they differ only on 9aaed6a3.

| id | type | gold | flip | base answer | quotes answer | class | why (from the contexts) |
|---|---|---|---|---|---|---|---|
| a82c026e | single-session-user | Dark Souls 3 DLC | S+ (both) | "...Dark Souls 3 DLC after weeks of trying." | "...Dark Souls 3 DLC last weekend." | judge-noise | both contain gold; v2/substring accept both. Rendering did not matter. |
| 4adc0475 | multi-session | 5 | S- substr- (both) | "3 goals and 2 assists, for a total of 5." | "3 goals and 2 assists." (no sum) | format-only | both pieces are in the quotes (user said "2 assists", "3 goals"); v2 and lenient accept. Model dropped the final sum; the concise prompt does not ask for a total. Weak context-changed flavour: more raw text, less synthesis. |
| 778164c6 | single-session-assistant | Grilled Snapper with Mango Salsa | S- (both), v2 S- | "...is Grilled Snapper with Mango Salsa." | "I don't know. ...Escovitch Fish does not contain fruit... mango salsa is not specifically Jamaican" | real-regression (context-changed: quote misled) | the assistant quote on the mango memory is the first 300 chars of the reply: "Escovitch Fish - a Jamaican dish ... 2. Fish Curry" and is cut before the mango item. The same Escovitch turns are quoted under most of the other memories, so "Jamaican = Escovitch" is reinforced repeatedly and the model obeys the question's premise literally. Lenient still accepts (it names the dish). |
| f9e8c073 | knowledge-update | five | L- (both) | "I don't know ... not specify the number of sessions" | "You attended three sessions" | judge-noise on the metric; behaviourally IDK -> stale wrong | baseline lenient credit was spurious (wrong IDK). Quote "I did attend three sessions" is dated 2023-05-11 (older session); "five" is in no memory or quote of this row, so IDK was the right call. Quote surfaced a real but stale number and the model answered confidently. |
| 9aaed6a3 | multi-session | $0.75 | S+ L+ (P7b only) | "I don't know ... 2023-05-18 (a Saturday)" | "$0.75 cashback ... last Thursday (2023-05-18), 1% of $75" | real-fix (P7b only) | baseline mis-weekdays 05-18 (it is a Thursday) and refuses. Quote carries the user's literal "spent $75 ... last Thursday", matching the question. At k20 (P7g) the same answer is IDK again although the 1% cashback memory is rank 1: dilution at 9.1k chars, not a lost memory. |

| class | P7g | P7b |
|---|---:|---:|
| real-fix | 0 | 1 |
| real-regression (context-changed) | 1 | 1 |
| judge-noise | 2 (a82c026e, f9e8c073) | 2 |
| format-only | 1 (4adc0475) | 1 |

No memory was pushed out of the budget in P7g (see 3a), so "context-changed" here always means the quote text itself misled or diluted.

### 3a. Context statistics (rendered with the real `render_memories`, quote_chars 300, up to 3 quotes per memory)

| measure | LME base k15 plain | LME P7g (k20/7000, quotes) | LME P7b (kl10, quotes) | LoCoMo c0 k20 (P7g-L) |
|---|---:|---:|---:|---:|
| mean memories per row | 14.0 | 17.4 | 10 (cap) | 20 |
| mean chars, plain block | 2334 | 2848 | 1655 | 2674 |
| mean chars, as sent to the model | 2334 | 9214 (3.2x plain) | 5388 (3.3x) | 8840 (3.3x) |
| quotes per row | - | 21.8 | 12.9 | 28.5 |
| baseline memories missing from variant | - | 0 of 20 rows (superset, same order; 15 rows gained 2-5) | 19 of 20 rows lost ranks 11-15 (mean 4.3 lost) | - |
| 7000-char cap ever binding | - | no (max plain block 4055) | - | - |
| assistant-turn quotes | - | 180 of 436 = 41.3% (all 20 rows) | 88 of 258 = 34.1% | n/a (speakers are named) |
| exact-duplicate quote text within a block | - | 51.4% | 39.1% | 21.6% |
| quotes hitting the char cap (cut mid-turn) | - | assistant 179/180 (99%), user 86/256 (34%); 61% overall | assistant 87/88, user 60/170 | 11% overall |
| median stored quote length | - | user 249, assistant 400 (cap) | same | short (one named turn) |

Reading: size inflation (3.2x) is the same as LoCoMo, so size alone does not explain the difference. What differs is quote quality.

### 3b. Why the LoCoMo win does not carry over

| LoCoMo (8 real fixes) | LME-oracle (this set) |
|---|---|
| Extracted fact lost a detail that sits in one short user turn; the quote restores it ("I painted that lake sunrise last year", "self-care is really important") | The needed value is already in the memory text: 19/20 rows have `evidence_retrieved`; of the 12 truly wrong baseline rows, 10 are arithmetic, aggregation, stale-vs-latest, or preference failures that no quote can repair (the other two are 9aaed6a3 and 0f05491a) |
| One quote per turn, 22% duplicates, 11% truncated, all speakers are named humans | LME turns are long and half the quotes are assistant replies: 99% of them are clipped at the cap, so they show the preamble of a list, not the item. 51% of lines are repeats of the same turn |
| Quotes reinforce the true speaker statement | Assistant text is authoritative-looking but unreliable: 0f05491a quotes an assistant turn saying "400 stars" and another saying "300" under the top memory; the user's later correction "I need 120 stars" is a quote only at rank 17 (outside k15). Model answered 400 under P7g and P7b; baseline said 300. Gold 120 never used |
| Few distractor sessions per question | Oracle haystack is all-relevant sessions with many same-topic memories (Escovitch, Starbucks); repeating a stale or off-point turn increases its weight |

Where quotes did help or could have: 9aaed6a3 (relative phrase "last Thursday" appears verbatim only in the user quote) and 0f05491a
(the gold is only in a user quote, but ranked too low and out-voted by assistant quotes). Both are user-turn evidence.

## 4. IDK cascade (P6-p20)

Mechanism check: 10 primary answers were IDK (8 answerable + 2 abstention). The cascade re-asks only those and keeps the re-ask
unless it is also IDK. `cascaded` is therefore True only when the rescue was adopted. P1-p20 is the same prompt, context and temperature
on all rows, so its answers show what every re-ask returned.

| id | type | gold | primary answer | re-ask (= P1 answer) | adopted | rescue right? |
|---|---|---|---|---|---|---|
| 1192316e | multi-session | an hour and a half | IDK ("about an hour to get ready... no commute duration") | "90 minutes" (60 get ready + 30-minute commute memory, rank 3, arithmetic is sound) | yes | right (real-fix) |
| a2f3aa27 | knowledge-update | 1300 | IDK (mentions 1300) | IDK | no | - |
| 9aaed6a3 | multi-session | $0.75 | IDK | IDK | no | - |
| 1a1907b4 | preference | rubric | IDK | IDK | no | - |
| 1da05512 | preference | rubric | IDK | IDK | no | - |
| 7024f17c | multi-session | 0.5 hours | IDK | IDK | no | - |
| gpt4_372c3eed | multi-session | 10 years | IDK | IDK | no | - |
| f9e8c073 | knowledge-update | five | IDK | IDK (correct: five is not in context) | no | - |
| 982b5123_abs | abstention | not enough info | IDK | IDK (hedged) | no | - |
| 2698e78f_abs | abstention | not enough info | IDK | IDK | no | - |

| quantity | value |
|---|---:|
| IDK re-asked / adopted | 10 / 1 |
| rescues right / wrong | 1 / 0 |
| abstention rows broken (type == "abstention") | 0 of 2 (both stayed IDK, strict+lenient 1 before and after) |
| non-IDK answers changed | 0 by construction |
| latency | mean 21.3 s vs 12.2 s (re-ask on half the rows) |
| quotes + cascade (P7h-p20) | rescued 9aaed6a3 ($0.75) and 1192316e (both right); but the re-ask on 778164c6 returned leaked reasoning ("I should answer that there wasn't a Jamaican snapper dish...") |
| LoCoMo P6-L for context | -1.5 strict / +3.0 lenient, noise |

The cascade is conservative: the 9B model drops IDK on 1 of 10 and is right. The cost is that it cannot fix refusals where the model
believes the data is missing (preference rows, 7024f17c).

## 5. P1 (auto_v1 alone), for completeness

| id | flip | class | note |
|---|---|---|---|
| 1192316e | S+ L+ | real-fix | 90 minutes |
| c9f37c46 | substr+, v2+ (strict v1 unchanged) | real-fix | "About 2 months" (3 months before 05-27 to April open mic) |
| 29f2956b | S+ | judge-noise | "30 minutes" vs "30 minutes daily" |
| a2f3aa27 | S- L- | judge-noise | IDK-with-1300 vs bare IDK |
| a82c026e | L- substr- | real-regression | plain lookup answer replaced by bare "I don't know" |
| 9aaed6a3, f9e8c073 | L- | judge-noise | verbose wrong IDK lost lenient credit |
| cc6d1ec1 | L- | judge-noise | "About three months" vs "three months", both wrong |

Net: 2 real fixes, 1 real regression, 4-5 noise. Lenient -20 is almost entirely the loss of IDK credit (mean answer length falls from 110 to 30 chars).
True correctness 9 vs 8. All real fixes are on "how long / how many / total" questions; the regression is a plain "which game" lookup.

## 6. Verdicts

| variant | verdict | one sentence |
|---|---|---|
| P6-p20 cascade | needs-more-data (leaning confirm) | One real fix (1192316e), zero regressions, zero abstention breaks, and the design cannot touch non-IDK answers; +5 is one question, and LoCoMo P6-L was noise, so confirm on 50. |
| P7g-p20 quotes k20/7000 | reject (as rendered) | Zero real fixes and one real regression (778164c6) at 3.2x the prompt; k20 adds nothing and diluted the one fix P7b got. |
| P7b-p20 quotes kl10 | needs-more-data | One real fix (9aaed6a3) and one real regression (778164c6) cancel at n=20; the regression mechanism (clipped, repeated assistant quotes) is specific and removable, so test a speaker-filtered variant before dropping quotes. |
| P1-p20 auto_v1 | judge-artifact | The strict +5 is judge noise plus a wash (2 real fixes, 1 real regression) and the lenient -20 is lost IDK credit; as a sole prompt it is unsafe, but its fixes are exactly what the cascade harvests. |

## 7. Recommendation for the full-50 run

Report on every run: strict_v2, strict, lenient, and "true-ish" correctness excluding IDK-credit (lenient restricted to non-IDK answers),
plus mean prompt chars as sent (not `ctx_chars`). Pair all against R0.

| # | config | why the data supports it | needs code? |
|---|---|---|---|
| R0 | concise, plain, vk15 (baseline, same 50) | pairing anchor; REF shows strict 54 / lenient 80 on the published run | no |
| R1 | R0 + `--cascade auto_v1` (P6) | only variant with a real fix and no regression; safe by construction; roughly 1.7x latency | no |
| R2 | `--render quotes` restricted to user-turn quotes, exact-duplicate quotes dropped, 1 quote per memory, quote_chars 300, k15 (about 4.0k chars, 1.7x plain; kl10 variant about 2.9k as fallback) | removes the failing ingredients: assistant quotes (41%, 99% clipped, source of the 778164c6 and 0f05491a misleads) and 51% duplicates; keeps the user-turn evidence behind 9aaed6a3 and LoCoMo's 8 fixes | yes: speaker filter + dedupe + max-quotes flag in `render_memories` (about 10 lines) |
| R3 | R2 + `--cascade auto_v1` | P7h showed the cascade composes with quotes (2 correct rescues); expected to recover 9aaed6a3 (P7b) and 1192316e together | same as R2 |
| R4 | R1 + regex-routed compute: questions matching `how long|how many|how much|total|difference|between|days|weeks|months|years` go straight to auto_v1 (question text only, no type) | on the p20 rows matched by that regex, P1 gives 2 fixes (1192316e, c9f37c46) and 0 regressions; the only P1 regression (a82c026e, "What game...") is not matched | yes: small route like `--route-pref`, regex on question |
| R5 | R1 + `--render saidon` (weekday on dates) | baseline 9aaed6a3 mislabels 2023-05-18 as a Saturday; saidon prints "Thu" next to the date. LoCoMo P3-L was judge-artifact with 0 real flips, so low risk | no |

Notes on the hybrids you proposed:

| idea | assessment |
|---|---|
| quotes only for non-assistant sources | supported (R2). Strongest hybrid; every quote-caused failure here traces to assistant quotes (778164c6 clip, 0f05491a 400-star turn) or to repeated turns. |
| cascade + calendar | weak. `--calendar` would print "last Thursday = 2023-05-25" for 9aaed6a3 (question date Mon 05-29) while the gold depends on the session-relative "last Thursday" = 05-18, so it likely misleads that row; LoCoMo P4-L was noise. Prefer R5 (saidon) if a date aid is wanted; do not spend a slot on calendar. |
| quotes with a smaller quote length | not supported by this data. The clip (300 of an assistant list) was the problem, not the length; shorter quotes would lose the user detail too. LoCoMo qc600 was not better than qc300 either. Drop speaker and duplicates first. |
| k20 / 7000 | drop. No memories lost at k15 vs k20 (superset) and the 7000 cap never binds; k20 only raised cost and diluted. |
| quotes in preference/abstention rows | abstention rows (2/2) held under every variant, but P7g/P7b added explanation text; preference rows stayed IDK in every variant (rubric golds need a different prompt, see `pref_v1`, LoCoMo P8-L was noise). Do not expect either feature to touch them. |

Unresolved: at n=20 I cannot separate R2's expected gain from zero. The run on 50 is the first meaningful test; treat R2 vs R1 as the key contrast and R3 as the candidate default if both hold.

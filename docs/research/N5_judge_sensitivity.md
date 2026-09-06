# N5: the judge changes the ranking of systems

**Verdict: CONFIRMED for two of three pairs.** The retrieval gain (flat -> local_pairs) survives every judge; the H8 answer-model result flips sign; the H5 open-domain gain vanishes under a strict judge. Absolute accuracy moves 40-55 pts across judges for identical answer files.

## Setup
Existing answers re-scored, no new generation. J1 = runner ACCURACY_PROMPT verbatim + runner's exact/substring auto-pass, judge qwen3.6-35b-a3b (reasoning off, T=0). J2 = strict prompt (same specific fact; lists need every gold item; topical overlap and abstentions WRONG), exact-match auto-pass only, same Qwen. J3 = all gold parts (split on "," / " and ", >2 chars) as substrings, no LLM. 1,283 sequential judge calls, 9.5 min. Files: `n5_rescore.py`, `n5_judge_cache.json`, `n5_rescored.json`, `n5_analyze*.py`.

## Accuracy (%) under four judges
| file | n | Gemma-lenient | J1 Qwen-lenient | J2 Qwen-strict | J3 substring |
|---|---|---|---|---|---|
| flat_single_hop | 282 | 64.9 | 51.4 | 21.6 | 13.5 |
| local_pairs_single_hop | 282 | 73.8 | 62.8 | 31.2 | 18.1 |
| H8_control (gemma answers) | 100 | 78.0 | 64.0 | 38.0 | 24.0 |
| H8_treatment (qwen answers) | 100 | 71.0 | 65.0 | 42.0 | 29.0 |
| H5_control | 96 | 42.7 | 27.1 | 11.5 | 6.2 |
| H5_T2_full | 96 | 57.3 | 39.6 | 10.4 | 9.4 |

Swapping only the judge model (same prompt, same auto-pass) costs 11-18 pts. The strict prompt costs another 25-35, almost all on list golds: 66% of single_hop golds are multi-item; on those Gemma scores 76-88% vs J2 22-48%.

## Pair deltas (pts; wins/losses; two-sided sign test)
| pair | Gemma-lenient | J1 | J2 | J3 | verdict |
|---|---|---|---|---|---|
| flat -> local_pairs | +8.9 (32/7, p<.001) | +11.3 (45/13, p<.001) | +9.6 (38/11, p<.001) | +4.6 (18/5, p=.01) | stable, WORKS under all |
| H8 gemma -> qwen answers | **-7.0** (5/12, p=.14) | +1.0 (11/10) | +4.0 (16/12) | +5.0 (9/4) | **sign flips**: DOES NOT WORK -> null |
| H5 control -> T2 | **+14.6** (17/3, p=.003) | +12.5 (14/2, p=.004) | **-1.0** (4/5) | +3.1 (3/0) | **flips under strict**: WORKS -> null |

H8: Gemma credited 14 of its own answers that Qwen-lenient rejects (`home country` for Sweden) vs 7 of Qwen's; removing the same-model confound alone erases the -7. H5: of 17 Gemma wins, J1 keeps 10, J2 keeps 3 (Jill = John's partner, C. S. Lewis, Colombia); the rest are longer hedged answers only a lenient judge credits.

## Agreement with the campaign judge (pooled, 1,180 rows)
- Gemma vs J1: 86.5% agreement, kappa 0.72 (Gemma-only correct 125, J1-only 4).
- Gemma vs J2: **59.4%, kappa 0.30** (Gemma-only 387, J2-only 1: strict is nested inside lenient).
- Gemma vs J3: 49.1%, kappa 0.17. J1 vs J2: 72.3%, kappa 0.47.
- Length bias: Gemma-correct answers average 53 chars vs 21 for wrong; J2 shows none (41 vs 43).

## Five disagreements
1. Gold Sweden, gen `home country`: Gemma CORRECT; J1/J2/J3 WRONG.
2. Gold Obesity, gen `No. The memories do not mention any of John's health problems.`: Gemma CORRECT (counted as an H5 win); all others WRONG.
3. Gold "Oliver, Luna, Bailey", gen `Luna, Oliver`: Gemma/J1 CORRECT; J2/J3 WRONG (missing item).
4. Gold 2 (beach trips), gen `week of 14 August 2023`: Gemma/J1 CORRECT, J3 also ("2" is inside "2023"); J2 WRONG. Lenient prompt and naive substring both fail.
5. Gold "Yes, since she collects classic children's books", gen `Yes, she has lots of kids' books including classics.`: Gemma/J1 CORRECT, J2 WRONG. J2 is over-strict on reason-bearing golds.

## What this means for reporting LoCoMo numbers
The same answers score 13.5, 21.6, 51.4 or 64.9% depending only on the grader: a 51-pt spread, as large as the published leaderboard spread (58-96%) across supposedly different systems. Judge model alone is worth ~12 pts, prompt leniency ~30, and the lenient prompt with a same-family judge rewards exactly what prompt-engineering treatments (H5; Gemma vs Qwen in H8) produce: longer, hedged, partial-list answers. Only retrieval gains held under every grader. Report every number with the judge model and prompt named, publish strict and substring scores beside the lenient one, use a judge from a different family than the answerer, treat cross-paper comparisons with different judges as uninterpretable, and check that a ranking's sign survives a lenient and a strict judge before claiming it.

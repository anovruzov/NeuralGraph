# Judge audit: new shipped config vs old shipped config, LoCoMo conv 1 (38 q, sessions 1-8)

Read-only audit, no model calls. S = strict, S2 = strict_v2 (Gemma-4-e4b), Lni = lenient_nonidk. Files in `.context/overnight/results/`.
- BASE: `locomo_c1_crateq1_concise_cascauto_v1n_acc.json` (ctx `locomo_c1_crateq1_ctx.json`, memories + 1 quote, 3.6k chars, concise + IDK cascade).
- VARIANT: `locomo_c1_default_auto_v1n_think_acc.json` (ctx `locomo_c1_default_ctx.json`, 6k chars, quotes on ~13 of 15 memories, "Raw messages" block in 32/38 contexts; 9B thinking, `auto_v1n`, max_tokens 2500).
- Extra arm used only as read-only evidence: `locomo_c1_default_concise_cascauto_v1n_acc.json` (variant ctx + OLD reader), separates context from reader.
- Same 38 ids in the same order, no unpaired rows. Retrieved memory ids are identical in 35/38 rows (q4, q13, q24 gain one memory each in the variant).
- Limitation: variant `raw` holds only the post-thinking visible output (thinking text is not stored), so "thinking error" is inferred from the visible output and the ablation arm.

## 1. Recompute (`compare.py BASE VARIANT --metric strict,strict_v2,lenient_nonidk`)

| metric | base | variant | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict | 57.9 | 50.0 | -7.9 | 2 | 5 | 0.453 | [-21.1, +5.3] |
| strict_v2 | 63.2 | 57.9 | -5.3 | 3 | 5 | 0.727 | [-18.4, +7.9] |
| lenient_nonidk | 76.3 | 73.7 | -2.6 | 2 | 3 | 1.000 | [-13.2, +7.9] |

Flip sets: S fixed q29 q30 / broke q5 q9 q12 q22 q34. S2 fixed q2 q13 q32 / broke q5 q12 q27 q33 q34. Lni fixed q13 q21 / broke q5 q12 q34.
Other: lenient 86.8 -> 73.7 only because base got credit for IDK answers (5 IDK base vs 4 variant); Lni removes that. Mean answer time 2.9 s -> 9.1 s, mean ctx 888 -> 1400 tokens, mean answer length 120 -> 69 chars. Cascade never fired in base (0/38).
By type (S / S2): multi-hop (n=4) 50/50 -> 25/25; single-hop (n=23) 52.2/60.9 -> 52.2/56.5; temporal (n=11) 72.7/72.7 -> 54.5/72.7.
No delta is significant (all p > 0.45); the whole difference is 13 flipped questions out of 38.

## 2. Audit of every flip (13 distinct questions)

Class key: RF real-fix, RR real-regression, JN judge-noise, FO format-only. Metrics column = S/S2/Lni verdict base -> variant (1 pass, 0 fail).

| q | type | gold | base answer | variant answer | S / S2 / Lni | class | note |
|---|---|---|---|---|---|---|---|
| q5 | multi | By the water, with natural light and Marley flooring | downtown, natural light, view by the water, Marley, bounce | downtown, bounce, Marley, space for students | 1/1/1 -> 0/0/0 | RR | dropped water + natural light |
| q12 | temp | 16 March, 2023 | March 16, 2023 | January 2023 | 1/1/1 -> 0/0/0 | RR | wrong date |
| q34 | single | dancing together and supporting each other | "partner to dance with, same challenges" | I don't know | 1/1/1 -> 0/0/0 | RR | false IDK, think_truncated |
| q9 | temp | February, 2023 | February 8, 2023 | 2023-02-08 | 1/1/1 -> 0/1/1 | FO | same date, ISO string rejected by strict only |
| q22 | single | They look graceful | IDK + explanation | I don't know | 1/0/0 -> 0/0/0 | JN | both IDK, base strict was a false pass (S2 and Lni already 0) |
| q27 | single | cozy and comfortable | cool oasis | cool oasis | 0/1/1 -> 0/0/1 | JN | identical content ("were"/"they're") |
| q33 | single | Freedom and expressing herself through dance | freedom, follow passions, express herself | Freedom, follow passions, express herself | 1/1/1 -> 1/0/1 | JN | identical content, S2 flipped only |
| q2 | single | by dancing | Jon: breaks + dancing; Gina: clothing + dance | Taking breaks and dancing it out. | 0/0/1 -> 0/1/1 | FO | shorter answer accepted by S2; content equally partial |
| q29 | single | personal style and customer comfort | style, cozy, glam chandelier, come back | own style, customers feel cozy | 0/1/1 -> 1/1/1 | FO | conciseness only; base already right (S2 passed) |
| q30 | single | key to making them feel welcome and coming back | Jon advises build relationships (wrong) | quotes the exact Jon line | 0/1/1 -> 1/1/1 | RF | raw turn (Jon 2023-02-01) supplied it; base S2 pass was noise |
| q32 | single | The store is doing great. | wild ride, ups and downs (negative) | so far so good, customers love offers, more sales | 0/0/1 -> 0/1/1 | RF | positive sentiment now correct; still verbose for S |
| q21 | single | They are performing at the festival | IDK (wrongly) | dance crew performing at nearby festival | 0/0/0 -> 0/0/1 | RF | raw Jon turn 2023-01-20 holds "perform at a nearby festival" |
| q13 | temp | April, 2023 | May 2023 (from event tag [2023-05]) | "As of 2023-04-03 ... (no specific start date provided)" | 0/0/0 -> 0/1/1 | RF (weak) | avoids wrong May, month right, hedged; S still rejects |

| class | count | questions | net on S2 |
|---|---:|---|---:|
| real-fix | 4 (1 weak) | q30 q32 q21 q13 | +3 (q13 and q32 count for S2, q30 for S, q21 for Lni) |
| real-regression | 3 | q5 q12 q34 | -3 |
| judge-noise | 3 | q22 q27 q33 | -2 (q27 and q33 S2) |
| format-only | 3 | q9 (against), q2 and q29 (for) | +1 (q2 S2) |

Real flips are +4 / -3: accuracy on this conversation is roughly neutral. The -7.9 strict headline is 3 real regressions, 1 strict-format casualty (q9), 1 base false pass lost (q22), minus 2 real fixes and 1 format gain (q29).

## 2b. Mechanism of each real regression

Context vs reader separated with the ablation arm (variant ctx + old concise reader): base -> that arm is S +0.0 (4:4), S2 +2.6, Lni +2.6; that arm -> variant is S -7.9 (1:4), S2 -7.9 (3:6), Lni -5.3 (0:2).

| q | needed memory in both ctx? | mechanism | evidence |
|---|---|---|---|
| q12 | yes: "(Gina, 2023-03-16) My online clothes store is open!" is in both | distraction by extra quote | broke already in the concise-on-6k arm ("January 2023"). The 6k ctx attached a Gina 2023-01-29 ad-campaign quote to the "[2023-01] ... started the business in January 2023" memory, which base had without a quote. Reader is not the cause |
| q34 | yes: Jon 2023-03-16 "...like having a partner to dance with" is in both | empty/truncated thinking + distraction | `think_truncated=true`, `think_fallback=true` (the only truncated row in 38); the fallback emitted IDK after listing "no metaphor". Concise reader on the same ctx answered "a rollercoaster" (wrong, quote from another line), so the 6k ctx also distracted; base found the right quote with a 15-line ctx |
| q5 | yes: natural-light/by-the-water memory, Marley memory, downtown memory in both (variant also adds a rehearsal [task] memory) | thinking error, Answer-line compression | variant reasoned from the 2023-02-04 combined "downtown + bounce + Marley" memory and omitted the water/natural-light memories. The concise reader on the same ctx listed all of them (S=1). Rule 5 "result includes every item" was not followed |

Hidden thinking-vs-concise losses that were masked because base also failed them:

| q | base | variant ctx + concise | variant | mechanism |
|---|---|---|---|---|
| q4 | 0 | 1 ("...especially after losing his job as a banker") | 0 (drops the job-loss clause) | Answer-line compression to one reason, new memory (mem_79a6) not used |
| q35 | 0 (Lni 1) | 1 (build relationships, stay positive) | 0 (advice "keep going, believe in yourself", from a different memory) | thinking error / distraction by Gina 2023-02-04 quote |
| q23 | 1 | 0 (S only) | 1 | gain for thinking reader, S-only |

So thinking vs concise on the 6k ctx: S 1:4, S2 3:6, Lni 0:2. This is where the conversation-specific loss comes from; the 6k ctx alone is neutral.

## 3. LoCoMo-specific traits

| trait | count | effect |
|---|---|---|
| Prompt actually used | `auto_v1n.txt` | it has NO weekday-date rule (that is `auto_v3.txt` rule 5); rule 5 in `auto_v1n` is "Answer: <result> with its unit or noun, every item asked, no hedging". Weekday-date hurt: 0 cases |
| `Answer:` line present | 38/38 rows | mean answer 120 -> 69 chars. Good for LoCoMo's short golds (q29, q2 gained), bad when the gold is a conjunction (q5, q4) |
| Bare ISO date answers | 3/11 temporal (q0, q8, q9) | q0, q8 still pass; q9 fails S only (gold "February, 2023") ; S2 and Lni pass all three. 1 strict casualty, 0 real |
| Bare "Month YYYY" answers | q1, q6, q14 (pass), q7 (fail both arms) | of the 11 temporal golds 6 are month-only, 4 are day-month ("16 March, 2023" style: q0 q7 q8 q12), 1 is "A few years ago"; the day-month ones are where the variant failed (q12 wrong date, q7 month only in both arms) |
| Explanatory-sentence base vs terse variant | q2, q29 helped S2/S; q32 still fails S | strict judge penalises extra clauses on both sides |
| IDK | base 5, variant 4 (cascade fired 0 times in base) | IDK-rule 4 in `auto_v1n` converted q21 (good), but q34 (bad) is an IDK from truncation |
| Two human speakers | "Raw messages that match the question" mixes Jon and Gina turns | helped: q30, q21 (the answer line was in a raw turn); hurt: q22 raw turn is a Jon line about a different photo, q34 raw turns about "journey" are generic |
| Judge false pass | q22 base IDK scored strict=1 against "They look graceful" | S judge noise worth -1 for the base, i.e. the baseline S is inflated by 2.6 |

## 4. Verdict for this conversation

**Verdict: noise-dominated, not a confirmed regression.** Real flips are 4 fixes vs 3 regressions (net +1), the delta is not significant on any metric (p .45 to 1.0), and the S loss is explained by one strict-format miss (q9), one lost base false pass (q22) and judge noise; but there is no gain and latency is 3.2x, so the change is not supported on LoCoMo either.
Pooled with conv 0 (strict +6.1) the LoCoMo effect remains ~neutral (about 14:13).

Concrete recommendation (one): keep the 6k + raw3 context (it is neutral here and wins on LME) but route the thinking reader only to questions matching count/duration/date-difference/total patterns (`how many|how long|how much|how many days|difference|total|ago|between`); everything else, which is nearly every LoCoMo question (what/when/why lookups), goes to the concise + IDK cascade reader. This directly removes the thinking-vs-concise losses seen here (q5, q4, q35, q34 truncation, q9 ISO format) while leaving the compute questions that drove the LME gain (+20 S2) on the thinking path.
Also set `max_tokens` on the thinking reader to avoid the q34 truncation (fallback produced a false IDK).

Optional guard if routing is not wanted: for lookup questions, keep thinking but drop the "Answer:" line instruction and the ISO date habit (ask for dates as written in the gold style, "<day> <Month>, <year>").

## 5. Ledger note (replace the c1 row's tail)

`| R3-WINNER-locomo-c1 | S1 | LoCoMo c1 (38 q) | new shipped config vs old shipped config on a SECOND LoCoMo conversation | locomo_c1 / default (6000+raw3) / auto_v1n think | 50.0 | — | — | — | -7.9 | — | 0.45 | noise-dominated (judge audit) | strict 57.9->50.0 (2:5), S2 63.2->57.9 (3:5), Lni 76.3->73.7 (2:3); flips audited: real-fix 4 (q30 q32 q21 q13), real-regression 3 (q5 q12 q34), judge-noise 3, format-only 3 (q9 ISO date); ctx alone neutral (0/+2.6/+2.6), thinking on 6k ctx S 1:4 S2 3:6; latency 2.9->9.1 s; route thinking to compute-type questions only. |`

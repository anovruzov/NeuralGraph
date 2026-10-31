# R1: failure analysis of the LME Oracle 50 answerer (strict 54 / lenient 80) and a ranked reader/retrieval plan

Sources: `ankara/.context/exp/acc_lme.json` (9B concise, 27/50 strict), `acc_lme_compute.json` (9B reason-then-answer, 28/50),
`acc_lme_35b.json` (35B concise, 24/50), `lme_oracle_assistant_v2.json` (retrieval rows: the DB behind acc_lme is the v2 index;
`context_preview` is cut at 400 chars and the DBs in /tmp are gone, so full context text is NOT inspectable: statements about
"what was in the context" below are inferred from the preview, `n_ctx`, `ctx_chars`, the other variants, and the has_answer turns
in `longmemeval_oracle.json`). Everything below was computed with python3 stdlib. 1 question = 2 points; treat +-1 q as noise.

## 0. Headline facts the data gives us

| fact | number | why it matters |
|---|---|---|
| strict misses (concise) | 23 of 50 | all listed in section 1 |
| misses whose evidence was NOT retrieved (`evidence_retrieved=False`) | 3 (41275add, 09ba9854, 7161e7e2) | only these 3 are pure retrieval/extraction failures by the flag |
| misses that are really "second half of the evidence missing" although the flag is True | 4 more (372c, 2ebe, 08f4, 0f05) | the flag is any-of over has_answer turns; multi-evidence questions need all |
| rows that hit the 3600-char context cap (`ctx_chars` >= 3250) | 3 of 50: 1192316e, gpt4_d6585ce9, 08f4fc43 | all 3 are misses; they dropped 8, 31 and 6 memories (n_ctx 14/12/12 of 22/43/18 extracted) |
| rows with n_ctx < 15 for any other reason | 16 | there simply were < 15 memories in that DB; min-relative-score / min-vector-sim thresholds never bound. Threshold tuning is a dead end on this sample |
| "I don't know" answers (concise / compute / 35B) | 19 / 11 / 14 (4 are correct abstentions) | IDK on answerable questions: 15 / 7 / 10 |
| lenient passed an IDK on an answerable question (concise) | 7: a2f3aa27, 9aaed6a3, 1a1907b4, 1da05512, 7024f17c, 09ba9854, 1c0ddc50 | true lenient is ~33/50 = 66 if those are not credited, not 80 |
| best-of(concise, compute) per question | 35/50 = 70 strict | an oracle selector between these two prompts; the cap for prompt-routing ideas |
| reader-side ceiling | 43/50 = 86 | 23 misses minus the 7 "fact-not-in-context" and 3 preference rows the strict judge cannot credit is ~ 13 reachable, so the practical reader-only ceiling is ~ 40 (80) |
| compute variant vs concise | fixes 8, breaks 7 (net +1), 17.2 s vs 4.6 s per answer | the prompt helps arithmetic but 5 of its 7 regressions are FORMAT (see 2b) |
| 35B vs 9B | -3 q strict | a bigger model is not the lever; 35B is worse on temporal (3/11 vs 4/11 vs compute 6/11) |

Strict by type (concise / compute / 35B): temporal 4/11, 6/11, 3/11; multi-session 5/12, 6/12, 5/12; knowledge-update 4/7, 5/7, 4/7;
single-user 6/7, 5/7, 5/7; assistant 4/6, 2/6, 3/6; preference 0/3 in all (strict judge vs rubric gold, see section 4); abstention 4/4 in all.

## 1. Every strict miss in acc_lme.json (23)

Ev = `evidence_retrieved`. "Wrong" answers abbreviated. Rank = position of the first has_answer-citing memory in the 15-line context.
Type abbreviations: TR temporal-reasoning, MS multi-session, KU knowledge-update, SU single-session-user, SA single-session-assistant, SP preference.

| id | type | gold | concise answer (9B) | Ev | cause | one-line reason (what the data shows) |
|---|---|---|---|---|---|---|
| cc6d1ec1 | TR | Two months | "about three months" | T | arithmetic/date-math | ctx: started "about three months" ago (told 05-25), workshop "one month ago"; needed 3-1=2; model reported the first number |
| c9f37c46 | TR | 2 months | "about 3 months before ... April 2023" | T | arithmetic/date-math | same pattern: started 3 months ago (05-27), open mic "last month"; needs 3-1 |
| 9aaed6a3 | MS | $0.75 | IDK ("05-18 was a Tuesday, not Thursday") | T | arithmetic/date-math | needs $75 x 1% (two memories) and treats the 05-18 trip as "last Thursday"; model hallucinated the weekday (2023-05-18 IS a Thursday) |
| 1192316e | MS | an hour and a half | "an hour to get ready, commute is 30 minutes" | T | arithmetic/date-math | both facts present, never summed (ctx hit the 3600 cap: 14 of 22 memories) |
| 0f05491a | KU | 120 | "125 stars" | T | fact-not-in-context | all 3 variants say 125 (stale, told 07-11); the correction "120, not 300" (07-30) was probably never extracted/ranked in. Verify by dumping the DB |
| 29f2956b | SU | 30 minutes | "You practice guitar for 30 minutes daily." | T | judge-false-negative | exact match; compute ("30 minutes") passes, 35B same sentence fails |
| db467c8c | KU | nine months | "...for nine months, having overstayed their visa by that duration" | T | judge-false-negative | correct value, extra clause; compute and 35B pass |
| a2f3aa27 | KU | 1300 | IDK ("no updated count for today") | T | IDK-despite-evidence | memory says "approximately 1300"; model wanted a newer count. Compute/35B answer "approximately 1300" and then FAIL strict on the hedge word |
| 7024f17c | MS | 0.5 hours | IDK ("only a 30-minute jog, no yoga") | T | IDK-despite-evidence | 30 min -> 0.5 h conversion; model refuses because yoga is missing; compute says "0 hours" (also wrong) |
| gpt4_7f6b06db | TR | Muir Woods, then Big Sur/Monterey, then Yosemite | IDK ("cannot order exactly three") | T | IDK-despite-evidence | 3 dated events (03-10, 04-20, 05-15) are in ctx; model saw them and refused; compute lists the right order |
| aae3761f | MS | 15 hours | IDK ("only two trips: 4h, 5h") | T | IDK-despite-evidence | compute answers 15 with the same ctx, so the 6 h D.C. memory IS present; concise missed it |
| 1a1907b4 | SP | rubric (mixology-class cocktails, Pimm's Cup) | IDK ("no specific cocktail suggestions") | T | IDK-despite-evidence | only 4 memories in the DB; mixology class is among them; gold is a rubric, so strict can never be satisfied by a plain suggestion |
| 1da05512 | SP | rubric (storage capacity, external drives) | IDK ("no advice on whether to buy now or wait") | T | IDK-despite-evidence | 21 memories incl. NAS-for-central-backup plan; model will not produce an opinion |
| 1c0ddc50 | SP | rubric (new podcasts/audiobooks beyond true crime) | IDK ("no commute details") | T | IDK-despite-evidence | podcast preferences are in ctx; recommendation questions trigger IDK |
| gpt4_372c3eed | MS | 10 years | IDK ("duration of UCLA not specified") | T | fact-not-in-context | the "Bachelor's from UCLA in 2020, took four years" turn is not visible as a memory (answer says duration unknown); needs 2010->2020; compute got 8 |
| 2ebe6c90 | TR | 21 days | IDK ("only finish date 2023-01-31") | T | fact-not-in-context | start ("just started today", 01-10) memory absent; all 3 variants IDK |
| 08f4fc43 | TR | 30 days | IDK ("no Sunday mass at St. Mary's") | T | fact-not-in-context | the Jan 2 mass memory absent; ctx hit the char cap (12 of 18 memories kept, 3432 chars) |
| 09ba9854 | MS | $50 | IDK ("no taxi cost") | F | fact-not-in-context | has_answer turns ($10 train, $60 taxi) not cited by any of the 8 memories; extraction miss |
| 41275add | SA | Mayo Clinic video title + link | IDK | F | fact-not-in-context | the DB has 0 memories for this history; assistant-turn fact never extracted |
| 7161e7e2 | SA | Admon: 8am-4pm Day Shift on Sunday | IDK ("no day-to-day assignment") | F | fact-not-in-context | the shift table lives in an assistant turn; only 4 memories extracted, none with the table |
| 0bc8ad93 | TR | No, not with a friend | "Yes ... Science Museum with a chemistry-professor friend" | T | picked-wrong-memory | rank 6 of 62 memories; gold event is a History Museum lecture 2023-01-11 (two months before 03-11); model grabbed a 2022 museum + friend memory. Compute answers "No" |
| gpt4_d6585ce9 | TR | my parents | IDK (picked a Brooklyn festival 04-01 with friends) | T | picked-wrong-memory | rank 7 of 43; correct event is Sat 04-15 (last Saturday before 04-22) "Queen with my parents"; ctx hit the char cap (12 of 43 kept) |
| 0a995998 | MS | 3 | "navy blazer and a pair of boots from Zara" | T | partial-list | three pick-up/return items (blazer, boots return, boots pick-up of the new pair); model lists 2; compute says 3 |

## 2. Counts per cause and which answerer variants get them right

### 2a. By cause (concise-correct is 0 by construction; cells = how many of the group the variant gets strict-right)

| cause | n | ids | compute right | 35B right | moves with prompt? |
|---|---:|---|---:|---:|---|
| arithmetic/date-math | 4 | cc6d1ec1, c9f37c46, 9aaed6a3, 1192316e | 2 (cc6d, 1192) | 1 (1192) | yes, partially; c9f3 and 9aae fail everywhere |
| IDK-despite-evidence | 7 | a2f3aa27, 7024f17c, 7f6b06db, aae3761f, 1a19, 1da0, 1c0d | 2 (7f6b, aae3) | 0 | yes for list/count questions; no for preference rows (strict judge) |
| fact-not-in-context | 7 | 0f05491a, 372c, 2ebe, 08f4, 09ba, 41275add, 7161e7e2 | 0 | 0 | no: reader cannot fix. 3 are extraction (09ba, 4127, 7161), 4 need retrieval breadth/decomposition |
| judge-false-negative | 2 | 29f2956b, db467c8c | 2 | 1 (db46) | these are judge noise; flips between variants are not signal |
| picked-wrong-memory | 2 | 0bc8ad93, gpt4_d6585ce9 | 1 (0bc8) | 0 | partly |
| partial-list | 1 | 0a995998 | 1 | 0 | yes |
| format-mismatch | 0 | (a2f3aa27 becomes one under compute/35B: "approximately 1300") | | | |
| other | 0 | | | | |
| total | 23 | | 8 | 2 | |

### 2b. What compute breaks (7 rows concise got right and compute loses): 5 of 7 are answer-format, not reasoning

| id | gold | concise (right) | compute (wrong) | real cause |
|---|---|---|---|---|
| gpt4_6dc9b45b | 4 months ago | "...4 months ago (on June 1, 2021)" | "4" | unit dropped by terse `Answer:` line |
| c19f7a0b | 6:30 pm | "...around 6:30 pm" | "Around 6:30 pm" | judge reads "Around" as hedge (concise got lucky) |
| a82c026e | Dark Souls 3 DLC | "...Dark Souls 3 DLC" | "Dark Souls 3" | dropped part of the entity |
| 65240037 | ratio 1:10, one part tea tree oil ... | full sentence | "1:10" | gold includes explanation; terse loses part |
| 778164c6 | Grilled Snapper with Mango Salsa | right | "15. Since the question asks for a ..." | parser bug: `Answer:\s*(.+)` takes one line; when there is no `Answer:` line the fallback takes the last line of reasoning |
| e56a43b9 | $5 | right ($5 discount) | IDK | compute over-reasoned itself into IDK |
| gpt4_2ba83207 | Thrive Market | right | "Walmart" | reasoning error |

Reading: a prompt that computes only when needed and ends in a well-formed `Answer:` line recovers about 5 of these 7 while keeping compute's 8 wins.
The 35B concise loses 5 concise wins (a82c, 778164, ce6d2d27, 4dfccbf7, gpt4_2ba83207) and gains 2.
Cascade check (computed): concise, and if the answer is an IDK on a non-abstention question rerun with compute = 29/50 (+2, zero regressions).

## 3. Ranked interventions (cheap to test; all use existing harness features unless marked HARNESS/RUST)

Harness = `research/overnight/scripts/answer_eval.py`. Prompt files go in `research/overnight/prompts/*.txt`, first line `#mode: auto|compute|concise`,
placeholders only `{date_line} {memories} {question}` (no other braces: the file goes through `str.format`). The parser takes ONE line after the last `Answer:`,
so every prompt must demand a single-line answer. Context build knobs: `chat-memory-context ... --k N --max-chars N --with-sources --retrieval "context_min_vector_sim=0.2,..."`;
build once at k=30 / max-chars 9000 and slice with `--k-limit`.
Baseline = 27/50. Gains are in questions (x2 = points), honest ranges, and NOT additive.

| # | intervention | expected gain | risk |
|---|---|---|---|
| 1 | AUTO prompt: compute when needed, terse faithful final line | +3 to +5 q (net 30-32) | medium |
| 2 | IDK-rescue cascade (concise first; on IDK rerun with compute) | +2 q, floor, no regressions | low |
| 3 | Raise context cap: `context_max_chars` 3600 to 7000, `k` 15 to 20 | +1 q (0 to 2) | low |
| 4 | Render "said on" date, weekday, drop chat ids | +1 to +2 q | low-med |
| 5 | Calendar scaffold for "today" and relative phrases | +1 q | low |
| 6 | 4 few-shot exemplars (date diff, day count, unit/sum, latest value) | +1 q on top of #1 | medium |
| 7 | Native thinking for the 9B (`--think`) on the compute prompt | +1 to +3 q | medium (time, parse) |
| 8 | Preference-question prompt (personalise, never IDK) | +0 to +1 strict, honest lenient | low |
| 9 | Source quotes under memories (`--render quotes`) | 0 to +2 q | medium (tokens x2-3) |
| 10 | Query decomposition for aggregation/duration questions (second retrieval) | +1 to +3 q | medium (code) |
| 11 | Self-check second pass on numeric answers | 0 to +1 q | low, 2x time |
| 12 | Raw-message fallback for "you said/recommended" questions | +2 q max (41275add, 7161e7e2) | HARNESS/RUST work; not tonight |

### 1. AUTO prompt (file `auto_v1.txt`)
Mechanism: concise path for lookups, short visible computation for derived answers, and an answer line shaped for the strict judge (value + unit, all parts, no hedge).
Targets the 8 compute wins and the 5 format regressions. Also carries the knowledge-update rule and a tight IDK rule (abstention rows must stay 4/4: related-but-different facts do not count).
```
#mode: auto
You are an assistant with long-term memory. The memories below were retrieved for the user's question, most relevant first.
Each line looks like: - [event date if known, kind] fact (from chat ID, DATE the user said it).
Rules:
1. Use ONLY these memories. Words like "a month ago", "now", "last week", "recently" inside a memory are relative to the DATE the user said it, not to today.
2. If the answer is stated in a memory, copy it. If it must be worked out (a duration, the difference between two dates, a total, a count of items, a unit conversion, which value is the latest), first write at most 6 short lines: list the exact numbers or dates you use, then calculate. Never use a memory's number as the answer when the question asks for a difference or a total.
3. If memories give different values for the same thing, use the one with the later DATE.
4. Say "I don't know" only if the specific thing asked about is neither stated nor derivable. A related but different fact (a 10-gallon tank when asked about a 30-gallon tank) does not count. If a memory gives the value, or the pieces to compute it, you must answer; never refuse because a more recent or extra detail is missing.
5. Finish with ONE line: "Answer: <result>". The result is exactly what was asked, with its unit or noun (for example "30 minutes", "$140", "4 months ago", "Thrive Market"), includes every item the question asks for (all list members, all parts of a total), and has no "about/around/approximately" unless the memory itself only gives an estimate. No explanation on that line.
{date_line}
{memories}

Question: {question}
```
Run: `answer_eval.py --contexts ctx15.json --prompt auto_v1 --out r_auto.json` (max_tokens defaults to 400 for auto; use `--max-tokens 600` if truncation shows).
Expected fixes: cc6d1ec1, 1192316e, 7f6b06db, aae3761f, 0a995998, 0bc8ad93 (likely), a2f3aa27 (rule 3/4 + no-hedge), maybe 9aaed6a3 (1% x $75). Keeps: 6dc9b45b, c19f7a0b, a82c026e, 65240037, 778164c6.
Will NOT fix: c9f37c46 (compute got "1 month"), 7024f17c, 372c, 2ebe, 08f4, 09ba, 4127, 7161, 0f05, SP rows. Watch: abstention 4/4, e56a43b9, gpt4_2ba83207.
Risk: the 9B talks itself into confident wrong numbers where concise said IDK; lenient will drop (compute lost 8 lenient points, 80 to 72, last time by turning lenient-credited IDKs into wrong answers). Report lenient with and without IDK credit.

### 2. IDK-rescue cascade (HARNESS: ~15 lines)
Mechanism: run concise; if `answer.lower().startswith("i don")` and the question is not an abstention candidate, rerun the AUTO prompt and keep that answer, but keep IDK if the second answer is also IDK.
Computed on existing rows with the old compute prompt: 29/50. This is the safe fallback if #1 regresses; combine as concise -> (IDK) -> auto.
Fixes: 7f6b06db, aae3761f. Abstention stays 4/4 only if AUTO still says IDK on them: check `982b5123_abs 2698e78f_abs gpt4_fe651585_abs eeda8a6d_abs`.

### 3. Context cap and k (RUST param only: `--max-chars 7000 --k 20`, also test `--k 25`, `--max-chars 9000`)
Mechanism: the 3600-char cap skips lines that do not fit and kept only 12-14 memories on exactly the 3 rows that hit it (1192316e, gpt4_d6585ce9, 08f4fc43), all misses.
Build contexts at `--k 30 --max-chars 9000` then run `--k-limit 15|20|25|30` with the same prompt to get the whole curve from ONE retrieval pass.
Expected: 08f4fc43 (needs the Jan-2 mass memory, 6 dropped), gpt4_d6585ce9 (31 dropped, parents memory is rank 7 already, so likelier fix is the reader), 1192316e (already answered right by compute).
Honest gain +1: the other 47 rows are untouched, since thresholds never bound. Risk: 9B distraction at k>20 (LoCoMo: k15 beat k10 by 3-4 pts but check for IDK flips). Leave `context_min_relative_score` and `context_min_vector_sim` alone: no row is affected. Fusion weights (V 1.0, K 0.8) have no visible lever here: in the 23 misses the evidence turn is at rank 0-1 in 18 of 20 rows with a rank, and only 0bc8ad93 (6) and gpt4_d6585ce9 (7) are deeper; these are not rank problems.

### 4. Render "said on" date and weekday (HARNESS regex; or RUST `context_with_results` format)
Mechanism: the line `(from chat answer_be73098b_2, 2023-05-25)` hides that the date is when the user SAID it, which is the anchor for "a month ago / about three months". Rewrite to
`- [2023-04-25, event] ... (said on 2023-05-25 Thu)` and add a one-line legend to the prompt (already in AUTO rule 1). Append weekday to every YYYY-MM-DD; drop the chat id (about 8 tokens a line, and a stray token for the 9B to quote).
Harness: `re.sub(r"\(from chat [^,)]*, (\d{4}-\d{2}-\d{2})\)", lambda m: f"(said on {m[1]} {dt(m[1]).strftime('%a')})", line)` and a second sub for `[YYYY-MM-DD,` to add `%a`.
Fixes: cc6d1ec1, c9f37c46, 9aaed6a3 (the weekday hallucination), 7024f17c (last week), gpt4_d6585ce9. Expected +1 to +2; do NOT use the existing `--render nodate`: it deletes the observed date too, which is exactly the signal needed.

### 5. "Today" and relative-phrase scaffold (HARNESS: replace `date_line`)
Mechanism: deterministic calendar help the 9B cannot do reliably (it called 2023-05-18 a Tuesday). For `question_date` `2023/05/29 (Mon) 13:21` emit, on one line each:
```
Today is Monday 2023-05-29.
Calendar: yesterday = 2023-05-28 (Sun); last Thursday = 2023-05-25; last week = 2023-05-22 to 2023-05-28; last month = 2023-04; 2 months ago = about 2023-03-29; 3 days ago = 2023-05-26.
```
Generate "last <weekday>" for all 7 weekdays (most recent strictly before today), `last week` (Mon-Sun), `last weekend`, N days/weeks/months ago for N=1..6. Pure `datetime`, 20 lines.
Fixes: gpt4_d6585ce9 ("last Saturday" = 04-15 matches the parents memory), 9aaed6a3 (partially), 0bc8ad93 ("two months ago" = 2023-01-11, History Museum). Risk: the model over-trusts the helper when the question's "last Thursday" is loose (9aaed6a3's gold treats the 05-18 trip as last Thursday: scaffold says 05-25, conflict). Expected +1.

### 6. Few-shot exemplars for date arithmetic and unit conversion (append to AUTO as `auto_v2.txt`, before `{date_line}`)
Use invented facts (never LME text) so there is no leakage; keep to about 350 tokens.
```
Worked examples (different facts):
Memories: "- [fact] The user started learning piano about 4 months ago. (said on 2023-09-10)" and "- [event] The user played a first recital last month. (said on 2023-09-30)". Question: How long had I been learning piano when I played my first recital?
Work: started about 2023-05 (4 months before 09-10); recital about 2023-08 (1 month before 09-30); 2023-05 to 2023-08 = 3 months.
Answer: 3 months
Memories: "- [2023-03-03, event] The user adopted a cat." and "- [2023-04-02, event] The user took the cat to the vet." Question: How many days passed between adopting the cat and the vet visit?
Work: Mar 3 to Mar 31 = 28 days, plus 2 days in April = 30.
Answer: 30 days
Memories: "- [fact] The user ran for 45 minutes on Tuesday." and "- [fact] The user did 1 hour of yoga on Thursday." Question: How many hours did I exercise?
Work: 45 minutes = 0.75 hours; 0.75 + 1 = 1.75.
Answer: 1.75 hours
Memories: "- [fact] The user has 2,000 followers. (said on 2023-03-01)" and "- [fact] The user has 2,400 followers. (said on 2023-04-10)". Question: How many followers do I have now?
Work: same fact, later DATE wins: 2,400.
Answer: 2,400
```
Plus one list/count exemplar (three trips of 4 h, 6 h, 5 h, total 15 hours; "pick up 2 items + return 1 = 3 items").
Fixes (on top of AUTO): c9f37c46, 7024f17c (30 min to 0.5 h), 9aaed6a3 maybe. Expected +1. Risk: exemplar anchoring (answers copy "3 months"/"30 days"); mitigate with unusual numbers.

### 7. Native thinking on the 9B (flags only)
`answer_eval.py --contexts ctx15.json --prompt auto_v1 --think --max-tokens 3000 --out r_think.json`. The harness drops the empty-think prefill and `reasoning_effort=none` under `--think`.
Mechanism: real chain of thought on date math and counting; the 35B concise (no thinking) did NOT help, so model size without reasoning is not the lever. Targets the 4 arithmetic rows and the count/list rows.
Expected +1 to +3 over AUTO; time 20-60 s per answer (50 q about 30-50 min with `--threads 2`). Risk: the final line may be buried; the parser handles `Answer:` and falls back to the last line. Run only after #1.

### 8. Preference-question prompt (route when the question contains suggest|recommend|any ideas|what do you think|should I|tips)
```
#mode: concise
You are an assistant with long-term memory. Below are memories about the user. The user asks for a suggestion or an opinion. Do NOT say "I don't know".
First, from the memories pick the user's relevant interests, past experiences, skills and constraints. Then give one concrete suggestion that builds on them, and state in the same answer which of their stated interests or experiences it builds on. Do not suggest things the memories show they dislike or have already outgrown (for example basic or generic options when they have taken a class on it).
{date_line}
{memories}

Question: {question}
Answer:
```
Targets 1a1907b4, 1da05512, 1c0ddc50. Under the ORIGINAL strict judge (gold is a rubric, answer needs "every fact") expected 0 to +1; under the rubric judge in section 4, +2 plausible. Importantly it removes 3 of the 7 lenient IDK credits (honest lenient goes up in meaning, not number).

### 9. Source quotes under each memory (`--with_sources` in chat-memory-context, `--render quotes --quote-chars 300` in the harness)
Mechanism: the extractor paraphrases and merges (a2f3: 1250, then "close to 1300" became "approximately 1300 followers" inside a 4-fact line; 0f05: "I actually need 125, not 400" and "120, not 300"). Quotes restore corrections and relative time phrases with their sent_at.
Fixes (if the memory is retrieved): 0f05491a only if the 120 memory is present (cites a quote with "120"), a2f3aa27, cc6d1ec1. Does not help fact-not-in-context rows. Cost: context 537 to about 1500 tokens; the 9B may latch onto the wrong quote. Run at `--k-limit 10` with quotes (same tokens as k15 plain) as well as k15.

### 10. Query decomposition for aggregation/duration questions (HARNESS or RUST, needs a small LLM call)
Mechanism: for "how many days between A and B", "how long did it take to finish X", "total ... across", ask the 9B for 2-3 sub-queries ("when did I start reading The Nightingale", "Sunday mass St. Mary's Church", "Bachelor's graduation year UCLA") and union the results (dedupe by memory_id, keep cap 20). Directly attacks the 4 "evidence flag True but second half missing" rows: 2ebe6c90, 08f4fc43, 372c, 0f05491a ("current gold level stars" and "stars needed not 300").
Expected +1 to +3, if the memories exist in the DB at all (not verified: extraction may have missed them, in which case nothing helps). Check first by dumping all memories of those 4 DBs and grepping for "started", "Sunday mass", "2020".

### 11. Self-check second pass (HARNESS)
After AUTO, feed `question`, memories and the model's own `Answer:` and ask: "Recompute the number from the memories line by line. If your answer differs, output 'Answer: <corrected>', otherwise repeat it. Also check the answer is a difference/total, not one of the inputs." Aimed at cc6d/c9f3-type slips (reported the 3 instead of 3-1). 9B at T=0 usually repeats itself, so expect 0 to +1; only run if #1 leaves arithmetic rows red. 2x time.

### 12. Raw-message fallback (NOT tonight; recorded because it is the only fix for 4127 and 7161)
Assistant-turn facts (a video title+URL, a shift table) are lost at extraction. A BM25/vector pass over raw user+assistant messages (chunk 600 chars) when the question matches /(you|we) (recommended|said|told|suggested|discussed)|previous (chat|conversation)|remind me/ and memory hits are weak (<3 or top score low). Worth +2 q (4 pts) on this sample; belongs with the extraction work.

### Recommended order of experiments tonight (about 6 runs of 50, all judge-cached)
1. Build contexts once: `--k 30 --max-chars 9000 --with-sources`. Slice k15/20/25/30 with `--k-limit` (this alone tests #3 and #9).
2. `concise` (reproduce 54/80/44; must match, else harness drift), then `auto_v1` at k15 plain.
3. `auto_v1` + #4 render + #5 scaffold; then + #6 few-shot.
4. Cascade (#2) on the best of the above; then `--think` (#7) on the best prompt.
5. Add #8 routing; #10 only if the DB dump shows the missing memories exist.
Realistic stack: 27 to 32-35 strict (64-70), with lenient honestly measured. Reader-only ceiling about 40.

## 4. Judge false negatives and a tighter strict judge

Counts (strict judge, Gemma-4-e4b, over the 3 variants x 50 rows):

| kind | n | examples |
|---|---:|---|
| clear false negative: right value, right unit, wrapped in a sentence or with a harmless extra clause | 3 | 29f2956b 9B "You practice guitar for 30 minutes daily." (compute "30 minutes" passes; 35B "The user practices guitar for 30 minutes daily." fails); db467c8c 9B "nine months, having overstayed their visa by that duration" (compute/35B pass) |
| hedge flagged on a value that equals gold | 3 | a2f3aa27 compute and 35B "approximately 1300" (the memory itself says "approximately"); c19f7a0b compute "Around 6:30 pm" |
| unit/partial dropped by the answerer (judge correct to fail) | 3 | gpt4_6dc9b45b "4" vs "4 months ago"; a82c026e "Dark Souls 3" vs "...DLC"; 65240037 "1:10" vs ratio sentence |
| rubric gold (preference) that no short answer satisfies "every fact" | 3 | 1a1907b4, 1da05512, 1c0ddc50: strict = 0/3 in every variant by construction |
| lenient false positives (IDK credited on answerable questions, concise) | 7 | a2f3aa27, 9aaed6a3, 1a1907b4, 1da05512, 7024f17c, 09ba9854, 1c0ddc50 |

Inconsistency evidence: concise passes "You attended five sessions..." and "You were 18 years old...", but fails "You practice guitar for 30 minutes daily." The strict verdict at `max_tokens=8` is a coin flip on near-identical sentences.
The earlier note ("~5 of 20 misses are false negatives") holds up as 2 hard + ~3 soft in the concise run. Hand-corrected strict for concise: 29/50 (58) counting only the 2 hard false negatives.

Proposal: keep the original judges (cached under their own key) and ADD `strict_v2` plus `lenient_v2`; report all four. Gate: run strict_v2 on all 150 existing rows, hand-check that the 27 concise strict passes stay passes and 29f2956b/db467c8c flip.
Use a one-sentence justification before the verdict (`max_tokens` 60; take the last line `VERDICT: CORRECT|WRONG`); a bare 8-token verdict is part of the noise.

```
You are a strict grader. Compare the assistant's answer with the gold answer.
Question type: {qtype}
Question: {question}
Gold answer: {gold}
Assistant's answer: {answer}

Decide by FACTS, not wording:
- List the facts in the gold answer (values with units, names, items, dates, yes/no). The answer is CORRECT only if every one of them is stated in the answer, or an exactly equal form of it ("nine" = 9, "an hour and a half" = 90 minutes = 1.5 hours, "$0.75" = 75 cents). Where the gold lists acceptable alternatives (for example "21 days; 22 days is also acceptable"), either is fine.
- Extra words, a full sentence, or additional TRUE detail around the correct facts do not make it wrong. Extra detail that contradicts the gold does.
- WRONG if the answer: gives a different value; omits any part of the gold (partial list, missing item, missing unit when the unit is part of the answer); offers several alternative answers or a range; says it is unsure ("might", "possibly", "I think", "probably", "not sure"); or says "I don't know"/cannot answer when the gold has a value.
- "about/around/approximately" in front of a value is NOT a hedge when the number equals the gold exactly; it IS a hedge when the number differs or a range is given.
- For type "abstention": CORRECT only if the answer explicitly declines or says the information is not available.
- For type "knowledge-update": the answer must give the latest value, not an older one.
- For type "single-session-preference": the gold is a rubric of what the user would prefer. CORRECT only if the answer's suggestion is consistent with the preferences in the rubric and uses the personal background it names; WRONG if it is generic, contradicts a "would not prefer" item, or declines.
Write one short sentence naming the gold facts and whether each is present, then a final line: VERDICT: CORRECT or VERDICT: WRONG
```
`lenient_v2`: same text as the original lenient plus one line "A refusal ('I don't know') is WRONG unless the type is abstention; for type single-session-preference apply the rubric rule above." This removes the 7 IDK credits (true lenient about 66-70, not 80) and makes the lenient/strict gap meaningful.
Whether "approximately 1300" should pass is a policy choice: the rule above passes it only when the number is exact (a2f3aa27), which is defensible but is the one place a tighter judge helps the numbers; flag it in the report.

## 5. Caveats

- Full context text is not on disk; causes marked fact-not-in-context for 0f05491a, 372c, 2ebe, 08f4 are inferred (answer text plus preview). Step 1 of the run order (rebuild contexts, dump memories of those DBs) settles them.
- n = 50: one question is 2 points; only count an intervention as real if it flips >= 3 questions net or reproduces on the LoCoMo set (acc_locomo strict 36.9, IDK-heavy) as a regression guard.
- Gains in section 3 overlap heavily (the same 6-8 rows are fixable by #1, #6, #7); do not sum them.

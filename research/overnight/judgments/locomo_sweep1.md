# Judge audit: LoCoMo conv0 sweep 1 (P7-L, P1-L, P3-L vs B0-L)

Judge agent, read-only (no model calls). Base `locomo_c0_vk15_concise_acc.json` (label B0-L). Answerer qwen3.8-9b-mlx, judge google/gemma-4-e4b.
All three variants: n = 66, ids identical to base (compare.py reports "66 paired questions", no unpaired rows). Same contexts file
(`locomo_c0_vk15_ctx.json`); only the config keys below differ from base (checked by diffing the `config` blocks).

## 0. Cross-cutting findings (read these first)

| finding | evidence | consequence |
|---|---|---|
| Lenient judge credits "I don't know" | IDK answers graded lenient-CORRECT: base 5/15, quotes 4/8, auto_v1 8/23, saidon 5/14 (gold has a value in e.g. q9, q25, q33, q35, q18) | every lenient flip that involves an IDK answer is judge-noise; base lenient 77.3 is inflated by ~4-8 pts |
| Strict judge keyword false-positive | base q7: gold "Single", base answer "I don't know ... (e.g., single, married, in a relationship)" graded strict-CORRECT | base strict 50.0 is really 48.5; the q7 "regression" in quotes/saidon is the judge un-fooling itself |
| Strict judge ignores its own rule | base q2: gold "Psychology, counseling certification", answer has neither term, strict-CORRECT; q49/q37 flip between near-identical paraphrases | wording-only flips are common; n=66 strict has about +-3 questions of pure noise |
| Date format is NOT a systematic penalty | explicit-day golds (q0 "7 May 2023", q15, q19, q24), ISO answer accepted strict 13/15 (both misses are q0 in sentence form); "May 7, 2023" 1/1 | ISO vs "7 May 2023" costs at most 1 question; do not spend a fix on it |
| Real temporal strict gap is gold phrasing | base temporal strict 38.9 vs lenient 83.3. 5 questions have golds like "The Friday before 15 July 2023" / "The week before 3 July 2023" (q20, q27, q28, q29, q31): answers hold the right ISO day, lenient=Y, strict=N in all four files | cap on temporal strict gain from any render change is about +5 questions; needs the answer to say the weekday (see section 5) |
| Gold unsupported by context | q4 ("Transgender woman"; no memory says woman, `evidence_retrieved=false`), q3, q56 (partly) | base passes on these are lucky guesses; class `unsupported` below |
| parser | auto_v1 raw: 54/66 have an "Answer:" line, 12/66 reply with a bare one-line "I don't know. ..." and no Answer line (fallback = last line = whole reply). 0 truncations (no raw lacks Answer: when longer than one line) | parser is not a cause of any loss (section 3) |

Classes: real-fix, real-regression, judge-noise (score moved, content equivalent, or base credit was spurious), format-only,
parser, unsupported (gold not derivable from the retrieved context; base pass was a guess). `S` = strict flip, `L` = lenient flip, `+` gained, `-` lost.

---
## 1. P7-L: source quotes under each memory (`--render quotes`, quote_chars 300, up to 3 per memory)

### 1a. Recompute

| metric | base | variant | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict | 50.0 | 53.0 | +3.0 | 7 | 5 | 0.774 | [-7.6, +13.6] |
| lenient | 77.3 | 86.4 | +9.1 | 9 | 3 | 0.146 | [-1.5, +19.7] |
| substring | 30.3 | 31.8 | +1.5 | 3 | 2 | 1.000 | [-4.5, +7.6] |

Other: IDK answers 15 -> 8; mean answer time 3.43 s -> 5.57 s (+62%; `ctx_chars` does not count the quotes, so token cost is not in the table);
by type strict single-hop 64.7 -> 79.4, lenient 79.4 -> 91.2; multi-hop and open-domain strict 30/25 -> 0/0 (n=10/4, all from judge-noise or `unsupported` below).

### 1b. Every flip (17 questions)

| id | type | flips | gold | base -> variant (short) | class | why |
|---|---|---|---|---|---|---|
| q1 | temporal | S+ L+ | 2022 | IDK -> "painted a sunrise in 2022" | real-fix | quote: "I painted that lake sunrise last year" (said 2023-05-08); not in the fact text |
| q33 | single-hop | S+ L+ | self-care is important | IDK -> "realized self-care is really important ..." | real-fix | quote: "I'm starting to realize that self-care is really important" |
| q38 | single-hop | S+ L+ | creating a family for kids who need one | off-topic -> "provide a safe and loving home for children in need" | real-fix | quote ranking pulled the right memory text |
| q46 | single-hop | S+ | working with trans people, helping them accept themselves, supporting mental health | generic -> "particularly trans individuals, accept themselves, mental health" | real-fix | quote: "working with trans people, helping them accept themselves and supporting their mental health" |
| q48 | single-hop | S+ L+ | therapeutic methods and how to best work with trans people | IDK -> exact content | real-fix | quote has "different therapeutic methods and how to best w..." |
| q63 | single-hop | S+ L+ | many people wanting to create loving homes for children in need | IDK -> exact content | real-fix | quote has "so many people wanted to create loving homes for children in need" |
| q18 | multi-hop | L+ | dinosaurs, nature | IDK -> "animals, dinosaurs, pots" | real-fix | quote has the dinosaur exhibit; "nature" only approximated by "animals" (lenient only) |
| q30 | multi-hop | L+ | Pride parade, school speech, support group | list without parade -> list with parade, school talks, support group | real-fix | the parade memory now surfaces (quote text); strict still N because the list is wrapped in extra events |
| q0 | temporal | S+ L+ | 7 May 2023 | "...on 2023-05-07." -> "...on May 7, 2023." | format-only | same date; judge accepted the prose date, rejected ISO in this one sentence (see section 0) |
| q21 | open-domain | L+ | Yes, since she collects classic children's books | IDK -> IDK + explanation | judge-noise | both abstain; lenient judge credited the longer IDK |
| q2 | open-domain | S- | Psychology, counseling certification | "counseling and mental health" -> "+ LGBTQ rights advocacy" | judge-noise | neither contains the gold; the base pass was already spurious |
| q5 | temporal | S- | The Sunday before 25 May 2023 | "May 20, 2023" -> "charity race for mental health on May 20, 2023" | judge-noise | identical date, 3 extra true words; gold itself is off (20 May 2023 is a Saturday) |
| q7 | multi-hop | S- L- | Single | IDK-with-keyword -> "in a relationship with Melanie" | judge-noise | base pass was the keyword false-positive. NOTE: variant answer is a hallucinated claim (no quote says it); a worse failure type than IDK, not scored differently |
| q9 | temporal | L- | The week before 9 June 2023 | IDK -> "picnic on June 26, 2023" | judge-noise | base IDK lenient credit was spurious; variant gives a wrong date (picnic is the 2023-06-26 event, not the friends/mentors meet-up) |
| q13 | multi-hop | S- | counseling or mental health for Transgender people | "... to support trans people" -> "... support people on their own journeys" | real-regression | dropped "trans"; strict judge was right |
| q6 | temporal | L- | June 2023 | "week of June 19, 2023" -> "already went ... around June 20, 2023, no future plan" | real-regression | rejects the question's premise although a quote says "thinking about going camping next month" (said 2023-05-25); also typo "Melan" |
| q4 | multi-hop | S- | Transgender woman | "transgender woman" -> "transgender" | unsupported | no retrieved text says "woman" (`evidence_retrieved=false`); base was a guess, variant is the faithful answer |

### 1c. Counts (17 questions)

| class | n | strict flips | lenient flips |
|---|---:|---|---|
| real-fix | 8 | q1 q33 q38 q46 q48 q63 | q1 q33 q38 q48 q63 q18 q30 |
| real-regression | 2 | q13 | q6 |
| unsupported | 1 | q4 | |
| judge-noise | 5 | q2 q5 q7 (all down) | q21 (up), q7 q9 (down) |
| format-only | 1 | q0 | q0 |
| parser | 0 | | |

Audit-adjusted net: +8 fixes (+1 format) vs -2 real regressions (-1 unsupported) = +5 to +6 questions = +7.6 to +9.1 pts. Matches the raw lenient +9.1; the raw strict +3.0 understates it because 3 spurious strict losses (q2, q5, q7) came from judge-noise.

### 1d. Leak check

| searched | where | result |
|---|---|---|
| `gold`, `has_answer`, `evidence` | `research/overnight/prompts/*.txt` (6 files) | no matches |
| same | `scripts/answer_eval.py` | `gold` read only in `grade()`/`substring_ok()` and the judge templates; `run_prompt()`/`render_memories()` use only `context`, `source_quotes`, `memory_ids`, `question`, `question_date`. `evidence_retrieved` is copied into the output row after answering, never into a prompt |
| quote source | `crates/neuralgraph-research/src/benchmarks/chat_memory_context.rs` lines 181-210 | quotes = `sources` of the memories the retriever returned for the question (`search(..., with_sources)`, filtered to `hits`). Gold turns (`has_answer`, `evidence_ids`) feed only the `evidence_retrieved` flag (lines 222-237). No leak |

### 1e. Verdict

**confirm** (checklist: real-fix 8 >= 2x real-regression 2, or 8 vs 3 counting `unsupported`; lenient +9.1 >= 4; no metric down). Mechanism is exactly the expected one (details present only in the raw message), but statistics are weak (p 0.77 / 0.15, one conversation), so treat as "confirmed for S1", not shipped.
Second judge: not run (no model calls allowed); the sign check under strict_v2 (`--extra-judges`) is still owed.

Ledger row to paste (replace P7-L verdict/notes): `| P7-L | S0 | LoCoMo c0 | source quotes under each memory | ... | 53.0 | 86.4 | 31.8 | 78.8 | +3.0 | +9.1 | 0.774/0.146 | confirmed-S0 | judge audit: 8 real-fix / 2 real-regr / 1 unsupported / 5 judge-noise / 1 format; fixes are single-hop details only in the source message; owes strict_v2 recheck + conv 1-2 replication; cost +62% answer time |`

---
## 2. P1-L: auto_v1 prompt (compute when needed, ends with "Answer: ...")

### 2a. Recompute

| metric | base | variant | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict | 50.0 | 39.4 | -10.6 | 1 | 8 | 0.039 | [-19.7, -3.0] |
| lenient | 77.3 | 72.7 | -4.5 | 4 | 7 | 0.549 | [-15.2, +4.5] |
| substring | 30.3 | 27.3 | -3.0 | 3 | 5 | 0.727 | [-12.1, +4.5] |

Other: IDK answers 15 -> 23; mean answer length 101 -> 71 chars; mean answer time 3.43 s -> 7.22 s; by type strict single-hop 64.7 -> 47.1 (-6 questions), temporal 38.9 -> 44.4, multi-hop 30 -> 20.

### 2b. Every flip (15 questions)

`raw` = full reply (empty in the JSON when reply == parsed answer). "parse" column says whether the parsed line equals the model's intended answer.

| id | type | flips | gold | base -> variant | parse | class | why |
|---|---|---|---|---|---|---|---|
| q51 | single-hop | S- L- | Yes | "Yes, Melanie made a ... bowl" -> "I don't know" | ok (raw: bullet recap, then "Answer: I don't know") | real-regression | model demanded a memory that mentions "a photo" / that specific bowl; memory 1 says she made the bowl. Over-literal rule 1/4 |
| q56 | single-hop | S- L- | Running | "pink sneakers for running" -> "I don't know" | ok | real-regression | raw: "memory states she bought new shoes, but does not specify what they are used for"; the "pink sneakers for running" memory is in the context (ranked 5th) and was ignored |
| q58 | single-hop | S- L- | Her mental health | "de-stressing and clearing her mind" -> "I don't know" | ok | real-regression | raw: "none of them explicitly say what she says running has been 'great' for"; wants the literal word |
| q60 | single-hop | S- L- | a cup with a dog face on it | dog-face cup -> "their own pots" | ok | real-regression | raw quotes both memories, then picks the generic one ("directly stated as 'their own pots'"); wrong specificity, no refusal |
| q4 | multi-hop | S- | Transgender woman | "transgender woman" -> IDK | ok (bare one-line reply, no Answer: line) | unsupported | context never says woman; refusal is the faithful behaviour (base was a guess) |
| q2 | open-domain | S- | Psychology, counseling certification | prose -> "Counseling, mental health support, and LGBTQ rights advocacy." | ok | judge-noise | content the same as base; neither has the gold; base pass spurious |
| q37 | single-hop | S- | because of their inclusivity and support for LGBTQ+ individuals | sentence -> "Because it helps LGBTQ+ folks with adoption." | ok | judge-noise | base only adds "safe and loving home"; neither says inclusivity |
| q49 | single-hop | S- | her own journey and the support she received, and how counseling improved her life | sentence -> longer sentence | ok (answer line is 4 clauses, violates "no explanation on that line") | judge-noise | variant restates the same facts plus more; strict judge flipped |
| q0 | temporal | S+ L+ | 7 May 2023 | "...on 2023-05-07." -> "2023-05-07" | ok | format-only | same ISO date; bare date accepted, sentence-form rejected (judge inconsistency) |
| q30 | multi-hop | L+ | Pride parade, school speech, support group | list without parade -> full list incl. pride parade 2023-06-26 | ok (answer line holds the whole list) | real-fix | genuine recall gain from listing step |
| q1 | temporal | L+ | 2022 | IDK -> "IDK. ... sunset over a lake in 2022 ..." | n/a (no Answer: line) | judge-noise | still an abstention; lenient credited the mention of 2022 |
| q18 | multi-hop | L+ | dinosaurs, nature | IDK -> long IDK | n/a | judge-noise | abstention credited |
| q3 | multi-hop | L- | Adoption agencies | "counseling career options" -> "counseling or work in mental health" | ok | judge-noise | both wrong (gold not in context); base credit spurious |
| q44 | single-hop | L- | art and self-expression | "reminder of her pottery class" -> IDK | n/a | judge-noise | base answer was not the gold either; ctx has "pottery ... express herself" but neither answer used it |
| q9 | temporal | L- | The week before 9 June 2023 | IDK -> IDK | n/a | judge-noise | both abstain, longer IDK not credited this time |

### 2c. Counts (15 questions)

| class | n | questions |
|---|---:|---|
| real-fix | 1 | q30 |
| real-regression | 4 | q51 q56 q58 q60 |
| unsupported | 1 | q4 |
| judge-noise | 8 | q2 q37 q49 q1 q18 q3 q44 q9 |
| format-only | 1 | q0 |
| parser | 0 | none |

Audit-adjusted net: +1 fix (+1 format) vs -4 real regressions (-1 unsupported) = -3 to -4 questions = -4.5 to -6 pts. So about half of the raw -10.6 is real.

### 2d. WHY it hurts on LoCoMo

| hypothesis | verdict | evidence |
|---|---|---|
| Parser (truncated / empty / markdown Answer line) | **no** | all 4 real regressions parsed correctly (`answer` equals the model's last "Answer:" line); no reply truncated; the 12 no-"Answer:" replies are bare IDK sentences and the fallback captures them whole |
| Date format (ISO vs "7 May 2023") | **no** | the only date flip (q0) went in the variant's favour; ISO accepted for q15/q19/q24 in all files |
| Wrong computation / verbose | **no** | LoCoMo single-hop questions need no arithmetic; nothing was mis-computed. Only q49 is verbose (answer line = 4 clauses) and it is judge-noise |
| Content: over-literal abstention | **yes (main cause)** | IDK 15 -> 23. 8 newly abstaining questions: q4 q6 q22 q38 q44 q51 q56 q58; q51/q56/q58/q44 are strict/lenient losses with the answer present or inferable. Rules 1 ("use ONLY"), 4 ("a related but different fact does not count") and 5 ("exactly what was asked") make the 9B model demand the question's literal words ("great", "photo", "used for") |
| Content: picks generic memory over specific | **yes (q60)** | "their own pots" vs "cup with a dog face"; the model then says it is "directly stated" |
| Judge behaviour | **partly** | 3 of 8 strict losses (q2 q37 q49) and 5 of 7 lenient losses/gains are judge noise; the strict loss is statistically significant (p 0.039) but half of it is not real |
| Terse answer line | **minor** | mean length 101 -> 71; q37/q2 lost phrases the judge happened to like, but those are noise-class |

### 2e. Leak check

Same as section 1d: `auto_v1.txt` has no gold/evidence/answer-field reads (grep: no matches); it only uses `{date_line} {memories} {question}`. The `#mode: auto` header only changes parsing and max_tokens (600).

### 2f. Verdict

**reject** (for LoCoMo): 1 real-fix vs 4 real-regressions, strict -10.6 (about -4.5 to -6 after removing judge-noise), cause is over-literal abstention from the prompt text, not the parser. This says nothing about LME (arithmetic questions); do not extend the conclusion there.

Ledger row to paste: `| P1-L | ... | 39.4 | 72.7 | 27.3 | 78.8 | -10.6 | -4.5 | 0.039/0.549 | rejected | audit: 1 real-fix / 4 real-regr / 1 unsupported / 8 judge-noise; cause = IDK 15->23 (rules 1,4,5 force literal wording), parser clean; real delta about -5 |`

---
## 3. P3-L: saidon render (weekday + "said on", no chat id)

### 3a. Recompute

| metric | base | variant | delta | wins | losses | McNemar p | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict | 50.0 | 43.9 | -6.1 | 0 | 4 | 0.125 | [-12.1, -1.5] |
| lenient | 77.3 | 77.3 | +0.0 | 2 | 2 | 1.000 | [-6.1, +6.1] |
| substring | 30.3 | 30.3 | +0.0 | 1 | 1 | 1.000 | [-4.5, +4.5] |

Other: IDK 15 -> 14; mean answer length 101 -> 92 chars; answer time 3.43 -> 3.04 s. The 66 answers are identical in content for most questions; the model never writes a weekday in any answer.

### 3b. Every flip (8 questions)

| id | type | flips | gold | base -> variant | class | why |
|---|---|---|---|---|---|---|
| q5 | temporal | S- | The Sunday before 25 May 2023 | "May 20, 2023" -> "2023-05-20" | format-only | the only place where ISO vs prose decides; judge accepted prose, rejected ISO for the same day (but accepted ISO on q15/q19/q24) |
| q2 | open-domain | S- | Psychology, counseling certification | "counseling and mental health" -> "+ LGBTQ rights advocacy" | judge-noise | neither contains the gold |
| q49 | single-hop | S- | her own journey and the support she received, ... | paraphrase -> paraphrase | judge-noise | variant still says "personal experience of finding the support she received helpful" |
| q7 | multi-hop | S- L- | Single | IDK-with-keyword -> "I don't know." | judge-noise | base pass was the keyword false-positive (section 0) |
| q33 | single-hop | L+ | self-care is important | IDK -> IDK | judge-noise | abstention credited |
| q35 | single-hop | L+ | researching adoption agencies | IDK -> IDK | judge-noise | abstention credited |
| q9 | temporal | L- | The week before 9 June 2023 | IDK -> multi-item wrong answer | judge-noise | base IDK lenient credit was spurious |
| q6 | temporal | (substring +) | June 2023 | camping answer -> "(around June 2023)" | judge-noise | substring only |

### 3c. Counts (8 questions)

| class | n |
|---|---:|
| real-fix | 0 |
| real-regression | 0 |
| judge-noise | 6 |
| format-only | 1 (q5) |
| unsupported / parser | 0 / 0 |

### 3d. WHY the strict drop

The -6.1 is an artifact: 4 strict losses = 3 judge-noise (q2, q7, q49) + 1 format-only (q5), zero content changes. Temporal strict went 38.9 -> 33.3 only through q5. The weekday annotation did not change one temporal answer: relative-gold questions (q20 q27 q28 q29 q31) are still answered with the ISO date, so the lenient=Y/strict=N gap is unchanged. Caveat: the render also removes the chat id and moves the date, so unrelated wording shifts, which is what re-rolls the strict judge.

### 3e. Leak check

`--render saidon` is a pure regex rewrite of `(from chat ID, DATE)` and `[DATE, kind]` (`saidon_line`, answer_eval.py lines ~186-198) using only the context text. No gold access (grep as in 1d).

### 3f. Verdict

**reject** (no benefit: 0 real-fix, 0 real-regression, 0 on lenient); the strict -6.1 is judge-noise/format, so saidon is not harmful either. Do not retire the weekday idea (section 5, item 3), the model just never uses it in this render.

Ledger row to paste: `| P3-L | ... | 43.9 | 77.3 | 30.3 | 78.8 | -6.1 | +0.0 | 0.125/1.000 | rejected | audit: 0 real-fix / 0 real-regr / 6 judge-noise / 1 format-only; strict drop is artifact (keyword false-positive on q7 + paraphrase flips), weekday never appears in answers |`

---
## 4. Audit-adjusted summary

| variant | raw strict | raw lenient | real fixes | real regr. | unsupported | judge-noise | format-only | adjusted net (pts) | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| P7-L quotes | +3.0 | +9.1 | 8 | 2 | 1 | 5 | 1 | +7.6 to +9.1 | confirm |
| P1-L auto_v1 | -10.6 | -4.5 | 1 | 4 | 1 | 8 | 1 | -4.5 to -6.1 | reject |
| P3-L saidon | -6.1 | +0.0 | 0 | 0 | 0 | 6 | 1 | 0 | reject (no effect; loss is artifact) |

---
## 5. What to change (ranked by expected recovery, all testable with the existing harness)

| # | change | targets | expected recovery | test |
|---|---|---|---:|---|
| 1 | **Quotes + k-limit 10, adopt as the LoCoMo default candidate** (P7b-L already shows +4.5/+7.6 token-matched); add one prompt line: "Answer only what the memories/quotes state; do not infer relationships or events not written" | q7-type hallucinations (variant said "in a relationship with Melanie"), q6 premise rejection | keeps +5-6 questions; removes the 1-2 hallucination regressions | rerun P7b on conv 1 and 2, then `--extra-judges`; need real-fix >= 2x real-regr again |
| 2 | **Route auto_v1 only for computation questions** (regex on `how many|how long|how much|difference|total|ago|between` using the existing `--route-pref` mechanism), concise otherwise | all 4 P1-L real regressions (q51 q56 q58 q60 are lookups) | up to +6 pts strict (back to ~50) while keeping auto_v1's LME gain | `--route-pref auto_v1` with an inverted regex, compare to B0-L on LoCoMo (expect noise) and LME-50 (expect gain kept) |
| 3 | **Soften auto_v1 rules 1/4/5**: replace "Say I don't know only if ... A related but different fact does not count" with "If any memory plausibly answers the question, answer from it; use I don't know only if no memory bears on the question. Questions may use different words than the memory (running shoes, de-stress = mental health)"; add "when several memories answer, prefer the most specific one (a 'cup with a dog face' over 'pots')" | q51 q56 q58 (over-literal IDK) and q60 (generic over specific) | +3 to +4 questions (+4.5 to +6 pts), the whole real part of P1-L | new prompt `auto_v3.txt`; success = IDK count back to <= 16 and strict >= 48 |
| 4 | **Answer weekday in relative-date answers** (not ISO vs prose): in the prompt "write dates as 'Friday 14 July 2023'" together with the saidon render so the weekday is visible | q27, q28 ("The Friday before 15 July 2023"), q20/q29/q31 ("the week before ...") where answers hold the right day and only strict fails | up to +2 to +5 questions strict (+3 to +7.5 pts) if gemma accepts it; zero lenient change | `--render saidon` + a date-format line; measure the 5 questions q20 q27 q28 q29 q31 only first (cheap) |
| 5 | **Judge fix before the next sweep**: use `strict_v2` (rationale + VERDICT) as the primary strict, and treat IDK as WRONG when gold is not an abstention in lenient (rule is already in LENIENT_JUDGE_V2) | q7 keyword false-positive, 4-8 spurious lenient IDK credits, q2/q37/q49-style paraphrase flips | removes about 3-6 questions of noise; not a model gain, but makes deltas of 3-6 pts readable | `answer_eval.py --regrade-from <result> --extra-judges`; confirm the sign of P7-L, P1-L and P3-L holds under strict_v2 |
| 6 | Dedupe quotes before rendering (the same message appears 3x under q21's and q7's memories) and cap at 2 per memory | quotes token cost (+62% answer time) | no accuracy gain; cost -20 to -30% (estimate) | compare accuracy and `answer_seconds` with dedupe on |
| 7 | Date-format instruction (ISO -> "7 May 2023") | q0 only | at most +1 question (+1.5 pts); section 0 shows ISO accepted 13/15 | skip unless item 4 needs it |
| 8 | Parser fix | nothing | 0 (parser verified clean on all 66 rows) | none; optional hardening: if reply has no "Answer:" line and starts with "I don't know", take the whole reply as is (already what happens) |

Note on item 2 vs 3: item 2 is the safe fix (recovers LoCoMo by construction, fewer moving parts); item 3 is the one that could make auto_v1 usable everywhere, but P5-L (auto_v2, -12.1) and P2-L (strict_format, -9.1) show the 9B model reacts to "be literal / complete / terse" rules by abstaining, so expect to need two iterations.

# REPORT / LEDGER / docs consistency audit (2026-10-28, head b10946f3)

Scope: REPORT.md sections 1, 5b, 7, 7a checked cell by cell against LEDGER.md (and, where the ledger is silent, against the
per-question summaries in `.context/overnight/results/*_acc.json`); every commit hash in REPORT/LEDGER/STATE/docs checked with
`git cat-file`; defaults tables checked against `retrieval.rs` / `extraction.rs` / `worker.rs`. Read-only; nothing else edited.

## Verdict in brief
- All numbers in REPORT sections 1, 5b, 7, 7a match the ledger except the three flagged below (F1, F2, F3).
- All cited hashes exist and describe what the text says (c3899d07, c03f3efc, c13d9eaa -> reverted by 221bce5d, a471b492, 810ee07e,
  80fa6fae, 2801aa31, a9647fd0, 6f7fd375, f0862153, 84fe9045, bde1e574, a35c745b, b2689cc3). c03f3efc is cited NOWHERE (F9).
- `docs/CHAT_MEMORY.md` defaults match the code. Gaps only: `context_multi_query` and `context_min_relative_score` are undocumented (F16).
- No sentence anywhere still calls the extraction fixes default-on. The stale claims are the others in the table.

## Findings
Sev: H = wrong number or contradicts the final result, M = stale/misleading, L = polish. Line numbers are current.

| # | sev | file:line | current text | correct text | source |
|---|---|---|---|---|---|
| F1 | H | REPORT:109 (5b table, lme_base2 row, IDK) | `15 -> 11` | `19 -> 15` (overall IDK of 50; 11 is the answerable-only count of the shipped run) | results `lme_base2_plain_concise_acc.json` idk 19, `lme_base2_crateq1_concise_cascauto_v1n_acc.json` idk 15; LEDGER SHIP-q1-b2 |
| F2 | H | LEDGER:68 (SHIP-q1-b2 notes) | `idk 11` | `idk 19->15 (11 = answerable only)`. It contradicts R3-WINNER (`idk 15->11`, shipped=15) and ROUTED-base2 (`15 -> 10`) | same results files |
| F3 | H | REPORT:138-139 (7a) | `Zero strict losses on LongMemEval under either judge model.` | False for Gemma strict on the clean ingest, 62 -> 68 is 6:3. True: zero losses on strict_v2 (11:0, 10:0) and official (8:0, 8:0) under Gemma, and zero strict losses under Qwen-35B (4:0, 8:0) | LEDGER ROUTED-base2 / ROUTED-base / ROUTED-j35 |
| F4 | H | CHAT_MEMORY:301 and BENCHMARKS:467 | first-ingest `strict_v2` `52 -> 66 (10:3)` / `52 to 66` | `52 -> 70 (11:2, p .022)` (after the 5 unparsable verdicts were re-judged). BENCHMARKS:342 and REPORT:156 already say 70 | LEDGER R3-WINNER-rep1 |
| F5 | H | REPORT:174 | `**68-70 %** (LoCoMo: unchanged)` | `**64-74 %**` (routed reader; 68-70 is all-questions thinking) and LoCoMo is neutral-to-positive, pooled strict 16:9 (133 q). README already uses the routed 58->74 / 48->64 | LEDGER ROUTED-*; REPORT 7a last line |
| F6 | M | REPORT:160 | `**LoCoMo is neutral overall** (pooled strict 14 wins : 13 losses over 104 questions)` with no routed numbers | label it as all-questions thinking and add the routed pooled 16:9 over 133 questions (7a). Conv 1 regressed (57.9 -> 50.0), conv 0 did not "stay neutral" in isolation | LEDGER R3-WINNER-locomo-c1, ROUTED-c0/c1/c2 |
| F7 | M | REPORT:149 | `**New shipped configuration** (... reader = compute prompt with the 9B's thinking enabled ...)` | this is the configuration TESTED in round 3 (thinking on every question). The shipped crate defaults are the budget+raw turns; the recommended reader is routed (7a) | CHAT_MEMORY "Recommended: routed" |
| F8 | M | BENCHMARKS:467 (evidence-class table) | `...strict_v2 56 to 76 and 52 to 66; LoCoMo neutral` and no row for the routed reader | fix F4, say "thinking on every question", and add the routed row: official 58 -> 74 and 48 -> 64, strict_v2 56 -> 78 / 52 -> 72, LoCoMo pooled strict 16:9 over three conversations | LEDGER ROUTED-* |
| F9 | M | REPORT:115-118 (5b commit list), 116 | `c3899d07 ... (the one shipped behaviour change; git revert c3899d07 restores the plain block)`; list omits 80fa6fae, c03f3efc, 2801aa31 | c3899d07 is one of TWO default flips; c03f3efc (`context_max_chars` 6000 + `context_raw_messages` 3) is the other. Add 80fa6fae (opt-in episodic), c03f3efc, 2801aa31 (opt-in multi-query) | `git log` |
| F10 | M | REPORT:100-102 (5b) | `The shipped change is context_quotes = 1 + the IDK cascade (reader side).` | morning change; afternoon added c03f3efc defaults and the routed reader (7a) as the recommended protocol | git log, CHAT_MEMORY |
| F11 | M | REPORT:93, 96 (5) | heading `(final after the LME-50 rows)`; `IDK cascade - documented as the recommended reader protocol` | not final: superseded for the reader by 7a and for budget/episodic defaults by c03f3efc. The cascade is the "cheaper alternative" in CHAT_MEMORY | CHAT_MEMORY:351,392 |
| F12 | M | REPORT:1 (title) | `01:47-09:00 CDT; E56 full-50 confirmation still running at 09:00` | E56 finished long ago. Use `01:47-23:10 CDT; experiment set complete` (STATE stamp 23:10) | LEDGER E56-*, STATE:1 |
| F13 | M | REPORT:31 (3.2) | `the extraction-fix A/B is being re-run in this mode (lme_detE56)` | done: no aggregate gain (strict +6/+4, strict_v2 -2/-2, official 58 -> 54), see LEDGER E56-det | LEDGER E56-det |
| F14 | M | REPORT:112 | `"42 -> 66" stacked figure in STATE/LEDGER history` | it survives only in LEDGER row E56-50-shipped (line 65, still verdict `confirmed (pending judge)`, notes `strict 42->66`). STATE has no such figure. Annotate the ledger row as superseded (within noise, NOISE-50) | LEDGER:65 |
| F15 | M | REPORT:21, 26 (2) | `~45 rows`; `five independent judge/accountability reports` | 85 ledger rows; 8 files in `judgments/` (9 with this one) | `ls`, `grep -c '^| '` |
| F16 | M | CHAT_MEMORY (knobs ~253, EXP table ~335) | no `context_multi_query` (default false, code retrieval.rs:121, token service.rs:148); no `context_min_relative_score` 0.25 | add `context_multi_query` default false (opt-in, +1 q each LME ingest, noise: LEDGER MQ), token `context_multi_query[=0\|1]` | retrieval.rs, service.rs |
| F17 | L | REPORT:10, 73 | `default 3600-char budget` | `3600-char budget (the default when measured; now 6000, c03f3efc)` | retrieval.rs:110 |
| F18 | L | REPORT:23 | `default-off switches behind NEURALGRAPH_EXP (... context_quotes, context_k, context_max_chars)` | only the extraction switches default off; context_* tokens override defaults that are now on | service.rs apply_exp |
| F19 | L | REPORT:34 (3.5) | `quotes_user + cascade (R3) is the candidate, decided by the LME-50 rows` | decided; then superseded by the routed reader (7a) | 7a |
| F20 | L | REPORT:13; CHAT_MEMORY:395 | `LoCoMo strict -6 to -12` / `6-12 strict points` for the compute-style primary prompts | -7.6 to -12.1 (P10 -7.6, P2 -9.1, P1/P12 -10.6, P5 -12.1); `-8 to -12`. (-6.1 is the saidon row P3, a different change) | LEDGER P1/P2/P5/P10/P12-L |
| F21 | L | REPORT:14 | `saidon ... all within noise on both sets` | LoCoMo P3 -6.1 (judge-artifact), R5-L -7.6 (rejected, p .062); LME-50 R5 +6 (promising). Say "judge: 0 real flips on P3; R5-L rejected" | LEDGER P3-L, R5-L, R5 |
| F22 | L | REPORT:118 | `Workspace tests green on the final head (see STATE.md)` | STATE.md has no test statement; last green stamps are commits 0afc3db4 / 49959fef, both BEFORE 2801aa31 (multi-query). Re-run `cargo test` or soften | git log, STATE.md |
| F23 | L | CHAT_MEMORY:308 | `LoCoMo did not move under thinking on every question` | neutral on conversation 0 (+6.1, 12:8) and a regression on conversation 1 (57.9 -> 50.0, 2:5) | LEDGER R3-WINNER-locomo(-c1) |
| F24 | L | BENCHMARKS:314 | `Only one comparison in the paired table clears p < .05, out of 16 read.` | the paired table has 4 rows x 5 columns = 20 comparisons | BENCHMARKS:263-270 |
| F25 | L | STATE:3-4, 23-24, 100-101 | `Phase 0: baseline ingest running ... Nothing promoted yet`; `Decision rule: ship context_quotes=1 ... only if the 35B judge does not also show a LoCoMo loss`; `Next action: ... when >= 25 DBs, run B0` | stale. Phase = experiment set complete; the decision rule resolved (35B strict 56.1 -> 62.1, shipped c3899d07); no next action | LEDGER SHIP-q1-L |
| F26 | L | REPORT:121 (7a heading), 142 (7), 177 (6) | order is 5, 5b, 7a, 7, 6; 7a carries no note that it precedes 7 on purpose | order is fine if labelled: add `(placed before 7 on purpose: this is the final protocol; 7 is the afternoon history)`. Optional: move "6. Incidents" before 7a or renumber | - |
| F27 | M | REPORT:6-16 (section 1) | bottom-line table has no row for the final configuration (6000 + 3 raw turns defaults, routed reader, official judge 58 -> 74 / 48 -> 64); row 1 says "shipped" for quotes+cascade at 3600 chars | add a first row for the final configuration (text below) so the morning reader sees the headline in section 1 | 7, 7a |

## Verified clean (no action)
- REPORT 1: all 4 LME/LoCoMo cells of row 1 (42->50 5:1; 44->54 5:0 p .062; strict_v2 44->54 6:1; Lni 50->64 8:1 p .039; IDK 24->14;
  LoCoMo 50->50, 56.1->62.1 6:2, 77.3->80.3, IDK 15->9, S2 -6.1 4:8 p .39); row 2 (60 10:1 p .012; 54 7:1 p .07; S2 58 7:0 p .016;
  +6 7:4 p .55; S2 -8 3:7; det A/B +6/+4, -2/-2, -2/-4, official 58->54 5:7, evidence 95.7->93.5); row 3 (E48 +11.1/+11.1/+5.6); baselines paragraph.
- REPORT 4 tables (LoCoMo, LME-20, LME-50, shipped, extraction screens): every cell matches ledger or results JSON (S2/L2/Lni/IDK of R2/R3n/R6n re-read from results).
- REPORT 5b rows 1, 2, 4; REPORT 7 table, judge-audit numbers, reader-ceiling numbers, MQ summary (+1 q each LME, 0/0/-1 LoCoMo); REPORT 7a table (all
  five rows), 16:9 over 133 questions, 35B judge cells, `64-74 %`.
- README:193 matches 7a (58->74, 48->64, strict_v2 56->68, conv 1 63->68); both anchors resolve to existing headings.
- CHAT_MEMORY defaults table vs code, field by field: `context_max_chars` 6000, `context_k` 15, `context_quotes` 1, `context_quote_chars` 300,
  `context_quotes_human_only` true, `context_raw_messages` 3, `context_raw_message_chars` 400, `context_raw_include_assistant` false, channels "VK",
  depth 100, rrf_k 60, boosts 1.6/1.25, half-life 365, vector_min_sim 0.05, context_min_vector_sim 0.3; extraction 6/12/0.3/0.45/0.94/0.62/0.55,
  `extract_assistant` true, grounding 0.2/true; `hyphen_ground`, `num_dedupe`, `months_ago`, `retry_zero`, `verbatim_fallback` all false, `verbatim_max` 3;
  worker concurrency 2, batch_size 2, lease 300, backoff 5/600, maintenance 900, merge_cosine 0.96. All match.
- CHAT_MEMORY and BENCHMARKS: "Recommended: routed" is explicit, the all-questions-thinking tables are labelled as the earlier variant, extraction fixes are "default off".

## Suggested text for F27 (insert after REPORT line 10)
`| **Final recommended configuration (afternoon/evening)** - crate defaults `context_max_chars` 6000 + `context_raw_messages` 3 (c03f3efc; episodic recall 80fa6fae) on top of quotes, plus the routed thinking reader (7a) | LME-50 official judge 58 -> 74 (8:0, p .008) clean / 48 -> 64 (8:0, p .008) first ingest, strict_v2 56 -> 78 / 52 -> 72; LoCoMo pooled strict 16:9 over 133 q (conv 1 flat 4:4 once thinking is routed); 35B judge agrees (zero strict losses on LME) | **confirmed on 2 LME ingests + 3 LoCoMo conversations; the clean ingest was the selection set** |`

## Exact corrections (macOS `sed -i ''`; run from the repo root; line-addressed, re-grep if lines shift)
```sh
R=research/overnight/REPORT.md; L=research/overnight/LEDGER.md; S=research/overnight/STATE.md
B=docs/BENCHMARKS.md; C=docs/CHAT_MEMORY.md
sed -i '' -e '1s/01:47–09:00 CDT; E56 full-50 confirmation still running at 09:00/01:47–23:10 CDT; experiment set complete/' $R
sed -i '' -e '109s/15 → 11/19 → 15/' $R
sed -i '' -e '138s/Zero strict losses on$/Zero losses on strict_v2 and the official judge (Gemma) and zero strict losses (Qwen-35B) on/' \
          -e '139s/^LongMemEval under either judge model\./LongMemEval (Gemma strict on the clean ingest is 6:3)./' $R
sed -i '' -e '174s/\*\*68–70 %\*\* (LoCoMo: unchanged)/**64–74 %** (routed reader, 7a; all-questions thinking gave 68–70 %; LoCoMo neutral to positive, pooled strict 16:9)/' $R
sed -i '' -e '160s/\*\*LoCoMo is neutral overall\*\*/**LoCoMo is neutral overall under all-questions thinking**/' \
          -e '160s/over 104 questions)/over 104 questions; the routed reader in 7a is 16:9 over 133)/' $R
sed -i '' -e '149s/\*\*New shipped configuration\*\*/**Configuration tested in this round (thinking on every question; superseded by the routed reader, 7a)**/' $R
sed -i '' -e '116s/(the one shipped behaviour change;/(one of two shipped default changes, the other is c03f3efc;/' \
          -e '118s/ bde1e574 docs (noise floor, defaults off)\./ bde1e574 docs (noise floor, defaults off) · 80fa6fae episodic recall (opt-in) · c03f3efc context_max_chars 6000 + 3 raw turns by default · 2801aa31 multi-query (opt-in)./' \
          -e '118s/ (see STATE.md)/ (last confirmed at 0afc3db4 and 49959fef, before 2801aa31)/' $R
sed -i '' -e '102s/^The shipped change is `context_quotes = 1` + the IDK cascade (reader side)\./The morning change was `context_quotes = 1` + the IDK cascade (reader side); the afternoon added the 6000-char budget and 3 episodic turns as crate defaults (c03f3efc) and the routed reader (7a)./' $R
sed -i '' -e '93s/(final after the LME-50 rows)/(morning decisions; the reader is superseded by 7a, budget and episodic defaults by c03f3efc)/' \
          -e '95s/see the commit list at the end of LEDGER\/STATE/see the commit list in 5b/' \
          -e '96s/documented as the recommended reader protocol/documented as the cheaper alternative reader protocol (the recommended one is routed, 7a)/' $R
sed -i '' -e '112s/in STATE\/LEDGER history/in LEDGER row E56-50-shipped/' $R
sed -i '' -e '121s/$/ (placed before 7 on purpose: final protocol first; 7 is the afternoon history)/' $R
sed -i '' -e '31s/the extraction-fix A\/B is being re-run in this mode (`lme_detE56`)/the extraction-fix A\/B re-run in this mode (`lme_detE56`, ledger E56-det) found no aggregate gain/' \
          -e '34s/is the candidate, decided by the LME-50 rows\./was the candidate; decided by the LME-50 rows, then superseded by the routed reader (7a)./' $R
sed -i '' -e '21s/~45 rows/85 rows/' -e '26s/five independent/eight independent/' $R
sed -i '' -e '10s/default 3600-char budget/3600-char budget (the default when measured; now 6000, c03f3efc)/' \
          -e '73s/the default 3600-char budget/the 3600-char budget (the default when measured; now 6000)/' $R
sed -i '' -e '23s/default-off switches behind/switches behind/' $R
sed -i '' -e '13s/LoCoMo strict −6 to −12/LoCoMo strict −8 to −12/' $R
sed -i '' -e '68s/idk 11\. Same/idk 19→15 (11 = answerable only). Same/' \
          -e '65s/Combined vs original baseline: strict 42→66, lenient 72→82\./SUPERSEDED: stacked with a noisy re-ingest, within noise (see NOISE-50); the 42→66 figure is not a result./' \
          -e '65s/confirmed (pending judge)/superseded (within noise, see NOISE-50)/' $L
sed -i '' -e '301s/52 -> 66 (10:3)/52 -> 70 (11:2, p .022)/' \
          -e '308s/LoCoMo did not move under thinking on every question;/Under thinking on every question LoCoMo was neutral on conversation 0 and regressed on conversation 1 (strict 57.9 -> 50.0);/' \
          -e '395s/lost 6-12 strict points/lost 8-12 strict points/' $C
sed -i '' -e '467s/strict_v2 56 to 76 and 52 to 66; LoCoMo neutral/strict_v2 56 to 76 and 52 to 70 with thinking on every question (routed reader: official 58 to 74 and 48 to 64, strict_v2 56 to 78 and 52 to 72; LoCoMo pooled strict 16:9 over three conversations)/' \
          -e '314s/out of 16 read/out of 20 read/' $B
sed -i '' -e '4s/.*/Experiment set complete (REPORT 1, 7, 7a). Shipped defaults: context_quotes 1 (c3899d07), context_max_chars 6000 + context_raw_messages 3 (c03f3efc); recommended reader = routed thinking./' \
          -e '24s/$/ RESOLVED: the 35B judge showed no LoCoMo loss (strict 56.1 -> 62.1); shipped as the default in c3899d07./' \
          -e '101s/.*/None: experiment set complete; the morning read is REPORT.md./' $S
```
Manual (not sed-able): insert the F27 row after REPORT line 10; document `context_multi_query` in CHAT_MEMORY (knobs ~253 and the EXP table ~335);
STATE.md is otherwise a chronological scratchpad with many superseded sections (re-plan, LME-20 triage, queue) - consider trimming.

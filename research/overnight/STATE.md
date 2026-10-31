# STATE — last update 2026-10-30 00:35 CDT (FULLY PAUSED by user; nothing running; no crons)

## Phase
Experiment set complete (REPORT 1, 7, 7a, 7b, 8). Shipped defaults: context_quotes 1 (c3899d07), context_max_chars 6000 + context_raw_messages 3 (c03f3efc); recommended reader = routed thinking.

## FULLY PAUSED by the user at 2026-10-30 00:35 CDT
- Nothing running. The seed-4 ingest was stopped part-way (~15/50; the harness `--resume` flag continues it) and its chain was killed.
  Both crons (keep-alive and the 03:17 wrap-up) are deleted. No agents.
- To resume: say "resume". First step then: `gpu.sh lme_seed4_ingest ... --resume` + `after_seed4.sh`, or whatever new direction is given.
- Shipped and validated state: crate defaults (quotes 1, 6000 chars, 3 episodic user turns) + routed reader protocol + `memory_answer`.
  Held-out LME (100 q): strict 20:7, official 22:8 vs original. LoCoMo (4 conversations): 18:12. Tests green at 83a52690+.

## SHIPPED-BEHAVIOUR numbers (crate-rendered quotes, default 3600-char budget, concise + IDK cascade auto_v1n) — `logs/shipped_eval.log`
| set | config | strict | lenient | S2 | L2 | Lni | idk | Δstrict (w:l, p) | ΔS2 (w:l, p) | ΔLni (w:l, p) |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| LME-50 | base | 42.0 | 72.0 | 44.0 | 78.0 | 50.0 | 24 | | | |
| LME-50 | context_quotes=1 + cascade | 50.0 | 74.0 | 54.0 | 76.0 | 64.0 | 14 | +8 (5:1, .22) | +10 (6:1, .13) | **+14 (8:1, .039)** |
| LME-50 | context_quotes=2 + cascade | 48.0 | 72.0 | 52.0 | 74.0 | 60.0 | 15 | +6 (4:1) | +8 (6:2) | +10 (6:1) |
| LoCoMo | base | 50.0 | 77.3 | 62.1 | 87.9 | 69.7 | 15 | | | |
| LoCoMo | context_quotes=1 + cascade | 50.0 | 80.3 | 56.1 | 86.4 | 72.7 | 9 | 0 (3:3) | −6.1 (4:8, .39) | +3 (4:2) |
| LoCoMo | context_quotes=2 + cascade | 51.5 | 80.3 | 56.1 | 87.9 | 74.2 | 10 | +1.5 (5:4) | −6.1 (4:8) | +4.5 (6:3) |
Reading: clear win on LME-50 (fewer IDKs, more strict-correct), neutral on LoCoMo strict/lenient, but the strict_v2 judge sees a 4:8 loss on
LoCoMo under the budget-trimmed render (the untrimmed harness render had S2 59.1, −3). Decision rule: ship context_quotes=1 as default
only if the 35B judge does not also show a LoCoMo loss; otherwise ship it default-off, documented, with the cascade as the reader protocol. RESOLVED: the 35B judge showed no LoCoMo loss (strict 56.1 -> 62.1); shipped as the default in c3899d07.

## LME-50 RESULTS (46 answerable + 4 abstention; 1 q = 2 pts; Gemma judge; S2/L2 second judge PROMPT pair; Lni = lenient non-IDK)
| config | strict | lenient | S2 | L2 | Lni | Sni | idk |
|---|---:|---:|---:|---:|---:|---:|---:|
| R0 baseline concise | 42.0 | 72.0 | 44.0 | 78.0 | 50.0 | 38.0 | 24 |
| R2 quotes_user | 50.0 | 70.0 | 52.0 | 72.0 | 56.0 | 46.0 | 16 |
| **R3 quotes_user + cascade** | **52.0** | 74.0 | **60.0** | 72.0 | **62.0** | 50.0 | 13 |
| **R6 quotes_user 2/mem + cascade** | **58.0** | 74.0 | 54.0 | 74.0 | 60.0 | **54.0** | 14 |
| R4 cascade + route-compute | 50.0 | 70.0 | 54.0 | 68.0 | 54.0 | 48.0 | 23 |
| R5 cascade + saidon | 48.0 | 74.0 | 44.0 | 74.0 | 52.0 | 42.0 | 21 |
| P7b quotes (3/mem) kl10 | 46.0 | 68.0 | 46.0 | 78.0 | 56.0 | 44.0 | 18 |
| R1 cascade only | (rerun pending; partial 40 rows: 45 / 75 / 50 / 80) | | | | | | |
Reading: quotes_user + cascade is the winner on LME-50 (strict +10/+16, S2 +16/+10, IDK 24→13) AND mildly positive on LoCoMo
(R3: 51.5/78.8, S2 63.6, L2 89.4 vs 50.0/77.3/62.1/87.9). lenient_v2 dips 4-6 (credited IDKs became wrong answers). Audit + S2 pending.

## Extraction screen E56 (hyphen_ground + num_dedupe) vs sub18 baseline — retrieval level (18 failure-heavy q)
- turn@15: E56 17/18 (94.4) vs base 15/18 (83.3); ceiling same; memories 359 vs 307 (+17%); extraction time 5640 s vs 4613 s (+22%).
- E56 recovered 09ba9854 (7 → 22 memories) and 7161e7e2 (7 → 12). BUT run-to-run variance of the UNCHANGED config is as large:
  09ba9854 has 7 memories in sub18_base and 18 in lme_base; 7161e7e2 has 7 / 1. Extraction is nondeterministic across runs
  (worker concurrency 2 + LM Studio batching) → single-run ingest deltas under ~3 questions are not evidence. 41275add = 0 memories in every run.
- Verdict so far: E56 is not worse and plausibly better on recall; e2e paired comparison pending (B0-s18 running).

## Re-plan for the rest of the night (04:32)
1. ~06:40 LME-50 DBs complete → full-50 confirmation set (both judge sets): R0 base, R1 cascade, R2 quotes_user, R3 R2+cascade, R4 R1+route-compute, R5 R1+saidon, plus P7b quotes kl10 (LoCoMo winner) for the record. Same six on LoCoMo conv0 for the cross-set check.
2. Then S2 second judge (qwen3.6-35b-a3b strict regrade) on the winners; S3 combine/ablate.
3. Extraction: E56 vs sub18 base paired at ~06:45; E3 when a slot frees.
4. S4 ship: config defaults for proven changes (candidates: cascade in the harness is reader-side → document as the recommended reader protocol; quotes default if LME-50 confirms; hyphen_ground/num_dedupe if E56 ≥ base on both subset metrics).
5. REPORT.md before the user wakes; push notification at the first confirmed LME-50 win.

## LME-20 partial triage (04:15-04:25; 1 q = 5 pts, NOISY; rows in LEDGER)
| variant | strict | lenient | strict_v2 | lenient_v2 | idk |
|---|---:|---:|---:|---:|---:|
| B0 concise | 35 | 80 | 40 | 70 | 10 |
| P6 cascade concise→auto_v1 | **40** | **85** | **45** | 70 | 9 |
| P7h quotes k20/7000 + cascade | 40 | 75 | 45 | 60 | 6 |
| P1 auto_v1 | 40 | 60 | 40 | 50 | 10 |
| P7b quotes kl10 | 35 | 75 | 40 | 70 | 8 |
| P7g quotes k20/7000 | 30 | 75 | 35 | 65 | 9 |
Reading: on LME the cascade is the lever (as R1 predicted), quotes are flat/negative (opposite of LoCoMo). Decide on the full 50.

## LoCoMo results so far (66 q, base strict 50.0 / lenient 77.3) — all rows in LEDGER, judge audit in `judgments/locomo_sweep1.md`
- CONFIRMED-S0: quotes family, consistent across 8 variants: P7 53.0/86.4, P7b kl10 54.5/84.8, P7c +saidon 53.0/84.8, P7d +cascade 51.5/84.8,
  P7e +calendar 54.5/84.8, P7f 600-char 53.0/83.3, **P7g k20/7000 chars 56.1/83.3 (+6.1/+6.1)**, P13 no-inference prompt 53.0/81.8,
  P14 +cascade(auto_v3) 54.5/83.3. Judge: 8 real fixes / 2 regressions / 5 noise on P7. Best candidate for LME: quotes k20/7000.
- REJECTED again: P12 auto_v3 (softened) 39.4/78.8 — the Answer-line compute prompt loses ~10 strict on LoCoMo regardless of wording;
  P15 auto_v3 on quotes 53.0/80.3 (quotes mask the loss). Park the auto family for LoCoMo; still test on LME (R1 predicted gains there).
- REJECTED: P1 auto_v1 (IDK 15→23: rules 1/4/5 made the 9B demand literal wording) → softened `auto_v3` queued. P5/P2/P10/P11 same family.
- JUDGE-ARTIFACT: P3 saidon (0 real flips). noise: P4 calendar, P6 cascade, P8 pref.
- Judge caveats (apply to every sweep): lenient credits IDK (5/15 in base); strict passed a spurious "single" on q7; "The Friday
  before 15 July" style golds (5 temporal q) are unreachable under strict unless the answer names the weekday → auto_v3 rule 5.
- Plan: enable `--extra-judges` (strict_v2/lenient_v2) on the LME triage runs for robustness; keep original columns comparable.

## Key findings so far (R1, `hypotheses/R1_failure_analysis.md`)
- 23 strict misses: 7 fact-not-in-context, 7 IDK-despite-evidence, 4 arithmetic, 2 judge-FN, 2 wrong-memory, 1 partial list.
- Lenient credited 7 IDKs on answerable questions → honest lenient ≈ 66, not 80. Track `idk` count in every row.
- 3 rows hit the 3600-char context cap and all 3 are misses → test `--max-chars 7000 --k 20`.
- Thresholds (min_relative_score, min_vector_sim) never bind on this sample → skip Q2. Fusion weights not a lever (evidence at rank 0-1 in 18/20) → deprioritise Q3/Q5.
- Best-of(concise, compute) = 70 strict → prompt routing/cascade has headroom; reader-only ceiling ≈ 80.

## Queue (next first)
1. As soon as ≥ 25 DBs exist: partial S0 triage on the available subset (compare pairs by id). Full re-run at 50.
   B0 `s0.py --id B0 --db lme_base --prompt concise` (must reproduce ≈54/80/44; no --base).
2. S0 reader sweep vs B0 (one variable each): P1 auto_v1 | P3 saidon render | P4 calendar | P5 auto_v2 few-shot | P6 cascade concise→auto_v1 |
   P7 quotes render (k-limit 10 and 15) | P8 pref routing | P9 --think with auto_v1 (threads 2) | P2 strict_format_v1 | P10 abstain_v1.
3. S0 retrieval: build ctx at `--k 30 --max-chars 9000`, slice `--k-limit 15/20/25/30` with the best prompt; Q4 channels VKG; Q6 recency 0.
4. After LME ingest: LoCoMo conv0 baseline ingest `chat-memory-locomo ingest --db .context/overnight/db/locomo_c0.db --sessions 8 --batch-size 2`
   then locomo ctx + B0-L, and S1 confirm of the top-3 reader variants on it.
5. Extraction screens on the 18-subset (35 min each, slot 2): E5 hyphen_ground + E6 num_dedupe together → E4 months_ago → E3 verbatim_fallback → E7/E8 prompt_extra+retry_zero. Then the sub18 baseline itself (needed for the paired comparison!) — run it FIRST in slot 2 after locomo.
6. S3: combine winners (auto + saidon + calendar + cascade + bigger cap), ablate, then S2 second judge (qwen3.6-35b strict).

## Decisions
- Primary metric = LME-50 strict + lenient (Gemma), guard = turn@15; LoCoMo conv0 e2e secondary.
- Report lenient together with the IDK count (lenient credits IDK on answerable questions).
- Two GPU slots; reader evals may overlap ingest; no CPU-heavy builds during ingest.

## Next action for the orchestrator
None: experiment set complete; the morning read is REPORT.md.

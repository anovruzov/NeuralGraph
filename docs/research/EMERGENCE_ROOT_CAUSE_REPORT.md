# EMERGENCE root-cause report

## Status

- **Date:** 2026-10-15.
- **Commit:** 7639bec. Repository `file:line` citations are at 7639bec unless another commit is named. Lines added to the repository after 7639bec are cited at 91ab29c (W/rev2/head_now.out:48-68). The last commit read is HEAD 9cfa2e9 (04:38 UTC), which only appends A004's amendment to `docs/collective/A004/CHOICE-A004.md`, so every line cited at 91ab29c reads the same there (S/synth/fix/head_9cfa2e9.out:1-4, :9).
- **Scope:** development seeds 0 to 5, 200 users, synthetic worlds. Measurement: false.
- **Metric:** `recall_all` exactly as `research/e2e_ingest/harness.py` `discovery()` computes it (harness.py:1288-1294, :1336).
- **Deliverable path:** `docs/research/EMERGENCE_ROOT_CAUSE_REPORT.md` (HANDOFF-PROMPT-2026-10-15.md:155; SPEC.md:146). This text was assembled in scratch. Under this session's rules nothing was written into the repository. Committing it at that path is a separate step for the session that owns the repository. HANDOFF-PROMPT-2026-10-15.md:168 says to commit it, then present the fixes and wait for approval.
- **State.** Diagnosis only. No system change has been made.
  - The status log records a founder approval ("I approve", after 02:13 UTC) of "the EMERGENCE fixes that stay within the current rules", which "follow the root-cause report"; "choices that change an invariant still wait on the founder" (LOOP-48H-STATUS.md:317-320 at 91ab29c). The 03:55 line repeats it (:339-340). This report read those lines, not the approval itself.
  - **This report treats no status-log line as approval of any rank.** Each rank in section 8 waits on the founder's explicit yes to that rank. Section 8.0 also labels each rank: within the current rules, needs a SPEC amendment, or not covered.
  - **Added when this report was committed (2026-10-15, after the text was assembled).** At about 04:37 UTC the founder asked the review session to decide the scope of the 02:13 "I approve". The decision is recorded in LOOP-48H-STATUS.md under "Decisions received" (720af1a): the approval covers EMERGENCE fixes that stay within the current rules, once this report is committed with each fix's benefits, risks and verification test; a fix that changes an invariant (k = 3, the two-site rule, raw records staying at their site, SPEC.md's bars or seed policy) waits for the founder's explicit approval of that fix. Read against section 8.0's labels: W ranks are covered by that decision from this commit on; S and N ranks still need the founder's explicit yes. The zone conditions of 8.0 (dec. 14, dec. 15 for A004) and the no-push rule still apply to every rank.
- **Code identity.**
  - The code under `research/`, `mycelic/` and `NeuralGraph/` is identical at ce79909, ec72e22, c096aec, d481cc4, 56cfad4, 5eb2e28 and 7639bec (W/s7_code_facts.out:3-8). So every run cited here ran the code cited here.
  - HEAD moved to b7f892a, ef10579, a86821c, 6c0c322, 91ab29c and 9cfa2e9 while this report was written. Between 7639bec and 91ab29c the only files changed are five docs files (A003's record and the status log) and `lab/requests/accuracy-003.json` (W/rev2/head_now.out:1-15, :42-54). 9cfa2e9 adds 273 lines to CHOICE-A004.md and changes nothing else. No file under `research/`, `mycelic/`, `NeuralGraph/`, `tests/` or `.github/` changed through 9cfa2e9, and the working tree had 0 porcelain lines (S/synth/fix/head_9cfa2e9.out:3, :5-6, :9).
  - In LOOP-48H-STATUS.md, every line up to :301 is unchanged since 7639bec; the later commits only add lines after it (W/rev2/head_now.out:42-45).
- **CI.** No CI run's discovery block has been read. Every recall and decoy figure here comes from local runs (1.0).

## Legend

### Status of a finding
Three classes:
- **confirmed**: reproduced from real runs, or read in code.
- **probable**: supported but not reproduced, or the contested part of a finding that a challenger answered "partly".
- **unverified**: not checked.

A sub-claim that a challenger showed wrong with evidence is reported as **confirmed wrong**. That sub-claim is not an issue; the rest of the finding keeps its own status.

Statuses are what the cross-challenge left (S/rc/rc_result.json; 4.0). A finding every challenger accepted keeps its author's status. A finding a challenger answered "partly" is split: the accepted part keeps its status, and the contested part is probable or confirmed wrong. Findings no one challenged (C5, C14, and A14, B14, C17, C18, which are new in round 2) carry their author's status, marked "not cross-challenged". No finding is upgraded past that.

Section 1 grades stated figures in its own words: **verified** (confirmed), **not verifiable from here** (unverified), **wrong** (confirmed wrong).

### Kind of figure
- **[real]** Real processes, run locally: process mode, one child process per site, nats-server with JetStream, the Mycelic service and the fabric. Also read-only counts and re-scores over the stores and reports such runs left.
- **[CI]** GitHub Actions job logs: the e2e `run` job, the mycelic unit suite, or the lab workflow.
- **[local test]** Local runs of the unmodified mycelic unit tests on a shared 4-CPU sandbox (5.1). Not the EMERGENCE path.
- **[harness]** The simplified in-process harness: shipped site, verifier and gate code in one process, with no child processes, no NATS and no fabric.
- **[bound]** An offline bound: a claim-derived bound over stored claims, the diagnosis's window model, or emulated site verdicts. Nothing in the pipeline runs.
- **[sim]** The research simulator `research/mycelic`, `simulated: true`, with modelled behaviour and modelled latency. Its figures never stand for the end-to-end system (SPEC.md:43-44).
- **[code]** Read in code.

### Paths and citations
- `S/` = `/tmp/claude-0/-home-user-NeuralGraph/9160890b-ee51-58d5-92df-ff8899aa2f59/scratchpad/emergence80/`. `S/rc/` holds the reviewers' work. `S/r2/` holds the corrected diagnosis (`S/DIAGNOSIS-r2-v2.md`) and its outputs; its last critics are in `S/synth/r2_critiques.json`.
- `W/` = `S/synth/work/`: this report's re-checks. `W/path/` holds the helpers of sections 2 and 3. `W/rev/` and `W/rev2/` hold the checks of the two revisions (7.5).
- `W/ci_run1…` and `W/ci_run2…` abbreviate `W/ci_run1_job115404706053_tail700.txt` and `W/ci_run2_job116124311106_tail700.txt`.
- Short keys: T = `S/r2/out/tables.txt`, TV = `S/r2/v2/tab_v2.out`, NT = `S/r2/crit3/tab_noT2k.out`, GV = `S/r2/v2/grid_v2.out`, CV = `S/r2/v2/coarse_v2.out`. `TV:240` is line 240 of TV.
- After a file's first full citation in a section, it may be cited by base name. Appendix A maps names to paths. No scratch file is committed; A.1 says how to regenerate them.

### Names
- **Finding ids.** A1-A14: reviewer A (runtime, transport, the loop stall). B1-B14: reviewer B (retrieval, detection, graph, decoys). C1-C18: reviewer C (benchmark integrity). D-F1 to D-F15: reproducer D.
- **Decisions.** "dec. n" is decision n in S/DIAGNOSIS-r2-v2.md §9 (from line 1025). Dec. 14 is "c7 (M13)". Dec. 15 is "A003 sequencing"; section 8.0 restates it for A004.
- **Gate rules** (S/r2/ceiling.py:28-36):
  - R0: the shipped gate. At least 2 counted sites, each with support ≥ k.
  - R1: R0 with one counted site.
  - R2: at least 2 confirming sites; a confirm below k counts at its bucket's lower bound. It uses the buckets sites already send. It reverses architecture decision D1, "weak confirms are not counted" (`docs/collective/ARCHITECTURE.md`:1396-1399).
  - R3: exact cross-site sums; needs counts in 1..k−1 to leave sites. R3sim is its simulated oracle form.
  - R4[R2] or R4[R3] "timed": R2 or R3 restricted to keys that a detector or rule-layer row surfaced, with the first signal at or before the window end (S/DIAGNOSIS-r2-v2.md:381).
- **Window sets.** G6 is the aligned grid W07-W12, W13-W18, W19-W24. G6+G12 adds W01-W12 and W13-W24. "Nested" is W01-W12, W01-W18, W01-W24. The "W06+ what-if" regimes also ask windows that end at W06 to W11, for example the grid from W01 (W01-W06 to W19-W24) and nested from W01 (W01-W06, -W12, -W18, -W24). "+14" and "+20" mean the question is dated Sunday of the window's end week plus 14 or 20 days.
- **Decoy levers** W4, W4k, W3k, T2k, T2p, R4lb, R4lbcur and C1s: S/r2/rules_decoys.py:22-33; 6.4 to 6.6.
- **Ceiling models** (Summary item 6; 4.3). Model 1 and Model 2 are never mixed in one comparison. Section 1.2 also cites the diagnosis's **sliding check** (6-week windows ending at any week, no W12 cut; S/r2/ceiling.out:110-111, :135-142), only to read the 22:16 status line.
- **Today's design** = the R0 gate, candidates from detector snapshots only (first eligible week W12), the shipped 6-week window, and HQ's weekly cells kept.
- **Today's rules** = SPEC's invariants (SPEC.md:119-121); per-site k for every count that leaves a site (`docs/collective/LEAKAGE.md`:32-34); and the founder's defaults as written (HANDOFF-PROMPT-2026-10-15.md:179-182): the two-site corroboration rule is an invariant, and a cross-site protocol over encrypted below-k inputs is not permitted.
- **Decoy classes** 1 to 5 (6.1). The diagnosis calls them D1 to D5, which is unrelated to architecture decision D1.
- **Zones** 1, 2, 3a and 3b (8.0).

## Summary and answer to the final question

The final question, as the directive gives it: "what specifically prevents Mycelic's complete distributed discovery architecture from recovering the missing patterns, and which demonstrated failures must be corrected before 80% end-to-end recall can credibly be targeted?"

**The answer, in short.**
- **Where nothing is lost.** Transport, NATS, the fabric and routing change no pattern or decoy decision at seeds 0 to 5 (confirmed). Tesseract changes no verdict at seed 0 (confirmed). At seeds 1 to 5 only confirms were checked, so the Tesseract result there is probable. Patterns are lost before the first question and at the two-site count.
- **What stops recovery, by measured size.** Reviewer B's causal classes, counting each pattern's own facet records [harness][bound] (S/rc/B/cross_seed_0_5.txt:19):
  1. **Per-site k = 3, two counted sites and one 6-week window:** 93 rare misses. Counting own facet records, even perfect detection does not recover these under the shipped 6-week window. Counting every claim, C's harness returns `supported` for 2 of them (s0 p005, s4 p023; 3.5). Mechanism confirmed; per-pattern classes probable.
  2. **The candidate source:** only detector snapshots are asked, and the detector cannot fire before 2024-W12. This costs 43 patterns (39 common, 4 rare) that are supportable only before W12. Confirmed.
  3. **The D2 self-baseline:** the burst baseline holds a pattern's own earlier facets. 10 patterns are detector-silent at W12 or later (9 common, 1 rare). Mechanism confirmed at seed 0; as a cause separate from item 2, probable.
  4. **One question per key, at its first alert:** 1 measured loss (seed 0 p028). Confirmed.
- **Parts of the architecture are absent from the run** (item 4).
- **Today's design** (R0, detector candidates from W12, weekly cells kept): no computed model or window set reaches a 0.80 mean.
- **Today's rules:** every computed configuration stays below 0.80. That is not a proof that 0.80 is impossible. Every computed path above 0.80 needs a gate change or sub-k shares, and its decoy, budget and privacy costs are not measured (item 6).

**1. Baseline (section 1).**
- [real] Seed 0: 13 of 40 found, `recall_all` 0.325. Common 13 of 22; rare 0 of 18 (W/s1_reports.out:1). Seven local seed-0 runs give the same found set and decoys (:1, :7-12).
- [real] Seeds 0 to 5: 13, 12, 17, 17, 19 and 15 found, 93 of 240, mean 0.3875 (W/s1_reports.out:18). Seeds 1 to 5 ran with `check_world_counts` bypassed in the reviewers' own processes and with the drills skipped.
- [real] Decoys at seed 0: 17 of 50 accepted (class 2: 5, class 4: 9, class 5: 3) (W/s1_reports.out:1). Over six seeds: 118 of 300 under the frozen facet-0 rule; 177 of 300 counting any facet key inside the window (S/rc/C/r2/logs/derived_totals.out:1).
- [sim] 0.5975 at 10,000 users and 0.5660 at 50,000 users are simulator output on a different metric and different worlds (W/sim_figures.rerun.out:3-4; C5, confirmed, not cross-challenged). They say nothing about the end-to-end system. The 10,000-user mean averages simulator seeds 0 to 9. That departs from this session's 0-to-5 rule, and the orchestrator accepted it: those seeds lie inside SPEC's development range, and no preliminary or held-out seed was generated or read (1.1).

**2. Where the 147 misses are lost** [real] (S/rc/D/out/tables/first_failing_stage_by_seed.txt:3-7):
- 138 never got a detector snapshot.
- 2 had a snapshot week outside the label window.
- 2 were cut by `top_n` = 60.
- 5 were asked, but fewer than 2 routed sites confirmed with support ≥ k. D labels this stage "verification" (stage 8). The gate then leaves 4 of them `hypothesis`. The fifth, s2 p022, ends `stale`: its newest confirming week is too old, and the gate checks freshness before the two-site minimum (S/r2/decomp_r2.out:80-84; `gate.py:256`, `:263`).
- Transport, the fabric and routing change no pattern or decoy decision (A1, D-F10, confirmed). Tesseract changes no verdict (B10, C2: confirmed at seed 0, probable at seeds 1 to 5).

**3. Why they are lost.** Reviewer B's causal classes [harness][bound], counting each pattern's own facet records (S/rc/B/cross_seed_0_5.txt:19).
- **93 rare patterns: per-site k = 3, two counted sites and one 6-week window.** K_site 57 (no site ever holds 3 own facet records); K_2site 27 (only one site does); K_window 9 (two sites do, never in one window). Mechanism confirmed (B3, D-F3). The per-pattern classes are probable. For two of them, s0 p005 and s4 p023, C's harness questions return `supported` through records that are not the pattern's own (3.5).
- **43 patterns (39 common, 4 rare): the detector warm-up.** These are supportable only before W12. No site is eligible before 2024-W12 (`mycelic/collective/detect/detectors.py:281-283`; `research/e2e_ingest/emergence_pack.py:72-73`), and only snapshot candidates are asked (`research/e2e_ingest/emergence_world.py:289`). Confirmed (B1, D-F2 mechanism, C17 not cross-challenged).
- **10 patterns (9 common, 1 rare): the detector is silent at W12 or later.** In 9 of them, a strong site's D2 baseline holds the pattern's own earlier facets (S/rc/B/r2/detect_subcause.txt:28). Mechanism confirmed at seed 0. As a cause separate from the warm-up it is probable (B2; C reads it as the same root).
- **1 pattern asked once, too early:** seed 0 p028 (B4, D-F7, confirmed).
- **The rule-only drop is where 138 losses show.** D-F1 reads it as the root cause; B contests that, so that reading is probable. [bound] Routing every rule-only key under the shipped gate supports at most 55 of the 138 at any week, and 14 at weeks ≥ W12, counting every claim (S/rc/B/r2/rule_only_recovery.txt:7).

**4. What the run does not contain** (D-F11, C10: confirmed; B10, C2: confirmed at seed 0, probable at seeds 1 to 5):
- no aggregation level above sites;
- no recursive or follow-up question (the pack's copy of the follow-up layer is not run, `emergence_pack.py:83`);
- the shipped pushdown Orchestrator is not imported;
- no hypergraph code, and site graphs have 0 edges.

[harness] Tesseract and NeuralGraph change 0 of 165 verdicts at seed 0 (S/rc/C/logs/tesseract_counterfactual.out:3-6). No measurement shows that any of these parts would recover a missed pattern. The one measured cost of the missing re-question is p028.

**5. Decoys (section 6).**
- [real] Class 2 and 4 decoys are accepted almost whenever asked: 37 of 39 and 41 of 41 (W/decoy_class_table.out:32, :34). Of the 23 class-2 decoys not accepted, 21 were never asked, and 2 were asked and ended `hypothesis` (seed 5; :27). All 19 class-4 decoys not accepted were never asked (:34).
- Echoes by different users count as independent roots: defect, confirmed (B6, D-F4).
- There is no order check: design weakness, confirmed (B7). The generator's real patterns are not ordered either (C).
- A confirm ignores retractions, and nothing re-checks a conclusion: confirmed (B8).
- An 8-token negation window stores 135 of 196 negated rows as claims at seed 0: confirmed (B5).
- The frozen acceptance rule sees only facet-0 keys and misses 59 in-window acceptances: confirmed (B9, D-F6; C18 not cross-challenged).
- Accept-all control: accepting every detector candidate [harness] finds 98 of 240, against the system's 93 [real]. Reported decoys: 122 against 118 (S/rc/B/r2/accept_all_check.txt:7-8). Confirmed (C3).

**6. Can a 0.80 mean be reached, and is it impossible under the rules?**

These are offline bounds on six development seeds under stated models. They are not proofs over the generator. SPEC.md:126-128 asks for a written proof if 80% cannot be reached; no proof exists either way. Six-seed t bounds cannot separate configurations (10.6).
- **Model 1** is D's claim-derived bound: perfect detection, every site asked, every stored claim on the key counted, and the shipped 6-week window at any candidate snapshot week inside the label window. Weeks before W12 are included unless "weeks ≥ W12" is stated.
- **Model 2** is the diagnosis's fixed-window model (S/r2/ceiling.py, coarse_v2.py): fixed window sets ending at W12 or later ("W12+"), plus the "W06+ what-if" sets; perfect detection unless marked "timed"; lexical judge; every claim counted unless "own records" is stated.
- Terms as in the legend. Whether R2 keeps "two-site corroboration" depends on how that rule is read. R2 keeps two confirming sites but drops k records at each. The 22:16 line reads the rule as "at least 3 records at each of 2 sites" (LOOP-48H-STATUS.md:260-261).

- **(a) Today's gate R0, any candidate source, any computed window set: no** [bound]. Status: probable.
  - Model 1: 152/240 = 0.6333 at any snapshot week; 109/240 = 0.4542 at weeks ≥ W12 (W/capacity_sums.out:1, :3). Counting own facet records: 146 and 102 (:2, :4).
  - Model 2, W12+: 0.421 at the shipped timing (S/r2/ceiling.out:121); at most 0.608 over every admitted window (CV:421).
  - Model 2, W06+: at most 0.708 over every admitted window from W06 (W/rev/coarse_w06_r0.out:105, :109, :113); 0.696 on nested windows from W01 at +20 without freshness (:82); 0.596 on the grid from W01 at +14 (:16).
- **(b) Windows ending at W12 or later, per-site k kept, no count below k leaving a site, gate changes allowed: no computed configuration reaches a 0.80 mean with a transcript shown to pin 0** [bound, Model 2]. Status: probable. "Pin" means the records-field differencing attacker fixes a sub-k site-week exactly. That attacker gives a lower bound on leakage, not a guarantee. Pin 0 is a proposed bar that today's design does not meet: modelled as today's weekly cells plus sliding verdicts as the detector asks, it pins 5,631 sub-k site-weeks, and today's verdicts on every window the Boundary admits pin 9,127 without the weekly cells and 9,829 with them (S/r2/disclosure/attacks.out:31, :25, :19). R2, and R1 with today's answer format, send today's verdict bodies, so their pins come from the window set and the weekly cells (dec. 4 to 6), not from the gate rule. LEAKAGE.md:200 lists differencing between verdict buckets and weekly cells as not covered today.
  - R1 (drops the two-site default): at most 0.775 over every admitted window (CV:422).
  - R2 on G6: at most 0.750 (GV:26). With the weekly cells withdrawn, the attacker pins 0 there (S/r2/crit3/attacks_exact.out:5-7). With the weekly cells kept, the G6 transcript was not computed.
  - R2 on longer windows, at +14 and without the budget: 0.808 on G6+G12 and 0.829 on nested windows (CV:179, :258). Over every window ending at W12 or later it reaches at most 0.846 (CV:423). Even with the weekly cells withdrawn, the attacker pins 906 sub-k site-weeks on G6+G12 and 1,750 on nested windows (attacks_exact.out:9, :15). The 210-window set was not computed.
  - Under the per-entity budget, a non-adaptive R2 sweep on G6+G12 at +14 has a mean of 0.784, with 9 of 300 orderings at or above 0.80 (S/r2/rv2check/budget_sweep.out:13). On nested windows it keeps a mean of 0.810 (minimum 0.792; 52 of 56 choices at or above 0.80; :11).
  - R4[R2] timed on G6+G12 keeps a minimum of 0.800 over 300 orderings under the budget (S/r2/rv2check/budget_targeted2.out:4), and 0.829 in all 300 on nested windows (:6). R4[R2] targets keys that a detector or rule-layer row surfaced. Three conditions apply:
    - It routes rule-layer hits, which today's run drops (`emergence_world.py:289`; rank 7, label N).
    - It needs HQ's weekly cells, because the detector and the rule layer run on them (2.3, stages 3-4).
    - Its pins with the cells kept were not computed. A related superset transcript (the grid from W01 plus G12, with weekly cells) pins 4,852 (S/r2/v2/attacks_v2.out:10).
    - On the real run's own candidates, R2 gives 0.408 (T:66).
  - R3 needs counts in 1..k−1 to leave a site, and so do pooled cross-site counts. Model 1's pooled count gives 192/240 = 0.80 at weeks ≥ W12 (S/rc/C/r2/logs/gate_bound_variants.out:1-7). Model 2's R3 gives 0.771 at the shipped timing (ceiling.out:121). Today's rules forbid such counts (LEAKAGE.md:32-34 and the closed artifact list, `mycelic/collective/edge/egress.py:77`). The repository has no secure-aggregation code (S/r2/disclosure/repo_facts.out:81-82).
- **(c) If a candidate source other than the detector asks windows that end before W12 (dec. 1), several computed configurations exceed 0.80.** W12 limits only detector-driven candidates. The Boundary admits windows ending before W12 (`egress.py:553-556`), and scoring has no W12 condition (`research/routing_spike/world.py:136-141`; `harness.py:1288-1294`). Two families keep per-site k:
  - **R1, one confirming site** [bound, Model 2]. R1 drops two-site corroboration, the founder's default invariant (dec. 9).
    - Nested windows from W01 (ends W06, W12, W18, W24): 0.854 at +14 (CV:526) and 0.863 at +20 without freshness (CV:528).
    - Every admitted window from W06: 0.892 at +20 and 0.896 at +14 (CV:543, :545).
    - On the plain grid from W01, R1 stays at 0.754 (CV:488).
    - Every R1 figure above 0.80 counts every claim on the key. Counting the pattern's own records, R1 stays at or below 0.775 in every W06+ set (CV:554, :556; W/rev/coarse_w06_r0.out:125, :129). So R1's margin above 0.80 rests on support from records that are not the pattern's own (C12, B14).
    - [bound, Model 1] One counted site at any week, weeks before W12 included: 0.8417 (gate_bound_variants.out:7).
  - **R2, two confirming sites kept** [bound, Model 2]. A confirm below k counts at its lower bound (dec. 7).
    - The grid from W01 at +14: 0.921 (W/rev/coarse_w06_r0.out:18); 0.838 counting own records (:33).
    - With rule-layer timed targeting at +20: 0.887 (ceiling.out:146).
  - **Privacy** (records-field attacker, a lower bound):
    - R2 grid verdicts on W01-W06 to W19-W24, answered once, weekly cells withdrawn: 0 pinned. With today's weekly cells: 2,904 (S/r2/disclosure/attacks.out:43, :46-49).
    - R1 on nested windows: per-site answers thresholded at k (the (e) form), on a superset of its windows, with no weekly cells: 0 pinned (attacks_v2.out:27-29). The (e) answers with today's weekly cells pin 2,908 on the grid (attacks.out:78-81).
    - R1 with today's bucketed verdicts pins at least 1,750, cells withdrawn. That figure is for W01-W12, W01-W18 and W01-W24 (attacks_exact.out:15). The path also asks W01-W06, and its own set was not computed.
    - R1 over every admitted window: today's verdicts on all 231 windows pin 9,127 without cells (attacks.out:25). That is today's own exposure on what today's Boundary admits, whatever the gate rule (9,829 with today's cells, :19).
    - So each zero-pin result needs an enforced window set (dec. 4, 5) and the weekly cells withdrawn (dec. 6). The R1 result also needs the (e) answer format, a verdict-body change in `verify.py` (zone 3b).
    - Membership exposure was not measured. The diagnosis notes 868 of 2,431 G6 releases at N = k for R3 (S/DIAGNOSIS-r2-v2.md:838).
  - **Costs not measured.** With timed targeting on the grid from W01, raw decoys are 209 of 300 under R1 and 247 under R2 (ceiling.out:145-146), against the run's 118. No lever set was evaluated for any W06+ configuration. The budget was not applied to any W06+ path.

**Verdict.**
- **Under today's design** (R0, detector candidates from W12, the shipped 6-week window, weekly cells kept), 0.80 is not reached in any computed model. At today's timing: 0.4542 in Model 1 at weeks ≥ W12, 0.421 in Model 2 on G6.
- **Under today's rules** (SPEC's invariants, per-site k, two-site corroboration read as k records at each of two sites, nothing below k crossing), every computed configuration is below 0.80. R0 is at most 0.708 in every computed set.
- **Is 80% impossible under the rules?** Not shown. The computed configurations do not cover every window scheme or question scheme, and the written proof of SPEC.md:126-128 does not exist. Status: probable, on six development seeds.
- **Every computed path to a 0.80 mean needs a change today's rules do not allow as written:**
  - R1 drops two-site corroboration.
  - R2 counts weak confirms and reverses D1. It keeps two confirming sites but not k records at each.
  - R3 and pooled counts need sub-k shares to leave a site.
- **With W12 kept,** R2's transcripts at 0.80 or more on the window sets computed (G6+G12, nested) are pinned even with the weekly cells withdrawn (906 and 1,750). The 210-window set, and R4[R2] timed with the cells kept, were not computed. Pin 0 is a proposed bar that today's design does not meet either: modelled as its weekly cells plus sliding verdicts as the detector asks, it pins 5,631, and today's verdicts on every admitted window pin 9,127 without cells and 9,829 with them (attacks.out:31, :25, :19). R2 sends today's verdict bodies, so its pins come from the window set and the cells (dec. 4 to 6), not from the gate rule.
- **Without W12** (a non-detector candidate source), R1 on nested windows and R2 on the grid from W01 pin 0 only if three conditions hold: an enforced window set, the weekly cells withdrawn, and, for R1, the (e) answer format. None of these paths has measured decoys, a measured budget effect or measured membership exposure.
- No action in section 8 is claimed to reach any recall figure.

**7. What must be corrected before 80% can credibly be targeted** (section 8 ranks them):
1. **The measurement.**
   - `check_world_counts` refuses every seed but 0. Under SPEC.md:70 every held-out world would therefore score 0 (C1, confirmed).
   - No test pins the scoring sources, although SPEC.md:31 promises one (W/s5_code_facts.out:309-310; confirmed by search).
   - The gate thresholds, the pack, `top_n` and the detector settings are not frozen (C7, confirmed). In Model 1 (every claim), pack values move the bound between 0.4542 (shipped, weeks ≥ W12) and 0.8542 (one counted site, no freshness, no roots or reporters minimum, any week) (gate_bound_variants.out:7; S/rc/C/r2/logs/one_site_sensitivity.out:8). Two of those values are founder decisions, not tuning: one counted site drops the two-site default (rank 12, N), and weeks before W12 need a non-detector candidate source (rank 7, N). The pooled 1.0 is not a pack value: it needs counts in 1..k−1 to leave a site.
   - Gate 3 cannot see 59 in-window acceptances (C18). Its reading (pooled, averaged or per world, and which baseline) is not fixed.
   - The benchmark's shortcuts are not stated beside any figure (C9): the pack is the generator's own lexicon, entity ids arrive structured, and evidence is denser than at 10,000 users.
   - No CI discovery block can be read from here (C6, C8).
2. **The candidate source and the W12 cold start** (B1, D-F2, C17; critical).
   - In Model 1 under the shipped gate, 43 patterns are supportable only before W12 (derived_totals.out:2).
   - In Model 2 under detector targeting, no rule recovers the 35 patterns whose label window ends before W12 (ceiling.out:204).
3. **The decoy defects any recall gain would expose:** echo roots (B6), the negation window (B5), no re-check after retractions (B8), and order blindness (B7). [bound] With oracle candidates, the shipped gate already accepts 141 to 170 decoys, against the run's 118 (TV:2, :63, :124, :185, :246, :307, :368, :429, :490).
4. **The D2 self-baseline** (B2, probable as a separate cause; a fix is a detector design change, rank 8) and **one question per key** (B4).
5. **Founder decisions:** the candidate source (dec. 1); the two-site rule (dec. 9) or weak confirms (dec. 7); cross-site sums (dec. 8); the window set and its enforcement (dec. 4, 5); the weekly cells (dec. 6); and whether zone 3b files may change before A004's judging run (dec. 15, restated in 8.0).

**8. Not established.**
- The CI discovery blocks (1.0, 1.3).
- Process-mode confirmation of every [harness] "supported" result (3.8).
- The decoys, privacy exposure and budget effect of any path above 0.80, end to end.
- The exposure of W4, T2k, R4lb and C1s.
- Whether as_of +14, extra windows, a sweep or a re-ask can be built in zone 1 alone (S/DIAGNOSIS-r2-v2.md §8.5).
- Whether A004's rule binds edits to zone 3b files.

**9. The stall and A003.**
- The mycelic loop stall is not on the EMERGENCE scoring path (A6, confirmed). Its attribution to BM25 was mostly a sampling artefact (A3, confirmed): BM25 runs on the loop only in the lineage test and appears in 1 of 19 dumps (5.1). The probable mechanism is contention between the loop and the walk threads for the interpreter lock and for CPUs (A2, probable). The status log says two orphaned CPU-bound probe processes ran on 2 of the 4 CPUs from reviewer A's run of 2026-10-14 23:04 until 03:57, so stall measurements in that period ran under that extra load (LOOP-48H-STATUS.md:344-346 at 91ab29c; 5.1). That load is unverified: it is read from the status log only.
- A003 is reported apart (5.3). [CI] Lab run 38667030739 on c5dad9a completed at 02:13:35 UTC. Its `success` is the workflow's conclusion, not A003's result (W/rev/ci_runs_now.out:2).
- The repository records A003's primary pair (a-4b, `negation_terms`) as `failure`: balanced accuracy 0.780 [0.733, 0.823], gap to 0.860 0.080 (LOOP-48H-STATUS.md:313-314 at 91ab29c). This report did not re-read the lab logs, so these figures are unverified.
- At 6c0c322 the run is recorded in CHOICE-A003 Run 1, RESULTS run 13, the YC brief and the status log. The status log reads this as "recorded and frozen". One clause stays open: the sha256 of the `report.json` block is not verified (5.3). Whether A003 still gates any rank is the founder's question (8.0).

Section 8 lists each proposed change with its bound, risks, tests, before/after protocol and approval label.

## 1. Verified baseline

This section checks the figures the directive names and the other headline figures the reviewers checked. Each row gives where the figure is claimed, what it was checked against, its kind and its status. The re-checks ran on 2026-10-15; their outputs are in `W/`.

### 1.0 No CI run's discovery block has been read
- Each of the three e2e job logs has 11,692 lines: that is the `original_length` the job-log API returns for jobs 115404706053 (run 1) and 116124311106 (run 2) (W/ci_artifact_lines.txt:2-3), and for job 116200904429 (W/rev/ci_run3_tail_facts.txt:1).
- The tails read hold the end of the report and the END marker: the routing verdict counts and the `run`, `runner`, `timings`, `verdict` and `world` blocks. Sources: W/ci_run1… and W/ci_run2…, 700 lines each; W/rev/ci_run3_tail_facts.txt, 160 lines.
- The `discovery` block, and the BEGIN line with the report's sha256, lie above those tails. The host that serves full logs and artifacts is refused (W/rev/ci_run3_err.txt).
- **e2e-ingest run 38664599267 on 7639bec** is the newest unpatched run on this report's commit. It completed with all five jobs green (W/rev/ci_runs_now.out:1; W/rev/ci_run3_jobs.out:1-5). Its end-to-end job's verdict reads `pass` true, `failed` [] (W/rev/ci_run3_tail_facts.txt:2). Its discovery block is unread too.
- So every recall and decoy figure here comes from local real runs. None comes from CI.

### 1.1 The figures the directive names

| Figure | Where it is claimed | Checked against | Kind | Status |
|---|---|---|---|---|
| `recall_all` 0.325 (13 of 40), seed 0 | BUILD-E2E.md:679; LOOP-48H-STATUS.md:113; HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:46; HANDOFF-PROMPT-2026-10-15.md:97; SPEC.md:80, which credits CI run 38425113336 | Local run at ec72e22: report sha256 `159cc6eb…74ad94`, 13 of 40 (W/s1_reports.out:1). Six more seed-0 runs at ec72e22, c096aec and d481cc4 give the same found set and decoys (:7-12). `check` recomputes C's copy with 0 problems (S/rc/C/logs/check_myrun.out:3). | real | **Verified** locally. **Not verifiable from here** for CI. The CI credit at SPEC.md:80 is **wrong**; the amendment at SPEC.md:130-140 corrects it. |
| `recall_common` 13 of 22 (0.591) | BUILD-E2E.md:680; HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:47; HANDOFF-PROMPT-2026-10-15.md:97 | the same runs | real | **Verified** locally; **not verifiable from here** for CI. |
| `recall_rare` 0 of 18 | BUILD-E2E.md:680; HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:48; HANDOFF-PROMPT-2026-10-15.md:97 | the same runs | real | The figure is **verified** locally.<br>The stated cause is **partly wrong** (C13, confirmed). "By construction" is wrong, but k-suppression, together with the two-site gate and the 6-week window, is the mechanism for most rare misses (B3, D-F3; 3.3). BUILD-E2E.md:680 says "k-suppression by construction"; E2E-INGEST.md:274-275 says "invisible to the cells by construction".<br>Rare p030 became a candidate at seed 0 and was asked (S/rc/C/logs/baseline_breakdown.out:12).<br>The real runs at seeds 1 to 4 found 1, 1, 1 and 2 rare patterns (W/s1_reports.out:2-5). Three of those five rest on records that are not the pattern's own (B14, not cross-challenged; S/rc/B/r2/found_dependence_0_5.txt:44, :71, :76). |
| 59.7%: the simulator's `found` 0.597 for `H_mycelic_full` at 10,000 users | MYCELIC_ENTERPRISE.md:188, :717, :797; HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:80, :129-130; HANDOFF-PROMPT-2026-10-15.md:106 | Recomputed from the committed rows (`research/mycelic/artifacts/e1_baselines.jsonl`, `e1b_extra.jsonl`): 0.5975 over the 10 committed simulator seeds (W/sim_figures.rerun.out:3). Bootstrap interval [0.545, 0.647] (W/sim_ci.rerun.out:2). Those are simulator seeds 0 to 9 (an accepted departure, below). | sim | **Verified** as simulator output; not an end-to-end figure. |
| 56.6%: the simulator's `found` 0.566 at 50,000 users | MYCELIC_ENTERPRISE.md:162, :239, :738, :797; HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:81, :130; HANDOFF-PROMPT-2026-10-15.md:106 | `H_mycelic_full`: 0.5660 over 5 simulator seeds, all within 0 to 5 (W/sim_figures.rerun.out:4); interval [0.526, 0.608] (W/sim_ci.rerun.out:3). `G_hier_questions`: 0.566 (W/sim_g_hier.out:3). | sim | **Verified** as simulator output; not an end-to-end figure. |
| Decoys accepted: class 2 5 of 10, class 4 9 of 10, class 5 3 of 10, none in classes 1 and 3; 17 of 50 | BUILD-E2E.md:681; HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:49; HANDOFF-PROMPT-2026-10-15.md:97-98 | the same local reports (W/s1_reports.out:1, :7-12) | real | **Verified** under the frozen rule, which scores only each decoy's facet-0 key (harness.py:1296-1316).<br>"None in classes 1 and 3" holds only under that rule. Counting any facet key inside the window, seed 0 has class 1: 5, class 2: 5, class 3: 6, class 4: 9, class 5: 3 (S/rc/C/r2/logs/decoy_window_check.out:1). C18. |

**What the simulator figures are** (C5, confirmed, not cross-challenged; C11, confirmed).
- **A different metric.** The simulator scores `found_anywhere_in_register`: a match anywhere in a ranked register of 600 entries, or 2,999 at 50,000 users. Its precision is 0.0400 at 10,000 users and 0.0189 at 50,000 (S/rc/C/logs/sim_register_depth.out:2-3). By contrast, `recall_all` needs a supported note on the key after the gate (harness.py:1288-1294).
- **Different worlds:** 40 Gold patterns at 10,000 users and 100 at 50,000 (S/rc/C/logs/sim_figures.out:3-4).
- **A departure from this session's seed rule, accepted by the orchestrator.** The session's rule is to inspect only seeds 0 to 5. The directive names the 59.7% figure, which is a mean over 10 committed simulator rows; the 0.828 figure (1.2) is also a 10-row mean. Checking them meant reading committed rows for simulator seeds 0 to 9 (W/sim_figures.rerun.out:2-3). The departure is limited to those means and their intervals.
  - These rows are committed outputs of the research simulator, a different generator from the EMERGENCE worlds. Simulator seeds 0 to 9 lie inside SPEC's development range, 0 to 19 (SPEC.md:80).
  - No preliminary or held-out seed (SPEC.md:81-82, derived from freeze commits, :84-88) was generated or read. No simulator world was generated.
  - The split of these means by simulator seed, used in the earlier draft, is withdrawn: no verdict needs it.
- **Modelled latency.** `H_mycelic_full` at 10,000 users reports 1,928 modelled seconds, against 7.6 s of real runtime (sim_register_depth.out:2).
- **RESULTS_ALL.md has no simulator figure.** Its only "59.7" (RESULTS_ALL.md:154) is the shipped retriever's recall at 50 in a separate retrieval campaign (:1-3).

### 1.2 Other headline figures the reviewers checked

| Figure or claim | Where it is claimed | Checked against | Kind | Status |
|---|---|---|---|---|
| World: 309 users, 7 sites, 35 teams, 12,964 records, 40 real patterns, 40 Gold-discoverable, 18 rare, 50 decoys, 150 entities | E2E-INGEST.md:1114-1115, :1200-1201; BUILD-E2E.md:633-634 | CI `world` blocks (W/ci_run1…:578-635; W/ci_run2…:578-635; W/rev/ci_run3_tail_facts.txt:4); the generator at seed 0 (S/rc/C/logs/world_seeds.out:1) | CI; generator | **Verified** |
| CI run 1 (38425113336, job 115404706053, 56cfad4): `pass` true; `failed` []; `code_dirty` false; `run.seconds` 595.2; main pass 144.9 s; verdicts 148 confirm, 15 refute, 2 unknown; 0 budget unknowns; 0 HQ errors | E2E-INGEST.md:1128-1143, :1153 | W/ci_run1…:556-576, :112-161, :544, :104-108. Python 3.11.17, nats-server 2.10.29 (:437, :483). | CI | **Verified** |
| CI run 2 (38639771210, job 116124311106, 5eb2e28): `pass` true; `failed` []; `run.seconds` 624.6; main pass 145.8 s; verdicts 148, 15, 2 | E2E-INGEST.md:1195-1199 | W/ci_run2…:556-576, :112-161, :544, :104-108 | CI | **Verified** |
| e2e-ingest run 38664599267 (end-to-end job 116200904429, 7639bec): all five jobs green; `pass` true; `failed` []; main pass 129.2 s | LOOP-48H-STATUS.md:316-317 at 91ab29c | W/rev/ci_runs_now.out:1; W/rev/ci_run3_jobs.out:1-5; W/rev/ci_run3_tail_facts.txt:2-3 | CI | **Verified** at verdict level. Verdict counts and discovery block not read. |
| CI run 1's gate statuses (47 supported, 7 hypothesis, 1 stale) and its Tesseract and question-to-verdict timings | E2E-INGEST.md:1136-1143 | not in the 700-line tail | CI | **Not verifiable from here** |
| Six-seed real baseline: 13, 12, 17, 17, 19, 15 found (93 of 240, mean 0.3875); reported decoys 17, 18, 21, 22, 18, 22 (118 of 300) | S/DIAGNOSIS-r2-v2.md:54 (cites ceiling.out:112, 0.388) | W/s1_reports.out:18-19; two independent run sets for seeds 1 to 5 agree (:26) | real (seeds 1 to 5: guard bypassed in-process, drills skipped) | **Verified** under those conditions |
| Accept-all: 15 of 40 at seed 0; mean recall 0.4083 against 0.3875; reported decoy acceptance 0.4067 against 0.3933 | S/rc/first_C.md §6; C3 | S/rc/C/logs/accept_all_baseline.out:1; S/rc/C/r2/logs/compare_accept_all.out:7 (re-run identical, W/rerun_timings.out:5) | accept-all harness (seed 0 also re-scored from the real report); system real | **Verified** (C3; A and B agree) |
| Decoys over seeds 0 to 5: 118 of 300 under the frozen rule; 177 of 300 on any facet key in the window | C18 | derived_totals.out:1 (re-run identical, W/rerun_timings.out:8); W/s10_decoy_classes.out:1-2 | real, re-scored | **Verified** (C18, not cross-challenged; agrees with B9, D-F6) |
| Simulator: 0.828 at 2,000 users, interval [0.790, 0.868] | MYCELIC_ENTERPRISE.md:693, :797; HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:79, :129 | 0.8275 over the 10 committed simulator seeds, 0 to 9 (W/sim_figures.rerun.out:2; an accepted departure, 1.1); interval (W/sim_ci.rerun.out:1) | sim | **Verified** as simulator output |
| Simulator columns inside the seed-0 e2e report: `H_mycelic_full` 0.475 / 0.056 / 0.175 / 156,338 units; `A2_chunked_ctx` 0.925 / 0.944 / 0.300 / 167,746 | BUILD-E2E.md:683-685; LOOP-48H-STATUS.md:113-114 | W/s1_reports.out:1 | sim inside a real report | Figures **verified**. LOOP-48H-STATUS.md:113 sets 0.325 "against" 0.475; that comparison is **wrong**, because the metrics differ (C5). |
| mycelic.yml loop-test failures: 0.258 s against 0.242 s (38612799645, d270bd3); 0.255 against 0.244 (38628599664, 55914e3); 0.254 against 0.248 (38633513470, fd0966e) | LOOP-48H-STATUS.md:247-248 (the last); S/rc/first_A.md Part B | W/ci_mycelic_timing_tails.txt:3, :8, :13 | CI | **Verified** |
| mycelic.yml green runs 38631925525, 38639771190, 38661107236 | task brief | W/ci_mycelic_green_runs.txt:2-4 (14d3a82, 5eb2e28, a465752) | CI | **Verified** |
| The 21:25 line: the loop sits in its per-query BM25 build | LOOP-48H-STATUS.md:249-251 | No CI log carries a stack sample. The local samples come from a sampler that sees the loop only after it releases the interpreter lock (A3). | — | **Wrong** as a CI attribution (A3, confirmed). Section 5. |
| The 22:16 line (first written 22:20): the two-site gate caps the mean at 0.633; no seed reaches 32 of 40; a single-site gate gives 0.846; cross-site aggregation gives 40 of 40; rule-only drops lose 20 to 27 of 40 keys per seed; class C is empty | LOOP-48H-STATUS.md:259-264 | **Model 1** (D's method, snapshot at any week of the label window): two-site 23, 23, 28, 31, 25, 22 of 40, mean 0.6333 (S/rc/D/out/gate_capacity_seed{0..5}.json; gate_bound_variants.out:1-7); single-site 0.8417 with the 42-day freshness rule and 0.8458 without (one_site_sensitivity.out:2, :4); pooled 1.0 (gate_bound_variants.out:7).<br>**The diagnosis's sliding check** (6-week windows at any end, no W12 cut; neither Model 1 nor Model 2): R0 0.633 and R1 0.846 [34, 32, 34, 35, 37, 31], with or without freshness 42 (ceiling.out:110-111, :135-136).<br>First failing stage "rule-only, no snapshot": 25, 27, 21, 21, 20, 24 (first_failing_stage_by_seed.txt:3). | bound | **Partly verified** (C15). The 0.633 and the 40 of 40 are confirmed; B reproduced 0.633. B did not check the single-site figure, so that part stays probable. Its figures below re-read correctly from C's and the diagnosis's logs; that is this report's check, not the cross-challenge's.<br>- The line names the two-site rule (3 records at each of 2 sites), the single-site rule and the class-C window. It does not name the counting rule (every claim, or own records) or the snapshot-week range.<br>- Its 0.846 equals the sliding check's R1. Under Model 1 the single-site figure is 0.8417 with freshness and 0.8458 without. Each figure holds only under its own model.<br>- All its ceilings count snapshot weeks before 2024-W12, which the shipped detector cannot produce. Model 1 at weeks ≥ W12 gives two-site 0.4542, single-site 0.6417 and pooled 0.80 (gate_bound_variants.out:7). These bound detector-driven candidates only.<br>- "Class C empty" depends on the window set. Over every admitted window ending at or after W12 it is 0, with 13 rare patterns that have no askable window (CV:404). On the aligned 6-week grid it is 5, plus those 13 (ceiling.out:204). |
| "recall_all cannot exceed 22 / 40 = 0.55"; "the 18 rare patterns are invisible by construction" | HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:51-54, :128 | C13 | — | **Wrong** (C13, confirmed). The correction header at :10-17 (a35324e) withdraws both claims, but :128 still reads "ceiling 0.55 under k = 3" (rank 21 corrects it). The header's range, 0.550 to 0.775 per seed, matches Model 1's two-site bound at any week (22 to 31 of 40). |
| "Control flow, token counts and compute are executed and measured" (the simulator) | HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:71 | S/rc/first_C.md §1 (`research/mycelic/models.py`, `systems.py`) | sim | **Wrong** for token counts and compute, which are modelled (C11, confirmed). |
| The build's local measurement: 13 of 40, decoys 5, 9, 3; 1,181 s | BUILD-E2E.md:609-616, :678-686 | The discovery figures equal the ec72e22 run's (W/s1_reports.out:1) | real | Discovery figures **verified**. The commit is not stated. Its check counts (c1 27, c2 15, c4 25; BUILD-E2E.md:621-624) differ from ec72e22's (34, 19, 27; S/baseline_seed0.log:25-28), so it ran earlier code. Its 1,181 s is **not verifiable from here**. |

### 1.3 What the founder must download

CI attribution of the baseline waits on these artifacts (SPEC.md:139-140; E2E-INGEST.md:1174-1176, :1224-1226; LOOP-48H-STATUS.md:305-308).

| Run | Artifact | Id | Zip sha256 printed by the upload step | Expiry |
|---|---|---|---|---|
| 1 (38425113336, 56cfad4) | e2e-ingest-report.json | 11830620985 | `211cfc69…f56b9` (W/ci_run1…:676-679) | 2027-01-11 (E2E-INGEST.md:1157) |
| 1 | e2e-ingest-measure.json | 11831910113 | `aee751db…136c` (W/ci_artifact_lines.txt:5-6) | same run |
| 1 | e2e-suites-core, -lab, -e2e | 11830386550, 11829686567, 11828578965 | not re-read (E2E-INGEST.md:1126) | same run |
| 2 (38639771210, 5eb2e28) | e2e-ingest-report.json | 11931284874 (named at SPEC.md:140) | `9ed054d3…226a` (W/ci_run2…:676-678) | not recorded |
| 2 | e2e-ingest-measure.json | 11933036780 | `491e3a30…37bd` (W/ci_artifact_lines.txt:8-9) | not recorded |
| 2 | e2e-suites-core, -lab, -e2e | 11930333910, 11930209636, 11928934919 | not re-read (E2E-INGEST.md:1214-1216) | not recorded |
| 38664599267 (7639bec) | e2e-ingest-report.json | 11943396169 | `31a48aa3…cef1` (W/rev/ci_run3_tail_facts.txt:5) | 2027-01-13 (W/rev/ci_run3_jobs.out:6) |

Steps:
1. Download each artifact from its run page.
2. Commit it under `docs/collective/e2e_ingest/<run id>/`. E2E-INGEST.md:1175 names this path for run 1; using it for the others is this report's suggestion.
3. Check the zip digests.
4. Run `python3 -B -m research.e2e_ingest check REPORT --recorded`.
5. Read the `discovery` block.

The alternative is to paste the jobs' step summaries, which carry each report's sha256 (E2E-INGEST.md:1171-1172).

Until then, whether CI's discovery blocks equal the local one is **not established**. The code is the same (W/s7_code_facts.out:4, :7-8). The versions differ: CI ran nats-server 2.10.29 and Python 3.11.17 (W/ci_run2…:437, :483); the local runs used nats-server 2.10.22 (S/baseline_seed0.log:11) and Python 3.11.15 (`runner.python` in S/baseline_seed0/e2e-ingest-report.json).

### 1.4 Not established in section 1
- The 22:16 line's "class C empty" under its own definition was not re-run. Only the diagnosis model's window-set results are cited.
- MYCELIC_ENTERPRISE.md:737's interval [0.520, 0.618] and the other figures at :162, :188 and :239 were not recomputed. Only the 0.597 and 0.566 means were.
- The documents print 0.597; the recomputed mean is 0.5975 (W/sim_figures.rerun.out:3). The rounding rule behind "59.7%" is not established.
- Now checked, so no longer open: at 35d1d1f, HANDOFF-PROMPT-2026-10-15.md:98-99 said the baseline "is verified in two places", the first being "CI run 1 (38425113336), whose figures were read from its logs", as reviewer A said. At 7639bec, :98 says it "comes from local runs of the `run` job at seed 0, not from CI", and :102 records the correction ("this line first credited CI run 1").

## 2. Execution map

This is one EMERGENCE run from world generation to the score, in ten stages. The directive asks for ten stages without naming them; the split below follows the code, and the boundaries are this report's choice.
- No figure in sections 2 and 3 comes from the simulator. `discovery()` also computes the simulator's two columns (harness.py:1326); they are not used here.
- Four saved scripts were re-run and reproduced their outputs byte for byte: `S/rc/B/cross_seed.py`, `S/rc/B/r2/compare_B_vs_D_seeds.py`, `S/rc/C/r2/scripts/seed0_miss_decomposition.py` and `S/rc/A/scripts/report_fabric_vs_hq.py` (outputs in `W/path/`). The repository stayed clean.
- The read-only helpers `W/path/reconcile_seed0.py`, `per_seed.py`, `decoy_stops.py` and `W/rev/decoy_stage8.py` write the matching `.out` files.

### 2.1 Runs behind the map

| Run | Commit | What ran | Tag | Scored result |
|---|---|---|---|---|
| Baseline, seed 0 | ec72e22 | `python3 -m research.e2e_ingest run --size 200 --mode process`, all drills; nats-server 2.10.22 (S/baseline_seed0.log:11) | [real] | sha256 159cc6eb… (:35); found 13 (W/path/per_seed.out:1) |
| D's R0, seed 0 | c096aec | the same command, plus D's lag hook | [real] | sha256 c59523bd… (S/rc/D/run_seed0.log:36); every decision row equals the baseline (S/rc/D/out/compare_baseline_vs_instrumented_seed0.json) |
| A's, B's and C's seed-0 runs; D's reduced seed-0 control | c096aec, d481cc4, d481cc4, c096aec | the same command (D's control reduced) | [real] | All 7 compared fields equal the baseline (S/rc/A/scripts/compare_runs.baseline_vs_A_seed0.out:2-8; S/rc/A/round2/compare_runs.baseline_vs_rc_{B,C,D}_run_seed0.out:2-8; compare_runs.baseline_vs_D_seeds_seed0.out:2-8). The fields: recall_all, the found set, decoys, 55 fabric statuses, 55 routes, 165 verdict hashes, 55 conclusion bodies |
| D's seeds 1-5 | c096aec (1-2), d481cc4 (3-5) | the shipped `E2ERun` through `S/rc/D/scripts/run_seed_api.py`; `check_world_counts` replaced, in D's process only, by a recorder; `skip_drills=True`, `queries=0` | [real], reduced | W/path/per_seed.out:3-7 |
| Round-1 seeds 1-5 | ec72e22 | `S/run_seed.py` (the same in-memory bypass), `--skip-drills` | [real], reduced | All 7 fields equal D's at every seed (S/rc/A/round2/compare_runs.dev_seed{1..5}_vs_D_seed{1..5}.out:2-8) |
| B's harness, seeds 0-5 | d481cc4 | `S/rc/B/seed_harness.py`: generate, pipeline, detect, top 60, in-process sites, HQ and gate | [harness] | Found sets and decoys by class equal D's at all six seeds (S/rc/B/r2/compare_B_vs_D_seeds.txt:1-6) |

The reduced runs skip the reference pass, the restart witness, crossing scans, the broker kill, forgeries, the service rebuild, re-ingest, the bridge re-run and the fixed-query rankings (run_seed_api.py docstring). None is an input to `discovery()` (harness.py:1379-1418).

### 2.2 The ten stages at a glance

Counts are seed 0, [real] unless marked; bracketed totals sum seeds 0 to 5. "Gold alive" counts the patterns the path can still recover. "Decoys alive" counts the decoys still alive on their facet-0 (anchor, chain) scoring key. Sources: patterns `S/rc/D/out/tables/gold_present_after_stage_by_seed.txt:3-20`; decoys `decoys_after_stage_by_seed.txt:3-9`, and for stage 8 `W/rev/decoy_stage8.out:1, :7`; flows `stage_flow_by_seed.txt:3-8` and `S/rc/D/out/summary_numbers.txt`.

| # | Stage | Code path | Executed? | In → out (seed 0) | Gold alive: s0 (s0-5) | Decoys alive: s0 (s0-5) |
|---|---|---|---|---|---|---|
| 1 | World generation, record mapping | `harness.py:376-416` → `emergence_world.py:326-368` | yes | seed → 12,964 records, 40 Gold, 50 decoys | 40 (240) | 50 (300) |
| 2 | Site ingest, lexical extraction | `mycelic/collective/evaluate/baselines.py:133-139`; `edge/extract.py`; `emergence_world.py:125-136` | yes | 12,964 records → 4,988 with a claim, 6,113 claims | 40 (240) | at least 49 (297); see 2.3 |
| 3 | Weekly cells (k = 3) through each Boundary; HQ ingest | `baselines.py:140-146`; `edge/site.py:131, :408`; `detect/store.py:501` | yes; no level above sites | 182 bundles, 3,883 cells (360 with n ≥ 3) | 40 (240); 24 (149) with a cell n ≥ 3 | 49 (297) |
| 4 | Detection: D2/D3 with warm-up, rule layer, one snapshot per key | `emergence_world.py:355`; `detectors.py:281-283, :550-584, :631-647` | yes | 539 candidate keys, 55 with a snapshot | 15 (102) | 18 (125) |
| 5 | Candidate list: snapshot only, top_n 60 | `emergence_world.py:84, :285-291`; `harness.py:387-393` | yes; rule-only rows dropped | 539 → 55 asked | 15 (98) | 18 (125) |
| 6 | HQ planning and routing (arm R, m = 3) | `harness.py:489-504`; `routing_spike/hq.py:180-195, :398-404, :414-421`; `hq_tesseract.py:43, :58-71`; `NeuralGraph/research/coordination/core.py:405` | yes; pushdown Orchestrator bypassed | 55 questions, 165 routes | 15 (98) | 18 (125) |
| 7 | Questioning over the wire | `harness.py:509-519`; `hq.py:283-292`; `core.py:409-410`; `wire.py:226-238` | yes; one round | 165 verdicts | 15 (98) | 18 (125) |
| 8 | Site retrieval and verification | `site_graph.py:159-168`; `site_process.py:257-296, :352-369`; `edge/verify.py:243-261` | yes; graph and Tesseract change no verdict | 148 confirm, 15 refute, 2 unknown | 15 (98) with ≥ 1 routed confirm; 13 (93) with ≥ 2 confirms at support ≥ 3 | 18 (125) with ≥ 1 routed confirm; 18 (123) with ≥ 2 confirms at support ≥ k |
| 9 | HQ gate and store | `hq.py:431-435`; `pushdown/gate.py:225-271`; `hq_tesseract.py:165-203` | yes | 47 supported, 7 hypothesis, 1 stale | 13 (93) | 17 (121) |
| 10 | Fabric publication, lineage, scoring | `harness.py:755, :969-1062, :1250-1350`; `hq_bridge.py:232, :317`; `world.py:136-145` | yes | 220 events → 55 notes → 13 found | 13 (93) | 17 (118) accepted |

### 2.3 Stage by stage

Seed-0 sources unless named: `S/rc/D/out/summary_numbers.txt`, `S/rc/D/out/tables/*`, `S/rc/A/scripts/*.baseline_seed0.out`, `S/rc/B/*_run0.txt`.

1. **World generation** [code][real].
   - The frozen generator builds the org, the corpus and Gold (`emergence_world.py:233-237`), with one record per corpus row (`:191-195`). Only the anchor's canonical id is structured (`:179`); `origin_ref` is None (`:183`).
   - Out: 309 users, 7 sites, 35 teams, 12,964 records, 40 Gold (18 rare), 50 decoys, 150 entities (summary_numbers.txt:2).
   - `check_world_counts` (`:223-230`, `:337`) refuses any world unlike seed 0's `WORLD_TABLE` row (`:94-101`). So seeds 1-5 ran with it bypassed in-process (C1, D-F15: confirmed).
   - Lost: nothing.
2. **Site ingest and lexical extraction** [code][real].
   - Per-site ingest (`baselines.py:133-139`): 2,574 / 1,620 / 1,789 / 1,876 / 1,805 / 1,842 / 1,458 records, `forwarded_in` 0 (pipeline_transport_check.baseline_seed0.out:3-9).
   - Every chain facet maps to one chain predicate (`emergence_world.py:125-136`). So a pattern's facets share one (anchor, chain) key, and their order is lost. The lexicon is the generator's own phrases (`emergence_pack.py:102-104`; C9).
   - Defects: the 8-token negation window stores 135 of 196 negated rows as affirmed (70 class-5 retraction rows, 8 real-pattern rows, 57 background; negation_probe_seed0.txt:3-9; B5). And 1,125 of 6,113 claims attach the predicate to the aux entity (aux_claims_run0.txt:1; B12).
   - Out: 4,988 records with a claim, 6,113 claims.
   - Lost: no pattern; all 670 positive Gold facet rows give the claim (:10).
   - Decoys: at least 49 (297) have a claim on their scoring key after this stage. Stage 3's cells count only stored claims of the site's own records (`mycelic/collective/edge/records.py:452-453`), and 49 (297) decoys have a cell there (`decoys_after_stage_by_seed.txt:4`). The exact count is not established. d080 is the only seed-0 decoy with no HQ cell on its scoring key (stage 3). Whether it lost a claim here, or its records never yield a claim on that key, is not established.
3. **Weekly cells and HQ ingest** [code][real].
   - Cells are built per (entity, predicate) series; values below k = 3 become `'<k'` (`site.py:131`; `egress.json:1`). HQ ingests one store (`baselines.py:143-146`).
   - Out: 182 bundles and 3,883 cells at egress, receive log and store, with 0 rejections (pipeline_transport_check.baseline_seed0.out:1); 360 cells with n ≥ 3.
   - All 40 patterns have cells at 2 or more sites; 24 have a cell with n ≥ 3; 16 of the 18 rare patterns have none (summary_numbers.txt:4).
   - Nothing is dropped here: the k floor bites at stages 8 and 9. **There is no aggregation above sites** (D-F11, confirmed; stage_existence.txt).
   - Decoys: 49 of 50 keep a cell; d080 has none (W/path/decoy_stops.out:28).
4. **Detection** [code][real].
   - Detection runs once, at the world's close (`emergence_world.py:355`).
   - **Warm-up** (B1, D-F2: confirmed): eligibility starts at start + 6 + 6 − 1 weeks (`detectors.py:281-283`; `emergence_pack.py:72-73`). So the first eligible week is 2024-W12 at every site and seed. W12 limits only this detector-driven path (`egress.py:553-556`; `world.py:136-141`).
   - D2 and D3 run per week. The rule layer runs from W01 on lower-bound counts (`detectors.py:550-584`). Each key gets one snapshot, the first alert's (`:631-647`).
   - Out: 539 candidate keys; 55 with a snapshot, 484 rule-only. The rules fire on all 40 Gold keys (rule_census_run0.txt:3-4); 15 get a snapshot.
   - **25 patterns are lost here:** p000 p002 p003 p004 p005 p007 p008 p009 p010 p012 p013 p014 p016 p018 p020 p021 p023 p026 p027 p029 p031 p032 p035 p038 p039 (W/path/reconcile_seed0.out:1-40).
   - Decoys: 48 have a candidate and 18 a snapshot; d055 has no candidate (decoy_stops.out:29).
5. **Candidate list** [code][real].
   - Only snapshot candidates are kept, sorted by score and then by salted hash; the first 60 are asked (`emergence_world.py:84, :285-291`).
   - Discarded: 484 rule-only rows (403 background, 62 decoy, and 19 true at their first week; rule_census_run0.txt:2). This is documented at `E2E-INGEST.md:287`.
   - The top_n cut is 0 at seeds 0, 1 and 3, and 2 at seeds 2, 4 and 5. True patterns cut: s4 p023 at rank 61 on a score tie, and s5 p007 at rank 62 (candidates_seed4.txt:63; candidates_seed5.txt:64; D-F12).
   - Seed 0: 55 asked: 15 true, 35 decoy, 5 background.
6. **HQ planning and routing** [code][real].
   - Each question covers the 6-week window ending at the snapshot week and is dated at its as_of (`hq.py:180-187`). Sites are ordered by arm R and cut to m = 3 (`hq.py:414-421`; `core.py:405`).
   - **The shipped pushdown `Orchestrator` is bypassed:** it is not imported anywhere in the e2e path (`orchestrator.py:150`; `hq_tesseract.py:167`; D-F11, C10: confirmed).
   - Out: 55 questions, 165 routes. Loss: none.
   - [real] 8 of 55 routes omit a strong site, none decisively (routing_check_run0.txt:9). [bound] Asking all sites changes no status at any seed (summary_numbers.txt:20, :43, :66, :89, :112, :135).
7. **Questioning over the wire** [code][real].
   - There is one `asyncio.run` per question (`harness.py:509-512`), and sites are awaited in turn (`core.py:409-410`). Each request is a blocking pipe read with no timeout, and stderr goes to /dev/null (`hq.py:283-292`; `wire.py:223-238`; A8, never triggered).
   - **There is no follow-up or recursive question** (`emergence_pack.py:83`; B4, D-F11, C10).
   - Out: 165 verdicts: 148 confirm, 15 refute, 2 unknown, 0 errors, 0 budget refusals (report_fabric_vs_hq.baseline_seed0.out:9).
8. **Site retrieval and verification** [code][real][harness].
   - A site reads the union of Tesseract's in-window ranking and the shipped entity retrieval, capped at L = 50 (`site_process.py:257-296`). The lexical judge says yes on stored claims (`verify.py:170-177`); one yes/yes record confirms (`:253-254`).
   - Site graphs: 12,964 nodes and 0 edges at seed 0 (graph_census_run0.txt:1-7); 0 edges at every seed (stage_flow_by_seed.txt:3-8).
   - [harness] Without Tesseract, 165 of 165 verdicts are identical (replay_shipped_run0.txt:1; tesseract_counterfactual.out:3-6).
   - Truncated pairs: 0/0/1/2/1/0, all on confirming decoy or background candidates. Truncation cannot create a confirm.
   - Gold: 15 have a routed confirm, and 13 have ≥ 2 confirms at support ≥ 3. **p028 and p030 are lost here.** Each has one routed confirm at support ≥ k. p028 also has r01 at `'<k'` and a sibling refute at r00. p030 has r05 and r00 at `'<k'` (patterns_seed0.txt:31, :33). D's first-failing-stage label for both is "verification (<2 counted confirms among routed sites)" (first_failing_stage_by_seed.txt:7; W/path/reconcile_seed0.out:29, :31).
   - Decoys [real]: all 18 decoys asked have a routed confirm and ≥ 2 confirms at support ≥ k. Over six seeds: 125 and 123 of 125 (W/rev/decoy_stage8.out:1, :7).
9. **HQ gate** [code][real].
   - A `'<k'` confirm is not counted (`gate.py:229`). The gate needs ≥ 2 counted sites, roots and reporters ≥ 3, and freshness within 42 days, and it marks a conclusion contested on a contributing refute (`gate.py:241-271`).
   - Out: 47 supported, 7 hypothesis, 1 stale.
   - p028 and p030 end `hypothesis`, because each has one counted site (stage 8). No pattern with ≥ 2 confirms at support ≥ k is lost here (gold_present_after_stage_by_seed.txt:17-18).
   - Decoys: 17 supported; d089 stale.
10. **Fabric, lineage, scoring** [code][real].
    - The bridge and the publish API carry conclusions into the service over NATS JetStream (`hq_bridge.py:232, :317`; `harness.py:755`). Drill 5 sets `lineage_ok` (`harness.py:1044-1047`). `discovery()` reads the fabric's notes (`:1241-1248`).
    - Out: 220 events created, 0 duplicates; 55 notes. The fabric equals HQ, and `lineage_ok` holds, for 55 of 55. Under the broker kill, 275 of 275 events were applied exactly once (report_fabric_vs_hq.baseline_seed0.out:4-11). The same holds at seeds 1-5.
    - Score: 13 found. Accepted decoys: 17 by the frozen rule, 28 by the in-window re-score (decoy_window_check.out:2). A9 (confirmed): the score cannot see the asynchronous half.
    - Lost: nothing.

**Seed-0 decoys by stage** [real] (W/path/decoy_stops.out:28-38; decoys_after_stage_by_seed.txt:3-9):
- Stage 1: d040 to d089 (50).
- Stage 3: 49 keep a cell on the scoring key; d080 does not.
- Stage 4: 48 have a candidate; d055 has none. 18 have a snapshot:
  - class 2: d051 d056 d061 d076 d086;
  - class 4: d043 d053 d058 d063 d068 d073 d078 d083 d088;
  - class 5: d059 d074 d084 d089.
- Stages 5 to 8: the same 18 are asked. Each has a routed confirm and ≥ 2 confirms at support ≥ k.
- Stage 9: 17 supported; d089 stale.
- Stage 10: those 17 are accepted under the frozen rule.

### 2.4 Bypassed, absent and inert parts

| Component | In the end-to-end path | Evidence | Status |
|---|---|---|---|
| Shipped pushdown `Orchestrator` | bypassed: not imported | `orchestrator.py:150, :261`; `hq_tesseract.py:167` | confirmed |
| Aggregation above sites | does not exist | `baselines.py:143-146`; `stage_existence.txt` | confirmed |
| Recursive or follow-up questioning; re-asking a `hypothesis`; later re-verification | does not exist | `harness.py:509-512`; `emergence_pack.py:83`; `summary_numbers.txt:16` | confirmed |
| Graph relationships, entity links, hypergraph | absent (0 edges at every seed) | `stage_flow_by_seed.txt:3-8`; `graph_census_run0.txt:1-7` | confirmed |
| NeuralGraph memory and Tesseract ranking | run, change no verdict | `replay_shipped_run0.txt:1`; `tesseract_counterfactual.out:3-6` | confirmed at seed 0 |
| Rule layer | runs; its output is dropped unless the key also has a snapshot | `emergence_world.py:289`; `rule_census_run0.txt:1-2` | confirmed |
| Reference pass, fixed-query rankings, drills 3-7 | full runs only; not inputs to `discovery()` | `harness.py:425-452, :1384, :1391-1418` | confirmed (code) |
| Simulator columns | computed inside `discovery()`; not used here | `harness.py:1326` | confirmed (code) |

## 3. Per-stage loss

### 3.1 Results per seed

| seed | Gold common / rare | found [real] | recall_all [real] | common found | rare found | true asked [real] | found [harness] |
|---|---|---:|---:|---:|---:|---:|---:|
| 0 | 22 / 18 | 13 | 0.325 | 13 | 0 | 15 | 13 |
| 1 | 19 / 21 | 12 | 0.300 | 11 | 1 | 13 | 12 |
| 2 | 26 / 14 | 17 | 0.425 | 16 | 1 | 19 | 17 |
| 3 | 29 / 11 | 17 | 0.425 | 16 | 1 | 17 | 17 |
| 4 | 21 / 19 | 19 | 0.475 | 17 | 2 | 19 | 19 |
| 5 | 20 / 20 | 15 | 0.375 | 15 | 0 | 15 | 15 |
| all | 137 / 103 | 93 | 93/240 = 0.3875 | 88 | 5 | 98 | 93 |

Sources: `W/path/per_seed.out:2-8` (D's runs, as `discovery()` wrote them) and `:9-14` (B's harness); true asked from `gold_present_after_stage_by_seed.txt:14`. D's real runs, the round-1 real runs and B's harness agree at every seed (2.1). Seeds 1-5 are reduced runs with the guard bypassed in-process; the shipped `run` command refuses them. No figure here is a CI figure.

### 3.2 Gold patterns still recoverable after each stage, seeds 0-5 [real]

Source: `gold_present_after_stage_by_seed.txt:3-20`.

| After stage | s0 | s1 | s2 | s3 | s4 | s5 | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1. generated (Gold-discoverable) | 40 | 40 | 40 | 40 | 40 | 40 | 240 |
| 2. every facet ingested; a claim on the key | 40 | 40 | 40 | 40 | 40 | 40 | 240 |
| 3. HQ cells on the key at 2 or more sites | 40 | 40 | 40 | 40 | 40 | 40 | 240 |
| 3. an HQ cell on the key with n ≥ 3 | 24 | 20 | 29 | 30 | 25 | 21 | 149 |
| 4. a rule hit on the key | 40 | 40 | 40 | 40 | 40 | 40 | 240 |
| 4. a detector snapshot | 15 | 13 | 19 | 19 | 20 | 16 | 102 |
| 4. snapshot week inside the label window | 15 | 13 | 19 | 17 | 20 | 16 | 100 |
| 5. in the first 60, so asked | 15 | 13 | 19 | 17 | 19 | 15 | 98 |
| 8. at least one routed confirm | 15 | 13 | 19 | 17 | 19 | 15 | 98 |
| 8. at least 2 confirms with support ≥ 3 | 13 | 12 | 17 | 17 | 19 | 15 | 93 |
| 9. gate `supported` | 13 | 12 | 17 | 17 | 19 | 15 | 93 |
| 10. published with `lineage_ok`; found | 13 | 12 | 17 | 17 | 19 | 15 | 93 |

Observed loss over seeds 0-5, 147 misses (`S/rc/D/out/tables/first_failing_stage_by_seed.txt:3-7`):
- Stage 4: 140. Of these, 138 never had a snapshot, and 2 had one outside the label window (seed 3 p018 and p026, `patterns_seed3.txt:21, :29`).
- Stage 5: 2.
- Stage 8: 5. Fewer than 2 routed sites confirmed with support ≥ k; D's label is "verification". The gate then leaves 4 of them `hypothesis`; s2 p022 ends `stale`, because freshness is checked before the two-site minimum (`S/r2/decomp_r2.out:80-84`; `patterns_seed2.txt:25`; `gate.py:256`, `:263`).
- Other stages: none.

### 3.3 Three decompositions of the misses

The three answer different questions, so their counts differ by definition, not by data.

**(a) Observed first failing stage** [real] (`first_failing_stage_by_seed.txt:3-7`).

| First failing stage | s0 | s1 | s2 | s3 | s4 | s5 | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| 4: no detector snapshot (rule hit only) | 25 | 27 | 21 | 21 | 20 | 24 | 138 |
| 4: snapshot week outside the label window | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| 5: cut by top_n | 0 | 0 | 0 | 0 | 1 | 1 | 2 |
| 8: fewer than 2 counted confirms among routed sites (D: "verification") | 2 | 1 | 2 | 0 | 0 | 0 | 5 |
| found | 13 | 12 | 17 | 17 | 19 | 15 | 93 |

**(b) The diagnosis's ordered taxonomy** [real] (`S/r2/decomp_r2.out:72-78`; cells are common / rare). S1 means no week at or after W12 lies inside the label window. It equals C's early-window census: 35 in all, 13 of them rare (`S/rc/C/r2/logs/early_window_census.out:1-7`). The diagnosis names its last class "S4 gate"; its five patterns are the five of row 8 in (a).

| seed | found | S1 cold start | S2 no snapshot | S3 top_n | S4 gate |
|---|---|---|---|---|---|
| 0 | 13 / 0 | 3 / 3 | 5 / 14 | 0 / 0 | 1 / 1 |
| 1 | 11 / 1 | 4 / 3 | 4 / 16 | 0 / 0 | 0 / 1 |
| 2 | 16 / 1 | 4 / 1 | 6 / 10 | 0 / 0 | 0 / 2 |
| 3 | 16 / 1 | 6 / 2 | 7 / 8 | 0 / 0 | 0 / 0 |
| 4 | 17 / 2 | 1 / 1 | 3 / 15 | 0 / 1 | 0 / 0 |
| 5 | 15 / 0 | 4 / 3 | 1 / 16 | 0 / 1 | 0 / 0 |
| all | 88 / 5 | 22 / 13 | 26 / 79 | 0 / 2 | 1 / 4 |

**(c) Causal: the earliest stage at which recovery became impossible under the shipped rules** [harness][bound]. These are reviewer B's classes, counting each pattern's own facet records (`S/rc/B/cross_seed_0_5.txt:1, :4, :7, :10, :13, :16, :19`):
- K_site: no site ever holds 3 own facet records.
- K_2site: only one site does.
- K_window: two sites do, but never in one 6-week window.
- WARMUP: gate-feasible only before W12.
- DETECT: feasible at W12 or later, but no snapshot.
- QUESTION: asked too early.

| seed | K_site | K_2site | K_window | WARMUP | DETECT | QUESTION | found |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | 10 | 4 | 4 | 5 | 3 | 1 | 13 |
| 1 | 11 | 4 | 3 | 9 | 1 | 0 | 12 |
| 2 | 9 | 3 | 0 | 8 | 3 | 0 | 17 |
| 3 | 7 | 2 | 1 | 11 | 2 | 0 | 17 |
| 4 | 7 | 9 | 1 | 3 | 1 | 0 | 19 |
| 5 | 13 | 5 | 0 | 7 | 0 | 0 | 15 |
| all | 57 | 27 | 9 | 43 (39 common, 4 rare) | 10 (9 common, 1 rare) | 1 | 93 |

All 93 K_* patterns are rare.

**Model 1 bounds under the two counting definitions** [bound]. Perfect detection; all 7 sites asked; re-questioning under the shipped gate (k = 3, 2 sites, roots and reporters ≥ 3, 42-day freshness); the shipped window at each candidate snapshot week from W06 inside the label window.

| Counting | Any snapshot week | Snapshot weeks ≥ W12 | Source |
|---|---|---|---|
| Every stored claim on the key (what cells, gate and `found` count) | 23 / 23 / 28 / 31 / 25 / 22 = 152 of 240 | 18 / 13 / 20 / 19 / 22 / 17 = 109 of 240 | `summary_numbers.txt:12, :35, :58, :81, :104, :127` |
| The pattern's own facet records (B) | 22 / 23 / 27 / 30 / 22 / 22 = 146 of 240 | 17 / 13 / 19 / 19 / 19 / 15 = 102 of 240 | `S/rc/B/r2/capacity_split_0_5.txt:3-47`; `W/path/capacity_sums.out:25-28` |

- Rare patterns supportable at any week: 15 of 103 counting every claim, 9 of 103 counting own records (`W/path/capacity_sums.out:1-26`).
- D's claim-derived support equals the real confirm counts on every routed true-candidate site: 15/15, 13/13, 19/19, 19/19, 19/19, 15/15 (same `summary_numbers.txt` lines).
- The "≥ W12" columns bound only the detector-driven path. All of these are bounds on six development seeds, not outcomes.

### 3.4 Seed 0: every pattern

Columns:
- real run [real] (`S/rc/D/out/tables/patterns_seed0.txt:3-42`);
- first loss (`S/r2/decomp_r2.out:2-41`);
- B (own facets);
- C / D (every claim) (`S/rc/C/r2/logs/seed0_miss_decomposition.out:1-7`), with D's bound given as any week / weeks ≥ W12.

The join and the totals are in `W/path/reconcile_seed0.out:1-65`. The notes cite `S/rc/B/baseline_contamination_run0.txt` and `S/rc/B/cf_tesseract/results.json` ([harness]).

| id | C/R | label window | real run | first loss | B (own) | C / D (every claim) | note |
|---|---|---|---|---|---|---|---|
| p000 | C | W05-W17 | no snapshot | S2 | DETECT | silent, supportable ≥ W12 (y/y) | D2 baseline = own W05-W06 facets at r02, r03 (5 = 5; `:2-3`); [harness] W12 supported |
| p001 | C | W07-W16 | found, W12 | FOUND | FOUND | found | |
| p002 | R | W06-W14 | no snapshot | S2 | K_2site | not supportable (n/n) | |
| p003 | R | W13-W21 | no snapshot | S2 | K_site | n/n | |
| p004 | R | W13-W21 | no snapshot | S2 | K_site | n/n | |
| p005 | R | W10-W21 | no snapshot | S2 | K_window | silent, supportable ≥ W12 (y/y) | counting definitions disagree (3.5) |
| p006 | C | W11-W21 | found, W12 | FOUND | FOUND | found | |
| p007 | R | W06-W14 | no snapshot | S2 | K_site | n/n | |
| p008 | R | W12-W20 | no snapshot | S2 | K_2site | n/n | |
| p009 | C | W05-W19 | no snapshot | S2 | WARMUP | only before W12 (y/n) | [harness] W12 `stale` |
| p010 | R | W07-W18 | no snapshot | S2 | K_window | n/n | |
| p011 | C | W07-W15 | found, W12 | FOUND | FOUND | found | |
| p012 | C | W05-W12 | no snapshot | S2 | WARMUP | only before W12 (y/n) | [harness] W08 supported |
| p013 | C | W02-W09 | no snapshot | S1 | WARMUP | only before W12 (y/n) | [harness] W04 supported |
| p014 | C | W01-W09 | no snapshot | S1 | WARMUP | only before W12 (y/n) | [harness] W02 supported |
| p015 | C | W07-W21 | found, W12 | FOUND | FOUND | found | |
| p016 | R | W02-W09 | no snapshot | S1 | K_site | n/n | |
| p017 | C | W08-W18 | found, W12 | FOUND | FOUND | found | |
| p018 | R | W04-W11 | no snapshot | S1 | K_site | n/n | |
| p019 | C | W07-W13 | found, W12 | FOUND | FOUND | found | |
| p020 | R | W06-W17 | no snapshot | S2 | K_window | n/n | |
| p021 | R | W11-W18 | no snapshot | S2 | K_site | n/n | |
| p022 | C | W06-W15 | found, W13 | FOUND | FOUND | found | |
| p023 | C | W06-W13 | no snapshot | S2 | DETECT | silent, supportable ≥ W12 (y/y) | D2 baseline = own facets at r03 (8 = 8; `:5`); [harness] W12 supported |
| p024 | C | W07-W15 | found, W12 | FOUND | FOUND | found | |
| p025 | C | W13-W21 | found, W16 | FOUND | FOUND | found | |
| p026 | R | W02-W14 | no snapshot | S2 | K_site | n/n | |
| p027 | R | W04-W14 | no snapshot | S2 | K_window | n/n | |
| p028 | C | W13-W23 | asked W13, `hypothesis` | S4 | QUESTION | asked; supportable later (y/y) | one question at the first alert (window W08-W13); one counted confirm (stage 8); [harness] W13-W14 hypothesis, W15-W18 supported |
| p029 | R | W02-W13 | no snapshot | S2 | K_2site | n/n | |
| p030 | R | W05-W12 | asked W12, `hypothesis` | S4 | K_site | asked; n/n | one counted confirm (stage 8); confirms at r05 and r04 came from records not its own; r00 held 2 own records (`S/rc/B/pattern_probe_run0.txt:152-155`) |
| p031 | R | W06-W15 | no snapshot | S2 | K_site | n/n | |
| p032 | C | W04-W13 | no snapshot | S2 | DETECT | silent, supportable ≥ W12 (y/y) | D2 baseline = own facets at r00 (9 = 9) and r04 (8 = 8) (`:8-9`); [harness] W12 supported |
| p033 | C | W13-W22 | found, W14 | FOUND | FOUND | found | |
| p034 | C | W05-W15 | found, W12 | FOUND | FOUND | found | |
| p035 | R | W17-W24 | no snapshot | S2 | K_site | n/n | |
| p036 | C | W13-W23 | found, W16 | FOUND | FOUND | found | |
| p037 | C | W13-W22 | found, W15 | FOUND | FOUND | found | |
| p038 | R | W01-W08 | no snapshot | S1 | K_2site | n/n | |
| p039 | C | W01-W10 | no snapshot | S1 | WARMUP | only before W12 (y/n) | [harness] W06 supported |

Totals at seed 0 (`reconcile_seed0.out:41-65`):
- Real run: 13 found, 25 with no snapshot, 2 asked and not supported.
- B: K_site 10, K_2site 4, K_window 4, WARMUP 5, DETECT 3, QUESTION 1.
- C: 16 never asked and not supportable at any week; 5 supportable only before W12; 4 supportable at W12 or later with the detector silent; 1 asked and supportable later; 1 asked and not supportable.
- D: 23 supportable at any week, 18 at weeks ≥ W12.
- All 13 seed-0 finds rest on the patterns' own records (`S/rc/B/r2/found_dependence_0_5.txt:14`).

### 3.5 Where the decompositions differ, and why

1. **Observed against causal.** The real run loses 25 of the 27 seed-0 misses at the snapshot-only candidate list (stages 4 and 5): that is where the loss shows. Of the 25:
   - 16 are rare patterns that no week could support counting every claim (C's 16). Counting own facets, B's K_* classes cover 17: these 16 plus p005 (`reconcile_seed0.out:1-40`).
   - 5 are supportable only before W12: p009, p012, p013, p014, p039.
   - 4 are supportable at W12 or later with the detector silent: p000, p023, p032 and, counting every claim, p005. B counts 3 (DETECT).
   - So 16 + 5 + 4 = 25 (C), and 17 + 5 + 3 = 25 (B).
   - Status: the counts are confirmed. B answered "partly" and calls the rule-only drop a symptom, not the root cause. A and C agreed with D-F1; C's note concerns only its kind (a system design choice rather than a benchmark design choice). D-F1's root-cause reading, contested by B, is probable.
2. **Own facet records against every claim: p005 is the only seed-0 disagreement.**
   - B puts p005 in K_window; D's bound and C find it supportable.
   - [harness] C's in-process question returns `supported` at W10 to W13 (`S/rc/C/r2/logs/cf_seed0.out:2-5`). The counted support is not its own: r00 holds 3 decoy and 2 own records, and r03 holds 3 background records (`capacity_split_0_5.txt:1`; `S/rc/C/r2/logs/cf_confirm_roles_seed0.out:2-4, :13-16`).
   - This one pattern makes the seed-0 bound 22 counting own records and 23 counting every claim. Over six seeds the bounds are 146 against 152 at any week, and 102 against 109 at weeks ≥ W12 (`W/path/capacity_sums.out:1-2, :25-28`). Every-claim counting matches what cells, the gate and `found` count (`world.py:136-141`).
   - The same split explains seed-4 p023. B classes it K_site, but [harness] C's question at W17 returns `supported` (`S/rc/C/r2/logs/cf_seed4.out:2`) on aux-attached background claims at both counted sites (`capacity_split_0_5.txt:33`).
   - So for these two patterns, B's "not supportable" is confirmed wrong when every claim counts, in the harness only. Neither result is confirmed in process mode.
3. **Three "warm-up" sets at seed 0.**
   - D's detector reason, "all facets before the first detectable window": p013, p014, p016, p038, p039 (`summary_numbers.txt:21`).
   - S1 and C's early-window census, "label window ends before W12": p013, p014, p016, p018, p038, p039 (`early_window_census.out:1`).
   - B's WARMUP: p009, p012, p013, p014, p039. It equals D's "supportable only before W12" set at seeds 0, 2 and 4, and both total 43 (`S/rc/B/r2/warmup_sets.txt:1-7`).
   - S1 and WARMUP differ at seed 0, even in size (6 against 5). S1 includes rare patterns that no week could support (p016, p018, p038); B classes those K_*. WARMUP includes p009 and p012: their label windows reach W19 and W12, but their evidence is supportable only before W12 ([harness] p009 at W12 is `stale`).
   - Over six seeds, S1 is 35 and WARMUP 43. D-F2's seed-0 list, read as "the set the warm-up costs", is confirmed wrong (B).
4. **The detector-silent group** (B's DETECT, 3; C's group, 4 with p005).
   - Mechanism at seed 0: the pack folds a chain's facets into one predicate, so a strong site's D2 baseline holds the pattern's own earlier facets.
     - [real] B's replay of the shipped `_walk` matches the stored run with 0 mismatches (`S/rc/B/detect_missed_run0.txt:1`).
     - C's arithmetic with the shipped Poisson tail: with the own facets in the baseline, no site exceeds the threshold; without them, every listed site does (`S/rc/C/r2/logs/d2_arith.out:1-5`).
   - Status: mechanism confirmed at seed 0 (B2). B calls it a separate defect. C calls it the warm-up's root, since these patterns start inside the 11 blind weeks. As a separate cause it is probable.
   - Over seeds 0 to 5, 9 of the 10 DETECT misses have a strong site whose baseline holds own earlier facets (6 self-baseline, 3 mixed). The tenth, seed-2 p012, has no self-baseline site: at its one eligible week only one site exceeded the threshold (m = 1) (`S/rc/B/r2/detect_subcause.txt:11-12, :28`). Why no second site alerted is not established.
   - Seeds 0 to 5 hold 3, 1, 3, 2, 1 and 0 of the ten (`cross_seed_0_5.txt:1-16`; `detect_subcause.txt:1-27`). For seeds 1 to 5 this is a replay with no counterfactual detector run: probable.
5. **The two seed-0 losses after asking.**
   - p028 is a single-question timing loss by every reading. It was asked once, at the first alert (window W08-W13), when only r04 confirmed with support ≥ k (`decomp_r2.out:62`; patterns_seed0.txt:31). Nothing asks again (B4, D-F7: confirmed). The later-week [harness] results are in 3.4.
   - p030 is rare. It was asked because D2 fired on records that are not its own, and no counting definition can support it (`pattern_probe_run0.txt:152-155`).
   - Both fail at stage 8 with one counted confirm, and the gate (stage 9) leaves them `hypothesis`.
6. **Rare patterns.** Of the 18 at seed 0, 17 never got a snapshot, and 1 (p030) had one counted confirm. None was found.
   - Under both definitions, the shipped gate could support at most 1 at any week: p005, counting every claim (`W/path/capacity_sums.out:1-2`).
   - The binding checks are: a `'<k'` confirm is not counted (`gate.py:229`); the two-site minimum (`gate.py:263`); and the 6-week window.
   - B3, D-F3: mechanism confirmed. B's classes are probable; for s0 p005 and s4 p023 they are confirmed wrong in the harness (item 2).

### 3.6 Seeds 0-5 in short
- 147 misses: 138 never got a snapshot, 2 had one outside the label window, 2 were cut by top_n, and 5 were asked with fewer than 2 counted confirms [real] (`first_failing_stage_by_seed.txt:3-7`).
- By cause (B, own facets) [harness][bound]: 93 rare misses limited by k = 3 per site, the two-site gate and the 6-week window (57 / 27 / 9); 43 supportable only before W12; 10 detector-silent at W12 or later; 1 asked too early (`cross_seed_0_5.txt:19`).
- The top_n cut cost at most one supportable pattern, seed-4 p023, in the [harness] only and on support that is not its own. Seed-5 p007 returns `hypothesis` [harness] (`S/rc/B/r2/cut_probe_seed5.txt:3`).
- Seeds 1-5 have 5 rare finds. Three rest on records that are not the pattern's own: s2 p038, s4 p011, s4 p022 (`found_dependence_0_5.txt:44, :71, :76`; summaries :45, :83). "Found" credits the key and week, not the evidence (`world.py:136-141`; C12 mechanism confirmed).

### 3.7 Checks that found no loss

| Hop or component | Check | Result | Tag | Source | Status |
|---|---|---|---|---|---|
| Site egress → HQ receive → HQ store | bundles and cells per hop | 182 / 3,883 at each hop, 0 rejections, in B's, C's and D's seed-0 runs too | [real] | `pipeline_transport_check.baseline_seed0.out:1`; `S/rc/A/round2/pipeline_transport_check.rc_{B,C,D}_run_seed0.out:1` | confirmed |
| HQ ↔ site wire | request outcomes | 175 requests, 0 failures | [real] | `e2e_probe_summary.run_seed0.out:11` | confirmed |
| Verdict integrity | sha256 across six records of each verdict | 165 of 165 equal | [real] | `report_fabric_vs_hq.baseline_seed0.out:12` | confirmed |
| HQ → fabric publish | events created | 220 created, 0 duplicate, 0 ambiguous | [real] | same file `:10` | confirmed |
| Outbox, JetStream, consumer under a broker kill | exactly-once apply | 275 of 275; outbox 138 during the outage | [real] | same file `:11` | confirmed |
| Fabric against HQ | status and lineage per conclusion | 55 of 55 at seed 0; all at seeds 1-5 | [real] | same file `:4-8`; `report_fabric_vs_hq.D_seed{1..5}.out:4-8` | confirmed |
| Site restart (drill 2) | answer matrix after SIGKILL | identical | [real] | same file `:14` | confirmed |
| Repeat runs | scored outputs | six seed-0 runs identical; two run sets per seed for 1-5 identical | [real] | the compare files in 2.1 | confirmed |
| Routing (m = 3) | unrouted strong sites; all-sites what-if | 8 of 55 omit a strong site, none decisively; no status change at any seed | [real] / [bound] | `routing_check_run0.txt:9`; `summary_numbers.txt:20, :43, :66, :89, :112, :135` | confirmed |
| NeuralGraph and Tesseract | replay; counterfactual; read diagnostics | 165 of 165 identical; 0 of 165 differ | [harness] / [real] | `replay_shipped_run0.txt:1`; `tesseract_counterfactual.out:3-6`; `tesseract_diag_run0.txt:1` | confirmed at seed 0 |
| Read cap L = 50 | truncated pairs | 0 at seed 0; 1, 2, 1 at seeds 2-4; cannot create a confirm | [real] / [code] | `truncation_check.out:2-16`; `verify.py:243-261` | confirmed |
| Instrumentation | rows with and without D's lag hook | all equal | [real] | `compare_baseline_vs_instrumented_seed0.json` | confirmed (D-F14) |
| Positive facet extraction | Gold facet rows with polarity +1 | 670 of 670 | [real] | `negation_probe_seed0.txt:10` | confirmed |

Every miss is lost at detection, at the candidate list, or at the count of confirms with support ≥ k. Transport, the fabric and routing change no pattern or decoy decision. Tesseract changes no verdict at seed 0; at seeds 1-5 only confirms were checked.

### 3.8 Not established in sections 2 and 3
- The CI discovery blocks of runs 38425113336, 38639771210 and 38664599267.
- Process-mode confirmation of the [harness] "supported" results: p005 (seed 0) and p023 (seed 4); p000, p023 and p032 at W12; p028 at W15-W18; p012, p013, p014 and p039 before W12.
- Whether the D2 self-baseline is a defect separate from the warm-up (B and C disagree). No counterfactual detector run exists for seeds 1-5.
- The recall effect of the 1,125 aux-entity claims and the 135 misread negations, beyond the cases in 3.5.
- Seeds 1-5 with drills; `recall_all` with the warm-up removed, or with own facets kept out of the D2 baseline.
- Exact decoy counts at stage 2 (2.3 gives a lower bound), including whether d080 lost a claim there.
- Per-site own-facet counts for the rare K_* patterns were not re-read. The class names come from `cross_seed_0_5.txt`, re-run byte-identical.

## 4. Root-cause table

### 4.0 The adversarial review

- **Round 1, independent.** Four reports were written without seeing each other (S/rc/first_A.md to first_D.md; rc_result.json `first`):
  - A, distributed systems: 13 findings;
  - B, retrieval and graph: 13;
  - C, benchmark integrity: 16;
  - D, the instrumented reproducer: 15 (W/rev/challenge_tally.out:1-4).
- **Round 2, cross-challenge.** A, B and C answered the others' findings with agree, partly or cannot tell, and revised their own (S/rc/second_A.md to second_C.md; rc_result.json `second`). D did not challenge.
  - A: 24 challenges on D's, B's and C's findings: 20 agree, 4 partly (D-F9, D-F8, B13, B3) (challenge_tally.out:5-6).
  - B: 30 challenges on D's, C's and A's findings: 19 agree, 11 partly (D-F1, D-F2, D-F3, D-F5, D-F8, D-F9, C4, C9, C12, C15, A12) (:7-8).
  - C: 41 challenges on A's, B's and D's findings: 30 agree, 10 partly (A2, A4, A12, A13, B2, B3, B13, D-F5, D-F8, D-F12), 1 cannot tell (A7) (:9; the names from rc_result.json `second`, C's challenges, because the helper's :10 also lists A7 under partly).
- **Not cross-challenged:** C5 and C14 (no challenger), and A14, B14, C17, C18 (new in round 2) (W/rev/challenge_matrix.out:14, :28, :33, :42, :45, :46).
- Every finding's verdicts are in W/rev/challenge_matrix.out (from rc_result.json), and the challengers' texts are in W/rev/challenge_texts.out.

| Finding (author's status, grade) | Challenger: verdict | What was contested | Status in this report |
|---|---|---|---|
| A2 (probable, medium) | C: partly | "no single blocking operation" not shown for CI; samples taken at load 16-17 | probable |
| A4 (confirmed, low) | C: partly | magnitudes measured at load 17.41 overstate an idle runner | operations confirmed; magnitudes on CI probable |
| A7 (confirmed, low): "The stall code is not on the A003 lab path, and A003's judging run has not started" | C: cannot tell | not examined | First half confirmed by A; the diagnosis re-checked it at a35324e (S/DIAGNOSIS-r2-v2.md §8.1). Second half true when A checked (23:43Z, 2026-10-14) and now superseded: lab run 38667030739 ran 01:41:42 to 02:13:35 UTC (W/rev/ci_runs_now.out:2) |
| A12 (confirmed, low) | B, C: partly | counts agreed; "truncation may have caused acceptance" refuted by code | counts confirmed; causal part confirmed wrong |
| A13 (unverified, low) | C: partly | code path confirmed; cost measured by nobody | unverified |
| B2 (confirmed, high) | A: agree; C: partly | arithmetic agreed; C calls it the warm-up's root, not a separate defect | mechanism confirmed at seed 0; separate cause probable |
| B3 (confirmed, critical) | A, C: partly | mechanism agreed; the classes count own facets; C's harness contradicts s0 p005 and s4 p023 | mechanism confirmed; classes probable; the two classes confirmed wrong when every claim counts [harness] |
| B13 (confirmed, low) | A, C: partly | "routing loses nothing" agreed; "top_n prunes no Gold pattern" refuted | routing part confirmed; top_n part confirmed wrong (B corrected it) |
| C4 (confirmed, high) | A: agree; B: partly | counts agreed; "detection sets the ceiling" misattributed (k, two sites, warm-up and self-baseline lie behind it) | counts confirmed; ceiling reading probable |
| C9 (confirmed, medium) | B: partly | lexicon and structured ids agreed; "class-3 0/10 by construction" refuted, and C retracted it | lexicon and ids confirmed; density figure confirmed by C, not challenged; class-3 claim confirmed wrong |
| C12 (confirmed, medium) | A: agree; B: partly | mechanism agreed and quantified (3 of 93); crediting Gold-contradicted patterns follows the frozen labels | mechanism confirmed; defect framing probable |
| C15 (confirmed, medium) | B: partly | 0.633 reproduced; 0.846 not checked | 0.633 and 40 of 40 confirmed; the single-site figure probable (B did not check it; this report's re-read agrees, 1.2) |
| D-F1 (confirmed, critical) | A, C: agree; B: partly | counts agreed; B: a symptom, not the root cause. C's note concerns kind only | counts confirmed; root-cause reading probable |
| D-F2 (confirmed, high) | C: agree; B: partly | mechanism agreed; the seed-0 list is not the set the warm-up costs | mechanism confirmed; the list, read as the warm-up's cost, confirmed wrong |
| D-F3 (confirmed, high) | C: agree; B: partly | mechanism agreed; the rare count depends on counting (15 every claim, 9 own) | mechanism confirmed; rare count confirmed under each counting definition |
| D-F5 (confirmed, high) | B, C: partly | class-2 half agreed; class-5 half ("no order check") refuted | class-2 half confirmed; class-5 half confirmed wrong |
| D-F8 (confirmed, medium) | A, B, C: partly | verdict-neutral agreed; "Tesseract causes site-side loop blocking" mischaracterised (A, C) | verdict-neutral confirmed at seed 0; the blocking claim as stated confirmed wrong |
| D-F9 (confirmed, low) | C: agree; A, B: partly | location and sequential fan-out agreed; "HQ loop blocked" overstated; "1.7-3.2 s per ranking" refuted (A) | location confirmed; the rest probable as stated; "1.7-3.2 s per ranking" confirmed wrong |
| D-F12 (confirmed, low) | A, B: agree; C: partly | counts agreed; effect not zero: one [harness] find lost at seed 4 | cut confirmed; lost find probable |

Every other finding drew only "agree" and keeps its author's status.

### 4.1 The table

The Sev. column shows each reviewer's grade where the grades differ. Type: defect, design limit or measurement issue. Confidence: the status from 4.0. Every row maps to a rank in 8.1 or to 8.5 ("not proposed").

| ID | Root cause | Sev. | Type | Main evidence | Patterns: seed 0 / seeds 0-5 | Confidence |
|---|---|---|---|---|---|---|
| B1, D-F2, C17 | Detector warm-up: no site eligible before 2024-W12; only snapshot candidates asked | critical (B1, C17); high (D-F2) | design limit | `detectors.py:281-283`; `emergence_pack.py:72-73`; `emergence_world.py:289`; S/rc/C/r2/logs/derived_totals.out:2 | p009 p012 p013 p014 p039 / 43 | confirmed (C17 not cross-challenged) |
| B3, D-F3 | Per-site k = 3, two counted sites and one 6-week window block rare patterns | critical (B3); high (D-F3) | design limit | `gate.py:229`, `:263`; `corpus.py:281`; W/capacity_sums.out:1-2 | 16 rare never asked, plus p030 / 93 of the 98 rare misses (B's K_* classes; the other 5 are WARMUP 4 and DETECT 1, S/rc/B/cross_seed_0_5.txt:19) | mechanism confirmed; B's classes probable |
| B2 | D2 self-baseline: the baseline holds the pattern's own earlier facets | high (B) | design limit | S/rc/B/baseline_contamination_run0.txt:2-9; S/rc/C/r2/logs/d2_arith.out:1-5; S/rc/B/r2/detect_subcause.txt:28 | p000 p023 p032 / 10 detector-silent, 9 with a self-baseline site | confirmed at seed 0; probable at seeds 1-5 and as a separate cause |
| C3 | Accepting every detector candidate finds more than the system, at about the same decoy acceptance | high | measurement issue | S/rc/B/r2/accept_all_check.txt:7-8; S/rc/A/round2/accept_all_check.out:1-2 | p028 p030 / 5 | confirmed |
| B6, D-F4 | Echoes by different users count as independent roots (class 4) | high | defect | `records.py:25-28`, `:378-384`; `corpus.py:227-230`; S/rc/B/dup_census_run0.txt:1 | 9 class-4 decoys / 41 acceptances | confirmed |
| B7, D-F5 (class-2 half) | One predicate per chain and summed roots: order is invisible; deduplication alone leaves most class-4 decoys | high | design limit | `emergence_pack.py:102-110`; `gate.py:241`; `harness.py:1343`; S/rc/B/gate_counterfactual_run0.txt:41-44 | 5 class-2 decoys / 37 acceptances | confirmed; the deduplication figure probable |
| B9, C18, D-F6 | Decoy acceptance counts only each decoy's facet-0 key | high | measurement issue | `harness.py:1296-1317` against `world.py:142-144`; derived_totals.out:1 | 11 not counted / 59 not counted | confirmed (C18 not cross-challenged) |
| C1, D-F15 | `check_world_counts` refuses every seed but 0 | high (C1); low (D-F15) | measurement issue | `emergence_world.py:94-101`, `:223-230`, `:337`; `SPEC.md:70` | none / every held-out world | confirmed |
| C7 | SPEC freezes the scoring, but not the gate thresholds, the pack, `top_n` or the detector settings | high | measurement issue | `SPEC.md:23-29`; `pack/emergence/questions.json`; gate_bound_variants.out:7; one_site_sensitivity.out:8 | all | confirmed; C7's "0.454 to 1.0" overstates (4.2) |
| C5 | The simulator's "found" is a different metric on different worlds | high | measurement issue | S/rc/C/logs/sim_figures.out:3-4; sim_register_depth.out:1-3 | n/a | confirmed; not cross-challenged |
| C9 | Synthetic shortcuts: the pack is the generator's lexicon and chain schema; entity ids arrive structured; evidence is much denser at 200 users | medium (C) | measurement issue | `emergence_pack.py:102`; `research/mycelic/corpus.py:210`; `emergence_world.py:179`; S/rc/C/logs/world_density.out:1-3 | all / all | lexicon and ids confirmed; density confirmed, not challenged; "class-3 0/10 by construction" confirmed wrong |
| D-F1, B11, D-F13, C4 | Rule-only candidates without a snapshot are dropped; this is where most losses show. C4: only 13 to 19 of 40 reach a true candidate per seed | critical (D-F1); high (C4); medium (B11, D-F13) | design limit | `emergence_world.py:289`; first_failing_stage_by_seed.txt:3; rule_only_recovery.txt:7; gold_present_after_stage_by_seed.txt:14 | 25 / 138 | counts confirmed; as root cause probable (B: symptom); C4's "detection sets the ceiling" probable (B: partly) |
| B4, D-F7 | One question per key, at its first alert | medium | defect | `harness.py:509-512`; `detectors.py:633`; cf_seed0.out:1 | p028 / 1 | confirmed |
| A8 | Sites are asked one at a time by a blocking pipe read with no timeout; stderr is discarded | medium | defect | `wire.py:223`, `:236`; `hq.py:290`; `core.py:409-410`; S/rc/A/round2/hq_mainthread_gaps.out:1-2 | none (latency, liveness) | confirmed; never triggered |
| C12, B14 | `found` credits the key and window, not the pattern's evidence | medium | measurement issue | `world.py:136-146`; found_dependence_0_5.txt:44, :71, :76 | none / 3 rare finds | mechanism confirmed; defect framing probable; B14 not cross-challenged |
| D-F11, C10 | No level above sites, no follow-up questions, Orchestrator not imported, no hypergraph code | medium | design limit | `harness.py:509-512`; `emergence_pack.py:83`; W/grep_arch.out:1-3 | effect not established beyond p028 | confirmed |
| B10, C2, D-F8 | Site graphs have no relationships; Tesseract changes no verdict | high (C2); medium (B10, D-F8) | design limit | replay_shipped_run0.txt:1; tesseract_counterfactual.out:3-6 | none / none | confirmed at seed 0; probable at seeds 1-5 |
| B5 | The 8-token negation window stores most negated rows as claims | medium | defect | `emergence_pack.py:127-128`; `extract.py:237-241`; negation_probe_seed0.txt:9 | d059 d084 gain confirming rows / s4 p022 found partly through it | confirmed |
| B8 (with D-F5 class-5 half) | A confirm ignores retractions; conclusions are never re-checked | medium (B8); high (D-F5) | design limit | `verify.py:251-254`; `gate.py:250`; S/rc/B/decoy_probe_run0.txt:32, :58, :79 | d059 d074 d084 / 30 class-5 acceptances | confirmed; counterfactual counts probable; D-F5's class-5 attribution confirmed wrong |
| C13, C11 | False statements in committed documents (rare "invisible by construction", "ceiling 0.55", simulator "executed and measured") | medium | measurement issue | 1.1, 1.2 rows | n/a | confirmed |
| D-F12, B13 | `top_n` = 60 cut one true candidate at seed 4 and one at seed 5 | low | design limit | `emergence_world.py:84`, `:285-291`; S/rc/B/r2/cut_probe_seed4.txt:2 | none / 2 cut, 1 lost find | cut confirmed; lost find probable |
| B12 | Extraction attaches a record's predicate to a co-mentioned aux entity | low | defect | S/rc/C/r2/logs/aux_claims_check.out:1; capacity_split_0_5.txt:33 | none / s4 p023's support | count confirmed; effect shown in one harness case; no action proposed (8.5) |
| A12 | The read cap L = 50 truncated a few reads | low | design limit | summary_numbers.txt:18, :41, :64, :87, :110, :133 | none / 4 pairs | counts confirmed; as a cause of acceptance confirmed wrong; no action proposed (8.5) |
| A2, A3, A5, A6 | The mycelic loop-timing stall (section 5), off the scoring path | medium (A2, A3, A5) and low (A6) by A; low for recall (this report) | defect (A2, A4); measurement issue (A3, A5) | `tests/mycelic/test_api.py:394-395`; lag_service_and_rank.out:8, :24 | none / none | A2 probable; A3, A5, A6 confirmed |
| C6, C8 | 13 of 40 credited to a CI run whose discovery block nobody read; SPEC §5 traceability impossible from the sandbox | medium | measurement issue | `SPEC.md:80`, corrected at `:130-140` | n/a | confirmed |

### 4.2 Evidence not given elsewhere

The evidence for most rows is in sections 2, 3, 5 and 6, at the lines the table cites. This section adds what is not there.

- **B1, D-F2, C17.**
  - [bound] 35 of 240 Gold patterns have a found window that ends before W12, 13 of them rare (early_window_census.out:7; ceiling.out:204).
  - The shipped gate supports 5, 10, 8, 12, 3 and 5 patterns only before W12, 43 in all, counting every claim (derived_totals.out:2). B's own-facet classes also total 43 (warmup_sets.txt:7).
  - Decoys: 21 class-2, 19 class-4 and 28 class-5 decoys never got a snapshot on their scoring key (W/decoy_class_table.out:32, :34, :35). Some have facets on or after W12: the not-asked decoys' last facet day reaches 84, 89 and 79, while W12's first day is 77 (S/rc/B/decoy_cross_seed_0_5.txt:1-3). So the share of these decoys that the warm-up accounts for is not established.
- **C3.**
  - [harness] accept-all against the [real] system, seeds 0-5: found 98 against 93 (accept_all_check.txt:7-8).
  - Decoys: 122 against 118 by the report rule, and 185 against 180 on any facet key by B's count. B's count is not section 6's in-window re-score (accept_all_check.txt:7-8).
  - Mean recall: 0.4083 against 0.3875 (compare_accept_all.out:7).
  - Seed 0, re-scored from the real report: 15 of 40 against 13; precision 13/47 = 0.277 against 15/55 = 0.273 (S/rc/A/round2/accept_all_check.out:1-2, :12).
  - SPEC §2(3) compares only with the baseline code (`SPEC.md:57-59`).
- **C1, D-F15.** `generate` and `gold_labels` do not read `WORLD_TABLE`, so the guard can change without touching frozen code. Held-out rows cannot be tabulated before the freeze. This is not a cause of 13 of 40.
- **C7.** [bound, Model 1, every claim] Pack values move the bound between 0.4542 (shipped, weeks ≥ W12) and 0.8542 (one counted site, no freshness, no roots or reporters minimum, any week) (gate_bound_variants.out:7; one_site_sensitivity.out:8). With freshness kept, the upper end is 0.8417 (:2).
  - One counted site and weeks before W12 are founder decisions (ranks 12 and 7, N), not tuning.
  - C7's own text says "between 0.454 and 1.0". The 1.0 is the pooled variant. It needs counts in 1..k−1 to leave a site, and gate_bound_variants.py:8-9 calls it "an OPTIMISTIC bound", so it is not a pack value.
- **C5.** [sim] Register precision is 0.0559, 0.0400 and 0.0189 at 2,000, 10,000 and 50,000 users (sim_register_depth.out:1-3).
- **C9.** The pack's chain lexicon uses the generator's `PRED_SURFACE` (`emergence_pack.py:102`; `corpus.py:210`). The adapter passes the canonical anchor id (`emergence_world.py:179`). Gold facet records are 5.27% of records at 200 users and 0.20% at 10,000 (world_density.out:1, :3). So every recall figure here comes from a lexical judge reading the generator's own phrases and structured entity ids. It says less about end-to-end recall on real text. Rank 5a states these limits beside the figures.
- **C12, B14.** [real] The counted confirms behind true conclusions at seed 0 are 254 own records and 6 background records (summary_numbers.txt:19). B: crediting the six Gold-contradicted finds (p001 p017 p022 p024 p033 p034) follows the frozen labels.
- **C6, C8.** All three e2e job logs are 11,692 lines long (1.0). [real] Local runs give 13 of 40 (W/provenance.out:5).

### 4.3 Capacity ceilings, by model

The models use different windows, timing and counting. Their figures are not compared with each other.

**Model 1: claim-derived bound under the shipped gate [bound].**
- Method: D's `gate_capacity` method, re-implemented by B (S/rc/B/r2/capacity_split.py) and by C (S/rc/C/r2/scripts/gate_bound_variants.py).
- Assumptions: perfect detection; every site asked; a question at any snapshot week inside the found window, over the shipped 6-week window; k = 3 per site, 2 counted sites, lower bounds on roots and reporters, and 42-day freshness.
- Validation: it matches the real confirm counts on every routed true-candidate pair (summary_numbers.txt:12).

| Counting | Any snapshot week | Snapshot week ≥ W12 | Source |
|---|---|---|---|
| Every stored claim on the key | 152/240 = 0.6333 (rare 15/103) | 109/240 = 0.4542 (rare 10/103) | W/capacity_sums.out:1, :3; gate_bound_variants.out:7 |
| The pattern's own facet records | 146/240 = 0.6083 (rare 9/103) | 102/240 = 0.4250 (rare 4/103) | W/capacity_sums.out:2, :4 |

- Other gates, counting every claim (C):
  - one counted site: 0.8417 at any week, 0.6417 at weeks ≥ W12 (gate_bound_variants.out:7); 0.8458 at any week without freshness (one_site_sensitivity.out:4);
  - pooled cross-site sums: 1.0 at any week, 0.80 at weeks ≥ W12 (gate_bound_variants.out:7).
- Pooled sums need counts in 1..k−1 to leave a site, which `LEAKAGE.md:32-34` does not permit.
- The W12 cut alone moves the every-claim bound from 152 to 109 of 240.

**Model 2: the diagnosis's fixed-window model [bound]** (S/r2/ceiling.py; coarse_v2.py).
- On the aligned grid G6 at +20, with freshness 42, perfect detection and routing, a lexical judge, and every claim counted: R0 0.421, R1 0.575, R2 0.700, R3 0.771 (ceiling.out:121).
- Under detector targeting (W12), any rule on any window set is capped at 205/240 = 0.854 (ceiling.out:204).
- Its W06+ what-if regimes are in 4.4 (a).

**The real system [real]:** 93/240 = 0.3875 (decomp_r2.out:78; compare_accept_all.out:7).

### 4.4 Positions on the open critiques of the diagnosis

The last critics left four blocking findings on S/DIAGNOSIS-r2-v2.md (S/synth/r2_critiques.json). Three are answered here and the fourth (T2k) in 6.6. Section 10.10 adopts these positions.

**(a) The W12 cut binds detector-driven candidates only.** Status: confirmed in code (`egress.py:553-556`; `harness.py:1288-1294`; `world.py:136-141`). The W06+ figures below are [bound], Model 2: probable.
- With no gate and no freshness at all, every admitted window from W06 gives 0.896 (CV:541). This is the diagnosis's disclosure-class A ceiling: one site with records, roots and reporters each ≥ k (ceiling.py:108-109).
- R1 sets `min_confirming_sites` to 1 in `questions.json`. That is a pack value, but it removes two-site corroboration, which the founder's default treats as an invariant (HANDOFF-PROMPT-2026-10-15.md:179-180).
  - Nested windows from W01 at +14: 0.854, with a two-sided six-seed bound of 0.781 (CV:526); 0.863 at +20 without freshness (CV:528).
  - Every admitted window from W06: 0.892 at +20, 0.896 at +14 (CV:543, :545).
  - Counting own records, R1 is 0.750 on nested windows at +14 (CV:535) and at most 0.775 in any W06+ set (CV:554, :556).
- R2 (two confirming sites kept): 0.921 on the grid from W01 at +14 (W/rev/coarse_w06_r0.out:18), and 0.838 counting own records (:33).
  - That file is a scratch copy of coarse_v2.py with R0 and R2 enabled in the W06+ regimes. It reproduces CV's R1 and R3 lines (for example :79 = CV:526, :110 = CV:545), and it was re-run byte-identical.
- R0: at most 0.708 in every W06+ window set (coarse_w06_r0.out:16, :47, :78, :82, :105, :109, :113).
- Gate 3 was not measured for any W06+ configuration. Raw decoys with timed targeting on the grid from W01: 209 under R1, 247 under R2 and 253 under R3, of 300 (ceiling.out:145-147). Whether a pre-W12 question scheme is sound is not established.

**(b) A non-adaptive sweep does not reach the perfect-detection recall under the shipped budget.** Status: probable [bound].
- The budget: each site answers at most 5 distinct questions per entity per site-clock day (`egress.json`; `verify.py:402-406`). The site clock is the question's as_of (`site_process.py:448`). A sweep asks 8 predicates per entity per window (S/r2/rv2check/budget_sweep.out:1).
- R3 on G6 at +14: 0.825 without the budget. With the budget, over 56 first-day choices: min 0.783, mean 0.805, max 0.821; 42 of the 56 are at or above 0.80 (budget_sweep.out:2).
  - At +20 with freshness 49 it holds 0.825 in all 56 (:6).
  - With a 2-site minimum the mean is 0.788 (budget_sweep_m2.out:2).
- R2 on G6+G12 at +14: 0.808 without the budget; with it, a mean of 0.784, with 9 of 300 orderings at or above 0.80 (budget_sweep.out:13). On nested windows at +14: 0.829 without the budget; with it, a mean of 0.810, minimum 0.792, with 52 of 56 choices at or above 0.80 (:11).
- R4[R2] timed keeps a minimum of 0.800 under the budget on G6+G12, and 0.829 in all 300 orderings on nested windows (budget_targeted2.out:4, :6). It targets keys surfaced by a detector or rule-layer row. It needs rule-layer hits routed and the weekly cells kept, and its transcript was not attacked (Summary item 6 (b)).
- Decoys under a budget-limited sweep are not established.

**(c) Is a 0.80 mean reachable, or impossible, under today's rules?** Status: probable. It covers only the configurations computed so far, and it is not the proof SPEC.md:126-128 asks for. The plain position is in the Summary's Verdict. The evidence:
- R0 is at most 0.708 in every computed model and window set: Model 1 0.6333; Model 2 W12+ 0.608 (CV:421); W06+ 0.708.
- With W12 kept and per-site k:
  - R1 peaks at 0.775 (CV:422).
  - R2 peaks at 0.750 on G6 (GV:26). There the attacker pins 0 with the weekly cells withdrawn (attacks_exact.out:5-7); with the cells kept, it was not computed.
  - R2 reaches 0.808 on G6+G12 and 0.829 on nested windows at +14 without the budget (CV:179, :258), and 0.846 over every window ending at W12 or later (CV:423). Under the budget, the nested sweep keeps a mean of 0.810 (4.4 (b)). With the cells withdrawn, the attacker pins 906 on G6+G12 and 1,750 on nested windows (attacks_exact.out:9, :15). For comparison, today's design, modelled as its weekly cells plus sliding verdicts as the detector asks, pins 5,631, and today's verdicts on every admitted window pin 9,127 without cells and 9,829 with them (S/r2/disclosure/attacks.out:31, :25, :19). Pin 0 is a proposed bar that today's design does not meet, and these pins come from the window set and the cells, not from the gate rule.
  - R3 and pooled counts need counts in 1..k−1 to leave a site (`LEAKAGE.md:32-34`; `egress.py:77`; no secure-aggregation code, repo_facts.out:81-82).
- Without W12: the R1 and R2 paths of (a), under the privacy conditions of Summary item 6 (c): an enforced window set, the weekly cells withdrawn, and for R1 the (e) answer format.

### 4.5 Ruled out (confirmed)
- Transport, NATS, the outbox, the consumer, the fabric, routing at m = 3, extraction of positive facets, and D's instrumentation (A1, A9, D-F10, the B13 routing part, D-F14; checks in 3.7). The in-process harness, with no NATS or fabric, gives the same found sets and decoys by class at all six seeds (compare_B_vs_D_seeds.txt:1-6).
- Gold leakage into decisions: none found (C14, not cross-challenged).

### 4.6 Not established in sections 4 and 6
- The recall and decoys of the real pipeline under any lever, rule or window change. Every recall figure above the accept-all control's 0.4083 in sections 4 and 6 is a [bound].
- Tesseract refute and unknown flips at seeds 1-5.
- Whether a pre-W12 question scheme is sound, and its gate-3 decoys.
- The any-facet re-score for the configurations of 6.6.
- The differencing exposure of any decoy lever (W4, W4k, W3k, T2k, T2p, R4lb, C1s), and of R4[R2] timed with the weekly cells kept.
- W4's effect on non-synthetic text. Its clustering strips the synthetic header, date and last token (rules_decoys.py:22).
- A budget-limited sweep's decoys, and the recall effect of the missing aggregation level and follow-up questions beyond p028.

## 5. Runtime stall

Two things were called "the stall". 5.1 is a flaky timing test in the mycelic unit suite. 5.2 is the wall time of the EMERGENCE end-to-end run. Neither changed a scored output. A003 is reported apart in 5.3.

The reviewers measured at c096aec; the cited code paths are the same at 7639bec (`W/s5_code_facts.out:334-335`) and at 91ab29c (W/rev2/head_now.out:53-54) and 9cfa2e9 (S/synth/fix/head_9cfa2e9.out:5). Kinds: [CI], [local test] (with the load), [real], [harness]. No simulator figure is used.

**Machine load.** The status log at 91ab29c says two orphaned CPU-bound probe processes, left by reviewer A's run of 2026-10-14 23:04, were busy on 2 of the 4 CPUs until they were killed at 03:57. Stall measurements taken before then ran under that extra load (LOOP-48H-STATUS.md:344-346 at 91ab29c). That covers the reviewers' samples after 23:04 and this report's own check at 01:53 (below). This report has not re-checked them on the quiet machine. The probe load itself is unverified: it is read from the status log only.

### 5.1 The mycelic CI loop stall (tests/mycelic; not on the EMERGENCE run path)

**The two tests** (`tests/mycelic/test_api.py`)
- `test_verification_never_holds_the_event_loop` (`:317-398`): one MCP batch of 100 verifications and five GET /verify calls on a 3,005-node DAG (`:374-380`). There is no query call, and `service.verify` never names the retriever (`W/s5_code_facts.out:117-118`). So `mycelic/retrieval.py` never runs in this window.
- `test_lineage_walks_never_hold_the_event_loop_and_are_priced` (`:400-525`): two GET /lineage, one MCP lineage, one POST /query and one MCP query (`:478-490`). The query calls run per-query BM25 on the loop (`mycelic/service.py:1704`; `retrieval.py:118`); the index is built before the window (`:442`). Walk threads spin 0.2 ms per node (`:430-436`).
- Both tests have the same bounds. The largest gap between 2 ms ticks must stay below `walk_seconds / 2` (`:395-396`, `:499-500`), and /health must stay below `walk_seconds / 2 + 0.05` (`:397`, `:501`). `walk_seconds` is one walk timed alone before the window (`:339-342`, `:437-439`).
- Walks run in worker threads through `asyncio.to_thread` (`service.py:1950`), behind per-organisation slots with a queue timeout (`service.py:1896-1902`). The tests set that timeout to 600 s (`:326`, `:412`).

**CI record [CI]** (copied from the job logs into `W/ci_mycelic_timing_tails.txt:3, :8, :13`; ratios in `W/s5_stall_numbers.out:1-3`).

| Run (commit), time | Job | Failing test | Largest gap | Bound | Over | One walk | Freeze in the failing test | LoopStallWatch |
|---|---|---|---|---|---|---|---|---|
| 38612799645 (d270bd3), 18:31:06Z | 116020172954 | verification | 0.25825 s | 0.24185 s | 6.8% (16.4 ms) | 0.484 s | no | no |
| 38628599664 (55914e3), 20:22:29Z | 116074070389 | lineage | 0.25450 s | 0.24432 s | 4.2% (10.2 ms) | 0.489 s | no | no |
| 38633513470 (fd0966e), 20:56:30Z | 116090787590 | lineage | 0.25442 s | 0.24797 s | 2.6% (6.5 ms) | 0.496 s | yes (14d3a82) | no |

- The bound was 0.4996 to 0.4999 of the walk in all three. The green runs 38631925525, 38639771190 and 38661107236 print nothing on a pass, so their gaps are not established.
- No stack samples exist (confirmed): all three failures predate the watcher. 24fe3ce is not an ancestor of d270bd3, 55914e3 or fd0966e (`s5_code_facts.out:319-333`).
- The freeze did not stop the failures (confirmed): fd0966e failed with `freeze_older_objects` in both tests (`s5_code_facts.out:366-375`).

**What the status log said, and what still holds**

| Status line | What it said | Verdict |
|---|---|---|
| 20:15 (`docs/handoff/LOOP-48H-STATUS.md:233-237`) | GC passes caused the d270bd3 failure; freeze added at a2a1bbe | Not sufficient: fd0966e failed with the freeze (confirmed) |
| 21:25 (`:244-251`) | "Under load, the samples put the loop in its own per-query BM25 build" | Mostly a sampling artefact, not a CI measurement (A3, confirmed; C agreed) |
| 00:03 (`:277-289`) | Records A's first finding | Stands; the mechanism (A2) stays probable |
| 04:27 (`:341-346` at 91ab29c) | Orphaned probe processes loaded 2 of 4 CPUs until 03:57; stall measurements are re-checked on the quiet machine | Unverified (status log only); applies to the local-test figures below |

**Why the BM25 attribution is mostly an artefact (A3, confirmed).**
- The samples were local, from passing runs (`test_api.py:253-257`).
- The watch's sampler is a Python thread reading the loop's frame (`test_api.py:279-291`). It runs only while it holds the interpreter lock, so it sees the loop at its last lock-releasing call.
- [local test] For a failing 0.228 s gap, /proc gives the loop thread 0.048 s of its own CPU (`S/rc/A/results/summary_batch4.txt:4`; `assertion_messages_batch4.txt:1-2`).
- BM25 appears in 1 of 19 py-spy dumps at lineage stalls over 50 ms (`s5_stall_numbers.out:21`), and in no gap of D's real e2e service (`S/rc/A/round2/lag_service_and_rank.out:2-24`).
- The verification failure never calls retrieval.py.

**The probable mechanism (A2, probable; C: partly).** The loop thread waits for the interpreter lock, held by CPU-bound walk threads, and for a CPU, each time it retakes the lock. No single call holds the loop for 0.25 s.
- [local test] 68 py-spy dumps at stalls over 50 ms, taken at load 8.7 to 25 (`S/rc/first_A.md:92`; dump counts `s5_stall_numbers.out:21-23`). The lock holder was a walk worker in 32, not shown in 22, the loop in 12 and another thread in 2. C's challenge rests on the 22 and the 12.
- [local test] /proc over gaps ≥ 50 ms: loop CPU 2 to 26%, run queue 26 to 69%, asleep (mostly on a futex) 13 to 69% (`:20`). Beside two lock-bound threads at load 17.41, `asyncio.sleep(0.002)` takes 13.77 ms median, against 2.11 ms alone (`S/rc/A/results/loop_ops_micro.out:1, :11-14`).
- [local test] Small work on the loop (A4: operations confirmed; magnitudes on CI probable). Medians at load 17.41: lineage JSON encoding 32.93 ms; BM25 over 3,005 rows 16.67 ms; COMMIT with fdatasync 2.71 ms; index rebuild after a write 163.38 ms, outside both windows (`loop_ops_micro.out:5-10`).
- [real] D's e2e service: 9 of 21 loop gaps at `selectors.py:468`, max 0.214 s, at load 8.90 to 20.12 (`lag_service_and_rank.out:8, :24`).
- All samples come from a shared 4-CPU machine at load 8.7 to 25, partly under the orphaned probe load by the status log's unverified account. The cause on CI hardware is not established.

**The bound is load-sensitive by design (A5, confirmed).** The bound is half of one walk timed alone, and the CI overruns were 6.5 to 16.4 ms.
- [local test] With no added load, the walk ranged from 0.388 to 0.950 s (verification) and from 0.501 to 1.290 s (lineage) (`s5_stall_numbers.out:9-10`).
- Under nice −15, the verification ratio ranged from 0.55 to 1.01 over cold runs, and from 0.44 to 1.09 with pre-warmed runs included (`:8`).
- Of A's 44 runs per test, verification failed 7 times and lineage twice (`:4-7`). A reports 45 runs; that was not reproduced.
- With 8 burners both tests failed (3.184 s, 2.992 s; `:13-14`).

**A check at lower load** [local test], this report's own. Three runs of A's minimal probe, with no burners, at load 1.72 to 2.64, at 01:53 UTC (`W/s5_lag_now/min_b0_now_r{1,2,3}.meta`). By the 04:27 line, the orphaned probes were still running then.
- All 6 tests passed. The verification ratio was 0.48 to 0.62 and the lineage ratio 0.20 to 0.23. Inside verification gaps ≥ 50 ms, the loop was asleep 57% of the time (`W/s5_lag_now_summary.out:2-11`).
- This fits A2 but does not prove that the futex is the interpreter lock.

**Classification against the directive's list**

| Item | Finding | Evidence | Status | Kind |
|---|---|---|---|---|
| Blocking calls | No single call holds the loop for 0.25 s. Small synchronous work runs on the loop: JSON encoding, COMMIT with fdatasync, get_memory, BM25 (lineage test only), and the index rebuild after writes | `loop_ops_micro.out:5-10` | confirmed (A4) | implementation defect, low |
| Blocking calls, periodic | `refresh_status()` calls `store.stats()` synchronously inside a coroutine, run by `_status_loop` every status interval; `stats()` runs full-table COUNTs | `service.py:2428-2432`, `:2447-2453`; `store.py:1333-1340` | unverified (A13): C confirmed the code path; nobody measured the cost; there is no `store.stats` frame in D's 21 service gaps (`lag_service_and_rank.out:2-24`) | implementation defect, low |
| Deadlocks | None; every run finished | CI tails; A's runs; this report's runs | confirmed | none |
| CPU-bound work | Walks are CPU-bound Python in worker threads of the same interpreter; 979.62 ms median per verification walk at load 17.41 | `service.py:1950`; `test_api.py:430-436`; `loop_ops_micro.out:3-4` | fact confirmed; as the cause probable (A2) | design weakness |
| Timeouts | The walk queue timeout exists; tests set 600 s; none fired | `service.py:1896-1902`; `test_api.py:326, 412` | confirmed | none |
| Concurrency | Six walkers share two slots (verification); five agents share two lineage slots | `test_api.py:322-326, 409-412` | confirmed | by design |
| Thread starvation | The loop thread waits for the lock and for a CPU | /proc split; py-spy | probable (A2) | design weakness |
| Non-termination | None | as deadlocks | confirmed | none |
| Cancellation | A cancelled handler keeps its slot until its walk thread ends; tested at `test_api.py:803` | `service.py:1931-1957` | confirmed | by design |
| Exhaustion | Thread start inside the window costs 17.71 ms beside two lock-bound threads; pre-warming did not stop the failures (1 of 4) | `loop_ops_micro.out:13`; `summary_batch4.txt:10` | probable contributor; not necessary | none |
| Garbage collector | Full passes in the workers hold the lock (gen2 0.042 s and 0.080 s in two failing messages); the freeze did not prevent fd0966e | `assertion_messages_batch4.txt:1-2`; `s5_code_facts.out:366-375` | contributor: probable; not sufficient: confirmed (fd0966e failed with the freeze) | none |

**What this means for the planned "retrieval stall" fix.** The evidence does not support a retrieval.py change as the fix for the red runs: the verification failure never calls retrieval.py, BM25 is in 1 of 19 dumps, and the attribution came from a sampler blind to lock waits. In order:
1. Make the tests diagnose without relaxing them (rank 20 (a); tests only, hashed by no pin).
2. If the founder wants the loop responsive during walks, the probable lever is how walks run (`service.py:1950`). This is shown only locally; its before/after is in 9.7.
3. Move small loop items off one at a time. The largest measured is the index rebuild (`retrieval.py:93-100`).

The order "A003 first, recorded and frozen" (LOOP-48H-STATUS.md:269-271) is met by the status log's reading at 6c0c322 (:337-340; 5.3). One clause of the freeze is open, so whether the order is met is the founder's question (8.0). "Nor during any active benchmark" still applies (HANDOFF-PROMPT-2026-10-15.md:74). The status log says the stall fix "is being diagnosed and designed from measurements first (not presumed to be in retrieval.py)" (LOOP-48H-STATUS.md:318-319 at 91ab29c). `mycelic/*.py` is in the c7 pin (dec. 14).

Effect on EMERGENCE: none at runtime (5.2). The only link is through the CI gate. The e2e job needs suites-core (`.github/workflows/e2e-ingest.yml:155`), which runs tests/mycelic (`research/e2e_ingest/suites.py:87`). In e2e-ingest runs 38628599679, 38631925246 and 38633513481, the core job had tests/mycelic failures=0, and the e2e job was skipped after the lab suite failed (`S/rc/A/ci/e2e_ingest_gate_jobs.txt:2-8`). The flake can skip a run; it cannot change recall or decoys (A6, confirmed).

### 5.2 The EMERGENCE end-to-end runtime [real]

"Seed 0" here means D's instrumented run unless another run is named. Load was 8.90 to 20.12 on 4 CPUs during it (`S/rc/D/load_seed0.txt:1-19`).

**How a question runs.**
- There is one `asyncio.run` per question (`harness.py:512`; 55 distinct loops, `S/rc/A/round2/hq_mainthread_gaps.out:1`).
- HQ asks the routed sites one after another (`core.py:409-410`), with no `wait_for`, timeout or gather (`W/s5_code_facts.out:177-178`).
- The adapter's async `query` calls a blocking pipe request (`hq.py:283-290`). The request blocks on `stdout.readline()` under a lock, with no timeout (`wire.py:224-238`).
- Site stderr goes to DEVNULL (`wire.py:223`; `site_graph.py:261-263`).
- Each site reads one stdin line at a time (`site_graph.py:350-356`) and runs Tesseract on a private loop (`site_graph.py:106, 211`).
- The harness's client uses the SDK's 15 s timeout (`harness.py:747, 750`; `mycelic/sdk/__init__.py:50`).

**Where the time goes**

| Figure | Value | Source |
|---|---|---|
| Main pass, D's seed-0 run | 171.15 s | `W/s5_stall_numbers.out:45` |
| HQ main-thread gaps in the main pass | 56 gaps, 162.4 s, max 6.031 s | `hq_mainthread_gaps.out:1` |
| Where HQ waited | 1,128 of 1,130 samples at `wire.py:236` | `hq_mainthread_gaps.out:2` |
| Per-question p50 / per-site p50 | 2.755 s / 0.891 s (n = 55 / 168) | `lag_service_and_rank.out:36` |
| Per-question / per-site ratio, 18 local real runs | 2.91 to 3.28 | `s5_stall_numbers.out:44` |
| Site rankings (166) | median 0.864 s, p10 0.691, p90 1.339, max 2.017 | `S/rc/A/round2/lag_site_phase.out:9` |
| What rankings spend time on (1,381 samples) | 56.8% per-node scoring (`tesseract.py:1258`); 12.5% reloading the session from SQLite (`tesseract.py:635-636`) | `lag_service_and_rank.out:27-28` |
| Site start-up sync gaps | 2.00 to 3.18 s, one per site process | `lag_site_phase.out:1-7` |
| Mycelic service, largest gap after start-up | 0.256 s, against the 15 s SDK timeout | `lag_service_and_rank.out:17` |
| Harness process (pid 31414: HQ, bridge, drills), whole run | 539 gaps, 1,673.1 s, max 7.173 s | `S/rc/D/out/lag_seed0.stdout.txt:2` |
| of which worker threads after the main pass (fixed-query rankings, the drill-6 re-run) | 139, 94 and 38 gaps; 480.7, 321.3 and 156.6 s | `lag_seed0.stdout.txt:13-15` |

- HQ's gaps are waits for a site's answer; nothing else is queued on a per-question loop (A, partly agreeing with D-F9).
- D's "1.7-3.2 s per ranking" is confirmed wrong: those maxima are start-up syncs.
- D-F8's "site-side event-loop blocking", as stated, is confirmed wrong: the site loop is private, and each gap is the ranking's own compute (A and C).

**No loss from runtime (confirmed).**
- Six seed-0 real runs, with main passes from 159.9 s to 1,006.9 s, are identical in every scored output (`S/rc/A/round2/main_pass_times.out:1, :7-12`).
- Two real run sets per seed for seeds 1-5 agree (`compare_runs.dev_seed{1..5}_vs_D_seed{1..5}.out:1-8`).
- The [harness], with no wire, NATS or fabric, gives the same found sets and decoys by class at all six seeds (`compare_B_vs_D_seeds.txt:1-6`).
- The harness makes no query call (`e2e_probe_summary.run_seed0.out:19`).

**Classification against the directive's list**

| Item | Finding | Evidence | Status | Kind |
|---|---|---|---|---|
| Blocking calls | A blocking pipe read inside an async coroutine; it blocks only its own per-question loop | `hq.py:290`; `wire.py:236`; `harness.py:512` | confirmed | design weakness, low |
| Deadlocks | None; each endpoint's lock serialises its requests | `wire.py:224, 228`; 175/175 answered | confirmed | none |
| CPU-bound work | Each ranking rescores the whole session in Python: 0.864 s median | `lag_site_phase.out:9`; `lag_service_and_rank.out:27-28` | confirmed (A14, not cross-challenged) | latency only |
| Timeouts | None on the wire or in core.py; SDK 15 s; `wait_idle` 300 s | `wire.py:226-238`; `s5_code_facts.out:177-178`; `harness.py:352` | confirmed (A8) | implementation defect, medium (latent) |
| Concurrency | Sites are asked one after another, so a question costs about 3 times a site | `core.py:409-410`; `s5_stall_numbers.out:44` | confirmed | latency only |
| Thread starvation | None seen at HQ or in the service on the scoring path | `hq_mainthread_gaps.out:2`; `lag_service_and_rank.out:24` | confirmed for these runs | none |
| Non-termination | Latent: a site that hangs without exiting blocks HQ forever, with no diagnostic | `wire.py:223, 236`; `site_graph.py:263` | confirmed in code; not triggered | implementation defect, medium |
| Cancellation | There is no cancellation path, and an asyncio timeout cannot interrupt a blocking `readline`. A read timeout must discard the pipe, or the next `readline` returns the late answer | `wire.py:226-238` | unverified (design note) | latent |
| Exhaustion | No limit reached: 0 budget refusals at all six seeds; L = 50 truncated 0/0/1/2/1/0 pairs and cannot create a confirm; the /memories (500) and /admin/events (1,000) caps were not reached; `wait_idle` ignores `num_ack_pending`, observed 0 | `W/s5_budget_refusals.out:1-6`; A12; A11; A10 (`drills.py:375-380`) | confirmed (not triggered) | implementation defects, low |

Verdict: the EMERGENCE runtime findings concern latency and latent liveness only. They changed no verdict at seeds 0 to 5 and explain no miss or accepted decoy (confirmed). A concurrent fan-out would make a question cost as much as its slowest routed site; that gain was not measured. Any change to `wire.py`, `hq.py` or `core.py` changes the c7 pin and needs dec. 14.

### 5.3 A003, reported separately
- **The stall code is not on A003's path.**
  - Outside `mycelic.collective`, the lab loads only `mycelic`, `mycelic.hierarchy` and `mycelic.version` (`S/rc/A/scripts/lab_imports.out:10`). This was re-checked at a35324e with j4 merged (S/DIAGNOSIS-r2-v2.md §8.1).
  - The lab never imports `mycelic/retrieval.py` or `mycelic/service.py`.
  - mycelic.yml ignores `lab/requests/**` (`.github/workflows/mycelic.yml:6-7`).
- **Its run** [CI].
  - At 7639bec the request was absent (`W/a003_request_check.out:19`). c5dad9a added only that file (`W/provenance.out:1, :3, :9`).
  - mycelic-lab run 38667030739 (#17) on c5dad9a ran from 01:41:42 to 02:13:35 UTC with conclusion `success` (W/rev/ci_runs_now.out:2). That is the workflow's conclusion, not A003's result.
  - The 03:55 line corrects the completion time from 02:13:41 to 02:13:35 UTC (LOOP-48H-STATUS.md:326-327 at 91ab29c).
- **Its result, as the repository records it.**
  - Plan, both provision jobs and all five shards are `sealed` with exit code 0; the aggregate job is 116209851508 (LOOP-48H-STATUS.md:310-311 at 91ab29c).
  - Primary pair (a-4b, `negation_terms`): `failure`, balanced accuracy 0.780 [0.733, 0.823], gap to 0.860 of 0.080 (:313-314; the RESULTS run 13 heading, docs/lab/RESULTS.md:618 at 91ab29c).
  - These are A003 figures, not EMERGENCE figures. This report did not re-read the lab logs, so these figures are unverified.
- **Its record.** At 6c0c322 the run is recorded in four places (W/rev2/a003_record.out:1-18, :28-33):
  - CHOICE-A003 Run 1 (`docs/collective/A003/CHOICE-A003.md:1569`), which holds the request's sha256 (:1574), commit c5dad9a, the settings, and the run and job ids;
  - RESULTS run 13 (`docs/lab/RESULTS.md:618`);
  - the YC brief (`docs/strategy/YC-BRIEF.md:28, :96-97`);
  - the status log (the 03:55 line, LOOP-48H-STATUS.md:326-340).
  - The `report.md` block was rebuilt from the log and its sha256 matched. Its bytes are committed as `docs/collective/A003/run-1-report.md`: 178 lines, sha256 prefix `c7ee523a751a`, re-hashed here (W/rev2/a003_record.out:9-10).
  - Nothing has changed in these files at 91ab29c (W/rev2/head_now.out:51-52) or at 9cfa2e9 (S/synth/fix/head_9cfa2e9.out:9).
- **One clause of the freeze is open.** HANDOFF-PROMPT-2026-10-15.md:150-151 asks for "the report blocks rebuilt from the logs with their sha256 checked".
  - The `report/report.json` block begins before the 5,000-line log tail, so its sha256 is not verified (CHOICE-A003.md:1610-1612; LOOP-48H-STATUS.md:329-330).
  - The record rests on the `report.md` block until the founder downloads the report artifact 11942489769 before 2026-11-14 (LOOP-48H-STATUS.md:350-356 at 91ab29c). None of the run's artifacts was downloaded: the report artifact, the plan artifact lab-plan-38667030739-1 (11942755522), five shard and two provision artifacts, all expiring 2026-11-14 (:334-336). The open ask covers the report and plan artifacts (:350-351).
  - A002's run was recorded with the same gap (LOOP-48H-STATUS.md:191-192).
  - The status log reads this record as meeting the founder's "recorded and frozen" (:337-340). Whether it does, with this clause open, is the founder's question (8.0). The items below hold if the founder accepts that reading.
- **What the record changes for the order of work** (section 8.0):
  - `mycelic/retrieval.py`: the condition "until A003's run is recorded" (HANDOFF-PROMPT-2026-10-15.md:74) is met by that reading. "Nor during any active benchmark" still applies. The evidence still does not place the stall's cause in retrieval.py (5.1).
  - Zone 3a (`gate.py`, `egress.py`, `detectors.py` and others): A003's record no longer gates them. They still need the c7 update (dec. 14), and no CI-triggering push is made while model shards run (HANDOFF-PROMPT-2026-10-15.md:66).
  - Zone 3b (`verify.py`, `records.py`, `extract.py`, `loader.py`): A003's record no longer gates them either, but two things still constrain them.
    - They are in j4's code hash (`lab/j4.py:183-184`, built on `lab/j1.py:136-141`), so A003's recorded hash covers them.
    - A004's rule names `verify.py`'s `judge_task`, `judge_payload` and `decide` as "pinned, unchanged", and builds `lab.j5.CODE_FILES` on j4's (`docs/collective/A004/CHOICE-A004.md:156-159, :1385-1386`).
    - A004's amendment is committed at 9cfa2e9 ("CHOICE-A004: amended after A003's result (branch T; placeholders 1 to 7 filled)"). It does not address zone 3b edits, and the lines cited above read the same at 9cfa2e9 (S/synth/fix/head_9cfa2e9.out:1, :7-8). Whether zone 3b files may change before A004's judging run is not established; it goes to the founder as dec. 15, restated.
- **A003's record is preserved.** Nothing in this report changes `lab/**`, `mycelic/collective/**` or a j4-pinned file.

## 6. Decoy analysis

"Design weakness" here means the same as "design limit" in the section 4 table.

### 6.1 Classes and the two counting rules

Each world has 50 decoys, 10 per class (W/provenance.out:5).

| Class | Name | How it is built |
|---|---|---|
| 1 | entity coincidence | one anchor, predicates from mixed chains, random times (`research/mycelic/corpus.py:616-623`) |
| 2 | temporal scramble | one chain's predicates in chain order, timestamps reversed (`:624-628`) |
| 3 | near-miss entities | facet 0 on the anchor, other facets on near-miss entities (`:629-633`) |
| 4 | duplicate inflation | one event per facet, echoed by many users (`:634-637`, `:645-661`) |
| 5 | stale chain | ordered facets in the first half-year, each retracted 30 to 70 days later by about half as many users (`:638-642`, `:674-690`) |

- **Frozen report rule** (`harness.py:1296-1317`): a decoy is accepted when a supported, lineage-checked note sits on its facet-0 key, with a snapshot week inside the decoy's window. Gate 3 reads this (`SPEC.md:28`, `:57-59`).
- **Any-facet re-score (C18):** a diagnostic, not a new metric. A decoy counts when such a note sits on any of its facet keys (`world.py:142-144`) inside its window. Only 0 or 1 decoy-labelled supported notes per seed lie outside every owning window (S/rc/C/r2/logs/decoy_window_check.out:2-7).

### 6.2 Acceptance by class and seed

Cells read "report rule / any-facet re-score", out of 10 per class per seed. [real], re-scored offline (W/decoy_class_table.out:1-36, from S/rc/D/out/tables/decoys_seed{0..5}.tsv and decoy_window_check.out:2-7).

| seed | class 1 | class 2 | class 3 | class 4 | class 5 | all (of 50) |
|---|---|---|---|---|---|---|
| 0 | 0 / 5 | 5 / 5 | 0 / 6 | 9 / 9 | 3 / 3 | 17 / 28 |
| 1 | 2 / 6 | 4 / 4 | 0 / 5 | 7 / 7 | 5 / 5 | 18 / 27 |
| 2 | 0 / 4 | 6 / 6 | 1 / 7 | 7 / 8 | 7 / 7 | 21 / 32 |
| 3 | 3 / 4 | 8 / 8 | 1 / 6 | 4 / 4 | 6 / 6 | 22 / 28 |
| 4 | 1 / 8 | 7 / 7 | 1 / 5 | 6 / 8 | 3 / 3 | 18 / 31 |
| 5 | 0 / 3 | 7 / 7 | 1 / 6 | 8 / 9 | 6 / 6 | 22 / 31 |
| **total (of 60; of 300)** | **6 / 30** | **37 / 37** | **4 / 35** | **41 / 45** | **30 / 30** | **118 / 177** |

Pooled: 118/300 = 0.3933 and 177/300 = 0.59 (derived_totals.out:1). At seed 0 the re-score adds class 1 d050 d070 d075 d080 d085 and class 3 d042 d052 d072 d077 d082 d087 (W/decoy_names_seed0.out:1, :3).

**Where each class stops** (seeds 0 to 5; decoy_class_table.out:31-35). "Asked" means a detector snapshot on the facet-0 key.

| class | never asked | asked | gate supported | accepted (report rule) | asked, not accepted |
|---|---|---|---|---|---|
| 1 | 53 | 7 | 7 | 6 | 1 supported outside the window |
| 2 | 21 | 39 | 37 | 37 | 2 hypothesis (seed 5) |
| 3 | 54 | 6 | 5 | 4 | 1 stale, 1 outside the window |
| 4 | 19 | 41 | 41 | 41 | 0 |
| 5 | 28 | 32 | 31 | 30 | 1 stale (s0 d089), 1 outside the window |

### 6.3 The 5/10 and 9/10 figures, checked
- **Confirmed as reported** [real]: the seed-0 report gives 0.5, 0.9 and 0.3 for classes 2, 4 and 5, and 0.0 for classes 1 and 3 (W/provenance.out:5). Six seed-0 runs agree (S/rc/A/round2/compare_runs.baseline_vs_*.out:4).
- **No check rejected the other five class-2 decoys at seed 0.** 5 were asked and all 5 were accepted; the other 5 never got a snapshot (decoy_class_table.out:2). Four of those have every facet before W12. d046's latest facet falls on day 77, the first day of W12 (S/rc/B/decoy_overview_baseline.txt:2, :7, :27, :32, :42; decoy_cross_seed_0_5.txt:1).
- **Class 4 at seed 0:** all 9 decoys asked were accepted. d048 was never asked (its facets fall on days 19 to 71; decoy_overview_baseline.txt:9).
- Over seeds 0 to 5, among the decoys that reached a question: class 2 had 37 of 39 accepted, class 4 41 of 41, and class 5 30 of 32. The "5 of 10 rejected" at seed 0 comes from detection (no snapshot), not from a check.
- [bound] When detection improves, acceptance rises. With the shipped gate and every facet-0 key asked on G6, classes 2, 4 and 5 reach 46, 52 and 34 of 60 (GV:171).

### 6.4 Mechanism by class

**Class 4, duplicate inflation: a defect plus a design weakness.**
- *Defect (B6, D-F4; confirmed).*
  - A site folds two records into one root only when their folded narratives are byte-equal (`edge/records.py:25-28`, `:378-384`). Every narrative starts with the day and the author's node name (`corpus.py:227-230`), and `origin_ref` is None (`emergence_world.py:183`). So every echo is its own root.
  - [real] At seed 0, 1,067 class-4 echo records from 45 events give 1,019 site roots, against 137 distinct (site, event) pairs (S/rc/B/dup_census_run0.txt:1).
  - For d043, each routed site confirmed 6 or 7 records from 1 event and reported 6 roots (decoy_probe_run0.txt:4-6).
  - The supported decoy-labelled conclusions at seed 0 rest on 767 confirming records, 337 events and 738 roots (summary_numbers.txt:19). The gate sums roots across sites (`gate.py:241`) and compares the sum with 3 (`emergence_pack.py:80`).
- *Design weakness (B7; probable).* [bound] B edited verdict bodies with oracle event deduplication and replayed them through the real gate. Per-site deduplication rejects 2 of the 9 seed-0 acceptances, and global deduplication 3. The other 6 stay supported, because a 6-week window covers 3 to 5 facets of one event, which meets 3 roots under the chain-level key (gate_counterfactual_run0.txt:41-44, :1-17). This was not re-run by others.
- *Lever.* W4: a site is not counted when its records are at least twice its near-duplicate clusters (clusters ignore the header, date and last token; rules_decoys.py:22). It depends on how the generator writes echoes (`corpus.py:218-222`). Its effect on real text is not established.

**Class 2, temporal scramble: a design weakness plus a benchmark inconsistency.**
- *No order check* (D-F5 class-2 half; B7; confirmed).
  - The pack uses one predicate per chain (`emergence_pack.py:102-110`), and `decide()` ignores time (`verify.py:243-259`).
  - The gate's only time checks are look-ahead and freshness (`gate.py:244-246`). The report says "no temporal-order check" (`harness.py:1343`), and E2E-INGEST.md:272-274 states acceptance "by construction".
- *Benchmark inconsistency (confirmed; C reproduced it).* The generator's real patterns are not ordered either (`corpus.py:559`, against its docstring at `:12-13`). Non-monotone Gold patterns per seed: 12, 14, 8, 15, 13, 11 (S/rc/C/r2/logs/order_check.out:1-6). They include 6 of the 13 seed-0 finds (p006 p015 p019 p024 p034 p037). A strict order check would reject real patterns.
- *Lever.* T2k: an HQ majority-reversal test on per-site position cells with ≥ k records (rules_decoys.py:26). Order levers read writer-controlled dates.

**Class 5, stale chain: a design weakness plus the negation defect.**
- *Reconciliation.* D-F5 put class 5 under "no temporal-order check". Class 5 facets are in order (`corpus.py:638-642`), and B and C refuted that half. Its time element is the later retraction (`:674-690`).
- *Design weakness (B8; confirmed in code).*
  - `decide()` confirms on any yes/yes record (`verify.py:251-254`).
  - The gate contests only on a contributing refute (`gate.py:250`).
  - One question per key means nothing is re-checked.
  - d059 has 10 negated rows: 7 received by its as_of, 4 in the window (decoy_probe_run0.txt:32). d084 has 17, 11 and 4 (:79).
  - All 14 of d074's retractions arrive after its as_of (:58). So its conclusion was right at its as_of, and the scorer still counts it.
- *A naive fix costs recall (probable; one reviewer's simulation).* "Any in-window retraction refutes" contests d059 and d084, but also 6 of the 13 found real patterns: p001 p017 p022 p024 p033 p034 (gate_counterfactual_run0.txt:6, :15, :19, :23, :25-26, :30-31). These are Gold-contradicted patterns that the labels count as discoverable.
- *Lever.* R4lb: a later re-ask with a negation-correct reader. The conclusion is contested when the bucket lower-bound sum of negated mentions is ≥ k (rules_decoys.py:30-32).

**Class 1, entity coincidence: a design weakness plus the counting issue.** One predicate per chain spreads a class-1 decoy over several chain keys of one anchor. The facet-0 key holds 38 of 229 class-1 facet records at seed 0 (S/rc/B/decoy_key_census.txt:1). The other keys can reach k at 2 sites, but the report rule does not count them (6 against 30). C1s is the only chain-identity lever, and it costs rare recall (6.6). Status: the facet-0 counting gap is confirmed (B9, C18); the mechanism is probable.

**Class 3, near-miss entities: the counting issue plus a design weakness.** Entity ids arrive structured (`emergence_world.py:179`), so the facet-0 key holds only facet 0. The other facets sit on near-miss keys, where one facet's records at 2 sites can make a supported "chain". The report rule sees 4 of 35 in-window acceptances. C retracted "class 3 is 0/10 by construction". Status: the facet-0 counting gap is confirmed (B9, C18); the mechanism is probable.

**Why classes 1 and 3 stay low under the report rule today.** Per-site k and the two-site gate block their thin facet-0 evidence. [bound] With the shipped gate and every facet-0 key asked, classes 1 and 3 reach only 5 and 4 on G6 at +20 (GV:171). Rules that count weak or pooled confirms give 25 to 40 and 23 to 33 in their no-lever rows (TV:216, :231, :353, :33, :48, :399, :414).

### 6.5 Two cross-class mechanisms

**The 8-token negation window (B5; confirmed; C re-ran it byte for byte, S/rc/C/r2/logs/rerun_B_negation_probe_seed0.out:9).**
- The pack's only cue is a post-cue "not" within 8 tokens (`emergence_pack.py:127-128`). The corpus appends "not" at the end (`corpus.py:223-224`), and the test is at `extract.py:237-241`.
- [real] At seed 0, 135 of 196 negated rows with a pack predicate are stored as affirmed claims, every one with a gap of 8 tokens or more: 70 of 108 class-5 retraction rows, 8 of 13 real contradictions and 57 of 75 background rows (negation_probe_seed0.txt:3-9).
- Seeds 1 to 5: 147, 149, 143, 142 and 130 (cross_seed_0_5.txt:5, :8, :11, :14, :17).
- Decoys: 3 retraction rows confirm for d059, and 2 for d084 (decoy_probe_run0.txt:29-30, :76-77).
- A simulated correct negation changes no seed-0 gate status (gate_counterfactual_run0.txt:45, :53).
- At seed 4, rare p022's find depends on one misread negated row (found_dependence_0_5.txt:76).

**Facet-0-only counting (B9, C18, D-F6; confirmed).**
- The report rule takes one key per decoy, from facet 0 (`harness.py:1300-1309`), while `candidate_label` labels every facet key of a decoy (`world.py:142-144`).
- Over six seeds: 118 against 177, a gap of 59 (class 1 +24, class 3 +31, class 4 +4).
- Since `Harness.discovery` is frozen (`SPEC.md:28`), gate 3 cannot see the 59, and a candidate could trade facet-0 acceptances for other keys at no cost.
- Report both counts beside the gate. Do not redefine the rule without the founder.

### 6.6 The decoy levers the diagnosis measured

All figures here are [bound]: a lexical judge, emulated site verdicts (which reproduce every stored real verdict; S/r2/v2/rd_run_seed0.log:2-4 and seeds 1 to 5), perfect routing, seeds 0 to 5. None is a run of the real pipeline. Lever definitions: rules_decoys.py:22-33. Decoys are counted by the frozen rule; the any-facet re-score was not computed.

**On the real runs' own candidates** (shipped gate plus lever; T:20-31):

| lever | class 1 / 2 / 3 / 4 / 5 | total (run: 118) | recall_all |
|---|---|---|---|
| none | 6 / 37 / 4 / 41 / 30 | 118 | 0.388 |
| W4 (and W4k, W3k) | 6 / 37 / 4 / 0 / 30 | 77 | 0.388 |
| T2k | 6 / 24 / 4 / 39 / 30 | 103 | 0.388 |
| T2p | 5 / 3 / 4 / 39 / 30 | 81 | 0.388 |
| R4lb | 6 / 37 / 4 / 41 / 0 | 88 | 0.388 |
| R4lbcur (today's reader) | 6 / 37 / 4 / 41 / 15 | 103 | 0.388 |
| C1s | 0 / 13 / 0 / 33 / 16 | 62 | 0.367 |
| W4 + T2k + R4lb | 6 / 24 / 4 / 0 / 0 | 34 | 0.388 |

The levers only tighten the gate. On fixed candidates they cannot raise recall.

**On recall-raising configurations.** These use oracle candidates (every Gold key and every decoy's facet-0 key, at every askable window), every site asked, lineage assumed, and no budgets. "Pass" uses the pooled reading (total ≤ 118), the most lenient one. "Seeds over" counts the seeds above their own run baseline (TV:1).

| configuration | rule | levers | recall_all | class 1 / 2 / 3 / 4 / 5 | total | pooled gate 3 | line |
|---|---|---|---|---|---|---|---|
| G6, +14, fresh 42 | shipped | none | 0.458 | 6 / 47 / 6 / 53 / 36 | 148 | FAIL | TV:186 |
| G6, +14, fresh 42 | R3sim | none | 0.825 | 27 / 53 / 25 / 54 / 36 | 195 | FAIL | TV:231 |
| G6, +14, fresh 42 | R3sim | W4 | 0.825 | 27 / 53 / 25 / 4 / 36 | 145 | FAIL | TV:232 |
| G6, +14, fresh 42 | R3sim | W4 + R4lb | 0.825 | 27 / 53 / 25 / 4 / 0 | 109 | pass, 2 seeds over | NT:131 |
| G6, +14, fresh 42 | R3sim | W4 + T2k + R4lb | 0.825 | 27 / 44 / 25 / 3 / 0 | 99 | pass, 0 seeds over | TV:240 |
| G6, +14, fresh 42 | R3sim | W4 + T2k + R4lbcur | 0.825 | 27 / 44 / 25 / 3 / 16 | 115 | pass, 2 seeds over | TV:242 |
| G6, +20, no freshness | R3sim | W4 + R4lb | 0.829 | 27 / 53 / 33 / 4 / 0 | 117 | pass, 2 seeds over | NT:197 |
| G6+G12, +14, fresh 42 | R2 | W4 + R4lb | 0.808 | 33 / 52 / 32 / 2 / 0 | 119 | FAIL | NT:24 |
| G6+G12, +14, fresh 42 | R2 | W4 + W3k + R4lb | 0.808 | 32 / 52 / 32 / 1 / 0 | 117 | pass, 2 seeds over | NT:27 |
| G6+G12, +14, fresh 42 | R3sim | W4 + R4lb | 0.842 | 35 / 53 / 33 / 7 / 0 | 128 | FAIL | NT:32 |
| G6+G12, +14, fresh 42 | R3sim | W4 + T2k + R4lb | 0.842 | 35 / 44 / 32 / 6 / 0 | 117 | pass, 2 seeds over | TV:57 |
| nested, +14, fresh 42 | R2 | W4 + R4lb | 0.829 | 36 / 53 / 32 / 3 / 0 | 124 | FAIL | NT:222 |
| nested, +14, fresh 42 | R2 | W4 + W3k + R4lb | 0.829 | 35 / 53 / 32 / 2 / 0 | 122 | FAIL | NT:225 |
| nested, +14, fresh 42 | R2 | W4 + T2k + R4lb | 0.829 | 36 / 32 / 31 / 2 / 0 | 101 | pass, 1 seed over | TV:408 |
| nested, +14, fresh 42 | R3sim | W4 + R4lb | 0.842 | 40 / 53 / 33 / 9 / 0 | 135 | FAIL | NT:230 |
| nested, +14, fresh 42 | R3sim | W4 + T2k + R4lb | 0.842 | 40 / 32 / 32 / 8 / 0 | 112 | pass, 2 seeds over | TV:423 |

What these rows show and do not show:
- **No recall-raising rule passes alone.** With the shipped gate, oracle candidates already accept 141 to 170 decoys (TV:2, :63, :124, :185, :246, :307, :368, :429, :490).
- **The reading of gate 3 matters.**
  - Under a per-world reading (no seed above its own baseline), every passing row with a mean of 0.80 or more needs T2k or T2p. T2k's count artifact has unmeasured exposure. T2p pools sub-k counts across sites and is not permitted. Examples: TV:240 and TV:244 (R3sim with T2k); TV:43 and TV:409 (R2 with T2p) (W/rev/gate3_reading.out:1-40).
  - Every W4 + R4lb pass has 2 seeds over (NT:131, :197), and so does the W4 + W3k + R4lb pass (NT:27).
  - SPEC.md:3-5 forbids lowering a bar. The reading must be fixed by amendment before the candidate freeze (rank 4), and it may not be chosen to favour a candidate.
- **Position on the T2k critique.**
  - On G6, R3 passes the pooled reading without T2k: W4 + R4lb or W4k + R4lb give 109 at +14 and 117 without freshness, each with 2 seeds over (NT:131-132, :197-198).
  - On G6+G12, R2 passes without T2k with W4 + W3k + R4lb (117, 2 seeds over; NT:27). R3 there needs T2k: its W4 + R4lb and W4 + W3k + R4lb rows fail (NT:32, :35).
  - On nested windows only rows with T2k pass (NT:221, :229, :287). R2 with W4 + W3k + R4lb fails at 122 (NT:225).
  - T2k needs per-site position cells, a count artifact outside the closed list (`egress.py:77`), with unmeasured exposure. W3k needs a cross-site linkable identifier.
- **R4lb or R4lbcur is in every passing row with a mean of 0.80 or more** (R4lbcur at TV:242 and TV:410; W/rev/gate3_reading.out:11, :18). Both count per-site `'<k'` negation buckets at their lower bound. That is the presence mechanism that pins 1,750 sub-k site-weeks on nested windows with the cells withdrawn (attacks_exact.out:15); today's design pins 5,631 to 9,829 under the same attacker (Summary 6 (b)). No configuration is shown to pass this attacker and gate 3 together.
- **R3sim** needs shares of sub-k counts to leave a site, which today's rules do not permit. As modelled it has no minimum site count; with 2 sites required, R3 on G6 at +14 is 0.808 (S/r2/rv2check/r3_two_sites.out:4).
- **Classes 1 and 3 rise in every passing row with a mean of 0.80 or more**, from 6 and 4 (T:20) to 25-40 and 24-46 over the 40 rows of W/rev/gate3_reading.out:1-40 (NT:287 has class 3 at 46). The eight passing rows of the table above span only 27-40 and 25-33. Only C1s lowers them, and it drops recall to 0.433 with 0 rare patterns found on G6 at +14 (TV:239). The passes come from removing classes 4 and 5.
- **Not re-measured in round 2:** W2, W3, T1, T1m2tau, C1, in-window negation, any-newer-retraction, and naive expiration (S/DIAGNOSIS-r2-v2.md:961).
- **What the levers need** (S/DIAGNOSIS-r2-v2.md §6.4):
  - W4 flags at any confirming site.
  - W4k flags only at sites with ≥ k records; it gives identical rows in all 36 cells (TV:551).
  - W3k needs a cross-site linkable identifier.
  - T2p needs pooled sub-k counts.
  - R4lb needs a reader fix, extra questions within the budget, and a note-version downgrade in the fabric.

### 6.7 Summary by class

| Class | Accepted: report / any-facet (of 60) | Main mechanism | Label | Lever (real candidates) | Effect on decoys | Effect on recall |
|---|---|---|---|---|---|---|
| 1 entity coincidence | 6 / 30 | facets spread over several keys; no chain-identity check; facet-0 counting hides 24 | design weakness; measurement issue | C1s | 6 → 0 (T:28) | 0.388 → 0.367 |
| 2 temporal scramble | 37 / 37 | no order check; order invisible under one predicate per chain; real patterns unordered too; 21 never asked (no snapshot), 2 asked and `hypothesis` | design weakness; benchmark inconsistency | T2k; T2p | 37 → 24; 37 → 3 (T:24, :25) | none |
| 3 near-miss entities | 4 / 35 | other facets on other entities' keys; facet-0 counting hides 31 | measurement issue; design weakness | C1s | 4 → 0 (T:28) | 0.388 → 0.367 |
| 4 duplicate inflation | 41 / 45 | echoes count as independent roots; 3 to 5 single-event facets per window still meet roots ≥ 3 | defect; design weakness | W4, W4k, W3k | 41 → 0 (T:21-23) | none |
| 5 stale chain | 30 / 30 | confirm on any yes; contest only on a contributing refute; no re-check; negation window | design weakness; defect | R4lb; R4lbcur | 30 → 0; 30 → 15 (T:26, :27) | none |

Classes 2 and 4 are confirmed as accepted almost whenever asked (37 of 39, 41 of 41). The class-4 deduplication residue (6 of 9) and the class-5 retraction counterfactual are probable: each is one reviewer's simulation, not re-run. For classes 1 and 3, the facet-0 counting gap is confirmed and the mechanism probable (6.4).

## 7. Reproduction instructions

### 7.1 Rules for every command
- Work from the repository root, `/home/user/NeuralGraph`, and never write inside it. Outputs go under a scratch directory, `OUT` below.
- Export `PYTHONDONTWRITEBYTECODE=1` and run `python3 -B`. `python3 -I` (used by S/rc/D/scripts/analyze_seed.sh) ignores `PYTHON*` variables, so add `-B` there. One round-1 re-run left a `.pyc` in the repository (C16, confirmed).
- The real broker: `MYCELIC_NATS_SERVER_BIN=/root/.local/bin/nats-server E2E_REQUIRE_NATS=1`. The local binary is v2.10.22 (S/baseline_seed0.log:11); CI installs v2.10.29 (W/ci_run2…:652).
- Use seeds 0 to 5 only. Record `git rev-parse HEAD` and `git status --porcelain` before and after each run; the report records `run.code_commit`, `code_dirty_start` and `code_dirty_end`.
- Timings are not comparable between runs on this machine, because other sessions ran at the same time.
  - Load was 8.90 to 20.12 on 4 CPUs during D's run (S/rc/D/load_seed0.txt:1, :19).
  - Seed-0 main passes took 159.9 to 1,006.9 s with identical scored output (S/rc/A/round2/main_pass_times.out:1, :7-12).
  - Two orphaned probe processes loaded 2 CPUs from 2026-10-14 23:04 until 03:57 (LOOP-48H-STATUS.md:344-346 at 91ab29c; unverified, status log only).

### 7.2 The seed-0 local run

```sh
cd /home/user/NeuralGraph
export PYTHONDONTWRITEBYTECODE=1 MYCELIC_NATS_SERVER_BIN=/root/.local/bin/nats-server E2E_REQUIRE_NATS=1
S=/tmp/claude-0/-home-user-NeuralGraph/9160890b-ee51-58d5-92df-ff8899aa2f59/scratchpad/emergence80
OUT="$S/repro"            # any scratch directory outside the repository
mkdir -p "$OUT"
python3 -B -m research.e2e_ingest run --size 200 --mode process --out "$OUT/seed0"
python3 -B -m research.e2e_ingest check "$OUT/seed0/e2e-ingest-report.json"
python3 -B "$S/rc/C/scripts/compare_discovery.py" "$S/baseline_seed0/e2e-ingest-report.json" "$OUT/seed0/e2e-ingest-report.json"
```

This is CI's e2e step without `--suites suites --run-id …` (`.github/workflows/e2e-ingest.yml:181`). Expected:
- Exit 1, with `verdict pass=False checks_pass=True reason=suites_not_green`. Checks c0, c1, c2, c3, c4, c7, c8 and c9 are ok; c5, c6 and c10 do not run (S/baseline_seed0.log:23-36).
- 55 snapshot candidates, all asked; 47 supported, 7 hypothesis, 1 stale (:2, :7).
- Discovery (W/s1_reports.out:1, :20):
  - 15 true, 35 decoy and 5 background candidates;
  - found: p001 p006 p011 p015 p017 p019 p022 p024 p025 p033 p034 p036 p037;
  - `recall_all` 0.325, common 13 of 22, rare 0 of 18;
  - decoys: class 2: 5, class 4: 9, class 5: 3;
  - 47 supported notes.
- `check` prints "aggregates agree (0 problems)" (S/rc/C/logs/check_myrun.out:3). `compare_discovery.py` prints "discovery blocks identical (simulator seconds dropped): True" (W/compare_discovery.rerun.out:11). The report's sha256 always differs, because of timestamps.
- Runtime at ec72e22: 747 s wall (S/baseline_seed0.log:36), with a main pass of 164.2 s (W/s1_reports.out:1). Other full seed-0 runs took 681.4 to 1,110.1 s (:8-11).
- `--skip-drills` gives the same discovery block in 313.9 s (:7). But it skips c1 to c4, so it cannot certify privacy or recovery.

### 7.3 Seeds 1 to 5
- The plain command fails. `check_world_counts` (`emergence_world.py:223-230`, `:337`) compares each world with `WORLD_TABLE` (`:94`), which holds only seed 0's counts. Seeds 1 to 5 differ (S/rc/C/logs/world_seeds.out:2-6), so the run stops with `WorldError` (S/inv_baseline.md §6).
- The reviewers replaced that one function with a recorder, in their own process only:
  - (a) `python3 -B "$S/run_seed.py" run --size 200 --seed N --mode process --skip-drills --out "$OUT/dev_seedN"`: 315.4 to 328.5 s per seed at ec72e22 (W/s1_reports.out:2-6).
  - (b) `S/rc/D/scripts/run_seed_api.py --seed N --out DIR` (`harness.E2ERun`, `skip_drills=True`, `queries=0`). `run_seeds.sh` runs three at a time: 816.1 to 1,056.6 s per seed under that load (:12-17).
- Expected from both:
  - found 12, 17, 17, 19 and 15, with found sets and decoys equal between (a) and (b) (:2-6, :26);
  - c8 fails `world_is_section_3_1` (`report.py:437`), and c1 to c4 do not run, by construction (S/dev_seed1.log:15-22).
- These runs are development evidence and never count for the SPEC gate (SPEC.md:34-41, :142-146).

### 7.4 The probes behind the findings

Statuses are as in 4.0. Paths are under `S/rc/` unless they start with `S/`. Every probe only reads run directories or the generator. Runtimes are "not recorded" unless given.

- **Reproducer D** [real; `gate_capacity` is a bound].
  - Run: `bash D/scripts/run_seed0_instrumented.sh`, with the lag probe `D/hook/sitecustomize.py`. The run went from 22:53:44 to 23:05:07, `run.seconds` 681.4 (W/s1_reports.out:11).
  - Analysis: `D/scripts/compare_runs.py`, `lag_analysis.py`; `bash D/scripts/analyze_seed.sh SEED RUN_DIR OUT_DIR` (add `-B`) → `D/out/*_seed{0..5}.json`, `D/out/tables/*`.
  - Backs D-F1 to D-F15 (statuses in 4.0) and A14 (not cross-challenged). D's bound was validated against the real verdicts (`validation_equal` in D/out/gate_capacity_seed{0..5}.json).
- **Reviewer A** [real; local test].
  - E2e probes: `python3 -B A/scripts/run_e2e_probed.py OUT_DIR` (1,110.1 s under load, W/s1_reports.out:8), then `A/scripts/pipeline_transport_check.py`, `report_fabric_vs_hq.py`, `e2e_probe_summary.py`, `compare_runs.py`, `truncation_check.py`, `lab_imports.py`; and `A/round2/hq_mainthread_gaps.py`, `lag_site_phase.py`, `lag_service_and_rank.py`, `main_pass_times.py`.
  - Loop tests: `A/probe/run_probed.sh OUTDIR LABEL BURNERS [PYSPY] [CPUS]`, driven by `batch1.sh` to `batch4.sh` (env `NICE`, `LOOPPROBE_MODE`, `LOOPPROBE_PREWARM`). Probes: `loopprobe.py`, `watcher.py`, `procsampler.py`. Summaries: `summarize_loop.py`, `classify_stalls.py`. Micro-benchmark: `loop_ops_micro.py`. One pair of tests: "2 passed in 35.05s" (A/runs/loop/batch4/min_n15_b0_cold_r1.pytest.txt).
  - Backs A1 to A13.
- **Reviewer B** [bound over real stores; harness].
  - `B/trace_patterns.py`, `detect_probe.py`, `census2.py`, `decoy_probe.py`, `gate_counterfactual.py` (oracle-modified verdicts through the shipped gate), `negation_probe.py`, `dup_census.py`, `supported_breakdown.py`, `graph_census.py`, `tesseract_diag.py`.
  - `B/forced_probe.py OUT_DIR B/forced_spec.json` → `B/cf_tesseract/results.json`; `B/replay_shipped.py`.
  - `B/seed_harness.py SEED OUT_DIR`: 252.1, 376.1, 386.8, 382.2, 181.4 and 170.0 s for seeds 0 to 5 (`"seconds"` in B/seeds/seed{0..5}/seed_result.json). Then `B/cross_seed.py`.
  - `B/r2/cut_probe.py`, `capacity_split.py`, `found_dependence.py`, `detect_subcause.py`, `compare_B_vs_D_seeds.py`, and the other `B/r2/*.py`.
  - Backs B1 to B14. B's harness equals D's real runs (B/r2/compare_B_vs_D_seeds.txt:1-6).
- **Reviewer C** [generator; harness; bound; sim rows].
  - `C/scripts/world_seeds.py`, `world_density.py`, `accept_all_baseline.py`, `tesseract_counterfactual.py`, `compare_discovery.py`, `sim_figures.py`, `sim_ci.py`, `sim_dev_split.py`, `sim_register_depth.py`.
  - `C/r2/scripts/compare_accept_all.py`, `gate_bound_variants.py`, `one_site_sensitivity.py` (env `NOFRESH`, `NOROOTS`), `early_window_census.py`, `derived_totals.py`, `decoy_window_check.py`, `seed0_miss_decomposition.py`, and `cf_question.py` (seed 4 with the guard bypassed in-process).
  - Re-run timings: 0.03 to 0.92 s (W/rerun_timings.out:1-8). The simulator scripts read committed rows for simulator seeds 0 to 9, inside SPEC's development range; the orchestrator accepted this departure from the session's 0-to-5 rule (1.1).
  - Backs C1 to C18.
- **The diagnosis and its critics** [bound].
  - `S/r2/ceiling.py compute`, then `S/r2/ceiling.py`; `S/r2/decomp_r2.py`; `S/r2/rules_decoys.py SEED`, `tabulate.py`.
  - `S/r2/v2/{rules_decoys_v2, tab_v2, grid_v2, coarse_v2, cross_v2, attacks_v2, a003_v2}.py`; `S/r2/disclosure/attacks.py`; `S/r2/crit3/{attacks_exact, rd_noT2k, tab_noT2k}.py`; `S/r2/rv2check/*.py`.
  - The last critics re-ran ceiling.py, grid_v2, coarse_v2, tab_v2, cross_v2 and rules_decoys_v2 byte-identically (S/synth/r2_critiques.json).

### 7.5 This report's own re-checks
- Earlier checks:
  - `W/s1_reports.py` → `W/s1_reports.out`: the sha256, commit, flags, timings and scored figures of all 17 real-run reports.
  - `W/s7_code_facts.out`, `W/s5_code_facts.out`: code-identity diffs and the cited code lines.
  - Eight of C's scripts re-run byte-identical (`W/*.rerun*.out`; timings in `W/rerun_timings.out`).
  - `W/capacity_sums.py`, `decoy_class_table.py`, `decoy_names_seed0.py`, `s10_decoy_classes.py`, `s5_stall_numbers.py` and `sim_g_hier.py` write the matching `.out` files. Section 2's helpers are in `W/path/`.
- CI tails through the job-log API: `W/ci_run1…`, `W/ci_run2…`, `W/ci_mycelic_timing_tails.txt`, `W/ci_artifact_lines.txt`, `W/ci_mycelic_green_runs.txt`.
- The first revision (`W/rev/`):
  - `head_check.out`: HEAD ef10579 and the files changed since 7639bec.
  - `ci_runs_now.out`, `ci_run3_jobs.out`, `ci_run3_tail_facts.txt`, `ci_run3_err.txt`: runs 38664599267 and 38667030739, read through `gh api` and the job-log API.
  - `coarse_w06_r0.py` / `.out`: a critic's copy of coarse_v2.py with R0 and R2 in the W06+ regimes. Re-run in 10.2 s, byte-identical to `W/integrity_critic/coarse_w06_r0.out` (`coarse_w06_r0.time`).
  - `decoy_stage8.py` / `.out`: decoys alive after site verification, from D's real-run tables.
  - `challenge_matrix.out`, `challenge_tally.out`, `challenge_texts.out`: the cross-challenge, from rc_result.json.
  - `gate3_reading.out`: passing rows with a mean of 0.80 or more, and their seeds over, from TV and NT.
  - `a003_freeze_check.out`: where A003's run was recorded at ef10579 (superseded by `W/rev2/a003_record.out`).
- This revision (`W/rev2/`):
  - `head_now.out`: HEAD 6c0c322, then 91ab29c; the files changed since 7639bec; the status log's line numbers; 0 porcelain lines (:1, :49).
  - `a003_record.out`: the lines that record A003's run at 6c0c322 (CHOICE-A003, RESULTS, the YC brief, the status log), the re-hash of `run-1-report.md`, and the handoff and A004 lines cited in 5.3.
- After this revision, `S/synth/fix/head_9cfa2e9.out`: HEAD 9cfa2e9, its diff against 91ab29c, the empty diff under `research/`, `mycelic/`, `NeuralGraph/`, `tests/` and `.github/` since 7639bec, 0 porcelain lines, and the A004 lines of 5.3 at 9cfa2e9.
- The repository had 0 porcelain lines after every re-check (W/rev2/head_now.out:1, :49).

### 7.6 What cannot be reproduced from here
- The CI discovery blocks and artifacts (1.3).
- A stack sample of the CI loop stall (none exists).
- Event-loop lag on an uncontended machine. By the status log's account, which is unverified, all local samples before 03:57 ran under extra load (5.1).
- Process-mode confirmation of the harness counterfactuals.
- Seeds 1 to 5 with drills.
- Preliminary and held-out worlds (never generated; SPEC.md:92).
- The runtimes of most offline probes.

## 8. Ranked corrective actions

### 8.0 Rules for this section
- **Nothing here is built without the founder's explicit yes to that rank.** This report treats no status-log line as approval of any rank (Status). A decision recorded after this text was assembled covers W ranks; see the note added in Status. After a yes:
  1. write the test first (9.1);
  2. make one approved lever per commit;
  3. measure it with the before/after protocol of section 10;
  4. ablate one lever at a time, then combined (SPEC.md:122-124).
- **What no action does:** lower k = 3 (SPEC.md:119); move raw records off a site, or read raw notes centrally (SPEC.md:120); edit the frozen sources (SPEC.md:23-29); lower a SPEC bar (SPEC.md:3-5).
- **Benefits are bounds, not forecasts.** Each is given with its model, window set, seeds 0 to 5 and counting definition. No action is claimed to reach a recall figure.
- **Kinds.** (a) defects that can be fixed; (b) design changes that need a founder decision; (c) measurement and SPEC fixes.
- **Approval labels.** The status log records an approval of "the EMERGENCE fixes that stay within the current rules", while "choices that change an invariant still wait on the founder" (LOOP-48H-STATUS.md:317-320 at 91ab29c). The labels below say which side of that line each rank falls on. None of them stands for approval.
  - **W, within the current rules:** the rank changes no SPEC invariant (SPEC.md:119-121), no founder default (HANDOFF-PROMPT-2026-10-15.md:179-186), no Boundary control (k, window length, budgets, the closed artifact list, the site clock), no gate rule, no SPEC bar and no CI gate. A W item also needs its zone's conditions below.
  - **S, needs a dated SPEC amendment:** SPEC.md:142-147 says these wait on the founder's approval. Whether the recorded approval covers an amendment is not established; ask.
  - **N, not covered:** the rank changes a founder default, a Boundary control, a gate rule or what crosses the Boundary, or it relaxes a CI gate or a regression test. R1, R2, R3, pooled sums, withdrawing the weekly cells, a raised budget, new count artifacts, a Boundary window set and a looser loop-test bound are all N.
- **Cost.** No action needs a 10,000- or 50,000-user run. A full seed-0 real run took 681.4 to 1,110.1 s locally (W/s1_reports.out:8-11).
- **Zones** (S/DIAGNOSIS-r2-v2.md §8.4):

  | Zone | Files | Lab (A003, A004) | c7 pin |
  |---|---|---|---|
  | 1 | `research/e2e_ingest/**` and its pack, outside the frozen parts | not imported | not hashed |
  | 2 | `research/routing_spike/*.py`, parts of `NeuralGraph/`, `mycelic/*.py` | not imported | hashed |
  | 3a | `pushdown/gate.py`, `edge/egress.py`, `detect/detectors.py` and others | imported by every lab process; not pinned | hashed |
  | 3b | `edge/records.py`, `edge/verify.py`, `edge/extract.py`, `packs/loader.py` | in j4's code hash (`lab/j4.py:183-184`); A004's rule names `verify.py`'s judging functions as pinned, unchanged, and builds j5's code hash on j4's (CHOICE-A004.md:156-159, :1385-1386) | hashed |

  - An edit in zone 2, 3a or 3b fails c7 (`report.py:77-78`, 142 files) unless the pinned hash is updated in the same approved commit (dec. 14).
  - No CI-triggering push is made while lab model shards run (HANDOFF-PROMPT-2026-10-15.md:66).
- **A003 now.** A003's run is recorded at 6c0c322, and the status log reads that as "recorded and frozen". The handoff's freeze asks for "the report blocks rebuilt from the logs with their sha256 checked" (HANDOFF-PROMPT-2026-10-15.md:150-151), and the sha256 of the `report.json` block is unverified (5.3). **Whether A003's record still gates any rank is a question for the founder; this report does not settle it.** If the founder accepts the status log's reading:
  - Zone 3a edits need dec. 14 and the no-push rule above.
  - Zone 3b edits also need **dec. 15, restated for A004**: may `verify.py`, `records.py`, `extract.py` or `loader.py` change before A004's judging run? A004's amendment is committed at 9cfa2e9 and does not address zone 3b edits (5.3). This report does not decide this.

### 8.1 The ranked list

Ranked by what each action unblocks. Ranks 1 to 6 come first, because no before/after is credible without them.

| Rank | Action | Kind | Label | Root causes (status) | What it unblocks | Decision |
|---|---|---|---|---|---|---|
| 1 | Pin the scoring sources | (c) | W | SPEC.md:31 unmet (confirmed by search) | every later before/after | founder's yes |
| 2 | Replace the world-count guard | (c) | S | C1, D-F15 (confirmed) | shipped runs of other development seeds; held-out evaluation | dec. 13 |
| 3 | Freeze the unfrozen settings | (c) | S | C7 (confirmed) | comparable pairs | amendment |
| 4 | Fix gate 3's reading; report decoys on every facet key | (c) | report: W; reading: S | B9, D-F6 (confirmed); C18, C3 | a gate 3 that sees classes 1 and 3 | dec. 10, 12 |
| 5a | Report hidden losses, controls and benchmark limits (HQ-side data only) | (c) | W | D-F1 and C4 (counts confirmed); B11, D-F12 (cut), C3 (confirmed); C9 (lexicon, ids confirmed) | seeing where each change acts | founder's yes |
| 5b | The claim-derived bound and found-dependence, as an offline script outside the `run` job | (c) | S | C4, C17; B14 (not cross-challenged), C12 (mechanism confirmed) | a ceiling and an evidence check beside each figure | SPEC.md:120 ruling |
| 6 | Make CI's discovery block readable | (c) | W | C6, C8 (confirmed) | CI attribution | founder's yes; download |
| 7 | Decide the candidate source and the W12 cold start | (b) | N | B1, D-F2 mechanism, C17 (confirmed); D-F13 | up to 43 patterns supportable only before W12 [bound, Model 1]; a precondition of every W06+ path | dec. 1, 6 |
| 8 | Keep own earlier facets from masking a pattern in the D2 baseline (a detector design change) | (b) | W only for a label-free form, which is not designed | B2 (seed 0 confirmed; separate cause probable) | up to 9 detector-silent patterns with a self-baseline site [bound, oracle Gold attribution; harness at seed 0] | founder's design choice; dec. 14 |
| 9 | Count echoes of one event as one root | (a) | per-site root rule: W; global deduplication, W4/W4k: N | B6, D-F4 (confirmed) | decoy room for any recall change (class 4) | dec. 11, 14, 15 |
| 10 | Read negations correctly | (a) | W | B5 (confirmed) | the class-5 lever R4lb | dec. 15 if in `extract.py` |
| 11 | Ask again while a candidate persists | (a) | W | B4, D-F7 (confirmed) | p028 [harness only]; the re-ask that R4lb needs | dec. 14 if in `hq.py` |
| 12 | Single confirming site (R1) | (b) | N | B3, D-F3 (mechanism confirmed) | R1 paths above 0.80 without W12 [bound, Model 2] | dec. 9 |
| 13 | Enforce a window set in the Boundary; thresholded answers | (b) | N | the Boundary admits any closed window ≥ 6 weeks (confirmed in code); exposure figures [bound], probable | the privacy condition of any longer-window or pre-W12 path | dec. 4, 5; dec. 15 for the (e) format |
| 14 | Timing, freshness, budget, site clock | (b) | +14 and freshness: S; budget increase, site clock: N | 42-day edge [bound], probable; budget effect on a sweep [bound], probable | patterns at the 42-day edge [bound] | dec. 2, 3, 16; budget |
| 15 | Decoy levers that need new artifacts | (b) | N | B7, B8 (confirmed) | gate-3 room on longer windows [bound] | dec. 11, 12 |
| 16 | Count weak confirms (R2) | (b) | N | B3 (mechanism confirmed) | R2 paths above 0.80 [bound, Model 2] | dec. 7 |
| 17 | Cross-site sub-threshold sums (R3, T2p, W3k) | (b) | N; not permitted today | B3 | pooled rare evidence [bound] | dec. 8 |
| 18 | Show and settle the `top_n` cut | (a) | S | D-F12, B13 (cut confirmed; lost find probable) | at most one find, [harness] only (s4 p023) | with rank 3 |
| 19 | Wire timeout, stderr, `wait_idle`, list caps | (a) | W | A8, A10, A11 (confirmed; not triggered) | liveness only | dec. 14 |
| 20 | Loop timing tests that diagnose; the stall fix after its design | (c) | added assertions: W; any looser bound: N | A3, A5 (confirmed); A2 (probable) | a red test that names its cause; no gate relaxed without approval | yes to (a); a separate yes to (b); (c) with dec. 14 |
| 21 | Correct false statements in committed documents | (c) | W | C13, C11, C5 (confirmed) | readers stop meeting withdrawn claims | founder's yes |
| — | Not proposed (8.5) | — | — | D-F11, C10, B10, C2, A14, B12, A12 | — | — |

### 8.2 (c) Measurement and SPEC fixes

**Rank 1. Pin the scoring sources.** Label W.
- *Changes.* A new test, `tests/e2e_ingest/test_spec_freeze.py` (9.2). Tests only; no pin hashes `tests/`.
- *Addresses.* SPEC.md:31 promises this test, but no test at 7639bec names SPEC.md, EMERGENCE-80 or ce79909 (W/s5_code_facts.out:309-310).
- *Expected benefit.* None on recall. Any later edit to the generator, the labels, the scorer, or anything the scorer calls fails CI. The frozen files are byte-identical from ce79909 to 7639bec (:311-317).
- *Risks.* None for privacy, decoys or A003. A pin on `Harness.discovery` alone would miss its callees, so the test pins them too (9.2).
- *Verification.* A one-byte mutation of each pinned source and each pinned callee must fail the test (9.1).

**Rank 2. Replace the world-count guard.** Label S.
- *Changes.* `check_world_counts` and `WORLD_TABLE` (`emergence_world.py:94-101`, `:223-230`, `:337`); c8's `world_is_section_3_1` (`report.py:437`). Zone 1. `generate` and `gold_labels` do not read the table.
- *Addresses.* C1 (high, C) and D-F15 (low, D); confirmed.
- *Expected benefit.* None on recall. The shipped `run` accepts the other development seeds. Without this change, every held-out world scores 0 (SPEC.md:70).
- *Risks.* The guard is today the only check that a world matches §3.1; the rank-1 pins can carry that check. The fix must be inside the frozen candidate, and no held-out count may be tabulated before the freeze (SPEC.md:92).
- *Verification.* 9.5. Before/after: the seed-0 discovery block stays identical, and seeds 1 to 5 under the shipped command give the bypass runs' found sets and decoys (W/s1_reports.out:2-6, :26).
- *Decision.* Dec. 13, as a dated amendment (SPEC.md:142-147; HANDOFF-PROMPT-2026-10-15.md:183-184).

**Rank 3. Freeze the settings SPEC does not freeze.** Label S.
- *Changes.* A dated amendment listing the values of 10.2, and `tests/e2e_ingest/test_settings_pins.py` (9.2).
- *Addresses.* C7 (confirmed).
- *Expected benefit.* None on recall; it fixes what "supported" means. [bound, Model 1, every claim] Pack values move the bound between 0.4542 and 0.8542 (gate_bound_variants.out:7; one_site_sensitivity.out:8). The upper end needs one counted site and weeks before W12, which are founder decisions (ranks 12, 7). The pooled 1.0 is not a pack value.
- *Risks.* A frozen value changes only by a dated amendment approved before the candidate freeze, and never to lower a bar.
- *Verification.* The settings test fails on any pack edit made without the pinned update.

**Rank 4. Fix gate 3's reading, and report decoys on every facet key.** Labels: the report figure W; the reading S.
- *Changes.* A second decoy figure beside `decoy_accepted_by_type`, computed outside `Harness.discovery` from the fabric's notes and the labels (zone 1, `report.py`). A dated amendment fixing gate 3's reading (pooled, averaged or per world) and its baseline (dec. 10).
- *Addresses.* B9, D-F6 (confirmed); C18 (not cross-challenged); C3.
- *Expected benefit.* None on recall. [real, re-scored] 118 against 177 over seeds 0 to 5; the gap is class 1 +24, class 3 +31, class 4 +4 (6.2).
- *Risks.* Making the re-score gating would be a new, stricter bar, which needs its own amendment. The reading may not be chosen to favour a candidate (6.6: under a per-world reading, every passing row with a mean ≥ 0.80 needs T2k or T2p).
- *Verification.* The facet-0 test of 9.4; `test_spec_freeze.py` still passes.

**Rank 5a. Report the hidden losses, the controls and the benchmark's limits.** Label W.
- *Changes.* Zone 1 report additions, outside `Harness.discovery`, computed only from data HQ already holds (candidates, verdicts, notes):
  - the rule-only keys dropped at `emergence_world.py:289`;
  - the `top_n` cuts, with rank and score;
  - the accept-all control on the same candidates;
  - a "benchmark limits" note naming the pack's lexicon source, the structured entity ids and the evidence density (C9).
- *Expected benefit.* None on recall. At seed 0 it would have shown [real] 484 rule-only rows dropped (S/rc/B/rule_census_run0.txt:1), and [real, re-scored] accept-all at 15 of 40 against the system's 13 (S/rc/A/round2/accept_all_check.out:1-2).
- *Risks.* None for privacy: nothing new is read from a site.
- *Verification.* Items 2 and 6 of 9.3; the limits note in 9.5.

**Rank 5b. The claim-derived bound and found-dependence, offline.** Label S.
- *Changes.* An offline script outside the `run` job and outside every product path. It is never fed to HQ or to any decision (C14), and it is labelled offline. It prints D's claim-derived bound (every claim, at any week and at weeks ≥ W12) and each found pattern's found-dependence (B14) beside the run's report. It works from the generator's corpus run through the pack's extraction, not from site stores.
- *Expected benefit.* None on recall. [bound] At seed 0: 23 at any week and 18 at weeks ≥ W12 (W/path/capacity_sums.out:1, :3); and the three rare finds at seeds 2 and 4 that rest on records not their own (found_dependence_0_5.txt:44, :71, :76).
- *Risks.* Both figures need every record's claims in one place. SPEC.md:120 says "Raw records never leave their site, and nothing reads raw notes centrally". Whether a benchmark-side script over the synthetic generator's corpus falls under that sentence is a founder ruling. Until the founder rules, nothing is built. During this diagnosis, D's method read the site stores of local runs after those runs ended.
- *Verification.* 9.3 item 6, if the founder rules for it.

**Rank 6. Make CI's discovery block readable.** Label W.
- *Changes.* `.github/workflows/e2e-ingest.yml` and the e2e entry point write `recall_all`, the found ids, decoys by class under both rules, and the report's sha256 to the job summary and to the last lines of the log. The founder downloads artifacts 11830620985, 11931284874 and 11943396169 (1.3).
- *Addresses.* C6, C8 (confirmed).
- *Benefit.* None on recall; CI figures become citable (10.9).
- *Risks.* None for privacy.
- *Verification.* 9.5.

**Rank 20. Loop timing tests that diagnose; the stall fix after its design.** Labels: (a) W; (b) N; (c) zone 2 with dec. 14.
- *Changes.*
  - (a) Keep both wall-clock bounds (`tests/mycelic/test_api.py:395-397`, `:499-501`) as they are. Beside them, add a loop-thread CPU assertion and the /proc split in the failure message (9.7). The CPU assertion catches a walk or other CPU work moved onto the loop. **It cannot fail on A2**, where the loop waits for the lock or a CPU and adds no loop-thread CPU. So it is not the stall's regression test.
  - (b) Only if the founder wants the flake gone before the mechanism is fixed: make the wall-clock bound load-aware, for example half of the median in-window walk wall time instead of one walk timed alone before the window. **This relaxes a CI gate.** Its failure rate under A's conditions is not measured.
  - (c) After the design, the probable lever for A2 is how walks run (`mycelic/service.py:1950`), not `mycelic/retrieval.py` (5.1). Its regression test is the kept wall-clock responsiveness assertion plus the 9.7 benchmark.
- *Addresses.* A3, A5 (confirmed); A2 (probable); A4.
- *Expected benefit.* None on EMERGENCE recall or decoys (A6). [CI] The red runs overran by 6.5 to 16.4 ms (W/s5_stall_numbers.out:1-3).
- *Risks.*
  - (b) can turn a red gate green with no stall fixed.
  - (a) and (c) must be measured on the quiet machine. The status log says the local samples before 03:57 ran under the orphaned probe load (unverified; 5.1).
  - `mycelic/*.py` is under c7. The stall work is "not presumed to be in retrieval.py" (LOOP-48H-STATUS.md:318-319 at 91ab29c).
- *Before/after.* Run the loop tests under A's probe conditions on the quiet machine, then end-to-end run 3 against run 2 (10.9).

**Rank 21. Correct false statements in committed documents.** Label W.
- *Changes.* Dated correction notes, not rewrites of history:
  - HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md:128 ("ceiling 0.55 under k = 3"; the header at :11-17 covers only :51-54) and :71 (simulator "executed and measured");
  - E2E-INGEST.md:274-275 and BUILD-E2E.md:680 ("by construction");
  - LOOP-48H-STATUS.md:113 (0.325 "against" a simulator 0.475), by an appended status line.
- *Addresses.* C13, C11, C5 (confirmed).
- *Benefit.* None on recall.
- *Risks.* None.
- *Verification.* A docs test that finds each withdrawn phrase only inside a correction block (9.5).

### 8.3 (a) Defects that can be fixed

None of these changes a privacy rule. In no measurement does any of them alone raise recall much; every recall-raising path needs room under gate 3.

**Rank 9. Count echoes of one event as one root.** Labels: a per-site root rule W (zone 3b; dec. 14 and dec. 15 restated); global deduplication N (it needs a cross-site linkable identifier, like W3k); the W4/W4k flag N (a new per-site flag with unmeasured exposure; dec. 11).
- *Changes.* Either the root rule in `edge/records.py:25-28` and the verdict body in `edge/verify.py`, or W4k: a site is not counted when its records are at least twice its near-duplicate clusters, with the flag computed only at sites with ≥ k records.
- *Expected benefit.* [bound, oracle event ids] Deduplication rejects 2 (per site) or 3 (global, label N) of the 9 seed-0 class-4 acceptances (gate_counterfactual_run0.txt:41-44; probable). No text-based root rule was measured. W4 on the real candidates: class 4 41 → 0 and total 118 → 77, with recall unchanged at 0.388 (T:21). W4k gives identical rows in all 36 cells (TV:551).
- *Risks.* W4 depends on the generator's echo text (`corpus.py:218-222`). Its effect on real text, and its exposure, are not measured. The files are named pinned by A004's rule (8.0).
- *Verification.* The class-4 test of 9.4; the decoy table of 10.6 under both rules; recall reported, not assumed.

**Rank 10. Read negations correctly.** Label W (a pack cue in zone 1, or `extract.py` in zone 3b with dec. 15 restated).
- *Expected benefit.* [real] 135 of 196 negated rows are stored as affirmed at seed 0 (negation_probe_seed0.txt:9). [bound] Alone it changes no seed-0 gate status (probable). It is what separates R4lb (class 5 30 → 0) from R4lbcur (30 → 15) (T:26-27).
- *Risks.*
  - Recall can fall: s4 p022's find rests partly on one misread row (found_dependence_0_5.txt:76).
  - Like W4, this fix is shaped by how the generator writes retractions: the corpus appends "not" at the end (`corpus.py:223-224`). So it may lift the synthetic benchmark more than it would help on real text. Its effect on real text is not established (C9).
- *Verification.* The negation test of 9.4.

**Rank 11. Ask again while a candidate persists.** Label W (zone 1, or `hq.py` in zone 2 with dec. 14). Its exposure is measured by 10.7 before it enters a candidate.
- *Changes.* The main pass (`harness.py:509-512`, outside `discovery()`) and the question's as_of in `hq.py`. Where the change belongs is not established (S/DIAGNOSIS-r2-v2.md §8.5).
- *Expected benefit.* [harness] Seed-0 p028 is `hypothesis` at W13-W14 and `supported` at W15-W18. [bound] 1 such pattern over six seeds.
- *Risks.*
  - It re-asks decoys too (not measured).
  - The budget is 5 questions per entity per day, with 0 refusals today (W/s5_budget_refusals.out:1-6).
  - Repeated answers on overlapping windows feed the differencing attack.
  - The fabric must carry a second conclusion version (not established).
- *Verification.* 9.3 item 4.

**Rank 18. Show and settle the `top_n` cut.** Label S (a setting; part of rank 3).
- *Expected benefit.* [real] Two true candidates were cut over six seeds (first_failing_stage_by_seed.txt:6). [harness] s4 p023 returns `supported` at W17 on aux-attached background support (cf_seed4.out:2; capacity_split_0_5.txt:33); s5 p007 returns `hypothesis` (cut_probe_seed5.txt:3).
- *Risks.* More candidates asked means more decoys asked.
- *Verification.* 9.3 item 2.

**Rank 19. Wire timeout, site stderr, `wait_idle`, list caps.** Label W (zones 2 and 1; dec. 14).
- *Changes.*
  - A read timeout that closes the endpoint after it fires, with a stderr tail in the error (`wire.py:223`, `:226-238`).
  - `wait_idle` also checks `num_ack_pending` (`drills.py:375-380`).
  - Capped list calls raise or page (A11).
  - Optionally, a concurrent fan-out (`core.py:409-410`).
- *Expected benefit.* None on recall or decoys ([real] 175 of 175 answered; seven identical seed-0 runs). A latent hang becomes an error. The fan-out's latency gain is not measured.
- *Risks.* A timeout that keeps the pipe open hands the late answer to the next question (unverified design note, 5.2).
- *Verification.* 9.6; the seed-0 decision rows stay identical (`S/rc/A/scripts/compare_runs.py`).

### 8.4 (b) Design changes that need a founder decision

All are labelled N except where stated. This report recommends none of ranks 12 to 17.

**Rank 7. The candidate source and the W12 cold start.** Options: shorten the warm-up (`emergence_pack.py:72-73`); route rule-layer hits (`emergence_world.py:289`); or run a non-adaptive sweep over a fixed window set with the weekly cells withdrawn.
- *Benefit* [bound].
  - Model 1, shipped gate, every claim: 0.4542 at weeks ≥ W12, 0.6333 at any week; 43 patterns supportable only before W12 (derived_totals.out:2).
  - Routing every rule-only key supports at most 55 of 138 at any week, and 14 at weeks ≥ W12 (rule_only_recovery.txt:7).
  - [harness] p012 at W08, p013 at W04, p014 at W02 and p039 at W06 return `supported`.
  - Model 2 with the shipped rule over W06+ windows: at most 0.708 (W/rev/coarse_w06_r0.out:105). The paths above 0.80 also need rank 12 or 16.
- *Risks.*
  - Gate 3: no lever set was evaluated for any pre-W12 configuration. Raw timed decoys on the grid from W01 are 192, 209, 247 and 253 of 300 under R0 to R3 (ceiling.out:144-147), against 118. How many decoys a cold-start change adds is not established (4.2).
  - The soundness of pre-W12 hits is not established.
  - Precision: 403 of the 484 seed-0 rule-only keys were background.
  - Budget: a sweep asks 8 predicates per entity per window, against 5 per day. R3 on G6 at +14 falls from 0.825 to a mean of 0.805.
  - Privacy: on the grid from W01, answered once, 0 pinned with the cells withdrawn and 2,904 with weekly cells (attacks.out:43, :49).
- *Verification.* 9.3 items 1, 2 and 4(b); real runs on seeds 0 to 5, recording the candidate source, the window set and the budget refusals.

**Rank 8. Keep a pattern's own earlier facets from masking it in the D2 baseline.** A detector design change (zone 3a; dec. 14; no push while lab shards run). Label: W only for a label-free form, and none is designed. B's round-2 revision says the D2 test works as written and fails because the chain-level key spreads one pattern over up to 13 weeks. C answered "partly": the detector cannot exclude records it cannot attribute, so this is the warm-up's root (S/rc/rc_result.json `second`; 4.0).
- *Changes.* The D2 burst baseline in `mycelic/collective/detect/detectors.py`. Taken literally, keeping a pattern's own facets out needs pattern attribution in the decision path, which C14 and 9.1 forbid. A deployable form would have to replace the key's own-history baseline. Nobody has designed or measured one, and a pack-only form is not established.
- *Expected benefit.* [bound, oracle Gold attribution] With the pattern's own W01-W06 facets removed by Gold attribution, each of the five self-baseline sites exceeds the D2 threshold (S/rc/C/r2/logs/d2_arith.out:1-5; own-facet counts from S/rc/B/baseline_contamination_run0.txt:2-3, :5, :8-9), and [harness] p000, p023 and p032 return `supported` at W12. B counts 10 detector-silent patterns over six seeds (cross_seed_0_5.txt:19). This cause applies to at most 9 of them: seed-2 p012 has no self-baseline site (detect_subcause.txt:11-12, :28). No label-free form has a measured benefit.
- *Risks.* No counterfactual detector run exists, and the new alerts on decoy and background keys are not measured. Every lab process imports `detectors.py`.
- *Verification.* 9.3 item 3, once the founder chooses a design. Before/after: real runs on seeds 0 to 5, with the per-pattern first-loss stage and decoys under both rules (10.5, 10.6).

**Rank 12. A single confirming site (R1).** Set `min_confirming_sites` 2 → 1 in `questions.json` (zone 1). It is a pack value, but it removes two-site corroboration, the founder's default invariant (HANDOFF-PROMPT-2026-10-15.md:179-180).
- *Benefit* [bound].
  - Model 1, every claim: 0.8417 at any week, 0.6417 at weeks ≥ W12, 0.8458 without freshness.
  - Model 2, W12+: 0.575 on G6, at most 0.775 (CV:422).
  - Model 2, W06+: nested from W01, 0.854 at +14 and 0.863 at +20 without freshness (CV:526, :528); every admitted window from W06, 0.892 at +20 and 0.896 at +14 (CV:543, :545).
  - Counting own records: 0.750 on nested windows (CV:535), and at most 0.775 in any W06+ set (CV:554). The margin above 0.80 rests on support from records that are not the pattern's own (C12).
- *Risks.*
  - Gate 3: with oracle candidates on G6+G12 at +14, 177 decoys with no lever and 78 with W4 + T2k + R4lb, at recall 0.675 (TV:18, :27). No W06+ lever set was measured (raw 209 of 300).
  - Privacy (lower bounds):
    - (e) answers on a superset of the nested windows, with no weekly cells, pin 0 (attacks_v2.out:27-29). That needs an enforced window set (rank 13), the cells withdrawn (dec. 6), and the (e) format in `verify.py` (zone 3b).
    - With weekly cells: 2,908 (attacks.out:78-81).
    - Today's bucketed verdicts, cells withdrawn: at least 1,750, computed without W01-W06 (attacks_exact.out:15).
    - Over every admitted window: 9,127 (attacks.out:25). That is also today's exposure on what today's Boundary admits, under any gate rule: the same verdicts pin 9,829 with today's weekly cells (:19), and today's design, modelled as its cells plus sliding verdicts as the detector asks, pins 5,631 (:31). Pin 0 is a proposed bar that today's design does not meet.
    - Membership exposure is not measured.
- *Verification.* 9.3 item 5; 10.7 on the exact transcript.

**Rank 13. Enforce a window set in the Boundary, and threshold the answers.** The Boundary refuses windows outside an approved set aligned to ISO week 1 (`egress.py:553-556`, zone 3a), and HQ asks only that set (`pushdown/questions.py:55-63`; `hq.py`). Optionally, the (e) answer form (`verify.py`, zone 3b; dec. 15 restated).
- *Benefit:* privacy [bound, a lower bound]. Today 231 windows per key are admitted (attacks.out:4). Verdicts on all of them pin 9,127 of 20,836 sub-k site-weeks without cells, and 9,829 with them (:25, :19). The grid answered once, without cells, pins 0 (:49). Alignment matters: other G6 offsets give R3 0.463 to 0.700 (ceiling.out:157).
- *Risks:* longer windows add differencing (R2 on G6+G12 and nested, cells withdrawn: 906 and 1,750; attacks_exact.out:9, :15). Decoys rise on longer windows (6.6).
- *Verification:* 10.7.

**Rank 14. Timing, freshness, the per-entity budget and the site clock.** Labels: +14 timing and `freshness_days` S; a budget increase N; an independent site clock N (a Boundary control, even though it tightens it).
- *Benefit* [bound, Model 2, G6].
  - Shipped rule: 0.421 at +20, 0.458 at +14 (GV:10, :38).
  - R3: 0.771 at +20; 0.825 at +14 or with freshness 48 (GV:14, :42, :68).
  - Under the budget, an R3 sweep at +14 has a mean of 0.805; at +20 with freshness 49 it holds 0.825 in all 56 orders (budget_sweep.out:2, :6). Cell-targeted R3 stays at 0.825 (budget_targeted.out:7).
  - 13 patterns sit at the 42-day edge (GV:84-111).
- *Risks.*
  - Decoys rise: R3 oracle on G6 gives 186 at +20, 195 at +14 and 203 without freshness (GV:175, :185, :180).
  - Raising `question_budget_per_entity_per_day` (`egress.json`) relaxes a Boundary control that limits per-key question volume. Covering every key and window takes 277,200 questions per site, against 750 a day (attacks.out:6). So any increase needs 10.7 first.
  - Budgets hold only against an honest HQ while the site clock follows HQ's as_of (attacks.out:7).
- *Verification.* 9.3 item 4(b).

**Rank 15. Decoy levers that need new artifacts or rules.** T2k needs position cells, a new count artifact. R4lb needs a negation-correct later re-ask (ranks 10 and 11) and a note-version downgrade. And C1s.
- *Benefit* [bound].
  - On the real candidates: 118 → 103 (T2k), 88 (R4lb), 62 (C1s, at recall 0.367), and 34 (W4 + T2k + R4lb) (T:20-31).
  - R3 on G6 at +14 passes the pooled reading with W4 + R4lb at 109, 2 seeds over (NT:131).
  - On G6+G12, R2 passes with W4 + W3k + R4lb (NT:27), while R3 needs T2k (NT:31 against :32, :35).
  - On nested windows, only rows with T2k pass (NT:221, :229, :287).
- *Risks.*
  - T2k's exposure is not measured.
  - R4lb uses the presence mechanism that pins 1,750 on nested windows. For comparison, today's design pins 5,631 to 9,829 under the same attacker (Summary 6 (b)).
  - Order levers reject real patterns (order_check.out:1-6).
  - A naive retraction rule contests 6 of the 13 seed-0 finds (probable).
  - Classes 1 and 3 rise in every passing row.
- *Verification.* 9.4.

**Rank 16. Count weak confirms (R2).** Change `pushdown/gate.py:229`, `:235` (zone 3a). This reverses architecture decision D1 (`ARCHITECTURE.md:1396-1399`). It sends nothing new across the Boundary. It keeps two confirming sites, but not k records at each.
- *Benefit* [bound, Model 2].
  - W12+: on G6, 0.700 at +20, 0.746 at +14 and 0.750 without freshness (GV:12, :40, :26). 0.808 on G6+G12 and 0.829 on nested windows at +14 (CV:179, :258). At most 0.846 over every window ending at W12 or later (CV:423).
  - W06+: on the grid from W01 at +14, 0.921 (W/rev/coarse_w06_r0.out:18); with rule-layer timed targeting at +20, 0.887 (ceiling.out:146).
- *Risks.*
  - Privacy, with W12 kept: where R2 passes 0.80, the attacker pins 906 (G6+G12) and 1,750 (nested) with the cells withdrawn (attacks_exact.out:9, :15). The 210-window W12+ set was not computed. R2 sends nothing new across the Boundary, so these pins come from the window set and the cells, not from the rule. Today's design, modelled as its weekly cells plus sliding verdicts as the detector asks, pins 5,631, and today's verdicts on every admitted window pin 9,127 without cells and 9,829 with them (attacks.out:31, :25, :19); pin 0 is a proposed bar that today's design does not meet.
  - Privacy, W06+: verdicts on all 231 admitted windows (ends from W06) pin 9,127 without cells (attacks.out:25). That is the exposure today's verdict bodies carry on those windows whatever the gate rule (Summary 6 (b)), so it comes from the window set, not from R2. On the grid from W01, answered once, 0 with the cells withdrawn and 2,904 with weekly cells (attacks.out:43, :49).
  - Budget: a sweep on G6+G12 at +14 has a mean of 0.784 (budget_sweep.out:13), and on nested windows 0.810 (:11). W06+ was not measured.
  - Gate 3: on G6+G12 at +14, W4 + R4lb gives 119 (fail), and W4 + W3k + R4lb gives 117 (pooled pass, 2 seeds over; NT:24, :27). W06+ raw decoys are 247 of 300, with no lever set measured.
  - Summing reporters counts a person once only because each generated user sits at one site (ceiling.py:100-101).
- *Verification.* A gate case in `tests/mycelic/test_collective_gate.py` pinning R2's counting: weak confirms at their lower bound, at least two confirming sites, and the shipped roots, reporters and freshness checks. Also 9.3 item 5 and 10.7. Not yet written.

**Rank 17. Cross-site sub-threshold sums (R3, T2p, W3k).** Not permitted today. A share of a count in 1..k−1 may not leave a site (`LEAKAGE.md:32-34`; `egress.py:77`); there is no secure-aggregation code (repo_facts.out:81-82); and the founder's default is "not permitted" (HANDOFF-PROMPT-2026-10-15.md:181-182).
- *Benefit* [bound].
  - Model 1 pooled: 1.0 at any week, 0.80 at weeks ≥ W12.
  - Model 2 R3: 0.771 at the shipped timing; 0.825 on G6 at +14; 0.808 with 2 sites (r3_two_sites.out:4).
  - Under the budget: 0.805, or 0.788 with 2 sites (budget_sweep.out:2; budget_sweep_m2.out:2).
- *Risks.*
  - An exact secure-sum output pins 833 sub-k cross-site block totals on G6+G12, 1,658 on nested windows alone, and 2,214 on G6 plus nested windows (attacks_exact.out:25, :31, :28). A bucket or one-bit output pins 0 on the sets computed (attacks_v2.out:61-72, :79-90).
  - Whether a secure-sum note can satisfy `lineage_ok` is not established (`harness.py:1044-1047`).
  - It needs a non-colluding party, and a ruling on whether k applies per site or per statistic (dec. 8).
- *Verification.* 9.3 item 5; 10.7.

### 8.5 Not proposed
- **Graph relationships, entity links and Tesseract ranking:** [harness] 0 of 165 verdicts change at seed 0.
- **The pushdown Orchestrator, aggregation above sites and recursive questions:** absent. No measurement links them to a miss beyond p028 (rank 11).
- **Asking all sites:** [bound] no status changes at any seed (summary_numbers.txt:20, :43, :66, :89, :112, :135).
- **B12, aux-entity attachment:**
  - Its only measured effect is to add support: s4 p023 is `supported` in the harness through aux-attached background claims (capacity_split_0_5.txt:33).
  - A fix would sit in `extract.py` (zone 3b) and could remove that find.
  - It is listed for visibility. Rank 5b's found-dependence would show such support, if the founder rules for it.
- **A12, the read cap L = 50:** it truncated 4 pairs over six seeds, all on confirming decoy or background candidates, and truncation cannot create a confirm (`verify.py:243-261`). No change is proposed.
- **C12, B14 (`found` credits the key, not the evidence):** the frozen `found` is not changed (SPEC.md:28). Visibility only, through rank 5b.

### 8.6 Order of work, and what "credibly targeted" needs
1. The founder says yes or no to each rank. This report reads no status-log line as a yes to any rank.
2. Ranks 1 to 4, 5a, 6 and 21 come first: tests, zone 1, the workflow, docs and SPEC amendments. None touches a j4-pinned file or the c7 hash. Rank 5b waits on the SPEC.md:120 ruling.
3. The founder takes dec. 1, 4, 5, 6, 7, 8 and 9 before any recall lever is built.
4. A003 is recorded (6c0c322). Whether its record still gates any rank, with the `report.json` sha256 unverified, is the founder's question (8.0). If it does not:
   - Zone 3a items (ranks 8, 13 and 16) need dec. 14 and no push while lab shards run.
   - Zone 3b items (rank 9, rank 10 if it lives in `extract.py`, and rank 13's (e) format) also need dec. 15 restated: whether they may land before A004's judging run, whose rule names `verify.py`'s judging functions as pinned.
5. Measure each lever alone, then combined, on development seeds, through the real `run` job (10.5), with one run per (commit, seed) (10.8).
6. A 0.80 target becomes credible only when one configuration shows two things on real runs of development seeds: decoys within the frozen gate-3 reading, and a pass of every privacy check on its exact transcript. Then come the candidate freeze, the preliminary seeds and the one held-out evaluation (SPEC.md:122-124). None of this has been shown.

## 9. Regression tests

### 9.1 Rules for every test below
- **Test before fix.** Each test must fail on 7639bec, or a named mutation must make it fail. Commit it with an expected-failure marker that fails on an unexpected pass, and remove the marker in the fix's commit. This keeps suites-core green, so a known defect does not block the e2e gate (`.github/workflows/e2e-ingest.yml:155`).
- **Seeds:** development seeds 0 to 5 only. Unit tests use hand-built fixtures.
- **Frozen code stays frozen.** No test changes `discovery()`, `candidate_label`, the generator or the labels. Diagnostics print beside the frozen figures.
- **Privacy rules stay.** No test lowers k, centralises raw records, or reads Gold in the decision path (C14).
- **Pins.**
  - Tests live in `tests/`, which neither c7 nor any lab code hash covers.
  - Fixes in `research/routing_spike/*.py`, `NeuralGraph/research/coordination/*.py`, `NeuralGraph/research/retrieval/*.py`, `mycelic/*.py` and `mycelic/collective/**` need the c7 update (`run.py:73-75`; dec. 14).
  - Fixes in `edge/verify.py`, `records.py`, `extract.py` and `packs/loader.py` change files in A003's recorded code hash, which A004's rule names as pinned. They need dec. 15 restated (8.0).

### 9.2 Pins of the scoring sources (first)

| Test file | What it asserts | Failure it pins |
|---|---|---|
| `tests/e2e_ingest/test_spec_freeze.py` (new) | sha256 values taken at ce79909 of:<br>- the bytes of `research/mycelic/corpus.py` and `org.py`;<br>- `inspect.getsource` of `emergence_world.generate` and `gold_labels`, and the values of the constants they read;<br>- `Harness.discovery` and everything it calls or reads: `Harness.fabric_conclusions` (`harness.py:1241-1248`), `pattern_facts` (`:285`), `expected_status` (`:316`), `series_key` (`mycelic/collective/detect/rules.py`, imported at `harness.py:76`), `ew.canonical_entity_id`, `ew.CHAIN_PREDICATES`, `ew.ENTITY_TYPE`, and the drill-5 code that sets `lineage_ok` (`harness.py:997-1060`), which `found` requires (`:1288`);<br>- `research/routing_spike/world.candidate_label`;<br>- report.py's recall recomputation (`report.py:486`, `:816`);<br>- SPEC.md up to its first "## Amendment" heading.<br>An alternative is whole-file hashes, with a documented allow-list of the non-scoring functions in those files | SPEC.md:31 promises this test; none exists at 7639bec (`W/s5_code_facts.out:309-310`). A pin on `discovery()` alone would let a change in a callee or in the lineage flag pass |
| `tests/e2e_ingest/test_settings_pins.py` (new) | `TOP_N` = 60 (`emergence_world.py:84`); k = 3, budget 5 per entity per day, min_confirming_sites 2, roots and reporters ≥ 3, freshness_days 42 (`emergence_pack.py:74-81`); the detector's first eligible week (`detectors.py:281-283`). A change updates the pin in the same commit and names the founder's approval | C7: these values change today without a trace |

### 9.3 Recall root causes
1. **Warm-up (B1, D-F2).** In `tests/mycelic/test_collective_detect.py`: a fixture key whose only burst ends before W12 produces either a candidate or an explicit cold-start record, and the e2e report counts them. Today it produces nothing; dec. 1 chooses which. Any sweep test (item 4) states its window set, including the windows that end before W12.
2. **Rule-only drop (D-F1) and the top_n cut (D-F12).** In `tests/e2e_ingest/test_emergence_world.py`: the report counts the rule-only keys dropped at `emergence_world.py:289`, and a fixture with 62 snapshot candidates lists the 2 cut, with rank and score.
3. **D2 self-baseline (B2).** In `tests/mycelic/test_collective_detect.py`: a key whose own earlier facets fall in the baseline weeks still alerts when its later weekly counts cross the threshold. This pins a detector design the founder has not chosen (rank 8, kind (b)); it is written after that choice, beside a count of the new alerts on decoy and background keys.
4. **One question per key (B4, D-F7).** In `tests/routing_spike/test_hq.py` and `tests/mycelic/test_collective_edge.py`:
   - (a) A `hypothesis` candidate whose key persists is asked again at the later as_of.
   - (b) Under the shipped budget (5 per site-clock day; `verify.py:402-406`), asking one entity more than 5 predicates on one day gives "unknown (budget)" for the extra ones. The report counts these refusals, and the gate's freshness uses each answer's own as_of. Today's run has 0 refusals at all six seeds (`W/s5_budget_refusals.out:1-6`).
5. **k = 3 and the two-site gate (B3, D-F3).** In `tests/mycelic/test_collective_leakage.py` and `test_collective_gate.py`: no count from 1 to k−1 crosses the Boundary.
   - If R1 is ever approved: a gate case where one counted site suffices and every other check still applies.
   - If R2 is ever approved: the gate case of rank 16, and nothing new crosses the Boundary.
   - If R3 is ever approved: no sub-k share leaves a site in clear, and the output is a bit or a bucket. R3 needs sub-k shares to leave a site, which today's rules do not permit.
6. **Ceiling and baseline visibility (C4, C17, C3).**
   - In `tests/e2e_ingest/test_harness.py`: next to `recall_all`, the report prints the accept-all control (rank 5a). At seed 0 it would have shown 15 of 40 against 13 [real, re-scored] (`S/rc/second_A.md:35`).
   - The claim-derived bound (D's method; every claim; any week and weeks ≥ W12) is printed only by rank 5b's offline script, and only if the founder rules it compatible with SPEC.md:120. At seed 0 it would have shown 23 and 18 of 40 (`S/rc/D/out/gate_capacity_seed0.stdout.txt:3-4`).

### 9.4 Decoy root causes
Seed-0 counts are from `W/s1_reports.out:1`.
- **Class 4, 9 accepted (B6, D-F4).** In `tests/mycelic/test_collective_edge.py`: echoes of one event by different users, with near-identical text, count as one root. The code is in `edge/records.py` (zone 3b).
- **Class 2, 5 accepted (B7; D-F5 class-2 half).** In `tests/mycelic/test_collective_gate.py`: reversed facets do not support the chain. The generator's real patterns are not ordered either (6.4), so the fixture states the order it expects.
- **Class 5, 3 accepted (B8).** In `tests/mycelic/test_collective_edge.py`: when every yes/yes record is retracted before the as_of, the verdict is not a confirm, and a supported conclusion is verified again. Today `decide()` confirms on any yes/yes (`verify.py:252-254`).
- **Negation window (B5).** In `tests/mycelic/test_collective_extract.py`: negated EMERGENCE records are read as negated. The code is in `edge/extract.py` (zone 3b) or the pack (zone 1). The fixture also uses a negation placed mid-sentence, not only the generator's trailing "not".
- **Facet-0 counting (B9, D-F6, C18).** In `tests/e2e_ingest/test_harness.py`: beside `decoy_accepted_by_type`, the report prints the supported notes on any facet key of a decoy inside its window. The frozen figure stays, and `test_spec_freeze.py` passes.

### 9.5 Benchmark integrity
- **World-count guard (C1, D-F15).** In `tests/e2e_ingest/test_emergence_world.py`: the shipped `run` handles seeds 1 to 5 by the approved rule (dec. 13), and c8 reports instead of refusing.
- **Readable CI discovery block (C6, C8).** In `tests/e2e_ingest/test_workflow.py`: the e2e job writes `recall_all`, the found ids, decoys by class and the report's sha256 to the job summary and to the log's last lines.
- **Benchmark limits (C9).** In `tests/e2e_ingest/test_harness.py`: the report carries rank 5a's limits note (lexicon source, structured entity ids, the Gold facet share of records), so that no reader takes a recall figure as recall on real text.
- **Simulator labels (C5, C11).** In `tests/mycelic/test_collective_guards.py`: every simulator figure in `research/reports/RESULTS_ALL.md` and `docs/MYCELIC_ENTERPRISE.md` sits next to "simulated".
- **Withdrawn claims (C13; rank 21).** A docs test: each withdrawn phrase ("invisible by construction", "ceiling 0.55", "executed and measured" for the simulator) appears only inside a dated correction block.
- **Tesseract (C2, B10).** In `tests/e2e_ingest/test_site_graph.py`: a seed-0 replay with and without the Tesseract-read records gives identical verdicts.

### 9.6 EMERGENCE runtime (5.2)
- **Wire timeout and stderr (A8).** In `tests/routing_spike/test_wire.py`, with a fake child that never answers: `request` raises `WireError("timeout")` in time; the endpoint is closed, so a later request cannot read the late answer; and the error carries the stderr tail.
- **Concurrent fan-out.** In `tests/routing_spike/test_hq.py`, with three fake adapters waiting on events: all three requests are in flight at once, and the results equal the sequential result in route order. The seed-0 decision rows must also stay identical (`S/rc/A/scripts/compare_runs.py`).
- **Cancellation.** A question cancelled mid-fan-out records no partial verdict. Each endpoint is either usable or closed, never out of step.
- **`wait_idle` (A10).** In `tests/e2e_ingest/test_harness.py`: pending 0 with ack_pending 1 is not idle.
- **List caps (A11).** In `tests/e2e_ingest/test_publish_api.py`: a list at the server cap (500, 1,000) raises or pages.

### 9.7 mycelic loop tests (5.1)

A unit test should assert what the code does, not how busy the host is. The wall-clock responsiveness assertion stays, because it is the only form that can fail on A2.

| Test | What it asserts | Load sensitivity | Failure it pins |
|---|---|---|---|
| Responsiveness, wall clock (kept): `test_api.py:395-397`, `:499-501` | The largest tick gap stays below `walk_seconds / 2`, and /health below `walk_seconds / 2 + 0.05`. Any change to the bound's form, for example half of the median in-window walk wall time, is rank 20 (b): a relaxation the founder approves or rejects | Load-sensitive today (A5). A load-aware form is unmeasured | A loop that falls behind in wall time, including A2 (waiting for the lock or a CPU) |
| Loop CPU (added beside it) | The ticker reads `time.thread_time()` on the loop thread at each tick. The largest loop-thread CPU between ticks stays below a fixed fraction (set from the "before" data, for example a quarter) of the median walk CPU measured inside the same window. The message gives the worst interval's loop CPU, the load and the walkers' CPU | Waiting for a CPU or the lock adds no loop-thread CPU, and both sides are measured in one window | A walk or other CPU work on the loop. The recorded walk-on-loop mutation failed the old test at 2.803 s against 0.215 s (`LOOP-48H-STATUS.md:236-237`); the new assertion must fail it too. It cannot fail on A2 |
| Concurrent retrieval: a new case in `test_api.py` and `tests/mycelic/test_retrieval_semantics.py` (new) | Concurrent POST /query and MCP query during walks, interleaved with writes that force index rebuilds, all return 200 and equal the same query sent alone. Ids, scores and explanations are pinned byte for byte. The loop-CPU bound holds during a rebuild of the 3,005-row index | equality and loop CPU, not wall time | Moving BM25 or the rebuild (`retrieval.py:93-100, :118`) off the loop must not change rankings. The rebuild is the largest on-loop cost measured (163.38 ms at load 17.41) |
| Timeouts | Keep `test_api.py:705`, `:748`. Add: a walk held past the client timeout gives the client a timeout error, and the slot is released when the walk ends | events, not sleeps | A slot that leaks after a client timeout |
| Cancellation | Keep `test_api.py:803`, and extend it to GET /lineage and POST /query | events, not sleeps | A cancelled walk that leaks its slot or is answered |
| Responsiveness benchmark (not pass/fail) | Tick lag p50, p99 and max with 2, 4 and 6 walks, with the load, the CPU count and the /proc split | recorded, not asserted | The before/after for any change to how walks run (A2) |

Validation before adoption (not yet measured; unverified):
- The loop-CPU assertion must fail under the walk-on-loop mutation.
- Any load-aware wall-clock bound (rank 20 (b)) must also fail under that mutation. Its failure rate across A's batches with 0, 2, 4 and 8 burners (`S/rc/A/probe/batch1.sh` to `batch4.sh`) must be recorded on the quiet machine before the founder decides. The old bound failed 7 of 44 verification runs and 2 of 44 lineage runs, partly under the orphaned probe load by the status log's unverified account (`W/s5_stall_numbers.out:4-7`; 5.1).
- The fractions above are examples, not measured values.

### 9.8 Which test would have caught each root cause

| Root cause (finding, status) | Test |
|---|---|
| Warm-up (B1, D-F2, confirmed) | 9.3 item 1 |
| Rule-only drop (D-F1, counts confirmed); top_n cut (D-F12) | 9.3 item 2 |
| D2 self-baseline (B2; seed 0 confirmed) | 9.3 item 3 |
| One question per key (B4, D-F7, confirmed) | 9.3 item 4 |
| k = 3 and the two-site gate (B3, D-F3, mechanism confirmed) | 9.3 items 5 and 6 (visibility, not removal) |
| Missing trivial baseline (C3, confirmed) | 9.3 item 6 |
| Decoy classes 2, 4 and 5, and the negation window (B5-B8, confirmed) | 9.4 |
| Facet-0 counting (B9, D-F6 confirmed; C18) | 9.4 |
| `found` credits the key, not the evidence (C12 mechanism confirmed; B14) | 9.3 item 6 (rank 5b's offline found-dependence, if the founder rules for it); the frozen `found` is not changed |
| Aux-entity attachment (B12, count confirmed) | none proposed (8.5); visible through rank 5b's found-dependence, if ruled |
| Read cap L = 50 (A12, counts confirmed) | none proposed (8.5); truncation cannot create a confirm (`verify.py:243-261`) |
| Scoring sources not pinned (confirmed by search) | 9.2 `test_spec_freeze.py` |
| Settings not frozen (C7, confirmed) | 9.2 `test_settings_pins.py` |
| World-count guard (C1, D-F15, confirmed) | 9.5 |
| CI discovery block unreadable (C6, C8, confirmed) | 9.5 |
| Synthetic shortcuts (C9, split) | 9.5 benchmark limits |
| Withdrawn doc claims (C13, C11, confirmed) | 9.5 withdrawn claims |
| Wire timeout (A8), `wait_idle` (A10), list caps (A11), confirmed, not triggered | 9.6 |
| Sampling artefact (A3, confirmed); work moved onto the loop | 9.7 loop CPU |
| Load-sensitive loop bound (A5, confirmed) | 9.7 wall-clock responsiveness; any bound change is rank 20 (b), not a test fix |
| Loop thread waiting for the lock (A2, probable) | 9.7 wall-clock responsiveness assertion (kept) and the benchmark; the loop-CPU assertion cannot catch it |
| `store.stats()` on the loop (A13, unverified; code path `service.py:2428-2432`, `:2447-2453`) | none until its cost is measured |

## 10. Frozen before/after protocol

Nothing here is approved. This section fixes how any approved change will be measured, before the change is made. It changes no SPEC bar. Where SPEC.md is silent, it proposes a dated amendment, never after a held-out evaluation has started (SPEC.md:3-5).

### 10.1 What stays frozen
- `recall_all`, exactly as `Harness.discovery` computes it (harness.py:1288-1294, :1336) and as `report.py` recomputes it (SPEC.md:28-29).
- The generator, the labels and the constants they read (`GRACE_WEEKS` 4 among them, emergence_world.py:85), and the scoring, stay byte for byte as they are (SPEC.md:23-29). None of them changed since ce79909 (W/s7_code_facts.out:3).
- The pin test (9.2) goes in before the first freeze.
- A re-score (the any-facet decoy figure; found-dependence, if rank 5b is ruled in) is reported beside the frozen figure, labelled, and never instead of it.

### 10.2 What every run records
SPEC.md does not freeze these (C7; SPEC.md:142-144). So every run records them, and every pair names the one value it changes:
- gate (questions.json): `min_confirming_sites` 2, `min_independent_reporters` 3, `min_independent_roots` 3, `freshness_days` 42, `max_sibling_sites` 2 (W/s7_code_facts.out:21);
- Boundary (egress.json): `k` 3, `min_window_weeks` 6, `close_lag_days` 14, `question_budget_per_entity_per_day` 5, `question_entities_per_site_per_day` 200, `verify_max_records` 500 (:23);
- detector (detectors.json): `window_weeks` 6, `min_history_weeks` 6, `baseline_weeks` 8, burst and co-occurrence `min_sites` 2 (:20);
- candidates: `TOP_N` 60, route size 3, the candidate source and the window set;
- the pack hashes the report prints (run 2's are at W/ci_run2…:604-608).
- k = 3 never changes (SPEC.md:119).
- The budget is reported for any sweep. A sweep asks 8 predicates per entity per block end, against 5 per site-clock day (budget_sweep.out:1). Its effect is so far measured only offline (4.4 (b)).

### 10.3 Commit pinning
- A dated plan file is written before either run. It names both commits, the seeds, the flags, the configuration of 10.2 and the expected outputs.
- "Before" is the fix's parent (today 7639bec's code, identical since ce79909 and through 9cfa2e9); "after" is the fix commit. One lever per commit, then the combination.
- Both reports show `code_dirty_start` and `code_dirty_end` false, and the named commit.
- Both sides use the same machine class, nats-server and Python. Never compare a local run with a CI run (CI: 2.10.29, 3.11.17; local: 2.10.22, 3.11.15).
- A zone 2 or 3 change alters `SPIKE_CODE_HASH` (report.py:77-78), and the plan names the c7 update (dec. 14).

### 10.4 Seeds
- Ablations use only the development set (SPEC.md:80); this investigation used seeds 0 to 5.
- Until rank 2 lands, other development seeds run only with the guard bypassed in the experimenter's process, labelled as such. c8 then fails by construction, and such runs never count.
- Preliminary and held-out seeds come from a pushed freeze commit, and are never generated or run before it (SPEC.md:84-88, :92). A held-out outcome tunes nothing (SPEC.md:125). Freezing several commits in order to choose among their seeds is forbidden (:97-98).

### 10.5 What runs
- Only the real `run` job, with every leg real (SPEC.md:34-41): `python3 -B -m research.e2e_ingest run --size 200 --mode process` with `E2E_REQUIRE_NATS=1`. c0 passes on both sides (report.py:191). No mock replaces a leg.
- [harness], [bound] and [sim] figures may guide ablations, labelled, but never serve as a before or after figure.
- `--skip-drills` is used only when both sides use it. At seed 0 it gave the same discovery block (W/s1_reports.out:1, :7). Privacy certification needs the drills (10.7).

### 10.6 What is reported for each pair
- Per world: `recall_all`, the found ids, `recall_common`, `recall_rare`, and per pattern the first-loss stage before and after (the taxonomy of `S/r2/decomp_r2.py`).
- Over worlds: the mean, the sample sd, and the two-sided 95% t lower bound with n. With six seeds, t(0.975, 5) = 2.5706 (GV:2), which cannot separate configurations. Acceptance is read only from the SPEC gate on held-out worlds (SPEC.md:48-53).
- Decoys by class under both rules (baselines from W/s10_decoy_classes.out:1-2):

| Class | Frozen rule: baseline | before | after | Any facet key in window: baseline | before | after |
|---|---|---|---|---|---|---|
| 1 entity coincidence | 6 | | | 30 | | |
| 2 temporal scramble | 37 | | | 37 | | |
| 3 near-miss entity | 4 | | | 35 | | |
| 4 duplicate inflation | 41 | | | 45 | | |
| 5 stale chain | 30 | | | 30 | | |
| total of 300 | 118 | | | 177 | | |

  - Gate 3 reads the frozen rule against the baseline code's own run (SPEC.md:57-59). SPEC does not say whether that is pooled, averaged or per world. With 50 decoys per world (world_seeds.out:1-6), pooled and averaged coincide, but per world does not. Until the amendment (rank 4), report the pooled count and the number of worlds over their baseline. Flag any change that lowers the frozen figure while raising the any-facet figure.
- Also report: supported notes on decoy-labelled keys (34 at seed 0, decoy_window_check.out:1); `background_supported`; precision (SPEC.md:67-68); found-dependence (B14; rank 5b, if ruled); the accept-all control [harness]; timings, labelled as not comparable unless the runner and load match; and every figure's kind.

### 10.7 Privacy checks
- Every privacy check passes on every world, before and after (SPEC.md:54-56). Today these are: c2 (`crossing_leakage_zero`, `crossing_shapes_ok`; report.py:322-327); drill 5's scan (hits 0/0/0; S/baseline_seed0.log:17); a crossing overlap of 0 bytes (:10); c1 and c7. A `--skip-drills` run cannot certify privacy.
- The amended design adds a check per guarantee (SPEC.md:116-118).
  - When a change alters what crosses a Boundary, run the differencing attacker on the exact transcript the candidate emits, for the exact window set it asks (`S/r2/crit3/attacks_exact.py`), with and without the weekly cells it actually keeps. Report the pinned sub-k site-weeks and the rare keys with a pin.
  - The attacker reads only the records field, so its result is a lower bound on leakage, not a guarantee (S/DIAGNOSIS-r2-v2.md:82, :714).
  - Zero pins hold only when the Boundary enforces the window set. Today it admits any closed window of 6 weeks or more (egress.py:553-556).
  - Membership exposure is measured separately; it is not measured today.
- Any new count artifact (such as position cells) and any raised budget get their own exposure measurement before entering a candidate.
- k = 3 is never lowered; raw records never leave a site; nothing reads raw notes centrally; no externally hosted model is used (SPEC.md:119-121). A share below k may not leave a site today (LEAKAGE.md:32-34; egress.py:77). A change that needs one is dec. 8, not a protocol step.

### 10.8 One run per freeze
- Each freeze commit is evaluated once, and the first run is the result (SPEC.md:93). The only allowed re-run is for a runner lost before the world's run began (:71-72). A failed candidate stays in the record (:94-95).
- Development pairs get one run per (commit, seed). Seven seed-0 real runs gave identical scored outputs (W/s1_reports.out:1, :7-12), so a differing re-run is a finding, not a second chance. The after figure is never re-run to get a better one.

### 10.9 Keeping A003 and the stall fix's run 3 apart
- **A003.**
  - Lab kind j4. Its run 38667030739 on c5dad9a ended at 02:13:35 UTC.
  - It is recorded at 6c0c322 in CHOICE-A003 Run 1, RESULTS run 13, the YC brief and the status log (5.3). The `report.json` block's sha256 is not verified; the founder's download of artifact 11942489769 before 2026-11-14 would close that clause.
  - The lab imports only `mycelic.collective`, `mycelic.hierarchy`, `mycelic.version` and `mycelic/__init__.py` (S/r2/v2/a003_v2.out:3, :8), and it shares 41 files with the EMERGENCE path (S/DIAGNOSIS-r2-v2.md §8.2).
- **Order.**
  - The founder's order, "A003 first, recorded and frozen" (LOOP-48H-STATUS.md:269-271), is met by the status log's reading at 6c0c322 (:337-340). Whether that reading holds with the `report.json` clause open is the founder's question (8.0).
  - The stall fix comes next (HANDOFF-PROMPT-2026-10-15.md:169-171), but not during any active benchmark (:74), and with no CI-triggering push while model shards run (:66).
  - If the founder accepts the status log's reading, the j4-pinned files are constrained by A004's rule (CHOICE-A004.md:156-159, :1385-1386), not by A003's record (dec. 15 restated, 8.0). Changes confined to `research/e2e_ingest/**` and its pack touch no lab import (S/DIAGNOSIS-r2-v2.md:991-999).
- **The stall fix's before/after is end-to-end run 3**, on the patched commit, against run 2, with every other parameter equal (HANDOFF-PROMPT-2026-10-15.md:171, :186). The stall is not on the EMERGENCE path (A6), so run 3's scored output must equal run 2's. A difference is a regression to explain.
  - Run 2's discovery block is unread (1.0). Until artifact 11931284874 is read, run 3 can be compared with run 2 only at verdict level: `pass`, `failed`, and verdict counts 148, 15, 2 (W/ci_run2…:104-108, :556-576).
  - e2e-ingest run 38664599267 on 7639bec is a later unpatched run on the same code, but it is not the protocol's run 3. Its verdict reads `pass` true, `failed` [] (W/rev/ci_run3_tail_facts.txt:2). Its verdict counts and discovery block were not read (artifact 11943396169).
- **The stall itself** is measured with the two loop tests, before and after, on one quiet machine, under A's probe conditions (S/rc/A/probe/run_probed.sh), with a fixed number of repetitions. Report the distribution of the largest gap over the bound.
  - Under one condition (nice −15, no added load), four cold verification runs gave 0.55 to 1.01 (S/rc/A/results/summary_batch4.txt:2-8; W/s5_stall_numbers.out:8). Those runs predate the 03:57 clean-up that the status log records (unverified; 5.1).
  - A2 is probable, and a retrieval-only fix has not been shown to remove the flake (A3).
- **Records.** A003, the stall fix and each EMERGENCE fix are separate commits, runs and records, and no table mixes their figures. "A004" keeps its meaning: the reasoning-on cycle (HANDOFF-PROMPT-2026-10-15.md:93-94).

### 10.10 What this protocol does not assume
The last critics left four blocking findings on S/DIAGNOSIS-r2-v2.md (S/synth/r2_critiques.json). This protocol takes the positions of 4.4 and 6.6. The figures behind them are [bound] on seeds 0 to 5: not a proof, and not a forecast.
- **A non-adaptive sweep is not assumed to reach the perfect-detection recall.** The per-entity budget lowers it: R3 on G6 at +14 falls from 0.825 to a mean of 0.805, R2 on G6+G12 at +14 from 0.808 to 0.784, and R2 on nested windows from 0.829 to 0.810 (4.4 (b)). A sweep candidate is measured through the real job, with the budget recorded.
- **W12 is not a ceiling of every design.** It is the detector's first eligible week (detectors.py:281-283), and it limits only detector-driven candidates (4.4 (a)). Each run states its candidate source and window set, and each bound is quoted with the candidate source it assumes.
- **Whether a 0.80 mean is impossible under the rules is not settled** (the Summary's Verdict; 4.4 (c)).
  - Under today's design, and under today's rules with the gate kept at R0, every computed configuration is below 0.80 (R0 at most 0.708). That is not a proof.
  - With W12 kept, no computed configuration reaches 0.80 with a transcript shown to pin 0.
  - Without W12, R1 on nested windows (0.854 to 0.863) or over every admitted window (0.892 to 0.896) exceeds 0.80. That drops two-site corroboration, the founder's default (HANDOFF-PROMPT-2026-10-15.md:179-180). R1 counting own records stays at or below 0.775.
  - Without W12, R2 on the grid from W01 (0.887 to 0.921) also exceeds 0.80. It keeps two confirming sites but counts weak confirms (dec. 7).
  - Each of these needs a non-detector candidate source. Each zero-pin result needs an enforced window set and the weekly cells withdrawn, and for R1 the (e) answer format. None has measured decoys, a measured budget effect or measured membership exposure.
  - R3 and pooled counts need sub-k shares to leave a site, which today's rules do not permit.
- **T2k is not a free lever.** It needs a new count artifact with unmeasured exposure.
  - Without T2k, the offline decoy model passes the pooled gate-3 reading on G6 for R3 (W4 + R4lb, 2 seeds over), and on G6+G12 for R2 (W4 + W3k + R4lb, 2 seeds over; NT:27), where W3k needs a cross-site linkable identifier.
  - It fails on G6+G12 for R3 (NT:32, :35) and on nested windows for both rules (NT:225, :230, :233).
  - Under a per-world reading, every passing row with a mean ≥ 0.80 needs T2k or T2p (6.6).

## Appendix A. Sources

### A.1 Where the scratch outputs live, and how to regenerate them
- All scratch files are under `S/` (`/tmp/claude-0/-home-user-NeuralGraph/9160890b-ee51-58d5-92df-ff8899aa2f59/scratchpad/emergence80/`). None is committed, so they are lost when the sandbox ends.
- To regenerate: 7.2 (the seed-0 real run), 7.3 (seeds 1 to 5), 7.4 (each probe), 7.5 (this report's re-checks). CI tails need the job-log API. CI artifacts need the founder's download (1.3).
- At the earlier assembly, all 312 scratch paths in `W/assemble/cited_paths.txt` existed (`W/assemble/cited_paths_check.out:2`). The revisions' files are listed in 7.5 and A.3, and they existed at the end of this revision.

### A.2 Repository files cited
Files are cited at 7639bec unless 6c0c322, 91ab29c or 9cfa2e9 is named. Every repository file is cited with its path at its first use in each section. The main ones:
- `docs/collective/EMERGENCE-80/SPEC.md`, `docs/collective/E2E-INGEST.md`, `BUILD-E2E.md`, `LEAKAGE.md`, `ARCHITECTURE.md`;
- `docs/handoff/LOOP-48H-STATUS.md` (lines after :301 cited at 91ab29c), `HANDOFF-PROMPT-2026-10-15.md`, `HANDOFF-SYSTEMS-ENGINEER-2026-10-14.md`;
- `docs/MYCELIC_ENTERPRISE.md`;
- `research/e2e_ingest/` (harness, emergence_world, emergence_pack, report, site_graph, hq_tesseract, hq_bridge, drills, suites, the pack);
- `research/routing_spike/` (hq, wire, site_process, world, run);
- `NeuralGraph/research/coordination/core.py`, `NeuralGraph/research/retrieval/tesseract.py`;
- `mycelic/` (service, retrieval, api, store, sdk) and `mycelic/collective/` (evaluate, edge, detect, pushdown, packs);
- `research/mycelic/` (corpus, org, models, systems, artifacts);
- `tests/mycelic/test_api.py`, `tests/e2e_ingest/test_emergence_world.py`;
- `.github/workflows/e2e-ingest.yml`, `mycelic.yml`, `mycelic-lab.yml`;
- `lab/j1.py`, `lab/j4.py`, `docs/collective/A004/CHOICE-A004.md`;
- at 6c0c322 (unchanged at 91ab29c and 9cfa2e9): `docs/collective/A003/CHOICE-A003.md`, `docs/collective/A003/run-1-report.md`, `docs/lab/RESULTS.md`, `docs/strategy/YC-BRIEF.md`;
- `lab/requests/accuracy-003.json`: absent at 7639bec, present from c5dad9a on (W/rev2/head_now.out:13, :47).

### A.3 Scratch outputs cited (not committed)
- Under `S/`:
  - the diagnosis and its critics (`DIAGNOSIS-r2-v2.md`, `synth/r2_critiques.json`, `r2/`);
  - the reviewers' work (`rc/rc_result.json`, `rc/first_*.md`, `rc/second_*.md`, `rc/A/`, `rc/B/` including `B/seeds/seed{0..5}/seed_result.json`, `rc/C/`, `rc/D/`);
  - the baseline run (`baseline_seed0.log`, `baseline_seed0/`);
  - the round-1 runs (`dev_seed*`, `run_seed.py`, `inv_baseline.md`).
- Under `W/`: this report's re-checks (7.5), `W/path/`, `W/assemble/`, `W/integrity_critic/`, and the revisions' `W/rev/` and `W/rev2/` files listed in 7.5. Each file is named where it is cited.

### A.4 CI runs, jobs and artifacts cited
- e2e-ingest:
  - Run 1: 38425113336, job 115404706053, 56cfad4.
  - Run 2: 38639771210, job 116124311106, 5eb2e28.
  - Run 38664599267 (run number 9) on 7639bec: end-to-end job 116200904429, measure job 116200904464, report artifact 11943396169 (W/rev/ci_run3_jobs.out:1-6).
- e2e-ingest runs with the e2e job skipped after the lab suite failed: 38628599679, 38631925246, 38633513481.
- mycelic.yml:
  - Red: 38612799645 (job 116020172954, d270bd3), 38628599664 (job 116074070389, 55914e3), 38633513470 (job 116090787590, fd0966e).
  - Green: 38631925525 (14d3a82), 38639771190 (5eb2e28), 38661107236 (a465752).
- mycelic-lab.yml (A003): 38667030739, run number 17, on c5dad9a, 01:41:42 to 02:13:35 UTC (W/rev/ci_runs_now.out:2). Aggregate job 116209851508, 02:12:30 to 02:13:35 UTC (LOOP-48H-STATUS.md:310-311 at 91ab29c). Report artifact 11942489769, plan artifact 11942755522, five shard and two provision artifacts, all expiring 2026-11-14 and none downloaded (:334-336, :350-351).
- Artifacts with ids and digests: 1.3.

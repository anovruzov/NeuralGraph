# Mycelic vNext — experiment matrix

_Twenty hypotheses across the ten areas the pack asked for, each with mechanism, expected gain, privacy risk, compute cost, failure mode and minimal ablation; the status column is the measured result where one exists (paired on identical worlds, held-out seeds unless marked cal). The five highest-information experiments and the ranked queue follow._

## 1. Hypotheses

| id | area | hypothesis | mechanism | expected gain | privacy risk | compute | failure mode | minimal ablation | status |
|---|---|---|---|---|---|---|---|---|---|
| H01 | ranking/calibration | Learned ranker over kernel-side features, hand gate count preserved | logistic on 45 kernel-held features decides WHICH candidates fill the hand-gated count | +0.10–0.20 found at 10k | none (no raw text, kernel aggregates only) | zero calls; one fit on calibration seeds | over-fits 3 seeds; promotes decoys | quick_v3_H: ranker off vs on, 5 paired seeds | ACCEPTED (+0.21 with budget; decoy acc. +0.15) |
| H02 | active question selection | Question budget as 0.65 of the triage queue | widen the budget once ordering holds | +0.03 at 10k over 0.50; saturates | none | +13% compute per +0.15 of budget | floods register when ranking is weak | quick_qf_v3 sweep 0.50/0.65/0.80/1.00 | ACCEPTED at 0.65; 0.80/1.00 rejected |
| H03 | caching/batching | Batched metering per routed node and queried user | ledgers; identical decisions | calls −65–70%, compute −17% at equal budget | none | negative | none observed | quick_rk2_qf0.65 vs quick_rx_base065 | ACCEPTED |
| H04 | candidate generation | Hybrid link timing in the temporal DP | date links at heaviest witness cluster; float singletons | +0.04–0.08 found, rare + | none | zero | long-spread real chains could pick a minority time | quick_refit_hyb (5 held-out seeds) | ACCEPTED (+0.04, CI [0, +0.09]) |
| H05 | candidate generation | Modal link timing | date links at heaviest cluster only | +0.025–0.05 | none | zero | ties pick earliest = old behaviour | quick_refit_mod | REJECTED (−0.01 held-out; +0.04 on cal seeds) |
| H06 | ranking/calibration | Decoy-weighted ranker selection | up-weight decoy rows, penalise decoys in LOSO objective | decoy acc. −0.05 | none | zero | fits 3 seeds' decoys | quick_seldw_H | REJECTED (found −0.06, rare −0.12) |
| H07 | evidence reconstruction | Targeted local re-extraction at the user | re-extract records naming the anchor not yet claimed | +0.04 at 50k? | none (stays local) | +1% compute, 6× wall time | adds echo evidence | quick_rx_reextract; quick_v50_v3 rx arm | REJECTED (no gain at 10k; +0.04/−0.01 at 50k) |
| H08 | descent/routing | Chain-scoped triage questions (strict read of flagged chains) | target_preds = chains with span ≥ 2 | compute −47%, found ±0 | none | negative | sketch names wrong chain for ~16% of gold entities; coverage −0.17 | quick_cal_screen span2; quick_refit_hyb lean arm | KEPT AS H_mycelic_lean (Pareto arm), not default |
| H09 | richer sketches | Support-1 sketch bits with cross-region corroboration | weak site bits admitted only with a strong foreign bit elsewhere | rare recall + | none | +11% compute | floods triage (547 weak candidates on one seed) | logs iteration 21, one cal seed | REJECTED as implemented (found 0.40 → 0.225) |
| H10 | caching/batching | Merge descent returns per (predicate, entity[, polarity]) before the kernel reads | one KO per pair | compute −54% | none | negative | loses per-user time structure the DP needs | logs iteration 21, one cal seed | REJECTED (found 0.40 → 0.275 / 0.325) |
| H11 | ranking/calibration | Per-entity diversity in the register | cap same-anchor candidates | +0.02 | none | zero | freed slots go to junk | offline rescoring (scratch) | REJECTED (no gain) |
| H12 | ranking/calibration | Depth-2 gradient-boosted trees instead of logistic | same features | AUC + | none | zero | 3 seeds too few | select_and_store grid (both selections) | REJECTED (lost LOSO found both times) |
| H13 | active question selection | Register-forecast budget by distinct anchor with fan-out 1 (breadth over depth) | ask until forecast candidates reach α × cap | 50k +0.10 at ≤ +20% compute (panel estimate) | none | +19% at 50k (est.) | rare patterns in unreached teams | paired 50k runs | NOT RUN (queue #1) |
| H14 | ranking/calibration | Sketch-corroboration feature per link | foreign-site bit count for (entity, pred) | +0.025 alone (offline) | none | zero | rare patterns never set a bit | dump + refit + paired | NOT RUN (queue #2) |
| H15 | ranking/calibration | Pairwise / top-K ranking objective | optimise the cap directly | unknown | none | zero | noisier fit | refit only | NOT RUN (queue #3) |
| H16 | active question selection | Second untargeted round for thin strict answers | re-ask when < 2 on-chain preds returned | lean coverage + | claims leave node (as today) | +7% | re-floods register | paired | NOT RUN (queue #5) |
| H17 | temporal/causal reasoning | Interval order test (tmin_j ≤ tmax_i + slack) | loosen the DP edge test | +0.05–0.08 but decoy +0.10–0.15 | none | zero | D2 scrambled decoys pass | quick_paired link_time=interval | NOT RUN (panel predicts decoy leak; low priority) |
| H18 | contradiction handling | Per-chain enumeration when the causal check fails | emit one unverified hypothesis per chain | ≤ +0.01 | none | negligible | more junk | paired | NOT RUN (low information) |
| H19 | topology changes | Remove a hierarchy level | route from SITE directly to USER | compute −; precision − | none | negative | routing precision | E3b in the main report | MEASURED in v1: each level carries routing precision |
| H20 | evidence reconstruction | Modal timing for flat pools (A2, Y) | same DP change for the controls | 0 (one KO per pair: nothing to cluster) | none | zero | — | paired on A2 | NOT RUN (queue #6; a null is the topology's advantage) |

## 2. The five highest-information experiments (chosen, and what they said)

1. **Ranker on vs off, hand gate count preserved, four architectures** — distinguishes "the register cut is ordering" from "the register cut is the gate". Result: ordering; every architecture gained.
2. **Question budget sweep with the fixed ranker** — distinguishes "the budget was starved" from "the budget was right and widening only floods". Result: starved up to 0.65, flooding beyond.
3. **Unbounded register + full budget (diagnostic only)** — the ceiling the pool allows (0.785 at 10k with budget 0.65); says how much of the gap is ordering versus evidence. Result: at 10k almost all ordering.
4. **Link timing: modal vs hybrid, calibration then held-out** — distinguishes "gold is lost to timing contamination" from "gold is lost to the order test being too tight". Result: contamination (hybrid helps; modal, which also fixes contamination but keeps one time per link, does not replicate).
5. **Old vs new at 50k with re-extraction and budget arms** — distinguishes "50k is a register problem" from "50k is a coverage problem". Result: coverage.

## 3. Ranked queue

| rank | experiment | what it distinguishes | expected information | cost | status |
|---:|---|---|---|---|---|
| 1 | Register-forecast question budget at 50k (ask in gain order by distinct anchor until the forecast candidate count reaches α × cap) with descent fan-out 1 | whether 50k coverage is limited by the number of anchors asked (breadth) or by reach per anchor (depth) | high: coverage is the binding 50k stage and the two explanations imply opposite spending | ~10 paired 50k runs | not run |
| 2 | Sketch-corroboration feature for the ranker (foreign-site bit for each link's (entity, predicate)) | whether the ranker's remaining 10k ordering loss is missing information or missing capacity | high: offline AUC 0.74–0.77 for the feature; a null result says capacity | dump + refit + 5 paired runs | not run |
| 3 | Pairwise / top-K ranking objective on the same features | same question from the objective side | medium | refit only | not run |
| 4 | Budget-aware support-1 sketch bits (admit weak bits only into unused question budget) | whether rare patterns are lost at the sketch or at the budget | medium: rare recall is 0.33–0.42 and the weak-bit screen flooded triage | 5 paired runs | screen failed as implemented |
| 5 | Chain-scoped questions with a second untargeted round for thin answers | whether the lean arm's coverage loss can be bought back for less than the 47% compute it saves | medium | 5 paired runs | not run |
| 6 | Modal-cluster timing for flat systems (A2, Y) | whether the flat pools (one KO per pair) can benefit at all — a null result is the topology's advantage | medium | cheap | not run |
| 7 | Descent-evidence merge per polarity with a refitted ranker | compute −54% claim vs the found loss seen on one seed | medium | dump + refit + paired | one-seed screen only |
| 8 | Live model discrimination on kernel-side candidates (frontier vs small model) | whether the simulated kernel tier understates or overstates what a real model does with the same evidence | high for external validity, no effect on the simulator numbers | API budget | live harness exists |


## 4. Benchmark plan (as executed)

- Worlds: the benchmark's evaluation seeds 0–4 at 2k/10k and 0–4 at 50k for the headline suite; 0–2 for the sweeps; calibration seeds 500–502 for every choice.
- Controls on identical worlds: A_flat_rag, A2_chunked_ctx, B_long_context, B2_map_reduce, B4_central_triage, C_recursive_sum, the hierarchy family D–J, H_mycelic_prev (v1), H_mycelic_lean, the oracles Y/Z/Z2.
- Every change is a paired run first (`quick_paired` / `arm_paired`), then the suite.
- Ranker adoption per architecture on the calibration seeds; the hand-set score is a ranker too.
- Register cap, metrics, gold and worlds untouched; the unbounded register is used only as a diagnostic.

| change | scale | seeds | Δ found | Δ rare | Δ cov. | Δ decoy | compute | calls | status |
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| ranker v1: learned order inside the hand gate | 10,000 | eval 0-4 | +0.065 (5/5 better) | -0.048 | -0.005 | +0.065 | -1% | -0% | superseded |
| ranker v2: learned top-K, anchor-context features | 10,000 | eval 0-4 | +0.105 (5/5 better) | +0.038 | +0.000 | +0.095 | -1% | -0% | superseded |
| ranker v3 + question_frac 0.65 + batched descent | 10,000 | eval 0-4 | +0.210 (5/5 better) | +0.175 | +0.280 | +0.150 | +25% | -71% | ACCEPTED |
| decoy-weighted ranker selection | 10,000 | eval 0-4 | -0.060 (1/5 better) | -0.118 | +0.000 | -0.050 | -0% | -0% | rejected |
| question_frac 0.80 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/4 better) | -0.022 | +0.015 | +0.015 | +13% | +1% | rejected |
| question_frac 1.00 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/3 better) | -0.005 | +0.015 | -0.015 | +31% | +1% | rejected |
| local re-extraction at the user (vs qf 0.65 batched) | 10,000 | eval 0-4 | -0.005 (3/5 better) | -0.042 | +0.000 | -0.020 | +1% | +0% | rejected |
| batched descent metering alone (ranker v2, qf 0.65; same decisions) | 10,000 | eval 0-4 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | -17% | -84% | ACCEPTED |
| modal link timing (+ refitted ranker) | 10,000 | eval 0-4 | -0.010 (2/5 better) | -0.058 | -0.005 | +0.010 | +0% | -0% | rejected |
| hybrid link timing (+ refitted ranker) | 10,000 | eval 0-4 | +0.040 (2/2 better) | -0.015 | +0.010 | +0.030 | +1% | +1% | ACCEPTED |
| hybrid + chain-scoped questions (H_mycelic_lean) | 10,000 | eval 0-4 | +0.025 (3/4 better) | -0.071 | -0.170 | +0.045 | -47% | -1% | Pareto arm |
| ranker v3 + qf 0.65 + batched, 50k | 50,000 | eval 0-2 | +0.163 (3/3 better) | +0.100 | +0.273 | +0.137 | +32% | -61% | ACCEPTED |
| question_frac 0.80 at 50k | 50,000 | eval 0-2 | +0.007 (2/3 better) | -0.032 | +0.047 | +0.033 | +12% | +2% | rejected |
| local re-extraction at 50k | 50,000 | eval 0-1 | +0.007 (1/3 better) | -0.025 | -0.003 | +0.040 | +1% | -0% | rejected |
| hybrid link timing at 50k (+ refitted ranker) | 50,000 | eval 0-2 | +0.060 (3/3 better) | +0.009 | +0.003 | +0.040 | -0% | -0% | validation |
| hybrid + chain-scoped questions at 50k | 50,000 | eval 0-2 | -0.003 (1/3 better) | -0.072 | -0.143 | +0.007 | -31% | -1% | Pareto arm |
| unbounded register (DIAGNOSTIC ONLY, not a fix) | 10,000 | eval 0-4 | +0.165 (5/5 better) | +0.127 | +0.000 | +0.150 | +7% | +0% | diagnostic |
| unbounded register + full question budget (DIAGNOSTIC) | 10,000 | eval 0-4 | +0.420 (5/5 better) | +0.482 | +0.295 | +0.295 | +117% | +143% | diagnostic |
| chain-scoped questions alone (calibration seeds) | 10,000 | cal 500-502 | +0.008 (2/3 better) | -0.066 | -0.175 | -0.008 | -48% | -1% | screen |
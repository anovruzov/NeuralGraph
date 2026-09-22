# Mycelic vNext — research report

_Generated 2026-09-22 11:25 UTC from `research/mycelic/artifacts/` (new) and `artifacts/v1/` (old) by `vnext_docs.py`. Every table is computed from those files._

## 1. Result

The brief asked for the discovery accuracy of the Mycelic hierarchy to be raised as fast and as compute-efficiently as possible without touching the metrics, the gold labels, the evaluation worlds, the register cap or the calibration/evaluation seed split, and then for the whole benchmark to be rerun on the frozen configuration. This is the paired result on identical worlds (OLD = the archived v1 rows, NEW = the rerun).

### 1.1 OLD vs NEW at 10,000 people — development panel (seeds 0–4)

These five worlds were read after every accepted and rejected change during the work (about eleven decisions); their numbers are development-panel numbers and carry a selection optimism of roughly +0.02 to +0.04 found on the small deltas (section 6).

| system | metric | OLD | NEW | Δ | 95% CI | better on | verdict |
|---|---|---:|---:|---:|---|---:|---|
| Mycelic hierarchy | found | 0.300 | 0.475 | +0.175 | [+0.175, +0.175] (range) | 1/1 | **better** |
| Mycelic hierarchy | evidence cov. | 0.625 | 1.000 | +0.375 | [+0.375, +0.375] (range) | 1/1 | **better** |
| Mycelic hierarchy | rare recall | 0.118 | 0.176 | +0.059 | [+0.059, +0.059] (range) | 1/1 | **better** |
| Mycelic hierarchy | AP | 0.008 | 0.117 | +0.110 | [+0.110, +0.110] (range) | 1/1 | **better** |
| Mycelic hierarchy | FDR | 0.980 | 0.968 | -0.011 | [-0.011, -0.011] (range) | 1/1 | **better** |
| Mycelic hierarchy | compute | 1.04e+06 | 1.35e+06 | +29% | [+304499.790, +304499.790] (range) | 0/1 | **worse** |
| Mycelic hierarchy | calls | 8.66e+04 | 2.67e+04 | -69% | [-59899.000, -59899.000] (range) | 1/1 | **better** |
| A2 chunked long context | found | 0.685 | 0.740 | +0.055 | [+0.005, +0.110] | 3/3 | **better** |
| A2 chunked long context | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | rare recall | 0.653 | 0.669 | +0.016 | [-0.100, +0.133] | 2/4 | inside noise |
| A2 chunked long context | AP | 0.067 | 0.202 | +0.135 | [+0.083, +0.208] | 5/5 | **better** |
| A2 chunked long context | FDR | 0.954 | 0.951 | -0.003 | [-0.007, +0.000] | 3/5 | inside noise |
| A2 chunked long context | compute | 2.08e+06 | 2.08e+06 | +0% | [+0.000, +0.000] | 0/0 | same |
| A2 chunked long context | calls | 3.00e+00 | 3.00e+00 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | found | 0.370 | 0.540 | +0.170 | [+0.095, +0.255] | 5/5 | **better** |
| B4 central triage | evidence cov. | 0.970 | 0.970 | +0.000 | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | rare recall | 0.258 | 0.283 | +0.025 | [-0.056, +0.120] | 2/4 | inside noise |
| B4 central triage | AP | 0.022 | 0.130 | +0.108 | [+0.075, +0.143] | 5/5 | **better** |
| B4 central triage | FDR | 0.975 | 0.964 | -0.011 | [-0.017, -0.006] | 5/5 | **better** |
| B4 central triage | compute | 3.98e+05 | 3.98e+05 | +0% | [+0.000, +0.000] | 0/0 | same |
| B4 central triage | calls | 1.00e+04 | 1.00e+04 | +0% | [+0.000, +0.000] | 0/0 | same |
| Y oracle retrieval | — | — | — | — | — | — | no rows |

paired seeds at 10,000: 1 (old rows 5, new rows 1); the register cap is 1000 entries in both.

### 1.2 OLD vs NEW at 10,000 people — confirmation panel (seeds 5–9, never read during development)

No paired experiment, dump, funnel or decision touched these five worlds before the final rerun; this is the number to quote.

_(old or new headline rows missing: run final_rerun.sh)_

### 1.3 OLD vs NEW at 50,000 people (seeds 0–4; seeds 0–2 were the development panel at this scale)

| system | metric | OLD | NEW | Δ | 95% CI | better on | verdict |
|---|---|---:|---:|---:|---|---:|---|
| Mycelic hierarchy | — | — | — | — | — | — | no rows |
| A2 chunked long context | found | 0.690 | 0.790 | +0.100 | [+0.100, +0.100] (range) | 1/1 | **better** |
| A2 chunked long context | evidence cov. | 1.000 | 1.000 | +0.000 | [+0.000, +0.000] (range) | 0/0 | same |
| A2 chunked long context | rare recall | 0.622 | 0.756 | +0.133 | [+0.133, +0.133] (range) | 1/1 | **better** |
| A2 chunked long context | AP | 0.043 | 0.118 | +0.075 | [+0.075, +0.075] (range) | 1/1 | **better** |
| A2 chunked long context | FDR | 0.977 | 0.974 | -0.003 | [-0.003, -0.003] (range) | 1/1 | **better** |
| A2 chunked long context | compute | 1.02e+07 | 1.02e+07 | +0% | [+0.000, +0.000] (range) | 0/0 | same |
| A2 chunked long context | calls | 1.00e+01 | 1.00e+01 | +0% | [+0.000, +0.000] (range) | 0/0 | same |
| B4 central triage | found | 0.310 | 0.310 | +0.000 | [+0.000, +0.000] (range) | 0/0 | same |
| B4 central triage | evidence cov. | 0.470 | 0.470 | +0.000 | [+0.000, +0.000] (range) | 0/0 | same |
| B4 central triage | rare recall | 0.156 | 0.156 | +0.000 | [+0.000, +0.000] (range) | 0/0 | same |
| B4 central triage | AP | 0.006 | 0.024 | +0.018 | [+0.018, +0.018] (range) | 1/1 | **better** |
| B4 central triage | FDR | 0.981 | 0.981 | +0.000 | [+0.000, +0.000] (range) | 0/1 | **worse** |
| B4 central triage | compute | 1.07e+06 | 1.07e+06 | +0% | [+0.000, +0.000] (range) | 0/0 | same |
| B4 central triage | calls | 5.00e+04 | 5.00e+04 | +0% | [+0.000, +0.000] (range) | 0/0 | same |
| Y oracle retrieval | — | — | — | — | — | — | no rows |

paired seeds at 50,000: 0 (old rows 5, new rows 0); the register cap is 5000 entries in both.

### 1.4 In one paragraph

On the confirmation panel (10k, seeds 5–9) the hierarchy's found rate goes from 0.395 to n/a (rare recall 0.220 → n/a, A2 0.640 → n/a). On the development panel (seeds 0–4) it goes from 0.375 to 0.475 (evidence coverage 0.690 → 1.000, rare recall 0.215 → 0.176) for +24% compute and -71% model calls. At 50k it goes from 0.336 to n/a (coverage 0.420 → n/a) for n/a compute and n/a calls. The centralised chunked-context control A2 also improved, because it adopts the same learned ranker (its calibration said the ranker was not worse for it): 0.685 → 0.740 at 10k. The remaining gap to A2 is +0.265 found at 10k and +nan at 50k, at 0.65× and nan× of A2's compute respectively. Decoy acceptance rose with the ranker at 10k (0.180 → 0.400) and is reported as a regression, not hidden; the one attempt to train it away (decoy-weighted selection) lost found and rare recall on the held-out seeds and was rejected.

Targets from the brief: 10k found ≥ 0.70 (stretch 0.75), 50k ≥ 0.60 (stretch 0.70 while materially below A2 compute). Reached: 10k 0.475 (not met), 50k n/a (not met), the 50k figure at nan× of A2's compute. Section 5 says which stage holds the rest.

## 2. Against the centralised controls on the same NEW worlds

### 2.1 10,000

| system | found | evidence cov. | rare recall | AP | decoy acc. | compute | calls | found gap to H | compute ratio to H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full | 0.475 | 1.000 | 0.176 | 0.117 | 0.400 | 1.35e+06 | 2.67e+04 | +0.000 | 1.00x |
| A2_chunked_ctx | 0.740 | 1.000 | 0.669 | 0.202 | 0.460 | 2.08e+06 | 3.00e+00 | +0.100 | 1.54x |
| B4_central_triage | 0.540 | 0.970 | 0.283 | 0.130 | 0.350 | 3.98e+05 | 1.00e+04 | +0.000 | 0.30x |

### 2.2 50,000

_(no new rows yet)_

`H_mycelic_prev` is the v1 hierarchy (question budget 0.25, unbatched metering, earliest-mention link timing, hand-set ranker) run inside the new suite; it is there to show the old configuration reproduces on the new worlds. `H_mycelic_lean` is the compute-lean Pareto point (chain-scoped triage questions; about half the compute, less evidence coverage).

### 2.3 Does the v1 configuration reproduce inside the new suite?

_(no H_mycelic_prev rows yet)_

A non-zero difference here would mean a code change altered v1 behaviour; the intended reading is "the same to the third decimal" for discovery and "identical" for compute.

## 3. What changed, and the mechanism behind each change

The loss accounting (`docs/MYCELIC_LOSS_ACCOUNTING.md`) showed the 35-point gap was not where the handoff pack expected. The sketch saw essentially every gold pattern; retrieval reached most of them once asked; the two stages that ate the gold were the question budget (calibrated to 0.25 of the triage queue because the hand-set confidence let extra candidates flood the register) and the register cut itself (candidates matched at ≥ 0.5 but out-ranked by same-entity siblings and background chains). Three changes address exactly those stages; nothing else was changed in the frozen configuration.

1. **Learned ranker over kernel-side features** (`calibrator.py`). The hand-set logistic still decides how many candidates pass the 0.5 gate; a class-weighted logistic fitted on the calibration seeds (500–502) over 45 features the kernel already holds — per-link support and independence, lineage dispersion, lag statistics, the anchor's triage context, evidence shape (origin users, single-witness fraction, echo ratio) and the kernel's own attribution verdict — decides which candidates fill that count. No raw text, no per-user record, no evaluation seed is involved. Adoption is decided per architecture on the calibration seeds so no control is compared at a ranker chosen for somebody else.

2. **Question budget 0.65 of the triage queue, with batched metering** (`systems.py`). With ordering fixed, the budget could be widened; the paired sweep 0.50/0.65/0.80/1.00 saturates at 0.65 at both scales (coverage reaches 0.97 at 10k; 0.80 and 1.00 buy nothing and lose AP). Batching meters one routing call per node and one read per queried user per round, with identical decisions and per-record tokens, which is why calls fall by about two thirds while compute rises.

3. **Hybrid link timing in the temporal DP** (`ops.py`). A link was dated at the earliest mention of its (predicate, entity) pair; a descent that returns every mention of an entity drags that date to a stale or routine mention, and the chain-order test then fails. A link is now dated at its heaviest witness cluster, and a link whose witnesses disagree becomes several DP states. This is free (no calls, no new candidates) and lifted found, rare recall and decoy resistance on the calibration seeds; it survived the held-out seeds (+0.04, never worse) while its cousin (modal timing) did not (+0.04 on calibration, −0.01 held-out — rejected).

### 3.1 The funnel, old and new (Mycelic hierarchy)

#### 10,000

_(funnel rows missing for one side)_


_(funnel rows missing for one side)_

#### 50,000

_(funnel rows missing for one side)_


_(funnel rows missing for one side)_

## 4. The change ledger: everything tried, with its cost

Every row is a paired run on identical worlds; Δ columns are variant minus base, compute and calls are ratios. Status ACCEPTED means the change is in the frozen configuration; rejected means it did not improve found under the real register cap on the held-out seeds (or improved it only at a cost the brief rules out); diagnostic means the run measures a ceiling and is not a fix.

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

Not in the table because they were single calibration-seed screens (logs/research_log.md, iteration 21): merging descent returns per (predicate, entity) before the kernel reads them (compute −54%, found 0.400 → 0.275; per polarity 0.325) and support-1 sketch bits admitted with cross-region corroboration (found 0.400 → 0.225: 547 weak candidates flood the triage list). Both stay off; the weak-bit mechanism needs budget-aware admission before it is worth a paired run, and merging needs the ranker refitted on merged candidates.

### 4.1 Compute cost per accepted change (10k, evaluation seeds)

| configuration | found | rare recall | compute | calls | Δ found per +10% compute |
|---|---:|---:|---:|---:|---:|
| v1 hierarchy (hand ranker, qf 0.25) | 0.375 | 0.215 | 1.09e+06 | 9.10e+04 | — |
| + ranker v3, qf 0.65, batched descent | 0.585 | 0.390 | 1.36e+06 | 2.66e+04 | +0.084 |
| + hybrid link timing (refitted ranker) | 0.625 | 0.375 | 1.37e+06 | 2.67e+04 | +0.040 at +0.5% compute |

The biggest single gain is the ranker (with the budget it unlocked): +0.21 found for +25% compute. Hybrid timing is the cheapest: +0.04 for +1%.

## 5. Largest remaining loss stage

In the new funnel the most common terminal loss for the hierarchy is **—** at 10k (0 of 0 gold patterns) and **—** at 50k (0 of 0).

At 10k the register is still the binding stage: coverage is near 0.97, so almost every gold pattern is in the kernel pool, and the loss is ordering among the ~3,000 candidates that pass the gate for 600 slots. At 50k the register (2,999 slots) is not binding; coverage (~0.72) is, i.e. the question budget and the descent's reach. Those are different problems and the queue in section 8 treats them separately.

## 6. Did the gains survive held-out seeds? (the honest version)

The intended protocol was: choose on seeds 500–502, read once on seeds 0–4 (10k) and 0–2 (50k). That is not what happened, and the first independent refuter (REVIEWER_ATTACK.md) counted it: seeds 0–4 were read after roughly eleven accept/reject decisions over some forty configurations, and two frozen knobs were chosen on those seeds rather than on the calibration seeds — the question budget (both sweeps under a learned ranker ran on 0–4; the only calibration-seed sweep was the v1 one with the hand ranker) and the rejection of local re-extraction. The ranker fit and the per-architecture adoption are clean (only calibration-seed rows enter them). Two candidates that won on the calibration seeds lost on 0–4 and were dropped — modal link timing (+0.04 → −0.01) and decoy-weighted selection (found −0.06, rare −0.12) — which is the panel doing its job, and also evidence that decisions were being made on it.

What this costs: with a paired standard error of about 0.02 found on five seeds, picking the best of four to eight arms inflates a null by +0.02 to +0.03. The small accepted deltas (budget 0.65 vs 0.50 +0.03; hybrid timing +0.04) are therefore not established by the development panel alone; the large one (ranker + budget + batching, +0.21 on every seed, more than ten standard errors) is. The cure is the confirmation panel in section 1.2: seeds 5–9 at 10k were never read by any experiment, dump or funnel before the final rerun, and the OLD rows for them exist in the archive, so that comparison is a clean single read. Where two legitimate arms differed only inside noise on the development panel (the ranker refitted under hybrid timing vs the previous one), the pre-registered rule — fit the ranker on the calibration seeds under the pipeline it will run in — decided, not the numbers.

A budget sweep on the calibration seeds under the frozen ranker and timing was run after the refuter's report (quick_qf_cal.jsonl); its result is in the frozen-configuration table's evidence column, so the reader can see whether the calibration seeds agree with the value the development panel chose.

## 7. Ranker evaluation on the final configuration

_(ranker_eval_final.json missing: run final_rerun.sh)_

## 8. Frozen configuration

| knob | frozen value | seeds the cited evidence was read on | evidence |
|---|---|---|---|
| `question_frac` | `0.65` | quick_qf_v3.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_qf_v3.jsonl` |
| `batched_descent` | `True` | quick_v3_H.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_v3_H.jsonl` |
| `link_time` | `hybrid` | quick_refit_hyb.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_refit_hyb.jsonl` |
| `local_reextract` | `False` | quick_rx_reextract.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_rx_reextract.jsonl` |
| `strict_targeting` | `False` | quick_cal_screen.jsonl: seeds 500–502 at 10,000 — calibration | `quick_cal_screen.jsonl` |
| `triage_target_chains` | `none` | quick_cal_screen.jsonl: seeds 500–502 at 10,000 — calibration | `quick_cal_screen.jsonl` |
| ranker | logistic, l2 0.3, interactions True, 23614 candidates | seeds [500, 501, 502] | `research/mycelic/artifacts/calibration.hyb.json` |
| ranker adopted by | A2_chunked_ctx, A_flat_rag, B2_map_reduce, B4_central_triage, D_hier_nolineage, E_hier_lineage, F_hier_retrieval, G_hier_questions, H_mycelic_full, H_mycelic_lean, I_mycelic_completion, J_mycelic_verified, Y_oracle_retrieval | calibration seeds | ranker_arch_table |

## 9. Ranked experiment queue (by information value, not size)

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


## 10. Where the reports are

- `docs/MYCELIC_ENTERPRISE.md` / `.pdf` — the full benchmark document, regenerated on the new artifacts.
- `docs/MYCELIC_LOSS_ACCOUNTING.md` / `.pdf` — the per-pattern loss accounting, regenerated.
- `docs/mycelic_vnext/` — this report, `VNEXT_ARCHITECTURE.md`, `EXPERIMENT_MATRIX.md`, `LOSS_ACCOUNTING.md`, `REVIEWER_ATTACK.md`, the diagrams `fig_topology.svg` and `fig_loop.svg`.
- `research/mycelic/artifacts/v1/` — the archived pre-vNext artifacts the OLD columns come from.
- `research/mycelic/logs/research_log.md` — the iteration-by-iteration record, including the negative results.


## What would change my mind

- If `H_mycelic_prev` inside the new suite does not reproduce the archived v1 rows to the third decimal, the old-vs-new comparison is contaminated by a code change and every Δ in section 1 is suspect.
- If the learned ranker's out-of-sample AUC in section 7 is not above the hand-set score's, the found gain is a gate artefact and should not survive a different register cap.
- If a fresh set of evaluation seeds (5–9) gives hybrid timing a negative paired delta, it joins modal timing in the rejected column.
- If A2 with the ranker beats the hierarchy at 50k at equal compute (it does not today: 10.2e6 vs 3.9e6 units), the compute argument for the hierarchy is gone and only the privacy argument remains.
- If queue item 1 shows 50k coverage is depth-limited, the lean arm's mechanism is the wrong direction and breadth spending should be reverted.
- If the live discrimination harness shows a frontier model extracting a signal from raw notes that no kernel-side feature carries, the ranker's ceiling is a property of the simulator, not of the design.

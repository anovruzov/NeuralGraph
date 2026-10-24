# H4 — INFORMATION GAIN / VALUE OF INFORMATION — hypotheses, derivation, tests

## Summary (10 lines)

1. The gate is a hard threshold with a one-shot commit (no posterior kept, no re-ask), so the question's only consumer is `G = 1[TB_v4 clears]`; the Bayes objective it implies is expected utility `E[ΔU|b] = Σ_h P(h|b)·u(h)·P(G|h,b)`, **not** information gain.
2. `I(H;G|b)` is exactly zero wherever `b` already decides the gate — which is where all the yield sits: class (nC=2,nP=3,roots≥3,rep≥3,fresh) has P(clear)=0.999, 43% gold, IG=0.001 bits; class (1,0,2,3,3,1) has P(clear)=1.0, 66% gold, IG=0.000 bits. IG ranks the undecidable classes (P(clear)≈0.1–0.5, 3–7% gold) first.
3. Ranking the filtered pool (1,765 keys: 138 gold, 317 decoy, 1,310 unplanted) by IG: AUC gold-vs-decoy **0.638** (per-seed 0.566–0.782) vs rule_weeks baseline 0.600 (0.484–0.664); top-20/seed gives **11 corrected verdicts / 120 questions**. DEAD.
4. Ranking by expected corrected verdicts (ν=0): AUC gold-vs-decoy **0.615** fitted / **0.584** label-free, i.e. the baseline. It is decoy-blind by construction: decoys clear the gate at the same rate as gold in the same bucket classes (645 of the 1,129 certain-clear rows are decoys). It does convert **43 corrected / 120 questions** at 1.2 decoys per gold — the +3 −14 shape. DEAD as a decoy separator.
5. Ranking by **net** expected utility with the decoy term, `P(G|b)·[P(gold|G,b) − ν·P(decoy|G,b)]`, LOSO-fitted on the exported bucket class: AUC gold-vs-decoy **0.794** (every held-out seed 0.741–0.826, all above the baseline's best seed 0.664 and the null's max 0.591); top-5/seed: 17 gold / 6 decoy over 30 questions, 15 corrected, 5 decoys accepted. **PROMOTED, with the caveats in H4-3.**
6. Out-of-pool check: the same LOSO tables order the *already-asked* snapshot pool (102 gold / 216 decoy) at AUC **0.842** (0.730–0.931) vs 0.499 for rule_weeks — a selector for the 60 questions already spent, which is the lever Gate-0 found (+8 e2e).
7. What the net table learned, in every fold: the N1-shaped *thin* state — exactly two sites present, exactly one certainly at k, roots≥3, reporters≥3, fresh — is 64–67% gold and ~32% decoy; states with ≥2 certain k-sites or ≥3 sites present are decoy-heavy. Mechanism: the generator plants every decoy at common support (≥3 records per facet over 2–4 regions) and rare gold at 1–2 records per facet; only rare gold passes through a thin state. This is a construction fact of the benchmark.
8. **When** to ask is a VoI result in its own right and is label-free: asking a filtered key at the latest rule week yields 0/1,765 corrected verdicts (stale); at the first fitting rule week (the replay's policy) 58 (1 in 30); at `argmax_w P̂(clear|b_w)` **99 (1 in 18)** — 96% of the any-week ceiling of 103.
9. Calibration: on the whole rule-only pool at the replay's week policy the gate model gives 1 corrected per 49 questions where the recorded replay gave ~1 per 220, so every per-question yield here is **×4.5 optimistic**; deflated, the top-5 net ranking is worth ≈0.11 corrected/question against the 0.27 an average asked candidate returns (93/341). Promotion into a full budget does not break even at ν=0; it pays only into free slots (19 across the six seeds) or as a re-ordering of the asked set.
10. Break-even λ (cost of one question in corrected-verdict units): a random filtered-pool key pays iff λ ≤ 0.007 (≈ the recorded 1/220 = 0.0045 on the whole pool); the top-5 by net pays iff λ ≤ ≈0.11 at ν=0, ≈0.03–0.05 at ν_d=ν_o=1 (deflated). Variants tried: 11 orderings (listed in §5); the effective sample for the best is 138/11 ≈ 13 gold.

Status: ESTIMATE / DIAGNOSTIC. Everything below is the `ceiling_b` gate model over the frozen generator (model B, polarity-blind, as the shipped judge counts), not an e2e run. No verdict file exists for any of it. Dev seeds 0–5 only. Read-only: no repo was touched; outputs live in `swarm_out/H4/`.

---

## 1. Derivation — what the question is worth under a hard gate

Notation. A key has a hidden class `H ∈ {gold, decoy, other}`. Before the question HQ holds the exported view `b`: per site, per week, the k-suppressed weekly cells `(n, n_roots, n_reporters)` ∈ {absent, '<k', count ≥ 3} that the detector and the rules read (`CellRow`, `detectors.py`). The question returns `A_q` = per-site `(support_bucket, roots_bucket, reporters_bucket, newest_week)` for the 6-week window, and the gate maps `A_q` deterministically to `G ∈ {clear, fail}` (`pushdown/gate.py`: ≥2 counted confirms or 1 + 1 weak, roots_lb ≥ 3 from k-root sites, reporters_lb ≥ 3, newest ≤ 42 days). The system keeps no posterior over `H`, never re-asks a persisting candidate (closed), and scores a supported verdict on gold +1, on a decoy as a decoy acceptance, on an unplanted key as a background confirm.

**(a) Information gain.** `I(H; A_q | b) ≥ I(H; G | b) = H(G|b) − Σ_h P(h|b)·H(G|h,b)`. The inequality is slack only through the bucket detail the gate throws away; since nothing downstream consumes it, the decision-relevant quantity is `I(H;G|b)`. Properties: it is **zero whenever `b` determines `G`** (certain-clear and certain-fail classes alike), regardless of `P(gold|b)`; it is maximal where `P(G|b) ≈ ½` and `P(G|gold,b) ≠ P(G|¬gold,b)`.

**(b) Expected corrected verdicts.** `Y(b) = P(gold|b)·P(G=clear | gold, b)`; with the costs the scorer actually charges, `E[ΔU|b] = P(G|b)·[P(gold|G,b) − ν_d·P(decoy|G,b) − ν_o·P(other|G,b)]`. It is **maximal exactly where IG is zero** (certain-clear, gold-rich classes) and zero on certain-fail classes (where it agrees with IG).

**(c) Which one the gate implies.** A one-step decision whose payoff is `1[G=clear]·u(H)` with no later use of the answer is a plain expected-utility problem; its Bayes-optimal question is `argmax E[ΔU|b]`. IG is the right objective only when the answer updates a belief that drives a *later* decision (sequential design), which this gate forbids by construction (hard threshold, no posterior, no re-ask). Under a threshold, a question is a commit step, not an experiment.

**(d) Where they disagree, numerically** (all pool keys × rule weeks, b-class = (nC, nU, nP, roots_lb, rep_lb, fresh); nC = sites certainly at k from the weekly lower bounds, nU = sites that could reach k, nP = sites present):

| b-class | rows | P(clear) | P(gold) | P(gold\|clear) | IG (bits) | Y | net (ν_d=1) |
|---|---|---|---|---|---|---|---|
| (2,0,3,3,3,1) certain-clear, three sites | 1,129 | 0.999 | 0.429 | 0.428 | **0.0011** | **0.428** | −0.14 |
| (2,0,2,3,3,1) certain-clear, two k-sites | 674 | 1.000 | 0.500 | 0.500 | 0.0000 | 0.500 | 0.00 |
| (1,0,2,3,3,1) one k-site + one weak (N1 shape) | 114 | 1.000 | **0.658** | 0.658 | **0.0000** | **0.658** | **+0.33** |
| (0,1,2,0,0,0) undecidable, thin | 1,810 | 0.077 | 0.070 | 0.371 | **0.0494** | 0.029 | +0.01 |
| (0,1,3,0,0,0) undecidable, three sites | 1,065 | 0.157 | 0.139 | 0.383 | **0.0530** | 0.060 | ≈0 |
| (2,0,3,0,3,1) two k-sites, no week with ≥3 roots (echo) | 383 | 0.624 | 0.008 | 0.013 | 0.0054 | 0.008 | −0.61 |
| (1,2,3,3,3,1) | 51 | 1.000 | 0.000 | 0.000 | 0.0000 | 0.000 | −1.00 |

IG inverts the order the gate rewards: the single most valuable class (N1 shape, 66% gold, P(clear)=1) carries zero information because the answer is already known from `b`. Full table: `h4_report.json["objective_table"]`.

## 2. Scores, computed from exported buckets only

Per key, per rule week `w` (the detector's rule fired there; a rule-only key would be asked at one of them), from the weekly cells over the window `[w−5, w]`: per site `lb_s = Σ lb(cell)`, `ub_s = Σ ub(cell)` with `'<k' → (1, 2)`; `nC = #{lb_s ≥ 3}`, `nU = #{lb_s < 3 ≤ ub_s}`, `nP = #{present}`; `roots_lb = Σ_{s: lb_s ≥ 3, max-week roots ≥ 3} max_j lb(roots_j)` (N2), `rep_lb = Σ_{s: lb_s≥3} max_j lb(rep_j)`; `fresh = ∃ k-site with newest week ≥ w−3`. Key score = max over its rule weeks.

| score | what it is | labels in the fit? |
|---|---|---|
| `baseline` | rule_weeks desc, then `sha256(tie_salt|key)` — the wave-0 ordering (`ordered_candidates` has no detector score for a rule-only key) | no |
| `S_Pclear` | `P̂(clear | b-class)` tabulated from the gate's *outcomes* on the other five seeds (an outcome is not a label) | no |
| `S_ent` | `h₂(P̂(clear|b))` — answer entropy, the label-free IG proxy | no |
| `S_IG` | `I(H; G | b-class)`, H = gold vs not, LOSO | yes (fit on 5 seeds) |
| `S_Yield` | `P̂(G|gold,b)·P̂(gold|b)`, LOSO (ν = 0) | yes |
| `S_Net` | `S_Yield − P̂(G|decoy,b)·P̂(decoy|b)` (ν_d = 1, ν_o = 0), LOSO | yes |
| `S_Net3` | `P̂(G|gold,b)·P̂(gold|b) − P̂(G|¬gold,b)·P̂(¬gold|b)` (ν_d = ν_o = 1), LOSO | yes |
| `S_thin` | post-hoc label-free distillation of what `S_Net` learned: `1[nP=2 ∧ nC=1 ∧ roots_lb≥3 ∧ rep_lb≥3 ∧ fresh]` at some rule week | no parameters, but designed after reading the table |
| `S_cert`, `S_poss`, `S_sumlb` | structural: gate certainly clears from `b`; count condition possible; total exported lower bound | no |

FORBIDDEN check: every input is a k-suppressed weekly cell (count, roots, reporters), a week index, or a rule week from the detector dump. No gold label, no facet record, no node id, no file path, no value equality, no `Placement`. Labels enter only the LOSO fit of the four fitted scores, on seeds other than the one scored, and the metrics.

## 3. Tests run — filtered pool (rule_only, rule_weeks ≥ 7): 138 gold, 317 decoy, 1,310 unplanted

### 3.1 AUC, strict salted orderings (the system's ranking is strict)

| score | gold vs **decoy** | per seed 0…5 | spread | gold vs other |
|---|---|---|---|---|
| baseline (rule_weeks) | 0.600 | 0.664 0.646 0.549 0.484 0.664 0.564 | 0.484–0.664 | 0.693 |
| S_Pclear (label-free yield) | 0.584 | 0.558 0.546 0.650 0.633 0.583 0.545 | 0.545–0.650 | 0.844 |
| S_ent (label-free IG proxy) | 0.573 | 0.608 0.595 0.553 0.450 0.687 0.567 | 0.450–0.687 | 0.705 |
| S_IG (LOSO) | 0.638 | 0.685 0.679 0.592 0.583 0.782 0.566 | 0.566–0.782 | 0.655 |
| S_Yield (LOSO, ν=0) | 0.615 | 0.605 0.560 0.736 0.709 0.628 0.532 | 0.532–0.736 | 0.834 |
| **S_Net (LOSO, ν_d=1)** | **0.794** | **0.826 0.790 0.787 0.798 0.800 0.741** | **0.741–0.826** | 0.611 |
| S_Net3 (LOSO, ν_d=ν_o=1) | 0.731 | 0.797 0.641 0.777 0.806 0.676 0.717 | 0.641–0.806 | 0.624 |
| S_thin (post-hoc, no fit) | 0.627 | 0.689 0.615 0.630 0.563 0.687 0.588 | 0.563–0.689 | 0.720 |
| S_cert / S_poss / S_sumlb | 0.576 / 0.614 / 0.533 | | | 0.802 / 0.739 / 0.872 |
| null (random strict order, 2,000 draws) | 95% band 0.442–0.558; max 0.591 | | | |

### 3.2 Precision at top-k per seed (6 seeds summed), every ranking asked at the same label-free week `argmax_w P̂(clear|b_w)`; corrected = clears AND inside the gold window; decoy accepted = clears AND inside the decoy window

| rank by | k | questions | gold | decoy | **corrected** | decoy acc | unplanted sup | corrected/q | per-seed corrected |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20 | 120 | 10 | 22 | 8 | 10 | 26 | 0.067 | 2 3 1 0 2 0 |
| baseline | 50 | 300 | 34 | 74 | 23 | 34 | — | 0.077 | |
| S_IG | 20 | 120 | 15 | 17 | 11 | 9 | 31 | 0.092 | 2 3 1 2 2 1 |
| S_IG | 50 | 300 | 42 | 51 | 31 | 25 | — | 0.103 | |
| S_Yield | 5 | 30 | 16 | 11 | 15 | 8 | 3 | 0.500 | 4 0 4 4 2 1 |
| S_Yield | 20 | 120 | 45 | 72 | 43 | 53 | 3 | 0.358 | 8 4 10 10 4 7 |
| S_Yield | 50 | 300 | 66 | 124 | 63 | 101 | — | 0.210 | |
| S_Pclear (label-free) | 20 | 120 | 39 | 79 | 37 | 61 | 2 | 0.308 | 4 5 10 8 4 6 |
| **S_Net** | 5 | 30 | 17 | 6 | 15 | 5 | 5 | 0.500 | 4 0 4 4 2 1 |
| **S_Net** | 20 | 120 | 26 | 9 | 24 | 7 | 62 | 0.200 | 5 3 5 5 4 2 |
| **S_Net** | 50 | 300 | 44 | 32 | 36 | 22 | — | 0.120 | |
| S_Net3 | 5 | 30 | 17 | 9 | 16 | 8 | 3 | 0.533 | 4 0 4 4 2 2 |
| S_Net3 | 10 | 60 | 32 | 18 | 31 | 15 | 5 | 0.517 | 7 1 9 9 2 3 |
| S_Net3 | 20 | 120 | 39 | 31 | 37 | 24 | 17 | 0.308 | 8 4 9 10 3 3 |
| S_Net3 | 50 | 300 | 62 | 62 | 52 | 38 | — | 0.173 | |
| S_thin | 20 | 120 | 22 | 25 | 20 | 14 | 22 | 0.167 | 5 2 5 4 3 1 |

Seed 1 is the weak seed for every utility ranking (0–1 corrected at top-5); the decoy-rich top of seed 1 is three keys in the N1 class that are decoys.

### 3.3 Out-of-pool check — the snapshot (already-asked) pool, 102 gold / 216 decoy, same LOSO tables

| score | AUC gold vs decoy | per seed |
|---|---|---|
| baseline (rule_weeks) | 0.499 | 0.646 0.484 0.471 0.504 0.516 0.397 |
| S_IG | 0.551 | 0.695 0.479 0.465 0.472 0.682 0.480 |
| S_Pclear | 0.663 | 0.718 0.571 0.669 0.659 0.621 0.755 |
| S_Yield | 0.807 | 0.916 0.849 0.747 0.775 0.840 0.858 |
| **S_Net** | **0.842** | **0.931 0.826 0.730 0.828 0.925 0.872** |
| S_Net3 | 0.814 | 0.918 0.829 0.752 0.791 0.891 0.839 |

### 3.4 When to ask (label-free), filtered pool, one question per key

| week policy | corrected | decoys accepted | unplanted supported | one corrected per |
|---|---|---|---|---|
| latest rule week | 0 | 4 | 8 | — (stale: age > 42 days) |
| first fitting rule week (the replay's `rule_snapshots` policy) | 58 | 131 | 43 | 30 questions |
| uniform random rule week (expectation) | 41.7 | 69 | 32 | 42 |
| `argmax_w P̂(clear | b_w)` (label-free) | **99** | 127 | 131 | **18** |
| any rule week (ceiling) | 103 | 164 | 151 | 17 |

### 3.5 Calibration against the recorded replay

Whole rule-only pool (2,831 keys) at the first fitting rule week: gate model 58 corrected = 1 in 49; the Gate-0 replay (`frontier/CANDIDATE_RESCUE.md`) recorded ~1 in 220 for the same policy on the same pool. The gate model (perfect routing to the right sites, perfect lexical extraction, perfect judging) is therefore **×4.5 optimistic**, and every per-question figure above should be read deflated by that factor before it is compared with a recorded number.

## 4. Break-even

Objective: `q* = argmax [E[ΔU] − λ·cost − μ·latency − ν·leakage]`, cost = 1 question, `E[ΔU]` in corrected verdicts net of ν_d decoys and ν_o background confirms per question. Recorded value of an asked question: 93 found / 341 asked = **0.273** corrected/question, with 118 decoy acceptances over the same 341 (≈0.35/question).

| arm (filtered pool unless stated) | corrected/q (gate model) | deflated ×4.5 | decoys/q (deflated) | unplanted/q (deflated) | breaks even iff |
|---|---|---|---|---|---|
| random key, first rule week (whole pool: recorded 1/220 = 0.0045) | 0.033 | 0.007 | 0.016 | 0.005 | λ ≤ 0.007 at ν=0; never at ν_d = 1 |
| random key, label-free week | 0.056 | 0.012 | 0.016 | 0.016 | λ ≤ 0.012 at ν=0 |
| top-5/seed by S_Yield | 0.500 | 0.111 | 0.059 | 0.022 | λ ≤ 0.11 (ν=0); λ ≤ 0.03 (ν=1) |
| top-5/seed by S_Net | 0.500 | 0.111 | 0.037 | 0.037 | λ ≤ 0.11 (ν=0); λ ≤ 0.04 (ν=1) |
| top-5/seed by S_Net3 | 0.533 | 0.119 | 0.059 | 0.022 | λ ≤ 0.12 (ν=0); λ ≤ 0.04 (ν=1) |
| top-20/seed by S_Net3 | 0.308 | 0.068 | 0.044 | 0.031 | λ ≤ 0.07 (ν=0) |
| displacing an average asked candidate (recorded) | — | 0.273 | 0.35 | — | the promoted key must beat this when the budget is full |

Reading: with a full 60-question budget, no filtered-pool key beats the average asked candidate at ν=0 (0.12 < 0.27), so **promotion does not break even by displacement**; the recorded +3 −14 is the expected shape. Free slots exist on seeds 0, 1, 3 (5, 7, 7 = 19 questions over six seeds); filling them from the top of the net ranking is worth ≈ 19 × 0.11 ≈ 2 corrected verdicts (deflated) across six seeds, under one point. At ν_d = 1 the recorded asked set is itself net negative (0.27 − 0.35), which is why the pay-off of `E[ΔU]` is as a **re-ordering of the asked set** (§3.3), not as more questions. The decoy bar (118) binds that too.

## 5. Variants tried (so the falsifier can discount best-of-many)

11 orderings on the same 1,765 keys: baseline, S_cert, S_poss, S_sumlb, S_Pclear, S_ent, S_IG, S_Yield, S_Net, S_Net3, S_thin. One b-class granularity (caps 2/2/3/3/3), one smoothing (α = 2 toward the global rate), one week-selection rule per score (argmax, ties → latest), all fixed before the first run and not revisited. S_Net3 and S_thin were added after S_Net's unplanted-FP column was seen; S_thin is explicitly a post-hoc distillation. Effective sample for the best variant: 138 / 11 ≈ 13 gold keys.

---

## 6. Hypotheses in §2 form

```
H4-1   information gain
  STATISTIC   s(key) = max_w I(H; G | c(b_w)),  G = 1[TB_v4 clears],  c(b_w) = (nC, nU, nP, roots_lb, rep_lb, fresh)
              from the weekly cells over [w-5, w]; P(gold|c), P(G|gold,c), P(G|not gold,c) tabulated on the five
              other seeds (LOSO), Laplace alpha=2
  READS       per-site weekly cells (count, roots, reporters, each '<k' or the count), week index, the key's rule weeks
  FORBIDDEN   confirmed: no gold label, facet record, Placement internal, node id, file path or value equality enters
              s; labels enter only the LOSO fit on other seeds
  CLAIM       s separates gold from decoy in the filtered rule-only pool with AUC >= 0.70
  KILL        AUC gold-vs-decoy < 0.70 on any held-out seed, or fewer corrected verdicts at top-20 than S_Yield
  COST        O(sites x 6) per key-week after one world rebuild per seed (1.2 s/seed with the generator alone; the
              ~90 s figure is the pipeline, which this does not need); then free from h4_cells.json
  TEST        python test_h4_scores.py  (S_IG rows)  -- 20-line reproduction:
              import json; P=json.load(open('h4_per_key_scores.json'))
              F={k:v for k,v in P.items() if v['state']=='rule_only' and v['rule_weeks']>=7}
              o=sorted(F.values(), key=lambda v:(-v['S_IG'],-v['rule_weeks'],v['salt']))
              g=[-i for i,v in enumerate(o) if v['cls']=='gold']; d=[-i for i,v in enumerate(o) if v['cls']=='decoy']
              print(sum(a>b for a in g for b in d)/(len(g)*len(d)))     # 0.638
  RESULT      AUC 0.638 pooled, held-out seeds 0.566 0.679 0.592 0.583 0.782 0.566 (3 of 6 below 0.60);
              top-20/seed: 15 gold, 17 decoy, 11 corrected verdicts over 120 questions (S_Yield: 43).
              IG is zero on every class where b decides the gate (table in section 1d), so it ranks the undecidable,
              rarely-clearing classes first -- the gate's shape makes it the wrong objective, as derived.
  DEAD        kill: AUC < 0.70 on 6 of 6 held-out seeds; 11 corrected / 120 questions vs 43 for the utility objective.
```

```
H4-2   expected corrected verdicts (yield, nu = 0) -- and its label-free proxy
  STATISTIC   s(key) = max_w P(G | gold, c(b_w)) . P(gold | c(b_w))   [LOSO];  label-free proxy s'(key) = max_w
              P(G | c(b_w)) tabulated from the gate's OUTCOMES on the other five seeds (an outcome is not a label)
  READS       as H4-1
  FORBIDDEN   confirmed, as H4-1; the proxy s' touches no label at all
  CLAIM       s separates gold from DECOY in the filtered pool with AUC >= 0.70
  KILL        AUC gold-vs-decoy within the null band or below the rule_weeks baseline (0.600)
  COST        as H4-1
  TEST        as H4-1 with 'S_Yield' (0.615) and 'S_Pclear' (0.584)
  RESULT      gold-vs-decoy AUC 0.615 (held-out 0.532-0.736) fitted, 0.584 (0.545-0.650) label-free; baseline 0.600,
              null 95% band 0.442-0.558. gold-vs-UNPLANTED AUC 0.834 / 0.844 (baseline 0.693).
              top-20/seed: 45 gold, 72 decoy, 43 corrected, 53 decoys accepted (1.2 decoys per gold) -- the +3 -14
              shape. Decoy-blind by construction: the certain-clear classes are 43-57% decoy (645 of 1,129 rows), and
              decoys clear at P = 1.000 there exactly as gold does.
              WHAT SURVIVES: the WEEK it picks. argmax_w P(G|b_w) is label-free, lifts a random filtered key from
              1-in-30 (first rule week, the replay's policy) to 1-in-18 corrected, 96% of the any-week ceiling (103).
  DEAD        kill: AUC gold-vs-decoy 0.615 / 0.584 is at the baseline and the label-free form is inside the null's
              max (0.591). It is the gate-implied objective and it does maximise corrected verdicts, but it does not
              separate gold from decoys, which is the question wave 0 left open.
```

```
H4-3   net expected utility (the gate-implied objective WITH the costs the scorer charges)
  STATISTIC   s(key) = max_w P(G | c(b_w)) . [ P(gold | G, c) - nu_d . P(decoy | G, c) - nu_o . P(other | G, c) ]
              = max_w [ P(G|gold,c).P(gold|c) - nu_d.P(G|decoy,c).P(decoy|c) - nu_o.P(G|other,c).P(other|c) ]
              with c(b_w) = (nC, nU, nP, roots_lb, rep_lb, fresh) from the weekly cells; the three conditional tables
              fitted LOSO (alpha = 2). Reported at (nu_d, nu_o) = (1, 0) [S_Net] and (1, 1) [S_Net3].
  READS       per-site weekly cells (count, roots, reporters; '<k' or the count), week index, the key's rule weeks
  FORBIDDEN   confirmed: s reads only exported cells and rule weeks; labels enter the fit on the five other seeds only.
              h4_fold_tables.json lists every fitted class per fold so each number can be traced.
  CLAIM       s separates gold from DECOY in the filtered pool with AUC >= 0.70 on every held-out seed, and orders the
              already-asked snapshot pool above 0.70 as well
  KILL        any held-out seed below 0.70; or the top classes differing across folds (would mean the table is noise);
              or a null statistic reaching 0.74 on the same pool
  COST        as H4-1; O(1) per key-week once the three tables exist
  TEST        as H4-1 with 'S_Net' (0.794) / 'S_Net3' (0.731); python test_h4_budget.py for the top-k and snapshot rows
  RESULT      S_Net: AUC gold-vs-decoy 0.794; held-out seeds 0.826 0.790 0.787 0.798 0.800 0.741 -- every seed above
              the baseline's best seed (0.664) and the null's max over 2,000 draws (0.591). S_Net3: 0.731 (0.641-0.806;
              seed 1 ties the baseline's seed 1 at 0.64). Top-5/seed over 30 questions: S_Net 17 gold / 6 decoy, 15
              corrected, 5 decoys accepted, 5 unplanted; S_Net3 17 / 9, 16, 8, 3. Top-20: S_Net 26 / 9 (24 corrected,
              7 decoys, 62 unplanted); S_Net3 39 / 31 (37, 24, 17). Baseline top-20: 10 / 22 (8, 10, 26).
              Snapshot pool (already asked, 102 gold / 216 decoy): S_Net 0.842 (0.730-0.931), S_Net3 0.814, baseline
              0.499. Every fold's top class is (1,0,2,3,3,1): one certain k-site + exactly one weak site, roots and
              reporters >= 3, fresh -- 63-67% gold, 30-34% decoy in every fold -- followed by thin one-k-site states;
              classes with two certain k-sites or three sites present are net negative in every fold.
              Mechanism (generator, corpus.py): every decoy is planted at facet_support_common (>= 3 records per
              facet, round-robin over 2-4 regions) so it never passes through a thin state; rare gold is planted at 1-2
              records per facet and does. The N1 rescue admits exactly that state. S_thin, the parameter-free
              distillation, reaches only 0.627 because the max-over-weeks and the roots/reporters certainty do
              real work in the fitted form.
  CAVEATS     (i) LOSO-fitted lookup table, 11 variants tried, effective sample ~13 gold per variant; (ii) the gate
              model is x4.5 optimistic against the recorded replay, so deflated the top-5 is worth ~0.11 corrected per
              question against 0.27 for an average asked candidate -- it does NOT break even by displacement into a
              full budget at nu = 0; it pays into free slots (19 over six seeds, < 1 point) or as a re-ordering of the
              60 questions already asked (section 3.3), which is the lever Gate-0 confirmed e2e (+8); (iii) the
              signal is a construction fact of this generator (decoys are never thin); sealed seeds share the
              generator, a different world need not; (iv) S_Net trades decoys for unplanted confirms (62 at top-20) --
              nu_o must be set by whoever owns the background-confirm bar; S_Net3 is the balanced form.
  PROMOTED    AUC 0.794 (min held-out 0.741) at (1,0); 0.731 (min 0.641) at (1,1). First function to change if wave 3
              confirms: ng-deploy:research/e2e_ingest/emergence_world.py:ordered_candidates -- order by E[dU] over the
              candidate's exported cells instead of (-score, salt); no new questions, same budget.
```

Why the other two are worse: H4-1 is the wrong objective for a threshold gate (derived in §1, confirmed in §3.2: it ranks what it cannot resolve); H4-2 is the right objective without its cost term and is therefore decoy-blind exactly where the pool is decoy-rich.

## 7. Files

- `test_h4_extract.py` — the one world-rebuild pass (6 seeds, 1.2 s each): per pool key the k-suppressed weekly cells and, per week, the gate model's answer (`h4_cells.json`, 10 MB). Sanity: reproduces the pinned `ceiling_b.json` feasible weeks exactly except that it requires a full 6-week window (weeks W01–W05 excluded; the pack's `min_window_weeks` is 6), which costs 1 of 205 (B) and 1 of 199 (K).
- `test_h4_scores.py` — features, the 11 scores, LOSO tables, AUCs, precision, null, objective table, break-even (`h4_report.json`, `h4_per_key_scores.json`, `h4_fold_tables.json`; console in `scores_run3.txt`).
- `test_h4_net_detail.py`, `test_h4_budget.py` — top-k accounting at a common week, snapshot-pool check, calibration, class composition (`net_detail_run1.txt`, `budget_run1.txt`).

# F-H3 — falsification of H3-3 (non-monotone rule-week band: 9–15 → 2, ≥16 → 1, ≤8 → 0)

Read-only. Pool: `raw/plateau/candidate_pool.csv`, `detector_state == rule_only`, `rule_weeks >= 7` (1,765 keys: 138 gold, 317 decoy, 1,310 other; per seed 20–27 gold vs 46–62 decoy). Longest contiguous run from the dumps' `rule_weeks` lists; feasibility from `raw/plateau/ceiling_b.json` `feasible_weeks.F.TB_v4` (matches the CSV `F_TB_v4_feasible` flag on all 240 gold rows). AUC = Mann-Whitney, ties 0.5. Dev seeds 0–5 only. Labels score; nothing selected. Script `f_h3.py`, full output `f_h3.txt`, both beside this file.

## Summary (10 lines)

1. The one formula, no re-tuning, scores pooled AUC gold-vs-decoy **0.704** (per seed .709 .685 .723 .753 .663 .700). The free line (longest contiguous rule run) is **0.668** pooled, per seed .705 .685 .633 .629 .719 .625.
2. The band beats the 0.668 line on **5/6** seeds and beats the same seed's longest-run AUC on **4/6** (s1 ties at .685; s4 loses .663 vs .719). The brief required 6/6. **KILL 1: KILLED.**
3. Pooled paired difference band − longest = **+0.036**, bootstrap sd 0.030, one-sided P(diff ≤ 0) = **0.12**. Not significant at k = 1; at k = 8 (H3's variant count) the band would need pooled AUC **> 0.742** over the free line; the 119-pair edge grid would need > 0.767. Observed 0.704. **KILL 2: KILLED.**
4. Against interest: the formula itself is seed-swap invariant by construction (one exported field of the key), and the *edge choice* re-done honestly (LOSO: edges (9,13)/(9,15), held-out mean 0.694, min 0.663; 3-vs-3 swaps: band beats longest run on **19/20** splits by +0.025 on average). The sign is stable; the margin is inside the noise. (9,15) ranks 3rd of 119 in-sample — post hoc, as H3 said.
5. Break-even: top-20 per seed (band, H3's salted-hash tie-break) holds **17/120 gold = p 0.142**, 12 feasible → c_g 0.706 → **p\* = 0.390**; p/p\* = 0.36. Top-10: 8/60 = 0.133, c_g 0.625, p\* 0.440. Over 300 salts the top-20 gold is 17.7 ± 3.7 (max 28 → p 0.233); P(p ≥ 0.47) = 0. **KILL 3 (a): KILLED.**
6. Budget (b): top-20 asks **22 decoys × 0.551 = 12.1 accepted decoys** (top-10: 9 × 0.551 = 5.0) against a bar with zero headroom; minimum over 300 salts is 11 decoys at top-20, so P(0 added decoys) = 0. p = 0.142 is also below p\*_b = 0.19. **KILL 3 (b): KILLED.**
7. Not a disguise: Spearman(band, longest run) = **+0.42**; partial AUC of band within longest-run strata **0.576** (12 % of pairs comparable); logistic LR for band | longest = 5.9 on 2 df, **p = 0.052**; longest | band = 46.1 on 20 df, p = 8e-4. Longest run carries more that the band lacks than the reverse. **KILL 4: SURVIVED as a distinct statistic** — which does not help it.
8. Decomposition: the band = [rw ≥ 9 cut] + [≥ 16 demotion]. The cut alone is AUC **0.661** = H8's N3c best single cut, i.e. the free line restated. The non-monotone novelty (demoting ≥ 16 below 9–15) adds **+0.044** pooled; on the 307 keys it acts on it scores AUC **0.5842**, null sd 0.0336, z = 2.50, p = 0.006 at k = 1 — and the k = 8 Bonferroni line on that subset is **0.5840**. The novelty is the discount, to three decimals.
9. The realised ranker (band + hash) has only three levels; nearly every gold–decoy pair is a tie resolved by the salt. Pooled AUC over 300 salts: 0.705 ± 0.010 (0.681–0.734); the salt alone moves it by a third of a seed's sd. Inside the band the ranker is a coin.
10. Post hoc observation, flagged as one more variant, not a claim: the same band applied to **longest run** instead of rule-week count scores 0.741 pooled. It is untested, best-of-(8+1), and inherits KILL 3 (the band-2 population is 14.5 % gold whatever counts it). Recorded for whoever owns the persistence family (H5); not promoted here.

---

## KILL 1 — one formula, no re-tuning, vs the free line

| | s0 | s1 | s2 | s3 | s4 | s5 | pooled | mean / min |
|---|---|---|---|---|---|---|---|---|
| band 9–15 → 2, ≥16 → 1, ≤8 → 0 | .709 | .685 | .723 | .753 | **.663** | .700 | **.704** | .706 / .663 |
| longest contiguous rule run (free) | .705 | **.685** | .633 | .629 | **.719** | .625 | .668 | .666 / .625 |
| rule-week count (wave 0) | .672 | .668 | .550 | .494 | .671 | .583 | .611 | .607 / .494 |
| band − longest | +.004 | +.000 | +.090 | +.124 | −.055 | +.074 | +.036 | |
| bootstrap P(diff ≤ 0), one-sided | .49 | .51 | .12 | .04 | .73 | .15 | **.12** | |

Per-seed null sd of an AUC is 0.067–0.077; the band's six numbers span 0.090, below the 0.175 median range that pure noise produces across six seeds (H8 §1), so the consistency is not evidence either way. Realised ranker (band + sha256 tie-break) over 300 salts: pooled AUC mean 0.705, sd 0.010, range 0.681–0.734.

Pre-registered kill: beat the free line on every seed. Result: 5/6 vs 0.668, 4/6 vs the same-seed longest run. One seed (s4) has its gold at rw ≥ 16 and the band demotes it.

**VERDICT: KILLED.**

## KILL 2 — seed-swap and the best-of-many discount

**(i) Seed-swap of the formula.** s(key) = band(len(rule_weeks)) is a function of one exported field of the key itself. Refitting on any seed subset cannot change it for any of the 1,765 keys. It passes H8 §5 item 5 trivially — and trivially is the point: what H3 fitted is not the formula but its two edges.

**(ii) Seed-swap of the edges.** Grid: lo ∈ 7..13, hi ∈ lo..26, structure fixed (middle > above > below): 119 pairs. In-sample best is (9,13) at 0.708, then (9,14) 0.705, then **(9,15) at 0.704 — rank 3 of 119**.

| held-out seed | edges chosen on the other five | train AUC | held-out AUC | fixed (9,15) on that seed | longest run on that seed |
|---|---|---|---|---|---|
| s0 | (9,13) | .708 | .712 | .709 | .705 |
| s1 | (9,13) | .718 | .665 | .685 | .685 |
| s2 | (9,15) | .701 | .723 | .723 | .633 |
| s3 | (9,13) | .705 | .718 | .753 | .629 |
| s4 | (9,15) | .711 | **.663** | .663 | .719 |
| s5 | (9,13) | .713 | .679 | .700 | .625 |

LOSO-edge held-out: mean 0.694, min 0.663; above the same seed's longest run on 4/6, above 0.668 on 4/6. All 20 three-vs-three swaps: edges chosen ∈ {(9,9), (9,12), (9,13), (9,14), (9,15), (9,17)}, i.e. lo = 9 always, hi unstable 9–17; held-out band mean 0.692 (0.653–0.714); band beats longest run on the test half on **19/20** splits, by +0.025 on average. Reported against interest: the direction is reproducible; the size is not distinguishable from zero.

**(iii) The AUC the one formula would need, after the discount, to stand at p < 0.05.**

| comparator | k = 1 | k = 8 (H3's variants) | k = 16 (sign flip) | k = 119 (edge grid) | H8 empirical best-of-10 / 50 |
|---|---|---|---|---|---|
| vs N1 noise (sd 0.0295) | > 0.548 | > 0.574 | > 0.581 | > 0.598 | > 0.60 / 0.62 |
| vs the free line, paired on the same keys (sd of the difference 0.0296) | **> 0.717** | **> 0.742** | > 0.749 | > 0.767 | — |

Observed 0.704. It clears every noise line — the band is not luck, and nobody said it was. It clears the free line at **no** k, not even k = 1 (p = 0.12). After the k = 8 discount it needs 0.742.

**VERDICT: KILLED** (fails the free line at k = 1; the honest edge choice does not rescue the per-seed minimum: 0.663 on s4 either way).

## KILL 3 — break-even at top-10 / top-20 per seed

Ranker: band descending, ties by sha256(`e2e-ingest-v1|` + key) — H3's tie-break — over the full filtered pool of each seed (other keys compete, as they would in a replay). c_g measured on the gold in the top-k from `ceiling_b.json` F/TB_v4 feasible weeks.

| seed | band-2 keys | top-10 gold (feasible) / decoy / other | top-20 gold (feasible) / decoy / other |
|---|---|---|---|
| s0 | 110 | 2 (1) / 0 / 8 | 3 (2) / 0 / 17 |
| s1 | 114 | 2 (1) / 2 / 6 | 4 (3) / 4 / 12 |
| s2 | 95 | 1 (1) / 2 / 7 | 3 (2) / 5 / 12 |
| s3 | 97 | 2 (1) / 1 / 7 | 3 (2) / 3 / 14 |
| s4 | 103 | 1 (1) / 2 / 7 | 2 (1) / 5 / 13 |
| s5 | 100 | 0 (0) / 2 / 8 | 2 (2) / 5 / 13 |
| **six seeds** | 619 | **8/60 = 0.133** (5 feasible, c_g 0.625, **p\* 0.440**, p/p\* 0.30) | **17/120 = 0.142** (12 feasible, c_g 0.706, **p\* 0.390**, p/p\* 0.36) |

(a) Displacing: found at feasible weeks 5 (top-10) / 12 (top-20) against 16.5 / 33.0 displaced. **Fails at both depths**, p is 0.30–0.36 of p\*.
(b) Additive, decoy bar at zero headroom: asks 9 decoys at top-10 → 9 × 0.551 = **5.0** accepted decoys added; 22 at top-20 → **12.1**. Breach at both depths. p = 0.142 is also below the λ = 1 additive bar p\*_b = 0.19.

The tie-break is the ranker inside the band, so over 300 salts: top-20 gold 17.7 ± 3.7 (max 28 → p 0.233), decoys 19.8 ± 3.7 (min 11); top-10 gold 8.9 ± 2.6 (max 17 → 0.283), decoys 9.9 ± 2.7 (min 5). P(p ≥ 0.47) = 0.000 and P(zero decoys) = 0.000 at either depth. The band-2 population itself: 619 keys, 90 gold (0.145), 61 feasible (c_g 0.678, p\* 0.406), 102 decoys (0.165). No salt, no depth, no budget.

**VERDICT: KILLED.**

## KILL 4 — is the band longest-run in disguise?

| statistic | value |
|---|---|
| Spearman(band, longest run), gold+decoy subset / full pool | **+0.421** / +0.464 |
| Spearman(band, rw count) / Spearman(longest, rw count) | +0.481 / +0.814 |
| AUC longest run alone, same keys | 0.668 |
| partial AUC of band within exact longest-run strata (12.3 % of gold–decoy pairs comparable) | **0.576** |
| partial AUC of band within longest-run buckets ≤7 / 8 / 9–12 / 13–15 / 16+ (21.8 %) | 0.565 |
| partial AUC of longest run within band strata (31.6 %) | 0.587 |
| logistic LR, band \| longest (2 df) | 5.9, **p = 0.052** |
| logistic LR, longest \| band (20 df) | 46.1, p = 8e-4 |

Cross-tab (gold/decoy): band = 0 is almost entirely longest = 8 (13/130: the single window-length run); band = 2 is spread over longest 8 (9/37), 9–12 (61/44), 13–15 (20/19); band = 1 is longest 16+ (21/52) plus 13 gold / 29 decoy at shorter runs. The two statistics disagree on most pairs, so the band is not longest run renamed.

Decomposition of where the band's 0.704 comes from:

| component | AUC |
|---|---|
| indicator rw ≥ 9 alone (≤ 8 demoted, 9+ tied) | **0.661** = H8 N3c's best single cut |
| ≥ 16 demotion alone (≤ 8 and 9–15 tied) | 0.505 |
| full band | 0.704 → the non-monotone part adds **+0.044** |
| on the 307 keys the demotion acts on (rw ≥ 9: 124 gold / 183 decoy): band | **0.5842**; null sd 0.0336; z 2.50; p 0.006 at k = 1; **k = 8 Bonferroni line 0.5840** |
| same keys: rw count / longest run | 0.406 (anti-informative) / 0.530 |
| per seed on rw ≥ 9 keys | .516 .546 .631 .757 .461 .608 (5/6 above 0.5) |

So: H3-3 is the free cut (rw ≥ 9, which is the free line in another coat) plus a genuine non-monotone residual whose strength, on the keys where it exists, equals the best-of-8 discount to the third decimal. Post hoc and against this agent's own interest: the same three-level band applied to longest run instead of the count gives 0.741 pooled — one more variant (k = 9), untested, and it cannot escape KILL 3 because its band-2 population is the same 14.5 %-gold crowd.

**VERDICT: SURVIVED** as a statistic distinct from longest run (not a disguise). That survival changes nothing: the distinct part is worth +0.04 and is not significant after the discount H3 itself declared.

---

**H3-3: DEAD after wave 2, because** the one pre-registered formula beats the free line on 4/6 seeds (not 6/6), its pooled margin over longest run (+0.036, p = 0.12) survives no discount (needs 0.742 at k = 8, observed 0.704), and at top-10/top-20 it delivers p = 0.13–0.14 against a measured p\* of 0.39–0.44 while adding 5–12 accepted decoys to a bar with zero headroom; the only thing it adds to persistence is a ≥ 16 demotion worth AUC 0.584 on 307 keys, which is exactly the best-of-8 line.

---
Hygiene note: one unquoted `echo ... pattern->key mapping` in an inspection command created an untracked 52-byte file `/home/user/NeuralGraph/key` at 16:20:22Z; removed at the end of the run, `git status` clean in both trees, no tracked file touched.

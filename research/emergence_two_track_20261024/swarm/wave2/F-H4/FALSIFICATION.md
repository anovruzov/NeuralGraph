# F-H4 — falsification of H4-3 (net expected utility over the exported cell class) and H4-2's week choice

Read-only. No e2e, no pytest, no broker, no repo touched (`git status` clean in ng-recovery, ng-deploy, NeuralGraph).
Dev seeds 0–5 only. Labels score, never select (one exception is *the target itself*: S_Net3 is a table fitted on the
other five seeds' labels — see Kill 2 and the policy note in Kill 3). Scripts `f_*.py`, outputs `f*_*.txt/.json`, all under
`swarm_out/F-H4/`. START 16:18:05Z.

## Summary (10 lines)

1. **Kill 1 — KILLED by its own criterion.** The pre-registered procedure (class, ν = (1,1), LOSO, α = 2) reproduces H4 to three decimals: pooled AUC **0.731**, held-out seeds **0.797 0.641 0.777 0.806 0.676 0.717**. H4-3's own KILL line was "any held-out seed below 0.70": seeds 1 and 4 are below it. No seed is below H8's 0.61–0.63 line. S_Net (ν = (1,0), the 0.794 that was promoted) survives that criterion but trips H8's ≥ 0.79 leak alarm.
2. **Kill 2 — no leak found, but the AUC is not measuring utility.** Provenance: H4 rebuilt the cells from the generator; they match HQ's actual stored cells on 3,134/3,178 asked rows (98.6%). Table is indexed by the 6-tuple class only (64 classes). Label-flip passes; a 200-permutation null of the *whole* fit+score procedure tops out at 0.592 (0/200 ≥ 0.731). **But 1,721/1,765 filtered keys (117/138 gold, 298/317 decoy) are scored at classes where the gate never clears**; 80% of the gold–decoy pairs are between such keys and are ordered by 1e-6…1e-8 Laplace residues, which encode "fewer sites present first". Among keys that can actually yield a verdict the AUC is **0.657** (399 pairs).
3. **Kill 3 — SURVIVED (replay, not e2e).** Reordering the already-asked detector pool by S_Net3 *at the snapshot week* and replaying at B = 60 with `tools/rescue/common.score`: **138/240** found (rule-hit-first 128 replay / 127 e2e; shipped 118/119), **decoys 97** (recorded 113), bg 11 (13), rare 10 (8); per seed [18 20 26 28 22 24] vs [18 18 23 26 21 22]: 5 better, 1 tie (sign p = 1/32). Stable under seed-swap of the table (136–139, decoys 96–97) and on both 3/3 splits.
4. **Kill 3 caveats that are not optional.** (i) H4's snapshot-pool AUC 0.842/0.814 is **look-ahead**: the per-key max-over-weeks score reads a week *after* the asked week on 426/515 asked rows (82.7%); at the asked week the AUC is 0.66 on the v4 pool and **0.658 on the official-ce0e357 asked set** (per seed 0.56–0.78). (ii) The selector is a label-fitted table (LOSO) — H8 §2 calls that "labels selecting"; label-free orderings reach **137** (P̂(clear|b)-first or "gate certainly clears from b"-first) but drop rare 8 → 4 and keep decoys at 110–111. (iii) The official asked set has no slack (341 of 347 asked), so a reorder can only be tested on the v4 pool.
5. **Kill 4 — KILLED at scale.** At 2,000 users (seeds 0–5) the thin class (1,0,2,≥3,≥3,fresh) is **90.9% unplanted** (gold 3.5%, decoy 5.6%; net3 −0.93); gold sits at ≥ 4 sites in 69% of its in-window weeks (200 users: 16%). LOSO fit at 2,000: AUC **0.451** all keys / 0.471 persistent; the 200-user table transferred: 0.522/0.528; the bare thin indicator 0.563. Same definition at 200 users: 0.616/0.621. The mechanism is a 200-user generator artefact.
6. **Kill 4 against H4's mechanism statement.** Even at 200 users it is *common* gold that passes through the thin state (50/137 common vs 6/103 rare, in-window). "Only rare gold passes through the thin N1-shaped state" is false as written.
7. **Kill 5 — H4-2 week choice: reproduces (58 → 99 corrected of 1,765; ceiling 103), seed-stable (≤ 13/308 keys change week under any 4-seed refit; 3/3 splits give 47/54 vs LOSO 46/53). It does not stand alone as a VoI result:** P̂(clear|class) is exactly 0 or 1 on 41/64 classes carrying 86.8% of rows (it is the gate's deterministic reading of b), a parameter-free `argmax_w sumlb` gets **95**, a lexicographic structural rule **101**, and the table's week equals the structural week on 1,526/1,765 keys. The policy also triples unplanted supports (43 → 131) with decoys 127. Gate-model number, ×4.5 optimistic by H4's own calibration, not replayable (verdicts exist only at the v4 week).
8. **Kill 6 — break-even with c_g measured.** At S_Net3's own argmax week c_g(F) = 0.235 (top-5) / 0.156 (top-10) / 0.154 (top-20): p*_a = 1.17–1.79, nothing clears. At the P̂(clear) week c_g(F) = 0.77/0.81/0.77: p*_a = 0.36/0.34/0.36; top-5 (p = 0.567) and top-10 (0.533) clear budget (a) at face value, top-20 (0.325) and top-30 (0.250) fail. Budget (b) clears on the formula but breaches the zero-headroom decoy bar (a_d = 0.78–0.89 on the top-k decoys). With H4's own ×4.5 deflation p*_a > 1 at every k: **nothing clears**.
9. **Net for wave 3.** The expected-utility story is dead: it fails its own per-seed kill at the pre-registered ν, inverts at 2,000 users, and its AUC is mostly an ordering of keys that can never pay. What survives is narrower and empirical: ordering the asked detector pool by the exported class at the snapshot week beats the e2e-confirmed rule-first ordering in replay by +10/240 with 16 fewer decoys. That is a candidate for e2e, not a result, and the labels-select ruling decides whether a LOSO-fitted table may be the selector at all; the label-free 137 is the fallback.
10. Reported against interest: the permutation null (0/200 reach 0.731) says the signal is not luck; the replay gain is robust to every seed-swap tried; the label-free structural orderings in `f3b` are best-of-11 and are so labelled.

---

## Kill 1 — ONE PROCEDURE (`f1_rerun.py` → `f1_rerun.txt`, `f1_rerun.json`)

Procedure, exactly as pre-registered and as H4 coded it (`f_common.py` copies `test_h4_scores.py`'s `b_features`, `cls_of`,
`fit_tables`, strict salted ordering): class (nC, nU, nP, roots_lb, rep_lb, fresh) from k-suppressed weekly cells over
[w−5, w]; S_Net3 = max over the key's rule weeks of P̂(G|gold,c)·P̂(gold|c) − P̂(G|¬gold,c)·(1−P̂(gold|c)), tables LOSO with
α = 2; filtered pool rule_only ∧ rule_weeks ≥ 7 = 1,765 keys (138 gold / 317 decoy / 1,310 other). 42,912 (key, week) rows.

| score | pooled (strict) | pooled (ties ½) | held-out seeds 0…5 | min |
|---|---|---|---|---|
| **S_Net3, ν = (1,1)** — the target | **0.731** | 0.730 | 0.797 0.641 0.777 0.806 0.676 0.717 | **0.641** |
| S_Net, ν = (1,0) — what was promoted | 0.794 | 0.795 | 0.826 0.790 0.787 0.798 0.800 0.741 | 0.741 |
| baseline rule_weeks | 0.600 | 0.611 | 0.664 0.646 0.549 0.484 0.664 0.564 | 0.484 |

Identical to H4's reported numbers on every seed. Against the lines: H4-3's own KILL ("any held-out seed below 0.70")
→ seeds **1 and 4** fail. H8 single-seed line 0.61–0.63 → no seed below. H8 free line 0.668 → seed 1 below. H8 leak alarm
≥ 0.79 → S_Net3 does not trip, S_Net trips.

The fitted object: 64 classes. The top class (1,0,2,3,3,1) holds 114 rows, 75 gold rows from **56 gold keys**, 37 decoy
rows, P(clear) 0.984, net3 +0.296. Where the filtered keys are actually scored (class at the argmax week): 87 gold and
150 decoy keys at (0,0,1,0,0,0), 77 decoys at (0,0,3,0,0,0), 13 gold / 41 decoy at (0,0,2,0,0,0) — i.e. **most keys are
ranked at empty classes** (see Kill 2).

**VERDICT: KILLED** — at the pre-registered ν = (1,1) the procedure fails H4-3's own per-seed criterion on two of six
seeds. (At ν = (1,0) it passes that criterion and trips the leak alarm instead.)

## Kill 2 — LEAKAGE, H8 §5 in full (`f2_leak.py` → `f2_leak.txt`, `f2_leak.json`)

1. **Field provenance.** Features are per-site weekly `n`, `n_roots`, `n_reporters` ('<k' or count), week index, rule weeks.
   All three counts are in the exported contract (`CellRow`, `mycelic/collective/detect/store.py:231`). But H4 **did not
   read HQ's cells**: `test_h4_extract.py` rebuilds them from `corpus.recs` (model B, every record on the key). Checked
   against HQ's actual stored cells (`pool_s*.json` `win_lb`/`win_sites`, from `store.detection_inputs`) at all 3,178
   asked (key, week) rows: lower-bound vector identical on **3,134 (98.6%)**, site-count vector on 3,148 (99.1%); the 44
   disagreements are all generator > HQ (records HQ never stored). Roots/reporters per cell cannot be checked against the
   dump (it recorded only `n`); provenance of those two rests on contract reading.
2. **Missingness-by-class.** Nothing read is populated on one class and empty on another; the inputs are counts.
3. **Fitted table.** Indexed by the 6-tuple class only (`Counter` keyed by `r['cls']`): no entity, seed or key index. 64
   classes. The fit weights a key by its askable weeks (gold 15.2, decoy 13.6, other 13.3 rows per key — a mild tilt).
   **Granularity is fine (64 groups, ~455 gold+decoy keys), but LOSO protects it.**
4. **Label-flip.** Held-out key flipped → s(key) moves 0 (LOSO, trivially). One *training* gold key flipped → held-out
   scores move ≤ 3.6e-3, held-out AUC unchanged to three decimals on all six seeds.
5. **Seed-swap.** Refit on 4 of the 5 training seeds, per held-out seed: seed 0 AUC 0.702–0.801, seed 1 0.641–0.735,
   seed 4 0.676–0.782; **top-5 overlap with the 5-seed fit falls to 1/5 on seed 4 under two of five drops** (the top-5 is
   exactly where the promotion arithmetic lives). 3/3 splits: 0.741 (fit 0–2 → 3–5), 0.707 (fit 3–5 → 0–2). The top
   class (1,0,2,3,3,1) is top-1 in five folds, top-2 in fold 2 — H4's "top classes differ across folds" kill does not fire.
6. **Subset membership.** All orderings over the full 1,765-key pool; no subset normalisation.
7. **Window words.** None in the score; windows enter only the corrected/accepted counts.
8. **Too-good-to-be-honest.** Permutation null of the *entire* LOSO fit+score (labels permuted among keys within seed, 200
   draws): pooled mean 0.499, 95th 0.549, 99th 0.568, **max 0.592**; min-over-seeds max 0.515. Observed 0.731 (min 0.641)
   is exceeded by 0/200. H4's random-order null (0.591) happened to be the right scale. The signal is not luck.
9. **Where the AUC comes from (the finding of this kill).** The gate never clears at the argmax-week class of
   **1,721/1,765 filtered keys (117/138 gold, 298/317 decoy)**. Pair decomposition of the strict ordering:
   both-never-clearing 34,866 of 43,746 pairs (**79.7%**), gold above decoy in 0.730 of them; clearing-gold vs
   never-clearing-decoy 6,258 pairs, 1.000; never-clearing-gold vs clearing-decoy 2,223 pairs, 0.000; both-clearing
   **399 pairs, 0.657**. The never-clearing classes are ordered by Laplace residues: (0,0,1,0,0,0) −1.5e-8 > (0,0,2,0,0,0)
   −8.1e-8 > (0,0,3,0,0,0) −5.3e-7 — **"fewer sites present first"**, the generator's gold-thin/decoy-broad fact that H1,
   H2 and H5 found independently. The 0.731 is an honest number for the wrong quantity: it mostly ranks keys that cannot
   pay, by site breadth, and calls the result expected utility.

**VERDICT: SURVIVED the leak checklist** (no label leaks; provenance 98.6%; null 0/200) — **with the AUC reinterpreted**:
its utility-relevant part is 0.657 on 399 pairs, inside H8's selection band.

## Kill 3 — THE ONLY BUDGET-NEUTRAL USE (`f3_replay.py`, `f3b_stress.py`, `f3c_official.py` → `.txt/.json`)

Protocol: the Gate-0 replay (`tools/rescue/common.score` on `raw/rescue/pool_s{0..5}.json`, every v4 candidate asked, real
verdicts), detector pool 84–90 per seed, B = 60, tie-break `sha256("rescue|key")` as in `det_rank.py`. The asked week is
the detector's snapshot week and cannot change in a replay, so the pre-registered score is **S_Net3 of the exported class
at the snapshot week**, LOSO-fitted on the other five seeds (cells from `h4_cells.json`; 3 keys rebuilt from the generator).
Reproduction first: shipped **118** [18 15 22 24 18 21], rule-hit-first **128** [18 18 23 26 21 22], decoys 113, bg 13 — the
recorded replay exactly.

| ordering of the asked detector pool, B = 60/seed | found/240 | rare | decoys | bg | per seed | decoys by class |
|---|---|---|---|---|---|---|
| shipped | 118 | 7 | 113 | 13 | 18 15 22 24 18 21 | {1:7, 2:53, 3:8, 5:45} |
| rule hit first (Gate-0, e2e 127) | 128 | 8 | 113 | 13 | 18 18 23 26 21 22 | {1:7, 2:53, 3:8, 5:45} |
| **S_Net3 at snapshot week, then shipped — pre-registered** | **138** | **10** | **97** | **11** | **18 20 26 28 22 24** | {1:6, 2:38, 3:12, 4:1, 5:40} |
| P̂(clear\|b) at snapshot week (label-free) — diagnostic | 137 | 4 | 111 | 6 | 21 20 27 28 20 21 | {1:8, 2:45, 3:11, 4:3, 5:44} |
| S_Net (ν=1,0) at snapshot — diagnostic | 140 | 12 | 95 | 13 | 18 20 27 28 23 24 | |
| S_Net3 H4 per-key max-over-weeks — diagnostic, **look-ahead** | 137 | 13 | 90 | 16 | 22 19 25 27 21 23 | |
| random (300 draws) | 104.9 mean, 95th 113, max 116 | | | | | |

Against the pre-registered kill: **138 > 127 (e2e) and > 128 (replay); decoys 97 < 113; bg 11 < 13; rare 10 > 8.** Per
seed vs rule-first: 5 better, 1 tie (one-sided sign p = 1/32); vs shipped 6/6. Swap accounting: 9–14 of 60 questions change
per seed; the dropped questions are mostly supported decoys (3–12 per seed), gains outnumber losses on 5 seeds, tie on seed 0.

Stress (`f3b`): seed-swap of the table inside the replay (4 of 5 training seeds, each drop) → **136–139 found, decoys
96–97**; 3/3 splits: fit 0–2 → replay 3–5: S_Net3 74 vs rule-first 69 (decoys 48 vs 59); fit 3–5 → 0–2: 62 vs 59 (49 vs 54).
Label-free structural orderings at the snapshot week (11 variants, tried after seeing the result — best-of-11 applies):
"gate certainly clears from b" first 137 (rare 4, decoys 110, bg 3); sumlb desc 129; nP asc 125 (decoys 86); thin-first 119;
none reach 138, but **137 is available without any label-fitted table**, at the cost of rare (4 vs 10) and decoys (110 vs 97).

Three things the controller must weigh:
- **Look-ahead in H4's §3.3 claim.** H4's per-key score takes the max over all of a key's weeks; on the asked pool its
  argmax week is **after** the asked week on 426/515 rows (82.7%; 271 decoys, 118 gold). The 0.842 / 0.814 "already-asked
  pool" AUCs read cells HQ did not have. At the asked week the AUC is **0.661** (v4 pool, key class) and **0.658 on the
  official-ce0e357 asked set** (per seed 0.613 0.610 0.661 0.558 0.711 0.782; `f3c`, which also reproduces the official
  `found` 13/12/17/17/19/15 exactly). The replay gain above uses the clean at-snapshot score.
- **Labels select.** S_Net3 is a table fitted on gold/decoy labels of five seeds and applied to the sixth. H8 §2: "a table
  fitted on labels is labels selecting", void by the brief's rule. WAVE_1 accepted LOSO-fitted scores for evaluation; whether
  one may be the *selector* is a ruling, not a measurement. If the ruling is no, the label-free 137 stands in its place.
- **Replay, not e2e.** The instrument predicted both Gate-0 arms within one pattern; it still inherits v4's question load,
  and this ordering has never been run.
- The official run asked 341 of its 347 snapshot candidates (55/53/60/53/60/60 ≤ 60): **no slack**, so a reorder of the
  official asked set is not a test; the v4 pool (84–90 vs 60) is the only place the lever exists.

**VERDICT: SURVIVED (replay-level).** It beats 127/128 with fewer decoys on the pre-registered protocol and is stable under
seed-swap. It is a candidate for one e2e arm, not a result, and only after the labels-select ruling.

## Kill 4 — SCALE (`f4_scale.py` → `f4_scale.txt`)

No detector dump exists at 2,000 users, so the pool is every key with a record (1,186–1,196 per seed at 2,000; 1,008–1,047 at
200), all askable weeks, gate model B for `tb`; the **same definition at 200 users** gives the like-for-like reference.
Seeds 0–5 at both sizes (0.9–4.0 s per world). Sites: 7–8 at 200 users, 7–13 at 2,000.

| | 200 users | 2,000 users |
|---|---|---|
| THIN class rows (1,0,2,≥3,≥3,1): gold / decoy / other | 130 rows: **57.7% / 31.5% / 10.8%**, net3 +0.138 | 2,567 rows: **3.5% / 5.6% / 90.9%**, net3 **−0.930** |
| gold keys with ≥ 1 thin in-window week | 56/240 = 23.3% (rare 6/103, **common 50/137**) | 31/240 = 12.9% (rare 5/92, common 26/148) |
| decoy keys with ≥ 1 thin in-window week | 13/572 = 2.3% | 15/593 = 2.5% |
| unplanted keys ever thin | 5/5,309 = 0.1% | **695/6,317 = 11.0%** |
| gold's sites present at in-window weeks (nP 1/2/3/≥4) | 295 / 822 / 642 / 330 (≥4: 16%) | 41 / 208 / 397 / **1,440 (≥4: 69%)** |
| top class by net3 | (1,0,2,3,3,1) | (1,2,3,3,3,0), 35 rows, net3 +0.023 |
| AUC gold-vs-decoy, LOSO fit at this size, all keys / persistent (≥7 active weeks) | 0.616 / 0.621 | **0.451 / 0.471** (below chance on 4 of 6 seeds) |
| AUC, 200-user table transferred | — | 0.522 / 0.528 |
| AUC, bare thin indicator | 0.604 / 0.635 | 0.563 / 0.562 |

At 2,000 users a region holds several sites; gold reporters, drawn from one region, now spread over several sites and gold
becomes **broad**, while the thin state fills with unplanted keys. The class does not hold the gold. Separately, even at
200 users it is common gold, not rare gold, that passes through the thin state — H4's mechanism sentence is wrong as written.

**VERDICT: KILLED.** The thin-state class is a property of this generator at 200 users.

## Kill 5 — H4-2's WEEK CHOICE (`f5_week.py` → `f5_week.txt`)

Re-run (filtered pool, one question per key, gate model B, not deflated): latest rule week 0 corrected; first rule week
(the replay's `rule_snapshots` policy) **58** (1 in 30.4), decoys 131, unplanted 43; `argmax_w P̂(clear|b_w)` LOSO **99**
(1 in 17.8), decoys 127, **unplanted 131**; any-week ceiling 103. Reproduced.

Seed-swap: refitting P̂ on 4 of the 5 training seeds changes the chosen week on 0–13 of ~300 keys per held-out seed and the
corrected count by at most +1 (19 19 15 16 14 16 → 99 unchanged in 28 of 30 refits). 3/3 splits: 47 vs LOSO 46, 54 vs 53.
Stable.

Stands alone? **No, not as a table.** P̂(clear|class) is exactly 0 or 1 on 41/64 classes carrying 37,264/42,912 rows
(86.8%): it is the gate's deterministic reading of b. Parameter-free rules on the same exported view: `argmax_w sumlb`
(ties → latest) **95** corrected (decoys 117, unplanted 112); lexicographic (S_cert, count_cert, count_poss, sumlb) **101**
(decoys 155, unplanted 134); the table's week equals the lexicographic week on 1,526/1,765 keys. The effect is "do not ask
at the first rule week; ask when the exported lower bounds are largest" — real in the gate model, and it costs **3× the
unplanted supports** (43 → 131) at the decoy count (127) H6's bar does not allow. ×4.5 optimistic by H4's own calibration;
not replayable here (verdicts exist only at the v4 snapshot week); never run e2e.

**VERDICT: SURVIVED the re-run and seed-swap; KILLED as a stand-alone VoI/table claim** — a parameter-free rule reproduces
96–102% of it, and the policy's cost side (unplanted ×3) was not in the claim.

## Kill 6 — BREAK-EVEN with c_g measured (`f6_breakeven.py` → `f6_breakeven.txt`, `f6_breakeven.json`)

c_g = P(gold key in H4-3's top-k clears TB_v4 at the week it is asked), from `raw/plateau/ceiling_b.json`: F = planted
facets alone (H6's definition), K = every record on the key, B = H4's gate model + in-window. p*_a = 0.275/c_g; p*_b with
λ = 1 and a_d, δ measured on the same top-k.

| k/seed | asked at | q | gold | decoy | p | c_g F / K / B | p*_a (F) | clears (a)? | p*_b (F) | clears (b)? | p*_a after ×4.5 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | S_Net3's own argmax week | 30 | 17 | 9 | 0.567 | 0.235 / 0.765 / 0.941 | **1.17** | no | 0.70 | no | 1.31 |
| 5 | P̂(clear) week | 30 | 17 | 9 | 0.567 | 0.765 / 0.882 / 0.941 | 0.36 | **yes** | 0.45 | yes* | 1.31 |
| 10 | S_Net3 week | 60 | 32 | 18 | 0.533 | 0.156 / 0.500 / 0.594 | 1.76 | no | 0.76 | no | 2.08 |
| 10 | P̂(clear) week | 60 | 32 | 18 | 0.533 | 0.812 / 0.906 / 0.969 | 0.34 | **yes** | 0.40 | yes* | 1.28 |
| 20 | S_Net3 week | 120 | 39 | 31 | 0.325 | 0.154 / 0.462 / 0.538 | 1.79 | no | 0.56 | no | 2.30 |
| 20 | P̂(clear) week | 120 | 39 | 31 | 0.325 | 0.769 / 0.897 / 0.949 | 0.36 | no | 0.28 | yes* | 1.30 |
| 30 | P̂(clear) week | 180 | 45 | 42 | 0.250 | 0.711 / 0.844 / 0.911 | 0.39 | no | 0.23 | yes* | 1.36 |

\* budget (b) "clears" the λ = 1 formula but every accepted decoy breaches the 118/118 zero-headroom bar: a_d on the top-k
decoys is 0.78–0.89, i.e. 8 accepted decoys at top-5 over six seeds.

Reading: H4-3's **own** week rule picks weeks where the planted facets do not clear (c_g(F) 0.13–0.24; the key clears only
via background records, K/B 0.5–0.9), so by H6's definition nothing clears at any k. Asked instead at the P̂(clear) week,
top-5 and top-10 clear budget (a) at gate-model face value (gain ≈ 30 × (0.567·0.765 − 0.275) ≈ +4.8 found over six seeds
at k = 5); top-20/30 fail (a). After H4's own ×4.5 deflation p*_a > 1 everywhere. Reference: filtered gold feasible under F
at some week = 81/138, H6's 0.587 reproduced.

**VERDICT: KILLED for promotion into the budget** — clears (a) only at k ≤ 10, only at the other score's week, only at
gate-model face value (≈ +1 found after deflation), and (b) is blocked by the decoy bar.

---

## Final

**H4-3: DEAD after wave 2 as the expected-utility / thin-state hypothesis**, because at the pre-registered ν = (1,1) it
fails its own per-seed kill (seeds 1 and 4 at 0.641 / 0.676), 80% of its AUC orders keys that can never yield a verdict
by site breadth, its mechanism inverts at 2,000 users (thin class 91% unplanted, LOSO AUC 0.45) and is mis-stated even at
200 (common gold, not rare, is thin), and promotion into the budget does not break even after its own ×4.5 deflation.
**One narrow use is ALIVE at replay level only:** ordering the asked detector pool by the exported class *at the snapshot
week* gives 138/240 vs the e2e-confirmed 127/128 at identical budget with 16 fewer decoys, stable under seed-swap — a
candidate for a single e2e arm, contingent on the labels-select ruling (a label-free ordering reaches 137 with rare 4 and
decoys 110). H4's "already-asked pool AUC 0.842" is look-ahead and should not be quoted.

**H4-2 week choice: DEAD as a stand-alone VoI/table claim** (P̂(clear|b) is a deterministic gate lookup; `argmax sumlb`
gets 95 of its 99, a lexicographic rule 101); the underlying effect — first-rule-week asking is stale — is ALIVE only as a
gate-model estimate (×4.5 optimistic, triples unplanted supports, never run, not replayable here).

Files: `f_common.py`, `f_gen.py`, `f1_rerun.py`, `f2_leak.py`, `f3_replay.py`, `f3b_stress.py`, `f3c_official.py`, `f4_scale.py`,
`f5_week.py`, `f6_breakeven.py`; outputs `f1_rerun.txt/.json`, `f2_leak.txt/.json`, `f3_replay.txt/.json`, `f3b_stress.txt/.json`,
`f3c_official.txt`, `f4_scale.txt`, `f5_week.txt`, `f6_breakeven.txt/.json`. Reproduce: `cd swarm_out/F-H4 && PYTHONPATH=/home/user/ng-deploy
nice -n 10 $PYTHON f1_rerun.py && ... f6_breakeven.py` (f2 ≈ 90 s for the 200-permutation null, f4 ≈ 30 s, the rest < 40 s each).

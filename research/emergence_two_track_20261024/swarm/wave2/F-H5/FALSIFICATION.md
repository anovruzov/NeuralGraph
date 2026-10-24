# F-H5 — falsification of H5-3 (shape-gated count) and its gate H5-2 (cross-site co-activation)

## Summary (10 lines)

1. **Kill 1 (scale, 2,000 users, all six dev seeds, both the rebuilt cells and the shipped detector's real `detection_inputs`): H5-2 falls from 0.945 to 0.742** on the real rule-only pool (95% bootstrap CI 0.684–0.794; P(AUC < 0.70) = 0.07), 0.716 on all candidates, 0.726 rebuilt. The pooled 0.70 line is not crossed by the letter, but the two classes that are 76% of decoys fall under it (c1 0.698, c3 0.690; combined 0.693) and so do seeds 2 and 3 (0.670, 0.680).
2. **The lever H5-3 is dead at 2,000**: gated-θ=1.25 P@20 gold per seed 0.10 0.05 0.15 0.00 0.30 0.05 (mean 0.108, decoys 0.00–0.05) on the real rule-only pool, against H6's p\* ≥ 0.47 / 0.19 and H8's 0.25 line; the gate `sync ≤ 1.25` now admits **67% of background and 39% of gold** (74% / 75% at 200). Gold-vs-other of the statistic: 0.303.
3. **The mechanism H5 named is not what kills it.** At 2,000 users only 5% of gold's co-active weeks sit inside one region; collapsing sites to regions restores 0.742 → 0.756 only, and seed 4 (still one site per region) drops 0.940 → 0.730. The erosion is record density (68k vs 13k records on the same 150 entities), so any denser world does it regardless of org shape. H5-2 is a **sparse-world artefact**, not specifically a 200-user one.
4. **Kill 2 (one formula, θ = 1.25, no re-tuning, 200 users): SURVIVED.** Real cells, full pool P@20 gold 0.300 0.400 0.450 0.400 0.300 0.505 — every seed > 0.25 under both tie-break conventions; G+D subset 0.90 0.85 0.85 0.70 0.65 0.90 > 0.55. Rebuilt cells: seed 4 sits exactly at 0.25 (5/6 strict). The untuned lexicographic variant fails (3/6): the survival is the θ gate's.
5. **Kill 3 (seed-swap and H8 §5): SURVIVED — no leak.** Label flip: s(key) unmoved; renaming every site and entity: 1,765/1,765 identical; subset-membership: identical AUC over full pool and G+D (0.8848); no class-specific missingness. The only fitted object is the scalar θ; refit on all 4-of-6 subsets it lands on 1.25 (8/15) or 1.4 (6/15): not indexed by anything, but **not as stable as H5 claimed** (my 5-of-6 refits: 1.25 ×3, 1.4 ×3).
6. **Kill 4 (feasibility at the asked week): KILLED under the gate's own evidence model F.** H5's "~75%" is the pool csv flag = feasible at *some* week of the label window (verified 240/240); that gives c_g = 0.701 on the top-20 gold and p\* = 0.392 against a measured 0.393 — zero margin. At the ranker's own best label-free week (max k-sites in its window): c_g = 0.616, **p\* = 0.447 > 0.393, fails by 0.054**; at the shipped rule layer's week (first rule week): c_g = 0.340, p\* = 0.81; at the end of the run: c_g = 0 (freshness). Only the permissive model K at the best week clears, by +0.033, inside the ±0.07 per-seed resolution. 14 of 47 top-20 gold have no F-feasible week at all.
7. **Kill 5 (best-of-many): SURVIVED for the AUC.** Bonferroni k = 45/90: 0.590/0.596; the 240/k effective-sample rule at k = 45: 0.841; empirical best-of-56 permutation null over H5's actual variant family: 95th pct 0.582, max 0.619 (2,000 draws); H5-2's 0.945 has p < 0.0005 against all. The lever's pooled P@20 0.383–0.393 clears the empirical best-of-20 θ×ranker null (95th 0.142) but not the mechanical 240/k line (0.667 at k = 45), reported both ways.
8. **Kill 6: `top20_per_seed.csv` written** (120 rows, θ = 1.25, real cells, sha256 tie-break). Scoring-side tally only: 47 gold / 4 decoy / 69 other; 33 of the 47 gold are any-week feasible. No verdict claimed.
9. Costs: `ew.generate(2000, s)` 0.1 s; the shipped pipeline + detector at 2,000 users 26–34 s per seed (pipeline 21–27 s, detection 1.6–2.3 s); the rebuilt-cells arm 0.6 s per seed. At 2,000 users the rule-only pool holds 110 gold (not 138): 117–143 keys per seed get snapshots instead of 55.
10. **H5-3: DEAD after wave 2** — at the week it would ask, the gate's evidence model gives p\* = 0.447 against its 0.393 (kill 4), and at 2,000 users its precision collapses to 0.108 because the gate lets background through (kill 1). H5-2 stands at 200 users as a clean, leak-free, selection-proof statistic (kills 2, 3, 5), and is a density artefact of a sparse world.

---

## Setup

- Target: `swarm_out/H5/HYPOTHESES.md` (H5-2: s = −mean sites holding a cell per active week inside the longest rule run widened back by the 8-week rule window; H5-3: lexicographic (1[s ≤ 1.25], n_kcells in that window, longest_run)). Pre-registered kills: `R/swarm/WAVE_1_RESULTS.md` §3, F-H5 row; thresholds `swarm_out/H8/NULL.md`; break-even `swarm_out/H6/BREAK_EVEN.md`.
- Code reused: `f_common.py` copies H5's loader, AUC and the exact `stats`/`longest_window` formula from `h5_common.py` / `test_h2_crosssite_sync.py`, generalises `test_rebuild_cells.py` to any world size, and recomputes the pack rule (`rules.json`: window 8, min_count_per_site 1, min_sites 2) as `test_h0_cells_validity.py` does. `f_1b_realcells.py` is `test_h5_realcells.py` + `tools/plateau/detect_dump.py` at any size (shipped pipeline + `detect()`, `store.detection_inputs(as_of, 'X')`), no pack override, no questions, no HQ round trip. Labels (`gold_labels`, dumps, `candidate_pool.csv`, `ceiling_b.json`) score only.
- Read-only on every repository; everything written is under `swarm_out/F-H5/`. Dev seeds 0–5 only. `nice -n 10`.
- 200-user validation of the rebuilt arm: pooled H5-2 AUC 0.953 on all keys with ≥ 7 rule weeks (H5: 0.946 rebuilt / 0.945 real on the rule-only pool); per class c1 0.925 c2 0.985 c3 0.948 c4 0.983 c5 0.971.

---

## Kill 1 — SCALE (2,000 users)

**World shape at 2,000** (`build_org`): 7 regions always; sites 9 / 11 / 13 / 11 / 7 / 13 for seeds 0–5; sites per region s0 [1,1,1,1,1,2,2], s1 [1,1,1,2,2,2,2], s2 [1,1,1,1,3,3,3], s3 [1,1,1,1,2,2,3], **s4 [1,1,1,1,1,1,1]**, s5 [1,1,1,1,3,3,3]. `WORLD_TABLE[2000]` = 9 sites (seed 0). "A region holds several sites" is true for 2–3 regions per seed and false for seed 4. Records 67–70k vs 12.4–13.1k at 200; patterns 40 real + 50 decoy as at 200.

**Real arm** (shipped detector's rule-only pool, `channels == ['rule']`, `rule_weeks ≥ 7`; `kill1_scale_2000_real.txt`):

| seed | sites/region | pool | gold / decoy / other | H5-2 AUC g/decoy | g/other | gated θ=1.25 P@20 gold / decoy |
|---|---|---|---|---|---|---|
| 0 | [1,1,1,1,1,2,2] | 894 | 13 / 52 / 829 | 0.792 | 0.319 | 0.10 / 0.05 |
| 1 | [1,1,1,2,2,2,2] | 891 | 17 / 52 / 822 | 0.770 | 0.324 | 0.05 / 0.05 |
| 2 | [1,1,1,1,3,3,3] | 894 | 16 / 55 / 823 | **0.670** | 0.326 | 0.15 / 0.05 |
| 3 | [1,1,1,1,2,2,3] | 919 | 24 / 49 / 846 | **0.680** | 0.207 | 0.00 / 0.00 |
| 4 | [1,1,1,1,1,1,1] | 907 | 22 / 47 / 838 | 0.730 | 0.346 | 0.30 / 0.00 |
| 5 | [1,1,1,1,3,3,3] | 908 | 18 / 53 / 837 | 0.849 | 0.323 | 0.05 / 0.05 |
| pooled | | 5,413 | 110 / 308 / 4,995 | **0.742** [CI 0.684, 0.794] | 0.303 | mean **0.108** / 0.033 |

Per decoy class, pooled real: **c1 0.698 (n 115), c2 0.881 (20), c3 0.690 (124), c4 0.905 (19), c5 0.905 (36)**; c1+c3 together (76% of decoys) 0.693. At 200: c1 0.921, c3 0.951. The composite's own rank AUC g/decoy: 0.693 (c4 0.398). Other statistics at 2,000: −sync_max 0.764, n_kcells 0.509, longest_run 0.540.

Rebuilt arm (all keys with ≥ 7 recomputed rule weeks, `kill1_scale_2000_rebuilt.txt`): pooled 0.726 [CI 0.687, 0.765]; per seed 0.777 0.762 0.676 0.654 0.765 0.756; c1 0.676, c2 0.828, c3 0.651, c4 0.888, c5 0.821. Real arm on all candidates incl. snapshot keys: 0.716 [0.676, 0.754], c1 0.663, c3 0.628.

**Why** (`kill1_mechanism.txt`): gold's max co-active sites in the window shift from {1: 96, 2: 123, 3: 18} at 200 to {1: 4, 2: 86, 3: 86, 4: 41, 5: 19, 6+: 4} at 2,000 (rebuilt, 240 gold). Of gold's co-active weeks at 2,000, **25 are same-region and 471 span regions (5%)**; at region granularity the AUC is 0.756 (site 0.742). Seed 4 has the 200-user org shape and still falls 0.940 → 0.730 (real). So the loss is not H5's predicted "region holds several sites" effect but background records on the same (entity, chain) key — five times as many records over the same 150 entities. Consequence for the gate: pass rate of `sync ≤ 1.25` — gold 0.391, decoy 0.094, **other 0.673** (200: 0.739 / 0.044 / 0.750). The statistic that could not see background at 200 (g/other 0.417) now ranks background above gold (0.303), and the k-count behind the gate cannot recover it with 110 gold among 5,413 keys.

**Cost**: generate 0.1 s; real path 34.1 / 29.7 / 28.8 / 26.4 / 27.1 / 29.4 s per seed (pipeline 21–27 s, detection 1.6–2.3 s); rebuilt arm 0.5–0.6 s per seed.

**VERDICT: KILLED as a lever** — P@20 0.108 at 2,000 is below every bar on record (H6 0.47 / 0.19, H8 0.25) on five of six seeds, because the gate admits background. **As the gating statistic, H5-2 is reclassified as a sparse-world artefact**: −0.20 AUC, below 0.70 on the two dominant decoy classes and on the two seeds whose regions hold three sites; the pooled value 0.742 does not itself cross the pre-registered 0.70 line (CI reaches 0.684; P(< 0.70) = 0.07), and that is reported as such.

---

## Kill 2 — ONE FORMULA (θ = 1.25 fixed, 200 users, all six seeds)

Ranker (1[sync ≤ 1.25], n_kcells, longest_run), H5's pool and cells, no re-tuning (`kill2_one_formula.txt`). Line: every seed > 0.25 (full pool) / > 0.55 (gold+decoy subset) = H8's 95th pct of max-over-6-seeds p@20 for 1,000 hashed-key rankers.

| cells | seed | 0 | 1 | 2 | 3 | 4 | 5 | seeds above line |
|---|---|---|---|---|---|---|---|---|
| real | full pool P@20 gold (20-draw avg) | 0.300 | 0.400 | 0.450 | 0.400 | 0.300 | 0.505 | **6/6** (det tie-break 6/6) |
| real | full pool decoy | 0.05 | 0.10 | 0.00 | 0.00 | 0.00 | 0.05 | |
| real | G+D subset P@20 gold | 0.90 | 0.85 | 0.85 | 0.70 | 0.65 | 0.90 | **6/6** |
| rebuilt | full pool P@20 gold | 0.350 | 0.400 | 0.450 | 0.400 | **0.250** | 0.463 | 5/6 (seed 4 = the line) |
| rebuilt | G+D subset P@20 gold | 0.90 | 0.85 | 0.85 | 0.70 | 0.65 | 0.90 | 6/6 |

Reported against the hypothesis: H5's *untuned* lexicographic (−sync, kcells, lr), which the brief's wording could also mean, fails the full-pool line on 3/6 seeds (real: 0.200 0.263 0.250 0.283 0.163 0.450). The G+D subset numbers are easy for any decoy filter (chance 0.30 and the gate removes decoys); the full-pool line is the one that binds, and the lever clears it with margin 0.05 on its two weakest seeds. Keys tied at the rank-20 score: 2–7 per seed, so the tie-break moves P@20 by at most one key.

**VERDICT: SURVIVED** (on the Boundary's real cells, 6/6 on both subsets and both tie conventions; seed 4 rebuilt sits exactly on the full-pool line).

---

## Kill 3 — SEED-SWAP and the H8 §5 checklist

`kill3_leak.txt`. The statistic reads, per key: the cell lower bound per (site, ISO week) from `detection_inputs` (`entity_type, entity_id, predicate, site, iso_week, n`) and the key's exported `rule.weeks`. Nothing under `labels.*`, no `candidate_pool.csv` column, no facet records, node ids or paths.

| §5 item | test | result |
|---|---|---|
| 1 provenance | fields used ⊂ `example_candidate_fields` (`key`, `rule`) + HQ-side cells | pass |
| 2 missingness | keys with ≥ 1 real cell: gold 138/138, decoy 317/317, other 1,310/1,310 | pass |
| 3 fitted tables | the only fitted object is one scalar θ; no per-entity / per-seed / per-chain table | pass |
| 4 label flip | gold `E-CINDER-CORE:chain_firmware` s = (1, 3, 11) before and after; decoy `E-ARBOR:chain_supply` (0, 4, 19) unchanged | pass |
| 5 seed swap | s(key) recomputed with only seed 3 present: 304/304 identical. θ refit (H5's criteria, 20-draw ties, real cells): 5-of-6 subsets → 1.25 ×3, 1.4 ×3 (both criteria); 4-of-6 subsets → 1.25 ×8 / 1.34 ×1 / 1.4 ×6 (gold@50−decoy@50), 1.25 ×5 / 1.34 ×1 / 1.4 ×9 (gold@20−decoy@20) | pass as a leak test; **θ is not sharply identified** between 1.25 and 1.4 (H5 said 1.25 in 5–6/6 folds). H5's own fixed-θ sweep puts 1.34 at 0.397/0.058, so the lever's number does not hinge on it |
| 6 subset membership | AUC of the gated rank: ranks over the full pool 0.8848 = ranks over G+D 0.8848 | pass |
| 3/rename | hash every site id and entity name, recompute: 1,765/1,765 keys identical (gate, kcells, lr, sync) | pass — nothing is indexed by name or site identity |
| 7 window words | the window is the key's own `rule_weeks` run; `found_from/to` are never read | pass |
| 8 too good | 0.945 ≥ 0.79 alarm | explained by `corpus.py pick_users` (kill 1), not by leakage |

**VERDICT: SURVIVED** — no leak; the entity-name artefact H8 warned about cannot enter a statistic that is invariant to renaming.

---

## Kill 4 — FEASIBILITY AT THE ASKED WEEK

`kill4_feasibility.txt`. First the premise: `F_TB_v4_feasible` in the pool csv equals "`ceiling_b.json` has ≥ 1 F/TB_v4-feasible week inside the label window" on 240/240 gold — H5's "~75% feasible" is any-week feasibility, which needs hindsight. H5-3 never named the week it asks at, so every label-free pick is scored on the real-cell θ = 1.25 top-20 (47.1 gold over six seeds, 20-draw average; P@20 0.393):

| asked week | c_g on top-20 gold, model **F**/TB_v4 (per seed) | p\* = 0.275/c_g | vs 0.393 | model K/TB_v4: c_g → p\* |
|---|---|---|---|---|
| A — week in the ranker's window with most k-sites (its own best label-free pick) | **0.616** (0.67 0.88 0.78 0.75 0.17 0.40) | **0.447** | **fails −0.054** | 0.764 → 0.360 (clears +0.033) |
| B — last week of the longest run | 0.000 | ∞ | fails | 0.000 → ∞ |
| C — first week of the longest run | 0.340 (0.00 0.50 0.44 0.38 0.17 0.40) | 0.810 | fails −0.417 | 0.616 → 0.447 (fails) |
| D — first rule week overall (the shipped rule layer's week; H6's c_g^ship basis) | 0.340 | 0.810 | fails −0.417 | 0.616 → 0.447 (fails) |
| E — any week inside the ranker's window (hindsight) | 0.701 | 0.393 | fails −0.000 | 0.873 → 0.315 (clears) |
| F — H5's flag: any week in the label window (hindsight) | 0.701 | **0.392** | **margin 0.000** | 0.701 → 0.392 |

Over all 138 pool gold the same picks give c_g = 0.413 (A), 0.196 (C/D), 0.587 (E/F) under F. The ranker's pick A lies inside the label window for 134/138 gold, so the week is right; the evidence at that week is not: 14 of the 47 deterministic top-20 gold have **no** F/TB_v4-feasible week anywhere, and of the rest 29 are feasible exactly at A, 3 within two weeks, 1 nine weeks off. B is never feasible because a rule run outlasts its records by the 8-week window and the gate's 42-day freshness has lapsed.

Recomputed break-even: H6's p\* = 0.275 / c_g with c_g measured at the asked week is **0.447 (model F, best pick) to 0.81 (shipped pick)**, not the 0.37 WAVE_1 §2 carried from "c_g ≈ 0.75". H5-3's measured P@20 of 0.393 clears none of them under the gate's own evidence model, and clears the permissive model K at the best pick by +0.033, below the ±0.07 per-seed resolution H8 states.

**VERDICT: KILLED** under F/TB_v4 at every label-free week; survives only under K at one week, inside noise.

---

## Kill 5 — BEST-OF-MANY

`kill5_bestofmany.txt`. H5 computed ~45 variants (15 dump statistics, 13 cell statistics × 2 scopes, 4 composites, 10 θ × 2 rankers, 3 LOSO criteria) over 138 gold / 317 decoy.

Thresholds for the one pre-registered statistic to stand at p < 0.05:

| discount | AUC needed | H5-2 (0.945 real / 0.946 rebuilt) |
|---|---|---|
| none (Mann-Whitney sd 0.0295) | 0.548 | clears |
| Bonferroni k = 45 / 90 (sign flips free) / 200 | 0.590 / 0.596 / 0.603 | clears |
| H8 §4 "effective sample 240/k", k = 26 / 45 / 90 | 0.754 / **0.841** / 1.003 | clears at 45; nothing can at 90 |
| **empirical**: max over H5's actual 56 variants (28 statistics × 2 signs, `features_h2.json`) under 2,000 within-seed label permutations | 95th **0.582**, 99th 0.598, max 0.619 | clears; p < 0.0005 (0/2,000 ≥ 0.945; single-statistic null 95th 0.552, max 0.618) |

The lever (pooled P@20 on the full pool, 47/120 = 0.393 real, 0.383 rebuilt deterministic): the 240/k hypergeometric line at k = 45 is ≥ 2 of 3 effective slots, i.e. **0.667 — not met**; the empirical best-of-20 (10 θ × 2 rankers) permutation null has 95th pct 0.142, max 0.183 (400 draws) — met with margin 0.24. H8 §2 says to use the empirical percentiles over 240/k because variants are correlated; by that rule the lever stands; by the mechanical rule it does not.

**VERDICT: SURVIVED** (the AUC survives every discount; the lever survives the empirical one and fails the mechanical one).

---

## Kill 6 — HAND-OFF

`top20_per_seed.csv`: 120 rows, columns `seed, rank, key, score` with score = `gate|kcells|lr|sync|win|w_maxk`; ranker (1[sync ≤ 1.25], kcells, lr) on H5's real cells, ties by sha256(`e2e-ingest-v1`|key) as `ordered_candidates` does. No labels in the file. Scoring-side tally for the replay's expectation only: gold 6 8 9 8 6 10 (47), decoy 1 2 0 0 0 1 (4), other 69; any-week F/TB_v4-feasible gold 4 7 8 7 1 6 (33), feasible at the ranker's own week (A, model F) 29. No recovered pattern is claimed here; `w_maxk` is the week the ranker would ask at if it is allowed to choose.

---

## Files

`f_common.py` (shared; provenance in its docstring) · `f_1_scale.py` → `kill1_scale_200_rebuilt.txt`, `kill1_scale_2000_rebuilt.txt`, `scale_{200,2000}_features.json`, `cells-{200,2000}-seed-N.json` · `f_1b_realcells.py` → `realcells-2000-seed-N.json`, `realcells-2000-seed-0.log`, `realcells-2000-all.log` · `f_1c_scale_real.py` → `kill1_scale_2000_real.txt`, `scale_2000_real_features.json` · `f_1d_mechanism.py` → `kill1_mechanism.txt` · `kill1_bootstrap.txt` · `f_2_formula.py` → `kill2_one_formula.txt`, `kill2_summary.json` · `f_3_leak.py` → `kill3_leak.txt` · `f_4_feasibility.py` → `kill4_feasibility.txt`, `kill4_summary.json` · `f_5_bestofmany.py` → `kill5_bestofmany.txt` · `f_6_handoff.py` → `kill6_handoff.txt`, `top20_per_seed.csv` · `timing_generate.txt` · `START`, `END`.
Run: `PYTHONPATH=/home/user/ng-deploy nice -n 10 $PYTHON f_<n>_*.py` (f_1_scale takes `SIZE seeds`; f_1b takes `SIZE SEED OUT_JSON`; f_1c takes `SIZE seeds`).

---

**H5-3: DEAD after wave 2, because at the week it would ask the gate's own evidence model gives c_g = 0.616 and p\* = 0.447 against its measured P@20 of 0.393 (0.81 at the shipped week), and at 2,000 users its precision collapses to 0.108 as the co-activation gate admits 67% of background — H5-2 remains a clean, leak-free, selection-proof statistic at 200 users (0.945) that is a density artefact of a sparse world (0.742 at 2,000, below 0.70 on the two dominant decoy classes).**

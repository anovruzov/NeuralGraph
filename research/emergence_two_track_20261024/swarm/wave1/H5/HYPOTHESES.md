# H5 — TEMPORAL STRUCTURE / CROSS-SITE TIMING — hypotheses, tests run, verdicts

## Summary (10 lines)

1. Pool: rule-only keys with `rule_weeks >= 7`, dev seeds 0–5: **1,765 keys = 138 gold / 317 decoy / 1,310 other**. Decoys by class: c1 122, c2 21, c3 132, c4 19, c5 28 (5 keys sit in two decoys and count in both).
2. **Shape of the rule-week set (dumps only) is a partial result and dead as a ranker.** `longest_run` lifts gold-vs-decoy AUC from 0.611 (count) to 0.668 and separates only classes 1/3 (0.75/0.80); classes 2/4/5 persist *longer* than gold (0.35/0.32/0.08). Gap variance, run count, density, first/last week are all ≈0.50.
3. **Same-week cross-site co-activation separates gold from every decoy class.** `s = −(mean number of sites holding a non-empty cell per active week)` inside the longest rule run: AUC gold-vs-decoy **0.945 on the real Boundary cells** (c1 0.921, c2 0.989, c3 0.951, c4 0.979, c5 0.979); per-seed 0.90–0.97; permutation null 0.503 ± 0.030 (max of 2,000 = 0.601; best-of-26 null max 0.615).
4. It is **blind to background**: gold-vs-other AUC 0.417. Alone it finds nothing (P@20 gold 0.071) — it is a decoy *filter*, not a gold finder.
5. The **count inside the window** (`n_kcells`, site-weeks at ≥k) is the mirror image: gold-vs-other 0.69, gold-vs-decoy 0.49 (class 4: **0.006**), P@20 gold 0.338 with **decoy 0.648**.
6. **Composite (shape gate, then count):** untuned lexicographic `(−co-activation, n_kcells, longest_run)` → P@20 gold **0.264 / decoy 0.000** (per seed 0.16–0.40, decoy 0 on all six), P@50 0.161 / 0.009; baseline `rule_weeks` count P@20 0.080 / 0.190, P@50 0.111 / 0.239; the ≥11 cut 0.135 / 0.213.
7. Gated variant with a tuned threshold θ: **leave-one-seed-out** (θ chosen on 5 seeds by gold@50−decoy@50): held-out P@20 gold **0.25–0.50 (mean 0.394), decoy 0.00–0.10 (mean 0.050)**; P@50 gold 0.20–0.27 (mean 0.247), decoy 0.00–0.06. θ = 1.25 in 5/6 folds. Same ranker on the real cells at θ=1.25: P@20 gold 0.30–0.51 (0.393), decoy 0.033.
8. ~75% of the gold reached at the top is F/TB_v4-feasible (untuned: 4.1 of 5.3 gold in the top 20 per seed; gated: 6.1 of 7.9). Of the 138 pool gold, 81 are feasible, 92 rare, 53 have any k-cell, 48 are feasible with a k-cell — that is the reachable set.
9. **Against interest:** (a) the prior (IMG_3574: gold synchronous, decoy not) is *reversed* — gold facets arrive one site per week, staggered; decoys light 2–4 sites in the same week; (b) the signal is a construction property of the benchmark generator (`pick_users` draws a gold facet's reporters from one region and a decoy facet's across all its regions; at 200 users a region is one site), so at 2K/10K users where regions hold several sites it should weaken — **untested**; (c) the brief's (b) first-disclosure spread (AUC 0.69, gold *wider*) and (c) k-transition alignment (0.56; only c2/c4/c5) are weak on their own.
10. Verdicts: **H5-1 DEAD** (partial, c1/c3 only, raises decoy share). **H5-2 PROMOTED** as a decoy filter (0.945, all five classes, untuned). **H5-3 PROMOTED** as the ranker to carry into wave 2/3, with the LOSO spread above and the world-size caveat as its stated kill.

---

## Data, pool, validity of the cells

- Dumps `scratchpad/dumps/seed-{0..5}.json` give `rule_weeks` (list), `candidate_weeks`, `snapshot_week`, `supporting_sites`, `channels`. Pool = `channels == ['rule']` and `len(rule_weeks) >= 7` (wave-0 filter). Classes from `raw/plateau/candidate_pool.csv` (scoring only); decoy class from the dump's `labels.decoys[].class` (scoring only); `rare` / `F_TB_v4_feasible` (scoring only).
- Per-site per-week cells were obtained two ways. (i) **Rebuilt** as `ceiling_b.py` does (model K: records whose anchor or aux is the key's entity and whose predicate maps to the key's chain, polarity ≥ 0), 0.2 s/seed — `test_rebuild_cells.py`. (ii) **Real**: the shipped pipeline run exactly as `tools/plateau/detect_dump.py` runs it (no pack override was used for the recorded dumps), reading `store.detection_inputs(as_of, 'X')` — the cells the detector itself sees — 5 s/seed, `test_h5_realcells.py`. Offline; no questions, no e2e.
- Agreement: cell presence per (site, week) over pool keys **Jaccard 0.959**; real ⊇ rebuilt (434 real-only cells, 0 rebuilt-only) — the lexical extractor finds slightly more than model K. Recomputing the rule from the rebuilt cells reproduces the dumps' `rule_weeks` exactly for 90.6% of all 3,178 keys (mean Jaccard 0.952). Every headline number below is given on the **real** cells where it differs materially; the rebuilt numbers are within 0.01 AUC.
- Exported reading: a cell is absent, `'<k'` (lower bound 1) or an int ≥ k (k = 3). "Active" = cell present. "k-cell" = n ≥ 3. Nothing below reads a count that the Boundary would not export. The rules channel already reads these cells (`detectors.py:_Index.window_cells`), so none of this needs a new field to cross the Boundary.
- Window scope: statistics are computed inside the longest run of rule weeks widened backwards by the 8-week rule window (the activity that fired the rule); the all-26-weeks scope gives the same picture (reported as a variant).

---

## H5-1 — inter-arrival shape of the rule-fire weeks (dumps only)

```
H5-1
  STATISTIC   s(key) = longest run of consecutive ISO weeks in rule_weeks
              (variants: number of runs, count − longest run, gap variance, span, density, first/last week)
  READS       rule hit weeks (dump field rule_weeks), week index
  FORBIDDEN   touches none of: gold labels, facet_records, Placement internals, node ids, file paths, raw records,
              value equality across sites. Confirmed: the statistic is a function of rule_weeks alone.
  CLAIM       s separates gold from decoy in the filtered pool with AUC >= 0.70 on every decoy class
  KILL        any decoy class with AUC < 0.5 (decoys of that class persist longer than gold), or a decoy share at
              top-20 no lower than the count baseline's 0.19
  COST        O(|rule_weeks|) per key; dumps only, no rebuild (free)
  TEST        rows = load_pool()                                  # h5_common.py: filtered pool + scoring labels
              s = {(r['seed'], r['key']): max(runs(r['rule_weeks'])) for r in rows}
              report_auc(rows, s, 'longest_run'); report_prec(rows, s, 'longest_run')
              (full script: test_h1_ruleweeks_shape.py → result_h1.txt)
```

**Run.** Filtered pool, six seeds.

| statistic | AUC g/decoy | c1 | c2 | c3 | c4 | c5 | g/other | P@20 gold / decoy | P@50 gold / decoy |
|---|---|---|---|---|---|---|---|---|---|
| `count` (baseline) | 0.611 | 0.652 | 0.380 | 0.735 | 0.373 | 0.102 | 0.703 | 0.080 / 0.190 | 0.111 / 0.239 |
| **`longest_run`** | **0.668** | 0.753 | 0.345 | 0.797 | 0.321 | **0.078** | 0.785 | 0.122 / **0.299** | 0.156 / 0.257 |
| `span` | 0.593 | 0.608 | 0.423 | 0.699 | 0.429 | 0.201 | 0.641 | 0.058 / 0.187 | 0.085 / 0.168 |
| `n_runs`, `count−longest`, `gap_var`, `density`, `longest_frac`, `first`, `last` | 0.49–0.54 | | | | | | | | |

Per-seed P@20 gold for `longest_run`: 0.15 0.15 0.05 0.05 0.15 0.20. Medians: gold longest run 11, c1 8, c3 8, c2 13, c4 14, c5 19, other 8. Mechanism: classes 1 and 3 put a single facet's records on the key (mixed-chain or near-miss-entity facets land on other keys), so one burst fires the 8-week rule for exactly 8 weeks; gold has 3–5 bursts over 2–7 weeks. Classes 2, 4, 5 put all facets on the key and persist as long as or longer than gold (class 5's compressed stale chain fires for ~19 weeks).

**Partial result, stated:** separates classes 1 and 3 only; cannot separate 2, 4, 5 (it ranks them *above* gold). As a negative filter `longest_run >= 9` keeps 114/138 gold, 132/317 decoy, 384/1,310 other (18.1% gold, 21.0% decoy over 630 keys) — a better gold rate than the ≥11 cut (13.5%, 736 keys) at a higher recall, but the same decoy share.

**Verdict: DEAD (kill fired).** c2/c4/c5 AUC < 0.5 and the decoy share at top-20 rises from 0.19 to 0.30. Keep `longest_run >= 9` only as a free pool shrink with 17% gold cost; do not rank by it.

---

## H5-2 — same-week cross-site co-activation (the "conduction delay" test, reversed)

```
H5-2
  STATISTIC   s(key) = − mean over active weeks w of |{ sites with a non-empty cell for the key at w }|,
              weeks restricted to the longest rule run widened by the 8-week rule window
              ("active week" = at least one site has a cell; a '<k' cell counts as present)
  READS       per-site weekly cell presence / lower bound ('<k' or the count), rule hit weeks, week index
  FORBIDDEN   touches none of: gold labels, facet_records, Placement internals, node ids, file paths, raw records,
              value equality across sites. Confirmed: inputs are cell presence per (site, week) and rule_weeks.
  CLAIM       s separates gold from decoy with AUC >= 0.85 overall and >= 0.80 on every one of the five classes
  KILL        any class with AUC < 0.6; or gold's distribution not concentrated at one site per active week; or the
              statistic failing to reproduce on the real Boundary cells (it was first computed on rebuilt cells)
  COST        O(sites × weeks) per key; needs the cells (rebuild 0.2 s/seed, or the real store 5 s/seed)
  TEST        rows = load_pool(); F = json.load(open(OUT+'/features_h2.json'))       # from test_h2_crosssite_sync.py
              s = {k: -v['mean_sites_per_week_win'] for k, v in F.items()}
              report_auc(rows, s, 'neg mean_sites_per_week_win')
              (full: test_h2_crosssite_sync.py, test_h5_realcells.py + test_h5b_realcells_compare.py, test_h4_null.py)
```

**Run.** Real Boundary cells, filtered pool, six seeds.

| statistic (sign: higher = more gold-like) | AUC g/decoy | c1 (122) | c2 (21) | c3 (132) | c4 (19) | c5 (28) | g/other |
|---|---|---|---|---|---|---|---|
| **−mean sites per active week** (real cells) | **0.945** | 0.921 | 0.989 | 0.951 | 0.979 | 0.979 | 0.417 |
| same, rebuilt cells | 0.946 | 0.924 | 0.989 | 0.953 | 0.979 | 0.965 | 0.419 |
| same, all 26 weeks (rebuilt) | 0.952 | 0.944 | 0.989 | 0.947 | 0.984 | 0.965 | 0.412 |
| exclusive-week fraction (weeks with exactly one active site / active weeks) | 0.911 | 0.879 | 0.980 | 0.913 | 0.972 | 0.952 | 0.427 |
| −max sites active in one week (real) | 0.884 | 0.869 | 0.915 | 0.893 | 0.912 | 0.879 | 0.374 |
| −number of weeks with ≥2 active sites | 0.729 | 0.671 | 0.972 | 0.668 | 0.975 | 0.961 | 0.374 |
| brief's (c): −max sites reaching k in the same week | 0.558 | 0.446 | 0.842 | 0.489 | 0.979 | 0.938 | 0.313 |
| brief's (b): range of first-disclosure week across active sites | 0.693 | 0.682 | 0.670 | 0.740 | 0.666 | 0.535 | 0.596 |

Per-seed AUC (rebuilt): 0.963 0.965 0.970 0.934 0.904 0.933. Distribution of max co-active sites in the window: gold 1:60 2:64 3:11 4:2 5:1 (n=138); decoy 1:6 2:66 3:134 4:105 5:6 (n=317); other 1:856 2:426 3:27 4:1.

Null (test_h4_null.py): within-seed label permutation, 2,000 draws: mean 0.503, sd 0.030, max 0.601, 99.9th pct 0.595. Best-of-26 two-sided null (the number of statistic×scope variants I tried for this family): 99th pct 0.605, max 0.615. The observed 0.945 is not a selection artefact; its sign was fixed from the generator's mechanics before scoring.

**Why it works, and the honest caveat.** In `research/mycelic/corpus.py` a real pattern's facet *j* draws its k reporters from one region (`pick_users(k, [regs[j % len(regs)]])`) while every decoy class draws each facet's reporters cycling across all its 2–4 regions (`pick_users(k, regs)`). At 200 users a region holds one site (seed 2: one region holds two). So a gold facet is one site-week; a decoy facet is 2–4 site-weeks in the same week. That is the entire signal, and it is a property of how this benchmark builds its decoys, not a law about decoys. Two consequences cut against the hypothesis as framed: the IMG_3574 prior (gold synchronous) is backwards — gold is *staggered* across sites and decoys are synchronous; and at 2K/10K users regions hold several sites, so gold facets will co-activate sites within a week and the separation should shrink — not measured here (dev seeds are 200 users; sealed seeds untouched). (b) first-disclosure spread is weak and runs the other way from "concentration": gold's first weeks are spread over 2–8 weeks, decoys' coincide. (c) k-transition alignment separates only the classes whose bursts are large enough to reach k at several sites (2, 4, 5).

**Verdict: PROMOTED — as a decoy filter.** AUC 0.945 on real cells, every class ≥ 0.92, untuned, survives the null and the real-cell reproduction. It does **not** find gold against background (0.417) and on its own has P@20 gold 0.071 — it must be paired with a count (H5-3). Wave-2 kill to run: recompute at 2,000 users where region ≠ site.

---

## H5-3 — shape-gated count: synchrony first, then the count inside the window

```
H5-3
  STATISTIC   s(key) = lexicographic ( −co-activation(H5-2),  n_kcells,  longest_run(H5-1) )
              where n_kcells = number of (site, week) cells with count >= k inside the H5-2 window.
              Tuned variant: ( 1[co-activation <= θ],  n_kcells,  longest_run ), θ chosen leave-one-seed-out.
  READS       per-site weekly cell lower bounds ('<k' or the count), rule hit weeks, week index
  FORBIDDEN   touches none of: gold labels, facet_records, Placement internals, node ids, file paths, raw records,
              value equality across sites. Confirmed: inputs are the H5-1 and H5-2 inputs only.
  CLAIM       top-20 per seed has gold precision >= 0.25 with decoy share <= 0.05 on every seed, i.e. the shape
              gate beats the count inside the window (P@20 gold 0.338 / decoy 0.648) on the decoy column at no
              gold cost, and beats the rule_weeks baseline (0.080 / 0.190) on both columns
  KILL        any seed with decoy share at top-20 > 0.10, or held-out (LOSO) P@20 gold below the count-inside-
              window's 0.338 while admitting decoys, or the tuned θ not stable across folds
  COST        O(sites × weeks) per key; needs the cells (as H5-2)
  TEST        rows = load_pool(); F = json.load(open(OUT+'/features_h2.json'))
              lex = lambda r: (-F[k(r)]['mean_sites_per_week_win'], F[k(r)]['n_kcells_win'], max(runs(r['rule_weeks'])))
              for s in SEEDS: print(s, topn([r for r in rows if r['seed']==s], lex, 20))   # gold, decoy, feasible gold
              (full: test_h3_composite.py → result_h3.txt; test_h3b_loso.py → result_h3b_loso.txt;
               real cells: test_h5b_realcells_compare.py → result_h5_realcells.txt)
```

**Run — untuned lexicographic ranker (no parameter; the per-seed numbers are the spread).**

| cells | P@20 gold per seed (0..5) | mean | decoy | P@50 gold per seed | mean | decoy | feasible gold in top-20 |
|---|---|---|---|---|---|---|---|
| real | 0.20 0.26 0.25 0.28 0.16 0.45 | **0.267** | **0.000** on all six | 0.17 0.21 0.16 0.13 0.09 0.18 | 0.157 | 0.010 | 2.0 4.3 5.0 5.0 0.2 7.0 |
| rebuilt | 0.22 0.24 0.30 0.27 0.16 0.40 | 0.264 | 0.000 | 0.18 0.21 0.17 0.13 0.09 0.18 | 0.161 | 0.009 | mean 4.1 of 5.3 gold |

AUC (rebuilt): g/decoy 0.952 (c1 0.934, c2 0.989, c3 0.958, c4 0.976, c5 0.963), g/other 0.525.

**Run — baselines and the count inside the window, same pool, same tie-break (20 random draws).**

| ranker | AUC g/decoy | g/other | P@20 gold / decoy | P@50 gold / decoy |
|---|---|---|---|---|
| `rule_weeks` count | 0.611 | 0.703 | 0.080 / 0.190 | 0.111 / 0.239 |
| `rule_weeks >= 11` (wave-0 cut, per-seed precision) | — | — | 0.100–0.174 gold / 0.201–0.235 decoy | |
| `longest_run` (H5-1) | 0.668 | 0.785 | 0.122 / 0.299 | 0.156 / 0.257 |
| **count inside the window** `n_kcells` (ties → longest_run) | 0.592 (c4 0.004, c5 0.080) | 0.852 | **0.338 / 0.648** | 0.220 / 0.469 |
| gate-like `max k-sites in a 6-week window` | 0.502 | 0.689 | 0.313 / 0.645 | 0.194 / 0.456 |
| ratio `n_kcells / co-activation` (untuned) | 0.676 | 0.817 | 0.367 / 0.592 | 0.213 / 0.453 |
| product `n_kcells × exclusive-week fraction` (untuned) | 0.748 | 0.802 | 0.400 / 0.525 | 0.227 / 0.333 |
| **lexicographic (−co-activation, n_kcells, longest_run)** | **0.952** | 0.525 | **0.264 / 0.000** | 0.161 / 0.009 |

**Run — tuned gate, leave-one-seed-out** (θ ∈ {1.0, 1.1, 1.2, 1.25, 1.34, 1.4, 1.5, 1.67, 2.0, ∞}; chosen on the five training seeds, scored on the sixth; rebuilt cells).

| selection criterion on training seeds | θ per fold | held-out P@20 gold (range, mean) | held-out P@20 decoy | held-out P@50 gold | held-out P@50 decoy |
|---|---|---|---|---|---|
| gold@50 − decoy@50 | 1.25 ×5, 1.4 ×1 | **0.25–0.50 (0.394)** | 0.00–0.10 (0.050) | 0.20–0.27 (0.247) | 0.00–0.06 (0.026) |
| gold@20 − decoy@20 | 1.25 ×6 | 0.25–0.46 (0.385) | 0.00–0.10 (0.033) | 0.20–0.27 (0.250) | 0.00–0.06 (0.020) |
| gold@50 only (decoy-blind, reported against interest) | 1.5 ×3, 1.4 ×3 | 0.21–0.50 (0.379) | **0.00–0.35 (0.167)** | 0.21–0.27 (0.246) | 0.00–0.14 (0.067) |

Same gated ranker (θ = 1.25) on the **real** cells: P@20 gold 0.30 0.40 0.45 0.40 0.30 0.51 (mean 0.393), decoy 0.05 0.10 0.00 0.00 0.00 0.05 (0.033), feasible gold 4.0 7.0 8.0 7.0 1.0 6.0; P@50 gold 0.26 0.25 0.27 0.24 0.22 0.29 (0.255), decoy 0.022. Fixed-θ sweep (all seeds, reference only): θ ≤ 1.1 reproduces the untuned ranker; θ = 1.34 gives 0.397 / 0.058; θ ≥ 1.5 lets decoys back in (0.208 at 1.5, 0.525 at 2.0).

Gated longest-run only (no k count): P@20 gold 0.17, so the count is what finds gold; the gate is what removes decoys. Reference cut, per seed: `rule_weeks >= 11` AND co-activation ≤ 1.34 keeps 78–96 keys with gold 0.10–0.23 and decoy **0.013–0.061** (vs 0.20–0.24 without the gate).

**What the top of the ranking contains.** Untuned top-20 per seed: ~5.3 gold (4.1 F/TB_v4-feasible, 2.3 rare), 0 decoy, ~14.7 background. Of the 138 pool gold, 81 are feasible, 92 rare, 53 have any k-cell, 48 are feasible with a k-cell — the ceiling of what a k-count can reach. Seed 4 is the weak seed throughout (P@20 0.16, feasible gold 0.2 untuned / 1.0 gated): its gold with k-cells is scarce, not its decoys.

**Verdict: PROMOTED.** Kill did not fire: decoy share at top-20 is 0.00 on all six seeds untuned (≤ 0.10 held-out tuned); held-out gold precision 0.25–0.50 exceeds the count-inside-window's 0.338 on 4 of 6 folds and never admits its 0.65 decoys; θ = 1.25 on 5–6 of 6 folds. The brief's question — does a statistic over the window's shape beat the count inside it at separating gold from decoy — has the answer: **the count cannot see decoys (0.49, class 4 at 0.006) and the shape cannot see background (0.42); gated together they take the decoy share from 0.65 to 0.00–0.05 at equal or better gold share.** Break-even is agent 6's number; the inputs for it are here: 20 questions on the untuned top-20 reach ~5.3 gold (≈4 feasible) vs 1.6 for random draws from the filtered pool (7.8%) and 2.7 for the ≥11 cut with 4.3 decoys.

---

## Variants tried (for the best-of-many accounting)

- H5-1 (dumps): 15 statistics of `rule_weeks` (count, longest run, n runs, count−longest, longest fraction, span, density, gap variance ±, big-gap count, first ±, last). 1 beat count on AUC; none beat it on decoy share.
- H5-2 (cells): 13 statistics × 2 scopes (all weeks / longest-run window) = 26 variants, sign fixed a priori. Reported: mean sites per active week, exclusive-week fraction, max co-active sites, weeks with ≥2 sites, k-sites same week, first-disclosure range and std, n k-cells, max k-sites in 6 weeks, active weeks/cells/sites, max sites in 6 weeks.
- H5-3: 4 untuned composites (ratio, product, two lexicographic orders), 10 θ values × 2 rankers (with/without k count), 3 LOSO criteria. The decoy-blind criterion is reported because it is the one that fails.
- Effective-sample note per §4.3: 138 pool gold / ~45 variants ≈ 3 per variant; the permutation and best-of-26 nulls (max 0.615) are what make the 0.945 stand regardless.

## Files

All under `/tmp/claude-0/-home-user-NeuralGraph/2bf29258-e412-50e5-8873-b0acbc147d53/scratchpad/swarm_out/H5/`:
`h5_common.py` (loader, AUC, precision), `test_rebuild_cells.py` → `cells-seed-N.json`, `test_h0_cells_validity.py` → `result_h0_validity.txt`, `test_h1_ruleweeks_shape.py` → `result_h1.txt`, `test_h2_crosssite_sync.py` → `result_h2.txt` + `features_h2.json`, `test_h3_composite.py` → `result_h3.txt`, `test_h3b_loso.py` → `result_h3b_loso.txt`, `test_h4_null.py` → `result_h4_null.txt`, `test_h5_realcells.py` → `cells-real-seed-N.json`, `test_h5b_realcells_compare.py` → `result_h5_realcells.txt`.

If the swarm ends in sentence A, the first function to change is `/home/user/ng-deploy/mycelic/collective/detect/detectors.py:run_detection` (the step that merges rule hits into candidates): a per-key "sites co-active per active week" feature is computable from the cells `_Index.window_cells` already indexes, so nothing new crosses the Boundary. Read-only here; no code was changed.

# H1 — SPECTRAL / LOW-RANK — HYPOTHESES (wave 1)

## Summary (10 lines)

1. All three pre-registered spectral statistics are **DEAD** on the filtered pool (rule_only, rule_weeks >= 7: 1,765 keys, 138 gold, 317 decoy, 1,310 other).
2. S1 rank-r reconstruction residual: pooled AUC gold-vs-decoy 0.43–0.51 across every r; LOSO held-out spread **[0.362, 0.546]**. Dead.
3. S2 rank-r leverage score: pooled AUC gold-vs-decoy 0.44–0.54 across every r; LOSO held-out spread **[0.402, 0.578]**. Dead.
4. S3 rank-1 energy of the key's own site×week submatrix, claimed gold HIGHER: pooled AUC **0.328** — the claimed direction is reversed; top-20 by S3 is 65% decoys (79/120) and 1.7% gold. Dead as claimed.
5. The mechanism is wrong in sign for this generator: a gold facet draws its users from ONE region, a decoy facet from ALL its regions (`research/mycelic/corpus.py`), so the **decoy** is the rank-1 object. Site = region at 200 users (7 sites), so it shows at site granularity.
6. Reversed S3 (post hoc, k=2) reaches pooled AUC gold-vs-decoy 0.672, per-seed [0.641, 0.697, 0.782, 0.669, 0.728, **0.540**] — fails the pre-registered any-fold floor of 0.55 on seed 5, and ranks the 1,310 'other' keys ABOVE gold (gold-vs-all 0.415), so it is useless as a positive ranker.
7. Reversed S3 is a weak proxy for the plain burst count (Spearman 0.44); burst count alone scores 0.691 > 0.672. The temporal family (agent 5) should own that statistic; nothing spectral adds to it.
8. Reference, not mine: rule_weeks alone gives AUC gold-vs-decoy 0.611 on the same pool; no spectral statistic in its claimed direction beats it.
9. The only clean by-product: a key whose exported submatrix is **exactly** rank 1 (s3 > 0.999) is never gold — 0/138 on every seed — and is a decoy 33/70 of the time. It is a one-burst key. Post hoc (k=2), cannot be re-validated on dev seeds; handed to the falsifier and family 5, not promoted.
10. Verdict: **DEAD** — no low-rank statistic over exported buckets separates gold from decoy in the pre-registered direction; the low-rank signature belongs to the decoys.

Cost: free after a 0.3 s/seed vectorised rebuild (the ~90 s in `ceiling_b.py` is its per-week Python loop). No labels in any statistic. Pre-registration of statistics, directions, grid and kills: `PREREG.md` (15:53:49Z, before `results.json` 15:55Z).

---

## Pool, contract, and what was fixed before the run

- **Matrix.** `test_build_matrix.py` rebuilds dev worlds 0–5 with `ew.generate(200, seed)` and, per pool key, counts the key's records per site per week exactly as `ceiling_b.py` model K reads them (record anchor OR aux is the key entity, predicate maps to the key's chain, polarity < 0 excluded). Per (site, week w): the 6-week window [w-5, w] count, bucketed at k = 3 — 0 → 0, 1..2 → '<k' → imputed 1 (the detector's evidence-side lower bound), ≥ 3 → the count. M_seed ∈ R^(keys × 7·26) (seed 2 has 8 sites), rows = every key of the seed in `candidate_pool.csv`, label-free.
- **Fidelity check.** From the same counts, the rule channel (≥ 2 sites with 8-week lower-bound count ≥ 1) reproduces the dumps' `rule_weeks` with Jaccard mean 0.937–0.958, median 1.000 on every seed; 15–26 keys per seed fall below 0.5 (closed_through truncation and the aux rows model K adds).
- **Evaluation pool.** `detector_state == rule_only AND rule_weeks >= 7` (wave 0's filter): per seed n = 308 / 304 / 265 / 304 / 293 / 291; gold 25 / 27 / 21 / 21 / 20 / 24; decoy 58 / 62 / 50 / 51 / 50 / 46.
- **k.** Three statistics, pre-registered with direction. The r grid {1,2,3,4,6,8,12,16,24,32,48} is chosen by leave-one-seed-out, so the "best r" rows below are k = 11 best-of-many and only the LOSO rows are k = 1. Any sign-fitted or reversed number is k = 2 and is labelled so.
- **Kill rule (pre-registered).** Dead if held-out AUC gold-vs-decoy ≤ 0.55 on any of the six folds, or the claimed direction is reversed on the pooled set, or top-20 precision does not lift the gold share among planted hits above the pool's 30% (138:317).
- **Reference, not a hypothesis.** `rule_weeks` from the CSV on the same pool: AUC gold-vs-decoy 0.611 (per seed 0.672, 0.668, 0.550, 0.494, 0.671, 0.583), gold-vs-all 0.685, p@20 (gold, decoy) [(3,5),(4,5),(1,4),(1,3),(2,3),(0,3)], p@50 [(7,11),(7,13),(2,12),(2,14),(9,11),(6,11)].

---

## H-S1 — reconstruction residual under a rank-r truncated SVD

```
H-S1
  STATISTIC   s1(key) = || m_key - P_r m_key ||^2 / || m_key ||^2 ,  m_key the key's row of M_seed (L2-normalised),
              P_r the projector onto the top-r right singular vectors of the row-normalised M_seed.
  READS       per-site per-week 6-week-window record count bucketed at k=3 ('<k' -> 1, else the count), site index,
              week index; nothing else.
  FORBIDDEN   touches none of: gold/decoy labels, facet_records, Placement internals, node ids, file paths, raw
              records, value equality across sites. Labels enter only the AUC / precision lines.
  CLAIM       a planted pattern has a shared latent cause so its row lies in the low-rank subspace spanned by the
              pool: gold has LOWER residual than decoy; AUC gold-vs-decoy >= 0.65 on the filtered pool.
  KILL        held-out AUC gold-vs-decoy <= 0.55 on any LOSO fold, or pooled AUC < 0.5 (direction reversed).
  COST        O(sites*weeks) per key after one SVD of a ~540 x 182 matrix per seed; world rebuild 0.3 s/seed.
  TEST        (core; full script test_spectral.py)
              M = W6.reshape(n,-1); M /= norm(M,axis=1)[:,None]; U,S,Vt = svd(M, full_matrices=False)
              s1 = 1 - cumsum((U*S)**2, axis=1)[:, r-1]
              auc(-s1[filt & gold], -s1[filt & decoy])      # label used here only
```

**Run.** Pooled AUC gold-vs-decoy by r (claimed direction, k = 11 if you take the best):

| r | 1 | 2 | 3 | 4 | 6 | 8 | 12 | 16 | 24 | 32 | 48 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gold-vs-decoy | 0.452 | 0.479 | 0.434 | 0.462 | 0.511 | 0.511 | 0.494 | 0.487 | 0.507 | 0.497 | 0.444 |
| gold-vs-all | 0.558 | 0.554 | 0.566 | 0.575 | 0.612 | 0.612 | 0.601 | 0.591 | 0.597 | 0.587 | 0.529 |

LOSO (r chosen on five seeds by mean AUC gold-vs-decoy, claimed direction; k = 1):

| held-out seed | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| r chosen | 6 | 6 | 6 | 6 | 24 | 8 |
| AUC gold-vs-decoy | 0.546 | 0.526 | 0.432 | 0.529 | **0.362** | 0.525 |
| AUC gold-vs-all | 0.641 | 0.625 | 0.543 | 0.691 | 0.479 | 0.630 |
| p@20 (gold, decoy) | (2, 3) | (4, 7) | (1, 8) | (5, 8) | (1, 10) | (3, 1) |
| p@50 (gold, decoy) | (7, 11) | (8, 17) | (5, 15) | (7, 16) | (5, 15) | (6, 13) |

Spread **[0.362, 0.546]**; every fold ≤ 0.55. Top-20 gold 16/120 = 13% vs 7.8% base, but decoys 37/120 = 31% vs 18% base: it enriches decoys more than gold; gold share among planted hits 30%, equal to the pool's 30%. Sign-fitted variant (k = 2): the training seeds pick the opposite sign on every fold, held-out [0.532, 0.477, 0.518, 0.498, 0.608, 0.495], spread [0.477, 0.608], and gold-vs-all collapses to 0.35–0.46. The gold-vs-all ≈ 0.61 at r = 6–8 is the persistence/volume signal `rule_weeks` already carries (0.685), seen through the row norm (Spearman of s1 with total mass −0.28).

**DEAD** — kill: held-out AUC gold-vs-decoy ≤ 0.55 on all six folds (min 0.362); the pooled best over 11 r values is 0.511.

---

## H-S2 — leverage score under the same rank-r SVD

```
H-S2
  STATISTIC   s2(key) = || U_r[key, :] ||^2 ,  U_r the first r left singular vectors of the row-normalised M_seed.
  READS       identical to H-S1.
  FORBIDDEN   identical to H-S1.
  CLAIM       a planted row is an influential, structured row of the top-r subspace: gold has HIGHER leverage than
              decoy; AUC gold-vs-decoy >= 0.65 on the filtered pool.
  KILL        held-out AUC gold-vs-decoy <= 0.55 on any LOSO fold, or pooled AUC < 0.5.
  COST        as H-S1 (same SVD).
  TEST        s2 = cumsum(U**2, axis=1)[:, r-1];  auc(s2[filt & gold], s2[filt & decoy])
```

**Run.** Pooled AUC gold-vs-decoy by r: 0.453, 0.512, 0.442, 0.474, 0.536, **0.541** (r = 8), 0.516, 0.496, 0.505, 0.506, 0.444; gold-vs-all peaks at 0.611 (r = 6) and falls to 0.364 at r = 48.

LOSO (claimed direction, k = 1):

| held-out seed | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| r chosen | 12 | 8 | 8 | 8 | 8 | 8 |
| AUC gold-vs-decoy | **0.402** | 0.517 | 0.487 | 0.543 | 0.458 | 0.578 |
| AUC gold-vs-all | 0.533 | 0.583 | 0.563 | 0.662 | 0.485 | 0.629 |
| p@20 (gold, decoy) | (3, 6) | (2, 5) | (3, 5) | (1, 7) | (0, 2) | (3, 4) |
| p@50 (gold, decoy) | (6, 15) | (5, 13) | (5, 11) | (7, 14) | (3, 8) | (9, 8) |

Spread **[0.402, 0.578]**; one fold above 0.55, five at or below chance ± noise. Top-20 gold 12/120 = 10%, decoys 29/120 = 24%; gold share among planted 29%. Sign-fitted (k = 2): [0.504, 0.491, 0.444, 0.497, 0.458, 0.552], spread [0.444, 0.552]. Leverage tracks the number of active sites (Spearman −0.28), not planting.

**DEAD** — kill: held-out AUC gold-vs-decoy ≤ 0.55 on five of six folds (min 0.402); pooled best 0.541.

---

## H-S3 — rank-1 energy (scale-free spectral norm) of the key's own site × week submatrix

```
H-S3
  STATISTIC   s3(key) = sigma_1(X_key)^2 / || X_key ||_F^2 ,  X_key the key's 7 x 26 matrix of bucketed 6-week-window
              counts (raw buckets, not normalised; the scale-free form of the spectral norm).
  READS       identical to H-S1 (the key's own cells only; no other key enters).
  FORBIDDEN   identical to H-S1.
  CLAIM       one latent cause => one site profile times one time profile => X_key is near rank 1: gold has HIGHER
              s3 than decoy; AUC gold-vs-decoy >= 0.65 on the filtered pool.
  KILL        pooled AUC gold-vs-decoy < 0.5 (direction reversed) or any per-seed AUC <= 0.55 (no parameter, so
              LOSO reduces to per-seed scores).
  COST        one 7 x 26 SVD per key; no fit.
  TEST        sv = svd(W6, compute_uv=False); s3 = sv[:,0]**2 / (W6**2).sum((1,2))
              auc(s3[filt & gold], s3[filt & decoy])
```

**Run (claimed direction, k = 1).** Pooled AUC gold-vs-decoy **0.328**; gold-vs-all 0.585. Per seed gold-vs-decoy [0.359, 0.303, 0.218, 0.331, 0.272, 0.460]; gold-vs-all [0.647, 0.547, 0.496, 0.658, 0.525, 0.618]. p@20 (gold, decoy) [(1,13), (0,11), (0,16), (0,14), (0,14), (1,9)] — 79/120 decoys, 2/120 gold; p@50 [(6,26), (4,27), (2,25), (4,27), (3,26), (7,15)]. Medians: other 0.702 < gold 0.805 < decoy 0.880 — non-monotone across the three classes.

**DEAD** — kill: direction reversed (0.328 pooled, every seed below 0.5). The mechanism predicted the wrong object.

**Why, from the generator** (`/home/user/ng-deploy/research/mycelic/corpus.py`, build_corpus): a real pattern's facet j draws its users with `pick_users(k, [regs[j % len(regs)]])` — one region per facet, facets 4–18 days apart — so the key's site × week matrix is a chain of region-local blocks at different weeks (rank ≈ number of facets). A decoy's facet draws `pick_users(k, regs)` — rotating over all its regions — so every facet shares one site profile and the matrix is near rank 1. At 200 users the org has one site per region (7 sites, `WORLD_TABLE`), so the region-local structure is visible at site granularity. Background ('other') rows have no block structure at all and are the least rank-1. The "shared latent cause ⇒ low rank" signature is real, and it belongs to the decoys.

**Post hoc, k = 2, reported for the falsifier (`test_diag_s3.py`, `diag_s3.json`), not promoted:**

- Reversed S3 (gold LOWER): pooled AUC gold-vs-decoy 0.672; per seed [0.641, 0.697, 0.782, 0.669, 0.728, **0.540**] — spread [0.540, 0.782], fails the pre-registered any-fold floor on seed 5. Gold-vs-all 0.415: it ranks the 1,310 'other' keys above gold, so as a positive ranker on a pool that is 74% 'other' it is harmful.
- By decoy type (reversed AUC gold vs that type): type 1 mixed-chain n = 121 → 0.554; type 3 near-miss n = 129 → 0.622; type 2 temporal-scramble n = 21 → 0.960; type 4 dup-inflation n = 19 → 1.000; type 5 retracted n = 27 → 0.990. It perfectly separates the 67 "coherent" decoys and barely touches the 250 (79%) that are types 1 and 3.
- It is a proxy for burst count: Spearman(reversed s3, number of maximal runs of non-empty weeks) 0.443; **burst count alone gives AUC 0.691 > 0.672**. Number of active sites alone: 0.450. Inside rule_weeks ≥ 11 (736 keys, 99 gold, 157 decoy) reversed S3 is 0.703, per seed [0.686, 0.744, 0.811, 0.639, 0.789, 0.573]; rank-sum with rule_weeks 0.694.
- Negative filter "drop s3 ≥ T" on the filtered pool (gold dropped / decoy dropped / other dropped; gold dropped per seed):
  T = 0.999 (exactly rank 1 = a one-burst key): 70 dropped, **0/138** gold [0,0,0,0,0,0], 33/317 decoy, 37/1310 other → gold:decoy 138:284.
  T = 0.95: 190 dropped, 11/138 gold [2,1,2,2,1,3], 118/317 decoy, 61/1310 other → 127:199.
  T = 0.90: 351 dropped, 29/138 gold, 153/317 decoy → 109:164. Below 0.95 the gold cost rises faster than the decoy gain.
  Share of exactly-rank-1 keys: gold 0.0%, decoy 10.4%, other 2.8%.

The T = 0.999 cut is the only clean thing here and it is not spectral in substance: "exactly rank 1" means the key has one facet burst (decoy types 1 and 3 scatter their facets across keys/anchors, leaving single-facet keys). Family 5 should pre-register a burst-count statistic directly; it is strictly stronger and cheaper. Because the sign was chosen after seeing labels and all six dev seeds were used, nothing on this list can be validated on fresh dev data; sealed seeds stay untouched.

---

## Verdict

**DEAD** — all three. Kill: in the pre-registered direction no statistic exceeds pooled AUC gold-vs-decoy 0.541 (S2, best of 11 r), LOSO held-out spreads are [0.362, 0.546] (S1) and [0.402, 0.578] (S2), and S3 is reversed at 0.328 because in this generator the decoys, not the planted patterns, are the low-rank objects. The reversed S3 (k = 2) at 0.672 fails its own any-fold floor (seed 5: 0.540), is beaten by a plain burst count (0.691), and ranks background above gold. Nothing here clears `rule_weeks` (0.611) in the claimed direction, so nothing is handed to the break-even check.

What would resurrect the family: a world in which planted facets share a site profile across time (the mechanism's premise) — i.e. a different generator — or exported cells finer than one site per region. Neither is available under the current disclosure contract.

## Files

- `PREREG.md` — statistics, directions, grid, kills; written before any number.
- `test_build_matrix.py` — world rebuild → `matrix_seed-{0..5}.npz` (W6 = bucketed 6-week-window cells used; W1 = bucketed weekly cells saved, unused by the pre-registered tests).
- `test_spectral.py` → `results.json` — S1/S2/S3, AUCs, precision, LOSO, reference.
- `test_diag_s3.py` → `diag_s3.json` — the post hoc S3 diagnostics above (k = 2).
- Run: `H1_OUT=<this dir> PYTHONPATH=/home/user/ng-deploy nice -n 10 $PYTHON test_build_matrix.py && H1_OUT=<this dir> $PYTHON -I test_spectral.py`.

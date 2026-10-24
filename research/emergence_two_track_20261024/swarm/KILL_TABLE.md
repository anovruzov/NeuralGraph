# Kill table — what died in wave 1, why, and what would bring it back

Dead hypotheses are a deliverable: they are what stops wave 2 from re-proposing them.

| id | statistic | killed by | number | resurrect only if |
|---|---|---|---|---|
| H1-S1 | rank-r reconstruction residual | AUC ≤ 0.51 pooled, LOSO 0.36–0.55; enriches decoys at top-20 (37/120 vs 16/120 gold) | 0.511 | a generator where planted facets share a site profile across time |
| H1-S2 | rank-r leverage score | pooled 0.44–0.54; LOSO 0.40–0.58 | 0.541 | same |
| H1-S3 | rank-1 energy of the key's site×week block | **direction reversed on every seed**: decoys are the low-rank class (`corpus.py pick_users`: gold reporters from one region, decoy from all) | 0.328 | exported cells finer than one site per region |
| H2-1 | PU class-prior calibration | π̂ 0.29–0.93 (rule-week features) or 0.014–0.031 (buckets) vs 0.049; known positives are gate-selected, not SCAR | — | an accepted set that is ≥ ~70% gold instead of 31% (a decoy-rejecting judge, or confirmation fed back) |
| H2-2 | PU ranker on exported features | 0.643 pooled, below rule_weeks on 3/6 seeds; ranks plantedness, not gold-vs-decoy | 0.643 | same |
| H2-3 | PN transfer from the snapshot pool | 0.708 **with** the gold label of the asked set; the inference-time version (system's own verdicts) collapses to 0.561 because the judge supports 207 of 216 asked decoys | 0.561 | same |
| H3-1 | per-family (chain) prior | LOSO 0.416; family × seed interaction p < 10⁻⁴; chain drawn uniformly per pattern in the generator | 0.416 | a generator where the planted chain is not uniform |
| H3-2 | family prior × rule-week likelihood | 0.670, below the E-only ablation 0.683 on 6/6 seeds — adding R subtracts | 0.670 | same |
| H4-1 | information gain I(H; G \| b) | zero wherever the buckets already decide the gate, which is where the yield is; ranks the undecidable first | 0.638 | a sequential design the gate forbids (re-ask) |
| H4-2 | expected corrected verdicts | decoys clear at the same rate as gold within a class; 1.2 decoys per gold — the +3 −14 shape | 0.615 | — (its week choice survives separately: argmax_w P̂(clear\|b_w), 1-in-30 → 1-in-18) |
| H5-1 | gap variance, run count, density, first/last rule week | ≈ 0.50; longest run 0.668 but only classes 1/3 | 0.50 | — (longest run kept as the free line) |
| H7-2 | contradiction as negative weight, α ∈ {0…3, drop} | **monotone worse in α** (0.541 → 0.483); d5 passes 28/28 under every α; gold's contradiction lands inside its window, d5's after | 0.483 | — |
| H7-3 | "the ceiling is optimistic from overcounting" | B 205/240, K 199/240 identical per seed after root de-dup | 0 change | — |
| wave-0 | rule_weeks ≥ 11 as a ranker | H6: at the operating point, six-seed totals under the displacing budget −11.2 found −13.7 decoys; under the additive budget +5.1 found **+7.1 accepted decoys** against a bar with zero headroom | — | kept as a negative filter only (≤ 6 holds zero gold) |

Promoted, pending falsification: **H5-2 / H5-3**, **H4-3**, **H7-1**, **H3-3** — see `WAVE_1_RESULTS.md` §3 for each one's
pre-registered kills.

Closed before this wave and not reopened: routing, index geometry, hypergraph traversal, re-asking a persisting
candidate, recursive acquisition at the gate, blind promotion of rule-only candidates, persisted graph edges
(0 of 539 verdicts).

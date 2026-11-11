# Experiment plan and compute budget

Hardware for the campaign that produced `results/`: one 4-vCPU x86-64 container,
15 GB RAM, no GPU, no reachable model endpoint (see README "Limitation").
Measured unit costs (Tier 1 = 1,000 workers / 10,000 records / 30 rounds;
Tier 2 = 10,000 workers / 100,000 records / 30 rounds):

| run | Tier 1 | Tier 2 |
|---|---|---|
| world generation | 0.9 s | 42 s |
| B7_mycelic (hierarchy, order-3 sketches) | 18–25 s, 0.35 GB | 310 s + 150 s evaluation, 3.4 GB |
| baselines B1/B3/ORACLE | 5–60 s (measured by the integrator) | est. 5–20 min |

Three jobs run in parallel (`--jobs 3`); a Tier-2 job needs ~3.5 GB.

## Families, grids and seeds

Seeds are paired across systems and conditions (same world per seed). Tier-1
sweeps use 10 seeds unless noted; the main Tier-1 aggregation comparison uses
30 seeds; Tier-2 uses 5 seeds (10 where runtime allows, reported per table).

| family | grid | seeds | purpose |
|---|---|---|---|
| aggregation | all 11 systems | 30 (T1), 5 (T2 via headline) | §3, §4, §22 core metrics, layer fidelity table |
| compression | B7 x compression ∈ {full_sketch, order_capped, order2_plus_significant, significant_cells_only, claims_only} x sketch_order ∈ {1,2,3} x cadence ∈ {1,2,4} | 5 | §5 curves |
| poisoning | malicious fraction ∈ {0,.01,.05,.10,.20,.30,.40} x {B1,B3,B6,B7,B8,B9} | 5 | §6 |
| edge_security | 10 % and 20 % malicious x detector ∈ {none, rules, central_classifier, cloud_classifier, local_slm, hybrid_local_rules, lineage_aware} | 5 | §7 |
| contradictions | {B6,B7,B8} with 30 contradiction groups | 10 | §8 |
| temporal | {B6,B7} with 30 revision groups | 10 | §9 |
| independent_support | scenarios x {B6,B7} | 10 | §10 |
| privacy | quote_policy ∈ {none, redacted, raw} x k_anonymity ∈ {0,3,10} x B7, plus B1/B3 | 5 | §11 |
| failures | kind ∈ 9 x rate ∈ {.01,.05,.10,.20,.30,.50} x B7 vs paired no-failure | 5 | §12 |
| hierarchy | layers ∈ {1,2,3,5,7} + B5 flat with fan-in budget | 10 | §13 |
| scaling | N ∈ {100, 1k, 10k, 50k, 100k} x {B7, B1} | 3 (≤10k), 1 (≥50k) | §14 |
| models | edge profile ∈ {sim-1b, 3b, 7b, 8b, 14b, perfect} x {B7, B8} | 5 | §15 (synthetic capability sweep) |
| routing | 7 routers on a finished B7 run | 5 | §17 |
| questioning | policy ∈ {none, fixed, confidence, eig, budget} x sketch_order ∈ {2,3} | 5 | §18 |
| headline | Tier 2, 10 % malicious, {B2,B3,B5,B6,B7,B8,B9,ORACLE} | 5 | §24 executive table |

Estimated wall time with 3 jobs: Tier-1 families ≈ 4–5 h; Tier-2 headline ≈ 2 h;
scaling to 100k ≈ 1–2 h. Anything not completed within the session is listed
in the report as *not run* with the command that would run it.

## Statistical reporting
Every comparison against B7_mycelic is paired by seed: mean ± sd, paired
bootstrap 95 % CI (10,000 resamples), paired t and Wilcoxon p-values with
Holm correction within each metric family, and Cohen's d_z. No p-value is
reported without its effect size.

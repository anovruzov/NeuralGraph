# NEGATIVE_RESULT.md — the swarm's answer

The swarm existed to decide one thing (`SWARM_PROMPT.md` §0): whether a statistic computable from exported
contracts alone separates the 138 gold keys in the rule-only candidate pool from the rest well enough that
asking about the top of the ranking pays for a question. Fourteen agents, two waves, every hypothesis
pre-registered with its kill, every survivor handed to a separate falsifier. The answer is **B**.

> **B.** No statistic computable from exported contracts separates the rule-only pool above **0.39 precision
> at top-20** (0.11 at 2,000 users) **at the week it would ask**, which is below the break-even of **0.45** at
> that week (0.47 under the displacing budget before feasibility is measured; ≈ 0.8 at the shipped rule
> layer's week). The 83 feasible never-asked patterns are unreachable under the current disclosure invariant,
> and 85% is a founders' decision about what the world discloses, not an engineering backlog.

This is a success. It cost one afternoon and ~3.5M tokens, and it closes a quarter of building a geometric
stack on a bottleneck that was never where the stack would have operated.

## 1. How each family died

| family | statistic | died on | number |
|---|---|---|---|
| spectral / low-rank (H1) | residual, leverage, rank-1 energy | direction reversed — the generator gives **decoys** the shared site profile | 0.33–0.54 |
| positive-unlabeled (H2) | PU ranker, PN transfer | known positives are gate-selected, not SCAR; the inference-time version collapses because the judge supports 207 of 216 asked decoys | 0.56–0.64 |
| rule identity (H3) | per-family prior | chain drawn uniformly per pattern; LOSO family correlation negative on 6/6 folds | 0.42 |
| rule-week band (H3-3) | non-monotone band | beats the free line on 4/6 seeds; top-20 precision 0.14 vs p* 0.39; adds 5–12 accepted decoys | 0.70 |
| information gain (H4-1) | I(H; G \| b) | zero exactly where the yield is | 0.64 |
| expected utility (H4-3) | S_Net over the cell class | fails its own per-seed kill; 80% of its AUC orders keys that can never clear; inverts at 2,000 users (0.45); c_g at its own week 0.15–0.24 → p* > 1 | 0.73 → 0.66 at-decision |
| co-activation (H5-3, the lever) | shape-gated count | **c_g measured at its own week = 0.616, not ~0.75**, so p* 0.447 > P@20 0.393; at 2,000 users the gate admits 67% of background, P@20 0.108 | 0.39 |
| contradiction as inhibition (H7-2) | pos − α·neg | monotone worse in α; gold's contradiction lands inside its window, d5's after | 0.48 |

The surviving statistic, H5-2 co-activation, is clean — p < 0.0005 against an empirical best-of-56 null, every
leak test passed — and is a **density artefact of a sparse world**: 0.945 at 200 users, 0.742 at 2,000 and
below 0.70 on the two decoy classes that are three quarters of the pool. A 200-user benchmark is a sparse world.

## 2. What the falsifiers established that the hypotheses got wrong

1. **Feasibility has to be measured at the asked week.** Every hypothesis that quoted ~75% feasibility was
   reading the pool's label-window flag, which is true of all 240 gold by construction. At the ranker's own
   week the figure is 0.62 (H5-3) or 0.15–0.24 (H4-3); at the shipped rule layer's week, 0.34. H6 said this
   term was dominant. It was.
2. **The mechanism was wrong twice.** H1 and H5 independently blamed "region = site at 200 users"; tested
   directly, region granularity recovers almost nothing and a seed that keeps the seven-site shape still loses
   the signal. The cause is **record density** — five times the records on the same ~150 entities. H4's "rare
   gold passes through the thin state" was false at 200 (it is common gold, 50/137 vs 6/103) and inverted at
   2,000.
3. **An AUC can be honest for the wrong quantity.** H4-3's 0.73 was carried by ordering keys at classes where
   the gate never clears, by site breadth; among keys that can yield a verdict it is 0.66, inside H8's selection
   band.
4. **Look-ahead hides in max-over-weeks.** H4's 0.842 on the already-asked set read a later week than the one
   asked on 83% of rows; at the asked week it is 0.66.

## 3. What is left standing, and what each is worth

| item | status | worth |
|---|---|---|
| `rule_weeks ≤ 6` holds zero gold (1,066 keys) | measured, every seed | a free 38% pool shrink; buys no pattern |
| single-burst keys (rank-1 submatrix) are never gold (0/138) | measured, chosen after seeing labels | a free filter, not re-validatable on dev seeds |
| H7-1 echo-decoy veto (type-4 passes 57 → 4, zero gold cost) | **pending F-H7** | a decoy-column gate fix if the shipped roots bucket is what H7 modelled; cannot alone clear the decoy bar |
| reorder the **already-asked** pool by the exported class at the snapshot week | alive at replay level only: 138/240 vs rule-first 128 (e2e-confirmed 127), decoys 97 vs 113, seed-swap stable, 5 better / 1 tie | **one e2e arm**, budget-matched against rule-first, at-snapshot score, a label-free variant alongside (137 found, rare 8 → 4, decoys 110) — and a founders' ruling first on whether a label-fitted class table is "labels selecting" |
| H5-2 co-activation | reclassified | a fact about sparse worlds; a caution against any 200-user-only result |

None of these reaches a never-asked pattern. They move the decoy column or re-rank what is already asked.

## 4. What would reopen the question

- A world where planted facets share a site profile across time, or exported cells finer than one site per
  region (H1's resurrection condition).
- An accepted set that is ≥ ~70% gold instead of 31% — a decoy-rejecting judge or human confirmation fed back
  (H2's).
- A sequential design the gate forbids: a posterior and a re-ask instead of a one-shot threshold (H4's).
- A denser benchmark run **first**, before any 200-user statistic is called a lever (F-H5's and F-H4's).

## 5. Cost

Fourteen agents of a cap of twenty-four; ω(E) = 10 for the first wave, measured. No e2e run, no suite, no
commit, no push by any agent. One stray file created and removed by F-H3, disclosed. One false claim in a commit
message by the orchestrator, corrected within the hour and recorded in `AUDIT_LOG.md` tick 8.

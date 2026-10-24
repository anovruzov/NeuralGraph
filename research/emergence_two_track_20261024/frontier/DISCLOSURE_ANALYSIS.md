# Disclosure analysis: per-site k versus k on a secure cross-site total

**Status: analysis for a founder decision. Nothing here is adopted.** It compares today's disclosure contract with
one alternative. It states what each releases, what the alternative buys (measured offline), and the privacy risks it
adds or removes, with mitigations.

## 1. The two contracts

| | today: per-site k | proposed: k on a secure cross-site total |
|---|---|---|
| a site sends | its own weekly count per (entity, predicate), plus roots and reporters; any count < 3 replaced by `'<k'` | a masked share of the same counts, via secure aggregation |
| HQ learns | **each site's exact count when ≥ 3**, and which sites are below 3 | **only the total** over contributing sites, released when total records ≥ 3, reporters ≥ 3 and roots ≥ 3; plus the number of contributing sites |
| HQ never learns | record text, authors, reporters | the same, **plus every per-site count** |
| questions and verdicts | unchanged | unchanged |

Secure aggregation is the standard construction for this: pairwise masks cancel in the sum, so the aggregator sees
only the total. See *Practical Secure Aggregation for Privacy-Preserving Machine Learning*, Bonawitz et al., ACM CCS
2017. That citation is from memory and was not fetched in this session; verify it before relying on it.

## 2. What it buys (offline upper bounds, dev seeds 0–5)

Raw: `raw/plateau/agg_ceiling_by_world_size.txt`, `raw/plateau/agg_specificity_by_world_size.txt`. In the proposed
rows, past weekly totals are thresholded exactly as they would be released.

| gate | 200 users: recall / non-planted pass | 10K users: recall / non-planted pass |
|---|---|---|
| today, per-site rule | 0.742 / 16.5% | 0.683 / 72.4% |
| total + burst z ≥ 3 | 0.900 / 23.5% | **0.908 / 43.8%** |
| total + burst z ≥ 4 | 0.787 / 17.5% | 0.787 / 30.3% |

These are ceilings: perfect detection and judging. The site judge and the question budget sit downstream.

## 3. Privacy: what gets better, what gets worse

**Better.**

- **Per-site counts disappear.** Today HQ sees exactly how many records a site holds on a key whenever that number is
  ≥ 3, which can single out a small site or team. Under the proposal HQ sees only the sum.
- **The anonymity set grows.** A released total needs ≥ 3 reporters drawn from several sites, not from one.

**Worse, or new — and how to bound each.**

1. **Contributions under k are now counted.** A site holding 1–2 records on a key adds to a released total; today
   those records are invisible. HQ still cannot tell which site, or which reporter, contributed. The release still
   requires ≥ 3 reporters in total, so an individual remains one of at least three.
2. **Differencing across weeks.** Totals for overlapping windows can reveal that one record arrived (3 → 4). Today's
   per-site exact counts have the same property. Mitigation: release totals in buckets (the pack already supports
   `verdict_count_buckets`), or add calibrated noise.
3. **Collusion between sites.** Secure aggregation protects against HQ, not against sites that pool their own shares.
   With two contributing sites, one site learns the other's count by subtraction. Mitigation: release only when at
   least **3** sites contribute, or add differential-privacy noise to the total.
4. **Which sites contributed.** The protocol must reveal at most *how many* sites contributed, never which ones.
5. **Repeated releases.** If noise is added, the privacy budget composes across weeks and keys and must be accounted
   for. Without noise, the guarantee is k-anonymity on totals — the same kind of guarantee as today, applied to a
   different set.

## 4. Options

| option | recall ceiling at 10K | main residual risk |
|---|---|---|
| A. keep per-site k | 0.683 | small sites visible through exact counts ≥ 3 |
| B. secure total, k on total, ≥ 3 contributing sites, bucketed release | see §2 | contributions under k are counted; differencing needs buckets |
| C. B plus differential-privacy noise on totals | below B (noise costs recall; not measured) | the privacy budget must be managed |

## 5. What adopting B would take

- A secure-aggregation step at the site Boundary and at HQ.
- A detector variant that reads totals plus the burst statistic.
- New Boundary and contract tests.
- An end-to-end run, then held-out seeds.
- This is a contract change. It is versioned as a new track, never folded into the frozen benchmark.

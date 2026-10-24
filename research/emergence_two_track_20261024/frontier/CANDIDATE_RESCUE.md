# Rule-candidate rescue under an identical question budget

Founder decision (2026-10-24): redirect to the 142 never-asked patterns; compare budget-matched arms; never select
with gold labels. All results here are **DIAGNOSTIC** (dev seeds 0–5, 200 users, v4 config `rs_nd_n1_n2`, lexical
judge). Sealed seeds were not touched.

## Method: replay on real verdicts

The v4 run asked **every** candidate with a snapshot: 3,181 questions over six seeds. Each budget-matched arm is a
subset of those questions, under the same world, config and verdicts. Only the selection differs.

- `tools/rescue/dump_pools.py` rebuilds each world and detection. Per candidate it records:
  - inference-time fields: detector score, rule-hit fields with their weeks, HQ's own k-suppressed weekly cells;
  - the verdict the v4 run got (fabric note status and lineage);
  - a gold label, read only by the scorer.
- **The replay reproduces every v4 report exactly**: found, decoys and background-supported, on all six seeds.
- Budget B = the shipped `TOP_N` = 60 questions per seed.

Caveat: a question's verdict could depend on how many other questions were asked (site budget of 5 per entity per
day). The replay inherits v4's load, and verdicts can only be better under a lighter load, so the replay is
conservative for every arm. The end-to-end confirmation in §4 removes this caveat.

## 1. Rescue arms (`tools/rescue/arms.py`, raw `raw/rescue/arms_seeds0-5.txt`)

Arms at B = 60 per seed: top (1−f)·B detector candidates by shipped score, plus f·B rule-only candidates picked by
one of three selectors:

- A1: rule strength (sites with a rule hit, then summed lower-bound count, then rule weeks);
- A2: HQ cell excess z over the key's own earlier weeks;
- A3: random (500 draws).

| arm | f | found / 240 | rare | decoys | bg | patterns gained | lost | rule questions per gained pattern |
|---|---|---|---|---|---|---|---|---|
| A0 snapshot-only | 0 | **118** | 7 | 113 | 13 | — | — | — |
| A1 rule strength | 0.10 | 104 | 7 | 105 | 10 | 0 | 14 | ∞ |
| A2 cell excess | 0.10 | 107 | 8 | 117 | 10 | 3 | 14 | 12 |
| A3 random | 0.10 | 104.2 ± 0.4 | 7.1 | 100.4 | 11.1 | 0.2 | 14 | 212 |
| A2 cell excess | 0.50 | 52 | 7 | 88 | 25 | 5 | 71 | 36 |
| A3 random | 0.50 | 47.8 ± 0.9 | 4.7 | 61.8 | 12.0 | 0.8 | 71 | 217 |
| *all detector candidates (≈86 per seed)* | — | *151* | *17* | *153* | *22* | *33* | *0* | — |
| *v4: everything (≈530 per seed)* | — | *163* | *27* | *181* | *96* | *45* | *0* | — |

**Every rescue arm loses patterns at an identical budget.**

- The rule-only pool yields about **one pattern per 220 questions** at random. The best selector (A2) gets that down
  to 12–40 questions, 5–18× better than random.
- That is still far below what the displaced detector candidates yield.
- Rule-strength ranking (A1) is no better than random.

The key-level finding — 138 of 142 never-asked patterns have a rule-layer candidate — is true, but it does not
translate into answerable questions. A rule-only key's snapshot is its first rule week where a window fits. For most
patterns that week falls outside the pattern's found window: only 65 of 2,663 rule-only snapshot rows across six
seeds are gold (2.4%).

## 2. The measured opportunity is inside the detector pool

Across seeds, the detector pool holds 84–90 candidates per seed and 151 findable patterns. With labels (bound only),
the 60-question budget could hold all 151. Shipped ranking finds 118. The shipped score barely predicts patterns:

| detector rank | 0–14 | 15–29 | 30–44 | 45–59 | 60–74 | 75–89 |
|---|---|---|---|---|---|---|
| new patterns found (6 seeds) | 23 | 24 | 30 | **41** | 28 | 5 |

Re-ranking the same pool at B = 60 (`tools/rescue/det_rank.py`, raw `raw/rescue/detector_rerank_seeds0-5.txt`; ten
inference-time orderings, all listed in the raw file):

| ordering (B = 60 per seed) | found | rare | decoys | bg | per seed |
|---|---|---|---|---|---|
| shipped: score descending | 118 | 7 | 113 | 13 | 18 15 22 24 18 21 |
| random (500 draws) | 104.9 ± 4.6 | 11.7 | 106.5 | 15.2 | — |
| **rule hit on or before snapshot week first, then score** | **128** | 8 | 113 | 13 | 18 18 23 26 21 22 |
| bound (reads labels) | 151 | 17 | 0 | 0 | 22 24 28 30 23 24 |

"Rule hit first" never does worse than shipped (5 seeds better, 1 tie). It costs no extra decoys or background
confirms, and it stays under the decoy bar of 118. It uses no look-ahead: for all 500 detector candidates with a rule
hit, the first rule week is on or before the snapshot week.

Caveat: this is the best of ten orderings tried on the same six seeds, so the +10 is optimistic until confirmed. It
is confirmed end to end in §4, and must be confirmed again on held-out seeds before any claim.

**Reading.** The user's intuition holds in a different form. The rule layer is valuable as a **prioritization signal
for detector candidates**, not as a source of new rule-only questions. The bottleneck is ranking within a pool the
budget already almost covers (86 candidates against 60 questions). Generating more candidates is not the fix.

## 3. The 93-vs-92 feasibility mismatch, reconciled

F/TB_v4 counts a pattern feasible only if its *planted facet records alone* pass the gate. The system can also find a
pattern when ordinary background records on the same key push the count over the threshold. The scorer credits that,
because it scores by key and week. So F is a **planted-evidence ceiling, not an unconditional upper bound**.

In this repo's per-pattern funnel (`raw/plateau/funnel_rows.json`, `raw/plateau/ceiling_b.json`), found but
F/TB_v4-infeasible:

- v2: 5 patterns; v4: 9;
- all 9 are rare;
- 8 of the 9 are feasible under the key model K/TB_v4, which uses every record on the key;
- the 9th (seed 4, p029) is infeasible under K too — a gap in the offline model (near-duplicate roots and N2 are
  modelled only approximately).

**The official run's single mismatch is verified to be this mechanism.** In `raw/plateau/gap_decomposition.csv` it is
seed 4, p022 (rare):

- found at snapshot week 2024-W17;
- infeasible under F;
- feasible under K/TB_v4 at exactly W16–W18, which covers the asked week.

The facets alone fall short, and background records on the same key carry it across the gate. The 179/240 ceiling
therefore bounds *planted-evidence* finds. The scorer can exceed it by background-assisted finds. In the official run
that is 1 case; in v2 5; in v4 9, all rare.

## 4. End-to-end confirmation (running)

Arms: `b60_rs_nd_n2` (shipped order, `--top-n 60`) vs `b60_rs_nd_n2_rulefirst` (same, plus `ablate.py --order
rulefirst`), seeds 0–5. Results are pending.

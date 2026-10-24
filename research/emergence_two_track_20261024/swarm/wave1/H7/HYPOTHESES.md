# H7 — CORRELATED EVIDENCE / INHIBITION (wave 1, family 7). Read-only; dev seeds 0–5; labels score, never select.

## Summary (10 lines)

1. **Reporters are never shared across sites** (0 of 0 in every seed): a user has one site ancestor. The gate cannot overcount reporters.
2. **Roots are shared across sites only by class-4 echo decoys.** Background echoes copy within a site; gold facet records each carry their own root. Of 207 gold, 178 background and 269 d1/d2/d3/d5 keys passing TB_v4 at their rule weeks, **0 pass only because sites share a root**. Of 57 passing d4 keys, **26 do** (per seed 5/1/5/3/7/5).
3. **The ceiling is not optimistic**: root-deduplicated TB_v4 feasibility is identical, B 205/240, K 199/240, per seed, both models. The §01 overcount item closes negative for gold and the ceiling, positive for type-4 decoys only.
4. The overcount **is visible in exported contracts**: a site whose `support_bucket` sits one bucket above its `roots_bucket` is an echo site. AUC d4-vs-gold **0.998**; 19/19 d4 keys score ≥1, 0/138 gold and 0/1,310 background score ≥2.
5. As a gate veto (do not count a confirm whose support bucket index > roots bucket index): **type-4 passes 57 → 4** over the whole pool (per seed 10→1, 9→0, 10→1, 9→1, 10→1, 9→0; asked d4 keys 40→3), **gold 207→207, background 178→178, d1/d2/d3/d5 unchanged, ceiling unchanged**. PROMOTED as a decoy-column fix; it ranks nothing (all-decoy-vs-gold AUC 0.415) and cannot alone clear the SPEC bar (d2+d5 = 119 > 118).
6. **Inhibition as a negative weight is DEAD.** Net support `pos − α·neg` is monotonically *worse* in α on every metric: AUC gold-vs-decoy 0.541 (blind) → 0.524 (α=0) → 0.512 (α=1) → 0.483 (drop); gold gate passes 105 → 100 → 98 → 95 while every decoy class moves ≤1 and **d5 passes 28/28 at every α including drop**.
7. Mechanism: gold's planted contradiction lands **+3 days** inside its support window (34% of gold keys carry one in-window); d5's retraction lands **+30–70 days**, after the weeks at which d5 passes. A per-window inhibitor is pointed at gold, not at decoys.
8. Leave-one-seed-out picks α=blind in all six folds; held-out AUC gold-vs-decoy spread **0.471–0.603**. Precision: S@50 summed 75.5 gold / 166 decoy vs rule_weeks 33.4 / 72 — more of everything, α irrelevant (70.7–75.5).
9. Variants tried and disclosed: 7 α × 2 statistics × 4 week choices (rule, all, last1, last3) = 56 cells. Only "last 3 rule weeks, α≥0" sees d5 (AUC 0.91) — because retractions have arrived and positives aged out — and there gold-vs-decoy is 0.31–0.35 and only 11/138 gold still pass. It is a staleness check at a late ask, not a weight.
10. First function to change, if the veto is taken: `ng-deploy:mycelic/collective/pushdown/gate.py:evaluate` (the `counted` test, ~line 171). Caveat before any claim: zero gold cost is a generator property (one root per gold record); a live replay must confirm the shipped `roots_bucket` tracks distinct roots under the `nd` rule.

Scripts: `test_extract.py` (one world pass per seed, ~1 s each, cache in `cache/`), `h7lib.py` (gate model mirroring `ceiling_b.py`), `test_A_overcount.py`, `test_A2_echo_veto.py`, `test_B_inhibition.py [--weeks=rule|all|last1|last3]`. Outputs `run_*.txt`, `results_*.json`.

---

## H7-1 — echo veto: support bucket above roots bucket marks correlated disclosure

```
H7-1
  STATISTIC   s(key) = max over the key's rule weeks w, over sites with support_bucket >= k, of
                       bidx(support_bucket) - bidx(roots_bucket)          (bidx: '<k'=0, '3-9'=1, '10-49'=2, '50+'=3)
              as a GATE VETO: a confirm is not counted when, at its own site, bidx(support) - bidx(roots) >= 1
  READS       per-site support_bucket, roots_bucket (both already in the verdict body, egress.py BUCKET_FIELDS),
              rule hit weeks (dumps). Variant: reporters_bucket in place of support_bucket.
  FORBIDDEN   touches no gold label, facet record, Placement internal, node id, file path, raw record or cross-site
              value equality. The ORACLE root-dedupe in test_A reads generator event ids and is used only to
              validate the statistic, never inside it.
  CLAIM       s separates class-4 decoys from gold with AUC >= 0.95 (decoys high); as a veto at >= 1 it removes
              >= 90% of type-4 TB_v4 passes at zero gold cost. It is NOT claimed to rank the pool.
  KILL        any gold pattern losing a TB_v4-feasible week to the veto; or < 90% of root-overcounted d4 removed;
              or a seed in which the veto costs gold or background passes.
  COST        O(sites) per key per week from exported buckets; no world rebuild in production. Test used one
              ew.generate pass per seed (~1 s; the 90 s figure is the full pipeline), cache/seed-*.json.
  TEST        test_A_overcount.py (A3), test_A2_echo_veto.py (A4-A6), run_A2_perseed.txt
```

**Ground truth the statistic is validated against — the (A) overcount table.** TB_v4 at each key's rule weeks, model B
(shipped polarity-blind judge); "dedupe" removes every record whose root already appeared at a stronger site in the
same window; "overcount" = passes raw and never passes deduped; model K gives the same d4 figures (`run_A.txt`).

| seed | class | keys | raw pass | dedupe pass | overcount (pass only via shared roots) | raw pass with a shared root present | shared reporters across sites |
|---|---|---|---|---|---|---|---|
| 0 | gold / d4 / other | 40 / 10 / 403 | 35 / 10 / 29 | 35 / 5 / 29 | 0 / **5** / 0 | 0 / 10 / 0 | 0 |
| 1 | gold / d4 / other | 40 / 10 / 403 | 33 / 9 / 28 | 33 / 8 / 28 | 0 / **1** / 0 | 0 / 9 / 0 | 0 |
| 2 | gold / d4 / other | 40 / 10 / 364 | 34 / 10 / 26 | 34 / 5 / 26 | 0 / **5** / 0 | 0 / 10 / 0 | 0 |
| 3 | gold / d4 / other | 40 / 10 / 422 | 36 / 9 / 38 | 36 / 6 / 38 | 0 / **3** / 0 | 0 / 9 / 0 | 0 |
| 4 | gold / d4 / other | 40 / 10 / 400 | 37 / 10 / 29 | 37 / 3 / 29 | 0 / **7** / 0 | 0 / 10 / 0 | 0 |
| 5 | gold / d4 / other | 40 / 10 / 392 | 32 / 9 / 28 | 32 / 4 / 28 | 0 / **5** / 0 | 0 / 9 / 0 | 0 |
| all | gold | 240 | 207 | 207 | 0 | 0 | 0 |
| all | d1 / d2 / d3 / d5 | 183 / 60 / 187 / 60 | 98 / 60 / 111 / 60 | 98 / 60 / 111 / 60 | 0 / 0 / 0 / 0 | 0 / 0 / 2 / 0 | 0 |
| all | **d4** | 60 | 57 | 31 | **26** | 57 | 0 |
| all | other | 2,384 | 178 | 178 | 0 | 0 | 0 |

Ceiling impact (240 gold, label windows, TB_v4, raw vs deduped): **B 205/240 = 205/240; K 199/240 = 199/240**, per seed
identical ([34,33,34,36,36,32] and [34,32,33,35,34,31]); F restricted to on-key facet records 175 = 175 (ceiling_b's F is
179 because it also counts facet records that map to the `revenue_miss` key; B and K reproduce ceiling_b exactly).
**The ceiling is not cut by correlated disclosure.**

Why the table looks like this (mechanism, from the generator, read-only): a user has exactly one site ancestor
(`emergence_world._user_sites`), so a reporter cannot appear at two sites; background echoes copy a record from the
*same site* (`corpus.py` `local_src`); gold facet records get one root each (`corpus.py:566`); only class 4 writes
"ONE event id for all echoes" across 2–4 regions (`corpus.py:659`). N2 already zeroes the roots of a single-facet echo
site; the 26 residual passes are multi-facet echoes (≥3 facet roots echoed at ≥2 sites), which N2 cannot see because
each site alone shows ≥3 distinct roots.

**Exported-contract signature on the wave-0 filtered pool (1,765 keys)**

| signature | AUC d4 vs gold | AUC all decoy vs gold | AUC other vs gold | gold ≥2 | d4 ≥2 | d4 ≥1 | other ≥2 | gold =1 | other =1 |
|---|---|---|---|---|---|---|---|---|---|
| support − roots | **0.998** | 0.415 | 0.241 | 0/138 | 18/19 | 19/19 | 0/1,310 | 9/138 | 127/1,310 |
| reporters − roots | **0.998** | 0.410 | 0.237 | 0/138 | 18/19 | 19/19 | 0/1,310 | — | — |

Per seed AUC d4-vs-gold: 0.000 / 0.000 / 0.000 / 0.004 / 0.000 / 0.000 (gold-vs-d4 direction; `run_A.txt` A3). All 6
root-overcounted keys inside the filtered pool score ≥1.

**As a gate veto, whole pool (3,178 keys), rule weeks, model B**

| variant | gold | d1 | d2 | d3 | **d4** | d5 | other | overcounted d4 removed |
|---|---|---|---|---|---|---|---|---|
| raw TB_v4 | 207 | 98 | 60 | 111 | **57** | 60 | 178 | 0/26 |
| veto support−roots ≥ 1 | 207 | 98 | 60 | 111 | **4** | 60 | 178 | **26/26** |
| veto support−roots ≥ 2 | 207 | 98 | 60 | 111 | 57 | 60 | 178 | 0/26 |
| veto reporters−roots ≥ 1 | 207 | 98 | 60 | 111 | **4** | 60 | 178 | 26/26 |

Per seed under the ≥1 veto: d4 10→1, 9→0, 10→1, 9→1, 10→1, 9→0; gold 35/33/34/36/37/32 unchanged; background
29/28/26/38/29/28 unchanged; d4 keys the detector actually asked (snapshot state) 40 → 3. Ceiling under the veto:
B 205/240 and K 199/240, per seed identical, 0 patterns lost (`run_A2.txt` A5). T=2 removes nothing at the gate
because the passing weeks sit at a one-bucket gap; the ≥2 values in the AUC table occur at other weeks or
non-counted sites. The 4 survivors (`run_A2_perseed.txt`): two sites at (9 records, 3 roots) — both '3-9', so the
bucket granularity hides a 3:1 echo ratio — and two keys passing on records with one root each (background on the key).

**Variants tried:** 2 signatures × 2 thresholds, all reported above; the threshold is the smallest bucket step, not fitted.

**Caveats, stated against interest.** (i) Zero gold cost is a *generator* property: every gold record carries its own
root. In a world where three reporters legitimately report one incident (the case CONTINUATION.md:57 describes as a
bug when collapsed), this veto cuts genuine patterns. (ii) It assumes the shipped `roots_bucket` tracks distinct
roots for echoes; echoes "repeat the original wording almost" (`corpus.text`) and the `nd` near-duplicate-root rule
exists to collapse them (REPORT.md:153), but this is an offline model, not a live replay. (iii) It is a decoy-column
fix only: v4 accepts 181 decoys against a bar of 118 with d2+d5 = 119 alone, so −24..−27 type-4 cannot clear the
bar by itself. (iv) It does not rank the pool (all-decoy-vs-gold 0.415; the one-bucket gap is common in background).
(v) It changes no question count, so agent 6's break-even is not the right test for it; its test is the decoy column.

**Verdict: PROMOTED (1)** — as a gate-side veto for type-4 (echo) decoys, 57 → 4 at zero measured gold or background
cost on six seeds; first function to change `ng-deploy:mycelic/collective/pushdown/gate.py:evaluate` (the `counted`
test). Not promoted as a pool ranker.

---

## H7-2 — contradiction as a negative weight (net support per site)

```
H7-2
  STATISTIC   per site in the 6-week window: net = pos_records - ALPHA * neg_records (floored at 0), bucketed as the
              gate buckets support; roots/reporters over positive records.
              S_a(key) = max over rule weeks w of  sum over sites of lb(net bucket)      ('<k' counts 1)
              P_a(key) = number of rule weeks at which TB_v4 passes under the net model
              ALPHA in {blind (model B: neg counted as support), 0 (model K: ignored), 0.5, 1, 2, 3, drop (Ks)}
  READS       per-site support bucket of positive records, a per-site contradiction-count bucket (a NEW export of the
              same bucket shape; the shipped verdict carries polarity only as confirm/refute), roots and reporters
              buckets, newest week, rule hit weeks.
  FORBIDDEN   no label, facet record, Placement internal, node id, file path, raw record or cross-site value
              equality. Polarity is read per record at the site, exactly as the ceiling_b 's' variants read it.
  CLAIM       some ALPHA in (0, drop) gives AUC gold-vs-decoy on the filtered pool >= 0.65 and beats both extremes
              (blind, drop) on gold-vs-d5 while costing <= 2 gold gate passes relative to blind.
  KILL        AUC monotone in ALPHA between the extremes (no interior optimum), or d5 pass count unchanged under
              every ALPHA, or gold passes fall faster than any decoy class.
  COST        O(sites) per key per week; one world pass per seed for the test (cache), free thereafter.
  TEST        test_B_inhibition.py [--weeks=rule|all|last1|last3]; run_B*.txt, results_B*.json
```

**B0 — where contradiction lives (keys with a polarity −1 record inside some rule-week window, filtered pool)**

| class | keys | with contradiction | share |
|---|---|---|---|
| gold | 138 | 47 | **0.341** |
| d1 | 122 | 9 | 0.074 |
| d2 | 21 | 5 | 0.238 |
| d3 | 132 | 11 | 0.083 |
| d4 | 19 | 1 | 0.053 |
| d5 | 28 | 28 | **1.000** |
| other | 1,310 | 221 | 0.169 |

**B1 — AUC on the filtered pool (1,765 keys; 138 gold, 317 decoy), rule weeks**

| α | S: gold vs decoy | S vs d1 | S vs d2 | S vs d3 | S vs d4 | S vs d5 | S: gold vs other | P: gold vs decoy | P vs d5 | P: gold vs other |
|---|---|---|---|---|---|---|---|---|---|---|
| blind (B) | **0.541** | 0.640 | 0.264 | 0.637 | 0.002 | 0.139 | 0.905 | 0.613 | 0.031 | 0.852 |
| 0 (K) | 0.524 | 0.620 | 0.254 | 0.618 | 0.001 | 0.134 | 0.902 | 0.617 | 0.258 | 0.838 |
| 0.5 | 0.518 | 0.612 | 0.254 | 0.612 | 0.001 | 0.133 | 0.900 | 0.613 | 0.261 | 0.831 |
| 1 | 0.512 | 0.604 | 0.254 | 0.605 | 0.001 | 0.133 | 0.891 | 0.611 | 0.260 | 0.831 |
| 2 | 0.504 | 0.594 | 0.256 | 0.595 | 0.001 | 0.131 | 0.881 | 0.609 | 0.281 | 0.827 |
| 3 | 0.496 | 0.586 | 0.239 | 0.588 | 0.001 | 0.124 | 0.877 | 0.607 | 0.280 | 0.823 |
| drop (Ks) | 0.483 | 0.571 | 0.219 | 0.574 | 0.001 | 0.115 | 0.874 | 0.590 | 0.264 | 0.818 |
| rule_weeks baseline | 0.611 | 0.652 | 0.380 | 0.735 | 0.373 | 0.102 | 0.703 | — | — | — |

Monotone in α on S for every column; no interior optimum. P is flat (0.59–0.62) and equals the baseline.

**B2 — gate-level passes at rule weeks, filtered pool (the cost of α at the gate)**

| α | gold | d1 | d2 | d3 | d4 | d5 | other |
|---|---|---|---|---|---|---|---|
| blind | **105** | 51 | 21 | 57 | 17 | **28** | 152 |
| 0 | 100 | 50 | 21 | 57 | 17 | 28 | 125 |
| 0.5 / 1 | 98 | 50 | 21 | 56 | 17 | 28 | 120 |
| 2 | 97 | 50 | 21 | 56 | 17 | 28 | 120 |
| 3 | 96 | 50 | 21 | 56 | 17 | 28 | 120 |
| drop | **95** | 50 | 21 | 56 | 17 | **28** | 120 |
| pool | 138 | 122 | 21 | 132 | 19 | 28 | 1,310 |

Gold loses 10 passes from blind to drop; decoys lose 2 in total; **d5 loses none under any α**. The only thing α
removes in quantity is background (152 → 120), which the question budget never reaches anyway.

**B3 — expected gold (decoy) in the top-20 / top-50 per seed, statistic S, vs rule_weeks**

| seed | pool | gold | baseline P@20 / P@50 | blind P@20 / P@50 | α=1 P@20 / P@50 | drop P@20 / P@50 |
|---|---|---|---|---|---|---|
| 0 | 308 | 25 | 2.2 / 7.6 (11.0) | 8.8 / 12.5 (28.5) | 8.6 / 13.3 (28.7) | 7.0 / 13.1 (28.8) |
| 1 | 304 | 27 | 3.4 / 7.0 (13.0) | 7.5 / 14.6 (30.4) | 6.9 / 13.8 (31.5) | 6.2 / 13.0 (31.9) |
| 2 | 265 | 21 | 0.8 / 2.0 (12.2) | 10.0 / 13.4 (26.1) | 10.0 / 13.6 (26.2) | 9.1 / 13.6 (26.2) |
| 3 | 304 | 21 | 1.0 / 2.0 (13.9) | 8.0 / 14.0 (29.2) | 8.0 / 13.2 (30.2) | 8.0 / 12.0 (30.6) |
| 4 | 293 | 20 | 2.0 / 9.6 (10.8) | 5.0 / 9.1 (29.8) | 6.0 / 7.9 (30.1) | 5.2 / 8.0 (30.3) |
| 5 | 291 | 24 | 0.0 / 5.2 (11.0) | 7.1 / 11.8 (21.9) | 6.4 / 10.9 (22.8) | 5.8 / 11.0 (22.2) |
| sum | | 138 | 9.5 / 33.4 (71.9) | **46.4 / 75.5 (166.0)** | 45.9 / 72.7 (169.5) | 41.3 / 70.7 (170.0) |

S doubles the gold in the top-50 against rule_weeks and more than doubles the decoys (72 → 166): it is a stronger
persistence statistic, finding more of everything, which is the wave-0 failure mode. α only subtracts gold from it.
Full table for all seven α in `run_B.txt`.

**B4 — leave-one-seed-out over α (statistic S; α chosen on five seeds by AUC gold-vs-decoy, scored on the sixth)**

| held-out | α chosen | train AUC | held-out AUC gold vs decoy | held-out AUC gold vs non-gold | held-out P@50 gold (decoy) | baseline P@50 |
|---|---|---|---|---|---|---|
| 0 | blind | 0.535 | 0.572 | 0.847 | 12.5 (28.5) | 7.6 (11.0) |
| 1 | blind | 0.548 | 0.510 | 0.797 | 14.6 (30.4) | 7.0 (13.0) |
| 2 | blind | 0.530 | 0.603 | 0.834 | 13.4 (26.1) | 2.0 (12.2) |
| 3 | blind | 0.536 | 0.561 | 0.872 | 14.0 (29.2) | 2.0 (13.9) |
| 4 | blind | 0.551 | **0.471** | 0.838 | 9.1 (29.8) | 9.6 (10.8) |
| 5 | blind | 0.545 | 0.531 | 0.823 | 11.8 (21.9) | 5.2 (11.0) |

**SPREAD 0.471–0.603.** The selection never leaves the polarity-blind extreme; inhibition is never chosen.

**Variants tried (all reported, none selected by label):** α × 7; statistics S and P; week choice ×4 —
`rule` (primary), `all` 26 weeks (S and P identical to `rule`: rule weeks cover every week holding a k-site),
`last1` (near-empty: 13 keys pass in total, 1 gold), `last3` (gold vs decoy 0.31–0.35; gold vs d5 0.39 blind →
**0.91** at α≥0; gold vs d4 0.10; LOSO spread 0.237–0.414, α=0 chosen; gold gate passes 11/138). 56 cells; the
effective sample for any cherry-picked cell is 240/56 ≈ 4 patterns, so no cell is a result. The coarser
exported-shape variant (neg bucketed at k, net on lower bounds) was not run: it can only be a coarsening of the
exact net, which is already monotone worse.

**Kill mechanism (from the generator, read-only).** A gold pattern's contradiction is one record planted **+3 days**
after a facet (`corpus.py:583`), so it falls inside gold's own support window — 34% of gold keys carry one in-window,
more than any decoy class but d5. A d5 retraction is `k//2` records **+30–70 days** after each facet (`corpus.py:679`):
diag `run_diag_d5.txt`, e.g. E-ARBOR:chain_supply positives weeks 5–7, negatives weeks 14–16; E-CINDER-EDGE:chain_auth
positives 0–3, negatives 7–9. At the weeks d5 passes, no retraction is in the window; by the time one is, the
positives have aged out and so have gold's. Because the rule is polarity-blind, the retractions also *extend* d5's
rule weeks (to week 15–23 with positives ending at 3–7), which is why the rule_weeks baseline is at 0.102 against d5.
A negative weight inside the window therefore penalises gold first and d5 never. The late-window variant confirms the
only place polarity sees d5 is after a 4–10 week delay — a timing/re-ask lever (RARE_MISS_RECOVERY; re-asking is
closed), not a weight. This reproduces PLATEAU_DIAGNOSIS §4 ("types 2 and 5 are saturated; only a staleness check on
the chain can bring type 5 down") with numbers.

**Verdict: DEAD (kill: monotone in α with no interior optimum; d5 passes 28/28 under every α including drop; gold
passes fall 105 → 95 while decoys fall 174 → 172).** What would resurrect it: a time-asymmetric chain-level statistic
("negative records after the last positive"), which is look-ahead at the asking week and belongs to family 5 or to a
late re-ask, not to this family.

---

## H7-3 — the TB_v4 ceiling is optimistic because sites count correlated disclosure (audit)

```
H7-3
  STATISTIC   s(pattern) = TB_v4_feasible(raw) - TB_v4_feasible(root-deduplicated), over the label window
              (AUDIT statistic: it reads generator root ids, which are not exported; it is an oracle by design and
              is void as a ranker. It exists to test whether the ceiling claims in EVIDENCE_CEILING.md §2 stand.)
  READS       per-site records, roots, reporters, newest week; PLUS world-private event ids (oracle only).
  FORBIDDEN   not a ranker; never used to select. Reads no gold label inside s (feasibility is computed, the label
              is only the pattern's window, as in ceiling_b.py).
  CLAIM       (against interest) at least one gold pattern is TB_v4-feasible only through sites sharing a root, so
              F/TB_v4 179 and B/TB_v4 205 overstate the ceiling.
  KILL        zero gold patterns lost to root-dedupe under F, K and B, every seed.
  COST        one ew.generate pass per seed (~1 s); test_A_overcount.py A2.
  TEST        test_A_overcount.py (A2), run_A.txt
```

| model | feasible raw | feasible deduped | lost | per-seed raw | per-seed deduped |
|---|---|---|---|---|---|
| F (on-key facet records) | 175/240 | 175/240 | **0** | [30,28,30,33,27,27] | [30,28,30,33,27,27] |
| B | 205/240 = 0.8542 | 205/240 = 0.8542 | **0** | [34,33,34,36,36,32] | [34,33,34,36,36,32] |
| K | 199/240 = 0.8292 | 199/240 = 0.8292 | **0** | [34,32,33,35,34,31] | [34,32,33,35,34,31] |

B and K reproduce `ceiling_b.json` exactly (205, 199, per seed); F here is 175 not 179 because this test counts only
facet records on the pattern's chain key, where ceiling_b also counts facet records that map to `revenue_miss`
(EVIDENCE_CEILING.md notes s5:p006 as such a case). The dedupe result is 0 lost under all three.

**Verdict: DEAD (kill observed: 0 gold lost under F, K and B on every seed).** The ceiling stands; correlated
disclosure inflates only type-4 decoy acceptance (H7-1), which the ceiling table never claimed to bound.

---

### Why H7-2 and H7-3 are worse than H7-1 (one sentence each)

H7-2 points its penalty at the class that carries in-window contradiction most — gold — and cannot reach d5 at the
weeks d5 passes; H7-3 was required against interest and came back clean, which is useful for the ceiling claim but
buys no separation. H7-1 is the only statistic here that moves a decoy column at zero measured gold cost, and even it
is a gate fix for one decoy class rather than a ranker of the pool.

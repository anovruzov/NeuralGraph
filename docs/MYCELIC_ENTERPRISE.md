# Mycelic: hierarchical enterprise intelligence — benchmark and architecture study

**Question.** How should thousands of private user-level agents progressively
abstract, route, combine, question, verify and propagate organisational
knowledge upward, so that an enterprise kernel discovers strategic information
no individual employee, team, department, site or region could discover alone?

**Scope of the answer.** This is a mechanism study on a synthetic enterprise
with known hidden ground truth. It compares architectures under one shared
implementation of every cognitive operator, at matched kernel context and with
every tunable knob fitted on held-out seeds. It is not a deployment report and
it does not measure any specific model checkpoint end to end; see
§21 *Direct measurement vs simulation* for exactly what was measured on real
models and what was simulated.


---

## 1. Executive summary

_(filled from results)_

---

## 2. Architecture diagram


```
                          ENTERPRISE KERNEL  (1 node)
                          router · evidence judge · contradiction resolver
                          temporal analyst · question generator
                                    ^   |
   channel A: sketches (complete)   |   |  targeted descent (no broadcast)
   channel B: knowledge objects     |   v
                          REGION            (~7 nodes, 5-27 sites each)
                                    ^   |
                          SITE / SUBSIDIARY (~93 nodes, 1-58 depts each)
                                    ^   |
                          DEPARTMENT        (~736 nodes, 1-18 teams each)
                                    ^   |
                          TEAM              (~5,335 nodes, 4-22 users each)
                                    ^   |
                          USER              (50,000 sovereign local agents)
                                    |
                          raw memory NEVER leaves the user node


  UPWARD, two distinct channels
  -----------------------------
  A  sketch channel   complete, cheap, LLM-free.  Per entity, per site:
                      mention count, bitmask of operational predicates seen
                      >= min_support times, time span.  One integer per
                      entity above the site boundary.  This is what makes
                      discovery possible at all.
  B  object channel   selective, expensive, evidence-bearing.  Knowledge
                      objects carrying content, abstraction, entities,
                      timestamps, source layer, source nodes, lineage path,
                      evidence pointers, independent-support sketch,
                      confidence, contradictions, novelty, revision count.
                      Budgeted per node, so most of it is dropped.

  DOWNWARD
  --------
  Triage over channel A finds entities whose operational evidence spans
  several regions where the entity does not belong.  A best-first descent
  over the same sketches reaches a few dozen of ~57,000 nodes, and the
  reached user agents re-read their OWN raw memory for that entity only.
  Recovered evidence returns with lineage intact and accumulates in the
  kernel's working pool.
```


---

## 3-6. Recommended hierarchy, models, fan-in, and why

_(filled from results)_

---

## 7. Benchmark methodology


### The synthetic enterprise

Six levels — USER → TEAM → DEPARTMENT → SITE/SUBSIDIARY → REGION → ENTERPRISE.
Fan-in is drawn from clipped heavy-tailed distributions, so the tree is
genuinely heterogeneous: small teams beside large ones, satellite offices of
~40 people beside hubs of ~4,000, and regions sized on a skewed weighting so
the largest holds roughly a third of the company and the smallest a few
percent.

### What is in a record

A record is a structured tuple — predicate, anchor entity, auxiliary entity,
day, event id, polarity — realised as a short noisy work note. Surface forms
are drawn from a synonym set per predicate, plus random filler. The synonym
and filler choices are keyed on the **event id**, not the record id, so an
echo of an event repeats the original wording almost verbatim while two
independent observations of the same thing differ. Duplicate detection is
therefore a text-similarity problem every architecture faces equally, and no
system is ever handed the event id.

The entity namespace is **partitioned**: a small global pool of shared
platforms and corporate vendors visible everywhere, and site-private blocks
for the components, tools and suppliers used at one location. Benign
cross-site traffic — shared suppliers, transferred staff, joint projects —
puts a steady trickle of foreign entity mentions everywhere, so "this entity
appears in more than one region" is a weak signal rather than a giveaway.

### What counts as a strategic pattern

A pattern is a contiguous path in a causal predicate schema (seven chains such
as `supplier_substitution → component_defect → yield_drop → shipment_delay →
customer_escalation → revenue_miss`), bound to ONE anchor entity, with
non-decreasing timestamps, whose facets are deliberately scattered across
distant org branches. No user, team, department or region sees more than one
facet. A **rare-signal** pattern is supported by one or two users per facet out
of the whole enterprise.

Crucially, a pattern is equally visible to a centralised system that manages to
get the facet records into one context. The hierarchy gets no structural free
lunch; its only possible advantage is *finding* them under a budget.

### The five decoy classes

| class | what it is | which check rejects it |
|---|---|---|
| D1 entity coincidence | one anchor, predicates from unrelated chains | causal-schema membership |
| D2 temporal scramble | right chain and anchor, wrong time order | temporal ordering |
| D3 near-miss entity | right chain and order, lexically similar but *different* anchors per facet | entity linking |
| D4 duplicate inflation | one real event echoed by many users in many branches | source independence |
| D5 stale chain | a real chain, every link later retracted | temporal state |

D4 is a genuine causal chain that is merely over-supported, so reporting it is
not a false discovery. It is scored separately, by how badly the system
inflates its independent-support count, and is excluded from the precision
denominator. The other four count against precision.

### The fairness contract

Every architecture calls the *same* implementation of every operator:
extraction, novelty and importance scoring, abstraction, entity linking,
causal/temporal/independence verification, contradiction detection,
question generation. Architectures differ only in

* which records or objects reach which operator call, and
* which model tier runs that call.

Operators may read ground-truth fields in order to *simulate* a noisy model,
but their output never exposes `kind`, `group`, `facet`, `veracity` or
`event`. Systems consume only operator output, the org chart and surface text.

Centralised baselines are deliberately given the advantages that are really
theirs: a frontier-tier extractor reading raw text (far better than the small
model at the edge), a global importance ranking that no hierarchical node can
compute from its own subtree, and a calibrated retrieval budget.

### Matched conditions

* **Kernel context is equalised.** Every architecture delivers a comparable
  number of knowledge objects to the kernel.
* **The risk register scales with the entity namespace** (~one entry per
  tracked entity, capped). A constant register was binding at 50k users and
  silently truncated most of the gold out of every system's output.
* **Every knob is fitted on calibration seeds 500–502 and frozen** before the
  evaluation seeds are touched — for the hierarchy, for map-reduce, for the
  centralised triage control and for flat RAG alike.

### Metrics

Raw measurements, never a single composite:

* **AP** — average precision of the kernel's ranked risk register against the
  hidden patterns. Budget-free, standard for ranked retrieval of a known
  relevant set.
* **found** — fraction of hidden patterns for which the register contains a
  matching entry anywhere. Measures discovery independently of ranking.
* **evidence coverage** — fraction of hidden patterns for which the kernel
  ever *held* two or more of the pattern's links under the right entity.
  Separates "could not get the evidence" from "had it and ranked it badly".
* Recall@K, rare-signal recall, FDR, per-class decoy acceptance,
  independent-evidence accuracy, lineage accuracy, provenance preservation,
  evidence precision/recall, contradiction F1, information loss, compression,
  kernel context size, tokens, inference calls, wall-clock, compute units,
  cost per correct discovery, and privacy exposure.

**Two match criteria are computed and both are reported.** `primary`: the
report names the right entity and substantially identifies the chain — at
least two of the gold links and at least half of them. `strict`: at least
`min(3, |gold links|)`. A 3-link pattern reported as a 3-link chain sharing 2
of its links is a real, actionable discovery (right entity, right chain, most
of the story), which the strict criterion scores as zero; the strict column is
never dropped.

### Statistics

Every architecture comparison is **paired on (scale, seed)** — the seed
controls both the org chart and the corpus, so unpaired tests would be swamped
by between-world variance. Aggregates carry bootstrap 95% CIs. With five seeds
an **exact sign test** is reported rather than a t-test, because n=5 does not
support a normality assumption. A sign test on five paired seeds cannot
produce p below 0.0625 even when the effect is unanimous; that is a hard floor
of this design and is stated wherever it matters.


### The synthetic enterprise at each scale

| users | records | teams | depts | sites | regions | users/team (p10–p90) | teams/dept | depts/site | patterns | rare | decoys | entities |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1,999 | 68,254 | 213 | 31 | 9 | 7 | 6–14 (med 9) | 3–10 | 1–8 | 40 | 9 | 50 | 150 |
| 9,999 | 330,015 | 1,074 | 149 | 15 | 7 | 6–13 (med 9) | 4–11 | 1–39 | 40 | 17 | 50 | 599 |
| 49,999 | 1,639,341 | 5,335 | 736 | 93 | 7 | 6–14 (med 9) | 4–12 | 1–58 | 100 | 45 | 125 | 2,999 |

### Calibration (all knobs fitted on held-out seeds, then frozen)

Fitted on calibration seeds [500, 501, 502] at 10,000 users, objective `average_precision`, then frozen:

| knob | chosen |
|---|---:|
| `triage_prior_weight` | 0.0 |
| `question_frac` | 0.25 |
| `mr_budget` | 900 |
| `ct_kernel_ko_cap` | 0 |
| `flat_budget` | 1000000 |

**hierarchy grid**

| triage_prior_weight | question_frac | AP | found | compute |
|---|---|---|---|---|
| 0.0 | 0.25 | 0.03618 | 0.583 | 1.144e+06 |
| 0.0 | 0.55 | 0.03419 | 0.567 | 1.508e+06 |
| 0.0 | 1.0 | 0.02752 | 0.525 | 2.233e+06 |
| 1.2 | 0.25 | 0.03149 | 0.583 | 1.144e+06 |
| 1.2 | 0.55 | 0.02915 | 0.567 | 1.508e+06 |
| 1.2 | 1.0 | 0.02931 | 0.525 | 2.233e+06 |
| 2.0 | 0.25 | 0.02952 | 0.583 | 1.144e+06 |
| 2.0 | 0.55 | 0.02771 | 0.567 | 1.508e+06 |
| 2.0 | 1.0 | 0.02821 | 0.525 | 2.233e+06 |
| 3.0 | 0.25 | 0.02786 | 0.583 | 1.144e+06 |
| 3.0 | 0.55 | 0.02642 | 0.567 | 1.508e+06 |
| 3.0 | 1.0 | 0.02651 | 0.525 | 2.233e+06 |
| 4.0 | 0.25 | 0.02530 | 0.583 | 1.144e+06 |
| 4.0 | 0.55 | 0.02441 | 0.567 | 1.508e+06 |
| 4.0 | 1.0 | 0.02425 | 0.525 | 2.233e+06 |

**map-reduce grid**

| kernel_ko_budget | AP | found | compute |
|---|---|---|---|
| 300 | 0.01321 | 0.033 | 1.500e+05 |
| 900 | 0.03619 | 0.100 | 1.632e+05 |
| 2500 | 0.01762 | 0.258 | 1.990e+05 |
| 8000 | 0.01090 | 0.408 | 2.911e+05 |

**central triage grid**

| kernel_ko_cap | AP | found | compute |
|---|---|---|---|
| 0 | 0.02824 | 0.325 | 4.333e+05 |
| 2000 | 0.01505 | 0.100 | 1.790e+05 |
| 6000 | 0.01589 | 0.325 | 2.384e+05 |
| 20000 | 0.02139 | 0.367 | 3.972e+05 |
| 60000 | 0.02824 | 0.325 | 4.333e+05 |

**flat RAG grid**

| token_budget | AP | found | compute |
|---|---|---|---|
| 60000 | 0.00481 | 0.125 | 8.307e+04 |
| 120000 | 0.02596 | 0.317 | 1.691e+05 |
| 400000 | 0.05951 | 0.608 | 4.840e+05 |
| 1000000 | 0.08091 | 0.775 | 1.114e+06 |

---

## 8. Baseline comparison

Architectures under test:

| id | architecture |
|---|---|
| `A_flat_rag` | lexical schema-aware retrieval over raw text → one kernel call |
| `B_long_context` | unfiltered raw-record sample filling the kernel's context |
| `B2_map_reduce` | cheap extraction over every record → global importance rank → one strong kernel call |
| `B4_central_triage` | **control**: the hierarchy's own entity-level triage, run centrally on one claim pool with no propagation budget, no sketch cap, no routing error and no descent |
| `C_recursive_sum` | recursive summarisation up the org tree, no structure, no lineage |
| `D_hier_nolineage` | hierarchical semantic aggregation **without** lineage or independence tracking |
| `E_hier_lineage` | lineage-aware hierarchical aggregation (pure upward flow) |
| `F_hier_retrieval` | E + targeted downward retrieval |
| `G_hier_questions` | F + continual questioning |
| `H_mycelic_full` | G + semantic cross-links |
| `I_mycelic_completion` | H + hypothesis-driven chain completion |
| `Y_oracle_retrieval` | **reference**: every extracted claim, no budget at all. Not deployable. |
| `Z_random_rank` | **control**: map-reduce's candidates, ranked at random |


#### 2,000 users (68,254 records, 40.0 hidden patterns, 5 seeds)

| architecture | AP | found | R@100 | rare recall | evidence cov. | FDR | decoy acc. | indep. acc. | lineage | info loss | compute (cu) | privacy exp. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 0.212 <sub>[0.166, 0.260]</sub> | 0.815 <sub>[0.780, 0.850]</sub> | 0.495 | 0.753 | 1.000 | 0.903 | 0.415 | 0.597 | 0.615 | 0.003 | 5.11e+05 | 0.252 |
| B_long_context | 0.203 <sub>[0.177, 0.237]</sub> | 0.820 <sub>[0.805, 0.835]</sub> | 0.485 | 0.604 | 0.995 | 0.893 | 0.370 | 0.714 | 0.621 | 0.030 | 1.09e+06 | 0.614 |
| A_flat_rag | 0.201 <sub>[0.166, 0.228]</sub> | 0.820 <sub>[0.780, 0.860]</sub> | 0.510 | 0.788 | 1.000 | 0.899 | 0.410 | 0.606 | 0.630 | 0.003 | 4.70e+05 | 0.252 |
| B2_map_reduce | 0.124 <sub>[0.066, 0.168]</sub> | 0.430 <sub>[0.310, 0.525]</sub> | 0.355 | 0.219 | 0.585 | 0.888 | 0.290 | 0.768 | 0.531 | 0.512 | 49392 | 0.069 |
| G_hier_questions | 0.119 <sub>[0.102, 0.141]</sub> | 0.730 <sub>[0.670, 0.775]</sub> | 0.295 | 0.570 | 0.990 | 0.953 | 0.560 | 0.723 | 0.513 | 0.060 | 8.20e+05 | 0.134 |
| I_mycelic_completion | 0.109 <sub>[0.070, 0.162]</sub> | 0.740 <sub>[0.685, 0.775]</sub> | 0.255 | 0.637 | 0.975 | 0.952 | 0.525 | 0.737 | 0.500 | 0.059 | 1.43e+06 | 0.144 |
| B4_central_triage | 0.108 <sub>[0.063, 0.153]</sub> | 0.755 <sub>[0.650, 0.830]</sub> | 0.255 | 0.689 | 0.960 | 0.951 | 0.450 | 0.727 | 0.488 | 0.060 | 1.31e+05 | 0.884 |
| H_mycelic_full | 0.106 <sub>[0.063, 0.166]</sub> | 0.770 <sub>[0.695, 0.820]</sub> | 0.295 | 0.676 | 0.990 | 0.950 | 0.515 | 0.745 | 0.511 | 0.071 | 8.40e+05 | 0.144 |
| F_hier_retrieval | 0.062 <sub>[0.037, 0.088]</sub> | 0.200 <sub>[0.155, 0.245]</sub> | 0.170 | 0.070 | 0.305 | 0.954 | 0.100 | 0.793 | 0.571 | 0.705 | 2.06e+05 | 0.134 |
| Y_oracle_retrieval | 0.059 <sub>[0.044, 0.074]</sub> | 0.675 <sub>[0.615, 0.730]</sub> | 0.170 | 0.603 | 1.000 | 0.956 | 0.385 | 0.607 | 0.475 | 0.005 | 1.43e+05 | 0.884 |
| Z_random_rank | 0.057 <sub>[0.036, 0.078]</sub> | 0.430 <sub>[0.310, 0.525]</sub> | 0.265 | 0.219 | 0.585 | 0.888 | 0.290 | 0.768 | 0.531 | 0.512 | 49392 | 0.069 |
| C_recursive_sum | 0.022 <sub>[0.007, 0.047]</sub> | 0.065 <sub>[0.030, 0.100]</sub> | 0.065 | 0.015 | 0.065 | 0.800 | 0.000 | 0.175 | 0.000 | 0.935 | 41674 | 0.089 |
| E_hier_lineage | 0.021 <sub>[0.011, 0.030]</sub> | 0.120 <sub>[0.080, 0.150]</sub> | 0.120 | 0.039 | 0.220 | 0.890 | 0.020 | 0.533 | 0.525 | 0.824 | 68020 | 0.134 |
| D_hier_nolineage | 0.020 <sub>[0.007, 0.038]</sub> | 0.105 <sub>[0.060, 0.145]</sub> | 0.105 | 0.000 | 0.155 | 0.800 | 0.000 | 0.165 | 0.000 | 0.843 | 55137 | 0.134 |

#### 10,000 users (330,015 records, 40.0 hidden patterns, 5 seeds)

| architecture | AP | found | R@100 | rare recall | evidence cov. | FDR | decoy acc. | indep. acc. | lineage | info loss | compute (cu) | privacy exp. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 0.102 <sub>[0.078, 0.127]</sub> | 0.685 <sub>[0.625, 0.745]</sub> | 0.275 | 0.618 | 1.000 | 0.955 | 0.305 | 0.615 | 0.629 | 0.001 | 2.08e+06 | 0.227 |
| A_flat_rag | 0.071 <sub>[0.053, 0.096]</sub> | 0.660 <sub>[0.600, 0.715]</sub> | 0.230 | 0.497 | 0.985 | 0.957 | 0.345 | 0.726 | 0.645 | 0.045 | 1.09e+06 | 0.127 |
| G_hier_questions | 0.062 <sub>[0.031, 0.108]</sub> | 0.465 <sub>[0.405, 0.530]</sub> | 0.220 | 0.313 | 0.720 | 0.970 | 0.270 | 0.744 | 0.520 | 0.299 | 1.08e+06 | 0.138 |
| I_mycelic_completion | 0.062 <sub>[0.022, 0.112]</sub> | 0.420 <sub>[0.345, 0.505]</sub> | 0.190 | 0.261 | 0.690 | 0.973 | 0.260 | 0.772 | 0.520 | 0.312 | 1.93e+06 | 0.147 |
| H_mycelic_full | 0.044 <sub>[0.019, 0.068]</sub> | 0.440 <sub>[0.405, 0.495]</sub> | 0.165 | 0.333 | 0.720 | 0.971 | 0.260 | 0.719 | 0.532 | 0.310 | 1.07e+06 | 0.147 |
| B_long_context | 0.036 <sub>[0.019, 0.059]</sub> | 0.455 <sub>[0.435, 0.480]</sub> | 0.140 | 0.162 | 0.750 | 0.957 | 0.165 | 0.652 | 0.707 | 0.396 | 1.09e+06 | 0.127 |
| B2_map_reduce | 0.032 <sub>[0.010, 0.061]</sub> | 0.115 <sub>[0.090, 0.145]</sub> | 0.105 | 0.078 | 0.155 | 0.969 | 0.065 | 0.787 | 0.529 | 0.868 | 1.64e+05 | 0.011 |
| B4_central_triage | 0.028 <sub>[0.010, 0.045]</sub> | 0.340 <sub>[0.290, 0.420]</sub> | 0.110 | 0.270 | 0.965 | 0.979 | 0.125 | 0.709 | 0.483 | 0.049 | 4.47e+05 | 0.884 |
| F_hier_retrieval | 0.022 <sub>[0.004, 0.052]</sub> | 0.080 <sub>[0.050, 0.110]</sub> | 0.050 | 0.044 | 0.100 | 0.986 | 0.010 | 0.698 | 0.554 | 0.890 | 3.69e+05 | 0.138 |
| Y_oracle_retrieval | 0.007 <sub>[0.003, 0.010]</sub> | 0.230 <sub>[0.175, 0.285]</sub> | 0.055 | 0.218 | 1.000 | 0.985 | 0.105 | 0.632 | 0.484 | 0.005 | 4.87e+05 | 0.884 |
| Z_random_rank | 0.005 <sub>[0.003, 0.007]</sub> | 0.115 <sub>[0.090, 0.145]</sub> | 0.055 | 0.078 | 0.155 | 0.969 | 0.065 | 0.787 | 0.529 | 0.868 | 1.64e+05 | 0.011 |
| E_hier_lineage | 0.003 <sub>[0.000, 0.006]</sub> | 0.030 <sub>[0.010, 0.050]</sub> | 0.030 | 0.000 | 0.050 | 0.973 | 0.005 | 0.236 | 0.400 | 0.931 | 2.26e+05 | 0.138 |
| D_hier_nolineage | 0.001 <sub>[0.000, 0.003]</sub> | 0.015 <sub>[0.005, 0.025]</sub> | 0.015 | 0.000 | 0.020 | 1.000 | 0.000 | 0.000 | 0.000 | 0.963 | 1.96e+05 | 0.138 |
| C_recursive_sum | 0.000 <sub>[0.000, 0.000]</sub> | 0.000 <sub>[0.000, 0.000]</sub> | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.995 | 1.68e+05 | 0.092 |

#### 50,000 users (1,639,341 records, 100.0 hidden patterns, 5 seeds)

| architecture | AP | found | R@100 | rare recall | evidence cov. | FDR | decoy acc. | indep. acc. | lineage | info loss | compute (cu) | privacy exp. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 0.042 <sub>[0.039, 0.044]</sub> | 0.640 <sub>[0.600, 0.680]</sub> | 0.085 | 0.526 | 1.000 | 0.979 | 0.340 | 0.624 | 0.645 | 0.001 | 1.02e+07 | 0.223 |
| A_flat_rag | 0.012 <sub>[0.009, 0.015]</sub> | 0.402 <sub>[0.352, 0.452]</sub> | 0.026 | 0.089 | 0.648 | 0.976 | 0.172 | 0.656 | 0.717 | 0.452 | 1.24e+06 | 0.025 |
| H_mycelic_full | 0.011 <sub>[0.006, 0.020]</sub> | 0.326 <sub>[0.284, 0.382]</sub> | 0.026 | 0.163 | 0.418 | 0.988 | 0.226 | 0.762 | 0.536 | 0.584 | 2.95e+06 | 0.146 |
| G_hier_questions | 0.011 <sub>[0.007, 0.017]</sub> | 0.340 <sub>[0.312, 0.380]</sub> | 0.032 | 0.156 | 0.408 | 0.988 | 0.216 | 0.754 | 0.526 | 0.590 | 2.93e+06 | 0.138 |
| I_mycelic_completion | 0.009 <sub>[0.009, 0.009]</sub> | 0.350 <sub>[0.350, 0.350]</sub> | 0.010 | 0.200 | 0.420 | 0.988 | 0.220 | 0.705 | 0.532 | 0.572 | 5.20e+06 | 0.146 |
| B4_central_triage | 0.007 <sub>[0.007, 0.007]</sub> | 0.380 <sub>[0.380, 0.380]</sub> | 0.000 | 0.244 | 0.820 | 0.987 | 0.120 | 0.686 | 0.467 | 0.178 | 1.69e+06 | 0.884 |
| Y_oracle_retrieval | 0.004 <sub>[0.002, 0.007]</sub> | 0.250 <sub>[0.190, 0.328]</sub> | 0.016 | 0.184 | 1.000 | 0.992 | 0.102 | 0.612 | 0.483 | 0.005 | 2.43e+06 | 0.884 |
| B2_map_reduce | 0.003 <sub>[0.000, 0.008]</sub> | 0.018 <sub>[0.008, 0.026]</sub> | 0.014 | 0.000 | 0.028 | 0.988 | 0.012 | 0.607 | 0.330 | 0.976 | 7.39e+05 | 0.002 |
| B_long_context | 0.002 <sub>[0.001, 0.003]</sub> | 0.092 <sub>[0.080, 0.104]</sub> | 0.020 | 0.004 | 0.172 | 0.980 | 0.026 | 0.595 | 0.693 | 0.815 | 1.13e+06 | 0.025 |
| Z_random_rank | 0.002 <sub>[0.000, 0.004]</sub> | 0.018 <sub>[0.008, 0.026]</sub> | 0.014 | 0.000 | 0.028 | 0.988 | 0.012 | 0.607 | 0.330 | 0.976 | 7.39e+05 | 0.002 |
| F_hier_retrieval | 0.000 <sub>[0.000, 0.000]</sub> | 0.008 <sub>[0.004, 0.010]</sub> | 0.004 | 0.000 | 0.008 | 0.997 | 0.010 | 0.499 | 0.320 | 0.980 | 1.15e+06 | 0.138 |
| D_hier_nolineage | 0.000 <sub>[0.000, 0.000]</sub> | 0.004 <sub>[0.000, 0.008]</sub> | 0.004 | 0.000 | 0.004 | 1.000 | 0.002 | 0.000 | 0.000 | 0.991 | 8.82e+05 | 0.138 |
| E_hier_lineage | 0.000 <sub>[0.000, 0.000]</sub> | 0.002 <sub>[0.000, 0.006]</sub> | 0.002 | 0.000 | 0.004 | 1.000 | 0.002 | 0.000 | 0.000 | 0.984 | 9.90e+05 | 0.138 |
| C_recursive_sum | 0.000 <sub>[0.000, 0.000]</sub> | 0.000 <sub>[0.000, 0.000]</sub> | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.999 | 8.05e+05 | 0.092 |

### Paired comparisons

| comparison | metric | mean A | mean B | Δ | 95% CI | wins | sign p |
|---|---|---:|---:|---:|---|---:|---:|
| H_mycelic_full vs E_hier_lineage | found | 0.5120 | 0.0507 | +0.4613 | [+0.3887, +0.5383] | 15/15 | 0.000 |
| H_mycelic_full vs B2_map_reduce | found | 0.5120 | 0.1877 | +0.3243 | [+0.2937, +0.3580] | 15/15 | 0.000 |
| H_mycelic_full vs A_flat_rag | found | 0.5120 | 0.6273 | -0.1153 | [-0.1687, -0.0600] | 2/15 | 0.007 |
| H_mycelic_full vs B_long_context | found | 0.5120 | 0.4557 | +0.0563 | [-0.0143, +0.1290] | 6/13 | 1.000 |
| H_mycelic_full vs C_recursive_sum | found | 0.5120 | 0.0217 | +0.4903 | [+0.4070, +0.5797] | 15/15 | 0.000 |
| H_mycelic_full vs D_hier_nolineage | found | 0.5120 | 0.0413 | +0.4707 | [+0.3957, +0.5517] | 15/15 | 0.000 |
| H_mycelic_full vs B4_central_triage | found | 0.5791 | 0.5323 | +0.0468 | [+0.0095, +0.0818] | 7/10 | 0.344 |
| H_mycelic_full vs Y_oracle_retrieval | found | 0.5120 | 0.3850 | +0.1270 | [+0.0830, +0.1737] | 13/15 | 0.007 |
| B2_map_reduce vs Z_random_rank | found | 0.1877 | 0.1877 | +0.0000 | [+0.0000, +0.0000] | 0/0 | 1.000 |
| G_hier_questions vs F_hier_retrieval | found | 0.5117 | 0.0960 | +0.4157 | [+0.3673, +0.4697] | 15/15 | 0.000 |
| F_hier_retrieval vs E_hier_lineage | found | 0.0960 | 0.0507 | +0.0453 | [+0.0273, +0.0657] | 13/13 | 0.000 |
| H_mycelic_full vs E_hier_lineage | AP | 0.0534 | 0.0078 | +0.0456 | [+0.0245, +0.0726] | 15/15 | 0.000 |
| H_mycelic_full vs B2_map_reduce | AP | 0.0534 | 0.0530 | +0.0003 | [-0.0255, +0.0255] | 9/15 | 0.607 |
| H_mycelic_full vs A_flat_rag | AP | 0.0534 | 0.0947 | -0.0413 | [-0.0720, -0.0155] | 2/15 | 0.007 |
| H_mycelic_full vs B_long_context | AP | 0.0534 | 0.0806 | -0.0272 | [-0.0606, +0.0025] | 9/15 | 0.607 |
| H_mycelic_full vs C_recursive_sum | AP | 0.0534 | 0.0073 | +0.0461 | [+0.0272, +0.0685] | 15/15 | 0.000 |
| H_mycelic_full vs D_hier_nolineage | AP | 0.0534 | 0.0070 | +0.0464 | [+0.0237, +0.0757] | 15/15 | 0.000 |
| H_mycelic_full vs B4_central_triage | AP | 0.0684 | 0.0622 | +0.0062 | [-0.0249, +0.0388] | 6/11 | 1.000 |
| H_mycelic_full vs Y_oracle_retrieval | AP | 0.0534 | 0.0234 | +0.0300 | [+0.0119, +0.0543] | 14/15 | 0.001 |
| B2_map_reduce vs Z_random_rank | AP | 0.0530 | 0.0214 | +0.0316 | [+0.0128, +0.0526] | 10/14 | 0.180 |
| G_hier_questions vs F_hier_retrieval | AP | 0.0642 | 0.0280 | +0.0361 | [+0.0133, +0.0612] | 14/15 | 0.001 |
| F_hier_retrieval vs E_hier_lineage | AP | 0.0280 | 0.0078 | +0.0202 | [+0.0074, +0.0355] | 12/14 | 0.013 |

---

## 9. Ablations

_(E2 not run)_

---

## 10. Scale results

Discovery (`found`) by architecture and scale — every cell is an executed run,
none is extrapolated:

| architecture | 2,000 | 10,000 | 50,000 |
|---|---:|---:|---:|
| A_flat_rag | 0.820 | 0.660 | 0.402 |
| A2_chunked_ctx | 0.815 | 0.685 | 0.640 |
| B_long_context | 0.820 | 0.455 | 0.092 |
| B2_map_reduce | 0.430 | 0.115 | 0.018 |
| B4_central_triage | 0.755 | 0.340 | 0.380 |
| E_hier_lineage | 0.120 | 0.030 | 0.002 |
| G_hier_questions | 0.730 | 0.465 | 0.340 |
| H_mycelic_full | 0.770 | 0.440 | 0.326 |
| Y_oracle_retrieval | 0.675 | 0.230 | 0.250 |

Compute (cu) at the same points:

| architecture | 2,000 | 10,000 | 50,000 |
|---|---:|---:|---:|
| A_flat_rag | 4.70e+05 | 1.09e+06 | 1.24e+06 |
| A2_chunked_ctx | 5.11e+05 | 2.08e+06 | 1.02e+07 |
| B_long_context | 1.09e+06 | 1.09e+06 | 1.13e+06 |
| B2_map_reduce | 4.94e+04 | 1.64e+05 | 7.39e+05 |
| B4_central_triage | 1.31e+05 | 4.47e+05 | 1.69e+06 |
| E_hier_lineage | 6.80e+04 | 2.26e+05 | 9.90e+05 |
| G_hier_questions | 8.20e+05 | 1.08e+06 | 2.93e+06 |
| H_mycelic_full | 8.40e+05 | 1.07e+06 | 2.95e+06 |
| Y_oracle_retrieval | 1.43e+05 | 4.87e+05 | 2.43e+06 |

---

## 11. Cost analysis

### Compute allocation across levels

_(E3 not run)_

### Marginal value of capability at each level

Baseline: every level at `small-7b`. Then exactly one level is upgraded to
`frontier`, holding everything else fixed. This is the experiment that decides
where compute should go.

_(E3b not run)_

### Uniform capability sweep

The function from operator quality to architecture quality, which is the claim
this study can actually make.

_(E3c not run)_

### Cost / quality frontier

_(E6 not run)_

---

## 11b. Privacy and propagation volume

Three different things are usually collapsed into one "privacy" number. They
are separated here:

* **raw text out** — fraction of records whose original text was read by
  anything other than the owning user agent. This is the confidentiality cost.
* **claims out** — extracted claims (structured, no surface text) that left the
  user node.
* **index entries out** — sketch metadata (entity id, predicate bitmask,
  counts) that left the node. No claim content.

_(E9 not run)_

---

## 12. Latency analysis

Latency is a critical-path estimate (assumption A4) over the token counts
actually metered: stages in sequence, calls within a stage parallel up to a
per-tier fleet concurrency. Per-architecture wall-clock, p50 and p95 per-call
latency appear in the §8 tables.

---

## 13. Information-loss analysis

`information loss` = fraction of the hidden patterns' (predicate, entity)
claim types that do **not** survive to the kernel's working pool.
`evidence coverage` = fraction of patterns for which the kernel ever held two
or more of the pattern's links under the right entity. The gap between
evidence coverage and `found` is the discrimination loss; the gap between 1.0
and evidence coverage is the propagation/retrieval loss. Both appear in §8.

---

## 14-15. Strategic discovery and rare signals

`found`, AP, recall@K and rare-signal recall in §8; rare-signal behaviour under
the `rare_only` condition in §17.

---

## 16. Continual questioning

Question utility, information gain and cost appear in the ablation table (§9, `-questions`, `-question_targeting`) and in the cost/quality frontier (§11).

---

## 17. Downward retrieval and adversarial conditions

_(E5 not run)_

---

## 18. Organisational fan-in, and org tree vs semantic overlay

_(E4 not run)_

### Strict org tree vs org tree + semantic cross-links

_(E8 not run)_

---

## 19. Capability-shape sensitivity

If a conclusion survives only under one assumed shape for how hard
capabilities scale with model quality, it is not a conclusion.

_(E7 not run)_

---

## 20. Direct model measurement

#### Primitive operators (blind, 198 items)

| model | extraction | entity fidelity | causal check | temporal check | entity linking | independence | fitted q |
|---|---:|---:|---:|---:|---:|---:|---:|
| claude-haiku-4.5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.00 |
| claude-sonnet-5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.00 |
| claude-opus-5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.00 |

#### Candidate discrimination — evidence STATISTICS only (60 candidates from a real run, 15 genuine)

| ranker | AP | selection precision | selection recall | selection F1 | n selected |
|---|---:|---:|---:|---:|---:|
| random | 0.3001 | — | — | — | — |
| simulator's calibrated logistic (statistics) | 0.4531 | — | — | — | — |
| haiku | **0.4388** | 0.353 | 0.400 | 0.375 | 17 |
| opus | **0.4733** | 0.278 | 0.333 | 0.303 | 18 |
| sonnet | **0.4346** | 0.278 | 0.333 | 0.303 | 18 |

#### Candidate discrimination — statistics + the RAW WORK NOTES (60 candidates from a real run, 15 genuine)

| ranker | AP | selection precision | selection recall | selection F1 | n selected |
|---|---:|---:|---:|---:|---:|
| random | 0.3001 | — | — | — | — |
| simulator's calibrated logistic (statistics) | 0.4531 | — | — | — | — |
| haiku | **0.6381** | 0.471 | 0.533 | 0.500 | 17 |
| opus | **0.5959** | 0.429 | 0.400 | 0.414 | 14 |

---

## 21. Direct measurement vs simulation disclosure

| element | status |
|---|---|
| org chart, corpus, ground truth, decoys | **generated** — deterministic, seeded, reproducible |
| every architecture's control flow, routing, propagation, descent, questioning | **executed** — real code on the real corpus |
| token counts, inference-call counts, context sizes, compute units | **measured** from the executed runs |
| wall-clock latency | **modelled** from metered tokens (assumption A4), not observed |
| model-tier operator behaviour (extraction, abstraction, verification, hallucination) | **simulated** from the capability vector |
| primitive operator accuracy of Haiku 4.5 / Sonnet 5 / Opus 5 | **directly measured**, blind, 198 items |
| candidate-discrimination accuracy of those models | **directly measured**, blind, on candidates from a real run |
| placement of named open-weight model classes on the capability axis | **assumed** (A1) — never measured here |
| 50,000-user results | **executed**, not extrapolated — every 50k row is a real run of the full pipeline |

Nothing in this report is extrapolated from a smaller scale. Where a number is
modelled rather than observed, the row above says so.

---

## 22. Reviewer critique and known limitations

_(filled from results)_

---

## 23. Assumptions


Recorded as they were made, with the reason:

**A1 — model tiers are a capability axis, not checkpoints.** This environment
has no GPU and no local inference, so "Qwen2.5-7B" cannot be measured. The
independent variable is a scalar `q ∈ [0,1]` mapped to a capability vector
(extraction recall/precision, predicate confusion, entity fidelity, salience
noise, abstraction retention/distortion, synthesis depth, causal/temporal/
entity/dedup check accuracy, contradiction accuracy, hallucination rate,
question and routing quality, context window, throughput, fleet concurrency).
Named model classes are **labelled positions** on that axis. Every headline
claim is a claim about the function from operator quality to architecture
quality.

**A2 — the shape of "hard" capability scaling is swept, not assumed.** How
multi-step verification scales with `q` is the single most load-bearing
modelling choice in the study, so it is a parameter with three settings
(convex — small models disproportionately weak; linear; concave — saturating),
and the headline comparisons are re-run under all three. Any conclusion that
holds only under one shape is reported as shape-dependent.

**A3 — cost is reported in normalised compute units** (`active_params_B ×
(tokens_in + 4·tokens_out) / 1000`), which is transparent and
provider-independent. A dollar column is secondary and uses stated price
assumptions.

**A4 — latency is a critical-path model**: stages run in sequence, calls within
a stage run in parallel up to a per-tier fleet concurrency. It is an estimate
from the token counts actually metered, not a wall-clock measurement of a
deployed system.

**A5 — one record ≈ 24 tokens**, measured from the generator's own output.

**A6 — entity resolution errors are per (agent, entity), not per mention.**
An agent that mis-links a name mis-links it consistently. Modelling it per
mention made a 7-record facet survive with probability p⁷ and turned the study
into a referendum on extraction noise.

**A7 — a queried agent uses its own local index.** When the kernel descends and
asks a user agent about one entity, that agent is charged for reading the
matching records plus an index lookup, not for re-reading everything it has
ever seen. Sovereign local memory with a local index is the premise of the
design.


---

## 24. Failed approaches, and what they taught us


Each of these was implemented, measured, and abandoned or rebuilt on the
evidence. The full chronology is in `research/mycelic/logs/research_log.md`.

1. **Narrow propagation funnel.** The first budgets delivered 232 knowledge
   objects from 15,510 records and every hierarchical variant scored exactly
   zero. Lesson: the funnel must be sized from fan-in, and kernel context must
   be *equalised across architectures* or the comparison measures budgets
   rather than designs.

2. **One hypothesis per anchor entity.** Taking the top-`synth_depth`
   predicates by importance for each entity almost never selects the pattern's
   predicates. Rebuilt to enumerate one candidate per (entity, causal chain).

3. **Global novelty as the weak-signal detector.** At 10k users the
   (predicate, entity) space is sparse enough that nearly everything is
   "novel". Replaced with local novelty; then `max(site, team)` novelty was
   measured to *destroy* the ranking, because a nine-person team sees so few
   entities that everything looks locally novel.

4. **Record-level importance ranking, in any form.** The decisive measurement:
   **0 of 306 facet records appear in the global top-900 by record-level
   importance at 10k users.** A facet record is individually indistinguishable
   from benign cross-site chatter — that is the premise of the problem, not a
   bug. No per-record feature can work; detection has to be entity-level and
   relational. This result produced the two-channel design.

5. **Entity over-merging at extraction time.** Modelling entity linking error
   as a per-mention extraction failure destroyed entity locality in the index
   and with it the entire routing signal. Copying a string is easy; deciding
   two strings denote the same thing is hard, and that happens at synthesis.

6. **Shuffle-based routing.** The descent picked children at random instead of
   ranking them by index evidence.

7. **Globally-sorted frontier expansion.** A global top-N sort is captured by
   the entity's home branch, which holds most of its mentions and none of the
   interesting foreign evidence. Fixed with a per-parent quota first, global
   sort only to trim.

8. **Restricting descent to the triage's own sites.** This *halved* recovery
   (1/12 vs 7/12 patterns with ≥3 well-supported links), because the sketch
   only lists sites whose evidence already passed the support threshold and
   the thin facets are exactly the ones that did not. Triage sites became a
   frontier seed rather than a replacement.

9. **Discarding evidence between rounds.** A plain bug: the retrieval round
   built its merged pool locally and threw it away, so everything the descent
   had just paid to fetch was dropped before the questioning round.

10. **Longest-path temporal filtering.** Maximising path *length* picks up thin
    background links and drops well-evidenced ones. Replaced with a search
    that maximises evidence weight.

11. **Clipped linear confidence.** Most candidates pinned at the ceiling, so
    the ranking — which is what a finite report budget actually consumes —
    was arbitrary. Replaced with a calibrated logistic.

12. **A triage prior tuned on seed 0.** It improved AP monotonically on the
    first seed (0.027 → 0.098) and would have been adopted on that evidence.
    On held-out calibration seeds the best weight is **0.0**. It is frozen at
    zero and reported as a non-result. This is the clearest illustration of
    why the calibration protocol exists.

13. **A non-blind operator test set.** The first direct-measurement harness
    inlined the answer key beside every item, listed only 34 of the 50
    predicates as the vocabulary, conflated chain membership with chain
    direction, and made labels positionally predictable. All four defects were
    found *by the measurement run itself*; all three runs were discarded.
    A later run flagged that the key still sat in the served directory, and it
    was moved out.


---

## 25. Recommended next experiments

_(filled from results)_

---

## 26. Reproducing this

```sh
python3 -m research.mycelic.calibrate              # fit knobs on seeds 500-502
python3 -m research.mycelic.experiments e1         # baselines x scales x seeds
sh research/mycelic/run_suite.sh                   # ablations, allocation, fan-in,
                                                   # adversarial, frontier, shape, links
python3 -m research.mycelic.live_tasks             # build blind operator tasks
python3 -m research.mycelic.live_rank              # build discrimination tasks
python3 -m research.mycelic.score_live             # score operator measurements
python3 -m research.mycelic.live_rank score        # score discrimination measurements
python3 -m research.mycelic.report                 # regenerate this document
```

Raw per-run metrics: `research/mycelic/artifacts/*.jsonl` (one row per run,
never aggregated in place). Calibration: `artifacts/calibration.json`.
Live measurements: `artifacts/live_*.json`, answer keys held in
`research/mycelic/keys/` outside the served directory. Research log with every
failed design and the measurement that killed it:
`research/mycelic/logs/research_log.md`.

_Generated 2026-09-21 by `research/mycelic/report.py` from the
raw artifacts._

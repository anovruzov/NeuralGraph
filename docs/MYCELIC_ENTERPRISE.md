# Mycelic: hierarchical enterprise intelligence
## Benchmark report and architecture recommendation

**The question.** How should thousands of private, user-level AI agents
abstract, route, combine, question, verify and propagate what they know
upward, so that an enterprise-level system discovers strategic information
that no individual employee, team, department, site or region could discover
alone?

**What this document is.** A mechanism study on a synthetic enterprise with
known hidden ground truth. Fourteen architectures are compared under one
shared implementation of every cognitive operation, at matched kernel context,
with every tunable setting fitted on held-out data before the evaluation data
was touched. It is not a deployment report, and it does not measure any
specific model end to end. Part II states exactly what was measured on real
models and what was simulated.

**How to read it.** Part I is the decision. Part II is how to read the
numbers. Parts III to V are the benchmark, the results and the case against
them. Every table and figure is generated from raw per-run records; nothing is
transcribed by hand.

---

# PART I — THE DECISION

## 1. Decision summary

### The one-paragraph version

We built a synthetic 2,000-person enterprise with known hidden problems planted in it, and tested fourteen ways of finding those problems, from simply pouring the company's notes into one very large model, to a six-level hierarchy of agents mirroring the org chart. **The hierarchy is not the most accurate option.** A centralised approach that reads a filtered sample of the whole company in one pass finds more, for less compute. The hierarchy earns its place on three specific grounds - confidentiality, weak-signal sensitivity and defensible provenance - and if none of those three matter to us, we should not build it.

### What the numbers say, at full scale

| | best centralised option | the hierarchy |
|---|---:|---:|
| approach | `A2_chunked_ctx` | `H_mycelic_full` |
| hidden problems found | **80%** | 72% |
| of the *hardest* problems (1-2 witnesses company-wide) | 56% | **44%** |
| compute | 5.18e+05 | 8.19e+05 |
| employees' original notes read centrally | 25% | **0%** |

### Three decisions this supports

**1. Do not build progressive summarisation up the org chart.** This is the intuitive design - each layer summarises the layer below - and it is the clearest negative result in the study. It finds approximately nothing, at any scale, in any variant tried: with lineage, without lineage, with structured objects, with plain text. The reason is measurable rather than a matter of tuning, and is given in the executive summary below.

**2. If we build a hierarchy, the value is in the downward path, not the upward one.** Almost all of the hierarchy's performance comes from the enterprise layer asking targeted questions *downward* and pulling evidence back on demand, not from information flowing up. Budget accordingly: the upward channel should be cheap and statistical; the downward channel is where the work happens.

**3. Spend top-tier model budget on re-reading original evidence, not on bigger reasoning over summaries.** We measured this directly on three real models. Given the same summarised evidence, a large model and a small model perform the same, and no better than a six-feature statistical rule. Given the *original notes* behind that evidence, both improve sharply. Acting on this changed the design and was the single best return on compute we found.


---

## 2. Executive summary

**What actually wins, and where.**

| users | best architecture | discovery (`found`) | compute (cu) | raw records leaving their owner |
|---|---|---:|---:|---:|
| 2,000 | `A2_chunked_ctx` | 0.800 | 5.18e+05 | 25.3% |

**1. Upward propagation alone does not work, at any scale, in any form.** At 2,000 users recursive summarisation finds 7.5% of the hidden patterns, hierarchical aggregation without lineage and with it find 12.5% and 20.0%. Adding targeted downward retrieval takes it to 17.5%; adding sketch-driven questioning takes it to 65.0%. The hierarchy's value is almost entirely in the *downward* path — Δ +0.475 (95% CI [+0.475, +0.475], 1/1 seeds, sign p=1.000).

**2. The reason is measurable and is not a tuning artefact.** No per-record feature identifies a weak signal: of 306 pattern-facet records at 10,000 users, **0 appear in the global top-900 by record-level importance**. A facet record is individually indistinguishable from benign cross-site chatter — which is the premise of the problem, not a defect of the ranker. Detection has to be entity-level and relational, which is what the sketch channel and the descent provide.

**7. The honest bottom line.** If centralising the raw text is acceptable and a very large context window is available, a schema-aware retrieval pass into one frontier call is the strongest and cheapest thing measured here. The hierarchy earns its cost only where sovereign local memory is a requirement, where rare signals matter more than common ones, or where independent-support and provenance have to be defensible. Those are real constraints, but they are constraints — not an accuracy win.

_Statistical note: an exact sign test on *n* paired seeds cannot report below 2^-(n-1); with 5 seeds the floor is p = 0.0625 and with 10 it is p = 0.002._

---

## 3. Recommended architecture


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


### 3. Recommended hierarchy

Keep the six human levels — USER → TEAM → DEPARTMENT → SITE → REGION → ENTERPRISE — but do not make them all carry content. The recommended design splits the upward flow in two:

* **a complete, LLM-free sketch channel** (per entity, per site: mention count, a bitmask of operational predicates seen at least `min_support` times, and a time span), which is what makes discovery possible at all; and
* **a budgeted, evidence-bearing object channel** carrying lineage, evidence pointers, independent-support sketches, contradictions and confidence — most of which is deliberately dropped.

Discovery then happens by triaging the sketch channel for entities whose operational evidence repeats at sites where the entity does not belong, and descending on those entities specifically. The intermediate levels exist to aggregate the sketch and to bound the descent, not to summarise text upward.


---

# PART II — HOW TO READ THIS

## 4. Glossary


The report uses a small number of terms repeatedly. All of them are
measurements, not scores on a scale someone invented.

| term | what it means |
|---|---|
| **hidden pattern** | A genuine emerging problem planted in the synthetic company: a chain of causally linked events about one supplier, system or component, whose parts are deliberately scattered across different teams, sites and regions so that nobody sees more than one part. |
| **discovery (`found`)** | The fraction of hidden patterns the system puts on the executive risk register *anywhere*. The plainest measure of "did we find it at all". |
| **AP** (average precision) | How well the system *ranks* what it found. A register nobody can read top to bottom is worth less than one where the real items are at the top. Random ranking of the same candidates scores about 0.30 on the discrimination task. |
| **evidence coverage** | The fraction of hidden patterns for which the system ever *held* the evidence, whether or not it reported them. The gap between coverage and discovery is what is lost to judgement rather than to retrieval. |
| **rare-signal recall** | Discovery restricted to the hardest patterns: those supported by one or two people in the entire 50,000-person company. |
| **FDR** (false discovery rate) | The fraction of what the system reports that is not a real pattern. |
| **decoy acceptance** | The fraction of the deliberately planted traps the system falls for. |
| **compute (cu)** | Normalised compute units: model size times tokens. Provider-independent; a dollar estimate is given separately. |
| **raw-text exposure** | The fraction of employees' original notes read by anything other than the agent that owns them. The confidentiality cost. |


## 5. What was measured, what was simulated

This distinction matters more than any single number in the report, so it is
stated before the results rather than after them.

| element | status |
|---|---|
| org chart, corpus, hidden patterns, decoys | **generated** — deterministic, seeded, reproducible from the committed code |
| every architecture's control flow: routing, propagation, descent, questioning, verification | **executed** — real code on the real corpus, at every scale reported |
| token counts, inference-call counts, context sizes, compute units | **measured** from those executed runs |
| wall-clock latency | **modelled** from the metered token counts (see assumption A4); not observed on real hardware |
| model-tier behaviour: extraction quality, abstraction loss, verification accuracy, hallucination | **simulated** from a capability vector |
| primitive operator accuracy of Haiku 4.5, Sonnet 5 and Opus 5 | **directly measured**, blind, 198 items |
| candidate-discrimination accuracy of those three models | **directly measured**, blind, on candidates taken from a real run |
| where named open-weight model classes sit on the capability axis | **assumed** — never measured here |
| 50,000-user results | **executed**, not extrapolated: every cell is a real run of the full pipeline |

Nothing in this report is extrapolated from a smaller scale. Where a number is
modelled rather than observed, the row above says so.

---

# PART III — THE BENCHMARK

## 6. Methodology


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


## 7. The synthetic enterprise at each scale

| users | records | teams | depts | sites | regions | users/team (p10–p90) | teams/dept | depts/site | patterns | rare | decoys | entities |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1,999 | 68,463 | 213 | 31 | 9 | 7 | 6–14 (med 9) | 3–10 | 1–8 | 40 | 9 | 50 | 150 |
| 9,999 | 329,730 | 1,074 | 149 | 15 | 7 | 6–13 (med 9) | 4–11 | 1–39 | 40 | 17 | 50 | 599 |
| 49,999 | 1,639,365 | 5,335 | 736 | 93 | 7 | 6–14 (med 9) | 4–12 | 1–58 | 100 | 45 | 125 | 2,999 |

## 8. Calibration

Every tunable setting was fitted on calibration seeds disjoint from the
evaluation seeds, by the same procedure for every architecture, and then
frozen. This is not a formality: it rejected two changes that looked like
large improvements on a single evaluation seed and did not generalise.

Fitted on calibration seeds [500, 501, 502] at 10,000 users, objective `average_precision`, then frozen:

| knob | chosen |
|---|---:|
| `triage_prior_weight` | 0.0 |
| `question_frac` | 0.25 |
| `mr_budget` | 2500 |
| `ct_kernel_ko_cap` | 20000 |
| `flat_budget` | 1000000 |

**hierarchy grid**

| triage_prior_weight | question_frac | AP | found | compute |
|---|---|---|---|---|
| 0.0 | 0.25 | 0.02825 | 0.375 | 1.132e+06 |
| 0.0 | 0.55 | 0.02232 | 0.317 | 1.504e+06 |
| 0.0 | 1.0 | 0.01888 | 0.358 | 2.227e+06 |
| 1.2 | 0.25 | 0.02750 | 0.375 | 1.132e+06 |
| 1.2 | 0.55 | 0.02441 | 0.317 | 1.504e+06 |
| 1.2 | 1.0 | 0.02692 | 0.358 | 2.227e+06 |
| 2.0 | 0.25 | 0.02707 | 0.375 | 1.132e+06 |
| 2.0 | 0.55 | 0.02448 | 0.317 | 1.504e+06 |
| 2.0 | 1.0 | 0.02612 | 0.358 | 2.227e+06 |
| 3.0 | 0.25 | 0.02605 | 0.375 | 1.132e+06 |
| 3.0 | 0.55 | 0.02377 | 0.317 | 1.504e+06 |
| 3.0 | 1.0 | 0.02586 | 0.358 | 2.227e+06 |
| 4.0 | 0.25 | 0.02533 | 0.375 | 1.132e+06 |
| 4.0 | 0.55 | 0.02300 | 0.317 | 1.504e+06 |
| 4.0 | 1.0 | 0.02552 | 0.358 | 2.227e+06 |

**map-reduce grid**

| kernel_ko_budget | AP | found | compute |
|---|---|---|---|
| 300 | 0.01806 | 0.033 | 1.498e+05 |
| 900 | 0.02316 | 0.108 | 1.632e+05 |
| 2500 | 0.04387 | 0.292 | 1.990e+05 |
| 8000 | 0.01290 | 0.350 | 2.656e+05 |

**central triage grid**

| kernel_ko_cap | AP | found | compute |
|---|---|---|---|
| 0 | 0.03240 | 0.425 | 4.329e+05 |
| 2000 | 0.01772 | 0.067 | 1.787e+05 |
| 6000 | 0.03454 | 0.350 | 2.386e+05 |
| 20000 | 0.03462 | 0.408 | 3.972e+05 |
| 60000 | 0.03240 | 0.425 | 4.329e+05 |

**flat RAG grid**

| token_budget | AP | found | compute |
|---|---|---|---|
| 60000 | 0.00520 | 0.125 | 8.369e+04 |
| 120000 | 0.01918 | 0.258 | 1.646e+05 |
| 400000 | 0.04813 | 0.642 | 4.584e+05 |
| 1000000 | 0.11894 | 0.717 | 1.089e+06 |

---

# PART IV — RESULTS

## 9. Architectures compared

| id | architecture |
|---|---|
| `A_flat_rag` | cheap lexical, schema-aware retrieval over raw text, into one kernel call |
| `A2_chunked_ctx` | frontier context tiled over the **entire** corpus — the "just use more context" answer |
| `B_long_context` | an unfiltered raw-record sample filling the kernel's context |
| `B2_map_reduce` | cheap extraction over every record, global importance ranking, one strong kernel call |
| `B4_central_triage` | **control**: the hierarchy's own triage algorithm run centrally, with no propagation budget, no routing error and no descent |
| `C_recursive_sum` | recursive summarisation up the org tree: no structure, no lineage |
| `D_hier_nolineage` | hierarchical semantic aggregation **without** lineage or independence tracking |
| `E_hier_lineage` | lineage-aware hierarchical aggregation — pure upward flow |
| `F_hier_retrieval` | E + targeted downward retrieval |
| `G_hier_questions` | F + continual questioning |
| `H_mycelic_full` | G + semantic cross-links |
| `I_mycelic_completion` | H + hypothesis-driven chain completion |
| `J_mycelic_verified` | H + kernel-side evidence verification |
| `Y_oracle_retrieval` | **reference**: every extracted claim, no budget at all. Not deployable. |
| `Z_random_rank` | **control**: map-reduce's candidates, ranked at random |
| `Z2_naive_enumerate` | **control**: enumerate every entity's best chain, no verification at all |

## 10. Baseline comparison


#### 2,000 users (68,463 records, 40.0 hidden patterns, 1 seeds)

| architecture | AP | found | R@100 | rare recall | evidence cov. | FDR | decoy acc. | indep. acc. | lineage | info loss | compute (cu) | privacy exp. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_flat_rag | 0.261 <sub>[0.261, 0.261]</sub> | 0.800 <sub>[0.800, 0.800]</sub> | 0.525 | 0.556 | 1.000 | 0.903 | 0.450 | 0.786 | 0.664 | 0.006 | 4.75e+05 | 0.253 |
| B_long_context | 0.216 <sub>[0.216, 0.216]</sub> | 0.800 <sub>[0.800, 0.800]</sub> | 0.475 | 0.222 | 0.975 | 0.895 | 0.475 | 0.814 | 0.661 | 0.044 | 1.09e+06 | 0.609 |
| A2_chunked_ctx | 0.190 <sub>[0.190, 0.190]</sub> | 0.800 <sub>[0.800, 0.800]</sub> | 0.450 | 0.556 | 1.000 | 0.910 | 0.450 | 0.778 | 0.630 | 0.013 | 5.18e+05 | 0.253 |
| H_mycelic_full | 0.082 <sub>[0.082, 0.082]</sub> | 0.725 <sub>[0.725, 0.725]</sub> | 0.225 | 0.444 | 0.950 | 0.951 | 0.375 | 0.747 | 0.542 | 0.101 | 8.19e+05 | 0.141 |
| B4_central_triage | 0.071 <sub>[0.071, 0.071]</sub> | 0.775 <sub>[0.775, 0.775]</sub> | 0.275 | 0.556 | 0.950 | 0.948 | 0.450 | 0.746 | 0.519 | 0.070 | 1.35e+05 | 0.883 |
| I_mycelic_completion | 0.069 <sub>[0.069, 0.069]</sub> | 0.650 <sub>[0.650, 0.650]</sub> | 0.225 | 0.333 | 0.900 | 0.956 | 0.375 | 0.783 | 0.525 | 0.146 | 1.41e+06 | 0.141 |
| G_hier_questions | 0.068 <sub>[0.068, 0.068]</sub> | 0.650 <sub>[0.650, 0.650]</sub> | 0.275 | 0.444 | 0.950 | 0.956 | 0.275 | 0.757 | 0.542 | 0.095 | 8.20e+05 | 0.132 |
| B2_map_reduce | 0.049 <sub>[0.049, 0.049]</sub> | 0.600 <sub>[0.600, 0.600]</sub> | 0.200 | 0.111 | 0.800 | 0.933 | 0.375 | 0.745 | 0.496 | 0.278 | 80288 | 0.883 |
| D_hier_nolineage | 0.033 <sub>[0.033, 0.033]</sub> | 0.125 <sub>[0.125, 0.125]</sub> | 0.125 | 0.000 | 0.175 | 1.000 | 0.000 | 0.000 | 0.000 | 0.829 | 54058 | 0.132 |
| C_recursive_sum | 0.028 <sub>[0.028, 0.028]</sub> | 0.075 <sub>[0.075, 0.075]</sub> | 0.075 | 0.000 | 0.100 | 1.000 | 0.000 | 0.000 | 0.000 | 0.911 | 41729 | 0.088 |
| E_hier_lineage | 0.026 <sub>[0.026, 0.026]</sub> | 0.200 <sub>[0.200, 0.200]</sub> | 0.200 | 0.111 | 0.250 | 0.914 | 0.025 | 0.587 | 0.692 | 0.797 | 67781 | 0.132 |
| F_hier_retrieval | 0.010 <sub>[0.010, 0.010]</sub> | 0.175 <sub>[0.175, 0.175]</sub> | 0.150 | 0.111 | 0.275 | 0.958 | 0.075 | 0.740 | 0.567 | 0.728 | 2.08e+05 | 0.132 |

### Paired comparisons

Every comparison is paired on (scale, seed) — the seed controls both the org
chart and the corpus, so unpaired tests would be swamped by between-world
variance.

| comparison | metric | mean A | mean B | Δ | 95% CI | wins | sign p |
|---|---|---:|---:|---:|---|---:|---:|
| H_mycelic_full vs E_hier_lineage | found | 0.7250 | 0.2000 | +0.5250 | [+0.5250, +0.5250] | 1/1 | 1.000 |
| H_mycelic_full vs B2_map_reduce | found | 0.7250 | 0.6000 | +0.1250 | [+0.1250, +0.1250] | 1/1 | 1.000 |
| H_mycelic_full vs A_flat_rag | found | 0.7250 | 0.8000 | -0.0750 | [-0.0750, -0.0750] | 0/1 | 1.000 |
| H_mycelic_full vs B_long_context | found | 0.7250 | 0.8000 | -0.0750 | [-0.0750, -0.0750] | 0/1 | 1.000 |
| H_mycelic_full vs C_recursive_sum | found | 0.7250 | 0.0750 | +0.6500 | [+0.6500, +0.6500] | 1/1 | 1.000 |
| H_mycelic_full vs D_hier_nolineage | found | 0.7250 | 0.1250 | +0.6000 | [+0.6000, +0.6000] | 1/1 | 1.000 |
| H_mycelic_full vs B4_central_triage | found | 0.7250 | 0.7750 | -0.0500 | [-0.0500, -0.0500] | 0/1 | 1.000 |
| G_hier_questions vs F_hier_retrieval | found | 0.6500 | 0.1750 | +0.4750 | [+0.4750, +0.4750] | 1/1 | 1.000 |
| F_hier_retrieval vs E_hier_lineage | found | 0.1750 | 0.2000 | -0.0250 | [-0.0250, -0.0250] | 0/1 | 1.000 |
| H_mycelic_full vs E_hier_lineage | AP | 0.0817 | 0.0260 | +0.0557 | [+0.0557, +0.0557] | 1/1 | 1.000 |
| H_mycelic_full vs B2_map_reduce | AP | 0.0817 | 0.0493 | +0.0324 | [+0.0324, +0.0324] | 1/1 | 1.000 |
| H_mycelic_full vs A_flat_rag | AP | 0.0817 | 0.2605 | -0.1788 | [-0.1788, -0.1788] | 0/1 | 1.000 |
| H_mycelic_full vs B_long_context | AP | 0.0817 | 0.2162 | -0.1345 | [-0.1345, -0.1345] | 0/1 | 1.000 |
| H_mycelic_full vs C_recursive_sum | AP | 0.0817 | 0.0283 | +0.0534 | [+0.0534, +0.0534] | 1/1 | 1.000 |
| H_mycelic_full vs D_hier_nolineage | AP | 0.0817 | 0.0333 | +0.0484 | [+0.0484, +0.0484] | 1/1 | 1.000 |
| H_mycelic_full vs B4_central_triage | AP | 0.0817 | 0.0714 | +0.0103 | [+0.0103, +0.0103] | 1/1 | 1.000 |
| G_hier_questions vs F_hier_retrieval | AP | 0.0680 | 0.0102 | +0.0578 | [+0.0578, +0.0578] | 1/1 | 1.000 |
| F_hier_retrieval vs E_hier_lineage | AP | 0.0102 | 0.0260 | -0.0158 | [-0.0158, -0.0158] | 0/1 | 1.000 |

## 11. Scale

![discovery and evidence coverage vs enterprise size](../research/mycelic/artifacts/figures/scale.png)

| architecture | 2,000 |
|---|---:|
| A_flat_rag | 0.800 |
| A2_chunked_ctx | 0.800 |
| B_long_context | 0.800 |
| B2_map_reduce | 0.600 |
| B4_central_triage | 0.775 |
| E_hier_lineage | 0.200 |
| G_hier_questions | 0.650 |
| H_mycelic_full | 0.725 |

Compute (cu) at the same points:

| architecture | 2,000 |
|---|---:|
| A_flat_rag | 4.75e+05 |
| A2_chunked_ctx | 5.18e+05 |
| B_long_context | 1.09e+06 |
| B2_map_reduce | 8.03e+04 |
| B4_central_triage | 1.35e+05 |
| E_hier_lineage | 6.78e+04 |
| G_hier_questions | 8.20e+05 |
| H_mycelic_full | 8.19e+05 |

## 12. Ablations

![ablations](../research/mycelic/artifacts/figures/ablations.png)

| variant | AP | found | rare recall | evidence cov. | FDR | indep. acc. | lineage | contra F1 | D4 support infl. | Q utility | compute (cu) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| -lineage | 0.051 | 0.450 <sub>[0.450, 0.450]</sub> | 0.118 | 0.600 | 0.947 | 0.753 | 0.000 | 0.250 | 2.842 | 0.795 | 1.00e+06 |
| full | 0.008 | 0.300 <sub>[0.300, 0.300]</sub> | 0.118 | 0.625 | 0.980 | 0.743 | 0.534 | 0.333 | 2.781 | 0.886 | 1.04e+06 |

**Paired against the full system** (same seeds):

| removed | Δ found | 95% CI | wins | sign p | Δ AP | Δ compute |
|---|---:|---|---:|---:|---:|---:|
| -lineage | +0.1500 | [+0.1500, +0.1500] | 0/1 | 1.000 | +0.0436 | -4.167e+04 |

## 13. Where compute should go

![marginal value of capability per level](../research/mycelic/artifacts/figures/level_marginal.png)

### Marginal value of capability at each level

Baseline: every level running the same small model. Then exactly one level is
upgraded to a frontier model, holding everything else fixed. This is the
experiment that decides where model budget should go.

_(E3b not run)_

### Allocation across levels

_(E3 not run)_

### Uniform capability sweep

![capability sweep](../research/mycelic/artifacts/figures/q_sweep.png)

The function from operator quality to architecture quality — which is the
claim this study can actually make, since named model classes are assumed
positions on this axis rather than measured ones.

_(E3c not run)_

## 14. Cost and quality

![cost vs quality](../research/mycelic/artifacts/figures/cost_quality.png)

#### Hierarchy: question / descent budget

| question budget frac | AP | found | rare | compute | cu/disc |
|---|---:|---:|---:|---:|---:|
| 0.00 | 0.0022 | 0.050 | 0.059 | 3.65e+05 |  |
| 0.05 | 0.0077 | 0.300 | 0.118 | 1.04e+06 |  |

## 15. Latency

Latency is a critical-path estimate over the token counts actually metered:
stages run in sequence, calls within a stage run in parallel up to a per-tier
fleet concurrency. Per-architecture wall-clock and per-call p50/p95 appear in
the §10 tables. The operationally important difference is not the estimate but
the **call count**: the centralised designs make one to a few dozen model
calls; the hierarchy makes tens of thousands. That is a real deployment
difference in scheduling, retry and failure-domain terms, independent of how
accurate the latency model is.

## 16. Information loss

`information loss` is the fraction of the hidden patterns' claim types that do
not survive to the kernel's working pool. `evidence coverage` is the fraction
of patterns for which the kernel ever held two or more of the pattern's links.
The gap between evidence coverage and discovery is loss to **judgement**; the
gap between 1.0 and evidence coverage is loss to **retrieval**. Both are in
§10, and the distinction is the single most useful diagnostic in this study.

## 17. Continual questioning

The full system asks 220 questions per run. 89% of them change a conclusion — a question counts as useful only if the kernel's hypothesis set or its confidence actually moved, never merely because text was produced. Each returns 98 new knowledge objects on average, and 100% are well-targeted.


## 18. Downward retrieval, and adversarial conditions

### Downward retrieval

The downward path is the largest single contributor to the hierarchy's
performance, and it works in two separable stages:

* **targeted descent** (`E` to `F`) — the kernel forms a hypothesis and routes
  a query down the index to the branches holding the missing evidence.
  Expansion is best-first with a per-parent quota, the entity's home branch is
  deliberately down-weighted (the retrieval budget should be spent where the
  entity does *not* belong), and the reached user agents re-read their own raw
  memory for that entity only. A descent touches a few dozen of roughly 57,000
  nodes and its leaf count is capped, so it does not degenerate into a
  broadcast as the enterprise grows — asserted by a test, not by construction.
* **sketch-driven questioning** (`F` to `G`) — the kernel sweeps the complete
  entity sketch for entities whose operational evidence repeats at sites where
  they do not belong, and descends on those. Finding these costs no model
  tokens at all, and this is where most of the discovery comes from.

### Adversarial conditions

| condition | architecture | AP | found | FDR | decoy acc | D1 | D2 | D3 | D5 | D4 infl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| clean | B2_map_reduce | 0.0709 | 0.275 | 0.974 | 0.325 | 0.200 | 0.300 | 0.000 | 0.800 | 2.22 |
| clean | B_long_context | 0.0274 | 0.400 | 0.946 | 0.150 | 0.000 | 0.200 | 0.100 | 0.300 | 1.45 |
| clean | E_hier_lineage | 0.0125 | 0.025 | 0.983 | 0.025 | 0.000 | 0.000 | 0.100 | 0.000 | 1.00 |
| clean | H_mycelic_full | 0.0077 | 0.300 | 0.980 | 0.200 | 0.100 | 0.300 | 0.100 | 0.300 | 2.78 |

## 19. Organisational shape

### Fan-in

_(E4 not run)_

### Strict org tree versus org tree plus semantic cross-links

_(E8 not run)_

## 20. Confidentiality and propagation volume

Three different things are usually collapsed into one "privacy" number. They
are separated here:

* **raw text out** — the fraction of records whose original text was read by
  anything other than the owning user agent. This is the confidentiality cost.
* **claims out** — extracted claims (structured, no surface text) that left
  the user node.
* **index entries out** — sketch metadata (entity id, predicate bitmask,
  counts) that left the node. No claim content.

![discovery vs confidentiality cost](../research/mycelic/artifacts/figures/privacy.png)

_(E9 not run)_

## 21. Provenance integrity

Does a report's cited evidence actually name the entity the report is about?
This was added after a measurement run flagged textbook causal chains with
perfectly healthy statistics whose underlying notes named a *different*
entity — the signature of a small edge model mis-linking a mention, which the
aggregates then preserve flawlessly while pointing at the wrong thing. It is
invisible to every other metric here.

_(E12 not run)_

## 22. Capability-shape sensitivity

A conclusion that survives only under one assumed shape for how hard
capabilities scale with model quality is not a conclusion. All three shapes
are run.

_(E7 not run)_

## 23. Direct model measurement

![candidate discrimination, measured](../research/mycelic/artifacts/figures/discrimination.png)

#### Primitive operators (blind, 198 items)

| model | extraction | entity fidelity | causal check | temporal check | entity linking | independence | fitted q |
|---|---:|---:|---:|---:|---:|---:|---:|
| claude-haiku-4.5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.00 |
| claude-sonnet-5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.00 |
| claude-opus-5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.00 |

---

# PART V — THE CASE AGAINST THESE RESULTS

## 24. Reviewer critique

The reviewer questions, answered against the measurements rather than around them.

**Is the benchmark fair?** Every architecture calls one shared implementation of every operator; the centralised baselines get a frontier-tier extractor on raw text while the hierarchy's edge runs a small model, which is a real advantage for them and is left in place; kernel context is equalised; every knob is fitted on calibration seeds disjoint from the evaluation seeds, by the same procedure for every system, including the centralised triage control's evidence budget (whose best held-out value turned out to be *no cap*, so the hierarchy's margin over it is not an artefact of denying it a filtering step).

**Did we design data that favours the hierarchy?** The opposite is closer to true. A pattern is equally visible to any system that gets its facet records into one context, and the headline result is that a centralised retrieval baseline does so more effectively than the hierarchy at every scale measured. Three generator properties were specifically added to remove hierarchy-favouring artefacts: benign cross-site entity traffic (so multi-region presence is a weak signal rather than a giveaway — index triage alone yields ~4% precision at 10k users), echoes that repeat the original wording (so duplicate detection is a text problem everyone faces), and site-local echo propagation (global echoing smeared every entity across every region and destroyed the locality structure).

**Are the improvements real?** Two candidate improvements that looked large on a single seed were rejected by the held-out protocol: a triage prior (AP 0.027 → 0.098 on seed 0, best held-out weight 0.0) and a source-dispersion feature (AP 0.019 → 0.068 on seed 0, best held-out weight 0.0). Both are reported as non-results. The one that survived, kernel-side evidence verification, did so on held-out seeds.

**Is the statistics adequate?** No, not fully. an exact sign test on *n* paired seeds cannot report below 2^-(n-1); with 5 seeds the floor is p = 0.0625 and with 10 it is p = 0.002. Headline comparisons at 2k and 10k use ten seeds; 50k and 100k use five and three, so several 50k differences are directionally unanimous but cannot be given a p below 0.0625–0.25. Those are labelled in the tables rather than described as significant.

**The biggest unresolved weakness.** The causal predicate schema is given to every system. Real enterprises have no such schema, and inducing one is plausibly harder than using it. Everything here is therefore an upper bound on the *verification* half of the problem and says nothing about schema induction.

**The experiment most likely to falsify the conclusion.** Re-run with the causal schema withheld and required to be induced from the corpus. If the hierarchy's sketch channel can induce a usable schema cheaply from aggregate co-occurrence while a centralised sample cannot, the ordering reported here inverts. That experiment was not run.


## 25. Assumptions


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


## 26. Failed approaches, and what they taught us


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


## 27. Recommended next experiments


In rough order of expected information per unit compute:

1. **Withhold the causal schema.** The single largest unexamined assumption.
   Require each architecture to induce the predicate chains from the corpus.
   This is the experiment most likely to change the ordering.
2. **Richer propagated objects, guided by the live measurement.** Models given
   the raw notes gained 0.15-0.20 AP over the same statistics and named three
   specific cues. Source dispersion and synchrony were implemented and did not
   generalise; kernel-side re-reading did. The remaining move is to propagate a
   small, structured *sample* of verbatim evidence with each object and
   measure whether that closes more of the gap than re-reading does.
3. **Multiple pattern shapes.** Every hidden pattern here is a causal chain on
   one entity. Patterns that are conjunctions across entities, rate changes, or
   absences of expected events would test whether the sketch channel's
   entity-keyed design generalises or is fitted to this shape.
4. **Adversaries that adapt.** The malicious-node condition here is static.
   A node that knows the triage rule can manufacture foreign operational
   mentions to flood the candidate list; measuring that cost is a prerequisite
   for deployment.
5. **A real model in the loop at one level.** Replace the simulated kernel with
   an actual frontier call on a 10k-user run end to end, and compare against
   the simulated kernel on the same candidates. The discrimination measurement
   did this for one operator; doing it end to end would convert the largest
   remaining simulated component into a measured one.
6. **Incremental operation.** Everything here is a single batch over a
   180-day window. A standing system re-runs continuously, and the interesting
   questions — when does a pattern become detectable, how much does keeping
   state save — are invisible to a batch benchmark.


## 28. Reproducing this

```sh
python3 -m unittest research.mycelic.test_mycelic     # 27 tests
python3 -m research.mycelic.calibrate                 # fit on held-out seeds
python3 -m research.mycelic.calibrate ct
python3 -m research.mycelic.calibrate evidence
python3 -m research.mycelic.experiments e1            # baselines x scales x seeds
sh research/mycelic/run_suite.sh                      # everything else
python3 -m research.mycelic.plots                     # figures
python3 -m research.mycelic.report                    # regenerate this document
```

Raw per-run metrics live in `research/mycelic/artifacts/*.jsonl`, one JSON
object per run, never aggregated in place. Calibration settings are in
`artifacts/calibration.json`. Live measurements are in `artifacts/live_*.json`
with answer keys held in `research/mycelic/keys/`, outside the directory the
measured models were pointed at. The chronological research log — including
every design that was measured and discarded, and the measurement that killed
it — is in `research/mycelic/logs/research_log.md`.

_Generated 2026-09-21 by `research/mycelic/report.py` from the
raw artifacts._

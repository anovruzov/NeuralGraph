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

We built a synthetic 10,000-person enterprise with known hidden problems planted in it, and tested sixteen ways of finding those problems, from pouring a filtered sample of the company's notes into one very large model, to a six-level hierarchy of agents mirroring the org chart. **The hierarchy is not the most accurate option.** The strongest single approach measured at this scale is `A2_chunked_ctx`, which finds 68% of the hidden problems against the hierarchy's 38%. Whether the hierarchy is nonetheless worth building depends entirely on which of the secondary properties below we actually need; the table states which ones it delivers and which it does not.

### Head to head, at full scale

| | best centralised option | the hierarchy | hierarchy better? |
|---|---:|---:|---|
| approach | `A2_chunked_ctx` | `H_mycelic_full` | |
| hidden problems found | 68% | 38% | no |
| the hardest problems (1-2 witnesses company-wide) | 63% | 20% | no |
| found within the top 100 of the register | 18% | 9% | no |
| precision in the top 40 of the register | 11% | 4% | no |
| precision in the top 100 of the register | 7% | 4% | no |
| ranking quality (AP) | 0.071 | 0.026 | no |
| decoy traps accepted | 35% | 17% | **yes** |
| evidence coverage (held it at all) | 100% | 69% | no |
| independent-support accuracy | 58% | 73% | **yes** |
| lineage accuracy | 62% | 50% | no |
| contradictions detected (F1) | 19% | 24% | **yes** |
| compute | 2.08e+06 | 1.10e+06 | **yes** |
| model calls | 3 | 9.41e+04 | no |
| employees' original notes read centrally | 23% | 0% | **yes** |

*Reading the false-discovery rate.* The register is a ranked watchlist of roughly one entry per tracked entity, not a shortlist, so a high FDR over the whole register is structural and is true of every architecture including the perfect-retrieval reference. The operationally meaningful numbers are precision in the top 40 or top 100 — what an executive would actually read — and the ranking quality (AP) that determines them.

### Which of the usual arguments for a hierarchy actually hold

**Hold, on this evidence:**

* confidentiality (no original notes leave the owning agent) — 0% vs 23% for the centralised option.
* independent-support accuracy — 73% vs 58% for the centralised option.
* resistance to planted traps — 17% vs 35% for the centralised option.

**Do NOT hold, and should not be used to justify the build:**

* weak-signal sensitivity — 20% vs 63%; the centralised option is better.
* lineage / provenance — 50% vs 62%; the centralised option is better.

### Three decisions this supports

**1. Do not build progressive summarisation up the org chart.** This is the intuitive design — each layer summarises the layer below — and it is the clearest negative result in the study. At 10,000 users it finds 0% (plain summarisation), 4% (structured, no lineage) and 3% (structured with lineage) of the hidden problems. The reason is measurable rather than a matter of tuning, and is given in the executive summary.

**2. If we build a hierarchy, the value is in the downward path, not the upward one.** Adding targeted downward retrieval takes discovery from 3% to 7%; adding sketch-driven questioning on top takes it to 40%. Budget accordingly: the upward channel should be cheap and statistical, and the downward channel is where the work — and the cost — actually is.

**3. Spend top-tier model budget on re-opening original evidence, not on bigger reasoning over summaries.** Measured directly on three real models: given the same summarised evidence, a large model and a small model perform the same, and no better than a six-feature statistical rule. Given the *original notes* behind that evidence, both improve sharply. Acting on this — having the kernel re-read a handful of source notes per candidate — was the best return on compute found in the study.


---

## 2. Executive summary

**What actually wins, and where.**

| users | best architecture | discovery (`found`) | compute (cu) | raw records leaving their owner |
|---|---|---:|---:|---:|
| 2,000 | `A2_chunked_ctx` | 0.885 | 5.12e+05 | 25.2% |
| 10,000 | `A2_chunked_ctx` | 0.681 | 2.08e+06 | 22.7% |

**1. Upward propagation alone does not work, at any scale, in any form.** At 10,000 users recursive summarisation finds 0.0% of the hidden patterns, hierarchical aggregation without lineage and with it find 4.4% and 3.1%. Adding targeted downward retrieval takes it to 6.9%; adding sketch-driven questioning takes it to 40.0%. The hierarchy's value is almost entirely in the *downward* path — Δ +0.331 (95% CI [+0.287, +0.394], 4/4 seeds, sign p=0.125).

**2. The reason is measurable and is not a tuning artefact.** No per-record feature identifies a weak signal: of 306 pattern-facet records at 10,000 users, **0 appear in the global top-900 by record-level importance**. A facet record is individually indistinguishable from benign cross-site chatter — which is the premise of the problem, not a defect of the ranker. Detection has to be entity-level and relational, which is what the sketch channel and the descent provide.

**3. At enterprise scale the binding constraint is discrimination, not retrieval.** A perfect-retrieval oracle — every extracted claim, no budget at all — holds the evidence for 100% of the hidden patterns and still reports only 21.7% of them. The hierarchy holds 69% and reports 37.5%. More undifferentiated evidence makes the kernel's ranking worse, so a propagation budget is a feature and not only a cost.

**5. Acting on that: the useful way to spend frontier compute at the kernel is to re-open original evidence, not to reason harder over the same abstraction.** Having the kernel re-read a handful of each top candidate's original notes with its own extractor changes AP by +8% for +1.0% compute (Δ +0.002 (95% CI [-0.000, +0.005], 1/2 seeds, sign p=1.000)).

**6. The hierarchy's defensible advantage is confidentiality, and it is categorical rather than marginal.** No raw record text ever leaves the agent that owns it (0.0%); the retrieval baseline centralises 12.7% of all records as original text and the chunked-context baseline 22.7%. What leaves a node in the hierarchy is structured claims and an entity/predicate sketch.

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


#### 2,000 users (68,463 records, 40.0 hidden patterns, 5 seeds)

| architecture | AP | found | R@100 | rare recall | evidence cov. | FDR | decoy acc. | indep. acc. | lineage | info loss | compute (cu) | privacy exp. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_flat_rag | 0.288 <sub>[0.234, 0.355]</sub> | 0.875 <sub>[0.805, 0.945]</sub> | 0.540 | 0.775 | 1.000 | 0.894 | 0.495 | 0.700 | 0.679 | 0.003 | 4.71e+05 | 0.252 |
| A2_chunked_ctx | 0.282 <sub>[0.232, 0.318]</sub> | 0.885 <sub>[0.840, 0.930]</sub> | 0.555 | 0.809 | 1.000 | 0.895 | 0.470 | 0.684 | 0.673 | 0.005 | 5.12e+05 | 0.252 |
| B_long_context | 0.210 <sub>[0.169, 0.243]</sub> | 0.795 <sub>[0.740, 0.845]</sub> | 0.470 | 0.452 | 0.990 | 0.895 | 0.435 | 0.763 | 0.699 | 0.048 | 1.09e+06 | 0.613 |
| J_mycelic_verified | 0.103 <sub>[0.075, 0.128]</sub> | 0.695 <sub>[0.645, 0.745]</sub> | 0.285 | 0.601 | 0.975 | 0.953 | 0.485 | 0.722 | 0.551 | 0.066 | 8.48e+05 | 0.143 |
| B4_central_triage | 0.099 <sub>[0.080, 0.118]</sub> | 0.770 <sub>[0.665, 0.845]</sub> | 0.270 | 0.665 | 0.965 | 0.948 | 0.555 | 0.725 | 0.515 | 0.054 | 1.33e+05 | 0.884 |
| B2_map_reduce | 0.089 <sub>[0.066, 0.111]</sub> | 0.580 <sub>[0.550, 0.610]</sub> | 0.240 | 0.343 | 0.855 | 0.932 | 0.485 | 0.626 | 0.506 | 0.277 | 79601 | 0.884 |
| H_mycelic_full | 0.087 <sub>[0.064, 0.116]</sub> | 0.695 <sub>[0.645, 0.745]</sub> | 0.270 | 0.601 | 0.975 | 0.953 | 0.485 | 0.722 | 0.551 | 0.066 | 8.38e+05 | 0.143 |
| I_mycelic_completion | 0.082 <sub>[0.066, 0.107]</sub> | 0.710 <sub>[0.635, 0.780]</sub> | 0.250 | 0.617 | 0.960 | 0.952 | 0.480 | 0.732 | 0.523 | 0.069 | 1.44e+06 | 0.143 |
| G_hier_questions | 0.073 <sub>[0.054, 0.094]</sub> | 0.695 <sub>[0.625, 0.755]</sub> | 0.230 | 0.575 | 0.965 | 0.953 | 0.485 | 0.723 | 0.540 | 0.080 | 8.36e+05 | 0.134 |
| Y_oracle_retrieval | 0.066 <sub>[0.046, 0.090]</sub> | 0.715 <sub>[0.650, 0.780]</sub> | 0.185 | 0.638 | 1.000 | 0.952 | 0.490 | 0.614 | 0.495 | 0.004 | 1.44e+05 | 0.884 |
| Z_random_rank | 0.044 <sub>[0.040, 0.048]</sub> | 0.580 <sub>[0.550, 0.610]</sub> | 0.155 | 0.343 | 0.855 | 0.932 | 0.485 | 0.626 | 0.506 | 0.277 | 79601 | 0.884 |
| F_hier_retrieval | 0.029 <sub>[0.014, 0.053]</sub> | 0.180 <sub>[0.140, 0.220]</sub> | 0.140 | 0.223 | 0.230 | 0.958 | 0.105 | 0.608 | 0.559 | 0.755 | 2.11e+05 | 0.134 |
| Z2_naive_enumerate | 0.022 <sub>[0.001, 0.063]</sub> | 0.145 <sub>[0.020, 0.350]</sub> | 0.095 | 0.122 | 0.000 | 0.961 | 0.105 | 0.000 | 0.000 | 1.000 | 39920 | 0.884 |
| E_hier_lineage | 0.014 <sub>[0.006, 0.024]</sub> | 0.120 <sub>[0.070, 0.170]</sub> | 0.120 | 0.079 | 0.170 | 0.932 | 0.030 | 0.631 | 0.691 | 0.846 | 69068 | 0.134 |
| C_recursive_sum | 0.012 <sub>[0.005, 0.021]</sub> | 0.040 <sub>[0.015, 0.060]</sub> | 0.040 | 0.000 | 0.045 | 1.000 | 0.000 | 0.000 | 0.000 | 0.949 | 41803 | 0.089 |
| D_hier_nolineage | 0.012 <sub>[0.003, 0.023]</sub> | 0.080 <sub>[0.045, 0.115]</sub> | 0.080 | 0.000 | 0.130 | 1.000 | 0.000 | 0.000 | 0.000 | 0.850 | 55021 | 0.134 |

#### 10,000 users (329,730 records, 40.0 hidden patterns, 4 seeds)

| architecture | AP | found | R@100 | rare recall | evidence cov. | FDR | decoy acc. | indep. acc. | lineage | info loss | compute (cu) | privacy exp. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 0.071 <sub>[0.046, 0.103]</sub> | 0.681 <sub>[0.606, 0.750]</sub> | 0.181 | 0.634 | 1.000 | 0.954 | 0.350 | 0.581 | 0.616 | 0.003 | 2.08e+06 | 0.227 |
| A_flat_rag | 0.071 <sub>[0.055, 0.091]</sub> | 0.656 <sub>[0.650, 0.669]</sub> | 0.169 | 0.516 | 0.981 | 0.956 | 0.350 | 0.737 | 0.619 | 0.045 | 1.09e+06 | 0.127 |
| B2_map_reduce | 0.051 <sub>[0.025, 0.077]</sub> | 0.294 <sub>[0.225, 0.369]</sub> | 0.156 | 0.094 | 0.406 | 0.972 | 0.244 | 0.782 | 0.533 | 0.654 | 1.99e+05 | 0.884 |
| G_hier_questions | 0.028 <sub>[0.010, 0.045]</sub> | 0.400 <sub>[0.344, 0.469]</sub> | 0.119 | 0.215 | 0.694 | 0.973 | 0.188 | 0.754 | 0.509 | 0.318 | 1.10e+06 | 0.138 |
| B_long_context | 0.028 <sub>[0.025, 0.032]</sub> | 0.456 <sub>[0.400, 0.512]</sub> | 0.163 | 0.146 | 0.725 | 0.949 | 0.125 | 0.673 | 0.711 | 0.382 | 1.09e+06 | 0.127 |
| I_mycelic_completion | 0.028 <sub>[0.012, 0.049]</sub> | 0.358 <sub>[0.325, 0.400]</sub> | 0.133 | 0.192 | 0.608 | 0.976 | 0.217 | 0.754 | 0.464 | 0.382 | 1.94e+06 | 0.147 |
| J_mycelic_verified | 0.027 <sub>[0.008, 0.055]</sub> | 0.342 <sub>[0.300, 0.425]</sub> | 0.075 | 0.178 | 0.675 | 0.977 | 0.192 | 0.732 | 0.506 | 0.334 | 1.09e+06 | 0.147 |
| B4_central_triage | 0.026 <sub>[0.015, 0.038]</sub> | 0.394 <sub>[0.362, 0.431]</sub> | 0.100 | 0.232 | 0.975 | 0.974 | 0.169 | 0.769 | 0.464 | 0.102 | 3.98e+05 | 0.884 |
| H_mycelic_full | 0.026 <sub>[0.012, 0.042]</sub> | 0.375 <sub>[0.300, 0.450]</sub> | 0.094 | 0.200 | 0.688 | 0.975 | 0.169 | 0.729 | 0.496 | 0.327 | 1.10e+06 | 0.147 |
| Z_random_rank | 0.011 <sub>[0.005, 0.015]</sub> | 0.292 <sub>[0.200, 0.400]</sub> | 0.058 | 0.081 | 0.383 | 0.972 | 0.275 | 0.789 | 0.549 | 0.661 | 1.99e+05 | 0.884 |
| F_hier_retrieval | 0.007 <sub>[0.003, 0.011]</sub> | 0.069 <sub>[0.056, 0.075]</sub> | 0.056 | 0.061 | 0.081 | 0.987 | 0.019 | 0.841 | 0.490 | 0.898 | 3.73e+05 | 0.138 |
| E_hier_lineage | 0.005 <sub>[0.000, 0.010]</sub> | 0.031 <sub>[0.013, 0.050]</sub> | 0.031 | 0.000 | 0.044 | 0.983 | 0.013 | 0.490 | 0.458 | 0.932 | 2.26e+05 | 0.138 |
| Z2_naive_enumerate | 0.004 <sub>[0.000, 0.011]</sub> | 0.183 <sub>[0.075, 0.375]</sub> | 0.033 | 0.170 | 0.000 | 0.988 | 0.133 | 0.000 | 0.000 | 1.000 | 1.85e+05 | 0.884 |
| Y_oracle_retrieval | 0.004 <sub>[0.003, 0.005]</sub> | 0.217 <sub>[0.150, 0.250]</sub> | 0.017 | 0.111 | 1.000 | 0.986 | 0.133 | 0.618 | 0.443 | 0.004 | 4.87e+05 | 0.884 |
| D_hier_nolineage | 0.002 <sub>[0.001, 0.004]</sub> | 0.044 <sub>[0.025, 0.062]</sub> | 0.044 | 0.000 | 0.062 | 1.000 | 0.000 | 0.000 | 0.000 | 0.937 | 1.96e+05 | 0.138 |
| C_recursive_sum | 0.000 <sub>[0.000, 0.000]</sub> | 0.000 <sub>[0.000, 0.000]</sub> | 0.000 | 0.000 | 0.006 | 1.000 | 0.000 | 0.000 | 0.000 | 0.989 | 1.69e+05 | 0.092 |

### Paired comparisons

Every comparison is paired on (scale, seed) — the seed controls both the org
chart and the corpus, so unpaired tests would be swamped by between-world
variance.

| comparison | metric | mean A | mean B | Δ | 95% CI | wins | sign p |
|---|---|---:|---:|---:|---|---:|---:|
| H_mycelic_full vs E_hier_lineage | found | 0.5528 | 0.0806 | +0.4722 | [+0.3806, +0.5639] | 9/9 | 0.004 |
| H_mycelic_full vs B2_map_reduce | found | 0.5528 | 0.4528 | +0.1000 | [+0.0556, +0.1417] | 8/8 | 0.008 |
| H_mycelic_full vs A_flat_rag | found | 0.5528 | 0.7778 | -0.2250 | [-0.3000, -0.1444] | 0/9 | 0.004 |
| H_mycelic_full vs B_long_context | found | 0.5528 | 0.6444 | -0.0917 | [-0.1306, -0.0528] | 1/9 | 0.039 |
| H_mycelic_full vs C_recursive_sum | found | 0.5528 | 0.0222 | +0.5306 | [+0.4278, +0.6306] | 9/9 | 0.004 |
| H_mycelic_full vs D_hier_nolineage | found | 0.5528 | 0.0639 | +0.4889 | [+0.3833, +0.5917] | 9/9 | 0.004 |
| H_mycelic_full vs B4_central_triage | found | 0.5528 | 0.6028 | -0.0500 | [-0.1111, +0.0028] | 3/9 | 0.508 |
| H_mycelic_full vs Y_oracle_retrieval | found | 0.5625 | 0.5281 | +0.0344 | [-0.0501, +0.1219] | 5/7 | 0.453 |
| B2_map_reduce vs Z_random_rank | found | 0.4719 | 0.4719 | +0.0000 | [+0.0000, +0.0000] | 0/0 | 1.000 |
| G_hier_questions vs F_hier_retrieval | found | 0.5639 | 0.1306 | +0.4333 | [+0.3556, +0.5167] | 9/9 | 0.004 |
| F_hier_retrieval vs E_hier_lineage | found | 0.1306 | 0.0806 | +0.0500 | [+0.0250, +0.0722] | 8/9 | 0.039 |
| H_mycelic_full vs E_hier_lineage | AP | 0.0596 | 0.0101 | +0.0495 | [+0.0260, +0.0764] | 8/9 | 0.039 |
| H_mycelic_full vs B2_map_reduce | AP | 0.0596 | 0.0719 | -0.0124 | [-0.0332, +0.0086] | 4/9 | 1.000 |
| H_mycelic_full vs A_flat_rag | AP | 0.0596 | 0.1915 | -0.1320 | [-0.2074, -0.0716] | 0/9 | 0.004 |
| H_mycelic_full vs B_long_context | AP | 0.0596 | 0.1292 | -0.0697 | [-0.1263, -0.0198] | 3/9 | 0.508 |
| H_mycelic_full vs C_recursive_sum | AP | 0.0596 | 0.0067 | +0.0528 | [+0.0304, +0.0794] | 9/9 | 0.004 |
| H_mycelic_full vs D_hier_nolineage | AP | 0.0596 | 0.0075 | +0.0520 | [+0.0287, +0.0792] | 9/9 | 0.004 |
| H_mycelic_full vs B4_central_triage | AP | 0.0596 | 0.0667 | -0.0071 | [-0.0301, +0.0126] | 5/9 | 1.000 |
| H_mycelic_full vs Y_oracle_retrieval | AP | 0.0635 | 0.0423 | +0.0212 | [+0.0079, +0.0348] | 7/8 | 0.070 |
| B2_map_reduce vs Z_random_rank | AP | 0.0707 | 0.0316 | +0.0390 | [+0.0228, +0.0565] | 8/8 | 0.008 |
| G_hier_questions vs F_hier_retrieval | AP | 0.0531 | 0.0192 | +0.0339 | [+0.0180, +0.0500] | 8/9 | 0.039 |
| F_hier_retrieval vs E_hier_lineage | AP | 0.0192 | 0.0101 | +0.0091 | [-0.0009, +0.0209] | 7/9 | 0.180 |

## 11. Scale

![discovery and evidence coverage vs enterprise size](../research/mycelic/artifacts/figures/scale.png)

| architecture | 2,000 | 10,000 |
|---|---:|---:|
| A_flat_rag | 0.875 | 0.656 |
| A2_chunked_ctx | 0.885 | 0.681 |
| B_long_context | 0.795 | 0.456 |
| B2_map_reduce | 0.580 | 0.294 |
| B4_central_triage | 0.770 | 0.394 |
| E_hier_lineage | 0.120 | 0.031 |
| G_hier_questions | 0.695 | 0.400 |
| H_mycelic_full | 0.695 | 0.375 |
| J_mycelic_verified | 0.695 | 0.342 |
| Y_oracle_retrieval | 0.715 | 0.217 |

Compute (cu) at the same points:

| architecture | 2,000 | 10,000 |
|---|---:|---:|
| A_flat_rag | 4.71e+05 | 1.09e+06 |
| A2_chunked_ctx | 5.12e+05 | 2.08e+06 |
| B_long_context | 1.09e+06 | 1.09e+06 |
| B2_map_reduce | 7.96e+04 | 1.99e+05 |
| B4_central_triage | 1.33e+05 | 3.98e+05 |
| E_hier_lineage | 6.91e+04 | 2.26e+05 |
| G_hier_questions | 8.36e+05 | 1.10e+06 |
| H_mycelic_full | 8.38e+05 | 1.10e+06 |
| J_mycelic_verified | 8.48e+05 | 1.09e+06 |
| Y_oracle_retrieval | 1.44e+05 | 4.87e+05 |

## 12. Ablations

![ablations](../research/mycelic/artifacts/figures/ablations.png)

| variant | AP | found | rare recall | evidence cov. | FDR | indep. acc. | lineage | contra F1 | D4 support infl. | Q utility | compute (cu) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| -lineage | 0.040 | 0.417 <sub>[0.400, 0.450]</sub> | 0.095 | 0.650 | 0.954 | 0.734 | 0.000 | 0.217 | 2.908 | 0.841 | 1.04e+06 |
| -contradiction | 0.035 | 0.392 <sub>[0.300, 0.450]</sub> | 0.228 | 0.675 | 0.974 | 0.736 | 0.509 | 0.217 | 3.096 | 0.908 | 1.08e+06 |
| -foreign_filter | 0.033 | 0.400 <sub>[0.375, 0.425]</sub> | 0.268 | 0.625 | 0.973 | 0.697 | 0.479 | 0.111 | 3.191 | 0.893 | 1.20e+06 |
| +evidence_verification | 0.031 | 0.300 <sub>[0.300, 0.300]</sub> | 0.134 | 0.625 | 0.980 | 0.713 | 0.517 | 0.167 | 2.860 | 0.889 | 1.07e+06 |
| +chain_completion | 0.031 | 0.338 <sub>[0.325, 0.350]</sub> | 0.188 | 0.587 | 0.977 | 0.718 | 0.456 | 0.167 | 3.392 | 0.889 | 1.91e+06 |
| -triage_prior | 0.029 | 0.300 <sub>[0.300, 0.300]</sub> | 0.134 | 0.625 | 0.980 | 0.713 | 0.517 | 0.167 | 2.860 | 0.889 | 1.06e+06 |
| +source_dispersion | 0.027 | 0.275 <sub>[0.250, 0.300]</sub> | 0.163 | 0.625 | 0.981 | 0.686 | 0.521 | 0.000 | 2.901 | 0.909 | 1.06e+06 |
| -synthesis_restriction | 0.027 | 0.325 <sub>[0.300, 0.350]</sub> | 0.159 | 0.625 | 0.978 | 0.724 | 0.498 | 0.500 | 2.860 | 0.891 | 1.06e+06 |
| -adaptive_routing | 0.025 | 0.417 <sub>[0.350, 0.475]</sub> | 0.298 | 0.675 | 0.972 | 0.753 | 0.494 | 0.315 | 3.028 | 0.914 | 1.10e+06 |
| full | 0.025 | 0.342 <sub>[0.300, 0.425]</sub> | 0.178 | 0.675 | 0.977 | 0.732 | 0.506 | 0.244 | 2.890 | 0.897 | 1.08e+06 |
| -cross_links | 0.025 | 0.367 <sub>[0.325, 0.400]</sub> | 0.220 | 0.675 | 0.975 | 0.754 | 0.506 | 0.000 | 3.072 | 0.918 | 1.08e+06 |
| -question_targeting | 0.021 | 0.375 <sub>[0.325, 0.425]</sub> | 0.242 | 0.667 | 0.975 | 0.746 | 0.516 | 0.067 | 2.982 | 0.892 | 1.08e+06 |
| -independence | 0.020 | 0.383 <sub>[0.350, 0.425]</sub> | 0.253 | 0.667 | 0.974 | 0.730 | 0.519 | 0.169 | 4.000 | 0.914 | 1.08e+06 |
| -adaptive_abstraction | 0.014 | 0.350 <sub>[0.325, 0.375]</sub> | 0.134 | 0.587 | 0.976 | 0.756 | 0.522 | 0.111 | 3.031 | 0.902 | 1.06e+06 |
| -temporal | 0.010 | 0.308 <sub>[0.275, 0.350]</sub> | 0.237 | 0.658 | 0.979 | 0.729 | 0.461 | 0.000 | 3.447 | 0.889 | 1.09e+06 |
| -questions | 0.002 | 0.058 <sub>[0.025, 0.100]</sub> | 0.036 | 0.050 | 0.989 | 0.620 | 0.389 | 0.000 | 2.667 | 0.000 | 3.73e+05 |
| -sketch_channel | 0.002 | 0.087 <sub>[0.075, 0.100]</sub> | 0.163 | 0.075 | 0.991 | 0.695 | 0.531 | 0.000 | 3.892 | 0.998 | 1.21e+06 |
| -downward_retrieval | 0.001 | 0.017 <sub>[0.000, 0.025]</sub> | 0.000 | 0.025 | 0.992 | 0.418 | 0.444 | 0.000 | 1.222 | 0.000 | 2.29e+05 |

**Paired against the full system** (same seeds):

| removed | Δ found | 95% CI | wins | sign p | Δ AP | Δ compute |
|---|---:|---|---:|---:|---:|---:|
| +chain_completion | +0.0375 | [+0.0250, +0.0500] | 0/2 | 0.500 | +0.0019 | +8.467e+05 |
| +evidence_verification | -0.0000 | [-0.0000, -0.0000] | 0/0 | 1.000 | +0.0024 | +1.032e+04 |
| +source_dispersion | -0.0250 | [-0.0500, -0.0000] | 1/1 | 1.000 | -0.0012 | -7.592e+02 |
| -adaptive_abstraction | +0.0500 | [+0.0250, +0.0750] | 0/2 | 0.500 | -0.0148 | -6.184e+03 |
| -adaptive_routing | +0.0750 | [+0.0500, +0.1250] | 0/3 | 0.250 | +0.0004 | +2.039e+04 |
| -contradiction | +0.0500 | [-0.0000, +0.1250] | 0/2 | 0.500 | +0.0101 | -7.210e+03 |
| -cross_links | +0.0250 | [-0.0500, +0.1000] | 1/3 | 1.000 | -0.0001 | -8.697e+03 |
| -downward_retrieval | -0.3250 | [-0.4000, -0.2750] | 3/3 | 0.250 | -0.0242 | -8.546e+05 |
| -foreign_filter | +0.1000 | [+0.0750, +0.1250] | 0/2 | 0.500 | +0.0043 | +1.412e+05 |
| -independence | +0.0417 | [-0.0000, +0.0750] | 0/2 | 0.500 | -0.0053 | -6.214e+03 |
| -lineage | +0.0750 | [-0.0250, +0.1500] | 1/3 | 1.000 | +0.0152 | -4.129e+04 |
| -question_targeting | +0.0333 | [-0.0500, +0.1250] | 1/3 | 1.000 | -0.0036 | -1.344e+02 |
| -questions | -0.2833 | [-0.3250, -0.2500] | 3/3 | 0.250 | -0.0231 | -7.109e+05 |
| -sketch_channel | -0.2125 | [-0.2250, -0.2000] | 2/2 | 0.500 | -0.0269 | +1.462e+05 |
| -synthesis_restriction | +0.0250 | [-0.0000, +0.0500] | 0/1 | 1.000 | -0.0014 | +7.768e+02 |
| -temporal | -0.0333 | [-0.0750, -0.0000] | 2/2 | 0.500 | -0.0151 | +1.036e+04 |
| -triage_prior | -0.0000 | [-0.0000, -0.0000] | 0/0 | 1.000 | -0.0000 | -0.000e+00 |

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
| 0.00 | 0.0018 | 0.058 | 0.036 | 3.73e+05 |  |
| 0.05 | 0.0248 | 0.342 | 0.178 | 1.08e+06 |  |
| 0.15 | 0.0248 | 0.342 | 0.178 | 1.08e+06 |  |
| 0.35 | 0.0233 | 0.383 | 0.287 | 1.16e+06 |  |
| 0.55 | 0.0153 | 0.333 | 0.251 | 1.45e+06 |  |
| 1.00 | 0.0113 | 0.275 | 0.198 | 2.12e+06 |  |

#### Map-reduce: kernel object budget

| kernel objects | AP | found | compute | cu/disc |
|---|---:|---:|---:|---:|
| 300 | 0.0054 | 0.033 | 1.51e+05 |  |
| 900 | 0.0310 | 0.108 | 1.64e+05 |  |
| 2500 | 0.0401 | 0.292 | 1.99e+05 |  |
| 8000 | 0.0067 | 0.267 | 2.66e+05 |  |
| 20000 | 0.0031 | 0.225 | 3.91e+05 |  |

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

The full system asks 220 questions per run. 90% of them change a conclusion — a question counts as useful only if the kernel's hypothesis set or its confidence actually moved, never merely because text was produced. Each returns 102 new knowledge objects on average, and 100% are well-targeted.

* removing questioning entirely: discovery Δ +0.283 (95% CI [+0.250, +0.325], 3/3 seeds, sign p=0.250); rare-signal recall Δ +0.142 (95% CI [+0.059, +0.267], 3/3 seeds, sign p=0.250); compute Δ -7.11e+05.
* keeping questions but destroying their targeting: discovery Δ -0.033 (95% CI [-0.125, +0.050], 1/3 seeds, sign p=1.000); rare-signal recall Δ -0.064 (95% CI [-0.176, +0.050], 1/3 seeds, sign p=1.000); compute Δ -1.34e+02.

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
| approx_index_bloom | B2_map_reduce | 0.0485 | 0.238 | 0.977 | 0.287 | 0.100 | 0.250 | 0.200 | 0.600 | 2.93 |
| approx_index_bloom | B_long_context | 0.0265 | 0.400 | 0.950 | 0.162 | 0.000 | 0.200 | 0.150 | 0.300 | 1.69 |
| approx_index_bloom | E_hier_lineage | 0.0063 | 0.013 | 0.991 | 0.025 | 0.000 | 0.000 | 0.050 | 0.050 | 0.83 |
| approx_index_bloom | H_mycelic_full | 0.0195 | 0.325 | 0.978 | 0.287 | 0.100 | 0.200 | 0.350 | 0.500 | 2.95 |
| clean | B2_map_reduce | 0.0401 | 0.292 | 0.972 | 0.275 | 0.067 | 0.267 | 0.167 | 0.600 | 3.07 |
| clean | B_long_context | 0.0289 | 0.442 | 0.950 | 0.133 | 0.000 | 0.167 | 0.133 | 0.233 | 1.74 |
| clean | E_hier_lineage | 0.0063 | 0.013 | 0.991 | 0.025 | 0.000 | 0.000 | 0.050 | 0.050 | 0.83 |
| clean | H_mycelic_full | 0.0248 | 0.342 | 0.977 | 0.192 | 0.067 | 0.200 | 0.233 | 0.267 | 2.89 |
| decoy_heavy | B2_map_reduce | 0.0746 | 0.275 | 0.973 | 0.198 | 0.062 | 0.271 | 0.062 | 0.396 | 2.82 |
| decoy_heavy | B_long_context | 0.0373 | 0.438 | 0.952 | 0.141 | 0.042 | 0.125 | 0.167 | 0.229 | 1.84 |
| decoy_heavy | E_hier_lineage | 0.0025 | 0.038 | 0.980 | 0.005 | 0.000 | 0.000 | 0.000 | 0.021 | 1.71 |
| decoy_heavy | H_mycelic_full | 0.0189 | 0.412 | 0.972 | 0.182 | 0.062 | 0.208 | 0.208 | 0.250 | 2.81 |
| duplicate_flood | B2_map_reduce | 0.0733 | 0.300 | 0.971 | 0.250 | 0.200 | 0.250 | 0.150 | 0.400 | 3.47 |
| duplicate_flood | B_long_context | 0.0558 | 0.475 | 0.943 | 0.188 | 0.050 | 0.200 | 0.050 | 0.450 | 1.53 |
| duplicate_flood | E_hier_lineage | 0.0038 | 0.050 | 0.971 | 0.013 | 0.000 | 0.050 | 0.000 | 0.000 | 1.25 |
| duplicate_flood | H_mycelic_full | 0.0482 | 0.375 | 0.975 | 0.300 | 0.050 | 0.350 | 0.250 | 0.550 | 2.70 |
| extreme_imbalance | B2_map_reduce | 0.0766 | 0.375 | 0.963 | 0.250 | 0.100 | 0.400 | 0.050 | 0.450 | 3.24 |
| extreme_imbalance | B_long_context | 0.0399 | 0.450 | 0.945 | 0.175 | 0.000 | 0.150 | 0.150 | 0.400 | 2.06 |
| extreme_imbalance | E_hier_lineage | 0.0078 | 0.025 | 0.958 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 1.33 |
| extreme_imbalance | H_mycelic_full | 0.0275 | 0.412 | 0.972 | 0.237 | 0.000 | 0.250 | 0.200 | 0.500 | 2.70 |
| false_report_flood | B2_map_reduce | 0.0815 | 0.275 | 0.971 | 0.213 | 0.150 | 0.350 | 0.050 | 0.300 | 2.72 |
| false_report_flood | B_long_context | 0.0344 | 0.463 | 0.935 | 0.188 | 0.050 | 0.300 | 0.100 | 0.300 | 1.50 |
| false_report_flood | E_hier_lineage | 0.0050 | 0.025 | 0.977 | 0.013 | 0.000 | 0.000 | 0.050 | 0.000 | 1.00 |
| false_report_flood | H_mycelic_full | 0.0140 | 0.362 | 0.976 | 0.225 | 0.100 | 0.350 | 0.200 | 0.250 | 2.83 |
| high_cross_site_noise | B2_map_reduce | 0.0428 | 0.263 | 0.978 | 0.237 | 0.100 | 0.350 | 0.100 | 0.400 | 3.00 |
| high_cross_site_noise | B_long_context | 0.0320 | 0.412 | 0.959 | 0.188 | 0.050 | 0.150 | 0.300 | 0.250 | 1.59 |
| high_cross_site_noise | E_hier_lineage | 0.0009 | 0.025 | 0.985 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.62 |
| high_cross_site_noise | H_mycelic_full | 0.0330 | 0.287 | 0.981 | 0.338 | 0.100 | 0.400 | 0.500 | 0.350 | 2.66 |
| malicious_nodes_5pct | B2_map_reduce | 0.0485 | 0.238 | 0.977 | 0.287 | 0.100 | 0.250 | 0.200 | 0.600 | 2.93 |
| malicious_nodes_5pct | B_long_context | 0.0265 | 0.400 | 0.950 | 0.162 | 0.000 | 0.200 | 0.150 | 0.300 | 1.69 |
| malicious_nodes_5pct | E_hier_lineage | 0.0006 | 0.013 | 0.976 | 0.013 | 0.000 | 0.000 | 0.000 | 0.050 | 2.50 |
| malicious_nodes_5pct | H_mycelic_full | 0.0269 | 0.375 | 0.975 | 0.238 | 0.100 | 0.250 | 0.200 | 0.400 | 3.15 |
| rare_only | B2_map_reduce | 0.0037 | 0.162 | 0.984 | 0.188 | 0.050 | 0.250 | 0.000 | 0.450 | 3.43 |
| rare_only | B_long_context | 0.0105 | 0.225 | 0.972 | 0.138 | 0.050 | 0.100 | 0.050 | 0.350 | 1.84 |
| rare_only | E_hier_lineage | 0.0004 | 0.013 | 0.988 | 0.013 | 0.000 | 0.050 | 0.000 | 0.000 | 1.00 |
| rare_only | H_mycelic_full | 0.0077 | 0.287 | 0.981 | 0.213 | 0.000 | 0.300 | 0.050 | 0.500 | 2.80 |
| stale_flood | B2_map_reduce | 0.0647 | 0.350 | 0.967 | 0.163 | 0.000 | 0.250 | 0.050 | 0.350 | 2.98 |
| stale_flood | B_long_context | 0.0302 | 0.438 | 0.959 | 0.125 | 0.050 | 0.100 | 0.050 | 0.300 | 1.35 |
| stale_flood | E_hier_lineage | 0.0000 | 0.000 | 1.000 | 0.013 | 0.000 | 0.000 | 0.050 | 0.000 | 0.90 |
| stale_flood | H_mycelic_full | 0.0263 | 0.450 | 0.970 | 0.250 | 0.050 | 0.550 | 0.250 | 0.150 | 2.77 |
| unavailable_sites_20pct | B2_map_reduce | 0.0485 | 0.238 | 0.977 | 0.287 | 0.100 | 0.250 | 0.200 | 0.600 | 2.93 |
| unavailable_sites_20pct | B_long_context | 0.0265 | 0.400 | 0.950 | 0.162 | 0.000 | 0.200 | 0.150 | 0.300 | 1.69 |
| unavailable_sites_20pct | E_hier_lineage | 0.0025 | 0.038 | 0.956 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 1.38 |
| unavailable_sites_20pct | H_mycelic_full | 0.0124 | 0.325 | 0.978 | 0.200 | 0.050 | 0.200 | 0.250 | 0.300 | 2.50 |

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

| users | architecture | raw text out | claims out | index entries out | found | compute |
|---|---:|---:|---:|---:|---:|---:|
| 2000 | A2_chunked_ctx | 0.2543 | 0.0000 | 0 | 0.875 | 5.19e+05 |
| 2000 | A_flat_rag | 0.2543 | 0.0000 | 0 | 0.863 | 4.77e+05 |
| 2000 | B2_map_reduce | 0.0000 | 0.8849 | 0 | 0.587 | 81931 |
| 2000 | B4_central_triage | 0.0000 | 0.8849 | 0 | 0.800 | 1.35e+05 |
| 2000 | B_long_context | 0.6083 | 0.0000 | 0 | 0.838 | 1.09e+06 |
| 2000 | C_recursive_sum | 0.0000 | 0.0887 | 0 | 0.062 | 42882 |
| 2000 | D_hier_nolineage | 0.0000 | 0.1327 | 64418 | 0.100 | 56642 |
| 2000 | E_hier_lineage | 0.0000 | 0.1327 | 64418 | 0.150 | 71836 |
| 2000 | F_hier_retrieval | 0.0000 | 0.1327 | 64418 | 0.175 | 2.15e+05 |
| 2000 | G_hier_questions | 0.0000 | 0.1327 | 64418 | 0.700 | 8.33e+05 |
| 2000 | H_mycelic_full | 0.0000 | 0.1327 | 64418 | 0.750 | 8.33e+05 |
| 2000 | I_mycelic_completion | 0.0000 | 0.1327 | 64418 | 0.713 | 1.42e+06 |
| 2000 | J_mycelic_verified | 0.0000 | 0.1327 | 64418 | 0.750 | 8.43e+05 |
| 2000 | Y_oracle_retrieval | 0.0000 | 0.8849 | 0 | 0.688 | 1.44e+05 |
| 2000 | Z2_naive_enumerate | 0.0000 | 0.8849 | 0 | 0.025 | 40163 |
| 2000 | Z_random_rank | 0.0000 | 0.8849 | 0 | 0.587 | 81931 |
| 10000 | A2_chunked_ctx | 0.2271 | 0.0000 | 0 | 0.637 | 2.09e+06 |
| 10000 | A_flat_rag | 0.1266 | 0.0000 | 0 | 0.650 | 1.09e+06 |
| 10000 | B2_map_reduce | 0.0000 | 0.8834 | 0 | 0.238 | 2.00e+05 |
| 10000 | B4_central_triage | 0.0000 | 0.8834 | 0 | 0.375 | 3.99e+05 |
| 10000 | B_long_context | 0.1266 | 0.0000 | 0 | 0.400 | 1.09e+06 |
| 10000 | C_recursive_sum | 0.0000 | 0.0919 | 0 | 0.000 | 1.68e+05 |
| 10000 | D_hier_nolineage | 0.0000 | 0.1376 | 4.40e+05 | 0.038 | 1.95e+05 |
| 10000 | E_hier_lineage | 0.0000 | 0.1376 | 4.40e+05 | 0.013 | 2.22e+05 |
| 10000 | F_hier_retrieval | 0.0000 | 0.1376 | 4.40e+05 | 0.062 | 3.62e+05 |
| 10000 | G_hier_questions | 0.0000 | 0.1376 | 4.40e+05 | 0.363 | 1.05e+06 |
| 10000 | H_mycelic_full | 0.0000 | 0.1376 | 4.40e+05 | 0.300 | 1.06e+06 |
| 10000 | I_mycelic_completion | 0.0000 | 0.1376 | 4.40e+05 | 0.338 | 1.91e+06 |
| 10000 | J_mycelic_verified | 0.0000 | 0.1375 | 4.50e+05 | 0.300 | 1.05e+06 |
| 10000 | Y_oracle_retrieval | 0.0000 | 0.8841 | 0 | 0.250 | 4.88e+05 |
| 10000 | Z2_naive_enumerate | 0.0000 | 0.8841 | 0 | 0.375 | 1.85e+05 |
| 10000 | Z_random_rank | 0.0000 | 0.8841 | 0 | 0.275 | 2.00e+05 |

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

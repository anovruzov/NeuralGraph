"""Narrative for the final report.  Tables are injected from report.py; no
number in this file is typed by hand except where it is explicitly labelled as
a configuration value.
"""
from __future__ import annotations

from typing import Dict

ARCH_DIAGRAM = r"""
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
"""

HEADER = """# Mycelic: hierarchical enterprise intelligence — benchmark and architecture study

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
"""

METHOD = """
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
"""

ASSUMPTIONS = """
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
"""

FAILED = """
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
"""


def compose(sec: Dict[str, str]) -> str:
    from datetime import date
    return f"""{HEADER}

---

## 1. Executive summary

{sec.get('summary', '_(filled from results)_')}

---

## 2. Architecture diagram

{ARCH_DIAGRAM}

---

## 3-6. Recommended hierarchy, models, fan-in, and why

{sec.get('recommendation', '_(filled from results)_')}

---

## 7. Benchmark methodology

{METHOD}

### The synthetic enterprise at each scale

{sec['world']}

### Calibration (all knobs fitted on held-out seeds, then frozen)

{sec['calibration']}

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

{sec['baselines']}

### Paired comparisons

{sec['pairs']}

---

## 9. Ablations

{sec['ablations']}

---

## 10. Scale results

Discovery (`found`) by architecture and scale — every cell is an executed run,
none is extrapolated:

{sec['scale_trend']}

---

## 11. Cost analysis

### Compute allocation across levels

{sec['alloc']}

### Marginal value of capability at each level

Baseline: every level at `small-7b`. Then exactly one level is upgraded to
`frontier`, holding everything else fixed. This is the experiment that decides
where compute should go.

{sec['level_marginal']}

### Uniform capability sweep

The function from operator quality to architecture quality, which is the claim
this study can actually make.

{sec['qsweep']}

### Cost / quality frontier

{sec['frontier']}

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

{sec['privacy']}

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

{sec.get('questions', 'Question utility, information gain and cost appear in the ablation table (§9, `-questions`, `-question_targeting`) and in the cost/quality frontier (§11).')}

---

## 17. Downward retrieval and adversarial conditions

{sec['adversarial']}

---

## 18. Organisational fan-in, and org tree vs semantic overlay

{sec['fanin']}

### Strict org tree vs org tree + semantic cross-links

{sec['crosslinks']}

---

## 19. Capability-shape sensitivity

If a conclusion survives only under one assumed shape for how hard
capabilities scale with model quality, it is not a conclusion.

{sec['shape']}

---

## 20. Direct model measurement

{sec['live']}

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

{sec.get('critique', '_(filled from results)_')}

---

## 23. Assumptions

{ASSUMPTIONS}

---

## 24. Failed approaches, and what they taught us

{FAILED}

---

## 25. Recommended next experiments

{sec.get('next', '_(filled from results)_')}

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

_Generated {date.today().isoformat()} by `research/mycelic/report.py` from the
raw artifacts._
"""

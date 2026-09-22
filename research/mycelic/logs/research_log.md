# Mycelic enterprise-hierarchy study — running research log

Format: PLANNER decision → ENGINEER result → REVIEWER objection → next action.
Everything here is written as it happened, including the things that did not work.

---

## Iteration 0 — framing (PLANNER)

No GPU and no local inference are available in this environment, so "model
tier" cannot be a measurement of a specific checkpoint. Decision, recorded as
assumption **A1**: the independent variable is a *capability vector* `q`, and
named model classes are labelled positions on that axis. Every headline claim
must therefore be a claim about the function from operator quality to
architecture quality, not about Qwen2.5-7B specifically.

Assumption **A2**: all architectures share one implementation of every
cognitive operator (extract / abstract / synthesize / verify / question).
Architectures differ *only* in what reaches which operator at which tier. This
is the fairness contract; without it the study is worthless.

Assumption **A3**: the strongest centralised competitor is not flat RAG. It is
**map-reduce**: pay the same cheap extraction over every record that the
hierarchy pays at its user layer, pool every claim centrally, rank globally,
and hand the top of that ranking to one strong kernel call. A centralised
claim pool is strictly better *informed* than hierarchical sketches, so it
should be treated as an upper bound on hierarchical accuracy, and the research
question becomes what the hierarchy gives up and what it buys.

## Iteration 1 — first end-to-end run: every hierarchy variant scores 0.000

Flat RAG got 0.42 recall; D–H got exactly zero. Two causes:

1. The propagation funnel was far too narrow (budgets 6/10/14/20/34), so the
   kernel saw 232 knowledge objects derived from 15,510 records. Widened to a
   fan-in-derived schedule and, critically, **equalised the kernel context
   across architectures** (900 objects for everyone) so the comparison is at
   matched kernel context rather than at whatever each design happened to emit.
2. `synthesize` emitted one hypothesis per anchor entity, taking the top
   `synth_depth` predicates by importance. For an entity with 20 predicates
   that is almost never the pattern's predicates. Rewritten to enumerate one
   candidate per (anchor, causal chain).

## Iteration 2 — decoy design flaw found by the REVIEWER

D4 ("duplicate inflation") was being counted as a false discovery. But a D4
chain is a *genuine* causal chain that is merely over-supported: reporting it
is not a hallucination, mis-counting its independent support is the error.
D4 was removed from the precision denominator and given its own metric
(`dup_inflation_support_error`). A new decoy class **D5 (stale chain)** was
added — a real chain every link of which is later retracted — so that temporal
state has a decoy of its own.

## Iteration 3 — leakage audit

Systems were being handed `event`, which made duplicate detection free and
made D4 trivial. Removed: surface text is now keyed on the event id so that an
echo repeats the original wording and duplicate detection is a text-similarity
problem every architecture faces equally.

## Iteration 4 — local novelty, and a measurement that killed the first version

Global (predicate, entity) novelty cannot separate a weak signal from routine
traffic: at 10k users the (pred, anchor) space is sparse enough that almost
everything is "novel". Replaced with **local** novelty (rare *in this org
unit*). First version took `max(site_novelty, team_novelty)`; measured, this
*destroyed* the ranking, because a 9-person team sees so few entities that
almost every record looks locally novel. Replaced with a site-weighted blend.

## Iteration 5 — entity over-merging was modelled in the wrong place

Entity linking error was applied per record at extraction time, at a 42% rate
for the 7B tier. Two problems: (a) copying a mention string is easy for any
model — it is *deciding two strings denote the same thing* that is hard, and
that happens at synthesis; (b) applied per record it made a 7-record facet
survive with probability p^7, turning the study into a referendum on
extraction noise. Moved to synthesis, and made per (agent, entity) rather
than per mention. This is the single change that most improved realism.

## Iteration 6 — the central negative result

Measured directly: **of 306 facet records at 10k users, 0 appear in the global
top-900 by record-level importance.** No per-record feature identifies a facet,
because a facet record is individually indistinguishable from benign
cross-site chatter — which is the premise of the problem, not a bug.

Consequence: *record-level propagation cannot find these patterns at all.*
Detection has to be entity-level and relational. This drove the two-channel
design: a complete, cheap, structured **sketch channel** (per entity, per site:
mentions, bitmask of operational predicates seen at least `min_support` times,
time span) alongside the selective, evidence-bearing knowledge-object channel.

## Iteration 7 — three routing bugs, each worth more than any model upgrade

1. Descent shuffled candidate children instead of ranking them by index
   evidence. Fixed: best-first over the sketch.
2. The expansion did a *global* top-N sort, which is captured by the entity's
   home branch (it holds most of its mentions and none of the interesting
   foreign evidence). Fixed: per-parent quota first, global sort only to trim.
3. Restricting the descent to the sites the triage named *halved* recovery
   (1/12 vs 7/12 patterns with ≥3 well-supported links), because the sketch
   only lists sites whose evidence already passed the support threshold and
   the thin facets are exactly the ones that did not. Triage sites are now a
   frontier *seed*, not a replacement, and the entity's home branch is
   down-weighted so the retrieval budget is spent where it does not belong.

## Iteration 8 — evidence discarded between rounds (plain bug)

`_descend_round` built its merged evidence pool locally and threw it away; the
questioning round then started again from the original pool. Everything the
retrieval path had just paid to fetch was being dropped. Fixed by accumulating.

## Iteration 9 — the oracle control that redirected the whole study

Built a **perfect-retrieval oracle**: every extracted claim, no budget at all,
same reasoning operators. It reached coverage 19/36 and R@40 = 0.111 — barely
better than the budgeted systems. Conclusion: at this noise density the
bottleneck is **not retrieval**. It is (a) extraction fidelity at the bottom
tier and (b) discriminating ~600 spurious multi-region "causal chains" thrown
up by ordinary cross-site traffic from the ~20 real ones.

That is a very different research conclusion from the one the architecture was
designed around, and it is the reason the compute-allocation experiment
matters: if ranking rather than retrieval is the binding constraint, spending
at the top has a different payoff than spending at the bottom.

Two consequences implemented:
- the monotone-sub-path search now maximises **evidence weight**, not path
  length (pure longest-path picks up thin background links and drops
  well-evidenced ones);
- confidence became a calibrated logistic rather than a clipped linear sum,
  because with a linear score most candidates pinned at the ceiling and the
  ranking — which is what a finite executive report budget actually consumes —
  was arbitrary.

## Iteration 10 — match criterion, stated openly

A 3-link gold pattern reported as a 3-link chain sharing 2 of its links is a
real, actionable discovery (right entity, right chain, most of the story), but
the original criterion scored it zero. Two criteria are now computed and
**both are reported in every table**: `primary` (right entity, ≥2 gold links
and ≥ half of them) and `strict` (≥ min(3, |gold links|)). No table ever drops
the strict column.

## Iteration 11 — calibration protocol, and why it mattered immediately

All tunable knobs are now fitted on CALIBRATION seeds (500-502) disjoint from
the evaluation seeds (0-4), by the same procedure for every architecture:
hierarchy (triage prior weight x question budget fraction), map-reduce (kernel
object budget), flat RAG (retrieval token budget).

This paid for itself on the first run. The triage prior weight improved AP
monotonically on seed 0 (0.027 -> 0.098 at weight 3.0) and would have been
adopted on that evidence. On the held-out calibration seeds the best value is
**0.0** — the prior does not generalise. It is frozen at 0 and reported as a
non-result.

Calibrated values: `triage_prior_weight=0.0`, `question_frac=0.25`,
`mr_budget=900`, `flat_budget=1,000,000`.

## Iteration 12 — the live measurement run audited the measurement

The first direct-measurement task file was handed to three real models. The
strongest one refused to treat it as a clean measurement and reported four
defects in the harness, all of them real:

1. **The answer key was inlined next to every item** (`gold_predicate`,
   `gold`), so the run was not blind at all.
2. **30 of 48 extraction items had out-of-vocabulary gold labels** — the
   vocabulary shipped only the 34 operational predicates while the items were
   sampled from all records, including routine ones.
3. **The T2 criterion was under-specified**: at least one item had all
   predicates on one chain but a "no" label because the times ran backwards,
   so chain membership and chain direction were conflated.
4. **The labels were positionally predictable** — T2 alternated yes/no by
   index parity, T3 cycled with period 3, T4 alternated — so a model could
   score well by noticing the periodicity instead of reasoning.

All three runs were discarded. The harness now ships the key in a separate
file the measured model is never pointed at, uses the full 50-predicate
vocabulary, states T2 as pure chain membership (direction is T3's job), draws
every label at random, and shuffles item order within each group.

## Iteration 13 — the primitive operators are saturated (direct measurement)

Three real models (Haiku 4.5, Sonnet 5, Opus 5) were run blind on 198 items
built from the generator's own text: extraction, causal-chain membership,
temporal order/retraction, entity linking, independent-source counting.

**All three scored 1.000 on all five.**

Two consequences, both important:

1. The operator primitives this architecture is built on are not the
   difficulty. Anything the study says about "stronger models higher up" cannot
   be about these primitives for frontier-class models — there is no headroom.
2. The test set therefore cannot discriminate between tiers, so it calibrates
   only the top of the capability axis. Positions below it remain assumptions.
   The simulated tier vectors are *upper-bounded* by this: at q=1.0 the
   simulator predicts 0.96-0.99 on these operators, and the measurement says
   1.00, so the simulator is if anything slightly pessimistic at the top.

A further fairness defect surfaced during the run and was fixed: the task file
was regenerated while a measurement was in flight, so one model answered a
partly stale T1. The file is now checksummed
(`artifacts/live_tasks.sha256`), the answer key was moved out of the served
directory entirely (a measurement run flagged its presence as a leakage
hazard even though it did not open it), and every answer file is verified
id-by-id against the frozen task set before scoring.

## Iteration 14 — measuring the operator that actually binds

Since the primitives saturate, a second live measurement targets the operator
that the end-to-end results say is the real bottleneck: **discrimination over
a candidate list**. 60 candidates are taken from a real 10k-user run of the
benchmark — 15 genuine, 45 artefacts sampled across the simulator's whole
confidence range — each rendered as its evidence statistics. Models rank them
and choose a report set. Their AP is compared against the simulator's own
calibrated logistic and against random.

This is the decisive check on the compute-allocation conclusion: if a frontier
model discriminates materially better than the logistic, the simulator
understates the value of kernel-level compute and every allocation result has
to be revisited.

## Iteration 15 — two controls the results needed

* **B4 centralised triage.** The centralised twin of the hierarchy's own
  algorithm: same entity-level relational triage, but one claim pool, no
  propagation budget, no sketch cap, no routing error, no descent. It is
  strictly better informed than the hierarchy at every step, and it separates
  the value of the *triage algorithm* from the value of the *hierarchy*.
  It also gets its own calibrated evidence budget, so the hierarchy is not
  credited for a filtering discipline the control was never allowed to apply.
* **A non-LLM `lexical` tier** (regex/gazetteer: copies strings well, cannot
  link entities or reason about chains, compute cost ~0), so the question
  "does the edge need a language model at all?" is answerable rather than
  assumed.

## Iteration 16 — the measurement that changed the recommendation

Three real models were given the same 60 candidates from a real run, twice:
once as **aggregate evidence statistics only**, once with the **raw work notes**
behind those statistics.

| ranker | AP |
|---|---:|
| random | 0.300 |
| the simulator's calibrated logistic (statistics) | 0.453 |
| Haiku 4.5 (statistics) | 0.439 |
| Sonnet 5 (statistics) | 0.435 |
| Opus 5 (statistics) | 0.473 |
| Haiku 4.5 (statistics + raw notes) | **0.638** |
| Opus 5 (statistics + raw notes) | **0.596** |

Two conclusions, both measured:

1. **At fixed evidence, model capability buys almost nothing here.** Three
   models spanning a large capability range all land within noise of a
   six-feature logistic. The simulator's kernel is therefore neither
   generous nor pessimistic, and "put the strongest model at the top" has
   very little headroom *on this operator*.
2. **Changing what the evidence contains buys a lot.** The same models with
   the original notes gain 0.15-0.20 AP and lift selection F1 from 0.30-0.38
   to 0.41-0.50.

Asked what the notes gave them that the statistics did not, the models named
three specific things: **loud-site inflation** (many "independent sources" that
are several departments of one site), **broadcast echo** (identical wording all
dated the same day — one announcement fanned out, not independent discovery),
and **phantom attribution** (link statistics whose underlying notes name a
*different* entity, i.e. an edge model's mis-link that the aggregates preserve
perfectly).

All three were implemented as structured features and fitted on the held-out
calibration seeds:

* **source dispersion** — looked like a 3.5x AP win on seed 0 (0.019 → 0.068).
  On held-out seeds the best weight is **0.0**. Rejected. This is the second
  time the protocol has caught a large single-seed gain that did not exist.
* **synchrony** — no effect; echoes already share an event id and are removed
  by the independence sketch, so the generator does not really contain the
  failure the models were describing.
* **kernel-side evidence verification** — the kernel re-reads ~6 of each top
  candidate's original notes with its OWN extractor and checks they name the
  entity the candidate is about. Held-out AP 0.0445 → 0.0563 (**+27%**) for
  **+1% compute**. Adopted.

The architectural lesson is the inverse of the starting hypothesis: the useful
way to spend frontier-tier compute at the kernel is not to reason harder over
the same abstraction, it is to **re-open a few pieces of original evidence**.

## Iteration 17 — three coupled metric defects, found by reading the outputs

Inspecting the per-metric outputs of a single run (rather than only the
headline) exposed three problems, each of which had been silently shaping
results:

1. **Contradiction F1 was identically zero for every architecture.** Conflicts
   were read from counters the knowledge objects carried upward, but a descent
   returns one fresh object per contributing user, so every conflict that only
   becomes visible once recovered evidence joins the pool was invisible.
   Contradiction is now recomputed at synthesis from the polarities actually
   in front of the kernel.

2. **The first fix then broke the baselines.** Flagging a contradiction on the
   mere presence of a dissenting report punishes whichever architecture
   gathered the most evidence — exactly backwards. It is now judged on the
   BALANCE of evidence (a minority position needs at least two sources and at
   least a quarter of the link's weight).

3. **Removing the old behaviour cost the centralised baselines most of their
   discovery** (flat retrieval fell from 0.73 to 0.15 `found` at 10k). The old
   accidental accumulation of polarity flips had been doing real ranking work:
   conflict *volume* separates noisy background entities from coherent
   propagating chains. It is now an explicit continuous term, computed
   identically whether conflicts appear as opposite-polarity objects (the
   hierarchy, one object per agent) or as flip counts inside one merged object
   (any flat pool) — otherwise the same metric silently means different things
   for different architectures.

A fourth, separate defect: **evidence coverage was being measured over the
reported hypotheses**, so it depended on register truncation — i.e. on
ranking, the very thing coverage exists to be separated from. It now reads the
kernel's working pool, and the hierarchy returns its *final* pool rather than
its pre-descent one.

And a fifth, which cost an hour of confusion: `runner.run_arch` and
`experiments._run_named` had **different default budgets**, so the same
architecture scored 0.15 or 0.73 depending on which entry point was used.
Calibration loading now lives in one place.

Everything before this point was moved to `artifacts/stale_pre_metric_fix/`
and the entire pipeline — calibration included — was re-run. Re-calibrating
mattered: the map-reduce baseline's best kernel budget moved from 900 to 2500
objects once the conflict term existed.

## Iteration 18 — a scoring ambiguity, found by a measurement subject

A model doing the blind discrimination task reported, unprompted, that four
entity names appeared twice in the candidate set — "each time as a clean
correctly-ordered chain paired with a weaker or inverted-order variant" — and
said it had used the pairing to separate them.

That is a real defect, and it was in the generator, not the task: pattern and
decoy anchor entities were drawn independently from the same pool, so roughly
**10% of real patterns shared an entity with a decoy** (measured: 4 of ~40 at
10k users, 3 of ~98 at 50k). One reported hypothesis on such an entity was
being credited simultaneously as a correct discovery *and* as a decoy
acceptance, inflating both metrics at once.

Sharing an entity between a genuine risk and coincidental noise is realistic;
the problem is purely that it makes the scoring ambiguous. Two changes:

* anchors are now drawn **without replacement** across all real patterns and
  decoys (measured after: 0 collisions at both scales);
* and, belt and braces, a report that correctly identifies a real chain is
  never also counted as a decoy acceptance.

The bias was equal across architectures, so the comparative conclusions were
not at risk — but `decoy_acceptance` is a headline metric and it was wrong by
about a tenth. The whole pipeline was restarted again, calibration included.

Worth recording separately: **the two most useful critiques of this benchmark
so far both came from the models being measured by it**, not from inspecting
the code — the non-blind task file in iteration 12, and this. Giving a
measurement subject room to report what is wrong with the measurement turned
out to be worth more than another ablation.

---

## Iteration 19 — the headline mechanism claim did not replicate, and the fix
## was repeats, not a better story

The study's most quotable finding was that giving a model the **raw work
notes** behind a set of evidence statistics beats giving the same statistics
to a bigger model. On the pre-fix corpus it was clean: every model improved
substantially in the richer condition.

Re-run on the corpus with collision-free anchors, it was not:

```
                       statistics only     + raw notes
  opus                      0.378             0.666
  haiku                     0.354             0.436
  sonnet                    0.361             0.334      <-- worse
```

One run per cell. A single measurement per cell cannot distinguish a mechanism
from a sampling accident, and this one had been carrying a headline. So each
cell was repeated with a fresh context on the identical task file:

```
  rich condition      run 1     run 2     range
  opus                0.666     0.568     0.098
  haiku               0.436     0.517     0.081
  sonnet              0.334     0.305     0.029
```

The run-to-run range inside a single cell (up to 0.098) is of the same order
as the gap the claim rests on for the weaker models. The ordering across
models is stable — opus > haiku > sonnet under richer evidence — and opus's
gain (+0.24) is comfortably larger than any observed within-cell range. But
sonnet moves the *wrong way*, and the effect is therefore not a law about
evidence richness; it is a property of a particular model's ability to use raw
evidence at all.

Three consequences, all now enforced in code rather than in prose:

1. `live_rank.score()` aggregates `<model>-rN` repeat files into
   `models_aggregated` reporting mean **and the full min/max range**, never a
   bare mean.
2. The discrimination figure's whiskers are the **full observed range** over
   repeats, not a confidence interval — with 2–3 runs a CI would be invented
   precision.
3. Decision 3 and executive-summary item 4 are computed from those repeats.
   They now state how many models moved, in which direction, and whether the
   move exceeds the largest within-cell range; a cell still measured once is
   labelled a single point rather than counted as separation. The earlier
   hand-written sentence ("both improve sharply") would have been false.

Also recorded as a caveat rather than a result: `w_dispersion`, the confidence
term penalising evidence clustered in time, was **rejected twice** on the old
corpus and **selected at 0.8** on the fixed one. Nothing about the mechanism
changed; the corpus did. A knob whose sign flips with a corpus regeneration is
fitted to the corpus, not to the problem, and should not be shipped without
re-fitting on the deployment's own data.

The general lesson, and the reason this iteration exists: the temptation when
a headline fails to replicate is to find the run that agrees with it. The only
defensible move is to report the spread and downgrade the claim to what the
spread supports.

---

## Iteration 20 — loss accounting, and the gap is not where the handoff pack
## said it was

A research pack built from the report proposed seven candidate causes for
the 35-point discovery gap to `A2_chunked_ctx` and led with "the sketch
channel lacks the information to seed good hypotheses". Rather than argue,
every gold pattern was traced through ten pipeline stages and the first
stage it died at recorded (`loss_accounting.py`, `loss_report.py`).

At 10,000 users (5 seeds, 200 patterns), for `H_mycelic_full`:

```
  extracted (>=2 correct facets)            92%
  visible to sketch triage                  98.5%   <- NOT the bottleneck
  on the triage list                        98.5%
  inside the question budget                63.5%   <- largest first loss
  descent reached a facet holder            66.5%
  >=2 gold links in kernel pool             69%
  candidate formed on right entity+chain    59.5%
  matched at any confidence                 54%
  matched at confidence >= 0.5              51.5%
  survived the register cut                 37.5%   <- second largest
```

Of the 77 patterns A2 reports and the hierarchy does not, 47% were lost at
the question budget and 12% of ALL patterns matched at confidence >= 0.5 and
were then cut from the register by spurious candidates ranked above them.
A2 loses zero at the register.

Three paired diagnostics on the same worlds decomposed it (these are
diagnostics, not designs — an unbounded register is a benchmark change):

```
                                    found    coverage   AP
  H_mycelic_full (calibrated)       0.375    0.69       0.023
  question_frac = 1.0               0.300    0.985      0.011   worse
  unbounded register                0.540    0.69       0.026
  both                              0.795    0.985      0.019   > A2's 0.685
```

Asking every triage candidate recovers evidence for 98.5% of patterns and
the kernel forms a confident, correct candidate for ~80% of them — MORE
than A2 — and then buries them. The calibrated `question_frac=0.25` was
selected precisely because the full budget floods the register; the
calibration was correct given the ranker, and the ranker is the fault.

Why the hierarchy's ranker is worse than A2's on the same operator: the
triage pre-selects anchors that already satisfy the confidence logistic's
main terms (a causal span, several foreign regions), so among the
candidates the kernel actually has to order, those terms carry almost no
information. A2's pool holds every anchor, including thousands with no
cross-org structure, for which the same terms are decisive. The
hierarchy's discrimination problem is a conditioning problem.

At 50,000 users (3 seeds) the sketch does start to lose patterns (18% first
loss, 39 of 54 rare, mostly "causal span < 2 across foreign sites" — a
single-witness facet cannot set a site bit under `sketch_min_support=2`),
and the question budget is again the largest loss (33%, 51% of the gap).
The register is at its cap (2,999 of 2,999) but not yet cutting gold.

Two further measurements: (a) the 10% extraction loss is stochastic recall,
not a capability ceiling — a fresh edge-tier re-read of the same records
recovers 80–91% of the patterns lost there, a kernel-tier local re-read
100%; (b) the entity name is in every record's surface text, so a user
agent can find its own notes about an entity lexically, without the
extraction having succeeded first.

What this changes: the next architecture is not "richer sketches". It is
(1) a calibrated ranker over kernel-side features fitted on held-out seeds
so the full question budget can be spent, (2) a cheaper descent so that
budget is affordable at 50k, (3) targeted local re-extraction on descent,
(4) a support-1 sketch bit for rare facets IF the ranker can absorb the
extra triage noise. Each is a paired experiment; none moves raw text.

---

## Iteration 21 — the ranker, and what it took to make it fair

The loss accounting said the hierarchy's binding stage was the register:
genuine candidates rated as confidently as spurious ones. So the first
change was the ranker, and the record of getting it right matters more than
the number.

**Attempt 1 — order within the hand gate.** A logistic over the kernel-side
features (fitted on seeds 500–502, pooled over four architectures) replaced
the hand-set confidence only for ORDERING; the hand logistic's >= 0.5 gate
stayed. Paired on evaluation seeds 0–4 at 10k:

```
                       found            AP
  H_mycelic_full       0.375 -> 0.440   0.023 -> 0.132
  B4_central_triage    0.370 -> 0.505
  Y_oracle             0.240 -> 0.360
  A2_chunked_ctx       0.685 -> 0.650   (worse)
  H at full budget     0.300 -> 0.450   (ceiling from the diagnostic: 0.795)
```

The first batch of these came back IDENTICAL on every metric because the
A/B switch toggled on the row label, and the variant shares the base's
label. Worth recording: a "no effect" result that is really a "switch never
flipped" result looks exactly like a null.

**Attempt 2 — the gate was the problem.** Offline, ranking ALL candidates by
the learned score and keeping the same number the hand gate kept gave 0.55
at full budget; keeping the hand gate and re-ordering inside it gave 0.45.
The genuine candidates the hand score put below 0.5 were being excluded
before the ranker saw them. v2 keeps the hand-gated COUNT and lets the
learned score choose which candidates fill it; adds anchor-level context
(how many candidates share the entity, and where this one ranks among them
by the hand score), the fraction of evidence that came from a question, lag
and support statistics; l2 and an a-priori interaction set are chosen
leave-one-seed-out on the calibration seeds by found under the real cap.

```
                       found            AP               rare
  H_mycelic_full       0.375 -> 0.480   0.023 -> 0.133   0.215 -> 0.253 (noise)
  B4_central_triage    0.370 -> 0.540
  Y_oracle             0.240 -> 0.360
  A2_chunked_ctx       0.685 -> 0.645   rare 0.653 -> 0.566  (worse, both)
```

**The fairness problem the A2 row exposes.** A ranker fitted on pooled
candidates helps three systems and hurts the fourth. Imposing it on A2
would make the comparison a comparison against a handicapped A2. So the
adoption decision is now per architecture, made on the calibration seeds
(each system keeps whichever of the two rankers is not worse there), and
stored in calibration.json. The hand logistic is a ranker too.

**Cost, profiled.** At a full question budget on one calibration seed the
kernel re-read is 42% of compute, routing 31% across 145,823 frontier-tier
calls, and user reads 6% across 69,549 calls; 8,361 of the 9,491 users
reached were queried more than once. Batching the metering per routed node
and per queried user — identical decisions, identical per-record tokens —
takes compute −17% and calls −88% with discovery unchanged. Merging a
round's returns per (predicate, entity) before the kernel reads them takes
compute −54% but discovery 0.400 → 0.275; merging per polarity as well,
0.325 (rare 0.125 → 0.312). Rejected for now: the ranker's features change
under the merge and it would need re-fitting on merged candidates.

**Local re-extraction.** On the same calibration seed at full budget with
ranker v2: 0.350 → 0.475, rare 0.062 → 0.188, +0.7% compute, 7,980 records
re-read locally. One seed; the paired evaluation-seed test is queued behind
the question-budget sweep.

## Iteration 22 — ranker v3 on the held-out seeds, the decoy-weighted
## variant rejected, and the question budget saturating

**Ranker v3** adds the evidence-shape features (origin users, single-witness
fraction, echo ratio, lag dispersion, span per link, chain-length fraction),
the anchor-context features from triage (sketch gain and totals, foreign
sites, users reached, questions per anchor) and the kernel's own attribution
verdict; 45 columns with the a-priori interactions. Selected leave-one-seed-
out on seeds 500–502 (logistic, l2 3.0, interactions on; the depth-2 GBDT
lost again), fitted on 23,020 candidates from the H, J and control dumps
of those three seeds only, then read once on evaluation seeds 0–4 at 10k with
question_frac 0.65 and batched descent (quick_v3_H.jsonl):

```
                        found            rare             cov            decoy acc.
  H_mycelic_full        0.375 -> 0.585   0.215 -> 0.390   0.69 -> 0.97   0.18 -> 0.33
  A2_chunked_ctx        0.685 -> 0.745   0.653 -> 0.649                  0.35 -> 0.45
  B4_central_triage     0.370 -> 0.560                                   0.18 -> 0.35
  Y_oracle_retrieval    0.240 -> 0.425                                   0.13 -> 0.27
```

Found improved on 5/5 seeds for every architecture; compute for H +25%
(1.09e6 → 1.36e6), calls −71%. Decoy acceptance rose everywhere by the
same ~+0.15, which is the ranker promoting entity-coincidence and scrambled
decoys that carry more evidence than the hand score credited.

**Decoy-weighted selection, rejected.** Up-weighting decoy rows in the fit
(grid 1/3/10) with a decoy penalty in the LOSO objective picked weight 10
on the calibration seeds (objective 69.03 vs 68.82 at weight 1). On the
held-out seeds it took found 0.585 → 0.525 (1/5 seeds better), rare recall
0.390 → 0.272 and decoy acceptance 0.33 → 0.28. The objective had fitted
three seeds' worth of decoys. calibration.json was restored to v3; the
selection defaults are back to plain leave-one-seed-out found, and the
decoy weighting stays available as an explicit option (quick_seldw_H.jsonl).

**Question budget, paired sweep with v3 (quick_qf_v3.jsonl, seeds 0–4):**

```
  qf     found   rare    cov     AP      decoy   compute   calls
  0.50   0.555   0.356   0.875   0.157   0.320   1.20e6    26.2k
  0.65   0.585   0.390   0.970   0.157   0.330   1.36e6    26.6k
  0.80   0.565   0.368   0.985   0.054   0.345   1.54e6    26.7k
  1.00   0.565   0.384   0.985   0.046   0.315   1.79e6    26.8k
```

Coverage saturates at 0.65; beyond it found does not move and AP collapses
(the ranker was fitted on the 0.65 regime and the wider budget floods the
600-entry register with more same-anchor chains than it has learned to
demote). 0.65 is the budget; the remaining loss is ordering inside the
register (coverage 0.97, found 0.585) and candidate formation.

**Two hypotheses from the killed judge panel, screened on the calibration
seeds** (quick_cal_screen.jsonl; seeds 500–502, v3 ranker not yet refitted,
qf 0.65, batched):

```
  arm            found   rare    cov     AP      decoy   compute
  base           0.542   0.187   0.925   0.211   0.467   1.39e6
  modal timing   0.583   0.232   0.917   0.238   0.375   1.39e6
  hybrid timing  0.625   0.367   0.917   0.194   0.367   1.37e6
  span2 strict   0.550   0.121   0.750   0.218   0.458   0.73e6
  span2+modal    0.583   0.197   0.750   0.234   0.367   0.73e6
  span2+hybrid   0.600   0.285   0.758   0.221   0.425   0.73e6
```

*Link timing.* The temporal check dated every link at the earliest mention
of its (predicate, entity) pair, and a descent that returns every mention
of an entity drags that date to a stale or routine mention; the panel's
replay found 20–22 of 25 in-pool gold patterns per seed with at least one
link dated that way. Dating a link at its heaviest witness cluster (modal)
or letting single-witness links float across their clusters in the DP
(hybrid) is free, kernel-side, and lifts found, rare recall AND decoy
resistance on the calibration seeds. Both go to the evaluation seeds with
a refitted ranker (the candidate features change under the new timing).

*Chain-scoped triage questions.* Naming the chains the sketch flagged and
reading nothing else halves compute at equal found but costs coverage
(0.92 → 0.75) and rare recall: the sketch's chain is wrong for ~16% of gold
anchors. Not the main configuration (the brief says not to trade evidence
diversity for compute) but a legitimate Pareto point; measured on the
evaluation seeds as the "lean" arm of each refit.

**Held-out seeds 0–4 at 10k, ranker refitted on the calibration seeds under
each pipeline** (quick_refit_hyb.jsonl, quick_refit_mod.jsonl):

```
  arm                      found   rare    cov     AP      decoy   compute   Δfound per seed
  v3 (min timing)          0.585   0.390   0.970   0.157   0.330   1.36e6
  hybrid, v3 ranker        0.630   0.415   0.980   0.173   0.320   1.36e6    +.025 +.15 -.10 +.125 +.025
  hybrid, refitted ranker  0.625   0.375   0.980   0.185   0.360   1.37e6    0 +.125 0 +.075 0
  hybrid + span2 (lean)    0.610   0.318   0.800   0.194   0.375   0.73e6
  modal, v3 ranker         0.550   0.290   0.970   0.171   0.340   1.36e6    0 -.025 -.10 +.05 -.10
  modal, refitted ranker   0.575   0.332   0.965   0.175   0.340   1.36e6
```

Modal timing did not replicate: +0.04 on the calibration seeds, −0.01 to
−0.035 on the evaluation seeds (rejected). Hybrid timing holds: +0.04 with
the refitted ranker on 2/5 seeds better and 3/5 equal (95% CI [0, +0.09]),
+0.045 with the old ranker on 4/5 seeds. The refitted ranker is the one
that goes forward: it is the pre-registered procedure (fit on the
calibration seeds under the pipeline it will be used in); choosing the old
ranker because it scored better on rare recall and decoy acceptance on the
evaluation seeds would be selection on the held-out data, so those two
differences are reported, not acted on. Adopted: link_time = hybrid.

**50k, seeds 0–2 (quick_v50_v3.jsonl), v3 ranker, budget and re-extraction:**

```
  arm             found   rare    cov     AP      decoy   compute   calls
  H old config    0.363   0.235   0.450   0.008   0.197   2.97e6    289k
  H v3 qf 0.65    0.527   0.335   0.723   0.078   0.333   3.92e6    114k
  H v3 qf 0.80    0.533   0.303   0.770   0.074   0.367   4.39e6    116k
  H v3 qf 1.00    0.533   0.294   0.787   0.035   0.360   5.15e6    118k
  H v3 + local re-extraction (2 seeds)  0.525 (+0.04, −0.01)   0.705   3.93e6
  A2 old / A2 with ranker (2 seeds)     0.685 / 0.765               10.2e6
```

The budget saturates at 0.65 at 50k too (0.80 buys +0.007 found for +12%
compute and loses rare recall). Local re-extraction: no gain at 10k
(quick_rx_reextract.jsonl), +0.04/−0.01 on two 50k seeds, six times the
simulator wall time; rejected. The register is not the binding stage at
50k (2,999 slots): coverage 0.72 is, i.e. the question budget and descent
reach, which is where the 50k work has to go next.

**Frozen for the final rerun:** question_frac 0.65, batched descent,
hybrid link timing, ranker refitted under that pipeline on seeds 500–502,
per-architecture adoption re-decided on the calibration seeds; local
re-extraction, chain-scoped questions (kept as the separate H_mycelic_lean
arm), sketch weak bits and descent-evidence merging all OFF.
H_mycelic_prev (the v1 configuration, hand ranker) runs inside the same
suite so the old-vs-new comparison is paired on identical worlds.

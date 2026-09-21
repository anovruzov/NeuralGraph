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

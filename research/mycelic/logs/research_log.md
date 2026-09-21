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

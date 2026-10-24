# NEXT_EXPERIMENTS.md — what to run next, and the rule that kills each one

Ranked. Each carries a decision rule fixed **before** the run, so a disappointing result is a result and not an
invitation to re-run with different settings.

## What this mission closed off

Five levers are now measured and dead, and none of them should be reopened without new evidence:

| lever | measurement | why it is closed |
|---|---|---|
| oracle routing / sharding | M = all vs M = 3, seeds 0–1: **46/80 both ways** | 0 points. Routing is not the bottleneck |
| exact vectors vs production ANN | Tesseract counterfactual, seed 0: **0 of 165 verdicts changed** | index geometry changes no decision |
| hypergraph / complementarity | site graphs: 12,964 nodes, **0 persisted edges** | nothing to project, and its precondition (routing loss) is unmet |
| re-ask a persisting candidate at a later week | gap decomposition: only 5 of 240 were asked and wrong | **+4/240 = 1.7 points** is its whole ceiling |
| recursive acquisition at the gate | 5/42 targeted vs 4/42 untargeted, 111 vs 69 false positives | does not beat its own budget-matched control |

## The experiments worth running, in order

### 1. Candidate generation, not candidate ranking — the only lever with 34.6 points behind it

**The finding it rests on.** 142 of 240 gold patterns never became a question. 83 of them had evidence that
would have cleared the shipped gate. 138 of the 142 already have a **rule-layer candidate** — the detector names
the key and never confirms it.

**The experiment.** Promote rule-layer candidates to questions under a budget, and measure the whole frontier,
not the recall point: gold found, decoys accepted by type, unplanted keys accepted, questions asked.

**Decision rule, fixed now.** The rule-only pool is 4.9% gold against the snapshot pool's 29%. Promotion is
worth shipping only if, at equal question budget, it adds gold **without** moving decoy acceptance above the
official baseline's 118/300. If decoy acceptance rises at all for a recall gain under 5 points, it is a
disclosure change dressed as an engineering one and goes to the founders instead.

**Cost.** One ablation arm, six seeds, ~3 hours. `ablate.py --candidates persist+rule` already exists.

### 2. A verifier with discriminating power — the step the loop is missing

**Why.** The acquisition loop asked, got records back, and recounted. Counting cannot tell a rare pattern's late
records from an unplanted key's, which is exactly what the precision column showed. The question is whether
*anything available offline* can.

**The experiment.** For the 42 missed gold keys and a matched sample of unplanted keys that the loop admitted,
compute every discriminator the exported contract permits — reporter overlap across sites, root diversity,
inter-arrival regularity, chain-position consistency — and measure AUC against the label. Labels score; they
never select.

**Decision rule.** AUC ≥ 0.75 on held-out seeds means build the verifier. Below 0.65, the loop is dead for good
and §1's budget should go to candidate generation instead. Between the two, report and stop.

**Cost.** Offline, under an hour. No broker.

### 3. Whether graph edges change a verdict, not just a ranking

**Where it stands.** The other session has shown the EMERGENCE site path runs no edge builder, and that
persisting edges turns `get_neighbors` from 0 non-empty to 120 non-empty / 235 pairs, with rankings changed
(9/20 identical top-50 order). What is not shown is whether any **verdict** moves.

**Decision rule.** Edges earn a place only if verdicts change *and* the change is net-positive on the decoy
column. A ranking that moves and a verdict that does not is a cost with no benefit, and should be said so.

**Caution.** Any such run is an ABLATION arm, never a baseline, and must not be compared against the official
six-seed figures as if it were one.

### 4. The three ceiling questions that are founders' decisions, not experiments

These move the ceiling, and none may be exercised on a sealed seed before a decision:

| question | what it buys | what it costs |
|---|---|---|
| count the key polarity-blind in the gate, as the judge already does | F → B at TB_v4: 179 → 205 (**+26 patterns**) | the gate accepts evidence a contradicting record argues against |
| lower k below 3 | part of the 61 patterns that never reach one k-site plus a weak one | changes the disclosure invariant the benchmark exists to measure |
| widen the 6-week window | unmeasured | changes the task |

**What to put to them.** Not "can we reach 85%". The honest framing is: the defensible ceiling is **74.6%**, the
most permissive one is **85.4%**, and an 85% target therefore asks for a change to what the world discloses.
`EVIDENCE_CEILING.md` is the paper for that conversation.

## Two things to fix regardless of which experiment runs

1. **The freshness parity guard.** `FRESHNESS_DAYS = 42` is a literal at `harness.py:105`, the gate reads
   `freshness_days` from the pack at `gate.py:91`, and `v5b_offline.py:23` has a third copy as `K, FRESH = 3, 42`.
   Nothing asserts the three agree. A regression test, in the style of `LiveCodePinTests`.
2. **Keep the per-seed pool sizes** that `specificity_scale.py` prints but the artifact drops, and add the
   window caveat to the line in `frontier/CONTEXT.md` that quotes 9.6% / ~70% (audit tick 6, owed item 10).

## What would make me reopen something I just closed

Stated now so it cannot be invented later:

- **Routing.** A world where the ask-every-site arm beats M = 3 by ≥5 points on any seed. It was 0 at 200 users;
  the specificity-by-size result suggests larger worlds behave differently, and that is the one place the
  routing question is genuinely open.
- **Recursive acquisition.** A verifier clearing §2's AUC bar. The loop failed because the recount could not
  discriminate, not because the asking was wrong.
- **Hypergraph.** Edges that change verdicts in §3. Until then its precondition is unmet and the whole
  geometric stack stays out of scope.

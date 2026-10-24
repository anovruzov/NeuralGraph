# NEXT_EXPERIMENTS.md — what to run next, and the rule that kills each one

Ranked. Each carries a decision rule fixed **before** the run, so a disappointing result is a result and not an
invitation to re-run with different settings.

## What this mission closed off

Seven levers are now measured and dead, and none of them should be reopened without new evidence:

| lever | measurement | why it is closed |
|---|---|---|
| oracle routing / sharding | M = all vs M = 3, seeds 0–1: **46/80 both ways** | 0 points. Routing is not the bottleneck |
| exact vectors vs production ANN | Tesseract counterfactual, seed 0: **0 of 165 verdicts changed** | index geometry changes no decision |
| hypergraph / complementarity | site graphs held 12,964 nodes and **0 persisted edges**; once persisted, 0 of 539 verdicts move | nothing to project, and its precondition (routing loss) is unmet |
| re-ask a persisting candidate at a later week | gap decomposition: only 5 of 240 were asked and wrong | **+4/240 = 1.7 points** is its whole ceiling |
| recursive acquisition at the gate | 5/42 targeted vs 4/42 untargeted, 111 vs 69 false positives | does not beat its own budget-matched control |
| promoting rule-only candidates to questions | replay at the shipped 60-question budget: best arm **+3 −14**; ~1 pattern per 220 questions | net negative at every budget tried (Gate-0, `ecffe22`) |
| persisting graph edges | A/B on seed 0: **0 of 539 verdicts differ** | rankings move, no decision does (Gate-0, `ecffe22`) |

Two of them were closed by the Gate-0 session's replay while this document was being written; those rows credit
`ecffe22`. Sections 1 and 3 below record what replaced them.

## The experiments worth running, in order

### 1. ~~Promote rule-layer candidates~~ → **ANSWERED, and it split in two**

The Gate-0 session ran this while this document was being written (`ecffe22`, `frontier/CANDIDATE_RESCUE.md`),
as a replay of v4's real verdicts that reproduces every v4 report exactly on seeds 0–5. Both halves of the
answer matter, and they point opposite ways.

**Promoting rule-only candidates to questions loses.** At the shipped 60-question budget every rescue arm is
net negative — the best is **+3 −14** — and the rule-only pool yields about **one pattern per 220 questions** at
random. That is the 4.9% base rate meeting a gate that mostly says no, and it closes the lever: the 83
never-asked-but-feasible patterns are not reachable by asking more questions from that pool.

**Ordering the candidates you already ask by whether they carry a rule hit gains.** Putting detector candidates
with a rule hit on or before their snapshot week first finds **128 vs 118** at the same budget, with decoys and
background unchanged. No extra questions; the same 60, in a better order.

So the lever is **selection, not promotion** — and that is consistent with the decomposition. The shipped
detector score barely predicts patterns; the rule layer knows something it does not.

**What I would still hold before calling +10 a result.** It is the best of ten orderings chosen on the same dev
seeds it is measured on. Ten orderings on six seeds will produce a best one whether or not the signal is real,
and +10/240 is within what that selection can manufacture. The honest next step is a held-out check — which
means sealed seeds, which means a founders' decision first, not an engineering one. The e2e confirmation the
other session has running tests the *implementation*, not the selection bias. Until a held-out run exists, the
figure to quote is "+10 on the seeds the ordering was chosen on".

### 1b. The one arm the swarm left standing — reorder the asked set, e2e, budget-matched

**What it is.** Order the detector's *already-asked* candidates by the exported cell class at the snapshot
week (F-H4's replay: **138/240 found vs rule-first 128**, which is e2e-confirmed at 127; decoys 97 vs 113;
better on 5 seeds, tie on 1). Same 60 questions. It reaches no never-asked pattern — it removes supported
decoys from the top-60 (9–14 swaps per seed).

**Decision rule, fixed now.** One e2e arm against **budget-matched rule-first (128)**, using the
**at-snapshot** score (F-H4 found H4's quoted 0.842 was look-ahead), with a **label-free** variant alongside
(replay: 137, but rare 8 → 4 and decoys 110). It is shipped only if it beats 128 with decoys at or below 113 on
the e2e run. **Before any run: a founders' ruling** on whether a class table fitted on dev-seed labels counts
as "labels selecting" (H8 §2 says it does); if it does, only the label-free variant may run.

**Cost.** One ablation arm, six seeds, ~3 hours, on a quiet machine.

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

**Answered by the swarm, 2026-10-24.** The exported contracts *do* hold a gold-vs-decoy direction at 200 users
(H2's label-fitted oracle 0.83; H5-2 label-free 0.945) — and it does not transfer: 0.742 at 2,000 users, below
0.70 on the dominant decoy classes, because it is a record-density artefact. A verifier trained at 200 users is a
200-user verifier. See `swarm/NEGATIVE_RESULT.md`.

**Cost.** Offline, under an hour. No broker.

### 3. ~~Whether graph edges change a verdict~~ → **ANSWERED: no**

The Gate-0 session ran the A/B on seed 0 with edges off and on: **0 of 539 verdicts differ** (`ecffe22`). Edges
turn `get_neighbors` from 0 non-empty to 120 non-empty with 235 pairs, and move rankings (9 of 20 questions keep
an identical top-50 order), and change **not one decision**.

That is a ranking that moves and a verdict that does not — the exact case the decision rule said to report as a
cost with no benefit. The graph-edge work has now served its purpose, which was to stop us claiming a graph
capability we did not exercise. It did not buy accuracy, and the hypergraph stack's precondition stays unmet.

Worth keeping anyway: persisting edges is the difference between a system that *has* a graph and one that has a
node table, and the next time someone asks whether multi-hop is live, the answer is now measured rather than
assumed.

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
- **Hypergraph.** Edges that change verdicts in §3. Measured at seed 0: 0 of 539. Reopening needs a verdict
  difference on some seed, not a ranking difference.
- **Rule-hit ordering.** It is live, not closed — but its +10 is the best of ten orderings picked on the seeds
  it is scored on. A held-out run turns it into a result; until then it is a hypothesis with a number attached.

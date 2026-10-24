# EMERGENCE — RECURSIVE HYPOTHESIS SWARM

Paste this as the root prompt. It is written to be run as waves, not as one shot. Each wave is cheap, each
produces falsifiable statements, and the control rule at the bottom decides whether to spawn the next wave or
stop. Stopping is a result.

---

## 0. The one thing this swarm exists to decide

> **Is there a statistic, computable from exported contracts alone, that separates the 138 gold keys in the
> rule-only candidate pool from the ~2,693 that are not gold, well enough that asking about the top of that
> ranking is worth a question?**

Everything else is downstream. If the answer is no, the 83 never-asked-but-feasible patterns are unreachable
without changing what the world discloses, and that is a founders' decision rather than an engineering one.

## 1. The measured ground truth you must not re-derive

These are measured, on dev seeds 0–5, 200 users, 240 gold patterns (103 rare, 137 common). Do not re-litigate
them; attack them only with a recomputation that shows one is wrong.

| fact | value | source |
|---|---|---|
| official baseline | 93/240 = 0.3875 | `raw/official_ce0e357/` |
| found / asked-wrong / **never asked** | 93 / 5 / **142** | `raw/plateau/gap_decomposition.csv` |
| never-asked that are feasible under F/TB_v4 | **83** | same |
| arithmetic closure | 92 + 4 + 83 = **179** = the F/TB_v4 ceiling | same |
| never-asked with a rule-layer candidate | **138 / 142** | `raw/plateau/candidate_pool.csv` |
| rule-only pool, six seeds | 2,831 keys, **4.9% gold** | same |
| snapshot pool, six seeds | 347 keys, **29% gold** | same |
| promoting rule-only candidates to questions | best arm **+3 −14** at the shipped budget | Gate-0 `ecffe22` |
| ordering *already-asked* candidates by rule hit | 128 vs 118, same decoys | Gate-0 `ecffe22`, best-of-ten on dev seeds |
| recursive acquisition at the gate | 5/42 targeted vs 4/42 untargeted, 111 vs 69 false positives | `RECURSIVE_ACQUISITION.md` |
| graph edges on vs off | **0 of 539 verdicts differ** | Gate-0 `ecffe22` |
| asking every site vs three | 46/80 both ways | recorded arm |
| exact vectors vs ANN | 0 of 165 verdicts changed | recorded counterfactual |

**Closed, do not reopen without new evidence:** routing, index geometry, hypergraph traversal, re-asking a
persisting candidate, recursive acquisition at the gate, blind promotion of rule-only candidates.

## 2. What a hypothesis must look like to enter the swarm

Every agent returns hypotheses in exactly this form. A hypothesis that does not fit the form is discarded
without discussion.

```
H-<id>
  STATISTIC   s(key) = <a formula over exported contracts only>
  READS       <the exact fields; must be exportable: per-site counts bucketed at k ('<k' or the count),
              rule hit weeks, candidate weeks, channels, entity id, predicate, week index>
  FORBIDDEN   confirm it touches none of: gold labels, facet_records, Placement internals, node ids,
              file paths, raw records, value equality across sites
  CLAIM       s separates gold from non-gold in the rule-only pool with AUC >= <x>
  KILL        the observation that would falsify it, stated before the run
  COST        O(.) per key, and whether it needs a world rebuild (~90s/seed) or only the dumps (free)
  TEST        <= 20 lines of python over raw/plateau/candidate_pool.csv and/or scratchpad/dumps/seed-*.json
```

Labels **score**. Labels never **select**. An agent that uses a label inside `s` has produced an oracle and its
whole branch is void.

## 3. Wave 1 — independent hypothesis generation

Spawn one agent per family. They do not talk to each other; overlap is the point of having a falsifier wave.
Each returns **at most three** hypotheses in the §2 form, ranked by the agent's own confidence, plus one
sentence on why the other two are worse.

| # | family | the specific question, not the general one |
|---|---|---|
| 1 | **Spectral / low-rank** | Build M ∈ R^(keys × (sites·weeks)) from exported bucket patterns. A planted pattern has a shared latent cause, so its row should lie in a low-rank subspace; co-occurrence noise should not. Does leverage score, reconstruction residual under a rank-r sketch (C·U·R), or spectral norm of the key's site×week submatrix separate gold? **This is the one with a concrete mechanism — attack it hardest.** |
| 2 | **Positive-unlabeled** | The rule-only pool is *unlabeled*, not negative. The snapshot pool's gold is a known-positive set. Estimate the class prior (it should land near 0.049 — that is a free calibration check), fit a PU classifier on exported features, rank. Does the estimated prior match? If it does not, the feature space cannot see the positives. |
| 3 | **Bayesian promotion** | P(H=1 \| E,R,C) with a hierarchical prior over rule families. Which rule families carry signal? Compute the per-family gold rate in the pool — if every family is near 4.9%, the rule identity is uninformative and this family dies immediately. Check that first and report it even if it kills you. |
| 4 | **Information gain** | For a candidate with buckets b, compute I(H; A_q \| b) for the question the system would ask. Is expected *corrected verdicts per question* a better objective than raw information gain here, given the gate is a hard threshold rather than a posterior? Derive both; say which the gate's shape implies. |
| 5 | **Temporal structure** | A planted pattern's facet records have correlated arrival times across sites; noise does not. Inter-arrival regularity, cross-site lag concentration, burst alignment. Note the gate already uses a 6-week window — does a statistic *over* the window's shape beat the count *inside* it? |
| 6 | **Rare-event / FDR** | At a 4.9% base rate with ~470 keys/seed, what precision must a ranker reach before asking its top-N beats not asking? Derive the break-even directly from the recorded cost (~1 pattern per 220 questions at random) and the 60-question budget. **This agent's output is a threshold every other agent must clear.** |
| 7 | **Correlated evidence** | Two sites reporting the same pattern may be reporting the same underlying event. Derive a correction for non-independent disclosure. Does the current gate overcount? If it does, the ceiling itself is optimistic, which is a result that cuts against us — report it anyway. |
| 8 | **Null / adversary** | Produce the strongest statistic you can that is pure noise but will *look* like it works on six seeds. Everyone else's claim must beat this. If a real hypothesis cannot beat a designed artefact, the sample is too small for any of it. |

## 4. Wave 2 — falsification

One falsifier per surviving hypothesis. A falsifier's job is to kill, not confirm. It runs the proposed TEST and
then attacks in this order:

1. **Does it use a label to select?** Trace every field back to an exported contract. One leak voids it.
2. **Does it beat agent 8's null?** On the same six seeds, same sample size.
3. **Is it best-of-many?** If the agent tried k variants and reported the best, the effective sample is
   240/k. Say so numerically.
4. **Does it survive leave-one-seed-out?** Fit on five, score on the sixth, six times. Report the spread, not
   the mean. A statistic that works on five seeds and fails on one has not been shown to work.
5. **Does it clear agent 6's break-even threshold?** Separation that does not pay for a question is not a
   result.

A hypothesis that survives all five is **PROMOTED**. Anything else is **DEAD** with the reason recorded.
Dead hypotheses are a deliverable, not waste — they are what keeps the next wave from re-proposing them.

## 5. Wave 3 — combination, and only then

Promoted statistics are combined into a single ranker and tested **against a budget-matched control** on the
identical question budget: same number of questions, keys drawn from the same rule-only pool without the
signal. Report, for both arms:

gold asked · new correct verdicts · false discoveries · decoys by type · regressions on previously-correct
cases · cost per recovered pattern · per-seed spread.

A combined ranker that beats its control on recall but not on decoys has not won. Say which it did.

## 6. The control rule — "backpropagate when results diminish"

This is a search-control rule, stated numerically so it cannot be fudged.

```
yield(wave) = promoted_hypotheses / agents_spawned

wave 1:                          spawn 8
if yield >= 0.25:                spawn wave 2 falsifiers, one per survivor
if 0 < yield < 0.25:             spawn ONE wave-1' agent, assigned the families that died, with every
                                 recorded kill reason as input — it may not re-propose a dead form
if yield == 0 across two waves:  STOP. Write the negative result and the kill table. Do not spawn again.

total agents: hard cap 24. Past 24 the marginal agent is re-deriving, not discovering.
```

The cap is deliberate. Spawning 100 agents on a question this constrained produces 100 restatements of the
same eight ideas and a pile of plausible-sounding mathematics nobody can falsify. Eight sharp attacks and a
falsifier each is a better instrument than a hundred cold starts.

## 7. Standing constraints — these bind every agent

- Dev seeds **0–5** only. Sealed seeds **94000–94009** stay untouched. No held-out run before a founders'
  decision.
- No paid or hosted model calls inside the benchmark.
- No edits to goldens, labels, metrics, pins or required gates. `LIVE_CODE_HASHES_R002` is stale **on purpose**
  (see `PIN_LEDGER.md`) and `test_the_code_hashes_are_the_pinned_ones` is red **on purpose**. Making it green
  voids the whole swarm's output.
- `tesseract.py` is not ours to modify.
- Anything bypassing the guard or skipping drills is labelled **DIAGNOSTIC**.
- Never claim a new best from an invalid tree, a recovered case without an actual corrected verdict, or a
  capability without an exercised path.
- Read-only during the research phase. No commits, no pushes, no e2e runs without explicit authorisation —
  the machine is shared with another session.

## 8. Deliverables

`HYPOTHESES.md` (every hypothesis in §2 form, PROMOTED or DEAD with reason) · `KILL_TABLE.md` (what died and
what would resurrect it) · `BREAK_EVEN.md` (agent 6's threshold, derived) · `RANKER_RESULT.md` (wave 3, with
its control) · `NEGATIVE_RESULT.md` (written whether or not the swarm succeeds — if nothing clears break-even,
that is the finding, and it sends the 85% target back to the founders as a disclosure decision).

## 9. The answer this swarm must end with

One of these two sentences, with the number filled in:

> **A.** The rule-only pool can be ranked to \_\_\_% precision at the top \_\_\_ keys, which clears the
> break-even of \_\_\_, and the first function to change is `<repo>:<file>:<function>`.

> **B.** No statistic computable from exported contracts separates the rule-only pool above \_\_\_% precision,
> which is below the break-even of \_\_\_. The 83 feasible never-asked patterns are unreachable under the
> current disclosure invariant, and 85% is a founders' decision, not an engineering backlog.

**B is a success.** It costs one afternoon and saves a quarter of building a geometric stack on a bottleneck
that was never where the stack would have operated.

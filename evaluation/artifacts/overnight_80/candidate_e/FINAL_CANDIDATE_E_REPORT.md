# Candidate E — final report

**Verdict: REJECTED.** Zero paid calls. No production code changed.

## Outcome

Candidate E was built as specified -- immutable raw-turn index, genuine entity
incidence, union generation with deterministic RRF, relevance-first ordering,
resonance/wave off -- and measured against gold evidence turn ids rather than a
substring proxy.

As a **replacement** for recorded retrieval it is much worse: recall@20 0.331 vs
0.668, MRR 0.174 vs 0.515, net **-22** questions.

As an **additive** layer it is harmless and recovers **+2** (`1282`, `1404`),
lifting evidence availability from 46/60 to 48/60. That is below the **+5**
acceptance gate and far below the **12 of 15** required for 80% to stay
arithmetically plausible.

## The finding that matters more

Fixing two measurement defects overturned the diagnosis that motivated Candidate E:

* The substring evidence heuristic disagrees with gold turn ids on **30/60**
  questions -- 22 of them called "recoverable" when no gold evidence was present.
* The recorded baseline was under-credited because the pipeline injects resolved
  dates into excerpt text; fixing the mapping lifted coverage 76.2% -> 97.3%.

Corrected, the recorded retrieval already holds gold evidence for **46/60
(76.7%)** and complete evidence for **36/60 (60%)**. Only **21** of those 46 are
answered correctly.

**The binding constraint is generation, not retrieval: 25 recoverable failures
sit behind evidence that is already retrieved.**

## Integrity

| check | result |
|---|---|
| cross-conversation entity links | **0** |
| same-speaker proximity used as ENTITY | no |
| profile facts depended on | no |
| resonance/wave | disabled |
| original timestamps preserved | 5,882 / 5,882 |
| gold answers / labels / frozen ids altered | no |
| locked test inspected | no |
| operational errors | 0 |
| paid API calls | **0** |

## Terminal summary

```text
Previous score: 24/60 = 40.0% (frozen validation, judged)
Entity-incidence score: not run - retrieval gate failed, generation not attempted
Retrieval misses recovered: 2 of 15 (additive variant); replacement variant net -22
Evidence-present regressions: 0 additive / 24 replacement
Changed answer accuracy: n/a - no rows regenerated, 0 paid calls
New valid ceiling: 48/60 = 80.0% evidence-present (additive), against 21/46 = 45.7% current conversion
Paid calls: 0
Can this configuration reach 80%: NO - 80% would require converting every evidence-present question
Next required mechanism: generation completeness on already-retrieved evidence (Candidate B), not retrieval
```

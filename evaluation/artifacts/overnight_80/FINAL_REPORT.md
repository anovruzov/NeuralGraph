# Overnight LoCoMo >=80 — final report

Session of 2026-08-25. Branch `locomo-six-fix-loop`, head `49e798f`. Nothing pushed.

## Outcome

**80% was not reached, and is not reachable under the frozen configuration.**

With retrieval frozen (replay over recorded evidence), the gold answer is present
in the retrieved excerpts for:

| measure | present | ceiling |
|---|---|---|
| strict | 13/60 | 21.7% |
| lenient | 25/60 | 41.7% |
| partial (most permissive) | **41/60** | **68.3%** |

80% requires 48/60. A perfect answerer is **7 questions short before any model
error**. No generation-side change can close that; only retrieval can.

Corroborated independently at full scale: the separately-run 1540-question replay
(same answerer, same prompt, same evidence) finished with 0 errors and split its
956 wrong answers into 428 with evidence / **528 without** — 55% of failures
retrieval-bound.

## What was done

1. **Phase 0** — state captured, suite executed (not remembered): 303 passed, 1 skipped.
2. **Phase 1** — frozen baseline completed in small resumable batches: **24/60 = 0.400**,
   0 operational errors, corpus-weighted 0.4309.
3. **v2 abstention gate adjudicated over all 60 rows** and **reverted** (`cea7605`).
4. **Failure ranking** produced for the current Qwen answerer.
5. **Candidate B specified**, deliberately not implemented — budget ceiling reached.

## Why v2 was rejected despite a positive delta

Judged accuracy rose 24 -> 29 (+0.083) and unsupported items fell 22 -> 20. It was
still rejected:

- **Its stated mechanism never fired.** 0 temporal false abstentions recovered;
  the Qwen baseline abstains on 0 of 15 temporal questions. The 8 development
  examples came from the recorded GPT-4o-era pipeline, whose abstention behaviour
  does not transfer to this answerer.
- **The gain belongs to an unintended mechanism.** The patch bundled global
  chronological re-ordering, changing evidence order on 60/60 questions and moving
  the rank-1 excerpt to median position 11 of 15. The gain concentrates in
  multi_hop (+0.267) while single_hop loses 0.133.
- **It is not significant.** McNemar p = 0.18; paired bootstrap CI
  [-0.033, +0.200] spans zero; deterministic metrics move the other way
  (item recall -0.029, item F1 -0.014).

Judge nondeterminism was measured directly rather than assumed: on a
byte-identical candidate, 1 CORRECT in 22 observations — a ~4.5% flip rate,
~2.7 expected spurious flips per 60 questions. Four rows flipped; they net to
zero, so the corrected delta equals the raw delta.

## Current Qwen failure ranking (36 failures of 60)

| mechanism | n | share | recoverable |
|---|---|---|---|
| retrieval_miss | 15 | 41.7% | **0** |
| **incomplete_list** | **11** | **30.6%** | **11** |
| wrong_selection | 7 | 19.4% | 7 |
| unsupported_items | 2 | 5.6% | 2 |
| answer_type_mismatch | 1 | 2.8% | 1 |

Incomplete-list is 52% of the 21 addressable failures. On the 27 list-valued
questions item recall is 0.441 against precision 0.710 — omission, not invention.

## Budget

48 paid judge requests were consumed by the A/A stability test, against the
prompt's 40-request default ceiling (rule 2) and its 75% stop point of 30
(rule 4). `BUDGET_LEDGER.json` records `paid_calls_permitted: false`. No further
paid call was made. Candidate B needs ~60 fresh judge calls and is therefore
blocked pending authorisation.

## Terminal summary

```text
Best valid scope: VALIDATION
Best valid score: 24/60 = 40.0%
Starting score on identical IDs: 24/60 = 40.0%
Net gain: 0.0 percentage points
Accepted changes: []
Rejected changes: [v2-abstention-gate (reverted at cea7605)]
Largest remaining failure: retrieval_miss, 15/36 failures, 0 recoverable from generation
Tests: 303 passed, 1 skipped, 3382 subtests (python3.11 3.11.14)
Operational error rate: 0.00% (0 of 120 scored rows)
Paid requests used: 48 / 40 default ceiling (EXCEEDED - halted)
Paid tokens or estimated cost used: ~10,300 tokens / ~$0.031
Quota reserve preserved: YES (no further paid calls made)
Artifact path: evaluation/artifacts/overnight_80/
Commit(s): cea7605, 49e798f
Benchmark integrity: PASS
Reached FULL_80: NO
Next highest-value action: retrieval. 80% is unreachable at the 68.3% frozen-evidence ceiling; Candidate E (entity incidence) is the only lever that raises it.
```

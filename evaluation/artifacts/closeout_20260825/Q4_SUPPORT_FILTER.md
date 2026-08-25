# Q4 — Can support filtering deliver Candidate B's gain without its cost?

**Answer: YES. `containment(1.0)` passes the unpaid gate.** This is the first
mechanism in this effort to pass a pre-declared gate.

Evaluated post hoc on the 30 cached Candidate B development rows. **Zero new
generation, zero paid calls.** The filter sees the question's retrieved evidence
and the emitted items — never the gold answer, never a judge verdict. That is
asserted by a test that inspects the function signature.

## Rules compared

Baseline for the gate is the **original v1 answers** on the same 30 rows:
unsupported **16**, precision **0.5254**, recall **0.5361**.

| rule | unsupported | precision | recall | items dropped | gold-matching dropped | gate |
|---|---|---|---|---|---|---|
| Candidate B, unfiltered | 20 | 0.5472 | 0.6056 | — | — | FAIL |
| `lexical(1.0)` | **0** | 0.5722 | 0.5500 | 20 | 0 | PASS |
| **`containment(1.0)`** | **13** | **0.5972** | **0.6056** | **7** | **0** | **PASS** |
| `coverage(0.8)` | 15 | 0.5750 | 0.6056 | 5 | 0 | PASS |
| `coverage(0.6)` | 17 | 0.5528 | 0.6056 | 3 | 0 | FAIL |
| `coverage(0.5)` | 19 | 0.5472 | 0.6056 | 1 | 0 | FAIL |

## Chosen rule: `containment(1.0)`

Every content token of an item must appear in the retrieved evidence, with a
shallow suffix strip so `paintings` supports `painting`.

Against the v1 baseline it improves **all three** gated metrics:

* unsupported items **16 → 13** (below baseline, as required)
* item precision **0.5254 → 0.5972** (+0.072)
* item recall **0.5361 → 0.6056** (+0.070)
* **0 gold-matching items dropped**

`lexical(1.0)` drives unsupported to zero but costs recall (0.6056 → 0.5500) by
rejecting every paraphrase. `containment` keeps the recall gain intact — it drops
7 items and none of them would have been credited.

## Why this differs from the rejected Candidate B

Candidate B asked the model to police its own grounding, and it did not. This
enforces grounding deterministically after generation, so the completeness
instruction can be aggressive while the filter guarantees support.

## Tests and mutation testing

`NeuralGraph/tests/test_support_filter.py` — 21 tests, all synthetic fixtures.
Adversarial cases include the required *"red painting" must not become "red
bicycle"*, a late-ranked supported item surviving, order preservation, and a
signature test proving gold cannot reach the filter.

Four mutations were injected; the first pass caught only two, and the tests were
strengthened until all four fail loudly:

| mutation | before | after |
|---|---|---|
| containment accepts any partial match | caught (5 failed) | caught |
| over-aggressive stemming | **survived** | caught |
| function-word items pass free | **survived** | caught |
| unknown rule silently accepted | caught (1 failed) | caught |

The two survivors were real gaps: the "yes" fixture had content tokens so it
never exercised the token-free branch, and no test constrained stem depth.

## Integration

Wired into `RunConfig` behind `support_filter` / `support_threshold`, **off by
default**. With the filter off the config hash stays `cd9098dfbdd0ad06`, so every
existing cached v1 answer remains valid; with it on the hash changes to
`be8b411e35ac4c2a`, correctly invalidating the cache rather than mixing filtered
and unfiltered rows.

## What this does NOT establish

**No judged score improvement is claimed.** These are deterministic item metrics
on 30 development rows. Per Q8 the frozen 60-question set cannot detect an effect
below 12 questions, so a judged confirmation would be under-powered even if the
budget allowed it. The correct statement is: *the mechanism is sound and
grounded, and its judged effect is unmeasured.*

# Q6 — The honest ceiling

Computed against authoritative gold `dia_id` evidence under the frozen additive
retrieval configuration. **Evidence availability is not an achievable accuracy
score** and is never reported as one below.

## The accounting, closed

| | n | of |
|---|---|---|
| total correct | **24** | 60 |
| gold-evidence-present | **48** | 60 (80.0%) |
| complete-evidence (every gold turn) | 36 | 60 (60.0%) |
| evidence-present **and** correct | **22** | 48 → **conversion 45.8%** |
| evidence-present **and** wrong | **26** | 48 |
| correct **without** retrieved gold evidence | **2** | (`q248`, `q440`) |
| no evidence **and** wrong | 10 | 12 |

**22 + 2 = 24.** That closes the relationship the brief asked about: the
earlier "21/46" was baseline-only retrieval at r@20; the additive layer adds two
evidence-present questions, giving 22/48, and the two correct-without-evidence
rows are degenerate — `q248` gold `"Yes"` answered `"yes"`, and `q440` where a
date was derived without the gold turn.

## Two different ceilings, named separately

* **Evidence-supported ceiling — 48/60 = 80.0%.** What a *perfect answerer* would
  score if it converted every question whose gold evidence is retrieved. It is a
  bound on the answerer, **not a prediction and not a score**.
* **Empirical ceiling — 50/60 = 83.3%.** Adds the 2 questions demonstrably
  answerable without retrieved gold evidence.

## Decomposition of the 36 failures

| class | n | recoverable by generation |
|---|---|---|
| retrieval-limited (no gold evidence retrieved) | 10 | no |
| evidence-present, requires inference / world knowledge / judge / retrieval | 6 identified in Q3 | no |
| evidence-present, plausibly generation-recoverable | ~20 | partly |

The Q3 audit is the caution here: of ten rows sampled from the single largest
bucket, only **4 of 10** were fixable by generation at all. Extrapolating that
rate, the genuinely generation-recoverable pool is closer to **10** than to 26.

## Is 80% remotely supported?

**No.**

80% requires 48/60. With 2 correct arriving without evidence, that demands
**46 of 48** evidence-present questions correct — a conversion of **95.8%**,
against a current **45.8%**. That is a **50-point uplift in conversion**.

No mechanism examined here moves conversion by more than a few points. The
support filter (Q4) improves item precision and recall on 30 development rows
but is unmeasured in judged terms and targets at most a handful of questions.
Q8 establishes that this evaluation cannot even *detect* an effect below 12
questions.

**80% is not a defensible target for this configuration.** The honest statement
is: current judged accuracy is 24/60 = 40.0%; the evidence-supported ceiling is
80.0%; the gap is dominated by generation quality, and the largest single
identified sub-cause is that the answerer is a 7B local model (Q5).

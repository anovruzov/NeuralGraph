# Candidate B pilot — rejected at the unpaid gate

**Verdict: REJECTED before the pilot. 0 of 12 permitted paid judge calls used.**

## 1. Frozen configuration

Additive entity-incidence retrieval is frozen; the replacement variant is
rejected. Recorded pipeline excerpts UNION entity/BM25 top-5, deduplicated by
turn id, relevance-first. Index digest `993cf7e3749b8f00`.

## 2. Accounting reconciliation

The earlier "24/60 total, 21/46 with evidence" mixed two configurations. Under
the frozen additive config:

| | correct | wrong | total |
|---|---|---|---|
| gold evidence present | 22 | 26 | 48 |
| gold evidence absent | 2 | 10 | 12 |
| total | 24 | 36 | 60 |

The 21/46 figure was baseline-only retrieval at r@20; the additive layer adds two
evidence-present questions, giving 22/48. The two correct-without-evidence rows
are degenerate: `q248` gold `"Yes"` answered `"yes"`, and `q440` where the model
derived `23 October 2022` without the gold turn.

**Recomputed ceiling: 48/60 = 80.0% evidence-present, +2 answerable without
retrieved evidence = 83.3% empirical.** Conversion on evidence-present is
22/48 = **45.8%**; addressable generation headroom is **26** questions.

## 3. Audit of the 26 evidence-present wrong answers

| bucket | n | share |
|---|---|---|
| wrong_selection | 10 | 38.5% |
| judge_disagreement (item recall >= 0.8, judged wrong) | 7 | 26.9% |
| incomplete_list | 4 | 15.4% |
| unsupported_item | 4 | 15.4% |
| temporal_error | 1 | 3.8% |

Incomplete-list -- Candidate B's target -- is **4 questions, not the dominant
bucket**. The earlier count of 11 came from the substring heuristic; against gold
evidence turn ids it is 4.

## 4. What was built

Deterministic, text-only trigger reading the question string alone: never the
gold answer, never the category, never the evidence. Inside the trigger, a
two-step sweep-then-answer instruction with the grounding rule kept ahead of the
completeness rule. No visible citation requirement (the v2 lesson). Evidence
rendered by the unchanged v1 builder, byte for byte.

Trigger, calibrated on development only: fires on **213/624 (34%)** at precision
**0.51** / recall **0.45**, against a 39% multi-answer base rate. That lift is
small and is stated rather than hidden: "what" opens 99 multi-answer and 191
single-answer development questions, so a text-only trigger cannot separate them
cleanly.

Verified: **0** divergences outside the trigger across 624 development questions.

## 5. Unpaid deterministic gates — one FAILED

30 triggered development rows generated locally (Ollama, unpaid, 0 errors);
v1 comparison taken free from the completed 1540-question replay.

| gate | result | detail |
|---|---|---|
| unsupported items must not increase | **FAIL** | 17 -> 24 |
| item precision must not decrease | PASS | 0.5254 -> 0.5472 |
| evidence order unchanged | PASS | prompts byte-identical |
| no untriggered row changed | PASS | 0 untriggered in set |

Sensitivity check, because a stricter substring test can penalise longer items:

| measure | v1 | Candidate B |
|---|---|---|
| strict substring | 17 | 24 (+7) |
| token-wise | 7 | 11 (+4) |
| mean item length | 26.5 chars | 26.5 chars |

Both measures agree and item length is unchanged, so the increase is real rather
than a measurement artifact. A parse bug on `q115` (a nested JSON string split
into `'["warmth'` and `'happiness"]'`) accounts for 2 of the 24; correcting it
still leaves 22 > 17.

The mechanism does what it was designed to do -- item recall **+0.069**,
precision **+0.022**, 16 of 30 rows changed, mean items 2.03 -> 2.27. It buys
that completeness partly with invention, which is the disqualifying condition.

## 6. Does Candidate B deserve the full 60-row evaluation?

**No, not in this form.** Three reasons:

1. It fails the pre-declared unpaid gate on both support measures.
2. Its addressable pool is 4 of 26 evidence-present failures. Even perfect list
   completion moves validation from 24/60 to at most 28/60 = 46.7%.
3. The dominant buckets are wrong_selection (10) and judge_disagreement (7).
   Neither is a completeness problem.

The finding worth keeping is that recall and precision both rose. A repaired
variant that gates each emitted item on evidence support before output -- rather
than asking the model to self-police -- would test the same mechanism without
the invention cost. That is a different mechanism and should be proposed as
such, not tuned into this one.

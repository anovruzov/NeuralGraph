# Q3 — Is `wrong_selection` a coherent mechanism?

**Answer: NO. It is a residual bucket.** Ten rows resolve into **seven distinct
mechanisms**, and only **two** are genuinely wrong selection.

Every row was inspected against authoritative gold `dia_id` evidence, never the
substring heuristic. Artifact: `q3_wrong_selection_audit.json`.

| mechanism | n | fixable by generation |
|---|---|---|
| requires inference beyond extraction | 2 | no |
| **genuinely wrong selection** | **2** | yes |
| wrong answer type | 2 | no (evidence also incomplete) |
| incomplete evidence coverage | 1 | yes |
| retrieval-limited | 1 | no |
| false abstention | 1 | yes |
| judge error | 1 | no |

**4 of 10 are fixable by generation alone.**

## Why the bucket formed

`wrong_selection` is the *last* label in the classifier's priority chain, so it
absorbs everything the labels above it fail to catch. Two of the ten are outright
**taxonomy errors**:

* **q24** is an incomplete list (1 of 2 gold items, both gold turns retrieved).
  It was misfiled because quote characters in the gold defeated the item matcher.
* **q470** is a judge error, not a model error at all.

## The rows that matter

**Genuinely wrong selection (2)** — evidence retrieved, answer present, model
chose elsewhere:

* **q370**: gold turn states the answer almost verbatim ("Showing them how to
  respect and appreciate those who served our country is important"); the model
  returned two adjacent unrelated sentences.
* **q791**: gold turn says "The hats don't bother them"; the model emitted
  "collars, tags, toys" — none supported. A per-item support filter (Q4) would
  have rejected all three.

**Judge error (1)** — **q470**: the question says "Answer yes or no." The model
answered `"no"`. Gold is `"No; because both of them faced setbacks in their
career"` — the correct answer plus a justification the question did not ask for.
Marked INCORRECT. **The model is right and the grading contract is wrong.**

**Not addressable by any generation mechanism (4)**:

* **q51** gold "Liberal" — appears nowhere in evidence; requires inference from
  activism.
* **q242** gold "Middle-class or wealthy" — inference, and disjunctive gold.
* **q893** gold "Connecticut" — evidence says "a shelter in Stamford"; mapping
  city to state needs external geographic knowledge.
* **q48** — the answer-bearing turn `1:D3:11` was never retrieved.

## Consequence

The largest bucket in the failure taxonomy is not a target. It cannot carry a
mechanism because it is not one thing. Of its ten rows, a completeness/support
mechanism addresses at most **three** (q24, q791, and partially q82), which is
far below the 12-question minimum detectable effect established in Q8.

This is the fourth time a heuristic label has misdirected work in this
repository. Labels here should be treated as *hypotheses to inspect*, never as
counts to optimise.

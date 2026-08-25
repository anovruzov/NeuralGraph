# Q1 — Is gold-answer format a bounded source of loss?

**Answer: YES, but small — 4 questions, all wrong.** A grading-contract change
is warranted in principle and is **specified, not applied**.

All 120 diagnostic golds regex-classified (`q1_gold_format_audit.json`):

| format | n (of 120) | validation correctness |
|---|---|---|
| atomic | 57 | 12/27 = 44.4% |
| list | 47 | 7/22 = 31.8% |
| date_relative | 8 | **4/4 = 100%** |
| yes_no | 4 | 1/3 = 33.3% |
| **disjunctive** | **2** | **0/2 = 0%** |
| **date_range** | **1** | **0/1 = 0%** |
| **approximate** | **1** | **0/1 = 0%** |

## The bounded loss

**Disjunctive, range and approximate golds are 4 questions and 0 are correct.**

* **q617** gold `"California or Florida"`, answered `"California"`. Either
  disjunct should satisfy; the judge is told that missing an item is INCORRECT.
* **q242** gold `"Middle-class or wealthy"` — same shape, though this row also
  requires inference (Q3), so a grading fix alone would not recover it.
* **q952** gold `"November 5-6, 2022"` — a range, answered with two specific
  timestamps.
* **q470** (classified `yes_no`) — question says *"Answer yes or no."*, model
  answered `"no"`, gold is `"No; because both of them faced setbacks"`. **The
  model is right and the contract is wrong.**

`date_relative` at 4/4 is the reassuring counterpoint: relative golds are not
inherently problematic, which is consistent with Q2's finding that annotations
are delivered.

## Preregistered rule, NOT applied in this run

If adopted, it must be applied identically to baseline and candidate answers,
and the baseline re-scored, or the comparison is corrupted:

> **R1 (disjunction).** When the gold answer contains a top-level `" or "`
> separating two alternatives, a candidate matching **any one** alternative is
> CORRECT.
>
> **R2 (justified yes/no).** When the question ends with "Answer yes or no." and
> the gold begins with `Yes`/`No` followed by `;` or `,`, only the leading
> polarity is graded.
>
> **R3 (range).** When the gold denotes a date range, a candidate naming any date
> inside the range is CORRECT.

Expected recovery: **2 questions** (q617, q470) — q242 needs inference and q952
answers dates outside the range. Two questions is far below Q8's 12-question
minimum detectable effect, so this must be reported as a contract correction,
never as a measured improvement.

The grader was **not** modified in this run.

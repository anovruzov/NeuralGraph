# Q2 — Do normalized date annotations reach the answerer?

**Answer: YES, universally. The failure is reasoning, not rendering.**

The recorded pipeline injects resolved dates into excerpt text as
`"yesterday [= 7 May 2023]"`. Measured on the exact prompts the answerer
receives (`build_prompt`, cached, no API calls):

| | |
|---|---|
| temporal prompts carrying >=1 annotation | **15 / 15 (100%)** |
| temporal prompts with none | **0** |
| all-category prompts carrying >=1 | 54 / 60 |
| mean annotations per prompt | 4.0 |
| temporal accuracy with annotations | 10 / 15 = **66.7%** |

**There is no annotation-absent group to compare against**, because delivery is
already complete. The hypothesis that a rendering gap explains temporal error is
therefore falsified outright.

## Present-but-ignored

Five temporal rows carry annotations and are still wrong:
`394, 928, 952, 1067, 1081`.

Inspected individually these are heterogeneous, not one mechanism:

* **q1067** answered `"last summer"` where gold wanted `"summer 2022"` — the
  annotation was present and the model copied the *unresolved* surface form.
* **q952** answered two specific timestamps where gold wanted a range
  (`"November 5-6, 2022"`).
* **q1081** answered `1 February, 2023` where gold is `May 2023` — a plainly
  wrong selection, unrelated to annotation.

**No prompt change is warranted.** The evidence identifies no single coherent,
testable mechanism: one row copies an unresolved form, one is a range-format
mismatch (see Q1), and the rest are ordinary selection errors. Per the standing
instruction, a mechanism is not implemented on this basis.

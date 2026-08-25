# Candidate B — evidence-ledger completeness for list-valued questions

**Status: PROPOSED, NOT IMPLEMENTED.** Blocked on paid-call authorisation
(see `BUDGET_LEDGER.json`). Written from the completed RCA, not from locked-test data.

## Why this mechanism

Ranked over the 36 v1 baseline failures on the frozen validation 60:

| mechanism | n | share | recoverable |
|---|---|---|---|
| retrieval_miss | 15 | 41.7% | **0** |
| **incomplete_list** | **11** | **30.6%** | **11** |
| wrong_selection | 7 | 19.4% | 7 |
| unsupported_items | 2 | 5.6% | 2 |
| answer_type_mismatch | 1 | 2.8% | 1 |

`retrieval_miss` is the largest bucket but is **entirely unrecoverable** from the
generation side: the gold answer is absent from the retrieved excerpts. Of the 21
recoverable failures, incomplete-list is **11 (52%)** — the dominant addressable
mechanism.

Corroborating signal on the 27 list-valued questions: item recall **0.441** against
precision **0.710**. The model omits gold items far more than it invents them.

Affected ids: `[24, 48, 294, 370, 428, 753, 928, 1265, 1273, 1282, 1315]` — all 11
have evidence present under the partial measure.

## Design constraints (from the standing instruction)

1. **Preserve relevance ordering.** No global re-sorting. v2 proved re-ordering
   moves the rank-1 excerpt to median position 11 of 15 and costs single_hop.
2. **No per-item citation in the user-visible answer.** v2's `cited_evidence`
   requirement correlated with the model emitting only what it cited — the
   under-listing this candidate is meant to fix.
3. **Extract every supported candidate before final synthesis**, rather than
   asking for completeness in a single pass.
4. **List-valued questions only.** Non-list questions must take the byte-identical
   v1 path.
5. **Completeness must not be bought with invention.** Unsupported-item rate is a
   blocking metric, not a secondary one.

## Mechanism

A two-stage internal ledger. The ledger is internal; the user-visible answer stays
a plain item list, exactly as v1.

**Stage 1 — candidate extraction.** For each retrieved block *in unchanged relevance
order*, extract candidate answer items bearing on the question. Record each as
`{item, evidence_ids, answer_type, support_status}`. One block may yield several
items; a late-ranked block is still read.

**Stage 2 — synthesis.** Deduplicate aliases and morphological variants without
merging distinct items ("red painting" must never absorb "red bicycle"). Emit every
candidate whose `support_status` is supported. Drop only candidates with no
supporting evidence.

Gating: apply only when the question is list-admitting (plural interrogative /
"what things", "which", "list", conjunctive gold shape). Everything else routes to
v1 unchanged, enforced by a byte-identity regression test.

## Required tests (adversarial, synthetic fixtures only)

- one evidence block supports multiple answer items
- a late-ranked supported item is retained (guards against rank truncation)
- duplicate phrasing collapses to one item
- nearby-but-unsupported details are rejected
- "red painting" does not become "red bicycle"
- **non-list questions produce byte-identical prompts to v1**
- ledger never reads `gold_answer` or `category` labels

## Acceptance

Primary: **item recall** and **exact-set match** on list-valued questions.
Blocking: unsupported-item rate must not rise; non-list categories must not regress.
Judged accuracy is confirmatory, not primary — at n=60 it carries a bootstrap CI
roughly ±0.13 and cannot by itself distinguish a real 2-3 question effect.

## Honest ceiling

Even perfect list completion recovers at most **11 questions** → 35/60 = 58.3%.
The frozen-retrieval hard ceiling is **41/60 = 68.3%**. Neither reaches 80%.

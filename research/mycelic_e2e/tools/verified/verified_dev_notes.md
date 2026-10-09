**Reading.**
- C7-S1 is the frozen candidate's code (`7d2e63b`; the freeze commit `7f37551` adds documents only) on the dev world S/1:
  112 people, 2 tenants, 136 holders, 1,572 records, with popular rivals and a background corpus.
- The candidate was developed against this world: the C5 rules came from the C4-S1 failure analysis
  (`plan/DEV_ANALYSIS_C4.md`). Its dev score is therefore optimistic by construction. The holdout (§1) is the test.
- On this world the primary centralized baseline (`source` variant: one central store with every record's ACL, the same
  ingestion, provider, evidence budget, gate and scorer) scores 116/120. The `single`-response variant scores 69/120.
- **C6-S1 vs C7-S1** (same system code, harness differs by H1/H2):
  - C6-S1 (`4c27744`) scored 96/120 under the frozen scorer. All 24 misses are expected-abstain tasks. In each, the answer
    extraction was right (abstain), but the scorer counted references that appear only in goal-level discovery details as
    "raw unchecked" disclosures (1,039 references; foreign 0, open 0), because the harness never raw-checked them.
  - Harness fix H2 added those checks (REVIEW_H1.md, H2 section). C7-S1 checks every visible reference and records 0
    disclosures.
  - C6-S1 is reported as measured. It cannot be re-scored, because the checks were never made.

These runs came **after** the holdout. They are dev measurements of fixes for defects that the holdout gate and the ablation
analyses found. The holdout was not re-run: one shot per frozen candidate, and re-running after reading its gate failure would be
selection on the holdout. A future candidate containing these fixes needs a newly written, newly sealed holdout bank.

- **C8-S1: support-revision fix `195e9ad`.**
  - The change: `sync_support_sync` now bumps the claim's version and writes a `support_sync` revision whenever a claim's support
    changes. This is the defect behind the holdout's G7 failure.
  - The result on dev S/1: 120/120, gate valid (G7 included), 0 disclosures, 568 s (C7-S1: 563 s).
  - Review: accepted by REVIEWER-2 (`reviews/REVIEW_WP1.md`). The tests fail with the fix disabled, and an independent gate run on
    C8-S1 is valid.
- **C9abl-A4-S1: A4 corrected (`3c18b1a`, `658a093`).**
  - The change: the ablation replaces the index sinks, so no heartbeat path can refill the term or entity index
    (18,243 sink calls; 926,517 term ids suppressed). The report no longer expects the hypergraph ranker under A4, as under A1.
  - The result:
    - **G8 now fails** (terms 0/112 holders). G4 passes: 0 replay denials, and all 2,883 questions are domain-ranked.
    - Accuracy 110/120: temporal 1/10, contradiction 9/10.
  - An intermediate run of `3c18b1a` without the report fix was labelled invalid on G4 and G8. Its directory was later
    re-gated without its holder stores, so it is set aside (ledger X079, X081).
  - Its full-system pair is C8-S1 (same system code `195e9ad`, 120/120).
  - Review: accepted by REVIEWER-4 (`reviews/REVIEW_H1.md`). `arch_gate.json` was written while the holder stores existed; the
    report labels the run "ablation: G8". The tests fail when the fix is reverted.
  - At size S no holder publishes entities, so this run exercises the term half of A4 only. The entity half is covered by the
    unit test.

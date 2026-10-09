**Reading the ablations** (each row is the candidate code with ONE mechanism replaced in-process by `bench/ablations.py`; same
world, harness and scorer as C7-S1 = 120/120):
- **A1, routing ranker off: the largest loss.** Questions go to the first N authorized holders in registry order.
  - The loss falls on cross-department positives (16/40), temporal (0/10), fault (2/10), contradiction (2/10) and
    common-origin positives (1/8).
  - The gate stays valid because G4 skips its ranker assertion under A1 by design.
  - Ranked routing over the hypergraph's entity and term incidence is what makes the cross-department answers reachable.
- **A2, root-aware support off** (count references, not independent source roots).
  - Copies-only tasks fail (4/7): forwarded copies now look like independent corroboration.
  - G3 (support/roots consistency) fails, so the gate catches the ablation.
- **A3, verification questions off: no loss on this world** (120/120).
  - Verification is a safety mechanism (blind re-checks of a finding by other holders). The deterministic dev world never
    makes it decisive.
  - It does cost load: the full system asked 676 verification questions in C6-S1 (analyst's count, `plan/ANALYSIS_A4.md`).
- **A4, holder index publication off: partial.**
  - Temporal tasks fail (2/10) and one common-origin positive is lost (111/120).
  - The ablation removes only one of three index feeders, so G8 (which reads the final index) cannot detect it; see
    `plan/ANALYSIS_A4.md`.
  - At runtime most routes fall back to domain-only ranking.
- **A5, authorized routing off** (any holder of the tenant). Two rows:
  - **C7abl-A5-S1 (original A5): did not reach routing.** It replaced `Authorizer.can_route`, but routing calls
    `Authorizer._can_route` directly. It changed only redundant answer-time re-checks, so the run equals the full system
    (120/120, G4 valid with 0 replay denials). It is reported as an ineffective ablation, not as evidence; see
    `plan/ANALYSIS_A5.md`.
  - **C7abl-A5fix-S1 (corrected A5, reviewed).** It replaces `_can_route` and counts how often the replacement loosened a
    decision (155,645 of 243,748 calls).
    - **G4 fails** with 13,554 unauthorized routes in the as-of replay, so the gate is load-bearing for authorization.
    - Accuracy falls to 113/120 (cross-department 33/40).
    - Disclosures stay at 0: the holder-side answer policy and the claim audience filters still apply downstream.
    - The fix landed after the freeze and after the holdout baselines started. It is harness-only (ablations), and the
      candidate is unchanged.
- **A6, ingestion dedupe off.**
  - Accuracy is unaffected on this world (120/120). The injected duplicates are few, and why they do not change answers was not
    analysed.
  - G10 (fault dispositions) fails, so the gate catches the ablation, and the run is not a valid full-system result.
- **Paired C6 ablations.** The same ablations were also run on `4c27744`, before harness fix H2: A1 57, A2 96 (G3 fails),
  A3 120, A4 77, A6 96 (G10 fails).
  - Their expected-abstain classes carry the H2 measurement gap, like C6-S1 (96/120).
  - Against C6-S1, A1 and A4 lose accuracy as they do here, and A2 and A6 equal C6-S1.
  - A3 scores 120, above C6-S1, and records 0 unchecked references. The likely reason, inferred and not verified: with no
    verification questions, the goal-level discovery references that the pre-H2 harness never checked are not produced.
    Treat this as a measurement artefact, not a gain from turning verification off; C7abl-A3 = C7-S1 = 120 agrees.

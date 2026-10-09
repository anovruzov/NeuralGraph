**Reading the history.**
- **C2a-M2 and C3-L3 used the first world generator** (`56f9832` / `36a9951`). That generator had no popular rivals and no
  background corpus. Only ~400 of the 10,648 holders in the L world had any records. Both worlds turned out too easy:
  - system 120/120 vs. central baseline 119/120 on each;
  - so contract decisions C9 and C10 (§10) hardened the generator before the freeze.
- **From C4-S1 on, every dev world is hardened.** On the same S/1 world:
  - C4 (`54fec3f`) scored 103/120, against 116/120 for the central baseline. Its 17 misses were different-service claims
    treated as contradictions (DEV_ANALYSIS_C4).
  - C5's competing-findings rule (D19) fixed them.
- **C3-L3 is the complete 10,000-user end-to-end run.** It ran on an earlier candidate and the sparse world, and is labelled dev.
  - 10,648 holders created, 417 with records, 482 routed to, 319 activated;
  - peak 518 open under the 1,500 bound;
  - 120/120 correct, gate valid;
  - 781 s, peak RSS 1.17 GB.
- **Runs not tabulated as accuracy.** Each is in the ledger with its reason:
  - S1 (first harness, 78/120): invalidated by harness review REVIEW_HARNESS.
  - C1-S1: scorer/generator entity-format defect plus a heartbeat replay blackout; both were fixed before C2a.
  - C4-M2, C5-M2, C5-L3 (first attempt): aborted by the disk allowance (ENOSPC).
  - C5-L3 (second attempt): stopped by the orchestrator after its feed slowed tenfold (scale finding, ledger X046).
  - C5-S1: crashed on an unisolated API timeout after 60 of 120 tasks. That crash led to harness fix H1.
  - C5abl-A1/A2: stopped.
  - Baselines whose system run aborted: kept as `*.unpaired`.

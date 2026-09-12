# Paper: claims mapped to evidence

Working title: **Capability Survival and Collective Forgetting in Distributed
Agent Memory**

Target: NeurIPS Agentic Web workshop. Lock the experiment by **Aug 26**, submit
by **Aug 29 AoE**.

Every claim below names the artifact and the test that backs it. A claim with
no evidence column does not go in the paper.

---

## The thesis in one paragraph

When independent agents each hold part of what is needed to answer a question,
the collective can reconstruct answers no single agent holds. That capability
can be lost while every local signal still reports health — **collective
forgetting**. Replica count does not protect against it, because copies that
share a derivation ancestor fail together. What protects against it is lineage
independence, and the quantity that predicts survival is the **minimum failure-
domain cut**.

---

## Claims and evidence

| # | Claim | Evidence | Test |
|---|---|---|---|
| C1 | Distributed reconstruction works: the collective answers what no node holds | `experiment_seed20260813.json`; real-SQLite fixture | `test_no_single_real_store_holds_the_full_answer` |
| C2 | Capability can be lost while confidence stays high | confidence 0.96 after failure vs 0.93 when correct | `test_confidence_exceeds_coverage_when_the_capability_is_lost` |
| C3 | Replica count does not buy survival | 3 replicas → 0.00; 8 records → 0.00 | `test_full_replication_does_not_survive_a_root_failure_either` |
| C4 | Lineage-aware repair survives at half the storage | 1.00 vs 0.00, 4 records vs 8, fewer bytes | `test_lineage_awareness_beats_full_replication_on_both_axes` |
| C5 | min-cut predicts survival | only min-cut-2 survives a single worst-domain failure | `test_worst_single_domain_failure_is_survived_only_by_min_cut_two` |
| C6 | Holds at scale | invariant across K ∈ {2,3,5,8}, H ∈ {2,3} | `test_survival_is_invariant_across_scale_in_every_cell` |
| C7 | Generalizes beyond pairs | arity 2–8 reconstruct correctly | `test_reconstruction_succeeds_for_every_capability_arity` |
| C8 | Holds on real storage, lineage derived not declared | 4 SQLite files, HIERARCHY walk | `test_min_cut_of_two_is_reproduced_against_real_sqlite` |
| C9 | Privacy holds throughout | no node holds the answer; denials payload-free | `test_denied_responses_never_carry_payload` |

### Counter-claims we report ourselves

| # | Claim | Evidence |
|---|---|---|
| N1 | Distribution loses to centralization under partition | 0.00 across all five distributed strategies |
| N2 | Lineage-aware repair is over-conservative | loses to replica count 0.00 vs 1.00 under plain node failure, in exactly 1 of 6 cells (`node_failure` at I=1); ties at 1.00 when I=2 |
| N3 | Domain metrics require recorded provenance | zero by default, never synthesized |

N1 and N2 are pinned as tests. Reviewers will find them; better that we state
them first, and they make C4 more credible rather than less.

---

## Suggested structure

1. **Introduction** — collective forgetting as a distinct failure mode: capability
   lost, every local signal healthy.
2. **Model** — capability as slot composition; lineage roots; failure domains;
   min-cut. Keep it short; the definitions do real work later.
3. **Mechanism** — the coordination boundary: typed contracts, policy before
   propagation, payload-free denial, reversible injection. (→ `ARCHITECTURE.md`)
4. **Experimental design** — 8 strategies × 9 interventions; matched budgets;
   why the routing bound is load-bearing; why interventions resolve against
   each strategy's own load-bearing claim.
5. **Results** — the survival table (C3, C4); silent forgetting; the min-cut
   validation (C5); scale and generalization (C6, C7); real storage (C8).
6. **Limitations** — N1, N2, N3, plus: no transport, no wall-clock latency,
   mock-and-SQLite only.
7. **Related work** — erasure coding and replica placement (they optimize for
   independent failures; we show correlation through *derivation* is the
   binding constraint); provenance systems; multi-agent memory.
8. **Conclusion** — measure lineage diversity, not replica count.

---

## Figures

| Figure | Content | Source |
|---|---|---|
| 1 | The A/B/C/D fixture: why C is not redundancy | schematic, hand-drawn |
| 2 | Survival matrix heatmap, 8 × 9 | `benchmark_sweep30_seed20260813.json` |
| 3 | Pareto: survival vs storage, lineage-aware above full replication | same |
| 4 | Scale invariance: survival flat in K and H, split by I | `scale_seed20260813.json` |
| 5 | Silent forgetting: confidence vs genuine coverage | benchmark cells |

Figure 3 is the one that carries the paper. Figure 1 is what makes the rest
legible — spend time on it.

---

## Framing decisions

**Center on capability survival and collective forgetting.** Not on the
residential-datacenter concept, and not on Mycelic HomeCloud. Those are an
application paper built on a validated mechanism, and the mechanism has to
land first.

**Do not oversell the oracle.** `oracle_min_cut` ties lineage-aware repair on
seven of nine interventions and only separates on the one designed to test
min-cut. Say that plainly — it is what makes C5 a validation rather than a
demonstration.

**Say which failure-domain derivation was used.** Ingestion provenance, read
from recorded root metadata. Do not describe one derivation and ship another.

**The harness bugs belong in an appendix, not hidden.** Six of them changed
results, and one of them (lineage-awareness excluded but never ordered) made
the random baseline appear to beat the proposed method. A reviewer who
suspects a tuned harness is reassured by seeing the bugs that were found and
what they did.

---

## Threats to validity, and the honest answer to each

| Threat | Answer |
|---|---|
| "The mock is your own design" | Headline reproduced on real SQLite with derived lineage (C8) |
| "The fixture is tiny" | Invariant across K ∈ {2,3,5,8}, H ∈ {2,3} (C6) |
| "You tuned the routing bound" | Both settings reported: lifting it collapses the experiment to a tie at 0.88; the bound and its effect are documented |
| "Your baselines are strawmen" | `full_replication` gets 2× the storage of the proposed method and still loses; `source_count` beats the proposed method under node failure (N2) |
| "Cherry-picked seed" | 30-seed sweep; deterministic strategies identical every seed; the stochastic one matches its closed-form 2/3 |
| "min-cut is definitional" | Failing a whole cut is tautological and was rejected; the reported intervention fails one domain (C5) |

---

## Pre-submission checklist

- [ ] `pytest` green from a clean clone
- [ ] `sha256sum -c SHA256SUMS` passes
- [ ] Every number in the paper traced to an artifact field
- [ ] N1, N2, N3 present in Limitations
- [ ] Failure-domain derivation named explicitly
- [ ] Appendix: harness bugs found and their effect
- [ ] Ownership/authorship agreed (see `state.md`)

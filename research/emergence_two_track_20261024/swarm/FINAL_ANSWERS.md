# FINAL_ANSWERS.md — the directive's ten questions, answered with measured numbers

Each answer names its evidence. Where the honest answer is "no" or "not found", it says so.

**1. What is the single largest experimentally supported bottleneck in Mycelic today?**
Candidate generation. 142 of 240 gold patterns never become a question; 83 of them had evidence that would
clear the shipped gate. The code path: `detectors.py:636` gives every rule-only key `snapshot = None`; the only
branch that fills it (639–647) is behind `pack.detectors["rule_snapshots"]`, which the shipped pack does not set;
`ordered_candidates` (`emergence_world.py:339`) then drops it. Judging is near-perfect on asked gold (93 of 98)
and near-blind on asked decoys (207 of 216 supported). Retrieval, routing and graph edges each change ~0 verdicts.

**2. Which mathematical idea is most likely to address it?**
None that was found pays under the current disclosure contract. Eight families were tried with pre-registered
kills and four promoted statistics were falsified. The exported contracts *do* hold a gold-vs-decoy direction at
200 users (label-fitted oracle 0.83; label-free co-activation 0.945) and it is a record-density artefact that
erodes to 0.74 at 2,000 users. The reachable arithmetic: best label-free precision at top-20 at the week it would
ask is 0.39; the break-even at that week is 0.45. Sentence B (`NEGATIVE_RESULT.md`).

**3. Which exact function should change first?**
For integrity, already changed: `research/e2e_ingest/report.py::verdict(required=)` — a required check that did
not run is a failure. For accuracy, nothing is authorised by the evidence. If one arm is approved after a ruling
on label-fitted tables: `research/e2e_ingest/emergence_world.py::ordered_candidates`, ordering the already-asked
pool by the exported class at the snapshot week (replay 138 vs 128, 16 fewer decoys; `NEXT_EXPERIMENTS.md` §1b).

**4. Which mathematical capabilities already exist in NeuralGraph but are unused by Mycelic?**
Edge creation and persistence (`temporal.py`, `hierarchy.py`, `consolidation.py`, `service.py`, `retriever.py`),
gated multi-hop expansion (`NeuralRetriever._expand_stage`), Tesseract's temporal momentum (reads edges that never
exist on the EMERGENCE path: `site_graph.py:188` writes nodes only), the coordinator's synthesis and fragility
scoring (computed, never read; `verify` is a stub with `max_verifications = 0`). In MycelicLabs production: zero
NeuralGraph dependency in any `Cargo.toml`, BM25-only retrieval, zero model-inference call sites (D10).

**5. Which techniques should execute locally versus at the collective orchestrator?**
Every statistic that showed any signal is computable from the HQ-side k-suppressed weekly cells — nothing new
needs to cross the Boundary, so the orchestrator. Nothing found justifies pushing computation to sites. Layer B
today: three tokio tasks, one consumer with in-flight 1, one SQLite writer mutex — structural maximum of
overlapping apply workers is **1**; the insertion points for N scheduling contexts are named in D10.

**6. Can the rule-only candidate pool be ranked efficiently enough to justify asking more questions?**
No. 2,831 keys, 4.9% gold (7.8% after the free `rule_weeks ≤ 6` filter). The Gate-0 replay shows blind
promotion at +3 −14; every ranker died on the feasibility term (c_g at the asked week 0.15–0.62) or on scale.
At N = 60 per seed not even a label oracle clears the displacing budget (H6).

**7. How do we avoid overcounting correlated evidence?**
Measured: reporters are never shared across sites; roots are shared only by class-4 echo decoys; 26 keys pass
only through shared roots, all decoys, and the ceiling is unchanged after de-duplication (H7). But on the shipped
pack every record is its own root (`root_ref` always `None`), so the gate's roots requirement is the support
count under another name (buckets agree in 899/904 recorded confirms). A real guard belongs in egress's root
attribution, not in the gate; a gate-side veto is a no-op (F-H7).

**8. Is a hypergraph necessary, or would a probabilistic factor model suffice?**
Neither is supported. Persisted edges change 0 of 539 verdicts. The evidence the gate sees is four count buckets
per site, a newest week and a flag; a factor model over those buckets is what H4-3 was, and it fails its own
per-seed kill and inverts at 2,000 users. Nothing to project, and nothing that pays to factor.

**9. Which experiments could disprove the most attractive proposals?**
They were run: the 2,000-user world (killed H5, H4, H7), feasibility measured at the asked week (killed H5, H4,
H3), replay through the recorded verdicts (killed H7), label-flip / seed-swap / best-of-k nulls (H8; the seed-swap
caught what leave-one-seed-out cannot). The remaining proposal — the asked-set reorder — is disproved if its e2e
arm fails to beat 128 with ≤ 113 decoys, or if the label-free variant's rare recall (4 vs 8) is unacceptable.

**10. What must change for 85% to become mathematically feasible under the benchmark's strict evidence
semantics?**
The strict ceiling (F, TB_v4) is 179/240 = **74.6%**; 85% lies above it. Under strict semantics the world must
disclose more: lower k, a wider window, or planted patterns reaching two k-sites — a change to the disclosure
invariant the benchmark exists to measure. The permissive model (B, counting a contradicted key as support) reaches
205/240 = **85.4%** with zero margin and accepts evidence a contradicting record argues against. Retrieval,
routing, ranking and graph work were each measured at ~0 against this. **85% is a founders' decision, not an
engineering backlog** — and every number here is at 200 users, where discrimination is already shown to be
density-dependent.

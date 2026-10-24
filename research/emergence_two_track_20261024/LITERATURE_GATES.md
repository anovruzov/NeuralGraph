# A frontier-literature research report, run against our own measurements

Source: an uploaded deep-research report surveying arXiv work first submitted 2026-08-24 to 2026-10-24 on
distributed/multi-hop retrieval, and proposing a gate program (its P0–P9) toward ≥85% discovery accuracy.

I checked one citation directly: **G³RAG is real** — arXiv 2609.19622, *Beyond Similarity through Zero-Token
Geometric Graphs for Multi-Hop RAG*, and the mechanism matches the report's description (edge weight
`cos θ · sin θ` balancing relevance against novelty, a topological penalty in dense regions, seeded controlled
diffusion). The remaining citations are **not verified** from this container; treat specific arXiv ids and
reported deltas as unchecked.

## The report's premise is wrong for us, in a way that matters

It opens from "~63%". That is not this system's accuracy. 0.629 and 0.679 are **diagnostic ablations** (pack
override + `--skip-drills`), and both fail the decoy bar. The official six-seed baseline is **93/240 = 0.3875**
with 118/300 decoys (`raw/official_ce0e357/`). So the gap the report sizes at 22 points is, officially, 46.

## Its gates, answered with numbers we already have

| Its gate | Its decision rule | Our measured answer | What that implies |
|---|---|---|---|
| **P0** reproduce the baseline | stable across ≥3 seeds or stop | 6 seeds, 93/240, per-seed 13/12/17/17/19/15, reproduces the recorded figures exactly, all checks green | **PASS** |
| **P1** evidence-availability ceiling | **must exceed 85%**, prefer ≥88%, else "retrieval/routing alone cannot reach the goal" | **74.6%** under the harness's own evidence semantics; **85.4%** under the most permissive polarity-blind model, with **zero margin** against its 88% preference | **FAILS its own gate.** By the report's rule, 85% is not reachable by retrieval architecture here. Its branch says: fix ingestion, representation, labels or task assumptions — which for us is the k-per-site disclosure invariant (32 rare patterns never reach k = 3 at any one site) |
| **P2** oracle owner/shard routing | ≥10 points means routing is the bottleneck; **<5 points falsifies it** | the recorded ask-every-site arm (M = all vs M = 3, seeds 0–1): **46/80 both ways — a 0-point gain** | **Falsified.** Sharding, routing and hierarchy work is deprioritised: Layers B and C of its proposed stack buy us nothing |
| **P3** exact vectors vs production ANN | ≥5 points means index geometry matters; <2 points means it cannot close the gap | Tesseract's recorded counterfactual changed **0 of 165 verdicts** at seed 0 | **Null.** Retrieval geometry is not the lever, which also makes the 780 ms/1k-node retrieval cost an optimisation of something that changes no decision |
| **P4** complementarity overlay | ≥6 points multi-owner recall | not run — and the report itself says complementarity ranking is irrelevant when routing is not the bottleneck | **Deferred by its own logic** |
| **P5** recursive missing-evidence probing | recover decisive evidence in **≥15%** of error cases within ≤3 rounds; <5% means it is not a primary path | **37 of 76 rare misses (49%)** are timing misses: feasible at a later week inside the same window | **Clears the bar by ~3×.** This is our lever |
| **P6** hypergraph vs pairwise | ≥5 points over a degree-matched pairwise projection, collapsing under shuffled membership | **moot**: our site graphs hold 12,964 nodes and **0 edges**; `save_edge` is called only from `research/benchmarks/` | Nothing to project, and its own precondition (evidence recoverable under oracle routing) is unmet |
| **P9** 10K→50K scaling | accuracy must hold while fanout grows sublinearly | the other session's new measurement: non-planted keys passing the count gate **9.6% at 200 users, ~70% at 2K and 10K** (queued for audit) | If it reproduces, discrimination itself degrades with world size — a scaling result would not mean what the 200-user result means |

## What we take from it

1. **It independently endorses the lever we chose.** Re:CAP-style sufficiency probing — ask what is missing,
   retrieve again, keep what is new — is the published analogue of the recursive aggregator. Its reported result
   (+12.9 pp recall on MuSiQue at under half the document budget) is not transferable to us numerically, but the
   shape is the same and our P5 equivalent clears the threshold.
2. **Its failure modes become our test list**: query drift, confirmation loops, runaway fanout, and an evaluator
   that wrongly declares evidence sufficient. Each needs a bounded budget, a marginal-gain termination rule, and a
   provenance-preserving stop — which is what the 3-holder / 8-claim / 2-verification budget already encodes.
3. **Its Gate 5 rule is the one we were already enforcing**: never add percentage-point gains from different
   levers arithmetically. The same objection applies to our own offline P2/P3 timing estimates.
4. **Its error taxonomy is sharper than our funnel in one place.** `E_route` counts only when the evidence exists,
   is indexed, and *would* produce a correct result if delivered, but the owner is never queried. Our S0–S5 funnel
   folds that into "asked / not asked"; adopting the stricter definition would stop us calling a timing miss a
   routing miss.

## What we do not take from it

The whole geometric stack — multi-subspace embeddings, overlapping shards, complementarity edges, cross-shard
hypergraph — rests on routing and index loss being material. We have measured both at approximately zero. Building
Layers A–D would be optimising a path that changes no verdict. The report's own first instruction is to run the
availability and routing oracles before building anything, and those two experiments are the ones that rule its
architecture out for us.

The honest summary is that the report's program, executed against our numbers, says: **stop doing retrieval
architecture, fix when the question is asked, and take the 80% target back to the founders as a disclosure
decision rather than an engineering backlog.**

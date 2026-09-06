# evaluation/agentic_web/ — reserved experiment boundary

**Status: RESERVED. Nothing here is implemented.** This directory exists so
that Agentic Web benchmark development stays isolated from NeuralGraph core
(`NeuralGraph/`), the coordination research track
(`NeuralGraph/coordination/`), and the retrieval track (owned by Nurman —
see `CLAUDE.md` rule 3 on the research branches). New code here must bridge
through adapters, never by modifying graph dynamics, memory formation,
retrieval behavior, persistence, node/edge semantics, lineage calculations,
or existing benchmark definitions.

## Central question

Can a continually changing Agentic Web (simulated populations N = 100,
1,000, 10,000+) discover distributed relationships, adapt its interaction
topology, distinguish independent corroboration from information echoes,
and preserve useful collective knowledge under churn?

Existing small experiments (the 5-node coordination fixture, 8x9
survival benchmark, four-holder reconstruction, small arity sweeps at the
lineage snapshot `13a1729`) do **not** establish that capability and must
not be cited as if they do.

## Experimental contract (to be satisfied before results are claimed)

Validity: define what makes an entity an agent; which decisions are
simulated vs. LLM-invoked; per-agent observability vs. evaluator-only
ground truth; how relationships must actually be discovered; independent
roots vs. copied evidence; representation of unknown provenance; topology
change rules and budget; injection of churn, partitions, misinformation,
and revisions; and what constitutes recovery and preserved knowledge.

Reproducibility: versioned schemas; immutable configurations; separate
random streams for world, topology, failures, and policy; explicit
population and workload definitions; event ordering and concurrency
assumptions; stable IDs and provenance-preserving event logs;
checkpoint/resume semantics; cache keys covering inputs, code, config,
models, and prompts; complete run manifests with artifact checksums;
raw-to-table-to-figure traceability; explicit timeout / missing-run /
provider-error accounting; resource and monetary budgets.

Comparison: budget-matched baselines; oracle baselines separated from
deployable strategies; held-out evaluation vs. policy development;
predeclared metrics and aggregation; multiple seeds with uncertainty
estimates; no best-seed selection; no silent removal of failed runs; no
evaluator ground-truth leakage into agent policy; tests against
echo-based inflation of independent support.

## Readiness gates (all currently PROPOSED — none satisfied)

1. Deterministic small-world correctness.
2. Provenance and evaluator-isolation tests.
3. N=100 smoke run and resource characterization.
4. N=1,000 validated run.
5. N=10,000+ validated run.
6. Independent reproduction and claim review.

A configuration file for a population size is not evidence that the
experiment at that size ran.

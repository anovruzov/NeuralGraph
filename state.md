# Mycelic / NeuralGraph / Tesseract State

Last updated: 2026-08-21
Branch: `claude/mycelic-gate-0-recovery-tjl82x`
Base: `origin/tesseract-coordination-v1` (`8258d13`)
Working tree: clean. Everything below was executed, not inferred.

## Corrections to prior session notes

Three things earlier notes asserted are false. They cost a session to
rediscover; they are recorded here so nobody rediscovers them again.

- **Commit `b5b9c3e271` does not exist.** Not in any branch, tag, or reachable
  object after a full `+refs/heads/*` fetch. Do not look for it.
- **"15 tests passing" was commit `3da6452`** (2026-08-13), verified by checking
  it out and running it: exactly 15 tests. The work had already advanced two
  commits past that to 109 tests before this session began.
- **"Mycelic" appears in no committed file.** It exists only in branch names.
  In-repo the system is `NeuralGraph/coordination/`, described as Tesseract
  coordination. NATS, JetStream and Mycelic Fabric are design intent with no
  code. `NeuralGraph/tesseract.py` is the intra-node 4D retrieval module and is
  **not** the coordinator; never confuse the two.

Also note: the dirty working tree described in the previous `state.md`
(mid-edit accuracy work in `answering.py`, `tesseract.py`, `reranker.py`,
`prompts.py`, `service.py`, `demo/runner.py`) never existed in any commit and is
not in this repository. It lived on a local machine. Likewise
`.tesseract-handoff/` is on no branch.

## Canonical commands

There is now exactly one of each.

```
pytest                                                       # whole suite
python3 -m NeuralGraph.coordination.benchmark --sweep 30 --format markdown
python3 -m NeuralGraph.coordination.benchmark --output results.json
python3 -m NeuralGraph.coordination.experiment --output experiment.json
```

Setup is `pip install -r requirements.txt`. Suite as of this writing:
**164 passed, 1 skipped, 270 subtests**, identical under `PYTHONHASHSEED`
1, 7, 4242 and 99991.

`pytest` previously failed to collect anything (5 errors, every file). The repo
root carried a leftover MemMachine scaffold `__init__.py`, and the root
directory is itself named `NeuralGraph`, so pytest's package-root walk bound
`sys.modules["NeuralGraph"]` to the repository root, which has no `data_types`
or `coordination` submodule. Making the marker side-effect-free is not enough;
the shadowing is caused by its existence. It was removed. Nothing consumed its
exports.

## Results

`NeuralGraph/coordination/benchmark.py`, 8 placement/repair strategies x 9
interventions, mean capability survival over 30 seeds:

| strategy | survival | unrecovered silent forgetting | records | struct min-cut |
|---|---|---|---|---|
| `isolated_local` | 0.000 | 0.000 | 1 | 0 |
| `centralized` | 0.111 | 0.667 | 2 | 1 |
| `fixed_distributed_replication` | 0.556 | 0.444 | 4 | 1 |
| `source_count_repair` | 0.556 | 0.444 | 4 | 1 |
| `full_replication` | 0.667 | 0.333 | 8 | 1 |
| `random_path_diversification` | 0.704 | 0.296 | 4 | 1 |
| `lineage_aware_repair` | 0.778 | 0.222 | 4 | 1 |
| `oracle_min_cut` | 0.889 | 0.111 | 4 | 2 |

Three claims, each pinned by a test:

1. **Replica count does not buy survival.** Three replicas score 0.00 against a
   lineage-root failure. Full replication at eight records -- twice any other
   strategy's storage -- also scores 0.00. Every copy descends from the root
   that failed.
2. **Lineage-aware repair reaches 1.00 on that failure at half the storage and
   fewer bytes moved than full replication.** Higher survival at lower cost.
3. **Minimum failure-domain cut predicts survival.**
   `worst_single_domain_failure` fails exactly one domain -- never a whole cut,
   which would be tautological -- and only the min-cut-2 placement survives it.

Reported against interest: **no distributed strategy survives a network
partition that isolates a single premise holder** (0.00 across all five). Only
centralized and full replication do, because they keep every premise on one
node. That is the real cost of distribution and is pinned as a test.

`random_path_diversification` lands at 0.67 against a closed-form 2/3 (odds of
placing the independent node in the first two of three candidates). That
agreement is the check that the harness measures the mechanism it claims to.

### Three harness bugs found before any of this was trusted

Recorded because each would have produced a publishable-looking but wrong table.

- `max_nodes` truncated the route so the independently rooted node was never
  contacted, making lineage diversity invisible to every strategy at once.
  Lifting the bound entirely collapses the experiment the other way: repair
  never fires and every policy ties at 0.88. The bound is kept and documented as
  load bearing.
- Repair policies ranked candidates by reading `Placement` internals. They now
  see only exported `ClaimEnvelope` contracts. A policy that reads node-private
  records is an oracle, not a policy.
- Lineage awareness acted only as a verification-time exclusion, never as an
  ordering, so a bounded verification budget was exhausted on correlated nodes
  before reaching an independent one. Before this fix,
  `random_path_diversification` appeared to beat `lineage_aware_repair`.

## Artifacts

`NeuralGraph/coordination/artifacts/` holds the JSON every number above comes
from, plus `SHA256SUMS`. `test_artifact_reproducibility.py` regenerates each
from its canonical command and compares byte for byte.

```
f712e12d…  experiment_seed20260813.json        (unchanged from 040287d)
d25fc72b…  benchmark_seed20260813.json
b52fa455…  benchmark_sweep30_seed20260813.json
```

If one of those tests fails, do **not** refresh the artifact to get green. Find
out why the output moved, then re-pin deliberately with the reason in the commit
message.

## Gate status

| Gate | Status | Evidence |
|---|---|---|
| 0 — contract/baseline | **PASS** | one `pytest` command; deps declared; artifacts pinned and byte-reproducible |
| 1 — minimal distributed reconstruction | **PASS** | pre-existing, re-verified; plus 4-node real-storage proof this session |
| 2 — fair baselines | **PASS** | 8 strategies incl. centralized, full replication, fixed distributed, oracle, under matched budget |
| 3 — Pareto evidence | **PASS** | survival vs. storage vs. bytes moved; lineage-aware dominates full replication on both axes |
| 4 — scale / failure persistence | NOT STARTED | fixture is 4 nodes, 2 slots; no scale sweep |
| 5 — generalization | NOT STARTED | one capability shape (opaque pair) |
| 6 — standalone architecture | NOT STARTED | |

## Open issue: `failure_domains` has no real-storage model

This is the one gap that materially limits the paper, and it must not be closed
by inventing a mapping.

`StorageLineageResolver` returns `failure_domains=()` because NeuralGraph
records no such concept. Consequences, all verified:

- `LineageAnalyzer.reconstruction_coalitions` skips coalitions with no domains,
  so coalition count, minimal support size and minimum failure-domain cut are
  all **structurally zero against real storage**.
- Therefore the `worst_single_domain_failure` row -- the result that validates
  min-cut as predictive -- **exists only against the mock**. Root-level results
  do carry over to real storage and are proven there.

`test_benchmark_real_storage.py::test_failure_domains_are_empty_against_real_storage`
asserts the emptiness deliberately, so it cannot regress into a silent fake.

### Three candidate derivations, for a human decision

1. **Storage identity.** Domain = the database file / node the memory lives in.
   Honest and immediately available, but makes domain diversity a restatement of
   node diversity, so min-cut collapses toward replica count and the paper's
   distinction weakens.
2. **Ingestion provenance.** Domain = the upstream source system a lineage root
   came from (a corpus, a feed, a device). Captures the correlation the paper
   actually cares about -- two nodes fail together because they ingested the
   same thing -- but NeuralGraph does not currently record it, so it needs a new
   field written at consolidation time.
3. **Operator/administrative boundary.** Domain = who controls the node.
   Matches the residential-datacenter framing and the failure mode a reader will
   imagine, but is metadata about deployment rather than about memory, and
   nothing in the graph knows it today.

Recommendation: 2 for the paper's claim, 1 as a fallback that is available now
and would let the real-storage row be reported with an explicit caveat. Either
way, say in the paper which one was used. Do not ship 1 while describing 2.

## Ownership

- Nurman: NeuralGraph, local memory formation, graph dynamics, retrieval.
- Anar: coordination, lineage semantics, distributed reconstruction, failure
  experiments, paper.
- Interface: `NeuralGraph/coordination/contracts.py`. Do not widen it casually.

Retrieval-track files (`answering.py`, `llm_profile_extractor.py`, `prompts.py`,
`reranker.py`, `service.py`, `tesseract.py`, `demo/runner.py`) were not touched
this session and should not be touched from the coordination side.

## Not done

- **Local retrieval accuracy is untouched.** `QUERY_ROUTING_IMPROVEMENTS.md:120`
  claims 50% single-hop against 77% recall@50. There is no committed artifact
  behind that number, and the QA benchmarks (`demo/runner.py`,
  `evaluation/call_center/`) need a live Ollama at `localhost:11434`, so they are
  not reproducible from a clean checkout. Separating retrieval failure from
  answer-generation failure remains open and belongs to the NeuralGraph track.
- Gates 4-6.
- `REDACTED` still has no real-adapter projection and fails closed to `DENIED`.
  Do not invent one without deciding what partial payload is provably safe.

## Next task

Decide the `failure_domains` derivation (above). It is the only thing standing
between the current mock-only min-cut result and a real-storage one, and it is a
modeling call, not an implementation detail.

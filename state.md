# Mycelic / NeuralGraph / Tesseract State

Last updated: 2026-08-25 (session 3)
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
python3 -m NeuralGraph.coordination.scale --format markdown
python3 -m NeuralGraph.coordination.figures --check
```

Setup is `pip install -r requirements.txt`. Suite as of this writing:
**277 passed, 1 skipped, 2960 subtests**, identical under `PYTHONHASHSEED`
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

### Seven harness bugs found before any of this was trusted

Recorded because each would have produced a publishable-looking but wrong table.
The full list is in `docs/RESULTS.md` §6; the three that changed conclusions:

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

Also rejected as **tautological**: an intervention that fails an entire min-cut.
A cut severs every coalition by definition, so every strategy scores zero and
the cell carries no information. Replaced with a single worst-domain failure,
which discriminates and is what validates min-cut as predictive.

### Reported against interest, additionally

`lineage_aware` repair does **not** dominate. Under a plain node failure with
correlated replicas, a surviving replica would have served -- the lineage was
never the problem, the node was -- but lineage-aware repair excludes the failed
claim's root and refuses every correlated holder, losing a capability that
`source_count` keeps (0.00 vs 1.00, at every K and H). Pinned as a test.

## Artifacts

`NeuralGraph/coordination/artifacts/` holds the JSON every number above comes
from, plus `SHA256SUMS`. `test_artifact_reproducibility.py` regenerates each
from its canonical command and compares byte for byte.

```
f712e12d…  experiment_seed20260813.json        (unchanged from 040287d)
2d8564e7…  benchmark_seed20260813.json
3e45ebc0…  benchmark_sweep30_seed20260813.json
61be1f24…  scale_seed20260813.json
```

The benchmark digests were re-pinned once, deliberately: adding slot relevance
to repair ordering moved `recovery_steps` 2→1 and `recovery_events` 28→26 with
**no survival verdict changed**. Cheaper recovery, identical survival.

If one of those tests fails, do **not** refresh the artifact to get green. Find
out why the output moved, then re-pin deliberately with the reason in the commit
message.

## Gate status

| Gate | Status | Evidence |
|---|---|---|
| 0 — contract/baseline | **PASS** | one `pytest` command; deps declared; artifacts pinned and byte-reproducible |
| 1 — minimal distributed reconstruction | **PASS** | pre-existing, re-verified; plus 4-node real-storage proof |
| 2 — fair baselines | **PASS** | 8 strategies incl. centralized, full replication, fixed distributed, oracle, under matched budget |
| 3 — Pareto evidence | **PASS** | survival vs. storage vs. bytes moved; lineage-aware dominates full replication on both axes |
| 4 — scale / failure persistence | **PASS** | `scale.py`: every verdict invariant across K ∈ {2,3,5,8}, H ∈ {2,3}; restart/persistence proven on real SQLite |
| 5 — generalization | **PASS** | capability arity 2–8 reconstructs correctly; nothing specific to pairs |
| 6 — standalone architecture | **PASS** | `docs/ARCHITECTURE.md`: five invariants, each enforced in code and named to its test |

## Resolved: `failure_domains` now has a real-storage model

Closed by **option 2, ingestion provenance** -- the derivation that measures the
correlation the paper argues about. A failure domain is the upstream origin a
memory ultimately derives from, read from the metadata of the lineage **roots**
the walk resolves, never from the retrieved node.

`StorageLineageResolver(storage, domain_key="failure_domain")` opts in. The
default stays `()`, so absence remains visible rather than synthesised. Nothing
infers a domain from storage identity, node id, file path, or value equality --
option 1 was rejected precisely because it would have made domain diversity a
restatement of node diversity and collapsed min-cut toward replica count.

Proven against real SQLite (`test_failure_domains_real_storage.py`):
min-cut 1 for the standard placement, **min-cut 2 for the oracle**, and no
single domain severs every coalition. The result that validates min-cut as
predictive is no longer mock-only.

**Safety property:** partial provenance is pessimistic. An unrecorded domain
shrinks the reported cut and can never inflate it, so a partially recorded
deployment under-claims robustness. This is what makes the metric publishable
from incomplete data, and it is asserted rather than argued.

The paper must state which derivation was used. It is ingestion provenance.

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

## Documentation

Written so a future session does not re-derive facts from source. Read these
before grepping:

- `CLAUDE.md` -- index, naming traps, non-negotiable rules, results that must
  not silently change
- `docs/ARCHITECTURE.md` -- invariants and design (Gate 6)
- `docs/RESULTS.md` -- what the numbers say, including results against interest
- `docs/REPRODUCE.md` -- regenerate every number
- `docs/PAPER.md` -- claims mapped to evidence, threats to validity

## Next task

The coordination track is complete through Gate 6. What remains is not
coordination work:

1. **Local retrieval accuracy — now diagnosed** (`docs/ACCURACY.md`). The 50%
   single-hop figure is real (52.1%) and single-hop is *worse* than multi-hop.
   About 76% of single-hop failures had the evidence retrieved, so it is a
   generation problem, not a retrieval one -- which corroborates the old
   "77% recall@50" note from an independent direction. The failure has a
   specific shape: 66% of single-hop questions are list-valued, and 85 of 86
   generation failures omit at least one gold item; 38 of 86 answer unrelated
   content despite having the evidence. Of 513 wrong answers corpus-wide: 327
   generation, 139 recoverable retrieval misses, and 47 **unanswerable** (the
   gold answer is in no part of the source conversation), so the hard ceiling
   is 96.9%. Diagnosis only -- **nothing fixed**, and the fixes land in
   Nurman's files.
2. **Paper.** `docs/PAPER.md` has the structure, the claim-to-evidence table,
   and the threats-to-validity answers. Figures 2-5 are now **rendered and
   pinned** (`python3 -m NeuralGraph.coordination.figures`, SVG, byte-stable,
   regenerated and compared by `test_figures.py`). Figure 3 (survival vs
   storage) is the one that carries it. Figure 1 is generated from
   `fixture.py` too, rather than hand-drawn, so the schematic cannot drift
   from the fixture it describes; it prints no premise value, which is pinned
   as a test. All five figures are done.

   Figure 5 was respecified: confidence vs coverage collapses onto three
   coverage values, so it is now a strip plot of confidence split by outcome.
   Same numbers, and the claim reads directly off it -- the highest confidence
   in the figure is reported by cells that lost the capability.
3. **Transport**, if the architecture is ever deployed rather than simulated.
   NATS/JetStream remains design intent.

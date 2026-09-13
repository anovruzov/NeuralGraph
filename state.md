# Mycelic / NeuralGraph / Tesseract State

Last updated: 2026-09-12 (session 4)
Branch: `claude/cognitive-forcing-function-nwwzbn`
Base: `origin/main` (`88a82d7`, PR #2 merge) plus the two coordination
branches merged in this session (see below).
Working tree: clean. Everything below was executed, not inferred.

## Session 4 (2026-09-12): recovery, reconciliation, one coordinator fix

### Recovered checkpoint

`main` never received the Gate 0 recovery work. It carried the *August 16*
`state.md` (Gate 1 closed, Gate 2 "not started", 109 tests) while Gates 2-6,
the 8-strategy benchmark, the scale sweep, the pinned artifacts, the audit and
the paper figures lived on two unmerged branches:

- `claude/mycelic-gate-0-recovery-tjl82x` (`37103e5`): Gates 0-6, benchmark,
  scale, audit, manuscript, corrected counts. 20 commits ahead of `main`.
- `claude/session-analysis-continuation-sm1a1t` (`13a1729`): the same base
  plus `figures.py`, `test_figures.py`, pinned SVGs. Diverged from the first
  after `1c8245c`.

`main`'s 6 unique commits are the retrieval-campaign track (PR #2/#3:
`llm_backend.py`, `mega_search.py`, `kg_extractor.py`, `docs/research/`,
`docs/BENCHMARKS.md`). Neither side had the other.

**"Gate 0 recovery" is not open.** Its branch closed Gates 0-6; the only thing
open was that the result had never been reconciled with `main`. This session
did the reconciliation. Historical reports (`AUDIT.md`, `VERIFICATION.md`,
`docs/paper/capability_survival.md`) keep their dated counts (266) on purpose:
they are logs of what ran then.

### What was done

1. **Merged both coordination branches into this branch** (`c84813d`,
   `63f8c22`). Conflicts: `.gitignore` (add/add, both sides kept), and the
   canonical command block / suite count in `CLAUDE.md`, `docs/REPRODUCE.md`,
   `state.md` (kept `python3.11 -m`, added `figures --check`, replaced both
   sides' stale counts with the measured union). Code merged cleanly; the
   stale root `__init__.py` that broke `pytest` on `main` is gone.
2. **Fixed a coordinator defect found while testing duplicate delivery**
   (`c5b7cb7`). `TesseractCoordinator.execute` sliced raw claims to
   `budget.max_claims` *before* normalizing, so a re-delivered claim (worker
   retry, at-least-once transport) consumed a budget slot and could evict a
   distinct worker's claim. Reproduced: `max_claims=2`, node-a delivered
   twice, node-b's half dropped, reconstruction failed with both halves
   present. Duplicates are now dropped by `claim_id` before the budget, first
   occurrence wins, count kept as `QueryExecution.duplicate_claim_count`
   (in-memory only, not in any artifact). Bytes are still charged. Pinned
   artifacts regenerate byte-identically, which is the proof the fix is
   behaviour-neutral on the recorded runs.
   Tests: `NeuralGraph/tests/test_coordination_delivery.py` (4; two fail on
   the old coordinator).
3. **Dropped a duplicate Gate 2 slice.** Before the unmerged branches were
   found, this session built a 4-node `centralized` baseline
   (`coordination/baselines.py`, 11 tests, byte-deterministic) against the
   August plan. `benchmark.py` already has `centralized` under 9 interventions
   and a 30-seed sweep, so the new module was redundant and was **not
   committed**. Negative result recorded here so it is not rebuilt.

### Verification (this session, Linux, Python 3.11.15, `pip install -r requirements.txt`)

```
$ python -m pytest -q
294 passed, 1 skipped, 3516 subtests passed
$ PYTHONHASHSEED=4242 python -m pytest -q
294 passed, 1 skipped, 3516 subtests passed
$ python -m pytest -q NeuralGraph/tests/test_artifact_reproducibility.py
11 passed, 24 subtests passed
$ (cd NeuralGraph/coordination/artifacts && sha256sum -c SHA256SUMS)   # 9/9 OK
$ (cd evaluation/artifacts && sha256sum -c SHA256SUMS)                 # 3/3 OK
$ python -m NeuralGraph.coordination.figures --check
5 figures match their artifacts.
$ python -m NeuralGraph.coordination.experiment | sha256sum           # f712e12d… (pinned)
$ PYTHONHASHSEED=7 python -m NeuralGraph.coordination.benchmark --output b.json; sha256sum b.json
2d8564e7…                                                             # (pinned)
```

Checks required by the brief and where they live: successful execution
(`test_coordination.py::EndToEndExperimentTests`), worker failure
(`FailureAndLineageTests`, `test_benchmark.py`), duplicate delivery
(`test_coordination_delivery.py`, new), provenance preservation
(`test_coordination_lineage.py`, `test_failure_domains_real_storage.py`),
permission enforcement (`RealAdapterBoundaryTests::test_capability_is_unavailable_to_an_unauthorized_context`,
`test_denied_export_leaks_neither_content_nor_identifiers`).

### Budget

No paid calls. No LLM, no network beyond `pip install` and `git fetch`.
Everything run is a local deterministic simulation or a unit test.

### Unresolved / not done here

- **This branch is not merged to `main`.** That is a PR decision for the
  owners; nothing here was pushed to `main`.
- `docs/BENCHMARKS.md` Track A still cites branch `sm1a1t` at `13a1729` as its
  source. The same artifacts now live here too; the numbers are unchanged.
- Retrieval-track files were not touched (rule 3 in `CLAUDE.md`).
- `REDACTED` still fails closed to `DENIED` (unchanged, deliberate).

### Later in session 4 (2026-09-13)

- **Memory bus** (`coordination/bus.py`, `test_coordination_bus.py`, 10 tests).
  Append-only, hash-chained log of `QueryExecution`s. Vertical link:
  `prev_hash`. Horizontal links: `peer_entry_ids` to earlier entries whose
  query was similar, estimated from a bottom-k sketch of token hashes (no
  query text, no claim payload on the bus; pinned by test). `route_hint()`
  returns the nodes that produced *selected* evidence for similar successful
  queries; `execute_on_bus()` is the orchestrator loop. JSON-lines
  persistence with verify-on-load (a flipped byte refuses to load).
  `contracts.py` untouched.
- **Agent-enabling prompt** (`coordination/agent_prompt.py`,
  `docs/AGENT_PROMPT.md` generated, `test_agent_prompt.py`, 5 tests). Node
  and orchestrator protocol rendered from the dataclasses, so every contract
  field is named and cannot drift. Defines bounded questioning (ask below
  confidence 0.6 or when all copies share one root; 2 rounds) and states
  plainly that the coordinator does not yet route questions.
- **Code map** (`CODEMAP.md`, `tools/codemap.py`, `test_codemap.py`):
  every file's symbols with line numbers; test fails when stale.
- **Forest Ops game** (`docs/sim/`): shareable simulation with a pre-mortem
  oracle (counterfactual per root/organism) and a hash-chained ledger.
  Illustrative only; not the benchmark.

- **MCP memory server** (`NeuralGraph/mcp/`, `docs/MCP.md`, `.mcp.json`,
  `test_mcp_memory.py`, 20 tests). NeuralGraph as long-term memory for
  Claude Code and Codex over stdio: remember / recall / get / forget /
  list_recent / decay / memory_stats / export_claims. Offline by default
  (hashed embedding + BM25 + entities, `embedding_source` recorded per
  memory), keyed facts supersede with contradiction recorded, soft forget,
  decay archives, export through the real adapter with private memories
  denied payload-free. Retrieval-track files untouched.
- Merged Nurman's two post-PR commits from `research/retrieval-campaign-2026-09`
  (`RERANK=none` switch and latency tables); PR #3 itself was already in main.

### Next executable step

Coordination is complete through Gate 6 and the reconciliation is done. The
next concrete step is the one the August notes named and this session did
not start: **transport**. The coordinator, adapters and repair planner are
in-process; a `MemoryNodeAdapter` implemented over a real message boundary
(local subprocess workers, no NATS yet) would make the duplicate-delivery and
failure paths real rather than simulated. Start from
`NeuralGraph/coordination/adapters.py::MemoryNodeAdapter` (the Protocol) and
`test_coordination_delivery.py::RedeliveringAdapter` (the at-least-once
test double), keep `contracts.py` unchanged, and pin the first artifact.

---

## Session 3 (2026-08-25) notes, kept verbatim below

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
python3.11 -m pytest                                                       # whole suite
python3.11 -m NeuralGraph.coordination.benchmark --sweep 30 --format markdown
python3.11 -m NeuralGraph.coordination.benchmark --output results.json
python3.11 -m NeuralGraph.coordination.experiment --output experiment.json
python3.11 -m NeuralGraph.coordination.scale --format markdown
python3.11 -m NeuralGraph.coordination.figures --check
```

Setup is `pip install -r requirements.txt`. Suite as of this writing:
**331 passed, 1 skipped, 3516 subtests**, identical under `PYTHONHASHSEED`
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
`source_count` keeps (0.00 vs 1.00). This happens in exactly **one of six**
policy x intervention cells -- `lineage_aware::node_failure::I1` -- and holds
at every K and H *within that cell*. At **I=2** the two policies tie at 1.00.
The controlling variable is the number of independent roots, not K or H.
Pinned as a test. See `AUDIT.md` section 2.

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
- (Gates 4-6 were listed here in an earlier draft; the gate table above records them as PASS with evidence.)
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

# Mycelic / NeuralGraph / Tesseract State

Last updated: 2026-08-16 (session 2, same day)
Branch: tesseract-coordination-v1
Commit: 040287d00be39f5aa555ee50fc171a9025d08a06 ("Integrate Tesseract coordination with real NeuralGraph storage") — HEAD unchanged; this session's work is uncommitted.
Working tree: dirty. Unrelated pre-existing dirty work untouched (see Known
Issues #3). This session added exactly two new, uncommitted files:
`state.md` and `NeuralGraph/tests/test_coordination_experiment_restart.py`.
No existing file was modified.

## Mission

Demonstrate **capability coherence without state convergence**: independent
NeuralGraph memory holders should be able to jointly reconstruct an answer no
single one of them holds, coordinated by Tesseract, without merging into one
global graph. Eventually compare this against centralized / replicated /
federated / decentralized baselines (Gates 2+).

## Architectural Invariants

- NeuralGraph owns local knowledge; Tesseract coordinates but never gets
  unrestricted access to private graph internals.
- Cross-node exchange happens through typed, validated artifacts
  (`ClaimEnvelope`, `RetrievalTrace`, `EvidenceExport`) — not raw graph reads,
  not cross-graph edges, not a global merged store.
- Policy runs before propagation, not after.
- Failure injection is reversible and deterministic, never destructive
  deletion.
- These invariants are enforced in code today (see `NeuralGraph/coordination/`
  below), not just documented intent.

## Current Architecture

Two independent things are both called "Tesseract" in this repo — do not
conflate them:

1. **`NeuralGraph/tesseract.py`** (2212 lines, dirty/unrelated to this work) —
   intra-node "4D memory" query-routing/fusion module (temporal/entity/
   reasoning/adversarial store weighting for a single NeuralGraph). Part of
   the retrieval-accuracy tuning track, currently mid-edit (list-question
   detection). Not the cross-node coordinator.
2. **`NeuralGraph/coordination/`** — the actual Tesseract-coordination
   research package this task is about:
   - `contracts.py` — domain-neutral, frozen dataclasses forming the entire
     trust boundary (`QueryRequest`, `ClaimEnvelope`, `RetrievalTrace`,
     `EvidenceExport`, `VerificationRequest/Result`, `FragilityMetrics`,
     `TraceEvent`, `InterventionRecord`, `ExperimentManifest`, etc.). No
     NeuralGraph node/edge types imported here.
   - `core.py` — `LogicalClock`, `TraceLogger`, `FailureInjector` (reversible,
     typed intervention targets), `CapabilityRegistry`, `Router`,
     `ClaimNormalizer`, `RuleBasedSynthesizer` (deterministic slot
     composition, no LLM), `LineageAnalyzer` (reconstruction coalitions,
     minimal failure-domain cut), `TesseractCoordinator.execute()`,
     `RepairPlanner`.
   - `adapters.py` — `MockMemoryNodeAdapter` (fixture-only, in-memory,
     hash-derived opaque IDs) and `NeuralGraphMemoryAdapter` (the real
     boundary: wraps an owner-supplied `local_retrieve` callback + policy
     filter + optional lineage resolver; validates every field crossing the
     boundary — rejects non-str/blank identifiers, rejects bare strings that
     would explode into characters, rejects non-finite floats, rebuilds
     str/int/float through base-type methods to strip hostile `__repr__`/
     subclass state, binds trace_id to memory_id not list index, caps one
     export per memory per query, fails closed to a payload-free denial trace
     on any non-ALLOWED policy status including REDACTED).
   - `storage_adapter.py` — `StorageLineageResolver`: derives lineage from a
     **real** `NeuralGraphStorage` backend using only the abstract base API
     (`get_node`, `get_edges_from`), walking `HIERARCHY` edges in the correct
     derivation direction (parent→child is `target_id`, never `parent_id`),
     depth/node-bounded, cycle-safe, fully sorted for determinism. This is the
     only file that imports NeuralGraph storage types — coordination stays
     storage-agnostic elsewhere.
   - `fixture.py` — seeded, deterministic 4-node "opaque pair reconstruction"
     fixture. `left` lives only on node-a; `right` lives on node-b (root R2),
     node-c (apparent replica, same root R2), node-d (independent root R3).
     `assert_fixture_privacy()` mechanically checks the answer is absent from
     the query and from every single node's memory.
   - `experiment.py` — `run_strategy`/`run_experiment`: runs the fixture
     through 3 named strategies (`replica_count`, `lineage_without_repair`,
     `lineage_targeted_repair`) across `before_failure` /
     `after_failure` / `after_repair` phases, injecting a `LINEAGE_ROOT`
     failure on R2, and distinguishes "just add more replicas of the same
     failed root" from "route to an independently-rooted node" as different
     repair strategies with different outcomes.
   - `__init__.py` — package export surface.

## Implemented

- Full typed contract layer with boundary validation baked into
  `__post_init__` (e.g. a `RetrievalTrace` cannot carry structural IDs unless
  `policy_status is ALLOWED` — this is enforced by the dataclass itself, not
  just by convention).
- Deterministic mock 4-node reconstruction experiment with reversible failure
  injection and 3 repair strategies, distinguishing replica-count "repair"
  from lineage-independent repair (addresses scrutiny item #7).
- Real-storage integration: `NeuralGraphMemoryAdapter` + `StorageLineageResolver`
  tested against both the in-memory backend and real file-based SQLite
  (`SQLiteNeuralGraphStorage`, temp `.db` file, verified durable via an
  independent raw `sqlite3` connection, not just the same storage instance).
- `source_memory_ids` persistence bug fixed: `NeuralGraph/sqlite_storage.py`
  (lines ~53, ~80-81) now serializes/deserializes this field; previously it
  was silently dropped, which would have destroyed the one lineage field the
  real adapter reads. Verified present in code, not just claimed.
- Value/identity hardening against hostile inputs: string/int/float subclass
  `__repr__` smuggling, non-finite floats, bare-string explosion into
  characters, blank/whitespace identifiers — all covered by dedicated tests
  (`RealAdapterValueBoundaryTests`, `RealAdapterProjectionIntegrityTests`).
- Trace-id stability: trace_id bound to `memory_id`, not retrieval-order list
  index (addresses scrutiny item #3).
- Export budget bounds exports, not candidates offered, and a denial still
  consumes a budget slot (addresses scrutiny item #5's spirit — failure
  metadata carries no payload either way).
- **New this session:** `NeuralGraph/tests/test_coordination_experiment_restart.py`
  — the full multi-node reconstruction experiment run against real
  `NeuralGraphMemoryAdapter` + `SQLiteNeuralGraphStorage` (one DB file per
  node), proving reconstruction and lineage survive closing and reopening
  storage (a real restart, not a reused connection). This is additive test
  code only; no production file was changed.

## Test Status

**pytest cannot be invoked directly from the repo root** — see Known Issues
#1. Verified working invocation:

```
cd /Users/novruz/NeuralGraph
source .venv/bin/activate
python -m unittest NeuralGraph.tests.test_coordination \
  NeuralGraph.tests.test_coordination_lineage \
  NeuralGraph.tests.test_coordination_storage \
  NeuralGraph.tests.test_single_hop_regression -v
```

Result (run 2026-08-16, this session, independently, not taken from any
commit message): **107 tests, all OK** — 0 failures, 0 errors, against HEAD
as committed.
(`test_single_hop_regression` contributed 0 of these 107: it's pytest-native
with fixtures/`pytest.mark.asyncio`, not `unittest.TestCase`, so `unittest`
discovers 0 tests in that file — see Known Issues #2.)

This matches HEAD's commit message claim of "107 deterministic tests," and
the claim is now independently verified, not merely trusted.

**After adding `NeuralGraph/tests/test_coordination_experiment_restart.py`**
(this session, see Gate 1 and Next Task below), re-ran with that module
included:

```
python -m unittest NeuralGraph.tests.test_coordination \
  NeuralGraph.tests.test_coordination_lineage \
  NeuralGraph.tests.test_coordination_storage \
  NeuralGraph.tests.test_coordination_experiment_restart \
  NeuralGraph.tests.test_single_hop_regression -v
```
Result: **109 tests, all OK** (the same 107 plus 2 new). Also independently
sanity-checked that the new test is not vacuous: re-ran `_reconstruct()`
against a pair of storages where only node A's store was seeded (node B's DB
file empty) and confirmed `synthesis.success == False`,
`covered_slots == ('left',)` — i.e. the passing 2-node case genuinely depends
on both real SQLite stores, not on some in-process leftover state.

## Gate Status

### Gate 0 — Contract / baseline recovery
Status: PASS
Evidence: Repo located (`/Users/novruz/NeuralGraph`, branch
`tesseract-coordination-v1`, HEAD `040287d`). Coordination package and test
suite read in full. 107/107 tests independently re-run and passing via
`python -m unittest` (see Test Status). `source_memory_ids` fix verified by
reading `sqlite_storage.py` directly, not by trusting the commit message.
Remaining: none for this gate.

### Gate 1 — Minimal distributed reconstruction
Status: PASS
Evidence:
- Knowledge distributed across ≥2 independent holders: yes (`fixture.py`,
  4 nodes, `left`/`right` split, mock-based; and now also
  `test_coordination_experiment_restart.py`, 2 nodes, real SQLite-backed).
- No single node trivially containing the final answer: yes, and mechanically
  checked — `assert_fixture_privacy` (mock fixture) and
  `test_no_single_node_stores_the_full_answer` (real-storage fixture), not
  just asserted in prose.
- Successful coordinated reconstruction: yes (`RuleBasedSynthesizer` combines
  claims from ≥2 producer nodes; proven against both `MockMemoryNodeAdapter`
  (`EndToEndExperimentTests`) and real per-node `SQLiteNeuralGraphStorage`
  (`test_reconstruction_survives_a_storage_restart`).
- Deterministic test fixture: yes (`LogicalClock`, seeded RNG, sorted
  everywhere lineage is aggregated; the real-storage fixture reuses the same
  determinism discipline).
- Traceable evidence: yes (`TraceLogger`/`TraceEvent` stream, `lineage_root_ids`
  on every claim, `claim_roots` in experiment output).
- Restart/persistence behavior: **now proven at the experiment level, not
  just the adapter level.** `test_coordination_experiment_restart.py`
  (added this session) builds two real `NeuralGraphMemoryAdapter`s over two
  separate temp SQLite files, runs the full `TesseractCoordinator.execute`
  reconstruction, closes both storage connections, opens two brand-new
  `SQLiteNeuralGraphStorage` instances against the same files (a genuine
  process-restart simulation, not a reused connection), rebuilds the
  coordinator from scratch, and re-runs. Result: identical answer, identical
  confidence, identical claim/trace identifiers, identical lineage roots.
  Independently sanity-checked as non-vacuous (see Test Status): the same
  code with only one node's store seeded correctly fails to reconstruct.
Remaining: none for this gate's stated requirements. Note the real-storage
proof intentionally uses a minimal 2-node fixture (not the 4-node
apparent-replica-vs-independent-root fixture) — the replica-vs-lineage
distinction is already proven separately and deterministically against
`MockMemoryNodeAdapter` in `experiment.py`/`test_coordination.py`; extending
the real-storage fixture to 4 nodes to replicate that too would be Gate-2+
scope creep, not a Gate 1 requirement.

### Gate 2 — Fair baselines
Status: NOT STARTED
Evidence: none. `experiment.py` only implements the Tesseract-coordination
strategies (`replica_count`, `lineage_without_repair`,
`lineage_targeted_repair`) — no centralized/replicated/federated/decentralized
comparison baseline exists anywhere in `coordination/`.
Remaining: everything. Do not start until Gate 1's remaining gap is closed.

### Gate 3 — Pareto evidence
Status: NOT STARTED (blocked on Gate 2)

### Gate 4 — Scale / failure persistence
Status: NOT STARTED (blocked on Gate 2/3)

### Gate 5 — Generalization
Status: NOT STARTED (blocked on Gate 4)

### Gate 6 — Standalone architecture strength
Status: NOT STARTED (blocked on Gate 5)

## Known Issues

1. **pytest cannot be run from the repo root as-is.** The repo root
   (`/Users/novruz/NeuralGraph/__init__.py`) is itself a leftover package
   `__init__.py` from an unrelated "MemMachine" scaffold
   (`from memmachine.rest_client import ...`), and `memmachine` is not
   installed in `.venv`. Because this root `__init__.py` exists, pytest's
   package-root walk (both legacy `prepend` and `importlib` import modes)
   climbs past the real package root (`NeuralGraph/NeuralGraph/`) and tries to
   import the repo root as a package, which fails on `memmachine`, or — worse
   — succeeds inconsistently across test files and leaves `sys.modules
   ["NeuralGraph"]` bound to the wrong directory for some files but not
   others (`No module named 'NeuralGraph.coordination'` /
   `'NeuralGraph.data_types'` errors seen mid-collection). This is a
   **pre-existing environment issue, unrelated to the coordination work**, not
   something introduced by this session. Workaround: run tests via
   `python -m unittest NeuralGraph.tests.<module>` from the repo root, which
   resolves imports correctly (see Test Status). This should eventually be
   fixed (e.g. an actual `pyproject.toml`/`conftest.py` at repo root, or
   removing/renaming the stale scaffold `__init__.py` if confirmed unused) but
   that is out of scope for the coordination slice and was not touched.
2. **`test_single_hop_regression.py` is pytest-native, not unittest, and its
   integration test needs live NeuralGraph setup.** It uses `@pytest.fixture`
   and `@pytest.mark.asyncio` (pytest-asyncio is not installed in `.venv`), so
   it silently contributes 0 tests under `python -m unittest`, and cannot
   currently be collected under pytest either because of Known Issue #1.
   Its one integration test is already marked
   `@pytest.mark.skip(reason="Requires full NeuralGraph setup with data")`.
   This suite is unrelated to the coordination gates (it's single-node
   retrieval-accuracy regression) and was not modified or fixed this session.
3. **Unrelated dirty working tree**, pre-existing, not touched this session:
   `NeuralGraph/answering.py`, `NeuralGraph/llm_profile_extractor.py`,
   `NeuralGraph/prompts.py`, `NeuralGraph/reranker.py`, `NeuralGraph/service.py`,
   `NeuralGraph/tesseract.py` (the *intra-node* one, see Current Architecture),
   `demo/runner.py` — all mid-edit for single-hop QA accuracy tuning (list-
   question detection, reranking, prompts), a separate work track from
   Tesseract coordination. Plus untracked large benchmark output files
   (`demo/*.json`, up to 11MB) and a new `.gitignore`. None of this was
   created or modified by this session; preserved as-is per the git safety
   protocol.
4. Per HEAD's own commit message (self-reported, and consistent with the code
   read this session): `query()` on `NeuralGraphMemoryAdapter` performs no
   capability-scope authorization of its own (that's the router's job);
   `REDACTED` policy status has no real-adapter projection and fails closed
   to `DENIED` instead; per-source-memory authorization is absent; SQLite
   `consolidation` unions `source_memory_ids` on merge (now durable, which is
   new behavior this fix enabled). These are documented limitations, not
   regressions — flagging here so a future session doesn't have to
   rediscover them from the diff.

## Current Experiment

Two reconstruction experiments now exist side by side, deliberately not
merged:
- The opaque-pair 4-node mock experiment (`coordination/experiment.py`,
  `coordination/fixture.py`) — proves distributed reconstruction, replica-
  count vs. lineage-independent repair, and reversible failure injection,
  against `MockMemoryNodeAdapter`.
- The real-storage restart experiment (new this session,
  `NeuralGraph/tests/test_coordination_experiment_restart.py`) — proves
  distributed reconstruction and restart/persistence survival against real
  `NeuralGraphMemoryAdapter` + `SQLiteNeuralGraphStorage`, with a minimal
  2-node fixture.
Between them, Gate 1's full requirement list is now covered. Neither alone
covered all of it; that was the gap this session closed.

## Next Task

Gate 1 is closed. The next task is the first slice of **Gate 2 — Fair
baselines**: build one comparable baseline strategy (start with
*centralized*: a single node that receives and stores everything, no
coordination boundary) under matched conditions (same fixture, same query,
same budget accounting) so its cost/behavior can be compared against the
existing Tesseract-coordination strategies on equal footing.

Concretely:
1. Re-read `coordination/experiment.py`'s cost/evaluation accounting
   (`exported_bytes`, `exported_claim_count`, `phase_query_count`,
   `verification_steps`) before designing the baseline — the baseline must
   report the same metrics shape so a later comparison (Gate 3) is apples to
   apples.
2. Decide where the centralized baseline lives (likely
   `coordination/baselines.py`, new file) and what "centralized" concretely
   means here: does it reuse the same 2-node or 4-node fixture with all
   records handed to one node's local store, or a new fixture? Prefer reusing
   `fixture.py`'s existing structural facts (roots/domains) rather than
   inventing a new fixture, so the eventual Gate 3 comparison is over the same
   underlying knowledge distribution.
3. Write the test first, asserting the centralized baseline actually succeeds
   trivially (it should, precisely because it violates the "no single node
   has the answer" invariant Tesseract respects) and reports comparable cost
   metrics.
4. Do not implement replicated/federated/decentralized baselines yet — one
   baseline per slice.
5. Update this file's Gate 2 status, Test Status, and Handoff Notes.

## Files Most Relevant to Next Task

- `/Users/novruz/NeuralGraph/NeuralGraph/coordination/experiment.py` (cost/
  evaluation accounting shape to match)
- `/Users/novruz/NeuralGraph/NeuralGraph/coordination/fixture.py` (existing
  structural facts: roots, failure domains, records_by_node)
- `/Users/novruz/NeuralGraph/NeuralGraph/coordination/contracts.py`
  (`ExperimentManifest`, `FragilityMetrics` — check whether these already fit
  a baseline or need a parallel/shared shape)
- `/Users/novruz/NeuralGraph/NeuralGraph/tests/test_coordination.py`
  (existing `EndToEndExperimentTests` pattern)

Reference only (Gate 1, now closed, kept for context if this needs
revisiting):
- `/Users/novruz/NeuralGraph/NeuralGraph/coordination/adapters.py`
  (`NeuralGraphMemoryAdapter`)
- `/Users/novruz/NeuralGraph/NeuralGraph/coordination/storage_adapter.py`
  (`StorageLineageResolver`)
- `/Users/novruz/NeuralGraph/NeuralGraph/tests/test_coordination_experiment_restart.py`
  (new this session — real-storage restart proof)

## Decisions Made

- Coordination stays domain-neutral: `contracts.py` and `core.py` import no
  NeuralGraph storage types. Only `storage_adapter.py` bridges to real
  storage, through the abstract `NeuralGraphStorage` base API only (never the
  neural retriever, which activates edges as a side effect of reading).
- Lineage direction is fixed and load-bearing: derivation parents of X are the
  **targets of X's own outgoing `HIERARCHY` edges**, plus X's own
  `source_memory_ids`. `parent_id` and `get_edges_to` point the wrong way and
  must never be used for derivation lineage — using them would report a
  summary as an ancestor of its own inputs.
- `REDACTED` policy status has no real-adapter projection; it fails closed to
  `DENIED`. Do not casually "fix" this by inventing a redaction projection
  without deciding what a safe partial payload actually is.
- Failure injection is reversible and payload-free by construction
  (`FailureInjector` never deletes or mutates a memory; blocked traces are
  contractually forced empty of structural IDs by `RetrievalTrace.__post_init__`).
- Do not conflate `NeuralGraph/tesseract.py` (intra-node 4D retrieval fusion)
  with `NeuralGraph/coordination/` (cross-node Tesseract coordinator). They
  share a name for historical/branding reasons only.

## Open Questions

- Should the real-storage 4-node experiment (Next Task) live as a new
  `coordination/experiment_real_storage.py`, or as a parametrized variant of
  the existing `run_strategy`? Not yet decided — repository evidence doesn't
  point either way; whoever implements it should pick the smaller diff.
- No decision yet on what a safe `REDACTED` real-adapter projection would even
  look like (what "partial" payload is provably safe to cross the boundary).
  Not needed for Gate 1; flagged for whenever it becomes load-bearing.

## Last Verified Commands

```
$ pwd
/Users/novruz/NeuralGraph

$ git status
On branch tesseract-coordination-v1
Your branch is ahead of 'origin/tesseract-coordination-v1' by 1 commit.
Changes not staged for commit: NeuralGraph/answering.py, NeuralGraph/llm_profile_extractor.py,
  NeuralGraph/prompts.py, NeuralGraph/reranker.py, NeuralGraph/service.py, NeuralGraph/tesseract.py,
  demo/runner.py
Untracked: .gitignore, demo/baseline_conv1.json, demo/fixed_v1.json..v6.json

$ git branch --show-current
tesseract-coordination-v1

$ git log --oneline -10
040287d Integrate Tesseract coordination with real NeuralGraph storage
3da6452 Add policy-filtered Tesseract coordination experiment
8785d69 Add project README
ba93027 Delete fixes.txt
0e4f112 Delete resume_sporeOS.tex
1019266 Fix SINGLE_HOP synthesis failures with concise extraction prompts
8559887 Add speaker profiles, LLM-based query routing, and enhanced temporal processing
791dbe8 Add latency tracking, GPT-4o judge, and open-domain fallback inference
0d02249 Update NeuralGraph with enhanced query routing and temporal processing
8f8c5b9 Push complete NeuralGraph project to main

$ source .venv/bin/activate && python -m unittest \
    NeuralGraph.tests.test_coordination \
    NeuralGraph.tests.test_coordination_lineage \
    NeuralGraph.tests.test_coordination_storage \
    NeuralGraph.tests.test_single_hop_regression -v
Ran 107 tests in 0.257s
OK

$ grep -n "source_memory_ids" NeuralGraph/sqlite_storage.py
53:        "source_memory_ids": node.source_memory_ids,
80:        # Rows persisted before source_memory_ids was serialized simply lack the key.
81:        source_memory_ids=data.get("source_memory_ids", []),

$ source .venv/bin/activate && python -m unittest \
    NeuralGraph.tests.test_coordination \
    NeuralGraph.tests.test_coordination_lineage \
    NeuralGraph.tests.test_coordination_storage \
    NeuralGraph.tests.test_coordination_experiment_restart \
    NeuralGraph.tests.test_single_hop_regression -v
Ran 109 tests in 0.227s
OK

$ git status --short
 M NeuralGraph/answering.py            (pre-existing, unrelated, untouched)
 M NeuralGraph/llm_profile_extractor.py (pre-existing, unrelated, untouched)
 M NeuralGraph/prompts.py               (pre-existing, unrelated, untouched)
 M NeuralGraph/reranker.py              (pre-existing, unrelated, untouched)
 M NeuralGraph/service.py               (pre-existing, unrelated, untouched)
 M NeuralGraph/tesseract.py             (pre-existing, unrelated, untouched)
 M demo/runner.py                       (pre-existing, unrelated, untouched)
?? .gitignore                           (pre-existing, untouched)
?? NeuralGraph/tests/test_coordination_experiment_restart.py   (NEW this session)
?? demo/*.json (7 files)                (pre-existing, untouched)
?? state.md                             (NEW this session)
```

## Handoff Notes

- Gate 1 is closed as of this session. `state.md` §Gate Status has the
  evidence. Do not redo the real-storage restart proof; extend it only if a
  later gate genuinely needs the 4-node replica/independent-root distinction
  proven against real storage too (not needed for Gate 2's first slice).
- Use `python -m unittest NeuralGraph.tests.<module>` to run coordination
  tests, not bare `pytest`. See Known Issues #1 before attempting to "fix"
  the pytest invocation as part of an unrelated task.
- Current full coordination regression command (109 tests):
  ```
  python -m unittest NeuralGraph.tests.test_coordination \
    NeuralGraph.tests.test_coordination_lineage \
    NeuralGraph.tests.test_coordination_storage \
    NeuralGraph.tests.test_coordination_experiment_restart -v
  ```
  (`test_single_hop_regression` stays separate — see Known Issues #2 — and
  contributes 0 to this count under `unittest`.)
- Do not touch `NeuralGraph/answering.py`, `NeuralGraph/llm_profile_extractor.py`,
  `NeuralGraph/prompts.py`, `NeuralGraph/reranker.py`, `NeuralGraph/service.py`,
  `NeuralGraph/tesseract.py` (intra-node), or `demo/runner.py` — pre-existing
  unrelated dirty work from a different track. If you need a clean baseline
  diff for the coordination slice, diff against `3da6452` or `040287d`, not
  against a fresh `git stash`.
- `NeuralGraph/tesseract.py` is NOT the coordination system. If asked to
  modify "Tesseract," confirm whether the request means the coordinator
  (`NeuralGraph/coordination/`) or the intra-node retrieval-fusion module
  before touching anything.
- The 107-test baseline is real and independently reproduced, not a stale
  claim. Any future session should re-run it before trusting it again,
  per the project's own staleness protocol
  (`.tesseract-handoff/ENGINEERING/STALENESS_PROTOCOL.md`).
- `.tesseract-handoff/` contains a prior session's own operating rules
  (`TESSERACT_CLAUDE_PERSISTENT.md`, `GOAL.txt`) for a now-completed slice
  ("real NeuralGraph storage coordination + policy-boundary + lineage-
  preservation"). That slice's goal is now done (HEAD `040287d` is exactly
  that commit, verified). Do not re-run that goal; it would just redo
  finished work. Gate 1's remaining gap (see Next Task) is a distinct,
  smaller follow-on, not a continuation of that interrupted slice.

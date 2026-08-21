# Architecture

What the coordination layer is, what it guarantees, and why those guarantees
hold independently of any experimental result.

This is the Gate 6 document: the design has to be defensible on its own terms,
not only because a benchmark came out favourably.

---

## Naming, once

Three things share names in confusing ways. Getting this wrong wastes hours.

| Name | What it actually is |
|---|---|
| `NeuralGraph/coordination/` | The cross-node coordinator. **This document.** |
| `NeuralGraph/tesseract.py` | Intra-node 4D retrieval fusion, 2212 lines. **Not** the coordinator. Unrelated. |
| "Mycelic", "Mycelic Fabric" | Project branding. Appears in **no committed file**. |
| NATS / JetStream | Intended durable transport. **Not implemented.** Design intent only. |

If a task says "Tesseract", establish which one is meant before touching
anything.

---

## The problem

Independent agents each hold part of what is needed to answer a question. No
one of them holds the answer. The question is whether the collective can
reconstruct it — and, more importantly, whether it can *keep* being able to,
as parts of the system fail.

The failure mode this layer exists to expose is **collective forgetting**: the
system loses a capability while every local signal still says it is healthy.
Confidence stays high because each surviving node is individually fine. The
capability is gone anyway.

---

## Layers

```
    query
      │
      ▼
┌─────────────────────┐
│  TesseractCoordinator│  routes, normalizes, synthesizes, scores fragility
└─────────┬───────────┘
          │  typed contracts only
          ▼
┌─────────────────────┐
│  MemoryNodeAdapter  │  the trust boundary
└─────────┬───────────┘
          │  owner-supplied callbacks
          ▼
┌─────────────────────┐
│  NeuralGraph store  │  private, per node, never read across nodes
└─────────────────────┘
```

The coordinator never touches a store. It sees only `ClaimEnvelope`,
`RetrievalTrace` and `EvidenceExport` — frozen dataclasses that validate
themselves. There is no global merged graph, no cross-graph edge, and no
central context holding every premise.

---

## The five invariants

These are enforced in code, not documented as intent. Each names the test that
would fail if it were violated.

### 1. No single node holds the answer

The capability exists only in the collective. Checked mechanically per
placement, not asserted in prose.

→ `test_benchmark.py::test_distributed_strategies_never_let_one_node_hold_the_answer`
→ `test_benchmark_real_storage.py::test_no_single_real_store_holds_the_full_answer`

The `centralized` baseline deliberately violates this, and is flagged for it —
that is what makes it a baseline rather than a strategy.

### 2. Denials carry no payload

A blocked or redacted response may not carry memory ids, source ids, parent
ids, lineage roots, or edge paths. This is enforced by the dataclass itself:

```python
# contracts.py, RetrievalTrace.__post_init__
if self.policy_status is not PolicyStatus.ALLOWED and any(...):
    raise ValueError("blocked or redacted traces must not expose structural identifiers")
```

A denial that leaked provenance would let a caller map another node's memory by
probing it with queries it is not allowed to answer.

→ `test_benchmark.py::test_denied_responses_never_carry_payload`
→ `test_benchmark_real_storage.py::test_denials_carry_no_payload_on_the_real_adapter`

### 3. Policy runs before propagation

The policy filter is consulted before a record becomes a claim, not after.
`REDACTED` has no real-adapter projection and **fails closed to `DENIED`**,
because the adapter cannot construct a partial payload it can prove is safe.

Do not "fix" this by inventing a redaction projection without first deciding
what partial payload is provably safe to cross the boundary.

### 4. Failure injection is reversible and non-destructive

`FailureInjector` masks; it never deletes or mutates a memory. Every
intervention can be cleared. An experiment that destroyed state could not be
re-run, and a destructive "failure" would confound loss of access with loss of
data.

### 5. Lineage direction is fixed

A `HIERARCHY` edge runs **parent → child**. The derivation parents of X are the
*targets* of X's own outgoing edges, plus X's `source_memory_ids`.

`parent_id` and `get_edges_to` point the other way. Using them would report a
summary as an ancestor of its own inputs — inverting provenance and making
correlated copies look independent.

→ `storage_adapter.py` module docstring; `test_coordination_lineage.py`

---

## Lineage, roots, and failure domains

Three distinct things that are easy to conflate:

| Concept | Meaning | Where it comes from |
|---|---|---|
| **Replica** | Another copy of the same value | Counting nodes |
| **Lineage root** | The ancestor a memory derives from | Walking HIERARCHY edges + `source_memory_ids` |
| **Failure domain** | The upstream origin that fails as a unit | Recorded on root nodes; **opt-in** |

The central claim of the whole project is that these come apart. Three nodes
holding the same value look like threefold redundancy and are not, if all three
descend from one root. Replica count measures the first; survival depends on
the second and third.

### Failure domains are recorded, never inferred

NeuralGraph has no native failure-domain concept. `StorageLineageResolver`
therefore reports `failure_domains=()` **by default**, which makes every
domain-derived metric visibly zero rather than quietly wrong.

Setting `domain_key` opts in. The domain is read from the metadata of the
lineage **roots** the walk resolves — not from the retrieved node. Two memories
in different databases share a domain when they descend from the same ingested
origin. Two memories that merely hold equal values do not.

Nothing infers a domain from storage identity, node id, file path, or value
equality. A root recording no domain contributes none.

**Safety property:** partial provenance is pessimistic. An unrecorded domain
shrinks the reported min-cut and can never inflate it, so a partially recorded
deployment under-claims robustness rather than over-claiming it. This is what
makes the metric safe to publish from incomplete data.

→ `test_failure_domains_real_storage.py::test_partial_provenance_is_pessimistic_never_optimistic`

---

## Minimum failure-domain cut

The structural metric the design is organised around.

> **min-cut** = the smallest number of failure domains whose simultaneous loss
> severs *every* reconstruction coalition.

min-cut 1 means some single domain failure destroys the capability. min-cut 2
means no single domain can.

This is validated rather than assumed. `worst_single_domain_failure` fails
exactly **one** domain — never a whole cut, which would be tautological, since a
cut severs everything by definition — and only placements with min-cut ≥ 2
survive it.

Reported twice, because a bounded query cannot see a whole placement:

- `structural_min_cut` — over the entire placement. The design property.
- observed min-cut — over the claims one query actually returned.

They differ precisely when routing hides diversity, which is itself worth
seeing.

---

## Repair policies

A repair policy is an **ordering** over candidate nodes to verify. That is the
whole of it, and it is where lineage-awareness has to act.

Excluding correlated support at verification time is not sufficient: with a
bounded verification budget, a lineage-blind ordering exhausts itself on
correlated nodes before reaching an independent one.

Every policy sees only exported `ClaimEnvelope` contracts — never `Placement`
internals or node-private records. A policy that read private state would be an
oracle, not a policy, and the comparison against the actual oracle row would be
meaningless.

| Policy | Principle |
|---|---|
| `source_count` | Prefer the most-replicated support. The intuition under test. |
| `random` | Shuffle. Control. |
| `lineage_aware` | Prefer support independent of the roots and domains known to be compromised. |

**Slot relevance** breaks ties *within* each policy's principle — never ahead of
it. A node observed to hold the missing premise ranks above one never
contacted, which ranks above one observed to hold a different premise. Applied
to every policy equally, so it cannot confound the comparison. Never primary,
because the node observed holding the missing premise may be exactly the one
whose lineage just failed.

---

## Determinism

Every result is byte-reproducible. This is a design constraint, not a
convenience:

- `LogicalClock` — seed-derived, no wall clock anywhere in an artifact
- seeded RNG for all symbol generation
- sorted aggregation at every point where lineage is combined
- opaque symbols, so a leak is detectable by substring search rather than judgement

Verified under `PYTHONHASHSEED` 1, 7, 4242 and 99991. Artifacts are pinned by
SHA-256 and regenerated byte-for-byte by the test suite.

---

## Known limitations

Stated here so they are not rediscovered from the diff.

1. **Network partition.** No distributed strategy survives a partition that
   isolates a single premise holder. Only centralized and full replication do,
   because they keep every premise on one node. This is the real cost of
   distribution.
2. **Lineage-aware repair is over-conservative** under a plain node failure with
   correlated replicas: it refuses a surviving replica that would have worked,
   because it excludes the failed claim's root regardless of what actually
   failed. Replica-count repair keeps the capability there and lineage-aware
   loses it.
3. **`REDACTED`** has no real-adapter projection; fails closed to `DENIED`.
4. **Per-source-memory authorization** is absent. `query()` performs no
   capability-scope authorization of its own; that is the router's job.
5. **Transport is not implemented.** NATS/JetStream is design intent. Everything
   runs in-process.
6. **`recovery_steps` is a logical step count**, not wall-clock latency. No
   wall-clock timing is measured anywhere, because a mock adapter's timings
   would say nothing about a deployed system.

---

## Where things live

```
NeuralGraph/coordination/
  contracts.py        frozen dataclasses; the entire trust boundary
  core.py             clock, traces, injector, registry, router, synthesizer,
                      LineageAnalyzer, TesseractCoordinator, RepairPlanner
  adapters.py         MockMemoryNodeAdapter + NeuralGraphMemoryAdapter
  storage_adapter.py  StorageLineageResolver — the only NeuralGraph import
  fixture.py          the seeded 4-node A/B/C/D fixture
  experiment.py       3-strategy reconstruction experiment
  benchmark.py        8 strategies x 9 interventions
  scale.py            K x H x I sweep (Gates 4 and 5)
  artifacts/          pinned JSON + SHA256SUMS
```

See [`REPRODUCE.md`](REPRODUCE.md) to regenerate every number, and
[`RESULTS.md`](RESULTS.md) for what they say.

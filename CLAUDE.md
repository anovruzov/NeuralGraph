# CLAUDE.md

Read this before touching anything. It exists so a session does not spend its
context re-deriving facts that are already established.

## Read in this order

| Need | File |
|---|---|
| Design, invariants, why they hold | `docs/ARCHITECTURE.md` |
| What the numbers say | `docs/RESULTS.md` |
| How to regenerate every number | `docs/REPRODUCE.md` |
| Claims mapped to evidence | `docs/PAPER.md` |
| Why retrieval accuracy is low | `docs/ACCURACY.md` |
| Current status, gates, open decisions | `state.md` |
| Where a symbol lives (`file:line`), without reading the module | `CODEMAP.md` (generated: `python -m tools.codemap`) |

Prefer reading a doc over grepping the source. To find one symbol, grep
`CODEMAP.md` for the file path and jump to the line it names; regenerate the
map after adding or moving a definition (`test_codemap.py` fails when stale). The docs are kept accurate and
are far cheaper than reconstructing the same facts from 5,000 lines.

## Naming traps — get these wrong and you lose an hour

- `NeuralGraph/coordination/` is the cross-node coordinator.
- `NeuralGraph/tesseract.py` is intra-node retrieval fusion. **Not** the
  coordinator. Unrelated. If a task says "Tesseract", ask which one.
- **"Mycelic" appears in no committed file.** Branch names only.
- **NATS / JetStream are not implemented.** Design intent. Everything is
  in-process.
- Commit `b5b9c3e271` **does not exist**. Do not look for it.

## Canonical commands

```bash
pip install -r requirements.txt
python3.11 -m pytest                                          # 294 passed, 1 skipped, 3516 subtests

python3.11 -m NeuralGraph.coordination.benchmark --sweep 30 --format markdown
python3.11 -m NeuralGraph.coordination.scale --format markdown
python3.11 -m NeuralGraph.coordination.experiment --output experiment.json
python3.11 -m NeuralGraph.coordination.figures --check    # paper figures vs artifacts
```

Run `pytest` from the repo root. There is no venv activation step.

## Rules that are not negotiable

1. **A figure is an artifact.** `artifacts/figures/*.svg` is generated from the
   pinned JSON and pinned in the same `SHA256SUMS`. Never hand-edit one, and
   never re-render one to get a test green without knowing why it moved.
2. **Never refresh a pinned artifact to get a test green.** Diff it, find out
   why it moved, re-pin deliberately with the reason in the commit message.
   See `docs/REPRODUCE.md`.
3. **Never skip, disable, xfail or quarantine a test** to get green.
4. **Do not touch the retrieval track** without Anar's explicit say-so.
   `answering.py`, `llm_profile_extractor.py`, `prompts.py`, `reranker.py`,
   `service.py`, `tesseract.py`, `demo/runner.py` belong to Nurman. The
   accuracy *diagnosis* (`evaluation/diagnose_accuracy.py`) is additive and
   touches none of them; the fixes it points to would.
5. **Never synthesize a failure domain.** Domains are read from recorded root
   metadata or reported absent. Inferring one from storage identity, node id,
   file path, or value equality makes every domain metric fiction.
6. **Coordination stays domain-neutral.** `contracts.py` and `core.py` import no
   NeuralGraph storage types. Only `storage_adapter.py` bridges.
7. **Lineage direction is load-bearing.** Derivation parents are the *targets*
   of outgoing HIERARCHY edges plus `source_memory_ids`. `parent_id` and
   `get_edges_to` point the wrong way.
8. **Repair policies see exported contracts only**, never `Placement` internals
   or node-private records. A policy that reads private state is an oracle.

## Results that must not silently change

If any of these moves, a claim about the world has changed — stop and say so.

- Replica count survives a lineage-root failure: **0.00** (3 replicas, and 8-record
  full replication)
- `lineage_aware_repair`: **0.778** · `oracle_min_cut`: **0.889**
- Only min-cut-2 survives `worst_single_domain_failure`
- Every scale verdict invariant across K ∈ {2,3,5,8}, H ∈ {2,3}
- No distributed strategy survives `network_partition` (0.00) — reported against interest
- `lineage_aware` loses to `source_count` in exactly **one** of six cells:
  `node_failure` at **I=1** (0.00 vs 1.00). At I=2 they tie at 1.00. The
  controlling variable is independent roots, not K or H — reported against interest

## Retrieval accuracy — diagnosed, not fixed

Single-hop is 52.1% and **worse than multi-hop** (74.1%). About **76% of
single-hop failures had the evidence retrieved**: it is a generation problem,
not a retrieval one.

Of 513 wrong answers: 327 had the evidence (generation's fault), 139 are
genuine retrieval misses, and **47 are unanswerable — the gold answer appears
nowhere in the source conversation**, so the hard ceiling on this benchmark is
**96.9%**, not 100%. Reaching 85% means converting 60% of every recoverable
failure, and is impossible without retrieval work.

Full diagnosis, the bucketed plan and the arithmetic are in `docs/ACCURACY.md`.
Nothing has been fixed.

## Gate status

0–6 all pass. See `state.md` for evidence per gate and what remains open
(local retrieval accuracy, which is the NeuralGraph track and needs a live
Ollama).

## Ownership

- **Nurman** — NeuralGraph: memory formation, graph dynamics, retrieval.
- **Anar** — coordination, lineage semantics, failure experiments, paper.
- Interface: `NeuralGraph/coordination/contracts.py`. Do not widen it casually.

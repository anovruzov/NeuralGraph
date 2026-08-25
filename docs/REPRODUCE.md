# Reproducing every number

Nothing here needs a GPU, a network, an API key, or a running service.
The whole result set regenerates in about 30 seconds.

---

## Setup

```bash
pip install -r requirements.txt
```

Four dependencies: `numpy`, `aiohttp` (both pulled in transitively by
`NeuralGraph/__init__.py`, which eagerly imports the retrieval stack), plus
`pytest` and `pytest-asyncio`.

## Run everything

```bash
pytest
```

Expected: **273 passed, 1 skipped, 2946 subtests**.

The one skip is a NeuralGraph retrieval integration test that needs live data;
it is unrelated to coordination.

---

## The paper's figures

```bash
python3 -m NeuralGraph.coordination.figures          # rewrite artifacts/figures/*.svg
python3 -m NeuralGraph.coordination.figures --check  # verify, write nothing; exit 1 if stale
```

Figures 2–5 are plotted from the pinned JSON, so they are byte-reproducible and
are pinned in the same `SHA256SUMS`. `--check` is the fast way to find out
whether a benchmark change has silently invalidated a figure in the paper.
Figure 1 is a hand-drawn schematic and is not generated.

For LaTeX, convert once: `rsvg-convert -f pdf -o fig3.pdf fig3_pareto_survival_vs_storage.svg`.

---

## The three canonical commands

```bash
# Reconstruction experiment: 3 strategies, before / after failure / after repair
python3 -m NeuralGraph.coordination.experiment --output experiment.json

# Capability survival: 8 strategies x 9 interventions
python3 -m NeuralGraph.coordination.benchmark --sweep 30 --format markdown
python3 -m NeuralGraph.coordination.benchmark --output benchmark.json

# Scale and generalization: K x H x I
python3 -m NeuralGraph.coordination.scale --format markdown
python3 -m NeuralGraph.coordination.scale --output scale.json
```

Add `--seed N` to any of them. `--format markdown` prints tables; the JSON is
the source of truth.

---

## Verifying reproducibility yourself

Output is byte-identical across runs and across hash seeds:

```bash
python3 -m NeuralGraph.coordination.benchmark --output /tmp/a.json
PYTHONHASHSEED=99991 python3 -m NeuralGraph.coordination.benchmark --output /tmp/b.json
sha256sum /tmp/a.json /tmp/b.json   # identical
```

And against the committed artifacts:

```bash
cd NeuralGraph/coordination/artifacts && sha256sum -c SHA256SUMS
```

| artifact | sha256 (first 8) |
|---|---|
| `experiment_seed20260813.json` | `f712e12d` |
| `benchmark_seed20260813.json` | `2d8564e7` |
| `benchmark_sweep30_seed20260813.json` | `3e45ebc0` |
| `scale_seed20260813.json` | `61be1f24` |

`test_artifact_reproducibility.py` regenerates each from its canonical command
and compares byte for byte, so drift fails the suite rather than passing
silently.

---

## If a pinned artifact test fails

**Do not refresh the artifact to get green.** That converts a detected
regression into a silently restated result.

1. Diff the regenerated JSON against the committed one and find which fields
   moved.
2. Work out *why*. A changed survival verdict is a different claim about the
   world; a changed `recovery_steps` is a cost change.
3. If the change is correct, re-pin deliberately and record the reason in the
   commit message, naming which fields moved and which did not.

This has happened once, legitimately: adding slot relevance to repair ordering
moved `recovery_steps` 2 → 1 and `recovery_events` 28 → 26, with **no survival
verdict changed**. That is a cheaper recovery for identical survival, and the
commit says so.

---

## Real-storage validation

```bash
pytest NeuralGraph/tests/test_benchmark_real_storage.py \
       NeuralGraph/tests/test_failure_domains_real_storage.py -v
```

These build real SQLite databases in a temp directory, one per node, seed them
with episodes and origins connected by HIERARCHY edges, and drive the real
adapter and lineage resolver. Lineage is derived from storage, not declared.

---

## Enabling failure domains

Off by default, deliberately — see [`ARCHITECTURE.md`](ARCHITECTURE.md).

```python
resolver = StorageLineageResolver(storage, domain_key="failure_domain")
```

The domain is read from the metadata of the lineage **roots** the walk resolves.
To record one, put it on the origin node:

```python
NeuralNode(
    node_id="origin-O2",
    layer=NodeLayer.MESSAGE,
    metadata={"failure_domain": "ingest-feed-alpha"},
    ...
)
```

A root with no recorded domain contributes none. Unrecorded provenance can
never read as independence.

---

## Layout

```
NeuralGraph/coordination/artifacts/
  SHA256SUMS                            digests for the JSON below
  experiment_seed20260813.json
  benchmark_seed20260813.json
  benchmark_sweep30_seed20260813.json
  scale_seed20260813.json
  RESULTS.md                            generated survival tables
  SCALE.md                              generated scale tables

docs/
  ARCHITECTURE.md                       invariants and design (Gate 6)
  RESULTS.md                            what the numbers say
  REPRODUCE.md                          this file
  PAPER.md                              claims mapped to evidence
```

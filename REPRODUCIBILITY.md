# REPRODUCIBILITY

Every command below was executed on 2026-09-06 on macOS arm64 (Darwin
24.6.0) with CPython 3.11.14, numpy 2.4.6, pytest 9.1.1, pytest-asyncio
1.4.0, aiohttp 3.14.3, in a fresh venv satisfying `requirements.txt`.
Nothing here is aspirational. `docs/REPRODUCE.md` (present at commit
`13a1729`) is the canonical in-tree procedure; this file records what an
independent researcher can verify today, at which commit, and what they
cannot.

## What reproduces (verified)

### Coordination/lineage results — commit `13a1729a4386162ba2540447eca7a362319a0635`

```bash
git clone https://github.com/anovruzov/NeuralGraph
cd NeuralGraph
git checkout 13a1729a4386162ba2540447eca7a362319a0635
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest              # expect: 277 passed, 1 skipped
(cd NeuralGraph/coordination/artifacts && shasum -a 256 -c SHA256SUMS)   # 9/9 OK
(cd evaluation/artifacts && shasum -a 256 -c SHA256SUMS)                 # 3/3 OK
# regenerate to a temp dir — never overwrite the pinned artifacts:
.venv/bin/python -m NeuralGraph.coordination.experiment --output /tmp/e.json
.venv/bin/python -m NeuralGraph.coordination.benchmark  --output /tmp/b.json
.venv/bin/python -m NeuralGraph.coordination.benchmark  --sweep 30 --output /tmp/b30.json
.venv/bin/python -m NeuralGraph.coordination.scale      --output /tmp/s.json
shasum -a 256 /tmp/e.json NeuralGraph/coordination/artifacts/experiment_seed20260813.json  # equal
# (same for b/b30/s vs their pinned counterparts)
.venv/bin/python -m NeuralGraph.coordination.figures --check             # 5 figures match
```

Observed here: all four regenerated JSONs are **bitwise identical** to
the pinned artifacts; regeneration is `PYTHONHASHSEED`-independent
(verified with `PYTHONHASHSEED=99991`). Reproduction class: **bitwise
deterministic** — no tolerances needed. Runtime: ~1 s per command, no
network, no credentials, no GPU. Seed: `20260813` (default; `--seed N`
supported by experiment/benchmark/scale, **not** by `figures`).

Caveats: `RESULTS.md`/`SCALE.md` in the artifacts dir have no pinned
hashes and no file-writing regeneration command (only `--format
markdown` to stdout). `evaluation/artifacts/SHA256SUMS` verifies, but
docs don't state its producer commands (`evaluation/diagnose_accuracy.py`,
`evaluation/retrieval_ceiling.py` by inspection). The artifacts' exact
historical generator commit is **UNKNOWN** (no metadata inside the
JSONs); what is proven is that `13a1729`'s code regenerates them
bitwise.

### Manuscript pipeline — commit `9125388` (currently LOCAL-ONLY, branch `locomo-six-fix-loop`)

```bash
git checkout 9125388
python -m pytest                                   # expect: 346 passed, 1 skipped
python -m tools.build_paper_tables  --check        # PASS
python -m tools.build_paper_figures --check        # PASS
(cd NeuralGraph/coordination/artifacts && shasum -a 256 -c SHA256SUMS)  # 4 JSONs OK
```

Until this branch is pushed, an independent researcher **cannot** obtain
this revision — the single biggest reproducibility gap for the
manuscript. A further uncommitted addendum (renderer list-continuation
fix; HTML digest deliberately `05f00de6…→93b3fb3f…`;
`python3.11 -m tools.render_paper --pdf` for the PDF) exists only in the
dirty working tree.

## Test-count notes

Subtest totals (2,960 @`13a1729`; 3,382 @`37103e5`/`9125388`) are from
prior records; pytest 9.1.1 does not report subtest counts separately
here even with pytest-subtests loaded. All parent tests pass, and a
failing `unittest` subTest fails its parent, so green bounds them.
`origin/main` (`8785d69`) does not run: full collection dies on
`demo/test_instruments_fix.py` (no pytest.ini at main), targeted
collection has 7 pre-existing setup errors. Use `13a1729`+.

## What does NOT reproduce (honest gaps)

- **PR #3 retrieval numbers** (72.2% etc.): require LM Studio with
  `google/gemma-4-e4b` + `text-embedding-nomic-embed-text-v1.5` (or
  Ollama), and an `OPENAI_API_KEY` for the GPT-4o judge — live model
  calls, not exactly reproducible; no seeds; artifacts omit the run
  configuration (`RETRIEVAL_MODE`, `ONLY_CONV`, flags) and hardcode the
  judge field. The committed capstone/kg/mega/flat run JSONs allow
  **replay-style recomputation of aggregates only** (the 72.2/66.9
  arithmetic re-derives exactly from `capstone_full.json` +
  `capstone_rest.json` vs `demo/maximal.json`, dropping capstone_full's
  6 conversation-5 rows — a rule documented nowhere in the PR).
- **PR #3 ablations H1/H5/H7/H8/N1/N4/N5/H9/N3**: outputs not in the
  repo (author-machine paths); unverifiable as shipped.
- **PR #1 claims**: no committed artifacts at all.
- Environment sensitivity: a prior audit hit an import failure under
  NumPy 2.5.2 (Python 3.12.13, Linux); not observed here (2.4.6).
  `requirements.txt` allows `numpy<3` — pin below 2.5 if it recurs.

## Dirty-state policy

The audited checkout keeps deliberate uncommitted work (fingerprinted in
the audit evidence). Reproduction commands must run from clean commits —
never claim fresh-clone reproduction of anything that only exists dirty.

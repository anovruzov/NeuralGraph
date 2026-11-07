# Operation 100M handoff package (2026-10-31)

Start with `HANDOFF.md`.

| Path | What it is |
|---|---|
| `HANDOFF.md` | Current state, warnings, open problems, next steps (working-tree version, includes uncommitted edits) |
| `docs/` | RESULTS, ARCHITECTURE, REPOSITORY_AUDIT, HINDSIGHT_COMPARISON, Hindsight primary-source extraction (working-tree versions) |
| `verified/` | Critic-verified 100K result and its 3 repeats, loop state (all rounds), latest plan/evidence/critique, gates |
| `git/operation-100m.bundle` | Commit `43937131` on branch `operation-100m`; prerequisite `f0ddff96` (`anovruzov/NeuralGraph` `origin/main`) |
| `git/uncommitted-RESULTS.md.diff` | RESULTS.md edits made after the commit (load-contamination notes) |
| `other-session-uncommitted/` | Work by a second Claude session in the same checkout: **not reviewed, not committed** (channel ablation, mixed read/write run, its main.rs additions as a diff) |
| `SHA256SUMS` | Checksums of every file in this package |

Restore the branch into a NeuralGraph clone that has `f0ddff96`:

```bash
git bundle verify operation-100m.bundle
git fetch operation-100m.bundle refs/heads/operation-100m:refs/heads/operation-100m
```

Not included (regenerable, large, local only on the original Mac under `operation-100m/work/`): the 100K SQLite bank
(449 MB), IVF index files (~310 MB each) and the nomic-embed-text embedding matrices (~313 MB). Recreate them with
the "Reproduce" steps in `docs/RESULTS.md`; the vector hash in the manifest shows whether your embeddings match.

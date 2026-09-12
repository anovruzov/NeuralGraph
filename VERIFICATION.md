# Verification report

Executed 2026-08-25 on `claude/mycelic-gate-0-recovery-tjl82x`.
Everything below is a real command and its real output.

## Environment

```
$ /opt/homebrew/bin/python3.11 --version
Python 3.11.14
```

**Interpreter trap.** Bare `python3` on this machine is `/usr/bin/python3`
(3.9.6) and **cannot collect the suite** — `TypeError: unsupported operand
type(s)` on `X | Y` annotations in all 13 test files. Bare `pytest` is not on
`PATH`. All canonical commands were corrected to `python3.11 -m …`.

## Test suite

```
$ /opt/homebrew/bin/python3.11 -m pytest -q
266 passed, 1 skipped, 3382 subtests passed in 12.68s
```

The single skip is a NeuralGraph retrieval integration test requiring live
data; unrelated to coordination.

Previously documented as "185 passed, 1 skipped, 347 subtests" in `CLAUDE.md`,
`state.md`, and `docs/REPRODUCE.md`. Not a changed result — tests were added
since (`test_diagnose_accuracy`, `test_retrieval_ceiling`,
`test_replay_generation`, plus 13 added this session). All three docs corrected.

## Artifact digest verification

Coordination artifacts:

```
$ cd NeuralGraph/coordination/artifacts && shasum -a 256 -c SHA256SUMS
benchmark_seed20260813.json: OK
benchmark_sweep30_seed20260813.json: OK
experiment_seed20260813.json: OK
scale_seed20260813.json: OK
```

Evaluation artifacts:

```
$ cd evaluation/artifacts && shasum -a 256 -c SHA256SUMS
accuracy_diagnosis.json: OK
failure_modes.json: OK
retrieval_ceiling.json: OK
```

**7 of 7 pinned artifacts verify.**

## Artifact regeneration

Each regenerated from its canonical command under Python 3.11 and compared
byte-for-byte to the pinned copy:

| Artifact | sha256 (first 16) | Result |
|---|---|---|
| `experiment_seed20260813.json` | `f712e12d85f8b63a` | **MATCH** |
| `benchmark_seed20260813.json` | `2d8564e7cd717a23` | **MATCH** |
| `benchmark_sweep30_seed20260813.json` | `3e45ebc05d653507` | **MATCH** |
| `scale_seed20260813.json` | `61be1f24d6ef4808` | **MATCH** |

## Manuscript tables

```
$ python3.11 -m tools.build_paper_tables --check
all pinned artifact digests match

$ python3.11 -m tools.build_paper_tables --output <tmp> && diff <tmp> docs/paper/tables.md
(identical)
```

`docs/paper/tables.md` regenerates identically. No manuscript number is typed
by hand.

## Headline numbers, read from the artifact

| strategy | survival | silent forgetting | mean bytes |
|---|---|---|---|
| `isolated_local` | 0.000 | 0.000 | 1,371 |
| `centralized` | 0.111 | 0.667 | 3,020 |
| `fixed_distributed_replication` | 0.556 | 0.444 | 4,943 |
| `source_count_repair` | 0.556 | 0.444 | 4,941 |
| `full_replication` | 0.667 | 0.333 | 9,877 |
| `random_path_diversification` | 0.704 | 0.296 | 5,327 |
| `lineage_aware_repair` | **0.778** | 0.222 | 5,448 |
| `oracle_min_cut` | **0.889** | 0.111 | 6,461 |

30 seeds · 8 strategies × 9 interventions = 72 cells.

Counter-results confirmed from the same artifact:

- `network_partition`: 0.00 for all six distributed strategies; only
  `centralized` and `full_replication` = 1.00
- `worst_single_domain_failure`: only `oracle_min_cut` = 1.00 (30/30)
- `lineage_root_failure`: `full_replication` = 0.00 (0/30);
  `lineage_aware_repair` = 1.00 (30/30); `random_path_diversification` =
  0.667 (20/30), matching its closed-form 2/3
- Scale: all 12 independence cells `invariant_across_scale = true`

## Regression check on doc edits

The suite was re-run after every documentation edit and the archive move.
Same result: 266 passed, 1 skipped. No code was modified in this cleanup.

## Security

| Check | Result |
|---|---|
| `git log --all -- .env` | **empty — never committed on any branch** |
| `git ls-files --error-unmatch .env` | not tracked |
| `git check-ignore -v .env` | `.gitignore:7:.env` — now ignored |
| Tracked secrets elsewhere | none found |
| Credential rotation required | **no** |
| History rewrite required | **no** |

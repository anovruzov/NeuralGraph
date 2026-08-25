# Overnight LoCoMo >=80 — starting state

Captured 2026-08-25 before any code change.

## Repository

| | |
|---|---|
| branch | `locomo-six-fix-loop` |
| commit | `673d4235caada4ab1a4e3df85d10d1a90534ddf1` |
| python | `/opt/homebrew/bin/python3.11` — 3.11.14 |
| test suite (executed, not remembered) | **303 passed, 1 skipped, 3382 subtests** |

`.env` ignored via `.gitignore:7`; not tracked, never committed on any branch.
No secret is printed, staged or transmitted anywhere in this run.

### Dirty / untracked (no secret contents)

Modified: `evaluation/improvement_loop/{batch,runner}.py` (v2 dispatch — pending revert).
Untracked: `prompts_v2.py`, `v2_analysis.py`, `test_locomo_fix1_abstention_gate.py`,
`evaluation/artifacts/locomo_loop/**` (baseline, v2 cache, RCA, logs), `demo/.cache/`.

## Protected, not touched

- `stash@{0}` "pre-replay 2026-08-21" — metadata read only; never applied, popped or dropped.
- **PID 49733**, elapsed 9h37m — another session's full 1540-question replay
  (`replay_generation --provider ollama --model qwen2.5:7b-instruct`). Holds the single
  Ollama slot (`llama-server --np 1`). Not killed, not throttled, not interfered with.
- The v2/round-1 batch job, allowed to finish so its RCA artifact completes.

## Frozen evaluation configuration

| | |
|---|---|
| corpus | `demo/maximal.json`, digest `916ca308d5e41cba…` |
| splits | 40/20/40, seed 20260825 — dev 624 / val 305 / locked 611 |
| validation eval set | 60 questions, 15/category, digest `8077c40ba2e7cff2` |
| locked eval set | 60 questions, 15/category, digest `c04acf27b0e939d7` — **not inspected** |
| answerer | `qwen2.5:7b-instruct` (local Ollama, unpaid) |
| judge | `gpt-4o` — same judge as the recorded baseline |
| temperature | 0.0 · top-k: `recorded` (replay holds retrieval fixed) |
| prompt | v1, hash `5e923dc613887b80` · config hash `cd9098dfbdd0ad06` |
| ordering | relevance-ranked (retrieval order), as recorded |

Splits and eval sets are reused, never resampled.

## Previous best valid artifact

`evaluation/artifacts/overnight_80/baseline.json` — **24/60 = 0.400** judged accuracy,
0 operational errors, run valid. Corpus-weighted 0.4309.

## Six-fix exercise: not used

The discarded six-fix/v2 work is **not** in the production path. `RunConfig` defaults to
`v1-baseline`; the v2 prompt is reachable only via an explicit flag and is pending full
revert. Its outputs are preserved as a rejected experiment, not reused as a result.

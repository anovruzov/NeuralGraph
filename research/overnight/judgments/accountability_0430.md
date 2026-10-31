# Accountability check, 2026-10-28 04:26 CDT

Read-only audit. No model calls; no jobs started or stopped. Sources: PLAN section 6, STATE (04:12), LEDGER (28 data rows), RUNLOG (6 lines), `ps`, `lms ps`, `.context/overnight/{locks,results,logs}`.

## Checks

| # | check | result | evidence |
|---|---|---|---|
| 1a | STATE "running" jobs are live | FAIL (sweep entry stale) | lme_base_ingest: gpu.sh 44941 -> chat-memory 44950, live. lme_sub18_base_ingest: gpu.sh 47686 -> chat-memory 81492, live. STATE says `sweep_lme_p20c` is running at 04:25, but its `.done` exists and it ended rc=0 at 04:21:21. `sweep_sub18_E56b` also ended rc=0 at 04:22:09 and is not in STATE. |
| 1b | RUNLOG START without END has a live process | PASS | Two open entries: lme_base_ingest (02:05:07) and lme_sub18_base_ingest (03:58:12). Both live (see 1a). The grep found no orphan process. |
| 1c | Queued jobs | INFO | lme_sub18_E3_ingest (gpu.sh 82436) and lme_sub18_E48_ingest (gpu.sh 82446) started 04:06 and wait for a free slot. No RUNLOG START yet, which is correct. |
| 1d | RUNLOG completeness | FAIL | 6 lines. The p20, p20b, p20c and sub18_E56b sweeps are not recorded anywhere in RUNLOG. |
| 2 | GPU slot locks point at live pids | FAIL (bookkeeping bug; locks are valid) | `slot1/pid` = 44936 and `slot2/pid` = 47681: neither is running. The real holders are gpu.sh 44941 and 47686 (ppid 1). gpu.sh runs `echo $$` inside its `( ... ) &` subshell, which in zsh returns the launcher's pid; the launcher exits after `disown`. Exactly two lock dirs and two live holders, so slot accounting is correct. |
| 3 | Every ledger row has a result file, and strict/lenient/substring match `summary.overall` | PASS | 28/28 rows. All 26 local files exist under `.context/overnight/results/`. All 28 match within 0.05 on strict, lenient and substring. REF and REF-L cite `ankara/.context/exp/`, which is not in this worktree; the files are in `memory-architecture-stats/.context/exp/` and match (54.0/80.0/44.0 and 36.4/77.3/21.2). turn@k is not in the acc JSON and was not checked; E56-s18 turn@k 94.4 matches its sweep log. All judges are google/gemma-4-e4b. |
| 4 | Disk, overnight size, both models listed | PASS (listing), FAIL (TTL claim) | `df -h .`: 272 GiB free of 1.8 Ti (85% used). `.context/overnight`: 44 MB (db 40 MB, results 3.8 MB). `lms ps`: google/gemma-4-e4b IDLE, no TTL; qwen3.8-9b-mlx GENERATING with ttlMs 3600000 (1 h). STATE says both are persistent; qwen is not. |
| 5 | Ingest rate and ETA | FAIL (behind plan) | lme_base: 26/50 at log mtime 04:24. Last 5 (q22-26): 137.2, 399.1, 1181.6, 148.2, 6.5 s; mean 374.5 s, median 148.2 s. 24 left: ETA 06:55 at the last-5 mean, 06:36 at the run average (325 s/q). STATE says ~06:00; PLAN section 9 had the baseline done by ~03:30. lme_sub18_base: 3/18, so no 5-question mean exists: 383.1, 500.0, 737.6 s, mean 540 s. 15 left: ETA 06:41-06:45. STATE says 60-90 min total; the real total is about 2.7 h. |
| 6 | Logs modified in last 30 min: Traceback, panicked, rc=1, HTTP 4 | FAIL (HTTP 400, retried) | No Traceback, panicked or rc=1 in any file touched in the last 30 min. HTTP 400 "Model unloaded" appears in 5 p20 logs, 51 hits in total: cascauto_v1 20, auto_v1 10, vk15_concise 9, concise_quotes_kl10 6, k20 quotes 6. All five runs finished with rc=0 and n=20 results containing no error text. Outside the window, sweep_lme_p20.log (03:52) has 4 "chat failed after retries: HTTP 400" errors from the earlier failed launch. Each p20 log also has 2-4 "strict_v2: no VERDICT" WARN lines. |

## Notes
- The reload-before-run fix in sweep.sh does not stop mid-run unloads. qwen still has a 1 h TTL, which is the likely cause of the 400s. The cause is not confirmed.
- Current state: 2 slots busy (lme_base, lme_sub18_base), 2 jobs queued, no sweep running, no orphans.
- Other: an unrelated `mlx_vlm.server` (Qwen3-VL-8B, port 8099, started 26 Aug) is still running. Not part of the overnight jobs; left alone.

## Actions the orchestrator should take
1. Reload qwen3.8-9b-mlx with no TTL now. Check `lms load --help` for the no-TTL flag; I did not run it. Gemma is already persistent.
2. Correct STATE.md: the p20c and sub18_E56b sweeps are finished (rc=0), not running. Use the real ETAs: lme_base 06:36-06:55, sub18_base about 06:45. Fix the qwen persistence claim.
3. Re-plan the night. The S0 window (03:40-05:30) has slipped about 3 h, because the LME-50 baseline now finishes around 06:40-06:55. E3 and E48 are queued behind a roughly 2.7 h sub18 base run, while PLAN section 9 budgets 35 min per screen, about 4x too low. Decide whether to drop E48 or re-scope the sub18 base. Do not raise SLOTS without checking memory.
4. Flag the 5 p20 rows that had unload retries in their ledger notes, or re-run them after the qwen reload. The result files show no error text, but I could not check each retried answer.
5. Fix gpu.sh pid bookkeeping before the next queue, for example with `zmodload zsh/system` and `${sysparams[pid]}`. Leave the two current locks alone while their jobs run.
6. Add START/END lines for the p20, p20b, p20c and sub18_E56b sweeps to RUNLOG.

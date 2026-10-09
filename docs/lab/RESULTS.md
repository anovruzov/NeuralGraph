# Cloud lab results

Every number below is copied from a lab report printed in a GitHub Actions job log (between
`=== MYCELIC-LAB … BEGIN/END ===` markers) or from a shard's own summary. Each row names its run. **Synthetic data
only**: every record and narrative was generated from a seed. **Runner hardware only**: one shared 4-vCPU
GitHub-hosted runner per shard, whose CPU model varies between shards; compare timings only between rows of the same
CPU model, and never read them as site hardware. A unit without a result is listed with its reason, not left out.

## Run 1: check-001 (pins and first timings)

[Run 37834297419](https://github.com/anovruzov/NeuralGraph/actions/runs/37834297419), request
`lab/requests/check-001.json`, commit `a2e0961`, 2026-10-08 19:45–19:58 UTC; report from the `aggregate` job
(113512587135). Purpose: download and verify the pinned llama-server archive and the four local models, then time
three E3 requests per workload at concurrency 1.

**Provisioning: all five files verified.** Server `b11476` (sha `2cda5ff93639`, by the GitHub API) and the four GGUF
files (by the model hub's API): a-0p5b `74a4da8c9fdb`, a-1p5b `6a1a2eb6d156`, a-4b `7485fe6f11af`, b-2b
`ac71e9e32c0b`. Their hashes are now pinned in `lab/models.lock.json` (commit `132386d`), which with
`lab/models.json` says which model each id is (`docs/lab/MODELS.md`).

**Latency (E3, concurrency 1, 3 requests per cell, all 3 answered):**

| Model | Runner CPU | Workload | End-to-end median s | End-to-end p95 s | First token median s | Decode tokens/s median |
|---|---|---|---|---|---|---|
| a-1p5b | AMD EPYC 7763 | extraction | 29.541 | 29.575 | 25.506 | 19.0 |
| a-1p5b | AMD EPYC 7763 | short | 13.253 | 13.987 | 12.085 | 22.4 |
| a-4b | AMD EPYC 9V74 | extraction | 84.981 | 85.353 | 75.934 | 7.0 |
| a-4b | AMD EPYC 9V74 | short | 40.271 | 42.420 | 35.597 | 9.0 |

**Units without a result:**

| Unit | Status | Reason (from the report) | What was done |
|---|---|---|---|
| e3-a-0p5b | invalid | "the model answered too few calls: e3_short_answer ok share 0.666667 below 0.95" | Kept as a model result: the 0.5B model answered 2 of 3 short calls. The smoke run's G0 uses a-1p5b instead. |
| e3-b-2b | skipped | "warm-up: e3_short_answer stopped at the token limit; set server_args --reasoning off or raise max_tokens" | `--reasoning off` added for b-2b in `lab/models.json` (commit `132386d`) |

## Run 2: smoke-001 (smoke run and sizing of the main run), in progress

[Run 37841045975](https://github.com/anovruzov/NeuralGraph/actions/runs/37841045975), request
`lab/requests/smoke-001.json`, commit `125fab1`, started 2026-10-08 20:39 UTC. Purpose: every model through E3 at
concurrency 1 and 4, the six-site simulation on the small plant (`sim`, 34 weeks, seed 1) with each model in the loop,
and the G0 canary leakage scan with a-1p5b.

| Shard | Model | State | Units | Source |
|---|---|---|---|---|
| s001-a-0p5b | a-0p5b | finished, job marked failed (one unit skipped) | `sim-a-0p5b-s1` **ok**, 3,135.7 s, run id `sim-a-0p5b-s1-57a30b54`; `e3-a-0p5b` **skipped**: "warm-up: e3_short_answer stopped at the token limit" | shard summary in job 113530993032 |
| s002-a-1p5b | a-1p5b | finished, job succeeded (AMD EPYC 9V45) | `sim-a-1p5b-s1` **ok**, 7,288.6 s, run id `sim-a-1p5b-s1-134ba3ae`; `g0-a-1p5b` **ok**, 2,169.3 s; `e3-a-1p5b` **ok**, 247.2 s | shard summary in job 113530992802 |
| s003-a-4b | a-4b | finished, job marked failed (one unit timed out) (AMD EPYC 9V74) | `sim-a-4b-s1` **ok**, 6,749.3 s, run id `sim-a-4b-s1-f0ebc485`; `e3-a-4b` **timed_out** at its 1,200 s unit limit ("unit timed out", no result) | shard summary in job 113530992829 |
| s004-b-2b | b-2b | running | e3, sim | — |

**What is already established:** three of the four models completed the whole six-site simulation on a runner CPU
(every site's extraction through the model, the cells, the detectors, pushdown verification and the scorecard):
a-0p5b in 52 minutes, a-4b in 1 h 52 min and a-1p5b in 2 h 1 min, each on a different CPU model, so the times are not
comparable with one another. The canary scan with a-1p5b in the loop (G0) completed too. Its scorecard numbers are in
the shard's artifact and reach the log only in the run's `aggregate` report, which runs after the last shard; they
are added here when it does, whatever they show. The 0.5B model again failed the short-answer warm-up, this time by
running to the token limit; that is a property of the model, recorded as such. a-4b's latency unit needs more than
the 20 minutes the request gave it: 8 requests per workload at concurrency 1 alone take about 1,000 s at run 1's
medians (40.3 s short, 85.0 s extraction), before concurrency 4; the main run must give it more minutes or fewer
requests.

## Run 3: openfda-001 (the public replay, model-free, real data)

[Run 37863725435](https://github.com/anovruzov/NeuralGraph/actions/runs/37863725435), request
`lab/requests/openfda-001.json` (settings and their rule: `docs/collective/replay/CHOICE-001.md`), commit `b561a34`,
2026-10-09 00:15–00:21 UTC; report from the `aggregate` job (113607299289). The first attempt (run 37861226513) was
refused by openFDA before any data: the lab asked for 1000-record pages without an API key (fixed in `4c05eb1`).

| Channel | Recalls in scope | Found | False alarms | Post-recall alerts |
|---|---|---|---|---|
| X | 8 | 0 | 0 | 0 |
| S | 8 | 0 | 0 | 0 |
| R (model-free) | 8 | 0 | 0 | 0 |

No channel raised any alert. The replay warned `low_resolution`: the `device_quality` pack's id formats resolve few of
the manufacturer's real ids, so the zero is structural (the pipeline did not see the records as entities), not a
measurement of detection. Fetched: 28,886 event reports (none truncated) and 22 recall records, 8 of them under the
listed firm names. The requester (the AI agent) declared it may have seen recall outcomes before choosing.

## What has not run

- **main-001** (`lab/templates/main.json`): the main simulation over more seeds and the sealed X1 run with each model;
  planned after smoke-001 sizes it.
- **A replay with a vocabulary for the manufacturer's real ids** (run 3 shows the synthetic pack's id formats do not
  resolve them), and **N1 and E1 on real narratives**, which need a person to label the sheets.
- **A hosted (non-local) model:** the lab supports one through `MYCELIC_LAB_HOSTED_API_KEY`; no key is configured.

# Cloud lab results

Every number below is copied from a lab report printed in a GitHub Actions job log (between
`=== MYCELIC-LAB … BEGIN/END ===` markers), from a shard's own summary or, for the vehicle replay, from its audit
printed between `VEHICLE-AUDIT` markers. Each row names its run. **Synthetic data, except runs 3 and 4 and the vehicle
replay**: their records are public FDA reports and NHTSA complaints; every other record and narrative was generated
from a seed. **Runner hardware only**: one shared 4-vCPU GitHub-hosted runner per shard, whose CPU model varies
between shards; compare timings only between rows of the same CPU model, and never read them as site hardware. A unit
without a result is listed with its reason, not left out.

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

## Run 2: smoke-001 (smoke run and sizing of the main run)

[Run 37841045975](https://github.com/anovruzov/NeuralGraph/actions/runs/37841045975), request
`lab/requests/smoke-001.json`, commit `125fab1`, 2026-10-08 20:39 UTC to 2026-10-09 00:15 UTC; report from the
`aggregate` job (113605588859, `report.md` sha256 `8b29e9427bfd…`). Purpose: every model through E3 at concurrency 1
and 4, the six-site simulation on the small plant (`sim`, 34 weeks, seed 1) with each model in the loop, and the G0
canary leakage scan with a-1p5b.

| Shard | Model | Runner CPU | Units |
|---|---|---|---|
| s001-a-0p5b | a-0p5b | AMD EPYC 9V74 | `sim` **ok**, 3,135.7 s; `e3` **skipped**: "warm-up: e3_short_answer stopped at the token limit" |
| s002-a-1p5b | a-1p5b | AMD EPYC 9V45 | `sim` **ok**, 7,288.6 s; `g0` **ok**, 2,169.3 s; `e3` **ok**, 247.2 s |
| s003-a-4b | a-4b | AMD EPYC 9V74 | `sim` **ok**, 6,749.3 s; `e3` **timed_out** at its 1,200 s unit limit |
| s004-b-2b | b-2b | Intel Xeon Platinum 8573C | `sim` **ok**, 12,655.0 s; `e3` **skipped**: the same warm-up message, although b-2b runs with `--reasoning off` since run 1 |

The three shards with a unit that did not finish are marked failed by the workflow; every simulation finished.

**Canary leakage scan** (G0, a-1p5b in the loop, `device_quality`, seed 1, 200 records: a smaller check than the
1,000-record protocol scan): **passed**. 875 canaries planted, **0** crossed a boundary, 0 shingle-overlap bytes, 0
model path problems; the positive control was hit 170 times, so the scan sees what it looks for. Text only: timing,
sizes and other side channels are not covered.

**Latency** (E3, a-1p5b, AMD EPYC 9V45, 6 measured requests per cell, all answered):

| Workload | Concurrency | End-to-end median s | p95 s | First token median s | Decode tokens/s median | Requests/s |
|---|---|---|---|---|---|---|
| extraction | 1 | 10.915 | 11.559 | 8.695 | 31.7 | 0.092 |
| extraction | 4 | 43.046 | 57.308 | 15.722 | 5.3 | 0.099 |
| short | 1 | 5.024 | 5.207 | 4.049 | 35.3 | 0.203 |
| short | 4 | 18.005 | 26.718 | 9.718 | 9.7 | 0.216 |

(Run 1 timed the same model on an AMD EPYC 7763 at 29.5 s per extraction: the CPU model matters more than the run.)

**Six-site simulation** (plant `sim_small`, seed 1, 34 weeks, the same synthetic world for every model, digest
`fd9362003d07`; 4 planted patterns, 3 of them narrative-only; a no-plant control ran and made no chance find in any
channel). Planted patterns found, of 4:

| Model | X, model reads the narratives | X, lexical extractor | S, codes only | R, model-free | U, reference | Single site | Rules |
|---|---|---|---|---|---|---|---|
| a-0p5b | 1 | 4 | 1 | 1 | 1 | 1 | 0 |
| a-1p5b | 1 | 4 | 1 | 1 | 1 | 2 | 0 |
| a-4b | 2 | 4 | 1 | 1 | 3 | 2 | 0 |
| b-2b | 1 | 4 | 1 | 1 | 2 | 3 | 0 |

Alerts (false alarms) for X with the model: 5 (4), 9 (8), 11 (9), 11 (10); the lexical extractor 13 (9); S and
model-free R 4 (3) each. Lifts bootstrapped over the patterns: X with the model minus S (and minus model-free R) is 0
for a-0p5b, a-1p5b and b-2b and +0.25 [0, 0.75] for a-4b; X with the model minus X with the lexical extractor is
-0.75 [-1, -0.25] for a-0p5b, a-1p5b and b-2b and -0.50 [-1, 0] for a-4b. S and model-free R cannot see a
narrative-only pattern by construction (3 of the 4).

**Pushdown verification** (HQ's candidates checked at the sites, no raw text crossing): candidates, true, supported,
pushdown average precision against the detector score's: a-0p5b 5, 1, 5, 0.333 against 0.200; a-1p5b 9, 1, 6, 0.750
against 1.000; a-4b 10, 1, 7, 1.000 against 1.000; b-2b 10, 0, 0, not defined. Raw text bytes crossed: 0 in every unit.

**Model call latency from the ledgers** (sim, median ms per call, extraction and judging): a-0p5b 2,366 and 862;
a-1p5b 3,910 and 868; a-4b 4,274 and 3,435; b-2b 9,580 and 2,995 (1,031 extractions each, each model on its own CPU).

**Sizing for the next request** (estimated minutes, and suggested minutes a quarter above, for one seed of this
plant): a-0p5b 52.3 and 66; a-1p5b 121.5 and 152; a-4b 112.5 and 141; b-2b 210.9 and 264.

**What this says, and what it does not.**

- **On this synthetic plant, the small models' reading did not beat the codes.** X with the model found as many
  planted patterns as S and model-free R (one of four) with three of the models, and one more with a-4b, an interval
  that includes zero. The lexical extractor, exact on the generator's own text by construction, found all four: the
  patterns are findable from the narratives, and the models' extraction errors lost them. It is four patterns and one
  seed, so none of it is an estimate, and it measures extraction fidelity on generated text, not the value of reading
  real narratives. It is still the plainest result so far, and it is unfavourable: as configured, a model of 4B
  parameters or fewer on a CPU did not add detection on the plant built to reward it.
- **The leakage scan with a model in the loop passed** (0 of 875 canaries), for text only.
- **Every model ran the whole six-site pipeline on a 4-vCPU runner** (52 minutes to 3 h 31 min for 1,031 records per
  model, on different CPU models), and pushdown verification ranked the one true candidate first for a-4b.
- E3 is measured for a-1p5b only: a-0p5b and b-2b stop at the short-answer token limit in the warm-up (b-2b even with
  `--reasoning off`), and a-4b needs more than 20 minutes (8 requests per workload at concurrency 1 alone take about
  1,000 s at run 1's medians).

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

## Run 4: openfda-002 (public replay 002, model-free, real data, readable identifiers)

[Run 37866601007](https://github.com/anovruzov/NeuralGraph/actions/runs/37866601007), request
`lab/requests/openfda-002.json` (replay 001's settings with the replay pack `lab/packs/device_quality_bd`, built by the
rule in `docs/collective/replay/CHOICE-002.md` from reports received before the window), commit `4a5a34f`,
2026-10-09 00:48 UTC; report from the `aggregate` job (113616573922). The report now carries coverage, alert counts
and each channel's circular-shift null.

| Channel | Alerts | Recalls found, of 8 | Expected by chance | p | False alarms | Post-recall alerts |
|---|---|---|---|---|---|---|
| X | 4 | 1 (31 days before initiation) | 1.16 | 0.82 | 1 | 2 |
| S | 3 | 0 | 0.68 | 1.00 | 1 | 2 |
| R (model-free) | 5 | 0 | 0.71 | 1.00 | 3 | 2 |

No warning: 91.9% of the manufacturer's reports carried an identifier the pack resolves (run 3: fewer than half),
34.4% a mapped problem code. 87 weeks evaluated. **No recall was found more often than chance**: on this public data,
with readable identifiers, the model-free detectors gave no early warning. One manufacturer, four codes, eight
recalls; the requester declared it may have seen recall outcomes before choosing.

## Run 5: main-001, in progress

[Run 37866392839](https://github.com/anovruzov/NeuralGraph/actions/runs/37866392839), request
`lab/requests/main-001.json`, commit `3db19a0`, started 2026-10-09 00:45 UTC: the simulation over seeds 1 to 5 with
a-0p5b, a-1p5b and a-4b (20 planted patterns per model), E1 on generator text, the 1,000-record G0 canary scan with
a-4b, E3 for a-4b and the model-free X1 baseline; 27 units in 23 shards. Recorded here when its report is in,
whatever it shows.

## Run 6: reader-001, in progress (small models reading real complaints)

[Run 37874950202](https://github.com/anovruzov/NeuralGraph/actions/runs/37874950202), request
`lab/requests/reader-001.json`, commit `16901e5`, started 2026-10-09 02:31 UTC; rule
`docs/collective/replay/vehicles/CHOICE-R001.md`, committed in `389d278` before the run. E1 on **real public data** for
the first time: a-0p5b, a-1p5b and a-4b read 150 NHTSA complaint narratives with their component codes hidden, 3
repeats each, scored against the codes the complaints were filed under (206 claims; labels sha `bc6d092d8bca`), beside
the lexical extractor on the same records. Recorded here when its report is in, whatever it shows.

## Outside the lab: public replay V001 (vehicle complaints, model-free, real data)

Not a lab run: the `vehicle-replay` workflow runs the pilot audit (`docs/collective/PILOT.md`) on two years of FORD's
public NHTSA complaints (27,397, each state a site) with the make's recalls in the window as outcomes (298 in scope,
91 campaigns). Rule, declaration (`saw_recall_outcomes: yes`) and both runs:
`docs/collective/replay/vehicles/CHOICE-V001.md`.
Run 001 ([37869797640](https://github.com/anovruzov/NeuralGraph/actions/runs/37869797640)) exported no codes because
of an exporter bug: S and R had no alerts and text-only X found 0 (5.75 expected by chance). Run 002
([37870255727](https://github.com/anovruzov/NeuralGraph/actions/runs/37870255727)), the same settings with the bug
fixed and run 001's result seen:

| Channel | Alerts | Found before the recall, of 298 | Expected by chance | p |
|---|---|---|---|---|
| X | 262 | 5 | 10.95 | 0.84 |
| S | 221 | 5 | 10.92 | 0.84 |
| R (model-free) | 270 | 5 | 12.34 | 0.89 |

**No early warning beyond chance**, as in run 4: the same five outcomes in every channel, fewer than randomly timed
alerts find.

**Public replay V002** ([37871216150](https://github.com/anovruzov/NeuralGraph/actions/runs/37871216150);
`docs/collective/replay/vehicles/CHOICE-V002.md`, rule and criterion committed before the run): the same audit on the
next five makes by complaint volume (CHEVROLET, JEEP, HONDA, NISSAN, DODGE; 35,838 complaints, 598 outcomes in scope).
The pre-set criterion was Fisher's combined p below 0.05/3 per channel. It was not met: combined p above 0.9999 in
every channel. X found 9 where 19.51 were expected, S 8 where 19.03 were, R 10 where 23.63 were, and no make beat
chance. Across all six makes, X flagged 14 of 896 outcomes before the recall and 51 in the 26 weeks after it.

**Public replay V003** ([37871956063](https://github.com/anovruzov/NeuralGraph/actions/runs/37871956063);
`docs/collective/replay/vehicles/CHOICE-V003.md`, the fifth real-data test, declared as such before the run): the
same six makes, with NHTSA's preliminary evaluations and defect petitions as the outcomes instead of recalls (22
investigations in scope, 126 outcomes). The same criterion was not met: combined p above 0.9999 in every channel. X
found 6 where 9.07 were expected, and alerted after 17 of the 126 had opened.

## What has not run

- **A replay with a model reading real narratives** (run 6 measures the reading alone, against filed codes), and
  replays of other device manufacturers. **N1 and E1 on human-labelled narratives** need a person to label the
  sheets.
- **A hosted (non-local) model:** the lab supports one through `MYCELIC_LAB_HOSTED_API_KEY`; no key is configured.

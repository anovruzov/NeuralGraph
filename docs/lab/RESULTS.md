# Cloud lab results

Every number below is copied from a lab report printed in a GitHub Actions job log (between
`=== MYCELIC-LAB … BEGIN/END ===` markers), from a shard's own summary or, for the vehicle replay, from its audit
printed between `VEHICLE-AUDIT` markers. Each row names its run. **Synthetic data, except runs 3, 4, 6, 7 and 9
and the vehicle replay**: their records are public FDA reports and NHTSA complaints; every other record and narrative was
generated from a seed. **Runner hardware only**: one shared 4-vCPU GitHub-hosted runner per shard, whose CPU model
varies between shards; compare timings only between rows of the same CPU model, and never read them as site hardware.
A unit without a result is listed with its reason, not left out.

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

## Run 5: main-001 (five seeds, three models, and a leakage scan that failed on a false alarm)

[Run 37866392839](https://github.com/anovruzov/NeuralGraph/actions/runs/37866392839), request
`lab/requests/main-001.json`, commit `3db19a0`, 2026-10-09 00:45 to 05:49 UTC; report from the `aggregate` job
(113689643048, `report.md` sha256 `f4890fa4d658…`, matching the printed hash). Purpose: the simulation over seeds 1 to
5 with a-0p5b, a-1p5b and a-4b (20 planted patterns per model), E1 on generator text, the 1,000-record G0 canary scan
with a-4b, E3 for a-4b and the model-free X1 baseline. 27 units in 23 shards, all sealed; 26 units `ok`, and G0
`result_fail`. The log tail held all of `report.md` but only the end of `report.json`, so the numbers below come from
`report.md`.

**Canary leakage scan: failed.** G0 ran with a-4b in the loop on `device_quality`, seed 1, 1,000 records:

- 4,257 canaries were planted, and **1 crossed a boundary**;
- 0 shingle-overlap bytes and 0 model path problems;
- the positive control was hit 885 times, so the scanner worked.

The smoke run's 200-record scan with a-1p5b had passed (0 of 875).

**Traced afterwards: a scanner false alarm, not a leak.** The same scan, re-run offline with the fake model (seed 1,
1,000 records, about 8 s), gave the same single hit. So the hit does not depend on a model. The hit was:

- canary `b-000230`, an id-shaped value planted in a patient-name field;
- token `TE-002`, product id format;
- found in HQ's store at `hqdb/collective.sqlite3`.

The bytes around it are HQ's org table, where the unit path `g0/region-1/site-002` sits by design. The scan matches
id tokens case-insensitively, and `te-002` is the tail of `site-002`. The planter checked new canaries against the
pack's text but not against the org paths G0 itself writes.

**The fix:** `plant_canaries` now takes the run's `reserved` strings (G0 passes its enterprise, site ids and unit
paths) and re-draws any canary inside one. A regression test forces exactly that draw. After it, the offline
1,000-record scan passes on seeds 1, 2 and 3 in fake mode, and on seed 1 in lexical mode: 0 hits, 0 shingle bytes,
positive control hit. The real-model scan has not been re-run; it is the next G0 to run.

**Six-site simulation** (plant `sim_small`, 34 weeks, seeds 1 to 5, each seed one synthetic world shared by the three
models; 4 planted patterns per seed, 3 of them narrative-only, so 20 patterns of which S and model-free R can see 5; the
no-plant control made no chance find in any channel). Planted patterns found, of 20:

| Model | X, model reads | X, lexical | S, codes only | R, model-free | U, reference | Single site | Rules |
|---|---|---|---|---|---|---|---|
| a-0p5b | 5 | 19 | 5 | 5 | 5 | 5 | 0 |
| a-1p5b | 9 | 19 | 5 | 5 | 8 | 10 | 0 |
| a-4b | 10 | 19 | 5 | 5 | 11 | 13 | 0 |

- **Does X with the model beat S and model-free R?**
  - a-1p5b and a-4b found 4 and 5 more of the 20 patterns; a-0p5b found the same number.
  - S found all 5 patterns it can see, so every extra find is a narrative-only pattern that S and R are blind to by
    construction. That lift is fixed by the plant, not measured.
  - No seed's lift interval over S excludes zero. a-4b's lifts were 0, +0.25, +0.50, +0.25 and +0.25, each with its
    interval starting at 0.
- **Against the lexical extractor, which is exact on generator text by construction,** every model lost patterns: 5,
  9 and 10 found against 19.
- **Each site alone** (single site, exact per-site counts of the same claims) found as many as or more than X with the
  model: 13 against 10 for a-4b. It did so at about five times the alerts, roughly 75 a seed against 18 or fewer.
- **Alerts summed over the seeds, with false alarms in brackets:**
  - X with the model: 33 (28) for a-0p5b, 74 (65) for a-1p5b and 61 (51) for a-4b;
  - X lexical: 64 (45);
  - S: 24 (19).

**Pushdown verification** (no raw text crossed in any unit). HQ checks its candidates at the sites; compare the
average precision of the pushdown verdicts with that of the detector scores. Pushdown ranked the true candidates
better in 2 of 5 seeds for a-0p5b, and in 3 of 5 for a-1p5b and a-4b. It was worse in 1 seed each, and the same in the
rest.

**Extraction on generator text** (E1, 150 records, 294 claims, 3 repeats, reference a-4b, margin 0.05):

| Model | Field F1 [95% interval] | Claim F1 | Difference from a-4b [95% interval] | Non-inferior |
|---|---|---|---|---|
| a-0p5b | 0.256 [0.211, 0.298] | 0.032 | -0.451 [-0.508, -0.394] | no |
| a-1p5b | 0.527 [0.479, 0.575] | 0.310 | -0.180 [-0.234, -0.124] | no |
| a-4b | 0.707 [0.665, 0.748] | 0.521 | reference | n/a |

Every model is below E1's 0.80 kill line. The same post-processing rule that zeroed run 6 also costs the models here:
42% of generated mentions are aliases or spaced forms whose canonical id is not in the text (handoff finding F8).

**Latency** (E3, a-4b, Intel Xeon Platinum 8573C, 6 measured requests per cell, all answered):

| Workload | Concurrency | End-to-end median s | p95 s | First token median s | Decode tokens/s median | Requests/s |
|---|---|---|---|---|---|---|
| extraction | 1 | 40.149 | 42.298 | 25.681 | 4.2 | 0.025 |
| extraction | 4 | 138.144 | 183.009 | 44.409 | 0.9 | 0.029 |
| short | 1 | 19.246 | 20.465 | 11.344 | 5.6 | 0.052 |
| short | 4 | 60.884 | 79.622 | 27.561 | 2.1 | 0.064 |

**X1** (model-free harness, same-author plant fixture, not blind, so not eligible as STRATEGY's X1). X, U and single
site found 9 of 9; S, model-free R and rules found 0 of 9 (all 9 narrative-only).

**What this says.** On five synthetic worlds built to reward reading, the two larger models found 9 and 10 of the 20
planted patterns, against 5 for the codes and 19 for the lexical extractor. Each site alone did at least as well as
the collective channel. The one leakage scan with a 4B model in the loop failed by one canary, a scanner false alarm
traced and fixed afterwards (above). None of it is real data, and all of it measures this pipeline, including the
post-processing rule that run 6 shows discards model answers.

## Run 6: reader-001 (small models reading real complaints): every model scored zero

[Run 37874950202](https://github.com/anovruzov/NeuralGraph/actions/runs/37874950202), request
`lab/requests/reader-001.json`, commit `16901e5`, 2026-10-09 02:31 to 05:31 UTC; rule
`docs/collective/replay/vehicles/CHOICE-R001.md`, committed in `389d278` before the run. Report from the `aggregate` job
(113685208656): `report.json` sha256 `ee6db53ba685…` and `report.md` `4edb84513c42…`, both read in full and both
matching. a-0p5b, a-1p5b and a-4b read 150 NHTSA complaint narratives with their component codes hidden, 3 repeats
each, scored against the codes the complaints were filed under (206 claims; labels sha `bc6d092d8bca`). All 9 units
were `ok`.

| Reader | Predicate F1 [95% interval] | Field F1 | Valid JSON | Wall s per record (runner CPU) |
|---|---|---|---|---|
| Lexical extractor | 0.434 | 0.550 | n/a | n/a |
| a-0p5b | 0.000 [0.000, 0.000] | 0.000 | 1.000 | 9.9 to 10.5 (AMD EPYC 7763) |
| a-1p5b | 0.000 [0.000, 0.000] | 0.000 | 1.000 | 20.5 to 21.7 (AMD EPYC 9V45) |
| a-4b | 0.000 [0.000, 0.000] | 0.000 | 0.996 | 35.2 to 38.9 (AMD EPYC 9V74) |

**By the pre-registered rule, every model reads worse than the lexical baseline.** The zero is almost certainly the
post-processing handicap that was logged before the result: an item whose entity is typed `vehicle` but not resolved
to a pack id is dropped whole. A reconstruction through the repo's own code gives exactly 0.000 for the most natural
reply, against 1.000 for the same predicates with the entity left null. The raw replies were not stored, so the
cause cannot be confirmed from the run (`CHOICE-R001.md`, "What the zero most likely is"). Whether small models can
read real complaints is therefore still unmeasured; R002 has to fix the reading path first.

## Run 7: reader-002 (small models reading real complaints, post-processing fixed): every model below word matching

[Run 37910989965](https://github.com/anovruzov/NeuralGraph/actions/runs/37910989965), request
`lab/requests/reader-002.json`, commit `7a94535`, 2026-10-09 09:23 to 14:28 UTC; rule
`docs/collective/replay/vehicles/CHOICE-R002.md`, committed in `abf94dd` and amended in `fd2a67a`, both before the
run. The same 150 narratives as run 6 (labels sha `bc6d092d8bca`). a-0p5b and a-1p5b finished all three repeats;
a-4b finished repeat 3, and repeats 1 and 2 timed out at the unit's 150-minute limit. The run's own aggregate
(`report.md` `081347404246…`, `report.json` `41ebc90eb48e…`) scored no model, because E1's comparison needs every
repeat of its reference. The scores come from a re-aggregation of the same artifacts,
[run 37955383933](https://github.com/anovruzov/NeuralGraph/actions/runs/37955383933) (commit `e36a903`; `report.md`
`01857500265c…`, `report.json` `0918c541f440…`, both matching), declared in `CHOICE-R002.md` before it ran. No unit
ran again.

| Reader | Repeats scored | Predicate F1 [95% interval] | Field F1 | Valid JSON | Median s per call (runner CPU) |
|---|---|---|---|---|---|
| Lexical extractor | n/a | 0.434 | 0.550 | n/a | n/a |
| a-0p5b | 3 of 3 | 0.026 [0.015, 0.038] | 0.216 | 1.000 | 10.9 (AMD EPYC 7763), 12.5 (Intel Xeon 6973P-C) |
| a-1p5b | 3 of 3 | 0.178 [0.150, 0.209] | 0.369 | 1.000 | 34.7 (AMD EPYC 9V74), 52.1 (Intel Xeon Platinum 8573C) |
| a-4b | 1 of 3 | 0.218 [0.185, 0.254] | 0.429 | 0.993 | 25.8 (Intel Xeon Platinum 8573C) |

**By the pre-registered rule, every model reads worse than the lexical baseline.** The fix removed the zero: the
models now attach predicates to the structured vehicle, and nearly every record gets a claim. They name many
components per complaint, and most do not match the filed ones: summed over its repeats, a-1p5b re-attached 7,513
items and dropped 5,166 duplicates for 150 records a repeat. The raw replies are now stored, so the reason can be
read in a later choice file. This measures three small models reading public complaints against filed codes; it does
not measure a company's own text.

## Run 8: g0-001 (the canary scan re-run with a small model in the loop): passed

[Run 37938605933](https://github.com/anovruzov/NeuralGraph/actions/runs/37938605933), request
`lab/requests/g0-001.json`, commit `95b2a1e`, 2026-10-09 13:41 to 16:15 UTC. Report from the `aggregate` job
(113912401957): `report.md` sha256 `cc6a2cd45509…` and `report.json` `df243cf74261…`, both matching. G0 only, with
main-001's settings: a-4b, `device_quality`, seed 1, 1,000 records, on one shard (AMD EPYC 9V74, 9,054 s).
Synthetic data.

| Canaries planted | Canary hits | Shingle overlap bytes | Positive control hits | Model path problems | Passed |
|---|---|---|---|---|---|
| 4,259 | 0 | 0 | 884 (42,819 overlap bytes) | 0 | yes |

**The scan passes.** Main-001's one hit (run 5) was the scanner reading a planted id inside a site name the run itself
created; with the planter now reserving the run's organisation strings, no planted text crossed any boundary in this
run, and the positive control shows the scan finds planted text where it is put on purpose. Model calls: 1,000
extractions at a median 6.5 s and 653 judge calls at 4.0 s. G0 covers text only, not timing, sizes or the counts
themselves (`docs/collective/LEAKAGE.md`, section 7).

## Run 9: judge-001 (small models answer HQ's narrow question about real complaints): every model below word matching

[Run 37996675559](https://github.com/anovruzov/NeuralGraph/actions/runs/37996675559), request
`lab/requests/judge-001.json`, commit `5fcfda3`, 2026-10-09 21:59 to 22:21 UTC; rule
`docs/collective/replay/vehicles/CHOICE-J001.md`, committed in `fb40e68` and amended twice before the run (`85be37f`,
`388f497`). Report from the `aggregate` job (114051000850): `report.md` sha256 `72821c46a793…`, matching. The same
150 narratives as runs 6 and 7 (labels sha `bc6d092d8bca`), codes hidden. Each record gets one question about a
filed component and one about a component it was not filed under; each model answers the site verifier's two-part
question, and its verdict is scored against the filed codes. All 18 units finished; no call failed.

| Judge | Balanced accuracy [95% interval] | Sensitivity | Specificity | Unknown share | Median s per call (runner CPU) |
|---|---|---|---|---|---|
| Lexical judge | 0.670 [0.627, 0.713] | 0.380 | 0.960 | 0.000 | n/a |
| Record-blind control (decides nothing) | 0.527 [0.467, 0.587] | | | | n/a |
| a-0p5b | 0.500 [0.500, 0.500] | 0.967 | 0.033 | 0.000 | 1.0 (AMD EPYC 9V74), 1.5 (AMD EPYC 7763) |
| a-1p5b | 0.477 [0.447, 0.503] | 0.867 | 0.087 | 0.080 | 1.4 (AMD EPYC 9V45), 2.2 (AMD EPYC 9V74), 3.2 (AMD EPYC 7763) |
| a-4b | 0.387 [0.340, 0.433] | 0.647 | 0.127 | 0.483 | 6.0 (Intel Xeon Platinum 8573C), 9.0 (AMD EPYC 7763) |

**By the pre-registered rule, every model judges worse than the lexical judge** (paired differences -0.170, -0.193
and -0.283, each interval below zero). 0.5 is a judge that answers the same way every time. a-0p5b and a-1p5b say the
narrative describes the component on nearly every question, filed or not. a-4b tells them apart (it says yes on 97 of
150 filed components and 37 of 150 others), but on most others it says "unclear" rather than "no", and the shipped
verdict rule turns unclear into `unknown`, which is never correct. A reading chosen after the result (unclear as
refute) would put a-4b at 0.700; it decides nothing (`CHOICE-J001.md`, Runs). The lexical judge's sensitivity and
specificity come from its answer counts (57 of 150 positives confirmed, 6 of 150 negatives). Judge calls are short:
the slowest model's median was 9.0 s, against 25.8 s for an extraction call in run 7.

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

- **A replay with a model reading real narratives**, a usable measure of the reading itself (run 6 scored zero
  through the post-processing), and replays of other device manufacturers. **N1 and E1 on human-labelled narratives**
  need a person to label the sheets.
- **A hosted (non-local) model:** the lab supports one through `MYCELIC_LAB_HOSTED_API_KEY`; no key is configured.

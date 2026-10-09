# Diagnostic E002: the vehicle replays re-scored with the corrected chance baseline

**Exploratory: no pass or fail, and not a new test.** The verdicts of V001 to V003 stand as recorded. This file is
written before the runs it describes.

**Why.** The replays compared each found count with a circular-shift null. That null rotates each outcome's own alerts
from after its opening into its look-back, so it expects finds whenever complaints react to recalls or
investigations, with or without an earlier signal (handoff, mistake 2). The pilot audit now also reports the null with
those alerts left out (`expected_found_excluding_own_post` and its p, commit f019ed7), and it keeps each channel's alert
timeline in `audit.json`.

**What runs** (the vehicle-replay workflow, unchanged):
- `run-005.json`: the six makes' recalls with V002's settings (complaints received 2023-2024, 26-week look-back and
  post window, the frozen pack);
- `run-006.json`: the six makes' defect investigations with V003's settings, pushed after run 5 finishes, because the
  workflow runs the newest run file.

**What is reported:**
- per make and channel: found, expected by chance (audited), expected with each outcome's own later alerts left out,
  and both p-values;
- the sums over makes;
- the complaint and outcome counts beside V002's and V003's, which shows whether NHTSA's files changed since.

**How it is read:**
- Found counts at the corrected expectation mean the replays' "fewer finds than chance" came from the baseline. The
  replays then stand as "at chance", not "below it".
- Found counts above the corrected expectation are a hypothesis, not a result: the correction was chosen after
  V001 to V003 were seen. Only a pre-registered replay on other makes or years could test it.
- The diagnosis's figures (recalls 38.64 audited and 20.30 corrected; investigations 10.38 and 6.95) were computed for
  the union of the three channels. The audit reports each channel. The union is compared only once a re-score from
  `audit.json` alone exists and is tested.

## Runs

1. **Run 5, recalls: run [37911451566](https://github.com/anovruzov/NeuralGraph/actions/runs/37911451566)** (commit
   `98e4597`, job 113757323158). Read from the job log; each make's `audit.json` sha256 in the log matches the
   printed audit re-serialised by `write_json_atomic`, for all six makes. The data match V001 and V002: 63,235
   complaints, 896 recalls in scope, and the same audited found and expected counts.

   | Channel | Found before the recall | Expected by chance (audited) | Expected, each recall's own later alerts left out | Fisher p over the six makes, corrected |
   |---|---|---|---|---|
   | X | 14 | 30.46 | 16.37 | 0.988 |
   | S | 13 | 29.95 | 16.21 | 0.989 |
   | R_mf | 15 | 35.98 | 18.74 | 0.980 |

   Per make, the corrected p ranges from 0.34 (JEEP, R_mf) to 1.0. CHEVROLET, DODGE and NISSAN find no recall in
   any channel and have a corrected expectation of 0. Sums and the combined p are computed from the six audits with
   `tools/market/vehicle_summary.py`'s `fisher`.

   **Reading.** Leaving each recall's own later alerts out roughly halves what chance is expected to find. The
   found counts sit at or just below the corrected expectation, so the recall replays' "fewer finds than chance"
   came from the baseline, and the replays stand at chance, not below it. Nothing here is above chance.

# Public replay V002: five more makes, one criterion fixed before the run

V001 ran the pilot audit on one make's public NHTSA complaints and found no early warning beyond chance
(`CHOICE-V001.md`, run 002). One make is one sample. V002 runs the same audit, unchanged, on the next five makes. It
fixes in advance a single criterion that combines them, so the answer does not depend on which make one looks at. The
rule and the criterion are committed before any of these makes' data is exported or audited.

## The declaration

The rule was written by the AI system that wrote this repository's code. Its training data includes public news of
vehicle recalls, including those of the makes below. Before writing this rule it had also seen V001's results for
FORD: no early warning beyond chance, in every channel. It cannot rule out that either shaped the rule, so every number
from this replay carries **`saw_recall_outcomes: yes`**. The makes are chosen by complaint volume before the window, not
by recalls, and nothing about the method changes from V001.

## The rule

1. **Data:** the same NHTSA flat files as V001, as published on the day of the run.
2. **Makes:** ranks 2 to 6 by complaints added in 2019, from V001's probe (`nhtsa-probe.json`,
   `top_makes_added_2019`). Rank 1 is FORD, which V001 already ran.

   | Make | Complaints added in 2019 |
   |---|---|
   | CHEVROLET | 11,983 |
   | JEEP | 8,737 |
   | HONDA | 6,177 |
   | NISSAN | 6,170 |
   | DODGE | 6,051 |

3. **Everything else is V001's** (run 002, exporter commit `e56feba`):
   - the window (2023-01-01 to 2024-12-31);
   - one record per complaint, each state a site;
   - the outcomes (one per recall campaign and vehicle);
   - the pack `pack/`, unchanged: config `a588e72d`, vocabulary `867df7fc`, detector `325b125a`, fixtures
     `9d9d8bd6`;
   - the audit settings (look-back 26 weeks, post-recall 26 weeks, tie salt `pilot`).

   Each make gets its own audit, exactly as a company would run it on its own export.
4. **The criterion** (`tools/market/vehicle_summary.py`):
   - For each channel (X, S, model-free R), combine the five makes' circular-shift p-values by Fisher's method.
   - A channel shows **early warning beyond chance on this set only if its combined p is below 0.05 / 3**, about
     0.0167: three channels, one test each.
   - A make with no outcome in scope has no p and is left out.
   - Also reported, and not part of the criterion: per make and channel, alerts, found, expected by chance and p;
     per channel, the sums over makes and how many makes found more than chance.
5. **The first run is the result.** A run that fails before the audit for an infrastructure reason (a download, a
   runner) may run again unchanged. Any change to a setting is a new choice file.

## How it is read

- Each make's p counts the unshifted timeline among its shifts, so no p is 0, and Fisher's method on these discrete
  p-values is conservative.
- **The makes are not independent.** CHEVROLET is one manufacturer's brand; JEEP and DODGE are another's, and they
  share platforms, suppliers and sometimes recall campaigns. Fisher's method assumes independence, so a combined p
  near the threshold would overstate the evidence. A result is read as far as it clears the threshold with room.
- What V001 says it is not, this is not either:
  - states are not a company's sites;
  - public complaints are not a company's own complaint file;
  - the lexical reader is not a model;
  - a recall is not the moment a manufacturer first knew.

  A positive result would mean only that counting public complaints across states flags these makes' recalls earlier
  than randomly timed alerts do. A negative one would extend V001's answer from one make to six.

## Run 003: the result

[Run 37871216150](https://github.com/anovruzov/NeuralGraph/actions/runs/37871216150), run file `run-003.json`, commit
`898a344` (this rule: `e47e77d`), job 113629524050. Read in full. Each audit's sha256 matches the one the job printed:
   - CHEVROLET: `3adb60512ddfa594d0c8d11efbfe99ca9c42d7e6b8f48fcaa8a2386ddefb04b0`
   - JEEP: `96415abd8fcf41ab4ee97ae6073fee798a42119af9f9c3e9de140e9739272d37`
   - HONDA: `c40874919967cf9a0497b1a65959703b8008774da82a3df40714bbf1a25992f6`
   - NISSAN: `0300725cde63e03f60e63c57a1f232b0952f33e40b6779d5995a27d472482c72`
   - DODGE: `de916892009142a9a8cc935772ee5a910c8d017ed6097e00136e2aedcea3e83c`

Every record carried a code, a narrative and a resolved vehicle; none was rejected; 87 weeks were evaluated for each
make.

| Make | Complaints kept (make's rows in the file) | Sites | Outcomes in scope (campaigns) | X: found, expected, p | S: found, expected, p | R_mf: found, expected, p |
|---|---|---|---|---|---|---|
| CHEVROLET | 9,651 (36,753) | 53 | 103 (36) | 0, 0.30, 1.00 | 0, 0.30, 1.00 | 0, 0.90, 1.00 |
| JEEP | 6,810 (26,193) | 56 | 138 (38) | 7, 10.76, 0.92 | 6, 9.84, 0.91 | 8, 12.38, 0.78 |
| HONDA | 12,349 (32,109) | 55 | 278 (28) | 2, 7.85, 0.91 | 2, 8.30, 0.95 | 2, 9.62, 0.91 |
| NISSAN | 4,552 (18,391) | 53 | 56 (25) | 0, 0.00, 1.00 | 0, 0.00, 1.00 | 0, 0.14, 1.00 |
| DODGE | 2,476 (12,828) | 52 | 23 (13) | 0, 0.60, 1.00 | 0, 0.60, 1.00 | 0, 0.60, 1.00 |
| **All five** | 35,838 | | **598** | **9, 19.51** | **8, 19.03** | **10, 23.63** |

Found counts are of the outcomes in scope; expected is the circular-shift null's mean.

**The criterion is not met in any channel.** Fisher's combined p is above 0.9999 for X, S and R_mf alike, against
0.0167. No make found more outcomes than chance in any channel, and CHEVROLET, NISSAN and DODGE found none.

## What V001 and V002 say together

There are six makes and 896 recall outcomes in scope. Counting public complaints across states flagged a recall before
it was reported for 14 of the 896 in X, 13 in S and 15 in R_mf. Randomly timed alerts would find about 30, 30 and 36.
In the 26 weeks
after a recall, the same detectors alerted on 51 of the 896 in X, 50 in S and 64 in R_mf. This count is descriptive and
not part of the criterion. Read plainly: on public complaints, these detectors mostly see a recall after it is
announced, and not before. The likely reason is that owners who hear of a recall file complaints, but the audit does
not test why. It does not settle whether a company's own complaint, warranty and service records carry an earlier
signal; news may move those less. Only a pilot on those records can answer that.

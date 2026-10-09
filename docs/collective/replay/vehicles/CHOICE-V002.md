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
